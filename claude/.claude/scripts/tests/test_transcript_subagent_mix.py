"""Tests for transcript_analysis/subagent_mix.py's cmd_subagent_mix, minus its dollar columns."""
import importlib.util
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._subagent_helpers import (
    _subagent_mix_args,
    _sum_column_across_rows,
)
from .conftest import (
    _agent_use,
    _asst,
    _skill_use,
    _table_cols,
    _write_jsonl,
    _write_subagent_dispatch,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _column_values_for_matching_rows(
    out: str, *, header_contains: str, label: str, row_prefix: str
) -> list[str]:
    """Sibling to _sum_column_across_rows for a caller that needs each
    matched row's own column value -- e.g. asserting several rows stayed
    distinct rather than merging into a sum. Same header-token-anchored
    column lookup, not a bare line.split()[N] index."""
    lines = out.splitlines()
    headers = [ln for ln in lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header_idx = lines.index(headers[0])
    col_idx = headers[0].split().index(label)
    values = []
    for ln in lines[header_idx + 1:]:
        if ln == "":
            break
        if ln.startswith(row_prefix):
            values.append(ln.split()[col_idx])
    assert values, f"no rows starting with {row_prefix!r} found under header {header_contains!r}"
    return values


class TestSubagentMix:
    def test_counts_agent_spawns_by_subagent_type(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[
                _agent_use("a1", "staff-backend-engineer"),
                _agent_use("a2", "ciso-reviewer"),
                _agent_use("a3", "staff-backend-engineer"),
            ]),
        ])
        args = _subagent_mix_args()
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "feat" in out
        assert "staff-backend-engineer(2)" in out
        assert "ciso-reviewer(1)" in out

    def test_counts_review_skill_invocations(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[
                _skill_use("s1", "code-review"),
                _skill_use("s2", "code-review"),
                _skill_use("s3", "plan-review"),
                _skill_use("s4", "ready-for-review"),
                _skill_use("s5", "respond-pr"),  # excluded — not in REVIEW_SKILLS
            ]),
        ])
        args = _subagent_mix_args()
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # CR=2, PR=1, RR=1; max_labels=6 excludes the trailing multi-word "Top subagent types" column
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=6)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"
        assert cols["PR"] == "1"
        assert cols["RR"] == "1"

    def test_legacy_task_tool_name_also_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="legacy", content=[
                _agent_use("t1", "staff-frontend-engineer", tool_name="Task"),
            ]),
        ])
        args = _subagent_mix_args()
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "staff-frontend-engineer(1)" in out

    def test_sidechain_spawns_not_counted(self, fake_projects, capsys):
        """Subagent-issued Agent calls (which appear on sidechain) must not double-count parent spawns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-backend-engineer")]),
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, content=[
                _agent_use("a2", "ciso-reviewer"),  # excluded — sidechain
            ]),
        ])
        args = _subagent_mix_args()
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "staff-backend-engineer(1)" in out
        assert "ciso-reviewer" not in out

    def test_branch_filter(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a", content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", branch="feat-b", content=[_agent_use("a2", "staff-backend-engineer")]),
        ])
        args = _subagent_mix_args(branches="feat-a")
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        assert "feat-b" not in out
        assert "staff-backend-engineer" not in out

    def test_per_session_splits_aggregate(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "abcd1234-aaaa.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_jsonl(fake_projects / "efgh5678-bbbb.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a2", "staff-backend-engineer")]),
        ])
        args = _subagent_mix_args(per_session=True)
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # Both sessions should appear with stem prefixes; aggregate "feat" alone should not be present as a row.
        assert "abcd1234" in out
        assert "efgh5678" in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = _subagent_mix_args()
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_single_root_output_strips_control_characters_from_branch_and_subagent_type(
        self, fake_projects, capsys
    ):
        """gitBranch and subagent_type are both transcript-sourced, not
        validated, before this table prints them -- same invariant as
        cmd_subagents' single-root branch sanitization, extended here to
        subagent_type since this table has its own second raw-value column."""
        branch_payload = "\x1b]0;PWNED-BRANCH\x07"
        stype_payload = "\x1b[31mPWNED-TYPE\x1b[0m"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch=branch_payload, content=[_agent_use("a1", stype_payload)]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        assert "]0;PWNED-BRANCH" in out
        assert "[31mPWNED-TYPE[0m(1)" in out
        assert "\x1b" not in out
        assert "\x07" not in out

    def test_control_byte_differing_branches_do_not_merge_into_one_row(self, fake_projects, capsys):
        """Two raw gitBranch values that differ only in a stripped control
        byte sanitize to the same display label but must stay distinct
        rows -- aggregating on the sanitized label instead of the raw value
        would silently sum their session/spawn counts into one row."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _asst("claude-opus-4-7", branch="feat\x01", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("b1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        sess_values = _column_values_for_matching_rows(
            out, header_contains="Sess", label="Sess", row_prefix="feat "
        )
        assert sess_values == ["1", "1"], f"expected two distinct 'feat' rows, each Sess=1: {sess_values}"


def _write_agent_frontmatter(config_dir_path: Path, agent_type: str, model: str) -> None:
    """Write a minimal on-disk agent file with a `model:` frontmatter pin,
    at the path _declared_pin reads: <config_dir>/agents/<agent_type>.md."""
    agents_dir = config_dir_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_type}.md").write_text(f"---\nmodel: {model}\nname: {agent_type}\n---\nbody\n")


class TestSubagentMixModelMix:
    """cmd_subagent_mix's second table: one test per column
    (Runs/Dangling/Declared/Requested/Observed) in cmd_subagent_mix's own
    docstring."""

    def test_declared_pin_violation_reports_opus_fraction_of_runs(self, fake_projects, tmp_path, capsys):
        """3 staff-sdet dispatches, declared pin sonnet: 2 observed opus (a
        pin violation each), 1 observed sonnet — Runs=3, Observed shows
        opus(2) and sonnet(1)."""
        _write_agent_frontmatter(tmp_path, "staff-sdet", "sonnet")
        session_id = "sess-mix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-sdet"),
                _agent_use("a2", "staff-sdet"),
                _agent_use("a3", "staff-sdet"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("claude-opus-4-7", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_asst("claude-opus-4-7", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-3", "a3",
            [_asst("claude-sonnet-4-6", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Runs", row_contains="staff-sdet", max_labels=4)
        assert cols["Runs"] == "3"
        assert cols["Declared"] == "sonnet"
        assert "opus(2)" in out
        assert "sonnet(1)" in out

    def test_mixed_sidechain_reports_literal_mixed_bucket(self, fake_projects, capsys):
        """Two distinct real model IDs within one dispatch's own sidechain
        report the literal "mixed" bucket, never collapsed to one family."""
        session_id = "sess-mixed"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [
                _asst("claude-opus-4-7", branch="main", sidechain=True),
                _asst("claude-sonnet-4-6", branch="main", sidechain=True),
            ],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        assert "mixed(1)" in out

    def test_synthetic_only_sidechain_lands_in_other_not_a_pin_violation(self, fake_projects, tmp_path, capsys):
        """A sidechain whose only recorded model is the literal "<synthetic>"
        resolves to the "other" bucket via _fam, distinct from any real
        model family — never miscounted as an opus (or any) pin violation."""
        _write_agent_frontmatter(tmp_path, "staff-sdet", "sonnet")
        session_id = "sess-synthetic"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("<synthetic>", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        assert "other(1)" in out
        assert "opus(1)" not in out

    def test_dangling_jsonl_excluded_from_runs_denominator(self, fake_projects, capsys):
        """A meta.json with no readable sibling .jsonl is a dangling dispatch:
        excluded from Runs, counted under Dangling instead."""
        session_id = "sess-dangling"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        meta = {"agentType": "staff-sdet", "description": "d", "toolUseId": "a1", "spawnDepth": 1}
        (subdir / "agent-1.meta.json").write_text(json.dumps(meta))
        # Deliberately no agent-1.jsonl written — the dangling case.
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Runs", row_contains="staff-sdet", max_labels=3)
        assert cols["Runs"] == "0"
        assert cols["Dangling"] == "1"

    def test_requested_model_present_vs_absent_in_meta(self, fake_projects, capsys):
        """meta.json's own "model" key drives the Requested column: present
        buckets by its value, absent buckets under _UNREQUESTED_MODEL_LABEL."""
        session_id = "sess-requested"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-sdet"),
                _agent_use("a2", "staff-sdet"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("claude-sonnet-4-6", branch="main", sidechain=True)],
            agent_type="staff-sdet", requested_model="sonnet",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_asst("claude-sonnet-4-6", branch="main", sidechain=True)],
            agent_type="staff-sdet",  # no requested_model -> key absent
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        assert "sonnet(1)" in out
        assert f"{_mod.subagent_mix._UNREQUESTED_MODEL_LABEL}(1)" in out

    def test_undefined_agent_type_renders_built_in_declared_pin(self, fake_projects, capsys):
        """An agentType with no on-disk agent file (e.g. general-purpose)
        renders "built-in" in the Declared column, never a pin violation."""
        session_id = "sess-builtin"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "general-purpose")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("claude-opus-4-7", branch="main", sidechain=True)],
            agent_type="general-purpose",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Runs", row_contains="general-purpose", max_labels=4)
        assert cols["Declared"] == _mod.subagent_mix._DECLARED_PIN_BUILT_IN

    def test_requested_and_observed_columns_are_directionally_distinct(self, fake_projects, capsys):
        """Requested and Observed must land under their own header, not just
        appear somewhere in the output -- uses disjoint value domains
        (requested "haiku", observed "opus") so a column-transposition bug
        (Requested/Observed populated from the swapped dict) produces a
        value neither assertion could otherwise pass on, unlike a whole-
        output substring check."""
        session_id = "sess-directional"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("claude-opus-4-7", branch="main", sidechain=True)],
            agent_type="staff-sdet", requested_model="haiku",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        header_line = next(ln for ln in out.splitlines() if "Requested" in ln and "Observed" in ln)
        row_line = next(ln for ln in out.splitlines() if ln.startswith("staff-sdet"))
        requested_start, observed_start = header_line.index("Requested"), header_line.index("Observed")
        assert row_line[requested_start:observed_start].strip() == "haiku(1)"
        assert row_line[observed_start:].strip() == "opus(1)"

    def test_non_string_meta_model_does_not_crash_the_run(self, fake_projects, capsys):
        """A meta.json whose "model" key is a list (a corrupted file, or a
        future harness shape this repo doesn't control) must not raise
        TypeError: unhashable type when used as a Requested-column dict key
        -- the dispatch is excluded and counted under meta_read_errors
        instead, isolated the same way an invalid-JSON or missing-toolUseId
        meta.json already is, rather than aborting the entire subagent-mix
        run for every branch/session in scope."""
        session_id = "sess-badmodel"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        meta = {
            "agentType": "staff-sdet", "description": "d", "toolUseId": "a1",
            "model": ["opus"], "spawnDepth": 1,
        }
        (subdir / "agent-1.meta.json").write_text(json.dumps(meta))
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())  # must not raise TypeError
        out = capsys.readouterr().out
        assert "(1 meta.json files failed to parse, excluded)" in out

    def test_control_byte_differing_subagent_types_do_not_merge_model_mix_rows(
        self, fake_projects, capsys
    ):
        """Two raw subagent_type values that differ only in a stripped
        control byte sanitize to the same AgentType label but must stay
        distinct model-mix rows -- aggregating on the sanitized label
        instead of the raw value would silently sum their Runs and dollar
        figures into one row."""
        session_id = "sess-collide"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-sdet\x01"),
                _agent_use("a2", "staff-sdet"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("claude-opus-4-7", branch="main", sidechain=True)],
            agent_type="staff-sdet\x01",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_asst("claude-opus-4-7", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        runs_values = _column_values_for_matching_rows(
            out, header_contains="Runs", label="Runs", row_prefix="staff-sdet "
        )
        assert runs_values == ["1", "1"], f"expected two distinct 'staff-sdet' rows, each Runs=1: {runs_values}"


class TestDeclaredPinPathSafety:
    """_declared_pin builds a filesystem path from subagent_type -- data
    that, under --config-dir, can originate from a scanned foreign root's
    own transcript content, not just this process's own dispatches."""

    def test_traversal_agent_type_does_not_escape_agents_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        secret_file = tmp_path / "outside-agents-dir.md"
        secret_file.write_text("---\nmodel: SECRET-LEAKED-VALUE\n---\nbody\n")
        assert (
            _mod.subagent_mix._declared_pin("../outside-agents-dir", agents_dir, {})
            == _mod.subagent_mix._DECLARED_PIN_BUILT_IN
        )

    def test_absolute_path_agent_type_does_not_escape_agents_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        secret_file = tmp_path / "outside-agents-dir.md"
        secret_file.write_text("---\nmodel: SECRET-LEAKED-VALUE\n---\nbody\n")
        absolute_agent_type = str(secret_file.with_suffix(""))
        assert _mod.subagent_mix._declared_pin(absolute_agent_type, agents_dir, {}) == _mod.subagent_mix._DECLARED_PIN_BUILT_IN

    def test_ordinary_agent_type_name_is_unaffected(self, tmp_path):
        """The allowlist must not reject real subagent_type shapes (kebab-case
        identifiers, underscores) -- only a deny-path regression, not a
        false-positive rejection of legitimate names."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "staff-sdet.md").write_text("---\nmodel: sonnet\n---\nbody\n")
        assert _mod.subagent_mix._declared_pin("staff-sdet", agents_dir, {}) == "sonnet"


class TestSubagentMixSince:
    def test_since_excludes_dispatches_older_than_window(self, fake_projects, capsys):
        old_ts = "2020-01-01T00:00:00Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=old_ts, content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_malformed_since_exits_nonzero_naming_subagent_mix(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(since="not-a-window"))
        assert exc_info.value.code == 1
        assert "subagent-mix: --since" in capsys.readouterr().err

    def test_since_boundary_is_inclusive(self, fake_projects, capsys, monkeypatch):
        """A dispatch timestamped exactly at the since-window cutoff (now -
        1 day) is included, not excluded -- mirrors TestSubagentsSince's own
        boundary test; both subcommands share the identical filter
        conditional. time.time() is frozen so the record's timestamp and
        _parse_since_nd_arg's own cutoff are computed from the same instant."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        boundary_ts = datetime.fromtimestamp(fixed_now - 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=boundary_ts, content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(since="1d"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spawns", row_contains="main", max_labels=6)
        assert cols["Spawns"] == "1"

    def test_since_excludes_dispatches_missing_timestamp(self, fake_projects, capsys):
        rec = _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")])  # no ts=
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out


class TestSubagentMixMultiRoot:
    """Repeatable --config-dir on subagent-mix, and its disclosure controls."""

    @pytest.fixture
    def _isolated_staff_sdet_allowlist(self, tmp_path, monkeypatch):
        """Points _REPO_AGENT_DEFINITIONS_DIR at a throwaway git-tracked
        agents/ directory tracking staff-sdet.md, decoupling the two
        --this-repo subagent_type disclosure tests below from this repo's
        own real agents/ tree -- the same isolation TestRepoTrackedAgentTypeNames
        applies to its own unit tests, via monkeypatch + cache_clear()."""
        agents_dir = tmp_path / "isolated-agents"
        agents_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=agents_dir, check=True)
        (agents_dir / "staff-sdet.md").write_text("---\nname: x\n---\n")
        subprocess.run(["git", "add", "--", "staff-sdet.md"], cwd=agents_dir, check=True)
        monkeypatch.setattr(_mod.redaction, "_REPO_AGENT_DEFINITIONS_DIR", agents_dir)
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()
        yield
        _mod.redaction._repo_tracked_agent_type_names.cache_clear()

    def test_two_roots_yield_strictly_more_spawns_than_either_alone(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        single_root_out = capsys.readouterr().out
        single_root_cols = _table_cols(single_root_out, header_contains="Spawns", row_contains="feat", max_labels=6)
        assert single_root_cols["Spawns"] == "1"

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("b1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        multi_root_out = capsys.readouterr().out
        total_spawns = _sum_column_across_rows(
            multi_root_out, header_contains="Spawns", label="Spawns", row_prefix="account-"
        )
        assert total_spawns > int(single_root_cols["Spawns"])
        # Single-root label was flat ("feat"); two-root labels are namespaced.
        assert "account-1/branch-1" in multi_root_out
        assert "account-2/branch-1" in multi_root_out

    def test_colliding_branch_names_across_roots_get_distinct_redacted_labels(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """Two roots each with their own "main" branch must not collapse
        into one row, and neither raw branch name may appear in output."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("b1", "staff-backend-engineer")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "account-1/branch-1" in out
        assert "account-2/branch-1" in out
        assert "account-1/branch-1" != "account-2/branch-1"

    def test_per_session_refused_under_multi_root(self, fake_projects, fake_config_dir_factory, capsys):
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)], per_session=True))
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "--per-session" in err
        assert "--config-dir" in err

    def test_multi_root_stamps_do_not_publish_banner_on_stdout_and_stderr(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_single_root_omits_do_not_publish_banner(self, fake_projects, capsys):
        """The allow-path counterpart to the fire test above -- mirrors
        cost's own test_default_redact_omits_do_not_publish_banner. Without
        this, a broken/inverted multi_root guard (banner always fires, or
        never fires) has no test signal in either direction."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.err

    def test_subagent_type_redacted_under_multi_root_in_both_tables(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """subagent_type carries the same disclosure risk gitBranch does (it
        can name a project-scoped custom agent definition) but, unlike
        gitBranch, was not redacted -- a distinctive custom subagent_type on
        the scanned foreign root must never appear verbatim in either the
        "Top subagent types" column or the new "AgentType" model-mix table.
        Uses two different subagent_type values across roots (a same-value
        fixture cannot surface this: it would leak either way)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        distinctive_type = "acme-corp-internal-deploy-reviewer"
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("b1", distinctive_type)]),
        ])
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert distinctive_type not in out
        assert "account-2/agent-type-1" in out

    def test_same_agent_type_across_roots_does_not_merge_model_mix_rows(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """The model-mix table is keyed on the redacted (root, subagent_type)
        label, not the raw subagent_type alone -- two accounts each
        dispatching "staff-sdet" must land in two separate rows (Runs=1
        each), never summed into one merged Runs=2 row that blends two
        accounts' data."""
        session_id = "sess-a"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("claude-sonnet-4-6", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        session_id_b = "sess-b"
        _write_jsonl(proj_b / f"{session_id_b}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("b1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            proj_b, session_id_b, "agent-b1", "b1",
            [_asst("claude-sonnet-4-6", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        row_a = _table_cols(out, header_contains="Runs", row_contains="account-1/agent-type-1", max_labels=4)
        row_b = _table_cols(out, header_contains="Runs", row_contains="account-2/agent-type-1", max_labels=4)
        assert row_a["Runs"] == "1"
        assert row_b["Runs"] == "1"

    def test_account_ordinal_is_resolved_path_sorted_not_scan_order(self, tmp_path, monkeypatch, capsys):
        """account-N is assigned by resolved-path sort (_redaction_ordinals),
        not by --config-dir argument order. The active/default profile is
        deliberately named "zzz-active" -- sorting AFTER the extra
        --config-dir root "aaa-extra" in resolved-path order despite being
        scanned first (active profile is always scan-order position 0) --
        so a regression back to raw scan-order indexing
        (_root_index_for_path's position used directly as the account
        number) would swap which root reads as account-1. Every sibling
        test in this class uses fake_projects, whose active root is always
        a path-prefix ancestor of any fake_config_dir_factory root and
        therefore always sorts first regardless — that shared setup cannot
        catch this regression class, the same blind spot PR #603's own
        pre-fix edit-format test had."""
        monkeypatch.setattr(_mod.scope, "declared_transcript_roots", lambda: [])
        active = tmp_path / "zzz-active"
        active_proj = active / "projects" / "-home-user-active-repo"
        active_proj.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: active)
        _write_jsonl(active_proj / "sess-active.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
        ])

        extra = tmp_path / "aaa-extra"
        extra_proj = extra / "projects" / "-home-user-extra-repo"
        extra_proj.mkdir(parents=True)
        _write_jsonl(extra_proj / "sess-extra.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[
                _agent_use("b1", "staff-sdet"), _agent_use("b2", "staff-sdet"),
            ]),
        ])

        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(extra)]))
        out = capsys.readouterr().out
        account_1 = _table_cols(out, header_contains="Spawns", row_contains="account-1/branch-1", max_labels=6)
        account_2 = _table_cols(out, header_contains="Spawns", row_contains="account-2/branch-1", max_labels=6)
        # "aaa-extra" (2 spawns) resolved-path-sorts before "zzz-active" (1
        # spawn) despite being scanned second -- account-1 must be the extra
        # root's row.
        assert account_1["Spawns"] == "2"
        assert account_2["Spawns"] == "1"

    def test_this_repo_with_explicit_config_dir_discloses_branch_and_allowlisted_agent_type(
        self, fake_projects, fake_config_dir_factory, _isolated_staff_sdet_allowlist, capsys
    ):
        """--this-repo plus subagent-mix's own repeatable --config-dir (not
        only the declared-roots file) discloses a raw branch name and an
        allowlisted subagent_type in both tables -- pins that the two flags
        are not mutually exclusive. Both roots write identical branch and
        subagent_type values, so the two disclosed labels are the same
        regardless of which physical root resolves to account-1 vs. account-2."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-testrepo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("b1", "staff-sdet")]),
        ])
        args = _subagent_mix_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagent_mix.cmd_subagent_mix(args)  # no SystemExit
        out = capsys.readouterr().out
        # Substring-on-combined-stdout, not a per-table _table_cols extract:
        # _mix_branch_label/_stype_label are idempotent per (root_idx, value)
        # key, so both tables render the same label for the same key even
        # though each computes it independently at its own print time -- a
        # future change breaking that idempotence would need this test
        # tightened to catch a per-table divergence.
        assert "account-1/feat" in out
        assert "account-2/feat" in out
        assert "account-1/staff-sdet" in out
        assert "account-2/staff-sdet" in out

    def test_this_repo_non_allowlisted_agent_type_counter_starts_at_one_despite_allowlisted_seen_first(
        self, fake_projects, fake_config_dir_factory, _isolated_staff_sdet_allowlist, capsys
    ):
        """A non-allowlisted subagent_type still renders as
        account-<K>/agent-type-1, with its counter starting at 1 despite an
        allowlisted type being seen first in the same session -- proves the
        disclosed path never writes into subagent_type_redact_map, matching
        TestRootScopedDisplayLabel's own unit-level pin at the integration
        layer."""
        acct_b = fake_config_dir_factory("acct-b")  # forces multi_root; carries no data of its own
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[
                _agent_use("a1", "staff-sdet"),  # allowlisted, dispatched first
                _agent_use("a2", "acme-corp-internal-tool"),  # not allowlisted
            ]),
        ])
        args = _subagent_mix_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert re.search(r"account-\d+/staff-sdet", out)
        assert re.search(r"account-\d+/agent-type-1\b", out)
        assert "acme-corp-internal-tool" not in out

    def test_this_repo_case_varied_spelling_of_allowlisted_type_stays_opaque(
        self, fake_projects, fake_config_dir_factory, _isolated_staff_sdet_allowlist, capsys
    ):
        """Pin against a later .lower()-style "robustness" change disclosing
        a private type that collides case-insensitively with a real
        allowlisted name. Asserts both directions in the same run: the
        mixed-case collision stays opaque, and the exact-case allowlisted
        form still discloses -- without the positive control, an
        accidentally-empty allowlist would pass this test for the wrong
        reason."""
        acct_b = fake_config_dir_factory("acct-b")  # forces multi_root; carries no data of its own
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[
                _agent_use("a1", "Staff-Sdet"),  # mixed-case collision, not allowlisted verbatim
                _agent_use("a2", "staff-sdet"),  # exact-case allowlisted form -- positive control
            ]),
        ])
        args = _subagent_mix_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "Staff-Sdet" not in out
        assert re.search(r"account-\d+/agent-type-1\b", out)
        assert re.search(r"account-\d+/staff-sdet\b", out)

    def test_this_repo_colliding_branch_names_across_accounts_stay_on_separate_rows(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """Two accounts' identically-named "main" branch must not collapse
        into one row under --this-repo disclosure either -- the table this
        measurement actually reads."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-testrepo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("b1", "staff-sdet")]),
        ])
        args = _subagent_mix_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "account-1/main" in out
        assert "account-2/main" in out

    def test_this_repo_still_stamps_do_not_publish_banner_under_multi_root(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        acct_b = fake_config_dir_factory("acct-b")
        args = _subagent_mix_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagent_mix.cmd_subagent_mix(args)
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_this_repo_per_session_still_refused_under_multi_root(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        acct_b = fake_config_dir_factory("acct-b")
        args = _subagent_mix_args(this_repo=True, extra_config_dirs=[str(acct_b)], per_session=True)
        args._this_repo_slugs = ["-home-user-testrepo"]
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_subagent_mix(args)
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "--per-session" in err

    def test_this_repo_single_root_prints_raw_branch_and_raw_non_allowlisted_type_with_no_account_prefix(
        self, fake_projects, capsys
    ):
        """Single-root path (no --config-dir, no declared roots): root_idx
        is always None, so both fields print raw with no account-<K>/
        prefix regardless of --this-repo or the allowlist. The
        non-allowlisted type is the load-bearing half: it proves the
        allowlist gate is never consulted at single root, catching a future
        reordering that checks `disclose` before `root_idx is not None`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "acme-corp-internal-tool")]),
        ])
        args = _subagent_mix_args(this_repo=True)
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "feat" in out
        assert "acme-corp-internal-tool" in out
        assert "account-" not in out
