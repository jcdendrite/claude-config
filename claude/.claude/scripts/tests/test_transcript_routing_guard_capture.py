"""Tests for transcript_analysis/routing_guard.py's --capture mode, on synthetic fixtures only."""
import argparse
import errno
import gc
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import textwrap
import threading
import weakref
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

import _config
import _config_dir
import pytest
from helpers import REPO_ROOT, add_linked_worktree, heading_texts, normalize_heading
from transcript_analysis import ledger_common, pricing, review_trace, routing_guard, scope

from ._pr_cost_helpers import _enable_pr_cost
from ._review_round_helpers import (
    _ORCHESTRATOR_AGENT_TYPE,
    _SONNET,
    _as_sidechain,
    _leak_sentinel,
    _marker_write_use,
    _orchestrator_return_record,
    _orchestrator_spawn_record,
)
from ._routing_guard_helpers import (
    _BANNER_PROVENANCE_SENTENCE,
    _JQ_ABSENT,
    _JQ_ABSENT_SKIP_REASON,
    _LOG_FILE_NAME,
    _NO_ROW_PAYLOAD,
    _NOISY_CONFIG_MALFORMED_TOKEN,
    _NOISY_CONFIG_UNKNOWN_KEY,
    _VALID_STATES,
    _append_log_rows,
    _assert_git_failure_diagnostic_precedes_note_refusal,
    _capture_file_text,
    _capture_loop,
    _config_enabled_stub,
    _digest_name,
    _environment_unchanged,
    _install_git_not_found,
    _install_path_spies,
    _install_subprocess_recorder,
    _invoke_routing_guard,
    _log_row,
    _note_line,
    _note_row,
    _opt_in_faults,
    _Outcome,
    _path_that_resolve_rejects_as_nul,
    _real_no_row_stdout,
    _recorder_attachment,
    _RecorderPositiveControls,
    _routing_deny_record,
    _routing_guard_section_lines,
    _run_recorder_hook,
    _strings_the_output_must_not_name,
    _write_log,
    _write_noisy_config_state_file,
)
from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _commit,
    _compact_boundary_rec,
    _hook_deny,
    _hook_deny_current,
    _init_repo,
    _priced,
    _skill_block,
    _slash_user,
    _task_notification_msg,
    _tool_result,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
)

_BRANCH = "feat-alpha"
_OTHER_MODEL = "claude-opus-5"
_DIGIT_SCAN_EXEMPT_PREFIXES = ("routing-guard: git-tracked check",)
_CAPTURED_FILE_MODE = 0o600
_CAPTURES_DIR_MODE = 0o700
_WORKTREE_INFIX = "--claude-worktrees-"
# Module-level so test_select_tests.py's TestCrossDomainReadCompleteness sees this read.
_TRANSCRIPT_ANALYSIS_DOC = REPO_ROOT / "docs" / "transcript-analysis.md"
# Module-level for the same reason.
_SESSION_CONFIG_KEYS_LOG_DOC = REPO_ROOT / "docs" / "session-config-keys-log.md"


class _World(NamedTuple):
    repo: Path
    config_dir: Path
    projects: Path
    main_slug: str
    note: Path

    @property
    def captures_dir(self) -> Path:
        return self.config_dir / "routing-guard-captures"


def _ts(minute: int, hour: int = 10) -> str:
    return f"2026-08-01T{hour:02d}:{minute:02d}:00.000Z"


def _marker_write_result(
    tool_id: str, *, minute: int, branch: str = _BRANCH, is_error: bool = False, text: str = "marker write output",
) -> dict:
    """The user record pairing a tool_result with the Bash call `tool_id`, an error result when `is_error`."""
    result_block = {**_tool_result(tool_id, text), **({"is_error": True} if is_error else {})}
    return _user_msg([result_block], branch=branch, ts=_ts(minute))


@pytest.fixture(scope="module", autouse=True)
def _recorder_notice_captured_before_any_spy():
    """Run the real hook once before any test installs a subprocess or path spy, so no spy records that run."""
    if not _JQ_ABSENT:
        _real_no_row_stdout()


@pytest.fixture
def world(tmp_path, monkeypatch) -> _World:
    """A temp repo as the cwd and the patched checkout root, one opted-in account, and an owner-only note."""
    repo = tmp_path / "checkout"
    _init_repo(repo)
    _commit(repo, "init")
    repo = repo.resolve()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(routing_guard, "_CHECKOUT_ROOT", repo)
    config_dir = tmp_path / "acct-a"
    projects = config_dir / "projects"
    projects.mkdir(parents=True)
    _enable_pr_cost(config_dir)
    note_dir = tmp_path / "notes"
    note_dir.mkdir()
    return _World(repo, config_dir, projects, scope._path_to_project_slug(str(repo)), note_dir / "note.tsv")


def _write_note(world: _World, *lines: str, mode: int = 0o600, text: str | None = None) -> None:
    world.note.write_text(text if text is not None else "\n".join(lines) + "\n")
    world.note.chmod(mode)


# The log fields for a row with all three keys off, at a time before every fixture record. A test passes it as `log=`.
_KEYS_OFF: MappingProxyType = MappingProxyType({})
_ROUTING_ON: MappingProxyType = MappingProxyType({"routing": "true"})


def _write_session(
    world: _World, records: list[dict], *, session_id: str = "sess-1", slug: str | None = None,
    log: MappingProxyType | dict | None = None,
) -> Path:
    """Write one session file, stamping every record with `session_id`.

    `log` holds the `_log_row` fields of the row to record for the session (key values and `logged_at`). The default
    `None` records no row, so a test opts in to the state it means.
    """
    project_dir = world.projects / (slug or world.main_slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(project_dir / f"{session_id}.jsonl", [{"sessionId": session_id, **rec} for rec in records])
    if log is not None:
        _append_log_rows(world.config_dir, _log_row(session_id, **log))
    return project_dir


def _args(world: _World, **overrides) -> Namespace:
    fields = {"config_dir": str(world.config_dir), "note": str(world.note), "capture": True, "out": None}
    fields.update(overrides)
    return Namespace(**fields)


def _run(capsys, world: _World, **overrides) -> _Outcome:
    return _invoke_routing_guard(capsys, _args(world, **overrides))


def _clean_loop(branch: str = _BRANCH, *, start: int = 0, tag: str = "a", later_input: int = 130_000) -> list[dict]:
    """One key-off loop: a code-review round whose span writes the marker, a fresh prompt and one later turn.

    Overhead is the round's two turns: 110,000 input and 500 output tokens. Growth is the later turn's context
    less the opening turn's: 30,000.
    """
    return [
        _priced(_SONNET, input=100_000, branch=branch, ts=_ts(start), content=[_skill_block(f"sk-{tag}", "code-review")]),
        _priced(_SONNET, input=10_000, output=500, branch=branch, ts=_ts(start + 1), content=[_marker_write_use(f"mk-{tag}")]),
        _marker_write_result(f"mk-{tag}", minute=start + 1, branch=branch),
        _user_msg("next task", branch=branch, ts=_ts(start + 2)),
        _priced(_SONNET, input=later_input, branch=branch, ts=_ts(start + 3)),
    ]


def _denied_skill_call(*tool_use_ids: str, branch: str = _BRANCH, minute: int = 0) -> dict:
    """A main-thread assistant record holding one `Skill(code-review)` tool_use per id, the calls a routing deny answers."""
    return _asst(
        _SONNET, branch=branch, ts=_ts(minute),
        content=[_skill_block(tool_use_id, "code-review") for tool_use_id in tool_use_ids],
    )


def _legacy_routing_deny_with_hook_name(tool_use_id: str, hook_name: str) -> dict:
    """A legacy attachment routing deny whose `hookName` is `hook_name`."""
    record = _routing_deny_record(tool_use_id, shape="legacy", branch=_BRANCH, ts=_ts(1))
    record["attachment"]["hookName"] = hook_name
    return record


def _published_files(world: _World) -> list[Path]:
    return sorted(world.captures_dir.glob("*.json")) if world.captures_dir.exists() else []


def _consent_gated_paths(world: _World, spies) -> list[str]:
    """The spied paths inside the account's transcript root, plus its config-keys log: what only an opted-in run may read."""
    projects_root = str(world.projects)
    log_path = str(world.config_dir / _LOG_FILE_NAME)
    return [
        path for path in spies.all_paths()
        if path in (log_path, projects_root) or path.startswith(projects_root + os.sep)
    ]


def _stored_loops(world: _World, branch: str) -> list[dict]:
    return json.loads((world.captures_dir / _digest_name(branch)).read_text())["loops"]


@pytest.fixture
def assembled_loops(monkeypatch) -> list[list[routing_guard._Loop]]:
    """Each `_assemble_loops` result of the run, in call order."""
    results: list[list[routing_guard._Loop]] = []
    real_assemble_loops = routing_guard._assemble_loops

    def _recording_assemble_loops(rounds):
        loops = real_assemble_loops(rounds)
        results.append(loops)
        return loops

    monkeypatch.setattr(routing_guard, "_assemble_loops", _recording_assemble_loops)
    return results


def _round_counts_per_loop(assembled_loops: list[list[routing_guard._Loop]]) -> list[int]:
    return [loop.round_count for loops in assembled_loops for loop in loops]


def _valid_capture_text(**loop_overrides) -> str:
    loop = _capture_loop(**{
        "overhead_tokens": {_SONNET: {"standard": {"input": 5, "output": 1}}}, "growth_tokens": 7,
        "later_turns": {_SONNET: 2}, **loop_overrides,
    })
    return _capture_file_text([loop])


def _capture_one_branch(world: _World, capsys, branch: str = _BRANCH) -> _Outcome:
    _write_session(world, _clean_loop(branch), log=_KEYS_OFF)
    _write_note(world, _note_line(world.config_dir, branch))
    return _run(capsys, world)


class TestFirstCapture:
    def test_publishes_one_owner_only_file_in_an_owner_only_directory_under_its_digest_name(self, world, capsys):
        outcome = _capture_one_branch(world, capsys)

        assert outcome.code == 0, outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]
        assert stat.S_IMODE((world.captures_dir / _digest_name(_BRANCH)).stat().st_mode) == _CAPTURED_FILE_MODE
        assert stat.S_IMODE(world.captures_dir.stat().st_mode) == _CAPTURES_DIR_MODE

    def test_stores_overhead_growth_later_turns_and_degraded_for_the_loop(self, world, capsys):
        _capture_one_branch(world, capsys)

        assert _stored_loops(world, _BRANCH) == [{
            "overhead_tokens": {_SONNET: {"standard": {
                "input": 110_000, "output": 500, "cache_read": 0, "cache_write_1h": 0, "cache_write_5m": 0,
            }}},
            "growth_tokens": 30_000,
            "later_turns": {_SONNET: 1},
            "degraded": False,
        }]

    def test_a_unicode_slash_branch_name_is_captured_under_the_digest_of_its_utf8_bytes(self, world, capsys):
        branch = "feat/ünï-branch"

        outcome = _capture_one_branch(world, capsys, branch)

        assert outcome.code == 0, outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(branch)]
        assert "ünï" not in outcome.out + outcome.err

    def test_capture_file_name_matches_the_hashlib_digest_and_revision(self):
        assert routing_guard._capture_file_name("feat/ünï", 3) == _digest_name("feat/ünï", 3)


# --- the note ----------------------------------------------------------------

_SENTINEL_BRANCH = "sentinel-branch-cell"


class _RefusalCase(NamedTuple):
    case_id: str
    note_text: str
    reason: str
    refusal_line: int
    control_text: str


def _refusal_notes(config_dir: str) -> list[_RefusalCase]:
    """Each refused note, the fixed reason its refusal prints and the note line it names.

    The bad row sits on line 2, below a comment line. Each case is a three-cell row whose one named defect is its only
    defect, so each asserts its own reason. `control_text` repairs that one defect and parses.
    """
    def note(*rows: str) -> str:
        return "# comment\n" + "\n".join(rows) + "\n"

    def row(*, branch: str = _SENTINEL_BRANCH, status: str = "merged", dir_cell: str | None = None) -> str:
        return "\t".join([dir_cell or config_dir, branch, status])

    return [
        _RefusalCase("too-few-columns", note(f"{config_dir}\t{_SENTINEL_BRANCH}"), "wrong column count", 2, note(row())),
        _RefusalCase("trailing-tab", note(row() + "\t"), "wrong column count", 2, note(row())),
        _RefusalCase("unknown-status", note(row(status="maybe")), "unknown status", 2, note(row())),
        _RefusalCase("config-path-in-status-column", note(row(status=config_dir)), "unknown status", 2, note(row())),
        _RefusalCase("unmerged-value", note(row(status="unmerged")), "unknown status", 2, note(row())),
        _RefusalCase(
            "relative-dir", note(row(dir_cell="relative/dir")), "the config directory is not absolute", 2, note(row()),
        ),
        _RefusalCase(
            "nul-in-the-config-dir-cell", note(row(dir_cell="/placeholder/acct\x00dir")), "cannot be resolved", 2, note(row()),
        ),
        _RefusalCase(
            "branch-with-surrounding-whitespace", note(row(branch=f" {_SENTINEL_BRANCH}")), "unknown branch value", 2,
            note(row()),
        ),
        _RefusalCase("empty-branch", note(row(branch="")), "unknown branch value", 2, note(row())),
        _RefusalCase("default-branch-main", note(row(branch="main")), "default-branch name", 2, note(row())),
        _RefusalCase(
            "default-branch-develop-on-excluded", note(row(branch="develop", status="excluded")), "default-branch name", 2,
            note(row(status="excluded")),
        ),
        _RefusalCase("branch-repeated-on-one-account", note(row(), row()), "listed twice", 3, note(row())),
        _RefusalCase(
            "branch-repeated-on-two-accounts", note(row(), row(dir_cell=config_dir + "-other")), "listed twice", 3,
            note(row(), row(dir_cell=config_dir + "-other", branch="another-branch")),
        ),
    ]


_WRONG_COLUMN_COUNT_ROWS = [
    ("five-columns-with-arm-and-pilots", "{dir}\t" + _SENTINEL_BRANCH + "\ton\toff\toff"),
    ("five-columns-unmerged-with-dashes", "{dir}\t" + _SENTINEL_BRANCH + "\tunmerged\t-\t-"),
    ("four-columns", "{dir}\t" + _SENTINEL_BRANCH + "\tmerged\toff"),
    ("one-column", "{dir}"),
]


def _expected_refusal_fragment(reason: str) -> str:
    """The text of the note parser's refusal for a case's reason label."""
    return {
        "wrong column count": routing_guard._WRONG_COLUMN_COUNT_REASON,
        "unknown status": routing_guard._UNKNOWN_STATUS_REASON,
    }.get(reason, reason)


def _symlink_loop_runtime_error(failing_path: Path) -> Exception:
    """The `RuntimeError` a symlink loop raises: its message embeds the path."""
    return RuntimeError(f"Symlink loop from '{failing_path}'")


def _symlink_loop_os_error(failing_path: Path) -> Exception:
    """The `OSError` a failing `stat` raises: it carries the path as `filename`."""
    return OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(failing_path))


def _resolve_raising_for(monkeypatch, unresolvable_path: Path, build_error) -> None:
    """Make `Path.resolve` raise `build_error(unresolvable_path)` for that path alone and resolve every other path for real."""
    real_resolve = Path.resolve

    def resolve_or_raise(self, *args, **kwargs):
        if self == unresolvable_path:
            raise build_error(unresolvable_path)
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_or_raise)


_RESOLVE_ERROR_BUILDERS = [
    pytest.param(_symlink_loop_runtime_error, id="runtime-error"),
    pytest.param(_symlink_loop_os_error, id="os-error"),
]


class TestNoteParser:
    @pytest.mark.parametrize("case_id", [case.case_id for case in _refusal_notes("/placeholder")])
    def test_refuses_each_malformed_note_with_exit_2_naming_the_line_and_echoing_no_cell(self, world, capsys, case_id):
        case = {case.case_id: case for case in _refusal_notes(str(world.config_dir))}[case_id]
        _write_note(world, text=case.note_text)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert f"note line {case.refusal_line}: " in outcome.err
        assert _expected_refusal_fragment(case.reason) in outcome.err
        assert _SENTINEL_BRANCH not in outcome.err + outcome.out
        assert str(world.config_dir) not in outcome.err + outcome.out
        assert _published_files(world) == []

    @pytest.mark.parametrize("build_error", _RESOLVE_ERROR_BUILDERS)
    def test_refuses_a_config_dir_cell_whose_resolve_raises_with_the_fixed_resolve_message(
        self, world, capsys, tmp_path, monkeypatch, build_error,
    ):
        unresolvable_cell = tmp_path / "unresolvable-account"
        _resolve_raising_for(monkeypatch, unresolvable_cell, build_error)
        _write_note(world, text=f"# comment\n{_note_line(unresolvable_cell, _SENTINEL_BRANCH)}\n")

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "note line 2: the config directory cannot be resolved" in outcome.err
        assert str(unresolvable_cell) not in outcome.out + outcome.err
        assert str(tmp_path) not in outcome.out + outcome.err
        assert _published_files(world) == []

    @pytest.mark.parametrize("case_id", [case.case_id for case in _refusal_notes("/placeholder")])
    def test_each_refused_note_parses_once_its_one_defect_is_repaired_and_is_refused_for_that_defect_alone(
        self, tmp_path, case_id,
    ):
        case = {case.case_id: case for case in _refusal_notes(str(tmp_path))}[case_id]

        repaired_rows = routing_guard._parse_note(case.control_text)

        assert repaired_rows
        with pytest.raises(routing_guard._NoteRefusal) as refusal:
            routing_guard._parse_note(case.note_text)
        message = str(refusal.value)
        assert message.startswith(f"routing-guard: note line {case.refusal_line}: ")
        assert _expected_refusal_fragment(case.reason) in message

    @pytest.mark.parametrize("case_id", [case_id for case_id, _row in _WRONG_COLUMN_COUNT_ROWS])
    def test_a_row_with_any_other_column_count_gets_the_fixed_message_naming_three_columns_and_the_log(
        self, world, capsys, case_id,
    ):
        row = dict(_WRONG_COLUMN_COUNT_ROWS)[case_id].format(dir=world.config_dir)
        _write_note(world, row)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert outcome.err.strip().splitlines()[-1] == f"routing-guard: note line 1: {routing_guard._WRONG_COLUMN_COUNT_REASON}"
        assert "three columns: config dir, branch and merged or excluded" in outcome.err
        assert "session config-keys log supplies the arm and pilot states" in outcome.err
        assert "session config-keys log" in outcome.err
        assert _published_files(world) == []

    def test_an_unknown_third_value_gets_the_fixed_message_naming_merged_and_excluded(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, _SENTINEL_BRANCH, "on"))

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert outcome.err.strip().splitlines()[-1] == f"routing-guard: note line 1: {routing_guard._UNKNOWN_STATUS_REASON}"
        assert "merged" in outcome.err
        assert "excluded" in outcome.err

    def test_a_merged_row_and_an_excluded_row_parse(self, tmp_path):
        rows = routing_guard._parse_note(f"{tmp_path}\tone\tmerged\n{tmp_path}\ttwo\texcluded\n")

        assert [(row.branch, row.status) for row in rows] == [("one", "merged"), ("two", "excluded")]

    def test_refuses_a_tilde_dir_when_home_is_unset(self, world, capsys, monkeypatch):
        monkeypatch.delenv("HOME", raising=False)
        _write_note(world, text=f"~/acct\t{_SENTINEL_BRANCH}\tmerged\n")

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "note line 1: '~/' needs an absolute HOME" in outcome.err

    def test_parses_crlf_lines_and_a_tilde_dir_against_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

        rows = routing_guard._parse_note("~/acct\tbranch-one\tmerged\r\n")

        assert rows == [routing_guard._NoteRow(1, (tmp_path / "acct").resolve(), "branch-one", "merged")]

    def test_two_spellings_of_one_directory_name_one_account(self, tmp_path):
        dir_cell = str(tmp_path / "acct")

        rows = routing_guard._parse_note(f"{dir_cell}/\tone\tmerged\n{dir_cell}\ttwo\texcluded\n")

        assert rows[0].resolved_config_dir == rows[1].resolved_config_dir

    def test_accepts_unicode_and_slash_branch_names_and_a_comment_only_note(self, tmp_path):
        rows = routing_guard._parse_note(f"{tmp_path}\tfeat/ünï-branch\texcluded\n")

        assert [row.branch for row in rows] == ["feat/ünï-branch"]
        assert routing_guard._parse_note("# only a comment\n\n") == []

    def test_does_not_trim_cells_so_a_padded_status_is_an_unknown_value(self, tmp_path):
        with pytest.raises(routing_guard._NoteRefusal, match="third column"):
            routing_guard._parse_note(f"{tmp_path}\tone\t merged\n")

    @pytest.mark.parametrize("note_state", ["missing", "directory", "not-utf8"])
    def test_refuses_an_unreadable_note_with_a_fixed_message_naming_no_path(self, world, capsys, note_state):
        if note_state == "directory":
            world.note.mkdir()
        elif note_state == "not-utf8":
            world.note.write_bytes(b"\xff\xfe\x00bad")

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert str(world.note) not in outcome.err
        assert "note" in outcome.err


class TestNoteLocation:
    def test_refuses_a_relative_note_path_and_publishes_nothing(self, world, capsys):
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world, note="notes/note.tsv")

        assert outcome.code == 2
        assert "absolute" in outcome.err
        assert _published_files(world) == []

    def test_refuses_a_note_inside_a_git_work_tree_without_naming_its_path(self, world, capsys):
        _write_session(world, _clean_loop())
        note_in_repo = world.repo / "note.tsv"
        note_in_repo.write_text(_note_line(world.config_dir, _BRANCH) + "\n")

        outcome = _run(capsys, world, note=str(note_in_repo))

        assert outcome.code == 2
        assert "git working tree" in outcome.err
        assert str(world.repo) not in outcome.err
        assert _published_files(world) == []

    def test_refuses_a_note_path_with_an_embedded_nul_with_the_fixed_resolve_message(self, world, capsys, tmp_path):
        note_with_nul = _path_that_resolve_rejects_as_nul(tmp_path)

        outcome = _run(capsys, world, note=note_with_nul)

        assert outcome.code == 2
        assert "--note cannot be resolved to a path" in outcome.err
        assert str(tmp_path) not in outcome.out + outcome.err

    @pytest.mark.parametrize("build_error", _RESOLVE_ERROR_BUILDERS)
    def test_refuses_a_note_path_whose_resolve_raises_with_the_fixed_resolve_message(
        self, world, capsys, tmp_path, monkeypatch, build_error,
    ):
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        _resolve_raising_for(monkeypatch, world.note, build_error)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "--note cannot be resolved to a path" in outcome.err
        assert str(world.note) not in outcome.out + outcome.err
        assert str(tmp_path) not in outcome.out + outcome.err
        assert _published_files(world) == []

    def test_accepts_a_note_outside_any_git_work_tree(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "excluded-elsewhere", "excluded"))

        assert _run(capsys, world).code == 0

    @pytest.mark.parametrize(
        "mode",
        [
            pytest.param(0o644, id="group-and-other-readable"),
            pytest.param(0o620, id="group-writable-not-readable"),
            pytest.param(0o602, id="other-writable-not-readable"),
            pytest.param(0o601, id="other-execute-only"),
        ],
    )
    def test_a_note_with_any_group_or_other_permission_prints_the_permission_notice(self, world, capsys, mode):
        _write_note(world, _note_line(world.config_dir, "excluded-one", "excluded"), mode=mode)

        outcome = _run(capsys, world)

        assert routing_guard._PERMISSIVE_NOTE_NOTICE in outcome.err
        assert "the note has group or other permissions" in outcome.err

    def test_an_owner_only_note_prints_no_permission_notice(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "excluded-one", "excluded"), mode=0o600)

        outcome = _run(capsys, world)

        assert routing_guard._PERMISSIVE_NOTE_NOTICE not in outcome.err


# --- flags and the opt-in ----------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=None)
    routing_guard._add_routing_guard_subparser(parser.add_subparsers(dest="subcommand", required=True))
    return parser


class TestFlagsAndStepOne:
    def test_a_run_with_no_top_level_config_dir_exits_2_and_publishes_nothing(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world, config_dir=None)

        assert outcome.code == 2
        assert str(world.config_dir) not in outcome.err
        assert _published_files(world) == []

    def test_a_config_dir_the_note_does_not_list_exits_2_naming_no_path(self, world, capsys, tmp_path):
        other_account = tmp_path / "acct-unlisted"
        (other_account / "projects").mkdir(parents=True)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world, config_dir=str(other_account))

        assert outcome.code == 2
        assert "acct-unlisted" not in outcome.err
        assert not (other_account / "routing-guard-captures").exists()

    def test_an_empty_out_exits_2_and_publishes_nothing(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world, out="")

        assert outcome.code == 2
        assert not any(path.endswith(".jsonl") for path in spies.opened_paths())
        assert outcome.err.strip() == routing_guard._EMPTY_OUT_MESSAGE
        assert outcome.out == ""
        assert _published_files(world) == []

    def test_the_note_flag_is_required_by_the_parser(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            _build_parser().parse_args(["routing-guard", "--capture"])

        assert exit_info.value.code == 2
        assert "--note" in capsys.readouterr().err


class TestOptInFunction:
    def test_reports_opted_in_for_an_account_with_the_key_on(self, world):
        assert routing_guard._classify_capture_opt_in(world.config_dir) is routing_guard._OptInOutcome.OPTED_IN

    @pytest.mark.parametrize(("case_id", "reader_result", "expected_outcome", "message_fragment"), _opt_in_faults())
    def test_classifies_each_non_opted_in_outcome_and_capture_exits_1_with_its_message(
        self, world, capsys, monkeypatch, case_id, reader_result, expected_outcome, message_fragment,
    ):
        monkeypatch.setattr(_config, "config_enabled", _config_enabled_stub(reader_result))
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world)

        assert _consent_gated_paths(world, spies) == []
        assert routing_guard._classify_capture_opt_in(world.config_dir) is expected_outcome
        assert outcome.code == 1
        assert message_fragment in outcome.err
        assert _published_files(world) == []

    def test_an_opted_in_run_reads_the_transcripts_and_the_log_the_refusals_must_not(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world)

        assert outcome.code == 0
        gated_paths = _consent_gated_paths(world, spies)
        assert str(world.projects / world.main_slug / "sess-1.jsonl") in gated_paths
        assert str(world.config_dir / _LOG_FILE_NAME) in gated_paths

    def test_an_unknown_key_is_told_apart_from_an_unreadable_schema(self, world, monkeypatch):
        monkeypatch.setattr(_config, "config_enabled", _config_enabled_stub(KeyError("pr_cost_recording")))

        assert routing_guard._classify_capture_opt_in(world.config_dir) is routing_guard._OptInOutcome.UNKNOWN_KEY

        monkeypatch.setattr(_config, "schema", dict)

        assert routing_guard._classify_capture_opt_in(world.config_dir) is routing_guard._OptInOutcome.SCHEMA_UNREADABLE


class TestConfigReaderStderr:
    def test_a_malformed_and_an_unknown_line_leave_neither_the_path_nor_the_line_in_any_stream(self, world, capsys, tmp_path):
        second_account = tmp_path / "acct-b"
        (second_account / "projects").mkdir(parents=True)
        _write_noisy_config_state_file(world.config_dir)
        _write_noisy_config_state_file(second_account)
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(second_account, "branch-on-b"))

        outcome = _run(capsys, world)

        combined = outcome.out + outcome.err
        assert outcome.code == 0, outcome.err
        assert _NOISY_CONFIG_MALFORMED_TOKEN not in combined
        assert _NOISY_CONFIG_UNKNOWN_KEY not in combined
        assert str(world.config_dir) not in combined
        assert str(second_account) not in combined
        assert routing_guard._CONFIG_READER_NOTICE in outcome.err

    def test_an_unreadable_schema_is_reported_with_no_path_and_no_digit(self, world, capsys, monkeypatch, tmp_path):
        monkeypatch.setattr(_config, "_SCHEMA_FILE", tmp_path / "no-such-schema.psv")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 1
        assert "could not read config-keys.psv" in outcome.err
        assert "no-such-schema" not in outcome.err + outcome.out
        failure_lines = [line for line in outcome.err.splitlines() if "could not read config-keys.psv" in line]
        assert not any(ch.isdigit() for line in failure_lines for ch in line)

    # Positive control: the `not in` assertions above go vacuous if the reader stops printing these tokens.
    def test_the_config_reader_itself_prints_the_malformed_and_unknown_tokens_to_stderr(self, capsys, tmp_path):
        _write_noisy_config_state_file(tmp_path)

        _config.config_enabled("pr_cost_recording", tmp_path)

        reader_stderr = capsys.readouterr().err
        assert _NOISY_CONFIG_MALFORMED_TOKEN in reader_stderr
        assert _NOISY_CONFIG_UNKNOWN_KEY in reader_stderr


# --- the whole verdict -------------------------------------------------------


def _round_facts(**overrides) -> routing_guard._RoundFacts:
    fields = {
        "session_key": "session-a", "session_id": "sess-a", "open_idx": 0, "open_ts": 1.0, "has_marker_write": False,
        "context_at_open": 10, "context_at_window_end": 40, "later_turns": {}, "overhead_tokens": {},
        "degraded": False, "denied_open": False, "spawn_open": False,
    }
    fields.update(overrides)
    return routing_guard._RoundFacts(**fields)


def _branch_facts(**overrides) -> routing_guard._BranchFacts:
    return routing_guard._BranchFacts(**{"has_timestamped_record": True, **overrides})


def _branch_states(
    *, unknown_causes: tuple[str, ...] = (), mixed: bool = False, state: str = "off",
) -> routing_guard._BranchKeyStates:
    """Key states for a branch: every key `state`, the routing key unknown with `unknown_causes` or mixed."""
    by_key = {key: state for key in routing_guard._STATE_KEYS}
    if unknown_causes:
        by_key[routing_guard._ROUTING_KEY] = routing_guard._STATE_UNKNOWN
    elif mixed:
        by_key[routing_guard._ROUTING_KEY] = routing_guard._STATE_MIXED
    return routing_guard._BranchKeyStates(by_key, unknown_causes)


def _whole_reason_text(branch_facts, rounds, key_states) -> str | None:
    failure = routing_guard._whole_failure(branch_facts, rounds, key_states)
    return failure.reason if failure is not None else None


class TestWholeReason:
    def test_a_branch_with_no_facts_has_no_records(self):
        assert _whole_reason_text(None, [], _branch_states()) == "no-records"

    def test_a_branch_whose_records_carry_no_parseable_timestamp_has_no_records(self):
        marker_round = _round_facts(has_marker_write=True)

        reason = _whole_reason_text(_branch_facts(has_timestamped_record=False), [marker_round], _branch_states())

        assert reason == "no-records"

    def test_a_branch_with_no_marker_writing_round_has_no_complete_loop(self):
        assert _whole_reason_text(_branch_facts(), [_round_facts()], _branch_states()) == "no-complete-loop"

    def test_an_unreadable_orchestrator_transcript_blocks_an_otherwise_whole_branch(self):
        facts = _branch_facts(orchestrator_unreadable=True)

        reason = _whole_reason_text(facts, [_round_facts(has_marker_write=True)], _branch_states())

        assert reason == "orchestrator-unreadable"

    def test_a_whole_branch_has_no_reason(self):
        assert _whole_reason_text(_branch_facts(), [_round_facts(has_marker_write=True)], _branch_states()) is None

    def test_a_branch_with_an_unknown_key_state_reads_key_state_unknown_with_its_causes_in_order(self):
        states = _branch_states(unknown_causes=("before-recorder", "hook-not-run"))

        reason = _whole_reason_text(_branch_facts(), [_round_facts(has_marker_write=True)], states)

        assert reason == "key-state-unknown: before-recorder, hook-not-run"

    def test_a_branch_with_a_mixed_key_state_reads_key_state_mixed(self):
        reason = _whole_reason_text(_branch_facts(), [_round_facts(has_marker_write=True)], _branch_states(mixed=True))

        assert reason == "key-state-mixed"

    def test_unknown_takes_precedence_over_mixed_when_both_hold_for_different_keys(self):
        by_key = {
            routing_guard._ROUTING_KEY: routing_guard._STATE_UNKNOWN,
            routing_guard._ROUND2_PILOT_KEY: routing_guard._STATE_MIXED,
            routing_guard._ARCHITECT_PILOT_KEY: "off",
        }
        states = routing_guard._BranchKeyStates(by_key, ("hook-not-run",))

        reason = _whole_reason_text(_branch_facts(), [_round_facts(has_marker_write=True)], states)

        assert reason == "key-state-unknown: hook-not-run"

    def test_reasons_take_precedence_in_the_order_records_then_loop_then_orchestrator_then_key_state(self):
        unreadable_without_records = _branch_facts(has_timestamped_record=False, orchestrator_unreadable=True)
        unreadable_without_loop = _branch_facts(orchestrator_unreadable=True)
        unknown = _branch_states(unknown_causes=("hook-not-run",))

        assert _whole_reason_text(unreadable_without_records, [], unknown) == "no-records"
        assert _whole_reason_text(unreadable_without_loop, [_round_facts()], unknown) == "no-complete-loop"
        assert _whole_reason_text(
            unreadable_without_loop, [_round_facts(has_marker_write=True)], unknown,
        ) == "orchestrator-unreadable"

    def test_leading_default_branch_records_are_credited_to_the_first_branch_entered(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [
            _asst(_SONNET, branch="main", ts=_ts(0)),
            _asst(_SONNET, branch="main", ts=_ts(1)),
            _asst(_SONNET, branch=_BRANCH, ts=_ts(5)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert set(scan.branches) == {_BRANCH}
        assert scan.branches[_BRANCH].has_timestamped_record

    def test_capture_reports_each_missing_reason_by_note_line_and_exits_3(self, world, capsys):
        no_marker = [
            _priced(_SONNET, input=1_000, branch="no-marker", ts=_ts(0), content=[_skill_block("sk-n", "code-review")]),
        ]
        spawn_without_transcript = [
            _orchestrator_spawn_record("o9", ts=_ts(10), branch="no-transcript"),
            _priced(_SONNET, input=1_000, branch="no-transcript", ts=_ts(11), content=[_marker_write_use("mk-u")]),
            _marker_write_result("mk-u", minute=11, branch="no-transcript"),
        ]
        _write_session(world, no_marker, session_id="sess-1")
        _write_session(world, spawn_without_transcript, session_id="sess-2")
        _write_note(
            world,
            _note_line(world.config_dir, "no-records-at-all"),
            _note_line(world.config_dir, "no-marker"),
            _note_line(world.config_dir, "no-transcript"),
        )

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (no-records)" in outcome.out
        assert "note line 2: missing (no-complete-loop)" in outcome.out
        assert "note line 3: missing (orchestrator-unreadable)" in outcome.out
        assert _published_files(world) == []


# --- loops -------------------------------------------------------------------


def _write_marker_writing_orchestrator(
    project_dir: Path, *, session_id: str = "sess-1", tool_use_id: str = "o1", orchestrator_write_is_error: bool = False,
) -> None:
    """The review-orchestrator's own transcript, holding the clean-marker write and its paired result."""
    records = [
        _as_sidechain(_priced(
            _SONNET, input=5_000, branch=_BRANCH, ts=_ts(2),
            content=[_marker_write_use(f"mk-{tool_use_id}")],
        )),
        _as_sidechain(_marker_write_result(f"mk-{tool_use_id}", minute=2, is_error=orchestrator_write_is_error)),
    ]
    _write_subagent_dispatch(
        project_dir, session_id, f"agent-{tool_use_id}", tool_use_id, records, agent_type=_ORCHESTRATOR_AGENT_TYPE,
    )


def _key_on_loop(branch: str = _BRANCH) -> list[dict]:
    """A denied Skill(code-review) followed by the review-orchestrator spawn that replaces it."""
    return [
        _priced(_SONNET, input=40_000, branch=branch, ts=_ts(0), content=[_skill_block("sk-denied", "code-review")]),
        _routing_deny_record("sk-denied", branch=branch, ts=_ts(1)),
        _orchestrator_spawn_record("o1", ts=_ts(2), branch=branch),
        _orchestrator_return_record("o1", ts=_ts(3), branch=branch),
    ]


def _round(
    branch: str, tag: str, minute: int, *, marker: bool, input_tokens: int = 1_000, write_result: str | None = "ok",
) -> list[dict]:
    """One code-review round opened by a Skill call, its outcome span holding a marker write or not.

    `write_result` is the paired result of the write: "ok", "error", or None for a write with no result.
    """
    content = [_marker_write_use(f"mk-{tag}")] if marker else []
    paired_result = (
        [_marker_write_result(f"mk-{tag}", minute=minute + 1, branch=branch, is_error=write_result == "error")]
        if marker and write_result is not None else []
    )
    return [
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute), content=[_skill_block(f"sk-{tag}", "code-review")]),
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute + 1), content=content),
        *paired_result,
        _user_msg(f"prompt after {tag}", branch=branch, ts=_ts(minute + 2)),
    ]


def _round_with_marker_after_prompt(branch: str, tag: str, minute: int, *, input_tokens: int = 1_000) -> list[dict]:
    """One code-review round whose marker write follows a fresh user prompt, outside the round's window."""
    return [
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute), content=[_skill_block(f"sk-{tag}", "code-review")]),
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute + 1)),
        _user_msg(f"prompt inside {tag}", branch=branch, ts=_ts(minute + 2)),
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute + 3), content=[_marker_write_use(f"mk-{tag}")]),
        _marker_write_result(f"mk-{tag}", minute=minute + 3, branch=branch),
    ]


class TestLoops:
    def test_two_rounds_ending_at_a_marker_write_form_one_loop_whose_overhead_covers_both(
        self, world, capsys, assembled_loops,
    ):
        records = (
            _round(_BRANCH, "a", 0, marker=False, input_tokens=1_000)
            + _round(_BRANCH, "b", 10, marker=True, input_tokens=2_000)
        )
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loops = _stored_loops(world, _BRANCH)
        assert _round_counts_per_loop(assembled_loops) == [2]
        assert len(loops) == 1
        assert loops[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 2 * 1_000 + 2 * 2_000

    def test_a_marker_write_after_a_user_prompt_closes_its_own_round_and_not_the_next_one(
        self, world, capsys, assembled_loops,
    ):
        records = _round_with_marker_after_prompt(_BRANCH, "a", 0) + _round(_BRANCH, "b", 10, marker=True)
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [1, 1]

    def test_an_orchestrator_spawn_after_a_user_prompt_closes_its_own_round_and_not_the_previous_one(
        self, world, capsys, assembled_loops,
    ):
        records = _round(_BRANCH, "a", 0, marker=False) + [
            _orchestrator_spawn_record("o1", ts=_ts(10), branch=_BRANCH),
            _orchestrator_return_record("o1", ts=_ts(11), branch=_BRANCH),
        ]
        project_dir = _write_session(world, records, log=_KEYS_OFF)
        _write_marker_writing_orchestrator(project_dir)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]

    def test_a_marker_write_after_the_next_rounds_open_belongs_to_that_round(self, world, capsys, assembled_loops):
        records = _round(_BRANCH, "a", 0, marker=False) + _round_with_marker_after_prompt(_BRANCH, "b", 10)
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]

    def test_two_marker_writes_give_two_loops(self, world, capsys, assembled_loops):
        records = _round(_BRANCH, "a", 0, marker=True) + _round(_BRANCH, "b", 10, marker=True)
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [1, 1]
        assert len(_stored_loops(world, _BRANCH)) == 2

    def test_a_write_with_an_error_result_does_not_end_its_loop(self, world, capsys, assembled_loops):
        records = (
            _round(_BRANCH, "a", 0, marker=True, write_result="error")
            + _round(_BRANCH, "b", 10, marker=True)
        )
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]
        assert len(_stored_loops(world, _BRANCH)) == 1

    def test_a_write_with_no_result_as_the_only_write_leaves_no_complete_loop(self, world, capsys):
        _write_session(world, _round(_BRANCH, "a", 0, marker=True, write_result=None), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "missing (no-complete-loop)" in outcome.out
        assert _published_files(world) == []

    def test_an_orchestrator_write_with_an_error_result_does_not_end_its_loop(self, world, capsys, assembled_loops):
        records = [
            _orchestrator_spawn_record("o1", ts=_ts(0), branch=_BRANCH),
            _orchestrator_return_record("o1", ts=_ts(1), branch=_BRANCH),
            _user_msg("prompt between the rounds", branch=_BRANCH, ts=_ts(5)),
            _orchestrator_spawn_record("o2", ts=_ts(10), branch=_BRANCH),
            _orchestrator_return_record("o2", ts=_ts(11), branch=_BRANCH),
        ]
        project_dir = _write_session(world, records, log=_KEYS_OFF)
        _write_marker_writing_orchestrator(project_dir, tool_use_id="o1", orchestrator_write_is_error=True)
        _write_marker_writing_orchestrator(project_dir, tool_use_id="o2")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]
        assert len(_stored_loops(world, _BRANCH)) == 1

    def test_a_write_whose_ok_result_reports_nothing_to_review_still_ends_its_loop_characterization(
        self, world, capsys, assembled_loops,
    ):
        # Pins the behavior docs/transcript-analysis.md § "routing-guard" states in its Purpose paragraph.
        nothing_to_review = "marker.sh: staged diff is empty -- nothing to review. Exiting without writing a marker."
        records = _round(_BRANCH, "a", 0, marker=False)
        records += [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(10), content=[_skill_block("sk-b", "code-review")]),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(11), content=[_marker_write_use("mk-b")]),
            _marker_write_result("mk-b", minute=11, text=nothing_to_review),
            _user_msg("prompt after b", branch=_BRANCH, ts=_ts(12)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]

    def test_a_marker_write_in_a_non_last_round_ends_its_loop_there_and_later_rounds_are_dropped(
        self, world, capsys, assembled_loops,
    ):
        records = (
            _round(_BRANCH, "a", 0, marker=False, input_tokens=1_000)
            + _round(_BRANCH, "b", 10, marker=True, input_tokens=2_000)
            + _round(_BRANCH, "c", 20, marker=False, input_tokens=4_000)
        )
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loops = _stored_loops(world, _BRANCH)
        assert _round_counts_per_loop(assembled_loops) == [2]
        assert len(loops) == 1
        assert loops[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 2 * 1_000 + 2 * 2_000

    def test_a_denied_skill_and_its_orchestrator_spawn_form_one_loop_whose_overhead_includes_the_denied_turn(self, world, capsys):
        project_dir = _write_session(world, _key_on_loop(), log=_ROUTING_ON)
        _write_marker_writing_orchestrator(project_dir)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        loops = _stored_loops(world, _BRANCH)
        assert outcome.out.count("captured (") == 1
        assert (
            "captured (2 rounds, 1 loops, 0 with growth defined, 1 denied round-opens joined, degraded: no,"
            " states: routing=on/consult-pilot=off/fix-plan-pilot=off, earliest record 2026-08-01)"
        ) in outcome.out
        assert loops[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 40_000 + 100_000 + 5_000

    def test_a_key_on_loop_whose_orchestrator_transcript_is_missing_has_no_complete_loop(self, world, capsys):
        _write_session(world, _key_on_loop(), log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "missing (no-complete-loop)" in outcome.out
        assert _published_files(world) == []

    def test_a_task_notification_inside_a_round_does_not_close_it(self, world, capsys):
        records = [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(0), content=[_skill_block("sk-a", "code-review")]),
            _task_notification_msg(branch=_BRANCH, ts=_ts(1)),
            _priced(_SONNET, input=2_000, branch=_BRANCH, ts=_ts(2), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=2),
            _user_msg("next task", branch=_BRANCH, ts=_ts(3)),
            _priced(_SONNET, input=9_000, branch=_BRANCH, ts=_ts(4)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loop = _stored_loops(world, _BRANCH)[0]
        assert loop["overhead_tokens"][_SONNET]["standard"]["input"] == 1_000 + 2_000
        assert loop["later_turns"] == {_SONNET: 1}

    def test_a_typed_code_review_opens_a_round(self, world, capsys, assembled_loops):
        records = [
            _slash_user("code-review", branch=_BRANCH, ts=_ts(0)),
            _priced(_SONNET, input=3_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=1),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [1]
        assert _stored_loops(world, _BRANCH)[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 3_000

    def test_sessions_in_reverse_path_order_still_sort_by_timestamp(self, world, capsys, assembled_loops):
        earlier_but_later_path = _round(_BRANCH, "a", 0, marker=False)
        later_but_earlier_path = _round(_BRANCH, "b", 30, marker=True)
        _write_session(world, earlier_but_later_path, session_id="zzz-first-in-time", log=_KEYS_OFF)
        _write_session(world, later_but_earlier_path, session_id="aaa-last-in-time", log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]
        assert [loop.growth_tokens for loop in assembled_loops[0]] == [None]

    def test_assemble_loops_orders_by_time_then_session_then_open_index(self):
        rounds = [
            _round_facts(session_key="b", open_idx=0, open_ts=5.0, has_marker_write=True),
            _round_facts(session_key="a", open_idx=3, open_ts=5.0),
            _round_facts(session_key="a", open_idx=1, open_ts=5.0),
            _round_facts(session_key="z", open_idx=0, open_ts=None),
        ]

        loops = routing_guard._assemble_loops(rounds)

        assert [loop.round_count for loop in loops] == [3]

    def test_a_round_with_no_timestamp_sorts_last(self):
        rounds = [
            _round_facts(session_key="a", open_idx=0, open_ts=None, has_marker_write=True),
            _round_facts(session_key="b", open_idx=0, open_ts=9.0, has_marker_write=True, context_at_window_end=100),
        ]

        loops = routing_guard._assemble_loops(rounds)

        assert [loop.growth_tokens for loop in loops] == [90, 30]

    def test_growth_of_a_two_round_loop_in_one_session_runs_from_the_first_rounds_open_to_the_last_rounds_window_end(self):
        first_round = _round_facts(open_idx=0, open_ts=1.0, context_at_open=10, context_at_window_end=40)
        closing_round = _round_facts(
            open_idx=5, open_ts=2.0, has_marker_write=True, context_at_open=70, context_at_window_end=100,
        )

        loops = routing_guard._assemble_loops([first_round, closing_round])

        assert [loop.growth_tokens for loop in loops] == [100 - 10]

    def test_each_loop_carries_the_later_turns_of_its_own_closing_round_not_of_its_first_round(self):
        first_round = _round_facts(open_idx=0, open_ts=1.0, later_turns={"model-of-the-first-round": 1})
        first_closing_round = _round_facts(
            open_idx=5, open_ts=2.0, has_marker_write=True, later_turns={"model-of-the-first-closing-round": 2},
        )
        second_closing_round = _round_facts(
            open_idx=9, open_ts=3.0, has_marker_write=True, later_turns={"model-of-the-second-closing-round": 3},
        )

        loops = routing_guard._assemble_loops([first_round, first_closing_round, second_closing_round])

        assert [loop.later_turns for loop in loops] == [
            {"model-of-the-first-closing-round": 2}, {"model-of-the-second-closing-round": 3},
        ]

    def test_a_dangling_dispatch_in_only_the_first_round_marks_the_whole_loop_degraded(self):
        first_round = _round_facts(open_idx=0, open_ts=1.0, degraded=True)
        closing_round = _round_facts(open_idx=5, open_ts=2.0, has_marker_write=True, degraded=False)

        loops = routing_guard._assemble_loops([first_round, closing_round])

        assert [loop.degraded for loop in loops] == [True]

    def test_a_denied_open_with_no_later_spawn_round_joins_nothing(self):
        denied_round = _round_facts(open_idx=0, open_ts=1.0, denied_open=True)
        closing_round = _round_facts(open_idx=5, open_ts=2.0, has_marker_write=True, spawn_open=False)

        loops = routing_guard._assemble_loops([denied_round, closing_round])

        assert [loop.joined_denied_opens for loop in loops] == [0]

    def test_a_denied_open_followed_by_a_spawn_round_joins_once(self):
        denied_round = _round_facts(open_idx=0, open_ts=1.0, denied_open=True)
        spawn_round = _round_facts(open_idx=5, open_ts=2.0, has_marker_write=True, spawn_open=True)

        loops = routing_guard._assemble_loops([denied_round, spawn_round])

        assert [loop.joined_denied_opens for loop in loops] == [1]

    def test_two_rounds_of_one_loop_merge_overlapping_and_unique_keys_by_adding_each_token_class(self):
        first_round = _round_facts(open_idx=0, overhead_tokens={
            _SONNET: {"standard": {"input": 10, "cache_read": 400}, "fast_us_geo": {"cache_write_1h": 30}},
        })
        closing_round = _round_facts(open_idx=1, has_marker_write=True, overhead_tokens={
            _SONNET: {"standard": {"input": 5, "output": 2, "cache_read": 100}},
            _OTHER_MODEL: {"fast_us_geo": {"cache_write_1h": 7, "cache_read": 9}},
        })
        merged = {
            _SONNET: {
                "standard": {"input": 15, "output": 2, "cache_read": 500},
                "fast_us_geo": {"cache_write_1h": 30},
            },
            _OTHER_MODEL: {"fast_us_geo": {"cache_write_1h": 7, "cache_read": 9}},
        }

        loops = routing_guard._assemble_loops([first_round, closing_round])

        assert routing_guard._merge_overhead_tokens([first_round, closing_round]) == merged
        assert [loop.overhead_tokens for loop in loops] == [merged]


# --- growth and later turns --------------------------------------------------


class TestGrowthAndLaterTurns:
    def test_same_session_growth_is_the_difference_of_the_two_contexts_on_split_records(self, world, capsys):
        open_a = _priced(_SONNET, input=100_000, branch=_BRANCH, ts=_ts(0), request_id="req-open",
                         content=[_skill_block("sk-a", "code-review")])
        open_b = _priced(_SONNET, input=100_000, output=200, branch=_BRANCH, ts=_ts(0), request_id="req-open", content=[])
        later_a = _priced(_SONNET, input=150_000, branch=_BRANCH, ts=_ts(5), request_id="req-later")
        later_b = _priced(_SONNET, input=150_000, output=50, branch=_BRANCH, ts=_ts(5), request_id="req-later")
        records = [open_a, open_b, _priced(_SONNET, input=1, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
                   _marker_write_result("mk-a", minute=1), _user_msg("next", branch=_BRANCH, ts=_ts(4)), later_a, later_b]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loop = _stored_loops(world, _BRANCH)[0]
        assert loop["growth_tokens"] == 150_000 - 100_000
        assert loop["later_turns"] == {_SONNET: 1}

    def test_growth_is_the_difference_of_the_full_contexts_when_the_turns_carry_cache_tokens(self, world, capsys):
        # Context is input plus cache reads plus both cache-write classes. input_tokens alone is 5 and 7 here.
        records = [
            _priced(_SONNET, input=5, cache_read=60_000, ephemeral_1h=30_000, ephemeral_5m=10_000, branch=_BRANCH,
                    ts=_ts(0), content=[_skill_block("sk-a", "code-review")]),
            _priced(_SONNET, input=1, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=1),
            _user_msg("next task", branch=_BRANCH, ts=_ts(2)),
            _priced(_SONNET, input=7, cache_read=70_000, ephemeral_1h=40_000, ephemeral_5m=20_000, branch=_BRANCH, ts=_ts(3)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        later_context = 7 + 70_000 + 40_000 + 20_000
        opening_context = 5 + 60_000 + 30_000 + 10_000
        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] == later_context - opening_context

    def test_a_loop_spanning_two_sessions_stores_null_growth(self, world, capsys):
        _write_session(world, _round(_BRANCH, "a", 0, marker=False), session_id="sess-1", log=_KEYS_OFF)
        _write_session(world, _round(_BRANCH, "b", 30, marker=True), session_id="sess-2", log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] is None

    def test_a_loop_with_no_turn_after_its_window_end_stores_null_growth(self, world, capsys):
        records = [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(0), content=[_skill_block("sk-a", "code-review")]),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=1),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "0 with growth defined" in outcome.out
        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] is None
        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {}

    def test_a_compaction_that_shrinks_context_stores_negative_growth(self, world, capsys):
        records = _clean_loop(_BRANCH, later_input=20_000)
        records.insert(3, _compact_boundary_rec())
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] == 20_000 - 100_000

    def test_later_turns_count_only_outside_every_review_window_a_plan_review_window_included(self, world, capsys):
        records = _clean_loop(_BRANCH) + [
            _priced("model-in-plan-review", input=1, branch=_BRANCH, ts=_ts(10), content=[_skill_block("sk-p", "plan-review")]),
            _priced("model-in-plan-review", input=1, branch=_BRANCH, ts=_ts(11)),
            _user_msg("after the plan review", branch=_BRANCH, ts=_ts(12)),
            _priced("model-after-every-window", input=1, branch=_BRANCH, ts=_ts(13)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {_SONNET: 1, "model-after-every-window": 1}

    def test_later_turns_start_at_the_closing_rounds_window_end_not_at_the_session_start_or_the_first_round(
        self, world, capsys, assembled_loops,
    ):
        records = [
            _priced("model-before-the-loop", input=1, branch=_BRANCH, ts=_ts(0)),
            *_round(_BRANCH, "a", 5, marker=False),
            _priced("model-between-the-rounds", input=1, branch=_BRANCH, ts=_ts(8)),
            *_round(_BRANCH, "b", 10, marker=True),
            _priced("model-after-the-loop", input=1, branch=_BRANCH, ts=_ts(14)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _round_counts_per_loop(assembled_loops) == [2]
        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {"model-after-the-loop": 1}

    def test_a_turn_after_a_second_loop_in_the_same_session_counts_for_both_loops(self, world, capsys):
        records = _round(_BRANCH, "a", 0, marker=True) + _round(_BRANCH, "b", 10, marker=True) + [
            _priced("model-after-both", input=1, branch=_BRANCH, ts=_ts(20)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        first_loop, second_loop = _stored_loops(world, _BRANCH)
        assert first_loop["later_turns"] == {"model-after-both": 1}
        assert second_loop["later_turns"] == {"model-after-both": 1}

    def test_sidechain_and_synthetic_records_are_not_later_turns(self, world, capsys):
        records = _clean_loop(_BRANCH) + [
            _as_sidechain(_priced("model-in-sidechain", input=1, branch=_BRANCH, ts=_ts(10))),
            _priced(pricing._SYNTHETIC_MODEL_ID, input=1, branch=_BRANCH, ts=_ts(11)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {_SONNET: 1}

    def test_an_unpriced_later_turn_is_counted_under_its_model_and_leaves_degraded_false(self, world, capsys):
        records = _clean_loop(_BRANCH) + [_priced("model-the-table-lacks", input=1, branch=_BRANCH, ts=_ts(10))]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loop = _stored_loops(world, _BRANCH)[0]
        assert loop["later_turns"]["model-the-table-lacks"] == 1
        assert loop["degraded"] is False

    def test_an_unpriced_turn_inside_a_round_is_stored_under_its_model_and_leaves_degraded_false(self, world, capsys):
        records = [
            _priced(
                "model-the-table-lacks", input=7_000, branch=_BRANCH, ts=_ts(0),
                content=[_skill_block("sk-a", "code-review")],
            ),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=1),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loop = _stored_loops(world, _BRANCH)[0]
        assert loop["overhead_tokens"]["model-the-table-lacks"]["standard"]["input"] == 7_000
        assert loop["degraded"] is False

    def test_a_dangling_dispatch_marks_the_loop_degraded(self, world, capsys):
        records = [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(0),
                    content=[_skill_block("sk-a", "code-review"), _agent_use("no-such-dispatch", "staff-sdet")]),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=1),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "degraded: yes" in outcome.out
        assert _stored_loops(world, _BRANCH)[0]["degraded"] is True
        assert outcome.code == 0, outcome.err
        assert "PRICING INTEGRITY" not in outcome.out
        assert pricing._subagent_format_drift_detected is False

    def test_an_in_file_sidechain_record_after_the_loop_leaves_the_stored_loop_unchanged(self, world, capsys):
        project_dir = _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        _run(capsys, world)
        baseline = _stored_loops(world, _BRANCH)
        (world.captures_dir / _digest_name(_BRANCH)).unlink()
        extra_records = _clean_loop(_BRANCH) + [_as_sidechain(_priced(_SONNET, input=900_000, branch=_BRANCH, ts=_ts(1)))]
        _write_jsonl(project_dir / "sess-1.jsonl", [{"sessionId": "sess-1", **rec} for rec in extra_records])

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH) == baseline

    def test_an_in_file_sidechain_record_inside_the_window_counts_as_overhead_but_not_as_growth_or_a_later_turn(
        self, world, capsys,
    ):
        clean = _clean_loop(_BRANCH)
        sidechain_turn = _as_sidechain(_priced(_SONNET, input=900_000, branch=_BRANCH, ts=_ts(0)))
        _write_session(world, [clean[0], sidechain_turn, *clean[1:]], log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert _stored_loops(world, _BRANCH) == [{
            "overhead_tokens": _standard_tokens(input=110_000 + 900_000, output=500),
            "growth_tokens": 30_000, "later_turns": {_SONNET: 1}, "degraded": False,
        }]

    @pytest.mark.parametrize("non_string_model", [["claude"], None], ids=["list", "none"])
    def test_a_later_turn_whose_model_is_not_a_string_is_stored_under_the_empty_model(
        self, world, capsys, non_string_model,
    ):
        records = _clean_loop(_BRANCH)
        records[-1]["message"]["model"] = non_string_model
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {"": 1}

    def test_the_scan_asks_for_main_thread_session_files_without_merging_subagent_transcripts(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        include_subagents_values: list[bool] = []
        real_iter_scoped_sessions = scope._iter_scoped_sessions

        def spy_on_include_subagents(slugs, include_subagents, *args, **kwargs):
            include_subagents_values.append(include_subagents)
            return real_iter_scoped_sessions(slugs, include_subagents, *args, **kwargs)

        monkeypatch.setattr(scope, "_iter_scoped_sessions", spy_on_include_subagents)

        _run(capsys, world)

        assert include_subagents_values == [False]


# --- malformed usage ---------------------------------------------------------


_MALFORMED_INPUT_TOKENS = pytest.mark.parametrize(
    "malformed_input_tokens", ["x", -5_000], ids=["non-numeric", "negative"],
)


def _loop_turn_with_malformed_input_tokens(malformed_input_tokens, *, minute: int, sidechain: bool = False) -> dict:
    turn = _priced(_SONNET, input=1, branch=_BRANCH, ts=_ts(minute))
    turn["message"]["usage"]["input_tokens"] = malformed_input_tokens
    return _as_sidechain(turn) if sidechain else turn


def _standard_tokens(*, input: int, output: int = 0) -> dict:
    return {_SONNET: {"standard": {
        "input": input, "output": output, "cache_read": 0, "cache_write_1h": 0, "cache_write_5m": 0,
    }}}


class TestMalformedUsage:
    """`_clean_loop`'s valid turns give overhead of 110,000 input and 500 output tokens, growth of 30,000 and one later turn."""

    @_MALFORMED_INPUT_TOKENS
    def test_a_malformed_main_thread_turn_inside_the_window_is_excluded_and_degrades_its_loop(
        self, world, capsys, malformed_input_tokens,
    ):
        clean = _clean_loop()
        records = [clean[0], _loop_turn_with_malformed_input_tokens(malformed_input_tokens, minute=0), *clean[1:]]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert _stored_loops(world, _BRANCH) == [{
            "overhead_tokens": _standard_tokens(input=110_000, output=500),
            "growth_tokens": 30_000, "later_turns": {_SONNET: 1}, "degraded": True,
        }]

    @_MALFORMED_INPUT_TOKENS
    def test_a_malformed_round_opening_turn_is_excluded_and_growth_starts_at_the_next_valid_turn(
        self, world, capsys, malformed_input_tokens,
    ):
        clean = _clean_loop()
        opener = clean[0]
        opener["message"]["usage"]["input_tokens"] = malformed_input_tokens
        _write_session(world, [opener, *clean[1:]], log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert _stored_loops(world, _BRANCH) == [{
            "overhead_tokens": _standard_tokens(input=10_000, output=500),
            "growth_tokens": 120_000, "later_turns": {_SONNET: 1}, "degraded": True,
        }]

    @_MALFORMED_INPUT_TOKENS
    def test_a_malformed_main_thread_turn_after_the_loop_is_ignored_by_growth_and_later_turns(
        self, world, capsys, malformed_input_tokens,
    ):
        clean = _clean_loop()
        records = [*clean[:-1], _loop_turn_with_malformed_input_tokens(malformed_input_tokens, minute=2), clean[-1]]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert _stored_loops(world, _BRANCH) == [{
            "overhead_tokens": _standard_tokens(input=110_000, output=500),
            "growth_tokens": 30_000, "later_turns": {_SONNET: 1}, "degraded": False,
        }]

    @_MALFORMED_INPUT_TOKENS
    def test_a_malformed_turn_in_a_nested_dispatch_of_the_round_is_excluded_and_degrades_its_loop(
        self, world, capsys, malformed_input_tokens,
    ):
        records = _clean_loop()
        records[0]["message"]["content"].append(_agent_use("a1", "staff-sdet"))
        project_dir = _write_session(world, records, log=_KEYS_OFF)
        _write_subagent_dispatch(project_dir, "sess-1", "agent-a", "a1", [_as_sidechain(_priced(
            _SONNET, input=7_000, branch=_BRANCH, ts=_ts(0), content=[_agent_use("n1", "staff-sdet")],
        ))])
        _write_subagent_dispatch(project_dir, "sess-1", "agent-n", "n1", [
            _as_sidechain(_priced(_SONNET, input=3_000, branch=_BRANCH, ts=_ts(0))),
            _loop_turn_with_malformed_input_tokens(malformed_input_tokens, minute=0, sidechain=True),
        ], spawn_depth=2)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert _stored_loops(world, _BRANCH) == [{
            "overhead_tokens": _standard_tokens(input=110_000 + 7_000 + 3_000, output=500),
            "growth_tokens": 30_000, "later_turns": {_SONNET: 1}, "degraded": True,
        }]


# --- deny reconciliation -----------------------------------------------------


class TestDenyExtraction:
    @pytest.mark.parametrize("shape", ["current", "legacy"])
    def test_both_deny_shapes_are_extracted_and_deduped_by_tool_use_id(self, tmp_path, shape):
        scan = routing_guard._ScanFacts()
        records = [
            _denied_skill_call("deny-1", "deny-2"),
            _routing_deny_record("deny-1", shape=shape, branch=_BRANCH, ts=_ts(1)),
            _routing_deny_record("deny-1", shape=shape, branch=_BRANCH, ts=_ts(2)),
            _routing_deny_record("deny-2", shape=shape, branch=_BRANCH, ts=_ts(3)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert [deny.branch for deny in scan.denies] == [_BRANCH, _BRANCH]

    def test_one_deny_recorded_in_both_shapes_is_counted_once(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [
            _denied_skill_call("deny-1"),
            _routing_deny_record("deny-1", shape="legacy", branch=_BRANCH, ts=_ts(1)),
            _routing_deny_record("deny-1", shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 1

    @pytest.mark.parametrize("denial_record", [
        pytest.param(_routing_deny_record("deny-legacy", shape="legacy", ts=_ts(1)), id="attachment-with-a-dict-error"),
        pytest.param(_hook_deny("alpha-gate", stringified=True, ts=_ts(1)), id="attachment-with-a-stringified-error"),
        pytest.param(_routing_deny_record("deny-current", shape="current", ts=_ts(1)), id="is-error-tool-result"),
        pytest.param(
            _hook_deny_current("Blocked by alpha gate: body", tool_id="deny-other", ts=_ts(1)), id="other-gate-tool-result",
        ),
    ])
    def test_a_denial_record_carries_the_same_tool_use_id_hook_name_and_message_as_in_review_trace(self, denial_record):
        records = [_asst(_SONNET, branch=_BRANCH, ts=_ts(0)), denial_record]

        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(records, None, None, None)

        trace_denials = [
            (event["tool_use_id"], event["hook_name"], event["message"]) for event in events if event["kind"] == "denial"
        ]
        assert len(trace_denials) == 1
        assert list(routing_guard._hook_denials_in_record(denial_record)) == trace_denials

    def test_a_deny_repeated_in_a_second_session_is_counted_once(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [_denied_skill_call("deny-1"), _routing_deny_record("deny-1", branch=_BRANCH, ts=_ts(1))]

        routing_guard._add_session_facts(scan, tmp_path / "s1.jsonl", list(records))
        routing_guard._add_session_facts(scan, tmp_path / "s2.jsonl", list(records))

        assert len(scan.denies) == 1

    def test_a_deny_already_counted_in_an_earlier_session_still_marks_this_sessions_denied_skill_open(self, tmp_path):
        scan = routing_guard._ScanFacts()
        second_session = tmp_path / "s2.jsonl"

        routing_guard._add_session_facts(scan, tmp_path / "s1.jsonl", _key_on_loop())
        routing_guard._add_session_facts(scan, second_session, _key_on_loop())

        skill_opens_of_second_session = [
            round_facts for round_facts in scan.rounds_by_branch[_BRANCH]
            if round_facts.session_key == str(second_session) and round_facts.open_idx == 0
        ]
        assert len(scan.denies) == 1
        assert [round_facts.denied_open for round_facts in skill_opens_of_second_session] == [True]

    @pytest.mark.parametrize("answered_call", [
        pytest.param(_bash_use("deny-1", "pytest hooks/tests"), id="a-bash-result-carrying-the-gate-wording"),
        pytest.param(_skill_block("deny-1", "plan-review"), id="a-skill-call-other-than-code-review"),
        pytest.param(None, id="a-result-with-no-tool-use-in-the-session"),
    ])
    def test_a_deny_whose_tool_use_is_not_a_code_review_skill_call_is_not_counted(self, tmp_path, answered_call):
        scan = routing_guard._ScanFacts()
        records = [_routing_deny_record("deny-1", branch=_BRANCH, ts=_ts(1))]
        if answered_call is not None:
            records.insert(0, _asst(_SONNET, branch=_BRANCH, ts=_ts(0), content=[answered_call]))

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert scan.denies == []

    def test_a_harness_attested_deny_with_no_tool_use_id_is_counted_each_time_beside_a_code_review_skill_call(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [
            _denied_skill_call("deny-1"),
            _routing_deny_record("", shape="legacy", branch=_BRANCH, ts=_ts(1)),
            _routing_deny_record("", shape="legacy", branch=_BRANCH, ts=_ts(2)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 2

    def test_a_text_derived_deny_with_no_tool_use_id_is_not_counted_beside_a_code_review_skill_call(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [
            _denied_skill_call("deny-1", ""),
            _routing_deny_record("", shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert scan.denies == []

    def test_a_plugin_qualified_code_review_skill_call_joins_a_text_derived_deny(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0), content=[_skill_block("deny-1", "plugin:code-review")]),
            _routing_deny_record("deny-1", shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 1

    def test_a_code_review_skill_call_in_another_session_does_not_join_a_text_derived_deny(self, tmp_path):
        scan = routing_guard._ScanFacts()

        routing_guard._add_session_facts(scan, tmp_path / "s1.jsonl", [_denied_skill_call("deny-1")])
        routing_guard._add_session_facts(
            scan, tmp_path / "s2.jsonl", [_routing_deny_record("deny-1", shape="current", branch=_BRANCH, ts=_ts(1))],
        )

        assert scan.denies == []
        assert scan.dropped_deny_without_tool_use

    @pytest.mark.parametrize("unjoined_call", [
        pytest.param(_bash_use("deny-x", "pytest hooks/tests"), id="id-of-a-bash-tool-use"),
        pytest.param(None, id="id-with-no-tool-use-in-the-session"),
    ])
    def test_a_harness_attested_deny_counts_once_when_its_id_joins_no_code_review_skill_call(self, tmp_path, unjoined_call):
        scan = routing_guard._ScanFacts()
        records = [_routing_deny_record("deny-x", shape="legacy", branch=_BRANCH, ts=_ts(1))]
        if unjoined_call is not None:
            records.insert(0, _asst(_SONNET, branch=_BRANCH, ts=_ts(0), content=[unjoined_call]))

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 1
        assert not scan.dropped_deny_without_tool_use

    @pytest.mark.parametrize("session_records", [
        pytest.param(
            [
                _as_sidechain(_denied_skill_call("deny-1")),
                _routing_deny_record("deny-1", shape="current", branch=_BRANCH, ts=_ts(1)),
            ],
            id="the-code-review-call-is-on-a-sidechain",
        ),
        pytest.param(
            [
                {**_denied_skill_call("deny-1"), "type": "user"},
                _routing_deny_record("deny-1", shape="current", branch=_BRANCH, ts=_ts(1)),
            ],
            id="the-code-review-call-is-in-a-user-record",
        ),
        pytest.param(
            [
                _denied_skill_call("other-call"),
                _legacy_routing_deny_with_hook_name("deny-1", "  "),
            ],
            id="a-whitespace-only-hook-name-is-text-derived",
        ),
    ])
    def test_a_text_derived_deny_does_not_join_a_call_that_is_not_a_main_thread_assistant_code_review_call(
        self, tmp_path, session_records,
    ):
        scan = routing_guard._ScanFacts()

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", session_records)

        assert scan.denies == []
        assert scan.dropped_deny_without_tool_use

    def test_malformed_content_elements_beside_a_valid_code_review_call_neither_raise_nor_count(self, tmp_path):
        scan = routing_guard._ScanFacts()
        skill_call_without_id = {"type": "tool_use", "name": "Skill", "input": {"skill": "code-review"}}
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0), content=[
                "a plain string element",
                skill_call_without_id,
                {**skill_call_without_id, "id": ["list-id"]},
                _skill_block("deny-valid", "code-review"),
            ]),
            _routing_deny_record("deny-valid", shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 1

    @pytest.mark.parametrize("malformed_input", [
        pytest.param({"skill": 5}, id="a-non-string-skill"),
        pytest.param(["code-review"], id="a-list-input"),
        pytest.param("code-review", id="a-string-input"),
    ])
    def test_a_skill_call_with_malformed_input_after_a_code_review_call_joins_the_tool_use_ids_only(
        self, malformed_input,
    ):
        malformed_skill_call = {"type": "tool_use", "id": "malformed-1", "name": "Skill", "input": malformed_input}
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0), content=[_skill_block("valid-1", "code-review"), malformed_skill_call]),
        ]

        tool_use_ids, code_review_skill_ids = routing_guard._main_thread_tool_use_ids(records)

        assert tool_use_ids == {"valid-1", "malformed-1"}
        assert code_review_skill_ids == {"valid-1"}

    @pytest.mark.parametrize("malformed_input", [
        pytest.param({"skill": 5}, id="a-non-string-skill"),
        pytest.param({"skill": ["code-review"]}, id="a-list-skill"),
        pytest.param(["code-review"], id="a-list-input"),
        pytest.param("code-review", id="a-string-input"),
    ])
    def test_a_code_review_call_after_a_skill_call_with_malformed_input_still_joins_both_id_sets(self, malformed_input):
        malformed_skill_call = {"type": "tool_use", "id": "malformed-1", "name": "Skill", "input": malformed_input}
        records = [
            _asst(
                _SONNET, branch=_BRANCH, ts=_ts(0),
                content=[malformed_skill_call, _skill_block("valid-same-record", "code-review")],
            ),
            _asst(_SONNET, branch=_BRANCH, ts=_ts(1), content=[_skill_block("valid-later-record", "code-review")]),
        ]

        tool_use_ids, code_review_skill_ids = routing_guard._main_thread_tool_use_ids(records)

        assert tool_use_ids == {"malformed-1", "valid-same-record", "valid-later-record"}
        assert code_review_skill_ids == {"valid-same-record", "valid-later-record"}

    def test_a_skill_call_with_non_dict_input_after_a_round_opening_code_review_call_neither_raises_nor_blocks_its_deny(
        self, tmp_path,
    ):
        scan = routing_guard._ScanFacts()
        malformed_skill_call = {"type": "tool_use", "id": "malformed-1", "name": "Skill", "input": ["code-review"]}
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0), content=[_skill_block("deny-valid", "code-review"), malformed_skill_call]),
            _routing_deny_record("deny-valid", shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 1

    @pytest.mark.parametrize("non_string_id", [
        pytest.param(["list-id"], id="a-list-id"),
        pytest.param({"key": "dict-id"}, id="a-dict-id"),
        pytest.param(7, id="an-int-id"),
    ])
    def test_a_text_derived_deny_with_a_non_string_tool_use_id_is_dropped_with_the_notice_flag(self, tmp_path, non_string_id):
        scan = routing_guard._ScanFacts()
        records = [
            _denied_skill_call("deny-1"),
            _routing_deny_record(non_string_id, shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert scan.denies == []
        assert scan.dropped_deny_without_tool_use is True

    @pytest.mark.parametrize("non_string_id", [
        pytest.param(["list-id"], id="a-list-id"),
        pytest.param({"key": "dict-id"}, id="a-dict-id"),
        pytest.param(7, id="an-int-id"),
    ])
    def test_a_harness_attested_deny_with_a_non_string_tool_use_id_is_counted_each_time_without_dedup(
        self, tmp_path, non_string_id,
    ):
        scan = routing_guard._ScanFacts()
        records = [
            _routing_deny_record(non_string_id, shape="legacy", branch=_BRANCH, ts=_ts(1)),
            _routing_deny_record(non_string_id, shape="legacy", branch=_BRANCH, ts=_ts(2)),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert len(scan.denies) == 2
        assert scan.seen_deny_ids == set()

    def test_other_hooks_denials_and_sidechain_denials_are_ignored(self, tmp_path):
        scan = routing_guard._ScanFacts()
        records = [
            _denied_skill_call("deny-1"),
            _hook_deny("require-code-review", branch=_BRANCH, ts=_ts(1)),
            _as_sidechain(_routing_deny_record("deny-1", branch=_BRANCH, ts=_ts(2))),
        ]

        routing_guard._add_session_facts(scan, tmp_path / "s.jsonl", records)

        assert scan.denies == []


_STATE_ON = routing_guard._KeyState("on", None)
_STATE_OFF = routing_guard._KeyState("off", None)
_STATE_UNKNOWN = routing_guard._KeyState(None, "hook-not-run")


class TestReconcileDenies:
    ACCOUNT = Path("/placeholder/account-a")
    OTHER_ACCOUNT = Path("/placeholder/account-b")

    def _reconcile(self, denies, deny_states, rows, *, built=(), branch_facts=None, routing_by_line=None):
        return routing_guard._reconcile_denies(
            denies, deny_states, rows, self.ACCOUNT, set(built), branch_facts or {}, routing_by_line or {},
        )

    def test_a_deny_on_an_unlisted_branch_refuses_with_its_utc_minute_and_not_its_name(self):
        unlisted_ts = datetime(2026, 8, 1, 10, 1, tzinfo=UTC).timestamp()

        result = self._reconcile([routing_guard._DenyFact("unlisted-branch", unlisted_ts, "sess-1")], [_STATE_ON], [])

        assert len(result.refusals) == 1
        assert "2026-08-01 10:01 UTC" in result.refusals[0]
        assert "unlisted-branch" not in result.refusals[0]
        assert "merged if the branch belongs in the sample, else excluded" in result.refusals[0]

    def test_two_unlisted_branches_each_get_a_refusal_in_the_order_of_their_first_deny(self):
        later_minute = datetime(2026, 8, 1, 11, 30, tzinfo=UTC).timestamp()
        earlier_minute = datetime(2026, 8, 1, 9, 5, tzinfo=UTC).timestamp()
        denies = [
            routing_guard._DenyFact("unlisted-one", later_minute, "sess-1"),
            routing_guard._DenyFact("unlisted-two", earlier_minute, "sess-1"),
        ]

        result = self._reconcile(denies, [_STATE_ON, _STATE_ON], [])

        assert len(result.refusals) == 2
        assert "2026-08-01 11:30 UTC" in result.refusals[0]
        assert "2026-08-01 09:05 UTC" in result.refusals[1]

    def test_an_unlisted_branch_with_two_denies_gets_one_refusal_naming_the_earlier_minute(self):
        later_minute = datetime(2026, 8, 1, 11, 30, tzinfo=UTC).timestamp()
        earlier_minute = datetime(2026, 8, 1, 9, 5, tzinfo=UTC).timestamp()
        denies = [
            routing_guard._DenyFact("unlisted-branch", later_minute, "sess-1"),
            routing_guard._DenyFact("unlisted-branch", earlier_minute, "sess-2"),
        ]

        result = self._reconcile(denies, [_STATE_ON, _STATE_ON], [])

        assert len(result.refusals) == 1
        assert "2026-08-01 09:05 UTC" in result.refusals[0]
        assert "2026-08-01 11:30 UTC" not in result.refusals[0]

    def test_an_unlisted_branch_with_a_timed_and_an_untimed_deny_gets_one_refusal_naming_the_timed_minute(self):
        timed_minute = datetime(2026, 8, 1, 9, 5, tzinfo=UTC).timestamp()
        denies = [
            routing_guard._DenyFact("unlisted-branch", None, "sess-1"),
            routing_guard._DenyFact("unlisted-branch", timed_minute, "sess-2"),
        ]

        result = self._reconcile(denies, [_STATE_ON, _STATE_ON], [])

        assert len(result.refusals) == 1
        assert "2026-08-01 09:05 UTC" in result.refusals[0]
        assert "an unknown time" not in result.refusals[0]

    def test_an_unlisted_branch_whose_denies_are_all_untimed_gets_one_refusal_naming_an_unknown_time(self):
        denies = [
            routing_guard._DenyFact("unlisted-branch", None, "sess-1"),
            routing_guard._DenyFact("unlisted-branch", None, "sess-2"),
        ]

        result = self._reconcile(denies, [_STATE_ON, _STATE_ON], [])

        assert len(result.refusals) == 1
        assert "a routing deny at an unknown time falls on a branch the note does not list" in result.refusals[0]

    def test_a_deny_on_an_empty_or_default_branch_prints_a_notice_and_does_not_refuse(self):
        denies = [routing_guard._DenyFact("", 1.0, "sess-1"), routing_guard._DenyFact("main", 2.0, "sess-1")]

        result = self._reconcile(denies, [_STATE_ON, _STATE_ON], [])

        assert result.refusals == []
        assert len(result.notices) == 1
        assert "empty or default-branch" in result.notices[0]

    _ROUTING_OFF_NOTICE = "the routing key reads off for it"

    @pytest.mark.parametrize("routing", ["on", "off"])
    @pytest.mark.parametrize("source", ["derived", "standing"])
    @pytest.mark.parametrize("deny_state", [_STATE_ON, _STATE_OFF, _STATE_UNKNOWN], ids=["on", "off", "unknown"])
    @pytest.mark.parametrize("status", ["merged", "excluded", "unlisted"])
    def test_the_outcome_for_each_deny_state_note_status_arm_source_and_routing_state(
        self, status, deny_state, source, routing,
    ):
        rows = [] if status == "unlisted" else [_note_row(3, self.ACCOUNT, "listed", status)]
        built = {3} if source == "derived" else set()
        routing_by_line = {3: routing}

        result = self._reconcile(
            [routing_guard._DenyFact("listed", 1.0, "sess-1")], [deny_state], rows, built=built, routing_by_line=routing_by_line,
        )

        merged = status == "merged"
        assert (len(result.refusals) == 1) == (status == "unlisted")
        expect_contradiction = merged and source == "derived" and deny_state is _STATE_OFF
        assert result.contradicted_line_nos == ({3} if expect_contradiction else set())
        expect_unknown_notice = merged and deny_state is _STATE_UNKNOWN
        assert any("note line 3" in notice and "unknown" in notice for notice in result.notices) == expect_unknown_notice
        # A contradicted row is itself `missing`, so it needs no notice.
        expect_routing_off_notice = merged and routing == "off" and not expect_contradiction
        assert any(
            "note line 3" in notice and self._ROUTING_OFF_NOTICE in notice for notice in result.notices
        ) == expect_routing_off_notice
        assert len(result.notices) == int(expect_unknown_notice) + int(expect_routing_off_notice)

    @pytest.mark.parametrize("deny_state", [_STATE_ON, _STATE_UNKNOWN], ids=["on", "unknown"])
    def test_a_deny_on_a_merged_row_this_run_builds_with_routing_off_prints_the_notice_a_standing_off_file_gets(
        self, deny_state,
    ):
        rows = [_note_row(3, self.ACCOUNT, "listed")]

        result = self._reconcile(
            [routing_guard._DenyFact("listed", 1.0, "sess-1")], [deny_state], rows, built={3}, routing_by_line={3: "off"},
        )

        assert result.contradicted_line_nos == set()
        assert any("note line 3" in notice and self._ROUTING_OFF_NOTICE in notice for notice in result.notices)

    @pytest.mark.parametrize(
        "deny_states", [[_STATE_ON, _STATE_OFF], [_STATE_OFF, _STATE_ON]], ids=["on-then-off", "off-then-on"],
    )
    def test_a_built_row_with_a_state_on_and_a_state_off_deny_is_contradicted_and_prints_no_routing_off_notice(
        self, deny_states,
    ):
        rows = [_note_row(3, self.ACCOUNT, "listed")]
        denies = [routing_guard._DenyFact("listed", 1.0, "sess-1"), routing_guard._DenyFact("listed", 2.0, "sess-1")]

        result = self._reconcile(denies, deny_states, rows, built={3}, routing_by_line={3: "off"})

        assert result.contradicted_line_nos == {3}
        assert result.notices == []

    @pytest.mark.parametrize("deny_state", [_STATE_ON, _STATE_OFF], ids=["on", "off"])
    def test_a_deny_on_a_merged_row_with_a_standing_key_off_file_prints_a_notice_naming_its_line(self, deny_state):
        rows = [_note_row(3, self.ACCOUNT, "listed")]

        result = self._reconcile(
            [routing_guard._DenyFact("listed", 1.0, "sess-1")], [deny_state], rows, built=(), routing_by_line={3: "off"},
        )

        assert result.contradicted_line_nos == set()
        assert [("note line 3" in notice and self._ROUTING_OFF_NOTICE in notice) for notice in result.notices] == [True]

    def test_a_deny_on_a_standing_key_off_file_prints_its_notice_once_for_several_denies(self):
        rows = [_note_row(3, self.ACCOUNT, "listed")]
        denies = [routing_guard._DenyFact("listed", 1.0, "sess-1"), routing_guard._DenyFact("listed", 2.0, "sess-1")]

        result = self._reconcile(denies, [_STATE_ON, _STATE_ON], rows, built=(), routing_by_line={3: "off"})

        assert len(result.notices) == 1

    def test_a_key_on_row_this_run_builds_with_no_deny_prints_a_notice_naming_its_line(self):
        rows = [_note_row(4, self.ACCOUNT, "no-deny-branch")]

        result = self._reconcile([], [], rows, built={4}, routing_by_line={4: "on"})

        assert [("note line 4" in notice) for notice in result.notices] == [True]

    def test_a_key_off_row_this_run_builds_with_no_deny_prints_no_notice(self):
        rows = [_note_row(4, self.ACCOUNT, "no-deny-branch")]

        assert self._reconcile([], [], rows, built={4}, routing_by_line={4: "off"}).notices == []

    def test_a_key_on_row_this_run_does_not_build_prints_no_denyless_notice(self):
        rows = [_note_row(4, self.ACCOUNT, "no-deny-branch")]

        assert self._reconcile([], [], rows, built=(), routing_by_line={4: "on"}).notices == []

    def test_an_excluded_row_with_no_deny_prints_no_denyless_notice(self):
        rows = [_note_row(4, self.ACCOUNT, "no-deny-branch", "excluded")]

        assert self._reconcile([], [], rows, built={4}, routing_by_line={4: "on"}).notices == []

    def test_a_branch_listed_for_another_config_dir_with_records_here_prints_a_notice_and_never_refuses(self):
        rows = [_note_row(9, self.OTHER_ACCOUNT, "split-branch")]
        facts = {"split-branch": routing_guard._BranchFacts(True, 1.0)}

        result = self._reconcile([routing_guard._DenyFact("split-branch", 1.0, "sess-1")], [_STATE_OFF], rows, branch_facts=facts)

        assert result.refusals == []
        assert result.contradicted_line_nos == set()
        assert any("note line 9" in notice and "split across accounts" in notice for notice in result.notices)

    def test_end_to_end_an_unlisted_branch_deny_refuses_and_publishes_nothing(self, world, capsys):
        records = _clean_loop(_BRANCH) + [
            _denied_skill_call("deny-u", branch="unlisted-work", minute=30),
            _routing_deny_record("deny-u", branch="unlisted-work", ts=_ts(31)),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "2026-08-01 10:31 UTC" in outcome.err
        for load_bearing_token in (
            "falls on a branch the note does not list", "review-trace", "--config-dir", "--deny-only", "--this-repo",
            "--projects=", "docs/transcript-analysis.md",
        ):
            assert load_bearing_token in outcome.err
        assert "--deny-summary" not in outcome.err
        assert "unlisted-work" not in outcome.err + outcome.out
        assert _published_files(world) == []
        assert not world.captures_dir.exists()

    def test_end_to_end_an_ordinary_error_carrying_the_gate_wording_on_an_unlisted_branch_does_not_refuse(
        self, world, capsys,
    ):
        records = _clean_loop(_BRANCH) + [
            _asst(_SONNET, branch="unlisted-work", ts=_ts(30), content=[_bash_use("bash-1", "pytest hooks/tests")]),
            _routing_deny_record("bash-1", branch="unlisted-work", ts=_ts(31)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert "falls on a branch the note does not list" not in outcome.err
        assert len(_published_files(world)) == 1

    def test_end_to_end_text_derived_denies_whose_tool_use_is_not_in_the_session_print_one_notice_per_run(
        self, world, capsys,
    ):
        records = _clean_loop(_BRANCH) + [
            _routing_deny_record("orphan-1", branch=_BRANCH, ts=_ts(31)),
            _routing_deny_record("orphan-2", branch=_BRANCH, ts=_ts(32)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert outcome.err.count(routing_guard._DROPPED_DENY_NOTICE) == 1

    def test_end_to_end_a_text_derived_deny_answering_a_bash_call_prints_no_dropped_deny_notice(self, world, capsys):
        records = _clean_loop(_BRANCH) + [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(30), content=[_bash_use("bash-1", "pytest hooks/tests")]),
            _routing_deny_record("bash-1", branch=_BRANCH, ts=_ts(31)),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert routing_guard._DROPPED_DENY_NOTICE not in outcome.err

    def test_end_to_end_a_text_derived_deny_with_an_empty_tool_use_id_is_not_counted_and_prints_the_notice_once(
        self, world, capsys,
    ):
        records = _clean_loop(_BRANCH) + [_routing_deny_record("", branch=_BRANCH, ts=_ts(31))]
        _write_session(world, records, log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert outcome.err.count(routing_guard._DROPPED_DENY_NOTICE) == 1
        assert "no routing deny was found for it" in outcome.err

    def test_end_to_end_a_text_derived_deny_answering_a_non_code_review_skill_call_is_not_counted_and_prints_no_notice(
        self, world, capsys,
    ):
        records = _clean_loop(_BRANCH) + [
            _priced(_SONNET, input=1, branch=_BRANCH, ts=_ts(30), content=[_skill_block("plan-1", "plan-review")]),
            _routing_deny_record("plan-1", branch=_BRANCH, ts=_ts(31)),
        ]
        _write_session(world, records, log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert routing_guard._DROPPED_DENY_NOTICE not in outcome.err
        assert "no routing deny was found for it" in outcome.err

    def test_end_to_end_a_rerun_with_every_row_already_captured_refuses_an_unlisted_branch_deny_added_since(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        first = _run(capsys, world)
        published_before = {path.name: path.read_bytes() for path in _published_files(world)}
        _write_session(world, [
            _denied_skill_call("deny-u", branch="unlisted-work", minute=30),
            _routing_deny_record("deny-u", branch="unlisted-work", ts=_ts(31)),
        ], session_id="sess-2", log=_KEYS_OFF)

        second = _run(capsys, world)

        assert first.code == 0
        assert second.code == 2
        assert "falls on a branch the note does not list" in second.err
        assert second.out == routing_guard._DO_NOT_PUBLISH_BANNER + "\n"
        assert {path.name: path.read_bytes() for path in _published_files(world)} == published_before

    def test_end_to_end_a_deny_where_the_log_says_off_reads_missing_contradicted_while_other_rows_proceed(
        self, world, capsys,
    ):
        _two_branch_sessions(world)
        _write_session(world, [
            _denied_skill_call("deny-b", branch=_BETA, minute=30),
            _routing_deny_record("deny-b", branch=_BETA, ts=_ts(31)),
        ], session_id="sess-2", log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: captured" in outcome.out
        assert "note line 2: missing (key-state-contradicted)" in outcome.out
        assert [path.name for path in _published_files(world)] == [_digest_name(_BRANCH)]

    def test_end_to_end_a_deny_where_the_log_says_off_on_a_row_missing_for_another_reason_keeps_that_reason_no_contradicted_line(
        self, world, capsys,
    ):
        _write_session(world, [
            _denied_skill_call("deny-a", minute=30),
            _routing_deny_record("deny-a", ts=_ts(31)),
        ], log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (no-complete-loop)" in outcome.out
        assert routing_guard._NEXT_STEP_LINES["key-state-contradicted"] not in outcome.out.splitlines()

    def test_end_to_end_a_legacy_deny_in_a_session_with_no_code_review_round_still_reads_that_sessions_log_row(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(_BRANCH), session_id="sess-a", log=_KEYS_OFF)
        _write_session(world, [
            _routing_deny_record("deny-legacy", shape="legacy", branch=_BRANCH, ts=_ts(31)),
        ], session_id="sess-deny-only", log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (key-state-contradicted)" in outcome.out

    def test_end_to_end_a_deny_where_the_log_leaves_the_state_unknown_prints_a_notice_naming_the_note_line(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_session(world, [
            _denied_skill_call("deny-b", branch=_BRANCH, minute=30),
            _routing_deny_record("deny-b", branch=_BRANCH, ts=_ts(31)),
        ], session_id="sess-2", log=None)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert "note line 1 holds a routing deny, but the log leaves the routing state at that deny unknown" in outcome.err
        notice_lines = [line for line in outcome.err.splitlines() if "routing state at that deny unknown" in line]
        assert len(notice_lines) == 1
        assert notice_lines[0].endswith("(hook-not-run)")
        assert routing_guard._DENY_UNTIMED_PHRASE not in notice_lines[0]

    def test_end_to_end_a_deny_with_no_session_id_prints_a_deny_phrase_instead_of_the_round_cause_token(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_session(world, [
            _denied_skill_call("deny-b", branch=_BRANCH, minute=30),
            {**_routing_deny_record("deny-b", branch=_BRANCH, ts=_ts(31)), "sessionId": None},
        ], session_id="sess-2", log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert (
            "note line 1 holds a routing deny, but the log leaves the routing state at that deny unknown"
            f" ({routing_guard._DENY_UNTIMED_PHRASE})"
        ) in outcome.err
        assert "round-untimed" not in outcome.err
        assert not any(ch.isdigit() for ch in routing_guard._DENY_UNTIMED_PHRASE)

    def test_end_to_end_a_deny_after_the_last_marker_where_the_log_says_on_prints_a_notice_for_a_row_built_key_off(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_session(world, [
            _denied_skill_call("deny-b", branch=_BRANCH, minute=30),
            _routing_deny_record("deny-b", branch=_BRANCH, ts=_ts(31)),
        ], session_id="sess-2", log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert "note line 1: captured" in outcome.out
        assert "note line 1 holds a routing deny, but the routing key reads off for it" in outcome.err

    def test_end_to_end_a_rerun_with_a_deny_added_to_an_already_captured_key_off_row_prints_the_standing_notice(
        self, world, capsys,
    ):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        first = _run(capsys, world)
        published_before = {path.name: path.read_bytes() for path in _published_files(world)}
        _write_session(world, [
            _denied_skill_call("deny-b", branch=_BETA, minute=30),
            _routing_deny_record("deny-b", branch=_BETA, ts=_ts(31)),
        ], session_id="sess-2", log=_ROUTING_ON)

        second = _run(capsys, world)

        assert first.code == 0
        assert second.code == 0
        assert "note line 2 holds a routing deny, but the routing key reads off for it" in second.err
        assert {path.name: path.read_bytes() for path in _published_files(world)} == published_before


# --- memory ------------------------------------------------------------------


class _TrackedRecord(dict):
    """A weakref-able record."""


class _TrackedRecordList(list):
    """A weakref-able session record list."""


class TestSessionRecordsAreDroppedPerSession:
    def test_a_capture_keeps_no_session_alive_past_the_next_session(self, world, capsys, monkeypatch, assembled_loops):
        session_count = 5
        live_lists: list[weakref.ref] = []
        live_records: list[list[weakref.ref]] = []
        survivors: list[str] = []
        project_dir = world.projects / world.main_slug

        def sessions(slugs, include_subagents, roots=None, *, scan_gaps=None):
            for k in range(session_count):
                gc.collect()
                if k >= 2:
                    if live_lists[k - 2]() is not None:
                        survivors.append(f"session {k - 2}'s record list")
                    survivors.extend(f"a record of session {k - 2}" for ref in live_records[k - 2] if ref() is not None)
                session_id = f"session-{k}"
                # A lost-event attachment per session, so the memory check covers the recorder facts too.
                session_records = [
                    _recorder_attachment(session_id, _ts(30, hour=9)), *_clean_loop(_BRANCH, start=k * 10, tag=str(k)),
                ]
                records = _TrackedRecordList(_TrackedRecord({"sessionId": session_id, **rec}) for rec in session_records)
                live_lists.append(weakref.ref(records))
                live_records.append([weakref.ref(rec) for rec in records])
                yield project_dir / f"session-{k}.jsonl", records

        monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions)
        # Each row follows its session's lost event, so the event hides no round.
        _write_log(world.config_dir, *[_log_row(f"session-{k}", "2026-08-01T09:59:00Z") for k in range(session_count)])
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert survivors == []
        assert len(live_lists) == session_count
        assert _round_counts_per_loop(assembled_loops) == [1] * session_count


# --- scope -------------------------------------------------------------------


def _add_linked_worktree(world: _World, branch: str) -> Path:
    worktree = add_linked_worktree(world.repo, world.repo / ".claude" / "worktrees" / branch, branch=branch)
    return worktree.resolve()


class TestScope:
    def test_an_orphaned_worktree_directory_is_read_and_a_sibling_fork_directory_is_not(self, world, capsys):
        orphan_slug = world.main_slug + _WORKTREE_INFIX + "removed-worktree"
        _write_session(world, _clean_loop("branch-orphan", tag="o"), slug=orphan_slug, session_id="sess-o", log=_KEYS_OFF)
        _write_session(
            world, _clean_loop("branch-fork", tag="f"), slug=world.main_slug + "-fork", session_id="sess-f", log=_KEYS_OFF,
        )
        _write_note(world, _note_line(world.config_dir, "branch-orphan"), _note_line(world.config_dir, "branch-fork"))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: captured" in outcome.out
        assert "note line 2: missing (no-records)" in outcome.out

    def test_a_cwd_outside_any_repo_gets_scopes_own_exit_1(self, world, capsys, monkeypatch, tmp_path):
        nowhere = tmp_path / "nowhere"
        nowhere.mkdir()
        monkeypatch.chdir(nowhere)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 1
        assert _published_files(world) == []

    def test_a_run_from_another_repos_worktree_exits_1_reads_no_session_and_names_neither_repo(
        self, world, capsys, monkeypatch, tmp_path,
    ):
        other_checkout = tmp_path / "other-checkout"
        _init_repo(other_checkout)
        _commit(other_checkout, "init")
        monkeypatch.setattr(routing_guard, "_CHECKOUT_ROOT", other_checkout.resolve())
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world)

        assert outcome.code == 1
        assert not any(path.endswith(".jsonl") for path in spies.opened_paths())
        assert _published_files(world) == []
        combined = outcome.out + outcome.err
        for identifying_text in (str(world.repo), str(other_checkout), world.main_slug, "other-checkout"):
            assert identifying_text not in combined

    def test_a_run_from_a_linked_worktree_of_the_checkout_passes_the_check(self, world, capsys, monkeypatch):
        worktree = _add_linked_worktree(world, "linked-worktree-branch")
        monkeypatch.chdir(worktree)
        worktree_slug = scope._path_to_project_slug(str(worktree))
        _write_session(world, _clean_loop(), slug=worktree_slug, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]

    def test_a_scan_gap_refuses_the_run_before_anything_is_published(self, world, capsys, monkeypatch):
        def sessions_with_a_gap(slugs, include_subagents, roots=None, *, scan_gaps=None):
            scan_gaps[scope._SCAN_GAP_SESSION_FILE] += 1
            yield world.projects / world.main_slug / "s.jsonl", _clean_loop()

        monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions_with_a_gap)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "Nothing was published" in outcome.err
        assert _published_files(world) == []
        assert str(world.projects) not in outcome.err


class TestGitCheckFailure:
    def test_a_failed_git_call_prints_the_diagnostic_under_the_routing_guard_label_before_the_note_refusal(
        self, world, capsys, monkeypatch, tmp_path,
    ):
        _install_git_not_found(monkeypatch)
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        _assert_git_failure_diagnostic_precedes_note_refusal(outcome, _DIGIT_SCAN_EXEMPT_PREFIXES, tmp_path)
        assert _published_files(world) == []


# --- the store ---------------------------------------------------------------


_BETA = "feat-beta"


def _two_branch_sessions(world: _World) -> None:
    _write_session(world, _clean_loop(_BRANCH, tag="a"), session_id="sess-a", log=_KEYS_OFF)
    _write_session(world, _clean_loop(_BETA, tag="b"), session_id="sess-b", log=_KEYS_OFF)


def _plant_standing_file(world: _World, branch: str, text: str) -> Path:
    world.captures_dir.mkdir(mode=0o700, exist_ok=True)
    path = world.captures_dir / _digest_name(branch)
    path.write_text(text)
    return path


class TestConfigDirSpelling:
    @pytest.mark.parametrize("spelling", ["symlink-to-the-account", "trailing-slash"])
    def test_a_config_dir_spelled_unlike_the_note_publishes_into_the_real_captures_directory(
        self, world, capsys, tmp_path, spelling,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        if spelling == "symlink-to-the-account":
            alias = tmp_path / "acct-alias"
            alias.symlink_to(world.config_dir, target_is_directory=True)
            config_dir_argument = str(alias)
        else:
            config_dir_argument = f"{world.config_dir}/"

        outcome = _run(capsys, world, config_dir=config_dir_argument)

        assert outcome.code == 0, outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]

    def test_a_note_that_is_a_symlink_into_a_git_work_tree_is_refused_without_naming_a_path(self, world, capsys):
        _write_session(world, _clean_loop())
        note_in_repo = world.repo / "note.tsv"
        note_in_repo.write_text(_note_line(world.config_dir, _BRANCH) + "\n")
        world.note.symlink_to(note_in_repo)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "git working tree" in outcome.err
        assert str(world.repo) not in outcome.err
        assert _published_files(world) == []


class TestStore:
    @pytest.mark.parametrize(
        ("published_count", "expected_text"),
        [
            (0, "this run published 0 files"),
            (1, "this run published 1 file, and a re-run reports it already captured"),
            (2, "this run published 2 files, and a re-run reports them already captured"),
        ],
    )
    def test_the_published_files_text_agrees_noun_and_pronoun_with_the_count(self, published_count, expected_text):
        assert routing_guard._published_files_text(published_count) == expected_text

    def test_a_second_run_reports_each_row_already_captured_and_leaves_every_file_untouched(self, world, capsys):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        first = _run(capsys, world)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in _published_files(world)}

        second = _run(capsys, world)

        assert first.code == second.code == 0
        assert sum(1 for line in second.out.splitlines() if ": already captured (" in line) == 2
        assert not any(": captured (" in line for line in second.out.splitlines())
        assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in _published_files(world)} == before

    def test_a_valid_standing_file_for_a_branch_with_no_transcripts_is_reported_and_never_rebuilt(
        self, world, capsys, monkeypatch,
    ):
        standing = _plant_standing_file(world, _BRANCH, _valid_capture_text())
        before = (standing.read_bytes(), standing.stat().st_mtime_ns)
        build_calls: list[int] = []
        real_build_row = routing_guard._build_row
        monkeypatch.setattr(
            routing_guard, "_build_row",
            lambda rounds, key_states: build_calls.append(1) or real_build_row(rounds, key_states),
        )
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert "already captured" in outcome.out
        assert (standing.read_bytes(), standing.stat().st_mtime_ns) == before
        assert build_calls == []

    @pytest.mark.parametrize("revision", [0, 2])
    def test_a_standing_file_at_another_revision_is_neither_opened_nor_reported(self, world, capsys, monkeypatch, revision):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        world.captures_dir.mkdir(mode=0o700)
        other_revision = world.captures_dir / _digest_name(_BRANCH, revision)
        other_revision.write_text("not even valid")
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert str(other_revision) not in spies.all_paths()
        assert other_revision.read_text() == "not even valid"
        assert (world.captures_dir / _digest_name(_BRANCH)).exists()
        assert ": invalid" not in outcome.out

    def test_a_run_with_one_whole_and_one_missing_row_publishes_the_whole_one_prints_both_and_exits_3(self, world, capsys):
        _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, "no-transcripts-branch"))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: captured" in outcome.out
        assert "note line 2: missing (no-records)" in outcome.out
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]

    @pytest.mark.parametrize("fault", ["fails-validator", "unreadable", "live-symlink", "dangling-symlink"])
    def test_a_bad_standing_file_reports_invalid_and_exits_3_beside_a_published_second_row(
        self, world, capsys, monkeypatch, tmp_path, fault,
    ):
        _write_session(world, _clean_loop(_BETA, tag="b"), log=_KEYS_OFF)
        world.captures_dir.mkdir(mode=0o700)
        standing = world.captures_dir / _digest_name(_BRANCH)
        if fault == "fails-validator":
            standing.write_text(_file_bytes([]).decode())
        elif fault == "unreadable":
            standing.write_text(_valid_capture_text())
            real_os_open = os.open

            def refusing_open(path, *args, **kwargs):
                if os.fsdecode(path) == str(standing):
                    raise PermissionError(errno.EACCES, "sentinel-denied")
                return real_os_open(path, *args, **kwargs)

            monkeypatch.setattr(os, "open", refusing_open)
        else:
            target = tmp_path / "symlink-target.json"
            if fault == "live-symlink":
                target.write_text(_valid_capture_text())
            standing.symlink_to(target)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: invalid" in outcome.out
        assert "note line 2: captured" in outcome.out
        assert (world.captures_dir / _digest_name(_BETA)).exists()
        assert standing.is_symlink() == (fault in ("live-symlink", "dangling-symlink"))
        if fault == "live-symlink":
            assert target.read_text() == _valid_capture_text()
        if fault == "dangling-symlink":
            assert not target.exists()

    def test_a_loops_only_file_at_the_current_revision_reads_invalid_with_the_loops_only_note_and_is_not_rebuilt(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(_BRANCH))
        loops_only_text = json.dumps({"loops": [_DEFAULT_LOOP]})
        standing = _plant_standing_file(world, _BRANCH, loops_only_text)
        before = (standing.read_bytes(), standing.stat().st_mtime_ns)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert f"routing-guard: note line 1: invalid ({routing_guard._LOOPS_ONLY_NOTE})" in outcome.out.splitlines()
        assert "the file holds loops and no key states" in outcome.out
        assert (standing.read_bytes(), standing.stat().st_mtime_ns) == before

    def test_a_planted_orphaned_temp_file_is_never_opened_and_stays(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        world.captures_dir.mkdir(mode=0o700)
        orphan = world.captures_dir / ".routing-guard-planted.tmp"
        orphan.write_text("left by a crashed run")
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert str(orphan) not in spies.all_paths()
        assert orphan.read_text() == "left by a crashed run"

    def test_a_concurrent_winner_of_the_name_is_read_and_reported_already_captured(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        winner_text = _valid_capture_text(growth_tokens=None, degraded=True)
        winner_path = world.captures_dir / _digest_name(_BRANCH)

        def link_losing_the_race(source, destination, *args, **kwargs):
            Path(destination).write_text(winner_text)
            raise FileExistsError(errno.EEXIST, "File exists")

        monkeypatch.setattr(os, "link", link_losing_the_race)

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert (
            "already captured (1 loops, 0 with growth defined, degraded: yes,"
            " states: routing=off/consult-pilot=off/fix-plan-pilot=off)"
        ) in outcome.out
        assert winner_path.read_text() == winner_text
        assert list(world.captures_dir.glob(".routing-guard-*")) == []

    def test_a_winner_of_the_name_that_vanishes_before_the_read_is_reported_invalid_and_leaves_no_temp_file(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def link_losing_the_race_to_a_file_that_is_gone(source, destination, *args, **kwargs):
            raise FileExistsError(errno.EEXIST, "File exists")

        monkeypatch.setattr(os, "link", link_losing_the_race_to_a_file_that_is_gone)

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "routing-guard: note line 1: invalid" in outcome.out.splitlines()
        assert "Traceback" not in outcome.out + outcome.err
        assert _published_files(world) == []
        assert list(world.captures_dir.glob(".routing-guard-*")) == []

    def test_a_symlinked_captures_directory_inside_a_git_work_tree_is_refused_before_any_transcript_is_read(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop())
        inside_repo = world.repo / "captures-inside-the-repo"
        inside_repo.mkdir()
        world.captures_dir.symlink_to(inside_repo, target_is_directory=True)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "git working tree" in outcome.err
        assert not any(path.endswith(".jsonl") for path in spies.opened_paths())
        assert list(inside_repo.iterdir()) == []

    @pytest.mark.parametrize(
        ("swapped_in", "message_fragment"), [("git-work-tree", "git working tree"), ("outside-link", "symlink")],
    )
    def test_a_captures_directory_swapped_for_a_symlink_during_the_scan_is_refused_before_the_first_publish(
        self, world, capsys, monkeypatch, tmp_path, swapped_in, message_fragment,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        link_target = world.repo / "captures-swapped-in" if swapped_in == "git-work-tree" else tmp_path / "captures-elsewhere"
        link_target.mkdir()
        real_iter_scoped_sessions = scope._iter_scoped_sessions

        def sessions_then_swap_the_captures_directory(*args, **kwargs):
            yield from real_iter_scoped_sessions(*args, **kwargs)
            world.captures_dir.symlink_to(link_target, target_is_directory=True)

        monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions_then_swap_the_captures_directory)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert message_fragment in outcome.err
        assert list(link_target.iterdir()) == []
        assert str(link_target) not in outcome.err + outcome.out

    def test_an_opt_in_revoked_during_the_scan_is_refused_before_the_first_publish(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        real_iter_scoped_sessions = scope._iter_scoped_sessions

        def sessions_then_revoke_the_opt_in(*args, **kwargs):
            yield from real_iter_scoped_sessions(*args, **kwargs)
            (world.config_dir / ".pr-cost-enabled").unlink()

        monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions_then_revoke_the_opt_in)

        outcome = _run(capsys, world)

        assert outcome.code == 1
        assert "not opted in" in outcome.err
        assert _published_files(world) == []
        assert not world.captures_dir.exists()

    def test_an_opt_in_that_stays_on_through_the_scan_publishes(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        real_iter_scoped_sessions = scope._iter_scoped_sessions

        def sessions_then_touch_an_unrelated_file(*args, **kwargs):
            yield from real_iter_scoped_sessions(*args, **kwargs)
            (world.config_dir / "unrelated-file").write_text("")

        monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions_then_touch_an_unrelated_file)

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]

    def test_a_config_dir_inside_a_git_work_tree_is_refused_before_any_transcript_is_read(self, world, capsys, monkeypatch):
        account_in_repo = world.repo / "account-inside-the-repo"
        (account_in_repo / "projects").mkdir(parents=True)
        _enable_pr_cost(account_in_repo)
        _write_note(world, _note_line(account_in_repo, _BRANCH))
        spies = _install_path_spies(monkeypatch)

        outcome = _run(capsys, world, config_dir=str(account_in_repo))

        assert outcome.code == 2
        assert "git working tree" in outcome.err
        assert not any(path.endswith(".jsonl") for path in spies.opened_paths())
        assert not (account_in_repo / "routing-guard-captures").exists()

    def test_a_failed_write_after_one_published_file_exits_4_with_the_write_message_and_the_published_count(
        self, world, capsys, monkeypatch,
    ):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        real_fsync = os.fsync
        fsync_calls: list[int] = []

        def fsync_failing_on_the_second_publish(fd):
            fsync_calls.append(fd)
            if len(fsync_calls) == 2:
                raise OSError(errno.EIO, "sentinel-strerror", "/sentinel/path")
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fsync_failing_on_the_second_publish)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "could not be written" in outcome.err
        assert "hard links" not in outcome.err
        assert "published 1 file, and a re-run reports it already captured" in outcome.err
        assert "/sentinel/path" not in outcome.err + outcome.out
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]
        assert list(world.captures_dir.glob(".routing-guard-*")) == []

    @pytest.mark.parametrize("link_errno", [errno.EXDEV, errno.EPERM], ids=["cross-device", "link-not-permitted"])
    def test_a_failed_link_after_one_published_file_exits_4_with_the_link_message_and_the_published_count(
        self, world, capsys, monkeypatch, link_errno,
    ):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        real_link = os.link
        link_calls: list[int] = []

        def link_failing_on_the_second_publish(source, destination, *args, **kwargs):
            link_calls.append(1)
            if len(link_calls) == 2:
                raise OSError(link_errno, "sentinel-strerror", "/sentinel/path")
            return real_link(source, destination, *args, **kwargs)

        monkeypatch.setattr(os, "link", link_failing_on_the_second_publish)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "could not be linked into place" in outcome.err
        assert "(the captures directory's filesystem must support hard links and have free space)" in outcome.err
        assert "published 1 file, and a re-run reports it already captured" in outcome.err
        assert "/sentinel/path" not in outcome.err + outcome.out
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]
        assert list(world.captures_dir.glob(".routing-guard-*")) == []

    def test_an_unrecognized_publish_outcome_exits_4_with_a_message_that_claims_neither_a_write_nor_a_link_failure(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        monkeypatch.setattr(ledger_common, "_publish_new_file", lambda *args: object())

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "a capture file could not be published; this run published 0 files" in outcome.err
        # A run that published nothing has no already-captured files to promise.
        assert "re-run" not in outcome.err
        assert "written" not in outcome.err
        assert "linked into place" not in outcome.err
        assert "hard links" not in outcome.err
        assert _published_files(world) == []

    def test_a_directory_creation_failure_exits_4_printing_only_the_strerror(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def mkdir_refused(path, *args, **kwargs):
            raise OSError(errno.EACCES, "sentinel-strerror", "/sentinel/path")

        monkeypatch.setattr(os, "mkdir", mkdir_refused)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "sentinel-strerror" in outcome.err
        assert "/sentinel/path" not in outcome.err + outcome.out

    def test_a_built_row_the_validator_rejects_exits_4_naming_zero_published_files_and_creating_no_directory(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        monkeypatch.setattr(
            routing_guard, "_build_row",
            lambda rounds, key_states: routing_guard._BuiltRow("{}", [], routing_guard._KeyStates("off", "off", "off")),
        )

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "failed validation; this run published 0 files" in outcome.err
        # A run that published nothing has no already-captured files to promise.
        assert "re-run" not in outcome.err
        assert not world.captures_dir.exists()

    def test_a_built_row_the_validator_rejects_after_one_published_row_reports_that_one_file(
        self, world, capsys, monkeypatch,
    ):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        real_build_row = routing_guard._build_row
        build_calls: list[int] = []

        def build_row_failing_validation_on_the_second_row(rounds, key_states):
            build_calls.append(1)
            if len(build_calls) == 1:
                return real_build_row(rounds, key_states)
            return routing_guard._BuiltRow("{}", [], routing_guard._KeyStates("off", "off", "off"))

        monkeypatch.setattr(routing_guard, "_build_row", build_row_failing_validation_on_the_second_row)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "failed validation; this run published 1 file, and a re-run reports it already captured" in outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]

    def test_an_unexpected_exception_exits_4_with_the_fixed_message_and_no_sentinel_or_traceback(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def explode(_rounds, _key_states):
            raise RuntimeError("sentinel-exception-message")

        monkeypatch.setattr(routing_guard, "_build_row", explode)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert outcome.err.splitlines()[-1] == f"{routing_guard._UNEXPECTED_ERROR_MESSAGE} (RuntimeError in explode)"
        assert "sentinel-exception-message" not in outcome.err + outcome.out
        assert "Traceback" not in outcome.err

    def test_the_innermost_frame_name_is_the_deepest_function_and_unknown_without_a_traceback(self):
        def inner_function():
            raise RuntimeError("sentinel-exception-message")

        def outer_function():
            inner_function()

        with pytest.raises(RuntimeError) as raised:
            outer_function()

        assert routing_guard._innermost_frame_name(raised.value) == "inner_function"
        assert routing_guard._innermost_frame_name(RuntimeError("never raised")) == "unknown"

    def test_a_run_that_publishes_nothing_creates_no_captures_directory(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "branch-with-no-transcripts"))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert not world.captures_dir.exists()


# --- the validator -----------------------------------------------------------


_DEFAULT_LOOP = _capture_loop()


def _loop_dict(**overrides) -> dict:
    return {**_DEFAULT_LOOP, **overrides}


def _file_bytes(loops: object, **state_overrides) -> bytes:
    return _capture_file_text(loops, **state_overrides).encode("utf-8")


def _loop_json(**overrides) -> bytes:
    return _file_bytes([_loop_dict(**overrides)])


_REASON_NOT_VALID_JSON = "not valid JSON"
_REASON_NOT_AN_OBJECT = "the file is not a JSON object"
_REASON_LOOPS_ONLY = "the file holds loops and no key states"
_REASON_WRONG_KEYS = "the file does not hold exactly the loops and the three key states"
_REASON_KEY_STATE_VALUE = "a key state is neither on nor off"
_REASON_LOOPS_EMPTY = "loops is not a non-empty list"
_REASON_LOOP_KEYS = "a loop does not hold exactly the four loop keys"
_REASON_GROWTH = "growth_tokens is neither an integer nor null"
_REASON_TOKEN_COUNT = "a token count is not a non-negative integer"
_REASON_LATER_TURNS = "later_turns is not an object of non-negative integers"
_INVALID_CAPTURES = [
    ("not-utf8", b"\xff\xfe", "not UTF-8"),
    ("invalid-json", b"{", _REASON_NOT_VALID_JSON),
    ("non-object-root", b'["loops"]', _REASON_NOT_AN_OBJECT),
    ("empty-loops", _file_bytes([]), _REASON_LOOPS_EMPTY),
    ("loops-not-a-list", _file_bytes({"k": 1}), _REASON_LOOPS_EMPTY),
    ("loop-not-an-object", _file_bytes([1]), _REASON_LOOP_KEYS),
    (
        "missing-loop-key",
        _file_bytes([{"overhead_tokens": {}, "growth_tokens": 1, "later_turns": {}}]), _REASON_LOOP_KEYS,
    ),
    ("extra-loop-key", _loop_json(extra=1), _REASON_LOOP_KEYS),
    ("boolean-growth", _loop_json(growth_tokens=True), _REASON_GROWTH),
    ("float-growth", _loop_json(growth_tokens=1.5), _REASON_GROWTH),
    ("string-growth", _loop_json(growth_tokens="1"), _REASON_GROWTH),
    ("boolean-token-count", _loop_json(overhead_tokens={_SONNET: {"standard": {"input": True}}}), _REASON_TOKEN_COUNT),
    ("float-token-count", _loop_json(overhead_tokens={_SONNET: {"standard": {"input": 2.0}}}), _REASON_TOKEN_COUNT),
    ("negative-token-count", _loop_json(overhead_tokens={_SONNET: {"standard": {"input": -1}}}), _REASON_TOKEN_COUNT),
    ("negative-turn-count", _loop_json(later_turns={_SONNET: -1}), _REASON_LATER_TURNS),
    ("float-turn-count", _loop_json(later_turns={_SONNET: 1.0}), _REASON_LATER_TURNS),
    ("later-turns-a-list", _loop_json(later_turns=[]), _REASON_LATER_TURNS),
    ("later-turns-null", _loop_json(later_turns=None), _REASON_LATER_TURNS),
    ("non-boolean-degraded", _loop_json(degraded=1), "degraded is not a boolean"),
    (
        "unknown-variant", _loop_json(overhead_tokens={_SONNET: {"sentinel-variant": {"input": 1}}}),
        "unknown pricing variant",
    ),
    (
        "unknown-class", _loop_json(overhead_tokens={_SONNET: {"standard": {"sentinel-class": 1}}}),
        "unknown token class",
    ),
    ("model-entry-not-an-object", _loop_json(overhead_tokens={_SONNET: 1}), "a model entry is not an object"),
    (
        "variant-entry-not-an-object", _loop_json(overhead_tokens={_SONNET: {"standard": 5}}),
        "a variant entry is not an object",
    ),
    ("overhead-not-an-object", _loop_json(overhead_tokens=[]), "overhead_tokens is not an object"),
    (
        "integer-past-the-digit-limit",
        b'{"loops": [{"overhead_tokens": {}, "growth_tokens": ' + b"9" * 5000 + b', "later_turns": {}, "degraded": false}],'
        b' "routing": "off", "round_consult_round2_pilot": "off", "architect_fix_plan_pilot": "off"}',
        _REASON_NOT_VALID_JSON,
    ),
    ("nesting-past-the-recursion-limit", b"[" * 500_000, _REASON_NOT_VALID_JSON),
    ("loops-only", json.dumps({"loops": [_DEFAULT_LOOP]}).encode(), _REASON_LOOPS_ONLY),
    *[
        (
            f"{missing_key}-missing",
            json.dumps({
                "loops": [_DEFAULT_LOOP], **{key: value for key, value in _VALID_STATES.items() if key != missing_key},
            }).encode(),
            _REASON_WRONG_KEYS,
        )
        for missing_key in _VALID_STATES
    ],
    *[
        (
            f"an-extra-key-beside-{state_key}",
            json.dumps({"loops": [_DEFAULT_LOOP], **_VALID_STATES, f"{state_key}-extra": "off"}).encode(),
            _REASON_WRONG_KEYS,
        )
        for state_key in _VALID_STATES
    ],
    *[
        (f"{key}-holding-{label}", _file_bytes([_DEFAULT_LOOP], **{key: value}), _REASON_KEY_STATE_VALUE)
        for key in _VALID_STATES
        for label, value in [
            ("true", True), ("false", False), ("null", None), ("a-number", 1), ("an-empty-string", ""),
            ("a-capitalised-on", "On"), ("mixed", "mixed"), ("unknown", "unknown"),
        ]
    ],
]


def _with_valid_states(data: bytes) -> bytes:
    return json.dumps({**json.loads(data), **_VALID_STATES}).encode()


# Each case puts a valid element first and the invalid one second at one nesting level.
_SECOND_ELEMENT_CASES_WITHOUT_STATES = [
    (
        "second-loop-with-a-boolean-growth",
        _file_bytes([_loop_dict(), _loop_dict(growth_tokens=True)]), _REASON_GROWTH,
    ),
    (
        "second-model-with-a-negative-token-count",
        json.dumps({"loops": [_loop_dict(
            overhead_tokens={_SONNET: {"standard": {"input": 5}}, _OTHER_MODEL: {"standard": {"input": -1}}},
        )]}).encode(),
        _REASON_TOKEN_COUNT,
    ),
    (
        "second-model-not-an-object",
        json.dumps({"loops": [_loop_dict(overhead_tokens={_SONNET: {"standard": {"input": 5}}, _OTHER_MODEL: 1})]}).encode(),
        "a model entry is not an object",
    ),
    (
        "second-variant-unknown",
        json.dumps({"loops": [_loop_dict(
            overhead_tokens={_SONNET: {"standard": {"input": 5}, "sentinel-variant": {"input": 1}}},
        )]}).encode(),
        "unknown pricing variant",
    ),
    (
        "second-variant-not-an-object",
        json.dumps({"loops": [_loop_dict(
            overhead_tokens={_SONNET: {"standard": {"input": 5}, "fast": 5}},
        )]}).encode(),
        "a variant entry is not an object",
    ),
    (
        "second-token-class-unknown",
        json.dumps({"loops": [_loop_dict(
            overhead_tokens={_SONNET: {"standard": {"input": 5, "sentinel-class": 1}}},
        )]}).encode(),
        "unknown token class",
    ),
    (
        "second-token-class-with-a-float-count",
        json.dumps({"loops": [_loop_dict(
            overhead_tokens={_SONNET: {"standard": {"input": 5, "output": 2.0}}},
        )]}).encode(),
        _REASON_TOKEN_COUNT,
    ),
    (
        "second-later-turns-entry-negative",
        json.dumps({"loops": [_loop_dict(later_turns={_SONNET: 1, _OTHER_MODEL: -1})]}).encode(), _REASON_LATER_TURNS,
    ),
    (
        "second-later-turns-entry-boolean",
        json.dumps({"loops": [_loop_dict(later_turns={_SONNET: 1, _OTHER_MODEL: True})]}).encode(), _REASON_LATER_TURNS,
    ),
]


_SECOND_ELEMENT_INVALID_CAPTURES = [
    (case_id, _with_valid_states(data), reason) for case_id, data, reason in _SECOND_ELEMENT_CASES_WITHOUT_STATES
]

_VALID_LOOP_TEXT = json.dumps(_DEFAULT_LOOP)
_VALID_STATES_TEXT = json.dumps(_VALID_STATES)[1:-1]
_VALID_FILE_TEXT = _capture_file_text([_DEFAULT_LOOP])
# Each case differs from the valid file in one place, so only the guard it names can refuse it.
_GUARD_CASES = [
    (
        "duplicate-loops-key-holding-two-valid-lists",
        f'{{"loops": [{_VALID_LOOP_TEXT}], "loops": [{_VALID_LOOP_TEXT}], {_VALID_STATES_TEXT}}}', _REASON_NOT_VALID_JSON,
    ),
    (
        "duplicate-key-inside-one-loop",
        _VALID_FILE_TEXT.replace('"growth_tokens": 1', '"growth_tokens": 1, "growth_tokens": 2'), _REASON_NOT_VALID_JSON,
    ),
    ("nan-growth", _VALID_FILE_TEXT.replace('"growth_tokens": 1', '"growth_tokens": NaN'), _REASON_NOT_VALID_JSON),
    (
        "infinity-growth",
        _VALID_FILE_TEXT.replace('"growth_tokens": 1', '"growth_tokens": Infinity'), _REASON_NOT_VALID_JSON,
    ),
    (
        "negative-infinity-growth",
        _VALID_FILE_TEXT.replace('"growth_tokens": 1', '"growth_tokens": -Infinity'), _REASON_NOT_VALID_JSON,
    ),
    ("nan-token-count", _VALID_FILE_TEXT.replace('"input": 5', '"input": NaN'), _REASON_NOT_VALID_JSON),
    ("infinity-token-count", _VALID_FILE_TEXT.replace('"input": 5', '"input": Infinity'), _REASON_NOT_VALID_JSON),
    (
        "extra-root-key-beside-valid-loops",
        _VALID_FILE_TEXT.replace('{"loops"', '{"extra": 1, "loops"'), _REASON_WRONG_KEYS,
    ),
]


class TestValidator:
    def test_the_valid_file_the_guard_cases_are_built_from_is_accepted(self):
        record = routing_guard._parse_capture_bytes(_VALID_FILE_TEXT.encode("utf-8"))

        assert [loop["growth_tokens"] for loop in record.loops] == [1]
        assert record.states == routing_guard._KeyStates("off", "off", "off")

    def test_the_valid_capture_text_helper_is_accepted(self):
        record = routing_guard._parse_capture_bytes(_valid_capture_text().encode("utf-8"))

        assert record.states == routing_guard._KeyStates("off", "off", "off")

    @pytest.mark.parametrize(("case_id", "text", "reason"), _GUARD_CASES, ids=[case[0] for case in _GUARD_CASES])
    def test_an_otherwise_valid_file_is_refused_for_the_one_guard_it_trips(self, case_id, text, reason):
        assert text != _VALID_FILE_TEXT

        with pytest.raises(routing_guard._InvalidCapture) as refusal:
            routing_guard._parse_capture_bytes(text.encode("utf-8"))

        assert refusal.value.reason == reason

    @pytest.mark.parametrize(
        ("case_id", "data", "reason"), _INVALID_CAPTURES, ids=[case[0] for case in _INVALID_CAPTURES],
    )
    def test_refuses_each_malformed_file_with_the_fixed_reason_of_the_guard_it_trips(self, case_id, data, reason):
        with pytest.raises(routing_guard._InvalidCapture) as refusal:
            routing_guard._parse_capture_bytes(data)

        assert refusal.value.reason == reason

    @pytest.mark.parametrize(
        ("case_id", "data", "reason"), _SECOND_ELEMENT_INVALID_CAPTURES,
        ids=[case[0] for case in _SECOND_ELEMENT_INVALID_CAPTURES],
    )
    def test_refuses_a_file_whose_second_element_at_a_nesting_level_is_invalid_with_the_single_element_reason(
        self, case_id, data, reason,
    ):
        with pytest.raises(routing_guard._InvalidCapture) as refusal:
            routing_guard._parse_capture_bytes(data)

        assert refusal.value.reason == reason

    def test_accepts_a_hand_written_literal_file_independent_of_the_writer(self):
        literal = (
            b'{"loops": [{"overhead_tokens": {"claude-sonnet-5": {"fast": {"input": 5, "cache_write_1h": 2}}},'
            b' "growth_tokens": -40, "later_turns": {"claude-sonnet-5": 0, "": 3}, "degraded": true},'
            b' {"overhead_tokens": {}, "growth_tokens": null, "later_turns": {}, "degraded": false}],'
            b' "routing": "on", "round_consult_round2_pilot": "off", "architect_fix_plan_pilot": "on"}'
        )

        record = routing_guard._parse_capture_bytes(literal)

        assert [loop["growth_tokens"] for loop in record.loops] == [-40, None]
        assert record.loops[0]["overhead_tokens"]["claude-sonnet-5"]["fast"]["cache_write_1h"] == 2
        assert record.states == routing_guard._KeyStates(
            routing="on", round_consult_round2_pilot="off", architect_fix_plan_pilot="on",
        )

    def test_a_row_built_by_the_writer_passes_the_validator(self):
        round_facts = _round_facts(
            has_marker_write=True, overhead_tokens={_SONNET: {"fast_us_geo": {"input": 3, "cache_write_1h": 4}}},
            later_turns={_SONNET: 2},
        )

        built = routing_guard._build_row([round_facts], _branch_states())

        assert routing_guard._parse_capture_bytes(built.text.encode("utf-8")) == routing_guard._CaptureRecord(
            [{
                "overhead_tokens": {_SONNET: {"fast_us_geo": {"input": 3, "cache_write_1h": 4}}},
                "growth_tokens": 30, "later_turns": {_SONNET: 2}, "degraded": False,
            }],
            routing_guard._KeyStates("off", "off", "off"),
        )

    @pytest.mark.parametrize("flipped_key", routing_guard._STATE_KEYS)
    def test_each_key_state_round_trips_to_its_own_field_so_a_transposition_fails(self, flipped_key):
        by_key = {key: "on" if key == flipped_key else "off" for key in routing_guard._STATE_KEYS}
        round_facts = _round_facts(has_marker_write=True)

        built = routing_guard._build_row([round_facts], routing_guard._BranchKeyStates(by_key, ()))
        record = routing_guard._parse_capture_bytes(built.text.encode("utf-8"))

        assert dict(zip(routing_guard._STATE_KEYS, record.states, strict=True)) == by_key


# --- the per-row lines -------------------------------------------------------


class TestRowLines:
    def test_the_captured_line_of_a_key_on_row_with_a_null_growth_loop_equals_a_literal(self, world, capsys):
        project_dir = _write_session(world, _key_on_loop(), log=_ROUTING_ON)
        _write_marker_writing_orchestrator(project_dir)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert (
            "routing-guard: note line 1: captured (2 rounds, 1 loops, 0 with growth defined,"
            " 1 denied round-opens joined, degraded: no,"
            " states: routing=on/consult-pilot=off/fix-plan-pilot=off, earliest record 2026-08-01)"
        ) in outcome.out.splitlines()

    def test_a_dangling_dispatch_reads_degraded_yes_on_the_captured_line(self, world, capsys):
        records = [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(0),
                    content=[_skill_block("sk-a", "code-review"), _agent_use("no-such-dispatch", "staff-sdet")]),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
            _marker_write_result("mk-a", minute=1),
        ]
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert (
            "routing-guard: note line 1: captured (1 rounds, 1 loops, 0 with growth defined,"
            " 0 denied round-opens joined, degraded: yes,"
            " states: routing=off/consult-pilot=off/fix-plan-pilot=off, earliest record 2026-08-01)"
        ) in outcome.out.splitlines()
        assert outcome.code == 0, outcome.err
        assert "PRICING INTEGRITY" not in outcome.out
        assert pricing._subagent_format_drift_detected is False

    def test_the_already_captured_line_reads_its_counts_from_the_validated_file(self, world, capsys):
        two_loops = json.dumps({"loops": [
            {"overhead_tokens": {}, "growth_tokens": None, "later_turns": {}, "degraded": False},
            {"overhead_tokens": {}, "growth_tokens": 5, "later_turns": {}, "degraded": True},
        ], "routing": "on", "round_consult_round2_pilot": "off", "architect_fix_plan_pilot": "on"})
        _plant_standing_file(world, _BRANCH, two_loops)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        expected_line = (
            "routing-guard: note line 1: already captured (2 loops, 1 with growth defined, degraded: yes,"
            " states: routing=on/consult-pilot=off/fix-plan-pilot=on)"
        )
        assert expected_line in outcome.out.splitlines()

    def test_the_invalid_and_missing_lines_equal_literals(self, world, capsys):
        _plant_standing_file(world, _BRANCH, "not json")
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, "no-transcripts"))

        outcome = _run(capsys, world)

        assert "routing-guard: note line 1: invalid" in outcome.out.splitlines()
        assert "routing-guard: note line 2: missing (no-records)" in outcome.out.splitlines()

    @pytest.mark.parametrize("drift_detected", [True, False])
    def test_the_pricing_integrity_line_prints_only_when_format_drift_was_detected(
        self, world, capsys, monkeypatch, drift_detected,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        monkeypatch.setattr(pricing, "_format_drift_detected", lambda: drift_detected)

        outcome = _run(capsys, world)

        assert ("PRICING INTEGRITY" in outcome.out) == drift_detected
        assert outcome.code == (3 if drift_detected else 0), outcome.err
        assert (world.captures_dir / _digest_name(_BRANCH)).exists()
        integrity_lines = [line for line in outcome.out.splitlines() if "PRICING INTEGRITY" in line]
        assert not any(ch.isdigit() for line in integrity_lines for ch in line)
        expected_next_action = (
            routing_guard._NEXT_ACTION_RESOLVE_PRICING_DRIFT if drift_detected else routing_guard._NEXT_ACTION_CAPTURE_AGAIN
        )
        assert outcome.out.splitlines()[-1] == expected_next_action
        assert not any(ch.isdigit() for ch in routing_guard._NEXT_ACTION_RESOLVE_PRICING_DRIFT)

    def test_a_missing_row_beside_format_drift_ends_on_the_combined_digit_free_action_and_exits_3(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, "no-transcripts"))
        monkeypatch.setattr(pricing, "_format_drift_detected", lambda: True)

        outcome = _run(capsys, world)

        assert "routing-guard: note line 2: missing (no-records)" in outcome.out.splitlines()
        assert routing_guard._PRICING_INTEGRITY_LINE in outcome.out.splitlines()
        last_line = outcome.out.splitlines()[-1]
        assert last_line == routing_guard._NEXT_ACTION_ACT_ON_LINES_AND_RESOLVE_DRIFT
        assert routing_guard._NEXT_ACTION_ACT_ON_LINES in last_line
        assert routing_guard._PRICING_DRIFT_REMEDY in last_line
        assert "delete the capture file" in last_line
        assert not any(ch.isdigit() for ch in routing_guard._NEXT_ACTION_ACT_ON_LINES_AND_RESOLVE_DRIFT)
        assert outcome.code == 3, outcome.err


# --- the golden file ---------------------------------------------------------


class TestGoldenFile:
    def test_a_capture_with_a_cached_reviewer_dispatch_publishes_the_literal_name_and_text(self, world, capsys):
        records = _clean_loop()
        # The later turn's context is the sum of its four token classes, so the literal growth below fails on a
        # context read from input_tokens alone.
        records[-1] = _priced(
            _SONNET, input=10_000, cache_read=100_000, ephemeral_1h=15_000, ephemeral_5m=5_000, branch=_BRANCH, ts=_ts(3),
        )
        records[0]["message"]["content"].append(_agent_use("rev-1", "staff-sdet"))
        project_dir = _write_session(world, records, log=_KEYS_OFF)
        reviewer_records = [_as_sidechain(_priced(
            _SONNET, input=2_000, output=100, cache_read=40_000, ephemeral_1h=3_000, branch=_BRANCH, ts=_ts(0),
        ))]
        _write_subagent_dispatch(project_dir, "sess-1", "agent-rev", "rev-1", reviewer_records, agent_type="staff-sdet")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)
        published = _published_files(world)

        branch_digest = hashlib.sha256(_BRANCH.encode("utf-8")).hexdigest()
        assert [p.name for p in published] == [f"{branch_digest}.r1.json"], (
            "The file name changed. Bump _CAPTURE_REVISION only when the change alters what a stored row means;"
            " otherwise update this literal, because a bump strands every standing file."
        )
        assert published[0].read_text() == (
            '{"architect_fix_plan_pilot": "off", "loops": [{"degraded": false, "growth_tokens": 30000,'
            ' "later_turns": {"claude-sonnet-5": 1}, "overhead_tokens": {"claude-sonnet-5": {"standard":'
            ' {"cache_read": 40000, "cache_write_1h": 3000, "cache_write_5m": 0, "input": 112000, "output": 600}}}}],'
            ' "round_consult_round2_pilot": "off", "routing": "off"}'
        ), (
            "The stored text changed. Bump _CAPTURE_REVISION only when the change alters what a stored row means;"
            " otherwise update this literal, because a bump strands every standing file."
        )


# --- criterion 3: the reads a capture makes ----------------------------------


class TestCriterionThreeInCaptureMode:
    @staticmethod
    def _is_allowed(path: str, world: _World) -> bool:
        resolved = Path(path)
        if path in (
            str(world.note), str(world.config_dir / "claude-config.toml"), str(_config._SCHEMA_FILE),
            str(world.config_dir / ".session-config-keys-log.jsonl"),
        ):
            return True
        if resolved == world.config_dir / "projects" or (world.config_dir / "projects") in resolved.parents:
            return True
        return resolved.parent == world.captures_dir and (
            resolved.name == _digest_name(_BRANCH) or (resolved.name.startswith(".routing-guard-") and resolved.suffix == ".tmp")
        )

    def test_a_declared_decoy_account_is_never_read_and_every_recorded_path_is_expected(
        self, world, capsys, monkeypatch, tmp_path,
    ):
        decoy = tmp_path / "decoy-account"
        decoy_project = decoy / "projects" / world.main_slug
        decoy_project.mkdir(parents=True)
        # A read decoy would refuse with exit 2: the joined deny sits on a branch the note does not list.
        _write_jsonl(decoy_project / "decoy-session.jsonl", _clean_loop() + [
            _denied_skill_call("decoy-deny", branch="decoy-unlisted", minute=30),
            _routing_deny_record("decoy-deny", branch="decoy-unlisted", ts=_ts(31)),
        ])
        _write_log(decoy, _log_row("decoy-session", routing="true"))
        roots_file = tmp_path / "declared-roots"
        roots_file.write_text(f"{decoy}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        assert decoy in _config_dir.declared_transcript_roots()
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)
        recorder = _install_subprocess_recorder(monkeypatch)

        with _environment_unchanged():
            outcome = _run(capsys, world)

        recorded = spies.all_paths()
        assert outcome.code == 0, outcome.err
        assert [path for path in recorded if not self._is_allowed(path, world)] == []
        assert not any(str(decoy) in path for path in recorded)
        assert str(world.note) in spies.opened_paths()
        assert any(path.endswith("sess-1.jsonl") for path in spies.opened_paths())
        assert str(world.captures_dir) not in spies.listed_paths()
        assert str(world.config_dir / ".session-config-keys-log.jsonl") in spies.opened_paths()
        assert recorder.executables() == {"git"}

    def test_a_note_listing_two_accounts_publishes_nothing_into_the_other_one(self, world, capsys, tmp_path):
        other_account = tmp_path / "acct-b"
        (other_account / "projects").mkdir(parents=True)
        _enable_pr_cost(other_account)
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(other_account, "branch-on-b"))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert not (other_account / "routing-guard-captures").exists()


class TestSubprocessRecorder(_RecorderPositiveControls):
    """Collects the recorder's positive controls, which the capture tests' no-other-executable assertions depend on."""


# --- free text and request ids -----------------------------------------------


# Model strings the rate table does not list. A capture file stores a model string verbatim as a key, so these
# prove the string reaches the file and neither output stream.
_MAIN_THREAD_MODEL_SENTINEL = "claude-main-thread-sentinel-model"
_NESTED_DISPATCH_MODEL_SENTINEL = "claude-nested-dispatch-sentinel-model"


class TestFreeText:
    def test_a_model_string_is_stored_as_a_key_and_reaches_neither_output_stream(self, world, capsys):
        records = _clean_loop(_BRANCH)
        records[0]["message"]["content"].append(_agent_use("rev-1", "staff-sdet"))
        records[1] = _priced(
            _MAIN_THREAD_MODEL_SENTINEL, input=10_000, output=500, branch=_BRANCH, ts=_ts(1),
            content=[_marker_write_use("mk-a")],
        )
        project_dir = _write_session(world, records, log=_KEYS_OFF)
        reviewer_records = [_as_sidechain(_priced(
            _SONNET, input=2_000, branch=_BRANCH, ts=_ts(0), content=[_agent_use("nested-1", "staff-sdet")],
        ))]
        nested_records = [_as_sidechain(_priced(_NESTED_DISPATCH_MODEL_SENTINEL, input=3_000, branch=_BRANCH, ts=_ts(0)))]
        _write_subagent_dispatch(project_dir, "sess-1", "agent-rev", "rev-1", reviewer_records, agent_type="staff-sdet")
        _write_subagent_dispatch(
            project_dir, "sess-1", "agent-nested", "nested-1", nested_records, agent_type="staff-sdet", spawn_depth=2,
        )
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        stored_overhead_models = set(_stored_loops(world, _BRANCH)[0]["overhead_tokens"])
        assert {_MAIN_THREAD_MODEL_SENTINEL, _NESTED_DISPATCH_MODEL_SENTINEL} <= stored_overhead_models
        for sentinel in (_MAIN_THREAD_MODEL_SENTINEL, _NESTED_DISPATCH_MODEL_SENTINEL):
            assert sentinel not in outcome.out
            assert sentinel not in outcome.err

    def test_a_non_ascii_and_a_lone_surrogate_model_string_are_published_as_ascii_and_read_back_unchanged(
        self, world, capsys,
    ):
        non_ascii_model = "claude-mödel-模型"
        lone_surrogate_model = "claude-\ud800-lone-surrogate"
        records = _clean_loop(_BRANCH)
        records[0]["message"]["content"].append(_agent_use("rev-1", "staff-sdet"))
        records[1] = _priced(
            non_ascii_model, input=10_000, output=500, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")],
        )
        project_dir = _write_session(world, records, log=_KEYS_OFF)
        reviewer_records = [_as_sidechain(_priced(
            _SONNET, input=2_000, branch=_BRANCH, ts=_ts(0), content=[_agent_use("nested-1", "staff-sdet")],
        ))]
        nested_records = [_as_sidechain(_priced(lone_surrogate_model, input=3_000, branch=_BRANCH, ts=_ts(0)))]
        _write_subagent_dispatch(project_dir, "sess-1", "agent-rev", "rev-1", reviewer_records, agent_type="staff-sdet")
        _write_subagent_dispatch(
            project_dir, "sess-1", "agent-nested", "nested-1", nested_records, agent_type="staff-sdet", spawn_depth=2,
        )
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        published_bytes = (world.captures_dir / _digest_name(_BRANCH)).read_bytes()
        assert published_bytes.isascii()
        stored_models = set(routing_guard._parse_capture_bytes(published_bytes).loops[0]["overhead_tokens"])
        assert {non_ascii_model, lone_surrogate_model} <= stored_models
        for model in (non_ascii_model, lone_surrogate_model):
            assert model not in outcome.out
            assert model not in outcome.err

    @pytest.mark.parametrize(
        "odd_model", ["claude-mödel-模型", "claude-\ud800-lone-surrogate"], ids=["non-ascii", "lone-surrogate"],
    )
    def test_serializing_a_loop_keyed_by_a_non_ascii_or_lone_surrogate_model_yields_ascii_text(self, odd_model):
        loop = routing_guard._Loop(
            {odd_model: {"standard": {"input": 5}}}, None, {odd_model: 1}, False, 1, 0,
        )
        states = routing_guard._KeyStates("off", "off", "off")

        text = routing_guard._serialize_capture([loop], states)

        assert text.isascii()
        assert json.loads(text)["loops"][0]["overhead_tokens"] == loop.overhead_tokens

    def test_no_free_text_field_reaches_the_file_stdout_or_stderr(self, world, capsys):
        records = [
            _priced(_SONNET, input=40_000, branch=_BRANCH, ts=_ts(0), content=[
                {"type": "text", "text": _leak_sentinel("assistant-text")},
                {"type": "tool_use", "id": "sk-denied", "name": "Skill",
                 "input": {"skill": "code-review", "args": _leak_sentinel("skill-args")}},
            ]),
            _routing_deny_record("sk-denied", branch=_BRANCH, ts=_ts(1), body=_leak_sentinel("deny-body")),
            _orchestrator_spawn_record("o1", ts=_ts(2), branch=_BRANCH),
            _orchestrator_return_record("o1", ts=_ts(3), branch=_BRANCH),
            _user_msg(_leak_sentinel("user-prompt"), branch=_BRANCH, ts=_ts(4)),
        ]
        project_dir = _write_session(world, records, log=_ROUTING_ON)
        orchestrator_records = [
            _as_sidechain(_priced(
                _SONNET, input=5_000, branch=_BRANCH, ts=_ts(2),
                content=[{"type": "text", "text": _leak_sentinel("orchestrator-text")}, _marker_write_use("mk-o")],
            )),
            _as_sidechain(_marker_write_result("mk-o", minute=2, text=_leak_sentinel("marker-write-result"))),
        ]
        _write_subagent_dispatch(
            project_dir, "sess-1", "agent-o", "o1", orchestrator_records, agent_type=_ORCHESTRATOR_AGENT_TYPE,
            description=_leak_sentinel("orchestrator-meta-description"),
        )
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert (world.captures_dir / _digest_name(_BRANCH)).exists()
        published_text = (world.captures_dir / _digest_name(_BRANCH)).read_text()
        for stream in (published_text, outcome.out, outcome.err):
            assert "LEAKSENTINEL" not in stream

    def test_the_body_of_a_dropped_text_derived_deny_and_of_a_legacy_attachment_deny_reaches_no_output(self, world, capsys):
        records = _clean_loop(_BRANCH) + [
            _routing_deny_record("orphan-1", branch=_BRANCH, ts=_ts(31), body=_leak_sentinel("orphan-deny-body")),
            _routing_deny_record(
                "legacy-1", shape="legacy", branch=_BRANCH, ts=_ts(32), body=_leak_sentinel("legacy-deny-body"),
            ),
        ]
        _write_session(world, records, log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert routing_guard._DROPPED_DENY_NOTICE in outcome.err
        published_text = (world.captures_dir / _digest_name(_BRANCH)).read_text()
        for stream in (published_text, outcome.out, outcome.err):
            assert "LEAKSENTINEL" not in stream

    @pytest.mark.parametrize("drift_shape", ["contiguous-usage-disagrees", "non-contiguous-merged", "non-contiguous-rejected"])
    def test_a_request_id_may_show_on_stderr_through_a_dedup_print_and_nowhere_else(
        self, world, capsys, monkeypatch, drift_shape,
    ):
        monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())
        request_id = "req-sentinel-id"
        records = _clean_loop()
        if drift_shape == "contiguous-usage-disagrees":
            records.insert(1, _priced(_SONNET, input=100_001, branch=_BRANCH, ts=_ts(0), request_id=request_id))
            records.insert(1, _priced(_SONNET, input=100_000, branch=_BRANCH, ts=_ts(0), request_id=request_id))
        else:
            second_usage = 7 if drift_shape == "non-contiguous-rejected" else 6
            records.append(_priced(_SONNET, input=6, branch=_BRANCH, ts=_ts(8), request_id=request_id))
            records.append(_user_msg("between", branch=_BRANCH, ts=_ts(9)))
            records.append(_priced(_SONNET, input=second_usage, branch=_BRANCH, ts=_ts(10), request_id=request_id))
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        # Only a contiguous run whose input usage disagrees trips the drift flag; a non-contiguous run never does.
        assert outcome.code == (3 if drift_shape == "contiguous-usage-disagrees" else 0), outcome.err
        assert request_id in outcome.err
        assert request_id not in outcome.out
        assert request_id not in (world.captures_dir / _digest_name(_BRANCH)).read_text()


# --- output ------------------------------------------------------------------


# Capture's own computed values: a locator, the progress count, a row's counts, the published count and a deny's UTC minute.
_PERMITTED_COMPUTED_VALUES = re.compile("|".join([
    r"note line \d+",
    r"scanning project directory \d+ of \d+",
    r"captured \(\d+ rounds, \d+ loops, \d+ with growth defined, \d+ denied round-opens joined, degraded: (?:yes|no),"
    r" states: routing=(?:on|off)/consult-pilot=(?:on|off)/fix-plan-pilot=(?:on|off), earliest record \d{4}-\d{2}-\d{2}\)",
    r"already captured \(\d+ loops, \d+ with growth defined, degraded: (?:yes|no),"
    r" states: routing=(?:on|off)/consult-pilot=(?:on|off)/fix-plan-pilot=(?:on|off)\)",
    r"summary: \d+ captured, \d+ already captured, \d+ missing, \d+ invalid, \d+ excluded;"
    r" key-on files: \d+, key-off files: \d+",
    r"published \d+ files?",
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC",
]))
_CAPTURED_LINE = (
    "routing-guard: note line 3: captured (2 rounds, 1 loops, 0 with growth defined, 1 denied round-opens joined,"
    " degraded: no, states: routing=on/consult-pilot=off/fix-plan-pilot=off, earliest record 2026-08-01)"
)


def _lines_with_values_outside_the_permitted_shapes(outcome: _Outcome) -> list[str]:
    """Every output line holding a digit or `$` once the permitted line shapes are set aside."""
    lines = (outcome.out + outcome.err).splitlines()
    scanned = [line for line in lines if not line.startswith(_DIGIT_SCAN_EXEMPT_PREFIXES)]
    return [line for line in scanned if any(ch.isdigit() or ch == "$" for ch in _PERMITTED_COMPUTED_VALUES.sub("", line))]


_OUTPUT_SCENARIOS = [
    ("captured", 0, "captured ("),
    ("already-captured", 0, "already captured ("),
    ("missing", 3, "missing (no-records)"),
    ("invalid", 3, "note line 1: invalid"),
    ("no-deny-notice", 0, "no routing deny was found for it"),
    ("split-branch-notice", 3, "split across accounts"),
    ("unlisted-deny-refusal", 2, "falls on a branch the note does not list"),
    ("dropped-deny-notice", 0, "was not counted"),
    ("scan-gap", 2, "Nothing was published"),
    ("publish-failure", 4, "published 1 file"),
    ("not-opted-in-refusal", 1, "not opted in"),
    ("unlisted-account-refusal", 2, "is not listed in the note"),
]


class TestOutput:
    def test_the_banner_opens_capture_output(self, world, capsys):
        outcome = _capture_one_branch(world, capsys)

        assert outcome.out.startswith(routing_guard._DO_NOT_PUBLISH_BANNER)
        assert not any(ch.isdigit() for ch in routing_guard._DO_NOT_PUBLISH_BANNER)

    def test_the_banner_states_that_the_key_state_rests_on_rows_any_process_running_as_the_account_can_write(
        self, world, capsys,
    ):
        outcome = _capture_one_branch(world, capsys)

        assert _BANNER_PROVENANCE_SENTENCE in outcome.out.splitlines()
        assert not any(ch.isdigit() for ch in _BANNER_PROVENANCE_SENTENCE)

    def test_the_banner_is_ascii_so_it_encodes_the_same_under_any_locale(self):
        assert routing_guard._DO_NOT_PUBLISH_BANNER.isascii()

    def test_the_permitted_shapes_pass_and_any_other_digit_or_dollar_amount_is_flagged(self):
        permitted = _Outcome(0, _CAPTURED_LINE + "\n", "")
        leaked = _Outcome(0, _CAPTURED_LINE[:-1] + ", 5000 tokens)\n", "routing-guard: cost $1.50\n")

        assert _lines_with_values_outside_the_permitted_shapes(permitted) == []
        assert len(_lines_with_values_outside_the_permitted_shapes(leaked)) == 2

    @pytest.mark.parametrize(
        ("scenario", "expected_code", "expected_fragment"), _OUTPUT_SCENARIOS, ids=[case[0] for case in _OUTPUT_SCENARIOS],
    )
    def test_no_digit_or_dollar_amount_reaches_either_stream_outside_the_permitted_shapes(
        self, world, capsys, monkeypatch, tmp_path, scenario, expected_code, expected_fragment,
    ):
        overrides = {}
        consent_spies = None
        if scenario == "missing":
            _write_note(world, _note_line(world.config_dir, "branch-without-transcripts"))
        elif scenario == "invalid":
            _plant_standing_file(world, _BRANCH, "not json")
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "split-branch-notice":
            other_account = tmp_path / "acct-b"
            _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
            _write_note(world, _note_line(world.config_dir, _BETA), _note_line(other_account, _BRANCH))
        elif scenario == "unlisted-deny-refusal":
            _write_session(world, _clean_loop(_BRANCH) + [
                _denied_skill_call("deny-u", branch="unlisted-work", minute=30),
                _routing_deny_record("deny-u", branch="unlisted-work", ts=_ts(31)),
            ], log=_KEYS_OFF)
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "no-deny-notice":
            _write_session(world, _clean_loop(_BRANCH), log=_ROUTING_ON)
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "dropped-deny-notice":
            _write_session(
                world, _clean_loop(_BRANCH) + [_routing_deny_record("orphan-1", branch=_BRANCH, ts=_ts(31))], log=_KEYS_OFF,
            )
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "scan-gap":
            def sessions_with_a_gap(slugs, include_subagents, roots=None, *, scan_gaps=None):
                scan_gaps[scope._SCAN_GAP_SESSION_FILE] += 1
                yield world.projects / world.main_slug / "s.jsonl", _clean_loop()

            monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions_with_a_gap)
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "publish-failure":
            _two_branch_sessions(world)
            _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
            real_fsync = os.fsync
            fsync_calls: list[int] = []

            def fsync_failing_on_the_second_publish(fd):
                fsync_calls.append(fd)
                if len(fsync_calls) == 2:
                    raise OSError(errno.EIO, "sentinel-strerror")
                return real_fsync(fd)

            monkeypatch.setattr(os, "fsync", fsync_failing_on_the_second_publish)
        elif scenario == "not-opted-in-refusal":
            _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
            (world.config_dir / ".pr-cost-enabled").unlink()
            _write_note(world, _note_line(world.config_dir, _BRANCH))
            consent_spies = _install_path_spies(monkeypatch)
        elif scenario == "unlisted-account-refusal":
            _write_note(world, _note_line(world.config_dir, _BRANCH))
            overrides["config_dir"] = str(tmp_path / "acct-elsewhere")
        elif scenario in ("captured", "already-captured"):
            _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
            _write_note(world, _note_line(world.config_dir, _BRANCH))
            if scenario == "already-captured":
                _run(capsys, world)
        else:
            raise AssertionError(f"unknown output scenario {scenario!r}")

        outcome = _run(capsys, world, **overrides)

        assert outcome.code == expected_code, outcome.err
        assert expected_fragment in outcome.out + outcome.err
        assert _lines_with_values_outside_the_permitted_shapes(outcome) == []
        if consent_spies is not None:
            assert _consent_gated_paths(world, consent_spies) == []

    def test_the_shared_output_check_fails_a_run_that_prints_a_branch_read_from_the_note(self, world, capsys, monkeypatch):
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def printing_the_branch(_args):
            print(f"leaked {_BRANCH}", file=sys.stderr)
            sys.exit(0)

        monkeypatch.setattr(routing_guard, "cmd_routing_guard", printing_the_branch)

        with pytest.raises(AssertionError, match="names 1 string"):
            _run(capsys, world)

    @pytest.mark.parametrize("leak", [
        pytest.param("note-cell", id="a-relative-config-dir-cell-of-the-note"),
        pytest.param("cell-final-segment", id="the-final-segment-of-an-absolute-config-dir-cell"),
        pytest.param("config-dir-argument", id="the-config-dir-argument"),
        pytest.param("argument-final-segment-doubled-slash", id="the-final-segment-of-an-argument-with-a-doubled-slash"),
        pytest.param("argument-final-segment-trailing-slash", id="the-final-segment-of-an-argument-with-a-trailing-slash"),
        pytest.param("expanded-home-path", id="the-expanded-path-of-a-tilde-config-dir-cell"),
        pytest.param("resolved-symlink-path", id="the-resolved-path-of-a-symlinked-config-dir-cell"),
    ])
    def test_the_shared_output_check_fails_a_run_that_prints_each_class_of_string_it_guards(
        self, world, capsys, monkeypatch, tmp_path, leak,
    ):
        overrides = {"config_dir": None}
        if leak == "note-cell":
            note_cell, printed = "relative-account-cell", "relative-account-cell"
        elif leak == "cell-final-segment":
            note_cell, printed = str(tmp_path / "parent" / "account-final-segment"), "account-final-segment"
        elif leak == "config-dir-argument":
            note_cell, printed = str(world.config_dir), "config-dir-argument-only"
            overrides["config_dir"] = printed
        elif leak.startswith("argument-final-segment"):
            note_cell, printed = str(world.config_dir), "argument-account"
            parent = tmp_path.resolve() / "argument-parent"
            overrides["config_dir"] = f"{parent}//{printed}" if leak.endswith("doubled-slash") else f"{parent}/{printed}/"
        elif leak == "expanded-home-path":
            home = tmp_path.resolve() / "home"
            monkeypatch.setenv("HOME", str(home))
            note_cell, printed = "~/account-under-home", str(home / "account-under-home")
        else:
            target = tmp_path.resolve() / "real-account"
            link = tmp_path / "link-account"
            link.symlink_to(target)
            note_cell, printed = str(link), str(target)
        _write_note(world, _note_line(note_cell, _BRANCH))

        def printing_the_string(_args):
            print(f"leaked {printed}", file=sys.stderr)
            sys.exit(0)

        monkeypatch.setattr(routing_guard, "cmd_routing_guard", printing_the_string)

        with pytest.raises(AssertionError, match=r"names \d+ string"):
            _run(capsys, world, **overrides)

    def test_the_strings_a_run_must_not_name_for_an_absolute_cell_are_the_cell_and_its_final_segment(self, tmp_path):
        account = tmp_path.resolve() / "account-abs"
        note = tmp_path / "note.tsv"
        note.write_text(_note_line(account, "branch-abs") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([str(account), "account-abs", "branch-abs"])

    def test_the_strings_a_run_must_not_name_for_a_relative_cell_are_its_whole_text_only(self, tmp_path):
        note = tmp_path / "note.tsv"
        note.write_text(_note_line("relative/account-rel", "branch-rel") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted(["relative/account-rel", "branch-rel"])

    def test_the_strings_a_run_must_not_name_skip_comment_and_blank_lines_and_drop_a_carriage_return(self, tmp_path):
        account = tmp_path.resolve() / "account-crlf"
        note = tmp_path / "note.tsv"
        comment_row = _note_line(tmp_path / "account-comment", "branch-comment")
        note.write_bytes(f"# {comment_row}\r\n\r\n{_note_line(account, 'branch-crlf')}\r\n".encode())

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([str(account), "account-crlf", "branch-crlf"])

    def test_the_strings_a_run_must_not_name_for_a_tilde_cell_include_its_expanded_path(self, tmp_path, monkeypatch):
        home = tmp_path.resolve() / "home"
        monkeypatch.setenv("HOME", str(home))
        note = tmp_path / "note.tsv"
        note.write_text(_note_line("~/account-tilde", "branch-tilde") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted(["~/account-tilde", str(home / "account-tilde"), "account-tilde", "branch-tilde"])

    def test_the_strings_a_run_must_not_name_for_a_symlinked_cell_include_its_resolved_path(self, tmp_path):
        target = tmp_path.resolve() / "real-account"
        target.mkdir()
        link = tmp_path.resolve() / "link-account"
        link.symlink_to(target)
        note = tmp_path / "note.tsv"
        note.write_text(_note_line(link, "branch-link") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([str(link), "link-account", str(target), "real-account", "branch-link"])

    def test_the_strings_a_run_must_not_name_for_an_unresolvable_cell_omit_the_resolved_path_and_do_not_raise(self, tmp_path):
        unresolvable_cell = _path_that_resolve_rejects_as_nul(tmp_path)
        note = tmp_path / "note.tsv"
        note.write_text(_note_line(unresolvable_cell, "branch-nul") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([unresolvable_cell, Path(unresolvable_cell).name, "branch-nul"])

    @pytest.mark.parametrize("blank_cell", [
        pytest.param("", id="an-empty-cell"),
        pytest.param("  ", id="a-whitespace-only-cell"),
    ])
    def test_the_strings_a_run_must_not_name_for_a_blank_config_dir_cell_include_no_blank_entry(self, tmp_path, blank_cell):
        note = tmp_path / "note.tsv"
        note.write_text(_note_line(blank_cell, "branch-blank") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert strings == ["branch-blank"]

    def test_the_strings_a_run_must_not_name_for_a_root_cell_include_no_empty_entry(self, tmp_path):
        note = tmp_path / "note.tsv"
        note.write_text(_note_line("/", "branch-root") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted(["/", "branch-root"])

    def test_the_strings_a_run_must_not_name_for_a_cell_ending_in_a_parent_segment_include_no_dot_dot_entry(self, tmp_path):
        parent = tmp_path.resolve()
        cell = str(parent / "account-up" / "..")
        note = tmp_path / "note.tsv"
        note.write_text(_note_line(cell, "branch-up") + "\n")

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([cell, str(parent), parent.name, "branch-up"])

    def test_the_strings_a_run_must_not_name_read_a_bare_carriage_return_inside_a_row_as_part_of_its_cell(self, tmp_path):
        cell = f"{tmp_path.resolve() / 'account-cr'}\r"
        note = tmp_path / "note.tsv"
        note.write_bytes(f"{cell}\tbranch-cr\ton\toff\toff\n".encode())

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([cell, "account-cr\r", "branch-cr"])

    def test_the_strings_a_run_must_not_name_for_a_note_with_two_accounts_and_branches_include_both_rows(self, tmp_path):
        first_account = tmp_path.resolve() / "account-first"
        second_account = tmp_path.resolve() / "account-second"
        note = tmp_path / "note.tsv"
        note.write_text(
            "\n".join([
                _note_line(first_account, "branch-first"),
                "",
                f"# {_note_line(tmp_path / 'account-commented', 'branch-commented')}",
                _note_line(second_account, "branch-second"),
                "",
            ]),
        )

        strings = _strings_the_output_must_not_name(str(note), None)

        assert sorted(strings) == sorted([
            str(first_account), "account-first", "branch-first",
            str(second_account), "account-second", "branch-second",
        ])

    def test_the_strings_a_run_must_not_name_for_an_absolute_config_dir_argument_are_the_argument_and_its_final_segment(
        self, tmp_path,
    ):
        argument_account = tmp_path.resolve() / "account-argument"
        missing_note = tmp_path / "missing-note.tsv"

        strings = _strings_the_output_must_not_name(str(missing_note), f"{argument_account}//")

        assert sorted(strings) == sorted([f"{argument_account}//", str(argument_account), "account-argument"])

    def test_the_strings_a_run_must_not_name_for_an_unreadable_note_are_the_config_dir_argument_only(self, tmp_path):
        missing_note = tmp_path / "missing-note.tsv"

        assert _strings_the_output_must_not_name(str(missing_note), "argument-text") == ["argument-text"]
        assert _strings_the_output_must_not_name(str(missing_note), None) == []

    def test_no_line_names_a_branch_a_slug_or_a_path_even_with_a_distinctive_worktree_name(self, world, capsys, monkeypatch):
        sentinel_branch = "sentinel-worktree-branch-xyz"
        worktree = _add_linked_worktree(world, sentinel_branch)
        monkeypatch.chdir(worktree)
        worktree_slug = scope._path_to_project_slug(str(worktree))
        _write_session(world, _clean_loop(sentinel_branch), slug=worktree_slug, log=_KEYS_OFF)
        _write_session(world, _clean_loop(_BETA, tag="b"), session_id="sess-b", log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, sentinel_branch), _note_line(world.config_dir, _BETA))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        combined = outcome.out + outcome.err
        for identifying_text in (sentinel_branch, world.main_slug, str(world.repo), str(world.config_dir), _BETA):
            assert identifying_text not in combined
        assert "scanning project directory 1 of 2" in outcome.err

    @pytest.mark.parametrize(
        ("refusal", "expected_code"),
        [("no-config-dir", 2), ("unlisted-config-dir", 2), ("not-opted-in", 1), ("oserror", 4)],
    )
    def test_refusals_and_a_fault_injected_oserror_name_no_branch_and_no_path(
        self, world, capsys, monkeypatch, refusal, expected_code,
    ):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        spies = _install_path_spies(monkeypatch)
        overrides = {}
        if refusal == "no-config-dir":
            overrides["config_dir"] = None
        elif refusal == "unlisted-config-dir":
            overrides["config_dir"] = str(world.config_dir.parent / "acct-elsewhere")
        elif refusal == "not-opted-in":
            (world.config_dir / ".pr-cost-enabled").unlink()
        else:
            monkeypatch.setattr(
                scope, "_iter_scoped_sessions",
                lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EACCES, "sentinel-strerror", str(world.config_dir))),
            )

        outcome = _run(capsys, world, **overrides)

        combined = outcome.out + outcome.err
        assert outcome.code == expected_code
        assert _BRANCH not in combined
        assert str(world.config_dir) not in combined
        assert "acct-elsewhere" not in combined
        if refusal == "not-opted-in":
            assert _consent_gated_paths(world, spies) == []


# --- key state: the session config-keys log ----------------------------------

_ROUTING = routing_guard._ROUTING_KEY
_ROUND2 = routing_guard._ROUND2_PILOT_KEY
_ARCHITECT = routing_guard._ARCHITECT_PILOT_KEY
_FIXED_ROUND_AT = "2026-08-01T10:00:00.000Z"


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _log_with(
    rows_by_session: dict[str, list[tuple[str, dict]]], *, status=routing_guard._LogStatus.READ, earliest: str | None = None,
) -> routing_guard._SessionConfigKeysLog:
    """An in-memory log: per session, (logged_at, keys) rows in file order."""
    rows = {
        session_id: [routing_guard._LogRow(_epoch(logged_at), keys) for logged_at, keys in session_rows]
        for session_id, session_rows in rows_by_session.items()
    }
    all_times = [row.logged_at for session_rows in rows.values() for row in session_rows]
    earliest_logged_at = _epoch(earliest) if earliest else (min(all_times) if all_times else None)
    return routing_guard._SessionConfigKeysLog(status, rows, earliest_logged_at)


def _keys(routing: str = "false", round2: str = "false", architect: str = "false") -> dict:
    return {_ROUTING: routing, _ROUND2: round2, _ARCHITECT: architect}


def _events(*, attachments: tuple[str, ...] = (), lost: tuple[str, ...] = ()) -> routing_guard._RecorderEvents:
    return routing_guard._RecorderEvents(
        min((_epoch(at) for at in attachments), default=None), [_epoch(at) for at in lost],
    )


def _branch_key_states_for(rounds, log, recorder_events) -> routing_guard._BranchKeyStates:
    return routing_guard._branch_key_states(rounds, log, recorder_events, routing_guard._coverage_start(log, recorder_events))


def _state_at(
    log, *, at: str | None = _FIXED_ROUND_AT, session: str | None = "sess-a", key: str = _ROUTING,
    events: dict | None = None, coverage_start: float | None = "derive",
) -> routing_guard._KeyState:
    recorder_events = events or {}
    start = routing_guard._coverage_start(log, recorder_events) if coverage_start == "derive" else coverage_start
    return routing_guard._key_state_at(key, session, _epoch(at) if at else None, log, recorder_events, start)


class _StatWith:
    """A stat result that reads like `real` except for the named fields."""

    def __init__(self, real, **overrides):
        self._real = real
        self._overrides = overrides

    def __getattr__(self, name):
        return self._overrides[name] if name in self._overrides else getattr(self._real, name)


# Far above the microseconds a non-blocking open takes, so only a blocked open trips it.
_FIFO_READ_TIMEOUT_SECONDS = 5


class _ReadlineSizeSpy:
    """A binary handle that records the size argument of every `readline` and delegates to the real handle."""

    def __init__(self, handle):
        self._handle = handle
        self.sizes: list[int | None] = []

    def readline(self, size=-1):
        self.sizes.append(size)
        return self._handle.readline(size)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._handle.__exit__(*exc_info)


def _log_row_text_of_length(content_bytes: int) -> str:
    """A syntactically valid log row for `sess-a` whose line, without its newline, is `content_bytes` long."""
    row = _log_row("sess-a", "2026-08-01T09:00:00Z")
    padding = content_bytes - len(json.dumps({**row, "pad": ""}))
    text = json.dumps({**row, "pad": "x" * padding})
    assert len(text.encode()) == content_bytes
    return text


class TestLogReader:
    def _read(self, tmp_path, *rows, wanted=("sess-a",), **write_kwargs):
        path = _write_log(tmp_path, *rows, **write_kwargs)
        return routing_guard._read_session_config_keys_log(path, set(wanted))

    def test_a_valid_row_is_kept_for_a_wanted_session_and_a_leading_blank_line_parses(self, tmp_path):
        log = self._read(tmp_path, _log_row("sess-a", "2026-08-01T09:00:00Z", routing="true"))

        assert log.status is routing_guard._LogStatus.READ
        assert (tmp_path / ".session-config-keys-log.jsonl").read_bytes().startswith(b"\n")
        assert [(row.logged_at, row.keys[_ROUTING]) for row in log.rows_by_session["sess-a"]] == [
            (_epoch("2026-08-01T09:00:00Z"), "true"),
        ]
        assert log.skipped_lines is False

    def test_a_missing_log_is_absent_with_no_rows(self, tmp_path):
        log = routing_guard._read_session_config_keys_log(tmp_path / ".session-config-keys-log.jsonl", {"sess-a"})

        assert log.status is routing_guard._LogStatus.ABSENT
        assert log.rows_by_session == {} and log.earliest_logged_at is None

    @pytest.mark.parametrize(
        "line_case",
        ["over-long", "bad-utf8", "torn-last", "torn-middle", "non-object", "not-json"],
    )
    def test_a_bad_line_costs_only_itself_and_marks_lines_skipped(self, tmp_path, line_case):
        good = _log_row("sess-a", "2026-08-01T09:00:00Z")
        later_good = _log_row("sess-a", "2026-08-01T09:05:00Z")
        bad_line = {
            "over-long": _log_row_text_of_length(routing_guard._LOG_LINE_MAX_BYTES + 1),
            "bad-utf8": b'{"session_id": "sess-a", "x": "\xff\xfe"}',
            "torn-last": b'{"session_id": "sess-a", "source": "start',
            "torn-middle": b'{"session_id": "sess-a", "source": "start',
            "non-object": b'["session_id"]',
            "not-json": b"not json at all",
        }[line_case]
        rows = [good, bad_line] if line_case == "torn-last" else [good, bad_line, later_good]

        log = self._read(tmp_path, *rows)

        expected = 1 if line_case == "torn-last" else 2
        assert len(log.rows_by_session["sess-a"]) == expected
        assert log.skipped_lines is True

    def test_a_line_of_exactly_the_row_bound_is_kept_and_a_blank_line_is_not_a_skip(self, tmp_path):
        at_the_bound = _log_row_text_of_length(routing_guard._LOG_LINE_MAX_BYTES)

        log = self._read(tmp_path, at_the_bound, "   ")

        assert len(log.rows_by_session["sess-a"]) == 1
        assert log.skipped_lines is False

    def test_a_valid_row_one_byte_over_the_row_bound_is_skipped(self, tmp_path):
        over_the_bound = _log_row_text_of_length(routing_guard._LOG_LINE_MAX_BYTES + 1)

        log = self._read(tmp_path, over_the_bound)

        assert log.rows_by_session == {}
        assert log.skipped_lines is True

    def test_a_file_with_no_newline_in_two_megabytes_is_skipped_and_never_read_past_the_bound_in_one_call(
        self, tmp_path, monkeypatch,
    ):
        file_bytes = 2_000_000
        path = tmp_path / ".session-config-keys-log.jsonl"
        path.write_bytes(b"x" * file_bytes)
        spies: list[_ReadlineSizeSpy] = []
        real_fdopen = os.fdopen

        def fdopen_with_a_size_spy(*args, **kwargs):
            spies.append(_ReadlineSizeSpy(real_fdopen(*args, **kwargs)))
            return spies[-1]

        monkeypatch.setattr(os, "fdopen", fdopen_with_a_size_spy)

        log = routing_guard._read_session_config_keys_log(path, {"sess-a"})

        assert log.status is routing_guard._LogStatus.READ
        assert log.rows_by_session == {} and log.skipped_lines is True
        sizes = [size for spy in spies for size in spy.sizes]
        assert len(sizes) >= file_bytes // (routing_guard._LOG_LINE_MAX_BYTES + 1)
        assert all(0 < size <= routing_guard._LOG_LINE_MAX_BYTES + 1 for size in sizes)

    def test_a_complete_final_line_with_no_trailing_newline_is_kept(self, tmp_path):
        log = self._read(
            tmp_path, _log_row("sess-a", "2026-08-01T09:00:00Z"), _log_row("sess-a", "2026-08-01T09:05:00Z"),
            final_newline=False,
        )

        assert len(log.rows_by_session["sess-a"]) == 2
        assert log.skipped_lines is False

    def test_a_torn_final_line_with_no_trailing_newline_is_skipped_and_the_rows_before_it_are_kept(self, tmp_path):
        log = self._read(
            tmp_path, _log_row("sess-a", "2026-08-01T09:00:00Z"), b'{"session_id": "sess-a", "source": "start',
            final_newline=False,
        )

        assert len(log.rows_by_session["sess-a"]) == 1
        assert log.skipped_lines is True

    def test_a_line_whose_parse_raises_recursion_error_costs_only_itself(self, tmp_path, monkeypatch):
        real_loads = json.loads

        def loads_recursing_on_the_marked_line(text, *args, **kwargs):
            if "recursion-marker" in text:
                raise RecursionError
            return real_loads(text, *args, **kwargs)

        monkeypatch.setattr(json, "loads", loads_recursing_on_the_marked_line)

        log = self._read(
            tmp_path, _log_row("sess-a", "2026-08-01T09:00:00Z"), '{"recursion-marker": 1}',
            _log_row("sess-a", "2026-08-01T09:05:00Z"),
        )

        assert log.status is routing_guard._LogStatus.READ
        assert [row.logged_at for row in log.rows_by_session["sess-a"]] == [
            _epoch("2026-08-01T09:00:00Z"), _epoch("2026-08-01T09:05:00Z"),
        ]
        assert log.skipped_lines is True

    @pytest.mark.parametrize(
        "mutation",
        [
            {"session_id": 7}, {"source": 7}, {"logged_at": 7}, {"keys": []}, {"keys": {_ROUTING: 1}},
            {"keys": {_ROUTING: True}}, {"keys": {_ROUTING: None}},
            {"session_id": "sess-a\n"}, {"source": "startup\n"}, {"logged_at": "2026-08-01T09:00:00Z\n"},
            {"keys": {_ROUTING: "false\n"}},
            {"keys": {"routing\n": "false"}}, {"keys": {"Routing": "false"}}, {"keys": {_ROUTING: "False"}},
            {"keys": {_ROUTING: ""}}, {"keys": {_ROUTING: "a b"}}, {"source": "Start"}, {"session_id": ""},
            {"logged_at": "2026-13-01T09:00:00Z"}, {"logged_at": "2026-08-01T09:00:00.000Z"},
            {"logged_at": "2026-08-01T09:00:00+00:00"}, {"logged_at": "2026-08-01 09:00:00Z"},
            {"logged_at": "٢026-08-01T09:00:00Z"},
        ],
        ids=lambda mutation: json.dumps(mutation, ensure_ascii=True)[:60],
    )
    def test_a_row_failing_validation_is_skipped_alone(self, tmp_path, mutation):
        invalid = {**_log_row("sess-a", "2026-07-01T09:00:00Z"), **mutation}
        valid = _log_row("sess-a", "2026-08-01T09:00:00Z")

        log = self._read(tmp_path, invalid, valid)

        assert [row.logged_at for row in log.rows_by_session["sess-a"]] == [_epoch("2026-08-01T09:00:00Z")]
        assert log.earliest_logged_at == _epoch("2026-08-01T09:00:00Z")
        assert log.skipped_lines is True

    def test_rows_for_sessions_outside_the_scan_are_not_retained_but_still_move_the_coverage_start_and_the_off_flag(
        self, tmp_path,
    ):
        log = self._read(
            tmp_path,
            _log_row("outside-session", "2026-06-01T09:00:00Z", routing="false"),
            _log_row("sess-a", "2026-08-01T09:00:00Z", routing="true"),
        )

        assert set(log.rows_by_session) == {"sess-a"}
        assert log.earliest_logged_at == _epoch("2026-06-01T09:00:00Z")
        assert log.routing_off_seen is True

    def test_an_all_true_log_never_sets_the_off_flag(self, tmp_path):
        log = self._read(tmp_path, _log_row("sess-a", routing="true"), _log_row("other", routing="dollars"))

        assert log.routing_off_seen is False

    def test_the_read_stops_at_the_size_fstat_reported(self, tmp_path, monkeypatch):
        first = _log_row("sess-a", "2026-08-01T09:00:00Z")
        path = _write_log(tmp_path, first)
        reported_size = path.stat().st_size
        _append_log_rows(tmp_path, _log_row("sess-a", "2026-08-01T09:10:00Z"))
        real_fstat = os.fstat

        def fstat_before_the_file_grew(fd):
            return _StatWith(real_fstat(fd), st_size=reported_size)

        monkeypatch.setattr(os, "fstat", fstat_before_the_file_grew)

        log = routing_guard._read_session_config_keys_log(path, {"sess-a"})

        assert [row.logged_at for row in log.rows_by_session["sess-a"]] == [_epoch("2026-08-01T09:00:00Z")]

    @pytest.mark.parametrize(
        "fault", ["symlink-to-valid-log", "dangling-symlink", "directory", "eacces", "foreign-owner"],
    )
    def test_a_log_that_cannot_be_opened_safely_is_unusable_with_no_rows(self, tmp_path, monkeypatch, fault):
        path = tmp_path / ".session-config-keys-log.jsonl"
        if fault == "symlink-to-valid-log":
            real_log = tmp_path / "real-log.jsonl"
            real_log.write_text("\n" + json.dumps(_log_row("sess-a")) + "\n")
            path.symlink_to(real_log)
        elif fault == "dangling-symlink":
            path.symlink_to(tmp_path / "no-such-log.jsonl")
        elif fault == "directory":
            path.mkdir()
        elif fault == "eacces":
            _write_log(tmp_path, _log_row("sess-a"))
            real_os_open = os.open

            def refusing_open(target, *args, **kwargs):
                if os.fsdecode(target) == str(path):
                    raise PermissionError(errno.EACCES, "sentinel-denied")
                return real_os_open(target, *args, **kwargs)

            monkeypatch.setattr(os, "open", refusing_open)
        else:
            _write_log(tmp_path, _log_row("sess-a"))
            real_fstat = os.fstat

            def fstat_with_a_foreign_owner(fd):
                return _StatWith(real_fstat(fd), st_uid=real_fstat(fd).st_uid + 1)

            monkeypatch.setattr(os, "fstat", fstat_with_a_foreign_owner)

        log = routing_guard._read_session_config_keys_log(path, {"sess-a"})

        assert log.status is routing_guard._LogStatus.UNUSABLE
        assert log.rows_by_session == {}

    def test_a_fifo_at_the_log_path_is_unusable_and_the_open_does_not_block(self, tmp_path):
        path = tmp_path / ".session-config-keys-log.jsonl"
        os.mkfifo(path)
        results: list[routing_guard._SessionConfigKeysLog] = []
        reader = threading.Thread(
            target=lambda: results.append(routing_guard._read_session_config_keys_log(path, {"sess-a"})), daemon=True,
        )

        reader.start()
        reader.join(_FIFO_READ_TIMEOUT_SECONDS)
        blocked = reader.is_alive()
        if blocked:
            # Opening a writer releases a reader blocked in a read-only open, so the thread ends and the failure is clean.
            os.close(os.open(path, os.O_WRONLY))
            reader.join(_FIFO_READ_TIMEOUT_SECONDS)

        assert not blocked, "the log open blocked on a FIFO"
        assert results[0].status is routing_guard._LogStatus.UNUSABLE
        assert results[0].rows_by_session == {}

    def test_an_unusable_log_prints_the_fixed_notice_and_reads_each_round_as_log_unusable(self, world, capsys):
        _write_session(world, _clean_loop(), log=None)
        (world.config_dir / ".session-config-keys-log.jsonl").symlink_to(world.config_dir / "no-such-log")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (key-state-unknown: log-unusable)" in outcome.out
        assert outcome.out.splitlines().count(routing_guard._LOG_UNUSABLE_NOTICE) == 1
        assert routing_guard._NEXT_STEP_LINES["log-unusable"] in outcome.out.splitlines()
        assert routing_guard._HOOK_NOT_RUN_ON_ACCOUNT_LINE not in outcome.out
        assert routing_guard._NO_KEY_OFF_ROW_LINE not in outcome.out

    def test_a_missing_log_prints_no_unusable_notice(self, world, capsys):
        _write_session(world, _clean_loop(), log=None)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert routing_guard._LOG_UNUSABLE_NOTICE not in outcome.out

    def test_a_sentinel_in_a_rejected_cell_reaches_neither_stream(self, world, capsys):
        _write_session(world, _clean_loop(), log=None)
        _write_log(
            world.config_dir,
            {**_log_row("sess-a"), "keys": {_ROUTING: "SENTINEL-REJECTED-VALUE"}},
            _log_row("sess-1"),
        )
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert "SENTINEL-REJECTED-VALUE" not in outcome.out + outcome.err
        assert routing_guard._SKIPPED_LOG_LINES_LINE in outcome.out.splitlines()

    def test_a_valid_off_row_for_a_session_outside_the_scan_clears_the_key_off_arm_line(self, world, capsys):
        _write_session(world, _clean_loop(), log=_ROUTING_ON)
        _append_log_rows(world.config_dir, _log_row("session-the-scan-never_saw", routing="false"))
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert routing_guard._NO_KEY_OFF_ROW_LINE not in outcome.out

    def test_a_log_whose_rows_never_show_the_routing_key_off_prints_the_key_off_arm_line(self, world, capsys):
        _write_session(world, _clean_loop(), log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert routing_guard._NO_KEY_OFF_ROW_LINE in outcome.out.splitlines()
        assert routing_guard._HOOK_NOT_RUN_ON_ACCOUNT_LINE not in outcome.out


class TestKeyStateAt:
    def test_the_latest_row_at_or_before_the_round_wins(self):
        log = _log_with({"sess-a": [
            ("2026-08-01T09:00:00Z", _keys(routing="false")),
            ("2026-08-01T09:30:00Z", _keys(routing="true")),
            ("2026-08-01T10:30:00Z", _keys(routing="false")),
        ]})

        assert _state_at(log) == routing_guard._KeyState("on", None)

    def test_a_later_line_wins_a_tie_on_logged_at(self):
        log = _log_with({"sess-a": [
            ("2026-08-01T09:00:00Z", _keys(routing="false")),
            ("2026-08-01T09:00:00Z", _keys(routing="true")),
        ]})

        assert _state_at(log).state == "on"

    def test_a_later_line_with_an_earlier_logged_at_does_not_beat_a_row_that_is_later_in_time(self):
        log = _log_with({"sess-a": [
            ("2026-08-01T09:30:00Z", _keys(routing="true")),
            ("2026-08-01T09:00:00Z", _keys(routing="false")),
        ]})

        assert _state_at(log).state == "on"

    def test_a_round_exactly_at_a_rows_logged_at_takes_that_row(self):
        log = _log_with({"sess-a": [("2026-08-01T10:00:00Z", _keys(routing="true"))]})

        assert _state_at(log, at="2026-08-01T10:00:00.000Z").state == "on"

    def test_a_same_second_row_after_a_millisecond_round_time_is_compared_as_times_not_strings(self):
        # As strings "…10:00:00Z" sorts after "…10:00:00.000Z" ('Z' follows '.'), which would put the row after the round.
        log = _log_with({"sess-a": [("2026-08-01T10:00:00Z", _keys(routing="true"))]})

        assert _state_at(log, at="2026-08-01T10:00:00.000Z").state == "on"
        assert _state_at(log, at="2026-08-01T09:59:59.999Z").cause == "before-recorder"

    def test_a_round_exactly_at_the_coverage_start_with_no_row_for_its_session_reads_hook_not_run(self):
        log = _log_with({"sess-other": [("2026-08-01T10:00:00Z", _keys())]})

        assert _state_at(log, at="2026-08-01T10:00:00.000Z").cause == "hook-not-run"
        assert _state_at(log, at="2026-08-01T09:59:59.999Z").cause == "before-recorder"

    def test_a_taken_row_that_lacks_the_key_is_key_not_in_row_even_when_an_earlier_row_has_it(self):
        log = _log_with({"sess-a": [
            ("2026-08-01T09:00:00Z", _keys(routing="true")),
            ("2026-08-01T09:30:00Z", {_ROUND2: "false"}),
        ]})

        assert _state_at(log) == routing_guard._KeyState(None, "key-not-in-row")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("false", routing_guard._KeyState("off", None)), ("true", routing_guard._KeyState("on", None)),
            ("dollars", routing_guard._KeyState("on", None)), ("unreadable", routing_guard._KeyState(None, "unreadable")),
        ],
    )
    def test_values_map_to_off_on_on_and_unknown(self, value, expected):
        log = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing=value))]})

        assert _state_at(log) == expected

    def test_a_duplicate_row_changes_nothing(self):
        once = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true"))]})
        twice = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true"))] * 2})

        assert _state_at(once) == _state_at(twice)

    def test_each_key_reads_its_own_entry_of_the_taken_row(self):
        log = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true", round2="false", architect="dollars"))]})

        assert [_state_at(log, key=key).state for key in (_ROUTING, _ROUND2, _ARCHITECT)] == ["on", "off", "on"]


_CAUSE_GRID = [
    ("round-untimed", {"at": None}),
    ("round-untimed-for-a-missing-session-id", {"session": None}),
    ("log-unusable", {"status": routing_guard._LogStatus.UNUSABLE}),
    ("lost-event", {"lost": ("2026-08-01T09:30:00.000Z",), "rows": {"sess-a": [("2026-08-01T09:00:00Z", _keys())]}}),
    ("before-recorder", {"rows": {"other": [("2026-08-01T10:30:00Z", _keys())]}}),
    ("hook-not-run", {"rows": {"other": [("2026-08-01T09:00:00Z", _keys())]}}),
    ("unreadable", {"rows": {"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="unreadable"))]}}),
    ("key-not-in-row", {"rows": {"sess-a": [("2026-08-01T09:00:00Z", {_ROUND2: "false"})]}}),
]


class TestCauses:
    @staticmethod
    def _result(spec: dict) -> routing_guard._KeyState:
        log = _log_with(spec.get("rows", {}), status=spec.get("status", routing_guard._LogStatus.READ))
        events = {"sess-a": _events(attachments=spec["lost"], lost=spec["lost"])} if "lost" in spec else {}
        return _state_at(log, at=spec.get("at", _FIXED_ROUND_AT), session=spec.get("session", "sess-a"), events=events)

    @pytest.mark.parametrize(("expected_cause", "spec"), _CAUSE_GRID, ids=[case[0] for case in _CAUSE_GRID])
    def test_each_line_of_the_cause_table_gives_its_literal_cause(self, expected_cause, spec):
        result = self._result(spec)

        assert result.state is None
        assert result.cause == expected_cause.removesuffix("-for-a-missing-session-id")

    def test_the_causes_are_the_table_in_order(self):
        assert routing_guard._CAUSES == (
            "round-untimed", "log-unusable", "lost-event", "before-recorder", "hook-not-run", "unreadable", "key-not-in-row",
        )

    def test_a_cause_is_present_exactly_when_the_state_is_unknown_across_a_grid_of_inputs(self):
        row_sets = {
            "none": {}, "earlier": {"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true"))]},
            "later": {"sess-a": [("2026-08-01T11:00:00Z", _keys(routing="true"))]},
            "other-session": {"other": [("2026-08-01T09:00:00Z", _keys())]},
            "unreadable": {"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="unreadable"))]},
        }
        lost_sets = {
            "none": (), "before-the-round": ("2026-08-01T09:30:00.000Z",), "after-the-round": ("2026-08-01T10:30:00.000Z",),
        }
        for rows_name, rows in row_sets.items():
            for lost_name, lost in lost_sets.items():
                for at in (None, _FIXED_ROUND_AT):
                    for status in routing_guard._LogStatus:
                        events = {"sess-a": _events(attachments=lost, lost=lost)} if lost else {}
                        log = _log_with(rows, status=status)
                        result = _state_at(log, at=at, events=events)
                        label = (rows_name, lost_name, at, status)
                        assert (result.state is None) == (result.cause is not None), label
                        assert result.cause is None or result.cause in routing_guard._CAUSES, label

    def test_log_unusable_takes_precedence_over_a_lost_event(self):
        log = _log_with({}, status=routing_guard._LogStatus.UNUSABLE)

        lost = ("2026-08-01T09:30:00.000Z",)

        result = _state_at(log, events={"sess-a": _events(attachments=lost, lost=lost)})

        assert result.cause == "log-unusable"

    def test_round_untimed_takes_precedence_over_log_unusable(self):
        log = _log_with({}, status=routing_guard._LogStatus.UNUSABLE)

        assert _state_at(log, at=None).cause == "round-untimed"

    def test_a_lost_event_takes_precedence_over_before_recorder(self):
        log = _log_with({"other": [("2026-08-01T10:30:00Z", _keys())]})
        lost = ("2026-08-01T09:30:00.000Z",)

        result = _state_at(log, events={"sess-a": _events(attachments=lost, lost=lost)})

        assert result.cause == "lost-event"

    def test_a_straddling_session_with_an_early_round_a_post_start_attachment_and_no_row_reads_before_recorder(self):
        log = _log_with({"other": [("2026-08-01T10:30:00Z", _keys())]})
        events = {"sess-a": _events(attachments=("2026-08-01T10:40:00.000Z",), lost=())}

        assert _state_at(log, events=events).cause == "before-recorder"

    def test_with_no_log_row_the_coverage_start_falls_back_to_the_earliest_attachment_among_the_scanned_sessions(self):
        log = _log_with({})
        events = {
            "sess-a": _events(attachments=("2026-08-01T10:40:00.000Z",)),
            "sess-b": _events(attachments=("2026-08-01T09:15:00.000Z",)),
        }

        assert routing_guard._coverage_start(log, events) == _epoch("2026-08-01T09:15:00.000Z")
        assert routing_guard._coverage_start(log, {}) is None

    def test_a_row_for_a_session_outside_the_scan_moves_the_coverage_start_not_the_branch_state(self, world, capsys):
        _write_session(world, _clean_loop(), log=None)
        _append_log_rows(world.config_dir, _log_row("outside-session", "2026-08-01T10:30:00Z"))
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "note line 1: missing (key-state-unknown: before-recorder)" in outcome.out

    def test_a_branch_with_several_causes_prints_them_in_table_order(self):
        log = _log_with({"other": [("2026-08-01T10:30:00Z", _keys())]})
        before = _round_facts(session_id="sess-a", open_ts=_epoch("2026-08-01T10:00:00.000Z"), has_marker_write=True)
        after = _round_facts(session_id="sess-b", open_ts=_epoch("2026-08-01T11:00:00.000Z"), open_idx=1, has_marker_write=True)

        states = _branch_key_states_for([after, before], log, {})

        assert states.causes == ("before-recorder", "hook-not-run")

    def test_a_round_with_an_unparseable_timestamp_is_round_untimed_and_does_not_raise(self):
        log = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys())]})
        untimed = _round_facts(session_id="sess-a", open_ts=None, has_marker_write=True)

        states = _branch_key_states_for([untimed], log, {})

        assert states.by_key[_ROUTING] == "unknown" and states.causes == ("round-untimed",)


class TestLostEvents:
    @pytest.mark.parametrize(
        "mutation",
        [
            {"command": "~/.claude/hooks/another-hook.sh"},
            {"command": "~/.claude/hooks/record-session-config-keys.sh/other.sh"},
            {"command": "~/.claude/hooks/record-session-config-keys.sh.bak"},
            {"attachment_type": "hook_blocking_error"},
            {"sidechain": True},
        ],
        ids=[
            "another-hooks-basename", "recorder-name-as-a-directory", "bak-suffix", "another-attachment-type",
            "sidechain-record",
        ],
    )
    def test_a_record_that_is_not_a_main_thread_recorder_hook_success_attachment_is_ignored(self, mutation):
        record = _recorder_attachment("sess-a", "2026-08-01T09:30:00.000Z", **mutation)

        assert routing_guard._recorder_attachment(record) is None

    def test_the_recorder_attachment_with_the_hooks_own_notice_is_a_lost_event(self):
        record = _recorder_attachment("sess-a", "2026-08-01T09:30:00.000Z")

        assert routing_guard._recorder_attachment(record) == ("sess-a", _epoch("2026-08-01T09:30:00.000Z"), True)

    def test_a_whitespace_only_stdout_counts_as_a_lost_event_and_an_empty_one_does_not(self):
        whitespace = _recorder_attachment("sess-a", "2026-08-01T09:30:00.000Z", stdout=" \n")
        empty = _recorder_attachment("sess-a", "2026-08-01T09:30:00.000Z", stdout="")

        assert routing_guard._recorder_attachment(whitespace)[2] is True
        assert routing_guard._recorder_attachment(empty)[2] is False

    @pytest.mark.parametrize("field", ["stdout", "timestamp", "sessionId", "command"])
    def test_a_non_string_field_makes_the_record_unusable(self, field):
        record = _recorder_attachment("sess-a", "2026-08-01T09:30:00.000Z")
        if field in ("stdout", "command"):
            record["attachment"][field] = 7
        else:
            record[field] = 7

        assert routing_guard._recorder_attachment(record) is None

    def test_an_unparseable_timestamp_makes_the_record_unusable(self):
        assert routing_guard._recorder_attachment(_recorder_attachment("sess-a", "not-a-time")) is None

    def test_a_row_rescues_only_when_its_logged_at_is_greater_than_the_attachments_whole_second(self):
        lost = ("2026-08-01T09:30:30.700Z",)
        events = {"sess-a": _events(attachments=lost, lost=lost)}
        same_second = _log_with({"sess-a": [("2026-08-01T09:30:30Z", _keys(routing="true"))]})
        next_second = _log_with({"sess-a": [("2026-08-01T09:30:31Z", _keys(routing="true"))]})

        assert _state_at(same_second, events=events).cause == "lost-event"
        assert _state_at(next_second, events=events).state == "on"

    def test_two_lost_events_leave_the_round_unknown_when_a_row_falls_only_between_them(self):
        lost = ("2026-08-01T09:30:00.000Z", "2026-08-01T09:50:00.000Z")
        events = {"sess-a": _events(attachments=lost, lost=lost)}
        between = _log_with({"sess-a": [("2026-08-01T09:40:00Z", _keys(routing="true"))]})
        after_both = _log_with({"sess-a": [("2026-08-01T09:40:00Z", _keys()), ("2026-08-01T09:55:00Z", _keys(routing="true"))]})

        assert _state_at(between, events=events).cause == "lost-event"
        assert _state_at(after_both, events=events).state == "on"

    def test_a_row_logged_after_the_round_does_not_rescue_a_lost_event_before_it(self):
        lost = ("2026-08-01T09:30:00.000Z",)
        events = {"sess-a": _events(attachments=lost, lost=lost)}
        row_after_the_round = _log_with({"sess-a": [("2026-08-01T10:30:00Z", _keys(routing="true"))]})

        assert _state_at(row_after_the_round, events=events).cause == "lost-event"

    def test_a_lost_event_exactly_at_the_round_time_hides_the_round(self):
        lost = (_FIXED_ROUND_AT,)
        events = {"sess-a": _events(attachments=lost, lost=lost)}
        log = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true"))]})

        assert _state_at(log, events=events).cause == "lost-event"

    def test_a_lost_event_after_the_round_has_no_effect(self):
        lost = ("2026-08-01T10:30:00.000Z",)
        log = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true"))]})

        assert _state_at(log, events={"sess-a": _events(attachments=lost, lost=lost)}).state == "on"

    def test_a_session_split_across_two_files_pools_its_attachments(self, tmp_path):
        scan = routing_guard._ScanFacts()
        first_file = [_recorder_attachment("sess-a", "2026-08-01T09:10:00.000Z", stdout="")]
        second_file = [_recorder_attachment("sess-a", "2026-08-01T09:30:00.000Z")]

        routing_guard._add_session_facts(scan, tmp_path / "first.jsonl", first_file)
        routing_guard._add_session_facts(scan, tmp_path / "second.jsonl", second_file)

        events = scan.recorder_events["sess-a"]
        assert events.earliest_attachment_time == _epoch("2026-08-01T09:10:00.000Z")
        assert events.lost_event_times == [_epoch("2026-08-01T09:30:00.000Z")]

    def test_end_to_end_a_lost_event_before_a_round_with_no_later_row_reads_lost_event_and_names_its_next_step(
        self, world, capsys,
    ):
        records = [_recorder_attachment("sess-1", "2026-08-01T09:30:00.000Z"), *_clean_loop()]
        _write_session(world, records, log={"logged_at": "2026-08-01T09:00:00Z"})
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (key-state-unknown: lost-event)" in outcome.out
        assert routing_guard._NEXT_STEP_LINES["lost-event"] in outcome.out.splitlines()

    def test_end_to_end_a_lost_event_stdout_sentinel_appears_nowhere(self, world, capsys):
        sentinel = "LOST-EVENT-STDOUT-SENTINEL"
        records = [_recorder_attachment("sess-1", "2026-08-01T09:30:00.000Z", stdout=sentinel), *_clean_loop()]
        _write_session(world, records, log={"logged_at": "2026-08-01T09:00:00Z"})
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "note line 1: missing (key-state-unknown: lost-event)" in outcome.out
        published = "".join(path.read_text() for path in _published_files(world))
        assert sentinel not in outcome.out + outcome.err + published


class TestBranchKeyStates:
    @staticmethod
    def _rounds(*opens: str, session_ids: tuple[str, ...] = ("sess-a",)) -> list[routing_guard._RoundFacts]:
        return [
            _round_facts(
                session_id=session_ids[index % len(session_ids)], open_ts=_epoch(opened), open_idx=index,
                has_marker_write=index == len(opens) - 1,
            )
            for index, opened in enumerate(opens)
        ]

    @pytest.mark.parametrize("key", routing_guard._STATE_KEYS)
    @pytest.mark.parametrize("values", [("true", "true", "on"), ("false", "false", "off"), ("true", "false", "mixed")])
    def test_a_key_reads_on_off_or_mixed_over_the_rounds_of_a_branch(self, key, values):
        first, second, expected = values
        log = _log_with({
            "sess-a": [("2026-08-01T09:00:00Z", {**_keys(), key: first}), ("2026-08-01T10:30:00Z", {**_keys(), key: second})],
        })
        rounds = self._rounds("2026-08-01T10:00:00.000Z", "2026-08-01T11:00:00.000Z")

        states = _branch_key_states_for(rounds, log, {})

        assert states.by_key[key] == expected
        assert {other: states.by_key[other] for other in routing_guard._STATE_KEYS if other != key} == {
            other: "off" for other in routing_guard._STATE_KEYS if other != key
        }

    def test_a_flip_between_a_rounds_open_and_its_window_end_does_not_change_that_rounds_state(self):
        log = _log_with({"sess-a": [
            ("2026-08-01T09:00:00Z", _keys(routing="true")), ("2026-08-01T10:00:30Z", _keys(routing="false")),
        ]})
        rounds = self._rounds("2026-08-01T10:00:00.000Z")

        assert _branch_key_states_for(rounds, log, {}).by_key[_ROUTING] == "on"

    def test_two_rounds_of_one_loop_on_either_side_of_a_row_read_mixed_so_reading_only_the_first_fails(self):
        log = _log_with({"sess-a": [
            ("2026-08-01T09:00:00Z", _keys(routing="true")), ("2026-08-01T10:00:30Z", _keys(routing="false")),
        ]})
        rounds = self._rounds("2026-08-01T10:00:00.000Z", "2026-08-01T10:01:00.000Z")

        assert _branch_key_states_for(rounds, log, {}).by_key[_ROUTING] == "mixed"

    def test_one_unknown_round_makes_the_key_unknown_even_when_the_others_agree(self):
        log = _log_with({"sess-a": [("2026-08-01T09:00:00Z", _keys(routing="true"))]})
        rounds = self._rounds("2026-08-01T10:00:00.000Z", "2026-08-01T10:01:00.000Z", session_ids=("sess-a", "sess-without-rows"))

        states = _branch_key_states_for(rounds, log, {})

        assert states.by_key[_ROUTING] == "unknown"
        assert states.causes == ("hook-not-run",)

    def test_end_to_end_a_trailing_round_after_the_last_marker_is_not_read_so_the_branch_stays_whole(self, world, capsys):
        trailing = _round(_BRANCH, "t", 20, marker=False)
        _write_session(world, _clean_loop(_BRANCH), log=_KEYS_OFF)
        _write_session(world, trailing, session_id="sess-2", log=None)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert "note line 1: captured" in outcome.out

    def test_end_to_end_a_key_flipped_between_two_rounds_of_a_branch_reads_missing_mixed(self, world, capsys):
        records = _round(_BRANCH, "a", 0, marker=False) + _round(_BRANCH, "b", 10, marker=True)
        _write_session(world, records, log={"routing": "true"})
        _append_log_rows(world.config_dir, _log_row("sess-1", "2026-08-01T10:05:00Z", routing="false"))
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (key-state-mixed)" in outcome.out
        assert routing_guard._NEXT_STEP_LINES["key-state-mixed"] in outcome.out.splitlines()
        assert _published_files(world) == []

    @pytest.mark.parametrize("key", routing_guard._STATE_KEYS)
    def test_end_to_end_each_stored_state_follows_its_own_key(self, world, capsys, key):
        log_fields = {"routing": "false", "round2": "false", "architect": "false"}
        field = {_ROUTING: "routing", _ROUND2: "round2", _ARCHITECT: "architect"}[key]
        log_fields[field] = "true"
        _write_session(world, _clean_loop(), log=log_fields)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)
        stored = json.loads((world.captures_dir / _digest_name(_BRANCH)).read_text())

        assert outcome.code == 0, outcome.err
        assert {name: stored[name] for name in routing_guard._KeyStates._fields} == {
            name: "on" if name == ("routing" if key == _ROUTING else key) else "off"
            for name in routing_guard._KeyStates._fields
        }

    def test_end_to_end_a_session_file_whose_stem_differs_from_its_session_id_joins_by_session_id(self, world, capsys):
        records = [{**rec, "sessionId": "real-session-id"} for rec in _clean_loop()]
        _write_session(world, records, session_id="file-stem-decoy", log=None)
        _write_log(
            world.config_dir,
            _log_row("file-stem-decoy", routing="true"),
            _log_row("real-session-id", routing="false"),
        )
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert json.loads((world.captures_dir / _digest_name(_BRANCH)).read_text())["routing"] == "off"

    def test_end_to_end_a_deny_with_a_log_that_says_on_does_not_change_the_stored_state_source(self, world, capsys):
        project_dir = _write_session(world, _key_on_loop(), log=_ROUTING_ON)
        _write_marker_writing_orchestrator(project_dir)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert json.loads((world.captures_dir / _digest_name(_BRANCH)).read_text())["routing"] == "on"

    def test_end_to_end_a_key_on_branch_with_a_block_and_no_deny_still_prints_the_no_deny_notice_with_zero_joins(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(), log=_ROUTING_ON)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "note line 1 was worked with the routing key on but no routing deny was found for it" in outcome.err
        assert "0 denied round-opens joined" in outcome.out


class TestCauseLinesEndToEnd:
    def test_an_account_with_neither_a_row_nor_an_attachment_prints_the_hook_has_not_run_line_and_each_row_reads_hook_not_run(
        self, world, capsys,
    ):
        _write_session(world, _clean_loop(), log=None)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert "note line 1: missing (key-state-unknown: hook-not-run)" in outcome.out
        assert routing_guard._HOOK_NOT_RUN_ON_ACCOUNT_LINE in outcome.out.splitlines()
        assert routing_guard._NEXT_STEP_LINES["hook-not-run"] in outcome.out.splitlines()
        assert routing_guard._NO_KEY_OFF_ROW_LINE not in outcome.out

    @pytest.mark.parametrize(
        ("attachment_stdout", "expected_row_cause"),
        [(None, "lost-event"), ("", "hook-not-run")],
        ids=["a-lost-event-attachment", "a-healthy-empty-stdout-attachment"],
    )
    def test_an_absent_log_with_a_recorder_attachment_of_any_kind_prints_no_hook_has_not_run_line(
        self, world, capsys, attachment_stdout, expected_row_cause,
    ):
        records = [_recorder_attachment("sess-1", "2026-08-01T09:30:00.000Z", stdout=attachment_stdout), *_clean_loop()]
        _write_session(world, records, log=None)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert f"note line 1: missing (key-state-unknown: {expected_row_cause})" in outcome.out
        assert routing_guard._HOOK_NOT_RUN_ON_ACCOUNT_LINE not in outcome.out.splitlines()

    def test_a_log_with_no_valid_row_prints_both_the_hook_has_not_run_and_the_key_off_arm_lines(self, world, capsys):
        _write_session(world, _clean_loop(), log=None)
        _write_log(world.config_dir, "not a row")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        lines = outcome.out.splitlines()
        assert routing_guard._HOOK_NOT_RUN_ON_ACCOUNT_LINE in lines
        assert routing_guard._NO_KEY_OFF_ROW_LINE in lines
        assert routing_guard._SKIPPED_LOG_LINES_LINE in lines

    def test_a_healthy_account_prints_none_of_the_account_level_lines(self, world, capsys):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        for line in (
            routing_guard._HOOK_NOT_RUN_ON_ACCOUNT_LINE, routing_guard._NO_KEY_OFF_ROW_LINE,
            routing_guard._SKIPPED_LOG_LINES_LINE, routing_guard._LOG_UNUSABLE_NOTICE,
        ):
            assert line not in outcome.out

    def test_one_next_step_line_prints_per_cause_class_however_many_rows_share_it(self, world, capsys):
        _write_session(world, _clean_loop(_BRANCH, tag="a"), session_id="sess-a", log=None)
        _write_session(world, _clean_loop(_BETA, tag="b"), session_id="sess-b", log=None)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))

        outcome = _run(capsys, world)

        assert outcome.out.splitlines().count(routing_guard._NEXT_STEP_LINES["hook-not-run"]) == 1
        assert outcome.out.count("missing (key-state-unknown: hook-not-run)") == 2

    def test_an_already_captured_row_prints_no_next_step_line_and_a_trimmed_log_cannot_mark_it(self, world, capsys):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        first = _run(capsys, world)
        (world.config_dir / ".session-config-keys-log.jsonl").unlink()

        second = _run(capsys, world)

        assert first.code == 0
        assert second.code == 0, second.err
        assert "note line 1: already captured" in second.out
        assert not any(line in second.out.splitlines() for line in routing_guard._NEXT_STEP_LINES.values())

    @pytest.mark.parametrize("cause_class", sorted(routing_guard._NEXT_STEP_LINES))
    def test_every_cause_class_has_a_fixed_digit_free_next_step_line(self, cause_class):
        line = routing_guard._NEXT_STEP_LINES[cause_class]

        assert cause_class in line
        assert not any(ch.isdigit() for ch in line)

    def test_the_round_untimed_line_covers_both_missing_time_and_missing_session_id_and_says_to_exclude(self):
        line = routing_guard._NEXT_STEP_LINES["round-untimed"]

        assert "no parseable time" in line
        assert "no session id" in line
        assert "list the branch `excluded`" in line

    @pytest.mark.parametrize(
        "untimed_open_record_edit",
        [
            pytest.param(lambda record: record.pop("timestamp"), id="a-round-opening-record-without-a-timestamp"),
            pytest.param(lambda record: record.update(sessionId=None), id="a-round-opening-record-with-a-bad-session-id"),
        ],
    )
    def test_a_round_whose_opening_record_has_no_time_or_session_id_reads_round_untimed_with_its_next_step_line(
        self, world, capsys, untimed_open_record_edit,
    ):
        records = _clean_loop()
        untimed_open_record_edit(records[0])
        _write_session(world, records, log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3, outcome.err
        assert "note line 1: missing (key-state-unknown: round-untimed)" in outcome.out
        assert outcome.out.splitlines().count(routing_guard._NEXT_STEP_LINES["round-untimed"]) == 1

    def test_the_next_step_classes_are_every_cause_and_the_two_row_level_reasons(self):
        assert set(routing_guard._NEXT_STEP_LINES) == {
            *routing_guard._CAUSES, "key-state-mixed", "key-state-contradicted",
        }


class TestSummaryAndDate:
    def test_the_summary_line_equals_a_literal_for_distinct_counts_so_transposed_counts_fail(self, world, capsys):
        captured_branches = [f"captured-{index}" for index in range(3)]
        for index, branch in enumerate(captured_branches):
            _write_session(world, _clean_loop(branch, tag=f"c{index}"), session_id=f"sess-c{index}", log=_KEYS_OFF)
        _plant_standing_file(world, "already-on", _valid_capture_text().replace('"routing": "off"', '"routing": "on"'))
        _plant_standing_file(world, "already-off", _valid_capture_text())
        _plant_standing_file(world, "invalid-one", "not json")
        lines = [_note_line(world.config_dir, branch) for branch in captured_branches]
        lines += [_note_line(world.config_dir, "already-on"), _note_line(world.config_dir, "already-off")]
        lines += [_note_line(world.config_dir, f"no-records-{index}") for index in range(4)]
        lines += [_note_line(world.config_dir, "invalid-one")]
        lines += [_note_line(world.config_dir, f"excluded-{index}", "excluded") for index in range(5)]
        _write_note(world, *lines)

        outcome = _run(capsys, world)

        assert outcome.code == 3
        assert (
            "routing-guard: summary: 3 captured, 2 already captured, 4 missing, 1 invalid, 5 excluded;"
            " key-on files: 1, key-off files: 4"
        ) in outcome.out.splitlines()
        assert outcome.out.splitlines()[-1] == routing_guard._NEXT_ACTION_ACT_ON_LINES

    def test_a_note_with_no_merged_row_ends_with_the_wait_for_a_merge_action(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "excluded-only", "excluded"))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert outcome.out.splitlines()[-1] == routing_guard._NEXT_ACTION_NO_MERGED_ROW
        assert (
            "0 captured, 0 already captured, 0 missing, 0 invalid, 1 excluded; key-on files: 0, key-off files: 0"
        ) in outcome.out

    def test_a_note_with_no_merged_row_beside_format_drift_still_ends_with_the_wait_for_a_merge_action(
        self, world, capsys, monkeypatch,
    ):
        _write_note(world, _note_line(world.config_dir, "excluded-only", "excluded"))
        monkeypatch.setattr(pricing, "_format_drift_detected", lambda: True)

        outcome = _run(capsys, world)

        assert routing_guard._PRICING_INTEGRITY_LINE in outcome.out.splitlines()
        assert outcome.out.splitlines()[-1] == routing_guard._NEXT_ACTION_NO_MERGED_ROW

    @pytest.mark.parametrize(
        ("drift_detected", "expected_next_action"),
        [
            (False, routing_guard._NEXT_ACTION_ACT_ON_LINES),
            (True, routing_guard._NEXT_ACTION_ACT_ON_LINES_AND_RESOLVE_DRIFT),
        ],
        ids=["no-drift", "drift"],
    )
    def test_a_run_with_one_invalid_row_and_no_missing_row_ends_with_the_act_on_the_lines_action(
        self, world, capsys, monkeypatch, drift_detected, expected_next_action,
    ):
        _plant_standing_file(world, _BRANCH, "not json")
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        monkeypatch.setattr(pricing, "_format_drift_detected", lambda: drift_detected)

        outcome = _run(capsys, world)

        assert outcome.code == 3, outcome.err
        assert "0 missing, 1 invalid" in outcome.out
        assert outcome.out.splitlines()[-1] == expected_next_action

    def test_a_clean_run_ends_with_the_capture_again_action(self, world, capsys):
        _write_session(world, _clean_loop(), log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert outcome.out.splitlines()[-1] == routing_guard._NEXT_ACTION_CAPTURE_AGAIN

    def test_the_captured_line_shows_the_utc_date_of_the_branchs_earliest_record_across_its_sessions(self, world, capsys):
        early = [_user_msg("earlier work", branch=_BRANCH, ts="2026-07-10T08:00:00.000Z")]
        _write_session(world, early, session_id="sess-early", log=None)
        _write_session(world, _clean_loop(), session_id="sess-late", log=_KEYS_OFF)
        _append_log_rows(world.config_dir, _log_row("sess-early", "2026-07-10T07:00:00Z"))
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "earliest record 2026-07-10)" in outcome.out

    def test_a_record_timestamp_outside_the_datetime_range_after_utc_conversion_reads_an_unknown_date(self, world, capsys):
        out_of_range_record = _user_msg("work", branch=_BRANCH, ts="0001-01-01T00:00:00+05:00")
        _write_session(world, [out_of_range_record, *_clean_loop()], log=_KEYS_OFF)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert "earliest record an unknown date)" in outcome.out

    def test_a_deny_time_outside_the_datetime_range_reads_an_unknown_time(self):
        assert routing_guard._utc_minute(_epoch("0001-01-01T00:00:00+05:00")) == "an unknown time"
        assert routing_guard._utc_minute(_epoch("2026-08-01T10:01:00Z")) == "2026-08-01 10:01 UTC"

    def test_the_date_reads_no_clock(self):
        assert routing_guard._utc_date(_epoch("2026-07-10T23:59:59.000Z")) == "2026-07-10"
        assert routing_guard._utc_date(None) == "an unknown date"


class TestKeyStateOutput:
    def test_a_sentinel_valued_token_that_matches_the_value_class_reads_on_and_appears_nowhere(self, world, capsys):
        token = "sentinel-value-token"
        _write_session(world, _clean_loop(), log={"routing": token})
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        stored = (world.captures_dir / _digest_name(_BRANCH)).read_text()
        assert json.loads(stored)["routing"] == "on"
        assert token not in outcome.out + outcome.err + stored

    def test_no_line_carries_a_session_id_and_the_shared_check_forbids_one(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop(), session_id="sess-distinctive-id")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def printing_the_session_id(_args):
            print("leaked sess-distinctive-id", file=sys.stderr)
            sys.exit(0)

        monkeypatch.setattr(routing_guard, "cmd_routing_guard", printing_the_session_id)

        with pytest.raises(AssertionError, match="names 1 string"):
            _run(capsys, world)

    @pytest.mark.parametrize(
        "scenario", ["before-recorder", "hook-not-run", "lost-event", "log-unusable", "mixed", "contradicted"],
    )
    def test_the_cause_and_hook_lines_carry_no_digit_outside_the_permitted_shapes(self, world, capsys, scenario):
        if scenario == "before-recorder":
            _write_session(world, _clean_loop(), log=None)
            _append_log_rows(world.config_dir, _log_row("outside-session", "2026-08-01T10:30:00Z"))
        elif scenario == "hook-not-run":
            _write_session(world, _clean_loop(), log=None)
        elif scenario == "lost-event":
            _write_session(world, [_recorder_attachment("sess-1", "2026-08-01T09:30:00.000Z"), *_clean_loop()])
        elif scenario == "log-unusable":
            _write_session(world, _clean_loop(), log=None)
            (world.config_dir / ".session-config-keys-log.jsonl").symlink_to(world.config_dir / "no-such-log")
        elif scenario == "mixed":
            _write_session(world, _round(_BRANCH, "a", 0, marker=False) + _round(_BRANCH, "b", 10, marker=True), log=_ROUTING_ON)
            _append_log_rows(world.config_dir, _log_row("sess-1", "2026-08-01T10:05:00Z", routing="false"))
        elif scenario == "contradicted":
            _write_session(world, [
                *_clean_loop(), _denied_skill_call("deny-c", minute=30), _routing_deny_record("deny-c", ts=_ts(31)),
            ], log=_KEYS_OFF)
        else:
            raise AssertionError(f"unknown cause-line scenario {scenario!r}")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 3, outcome.err
        assert _lines_with_values_outside_the_permitted_shapes(outcome) == []
        if scenario == "contradicted":
            assert "note line 1: missing (key-state-contradicted)" in outcome.out
            assert outcome.out.splitlines().count(routing_guard._NEXT_STEP_LINES["key-state-contradicted"]) == 1


# --- contract with the recorder ----------------------------------------------


def _schema_key_names() -> list[str]:
    return list(_config.schema())


@pytest.mark.skipif(_JQ_ABSENT, reason=_JQ_ABSENT_SKIP_REASON)
class TestRecorderContract:
    @pytest.mark.parametrize("key_on", routing_guard._STATE_KEYS)
    def test_rows_the_real_hook_writes_for_startup_resume_and_compact_are_accepted_and_resolve_to_the_set_values(
        self, tmp_path, key_on,
    ):
        home = tmp_path / "home"
        config_dir = tmp_path / "config"
        home.mkdir()
        config_dir.mkdir()
        (config_dir / "claude-config.toml").write_text(f"{key_on} = true\n")
        for source in ("startup", "resume", "compact"):
            payload = {"hook_event_name": "SessionStart", "session_id": "contract-session", "source": source}
            run = _run_recorder_hook(config_dir, home, payload, tmp_path)
            assert run.returncode == 0 and run.stdout == "", (source, run.stderr)

        log = routing_guard._read_session_config_keys_log(config_dir / ".session-config-keys-log.jsonl", {"contract-session"})

        assert log.status is routing_guard._LogStatus.READ and log.skipped_lines is False
        assert len(log.rows_by_session["contract-session"]) == 3
        after_every_row = max(row.logged_at for row in log.rows_by_session["contract-session"]) + 60
        states = {
            key: routing_guard._key_state_at(key, "contract-session", after_every_row, log, {}, log.earliest_logged_at)
            for key in routing_guard._STATE_KEYS
        }
        assert states == {
            key: routing_guard._KeyState("on" if key == key_on else "off", None) for key in routing_guard._STATE_KEYS
        }

    def test_a_payload_the_real_hook_writes_no_row_for_prints_a_notice_the_reader_classifies_as_a_lost_event(self, tmp_path):
        home = tmp_path / "home"
        config_dir = tmp_path / "config"
        home.mkdir()
        config_dir.mkdir()

        run = _run_recorder_hook(config_dir, home, _NO_ROW_PAYLOAD, tmp_path)

        assert run.returncode == 0, run.stderr
        assert run.stdout.strip() != ""
        assert not (config_dir / ".session-config-keys-log.jsonl").exists()
        attachment = _recorder_attachment("contract-session", "2026-08-01T09:30:00.000Z", stdout=run.stdout)
        assert routing_guard._recorder_attachment(attachment) == (
            "contract-session", _epoch("2026-08-01T09:30:00.000Z"), True,
        )


class TestSchemaDrift:
    def test_every_schema_key_matches_the_readers_key_name_class_and_the_three_constants_are_schema_keys(self):
        schema_key_names = _schema_key_names()

        assert schema_key_names
        assert all(routing_guard._LOG_KEY_NAME_PATTERN.fullmatch(name) for name in schema_key_names)
        assert set(routing_guard._STATE_KEYS) <= set(schema_key_names)


# A printed message cites a doc as `<path> § "<heading>"`, with no backticks around the path.
_MESSAGE_CITATION_RE = re.compile(r'(?P<doc_path>[\w./-]+\.md) § "(?P<heading>[^"\n]+)"')


def _message_constant_texts() -> list[str]:
    """The text of every module-level constant of routing_guard that holds a string, or a collection of strings."""
    texts: list[str] = []
    for name, value in vars(routing_guard).items():
        if not re.fullmatch(r"_[A-Z][A-Z0-9_]*", name):
            continue
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            texts.extend(item for item in value.values() if isinstance(item, str))
        elif isinstance(value, tuple):
            texts.extend(item for item in value if isinstance(item, str))
    return texts


def _message_citations() -> set[tuple[str, str]]:
    return {
        (match["doc_path"], match["heading"])
        for text in _message_constant_texts() for match in _MESSAGE_CITATION_RE.finditer(text)
    }


# Messages cite the routing-guard doc section as `<path> routing-guard "<heading>"`, a form `_MESSAGE_CITATION_RE` misses.
_ROUTING_GUARD_CITATION_RE = re.compile(r'docs/transcript-analysis\.md routing-guard "(?P<heading>[^"\n]+)"')

# The banner cites its doc as `<path>` section "<heading>", a form `_MESSAGE_CITATION_RE` does not match.
_BANNER_CITATION_RE = re.compile(r'`(?P<doc_path>[\w./-]+\.md)` section "(?P<heading>[^"\n]+)"')


def _routing_guard_section_citation_headings() -> set[str]:
    """Every heading cited as `routing-guard "<heading>"` by a message constant or by the unlisted-deny refusal.

    The refusal is built inside `_reconcile_denies`, so it is produced by calling that function."""
    unlisted_deny_refusals = routing_guard._reconcile_denies(
        [routing_guard._DenyFact("unlisted-branch", 1.0, "sess-1")], [_STATE_ON], [],
        Path("/placeholder/account-a"), set(), {}, {},
    ).refusals
    texts = [*_message_constant_texts(), *unlisted_deny_refusals]
    return {match["heading"] for text in texts for match in _ROUTING_GUARD_CITATION_RE.finditer(text)}


def _select_tests_module():
    script = Path(__file__).parent.parent / "select-tests.py"
    spec = importlib.util.spec_from_file_location("select_tests", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMessageCitations:
    def test_every_doc_a_section_citation_selects_this_module(self):
        """Scope: docs cited as `<path> § "<heading>"` plus the banner's doc; other citation forms are not read."""
        select_tests = _select_tests_module()
        this_module_path = str(Path(__file__).resolve().relative_to(REPO_ROOT))
        banner_match = _BANNER_CITATION_RE.search(routing_guard._DO_NOT_PUBLISH_BANNER)
        assert banner_match
        cited_doc_paths = {doc_path for doc_path, _heading in _message_citations()} | {banner_match["doc_path"]}

        not_selecting_this_module = {
            doc_path: selection.target_paths
            for doc_path in sorted(cited_doc_paths)
            for selection in [select_tests.select_pytest_targets([doc_path])]
            if not selection.is_full_suite and not any(
                select_tests._is_under(this_module_path, target)
                or this_module_path in select_tests._expand_target(target, repo_root=REPO_ROOT)
                for target in selection.target_paths
            )
        }
        assert _message_citations()
        assert not_selecting_this_module == {}

    def test_the_lost_event_next_step_cites_the_notice_section_of_the_log_doc(self):
        assert ("docs/session-config-keys-log.md", "Notice") in _message_citations()

    def test_the_printed_messages_still_cite_every_section_they_are_known_to_cite(self):
        expected_citations = {
            ("README.md", "Updating after git pull"),
            ("docs/session-config-keys-log.md", "Coverage"),
            ("docs/session-config-keys-log.md", "Notice"),
        }

        assert expected_citations <= _message_citations()

    def test_the_banner_cites_an_existing_heading_of_the_redaction_doc(self):
        match = _BANNER_CITATION_RE.search(routing_guard._DO_NOT_PUBLISH_BANNER)

        assert match
        assert match["doc_path"] == "docs/private-project-redaction.md"
        assert normalize_heading(match["heading"]) in heading_texts((REPO_ROOT / match["doc_path"]).read_text())

    def test_the_routing_guard_section_holds_every_heading_a_printed_message_cites(self):
        cited_headings = _routing_guard_section_citation_headings()
        section_headings = heading_texts("\n".join(_routing_guard_section_lines(_TRANSCRIPT_ANALYSIS_DOC.read_text())))

        assert {"The captures directory", "Deny reconciliation"} <= cited_headings
        assert [heading for heading in sorted(cited_headings) if normalize_heading(heading) not in section_headings] == []

    def test_the_log_doc_holds_the_notice_heading_the_lost_event_next_step_cites(self):
        assert normalize_heading("Notice") in heading_texts(_SESSION_CONFIG_KEYS_LOG_DOC.read_text())

    def test_every_doc_section_a_printed_message_cites_exists(self):
        citations = _message_citations()

        unresolved = [
            f'{doc_path} § "{heading}"' for doc_path, heading in sorted(citations)
            if not (REPO_ROOT / doc_path).is_file()
            or normalize_heading(heading) not in heading_texts((REPO_ROOT / doc_path).read_text())
        ]
        assert citations
        assert not unresolved


# The documented loop reads its short note and runs a stub per account: a few shell builtins and at most three stub
# processes, so milliseconds. 30 seconds is far above that, and well below the 25-minute CI job cap
# (.github/workflows/tests.yml) that a loop regressed to a blocking read would otherwise stall until.
_DOCUMENTED_LOOP_TIMEOUT_SECONDS = 30


def _documented_capture_loop(note: Path) -> str:
    """The `bash` fence under the doc's run order that prints `capture exit:` lines, with its note path filled in."""
    fence_lines: list[str] = []
    in_fence = False
    for line in _routing_guard_section_lines(_TRANSCRIPT_ANALYSIS_DOC.read_text()):
        if line.lstrip().startswith("```"):
            if in_fence and any("capture exit:" in fence_line for fence_line in fence_lines):
                break
            in_fence = not in_fence
            fence_lines = []
        elif in_fence:
            fence_lines.append(line)
    assert fence_lines, "the routing-guard section holds no fence that prints capture exit lines"
    return textwrap.dedent("\n".join(fence_lines)).replace("<note-path>", shlex.quote(str(note)))


def _install_stub_at_documented_path(home: Path, stub_script: str) -> None:
    """Write `stub_script` where the doc's `python3 ~/.claude/scripts/transcript-analysis.py` resolves under `home`."""
    scripts_dir = home / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "transcript-analysis.py").write_text(stub_script)


class TestDocumentedCaptureLoop:
    # The stub is a Python script, since the loop runs the tool as `python3 <path>`.
    STUB_SCRIPT = """\
import os
import sys

args = sys.argv[1:]
if len(args) != 6 or (args[0], args[2], args[3], args[5]) != ("--config-dir", "routing-guard", "--note", "--capture"):
    sys.exit(98)
# Without the documented "< /dev/null", this read swallows the rest of the loop's account list, so later accounts never run.
sys.stdin.read()
with open(os.environ["STUB_INVOCATIONS"], "a") as invocations:
    invocations.write(args[1] + "\\n")
exit_code_file = os.path.join(os.environ["STUB_EXIT_CODES"], os.path.basename(args[1]))
if not os.path.isfile(exit_code_file):
    sys.exit(99)
with open(exit_code_file) as exit_code_handle:
    sys.exit(int(exit_code_handle.read()))
"""

    def _run_loop(
        self, tmp_path: Path, exit_codes_in_sorted_order: tuple[int, ...],
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the documented loop over a note naming one account per exit code.

        Returns the run and each `--config-dir` the stub saw, relative to `tmp_path`. The accounts sort as `acct-a`,
        `acct-b`, `~/acct-c` under the C locale, and the note lists them out of order.
        """
        home = tmp_path / "home"
        exit_codes_dir = tmp_path / "exit-codes"
        for directory in (home, exit_codes_dir):
            directory.mkdir()
        _install_stub_at_documented_path(home, self.STUB_SCRIPT)
        account_a, account_b = tmp_path / "acct-a", tmp_path / "acct-b"
        accounts_by_sorted_order = [str(account_a), str(account_b), "~/acct-c"]
        for account, exit_code in zip(
            accounts_by_sorted_order[:len(exit_codes_in_sorted_order)], exit_codes_in_sorted_order, strict=True,
        ):
            (exit_codes_dir / Path(account).name).write_text(str(exit_code))
        note = tmp_path / "note.tsv"
        # A CRLF row holds only its config directory: the loop reads the first cell, which keeps a trailing CR unless stripped.
        note_text = "\n".join([
            "# acct-z\tcommented-out\tmerged",
            *([_note_line("~/acct-c", "branch-c")] if len(exit_codes_in_sorted_order) == 3 else []),
            "",
            _note_line(account_a, "branch-a"),
            f"{account_b}\r",
            _note_line(account_a, "branch-a-again"),
        ]) + "\n"
        note.write_text(note_text)
        invocations = tmp_path / "invocations"
        environment = {
            **os.environ, "HOME": str(home), "LC_ALL": "C",
            "STUB_INVOCATIONS": str(invocations), "STUB_EXIT_CODES": str(exit_codes_dir),
        }

        run = subprocess.run(
            ["bash", "-c", _documented_capture_loop(note)], env=environment, capture_output=True, text=True, check=False,
            timeout=_DOCUMENTED_LOOP_TIMEOUT_SECONDS,
        )

        seen = invocations.read_text().splitlines() if invocations.exists() else []
        return run, [str(Path(account).relative_to(tmp_path)) for account in seen]

    @pytest.mark.parametrize(
        ("exit_codes_in_sorted_order", "expected_loop_exit", "expected_accounts_run"),
        [
            pytest.param((0, 0), 0, ["acct-a", "acct-b"], id="all-zero-exits-zero"),
            pytest.param((0, 3, 0), 3, ["acct-a", "acct-b", "home/acct-c"], id="an-exit-3-continues-and-the-loop-exits-3"),
            pytest.param((3, 2, 0), 2, ["acct-a", "acct-b"], id="a-later-failure-beats-an-earlier-3-and-spares-third-account"),
            pytest.param((2, 3), 2, ["acct-a"], id="the-first-failure-stops-the-loop-before-a-later-3"),
        ],
    )
    def test_the_loop_exits_with_the_first_code_that_is_neither_0_nor_3_else_3_else_0(
        self, tmp_path, exit_codes_in_sorted_order, expected_loop_exit, expected_accounts_run,
    ):
        run, accounts_run = self._run_loop(tmp_path, exit_codes_in_sorted_order)

        assert run.returncode == expected_loop_exit, run.stderr
        assert run.stdout.splitlines() == [
            f"capture exit: {exit_code}" for exit_code in exit_codes_in_sorted_order[:len(expected_accounts_run)]
        ]
        assert accounts_run == expected_accounts_run

    @pytest.mark.parametrize(
        "note_text",
        [
            pytest.param("", id="an-empty-note"),
            pytest.param("# only a comment\n\n", id="a-note-of-comments-and-blank-lines"),
            pytest.param(None, id="a-note-path-that-does-not-exist"),
        ],
    )
    def test_the_loop_exits_nonzero_when_the_note_yields_no_config_directory(self, tmp_path, note_text):
        home = tmp_path / "home"
        home.mkdir()
        invocations = tmp_path / "invocations"
        _install_stub_at_documented_path(
            home, f"with open({str(invocations)!r}, 'a') as invocations:\n    invocations.write('ran\\n')\n",
        )
        note = tmp_path / "note.tsv"
        if note_text is not None:
            note.write_text(note_text)
        environment = {**os.environ, "HOME": str(home), "LC_ALL": "C"}

        run = subprocess.run(
            ["bash", "-c", _documented_capture_loop(note)], env=environment, capture_output=True, text=True, check=False,
            timeout=_DOCUMENTED_LOOP_TIMEOUT_SECONDS,
        )

        assert run.returncode == 2
        assert "no capture ran" in run.stderr
        assert run.stdout == ""
        assert not invocations.exists()
