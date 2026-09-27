"""Tests for transcript_analysis/read_scope.py (cmd_read_scope)."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from .conftest import (
    _asst,
    _bash_use,
    _compact_boundary_rec,
    _opus,
    _tool_result,
    _user_msg,
    _write_cost_root,
    _write_jsonl,
    _write_subagent_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


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
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        _mod.read_scope._read_scope_report(_read_scope_args(), roots=[root_a, root_b])
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
        _mod.read_scope._read_scope_report(_read_scope_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert _extract_account_read_calls(out, "account-1") == 1
        assert "account-2  no Read calls" in out

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.read_scope.cmd_read_scope(_read_scope_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root_content_unchanged(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * 40),
        ])
        _mod.read_scope._read_scope_report(_read_scope_args(no_redact=True))
        out = capsys.readouterr().out
        assert _mod._DO_NOT_PUBLISH_BANNER in out
        assert _extract_read_total(out) == 1

    def test_no_redact_refused_by_read_scope_report_itself_even_when_called_directly(self, tmp_path, capsys):
        """Defense-in-depth: _read_scope_report must refuse the multi-root +
        --no-redact combination itself rather than trusting that
        _resolve_cost_roots already validated it, mirroring
        test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly.
        Refusal happens before any output is printed."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _opus([_read_tool_use("r2", file_path="/b.py")]),
        ])
        with pytest.raises(SystemExit) as exc_info:
            _mod.read_scope._read_scope_report(_read_scope_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2
        assert capsys.readouterr().out == ""

    def test_since_flag_filters_growth_deltas_at_report_level(self, fake_projects, capsys):
        records = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=10),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=50),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=90),
        ]
        _write_jsonl(fake_projects / "sess.jsonl", records)
        _mod.read_scope._read_scope_report(_read_scope_args(since="9999d"))
        out_all_time = capsys.readouterr().out
        assert _extract_growth_tokens(out_all_time) == 80

        _mod.read_scope._read_scope_report(_read_scope_args(since="1d"))
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
        _mod.read_scope._read_scope_report(_read_scope_args())
        out = capsys.readouterr().out
        assert _extract_growth_tokens(out) == 160
        assert "repeat reads: 1" in out

    def test_locate_step_reports_zero_without_division_error(self, fake_projects, capsys):
        """No Grep/Glob calls in scope must print a zero locate-step line,
        not raise ZeroDivisionError computing the mean."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        _mod.read_scope._read_scope_report(_read_scope_args())
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_TARGETED] == 1
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE] == 0

    def test_limit_zero_lands_in_targeted_not_whole_file(self):
        """limit=0 is falsy in Python but a valid present value -- pins the
        is-not-None classification discipline against a truthiness
        regression that would misclassify it as whole-file, mirroring the
        offset=0 case above."""
        records = [
            _opus([_read_tool_use("r1", file_path="/foo.py", limit=0)]),
            _read_result("r1", ""),
        ]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_TARGETED] == 1
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_TARGETED] == 2
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE] == 0

    def test_subagent_merged_read_lands_in_subagent_scope_not_main(self):
        """The published 'whole-file tokens inside subagents' figure
        depends on this bucketing being right."""
        records = [
            _opus([_read_tool_use("r1", file_path="/main.py")]),
            _read_result("r1", "z" * 4000),
            _asst("claude-sonnet-4-6", sidechain=True, content=[_read_tool_use("r2", file_path="/sub.py")]),
            _read_result("r2", "w" * 4000),
        ]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_scope_count"][
            (_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE, _mod.read_scope._READ_SCOPE_SCOPE_MAIN)
        ] == 1
        assert stats["cohort_scope_count"][
            (_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE, _mod.read_scope._READ_SCOPE_SCOPE_SUBAGENT)
        ] == 1

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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        cohort_scope = (_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE, _mod.read_scope._READ_SCOPE_SCOPE_MAIN)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert _mod.read_scope._read_scope_cohort_bucket_token_total(stats, _mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE) == 760

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
        stats = _mod.read_scope._scan_read_scope_session(records, groups, None)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["repeat_whole_file_reads"] == 1
        assert stats["repeat_whole_file_output_log_reads"] == 0

    def test_non_read_tool_with_offset_limit_shaped_input_not_counted(self):
        records = [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "x", "offset": 5, "limit": 10}}]),
        ]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["locate_call_n"] == 1
        assert stats["locate_result_tokens_total"] == 0

    def test_error_result_counted_and_excluded_from_histogram(self):
        records = [
            _opus([_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "permission denied", is_error=True),
        ]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["error_result"] == 1
        assert stats["cohort_scope_count"][
            (_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE, _mod.read_scope._READ_SCOPE_SCOPE_MAIN)
        ] == 0
        assert stats["read_total"] == 1

    def test_unpaired_read_call_counted_but_call_still_in_census(self):
        records = [_opus([_read_tool_use("r1", file_path="/a.py")])]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["unpaired"] == 1
        assert stats["read_total"] == 1

    def test_non_text_result_counted_and_excluded_from_histogram(self):
        records = [
            _opus([_read_tool_use("r1", file_path="/a.png")]),
            _read_result("r1", [{"type": "image", "source": {"type": "base64", "data": "..."}}]),
        ]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["non_text_result"] == 1
        assert stats["cohort_scope_count"][
            (_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE, _mod.read_scope._READ_SCOPE_SCOPE_MAIN)
        ] == 0
        assert stats["read_total"] == 1

    def test_pages_read_lands_in_own_counter_not_a_cohort(self):
        records = [
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/doc.pdf", "pages": "1-3"}}]),
            _read_result("r1", "page text " * 20),
        ]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_PAGES] == 1
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_TARGETED] == 0
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE] == 0
        assert stats["cohort_scope_count"][
            (_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE, _mod.read_scope._READ_SCOPE_SCOPE_MAIN)
        ] == 0
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_TARGETED] == 0
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_WHOLE_FILE] == 0
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_UNPARSED] == 1
        assert stats["read_total"] == 1

    def test_read_call_with_empty_input_lands_in_unparsed_input(self):
        records = [_opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}])]
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
        assert stats["cohort_n"][_mod.read_scope._READ_SCOPE_COHORT_UNPARSED] == 1
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
        stats = _mod.read_scope._scan_read_scope_session(records, [records], None)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 20

    def test_two_subagent_files_do_not_produce_a_cross_file_delta(self):
        """Flattened into one chain, the second file's first turn
        (context=500) would be diffed against the first file's only turn
        (context=10), counting 490 tokens of growth that never happened."""
        group_a = [_growth_asst(ts="2026-05-19T10:00:00.000Z", context=10)]
        group_b = [_growth_asst(ts="2026-05-19T10:05:00.000Z", context=500)]
        stats = _mod.read_scope._scan_read_scope_session(group_a + group_b, [group_a, group_b], None)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 80

    def test_absent_usage_turn_skipped_not_treated_as_zero_context(self):
        """Treating the missing-usage turn as context=0 would manufacture a
        150-token spike on the following turn instead of the true 50."""
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=100),
            _asst("claude-sonnet-5", ts="2026-05-19T10:01:00.000Z", content=[]),
            _growth_asst(ts="2026-05-19T10:02:00.000Z", context=150),
        ]
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
        assert stats["growth_tokens"] == 50

    def test_shrinking_context_yields_no_negative_contribution(self):
        group = [
            _growth_asst(ts="2026-05-19T10:00:00.000Z", context=100),
            _growth_asst(ts="2026-05-19T10:01:00.000Z", context=40),
        ]
        stats = _mod.read_scope._scan_read_scope_session(group, [group], None)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], since_ts)
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
        stats = _mod.read_scope._scan_read_scope_session(group, [group], since_ts)
        assert stats["growth_tokens"] == 0
        assert stats["growth_unparseable_ts_excluded"] == 1
