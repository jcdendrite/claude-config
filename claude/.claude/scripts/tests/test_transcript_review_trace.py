"""Tests for review_trace.py's timeline: sidechain merge, --since/--until, --deny-only, branch attribution, and CLI wiring."""
import argparse

from transcript_analysis import (
    cli,
    review_trace,
    scope,
)

from .conftest import (
    _agent_use,
    _asst,
    _hook_deny,
    _hook_deny_current,
    _review_trace_args,
    _skill_use,
    _write_jsonl,
    _write_subagent_dispatch,
    _write_subagent_jsonl,
)


def _since_until_epochs(since: str | None, until: str | None) -> tuple[float | None, float | None]:
    """Mirror cmd_review_trace's own --since/--until date-string -> epoch-second
    boundary conversion, so a test calling _review_trace_session_events directly
    passes boundaries in the same form the CLI itself would compute."""
    return scope._parse_absolute_window_args(argparse.Namespace(since=since, until=until), "review-trace")


# ---------------------------------------------------------------------------
# review-trace
# ---------------------------------------------------------------------------


class TestReviewTrace:
    # -----------------------------------------------------------------------
    # GH-896: merge/sort of main and subagent records
    # -----------------------------------------------------------------------

    def test_review_trace_end_to_end_detects_consult_dispatched_from_subagent_file(
        self, fake_projects, capsys
    ):
        """End to end through cmd_review_trace's own scope resolution (not
        just the direct _review_trace_session_events call the other tests in
        this class use): a plan-architect consult dispatched from inside a
        subagent's own <session_id>/subagents/*.jsonl file is detected now
        that include_subagents=True is the default, and its timeline row
        prints thread=sidechain with its line number suppressed as n/a,
        since a sidechain event's line_no indexes no real file."""
        session_id = "sess-subagent-consult"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a2", "plan-architect", prompt="MODE=consult\nquestion")]),
        ])
        review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "architect-consults=1" in out
        assert "thread=sidechain" in out
        assert "line   n/a" in out

    def test_review_trace_sees_the_code_review_skill_call_inside_a_merged_orchestrator_transcript(
        self, fake_projects, capsys
    ):
        """A main-thread review-orchestrator dispatch carries no Skill block, so
        review-trace gets the code-review skill event only by merging the
        orchestrator's own subagent transcript, where the call sits on a
        sidechain record."""
        session_id = "sess-orchestrator-round"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_agent_use("o1", "review-orchestrator")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-o", "o1",
            [_asst("claude-sonnet-4-6", branch="feat", sidechain=True, ts="2026-05-19T10:01:00.000Z",
                   content=[_skill_use("s1", "code-review")])],
            agent_type="review-orchestrator",
        )

        review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out

        assert "skills=1" in out
        assert "skill        code-review  (branch=feat model=sonnet thread=sidechain)" in out

    def test_merged_stream_interleaves_main_and_sidechain_chronologically(self):
        """Main and subagent records are merged into one chronological
        stream before detection, not left in main-then-subagent
        concatenation order: a subagent record timestamped between two
        main-thread records lands between their events in the emitted
        timeline."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:10:00.000Z",
                  content=[_skill_use("s2", "plan-review")]),
            _asst("claude-opus-4-7", branch="feat", sidechain=True, ts="2026-05-19T10:05:00.000Z",
                  content=[_skill_use("s3", "ready-for-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["skill"] for e in events] == ["code-review", "ready-for-review", "plan-review"]
        assert [e["thread"] for e in events] == ["main", "sidechain", "main"]

    def test_line_no_still_resolves_to_main_file_line_after_sort(self):
        """A thread=main event's line_no still equals its position in the
        main transcript after the merge sort reorders it relative to a
        later-positioned but earlier-timestamped subagent record."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:10:00.000Z",
                  content=[_skill_use("s1", "code-review")]),  # main file line 1, later ts
            _asst("claude-opus-4-7", branch="feat", sidechain=True, ts="2026-05-19T09:00:00.000Z",
                  content=[_skill_use("s2", "plan-review")]),  # subagent record, earlier ts
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert events[0]["thread"] == "sidechain"
        assert events[1]["thread"] == "main"
        assert events[1]["line_no"] == 1

    def test_interior_unparseable_timestamp_forward_fills_and_stays_adjacent(self):
        """An interior record whose timestamp fails to parse forward-fills
        the immediately preceding record's effective_ts, so it sorts
        adjacent to that neighbour rather than raising TypeError or
        drifting elsewhere in the stream."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="not-a-timestamp",
                  content=[_skill_use("s2", "plan-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:10:00.000Z",
                  content=[_skill_use("s3", "ready-for-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["skill"] for e in events] == ["code-review", "plan-review", "ready-for-review"]

    def test_leading_unparseable_timestamp_sorts_to_head_not_typeerror(self):
        """A leading record whose timestamp fails to parse has no preceding
        record to forward-fill from -- it falls back to float("-inf") and
        sorts to the head of the stream instead of raising TypeError
        against its neighbour's float key, a distinct case from an interior
        unparseable timestamp above."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat", ts="not-a-timestamp",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s2", "plan-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["skill"] for e in events] == ["code-review", "plan-review"]

    def test_same_timestamp_main_sidechain_tie_break_main_first(self):
        """Two records sharing one timestamp, one main and one sidechain,
        place the main event first -- the tie-break is a stated choice
        (cause before effect), not inherited from the records' own list
        position, which here has the sidechain record listed ahead of the
        main one."""
        records = [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T10:00:00.000Z", content=[_skill_use("s1", "code-review")]),
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T10:00:00.000Z", content=[_skill_use("s2", "plan-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["thread"] for e in events] == ["main", "sidechain"]
        assert [e["skill"] for e in events] == ["plan-review", "code-review"]

    def test_sidechain_event_inherits_main_threads_branch_not_its_own(self):
        """A sidechain event's branch is the carried-forward value from the
        preceding main-thread record, never the sidechain record's own
        gitBranch -- the isSidechain guard on the carry-forward trackers
        (docs/design-decisions.md §58) is load-bearing specifically because
        lifting it would attribute a sidechain event to its own record's
        branch instead of the dispatching main thread's. Every other
        sidechain fixture in this file sets the sidechain record's own
        branch equal to the preceding main record's, so only a fixture with
        a genuinely differing sidechain gitBranch can catch a regression
        that reads it directly."""
        records = [
            _asst("claude-sonnet-4-6", branch="A", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-4-7", branch="B", sidechain=True, ts="2026-05-19T10:05:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 1
        assert events[0]["branch"] == "A"

    def test_denial_dedup_by_tool_use_id_survives_sort_reorder(self):
        """A denial recorded as both a sidechain attachment record and its
        earlier-timestamped main-thread current-format twin still collapses
        to one event once the merge sort reorders them ahead of their
        original list position -- dedup runs against the sorted stream, not
        the pre-sort one."""
        attach = _hook_deny("worktree", ts="2026-05-19T10:05:00.000Z")  # toolUseID == toolu_worktree
        attach["isSidechain"] = True
        twin = _hook_deny_current(
            "Blocked by worktree-enforcement hook: 'git add' not allowed.",
            tool_id="toolu_worktree", ts="2026-05-19T10:00:00.000Z",
        )
        # attach (sidechain, later ts) is listed first; twin (main, earlier
        # ts) is listed second -- pre-sort order is the reverse of
        # chronological order, so this exercises the merge sort as well as
        # dedup.
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            [attach, twin], None, None, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "denial"
        # hook_name=="" (rather than "worktree") proves the sort actually ran
        # twin (main, earlier ts) ahead of attach before dedup, not merely
        # that dedup collapsed whichever one happened to be processed first.
        assert events[0]["hook_name"] == ""
        assert events[0]["thread"] == "main"

    def test_forward_fill_does_not_cross_subagent_file_boundary(self, fake_projects, capsys):
        """An unparseable-timestamp record that is the first record of its
        own subagent file resets to the earliest sort position instead of
        forward-filling from a different subagent file's last record --
        filename-sorted subagent files carry no chronological relationship
        to each other, so a global (rather than per-source-group)
        forward-fill would wrongly inherit agent-1's late timestamp into
        agent-2's own first record. Exercised end to end through
        cmd_review_trace, since the per-file group boundary this needs only
        exists once records are read via real subagent files, not via a
        hand-built flat records list."""
        session_id = "sess-cross-file-forward-fill"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T15:00:00.000Z",
                  content=[_skill_use("s-main", "skill-review")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T20:00:00.000Z",
                  content=[_skill_use("s-a1", "code-review")]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", [
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="not-a-timestamp",
                  content=[_skill_use("s-a2-bad", "plan-review")]),
            _asst("claude-opus-4-7", branch="feat", sidechain=True,
                  ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s-a2-good", "ready-for-review")]),
        ])
        review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        # agent-2's own unparseable-ts record (plan-review) must sort ahead
        # of agent-2's own later, real-ts record (ready-for-review), and
        # both ahead of main (skill-review, 15:00) and agent-1 (code-review,
        # 20:00, the last record of the *other*, filename-earlier-sorted
        # subagent file) -- not fall back to agent-1's 20:00 timestamp,
        # which would instead sort plan-review last.
        assert (
            out.index("plan-review") < out.index("ready-for-review")
            < out.index("skill-review") < out.index("code-review")
        )

    def test_since_boundary_inclusive_record_included(self):
        """A record whose timestamp matches exactly --since is included."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-19T00:00:00Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs("2026-05-19", None)
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        review_trace.cmd_review_trace(_review_trace_args(since="2026-05-01"))
        # No crash; output may be empty (no matching events survive the date filter).
        capsys.readouterr()  # consume; success if no exception raised above

    def test_until_subsecond_record_included(self):
        """A record at T23:59:59.500Z on the --until date IS included (sub-second gap fix)."""
        records = [
            _asst("claude-sonnet-4-6", branch="feat",
                  ts="2026-05-10T23:59:59.500Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        since_ts, until_epoch = _since_until_epochs(None, "2026-05-10")
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["kind"] == "skill"

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
        review_trace.cmd_review_trace(_review_trace_args(deny_only=True))
        out = capsys.readouterr().out
        assert "denial-session.jsonl" in out
        assert "skill-only-session.jsonl" not in out

    def test_default_timeline_denial_line_carries_cause_field(self, fake_projects, capsys):
        """The plain (non-deny-summary) timeline's denial line prints
        cause=<kind> classified from the denial's own message, alongside
        the existing hook= field."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _hook_deny_current("Blocked by code-review gate: could not source _lib.sh."),
        ])
        review_trace.cmd_review_trace(_review_trace_args())
        out = capsys.readouterr().out
        assert "cause=lib-source" in out

    # -----------------------------------------------------------------------
    # GH-482: per-record branch/model attribution
    # -----------------------------------------------------------------------

    def test_events_attributed_to_own_branch_not_session_first_branch(self):
        """A session opening on one branch, then moving to another before any review
        event fires, must attribute every event to its own (later) branch — and
        branch_filter must select by that per-event value, not the session's
        first record's branch."""
        records = [
            _asst("claude-sonnet-4-6", branch="main", ts="2026-05-19T09:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feature-x", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:05:00.000Z",
                  content=[_agent_use("a1", "staff-backend-engineer")]),
        ]

        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert len(events) == 2
        for evt in events:
            assert evt["branch"] == "feature-x", f"event must attribute to feature-x, not main: {evt!r}"

        events_feature_x, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, {"feature-x"},
        )
        assert {e["kind"] for e in events_feature_x} == {"skill", "reviewer-spawn"}

        events_main_only, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        denial_event = next(e for e in events if e["kind"] == "denial")
        assert denial_event["model"] == "opus"

    def test_unresolvable_branch_renders_sentinel(self):
        records = [
            _asst("claude-sonnet-4-6", branch="", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 1
        assert events[0]["branch"] == "old-branch"

    def test_deny_only_with_branches_filters_before_gating(self):
        """The sole denial sits on a branch the filter excludes: branch filtering
        drops it before deny_only's has_denial check ever sees it (filter-then-deny),
        not a session that still qualifies because it had a denial before filtering."""
        records = [_hook_deny("require-code-review", branch="wrong-branch")]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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

        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
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
        events_branch_b_only, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, {"branch-b"},
        )
        assert events_branch_b_only == []


class TestReviewTracePreBranchAttribution:
    """A session that opens on the default branch and then enters a feature branch:
    events carried from the leading default-branch records print the branch entered,
    while an event with no preceding main-thread record still renders '?'."""

    @staticmethod
    def _prefix_session_records() -> list[dict]:
        return [
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z",
                  content=[_skill_use("s1", "plan-it")]),
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:01:00.000Z",
                  content=[_agent_use("a1", "plan-architect", prompt="MODE=consult\nquestion")]),
            _asst("claude-sonnet-4-6", branch="main", sidechain=True, ts="2026-05-19T10:02:00.000Z",
                  content=[_skill_use("s2", "code-review")]),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:05:00.000Z"),
        ]

    def test_prefix_skill_consult_and_sidechain_events_print_the_branch_entered(self):
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            self._prefix_session_records(), None, None, None,
        )
        assert {e["kind"] for e in events} == {"skill", "architect-consult"}
        assert len(events) == 3
        assert {e["branch"] for e in events} == {"feature-x"}
        assert any(e["thread"] == "sidechain" for e in events)

    def test_event_with_no_preceding_main_thread_record_still_renders_the_sentinel(self):
        records = [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True, ts="2026-05-19T09:00:00.000Z",
                  content=[_skill_use("s1", "code-review")]),
            _asst("claude-opus-4-7", branch="main", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-opus-4-7", branch="feature-x", ts="2026-05-19T10:05:00.000Z"),
        ]
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, None, None, None,
        )
        assert [e["branch"] for e in events] == ["?"]

    def test_until_before_the_boundary_still_prints_the_branch_entered(self):
        records = self._prefix_session_records()
        records[-1]["timestamp"] = "2026-05-20T10:05:00.000Z"  # the branch entry falls outside the window
        since_ts, until_epoch = _since_until_epochs(None, "2026-05-19")
        events, _tool_use_commands, _pre_regime = review_trace._review_trace_session_events(
            records, since_ts, until_epoch, None,
        )
        assert len(events) == 3
        assert {e["branch"] for e in events} == {"feature-x"}

    def test_header_and_branches_filter_follow_the_attributed_branch(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", self._prefix_session_records())

        review_trace.cmd_review_trace(_review_trace_args(branches="feature-x"))
        kept = capsys.readouterr().out
        review_trace.cmd_review_trace(_review_trace_args(branches="main"))
        dropped = capsys.readouterr().out

        assert "branches=feature-x" in kept
        assert "skill        plan-it  (branch=feature-x model=opus)" in kept
        assert review_trace._REVIEW_TRACE_NO_SESSIONS_MSG in dropped


class TestReviewTraceArgparseWiring:
    def test_review_trace_parses_omit_plan_review_spawns(self):
        """The flag is registered, and the default timeline keeps every row without it."""
        parser = cli.build_parser()
        assert parser.parse_args(["review-trace", "--omit-plan-review-spawns"]).omit_plan_review_spawns is True
        assert parser.parse_args(["review-trace"]).omit_plan_review_spawns is False
