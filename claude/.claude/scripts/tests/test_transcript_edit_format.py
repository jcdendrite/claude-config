"""Tests for transcript_analysis/edit_format.py's cmd_edit_format: the per-tool call census and
failure classification, governance re-bucketing, not_found cause attribution, token overhead, the
per-account breakdown and its ordinals, the --no-redact refusal and banner, and the per-session scan
dict.
"""
import re

import pytest
from transcript_analysis import edit_format, scope

from .conftest import (
    _bash_use,
    _hook_deny_current,
    _opus,
    _thinking_block,
    _user_msg,
    _write_cost_root,
    _write_jsonl,
)


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
        edit_format._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_call_census_count(out, "Edit") == 2
        assert _extract_call_census_count(out, "Write") == 1
        assert _extract_call_census_count(out, "MultiEdit") == 0

    def test_recognized_edit_tool_set_includes_multi_edit(self):
        """A future rename or MultiEdit's reintroduction must be counted
        against its own denominator, not silently zeroed — pinned by
        asserting the recognized-tool constant directly."""
        assert frozenset({"Edit", "Write", "MultiEdit"}) == edit_format.EDIT_FAMILY_TOOLS

    def test_multi_edit_reappearing_is_tracked_not_silently_zeroed(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_multi_edit_tool_use("m1")], out=10),
            _error_result("m1", "has not been read yet"),
        ])
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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

        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
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
        edit_format._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_unclassified_count(out) == 1

    def test_old_string_and_write_char_sums_and_percentages(self, fake_projects, capsys):
        """old_string/new_string/Write char sums and the derived overhead
        percentages, hand-computed against a small deterministic fixture."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _opus([_edit_tool_use("e1", old_string="12345", new_string="1234567890")], out=100),
            _opus([_write_tool_use("w1", content="abcdefghij")], out=50),
        ])
        edit_format._edit_format_report(_edit_format_args())
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
        monkeypatch.setattr(scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            edit_format.cmd_edit_format(_edit_format_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
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
        edit_format._edit_format_report(_edit_format_args(no_redact=True))
        out = capsys.readouterr().out
        assert scope._DO_NOT_PUBLISH_BANNER in out
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
        edit_format._edit_format_report(_edit_format_args(), roots=[root_a, root_b])
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
        edit_format._edit_format_report(_edit_format_args(), roots=[root_empty, root_b])
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
        edit_format._edit_format_report(_edit_format_args(), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert _extract_account_edit_calls(out, "account-1") == 1
        assert "account-2  no edit-family calls" in out

    def test_zero_edit_family_calls_prints_zeroes_without_division_error(self, fake_projects, capsys):
        """An empty scope (no Edit/Write/MultiEdit calls anywhere) prints a
        zero census and 0.0%/n-a shares rather than raising ZeroDivisionError."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        edit_format._edit_format_report(_edit_format_args())
        out = capsys.readouterr().out
        assert _extract_call_census_count(out, "Edit") == 0
        assert "mean old_string chars/edit: n/a" in out
        assert "old_string share of Edit payload: 0.0%" in out

    def test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly(self, tmp_path, capsys):
        """Defense-in-depth: _edit_format_report must refuse the multi-root +
        --no-redact combination itself rather than trusting that
        _resolve_cost_roots already validated it, mirroring
        test_no_redact_refused_by_cost_report_itself_even_when_called_directly
        — the in-function guard's own docstring claims this module's tests
        exercise it directly; prior to this test, none did. Refusal happens
        before any output is printed."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", [
            _opus([_edit_tool_use("e1", old_string="a", new_string="b")], out=10),
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", [
            _opus([_edit_tool_use("e2", old_string="c", new_string="d")], out=10),
        ])
        with pytest.raises(SystemExit) as exc_info:
            edit_format._edit_format_report(_edit_format_args(no_redact=True), roots=[root_a, root_b])
        assert exc_info.value.code == 2
        assert capsys.readouterr().out == ""


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
        stats = edit_format._scan_edit_format_session(records)
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
        stats = edit_format._scan_edit_format_session(records)
        assert stats["unpaired"] == 1
        assert stats["unclassified"] == 1
        assert stats["governance"]["plan-review"] == 1

    def test_notfound_cause_attribution_direct(self):
        records = [
            _opus([_edit_tool_use("e1", file_path="/foo.py", old_string="x=1", new_string="x=2")], out=10),
            _error_result("e1", "String to replace not found in file."),
            _opus([_edit_tool_use("e2", file_path="/foo.py", old_string="x = 1", new_string="x = 2")], out=10),
        ]
        stats = edit_format._scan_edit_format_session(records)
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
        stats = edit_format._scan_edit_format_session(records)
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
        stats = edit_format._scan_edit_format_session(records)
        assert stats["cause"]["identical_retry"] == 1

    def test_multi_edit_notfound_counted_as_owner_not_tracked_not_a_crash(self):
        """A not_found failure whose owner is MultiEdit (not Edit) has no
        old_string in edit_order to pair against — must be counted, not
        raise, since edit_order only ever tracks Edit's own calls."""
        records = [
            _opus([_multi_edit_tool_use("m1")], out=10),
            _error_result("m1", "String to replace not found in file."),
        ]
        stats = edit_format._scan_edit_format_session(records)
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
        stats = edit_format._scan_edit_format_session(records)
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
        assert edit_format._old_string_size_bucket(length) == expected_bucket

    def test_old_string_size_histogram_direct(self):
        records = [
            _opus([_edit_tool_use("e1", old_string="x" * 50, new_string="y")], out=10),
            _opus([_edit_tool_use("e2", old_string="x" * 200, new_string="y")], out=10),
            _opus([_edit_tool_use("e3", old_string="x" * 50, new_string="y")], out=10),
        ]
        stats = edit_format._scan_edit_format_session(records)
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
        stats = edit_format._scan_edit_format_session(records)
        assert stats["output_tokens"] == 3111
        assert stats["calls"] == {"Edit": 1}
