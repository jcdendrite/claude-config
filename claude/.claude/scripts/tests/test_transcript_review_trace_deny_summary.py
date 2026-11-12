"""Tests for review_trace.py's --deny-summary: compute_deny_summary_data and its printed tables."""

import pytest
from transcript_analysis import (
    corpus,
    denials,
    review_trace,
)

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _hook_deny,
    _hook_deny_current,
    _review_trace_args,
    _skill_use,
    _table_cols,
    _write_jsonl,
    _write_subagent_jsonl,
)


class TestReviewTrace:
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"code-review": 2, "ready-for-review": 1}
        assert dict(data["command_shape_counts"]) == {"git commit": 2, "git push": 1}

    def test_deny_summary_command_shape_empty_command_bucketed_as_other(self):
        """A denial with an enumerated hook name but no paired Bash tool_use (an
        empty command string) still lands the command-shape axis in 'other' — the
        shape-axis counterpart to test_deny_summary_unmatched_hook_name_bucketed_not_dropped,
        isolated from that test's hook-axis unmatched-ness."""
        records = [_hook_deny_current("Commit blocked by code-review gate: run /code-review.")]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git commit": 1}

    def test_deny_summary_git_dash_lowercase_c_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git -c key=value commit' (a separate-token config override, the value
        itself containing '=') buckets as 'git commit', not 'other'."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git -c user.name=eng commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {"git status": 1}

    def test_deny_summary_work_tree_separate_token_flag_value_dropped_bucketed_as_true_subcommand(self):
        """'git --work-tree <path> commit' (a separate-token flag) buckets as
        'git commit', not 'other'. The path never leaks into the returned shape counts."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_bash_use("b1", "git --work-tree ~/repo commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["command_shape_counts"]) == {denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

    def test_deny_summary_unmatched_hook_name_bucketed_not_dropped(self):
        """A denial matched via _HOOK_DENIAL_SIGNATURE's 'invocation denied' alternative,
        which names no hook, lands in the 'unmatched' bucket rather than being silently
        dropped from --deny-summary's total. Its unresolvable tool_use_id also lands in
        the command-shape grouping's 'other' bucket."""
        records = [_hook_deny_current("Skill invocation denied.")]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}
        assert dict(data["command_shape_counts"]) == {denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE: 1}

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {"marker.sh": 1}

    def test_deny_summary_covers_self_labeled_gate_colon_wording(self):
        """check-skill-length.sh states its own label as the message's own prefix
        ('Skill length gate: ...') rather than via 'blocked by' — the
        '<name> gate:' pattern extracts 'Skill length' as an enumerated label."""
        records = [
            _hook_deny_current(
                "Skill length gate: one or more SKILL.md files grew past their "
                "per-skill limit. Reduce to the limit before committing."
            ),
        ]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    def test_deny_summary_attachment_hookname_not_enumerated_falls_to_unmatched(self):
        """The legacy attachment branch's hookName field is bounded the same way as
        the regex-extracted branch: an unenumerated hookName (legacy transcripts
        predate this bound, so any historical value is unverified) is not echoed
        verbatim into the returned hook counts — it falls to 'unmatched'."""
        records = [_hook_deny("legacy-hook-slug")]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert dict(data["hook_counts"]) == {denials._DENY_SUMMARY_UNMATCHED_HOOK: 1}

    def test_deny_summary_replaces_per_session_listing(self, fake_projects, capsys):
        """--deny-summary suppresses the normal per-session event listing entirely —
        no '### <file>' block appears, only the two grouped-count tables."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate."),
        ])
        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
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
        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "No denials found in scope." in out
        assert "Denials by hook/gate" not in out

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
        data = review_trace.compute_deny_summary_data(
            [("friction_only.jsonl", records)], deny_only=True,
        )
        assert data["any_session_matched"] is True
        assert dict(data["friction_counts"]) == {"interrupted": 1}

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["corpus_min_ts"] == corpus._parse_ts("2026-07-01T10:00:01.000Z")
        assert data["corpus_max_ts"] == corpus._parse_ts("2026-07-15T09:00:01.000Z")

    def test_deny_summary_corpus_window_widened_by_consult_event_outside_denial_range(self):
        """The corpus min/max window reads every event kind, not just denial.
        An architect-consult event timestamped outside the range the
        corpus's own denial events establish must move the window -- proving
        the widening is real, not merely that the session registers."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-07-01T10:00:00.000Z",
                  content=[_bash_use("b1", "git commit -m x")]),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.",
                                tool_id="b1", ts="2026-07-01T10:00:01.000Z"),
            _asst("claude-opus-4-7", branch="main", ts="2026-07-20T09:00:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
        ]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["corpus_min_ts"] == corpus._parse_ts("2026-07-01T10:00:01.000Z")
        assert data["corpus_max_ts"] == corpus._parse_ts("2026-07-20T09:00:00.000Z")

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        # Marginals confirm the symmetric setup (both hooks 3, both shapes 3).
        assert dict(data["command_shape_counts"]) == {"git commit": 3, "git checkout": 3}
        # The cross-tab is what actually distinguishes the two hooks' shapes.
        assert data["hook_shape_counts"][("code-review", "git commit")] == 2
        assert data["hook_shape_counts"][("code-review", "git checkout")] == 1
        assert data["hook_shape_counts"][("worktree-enforcement", "git commit")] == 1
        assert data["hook_shape_counts"][("worktree-enforcement", "git checkout")] == 2

    def test_deny_summary_cause_cross_tab_shows_joint_counts_not_just_marginals(self):
        """Two hooks each produce a mix of behavioral and lib-source denials
        with symmetric marginals (code-review: 2 behavioral + 1 lib-source;
        worktree-enforcement: 1 behavioral + 2 lib-source) — the cause
        marginal alone can't say which hook hit which failure family.
        hook_cause_counts must carry the true joint counts."""
        records = [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b2"),
            _hook_deny_current("Blocked by code-review gate: could not source _lib.sh.", tool_id="b3"),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook: 'git commit' is not on the read-only allowlist.",
                tool_id="b4",
            ),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh.",
                tool_id="b5",
            ),
            _hook_deny_current(
                "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh.",
                tool_id="b6",
            ),
        ]
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["hook_cause_counts"][("code-review", "behavioral")] == 2
        assert data["hook_cause_counts"][("code-review", "lib-source")] == 1
        assert data["hook_cause_counts"][("worktree-enforcement", "behavioral")] == 1
        assert data["hook_cause_counts"][("worktree-enforcement", "lib-source")] == 2

    def test_deny_summary_cause_table_prints_header_and_correct_cross_tab_cell(
        self, fake_projects, capsys
    ):
        """--deny-summary's printed output carries the hook/gate x cause table
        header and a joint count in the right column — not just the
        marginal hook/gate and friction tables that predate this axis."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
            _hook_deny_current("Blocked by code-review gate: could not source _lib.sh.", tool_id="b2"),
        ])
        review_trace.cmd_review_trace(_review_trace_args(deny_summary=True))
        out = capsys.readouterr().out
        assert "## Denials by hook/gate x cause" in out
        cols = _table_cols(out, header_contains="behavioral", row_contains="code-review")
        assert cols["behavioral"] == "1"
        assert cols["lib-source"] == "1"

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
        data = review_trace.compute_deny_summary_data([("sess.jsonl", records)])
        assert data["command_shape_counts"].get(denials._DENY_SUMMARY_OTHER_COMMAND_SHAPE, 0) == 0
        assert data["hook_counts"].get(denials._DENY_SUMMARY_UNMATCHED_HOOK, 0) == 0


class TestComputeDenySummaryDataGroupBoundaryFreshRead:
    """_compute_deny_summary_data must derive group_boundaries from the same
    read that produces its records, not from session_iter's own records
    paired with an independent, later _read_session_file_partitioned call --
    the two reads can observe a growing transcript file differently."""

    def test_stale_session_iter_records_are_replaced_by_the_fresh_disk_read(self, fake_projects):
        """session_iter hands in a deliberately empty records list for this
        session -- simulating a read taken before the main and subagent
        files carried their denials -- while the real on-disk files already
        have one denial each. Both denials must still surface, proving
        detection runs against a fresh read of the files, not the stale,
        empty tuple session_iter provided."""
        session_id = "sess-toctou"
        jsonl = fake_projects / f"{session_id}.jsonl"
        _write_jsonl(jsonl, [
            _hook_deny_current("Commit blocked by code-review gate: run /code-review.", tool_id="b1"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _hook_deny_current("Push blocked by ready-for-review gate.", tool_id="b2"),
        ])

        data = review_trace.compute_deny_summary_data([(jsonl, [])])

        assert dict(data["hook_counts"]) == {"code-review": 1, "ready-for-review": 1}
