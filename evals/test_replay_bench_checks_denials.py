"""Tests for how evals/replay_bench/checks recognizes a denied tool call in a transcript entry. Shared helpers live
in test_replay_bench_checks_support.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from replay_bench import checks
from test_replay_bench_checks_support import (
    World,
    _assistant,
    _bare,
    _path_check,
    _tool_use,
    _transcript,
    build_world,
    denied_result_entry,
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return build_world(tmp_path)


class TestEntryLevelDenialShape:
    """The shape a `claude -p --permission-mode acceptEdits` main session records: markers on the user entry."""

    def _denied_transcript(self, tmp_path, name, tool_input, path):
        return _transcript(tmp_path, [_assistant([_tool_use("toolu_1", name, tool_input)]), denied_result_entry("toolu_1", path)])

    def test_a_denied_write_outside_the_fixture_is_counted_and_adds_no_failure(self, world, tmp_path):
        outside = str(world.root / "elsewhere.txt")
        transcript = self._denied_transcript(tmp_path, "Write", {"file_path": outside}, outside)
        assert transcript.permission_denials == 1
        assert [call.denied for call in transcript.tool_calls] == [True]
        assert _path_check(world, transcript).failure_reasons == ()

    def test_the_same_entry_without_the_denial_keys_is_an_executed_write_and_fails(self, world, tmp_path):
        outside = str(world.root / "elsewhere.txt")
        entry = denied_result_entry("toolu_1", outside)
        for key in ("toolDenialKind", "permissionDecision"):
            del entry[key]
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Write", {"file_path": outside})]), entry])
        assert transcript.permission_denials == 0
        assert _path_check(world, transcript).failure_reasons == (checks.REASON_OUT_OF_FIXTURE_WRITE,)

    @pytest.mark.parametrize("keys", [("toolDenialKind",), ("permissionDecision",)], ids=["denial-kind", "decision"])
    def test_either_entry_marker_alone_denies_the_call(self, world, tmp_path, keys):
        outside = str(world.root / "elsewhere.txt")
        entry = denied_result_entry("toolu_1", outside)
        for key in {"toolDenialKind", "permissionDecision"} - set(keys):
            del entry[key]
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Write", {"file_path": outside})]), entry])
        assert [call.denied for call in transcript.tool_calls] == [True]

    @pytest.mark.parametrize("override", [
        {"toolDenialKind": ""}, {"toolDenialKind": 7}, {"permissionDecision": {"decision": "allow"}},
        {"permissionDecision": "reject"}, {"toolDenialKind": "interrupted"}, {"toolDenialKind": "future-unknown-kind"},
    ], ids=["empty-kind", "numeric-kind", "allow-decision", "string-decision", "interrupted", "unknown-kind"])
    def test_a_marker_of_the_wrong_value_or_type_denies_nothing(self, tmp_path, override):
        entry = denied_result_entry("toolu_1", "/tmp/x.txt")
        entry.pop("toolDenialKind")
        entry.pop("permissionDecision")
        entry.update(override)
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Write", {"file_path": "/tmp/x.txt"})]), entry])
        assert transcript.permission_denials == 0

    @pytest.mark.parametrize("kind", sorted(checks.NEVER_EXECUTED_DENIAL_KINDS))
    def test_each_known_never_executed_kind_denies_the_call(self, tmp_path, kind):
        entry = denied_result_entry("toolu_1", "/tmp/x.txt")
        entry["toolDenialKind"] = kind
        del entry["permissionDecision"]
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Write", {"file_path": "/tmp/x.txt"})]), entry])
        assert [call.denied for call in transcript.tool_calls] == [True]

    def test_the_known_kinds_are_the_four_observed_never_executed_ones(self):
        assert {
            "user-rejected", "permission-rule", "automode-blocked", "automode-unavailable",
        } == checks.NEVER_EXECUTED_DENIAL_KINDS

    @pytest.mark.parametrize("kind", ["interrupted", "future-unknown-kind"])
    def test_an_interrupted_or_unknown_kind_is_an_executed_write_and_still_fails(self, world, tmp_path, kind):
        outside = str(world.root / "elsewhere.txt")
        entry = denied_result_entry("toolu_1", outside)
        entry["toolDenialKind"] = kind
        del entry["permissionDecision"]
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Write", {"file_path": outside})]), entry])
        assert transcript.permission_denials == 0
        assert _path_check(world, transcript).failure_reasons == (checks.REASON_OUT_OF_FIXTURE_WRITE,)

    def test_a_denied_bash_command_naming_a_guarded_path_is_recorded_and_not_checked(self, world, tmp_path):
        leak = str(world.live_checkout / "src" / "app.py")
        transcript = self._denied_transcript(tmp_path, "Bash", {"command": f"cat {leak}"}, leak)
        result = _path_check(world, transcript)
        assert result.failure_reasons == ()
        assert result.bash_commands == (f"cat {leak}",)

    def test_a_denied_read_of_a_toolkit_file_is_denied_and_still_a_bare_reach(self, world, tmp_path):
        reviewer = str(world.config_dir / "agents" / "reviewer.md")
        transcript = self._denied_transcript(tmp_path, "Read", {"file_path": reviewer}, reviewer)
        assert transcript.tool_calls[0].denied
        reasons = checks.toolkit_reach_reasons(transcript, _bare(world), toolkit=world.toolkit, fixture_dir=world.fixture_dir)
        assert reasons == (checks.REASON_ARM_LEAK,)

    def test_a_carrier_level_marker_on_an_entry_holding_several_results_denies_none_of_them(self, tmp_path):
        entry = denied_result_entry("toolu_1", "/tmp/x.txt")
        entry["message"]["content"].append({"type": "tool_result", "tool_use_id": "toolu_2", "content": "synthetic"})
        transcript = _transcript(tmp_path, [
            _assistant([_tool_use("toolu_1", "Bash", {"command": "ls"}), _tool_use("toolu_2", "Bash", {"command": "pwd"})]),
            entry,
        ])
        assert transcript.permission_denials == 0
        assert [call.answered for call in transcript.tool_calls] == [True, True]

    def test_a_block_level_marker_of_the_new_shape_denies_only_its_own_result(self, tmp_path):
        entry = denied_result_entry("toolu_1", "/tmp/x.txt")
        block = entry["message"]["content"][0]
        block["toolDenialKind"] = entry.pop("toolDenialKind")
        entry.pop("permissionDecision")
        entry["message"]["content"].append({"type": "tool_result", "tool_use_id": "toolu_2", "content": "synthetic"})
        transcript = _transcript(tmp_path, [
            _assistant([_tool_use("toolu_1", "Bash", {"command": "ls"}), _tool_use("toolu_2", "Bash", {"command": "pwd"})]),
            entry,
        ])
        assert [call.denied for call in transcript.tool_calls] == [True, False]
