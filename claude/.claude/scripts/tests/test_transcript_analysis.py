"""Tests for transcript-analysis.py."""
import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from conftest import _agent_use, _asst, _bash_use, _table_cols, _tool_result, _user_msg, _write_jsonl
from helpers import HOOKS_DIR, SKILLS_DIR, bash_input, run_hook_reason

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _write_subagent_jsonl(
    proj: Path, session_id: str, agent_id: str, records: list[dict]
) -> None:
    """Write records to the split subagent layout: <session_id>/subagents/<agent_id>.jsonl."""
    subdir = proj / session_id / _mod.SUBAGENT_SUBDIR
    subdir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(subdir / f"{agent_id}.jsonl", records)


def _write_subagent_dispatch(
    proj: Path, session_id: str, agent_id: str, tool_use_id: str, records: list[dict],
    *, agent_type: str = "staff-backend-engineer", description: str = "review",
    requested_model: str | None = None,
) -> None:
    """Write both the .jsonl and its paired .meta.json for a synthetic subagent
    dispatch — _write_subagent_jsonl (above) writes only the .jsonl, never the
    meta.json sidecar reviewer-yield's and subagent-mix's dispatch joins read.
    Matches the real on-disk shape: {"agentType", "description", "toolUseId",
    "spawnDepth"}. requested_model, when given, adds meta.json's own "model"
    key — absent by default, matching a dispatch that requested no explicit
    model."""
    _write_subagent_jsonl(proj, session_id, agent_id, records)
    subdir = proj / session_id / _mod.SUBAGENT_SUBDIR
    meta = {"agentType": agent_type, "description": description, "toolUseId": tool_use_id, "spawnDepth": 1}
    if requested_model is not None:
        meta["model"] = requested_model
    (subdir / f"{agent_id}.meta.json").write_text(json.dumps(meta))


def _md_table_cols(out: str, *, header_contains: str, row_contains: str | Sequence[str],
                    occurrence: int | None = None) -> dict[str, str]:
    """Map column-label -> cell value for the GFM pipe-table data row matching
    `row_contains` -- the markdown-table counterpart to _table_cols, splitting
    on `|` instead of whitespace since a markdown cell (a model ID, a share
    percentage) may itself contain no whitespace token boundary to split on.

    Mirrors _table_cols' anchoring (locates the section by the header line,
    scoped through the next blank line or repeated header) and its
    fail-loud-on-ambiguous-match semantics: exactly one header / one matching
    data row must be found unless `occurrence` disambiguates a repeated
    header. The `|---|---|...|` separator row never satisfies `row_contains`
    (it has no cell text to match), so it needs no special-casing to stay
    out of the returned row.
    """
    lines = out.splitlines()
    header_indices = [i for i, ln in enumerate(lines) if header_contains in ln]
    if occurrence is None:
        assert len(header_indices) == 1, f"header match not unique for {header_contains!r}: {len(header_indices)}"
        start = header_indices[0]
    else:
        assert len(header_indices) >= occurrence, (
            f"header occurrence {occurrence} requested but only {len(header_indices)} "
            f"found for {header_contains!r}"
        )
        start = header_indices[occurrence - 1]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if not lines[i].strip() or header_contains in lines[i]:
            end = i
            break
    section_lines = lines[start:end]

    headers = [ln for ln in section_lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header = headers[0]

    needles = (row_contains,) if isinstance(row_contains, str) else tuple(row_contains)
    rows = [ln for ln in section_lines if ln != header and all(n in ln for n in needles)]
    assert len(rows) == 1, f"row match not unique for {row_contains!r}: {len(rows)}"
    labels = [c.strip() for c in header.strip().strip("|").split("|")]
    values = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    assert len(values) >= len(labels), f"row has fewer cells than labels: {rows[0]!r}"
    return dict(zip(labels, values, strict=False))


def _sum_column_across_rows(out: str, *, header_contains: str, label: str, row_prefix: str) -> int:
    """Sum one single-token integer column across every row whose leading
    label starts with `row_prefix` (e.g. every multi-root "account-" row).

    _table_cols can't be reused directly here: it asserts exactly one
    matching data row, and a multi-root sum needs several. This still
    anchors the column position to the header row's own token index
    (`header.split().index(label)`) instead of a bare `line.split()[N]`
    index, so a column reorder fails with a clear ValueError ("label not in
    list") instead of silently summing the wrong column.
    """
    lines = out.splitlines()
    headers = [ln for ln in lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header_idx = lines.index(headers[0])
    col_idx = headers[0].split().index(label)
    total = 0
    matched_any = False
    for ln in lines[header_idx + 1:]:
        if ln == "":
            break
        if not ln.startswith(row_prefix):
            continue
        matched_any = True
        total += int(ln.split()[col_idx])
    assert matched_any, f"no rows starting with {row_prefix!r} found under header {header_contains!r}"
    return total


def _extract_unpriced_total(out: str) -> int:
    """Read cmd_cost's 'Unpriced tokens (unknown model IDs): N' line as an int.

    A single named extractor for this one non-tabular summary line, matching
    _table_cols' role for tabular output — one parse point to update if the
    line's wording changes, instead of an inline regex at each call site.
    """
    match = re.search(r"Unpriced tokens \(unknown model IDs\): ([\d,]+)", out)
    assert match is not None, "unpriced-tokens summary line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_grand_total(out: str) -> float:
    """Read cost's grand-total row ('total  $X.XX') from the token-class table."""
    match = re.search(r"^total\s+([\d,]+\.\d\d)\s*$", out, re.MULTILINE)
    assert match is not None, "grand total row not found in output"
    return float(match.group(1).replace(",", ""))


def _extract_arm_dollars(out: str, arm_label: str) -> float:
    """Read plan-boundary's per-arm dollar figure (e.g. arm_label='C: fresh
    Sonnet handoff') by row-label prefix, not by the row's full formatted
    text -- survives cosmetic changes to column width/precision."""
    match = re.search(rf"^{re.escape(arm_label)}\s+([\d,]+\.\d\d)\s*$", out, re.MULTILINE)
    assert match is not None, f"no row found for arm {arm_label!r}"
    return float(match.group(1).replace(",", ""))


def _extract_md_grand_total(out: str) -> float:
    """Read --summary's bolded grand-total row ('| **total** | **X.XX** | | |')
    from the markdown token-class table."""
    match = re.search(r"^\|\s*\*\*total\*\*\s*\|\s*\*\*([\d,]+\.\d\d)\*\*\s*\|", out, re.MULTILINE)
    assert match is not None, "markdown grand total row not found in output"
    return float(match.group(1).replace(",", ""))


def _extract_account_totals(out: str) -> dict[int, float]:
    """Map account ordinal -> that account's own token-class 'total' row
    dollar figure, by splitting cost's '## Cost by account' section on its
    own '### account-N' sub-headers."""
    totals: dict[int, float] = {}
    for block in out.split("### account-")[1:]:
        ordinal_str, _, rest = block.partition("\n")
        match = re.search(r"^total\s+([\d,]+\.\d\d)\s*$", rest, re.MULTILINE)
        assert match is not None, f"no total row found for account-{ordinal_str.strip()}"
        totals[int(ordinal_str.strip())] = float(match.group(1).replace(",", ""))
    return totals


def _extract_summary_unpriced(out: str) -> tuple[int, int]:
    """Read --summary's dedicated 'Unpriced tokens: N tokens across M model IDs'
    line as (N, M) — distinct from the full report's own
    'Unpriced tokens (unknown model IDs): N' line, which _extract_unpriced_total
    reads."""
    match = re.search(r"Unpriced tokens: ([\d,]+) tokens across (\d+) model IDs", out)
    assert match is not None, "summary unpriced-tokens line not found in output"
    return int(match.group(1).replace(",", "")), int(match.group(2))


def _priced_sidechain_asst(
    model: str, *, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0,
    ts: str | None = None, branch: str = "main",
) -> dict:
    """Build a sidechain assistant record with explicit, flat-priced usage
    fields, for subagent-mix's Actual $/Counterfactual $ dollar-column tests
    -- a sidechain counterpart to TestCost's own _priced (cache-write-split
    fidelity is irrelevant to these tests' hand-computed input-token math)."""
    rec = _asst(model, branch=branch, sidechain=True, ts=ts, content=[])
    rec["message"]["usage"] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": 0,
    }
    return rec


def _skill_use(tool_id: str, skill: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


def _edit_use(tool_id: str, *, path: str = "/foo.py") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Edit", "input": {"file_path": path}}


def _write_use(tool_id: str, content: str, *, path: str = "/scratch/findings.md") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Write", "input": {"file_path": path, "content": content}}


def _mcp_use(tool_id: str, server: str, tool: str) -> dict:
    """Build an mcp__<server>__<tool> tool_use block, the on-disk shape for an MCP tool call."""
    return {"type": "tool_use", "id": tool_id, "name": f"mcp__{server}__{tool}", "input": {}}


def test_projects_dir_honors_claude_config_dir(monkeypatch, tmp_path):
    """scope.PROJECTS_DIR is computed at import time from config_dir(); a fresh
    import of transcript_analysis.scope with CLAUDE_CONFIG_DIR set resolves
    under that directory instead of ~/.claude.

    Loaded by path (not a plain `import transcript_analysis.scope`, which
    would hit sys.modules' already-imported copy) for the same reason _mod
    above is: the module-level PROJECTS_DIR assignment only re-runs on a
    genuinely fresh exec.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    scope_path = _SCRIPT.parent / "transcript_analysis" / "scope.py"
    spec = importlib.util.spec_from_file_location("transcript_analysis_scope_config_dir_case", scope_path)
    scope_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope_mod)
    assert tmp_path / "projects" == scope_mod.PROJECTS_DIR


class TestConfigDirFlag:
    """--config-dir reassigns scope.PROJECTS_DIR after argument parsing,
    distinct from the CLAUDE_CONFIG_DIR-at-import-time behavior
    test_projects_dir_honors_claude_config_dir covers above. Every test here
    registers _mod.scope.PROJECTS_DIR with monkeypatch before calling main()
    (even when just re-setting it to its current value) so main()'s direct
    `scope.PROJECTS_DIR = ...` reassignment — which bypasses monkeypatch — is
    still restored to its pre-test value at teardown, protecting the rest of
    this shared, once-imported module from leaking state across tests."""

    def test_flag_set_enumerates_sessions_from_the_fixture_dir(self, monkeypatch, tmp_path, capsys):
        """The reassignment's core invariant: a subcommand actually finds
        sessions under PATH/projects, not the module's default. The no-flag
        and error-path tests below can both pass while this one is broken.

        Asserts on captured stdout, not just the PROJECTS_DIR attribute --
        the attribute alone would stay green even if `buckets`' own
        session-iteration path captured PROJECTS_DIR into a local before
        main()'s post-parse reassignment, silently reporting zero sessions
        (the exact regression class this feature exists to close)."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", _mod.scope.PROJECTS_DIR)
        config_dir = tmp_path / "other-account"
        proj = config_dir / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat-config-dir")])

        monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "--config-dir", str(config_dir), "buckets"])
        _mod.main()

        assert config_dir / "projects" == _mod.scope.PROJECTS_DIR
        out = capsys.readouterr().out
        assert "feat-config-dir" in out, (
            f"the seeded session's branch never surfaced in `buckets` output: {out!r}"
        )

    def test_no_flag_leaves_projects_dir_unchanged_after_parsing(self, monkeypatch, tmp_path):
        """Without --config-dir, main()'s post-parse reassignment must not
        run at all. test_projects_dir_honors_claude_config_dir only covers
        import time; the regression risk this guards is in the reassignment
        main() performs after parsing, e.g. an unguarded `Path(None)`."""
        fixture_projects_dir = tmp_path / "projects"
        fixture_projects_dir.mkdir()
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", fixture_projects_dir)

        monkeypatch.setattr(sys, "argv", ["transcript-analysis.py", "buckets"])
        _mod.main()

        assert fixture_projects_dir == _mod.scope.PROJECTS_DIR

    def test_this_repo_loud_error_on_zero_matches(self, monkeypatch, tmp_path, capsys):
        """--config-dir + --this-repo, resolved against a config dir with no
        matching project directories, errors loudly instead of silently
        reporting an empty scope -- closes the original reported symptom
        (declaring no sessions exist for a container that has them)."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", _mod.scope.PROJECTS_DIR)
        config_dir = tmp_path / "other-account"
        (config_dir / "projects").mkdir(parents=True)  # exists, but empty

        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(config_dir), "buckets", "--this-repo"],
        )
        with pytest.raises(SystemExit):
            _mod.main()

        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert "buckets" in err

    @pytest.mark.parametrize(
        "subcommand", ["cost", "context-distribution", "read-scope", "subagents", "subagent-mix", "cost-trend"]
    )
    def test_top_level_config_dir_refused_for_subcommands_with_their_own(
        self, monkeypatch, tmp_path, capsys, subcommand
    ):
        """cost, context-distribution, read-scope, subagents, and
        subagent-mix all resolve their own scan roots via their own
        --config-dir (_resolve_cost_roots -> config_dir() +
        declared_transcript_roots()), never reading the module-global
        PROJECTS_DIR this top-level flag reassigns. Letting the top-level
        flag through silently would reassign an unused global while the
        actual scan root stays whatever config_dir() resolves to -- an
        operator typing --config-dir /other-account cost would see no error
        and would silently scan their own default account instead. main()
        refuses the combination outright, matching every other subcommand's
        actually-effective top-level --config-dir. The refusal is
        unconditional on subcommand alone, checked before args.this_repo is
        ever read, so a bare subcommand invocation (no --this-repo) is the
        correct, strictly-scoped regression pin -- a --this-repo variant
        would hit the identical check with no new branch coverage."""
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        active_config_dir = tmp_path / "active-account"
        (active_config_dir / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active_config_dir))

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(other_account), subcommand],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != _mod.scope.PROJECTS_DIR  # refusal happens before reassignment
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert subcommand in err


# ---------------------------------------------------------------------------
# _table_cols self-tests
# ---------------------------------------------------------------------------


class TestTableColsHelper:
    """Unit tests for the _table_cols helper — verifies the safety properties
    the refactor depends on."""

    def test_basic_column_lookup(self):
        out = "Branch Lead Main\nfeat   1    0  \n"
        cols = _table_cols(out, header_contains="Lead", row_contains="feat")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"

    def test_drop_leading_labels(self):
        out = "Branch Thread Opus Sonnet\nmain   main   1    2\n       sidechain 0  1\n"
        # sidechain row has Branch suppressed — one fewer leading token
        cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                           drop_leading_labels=1)
        assert cols["Thread"] == "sidechain"
        assert cols["Opus"] == "0"
        assert cols["Sonnet"] == "1"

    def test_row_not_unique_raises(self):
        out = "Branch Lead\nfeat   1\nfeat   2\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_row_too_few_tokens_raises(self):
        out = "Branch Lead Main\nfeat   1\n"
        with pytest.raises(AssertionError, match="fewer cells"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_max_labels_excludes_trailing_multiword_header(self):
        # "Top subagent types" is 3 header tokens but 1 data token (—); max_labels=4
        # limits to Branch/Sess/Spawns/CR so the assertion len(values)>=len(labels) passes.
        out = "Branch Sess Spawns CR Top subagent types\nfeat   1    0      2 —\n"
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=4)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"

    def test_row_startswith_excludes_indented_lines(self):
        # "code-review" appears at column 0 in the data row and indented in the summary.
        out = "skill  top-level user-slash\ncode-review  1  0\n  code-review (1 top)\n"
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review",
                           row_startswith=True)
        assert cols["top-level"] == "1"
        assert cols["user-slash"] == "0"

    def test_header_not_unique_raises(self):
        # Two lines both contain "Lead" → header ambiguity must raise, not silently pick one.
        out = "Bin Lead Main\n2026-W20 1 0\nLead row duplicate 0 1\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="2026-W20")

    def test_occurrence_scoping_excludes_content_after_the_selected_block(self):
        """occurrence=N must stop its row/header search at its own table's
        boundary (next blank line or next header-containing line), not
        search to the end of `out` — every existing occurrence= call site
        (reviewer-yield's Table 2, always the last table printed, with a
        composite row_contains already globally unique) would stay green
        even if that boundary were dropped, since there is nothing after
        Table 2 for an unscoped search to leak into. This fixture puts a
        second occurrence directly after the first's true boundary, with a
        row that would also satisfy `row_contains` if the boundary weren't
        enforced — occurrence=1 must select only the first table's row."""
        out = "\n".join([
            "Header      Count",
            "------------------",
            "alpha           1",
            "",
            "Header      Count",
            "------------------",
            "alpha           2",
        ])
        cols = _table_cols(out, header_contains="Header", row_contains="alpha", occurrence=1)
        assert cols["Count"] == "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_fam_opus(self):
        assert _mod._fam("claude-opus-4-7") == "opus"

    def test_fam_sonnet(self):
        assert _mod._fam("claude-sonnet-4-6") == "sonnet"

    def test_fam_haiku(self):
        assert _mod._fam("claude-haiku-4-5") == "haiku"

    def test_fam_unknown_model(self):
        assert _mod._fam("gpt-4") == "other"

    def test_content_text_plain_string(self):
        assert _mod._content_text("hello world") == "hello world"

    def test_content_text_list_joins_text_blocks(self):
        content = [{"type": "text", "text": "hello"}, {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "world"}]
        assert _mod._content_text(content) == "hello world"

    def test_content_text_none_returns_empty(self):
        assert _mod._content_text(None) == ""

    def test_parse_ts_valid_iso_z(self):
        ts = _mod._parse_ts("2024-06-01T12:00:00.000Z")
        assert ts is not None and ts > 0

    def test_parse_ts_none_input(self):
        assert _mod._parse_ts(None) is None

    def test_parse_ts_invalid_string(self):
        assert _mod._parse_ts("not-a-date") is None

    def test_iso_date_valid_returns_string(self):
        assert _mod._iso_date("2026-05-19") == "2026-05-19"

    def test_iso_date_invalid_month_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._iso_date("2026-13-01")

    def test_iso_date_garbage_raises_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _mod._iso_date("garbage")


class TestParseSinceNdArg:
    """The shared --since Nd parser behind cmd_audit_routing, _cost_report,
    cmd_audit_routing_shape, and cmd_audit_routing_samples."""

    @pytest.mark.timing
    def test_valid_nd_value_returns_timestamp_and_raw_string(self):
        since_ts, since_raw = _mod._parse_since_nd_arg(
            argparse.Namespace(since="1d"), "cost"
        )
        assert since_raw == "1d"
        assert since_ts == pytest.approx(time.time() - 86400, abs=1)

    def test_absent_since_returns_none_for_both(self):
        since_ts, since_raw = _mod._parse_since_nd_arg(
            argparse.Namespace(since=None), "cost"
        )
        assert since_ts is None
        assert since_raw is None

    def test_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod._parse_since_nd_arg(argparse.Namespace(since="not-a-window"), "cost")
        assert exc_info.value.code == 1
        assert "cost: --since: expected Nd like '35d'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# fail-seq regexes
# ---------------------------------------------------------------------------


class TestFailSeqRegexes:
    def test_failed_re_extracts_count(self):
        assert _mod.FAILED_RE.findall("5 failed, 20 passed") == ["5"]

    def test_failed_re_finds_all_matches_for_caller_max(self):
        matches = [int(m) for m in _mod.FAILED_RE.findall("3 failed\n105 failed\n2 failed")]
        assert max(matches) == 105

    def test_test_runner_re_matches_npm_run_test(self):
        assert _mod.TEST_RUNNER_RE.search("npm run test")

    def test_test_runner_re_matches_npm_run_verify(self):
        assert _mod.TEST_RUNNER_RE.search("npm run verify")

    def test_test_runner_re_matches_npm_run_lint(self):
        assert _mod.TEST_RUNNER_RE.search("npm run lint")

    def test_test_runner_re_matches_pytest(self):
        assert _mod.TEST_RUNNER_RE.search("pytest claude/.claude/")

    def test_test_runner_re_matches_vitest(self):
        assert _mod.TEST_RUNNER_RE.search("npx vitest --run")

    def test_test_runner_re_matches_ruff_check(self):
        assert _mod.TEST_RUNNER_RE.search("ruff check .")

    def test_test_runner_re_matches_deno_test(self):
        assert _mod.TEST_RUNNER_RE.search("deno test")

    def test_test_runner_re_does_not_match_git_commands(self):
        assert not _mod.TEST_RUNNER_RE.search("git status")

    def test_test_runner_re_does_not_match_ls(self):
        assert not _mod.TEST_RUNNER_RE.search("ls -la")

    def test_test_runner_re_does_not_match_echo(self):
        assert not _mod.TEST_RUNNER_RE.search("echo hello")


# ---------------------------------------------------------------------------
# _longest_fail_streak
# ---------------------------------------------------------------------------


class TestLongestFailStreak:
    def test_empty_list(self):
        assert _mod._longest_fail_streak([]) == 0

    def test_all_passing(self):
        assert _mod._longest_fail_streak([False, False, False]) == 0

    def test_all_failing(self):
        assert _mod._longest_fail_streak([True, True, True]) == 3

    def test_single_spike_then_green(self):
        assert _mod._longest_fail_streak([False, True, False, False]) == 1

    def test_longest_streak_not_first(self):
        assert _mod._longest_fail_streak([False, True, True, False, True]) == 2

    def test_streak_at_end(self):
        assert _mod._longest_fail_streak([False, False, True, True, True]) == 3

    def test_oscillation_no_run_longer_than_one(self):
        assert _mod._longest_fail_streak([True, False, True, False, True]) == 1


# ---------------------------------------------------------------------------
# buckets
# ---------------------------------------------------------------------------


class TestBuckets:
    def test_counts_non_sidechain_assistant_turns(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-a", sidechain=True),  # excluded
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        # 1 opus + 1 sonnet = 2 non-sidechain turns
        assert " 2 " in out

    def test_branch_filter_excludes_other_branches(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-b"),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat-a"})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "feat-a" in out
        assert "feat-b" not in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_proj_column_counts_distinct_project_dirs_not_sessions(self, tmp_path, monkeypatch, capsys):
        """Proj tracks a set of project dirs per branch, not a session counter: the
        same project contributing two session files on one branch reports Proj==1,
        Sess==2 — the case that discriminates set semantics from a counter."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-u-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess1.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_a / "sess2.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "1"
        assert cols["Sess"] == "2"

    def test_proj_column_counts_two_project_dirs_sharing_a_branch(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        proj_a = projects / "-home-u-repo-a"
        proj_b = projects / "-home-u-repo-b"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        _write_jsonl(proj_b / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)

        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        _mod.cmd_buckets(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Branch", row_contains="feat", max_labels=8)
        assert cols["Proj"] == "2"
        assert cols["Sess"] == "2"


# ---------------------------------------------------------------------------
# fail-seq end-to-end (core parsing logic, via shared helper)
# ---------------------------------------------------------------------------


def _collect_runs(target_branch: str) -> list[tuple[str, int]]:
    """Extract (model_family, failed_count) pairs for target_branch using the module's core logic."""
    runs: list[tuple[str, int]] = []
    for _jsonl, records in _mod.iter_sessions(_mod.scope.PROJECTS_DIR):
        pending: dict[str, str] = {}
        current_branch: str = ""
        for rec in records:
            branch = rec.get("gitBranch") or ""
            if branch != current_branch:
                pending.clear()
                current_branch = branch
            if branch != target_branch or bool(rec.get("isSidechain")):
                continue
            rtype = rec.get("type", "")
            msg = rec.get("message") or {}
            if rtype == "assistant":
                fam = _mod._fam(msg.get("model", ""))
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        cmd = (block.get("input") or {}).get("command", "")
                        if _mod.TEST_RUNNER_RE.search(cmd):
                            pending[block["id"]] = fam
            elif rtype in ("user", "human"):
                content = msg.get("content") or []
                for block in (content if isinstance(content, list) else []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        if tid in pending:
                            fam = pending.pop(tid)
                            text = _mod._content_text(block.get("content", ""))
                            counts = [int(m) for m in _mod.FAILED_RE.findall(text)]
                            runs.append((fam, max(counts) if counts else 0))
    return runs


class TestFailSeqEndToEnd:
    def test_extracts_fail_and_pass_counts(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat"),
        ])
        assert _collect_runs("feat") == [("sonnet", 3), ("sonnet", 0)]

    def test_sidechain_bash_calls_not_tracked(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True, content=[_bash_use("ts", "pytest")]),
            _user_msg([_tool_result("ts", "7 failed")], branch="feat"),
        ])
        assert _collect_runs("feat") == []

    def test_non_test_bash_not_tracked(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "git status")]),
            _user_msg([_tool_result("t1", "M file.txt")], branch="feat"),
        ])
        assert _collect_runs("feat") == []

    def test_pending_cleared_on_branch_change(self, fake_projects):
        """tool_use from branch-a is not matched to tool_result after branch switches."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a", content=[_bash_use("t1", "pytest")]),
            _asst("claude-sonnet-4-6", branch="feat-b"),  # branch switches → pending cleared
            _user_msg([_tool_result("t1", "5 failed")], branch="feat-b"),
        ])
        # t1 was from feat-a; after branch switch pending clears → not attributed to feat-a
        assert _collect_runs("feat-a") == []

    def test_max_count_used_when_multiple_failed_in_output(self, fake_projects):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_bash_use("t1", "npm run test")]),
            _user_msg([_tool_result("t1", "2 failed\n105 failed\n1 failed")], branch="feat"),
        ])
        runs = _collect_runs("feat")
        assert runs == [("opus", 105)]


# ---------------------------------------------------------------------------
# duration gap splitting
# ---------------------------------------------------------------------------


class TestDurationGapSplit:
    def test_large_gap_counted_as_idle(self):
        now = time.time()
        tss = sorted([now, now + 60, now + 120, now + 8000, now + 8060])
        gap_secs = 1800
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        assert len(idle_gaps) == 1
        assert idle_gaps[0] == pytest.approx(8000 - 120, abs=1)

    def test_no_gaps_means_fully_active(self):
        now = time.time()
        tss = [now, now + 60, now + 120]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > 1800]
        assert idle_gaps == []

    def test_active_time_is_span_minus_idle(self):
        now = time.time()
        tss = sorted([now, now + 300, now + 10000, now + 10300])
        gap_secs = 1800
        span = tss[-1] - tss[0]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        idle = sum(idle_gaps)
        active = span - idle
        # gap between tss[1] and tss[2] = 9700s; active = 300 + 300 = 600s
        assert active == pytest.approx(600, abs=2)


# ---------------------------------------------------------------------------
# subagent-mix
# ---------------------------------------------------------------------------


def _subagent_mix_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    per_session: bool = False,
    since: str | None = None,
    since_date: str | None = None,
    until_date: str | None = None,
    reprice_as: str | None = None,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "per_session": per_session,
        "since": since,
        "since_date": since_date,
        "until_date": until_date,
        "reprice_as": reprice_as,
        "extra_config_dirs": extra_config_dirs,
    })()


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
        _mod.cmd_subagent_mix(args)
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
        _mod.cmd_subagent_mix(args)
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
        _mod.cmd_subagent_mix(args)
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
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "staff-backend-engineer(1)" in out
        assert "ciso-reviewer" not in out

    def test_branch_filter(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-a", content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", branch="feat-b", content=[_agent_use("a2", "staff-backend-engineer")]),
        ])
        args = _subagent_mix_args(branches="feat-a")
        _mod.cmd_subagent_mix(args)
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
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        # Both sessions should appear with stem prefixes; aggregate "feat" alone should not be present as a row.
        assert "abcd1234" in out
        assert "efgh5678" in out

    def test_no_data_prints_message(self, fake_projects, capsys):
        args = _subagent_mix_args()
        _mod.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "No data found." in out


def _write_agent_frontmatter(config_dir_path: Path, agent_type: str, model: str) -> None:
    """Write a minimal on-disk agent file with a `model:` frontmatter pin,
    at the path _declared_pin reads: <config_dir>/agents/<agent_type>.md."""
    agents_dir = config_dir_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_type}.md").write_text(f"---\nmodel: {model}\nname: {agent_type}\n---\nbody\n")


class TestSubagentMixModelMix:
    """cmd_subagent_mix's second table: one case per method term from the
    plan's requested/observed/declared/run/dangling definitions."""

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
        _mod.cmd_subagent_mix(_subagent_mix_args())
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
        _mod.cmd_subagent_mix(_subagent_mix_args())
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
        _mod.cmd_subagent_mix(_subagent_mix_args())
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
        _mod.cmd_subagent_mix(_subagent_mix_args())
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
        _mod.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        assert "sonnet(1)" in out
        assert f"{_mod._UNREQUESTED_MODEL_LABEL}(1)" in out

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
        _mod.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Runs", row_contains="general-purpose", max_labels=4)
        assert cols["Declared"] == _mod._DECLARED_PIN_BUILT_IN

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
        _mod.cmd_subagent_mix(_subagent_mix_args())
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
        _mod.cmd_subagent_mix(_subagent_mix_args())  # must not raise TypeError
        out = capsys.readouterr().out
        assert "(1 meta.json files failed to parse, excluded)" in out


class TestSubagentMixDollars:
    """The model-mix table's Actual$/Counterfactual$/Delta columns
    (_dispatch_usage_summary), including --since-date/--until-date's
    per-record (not per-dispatch) window and --reprice-as's counterfactual
    pricing."""

    def test_actual_dollars_match_hand_computed_usage(self, fake_projects, capsys):
        """1,000,000 input tokens at claude-sonnet-4-6's $3.00/MTok base rate
        prices to exactly $3.00, with every other usage field at zero."""
        session_id = "sess-actual"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$3.00"

    def test_reprice_as_delta_arithmetic(self, fake_projects, capsys):
        """The same 1,000,000-input-token dispatch re-priced at
        claude-haiku-4-5-20251001's $1.00/MTok rate: Actual $3.00,
        Counterfactual $1.00, Delta (Actual − Counterfactual) $2.00."""
        session_id = "sess-reprice"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(reprice_as="claude-haiku-4-5-20251001"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=7)
        assert cols["Actual$"] == "$3.00"
        assert cols["Counterfactual$"] == "$1.00"
        assert cols["Delta"] == "$2.00"

    def test_reprice_as_same_model_yields_zero_delta(self, fake_projects, capsys):
        """--reprice-as set to the dispatch's own real model must not diverge
        from the actual-dollars path -- Delta is exactly $0.00, not merely
        close to it, since both columns price the identical usage at the
        identical model ID."""
        session_id = "sess-reprice-same"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=500)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(reprice_as="claude-sonnet-4-6"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=7)
        assert cols["Actual$"] == cols["Counterfactual$"]
        assert cols["Delta"] == "$0.00"

    def test_invalid_reprice_as_value_exits_nonzero_listing_valid_ids(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_subagent_mix(_subagent_mix_args(reprice_as="not-a-real-model"))
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "subagent-mix: --reprice-as" in err
        assert "not-a-real-model" in err
        assert "claude-opus-5" in err  # one of _MODEL_BASE_INPUT_RATES' listed valid IDs

    def test_since_date_boundary_is_inclusive(self, fake_projects, capsys):
        """A sidechain record timestamped exactly at --since-date's own
        day-start instant is included, not excluded -- the [since_ts, ...)
        lower bound is inclusive."""
        session_id = "sess-since-boundary"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst(
                "claude-sonnet-4-6", input_tokens=1_000_000, ts="2026-07-01T00:00:00.000Z",
            )],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(since_date="2026-07-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$3.00"

    def test_until_date_boundary_is_exclusive(self, fake_projects, capsys):
        """A sidechain record timestamped exactly at --until-date's own
        day-after instant (the [..., until_ts) upper bound) is excluded, not
        included -- the dispatch itself still counts as a Run since window
        filtering scopes only the dollar columns, not Runs/Observed."""
        session_id = "sess-until-boundary"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst(
                "claude-sonnet-4-6", input_tokens=1_000_000, ts="2026-07-02T00:00:00.000Z",
            )],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(until_date="2026-07-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Runs"] == "1"
        assert cols["Actual$"] == "$0.00"

    def test_boundary_straddling_dispatch_prices_only_in_window_records(self, fake_projects, capsys):
        """A single dispatch's own sidechain straddles --until-date: one
        record before the cutoff, one after. Only the before-cutoff record's
        usage may be priced into Actual $ -- a per-dispatch (rather than
        per-record) filter would either price the whole $12.00 sidechain or
        none of it, never the correct $3.00 in-window slice. Direct
        regression test for _dispatch_usage_summary's per-record filtering."""
        session_id = "sess-straddle"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [
                _priced_sidechain_asst(
                    "claude-sonnet-4-6", input_tokens=1_000_000, ts="2026-07-01T00:00:00.000Z",
                ),
                _priced_sidechain_asst(
                    "claude-sonnet-4-6", input_tokens=3_000_000, ts="2026-07-02T00:00:00.000Z",
                ),
            ],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(until_date="2026-07-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$3.00"

    def test_synthetic_only_sidechain_renders_zero_dollars(self, fake_projects, capsys):
        """A sidechain whose only recorded model is the literal "<synthetic>"
        has no priced usage at all -- Actual $ renders "$0.00", never a crash
        or a bare "None"."""
        session_id = "sess-synthetic-dollars"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("<synthetic>", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args())  # must not raise
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$0.00"

    def test_dollar_totals_not_merged_across_roots_under_multi_root_redaction(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """The model-mix table is keyed on the redacted (root, subagent_type)
        label -- two accounts' same-named "staff-sdet" dispatches must each
        keep their own Actual $ total, never summed into one merged row that
        blends two accounts' dollar figures."""
        session_id = "sess-a"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
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
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=2_000_000)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        # _redaction_ordinals sorts by resolved path, not scan/insertion order,
        # so which physical root lands on account-1 vs account-2 isn't asserted
        # here -- only that the two accounts' dollar totals stay distinct
        # (never summed into one merged $9.00 row).
        cols_a = _table_cols(
            out, header_contains="Actual$", row_contains="account-1/agent-type-1",
            row_startswith=True, max_labels=5,
        )
        cols_b = _table_cols(
            out, header_contains="Actual$", row_contains="account-2/agent-type-1",
            row_startswith=True, max_labels=5,
        )
        assert {cols_a["Actual$"], cols_b["Actual$"]} == {"$3.00", "$6.00"}

    def test_reprice_as_more_expensive_model_yields_negative_delta(self, fake_projects, capsys):
        """--reprice-as a model *pricier* than the dispatch's own real model
        (a realistic use case: "what would this have cost on Opus?") must
        render Delta with the conventional -$N.NN form, not $-N.NN -- covers
        _fmt_usd's negative branch, which every other reprice test in this
        class leaves unexercised since they all reprice to something
        cheaper or identical."""
        session_id = "sess-reprice-pricier"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args(reprice_as="claude-opus-5"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=7)
        assert cols["Actual$"] == "$3.00"
        assert cols["Counterfactual$"] == "$5.00"
        assert cols["Delta"] == "-$2.00"

    def test_actual_dollars_sum_across_multiple_dispatches_of_same_agent_type(self, fake_projects, capsys):
        """Two separate dispatches of the same agent_type under one root must
        accumulate into one row's Actual$ total (row["actual_dollars"] +=),
        not overwrite or double-count -- the multi-root test above never
        exercises this since it keeps exactly one dispatch per account."""
        session_id = "sess-multi-dispatch"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-sdet"), _agent_use("a2", "staff-sdet"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=2_000_000)],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Runs"] == "2"
        assert cols["Actual$"] == "$9.00"

    def test_unpriced_turn_surfaced_not_silently_zero(self, fake_projects, capsys):
        """A turn whose model ID isn't in _MODEL_BASE_INPUT_RATES must not
        silently read as a genuinely zero-cost dispatch -- matches cost's own
        "(N unpriced turns / M tokens excluded ...)" convention. Before this
        fix, _dispatch_usage_summary discarded _price_turn's unpriced-tokens
        return value entirely."""
        session_id = "sess-unpriced"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst(
                "claude-unreleased-model", input_tokens=1_000_000, output_tokens=500,
            )],
            agent_type="staff-sdet",
        )
        _mod.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$0.00"
        assert "1 unpriced turns / 1,000,500 tokens excluded" in out


class TestDeclaredPinPathSafety:
    """_declared_pin builds a filesystem path from subagent_type -- data
    that, under --config-dir, can originate from a scanned foreign root's
    own transcript content, not just this process's own dispatches."""

    def test_traversal_agent_type_does_not_escape_agents_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        secret_file = tmp_path / "outside-agents-dir.md"
        secret_file.write_text("---\nmodel: SECRET-LEAKED-VALUE\n---\nbody\n")
        assert _mod._declared_pin("../outside-agents-dir", agents_dir, {}) == _mod._DECLARED_PIN_BUILT_IN

    def test_absolute_path_agent_type_does_not_escape_agents_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        secret_file = tmp_path / "outside-agents-dir.md"
        secret_file.write_text("---\nmodel: SECRET-LEAKED-VALUE\n---\nbody\n")
        absolute_agent_type = str(secret_file.with_suffix(""))
        assert _mod._declared_pin(absolute_agent_type, agents_dir, {}) == _mod._DECLARED_PIN_BUILT_IN

    def test_ordinary_agent_type_name_is_unaffected(self, tmp_path):
        """The allowlist must not reject real subagent_type shapes (kebab-case
        identifiers, underscores) -- only a deny-path regression, not a
        false-positive rejection of legitimate names."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "staff-sdet.md").write_text("---\nmodel: sonnet\n---\nbody\n")
        assert _mod._declared_pin("staff-sdet", agents_dir, {}) == "sonnet"


class TestSubagentMixSince:
    def test_since_excludes_dispatches_older_than_window(self, fake_projects, capsys):
        old_ts = "2020-01-01T00:00:00Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=old_ts, content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.cmd_subagent_mix(_subagent_mix_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_malformed_since_exits_nonzero_naming_subagent_mix(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_subagent_mix(_subagent_mix_args(since="not-a-window"))
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
        _mod.cmd_subagent_mix(_subagent_mix_args(since="1d"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spawns", row_contains="main", max_labels=6)
        assert cols["Spawns"] == "1"

    def test_since_excludes_dispatches_missing_timestamp(self, fake_projects, capsys):
        rec = _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")])  # no ts=
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.cmd_subagent_mix(_subagent_mix_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out


class TestSubagentMixMultiRoot:
    """Repeatable --config-dir on subagent-mix, and its disclosure controls."""

    def test_two_roots_yield_strictly_more_spawns_than_either_alone(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.cmd_subagent_mix(_subagent_mix_args())
        single_root_out = capsys.readouterr().out
        single_root_cols = _table_cols(single_root_out, header_contains="Spawns", row_contains="feat", max_labels=6)
        assert single_root_cols["Spawns"] == "1"

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="feat", content=[_agent_use("b1", "staff-sdet")]),
        ])
        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
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
        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "account-1/branch-1" in out
        assert "account-2/branch-1" in out
        assert "account-1/branch-1" != "account-2/branch-1"

    def test_per_session_refused_under_multi_root(self, fake_projects, fake_config_dir_factory, capsys):
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)], per_session=True))
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
        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
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
        _mod.cmd_subagent_mix(_subagent_mix_args())
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
        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
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
        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
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

        _mod.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(extra)]))
        out = capsys.readouterr().out
        account_1 = _table_cols(out, header_contains="Spawns", row_contains="account-1/branch-1", max_labels=6)
        account_2 = _table_cols(out, header_contains="Spawns", row_contains="account-2/branch-1", max_labels=6)
        # "aaa-extra" (2 spawns) resolved-path-sorts before "zzz-active" (1
        # spawn) despite being scanned second -- account-1 must be the extra
        # root's row.
        assert account_1["Spawns"] == "2"
        assert account_2["Spawns"] == "1"


# ---------------------------------------------------------------------------
# reviewer-yield
# ---------------------------------------------------------------------------


def _reviewer_yield_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "redact": redact,
    })()


def _n_cited_reviewer_dispatches(
    proj: Path, session_id: str, subagent_type: str, count: int, *, cited_path: str = "src/foo.py",
) -> list[dict]:
    """Build `count` reviewer dispatches of `subagent_type`, each citing
    `cited_path` and each paired with its own tool_result at a distinct,
    increasing timestamp — records to prepend to the session's own list. Each
    dispatch's subagent transcript/meta.json is written as a side effect.
    Sanctioned by test-conventions §6 for the Active=N floor boundary fixture.

    The verdict text ends with a word after `cited_path`, not a period
    directly against it — _CITED_PATH_CANDIDATE_RE's char class includes
    ".", so a trailing sentence period would extract as part of the
    candidate and normalize to a different key than the edit side's clean
    file_path/notebook_path string.
    """
    records: list[dict] = []
    for i in range(count):
        tool_id = f"a{i}"
        dispatch_ts = f"2026-05-19T10:{i:02d}:00.000Z"
        result_ts = f"2026-05-19T10:{i:02d}:30.000Z"
        records.append(_asst("claude-opus-4-7", ts=dispatch_ts, content=[_agent_use(tool_id, subagent_type)]))
        records.append(_user_msg([_tool_result(tool_id, "ok")], ts=result_ts))
        _write_subagent_dispatch(
            proj, session_id, f"agent-{tool_id}", tool_id,
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": f"Found 1 issue in {cited_path} needing a fix"},
            ])],
            agent_type=subagent_type,
        )
    return records


class TestIsReviewerSubagentType:
    def test_recognizes_prefix_and_all_exact_names(self):
        """staff- prefix, plus each of the three exact-name reviewers sharing
        _REVIEWER_EXACT_NAMES with review-trace's own detection."""
        assert _mod._is_reviewer_subagent_type("staff-backend-engineer")
        assert _mod._is_reviewer_subagent_type("ciso-reviewer")
        assert _mod._is_reviewer_subagent_type("comment-discipline-reviewer")
        assert _mod._is_reviewer_subagent_type("skill-fidelity-reviewer")
        assert not _mod._is_reviewer_subagent_type("general-purpose")


class TestReviewerYield:
    def test_comment_discipline_reviewer_joins_and_classifies_zero_finding(self, fake_projects, capsys):
        """comment-discipline-reviewer is a newly-eligible _REVIEWER_EXACT_NAMES
        member (previously recognized by neither review-trace nor
        reviewer-yield) — this exercises its dispatch-to-verdict join
        end-to-end, not just the _is_reviewer_subagent_type predicate."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "comment-discipline-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No comment-discipline concerns**"}])],
            agent_type="comment-discipline-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains="comment-discipline-reviewer",
            row_startswith=True, occurrence=1,
        )
        assert cols["Dispatches"] == "1"
        assert cols["Zero"] == "1"

    def test_no_concerns_verdict_adjacent_to_bold_markers_classified_zero_finding(self, fake_projects, capsys):
        """`\\b` word-boundary anchors match identically next to whitespace or
        markdown punctuation (`**`), so a verdict wrapped in bold markers
        classifies the same as unadorned text — this guards that punctuation
        adjacency, not a "bold" feature of the regex."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="ciso-reviewer", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"
        assert cols["Zero"] == "1"

    def test_request_changes_verdict_classified_findings_found(self, fake_projects, capsys):
        """A 'Request changes' verdict classifies as findings-found, not
        unclassified — the only coverage of `_REVIEWER_REQUEST_CHANGES_RE`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Request changes\n- No test covers the retry path."},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Found"] == "1"
        assert cols["Unclass"] == "0"

    def test_approve_with_concerns_verdict_contributes_zero_to_findings_total(self, fake_projects, capsys):
        """An 'Approve with concerns' verdict carries no derivable count, so it
        lands in findings-found but contributes 0 to the total-findings sum —
        distinct from a numeric 'Found <N> issues' verdict."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "**Approve with concerns**\n- Rotate the leaked token."},
            ])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="ciso-reviewer", row_startswith=True, occurrence=1)
        assert cols["Found"] == "1"
        assert cols["Findings"] == "0"

    def test_singular_issue_verdict_classified_findings_found(self, fake_projects, capsys):
        """'Found 1 issue' (singular) — the loosened regex's singular/plural
        relaxation — classifies as findings-found with 1 total finding."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", ts="2026-05-19T10:00:00.000Z",
                content=[_agent_use("a1", "staff-backend-engineer")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Wrote findings to /tmp/x.md. Found 1 issue. Missing null check."},
            ])],
            agent_type="staff-backend-engineer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains="staff-backend-engineer",
            row_startswith=True, occurrence=1,
        )
        assert cols["Found"] == "1"
        assert cols["Findings"] == "1"

    def test_plural_issues_verdict_classified_findings_found(self, fake_projects, capsys):
        """'Found 3 issues' (plural) classifies as findings-found with 3 total findings."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Wrote findings to /tmp/x.md. Found 3 issues. Coverage gaps."},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Found"] == "1"
        assert cols["Findings"] == "3"

    def test_case_insensitive_verdict_matching(self, fake_projects, capsys):
        """An all-caps 'FOUND 2 ISSUES' verdict — the loosened regex's
        case-insensitivity relaxation — still classifies as findings-found."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst(
                "claude-opus-4-7", ts="2026-05-19T10:00:00.000Z",
                content=[_agent_use("a1", "staff-platform-engineer")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "FOUND 2 ISSUES IN THE PIPELINE CONFIG."},
            ])],
            agent_type="staff-platform-engineer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains="staff-platform-engineer",
            row_startswith=True, occurrence=1,
        )
        assert cols["Found"] == "1"
        assert cols["Findings"] == "2"

    def test_non_reviewer_subagent_type_excluded_entirely(self, fake_projects, capsys):
        """A general-purpose dispatch (not in the reviewer set) is excluded from
        aggregation entirely, even though its subagent transcript resolves fine."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "general-purpose")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Found 5 issues."}])],
            agent_type="general-purpose",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "general-purpose" not in out
        assert "No reviewer-agent dispatches found." in out

    def test_dispatch_with_no_matching_meta_json_excluded_not_unclassified(self, fake_projects, capsys):
        """A reviewer-type dispatch with no subagents/*.meta.json at all must not
        crash, and is excluded entirely (not counted as unclassified) — meta.json
        is the only signal that a subagent transcript for the dispatch exists."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "No reviewer-agent dispatches found." in out
        assert "staff-sdet" not in out

    def test_empty_subagent_transcript_classified_unclassified(self, fake_projects, capsys):
        """A resolved subagent .jsonl with zero assistant text blocks (only
        tool_use content, no text) — distinct from the missing-meta.json case
        above — must not crash, and classifies as unclassified."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"
        assert cols["Unclass"] == "1"

    def test_default_run_has_no_project_or_session_fields(self, fake_projects, capsys):
        """Default (no --redact) run: output is aggregate-only per agent type and
        carries no raw project label or session id — proving the schema's
        aggregate-only claim rather than leaving it unverified."""
        _write_jsonl(fake_projects / "distinctive-session-id.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "distinctive-session-id", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "distinctive-session-id" not in out
        assert "testrepo" not in out

    def test_redact_flag_is_true_no_op(self, fake_projects, capsys):
        """--redact produces byte-identical output to the default run, proving
        the flag is genuinely inert given the aggregate-only schema — not just
        documented as such."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args(redact=False))
        default_out = capsys.readouterr().out
        _mod.cmd_reviewer_yield(_reviewer_yield_args(redact=True))
        redacted_out = capsys.readouterr().out
        assert default_out == redacted_out

    def test_since_filter_excludes_out_of_window_dispatch(self, fake_projects, capsys):
        """A dispatch outside the --since window is excluded from the aggregate;
        one inside it is still counted."""
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts=old_ts, content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", ts=new_ts, content=[_agent_use("a2", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args(since="1d"))
        out = capsys.readouterr().out
        assert "ciso-reviewer" not in out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"

    def test_same_agent_type_dispatched_twice_accumulates_not_overwrites(self, fake_projects, capsys):
        """Two dispatches of the same subagent_type within one aggregation run
        sum into the same row instead of the second overwriting the first."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "ciso-reviewer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Found 2 issues. Missing checks."}])],
            agent_type="ciso-reviewer",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Found 3 issues. Leaked token."}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="ciso-reviewer", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "2"
        assert cols["Findings"] == "5"

    def test_unreadable_meta_json_files_excluded_and_counted(self, fake_projects, capsys):
        """An invalid-JSON meta.json and a valid-JSON meta.json missing
        toolUseId are both excluded from aggregation (not crashed) and both
        counted in the printed meta-read-errors line — distinct from the
        no-meta.json-at-all exclusion, which is not counted."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
        ])
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-a1.meta.json").write_text("{not valid json")
        (subdir / "agent-a2.meta.json").write_text(
            json.dumps({"agentType": "staff-sdet", "description": "review", "spawnDepth": 1})
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "No reviewer-agent dispatches found." in out
        assert "(2 meta.json files failed to parse, excluded)" in out

    def test_unreadable_meta_json_counted_alongside_a_resolved_table_row(self, fake_projects, capsys):
        """The meta-read-errors count is also reported when other dispatches in
        the same run resolve fine and produce a normal agent-type table — the
        counter line isn't only reachable from the all-excluded empty-table path."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        (subdir / "agent-a2.meta.json").write_text("{not valid json")
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1)
        assert cols["Dispatches"] == "1"
        assert "(1 meta.json files failed to parse, excluded)" in out

    def test_malformed_meta_field_types_excluded_and_counted(self, fake_projects, capsys):
        """A toolUseId of the wrong type (int, not str) and a "model" value
        of the wrong type (list, not str/None) are both malformed-meta.json
        cases, excluded and counted the same as invalid JSON or a missing
        toolUseId -- not silently absorbed into the "no matching meta.json"
        path, and not left to reach a caller that would use either value as
        a dict key."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
        ])
        subdir = fake_projects / "sess" / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-a1.meta.json").write_text(
            json.dumps({"agentType": "staff-sdet", "description": "d", "toolUseId": 12345, "spawnDepth": 1})
        )
        (subdir / "agent-a2.meta.json").write_text(json.dumps({
            "agentType": "staff-sdet", "description": "d", "toolUseId": "a2",
            "model": ["opus"], "spawnDepth": 1,
        }))
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        assert "No reviewer-agent dispatches found." in out
        assert "(2 meta.json files failed to parse, excluded)" in out
    # -----------------------------------------------------------------
    # Table 2: cited-path edit overlap
    # -----------------------------------------------------------------

    def test_cited_dispatch_with_no_subsequent_edit_stays_cited_not_active(self, fake_projects, capsys):
        """A dispatch with an extracted citation but no later edit anywhere
        in the session stays in Cited and is absent from Active — the
        Cited/Active denominator arithmetic, asserted on specific counts.
        Active=0 also pins the zero-Active sentinel, not a ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"
        assert cols["Rate"] == "insufficient"

    def test_zero_extraction_dispatch_stays_in_dispatches_absent_from_cited(self, fake_projects, capsys):
        """A dispatch whose verdict text yields zero extracted citations
        stays counted in table 1's Dispatches but contributes nothing to
        table 2's Cited — not entered as a citation event."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols1 = _table_cols(
            out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1,
        )
        assert cols1["Dispatches"] == "1"
        cols2 = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols2["Cited"] == "0"

    def test_findings_found_and_zero_finding_rows_are_adjacent_for_the_same_agent_type(self, fake_projects, capsys):
        """Reviewer-major, bucket-minor ordering: an agent type's two
        verdict-bucket rows in table 2 sit next to each other, not separated
        by another agent type's row."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
            _user_msg([_tool_result("a2", "ok")], ts="2026-05-19T11:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No testing concerns**"}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        lines = out.splitlines()
        found_idx = next(i for i, ln in enumerate(lines) if "staff-sdet" in ln and "findings-found" in ln)
        zero_idx = next(i for i, ln in enumerate(lines) if "staff-sdet" in ln and "zero-finding" in ln)
        assert abs(found_idx - zero_idx) == 1

    def test_unclassified_bucket_row_prints_excluded_sentinels(self, fake_projects, capsys):
        """The unclassified bucket is not scored — its row prints the
        literal 'excluded' sentinel for Cited/Active/Edited/Rate, asserted
        by bucket name rather than left implicit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "Reviewed the code."}])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "unclassified"), occurrence=2,
        )
        assert cols["Cited"] == "excluded"
        assert cols["Active"] == "excluded"
        assert cols["Edited"] == "excluded"
        assert cols["Rate"] == "excluded"

    def test_active_below_floor_of_ten_renders_insufficient(self, fake_projects, capsys):
        """Active=9 (one below the N=10 floor) renders 'insufficient', not a
        computed percentage."""
        records = _n_cited_reviewer_dispatches(fake_projects, "sess", "staff-sdet", 9)
        records.append(
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")])
        )
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "9"
        assert cols["Rate"] == "insufficient"

    def test_active_at_floor_of_ten_renders_exact_rate(self, fake_projects, capsys):
        """Active=10 (the N=10 floor) with Edited=10 renders an exact
        100.0% rate, not merely a non-'insufficient' value — pins the >=
        boundary, not just != 'insufficient'."""
        records = _n_cited_reviewer_dispatches(fake_projects, "sess", "staff-sdet", 10)
        records.append(
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")])
        )
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "10"
        assert cols["Edited"] == "10"
        assert cols["Rate"] == "100.0%"

    def test_edit_preceding_dispatch_return_does_not_count(self, fake_projects, capsys):
        """An edit timestamped after the dispatch started but before its own
        tool_result returned must not count toward Active — pins the
        threshold against the return time specifically, not dispatch start
        (an edit before dispatch start would pass this check too loosely to
        tell the two thresholds apart)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:15.000Z", content=[_edit_use("e1", path="src/foo.py")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_edit_at_exact_tool_result_timestamp_does_not_count(self, fake_projects, capsys):
        """An edit timestamped exactly at the dispatch's tool_result
        timestamp must not count — the ordering rule is strict >, not >=."""
        ts = "2026-05-19T10:00:30.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts=ts),
            _asst("claude-opus-4-7", ts=ts, content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "0"

    def test_edit_one_second_after_tool_result_counts(self, fake_projects, capsys):
        """An edit timestamped one second after the dispatch's tool_result
        does count — the earliest instant strict > admits."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:31.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_unparseable_tool_result_timestamp_excludes_active_not_cited(self, fake_projects, capsys):
        """A tool_result with an unparseable timestamp leaves the dispatch's
        Active/Edited ordering undecidable — excluded from Active regardless
        of a later edit — but does not affect Cited."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="not-a-timestamp"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_dispatch_with_no_paired_tool_result_excludes_active_not_cited(self, fake_projects, capsys):
        """A dispatch whose Agent tool_use has no paired tool_result at all
        is excluded from Active regardless of a later edit, but not from
        Cited."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_unreadable_subagent_transcript_excluded_from_cited_and_counted_in_read_error_line(
        self, fake_projects, capsys
    ):
        """A reviewer dispatch whose meta.json resolves but whose .jsonl is
        unreadable is excluded from Cited (never entered as a legitimate
        zero) and counted in a printed read-error line — distinguishable
        from the zero-extraction case, which prints no such line."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        (fake_projects / "sess" / _mod.SUBAGENT_SUBDIR / "agent-a1.jsonl").unlink()
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols1 = _table_cols(
            out, header_contains="AgentType", row_contains="staff-sdet", row_startswith=True, occurrence=1,
        )
        assert cols1["Unclass"] == "1"
        assert "(1 reviewer transcripts failed to read, excluded from Cited)" in out

    def test_parent_write_edit_counts_toward_active_and_edited(self, fake_projects, capsys):
        """A parent main-thread Write (not just Edit) to the cited path
        counts toward Active/Edited — Write is part of _CODE_WRITE_TOOLS."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _write_use("w1", "new content", path="src/foo.py"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_parent_multiedit_counts_toward_edited(self, fake_projects, capsys):
        """A parent MultiEdit (single file_path, plus an edits list) counts
        toward Edited alongside plain Edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                {"type": "tool_use", "id": "m1", "name": "MultiEdit", "input": {
                    "file_path": "src/foo.py", "edits": [{"old_string": "a", "new_string": "b"}],
                }},
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Edited"] == "1"

    def test_notebook_edit_counts_via_notebook_path_fallback(self, fake_projects, capsys):
        """NotebookEdit carries notebook_path instead of file_path — the
        edit index falls back to it rather than missing notebook edits
        entirely."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                {"type": "tool_use", "id": "n1", "name": "NotebookEdit", "input": {
                    "notebook_path": "nb/analysis.ipynb",
                }},
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in nb/analysis.ipynb needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Edited"] == "1"

    def test_subagent_authored_edit_never_counts_under_parent_only_index(self, fake_projects, capsys):
        """Pins the shipped parent-only-index behavior: an edit made inside a
        code-writer subagent transcript does not count toward Active/Edited
        even though it followed the reviewer dispatch. This does not exercise
        a subagent_type-based reviewer-write exclusion — no such mechanism
        exists in the shipped code (removed by the cost-gate fallback); the
        edit index simply never reads subagent transcripts at all."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("cw1", "code-writer")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-cw1", "cw1",
            [_asst("claude-sonnet-4-6", ts="2026-05-19T11:05:00.000Z", content=[_edit_use("e1", path="src/foo.py")])],
            agent_type="code-writer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Active"] == "0"
        assert cols["Edited"] == "0"

    def test_sibling_reviewer_findings_write_never_counts_under_parent_only_index(self, fake_projects, capsys):
        """Pins the shipped parent-only-index behavior: a sibling reviewer
        dispatched after a zero-finding dispatch writes only its own findings
        file, and that write does not satisfy Active for the zero-finding
        dispatch. This does not exercise a subagent_type-based reviewer-write
        exclusion — no such mechanism exists in the shipped code (removed by
        the cost-gate fallback); the edit index simply never reads subagent
        transcripts at all, reviewer or otherwise."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "ciso-reviewer")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "staff-sdet")]),
            _user_msg([_tool_result("a2", "ok")], ts="2026-05-19T11:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "**No concerns** src/foo.py is clean"},
            ])],
            agent_type="ciso-reviewer",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", ts="2026-05-19T11:05:00.000Z", content=[
                _write_use("w1", "No issues found.", path="/scratch/sibling-findings.md"),
                {"type": "text", "text": "**No testing concerns**"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("ciso-reviewer", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "0"

    def test_dispatch_own_write_target_excluded_from_cited(self, fake_projects, capsys):
        """The dispatch's own findings-file Write target, later echoed in
        the reviewer's own output, is excluded from Cited — a
        path-normalized set-membership check against the reviewer's own
        recorded Write target, not free-text prose matching against the
        dispatching prompt (which would risk excluding files the prompt
        also happens to name)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "No issues found.", path="/scratch/my-findings.md"),
                {"type": "text", "text": "**No concerns** Findings written to /scratch/my-findings.md, nothing else found"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "0"

    def test_prompt_named_file_legitimately_cited_in_findings_is_not_excluded(self, fake_projects, capsys):
        """Regression: a file named in the dispatching prompt ('review
        src/foo.py') that the reviewer then legitimately cites in its own
        findings must still count as Cited — a prompt routinely names the
        very files under review, so keying the self-reference exclusion off
        prompt text (rather than the reviewer's own Write target) would
        silently drop exactly the citations most likely to be real."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[
                _agent_use(
                    "a1", "staff-sdet",
                    prompt="Review the diff in src/foo.py and write findings to /scratch/findings.md",
                ),
            ]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "Missing null check.", path="/scratch/findings.md"),
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"

    def test_citation_normalizes_against_subagent_own_cwd_not_parent_cwd(self, fake_projects, capsys):
        """A relative citation in the reviewer's own output normalizes
        against the reviewer subagent's own transcript cwd, not the
        dispatching parent's — the two can diverge (ledger row A; this
        repo's own CLAUDE.md sanctions isolation:worktree for reviewer
        dispatches specifically)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            {**_asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
             "cwd": "/parent/wrong/cwd"},
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            {**_asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _edit_use("e1", path="/repo/src/foo.py"),
             ]), "cwd": "/parent/wrong/cwd"},
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [{**_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
             ]), "cwd": "/repo"}],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Edited"] == "1"

    def test_plan_file_citations_excluded_from_cited_both_home_and_repo_forms(self, fake_projects, capsys):
        """A cited plan file under ~/.claude/plans/ or an in-repo
        .claude/plans/ is excluded — a /plan-review dispatch routinely
        cites the very plan the parent session then edits, a guaranteed
        self-match with no fix-work signal."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[{
                "type": "text",
                "text": "**No concerns** See ~/.claude/plans/foo.md and .claude/plans/bar.md for context",
            }])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "0"

    def test_multi_path_citation_with_one_edited_counts_dispatch_once(self, fake_projects, capsys):
        """A dispatch citing two paths, only one of which is later edited,
        still counts once in Cited/Active/Edited — not once per cited
        path."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 2 issues in src/foo.py and src/bar.py needing review"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_citation_from_write_blob_content_is_recognized(self, fake_projects, capsys):
        """A path cited only in the reviewer's own Write blob (its findings
        body), not in the last assistant text, is still recognized — both
        citation sources are scanned, not just the final text block."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "Reviewed src/foo.py, no issues found."),
                {"type": "text", "text": "**No concerns**"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "zero-finding"), occurrence=2,
        )
        assert cols["Cited"] == "1"

    def test_path_cited_in_both_text_and_write_blob_counts_once(self, fake_projects, capsys):
        """A path cited in both the last assistant text and the reviewer's
        own Write blob dedupes to one citation — Cited/Active/Edited stay
        dispatch-level, not inflated by the overlap between the two
        sources."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_edit_use("e1", path="src/foo.py")]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", "Reviewed src/foo.py, confirmed the issue."),
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        out = capsys.readouterr().out
        cols = _table_cols(
            out, header_contains="AgentType", row_contains=("staff-sdet", "findings-found"), occurrence=2,
        )
        assert cols["Cited"] == "1"
        assert cols["Active"] == "1"
        assert cols["Edited"] == "1"

    def test_table_two_output_deterministic_across_two_runs(self, fake_projects, capsys):
        """Table 2's output is byte-identical across two runs over the same
        corpus — reviewer-major/bucket-minor ordering is name-sorted, not
        dependent on dict-iteration order."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[_agent_use("a2", "ciso-reviewer")]),
            _user_msg([_tool_result("a2", "ok")], ts="2026-05-19T11:00:30.000Z"),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": "Found 1 issue in src/foo.py needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a2", "a2",
            [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": "**No CISO concerns**"}])],
            agent_type="ciso-reviewer",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        first_out = capsys.readouterr().out
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        second_out = capsys.readouterr().out
        assert first_out == second_out

    def test_cited_and_edited_paths_never_appear_in_output(self, fake_projects, capsys):
        """A distinctive sentinel path, cited and later edited, must not
        reach stdout or stderr — cited-path candidates are held only as
        sha256 digests, so no path can print by construction. Scoped to the
        table bodies: the pre-existing --projects scope-header leak (ledger
        row O) is a separate, unfixed channel this plan does not fix."""
        sentinel_path = "SENTINEL-ROOT/SENTINEL-PROJ/x.py"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _edit_use("e1", path=sentinel_path),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                {"type": "text", "text": f"Found 1 issue in {sentinel_path} needing a fix"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        captured = capsys.readouterr()
        assert sentinel_path not in captured.out
        assert sentinel_path not in captured.err

    def test_cited_via_write_blob_and_edited_paths_never_appear_in_output(self, fake_projects, capsys):
        """The same non-leakage guarantee as
        test_cited_and_edited_paths_never_appear_in_output, but with the
        sentinel path cited only through a reviewer's own Write blob
        (input.content), not the last assistant text — both citation-
        extraction code paths must not leak a raw path."""
        sentinel_path = "SENTINEL-ROOT/SENTINEL-PROJ/y.py"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", ts="2026-05-19T10:00:00.000Z", content=[_agent_use("a1", "staff-sdet")]),
            _user_msg([_tool_result("a1", "ok")], ts="2026-05-19T10:00:30.000Z"),
            _asst("claude-opus-4-7", ts="2026-05-19T11:00:00.000Z", content=[
                _edit_use("e1", path=sentinel_path),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a1", "a1",
            [_asst("claude-sonnet-4-6", content=[
                _write_use("w1", f"Reviewed {sentinel_path}, confirmed the issue."),
                {"type": "text", "text": "**No concerns**"},
            ])],
            agent_type="staff-sdet",
        )
        _mod.cmd_reviewer_yield(_reviewer_yield_args())
        captured = capsys.readouterr()
        assert sentinel_path not in captured.out
        assert sentinel_path not in captured.err


class TestExtractCitedPaths:
    """_extract_cited_paths(text) -> set[str]: a pure tokenizer over one
    bounded, length-capped character class. Deliberately unselective — a
    bare word matches too — so these tests document what the function
    returns *before* _normalize_cited_path filters it, not "real paths only."
    """

    def test_extracts_a_slash_containing_candidate_verbatim(self):
        result = _mod._extract_cited_paths("claude/.claude/scripts/transcript-analysis.py:2549")
        assert result == {"claude/.claude/scripts/transcript-analysis.py:2549"}

    def test_extracts_a_tilde_path_without_expanding_it(self):
        """Extraction is purely lexical tokenization — ~-expansion is
        _normalize_cited_path's job, not this function's."""
        result = _mod._extract_cited_paths("~/.claude/plans/x.md")
        assert result == {"~/.claude/plans/x.md"}

    def test_bare_prose_words_are_extracted_too(self):
        """No `/` or `.` is required by the regex — separator filtering
        happens downstream in _normalize_cited_path's step 2, not here."""
        result = _mod._extract_cited_paths("no findings here")
        assert result == {"no", "findings", "here"}

    def test_real_path_recovered_from_surrounding_prose(self):
        text = "Reviewed the diff; see claude/.claude/scripts/transcript-analysis.py:2455 for the join."
        result = _mod._extract_cited_paths(text)
        assert "claude/.claude/scripts/transcript-analysis.py:2455" in result

    def test_non_ascii_path_extracted_verbatim(self):
        """_CITED_PATH_CANDIDATE_RE's \\w is Unicode-by-default (no re.ASCII
        flag), relied on deliberately so a non-ASCII path segment is not
        split at the boundary of its non-ASCII characters."""
        result = _mod._extract_cited_paths("see café/日本語/foo.py for the fix")
        assert "café/日本語/foo.py" in result

    def test_empty_text_returns_empty_set(self):
        assert _mod._extract_cited_paths("") == set()

    def test_candidate_longer_than_the_cap_splits_into_bounded_chunks(self):
        """A single unbroken run longer than _CITED_PATH_CANDIDATE_MAX_CHARS
        is not dropped or truncated silently — the greedy bounded quantifier
        emits consecutive capped matches that together cover the whole run."""
        max_chars = _mod._CITED_PATH_CANDIDATE_MAX_CHARS
        run = "a" * (max_chars + 50)
        result = _mod._extract_cited_paths(run)
        assert result == {"a" * max_chars, "a" * 50}

    def test_adversarial_slash_run_completes_under_one_second(self):
        """A 100 KB non-matching slash-heavy line (no terminating token) must
        not trigger catastrophic backtracking. _CITED_PATH_CANDIDATE_RE's
        flat character class is linear-time regardless of input shape, unlike
        a nested-quantifier "(?:[\\w.-]+/)+[\\w.-]+" pattern, which hangs on
        this exact input. A hard deadline is required rather than a
        post-hoc wall-clock assertion: catastrophic backtracking hangs
        rather than returns slowly, so a measured-after-the-fact assertion
        can never fail on the failure mode it exists to catch."""
        if not hasattr(signal, "SIGALRM"):
            pytest.skip("signal.alarm is POSIX-only")
        adversarial_input = "a/" * 50_000  # 100,000 chars, no terminating token

        def _raise_timeout(signum, frame):
            raise TimeoutError("_extract_cited_paths did not complete within 1 second")

        previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
        signal.alarm(1)
        try:
            _mod._extract_cited_paths(adversarial_input)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)


class TestNormalizeCitedPath:
    """_normalize_cited_path(candidate, cwd) -> key | None: the six ordered,
    lexical-only normalization steps that turn a raw extracted candidate
    into (or discard it from) the edit-index join key."""

    def _key(self, path: str) -> str:
        """The same digest _normalize_cited_path computes, for expected-value
        construction — not a re-implementation of any normalization step."""
        return _mod.hashlib.sha256(path.encode()).hexdigest()[:16]

    def test_tilde_path_expands_against_home_before_relative_resolution(self, monkeypatch, tmp_path):
        """Pins step 3 (~-expansion) running before step 4 (relative-path
        resolution): if step 4 ran first, '~/.claude/plans/x.md' would be
        joined onto `cwd` unexpanded rather than resolved against $HOME."""
        home = tmp_path / "home" / "reviewer"
        monkeypatch.setenv("HOME", str(home))
        result = _mod._normalize_cited_path("~/.claude/plans/x.md", cwd="/repo/unrelated/cwd")
        assert result == self._key(f"{home}/.claude/plans/x.md")

    def test_unexpandable_other_user_tilde_is_discarded(self, monkeypatch, tmp_path):
        """A '~otheruser/...' candidate is discarded outright, not resolved
        via a pwd.getpwnam directory-service lookup — expanduser leaves it
        starting with '~' when the user doesn't exist locally."""
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "reviewer"))
        result = _mod._normalize_cited_path(
            "~definitely-not-a-real-account-xyz/notes.md", cwd="/repo"
        )
        assert result is None

    def test_private_tmp_collapses_to_tmp(self):
        result = _mod._normalize_cited_path("/private/tmp/scratch/report.md", cwd="/repo")
        assert result == self._key("/tmp/scratch/report.md")

    def test_trailing_line_suffix_stripped(self):
        with_line = _mod._normalize_cited_path("src/foo.py:42", cwd="/repo")
        without_line = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert with_line == without_line == self._key("/repo/src/foo.py")

    def test_trailing_line_col_suffix_stripped(self):
        with_line_col = _mod._normalize_cited_path("src/foo.py:42:7", cwd="/repo")
        without_suffix = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert with_line_col == without_suffix == self._key("/repo/src/foo.py")

    def test_relative_and_absolute_citations_of_the_same_file_match(self):
        relative = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        absolute = _mod._normalize_cited_path("/repo/src/foo.py", cwd="/anywhere")
        assert relative == absolute == self._key("/repo/src/foo.py")

    def test_worktree_rooted_absolute_path_matches_plain_repo_relative(self):
        """A worktree-rooted absolute citation normalizes to the same key as
        a plain repo-relative one once the worktree-prefix segment is
        stripped — that's the join key's whole purpose."""
        worktree_rooted = _mod._normalize_cited_path(
            "/repo/.claude/worktrees/gh558-branch/src/foo.py", cwd="/irrelevant"
        )
        plain_relative = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert worktree_rooted == plain_relative == self._key("/repo/src/foo.py")

    def test_absolute_citation_matches_worktree_rooted_edit_on_a_different_branch(self):
        """Worktree-prefix stripping is branch-name-agnostic by construction
        (`.claude/worktrees/[^/]+/` matches any single branch segment): an
        edit made in a different branch's worktree of the *same* repo
        normalizes to the same key as a plain absolute citation of that
        repo's file — the join key carries no branch identity."""
        absolute_citation = _mod._normalize_cited_path("/repo/src/foo.py", cwd="/repo")
        worktree_edit = _mod._normalize_cited_path(
            "src/foo.py", cwd="/repo/.claude/worktrees/some-other-branch"
        )
        assert absolute_citation == worktree_edit == self._key("/repo/src/foo.py")

    def test_two_repos_sharing_a_relative_suffix_do_not_match(self):
        repo_a = _mod._normalize_cited_path("src/foo.py", cwd="/projects/repo-a")
        repo_b = _mod._normalize_cited_path("src/foo.py", cwd="/projects/repo-b")
        assert repo_a != repo_b

    def test_nested_worktree_stripped_to_fixpoint(self):
        """An isolation:worktree agent spawned under a worktree-anchored
        parent leaves two '.claude/worktrees/<branch>/' segments in the
        path; both must be stripped, not just the first."""
        nested = _mod._normalize_cited_path(
            "/repo/.claude/worktrees/outer-branch/.claude/worktrees/inner-agent/src/foo.py",
            cwd="/irrelevant",
        )
        assert nested == self._key("/repo/src/foo.py")

    def test_slash_containing_branch_slug_under_strips_to_first_segment(self):
        """A hand-created branch named 'docs/x' (violating this repo's own
        single-segment branch-slug convention) is not losslessly decidable
        from the path alone with zero filesystem access — the normalizer
        takes only the first segment ('docs') as the branch, leaving 'x/'
        as an unstripped leftover. Documents the bias; does not crash."""
        result = _mod._normalize_cited_path(
            "/repo/.claude/worktrees/docs/x/src/foo.py", cwd="/irrelevant"
        )
        assert result == self._key("/repo/x/src/foo.py")

    def test_dotdot_resolves_against_unstripped_cwd_before_worktree_stripping(self):
        """'../../../.venv/bin/pytest' (this repo's own CLAUDE.md idiom) means
        three levels above the *worktree* — resolving it against the
        unstripped `cwd` (not a pre-stripped one) is what makes that true."""
        result = _mod._normalize_cited_path(
            "../../../.venv/bin/pytest", cwd="/repo/.claude/worktrees/gh558-branch"
        )
        assert result == self._key("/repo/.venv/bin/pytest")

    def test_bare_filename_with_no_directory_separator_is_rejected(self):
        assert _mod._normalize_cited_path("SKILL.md", cwd="/repo") is None

    def test_no_resolvable_repo_still_normalizes(self):
        """A cwd with no .claude/worktrees marker at all (a plain, non-worktree
        checkout) is not an error case — the function needs no repo
        detection, only lexical joining."""
        result = _mod._normalize_cited_path("src/foo.py", cwd="/home/reviewer/plain-checkout")
        assert result == self._key("/home/reviewer/plain-checkout/src/foo.py")

    def test_non_ascii_path_round_trips_through_extraction_and_normalization(self):
        """A non-ASCII path (café/日本語-style) survives extraction and
        normalization to a stable key — pins that a future 'safety' narrowing
        of _CITED_PATH_CANDIDATE_RE to ASCII-only would silently break
        non-ASCII paths with no test catching it."""
        candidates = _mod._extract_cited_paths("see café/日本語/foo.py for the fix")
        candidate = next(c for c in candidates if "/" in c)
        result = _mod._normalize_cited_path(candidate, cwd="/repo")
        assert result == self._key("/repo/café/日本語/foo.py")


class TestBuildToolResultTsMap:
    """_build_tool_result_ts_map(records, since_ts) -> {tool_use_id: timestamp}:
    the tool_result side of reviewer-yield's Active/Edited ordering join."""

    def test_tool_result_on_user_type_record_maps_to_its_own_timestamp(self):
        ts = "2026-05-19T10:00:30.000Z"
        records = [_user_msg([_tool_result("a1", "ok")], ts=ts)]
        result = _mod._build_tool_result_ts_map(records, since_ts=None)
        assert result == {"a1": _mod._parse_ts(ts)}

    def test_since_excludes_out_of_window_tool_result(self):
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        records = [
            _user_msg([_tool_result("old", "ok")], ts=old_ts),
            _user_msg([_tool_result("new", "ok")], ts=new_ts),
        ]
        since_ts = _mod._parse_ts("2050-01-01T00:00:00.000Z")
        result = _mod._build_tool_result_ts_map(records, since_ts)
        assert result == {"new": _mod._parse_ts(new_ts)}

    def test_unparseable_timestamp_omitted(self):
        records = [_user_msg([_tool_result("a1", "ok")], ts="not-a-timestamp")]
        result = _mod._build_tool_result_ts_map(records, since_ts=None)
        assert result == {}


class TestIndexParentEdits:
    """_index_parent_edits(records, since_ts) -> {normalized_key: latest_ts}:
    the parent-main-thread edit side of reviewer-yield's overlap join."""

    def test_write_tool_use_produces_keyed_entry(self):
        ts = "2026-05-19T11:00:00.000Z"
        records = [
            {**_asst("claude-opus-4-7", ts=ts, content=[_write_use("w1", "content", path="src/foo.py")]),
             "cwd": "/repo"},
        ]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_edit_tool_use_produces_keyed_entry(self):
        ts = "2026-05-19T11:00:00.000Z"
        records = [
            {**_asst("claude-opus-4-7", ts=ts, content=[_edit_use("e1", path="src/foo.py")]), "cwd": "/repo"},
        ]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_multiedit_tool_use_produces_keyed_entry(self):
        ts = "2026-05-19T11:00:00.000Z"
        multiedit = {"type": "tool_use", "id": "m1", "name": "MultiEdit", "input": {
            "file_path": "src/foo.py", "edits": [{"old_string": "a", "new_string": "b"}],
        }}
        records = [{**_asst("claude-opus-4-7", ts=ts, content=[multiedit]), "cwd": "/repo"}]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("src/foo.py", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_notebook_edit_tool_use_produces_keyed_entry(self):
        """NotebookEdit carries notebook_path, not file_path — the index's
        _code_write_target_path fallback."""
        ts = "2026-05-19T11:00:00.000Z"
        notebook_edit = {"type": "tool_use", "id": "n1", "name": "NotebookEdit", "input": {
            "notebook_path": "nb/analysis.ipynb",
        }}
        records = [{**_asst("claude-opus-4-7", ts=ts, content=[notebook_edit]), "cwd": "/repo"}]
        result = _mod._index_parent_edits(records, since_ts=None)
        key = _mod._normalize_cited_path("nb/analysis.ipynb", cwd="/repo")
        assert result == {key: _mod._parse_ts(ts)}

    def test_since_excludes_out_of_window_edit(self):
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        records = [
            {**_asst("claude-opus-4-7", ts=old_ts, content=[_edit_use("e1", path="src/old.py")]), "cwd": "/repo"},
            {**_asst("claude-opus-4-7", ts=new_ts, content=[_edit_use("e2", path="src/new.py")]), "cwd": "/repo"},
        ]
        since_ts = _mod._parse_ts("2050-01-01T00:00:00.000Z")
        result = _mod._index_parent_edits(records, since_ts)
        assert list(result) == [_mod._normalize_cited_path("src/new.py", cwd="/repo")]


class TestReviewerYieldCitedKeys:
    """_reviewer_yield_cited_keys(last_assistant_text, write_content_blobs, cwd,
    self_ref_keys) -> set[key]: the citation-extraction join key set for one
    reviewer dispatch."""

    def test_text_channel_candidate_surfaces(self):
        result = _mod._reviewer_yield_cited_keys(
            "Found 1 issue in src/foo.py needing a fix", [], cwd="/repo", self_ref_keys=set(),
        )
        assert result == {_mod._normalize_cited_path("src/foo.py", cwd="/repo")}

    def test_blob_channel_candidate_surfaces(self):
        result = _mod._reviewer_yield_cited_keys(
            "", ["Reviewed src/foo.py, no issues found."], cwd="/repo", self_ref_keys=set(),
        )
        assert result == {_mod._normalize_cited_path("src/foo.py", cwd="/repo")}

    def test_self_ref_key_excluded(self):
        self_ref_key = _mod._normalize_cited_path("/scratch/findings.md", cwd="/repo")
        result = _mod._reviewer_yield_cited_keys(
            "Findings written to /scratch/findings.md", [], cwd="/repo", self_ref_keys={self_ref_key},
        )
        assert result == set()

    def test_plan_file_candidate_excluded(self):
        result = _mod._reviewer_yield_cited_keys(
            "See .claude/plans/foo.md for context", [], cwd="/repo", self_ref_keys=set(),
        )
        assert result == set()

    def test_same_path_in_both_channels_dedupes_to_one_key(self):
        result = _mod._reviewer_yield_cited_keys(
            "Found 1 issue in src/foo.py needing a fix",
            ["Reviewed src/foo.py, confirmed the issue."],
            cwd="/repo", self_ref_keys=set(),
        )
        assert result == {_mod._normalize_cited_path("src/foo.py", cwd="/repo")}


class TestDispatchSelfReferenceKeys:
    """_dispatch_self_reference_keys(write_target_paths, transcript_cwd) ->
    set[key]: the reviewer's own Write-target self-reference exclusion set."""

    def test_normalizes_each_write_target_path(self):
        result = _mod._dispatch_self_reference_keys(
            ["/scratch/findings.md", "src/foo.py"], transcript_cwd="/repo",
        )
        assert result == {
            _mod._normalize_cited_path("/scratch/findings.md", cwd="/repo"),
            _mod._normalize_cited_path("src/foo.py", cwd="/repo"),
        }

    def test_empty_write_target_paths_returns_empty_set(self):
        assert _mod._dispatch_self_reference_keys([], transcript_cwd="/repo") == set()


# ---------------------------------------------------------------------------
# skill-pair
# ---------------------------------------------------------------------------

# Fixed timestamp used for tests 1–6, 8–10: 2026-05-12T12:00:00Z → ISO 2026-W20
_TS_FIXED = "2026-05-12T12:00:00Z"
# Boundary timestamps for test 7 (ISO week boundary Sun → Mon)
_TS_SUNDAY = "2026-05-17T23:59:59Z"   # ISO 2026-W20 (Sunday = last day of W20)
_TS_MONDAY = "2026-05-18T00:00:01Z"   # ISO 2026-W21 (Monday = first day of W21)


def _skill_pair_args(leader="plan-it", follower="plan-review", *, projects="*", this_repo=False,
                      exclude_projects=None, branches=None):
    return type("A", (), {
        "leader": leader,
        "follower": follower,
        "projects": projects,
        "this_repo": this_repo,
        "exclude_projects": exclude_projects,
        "branches": branches,
    })()


class TestSkillPair:
    def test_empty_jsonl_prints_no_data(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _mod.cmd_skill_pair(_skill_pair_args())
        assert "No data found." in capsys.readouterr().out

    def test_leader_only_session_counted_with_zero_followers(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"
        assert cols["Side"] == "0"
        assert "0.0%" in cols["Pair%"]

    def test_leader_plus_main_follower_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "1"
        assert "100.0%" in cols["Pair%"]

    def test_leader_plus_sidechain_only_follower(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"  # no main-thread follower
        assert cols["Side"] == "1"

    def test_both_main_and_sidechain_follower_counts_main_only(self, fake_projects, capsys):
        """Session with both main and sidechain follower hits counts in Main, NOT Side."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s3", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Main"] == "1"
        assert cols["Side"] == "0"  # sidechain-only requires no main hit

    def test_multiple_leader_hits_count_as_one_session(self, fake_projects, capsys):
        """Three leader invocations in one session → Lead=1, not 3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _skill_use("s2", "plan-it"),
                _skill_use("s3", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

    def test_iso_week_boundary_sunday_vs_monday(self, fake_projects, capsys):
        """Leader on Sun 23:59:59 UTC → W20; leader on Mon 00:00:01 UTC → W21."""
        proj2 = fake_projects.parent / "-home-user-testrepo2"
        proj2.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess-sun.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_SUNDAY, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_jsonl(proj2 / "sess-mon.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_MONDAY, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if ln.startswith("2026-W")]
        bins = [ln.split()[0] for ln in data_lines]
        assert "2026-W20" in bins
        assert "2026-W21" in bins
        assert bins != ["2026-W20"]  # both bins present as distinct rows

    def test_projects_glob_excludes_unmatched_project(self, fake_projects, capsys):
        """Only project dirs matching --projects are included."""
        proj_b = fake_projects.parent / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_jsonl(proj_b / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        # Only match the first project dir
        _mod.cmd_skill_pair(_skill_pair_args(projects="-home-user-testrepo"))
        out = capsys.readouterr().out
        # Only 1 leader session (the included project)
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

    def test_exclude_projects_glob_omits_matching_dir(self, fake_projects, capsys):
        """Project dirs matching --exclude-projects are skipped even when also matching --projects=*."""
        proj_eval = fake_projects.parent / "-tmp-claude-eval-abc123"
        proj_normal = fake_projects.parent / "-home-user-normalrepo"
        proj_eval.mkdir(parents=True)
        proj_normal.mkdir(parents=True)
        # Normal project: 1 leader session
        _write_jsonl(proj_normal / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        # Eval project: 2 leader sessions — should be excluded
        _write_jsonl(proj_eval / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        _write_jsonl(proj_eval / "sess2.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s3", "plan-it"),
            ]),
        ])
        # fake_projects itself has no sessions for this test; use exclude on the eval dir
        _mod.cmd_skill_pair(_skill_pair_args(exclude_projects="-tmp-claude-eval-*"))
        out = capsys.readouterr().out
        # Only 1 session from the normal project; eval sessions excluded
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

    def test_branches_filter_excludes_other_branch(self, fake_projects, capsys):
        """With --branches=branch-a, only sessions on branch-a contribute."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="branch-a", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="branch-a", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _write_jsonl(fake_projects / "sess2.jsonl", [
            _asst("claude-sonnet-4-6", branch="branch-b", ts=_TS_FIXED, content=[
                _skill_use("s3", "plan-it"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args(branches="branch-a"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"  # branch-a only
        assert cols["Main"] == "1"  # follower on branch-a


# ---------------------------------------------------------------------------
# pr-link (stubbed gh)
# ---------------------------------------------------------------------------


class TestPrLink:
    def test_stubbed_gh_produces_row_with_pr_number(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-pr"),
        ])

        def fake_run(cmd, *_, **__):
            class R:
                stdout = ""
                returncode = 0

            r = R()
            if "list" in cmd:
                r.stdout = json.dumps([{"number": 77}])
            elif "issues" in " ".join(cmd):
                r.stdout = "alice\nbob\n"
            elif "pulls" in " ".join(cmd):
                r.stdout = "alice\n"
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {
            "repo": "owner/repo", "branches": "feat-pr", "author": "alice",
            "projects": "*", "this_repo": False,
        })()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "77" in out
        assert "feat-pr" in out

    def test_missing_gh_binary_shows_error_marker(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat-x"),
        ])

        def fake_run(*_, **__):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {"repo": "owner/repo", "branches": "feat-x", "author": "", "projects": "*", "this_repo": False})()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "gh-err" in out or "?" in out

    def test_branch_with_no_pr_shows_none(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="no-pr-branch"),
        ])

        def fake_run(cmd, *_, **__):
            class R:
                stdout = "[]"
                returncode = 0

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {
            "repo": "owner/repo", "branches": "no-pr-branch", "author": "",
            "projects": "*", "this_repo": False,
        })()
        _mod.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "none" in out


# ---------------------------------------------------------------------------
# commit-gate
# ---------------------------------------------------------------------------


def _gate_args(skill: str, *, by_permission_mode: bool = False, projects: str = "*", this_repo: bool = False,
               exclude_projects: str | None = None, branches: str | None = None):
    """Build a minimal argparse.Namespace for cmd_commit_gate."""
    return type("A", (), {
        "skill": skill,
        "by_permission_mode": by_permission_mode,
        "projects": projects,
        "this_repo": this_repo,
        "exclude_projects": exclude_projects,
        "branches": branches,
    })()


class TestCommitGate:
    def test_empty_jsonl_prints_no_data(self, fake_projects, capsys):
        """Empty project dir → 'No data found.' and implicit exit 0."""
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_single_session_no_commits_one_skill_invocation(self, fake_projects, capsys):
        """One skill call, no commits: 1 session, 0 commits, 1 invocation, rate > 0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        # 1 session, 0 commits, 1 skill invocation; rate = 1000/1 = 1000.0
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["sessions"] == "1"
        assert int(cols["commits"]) == 0
        assert int(cols["skill-inv"]) == 1

    def test_commit_after_skill_is_gated(self, fake_projects, capsys):
        """Skill invocation before commit in same session → commits-with-prior-skill = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'x'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"
        assert cols["w-skill"] == "1"
        assert cols["wo-skill"] == "0"
        assert cols["no-verify"] == "0"

    def test_commit_before_skill_is_ungated(self, fake_projects, capsys):
        """Commit before any skill invocation → commits-without-prior-skill = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'x'")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"
        assert cols["w-skill"] == "0"
        assert cols["wo-skill"] == "1"

    def test_two_commits_one_skill_between_consumes_only_first(self, fake_projects, capsys):
        """Skill between two commits: first commit is gated, second is not (skill consumed)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'first'")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:02:00.000Z",
                  content=[_bash_use("b2", "git commit -m 'second'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "2"
        assert cols["w-skill"] == "1"  # second commit, after skill
        assert cols["wo-skill"] == "1"  # first commit, no prior skill

    def test_no_verify_counted_in_commits_and_no_verify_but_not_gated(self, fake_projects, capsys):
        """git commit --no-verify after /code-review: counted in commits + no-verify, NOT in w-skill."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit --no-verify -m 'bypass'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"
        assert cols["w-skill"] == "0"  # bypass is NOT credited as gated
        assert cols["wo-skill"] == "1"
        assert cols["no-verify"] == "1"

    def test_amend_counted_as_commit(self, fake_projects, capsys):
        """git commit --amend is counted in the commits total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit --amend --no-edit")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "1"

    def test_git_commit_tree_not_counted(self, fake_projects, capsys):
        """git commit-tree must NOT match the commit regex (trailing word boundary)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit-tree HEAD^{tree} -m 'msg'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["commits"] == "0"  # commit-tree must not be counted

    def test_sidechain_skill_not_counted(self, fake_projects, capsys):
        """A /code-review call inside a sidechain record must not credit the main thread."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  sidechain=True, content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'main'")]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["skill-inv"] == "0"  # sidechain skill not counted
        assert cols["w-skill"] == "0"    # sidechain skill must not gate the commit
        assert cols["wo-skill"] == "1"

    def test_skill_name_exact_match_plugin_prefix_not_matched(self, fake_projects, capsys):
        """'skill-management:skill-review' does NOT match commit-gate skill-review (byte-equal)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "skill-management:skill-review")]),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b1", "git commit -m 'x'")]),
        ])
        args = _gate_args("skill-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["skill-inv"] == "0"  # plugin-prefixed name must not match
        assert cols["w-skill"] == "0"

    def test_same_record_skill_before_commit_is_gated(self, fake_projects, capsys):
        """Within one assistant record, Skill block at lower index than Bash → commit is gated."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _skill_use("s1", "code-review"),
                      _bash_use("b1", "git commit -m 'x'"),
                  ]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["w-skill"] == "1"
        assert cols["wo-skill"] == "0"

    def test_same_record_commit_before_skill_is_ungated(self, fake_projects, capsys):
        """Within one assistant record, Bash block at lower index than Skill → commit is ungated."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _bash_use("b1", "git commit -m 'x'"),
                      _skill_use("s1", "code-review"),
                  ]),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert cols["w-skill"] == "0"
        assert cols["wo-skill"] == "1"

    def test_by_permission_mode_splits_auto_and_default(self, fake_projects, capsys):
        """Two sessions, one with permissionMode 'auto', one without → two rows per bin."""
        proj2 = fake_projects.parent / "-home-user-repo2"
        proj2.mkdir(parents=True)
        # Session with permissionMode='auto'
        auto_rec = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z")
        auto_rec["permissionMode"] = "auto"
        _write_jsonl(fake_projects / "auto.jsonl", [auto_rec])
        # Session without permissionMode (→ 'default')
        _write_jsonl(proj2 / "default.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T11:00:00.000Z"),
        ])
        args = _gate_args("code-review", by_permission_mode=True)
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        # Verify both mode values appear; use column-value membership (not positional
        # split-index) so a future column insertion doesn't silently read the wrong field.
        assert any("auto" in ln.split() for ln in data_lines), "expected an 'auto' mode row"
        assert any("default" in ln.split() for ln in data_lines), "expected a 'default' mode row"

    def test_by_permission_mode_sparse_picks_first_carrying_record(self, fake_projects, capsys):
        """permissionMode on a mid-session record (not the first) is still discovered."""
        rec1 = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z")
        rec2 = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z")
        rec2["permissionMode"] = "bypassPermissions"
        _write_jsonl(fake_projects / "sess.jsonl", [rec1, rec2])
        args = _gate_args("code-review", by_permission_mode=True)
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        # The second record carries the mode; our "first carrying record" rule picks it.
        assert "bypassPermissions" in data_lines[0]

    def test_by_permission_mode_extracted_from_user_record(self, fake_projects, capsys):
        """permissionMode on a user record (the real-world shape) is picked up.

        Surfaced by the trend audit: empirically, transcripts carry permissionMode
        on user records (initial session-meta record), not on assistant records.
        Filtering extraction to assistant records misses every real session.
        """
        user_rec = _user_msg("hi", branch="main")
        user_rec["timestamp"] = "2026-05-19T10:00:00.000Z"
        user_rec["permissionMode"] = "auto"
        asst_rec = _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:01.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [user_rec, asst_rec])
        args = _gate_args("code-review", by_permission_mode=True)
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1
        assert "auto" in data_lines[0]

    def test_iso_week_boundary_assigns_correct_bin(self, fake_projects, capsys):
        """Sessions just before and just after a Monday 00:00 UTC get different ISO-week bins."""
        proj2 = fake_projects.parent / "-home-user-repo3"
        proj2.mkdir(parents=True)
        # 2026-05-17 (Sunday) 23:59:59 UTC → W20
        # 2026-05-18 (Monday) 00:00:01 UTC → W21
        _write_jsonl(fake_projects / "sunday.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-17T23:59:59.000Z"),
        ])
        _write_jsonl(proj2 / "monday.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-18T00:00:01.000Z"),
        ])
        args = _gate_args("code-review")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        bins = [ln.split()[0] for ln in out.splitlines() if "2026-W" in ln]
        assert "2026-W20" in bins
        assert "2026-W21" in bins

    def test_projects_glob_filters_by_project_dir(self, fake_projects, capsys):
        """--projects glob restricts which project dirs are walked."""
        other_proj = fake_projects.parent / "-home-user-otherrepo"
        other_proj.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        _write_jsonl(other_proj / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        # Only match fake_projects dir
        args = _gate_args("code-review", projects="-home-user-testrepo")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert int(cols["sessions"]) == 1  # only 1 session

    def test_exclude_projects_omits_matching_dir(self, fake_projects, capsys):
        """--exclude-projects glob removes matching dirs even if --projects would include them."""
        eval_proj = fake_projects.parent / "-tmp-claude-eval-run1"
        eval_proj.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        _write_jsonl(eval_proj / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        args = _gate_args("code-review", exclude_projects="-tmp-claude-eval-*")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="sessions", row_contains="2026-W")
        assert int(cols["sessions"]) == 1  # only the non-excluded session

    def test_branches_filter_skips_sessions_with_no_matching_branch(self, fake_projects, capsys):
        """--branches filter: sessions whose main-thread records are all on other branches are skipped."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-a", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feat-b", ts="2026-05-19T10:01:00.000Z"),
        ])
        args = _gate_args("code-review", branches="feat-a")
        _mod.cmd_commit_gate(args)
        out = capsys.readouterr().out
        # feat-a is present → session counted
        data_lines = [ln for ln in out.splitlines() if "2026-W" in ln]
        assert len(data_lines) == 1

        # Session with ONLY feat-b branch is excluded
        proj2 = fake_projects.parent / "-home-user-repo4"
        proj2.mkdir(parents=True)
        _write_jsonl(proj2 / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-b", ts="2026-05-19T10:00:00.000Z"),
        ])
        args2 = _gate_args("code-review", branches="feat-a")
        _mod.cmd_commit_gate(args2)
        out2 = capsys.readouterr().out
        # Still only 1 session (feat-b-only session excluded)
        cols2 = _table_cols(out2, header_contains="sessions", row_contains="2026-W")
        assert int(cols2["sessions"]) == 1


# ---------------------------------------------------------------------------
# review-trace
# ---------------------------------------------------------------------------


def _hook_deny(
    hook_name: str, *, stringified: bool = False, branch: str = "main", ts: str | None = None
) -> dict:
    """Build an attachment/hook_blocking_error record using the real transcript shape.

    Real transcripts nest the human-readable denial text in a "blockingError" key
    inside the blockingError dict (alongside a "command" key).

    When stringified=True, the outer blockingError value is a JSON-encoded string
    of that dict rather than the dict itself (as seen in some real transcripts).

    branch/ts mirror the sibling _hook_deny_current — a synthetic attachment
    denial carries its own gitBranch/timestamp too, so tests can distinguish an
    implementation that reads the record's own branch from one that only
    exercises the carry-forward path.
    """
    human_message = f"Hook '{hook_name}' blocked the operation"
    error_dict = {"blockingError": human_message, "command": "git commit -m x"}
    blocking_error = json.dumps(error_dict) if stringified else error_dict
    rec: dict = {
        "type": "attachment",
        "gitBranch": branch,
        "attachment": {
            "type": "hook_blocking_error",
            "hookName": hook_name,
            "toolUseID": f"toolu_{hook_name[:8]}",
            "blockingError": blocking_error,
        },
    }
    if ts:
        rec["timestamp"] = ts
    return rec


def _hook_deny_current(
    message: str,
    *,
    tool_id: str = "toolu_cur",
    ts: str | None = None,
    branch: str = "main",
    tool_denial_kind: str | None = None,
    is_error: bool = True,
) -> dict:
    """Build a current-format hook denial.

    Newer Claude Code transcripts no longer emit a hook_blocking_error
    attachment record — a denial surfaces only as a user record whose
    tool_result block carries is_error and the denial text. tool_denial_kind
    mirrors the real toolDenialKind field, which lives on this parent user
    record, not on the tool_result block itself.
    """
    rec: dict = {
        "type": "user",
        "gitBranch": branch,
        "isSidechain": False,
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id,
             "content": message, "is_error": is_error},
        ]},
    }
    if ts:
        rec["timestamp"] = ts
    if tool_denial_kind:
        rec["toolDenialKind"] = tool_denial_kind
    return rec


def _review_trace_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    deny_only: bool = False,
    deny_summary: bool = False,
    skill: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "deny_only": deny_only,
        "deny_summary": deny_summary,
        "skill": skill,
    })()


def _since_until_epochs(since: str | None, until: str | None) -> tuple[float | None, float | None]:
    """Mirror cmd_review_trace's own --since/--until date-string -> epoch-second
    boundary conversion, so a test calling _review_trace_session_events directly
    passes boundaries in the same form the CLI itself would compute."""
    since_ts = _mod._parse_ts(f"{since}T00:00:00Z") if since else None
    until_epoch = None
    if until:
        day_start = _mod._parse_ts(f"{until}T00:00:00Z")
        if day_start is not None:
            until_epoch = day_start + 86400
    return since_ts, until_epoch


class TestDropDenialCommandFlagValues:
    """Direct unit coverage for _drop_denial_command_flag_values — the CLI-layer
    --deny-summary tests (TestReviewTrace) exercise this only indirectly
    through the full JSONL-fixture-to-stdout path."""

    def test_empty_tokens_returns_empty_list(self):
        assert _mod._drop_denial_command_flag_values([]) == []

    def test_no_flags_returns_tokens_unchanged(self):
        assert _mod._drop_denial_command_flag_values(["git", "commit", "-m", "x"]) == [
            "git", "commit", "-m", "x",
        ]

    def test_separate_token_flag_drops_flag_and_its_value(self):
        assert _mod._drop_denial_command_flag_values(["git", "-C", "/path", "commit"]) == [
            "git", "commit",
        ]

    def test_double_separate_token_flags_both_dropped(self):
        assert _mod._drop_denial_command_flag_values(
            ["git", "-C", "/path", "-c", "user.name=x", "commit"]
        ) == ["git", "commit"]

    def test_attached_equals_flag_drops_only_the_one_token(self):
        assert _mod._drop_denial_command_flag_values(["git", "--git-dir=/path", "status"]) == [
            "git", "status",
        ]

    def test_all_four_named_flag_forms_drop_their_value(self):
        assert _mod._drop_denial_command_flag_values(["git", "-C", "/a", "commit"]) == ["git", "commit"]
        assert _mod._drop_denial_command_flag_values(["git", "-c", "x=y", "commit"]) == ["git", "commit"]
        assert _mod._drop_denial_command_flag_values(["git", "--git-dir", "/a", "commit"]) == [
            "git", "commit",
        ]
        assert _mod._drop_denial_command_flag_values(["git", "--work-tree", "/a", "commit"]) == [
            "git", "commit",
        ]

    def test_separate_token_flag_at_end_of_list_drops_flag_with_no_value_token(self):
        """A value-taking flag as the last token, with nothing after it, still
        drops the flag itself — the unconditional i += 2 skip doesn't require
        a following token to exist."""
        assert _mod._drop_denial_command_flag_values(["git", "commit", "-C"]) == ["git", "commit"]


class TestIsNongateFrictionKind:
    """Direct unit coverage for _is_nongate_friction_kind — the CLI-layer
    --deny-summary/timeline tests (TestReviewTrace) exercise this only
    indirectly through the full JSONL-fixture-to-stdout path."""

    def test_already_gate_denied_returns_false_even_with_nongate_kind(self):
        assert _mod._is_nongate_friction_kind("interrupted", already_gate_denied=True) is False

    def test_falsy_kind_returns_false(self):
        assert _mod._is_nongate_friction_kind("", already_gate_denied=False) is False

    def test_gate_kind_returns_false(self):
        assert _mod._is_nongate_friction_kind(_mod._GATE_TOOL_DENIAL_KIND, already_gate_denied=False) is False

    @pytest.mark.parametrize(
        "kind", ["user-rejected", "automode-blocked", "automode-unavailable", "interrupted"]
    )
    def test_nongate_kind_returns_true(self, kind):
        assert _mod._is_nongate_friction_kind(kind, already_gate_denied=False) is True

    def test_unenumerated_future_kind_still_returns_true(self):
        """A toolDenialKind value outside the four named kinds is still
        non-gate friction as long as it isn't the gate kind — the label
        printed for it is _friction_kind_label's concern, not this
        predicate's."""
        assert _mod._is_nongate_friction_kind("some-future-kind", already_gate_denied=False) is True


class TestFrictionKindLabel:
    """Direct unit coverage for _friction_kind_label — the CLI-layer
    --deny-summary/timeline tests (TestReviewTrace) exercise this only
    indirectly through the full JSONL-fixture-to-stdout path."""

    @pytest.mark.parametrize(
        "kind", ["user-rejected", "automode-blocked", "automode-unavailable", "interrupted"]
    )
    def test_enumerated_kind_returns_itself(self, kind):
        assert _mod._friction_kind_label(kind) == kind

    def test_unenumerated_kind_returns_other_kind_sentinel(self):
        assert _mod._friction_kind_label("some-future-kind") == _mod._FRICTION_KIND_OTHER


class TestSanitizeTableCell:
    """Direct unit coverage for _sanitize_table_cell — the defense-in-depth
    control-character strip every --deny-summary table cell passes through.
    No CLI-layer test can reach this with live malicious input today, since
    every caller already passes an allowlist-bounded label (_DENIAL_HOOK_LABELS,
    _DENIAL_COMMAND_SUBCOMMANDS, _FRICTION_KINDS) — this pins the helper's own
    behavior directly instead."""

    def test_strips_esc_byte(self):
        """Only the ESC control byte itself is stripped, not surrounding
        printable characters — e.g. an ANSI escape sequence's bracket/digit
        payload is left in place, just no longer interpretable as an escape."""
        assert _mod._sanitize_table_cell("git\x1b[31mcommit") == "git[31mcommit"

    def test_strips_null_and_del_bytes(self):
        assert _mod._sanitize_table_cell("a\x00b\x7fc") == "abc"

    def test_plain_label_unchanged(self):
        assert _mod._sanitize_table_cell("git commit") == "git commit"

    def test_empty_string_unchanged(self):
        assert _mod._sanitize_table_cell("") == ""


class TestReviewTrace:
    def test_skill_invocation_appears_in_output(self):
        """Main-thread Skill call for a review skill produces a 'skill' event."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"
        assert events[0]["skill"] == "code-review"

    def test_denial_dict_blockingError_parsed(self):
        """hook_blocking_error with blockingError as a dict produces a denial event."""
        records = [_hook_deny("require-code-review", stringified=False)]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert events[0]["hook_name"] == "require-code-review"

    def test_denial_stringified_blockingError_parsed_identically(self):
        """hook_blocking_error with blockingError as a JSON string produces
        an identical denial event to the dict form."""
        dict_events, _tuc1, _pr1 = _mod._review_trace_session_events(
            [_hook_deny("require-code-review", stringified=False)], None, None, None,
        )
        str_events, _tuc2, _pr2 = _mod._review_trace_session_events(
            [_hook_deny("require-code-review", stringified=True)], None, None, None,
        )
        assert len(dict_events) == 1
        assert len(str_events) == 1
        assert dict_events[0]["hook_name"] == "require-code-review"
        assert str_events[0]["hook_name"] == "require-code-review"
        # The human-readable message text must appear in both forms, not a dict repr.
        assert "blocked the operation" in dict_events[0]["message"]
        assert "blocked the operation" in str_events[0]["message"]
        assert "{'blockingError'" not in dict_events[0]["message"]
        assert "{'blockingError'" not in str_events[0]["message"]

    def test_hook_non_blocking_error_produces_zero_denial_events(self):
        """hook_non_blocking_error records must NOT produce a denial event."""
        non_blocking_rec = {
            "type": "attachment",
            "attachment": {
                "type": "hook_non_blocking_error",
                "hookName": "some-hook",
                "toolUseID": "toolu_abc",
                "blockingError": {"message": "non-fatal"},
            },
        }
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [non_blocking_rec], None, None, None,
        )
        assert events == []

    def test_reviewer_spawn_detected_general_purpose_excluded(self):
        """staff-backend-engineer spawn produces a reviewer-spawn event; general-purpose does not."""
        records = [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _agent_use("a1", "staff-backend-engineer"),
                      _agent_use("a2", "general-purpose"),
                  ]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        reviewer_events = [e for e in events if e["kind"] == "reviewer-spawn"]
        assert len(reviewer_events) == 1
        assert reviewer_events[0]["subagent_type"] == "staff-backend-engineer"

    def test_reviewer_spawn_detected_comment_discipline_and_skill_fidelity(self):
        """comment-discipline-reviewer and skill-fidelity-reviewer are exact-name
        reviewer-spawn matches, not just the staff- prefix or ciso-reviewer."""
        records = [
            _asst("claude-opus-4-7", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[
                      _agent_use("a1", "comment-discipline-reviewer"),
                      _agent_use("a2", "skill-fidelity-reviewer"),
                  ]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        reviewer_types = {e["subagent_type"] for e in events if e["kind"] == "reviewer-spawn"}
        assert reviewer_types == {"comment-discipline-reviewer", "skill-fidelity-reviewer"}

    def test_sidechain_skill_invocation_excluded(self):
        """A code-review Skill call inside a sidechain record must not produce a skill event."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_since_boundary_inclusive_record_included(self):
        """A record whose timestamp matches exactly --since is included."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T00:00:00Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs("2026-05-19", None)
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_until_boundary_inclusive_record_included(self):
        """A record whose timestamp matches exactly --until is included."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T23:59:59Z",
                  content=[_skill_use("s1", "plan-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs(None, "2026-05-19")
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["skill"] == "plan-review"

    def test_record_with_no_timestamp_excluded_no_crash(self, fake_projects, capsys):
        """A record with no parseable timestamp is excluded when a date filter is active; no crash."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat",
                  content=[_skill_use("s1", "code-review")]),  # no ts field
        ])
        # No exception must be raised; the missing-timestamp record is silently skipped.
        _mod.cmd_review_trace(_review_trace_args(since="2026-05-01"))
        # No crash; output may be empty (no matching events survive the date filter).
        capsys.readouterr()  # consume; success if no exception raised above

    def test_deny_only_restricts_to_denial_sessions(self):
        """--deny-only retains sessions with a denial event; a session with a
        reviewer spawn but no denial does not qualify."""
        session_a, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [_hook_deny("require-code-review")], None, None, None,
        )
        session_b, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [
                _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                      content=[_agent_use("a1", "staff-backend-engineer")]),
            ],
            None, None, None,
        )
        assert any(e["kind"] == "denial" for e in session_a)
        assert not any(e["kind"] == "denial" for e in session_b)

    def test_until_subsecond_record_included(self):
        """A record at T23:59:59.500Z on the --until date IS included (sub-second gap fix)."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-10T23:59:59.500Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs(None, "2026-05-10")
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

    def test_denial_blockingError_key_used_for_display_message(self):
        """Denial event's message carries the nested blockingError string, not a dict repr."""
        human_message = "Hook 'require-code-review' blocked the operation"
        error_dict = {"blockingError": human_message, "command": "git commit -m x"}
        denial_rec = {
            "type": "attachment",
            "attachment": {
                "type": "hook_blocking_error",
                "hookName": "require-code-review",
                "toolUseID": "toolu_abc",
                "blockingError": error_dict,
            },
        }
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [denial_rec], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["message"] == human_message
        assert "{'blockingError'" not in events[0]["message"]

    def test_no_match_session_produces_no_output(self):
        """A session with only non-review tool_use (Bash) produces no events."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git status")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_current_format_denial_detected(self):
        """A current-format is_error tool_result with a hook-denial signature produces a denial event."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert "code-review gate" in events[0]["message"]

    def test_current_format_ordinary_error_is_not_a_denial(self):
        """An is_error tool_result without a hook-denial signature produces no events."""
        records = [_hook_deny_current("npm ERR! command failed with exit code 1")]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert events == []

    def test_current_format_denial_text_without_is_error_ignored(self):
        """A tool_result with denial-shaped text but no is_error flag produces no events."""
        rec = _hook_deny_current("Blocked by worktree-enforcement hook: not allowed.")
        rec["message"]["content"][0]["is_error"] = False
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [rec], None, None, None,
        )
        assert events == []

    def test_legacy_and_current_shapes_deduped_by_tool_use_id(self):
        """A denial recorded as both an attachment and an is_error tool_result for one
        tool_use_id collapses to one event. Dedup keeps whichever record appears first
        in the transcript; here the attachment is written ahead of its twin, so the
        retained event carries the hook name the attachment record provides."""
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree", hookName "worktree"
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree",
        )
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [attach, twin], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        # Dedup retains the first-seen record. The attachment is written ahead of the
        # current-format twin above, so the retained event carries hook=worktree; had
        # the twin come first, hook_name would be empty.
        assert events[0]["hook_name"] == "worktree"

    def test_multiple_distinct_current_format_denials_each_counted(self):
        """Two current-format denials with distinct tool_use_ids count as two events —
        dedup collapses same-id pairs, not distinct denials."""
        records = [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        assert len(denial_events) == 2

    def test_current_format_denial_with_list_content_detected(self):
        """A current-format denial whose tool_result content is a list of text blocks
        (not a bare string) is still detected — hook_denial_key's signature match
        relies on _content_text to decode the list shape before matching."""
        rec = _hook_deny_current("placeholder")
        rec["message"]["content"][0]["content"] = [
            {"type": "text", "text": "Commit blocked by code-review gate: run /code-review."},
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [rec], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        assert "code-review gate" in events[0]["message"]

    def test_deny_only_matches_current_format_denial(self):
        """--deny-only retains a session whose only denial is current-format."""
        denial_events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [_hook_deny_current("Push to a branch blocked by ready-for-review gate.")], None, None, None,
        )
        no_denial_events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [
                _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                      content=[_agent_use("a1", "staff-sdet")]),
            ],
            None, None, None,
        )
        assert any(e["kind"] == "denial" for e in denial_events)
        assert not any(e["kind"] == "denial" for e in no_denial_events)

    def test_deny_only_plain_timeline_restricts_to_denial_sessions(self, fake_projects, capsys):
        """--deny-only's session-skip (the `if deny_only and not has_denial:
        continue` gate inside cmd_review_trace itself, not either accessor) drops
        a session with a matched event but no denial from the plain (non-
        --deny-summary) timeline — a session with a denial still prints."""
        _write_jsonl(fake_projects / "denial-session.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review."),
        ])
        _write_jsonl(fake_projects / "skill-only-session.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_only=True))
        out = capsys.readouterr().out
        assert "denial-session.jsonl" in out
        assert "skill-only-session.jsonl" not in out

    # -----------------------------------------------------------------------
    # GH-482: per-record branch/model attribution
    # -----------------------------------------------------------------------

    def test_gh482_events_attributed_to_own_branch_not_session_first_branch(self):
        """A session opening on one branch, then moving to another before any review
        event fires, must attribute every event to its own (later) branch — and
        branch_filter must select by that per-event value, not the session's first
        record's branch (the 53-session class from row 4 of the GH-482 plan)."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T09:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feature-x", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ]

        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 2
        for evt in events:
            assert evt["branch"] == "feature-x", f"event must attribute to feature-x, not main: {evt!r}"

        events_feature_x, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, {"feature-x"},
        )
        assert {e["kind"] for e in events_feature_x} == {"skill", "reviewer-spawn"}

        events_main_only, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, {"main"},
        )
        assert events_main_only == [], "the session's first-record branch must return zero events"

    def test_header_branches_and_models_are_distinct_sorted_sets(self):
        """The per-event branch/model values a session contributes are the distinct
        set cmd_review_trace's header line joins and sorts, not a single session-wide value."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat-a", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feat-b", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert {e["branch"] for e in events} == {"feat-a", "feat-b"}
        assert {e["model"] for e in events} == {"sonnet", "opus"}

    def test_denial_stamped_with_its_own_branch_not_carried_forward(self):
        """An attachment denial record carrying its own gitBranch, differing from the
        carried-forward branch, is stamped with the record's own value."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _hook_deny("require-code-review", branch="feature-y", ts="2026-05-19T10:05:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        denial_event = next(e for e in events if e["kind"] == "denial")
        assert denial_event["branch"] == "feature-y"

    def test_denial_inherits_last_assistant_model_not_other(self):
        """A denial carries no message.model of its own — it must inherit the last
        main-thread assistant model family, not render 'other'."""
        records = [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _hook_deny("require-code-review", branch="main", ts="2026-05-19T10:05:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        denial_event = next(e for e in events if e["kind"] == "denial")
        assert denial_event["model"] == "opus"

    def test_unresolvable_branch_renders_sentinel(self):
        records = [
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert events[0]["branch"] == "?"

    def test_branch_carry_forward_crosses_since_boundary(self):
        """An in-window event with no gitBranch of its own inherits the branch of an
        out-of-window record — carry-forward crosses the --since boundary."""
        records = [
            _asst("claude-sonnet-4-6", branch="old-branch", ts="2026-05-01T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs("2026-05-10", None)
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["branch"] == "old-branch"

    def test_deny_only_with_branches_filters_before_gating(self):
        """The sole denial sits on a branch the filter excludes: branch filtering
        drops it before deny_only's has_denial check ever sees it (filter-then-deny),
        not a session that still qualifies because it had a denial before filtering."""
        records = [_hook_deny("require-code-review", branch="wrong-branch")]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, {"right-branch"},
        )
        assert events == []

    def test_dedup_before_branch_filter_pins_ordering(self):
        """A duplicate-id denial recorded on two different branches must still
        collapse to one event when both branches are in scope — dedup (step 3)
        is global and runs before branch filtering (step 5), not scoped per branch."""
        attach = _hook_deny("worktree", branch="branch-a")
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree", branch="branch-b",
        )
        records = [attach, twin]

        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        assert len(denial_events) == 1

        # attach (branch-a) is the first-occurring record, so dedup collapses the
        # pair to a single event attributed to branch-a — filtering to branch-b
        # alone (the second-occurring, non-surviving branch) must then drop that
        # event entirely. A filter-before-dedup implementation would instead
        # exclude attach before dedup ever runs, letting twin (branch-b) through
        # undeduped and yielding one event — the regression this pins against.
        events_branch_b_only, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, {"branch-b"},
        )
        assert events_branch_b_only == []

    def test_deny_summary_groups_by_hook_and_command_shape(self):
        """--deny-summary groups denials by hook/gate name and by attempted command
        shape, mixing multiple hook names (code-review x2, ready-for-review x1) and
        multiple git-command shapes (git commit x2, git push x1)."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_bash_use("b2", "git push origin main")]),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="b2"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:02:00.000Z",
                  content=[_bash_use("b3", "git commit -m y")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b3"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"code-review": 2, "ready-for-review": 1}
        assert dict(data["command_shape_counts"]) == {"git commit": 2, "git push": 1}

    def test_deny_summary_command_shape_empty_command_bucketed_as_other(self):
        """A denial with an enumerated hook name but no paired Bash tool_use (an
        empty command string) still lands the command-shape axis in 'other' — the
        shape-axis counterpart to test_deny_summary_unmatched_hook_name_bucketed_not_dropped,
        isolated from that test's hook-axis unmatched-ness."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {_mod._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_git_dash_c_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git -C <path> commit' buckets as 'git commit', not 'other' and not a
        naive misread of <path> as the subcommand — -C is
        require-worktree-for-git-writes.sh's own resolution mechanism for a
        compliant worktree write, so this is the dominant separate-token flag
        shape in the worktree-enforcement denial category. The path itself never
        appears in the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git -C ~/repo commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_git_dash_lowercase_c_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git -c key=value commit' (a separate-token config override, the value
        itself containing '=') buckets as 'git commit', not 'other'."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git -c user.name=eng commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_git_dir_equals_attached_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git --git-dir=<path> status' (an =-attached flag, consuming only its
        own token) buckets as 'git status', not 'other'. The path never leaks
        into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --git-dir=~/repo/.git status")]),
            _hook_deny_current("Blocked by worktree-enforcement gate: not in a linked worktree.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git status": 1}

    def test_deny_summary_work_tree_separate_token_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git --work-tree <path> commit' (a separate-token flag) buckets as
        'git commit', not 'other'. The path never leaks into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --work-tree ~/repo commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_env_assignment_prefix_stripped_before_classification(self):
        """A leading NAME=VALUE environment-assignment prefix (the corpus shape
        wrapping a marker.sh invocation with a live per-machine token) is
        stripped before classification — the denial buckets as 'marker.sh
        write' and the env value never leaks into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use(
                      "b1",
                      "CLAUDE_CONFIG_DIR=~/.config/claude-accounts/proj "
                      "~/.claude/scripts/marker.sh write code-review",
                  )]),
            _hook_deny_current(
                "marker.sh invocation denied (path traversal '..' detected). "
                "Command (truncated): ~/.claude/scripts/marker.sh write code-review",
                tool_id="b1",
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"marker.sh write": 1}

    def test_deny_summary_absolute_marker_script_path_basenamed_not_leaked(self):
        """An absolute marker.sh invocation path (rather than the tilde form) is
        basenamed before classification — the denial buckets as 'marker.sh
        activate', with no home-rooted path surviving into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "~/.claude/scripts/marker.sh activate plan-review")]),
            _hook_deny_current(
                "marker.sh invocation denied (path traversal '..' detected). "
                "Command (truncated): ~/.claude/scripts/marker.sh activate plan-review",
                tool_id="b1",
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"marker.sh activate": 1}

    def test_deny_summary_unenumerated_attached_flag_before_subcommand_falls_to_other_no_leak(self):
        """A git global flag outside the named value-taking set (e.g.
        --exec-path=<path>) is left in place by _drop_denial_command_flag_values,
        but since it looks like a flag it must never be read as, and bucketed as,
        the subcommand — the denial falls to 'other' rather than leaking the
        attached path into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --exec-path=~/secret-tools status")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {_mod._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_esc_byte_in_trailing_argument_never_reaches_stdout(self):
        """An ESC byte embedded in an argument past the subcommand (e.g. a commit
        message) never survives into the returned command-shape data — the
        classifier only ever keeps the command and one subcommand token, so the
        denial buckets as 'git commit' with the control byte discarded along with
        the rest of the argument."""
        esc_message = "\x1b[31mFAKE PROMPT\x1b[0m"
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", f'git commit -m "{esc_message}"')]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_unenumerated_non_flag_subcommand_token_falls_to_other_no_leak(self):
        """A credential-shaped token occupying the subcommand position itself
        (not a flag, not a member of _DENIAL_COMMAND_SUBCOMMANDS) must never be
        read as, and bucketed as, the subcommand — the denial falls to 'other'
        and the token never appears as a key in the returned shape counts."""
        credential_token = "AKIA_FAKE_SECRET_ACCESS_KEY_ABCDEFGHIJKL"
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", f"git {credential_token} status")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {_mod._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_unmatched_hook_name_bucketed_not_dropped(self):
        """A denial matched via _HOOK_DENIAL_SIGNATURE's 'invocation denied' alternative,
        which names no hook, lands in the 'unmatched' bucket rather than being silently
        dropped from --deny-summary's total. Its unresolvable tool_use_id also lands in
        the command-shape grouping's 'other' bucket."""
        records = [_hook_deny_current("Skill invocation denied.")]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod._DENY_SUMMARY_UNMATCHED_HOOK: 1}
        assert dict(data["command_shape_counts"]) == {_mod._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_covers_marker_invocation_denied_wording(self):
        """enforce-marker-script-shape.sh's 'marker.sh invocation denied ...' wording
        names no hook via the 'blocked by <name> hook/gate' idiom, but the
        '<name> invocation denied' pattern extracts 'marker.sh' as an enumerated
        label rather than falling to unmatched."""
        records = [
            _hook_deny_current(
                "marker.sh invocation denied (path traversal '..' detected). "
                "Command (truncated): ~/.claude/scripts/marker.sh write ../foo"
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"marker.sh": 1}

    def test_deny_summary_covers_self_labeled_gate_colon_wording(self):
        """check-skill-length.sh states its own label as the message's own prefix
        ('Skill length gate: ...') rather than via 'blocked by' — the
        '<name> gate:' pattern extracts 'Skill length' as an enumerated label."""
        records = [
            _hook_deny_current(
                "Skill length gate: one or more SKILL.md files grew past their "
                "per-skill limit. Reduce to the limit or fewer lines before committing."
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"Skill length": 1}

    def test_deny_summary_unenumerated_colon_wording_falls_to_unmatched_no_leak(self):
        """deny-credential-file-reads.sh's 'Read of '<path>' denied by the
        credential-file read gate: ...' wording now matches _HOOK_DENIAL_SIGNATURE's
        colon-anchored alternative (previously invisible), but the captured span
        includes the 'denied by the' prefix and so isn't an enumerated label —
        it falls to 'unmatched' rather than fabricating a new hook bucket, and the
        credential-shaped path never appears as a key in the returned hook counts."""
        records = [
            _hook_deny_current(
                "Read of './secrets/.netrc' denied by the credential-file "
                "read gate: the path is credential-shaped."
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    @pytest.mark.parametrize(
        "message_template",
        [
            "Blocked by {name} gate: could not source _lib.sh.",
            "{name} invocation denied. Command (truncated): ~/.claude/scripts/marker.sh write foo",
            "{name} gate: some detail.",
        ],
    )
    def test_deny_summary_over_max_chars_hook_name_candidate_falls_to_unmatched_no_leak(self, message_template):
        """A candidate hook-name span longer than _DENIAL_HOOK_NAME_MAX_CHARS
        (40) across each of the three extraction patterns never yields an
        enumerated label — it falls to 'unmatched', and the credential-shaped
        name is never returned as the label."""
        over_cap_name = "AKIA_FAKE_SECRET_ACCESS_KEY_" + "X" * 20  # 48 chars, over the 40-char cap
        records = [_hook_deny_current(message_template.format(name=over_cap_name))]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    def test_deny_summary_attachment_hookname_not_enumerated_falls_to_unmatched(self):
        """The legacy attachment branch's hookName field is bounded the same way as
        the regex-extracted branch: an unenumerated hookName (legacy transcripts
        predate this bound, so any historical value is unverified) is not echoed
        verbatim into the returned hook counts — it falls to 'unmatched'."""
        records = [_hook_deny("legacy-hook-slug")]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {_mod._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    def test_deny_summary_replaces_per_session_listing(self, fake_projects, capsys):
        """--deny-summary suppresses the normal per-session event listing entirely —
        no '### <file>' block appears, only the two grouped-count tables."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate."),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "### " not in out
        assert "Denials by hook/gate" in out
        assert "Denials by attempted command shape" in out

    def test_deny_summary_with_matching_session_but_zero_denials_prints_explicit_message(
        self, fake_projects, capsys
    ):
        """A scope with a matching session (a skill event, no denial) under
        --deny-summary prints an explicit 'no denials found' message with the
        scope header — not byte-for-byte empty output, which would be
        indistinguishable from a broken --branches/scope flag matching nothing."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "No denials found in scope." in out
        assert "Denials by hook/gate" not in out

    def test_absent_toolDenialKind_produces_no_friction_event(self):
        """A current-format denial with no toolDenialKind field produces only a
        `denial` event — no `friction` event, since a falsy toolDenialKind means
        the field is absent, not friction."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"

    def test_already_gate_denied_record_produces_denial_not_friction(self):
        """A record whose text matches the hook-denial signature AND carries a
        non-gate toolDenialKind produces only a `denial` event, never also a
        `friction` one — already_gate_denied short-circuits
        _is_nongate_friction_kind so one record can't double-count across both
        axes."""
        records = [
            _hook_deny_current(
                "Commit blocked by code-review gate: run /code-review.",
                tool_id="toolu_both", tool_denial_kind="user-rejected",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"

    def test_multi_block_record_produces_one_friction_event_for_the_errored_block_only(self):
        """toolDenialKind lives once on the parent user record, but a parallel
        tool call can carry multiple tool_result blocks under it — only the
        block whose own is_error is True is the one the interruption applies
        to. A sibling successful block (is_error False) must not also be
        promoted to its own spurious friction event carrying its unrelated
        successful output."""
        records = [
            {
                "type": "user",
                "gitBranch": "main",
                "isSidechain": False,
                "toolDenialKind": "interrupted",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_errored",
                     "content": "Request interrupted by user for tool use", "is_error": True},
                    {"type": "tool_result", "tool_use_id": "toolu_ok",
                     "content": "some unrelated successful output", "is_error": False},
                ]},
            },
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 1
        assert friction_events[0]["tool_use_id"] == "toolu_errored"

    def test_legacy_attachment_denial_and_friction_kind_coexist(self):
        """A legacy attachment denial and a separate current-format friction
        record (distinct tool_use_ids) in the same session produce one denial
        event and one friction event — the legacy shape never carries
        toolDenialKind, so it cannot itself become friction, and the two axes
        don't interfere with each other."""
        records = [
            _hook_deny("require-code-review"),
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_interrupt",
                tool_denial_kind="interrupted",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(denial_events) == 1
        assert len(friction_events) == 1

    def test_friction_dedup_set_independent_of_denial_dedup_set(self):
        """A legacy attachment denial and a current-format record sharing the
        SAME tool_use_id, where the current-format record carries a non-gate
        toolDenialKind and non-signature-matching text, still produces a
        friction event — friction dedups against its own set, never
        seen_denial_ids, so an id already recorded there doesn't suppress a
        later friction event."""
        shared_id = "toolu_worktree"
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree"
        friction_twin = _hook_deny_current(
            "Request interrupted by user for tool use", tool_id=shared_id,
            tool_denial_kind="interrupted",
        )
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            [attach, friction_twin], None, None, None,
        )
        denial_events = [e for e in events if e["kind"] == "denial"]
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(denial_events) == 1
        assert len(friction_events) == 1

    def test_friction_event_with_empty_tool_use_id_not_deduped_against_others(self):
        """Multiple friction records with no tool_use_id (empty string) each
        still produce their own event — an empty id is falsy and so is never
        added to seen_friction_ids, matching hook_denial_key's own 'empty
        string is a valid id' contract for denials."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="",
                tool_denial_kind="interrupted",
            ),
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="",
                tool_denial_kind="interrupted", ts="2026-05-19T10:01:00.000Z",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 2

    def test_unrecognized_toolDenialKind_prints_as_other_kind_not_raw_value(self):
        """A toolDenialKind value outside the closed four-value enumeration
        still produces a friction event, carrying the raw field value verbatim
        on the returned event — _friction_kind_label (already unit-tested
        separately) is what maps it to `other-kind` at print/count time, not
        the accessor itself."""
        records = [
            _hook_deny_current(
                "Some new denial shape not yet enumerated.", tool_id="toolu_future",
                tool_denial_kind="some-future-kind",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        friction_events = [e for e in events if e["kind"] == "friction"]
        assert len(friction_events) == 1
        assert friction_events[0]["friction_kind"] == "some-future-kind"

    def test_friction_only_session_survives_deny_only_with_deny_summary(self):
        """A session with only friction events (no denial-kind events at all) is
        not dropped by --deny-only when --deny-summary also runs: the friction
        tally reads the full per-session events list before deny_only's
        has_denial skip is applied."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_a",
                tool_denial_kind="interrupted",
            ),
        ]
        data = _mod._compute_deny_summary_data(
            [("friction_only.jsonl", records)], deny_only=True,
        )
        assert data["any_session_matched"] is True
        assert dict(data["friction_counts"]) == {"interrupted": 1}

    def test_friction_only_session_renders_timeline_line_default_output(self):
        """Without --deny-summary, a friction-only session's events list carries
        a `friction`-kind event rather than nothing, and pinning the flip side,
        no `denial`-kind event is present — has_denial and --deny-only's own
        session-selection semantics stay denial-kind-only."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_a",
                tool_denial_kind="interrupted",
            ),
        ]
        events, _tool_use_commands, _pre_regime = _mod._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "friction"
        assert not any(e["kind"] == "denial" for e in events)

    def test_deny_summary_prints_corpus_window(self):
        """--deny-summary computes the earliest/latest in-scope event
        timestamp as the corpus window, not just the grouped counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.",
                                tool_id="b1", ts="2026-07-01T10:00:01.000Z"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-15T09:00:00.000Z",
                  content=[_bash_use("b2", "git push origin main")]),
            _hook_deny_current("Push blocked by ready-for-review gate.",
                                tool_id="b2", ts="2026-07-15T09:00:01.000Z"),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert data["corpus_min_ts"] == _mod._parse_ts("2026-07-01T10:00:01.000Z")
        assert data["corpus_max_ts"] == _mod._parse_ts("2026-07-15T09:00:01.000Z")

    def test_deny_summary_pre_regime_record_excluded_from_kind_breakdown_and_counted_separately(self):
        """An errored, non-gate-signature tool_result timestamped before
        toolDenialKind's 2026-07-20 introduction structurally cannot carry
        the field — it produces neither a denial nor a friction event (the
        exact record shape this design would silently read as zero friction),
        but pre_regime_tool_result_count reports it separately rather than
        folding it into a zero. A same-shaped record dated inside the
        regime with a real toolDenialKind is included as a control, pinning
        that the pre-regime count is date-gated, not 'every non-denial
        record'. A gate-matching denial dated before the regime is also
        included, pinning that already-gate-denied records — already
        correctly classified on the hook/gate axis regardless of era — are
        excluded from the pre-regime count, which counts only the population
        whose kind is genuinely unknowable, not every old record."""
        records = [
            _hook_deny_current(
                "Request interrupted by user for tool use",
                tool_id="pre_regime", ts="2026-06-25T10:00:00.000Z",
            ),
            _hook_deny_current(
                "Commit blocked by code-review gate: run /code-review.",
                tool_id="pre_regime_gate", ts="2026-06-25T10:01:00.000Z",
            ),
            _hook_deny_current(
                "Request interrupted by user for tool use",
                tool_id="in_regime", tool_denial_kind="interrupted",
                ts="2026-07-25T10:00:00.000Z",
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert data["pre_regime_tool_result_count"] == 1
        assert dict(data["friction_counts"]) == {"interrupted": 1}

    def test_deny_summary_cross_tab_shows_joint_counts_not_just_marginals(self):
        """Two hooks each deny two command shapes with symmetric marginals
        (code-review: 2 commits + 1 checkout = 3; worktree-enforcement: 1
        commit + 2 checkouts = 3; git commit: 2+1=3; git checkout: 1+2=3) —
        the marginal hook and shape counts alone can't distinguish which hook
        denied which shape how many times. hook_shape_counts must carry the
        true joint counts (code-review x git commit = 2, worktree-enforcement
        x git checkout = 2), not the marginal-implied even split."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:01:00.000Z",
                  content=[_bash_use("b2", "git commit -m y")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b2"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:02:00.000Z",
                  content=[_bash_use("b3", "git checkout main")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b3"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:03:00.000Z",
                  content=[_bash_use("b4", "git commit -m z")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git commit' is not on the read-only allowlist.",
                tool_id="b4",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:04:00.000Z",
                  content=[_bash_use("b5", "git checkout main")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git checkout' is not on the read-only allowlist.",
                tool_id="b5",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:05:00.000Z",
                  content=[_bash_use("b6", "git checkout main")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git checkout' is not on the read-only allowlist.",
                tool_id="b6",
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        # Marginals confirm the symmetric setup (both hooks 3, both shapes 3).
        assert dict(data["command_shape_counts"]) == {"git commit": 3, "git checkout": 3}
        # The cross-tab is what actually distinguishes the two hooks' shapes.
        assert data["hook_shape_counts"][("code-review", "git commit")] == 2
        assert data["hook_shape_counts"][("code-review", "git checkout")] == 1
        assert data["hook_shape_counts"][("worktree-enforcement", "git commit")] == 1
        assert data["hook_shape_counts"][("worktree-enforcement", "git checkout")] == 2

    def test_deny_summary_real_corpus_shapes_all_classify_no_other_or_unmatched(self):
        """A fixture drawn from real transcript-analysis.py corpus denials —
        realistic multi-line/chained commands and full hook-message wording,
        not minimal strings copied from A3's own allowlist — across both of
        GH-557's named categories (worktree-enforcement/other-git, marker.sh)
        plus two more hooks (code-review, respond-pr) for label diversity.
        Every one of these shapes was observed actually landing in
        --deny-summary's 'other'/'unmatched' buckets before A2/A3, and must
        classify cleanly now: both denominators are 0 for this fixture. The
        four non-gate friction kinds never contribute to either denominator
        in the first place — hook_counts/command_shape_counts are populated
        only from `denial`-kind events, never `friction`-kind ones — so they
        are irrelevant to, not merely absent from, this fixture."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z", content=[_bash_use(
                "b1", "git checkout main && git pull --ff-only && git worktree add "
                      ".claude/worktrees/some-feature -b some-feature",
            )]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git checkout' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b1",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:01:00.000Z", content=[_bash_use(
                "b2", "git -C ~/repo/.claude/worktrees/some-feature add -A",
            )]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git add' targets a working directory outside "
                "this repository (or its git state could not be determined), so it cannot be confirmed safe.",
                tool_id="b2",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:02:00.000Z",
                  content=[_bash_use("b3", "git push -u origin some-feature 2>&1")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git push' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b3",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:03:00.000Z",
                  content=[_bash_use("b4", "git -C /tmp/ignoretest init -q")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git init' targets a working directory outside "
                "this repository (or its git state could not be determined), so it cannot be confirmed safe.",
                tool_id="b4",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:04:00.000Z",
                  content=[_bash_use("b5", "git pull --ff-only")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git pull' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b5",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:05:00.000Z",
                  content=[_bash_use("b6", "git config --system --show-origin --get-all credential.helper")]),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git config' is not on the read-only allowlist, "
                "and this write targets the MAIN working tree of a repo where worktree discipline is active.",
                tool_id="b6",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:06:00.000Z", content=[_bash_use(
                "b7", "~/.claude/scripts/marker.sh write ready-for-review\n"
                      "~/.claude/scripts/marker.sh deactivate ready-for-review 2>&1 || true",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh write "
                "ready-for-review",
                tool_id="b7",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:07:00.000Z",
                  content=[_bash_use("b8", "~/.claude/scripts/marker.sh activate ready-for-review 2>&1")]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh activate "
                "ready-for-review",
                tool_id="b8",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:08:00.000Z", content=[_bash_use(
                "b9", "~/.claude/scripts/marker.sh deactivate plan-review && "
                      "~/.claude/scripts/marker.sh write plan-review && echo \"markers updated\"",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh deactivate "
                "plan-review",
                tool_id="b9",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:09:00.000Z", content=[_bash_use(
                "b10", "~/.claude/scripts/marker.sh status plan-review 2>&1 || "
                       "ls -la ~/.claude/plan-review-markers/ 2>&1 | head",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh status "
                "plan-review",
                tool_id="b10",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:10:00.000Z", content=[_bash_use(
                "b11", "~/.claude/scripts/marker.sh clear-stale\necho \"--- after ---\"\n"
                       "ls ~/.claude/.plan-review-active.d/ 2>/dev/null",
            )]),
            _hook_deny_current(
                "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh clear-stale",
                tool_id="b11",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:11:00.000Z", content=[_bash_use(
                "b12", "git commit --amend --no-edit\ngit log --oneline -3",
            )]),
            _hook_deny_current(
                "Commit blocked by code-review gate: the currently staged changes have not been reviewed, "
                "or the staged state has changed since the last review. Run the /code-review skill now.",
                tool_id="b12",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:12:00.000Z", content=[_bash_use(
                "b13", "gh api repos/example-org/example-repo/pulls/1/reviews "
                       "--jq '.[] | {user: .user.login, state: .state, body: .body}' 2>&1 | head -60",
            )]),
            _hook_deny_current(
                "PR comment access blocked by respond-pr gate. Run the /respond-pr skill instead.",
                tool_id="b13",
            ),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:13:00.000Z", content=[_bash_use(
                "b14", "gh pr review 1 --repo example-org/example-repo --comment "
                       "--body-file ~/handoffs/pr-1-review-body.md",
            )]),
            _hook_deny_current(
                "PR/issue comment write blocked by respond-pr gate. Writes are denied for every repo.",
                tool_id="b14",
            ),
        ]
        data = _mod._compute_deny_summary_data([("sess.jsonl", records)])
        assert data["command_shape_counts"].get(_mod._DENY_SUMMARY_OTHER_COMMAND_SHAPE, 0) == 0
        assert data["hook_counts"].get(_mod._DENY_SUMMARY_UNMATCHED_HOOK, 0) == 0


# ---------------------------------------------------------------------------
# audit-routing
# ---------------------------------------------------------------------------


def _opus(
    content: list, *, out: int = 100, cr: int = 0, ts: str = "2026-05-19T10:00:00.000Z",
    request_id: str | None = None,
) -> dict:
    """Build an Opus assistant record with explicit usage values for audit-routing tests."""
    rec = _asst(
        "claude-opus-4-7",
        branch="main",
        ts=ts,
        content=content,
        request_id=request_id,
    )
    rec["message"]["usage"] = {
        "input_tokens": 50,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cr,
    }
    return rec


def _priced_opus(
    content: list, *, out: int = 100, cr: int = 0, ts: str = "2026-05-19T10:00:00.000Z",
    model: str = "claude-opus-5", request_id: str | None = None,
) -> dict:
    """Build a priced-Opus assistant record (default claude-opus-5, in
    _MODEL_BASE_INPUT_RATES) for audit-routing's dollar-headline tests —
    _opus()'s claude-opus-4-7 is deliberately unpriced."""
    rec = _asst(model, branch="main", ts=ts, content=content, request_id=request_id)
    rec["message"]["usage"] = {
        "input_tokens": 50,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cr,
    }
    return rec


def _priced(
    model: str,
    *,
    input: int = 0,
    cache_read: int = 0,
    ephemeral_1h: int = 0,
    ephemeral_5m: int = 0,
    output: int = 0,
    flat_cache_creation: int | None = None,
    ts: str = "2026-05-19T10:00:00.000Z",
    branch: str = "main",
    request_id: str | None = None,
    content: list | None = None,
    speed: str | None = None,
    inference_geo: str | None = None,
) -> dict:
    """Build an assistant record with explicit priced usage fields for cost tests.

    flat_cache_creation=None (the default) emits the nested cache_creation
    block from ephemeral_1h/ephemeral_5m, with the flat cache_creation_input_tokens
    field set to their sum — matching every real usage record sampled, where the
    two always agree. flat_cache_creation=N omits the nested block entirely and
    emits only the flat field (the pre-nested-block fallback shape), ignoring
    ephemeral_1h/ephemeral_5m. branch="main" by default so every pre-existing
    call site (predating --branches) is unaffected. content=None (the default)
    keeps every pre-existing call site's empty-content shape; rearm-backtest's
    boundary-detection tests pass real tool_use/tool_result blocks instead,
    needing both a realistic content shape and known, priced usage in one record.
    speed/inference_geo default to None (field absent), matching every usage
    record sampled outside fast-mode/data-residency requests.
    """
    rec = _asst(model, branch=branch, ts=ts, content=content if content is not None else [], request_id=request_id)
    usage: dict = {
        "input_tokens": input,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
    }
    if flat_cache_creation is not None:
        usage["cache_creation_input_tokens"] = flat_cache_creation
    else:
        usage["cache_creation_input_tokens"] = ephemeral_1h + ephemeral_5m
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": ephemeral_1h,
            "ephemeral_5m_input_tokens": ephemeral_5m,
        }
    if speed is not None:
        usage["speed"] = speed
    if inference_geo is not None:
        usage["inference_geo"] = inference_geo
    rec["message"]["usage"] = usage
    return rec


def _cost_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    by_project: bool = False,
    branches: str | None = None,
    summary: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "by_project": by_project,
        "branches": branches,
        "summary": summary,
    })()


def _context_distribution_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER = "## Peak absolute-token crossing thresholds"


def _extract_context_distribution_row(out: str, pct: int) -> dict[str, str]:
    """Read one context-distribution percentage-table threshold row
    ('Threshold Sessions SessShare $ DollarShare') by its leading 'NN%'
    token, rather than _table_cols' row_contains, since every row's leading
    token is a candidate substring of another row's trailing percentage
    columns. Scoped to the output before the absolute-token table's own
    header, so an absolute threshold's leading token (a plain digit, never a
    percentage) can't be misread as a percentage row by coincidence."""
    section = out.split(_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER, 1)[0]
    target = f"{pct}%"
    for line in section.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == target:
            return {
                "sessions": tokens[1],
                "sess_share": tokens[2],
                "dollars": tokens[3],
                "dollar_share": tokens[4],
            }
    raise AssertionError(f"threshold row for {pct}% not found in output:\n{out}")


def _extract_context_distribution_abs_row(out: str, threshold: int) -> dict[str, str]:
    """Read one context-distribution absolute-token-table threshold row by
    its leading comma-formatted token (e.g. '80,000'). Scoped to the output
    at/after the absolute table's own header, the section-boundary
    counterpart of _extract_context_distribution_row above."""
    if _CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER not in out:
        raise AssertionError(f"absolute-token table header not found in output:\n{out}")
    section = out.split(_CONTEXT_DISTRIBUTION_ABS_TABLE_HEADER, 1)[1]
    target = f"{threshold:,}"
    for line in section.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == target:
            return {
                "sessions": tokens[1],
                "sess_share": tokens[2],
                "dollars": tokens[3],
                "dollar_share": tokens[4],
            }
    raise AssertionError(f"absolute threshold row for {threshold:,} not found in output:\n{out}")


def _exit_plan_mode(tool_id: str = "epm1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "ExitPlanMode", "input": {}}


def _thinking_block() -> dict:
    return {"type": "thinking", "thinking": "some thought"}


def _audit_routing_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "redact": redact,
    })()


def _extract_corpus_class_tokens(out: str, cls: str) -> int:
    """Parse output-token value for a class from the corpus aggregate section."""
    # Locate the header to anchor the column index for "Output tokens".
    # "Output" is the unique leading word of the "Output tokens" column.
    header_line = next((ln for ln in out.splitlines() if "Class" in ln and "Output tokens" in ln), None)
    out_idx = header_line.split().index("Output") if header_line else 1
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(cls):
            parts = stripped.split()
            if len(parts) > out_idx:
                return int(parts[out_idx].replace(",", ""))
    return 0


def _extract_sonnet_tier_dollar_estimate(out: str) -> float:
    """Parse the dollar-weighted 'Sonnet-tier estimate: $N' headline.

    The dollar headline prints first, ahead of the token-based secondary
    diagnostic line that reuses the same 'Sonnet-tier estimate:' label — this
    regex only matches the '$'-prefixed form, so it can't accidentally read
    the token line.
    """
    match = re.search(r"Sonnet-tier estimate: \$([\d,]+\.\d{2})", out)
    assert match is not None, "dollar Sonnet-tier estimate line not found in output"
    return float(match.group(1).replace(",", ""))


class TestAuditRouting:
    def test_basic_per_class_routing(self, fake_projects, capsys):
        """One Opus turn of each class; corpus aggregate totals must match synthesized usage."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # orchestration: Agent tool_use
            _opus([_agent_use("a1", "code-writer")], out=100),
            # judgment: Skill tool_use (opens span, turn itself is judgment)
            _opus([_skill_use("s1", "code-review")], out=200),
            # user turn resets span
            _user_msg("hi", branch="main"),
            # code-write: Edit tool_use (span closed)
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            # code-read: only Read tool_use
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
            # pure-thinking: thinking block, no tool_use
            _opus([_thinking_block()], out=500),
            # other: no tool_use, no thinking
            _opus([], out=600),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert _extract_corpus_class_tokens(out, "orchestration") == 100
        assert _extract_corpus_class_tokens(out, "judgment") == 200
        assert _extract_corpus_class_tokens(out, "code-write") == 300
        assert _extract_corpus_class_tokens(out, "code-read") == 400
        assert _extract_corpus_class_tokens(out, "pure-thinking") == 500
        assert _extract_corpus_class_tokens(out, "other") == 600

    def test_judgment_span_covers_subsequent_read_and_write_turns(self, fake_projects, capsys):
        """Skill invocation opens a span; Read/Write turns inside span → judgment, not code-read/write.
        User turn resets span; Edit turn after reset → code-write."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # User turn (normal) — no plan-mode text
            _user_msg("start", branch="main"),
            # Skill invocation: opens span; turn itself → judgment
            _opus([_skill_use("s1", "code-review")], out=10),
            # Read turn inside span → judgment (not code-read)
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=20),
            # Write turn inside span → judgment (not code-write)
            _opus([{"type": "tool_use", "id": "w1", "name": "Write", "input": {}}], out=30),
            # User turn resets span
            _user_msg("continue", branch="main"),
            # Edit turn after span closed → code-write
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=40),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = skill turn (10) + read inside span (20) + write inside span (30) = 60
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # code-write = edit after reset = 40
        assert _extract_corpus_class_tokens(out, "code-write") == 40
        # code-read = 0 (the Read turn was inside the span → judgment)
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_plan_mode_span_and_exit_plan_mode(self, fake_projects, capsys):
        """Plan-mode activation → subsequent turns are judgment until ExitPlanMode.
        ExitPlanMode turn itself is still judgment; Edit after exit → code-write."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # User turn that activates plan mode
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            # Read turn inside plan-mode → judgment
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=50),
            # ExitPlanMode turn: still in plan-mode span → judgment; clears flag for next turn
            _opus([_exit_plan_mode("epm1")], out=75),
            # Edit turn after plan-mode cleared → code-write
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=90),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = read (50) + ExitPlanMode (75) = 125
        assert _extract_corpus_class_tokens(out, "judgment") == 125
        # code-write = edit after exit = 90
        assert _extract_corpus_class_tokens(out, "code-write") == 90
        # code-read = 0
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_redact_flag_anonymizes_project_names(self, fake_projects, capsys):
        """--redact replaces project dir names with private-project-1/2/…; claude-config kept."""
        # Session in the default project (-home-user-testrepo → 'home/testrepo' via derivation)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=100),
        ])
        # A second project whose derived name is 'claude-config'
        proj_cc = fake_projects.parent / "-home-user-claude-config"
        proj_cc.mkdir(parents=True)
        _write_jsonl(proj_cc / "sess.jsonl", [
            _opus([_agent_use("a2", "code-writer")], out=200),
        ])
        args = _audit_routing_args(redact=True)
        _mod.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # claude-config must appear without redaction
        assert "claude-config" in out
        # The default project name should NOT appear verbatim (redacted)
        # (the derived label for -home-user-testrepo is 'user/testrepo' or 'testrepo')
        # We just verify that private-project labels appear in the output
        assert "private-project-" in out

    def test_since_filter_excludes_out_of_window_turns(self, fake_projects, capsys):
        """--since filter: turn outside window excluded; only in-window turn appears in aggregate."""
        old_ts = "2020-01-01T00:00:00.000Z"   # far in the past — always out-of-window
        new_ts = "2099-12-31T00:00:00.000Z"   # far in the future — always in-window
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=111, ts=old_ts),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=222, ts=new_ts),
        ])
        # Use "1d" window: old_ts is excluded, new_ts is included
        args = _audit_routing_args(since="1d")
        _mod.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # Only the new_ts turn (code-write, 222) should appear
        assert _extract_corpus_class_tokens(out, "code-write") == 222
        assert _extract_corpus_class_tokens(out, "orchestration") == 0

    def test_no_opus_turns_produces_empty_aggregate(self, fake_projects, capsys):
        """Session with only Sonnet turns produces no rows and zero corpus totals."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00Z",
                  content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}]),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Corpus aggregate section must be present but all classes are zero
        assert "Corpus aggregate" in out
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_sonnet_tier_estimate_printed(self, fake_projects, capsys):
        """Sonnet-tier estimate line appears and reflects code-write + code-read total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert "Sonnet-tier estimate: 700" in out

    def test_sonnet_tier_estimate_dollar_headline_printed(self, fake_projects, capsys):
        """Dollar-weighted Sonnet-tier headline reflects code-write + code-read priced spend,
        hand-computed against claude-opus-5's base $5/MTok rate (output at its 5x multiplier)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced_opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _priced_opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Two turns, each input=50 (100 total); output 300+400=700; both code-write/code-read
        # so this is 100% of priced spend in the window.
        expected_dollars = (100 / 1_000_000 * 5.00) + (700 / 1_000_000 * 25.00)
        # abs tolerance matches the headline's own 2-decimal-place ($.NN) display rounding.
        assert _extract_sonnet_tier_dollar_estimate(out) == pytest.approx(expected_dollars, abs=0.005)
        assert "= 100% of priced Opus spend in this window" in out

    def test_dollar_headline_mixed_priced_and_unpriced_turns_not_double_counted(self, fake_projects, capsys):
        """A priced turn and an unpriced turn in the same corpus: the dollar headline
        reflects only the priced turn, and the unpriced turn is surfaced via its own
        counter rather than silently dropped or folded into the dollar figure at $0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced_opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=300),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400),  # unpriced
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        expected_dollars = (50 / 1_000_000 * 5.00) + (300 / 1_000_000 * 25.00)
        # abs tolerance matches the headline's own 2-decimal-place ($.NN) display rounding.
        assert _extract_sonnet_tier_dollar_estimate(out) == pytest.approx(expected_dollars, abs=0.005)
        # _opus()'s turn: input 50 + output 400 + cache_read 0 = 450 unpriced tokens.
        assert "1 unpriced turns / 450 tokens excluded from priced spend" in out
        # Token-based secondary line still reflects BOTH turns' output tokens, unaffected
        # by pricing — proves the token and dollar accumulators are independent.
        assert _extract_corpus_class_tokens(out, "code-write") == 300
        assert _extract_corpus_class_tokens(out, "code-read") == 400

    def test_orchestration_takes_priority_over_active_judgment_span(self):
        """orchestration is first-match: Agent turn inside an open span → orchestration, not judgment."""
        result = _mod._classify_opus_turn(
            [_agent_use("a1", "code-writer")],
            in_judgment_span=True,
            plan_mode_active=False,
        )
        assert result == "orchestration"

    def test_opus_turn_with_empty_usage_is_skipped(self, fake_projects, capsys):
        """Opus turn with empty usage dict is excluded; corpus totals stay zero."""
        rec = _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z",
                    content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}])
        # usage is {} by default from _asst — cmd_audit_routing skips falsy usage
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_judgment_span_persists_across_unrecognized_skill_invocation(self, fake_projects, capsys):
        """An unrecognized Skill call inside an open span does not close the span."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Opens span
            _opus([_skill_use("s1", "code-review")], out=10),
            # Unrecognized skill — span stays open; turn is still judgment
            _opus([_skill_use("s2", "some-unknown-skill")], out=20),
            # Read turn inside still-open span → judgment, not code-read
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
            # User turn closes span
            _user_msg("continue", branch="main"),
            # Read turn after span closed → code-read
            _opus([{"type": "tool_use", "id": "r2", "name": "Read", "input": {}}], out=40),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # All three turns inside the span (10 + 20 + 30) → judgment
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # Only the post-span Read turn → code-read
        assert _extract_corpus_class_tokens(out, "code-read") == 40

    def test_combined_plan_mode_and_judgment_span_independent_tracking(self, fake_projects, capsys):
        """ExitPlanMode clears plan_mode_active but an open judgment span keeps the classification."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Activate plan-mode
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            # Judgment-skill invocation: opens span AND plan-mode is active
            _opus([_skill_use("s1", "code-review")], out=10),
            # ExitPlanMode: clears plan_mode_active; span from code-review still open
            _opus([_exit_plan_mode("epm1")], out=20),
            # Turn after ExitPlanMode: span still open (no user turn yet) → judgment
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
            # User turn resets span
            _user_msg("done", branch="main"),
            # Read turn after both flags cleared → code-read
            _opus([{"type": "tool_use", "id": "r2", "name": "Read", "input": {}}], out=40),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # judgment = skill-open (10) + ExitPlanMode (20) + post-exit-still-in-span read (30) = 60
        assert _extract_corpus_class_tokens(out, "judgment") == 60
        # code-read = post-user-reset read = 40
        assert _extract_corpus_class_tokens(out, "code-read") == 40

    def test_since_filter_excludes_turn_with_missing_timestamp(self, fake_projects, capsys):
        """With --since active, turns lacking a timestamp field are excluded."""
        rec_no_ts = _asst("claude-opus-4-7", branch="main",
                           content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}])
        rec_no_ts["message"]["usage"] = {"input_tokens": 50, "output_tokens": 200,
                                          "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 0}
        # No "timestamp" key on the record
        assert "timestamp" not in rec_no_ts
        _write_jsonl(fake_projects / "sess.jsonl", [rec_no_ts])
        args = _audit_routing_args(since="1d")
        _mod.cmd_audit_routing(args)
        out = capsys.readouterr().out
        # Turn with no timestamp is excluded by the --since filter
        assert _extract_corpus_class_tokens(out, "code-write") == 0

    def test_request_id_group_later_block_skill_invocation_still_opens_judgment_span(
        self, fake_projects, capsys
    ):
        """A requestId group whose Skill tool_use block is not the group's
        first content block still opens a judgment span for that turn and the
        one after it — dedup merges every block in the group in order, so a
        later block's signal is never dropped the way keeping only the
        group's first record would drop it."""
        ts = "2026-05-19T10:00:00.000Z"
        rec_a = _opus([_thinking_block()], out=20, ts=ts, request_id="req-1")
        rec_b = _opus([_skill_use("s1", "code-review")], out=20, ts=ts, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [
            rec_a, rec_b,
            # Next turn, still inside the span the merged group's Skill block opened.
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=30),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Merged group (20, byte-identical usage priced/counted once) + the
        # next turn still in-span (30) = 50 judgment output tokens. A dedup
        # that kept only the group's first record would drop the Skill block,
        # classify the group as pure-thinking, never open the span, and
        # misclassify the next Read turn as code-read instead.
        assert _extract_corpus_class_tokens(out, "judgment") == 50
        assert _extract_corpus_class_tokens(out, "code-read") == 0

    def test_request_id_group_later_block_exit_plan_mode_clears_plan_mode_for_next_turn(
        self, fake_projects, capsys
    ):
        """A requestId group whose ExitPlanMode tool_use is not the group's
        first content block still clears plan-mode for the turn after it —
        dedup merges every block in the group in order, so a dedup that kept
        only the group's first record would drop the ExitPlanMode block,
        leave plan-mode stuck active, and misclassify the next turn as
        judgment instead of code-write."""
        rec_a = _opus([_thinking_block()], out=75, request_id="req-1")
        rec_b = _opus([_exit_plan_mode("epm1")], out=75, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "Plan mode is active"}], branch="main"),
            rec_a, rec_b,
            # Turn after the merged group, only code-write if plan-mode was
            # actually cleared by the group's (later-block) ExitPlanMode.
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=90),
        ])
        _mod.cmd_audit_routing(_audit_routing_args())
        out = capsys.readouterr().out
        # Merged group (75, byte-identical usage priced/counted once) is
        # still judgment (plan-mode was active during its own classification).
        assert _extract_corpus_class_tokens(out, "judgment") == 75
        assert _extract_corpus_class_tokens(out, "code-write") == 90

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        """A malformed --since value fails closed with the audit-routing-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod.cmd_audit_routing(_audit_routing_args(since="not-a-window"))
        assert "audit-routing: --since: expected Nd like '35d'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


class TestCost:
    def test_price_turn_hand_computed_dollar_total(self):
        """_price_turn's per-class dollars match a hand-computed total, read back
        through the named extractor (_price_turn itself) rather than a string
        match on formatted `$`-prefixed, comma-separated table output."""
        usage = _priced(
            "claude-sonnet-5",
            input=100_000, cache_read=200_000, ephemeral_1h=10_000, ephemeral_5m=20_000, output=5_000,
        )["message"]["usage"]
        dollars, context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert context_at_turn == 100_000 + 200_000 + 10_000 + 20_000
        # Sonnet 5 base $2/MTok: output 5x=$10, cache_write_5m 1.25x=$2.5,
        # cache_write_1h 2x=$4, cache_read 0.1x=$0.2, input=$2 (all per MTok).
        assert dollars["input"] == pytest.approx(100_000 / 1_000_000 * 2.0)
        assert dollars["output"] == pytest.approx(5_000 / 1_000_000 * 10.0)
        assert dollars["cache_read"] == pytest.approx(200_000 / 1_000_000 * 0.2)
        assert dollars["cache_write_1h"] == pytest.approx(10_000 / 1_000_000 * 4.0)
        assert dollars["cache_write_5m"] == pytest.approx(20_000 / 1_000_000 * 2.5)

    def test_raw_dollars_summed_before_rounding_not_after(self, fake_projects, capsys):
        """Three sub-cent turns ($0.166 each) sum to $0.498, rendering '0.50' — a
        round-then-sum bug would render each turn as $0.17 and total '0.51'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=83_000),
            _priced("claude-sonnet-5", input=83_000),
            _priced("claude-sonnet-5", input=83_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "0.50"

    def test_nested_cache_creation_priced_separately_1h_and_5m(self):
        """Nested ephemeral_1h/ephemeral_5m tokens are split and never summed together."""
        usage = _priced("claude-sonnet-5", ephemeral_1h=10_000, ephemeral_5m=20_000)["message"]["usage"]
        assert _mod._cache_write_split(usage) == (10_000, 20_000)

    def test_flat_and_nested_both_present_not_double_counted(self):
        """flat cache_creation_input_tokens sums the nested fields on every real
        record (row2); the split must read only the nested block, never add
        flat on top of it."""
        usage = _priced("claude-sonnet-5", ephemeral_1h=10_000, ephemeral_5m=20_000)["message"]["usage"]
        assert usage["cache_creation_input_tokens"] == 30_000  # flat present too
        eph_1h, eph_5m = _mod._cache_write_split(usage)
        assert eph_1h + eph_5m == 30_000  # not 60,000 (double-counted)

    def test_flat_cache_creation_fallback_priced_as_5m_only(self):
        """Nested block absent (the untested-by-real-data fallback path): the flat
        total is priced entirely as 5m, at 1.25x — never split into 1h at 2x."""
        usage = _priced("claude-sonnet-5", flat_cache_creation=40_000)["message"]["usage"]
        assert "cache_creation" not in usage
        assert _mod._cache_write_split(usage) == (0, 40_000)
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["cache_write_5m"] == pytest.approx(40_000 / 1_000_000 * 2.5)
        assert dollars["cache_write_1h"] == 0.0

    def test_empty_nested_cache_creation_block_not_treated_as_absent(self):
        """A present-but-empty nested block ({}) means zero of both ephemeral kinds —
        it must not fall through to the flat field as if the nested block were
        absent, even though both {} and a missing key are falsy."""
        usage = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 40_000, "cache_creation": {},
        }
        assert _mod._cache_write_split(usage) == (0, 0)

    def test_per_model_id_selection_sonnet5_vs_sonnet46(self, fake_projects, capsys):
        """Sonnet 5 ($2 base) and Sonnet 4.6 ($3 base) price the same input tokens differently."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),
            _priced("claude-sonnet-4-6", input=1_000_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        sonnet5 = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-5")
        sonnet46 = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-4-6")
        assert sonnet5["$"] == "2.00"
        assert sonnet46["$"] == "3.00"

    def test_claude_sonnet_4_5_prices_at_vendor_rate(self):
        """claude-sonnet-4-5's five derived rates match the vendor table ($3
        input / $15 output / $3.75 5m-write / $6 1h-write / $0.30 cache-read),
        via the same _model_rates derivation every other model in the table
        uses."""
        rates = _mod._model_rates("claude-sonnet-4-5")
        assert rates is not None
        assert rates["input"] == pytest.approx(3.00)
        assert rates["output"] == pytest.approx(15.00)
        assert rates["cache_write_5m"] == pytest.approx(3.75)
        assert rates["cache_write_1h"] == pytest.approx(6.00)
        assert rates["cache_read"] == pytest.approx(0.30)

    def test_claude_sonnet_4_5_dated_snapshot_unpriced_but_200k_bucketed(self):
        """_MODEL_BASE_INPUT_RATES is an exact-match dict: a dated-snapshot
        variant of claude-sonnet-4-5 still 200k-context-buckets correctly
        (prefix match) but prices as unpriced (exact-match miss) -- pins the
        asymmetry documented on the pricing-table entry."""
        dated_model = "claude-sonnet-4-5-20260115"
        assert _mod._model_rates(dated_model) is None
        assert _mod._context_window_for_model(dated_model) == 200_000

    def test_unknown_model_id_surfaced_and_excluded_from_total(self, fake_projects, capsys):
        """Unknown model IDs are named in the model table and their tokens counted
        separately, never silently folded into the priced total at $0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000, output=500_000, cache_read=200_000),
            _priced("claude-sonnet-5", input=1_000_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "<synthetic>" in out
        assert "unpriced" in out
        assert _extract_unpriced_total(out) == 1_000_000 + 500_000 + 200_000
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "2.00"  # only the priced sonnet-5 turn's $2/MTok * 1M input tokens

    def test_mixed_model_ids_within_session_sums_per_turn(self, fake_projects, capsys):
        """A session mixing Sonnet 5 and Sonnet 4.6 turns prices each against its own
        model ID; the session total is their sum, not a dominant-family pick
        (token-analyzer.py:16-23's per-session _fam shape, which this must not copy)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),      # $2.00
            _priced("claude-sonnet-4-6", input=1_000_000),    # $3.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Session", row_contains="private-project-1")
        assert cols["$"] == "5.00"

    def test_context_bucket_threshold_boundaries(self):
        """199,999 is <200k; exactly 200,000 and 200,001 are >=200k (inclusive edge)."""
        assert _mod._context_bucket(199_999) == "<200k"
        assert _mod._context_bucket(200_000) == ">=200k"
        assert _mod._context_bucket(200_001) == ">=200k"

    def test_price_turn_rate_independent_of_context_bucket(self):
        """The $/token rate does not change with context size — pins row4 (no
        >200k long-context premium applies to any model in the corpus) against
        a future regression."""
        usage_small = _priced("claude-sonnet-5", input=199_999)["message"]["usage"]
        usage_large = _priced("claude-sonnet-5", input=200_001)["message"]["usage"]
        dollars_small, ctx_small, _ = _mod._price_turn("claude-sonnet-5", usage_small)
        dollars_large, ctx_large, _ = _mod._price_turn("claude-sonnet-5", usage_large)
        assert _mod._context_bucket(ctx_small) == "<200k"
        assert _mod._context_bucket(ctx_large) == ">=200k"
        assert dollars_small["input"] / 199_999 == pytest.approx(dollars_large["input"] / 200_001)

    def test_cost_by_context_bucket_section_reflects_boundary(self, fake_projects, capsys):
        """A <200k turn and a >=200k turn land in their respective bucket rows."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000),    # <200k, $0.20
            _priced("claude-sonnet-5", input=1_000_000),  # >=200k, $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        under = _table_cols(out, header_contains="Bucket", row_contains="<200k")
        over = _table_cols(out, header_contains="Bucket", row_contains=">=200k")
        assert under["$"] == "0.20"
        assert over["$"] == "2.00"

    def test_empty_corpus_renders_clean_zero_state(self, fake_projects, capsys):
        """No priced turns at all: every share renders 0.0% and there's no traceback."""
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "0.0%" in out
        assert "(no priced turns in range)" in out

    def test_staleness_banner_fires_when_today_past_expires(self, fake_projects, capsys):
        """today past the default fetch-date+90d re-verify-by date: the banner
        fires in the same output block as the dollar tables, not a separate
        log line."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 11, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" in out
        assert "claude-sonnet-5" in out.split("## Cost by token class")[0]

    def test_staleness_banner_absent_when_today_before_expires(self, fake_projects, capsys):
        """today before expiry: no banner — a one-direction test would pass even
        if the banner always printed."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_sonnet_5_no_longer_flagged_stale_by_cancelled_promo_expiry_date(self, fake_projects, capsys):
        """The vendor cancelled Sonnet 5's Sept 1, 2026 rate increase, so
        today=2026-09-01 (past the old, now-removed 2026-08-31 promo-expiry
        date) must not fire the banner -- Sonnet 5 uses the same
        _DEFAULT_REVERIFY_BY schedule as every other model now."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 9, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_sidechain_turns_priced_exactly_once(self, fake_projects, capsys):
        """cost prices subagent (isSidechain) turns via include_subagents=True —
        exactly once, not skipped and not duplicated."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main thread: $2.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _priced("claude-sonnet-5", input=1_000_000),  # sidechain: $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "4.00"

    def test_multi_record_request_id_group_priced_exactly_once(self, tmp_path, monkeypatch, capsys):
        """Three assistant records sharing one requestId (one JSONL record per
        content block, as Claude Code writes for a single API call) carry a
        byte-identical usage dict — cost prices the group's usage once, not
        once per record, and counts it as one priced turn, not three.

        Uses --summary (rather than this class's usual fake_projects fixture)
        since priced_turn_count is only rendered in --summary's output, and
        --summary requires --this-repo -- hence the git-worktree-list/getcwd
        mocks, mirroring TestCostSummary's own fixture pattern.
        """
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        for rec in recs:
            rec["message"]["usage"] = dict(usage)
        _write_jsonl(projects / "-repo-main" / "sess.jsonl", recs)
        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        # $2.00 (claude-sonnet-5's $2/MTok input rate on 1M input tokens) once,
        # not $6.00 for pricing the group's usage three times over.
        assert _extract_md_grand_total(out) == pytest.approx(2.00)
        # Three content-block records collapse into one priced turn, not three.
        assert "1 priced turns" in out

    def test_sidechain_multi_record_request_id_group_composes_with_sidechain_dedup(
        self, fake_projects, capsys
    ):
        """A sidechain (subagent-file) request split into two content-block
        records shares one requestId and is priced once for its own group —
        this composes with, but is a different mechanism from,
        test_sidechain_turns_priced_exactly_once's invariant above (dedup of
        subagent *files* vs. the main file, not requestId dedup)."""
        session_id = "sess-side-multi"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main thread: $2.00
        ])
        side_usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        side_a = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}],
                        request_id="req-side", sidechain=True)
        side_a["message"]["usage"] = dict(side_usage)
        side_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}],
                        request_id="req-side", sidechain=True)
        side_b["message"]["usage"] = dict(side_usage)
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [side_a, side_b])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        # main $2.00 + sidechain $2.00 (priced once despite 2 content-block records) = $4.00.
        assert cols["$"] == "4.00"

    def test_redact_proj_label_map_miss_returns_opaque_token_not_raw_name(self):
        """A project label absent from the map renders as the fixed opaque token,
        never the raw project name — a map miss must fail closed. Built with
        two real labels so the map can express "one mapped, one missing"; a
        single-label fixture can't distinguish a miss from an empty map."""
        redact_map = {"project-a": "private-project-1"}  # "project-b" deliberately absent
        assert _mod._redact_proj_label("project-a", redact_map) == "private-project-1"
        assert _mod._redact_proj_label("project-b", redact_map) == _mod._REDACT_MAP_MISS_TOKEN
        assert _mod._REDACT_MAP_MISS_TOKEN != "project-b"

    def test_redact_session_id_map_miss_returns_opaque_token_not_raw_id(self):
        """Same fail-closed contract as the project-label map, for session IDs:
        a session_id absent from the run-scoped map renders as the fixed
        opaque token, never the raw session ID."""
        session_map = {"sess-real-id": "session-1"}  # "sess-other-id" deliberately absent
        assert _mod._redact_session_id("sess-real-id", session_map) == "session-1"
        assert _mod._redact_session_id("sess-other-id", session_map) == _mod.redaction._REDACT_SESSION_MISS_TOKEN
        assert _mod.redaction._REDACT_SESSION_MISS_TOKEN != "sess-other-id"

    def test_redact_proj_label_claude_config_passthrough_preserved(self):
        """claude-config still passes through unredacted after the fail-closed rewrite."""
        assert _mod._redact_proj_label("claude-config", {}) == "claude-config"

    def test_cost_redact_default_hides_project_names_and_session_ids(self, fake_projects, capsys):
        """Default (no --no-redact): raw project labels and raw session IDs are
        absent from stdout across every cost section — catches a partial leak,
        not just the total no-op test_redact_flag_anonymizes_project_names checks."""
        _write_jsonl(fake_projects / "sess-one.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        proj2 = fake_projects.parent / "-home-user-otherrepo"
        proj2.mkdir(parents=True)
        _write_jsonl(proj2 / "sess-two.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "testrepo" not in out
        assert "otherrepo" not in out
        assert "sess-one" not in out
        assert "sess-two" not in out
        assert "private-project-1" in out
        assert "private-project-2" in out

    def test_cost_no_redact_emits_real_label_and_session_id(self, fake_projects, capsys):
        """--no-redact emits the real project label and real session ID — proving
        the default redaction didn't silently become the only mode."""
        _write_jsonl(fake_projects / "sess-plain.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "testrepo" in out
        assert "sess-plain" in out

    def test_shared_redact_map_binds_same_project_across_cost_and_audit_routing(self, fake_projects, capsys):
        """cost and audit-routing share _build_redact_map: a project binds to the
        same private-project-N placeholder from both, even though cost's own
        --projects filter here is narrower than the full corpus — a per-command
        map built only from the narrowed glob would assign zzzlast
        private-project-1 instead of -2, since it would be the only project seen."""
        proj_b = fake_projects.parent / "-home-user-zzzlast"
        proj_b.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess-a.jsonl", [_opus([_agent_use("a1", "code-writer")], out=100)])
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=100),
            _priced("claude-sonnet-5", input=1_000_000),
        ])

        expected_map = _mod._build_redact_map()
        assert expected_map["testrepo"] == "private-project-1"
        assert expected_map["zzzlast"] == "private-project-2"

        _mod.cmd_audit_routing(_audit_routing_args(projects="*", redact=True))
        audit_out = capsys.readouterr().out
        assert "private-project-2" in audit_out

        _mod._cost_report(_cost_args(projects="-home-user-zzzlast", no_redact=False), date(2026, 8, 2))
        cost_out = capsys.readouterr().out
        assert "private-project-2" in cost_out
        assert "private-project-1" not in cost_out  # testrepo's session wasn't in this narrowed run

    def test_since_filter_excludes_out_of_window_turn(self, fake_projects, capsys):
        """--since window: a turn timestamped outside the window is excluded from the total."""
        old_ts = "2020-01-01T00:00:00.000Z"   # far in the past — always out-of-window
        new_ts = "2099-12-31T00:00:00.000Z"   # far in the future — always in-window
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts=old_ts),
            _priced("claude-sonnet-5", input=2_000_000, ts=new_ts),
        ])
        _mod._cost_report(_cost_args(since="1d"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "4.00"  # only the 2,000,000-input-token turn is in-window

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, fake_projects, capsys):
        """A malformed --since value fails closed with the cost-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(since="not-a-window"), date(2026, 8, 2))
        assert "cost: --since: expected Nd like '35d'" in capsys.readouterr().err

    def test_top_n_truncates_session_rows(self, fake_projects, capsys):
        """--top N keeps only the N highest-dollar sessions; excluded sessions don't appear."""
        for i in range(5):
            _write_jsonl(fake_projects / f"sess-{i}.jsonl", [_priced("claude-sonnet-5", input=(i + 1) * 1_000_000)])
        _mod._cost_report(_cost_args(top=2), date(2026, 8, 2))
        out = capsys.readouterr().out
        top_section = out.split("## Top 2 sessions by dollars")[1]
        session_lines = [ln for ln in top_section.splitlines() if ln.startswith("session-")]
        assert len(session_lines) == 2
        # The two highest-dollar sessions (i=3,4 -> $8.00, $10.00) survive; i=0,1,2 are truncated.
        assert "10.00" in top_section
        assert "8.00" in top_section
        assert "2.00" not in top_section

    def test_this_repo_scopes_via_resolve_project_scope(self, tmp_path, monkeypatch, capsys):
        """cost wires --this-repo through the shared _resolve_project_scope helper
        like every other subcommand — pinned here because cost predates that
        convention and a prior rebase silently left it on a bespoke --projects-only
        argument with no --this-repo support at all."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert (
            "COST SOURCES (this repo (1 project dirs); "
            "1 root (no ~/.claude/transcript-config-dirs declared))"
        ) in out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "2.00"

    def test_redact_map_ordinal_shifts_with_out_of_scope_project(self, tmp_path, monkeypatch, capsys):
        """_build_redact_map's ordinals are assigned over the full local corpus,
        not the caller's --this-repo scope: pins that a --this-repo report's
        printed private-project-N number depends on other private projects that
        never appear in the report — the ordinal side-channel documented on
        _build_redact_map. "aardvark" sorts before "main" (this repo's derived
        label), so its mere presence on disk — outside --this-repo scope and
        never printed — bumps "main" from private-project-1 to private-project-2."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        # Run 1: only this repo's own project dir exists on disk.
        solo_projects = tmp_path / "solo" / "projects"
        mine_solo = solo_projects / "-repo-main"
        mine_solo.mkdir(parents=True)
        _write_jsonl(mine_solo / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", solo_projects)
        _mod._cost_report(_cost_args(this_repo=True, no_redact=False), date(2026, 8, 2))
        solo_out = capsys.readouterr().out
        assert "private-project-1" in solo_out

        # Run 2: an out-of-scope project ("aardvark") sorts before this repo's
        # own label and is never surfaced by --this-repo, yet still shifts the
        # ordinal assigned to this repo's project.
        shared_projects = tmp_path / "shared" / "projects"
        mine_shared = shared_projects / "-repo-main"
        mine_shared.mkdir(parents=True)
        other = shared_projects / "-home-user-aardvark"
        other.mkdir(parents=True)
        _write_jsonl(mine_shared / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _write_jsonl(other / "o.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", shared_projects)
        _mod._cost_report(_cost_args(this_repo=True, no_redact=False), date(2026, 8, 2))
        shared_out = capsys.readouterr().out
        assert "private-project-2" in shared_out
        assert "private-project-1" not in shared_out
        assert "aardvark" not in shared_out  # the out-of-scope project itself never prints

    def test_redact_map_ordinal_unaffected_by_out_of_scope_project_sorting_after(
        self, tmp_path, monkeypatch, capsys
    ):
        """Companion to the shifts-before case: an out-of-scope project whose
        derived label sorts *after* the in-scope one leaves the in-scope
        project's ordinal unchanged, since _build_redact_map assigns ordinals
        in alphabetical order. "zzz-other" sorts after "main", so it must not
        bump "main" off private-project-1."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        shared_projects = tmp_path / "shared" / "projects"
        mine_shared = shared_projects / "-repo-main"
        mine_shared.mkdir(parents=True)
        other = shared_projects / "-home-user-zzz-other"
        other.mkdir(parents=True)
        _write_jsonl(mine_shared / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _write_jsonl(other / "o.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", shared_projects)
        _mod._cost_report(_cost_args(this_repo=True, no_redact=False), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "private-project-1" in out
        assert "zzz-other" not in out


def _write_cost_root(base: Path, name: str, proj_slug: str, session_id: str, records: list[dict]) -> Path:
    """Build one --config-dir root's project-dir tree — same shape as
    fake_projects' own PROJECTS_DIR (the root directly contains project-slug
    subdirectories, no extra projects/ layer), parameterized so multi-root
    tests can build more than one root under the same tmp_path."""
    root = base / name
    proj = root / proj_slug
    proj.mkdir(parents=True)
    _write_jsonl(proj / f"{session_id}.jsonl", records)
    return root


# ---------------------------------------------------------------------------
# cost — multi-root (--config-dir)
# ---------------------------------------------------------------------------


class TestCostResolveRoots:
    """_resolve_cost_roots: the --config-dir CLI-boundary contract, mirroring
    post-crash-sessions.py:1067-1111's --config-dir (transcript-analysis.py's
    own sibling implementation of the same contract)."""

    def test_default_root_alone_when_no_extra_config_dirs(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        roots = _mod._resolve_cost_roots(_cost_args())
        assert roots == [default_dir / "projects"]

    def test_single_extra_config_dir_allowed(self, tmp_path, monkeypatch, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(acct_b)]))
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_extra_roots_appended_in_argument_order(self, tmp_path, monkeypatch, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        acct_c = fake_config_dir_factory("acct-c")
        roots = _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(acct_b), str(acct_c)]))
        assert roots == [default_dir / "projects", acct_b / "projects", acct_c / "projects"]

    def test_duplicate_root_deduped_by_resolve_not_string_equality(self, tmp_path, monkeypatch, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        # Same directory, supplied twice under different (but equal-once-
        # resolved) spellings — the dedup guard is by .resolve(), not string
        # equality.
        roots = _mod._resolve_cost_roots(
            _cost_args(extra_config_dirs=[str(acct_b), str(acct_b) + "/."])
        )
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_nonexistent_root_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        missing = tmp_path / "does-not-exist"
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(missing)]))
        assert exc_info.value.code == 2
        assert str(missing) in capsys.readouterr().err

    def test_root_without_projects_subdir_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(bogus)]))
        assert exc_info.value.code == 2
        assert str(bogus) in capsys.readouterr().err

    def test_this_repo_and_config_dir_compose_returning_both_roots(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        """--this-repo no longer refuses an explicit --config-dir extra:
        _iter_scoped_sessions matches slugs by basename and
        _path_to_project_slug derives them from `git worktree list` alone,
        both root-independent, so the refusal's own rationale ("--this-repo
        cannot filter a foreign config dir's worktrees") didn't match the
        mechanism. _resolve_cost_roots just returns the union of both roots;
        --this-repo's own filtering happens downstream in
        _resolve_project_scope."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(_cost_args(this_repo=True, extra_config_dirs=[str(acct_b)]))
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root(self, tmp_path, monkeypatch):
        """--no-redact with no --config-dir (single root) is unaffected."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        roots = _mod._resolve_cost_roots(_cost_args(no_redact=True))
        assert roots == [default_dir / "projects"]

    def test_summary_narrows_to_active_root_only_ignoring_declared_and_config_dir(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        """Mechanism 1a: --summary resolves to config_dir()/projects alone,
        skipping both declared_transcript_roots() and --config-dir extras
        entirely -- --summary already refuses --config-dir in combination at
        _cost_report, so this only has to prove the union itself never
        forms."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        monkeypatch.setattr(_mod.scope, "declared_transcript_roots", lambda: [tmp_path / "declared-account"])
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(_cost_args(summary=True, extra_config_dirs=[str(acct_b)]))
        assert roots == [default_dir / "projects"]

    def test_declared_roots_union_unaffected_for_non_cost_subcommand(self, tmp_path, monkeypatch):
        """Mechanism 1 narrows _resolve_cost_roots only for subcommand ==
        "cost" -- a populated declared-roots file still unions for every
        other _SUBCOMMANDS_WITH_OWN_CONFIG_DIR caller, since only cost's
        argparser defines --summary today and the gate is on `subcommand`,
        not on a bare summary_mode check."""
        active = tmp_path / "acct-a"
        (active / "projects").mkdir(parents=True)
        other = tmp_path / "acct-b"
        (other / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active))
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{other}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        roots = _mod._resolve_cost_roots(_context_distribution_args(), subcommand="context-distribution")
        assert roots == [active / "projects", other / "projects"]


class TestCostMultiRootReport:
    """_cost_report's roots parameter: per-root scan diagnostics, the three
    distinct empty states, and the overlapping-root double-count guard."""

    def test_scan_summary_line_printed_per_root(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "cost: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out

    def test_this_repo_scan_count_reflects_repo_scope_not_whole_root(
        self, tmp_path, monkeypatch, capsys
    ):
        """--this-repo's diagnostic scan must count only the repo-scoped slug's
        transcripts, not _projects_glob's "*" fallback (always "*" under
        --this-repo) — otherwise a genuinely-empty repo-scoped result hides
        behind an unrelated project's nonzero count under the same root,
        reintroducing the silent-zero failure Step 8 exists to surface."""
        root = _write_cost_root(tmp_path, "acct-a", "-other-unrelated-project", "sess-a",
                                 [_priced("claude-sonnet-5", input=1_000_000)])
        # This repo's own slug dir exists but has zero transcripts — the
        # genuinely-empty case this diagnostic must not mask.
        (root / "-home-user-this-repo").mkdir()
        args = _cost_args(this_repo=True)
        args._this_repo_slugs = ["-home-user-this-repo"]
        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: args._this_repo_slugs)

        _mod._cost_report(args, date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        assert "cost: account-1: scanned 0 transcripts, 0 skipped (unreadable)" in out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out

    def test_permission_error_while_scanning_root_caught_and_reported_per_root(
        self, tmp_path, capsys
    ):
        """A real unreadable root must not propagate and abort the whole
        report — it's caught, reported for that root only (root_b's own scan
        and priced spend are unaffected), and the raw path embedded in
        str(exc) is suppressed under default redaction. Uses a genuine
        os.chmod'd directory rather than mocking _scan_root_transcripts —
        review found that pathlib.Path.glob silently swallows OSError while
        walking an unreadable directory rather than propagating it, so a
        mock-based test would pass even if the real permission-check path
        (os.access, in _scan_root_transcripts) were removed entirely."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        os.chmod(root_a, 0o000)
        try:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        finally:
            os.chmod(root_a, 0o755)  # restore before tmp_path teardown

        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert "cannot scan" in err
        assert str(root_a) not in err
        assert "cost: account-1: scanned 0 transcripts, 0 skipped (unreadable)" in out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out
        # occurrence=1: the global section, not one of the new per-account
        # "## Cost by account" sections this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "2.00"

    def test_empty_state_zero_transcripts_opened_warns_per_root_not_masked(self, tmp_path, capsys):
        """State (a)/(b): a root with no *.jsonl at all fires its own warning,
        not masked by a sibling root's non-zero scan — the original silent-
        zero bug wearing a per-root hat. account-N is assigned by resolved-path
        sort (_redaction_ordinals), not by roots= list order — "acct-b" sorts
        before "acct-empty", so root_b is account-1 despite being passed second."""
        empty_root = tmp_path / "acct-empty"
        empty_root.mkdir(parents=True)  # exists, but holds no project dirs at all
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[empty_root, root_b])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-2: no transcripts found for this scope" in out
        assert "WARNING: cost: account-1" not in out

    def test_empty_state_project_dir_with_no_jsonl_also_warns(self, tmp_path, capsys):
        """State (b) specifically: a root whose project dir exists but holds
        no *.jsonl files at all — same warning predicate as an empty root."""
        root = tmp_path / "acct-a"
        (root / "-home-user-repo").mkdir(parents=True)
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out

    def test_empty_state_transcripts_present_zero_priced_turns(self, fake_projects, capsys):
        """State (c): a transcript exists but carries no priced usage — the
        existing zero-state line, unchanged, and no scan warning (a
        transcript was found)."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "(no priced turns in range)" in out
        assert "WARNING: cost:" not in out

    def test_empty_state_priced_spend_is_normal_report(self, fake_projects, capsys):
        """Priced spend: neither the scan warning nor the zero-state line appears."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out
        assert "(no priced turns in range)" not in out

    def test_three_empty_state_messages_are_textually_distinct(self, tmp_path, fake_projects, capsys):
        """Pins that Step 8's three states render different text — a single
        collapsed message would pass every test above individually but fail
        this one."""
        empty_root = tmp_path / "acct-empty"
        empty_root.mkdir(parents=True)
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[empty_root])
        zero_transcripts_out = capsys.readouterr().out

        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        zero_priced_out = capsys.readouterr().out

        _write_jsonl(fake_projects / "sess2.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        priced_out = capsys.readouterr().out

        assert "WARNING: cost:" in zero_transcripts_out
        assert "WARNING: cost:" not in zero_priced_out
        assert "WARNING: cost:" not in priced_out
        assert "(no priced turns in range)" in zero_priced_out
        assert "(no priced turns in range)" not in priced_out
        assert zero_transcripts_out != zero_priced_out != priced_out

    def test_same_project_slug_under_two_roots_sums_not_doubles(self, tmp_path, capsys):
        """Two distinct roots each hold a project dir with the identical slug
        name — genuinely different directories (different accounts), so both
        sessions count once each; the grand total is their sum, never
        doubled nor collapsed to just one."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])   # $2.00
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo", "sess-b",
                                   [_priced("claude-sonnet-5", input=2_000_000)])   # $4.00
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        # occurrence=1: the global section, not one of the new per-account
        # "## Cost by account" sections this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "6.00"

    def test_nested_root_alias_does_not_double_count(self, tmp_path, capsys):
        """One supplied root is a symlink, placed inside another root's own
        directory tree, that resolves back to that same root — nested inside
        another root as well as identical once resolved. The multi-root scan
        must dedupe by resolved real path so the grand total isn't doubled."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        nested_alias = root_a / "-home-user-repo" / "nested-alias-back-to-root-a"
        nested_alias.symlink_to(root_a)
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, nested_alias])
        out = capsys.readouterr().out
        # occurrence=1: the global section, not the new per-account "## Cost
        # by account" section this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "2.00"


class TestCostCorpusCoverageWarning:
    """--since window vs. a root's actual earliest turn: a root whose local
    corpus starts well after the requested window start must warn, not
    silently under-report -- one line per short root, never masked by a
    sibling root's full coverage."""

    def test_warning_fires_when_root_short_of_since_window(self, fake_projects, monkeypatch, capsys):
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        earliest_ts = since_ts + 3 * 86400  # 3 days after window start, well over the 1-day threshold
        earliest_iso = datetime.fromtimestamp(earliest_ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=earliest_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: earliest turn found is" in out
        assert _mod._fmt_date(earliest_ts) in out
        assert _mod._fmt_date(since_ts) in out

    def test_warning_silent_when_root_fully_covers_window(self, fake_projects, monkeypatch, capsys):
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        earliest_iso = datetime.fromtimestamp(since_ts - 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=earliest_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out

    def test_warning_silent_when_since_not_given(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000, ts="2020-01-01T00:00:00.000Z"),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out

    def test_boundary_exactly_24h_short_is_silent(self, fake_projects, monkeypatch, capsys):
        """Exactly 1 day after the window start is not "more than" 1 day
        short -- the inclusive edge of the no-warning side."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        boundary_iso = datetime.fromtimestamp(since_ts + 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=boundary_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out

    def test_boundary_24h_plus_epsilon_short_fires(self, fake_projects, monkeypatch, capsys):
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        over_iso = datetime.fromtimestamp(since_ts + 86400 + 1, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=over_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: earliest turn found is" in out

    def test_two_root_case_names_short_root_not_masked_by_covered_root(self, tmp_path, monkeypatch, capsys):
        """acct-covered sorts before acct-short (resolved-path ordering), so
        acct-covered is account-1 and acct-short is account-2 -- the
        well-covered root's account-1 label must not appear in the warning."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        covered_iso = datetime.fromtimestamp(since_ts - 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        short_iso = datetime.fromtimestamp(since_ts + 3 * 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        root_covered = _write_cost_root(tmp_path, "acct-covered", "-home-user-repo-a", "sess-a",
                                         [_priced("claude-sonnet-5", input=1_000, ts=covered_iso)])
        root_short = _write_cost_root(tmp_path, "acct-short", "-home-user-repo-b", "sess-b",
                                       [_priced("claude-sonnet-5", input=1_000, ts=short_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[root_covered, root_short])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-2: earliest turn found is" in out
        assert "WARNING: cost: account-1:" not in out


class TestScanRootTranscripts:
    """Direct unit tests for _scan_root_transcripts — neither its glob-count
    behavior nor its per-file skipped-counting was exercised at this layer
    before review; every prior assertion on 'skipped' asserted 0."""

    def test_unreadable_root_raises_permission_error(self, tmp_path):
        """os.access is an explicit probe, not reliance on Path.glob to raise
        — glob silently swallows OSError while walking an unreadable
        directory rather than propagating it (verified empirically during
        review), so this pins the probe itself, not glob's behavior."""
        root = tmp_path / "locked"
        root.mkdir()
        os.chmod(root, 0o000)
        try:
            with pytest.raises(PermissionError):
                _mod._scan_root_transcripts(root, "*")
        finally:
            os.chmod(root, 0o755)

    def test_readable_root_with_no_matches_returns_zero_zero(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        assert _mod._scan_root_transcripts(root, "*") == (0, 0)

    def test_skipped_counts_unreadable_file_separately_from_scanned(self, tmp_path):
        proj = tmp_path / "-home-user-repo"
        proj.mkdir()
        readable = proj / "readable.jsonl"
        readable.write_text("{}\n")
        locked = proj / "locked.jsonl"
        locked.write_text("{}\n")
        os.chmod(locked, 0o000)
        try:
            scanned, skipped = _mod._scan_root_transcripts(tmp_path, "*")
        finally:
            os.chmod(locked, 0o644)
        assert (scanned, skipped) == (2, 1)

    def test_slugs_mode_dedupes_symlinked_alias_by_resolved_path(self, tmp_path):
        """The slugs branch (--this-repo's path) must dedupe the same way the
        glob branch does via _iter_scoped_sessions — a symlinked slug aliasing
        another slug's directory must not double the transcript count."""
        real_proj = tmp_path / "-home-user-repo"
        real_proj.mkdir()
        (real_proj / "sess.jsonl").write_text("{}\n")
        alias = tmp_path / "-home-user-repo-alias"
        alias.symlink_to(real_proj)
        scanned, _skipped = _mod._scan_root_transcripts(
            tmp_path, "*", slugs=["-home-user-repo", "-home-user-repo-alias"]
        )
        assert scanned == 1

    def test_glob_mode_dedupes_symlinked_alias_by_resolved_path(self, tmp_path):
        """The glob branch (--projects' default path) must dedupe the same
        way the slugs branch does — review found this branch's own dedup was
        missing, an asymmetry between the two branches of one function
        introduced when the slugs branch was added later for --this-repo."""
        real_proj = tmp_path / "-home-user-repo"
        real_proj.mkdir()
        (real_proj / "sess.jsonl").write_text("{}\n")
        alias = tmp_path / "-home-user-repo-alias"
        alias.symlink_to(real_proj)
        scanned, _skipped = _mod._scan_root_transcripts(tmp_path, "*")
        assert scanned == 1


class TestCostMultiRootRedaction:
    """Deny-case tests for cost's --config-dir redaction surface: no raw
    project label, config-dir path, or account-identifying directory name may
    appear anywhere in default-redacted multi-root stdout."""

    def test_default_redaction_hides_raw_labels_and_root_paths(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "repo-a" not in out
        assert "repo-b" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out
        assert "acct-alice-clientwork" not in out
        assert "acct-bob-clientwork" not in out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out

    def test_corpus_fingerprint_line_present_under_default_redaction(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Corpus fingerprint:" in out

    def test_corpus_fingerprint_deterministic_for_same_label_set(self):
        """Same raw-label set, different key shapes (plain str vs. (root_idx,
        label) tuple) — the fingerprint hashes raw labels only, so the two
        must be equal."""
        flat_map = {"proj-a": "private-project-1", "proj-b": "private-project-2"}
        namespaced_map = {(0, "proj-a"): "account-1/private-project-1", (1, "proj-b"): "account-2/private-project-1"}
        assert _mod._corpus_fingerprint(flat_map) == _mod._corpus_fingerprint(namespaced_map)

    def test_corpus_fingerprint_differs_for_different_label_set(self):
        map_a = {"proj-a": "private-project-1"}
        map_b = {"proj-a": "private-project-1", "proj-b": "private-project-2"}
        assert _mod._corpus_fingerprint(map_a) != _mod._corpus_fingerprint(map_b)

    def test_no_redact_stamps_do_not_publish_banner_on_stdout_and_stderr(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_default_redact_omits_do_not_publish_banner(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.err

    def test_redact_map_miss_raises_instead_of_printing_unmapped_row(self, tmp_path, monkeypatch):
        """A project label absent from the redact map (e.g. built over a
        stale roots list) is a hard error, never a printed 'unmapped' row —
        pins the fail-closed rewrite from _REDACT_MAP_MISS_TOKEN into an
        AssertionError for cost specifically."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod, "_build_redact_map", lambda roots=None: {})
        with pytest.raises(AssertionError, match="redact map has no entry"):
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a])

    def test_redact_map_miss_assertion_omits_raw_label(self, tmp_path, monkeypatch):
        """The redact-map-miss hard-fail's own message must not embed the raw
        project label — main() has no top-level exception handler, so an
        uncaught AssertionError reaches stderr, and a raw label there would
        re-leak exactly what --redact exists to hide (a real gap found by
        review: the original message used {scoped_label!r})."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod, "_build_redact_map", lambda roots=None: {})
        with pytest.raises(AssertionError) as exc_info:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a])
        assert "-home-user-secret-clientname" not in str(exc_info.value)

    def test_root_index_lookup_failure_omits_raw_path(self, tmp_path):
        """_root_index_for_path's own 'no known scan root' AssertionError is a
        structural sibling of the redact-map-miss assertion above — found by
        cumulative-diff review to still embed the raw jsonl path (f"cost:
        {jsonl} matched...") after the redact-map-miss sibling was fixed.
        Reproduces the real trigger: a project dir under a declared
        --config-dir root that is a symlink resolving OUTSIDE every declared
        root (not just aliasing another declared root, which is the already-
        covered, already-deduped case)."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        external = tmp_path / "external-unrelated-secret-clientname"
        external.mkdir()
        _write_jsonl(external / "sess-escaped.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        (root_b / "-home-user-escaped").symlink_to(external)

        with pytest.raises(AssertionError) as exc_info:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        assert "external-unrelated-secret-clientname" not in str(exc_info.value)
        assert "-home-user-escaped" not in str(exc_info.value)

    def test_no_redact_refused_by_cost_report_itself_even_when_called_directly(self, tmp_path):
        """Defense-in-depth: _cost_report is the function that actually prints
        raw labels when redact is False, so it must refuse the multi-root +
        --no-redact combination itself rather than trusting that
        _resolve_cost_roots already validated it — every test in this class
        calls _cost_report directly, bypassing that CLI-level boundary."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2), roots=[root_a, root_b])
        assert exc_info.value.code == 2

    def test_no_redact_refused_at_cmd_cost_when_config_dir_given(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        args = _cost_args(no_redact=True, extra_config_dirs=[str(acct_b)])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_cost(args)
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_this_repo_end_to_end_reaches_report_with_config_dir(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """--config-dir + --this-repo end-to-end through cmd_cost: distinct
        from the _resolve_cost_roots-only allow-case above, since its own
        value is proving no path still reaches the report bypassing the
        (now-removed) guard."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        slug = "-home-user-this-repo"
        proj_default = default_dir / "projects" / slug
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-default.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / slug
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_priced("claude-sonnet-5", input=2_000_000)])  # $4.00

        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: [slug])

        args = _cost_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        _mod.cmd_cost(args)

        out = capsys.readouterr().out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        # occurrence=1: the global section, not one of the new per-account
        # "## Cost by account" sections this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "6.00"


class TestCostByProject:
    """--by-project: per-(account root, project family) aggregation."""

    def test_by_project_flag_composes_with_this_repo_at_argparse_level(self):
        """--by-project is not in --this-repo/--projects' mutually exclusive
        group — both flags parse together instead of argparse rejecting the
        combination."""
        args = _mod.build_parser().parse_args(["cost", "--this-repo", "--by-project"])
        assert args.this_repo is True
        assert args.by_project is True

    def test_by_project_flag_composes_with_projects_at_argparse_level(self):
        args = _mod.build_parser().parse_args(["cost", "--projects", "foo", "--by-project"])
        assert args.projects == "foo"
        assert args.by_project is True

    def test_by_project_composes_with_this_repo_execution(self, tmp_path, monkeypatch, capsys):
        """End-to-end (not just argparse): --by-project + --this-repo runs
        _resolve_project_scope's repo-scoped iterator and still emits a
        per-project section for it."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(this_repo=True, by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith("Project")]
        assert len(data_rows) == 1
        assert float(data_rows[0].split()[-2].replace(",", "")) == pytest.approx(2.00)

    def test_per_project_rows_sum_to_grand_total_across_multi_root_fixture(self, tmp_path, capsys):
        """Three sessions across two --config-dir roots: per-project rows'
        dollars sum to the report's own grand total, hand-computed."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a1",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        _write_jsonl(root_a / "-home-user-repo-a" / "sess-a2.jsonl", [
            _priced("claude-sonnet-5", input=1_500_000),  # $3.00
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b1",
                                   [_priced("claude-sonnet-5", input=2_000_000)])  # $4.00
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(9.00)  # 2.00 + 3.00 + 4.00, hand-computed

        section = out.split("## Cost by project")[1].split("## Top")[0]
        row_dollars = [
            float(ln.split()[-2].replace(",", ""))
            for ln in section.splitlines() if ln.strip().startswith("account-")
        ]
        assert len(row_dollars) == 2  # one row per (root, family)
        assert sum(row_dollars) == pytest.approx(grand_total)

    def test_multi_root_project_column_omits_redundant_account_prefix(self, tmp_path, capsys):
        """The Project column must not repeat the 'account-K/' prefix the
        adjacent Account column already carries — review found the redact
        map's raw namespaced value ('account-1/private-project-1') being
        printed verbatim into a row that already had 'account-1' as its own
        column."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip().startswith("account-")]
        assert len(data_rows) == 2
        for row in data_rows:
            assert "account-1/private-project-1" not in row
            assert "account-2/private-project-1" not in row
            assert "private-project-1" in row

    def test_worktree_suffixed_siblings_collapse_into_one_family_row(self, tmp_path, monkeypatch, capsys):
        """Two of this repo's own linked-worktree project dirs (sharing the
        base-repo-slug-plus---claude-worktrees-<branch> shape iter_sessions
        documents) aggregate into a single --by-project row instead of
        fragmenting one row per branch."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-user-testrepo--claude-worktrees-branch-a"
        proj_b = projects / "-home-user-testrepo--claude-worktrees-branch-b"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(proj_a / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        _write_jsonl(proj_b / "sess-b.jsonl", [_priced("claude-sonnet-5", input=500_000)])    # $1.00

        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out

        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith("Project")]
        assert len(data_rows) == 1  # not one row per worktree
        assert float(data_rows[0].split()[-2].replace(",", "")) == pytest.approx(3.00)

    def test_by_project_empty_corpus_renders_clean_zero_state(self, fake_projects, capsys):
        """No priced turns at all: the per-project section renders the same
        zero-state line as the top-N-sessions section, not a traceback or an
        empty table header with no explanation."""
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        assert "(no priced turns in range)" in section

    def test_by_project_single_root_omits_raw_label_under_default_redaction(self, tmp_path, capsys):
        """General invariant, not just the prefix-specific one covered by
        test_multi_root_project_column_omits_redundant_account_prefix: the raw
        (pre-redaction) project directory name must be absent from
        --by-project output — the single-root branch has no account prefix
        to begin with, so it needs its own assertion of the general rule."""
        root = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname", "sess-a",
                                 [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        assert "-home-user-secret-clientname" not in section
        assert "private-project-1" in section

    def test_by_project_multi_root_omits_raw_label_under_default_redaction(self, tmp_path, capsys):
        """Same general invariant as the single-root test above, for the
        multi-root branch — distinct from the redundant-prefix-specific
        assertion in test_multi_root_project_column_omits_redundant_account_prefix."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-secret-clientname-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        assert "-home-user-secret-clientname-a" not in section
        assert "-home-user-secret-clientname-b" not in section

    def test_by_project_flag_off_omits_section_entirely(self, tmp_path, capsys):
        """--by-project defaults False; the '## Cost by project' header must
        not appear at all when the flag is omitted — pins the `if by_project:`
        guard against a future refactor hoisting the print unconditionally."""
        root = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                 [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        assert "## Cost by project" not in out

    def test_non_worktree_label_colliding_with_suffix_shape_merges_into_existing_family(
        self, tmp_path, monkeypatch, capsys
    ):
        """_project_family matches on the literal '--claude-worktrees-'
        substring, not on genuine worktree provenance. Genuine collision: a
        project whose own (non-worktree) name happens to end in that exact
        substring strips down to the same family as an unrelated project
        that already has that name verbatim — merging two distinct projects'
        spend into one --by-project row. Pins the CURRENT (merging) behavior
        as a documented limitation, not a desired one — see
        _project_family's docstring."""
        projects = tmp_path / "projects"
        unrelated = projects / "-home-user-shared-family"
        coincidental = projects / "-home-user-shared-family--claude-worktrees-fake"
        unrelated.mkdir(parents=True)
        coincidental.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(unrelated / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])    # $2.00
        _write_jsonl(coincidental / "sess-b.jsonl", [_priced("claude-sonnet-5", input=500_000)])   # $1.00

        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith("Project")]
        # Both derive to family "-home-user-shared-family" — one row, $3.00 total,
        # even though they are two genuinely unrelated project directories.
        assert len(data_rows) == 1
        assert float(data_rows[0].split()[-2].replace(",", "")) == pytest.approx(3.00)


class TestCostByAccount:
    """'## Cost by account': per-account token-class/model-ID breakdown,
    auto-shown under multi_root with no new flag (edit-format's own
    precedent), plus the third cross-check assertion guarding it."""

    def test_per_account_totals_sum_to_grand_total_across_multi_root_fixture(self, tmp_path, capsys):
        """Three sessions across two --config-dir roots: per-account
        token-class totals sum to the report's own grand total, hand-computed
        -- exercises the new per-account cross-check assertion's normal pass
        case, mirroring TestCostByProject's own sum-to-grand-total test."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a1",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        _write_jsonl(root_a / "-home-user-repo-a" / "sess-a2.jsonl", [
            _priced("claude-sonnet-5", input=1_500_000),  # $3.00
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b1",
                                   [_priced("claude-sonnet-5", input=2_000_000)])  # $4.00
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(9.00)  # 2.00 + 3.00 + 4.00, hand-computed

        account_totals = _extract_account_totals(out)
        assert len(account_totals) == 2
        assert sum(account_totals.values()) == pytest.approx(grand_total)
        assert account_totals[1] == pytest.approx(5.00)  # account-1 = acct-a: 2.00 + 3.00
        assert account_totals[2] == pytest.approx(4.00)  # account-2 = acct-b

    def test_per_account_specific_values_hand_computed(self, tmp_path, capsys):
        """Per-account class/model $ figures match a hand-computed value tied
        to that account's own fixture data -- not just that the two accounts'
        rows sum to the grand total, which would still pass even if the two
        accounts' dollars were swapped under the wrong ordinal."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00, all "input"
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-opus-5", input=500_000)])      # $2.50, all "input"
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out

        acct1_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=2)
        assert acct1_class["$"] == "2.00"
        acct2_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=3)
        assert acct2_class["$"] == "2.50"

        acct1_model = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-5", occurrence=2)
        assert acct1_model["$"] == "2.00"
        acct2_model = _table_cols(out, header_contains="Model", row_contains="claude-opus-5", occurrence=3)
        assert acct2_model["$"] == "2.50"

    def test_per_account_missing_turn_raises_cross_check_assertion(self, tmp_path, monkeypatch):
        """Fault injection: skip the per-account accumulation for one turn
        while the global accumulators still see it -- proves the new
        per-account cross-check assertion actually fires, not just that it
        passes on correct code (mirrors the untested gap in the two
        pre-existing cross-checks, which also have no such test today)."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])

        original = _mod._accumulate_per_account_turn
        calls = {"n": 0}

        def flaky_accumulate(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return  # drop the first turn's per-account contribution
            return original(*args, **kwargs)

        monkeypatch.setattr(_mod, "_accumulate_per_account_turn", flaky_accumulate)

        with pytest.raises(AssertionError, match="per-account"):
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])

    def test_cost_by_account_section_absent_at_single_root(self, fake_projects, capsys):
        """No --config-dir (single root): the '## Cost by account' section
        must not appear at all -- promoted from the plan's manual
        verification check into an automated regression pin."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "## Cost by account" not in out

    def test_per_account_zero_priced_account_renders_clean_zero_state(self, tmp_path, capsys):
        """One of two --config-dir roots has zero priced turns -- its own
        per-account section renders a clean $0.00/0.0% zero state, not a
        crash or malformed 0/0-share row -- mirrors edit-format's own
        up-front per_account initialization for every ordinal."""
        root_full = _write_cost_root(tmp_path, "acct-full", "-home-user-repo-a", "sess-a",
                                      [_priced("claude-sonnet-5", input=1_000_000)])
        root_zero = tmp_path / "acct-zero"
        (root_zero / "-home-user-repo-b").mkdir(parents=True)  # exists, no *.jsonl
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_full, root_zero])
        out = capsys.readouterr().out

        zero_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=3)
        assert zero_class["$"] == "0.00"
        assert zero_class["Share"] == "0.0%"

    def test_cost_by_account_negative_content_no_raw_project_labels(self, tmp_path, capsys):
        """The full multi-root report's stdout contains no raw project-label
        or config-dir-path substrings from the fixture roots. Also asserts
        the new '## Cost by account' section actually rendered -- a prior
        version of this test asserted only the negative and still passed
        with that entire section removed, since neither of its printers ever
        takes a project-label/path argument to begin with; pinning the
        section's presence ties the absence-of-leak claim to the code path
        it's meant to guard."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-secret-clientname-b", "sess-b",
                                   [_priced("claude-opus-5", input=500_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "## Cost by account" in out
        assert "### account-1" in out
        assert "### account-2" in out
        assert "-home-user-secret-clientname-a" not in out
        assert "-home-user-secret-clientname-b" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_permission_error_while_scanning_root_caught_and_reported_per_account_section(
        self, tmp_path, capsys
    ):
        """A real unreadable root among a multi-root scan must not leak its
        path via the new per-account section's presence/rendering, mirroring
        TestCostMultiRootReport's own chmod-based PermissionError test for
        the pre-existing global tables -- os.access's real permission check,
        not a mock, since pathlib.Path.glob silently swallows OSError while
        walking an unreadable directory rather than propagating it."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        os.chmod(root_a, 0o000)
        try:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        finally:
            os.chmod(root_a, 0o755)  # restore before tmp_path teardown

        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert "cannot scan" in err
        assert str(root_a) not in err
        assert "## Cost by account" in out
        assert "### account-1" in out
        assert "### account-2" in out
        zero_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=2)
        assert zero_class["$"] == "0.00"


class TestCostThreadSplit:
    """Cost by thread: main-thread vs. subagent (isSidechain) dollar split."""

    def test_main_and_subagent_dollars_sum_to_grand_total(self, fake_projects, capsys):
        session_id = "sess-thread"
        subagent_rec = _priced("claude-sonnet-5", input=500_000)  # subagent: $1.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main: $2.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(3.00)  # 2.00 + 1.00, hand-computed

        thread_section = out.split("## Cost by thread")[1].split("## Top")[0]
        main_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("main"))
        subagent_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("subagent"))
        main_dollars = float(main_line.split()[-2].replace(",", ""))
        subagent_dollars = float(subagent_line.split()[-2].replace(",", ""))
        assert main_dollars == pytest.approx(2.00)
        assert subagent_dollars == pytest.approx(1.00)
        assert main_dollars + subagent_dollars == pytest.approx(grand_total)

    def test_subagent_dollars_not_misattributed_to_main_via_path_check(self, fake_projects, capsys):
        """Subagent records are merged into the parent session's record list
        by _read_session_file; iter_sessions yields only the main .jsonl
        path, which never contains 'subagents' in its parts. A
        `"subagents" in jsonl.parts`-style check on that yielded path would
        find no match and misattribute every dollar here to main; the
        isSidechain-based split must carry a nonzero subagent share instead."""
        session_id = "sess-path-check"
        main_jsonl = fake_projects / f"{session_id}.jsonl"
        subagent_rec = _priced("claude-sonnet-5", input=1_000_000)  # subagent: $2.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(main_jsonl, [_priced("claude-sonnet-5", input=1_000_000)])  # main: $2.00
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])
        assert "subagents" not in main_jsonl.parts  # the path a naive check would inspect

        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        thread_section = out.split("## Cost by thread")[1].split("## Top")[0]
        subagent_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("subagent"))
        assert float(subagent_line.split()[-2].replace(",", "")) == pytest.approx(2.00)  # not 0.00

    def test_sidechain_fast_mode_multiplier_reflected_in_subagent_accumulator(self, fake_projects, capsys):
        """A speed:"fast" sidechain turn's 2x multiplier (already pinned at the
        unit level by TestPriceTurnSpeedGeoMultipliers) survives the isSidechain
        main/subagent split and stdout table rendering end-to-end."""
        session_id = "sess-sidechain-fast"
        subagent_rec = _priced("claude-sonnet-4-5", input=1_000_000, speed="fast")  # 2x: $6.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-4-5", input=1_000_000),  # main: $3.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(9.00)  # 3.00 + 6.00

        thread_section = out.split("## Cost by thread")[1].split("## Top")[0]
        main_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("main"))
        subagent_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("subagent"))
        assert float(main_line.split()[-2].replace(",", "")) == pytest.approx(3.00)
        assert float(subagent_line.split()[-2].replace(",", "")) == pytest.approx(6.00)  # not 3.00

    def test_print_thread_table_markdown_branch_renders_exact_gfm_lines(self, capsys):
        """Direct unit coverage of _print_thread_table's markdown branch,
        called with known args rather than relying solely on full-report
        integration coverage to catch a wiring bug (wrong argument order,
        wrong _pct_of denominator)."""
        _mod._print_thread_table(3.00, 1.00, 4.00, markdown=True)
        out = capsys.readouterr().out
        assert out == (
            "\n### Cost by thread\n\n"
            "| Thread | $ | Share |\n"
            "|---|---|---|\n"
            "| main | 3.00 | 75.0% |\n"
            "| subagent | 1.00 | 25.0% |\n"
        )


class TestCostMarkdownTablePrinters:
    """Direct unit coverage of _print_token_class_table's and
    _print_model_id_table's markdown branches, mirroring
    TestCostThreadSplit's _print_thread_table unit test -- pins each
    function's own formatting invariants without paying --summary's full
    _cost_report fixture cost."""

    def test_print_token_class_table_markdown_branch_renders_exact_gfm_lines(self, capsys):
        class_totals = {cls: 0.0 for cls in _mod._TOKEN_CLASSES}
        class_token_totals = {cls: 0 for cls in _mod._TOKEN_CLASSES}
        class_totals["input"] = 3.00
        class_token_totals["input"] = 1_500_000
        _mod._print_token_class_table(class_totals, class_token_totals, 3.00, markdown=True)
        out = capsys.readouterr().out
        assert out == (
            "### Cost by token class\n\n"
            "| Class | $ | Share | Tokens |\n"
            "|---|---|---|---|\n"
            "| cache_read | 0.00 | 0.0% | 0 |\n"
            "| cache_write_5m | 0.00 | 0.0% | 0 |\n"
            "| cache_write_1h | 0.00 | 0.0% | 0 |\n"
            "| output | 0.00 | 0.0% | 0 |\n"
            "| input | 3.00 | 100.0% | 1,500,000 |\n"
            "| **total** | **3.00** | | |\n"
        )

    def test_print_model_id_table_markdown_branch_renders_exact_gfm_lines(self, capsys):
        _mod._print_model_id_table({"claude-sonnet-5": 3.00, "claude-opus-5": 1.00}, 4.00, markdown=True)
        out = capsys.readouterr().out
        assert out == (
            "\n### Cost by model ID\n\n"
            "| Model | $ | Share |\n"
            "|---|---|---|\n"
            "| claude-sonnet-5 | 3.00 | 75.0% |\n"
            "| claude-opus-5 | 1.00 | 25.0% |\n"
        )


class TestPriceTurnArity:
    def test_price_turn_returns_exactly_three_values(self):
        """Pins _price_turn's (dollars_by_class, context_at_turn, unpriced_tokens)
        three-tuple return signature — three other call sites (audit-routing,
        context-distribution, cost-trend) consume it positionally; a future
        edit widening it for one caller would silently break the rest.
        audit-routing-shape and audit-routing-samples classify Opus turn
        shape only and never call _price_turn."""
        usage = _priced("claude-sonnet-5", input=100)["message"]["usage"]
        result = _mod._price_turn("claude-sonnet-5", usage)
        assert len(result) == 3
        dollars, context_at_turn, unpriced_tokens = result
        assert isinstance(dollars, dict)
        assert isinstance(context_at_turn, int)
        assert isinstance(unpriced_tokens, int)

    def test_context_at_turn_extraction_matches_price_turns_own_return_value(self):
        """_context_at_turn was extracted out of _price_turn as a pure
        refactor -- pins that the extracted computation still equals the sum
        _price_turn's own docstring specifies (input + cache_read +
        ephemeral_1h + ephemeral_5m) for a representative usage record."""
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 5},
        }
        _dollars, context_at_turn, _unpriced_tokens = _mod._price_turn("claude-sonnet-5", usage)
        assert context_at_turn == 100 + 50 + 10 + 5


class TestPriceTurnSpeedGeoMultipliers:
    """_price_turn's speed:"fast" (2x) and inference_geo:"us" (1.1x) dollar
    multipliers -- vendor-billed outcome fields, applied regardless of the
    model's own fast-mode/data-residency eligibility (see the regression-
    anchor test below)."""

    def test_speed_fast_multiplies_dollars_by_two(self):
        usage = _priced("claude-sonnet-5", input=1_000_000, speed="fast")["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 2)

    def test_inference_geo_us_multiplies_dollars_by_1_1(self):
        usage = _priced("claude-sonnet-5", input=1_000_000, inference_geo="us")["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 1.1)

    def test_speed_and_inference_geo_stack_multiplicatively(self):
        """Both multipliers present on the same turn compose to 2 * 1.1 = 2.2x,
        not just the larger of the two."""
        usage = _priced(
            "claude-sonnet-5", input=1_000_000, speed="fast", inference_geo="us",
        )["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 2.2)

    def test_multiplier_applies_regardless_of_model_eligibility(self):
        """Regression anchor, not a new runtime check: _price_turn trusts the
        API's own reported speed/inference_geo outcome rather than
        hand-maintaining a per-model eligibility list, so a model that isn't
        actually fast-mode/data-residency-eligible still gets the multiplier
        if the usage record carries the field."""
        usage = _priced(
            "claude-sonnet-4-6", input=1_000_000, speed="fast", inference_geo="us",
        )["message"]["usage"]
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-4-6", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 3.0 * 2.2)


class TestDedupTurnsByRequestId:
    """Direct tests for _dedup_turns_by_request_id, the shared turn iterator
    cmd_audit_routing, _cost_report, _context_distribution_report,
    _cost_trend_report, and cmd_subagents all apply to their own per-session
    records before their per-record loops -- Claude Code writes one JSONL
    record per assistant content block, and every record from one API call
    shares a requestId. input_tokens and the cache_* classes are identical
    across a run's records; output_tokens ascends within the run and
    completes only on the last record."""

    def test_single_record_request_is_a_no_op(self):
        """A lone assistant record (no run to merge) is returned unchanged."""
        rec = _priced("claude-sonnet-5", input=100, request_id="req-1")
        assert _mod._dedup_turns_by_request_id([rec]) == [rec]

    def test_multi_record_run_merges_content_in_order_and_keeps_last_usage(self):
        """Three records sharing one requestId collapse into one turn whose
        content is the concatenation of all three blocks in original order,
        and whose usage is the run's LAST record's usage -- so a caller
        pricing the merged record prices the run's final (billed) usage,
        not an earlier record's."""
        block_a = {"type": "thinking", "thinking": "..."}
        block_b = {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
        block_c = {"type": "text", "text": "done"}
        recs = [
            _asst("claude-sonnet-5", content=[block_a], request_id="req-1"),
            _asst("claude-sonnet-5", content=[block_b], request_id="req-1"),
            _asst("claude-sonnet-5", content=[block_c], request_id="req-1"),
        ]
        recs[0]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[1]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[2]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id(recs)
        assert len(result) == 1
        assert result[0]["message"]["content"] == [block_a, block_b, block_c]
        assert result[0]["message"]["usage"] == {"input_tokens": 100, "output_tokens": 50}

    def test_ascending_output_tokens_within_run_prices_using_last_record(self):
        """The run's output_tokens ascends record-to-record and completes on
        the last one (measured across 15,653 multi-record runs, 100% of
        which peak on the last record) -- a merged turn's usage must reflect
        that final, billed value. Taking the first record's stub value here
        would undercount output tokens, the regression this test guards."""
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        recs[0]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[1]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[2]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3111}
        result = _mod._dedup_turns_by_request_id(recs)
        assert result[0]["message"]["usage"]["output_tokens"] == 3111

    def test_non_identical_input_usage_within_run_emits_stderr_warning(self, monkeypatch, capsys):
        """A future transcript format emitting non-identical input_tokens
        within one requestId run is a silent-mispricing risk with no signal
        today -- this canary fires a stderr WARNING when
        _merge_assistant_run's input/cache-invariant-usage assumption is
        violated, mirroring _warn_if_subagent_format_drift's pattern.
        _usage_drift_warned is reset here since the canary is rate-limited to
        one warning per process (see _warn_if_run_usage_drift) and other
        tests in this module-scoped process may have already tripped it."""
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", False)
        rec1 = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 999, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([rec1, rec2])
        assert "WARNING" in capsys.readouterr().err

    def test_ascending_output_tokens_within_run_emits_no_warning(self, monkeypatch, capsys):
        """The normal case -- output_tokens ascends within a run while
        input_tokens and the cache_* classes stay identical -- never fires
        the drift canary; an ascending output_tokens is the documented norm,
        not drift."""
        monkeypatch.setattr(_mod.pricing, "_usage_drift_warned", False)
        rec1 = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([rec1, rec2])
        assert "WARNING" not in capsys.readouterr().err

    def test_missing_request_id_records_each_count_separately(self):
        """Two assistant records with no requestId key at all never merge
        with each other — each is returned as its own one-record turn."""
        rec1 = _priced("claude-sonnet-5", input=100)
        rec2 = _priced("claude-sonnet-5", input=200)
        assert "requestId" not in rec1
        assert "requestId" not in rec2
        assert _mod._dedup_turns_by_request_id([rec1, rec2]) == [rec1, rec2]

    def test_null_and_empty_request_id_records_each_count_separately(self):
        """A null requestId and an empty-string requestId are both treated as
        'missing' — neither merges with the other or with a truly absent
        requestId, matching the never-merge-two-missing-ids requirement."""
        rec1 = _priced("claude-sonnet-5", input=100)
        rec1["requestId"] = None
        rec2 = _priced("claude-sonnet-5", input=200)
        rec2["requestId"] = ""
        assert _mod._dedup_turns_by_request_id([rec1, rec2]) == [rec1, rec2]

    def test_non_contiguous_run_with_matching_usage_merges_across_user_record(self):
        """A same-requestId assistant run interleaved with a user tool_result
        record -- the harness's own shape for a multi-tool_use response
        dispatched one tool call at a time -- merges into one turn when
        every record's usage matches, with the intervening user record
        passing through unchanged at its own position. Treating the
        intervening user record as ending the run and never revisiting the
        second assistant block would inflate turn counts and dollar totals
        for this shape, the regression this test guards."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert len(result) == 2
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]
        assert result[0]["message"]["usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert result[1] == user

    def test_non_contiguous_run_with_mismatched_output_tokens_does_not_merge(self):
        """Two assistant records share a requestId but diverge on
        output_tokens -- the signature of two genuinely separate API calls
        that happen to collide on requestId (e.g. a hook-denial retry),
        rather than one once-billed non-contiguous run. The corroboration
        bar rejects the merge and all three records stay separate turns in
        original order."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user = _user_msg([_tool_result("t1", "continue")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 999}
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert result == [rec1, user, rec2]

    def test_three_record_non_contiguous_run_merges(self):
        """The merge isn't hardcoded to pairs: a 3-record non-contiguous run
        with matching usage on every record merges into one turn, with both
        intervening user records passing through unchanged."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user1 = _user_msg([_tool_result("t1", "tool result 1")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user2 = _user_msg([_tool_result("t2", "tool result 2")])
        rec3 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec3["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id([rec1, user1, rec2, user2, rec3])
        assert len(result) == 3
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}, {"type": "text", "text": "c"},
        ]
        assert result[1] == user1
        assert result[2] == user2

    def test_three_record_non_contiguous_run_with_one_divergent_member_does_not_merge(self):
        """Pins that _non_contiguous_run_usage_matches compares every member
        against the run's first record, not just adjacent pairs or a
        majority: a 3-record non-contiguous run where two members match and
        the third diverges on one invariant key (cache_read_input_tokens)
        fails the corroboration bar, so all three records stay separate
        turns regardless of the divergent member's position in the group."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        user1 = _user_msg([_tool_result("t1", "tool result 1")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        user2 = _user_msg([_tool_result("t2", "tool result 2")])
        rec3 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec3["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 999, "output_tokens": 50,
        }
        result = _mod._dedup_turns_by_request_id([rec1, user1, rec2, user2, rec3])
        assert result == [rec1, user1, rec2, user2, rec3]

    def test_interleaved_different_request_id_group_leaves_other_run_record_untouched(self):
        """Records ordered [A req-1, B req-2, C req-1], where req-1's usage
        matches across A and C: the two-pass indexer groups by requestId
        across the full input, so B -- a different run's own record sitting
        between A and C -- passes through unchanged at its own position
        while A and C merge at A's original position, pinning that indexing
        by requestId across the full input doesn't disturb an unrelated
        run's record that happens to sit between this run's members."""
        rec_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-2")
        rec_b["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        rec_c = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec_c["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = _mod._dedup_turns_by_request_id([rec_a, rec_b, rec_c])
        assert len(result) == 2
        assert result[0]["requestId"] == "req-1"
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "c"},
        ]
        assert result[1] == rec_b

    def test_non_contiguous_merge_and_rejection_notices_are_independently_rate_limited(
        self, monkeypatch, capsys
    ):
        """Both non-contiguous merge and rejection decisions log one stderr
        NOTICE per process, independently rate-limited by decision kind
        (_non_contiguous_merge_notices_logged) -- a merge NOTICE firing must
        not suppress a later rejection NOTICE, and each message names its
        own decision kind so the two are distinguishable by grep, not just a
        generic 'NOTICE' substring. _non_contiguous_merge_notices_logged is
        reset here since it's rate-limited across the whole process and
        other tests in this module-scoped run may have already tripped
        either kind (mirrors
        test_non_identical_input_usage_within_run_emits_stderr_warning's
        reset of _usage_drift_warned)."""
        monkeypatch.setattr(_mod.pricing, "_non_contiguous_merge_notices_logged", set())

        merge_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        merge_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        merge_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        merge_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([merge_a, _user_msg([_tool_result("t1", "tool result")]), merge_b])
        merge_stderr = capsys.readouterr().err
        assert "NOTICE" in merge_stderr
        assert "merged" in merge_stderr
        assert "rejected" not in merge_stderr

        reject_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "x"}], request_id="req-2")
        reject_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        reject_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "y"}], request_id="req-2")
        reject_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 999}
        _mod._dedup_turns_by_request_id([reject_a, _user_msg([_tool_result("t2", "tool result")]), reject_b])
        reject_stderr = capsys.readouterr().err
        assert "NOTICE" in reject_stderr
        assert "rejected" in reject_stderr

    def test_non_contiguous_merge_notice_suppressed_on_repeat_same_kind(self, monkeypatch, capsys):
        """A second non-contiguous merge (different requestId, same 'merged'
        decision kind) after the first must not print a second NOTICE --
        pinning the rate-limit half of 'independently rate-limited', not
        just the cross-kind independence the prior test covers."""
        monkeypatch.setattr(_mod.pricing, "_non_contiguous_merge_notices_logged", set())

        first_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        first_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        first_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        first_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([first_a, _user_msg([_tool_result("t1", "tool result")]), first_b])
        capsys.readouterr()  # drain the first NOTICE

        second_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "x"}], request_id="req-2")
        second_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        second_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "y"}], request_id="req-2")
        second_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        _mod._dedup_turns_by_request_id([second_a, _user_msg([_tool_result("t2", "tool result")]), second_b])
        assert capsys.readouterr().err == ""

    def test_non_contiguous_run_with_missing_usage_does_not_merge(self):
        """Two same-requestId assistant records with no usage dict at all
        have zero evidence to corroborate on -- treating that absence as
        agreement (None == None on every key) would merge two records with
        no actual usage match, the failure mode this test guards against."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        del rec1["message"]["usage"]
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        del rec2["message"]["usage"]
        result = _mod._dedup_turns_by_request_id([rec1, user, rec2])
        assert result == [rec1, user, rec2]

    def test_request_id_does_not_merge_across_session_files(self, fake_projects, capsys):
        """The same requestId string appearing in two separate session files
        prices as two separate turns, not one merged turn — the dedup helper
        is applied fresh to each session's own records list, so a requestId
        collision across sessions (however unlikely with real UUIDs) can't
        collapse them."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, request_id="shared-req"),  # $2.00
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, request_id="shared-req"),  # $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(4.00)

    def test_shared_request_id_merges_across_main_and_sidechain_records(self):
        """A requestId shared by a main-thread record and a sidechain record
        merges into one turn — the merge condition checks requestId equality
        alone, not isSidechain, relying on requestId's global uniqueness (see
        this function's own docstring). include_subagents=True concatenates a
        session's main file with its subagent files into one records list
        before dedup runs, so this boundary is reachable in that concatenated
        list even though it never occurs within a single raw JSONL file."""
        main_rec = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}],
                          request_id="req-1", sidechain=False)
        side_rec = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}],
                          request_id="req-1", sidechain=True)
        result = _mod._dedup_turns_by_request_id([main_rec, side_rec])
        assert len(result) == 1
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]


class TestCostBranchFilter:
    """--branches: per-record (not per-session) gitBranch filtering."""

    def test_branch_filter_is_per_record_not_per_session(self, fake_projects, capsys):
        """One session's records split across two branches: --branches A
        returns only A's dollars, and --branches A + --branches B sum to the
        unfiltered total — a session-level (not per-record) filter would
        misprice this in both directions (row2: one real session in this
        repo's own corpus splits 863 records on a feature branch, 212 on
        main)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),  # $2.00
            _priced("claude-sonnet-5", input=500_000, branch="main"),         # $1.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        unfiltered_total = _extract_grand_total(capsys.readouterr().out)

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        feature_total = _extract_grand_total(capsys.readouterr().out)
        assert feature_total == pytest.approx(2.00)

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        main_total = _extract_grand_total(capsys.readouterr().out)
        assert main_total == pytest.approx(1.00)

        assert feature_total + main_total == pytest.approx(unfiltered_total)

    def test_branch_filter_accepts_comma_separated_list(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),  # $2.00
            _priced("claude-sonnet-5", input=500_000, branch="feature-b"),    # $1.00
            _priced("claude-sonnet-5", input=250_000, branch="main"),         # $0.50
        ])
        _mod._cost_report(_cost_args(branches="feature-a,feature-b"), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(3.00)

    def test_null_git_branch_record_counted_unfiltered_excluded_under_branch_filter(
        self, fake_projects, capsys
    ):
        """A null-gitBranch record is counted in an unfiltered run but
        excluded (deliberately) under any --branches filter — pins that the
        branch-filter sum invariant above isn't silently passing only because
        the fixture happens to have no null-branch record."""
        no_branch_rec = _priced("claude-sonnet-5", input=1_000_000)  # $2.00
        no_branch_rec["gitBranch"] = None
        _write_jsonl(fake_projects / "sess.jsonl", [
            no_branch_rec,
            _priced("claude-sonnet-5", input=500_000, branch="main"),  # $1.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(3.00)

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(1.00)


class TestCostTokensColumn:
    """The 'Cost by token class' table's Tokens column, from _token_counts."""

    def test_tokens_column_hand_computed_totals(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced(
                "claude-sonnet-5",
                input=100_000, cache_read=200_000, ephemeral_1h=10_000, ephemeral_5m=20_000, output=5_000,
            ),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        input_cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert int(input_cols["Tokens"].replace(",", "")) == 100_000
        cache_read_cols = _table_cols(out, header_contains="Class", row_contains="cache_read", row_startswith=True)
        assert int(cache_read_cols["Tokens"].replace(",", "")) == 200_000
        output_cols = _table_cols(out, header_contains="Class", row_contains="output", row_startswith=True)
        assert int(output_cols["Tokens"].replace(",", "")) == 5_000

    def test_tokens_column_excludes_unpriced_turns(self, fake_projects, capsys):
        """An unpriced model's tokens are excluded from the Tokens column —
        the same `dollars_by_class is None: continue` guard _price_turn's
        dollar accumulation already applies, so an unpriced turn's tokens are
        surfaced only via the separate unpriced-tokens counter, never folded
        into a total that looks complete."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000),   # unpriced
            _priced("claude-sonnet-5", input=100_000),  # priced
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        input_cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert int(input_cols["Tokens"].replace(",", "")) == 100_000  # not 1,100,000


def _two_declared_roots_with_this_repo_sessions(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Active profile (config_dir() via CLAUDE_CONFIG_DIR) plus one declared
    root (via TRANSCRIPT_CONFIG_DIRS_FILE), each holding a matching --this-repo
    project dir ("-repo-main") with a nonzero-cost transcript on branch
    "main" -- the minimal fixture mechanism 1's --summary narrowing test and
    its non-summary union counterpart share. Mocks `git worktree list` /
    `git rev-parse --show-toplevel` the same way every other --this-repo
    TestCostSummary fixture does."""
    active = tmp_path / "acct-active"
    other = tmp_path / "acct-other"
    (active / "projects").mkdir(parents=True)
    (other / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active))
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{other}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

    slug = "-repo-main"
    proj_active = active / "projects" / slug
    proj_active.mkdir(parents=True)
    _write_jsonl(proj_active / "sess-active.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
    proj_other = other / "projects" / slug
    proj_other.mkdir(parents=True)
    _write_jsonl(proj_other / "sess-other.jsonl", [_priced("claude-sonnet-5", input=5_000_000)])  # $10.00

    monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

    def fake_run(cmd, *a, **k):
        if cmd[:3] == ["git", "worktree", "list"]:
            porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/main\n"
            return subprocess.CompletedProcess(cmd, 0, porcelain, "")
        assert cmd == ["git", "rev-parse", "--show-toplevel"]
        return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    return active / "projects", other / "projects"


class TestCostSummary:
    """--summary: a structurally scoped, aggregate-only rendering branch."""

    def test_summary_without_this_repo_exits_nonzero(self, fake_projects, capsys):
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True), date(2026, 8, 2))
        assert "--summary requires --this-repo" in capsys.readouterr().err

    def test_summary_with_explicit_default_projects_glob_exits_nonzero(self, fake_projects, capsys):
        """Even the literal default glob '*' is refused alongside --summary
        when --this-repo is absent — --summary does not accept --projects as
        an alternative scope gate at all, default value or otherwise."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, projects="*"), date(2026, 8, 2))

    def test_summary_with_machine_wide_projects_bypass_glob_exits_nonzero(self, fake_projects, capsys):
        """row23: every _path_to_project_slug-derived project slug begins
        with '-', so a glob like '-*' is machine-wide despite not being the
        literal default '*' — pins that this bypass is refused too, not just
        the literal default value."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, projects="-*"), date(2026, 8, 2))

    def test_summary_refuses_by_project_in_combination(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, this_repo=True, by_project=True), date(2026, 8, 2))

    def test_summary_refuses_no_redact_in_combination(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, this_repo=True, no_redact=True), date(2026, 8, 2))

    def test_summary_refuses_config_dir_in_combination(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._cost_report(
                _cost_args(summary=True, this_repo=True, extra_config_dirs=["/somewhere"]),
                date(2026, 8, 2),
            )

    def test_summary_emits_nothing_identifying_and_excludes_cross_repo_spend(
        self, tmp_path, monkeypatch, capsys
    ):
        """Items 7 and 7a: a ≥2-project fixture (neither labelled
        claude-config, so the redact-map-miss path is exercisable if this
        path wrongly touched it). This repo's own raw label, the out-of-scope
        project's raw label, and every session ID are absent from --summary
        output; _redact_proj_label/_build_redact_map are never called on this
        path; and the out-of-scope project's dollars are excluded from the
        printed total, not just from the label set."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        other = projects / "-home-user-otherrepo"
        other.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess-mine.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])    # $2.00
        _write_jsonl(other / "sess-other.jsonl", [_priced("claude-sonnet-5", input=5_000_000)])  # $10.00
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        def _must_not_be_called(*a, **k):
            raise AssertionError("must not be called under --summary")
        monkeypatch.setattr(_mod, "_redact_proj_label", _must_not_be_called)
        monkeypatch.setattr(_mod, "_build_redact_map", _must_not_be_called)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out

        assert "repo-main" not in out
        assert "otherrepo" not in out
        assert "sess-mine" not in out
        assert "sess-other" not in out
        assert "private-project" not in out
        assert _extract_md_grand_total(out) == pytest.approx(2.00)  # not 12.00 — other project's $10 excluded

    def test_summary_never_prints_session_or_project_sections(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "## Top" not in out
        assert "## Cost by project" not in out
        assert "## Cost by context-at-turn bucket" not in out
        assert "## Cost by account" not in out
        assert "1 priced sessions" in out
        assert "1 priced turns" in out

    def test_summary_unpriced_model_line_and_tokens_column_exclusion(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000, output=500_000),  # unpriced
            _priced("claude-sonnet-5", input=100_000),                 # priced
        ])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        unpriced_tokens, unpriced_models = _extract_summary_unpriced(out)
        assert unpriced_tokens == 1_000_000 + 500_000
        assert unpriced_models == 1
        input_cols = _md_table_cols(out, header_contains="Class", row_contains="input")
        assert int(input_cols["Tokens"].replace(",", "")) == 100_000  # unpriced turn excluded

    def test_summary_unpriced_line_present_even_when_zero(self, tmp_path, monkeypatch, capsys):
        """Always prints the unpriced-tokens line, even at zero — an
        unrecognized model ID must never silently understate a published
        figure with no marker."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=100_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        unpriced_tokens, unpriced_models = _extract_summary_unpriced(out)
        assert unpriced_tokens == 0
        assert unpriced_models == 0

    def test_summary_stale_pricing_banner_present_past_expiry(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 11, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" in out

    def test_summary_stale_pricing_banner_absent_before_expiry(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_summary_totals_equal_active_root_only_when_second_account_declared(
        self, tmp_path, monkeypatch, capsys
    ):
        """Mechanism 1's core guarantee, driven through cmd_cost (not
        _cost_report directly) so _resolve_cost_roots' own narrowing is
        actually exercised: with two declared roots and a matching,
        nonzero-cost --this-repo project dir under each, cost --this-repo
        --branches main --summary's total equals the active root's total
        exactly -- the other declared account's $10.00 is excluded, not just
        unlabeled -- and the per-root scan line appears exactly once."""
        _two_declared_roots_with_this_repo_sessions(tmp_path, monkeypatch)
        _mod.cmd_cost(_cost_args(summary=True, this_repo=True, branches="main"))
        out = capsys.readouterr().out
        assert _extract_md_grand_total(out) == pytest.approx(2.00)  # not 12.00 -- other account excluded
        assert out.count("cost: account-1: scanned") == 1

    def test_without_summary_the_same_fixture_still_unions_both_accounts(
        self, tmp_path, monkeypatch, capsys
    ):
        """Allow-path counterpart: the identical two-declared-root fixture,
        the same command minus --summary, still unions both accounts' totals
        -- proves mechanism 1's narrowing is --summary-specific, not a
        blanket regression on --this-repo plus a declared root."""
        _two_declared_roots_with_this_repo_sessions(tmp_path, monkeypatch)
        _mod.cmd_cost(_cost_args(this_repo=True, branches="main"))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(12.00)

    def test_summary_scope_line_states_single_account_and_that_dropping_flag_widens_it(
        self, tmp_path, monkeypatch, capsys
    ):
        """1c: the Scope: line must make it legible to a reader that dropping
        --summary from the printed command returns a different, larger
        total, not just state a transcript count."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert (
            "Scope: this account only (1 transcripts scanned, 1 priced sessions, 1 priced turns)"
            " — dropping --summary reports every declared account too"
        ) in out

    def test_direct_cost_report_call_refuses_more_than_one_root_under_summary(
        self, tmp_path, capsys
    ):
        """Defense-in-depth (1b): _cost_report itself refuses summary_mode
        with more than one resolved root, since every direct caller
        (including this module's own tests) bypasses _resolve_cost_roots'
        CLI-level narrowing. The fixture clears both of _cost_report's
        earlier summary-mode exit-2 paths (this_repo=True, projects left at
        the accepted default "*", no by_project/no_redact/extra_config_dirs)
        so the new guard is the one that actually fires; asserting only the
        exit code would pass even with the new guard unimplemented, since
        either earlier refusal also exits 2 -- the guard's own distinct
        message is what proves it fired."""
        root_a = tmp_path / "acct-a" / "projects"
        root_a.mkdir(parents=True)
        root_b = tmp_path / "acct-b" / "projects"
        root_b.mkdir(parents=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_report(
                _cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[root_a, root_b],
            )
        assert exc_info.value.code == 2
        assert "refusing to report a multi-account total" in capsys.readouterr().err

    def test_summary_stdout_with_priced_data_never_leaks_a_home_rooted_path(
        self, tmp_path, monkeypatch, capsys
    ):
        """Shape-level redaction guard over the FULL --summary stdout: no
        /Users/, /home/, or the tmp_path fixture's own substring may appear
        -- a denylist of specific known strings wouldn't catch a new line
        added to this path later."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert "/Users/" not in out
        assert "/home/" not in out
        assert str(tmp_path) not in out

    def test_summary_stdout_never_leaks_a_home_rooted_path_in_the_zero_transcripts_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """Same shape-level guard, exercising the WARNING: cost: account-N:
        no transcripts found ... line specifically -- it also reaches
        stdout (not stderr) under --summary and must pass the same guard,
        so this repo's own slug dir is left with zero transcripts."""
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: no transcripts found" in out
        assert "/Users/" not in out
        assert "/home/" not in out
        assert str(tmp_path) not in out

    def test_summary_model_id_markdown_table_well_formed_with_zero_transcripts(
        self, tmp_path, monkeypatch, capsys
    ):
        """A zero-transcript --summary run still renders a well-formed GFM
        model-ID table -- header and separator rows present even though no
        model ever priced a turn to produce a data row."""
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        lines = out.splitlines()
        header_idx = next(i for i, ln in enumerate(lines) if ln == "| Model | $ | Share |")
        assert lines[header_idx + 1] == "|---|---|---|"
        assert lines[header_idx + 2].strip() == ""  # no data rows -- blank line ends the table

    def test_summary_token_class_total_row_renders_exact_bolded_gfm_shape(
        self, tmp_path, monkeypatch, capsys
    ):
        """The token-class table's closing row renders as the exact bolded
        GFM shape '| **total** | **X.XX** | | |' -- not just a numerically
        correct total, since a formatting regression (missing bold markers,
        wrong column count) would still pass a value-only pytest.approx
        check on the parsed number."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        # $2.00 -- claude-sonnet-5's $2/MTok input rate on 1M input tokens, hand-computed.
        assert "| **total** | **2.00** | | |" in out
        assert _extract_md_grand_total(out) == pytest.approx(2.00)

    def test_summary_title_line_is_bare_prose_not_a_markdown_heading(
        self, tmp_path, monkeypatch, capsys
    ):
        """The --summary title line renders as bare prose ('Cost summary
        (...)'), not a '## '-prefixed heading -- it sits inside the
        pr-description skill's own '## Cost' heading, so a '##' here would
        collide with that wrapper. A future edit re-adding '## ' would
        silently reintroduce that collision without this pin."""
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert "\nCost summary (all time)\n" in out
        assert "## Cost summary" not in out


class TestCostWorktreeAgentBranchCarryForward:
    """A worktree-agent-* subagent record's branch is resolved by carry-
    forward against its own session's main-thread branch history, not
    excluded and not taken literally — see _session_branch_index and
    _attributed_branch."""

    def test_worktree_agent_record_folds_into_branch_active_at_dispatch_time(self, fake_projects, capsys):
        """(a): a main-thread record on the requested branch, followed (later
        timestamp) by a worktree-agent-* record — the subagent's dollars fold
        into the requested branch's headline total, not a separate line."""
        session_id = "sess-carry-a"
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T11:00:00.000Z",
        )  # $1.00, later than main_rec
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(3.00)  # 2.00 + 1.00 folded in

    def test_worktree_agent_record_before_any_main_thread_activity_falls_forward(self, fake_projects, capsys):
        """(b): the worktree-agent-* record's timestamp is *earlier* than any
        main-thread record in the session — resolves by falling forward to
        the index's earliest entry, same result as (a)."""
        session_id = "sess-carry-b"
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T09:00:00.000Z",
        )  # earlier than the only main-thread record
        agent_rec["isSidechain"] = True
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(3.00)

    def test_worktree_agent_record_resolves_through_mid_session_branch_switch(self, fake_projects, capsys):
        """(c) the discriminating case: main-thread records switch branches
        mid-session (feature-a, then later main), and the worktree-agent-*
        record's real timestamp falls *before* the switch — must resolve to
        the pre-switch (feature-a) branch. _read_session_file appends every
        subagent record after every main-thread record, so this record sits
        *after* both main-thread records in the merged list despite its
        earlier timestamp; a position- or last-branch-seen-based resolution
        would misresolve it to main instead of feature-a."""
        session_id = "sess-carry-c"
        first_main = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        second_main = _priced(
            "claude-sonnet-5", input=1_000_000, branch="main", ts="2026-08-01T12:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T11:00:00.000Z",
        )  # $1.00, between the two main-thread turns
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [first_main, second_main])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        feature_out = capsys.readouterr().out
        assert _extract_grand_total(feature_out) == pytest.approx(3.00)  # first_main + agent

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        main_out = capsys.readouterr().out
        assert _extract_grand_total(main_out) == pytest.approx(2.00)  # second_main only

    def test_worktree_agent_record_unresolvable_with_no_main_thread_branch_in_session(
        self, fake_projects, capsys
    ):
        """(d): a session with no main-thread branch-bearing record at all —
        the worktree-agent-* record is counted in an unfiltered run but
        excluded from every --branches filter's total, the one case that
        stays genuinely unattributable (renders '?', GH-482's sentinel
        convention reused). The main-thread record present here carries no
        gitBranch of its own (branch="") — a wholly empty main file would
        instead exercise a pre-existing, unrelated desync between
        _build_redact_map's own (non-subagent-merged) scan basis and cost's
        subagent-merged session iterator, not the carry-forward behavior
        this test targets."""
        session_id = "sess-carry-d"
        agent_rec = _priced("claude-sonnet-5", input=1_000_000, branch="worktree-agent-abc123")  # $2.00
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_user_msg("hi", branch="")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(2.00)

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# context-distribution
# ---------------------------------------------------------------------------


class TestContextDistribution:
    def test_threshold_crossing_and_dollar_share_hand_computed(self, fake_projects, capsys):
        """Three sessions with distinct peak context-at-turn percentages of
        claude-sonnet-5's 1M default window (65%, 45%, 10%) cross a different
        subset of the 30/40/50/60% thresholds — crossing-count and
        dollar-share are verified against hand-computed expected values at
        every threshold, not just one."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=650_000),  # peak 65%, $1.30
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=450_000),  # peak 45%, $0.90
        ])
        _write_jsonl(fake_projects / "sess-c.jsonl", [
            _priced("claude-sonnet-5", input=100_000),  # peak 10%, $0.20
        ])
        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out

        assert "Sessions in scope: 3" in out
        assert "Total priced dollars: 2.40" in out

        # 30%/40%: sessions A and B cross (65% and 45% both >= threshold);
        # dollars = 1.30 + 0.90 = 2.20; share = 2.20 / 2.40 = 91.7%.
        for pct in (30, 40):
            row = _extract_context_distribution_row(out, pct)
            assert row["sessions"] == "2"
            assert row["dollars"] == "2.20"
            assert row["dollar_share"] == "91.7%"

        # 50%/60%: only session A crosses (65% >= threshold, 45% does not);
        # dollars = 1.30; share = 1.30 / 2.40 = 54.2%.
        for pct in (50, 60):
            row = _extract_context_distribution_row(out, pct)
            assert row["sessions"] == "1"
            assert row["dollars"] == "1.30"
            assert row["dollar_share"] == "54.2%"

    def test_peak_exactly_at_threshold_counts_as_crossing(self, fake_projects, capsys):
        """A session whose peak lands exactly on a candidate threshold (not
        strictly above it) still counts as crossing — the comparison is
        >=, mirroring the nudge hook's own 'fires at exactly threshold'
        test for its single threshold. Regression guard for a future
        float-rounding change silently flipping >= to >."""
        _write_jsonl(fake_projects / "sess-exact.jsonl", [
            _priced("claude-sonnet-5", input=400_000),  # peak exactly 40% of 1M
        ])
        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        row_40 = _extract_context_distribution_row(out, 40)
        assert row_40["sessions"] == "1"

    def test_sidechain_turns_excluded_from_peak_context_tracking(self, fake_projects, capsys):
        """A session's peak is computed from main-thread turns only — a deep
        sidechain (subagent) turn must not count toward the session's own
        handoff-relevant peak, since subagent context isn't shared with the
        parent and doesn't accumulate toward a /handoff decision."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=100_000),  # main: peak 10%
        ])
        subagent_rec = _priced("claude-sonnet-5", input=900_000)  # sidechain: would-be peak 90%
        subagent_rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])

        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        # Session's own peak stays 10% (main-thread only) — crosses no threshold.
        row_30 = _extract_context_distribution_row(out, 30)
        assert row_30["sessions"] == "0"
        # But dollars still sum main + sidechain: $0.20 (main, 100k input) +
        # $1.80 (sidechain, 900k input) = $2.00 — a bug that coupled the
        # sidechain exclusion to the dollar total too (instead of only the
        # peak) would silently drop the sidechain's $1.80 here.
        assert "Total priced dollars: 2.00" in out

    def test_multi_record_request_id_group_priced_exactly_once(self, fake_projects, capsys):
        """Three assistant records sharing one requestId (one JSONL record per
        content block, as Claude Code writes for a single API call) carry a
        byte-identical usage dict — context-distribution prices the group's
        usage once, not once per record."""
        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        for rec in recs:
            rec["message"]["usage"] = dict(usage)
        _write_jsonl(fake_projects / "sess.jsonl", recs)
        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        # $2.00 (claude-sonnet-5's $2/MTok input rate on 1M input tokens) once,
        # not $6.00 for pricing the group's usage three times over.
        assert "Total priced dollars: 2.00" in out

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """--no-redact is refused when --config-dir puts more than one root in
        scope, mirroring cost's own refusal exactly."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_context_distribution(
                _context_distribution_args(no_redact=True, extra_config_dirs=[str(acct_b)])
            )
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root(self, fake_projects, capsys):
        """--no-redact with no --config-dir (single root) is unaffected — it
        prints the DO NOT PUBLISH banner but does not exit, the allow-path
        counterpart to the multi-root refusal above."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=100_000)])
        _mod._context_distribution_report(_context_distribution_args(no_redact=True))
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in out
        assert "Sessions in scope: 1" in out

    def test_no_sessions_in_scope_prints_zero_without_division_error(self, fake_projects, capsys):
        """An empty scope (no priced or main-thread turns anywhere) prints a
        zero-sessions summary and 0.0% shares rather than raising
        ZeroDivisionError — in both the percentage table and its absolute-token
        sibling, since both share the same _pct_of-guarded arithmetic."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        assert "Sessions in scope: 0" in out
        row_30 = _extract_context_distribution_row(out, 30)
        assert row_30["sessions"] == "0"
        assert row_30["dollar_share"] == "0.0%"
        row_abs = _extract_context_distribution_abs_row(out, 80_000)
        assert row_abs["sessions"] == "0"
        assert row_abs["dollar_share"] == "0.0%"

    def test_unpriced_model_still_contributes_to_absolute_bucket(self, fake_projects, capsys):
        """A model ID absent from _MODEL_BASE_INPUT_RATES prices at $0, but its
        turns still feed the absolute-token peak — that path computes from
        context_at_turn + output_tokens directly, and its value doesn't depend
        on _context_window_for_model's result, unlike the percentage path."""
        _write_jsonl(fake_projects / "sess-unpriced.jsonl", [
            _priced("claude-opus-4-7", input=250_000),  # unpriced model; crosses the 250,000 bucket
        ])
        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        row = _extract_context_distribution_abs_row(out, 250_000)
        assert row["sessions"] == "1"
        assert row["dollars"] == "0.00"

    def test_mixed_window_session_argmax_differs_between_pct_and_abs_peak(self, fake_projects, capsys):
        """A session with one turn on a 200k-window model (high percentage,
        lower absolute tokens) and one turn on a 1M-window model (lower
        percentage, higher absolute tokens) — the percentage table's peak and
        the absolute table's peak come from different turns, pinning that
        peak_abs_tokens is its own tracked maximum, not peak_pct * window."""
        _write_jsonl(fake_projects / "sess-mixed.jsonl", [
            _priced("claude-sonnet-4-5", input=190_000),  # 200k model: 95% of window, abs 190,000
            _priced("claude-sonnet-5", input=300_000),  # 1M model: 30% of window, abs 300,000
        ])
        _mod._context_distribution_report(_context_distribution_args())
        out = capsys.readouterr().out
        row_60 = _extract_context_distribution_row(out, 60)
        assert row_60["sessions"] == "1"  # peak_pct 95%, from the 200k turn
        row_250k = _extract_context_distribution_abs_row(out, 250_000)
        assert row_250k["sessions"] == "1"  # peak_abs_tokens 300,000, from the 1M turn
        row_400k = _extract_context_distribution_abs_row(out, 400_000)
        assert row_400k["sessions"] == "0"  # peak_abs_tokens is 300,000, not higher

    def test_absolute_peak_accumulator_flat_and_nested_cache_creation_agree(self):
        """One turn's cache_creation expressed via the hook's flat
        cache_creation_input_tokens field and an equal-total turn expressed via
        the nested ephemeral_1h/ephemeral_5m split feed the new absolute-peak
        accumulator (_session_peak_context) to the identical total — pinning
        the _cache_write_split equivalence the accumulator leans on, rather
        than only citing it."""
        flat_usage = _priced(
            "claude-sonnet-5", input=100_000, cache_read=50_000, output=5_000, flat_cache_creation=30_000
        )["message"]["usage"]
        nested_usage = _priced(
            "claude-sonnet-5", input=100_000, cache_read=50_000, output=5_000,
            ephemeral_1h=10_000, ephemeral_5m=20_000,
        )["message"]["usage"]

        _dollars_flat, context_flat, _unpriced_flat = _mod._price_turn("claude-sonnet-5", flat_usage)
        _dollars_nested, context_nested, _unpriced_nested = _mod._price_turn("claude-sonnet-5", nested_usage)

        _peak_pct_flat, peak_abs_flat = _mod._session_peak_context([(context_flat, 5_000, 1_000_000)])
        _peak_pct_nested, peak_abs_nested = _mod._session_peak_context([(context_nested, 5_000, 1_000_000)])

        expected = 100_000 + 50_000 + 30_000 + 5_000
        assert peak_abs_flat == peak_abs_nested == expected

    def test_session_peak_context_tracks_independent_maxima(self):
        """_session_peak_context's two maxima are independent — the turn with
        the highest percentage of its own window need not be the turn with the
        highest absolute token count."""
        turns = [
            (190_000, 0, 200_000),  # pct 95%, abs 190,000
            (300_000, 0, 1_000_000),  # pct 30%, abs 300,000
        ]
        peak_pct, peak_abs_tokens = _mod._session_peak_context(turns)
        assert peak_pct == pytest.approx(0.95)
        assert peak_abs_tokens == 300_000

    def test_session_peak_context_abs_includes_output_tokens(self):
        """peak_abs_tokens is context_at_turn + output_tokens — the hook's own
        four-field ESTIMATE sum — not context_at_turn alone; peak_pct is
        unaffected by output_tokens, matching context_at_turn's own definition."""
        turns = [(100_000, 50_000, 1_000_000)]
        peak_pct, peak_abs_tokens = _mod._session_peak_context(turns)
        assert peak_pct == pytest.approx(0.1)
        assert peak_abs_tokens == 150_000

    def test_session_peak_context_no_main_thread_turns_returns_zero(self):
        """No main-thread turns (e.g. a session with only sidechain activity)
        yields (0.0, 0) rather than raising."""
        assert _mod._session_peak_context([]) == (0.0, 0)

    def test_context_distribution_rows_session_share_hand_computed(self):
        """_context_distribution_rows' session-share arithmetic, exercised
        directly on a synthetic multi-session fixture with a known expected
        fraction — a handoff-nudge cap chosen off this report's session-share
        column is only as trustworthy as this arithmetic."""
        peaks = [0.9, 0.5, 0.3, 0.1, 0.05]
        dollars = [1.0, 1.0, 1.0, 1.0, 1.0]
        rows = _mod._context_distribution_rows([0.4], peaks, dollars)
        # 2 of 5 sessions (0.9, 0.5) cross 0.4 -> 40.0% session-share.
        assert rows[0]["sessions"] == 2
        assert rows[0]["session_share"] == "40.0%"
        assert rows[0]["dollars"] == pytest.approx(2.0)
        assert rows[0]["dollar_share"] == "40.0%"


# ---------------------------------------------------------------------------
# edit-format
# ---------------------------------------------------------------------------


def _edit_format_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


def _edit_tool_use(tool_id: str, *, file_path: str = "/foo.py", old_string: str = "", new_string: str = "") -> dict:
    return {
        "type": "tool_use", "id": tool_id, "name": "Edit",
        "input": {"file_path": file_path, "old_string": old_string, "new_string": new_string},
    }


def _write_tool_use(tool_id: str, *, file_path: str = "/foo.py", content: str = "") -> dict:
    return {
        "type": "tool_use", "id": tool_id, "name": "Write",
        "input": {"file_path": file_path, "content": content},
    }


def _multi_edit_tool_use(tool_id: str, *, file_path: str = "/foo.py") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "MultiEdit", "input": {"file_path": file_path}}


def _error_result(tool_id: str, text: str) -> dict:
    """A user-record is_error tool_result — reuses _hook_deny_current's exact
    record shape, generalized here beyond governance-hook denial text since
    the shape itself (not the wording) is what edit-format's own error
    classification depends on."""
    return _hook_deny_current(text, tool_id=tool_id)


def _extract_call_census_count(out: str, tool: str) -> int | None:
    """Read one edit-family tool's top-of-report call count (unindented,
    unlike every detail-table row below it, which lets this anchor on `^`
    without also matching that tool's own indented failure rows)."""
    match = re.search(rf"^{re.escape(tool)}\s+([\d,]+)", out, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


def _extract_known_failure_count(out: str, tool: str, label: str) -> int | None:
    # count= is right-aligned to a fixed width, so its value is padded with
    # leading spaces, not glued to the "=".
    match = re.search(rf"^\s*{re.escape(tool)}\s+{re.escape(label)}\s+count=\s*([\d,]+)", out, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


def _extract_governance_count(out: str, label: str) -> int | None:
    match = re.search(rf"^\s*{re.escape(label)}\s+count=\s*([\d,]+)", out, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


def _extract_cause_count(out: str, cause: str) -> int | None:
    match = re.search(rf"^\s*{re.escape(cause)}\s+count=\s*([\d,]+)", out, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


def _extract_unclassified_count(out: str) -> int:
    match = re.search(r"unclassified \(edit-family errors matching neither list above\): ([\d,]+)", out)
    assert match is not None, "unclassified summary line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_unpaired_count(out: str) -> int:
    match = re.search(r"unpaired \(is_error tool_result with no matching tool_use in this session\): ([\d,]+)", out)
    assert match is not None, "unpaired summary line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_account_edit_calls(out: str, account_label: str) -> int | None:
    match = re.search(rf"^\s*{re.escape(account_label)}\s+calls=\s*([\d,]+)", out, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


class TestEditFormat:
    def test_call_census_counts_each_tool_separately_and_multi_edit_stays_zero(self, fake_projects, capsys):
        """Edit/Write calls tally under their own tool name; MultiEdit is
        tracked (not hardcoded away) even though this fixture emits none."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
            _opus([_write_tool_use("w1", content="hello")], out=10),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_call_census_count(out, "Edit") == 2
        assert _extract_call_census_count(out, "Write") == 1
        assert _extract_call_census_count(out, "MultiEdit") == 0

    def test_recognized_edit_tool_set_includes_multi_edit(self):
        """A future rename or MultiEdit's reintroduction must be counted
        against its own denominator, not silently zeroed — pinned by
        asserting the recognized-tool constant directly."""
        assert frozenset({"Edit", "Write", "MultiEdit"}) == _mod.EDIT_FAMILY_TOOLS

    def test_multi_edit_reappearing_is_tracked_not_silently_zeroed(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_multi_edit_tool_use("m1")], out=10),
            _error_result("m1", "has not been read yet"),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_call_census_count(out, "MultiEdit") == 1
        assert _extract_known_failure_count(out, "MultiEdit", "unread") == 1
        assert "rate=100.0% of MultiEdit" in out

    def test_write_failure_rate_uses_write_denominator_not_edit(self, fake_projects, capsys):
        """A Write-side 'file has not been read yet' failure is rated
        against Write's own call count, not Edit's — the per-tool pairing
        this subcommand exists to get right."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
            _opus([_write_tool_use("w1", content="x")], out=10),
            _error_result("w1", "Error: file has not been read yet."),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_known_failure_count(out, "Write", "unread") == 1
        assert "rate=100.0% of Write" in out
        assert "rate=50.0% of Write" not in out

    def test_non_edit_tool_error_with_matching_text_not_counted(self, fake_projects, capsys):
        """A Bash error that happens to contain the literal 'String to
        replace not found' text must not be misattributed to Edit — mirrors
        test_current_format_denial_text_without_is_error_ignored's discipline
        against substring-only matching, now that this session's own
        transcript activity can genuinely contain that string in prose."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _opus([_bash_use("b1", "grep -r 'String to replace not found' .")], out=10),
            _error_result("b1", "String to replace not found in grep output"),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_known_failure_count(out, "Edit", "not_found") is None

    def test_unpaired_error_with_no_matching_tool_use_counted_explicitly(self, fake_projects, capsys):
        """An is_error tool_result whose tool_use_id has no tool_use anywhere
        in this session (e.g. a subagent boundary separating a call from its
        own result) increments the unpaired counter instead of being
        silently skipped."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _error_result("orphan-1", "String to replace not found"),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_unpaired_count(out) == 1
        assert _extract_known_failure_count(out, "Edit", "not_found") is None

    def test_noop_pattern_matches_real_error_text(self, fake_projects, capsys):
        """The no-op classifier matches the real message Edit emits
        ('old_string and new_string are exactly the same'), not a mismatched
        pattern — a no-op silently falling into unclassified is the exact
        miscount this pins against."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="same", new_string="same")], out=10),
            _error_result("e1", "InputValidationError: old_string and new_string are exactly the same."),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_known_failure_count(out, "Edit", "noop") == 1
        assert _extract_unclassified_count(out) == 0

    def test_mechanical_total_excludes_noop_but_all_errors_total_includes_it(self, fake_projects, capsys):
        """The 57-vs-63-style distinction: str_replace-mechanical (not_found +
        unread + multi_match) excludes no-ops, while the all-non-governance
        total includes them — asserted as two separate counts, not
        collapsed into one."""
        records: list[dict] = []
        for i, message in enumerate([
            "String to replace not found",
            "String to replace not found",
            "has not been read yet",
            "but replace_all is false",
        ]):
            tid = f"e{i}"
            records.append(_opus([_edit_tool_use(tid, old_string=f"x{i}", new_string=f"y{i}")], out=10))
            records.append(_error_result(tid, message))
        records.append(_opus([_edit_tool_use("e-noop", old_string="same", new_string="same")], out=10))
        records.append(_error_result("e-noop", "old_string and new_string are exactly the same"))
        _write_jsonl(fake_projects / "sess.jsonl", records)

        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert "str_replace-mechanical (Edit not_found+unread+multi_match, no-ops excluded): 4 /" in out
        assert "all non-governance Edit errors (no-ops included): 5 /" in out

    def test_notfound_cause_not_misled_by_indentation_alone(self, fake_projects, capsys):
        """The corrected classifier judges by diffing against the actual
        retry, not by detecting indentation in isolation — an indented retry
        whose content genuinely differs lands in content_differs, not
        whitespace_only, pinning the exact defect the discarded regex
        classifier had (it fired on indentation presence alone, at a 12x
        over-attribution rate against the real corpus)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", file_path="/foo.py", old_string="x = 1", new_string="x = 2")], out=10),
            _error_result("e1", "String to replace not found in file."),
            _opus([_edit_tool_use(
                "e2", file_path="/foo.py",
                old_string="    def foo():\n        return 1",
                new_string="    def foo():\n        return 2",
            )], out=10),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_cause_count(out, "content_differs") == 1
        assert _extract_cause_count(out, "whitespace_only") is None

    def test_notfound_cause_genuine_whitespace_only_retry(self, fake_projects, capsys):
        """The positive counterpart to the indentation-alone test above: a
        retry whose old_string differs from the failed one ONLY in
        whitespace lands in whitespace_only."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", file_path="/foo.py", old_string="x=1", new_string="x=2")], out=10),
            _error_result("e1", "String to replace not found in file."),
            _opus([_edit_tool_use("e2", file_path="/foo.py", old_string="x = 1", new_string="x = 2")], out=10),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_cause_count(out, "whitespace_only") == 1

    def test_notfound_cause_redacted_credential_bucket(self, fake_projects, capsys):
        """A not_found failure whose old_string carries this repo's own
        [REDACTED-CREDENTIAL] placeholder is attributed to the redaction
        hook, not to content drift or abandonment."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use(
                "e1", file_path="/foo.py",
                old_string="token = '[REDACTED-CREDENTIAL]'", new_string="token = ''",
            )], out=10),
            _error_result("e1", "String to replace not found in file."),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_cause_count(out, "redacted_credential") == 1

    @pytest.mark.parametrize("message,expected_label", [
        ("Blocked by plan-review gate: run /plan-review first.", "plan-review"),
        ("Denied: deny-reviewer-tree-mutation.sh reviewer-tree-mutation detected.", "reviewer-tree"),
        ("Blocked by worktree-enforcement: writes must land in a worktree.", "worktree"),
        ("Error: path cannot be safely resolved.", "path-spelling"),
        ("This action was denied by your permission settings.", "permissions"),
        ("Error: writes must stay isolated in the worktree assigned to this session.", "worktree-isolation"),
    ])
    def test_governance_pattern_lands_in_named_bucket(self, fake_projects, capsys, message, expected_label):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _error_result("e1", message),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_governance_count(out, expected_label) == 1

    def test_unrecognized_denial_lands_in_unclassified_not_dropped(self, fake_projects, capsys):
        """A denial matching none of the six governance patterns and none of
        the four known failure patterns still surfaces in a reported count —
        never silently dropped."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _error_result("e1", "Some unrelated tool failure with no known shape."),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_unclassified_count(out) == 1

    def test_old_string_and_write_char_sums_and_percentages(self, fake_projects, capsys):
        """old_string/new_string/Write char sums and the derived overhead
        percentages, hand-computed against a small deterministic fixture."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="12345", new_string="1234567890")], out=100),
            _opus([_write_tool_use("w1", content="abcdefghij")], out=50),
        ])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert "old_string chars: 5" in out
        assert "new_string chars: 10" in out
        assert "write content chars: 10" in out
        assert "old_string share of Edit payload: 33.3%" in out  # 5 / (5 + 10)
        assert "total assistant output tokens (all sessions): 150" in out

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """--no-redact is refused when --config-dir puts more than one root
        in scope, mirroring cost's and context-distribution's own refusal."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_edit_format(_edit_format_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root_content_unchanged(self, fake_projects, capsys):
        """--no-redact with no --config-dir (single root) is unaffected —
        this report's content never varies with redact, since it carries no
        project name or session ID, but the banner still prints for CLI
        parity with cost/context-distribution."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
        ])
        _mod._edit_format_report(_edit_format_args(no_redact=True))
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in out
        assert _extract_call_census_count(out, "Edit") == 1

    def test_per_account_breakdown_uses_account_n_labels_not_raw_paths(self, tmp_path, capsys):
        """Per-account figures are emitted through the same account-N
        labeling convention _build_redact_map documents — never the raw
        config-dir path or account-identifying directory name."""
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
        ])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b", [
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
            _opus([_edit_tool_use("e3", old_string="e", new_string="f")], out=10),
        ])
        _mod._edit_format_report(_edit_format_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "acct-alice-clientwork" not in out
        assert "acct-bob-clientwork" not in out
        assert "repo-a" not in out
        assert "repo-b" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out
        assert _extract_account_edit_calls(out, "account-1") == 1
        assert _extract_account_edit_calls(out, "account-2") == 2

    def test_per_account_breakdown_ordinal_is_resolved_path_sorted_not_scan_order(self, tmp_path, capsys):
        """account-N is assigned by resolved-path sort (_redaction_ordinals),
        not by roots= list order -- "acct-b" sorts before "acct-empty", so
        root_b is account-1 despite being passed second. The sibling test
        above can't catch a regression to scan-order indexing because its
        two root names already sort in scan order."""
        root_empty = _write_cost_root(tmp_path, "acct-empty", "-home-user-repo-e", "sess-e", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
            _opus([_edit_tool_use("e3", old_string="e", new_string="f")], out=10),
        ])
        _mod._edit_format_report(_edit_format_args(), roots=[root_empty, root_b])
        out = capsys.readouterr().out
        assert _extract_account_edit_calls(out, "account-1") == 2  # root_b, resolved-path-sorted first
        assert _extract_account_edit_calls(out, "account-2") == 1  # root_empty

    def test_per_account_zero_calls_prints_no_edit_family_line(self, tmp_path, capsys):
        """An account contributing zero Edit/Write/MultiEdit calls prints the
        'no edit-family calls' line for its row rather than a ZeroDivisionError
        or a silently-omitted row — the per-account counterpart to
        test_zero_edit_family_calls_prints_zeroes_without_division_error,
        which only covers the case where every account is empty."""
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
        ])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b", [
            _user_msg("hi"),
        ])
        _mod._edit_format_report(_edit_format_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert _extract_account_edit_calls(out, "account-1") == 1
        assert "account-2  no edit-family calls" in out

    def test_zero_edit_family_calls_prints_zeroes_without_division_error(self, fake_projects, capsys):
        """An empty scope (no Edit/Write/MultiEdit calls anywhere) prints a
        zero census and 0.0%/n-a shares rather than raising ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_call_census_count(out, "Edit") == 0
        assert "mean old_string chars/edit: n/a" in out
        assert "old_string share of Edit payload: 0.0%" in out

    def test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly(self, tmp_path):
        """Defense-in-depth: _edit_format_report must refuse the multi-root +
        --no-redact combination itself rather than trusting that
        _resolve_cost_roots already validated it, mirroring
        test_no_redact_refused_by_cost_report_itself_even_when_called_directly
        — the in-function guard's own docstring claims this module's tests
        exercise it directly; prior to this test, none did."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._edit_format_report(_edit_format_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2


class TestScanEditFormatSession:
    """Direct unit tests for _scan_edit_format_session's returned stats dict
    — the pure scan/classify/attribute layer this diff exposes, asserted on
    directly rather than only through _edit_format_report's printed report
    (TestEditFormat above), which additionally must survive the print
    layer's own formatting to catch a classifier regression. Both layers are
    warranted; this one is cheaper and more precise for the
    classification/pairing/cause-attribution invariants specifically,
    mirroring TestScanRootTranscripts' direct-dict/tuple assertion shape."""

    def test_call_and_known_failure_counts(self):
        records = [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
            _error_result("e2", "String to replace not found in file."),
            _opus([_write_tool_use("w1", content="x")], out=10),
            _error_result("w1", "Error: file has not been read yet."),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["calls"] == {"Edit": 2, "Write": 1}
        assert stats["known_failures"][("Edit", "not_found")] == 1
        assert stats["known_failures"][("Write", "unread")] == 1

    def test_unpaired_and_unclassified_and_governance_counted_directly(self):
        records = [
            _error_result("orphan-1", "String to replace not found"),
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _error_result("e1", "Some unrelated tool failure with no known shape."),
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
            _error_result("e2", "Blocked by plan-review gate: run /plan-review first."),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["unpaired"] == 1
        assert stats["unclassified"] == 1
        assert stats["governance"]["plan-review"] == 1

    def test_notfound_cause_attribution_direct(self):
        records = [
            _opus([_edit_tool_use("e1", file_path="/foo.py", old_string="x=1", new_string="x=2")], out=10),
            _error_result("e1", "String to replace not found in file."),
            _opus([_edit_tool_use("e2", file_path="/foo.py", old_string="x = 1", new_string="x = 2")], out=10),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["cause"]["whitespace_only"] == 1

    def test_notfound_cause_abandoned_no_retry(self):
        """A not_found failure with no later Edit call on the SAME
        file_path (a later call on a different file doesn't count) — the
        model gave up rather than retrying — lands in abandoned_no_retry,
        not content_differs or any other bucket."""
        records = [
            _opus([_edit_tool_use("e1", file_path="/foo.py", old_string="x=1", new_string="x=2")], out=10),
            _error_result("e1", "String to replace not found in file."),
            _opus([_edit_tool_use("e2", file_path="/bar.py", old_string="y=1", new_string="y=2")], out=10),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["cause"]["abandoned_no_retry"] == 1

    def test_notfound_cause_identical_retry(self):
        """A not_found failure whose next same-file Edit retries the exact
        same old_string (byte-identical, not just whitespace-equivalent)
        lands in identical_retry, not whitespace_only or content_differs."""
        records = [
            _opus([_edit_tool_use("e1", file_path="/foo.py", old_string="x = 1", new_string="x = 2")], out=10),
            _error_result("e1", "String to replace not found in file."),
            _opus([_edit_tool_use("e2", file_path="/foo.py", old_string="x = 1", new_string="x = 3")], out=10),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["cause"]["identical_retry"] == 1

    def test_multi_edit_notfound_counted_as_owner_not_tracked_not_a_crash(self):
        """A not_found failure whose owner is MultiEdit (not Edit) has no
        old_string in edit_order to pair against — must be counted, not
        raise, since edit_order only ever tracks Edit's own calls."""
        records = [
            _opus([_multi_edit_tool_use("m1")], out=10),
            _error_result("m1", "String to replace not found in file."),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["known_failures"][("MultiEdit", "not_found")] == 1
        assert stats["cause"]["owner_not_tracked"] == 1

    def test_known_failure_pattern_takes_precedence_over_governance(self):
        """A tool_result whose text matches both a known-failure pattern and
        a governance pattern classifies as the known failure — pinning the
        early-continue ordering at the site that decides it, so a future
        pattern addition that should instead take governance precedence
        fails a test rather than silently misclassifying."""
        records = [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
            _error_result(
                "e1",
                "Error: file has not been read yet, denied by your permission settings.",
            ),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["known_failures"][("Edit", "unread")] == 1
        assert stats["governance"]["permissions"] == 0

    @pytest.mark.parametrize("length,expected_bucket", [
        (0, "0-99"), (99, "0-99"),
        (100, "100-299"), (299, "100-299"),
        (300, "300-699"), (699, "300-699"),
        (700, "700-1499"), (1499, "700-1499"),
        (1500, "1500+"), (5000, "1500+"),
    ])
    def test_old_string_size_bucket_boundaries(self, length, expected_bucket):
        assert _mod._old_string_size_bucket(length) == expected_bucket

    def test_old_string_size_histogram_direct(self):
        records = [
            _opus([_edit_tool_use("e1", old_string="x" * 50, new_string="y")], out=10),
            _opus([_edit_tool_use("e2", old_string="x" * 200, new_string="y")], out=10),
            _opus([_edit_tool_use("e3", old_string="x" * 50, new_string="y")], out=10),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["old_string_size_hist"]["0-99"] == 2
        assert stats["old_string_size_hist"]["100-299"] == 1

    def test_multi_record_request_id_group_sums_output_tokens_once(self):
        """A requestId group's output_tokens is taken once from the merged
        turn's last record, not summed once per raw content-block record —
        without dedup, stats["output_tokens"] would inflate by however many
        blocks the response split into."""
        records = [
            _opus([_thinking_block()], out=3, request_id="req-1"),
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=3, request_id="req-1"),
            _opus([], out=3111, request_id="req-1"),
        ]
        stats = _mod._scan_edit_format_session(records)
        assert stats["output_tokens"] == 3111
        assert stats["calls"] == {"Edit": 1}


# ---------------------------------------------------------------------------
# read-scope
# ---------------------------------------------------------------------------


def _read_scope_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    since: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "since": since,
    })()


def _read_tool_use(tool_id: str, *, file_path: str, offset: int | None = None, limit: int | None = None) -> dict:
    tool_input: dict = {"file_path": file_path}
    if offset is not None:
        tool_input["offset"] = offset
    if limit is not None:
        tool_input["limit"] = limit
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": tool_input}


def _read_result(tool_id: str, content, *, is_error: bool = False) -> dict:
    """A user-record tool_result for a Read call. `content` is either a plain
    string (a real Read's text output) or a content-block list (e.g. an
    image result) — read-scope's own classification depends on telling the
    two apart, mirroring _tool_result's role for edit-format's tests."""
    return _user_msg([{"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}])


def _growth_asst(*, ts: str, context: int, session_id: str | None = None) -> dict:
    """An assistant record carrying just enough usage for the growth chain:
    input_tokens alone (cache_read/ephemeral left at 0), so
    _context_at_turn(usage) == context exactly."""
    rec = _asst("claude-sonnet-5", ts=ts, content=[])
    rec["message"]["usage"] = {"input_tokens": context, "output_tokens": 10, "cache_read_input_tokens": 0}
    if session_id is not None:
        rec["sessionId"] = session_id
    return rec


def _compact_boundary_rec() -> dict:
    return {"type": "system", "subtype": "compact_boundary"}


def _extract_read_total(out: str) -> int:
    match = re.search(r"^Read calls: ([\d,]+)", out, re.MULTILINE)
    assert match is not None, "Read calls line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_cohort_count(out: str, cohort_label: str) -> int:
    match = re.search(rf"^{re.escape(cohort_label)}\s+([\d,]+)\s+\(", out, re.MULTILINE)
    assert match is not None, f"{cohort_label} line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_read_scope_summary_count(out: str, label: str) -> int:
    """Read one of the parenthetical-definition summary lines
    ("<label> (...): N") — pages, unparsed_input, unpaired, error_result, and
    non_text_result all share this shape."""
    match = re.search(rf"^{re.escape(label)} \(.*?\): ([\d,]+)", out, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{label} summary line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_account_read_calls(out: str, account_label: str) -> int | None:
    match = re.search(rf"^\s*{re.escape(account_label)}\s+calls=\s*([\d,]+)", out, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


def _extract_growth_tokens(out: str) -> int:
    match = re.search(r"^prompt-token growth: ([\d,]+)", out, re.MULTILINE)
    assert match is not None, "prompt-token growth line not found in output"
    return int(match.group(1).replace(",", ""))


class TestReadScope:
    def test_zero_read_calls_prints_zeroes_without_division_error(self, fake_projects, capsys):
        """An empty scope (no Read calls anywhere) prints a zero census and
        0.0% shares rather than raising ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert _extract_read_total(out) == 0
        assert "targeted    0  (0.0% of Read calls)" in out
        assert "whole_file  0  (0.0% of Read calls)" in out

    def test_read_call_census_counts_offset_limit_and_pages_calls(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_tool_use("r1", file_path="/a.py", offset=0, limit=50)]),
            _read_result("r1", "x" * 40),
            _opus([_read_tool_use("r2", file_path="/b.py")]),
            _read_result("r2", "y" * 400),
        ])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert _extract_read_total(out) == 2
        assert _extract_cohort_count(out, "targeted") == 1
        assert _extract_cohort_count(out, "whole_file") == 1

    def test_cohort_percentages_divide_by_full_census_not_targeted_plus_whole_file(self, fake_projects, capsys):
        """A third Read call whose input has no file_path (unparsed_input)
        must still count in the denominator every cohort share divides by —
        a `targeted + whole_file` denominator would print 50%/50% instead."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_tool_use("r1", file_path="/a.py", offset=0)]),
            _read_result("r1", "x" * 40),
            _opus([_read_tool_use("r2", file_path="/b.py")]),
            _read_result("r2", "y" * 400),
            _opus([{"type": "tool_use", "id": "r3", "name": "Read", "input": {}}]),
        ])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert _extract_read_total(out) == 3
        assert "targeted    1  (33.3% of Read calls)" in out
        assert "whole_file  1  (33.3% of Read calls)" in out

    def test_unpaired_error_non_text_and_unparsed_input_each_print_own_line(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),  # unpaired
            _opus([_read_tool_use("r2", file_path="/b.py")]),
            _read_result("r2", "denied", is_error=True),  # error_result
            _opus([_read_tool_use("r3", file_path="/c.png")]),
            _read_result("r3", [{"type": "image", "source": {}}]),  # non_text_result
            _opus([{"type": "tool_use", "id": "r4", "name": "Read", "input": {"__unparsedToolInput": "x"}}]),
            _read_result("r4", "text"),
        ])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert _extract_read_scope_summary_count(out, "unpaired") == 1
        assert _extract_read_scope_summary_count(out, "error_result") == 1
        assert _extract_read_scope_summary_count(out, "non_text_result") == 1
        assert _extract_read_scope_summary_count(out, "unparsed_input") == 1
        assert _extract_read_total(out) == 4

    def test_no_file_path_substring_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        distinctive_path = "/very/distinctive/secret-project-path/module.py"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_tool_use("r1", file_path=distinctive_path)]),
            _read_result("r1", "x" * 4000),
            _opus([_read_tool_use("r2", file_path=distinctive_path)]),
            _read_result("r2", "y" * 4000),
        ])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert distinctive_path not in out
        assert "secret-project-path" not in out

    def test_per_account_breakdown_uses_account_n_labels_not_raw_paths(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * 40),
        ])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b", [
            _opus([_read_tool_use("r2", file_path="/b.py", offset=0, limit=10)]),
            _read_result("r2", "y" * 40),
        ])
        _mod._read_scope_report(_read_scope_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "acct-alice-clientwork" not in out
        assert "acct-bob-clientwork" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out
        assert _extract_account_read_calls(out, "account-1") == 1
        assert _extract_account_read_calls(out, "account-2") == 1

    def test_per_account_zero_calls_prints_no_read_calls_line(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * 40),
        ])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b", [
            _user_msg("hi"),
        ])
        _mod._read_scope_report(_read_scope_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert _extract_account_read_calls(out, "account-1") == 1
        assert "account-2  no Read calls" in out

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_read_scope(_read_scope_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root_content_unchanged(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * 40),
        ])
        _mod._read_scope_report(_read_scope_args(no_redact=True))
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in out
        assert _extract_read_total(out) == 1

    def test_no_redact_refused_by_read_scope_report_itself_even_when_called_directly(self, tmp_path):
        """Defense-in-depth: _read_scope_report must refuse the multi-root +
        --no-redact combination itself rather than trusting that
        _resolve_cost_roots already validated it, mirroring
        test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _opus([_read_tool_use("r2", file_path="/b.py")]),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._read_scope_report(_read_scope_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2

    def test_since_flag_filters_growth_deltas_at_report_level(self, fake_projects, capsys):
        records = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=50),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=90),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod._read_scope_report(_read_scope_args(since="9999d"))
        out_all_time = capsys.readouterr().out
        assert _extract_growth_tokens(out_all_time) == 80

        _mod._read_scope_report(_read_scope_args(since="1d"))
        out_recent = capsys.readouterr().out
        assert _extract_growth_tokens(out_recent) == 0

    def test_growth_and_repeat_reads_wire_real_subagent_files_through_the_report(self, fake_projects, capsys):
        """Exercises the disk-partitioning wiring end to end: growth_tokens
        and the repeat-whole-file-read count must reflect the per-source-file
        boundary _read_session_file_partitioned reads from a real subagent
        file on disk, not just the hand-built groups TestScanReadScopeSession's
        direct-dict tests construct.

        The subagent file is written the way real ones are -- named for its
        agent ("agent-a") while its records carry the PARENT session's id
        ("sess") -- and contributes 120 of the expected 160. Attributing a
        group against its own filename zeroes that contribution and measured
        a 54% drop in total growth on the real corpus; this assertion is what
        catches it. The subagent's first turn (500) has no predecessor and so
        never diffs against the main file's last (50)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10, session_id="sess"),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=50, session_id="sess"),
            _opus([_read_tool_use("r1", file_path="/shared.py")]),
            _read_result("r1", "x" * 4000),
            _opus([_read_tool_use("r2", file_path="/shared.py")]),
            _read_result("r2", "y" * 4000),
        ])
        _write_subagent_jsonl(fake_projects, "sess", "agent-a", [
            _growth_asst(ts="2026-05-19T10:05:00.000Z", context=500, session_id="sess"),
            _growth_asst(ts="2026-05-19T10:06:00.000Z", context=620, session_id="sess"),
        ])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert _extract_growth_tokens(out) == 160
        assert "repeat reads: 1" in out

    def test_locate_step_reports_zero_without_division_error(self, fake_projects, capsys):
        """No Grep/Glob calls in scope must print a zero locate-step line,
        not raise ZeroDivisionError computing the mean."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert "0 calls, ~0 tok, mean ~0 tok/call" in out

    def test_locate_step_mean_is_integer_division(self, fake_projects, capsys):
        """7 total locate-result tokens across 2 calls floors to a mean of 3,
        not the 3.5 a float division would print."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep", "input": {"pattern": "x", "path": "."}}]),
            _user_msg([_tool_result("g1", "x" * 16)]),  # 4 tokens
            _opus([{"type": "tool_use", "id": "gl1", "name": "Glob", "input": {"pattern": "**/*.py", "path": "."}}]),
            _user_msg([_tool_result("gl1", "y" * 12)]),  # 3 tokens
        ])
        _mod._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert "2 calls, ~7 tok, mean ~3 tok/call" in out


class TestScanReadScopeSession:
    """Direct unit tests for _scan_read_scope_session's returned stats dict,
    mirroring TestScanEditFormatSession's role: classification/pairing/growth
    invariants asserted directly on the dict, cheaper and more precise than
    only exercising them through _read_scope_report's printed output
    (TestReadScope above)."""

    def test_offset_zero_lands_in_targeted_not_whole_file(self):
        """offset=0 is a valid first-line read and is falsy in Python —
        pins the is-not-None classification discipline against a
        truthiness regression that would silently file first-line reads
        as whole-file."""
        records = [
            _opus([_read_tool_use("r1", file_path="/foo.py", offset=0, limit=50)]),
            _read_result("r1", "line1\nline2\n"),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_TARGETED] == 1
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_WHOLE_FILE] == 0

    def test_limit_zero_lands_in_targeted_not_whole_file(self):
        """limit=0 is falsy in Python but a valid present value -- pins the
        is-not-None classification discipline against a truthiness
        regression that would misclassify it as whole-file, mirroring the
        offset=0 case above."""
        records = [
            _opus([_read_tool_use("r1", file_path="/foo.py", limit=0)]),
            _read_result("r1", ""),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_TARGETED] == 1
        assert stats["limit_n"] == 1

    def test_offset_only_and_limit_only_each_land_in_targeted(self):
        """An implementation checking only one of offset/limit would
        misclassify the other with no test failing unless both are covered
        independently."""
        records = [
            _opus([_read_tool_use("r1", file_path="/a.py", offset=10)]),
            _read_result("r1", "x" * 40),
            _opus([_read_tool_use("r2", file_path="/b.py", limit=20)]),
            _read_result("r2", "y" * 40),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_TARGETED] == 2
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_WHOLE_FILE] == 0

    def test_subagent_merged_read_lands_in_subagent_scope_not_main(self):
        """The published 'whole-file tokens inside subagents' figure
        depends on this bucketing being right."""
        records = [
            _opus([_read_tool_use("r1", file_path="/main.py")]),
            _read_result("r1", "z" * 4000),
            _asst("claude-sonnet-4-6", sidechain=True, content=[_read_tool_use("r2", file_path="/sub.py")]),
            _read_result("r2", "w" * 4000),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_scope_count"][(_mod._READ_SCOPE_COHORT_WHOLE_FILE, _mod._READ_SCOPE_SCOPE_MAIN)] == 1
        assert stats["cohort_scope_count"][(_mod._READ_SCOPE_COHORT_WHOLE_FILE, _mod._READ_SCOPE_SCOPE_SUBAGENT)] == 1

    def test_size_hist_tokens_accumulates_into_same_key_as_size_hist_count(self):
        """size_hist_tokens keys identically to size_hist -- (cohort, scope,
        bucket) -- and sums est. tokens rather than counting occurrences.
        Two Reads of different sizes must land in different buckets, each
        with its own count and its own token sum."""
        records = [
            _opus([_read_tool_use("r1", file_path="/small.py")]),
            _read_result("r1", "a" * 40),  # 10 tokens -> bucket "0-499"
            _opus([_read_tool_use("r2", file_path="/big.py")]),
            _read_result("r2", "b" * 3000),  # 750 tokens -> bucket "500-1999"
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        cohort_scope = (_mod._READ_SCOPE_COHORT_WHOLE_FILE, _mod._READ_SCOPE_SCOPE_MAIN)
        assert stats["size_hist"][(*cohort_scope, "0-499")] == 1
        assert stats["size_hist_tokens"][(*cohort_scope, "0-499")] == 10
        assert stats["size_hist"][(*cohort_scope, "500-1999")] == 1
        assert stats["size_hist_tokens"][(*cohort_scope, "500-1999")] == 750

    def test_cohort_bucket_token_total_sums_across_both_scopes(self):
        """The size-histogram percentage denominator sums a cohort's tokens
        across both main and subagent scope, not the printing scope's own
        total -- a single-scope denominator would hide that a subagent's
        reads dominate the cohort's tokens."""
        records = [
            _opus([_read_tool_use("r1", file_path="/main.py")]),
            _read_result("r1", "a" * 40),  # 10 tokens, whole_file/main
            _asst("claude-sonnet-4-6", sidechain=True, content=[_read_tool_use("r2", file_path="/sub.py")]),
            _read_result("r2", "b" * 3000),  # 750 tokens, whole_file/subagent
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert _mod._read_scope_cohort_bucket_token_total(stats, _mod._READ_SCOPE_COHORT_WHOLE_FILE) == 760

    def test_repeat_whole_file_read_not_counted_across_main_and_subagent(self):
        """A parent transcript and its subagent are separate context windows:
        the same path read whole-file once in the main group and once in a
        subagent group is not a repeat -- the subagent's read is the only
        way that subagent can see the file at all, not redundant work."""
        main_group = [
            _opus([_read_tool_use("r1", file_path="/shared.py")]),
            _read_result("r1", "x" * 4000),
        ]
        subagent_group = [
            _asst("claude-sonnet-4-6", sidechain=True, content=[_read_tool_use("r2", file_path="/shared.py")]),
            _read_result("r2", "y" * 4000),
        ]
        groups = [main_group, subagent_group]
        records = main_group + subagent_group
        stats = _mod._scan_read_scope_session(records, groups, None)
        assert stats["repeat_whole_file_reads"] == 0
        assert stats["repeat_whole_file_tokens"] == 0

    def test_repeat_whole_file_read_counted_within_same_source_file(self):
        """The same path read whole-file twice within the same source file
        (same context window) is exactly one repeat."""
        records = [
            _opus([_read_tool_use("r1", file_path="/shared.py")]),
            _read_result("r1", "x" * 4000),
            _opus([_read_tool_use("r2", file_path="/shared.py")]),
            _read_result("r2", "y" * 4000),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["repeat_whole_file_reads"] == 1
        assert stats["repeat_whole_file_tokens"] == 1000

    def test_repeat_output_log_suffixed_path_counted_in_output_log_subcounter(self):
        """A repeated .log-suffixed whole-file read increments the
        .output/.log sub-counter, not just the general repeat counter."""
        records = [
            _opus([_read_tool_use("r1", file_path="/run.log")]),
            _read_result("r1", "x" * 4000),
            _opus([_read_tool_use("r2", file_path="/run.log")]),
            _read_result("r2", "y" * 4000),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["repeat_whole_file_reads"] == 1
        assert stats["repeat_whole_file_output_log_reads"] == 1

    def test_repeat_non_output_log_suffixed_path_not_counted_in_output_log_subcounter(self):
        """A repeated whole-file read of a path with no .output/.log suffix
        must leave the sub-counter at 0 even though the general repeat
        counter still increments."""
        records = [
            _opus([_read_tool_use("r1", file_path="/shared.py")]),
            _read_result("r1", "x" * 4000),
            _opus([_read_tool_use("r2", file_path="/shared.py")]),
            _read_result("r2", "y" * 4000),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["repeat_whole_file_reads"] == 1
        assert stats["repeat_whole_file_output_log_reads"] == 0

    def test_non_read_tool_with_offset_limit_shaped_input_not_counted(self):
        records = [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "x", "offset": 5, "limit": 10}}]),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["read_total"] == 0
        assert stats["offset_n"] == 0
        assert stats["limit_n"] == 0

    def test_grep_and_glob_increment_locate_call_n_and_result_tokens_bash_and_read_do_not(self):
        records = [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep", "input": {"pattern": "x", "path": "."}}]),
            _user_msg([_tool_result("g1", "x" * 80)]),  # 20 tokens
            _opus([{"type": "tool_use", "id": "gl1", "name": "Glob", "input": {"pattern": "**/*.py", "path": "."}}]),
            _user_msg([_tool_result("gl1", "y" * 40)]),  # 10 tokens
            _opus([_bash_use("b1", "ls")]),
            _user_msg([_tool_result("b1", "z" * 400)]),  # not a locate tool -- excluded
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "w" * 40),  # Read itself is never a locate call
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["locate_call_n"] == 2
        assert stats["locate_result_tokens_total"] == 30

    def test_grep_error_result_still_counts_as_locate_call_with_tokens_included(self):
        """The call already happened at tool_use time -- locate_call_n is
        incremented there, before any result arrives -- so an is_error
        result doesn't retroactively un-count it. Pins that the result's
        string content is still summed into locate_result_tokens_total
        regardless of is_error, matching all_tool_result_tokens_total's own
        unconditional accounting."""
        records = [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep", "input": {"pattern": "x", "path": "."}}]),
            _user_msg([{"type": "tool_result", "tool_use_id": "g1", "content": "x" * 40, "is_error": True}]),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["locate_call_n"] == 1
        assert stats["locate_result_tokens_total"] == 10

    def test_grep_non_string_result_still_counts_as_locate_call_with_zero_tokens(self):
        """A non-string result (e.g. a content-block list) can't be sized in
        chars, so it contributes 0 tokens -- but the call itself still
        counts, for the same reason as the error-result case above."""
        records = [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep", "input": {"pattern": "x", "path": "."}}]),
            _user_msg([{"type": "tool_result", "tool_use_id": "g1", "content": [{"type": "text", "text": "hits"}]}]),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["locate_call_n"] == 1
        assert stats["locate_result_tokens_total"] == 0

    def test_error_result_counted_and_excluded_from_histogram(self):
        records = [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "permission denied", is_error=True),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["error_result"] == 1
        assert stats["cohort_scope_count"][(_mod._READ_SCOPE_COHORT_WHOLE_FILE, _mod._READ_SCOPE_SCOPE_MAIN)] == 0
        assert stats["read_total"] == 1

    def test_unpaired_read_call_counted_but_call_still_in_census(self):
        records = [_opus([_read_tool_use("r1", file_path="/a.py")])]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["unpaired"] == 1
        assert stats["read_total"] == 1

    def test_non_text_result_counted_and_excluded_from_histogram(self):
        records = [
            _opus([_read_tool_use("r1", file_path="/a.png")]),
            _read_result("r1", [{"type": "image", "source": {"type": "base64", "data": "..."}}]),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["non_text_result"] == 1
        assert stats["cohort_scope_count"][(_mod._READ_SCOPE_COHORT_WHOLE_FILE, _mod._READ_SCOPE_SCOPE_MAIN)] == 0
        assert stats["read_total"] == 1

    def test_pages_read_lands_in_own_counter_not_a_cohort(self):
        records = [
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/doc.pdf", "pages": "1-3"}}]),
            _read_result("r1", "page text " * 20),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_PAGES] == 1
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_TARGETED] == 0
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_WHOLE_FILE] == 0
        assert stats["cohort_scope_count"][(_mod._READ_SCOPE_COHORT_WHOLE_FILE, _mod._READ_SCOPE_SCOPE_MAIN)] == 0
        assert stats["read_total"] == 1

    def test_unparsed_tool_input_lands_in_neither_cohort_but_still_in_census(self):
        """Asserts three things, not one: both cohort counters are 0,
        unparsed_input == 1, and the total Read call census still counts the
        record — a classifier that silently dropped the record instead of
        filing it under unparsed_input would satisfy only the first two."""
        records = [
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {"__unparsedToolInput": "garbled"}}]),
            _read_result("r1", "whatever"),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_TARGETED] == 0
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_WHOLE_FILE] == 0
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_UNPARSED] == 1
        assert stats["read_total"] == 1

    def test_read_call_with_empty_input_lands_in_unparsed_input(self):
        records = [_opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}])]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod._READ_SCOPE_COHORT_UNPARSED] == 1
        assert stats["read_total"] == 1

    def test_reads_excluded_from_histogram_contribute_to_neither_size_hist_nor_tokens(self):
        """error, non-text, pages, and unparsed_input Reads must all skip the
        size histogram entirely, in both its count and its token sum -- a
        token sum that counted any of these would inflate the denominator
        the revisit trigger and any ceiling estimate are stated against."""
        records = [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * 4000, is_error=True),
            _opus([_read_tool_use("r2", file_path="/b.png")]),
            _read_result("r2", [{"type": "image", "source": {}}]),
            _opus([{"type": "tool_use", "id": "r3", "name": "Read", "input": {"file_path": "/c.pdf", "pages": "1-3"}}]),
            _read_result("r3", "page text " * 500),
            _opus([{"type": "tool_use", "id": "r4", "name": "Read", "input": {"__unparsedToolInput": "x"}}]),
            _read_result("r4", "y" * 4000),
        ]
        stats = _mod._scan_read_scope_session(records, [records], None)
        assert sum(stats["size_hist"].values()) == 0
        assert sum(stats["size_hist_tokens"].values()) == 0

    def test_read_session_file_merges_main_and_two_subagent_files_in_sorted_order(self, fake_projects):
        """Independent oracle: expected is hand-built here, not derived by
        calling _read_session_file_partitioned (the function under test) --
        _read_session_file is now defined as exactly that function's own
        flatten, so an oracle built the same way could never fail."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        _write_subagent_jsonl(fake_projects, "sess", "agent-a", [_opus([_read_tool_use("r2", file_path="/b.py")])])
        _write_subagent_jsonl(fake_projects, "sess", "agent-b", [_opus([_read_tool_use("r3", file_path="/c.py")])])
        jsonl = fake_projects / "sess.jsonl"

        expected = [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _opus([_read_tool_use("r2", file_path="/b.py")]),
            _opus([_read_tool_use("r3", file_path="/c.py")]),
        ]
        assert _mod.corpus.read_session_file(jsonl, include_subagents=True) == expected

    def test_read_session_file_partitioned_keeps_empty_main_group_ahead_of_subagents(self, fake_projects):
        """A readable-but-empty main file still yields its own empty group
        ahead of its subagent's group -- pins the empty-main-file edge case
        (an early return on a falsy main group would drop the subagent
        records entirely) against a hand-built expected value."""
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _write_subagent_jsonl(fake_projects, "sess", "agent-a", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        jsonl = fake_projects / "sess.jsonl"

        expected = [
            [],
            [_opus([_read_tool_use("r1", file_path="/a.py")])],
        ]
        assert _mod._read_session_file_partitioned(jsonl, include_subagents=True) == expected

    # -- growth chain --

    def test_single_turn_sequence_yields_zero_growth(self):
        group = [_growth_asst(ts="2026-05-19T10:00:00.000Z", context=100)]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 0

    def test_compact_boundary_resets_the_chain(self):
        """Without the reset, the first post-compaction turn (context=50)
        would be diffed against the pre-compaction turn (context=10),
        counting the new sequence's own baseline as 40 tokens of growth."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10),
            _compact_boundary_rec(),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=50),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=70),
        ]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 20

    def test_two_subagent_files_do_not_produce_a_cross_file_delta(self):
        """Flattened into one chain, the second file's first turn
        (context=500) would be diffed against the first file's only turn
        (context=10), counting 490 tokens of growth that never happened."""
        group_a = [_growth_asst(ts="2026-05-19T10:00:00.000Z", context=10)]
        group_b = [_growth_asst(ts="2026-05-19T10:05:00.000Z", context=500)]
        stats = _mod._scan_read_scope_session(group_a + group_b, [group_a, group_b], None)
        assert stats["growth_tokens"] == 0

    def test_foreign_session_id_mid_file_excluded_from_neighbouring_deltas(self):
        """The interleaved sessionId="B" record's own context (9999) must not
        be diffed against either of its sessionId="A" neighbours. Chaining
        per sessionId gives "B" its own chain, where it is a first turn with
        no predecessor and so contributes nothing, while "A" chains 10 -> 30
        across it."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10, session_id="A"),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=9999, session_id="B"),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=30, session_id="A"),
        ]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 20

    def test_growth_is_independent_of_which_session_appears_first(self):
        """A foreign sessionId="B" record arriving BEFORE the group's
        sessionId="A" records must not change the result. An earlier
        first-seen-reference design adopted "B" here and excluded both "A"
        records as foreign, yielding 0 instead of the true 20; chaining per
        sessionId removes the ordering dependency entirely rather than
        picking a better reference."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=9999, session_id="B"),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=10, session_id="A"),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=30, session_id="A"),
        ]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 20

    def test_subagent_group_contributes_growth_though_its_records_carry_the_parent_session_id(self):
        """A subagent transcript is named for its agent but its records carry
        the PARENT session's id, so no session id in the group matches the
        file it came from. Growth must still be attributed. Filtering a group
        against its own filename measured a 54% drop in total growth on the
        real corpus -- every subagent group silently contributed zero -- while
        every other test here still passed, because they all use groups whose
        records happen to match their own name."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=100, session_id="parent-session"),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=180, session_id="parent-session"),
        ]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 80

    def test_absent_usage_turn_skipped_not_treated_as_zero_context(self):
        """Treating the missing-usage turn as context=0 would manufacture a
        150-token spike on the following turn instead of the true 50."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=100),
            _asst("claude-sonnet-5", ts="2026-05-19T10:01:00.000Z", content=[]),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=150),
        ]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 50

    def test_shrinking_context_yields_no_negative_contribution(self):
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=100),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=40),
        ]
        stats = _mod._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 0

    def test_since_filters_completed_deltas_not_records(self):
        """--since excludes the first delta (owned by the T1 turn, before the
        cutoff) while still using T1 as the T2 delta's predecessor — a
        record-level filter would either drop T1 and inflate T2's delta
        against T0, or drop T1 and read T2 as a first turn contributing 0."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=50),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=90),
        ]
        since_ts = _mod._parse_ts("2026-05-19T10:01:30.000Z")
        stats = _mod._scan_read_scope_session(group, [group], since_ts)
        assert stats["growth_tokens"] == 40

    def test_since_active_with_unparseable_owning_timestamp_excludes_delta_and_counts_it(self):
        """--since is fail-closed on an unparseable owning-turn timestamp:
        the delta is excluded from growth_tokens rather than included (which
        would silently inflate the figure beyond what --since promises), and
        the exclusion is counted so the number stays auditable."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10),
            _growth_asst(ts="not-a-timestamp", context=50),
        ]
        since_ts = _mod._parse_ts("2026-05-19T09:00:00.000Z")
        stats = _mod._scan_read_scope_session(group, [group], since_ts)
        assert stats["growth_tokens"] == 0
        assert stats["growth_unparseable_ts_excluded"] == 1


# ---------------------------------------------------------------------------
# instrument-authoring
# ---------------------------------------------------------------------------


def _instrument_authoring_args(
    *, projects: str = "*", this_repo: bool = False, extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects, "this_repo": this_repo, "extra_config_dirs": extra_config_dirs,
    })()


def _extract_instrument_authoring_call(out: str, shape_label: str, scope: str) -> tuple[int, int]:
    """Read one census line's (count, chars) -- e.g. "Bash (heredoc/-c/-e)   main      count=...  chars=~..."."""
    match = re.search(
        rf"^\s*{re.escape(shape_label)}\s+{re.escape(scope)}\s+count=\s*([\d,]+)\s+chars=~\s*([\d,]+)",
        out, re.MULTILINE,
    )
    assert match is not None, f"{shape_label}/{scope} census line not found in output"
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _extract_instrument_authoring_bucket_count(out: str, scope: str, label: str) -> int:
    match = re.search(
        rf"^\s*{re.escape(scope)}\s+{re.escape(label)}\s+count=\s*([\d,]+)",
        out, re.MULTILINE,
    )
    assert match is not None, f"{scope}/{label} bucket line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_instrument_authoring_cohort(out: str, cohort_label: str) -> tuple[int, int]:
    """Read one cohort line's (session_n, payload_chars)."""
    match = re.search(
        rf"^\s*{re.escape(cohort_label)}\s+sessions=\s*([\d,]+).*?chars=~\s*([\d,]+)",
        out, re.MULTILINE,
    )
    assert match is not None, f"{cohort_label} cohort line not found in output"
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


class TestInstrumentAuthoring:
    def test_subagent_authoring_shapes_attributed_to_subagent_cohort_not_main(self, fake_projects, capsys):
        """The same authoring shapes on a subagent (isSidechain: true) record
        attribute to the subagent scope, not main -- exercised through a real
        merged main+subagent transcript pair, pinning include_subagents=True."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_bash_use("m1", "git log --oneline -5")]),
        ])
        _write_subagent_jsonl(fake_projects, "sess", "agent-a", [
            _asst("claude-opus-4-7", branch="main", sidechain=True, content=[
                _bash_use("s1", "python3 -c 'print(1)'"),
            ]),
        ])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        main_count, _ = _extract_instrument_authoring_call(out, "Bash (heredoc/-c/-e)", "main")
        subagent_count, _ = _extract_instrument_authoring_call(out, "Bash (heredoc/-c/-e)", "subagent")
        assert main_count == 0
        assert subagent_count == 1

    def test_cohort_lines_printed_for_a_zero_dispatch_and_a_dispatched_session(self, fake_projects, capsys):
        """Report-level smoke test: a zero-dispatch and a dispatched session
        each land in their own printed cohort line -- the arithmetic itself
        is TestScanInstrumentAuthoringSession's
        test_cohort_arithmetic_sums_across_multiple_sessions' job."""
        _write_jsonl(fake_projects / "sess-zero.jsonl", [
            _opus([_bash_use("b1", "python3 -c 'print(1)'")]),
        ])
        _write_jsonl(fake_projects / "sess-dispatched.jsonl", [
            _opus([_bash_use("b2", "python3 -c 'print(1)'")]),
            _opus([_agent_use("a1", "general-purpose")]),
        ])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        zero_sessions, _zero_chars = _extract_instrument_authoring_cohort(out, "zero_dispatch")
        dispatched_sessions, _dispatched_chars = _extract_instrument_authoring_cohort(out, "dispatched")
        assert zero_sessions == 1
        assert dispatched_sessions == 1

    def test_size_histogram_bucket_wired_through_report(self, fake_projects, capsys):
        """The per-scan size bucket reaches the printed histogram section,
        not just _scan_instrument_authoring_session's own returned dict."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_bash_use("b1", "python3 -c 'print(1)'")])])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert _extract_instrument_authoring_bucket_count(out, "main", "0-99") == 1

    def test_no_authored_payload_text_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        sentinel = "DISTINCTIVE-SENTINEL-PAYLOAD-MUST-NOT-LEAK"
        command = f"python3 -c 'print(\"{sentinel}\")'"
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_bash_use("b1", command)])])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert sentinel not in out

    def test_no_heredoc_body_text_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        sentinel = "DISTINCTIVE-HEREDOC-SENTINEL-MUST-NOT-LEAK"
        command = f"cat <<EOF > /tmp/instrument.py\n{sentinel}\nEOF\n"
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_bash_use("b1", command)])])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert sentinel not in out

    def test_no_write_content_text_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        sentinel = "DISTINCTIVE-WRITE-SENTINEL-MUST-NOT-LEAK"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_write_use("w1", sentinel, path="/tmp/scratch/instrument.py")]),
        ])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert sentinel not in out

    def test_zero_calls_prints_zeroes_without_division_error(self, fake_projects, capsys):
        """An empty scope (no authoring calls anywhere) prints a zero census
        and 0.0% cohort shares rather than raising ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert "zero_dispatch  sessions=     1 (100.0% of sessions)" in out
        assert "dispatched     sessions=     0 (0.0% of sessions)" in out
        assert "(0.0% of authored mass)" in out


class TestScanInstrumentAuthoringSession:
    """Direct unit tests for _scan_instrument_authoring_session's returned
    stats dict, mirroring TestScanEditFormatSession's role: classification
    invariants asserted directly on the dict, cheaper and more precise than
    TestInstrumentAuthoring's report-level assertions."""

    def test_heredoc_bash_call_counted_with_body_only_payload(self):
        command = "cat <<EOF > /tmp/instrument.py\nprint('hi')\nEOF\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len("print('hi')")

    def test_inline_python_one_liner_lands_in_smallest_bucket(self):
        records = [_opus([_bash_use("b1", "python3 -c 'print(1)'")])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["size_hist"][("main", "0-99")] == 1

    def test_write_scratchpad_paths_counted_repo_path_not_counted(self):
        """Both /tmp/... and /private/tmp/... scratchpad forms count; an
        ordinary repo file path does not."""
        records = [
            _opus([_write_use("w1", "x" * 40, path="/tmp/scratch/instrument.py")]),
            _opus([_write_use("w2", "y" * 40, path="/private/tmp/scratch/instrument.py")]),
            _opus([_write_use("w3", "z" * 40, path="/repo/src/module.py")]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("write", "main")] == 2
        assert stats["payload_chars"][("write", "main")] == 80

    def test_write_scratchpad_path_component_outside_tmp_root_counted(self):
        """A 'scratchpad' path segment counts even when the path isn't
        rooted under /tmp or /private/tmp; a segment that only contains
        'scratchpad' as a substring of a different directory name does not."""
        records = [
            _opus([_write_use("w1", "x" * 40, path="/repo/scratchpad/notes.py")]),
            _opus([_write_use("w2", "y" * 40, path="/repo/scratchpad_utils.py")]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("write", "main")] == 1
        assert stats["payload_chars"][("write", "main")] == 40

    def test_spawn_count_from_both_agent_and_task_tool_names(self):
        records = [
            _opus([_agent_use("a1", "general-purpose", tool_name="Agent")]),
            _opus([_agent_use("a2", "code-writer", tool_name="Task")]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["spawn_dispatch_n"] == 2

    def test_subagent_spawn_dispatch_excluded_and_authoring_call_scoped_to_subagent(self):
        """A subagent's own Agent/Task tool_use (isSidechain: true) must not
        increment this session's main-thread spawn_dispatch_n -- only
        main-thread dispatches decide the cohort split -- and a Bash
        authoring call in the same sidechain record attributes to the
        subagent scope, pinned directly on the scan dict rather than only
        through the heavier report-level fixture test."""
        subagent_records = [
            _asst("claude-opus-4-7", branch="main", sidechain=True, content=[
                _agent_use("a1", "general-purpose", tool_name="Agent"),
                _bash_use("b1", "python3 -c 'print(1)'"),
            ]),
        ]
        stats = _mod._scan_instrument_authoring_session(subagent_records)
        assert stats["spawn_dispatch_n"] == 0
        assert stats["call_n"][("bash", "subagent")] == 1
        assert stats["call_n"][("bash", "main")] == 0

        main_records = [_opus([_agent_use("a2", "general-purpose", tool_name="Task")])]
        main_stats = _mod._scan_instrument_authoring_session(main_records)
        assert main_stats["spawn_dispatch_n"] == 1

    def test_main_payload_chars_excludes_subagent_scope_authoring(self):
        """main_payload_chars feeds cohort_totals[...]['payload_chars']
        directly -- the reported "authored mass" figure -- so it must sum
        only main-thread authoring calls, never subagent ones."""
        records = [
            _opus([_bash_use("b1", "python3 -c 'main-payload'")]),
            _asst("claude-opus-4-7", branch="main", sidechain=True, content=[
                _bash_use("b2", "python3 -c 'subagent-payload-much-longer'"),
            ]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["main_payload_chars"] == len("main-payload")

    def test_ordinary_bash_call_not_classified_as_authoring(self):
        records = [
            _opus([_bash_use("b1", "git log --oneline -10")]),
            _opus([_bash_use("b2", "pytest claude/.claude/scripts/tests/")]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0

    def test_adversarial_dash_c_false_positives_not_counted(self):
        """-c bound to a non-interpreter argv[0] (curl, tar, ssh) never
        counts, mirroring TestEditFormat's
        test_non_edit_tool_error_with_matching_text_not_counted's discipline
        against substring-only matching."""
        records = [
            _opus([_bash_use("b1", "curl -c cookies.txt https://example.com")]),
            _opus([_bash_use("b2", "tar -cf out.tar .")]),
            _opus([_bash_use("b3", "ssh -c aes256-ctr host 'ls'")]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0

    @pytest.mark.parametrize("command,inline_program", [
        ("python3 -c 'print(1)'", "print(1)"),
        ("python -c 'print(1)'", "print(1)"),
        ("sh -c 'echo hi'", "echo hi"),
        ("bash -c 'echo hi'", "echo hi"),
        ("node -e 'console.log(1)'", "console.log(1)"),
        ("perl -e 'print 1'", "print 1"),
        ("ruby -e 'puts 1'", "puts 1"),
    ])
    def test_each_recognized_interpreter_matches_its_own_flag(self, command, inline_program):
        """Every entry in _INSTRUMENT_AUTHORING_INLINE_INTERPRETER_FLAGS gets
        a genuine positive match on its own flag, not just python3/node."""
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len(inline_program)

    @pytest.mark.parametrize("command", [
        "bash -e deploy.sh",  # errexit, not inline-eval
        "python3 -m pytest",  # module flag, not -c
    ])
    def test_recognized_interpreter_with_wrong_flag_not_counted(self, command):
        """A recognized interpreter bound to a flag other than its own
        inline-eval flag (bash -e, python3 -m) never counts."""
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0

    def test_heredoc_dash_variant_with_quoted_delimiter_strips_leading_tabs(self):
        command = "cat <<-'EOF'\n\thello\n\tEOF\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len("hello")

    def test_heredoc_non_eof_delimiter_recognized(self):
        command = "cat <<PAYLOAD\nbody text\nPAYLOAD\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len("body text")

    def test_heredoc_body_containing_delimiter_word_as_data_does_not_truncate(self):
        command = "cat <<EOF\nfirst line\nEOF is mentioned here, not a real terminator\nlast line\nEOF\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        expected_body = "first line\nEOF is mentioned here, not a real terminator\nlast line"
        assert stats["payload_chars"][("bash", "main")] == len(expected_body)

    def test_two_heredocs_chained_with_and_sum_payloads(self):
        command = "cat <<A > /tmp/a.txt && cat <<B > /tmp/b.txt\nfirst\nA\nsecond\nB\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len("first") + len("second")

    def test_quoted_herestring_not_misparsed_as_heredoc(self):
        """A bash here-string (`<<<`) with a quoted right-hand side is not
        mistaken for a heredoc opener."""
        command = 'cat <<< "$var"\necho after\n'
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0

    def test_unquoted_herestring_not_misparsed_as_heredoc(self):
        """Regression test: `cat <<<EOF` (an unquoted here-string, not a
        heredoc) must not match the heredoc-opener regex starting one
        character into the `<<<` operator -- doing so would consume every
        following line as a fake heredoc body since no line ever equals the
        delimiter "EOF"."""
        command = "cat <<<EOF\necho after\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0
        assert stats["payload_chars"][("bash", "main")] == 0

    def test_heredoc_body_containing_inline_program_shape_not_double_counted(self):
        """A heredoc body containing a line shaped like an inline-program
        invocation (e.g. example code written as data) is not counted a
        second time as its own separate authoring invocation -- the body's
        own char count already includes it once."""
        inline_example_line = "python3 -c 'foo'"
        command = f"cat <<EOF > /tmp/instrument.py\nprint('hi')\n{inline_example_line}\nEOF\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        expected_body = f"print('hi')\n{inline_example_line}"
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len(expected_body)

    def test_double_quoted_inline_program_argument_strips_quotes(self):
        """_extract_shell_arg_at's double-quoted branch (vs. the single-
        quoted/bare forms every other inline-program test here uses) also
        strips the surrounding quotes from the payload it measures."""
        command = 'python3 -c "print(1)"'
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len("print(1)")

    def test_versioned_python_interpreter_recognized(self):
        """Regression test: `python3.11 -c '...'` must classify as authoring
        -- a bare `\\bpython3\\b` word boundary fails between "3" and "."
        (both word-adjacent-to-non-word), which previously blocked the match
        entirely."""
        command = "python3.11 -c 'print(1)'"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len("print(1)")

    def test_unparsed_bash_and_write_input_counted_in_own_cohort_not_dropped(self):
        records = [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"__unparsedToolInput": "x"}}]),
            _opus([{"type": "tool_use", "id": "w1", "name": "Write", "input": {"__unparsedToolInput": "y"}}]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["unparsed_n"]["main"] == 2
        assert sum(stats["call_n"].values()) == 0

    def test_parallel_tool_use_blocks_in_one_turn_both_counted(self):
        records = [
            _opus([
                _bash_use("b1", "python3 -c 'print(1)'"),
                _bash_use("b2", "node -e 'console.log(2)'"),
            ]),
        ]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 2

    def test_bucket_boundary_at_largest_finite_bucket_edge(self):
        """A payload of exactly 9999 chars (the largest finite bucket's own
        upper edge) lands in "2000-9999", not the 10000+ overflow -- pins the
        exclusive-upper-bound convention against an off-by-one."""
        payload = "x" * 9999
        command = f"python3 -c '{payload}'"
        records = [_opus([_bash_use("b1", command)])]
        stats = _mod._scan_instrument_authoring_session(records)
        assert stats["size_hist"][("main", "2000-9999")] == 1
        assert stats["size_hist"][("main", "10000+")] == 0

    @pytest.mark.parametrize("chars,expected_bucket", [
        (0, "0-99"), (99, "0-99"),
        (100, "100-499"), (499, "100-499"),
        (500, "500-1999"), (1999, "500-1999"),
        (2000, "2000-9999"), (9999, "2000-9999"),
        (10000, "10000+"), (50000, "10000+"),
    ])
    def test_instrument_authoring_size_bucket_boundaries(self, chars, expected_bucket):
        assert _mod._instrument_authoring_size_bucket(chars) == expected_bucket

    def test_cohort_arithmetic_sums_across_multiple_sessions(self):
        """The load-bearing computation: two zero-dispatch sessions carrying
        large payloads plus two dispatched sessions carrying small ones must
        produce the two cohort totals the go/no-go rule reads."""
        def _session_stats(*, spawn_dispatch_n, main_payload_chars):
            stats = _mod._new_instrument_authoring_stats()
            stats["spawn_dispatch_n"] = spawn_dispatch_n
            stats["main_payload_chars"] = main_payload_chars
            return stats

        sessions = [
            _session_stats(spawn_dispatch_n=0, main_payload_chars=500),
            _session_stats(spawn_dispatch_n=0, main_payload_chars=500),
            _session_stats(spawn_dispatch_n=1, main_payload_chars=10),
            _session_stats(spawn_dispatch_n=1, main_payload_chars=10),
        ]
        _stats, cohort_totals = _mod._aggregate_instrument_authoring_sessions(sessions)
        assert cohort_totals["zero_dispatch"]["session_n"] == 2
        assert cohort_totals["dispatched"]["session_n"] == 2
        assert cohort_totals["zero_dispatch"]["payload_chars"] == 1000
        assert cohort_totals["dispatched"]["payload_chars"] == 20


# ---------------------------------------------------------------------------
# cost-trend
# ---------------------------------------------------------------------------


def _cost_trend_args(
    *, projects: str = "*", this_repo: bool = False, extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects, "this_repo": this_repo, "extra_config_dirs": extra_config_dirs,
    })()


def _extract_cost_trend_row(out: str, week_label: str) -> dict[str, str] | None:
    """Parse one cost-trend row. week_label may include the trailing ' (partial)'
    suffix — the label itself can be multi-word, so this matches as a literal
    line prefix rather than reusing _table_cols' one-token-per-column model."""
    for line in out.splitlines():
        if line.startswith(week_label):
            rest = line[len(week_label):].split()
            if len(rest) == 3:
                return {"total": rest[0], "context_pct": rest[1], "opus_pct": rest[2]}
    return None


class TestCostTrend:
    def test_week_bucket_boundary_and_per_model_pricing(self, fake_projects, capsys):
        """Turns in two different ISO weeks land in separate rows, each priced
        against its own model's rate via _price_turn (Sonnet 5's $2/MTok base)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # ISO 2026-W23
            _priced("claude-sonnet-5", input=2_000_000, ts="2026-06-08T10:00:00.000Z"),  # ISO 2026-W24
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        w23 = _extract_cost_trend_row(out, "2026-W23")
        w24 = _extract_cost_trend_row(out, "2026-W24")
        assert w23 is not None and w23["total"] == "2.00"
        assert w24 is not None and w24["total"] == "4.00"

    def test_opus_share_and_context_share_computed_per_week(self, fake_projects, capsys):
        """A week mixing an Opus-family turn with a Sonnet turn, and a >=200k
        context turn with a <200k turn, reports both shares correctly."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-5", input=100_000, ts="2026-06-01T10:00:00.000Z"),          # $0.50, <200k
            _priced("claude-sonnet-5", input=100_000, ts="2026-06-01T11:00:00.000Z"),        # $0.20, <200k
            _priced("claude-sonnet-5", input=100_000, cache_read=100_000,
                    ts="2026-06-01T12:00:00.000Z"),  # $0.22 total, context 200,000 >=200k (inclusive edge)
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        assert row is not None
        assert row["total"] == "0.92"
        assert row["opus_pct"] == "54.3%"
        assert row["context_pct"] == "23.9%"

    def test_current_week_labeled_partial_other_weeks_not(self, fake_projects, capsys):
        """The trailing bucket matching `today`'s ISO week is labeled '(partial)';
        an earlier, complete week is not."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23, complete
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-08T10:00:00.000Z"),  # 2026-W24, "today"'s week
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2026, 6, 8))  # today falls in 2026-W24
        out = capsys.readouterr().out
        assert "2026-W24 (partial)" in out
        assert "2026-W23 (partial)" not in out
        # The complete week's own row is unlabeled — its line starts with the bare week string.
        assert any(ln.startswith("2026-W23 ") and "(partial)" not in ln for ln in out.splitlines())

    def test_iso_year_boundary_dec31_and_jan1_share_correct_iso_week(self, fake_projects, capsys):
        """Dec 31 2025 falls in ISO week 2026-W01 — isocalendar() assigns
        year-end dates to the following calendar year's week numbering, which
        a bucket keyed on the datetime's plain `.year` would get wrong. `today`
        Jan 1 2026 is in the same ISO week, so the row is labeled '(partial)'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2025-12-31T10:00:00.000Z"),
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2026, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W01 (partial)")
        assert row is not None
        assert row["total"] == "2.00"
        assert "2025-W53" not in out

    def test_unpriced_model_turn_excluded_from_total_and_counted(self, fake_projects, capsys):
        """A turn from a model with no _MODEL_BASE_INPUT_RATES entry is
        excluded from every week's dollar total and reported via its own
        unpriced-turns counter, rather than silently dropped from the total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400,
                  ts="2026-06-01T11:00:00.000Z"),  # claude-opus-4-7 is deliberately unpriced
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        assert row is not None and row["total"] == "2.00"
        # _opus()'s turn: input 50 + output 400 + cache_read 0 = 450 unpriced tokens.
        assert "1 unpriced turns / 450 tokens excluded from priced spend" in out

    def test_multi_record_request_id_group_priced_exactly_once(self, fake_projects, capsys):
        """Three assistant records sharing one requestId (one JSONL record per
        content block, as Claude Code writes for a single API call) carry a
        byte-identical usage dict — cost-trend prices the group's usage once,
        not once per record."""
        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}],
                  ts="2026-06-01T10:00:00.000Z", request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  ts="2026-06-01T10:00:00.000Z", request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}],
                  ts="2026-06-01T10:00:00.000Z", request_id="req-1"),
        ]
        for rec in recs:
            rec["message"]["usage"] = dict(usage)
        _write_jsonl(fake_projects / "sess.jsonl", recs)
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        # $2.00 (claude-sonnet-5's $2/MTok input rate on 1M input tokens) once,
        # not $6.00 for pricing the group's usage three times over.
        assert row is not None and row["total"] == "2.00"


class TestCostTrendConfigDir:
    """cost-trend --config-dir: mirrors TestCostResolveRoots/TestCostMultiRootReport's
    shape for cost-trend's own wiring through _resolve_cost_roots."""

    def test_default_root_alone_when_no_extra_config_dirs(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        roots = _mod._resolve_cost_roots(_cost_trend_args(), subcommand="cost-trend")
        assert roots == [default_dir / "projects"]

    def test_multiple_extra_config_dirs_appended_in_argument_order(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        acct_c = fake_config_dir_factory("acct-c")
        roots = _mod._resolve_cost_roots(
            _cost_trend_args(extra_config_dirs=[str(acct_b), str(acct_c)]), subcommand="cost-trend"
        )
        assert roots == [default_dir / "projects", acct_b / "projects", acct_c / "projects"]

    def test_duplicate_root_deduped_by_resolve_not_string_equality(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(
            _cost_trend_args(extra_config_dirs=[str(acct_b), str(acct_b) + "/."]), subcommand="cost-trend"
        )
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_nonexistent_root_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        missing = tmp_path / "does-not-exist"
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_trend_args(extra_config_dirs=[str(missing)]), subcommand="cost-trend")
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert str(missing) in err
        # Subcommand label composes correctly -- distinct from _resolve_cost_roots'
        # own default "cost" label, which every TestCostResolveRoots case exercises.
        assert "cost-trend:" in err

    def test_root_without_projects_subdir_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_trend_args(extra_config_dirs=[str(bogus)]), subcommand="cost-trend")
        assert exc_info.value.code == 2
        assert "cost-trend:" in capsys.readouterr().err

    def test_this_repo_and_config_dir_compose_end_to_end(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """--config-dir + --this-repo end-to-end through cmd_cost_trend,
        mirroring TestCostMultiRootRedaction's own this-repo-composition test."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        slug = "-home-user-this-repo"
        proj_default = default_dir / "projects" / slug
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-default.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # $2.00
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / slug
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=2_000_000, ts="2026-06-01T10:00:00.000Z"),  # $4.00
        ])
        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: [slug])

        args = _cost_trend_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        _mod.cmd_cost_trend(args)
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        assert row is not None and row["total"] == "6.00"

    def test_redaction_label_shape_account_prefix_no_raw_config_dir_path(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """The new per-root scan diagnostic labels each root account-N (not
        the raw config-dir path), matching cost's own labeling convention."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        proj_default = default_dir / "projects" / "-home-user-repo-a"
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "cost-trend: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost-trend: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert str(default_dir) not in out
        assert str(acct_b) not in out

    def test_per_root_empty_window_warns(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """One of two --config-dir roots holds no transcripts at all -- its
        own WARNING fires, not masked by the other root's non-empty scan
        (Mechanism 2 step 4's new per-root diagnostic, cost-trend's own
        counterpart to cost's own per-root-empty-state coverage)."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        (default_dir / "projects").mkdir(parents=True)  # exists, holds no project dirs
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        # account-N is assigned by resolved-path sort (_redaction_ordinals),
        # not by scan order -- "acct-b" sorts before "default", so the empty
        # default root is account-2 despite being scanned first.
        assert "WARNING: cost-trend: account-2: no transcripts found for this scope" in out
        assert "WARNING: cost-trend: account-1" not in out

    def test_negative_content_no_raw_project_labels_in_stdout(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """Also asserts the new per-root scan-diagnostic lines actually
        printed -- a prior version of this test asserted only the negative
        and still passed with that entire diagnostic block removed, since it
        is the only new code in cost-trend capable of interpolating a path;
        pinning the diagnostic's presence ties the absence-of-leak claim to
        the code path it's meant to guard."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        proj_default = default_dir / "projects" / "-home-user-secret-clientname-a"
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-secret-clientname-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "cost-trend: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost-trend: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "-home-user-secret-clientname-a" not in out
        assert "-home-user-secret-clientname-b" not in out
        assert str(default_dir) not in out
        assert str(acct_b) not in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_permission_error_while_scanning_root_reported_without_raw_path(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """A real unreadable --config-dir root's diagnostic goes to stderr
        without the raw path -- cost-trend's own counterpart to cost's
        chmod-based PermissionError test. This is the one place in this
        diff's cost-trend code capable of interpolating a raw path (the
        `detail = str(exc) if not redact else ...` branch), and it had zero
        fixture coverage (positive or negative) before this test -- the
        prior negative-content test's fixtures never triggered a
        PermissionError, so the redact-suppression logic here was analyzed
        but never actually exercised."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        (default_dir / "projects").mkdir(parents=True)
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        os.chmod(default_dir / "projects", 0o000)
        try:
            _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        finally:
            os.chmod(default_dir / "projects", 0o755)  # restore before tmp_path teardown

        captured = capsys.readouterr()
        out, err = captured.out, captured.err
        assert "cannot scan" in err
        assert str(default_dir) not in err
        # account-N is assigned by resolved-path sort (_redaction_ordinals):
        # "acct-b" sorts before "default" under the same tmp_path parent, so
        # acct_b is account-1 despite default_dir being scanned first.
        assert "cost-trend: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out

    def test_no_redact_flag_not_registered(self, capsys):
        """Pins that cost-trend has no --no-redact argparse entry today, so
        `redact` in _cost_trend_report's diagnostic can never actually be
        False -- the raw-path branches it guards are dead code, not a live
        leak. This test exists so that adding --no-redact to cost-trend
        later (without deliberately revisiting that redaction logic) fails
        loudly here first, instead of silently reactivating a branch nothing
        else currently protects."""
        with pytest.raises(SystemExit):
            _mod.build_parser().parse_args(["cost-trend", "--no-redact"])
        err = capsys.readouterr().err
        assert "unrecognized arguments" in err


# ---------------------------------------------------------------------------
# cost-ledger
# ---------------------------------------------------------------------------


@pytest.fixture()
def cost_ledger_enabled(tmp_path, monkeypatch):
    """Isolated config dir carrying the cost-ledger opt-in sentinel. Patches
    _mod's own config_dir binding, not scope.config_dir: cost-ledger isn't in
    _SUBCOMMANDS_WITH_OWN_CONFIG_DIR, so its sentinel check
    (config_dir() / ".cost-ledger-enabled") reads the shim's own import,
    never scope.py's _resolve_cost_roots."""
    cfg_dir = tmp_path / "isolated-claude-config"
    cfg_dir.mkdir()
    (cfg_dir / ".cost-ledger-enabled").touch()
    monkeypatch.setattr(_mod, "config_dir", lambda: cfg_dir)
    return cfg_dir


def _cost_ledger_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    record: bool = False,
    machine_label: str | None = None,
    force: bool = False,
    note: str = "",
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "record": record,
        "machine_label": machine_label,
        "force": force,
        "note": note,
    })()


def _cost_ledger_row(**overrides) -> dict:
    """A complete, valid row dict with sensible defaults, overridden per test."""
    row = {
        "week": "2026-W20", "machine": "m1", "rates": "2026-08-02", "usd": 12.5,
        "context_pct": 10.0, "opus_pct": 5.0, "ge200k_pct": 10.0,
        "denials": 2, "reviewer_gap_pp": 3.5, "note": "baseline",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# cache-rebuild
# ---------------------------------------------------------------------------


def _cache_rebuild_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    threshold: int | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "threshold": threshold,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


def _extract_cache_rebuild_summary(out: str) -> dict[str, str]:
    """Read cache-rebuild's 'Calls scanned: N' / 'Calls writing >= T tokens:
    N' summary lines as {"scanned": ..., "tail": ...}."""
    scanned = re.search(r"Calls scanned: ([\d,]+)", out)
    tail = re.search(r"Calls writing >= [\d,]+ tokens: ([\d,]+)", out)
    assert scanned is not None, "'Calls scanned' line not found in output"
    assert tail is not None, "'Calls writing >= ... tokens' line not found in output"
    return {"scanned": scanned.group(1).replace(",", ""), "tail": tail.group(1).replace(",", "")}


def _extract_cache_rebuild_row(out: str, row_label: str) -> tuple[int, str]:
    """Read one (count, dollars) row from cache-rebuild's cause-breakdown,
    concurrency-split, or per-account table by its leading label -- labels
    may contain spaces, so this matches the row as a literal line prefix
    rather than reusing _table_cols' one-token-per-column model. The
    cause-breakdown table has no dollars column, so a 1-cell row is read as
    (count, "")."""
    for line in out.splitlines():
        if line.startswith(row_label):
            rest = line[len(row_label):].split()
            if len(rest) == 1:
                return int(rest[0].replace(",", "")), ""
            if len(rest) == 2:
                return int(rest[0].replace(",", "")), rest[1]
    raise AssertionError(f"row not found for {row_label!r}")


class TestCacheRebuildExcessPricing:
    """Direct unit coverage for _cache_rebuild_excess_dollars -- new pricing
    logic (the counterfactual warm-read leg) not exercised by any of
    _price_turn's own existing tests."""

    def test_fast_mode_multiplier_applies_to_both_write_and_warm_read_dollars(self):
        """Mirrors _price_turn's own fast-mode multiplier on the
        counterfactual warm-read leg too, not just the actual write leg --
        otherwise a fast-mode call's excess would overstate the gap between
        what was paid and what a warm hit would have cost."""
        usage = _priced("claude-sonnet-5", ephemeral_5m=1_000_000, speed="fast")["message"]["usage"]
        excess, unpriced_tokens = _mod._cache_rebuild_excess_dollars("claude-sonnet-5", usage)
        # write: 1,000,000/1e6 * 2.00*1.25*2(fast) = 5.00; warm read:
        # 1,000,000/1e6 * 2.00*0.10*2(fast) = 0.40; excess = 4.60.
        assert excess == pytest.approx(4.60)
        assert unpriced_tokens == 0

    def test_unpriced_model_returns_none_excess_not_a_silent_zero(self):
        """A model absent from _MODEL_BASE_INPUT_RATES must not silently
        price its excess as $0 -- callers distinguish 'unpriced' from
        'priced at zero' via the None sentinel, matching _price_turn's own
        unpriced-model contract."""
        usage = _priced("claude-unknown-model", ephemeral_5m=1_000_000, input=10, output=5)["message"]["usage"]
        excess, unpriced_tokens = _mod._cache_rebuild_excess_dollars("claude-unknown-model", usage)
        assert excess is None
        assert unpriced_tokens > 0


class TestCacheRebuildClassification:
    """Direct coverage for _cache_rebuild_report's per-call cause
    classification and priced excess, against a single hand-built
    transcript exercising every case Verification item 1 in
    .claude/plans/context-cost-root-cause.md names."""

    def test_dedup_synthetic_exclusion_cause_classification_and_priced_excess(
        self, fake_projects, capsys
    ):
        """One transcript combining: a multi-record requestId run (must
        dedup to one small, non-tail call), a requestId-less <synthetic>
        entry (must be excluded from both pricing and the i/prev_ts/
        prev_model bookkeeping), the transcript's own first call (must
        classify session start, never an idle bucket), a flat-field
        cache_creation fallback (classifies unexplained at a sub-5-minute
        gap; its pricing-as-5m-only is covered separately by
        test_flat_cache_creation_fallback_priced_as_5m_only), a record with
        no timestamp at all and,
        separately, a negative-gap clock-skew pair and a genuinely garbled
        (non-empty) timestamp string (all three must classify as the
        explicit timestamp-anomaly bucket, never silently folded into idle
        or unexplained), a 6-minute gap (idle 5m-1h, with its priced excess
        hand-computed below), and a model switch at a sub-5-minute gap
        (classifies model switch, not unexplained)."""
        run_ts = "2026-08-01T10:01:00.000Z"
        records = [
            # i=0: first call ever -- session start, regardless of tail size.
            _priced("claude-sonnet-5", ephemeral_5m=150_000, ts="2026-08-01T10:00:00.000Z", request_id="req-first"),
            # Three raw records sharing one requestId: one JSONL record per
            # content block, dedup must collapse this run to one logical
            # call. Small (non-tail) so it doesn't add a 4th tail call.
            _priced("claude-sonnet-5", ephemeral_5m=500, ts=run_ts, request_id="req-multi", output=3),
            _priced("claude-sonnet-5", ephemeral_5m=500, ts=run_ts, request_id="req-multi", output=3),
            _priced("claude-sonnet-5", ephemeral_5m=500, ts=run_ts, request_id="req-multi", output=50),
            # requestId-less synthetic entry, large enough that its wrongful
            # inclusion would be obvious in the cause-breakdown totals below.
            _priced("<synthetic>", ephemeral_5m=500_000, ts="2026-08-01T10:02:00.000Z"),
            # gap from the merged run (10:01:00) is 120s: not idle, same
            # model -- unexplained. Flat-field fallback (no nested
            # cache_creation block) -- see test_flat_cache_creation_fallback_priced_as_5m_only
            # for the pricing-as-5m-only coverage this fixture shape doesn't
            # itself price.
            _priced(
                "claude-sonnet-5", flat_cache_creation=180_000,
                ts="2026-08-01T10:03:00.000Z", request_id="req-flat",
            ),
            # No timestamp at all -- distinct from the clock-skew case below
            # (both endpoints present but out of order): here the call's own
            # endpoint is missing, so no gap can be computed either way.
            _priced("claude-sonnet-5", ephemeral_5m=210_000, ts="", request_id="req-absentts"),
            # Clock skew: 30s BEFORE req-flat's own timestamp (the absent-ts
            # record above never updates the tracked "previous timestamp") --
            # negative gap, must classify as the explicit anomaly bucket too.
            _priced(
                "claude-sonnet-5", ephemeral_5m=250_000,
                ts="2026-08-01T10:02:30.000Z", request_id="req-negts",
            ),
            # 6-minute gap from the clock-skew record's own (still valid)
            # timestamp -- idle 5m-1h. Excess hand-computed below.
            _priced(
                "claude-sonnet-5", ephemeral_5m=300_000,
                ts="2026-08-01T10:08:30.000Z", request_id="req-idle",
            ),
            # 60s gap (not idle) but a different, still-priced model --
            # model switch, not unexplained.
            _priced(
                "claude-opus-5", ephemeral_5m=220_000,
                ts="2026-08-01T10:09:30.000Z", request_id="req-switch",
            ),
            # Genuinely garbled, non-empty timestamp string -- distinct from
            # the absent-timestamp case above, must land in the same
            # explicit anomaly bucket rather than raising or silently
            # excluding the call from the scan entirely.
            _priced(
                "claude-sonnet-5", ephemeral_5m=150_000,
                ts="not-a-date", request_id="req-malformed-ts",
            ),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", records)

        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        # Dedup: 11 raw records collapse to 8 logical calls (the 3-record
        # run counts once); the synthetic entry is excluded entirely (not 9).
        summary = _extract_cache_rebuild_summary(out)
        assert summary["scanned"] == "8"
        assert summary["tail"] == "7"

        # Cause-breakdown rows are (count, share%) -- only the count is
        # asserted here, per-cause share formatting is not this test's concern.
        assert _extract_cache_rebuild_row(out, "session start")[0] == 1
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        assert _extract_cache_rebuild_row(out, "idle >1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "model switch")[0] == 1
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 1
        # The absent-timestamp, negative-gap, and malformed-timestamp
        # records all land here.
        assert _extract_cache_rebuild_row(out, "excluded (timestamp anomaly)")[0] == 3

        # 300,000 5m-tier cache-write tokens at claude-sonnet-5's $2.00/MTok
        # base: write $0.75 (1.25x), warm-read-equivalent $0.06 (0.1x),
        # excess $0.69. Single-transcript fixture: no other session was ever
        # active, so this lands in "everything idle", not "another session".
        assert _extract_cache_rebuild_row(out, "Everything idle (a break)") == (1, "0.69")
        assert _extract_cache_rebuild_row(out, "Another session active") == (0, "0.00")


class TestCacheRebuildGroupBoundary:
    """Verification item 1 (.claude/plans/context-cost-root-cause.md):
    is_first_call/gap_seconds/model_changed must reset at every
    _read_session_file_partitioned group boundary, not just once at the top
    of the whole flattened (main thread + subagent files) session."""

    def test_subagent_groups_own_first_call_never_inherits_main_threads_gap(
        self, fake_projects, capsys
    ):
        """A subagent group's own first call is a large (>=threshold)
        cache-write landing only 6 minutes after the main thread's own
        (small) first call -- in flattened file-concatenation order this
        would fall inside the main thread's own idle-5m-1h gap window.
        Classified per group instead, the subagent's own first call has no
        predecessor within its own group and must classify session start,
        never an idle-gap/model-switch/unexplained cause carried over from
        the main thread's prior call."""
        session_id = "sess-boundary"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="main-1"),
        ])
        subagent_rec = _priced(
            "claude-sonnet-5", ephemeral_5m=200_000,
            ts="2026-08-01T10:06:00.000Z", request_id="sub-1",
        )
        subagent_rec["isSidechain"] = True
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])

        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out

        summary = _extract_cache_rebuild_summary(out)
        assert summary["scanned"] == "2"
        assert summary["tail"] == "1"

        assert _extract_cache_rebuild_row(out, "session start")[0] == 1
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "model switch")[0] == 0
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 0


class TestCacheRebuildCacheTierGapMismatch:
    """Verification item 4: a call's cache-write tier, not just its elapsed
    gap, gates the idle-5m-1h cause -- a purely ephemeral_1h-tier write
    inside a <1h gap cannot have been forced by that gap's TTL expiry, since
    the 1h-tier cache would still be warm."""

    def test_pure_1h_tier_write_in_5m_1h_gap_reclassifies_unexplained(self, fake_projects, capsys):
        """A 6-minute gap whose tail write is entirely ephemeral_1h-tier
        (no ephemeral_5m tokens at all) falls to unexplained, not idle
        5m-1h -- the 1h-TTL cache can't have expired inside 6 minutes."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=200_000,
                ts="2026-08-01T10:06:00.000Z", request_id="r2-pure-1h",
            ),
        ])
        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 0
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 1

    def test_mixed_tier_write_in_5m_1h_gap_still_classifies_idle(self, fake_projects, capsys):
        """The same 6-minute gap, but the tail write carries SOME
        ephemeral_5m tokens alongside its ephemeral_1h tokens -- the
        5m-tier portion could genuinely have been forced by the gap, so
        this stays idle 5m-1h, not unexplained."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=100, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced(
                "claude-sonnet-5", ephemeral_1h=50_000, ephemeral_5m=150_000,
                ts="2026-08-01T10:06:00.000Z", request_id="r2-mixed",
            ),
        ])
        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _extract_cache_rebuild_row(out, "idle 5m-1h")[0] == 1
        assert _extract_cache_rebuild_row(out, "unexplained")[0] == 0


class TestCacheRebuildThresholdBoundary:
    def test_exactly_threshold_tokens_counts_as_tail_one_below_does_not(self, fake_projects, capsys):
        """A call writing exactly the default 100,000-token threshold counts
        as a large rebuild; one writing 99,999 does not -- the >= boundary
        the corpus's own tail-call figures depend on."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", ephemeral_5m=99_999, ts="2026-08-01T10:00:00.000Z", request_id="r1"),
            _priced("claude-sonnet-5", ephemeral_5m=100_000, ts="2026-08-01T10:00:01.000Z", request_id="r2"),
        ])
        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[fake_projects.parent])
        summary = _extract_cache_rebuild_summary(capsys.readouterr().out)
        assert summary["scanned"] == "2"
        assert summary["tail"] == "1"


class TestCacheRebuildConcurrencySplit:
    def test_own_subagent_activity_never_self_matches_and_cross_root_activity_flags_concurrent(
        self, tmp_path, capsys
    ):
        """Three-root fixture (Verification item 3): root A's own session
        has two idle-gap tail calls, and between them a subagent record
        whose own timestamp falls INSIDE the first gap despite being
        processed after both main-thread calls (subagent files are appended
        after the main thread in _read_session_file's file-concatenation
        order, not re-sorted by timestamp) -- proving a transcript's own
        later record never counts as "another session" even when it lands
        chronologically inside its own gap. Root B's own call falls inside
        the SECOND gap, correctly flagging that gap concurrent. Root C is
        valid but holds no transcripts at all -- the realistic state of a
        rarely-used account, exercised here for no crash and no false
        signal on either other root's classification."""
        proj_slug = "-home-user-repo"
        root_a = _write_cost_root(tmp_path, "acct-a", proj_slug, "sess-a", [
            _priced("claude-sonnet-5", ephemeral_5m=500, ts="2026-08-01T10:00:00.000Z", request_id="a-1"),
            # gap from a-1 is 600s -- idle 5m-1h. Window (10:00:00, 10:10:00).
            _priced("claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:10:00.000Z", request_id="a-2"),
            # gap from a-2 is 3,900s -- idle >1h. Window (10:10:00, 11:15:00).
            _priced("claude-sonnet-5", ephemeral_1h=150_000, ts="2026-08-01T11:15:00.000Z", request_id="a-3"),
        ])
        subagent_rec = _priced(
            "claude-sonnet-5", ephemeral_5m=500, ts="2026-08-01T10:05:00.000Z", request_id="a-sub",
        )
        subagent_rec["isSidechain"] = True
        _write_subagent_jsonl(root_a / proj_slug, "sess-a", "agent-1", [subagent_rec])

        # Falls inside the SECOND gap's window -- that gap must flag concurrent.
        root_b = _write_cost_root(tmp_path, "acct-b", proj_slug, "sess-b", [
            _priced("claude-sonnet-5", ephemeral_5m=500, ts="2026-08-01T10:35:00.000Z", request_id="b-1"),
        ])

        root_c = tmp_path / "acct-c"
        root_c.mkdir()  # valid directory, no project dirs or transcripts at all

        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[root_a, root_b, root_c])
        out = capsys.readouterr().out

        assert "[unverified]" in out

        # 200,000 5m-tier tokens: write $0.50, warm read $0.04, excess $0.46.
        assert _extract_cache_rebuild_row(out, "Everything idle (a break)") == (1, "0.46")
        # 150,000 1h-tier tokens: write $0.60, warm read $0.03, excess $0.57.
        assert _extract_cache_rebuild_row(out, "Another session active") == (1, "0.57")

        # account-1 = acct-a (both idle-gap rebuilds live here, including
        # the one whose own gap contained its own subagent's activity);
        # account-2 = acct-b (its own call is other-session activity, never
        # itself a rebuild); account-3 = acct-c (valid but empty).
        assert _extract_cache_rebuild_row(out, "account-1") == (2, "1.03")
        assert _extract_cache_rebuild_row(out, "account-2") == (0, "0.00")
        assert _extract_cache_rebuild_row(out, "account-3") == (0, "0.00")


class TestCacheRebuildMultiRootRegression:
    def test_hand_computed_rebuild_count_and_dollar_total_match_the_union_across_two_roots(
        self, tmp_path, capsys
    ):
        """Verification item 4: a generated corpus of known composition
        (150 calls per root, 300 total), split across two synthetic roots.
        Every 5th call after the first is a large (150,000-token, 5m-tier)
        idle-gap rebuild, reached by a 400-second gap (inside the 5m-1h
        idle bucket); every other call is a small, closely-spaced (10s)
        filler that never crosses the threshold. Regresses against the
        UNION total across both roots, not one root's own count --
        aggregation across roots is exactly what this subcommand adds, and
        a running total or requestId dedup scoped per-root instead of
        post-union would hide here."""
        calls_per_root = 150
        idle_gap_indices = range(5, calls_per_root, 5)  # 5, 10, ..., 145 -> 29 per root
        base_ts = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)

        def _build_records(root_label: str) -> list[dict]:
            records = []
            ts = base_ts
            for i in range(calls_per_root):
                if i > 0:
                    ts += timedelta(seconds=400 if i % 5 == 0 else 10)
                is_rebuild = i > 0 and i % 5 == 0
                records.append(_priced(
                    "claude-sonnet-5",
                    ephemeral_5m=150_000 if is_rebuild else 100,
                    ts=ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    request_id=f"{root_label}-{i}",
                ))
            return records

        root_x = _write_cost_root(tmp_path, "acct-x", "-home-user-repo", "sess-x", _build_records("x"))
        root_y = _write_cost_root(tmp_path, "acct-y", "-home-user-repo", "sess-y", _build_records("y"))

        _mod._cache_rebuild_report(_cache_rebuild_args(), roots=[root_x, root_y])
        out = capsys.readouterr().out

        expected_rebuilds = len(idle_gap_indices) * 2
        # 150,000 5m-tier tokens at $2.00/MTok base: write $0.375, warm read
        # $0.03, excess $0.345 per rebuild.
        expected_excess = expected_rebuilds * 0.345

        summary = _extract_cache_rebuild_summary(out)
        assert summary["tail"] == str(expected_rebuilds)

        idle_rebuilds, _idle_excess = _extract_cache_rebuild_row(out, "Everything idle (a break)")
        concurrent_rebuilds, _concurrent_excess = _extract_cache_rebuild_row(out, "Another session active")
        assert idle_rebuilds + concurrent_rebuilds == expected_rebuilds

        total_rebuilds, total_excess = _extract_cache_rebuild_row(out, "Total idle-gap rebuilds")
        assert total_rebuilds == expected_rebuilds
        assert total_excess == f"{expected_excess:,.2f}"


class TestCacheRebuildNoRedactMultiRootRefusal:
    def test_no_redact_refused_by_cache_rebuild_report_itself_even_when_called_directly(self, tmp_path):
        """Defense-in-depth, mirroring _cost_report's own version of this
        test: _cache_rebuild_report must refuse the multi-root + --no-redact
        combination itself rather than trusting that _resolve_cost_roots
        already validated it -- every test in this module calls
        _cache_rebuild_report directly, bypassing that CLI-level boundary."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", ephemeral_5m=100_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", ephemeral_5m=100_000)])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cache_rebuild_report(_cache_rebuild_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2


class TestCacheRebuildArgparseWiring:
    def test_parses_since_threshold_and_extra_config_dirs(self):
        """cache-rebuild's real argparse wiring, not just the hand-rolled
        _cache_rebuild_args() test shim -- --since defaults to
        _CACHE_REBUILD_DEFAULT_SINCE ('30d'), unlike the shim's own
        since=None default, so a caller relying on the shim alone would
        never catch the two drifting apart."""
        parser = _mod.build_parser()
        args = parser.parse_args(["cache-rebuild"])
        assert args.since == _mod._CACHE_REBUILD_DEFAULT_SINCE
        assert args.threshold == _mod._CACHE_REBUILD_DEFAULT_THRESHOLD
        assert args.extra_config_dirs is None

        args = parser.parse_args([
            "cache-rebuild", "--since", "7d", "--threshold", "50000",
            "--config-dir", "/tmp/acct-b",
        ])
        assert args.since == "7d"
        assert args.threshold == 50_000
        assert args.extra_config_dirs == ["/tmp/acct-b"]


def _reviewer_dispatch_records(
    proj: Path, session_id: str, tool_id: str, subagent_type: str, verdict_text: str,
    *, dispatch_ts: str, result_ts: str,
) -> list[dict]:
    """One reviewer-agent dispatch + its paired tool_result, at explicit
    caller-chosen timestamps (unlike _n_cited_reviewer_dispatches, which
    hardcodes 2026-05-19 and so can't be placed inside an arbitrary
    cost-ledger test week). Writes the paired subagent transcript/meta.json
    as a side effect."""
    records = [
        _asst("claude-opus-4-7", ts=dispatch_ts, content=[_agent_use(tool_id, subagent_type)]),
        _user_msg([_tool_result(tool_id, "ok")], ts=result_ts),
    ]
    _write_subagent_dispatch(
        proj, session_id, f"agent-{tool_id}", tool_id,
        [_asst("claude-sonnet-4-6", content=[{"type": "text", "text": verdict_text}])],
        agent_type=subagent_type,
    )
    return records


class TestCostLedgerPathResolution:
    def test_override_absolute_honored(self, monkeypatch, tmp_path):
        override = tmp_path / "custom-ledger-location" / "cost-ledger.md"
        monkeypatch.setenv("COST_LEDGER_PATH", str(override))
        assert _mod._cost_ledger_path() == override

    def test_override_relative_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("COST_LEDGER_PATH", "relative/cost-ledger.md")
        with pytest.raises(ValueError, match="must be an absolute path"):
            _mod._cost_ledger_path()

    def test_unset_falls_back_to_config_dir(self, monkeypatch, tmp_path):
        """Unset COST_LEDGER_PATH resolves against a monkeypatched
        CLAUDE_CONFIG_DIR, not this workstation's real $HOME."""
        monkeypatch.delenv("COST_LEDGER_PATH", raising=False)
        cfg_dir = tmp_path / "isolated-claude-config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
        assert _mod._cost_ledger_path() == cfg_dir / "cost-ledger.md"


class TestCostLedgerReadMode:
    def test_read_mode_lists_existing_rows_and_flags_unrecorded_live_week(
        self, fake_projects, cost_ledger_file, capsys
    ):
        """Read mode prints an existing row, plus a live-corpus week with no
        row for any machine, as a recording gap."""
        existing = _cost_ledger_row(week="2026-W20", machine="m1")
        cost_ledger_file.write_text(
            cost_ledger_file.read_text() + _mod._format_cost_ledger_row(existing) + "\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23
        ])
        _mod._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        out = capsys.readouterr().out
        assert "2026-W20" in out
        assert "m1" in out
        assert "Weeks present in the live corpus with no ledger row yet:" in out
        assert "2026-W23" in out

    def test_read_mode_missing_ledger_file_refuses(self, fake_projects, tmp_path, monkeypatch):
        """A missing docs/cost-ledger.md refuses rather than silently
        reporting an empty ledger — the file is never lazily created."""
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: tmp_path / "absent-cost-ledger.md")
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        assert exc_info.value.code != 0

    def test_read_mode_missing_ledger_file_prints_never_recorded_wording(
        self, fake_projects, tmp_path, monkeypatch, capsys
    ):
        """Behavioral check that the "never recorded here yet" wording is
        actually reached and printed on this code path, not just present
        somewhere in source — see the source-grep tripwire below for the
        companion check that the old wording doesn't silently return."""
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: tmp_path / "absent-cost-ledger.md")
        with pytest.raises(SystemExit):
            _mod._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        err = capsys.readouterr().err
        assert "no ledger recorded here yet" in err

    def test_old_ledger_file_not_found_wording_absent_from_source(self):
        """Source-grep tripwire, not a behavioral guarantee (see the
        behavioral test above): pins against the literal old message text
        silently reappearing."""
        assert "ledger file not found" not in _SCRIPT.read_text()

    def test_format_read_row_renders_exact_fixed_width_columns(self):
        """_format_cost_ledger_read_row's fixed-width terminal line, checked
        cell-by-cell against a known row -- distinct from the write-side
        markdown line, which round-trips through the canonical parser
        instead (there is no parser for this display-only format)."""
        row = _cost_ledger_row(
            week="2026-W20", machine="m1", usd=1234.56, context_pct=12.3, opus_pct=45.6,
            ge200k_pct=12.3, denials=7, reviewer_gap_pp=-3.2, note="rolled out F3 fix",
        )
        assert _mod._format_cost_ledger_read_row(row) == (
            "2026-W20   m1        2026-08-02      1,234.56     12.3%   45.6%    12.3%"
            "        7      -3.2pp  rolled out F3 fix"
        )

    def test_read_mode_still_returns_union_with_two_declared_roots(
        self, fake_projects, cost_ledger_file, monkeypatch, tmp_path, capsys
    ):
        """Mechanism 8 refuses only --record; a plain read with two declared
        roots must still return the union unchanged -- pins that mechanism 8
        does not touch read-mode semantics."""
        other = tmp_path / "acct-other"
        other_proj = other / "projects" / "-repo-main"
        other_proj.mkdir(parents=True)
        _write_jsonl(other_proj / "sess-other.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{other}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod._cost_ledger_report(_cost_ledger_args(), date(2026, 6, 3))
        out = capsys.readouterr().out
        assert "COST LEDGER SOURCES (" in out
        assert "2 roots" in out


class TestCostLedgerSerializationRoundTrip:
    @staticmethod
    def _table(row_line: str) -> str:
        return (
            _mod._COST_LEDGER_HEADER_LINE + "\n"
            + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
            + row_line + "\n"
        )

    def test_full_row_round_trips_through_the_markdown_line_format(self):
        """A row dict serialized to its markdown line and parsed back through
        the canonical file parser produces the identical dict — no
        dependence on any corpus scan."""
        row = _cost_ledger_row(
            week="2026-W20", machine="m1", usd=1234.56, context_pct=12.3, opus_pct=45.6,
            ge200k_pct=12.3, denials=7, reviewer_gap_pp=-3.2, note="rolled out F3 fix",
        )
        line = _mod._format_cost_ledger_row(row)
        _preamble, rows = _mod._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]

    def test_unmeasured_gap_and_empty_note_round_trip(self):
        """An unmeasured reviewer_gap_pp (None) and an empty note both
        round-trip through the markdown line format unchanged."""
        row = _cost_ledger_row(usd=0.0, context_pct=0.0, opus_pct=0.0, ge200k_pct=0.0,
                                denials=0, reviewer_gap_pp=None, note="")
        line = _mod._format_cost_ledger_row(row)
        _preamble, rows = _mod._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]

    def test_note_with_isolated_brackets_round_trips(self):
        """A '[' and ']' pair with no immediately-following '(...)' is not
        markdown link/image syntax and must round-trip unchanged -- pins the
        markdown-link regex's negative boundary, not just its positive
        matches."""
        row = _cost_ledger_row(note="fix [WIP] rollout")
        line = _mod._format_cost_ledger_row(row)
        _preamble, rows = _mod._parse_cost_ledger_file_text(self._table(line))
        assert rows == [row]


class TestCostLedgerParserHostility:
    @staticmethod
    def _table(row_line: str) -> str:
        return (
            _mod._COST_LEDGER_HEADER_LINE + "\n"
            + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
            + row_line + "\n"
        )

    def test_wrong_column_count_rejected(self):
        with pytest.raises(_mod._CostLedgerParseError, match="expected 10 columns"):
            _mod._parse_cost_ledger_file_text(self._table("| 2026-W20 | m1 | too | few |"))

    def test_non_iso_week_label_rejected(self):
        row = _mod._format_cost_ledger_row(_cost_ledger_row(week="2026-W99"))
        with pytest.raises(_mod._CostLedgerParseError, match="malformed week label"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_non_numeric_percentage_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 12.x% | 1.0% | 1.0% | 0 |  |  |"
        with pytest.raises(_mod._CostLedgerParseError, match="non-numeric context_pct"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_percentage_missing_trailing_percent_sign_rejected(self):
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | not-a-pct | 1.0% | 1.0% | 0 |  |  |"
        with pytest.raises(_mod._CostLedgerParseError, match="malformed context_pct"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_pipe_inside_a_cell_rejected_as_wrong_column_count(self):
        """A raw, unescaped '|' inside note (never a supported escape) splits
        into an extra column, surfacing as the same wrong-column-count error
        as any other malformed row — no separate pipe-detection code path
        is needed."""
        row = "| 2026-W20 | m1 | 2026-08-02 | 1.00 | 1.0% | 1.0% | 1.0% | 0 |  | rolled out | oops |"
        with pytest.raises(_mod._CostLedgerParseError, match="expected 10 columns"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_note_with_ansi_escape_byte_rejected(self):
        """A non-printable-ASCII byte in note (e.g. an ANSI/OSC terminal
        escape sequence) is rejected by the canonical parser itself, not
        only by --record-time validation -- a hand-edited or PR-introduced
        row must be held to the same contract."""
        row = _mod._format_cost_ledger_row(_cost_ledger_row(note="\x1b]0;PWNED\x07\x1b[2J\x1b[H"))
        with pytest.raises(_mod._CostLedgerParseError, match="malformed note"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_note_with_markdown_image_syntax_rejected(self):
        """A markdown image reference in note is rejected by the canonical
        parser -- docs/cost-ledger.md is rendered by GitHub, so an image
        reference would beacon an external server on every view."""
        row = _mod._format_cost_ledger_row(_cost_ledger_row(note="![](https://example.com/t.png)"))
        with pytest.raises(_mod._CostLedgerParseError, match="malformed note"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_note_with_markdown_link_syntax_rejected(self):
        """A plain markdown link (no leading '!') in note is rejected by the
        canonical parser -- docs/cost-ledger.md is rendered by GitHub, so a
        link would beacon an external server on every view just as an image
        reference would."""
        row = _mod._format_cost_ledger_row(_cost_ledger_row(note="[click here](https://example.com/t)"))
        with pytest.raises(_mod._CostLedgerParseError, match="malformed note"):
            _mod._parse_cost_ledger_file_text(self._table(row))

    def test_unresolved_merge_conflict_marker_rejected(self):
        text = (
            _mod._COST_LEDGER_HEADER_LINE + "\n"
            + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
            + "<<<<<<< HEAD\n"
            + _mod._format_cost_ledger_row(_cost_ledger_row(machine="m1")) + "\n"
            + "=======\n"
            + _mod._format_cost_ledger_row(_cost_ledger_row(machine="m2")) + "\n"
            + ">>>>>>> branch\n"
        )
        with pytest.raises(_mod._CostLedgerParseError, match="merge-conflict marker"):
            _mod._parse_cost_ledger_file_text(text)


class TestCostLedgerRecordParity:
    def test_record_row_matches_the_compute_functions_independently(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """--record's row values equal what _compute_cost_trend_data,
        _compute_deny_summary_data, and _compute_reviewer_yield_data compute
        independently for the same week — the parity check that catches
        drift between the recorder and the report subcommands it reuses."""
        proj = fake_projects
        session_id = "sess-parity"
        records = [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
            _hook_deny("require-code-review", ts="2026-06-02T10:00:00.000Z"),
        ]
        records += _reviewer_dispatch_records(
            proj, session_id, "f1", "staff-backend-engineer", "Found 1 issue in src/foo.py needing a fix",
            dispatch_ts="2026-06-01T09:00:00.000Z", result_ts="2026-06-01T09:00:30.000Z",
        )
        records.append(_asst("claude-opus-4-7", ts="2026-06-01T09:05:00.000Z",
                              content=[_edit_use("ef1", path="src/foo.py")]))
        records += _reviewer_dispatch_records(
            proj, session_id, "z1", "staff-backend-engineer", "Found 0 issues in src/other.py after review",
            dispatch_ts="2026-06-01T09:10:00.000Z", result_ts="2026-06-01T09:10:30.000Z",
        )
        records.append(_asst("claude-opus-4-7", ts="2026-06-01T09:15:00.000Z",
                              content=[_edit_use("ez1", path="src/unrelated.py")]))
        _write_jsonl(proj / f"{session_id}.jsonl", records)

        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        capsys.readouterr()

        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        row = rows[0]
        assert row["week"] == "2026-W23"

        cost_iter, _scope = _mod._resolve_project_scope(_cost_ledger_args(), "cost-ledger", include_subagents=True)
        cost_weeks, _u, _t = _mod._compute_cost_trend_data(cost_iter)
        week_data = cost_weeks["2026-W23"]
        assert row["usd"] == pytest.approx(week_data["total"])
        # context_pct (context-class dollar share, GH-554 F1) and ge200k_pct
        # (>=200k-context-bucket dollar share, cost-trend's own existing
        # metric) are distinct fields of _compute_cost_trend_data, asserted
        # independently -- the fixture's only turn is all input tokens (no
        # cache_read/cache_write) but crosses the 200k-context threshold, so
        # a regression that swaps or re-aliases the two would be caught by
        # either assertion failing, not just one.
        assert row["context_pct"] == pytest.approx(
            _mod._pct_value(week_data["context_class_dollars"], week_data["total"])
        )
        assert row["ge200k_pct"] == pytest.approx(_mod._pct_value(week_data["context_over"], week_data["total"]))
        assert row["context_pct"] == pytest.approx(0.0)
        assert row["ge200k_pct"] == pytest.approx(100.0)
        assert row["opus_pct"] == pytest.approx(_mod._pct_value(week_data["opus"], week_data["total"]))

        week_start = _mod.datetime(2026, 6, 1, tzinfo=_mod.UTC).timestamp()
        week_end = week_start + 7 * 86400
        deny_iter, _scope = _mod._resolve_project_scope(_cost_ledger_args(), "cost-ledger")
        deny_data = _mod._compute_deny_summary_data(deny_iter, since_ts=week_start, until_ts=week_end)
        assert row["denials"] == sum(deny_data["hook_counts"].values())
        assert row["denials"] == 1

        reviewer_iter, _scope = _mod._resolve_project_scope(_cost_ledger_args(), "cost-ledger")
        reviewer_data = _mod._compute_reviewer_yield_data(reviewer_iter, since_ts=week_start, until_ts=week_end)
        assert row["reviewer_gap_pp"] == pytest.approx(_mod._reviewer_gap_pp(reviewer_data["agg2"]))
        assert row["reviewer_gap_pp"] == pytest.approx(100.0)  # findings-found 100% edited vs. zero-finding 0%

    def test_denial_at_next_weeks_monday_boundary_excluded_from_this_weeks_row(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """The per-week window is [week_start_ts, week_end_ts) -- a denial
        timestamped exactly at this Monday's 00:00:00 UTC is the window's
        first included instant, while one at the following Monday's own
        00:00:00 UTC belongs to next week and must not inflate this
        week's count."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23
            _hook_deny("require-code-review", ts="2026-06-01T00:00:00.000Z"),  # week_start_ts: included
            _hook_deny("require-code-review", ts="2026-06-08T00:00:00.000Z"),  # week_end_ts: excluded
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        capsys.readouterr()

        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert rows[0]["week"] == "2026-W23"
        assert rows[0]["denials"] == 1


class TestCostLedgerWriteFidelity:
    def test_write_succeeds_for_a_dollar_amount_that_does_not_round_to_a_clean_value(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """Regression test: _write_cost_ledger_file's write-verification step
        must compare the temp file's written bytes against the intended
        text, not re-parsed row dicts against the original row -- usd is
        formatted to cents and percentages to one decimal, so a row's raw
        float legitimately differs from its formatted-then-reparsed value.
        Comparing rows directly would refuse to write almost any real
        (non-round-number) week's figures. 350,000 input tokens at Sonnet
        5's $2/MTok base rate prices to $0.70 (a clean total), but the
        >=200k-bucket dollar share is 100% here — this instead exercises a
        percentage that does not land on a clean one-decimal boundary via a
        second turn that is priced but contributes an uneven opus share."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=333_333, ts="2026-06-01T10:00:00.000Z"),
            _priced_opus([], out=100, ts="2026-06-01T11:00:00.000Z"),
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert 0 < rows[0]["opus_pct"] < 100

    def test_write_to_nonexistent_ledger_path_leaves_mkstemp_default_mode(self, tmp_path):
        """ledger_path not existing yet (the first write against a fresh
        path) must not crash stat()'ing a nonexistent file while preserving
        permissions -- it should leave tempfile.mkstemp's own 0600 default
        in place instead."""
        ledger_path = tmp_path / "cost-ledger.md"
        preamble = _mod._COST_LEDGER_HEADER_LINE + "\n" + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
        _mod._write_cost_ledger_file(ledger_path, preamble, [])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600

    def test_write_to_existing_ledger_path_preserves_its_mode(self, tmp_path):
        """The existing-file case -- chmod to the existing file's own mode
        -- is unaffected by the ledger_path.exists() guard added for the
        nonexistent-path case above."""
        ledger_path = tmp_path / "cost-ledger.md"
        preamble = _mod._COST_LEDGER_HEADER_LINE + "\n" + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
        ledger_path.write_text(preamble)
        ledger_path.chmod(0o640)
        _mod._write_cost_ledger_file(ledger_path, preamble, [])
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o640


class TestCostLedgerAutoCreate:
    def test_record_creates_fresh_file_with_default_preamble_when_none_exists(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """--record against a path with no file yet, but an existing parent
        directory, creates the ledger fresh (default preamble, one row) --
        round-trips through the canonical parser exactly like an
        already-canonical file."""
        ledger_path = tmp_path / "cost-ledger.md"
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: ledger_path)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))

        preamble, rows = _mod._parse_cost_ledger_file_text(ledger_path.read_text())
        assert preamble == _mod._default_cost_ledger_preamble()
        assert len(rows) == 1
        assert rows[0]["week"] == "2026-W23"

    def test_record_creates_missing_parent_directory_too(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """--record against a path whose parent directory also doesn't
        exist yet (a never-before-used $CLAUDE_CONFIG_DIR) must create both
        the directory and the file -- a non-recursive mkdir() would pass
        the previous test while still crashing here."""
        ledger_path = tmp_path / "fresh-config-dir" / "cost-ledger.md"
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: ledger_path)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))

        assert ledger_path.parent.is_dir()
        _preamble, rows = _mod._parse_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1

    def test_record_refused_before_sentinel_check_leaves_no_directory_behind(
        self, fake_projects, tmp_path, monkeypatch
    ):
        """A guard that rejects ahead of the auto-create mkdir (here: the
        missing-sentinel check, the first guard --record hits) must leave
        zero filesystem side effects -- this is the property every other
        guard-rejection test only checks via exit code/stderr, not via the
        directory the mkdir call would have created. A future edit that
        hoisted the mkdir above a guard would pass every other test in this
        file unchanged while still failing this one."""
        ledger_path = tmp_path / "never-created-config-dir" / "cost-ledger.md"
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: ledger_path)
        cfg_dir_no_sentinel = tmp_path / "isolated-claude-config-no-sentinel"
        cfg_dir_no_sentinel.mkdir()
        monkeypatch.setattr(_mod, "config_dir", lambda: cfg_dir_no_sentinel)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert not ledger_path.parent.exists()


class TestCostLedgerRecordIdempotence:
    def test_second_record_without_force_refused_and_file_byte_identical(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_record_with_force_replaces_row_leaving_other_rows_untouched(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        other_row = _cost_ledger_row(week="2026-W23", machine="other1", note="unrelated machine")
        cost_ledger_file.write_text(
            cost_ledger_file.read_text() + _mod._format_cost_ledger_row(other_row) + "\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(
            _cost_ledger_args(record=True, machine_label="tstm1", note="first"), date(2026, 6, 3)
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-02T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(
            _cost_ledger_args(record=True, machine_label="tstm1", force=True, note="second"), date(2026, 6, 3)
        )
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 2
        assert rows[0] == other_row
        tstm1_row = next(r for r in rows if r["machine"] == "tstm1")
        assert tstm1_row["note"] == "second"
        assert tstm1_row["usd"] == pytest.approx(4.0)


class TestCostLedgerDegenerateCorpora:
    def test_empty_corpus_refuses_and_writes_nothing(self, fake_projects, cost_ledger_file, cost_ledger_enabled):
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_current_week_all_turns_unpriced_refuses_and_writes_nothing(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400,
                  ts="2026-06-01T10:00:00.000Z"),  # claude-opus-4-7 is deliberately unpriced
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_clock_skew_between_corpus_and_current_week_refuses(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """The corpus's most recent activity landing in a later week than
        the machine's computed 'today' refuses rather than mislabeling the
        row under the wrong (week, machine) slot."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-15T10:00:00.000Z"),  # 2026-W25
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)  # resolves to 2026-W23
            )
        assert exc_info.value.code != 0


class TestCostLedgerConcurrency:
    def test_two_racing_records_produce_exactly_one_row(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """Two --record calls racing for the same (week, machine) key, run
        concurrently, leave exactly one row: the second acquires the lock
        after the first has already written and committed, sees the
        already-recorded row under the lock, and refuses the duplicate —
        not a double append, not a corrupted table."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        args = _cost_ledger_args(record=True, machine_label="tstm1")
        today = date(2026, 6, 3)
        exit_codes: list[int | None] = [None, None]

        def _run(i: int) -> None:
            try:
                _mod._cost_ledger_report(args, today)
                exit_codes[i] = 0
            except SystemExit as exc:
                exit_codes[i] = exc.code

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1
        assert sorted(exit_codes) == [0, 1]

    def test_two_racing_records_onto_not_yet_existing_parent_directory_produce_exactly_one_row(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """Same race as above, but onto a path whose parent directory
        doesn't exist yet -- the one directory-existence invariant this
        auto-create feature actually changes. Both threads call
        mkdir(parents=True, exist_ok=True) before acquiring the lock;
        Path.mkdir(exist_ok=True) is documented race-safe under concurrent
        creation, and this pins that property for this specific code path
        rather than relying on it being true elsewhere."""
        ledger_path = tmp_path / "fresh-config-dir" / "cost-ledger.md"
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: ledger_path)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        args = _cost_ledger_args(record=True, machine_label="tstm1")
        today = date(2026, 6, 3)
        exit_codes: list[int | None] = [None, None]

        def _run(i: int) -> None:
            try:
                _mod._cost_ledger_report(args, today)
                exit_codes[i] = 0
            except SystemExit as exc:
                exit_codes[i] = exc.code

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _preamble, rows = _mod._parse_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        assert sorted(exit_codes) == [0, 1]


class TestCostLedgerPublishSafety:
    def test_record_output_and_file_carry_no_project_or_session_identifiers(
        self, tmp_path, monkeypatch, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """A distinctive project/session marker present in the scanned
        corpus must not reach the ledger file or stdout — cost-ledger's row
        is aggregate-only by construction (no path/session/project field in
        its schema), mirroring #601's cited-path join test shape."""
        projects = tmp_path / "projects"
        proj = projects / "SENTINEL-PROJECT-marker"
        proj.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(proj / "SENTINEL-SESSION-marker.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        out = capsys.readouterr().out
        assert "SENTINEL-PROJECT-marker" not in out
        assert "SENTINEL-SESSION-marker" not in out
        file_text = cost_ledger_file.read_text()
        assert "SENTINEL-PROJECT-marker" not in file_text
        assert "SENTINEL-SESSION-marker" not in file_text

    def test_machine_label_equal_to_hostname_refused_without_echoing_it(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, monkeypatch, capsys
    ):
        monkeypatch.setattr(_mod.socket, "gethostname", lambda: "realhost")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="realhost"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "hostname" in err
        assert "realhost" not in err

    def test_machine_label_case_insensitive_hostname_comparison_pinned_deterministically(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, monkeypatch, capsys
    ):
        """A fixed, monkeypatched hostname (rather than the ambient real one)
        pins the .lower() comparison itself: a distinct label succeeds, and a
        same-value-different-case label is still rejected."""
        monkeypatch.setattr(_mod.socket, "gethostname", lambda: "RealHost")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        capsys.readouterr()
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="realhost"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "hostname" in err

    def test_note_containing_pipe_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note containing '|' is refused outright, since it would
        corrupt the table's row format on write — exercised here with a
        note shaped like it might carry a private project name, the
        highest-risk column per docs/cost-ledger.md. The recorder does not
        re-implement deny-private-project-refs.sh's own blocklist scan;
        that hook covers the actual publish boundary, `git commit`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1", note="acme-corp | internal rollout"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_note_containing_ansi_escape_byte_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note carrying an ANSI/OSC terminal escape sequence is
        refused outright -- cost-ledger's read mode interpolates note
        unescaped into terminal output, and this is the default (no
        sentinel/opt-in) subcommand."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1", note="\x1b]0;PWNED\x07\x1b[2J\x1b[H"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_note_containing_markdown_image_syntax_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note carrying markdown image syntax is refused outright --
        docs/cost-ledger.md is rendered by GitHub, so an image reference
        would beacon an external server on every view."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1", note="![](https://example.com/t.png)"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_note_containing_markdown_link_syntax_refused_before_any_write(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled
    ):
        """A --note carrying a plain markdown link (no leading '!') is
        refused outright -- docs/cost-ledger.md is rendered by GitHub, so a
        link would beacon an external server on every view."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1", note="[click here](https://example.com/t)"),
                date(2026, 6, 3),
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before


class TestCostLedgerSentinelGate:
    def test_record_refuses_without_sentinel(self, fake_projects, cost_ledger_file, tmp_path, monkeypatch):
        cfg_dir = tmp_path / "isolated-claude-config-no-sentinel"
        cfg_dir.mkdir()
        monkeypatch.setattr(_mod, "config_dir", lambda: cfg_dir)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3))
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_without_machine_label(self, fake_projects, cost_ledger_file, cost_ledger_enabled):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(_cost_ledger_args(record=True), date(2026, 6, 3))
        assert exc_info.value.code != 0

    def test_record_refuses_malformed_machine_label(self, fake_projects, cost_ledger_file, cost_ledger_enabled):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="Too-Long-Label"), date(2026, 6, 3)
            )
        assert exc_info.value.code != 0

    def test_record_refuses_machine_label_with_trailing_newline(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, capsys
    ):
        """Python's `$` (without re.MULTILINE) matches immediately before a
        trailing '\\n' as well as end-of-string, so a naive ^...$ pattern
        would let "tstm1\\n" slip past _MACHINE_LABEL_RE. Asserts the
        rejection happens at this CLI validation boundary itself (its own
        "must match" error text) rather than passing validation and only
        being caught downstream by _write_cost_ledger_file's
        write-verification backstop."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1\n"), date(2026, 6, 3)
            )
        assert exc_info.value.code != 0
        assert cost_ledger_file.read_text() == before
        err = capsys.readouterr().err
        assert "must match" in err

    def test_record_refuses_when_multi_root_and_ledger_path_git_tracked(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """--record refuses when a second account is in scope via the
        declared-roots file AND the resolved ledger path sits inside a git
        working tree, appending no row -- refusing this call shape is what
        keeps a union commit from landing in a path git could commit/push."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert "more than one root is in scope" in capsys.readouterr().err
        assert cost_ledger_file.read_text() == before

    def test_record_succeeds_when_multi_root_and_ledger_path_not_git_tracked(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """The ledger's default path is not git-tracked, so a second
        declared account does not block --record -- only a git-tracked
        destination does (see the git-tracked case above)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod._cost_ledger_report(
            _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
        )
        capsys.readouterr()
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

    def test_record_succeeds_when_multi_root_and_ledger_path_in_bare_repo(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """git rev-parse --is-inside-work-tree exits 0 with stdout "false"
        for a bare repository (tracked by git, but not a work tree) -- pins
        that this is treated the same as "not git-tracked", not misrouted
        into the fail-closed branch."""
        subprocess.run(["git", "init", "-q", "--bare"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod._cost_ledger_report(
            _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
        )
        capsys.readouterr()
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

    def test_record_refuses_when_git_tracked_check_times_out(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A timed-out git-tracked check fails closed (refuses) rather than
        treating a hung check as "not tracked"."""
        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)
        monkeypatch.setattr(_mod.subprocess, "run", _raise_timeout)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_git_tracked_check_binary_missing(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A missing git binary (FileNotFoundError, e.g. git absent from
        PATH) fails closed (refuses) rather than raising past both except
        clauses uncaught."""
        def _raise_not_found(*args, **kwargs):
            raise FileNotFoundError("git")
        monkeypatch.setattr(_mod.subprocess, "run", _raise_not_found)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_git_tracked_check_stderr_has_invalid_utf8(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """Non-UTF-8 bytes on the git-tracked check's stderr (e.g. a
        non-ASCII ancestor path in a permission-denied message) decode via
        errors="replace" rather than raising UnicodeDecodeError uncaught,
        which would otherwise crash --record instead of failing closed. A
        fake `git` on PATH emits invalid UTF-8 so this doesn't depend on the
        host's locale or filesystem permission semantics."""
        fake_bin = tmp_path / "fake-git-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text("#!/bin/sh\nprintf '\\377\\376 permission denied\\n' >&2\nexit 128\n")
        fake_git.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_git_tracked_check_exits_nonzero_unexpectedly(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A non-zero git exit whose stderr doesn't match the expected "not
        a git repository" text fails closed -- the branch most likely to
        silently flip if that stderr text ever changes."""
        def _fake_permission_denied(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: permission denied\n")
        monkeypatch.setattr(_mod.subprocess, "run", _fake_permission_denied)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_refuses_when_multi_root_and_ledger_path_in_linked_worktree(
        self, fake_projects, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """A linked worktree's directory contains a `.git` file (a worktree
        pointer), not a `.git` directory -- this repo's own convention
        (.claude/worktrees/<branch>/) makes that the dominant real-world
        layout, and no other case here exercises it."""
        main_repo = tmp_path / "main-repo"
        subprocess.run(["git", "init", "-q", str(main_repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=main_repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=main_repo, check=True)
        (main_repo / "README.md").write_text("x\n")
        subprocess.run(["git", "add", "README.md"], cwd=main_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=main_repo, check=True)
        worktree_dir = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(worktree_dir), "-b", "wt-branch"],
            cwd=main_repo, check=True,
        )
        ledger_path = worktree_dir / "cost-ledger.md"
        ledger_path.write_text(
            "# Cost-trend ledger\n\n"
            + _mod._COST_LEDGER_HEADER_LINE + "\n"
            + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
        )
        monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: ledger_path)

        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = ledger_path.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert ledger_path.read_text() == before

    def test_record_refuses_when_multi_root_and_ledger_path_git_tracked_even_with_force(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch, capsys
    ):
        """Regression: --force skips the duplicate-(week, machine)-row check
        in _upsert_cost_ledger_row, not the multi-root git-tracked refusal
        above it -- pins that ordering against a future change that
        special-cases --force to bypass this guard too."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        before = cost_ledger_file.read_text()
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_ledger_report(
                _cost_ledger_args(record=True, machine_label="tstm1", force=True), date(2026, 6, 3)
            )
        assert exc_info.value.code == 2
        assert cost_ledger_file.read_text() == before

    def test_record_succeeds_when_single_root_and_ledger_path_git_tracked(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path
    ):
        """The git-tracked check only runs when more than one root is in
        scope -- a single declared account still succeeds against a
        git-tracked ledger path, pinning that boundary against a future
        edit to the `len(roots) > 1 and ...` guard."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod._cost_ledger_report(
            _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
        )
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1

    def test_record_not_redirected_by_inherited_git_dir_env(
        self, fake_projects, cost_ledger_file, cost_ledger_enabled, tmp_path, monkeypatch
    ):
        """An operator's shell exporting GIT_DIR/GIT_WORK_TREE for an
        unrelated repo must not redirect the git-tracked check to that
        repo's tracked status -- the check has to see the ledger path's own
        (untracked) ancestor, not whatever the caller's env points at.
        GIT_WORK_TREE is set to the ledger's own ancestor (not a sibling
        directory) specifically so an unstripped env would answer "true"
        (wrongly tracked) while the stripped env correctly answers "false" --
        a sibling GIT_WORK_TREE answers "false" either way and wouldn't
        discriminate the two behaviors."""
        unrelated_repo = tmp_path / "unrelated-repo"
        unrelated_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=unrelated_repo, check=True)
        monkeypatch.setenv("GIT_DIR", str(unrelated_repo / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = tmp_path / "acct-b"
        (acct_b / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{acct_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod._cost_ledger_report(
            _cost_ledger_args(record=True, machine_label="tstm1"), date(2026, 6, 3)
        )
        _preamble, rows = _mod._parse_cost_ledger_file_text(cost_ledger_file.read_text())
        assert len(rows) == 1


class TestCostLedgerDefaultPathCliWiring:
    def test_cmd_cost_ledger_record_lands_at_config_dir_default_path(self, monkeypatch, tmp_path):
        """cmd_cost_ledger's own dispatch wiring -- not just a direct
        _cost_ledger_report() call -- resolves the ledger's default
        location through config_dir() when COST_LEDGER_PATH is unset.
        cmd_cost_ledger reads datetime.now(UTC) itself with no override
        parameter, so "today" is pinned via a real datetime subclass (not a
        bare stub, so the datetime(...) constructor calls inside
        _cost_ledger_report keep working) instead of depending on the real
        wall clock."""
        monkeypatch.delenv("COST_LEDGER_PATH", raising=False)
        cfg_dir = tmp_path / "fresh-claude-config"
        cfg_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
        (cfg_dir / ".cost-ledger-enabled").touch()
        expected_path = cfg_dir / "cost-ledger.md"
        expected_path.write_text(
            _mod._COST_LEDGER_HEADER_LINE + "\n" + _mod._COST_LEDGER_SEPARATOR_LINE + "\n"
        )

        projects = tmp_path / "projects"
        proj = projects / "-home-user-testrepo"
        proj.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(proj / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 6, 3, 12, 0, tzinfo=tz)

        monkeypatch.setattr(_mod, "datetime", _FixedDatetime)

        _mod.cmd_cost_ledger(_cost_ledger_args(record=True, machine_label="tstm1"))

        assert _mod._cost_ledger_path() == expected_path
        _preamble, rows = _mod._parse_cost_ledger_file_text(expected_path.read_text())
        assert len(rows) == 1
        assert rows[0]["week"] == "2026-W23"


# ---------------------------------------------------------------------------
# handoff-ratio
# ---------------------------------------------------------------------------


def _handoff_skill_block() -> dict:
    """Build a Skill tool_use block for /handoff."""
    return {"type": "tool_use", "id": "tool-handoff-1", "name": "Skill", "input": {"skill": "handoff"}}


def _compaction_record(ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    """Build a compact_boundary system record (the shape Claude Code writes on auto-compaction)."""
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
        "timestamp": ts,
        "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 11000, "durationMs": 145000},
    }


def _handoff_args(since: str | None = None, projects: str = "*") -> argparse.Namespace:
    return type("A", (), {
        "projects": projects, "this_repo": False, "since": since, "debug_detector": False,
    })()


class TestHandoffRatio:
    def test_handoff_ratio_counts_handoffs_and_compactions(self, fake_projects, capsys):
        """Sessions with handoff skill calls and compaction events are counted correctly."""
        # Session 1: has handoff
        _write_jsonl(fake_projects / "sess1.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-11T09:00:00.000Z",
                  content=[_handoff_skill_block()]),
        ])
        # Session 2: has compaction
        _write_jsonl(fake_projects / "sess2.jsonl", [
            _compaction_record("2026-05-11T10:00:00.000Z"),
        ])
        # Session 3: both handoff and compaction (counts in both columns)
        _write_jsonl(fake_projects / "sess3.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-11T10:00:00.000Z",
                  content=[_handoff_skill_block()]),
            _compaction_record("2026-05-11T11:00:00.000Z"),
        ])
        _mod.cmd_handoff_ratio(_handoff_args())
        out = capsys.readouterr().out
        assert "Handoffs" in out
        assert "Compactions" in out
        # Totals row: 2 handoffs, 2 compactions, 50% ratio.
        cols = _table_cols(out, header_contains="Handoffs", row_contains="Total")
        assert int(cols["Handoffs"]) == 2, "Expected 2 handoffs in Total row"
        assert int(cols["Compactions"]) == 2, "Expected 2 compactions in Total row"
        assert "50.0%" in out

    def test_handoff_ratio_empty_corpus_no_divide_by_zero(self, fake_projects, capsys):
        """Empty corpus (no handoffs or compactions) prints a graceful 'no data' message."""
        # Write a session with no handoff or compaction records.
        _write_jsonl(fake_projects / "plain.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod.cmd_handoff_ratio(_handoff_args())
        out = capsys.readouterr().out
        assert "No handoff or compaction events found" in out

    def test_handoff_ratio_since_filter_excludes_older(self, fake_projects, capsys):
        """Events with timestamps before --since are excluded from counts."""
        # Old session: before cutoff
        _write_jsonl(fake_projects / "old.jsonl", [
            _compaction_record("2026-01-15T10:00:00.000Z"),
        ])
        # New session: after cutoff
        _write_jsonl(fake_projects / "new.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_handoff_skill_block()]),
        ])
        _mod.cmd_handoff_ratio(_handoff_args(since="2026-05-01"))
        out = capsys.readouterr().out
        # Only the new session (handoff) should appear; old compaction excluded.
        assert "Handoffs" in out
        cols = _table_cols(out, header_contains="Handoffs", row_contains="Total")
        assert int(cols["Handoffs"]) == 1, "Expected 1 handoff in Total row"
        assert int(cols["Compactions"]) == 0, "Expected 0 compactions in Total row"


# ---------------------------------------------------------------------------
# audit-routing-shape
# ---------------------------------------------------------------------------


def _read_use(tool_id: str, file_path: str) -> dict:
    """Build a Read tool_use block with the given file_path."""
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": file_path}}


def _grep_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Grep", "input": {"pattern": "x", "path": "."}}


def _glob_use(tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Glob", "input": {"pattern": "**/*.py", "path": "."}}


def _audit_routing_shape_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
    })()


def _extract_shape_d1(out: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D1 bucket from audit-routing-shape output."""
    in_d1 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D1" in stripped and "Files Read" in stripped:
            in_d1 = True
            continue
        if in_d1 and stripped.startswith("###"):
            break
        if in_d1 and "Bucket" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_d1 and stripped.startswith(bucket):
            parts = stripped.split()
            # Anchor Turns and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            turns_idx = header_line.split().index("Turns") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else turns_idx + 1
            if len(parts) > out_idx:
                return int(parts[turns_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d2(out: str, bucket: str) -> tuple[int, int]:
    """Parse (streak_count, output_tokens) for a D2 bucket from audit-routing-shape output."""
    in_d2 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D2" in stripped and "streak" in stripped.lower():
            in_d2 = True
            continue
        if in_d2 and stripped.startswith("###"):
            break
        if in_d2 and "Bucket" in stripped and "Streaks" in stripped:
            header_line = stripped
            continue
        if in_d2 and stripped.startswith(bucket):
            parts = stripped.split()
            # Anchor Streaks and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            streaks_idx = header_line.split().index("Streaks") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else streaks_idx + 1
            if len(parts) > out_idx:
                return int(parts[streaks_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d3(out: str, case: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 case from audit-routing-shape output."""
    in_d3 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D3" in stripped and "Read-then-edit" in stripped:
            in_d3 = True
            continue
        if in_d3 and stripped.startswith("###"):
            break
        if in_d3 and stripped.startswith("####"):
            break
        if in_d3 and "Case" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_d3 and stripped.startswith(case):
            parts = stripped.split()
            # Anchor Turns and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            turns_idx = header_line.split().index("Turns") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else turns_idx + 1
            if len(parts) > out_idx:
                return int(parts[turns_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _extract_shape_d3_xtab(out: str, case: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D3 × D1 cross-tab cell."""
    in_xtab = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D3 × D1 cross-tab" in stripped or "D3 x D1 cross-tab" in stripped.lower():
            in_xtab = True
            continue
        if in_xtab and (stripped.startswith("###") or stripped.startswith("Dispatchable")):
            break
        if in_xtab and "Case" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_xtab and stripped.startswith(case) and bucket in stripped:
            parts = stripped.split()
            # "D1 bucket" is a 2-token header name but maps to 1 data token (e.g. "2-3").
            # header.split().index("Turns") = 3, but the data-row Turns value is at position 2;
            # subtract 1 to correct for the header's extra token from the multi-word column.
            turns_idx = header_line.split().index("Turns") - 1 if header_line else 2
            if len(parts) > turns_idx + 1:
                return int(parts[turns_idx].replace(",", "")), int(parts[turns_idx + 1].replace(",", ""))
    return 0, 0


class TestAuditRoutingShape:
    def test_d1_buckets(self, fake_projects, capsys):
        """One turn per D1 bucket (0, 1, 2, 5, 9 Reads); each bucket has turn count 1 and correct tokens."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # 0 Reads → bucket "0"
            _opus([_bash_use("b1", "ls")], out=10),
            # 1 Read → bucket "1"
            _opus([_read_use("r1", "/a.txt")], out=20),
            # 2 Reads → bucket "2-3"
            _opus([_read_use("r2", "/a.txt"), _read_use("r3", "/b.txt")], out=30),
            # 5 Reads → bucket "4-7"
            _opus([_read_use(f"r{i}", f"/f{i}.txt") for i in range(10, 15)], out=40),
            # 9 Reads → bucket "8+"
            _opus([_read_use(f"r{i}", f"/f{i}.txt") for i in range(20, 29)], out=50),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert _extract_shape_d1(out, "0") == (1, 10)
        assert _extract_shape_d1(out, "1") == (1, 20)
        assert _extract_shape_d1(out, "2-3") == (1, 30)
        assert _extract_shape_d1(out, "4-7") == (1, 40)
        assert _extract_shape_d1(out, "8+") == (1, 50)

    def test_d1_excludes_grep_glob_bash(self, fake_projects, capsys):
        """Turns with only Grep or Glob tool_use land in bucket '0' (neither counts as a Read)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_grep_use("g1")], out=77),
            _opus([_glob_use("gl1")], out=88),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns_0, tokens_0 = _extract_shape_d1(out, "0")
        assert turns_0 == 2
        assert tokens_0 == 165  # 77 + 88
        # bucket "1" must be empty
        assert _extract_shape_d1(out, "1") == (0, 0)

    def test_d2_streak_1(self, fake_projects, capsys):
        """Two sessions each with one code-read turn surrounded by non-code-read turns produce
        two distinct streaks of length 1; bucket '1' streak count = 2."""
        # Session A: code-write, code-read, code-write → streak of length 1
        _write_jsonl(fake_projects / "sess_a.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
            _opus([_read_use("r1", "/a.txt")], out=50),
            _opus([{"type": "tool_use", "id": "e2", "name": "Edit", "input": {}}], out=100),
        ])
        # Session B: code-write, code-read, code-write → streak of length 1
        _write_jsonl(fake_projects / "sess_b.jsonl", [
            _opus([{"type": "tool_use", "id": "e3", "name": "Edit", "input": {}}], out=100),
            _opus([_read_use("r2", "/b.txt")], out=60),
            _opus([{"type": "tool_use", "id": "e4", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "1")
        assert streak_count == 2
        assert streak_out == 110  # 50 + 60

    def test_d2_streak_2(self, fake_projects, capsys):
        """Two consecutive code-read turns followed by a non-code-read: one streak, bucket '2', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=30),
            _opus([_read_use("r2", "/b.txt")], out=40),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "2")
        assert streak_count == 1
        assert streak_out == 70  # 30 + 40

    def test_d2_streak_3_5(self, fake_projects, capsys):
        """A session with 4 consecutive code-read turns: one streak, bucket '3-5', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=25) for i in range(4)
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "3-5")
        assert streak_count == 1
        assert streak_out == 100  # 4 × 25

    def test_d2_streak_6_10(self, fake_projects, capsys):
        """Seven consecutive code-read turns: one streak, bucket '6-10', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=10) for i in range(7)
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "6-10")
        assert streak_count == 1
        assert streak_out == 70  # 7 × 10

    def test_d2_streak_11_plus(self, fake_projects, capsys):
        """Twelve consecutive code-read turns: one streak, bucket '11+', count = 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.txt")], out=10) for i in range(12)
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "11+")
        assert streak_count == 1
        assert streak_out == 120  # 12 × 10

    def test_d2_streak_split_by_user(self, fake_projects, capsys):
        """A user turn between two code-read turns splits them into two streaks of length 1.
        Specifically, [r1, user-turn, r2] must NOT be treated as one streak of 2."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=10),
            _user_msg("continue", branch="main"),
            _opus([_read_use("r2", "/b.txt")], out=10),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        # User turn breaks the streak — two separate streaks of length 1, not one streak of 2
        streak_1_count, _ = _extract_shape_d2(out, "1")
        streak_2_count, _ = _extract_shape_d2(out, "2")
        streak_35_count, _ = _extract_shape_d2(out, "3-5")
        assert streak_1_count == 2
        assert streak_2_count == 0
        assert streak_35_count == 0

    def test_d3_inline_edit(self, fake_projects, capsys):
        """A code-read turn followed within 3 turns by a code-write with no orchestration → case inline-edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100

    def test_d3_dispatched(self, fake_projects, capsys):
        """A code-read turn followed within 3 turns by an orchestration turn → case dispatched."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "dispatched")
        assert turns == 1
        assert tokens == 100

    def test_d3_neither(self, fake_projects, capsys):
        """A code-read turn with no code-write or orchestration within 3 turns → case neither."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            # Three non-code-write, non-orchestration turns
            _opus([], out=10),
            _opus([], out=10),
            _opus([], out=10),
            # code-write beyond the 3-turn window — must not affect classification
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "neither")
        assert turns == 1
        assert tokens == 100
        # inline-edit must be 0 turns (the edit was outside the 3-turn window)
        assert _extract_shape_d3(out, "inline-edit") == (0, 0)

    def test_d3_inline_edit_at_window_boundary(self, fake_projects, capsys):
        """A code-write at exactly lookahead position 3 (the last inclusive position) is inline-edit."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([], out=10),  # lookahead 1: other
            _opus([], out=10),  # lookahead 2: other
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),  # lookahead 3
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100
        assert _extract_shape_d3(out, "neither") == (0, 0)

    def test_d3_inline_edit_across_user_turn(self, fake_projects, capsys):
        """A code-read turn followed by a user turn then a code-write within 3 Opus turns
        must still classify as inline-edit — user turns don't consume lookahead budget."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _user_msg("continue", branch="main"),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3(out, "inline-edit")
        assert turns == 1
        assert tokens == 100
        assert _extract_shape_d3(out, "neither") == (0, 0)

    def test_d3_cross_tab_file_bucket(self, fake_projects, capsys):
        """A code-read turn with 2 Reads followed by an inline edit appears in cross-tab inline-edit × 2-3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt"), _read_use("r2", "/b.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        turns, tokens = _extract_shape_d3_xtab(out, "inline-edit", "2-3")
        assert turns == 1
        assert tokens == 100

    def test_d2_and_d3_streak_then_edit(self, fake_projects, capsys):
        """Three consecutive code-read turns followed by a code-write: D2 sees one streak in
        '3-5' AND D3 sees all three turns as inline-edit (the edit is within 3 Opus turns of each)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        streak_count, streak_out = _extract_shape_d2(out, "3-5")
        assert streak_count == 1
        assert streak_out == 300  # 3 × 100
        d3_turns, d3_out = _extract_shape_d3(out, "inline-edit")
        assert d3_turns == 3  # all three code-read turns see the edit within 3 Opus turns
        assert d3_out == 300

    def test_cross_validation_with_audit_routing(self, fake_projects, capsys):
        """D1 total output tokens across all buckets must equal audit-routing's corpus code-read total.
        This guards against drift between the duplicated judgment-span state machines.
        A Read turn inside a judgment span (99 tokens) must be excluded from both totals."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Skill invocation opens a judgment span; turn itself → judgment (50 tokens)
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span → judgment, NOT code-read (99 tokens — must not appear in D1)
            _opus([_read_use("rx", "/span-file.txt")], out=99),
            # User turn resets span
            _user_msg("done", branch="main"),
            # 3 code-read turns outside span (100 each) + 1 code-write turn (200)
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=200),
        ])
        # Run audit-routing and capture code-read corpus total
        _mod.cmd_audit_routing(_audit_routing_args())
        routing_out = capsys.readouterr().out
        routing_code_read = _extract_corpus_class_tokens(routing_out, "code-read")

        # Run audit-routing-shape and sum D1 output tokens across all buckets
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        shape_out = capsys.readouterr().out
        d1_total = sum(_extract_shape_d1(shape_out, bkt)[1] for bkt in _mod._D1_BUCKETS)

        assert d1_total == routing_code_read  # both should be 300 (judgment-span Read excluded from both)

    def test_judgment_span_excluded_from_distributions(self, fake_projects, capsys):
        """A Read turn inside a judgment span must not appear in D1, D2, or D3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Open judgment span
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span — must be excluded from all distributions
            _opus([_read_use("r1", "/a.txt")], out=99),
            # User turn closes span
            _user_msg("done", branch="main"),
            # Read after span closed — must appear in D1
            _opus([_read_use("r2", "/b.txt")], out=77),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        # Only the post-span Read (77 tokens, 1 Read → bucket "1") should appear
        turns_1, tokens_1 = _extract_shape_d1(out, "1")
        assert turns_1 == 1
        assert tokens_1 == 77
        # Total D1 output tokens across all buckets must be 77 (not 176)
        d1_total = sum(_extract_shape_d1(out, bkt)[1] for bkt in _mod._D1_BUCKETS)
        assert d1_total == 77

    def test_dispatchable_share_summary_value(self, fake_projects, capsys):
        """Dispatchable-share percentage is computed correctly for a controlled fixture.
        4 code-read turns: A(D1=2-3, D3=inline-edit), B(D1=1, D3=inline-edit),
        C(D1=1, D3=dispatched), D(D1=1, D3=neither). Tokens: 100+200+300+400=1000.
        Dispatchable via D1∪D3: A+C+D = 100+300+400=800 → 80%."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Turn A: 2 Reads → D1="2-3" (dispatchable). Followed by inline-edit.
            _opus([_read_use("a1", "/a1.txt"), _read_use("a2", "/a2.txt")], out=100),
            _opus([{"type": "tool_use", "id": "ea", "name": "Edit", "input": {}}], out=50),
            # Turn B: 1 Read → D1="1". Followed by inline-edit within 3 turns.
            _opus([_read_use("b1", "/b1.txt")], out=200),
            _opus([{"type": "tool_use", "id": "eb", "name": "Edit", "input": {}}], out=50),
            # Turn C: 1 Read → D1="1". Followed by orchestration.
            _opus([_read_use("c1", "/c1.txt")], out=300),
            _opus([_agent_use("ag1", "code-writer")], out=50),
            # Turn D: 1 Read → D1="1". No code-write or orchestration within 3 turns.
            _opus([_read_use("d1", "/d1.txt")], out=400),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert "Dispatchable share: 80%" in out

    def test_dispatchable_share_empty_corpus(self, fake_projects, capsys):
        """No code-read turns → dispatchable share prints '—'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "e1", "name": "Edit", "input": {}}], out=100),
        ])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert "Dispatchable share: —" in out

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        """A malformed --since value fails closed with the audit-routing-shape-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod.cmd_audit_routing_shape(_audit_routing_shape_args(since="not-a-window"))
        assert "audit-routing-shape: --since: expected Nd like '35d'" in capsys.readouterr().err

    def test_request_id_group_reads_split_across_records_bucketed_by_union(self, fake_projects, capsys):
        """A requestId group whose 2 Read tool_use blocks land on separate
        raw records is bucketed as one code-read turn with D1='2-3' (the
        union of both blocks), not as two separate D1='1' turns — dedup
        merges the group's content before _count_read_file_paths sees it."""
        rec_a = _opus([_read_use("r1", "/a.txt")], out=20, request_id="req-1")
        rec_b = _opus([_read_use("r2", "/b.txt")], out=20, request_id="req-1")
        _write_jsonl(fake_projects / "sess.jsonl", [rec_a, rec_b])
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        out = capsys.readouterr().out
        assert _extract_shape_d1(out, "2-3") == (1, 20)
        assert _extract_shape_d1(out, "1") == (0, 0)


# ---------------------------------------------------------------------------
# audit-routing-samples
# ---------------------------------------------------------------------------


def _audit_routing_samples_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    sample: int = 100,
    seed: int | None = 42,
    output_format: str = "json",
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "sample": sample,
        "seed": seed,
        "output_format": output_format,
    })()


class TestAuditRoutingSamples:
    def test_code_read_turn_emitted(self, fake_projects, capsys):
        """A single code-read turn produces one record; verify session_id, turn_index,
        and assistant_tool_call fields."""
        _write_jsonl(fake_projects / "abc12345-test.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        rec = records[0]
        assert rec["session_id"] == "abc12345-test"
        assert rec["turn_index"] == 0
        assert rec["assistant_tool_call"] == {"name": "Read", "input": {"file_path": "/foo/bar.py"}}

    def test_prior_user_message_captured(self, fake_projects, capsys):
        """A user message immediately before the code-read turn is captured in prior_user_message."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "Please read the file"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["prior_user_message"] == "Please read the file"

    def test_prior_user_message_empty_when_none(self, fake_projects, capsys):
        """No preceding user turn → prior_user_message is empty string."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["prior_user_message"] == ""

    def test_next_action_edit(self, fake_projects, capsys):
        """Next Opus turn is Edit (code-write) → next_assistant_action is 'edit'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_edit_use("e1")], out=200),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "edit"

    def test_next_action_dispatch(self, fake_projects, capsys):
        """Next Opus turn spawns an Agent (orchestration) → next_assistant_action is 'dispatch'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "dispatch"

    def test_next_action_another_read(self, fake_projects, capsys):
        """Next Opus turn is another Read (code-read) → next_assistant_action is 'another-read'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_read_use("r2", "/b.py")], out=150),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        # Two code-read turns produce two records; only check the first turn here
        first = next(r for r in records if r["turn_index"] == 0)
        assert first["next_assistant_action"] == "another-read"

    def test_next_action_respond_to_user(self, fake_projects, capsys):
        """Next Opus turn has no tool_use (text-only 'other' class) → next_assistant_action is 'respond-to-user'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": "Here is what I found"}], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "respond-to-user"

    def test_next_action_other_when_no_next(self, fake_projects, capsys):
        """Code-read is the last turn in the session → next_assistant_action is 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "other"

    def test_next_turn_excerpt_populated(self, fake_projects, capsys):
        """next_turn_excerpt contains text from the next Opus turn."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": "I read the file and found the answer"}], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert "I read the file" in records[0]["next_turn_excerpt"]

    def test_sample_limit_respected(self, fake_projects, capsys):
        """10 eligible turns with sample=5 → exactly 5 records returned."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(10)
        ])
        args = _audit_routing_samples_args(sample=5, seed=0)
        _mod.cmd_audit_routing_samples(args)
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 5

    def test_seed_produces_deterministic_output(self, fake_projects, capsys):
        """Same seed on the same corpus produces the same turn selection."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(20)
        ])
        args_a = _audit_routing_samples_args(sample=5, seed=7)
        _mod.cmd_audit_routing_samples(args_a)
        records_a = json.loads(capsys.readouterr().out)

        args_b = _audit_routing_samples_args(sample=5, seed=7)
        _mod.cmd_audit_routing_samples(args_b)
        records_b = json.loads(capsys.readouterr().out)

        assert [r["turn_index"] for r in records_a] == [r["turn_index"] for r in records_b]

    def test_judgment_span_excluded(self, fake_projects, capsys):
        """Read turns inside a judgment span (opened by a judgment skill) are NOT included."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Opens judgment span; turn itself → judgment
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span — must be excluded
            _opus([_read_use("r1", "/span-file.py")], out=99),
            # User turn closes span
            _user_msg([{"type": "text", "text": "continue"}]),
            # Read after span closed — must be included
            _opus([_read_use("r2", "/normal.py")], out=77),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        # Only the post-span Read should appear
        assert len(records) == 1
        assert records[0]["assistant_tool_call"]["input"]["file_path"] == "/normal.py"

    def test_since_filter_excludes_old_turns(self, fake_projects, capsys):
        """Turns with timestamps older than --since Nd are excluded."""
        old_ts = "2020-01-01T00:00:00.000Z"
        new_ts = "2099-12-31T00:00:00.000Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/old.py")], out=100, ts=old_ts),
            _opus([_read_use("r2", "/new.py")], out=100, ts=new_ts),
        ])
        args = _audit_routing_samples_args(since="1d")
        _mod.cmd_audit_routing_samples(args)
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["assistant_tool_call"]["input"]["file_path"] == "/new.py"

    def test_non_code_read_turns_not_in_output(self, fake_projects, capsys):
        """code-write, orchestration, and other turns produce no records."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_use("e1")], out=100),
            _opus([_agent_use("a1", "code-writer")], out=200),
            _opus([], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 0

    def test_cross_validation_with_audit_routing(self, fake_projects, capsys):
        """The count of code-read samples (with sample large enough) equals the D1 total turn
        count from cmd_audit_routing_shape for the same fixture.

        This guards against state-machine drift between the two subcommands. A Read turn
        inside a judgment span (99 tokens) must be excluded from both counts."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Skill invocation opens a judgment span; turn itself → judgment (50 tokens)
            _opus([_skill_use("s1", "code-review")], out=50),
            # Read inside judgment span → excluded from both subcommands
            _opus([_read_use("rx", "/span-file.txt")], out=99),
            # User turn resets span
            _user_msg("done"),
            # 3 code-read turns outside span + 1 code-write
            _opus([_read_use("r1", "/a.txt")], out=100),
            _opus([_read_use("r2", "/b.txt")], out=100),
            _opus([_read_use("r3", "/c.txt")], out=100),
            _opus([_edit_use("e1")], out=200),
        ])

        # Run audit-routing-shape: sum D1 turn counts across all buckets
        _mod.cmd_audit_routing_shape(_audit_routing_shape_args())
        shape_out = capsys.readouterr().out
        d1_total_turns = sum(_extract_shape_d1(shape_out, bkt)[0] for bkt in _mod._D1_BUCKETS)

        # Run audit-routing-samples with a large sample to capture all eligible turns
        args = _audit_routing_samples_args(sample=1000, seed=0)
        _mod.cmd_audit_routing_samples(args)
        samples = json.loads(capsys.readouterr().out)

        assert len(samples) == d1_total_turns  # both should be 3

    def test_user_turn_skipped_in_forward_scan(self, fake_projects, capsys):
        """A user turn between a code-read and a code-write is skipped in the forward scan;
        next_assistant_action must still be 'edit' not 'other'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _user_msg([{"type": "text", "text": "looks good"}]),
            _opus([_edit_use("e1")], out=200),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["next_assistant_action"] == "edit"

    def test_prior_user_message_nonadjacent(self, fake_projects, capsys):
        """The backward walk finds the most recent user turn even with intervening code-read turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "investigate this"}]),
            _opus([_read_use("r1", "/first.py")], out=100),
            _opus([_read_use("r2", "/second.py")], out=100),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 2
        # Both reads should trace back to the same user message
        assert records[0]["prior_user_message"] == "investigate this"
        assert records[1]["prior_user_message"] == "investigate this"

    def test_next_turn_excerpt_truncated_at_200(self, fake_projects, capsys):
        """next_turn_excerpt is truncated to at most 200 characters."""
        long_text = "x" * 400
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([{"type": "text", "text": long_text}], out=50),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert len(records[0]["next_turn_excerpt"]) == 200

    def test_different_seed_produces_different_output(self, fake_projects, capsys):
        """Different seeds on the same corpus produce different selections (shuffle is live)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use(f"r{i}", f"/f{i}.py")], out=100) for i in range(20)
        ])
        args_a = _audit_routing_samples_args(sample=5, seed=1)
        _mod.cmd_audit_routing_samples(args_a)
        records_a = json.loads(capsys.readouterr().out)

        args_b = _audit_routing_samples_args(sample=5, seed=99)
        _mod.cmd_audit_routing_samples(args_b)
        records_b = json.loads(capsys.readouterr().out)

        assert [r["turn_index"] for r in records_a] != [r["turn_index"] for r in records_b]

    def test_format_md_emits_markdown_document(self, fake_projects, capsys):
        """--format md produces a markdown document, not a JSON array."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert out.startswith("# audit-routing-samples curation")

    def test_format_md_one_section_per_record(self, fake_projects, capsys):
        """--format md produces exactly one ## section header per sampled record."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/a.py")], out=100),
            _opus([_read_use("r2", "/b.py")], out=100),
            _opus([_read_use("r3", "/c.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md", sample=10)
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Count lines that start with '## ' (section headers, not the h1)
        section_headers = [line for line in out.splitlines() if line.startswith("## ")]
        assert len(section_headers) == 3

    def test_format_md_renders_read_tool_call(self, fake_projects, capsys):
        """--format md renders a Read tool call as **Read:** `<file_path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "src/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Read:** `src/foo/bar.py`" in out

    def test_format_md_renders_grep_tool_call(self, fake_projects, capsys):
        """--format md renders a Grep tool call as **Grep:** `<pattern>` in `<path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "g1", "name": "Grep",
                    "input": {"pattern": "render_widget", "path": "src/"}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Grep:** `render_widget` in `src/`" in out

    def test_format_md_renders_bash_tool_call_truncated(self, fake_projects, capsys):
        """--format md truncates a Bash command longer than 80 chars to exactly 80 chars + '…'."""
        long_command = "a" * 100
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash",
                    "input": {"command": long_command}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Bash:**" in out
        # Extract the backtick-delimited command portion
        match = re.search(r"\*\*Bash:\*\* `([^`]*)`", out)
        assert match is not None
        rendered_command = match.group(1)
        assert rendered_command == "a" * _mod.render._BASH_COMMAND_DISPLAY_CHARS + "…"

    def test_format_md_fallback_for_unknown_tool(self):
        """_pretty_tool_call renders an unrecognised tool name using the **<Name>:** fallback."""
        # SomeOtherTool is not in _CODE_READ_TOOLS, so a turn using only it would never
        # be classified as code-read and would not reach _pretty_tool_call via the full
        # pipeline.  Testing the helper directly exercises the fallback rendering path.
        rendered = _mod.render._pretty_tool_call({"name": "SomeOtherTool", "input": {"x": 1}})
        assert "**SomeOtherTool:**" in rendered

    def test_format_md_blockquotes_multiline_user_message(self, fake_projects, capsys):
        """--format md blockquotes each line of a multiline prior user message."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "line one\nline two"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "> line one" in out
        assert "> line two" in out

    def test_format_json_unchanged_by_default(self, fake_projects, capsys):
        """Default output (no --format flag) is a JSON array starting with '['."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args()  # output_format defaults to "json"
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert out.strip().startswith("[")

    def test_format_md_renders_glob_tool_call(self, fake_projects, capsys):
        """--format md renders a Glob tool call as **Glob:** `<pattern>` in `<path>`."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "gl1", "name": "Glob",
                    "input": {"pattern": "**/*.ts", "path": "src/"}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Glob:** `**/*.ts` in `src/`" in out

    def test_format_md_renders_bash_tool_call_short(self, fake_projects, capsys):
        """--format md renders a short Bash command without truncation or ellipsis."""
        short_command = "git status"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "tool_use", "id": "b2", "name": "Bash",
                    "input": {"command": short_command}}], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert f"**Bash:** `{short_command}`" in out
        assert "…" not in out

    def test_format_md_blockquotes_blank_lines_in_user_message(self, fake_projects, capsys):
        """--format md preserves blank-line continuity: blank lines render as '>' not bare empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _user_msg([{"type": "text", "text": "para one\n\npara two"}]),
            _opus([_read_use("r1", "/a.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        lines = out.splitlines()
        # Find the blockquoted user section; the blank line between paragraphs must be ">"
        # (not an empty string), so blockquote continuity is preserved in markdown renderers.
        assert ">" in lines   # at least one plain ">" line exists
        # Verify the blank line between the two paragraphs is ">" not ""
        # by finding the two paragraph lines and checking what's between them
        para1_idx = next(i for i, ln in enumerate(lines) if ln == "> para one")
        para2_idx = next(i for i, ln in enumerate(lines) if ln == "> para two")
        for between_line in lines[para1_idx + 1:para2_idx]:
            assert between_line == ">"

    def test_pretty_tool_call_bash_with_description(self):
        """_pretty_tool_call renders Bash with description as 'description — `cmd`'."""
        result = _mod.render._pretty_tool_call({
            "name": "Bash",
            "input": {"command": "grep -rn foo bar/", "description": "Find foo"},
        })
        assert result == "**Bash:** Find foo — `grep -rn foo bar/`"

    def test_pretty_tool_call_bash_without_description(self):
        """_pretty_tool_call Bash without description falls back to bare command."""
        result = _mod.render._pretty_tool_call({
            "name": "Bash",
            "input": {"command": "ls /tmp"},
        })
        assert result == "**Bash:** `ls /tmp`"

    def test_format_md_narration_section_present(self, fake_projects, capsys):
        """--format md includes **Recent agent narration:** when prior text exists."""
        # Two Opus turns: first has text, second is the code-read (sampled).
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Checking dependencies first."},
                   _read_use("r0", "/other.py")], out=100),
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent agent narration:**" in out
        assert "Checking dependencies first." in out

    def test_format_md_narration_section_absent_when_no_prior_text(self, fake_projects, capsys):
        """--format md omits **Recent agent narration:** when there is no prior assistant text."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent agent narration:**" not in out

    def test_format_md_tool_trail_section_present(self, fake_projects, capsys):
        """--format md includes **Recent tool trail:** when prior tool calls exist."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r0", "/other.py")], out=100),
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent tool trail:**" in out
        assert "Read: /other.py" in out

    def test_format_md_tool_trail_section_absent_at_session_start(self, fake_projects, capsys):
        """--format md omits **Recent tool trail:** when candidate is the first turn."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo/bar.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        assert "**Recent tool trail:**" not in out

    def test_format_md_trail_and_narration_cap_at_n(self, fake_projects, capsys):
        """Lookback caps at 3: 10 prior assistant turns yields exactly 3 narration/trail entries."""
        prior_turns = [
            _opus([{"type": "text", "text": f"Step {i}."},
                   _read_use(f"r{i}", f"/f{i}.py")], out=100)
            for i in range(10)
        ]
        candidate = _opus([_read_use("rc", "/candidate.py")], out=100)
        _write_jsonl(fake_projects / "sess.jsonl", prior_turns + [candidate])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Isolate only the card for /candidate.py (the last candidate turn)
        sections = out.split("---")
        candidate_section = next((s for s in sections if "/candidate.py" in s), None)
        assert candidate_section is not None, "No section found for /candidate.py"
        # Count narration blockquote lines starting with "> " that contain "Step"
        narration_lines = [ln for ln in candidate_section.splitlines() if ln.startswith("> ") and "Step" in ln]
        assert len(narration_lines) == 3
        # Count trail bullet lines
        trail_lines = [ln for ln in candidate_section.splitlines() if ln.startswith("- Read:") or ln.startswith("- Bash:")]
        assert len(trail_lines) == 3

    def test_format_md_fewer_than_3_priors(self, fake_projects, capsys):
        """Lookback with 1 prior turn yields exactly 1 narration and 1 trail entry."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Only prior."},
                   _read_use("r0", "/only.py")], out=100),
            _opus([_read_use("rc", "/candidate.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        narration_lines = [ln for ln in out.splitlines() if ln.startswith("> ") and "Only prior." in ln]
        assert len(narration_lines) == 1
        trail_lines = [ln for ln in out.splitlines() if "Read: /only.py" in ln]
        assert len(trail_lines) == 1

    def test_format_md_tool_only_turns_skipped_by_narration(self, fake_projects, capsys):
        """Narration walker skips turns with no text block; still collects text-bearing turns."""
        # 3 text-bearing turns, then 3 tool-only turns, then candidate.
        # Walking backward from candidate: hit 3 tool-only (skipped), then 3 text-bearing (collected).
        tool_only = [_opus([_read_use(f"r{i}", f"/t{i}.py")], out=100) for i in range(3)]
        text_bearing = [
            _opus([{"type": "text", "text": f"Text {i}."}, _read_use(f"rt{i}", f"/tb{i}.py")], out=100)
            for i in range(3)
        ]
        candidate = _opus([_read_use("rc", "/candidate.py")], out=100)
        _write_jsonl(fake_projects / "sess.jsonl", text_bearing + tool_only + [candidate])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Isolate the candidate card and assert narration contains the text-bearing entries
        sections = out.split("---")
        candidate_section = next((s for s in sections if "/candidate.py" in s), None)
        assert candidate_section is not None, "No section found for /candidate.py"
        for i in range(3):
            assert f"Text {i}." in candidate_section

    def test_format_md_newlines_stripped_in_narration(self, fake_projects, capsys):
        """Newlines in narration text are replaced with spaces (no blockquote corruption)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([{"type": "text", "text": "Line one.\nLine two."},
                   _read_use("r0", "/a.py")], out=100),
            _opus([_read_use("rc", "/candidate.py")], out=100),
        ])
        args = _audit_routing_samples_args(output_format="md")
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Should appear as a single blockquote line with a space, not two separate lines
        assert "> Line one. Line two." in out

    def test_format_md_cross_session_isolation(self, fake_projects, capsys):
        """Candidate in session B does not pick up session A's narration or trail."""
        # Session A: has a text turn with distinctive content.
        (fake_projects / "sess_a.jsonl").write_text(
            "\n".join(
                json.dumps(r) for r in [
                    _opus([{"type": "text", "text": "Session A unique text."},
                           _read_use("ra", "/a.py")], out=100),
                ]
            ) + "\n"
        )
        # Session B: single code-read candidate, no prior turns.
        (fake_projects / "sess_b.jsonl").write_text(
            "\n".join(
                json.dumps(r) for r in [
                    _opus([_read_use("rb", "/b.py")], out=100),
                ]
            ) + "\n"
        )
        args = _audit_routing_samples_args(output_format="md", sample=10)
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        # Split by section headers and check B's card specifically.
        sections = out.split("---")
        b_section = next((s for s in sections if "/b.py" in s), None)
        if b_section:  # only assert if B was sampled
            assert "Session A unique text." not in b_section

    def test_format_json_record_key_set(self, fake_projects, capsys):
        """JSON output record has exactly the expected key set (no missing, no extra)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_use("r1", "/foo.py")], out=100),
        ])
        args = _audit_routing_samples_args()  # default JSON format
        _mod.cmd_audit_routing_samples(args)
        out = capsys.readouterr().out
        records = json.loads(out)
        assert len(records) == 1
        assert set(records[0].keys()) == {
            "session_id", "turn_index", "prior_user_message",
            "assistant_tool_call", "next_assistant_action", "next_turn_excerpt",
            "recent_assistant_text", "recent_tool_trail",
        }

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, capsys):
        """A malformed --since value fails closed with the audit-routing-samples-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod.cmd_audit_routing_samples(_audit_routing_samples_args(since="not-a-window"))
        assert "audit-routing-samples: --since: expected Nd like '35d'" in capsys.readouterr().err

    def test_request_id_group_tool_use_on_later_record_still_one_turn_at_index_zero(
        self, fake_projects, capsys
    ):
        """A requestId group whose Read tool_use lands on the second raw
        record (the first record is thinking-only) still emits exactly one
        code-read candidate at turn_index 0 with assistant_tool_call set from
        the merged content's own tool_use block — without dedup the
        thinking-only first record becomes its own phantom pure-thinking
        turn, pushing the real turn to index 1."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_thinking_block()], out=20, request_id="req-1"),
            _opus([_read_use("r1", "/a.py")], out=20, request_id="req-1"),
        ])
        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["turn_index"] == 0
        assert records[0]["assistant_tool_call"] == {"name": "Read", "input": {"file_path": "/a.py"}}


# ---------------------------------------------------------------------------
# cmd_struggle — new STRUGGLE_PHRASES entries
# ---------------------------------------------------------------------------


class TestCmdStruggle:
    def test_hallucinat_phrase_registers_as_struggle(self, fake_projects, capsys):
        """"I think you hallucinated about this" should register; "stale cache" should not."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            # Assistant turn so branch has a model family to attribute to.
            _asst("claude-sonnet-4-6", branch="feat"),
            # Human turn containing the new phrase — should register as struggle.
            _user_msg(
                [{"type": "text", "text": "I think you hallucinated about this"}],
                branch="feat",
            ),
            # Control: "stale" is excluded from STRUGGLE_PHRASES (legitimate technical term).
            _user_msg(
                [{"type": "text", "text": "the stale cache needs clearing"}],
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out

        # The output table should include "feat" with at least one struggle signal.
        assert "feat" in out, "branch not found in output"
        # Parse the data row for "feat": columns are Branch Opus Sonnet Haiku Other Unknown
        cols = _table_cols(out, header_contains="Opus", row_contains="feat")
        total_signals = sum(int(cols[k]) for k in ["Opus", "Sonnet", "Haiku", "Other", "Unknown"])
        assert total_signals == 1, (
            f"expected exactly 1 struggle signal (from 'hallucinated', not from 'stale cache'); got cols={cols}"
        )

    def test_stale_control_phrase_does_not_inflate_count(self, fake_projects, capsys):
        """Bare "stale" must not match — excluded from STRUGGLE_PHRASES due to false-positive risk."""
        # Confirm "stale" alone is absent from STRUGGLE_PHRASES.
        assert not any(p == "stale" for p in _mod.STRUGGLE_PHRASES), (
            "bare 'stale' was added to STRUGGLE_PHRASES; it is intentionally excluded — "
            "remove it to avoid false positives on technical uses like 'stale cache'"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                [{"type": "text", "text": "the stale cache needs clearing"}],
                branch="feat",
            ),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out
        # "stale cache" alone should produce no struggle signals — branch absent from output.
        assert "feat" not in out, (
            f"'stale cache' should not register as a struggle signal; branch appeared in output: {out!r}"
        )

    @pytest.mark.parametrize(
        "phrase,text",
        [
            ("are you saying", "Are you saying this is correct?"),
            ("you should be able to", "You should be able to run it directly."),
            ("that doesn't exist", "that doesn't exist in the repo"),
            ("that doesn't match", "that doesn't match what I see"),
            ("not what i wanted", "that's not what i wanted at all"),
            ("not what i asked", "that's not what i asked you to do"),
            ("hallucinat", "I think you hallucinated that function"),
        ],
    )
    def test_new_phrases_register_as_struggle(self, phrase, text, fake_projects, capsys):
        """Each newly-added STRUGGLE_PHRASES entry must register when present in a user turn."""
        assert any(phrase in p for p in _mod.STRUGGLE_PHRASES), (
            f"phrase {phrase!r} not found in STRUGGLE_PHRASES — was it removed or mistyped?"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg([{"type": "text", "text": text}], branch="feat"),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": "feat"})()
        _mod.cmd_struggle(args)
        out = capsys.readouterr().out
        assert "feat" in out, (
            f"phrase {phrase!r} (text={text!r}) should register as a struggle signal; branch absent from output: {out!r}"
        )


# ---------------------------------------------------------------------------
# user-input
# ---------------------------------------------------------------------------


def _ui_user(content, *, branch: str = "main", ts: str | None = None,
             session_id: str | None = None, **extra_fields) -> dict:
    """Fresh-prompt-shaped user record for user-input tests, with optional
    timestamp/sessionId/extra top-level fields the shared _user_msg helper
    doesn't parametrize."""
    rec: dict = {"type": "user", "gitBranch": branch, "message": {"content": content}}
    if ts:
        rec["timestamp"] = ts
    if session_id:
        rec["sessionId"] = session_id
    rec.update(extra_fields)
    return rec


def _user_input_args(
    *,
    projects: str = "*",
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    corrections_only: bool = False,
    truncate_chars: int = 500,
    out: str | None = None,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "branches": branches,
        "since": since,
        "until": until,
        "corrections_only": corrections_only,
        "truncate_chars": truncate_chars,
        "out": out,
        "redact": redact,
    })()


class TestCmdUserInput:
    def test_classification_initial_followup_explicit_correction(self, fake_projects, capsys):
        """First prompt -> INITIAL; a non-matching later prompt -> FOLLOWUP; a
        STRUGGLE_PHRASES match later in the session -> EXPLICIT_CORRECTION with
        the matched phrase noted."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("plain initial prompt", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
            _ui_user("the stale cache needs clearing", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
            _ui_user("I think you hallucinated about this", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "**[? · INITIAL · sonnet]**\n~~~text\nplain initial prompt\n~~~" in out
        assert "**[? · FOLLOWUP · sonnet]**\n~~~text\nthe stale cache needs clearing\n~~~" in out
        assert (
            '**[? · EXPLICIT_CORRECTION · sonnet]** (matched: "hallucinat")\n'
            "~~~text\nI think you hallucinated about this\n~~~"
        ) in out

    def test_corrections_only_excludes_initial(self, fake_projects, capsys):
        """--corrections-only drops INITIAL prompts but keeps FOLLOWUP/EXPLICIT_CORRECTION."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("plain initial prompt", branch="feat"),
            _ui_user("the stale cache needs clearing", branch="feat"),
            _ui_user("I think you hallucinated about this", branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args(corrections_only=True))
        out = capsys.readouterr().out
        assert "plain initial prompt" not in out
        assert "the stale cache needs clearing" in out
        assert "I think you hallucinated about this" in out

    def test_since_until_date_bounds(self, fake_projects, capsys):
        """A prompt outside the --since/--until window is excluded; one inside the window is kept."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("old prompt outside window", ts="2020-01-01T00:00:00.000Z"),
            _ui_user("new prompt inside window", ts="2026-05-20T00:00:00.000Z"),
        ])
        _mod.cmd_user_input(_user_input_args(since="2026-05-01", until="2026-05-31"))
        out = capsys.readouterr().out
        assert "old prompt outside window" not in out
        assert "new prompt inside window" in out

    def test_redact_remaps_project_label_not_prompt_text(self, fake_projects, capsys):
        """--redact remaps the project label to private-project-N but leaves prompt text verbatim
        — the flag anonymizes labels and session IDs only, never message content."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("keep this project name private", branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args(redact=True))
        out = capsys.readouterr().out
        assert "private-project-1" in out
        assert "testrepo" not in out
        assert "keep this project name private" in out

    def test_redact_remaps_session_id(self, fake_projects, capsys):
        """--redact remaps the raw session ID to an opaque session-N label via the file's
        existing _assign_session_redact_label/_redact_session_id helpers — the same
        primitive audit-routing and cost already use. Without --redact, the raw
        session ID's first 8 chars are shown unmapped."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("hello", branch="feat", session_id="real-session-uuid-1234"),
        ])
        _mod.cmd_user_input(_user_input_args(redact=True))
        out_redacted = capsys.readouterr().out
        assert "Session `session-1`" in out_redacted
        assert "real-session" not in out_redacted

        _mod.cmd_user_input(_user_input_args(redact=False))
        out_unredacted = capsys.readouterr().out
        assert "Session `real-ses`" in out_unredacted

    def test_truncate_chars_including_zero_disables_truncation(self, fake_projects, capsys):
        """--truncate-chars truncates long prompt text with an annotation; 0 disables truncation entirely."""
        long_text = "x" * 600
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user(long_text, branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args(truncate_chars=100))
        out_truncated = capsys.readouterr().out
        assert "x" * 100 in out_truncated
        assert "(truncated, 600 chars total)" in out_truncated
        assert long_text not in out_truncated

        _mod.cmd_user_input(_user_input_args(truncate_chars=0))
        out_full = capsys.readouterr().out
        assert long_text in out_full
        assert "truncated" not in out_full

    def test_unrecognized_shape_counted_and_reported_on_stderr(self, fake_projects, capsys):
        """A user record with list-of-blocks content matching no known discriminator
        increments the shape-audit counter printed to stderr, independent of
        whether it counts as a fresh prompt."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user([{"type": "text", "text": "odd shape"}], branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args())
        captured = capsys.readouterr()
        assert "- Fresh prompts: 0" in captured.out
        assert "Shape audit: 1 unrecognized user records skipped" in captured.err

    @pytest.mark.parametrize(
        "extra_fields",
        [
            {"isMeta": True},
            {"isSidechain": True},
            {"toolUseResult": {"stdout": "x"}, "sourceToolUseID": "t1", "sourceToolAssistantUUID": "u1"},
        ],
        ids=["isMeta", "isSidechain", "tool_result_keys"],
    )
    def test_exclusion_guards_exclude_from_fresh_prompt_count(self, extra_fields, fake_projects, capsys):
        """isMeta=True, isSidechain=True, and toolUseResult/sourceToolUseID/sourceToolAssistantUUID
        records are excluded from total_fresh_prompts."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("genuine prompt", branch="feat"),
            _ui_user("excluded record", branch="feat", **extra_fields),
        ])
        _mod.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "- Fresh prompts: 1" in out
        assert "excluded record" not in out

    def test_empty_string_plain_content_included_no_strip_guard(self):
        """Pin actual behavior: the plain-string content path has no .strip() guard,
        so an empty string is NOT excluded — unlike the list-of-blocks path, which
        requires non-empty stripped text via a promptId-gated _content_text().strip() check."""
        rec = _ui_user("")
        assert _mod._is_fresh_user_prompt_for_narrative(rec) is True

    def test_attribute_model_unknown_when_no_matching_assistant(self):
        """No assistant record follows the prompt -> model_fam is 'unknown'."""
        records = [_ui_user("solo prompt", session_id="s1")]
        assert _mod._attribute_model_to_prompt(records, 0, "s1") == "unknown"

    def test_attribute_model_ignores_cross_session_interleaved_assistant(self):
        """An assistant record from a different sessionId interleaved between the
        prompt and its own session's reply is not used for attribution."""
        records = [
            _ui_user("prompt in s1", session_id="s1"),
            _asst("claude-opus-4-7", branch="main"),
            _asst("claude-sonnet-4-6", branch="main"),
        ]
        records[1]["sessionId"] = "s2"  # interleaved reply from a different session — must be skipped
        records[2]["sessionId"] = "s1"  # the real reply for this prompt's session
        assert _mod._attribute_model_to_prompt(records, 0, "s1") == "sonnet"

    def test_attribute_model_two_consecutive_prompts_share_the_same_reply(self):
        """Pin actual behavior: two fresh prompts with no assistant reply between them
        both attribute to the single assistant record that follows both — the forward
        scan has no boundary check against an intervening user prompt."""
        records = [
            _ui_user("first prompt, no reply yet", session_id="s1"),
            _ui_user("second prompt, still no reply", session_id="s1"),
            _asst("claude-opus-4-7", branch="main"),
        ]
        records[2]["sessionId"] = "s1"
        assert _mod._attribute_model_to_prompt(records, 0, "s1") == "opus"
        assert _mod._attribute_model_to_prompt(records, 1, "s1") == "opus"

    def test_classify_prompt_direct(self):
        """Direct unit coverage of the pure classification function, independent of
        cmd_user_input's I/O and markdown rendering."""
        assert _mod._classify_prompt("anything at all", True) == ("INITIAL", "")
        assert _mod._classify_prompt("this is a normal follow-up", False) == ("FOLLOWUP", "")
        classification, matched_phrase = _mod._classify_prompt(
            "I think you hallucinated that function", False
        )
        assert classification == "EXPLICIT_CORRECTION"
        assert matched_phrase == "hallucinat"

    def test_truncate_prompt_text_direct(self):
        """Direct unit coverage of the pure truncation function: under-limit and
        exact-limit text pass through unchanged, over-limit text is truncated with
        an annotation, and limit=0 disables truncation regardless of length."""
        assert _mod._truncate_prompt_text("short", 100) == "short"
        exact = "x" * 50
        assert _mod._truncate_prompt_text(exact, 50) == exact
        over = "x" * 60
        truncated = _mod._truncate_prompt_text(over, 50)
        assert truncated == "x" * 50 + "… (truncated, 60 chars total)"
        assert _mod._truncate_prompt_text("x" * 10_000, 0) == "x" * 10_000

    def test_session_sort_order_and_missing_first_ts_placement(self, fake_projects, capsys):
        """Sessions sort ascending by first-prompt timestamp; a session whose first
        prompt has no parseable timestamp sorts first under the `first_ts or 0.0`
        key, ahead of every dated session — pinning current behavior, not
        asserting it's the only correct choice."""
        _write_jsonl(fake_projects / "sess_a.jsonl", [
            _ui_user("prompt in session a", branch="branch-a", ts="2026-06-01T00:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "sess_b.jsonl", [
            _ui_user("prompt in session b", branch="branch-b", ts="2026-01-01T00:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "sess_c.jsonl", [
            _ui_user("prompt in session c", branch="branch-c"),  # no timestamp -> first_ts is None
        ])
        _mod.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        pos_a = out.index("branch-a")
        pos_b = out.index("branch-b")
        pos_c = out.index("branch-c")
        assert pos_c < pos_b < pos_a, f"expected order c, b, a; got positions a={pos_a} b={pos_b} c={pos_c}"

    def test_first_prompt_with_struggle_phrase_still_initial(self, fake_projects, capsys):
        """The first prompt in a session classifies INITIAL even when it contains
        a STRUGGLE_PHRASES match — is_first_in_session takes precedence, and
        matched_phrase stays empty (no '(matched: ...)' suffix)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("I think you hallucinated about this", branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "INITIAL" in out
        assert "EXPLICIT_CORRECTION" not in out
        assert "matched:" not in out

    def test_branches_filter_excludes_nonmatching_branch(self, fake_projects, capsys):
        """--branches restricts output to the named branch(es); a non-matching
        branch's prompts are excluded entirely."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prompt on feat branch", branch="feat"),
            _ui_user("prompt on main branch", branch="main"),
        ])
        _mod.cmd_user_input(_user_input_args(branches="feat"))
        out = capsys.readouterr().out
        assert "prompt on feat branch" in out
        assert "prompt on main branch" not in out

    def test_redact_claude_config_self_exception(self, fake_projects, capsys):
        """--redact's claude-config self-exception passes that label through
        unredacted while remapping the other project to private-project-N."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("hello from the other project", branch="feat"),
        ])
        proj_cc = fake_projects.parent / "-home-user-claude-config"
        proj_cc.mkdir(parents=True)
        _write_jsonl(proj_cc / "sess.jsonl", [
            _ui_user("hello from claude-config", branch="feat"),
        ])
        _mod.cmd_user_input(_user_input_args(redact=True))
        out = capsys.readouterr().out
        assert "### claude-config ·" in out
        assert "### private-project-1 ·" in out

    def test_out_write_failure_exits_1(self, fake_projects, capsys, tmp_path):
        """A write failure to --out's target (parent directory missing) exits 1
        with the user-input-specific stderr message; nothing is printed to stdout."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prompt", branch="feat"),
        ])
        bad_out_path = tmp_path / "missing-dir" / "output.txt"
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_user_input(_user_input_args(out=str(bad_out_path)))
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert f"user-input: failed to write {bad_out_path}: " in captured.err
        assert captured.out == ""

    def test_out_write_success_writes_file_and_confirms_on_stdout(self, fake_projects, capsys, tmp_path):
        """A successful --out write produces a file whose contents match what stdout
        would otherwise have shown, and prints the confirmation line to stdout."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prompt written to file", branch="feat"),
        ])
        out_path = tmp_path / "output.md"
        _mod.cmd_user_input(_user_input_args(out=str(out_path)))
        captured = capsys.readouterr()
        assert f"Wrote output to {out_path}" in captured.out
        file_content = out_path.read_text(encoding="utf-8")
        assert "prompt written to file" in file_content
        assert "# User Input — Conversation Narrative" in file_content


# ---------------------------------------------------------------------------
# skill-invocation
# ---------------------------------------------------------------------------


class TestSkillInvocation:
    def _run(self, capsys):
        args = argparse.Namespace(projects="*")
        _mod.cmd_skill_invocation(args)
        return capsys.readouterr().out

    def test_top_level_invocation_counted(self, fake_projects, capsys):
        """Assistant record with Skill tool_use and no attributionSkill → counted as top-level."""
        asst_rec = _asst("claude-opus-4-7", branch="main", content=[
            _skill_use("t1", "code-review")
        ])
        # No attributionSkill field → top-level
        _write_jsonl(fake_projects / "s1.jsonl", [asst_rec])
        out = self._run(capsys)
        assert "code-review" in out
        # Top-level column should be ≥1
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) >= 1, f"expected top-level ≥1, got: {cols}"
        assert int(cols["routed"]) == 0, f"expected routed=0, got: {cols}"
        assert int(cols["user-slash"]) == 0, f"expected slash=0, got: {cols}"

    def test_routed_invocation_counted(self, fake_projects, capsys):
        """Assistant record with Skill tool_use AND attributionSkill → counted as routed."""
        asst_rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("t2", "code-review")
        ])
        asst_rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s2.jsonl", [asst_rec])
        out = self._run(capsys)
        # Routed pair must appear in the ROUTED PAIRS section with count 1
        pair_lines = [ln for ln in out.splitlines() if "ready-for-review -> code-review" in ln]
        assert pair_lines, "ready-for-review -> code-review pair line not found in output"
        assert ": 1" in pair_lines[0], f"expected count=1 in pair line: {pair_lines[0]!r}"

    def test_routed_column_count_correct(self, fake_projects, capsys):
        """Routed invocation increments the routed column and not top-level."""
        asst_rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("t3", "plan-review")
        ])
        asst_rec["attributionSkill"] = "plan-it"
        _write_jsonl(fake_projects / "s3.jsonl", [asst_rec])
        out = self._run(capsys)
        cols = _table_cols(out, header_contains="user-slash", row_contains="plan-review", row_startswith=True)
        assert int(cols["top-level"]) == 0, f"expected top-level=0, got: {cols}"
        assert int(cols["routed"]) >= 1, f"expected routed≥1, got: {cols}"

    def test_slash_invocation_counted(self, fake_projects, capsys):
        """User record whose content contains <command-name>/plan-it</command-name> → user-slash count."""
        user_rec = _user_msg(
            "<command-message>plan-it</command-message>\n<command-name>/plan-it</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s4.jsonl", [user_rec])
        out = self._run(capsys)
        # plan-it must appear in the table; slash column should be ≥1
        assert "plan-it" in out
        cols = _table_cols(out, header_contains="user-slash", row_contains="plan-it", row_startswith=True)
        assert int(cols["user-slash"]) >= 1, f"expected slash≥1, got: {cols}"

    def test_sidechain_excluded(self, fake_projects, capsys):
        """Sidechain assistant records are not counted in any bucket."""
        asst_rec = _asst("claude-opus-4-7", branch="main", sidechain=True, content=[
            _skill_use("t5", "code-review")
        ])
        _write_jsonl(fake_projects / "s5.jsonl", [asst_rec])
        out = self._run(capsys)
        # No non-sidechain invocations → no data → "No skill invocations found."
        assert "No skill invocations found." in out

    def test_routed_pair_grouping(self, fake_projects, capsys):
        """Two records both with attributionSkill='ready-for-review' and skill='code-review'
        → routed_pairs contains (ready-for-review, code-review): 2."""
        rec1 = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t6a", "code-review")])
        rec1["attributionSkill"] = "ready-for-review"
        rec2 = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t6b", "code-review")])
        rec2["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s6.jsonl", [rec1, rec2])
        out = self._run(capsys)
        # The pair should appear exactly once in the routed pairs section with count 2
        pair_lines = [ln for ln in out.splitlines() if "ready-for-review -> code-review" in ln]
        assert len(pair_lines) == 1, f"Expected exactly one pair line, got: {pair_lines}"
        assert ": 2" in pair_lines[0], f"Expected count=2 in pair line: {pair_lines[0]!r}"

    def test_routed_only_candidate_in_summary(self, fake_projects, capsys):
        """A skill with only routed invocations appears in the 'Routed-only candidates' section."""
        rec = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t7", "agent-review")])
        rec["attributionSkill"] = "code-review"
        _write_jsonl(fake_projects / "s7.jsonl", [rec])
        out = self._run(capsys)
        assert "Routed-only candidates" in out, f"section header not found in output: {out!r}"
        assert "agent-review" in out
        # agent-review must appear in the routed-only section, not load-bearing
        routed_only_start = out.find("Routed-only candidates")
        slash_only_start = out.find("Slash-only candidates")
        assert routed_only_start != -1, "Routed-only candidates section not found"
        assert slash_only_start != -1, "Slash-only candidates section not found"
        routed_only_section = out[routed_only_start:slash_only_start]
        assert "agent-review" in routed_only_section

    def test_load_bearing_in_summary(self, fake_projects, capsys):
        """A skill with top-level invocations appears in the 'Load-bearing' section."""
        rec = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t8", "skill-review")])
        _write_jsonl(fake_projects / "s8.jsonl", [rec])
        out = self._run(capsys)
        load_bearing_start = out.find("Load-bearing")
        routed_only_start = out.find("Routed-only candidates")
        assert load_bearing_start != -1, "Load-bearing section not found"
        assert routed_only_start != -1, "Routed-only candidates section not found"
        load_bearing_section = out[load_bearing_start:routed_only_start]
        assert "skill-review" in load_bearing_section

    def test_slash_only_candidate_in_summary(self, fake_projects, capsys):
        """A skill with only slash invocations appears in the 'Slash-only candidates' section."""
        user_rec = _user_msg(
            "<command-name>/handoff</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s9.jsonl", [user_rec])
        out = self._run(capsys)
        slash_only_start = out.find("Slash-only candidates")
        assert slash_only_start != -1, "Slash-only candidates section not found"
        slash_only_section = out[slash_only_start:]
        assert "handoff" in slash_only_section

    def test_no_invocations_prints_not_found(self, fake_projects, capsys):
        """Empty corpus prints 'No skill invocations found.'"""
        # Write a session with only a Bash call — no Skill tool_use or slash
        _write_jsonl(fake_projects / "s_empty.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[
                _bash_use("b1", "git status")
            ])
        ])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_projects_filter_restricts_to_named_project(self, tmp_path, monkeypatch, capsys):
        """--projects filter causes only the named project dir to be scanned."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-user-repoA"
        proj_b = projects / "-home-user-repoB"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        # repoA has a skill invocation; repoB has a different skill
        rec_a = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("ta1", "code-review")])
        rec_b = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tb1", "plan-review")])
        _write_jsonl(proj_a / "s_a.jsonl", [rec_a])
        _write_jsonl(proj_b / "s_b.jsonl", [rec_b])
        # Filter to repoA only
        args = argparse.Namespace(projects="-home-user-repoA")
        _mod.cmd_skill_invocation(args)
        out = capsys.readouterr().out
        assert "code-review" in out, "repoA skill not found"
        assert "plan-review" not in out, "repoB skill leaked through the project filter"

    def test_empty_skill_field_in_tool_use_skipped(self, fake_projects, capsys):
        """Skill tool_use with empty or absent input.skill is not counted."""
        rec_empty = _asst("claude-sonnet-4-6", branch="main", content=[
            {"type": "tool_use", "id": "te1", "name": "Skill", "input": {"skill": ""}},
        ])
        rec_absent = _asst("claude-sonnet-4-6", branch="main", content=[
            {"type": "tool_use", "id": "te2", "name": "Skill", "input": {}},
        ])
        _write_jsonl(fake_projects / "s_empty_skill.jsonl", [rec_empty, rec_absent])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_multiple_skills_in_one_assistant_record(self, fake_projects, capsys):
        """Two Skill tool_use blocks in a single assistant record's content → both counted."""
        rec = _asst("claude-sonnet-4-6", branch="main", content=[
            _skill_use("tm1", "code-review"),
            _skill_use("tm2", "plan-review"),
        ])
        _write_jsonl(fake_projects / "s_multi_skill.jsonl", [rec])
        out = self._run(capsys)
        cr_cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cr_cols["top-level"]) == 1, f"code-review top-level count should be 1: {cr_cols}"
        pr_cols = _table_cols(out, header_contains="user-slash", row_contains="plan-review", row_startswith=True)
        assert int(pr_cols["top-level"]) == 1, f"plan-review top-level count should be 1: {pr_cols}"

    def test_multiple_slash_tags_in_one_user_record(self, fake_projects, capsys):
        """Two <command-name> tags in a single user record → both skill names counted in slash column."""
        user_rec = _user_msg(
            "<command-name>/plan-it</command-name>\n<command-name>/code-review</command-name>",
            branch="main",
        )
        _write_jsonl(fake_projects / "s_multi_slash.jsonl", [user_rec])
        out = self._run(capsys)
        pi_cols = _table_cols(out, header_contains="user-slash", row_contains="plan-it", row_startswith=True)
        assert int(pi_cols["user-slash"]) >= 1, f"plan-it slash count should be ≥1: {pi_cols}"
        cr_cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cr_cols["user-slash"]) >= 1, f"code-review slash count should be ≥1: {cr_cols}"

    def test_slash_detection_with_list_form_user_content(self, fake_projects, capsys):
        """User record whose content is a list-of-blocks → slash command still detected via _content_text."""
        user_rec = {
            "type": "user",
            "gitBranch": "main",
            "message": {
                "content": [
                    {"type": "text", "text": "<command-name>/plan-it</command-name>"},
                ]
            },
        }
        _write_jsonl(fake_projects / "s_list_content.jsonl", [user_rec])
        out = self._run(capsys)
        assert "plan-it" in out
        cols = _table_cols(out, header_contains="user-slash", row_contains="plan-it", row_startswith=True)
        assert int(cols["user-slash"]) >= 1, f"plan-it slash count should be ≥1: {cols}"

    def test_mixed_top_and_routed_in_same_session(self, fake_projects, capsys):
        """A session with both top-level and routed calls for the same skill tallies both columns."""
        top_rec = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t10a", "code-review")])
        # No attributionSkill → top-level
        routed_rec = _asst("claude-haiku-4-5", branch="main", content=[_skill_use("t10b", "code-review")])
        routed_rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s10.jsonl", [top_rec, routed_rec])
        out = self._run(capsys)
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) == 1, f"expected top-level=1, got: {cols}"
        assert int(cols["routed"]) == 1, f"expected routed=1, got: {cols}"
        assert int(cols["total"]) == 2, f"expected total=2, got: {cols}"

    def test_sidechain_user_records_excluded_from_slash(self, fake_projects, capsys):
        """User records with isSidechain=True are not scanned for slash invocations."""
        sidechain_user = {
            "type": "user",
            "isSidechain": True,
            "gitBranch": "main",
            "message": {"content": "<command-name>/code-review</command-name>"},
        }
        _write_jsonl(fake_projects / "s_sidechain_user.jsonl", [sidechain_user])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_sidechain_user_excluded_main_thread_user_counted(self, fake_projects, capsys):
        """Mixed session: sidechain user slash is excluded, main-thread user slash is counted."""
        sidechain_user = {
            "type": "user",
            "isSidechain": True,
            "gitBranch": "main",
            "message": {"content": "<command-name>/plan-review</command-name>"},
        }
        main_user = _user_msg("<command-name>/code-review</command-name>", branch="main")
        _write_jsonl(fake_projects / "s_sidechain_mixed.jsonl", [sidechain_user, main_user])
        out = self._run(capsys)
        assert "code-review" in out, "main-thread slash should be counted"
        assert "plan-review" not in out, "sidechain slash should be excluded"
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["user-slash"]) == 1, f"expected slash=1, got: {cols}"

    def test_multiple_skill_blocks_on_routed_record(self, fake_projects, capsys):
        """An attributed record with two Skill tool_use blocks counts both as routed, not top-level."""
        rec = _asst("claude-haiku-4-5", branch="main", content=[
            _skill_use("tr1", "code-review"),
            _skill_use("tr2", "plan-review"),
        ])
        rec["attributionSkill"] = "ready-for-review"
        _write_jsonl(fake_projects / "s_multi_routed.jsonl", [rec])
        out = self._run(capsys)
        cr_cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cr_cols["top-level"]) == 0, f"code-review top-level should be 0: {cr_cols}"
        assert int(cr_cols["routed"]) == 1, f"code-review routed should be 1: {cr_cols}"
        pr_cols = _table_cols(out, header_contains="user-slash", row_contains="plan-review", row_startswith=True)
        assert int(pr_cols["top-level"]) == 0, f"plan-review top-level should be 0: {pr_cols}"
        assert int(pr_cols["routed"]) == 1, f"plan-review routed should be 1: {pr_cols}"

    def test_cross_project_aggregation(self, tmp_path, monkeypatch, capsys):
        """Default glob aggregates counts across multiple project directories."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-user-repoA"
        proj_b = projects / "-home-user-repoB"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        rec_a = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tc1", "code-review")])
        rec_b = _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("tc2", "code-review")])
        _write_jsonl(proj_a / "s_a.jsonl", [rec_a])
        _write_jsonl(proj_b / "s_b.jsonl", [rec_b])
        args = argparse.Namespace(projects="*")
        _mod.cmd_skill_invocation(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review", row_startswith=True)
        assert int(cols["top-level"]) == 2, f"expected 2 top-level from both projects: {cols}"


# ---------------------------------------------------------------------------
# skill-invocation: --branches, --include-subagents, name normalization
# ---------------------------------------------------------------------------


def _skill_inv_args(*, projects="*", branches=None, include_subagents=False):
    """Args object for cmd_skill_invocation. projects='*' takes the explicit-glob
    escape hatch, bypassing git derivation — the default for cases that only
    exercise counting/filtering logic on fake_projects data."""
    return argparse.Namespace(
        projects=projects, branches=branches, include_subagents=include_subagents
    )


class TestSkillInvocationScoping:
    """Covers the branch/subagent scoping the procedural-fidelity consumer needs.

    The default (no flags) path is pinned by TestSkillInvocation above; these
    cases assert the opt-in behavior and that enabling it does not change what
    the default reports. They pass projects='*' (explicit escape hatch) so the
    git-derivation path is exercised separately in TestSkillInvocationRepoScope.
    """

    def _run(self, capsys, **kw):
        _mod.cmd_skill_invocation(_skill_inv_args(**kw))
        return capsys.readouterr().out

    def test_branch_filter_excludes_other_branches(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="wanted", content=[_skill_use("b1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="other", content=[_skill_use("b2", "plan-review")]),
        ])
        out = self._run(capsys, branches="wanted")
        assert "code-review" in out, "on-branch skill should be counted"
        assert "plan-review" not in out, "off-branch skill must be excluded"

    def test_branch_filter_accepts_comma_separated_list(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="b-one", content=[_skill_use("m1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="b-two", content=[_skill_use("m2", "plan-review")]),
            _asst("claude-sonnet-4-6", branch="b-three", content=[_skill_use("m3", "handoff")]),
        ])
        out = self._run(capsys, branches="b-one,b-two")
        assert "code-review" in out and "plan-review" in out
        assert "handoff" not in out, "branch outside the list must be excluded"

    def test_branch_filter_applies_to_slash_invocations(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _user_msg("<command-name>/plan-it</command-name>", branch="wanted"),
            _user_msg("<command-name>/handoff</command-name>", branch="other"),
        ])
        out = self._run(capsys, branches="wanted")
        assert "plan-it" in out
        assert "handoff" not in out, "off-branch slash invocation must be excluded"

    def test_unfiltered_run_still_counts_records_without_gitbranch(self, fake_projects, capsys):
        """A record with no gitBranch is counted when no --branches filter is given —
        guards the default-output regression noted in the plan."""
        rec = {
            "type": "assistant",
            "message": {"model": "claude-sonnet-4-6", "content": [_skill_use("nb", "code-review")]},
        }
        _write_jsonl(fake_projects / "s.jsonl", [rec])
        out = self._run(capsys)
        assert "code-review" in out, "branchless record must still count when unfiltered"

    def test_subagent_invocation_ignored_by_default(self, fake_projects, capsys):
        session_id = "sess-sub"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_asst("claude-opus-4-7", branch="main")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True,
                  content=[_skill_use("sa1", "code-review")]),
        ])
        out = self._run(capsys)
        assert "No skill invocations found." in out

    def test_include_subagents_reports_sidechain_row(self, fake_projects, capsys):
        session_id = "sess-sub"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_asst("claude-opus-4-7", branch="main")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True,
                  content=[_skill_use("sa1", "code-review")]),
        ])
        out = self._run(capsys, include_subagents=True)
        cols = _table_cols(out, header_contains="thread", row_contains="code-review",
                           row_startswith=True)
        assert cols["thread"] == "sidechain", f"expected sidechain thread row: {cols}"
        assert int(cols["top-level"]) == 1, f"expected 1 sidechain invocation: {cols}"

    def test_main_and_sidechain_kept_on_separate_rows(self, fake_projects, capsys):
        session_id = "sess-both"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_skill_use("m1", "code-review")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
        ])
        out = self._run(capsys, include_subagents=True)
        rows = [ln for ln in out.splitlines() if ln.startswith("code-review")]
        assert len(rows) == 2, f"expected separate main and sidechain rows, got: {rows}"
        # The thread value is the second whitespace-delimited column; compare on the
        # parsed token rather than a padded-substring match, which would break on a
        # benign column-width change.
        threads = {r.split()[1] for r in rows}
        assert threads == {"main", "sidechain"}, f"expected one main and one sidechain row, got: {rows}"

    def test_thread_column_absent_without_flag(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("t1", "code-review")]),
        ])
        out = self._run(capsys)
        header = [ln for ln in out.splitlines() if "user-slash" in ln][0]
        assert "thread" not in header, f"default output must not gain a thread column: {header!r}"

    def test_branch_filter_composes_with_include_subagents(self, fake_projects, capsys):
        session_id = "sess-mix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [_asst("claude-opus-4-7", branch="wanted")])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="wanted", sidechain=True,
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="other", sidechain=True,
                  content=[_skill_use("s2", "plan-review")]),
        ])
        out = self._run(capsys, branches="wanted", include_subagents=True)
        assert "code-review" in out
        assert "plan-review" not in out, "off-branch subagent invocation must be excluded"


class TestSkillInvocationNameNormalization:
    """Worktree-qualified and colon-prefixed skill-name handling (row hygiene)."""

    def _run(self, capsys, **kw):
        _mod.cmd_skill_invocation(_skill_inv_args(**kw))
        return capsys.readouterr().out

    def test_worktree_qualified_name_collapses_to_bare(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[
                _skill_use("c1", ".claude/worktrees/b1/claude:code-review"),
                _skill_use("c2", "claude:code-review"),
            ]),
        ])
        out = self._run(capsys)
        rows = [ln for ln in out.splitlines() if ln.startswith("claude:code-review")]
        assert len(rows) == 1, f"expected one collapsed row, got: {rows}"
        cols = _table_cols(out, header_contains="user-slash",
                           row_contains="claude:code-review", row_startswith=True)
        assert int(cols["top-level"]) == 2, f"both spellings should sum into one row: {cols}"
        assert "worktrees" not in out, "path fragment must not survive normalization"

    def test_plugin_prefix_preserved(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main",
                  content=[_skill_use("p1", "skill-management:skill-review")]),
        ])
        out = self._run(capsys)
        assert "skill-management:skill-review" in out, "plugin qualifier must be preserved"

    def test_attribution_parent_normalized_in_routed_pairs(self, fake_projects, capsys):
        """The ROUTED PAIRS parent is normalized too, so a worktree-qualified
        attributionSkill does not carry its path into output."""
        rec = _asst("claude-sonnet-4-6", branch="main",
                    content=[_skill_use("r1", "code-review")])
        rec["attributionSkill"] = ".claude/worktrees/some-branch/claude:ready-for-review"
        _write_jsonl(fake_projects / "s.jsonl", [rec])
        out = self._run(capsys)
        assert "ready-for-review -> code-review" in out, "normalized pair should appear"
        assert "worktrees" not in out, "attribution path fragment must not survive"


class TestSkillInvocationProvenance:
    """Only input['skill'] is extracted — never input['args'], which carries
    absolute paths even for an in-scope session's own record. Scoping the read to
    this repo cannot prevent this leak (the path is inside a dir that IS in
    scope), so the extract-only-skill rule is its own supporting invariant and
    needs its own regression test."""

    def test_args_value_never_appears_in_output(self, fake_projects, capsys):
        skill_with_args = {
            "type": "tool_use", "id": "a1", "name": "Skill",
            "input": {"skill": "code-review", "args": "/abs/path/secret-name"},
        }
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[skill_with_args]),
        ])
        _mod.cmd_skill_invocation(_skill_inv_args())
        out = capsys.readouterr().out
        assert "code-review" in out, "the skill name must still be counted"
        assert "secret-name" not in out, "input['args'] value must never reach output"
        assert "/abs/path" not in out, "no fragment of input['args'] may reach output"


class TestSkillInvocationRepoScope:
    """The repo-scoped default (--projects unset) derives this repo's worktree
    slugs via git and fails closed. subprocess.run is stubbed at the module seam
    used by TestPrLink, so no real git repo is needed."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_scoped_default_reads_only_this_repos_dirs(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        theirs = projects / "-other-project"
        mine.mkdir(parents=True)
        theirs.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("x1", "code-review")]),
        ])
        _write_jsonl(theirs / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("x2", "plan-review")]),
        ])
        # cwd "/repo/main" maps via the real _path_to_project_slug to "-repo-main"
        # (only '/' and '.' become '-'); git lists that one worktree. Let the real
        # function run — a hand-copied lambda would freeze it against future drift.
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "in-scope skill must appear"
        assert "plan-review" not in out, "another project's skill must not appear"

    def test_fail_closed_when_git_unavailable(self, fake_projects, monkeypatch, capsys):
        """git failing must exit non-zero with NOTHING on stdout — not fall back to '*'."""
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        captured = capsys.readouterr()
        assert captured.out == "", "fail-closed path must print nothing to stdout"
        assert "refusing" in captured.err.lower(), "should explain the refusal on stderr"

    def test_fail_closed_when_cwd_not_in_worktrees(self, fake_projects, monkeypatch, capsys):
        """git succeeds but resolves a repo unrelated to cwd (GIT_DIR / submodule):
        the cwd-not-in-slugs guard must fail closed."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/a/foreign/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        assert capsys.readouterr().out == "", "fail-closed path must print nothing to stdout"

    def test_explicit_projects_never_invokes_git(self, fake_projects, monkeypatch, capsys):
        """The escape hatch: an explicit --projects must not touch the git derivation."""
        def boom(cmd, *a, **k):
            raise AssertionError("git must not be called when --projects is explicit")
        monkeypatch.setattr(subprocess, "run", boom)
        _write_jsonl(fake_projects / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("e1", "code-review")]),
        ])
        # Must not raise AssertionError from boom, and must produce output.
        _mod.cmd_skill_invocation(argparse.Namespace(projects="*", branches=None, include_subagents=False))
        assert "code-review" in capsys.readouterr().out

    def test_scope_matches_by_literal_name_not_glob(self, tmp_path, monkeypatch, capsys):
        """A derived slug is matched as a literal directory name, not a glob. A
        slug containing a glob metacharacter (from a `*`/`?`/`[` in the home or
        username path) must not widen the read to a sibling project dir the
        wildcard would otherwise match — string equality does not; Path.glob
        would."""
        projects = tmp_path / "projects"
        mine = projects / "-home-u-r*-main"       # in-scope slug carries a '*'
        theirs = projects / "-home-u-rX-main"     # a wildcard on 'mine' would match this
        mine.mkdir(parents=True)
        theirs.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("y1", "code-review")]),
        ])
        _write_jsonl(theirs / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("y2", "plan-review")]),
        ])
        # The real _path_to_project_slug maps "/home/u/r*/main" -> "-home-u-r*-main"
        # ('/' and '.' -> '-'; the '*' is preserved), so no monkeypatch is needed —
        # letting it run is what makes this a real test of the '*' surviving into a
        # slug and still being matched literally.
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/home/u/r*/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/home/u/r*/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/home/u/r*/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "the exact-name dir must be read"
        assert "plan-review" not in out, "a glob metacharacter must not widen the match to a sibling dir"

    def test_multiple_worktrees_all_in_scope(self, tmp_path, monkeypatch, capsys):
        """git worktree list returns main + a linked worktree, and cwd is the
        *linked* one (a non-first entry). Both of this repo's worktree project dirs
        must contribute — not just the first — which a first-match-only bug in the
        slug list would break."""
        projects = tmp_path / "projects"
        main_dir = projects / "-repo"                        # slug of /repo
        linked_dir = projects / "-repo--claude-worktrees-feat"  # slug of the linked worktree
        main_dir.mkdir(parents=True)
        linked_dir.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(main_dir / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", content=[_skill_use("m1", "code-review")]),
        ])
        _write_jsonl(linked_dir / "s.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", content=[_skill_use("l1", "handoff")]),
        ])
        # cwd is the linked worktree (the second git entry), not the main one.
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/.claude/worktrees/feat")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(
                    cmd, 0,
                    self._worktree_porcelain("/repo", "/repo/.claude/worktrees/feat"), "",
                )
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/.claude/worktrees/feat\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_skill_invocation(argparse.Namespace(projects=None, branches=None, include_subagents=False))
        out = capsys.readouterr().out
        assert "code-review" in out, "the main worktree's skill must appear"
        assert "handoff" in out, "the linked worktree's skill must appear"


class TestRepoScopedProjectSlugsGuard:
    """_repo_scoped_project_slugs' three fail-closed sys.exit sites and the
    row-20 containment-plus-identity cwd guard, exercised via a non-
    skill-invocation caller label to cover the newly-generic path."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_fails_closed_when_worktree_list_command_fails(self, monkeypatch, capsys):
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_worktree_list_returns_no_worktrees(self, monkeypatch, capsys):
        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("buckets")
        assert "buckets:" in capsys.readouterr().err

    def test_fails_closed_when_cwd_outside_all_worktrees(self, monkeypatch, capsys):
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/somewhere/else")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err

    def test_cwd_inside_worktree_subdirectory_resolves_successfully(self, monkeypatch):
        """Containment (not slug equality) lets --this-repo run from a subdirectory
        of the worktree root."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested/dir")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        slugs = _mod._repo_scoped_project_slugs("duration")
        assert slugs == [_mod.scope._path_to_project_slug("/repo")]

    def test_sibling_repo_sharing_string_prefix_is_rejected(self, monkeypatch, capsys):
        """cwd inside a sibling path sharing the same string prefix (<repo>-fork)
        must exit 1 — the case bare str.startswith containment would wrongly
        accept."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo-fork/nested")

        def fake_run(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert capsys.readouterr().out == ""

    def test_fails_closed_when_cwd_repo_root_diverges_from_worktree_list(self, monkeypatch, capsys):
        """Identity guard: cwd sits under a listed worktree (containment passes)
        but `git rev-parse --show-toplevel` reports a different root — the case
        an environment override like GIT_WORK_TREE could produce by decoupling
        toplevel reporting from the worktree-list enumeration. Every other test
        in this class returns the same path from both git calls, so this is the
        only test that would catch a typo bug in the identity check itself."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/outer\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "repo root is not among the resolved worktrees" in capsys.readouterr().err

    def test_fails_closed_when_rev_parse_toplevel_command_fails(self, monkeypatch, capsys):
        """The identity-check subprocess call erroring must fail closed, not
        fall back to a machine-wide scope, even though worktree list succeeded."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/nested")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            raise FileNotFoundError("git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._repo_scoped_project_slugs("duration")
        assert "duration:" in capsys.readouterr().err


class TestResolveProjectScope:
    """The scope-dispatch helper behind --projects/--this-repo on the 16
    non-skill-invocation subcommands."""

    def _worktree_porcelain(self, *paths):
        return "\n".join(f"worktree {p}\nHEAD 0000\nbranch refs/heads/x\n" for p in paths)

    def test_this_repo_unset_raises_rather_than_defaulting_to_machine_wide(self, fake_projects):
        """A subparser wired without _add_project_scope_args (missing this_repo
        entirely) must raise, not silently fall through to '*' scope."""
        args = type("A", (), {"projects": "*"})()  # no this_repo attribute at all
        with pytest.raises(AttributeError):
            _mod._resolve_project_scope(args, "buckets")

    def test_called_twice_from_one_args_yields_same_session_list_both_times(self, tmp_path, monkeypatch):
        """cmd_audit_routing's redact pass and main pass both call the scope helper
        on the same args object — a shared/exhausted generator would make the
        second pass silently empty."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = argparse.Namespace(this_repo=True, projects="*")
        iter1, label1 = _mod._resolve_project_scope(args, "audit-routing")
        iter2, label2 = _mod._resolve_project_scope(args, "audit-routing")
        sessions1 = list(iter1)
        sessions2 = list(iter2)
        assert len(sessions1) == 1
        assert len(sessions2) == 1
        assert label1 == label2

    def test_resolved_scope_header_renders_under_this_repo_and_under_a_glob(self, tmp_path, monkeypatch, capsys):
        """The resolved-scope header prints for a non-skill-invocation subcommand
        both under --this-repo and under the default machine-wide glob, so no
        output is scope-ambiguous."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_asst("claude-sonnet-4-6", branch="feat")])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                return subprocess.CompletedProcess(cmd, 0, self._worktree_porcelain("/repo/main"), "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out_repo = capsys.readouterr().out
        assert (
            "BUCKETS SOURCES (this repo (1 project dirs); "
            "1 root (no ~/.claude/transcript-config-dirs declared))"
        ) in out_repo

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out_glob = capsys.readouterr().out
        assert "BUCKETS SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))" in out_glob


class TestBuildParser:
    """build_parser() as a testable seam — the argparse layer without executing
    a subcommand against the real ~/.claude/projects."""

    def test_this_repo_and_projects_mutually_exclusive_on_generic_subcommand(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["buckets", "--this-repo", "--projects", "x"])

    def test_this_repo_and_projects_mutually_exclusive_on_skill_invocation(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["skill-invocation", "--this-repo", "--projects", "x"])

    def test_this_repo_alone_parses_without_error(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["buckets", "--this-repo"])
        assert parsed.projects == "*"
        assert parsed.this_repo is True


class TestIterSessionsOrdering:
    """iter_sessions must yield in a single flat sort over full file paths, NOT
    grouped by directory. cmd_audit_routing's redact-label first pass assigns
    Project-N labels by first-seen order, and this repo's own worktree dirs
    (-home-u-repo vs -home-u-repo--claude-worktrees-b) are the prefix-colliding
    pair whose flat-sort vs dir-grouped order differs."""

    def test_flat_path_order_across_prefix_colliding_dirs(self, tmp_path):
        projects = tmp_path / "projects"
        main_dir = projects / "-home-u-repo"
        wt_dir = projects / "-home-u-repo--claude-worktrees-b"
        main_dir.mkdir(parents=True)
        wt_dir.mkdir(parents=True)
        _write_jsonl(main_dir / "a.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        _write_jsonl(wt_dir / "m.jsonl", [_asst("claude-sonnet-4-6", branch="b")])
        yielded = [jsonl for jsonl, _records in _mod.iter_sessions(projects, "*")]
        # The flat full-path sort places the worktree dir's file first ('-' 0x2D <
        # '/' 0x2F at the byte after the shared prefix); a dir-grouped traversal
        # would place the main dir first. Pin the flat order explicitly.
        expected = sorted(projects.glob("*/*.jsonl"))
        assert yielded == expected, (
            f"iter_sessions must yield in flat full-path sort order; got "
            f"{[p.parent.name + '/' + p.name for p in yielded]}"
        )


class TestPathToProjectSlug:
    """The slug transform and its known, accepted lossiness."""

    def test_main_repo_path(self):
        assert _mod.scope._path_to_project_slug("/home/<user>/repo") == "-home-<user>-repo"

    def test_worktree_path(self):
        assert (_mod.scope._path_to_project_slug("/home/<user>/repo/.claude/worktrees/b")
                == "-home-<user>-repo--claude-worktrees-b")

    def test_known_slug_collision_is_accepted(self):
        """`/` and `.` both map to `-`, so distinct paths can collapse to one slug.
        This is Claude Code's own dir-naming scheme, not ours to change; the repo
        scoping accepts this residual (see _repo_scoped_project_slugs). Pinned so a
        later refactor cannot silently assume injectivity."""
        assert _mod.scope._path_to_project_slug("/home/<user>/a/b") == _mod.scope._path_to_project_slug("/home/<user>/a.b")


# ---------------------------------------------------------------------------
# iter_sessions subagent merge
# ---------------------------------------------------------------------------


class TestIterSessionsSubagentMerge:
    def test_include_subagents_false_ignores_subdir(self, fake_projects):
        """include_subagents=False must not read the subagents/ subdirectory."""
        session_id = "sess-abc"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=False))
        assert len(results) == 1
        _path, records = results[0]
        # Only the main record; sidechain record must not appear.
        assert len(records) == 1
        assert records[0].get("isSidechain") is False

    def test_include_subagents_true_merges_subagent_records(self, fake_projects):
        """include_subagents=True appends subagent file records to the session's record list."""
        session_id = "sess-def"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 2
        types = [r.get("isSidechain") for r in records]
        assert False in types
        assert True in types

    def test_missing_subagent_dir_is_safe(self, fake_projects):
        """A session with no subagents/ directory yields normally with include_subagents=True."""
        _write_jsonl(fake_projects / "lone-session.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # No subagents/ dir for this session.
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 1

    def test_multiple_subagent_files_all_merged(self, fake_projects):
        """All *.jsonl files in the subagents/ dir are merged into one record list."""
        session_id = "sess-multi"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", [
            _asst("claude-haiku-4-5", branch="main", sidechain=True),
        ])
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 3, "expected main + 2 subagent records"
        models = [(r.get("message") or {}).get("model") for r in records]
        assert "claude-opus-4-7" in models
        assert "claude-sonnet-4-6" in models
        assert "claude-haiku-4-5" in models

    def test_corrupt_line_in_subagent_file_skipped_gracefully(self, fake_projects):
        """A corrupt JSON line in a subagent file is skipped; valid records still merge."""
        session_id = "sess-corrupt"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # Write a subagent file with one valid record and one corrupt line.
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-corrupt.jsonl").write_text(
            '{"type":"assistant","isSidechain":true,"gitBranch":"main",'
            '"message":{"model":"claude-sonnet-4-6","content":[],"usage":{}}}\n'
            'THIS IS NOT JSON\n'
        )
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain = [r for r in records if r.get("isSidechain")]
        assert len(sidechain) == 1, "corrupt line skipped; valid sidechain record present"


# ---------------------------------------------------------------------------
# subagents (cmd_subagents)
# ---------------------------------------------------------------------------


def _subagents_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "extra_config_dirs": extra_config_dirs,
    })()


class TestSubagents:
    def test_split_subagent_file_populates_sidechain_row(self, fake_projects, capsys):
        """Sidechain records from a split subagent file appear in the sidechain row."""
        session_id = "sess-split"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="test-branch"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="test-branch", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args(branches="test-branch"))
        out = capsys.readouterr().out
        assert any("main" in ln for ln in out.splitlines()), "expected a main row in output"
        assert any("sidechain" in ln for ln in out.splitlines()), "expected a sidechain row in output"
        # Verify actual counts: 1 opus main turn, 1 sonnet sidechain turn.
        # main row: Branch label present → drop_leading_labels=0
        main_cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert main_cols["Opus"] == "1", "expected 1 opus main turn"
        assert main_cols["Sonnet"] == "0", "expected 0 sonnet main turns"
        # sidechain row: Branch label absent on second row → drop_leading_labels=1
        sidechain_cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                                     drop_leading_labels=1)
        assert sidechain_cols["Opus"] == "0", "expected 0 opus sidechain turns"
        assert sidechain_cols["Sonnet"] == "1", "expected 1 sonnet sidechain turn"

    def test_branch_filter_still_applies_to_output(self, fake_projects, capsys):
        """Branch filter limits the output rows even with split subagent files."""
        session_id = "sess-two-branches"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="branch-a"),
            _asst("claude-opus-4-7", branch="branch-b"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="branch-a", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args(branches="branch-a"))
        out = capsys.readouterr().out
        assert "branch-a" in out
        assert "branch-b" not in out

    def test_multi_record_request_id_group_counts_as_one_turn(self, fake_projects, capsys):
        """Three assistant records sharing one requestId (one per content
        block, as Claude Code writes for a single API call) count as one
        turn in the per-branch table, not three."""
        recs = [
            _asst("claude-opus-4-7", branch="test-branch",
                  content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-opus-4-7", branch="test-branch",
                  content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-opus-4-7", branch="test-branch",
                  content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", recs)
        _mod.cmd_subagents(_subagents_args(branches="test-branch"))
        out = capsys.readouterr().out
        main_cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert main_cols["Opus"] == "1", "three content-block records for one API call count as one turn"


class TestSubagentsToolResultBytes:
    """cmd_subagents' tool-result byte-count dimension: main vs. sidechain,
    per branch, reusing the same tool_result-block walk as cmd_fail_seq and
    the friction-signal helpers."""

    def test_main_thread_tool_result_bytes_attributed_to_main_row(self, fake_projects, capsys):
        """A main-thread (isSidechain unset) tool_result block's content length
        is counted into that branch's main row."""
        text = "x" * 250
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[]),
            _user_msg([_tool_result("t1", text)], branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Bytes"] == str(len(text.encode()))

    def test_sidechain_tool_result_bytes_attributed_to_sidechain_row_not_main(
        self, fake_projects, capsys
    ):
        """A sidechain (isSidechain=True) tool_result block's content length
        is counted into that branch's sidechain row, never the main row."""
        text = "y" * 100
        sidechain_result = _user_msg([_tool_result("t2", text)], branch="main")
        sidechain_result["isSidechain"] = True
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[]),
            _asst("claude-sonnet-4-6", branch="main", sidechain=True, content=[]),
            sidechain_result,
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        main_cols = _table_cols(out, header_contains="Thread", row_contains="main")
        sidechain_cols = _table_cols(
            out, header_contains="Thread", row_contains="sidechain", drop_leading_labels=1
        )
        assert main_cols["Bytes"] == "0"
        assert sidechain_cols["Bytes"] == str(len(text.encode()))

    def test_user_record_without_tool_result_block_contributes_zero_bytes(
        self, fake_projects, capsys
    ):
        """A plain user message (string content, no tool_result block) contributes
        0 bytes — also pins the isinstance(content, list) guard against treating
        a string message's characters as blocks."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[]),
            _user_msg("just a plain user message, no tool_result", branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Bytes"] == "0"

    def test_empty_transcript_produces_no_data_found_without_crash(self, fake_projects, capsys):
        """A transcript file with zero records is skipped by iter_sessions
        (records list is empty) — cmd_subagents prints the existing
        no-data message rather than crashing on an empty session."""
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "No data found." in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_transcript_file_skipped_without_crash(self, fake_projects, capsys):
        """An unreadable transcript file is silently skipped (mirrors
        _read_session_file's existing OSError→[] handling) rather than
        aborting the byte-attribution walk; a sibling readable transcript's
        bytes are still counted correctly."""
        text = "z" * 40
        _write_jsonl(fake_projects / "readable.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[]),
            _user_msg([_tool_result("t3", text)], branch="main"),
        ])
        locked = fake_projects / "locked.jsonl"
        locked.write_text('{"type": "assistant"}\n')
        os.chmod(locked, 0o000)
        try:
            _mod.cmd_subagents(_subagents_args())
        finally:
            os.chmod(locked, 0o644)  # restore before tmp_path teardown
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Bytes"] == str(len(text.encode()))


class TestSubagentsByteGroupingByTool:
    """cmd_subagents' second table: tool-result bytes grouped by the tool
    name that produced them, paired via a tool_use_id -> name index built
    from the same corpus walk."""

    def test_bytes_grouped_under_producing_tool_name(self, fake_projects, capsys):
        text = "r" * 64
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_read_use("t1", "/x")]),
            _user_msg([_tool_result("t1", text)], branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "Read" in out
        assert str(len(text.encode())) in out

    def test_byte_count_uses_utf8_encoded_length_not_character_count(self, fake_projects, capsys):
        """"é" is 1 character but 2 UTF-8 bytes -- every other fixture in
        this class is ASCII, where character count and encoded byte count
        are identical and a len(text) regression would be invisible."""
        text = "é" * 10
        assert len(text) != len(text.encode()), "fixture must actually differ under the two length functions"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_read_use("t1", "/x")]),
            _user_msg([_tool_result("t1", text)], branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Tool", row_contains="Read")
        assert cols["Bytes"] == str(len(text.encode()))

    def test_mcp_tool_names_collapse_into_one_bucket(self, fake_projects, capsys):
        """Two distinct mcp__<server>__<tool> tool names must both land in the
        single _MCP_TOOL_BUCKET_LABEL row — an MCP server name is a
        per-account integration identifier and must never appear raw."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _mcp_use("m1", "github", "search_issues"),
                _mcp_use("m2", "linear", "list_issues"),
            ]),
            _user_msg([_tool_result("m1", "a" * 10), _tool_result("m2", "b" * 20)], branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "mcp__github" not in out
        assert "mcp__linear" not in out
        assert _mod._MCP_TOOL_BUCKET_LABEL in out
        cols = _table_cols(out, header_contains="Tool", row_contains=_mod._MCP_TOOL_BUCKET_LABEL)
        assert cols["Bytes"] == "30"

    def test_tool_result_with_no_matching_tool_use_buckets_as_unknown(self, fake_projects, capsys):
        """A tool_result whose tool_use_id has no matching tool_use in this
        corpus (e.g. the use was in a truncated or unparsed record) still
        contributes its bytes, under an 'unknown' bucket rather than being
        silently dropped."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[]),
            _user_msg([_tool_result("orphan", "z" * 12)], branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "unknown" in out


class TestSubagentsSince:
    """--since Nd filters both of cmd_subagents' reported tables but never
    the corpus-wide counters feeding _warn_if_subagent_format_drift."""

    def test_since_excludes_turns_older_than_window(self, fake_projects, capsys):
        old_ts = "2020-01-01T00:00:00Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=old_ts),
        ])
        _mod.cmd_subagents(_subagents_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_since_boundary_is_inclusive(self, fake_projects, capsys, monkeypatch):
        """A record timestamped exactly at the since-window cutoff (now - 1
        day) is included, not excluded -- the filter compares with `<`, not
        `<=`. time.time() is frozen so the record's timestamp and
        _parse_since_nd_arg's own cutoff are computed from the same instant;
        without that, the two live wall-clock reads would race and the
        record could land a hair on either side of the boundary."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        boundary_ts = datetime.fromtimestamp(fixed_now - 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=boundary_ts),
        ])
        _mod.cmd_subagents(_subagents_args(since="1d"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Opus"] == "1"

    def test_since_excludes_records_missing_timestamp(self, fake_projects, capsys):
        rec = _asst("claude-opus-4-7", branch="main")  # no ts= given -> no timestamp key
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.cmd_subagents(_subagents_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_malformed_since_exits_nonzero_naming_subagents(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_subagents(_subagents_args(since="not-a-window"))
        assert exc_info.value.code == 1
        assert "subagents: --since" in capsys.readouterr().err

    def test_since_does_not_suppress_format_drift_warning(self, fake_projects, capsys):
        """A narrow --since window that excludes this session's only record
        from the reported table must NOT also zero out the corpus-wide drift
        canary: corpus_spawns/corpus_sidechain_turns are counted before the
        --since filter runs, so a real spawns>0/sidechain_turns==0 drift
        signature still fires the warning even though the table below prints
        'No data found.' A buggy implementation that filtered those counters
        by --since too would report corpus_spawns=0 here and silently drop
        the warning — the false negative this test guards against."""
        old_ts = "2020-01-01T00:00:00Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=old_ts, content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_subagents(_subagents_args(since="1d"))
        assert "WARNING" in capsys.readouterr().err


class TestSubagentsMultiRoot:
    """Repeatable --config-dir on subagents, and its disclosure controls --
    mirrors TestSubagentMixMultiRoot's coverage for cmd_subagents' own output
    shape. cmd_subagents carries no --per-session-shaped flag, so there is no
    analogous refusal case to pin here (unlike subagent-mix's --per-session)."""

    def test_two_roots_yield_strictly_more_turns_than_either_alone(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _mod.cmd_subagents(_subagents_args())
        single_root_out = capsys.readouterr().out
        single_root_cols = _table_cols(single_root_out, header_contains="Thread", row_contains="feat")
        assert single_root_cols["Opus"] == "1"

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _mod.cmd_subagents(_subagents_args(extra_config_dirs=[str(acct_b)]))
        multi_root_out = capsys.readouterr().out
        total_opus = _sum_column_across_rows(
            multi_root_out, header_contains="Thread", label="Opus", row_prefix="account-"
        )
        assert total_opus > int(single_root_cols["Opus"])
        # Single-root label was flat ("feat"); two-root labels are namespaced.
        assert "account-1/branch-1" in multi_root_out
        assert "account-2/branch-1" in multi_root_out

    def test_colliding_branch_names_across_roots_get_distinct_redacted_labels(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """Two roots each with their own "main" branch must not collapse
        into one row, and neither raw branch name may appear in output."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "account-1/branch-1" in out
        assert "account-2/branch-1" in out
        assert "account-1/branch-1" != "account-2/branch-1"

    def test_multi_root_stamps_do_not_publish_banner_on_stdout_and_stderr(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        _mod.cmd_subagents(_subagents_args(extra_config_dirs=[str(acct_b)]))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_single_root_omits_do_not_publish_banner(self, fake_projects, capsys):
        """The allow-path counterpart to the fire test above -- mirrors
        cost's own test_default_redact_omits_do_not_publish_banner. Without
        this, a broken/inverted multi_root guard (banner always fires, or
        never fires) has no test signal in either direction."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.err

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
            _asst("claude-opus-4-7", branch="feat"),
        ])

        extra = tmp_path / "aaa-extra"
        extra_proj = extra / "projects" / "-home-user-extra-repo"
        extra_proj.mkdir(parents=True)
        _write_jsonl(extra_proj / "sess-extra.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
            _asst("claude-opus-4-7", branch="feat"),
        ])

        _mod.cmd_subagents(_subagents_args(extra_config_dirs=[str(extra)]))
        out = capsys.readouterr().out
        account_1 = _table_cols(out, header_contains="Thread", row_contains="account-1/branch-1")
        account_2 = _table_cols(out, header_contains="Thread", row_contains="account-2/branch-1")
        # "aaa-extra" (2 opus turns) resolved-path-sorts before "zzz-active"
        # (1 opus turn) despite being scanned second -- account-1 must be
        # the extra root's row.
        assert account_1["Opus"] == "2"
        assert account_2["Opus"] == "1"


class TestSkillPairSubagentFile:
    def test_follower_in_subagent_file_increments_sidechain_only(self, fake_projects, capsys):
        """Follower skill in a split subagent file (isSidechain=True) counts as Side, not Main."""
        session_id = "sess-pair"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"
        assert cols["Side"] == "1"


# ---------------------------------------------------------------------------
# format-drift canary
# ---------------------------------------------------------------------------


class TestFormatDriftCanary:
    def test_drift_warning_emitted_when_spawns_but_no_sidechain_turns(
        self, fake_projects, capsys
    ):
        """Spawn tool_use with no subagent files → WARNING on stderr."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "isSidechain" in captured.err
        # The warning must not bleed into stdout table rows.
        assert "WARNING" not in captured.out

    def test_no_warning_when_no_spawns(self, fake_projects, capsys):
        """No Agent/Task tool_uses → no drift warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main"),
        ])
        _mod.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_no_warning_old_inlined_format(self, fake_projects, capsys):
        """Agent spawn + isSidechain records inlined in the top-level file → no warning."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-backend-engineer"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _mod.cmd_subagents(_subagents_args())
        assert "WARNING" not in capsys.readouterr().err

    def test_drift_warning_also_fires_in_skill_pair(self, fake_projects, capsys):
        """cmd_skill_pair also emits the drift warning when spawns have no sidechain turns."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _agent_use("a1", "staff-backend-engineer"),
            ]),
        ])
        _mod.cmd_skill_pair(_skill_pair_args())
        assert "WARNING" in capsys.readouterr().err

    def test_subagent_records_without_issidechain_field_treated_as_main_thread(
        self, fake_projects, capsys
    ):
        """Subagent records missing the isSidechain field are counted as main-thread.

        This documents the failure mode if the transcript format drifts to drop
        isSidechain — subagent turns silently appear in the main row, not sidechain.
        """
        session_id = "sess-no-sidechain-field"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "check-runner"),
            ]),
        ])
        # Subagent file record WITHOUT isSidechain field.
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        rec = _asst("claude-sonnet-4-6", branch="main", sidechain=True)
        del rec["isSidechain"]  # simulate format drift
        (subdir / "agent-drifted.jsonl").write_text(json.dumps(rec) + "\n")
        _mod.cmd_subagents(_subagents_args())
        captured = capsys.readouterr()
        sidechain_lines = [ln for ln in captured.out.splitlines() if "sidechain" in ln]
        # Without isSidechain=True, the subagent record is not a sidechain turn.
        assert not sidechain_lines, (
            "subagent records without isSidechain field must not appear in sidechain row"
        )
        # The drift canary fires: spawns=1 (main-thread Agent), sidechain_turns=0
        # (subagent record has no isSidechain field → not counted as sidechain).
        assert "WARNING" in captured.err


# ---------------------------------------------------------------------------
# subagent format contract
# ---------------------------------------------------------------------------


class TestSubagentFormatContract:
    """Pin the on-disk subagent transcript format the reader depends on.

    These assertions describe what the code EXPECTS from the JSONL on disk.
    If any of these fail on a real corpus run (not in CI), the transcript format
    has drifted — update the reader (and iter_sessions) before trusting metric output.
    """

    def test_subagent_dir_path_convention(self, fake_projects):
        """Subagent files live at <session_id>/subagents/*.jsonl relative to project dir."""
        session_id = "abc123"
        agent_id = "agent-xyz"
        _write_subagent_jsonl(
            fake_projects, session_id, agent_id,
            [_asst("claude-sonnet-4-6", sidechain=True)]
        )
        subdir = fake_projects / session_id / _mod.SUBAGENT_SUBDIR
        assert subdir.is_dir(), f"Expected subagent dir at {subdir}"
        files = list(subdir.glob("*.jsonl"))
        assert len(files) == 1

    def test_subagent_assistant_record_carries_required_fields(self, fake_projects):
        """Assistant-type subagent records carry isSidechain, gitBranch, type, message.model
        when read back through iter_sessions with include_subagents=True."""
        session_id = "contract-sess"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _write_subagent_jsonl(
            fake_projects, session_id, "agent-contract",
            [_asst("claude-sonnet-4-6", branch="feat", sidechain=True)]
        )
        results = list(_mod.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain_records = [r for r in records if r.get("isSidechain")]
        assert len(sidechain_records) == 1, "expected one sidechain record from subagent file"
        rec = sidechain_records[0]
        assert rec.get("isSidechain") is True
        assert rec.get("gitBranch") == "feat"
        assert rec.get("type") == "assistant"
        assert (rec.get("message") or {}).get("model") == "claude-sonnet-4-6"

    def test_user_msg_helper_has_no_message_model(self):
        """User-type records from _user_msg() have no message.model.

        This documents the helper's shape, not an iter_sessions invariant — user
        records in subagent files are not relied on by the script's analysis paths.
        """
        rec = _user_msg("some content", branch="feat")
        # isSidechain may be absent on user records; message.model must not be asserted on them
        assert rec.get("type") == "user"
        assert (rec.get("message") or {}).get("model") is None


# ---------------------------------------------------------------------------
# judgment-pair
# ---------------------------------------------------------------------------


def _judgment_pair_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    skills: str | None = None,
    truncate_chars: int = 1000,
    out: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "skills": skills or ",".join(_mod.REVIEW_SKILLS),
        "truncate_chars": truncate_chars,
        "out": out,
    })()


def _skill_use_rec(skill: str, ts: str, branch: str = "main", tool_id: str = "s1") -> dict:
    """Main-thread assistant record containing a Skill tool_use.

    Pass distinct tool_id values when two invocations appear in the same session
    to match production transcript invariants (tool_use ids are unique per block).
    """
    return _asst("claude-sonnet-4-6", branch=branch, ts=ts, content=[_skill_use(tool_id, skill)])


def _review_asst(text: str, ts: str, branch: str = "main") -> dict:
    """Main-thread assistant record with plain text content."""
    return _asst("claude-sonnet-4-6", branch=branch, ts=ts, content=[{"type": "text", "text": text}])


def _user_reply(text: str, branch: str = "main") -> dict:
    """Plain user message (fresh user prompt)."""
    return _user_msg(text, branch=branch)


class TestIsFreshUserPrompt:
    def test_plain_text_user_record_returns_true(self):
        rec = _user_msg("Please fix that", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is True

    def test_tool_result_bearing_record_returns_false(self):
        rec = _user_msg([_tool_result("t1", "some result")], branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_ismeta_true_returns_false(self):
        rec = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "injected meta"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_issidechain_true_returns_false(self):
        rec = {
            "type": "user",
            "isSidechain": True,
            "message": {"content": "sidechain msg"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_iscompactsummary_true_returns_false(self):
        rec = {
            "type": "user",
            "isCompactSummary": True,
            "message": {"content": "summary"},
            "gitBranch": "main",
        }
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_empty_text_content_returns_false(self):
        rec = _user_msg("", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False

    def test_assistant_record_returns_false(self):
        rec = _asst("claude-sonnet-4-6", branch="main")
        assert _mod._is_fresh_user_prompt(rec) is False


class TestJudgmentPair:
    def test_single_turn_review_emits_pair(self, fake_projects, capsys):
        """Invocation record followed by one review text turn and a user reply emits one pair."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Found 3 issues with the auth middleware.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Thanks, I'll fix those auth issues."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Found 3 issues with the auth middleware." in out
        assert "Thanks, I'll fix those auth issues." in out

    def test_multi_turn_review_captures_last_text_turn(self, fake_projects, capsys):
        """Among multiple assistant turns in the window, the last with non-empty text is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Preamble: reviewing now.", "2026-05-20T10:00:30.000Z"),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:45.000Z",
                  content=[_bash_use("b1", "ls")]),  # tool-only, no text
            _review_asst("Synthesis: two real issues found in cache invalidation.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Got it, will fix cache invalidation."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Synthesis: two real issues found in cache invalidation." in out
        assert "Preamble: reviewing now." not in out

    def test_skill_outside_set_excluded(self, fake_projects, capsys):
        """Invocation of a skill not in the default set produces no pair."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:00.000Z",
                  content=[_skill_use("s1", "handoff")]),
            _review_asst("Handoff summary.", "2026-05-20T10:01:00.000Z"),
            _user_reply("OK."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_skills_override_filters_correctly(self, fake_projects, capsys):
        """--skills plan-review causes only the plan-review pair to be emitted."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("plan-review", "2026-05-20T10:00:00.000Z", tool_id="s1"),
            _review_asst("Plan looks good but step 3 is ambiguous.", "2026-05-20T10:01:00.000Z"),
            _user_reply("I'll clarify step 3."),
            _skill_use_rec("code-review", "2026-05-20T10:10:00.000Z", tool_id="s2"),
            _review_asst("Code has an off-by-one in the loop.", "2026-05-20T10:11:00.000Z"),
            _user_reply("Fixing the loop now."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(skills="plan-review"))
        out = capsys.readouterr().out
        assert "Plan looks good but step 3 is ambiguous." in out
        assert "Code has an off-by-one in the loop." not in out
        assert "I'll clarify step 3." in out

    def test_sidechain_invocation_excluded(self, fake_projects, capsys):
        """Skill tool_use on a sidechain assistant record is not detected as an invocation."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:00.000Z",
                  sidechain=True, content=[_skill_use("s1", "code-review")]),
            _review_asst("Some review text.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Thanks."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_tool_result_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record bearing tool_result blocks is skipped; the next real user prompt is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Missing null check in parser.", "2026-05-20T10:01:00.000Z"),
            _user_msg([_tool_result("t1", "tool output text")]),  # tool result — not a fresh prompt
            _user_reply("I'll add the null check."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "I'll add the null check." in out
        assert "tool output text" not in out

    def test_ismeta_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record with isMeta=True is skipped; the next real user turn is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Security issue in token validation.", "2026-05-20T10:01:00.000Z"),
            {"type": "user", "isMeta": True,
             "message": {"content": "meta injection"}, "gitBranch": "main"},
            _user_reply("I'll harden the token validation."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "I'll harden the token validation." in out
        assert "meta injection" not in out

    def test_iscompactsummary_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record with isCompactSummary=True is skipped; the next real user turn is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Duplicate logic in helper.", "2026-05-20T10:01:00.000Z"),
            {"type": "user", "isCompactSummary": True,
             "message": {"content": "compaction summary text"}, "gitBranch": "main"},
            _user_reply("Extracting the duplicate now."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Extracting the duplicate now." in out
        assert "compaction summary text" not in out

    def test_since_filter_excludes_old_invocation(self, fake_projects, capsys):
        """Invocation with timestamp before --since is excluded."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-04-01T10:00:00.000Z"),
            _review_asst("Old review output.", "2026-04-01T10:01:00.000Z"),
            _user_reply("Response to old review."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(since="2026-05-01"))
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_until_filter_excludes_future_invocation(self, fake_projects, capsys):
        """Invocation with timestamp after --until is excluded."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-06-20T10:00:00.000Z"),
            _review_asst("Future review output.", "2026-06-20T10:01:00.000Z"),
            _user_reply("Response to future review."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(until="2026-06-01"))
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_missing_timestamp_no_filter_emits_sentinel_date(self, fake_projects, capsys):
        """Invocation with no timestamp field and no date filter emits pair with '?' date label."""
        invocation_no_ts = {
            "type": "assistant",
            "message": {"content": [_skill_use("sv1", "code-review")]},
            "gitBranch": "main",
            # deliberately no "timestamp" key
        }
        _write_jsonl(fake_projects / "sess.jsonl", [
            invocation_no_ts,
            _review_asst("Review emitted despite missing timestamp.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Understood."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "Review emitted despite missing timestamp." in out
        assert "Understood." in out
        assert "· ?" in out  # sentinel date label from the missing-timestamp guard

    def test_no_user_response_emits_sentinel(self, fake_projects, capsys):
        """When no user turn follows the review window, the sentinel message is shown."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Looks good overall.", "2026-05-20T10:01:00.000Z"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "(no user response — end of session)" in out

    def test_truncate_chars_truncates_review_output(self, fake_projects, capsys):
        """Review text longer than --truncate-chars is cut and suffixed with ellipsis."""
        long_review = "X" * 50
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst(long_review, "2026-05-20T10:01:00.000Z"),
            _user_reply("Got it."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(truncate_chars=10))
        out = capsys.readouterr().out
        assert "XXXXXXXXXX…" in out
        assert "X" * 11 not in out

    def test_branches_filter(self, fake_projects, capsys):
        """--branches restricts output to sessions on the matching branch."""
        _write_jsonl(fake_projects / "sess-main.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", branch="main"),
            _review_asst("Main branch review.", "2026-05-20T10:01:00.000Z", branch="main"),
            _user_reply("Main branch reply.", branch="main"),
        ])
        _write_jsonl(fake_projects / "sess-feat.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", branch="feat"),
            _review_asst("Feature branch review.", "2026-05-20T10:01:00.000Z", branch="feat"),
            _user_reply("Feature branch reply.", branch="feat"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(branches="feat"))
        out = capsys.readouterr().out
        assert "Feature branch review." in out
        assert "Main branch review." not in out

    def test_branches_filter_selects_by_invocation_branch_across_a_branch_change(self, fake_projects, capsys):
        """--branches filters on the invocation record's own gitBranch, not a single
        session-wide branch — a session whose branch changes between the invocation
        and the user response is selected by where the invocation itself happened."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", branch="feat"),
            _review_asst("Review while still on feat.", "2026-05-20T10:01:00.000Z", branch="feat"),
            _user_reply("Reply after switching to main.", branch="main"),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(branches="feat"))
        out_feat = capsys.readouterr().out
        assert "Review while still on feat." in out_feat

        _mod.cmd_judgment_pair(_judgment_pair_args(branches="main"))
        out_main = capsys.readouterr().out
        assert "No judgment pairs found." in out_main

    def test_out_path_writes_to_file(self, fake_projects, capsys, tmp_path):
        """--out writes the pair block to the specified file instead of stdout,
        with the resolved-scope header prepended so the file stays
        self-documenting about its scope even pasted elsewhere without the
        original terminal output."""
        out_file = tmp_path / "output.txt"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Logic error in retry loop.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Will fix the retry logic."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        out_stdout = capsys.readouterr().out
        assert out_stdout == ""  # nothing printed to stdout
        file_content = out_file.read_text()
        assert (
            file_content.splitlines()[0]
            == "JUDGMENT PAIR SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))"
        )
        assert "Logic error in retry loop." in file_content
        assert "Will fix the retry logic." in file_content

    def test_no_pairs_found_prints_message(self, fake_projects, capsys):
        """When no matching sessions exist, 'No judgment pairs found.' is printed."""
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_out_path_with_no_pairs_prints_to_stdout(self, fake_projects, capsys, tmp_path):
        """--out set but no pairs found: sentinel goes to stdout, no file is created."""
        out_file = tmp_path / "output.txt"
        _mod.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        out_stdout = capsys.readouterr().out
        assert "No judgment pairs found." in out_stdout
        assert not out_file.exists()

    def test_back_to_back_invocations_get_distinct_outputs(self, fake_projects, capsys):
        """Two consecutive skill invocations each get their own distinct review output block."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", tool_id="s1"),
            _review_asst("First review: missing validation.", "2026-05-20T10:01:00.000Z"),
            _skill_use_rec("plan-review", "2026-05-20T10:02:00.000Z", tool_id="s2"),
            _review_asst("Second review: plan is incomplete.", "2026-05-20T10:03:00.000Z"),
            _user_reply("Thanks for both reviews."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        # Both review texts are present.
        assert "First review: missing validation." in out
        assert "Second review: plan is incomplete." in out
        # The first invocation's window closes at the second invocation, so its user
        # response is the sentinel (no user prompt between the two skill calls).
        assert "(no user response — end of session)" in out
        # The second invocation's window closes at the user reply.
        assert "Thanks for both reviews." in out
        # Sanity: two block headers (structural, supplements content checks above).
        assert out.count("--- REVIEW OUTPUT") == 2

    def test_back_to_back_invocations_with_no_text_between(self, fake_projects, capsys):
        """Two skill invocations with no assistant turns between: first emits '(no review text found)'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z", tool_id="s1"),
            _skill_use_rec("plan-review", "2026-05-20T10:01:00.000Z", tool_id="s2"),
            _review_asst("Plan is missing rollback steps.", "2026-05-20T10:02:00.000Z"),
            _user_reply("Adding rollback steps now."),
        ])
        _mod.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        # First invocation window is empty (no assistant turns before the next invocation).
        assert "(no review text found)" in out
        # Second invocation window contains the review text.
        assert "Plan is missing rollback steps." in out


# ---------------------------------------------------------------------------
# friction-count
# ---------------------------------------------------------------------------


def _friction_count_args(
    transcript: str, *, json_output: bool = False, checkpoint: str | None = None
) -> object:
    return type("A", (), {"transcript": transcript, "json": json_output, "checkpoint": checkpoint})()


class TestFrictionCount:
    def test_legacy_denial_shape_counted(self, fake_projects, capsys):
        """A legacy attachment/hook_blocking_error record counts as one denial."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [_hook_deny("require-code-review")])
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_current_format_denial_shape_counted(self, fake_projects, capsys):
        """A current-format is_error tool_result with the denial signature counts as one denial."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [_hook_deny_current("Commit blocked by code-review gate.")])
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_denial_deduped_across_shapes_by_tool_use_id(self, fake_projects, capsys):
        """A denial recorded as both an attachment and its is_error twin (same
        tool_use_id) collapses to one denial event — mirrors cmd_review_trace's dedup."""
        path = fake_projects / "sess.jsonl"
        attach = _hook_deny("worktree")  # toolUseID == "toolu_worktree"
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree",
        )
        _write_jsonl(path, [attach, twin])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 1

    def test_distinct_denials_each_counted(self, fake_projects, capsys):
        """Two current-format denials with distinct tool_use_ids count as two denials."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 2

    def test_counts_all_branches_regardless_of_gitBranch(self, fake_projects, capsys):
        """friction-count is flat per-file — struggle signals on two different branches
        in the same transcript both count; this is intentional (no branch filter)."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat-a"),
            _user_msg([{"type": "text", "text": "hold on, that's wrong"}], branch="feat-a"),
            _asst("claude-sonnet-4-6", branch="feat-b"),
            _user_msg([{"type": "text", "text": "try again please"}], branch="feat-b"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 2

    def test_failed_test_pairing_counts_only_failing_runs(self, fake_projects, capsys):
        """A failing run (N failed > 0) counts; a passing run does not."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["failed_test_runs"] == 1

    def test_struggle_phrase_match_counted(self, fake_projects, capsys):
        """A user turn containing a STRUGGLE_PHRASES entry counts as one struggle turn."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg([{"type": "text", "text": "no not that, try again"}], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 1

    def test_isSidechain_records_skipped(self, fake_projects, capsys):
        """isSidechain denial/failed-test/struggle records are excluded from every signal."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat", sidechain=True,
                  content=[_bash_use("t1", "pytest")]),
            {
                **_user_msg([_tool_result("t1", "3 failed")], branch="feat"),
                "isSidechain": True,
            },
            {
                **_user_msg([{"type": "text", "text": "hold on, try again"}], branch="feat"),
                "isSidechain": True,
            },
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals == {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0, "composite": 0}

    def test_empty_transcript_is_zero(self, fake_projects, capsys):
        """An empty transcript file produces a composite of 0, no crash."""
        path = fake_projects / "sess.jsonl"
        path.write_text("")
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "0"

    def test_malformed_line_skipped_gracefully(self, fake_projects, capsys):
        """A malformed JSONL line is silently skipped; valid lines around it still count."""
        path = fake_projects / "sess.jsonl"
        good_denial = json.dumps(_hook_deny_current("Commit blocked by code-review gate."))
        path.write_text(f"{good_denial}\nnot json at all\n{{broken\n")
        _mod.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_json_breakdown_and_composite_equals_sum(self, fake_projects, capsys):
        """--json emits all four keys; composite equals the sum of the three signals."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny("require-code-review"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "2 failed")], branch="feat"),
            _user_msg([{"type": "text", "text": "still failing, hold on"}], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert set(signals.keys()) == {"denials", "failed_test_runs", "struggle_turns", "composite"}
        assert signals["denials"] == 1
        assert signals["failed_test_runs"] == 1
        assert signals["struggle_turns"] == 1
        assert signals["composite"] == 3
        assert signals["composite"] == (
            signals["denials"] + signals["failed_test_runs"] + signals["struggle_turns"]
        )

    def test_toolDenialKind_field_ignored_by_signature_matching_count(self, fake_projects, capsys):
        """friction-count's denial signal counts purely by is_error + message-text
        signature match (hook_denial_key), regardless of a record's own
        toolDenialKind value — hook_denial_key never reads that field. Of the 8
        records below, 7 carry a signature-matching is_error tool_result (or the
        legacy attachment shape) and count: the plain attachment denial, the
        signature-text record with no toolDenialKind, and the five kind-value
        records (permission-rule, user-rejected, automode-blocked,
        automode-unavailable, interrupted) whose text also matches the
        signature. The 8th record — permission-rule kind, ordinary non-signature
        error text — does not count."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny("require-code-review"),
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_nokind"),
            _hook_deny_current(
                "Commit blocked by code-review gate.", tool_id="toolu_pr",
                tool_denial_kind="permission-rule",
            ),
            _hook_deny_current(
                "Commit blocked by code-review gate.", tool_id="toolu_ur",
                tool_denial_kind="user-rejected",
            ),
            _hook_deny_current(
                "Commit blocked by code-review gate.", tool_id="toolu_ab",
                tool_denial_kind="automode-blocked",
            ),
            _hook_deny_current(
                "Commit blocked by code-review gate.", tool_id="toolu_au",
                tool_denial_kind="automode-unavailable",
            ),
            _hook_deny_current(
                "Commit blocked by code-review gate.", tool_id="toolu_int",
                tool_denial_kind="interrupted",
            ),
            _hook_deny_current(
                "npm ERR! command failed with exit code 1", tool_id="toolu_pr_nosig",
                tool_denial_kind="permission-rule",
            ),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 7


# ---------------------------------------------------------------------------
# friction-count cross-path equality — pins hook_denial_key and the failed-test
# signal against the two subcommands they must never silently drift from.
# ---------------------------------------------------------------------------


class TestFrictionCountCrossPathEquality:
    def test_denial_count_matches_review_trace(self, fake_projects, capsys):
        """friction-count's denial count over one file equals cmd_review_trace's denial
        count over that same session. No isSidechain denial records in this fixture,
        so cmd_review_trace's (unfiltered) and friction-count's (isSidechain-filtered)
        counts are directly comparable. This fixture carries no toolDenialKind field
        at all, so the equality holds only on the text-signature (denial-only) path —
        see test_review_trace_friction_line_diverges_from_friction_count_denial_count
        below for a record whose toolDenialKind makes the two counts diverge."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny("require-code-review"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        trace_out = capsys.readouterr().out
        m = re.search(r"denials=(\d+)", trace_out)
        assert m is not None, f"no denials= marker found in review-trace output: {trace_out!r}"
        review_trace_denials = int(m.group(1))
        assert review_trace_denials == 2

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == review_trace_denials

    def test_review_trace_friction_line_diverges_from_friction_count_denial_count(self, fake_projects, capsys):
        """A record carrying a non-gate toolDenialKind and non-signature-matching
        text is invisible to friction-count's denial signal (hook_denial_key
        never matches it) but produces review-trace's own `friction` line, not a
        `denial` line — the two surfaces intentionally diverge once toolDenialKind
        data exists, unlike the kind-free fixture in the test above."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny_current(
                "Request interrupted by user for tool use", tool_id="toolu_interrupt",
                tool_denial_kind="interrupted",
            ),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        trace_out = capsys.readouterr().out
        event_lines = [ln for ln in trace_out.splitlines() if ln.startswith("  [")]
        assert len(event_lines) == 1
        assert "friction" in event_lines[0]
        assert "denial" not in event_lines[0]
        assert "denials=0" in trace_out

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 0

    def test_failed_test_count_matches_fail_seq_failing_subtotal(self, fake_projects, capsys):
        """friction-count's failed-test count equals cmd_fail_seq's failing-run
        subtotal (f > 0), not its total matched-run count — cmd_fail_seq records
        every matched run including passing ones."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t2", "pytest")]),
            _user_msg([_tool_result("t2", "all passed")], branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t3", "pytest")]),
            _user_msg([_tool_result("t3", "1 failed, 19 passed")], branch="feat"),
        ])
        fail_seq_args = type("A", (), {"branches": "feat", "projects": "*", "this_repo": False})()
        _mod.cmd_fail_seq(fail_seq_args)
        fail_seq_out = capsys.readouterr().out
        m = re.search(r"Total runs: (\d+)\s+Failing: (\d+)", fail_seq_out)
        assert m is not None, f"no Total runs/Failing marker found in fail-seq output: {fail_seq_out!r}"
        total_runs, failing_subtotal = int(m.group(1)), int(m.group(2))
        assert total_runs == 3
        assert failing_subtotal == 2

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["failed_test_runs"] == failing_subtotal
        assert signals["failed_test_runs"] != total_runs


# ---------------------------------------------------------------------------
# friction-count --checkpoint — incremental byte-offset scan.
# ---------------------------------------------------------------------------


class TestFrictionCountCheckpoint:
    def test_first_call_with_no_checkpoint_scans_from_zero_and_creates_file(self, fake_projects, capsys):
        """No checkpoint file yet: full scan from offset 0, and a checkpoint
        file is written afterward recording the offset and per-signal totals."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])
        assert not checkpoint.exists()

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

        assert checkpoint.exists()
        state = json.loads(checkpoint.read_text())
        assert state["offset"] == path.stat().st_size
        assert state["totals"] == {"denials": 1, "failed_test_runs": 0, "struggle_turns": 0}

    def test_incremental_call_matches_full_rescan_baseline(self, fake_projects, capsys):
        """A second checkpointed call after appending new lines re-scans only
        the appended bytes and returns the cumulative composite — asserted
        against a full-rescan baseline (no --checkpoint) over the same final
        file, to catch incremental/full-scan drift."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _asst("claude-sonnet-4-6", branch="feat", content=[_bash_use("t1", "pytest")]),
            _user_msg([_tool_result("t1", "3 failed, 17 passed")], branch="feat"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        first_composite = int(capsys.readouterr().out.strip())
        assert first_composite == 2  # 1 denial + 1 failed test run

        with path.open("a") as fh:
            for rec in (
                _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
                _user_msg([{"type": "text", "text": "hold on, try again"}], branch="feat"),
            ):
                fh.write(json.dumps(rec) + "\n")

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        incremental_composite = int(capsys.readouterr().out.strip())
        assert incremental_composite == 4  # 2 denials + 1 failed test run + 1 struggle turn
        # Non-vacuous: the incremental call actually counted the newly appended signals.
        assert incremental_composite != first_composite

        _mod.cmd_friction_count(_friction_count_args(str(path)))  # no checkpoint: full rescan
        baseline_composite = int(capsys.readouterr().out.strip())
        assert incremental_composite == baseline_composite

    def test_malformed_checkpoint_json_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A checkpoint file that isn't valid JSON is treated as absent: full
        rescan from offset 0, no error raised."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text("not json at all")
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_missing_totals_field_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A checkpoint file missing the 'totals' key is treated as malformed
        as a whole (not partially trusted): full rescan from offset 0, not a
        scan starting from the recorded offset with zero totals."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({"offset": 5}))  # no "totals" key
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_negative_offset_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A negative offset is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": -1,
            "totals": {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_non_int_offset_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A string offset is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": "5",
            "totals": {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_bool_offset_falls_back_to_full_rescan(self, fake_projects, capsys):
        """Python's bool subclasses int (isinstance(True, int) is True); a
        checkpoint with offset: true must still be rejected as malformed,
        not silently treated as offset 1."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": True,
            "totals": {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_negative_totals_value_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A negative per-key totals value is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": 5,
            "totals": {"denials": -1, "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_non_int_totals_value_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A string per-key totals value is malformed: full rescan from offset 0."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": 5,
            "totals": {"denials": "1", "failed_test_runs": 0, "struggle_turns": 0},
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_totals_missing_one_of_three_keys_falls_back_to_full_rescan(
        self, fake_projects, capsys
    ):
        """A 'totals' dict present but missing exactly one of the three
        required keys (struggle_turns here) is malformed as a whole: full
        rescan from offset 0, not a partial trust of the two present keys."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "offset": 5,
            "totals": {"denials": 1, "failed_test_runs": 2},  # no "struggle_turns"
        }))
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_checkpoint_offset_beyond_transcript_size_falls_back_to_full_rescan(self, fake_projects, capsys):
        """A checkpoint whose stored offset exceeds the transcript's current
        size (e.g. the transcript was truncated/rewritten while the
        session-keyed checkpoint persisted) must not seek past EOF and
        freeze forever at the checkpoint's stale totals — it must fail open
        to a full rescan, exactly like a malformed checkpoint."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])
        # A well-formed checkpoint whose offset is larger than the actual
        # transcript file's current byte size.
        checkpoint.write_text(json.dumps({
            "offset": path.stat().st_size + 1000,
            "totals": {"denials": 7, "failed_test_runs": 0, "struggle_turns": 0},
        }))

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        out = capsys.readouterr().out.strip()
        assert out == "1", "a stale offset must trigger a full rescan, not a frozen stale composite"

    def test_checkpoint_offset_never_rereads_consumed_bytes(self, fake_projects, capsys):
        """Calling friction-count again with no new appended lines must not
        double-count the already-consumed denial — this is only true if the
        checkpoint's stored offset actually gates re-reading those bytes."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])
        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        assert capsys.readouterr().out.strip() == "1"

        # No new lines appended — a second call over the unchanged file must
        # return the same cumulative composite, not double it.
        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        second_out = capsys.readouterr().out.strip()
        assert second_out == "1", "re-reading already-consumed bytes would double-count the denial"

    def test_partial_trailing_line_not_consumed_until_terminated(self, fake_projects, capsys):
        """A transcript with a trailing unterminated line (as if the harness is
        actively writing it) only advances the checkpoint offset to the last
        complete line boundary. The partial line's content is picked up whole,
        exactly once, on the next call once it's terminated — not dropped, not
        double-read."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        complete_denial = json.dumps(
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a")
        )
        partial_denial = json.dumps(
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b")
        )
        # The second record's bytes have no trailing newline, as if the
        # transcript writer stopped mid-record.
        path.write_text(f"{complete_denial}\n{partial_denial[:20]}")

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        first_out = capsys.readouterr().out.strip()
        assert first_out == "1", "only the complete first line should be counted"
        state = json.loads(checkpoint.read_text())
        assert state["offset"] == len(complete_denial) + 1, (
            "offset must stop at the complete-line boundary, not consume the partial trailing bytes"
        )

        # Complete and terminate the partial line, simulating the writer
        # finishing the record on the next turn.
        with path.open("a") as fh:
            fh.write(partial_denial[20:] + "\n")

        _mod.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        second_out = capsys.readouterr().out.strip()
        assert second_out == "2", "the now-terminated second denial is picked up whole, not double-read"

    def test_checkpoint_and_json_combined_returns_same_four_keys_as_non_checkpoint_json(
        self, fake_projects, capsys
    ):
        """--checkpoint and --json together (a real, reachable CLI combination
        the hook itself never exercises) must build the same four-key shape
        as the non-checkpoint --json path — this dict is constructed
        differently (**running_totals spread) than the non-checkpoint path's
        _friction_signals return, so a key-naming/ordering regression here
        would not be caught by any non-checkpoint --json test."""
        path = fake_projects / "sess.jsonl"
        checkpoint = fake_projects / "checkpoint.json"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
        ])

        _mod.cmd_friction_count(
            _friction_count_args(str(path), checkpoint=str(checkpoint), json_output=True)
        )
        checkpoint_signals = json.loads(capsys.readouterr().out)

        _mod.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        non_checkpoint_signals = json.loads(capsys.readouterr().out)

        assert set(checkpoint_signals.keys()) == set(non_checkpoint_signals.keys())
        assert set(checkpoint_signals.keys()) == {
            "denials", "failed_test_runs", "struggle_turns", "composite",
        }
        assert checkpoint_signals == non_checkpoint_signals


# ---------------------------------------------------------------------------
# _denial_hook_label enumeration — pins _DENIAL_HOOK_LABELS against each
# hook's real deny-path wording, one case per hooks/*.sh label.
# ---------------------------------------------------------------------------


class TestDenialHookLabelEnumeration:
    """Feeds each hook's deny-path wording (hand-transcribed verbatim from
    hooks/*.sh, not driven through the real hook) through _denial_hook_label
    and asserts the label it produces is a member of _DENIAL_HOOK_LABELS —
    not _DENY_SUMMARY_UNMATCHED_HOOK. A hook's wording changing without
    updating both this fixture and the enumeration set fails the affected
    case, so drift here is caught rather than silently stale — but a hook
    changing its wording to something this fixture never updates to match
    would not be caught, since no real hook process ever runs.
    TestDenialHookLabelEnumerationRealHooks below closes that gap for all
    but three rows by driving the real hook subprocess instead."""

    @pytest.mark.parametrize("hook_file,message,expected_label", [
        ("block-gh-pr-merge.sh:49",
         "Blocked by gh-pr-merge gate: could not source _lib.sh.",
         "gh-pr-merge"),
        ("check-claude-md-length.sh:42",
         "Blocked by CLAUDE.md length gate: could not source _lib.sh.",
         "CLAUDE.md length"),
        ("check-skill-length.sh:41",
         "Blocked by skill length gate: could not source _lib.sh.",
         "skill length"),
        ("deny-credential-bash-reads.sh:27",
         "Blocked by credential-path Bash gate: could not source _lib.sh.",
         "credential-path Bash"),
        ("deny-credential-file-reads.sh:27",
         "Blocked by credential-file read gate: could not source _lib.sh.",
         "credential-file read"),
        ("deny-data-file-reads.sh:65",
         "Blocked by data-file read gate: could not source _lib.sh.",
         "data-file read"),
        ("deny-env-reads.sh:47",
         "Blocked by env-read gate: could not source _lib.sh.",
         "env-read"),
        ("deny-escaped-backticks-in-pr-body.sh:46",
         "Blocked by backtick-escape gate: could not source _lib.sh.",
         "backtick-escape"),
        ("deny-network-installs.sh:40",
         "Blocked by network-install gate: could not source _lib.sh.",
         "network-install"),
        ("deny-pii-in-commits.sh:127",
         "Blocked by PII commit gate: could not source _lib.sh — hook cannot evaluate the commit safely.",
         "PII commit"),
        ("deny-private-project-refs.sh:180",
         "Blocked by redaction gate: could not source _lib.sh — hook cannot evaluate command detection safely.",
         "redaction"),
        ("deny-repo-relocation.sh:63",
         "Blocked by repo-relocation hook: could not source _lib.sh — hook cannot evaluate relocation discipline safely.",
         "repo-relocation"),
        ("deny-reviewer-tree-mutation.sh:146",
         "Blocked by reviewer-tree-mutation hook: could not source _lib.sh — hook cannot evaluate reviewer discipline safely.",
         "reviewer-tree-mutation"),
        ("enforce-marker-script-shape.sh:68",
         "Blocked by marker-script-shape gate: could not source _lib.sh.",
         "marker-script-shape"),
        ("guard-settings-session-keys.sh:49",
         "Blocked by settings session-keys gate: could not source _lib.sh.",
         "settings session-keys"),
        ("require-code-review.sh:48",
         "Blocked by code-review gate: could not source _lib.sh.",
         "code-review"),
        ("require-memory-skill.sh:59",
         "Blocked by memory-skill gate: could not source _lib.sh.",
         "memory-skill"),
        ("require-memory-skill.sh:125",
         "Memory write blocked by ai-instruction-and-memory-files gate. You are writing to "
         "MEMORY.md, which is part of Claude Code's auto-memory file system.",
         "ai-instruction-and-memory-files"),
        ("require-plan-review.sh:66",
         "Blocked by plan-review gate: could not source _lib.sh.",
         "plan-review"),
        ("require-plan-review.sh:239",
         "Plan presentation blocked by the plan-review gate: an uncommitted or modified "
         "plan file exists in .claude/plans/ but no plan-review marker covering the "
         "current plan set was found.",
         "plan-review"),
        ("require-routing-read.sh:27",
         "Blocked by routing-read gate: could not source _lib.sh.",
         "routing-read"),
        ("require-routing-read.sh:68",
         "Agent spawn blocked by plan-review routing gate: Read the plan-review skill's "
         "ROUTING.md before spawning any specialist agent.",
         "plan-review routing"),
        ("require-ready-for-review.sh:80",
         "Blocked by ready-for-review gate: could not source _lib.sh.",
         "ready-for-review"),
        ("require-respond-pr.sh:69",
         "Blocked by respond-pr gate: could not source _lib.sh.",
         "respond-pr"),
        ("require-stow-reminder.sh:71",
         "Blocked by stow-reminder gate: could not source _lib.sh.",
         "stow-reminder"),
        ("require-worktree-for-file-writes.sh:50",
         "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh.",
         "worktree-enforcement"),
        ("require-worktree-for-git-writes.sh:91",
         "Blocked by worktree-enforcement hook: could not source _lib.sh — hook cannot "
         "evaluate git discipline safely.",
         "worktree-enforcement"),
        ("enforce-marker-script-shape.sh:277",
         "marker.sh invocation denied (path traversal '..' detected). Command "
         "(truncated): ~/.claude/scripts/marker.sh write foo",
         "marker.sh"),
        ("enforce-marker-script-shape.sh:353",
         "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh bogus",
         "marker.sh"),
        ("check-claude-md-length.sh:85",
         "CLAUDE.md/AGENTS.md length gate: one or more files grew past the 200-line limit. "
         "Reduce to the limit or fewer lines before committing.",
         "AGENTS.md length"),
        ("check-skill-length.sh:87",
         "Skill length gate: one or more SKILL.md files grew past their per-skill limit. "
         "Reduce to the limit or fewer lines before committing.",
         "Skill length"),
    ])
    def test_hook_wording_produces_enumerated_label(self, hook_file, message, expected_label):
        got = _mod._denial_hook_label("", message)
        assert got == expected_label, (
            f"{hook_file}'s wording produced {got!r}, expected the enumerated "
            f"label {expected_label!r} — either the hook's wording drifted or "
            f"_DENIAL_HOOK_LABELS is stale"
        )
        assert got in _mod._DENIAL_HOOK_LABELS
        assert got != _mod._DENY_SUMMARY_UNMATCHED_HOOK


# Every gate hook bootstraps identically: `set -uo pipefail`, define a raw
# emit_deny stub, then `. "$(dirname "$0")/_lib.sh"` — if that source fails,
# the stub denies with "Blocked by <label> gate/hook: could not source
# _lib.sh." before ever reading stdin. Copying one hook script alone (no
# _lib.sh alongside it, see _isolated_hook_copy) into a fresh directory
# reliably fails that source line, driving this exact wording for real
# rather than hand-typing it — one entry per _DENIAL_HOOK_LABELS member
# reachable through this shared path.
_BOOTSTRAP_FALLBACK_HOOKS: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh", "gh-pr-merge"),
    ("check-claude-md-length.sh", "CLAUDE.md length"),
    ("check-skill-length.sh", "skill length"),
    ("deny-credential-bash-reads.sh", "credential-path Bash"),
    ("deny-credential-file-reads.sh", "credential-file read"),
    ("deny-data-file-reads.sh", "data-file read"),
    ("deny-env-reads.sh", "env-read"),
    ("deny-escaped-backticks-in-pr-body.sh", "backtick-escape"),
    ("deny-network-installs.sh", "network-install"),
    ("deny-pii-in-commits.sh", "PII commit"),
    ("deny-private-project-refs.sh", "redaction"),
    ("deny-repo-relocation.sh", "repo-relocation"),
    ("deny-reviewer-tree-mutation.sh", "reviewer-tree-mutation"),
    ("enforce-marker-script-shape.sh", "marker-script-shape"),
    ("guard-settings-session-keys.sh", "settings session-keys"),
    ("require-code-review.sh", "code-review"),
    ("require-memory-skill.sh", "memory-skill"),
    ("require-plan-review.sh", "plan-review"),
    ("require-routing-read.sh", "routing-read"),
    ("require-ready-for-review.sh", "ready-for-review"),
    ("require-respond-pr.sh", "respond-pr"),
    ("require-stow-reminder.sh", "stow-reminder"),
    ("require-worktree-for-file-writes.sh", "worktree-enforcement"),
    ("require-worktree-for-git-writes.sh", "worktree-enforcement"),
)


def _isolated_hook_copy(tmp_path: Path, hook_name: str) -> Path:
    """Copy one hooks/*.sh script alone into an isolated directory, with no
    _lib.sh alongside it, so the hook's own `. "$(dirname "$0")/_lib.sh"`
    bootstrap line genuinely fails to source."""
    dest_dir = tmp_path / "isolated-hook"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / hook_name
    shutil.copy2(HOOKS_DIR / hook_name, dest)
    return dest


def _run_hook_raw_stderr(hook: Path, tool_input: dict) -> str:
    """Invoke `hook` directly and return its raw stderr text.

    Distinct from helpers.run_hook_reason, which parses a JSON stdout
    payload emitted by the fully-sourced _lib_emit_deny — the bootstrap
    source-failure path denies via the pre-source emit_deny stub, which
    writes straight to stderr and exits 2 with empty stdout, so
    run_hook_reason would read that as "allowed silently" (returns None).
    """
    result = subprocess.run(
        [str(hook)], input=json.dumps(tool_input), capture_output=True, text=True, check=False,
    )
    return result.stderr


class TestDenialHookLabelEnumerationRealHooks:
    """Drives each hook's actual deny path via subprocess (the helpers.run_hook
    pattern already established in hooks/tests/test_enforce_marker_script_shape.py)
    and feeds the hook's own real stdout/stderr message through
    _denial_hook_label, rather than a hand-transcribed string —
    TestDenialHookLabelEnumeration above never runs a hook process at all.

    Three of TestDenialHookLabelEnumeration's 31 rows stay fixture-only:
    their real trigger needs machinery (an isolated $HOME with a live
    active-bypass marker or session-keyed state) that belongs in each hook's
    own dedicated test file, not duplicated here — require-memory-skill.sh:125
    (ai-instruction-and-memory-files), require-plan-review.sh:239 (plan-review;
    the label itself is still proven live below via require-plan-review.sh:66's
    bootstrap-failure case), and require-routing-read.sh:68 (plan-review
    routing)."""

    @pytest.mark.parametrize("hook_name,expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_bootstrap_lib_sh_failure_produces_enumerated_label(self, tmp_path, hook_name, expected_label):
        dest = _isolated_hook_copy(tmp_path, hook_name)
        message = _run_hook_raw_stderr(dest, bash_input("echo hi"))
        got = _mod._denial_hook_label("", message)
        assert got == expected_label, (
            f"{hook_name}'s real bootstrap-failure wording {message!r} produced "
            f"{got!r}, expected the enumerated label {expected_label!r}"
        )

    def test_marker_sh_path_traversal_produces_enumerated_label(self):
        """enforce-marker-script-shape.sh's own path-traversal deny path —
        distinct real wording from the bootstrap-failure case above, which
        shares the same 'marker.sh' label."""
        cmd = "../../.claude/scripts/marker.sh write code-review"
        message = run_hook_reason(HOOKS_DIR / "enforce-marker-script-shape.sh", bash_input(cmd))
        assert message is not None
        assert _mod._denial_hook_label("", message) == "marker.sh"

    def test_marker_sh_unknown_subcommand_produces_enumerated_label(self):
        """enforce-marker-script-shape.sh's general 'invocation denied'
        wording for an unenumerated subcommand — distinct real wording from
        the path-traversal case above, which shares the same 'marker.sh' label."""
        cmd = "~/.claude/scripts/marker.sh forge code-review"
        message = run_hook_reason(HOOKS_DIR / "enforce-marker-script-shape.sh", bash_input(cmd))
        assert message is not None
        assert _mod._denial_hook_label("", message) == "marker.sh"

    def test_agents_md_over_limit_produces_enumerated_label(self, tmp_path):
        """check-claude-md-length.sh's real 'grew past the 200-line limit'
        deny path for a root AGENTS.md, mirroring
        test_check_claude_md_length.py's own git-repo fixture pattern."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        agents_md = repo / "AGENTS.md"
        agents_md.write_text("\n".join(f"line {i}" for i in range(190)) + "\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        agents_md.write_text("\n".join(f"line {i}" for i in range(201)) + "\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        message = run_hook_reason(
            HOOKS_DIR / "check-claude-md-length.sh", bash_input("git commit -m foo"), cwd=repo,
        )
        assert message is not None
        assert _mod._denial_hook_label("", message) == "AGENTS.md length"

    def test_skill_md_over_limit_produces_enumerated_label(self, tmp_path):
        """check-skill-length.sh's real 'grew past their per-skill limit' deny
        path, mirroring test_check_skill_length.py's own git-repo fixture
        pattern."""
        repo = tmp_path / "repo"
        skill_dir = repo / "claude" / ".claude" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        skill_md = skill_dir / "SKILL.md"
        skill_path = "claude/.claude/skills/my-skill/SKILL.md"
        skill_md.write_text("\n".join(f"line {i}" for i in range(190)) + "\n")
        subprocess.run(["git", "add", skill_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        skill_md.write_text("\n".join(f"line {i}" for i in range(201)) + "\n")
        subprocess.run(["git", "add", skill_path], cwd=repo, check=True)
        message = run_hook_reason(
            HOOKS_DIR / "check-skill-length.sh", bash_input("git commit -m foo"), cwd=repo,
        )
        assert message is not None
        assert _mod._denial_hook_label("", message) == "Skill length"


# ---------------------------------------------------------------------------
# Multi-account scope (transcript-corpus-multi-account-scope plan) —
# cross-subcommand resolved-scope-header and roots-threading coverage.
# ---------------------------------------------------------------------------


def _fake_gh_pr_list_run(cmd, *a, **k):
    """A no-op `gh` double for subcommands (pr-link) whose branch-iteration
    loop shells out regardless of session content."""
    if cmd[:2] == ["gh", "pr"]:
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


# (cli_name, header_name, cmd_func, zero-arg args factory) for the 23
# subcommands whose resolved-scope header prints unconditionally, even over
# an empty scope. review-trace and skill-invocation print unconditionally
# too, but carry zero-match message text and branches the other 23 don't, so
# they get their own tests below rather than a row here.
_UNCONDITIONAL_HEADER_CASES: list[tuple[str, str, object, object]] = [
    ("buckets", "BUCKETS", _mod.cmd_buckets,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("fail-seq", "FAIL SEQ", _mod.cmd_fail_seq,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": "main"})()),
    ("struggle", "STRUGGLE", _mod.cmd_struggle,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("duration", "DURATION", _mod.cmd_duration,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None})()),
    ("subagents", "SUBAGENTS", _mod.cmd_subagents, _subagents_args),
    ("subagent-mix", "SUBAGENT MIX", _mod.cmd_subagent_mix,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "branches": None, "per_session": False})()),
    ("reviewer-yield", "REVIEWER YIELD", _mod.cmd_reviewer_yield, _reviewer_yield_args),
    ("skill-pair", "SKILL PAIR", _mod.cmd_skill_pair, _skill_pair_args),
    ("pr-link", "PR LINK", _mod.cmd_pr_link,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "branches": "main",
         "repo": "owner/repo", "author": None,
     })()),
    ("commit-gate", "COMMIT GATE", _mod.cmd_commit_gate, lambda: _gate_args("code-review")),
    ("audit-routing", "AUDIT ROUTING", _mod.cmd_audit_routing, _audit_routing_args),
    ("cost", "COST", _mod.cmd_cost, _cost_args),
    ("context-distribution", "CONTEXT DISTRIBUTION", _mod.cmd_context_distribution, _context_distribution_args),
    ("context-composition", "CONTEXT COMPOSITION", _mod.cmd_context_composition,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "since": None, "no_redact": False, "extra_config_dirs": None,
     })()),
    ("cost-trend", "COST TREND", _mod.cmd_cost_trend, _cost_trend_args),
    ("cache-rebuild", "CACHE REBUILD", _mod.cmd_cache_rebuild, _cache_rebuild_args),
    ("handoff-ratio", "HANDOFF RATIO", _mod.cmd_handoff_ratio, _handoff_args),
    ("audit-routing-shape", "AUDIT ROUTING SHAPE", _mod.cmd_audit_routing_shape, _audit_routing_shape_args),
    ("audit-routing-samples", "AUDIT ROUTING SAMPLES", _mod.cmd_audit_routing_samples, _audit_routing_samples_args),
    ("judgment-pair", "JUDGMENT PAIR", _mod.cmd_judgment_pair, _judgment_pair_args),
    ("edit-format", "EDIT FORMAT", _mod.cmd_edit_format,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "no_redact": False, "extra_config_dirs": None})()),
    ("read-scope", "READ SCOPE", _mod.cmd_read_scope,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "no_redact": False, "extra_config_dirs": None})()),
    ("instrument-authoring", "INSTRUMENT AUTHORING", _mod.cmd_instrument_authoring,
     lambda: type("A", (), {"projects": "*", "this_repo": False, "extra_config_dirs": None})()),
    ("cost-ledger", "COST LEDGER", _mod.cmd_cost_ledger, _cost_ledger_args),
    ("plan-boundary", "PLAN BOUNDARY", _mod.cmd_plan_boundary,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "since": None, "no_redact": False, "extra_config_dirs": None,
     })()),
    ("sessions", "SESSIONS", _mod.cmd_sessions,
     lambda: type("A", (), {
         "projects": "*", "this_repo": False, "paths": True, "include_subagents": False,
     })()),
]


class TestAllSubcommandsSingleRootHeader:
    """Across every funnel subcommand, the resolved-scope header states the
    root count unconditionally — even at one root with nothing declared, the
    exact state that produced the original corpus undercount — and no
    per-root stderr progress line prints at one root (that line is gated on
    len(roots) > 1)."""

    _HEADER_SUFFIX = "1 root (no ~/.claude/transcript-config-dirs declared))"

    @pytest.mark.parametrize("subcommand,header_name,cmd_func,args_factory", _UNCONDITIONAL_HEADER_CASES)
    def test_header_states_one_root_and_no_progress_line_prints(
        self, fake_projects, cost_ledger_file, monkeypatch, capsys, subcommand, header_name, cmd_func, args_factory
    ):
        monkeypatch.setattr(subprocess, "run", _fake_gh_pr_list_run)
        cmd_func(args_factory())
        out, err = capsys.readouterr()
        combined = out + err
        assert f"{header_name} SOURCES (" in combined, f"{subcommand}: no resolved-scope header printed"
        assert self._HEADER_SUFFIX in combined, f"{subcommand}: header missing the unconditional root-count suffix"
        assert "scanning root" not in combined, f"{subcommand}: a single-root run must not print a per-root progress line"

    def test_review_trace_header_states_one_root_once_a_session_matches(self, fake_projects, capsys):
        """review-trace prints its header before the scan regardless of match
        count; seed one qualifying session so the matched path is covered here
        (the zero-match path has its own test)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args())
        out, err = capsys.readouterr()
        combined = out + err
        assert "REVIEW TRACE SOURCES (" in combined
        assert self._HEADER_SUFFIX in combined
        assert "scanning root" not in combined

    def test_skill_invocation_header_states_one_root_once_a_skill_matches(self, fake_projects, capsys):
        """skill-invocation prints its header before the zero-match return;
        this covers the matched path (the zero-match path has its own test)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(_skill_inv_args())
        out, err = capsys.readouterr()
        combined = out + err
        assert "SKILL INVOCATION SOURCES (" in combined
        assert self._HEADER_SUFFIX in combined
        assert "scanning root" not in combined

    def test_review_trace_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """A zero-match run is the case the header exists for: at one root no
        per-root progress line prints either, so without this the run is
        byte-for-byte silent and a mis-scoped scan reads as a genuine empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args(branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "REVIEW TRACE SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No sessions matched in scope." in out
        assert "### " not in out, "no session block should be emitted on a zero-match run"
        assert "scanning root" not in out + err

    def test_review_trace_deny_summary_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """--deny-summary's third state — scope matched no sessions at all, as
        distinct from matching sessions that carried no denial."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_summary=True, branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "REVIEW TRACE SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No sessions matched in scope." in out
        assert "No denials found in scope." not in out, (
            "zero sessions matched is a different state from matched-but-no-denials"
        )
        assert "scanning root" not in out + err

    def test_skill_invocation_zero_match_still_states_its_scope(self, fake_projects, capsys):
        """The not-found message alone cannot distinguish a wrongly-scoped scan
        from a correctly-scoped empty one; the header is what separates them."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(_skill_inv_args(branches="no-such-branch"))
        out, err = capsys.readouterr()
        assert "SKILL INVOCATION SOURCES (" in out
        assert self._HEADER_SUFFIX in out
        assert "No skill invocations found." in out
        assert "scanning root" not in out + err

    def test_skill_invocation_zero_match_header_names_the_subagent_thread_scope(
        self, fake_projects, capsys
    ):
        """The thread-scope clause is part of what a zero-match run must disclose —
        'searched main+subagents and found nothing' is a different claim from
        'searched the main thread and found nothing'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(
            _skill_inv_args(branches="no-such-branch", include_subagents=True)
        )
        out = capsys.readouterr().out
        assert "main+subagents" in out
        assert "No skill invocations found." in out

    _DISTINCTIVE_GLOB = "-home-user-somerepo-worktrees-somebranch"

    def test_review_trace_zero_match_header_echoes_an_explicit_projects_glob_verbatim(
        self, fake_projects, capsys
    ):
        """review-trace's scope label IS the glob, so an operator's own --projects
        value reaches stdout even at zero matches — the reason the section's
        not-publish-safe warning has to cover empty runs. Pinned so a later
        'redact the empty case to be safe' change breaks a test, not a promise."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(
            _review_trace_args(projects=self._DISTINCTIVE_GLOB, branches="no-such-branch")
        )
        out = capsys.readouterr().out
        assert "REVIEW TRACE SOURCES (" in out
        assert self._DISTINCTIVE_GLOB in out

    def test_skill_invocation_zero_match_header_does_not_echo_the_projects_glob(
        self, fake_projects, capsys
    ):
        """skill-invocation labels the escape hatch ('explicit --projects
        (not repo-scoped)') instead of interpolating the glob, so its output stays
        free of the operator's path even under --projects. That divergence from
        review-trace is a minimization property, not an oversight."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_skill_invocation(
            _skill_inv_args(projects=self._DISTINCTIVE_GLOB, branches="no-such-branch")
        )
        out = capsys.readouterr().out
        assert "explicit --projects (not repo-scoped)" in out
        assert self._DISTINCTIVE_GLOB not in out

    def test_review_trace_deny_only_does_not_mask_which_zero_match_state_was_reached(
        self, fake_projects, capsys
    ):
        """`any_session_matched` is set before the --deny-only skip, so a run whose
        sessions matched but carried no denial must still say 'No denials found',
        not 'No sessions matched'. Moving that assignment below the skip would
        silently misreport scope coverage as empty."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        _mod.cmd_review_trace(_review_trace_args(deny_summary=True, deny_only=True))
        out = capsys.readouterr().out
        assert "No denials found in scope." in out
        assert "No sessions matched in scope." not in out


def _two_declared_roots(tmp_path, monkeypatch) -> list[Path]:
    """Active profile (acct-a) plus one declared root (acct-b, via
    TRANSCRIPT_CONFIG_DIRS_FILE) -- the minimal multi-root setup where a call
    site that forgot to thread `roots` is distinguishable from one that
    threaded it correctly (both look identical at one root, since
    _resolve_project_scope's own internal default is also (PROJECTS_DIR,)).
    Pins both PROJECTS_DIR (_resolve_scan_roots' base, used by 18 of the 19
    funnel subcommands) and CLAUDE_CONFIG_DIR (config_dir(), which
    _resolve_cost_roots reads independently for cost/context-distribution) at
    the same acct-a, so every subcommand agrees on the same two-root list."""
    acct_a = tmp_path / "acct-a"
    (acct_a / "projects").mkdir(parents=True)
    acct_b = tmp_path / "acct-b"
    (acct_b / "projects").mkdir(parents=True)
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", acct_a / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{acct_b}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    return [acct_a / "projects", acct_b / "projects"]


class TestRootsThreadingSpy:
    """Every _resolve_project_scope call site threads the SAME `roots` list
    to both scope resolution and the header call —
    format-independent, catches a subcommand whose roots list isn't threaded
    identically to both call sites, regardless of what it prints. Wraps (not
    replaces) the real functions, so each subcommand's normal behavior and
    assertions from the header test above still apply; only the call
    arguments are additionally recorded."""

    @pytest.mark.parametrize("subcommand,header_name,cmd_func,args_factory", _UNCONDITIONAL_HEADER_CASES)
    def test_resolve_project_scope_and_header_receive_identical_multi_root_list(
        self, tmp_path, cost_ledger_file, monkeypatch, subcommand, header_name, cmd_func, args_factory
    ):
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        scope_calls: list[list] = []
        header_calls: list[list] = []
        real_resolve = _mod._resolve_project_scope
        real_print = _mod._print_resolved_scope

        def spy_resolve(*a, **k):
            scope_calls.append(list(k["roots"]) if k.get("roots") is not None else None)
            return real_resolve(*a, **k)

        def spy_print(*a, **k):
            roots = a[2] if len(a) > 2 else k.get("roots")
            header_calls.append(list(roots))
            return real_print(*a, **k)

        monkeypatch.setattr(_mod, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(_mod, "_print_resolved_scope", spy_print)
        monkeypatch.setattr(subprocess, "run", _fake_gh_pr_list_run)

        cmd_func(args_factory())

        assert scope_calls, f"{subcommand}: _resolve_project_scope was never called"
        assert header_calls, f"{subcommand}: _print_resolved_scope was never called"
        assert scope_calls[-1] == expected_roots, (
            f"{subcommand}: scope resolution did not receive the multi-root list — {scope_calls[-1]!r}"
        )
        assert header_calls[-1] == expected_roots, (
            f"{subcommand}: header did not receive the multi-root list — {header_calls[-1]!r}"
        )

    def test_review_trace_scope_and_header_receive_identical_multi_root_list(self, tmp_path, monkeypatch):
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        proj = expected_roots[0] / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
        ])
        scope_calls: list[list] = []
        header_calls: list[list] = []
        real_resolve = _mod._resolve_project_scope
        real_print = _mod._print_resolved_scope

        def spy_resolve(*a, **k):
            scope_calls.append(list(k["roots"]) if k.get("roots") is not None else None)
            return real_resolve(*a, **k)

        def spy_print(*a, **k):
            roots = a[2] if len(a) > 2 else k.get("roots")
            header_calls.append(list(roots))
            return real_print(*a, **k)

        monkeypatch.setattr(_mod, "_resolve_project_scope", spy_resolve)
        monkeypatch.setattr(_mod, "_print_resolved_scope", spy_print)

        _mod.cmd_review_trace(_review_trace_args())

        assert scope_calls[-1] == expected_roots
        assert header_calls[-1] == expected_roots

    def test_skill_invocation_glob_scope_receives_the_multi_root_list_as_is(self, tmp_path, monkeypatch):
        """cmd_skill_invocation never calls _resolve_project_scope — its two
        call sites (:2146, :2148 in the plan's line numbering) route through
        _iter_glob_scoped_sessions/_iter_scoped_sessions directly. Above one
        root, the --projects branch takes the _iter_glob_scoped_sessions path
        with the full roots list (not iter_sessions' single-Path shape)."""
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        calls: list[list] = []
        real_glob_scoped = _mod._iter_glob_scoped_sessions

        def spy_glob_scoped(roots, *a, **k):
            calls.append(list(roots))
            return real_glob_scoped(roots, *a, **k)

        monkeypatch.setattr(_mod, "_iter_glob_scoped_sessions", spy_glob_scoped)

        _mod.cmd_skill_invocation(_skill_inv_args())  # projects="*" -- the explicit-glob branch

        assert calls, "_iter_glob_scoped_sessions was never called"
        assert calls[-1] == expected_roots

    def test_skill_invocation_repo_scoped_call_receives_the_multi_root_list(self, tmp_path, monkeypatch):
        """The --this-repo-equivalent default branch (--projects unset) threads
        roots=roots into _iter_scoped_sessions rather than defaulting."""
        expected_roots = _two_declared_roots(tmp_path, monkeypatch)
        calls: list[list | None] = []
        real_scoped = _mod._iter_scoped_sessions

        def spy_scoped(slugs, include_subagents, roots=None):
            calls.append(list(roots) if roots is not None else None)
            return real_scoped(slugs, include_subagents, roots=roots)

        monkeypatch.setattr(_mod, "_iter_scoped_sessions", spy_scoped)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_git_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_git_run)

        _mod.cmd_skill_invocation(_skill_inv_args(projects=None))  # unset -- the repo-scoped default branch

        assert calls, "_iter_scoped_sessions was never called"
        assert calls[-1] == expected_roots

    def test_single_root_skill_invocation_glob_call_passes_a_bare_path_not_a_list(self, fake_projects, monkeypatch):
        """At one root, cmd_skill_invocation's --projects branch keeps calling
        iter_sessions(roots[0], ...) directly (a single Path, not a list) —
        iter_sessions' documented flat-sort-over-full-paths ordering guarantee
        is unaffected by this plan at single root."""
        calls: list[Path] = []
        real_iter_sessions = _mod.iter_sessions

        def spy_iter_sessions(projects_dir, *a, **k):
            calls.append(projects_dir)
            return real_iter_sessions(projects_dir, *a, **k)

        monkeypatch.setattr(_mod, "iter_sessions", spy_iter_sessions)

        _mod.cmd_skill_invocation(_skill_inv_args())  # projects="*", single root (fake_projects' PROJECTS_DIR)

        assert calls == [_mod.scope.PROJECTS_DIR]


class TestThisRepoUnionsAcrossRoots:
    """--this-repo (and, at the _iter_scoped_sessions layer it shares, every
    other slug-scoped caller) unions across every resolved root, whether
    those roots came from a declared-roots file or from cost's own explicit
    --config-dir extras -- the union mechanism at this layer is the same
    regardless of which resolver assembled `roots`."""

    def test_multi_root_slug_match_is_name_only_and_dedupes_across_roots(self, tmp_path):
        """Direct coverage of _iter_scoped_sessions(roots=[a, b]): the same
        slug present under two roots unions both sessions, and a third root
        that's a symlink alias to one of the first two contributes no
        duplicate (dedup spans every root, not just identical-path roots)."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-a")])

        root_b = tmp_path / "acct-b"
        proj_b = root_b / "-repo-main"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-b")])

        root_c_alias = tmp_path / "acct-a-alias"
        root_c_alias.symlink_to(root_a)

        sessions = list(_mod._iter_scoped_sessions(
            ["-repo-main"], False, roots=[root_a, root_b, root_c_alias],
        ))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-root-a", "from-root-b"}
        assert len(sessions) == 2  # root_c_alias contributes no duplicate of root_a's session

    def test_this_repo_excludes_foreign_project_dirs_under_extra_root(self, tmp_path):
        """The minimization guard that replaces the (out-of-scope-here)
        refusal on cost's --this-repo + --config-dir combination — must not
        be skipped: a directory under a declared
        root that isn't one of the resolved worktree slugs stays excluded,
        proving the union doesn't widen matching to every project on that root."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-a")])

        root_b = tmp_path / "acct-b"
        proj_b_matching = root_b / "-repo-main"
        proj_b_matching.mkdir(parents=True)
        _write_jsonl(proj_b_matching / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-root-b")])
        proj_b_foreign = root_b / "-home-user-unrelated-secret-clientname"
        proj_b_foreign.mkdir(parents=True)
        _write_jsonl(proj_b_foreign / "sess-foreign.jsonl", [_asst("claude-sonnet-4-6", branch="foreign-branch")])

        sessions = list(_mod._iter_scoped_sessions(["-repo-main"], False, roots=[root_a, root_b]))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-root-a", "from-root-b"}
        assert "foreign-branch" not in branches_seen

    def test_this_repo_slug_collision_admits_foreign_project_with_identical_slug(
        self, tmp_path, monkeypatch
    ):
        """Pins a known, plan-accepted residual risk (see Step 21 of
        .claude/plans/transcript-corpus-multi-account-scope.md: "[/.]→- is
        not injective, so /a/b.c and /a/b/c collide"), not a bug to fix here.
        _iter_scoped_sessions matches by basename equality alone, so it
        cannot distinguish this repo's own project dir from a foreign
        account's unrelated project dir that happens to collide onto the
        same slug -- under --this-repo, both are admitted. A future change to
        slug derivation that silently alters this posture, in either
        direction, must fail this test."""
        this_repo_slug = _mod.scope._path_to_project_slug("/a/b/c")
        foreign_path_slug = _mod.scope._path_to_project_slug("/a/b.c")
        assert this_repo_slug == foreign_path_slug == "-a-b-c"  # the collision this test pins

        root_a = tmp_path / "acct-a"
        proj_a = root_a / this_repo_slug
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="this-repo-session")])

        root_b = tmp_path / "acct-b"
        proj_b_colliding = root_b / foreign_path_slug
        proj_b_colliding.mkdir(parents=True)
        _write_jsonl(proj_b_colliding / "sess-foreign.jsonl",
                     [_asst("claude-sonnet-4-6", branch="foreign-colliding-session")])

        args = type("A", (), {"projects": "*", "this_repo": True})()
        args._this_repo_slugs = [this_repo_slug]
        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: args._this_repo_slugs)

        session_iter, _scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        branches_seen = {rec["gitBranch"] for _jsonl, records in session_iter for rec in records}
        assert branches_seen == {"this-repo-session", "foreign-colliding-session"}

    def test_this_repo_unions_same_slug_across_roots(self, tmp_path, monkeypatch, capsys):
        """End-to-end: buckets --this-repo, with a declared-roots file adding
        a second root that also contains this repo's own worktree slug --
        both roots' sessions appear in one report, not just the active
        profile's."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="feat-in-root-a")])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", root_a)

        root_b = tmp_path / "acct-b-config"
        proj_b = root_b / "projects" / "-repo-main"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="feat-in-root-b")])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{root_b}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": True, "branches": None})())
        out = capsys.readouterr().out
        assert "feat-in-root-a" in out
        assert "feat-in-root-b" in out
        assert "2 roots" in out

    def test_this_repo_fail_closed_across_all_roots(self, tmp_path):
        """The :2061-equivalent deny case: zero slug matches under EVERY
        resolved root still exits 1 — direct unit coverage of `any(...
        for root in roots for slug in slugs)` iterating every root, not just
        roots[0]. Calls _resolve_project_scope directly with a hand-built
        multi-element `roots` list (unreachable through the real CLI today,
        since an explicit top-level --config-dir always collapses `roots` to
        one element — see _resolve_scan_roots) as a robustness pin against a
        future precedence change, per the plan's own framing."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        args = argparse.Namespace(this_repo=True, projects="*", config_dir=str(root_a), _this_repo_slugs=["-repo-main"])
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert exc_info.value.code == 1

    def test_this_repo_allow_case_when_only_a_non_first_root_matches(self, tmp_path):
        """Companion allow-case to the deny test above: a match under the
        SECOND resolved root (not roots[0]) must not fail closed — proves the
        `any(...)` genuinely iterates every root rather than short-circuiting
        on the first."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        (root_b / "-repo-main").mkdir(parents=True)
        args = argparse.Namespace(this_repo=True, projects="*", config_dir=str(root_a), _this_repo_slugs=["-repo-main"])
        session_iter, scope_label = _mod._resolve_project_scope(args, "buckets", roots=[root_a, root_b])
        assert list(session_iter) == []  # no sessions written, but no SystemExit either
        assert scope_label == "this repo (1 project dirs)"


class TestIterScopedSessionsUnreadableRoot:
    """Security regression: root.iterdir() raises PermissionError on an
    unreadable root with no exception handling in the pre-fix code -- an
    uncaught traceback that prints the raw path, bypassing --redact
    entirely. Reachable via --this-repo (now multi-root by default) on any
    subcommand funneling through _iter_scoped_sessions. Must be caught,
    reported to stderr without the raw path, and the scan must continue
    across the remaining roots rather than crash."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_root_is_skipped_without_crash_or_path_leak(self, tmp_path, capsys):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])

        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        try:
            sessions = list(_mod._iter_scoped_sessions(["-repo-main"], False, roots=[root_a, root_b]))
        finally:
            os.chmod(root_b, 0o755)  # restore before tmp_path teardown

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}  # root_a's session still found -- no crash

        err = capsys.readouterr().err
        assert str(root_b) not in err  # the raw unreadable path is never printed
        assert "skipping" in err


class TestPoisonedProjectsDirGlobal:
    """PROJECTS_DIR pointed at a nonexistent path, with one real root
    declared via TRANSCRIPT_CONFIG_DIRS_FILE — sessions are still found
    through the declared root, proving no funnel site still reads
    PROJECTS_DIR as an unthreaded, single-root default."""

    def test_sessions_found_via_declared_root_despite_nonexistent_projects_dir(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "nonexistent-active-profile" / "projects")
        real_root = tmp_path / "real-account"
        proj = real_root / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-declared-root")])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{real_root}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cmd_buckets(type("A", (), {"projects": "*", "this_repo": False, "branches": None})())
        out = capsys.readouterr().out
        assert "feat-declared-root" in out
        assert "2 roots" in out


class TestMultiRootFormatOutliers:
    """End-to-end multi-root output tests for the three format-outlier
    subcommands (judgment-pair writes --out,
    audit-routing-samples is a JSON stream with the header on stderr, cost
    redacts by default) — each reached through the declared-roots file (the
    default union _resolve_scan_roots builds from declared_transcript_roots()),
    not cost's own --config-dir extra (already covered by
    TestCostMultiRootRedaction)."""

    def test_cost_redacted_totals_sum_across_declared_roots(self, tmp_path, monkeypatch, capsys):
        default_config = tmp_path / "default-account"
        default_proj = default_config / "projects" / "-home-user-repo-a"
        default_proj.mkdir(parents=True)
        _write_jsonl(default_proj / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_config)

        declared_config = tmp_path / "declared-account"
        declared_proj = declared_config / "projects" / "-home-user-repo-b"
        declared_proj.mkdir(parents=True)
        _write_jsonl(declared_proj / "sess-b.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared_config}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        _mod.cmd_cost(_cost_args())
        out = capsys.readouterr().out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        assert _extract_grand_total(out) == pytest.approx(4.0)

    def test_audit_routing_samples_stdout_still_parses_as_json_across_declared_roots(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [_opus([_read_use("r1", "/a.py")], out=100)])

        _mod.cmd_audit_routing_samples(_audit_routing_samples_args())
        out, err = capsys.readouterr()
        assert "2 roots" in err  # header routed to stderr, matching audit-routing-samples' convention
        # The per-root progress line is gated on len(roots) > 1, so above one
        # root (unlike TestAllSubcommandsSingleRootHeader's assertions) it must print.
        assert "scanning root 1/2..." in err
        assert "scanning root 2/2..." in err
        records = json.loads(out)
        assert {rec["session_id"] for rec in records} == {"sess-0", "sess-1"}

    def test_judgment_pair_out_file_still_one_header_line_across_declared_roots(
        self, tmp_path, monkeypatch
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
                _review_asst("Logic error in retry loop.", "2026-05-20T10:01:00.000Z"),
                _user_reply("Will fix the retry logic."),
            ])
        out_file = roots[0].parent / "out.txt"

        _mod.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
        content = out_file.read_text()
        lines = content.splitlines()
        assert lines[0] == "JUDGMENT PAIR SOURCES (*; 2 roots)"
        header_lines = [ln for ln in lines if ln.startswith("JUDGMENT PAIR SOURCES")]
        assert len(header_lines) == 1
        assert content.count("Logic error in retry loop.") == 2  # one block per root's session


class TestAuditRoutingMultiRootRedaction:
    """audit-routing's --redact must look up each row's label via the shared
    _redaction_ordinals mapping, not a flat string key — proven by seeding
    the same raw project label under two declared roots and asserting both
    rows resolve to distinct account-N tokens instead of one colliding with
    (or being missed by) the other."""

    def test_same_raw_label_under_two_roots_resolves_to_distinct_account_tokens(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for root in roots:
            proj = root / "-home-user-repo"
            proj.mkdir(parents=True)
            _write_jsonl(proj / "sess.jsonl", [_opus([_agent_use("a1", "code-writer")], out=100)])

        _mod.cmd_audit_routing(_audit_routing_args(redact=True))
        out = capsys.readouterr().out
        assert _mod._REDACT_MAP_MISS_TOKEN not in out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        assert "-home-user-repo" not in out


class TestSubagentsDeclaredRootsMultiRoot:
    """subagents' and subagent-mix's multi_root-gated disclosure controls
    (DO_NOT_PUBLISH banner, branch/subagent_type redaction) are gated on
    len(roots) > 1 alone, not on whether --config-dir was passed --
    _resolve_cost_roots now also unions declared_transcript_roots(), so a
    populated ~/.claude/transcript-config-dirs makes multi_root True with
    zero --config-dir flags. Neither TestSubagentsMultiRoot nor
    TestSubagentMixMultiRoot covers this: every test in both classes passes
    extra_config_dirs explicitly."""

    def test_subagent_mix_banner_and_redaction_fire_via_declared_roots_alone(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _asst("claude-opus-4-7", branch="main", content=[_agent_use(f"a{idx}", "staff-sdet")]),
            ])

        _mod.cmd_subagent_mix(_subagent_mix_args())  # no extra_config_dirs passed
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err
        assert "account-1/branch-1" in captured.out
        assert "account-2/branch-1" in captured.out

    def test_subagents_banner_and_redaction_fire_via_declared_roots_alone(
        self, tmp_path, monkeypatch, capsys
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for idx, root in enumerate(roots):
            proj = root / f"-home-user-repo-{idx}"
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _asst("claude-opus-4-7", branch="main"),
            ])

        _mod.cmd_subagents(_subagents_args())  # no extra_config_dirs passed
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err
        assert "account-1/branch-1" in captured.out
        assert "account-2/branch-1" in captured.out


class TestResolveScanRoots:
    """_resolve_scan_roots as a directly callable, unit-testable function --
    the explicit top-level --config-dir precedence over a populated
    declared-roots file, and getattr-based tolerance of a hand-built `args`
    Namespace that predates the config_dir attribute entirely (this file's
    many such fixtures)."""

    def test_no_config_dir_no_declared_roots_returns_projects_dir_alone(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        args = argparse.Namespace(config_dir=None)
        assert _mod._resolve_scan_roots(args) == [tmp_path / "active" / "projects"]

    def test_no_config_dir_with_declared_roots_unions_both(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        declared = tmp_path / "declared-account"
        (declared / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        args = argparse.Namespace(config_dir=None)
        assert _mod._resolve_scan_roots(args) == [
            tmp_path / "active" / "projects", declared / "projects",
        ]

    def test_explicit_top_level_config_dir_overrides_populated_declared_roots_file(
        self, monkeypatch, tmp_path
    ):
        """The flagship precedence rule: an explicit --config-dir wins outright,
        returning that one directory's projects/ subdirectory alone — the
        declared-roots file's entries are not unioned in on top of it."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        declared = tmp_path / "declared-account"
        (declared / "projects").mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        override_dir = tmp_path / "explicit-override"
        args = argparse.Namespace(config_dir=str(override_dir))
        assert _mod._resolve_scan_roots(args) == [override_dir / "projects"]

    def test_config_dir_attribute_absent_from_parsed_does_not_raise(self, monkeypatch, tmp_path):
        """A hand-built test `args` Namespace that predates the top-level
        --config-dir flag (this file's many such fixtures) must not raise
        AttributeError -- its absence means "not passed," the real default."""
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", tmp_path / "active" / "projects")
        args = type("A", (), {})()  # no config_dir attribute at all
        assert _mod._resolve_scan_roots(args) == [tmp_path / "active" / "projects"]

    def test_declared_root_matching_active_profile_is_deduped_not_double_listed(
        self, monkeypatch, tmp_path
    ):
        """PROJECTS_DIR is pre-seeded into seen_resolved before iterating
        declared roots, so a declared root that IS (or symlink-aliases) the
        active profile is deduped, not double-listed."""
        active_config = tmp_path / "active-account"
        (active_config / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active_config / "projects")
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{active_config}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

        args = argparse.Namespace(config_dir=None)
        assert _mod._resolve_scan_roots(args) == [active_config / "projects"]


class TestRedactionOrdinalStability:
    """_redaction_ordinals assigns the same ordinal to the same physical
    root regardless of which profile is active (list order) -- guards
    against the bug a local-sort-inside-_build_redact_map approach would
    reintroduce."""

    def test_same_two_roots_different_scan_order_yield_identical_ordinals(self, tmp_path):
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = tmp_path / "acct-b"
        root_b.mkdir()

        ordinals_a_first = _mod._redaction_ordinals([root_a, root_b])
        ordinals_b_first = _mod._redaction_ordinals([root_b, root_a])

        assert ordinals_a_first == ordinals_b_first
        assert ordinals_a_first[root_a.resolve()] == ordinals_b_first[root_a.resolve()]
        assert ordinals_a_first[root_b.resolve()] == ordinals_b_first[root_b.resolve()]

    def test_cost_report_assigns_same_account_label_regardless_of_which_profile_is_active(
        self, tmp_path, capsys
    ):
        """End-to-end version of the ordinal-stability fix: the same physical
        root reads as the same account-N whether it's scanned as the active
        profile (scanned first) or as a declared extra (scanned second)."""
        root_alpha = _write_cost_root(tmp_path, "acct-alpha", "-home-user-repo-alpha", "sess-alpha",
                                       [_priced("claude-sonnet-5", input=1_000_000)])
        root_zulu = _write_cost_root(tmp_path, "acct-zulu", "-home-user-repo-zulu", "sess-zulu",
                                      [_priced("claude-sonnet-5", input=1_000_000)])

        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_alpha, root_zulu])
        out_alpha_active = capsys.readouterr().out

        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_zulu, root_alpha])
        out_zulu_active = capsys.readouterr().out

        # root_alpha resolves alphabetically first, so it holds account-1 in both
        # runs regardless of which one was scanned first (which is active).
        assert "account-1/private-project-1" in out_alpha_active
        assert "account-2/private-project-1" in out_alpha_active
        assert "account-1/private-project-1" in out_zulu_active
        assert "account-2/private-project-1" in out_zulu_active


class TestCorpusFingerprintRootRemovalLimits:
    """_corpus_fingerprint hashes the raw-label SET, not root count --
    removing a root whose labels are a strict subset of a surviving root's
    own labels is invisible to the fingerprint, by design, not by bug."""

    def test_fingerprint_unchanged_when_removed_roots_labels_are_a_subset_of_a_surviving_root(self):
        two_root_map = {
            (0, "shared-label"): "account-1/private-project-1",
            (1, "shared-label"): "account-2/private-project-1",
        }
        one_root_map = {"shared-label": "private-project-1"}
        # Root 2's only label ("shared-label") is a strict subset of root 1's
        # own label set, so dropping root 2 does not change the label SET —
        # the fingerprint is identical even though the account count changed.
        assert _mod._corpus_fingerprint(two_root_map) == _mod._corpus_fingerprint(one_root_map)


class TestResolvedScopeHeaderDirectUnit:
    """Direct unit coverage of _resolved_scope_header's pure string
    formatting — the many end-to-end subcommand tests exercise this function
    only through a full report run; these pin its exact return value at each
    root-count branch."""

    def test_one_root_states_no_declared_roots_file(self, tmp_path):
        header = _mod._resolved_scope_header("buckets", "*", [tmp_path / "acct-a" / "projects"])
        assert header == (
            "BUCKETS SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))"
        )

    def test_n_roots_states_the_count(self, tmp_path):
        roots = [
            tmp_path / "acct-a" / "projects",
            tmp_path / "acct-b" / "projects",
            tmp_path / "acct-c" / "projects",
        ]
        header = _mod._resolved_scope_header("cost", "this repo (2 project dirs)", roots)
        assert header == "COST SOURCES (this repo (2 project dirs); 3 roots)"


class TestRootCountDescDirectUnit:
    """Mechanism 2: _root_count_desc's one-root branch must distinguish the
    roots file being absent from it being present but contributing no
    additional root -- claiming "no ... declared" about a file that exists
    would misrepresent it."""

    def test_one_root_absent_roots_file_states_no_declared_roots_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        assert _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"]) == (
            "1 root (no ~/.claude/transcript-config-dirs declared)"
        )

    def test_one_root_present_but_additive_nothing_does_not_claim_undeclared(self, tmp_path, monkeypatch):
        """The populated-but-additive-nothing case: the roots file exists
        (comments-only, here) but every line was skipped, so exactly one
        root is in scope -- the text must not claim the file is undeclared."""
        roots_file = tmp_path / "roots"
        roots_file.write_text("# just a comment\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        desc = _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"])
        assert "no ~/.claude/transcript-config-dirs declared" not in desc
        assert "~/.claude/transcript-config-dirs" in desc

    def test_one_root_unreadable_roots_file_states_unreadable_not_declared(self, tmp_path, monkeypatch):
        """A directory at the roots-file path raises OSError on read --
        "unreadable", not "absent", and distinct from "present" (a failed
        read never honored any declarations, so wording it as "declared"
        would misrepresent the read that never happened); see
        declared_roots_file_state()'s own docstring for why a directory (not
        chmod) simulates this without silently degrading under a
        root-executing test runner."""
        roots_file_as_dir = tmp_path / "roots-is-a-directory"
        roots_file_as_dir.mkdir()
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file_as_dir))
        assert _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"]) == (
            "1 root (~/.claude/transcript-config-dirs present but unreadable)"
        )

    def test_multi_root_never_names_the_roots_file(self, tmp_path, monkeypatch):
        """Extra roots may come from --config-dir, not only a declared-roots
        file, so the >1-root branch must not name the file at all."""
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        roots = [tmp_path / "acct-a" / "projects", tmp_path / "acct-b" / "projects"]
        assert _mod.scope._root_count_desc(roots) == "2 roots"
        assert "transcript-config-dirs" not in _mod.scope._root_count_desc(roots)

    def test_absent_state_literal_appears_in_both_skill_files(self, tmp_path, monkeypatch):
        """Contract test, a tripwire not a full guarantee: derives
        _root_count_desc()'s absent-state literal and asserts it appears in
        both transcript-analysis/SKILL.md and transcript-narrative/SKILL.md
        source text. This only catches the rendered literal drifting out of
        sync with what the skills quote verbatim -- each skill's own
        verbatim positive pin on its own scope-confirmation sentence (see
        each skill's dedicated tests) is the real contract."""
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
        literal = _mod.scope._root_count_desc([tmp_path / "acct-a" / "projects"])
        for skill_name in ("transcript-analysis", "transcript-narrative"):
            skill_text = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
            assert literal in skill_text, f"{skill_name}/SKILL.md does not quote _root_count_desc()'s literal"


class TestSkillFilesReportObservedScopeNotUnionGuarantee:
    """Mechanism 6: transcript-narrative/SKILL.md and transcript-analysis/SKILL.md
    must instruct the reader to record the *observed* scope rather than assert
    a multi-account union/unconditional-header guarantee that --summary makes
    false. Each positive pin is the real contract (source-scanning a SKILL.md
    is acceptable here -- it has no executable form to run, unlike the .py
    files this module tests elsewhere). Each negative-grep pin is a tripwire
    only: it catches the exact old sentence being re-added verbatim, not a
    reworded regression that reintroduces the same false claim in different
    words."""

    def test_transcript_narrative_names_the_sessions_sources_line_to_record(self):
        skill_text = (SKILLS_DIR / "transcript-narrative" / "SKILL.md").read_text()
        assert (
            "`sessions --paths` prints its resolved-scope header (`SESSIONS SOURCES (...)`) to stderr"
            " — record that line."
        ) in skill_text

    def test_transcript_narrative_no_longer_asserts_every_declared_account_union(self):
        skill_text = (SKILLS_DIR / "transcript-narrative" / "SKILL.md").read_text()
        assert "every declared account, not just the active one — and read only those files" not in skill_text
        assert "resolved against every declared account (`~/.claude/transcript-config-dirs`)" not in skill_text

    def test_transcript_analysis_names_the_summary_scope_line_as_the_carrier(self):
        skill_text = (SKILLS_DIR / "transcript-analysis" / "SKILL.md").read_text()
        assert (
            "`cost --summary` prints no resolved-scope header — it is always scoped to the active"
            " account only, and states so on its own `Scope: this account only (...)` line instead"
        ) in skill_text

    def test_transcript_analysis_no_longer_claims_the_header_is_unconditional_for_every_subcommand(self):
        skill_text = (SKILLS_DIR / "transcript-analysis" / "SKILL.md").read_text()
        assert (
            "not just the active profile. The resolved-scope header states the root count unconditionally,"
            " even at one root with nothing declared — see \"Scope confirmation\" above."
        ) not in skill_text


class TestBuildRedactMapDirectUnit:
    """Direct unit coverage of _build_redact_map's multi-root ordinal-
    assignment math — the many end-to-end subcommand tests exercise this
    function only through a full report run; this pins its returned dict's
    keys/values directly when the same raw project label collides across two
    declared roots."""

    def test_two_roots_with_colliding_raw_label_map_to_distinct_account_scoped_keys(self, tmp_path):
        root_a = tmp_path / "acct-a" / "projects"
        proj_a = root_a / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-a")])

        root_b = tmp_path / "acct-b" / "projects"
        proj_b = root_b / "-home-user-testrepo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_asst("claude-sonnet-4-6", branch="from-b")])

        ordinals = _mod._redaction_ordinals([root_a, root_b])
        ordinal_a, ordinal_b = ordinals[root_a.resolve()], ordinals[root_b.resolve()]

        redact_map = _mod._build_redact_map([root_a, root_b])

        assert redact_map == {
            (ordinal_a, "testrepo"): f"account-{ordinal_a}/private-project-1",
            (ordinal_b, "testrepo"): f"account-{ordinal_b}/private-project-1",
        }


# ---------------------------------------------------------------------------
# rearm-backtest
# ---------------------------------------------------------------------------


def _rearm_backtest_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    branches: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    spacings: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "branches": branches,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "spacings": spacings,
    })()


def _tool_use_asst(model: str, tool_id: str, *, output: int = 100, ts: str = "2026-05-19T10:00:00.000Z") -> dict:
    """A priced main-thread assistant turn carrying a real Bash tool_use block
    -- _priced's content=[] default can't exercise _hook_observable_boundaries,
    which needs a tool_use/tool_result content shape (not just known usage) to
    tell a tool-call-only stretch apart from a genuine user message."""
    return _priced(model, output=output, ts=ts, content=[_bash_use(tool_id, "echo hi")])


def _ramp_curve_from_records(*sessions_records: list[dict]) -> tuple[dict[str, dict[str, float]], int]:
    """Build _ramp_curve_from_corpus's own input the way _rearm_backtest_report
    does -- one _extract_rearm_session_turns call per session's raw records --
    for TestRampCurveFromCorpus's synthetic-records tests."""
    return _mod._ramp_curve_from_corpus(_mod._extract_rearm_session_turns(recs) for recs in sessions_records)


class TestHookEffectiveFireThreshold:
    def test_200k_window_model_fires_at_40pct_not_the_abs_cap(self):
        """A 200k-context-window model's real fire point is 80,000 (40% of
        its own window) -- well under the 1M-window arm's 360,000 cap, so
        using the cap uniformly for every session would understate how early
        such sessions actually get nudged today."""
        assert _mod._hook_effective_fire_threshold("claude-sonnet-4-5") == 80_000

    def test_1m_window_model_fires_at_the_abs_cap_not_40pct(self):
        """A 1M-context-window model's 40% figure (400,000) exceeds
        _HANDOFF_NUDGE_ABS_CAP, so the cap governs instead."""
        assert _mod._hook_effective_fire_threshold("claude-sonnet-5") == 360_000


class TestHookObservableBoundaries:
    def test_tool_call_only_stretch_produces_no_mid_stretch_boundary(self):
        """Three tool_use turns chained by tool_result-bearing user records,
        then one genuine user message, contribute exactly one internal
        boundary -- at position 3 (after the third turn), not one per turn --
        since Stop only fires once the agent yields back to the user."""
        records = [
            _tool_use_asst("claude-sonnet-5", "t1"),
            _user_msg([_tool_result("t1", "ok")]),
            _tool_use_asst("claude-sonnet-5", "t2"),
            _user_msg([_tool_result("t2", "ok")]),
            _tool_use_asst("claude-sonnet-5", "t3"),
            _user_msg("please continue"),
        ]
        assert _mod._hook_observable_boundaries(records) == [0, 3]

    def test_genuine_multi_turn_conversation_produces_one_boundary_per_turn(self):
        """Each turn immediately followed by a genuine user message
        contributes its own boundary."""
        records = [
            _priced("claude-sonnet-5", output=100), _user_msg("go on"),
            _priced("claude-sonnet-5", output=100), _user_msg("go on"),
            _priced("claude-sonnet-5", output=100), _user_msg("go on"),
        ]
        assert _mod._hook_observable_boundaries(records) == [0, 1, 2, 3]

    def test_session_end_with_no_trailing_user_message_still_surfaces_boundary(self):
        """A session whose last record is an assistant turn with no further
        user message still gets a boundary at session end -- the case
        nudge-handoff-near-context-cap.sh's own Stop registration exists to
        cover (docs/handoff-nudge.md: "registered on both events so a session
        that crosses the threshold on its final turn... still gets warned")."""
        records = [
            _user_msg("go"),
            _priced("claude-sonnet-5", output=100),
            _priced("claude-sonnet-5", output=100),
        ]
        assert _mod._hook_observable_boundaries(records) == [0, 2]


class TestRampCurveFromCorpus:
    def test_turn_index_bucket_edges_match_pr605_bands_including_the_gap(self):
        """PR #605's own table never labeled turn index 10-19 (its bands jump
        from "5-10" to "20-40"); the cascading less-than lookup this reuses
        from _EDIT_OLD_STRING_SIZE_BUCKETS' own convention folds that range
        into "20-40" rather than leaving it unbucketed."""
        cases = {
            0: "0-5", 4: "0-5",
            5: "5-10", 9: "5-10",
            10: "20-40", 19: "20-40", 39: "20-40",
            40: "40-80", 79: "40-80",
            80: "80-150", 149: "80-150",
            150: "150-300", 299: "150-300",
            300: "300+", 1000: "300+",
        }
        for turn_index, expected_label in cases.items():
            assert _mod._ramp_curve_turn_index_bucket(turn_index) == expected_label, turn_index

    def test_sane_rate_and_mean_context_on_synthetic_corpus_with_known_growth(self):
        """A two-turn session with known input/output/context, both turns
        landing in the '0-5' bucket, produces a hand-computed $/1k-output
        rate and output-token-weighted mean context -- not a bounds check."""
        recs = [
            _priced("claude-sonnet-5", input=100_000, output=1000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=200_000, output=3000, ts="2026-05-19T10:01:00.000Z"),
        ]
        curve, total_output_tokens = _ramp_curve_from_records(recs)
        rates = _mod._model_rates("claude-sonnet-5")
        turn1_dollars = 100_000 / 1_000_000 * rates["input"] + 1000 / 1_000_000 * rates["output"]
        turn2_dollars = 200_000 / 1_000_000 * rates["input"] + 3000 / 1_000_000 * rates["output"]
        expected_rate = (turn1_dollars + turn2_dollars) / ((1000 + 3000) / 1000)
        expected_mean_context = (100_000 * 1000 + 200_000 * 3000) / (1000 + 3000)
        assert curve["0-5"]["rate"] == pytest.approx(expected_rate)
        assert curve["0-5"]["mean_context"] == pytest.approx(expected_mean_context)
        assert total_output_tokens == 1000 + 3000

    def test_bucket_with_zero_turns_falls_back_to_corpus_wide_rate_not_nan(self):
        """A corpus with data only in the '0-5' bucket still returns a
        defined, non-NaN rate/mean_context for a bucket with zero turns
        (e.g. '300+'), equal to the corpus-wide rate/context since '0-5' is
        the only contributing bucket -- not a division-by-zero or NaN
        propagating into _simulate_rearm_spacing."""
        recs = [_priced("claude-sonnet-5", input=100_000, output=1000)]
        curve, _total_output_tokens = _ramp_curve_from_records(recs)
        assert curve["300+"]["rate"] == pytest.approx(curve["0-5"]["rate"])
        assert curve["300+"]["mean_context"] == pytest.approx(curve["0-5"]["mean_context"])

    def test_whole_corpus_unpriced_reports_zero_total_output_tokens(self):
        """A corpus whose only turns are on an unpriced model can't compute
        a real ramp curve at all -- total_output_tokens is 0, letting a
        caller (_rearm_backtest_report) tell "genuinely cheap ramp" apart
        from "curve couldn't be computed", which every bucket's own
        rate/mean_context (both silently 0.0 here) can't distinguish."""
        recs = [_priced("claude-opus-4-7", input=100_000, output=1000)]  # unpriced model
        curve, total_output_tokens = _ramp_curve_from_records(recs)
        assert total_output_tokens == 0
        assert curve["0-5"]["rate"] == 0.0


class TestSimulateRearmSpacing:
    def test_hand_computed_dollar_total_with_a_single_split(self):
        """One band crossing splits the session into an actual-priced prefix
        and a ramp-priced remainder -- hand-computed against a synthetic ramp
        curve, not a bounds check against baseline or a naive reprice."""
        ramp_curve = {label: {"rate": 1.0, "mean_context": 0.0} for label in _mod._RAMP_CURVE_BUCKET_LABELS}
        ramp_curve["0-5"] = {"rate": 2.0, "mean_context": 100.0}
        turns = [
            (0, 50, 5.0),
            (50, 60, 6.0),      # abs=110 >= threshold(100) at boundary 2 -> split after this turn
            (110, 10, 999.0),   # post-split turn 0: priced at ramp rate, not the (unreachable) actual 999.0
        ]
        boundaries = [0, 1, 2, 3]
        total, _ctx_weighted, weight = _mod._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100,
        )
        assert total == pytest.approx(5.0 + 6.0 + (10 / 1000 * 2.0))
        assert weight == 50 + 60 + 10

    def test_two_sequential_rearms_within_one_session(self):
        """A remainder that itself crosses a second band splits again -- the
        compounding re-arm this feature exists to model, distinct from a
        one-shot baseline that only ever splits once."""
        ramp_curve = {label: {"rate": 1.0, "mean_context": 0.0} for label in _mod._RAMP_CURVE_BUCKET_LABELS}
        turns = [
            (0, 50, 5.0),
            (50, 60, 6.0),       # split 1 after this turn (abs=110 >= 100)
            (110, 10, 999.0),    # ramp-priced, turns-since-restart 0
            (120, 200, 999.0),   # abs=320 >= 150 (100 + 1*50) -> split 2 after this turn
            (320, 5, 999.0),     # ramp-priced again, turns-since-restart 0 (post split 2)
        ]
        boundaries = [0, 1, 2, 3, 4, 5]
        total, _ctx_weighted, weight = _mod._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100,
        )
        expected = 5.0 + 6.0 + (10 / 1000 * 1.0) + (200 / 1000 * 1.0) + (5 / 1000 * 1.0)
        assert total == pytest.approx(expected)
        assert weight == 50 + 60 + 10 + 200 + 5

    def test_response_lag_delays_the_split_point(self):
        """response_lag_tokens shifts a band's trigger point later -- the
        compliance-realistic model's operator-response-lag correction."""
        ramp_curve = {label: {"rate": 5.0, "mean_context": 0.0} for label in _mod._RAMP_CURVE_BUCKET_LABELS}
        turns = [
            (0, 50, 5.0),
            (50, 60, 6.0),
            (110, 20, 7.0),
        ]
        boundaries = [0, 1, 2, 3]
        total_no_lag, _c1, _w1 = _mod._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100, response_lag_tokens=0,
        )
        total_with_lag, _c2, _w2 = _mod._simulate_rearm_spacing(
            turns, boundaries, spacing=50, ramp_curve=ramp_curve, threshold=100, response_lag_tokens=20,
        )
        # No lag: the crossing fires after turn index 1 (abs=110 >= 100), so
        # turn index 2's dollars are ramp-priced (20/1000*5.0=0.1) instead of actual (7.0).
        assert total_no_lag == pytest.approx(5.0 + 6.0 + 0.1)
        # With a 20-token lag, that same crossing isn't detectable until
        # abs>=120, which only happens after turn index 2 -- too late for any
        # turn to be re-priced, so every turn keeps its actual dollars.
        assert total_with_lag == pytest.approx(5.0 + 6.0 + 7.0)
        assert total_with_lag > total_no_lag


class TestParseNudgeLogEntries:
    def test_all_three_line_shapes_are_parsed(self, tmp_path):
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "nudged session=abc123 est=400000 model=claude-opus-5 window=1000000 event=Stop\n"
            "schema-drift session=def456 event=UserPromptSubmit\n"
            "handoff session=abc123\n"
        )
        assert _mod._parse_nudge_log_entries(log_path) == [
            {"kind": "nudged", "session": "abc123", "est": 400000, "model": "claude-opus-5",
             "window": 1000000, "event": "Stop"},
            {"kind": "schema-drift", "session": "def456", "event": "UserPromptSubmit"},
            {"kind": "handoff", "session": "abc123"},
        ]

    def test_malformed_lines_are_skipped_without_raising(self, tmp_path):
        log_path = tmp_path / ".handoff-nudge.log"
        log_path.write_text(
            "not a recognized line at all\n"
            "nudged session=abc est=not-an-int model=x window=1000000 event=Stop\n"
            "nudged session=abc est=400000 model=x window=1000000\n"  # missing event=
            "nudged session=abc bare-token-no-equals est=400000 model=x window=1000000 event=Stop\n"
            "nudged session=abc est=400000 model=x window=1000000 event=Stop\n"  # valid
        )
        assert _mod._parse_nudge_log_entries(log_path) == [
            {"kind": "nudged", "session": "abc", "est": 400000, "model": "x", "window": 1000000, "event": "Stop"},
        ]

    def test_missing_log_file_returns_empty_list(self, tmp_path):
        assert _mod._parse_nudge_log_entries(tmp_path / "does-not-exist.log") == []


class TestOperatorResponseLagFromLog:
    def test_exact_match_join_measures_lag_past_the_fire_point(self):
        session_traces = {"abc": [100, 200, 405_000, 410_000]}
        log_entries = [
            {"kind": "nudged", "session": "abc", "est": 405_000, "model": "x", "window": 1_000_000, "event": "Stop"},
        ]
        lags, excluded = _mod._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == [410_000 - 405_000]
        assert excluded == 0

    def test_no_match_is_excluded_and_counted_not_silently_dropped(self):
        session_traces = {"abc": [100, 200]}
        log_entries = [
            {"kind": "nudged", "session": "does-not-exist", "est": 100, "model": "x", "window": 1, "event": "Stop"},
        ]
        lags, excluded = _mod._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == []
        assert excluded == 1

    def test_first_value_at_or_above_est_is_picked_over_an_earlier_below_est_value(self):
        """A nudged line carries no timestamp, only est= -- the join skips
        300 (below est=400, however close) and picks 500 (index 2), the
        trace's first value that actually reaches est. This fixture's peak-
        from-fire-point-onward happens to land on the same lag either way a
        fire index is chosen here (the suffix's max value dominates
        regardless of start point), so it does not by itself distinguish
        first-crossing from a nearest-value join -- see
        test_post_compaction_dip_does_not_mis_join_to_a_later_closer_looking_turn
        for the fixture that actually pins that distinction, since a
        same-lag result requires the higher peak to be reachable from every
        candidate start point, which a monotonically non-decreasing trace
        (like this one) always satisfies."""
        session_traces = {"s": [100, 300, 500]}
        log_entries = [{"kind": "nudged", "session": "s", "est": 400, "model": "x", "window": 1, "event": "Stop"}]
        lags, excluded = _mod._operator_response_lag_from_log(session_traces, log_entries)
        # First value >= est is 500 (index 2); peak at or after it is 500,
        # so lag = 500 - 400 = 100.
        assert lags == [100]
        assert excluded == 0

    def test_no_trace_value_reaches_est_is_excluded_not_crashing(self):
        """A trace that never reaches the logged est (e.g. a truncated or
        mismatched transcript) can't identify a fire turn -- excluded and
        counted, not a false join to whichever value happens to be closest."""
        session_traces = {"s": [100, 200, 300]}
        log_entries = [{"kind": "nudged", "session": "s", "est": 400, "model": "x", "window": 1, "event": "Stop"}]
        lags, excluded = _mod._operator_response_lag_from_log(session_traces, log_entries)
        assert lags == []
        assert excluded == 1

    def test_post_compaction_dip_does_not_mis_join_to_a_later_closer_looking_turn(self):
        """A mid-session isCompactSummary drop can produce a later turn
        whose abs-token value is numerically closer to est than the true,
        earlier first-crossing turn -- the join must still land on the first
        turn that actually reaches est, not the nearest-looking one after
        the dip."""
        session_traces = {"abc": [100, 450_000, 900_000, 60_000, 200_000, 449_000]}
        log_entries = [
            {"kind": "nudged", "session": "abc", "est": 400_000, "model": "x", "window": 1, "event": "Stop"},
        ]
        lags, excluded = _mod._operator_response_lag_from_log(session_traces, log_entries)
        # True first crossing is index 1 (450_000 >= est); the peak at or
        # after it is 900_000, so lag = 900_000 - 400_000 = 500_000. A
        # nearest-est join would instead pick index 5 (449_000, closer to
        # est than 450_000 is) and understate the lag to 49_000.
        assert lags == [500_000]
        assert excluded == 0


class TestParseRearmSpacingsArg:
    def test_default_value_when_spacings_is_unset(self):
        """--spacings absent falls back to _REARM_BACKTEST_DEFAULT_SPACINGS."""
        assert _mod._parse_rearm_spacings_arg(argparse.Namespace(spacings=None)) == list(
            _mod._REARM_BACKTEST_DEFAULT_SPACINGS
        )

    def test_non_integer_token_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod._parse_rearm_spacings_arg(argparse.Namespace(spacings="40000,not-a-number"))
        assert exc_info.value.code == 2
        assert "expected comma-separated integers" in capsys.readouterr().err

    def test_non_positive_value_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod._parse_rearm_spacings_arg(argparse.Namespace(spacings="40000,0"))
        assert exc_info.value.code == 2
        assert "values must be positive" in capsys.readouterr().err

    def test_whitespace_only_value_exits_2_at_least_one_required(self, capsys):
        """A --spacings value that's non-empty but strips to nothing on
        every comma-separated token (all whitespace) leaves the parsed list
        empty -- the same "at least one required" exit as an entirely blank
        flag, not a silent empty result."""
        with pytest.raises(SystemExit) as exc_info:
            _mod._parse_rearm_spacings_arg(argparse.Namespace(spacings="   "))
        assert exc_info.value.code == 2
        assert "at least one spacing value is required" in capsys.readouterr().err


class TestSessionMatchesRearmScope:
    """--since and --branches scope whole sessions here (unlike `cost`'s
    per-record --branches filter) -- see _session_matches_rearm_scope's own
    docstring for why."""

    def test_session_excluded_when_first_timestamp_is_before_since_cutoff(self):
        since_ts = _mod._parse_ts("2026-05-10T00:00:00.000Z")
        records = [_asst("claude-sonnet-5", ts="2026-05-01T00:00:00.000Z")]
        assert _mod._session_matches_rearm_scope(records, since_ts, None) is False

    def test_session_included_when_first_timestamp_is_exactly_at_the_since_boundary(self):
        since_ts = _mod._parse_ts("2026-05-10T00:00:00.000Z")
        records = [_asst("claude-sonnet-5", ts="2026-05-10T00:00:00.000Z")]
        assert _mod._session_matches_rearm_scope(records, since_ts, None) is True

    def test_session_excluded_when_no_main_thread_turn_matches_branches(self):
        records = [_asst("claude-sonnet-5", branch="other")]
        assert _mod._session_matches_rearm_scope(records, None, {"main"}) is False

    def test_session_excluded_when_only_a_sidechain_turn_matches_the_branch_filter(self):
        """A sidechain turn sharing the target branch name must not count --
        --branches scopes to main-thread turns only, matching the
        `not bool(r.get("isSidechain"))` guard."""
        records = [_asst("claude-sonnet-5", branch="main", sidechain=True)]
        assert _mod._session_matches_rearm_scope(records, None, {"main"}) is False

    def test_session_included_when_branch_changes_mid_session_and_only_some_turns_match(self):
        records = [
            _asst("claude-sonnet-5", branch="other"),
            _asst("claude-sonnet-5", branch="main"),
        ]
        assert _mod._session_matches_rearm_scope(records, None, {"main"}) is True


class TestRearmBacktestReport:
    """End-to-end coverage against .claude/plans/handoff-nudge-rearm-backtest.md's
    Verification section -- items 2, 4, and 5, encoded as pytests against a
    shared fixture corpus rather than a one-time manual run."""

    def test_baseline_dollars_match_cost_reports_own_total(self, fake_projects, capsys):
        """Verification item 2: the baseline row (today's real recorded
        totals, no re-arm simulation) must equal _cost_report's own total for
        the same fixture scope -- an independent, already-verified code path
        computing the same real, non-counterfactual dollars over the same
        corpus should agree."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=500_000, output=5_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=500_000, output=5_000, ts="2026-05-19T10:01:00.000Z"),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        cost_total = _extract_grand_total(capsys.readouterr().out)

        _mod._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spacing", row_contains="baseline")
        baseline_total = float(cols["$"].replace(",", ""))
        assert baseline_total == pytest.approx(cost_total)

    def test_prints_fixed_threshold_and_model_routing_disclosure(self, fake_projects, capsys):
        """Verification item 5: the report explicitly states that model
        routing and the fixed fire threshold are not backtested."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "Model routing and each session's own fire threshold" in out
        assert "NOT backtested" in out

    def test_excluded_operator_lag_count_is_reported(self, fake_projects, tmp_path, capsys):
        """Verification item 4: a nudged log line that can't be joined to any
        session in scope is counted in the excluded figure, not silently
        dropped."""
        (tmp_path / ".handoff-nudge.log").write_text(
            "nudged session=not-in-scope est=100000 model=claude-sonnet-5 window=1000000 event=Stop\n"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "1 excluded" in out

    def test_200k_window_session_re_arms_off_its_own_80k_threshold(self, fake_projects, capsys):
        """A session on a 200k-context-window model crosses its own real fire
        point (80,000) well under _HANDOFF_NUDGE_ABS_CAP (360,000) -- a
        report that used the cap uniformly for every session would never
        simulate a split for this session at all, understating the re-arm
        benefit on the 200k-window arm entirely."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-4-5", input=70_000, output=5_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-4-5", input=80_000, output=5_000, ts="2026-05-19T10:01:00.000Z"),
            _user_msg("continue", ts="2026-05-19T10:02:00.000Z"),
            _priced("claude-sonnet-4-5", input=90_000, output=5_000, ts="2026-05-19T10:03:00.000Z"),
        ])
        _mod._rearm_backtest_report(_rearm_backtest_args(spacings="40000"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spacing", row_contains=["40,000", "perfect"])
        assert cols["DeltaUSD"] != "-0.00", "40k-spacing row must diverge from baseline once the 80k threshold fires"

    def test_warns_when_no_priced_output_tokens_are_in_scope_for_the_ramp_curve(self, fake_projects, capsys):
        """A corpus with only unpriced-model turns can't derive a real ramp
        curve -- every re-armed remainder would otherwise be silently priced
        at $0 with nothing distinguishing "genuinely cheap ramp" from "curve
        couldn't be computed at all"."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-4-7", input=100_000, output=1_000, ts="2026-05-19T10:00:00.000Z"),
        ])
        _mod._rearm_backtest_report(_rearm_backtest_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "ramp curve could not be computed" in out

    def test_synthetic_no_usage_record_does_not_desync_boundaries_from_main_thread_turns(
        self, fake_projects, capsys
    ):
        """A main-thread assistant record with no usage block (a synthetic
        error record) sits between two real, priced turns -- if it were to
        advance _hook_observable_boundaries' own turn-count position (as it
        would if that function's usage-block guard were ever lost), the
        crossing right after the first real turn would never line up with
        any boundary this report's own main_thread_turns list can use, and
        the second turn would silently keep its actual (unrepriced) dollars.
        Runs the real pipeline end to end and checks a hand-computed dollar
        total, not merely a nonzero delta, so an index mismatch between the
        two functions actually fails the test."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-5", ts="2026-05-19T10:00:00.000Z"),  # no usage block
            _priced("claude-sonnet-5", input=355_000, output=5_000, ts="2026-05-19T10:00:01.000Z"),
            _user_msg("continue", ts="2026-05-19T10:00:02.000Z"),
            _priced("claude-sonnet-5", input=500, output=2_000, ts="2026-05-19T10:00:03.000Z"),
        ])
        _mod._rearm_backtest_report(_rearm_backtest_args(spacings="40000"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Spacing", row_contains=["40,000", "perfect"])
        total = float(cols["$"].replace(",", ""))

        rates = _mod._model_rates("claude-sonnet-5")
        turn0_dollars = 355_000 / 1_000_000 * rates["input"] + 5_000 / 1_000_000 * rates["output"]
        turn1_dollars = 500 / 1_000_000 * rates["input"] + 2_000 / 1_000_000 * rates["output"]
        # Turn 0's own abs-tokens (360,000) crosses the model's 360,000
        # threshold (its 1M-window 40% figure exceeds _HANDOFF_NUDGE_ABS_CAP,
        # so the cap governs), so turn 1 is ramp-priced at the "0-5" bucket's
        # rate -- which, since both turns land in that bucket, is their own
        # blended $/1k-output rate.
        ramp_rate = (turn0_dollars + turn1_dollars) / ((5_000 + 2_000) / 1000)
        expected_total = turn0_dollars + (2_000 / 1000) * ramp_rate
        # abs= accounts for the table's own 2-decimal-place rounding
        # ($X,XXX.XX), not slack in the expected computation itself.
        assert total == pytest.approx(expected_total, abs=0.005)


# ---------------------------------------------------------------------------
# plan-boundary
# ---------------------------------------------------------------------------


class TestExtractRearmSessionTurnsModelAndPosition:
    """main_thread_models / main_thread_record_positions are new parallel
    lists alongside main_thread_turns, not a widening of its own 3-tuple
    shape -- _ramp_curve_from_corpus, _simulate_rearm_spacing, and
    _rearm_backtest_report all positionally unpack that tuple as
    Sequence[tuple[int, int, float]]."""

    def test_models_and_positions_are_parallel_to_main_thread_turns(self):
        records = [
            _priced("claude-opus-5", input=100, output=50, ts="2026-05-19T10:00:00.000Z"),
            _user_msg("go", ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-sonnet-5", input=200, output=75, ts="2026-05-19T10:00:02.000Z"),
        ]
        data = _mod._extract_rearm_session_turns(records)
        assert data["main_thread_models"] == ["claude-opus-5", "claude-sonnet-5"]
        assert len(data["main_thread_record_positions"]) == len(data["main_thread_turns"])
        for turn_index, record_index in enumerate(data["main_thread_record_positions"]):
            rec = data["deduped"][record_index]
            assert rec["message"]["model"] == data["main_thread_models"][turn_index]

    def test_record_positions_skip_sidechain_and_no_usage_records(self):
        """A no-usage synthetic record and a sidechain turn both advance
        "deduped"'s own index but must not appear in
        main_thread_record_positions -- that list only indexes usage-carrying
        main-thread turns, mirroring _hook_observable_boundaries' own
        desync guard."""
        records = [
            _asst("claude-opus-5", ts="2026-05-19T10:00:00.000Z"),  # no usage block
            _priced_sidechain_asst("claude-opus-5", output_tokens=10, ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", input=100, output=50, ts="2026-05-19T10:00:02.000Z"),
        ]
        data = _mod._extract_rearm_session_turns(records)
        assert data["main_thread_record_positions"] == [2]


class TestCacheMissReason:
    """_cache_miss_reason parses message.diagnostics.cache_miss_reason."""

    def test_returns_reason_when_present(self):
        message = {
            "diagnostics": {
                "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 12345},
            }
        }
        assert _mod._cache_miss_reason(message) == "model_changed"

    def test_returns_none_when_diagnostics_absent(self):
        assert _mod._cache_miss_reason({}) is None

    def test_returns_none_when_diagnostics_not_a_dict(self):
        assert _mod._cache_miss_reason({"diagnostics": "oops"}) is None

    def test_returns_none_when_cache_miss_reason_absent(self):
        assert _mod._cache_miss_reason({"diagnostics": {}}) is None

    def test_returns_none_when_cache_miss_reason_not_a_dict(self):
        assert _mod._cache_miss_reason({"diagnostics": {"cache_miss_reason": 42}}) is None

    def test_returns_none_when_cache_miss_reason_type_not_a_string(self):
        message = {"diagnostics": {"cache_miss_reason": {"type": 42}}}
        assert _mod._cache_miss_reason(message) is None

    def test_previous_message_not_found_reason_has_no_cache_missed_input_tokens_field(self):
        """The previous_message_not_found variant carries no
        cache_missed_input_tokens field at all -- the parser must not assume
        its presence."""
        message = {"diagnostics": {"cache_miss_reason": {"type": "previous_message_not_found"}}}
        assert _mod._cache_miss_reason(message) == "previous_message_not_found"

    def test_unavailable_reason_has_no_cache_missed_input_tokens_field(self):
        """The unavailable variant also carries no cache_missed_input_tokens
        field -- the parser must not assume its presence."""
        message = {"diagnostics": {"cache_miss_reason": {"type": "unavailable"}}}
        assert _mod._cache_miss_reason(message) == "unavailable"

    def test_merged_run_takes_cache_miss_reason_from_first_record(self):
        """_merge_assistant_run takes every non-content, non-usage field from
        a requestId run's first record unchanged -- a merged turn's own
        diagnostics survives with no extra collapsing logic added for it."""
        rec_a = _priced("claude-opus-5", output=10, request_id="req-1")
        rec_a["message"]["diagnostics"] = {
            "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 999},
        }
        rec_b = _priced("claude-opus-5", output=20, request_id="req-1")
        rec_b["message"]["diagnostics"] = {"cache_miss_reason": {"type": "something_else"}}
        merged = _mod._dedup_turns_by_request_id([rec_a, rec_b])
        assert len(merged) == 1
        assert _mod._cache_miss_reason(merged[0]["message"]) == "model_changed"


def _plan_boundary_turn_index(records: list[dict]) -> int | None:
    """Run _extract_rearm_session_turns + _plan_boundary_turn_index end to
    end -- the shape _plan_boundary_report itself uses."""
    data = _mod._extract_rearm_session_turns(records)
    return _mod._plan_boundary_turn_index(data["deduped"], data["main_thread_record_positions"])


class TestPlanBoundaryTurnIndex:
    def test_exit_plan_mode_signal_marks_the_boundary_turn(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=30, ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_plan_review_skill_invocation_signal_marks_the_boundary_turn(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, content=[_skill_use("s1", "plan-review")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=30, ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_no_boundary_signal_returns_none(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, ts="2026-05-19T10:00:01.000Z"),
        ]
        assert _plan_boundary_turn_index(records) is None

    def test_boundary_as_the_session_final_turn_still_resolves_a_turn_index(self):
        """The divide-by-zero guard against zero post-boundary turns lives in
        the report, not here -- this function must still return the correct
        index when the boundary turn is the session's last one."""
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_boundary_inside_sidechain_is_ignored(self):
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-5", sidechain=True, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm2")], ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_multiple_boundaries_first_occurrence_wins(self):
        """A session's later ExitPlanMode/plan-review calls are re-planning
        inside work this measurement already treats as post-boundary -- the
        first occurrence is the boundary, not the last."""
        records = [
            _priced("claude-opus-5", output=10, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", output=20, ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-5", output=30, content=[_skill_use("s1", "plan-review")], ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 0

    def test_no_usage_record_before_boundary_does_not_desync_boundary_index(self):
        """A main-thread assistant record with no usage block sitting before
        the real boundary turn must not shift the boundary's own mapped
        main_thread_turns index -- main_thread_record_positions gives
        the boundary's own "deduped" record index directly, so no manual
        turn-counting desync (the class of bug
        test_synthetic_no_usage_record_does_not_desync_boundaries_from_main_thread_turns
        guards in the sibling rearm-backtest code) is possible here."""
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-5", ts="2026-05-19T10:00:01.000Z"),  # no usage block
            _priced("claude-opus-5", output=20, content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:02.000Z"),
            _priced("claude-opus-5", output=30, ts="2026-05-19T10:00:03.000Z"),
        ]
        assert _plan_boundary_turn_index(records) == 1

    def test_no_usage_record_at_the_trigger_itself_cannot_be_mapped_and_returns_none(self):
        """A boundary signal on a record with no usage block has no entry in
        main_thread_record_positions to map onto -- undetectable, not
        silently mapped to the wrong main_thread_turns index."""
        records = [
            _priced("claude-opus-5", output=10, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-5", content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),  # no usage
            _priced("claude-opus-5", output=20, ts="2026-05-19T10:00:02.000Z"),
        ]
        assert _plan_boundary_turn_index(records) is None


class TestArmBBoundaryPlusOneDollars:
    def test_charges_sonnet_cache_write_over_boundary_context_and_own_new_tokens_no_cache_read(self):
        """Component-level pin: expected values derive from
        _model_rates("claude-sonnet-5") per token class, never a hardcoded
        multiplier -- a hardcoded value would pass even if the price table
        drifted."""
        usage = {
            "input_tokens": 500, "output_tokens": 1000,
            "cache_read_input_tokens": 999_000,  # must be ignored entirely, never scaled
            "cache_creation_input_tokens": 0,
        }
        boundary_context_tokens = 200_000
        result = _mod._arm_b_boundary_plus_one_dollars(usage, boundary_context_tokens)

        rates = _mod._model_rates("claude-sonnet-5")
        expected = (
            boundary_context_tokens / 1_000_000 * rates["cache_write_5m"]
            + 500 / 1_000_000 * rates["input"]
            + 1000 / 1_000_000 * rates["output"]
        )
        assert result == pytest.approx(expected)

    def test_cache_read_never_changes_the_result(self):
        """Regression guard against scaling the observed cache-read and also
        charging a cache-write over the same tokens -- double-billing them
        as both a read and a write."""
        usage_no_read = {"input_tokens": 500, "output_tokens": 1000, "cache_read_input_tokens": 0}
        usage_high_read = {"input_tokens": 500, "output_tokens": 1000, "cache_read_input_tokens": 500_000}
        assert _mod._arm_b_boundary_plus_one_dollars(usage_no_read, 200_000) == pytest.approx(
            _mod._arm_b_boundary_plus_one_dollars(usage_high_read, 200_000)
        )


class TestArmBLaterTurnDollars:
    def test_prices_observed_read_write_split_at_sonnet_rates(self):
        """Component-level pin: every turn after boundary+1 carries the
        observed read/write split forward, priced at Sonnet rates via
        _model_rates, not the turn's real (Opus) model."""
        usage = {
            "input_tokens": 200, "output_tokens": 800,
            "cache_read_input_tokens": 50_000,
            "cache_creation_input_tokens": 3_000,
            "cache_creation": {"ephemeral_1h_input_tokens": 1_000, "ephemeral_5m_input_tokens": 2_000},
        }
        result = _mod._arm_b_later_turn_dollars(usage)

        rates = _mod._model_rates("claude-sonnet-5")
        expected = (
            200 / 1_000_000 * rates["input"]
            + 800 / 1_000_000 * rates["output"]
            + 50_000 / 1_000_000 * rates["cache_read"]
            + 1_000 / 1_000_000 * rates["cache_write_1h"]
            + 2_000 / 1_000_000 * rates["cache_write_5m"]
        )
        assert result == pytest.approx(expected)

    def test_zero_cache_creation_prices_cleanly(self):
        usage = {
            "input_tokens": 100, "output_tokens": 100,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }
        rates = _mod._model_rates("claude-sonnet-5")
        expected = 100 / 1_000_000 * rates["input"] + 100 / 1_000_000 * rates["output"]
        assert _mod._arm_b_later_turn_dollars(usage) == pytest.approx(expected)


class TestArmCTurnDollars:
    def test_prices_via_multiply_back_convention(self):
        """Arm C prices (output_tokens/1000) * the ramp curve's own bucket
        rate -- never a scaled actual-dollars figure, which would double-
        count the model-price gap already embedded in the observed dollars."""
        ramp_curve = {"0-5": {"rate": 3.5, "mean_context": 10_000}}
        assert _mod._arm_c_turn_dollars(2_000, 0, ramp_curve) == pytest.approx((2_000 / 1000) * 3.5)

    def test_falls_back_to_zero_rate_when_ramp_curve_has_no_bucket_entry(self):
        assert _mod._arm_c_turn_dollars(1_000, 0, {}) == 0.0


class TestPlanBoundaryWorkInflationBreakeven:
    def test_breakeven_pin_against_hand_computed_delta(self):
        """Hand-computed fixture: a $6 cheaper arm with a $4 delta over 100
        post-boundary turns / 10,000 output tokens absorbs pct=4/6 more work
        (~66.67 extra turns, ~6,666.7 extra output tokens) before the
        advantage disappears."""
        result = _mod._plan_boundary_work_inflation_breakeven(6.0, 4.0, 100, 10_000)
        assert result["pct"] == pytest.approx(4.0 / 6.0)
        assert result["extra_turns"] == pytest.approx(100 * (4.0 / 6.0))
        assert result["extra_output_tokens"] == pytest.approx(10_000 * (4.0 / 6.0))

    def test_zero_cheaper_dollars_returns_none_fields(self):
        result = _mod._plan_boundary_work_inflation_breakeven(0.0, 4.0, 100, 10_000)
        assert result == {"pct": None, "extra_turns": None, "extra_output_tokens": None}


def _plan_boundary_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


def _opus_boundary_session(*, boundary_content: list | None = None) -> list[dict]:
    """One Opus-anchored session with a plan boundary at main-thread turn
    index 1 and two post-boundary turns -- the shared fixture for
    plan-boundary's end-to-end report tests."""
    boundary_content = boundary_content if boundary_content is not None else [_exit_plan_mode("epm1")]
    return [
        _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
        _user_msg("approve the plan", ts="2026-05-19T10:00:01.000Z"),
        _priced("claude-opus-5", input=2000, output=200, cache_read=100_000, content=boundary_content,
                ts="2026-05-19T10:00:02.000Z"),
        _user_msg("go", ts="2026-05-19T10:00:03.000Z"),
        _priced("claude-opus-5", input=500, output=300, cache_read=150_000, ts="2026-05-19T10:00:04.000Z"),
        _user_msg("go", ts="2026-05-19T10:00:05.000Z"),
        _priced("claude-opus-5", input=500, output=400, cache_read=155_000, ts="2026-05-19T10:00:06.000Z"),
    ]


class TestPlanBoundaryReport:
    def test_unpriced_sonnet_model_fails_at_report_start_not_mid_scan(self, fake_projects, monkeypatch, capsys):
        """Arms B and C reprice every post-boundary turn at
        _PLAN_BOUNDARY_SONNET_MODEL's rates; an unpriced model must fail the
        whole report up front with a named message, not crash mid-scan on an
        arbitrary turn."""
        monkeypatch.setattr(_mod, "_PLAN_BOUNDARY_SONNET_MODEL", "claude-sonnet-5-unpriced-test-double")
        _write_jsonl(fake_projects / "sess.jsonl", _opus_boundary_session())
        with pytest.raises(SystemExit) as exc_info:
            _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        assert exc_info.value.code != 0
        assert "claude-sonnet-5-unpriced-test-double" in capsys.readouterr().err

    def test_redacts_by_default(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", _opus_boundary_session())
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER not in out

    def test_no_redact_prints_do_not_publish_banner(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", _opus_boundary_session())
        _mod._plan_boundary_report(_plan_boundary_args(no_redact=True), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in out

    def test_no_redact_refused_by_report_itself_when_multi_root(self, tmp_path):
        """Defense-in-depth, mirroring _cache_rebuild_report's own version of
        this test: every test in this module calls _plan_boundary_report
        directly, bypassing _resolve_cost_roots' CLI-level enforcement."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _opus_boundary_session())
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", _opus_boundary_session())
        with pytest.raises(SystemExit) as exc_info:
            _mod._plan_boundary_report(_plan_boundary_args(no_redact=True), date(2026, 8, 16), roots=[root_a, root_b])
        assert exc_info.value.code == 2

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """CLI-level enforcement, mirroring the sibling cost/context-distribution/
        edit-format/subagent-mix tests of the same name: cmd_plan_boundary itself
        (via _resolve_cost_roots), not just _plan_boundary_report, refuses the
        combination."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_plan_boundary(_plan_boundary_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_output_never_contains_plan_text_or_plan_file_path(self, fake_projects, capsys):
        """Negative assertion: a fixture record carrying plan text and
        planFilePath must never surface in this report's output, redacted or
        not -- boundary records are consumed for type and position only."""
        secret_plan_text = "REDACT-ME-PLAN-BODY"
        secret_plan_file_path = "REDACT-ME-PLAN-FILE-PATH"
        session = _opus_boundary_session(
            boundary_content=[{
                "type": "tool_use", "id": "epm1", "name": "ExitPlanMode",
                "input": {"plan": secret_plan_text, "planFilePath": secret_plan_file_path},
            }]
        )
        _write_jsonl(fake_projects / "sess.jsonl", session)
        for no_redact in (False, True):
            _mod._plan_boundary_report(
                _plan_boundary_args(no_redact=no_redact), date(2026, 8, 16), roots=[fake_projects.parent]
            )
            out = capsys.readouterr().out
            assert secret_plan_text not in out
            assert secret_plan_file_path not in out
            assert "planFilePath" not in out

    def test_output_never_contains_a_per_session_breakdown(self, fake_projects, tmp_path, monkeypatch, capsys):
        """Aggregate-only claim: across the whole argument surface (default
        redact, --no-redact, --since, multi-root, --this-repo), no output
        ever names an individual session."""
        _write_jsonl(fake_projects / "sess-one.jsonl", _opus_boundary_session())
        _write_jsonl(fake_projects / "sess-two.jsonl", _opus_boundary_session())
        single_root = [fake_projects.parent]

        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _opus_boundary_session())
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", _opus_boundary_session())
        multi_root = [root_a, root_b]

        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: ["-home-user-testrepo"])

        for kwargs, roots in (
            ({}, single_root),
            ({"no_redact": True}, single_root),
            ({"since": "365d"}, single_root),
            ({}, multi_root),
            ({"this_repo": True}, single_root),
        ):
            _mod._plan_boundary_report(_plan_boundary_args(**kwargs), date(2026, 8, 16), roots=roots)
            out = capsys.readouterr().out
            assert "sess-one" not in out
            assert "sess-two" not in out
            assert "sess-a" not in out
            assert "sess-b" not in out
            assert "session-" not in out  # _redact_session_id's own placeholder shape

    def test_unpriced_post_boundary_model_lands_in_unpriced_turns_not_priced_at_zero(self, fake_projects, capsys):
        """A post-boundary turn on a model with no price-table entry is
        excluded from arm A's own dollar total and counted as unpriced, not
        silently priced at $0 and folded in as if genuinely free."""
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-opus-4-9-unpriced", input=500, output=300, ts="2026-05-19T10:00:02.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Plan-boundary sessions repriced: 1" in out
        assert "1 unpriced turns" in out

    def test_unpriced_post_boundary_turn_contributes_zero_dollars_to_every_arm_symmetrically(
        self, fake_projects, capsys
    ):
        """An unpriced post-boundary turn among priced ones must be excluded
        from arms B and C the same way it's already excluded from arm A --
        repricing it from raw tokens would bias every dollar comparison
        toward arm A."""
        boundary_turn = _priced(
            "claude-opus-5", input=2000, output=200, cache_read=100_000,
            content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z",
        )
        priced_offset0 = _priced(
            "claude-opus-5", input=500, output=300, cache_read=150_000, ts="2026-05-19T10:00:02.000Z"
        )
        unpriced_offset1 = _priced(
            "claude-opus-4-9-unpriced", input=600, output=400, cache_read=99_000, ts="2026-05-19T10:00:03.000Z"
        )
        priced_offset2 = _priced(
            "claude-opus-5", input=500, output=250, cache_read=160_000, ts="2026-05-19T10:00:04.000Z"
        )
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            boundary_turn,
            priced_offset0,
            unpriced_offset1,
            priced_offset2,
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        ramp_curve, _ramp_tokens = _ramp_curve_from_records(session)
        boundary_context_tokens = _mod._context_at_turn(boundary_turn["message"]["usage"])

        def priced_dollars(rec: dict) -> float:
            dollars_by_class, _c, _u = _mod._price_turn("claude-opus-5", rec["message"]["usage"])
            return sum(dollars_by_class.values())

        expected_arm_a = priced_dollars(priced_offset0) + priced_dollars(priced_offset2)
        expected_arm_b = (
            _mod._arm_b_boundary_plus_one_dollars(priced_offset0["message"]["usage"], boundary_context_tokens)
            + _mod._arm_b_later_turn_dollars(priced_offset2["message"]["usage"])
        )
        expected_arm_c = _mod._arm_c_turn_dollars(300, 0, ramp_curve) + _mod._arm_c_turn_dollars(250, 2, ramp_curve)

        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "1 unpriced turns" in out
        assert _extract_arm_dollars(out, "A: continue on Opus") == pytest.approx(expected_arm_a, abs=0.005)
        assert _extract_arm_dollars(out, "B: switch to Sonnet") == pytest.approx(expected_arm_b, abs=0.005)
        assert _extract_arm_dollars(out, "C: fresh Sonnet handoff") == pytest.approx(expected_arm_c, abs=0.005)

    def test_boundary_as_final_turn_is_excluded_without_dividing_by_zero(self, fake_projects, capsys):
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "final main-thread turn (excluded, no post-boundary work): 1" in out
        assert "No plan-boundary sessions with post-boundary work found in scope." in out

    def test_no_boundary_session_is_excluded_and_counted(self, fake_projects, capsys):
        session = [
            _priced("claude-opus-5", input=1000, output=100, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, ts="2026-05-19T10:00:01.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "No plan boundary detected: 1" in out

    def test_multiple_post_boundary_model_switches_do_not_break_ground_truth_check(self, fake_projects, capsys):
        """A session with more than one post-boundary model switch must not
        crash or miscount the boundary+1 ground-truth check, which looks
        only at the single boundary_index -> boundary_index+1 transition."""
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-sonnet-5", input=500, output=300, cache_read=0, ts="2026-05-19T10:00:02.000Z"),
            _priced("claude-opus-5", input=500, output=300, cache_read=150_000, ts="2026-05-19T10:00:03.000Z"),
            _priced("claude-sonnet-5", input=500, output=300, cache_read=0, ts="2026-05-19T10:00:04.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Sessions with a real model change observed at boundary+1: 1 of 1" in out

    def test_ground_truth_buckets_previous_message_not_found_reason(self, fake_projects, capsys):
        """A missing/malformed cache_miss_reason, including the
        previous_message_not_found variant (which carries no
        cache_missed_input_tokens field), must be reported rather than
        raising or silently dropped."""
        boundary_plus_one = _priced(
            "claude-sonnet-5", input=500, output=300, cache_read=0, ts="2026-05-19T10:00:02.000Z"
        )
        boundary_plus_one["message"]["diagnostics"] = {"cache_miss_reason": {"type": "previous_message_not_found"}}
        session = [
            _priced("claude-opus-5", input=1000, output=100, cache_read=50_000, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-opus-5", input=2000, output=200, cache_read=100_000,
                    content=[_exit_plan_mode("epm1")], ts="2026-05-19T10:00:01.000Z"),
            boundary_plus_one,
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "previous_message_not_found: 1" in out

    def test_zero_sessions_in_scope_prints_zero_state_without_crashing(self, fake_projects, capsys):
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Sessions scanned: 0" in out
        assert "No plan-boundary sessions with post-boundary work found in scope." in out

    def test_arm_c_ramp_curve_is_scoped_to_sonnet_anchored_sessions_not_pooled(self, fake_projects, capsys):
        """Arm C models a fresh Sonnet handoff, so its ramp curve must come
        from Sonnet-anchored ramp sessions only -- a curve pooled across both
        families would misprice arm C toward Opus's own rate."""
        opus_ramp_session = [
            _priced("claude-opus-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:00:00.000Z"),
            _priced("claude-opus-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:00:01.000Z"),
        ]
        sonnet_ramp_session = [
            _priced("claude-sonnet-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:01:00.000Z"),
            _priced("claude-sonnet-5", input=1000, output=1000, cache_read=50_000, ts="2026-05-19T09:01:01.000Z"),
        ]
        boundary_session = _opus_boundary_session()
        _write_jsonl(fake_projects / "ramp-opus.jsonl", opus_ramp_session)
        _write_jsonl(fake_projects / "ramp-sonnet.jsonl", sonnet_ramp_session)
        _write_jsonl(fake_projects / "boundary.jsonl", boundary_session)

        sonnet_only_curve, _sonnet_only_tokens = _ramp_curve_from_records(sonnet_ramp_session)
        pooled_curve, _pooled_tokens = _ramp_curve_from_records(opus_ramp_session, sonnet_ramp_session, boundary_session)
        # Sanity check: if the two curves' "0-5" rates happened to agree, this
        # test couldn't distinguish Sonnet-only scoping from pooling.
        assert sonnet_only_curve["0-5"]["rate"] != pooled_curve["0-5"]["rate"]

        # _opus_boundary_session's two post-boundary turns (output 300, 400)
        # both fall in the "0-5" turns-since-boundary bucket.
        expected_arm_c_dollars = (
            _mod._arm_c_turn_dollars(300, 0, sonnet_only_curve)
            + _mod._arm_c_turn_dollars(400, 1, sonnet_only_curve)
        )

        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert _extract_arm_dollars(out, "C: fresh Sonnet handoff") == pytest.approx(expected_arm_c_dollars, abs=0.005)

    def test_sonnet_anchored_session_is_excluded_from_opus_anchored_count(self, fake_projects, capsys):
        """A session whose first main-thread turn is on Sonnet, not Opus, is
        excluded from repricing entirely -- Opus-anchored-only scope is this
        measurement's own design choice, not a general corpus convention."""
        session = [
            _priced("claude-sonnet-5", input=1000, output=100, ts="2026-05-19T10:00:00.000Z"),
            _priced("claude-sonnet-5", input=2000, output=200, content=[_exit_plan_mode("epm1")],
                    ts="2026-05-19T10:00:01.000Z"),
            _priced("claude-sonnet-5", input=500, output=300, ts="2026-05-19T10:00:02.000Z"),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", session)
        _mod._plan_boundary_report(_plan_boundary_args(), date(2026, 8, 16), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Sessions scanned: 1" in out
        assert "Opus-anchored: 0" in out
        assert "No plan-boundary sessions with post-boundary work found in scope." in out


class TestPlanBoundaryArgparseWiring:
    def test_registers_plan_boundary_subcommand_with_expected_defaults(self):
        parser = _mod.build_parser()
        args = parser.parse_args(["plan-boundary"])
        assert args.since is None
        assert args.extra_config_dirs is None
        assert args.no_redact is False
        assert args.func == _mod.cmd_plan_boundary
