"""Tests for read_cost._scan_window: one window's reads, turns and billing."""
import pytest
from transcript_analysis import pricing, read_cost

from ._read_cost_helpers import (
    _HUNDRED_TOKEN_TEXT,
    _read_exchange,
    _scan,
    _turn,
    _weights,
)
from .conftest import (
    _NON_CONVERSATION_CONTENT_RECORD_TYPES,
    _api_request_blob_rec,
    _asst,
    _compact_boundary_rec,
    _edit_use,
    _non_boundary_system_rec,
    _read_result,
    _read_use,
    _user_msg,
)


def _event_for(scan: read_cost._WindowScan, path: str) -> read_cost._ReadEvent:
    return next(event for event in scan.events if event.path == path)


class TestScanWindow:
    def test_turns_count_distinct_message_ids_not_records(self):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _turn("m1"),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2"),
            _turn("m2"),
            _turn("m3"),
        ])
        assert len(scan.turn_message_ids) == 3
        assert _event_for(scan, "/a.py").remaining_turns == 2

    def test_compaction_boundary_ends_the_window_a_read_is_resent_in(self):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _non_boundary_system_rec(),
            _turn("m2", [_read_use("r2", file_path="/b.py")]),
            _read_result("r2", _HUNDRED_TOKEN_TEXT),
            _compact_boundary_rec(),
            _turn("m3", [_read_use("r3", file_path="/c.py")]),
            _read_result("r3", _HUNDRED_TOKEN_TEXT),
            _turn("m4"),
        ])
        assert len(scan.turn_message_ids) == 4
        # r1 is resent once (turn 2) before the boundary, r2 in the last turn before it is never resent, and r3
        # is resent in the one turn after it. The non-boundary system record changes none of these values and
        # exists so a guard keyed on type == "system" alone fails the /a.py assertion.
        assert _event_for(scan, "/a.py").remaining_turns == 1
        assert _event_for(scan, "/b.py").remaining_turns == 0
        assert _event_for(scan, "/c.py").remaining_turns == 1

    def test_token_estimate_counts_characters_not_bytes_of_a_multibyte_result(self):
        multibyte_text = "é" * 400
        assert len(multibyte_text.encode("utf-8")) == 2 * len(multibyte_text)
        scan = _scan(_read_exchange("m1", "r1", "/a.py", text=multibyte_text))
        assert _event_for(scan, "/a.py").tokens == 400 // read_cost._READ_COST_CHARS_PER_TOKEN

    def test_read_with_no_boundary_after_it_runs_to_the_end_of_the_window(self):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2"),
            _turn("m3"),
        ])
        assert _event_for(scan, "/a.py").remaining_turns == 2

    @pytest.mark.parametrize("record_type", _NON_CONVERSATION_CONTENT_RECORD_TYPES)
    def test_api_request_blob_adds_no_event_and_is_not_a_repeated_read(self, record_type):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _api_request_blob_rec([_read_use("r1", file_path="/a.py")], agent_id="agent-a", record_type=record_type),
            _api_request_blob_rec([_read_use("r2", file_path="/b.py")], agent_id="agent-a", record_type=record_type),
            _api_request_blob_rec(
                [{"type": "tool_result", "tool_use_id": "r2", "content": _HUNDRED_TOKEN_TEXT}], record_type=record_type,
            ),
        ])
        assert [event.path for event in scan.events] == ["/a.py"]
        assert scan.repeated_reads == 0

    def test_assistant_record_without_message_id_is_not_a_turn_billed_or_invalid(self):
        id_less_turn = _asst("claude-sonnet-5")
        id_less_turn["message"]["usage"] = {"output_tokens": 1}
        invalid_id_less_turn = _asst("claude-sonnet-5")
        invalid_id_less_turn["message"]["usage"] = {"output_tokens": -1}
        scan = _scan([id_less_turn, invalid_id_less_turn])
        assert len(scan.turn_message_ids) == 0
        assert scan.usage_by_message == {}
        assert scan.invalid_usage_message_ids == frozenset()

    def test_read_without_a_file_path_produces_no_event_and_leaves_its_id_unseen(self):
        seen: set[str] = set()
        pathless_read = {"type": "tool_use", "id": "r1", "name": "Read", "input": {}}
        scan = _scan([_turn("m1", [pathless_read]), _read_result("r1", _HUNDRED_TOKEN_TEXT)], seen)
        assert scan.events == []
        assert scan.repeated_reads == 0
        assert seen == set()

    def test_id_less_read_and_id_less_result_do_not_pair_with_each_other(self):
        seen: set[str] = set()
        id_less_read = {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.py"}}
        id_less_result = _user_msg([{"type": "tool_result", "content": _HUNDRED_TOKEN_TEXT}])
        scan = _scan([_turn("m1", [id_less_read]), id_less_result], seen)
        assert scan.events == []
        assert seen == set()

    @pytest.mark.parametrize("unusable_tool_id", ["", 7, ["r1"], {"id": "r1"}])
    def test_read_with_an_empty_or_non_string_id_produces_no_event_and_leaves_no_id_seen(self, unusable_tool_id):
        seen: set[str] = set()
        scan = _scan([
            _turn("m1", [_read_use(unusable_tool_id, file_path="/a.py")]),
            _read_result(unusable_tool_id, _HUNDRED_TOKEN_TEXT),
        ], seen)
        assert scan.events == []
        assert scan.repeated_reads == 0
        assert seen == set()

    @pytest.mark.parametrize("unusable_result_id", ["", 7, ["r1"], {"id": "r1"}])
    def test_result_with_an_empty_or_non_string_tool_use_id_leaves_a_pending_read_to_its_own_result(
        self, unusable_result_id,
    ):
        seen: set[str] = set()
        stray_result = _user_msg([
            {"type": "tool_result", "tool_use_id": unusable_result_id, "content": _HUNDRED_TOKEN_TEXT},
        ])
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            stray_result,
            _read_result("r1", "x" * 800),
        ], seen)
        assert [(event.path, event.tokens) for event in scan.events] == [("/a.py", 200)]
        assert seen == {"r1"}

    @pytest.mark.parametrize("unusable_content", [None, "a plain-text user prompt"])
    def test_record_whose_message_content_is_not_a_block_list_is_skipped_without_error(self, unusable_content):
        scan = _scan([
            _user_msg(unusable_content),
            _turn("m1", unusable_content),
            *_read_exchange("m2", "r1", "/a.py"),
        ])
        assert [event.path for event in scan.events] == ["/a.py"]

    def test_id_less_assistant_record_is_no_turn_and_its_read_sits_at_the_previous_turns_index(self):
        id_less_turn = _asst("claude-sonnet-5", content=[_read_use("r1", file_path="/a.py")])
        id_less_turn_after_the_read = _asst("claude-sonnet-5", content=[])
        scan = _scan([
            _turn("m1"), id_less_turn, _read_result("r1", _HUNDRED_TOKEN_TEXT), id_less_turn_after_the_read,
            _turn("m2"),
        ])
        assert scan.turn_message_ids == {"m1", "m2"}
        assert _event_for(scan, "/a.py").remaining_turns == 1

    def test_read_result_without_an_is_error_key_is_counted(self):
        successful_result = _user_msg([{"type": "tool_result", "tool_use_id": "r1", "content": _HUNDRED_TOKEN_TEXT}])
        scan = _scan([_turn("m1", [_read_use("r1", file_path="/a.py")]), successful_result])
        assert _event_for(scan, "/a.py").tokens == 100

    def test_non_dict_content_block_is_skipped_beside_a_valid_read(self):
        scan = _scan([
            _turn("m1", ["stray text", None, _read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ])
        assert [event.path for event in scan.events] == ["/a.py"]

    def test_edit_after_the_read_marks_the_read_edited_later(self):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2", [_edit_use("e1", path="/a.py")]),
        ])
        assert _event_for(scan, "/a.py").edited_later is True

    def test_edit_in_the_same_turn_as_the_read_marks_the_read_edited_later(self):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py"), _edit_use("e1", path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ])
        assert _event_for(scan, "/a.py").edited_later is True

    def test_edit_only_before_the_read_or_of_another_path_leaves_the_read_unedited(self):
        scan = _scan([
            _turn("m1", [_edit_use("e1", path="/a.py")]),
            _turn("m2", [_read_use("r1", file_path="/a.py"), _edit_use("e2", path="/other.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ])
        assert _event_for(scan, "/a.py").edited_later is False

    def test_non_dict_tool_input_is_skipped_without_error(self):
        malformed = {"type": "tool_use", "id": "r1", "name": "Read", "input": ["/a.py"]}
        assert _scan([_turn("m1", [malformed]), _read_result("r1", _HUNDRED_TOKEN_TEXT)]).events == []

    def test_read_with_offset_or_limit_is_not_whole_file(self):
        scan = _scan([
            _turn("m1", [
                _read_use("r1", file_path="/a.py", limit=10),
                _read_use("r2", file_path="/b.py", offset=0),
                _read_use("r3", file_path="/c.py"),
            ]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _read_result("r2", _HUNDRED_TOKEN_TEXT),
            _read_result("r3", _HUNDRED_TOKEN_TEXT),
        ])
        assert {event.path: event.whole_file for event in scan.events} == {
            "/a.py": False, "/b.py": False, "/c.py": True,
        }

    def test_read_with_only_pages_set_is_whole_file(self):
        pages_only_read = _read_use("r1", file_path="/a.pdf")
        pages_only_read["input"]["pages"] = "1-5"
        scan = _scan([_turn("m1", [pages_only_read]), _read_result("r1", _HUNDRED_TOKEN_TEXT)])
        assert _event_for(scan, "/a.pdf").whole_file is True

    def test_error_and_non_text_results_produce_no_event(self):
        scan = _scan([
            _turn("m1", [
                _read_use("r1", file_path="/a.py"),
                _read_use("r2", file_path="/b.py"),
            ]),
            _read_result("r1", "boom", is_error=True),
            _read_result("r2", [{"type": "image"}]),
        ])
        assert scan.events == []

    def test_text_block_list_result_is_dropped_like_any_non_string_result(self):
        # docs/transcript-analysis.md's read-cost Known limits: a result that is not plain text is left out.
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", [{"type": "text", "text": _HUNDRED_TOKEN_TEXT}]),
        ])
        assert scan.events == []

    def test_tool_use_id_already_seen_in_another_window_is_not_counted_again(self):
        seen: set[str] = set()
        records = [
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ]
        assert len(_scan(records, seen).events) == 1
        assert _scan(records, seen).events == []

    def test_event_tokens_and_lines_come_from_the_result_text(self):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", "ab\n" * 7 + "abc"),
        ])
        event = _event_for(scan, "/a.py")
        assert (event.tokens, event.lines) == (6, 8)

    @pytest.mark.parametrize(("result_text", "expected_lines"), [
        ("", 0),
        ("ab", 1),
        ("ab\n", 1),
        ("ab\nab", 2),
        ("ab\nab\n", 2),
    ])
    def test_event_lines_count_the_numbered_lines_whether_or_not_the_result_ends_in_a_newline(
        self, result_text, expected_lines,
    ):
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", result_text),
        ])
        assert _event_for(scan, "/a.py").lines == expected_lines

    @pytest.mark.parametrize(("line_threshold", "row_count", "in_cohort"), [
        (350, 351, True),
        (350, 350, False),
        (800, 801, True),
        (800, 800, False),
    ])
    def test_unterminated_numbered_read_result_lands_in_the_cohort_only_above_the_threshold(
        self, line_threshold, row_count, in_cohort,
    ):
        # The real Read tool prefixes each row with a line number and a tab, and ends the last row without a newline.
        real_shape_result = "\n".join(f"{row_number:6d}\t line {row_number}" for row_number in range(1, row_count + 1))
        scan = _scan([
            _turn("m1", [_read_use("r1", file_path="/a.py")]),
            _read_result("r1", real_shape_result),
        ])

        cohort = read_cost._cohort_figures(scan.events, line_threshold, _weights(), 0.0, 0.0)

        assert cohort.reads == (1 if in_cohort else 0)

    def test_tool_result_of_a_legacy_human_record_is_kept(self):
        human_result = _read_result("r1", _HUNDRED_TOKEN_TEXT)
        human_result["type"] = "human"
        scan = _scan([_turn("m1", [_read_use("r1", file_path="/a.py")]), human_result])
        assert [event.path for event in scan.events] == ["/a.py"]

    @pytest.mark.parametrize(("tool_name", "path_key"), [
        ("Edit", "file_path"),
        ("Write", "file_path"),
        ("MultiEdit", "file_path"),
        ("NotebookEdit", "notebook_path"),
    ])
    def test_each_edit_tool_marks_the_read_edited_later(self, tool_name, path_key):
        edit = {"type": "tool_use", "id": "e1", "name": tool_name, "input": {path_key: "/a.py"}}
        scan = _scan([*_read_exchange("m1", "r1", "/a.py"), _turn("m2", [edit])])
        assert _event_for(scan, "/a.py").edited_later is True

    def test_non_edit_tool_naming_the_path_leaves_the_read_unedited(self):
        grep = {"type": "tool_use", "id": "g1", "name": "Grep", "input": {"file_path": "/a.py"}}
        scan = _scan([*_read_exchange("m1", "r1", "/a.py"), _turn("m2", [grep])])
        assert _event_for(scan, "/a.py").edited_later is False

    def test_failed_edit_still_marks_the_read_edited_later(self):
        scan = _scan([
            *_read_exchange("m1", "r1", "/a.py"),
            _turn("m2", [_edit_use("e1", path="/a.py")]),
            _read_result("e1", "old_string not found", is_error=True),
        ])
        assert _event_for(scan, "/a.py").edited_later is True

    def test_second_boundary_does_not_extend_the_window_of_a_read_before_the_first(self):
        scan = _scan([
            *_read_exchange("m1", "r1", "/a.py"),
            _turn("m2"),
            _compact_boundary_rec(),
            *_read_exchange("m3", "r3", "/b.py"),
            _turn("m4"),
            _compact_boundary_rec(),
            *_read_exchange("m5", "r5", "/c.py"),
            _turn("m6"),
        ])
        assert [_event_for(scan, path).remaining_turns for path in ("/a.py", "/b.py", "/c.py")] == [1, 1, 1]

    def test_edit_after_a_compaction_marks_a_read_before_it_edited_later(self):
        scan = _scan([
            *_read_exchange("m1", "r1", "/a.py"),
            _compact_boundary_rec(),
            _turn("m2", [_edit_use("e1", path="/a.py")]),
        ])
        event = _event_for(scan, "/a.py")
        assert (event.edited_later, event.remaining_turns) == (True, 0)

    def test_synthetic_turn_is_neither_a_turn_nor_usage(self):
        synthetic = _turn("m-synthetic", usage={"output_tokens": 5})
        synthetic["message"]["model"] = pricing._SYNTHETIC_MODEL_ID
        scan = _scan([synthetic, _turn("m1", usage={"output_tokens": 7})])
        assert len(scan.turn_message_ids) == 1
        assert list(scan.usage_by_message) == ["m1"]

    @pytest.mark.parametrize("invalid_usage", [
        {"output_tokens": None},
        {"output_tokens": "5"},
        {"output_tokens": -1},
        {"cache_creation": {"ephemeral_5m_input_tokens": -1}},
        "not-an-object",
    ])
    def test_turn_with_invalid_usage_is_excluded_and_reported(self, invalid_usage):
        scan = _scan([_turn("m1", usage=invalid_usage)])
        assert scan.usage_by_message == {}
        assert scan.invalid_usage_message_ids == {"m1"}
        assert len(scan.turn_message_ids) == 1

    def test_invalid_usage_in_a_later_record_does_not_replace_valid_usage(self):
        good_usage = {"output_tokens": 5}
        scan = _scan([_turn("m1", usage=good_usage), _turn("m1", usage={"output_tokens": -1})])
        assert scan.usage_by_message == {"m1": good_usage}
        assert scan.invalid_usage_message_ids == frozenset()

    def test_valid_usage_in_a_later_record_replaces_invalid_usage(self):
        good_usage = {"output_tokens": 5}
        scan = _scan([_turn("m1", usage={"output_tokens": -1}), _turn("m1", usage=good_usage)])
        assert scan.usage_by_message == {"m1": good_usage}
        assert scan.invalid_usage_message_ids == frozenset()

    def test_record_with_no_usage_after_a_populated_one_keeps_the_populated_usage(self):
        usage = {"output_tokens": 5}
        scan = _scan([_turn("m1", usage=usage), _turn("m1")])
        assert scan.usage_by_message == {"m1": usage}

    def test_last_usage_for_a_repeated_message_id_wins(self):
        scan = _scan([_turn("m1", usage={"output_tokens": 1}), _turn("m1", usage={"output_tokens": 9})])
        assert scan.usage_by_message == {"m1": {"output_tokens": 9}}

    def test_read_already_seen_in_another_window_is_counted_as_a_repeated_read(self):
        seen: set[str] = set()
        records = _read_exchange("m1", "r1", "/a.py")
        assert _scan(records, seen).repeated_reads == 0
        assert _scan(records, seen).repeated_reads == 1

    def test_copy_cut_off_before_the_result_does_not_hide_the_read_in_a_later_copy(self):
        seen: set[str] = set()
        cut_off = [_turn("m1", [_read_use("r1", file_path="/a.py")])]
        assert _scan(cut_off, seen).events == []
        assert seen == set()
        full_copy = _scan([*cut_off, _read_result("r1", _HUNDRED_TOKEN_TEXT)], seen)
        assert [event.path for event in full_copy.events] == ["/a.py"]
        assert seen == {"r1"}

    def test_read_with_an_error_result_leaves_its_id_unseen(self):
        seen: set[str] = set()
        _scan([_turn("m1", [_read_use("r1", file_path="/a.py")]), _read_result("r1", "boom", is_error=True)], seen)
        assert seen == set()
