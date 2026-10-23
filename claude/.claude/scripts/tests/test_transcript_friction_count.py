"""Tests for transcript_analysis/friction_count.py's cmd_friction_count: each friction signal, parity with
review-trace's denial count and fail-seq's failing subtotal, and the --checkpoint incremental scan.
"""
import json
import re

from transcript_analysis import fail_seq, friction_count, review_trace

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _hook_deny,
    _hook_deny_current,
    _review_trace_args,
    _tool_result,
    _user_msg,
    _write_jsonl,
)


def _friction_count_args(
    transcript: str, *, json_output: bool = False, checkpoint: str | None = None
) -> object:
    return type("A", (), {"transcript": transcript, "json": json_output, "checkpoint": checkpoint})()


class TestFrictionCount:
    def test_legacy_denial_shape_counted(self, fake_projects, capsys):
        """A legacy attachment/hook_blocking_error record counts as one denial."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [_hook_deny("require-code-review")])
        friction_count.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_current_format_denial_shape_counted(self, fake_projects, capsys):
        """A current-format is_error tool_result with the denial signature counts as one denial."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [_hook_deny_current("Commit blocked by code-review gate.")])
        friction_count.cmd_friction_count(_friction_count_args(str(path)))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 1

    def test_distinct_denials_each_counted(self, fake_projects, capsys):
        """Two current-format denials with distinct tool_use_ids count as two denials."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _hook_deny_current("Commit blocked by code-review gate.", tool_id="toolu_a"),
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
        ])
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["failed_test_runs"] == 1

    def test_struggle_phrase_match_counted(self, fake_projects, capsys):
        """A user turn containing a STRUGGLE_PHRASES entry counts as one struggle turn."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg([{"type": "text", "text": "no not that, try again"}], branch="feat"),
        ])
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 1

    def test_task_notification_only_transcript_zero_struggle_turns(self, fake_projects, capsys):
        """A transcript containing only a forwarded <task-notification> record whose
        <summary> has a struggle phrase counts zero struggle turns."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                "<task-notification><summary>still failing, try again</summary></task-notification>",
                branch="feat",
            ),
        ])
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 0

    def test_task_notification_mixed_turn_still_counts_outside_phrase(self, fake_projects, capsys):
        """A struggle phrase outside the envelope in the same turn still counts one
        struggle turn, even though the envelope portion is excluded from matching."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                "no not that, try again. also: "
                "<task-notification><summary>still failing</summary></task-notification>",
                branch="feat",
            ),
        ])
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["struggle_turns"] == 1

    def test_unterminated_envelope_with_embedded_phrase_still_counts(self, fake_projects, capsys):
        """A struggle phrase inside an envelope missing its closing tag still counts: the
        unterminated opener is left in place rather than swallowing the rest of the turn."""
        path = fake_projects / "sess.jsonl"
        _write_jsonl(path, [
            _asst("claude-sonnet-4-6", branch="feat"),
            _user_msg(
                "<task-notification><summary>still failing, try again",
                branch="feat",
            ),
        ])
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals == {"denials": 0, "failed_test_runs": 0, "struggle_turns": 0, "composite": 0}

    def test_empty_transcript_is_zero(self, fake_projects, capsys):
        """An empty transcript file produces a composite of 0, no crash."""
        path = fake_projects / "sess.jsonl"
        path.write_text("")
        friction_count.cmd_friction_count(_friction_count_args(str(path)))
        out = capsys.readouterr().out.strip()
        assert out == "0"

    def test_malformed_line_skipped_gracefully(self, fake_projects, capsys):
        """A malformed JSONL line is silently skipped; valid lines around it still count."""
        path = fake_projects / "sess.jsonl"
        good_denial = json.dumps(_hook_deny_current("Commit blocked by code-review gate."))
        path.write_text(f"{good_denial}\nnot json at all\n{{broken\n")
        friction_count.cmd_friction_count(_friction_count_args(str(path)))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["denials"] == 7


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
        review_trace.cmd_review_trace(_review_trace_args())
        trace_out = capsys.readouterr().out
        m = re.search(r"denials=(\d+)", trace_out)
        assert m is not None, f"no denials= marker found in review-trace output: {trace_out!r}"
        review_trace_denials = int(m.group(1))
        assert review_trace_denials == 2

        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
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
        review_trace.cmd_review_trace(_review_trace_args())
        trace_out = capsys.readouterr().out
        event_lines = [ln for ln in trace_out.splitlines() if ln.startswith("  [")]
        assert len(event_lines) == 1
        assert "friction" in event_lines[0]
        assert "denial" not in event_lines[0]
        assert "denials=0" in trace_out

        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
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
        fail_seq.cmd_fail_seq(fail_seq_args)
        fail_seq_out = capsys.readouterr().out
        m = re.search(r"Total runs: (\d+)\s+Failing: (\d+)", fail_seq_out)
        assert m is not None, f"no Total runs/Failing marker found in fail-seq output: {fail_seq_out!r}"
        total_runs, failing_subtotal = int(m.group(1)), int(m.group(2))
        assert total_runs == 3
        assert failing_subtotal == 2

        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        signals = json.loads(capsys.readouterr().out)
        assert signals["failed_test_runs"] == failing_subtotal
        assert signals["failed_test_runs"] != total_runs


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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        first_composite = int(capsys.readouterr().out.strip())
        assert first_composite == 2  # 1 denial + 1 failed test run

        with path.open("a") as fh:
            for rec in (
                _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="toolu_b"),
                _user_msg([{"type": "text", "text": "hold on, try again"}], branch="feat"),
            ):
                fh.write(json.dumps(rec) + "\n")

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        incremental_composite = int(capsys.readouterr().out.strip())
        assert incremental_composite == 4  # 2 denials + 1 failed test run + 1 struggle turn
        # Non-vacuous: the incremental call actually counted the newly appended signals.
        assert incremental_composite != first_composite

        friction_count.cmd_friction_count(_friction_count_args(str(path)))  # no checkpoint: full rescan
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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
        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
        assert capsys.readouterr().out.strip() == "1"

        # No new lines appended — a second call over the unchanged file must
        # return the same cumulative composite, not double it.
        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(_friction_count_args(str(path), checkpoint=str(checkpoint)))
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

        friction_count.cmd_friction_count(
            _friction_count_args(str(path), checkpoint=str(checkpoint), json_output=True)
        )
        checkpoint_signals = json.loads(capsys.readouterr().out)

        friction_count.cmd_friction_count(_friction_count_args(str(path), json_output=True))
        non_checkpoint_signals = json.loads(capsys.readouterr().out)

        assert set(checkpoint_signals.keys()) == set(non_checkpoint_signals.keys())
        assert set(checkpoint_signals.keys()) == {
            "denials", "failed_test_runs", "struggle_turns", "composite",
        }
        assert checkpoint_signals == non_checkpoint_signals
