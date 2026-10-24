"""Tests for transcript_analysis/instrument_authoring.py's cmd_instrument_authoring: heredoc,
inline-program, and scratchpad-Write classification, main-thread and subagent scope attribution,
size buckets, spawn-dispatch cohorts, and the aggregate-only output that prints no authored payload.
"""
import re

import pytest
from transcript_analysis import instrument_authoring

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _opus,
    _user_msg,
    _write_jsonl,
    _write_subagent_jsonl,
    _write_use,
)


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
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
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
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        zero_sessions, _zero_chars = _extract_instrument_authoring_cohort(out, "zero_dispatch")
        dispatched_sessions, _dispatched_chars = _extract_instrument_authoring_cohort(out, "dispatched")
        assert zero_sessions == 1
        assert dispatched_sessions == 1

    def test_size_histogram_bucket_wired_through_report(self, fake_projects, capsys):
        """The per-scan size bucket reaches the printed histogram section,
        not just _scan_instrument_authoring_session's own returned dict."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_bash_use("b1", "python3 -c 'print(1)'")])])
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert _extract_instrument_authoring_bucket_count(out, "main", "0-99") == 1

    def test_no_authored_payload_text_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        sentinel = "DISTINCTIVE-SENTINEL-PAYLOAD-MUST-NOT-LEAK"
        command = f"python3 -c 'print(\"{sentinel}\")'"
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_bash_use("b1", command)])])
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert sentinel not in out

    def test_no_heredoc_body_text_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        sentinel = "DISTINCTIVE-HEREDOC-SENTINEL-MUST-NOT-LEAK"
        command = f"cat <<EOF > /tmp/instrument.py\n{sentinel}\nEOF\n"
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_bash_use("b1", command)])])
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert sentinel not in out

    def test_no_write_content_text_appears_anywhere_in_printed_report(self, fake_projects, capsys):
        sentinel = "DISTINCTIVE-WRITE-SENTINEL-MUST-NOT-LEAK"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_write_use("w1", sentinel, path="/tmp/scratch/instrument.py")]),
        ])
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
        out = capsys.readouterr().out
        assert sentinel not in out

    def test_zero_calls_prints_zeroes_without_division_error(self, fake_projects, capsys):
        """An empty scope (no authoring calls anywhere) prints a zero census
        and 0.0% cohort shares rather than raising ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        instrument_authoring._instrument_authoring_report(_instrument_authoring_args())
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len("print('hi')")

    def test_inline_python_one_liner_lands_in_smallest_bucket(self):
        records = [_opus([_bash_use("b1", "python3 -c 'print(1)'")])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["call_n"][("write", "main")] == 1
        assert stats["payload_chars"][("write", "main")] == 40

    def test_spawn_count_from_both_agent_and_task_tool_names(self):
        records = [
            _opus([_agent_use("a1", "general-purpose", tool_name="Agent")]),
            _opus([_agent_use("a2", "code-writer", tool_name="Task")]),
        ]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(subagent_records)
        assert stats["spawn_dispatch_n"] == 0
        assert stats["call_n"][("bash", "subagent")] == 1
        assert stats["call_n"][("bash", "main")] == 0

        main_records = [_opus([_agent_use("a2", "general-purpose", tool_name="Task")])]
        main_stats = instrument_authoring._scan_instrument_authoring_session(main_records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["main_payload_chars"] == len("main-payload")

    def test_ordinary_bash_call_not_classified_as_authoring(self):
        records = [
            _opus([_bash_use("b1", "git log --oneline -10")]),
            _opus([_bash_use("b2", "pytest claude/.claude/scripts/tests/")]),
        ]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len(inline_program)

    @pytest.mark.parametrize("command", [
        "bash -e deploy.sh",  # errexit, not inline-eval
        "python3 -m pytest",  # module flag, not -c
    ])
    def test_recognized_interpreter_with_wrong_flag_not_counted(self, command):
        """A recognized interpreter bound to a flag other than its own
        inline-eval flag (bash -e, python3 -m) never counts."""
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0

    def test_heredoc_dash_variant_with_quoted_delimiter_strips_leading_tabs(self):
        command = "cat <<-'EOF'\n\thello\n\tEOF\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len("hello")

    def test_heredoc_non_eof_delimiter_recognized(self):
        command = "cat <<PAYLOAD\nbody text\nPAYLOAD\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len("body text")

    def test_heredoc_body_containing_delimiter_word_as_data_does_not_truncate(self):
        command = "cat <<EOF\nfirst line\nEOF is mentioned here, not a real terminator\nlast line\nEOF\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        expected_body = "first line\nEOF is mentioned here, not a real terminator\nlast line"
        assert stats["payload_chars"][("bash", "main")] == len(expected_body)

    def test_two_heredocs_chained_with_and_sum_payloads(self):
        command = "cat <<A > /tmp/a.txt && cat <<B > /tmp/b.txt\nfirst\nA\nsecond\nB\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len("first") + len("second")

    def test_quoted_herestring_not_misparsed_as_heredoc(self):
        """A bash here-string (`<<<`) with a quoted right-hand side is not
        mistaken for a heredoc opener."""
        command = 'cat <<< "$var"\necho after\n'
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert sum(stats["call_n"].values()) == 0

    def test_unquoted_herestring_not_misparsed_as_heredoc(self):
        """Regression test: `cat <<<EOF` (an unquoted here-string, not a
        heredoc) must not match the heredoc-opener regex starting one
        character into the `<<<` operator -- doing so would consume every
        following line as a fake heredoc body since no line ever equals the
        delimiter "EOF"."""
        command = "cat <<<EOF\necho after\n"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        expected_body = f"print('hi')\n{inline_example_line}"
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len(expected_body)

    def test_double_quoted_inline_program_argument_strips_quotes(self):
        """_extract_shell_arg_at's double-quoted branch (vs. the single-
        quoted/bare forms every other inline-program test here uses) also
        strips the surrounding quotes from the payload it measures."""
        command = 'python3 -c "print(1)"'
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["payload_chars"][("bash", "main")] == len("print(1)")

    def test_versioned_python_interpreter_recognized(self):
        """Regression test: `python3.11 -c '...'` must classify as authoring
        -- a bare `\\bpython3\\b` word boundary fails between "3" and "."
        (both word-adjacent-to-non-word), which previously blocked the match
        entirely."""
        command = "python3.11 -c 'print(1)'"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 1
        assert stats["payload_chars"][("bash", "main")] == len("print(1)")

    def test_unparsed_bash_and_write_input_counted_in_own_cohort_not_dropped(self):
        records = [
            _opus([{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"__unparsedToolInput": "x"}}]),
            _opus([{"type": "tool_use", "id": "w1", "name": "Write", "input": {"__unparsedToolInput": "y"}}]),
        ]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["unparsed_n"]["main"] == 2
        assert sum(stats["call_n"].values()) == 0

    def test_parallel_tool_use_blocks_in_one_turn_both_counted(self):
        records = [
            _opus([
                _bash_use("b1", "python3 -c 'print(1)'"),
                _bash_use("b2", "node -e 'console.log(2)'"),
            ]),
        ]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
        assert stats["call_n"][("bash", "main")] == 2

    def test_bucket_boundary_at_largest_finite_bucket_edge(self):
        """A payload of exactly 9999 chars (the largest finite bucket's own
        upper edge) lands in "2000-9999", not the 10000+ overflow -- pins the
        exclusive-upper-bound convention against an off-by-one."""
        payload = "x" * 9999
        command = f"python3 -c '{payload}'"
        records = [_opus([_bash_use("b1", command)])]
        stats = instrument_authoring._scan_instrument_authoring_session(records)
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
        assert instrument_authoring._instrument_authoring_size_bucket(chars) == expected_bucket

    def test_cohort_arithmetic_sums_across_multiple_sessions(self):
        """The load-bearing computation: two zero-dispatch sessions carrying
        large payloads plus two dispatched sessions carrying small ones must
        produce the two cohort totals the go/no-go rule reads."""
        def _session_stats(*, spawn_dispatch_n, main_payload_chars):
            stats = instrument_authoring._new_instrument_authoring_stats()
            stats["spawn_dispatch_n"] = spawn_dispatch_n
            stats["main_payload_chars"] = main_payload_chars
            return stats

        sessions = [
            _session_stats(spawn_dispatch_n=0, main_payload_chars=500),
            _session_stats(spawn_dispatch_n=0, main_payload_chars=500),
            _session_stats(spawn_dispatch_n=1, main_payload_chars=10),
            _session_stats(spawn_dispatch_n=1, main_payload_chars=10),
        ]
        _stats, cohort_totals = instrument_authoring._aggregate_instrument_authoring_sessions(sessions)
        assert cohort_totals["zero_dispatch"]["session_n"] == 2
        assert cohort_totals["dispatched"]["session_n"] == 2
        assert cohort_totals["zero_dispatch"]["payload_chars"] == 1000
        assert cohort_totals["dispatched"]["payload_chars"] == 20
