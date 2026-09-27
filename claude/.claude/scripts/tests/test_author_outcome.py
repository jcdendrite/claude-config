"""Tests for transcript_analysis/author_outcome.py (author-outcome)."""
import importlib.util
import itertools
import json
import os
import subprocess
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
_REVIEW_LEDGER_SH = Path(__file__).parent.parent / "review-ledger.sh"
_LIB_SH = Path(__file__).parent.parent.parent / "hooks" / "_lib.sh"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package.
# The standard importlib recipe does register in sys.modules and would shadow it --
# don't switch to that recipe here.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

# claude/.claude/ itself, so `hooks.tests.conftest` below resolves.
# Mirrors test_config_parser_parity.py's identical cross-package import.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from hooks.tests.conftest import _seed_session  # noqa: E402


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


def _entries(rows: list[dict], file_index: int = 0) -> list[tuple[int, dict]]:
    """Tags every row with the same source file index -- _round_number_mismatch's
    own (file_index, row) input shape, for the common single-file case most
    of its unit tests exercise."""
    return [(file_index, row) for row in rows]


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


class TestRoundHasMarkerWrite:
    def test_sidechain_marker_write_alone_is_not_found(self):
        """isSidechain assistant records are a subagent's own transcript
        entries replayed into the main session file -- a marker.sh write
        there must not count as this round's own clean-review signal."""
        records = [_asst("claude-sonnet-5", sidechain=True, content=[_marker_write_use("side-1")])]
        assert ao._round_has_marker_write(records, 0, len(records)) is False

    def test_main_thread_marker_write_is_still_found_alongside_a_sidechain_one(self):
        records = [
            _asst("claude-sonnet-5", sidechain=True, content=[_marker_write_use("side-1")]),
            _asst("claude-sonnet-5", content=[_marker_write_use("main-1")]),
        ]
        assert ao._round_has_marker_write(records, 0, len(records)) is True


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


class TestLedgerFilesForSession:
    def test_finds_ledger_file_by_session_id_glob(self, tmp_path):
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        expected = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        expected.write_text("")
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._ledger_files_for_session(jsonl) == [expected]

    def test_returns_empty_list_when_no_ledger_file_exists(self, tmp_path):
        jsonl = tmp_path / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._ledger_files_for_session(jsonl) == []

    def test_finds_every_repo_hash_prefixed_file_for_the_same_session_id(self, tmp_path):
        """A session spanning two git worktrees of the same repo writes one
        ledger file per repo-hash under the same session id -- both must be
        found, sorted by filename."""
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        first = ledger_dir / ("0" * 64 + ".sess-1.jsonl")
        second = ledger_dir / ("1" * 64 + ".sess-1.jsonl")
        second.write_text("")
        first.write_text("")
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._ledger_files_for_session(jsonl) == [first, second]


class TestReadLedgerRowEntriesForSession:
    """_write_ledger_file/_ledger_row (the conftest fixtures every other
    test in this file uses) can only ever produce valid-JSON lines, so they
    can't exercise the malformed-line/unreadable-file tolerance
    _read_ledger_row_entries_for_session's own docstring claims -- these
    write the ledger file directly instead."""

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

        entries, any_file_found = ao._read_ledger_row_entries_for_session(jsonl)
        rows = [row for _file_index, row in entries]

        assert [r["round"] for r in rows] == [1, 2], (
            "the malformed line must be dropped, and the surrounding valid "
            "rows must still be returned in file order"
        )
        assert any_file_found is True

    def test_non_dict_json_line_is_skipped_not_fatal(self, tmp_path):
        """A bare JSON array parses cleanly but isn't a row.
        Distinct from the JSONDecodeError branch
        test_malformed_line_is_skipped_not_fatal exercises above."""
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        ledger_path = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        ledger_path.write_text('[1,2,3]\n{"round":1,"disposition":"ADDRESS"}\n')
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        entries, any_file_found = ao._read_ledger_row_entries_for_session(jsonl)
        rows = [row for _file_index, row in entries]

        assert [r["round"] for r in rows] == [1], (
            "the non-dict line must be dropped, and the surrounding valid "
            "row must still be returned"
        )
        assert any_file_found is True

    def test_unreadable_ledger_path_returns_empty_list_and_is_not_found(self, tmp_path):
        """A directory where a ledger file is expected raises OSError
        (IsADirectoryError) on open() -- the same branch a permission
        error or other unreadable-file condition would hit. A file that
        matched the glob but couldn't be opened at all must not count
        toward any_file_found (see _read_ledger_rows_from_file's own
        opened=False contract): it's indistinguishable here from the file
        never having existed, which is exactly what makes it a possibly-
        swept signal rather than a present-but-empty one."""
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        (ledger_dir / ("a" * 64 + ".sess-1.jsonl")).mkdir()
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        assert ao._read_ledger_row_entries_for_session(jsonl) == ([], False)

    def test_file_vanishing_between_glob_and_open_is_not_found(self, tmp_path, monkeypatch):
        """A file present when _ledger_files_for_session globbed it, but
        gone by the time _read_ledger_rows_from_file actually opens it
        (e.g. a concurrent review-ledger.sh clear-stale sweep), must not
        count toward any_file_found. Simulated here by having the glob
        stub return a path that was never created, which raises the
        identical OSError branch a real mid-read deletion would."""
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        vanished_path = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")
        monkeypatch.setattr(ao, "_ledger_files_for_session", lambda _jsonl: [vanished_path])

        assert ao._read_ledger_row_entries_for_session(jsonl) == ([], False)

    def test_one_file_vanishing_does_not_suppress_a_surviving_siblings_rows(self, tmp_path, monkeypatch):
        """The realistic multi-worktree race: two files glob-matched, one
        opens fine, the other gets evicted (e.g. by a concurrent
        clear-stale sweep) before its own open(). Guards against
        `any_file_opened = any_file_opened or opened` regressing to `and`,
        which would silently report any_file_found=False and misroute a
        live session into the possibly-swept bucket even though a real
        file's rows were read fine."""
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        surviving_path = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        surviving_path.write_text('{"round":1,"disposition":"ADDRESS"}\n')
        vanished_path = ledger_dir / ("b" * 64 + ".sess-1.jsonl")
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")
        monkeypatch.setattr(
            ao, "_ledger_files_for_session", lambda _jsonl: [surviving_path, vanished_path]
        )

        entries, any_file_found = ao._read_ledger_row_entries_for_session(jsonl)

        assert any_file_found is True
        assert [row["round"] for _file_index, row in entries] == [1]

    def test_unreadable_ledger_path_prints_diagnostic_to_stderr(self, tmp_path, capsys):
        config_dir_root = tmp_path
        ledger_dir = config_dir_root / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True)
        ledger_path = ledger_dir / ("a" * 64 + ".sess-1.jsonl")
        ledger_path.mkdir()
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        ao._read_ledger_row_entries_for_session(jsonl)

        captured = capsys.readouterr()
        assert str(ledger_path) in captured.err
        assert captured.out == ""

    def test_merge_sorts_by_event_time_not_file_glob_order(self, tmp_path):
        """Two files matching the session-id glob, with real (fixture-
        supplied, not the shared constant default) event_time values that
        invert filename/glob order -- repo_hash "0"*64 sorts first by
        glob, but carries the LATER event_time, while "f"*64 sorts last by
        glob and carries the EARLIER one. Every row in every fixture used
        elsewhere in this file shares one fixed event_time, which makes
        Python's stable sort fall back to file-encounter order and would
        silently mask a broken sort key. Mirrors
        test_show_merges_rows_from_a_different_repo_hash_in_event_time_order
        in test_review_ledger_script.py, which pins the same invariant on
        the shell side."""
        config_dir_root = tmp_path
        _write_ledger_file(
            config_dir_root, "sess-1",
            [_ledger_row(round=2, disposition="DEFER", event_time="2026-08-01T10:05:00Z")],
            repo_hash="0" * 64,
        )
        _write_ledger_file(
            config_dir_root, "sess-1",
            [_ledger_row(round=1, disposition="ADDRESS", event_time="2026-08-01T10:00:00Z")],
            repo_hash="f" * 64,
        )
        jsonl = config_dir_root / "projects" / "-home-user-testrepo" / "sess-1.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("")

        entries, any_file_found = ao._read_ledger_row_entries_for_session(jsonl)

        assert any_file_found is True
        assert [row["round"] for _file_index, row in entries] == [1, 2], (
            "the merged order must follow event_time (the 'f'*64 file's "
            "earlier row first), not glob/file-encounter order (the "
            "'0'*64 file sorts first alphabetically but is read second here)"
        )


class TestRoundNumberMismatch:
    def test_matching_sequence_is_not_a_mismatch(self):
        rows = [
            _ledger_row(round=1, disposition="DEFER"),
            _ledger_row(round=1, disposition="ADDRESS"),
            _ledger_row(round=2, disposition="DEFER"),
            _ledger_row(round=3, disposition="DEFER"),
        ]
        assert ao._round_number_mismatch(_entries(rows), round_open_count=3) is False

    def test_gap_in_sequence_is_a_mismatch(self):
        """Round 2 opened in the transcript but never got a ledger row --
        a gap between the ledger's own round 1 and round 3."""
        rows = [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=3, disposition="DEFER")]
        assert ao._round_number_mismatch(_entries(rows), round_open_count=3) is True

    def test_ledger_round_with_no_corresponding_open_is_a_mismatch(self):
        rows = [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=5, disposition="DEFER")]
        assert ao._round_number_mismatch(_entries(rows), round_open_count=1) is True

    def test_legacy_only_ledger_is_not_a_mismatch(self):
        """Every row predates the round field -- nothing to evaluate a
        sequence against, so this must not flag every pre-migration
        session as a mismatch."""
        rows = [_ledger_row(round=None, disposition="ADDRESS"), _ledger_row(round=None, disposition="DEFER")]
        assert ao._round_number_mismatch(_entries(rows), round_open_count=2) is False

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
        assert ao._round_number_mismatch(_entries(rows), round_open_count=3) is True

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
        assert ao._round_number_mismatch(_entries(rows), round_open_count=2) is True

    def test_zero_round_opens_with_a_round_keyed_row_is_a_mismatch(self):
        """round_open_count=0 (the transcript's own round-open detector
        found no code-review round at all) against a ledger that still
        carries a round-keyed row: contiguous_blocks is the non-empty
        [1], but the expected list(range(1, 1)) is empty, so this must
        resolve True rather than slip through as the legacy-only-ledger
        False case above."""
        rows = [_ledger_row(round=1, disposition="ADDRESS")]
        assert ao._round_number_mismatch(_entries(rows), round_open_count=0) is True

    def test_bool_round_only_ledger_is_not_a_mismatch(self):
        """round=True/False are JSON booleans, not round numbers -- Python's
        bool being an int subclass would otherwise let them slip into
        rounds_with_key as round 1/0. Excluded the same way a legacy
        round=None row is, so this must not flag a bool-only ledger as a
        mismatch."""
        rows = [_ledger_row(round=True, disposition="ADDRESS"), _ledger_row(round=False, disposition="DEFER")]
        assert ao._round_number_mismatch(_entries(rows), round_open_count=2) is False

    def test_worktree_switch_without_compaction_across_two_files_is_not_a_mismatch(self):
        """Rounds 1 and 2 both appended from one worktree's file (index 0),
        round 3 from a second worktree's file (index 1) -- a clean switch
        with no round number claimed by more than one file. This is the
        common non-buggy multi-file case and must not regress into a
        false-positive exclusion."""
        entries = [
            (0, _ledger_row(round=1, disposition="DEFER")),
            (0, _ledger_row(round=2, disposition="DEFER")),
            (1, _ledger_row(round=3, disposition="DEFER")),
        ]
        assert ao._round_number_mismatch(entries, round_open_count=3) is False

    def test_same_round_number_claimed_by_two_different_files_is_a_mismatch(self):
        """A worktree-subagent race: file 0 and file 1 each independently
        append their own row for round 1 -- there is no safe way to pick
        one file's row as authoritative, so this must fail closed even
        though each file's own round sequence looks fine in isolation."""
        entries = [
            (0, _ledger_row(round=1, disposition="DEFER")),
            (1, _ledger_row(round=1, disposition="ADDRESS")),
            (0, _ledger_row(round=2, disposition="DEFER")),
        ]
        assert ao._round_number_mismatch(entries, round_open_count=2) is True

    def test_same_file_reusing_a_round_non_contiguously_is_a_mismatch_by_true_identity(self):
        """Guards a regression that tagged file identity by each entry's
        own position in the merged list instead of its true source file:
        file 0 claims round 1, file 1's own unrelated round 2 sits between
        them, then file 0 claims round 1 again. By true file identity,
        round 1's file set is {0} alone -- not a two-file collision -- so
        this must resolve to a mismatch via the round-sequence check
        (round 1 reappearing after round 2 already appeared), the same
        path test_round_value_reappearing_after_a_later_round_is_a_mismatch
        pins for the single-file case."""
        entries = [
            (0, _ledger_row(round=1, disposition="DEFER")),
            (1, _ledger_row(round=2, disposition="DEFER")),
            (0, _ledger_row(round=1, disposition="ADDRESS")),
        ]
        assert ao._round_number_mismatch(entries, round_open_count=2) is True

    def test_round_shared_by_two_files_with_a_third_files_row_interleaved_is_a_mismatch(self):
        """The complementary case to the same-file reuse above: round 1 is
        genuinely claimed by two different files (0 and 1), with file 2's
        own unrelated round sitting between them so the two round-1 rows
        are non-adjacent in the merged order. Pins that the round-sequence
        check catches a cross-file collision across the whole blocks list,
        not just adjacent entries, for a 3-distinct-file merge."""
        entries = [
            (0, _ledger_row(round=1, disposition="ADDRESS")),
            (2, _ledger_row(round=5, disposition="DEFER")),
            (1, _ledger_row(round=1, disposition="DEFER")),
        ]
        assert ao._round_number_mismatch(entries, round_open_count=2) is True

    def test_round_shared_by_non_adjacent_files_zero_and_two_is_a_mismatch(self):
        """3 distinct file_index values, with the colliding round claimed
        by files 0 and 2 while file 1's own round sits strictly between
        them -- pins that the round-sequence check catches the collision
        across the whole blocks list, not only adjacent ones."""
        entries = [
            (0, _ledger_row(round=1, disposition="ADDRESS")),
            (1, _ledger_row(round=2, disposition="DEFER")),
            (2, _ledger_row(round=1, disposition="DEFER")),
        ]
        assert ao._round_number_mismatch(entries, round_open_count=2) is True

    def test_legacy_file_merged_with_schema_v2_file_is_not_a_mismatch(self):
        """A legacy-only file (file_index 0, no `round` key at all) merged
        with a schema-v2 file (file_index 1, real round-keyed rows) --
        the legacy rows must be filtered out before the sequence check
        runs, neither triggering nor suppressing the mismatch that the
        schema-v2 file's own valid 1..2 sequence would otherwise not
        have. Distinct from test_legacy_only_ledger_is_not_a_mismatch,
        which covers a single all-legacy file via _entries()'s always
        file_index=0 tagging."""
        entries = [
            (0, _ledger_row(round=None, disposition="DEFER")),
            (0, _ledger_row(round=None, disposition="ADDRESS")),
            (1, _ledger_row(round=1, disposition="DEFER")),
            (1, _ledger_row(round=2, disposition="DEFER")),
        ]
        assert ao._round_number_mismatch(entries, round_open_count=2) is False


class TestLedgerPossiblySwept:
    _OLD_RECORD_TS = "2026-08-01T10:00:00.000Z"
    _NOW_WELL_PAST_WINDOW = corpus._parse_ts("2026-09-15T10:00:00.000Z")  # 45 days after _OLD_RECORD_TS
    _NOW_WITHIN_WINDOW = corpus._parse_ts("2026-08-10T10:00:00.000Z")  # 9 days after _OLD_RECORD_TS

    def test_old_session_with_no_ledger_file_is_possibly_swept(self):
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept(
            [(0, 1)], [], records, any_ledger_file_found=False, now=self._NOW_WELL_PAST_WINDOW,
        ) is True

    def test_recent_session_with_no_ledger_file_is_not_possibly_swept(self):
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept(
            [(0, 1)], [], records, any_ledger_file_found=False, now=self._NOW_WITHIN_WINDOW,
        ) is False

    def test_widened_cleanup_period_days_does_not_delay_possibly_swept(self, tmp_path):
        """Mirrors test_review_ledger_script.py's
        test_append_sweep_ignores_a_widened_cleanup_period_days: this
        classifier must track append's fixed 30-day floor, not
        clear-stale's dynamic cleanupPeriodDays-widened window, so a
        session aged past the floor but still within a wider configured
        value is still flagged as possibly swept."""
        (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 60}))
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept(
            [(0, 1)], [], records, any_ledger_file_found=False, now=self._NOW_WELL_PAST_WINDOW,
        ) is True

    def test_no_code_review_rounds_is_never_possibly_swept(self):
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept(
            [], [], records, any_ledger_file_found=False, now=self._NOW_WELL_PAST_WINDOW,
        ) is False

    def test_nonempty_ledger_rows_is_never_possibly_swept(self):
        records = [{"timestamp": self._OLD_RECORD_TS}]
        rows = [_ledger_row(round=1, disposition="DEFER")]
        assert ao._ledger_possibly_swept(
            [(0, 1)], rows, records, any_ledger_file_found=True, now=self._NOW_WELL_PAST_WINDOW,
        ) is False

    def test_ledger_file_present_but_zero_rows_is_never_possibly_swept(self):
        """A ledger file that exists but is empty (or every line malformed)
        is a different failure mode from a swept/never-written ledger --
        this check keys on any_ledger_file_found, not the row count."""
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept(
            [(0, 1)], [], records, any_ledger_file_found=True, now=self._NOW_WELL_PAST_WINDOW,
        ) is False

    def test_no_parseable_timestamp_is_never_possibly_swept(self):
        records = [{"timestamp": None}, {}]
        assert ao._ledger_possibly_swept(
            [(0, 1)], [], records, any_ledger_file_found=False, now=self._NOW_WELL_PAST_WINDOW,
        ) is False

    def test_boundary_exactly_at_sweep_window_is_not_possibly_swept(self):
        """The earliest round-open's own timestamp == now -
        _LEDGER_SWEEP_FLOOR_DAYS * 86400 sits on the strict `<`
        inequality's excluded side, one second short of swept -- the
        fixed floor _ledger_possibly_swept actually compares against."""
        record_ts = corpus._parse_ts(self._OLD_RECORD_TS)
        now = record_ts + ao._LEDGER_SWEEP_FLOOR_DAYS * 86400
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept([(0, 1)], [], records, any_ledger_file_found=False, now=now) is False

    def test_boundary_one_second_past_sweep_window_is_possibly_swept(self):
        """One second older than the exact boundary above crosses onto
        the swept side of the same strict `<` inequality."""
        record_ts = corpus._parse_ts(self._OLD_RECORD_TS)
        now = record_ts + ao._LEDGER_SWEEP_FLOOR_DAYS * 86400 + 1
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept([(0, 1)], [], records, any_ledger_file_found=False, now=now) is True

    def test_now_omitted_defaults_to_the_real_clock(self, monkeypatch):
        """now=None (the default) falls back to the real time.time() call,
        confirmed here by monkeypatching it directly rather than passing an
        override."""
        monkeypatch.setattr(ao.time, "time", lambda: self._NOW_WELL_PAST_WINDOW)
        records = [{"timestamp": self._OLD_RECORD_TS}]
        assert ao._ledger_possibly_swept([(0, 1)], [], records, any_ledger_file_found=False) is True

    def test_fresh_newest_record_but_stale_earliest_round_open_is_possibly_swept(self):
        """The session's LAST record is fresh (well within the sweep
        window), but the record at its earliest code-review round's own
        open_idx is stale -- keying on the newest record instead (the
        prior behavior) would read this session as recent and miss it.
        This is the exact case Fix 2 changes behavior for."""
        records = [
            {"timestamp": self._OLD_RECORD_TS},  # round 1 opens here, index 0
            {"timestamp": "2026-09-14T10:00:00.000Z"},  # fresh, unrelated later activity
        ]
        assert ao._ledger_possibly_swept(
            [(0, 2)], [], records, any_ledger_file_found=False, now=self._NOW_WELL_PAST_WINDOW,
        ) is True


class TestLedgerSweepFloorDaysMatchesLibSh:
    def test_literal_floor_values_are_equal(self):
        """_LEDGER_SWEEP_FLOOR_DAYS is duplicated independently in _lib.sh
        and here -- an accepted small-duplicated-value exception since the
        two runtimes share no process -- but nothing else pins the two
        literals to the same value. This sources _lib.sh and reads its
        variable's actual runtime value (rather than regex-scanning the
        source text, which would pass even if a reformat left the
        assignment unparseable) and asserts equality, so a future edit to
        one side without the other fails CI instead of silently drifting
        the two languages' resolved sweep window apart."""
        result = subprocess.run(
            ["bash", "-c", f'. "{_LIB_SH}"; printf %s "$_LEDGER_SWEEP_FLOOR_DAYS"'],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert int(result.stdout) == ao._LEDGER_SWEEP_FLOOR_DAYS


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

    def test_bool_round_value_never_matches_round_ordinal_via_equality(self):
        """round=True == 1 in Python (bool is an int subclass), so without
        the isinstance(..., bool) guard this row would wrongly match
        round_ordinal=1 via plain equality. Distinct from
        test_legacy_row_without_round_key_never_matches_any_round above:
        that case relies on None != int, already false without any guard,
        while this one relies on the explicit bool exclusion."""
        data_quality = ao.Counter({key: 0 for key in ao._DATA_QUALITY_KEYS})
        rows = [_ledger_row(round=True, disposition="ADDRESS")]
        classification, matching = ao._classify_round(1, rows, has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_UNATTRIBUTED
        assert matching == []


class TestAgentDispatchToolUseIds:
    def test_sidechain_dispatch_is_excluded_but_main_thread_dispatch_is_found(self):
        """isSidechain assistant records are a subagent's own transcript
        entries replayed into the main session file -- a dispatch tool_use
        there is not this session's own dispatch and must not appear in
        the result."""
        data_quality = ao.Counter()
        records = [
            _asst("claude-sonnet-5", sidechain=True, content=[_agent_use("side-1", "code-writer")]),
            _asst("claude-sonnet-5", content=[_agent_use("main-1", "code-writer")]),
        ]
        result = ao._agent_dispatch_tool_use_ids(records, "code-writer", data_quality)
        assert result == [("main-1", 1)]


class TestBuildToolResultIndexMap:
    def test_sidechain_tool_result_is_excluded_but_main_thread_one_is_found(self):
        """isSidechain user records are a subagent's own transcript entries
        replayed into the main session file -- a tool_result there belongs
        to the subagent's own dispatch bookkeeping, not this session's own
        completion-index map."""
        sidechain_result = _user_msg([_tool_result("side-1", "done")])
        sidechain_result["isSidechain"] = True
        records = [sidechain_result, _user_msg([_tool_result("main-1", "done")])]
        assert ao._build_tool_result_index_map(records) == {"main-1": 1}


class TestReviewLedgerSubprocessIntegration:
    """Runs the real review-ledger.sh append subprocess and reads it back
    through the same reader functions other tests exercise only against a
    hand-maintained fixture, pinning writer/reader schema agreement by
    actual execution instead of two independently maintained copies."""

    SESSION_ID = "subprocess-integration-session"

    def _make_git_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "file.txt").write_text("first\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        return repo

    def _run_append(self, args: list[str], *, cwd: Path, home: Path) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(home)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        return subprocess.run(
            ["bash", str(_REVIEW_LEDGER_SH), "append", "code-review", *args],
            cwd=cwd, env=env, capture_output=True, text=True,
        )

    def _fake_transcript_path(self, home: Path) -> Path:
        # _read_ledger_row_entries_for_session only reads jsonl.stem (the
        # session id) and walks three parents up to the config-dir root -- see
        # _config_dir_root_for_session's own docstring for that resolution.
        # A placeholder transcript path with the right stem and depth under
        # $HOME/.claude is therefore enough.
        # The real ledger file is found by review-ledger.sh's own
        # $CONFIG_DIR/review-narrative-ledger glob, not by this path itself.
        return home / ".claude" / "projects" / "-fake-project" / f"{self.SESSION_ID}.jsonl"

    def test_real_address_append_classifies_as_failure(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        repo = self._make_git_repo(tmp_path)
        _seed_session(home, self.SESSION_ID)

        result = self._run_append(
            [
                "--finding", "Missing error handling in foo()",
                "--disposition", "ADDRESS",
                "--rationale", "fixed inline",
                "--round", "1",
                "--authoring-agent", "code-writer",
            ],
            cwd=repo, home=home,
        )
        assert result.returncode == 0, result.stderr

        jsonl = self._fake_transcript_path(home)
        entries, any_file_found = ao._read_ledger_row_entries_for_session(jsonl)
        rows = [row for _file_index, row in entries]
        assert len(rows) == 1
        assert any_file_found is True
        data_quality = ao.Counter({key: 0 for key in ao._DATA_QUALITY_KEYS})
        classification, matching = ao._classify_round(1, rows, has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_FAILURE
        assert matching == rows

    def test_real_clean_append_classifies_as_pass(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        repo = self._make_git_repo(tmp_path)
        _seed_session(home, self.SESSION_ID)

        result = self._run_append(["--disposition", "CLEAN", "--round", "1"], cwd=repo, home=home)
        assert result.returncode == 0, result.stderr

        jsonl = self._fake_transcript_path(home)
        entries, any_file_found = ao._read_ledger_row_entries_for_session(jsonl)
        rows = [row for _file_index, row in entries]
        assert len(rows) == 1
        assert any_file_found is True
        data_quality = ao.Counter({key: 0 for key in ao._DATA_QUALITY_KEYS})
        classification, _matching = ao._classify_round(1, rows, has_marker_write=False, data_quality=data_quality)
        assert classification == ao._OUTCOME_PASS


class TestComputeAuthorOutcomesLedgerFilesResolution:
    """compute_author_outcomes must resolve each session's ledger files
    exactly once and reuse the result for the round-number-mismatch check,
    the sweep check, and every round's own classification, not glob for it
    once per lookup. The resolution must not be cached across sessions: a
    session seen later in the same run still gets its own fresh
    resolution, not one read through an earlier snapshot."""

    def test_ledger_files_for_session_is_called_once_per_session(self, fake_projects, monkeypatch):
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        real_ledger_files_for_session = ao._ledger_files_for_session
        call_count = 0

        def _counting_ledger_files_for_session(jsonl):
            nonlocal call_count
            call_count += 1
            return real_ledger_files_for_session(jsonl)

        monkeypatch.setattr(ao, "_ledger_files_for_session", _counting_ledger_files_for_session)
        ao.compute_author_outcomes(_session_iter(fake_projects))
        assert call_count == 1, f"expected one glob for sess-1's ledger files, got {call_count}"

    def test_second_sessions_ledger_file_created_between_sessions_is_still_seen(self, fake_projects, monkeypatch):
        """A corpus-wide index memoized once before the loop starts would
        show sess-2 as ledger-less, since its ledger file doesn't exist yet
        at that point. That's the round-1 bug the single-session test above
        already guards against. A snapshot-based resolution and a
        resolved-fresh one only disagree once a second session's ledger
        file appears after the first session is processed, which needs two
        sessions to reproduce."""
        session_1, session_2 = "sess-1", "sess-2"
        for session_id in (session_1, session_2):
            _write_jsonl(fake_projects / f"{session_id}.jsonl", [
                _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
                _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
                _asst(
                    "claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z",
                    content=[_skill_block("s1", "code-review")],
                ),
                _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
            ])
        real_ledger_files_for_session = ao._ledger_files_for_session

        def _write_session_2_ledger_once_session_1_resolves(jsonl):
            resolved = real_ledger_files_for_session(jsonl)
            if jsonl.stem == session_1:
                _seed_ledger(fake_projects, session_2, [_ledger_row(round=1, disposition="ADDRESS")])
            return resolved

        monkeypatch.setattr(ao, "_ledger_files_for_session", _write_session_2_ledger_once_session_1_resolves)
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 1


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
        own marker.sh write code-review call still ran. now= is pinned
        well inside the sweep window, distinct from
        TestLedgerPossiblySweptIntegration's own old-session case below.
        Pinning it is required: this test's fixture dates are hardcoded to
        2026-08-01, so leaving now= at its real-clock default would flake
        once wall-clock time passes the 30-day sweep window from that date."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:10.000Z", content=[_marker_write_use("m1")]),
        ])
        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-08-01T11:00:00.000Z"),
        )
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["data_quality"][ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 1

    def test_dispatch_is_unattributed_when_no_ledger_row_and_no_marker_write(self, fake_projects):
        """now= is pinned well inside the sweep window, distinct from
        TestLedgerPossiblySweptIntegration's own old-session case below.
        Pinning it is required: this test's fixture dates are hardcoded to
        2026-08-01, so leaving now= at its real-clock default would flake
        once wall-clock time passes the 30-day sweep window from that date."""
        session_id = "sess-1"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-08-01T11:00:00.000Z"),
        )
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

    def test_dispatch_with_missing_tool_use_id_is_excluded_and_counted(self, fake_projects):
        """A malformed dispatch block with no `id` can't be paired to a
        tool_result, so it must not silently vanish from dispatch counting --
        it needs its own data-quality counter, distinct from _DQ_UNDECIDABLE
        (which tracks a well-formed dispatch missing its completion)."""
        session_id = "sess-1"
        malformed_dispatch = _agent_use("", "code-writer")
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[malformed_dispatch]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert sum(result["outcomes"].values()) == 0
        assert result["data_quality"][ao._DQ_MALFORMED_DISPATCH_ID] == 1
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 0
        # data_quality is a Counter, which accepts any key -- pin the bucket
        # into the tuple the printed report actually iterates over.
        assert ao._DQ_MALFORMED_DISPATCH_ID in ao._DATA_QUALITY_KEYS


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

    def test_co_authored_rounds_counter_uses_since_filtered_dispatch_count(self, fake_projects):
        """_DQ_CO_AUTHORED_ROUNDS reads dispatch_count (the --since-filtered
        count), unlike the authoring_agent inconsistency check above, which
        reads unfiltered_dispatch_count -- deliberate, since co-authored
        measures fan-in to the headline in-scope aggregate rather than the
        ledger's own unfiltered declaration. a1 falls outside the --since
        cutoff and a2 falls inside it, both attributed to round 1: the round
        has two dispatches transcript-side but only one in scope, so it must
        not count as co-authored."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS", authoring_agent="code-writer")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T09:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T09:00:10.000Z"),
            _dispatch_start("a2", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a2", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        since_ts = corpus._parse_ts("2026-08-01T09:30:00.000Z")
        result = ao.compute_author_outcomes(_session_iter(fake_projects), since_ts=since_ts)
        assert result["data_quality"][ao._DQ_CO_AUTHORED_ROUNDS] == 0
        assert result["data_quality"][ao._DQ_AUTHORING_AGENT_INCONSISTENT] == 0

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

    def test_zero_round_opens_with_a_round_keyed_ledger_excludes_the_unresolved_dispatch(
        self, fake_projects,
    ):
        """A session whose ledger carries a round-keyed row but whose own
        transcript never opens a code-review round at all
        (round_open_count=0) is a round-number mismatch too. Without the
        exclusion, the session's one completed dispatch would classify
        UNRESOLVED (no code-review round ever opens after it completes)
        and silently pollute the headline aggregate -- the mismatch
        exclusion drops it instead."""
        session_id = "sess-zero-round-opens"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        # a1 would otherwise classify UNRESOLVED (no code-review round
        # ever opens in this transcript) -- excluded by the mismatch instead.
        assert sum(result["outcomes"].values()) == 0
        assert result["outcomes"][ao._OUTCOME_UNRESOLVED] == 0

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

    def test_mismatched_session_still_increments_kill_switch_inferred_clean(self, fake_projects):
        """_DQ_KILL_SWITCH_INFERRED_CLEAN measures a transcript-side fact
        (a round's own marker write with no matching ledger row), not the
        ledger/round join session_round_mismatch guards. It must still
        increment for a round inside a mismatched session, unlike the
        headline outcomes counters, which this same session's mismatch
        excludes. Mirrors TestLedgerPossiblySweptIntegration's own
        test_old_session_with_no_ledger_file_excludes_dispatch_and_increments_counter,
        which pins the analogous interaction for the possibly-swept
        exclusion."""
        session_id = "sess-mismatched-with-inferred-clean-round"
        _seed_ledger(fake_projects, session_id, [
            _ledger_row(round=1, disposition="ADDRESS"),
            _ledger_row(round=3, disposition="DEFER"),
        ])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s2", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:10.000Z", content=[_marker_write_use("m1")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:02:00.000Z", content=[_skill_block("s3", "code-review")]),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))

        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        # Round 2 has no matching ledger row (the ledger only carries
        # rounds 1 and 3), but its own marker write still lets the
        # kill-switch-inferred-clean fallback fire.
        assert result["data_quality"][ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 1


class TestMultiWorktreeLedgerMerge:
    """A session spanning more than one git worktree of the same repo
    writes one ledger file per repo-hash under the same session id.
    compute_author_outcomes merges every matching file (see
    _read_ledger_row_entries_for_session) rather than reading only the
    sorted-first one."""

    def test_worktree_switch_without_compaction_classifies_every_round_correctly(self, fake_projects):
        """Rounds 1 and 2 both appended from one worktree's ledger file,
        round 3 from a second worktree's file after a mid-session worktree
        switch -- no round number is claimed by more than one file, so
        this must not be flagged as a mismatch. This is the common
        non-buggy multi-file case."""
        session_id = "sess-multi-worktree-clean-switch"
        config_dir_root = _config_dir_root(fake_projects)
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=1, disposition="DEFER"), _ledger_row(round=2, disposition="DEFER")],
            repo_hash="0" * 64,
        )
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=3, disposition="DEFER")],
            repo_hash="1" * 64,
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:00:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s2", "code-review")]),
            _dispatch_start("a1", "2026-08-01T10:02:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:02:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:03:00.000Z", content=[_skill_block("s3", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:04:00.000Z"),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 0
        # a1 attributes to round 3 (the second file's own DEFER row) -- PASS.
        assert result["outcomes"][ao._OUTCOME_PASS] == 1
        assert result["outcomes"][ao._OUTCOME_FAILURE] == 0

    def test_same_round_number_claimed_by_two_worktrees_is_excluded_as_a_mismatch(self, fake_projects):
        """A worktree-subagent race: the parent session's own worktree
        (repo_hash "0"*64) and a subagent dispatched into a different
        worktree under the same parent session id (repo_hash "1"*64) each
        independently append their own row for round 1. Neither file's own
        round 1 row is safe to treat as authoritative, so the session's
        dispatches must be excluded from the headline aggregate instead of
        silently keeping one file's version."""
        session_id = "sess-multi-worktree-interleaved"
        config_dir_root = _config_dir_root(fake_projects)
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=1, disposition="ADDRESS")],
            repo_hash="0" * 64,
        )
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=1, disposition="DEFER")],
            repo_hash="1" * 64,
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        # a1's attributed round (round 1) would otherwise be a FAILURE (the
        # "0"*64 file's own ADDRESS row) -- excluded by the mismatch instead.
        assert sum(result["outcomes"].values()) == 0

    def test_one_worktrees_ledger_file_already_swept_still_reads_the_survivor_without_crashing(
        self, fake_projects,
    ):
        """One of the session's two ledger files has already been evicted
        by retention sweep -- only the survivor matches the glob. This must
        not crash, and (since the survivor alone shows a gap against the
        transcript's own two round-opens) fails closed via the existing
        round-number-mismatch exclusion rather than misclassifying the
        session as clean."""
        session_id = "sess-multi-worktree-partial-sweep"
        config_dir_root = _config_dir_root(fake_projects)
        _write_ledger_file(
            config_dir_root, session_id,
            [_ledger_row(round=1, disposition="ADDRESS")],
            repo_hash="0" * 64,
        )
        # The "1"*64 file that used to cover round 2 has already been
        # swept -- deliberately not written here.
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:02:00.000Z", content=[_skill_block("s2", "code-review")]),
        ])

        result = ao.compute_author_outcomes(_session_iter(fake_projects))
        assert result["data_quality"][ao._DQ_ROUND_NUMBER_MISMATCH] == 1
        assert sum(result["outcomes"].values()) == 0


class TestLedgerPossiblySweptIntegration:
    def test_old_session_with_no_ledger_file_excludes_dispatch_and_increments_counter(
        self, fake_projects,
    ):
        """No ledger file exists at all, and the session's newest record is
        well past the 30-day sweep window. That is indistinguishable from a
        genuinely swept ledger, so the dispatch is excluded from the
        headline outcomes. The round's own marker write still lets the
        kill-switch-inferred-clean counter increment, since that counter
        measures a transcript-side fact this exclusion doesn't gate."""
        session_id = "sess-old-no-ledger"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:10.000Z", content=[_marker_write_use("m1")]),
        ])

        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-09-15T10:00:00.000Z"),
        )

        assert result["data_quality"][ao._DQ_LEDGER_POSSIBLY_SWEPT] == 1
        assert result["data_quality"][ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 1
        assert sum(result["outcomes"].values()) == 0

    def test_recent_session_with_no_ledger_file_is_unaffected(self, fake_projects):
        """Same shape as the old-session case above, but the session's
        newest record is recent -- the existing kill-switch-inferred-clean
        PASS behavior must be unaffected by this exclusion."""
        session_id = "sess-recent-no-ledger"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:10.000Z", content=[_marker_write_use("m1")]),
        ])

        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-08-01T11:00:00.000Z"),
        )

        assert result["data_quality"][ao._DQ_LEDGER_POSSIBLY_SWEPT] == 0
        assert result["data_quality"][ao._DQ_KILL_SWITCH_INFERRED_CLEAN] == 1
        assert result["outcomes"][ao._OUTCOME_PASS] == 1

    def test_ledger_file_present_is_never_flagged_regardless_of_age(self, fake_projects):
        """A ledger file exists for this old session. Its one row is legacy
        (no `round` key), so it never matches round 1. This check keys on
        the file's own presence, not the usability of its rows, so it must
        never flag this session no matter how cold its newest record is."""
        session_id = "sess-old-with-ledger-file"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=None, disposition="DEFER")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])

        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-09-15T10:00:00.000Z"),
        )

        assert result["data_quality"][ao._DQ_LEDGER_POSSIBLY_SWEPT] == 0
        assert result["outcomes"][ao._OUTCOME_UNATTRIBUTED] == 1

    def test_possibly_swept_session_still_increments_transcript_side_dq_counters(
        self, fake_projects,
    ):
        """_DQ_CO_AUTHORED_ROUNDS and _DQ_UNDECIDABLE measure transcript-side
        dispatch-to-round attribution, not the ledger-possibly-swept
        exclusion above. Both must still increment for a possibly-swept
        session, unlike the headline outcomes counters, which this same
        session's exclusion removes entirely. Mirrors
        TestRoundNumberMismatchIntegration's own
        test_mismatched_session_still_increments_transcript_side_dq_counters
        for the analogous round-number-mismatch exclusion."""
        session_id = "sess-swept-with-quality-issues"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:10.000Z"),
            _dispatch_start("a2", "2026-08-01T10:01:00.000Z"),
            _dispatch_complete("a2", "2026-08-01T10:01:10.000Z"),
            _dispatch_start("a3", "2026-08-01T10:02:00.000Z"),  # never completes
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:03:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])

        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-09-15T10:00:00.000Z"),
        )

        assert result["data_quality"][ao._DQ_LEDGER_POSSIBLY_SWEPT] == 1
        # a1 and a2 both complete before the session's one round-open and
        # attribute to it, co-authoring round 1 despite the exclusion above.
        assert result["data_quality"][ao._DQ_CO_AUTHORED_ROUNDS] == 1
        # a3 never gets a paired tool_result, so it's undecidable regardless
        # of the exclusion above.
        assert result["data_quality"][ao._DQ_UNDECIDABLE] == 1

    def test_ledger_file_vanishing_between_glob_and_open_is_possibly_swept(
        self, fake_projects, monkeypatch,
    ):
        """Before _read_ledger_row_entries_for_session distinguished a
        failed open() from a successful one, any_ledger_file_found stayed
        True purely from the glob snapshot, so a ledger file evicted by a
        concurrent review-ledger.sh clear-stale sweep between the glob and
        the read silently read as 'found' and this old session was never
        flagged possibly-swept. Simulated by stubbing the glob to return a
        path that was never created."""
        session_id = "sess-vanished-ledger"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
        ])
        vanished_path = (
            _config_dir_root(fake_projects) / "review-narrative-ledger" / ("a" * 64 + f".{session_id}.jsonl")
        )
        monkeypatch.setattr(ao, "_ledger_files_for_session", lambda _jsonl: [vanished_path])

        result = ao.compute_author_outcomes(
            _session_iter(fake_projects), now=corpus._parse_ts("2026-09-15T10:00:00.000Z"),
        )

        assert result["data_quality"][ao._DQ_LEDGER_POSSIBLY_SWEPT] == 1


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

    def test_non_default_agent_reaches_compute_and_the_printed_header(self, fake_projects, capsys):
        """args.agent must actually reach compute_author_outcomes (not just
        the parser default) and the printed header -- a staff-sdet dispatch
        is classified as this round's FAILURE, and the header prints
        agent=staff-sdet rather than the code-writer default."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", agent_type="staff-sdet"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args(agent="staff-sdet"))
        out = capsys.readouterr().out
        assert "agent=staff-sdet  window=all time" in out
        assert "Failure share: 1 of 1 resolved dispatches (100.0%)" in out

    def test_agent_inline_is_rejected_as_a_reserved_sentinel(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _mod.cmd_author_outcome(self._args(agent=ao._AUTHORING_AGENT_INLINE))
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "reserved sentinel" in err

    def test_agent_mixed_is_rejected_as_a_reserved_sentinel(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _mod.cmd_author_outcome(self._args(agent=ao._AUTHORING_AGENT_MIXED))
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "reserved sentinel" in err
        assert ao._AUTHORING_AGENT_MIXED in err

    def test_agent_unknown_is_rejected_as_a_reserved_sentinel(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _mod.cmd_author_outcome(self._args(agent=ao._AUTHORING_AGENT_UNKNOWN))
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "reserved sentinel" in err
        assert ao._AUTHORING_AGENT_UNKNOWN in err

    def test_agent_near_miss_case_is_accepted_not_rejected_as_a_reserved_sentinel(self, fake_projects, capsys):
        """A cased near-miss of a sentinel (e.g. "Mixed") is not the sentinel
        itself -- the reserved-sentinel guard is exact-case, so it must not
        raise SystemExit."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", agent_type="Mixed"),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args(agent="Mixed"))
        out = capsys.readouterr().out
        assert "agent=Mixed  window=all time" in out
        assert "Failure share: 1 of 1 resolved dispatches (100.0%)" in out

    def test_agent_empty_string_passes_through_not_remapped_to_default(self, fake_projects, capsys):
        """--agent "" is a literal empty string, not the code-writer default
        -- an explicit empty value must not be silently remapped."""
        session_id = "sess-1"
        _seed_ledger(fake_projects, session_id, [_ledger_row(round=1, disposition="ADDRESS")])
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _dispatch_start("a1", "2026-08-01T10:00:00.000Z", agent_type=""),
            _dispatch_complete("a1", "2026-08-01T10:00:30.000Z"),
            _asst("claude-sonnet-5", branch="feat", ts="2026-08-01T10:01:00.000Z", content=[_skill_block("s1", "code-review")]),
            _user_msg("thanks", branch="feat", ts="2026-08-01T10:02:00.000Z"),
        ])
        _mod.cmd_author_outcome(self._args(agent=""))
        out = capsys.readouterr().out
        assert "agent=  window=all time" in out
        assert "Failure share: 1 of 1 resolved dispatches (100.0%)" in out

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
