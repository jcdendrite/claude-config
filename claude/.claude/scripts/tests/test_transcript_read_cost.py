"""Tests for transcript_analysis/read_cost.py (read-cost).

The window scan, billing, and per-agent-type rows are tested as pure functions over literal records. The
command is tested on transcript files under the fake_projects corpus.
"""
import dataclasses
import os
from collections import Counter

import pytest
from transcript_analysis import corpus, pricing, read_cost, redaction, scope, subagent_mix

from .conftest import (
    _api_request_blob_rec,
    _asst,
    _compact_boundary_rec,
    _edit_use,
    _read_result,
    _read_tool_use,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
)

_PROJECT_SLUG = "-home-user-testrepo"
# An agent type this repo tracks under agents/, and one that no repo tracks.
_TRACKED_AGENT_TYPE = "staff-backend-engineer"
_UNTRACKED_AGENT_TYPE = "sentinel-untracked-agent-type"

# 400 characters are 100 estimated tokens (chars // 4).
_HUNDRED_TOKEN_TEXT = "x" * 400


def _weights() -> dict[str, float]:
    return pricing._cache_read_equivalent_weights()


def _turn(message_id: str, content: list | None = None, *, usage: dict | None = None) -> dict:
    rec = _asst("claude-sonnet-5", content=content)
    rec["message"]["id"] = message_id
    if usage is not None:
        rec["message"]["usage"] = usage
    return rec


def _read_cost_args(*, this_repo: bool = True, projects: str = "*") -> object:
    """Pre-seeds args._this_repo_slugs -- the cache _resolve_project_scope reads first -- so scope resolution
    does not ask git for this repo's worktrees."""
    return type("A", (), {
        "this_repo": this_repo,
        "projects": projects,
        "_this_repo_slugs": [_PROJECT_SLUG],
    })()


def _scan(records: list[dict], seen_tool_use_ids: set[str] | None = None) -> read_cost._WindowScan:
    return read_cost._scan_window(records, set() if seen_tool_use_ids is None else seen_tool_use_ids)


def _read_exchange(message_id: str, tool_id: str, path: str, *, text: str = _HUNDRED_TOKEN_TEXT) -> list[dict]:
    return [_turn(message_id, [_read_tool_use(tool_id, file_path=path)]), _read_result(tool_id, text)]


def _event_for(scan: read_cost._WindowScan, path: str) -> read_cost._ReadEvent:
    return next(event for event in scan.events if event.path == path)


def _row_with_every_field_set(value: int) -> read_cost._AgentTypeRow:
    row = read_cost._AgentTypeRow()
    for row_field in dataclasses.fields(row):
        default = getattr(row, row_field.name)
        if isinstance(default, Counter):
            setattr(row, row_field.name, Counter({read_cost._KIND_INSTRUCTION: value}))
        else:
            setattr(row, row_field.name, type(default)(value))
    return row


class TestCacheReadEquivalentWeights:
    def test_weights_pin_the_literal_ratios_of_each_class_to_cache_read(self):
        assert _weights() == pytest.approx({
            "cache_read": 1.0, "cache_write_5m": 12.5, "cache_write_1h": 20.0, "output": 50.0, "input": 10.0,
        })

    def test_weights_cover_every_token_class(self):
        assert set(_weights()) == set(pricing._TOKEN_CLASSES)

    def test_weights_agree_with_model_rates_for_a_model_without_a_cache_read_override(self):
        model = "claude-sonnet-4-6"
        assert model not in pricing._CACHE_READ_MULTIPLIER_OVERRIDES
        rates = pricing._model_rates(model)
        assert {cls: rate / rates["cache_read"] for cls, rate in rates.items()} == pytest.approx(_weights())


class TestScanWindow:
    def test_turns_count_distinct_message_ids_not_records(self):
        scan = _scan([_turn("m1"), _turn("m1"), _turn("m2")])
        assert len(scan.turn_message_ids) == 2

    def test_compaction_boundary_ends_the_window_a_read_is_resent_in(self):
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2", [_read_tool_use("r2", file_path="/b.py")]),
            _read_result("r2", _HUNDRED_TOKEN_TEXT),
            _compact_boundary_rec(),
            _turn("m3", [_read_tool_use("r3", file_path="/c.py")]),
            _read_result("r3", _HUNDRED_TOKEN_TEXT),
            _turn("m4"),
        ])
        assert len(scan.turn_message_ids) == 4
        # r1 is resent once (turn 2) before the boundary, r2 in the last turn before it is never resent, and r3
        # is resent in the one turn after it.
        assert _event_for(scan, "/a.py").remaining_turns == 1
        assert _event_for(scan, "/b.py").remaining_turns == 0
        assert _event_for(scan, "/c.py").remaining_turns == 1

    def test_read_with_no_boundary_after_it_runs_to_the_end_of_the_window(self):
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2"),
            _turn("m3"),
        ])
        assert _event_for(scan, "/a.py").remaining_turns == 2

    def test_api_request_blob_adds_no_event_and_is_not_a_repeated_read(self):
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _api_request_blob_rec([_read_tool_use("r1", file_path="/a.py")], agent_id="agent-a"),
            _api_request_blob_rec([_read_tool_use("r2", file_path="/b.py")], agent_id="agent-a"),
            _api_request_blob_rec([{"type": "tool_result", "tool_use_id": "r2", "content": _HUNDRED_TOKEN_TEXT}]),
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
            _turn("m1", [_read_tool_use(unusable_tool_id, file_path="/a.py")]),
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
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
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
        id_less_turn = _asst("claude-sonnet-5", content=[_read_tool_use("r1", file_path="/a.py")])
        id_less_turn_after_the_read = _asst("claude-sonnet-5", content=[])
        scan = _scan([
            _turn("m1"), id_less_turn, _read_result("r1", _HUNDRED_TOKEN_TEXT), id_less_turn_after_the_read,
            _turn("m2"),
        ])
        assert scan.turn_message_ids == {"m1", "m2"}
        assert _event_for(scan, "/a.py").remaining_turns == 1

    def test_read_result_without_an_is_error_key_is_counted(self):
        successful_result = _user_msg([{"type": "tool_result", "tool_use_id": "r1", "content": _HUNDRED_TOKEN_TEXT}])
        scan = _scan([_turn("m1", [_read_tool_use("r1", file_path="/a.py")]), successful_result])
        assert _event_for(scan, "/a.py").tokens == 100

    def test_non_dict_content_block_is_skipped_beside_a_valid_read(self):
        scan = _scan([
            _turn("m1", ["stray text", None, _read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ])
        assert [event.path for event in scan.events] == ["/a.py"]

    def test_edit_after_the_read_marks_the_read_edited_later(self):
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2", [_edit_use("e1", path="/a.py")]),
        ])
        assert _event_for(scan, "/a.py").edited_later is True

    def test_edit_in_the_same_turn_as_the_read_marks_the_read_edited_later(self):
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py"), _edit_use("e1", path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ])
        assert _event_for(scan, "/a.py").edited_later is True

    def test_edit_only_before_the_read_or_of_another_path_leaves_the_read_unedited(self):
        scan = _scan([
            _turn("m1", [_edit_use("e1", path="/a.py")]),
            _turn("m2", [_read_tool_use("r1", file_path="/a.py"), _edit_use("e2", path="/other.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ])
        assert _event_for(scan, "/a.py").edited_later is False

    def test_non_dict_tool_input_is_skipped_without_error(self):
        malformed = {"type": "tool_use", "id": "r1", "name": "Read", "input": ["/a.py"]}
        assert _scan([_turn("m1", [malformed]), _read_result("r1", _HUNDRED_TOKEN_TEXT)]).events == []

    def test_read_with_offset_or_limit_is_not_whole_file(self):
        scan = _scan([
            _turn("m1", [
                _read_tool_use("r1", file_path="/a.py", limit=10),
                _read_tool_use("r2", file_path="/b.py", offset=0),
                _read_tool_use("r3", file_path="/c.py"),
            ]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _read_result("r2", _HUNDRED_TOKEN_TEXT),
            _read_result("r3", _HUNDRED_TOKEN_TEXT),
        ])
        assert {event.path: event.whole_file for event in scan.events} == {
            "/a.py": False, "/b.py": False, "/c.py": True,
        }

    def test_read_with_only_pages_set_is_whole_file(self):
        pages_only_read = _read_tool_use("r1", file_path="/a.pdf")
        pages_only_read["input"]["pages"] = "1-5"
        scan = _scan([_turn("m1", [pages_only_read]), _read_result("r1", _HUNDRED_TOKEN_TEXT)])
        assert _event_for(scan, "/a.pdf").whole_file is True

    def test_error_and_non_text_results_produce_no_event(self):
        scan = _scan([
            _turn("m1", [
                _read_tool_use("r1", file_path="/a.py"),
                _read_tool_use("r2", file_path="/b.py"),
            ]),
            _read_result("r1", "boom", is_error=True),
            _read_result("r2", [{"type": "image"}]),
        ])
        assert scan.events == []

    def test_text_block_list_result_is_dropped_like_any_non_string_result(self):
        # docs/transcript-analysis.md's read-cost Known limits: a result that is not plain text is left out.
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", [{"type": "text", "text": _HUNDRED_TOKEN_TEXT}]),
        ])
        assert scan.events == []

    def test_tool_use_id_already_seen_in_another_window_is_not_counted_again(self):
        seen: set[str] = set()
        records = [
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ]
        assert len(_scan(records, seen).events) == 1
        assert _scan(records, seen).events == []

    def test_event_tokens_and_lines_come_from_the_result_text(self):
        scan = _scan([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "ab\n" * 8),
        ])
        event = _event_for(scan, "/a.py")
        assert (event.tokens, event.lines) == (6, 8)

    def test_tool_result_of_a_legacy_human_record_is_kept(self):
        human_result = _read_result("r1", _HUNDRED_TOKEN_TEXT)
        human_result["type"] = "human"
        scan = _scan([_turn("m1", [_read_tool_use("r1", file_path="/a.py")]), human_result])
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
        cut_off = [_turn("m1", [_read_tool_use("r1", file_path="/a.py")])]
        assert _scan(cut_off, seen).events == []
        assert seen == set()
        full_copy = _scan([*cut_off, _read_result("r1", _HUNDRED_TOKEN_TEXT)], seen)
        assert [event.path for event in full_copy.events] == ["/a.py"]
        assert seen == {"r1"}

    def test_read_with_an_error_result_leaves_its_id_unseen(self):
        seen: set[str] = set()
        _scan([_turn("m1", [_read_tool_use("r1", file_path="/a.py")]), _read_result("r1", "boom", is_error=True)], seen)
        assert seen == set()


class TestBilledWeight:
    def test_usage_is_weighted_per_token_class(self):
        usage = {
            "input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3,
            "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 5},
        }
        assert read_cost._billed_weight(usage, _weights()) == pytest.approx(
            1 * 10 + 2 * 50 + 3 * 1 + 4 * 12.5 + 5 * 20
        )

    def test_flat_cache_creation_field_is_priced_as_five_minute_writes(self):
        usage = {"cache_creation_input_tokens": 4}
        assert read_cost._billed_weight(usage, _weights()) == pytest.approx(4 * 12.5)

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
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2", [_read_tool_use("r2", file_path="/a.py")]),
            _read_result("r2", _HUNDRED_TOKEN_TEXT),
        ], set())
        assert row.repeat_in_agent_weight == pytest.approx(100 * 12.5)
        assert row.seen_elsewhere_weight == 0.0
        assert read_paths == {"/a.py"}

    def test_path_in_the_session_set_is_seen_elsewhere_not_a_repeat(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ], {"/a.py"})
        assert row.seen_elsewhere_weight == pytest.approx(100 * 12.5)
        assert row.repeat_in_agent_weight == 0.0

    def test_large_unedited_read_counts_as_large_and_large_unedited(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", "x" * (4 * read_cost._LARGE_READ_TOKENS)),
        ], set())
        assert row.large_weight == row.large_unedited_weight == pytest.approx(read_cost._LARGE_READ_TOKENS * 12.5)

    def test_large_edited_read_counts_as_large_but_not_large_unedited(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py"), _edit_use("e1", path="/a.py")]),
            _read_result("r1", "x" * (4 * read_cost._LARGE_READ_TOKENS)),
        ], set())
        assert row.large_weight == pytest.approx(read_cost._LARGE_READ_TOKENS * 12.5)
        assert row.large_unedited_weight == 0.0

    def test_partial_read_counts_toward_all_reads_but_not_whole_file(self):
        row, read_paths = self._row([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py", limit=5)]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
        ], set())
        assert row.all_read_weight == pytest.approx(100 * 12.5)
        assert (row.whole_file_reads, row.whole_file_weight) == (0, 0.0)
        assert read_paths == set()

    def test_remaining_turns_add_one_cache_read_each_to_the_weight(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
            _read_result("r1", _HUNDRED_TOKEN_TEXT),
            _turn("m2"),
            _turn("m3"),
        ], set())
        assert row.whole_file_weight == pytest.approx(100 * (12.5 + 2))

    def test_path_in_the_session_set_and_read_twice_is_seen_elsewhere_once_and_a_repeat_once(self):
        row, _read_paths = self._row([
            *_read_exchange("m1", "r1", "/a.py"),
            *_read_exchange("m2", "r2", "/a.py"),
        ], {"/a.py"})
        # The first read has one later turn (m2), the second none.
        assert row.seen_elsewhere_weight == pytest.approx(100 * (12.5 + 1))
        assert row.repeat_in_agent_weight == pytest.approx(100 * 12.5)

    def test_reread_after_an_edit_of_the_path_is_a_repeat_and_the_reread_reads_as_unedited(self):
        records = [
            *_read_exchange("m1", "r1", "/a.py"),
            _turn("m2", [_edit_use("e1", path="/a.py")]),
            *_read_exchange("m3", "r2", "/a.py"),
        ]
        row, _read_paths = self._row(records, set())
        assert [event.edited_later for event in _scan(records).events] == [True, False]
        assert row.repeat_in_agent_weight == pytest.approx(100 * 12.5)

    def test_reread_after_a_compaction_is_a_repeat(self):
        row, _read_paths = self._row([
            *_read_exchange("m1", "r1", "/a.py"),
            _compact_boundary_rec(),
            *_read_exchange("m2", "r2", "/a.py"),
        ], set())
        assert row.repeat_in_agent_weight == pytest.approx(100 * 12.5)

    def test_whole_file_read_one_token_under_the_threshold_is_not_large(self):
        row, _read_paths = self._row(_read_exchange(
            "m1", "r1", "/a.py", text="x" * (4 * (read_cost._LARGE_READ_TOKENS - 1)),
        ), set())
        assert row.large_weight == row.large_unedited_weight == 0.0
        assert row.whole_file_weight == pytest.approx((read_cost._LARGE_READ_TOKENS - 1) * 12.5)

    def test_read_of_a_character_count_that_floors_to_one_token_under_the_threshold_is_not_large(self):
        row, _read_paths = self._row(_read_exchange("m1", "r1", "/a.py", text="x" * 7999), set())
        assert row.large_weight == 0.0
        assert row.whole_file_tokens == 1999

    def test_large_weight_carries_only_the_read_at_the_threshold_when_a_read_just_under_it_precedes(self):
        row, _read_paths = self._row([
            _turn("m1", [_read_tool_use("r1", file_path="/a.py"), _read_tool_use("r2", file_path="/b.py")]),
            _read_result("r1", "x" * (4 * (read_cost._LARGE_READ_TOKENS - 1))),
            _read_result("r2", "x" * (4 * read_cost._LARGE_READ_TOKENS)),
        ], set())
        assert row.large_weight == pytest.approx(read_cost._LARGE_READ_TOKENS * 12.5)
        assert row.whole_file_weight == pytest.approx((2 * read_cost._LARGE_READ_TOKENS - 1) * 12.5)

    def test_kind_weight_sums_whole_file_read_weight_per_kind_and_ignores_partial_reads(self):
        row, _read_paths = self._row([
            _turn("m1", [
                _read_tool_use("r1", file_path="/repo/CLAUDE.md"),
                _read_tool_use("r2", file_path="/repo/docs/guide.md"),
                _read_tool_use("r3", file_path="/repo/src/app.py"),
                _read_tool_use("r4", file_path="/repo/.claude/rules/style.md", limit=5),
            ]),
            _read_result("r1", "x" * (4 * 100)),
            _read_result("r2", "x" * (4 * 200)),
            _read_result("r3", "x" * (4 * 300)),
            _read_result("r4", "x" * (4 * 500)),
        ], set())
        assert row.kind_weight == pytest.approx({
            read_cost._KIND_INSTRUCTION: 100 * 12.5,
            read_cost._KIND_OTHER_PROSE: 200 * 12.5,
            read_cost._KIND_CODE_OR_OTHER: 300 * 12.5,
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
    def test_main_file_sidechain_records_are_left_to_the_subagent_windows(self):
        sidechain_turn = _turn("m-side", [_read_tool_use("r1", file_path="/a.py")])
        sidechain_turn["isSidechain"] = True
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [sidechain_turn, _read_result("r1", "x" * 400)], [], _weights(),
        )
        assert totals.main_whole_file_events == []

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
                _turn("m1", [_read_tool_use("r1", file_path="/a.py")]),
                _read_result("r1", "x" * 4 * 100),
            ],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [_read_tool_use("r2", file_path="/b.py")]),
                _read_result("r2", "x" * 4 * 6000),
            ])],
            _weights(),
        )
        assert totals.bucket_calls[(read_cost._SCOPE_MAIN, "<2000")] == 1
        assert totals.bucket_calls[(read_cost._SCOPE_SUBAGENT, "5000+")] == 1
        assert totals.bucket_tokens[(read_cost._SCOPE_SUBAGENT, "5000+")] == 6000

    def test_subagent_row_carries_its_own_window_billed_weight_and_whole_file_tokens(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [_turn("m1", usage={"output_tokens": 2})],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [
                    _read_tool_use("r1", file_path="/a.py"),
                    _read_tool_use("r2", file_path="/b.py"),
                    _read_tool_use("r3", file_path="/c.py", limit=5),
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
            _turn("m1", [_read_tool_use("r1", file_path="/a.py")], usage={"output_tokens": 1}),
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
            [_turn("m1", [_read_tool_use("r1", file_path="/a.py")]), _read_result("r1", _HUNDRED_TOKEN_TEXT)],
            [(_TRACKED_AGENT_TYPE, [
                _turn("s1", [_read_tool_use("r2", file_path="/a.py")]),
                _read_result("r2", _HUNDRED_TOKEN_TEXT),
            ])],
            _weights(),
        )
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].seen_elsewhere_weight == pytest.approx(100 * 12.5)

    def test_main_thread_partial_read_does_not_make_a_path_seen_elsewhere_for_a_subagent(self):
        totals = read_cost._ReadCostTotals()
        read_cost._accumulate_session(
            totals, read_cost._SeenIds(),
            [
                _turn("m1", [_read_tool_use("r1", file_path="/a.py", limit=10)]),
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
                _read_tool_use("b-r3", file_path="/a.py"),
                _read_tool_use("b-r4", file_path="/b.py"),
                _read_tool_use("b-r5", file_path="/c.py"),
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
        assert totals.rows_by_agent_type[_TRACKED_AGENT_TYPE].seen_elsewhere_weight == pytest.approx(2 * 100 * 12.5)

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
        assert totals.rows_by_agent_type[second_scanned_type].seen_elsewhere_weight == pytest.approx(100 * 12.5)

    def test_subagent_transcript_repeated_in_a_second_file_is_billed_and_read_once(self):
        window_records = [
            _turn("s1", [_read_tool_use("r1", file_path="/a.py")], usage={"output_tokens": 1}),
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


class TestAgentTypeFor:
    def test_reads_agent_type_from_the_sibling_meta_json(self, tmp_path):
        (tmp_path / "agent-a.jsonl").write_text("")
        (tmp_path / "agent-a.meta.json").write_text('{"agentType": "Plan"}')
        assert read_cost._agent_type_for(tmp_path / "agent-a.jsonl") == "Plan"

    @pytest.mark.parametrize("meta_text", [None, "not json", "[]", '{"agentType": ""}', '{"agentType": 3}', "{}"])
    def test_missing_malformed_or_untyped_meta_is_unknown(self, tmp_path, meta_text):
        (tmp_path / "agent-a.jsonl").write_text("")
        if meta_text is not None:
            (tmp_path / "agent-a.meta.json").write_text(meta_text)
        assert read_cost._agent_type_for(tmp_path / "agent-a.jsonl") == read_cost._UNKNOWN_AGENT_TYPE

    def test_missing_meta_is_not_a_scan_gap(self, tmp_path):
        scan_gaps: Counter = Counter()
        assert read_cost._agent_type_for(tmp_path / "agent-a.jsonl", scan_gaps) == read_cost._UNKNOWN_AGENT_TYPE
        assert scan_gaps == Counter()

    def test_meta_that_is_not_valid_json_is_a_scan_gap(self, tmp_path):
        (tmp_path / "agent-a.meta.json").write_text("not json")
        scan_gaps: Counter = Counter()
        assert read_cost._agent_type_for(tmp_path / "agent-a.jsonl", scan_gaps) == read_cost._UNKNOWN_AGENT_TYPE
        assert scan_gaps == Counter({read_cost._GAP_SUBAGENT_META: 1})

    def test_meta_that_is_not_utf8_is_a_scan_gap(self, tmp_path):
        (tmp_path / "agent-a.meta.json").write_bytes(b"\xff\xfe")
        scan_gaps: Counter = Counter()
        assert read_cost._agent_type_for(tmp_path / "agent-a.jsonl", scan_gaps) == read_cost._UNKNOWN_AGENT_TYPE
        assert scan_gaps == Counter({read_cost._GAP_SUBAGENT_META: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_meta_is_a_scan_gap(self, tmp_path):
        meta_path = tmp_path / "agent-a.meta.json"
        meta_path.write_text('{"agentType": "Plan"}')
        os.chmod(meta_path, 0o000)
        scan_gaps: Counter = Counter()
        try:
            agent_type = read_cost._agent_type_for(tmp_path / "agent-a.jsonl", scan_gaps)
        finally:
            os.chmod(meta_path, 0o644)  # restore before tmp_path teardown
        assert agent_type == read_cost._UNKNOWN_AGENT_TYPE
        assert scan_gaps == Counter({read_cost._GAP_SUBAGENT_META: 1})

    def test_meta_that_parses_without_an_agent_type_is_not_a_scan_gap(self, tmp_path):
        (tmp_path / "agent-a.meta.json").write_text("{}")
        scan_gaps: Counter = Counter()
        assert read_cost._agent_type_for(tmp_path / "agent-a.jsonl", scan_gaps) == read_cost._UNKNOWN_AGENT_TYPE
        assert scan_gaps == Counter()


class TestReadSubagentWindows:
    def _subagent_dir(self, tmp_path):
        subagent_dir = tmp_path / "sess" / "subagents"
        subagent_dir.mkdir(parents=True)
        return subagent_dir

    def test_yields_one_window_per_transcript_in_file_name_order_and_ignores_sidecars(self, tmp_path):
        subagent_dir = self._subagent_dir(tmp_path)
        for agent_id, agent_type in (("agent-b", "Plan"), ("agent-a", _TRACKED_AGENT_TYPE)):
            _write_jsonl(subagent_dir / f"{agent_id}.jsonl", [_turn(f"m-{agent_id}")])
            (subagent_dir / f"{agent_id}.meta.json").write_text(f'{{"agentType": "{agent_type}"}}')

        windows = read_cost._read_subagent_windows(tmp_path / "sess.jsonl", Counter())

        assert [(agent_type, records[0]["message"]["id"]) for agent_type, records in windows] == [
            (_TRACKED_AGENT_TYPE, "m-agent-a"), ("Plan", "m-agent-b"),
        ]

    def test_a_transcript_is_not_parsed_until_the_window_before_it_is_consumed(self, tmp_path, monkeypatch):
        subagent_dir = self._subagent_dir(tmp_path)
        for agent_id in ("agent-a", "agent-b"):
            _write_jsonl(subagent_dir / f"{agent_id}.jsonl", [_turn(f"m-{agent_id}")])
        parsed_file_names: list[str] = []
        parse_jsonl_records = corpus._parse_jsonl_records

        def recording_parse(path):
            parsed_file_names.append(path.name)
            return parse_jsonl_records(path)

        monkeypatch.setattr(corpus, "_parse_jsonl_records", recording_parse)

        windows = read_cost._read_subagent_windows(tmp_path / "sess.jsonl", Counter())
        next(windows)
        assert parsed_file_names == ["agent-a.jsonl"]

        next(windows)
        assert parsed_file_names == ["agent-a.jsonl", "agent-b.jsonl"]

    def test_session_without_a_subagent_directory_yields_nothing_and_records_no_gap(self, tmp_path):
        scan_gaps: Counter = Counter()
        assert list(read_cost._read_subagent_windows(tmp_path / "sess.jsonl", scan_gaps)) == []
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unlistable_subagent_directory_yields_nothing_and_records_a_gap(self, tmp_path):
        subagent_dir = self._subagent_dir(tmp_path)
        _write_jsonl(subagent_dir / "agent-a.jsonl", [_turn("m1")])
        os.chmod(subagent_dir, 0o000)
        scan_gaps: Counter = Counter()
        try:
            windows = list(read_cost._read_subagent_windows(tmp_path / "sess.jsonl", scan_gaps))
        finally:
            os.chmod(subagent_dir, 0o755)  # restore before tmp_path teardown
        assert windows == []
        assert scan_gaps == Counter({read_cost._GAP_SUBAGENT_DIR: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_transcript_is_skipped_and_recorded_without_reading_its_sidecar(self, tmp_path):
        subagent_dir = self._subagent_dir(tmp_path)
        _write_jsonl(subagent_dir / "agent-a.jsonl", [_turn("m1")])
        (subagent_dir / "agent-a.meta.json").write_text("not json")
        _write_jsonl(subagent_dir / "agent-b.jsonl", [_turn("m2")])
        os.chmod(subagent_dir / "agent-a.jsonl", 0o000)
        scan_gaps: Counter = Counter()
        try:
            windows = list(read_cost._read_subagent_windows(tmp_path / "sess.jsonl", scan_gaps))
        finally:
            os.chmod(subagent_dir / "agent-a.jsonl", 0o644)
        assert [records[0]["message"]["id"] for _agent_type, records in windows] == ["m2"]
        assert scan_gaps == Counter({read_cost._GAP_SUBAGENT_FILE: 1})


class TestScopeRefusal:
    def test_refused_when_more_than_one_root_is_in_scope(self, fake_projects, tmp_path, capsys):
        other_root = tmp_path / "other-account" / "projects"
        other_root.mkdir(parents=True)
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_report(_read_cost_args(), [fake_projects.parent, other_root])
        assert raised.value.code == 2
        captured = capsys.readouterr()
        assert "more than one root" in captured.err
        assert captured.out == ""

    def test_refused_without_this_repo(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_report(_read_cost_args(this_repo=False), [fake_projects.parent])
        assert raised.value.code == 2
        assert "requires --this-repo" in capsys.readouterr().err

    def test_refused_with_a_projects_glob(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_report(_read_cost_args(projects="-home-*"), [fake_projects.parent])
        assert raised.value.code == 2
        assert "refuses any --projects scope" in capsys.readouterr().err

    @pytest.mark.usefixtures("only_the_built_in_agent_types_are_tracked")
    def test_cmd_scans_the_active_account_alone_even_when_other_roots_are_declared(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        declared = tmp_path / "declared-account"
        (declared / "projects" / _PROJECT_SLUG).mkdir(parents=True)
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{declared}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_jsonl(declared / "projects" / _PROJECT_SLUG / "other.jsonl", [_turn("m9")])

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "sessions=1 " in out

    @pytest.mark.usefixtures("only_the_built_in_agent_types_are_tracked")
    def test_header_states_the_active_account_this_repository_and_the_unconsulted_roots_file(
        self, fake_projects, capsys,
    ):
        read_cost.cmd_read_cost(_read_cost_args())

        assert capsys.readouterr().out.splitlines()[0] == (
            "READ COST SOURCES (this repo (1 project dirs); reads one account (the active account),"
            " this repository only; ~/.claude/transcript-config-dirs not consulted)"
        )

    def test_roots_are_the_active_config_dirs_projects_directory(self, fake_projects):
        assert read_cost._read_cost_roots() == [fake_projects.parent]

    def test_unresolvable_config_dir_exits_2(self, monkeypatch, capsys):
        def unresolvable_config_dir():
            raise ValueError("CLAUDE_CONFIG_DIR is not absolute")

        monkeypatch.setattr(scope, "config_dir", unresolvable_config_dir)
        with pytest.raises(SystemExit) as raised:
            read_cost._read_cost_roots()
        assert raised.value.code == 2
        assert capsys.readouterr().err == "read-cost: CLAUDE_CONFIG_DIR is not absolute\n"


@pytest.fixture()
def only_the_built_in_agent_types_are_tracked(monkeypatch):
    monkeypatch.setattr(redaction, "_repo_tracked_agent_type_names", lambda: redaction._BUILT_IN_AGENT_TYPES)


@pytest.fixture()
def only_the_tracked_agent_type_is_tracked(monkeypatch):
    tracked = frozenset({_TRACKED_AGENT_TYPE}) | redaction._BUILT_IN_AGENT_TYPES
    monkeypatch.setattr(redaction, "_repo_tracked_agent_type_names", lambda: tracked)


_SKIPPED_INPUT_KEYS = (
    "unreadable_roots", "unreadable_project_dirs", "unreadable_main_files", "unreadable_subagent_dirs",
    "unreadable_subagent_files", "unreadable_subagent_meta", "invalid_usage_turns",
)
_NO_SKIPPED_INPUT = dict.fromkeys(_SKIPPED_INPUT_KEYS, 0)


def _skipped_input_counts(report: str) -> dict[str, int]:
    line = next(line for line in report.splitlines() if line.startswith("skipped input:"))
    return {key: int(value.replace(",", "")) for key, value in (token.split("=") for token in line.split()[2:])}


def _lines_after(report: str, header: str, count: int) -> list[str]:
    lines = report.splitlines()
    start = lines.index(header) + 1
    return lines[start:start + count]


@pytest.mark.usefixtures("only_the_tracked_agent_type_is_tracked")
class TestSkippedInput:
    def _run(self, capsys) -> str:
        read_cost.cmd_read_cost(_read_cost_args())
        return capsys.readouterr().out

    def test_clean_corpus_reports_zero_for_every_count(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1", usage={"output_tokens": 1})])
        assert _skipped_input_counts(self._run(capsys)) == _NO_SKIPPED_INPUT

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_projects_directory_is_counted_as_an_unreadable_root(self, fake_projects, capsys):
        projects_dir = fake_projects.parent
        os.chmod(projects_dir, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(projects_dir, 0o755)  # restore before tmp_path teardown
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_roots": 1}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_project_directory_is_counted(self, fake_projects, capsys):
        os.chmod(fake_projects, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(fake_projects, 0o755)
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_project_dirs": 1}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_main_transcript_is_counted(self, fake_projects, capsys):
        main_transcript = fake_projects / "sess.jsonl"
        _write_jsonl(main_transcript, [_turn("m1")])
        os.chmod(main_transcript, 0o000)
        try:
            out = self._run(capsys)
        finally:
            os.chmod(main_transcript, 0o644)
        assert _skipped_input_counts(out) == {**_NO_SKIPPED_INPUT, "unreadable_main_files": 1}
        assert "sessions=0 " in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unlistable_subagent_directory_is_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", [_turn("s1")])
        subagent_dir = fake_projects / "sess" / "subagents"
        os.chmod(subagent_dir, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(subagent_dir, 0o755)
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_subagent_dirs": 1}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_subagent_transcript_is_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", [_turn("s1")])
        subagent_transcript = fake_projects / "sess" / "subagents" / "agent-a.jsonl"
        os.chmod(subagent_transcript, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(subagent_transcript, 0o644)
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_subagent_files": 1}

    def test_unreadable_meta_is_counted_beside_the_withheld_row_its_agent_folds_into(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", [_turn("s1")])
        (fake_projects / "sess" / "subagents" / "agent-a.meta.json").write_text("not json")

        out = self._run(capsys)

        assert _skipped_input_counts(out) == {**_NO_SKIPPED_INPUT, "unreadable_subagent_meta": 1}
        withheld_row = f"{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}: agents=1 "
        withheld_index = next(i for i, line in enumerate(out.splitlines()) if line.startswith(withheld_row))
        assert out.splitlines()[withheld_index + 2] == (
            "    also holds agents with a missing, unreadable or typeless .meta.json; unreadable: 1"
        )

    def test_missing_meta_is_not_counted_as_unreadable(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", [_turn("s1")])
        (fake_projects / "sess" / "subagents" / "agent-a.meta.json").unlink()

        assert _skipped_input_counts(self._run(capsys)) == _NO_SKIPPED_INPUT

    def test_turn_with_invalid_usage_is_counted_and_not_billed(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _turn("m1", usage={"output_tokens": -1}), _turn("m2", usage={"output_tokens": 1}),
        ])

        out = self._run(capsys)

        assert _skipped_input_counts(out) == {**_NO_SKIPPED_INPUT, "invalid_usage_turns": 1}
        assert "sessions=1 api_calls=1" in out


@pytest.mark.usefixtures("only_the_tracked_agent_type_is_tracked")
class TestReport:
    def _write_corpus(self, fake_projects) -> None:
        main_records = [
            _turn("m1", [_read_tool_use("r1", file_path="/sentinel/dir/big.py")], usage={"output_tokens": 10}),
            _read_result("r1", "line\n" * 1000),
            _turn("m2", [_edit_use("e1", path="/sentinel/dir/big.py")], usage={"output_tokens": 10}),
        ]
        _write_jsonl(fake_projects / "sentinel-session-id.jsonl", main_records)
        _write_subagent_dispatch(
            fake_projects, "sentinel-session-id", "agent-tracked", "tool-1",
            [
                _turn("s1", [_read_tool_use("a1", file_path="/sentinel/dir/big.py")], usage={"output_tokens": 10}),
                _read_result("a1", "line\n" * 1000),
            ],
            agent_type=_TRACKED_AGENT_TYPE,
        )
        _write_subagent_dispatch(
            fake_projects, "sentinel-session-id", "agent-untracked", "tool-2",
            [
                _turn("s2", [_read_tool_use("a2", file_path="/sentinel/dir/other.py")], usage={"output_tokens": 10}),
                _read_result("a2", "line\n" * 1000),
            ],
            agent_type=_UNTRACKED_AGENT_TYPE,
        )

    def test_report_prints_the_price_ratio_note_and_aggregates_without_identifiers(
        self, fake_projects, capsys,
    ):
        self._write_corpus(fake_projects)

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "One price ratio" in out
        assert "sessions=1 api_calls=4" in out
        assert "## Main-thread whole-file reads over 800 lines: 1" in out
        assert "edited_later=True: n=1" in out
        for withheld in ("sentinel", "big.py", "other.py", _UNTRACKED_AGENT_TYPE, "tool-1", str(fake_projects)):
            assert withheld not in out

    def test_tracked_agent_type_prints_by_name_and_untracked_folds_into_the_withheld_row(
        self, fake_projects, capsys,
    ):
        self._write_corpus(fake_projects)

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert f"\n{_TRACKED_AGENT_TYPE}: agents=1 " in out
        assert f"\n{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}: agents=1 " in out
        assert "\nTOTAL: agents=2 " in out

    def test_agent_type_named_only_by_the_allowlist_seam_prints_by_name(self, fake_projects, monkeypatch, capsys):
        allowlist_only_type = "sentinel-allowlist-only-agent-type"
        monkeypatch.setattr(redaction, "_repo_tracked_agent_type_names", lambda: frozenset({allowlist_only_type}))
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a", "tool-1", [_turn("s1")], agent_type=allowlist_only_type,
        )

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert f"\n{allowlist_only_type}: agents=1 " in out
        assert f"\n{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}:" not in out

    def test_empty_corpus_prints_zero_shares_without_division_error(self, fake_projects, capsys):
        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "sessions=0 api_calls=0" in out
        assert "billed share: main=0.0% subagent=0.0%" in out
        assert "edited_later=False: n=0 tokens=0 median_remaining_turns=0" in out
        assert "\nTOTAL: agents=0 " in out

    def test_resent_session_reports_the_repeated_reads_it_skipped(self, fake_projects, capsys):
        resent_records = _read_exchange("m1", "r1", "/a.py")
        _write_jsonl(fake_projects / "sess-a.jsonl", resent_records)
        _write_jsonl(fake_projects / "sess-b.jsonl", resent_records)

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "sessions=2 " in out
        assert "main-thread whole-file reads scanned=1\n" in out
        sessions_line = next(line for line in out.splitlines() if line.startswith("sessions="))
        assert sessions_line.endswith(" repeated_reads_skipped=1")


@pytest.mark.usefixtures("only_the_tracked_agent_type_is_tracked")
class TestPrintedFigures:
    """Shares and denominators over a hand-computed fixture: total billed 75000, main billed 50000.

    A main-thread whole-file read of `r` remaining turns and 200 tokens weighs 200 * (12.5 + r).
    """

    @staticmethod
    def _main_read(lines: int, remaining_turns: int, *, edited_later: bool) -> read_cost._ReadEvent:
        return read_cost._ReadEvent(
            path="/p.py", whole_file=True, tokens=200, lines=lines,
            remaining_turns=remaining_turns, edited_later=edited_later,
        )

    def _print(self, totals: read_cost._ReadCostTotals, tracked: frozenset[str], capsys) -> str:
        read_cost._print_read_cost_report(totals, _weights(), tracked)
        return capsys.readouterr().out

    def _hand_computed_totals(self) -> read_cost._ReadCostTotals:
        totals = read_cost._ReadCostTotals(billed_main=50000.0, billed_subagent=25000.0)
        totals.main_whole_file_events = [
            self._main_read(350, 2, edited_later=False),
            self._main_read(351, 4, edited_later=False),
            self._main_read(800, 1, edited_later=True),
            self._main_read(801, 3, edited_later=True),
            self._main_read(801, 6, edited_later=False),
        ]
        totals.rows_by_agent_type = {
            _TRACKED_AGENT_TYPE: read_cost._AgentTypeRow(
                agents=2, turns=10, billed=7500.0, whole_file_reads=3, whole_file_tokens=600,
                whole_file_weight=3000.0, all_read_weight=4500.0, large_weight=1500.0,
                large_unedited_weight=750.0, repeat_in_agent_weight=450.0, seen_elsewhere_weight=600.0,
                kind_weight=Counter({read_cost._KIND_INSTRUCTION: 1200.0, read_cost._KIND_CODE_OR_OTHER: 1800.0}),
            ),
        }
        return totals

    def test_billed_share_splits_main_from_subagent_over_total_billed(self, capsys):
        out = self._print(self._hand_computed_totals(), frozenset({_TRACKED_AGENT_TYPE}), capsys)
        assert "billed share: main=66.7% subagent=33.3%" in out

    def test_cohort_over_350_lines_excludes_exactly_350_and_splits_by_edited_later(self):
        totals = self._hand_computed_totals()

        cohort = read_cost._cohort_figures(
            totals.main_whole_file_events, 350, _weights(), totals.billed_total, totals.billed_main,
        )

        assert cohort.reads == 4
        assert cohort.edited_later.reads == 2
        assert cohort.edited_later.tokens == 400
        assert cohort.edited_later.median_remaining_turns == 3
        assert cohort.edited_later.share_of_total_pct == pytest.approx(100 * 5800 / 75000)
        assert cohort.edited_later.share_of_main_pct == pytest.approx(11.6)
        assert cohort.never_edited.reads == 2
        assert cohort.never_edited.tokens == 400
        assert cohort.never_edited.median_remaining_turns == 6
        assert cohort.never_edited.share_of_total_pct == pytest.approx(100 * 7000 / 75000)
        assert cohort.never_edited.share_of_main_pct == pytest.approx(14.0)

    def test_cohort_over_800_lines_excludes_exactly_800_and_splits_by_edited_later(self):
        totals = self._hand_computed_totals()

        cohort = read_cost._cohort_figures(
            totals.main_whole_file_events, 800, _weights(), totals.billed_total, totals.billed_main,
        )

        assert cohort.reads == 2
        assert cohort.edited_later.reads == 1
        assert cohort.edited_later.tokens == 200
        assert cohort.edited_later.median_remaining_turns == 3
        assert cohort.edited_later.share_of_total_pct == pytest.approx(100 * 3100 / 75000)
        assert cohort.edited_later.share_of_main_pct == pytest.approx(6.2)
        assert cohort.never_edited.reads == 1
        assert cohort.never_edited.tokens == 200
        assert cohort.never_edited.median_remaining_turns == 6
        assert cohort.never_edited.share_of_total_pct == pytest.approx(100 * 3700 / 75000)
        assert cohort.never_edited.share_of_main_pct == pytest.approx(7.4)

    def test_prints_exactly_the_350_and_800_line_cohorts_in_that_order(self, capsys):
        out = self._print(self._hand_computed_totals(), frozenset({_TRACKED_AGENT_TYPE}), capsys)

        cohort_headers = [line for line in out.splitlines() if line.startswith("## Main-thread whole-file reads over")]
        assert cohort_headers == [
            "## Main-thread whole-file reads over 350 lines: 4",
            "## Main-thread whole-file reads over 800 lines: 2",
        ]

    def test_cohort_with_no_reads_has_zero_figures(self):
        cohort = read_cost._cohort_figures([], 350, _weights(), 0.0, 0.0)

        assert cohort.reads == 0
        for subset in (cohort.edited_later, cohort.never_edited):
            assert subset.reads == 0
            assert subset.tokens == 0
            assert subset.median_remaining_turns == 0
            assert subset.share_of_total_pct == 0.0
            assert subset.share_of_main_pct == 0.0

    def test_agent_type_figures_divide_each_weight_by_its_denominator(self):
        row = self._hand_computed_totals().rows_by_agent_type[_TRACKED_AGENT_TYPE]

        figures = read_cost._agent_type_figures(row, 75000.0)

        assert figures == read_cost._AgentTypeFigures(
            agents=2,
            whole_file_reads=3,
            whole_file_tokens=600,
            mean_turns=5.0,
            share_of_total_spend_pct=pytest.approx(10.0),
            whole_file_weight_share_pct=pytest.approx(4.0),
            all_read_weight_share_pct=pytest.approx(6.0),
            large_pct=pytest.approx(50.0),
            large_unedited_pct=pytest.approx(25.0),
            repeat_in_agent_pct=pytest.approx(15.0),
            seen_elsewhere_pct=pytest.approx(20.0),
            kind_pcts={
                read_cost._KIND_CODE_OR_OTHER: pytest.approx(60.0), read_cost._KIND_INSTRUCTION: pytest.approx(40.0),
            },
        )

    def test_agent_type_with_no_agents_and_no_reads_has_zero_figures(self):
        figures = read_cost._agent_type_figures(read_cost._AgentTypeRow(), 0.0)

        assert figures == read_cost._AgentTypeFigures(
            agents=0,
            whole_file_reads=0,
            whole_file_tokens=0,
            mean_turns=0.0,
            share_of_total_spend_pct=0.0,
            whole_file_weight_share_pct=0.0,
            all_read_weight_share_pct=0.0,
            large_pct=0.0,
            large_unedited_pct=0.0,
            repeat_in_agent_pct=0.0,
            seen_elsewhere_pct=0.0,
            kind_pcts={},
        )

    def test_printed_cohort_and_agent_type_lines_keep_their_published_layout(self, capsys):
        out = self._print(self._hand_computed_totals(), frozenset({_TRACKED_AGENT_TYPE}), capsys)

        assert (
            "\n## Main-thread whole-file reads over 800 lines: 2\n"
            "  edited_later=True: n=1 tokens=200 median_remaining_turns=3 share_of_total=4.1% share_of_main=6.2%\n"
            "  edited_later=False: n=1 tokens=200 median_remaining_turns=6 share_of_total=4.9% share_of_main=7.4%\n"
        ) in out
        assert (
            f"\n{_TRACKED_AGENT_TYPE}: agents=2 mean_turns=5 share_of_total_spend=10.0% whole_file_reads=3"
            " whole_file_tokens=600 whole_file_weight_share=4.0% all_read_weight_share=6.0%\n"
            "    of whole: large=50.0% large_unedited=25.0% repeat_in_agent=15.0% seen_elsewhere=20.0%"
            " kinds: code/other=60.0% instruction=40.0%\n"
        ) in out

    def test_every_tracked_agent_type_prints_a_row(self, capsys):
        tracked_names = [f"tracked-agent-{index:02d}" for index in range(20)]
        totals = read_cost._ReadCostTotals(billed_main=1.0)
        totals.rows_by_agent_type = {
            name: read_cost._AgentTypeRow(agents=1, whole_file_weight=float(index))
            for index, name in enumerate(tracked_names)
        }

        out = self._print(totals, frozenset(tracked_names), capsys)

        assert all(f"\n{name}: agents=1 " in out for name in tracked_names)

    def test_size_bucket_table_prints_each_scope_and_label_cell(self, capsys):
        totals = read_cost._ReadCostTotals(billed_main=1.0)
        cell_counts = {
            (read_cost._SCOPE_MAIN, "<2000"): 1, (read_cost._SCOPE_MAIN, "2000-4999"): 2,
            (read_cost._SCOPE_MAIN, "5000+"): 3, (read_cost._SCOPE_SUBAGENT, "<2000"): 4,
            (read_cost._SCOPE_SUBAGENT, "2000-4999"): 5, (read_cost._SCOPE_SUBAGENT, "5000+"): 6,
        }
        for key, count in cell_counts.items():
            totals.bucket_calls[key] = count
            totals.bucket_tokens[key] = count * 1000

        out = self._print(totals, frozenset(), capsys)

        assert _lines_after(out, "## Whole-file reads by size bucket (est. tokens)", 6) == [
            "  main      <2000      calls=1 tokens=1,000",
            "  main      2000-4999  calls=2 tokens=2,000",
            "  main      5000+      calls=3 tokens=3,000",
            "  subagent  <2000      calls=4 tokens=4,000",
            "  subagent  2000-4999  calls=5 tokens=5,000",
            "  subagent  5000+      calls=6 tokens=6,000",
        ]

    def test_tracked_agent_types_print_by_descending_whole_file_weight_then_name(self, capsys):
        totals = read_cost._ReadCostTotals(billed_main=1.0)
        totals.rows_by_agent_type = {
            "b-tie": read_cost._AgentTypeRow(agents=1, whole_file_weight=3.0),
            "light": read_cost._AgentTypeRow(agents=1, whole_file_weight=1.0),
            "a-tie": read_cost._AgentTypeRow(agents=1, whole_file_weight=3.0),
            "heavy": read_cost._AgentTypeRow(agents=1, whole_file_weight=5.0),
        }

        out = self._print(totals, frozenset(totals.rows_by_agent_type), capsys)

        printed_labels = [line.split(":")[0] for line in out.splitlines() if ": agents=" in line]
        assert printed_labels == ["TOTAL", "heavy", "a-tie", "b-tie", "light"]
