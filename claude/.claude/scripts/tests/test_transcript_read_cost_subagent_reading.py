"""Tests for read_cost's subagent sidecar and transcript reading."""
import os
from collections import Counter

import pytest
from transcript_analysis import corpus, read_cost

from ._read_cost_helpers import (
    _TRACKED_AGENT_TYPE,
    _turn,
)
from .conftest import (
    _write_jsonl,
)


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
