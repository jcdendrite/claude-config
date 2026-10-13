"""Tests for transcript_analysis/subagents.py's cmd_subagents, plus declared-roots multi-root for it and subagent-mix."""
import importlib.util
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._subagent_helpers import (
    _subagent_mix_args,
    _subagents_args,
    _sum_column_across_rows,
)
from .conftest import (
    _agent_use,
    _asst,
    _hook_deny,
    _read_use,
    _table_cols,
    _tool_result,
    _two_declared_roots,
    _user_msg,
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


def _mcp_use(tool_id: str, server: str, tool: str) -> dict:
    """Build an mcp__<server>__<tool> tool_use block, the on-disk shape for an MCP tool call."""
    return {"type": "tool_use", "id": tool_id, "name": f"mcp__{server}__{tool}", "input": {}}


def _attr_ts(minute: int) -> str:
    """Strictly increasing timestamps one minute apart, so a fixture's main-thread
    timeline (the branch index) is unambiguous."""
    return f"2026-05-12T12:{minute:02d}:00.000Z"


def _sidechain_tool_result(tool_id: str, text: str, *, branch: str, ts: str) -> dict:
    """A subagent-file user record carrying one tool_result block, tagged with the subagent's own `branch`."""
    return {**_user_msg([_tool_result(tool_id, text)], branch=branch, ts=ts), "isSidechain": True}


_SIDECHAIN_TOOL_RESULT_BYTES = 40


def _write_sidechain_tool_result_session(fake_projects: Path, own_tag: str) -> None:
    """One session whose main thread is on 'feature-x' and whose subagent reads a file
    on a branch tagged `own_tag`, so the Read result's bytes belong to 'feature-x'."""
    session_id = "sess-sidechain-result"
    _write_jsonl(fake_projects / f"{session_id}.jsonl", [
        _asst("claude-opus-4-7", branch="feature-x", ts=_attr_ts(1)),
    ])
    _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
        _asst("claude-sonnet-4-6", branch=own_tag, sidechain=True, ts=_attr_ts(5),
              content=[_read_use("t1", "/x")]),
        _sidechain_tool_result("t1", "y" * _SIDECHAIN_TOOL_RESULT_BYTES, branch=own_tag, ts=_attr_ts(6)),
    ])


def _assert_sidechain_bytes_land_under_branch(out: str, branch_label_pattern: str) -> None:
    """The sidechain Read result's bytes appear in both tables, the tool table under a Branch cell matching the pattern."""
    sidechain_cols = _table_cols(out, header_contains="Thread", row_contains="sidechain", drop_leading_labels=1)
    assert sidechain_cols["Bytes"] == str(_SIDECHAIN_TOOL_RESULT_BYTES)
    tool_cols = _table_cols(out, header_contains="Side", row_contains="Read")
    assert re.fullmatch(branch_label_pattern, tool_cols["Branch"])
    assert tool_cols["Bytes"] == str(_SIDECHAIN_TOOL_RESULT_BYTES)


def _branch_block(out: str, label_pattern: str) -> list[str]:
    """The first-table lines under the branch row whose label matches `label_pattern`:
    the labelled row plus its continuation rows, whose Branch cell is blank."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(label_pattern, ln))
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if not ln.startswith(" "):
            break
        block.append(ln)
    return block


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
        _mod.subagents.cmd_subagents(_subagents_args(branches="test-branch"))
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
        _mod.subagents.cmd_subagents(_subagents_args(branches="branch-a"))
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
        _mod.subagents.cmd_subagents(_subagents_args(branches="test-branch"))
        out = capsys.readouterr().out
        main_cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert main_cols["Opus"] == "1", "three content-block records for one API call count as one turn"

    def test_single_root_branch_output_strips_control_characters(self, fake_projects, capsys):
        """gitBranch is transcript-sourced, not git-validated -- an
        OSC-injection payload must not reach the single-root (no
        --config-dir) table row raw, the same invariant
        _root_scoped_display_label's disclose path enforces under multi-root."""
        payload = "\x1b]0;PWNED\x07\x1b[2J\x1b[H\x1b[31mFAKE-ROW\x1b[0m"
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-opus-4-7", branch=payload)])
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "]0;PWNED[2J[H[31mFAKE-ROW[0m" in out
        assert "\x1b" not in out
        assert "\x07" not in out


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
        _mod.subagents.cmd_subagents(_subagents_args())
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
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        main_cols = _table_cols(out, header_contains="Thread", row_contains="main")
        sidechain_cols = _table_cols(
            out, header_contains="Thread", row_contains="sidechain", drop_leading_labels=1
        )
        assert main_cols["Bytes"] == "0"
        assert sidechain_cols["Bytes"] == str(len(text.encode()))

    @pytest.mark.parametrize("own_tag", ["unrelated-repo-branch", "worktree-agent-abc123"])
    def test_sidechain_tool_result_with_a_foreign_own_tag_counts_under_the_dispatching_branch(
        self, fake_projects, capsys, own_tag
    ):
        """Single-root mode prints the attributed branch raw, so the subagent's own tag
        must not reach either table through the user-record arm."""
        _write_sidechain_tool_result_session(fake_projects, own_tag)
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert own_tag not in out
        _assert_sidechain_bytes_land_under_branch(out, r"feature-x")

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
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Bytes"] == "0"

    def test_empty_transcript_produces_no_data_found_without_crash(self, fake_projects, capsys):
        """A transcript file with zero records is skipped by iter_sessions
        (records list is empty) — cmd_subagents prints the existing
        no-data message rather than crashing on an empty session."""
        _write_jsonl(fake_projects / "sess.jsonl", [])
        _mod.subagents.cmd_subagents(_subagents_args())
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
            _mod.subagents.cmd_subagents(_subagents_args())
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
        _mod.subagents.cmd_subagents(_subagents_args())
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
        _mod.subagents.cmd_subagents(_subagents_args())
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
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "mcp__github" not in out
        assert "mcp__linear" not in out
        assert _mod.subagents._MCP_TOOL_BUCKET_LABEL in out
        cols = _table_cols(out, header_contains="Tool", row_contains=_mod.subagents._MCP_TOOL_BUCKET_LABEL)
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
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "unknown" in out

    def test_tool_name_output_strips_control_characters(self, fake_projects, capsys):
        """tool_use.name is transcript-sourced, not validated -- an
        OSC-injection payload must not reach the byte-by-tool table's Tool
        column raw, the same invariant cmd_subagents' branch column already
        enforces (test_single_root_branch_output_strips_control_characters)."""
        payload = "\x1b]0;PWNED-TOOL\x07\x1b[31mFAKE-TOOL-ROW\x1b[0m"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                {"type": "tool_use", "id": "t1", "name": payload, "input": {}},
            ]),
            _user_msg([_tool_result("t1", "z" * 16)], branch="main"),
        ])
        _mod.subagents.cmd_subagents(_subagents_args())
        out = capsys.readouterr().out
        assert "]0;PWNED-TOOL[31mFAKE-TOOL-ROW[0m" in out
        assert "\x1b" not in out
        assert "\x07" not in out


class TestSubagentsSince:
    """--since Nd filters both of cmd_subagents' reported tables but never
    the corpus-wide counters feeding _warn_if_subagent_format_drift."""

    def test_since_excludes_turns_older_than_window(self, fake_projects, capsys):
        old_ts = "2020-01-01T00:00:00Z"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=old_ts),
        ])
        _mod.subagents.cmd_subagents(_subagents_args(since="1d"))
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
        _mod.subagents.cmd_subagents(_subagents_args(since="1d"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Thread", row_contains="main")
        assert cols["Opus"] == "1"

    def test_since_excludes_records_missing_timestamp(self, fake_projects, capsys):
        rec = _asst("claude-opus-4-7", branch="main")  # no ts= given -> no timestamp key
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        _mod.subagents.cmd_subagents(_subagents_args(since="1d"))
        out = capsys.readouterr().out
        assert "No data found." in out

    def test_malformed_since_exits_nonzero_naming_subagents(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagents.cmd_subagents(_subagents_args(since="not-a-window"))
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
        _mod.subagents.cmd_subagents(_subagents_args(since="1d"))
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
        _mod.subagents.cmd_subagents(_subagents_args())
        single_root_out = capsys.readouterr().out
        single_root_cols = _table_cols(single_root_out, header_contains="Thread", row_contains="feat")
        assert single_root_cols["Opus"] == "1"

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _mod.subagents.cmd_subagents(_subagents_args(extra_config_dirs=[str(acct_b)]))
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
        _mod.subagents.cmd_subagents(_subagents_args(extra_config_dirs=[str(acct_b)]))
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
        _mod.subagents.cmd_subagents(_subagents_args(extra_config_dirs=[str(acct_b)]))
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
        _mod.subagents.cmd_subagents(_subagents_args())
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

        _mod.subagents.cmd_subagents(_subagents_args(extra_config_dirs=[str(extra)]))
        out = capsys.readouterr().out
        account_1 = _table_cols(out, header_contains="Thread", row_contains="account-1/branch-1")
        account_2 = _table_cols(out, header_contains="Thread", row_contains="account-2/branch-1")
        # "aaa-extra" (2 opus turns) resolved-path-sorts before "zzz-active"
        # (1 opus turn) despite being scanned second -- account-1 must be
        # the extra root's row.
        assert account_1["Opus"] == "2"
        assert account_2["Opus"] == "1"


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

        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())  # no extra_config_dirs passed
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

        _mod.subagents.cmd_subagents(_subagents_args())  # no extra_config_dirs passed
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err
        assert "account-1/branch-1" in captured.out
        assert "account-2/branch-1" in captured.out

    def test_this_repo_via_declared_roots_discloses_branch_raw(self, tmp_path, monkeypatch, capsys):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        this_repo_slug = "-repo-main"
        for idx, root in enumerate(roots):
            proj = root / this_repo_slug
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _asst("claude-opus-4-7", branch="feat-disclosed"),
            ])
        args = _subagents_args(this_repo=True)
        args._this_repo_slugs = [this_repo_slug]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert "account-1/feat-disclosed" in out
        assert "account-2/feat-disclosed" in out

    def test_subagent_mix_this_repo_via_declared_roots_discloses_branch_raw(
        self, tmp_path, monkeypatch, capsys
    ):
        """cmd_subagent_mix's own --this-repo x declared-roots-file
        coverage, mirroring test_this_repo_via_declared_roots_discloses_branch_raw
        above for cmd_subagents -- closes the asymmetry where only
        cmd_subagents' declared-roots path (as opposed to explicit
        --config-dir, already covered by TestSubagentMixMultiRoot) had this
        coverage."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        this_repo_slug = "-repo-main"
        for idx, root in enumerate(roots):
            proj = root / this_repo_slug
            proj.mkdir(parents=True)
            _write_jsonl(proj / f"sess-{idx}.jsonl", [
                _asst("claude-opus-4-7", branch="feat-disclosed", content=[_agent_use(f"a{idx}", "staff-sdet")]),
            ])
        args = _subagent_mix_args(this_repo=True)
        args._this_repo_slugs = [this_repo_slug]
        _mod.subagent_mix.cmd_subagent_mix(args)
        out = capsys.readouterr().out
        assert "account-1/feat-disclosed" in out
        assert "account-2/feat-disclosed" in out

    def test_this_repo_sidechain_record_follows_its_dispatcher_and_never_discloses_its_own_branch(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """One session's main thread is on 'alpha'; its sidechain record carries a
        different branch 'beta'. The sidechain turn prints under alpha's disclosed
        label, 'beta' never appears, and no opaque label is needed."""
        acct_b = fake_config_dir_factory("acct-b")  # forces multi_root; carries no data of its own
        session_id = "sess-attest"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="alpha", ts=_attr_ts(1)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="beta", sidechain=True, ts=_attr_ts(5)),
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert re.search(r"account-\d+/alpha\b", out)
        assert not re.search(r"account-\d+/branch-\d+", out)
        assert "beta" not in out
        sidechain_cols = _table_cols(out, header_contains="Thread", row_contains="sidechain", drop_leading_labels=1)
        assert sidechain_cols["Sonnet"] == "1"

    def test_this_repo_prefix_and_post_boundary_sidechain_records_print_under_the_branch_entered(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        acct_b = fake_config_dir_factory("acct-b")
        session_id = "sess-prefix"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(1)),
            _asst("claude-opus-4-7", branch="feature-x", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True, ts=_attr_ts(2)),  # prefix
            _asst("claude-sonnet-4-6", branch="main", sidechain=True, ts=_attr_ts(8)),  # after the boundary
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert re.search(r"account-\d+/feature-x\b", out)
        sidechain_cols = _table_cols(out, header_contains="Thread", row_contains="sidechain", drop_leading_labels=1)
        assert sidechain_cols["Sonnet"] == "2"
        assert not re.search(r"account-\d+/main\b", out)
        assert not re.search(r"account-\d+/branch-\d+", out)

    def test_this_repo_foreign_and_isolation_tags_never_print_and_need_no_opaque_label(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        acct_b = fake_config_dir_factory("acct-b")
        session_id = "sess-foreign"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feature-x", ts=_attr_ts(1)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="unrelated-repo-branch", sidechain=True, ts=_attr_ts(5)),
            _asst("claude-sonnet-4-6", branch="worktree-agent-abc123", sidechain=True, ts=_attr_ts(6)),
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert "unrelated-repo-branch" not in out
        assert "worktree-agent" not in out
        assert not re.search(r"account-\d+/branch-\d+", out)
        sidechain_cols = _table_cols(out, header_contains="Thread", row_contains="sidechain", drop_leading_labels=1)
        assert sidechain_cols["Sonnet"] == "2"

    @pytest.mark.parametrize("own_tag", ["unrelated-repo-branch", "worktree-agent-abc123"])
    def test_this_repo_sidechain_tool_result_with_a_foreign_own_tag_counts_under_the_dispatching_branch(
        self, fake_projects, fake_config_dir_factory, capsys, own_tag
    ):
        acct_b = fake_config_dir_factory("acct-b")
        _write_sidechain_tool_result_session(fake_projects, own_tag)
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert own_tag not in out
        assert not re.search(r"account-\d+/branch-\d+", out)
        _assert_sidechain_bytes_land_under_branch(out, r"account-\d+/feature-x")

    def test_this_repo_branch_carried_only_by_a_non_assistant_non_user_main_thread_record_stays_opaque(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """Deny path: the attestation set takes main-thread assistant and user records
        only. A branch a hook-denial attachment alone carried is not disclosed even though
        the timeline resolves later sidechain records to it, and a sidechain record
        (assistant or user) tagged with that same name does not attest it either."""
        acct_b = fake_config_dir_factory("acct-b")
        session_id = "sess-deny"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="alpha", ts=_attr_ts(1)),
            _hook_deny("some-hook", branch="unattested-branch", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="unattested-branch", sidechain=True, ts=_attr_ts(8)),
            _sidechain_tool_result("t1", "x" * 10, branch="unattested-branch", ts=_attr_ts(9)),
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert re.search(r"account-\d+/branch-\d+", out)
        assert "unattested-branch" not in out

    def test_this_repo_branch_carried_only_by_a_main_thread_user_record_prints_raw(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """Allow path for the user arm: a main-thread user record with a tool_result is
        the sole attester of the branch the sidechain turn resolves to."""
        acct_b = fake_config_dir_factory("acct-b")
        session_id = "sess-user-attests"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="alpha", ts=_attr_ts(1)),
            _user_msg([_tool_result("t1", "x" * 10)], branch="user-only-branch", ts=_attr_ts(5)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="alpha", sidechain=True, ts=_attr_ts(8)),
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert re.search(r"account-\d+/user-only-branch\b", out)
        assert not re.search(r"account-\d+/branch-\d+", out)

    def test_this_repo_session_without_a_branch_bearing_main_thread_record_contributes_no_row(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        acct_b = fake_config_dir_factory("acct-b")
        session_id = "sess-no-branch"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="", ts=_attr_ts(1)),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="orphan-branch", sidechain=True, ts=_attr_ts(5)),
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert "No data found." in out
        assert "orphan-branch" not in out

    def test_this_repo_attestation_is_corpus_wide_not_since_window_scoped(
        self, fake_projects, fake_config_dir_factory, capsys, monkeypatch
    ):
        """main_thread_branches attestation runs before the --since filter
        and is never narrowed by it -- a branch attested by a main-thread
        record outside the --since window still discloses raw for an
        in-window sidechain record on that same branch. Pins this as
        intentional: the attestation question is whether a real
        main-thread session ever used this branch, not whether the
        attesting record itself appears in the displayed table."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        old_ts = datetime.fromtimestamp(fixed_now - 10 * 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        recent_ts = datetime.fromtimestamp(fixed_now, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        acct_b = fake_config_dir_factory("acct-b")  # forces multi_root; carries no data of its own
        session_id = "sess-attest-window"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="gamma", ts=old_ts),  # main-thread, outside --since 1d
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="gamma", sidechain=True, ts=recent_ts),  # in-window
        ])
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)], since="1d")
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert re.search(r"account-\d+/gamma\b", out)

    def test_this_repo_cross_account_attestation_independence(self, tmp_path, monkeypatch, capsys):
        """main_thread_branches keyed on (root_idx, branch), not a flat
        set[str] -- account A attests 'main' through a main-thread assistant
        record, account B carries 'main' only on a hook-denial record the gate
        ignores, so B's sidechain turn (which resolves to 'main') must print
        under an opaque label rather than ride A's disclosure."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        this_repo_slug = "-repo-main"
        proj_a = roots[0] / this_repo_slug
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [
            _asst("claude-opus-4-7", branch="main", ts=_attr_ts(1)),
        ])
        proj_b = roots[1] / this_repo_slug
        proj_b.mkdir(parents=True)
        session_id_b = "sess-b"
        _write_jsonl(proj_b / f"{session_id_b}.jsonl", [
            _asst("claude-opus-4-7", branch="other", ts=_attr_ts(1)),
            _hook_deny("some-hook", branch="main", ts=_attr_ts(3)),
        ])
        _write_subagent_jsonl(proj_b, session_id_b, "agent-1", [
            _asst("claude-sonnet-4-6", branch="other", sidechain=True, ts=_attr_ts(5)),
        ])
        args = _subagents_args(this_repo=True)
        args._this_repo_slugs = [this_repo_slug]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert len(re.findall(r"account-\d+/main\b", out)) == 1, "only account A's row may print 'main' raw"
        assert not any("sidechain" in ln for ln in _branch_block(out, r"account-\d+/main\b"))
        assert any("sidechain" in ln for ln in _branch_block(out, r"account-\d+/branch-\d+"))

    def test_this_repo_still_stamps_do_not_publish_banner_under_multi_root(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        acct_b = fake_config_dir_factory("acct-b")
        args = _subagents_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_this_repo_single_root_prints_raw_branch_with_no_account_prefix(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-opus-4-7", branch="feat")])
        args = _subagents_args(this_repo=True)
        args._this_repo_slugs = ["-home-user-testrepo"]
        _mod.subagents.cmd_subagents(args)
        out = capsys.readouterr().out
        assert "feat" in out
        assert "account-" not in out
