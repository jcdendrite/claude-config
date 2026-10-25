"""Tests for transcript_analysis/judgment_pair.py's cmd_judgment_pair: review-output and user-response window
extraction, filters, truncation, and --out."""
from transcript_analysis import judgment_pair

from ._skill_helpers import _judgment_pair_args, _review_asst, _user_reply
from .conftest import (
    _TASK_NOTIFICATION_TEXT,
    _agent_use,
    _asst,
    _bash_use,
    _skill_use,
    _skill_use_rec,
    _task_notification_msg,
    _tool_result,
    _user_msg,
    _write_jsonl,
)


class TestJudgmentPair:
    def test_single_turn_review_emits_pair(self, fake_projects, capsys):
        """Invocation record followed by one review text turn and a user reply emits one pair."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Found 3 issues with the auth middleware.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Thanks, I'll fix those auth issues."),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(skills="plan-review"))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_review_orchestrator_spawn_opens_no_pair(self, fake_projects, capsys):
        """cmd_judgment_pair matches `Skill` blocks only, so a main-thread
        review-orchestrator dispatch is not a round-open here, unlike for
        review-round-cost."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:00:00.000Z",
                  content=[_agent_use("o1", "review-orchestrator")]),
            _review_asst("Orchestrator summary text.", "2026-05-20T10:01:00.000Z"),
            _user_reply("Thanks."),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "I'll add the null check." in out
        assert "tool output text" not in out

    def test_task_notification_neither_closes_the_window_nor_is_taken_as_response(self, fake_projects, capsys):
        """GH-1312: a task notification between two review turns does not end the window, so the
        later review text is the output and the next human reply is the response."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("First-wave findings.", "2026-05-20T10:01:00.000Z"),
            _task_notification_msg(),
            _review_asst("Second-wave synthesis of both reviewers.", "2026-05-20T10:03:00.000Z"),
            _user_reply("I'll fix the second-wave items."),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert (
            "Second-wave synthesis of both reviewers.\n\n"
            "--- USER RESPONSE ---\nI'll fix the second-wave items.\n---"
        ) in out
        assert "First-wave findings." not in out
        assert "<task-notification>" not in out

    def test_assistant_turn_without_text_after_a_task_notification_keeps_the_prior_review_output(
        self, fake_projects, capsys,
    ):
        """GH-1312: a tool-only assistant turn after the notification is no review text, so the
        review output stays the last turn that had text."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Missing null check in parser.", "2026-05-20T10:01:00.000Z"),
            _task_notification_msg(),
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-20T10:03:00.000Z",
                  content=[_bash_use("b1", "ls")]),
            _user_reply("I'll add the null check."),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert (
            "Missing null check in parser.\n\n"
            "--- USER RESPONSE ---\nI'll add the null check.\n---"
        ) in out
        assert "<task-notification>" not in out

    def test_task_notification_at_end_of_session_emits_end_of_session_marker(self, fake_projects, capsys):
        """GH-1312: with a notification as the last record there is still no human response."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Looks good overall.", "2026-05-20T10:01:00.000Z"),
            _task_notification_msg(),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "(no user response — end of session)" in out
        assert "<task-notification>" not in out

    def test_prompt_with_envelope_beside_human_text_is_printed_as_the_user_response(self, fake_projects, capsys):
        """GH-1312: a prompt with a task-notification envelope beside human text is still a fresh prompt, so it is
        printed as recorded (envelope included) as the USER RESPONSE; a predicate keyed on envelope presence would skip it."""
        recorded_prompt = f"{_TASK_NOTIFICATION_TEXT}\n\nthanks, merge it"
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Looks good overall.", "2026-05-20T10:01:00.000Z"),
            _user_reply(recorded_prompt),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert f"--- USER RESPONSE ---\n{recorded_prompt}\n---" in out
        assert "(no user response — end of session)" not in out

    def test_ismeta_user_not_taken_as_response(self, fake_projects, capsys):
        """A user record with isMeta=True is skipped; the next real user turn is used."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-05-20T10:00:00.000Z"),
            _review_asst("Security issue in token validation.", "2026-05-20T10:01:00.000Z"),
            {"type": "user", "isMeta": True,
             "message": {"content": "meta injection"}, "gitBranch": "main"},
            _user_reply("I'll harden the token validation."),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(since="2026-05-01"))
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_until_filter_excludes_future_invocation(self, fake_projects, capsys):
        """Invocation with timestamp after --until is excluded."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _skill_use_rec("code-review", "2026-06-20T10:00:00.000Z"),
            _review_asst("Future review output.", "2026-06-20T10:01:00.000Z"),
            _user_reply("Response to future review."),
        ])
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(until="2026-06-01"))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(truncate_chars=10))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(branches="feat"))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(branches="feat"))
        out_feat = capsys.readouterr().out
        assert "Review while still on feat." in out_feat

        judgment_pair.cmd_judgment_pair(_judgment_pair_args(branches="main"))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        assert "No judgment pairs found." in out

    def test_out_path_with_no_pairs_prints_to_stdout(self, fake_projects, capsys, tmp_path):
        """--out set but no pairs found: sentinel goes to stdout, no file is created."""
        out_file = tmp_path / "output.txt"
        judgment_pair.cmd_judgment_pair(_judgment_pair_args(out=str(out_file)))
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
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
        judgment_pair.cmd_judgment_pair(_judgment_pair_args())
        out = capsys.readouterr().out
        # First invocation window is empty (no assistant turns before the next invocation).
        assert "(no review text found)" in out
        # Second invocation window contains the review text.
        assert "Plan is missing rollback steps." in out
