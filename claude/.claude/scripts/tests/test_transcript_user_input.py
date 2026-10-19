"""Tests for transcript_analysis/user_input.py's cmd_user_input: prompt classification, filters, --redact,
truncation, model attribution, session order, and --out.
"""
import pytest
from transcript_analysis import user_input

from ._session_signal_helpers import _ui_user, _user_input_args
from .conftest import _asst, _write_jsonl


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
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "**[? · INITIAL · sonnet]**\n~~~text\nplain initial prompt\n~~~" in out
        assert "**[? · FOLLOWUP · sonnet]**\n~~~text\nthe stale cache needs clearing\n~~~" in out
        assert (
            '**[? · EXPLICIT_CORRECTION · sonnet]** (matched: "hallucinat")\n'
            "~~~text\nI think you hallucinated about this\n~~~"
        ) in out

    def test_task_notification_not_explicit_correction_but_text_displayed_verbatim(self, fake_projects, capsys):
        """A forwarded <task-notification> whose <summary> contains a struggle phrase is
        not classified EXPLICIT_CORRECTION, since it's subagent prose rather than human
        input. The ~~~text block still renders the full unstripped envelope. Display and
        scoring are separate copies of the same string."""
        envelope = (
            "<task-notification><status>completed</status>"
            "<summary>Background command still failing, incorrect output</summary>"
            "</task-notification>"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("plain initial prompt", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
            _ui_user(envelope, branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "EXPLICIT_CORRECTION" not in out
        assert f"~~~text\n{envelope}\n~~~" in out

    def test_task_notification_followup_leaves_fresh_prompt_and_followup_counts_unchanged(self, fake_projects, capsys):
        """A task-notification record still counts as one fresh prompt and one followup,
        same as any other non-matching FOLLOWUP. The phrase-matching exclusion affects
        only EXPLICIT_CORRECTION classification, not the fresh-prompt/followup tally."""
        envelope = (
            "<task-notification><status>completed</status>"
            "<summary>Background command still failing, incorrect output</summary>"
            "</task-notification>"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("plain initial prompt", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
            _ui_user(envelope, branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "- Fresh prompts: 2" in out
        assert "- Followups (quiet redirects): 1" in out

    def test_task_notification_mixed_turn_still_explicit_correction_on_outside_phrase(self, fake_projects, capsys):
        """A struggle phrase outside the envelope in the same turn still classifies
        EXPLICIT_CORRECTION, even though the envelope portion of that turn is excluded."""
        text = (
            "that's incorrect. also: <task-notification>"
            "<summary>still failing</summary></task-notification>"
        )
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("plain initial prompt", branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
            _ui_user(text, branch="feat"),
            _asst("claude-sonnet-4-6", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert '**[? · EXPLICIT_CORRECTION · sonnet]** (matched: "incorrect")' in out

    def test_corrections_only_excludes_initial(self, fake_projects, capsys):
        """--corrections-only drops INITIAL prompts but keeps FOLLOWUP/EXPLICIT_CORRECTION."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("plain initial prompt", branch="feat"),
            _ui_user("the stale cache needs clearing", branch="feat"),
            _ui_user("I think you hallucinated about this", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args(corrections_only=True))
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
        user_input.cmd_user_input(_user_input_args(since="2026-05-01", until="2026-05-31"))
        out = capsys.readouterr().out
        assert "old prompt outside window" not in out
        assert "new prompt inside window" in out

    def test_redact_remaps_project_label_not_prompt_text(self, fake_projects, capsys):
        """--redact remaps the project label to private-project-N but leaves prompt text verbatim
        — the flag anonymizes labels and session IDs only, never message content."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("keep this project name private", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args(redact=True))
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
        user_input.cmd_user_input(_user_input_args(redact=True))
        out_redacted = capsys.readouterr().out
        assert "Session `session-1`" in out_redacted
        assert "real-session" not in out_redacted

        user_input.cmd_user_input(_user_input_args(redact=False))
        out_unredacted = capsys.readouterr().out
        assert "Session `real-ses`" in out_unredacted

    def test_truncate_chars_including_zero_disables_truncation(self, fake_projects, capsys):
        """--truncate-chars truncates long prompt text with an annotation; 0 disables truncation entirely."""
        long_text = "x" * 600
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user(long_text, branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args(truncate_chars=100))
        out_truncated = capsys.readouterr().out
        assert "x" * 100 in out_truncated
        assert "(truncated, 600 chars total)" in out_truncated
        assert long_text not in out_truncated

        user_input.cmd_user_input(_user_input_args(truncate_chars=0))
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
        user_input.cmd_user_input(_user_input_args())
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
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "- Fresh prompts: 1" in out
        assert "excluded record" not in out

    def test_empty_string_plain_content_included_no_strip_guard(self):
        """Pin actual behavior: the plain-string content path has no .strip() guard,
        so an empty string is NOT excluded — unlike the list-of-blocks path, which
        requires non-empty stripped text via a promptId-gated _content_text().strip() check."""
        rec = _ui_user("")
        assert user_input._is_fresh_user_prompt_for_narrative(rec) is True

    def test_attribute_model_unknown_when_no_matching_assistant(self):
        """No assistant record follows the prompt -> model_fam is 'unknown'."""
        records = [_ui_user("solo prompt", session_id="s1")]
        assert user_input._attribute_model_to_prompt(records, 0, "s1") == "unknown"

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
        assert user_input._attribute_model_to_prompt(records, 0, "s1") == "sonnet"

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
        assert user_input._attribute_model_to_prompt(records, 0, "s1") == "opus"
        assert user_input._attribute_model_to_prompt(records, 1, "s1") == "opus"

    def test_classify_prompt_direct(self):
        """Direct unit coverage of the pure classification function, independent of
        cmd_user_input's I/O and markdown rendering."""
        assert user_input._classify_prompt("anything at all", True) == ("INITIAL", "")
        assert user_input._classify_prompt("this is a normal follow-up", False) == ("FOLLOWUP", "")
        classification, matched_phrase = user_input._classify_prompt(
            "I think you hallucinated that function", False
        )
        assert classification == "EXPLICIT_CORRECTION"
        assert matched_phrase == "hallucinat"

    def test_truncate_prompt_text_direct(self):
        """Direct unit coverage of the pure truncation function: under-limit and
        exact-limit text pass through unchanged, over-limit text is truncated with
        an annotation, and limit=0 disables truncation regardless of length."""
        assert user_input._truncate_prompt_text("short", 100) == "short"
        exact = "x" * 50
        assert user_input._truncate_prompt_text(exact, 50) == exact
        over = "x" * 60
        truncated = user_input._truncate_prompt_text(over, 50)
        assert truncated == "x" * 50 + "… (truncated, 60 chars total)"
        assert user_input._truncate_prompt_text("x" * 10_000, 0) == "x" * 10_000

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
        user_input.cmd_user_input(_user_input_args())
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
        user_input.cmd_user_input(_user_input_args())
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
        user_input.cmd_user_input(_user_input_args(branches="feat"))
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
        user_input.cmd_user_input(_user_input_args(redact=True))
        out = capsys.readouterr().out
        assert "### claude-config ·" in out
        assert "### private-project-1 ·" in out

    def test_scope_project_count_collapses_worktree_dir_into_main_repo(self, fake_projects, capsys):
        """A repo's main checkout and its own linked worktree are one repo, not two --
        the Scope line's project count must collapse the worktree-suffixed slug back
        to the main slug before counting. Same as cmd_buckets's Proj column."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prompt in main checkout", branch="feat"),
        ])
        proj_worktree = fake_projects.parent / f"{fake_projects.name}--claude-worktrees-feat"
        proj_worktree.mkdir(parents=True)
        _write_jsonl(proj_worktree / "sess.jsonl", [
            _ui_user("prompt in linked worktree", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "Scope: 1 projects, 2 sessions, 2 fresh prompts" in out

    def test_scope_project_count_counts_same_repo_name_under_two_home_dirs_as_two_projects(self, fake_projects, capsys):
        """Regression guard: routing the raw slug through _derive_proj_label first
        would discard the home/user prefix. That would falsely merge these two
        distinct repos into one family and undercount the Scope line's project
        count. Same invariant as TestBuckets's Proj-column sibling guard for
        cmd_buckets — see that test for the mirrored intent, not identical setup
        mechanics."""
        proj_alice = fake_projects.parent / "-home-alice-repo"
        proj_bob = fake_projects.parent / "-home-bob-repo"
        proj_alice.mkdir(parents=True)
        proj_bob.mkdir(parents=True)
        _write_jsonl(proj_alice / "sess.jsonl", [
            _ui_user("prompt from alice's repo", branch="feat"),
        ])
        _write_jsonl(proj_bob / "sess.jsonl", [
            _ui_user("prompt from bob's repo", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "Scope: 2 projects, 2 sessions, 2 fresh prompts" in out

    def test_scope_project_count_collapses_worktree_while_counting_same_basename_sibling_separately(
        self, fake_projects, capsys
    ):
        """The collapse fix and the over-collapse guard rail both hold in the same
        counting pass: a worktree checkout collapses into its own main repo, while
        a same-repo-name checkout under a different home directory stays a
        distinct family. Same invariant as TestBuckets's combined sibling test."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prompt in main checkout", branch="feat"),
        ])
        proj_worktree = fake_projects.parent / f"{fake_projects.name}--claude-worktrees-feat"
        proj_worktree.mkdir(parents=True)
        _write_jsonl(proj_worktree / "sess.jsonl", [
            _ui_user("prompt in linked worktree", branch="feat"),
        ])
        proj_sibling = fake_projects.parent / "-home-otheruser-testrepo"
        proj_sibling.mkdir(parents=True)
        _write_jsonl(proj_sibling / "sess.jsonl", [
            _ui_user("prompt from a same-named repo under a different home dir", branch="feat"),
        ])
        user_input.cmd_user_input(_user_input_args())
        out = capsys.readouterr().out
        assert "Scope: 2 projects, 3 sessions, 3 fresh prompts" in out

    def test_out_write_failure_exits_1(self, fake_projects, capsys, tmp_path):
        """A write failure to --out's target (parent directory missing) exits 1
        with the user-input-specific stderr message; nothing is printed to stdout."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _ui_user("prompt", branch="feat"),
        ])
        bad_out_path = tmp_path / "missing-dir" / "output.txt"
        with pytest.raises(SystemExit) as exc_info:
            user_input.cmd_user_input(_user_input_args(out=str(bad_out_path)))
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
        user_input.cmd_user_input(_user_input_args(out=str(out_path)))
        captured = capsys.readouterr()
        assert f"Wrote output to {out_path}" in captured.out
        file_content = out_path.read_text(encoding="utf-8")
        assert "prompt written to file" in file_content
        assert "# User Input — Conversation Narrative" in file_content
