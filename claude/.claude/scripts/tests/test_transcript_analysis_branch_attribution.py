"""Tests for transcript_analysis/scope.py's per-record branch attribution
(_session_branch_context, _attributed_branch, _attribute_carried_branch): the pre-branch step that credits a session's leading
default-branch records to the first branch it enters, and the timeline step
that resolves every sidechain record against its dispatching main thread."""
import re

import pytest
from helpers import REPO_ROOT
from transcript_analysis import scope


def _iso(minute: int) -> str:
    """Strictly increasing timestamps: one minute apart within one hour."""
    return f"2026-08-01T10:{minute:02d}:00.000Z"


def _ts(minute: int) -> float:
    return scope._parse_ts(_iso(minute))


def _record(branch: str | None, minute: int | None, *, sidechain: bool = False) -> dict:
    rec: dict = {"type": "assistant", "isSidechain": sidechain}
    if branch is not None:
        rec["gitBranch"] = branch
    if minute is not None:
        rec["timestamp"] = _iso(minute)
    return rec


def _attributed(records: list[dict], target: dict) -> str | None:
    return scope._attributed_branch(target, scope._session_branch_context(records))


class TestPreBranchStep:
    def test_leading_default_branch_record_counts_toward_the_first_branch_entered(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        assert _attributed([leading, entered], leading) == "feature-x"
        assert _attributed([leading, entered], entered) == "feature-x"

    def test_trailing_default_branch_record_after_the_boundary_stays_on_the_default_branch(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        trailing = _record("main", 5)
        records = [leading, entered, trailing]
        assert _attributed(records, leading) == "feature-x"
        assert _attributed(records, trailing) == "main"

    def test_only_the_first_real_branch_receives_the_leading_run(self):
        leading = _record("main", 1)
        first = _record("feature-x", 3)
        second = _record("feature-y", 5)
        records = [leading, first, second]
        assert _attributed(records, leading) == "feature-x"
        assert _attributed(records, second) == "feature-y"

    def test_consecutive_default_branch_entries_all_fold_into_the_first_real_branch(self):
        on_main = _record("main", 1)
        on_develop = _record("develop", 2)
        entered = _record("feature-x", 4)
        records = [on_main, on_develop, entered]
        assert _attributed(records, on_main) == "feature-x"
        assert _attributed(records, on_develop) == "feature-x"

    @pytest.mark.parametrize("default_branch", ["main", "master", "develop"])
    def test_each_default_branch_candidate_starts_a_leading_run(self, default_branch):
        leading = _record(default_branch, 1)
        entered = _record("feature-x", 3)
        assert _attributed([leading, entered], leading) == "feature-x"

    def test_session_that_starts_on_a_feature_branch_is_not_remapped(self):
        on_feature = _record("feature-x", 1)
        later_main = _record("main", 3)
        records = [on_feature, later_main]
        assert _attributed(records, on_feature) == "feature-x"
        assert _attributed(records, later_main) == "main"

    def test_session_that_never_leaves_the_default_branch_stays_on_it(self):
        only = _record("main", 1)
        later = _record("main", 3)
        assert _attributed([only, later], only) == "main"

    @pytest.mark.parametrize("near_miss", ["main-x", "develop/foo", "Main", "mainline"])
    def test_near_miss_branch_names_are_not_default_branch_candidates(self, near_miss):
        leading = _record(near_miss, 1)
        entered = _record("feature-x", 3)
        assert _attributed([leading, entered], leading) == near_miss

    def test_detached_head_entry_before_the_run_is_skipped_and_kept_raw(self):
        detached = _record("HEAD", 1)
        leading = _record("main", 2)
        entered = _record("feature-x", 4)
        records = [detached, leading, entered]
        assert _attributed(records, detached) == "HEAD"
        assert _attributed(records, leading) == "feature-x"

    def test_detached_head_entry_inside_the_run_is_never_the_successor(self):
        leading = _record("main", 1)
        detached = _record("HEAD", 2)
        entered = _record("feature-x", 4)
        records = [leading, detached, entered]
        assert _attributed(records, leading) == "feature-x"
        assert _attributed(records, detached) == "HEAD"

    def test_main_thread_worktree_agent_entry_is_never_the_successor_and_stays_raw(self):
        leading = _record("main", 1)
        isolated = _record("worktree-agent-abc123", 2)
        entered = _record("feature-x", 4)
        records = [leading, isolated, entered]
        assert _attributed(records, leading) == "feature-x"
        assert _attributed(records, isolated) == "worktree-agent-abc123"

    def test_session_that_only_reaches_a_worktree_agent_branch_remaps_nothing(self):
        leading = _record("main", 1)
        isolated = _record("worktree-agent-abc123", 3)
        assert _attributed([leading, isolated], leading) == "main"

    def test_default_branch_record_without_a_timestamp_stays(self):
        leading = _record("main", 1)
        untimed = _record("main", None)
        entered = _record("feature-x", 3)
        records = [leading, untimed, entered]
        assert _attributed(records, untimed) == "main"
        assert _attributed(records, leading) == "feature-x"

    def test_record_at_exactly_the_boundary_timestamp_stays(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        at_boundary = _record("main", 3)
        records = [leading, entered, at_boundary]
        assert _attributed(records, at_boundary) == "main"
        assert _attributed(records, leading) == "feature-x"

    def test_record_without_a_branch_is_never_remapped(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        branchless = _record(None, 2)
        records = [leading, branchless, entered]
        assert _attributed(records, branchless) is None

    def test_tied_first_entries_sort_by_branch_name_so_a_tied_real_branch_ends_the_run_before_it_starts(self):
        """index.sort() orders equal timestamps by branch name: "X" < "main"
        puts the real branch first, so the first remaining entry is not a
        candidate and no run exists."""
        main_entry = _record("main", 1)
        real_entry = _record("X", 1)
        context = scope._session_branch_context([main_entry, real_entry])
        assert context.pre_branch_run is None
        assert scope._attributed_branch(main_entry, context) == "main"

    def test_tied_first_entries_with_the_candidate_sorting_first_start_a_run_that_excludes_the_tie(self):
        """"develop" < "feature": the candidate sorts first, the run's
        boundary is the shared timestamp, and a record at exactly that
        timestamp stays."""
        develop_entry = _record("develop", 1)
        feature_entry = _record("feature", 1)
        context = scope._session_branch_context([develop_entry, feature_entry])
        assert context.pre_branch_run == scope._PreBranchRun("feature", _ts(1))
        assert scope._attributed_branch(develop_entry, context) == "develop"


class TestTimelineStep:
    def test_prefix_sidechain_record_tagged_with_the_default_branch_resolves_to_the_entered_branch(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 5)
        prefix_sidechain = _record("main", 3, sidechain=True)
        assert _attributed([leading, entered, prefix_sidechain], prefix_sidechain) == "feature-x"

    def test_post_boundary_sidechain_record_tagged_with_the_default_branch_resolves_to_the_entered_branch(self):
        """An architect still running after the session entered X: its own
        tag is the dispatch-time default branch, but the main thread is on X."""
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        still_running = _record("main", 6, sidechain=True)
        assert _attributed([leading, entered, still_running], still_running) == "feature-x"

    def test_sidechain_record_with_a_foreign_tag_follows_the_main_thread(self):
        entered = _record("feature-x", 1)
        foreign = _record("other", 3, sidechain=True)
        assert _attributed([entered, foreign], foreign) == "feature-x"

    def test_sidechain_record_after_a_second_switch_resolves_to_the_second_branch_whatever_its_tag(self):
        leading = _record("main", 1)
        first = _record("feature-x", 3)
        second = _record("feature-y", 5)
        stale_tag = _record("feature-x", 7, sidechain=True)
        assert _attributed([leading, first, second, stale_tag], stale_tag) == "feature-y"

    def test_sidechain_record_at_two_tied_entries_resolves_to_the_later_sorted_entry(self):
        leading = _record("main", 1)
        tied_first = _record("feature-x", 3)
        tied_second = _record("feature-y", 3)
        at_tie = _record("main", 3, sidechain=True)
        assert _attributed([leading, tied_first, tied_second, at_tie], at_tie) == "feature-y"

    def test_sidechain_record_without_a_gitbranch_takes_the_main_threads_branch(self):
        entered = _record("feature-x", 1)
        untagged = _record(None, 3, sidechain=True)
        assert _attributed([entered, untagged], untagged) == "feature-x"

    def test_sidechain_record_without_a_timestamp_falls_forward_to_the_first_entry(self):
        first = _record("feature-x", 1)
        second = _record("feature-y", 3)
        untimed = _record("feature-y", None, sidechain=True)
        assert _attributed([first, second, untimed], untimed) == "feature-x"

    def test_sidechain_record_without_a_timestamp_resolves_to_the_entered_branch_when_the_first_entry_is_in_the_prefix(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        untimed = _record("main", None, sidechain=True)
        assert _attributed([leading, entered, untimed], untimed) == "feature-x"

    def test_sidechain_record_timestamped_before_the_first_entry_falls_forward_to_it(self):
        early = _record("feature-x", 1, sidechain=True)
        entered = _record("feature-x", 5)
        assert _attributed([early, entered], early) == "feature-x"

    def test_sidechain_record_predating_a_multi_entry_index_resolves_to_the_first_entry_not_the_last(self):
        """With two or more entries, position -1 and position 0 name different
        entries, so the fall-forward clamp is what keeps the record on the first."""
        first = _record("feature-x", 3)
        second = _record("feature-y", 5)
        early = _record("feature-y", 1, sidechain=True)
        assert _attributed([first, second, early], early) == "feature-x"

    def test_sidechain_record_predating_a_prefix_index_resolves_to_the_entered_branch(self):
        leading = _record("main", 3)
        entered = _record("feature-x", 5)
        early = _record("main", 1, sidechain=True)
        assert _attributed([leading, entered, early], early) == "feature-x"

    @pytest.mark.parametrize("minute", [2, 6])
    def test_worktree_agent_sidechain_record_in_and_after_the_prefix_resolves_to_the_entered_branch(self, minute):
        leading = _record("main", 1)
        entered = _record("feature-x", 4)
        isolated = _record("worktree-agent-abc123", minute, sidechain=True)
        assert _attributed([leading, entered, isolated], isolated) == "feature-x"

    def test_session_with_no_branch_bearing_main_thread_record_resolves_sidechain_records_to_none(self):
        branchless_main = _record(None, 1)
        sidechain = _record("feature-x", 3, sidechain=True)
        assert _attributed([branchless_main, sidechain], sidechain) is None

    def test_main_thread_branches_without_timestamps_give_the_first_one_in_file_order(self):
        first_untimed = _record("feature-x", None)
        second_untimed = _record("feature-y", None)
        sidechain = _record("other", 3, sidechain=True)
        assert _attributed([first_untimed, second_untimed, sidechain], sidechain) == "feature-x"

    def test_untimed_main_thread_default_branch_is_not_remapped_for_a_sidechain_record(self):
        first_untimed = _record("main", None)
        second_untimed = _record("feature-x", None)
        sidechain = _record("other", 3, sidechain=True)
        assert _attributed([first_untimed, second_untimed, sidechain], sidechain) == "main"


class TestIndexOrdering:
    def test_main_thread_records_out_of_timestamp_order_build_the_same_context_as_in_order(self):
        in_order = [_record("main", 1), _record("feature-x", 5)]
        out_of_order = [_record("feature-x", 5), _record("main", 1)]
        in_order_context = scope._session_branch_context(in_order)
        out_of_order_context = scope._session_branch_context(out_of_order)
        assert out_of_order_context.branch_index == in_order_context.branch_index
        assert out_of_order_context.pre_branch_run == in_order_context.pre_branch_run
        assert out_of_order_context.pre_branch_run == scope._PreBranchRun("feature-x", _ts(5))

    def test_sidechain_record_between_out_of_order_main_thread_records_resolves_to_the_entered_branch(self):
        records = [_record("feature-x", 5), _record("main", 1)]
        sidechain = _record("main", 3, sidechain=True)
        assert _attributed([*records, sidechain], sidechain) == "feature-x"


class TestNonStringBranchTags:
    """On the timeline path (the index, the timeline entry and the file-order
    fallback) a malformed (non-string) gitBranch is read as an absent tag and
    never raised on. Positional consumers read the tag through the same helper
    (see test_transcript_review_rounds.py)."""

    @pytest.mark.parametrize("malformed_tag", [7, ["main"], {"name": "main"}, True])
    def test_non_string_main_thread_tag_is_ignored_without_raising(self, malformed_tag):
        leading = _record("main", 1)
        malformed = _record(None, 2)
        malformed["gitBranch"] = malformed_tag
        entered = _record("feature-x", 3)
        sidechain = _record("other", 4, sidechain=True)
        records = [leading, malformed, entered, sidechain]

        context = scope._session_branch_context(records)

        assert context.branch_index == [(_ts(1), "main"), (_ts(3), "feature-x")]
        assert scope._attributed_branch(malformed, context) is None
        assert scope._attributed_branch(leading, context) == "feature-x"
        assert scope._attributed_branch(sidechain, context) == "feature-x"

    def test_non_string_tag_is_never_the_file_order_fallback_branch(self):
        malformed = _record(None, None)
        malformed["gitBranch"] = ["main"]
        untimed = _record("feature-x", None)
        sidechain = _record("other", 3, sidechain=True)
        assert _attributed([malformed, untimed, sidechain], sidechain) == "feature-x"


class TestEmptyStringBranchTags:
    """An empty gitBranch is read as an absent tag."""

    def test_leading_empty_tagged_record_neither_enters_the_index_nor_turns_off_the_pre_branch_step(self):
        empty_tagged = _record("", 0)
        leading = _record("main", 2)
        entered = _record("feature-x", 4)
        context = scope._session_branch_context([empty_tagged, leading, entered])

        assert context.branch_index == [(_ts(2), "main"), (_ts(4), "feature-x")]
        assert context.pre_branch_run == scope._PreBranchRun("feature-x", _ts(4))
        assert scope._attributed_branch(empty_tagged, context) is None
        assert scope._attributed_branch(leading, context) == "feature-x"

    def test_empty_first_tag_is_never_the_file_order_fallback_branch(self):
        empty_tagged = _record("", None)
        untimed = _record("feature-x", None)
        sidechain = _record("other", 3, sidechain=True)
        assert _attributed([empty_tagged, untimed, sidechain], sidechain) == "feature-x"


class TestIndexPurity:
    def test_sidechain_record_with_a_foreign_tag_is_neither_an_index_entry_nor_the_successor(self):
        leading = _record("main", 1)
        foreign = _record("foreign-branch", 2, sidechain=True)
        entered = _record("feature-x", 3)
        context = scope._session_branch_context([leading, foreign, entered])
        assert context.branch_index == [(_ts(1), "main"), (_ts(3), "feature-x")]
        assert context.pre_branch_run == scope._PreBranchRun("feature-x", _ts(3))


class TestAttributeCarriedBranch:
    def test_carried_default_branch_before_the_boundary_is_remapped_by_the_records_own_timestamp(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        context = scope._session_branch_context([leading, entered])
        assert scope._attribute_carried_branch("main", _record(None, 2, sidechain=True), context) == "feature-x"

    def test_carried_default_branch_at_or_after_the_boundary_stays(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        context = scope._session_branch_context([leading, entered])
        assert scope._attribute_carried_branch("main", _record(None, 3), context) == "main"
        assert scope._attribute_carried_branch("main", _record(None, 5), context) == "main"

    def test_record_without_a_timestamp_is_never_remapped(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        context = scope._session_branch_context([leading, entered])
        assert scope._attribute_carried_branch("main", _record(None, None), context) == "main"

    def test_empty_carried_branch_stays_empty(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        context = scope._session_branch_context([leading, entered])
        assert scope._attribute_carried_branch("", _record(None, 2), context) == ""

    def test_non_default_carried_branch_is_untouched(self):
        leading = _record("main", 1)
        entered = _record("feature-x", 3)
        context = scope._session_branch_context([leading, entered])
        assert scope._attribute_carried_branch("feature-y", _record(None, 2), context) == "feature-y"


class TestDefaultBranchCandidatesMirrorTheShellLibrary:
    def test_candidates_equal_the_loop_in_lib_default_branch_or_guess(self):
        lib_source = (REPO_ROOT / "claude" / ".claude" / "hooks" / "_lib.sh").read_text()
        function_header = "_lib_default_branch_or_guess() {"
        assert function_header in lib_source, f"{function_header} not found in _lib.sh"
        function_start = lib_source.index(function_header)
        function_end = lib_source.index("\n}\n", function_start)
        loop_match = re.search(r"for candidate in ([^;]+); do", lib_source[function_start:function_end])
        assert loop_match is not None, "candidate loop not found in _lib_default_branch_or_guess"
        assert frozenset(loop_match.group(1).split()) == scope._DEFAULT_BRANCH_CANDIDATES
