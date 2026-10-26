"""Tests for transcript_analysis/routing_guard.py's --capture mode, on synthetic fixtures only."""
import argparse
import errno
import gc
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import weakref
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import _config
import pytest
from transcript_analysis import pricing, review_trace, routing_guard, scope

from ._pr_cost_helpers import _enable_pr_cost
from ._review_round_helpers import (
    _ORCHESTRATOR_AGENT_TYPE,
    _SONNET,
    _as_sidechain,
    _digest_name,
    _environment_unchanged,
    _install_path_spies,
    _install_subprocess_recorder,
    _invoke_routing_guard,
    _leak_sentinel,
    _marker_write_use,
    _note_line,
    _note_row,
    _orchestrator_return_record,
    _orchestrator_spawn_record,
    _Outcome,
    _RecorderPositiveControls,
    _routing_deny_record,
)
from .conftest import (
    _agent_use,
    _asst,
    _commit,
    _compact_boundary_rec,
    _hook_deny,
    _hook_deny_current,
    _init_repo,
    _priced,
    _skill_block,
    _slash_user,
    _task_notification_msg,
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


def _write_session(world: _World, records: list[dict], *, session_id: str = "sess-1", slug: str | None = None) -> Path:
    project_dir = world.projects / (slug or world.main_slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(project_dir / f"{session_id}.jsonl", records)
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
        _user_msg("next task", branch=branch, ts=_ts(start + 2)),
        _priced(_SONNET, input=later_input, branch=branch, ts=_ts(start + 3)),
    ]


def _published_files(world: _World) -> list[Path]:
    return sorted(world.captures_dir.glob("*.json")) if world.captures_dir.exists() else []


def _stored_loops(world: _World, branch: str) -> list[dict]:
    return json.loads((world.captures_dir / _digest_name(branch)).read_text())["loops"]


def _valid_capture_text(**loop_overrides) -> str:
    loop = {
        "overhead_tokens": {_SONNET: {"standard": {"input": 5, "output": 1}}},
        "growth_tokens": 7, "later_turns": {_SONNET: 2}, "degraded": False,
    }
    loop.update(loop_overrides)
    return json.dumps({"loops": [loop]})


def _capture_one_branch(world: _World, capsys, branch: str = _BRANCH) -> _Outcome:
    _write_session(world, _clean_loop(branch))
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


def _refusal_notes(config_dir: str) -> list[tuple[str, str]]:
    """(id, note text) for each refusal. The bad row sits on line 2, below a comment line."""
    def note(row: str) -> str:
        return f"# comment\n{row}\n"

    def row(
        *, branch: str = _SENTINEL_BRANCH, arm: str = "on", pilots: tuple[str, str] = ("off", "off"),
        dir_cell: str | None = None,
    ) -> str:
        return "\t".join([dir_cell or config_dir, branch, arm, *pilots])

    return [
        ("too-few-columns", note(f"{config_dir}\t{_SENTINEL_BRANCH}\ton\toff")),
        ("trailing-tab", note(row() + "\t")),
        ("unknown-arm", note(row(arm="maybe"))),
        ("config-path-in-arm-column", note(row(arm=config_dir))),
        ("relative-dir", note(row(dir_cell="relative/dir"))),
        ("dash-on-an-on-row", note(row(pilots=("-", "off")))),
        ("one-dash-on-an-unmerged-row", note(row(arm="unmerged", pilots=("-", "on")))),
        ("pilot-value-on-an-unmerged-row", note(row(arm="unmerged", pilots=("on", "on")))),
        ("branch-with-surrounding-whitespace", note(row(branch=f" {_SENTINEL_BRANCH}"))),
        ("empty-branch", note(row(branch=""))),
        ("default-branch-main", note(row(branch="main"))),
        ("default-branch-develop-on-unmerged", note(row(branch="develop", arm="unmerged", pilots=("-", "-")))),
        ("branch-repeated-on-one-account", note(row() + "\n" + row())),
        ("branch-repeated-on-two-accounts", note(row() + "\n" + row(dir_cell=config_dir + "-other"))),
    ]


class TestNoteParser:
    @pytest.mark.parametrize("case_id", [case_id for case_id, _text in _refusal_notes("/placeholder")])
    def test_refuses_each_malformed_note_with_exit_2_naming_the_line_and_echoing_no_cell(self, world, capsys, case_id):
        text = dict(_refusal_notes(str(world.config_dir)))[case_id]
        _write_note(world, text=text)

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "note line 2" in outcome.err or "note line 3" in outcome.err
        assert _SENTINEL_BRANCH not in outcome.err + outcome.out
        assert str(world.config_dir) not in outcome.err + outcome.out
        assert _published_files(world) == []

    def test_refuses_a_tilde_dir_when_home_is_unset(self, world, capsys, monkeypatch):
        monkeypatch.delenv("HOME", raising=False)
        _write_note(world, text=f"~/acct\t{_SENTINEL_BRANCH}\ton\toff\toff\n")

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "note line 1" in outcome.err

    def test_parses_crlf_lines_and_a_tilde_dir_against_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

        rows = routing_guard._parse_note("~/acct\tbranch-one\ton\toff\ton\r\n")

        assert rows == [routing_guard._NoteRow(1, (tmp_path / "acct").resolve(), "branch-one", "on", "off", "on")]

    def test_two_spellings_of_one_directory_name_one_account(self, tmp_path):
        dir_cell = str(tmp_path / "acct")

        rows = routing_guard._parse_note(f"{dir_cell}/\tone\ton\toff\toff\n{dir_cell}\ttwo\toff\toff\toff\n")

        assert rows[0].resolved_config_dir == rows[1].resolved_config_dir

    def test_accepts_unicode_and_slash_branch_names_and_a_comment_only_note(self, tmp_path):
        rows = routing_guard._parse_note(f"{tmp_path}\tfeat/ünï-branch\tunmerged\t-\t-\n")

        assert [row.branch for row in rows] == ["feat/ünï-branch"]
        assert routing_guard._parse_note("# only a comment\n\n") == []

    def test_does_not_trim_cells_so_a_padded_arm_is_an_unknown_value(self, tmp_path):
        with pytest.raises(routing_guard._NoteRefusal):
            routing_guard._parse_note(f"{tmp_path}\tone\t on\toff\toff\n")

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

    def test_accepts_a_note_outside_any_git_work_tree(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "merged-elsewhere", "unmerged"))

        assert _run(capsys, world).code == 0

    def test_a_note_readable_by_others_prints_the_permission_notice(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "unmerged-one", "unmerged"), mode=0o644)

        outcome = _run(capsys, world)

        assert routing_guard._PERMISSIVE_NOTE_NOTICE in outcome.err

    def test_an_owner_only_note_prints_no_permission_notice(self, world, capsys):
        _write_note(world, _note_line(world.config_dir, "unmerged-one", "unmerged"), mode=0o600)

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

    def test_the_note_flag_is_required_by_the_parser(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            _build_parser().parse_args(["routing-guard", "--capture"])

        assert exit_info.value.code == 2
        assert "--note" in capsys.readouterr().err


_OPT_IN_FAULTS = [
    ("not-opted-in", False, routing_guard._OptInOutcome.NOT_OPTED_IN, "not opted in"),
    ("config-dir-unresolved", None, routing_guard._OptInOutcome.CONFIG_DIR_UNRESOLVED, "could not resolve"),
    (
        "schema-empty", _config.ConfigSchemaEmptyError("pr_cost_recording"),
        routing_guard._OptInOutcome.SCHEMA_EMPTY, "empty or malformed",
    ),
    (
        "row-truncated", _config.ConfigSchemaRowTruncatedError("pr_cost_recording"),
        routing_guard._OptInOutcome.ROW_TRUNCATED, "truncated",
    ),
    ("unknown-key", KeyError("pr_cost_recording"), routing_guard._OptInOutcome.UNKNOWN_KEY, "unknown config key"),
]


class TestOptInFunction:
    def test_reports_opted_in_for_an_account_with_the_key_on(self, world):
        assert routing_guard._classify_capture_opt_in(world.config_dir) is routing_guard._OptInOutcome.OPTED_IN

    @pytest.mark.parametrize(("case_id", "reader_result", "expected_outcome", "message_fragment"), _OPT_IN_FAULTS)
    def test_classifies_each_non_opted_in_outcome_and_capture_exits_1_with_its_message(
        self, world, capsys, monkeypatch, case_id, reader_result, expected_outcome, message_fragment,
    ):
        def config_enabled(_key, config_dir_override=None):
            if isinstance(reader_result, Exception):
                raise reader_result
            return reader_result

        monkeypatch.setattr(_config, "config_enabled", config_enabled)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert routing_guard._classify_capture_opt_in(world.config_dir) is expected_outcome
        assert outcome.code == 1
        assert message_fragment in outcome.err
        assert _published_files(world) == []

    def test_an_unknown_key_is_told_apart_from_an_unreadable_schema(self, world, monkeypatch):
        def raise_unknown_key(_key, config_dir_override=None):
            raise KeyError("pr_cost_recording")

        monkeypatch.setattr(_config, "config_enabled", raise_unknown_key)

        assert routing_guard._classify_capture_opt_in(world.config_dir) is routing_guard._OptInOutcome.UNKNOWN_KEY

        monkeypatch.setattr(_config, "schema", dict)

        assert routing_guard._classify_capture_opt_in(world.config_dir) is routing_guard._OptInOutcome.SCHEMA_UNREADABLE


class TestConfigReaderStderr:
    @staticmethod
    def _write_noisy_state_file(config_dir: Path) -> None:
        (config_dir / "claude-config.toml").write_text(
            "malformed-line-sentinel without an equals sign\nunknown_key_sentinel = true\n",
        )

    def test_a_malformed_and_an_unknown_line_leave_neither_the_path_nor_the_line_in_any_stream(self, world, capsys, tmp_path):
        second_account = tmp_path / "acct-b"
        (second_account / "projects").mkdir(parents=True)
        self._write_noisy_state_file(world.config_dir)
        self._write_noisy_state_file(second_account)
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(second_account, "branch-on-b"))

        outcome = _run(capsys, world)

        combined = outcome.out + outcome.err
        assert outcome.code == 0, outcome.err
        assert "malformed-line-sentinel" not in combined
        assert "unknown_key_sentinel" not in combined
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


# --- the whole verdict -------------------------------------------------------


def _round_facts(**overrides) -> routing_guard._RoundFacts:
    fields = {
        "session_key": "session-a", "open_idx": 0, "open_ts": 1.0, "has_marker_write": False,
        "context_at_open": 10, "context_at_window_end": 40, "later_turns": {}, "overhead_tokens": {},
        "degraded": False, "denied_open": False, "spawn_open": False,
    }
    fields.update(overrides)
    return routing_guard._RoundFacts(**fields)


def _branch_facts(**overrides) -> routing_guard._BranchFacts:
    return routing_guard._BranchFacts(**{"has_timestamped_record": True, **overrides})


class TestWholeReason:
    def test_a_branch_with_no_facts_has_no_records(self):
        assert routing_guard._whole_reason(None, []) == "no-records"

    def test_a_branch_whose_records_carry_no_parseable_timestamp_has_no_records(self):
        marker_round = _round_facts(has_marker_write=True)

        assert routing_guard._whole_reason(_branch_facts(has_timestamped_record=False), [marker_round]) == "no-records"

    def test_a_branch_with_no_marker_writing_round_has_no_complete_loop(self):
        assert routing_guard._whole_reason(_branch_facts(), [_round_facts()]) == "no-complete-loop"

    def test_an_unreadable_orchestrator_transcript_blocks_an_otherwise_whole_branch(self):
        facts = _branch_facts(orchestrator_unreadable=True)

        assert routing_guard._whole_reason(facts, [_round_facts(has_marker_write=True)]) == "orchestrator-unreadable"

    def test_a_whole_branch_has_no_reason(self):
        assert routing_guard._whole_reason(_branch_facts(), [_round_facts(has_marker_write=True)]) is None

    def test_reasons_take_precedence_in_the_order_records_then_loop_then_orchestrator(self):
        unreadable_without_records = _branch_facts(has_timestamped_record=False, orchestrator_unreadable=True)
        unreadable_without_loop = _branch_facts(orchestrator_unreadable=True)

        assert routing_guard._whole_reason(unreadable_without_records, []) == "no-records"
        assert routing_guard._whole_reason(unreadable_without_loop, [_round_facts()]) == "no-complete-loop"

    def test_leading_default_branch_records_are_credited_to_the_first_branch_entered(self, world):
        scan = routing_guard._ScanFacts()
        records = [
            _asst(_SONNET, branch="main", ts=_ts(0)),
            _asst(_SONNET, branch="main", ts=_ts(1)),
            _asst(_SONNET, branch=_BRANCH, ts=_ts(5)),
        ]

        routing_guard._add_session_facts(scan, world.projects / "s.jsonl", records)

        assert set(scan.branches) == {_BRANCH}
        assert scan.branches[_BRANCH].has_timestamped_record

    def test_capture_reports_each_missing_reason_by_note_line_and_exits_3(self, world, capsys):
        no_marker = [
            _priced(_SONNET, input=1_000, branch="no-marker", ts=_ts(0), content=[_skill_block("sk-n", "code-review")]),
        ]
        spawn_without_transcript = [
            _orchestrator_spawn_record("o9", ts=_ts(10), branch="no-transcript"),
            _priced(_SONNET, input=1_000, branch="no-transcript", ts=_ts(11), content=[_marker_write_use("mk-u")]),
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


def _write_marker_writing_orchestrator(project_dir: Path, *, session_id: str = "sess-1", tool_use_id: str = "o1") -> None:
    """The review-orchestrator's own transcript, holding the clean-marker write."""
    records = [_as_sidechain(_priced(
        _SONNET, input=5_000, branch=_BRANCH, ts=_ts(2),
        content=[_marker_write_use(f"mk-{tool_use_id}")],
    ))]
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


def _round(branch: str, tag: str, minute: int, *, marker: bool, input_tokens: int = 1_000) -> list[dict]:
    """One code-review round opened by a Skill call, its outcome span holding a marker write or not."""
    content = [_marker_write_use(f"mk-{tag}")] if marker else []
    return [
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute), content=[_skill_block(f"sk-{tag}", "code-review")]),
        _priced(_SONNET, input=input_tokens, branch=branch, ts=_ts(minute + 1), content=content),
        _user_msg(f"prompt after {tag}", branch=branch, ts=_ts(minute + 2)),
    ]


class TestLoops:
    def test_two_rounds_ending_at_a_marker_write_form_one_loop_whose_overhead_covers_both(self, world, capsys):
        records = (
            _round(_BRANCH, "a", 0, marker=False, input_tokens=1_000)
            + _round(_BRANCH, "b", 10, marker=True, input_tokens=2_000)
        )
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        loops = _stored_loops(world, _BRANCH)
        assert "captured (2 rounds, 1 loops" in outcome.out
        assert len(loops) == 1
        assert loops[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 2 * 1_000 + 2 * 2_000

    def test_two_marker_writes_give_two_loops(self, world, capsys):
        records = _round(_BRANCH, "a", 0, marker=True) + _round(_BRANCH, "b", 10, marker=True)
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "captured (2 rounds, 2 loops" in outcome.out
        assert len(_stored_loops(world, _BRANCH)) == 2

    def test_a_marker_write_in_a_non_last_round_ends_its_loop_there_and_later_rounds_are_dropped(self, world, capsys):
        records = (
            _round(_BRANCH, "a", 0, marker=False, input_tokens=1_000)
            + _round(_BRANCH, "b", 10, marker=True, input_tokens=2_000)
            + _round(_BRANCH, "c", 20, marker=False, input_tokens=4_000)
        )
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        loops = _stored_loops(world, _BRANCH)
        assert "captured (2 rounds, 1 loops" in outcome.out
        assert loops[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 2 * 1_000 + 2 * 2_000

    def test_a_denied_skill_and_its_orchestrator_spawn_form_one_loop_whose_overhead_includes_the_denied_turn(self, world, capsys):
        project_dir = _write_session(world, _key_on_loop())
        _write_marker_writing_orchestrator(project_dir)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        loops = _stored_loops(world, _BRANCH)
        assert outcome.out.count("captured (") == 1
        assert "captured (2 rounds, 1 loops, 0 with growth defined, 1 denied round-opens joined, degraded: no)" in outcome.out
        assert loops[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 40_000 + 100_000 + 5_000

    def test_a_key_on_loop_whose_orchestrator_transcript_is_missing_has_no_complete_loop(self, world, capsys):
        _write_session(world, _key_on_loop())
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
            _user_msg("next task", branch=_BRANCH, ts=_ts(3)),
            _priced(_SONNET, input=9_000, branch=_BRANCH, ts=_ts(4)),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        loop = _stored_loops(world, _BRANCH)[0]
        assert loop["overhead_tokens"][_SONNET]["standard"]["input"] == 1_000 + 2_000
        assert loop["later_turns"] == {_SONNET: 1}

    def test_a_typed_code_review_opens_a_round(self, world, capsys):
        records = [
            _slash_user("code-review", branch=_BRANCH, ts=_ts(0)),
            _priced(_SONNET, input=3_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "captured (1 rounds, 1 loops" in outcome.out
        assert _stored_loops(world, _BRANCH)[0]["overhead_tokens"][_SONNET]["standard"]["input"] == 3_000

    def test_sessions_in_reverse_path_order_still_sort_by_timestamp(self, world, capsys):
        earlier_but_later_path = _round(_BRANCH, "a", 0, marker=False)
        later_but_earlier_path = _round(_BRANCH, "b", 30, marker=True)
        _write_session(world, earlier_but_later_path, session_id="zzz-first-in-time")
        _write_session(world, later_but_earlier_path, session_id="aaa-last-in-time")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "captured (2 rounds, 1 loops, 0 with growth defined" in outcome.out

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


class TestNestTokenSink:
    def test_a_none_model_and_an_empty_model_name_add_their_counts_per_class_instead_of_replacing_each_other(self):
        token_sink = {
            (None, "standard"): {"input": 5, "cache_read": 3},
            ("", "standard"): {"input": 7, "output": 2},
        }

        nested = routing_guard._nest_token_sink(token_sink)

        assert nested == {"": {"standard": {"input": 12, "cache_read": 3, "output": 2}}}


# --- growth and later turns --------------------------------------------------


class TestGrowthAndLaterTurns:
    def test_same_session_growth_is_the_difference_of_the_two_contexts_on_split_records(self, world, capsys):
        open_a = _priced(_SONNET, input=100_000, branch=_BRANCH, ts=_ts(0), request_id="req-open",
                         content=[_skill_block("sk-a", "code-review")])
        open_b = _priced(_SONNET, input=100_000, output=200, branch=_BRANCH, ts=_ts(0), request_id="req-open", content=[])
        later_a = _priced(_SONNET, input=150_000, branch=_BRANCH, ts=_ts(5), request_id="req-later")
        later_b = _priced(_SONNET, input=150_000, output=50, branch=_BRANCH, ts=_ts(5), request_id="req-later")
        records = [open_a, open_b, _priced(_SONNET, input=1, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
                   _user_msg("next", branch=_BRANCH, ts=_ts(4)), later_a, later_b]
        _write_session(world, records)
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
            _user_msg("next task", branch=_BRANCH, ts=_ts(2)),
            _priced(_SONNET, input=7, cache_read=70_000, ephemeral_1h=40_000, ephemeral_5m=20_000, branch=_BRANCH, ts=_ts(3)),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        later_context = 7 + 70_000 + 40_000 + 20_000
        opening_context = 5 + 60_000 + 30_000 + 10_000
        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] == later_context - opening_context

    def test_a_loop_spanning_two_sessions_stores_null_growth(self, world, capsys):
        _write_session(world, _round(_BRANCH, "a", 0, marker=False), session_id="sess-1")
        _write_session(world, _round(_BRANCH, "b", 30, marker=True), session_id="sess-2")
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] is None

    def test_a_loop_with_no_turn_after_its_window_end_stores_null_growth(self, world, capsys):
        records = [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(0), content=[_skill_block("sk-a", "code-review")]),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "0 with growth defined" in outcome.out
        assert _stored_loops(world, _BRANCH)[0]["growth_tokens"] is None
        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {}

    def test_a_compaction_that_shrinks_context_stores_negative_growth(self, world, capsys):
        records = _clean_loop(_BRANCH, later_input=20_000)
        records.insert(3, _compact_boundary_rec())
        _write_session(world, records)
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
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {_SONNET: 1, "model-after-every-window": 1}

    def test_a_turn_after_a_second_loop_in_the_same_session_counts_for_both_loops(self, world, capsys):
        records = _round(_BRANCH, "a", 0, marker=True) + _round(_BRANCH, "b", 10, marker=True) + [
            _priced("model-after-both", input=1, branch=_BRANCH, ts=_ts(20)),
        ]
        _write_session(world, records)
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
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH)[0]["later_turns"] == {_SONNET: 1}

    def test_an_unpriced_later_turn_is_counted_under_its_model_and_leaves_degraded_false(self, world, capsys):
        records = _clean_loop(_BRANCH) + [_priced("model-the-table-lacks", input=1, branch=_BRANCH, ts=_ts(10))]
        _write_session(world, records)
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
        ]
        _write_session(world, records)
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
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert "degraded: yes" in outcome.out
        assert _stored_loops(world, _BRANCH)[0]["degraded"] is True

    def test_an_in_file_sidechain_record_after_the_loop_leaves_the_stored_loop_unchanged(self, world, capsys):
        project_dir = _write_session(world, _clean_loop(_BRANCH))
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        _run(capsys, world)
        baseline = _stored_loops(world, _BRANCH)
        (world.captures_dir / _digest_name(_BRANCH)).unlink()
        extra_records = _clean_loop(_BRANCH) + [_as_sidechain(_priced(_SONNET, input=900_000, branch=_BRANCH, ts=_ts(1)))]
        _write_jsonl(project_dir / "sess-1.jsonl", extra_records)

        _run(capsys, world)

        assert _stored_loops(world, _BRANCH) == baseline

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


# --- deny reconciliation -----------------------------------------------------


class TestDenyExtraction:
    @pytest.mark.parametrize("shape", ["current", "legacy"])
    def test_both_deny_shapes_are_extracted_and_deduped_by_tool_use_id(self, world, shape):
        scan = routing_guard._ScanFacts()
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0)),
            _routing_deny_record("deny-1", shape=shape, branch=_BRANCH, ts=_ts(1)),
            _routing_deny_record("deny-1", shape=shape, branch=_BRANCH, ts=_ts(2)),
            _routing_deny_record("deny-2", shape=shape, branch=_BRANCH, ts=_ts(3)),
        ]

        routing_guard._add_session_facts(scan, world.projects / "s.jsonl", records)

        assert [deny.branch for deny in scan.denies] == [_BRANCH, _BRANCH]

    def test_one_deny_recorded_in_both_shapes_is_counted_once(self, world):
        scan = routing_guard._ScanFacts()
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0)),
            _routing_deny_record("deny-1", shape="legacy", branch=_BRANCH, ts=_ts(1)),
            _routing_deny_record("deny-1", shape="current", branch=_BRANCH, ts=_ts(1)),
        ]

        routing_guard._add_session_facts(scan, world.projects / "s.jsonl", records)

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

    def test_a_deny_repeated_in_a_second_session_is_counted_once(self, world):
        scan = routing_guard._ScanFacts()
        records = [_asst(_SONNET, branch=_BRANCH, ts=_ts(0)), _routing_deny_record("deny-1", branch=_BRANCH, ts=_ts(1))]

        routing_guard._add_session_facts(scan, world.projects / "s1.jsonl", list(records))
        routing_guard._add_session_facts(scan, world.projects / "s2.jsonl", list(records))

        assert len(scan.denies) == 1

    def test_a_deny_already_counted_in_an_earlier_session_still_marks_this_sessions_denied_skill_open(self, world):
        scan = routing_guard._ScanFacts()
        second_session = world.projects / "s2.jsonl"

        routing_guard._add_session_facts(scan, world.projects / "s1.jsonl", _key_on_loop())
        routing_guard._add_session_facts(scan, second_session, _key_on_loop())

        skill_opens_of_second_session = [
            round_facts for round_facts in scan.rounds_by_branch[_BRANCH]
            if round_facts.session_key == str(second_session) and round_facts.open_idx == 0
        ]
        assert len(scan.denies) == 1
        assert [round_facts.denied_open for round_facts in skill_opens_of_second_session] == [True]

    def test_other_hooks_denials_and_sidechain_denials_are_ignored(self, world):
        scan = routing_guard._ScanFacts()
        records = [
            _asst(_SONNET, branch=_BRANCH, ts=_ts(0)),
            _hook_deny("require-code-review", branch=_BRANCH, ts=_ts(1)),
            _as_sidechain(_routing_deny_record("deny-1", branch=_BRANCH, ts=_ts(2))),
        ]

        routing_guard._add_session_facts(scan, world.projects / "s.jsonl", records)

        assert scan.denies == []


class TestReconcileDenies:
    ACCOUNT = Path("/placeholder/account-a")
    OTHER_ACCOUNT = Path("/placeholder/account-b")

    def _reconcile(self, denies, rows, *, built=(), branch_facts=None):
        return routing_guard._reconcile_denies(denies, rows, self.ACCOUNT, set(built), branch_facts or {})

    def test_a_deny_on_an_off_row_refuses_naming_that_rows_line(self):
        rows = [_note_row(7, self.ACCOUNT, "off-branch", "off")]

        result = self._reconcile([routing_guard._DenyFact("off-branch", 1.0)], rows)

        assert len(result.refusals) == 1
        assert "note line 7" in result.refusals[0]
        assert "off-branch" not in result.refusals[0]

    def test_a_deny_on_an_unlisted_branch_refuses_with_its_utc_minute_and_not_its_name(self):
        unlisted_ts = datetime(2026, 8, 1, 10, 1, tzinfo=UTC).timestamp()

        result = self._reconcile([routing_guard._DenyFact("unlisted-branch", unlisted_ts)], [])

        assert len(result.refusals) == 1
        assert "2026-08-01 10:01 UTC" in result.refusals[0]
        assert "unlisted-branch" not in result.refusals[0]

    @pytest.mark.parametrize("arm", ["on", "unmerged"])
    def test_a_deny_on_an_on_or_unmerged_row_passes(self, arm):
        rows = [_note_row(1, self.ACCOUNT, "listed", arm)]

        result = self._reconcile([routing_guard._DenyFact("listed", 1.0)], rows, built={1})

        assert result == routing_guard._DenyReconciliation([], [])

    def test_a_deny_on_an_empty_or_default_branch_prints_a_notice_and_does_not_refuse(self):
        denies = [routing_guard._DenyFact("", 1.0), routing_guard._DenyFact("main", 2.0)]

        result = self._reconcile(denies, [])

        assert result.refusals == []
        assert len(result.notices) == 1
        assert "empty or default-branch" in result.notices[0]

    def test_an_on_row_this_run_builds_with_no_deny_prints_a_notice_naming_its_line(self):
        rows = [_note_row(4, self.ACCOUNT, "no-deny-branch", "on")]

        result = self._reconcile([], rows, built={4})

        assert [("note line 4" in notice) for notice in result.notices] == [True]

    def test_an_on_row_this_run_does_not_build_prints_no_denyless_notice(self):
        rows = [_note_row(4, self.ACCOUNT, "no-deny-branch", "on")]

        assert self._reconcile([], rows, built=()).notices == []

    def test_an_off_row_this_run_builds_with_a_record_later_than_the_first_deny_prints_a_notice(self):
        rows = [_note_row(5, self.ACCOUNT, "off-branch", "off"), _note_row(6, self.ACCOUNT, "on-branch", "on")]
        denies = [routing_guard._DenyFact("on-branch", 100.0)]
        facts = {"off-branch": routing_guard._BranchFacts(True, 200.0)}

        result = self._reconcile(denies, rows, built={5, 6}, branch_facts=facts)

        assert result.refusals == []
        assert any("note line 5" in notice and "turned off again" in notice for notice in result.notices)

    def test_an_off_row_with_records_before_the_first_deny_prints_no_notice(self):
        rows = [_note_row(5, self.ACCOUNT, "off-branch", "off"), _note_row(6, self.ACCOUNT, "on-branch", "on")]
        denies = [routing_guard._DenyFact("on-branch", 300.0)]
        facts = {"off-branch": routing_guard._BranchFacts(True, 200.0)}

        result = self._reconcile(denies, rows, built={5, 6}, branch_facts=facts)

        assert result.notices == []

    def test_an_off_row_this_run_does_not_build_prints_no_later_record_notice(self):
        rows = [_note_row(5, self.ACCOUNT, "off-branch", "off"), _note_row(6, self.ACCOUNT, "on-branch", "on")]
        denies = [routing_guard._DenyFact("on-branch", 100.0)]
        facts = {"off-branch": routing_guard._BranchFacts(True, 200.0)}

        result = self._reconcile(denies, rows, built={6}, branch_facts=facts)

        assert result.notices == []

    def test_a_branch_listed_for_another_config_dir_with_records_here_prints_a_notice_and_never_refuses(self):
        rows = [_note_row(9, self.OTHER_ACCOUNT, "split-branch", "off")]
        facts = {"split-branch": routing_guard._BranchFacts(True, 1.0)}

        result = self._reconcile([routing_guard._DenyFact("split-branch", 1.0)], rows, branch_facts=facts)

        assert result.refusals == []
        assert any("note line 9" in notice and "split across accounts" in notice for notice in result.notices)

    def test_end_to_end_an_unlisted_branch_deny_refuses_and_publishes_nothing(self, world, capsys):
        records = _clean_loop(_BRANCH) + [
            _asst(_SONNET, branch="unlisted-work", ts=_ts(30)),
            _routing_deny_record("deny-u", branch="unlisted-work", ts=_ts(31)),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 2
        assert "2026-08-01 10:31 UTC" in outcome.err
        assert (
            "falls on a branch the note does not list; run review-trace --this-repo --deny-only, match the row at that"
            " minute, and list its branch as on if it merged, else as unmerged"
        ) in outcome.err
        assert "--deny-summary" not in outcome.err
        assert "unlisted-work" not in outcome.err + outcome.out
        assert _published_files(world) == []
        assert not world.captures_dir.exists()


# --- memory ------------------------------------------------------------------


class _TrackedRecord(dict):
    """A weakref-able record."""


class _TrackedRecordList(list):
    """A weakref-able session record list."""


class TestSessionRecordsAreDroppedPerSession:
    def test_a_capture_keeps_no_session_alive_past_the_next_session(self, world, capsys, monkeypatch):
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
                records = _TrackedRecordList(
                    _TrackedRecord(rec) for rec in _clean_loop(_BRANCH, start=k * 10, tag=str(k))
                )
                live_lists.append(weakref.ref(records))
                live_records.append([weakref.ref(rec) for rec in records])
                yield project_dir / f"session-{k}.jsonl", records

        monkeypatch.setattr(scope, "_iter_scoped_sessions", sessions)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert survivors == []
        assert len(live_lists) == session_count
        assert "captured (5 rounds, 5 loops" in outcome.out


# --- scope -------------------------------------------------------------------


def _add_linked_worktree(world: _World, branch: str) -> Path:
    worktree = world.repo / ".claude" / "worktrees" / branch
    subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(worktree)], cwd=world.repo, check=True)
    return worktree.resolve()


class TestScope:
    def test_an_orphaned_worktree_directory_is_read_and_a_sibling_fork_directory_is_not(self, world, capsys):
        orphan_slug = world.main_slug + _WORKTREE_INFIX + "removed-worktree"
        _write_session(world, _clean_loop("branch-orphan", tag="o"), slug=orphan_slug, session_id="sess-o")
        _write_session(world, _clean_loop("branch-fork", tag="f"), slug=world.main_slug + "-fork", session_id="sess-f")
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
        _write_session(world, _clean_loop(), slug=worktree_slug)
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


# --- the store ---------------------------------------------------------------


_BETA = "feat-beta"


def _two_branch_sessions(world: _World) -> None:
    _write_session(world, _clean_loop(_BRANCH, tag="a"), session_id="sess-a")
    _write_session(world, _clean_loop(_BETA, tag="b"), session_id="sess-b")


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
        _write_session(world, _clean_loop())
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
    def test_a_second_run_reports_each_row_already_captured_and_leaves_every_file_untouched(self, world, capsys):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA, "off"))
        first = _run(capsys, world)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in _published_files(world)}

        second = _run(capsys, world)

        assert first.code == second.code == 0
        assert second.out.count("already captured") == 2
        assert "captured (" not in second.out.replace("already captured (", "")
        assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in _published_files(world)} == before

    def test_a_valid_standing_file_for_a_branch_with_no_transcripts_is_reported_and_never_rebuilt(
        self, world, capsys, monkeypatch,
    ):
        standing = _plant_standing_file(world, _BRANCH, _valid_capture_text())
        before = (standing.read_bytes(), standing.stat().st_mtime_ns)
        build_calls: list[int] = []
        real_build_row = routing_guard._build_row
        monkeypatch.setattr(routing_guard, "_build_row", lambda rounds: build_calls.append(1) or real_build_row(rounds))
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert "already captured" in outcome.out
        assert (standing.read_bytes(), standing.stat().st_mtime_ns) == before
        assert build_calls == []

    @pytest.mark.parametrize("revision", [0, 2])
    def test_a_standing_file_at_another_revision_is_neither_opened_nor_reported(self, world, capsys, monkeypatch, revision):
        _write_session(world, _clean_loop())
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
        assert "invalid" not in outcome.out

    def test_a_run_with_one_whole_and_one_missing_row_publishes_the_whole_one_prints_both_and_exits_3(self, world, capsys):
        _write_session(world, _clean_loop(_BRANCH))
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
        _write_session(world, _clean_loop(_BETA, tag="b"))
        world.captures_dir.mkdir(mode=0o700)
        standing = world.captures_dir / _digest_name(_BRANCH)
        if fault == "fails-validator":
            standing.write_text('{"loops": []}')
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

    def test_a_planted_orphaned_temp_file_is_never_opened_and_stays(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop())
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
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        winner_text = _valid_capture_text(growth_tokens=None, degraded=True)
        winner_path = world.captures_dir / _digest_name(_BRANCH)

        def link_losing_the_race(source, destination, *args, **kwargs):
            Path(destination).write_text(winner_text)
            raise FileExistsError(errno.EEXIST, "File exists")

        monkeypatch.setattr(os, "link", link_losing_the_race)

        outcome = _run(capsys, world)

        assert outcome.code == 0
        assert "already captured (1 loops, 0 with growth defined, degraded: yes)" in outcome.out
        assert winner_path.read_text() == winner_text
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
        _write_session(world, _clean_loop())
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
        _write_session(world, _clean_loop())
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
        _write_session(world, _clean_loop())
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
        assert "published 1 files" in outcome.err
        assert "/sentinel/path" not in outcome.err + outcome.out
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]
        assert list(world.captures_dir.glob(".routing-guard-*")) == []

    def test_a_failed_link_after_one_published_file_exits_4_with_the_link_message_and_the_published_count(
        self, world, capsys, monkeypatch,
    ):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        real_link = os.link
        link_calls: list[int] = []

        def link_failing_on_the_second_publish(source, destination, *args, **kwargs):
            link_calls.append(1)
            if len(link_calls) == 2:
                raise OSError(errno.EXDEV, "sentinel-strerror", "/sentinel/path")
            return real_link(source, destination, *args, **kwargs)

        monkeypatch.setattr(os, "link", link_failing_on_the_second_publish)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "could not be linked into place" in outcome.err
        assert "published 1 files" in outcome.err
        assert "/sentinel/path" not in outcome.err + outcome.out
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]
        assert list(world.captures_dir.glob(".routing-guard-*")) == []

    def test_a_directory_creation_failure_exits_4_printing_only_the_strerror(self, world, capsys, monkeypatch):
        _write_session(world, _clean_loop())
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
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        monkeypatch.setattr(routing_guard, "_build_row", lambda rounds: routing_guard._BuiltRow("{}", []))

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "failed validation; this run published 0 files" in outcome.err
        assert not world.captures_dir.exists()

    def test_a_built_row_the_validator_rejects_after_one_published_row_reports_that_one_file(
        self, world, capsys, monkeypatch,
    ):
        _two_branch_sessions(world)
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(world.config_dir, _BETA))
        real_build_row = routing_guard._build_row
        build_calls: list[int] = []

        def build_row_failing_validation_on_the_second_row(rounds):
            build_calls.append(1)
            return real_build_row(rounds) if len(build_calls) == 1 else routing_guard._BuiltRow("{}", [])

        monkeypatch.setattr(routing_guard, "_build_row", build_row_failing_validation_on_the_second_row)

        outcome = _run(capsys, world)

        assert outcome.code == 4
        assert "failed validation; this run published 1 files" in outcome.err
        assert [p.name for p in _published_files(world)] == [_digest_name(_BRANCH)]

    def test_an_unexpected_exception_exits_4_with_the_fixed_message_and_no_sentinel_or_traceback(
        self, world, capsys, monkeypatch,
    ):
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def explode(_rounds):
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


def _loop_json(**overrides) -> bytes:
    loop = {
        "overhead_tokens": {_SONNET: {"standard": {"input": 5}}},
        "growth_tokens": 1, "later_turns": {_SONNET: 1}, "degraded": False,
    }
    loop.update(overrides)
    return json.dumps({"loops": [loop]}).encode("utf-8")


_INVALID_CAPTURES = [
    ("not-utf8", b"\xff\xfe"),
    ("invalid-json", b"{"),
    ("non-object-root", b"[]"),
    ("empty-loops", b'{"loops": []}'),
    ("loops-not-a-list", b'{"loops": {}}'),
    ("loop-not-an-object", b'{"loops": [1]}'),
    ("missing-loop-key", json.dumps({"loops": [{"overhead_tokens": {}, "growth_tokens": 1, "later_turns": {}}]}).encode()),
    ("extra-loop-key", _loop_json(extra=1)),
    ("boolean-growth", _loop_json(growth_tokens=True)),
    ("float-growth", _loop_json(growth_tokens=1.5)),
    ("string-growth", _loop_json(growth_tokens="1")),
    ("boolean-token-count", _loop_json(overhead_tokens={_SONNET: {"standard": {"input": True}}})),
    ("float-token-count", _loop_json(overhead_tokens={_SONNET: {"standard": {"input": 2.0}}})),
    ("negative-token-count", _loop_json(overhead_tokens={_SONNET: {"standard": {"input": -1}}})),
    ("negative-turn-count", _loop_json(later_turns={_SONNET: -1})),
    ("float-turn-count", _loop_json(later_turns={_SONNET: 1.0})),
    ("non-boolean-degraded", _loop_json(degraded=1)),
    ("unknown-variant", _loop_json(overhead_tokens={_SONNET: {"sentinel-variant": {"input": 1}}})),
    ("unknown-class", _loop_json(overhead_tokens={_SONNET: {"standard": {"sentinel-class": 1}}})),
    ("model-entry-not-an-object", _loop_json(overhead_tokens={_SONNET: 1})),
    ("overhead-not-an-object", _loop_json(overhead_tokens=[])),
    ("integer-past-the-digit-limit", b'{"loops": [{"overhead_tokens": {}, "growth_tokens": ' + b"9" * 5000
     + b', "later_turns": {}, "degraded": false}]}'),
    ("nesting-past-the-recursion-limit", b"[" * 500_000),
]


_VALID_LOOP_TEXT = json.dumps({
    "overhead_tokens": {_SONNET: {"standard": {"input": 5}}},
    "growth_tokens": 1, "later_turns": {_SONNET: 1}, "degraded": False,
})
_VALID_FILE_TEXT = f'{{"loops": [{_VALID_LOOP_TEXT}]}}'
_REASON_NOT_VALID_JSON = "not valid JSON"
_REASON_NOT_ONLY_LOOPS = "the file is not an object holding only loops"
# Each case differs from the valid file in one place, so only the guard it names can refuse it.
_GUARD_CASES = [
    (
        "duplicate-loops-key-holding-two-valid-lists",
        f'{{"loops": [{_VALID_LOOP_TEXT}], "loops": [{_VALID_LOOP_TEXT}]}}', _REASON_NOT_VALID_JSON,
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
    ("extra-root-key-beside-valid-loops", f'{{"loops": [{_VALID_LOOP_TEXT}], "extra": 1}}', _REASON_NOT_ONLY_LOOPS),
]


class TestValidator:
    def test_the_valid_file_the_guard_cases_are_built_from_is_accepted(self):
        loops = routing_guard._parse_capture_bytes(_VALID_FILE_TEXT.encode("utf-8"))

        assert [loop["growth_tokens"] for loop in loops] == [1]

    @pytest.mark.parametrize(("case_id", "text", "reason"), _GUARD_CASES, ids=[case[0] for case in _GUARD_CASES])
    def test_an_otherwise_valid_file_is_refused_for_the_one_guard_it_trips(self, case_id, text, reason):
        assert text != _VALID_FILE_TEXT

        with pytest.raises(routing_guard._InvalidCapture) as refusal:
            routing_guard._parse_capture_bytes(text.encode("utf-8"))

        assert refusal.value.reason == reason

    @pytest.mark.parametrize(("case_id", "data"), _INVALID_CAPTURES, ids=[case_id for case_id, _data in _INVALID_CAPTURES])
    def test_refuses_each_malformed_file_with_a_fixed_reason_that_echoes_no_cell(self, case_id, data):
        with pytest.raises(routing_guard._InvalidCapture) as refusal:
            routing_guard._parse_capture_bytes(data)

        assert "sentinel" not in refusal.value.reason

    def test_accepts_a_hand_written_literal_file_independent_of_the_writer(self):
        literal = (
            b'{"loops": [{"overhead_tokens": {"claude-sonnet-5": {"fast": {"input": 5, "cache_write_1h": 2}}},'
            b' "growth_tokens": -40, "later_turns": {"claude-sonnet-5": 0, "": 3}, "degraded": true},'
            b' {"overhead_tokens": {}, "growth_tokens": null, "later_turns": {}, "degraded": false}]}'
        )

        loops = routing_guard._parse_capture_bytes(literal)

        assert [loop["growth_tokens"] for loop in loops] == [-40, None]
        assert loops[0]["overhead_tokens"]["claude-sonnet-5"]["fast"]["cache_write_1h"] == 2

    def test_a_row_built_by_the_writer_passes_the_validator(self):
        round_facts = _round_facts(
            has_marker_write=True, overhead_tokens={_SONNET: {"fast_us_geo": {"input": 3, "cache_write_1h": 4}}},
            later_turns={_SONNET: 2},
        )

        built = routing_guard._build_row([round_facts])

        assert routing_guard._parse_capture_bytes(built.text.encode("utf-8")) == [{
            "overhead_tokens": {_SONNET: {"fast_us_geo": {"input": 3, "cache_write_1h": 4}}},
            "growth_tokens": 30, "later_turns": {_SONNET: 2}, "degraded": False,
        }]


# --- the per-row lines -------------------------------------------------------


class TestRowLines:
    def test_the_captured_line_of_a_key_on_row_with_a_null_growth_loop_equals_a_literal(self, world, capsys):
        project_dir = _write_session(world, _key_on_loop())
        _write_marker_writing_orchestrator(project_dir)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert (
            "routing-guard: note line 1: captured (2 rounds, 1 loops, 0 with growth defined,"
            " 1 denied round-opens joined, degraded: no)"
        ) in outcome.out.splitlines()

    def test_a_dangling_dispatch_reads_degraded_yes_on_the_captured_line(self, world, capsys):
        records = [
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(0),
                    content=[_skill_block("sk-a", "code-review"), _agent_use("no-such-dispatch", "staff-sdet")]),
            _priced(_SONNET, input=1_000, branch=_BRANCH, ts=_ts(1), content=[_marker_write_use("mk-a")]),
        ]
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert (
            "routing-guard: note line 1: captured (1 rounds, 1 loops, 0 with growth defined,"
            " 0 denied round-opens joined, degraded: yes)"
        ) in outcome.out.splitlines()

    def test_the_already_captured_line_reads_its_counts_from_the_validated_file(self, world, capsys):
        two_loops = json.dumps({"loops": [
            {"overhead_tokens": {}, "growth_tokens": None, "later_turns": {}, "degraded": False},
            {"overhead_tokens": {}, "growth_tokens": 5, "later_turns": {}, "degraded": True},
        ]})
        _plant_standing_file(world, _BRANCH, two_loops)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        expected_line = "routing-guard: note line 1: already captured (2 loops, 1 with growth defined, degraded: yes)"
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
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
        monkeypatch.setattr(pricing, "_format_drift_detected", lambda: drift_detected)

        outcome = _run(capsys, world)

        assert ("PRICING INTEGRITY" in outcome.out) == drift_detected
        integrity_lines = [line for line in outcome.out.splitlines() if "PRICING INTEGRITY" in line]
        assert not any(ch.isdigit() for line in integrity_lines for ch in line)


# --- the golden file ---------------------------------------------------------


class TestGoldenFile:
    def test_a_capture_with_a_cached_reviewer_dispatch_publishes_the_literal_name_and_text(self, world, capsys):
        records = _clean_loop()
        # The later turn's context is the sum of its four token classes, so the literal growth below fails on a
        # context read from input_tokens alone.
        records[3] = _priced(
            _SONNET, input=10_000, cache_read=100_000, ephemeral_1h=15_000, ephemeral_5m=5_000, branch=_BRANCH, ts=_ts(3),
        )
        records[0]["message"]["content"].append(_agent_use("rev-1", "staff-sdet"))
        project_dir = _write_session(world, records)
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
            " otherwise update this literal, because a bump leaves every earlier file unpoolable."
        )
        assert published[0].read_text() == (
            '{"loops": [{"degraded": false, "growth_tokens": 30000, "later_turns": {"claude-sonnet-5": 1},'
            ' "overhead_tokens": {"claude-sonnet-5": {"standard": {"cache_read": 40000, "cache_write_1h": 3000,'
            ' "cache_write_5m": 0, "input": 112000, "output": 600}}}}]}'
        ), (
            "The stored text changed. Bump _CAPTURE_REVISION only when the change alters what a stored row means;"
            " otherwise update this literal, because a bump leaves every earlier file unpoolable."
        )


# --- criterion 3: the reads a capture makes ----------------------------------


class TestCriterionThreeInCaptureMode:
    @staticmethod
    def _is_allowed(path: str, world: _World) -> bool:
        resolved = Path(path)
        if path in (str(world.note), str(world.config_dir / "claude-config.toml"), str(_config._SCHEMA_FILE)):
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
        _write_jsonl(decoy_project / "decoy-session.jsonl", _clean_loop() + [_routing_deny_record("decoy-deny", branch=_BRANCH)])
        roots_file = tmp_path / "declared-roots"
        roots_file.write_text(f"{decoy}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        _write_session(world, _clean_loop())
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
        assert recorder.executables() == {"git"}

    def test_a_note_listing_two_accounts_publishes_nothing_into_the_other_one(self, world, capsys, tmp_path):
        other_account = tmp_path / "acct-b"
        (other_account / "projects").mkdir(parents=True)
        _enable_pr_cost(other_account)
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH), _note_line(other_account, "branch-on-b"))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert not (other_account / "routing-guard-captures").exists()


class TestSubprocessRecorder(_RecorderPositiveControls):
    """The one collected copy of the recorder's positive controls, which the compute tests rely on too."""


# --- free text and request ids -----------------------------------------------


class TestFreeText:
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
        project_dir = _write_session(world, records)
        orchestrator_records = [_as_sidechain(_priced(
            _SONNET, input=5_000, branch=_BRANCH, ts=_ts(2),
            content=[{"type": "text", "text": _leak_sentinel("orchestrator-text")}, _marker_write_use("mk-o")],
        ))]
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
        _write_session(world, records)
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        outcome = _run(capsys, world)

        assert outcome.code == 0, outcome.err
        assert request_id in outcome.err
        assert request_id not in outcome.out
        assert request_id not in (world.captures_dir / _digest_name(_BRANCH)).read_text()


# --- output ------------------------------------------------------------------


# Capture's own computed values: a locator, the progress count, a row's counts, the published count and a deny's UTC minute.
_PERMITTED_COMPUTED_VALUES = re.compile("|".join([
    r"note line \d+",
    r"scanning project directory \d+ of \d+",
    r"captured \(\d+ rounds, \d+ loops, \d+ with growth defined, \d+ denied round-opens joined, degraded: (?:yes|no)\)",
    r"already captured \(\d+ loops, \d+ with growth defined, degraded: (?:yes|no)\)",
    r"published \d+ files",
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC",
]))
_CAPTURED_LINE = (
    "routing-guard: note line 3: captured (2 rounds, 1 loops, 0 with growth defined, 1 denied round-opens joined,"
    " degraded: no)"
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
    ("no-deny-notice", 0, "listed on but no routing deny was found"),
    ("split-branch-notice", 3, "split across accounts"),
    ("unlisted-deny-refusal", 2, "falls on a branch the note does not list"),
    ("scan-gap", 2, "Nothing was published"),
    ("publish-failure", 4, "published 1 files"),
    ("not-opted-in-refusal", 1, "not opted in"),
    ("unlisted-account-refusal", 2, "is not listed in the note"),
]


class TestOutput:
    def test_the_banner_opens_capture_output(self, world, capsys):
        outcome = _capture_one_branch(world, capsys)

        assert outcome.out.startswith(routing_guard._DO_NOT_PUBLISH_BANNER)
        assert not any(ch.isdigit() for ch in routing_guard._DO_NOT_PUBLISH_BANNER)

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
        if scenario == "missing":
            _write_note(world, _note_line(world.config_dir, "branch-without-transcripts"))
        elif scenario == "invalid":
            _plant_standing_file(world, _BRANCH, "not json")
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "split-branch-notice":
            other_account = tmp_path / "acct-b"
            _write_session(world, _clean_loop(_BRANCH))
            _write_note(world, _note_line(world.config_dir, _BETA), _note_line(other_account, _BRANCH))
        elif scenario == "unlisted-deny-refusal":
            _write_session(world, _clean_loop(_BRANCH) + [
                _asst(_SONNET, branch="unlisted-work", ts=_ts(30)),
                _routing_deny_record("deny-u", branch="unlisted-work", ts=_ts(31)),
            ])
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
            (world.config_dir / ".pr-cost-enabled").unlink()
            _write_note(world, _note_line(world.config_dir, _BRANCH))
        elif scenario == "unlisted-account-refusal":
            _write_note(world, _note_line(world.config_dir, _BRANCH))
            overrides["config_dir"] = str(tmp_path / "acct-elsewhere")
        else:
            _write_session(world, _clean_loop(_BRANCH))
            _write_note(world, _note_line(world.config_dir, _BRANCH))
            if scenario == "already-captured":
                _run(capsys, world)

        outcome = _run(capsys, world, **overrides)

        assert outcome.code == expected_code, outcome.err
        assert expected_fragment in outcome.out + outcome.err
        assert _lines_with_values_outside_the_permitted_shapes(outcome) == []

    def test_the_shared_output_check_fails_a_run_that_prints_a_branch_read_from_the_note(self, world, capsys, monkeypatch):
        _write_note(world, _note_line(world.config_dir, _BRANCH))

        def printing_the_branch(_args):
            print(f"leaked {_BRANCH}", file=sys.stderr)
            sys.exit(0)

        monkeypatch.setattr(routing_guard, "cmd_routing_guard", printing_the_branch)

        with pytest.raises(AssertionError, match="names 1 string"):
            _run(capsys, world)

    def test_no_line_names_a_branch_a_slug_or_a_path_even_with_a_distinctive_worktree_name(self, world, capsys, monkeypatch):
        sentinel_branch = "sentinel-worktree-branch-xyz"
        worktree = _add_linked_worktree(world, sentinel_branch)
        monkeypatch.chdir(worktree)
        worktree_slug = scope._path_to_project_slug(str(worktree))
        _write_session(world, _clean_loop(sentinel_branch), slug=worktree_slug)
        _write_session(world, _clean_loop(_BETA, tag="b"), session_id="sess-b")
        _write_note(world, _note_line(world.config_dir, sentinel_branch), _note_line(world.config_dir, _BETA, "off"))

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
        _write_session(world, _clean_loop())
        _write_note(world, _note_line(world.config_dir, _BRANCH))
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
