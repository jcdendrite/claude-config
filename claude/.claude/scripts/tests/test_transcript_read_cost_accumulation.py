"""Tests for read_cost's per-agent-type accumulation, billed weight, size buckets and untracked-type fold."""
import dataclasses
from collections import Counter

import pytest
from transcript_analysis import read_cost

from ._read_cost_helpers import (
    _HUNDRED_TOKEN_TEXT,
    _TRACKED_AGENT_TYPE,
    _UNTRACKED_AGENT_TYPE,
    _read_exchange,
    _scan,
    _sidechain_copies,
    _turn,
    _weights,
)
from .conftest import (
    _compact_boundary_rec,
    _edit_use,
    _read_result,
    _read_use,
)

_CACHE_WRITE_WEIGHT = _weights()["cache_write_5m"]


def _row_with_every_field_set(value: int) -> read_cost._AgentTypeRow:
    row = read_cost._AgentTypeRow()
    for row_field in dataclasses.fields(row):
        default = getattr(row, row_field.name)
        if isinstance(default, Counter):
            setattr(row, row_field.name, Counter({read_cost._KIND_INSTRUCTION: value}))
        else:
            setattr(row, row_field.name, type(default)(value))
    return row


class TestBilledWeight:
    # The literals are pricing._cache_read_equivalent_weights ratios; _CACHE_WRITE_WEIGHT is derived from the same function.
    def test_usage_is_weighted_per_token_class(self):
        usage = {
            "input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3,
            "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 5},
        }
        assert read_cost._billed_weight(usage, _weights()) == pytest.approx(
            1 * 10 + 2 * 50 + 3 * 1 + 4 * _CACHE_WRITE_WEIGHT + 5 * 20
        )

    def test_flat_cache_creation_field_is_priced_as_five_minute_writes(self):
        usage = {"cache_creation_input_tokens": 4}
        assert read_cost._billed_weight(usage, _weights()) == pytest.approx(4 * _CACHE_WRITE_WEIGHT)

    def test_usage_already_counted_in_another_window_is_billed_once(self):
        scan = _scan([_turn("m1", usage={"output_tokens": 1}), _turn("m2", usage={"output_tokens": 1})])
        seen: set[str] = {"m1"}
        billed, calls = read_cost._new_billed_weight(scan, seen, _weights())
        assert (billed, calls) == (pytest.approx(50.0), 1)
        assert read_cost._new_billed_weight(scan, seen, _weights()) == (0.0, 0)


class TestAgentTypeRow:
    def _row(self, records: list[dict], session_paths: set[str]) -> tuple[read_cost._AgentTypeRow, set[str]]:
        scan = _scan(records)
        return read_cost._agent_type_row(scan, len(scan.turn_message_ids), 0.0, _weights(), session_paths)

    def test_second_whole_file_read_of_a_path_in_the_same_window_is_a_repeat(self):
        row, read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2", [_read_use("r2", file_path="/a.py")]),
            _read_result("r2", _HUNDRED_TOKEN_TEXT),
        ], set())
        assert row.repeat_in_agent_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)
        assert row.seen_elsewhere_weight == 0.0
        assert read_paths == {"/a.py"}

    def test_path_in_the_session_set_is_seen_elsewhere_not_a_repeat(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ], {"/a.py"})
        assert row.seen_elsewhere_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)
        assert row.repeat_in_agent_weight == 0.0

    def test_large_unedited_read_counts_as_large_and_large_unedited(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * (4 * read_cost._LARGE_READ_TOKENS)),
        ], set())
        assert row.large_weight == row.large_unedited_weight == pytest.approx(read_cost._LARGE_READ_TOKENS * _CACHE_WRITE_WEIGHT)

    def test_large_edited_read_counts_as_large_but_not_large_unedited(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py"), _edit_use("e1", path="/a.py")]),
            _read_result("r1", "x" * (4 * read_cost._LARGE_READ_TOKENS)),
        ], set())
        assert row.large_weight == pytest.approx(read_cost._LARGE_READ_TOKENS * _CACHE_WRITE_WEIGHT)
        assert row.large_unedited_weight == 0.0

    def test_partial_read_counts_toward_all_reads_but_not_whole_file(self):
        row, read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py", limit=5)]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ], set())
        assert row.all_read_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)
        assert (row.whole_file_reads, row.whole_file_weight) == (0, 0.0)
        assert read_paths == set()

    def test_remaining_turns_add_one_cache_read_each_to_the_weight(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2"),
            _turn("m3"),
        ], set())
        assert row.whole_file_weight == pytest.approx(100 * (_CACHE_WRITE_WEIGHT + 2))

    def test_path_in_the_session_set_and_read_twice_is_seen_elsewhere_once_and_a_repeat_once(self):
        row, _read_paths = self._row([
            *_read_exchange("m1", "r1", "/a.py"),
            *_read_exchange("m2", "r2", "/a.py"),
        ], {"/a.py"})
        # The first read has one later turn (m2), the second none.
        assert row.seen_elsewhere_weight == pytest.approx(100 * (_CACHE_WRITE_WEIGHT + 1))
        assert row.repeat_in_agent_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)

    def test_reread_after_an_edit_of_the_path_is_a_repeat_and_the_reread_reads_as_unedited(self):
        records = [
            *_read_exchange("m1", "r1", "/a.py"),
            _turn("m2", [_edit_use("e1", path="/a.py")]),
            *_read_exchange("m3", "r2", "/a.py"),
        ]
        row, _read_paths = self._row(records, set())
        assert [event.edited_later for event in _scan(records).events] == [True, False]
        assert row.repeat_in_agent_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)

    def test_reread_after_a_compaction_is_a_repeat(self):
        row, _read_paths = self._row([
            *_read_exchange("m1", "r1", "/a.py"),
            _compact_boundary_rec(),
            *_read_exchange("m2", "r2", "/a.py"),
        ], set())
        assert row.repeat_in_agent_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)

    def test_whole_file_read_one_token_under_the_threshold_is_not_large(self):
        row, _read_paths = self._row(_read_exchange(
            "m1", "r1", "/a.py", text="x" * (4 * (read_cost._LARGE_READ_TOKENS - 1)),
        ), set())
        assert row.large_weight == row.large_unedited_weight == 0.0
        assert row.whole_file_weight == pytest.approx((read_cost._LARGE_READ_TOKENS - 1) * _CACHE_WRITE_WEIGHT)

    def test_read_of_a_character_count_that_floors_to_one_token_under_the_threshold_is_not_large(self):
        row, _read_paths = self._row(_read_exchange("m1", "r1", "/a.py", text="x" * 7999), set())
        assert row.large_weight == 0.0
        assert row.whole_file_tokens == 1999

    def test_large_weight_carries_only_the_read_at_the_threshold_when_a_read_just_under_it_precedes(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_use("r1", file_path="/a.py"), _read_use("r2", file_path="/b.py")]),
            _read_result("r1", "x" * (4 * (read_cost._LARGE_READ_TOKENS - 1))),
            _read_result("r2", "x" * (4 * read_cost._LARGE_READ_TOKENS)),
        ], set())
        assert row.large_weight == pytest.approx(read_cost._LARGE_READ_TOKENS * _CACHE_WRITE_WEIGHT)
        assert row.whole_file_weight == pytest.approx((2 * read_cost._LARGE_READ_TOKENS - 1) * _CACHE_WRITE_WEIGHT)

    def test_kind_weight_sums_whole_file_read_weight_per_kind_and_ignores_partial_reads(self):
        row, _read_paths = self._row([
            _turn("m1", [
                _read_use("r1", file_path="/repo/CLAUDE.md"),
                _read_use("r2", file_path="/repo/docs/guide.md"),
                _read_use("r3", file_path="/repo/src/app.py"),
                _read_use("r4", file_path="/repo/.claude/rules/style.md", limit=5),
            ]),
            _read_result("r1", "x" * (4 * 100)),
            _read_result("r2", "x" * (4 * 200)),
            _read_result("r3", "x" * (4 * 300)),
            _read_result("r4", "x" * (4 * 500)),
        ], set())
        assert row.kind_weight == pytest.approx({
            read_cost._KIND_INSTRUCTION: 100 * _CACHE_WRITE_WEIGHT,
            read_cost._KIND_OTHER_PROSE: 200 * _CACHE_WRITE_WEIGHT,
            read_cost._KIND_CODE_OR_OTHER: 300 * _CACHE_WRITE_WEIGHT,
        })

    def test_merge_adds_kind_weight_across_rows_with_overlapping_kinds(self):
        row = read_cost._AgentTypeRow(kind_weight=Counter({
            read_cost._KIND_INSTRUCTION: 10.0, read_cost._KIND_OTHER_PROSE: 1.0,
        }))
        row.merge(read_cost._AgentTypeRow(kind_weight=Counter({
            read_cost._KIND_INSTRUCTION: 5.0, read_cost._KIND_CODE_OR_OTHER: 2.0,
        })))
        assert row.kind_weight == {
            read_cost._KIND_INSTRUCTION: 15.0, read_cost._KIND_OTHER_PROSE: 1.0, read_cost._KIND_CODE_OR_OTHER: 2.0,
        }

    def test_merge_sums_every_field_of_two_rows_that_have_every_field_set(self):
        row = _row_with_every_field_set(1)
        row.merge(_row_with_every_field_set(2))
        for row_field in dataclasses.fields(row):
            expected = Counter({read_cost._KIND_INSTRUCTION: 3}) if row_field.name == "kind_weight" else 3
            assert getattr(row, row_field.name) == expected, row_field.name

    @pytest.mark.parametrize(("path", "kind"), [
        ("/repo/.claude/skills/x/SKILL.md", read_cost._KIND_INSTRUCTION),
        ("/repo/.claude/agents/reviewer.md", read_cost._KIND_INSTRUCTION),
        ("/repo/.claude/rules/style.md", read_cost._KIND_INSTRUCTION),
        ("/repo/CLAUDE.md", read_cost._KIND_INSTRUCTION),
        ("/repo/.claude/skills/x/run.py", read_cost._KIND_INSTRUCTION),
        # Kind is read from path fragments, so a code file under any rules/ directory reads as instruction (the
        # kind definition in docs/transcript-analysis.md's read-cost Output bullet).
        ("/repo/src/rules/engine.py", read_cost._KIND_INSTRUCTION),
        ("/repo/docs/myclaude.md", read_cost._KIND_OTHER_PROSE),
        ("/repo/docs/guide.md", read_cost._KIND_OTHER_PROSE),
        ("/repo/docs/guide.mdx", read_cost._KIND_OTHER_PROSE),
        ("/repo/docs/notes.txt", read_cost._KIND_OTHER_PROSE),
        ("/repo/src/app.py", read_cost._KIND_CODE_OR_OTHER),
    ])
    def test_file_kind_splits_instruction_prose_and_code(self, path, kind):
        assert read_cost._file_kind(path) == kind


class TestSizeBucket:
    @pytest.mark.parametrize(("tokens", "label"), [
        (0, "<2000"),
        (1999, "<2000"),
        (2000, "2000-4999"),
        (4999, "2000-4999"),
        (5000, "5000+"),
    ])
    def test_bucket_boundaries_are_inclusive_at_the_lower_bound(self, tokens, label):
        assert read_cost._size_bucket(tokens) == label


class TestAccumulateSession:
    def test_main_file_sidechain_record_is_dropped_and_contributes_nothing(self):
        sidechain_records = _sidechain_copies([
            _turn("m-side", [_read_use("r1", file_path="/a.py")], usage={"output_tokens": 5}),
            _read_result("r1", "x" * 400),
        ])
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            sidechain_records, [], _weights(),
        )
        assert totals.main_whole_file_events == []
        assert totals.billed_main == 0
        assert totals.billed_subagent == 0
        assert totals.api_calls == 0

    def test_subagent_window_of_sidechain_flagged_records_still_counts_toward_every_subagent_figure(self):
        weights = _weights()
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [_turn("m1", usage={"output_tokens": 2})],
            [(_TRACKED_AGENT_TYPE, _sidechain_copies([
                _turn("s1", [_read_use("r1", file_path="/a.py")], usage={"output_tokens": 1}),
                _read_result("r1", _HUNDRED_TOKEN_TEXT),
                _turn("s2", usage={"output_tokens": 1}),
            ]))],
            weights,
        )
        row = totals.rows_by_agent_type[_TRACKED_AGENT_TYPE]
        assert row.agents == 1
        assert row.turns == 2
        assert row.billed == pytest.approx(2 * weights["output"])
        assert row.whole_file_reads == 1
        assert row.all_read_weight == pytest.approx(100 * (weights["cache_write_5m"] + weights["cache_read"]))
        assert totals.billed_subagent == pytest.approx(row.billed)
        assert totals.bucket_calls[(read_cost._SCOPE_SUBAGENT, "<2000")] == 1

    def test_subagent_windows_are_pulled_one_at_a_time_and_each_is_accumulated_before_the_next_is_pulled(self):
        totals = read_cost._ReadCostTotals()
        agents_accumulated_when_each_window_was_pulled: list[int] = []

        def windows_recording_progress():
            for window_index in range(3):
                agents_accumulated_when_each_window_was_pulled.append(
                    sum(row.agents for row in totals.rows_by_agent_type.values()),
                )
                yield _TRACKED_AGENT_TYPE, [_turn(f"s{window_index}")]

        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [_turn("m1")], windows_recording_progress(), _weights(),
        )

        assert agents_accumulated_when_each_window_was_pulled == [0, 1, 2]
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].agents == 3

    def test_retained_main_thread_events_carry_no_path(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), _read_exchange("m1", "r1", "/sentinel/a.py"), [], _weights(),
        )
        assert [event.path for event in totals.main_whole_file_events] == [""]

    def test_bucket_tables_split_main_from_subagent_by_size(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [
                _turn("m1", [_read_use("r1", file_path="/a.py")]),
                _read_result("r1", "x" * 4 * 100),
            ],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [_read_use("r2", file_path="/b.py")]),
                _read_result("r2", "x" * 4 * 6000),
            ])],
            _weights(),
        )
        assert totals.bucket_calls[(read_cost._SCOPE_MAIN, "<2000")] == 1
        assert totals.bucket_tokens[(read_cost._SCOPE_MAIN, "<2000")] == 100
        assert totals.bucket_calls[(read_cost._SCOPE_SUBAGENT, "5000+")] == 1
        assert totals.bucket_tokens[(read_cost._SCOPE_SUBAGENT, "5000+")] == 6000

    def test_bucket_tables_sum_same_size_reads_within_and_across_sessions(self):
        totals = read_cost._ReadCostTotals()
        seen = read_cost._SeenIds()
        small_text = "x" * 4 * 100
        large_text = "x" * 4 * 6000
        for session_prefix in ("a", "b"):
            read_cost._accumulate_session(
                totals, seen,
                _read_exchange(f"{session_prefix}-m1", f"{session_prefix}-r1", "/a.py", text=small_text)
                + _read_exchange(f"{session_prefix}-m2", f"{session_prefix}-r2", "/b.py", text=small_text),
                [(_TRACKED_AGENT_TYPE,
                  _read_exchange(f"{session_prefix}-s1", f"{session_prefix}-r3", "/c.py", text=large_text)
                  + _read_exchange(f"{session_prefix}-s2", f"{session_prefix}-r4", "/d.py", text=large_text))],
                _weights(),
            )
        assert totals.bucket_calls[(read_cost._SCOPE_MAIN, "<2000")] == 4
        assert totals.bucket_tokens[(read_cost._SCOPE_MAIN, "<2000")] == 400
        assert totals.bucket_calls[(read_cost._SCOPE_SUBAGENT, "5000+")] == 4
        assert totals.bucket_tokens[(read_cost._SCOPE_SUBAGENT, "5000+")] == 24000

    def test_bucket_tables_exclude_a_large_partial_read_on_the_main_thread(self):
        totals = read_cost._ReadCostTotals()
        large_text = "x" * 4 * 6000
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [
                _turn("m1", [
                    _read_use("r1", file_path="/whole.py"),
                    _read_use("r2", file_path="/partial.py", limit=5),
                ]),
                _read_result("r1", large_text),
                _read_result("r2", large_text),
            ],
            [],
            _weights(),
        )
        assert totals.bucket_calls[(read_cost._SCOPE_MAIN, "5000+")] == 1
        assert totals.bucket_tokens[(read_cost._SCOPE_MAIN, "5000+")] == 6000

    def test_bucket_tables_exclude_a_partial_read_in_a_subagent_window(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [_turn("m1", usage={"output_tokens": 2})],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [
                    _read_use("r1", file_path="/a.py"),
                    _read_use("r2", file_path="/b.py"),
                    _read_use("r3", file_path="/c.py", limit=5),
                ], usage={"output_tokens": 1}),
                _read_result("r1", _HUNDRED_TOKEN_TEXT),
                _read_result("r2", "x" * 1000),
                _read_result("r3", "x" * 4000),
            ])],
            _weights(),
        )
        assert totals.bucket_calls[(read_cost._SCOPE_SUBAGENT, "<2000")] == 2
        assert totals.bucket_tokens[(read_cost._SCOPE_SUBAGENT, "<2000")] == 350

    def test_subagent_row_carries_its_own_window_billed_weight_and_whole_file_tokens(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [_turn("m1", usage={"output_tokens": 2})],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [
                    _read_use("r1", file_path="/a.py"),
                    _read_use("r2", file_path="/b.py"),
                    _read_use("r3", file_path="/c.py", limit=5),
                ], usage={"output_tokens": 1}),
                _read_result("r1", _HUNDRED_TOKEN_TEXT),
                _read_result("r2", "x" * 1000),
                _read_result("r3", "x" * 4000),
            ])],
            _weights(),
        )
        row = totals.rows_by_agent_type[_TRACKED_AGENT_TYPE]
        assert row.billed == pytest.approx(50.0)
        assert row.whole_file_tokens == 350

    def test_session_resent_in_a_second_file_is_counted_once(self):
        records = [
            _turn("m1", [_read_use("r1", file_path="/a.py")], usage={"output_tokens": 1}),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ]
        totals = read_cost._ReadCostTotals()
        seen = read_cost._SeenIds()
        read_cost._accumulate_session(totals, seen, records, [], _weights())
        read_cost._accumulate_session(totals, seen, records, [], _weights())
        assert totals.sessions == 2
        assert len(totals.main_whole_file_events) == 1
        assert totals.api_calls == 1
        assert totals.billed_main == pytest.approx(50.0)

    def test_path_read_whole_file_by_the_main_thread_is_seen_elsewhere_for_a_subagent(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [_turn("m1", [_read_use("r1", file_path="/a.py")]), _read_result("r1", _HUNDRED_TOKEN_TEXT)],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [_read_use("r2", file_path="/a.py")]),
                _read_result("r2", _HUNDRED_TOKEN_TEXT),
            ])],
            _weights(),
        )
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].seen_elsewhere_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)

    def test_main_thread_partial_read_does_not_make_a_path_seen_elsewhere_for_a_subagent(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [
                _turn("m1", [_read_use("r1", file_path="/a.py", limit=10)]),
                _read_result("r1", _HUNDRED_TOKEN_TEXT),
            ],
            [(_TRACKED_AGENT_TYPE, _read_exchange("s1", "r2", "/a.py"))],
            _weights(),
        )
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].whole_file_reads == 1
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].seen_elsewhere_weight == 0.0

    def test_seen_elsewhere_credit_stops_at_the_session_boundary(self):
        totals = read_cost._ReadCostTotals()
        seen = read_cost._SeenIds()
        read_cost._accumulate_session(totals, seen, _read_exchange("a-m1", "a-r1", "/a.py"), [], _weights())
        both_paths_in_one_turn = [
            _turn("b-s2", [
                _read_use("b-r3", file_path="/a.py"),
                _read_use("b-r4", file_path="/b.py"),
                _read_use("b-r5", file_path="/c.py"),
            ]),
            _read_result("b-r3", _HUNDRED_TOKEN_TEXT),
            _read_result("b-r4", _HUNDRED_TOKEN_TEXT),
            _read_result("b-r5", _HUNDRED_TOKEN_TEXT),
        ]
        read_cost._accumulate_session(
            totals, seen,
            _read_exchange("b-m1", "b-r1", "/b.py"),
            [("Plan", _read_exchange("b-s1", "b-r2", "/c.py")), (_TRACKED_AGENT_TYPE, both_paths_in_one_turn)],
            _weights(),
        )
        # /a.py was read in session A only, so the second session's subagent is credited for /b.py (its main
        # thread) and /c.py (an earlier subagent) but not for /a.py.
        assert totals.rows_by_agent_type["Plan"].seen_elsewhere_weight == 0.0
        tracked_row = totals.rows_by_agent_type[_TRACKED_AGENT_TYPE]
        assert tracked_row.seen_elsewhere_weight == pytest.approx(2 * 100 * _CACHE_WRITE_WEIGHT)

    @pytest.mark.parametrize(("first_scanned_type", "second_scanned_type"), [
        (_TRACKED_AGENT_TYPE, "Plan"),
        ("Plan", _TRACKED_AGENT_TYPE),
    ])
    def test_second_scanned_subagent_reading_a_path_is_seen_elsewhere_and_the_first_is_not(
        self, first_scanned_type, second_scanned_type,
    ):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [],
            [
                (first_scanned_type, _read_exchange("s1", "r1", "/a.py")),
                (second_scanned_type, _read_exchange("s2", "r2", "/a.py")),
            ],
            _weights(),
        )
        assert totals.rows_by_agent_type[first_scanned_type].seen_elsewhere_weight == 0.0
        assert totals.rows_by_agent_type[second_scanned_type].seen_elsewhere_weight == pytest.approx(100 * _CACHE_WRITE_WEIGHT)

    def test_subagent_transcript_repeated_in_a_second_file_is_billed_and_read_once(self):
        window_records = [
            _turn("s1", [_read_use("r1", file_path="/a.py")], usage={"output_tokens": 1}),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ]
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [],
            [(_TRACKED_AGENT_TYPE, window_records), (_TRACKED_AGENT_TYPE, window_records)],
            _weights(),
        )
        assert totals.api_calls == 1
        assert totals.billed_subagent == pytest.approx(50.0)
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].whole_file_reads == 1
        assert totals.bucket_calls[(read_cost._SCOPE_SUBAGENT, "<2000")] == 1
        assert totals.repeated_reads == 1
        row = totals.rows_by_agent_type[_TRACKED_AGENT_TYPE]
        assert (row.agents, row.turns) == (1, 1)
        assert row.billed == pytest.approx(50.0)

    def test_subagent_window_with_no_turn_is_not_an_agent(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [], [(_TRACKED_AGENT_TYPE, [])], _weights(),
        )
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].agents == 0

    @pytest.mark.parametrize(("subagent_turn_ids", "expected_agents_and_turns"), [
        (["m1"], (0, 0)),
        (["m1", "s1"], (1, 1)),
    ])
    def test_turn_in_both_the_main_window_and_a_subagent_file_adds_no_subagent_turn(
        self, subagent_turn_ids, expected_agents_and_turns,
    ):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [_turn("m1")],
            [(_TRACKED_AGENT_TYPE, [_turn(turn_id) for turn_id in subagent_turn_ids])], _weights(),
        )
        row = totals.rows_by_agent_type[_TRACKED_AGENT_TYPE]
        assert (row.agents, row.turns) == expected_agents_and_turns

    def test_subagent_read_of_a_path_the_main_thread_edits_reads_as_unedited(self):
        # Edited-later is per window, so an edit in another window is invisible; this is the documented limit in
        # docs/transcript-analysis.md's read-cost Known limits.
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [_turn("m1", [_edit_use("e1", path="/a.py")])],
            [(_TRACKED_AGENT_TYPE, _read_exchange("s1", "r1", "/a.py", text="x" * (4 * read_cost._LARGE_READ_TOKENS)))],
            _weights(),
        )
        row = totals.rows_by_agent_type[_TRACKED_AGENT_TYPE]
        assert row.large_unedited_weight == row.large_weight > 0

    def test_invalid_usage_turn_is_counted_once_and_not_billed(self):
        invalid_turn = _turn("m1", usage={"output_tokens": -1})
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [invalid_turn],
            [(_TRACKED_AGENT_TYPE, [invalid_turn])], _weights(),
        )
        assert totals.invalid_usage_turns == 1
        assert (totals.api_calls, totals.billed_main, totals.billed_subagent) == (0, 0.0, 0.0)

    @pytest.mark.parametrize("invalid_window_is_scanned_first", [True, False])
    def test_turn_with_invalid_usage_in_one_window_and_valid_usage_in_another_is_billed_not_invalid(
        self, invalid_window_is_scanned_first,
    ):
        invalid_window = [_turn("m1", usage={"output_tokens": -1})]
        valid_window = [_turn("m1", usage={"output_tokens": 1})]
        first, second = (invalid_window, valid_window) if invalid_window_is_scanned_first else (
            valid_window, invalid_window,
        )
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [],
            [(_TRACKED_AGENT_TYPE, first), (_TRACKED_AGENT_TYPE, second)], _weights(),
        )
        assert totals.invalid_usage_turns == 0
        assert totals.api_calls == 1

    def test_repeated_reads_of_a_resent_session_sum_across_windows(self):
        records = _read_exchange("m1", "r1", "/a.py")
        totals = read_cost._ReadCostTotals()
        seen = read_cost._SeenIds()
        read_cost._accumulate_session(totals, seen, records, [], _weights())
        read_cost._accumulate_session(totals, seen, records, [], _weights())
        assert totals.repeated_reads == 1


class TestFoldUntrackedAgentTypes:
    def test_untracked_types_merge_into_one_withheld_row_and_tracked_types_keep_their_name(self):
        rows = {
            _TRACKED_AGENT_TYPE: read_cost._AgentTypeRow(agents=1, whole_file_reads=2),
            "private-a": read_cost._AgentTypeRow(agents=1, whole_file_reads=3),
            "private-b": read_cost._AgentTypeRow(agents=2, whole_file_reads=4),
        }
        named, withheld = read_cost._fold_untracked_agent_types(rows, frozenset({_TRACKED_AGENT_TYPE}))
        assert list(named) == [_TRACKED_AGENT_TYPE]
        assert (withheld.agents, withheld.whole_file_reads) == (3, 7)

    def test_withheld_row_with_billed_weight_and_no_agent_is_kept_so_rows_sum_to_the_total(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(), [],
            [
                (_TRACKED_AGENT_TYPE, [_turn("m1", usage={"output_tokens": -1})]),
                (_UNTRACKED_AGENT_TYPE, [_turn("m1", usage={"output_tokens": 1})]),
            ],
            _weights(),
        )
        named, withheld = read_cost._fold_untracked_agent_types(
            totals.rows_by_agent_type, frozenset({_TRACKED_AGENT_TYPE}),
        )
        assert (withheld.agents, withheld.billed) == (0, pytest.approx(50.0))
        assert named[_TRACKED_AGENT_TYPE].billed + withheld.billed == pytest.approx(totals.billed_subagent)

    def test_withheld_row_holding_only_read_figures_is_kept(self):
        rows = {_UNTRACKED_AGENT_TYPE: read_cost._AgentTypeRow(whole_file_reads=1, whole_file_tokens=250)}
        _named, withheld = read_cost._fold_untracked_agent_types(rows, frozenset({_TRACKED_AGENT_TYPE}))
        assert withheld is not None
        assert (withheld.agents, withheld.billed, withheld.whole_file_reads) == (0, 0.0, 1)

    def test_named_rows_and_the_withheld_row_partition_every_field_of_the_merged_total(self):
        rows = {
            _TRACKED_AGENT_TYPE: _row_with_every_field_set(1),
            "private-a": _row_with_every_field_set(2),
            "private-b": _row_with_every_field_set(4),
        }
        merged_total = read_cost._AgentTypeRow()
        for row in rows.values():
            merged_total.merge(row)

        named, withheld = read_cost._fold_untracked_agent_types(rows, frozenset({_TRACKED_AGENT_TYPE}))

        partition_total = read_cost._AgentTypeRow()
        for row in [*named.values(), withheld]:
            partition_total.merge(row)
        for row_field in dataclasses.fields(merged_total):
            assert getattr(partition_total, row_field.name) == getattr(merged_total, row_field.name), row_field.name

    def test_no_withheld_row_when_every_type_is_tracked(self):
        rows = {_TRACKED_AGENT_TYPE: read_cost._AgentTypeRow(agents=1)}
        _named, withheld = read_cost._fold_untracked_agent_types(rows, frozenset({_TRACKED_AGENT_TYPE}))
        assert withheld is None
