"""Tests for transcript_analysis/author_outcome.py (author-outcome)."""
import importlib.util
import itertools
import sys
from pathlib import Path

import pytest
from transcript_analysis import author_outcome as ao
from transcript_analysis import corpus

from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _ledger_row,
    _skill_block,
    _tool_result,
    _user_msg,
    _write_jsonl,
    _write_ledger_file,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package.
# The standard importlib recipe does register in sys.modules and would shadow it --
# don't switch to that recipe here.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _session_iter(fake_projects):
    return corpus.iter_sessions(fake_projects.parent, "*")


def _config_dir_root(fake_projects: Path) -> Path:
    """fake_projects (the conftest fixture) returns the single project
    directory <config_dir_root>/projects/-home-user-testrepo -- two parents
    up is the config-dir root author_outcome.py's own
    _config_dir_root_for_session derives from a transcript path."""
    return fake_projects.parent.parent


def _seed_ledger(fake_projects: Path, session_id: str, rows: list[dict]) -> Path:
    return _write_ledger_file(_config_dir_root(fake_projects), session_id, rows)


def _marker_write_use(tool_id: str) -> dict:
    return _bash_use(tool_id, "marker.sh write code-review")


def _dispatch_start(tool_id: str, ts: str, *, agent_type: str = "code-writer", tool_name: str = "Agent") -> dict:
    return _asst("claude-sonnet-5", branch="feat", ts=ts, content=[_agent_use(tool_id, agent_type, tool_name=tool_name)])


def _dispatch_complete(tool_id: str, ts: str) -> dict:
    return _user_msg([_tool_result(tool_id, "done")], branch="feat", ts=ts)


class TestIsCleanMarkerWrite:
    def test_marker_write_chained_with_git_commit_is_matched(self):
        assert ao._is_clean_marker_write("marker.sh write code-review && git commit -m wip") is True

    def test_unrelated_bash_command_does_not_match(self):
        assert ao._is_clean_marker_write("git status") is False

    def test_path_qualified_marker_write_still_matches_via_basename(self):
        assert ao._is_clean_marker_write("~/.claude/scripts/marker.sh write code-review") is True

    def test_unbalanced_quote_falls_back_to_naive_split(self):
        """shlex.split raises ValueError on the dangling quote below;
        str.split() doesn't interpret quoting at all, so the "&&" inside
        what would have been a single quoted token instead splits into its
        own segment -- the actual, documented behavior of the fallback,
        not shlex's quote-aware one."""
        command = 'marker.sh write code-review "note && oops'
        assert corpus.split_command_segments(command) == [
            ["marker.sh", "write", "code-review", '"note'],
            ["oops"],
        ]
        assert ao._is_clean_marker_write(command) is True


class TestConfigDirRootForSession:
    def test_derives_the_owning_root_for_two_distinct_config_dir_roots(self, tmp_path):
        """TestMultiRootLedgerLookup (below) pins the same invariant through
        compute_author_outcomes' full pipeline; this pins it directly on
        _config_dir_root_for_session, the cheaper unit underneath it."""
        root_a = tmp_path / "acct-a"
        root_b = tmp_path / "acct-b"
        jsonl_a = root_a / "projects" / "-home-user-repo1" / "sess-a.jsonl"
        jsonl_b = root_b / "projects" / "-home-user-repo2" / "sess-b.jsonl"
        jsonl_a.parent.mkdir(parents=True)
        jsonl_b.parent.mkdir(parents=True)
        jsonl_a.write_text("")
        jsonl_b.write_text("")

        assert ao._config_dir_root_for_session(jsonl_a) == root_a
        assert ao._config_dir_root_for_session(jsonl_b) == root_b


class TestLedgerPathForSession:
    def test_finds_ledger_file_by_session_id_glob(self, tmp_path):
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        expected = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        expected.write_text("")
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._ledger_path_for_session(jsonl) == expected

    def test_returns_none_when_no_ledger_file_exists(self, tmp_path):
        jsonl = tmp_path / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._ledger_path_for_session(jsonl) is None


class TestReadLedgerRowsForSession:
    """_write_ledger_file/_ledger_row (the conftest fixtures every other
    test in this file uses) can only ever produce valid-JSON lines, so they
    can't exercise the malformed-line/unreadable-file tolerance
    _read_ledger_rows_for_session's own docstring claims -- these write the
    ledger file directly instead."""

    def test_malformed_line_is_skipped_not_fatal(self, tmp_path):
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        ledger_path = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        ledger_path.write_text(
            '{"round":1,"disposition":"ADDRESS"}\n'
            "this is not json at all\n"
            '{"round":2,"disposition":"DEFER"}\n'
        )
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        rows = ao._read_ledger_rows_for_session(jsonl)

        assert [r["round"] for r in rows] == [1, 2], (
            "the malformed line must be dropped, and the surrounding valid "
            "rows must still be returned in file order"
        )

    def test_unreadable_ledger_path_returns_empty_list(self, tmp_path):
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        # A directory where a ledger file is expected raises OSError
        # (IsADirectoryError) on open() -- the same branch a permission
        # error or other unreadable-file condition would hit.
        (ledger_dir / ("a" * 64 + ".sess-1.jsonl")).mkdir()
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._read_ledger_rows_for_session(jsonl) == []


class TestRoundNumberMismatch:
    def test_matching_sequence_is_not_a_mismatch(self):
        rows = [
            _ledger_row(round=1, disposition="DEFER"),
            _ledger_row(round=1, disposition="ADDRESS"),
            _ledger_row(round=2, disposition="DEFER"),
            _ledger_row(round=3, disposition="DEFER"),
        ]
        assert ao._round_number_mismatch(rows, round_open_count=3) is False

    def test_gap_in_sequence_is_a_mismatch(self):
        """Round 2 opened in the transcript but never got a ledger row --
        a gap between the ledger's own round 1 and round 3."""
        rows = [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=3, disposition="DEFER")]
        assert ao._round_number_mismatch(rows, round_open_count=3) is True

    def test_ledger_round_with_no_corresponding_open_is_a_mismatch(self):
        rows = [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=5, disposition="DEFER")]
        assert ao._round_number_mismatch(rows, round_open_count=1) is True

    def test_legacy_only_ledger_is_not_a_mismatch(self):
        """Every row predates the round field -- nothing to evaluate a
        sequence against, so this must not flag every pre-migration
        session as a mismatch."""
        rows = [_ledger_row(round=None, disposition="ADDRESS"), _ledger_row(round=None, disposition="DEFER")]
        assert ao._round_number_mismatch(rows, round_open_count=2) is False

    def test_empty_ledger_is_not_a_mismatch(self):
        assert ao._round_number_mismatch([], round_open_count=3) is False

    def test_reversed_but_complete_sequence_is_a_mismatch(self):
        """Round rows present in file order 3, 2, 1 -- a complete 1..3 set,
        but not recorded in ascending sequence -- still counts as a
        mismatch under this function's own "round rows recorded out of
        sequence" branch, since first-occurrence order [3, 2, 1] doesn't
        equal the expected [1, 2, 3]."""
        rows = [
            _ledger_row(round=3, disposition="DEFER"),
            _ledger_row(round=2, disposition="DEFER"),
            _ledger_row(round=1, disposition="DEFER"),
        ]
        assert ao._round_number_mismatch(rows, round_open_count=3) is True

    def test_round_value_reappearing_after_a_later_round_is_a_mismatch(self):
        """Round rows in file order 1, 2, 1 -- a session-resume counter
        reset that re-emits round 1 after round 2 already appeared.
        First-occurrence dedup alone collapses this to [1, 2], which is
        set-equal to the expected 1..2 sequence and would wrongly pass;
        this pins that the non-contiguous reappearance is still caught."""
        rows = [
            _ledger_row(round=1, disposition="DEFER"),
            _ledger_row(round=2, disposition="DEFER"),
            _ledger_row(round=1, disposition="ADDRESS"),
        ]
        assert ao._round_number_mismatch(rows, round_open_count=2) is True


class TestClassifyRound:
    def test_address_row_is_failure_even_with_other_defer_rows_present(self):
        data_quality = ao.Counter()
        rows = [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=1, disposition="ADDRESS")]
        classification, matching = ao._classify_round(1, rows, has_marker_write=True, data_quality=data_quality)
        assert classification == ao._OUTCOME_FAILURE
        assert len(matching) == 2

    def test_defer_only_rows_are_pass(self):
        data_quality = ao.Counter()
        rows = [_ledger_row(round=1, disposition="DEFER")]
        classification, _matching = ao._classify_round(1, rows, has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_PASS

    def test_clean_disposition_row_is_pass(self):
        data_quality = ao.Counter()
        rows = [_ledger_row(round=1, disposition="CLEAN", finding="", rationale="")]
        classification, _matching = ao._classify_round(1, rows, has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_PASS

    def test_no_matching_rows_but_marker_write_is_pass_and_counts_kill_switch_inferred(self):
        data_quality = ao.Counter({key: 0 for key in ao._DATA_QUALITY_KEYS})
        classification, matching = ao._classify_round(1, [], has_marker_write=True, data_quality=data_quality)
        assert classification == ao._OUTCOME_PASS
        assert matching == []
        assert data_quality[ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 1

    def test_no_matching_rows_and_no_marker_write_is_unattributed(self):
        data_quality = ao.Counter({key: 0 for key in ao._DATA_QUALITY_KEYS})
        classification, _matching = ao._classify_round(1, [], has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_UNATTRIBUTED
        assert data_quality[ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 0

    def test_legacy_row_without_round_key_never_matches_any_round(self):
        """A legacy row's round is absent (None), which can never equal an
        int round_ordinal -- it must not accidentally satisfy round 1's
        match, even though it's the only row in the ledger."""
        data_quality = ao.Counter({key: 0 for key in ao._DATA_QUALITY_KEYS})
        rows = [_ledger_row(round=None, disposition="ADDRESS")]
        classification, matching = ao._classify_round(1, rows, has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_UNATTRIBUTED
        assert matching == []


class TestComputeAuthorOutcomesBuckets:
    """compute_author_outcomes: each of the four per-dispatch buckets."""

    def test_dispatch_is_failure_when_its_attributed_round_has_an_address_finding(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 0

    def test_dispatch_is_pass_when_its_attributed_round_has_only_defer_findings(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0

    def test_dispatch_is_pass_when_its_attributed_round_has_a_clean_disposition_row(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="CLEAN", finding="", rationale="")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1

    def test_dispatch_is_pass_when_marker_write_present_with_no_ledger_row_at_all(self, fake_projects):
        """The review-narrative-ledger kill switch was on for this round --
        no ledger file exists for the session at all -- but the round's
        own marker.sh write code-review call still ran."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:10.000Z", content=[_marker_write_use("m1")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["data_quality"][ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 1

    def test_dispatch_is_unattributed_when_no_ledger_row_and_no_marker_write(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 1
        assert result["data_quality"][ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 0

    def test_task_tool_name_dispatch_is_picked_up_identically_to_agent_tool_name(self, fake_projects):
        """pricing._SPAWN_TOOL_NAMES covers both "Agent" and "Task" --
        a dispatch spawned via the "Task" tool must classify the same as
        every other fixture in this class, which all use "Agent"."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", tool_name="Task"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0

    def test_dispatch_is_unresolved_when_no_code_review_round_follows_it(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _user_msg("thanks, no review needed", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_UNRESOLVED] == 1


class TestOrderingAndUndecidable:
    def test_dispatch_still_running_when_a_round_opens_attributes_to_the_following_round(self, fake_projects):
        """A round that opens before a still-running dispatch's tool_result
        returns must not claim that dispatch -- attribution is keyed on
        completion index, not start index."""
        session_id = "sess-1"
        # round=1 has its own row too (unrelated to this test's own focus)
        # so the session's ledger round sequence is the exact [1, 2] the
        # transcript's two round-opens expect -- round1 would otherwise
        # read as a round-number mismatch (no round-keyed row at all) and
        # have its own session's dispatches excluded from the headline
        # outcomes this test asserts on.
        _seed_ledger(fake_projects, session_id, [
            _ledger_row(round=1, disposition="DEFER"),
            _ledger_row(round=2, disposition="DEFER"),
        ])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),  # idx0: a1 dispatch starts
            _asst(  # idx1: round1 opens while a1 is still running
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:10.000Z",
                content=[_skill_block("s1", "code-review")],
            ),
            _dispatch_complete("a1", "2026-08-01T10:00:20.000Z"),  # idx2: a1 completes
            _asst(  # idx3: round2 opens
                "claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:30.000Z",
                content=[_skill_block("s2", "code-review")],
            ),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        # Attributed to round2 (PASS, its own DEFER row) -- if round1 had
        # wrongly claimed it, round1's own zero-row/no-marker span would
        # instead classify it UNATTRIBUTED.
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 0

    def test_dispatch_with_no_paired_tool_result_is_undecidable(self, fake_projects):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            # no tool_result for a1 anywhere -- dispatch never completes in this transcript
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 1

    def test_out_of_scope_dispatch_with_no_paired_tool_result_does_not_increment_undecidable(self, fake_projects):
        """_DQ_UNDECIDABLE only tracks in-scope work -- a dispatch that
        falls before a --since cutoff and never completes must not
        inflate the counter."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            # no tool_result for a1 anywhere -- dispatch never completes in this transcript
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        since_ts = corpus._parse_ts("2026-08-01T10:00:30.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 0


class TestInlineAndCoAuthored:
    def test_session_with_no_agent_dispatch_contributes_zero_dispatches(self, fake_projects):
        """A zero-Agent-record session: the round's own ledger row is real
        (and attributable), but no code-writer dispatch exists to charge
        it to -- the "inline" case."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS", authoring_agent="inline")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert sum(result["outcomes"].values()) == 0
        # declared "inline" against a transcript with zero attributing
        # dispatches (transcript_side "inline" too) -- consistent, not counted.
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_two_dispatches_attributed_to_the_same_round_are_a_co_authored_fan_out(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS", authoring_agent="mixed")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _dispatch_start("a2", "2026-08-01T10:00:20.000Z"),
            _dispatch_complete("a2", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 2
        assert result["data_quality"][ao._DQ_CO_AUTHORED_ROUNDS] == 1
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_two_ledger_rows_for_one_round_each_add_their_own_inconsistency(self, fake_projects):
        """Two ledger rows matching the same round, each with its own
        authoring_agent value independently inconsistent with the round's
        transcript_side, must each increment the counter -- not collapse
        into a single per-round flag."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [
            _ledger_row(round=1, disposition="ADDRESS", authoring_agent="code-writer"),
            _ledger_row(round=1, disposition="DEFER", authoring_agent="mixed"),
        ])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 2

    def test_mixed_declared_against_zero_attributing_dispatches_is_inconsistent(self, fake_projects):
        """declared "mixed" against a round with zero attributing
        code-writer dispatches (transcript_side "inline") -- the
        inconsistent side of the mixed branch; the co-authored-fan-out
        test above covers only the consistent side (two dispatches,
        transcript_side "code-writer")."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS", authoring_agent="mixed")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_authoring_agent_inline_declared_against_a_code_writer_round_is_inconsistent(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="inline")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_authoring_agent_unknown_declared_against_a_code_writer_round_is_skipped(self, fake_projects):
        """declared "unknown" against a round genuinely authored by a
        completed code-writer dispatch -- skipped like an absent flag,
        not counted as inconsistent."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="unknown")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_authoring_agent_unknown_declared_against_a_zero_dispatch_round_is_skipped(self, fake_projects):
        """declared "unknown" against a round with zero attributing
        dispatches (transcript_side "inline") -- still skipped, the skip
        does not depend on which transcript_side the round has."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="unknown")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_authoring_agent_code_writer_declared_against_a_code_writer_round_is_consistent(self, fake_projects):
        """declared "code-writer" -- the module's own default -- against a
        round genuinely authored by a completed code-writer dispatch:
        consistent, not counted."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="code-writer")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_transcript_side_uses_the_actual_agent_type_not_a_hardcoded_code_writer(self, fake_projects):
        """A non-default --agent run must compare against its own agent_type,
        not a hardcoded "code-writer" literal -- a round attributed to a
        "some-other-agent" dispatch with "code-writer" declared in the ledger
        is inconsistent, which a hardcoded transcript_side would have missed
        (it would have read "code-writer" too and reported consistent)."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="code-writer")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", agent_type="some-other-agent"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects), agent_type="some-other-agent")
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_mixed_declared_against_a_non_default_agent_type_is_consistent(self, fake_projects):
        """The "mixed" branch must compare transcript_side against the
        actual agent_type too, not a hardcoded "code-writer" literal -- a
        round attributed to a "general-purpose" dispatch with "mixed"
        declared in the ledger is consistent, which a hardcoded comparison
        against "code-writer" would have missed (it would have read
        transcript_side "general-purpose" != "code-writer" and reported
        inconsistent)."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="mixed")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", agent_type="general-purpose"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects), agent_type="general-purpose")
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_authoring_agent_code_writer_declared_against_a_zero_dispatch_round_is_inconsistent(self, fake_projects):
        """declared "code-writer" against a round with zero attributing
        dispatches (transcript_side "inline") -- inconsistent."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="code-writer")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_authoring_agent_absent_is_skipped_not_miscounted(self, fake_projects):
        """authoring_agent recorded as the empty string (review-ledger.sh's
        own "not declared" sentinel, or a pre-migration row entirely
        missing the key) is skipped by the cross-check rather than treated
        as an inconsistency."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

    def test_legacy_row_without_round_key_is_skipped_by_authoring_agent_cross_check(self, fake_projects):
        """A legacy row never matches any round (see TestClassifyRound), so
        it never enters a round's own matching_ledger_rows list and can't
        reach the authoring_agent cross-check at all -- this must not
        crash, and must not count anything."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=None, disposition="ADDRESS", authoring_agent="mixed")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 1
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0


class TestSinceFilterMainline:
    def test_since_filter_counts_the_in_window_dispatch_and_excludes_the_out_of_window_one(self, fake_projects):
        """The ordinary, most-common --since shape: two dispatches in one
        session, each attributed normally to its own round -- one before
        since_ts, one after. The in-window dispatch (a2, PASS) must be
        counted in "Dispatches in scope" and its own outcome bucket; the
        out-of-window dispatch (a1, would-be FAILURE) must be excluded from
        both, not merely from the outcome label."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [
            _ledger_row(round=1, disposition="ADDRESS"),
            _ledger_row(round=2, disposition="DEFER"),
        ])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _dispatch_start("a2", "2026-08-01T10:02:00.000Z"),
            _dispatch_complete("a2", "2026-08-01T10:02:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:03:00.000Z", content=[_skill_block("s2", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:04:00.000Z"),
        ])
        since_ts = corpus._parse_ts("2026-08-01T10:01:30.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert sum(result["outcomes"].values()) == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0


class TestSinceFilterBifurcation:
    def test_authoring_agent_inconsistent_uses_unfiltered_dispatch_count_not_since_filtered(self, fake_projects):
        """A --since cutoff landing after the attributing dispatch's own
        completion timestamp but before the round's own span is the only
        shape distinguishing "correctly unfiltered" from "still filtered":
        a1 fails the --since filter (excluded from "Dispatches in scope")
        yet still completed inside the span that attributes it to this
        round, so the cross-check's own transcript_side must still read
        "code-writer", not silently fall back to "inline"."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER", authoring_agent="inline")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        since_ts = corpus._parse_ts("2026-08-01T10:00:30.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 1

    def test_round_number_mismatch_still_increments_when_the_only_dispatch_is_out_of_since_scope(self, fake_projects):
        """_DQ_ROUND_NUMBER_MISMATCH is computed from the ledger/transcript
        round-open comparison alone, before any --since filtering -- it must
        still increment even when the session's only dispatch falls entirely
        outside the --since cutoff and so contributes zero to "Dispatches in
        scope"."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [
            _ledger_row(round=1, disposition="DEFER"),
            _ledger_row(round=3, disposition="DEFER"),
        ])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T09:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T09:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s2", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:02:00.000Z", content=[_skill_block("s3", "code-review")]),
        ])
        since_ts = corpus._parse_ts("2026-08-01T09:30:00.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        assert sum(result["outcomes"].values()) == 0


class TestRoundNumberMismatchIntegration:
    def test_ledger_missing_a_round_between_two_present_rounds_increments_the_counter(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [
            _ledger_row(round=1, disposition="DEFER"),
            _ledger_row(round=3, disposition="DEFER"),
        ])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s2", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:02:00.000Z", content=[_skill_block("s3", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1

    def test_ledger_matching_every_round_open_does_not_increment_the_counter(self, fake_projects):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="DEFER")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 0

    def test_mismatched_sessions_dispatches_excluded_from_headline_outcomes(self, fake_projects):
        """A round-number-mismatched session's own dispatches must not
        pollute the headline outcomes/"Dispatches in scope" aggregate --
        the session still counts toward _DQ_ROUND_NUMBER_MISMATCH itself.
        A clean session's dispatch in the same run is unaffected."""
        mismatched_session_id = "sess-mismatched"
        _seed_ledger(fake_projects, mismatched_session_id, [
            _ledger_row(round=1, disposition="ADDRESS"),
            _ledger_row(round=3, disposition="DEFER"),
        ])
        _write_jsonl(fake_projects / f"{mismatched_session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:02:00.000Z", content=[_skill_block("s2", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:03:00.000Z", content=[_skill_block("s3", "code-review")]),
        ])

        clean_session_id = "sess-clean"
        _seed_ledger(fake_projects, clean_session_id, [_ledger_row(round=1, disposition="DEFER")])
        _write_jsonl(fake_projects / f"{clean_session_id}.jsonl", [
            _dispatch_start("b1", "2026-08-01T11:00:00.000Z"),
            _dispatch_complete("b1", "2026-08-01T11:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T11:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T11:02:00.000Z"),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        # a1's round (round 1, ADDRESS) would otherwise be a FAILURE -- excluded.
        # b1's round (round 1, DEFER, from the clean session) is still a PASS.
        assert sum(result["outcomes"].values()) == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0

    def test_two_worktrees_of_one_session_id_trigger_the_mismatch_exclusion(self, fake_projects):
        """Accepted risk (docs/transcript-analysis.md's author-outcome
        section): a session spanning more than one worktree writes one
        ledger file per repo-hash under the same session id, but
        _ledger_path_for_session's sorted-first glob picks only one of
        them. Here the picked file (repo_hash "0"*64, sorts first) covers
        only round 1 of the session's two code-review rounds, while the
        other worktree's file (repo_hash "1"*64, never read) covers both --
        pinning that the round-number-mismatch exclusion this design
        already relies on for a same-worktree gap also fires for a genuine
        cross-worktree collision, dropping the session's dispatches from
        `outcomes`."""
        session_id = "sess-multi-worktree"
        config_dir_root = _config_dir_root(fake_projects)
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=1, disposition="ADDRESS")],
            repo_hash="0" * 64,
        )
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=2, disposition="DEFER")],
            repo_hash="1" * 64,
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:02:00.000Z", content=[_skill_block("s2", "code-review")]),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        # a1's attributed round (round 1, ADDRESS from the picked file)
        # would otherwise be a FAILURE -- excluded by the mismatch instead.
        assert sum(result["outcomes"].values()) == 0

    def test_mismatched_session_still_increments_transcript_side_dq_counters(self, fake_projects):
        """_DQ_UNDECIDABLE and _DQ_CO_AUTHORED_ROUNDS measure transcript-side
        dispatch-to-round attribution, not the ledger/round join
        session_round_mismatch guards -- both must still increment for a
        mismatched session, unlike the headline outcomes counters, which
        this same session's mismatch excludes."""
        session_id = "sess-mismatched-with-quality-issues"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=2, disposition="DEFER")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _dispatch_start("a2", "2026-08-01T10:01:00.000Z"),
            _dispatch_complete("a2", "2026-08-01T10:01:10.000Z"),
            _dispatch_start("a3", "2026-08-01T10:02:00.000Z"),  # never completes
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:03:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))

        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        # a1 and a2 both complete before the session's one round-open and
        # attribute to it, co-authoring round 1 despite the mismatch above.
        assert result["data_quality"][ao._DQ_CO_AUTHORED_ROUNDS] == 1
        # a3 never gets a paired tool_result, so it's undecidable regardless
        # of the mismatch above.
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 1


class TestMultiRootLedgerLookup:
    """_config_dir_root_for_session derives each session's own ledger
    lookup root from that session's own jsonl path, not from a single root
    fixed by the caller -- a session scanned from a second --config-dir
    root must still find its own ledger file there, not silently read as
    zero rows and get misclassified."""

    def test_second_roots_session_ledger_row_is_found_not_dropped(
        self, fake_projects, fake_config_dir_factory,
    ):
        root1_session_id = "sess-root1"
        _seed_ledger(fake_projects, root1_session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{root1_session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])

        acct_b = fake_config_dir_factory("acct-b")
        root2_project = acct_b / "projects" / "-home-user-otherrepo"
        root2_project.mkdir(parents=True)
        root2_session_id = "sess-root2"
        _write_ledger_file(acct_b, root2_session_id, [_ledger_row(round=1, disposition="DEFER")])
        _write_jsonl(root2_project / f"{root2_session_id}.jsonl", [
            _dispatch_start("b1", "2026-08-01T11:00:00.000Z"),
            _dispatch_complete("b1", "2026-08-01T11:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T11:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T11:02:00.000Z"),
        ])

        session_iter = itertools.chain(
            corpus.iter_sessions(fake_projects.parent, "*"),
            corpus.iter_sessions(acct_b / "projects", "*"),
        )
        result = ao.compute_author_outcomes(session_iter)
        # root1's ADDRESS row -> FAILURE; root2's DEFER row -> PASS. A
        # dropped root2 ledger lookup would read root2 as zero rows, no
        # marker write, and reclassify it UNATTRIBUTED instead of PASS.
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 0


class TestBuildParserWiring:
    """build_parser() as a testable seam -- the argparse layer without
    executing a subcommand or shelling out to git."""

    def test_author_outcome_defaults(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["author-outcome"])
        assert parsed.agent == "code-writer"
        assert parsed.since is None
        assert parsed.this_repo is False

    def test_author_outcome_this_repo_and_projects_mutually_exclusive(self):
        parser = _mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["author-outcome", "--this-repo", "--projects", "x"])

    def test_author_outcome_agent_and_since_flags_parse(self):
        parser = _mod.build_parser()
        parsed = parser.parse_args(["author-outcome", "--agent", "staff-sdet", "--since", "14d"])
        assert parsed.agent == "staff-sdet"
        assert parsed.since == "14d"


class TestCmdAuthorOutcomeReport:
    """cmd_author_outcome end-to-end."""

    def _args(self, *, agent: str = "code-writer", since: str | None = None, this_repo: bool = False, projects: str = "*"):
        return type("A", (), {"agent": agent, "since": since, "this_repo": this_repo, "projects": projects})()

    def test_report_prints_header_and_bucket_lines(self, fake_projects, capsys):
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args())
        out = capsys.readouterr().out
        assert "AUTHOR OUTCOME SOURCES" in out
        assert "agent=code-writer  window=all time" in out
        assert "Dispatches in scope" in out
        assert "Failure share: 1 of 1 resolved dispatches (100.0%)" in out
        assert "Data quality" in out

    def test_agent_inline_is_rejected_as_a_reserved_sentinel(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _mod.cmd_author_outcome(self._args(agent=ao._AUTHORING_AGENT_INLINE))
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "reserved sentinel" in err

    def test_report_prints_zero_resolved_dispatches_without_zero_division(self, fake_projects, capsys):
        """A session with only an UNRESOLVED dispatch (no code-review round
        ever follows it) contributes zero to both failure and passed, so
        resolved computes to 0 -- render._pct_of's pre-existing zero-guard
        renders that as "0 of 0 resolved dispatches (0.0%)"."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _user_msg("thanks, no review needed", branch="feat", ts="2026-08-01T10:01:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args())
        out = capsys.readouterr().out
        assert "Failure share: 0 of 0 resolved dispatches (0.0%)" in out
