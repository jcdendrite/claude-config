"""Tests for evals/replay_bench/checks over the subagent transcript: first-turn size, tool-call paths, toolkit reach,
Read grants, denials and the exclude note. Shared helpers live in test_replay_bench_checks_support.
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
from replay_bench import checks
from replay_bench.checks import (
    InstructionLog,
)
from test_replay_bench_checks_support import (
    ABSENT,
    AGENT_ID,
    PATH_GLOB_MATCH,
    World,
    _assistant,
    _bare,
    _bash_transcript,
    _call_and_result,
    _dispatcher_claude_md,
    _line,
    _link,
    _parse,
    _path_check,
    _result,
    _send_back_attachment,
    _tool_use,
    _toolkit_arm,
    _touch,
    _transcript,
    build_world,
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return build_world(tmp_path)


class TestFirstTurnPromptSize:
    def test_it_is_the_sum_of_the_three_usage_fields_of_the_first_assistant_entry(self, tmp_path):
        transcript = _transcript(tmp_path, [
            _assistant([], usage={"input_tokens": 11, "cache_creation_input_tokens": 22, "cache_read_input_tokens": 33}),
            _assistant([], usage={"input_tokens": 1000, "cache_creation_input_tokens": 1, "cache_read_input_tokens": 1}),
        ])
        assert transcript.first_turn_prompt_size == 66

    def test_it_is_null_when_one_field_is_absent(self, tmp_path):
        transcript = _transcript(tmp_path, [_assistant([], usage={"input_tokens": 11, "cache_read_input_tokens": 33})])
        assert transcript.first_turn_prompt_size is None

    def test_it_is_null_when_a_field_is_not_a_count(self, tmp_path):
        usage = {"input_tokens": 11, "cache_creation_input_tokens": True, "cache_read_input_tokens": 33}
        assert _transcript(tmp_path, [_assistant([], usage=usage)]).first_turn_prompt_size is None

    def test_it_is_null_when_a_field_is_negative(self, tmp_path):
        usage = {"input_tokens": 11, "cache_creation_input_tokens": -5000, "cache_read_input_tokens": 33}
        assert _transcript(tmp_path, [_assistant([], usage=usage)]).first_turn_prompt_size is None

    def test_it_is_null_when_all_three_fields_are_zero(self, tmp_path):
        usage = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        assert _transcript(tmp_path, [_assistant([], usage=usage)]).first_turn_prompt_size is None

    def test_it_is_null_when_the_transcript_holds_no_assistant_entry(self, tmp_path):
        assert _transcript(tmp_path, [_result("toolu_1")]).first_turn_prompt_size is None

    def test_a_send_back_attachment_before_the_first_assistant_entry_is_tolerated(self, tmp_path):
        transcript = _transcript(tmp_path, [_send_back_attachment(), "not json", _assistant([]), _send_back_attachment()])
        assert transcript.first_turn_prompt_size == 66
        assert transcript.tool_calls == ()


def _log_with_trigger(world: World, trigger: object) -> InstructionLog:
    return _parse(world, [
        _dispatcher_claude_md(world),
        _line(world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID, trigger_file_path=trigger),
    ])


def _note(world: World, tmp_path: Path, trigger: object, bare_entries: list[dict]) -> tuple[str, ...]:
    return checks.exclude_note_lines(
        _log_with_trigger(world, trigger), _transcript(tmp_path, bare_entries), toolkit=world.toolkit,
        toolkit_fixture_dir=world.fixture_dir, bare_fixture_dir=world.other_fixture_dir,
    )


class TestExcludeNote:
    def test_a_trigger_path_the_bare_transcript_read_prints_the_exercised_line_compared_per_fixture(self, world, tmp_path):
        trigger = str(world.fixture_dir / "src" / "app.py")
        read_in_bare_fixture = _call_and_result("toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "src" / "app.py")})
        assert _note(world, tmp_path, trigger, read_in_bare_fixture) == (
            checks.EXCLUDE_NOTE_EXERCISED, checks.EXCLUDE_NOTE_RESIDUAL,
        )

    def test_a_bare_transcript_with_no_such_read_prints_the_not_exercised_line(self, world, tmp_path):
        trigger = str(world.fixture_dir / "src" / "app.py")
        other_read = _call_and_result("toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "docs" / "other.md")})
        assert _note(world, tmp_path, trigger, other_read) == (
            checks.EXCLUDE_NOTE_NOT_EXERCISED, checks.EXCLUDE_NOTE_RESIDUAL,
        )

    def test_a_bare_transcript_whose_only_such_read_was_denied_prints_the_not_exercised_line(self, world, tmp_path):
        trigger = str(world.fixture_dir / "src" / "app.py")
        denied_read = _call_and_result(
            "toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "src" / "app.py")}, denied=True,
        )
        assert _note(world, tmp_path, trigger, denied_read) == (checks.EXCLUDE_NOTE_NOT_EXERCISED, checks.EXCLUDE_NOTE_RESIDUAL)

    @pytest.mark.parametrize("trigger", [ABSENT, 7, "/synthetic/a\x00b.py"], ids=["absent", "not-a-string", "nul-byte"])
    def test_a_trigger_field_that_is_absent_not_a_string_or_holds_a_nul_leaves_the_line_out_with_the_residual(
        self, world, tmp_path, trigger,
    ):
        read = _call_and_result("toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "src" / "app.py")})
        assert _note(world, tmp_path, trigger, read) == (checks.EXCLUDE_NOTE_RESIDUAL,)

    def test_a_bare_read_with_no_result_prints_the_not_exercised_line(self, world, tmp_path):
        trigger = str(world.fixture_dir / "src" / "app.py")
        unanswered = [_assistant([_tool_use("toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "src" / "app.py")})])]
        assert _note(world, tmp_path, trigger, unanswered) == (checks.EXCLUDE_NOTE_NOT_EXERCISED, checks.EXCLUDE_NOTE_RESIDUAL)

    def test_a_fixture_relative_trigger_path_is_compared_as_given(self, world, tmp_path):
        read = _call_and_result("toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "src" / "app.py")})
        assert _note(world, tmp_path, "src/./app.py", read) == (checks.EXCLUDE_NOTE_EXERCISED, checks.EXCLUDE_NOTE_RESIDUAL)

    @pytest.mark.parametrize("trigger", ["../escape.py", ".."])
    def test_a_relative_trigger_path_that_leaves_the_fixture_leaves_the_line_out(self, world, tmp_path, trigger):
        assert _note(world, tmp_path, trigger, []) == (checks.EXCLUDE_NOTE_RESIDUAL,)

    def test_a_trigger_path_reported_in_resolved_form_is_compared_through_a_symlinked_fixture_parent(self, world, tmp_path):
        linked_fixture = _link(tmp_path / "linked-toolkit-fixture", world.fixture_dir)
        log = _log_with_trigger(world, str(world.fixture_dir / "src" / "app.py"))
        read = _call_and_result("toolu_1", "Read", {"file_path": str(world.other_fixture_dir / "src" / "app.py")})
        note = checks.exclude_note_lines(
            log, _transcript(tmp_path, read), toolkit=world.toolkit,
            toolkit_fixture_dir=linked_fixture, bare_fixture_dir=world.other_fixture_dir,
        )
        assert note == (checks.EXCLUDE_NOTE_EXERCISED, checks.EXCLUDE_NOTE_RESIDUAL)

    def test_a_trigger_path_outside_the_toolkit_fixture_leaves_the_line_out(self, world, tmp_path):
        assert _note(world, tmp_path, "/synthetic/elsewhere/app.py", []) == (checks.EXCLUDE_NOTE_RESIDUAL,)

    def test_a_toolkit_log_whose_control_did_not_fire_prints_the_residual_alone(self, world, tmp_path):
        log = _parse(world, [_dispatcher_claude_md(world)])
        note = checks.exclude_note_lines(
            log, _transcript(tmp_path, []), toolkit=world.toolkit,
            toolkit_fixture_dir=world.fixture_dir, bare_fixture_dir=world.other_fixture_dir,
        )
        assert note == (checks.EXCLUDE_NOTE_RESIDUAL,)


class TestReadGrantRecords:
    def _records(self, world: World, tmp_path: Path, entries: list[dict]) -> checks.ReadGrantRecords:
        return checks.record_read_grants(_transcript(tmp_path, entries), toolkit=world.toolkit, fixture_dir=world.fixture_dir)

    def test_a_listed_read_that_ran_and_a_listed_read_denied_are_recorded(self, world, tmp_path):
        entries = [
            *_call_and_result("toolu_1", "Read", {"file_path": str(world.allow_file)}),
            *_call_and_result("toolu_2", "Read", {"file_path": str(world.second_allow_file)}, denied=True),
        ]
        assert self._records(world, tmp_path, entries).allow_list_reads == (
            checks.AllowListRead(str(world.allow_file), denied=False),
            checks.AllowListRead(str(world.second_allow_file), denied=True),
        )

    def test_a_denied_read_of_an_unlisted_toolkit_file_is_recorded_by_path(self, world, tmp_path):
        reviewer = str(world.config_dir / "agents" / "reviewer.md")
        entries = [
            *_call_and_result("toolu_1", "Read", {"file_path": reviewer}, denied=True),
            *_call_and_result("toolu_2", "Read", {"file_path": str(world.fixture_dir / "src" / "app.py")}, denied=True),
            *_call_and_result("toolu_3", "Read", {"file_path": str(world.config_dir / "skills" / "demo" / "SKILL.md")}),
        ]
        records = self._records(world, tmp_path, entries)
        assert records.denied_other_toolkit_reads == (reviewer,)
        assert records.allow_list_reads == ()

    @pytest.mark.parametrize("listed_form, read_form", [("link", "target"), ("target", "link")])
    def test_an_allow_list_entry_and_a_read_naming_the_same_file_by_link_and_target_match(
        self, world, tmp_path, listed_form, read_form,
    ):
        target = _touch(tmp_path / "checkout" / "agents" / "stowed.md")
        link = _link(tmp_path / "personal-link" / ".claude" / "agents" / "stowed.md", target)
        forms = {"link": link, "target": target}
        toolkit = checks.ToolkitPaths.from_config_dir(world.config_dir, [forms[listed_form]])
        entries = _call_and_result("toolu_1", "Read", {"file_path": str(forms[read_form])})
        records = checks.record_read_grants(_transcript(tmp_path, entries), toolkit=toolkit, fixture_dir=world.fixture_dir)
        assert records.allow_list_reads == (checks.AllowListRead(str(forms[read_form]), denied=False),)

    def test_a_read_with_no_result_is_in_neither_record(self, world, tmp_path):
        entries = [_assistant([_tool_use("toolu_1", "Read", {"file_path": str(world.allow_file)})])]
        assert self._records(world, tmp_path, entries) == checks.ReadGrantRecords((), ())


class TestPermissionDenials:
    def test_a_denied_call_is_counted_whichever_path_denied_it(self, tmp_path):
        # A hook denial and a permission-flow denial mark the result the same way, so one shape stands for both.
        transcript = _transcript(tmp_path, [
            *_call_and_result("toolu_1", "Bash", {"command": "ls"}, denied=True),
            *_call_and_result("toolu_2", "Edit", {"file_path": "src/app.py"}, denied=True),
            *_call_and_result("toolu_3", "Bash", {"command": "ls"}),
            _assistant([_tool_use("toolu_4", "Bash", {"command": "ls"})]),
            _result("toolu_4", denied=True, kind="another-kind"),
        ])
        assert transcript.permission_denials == 2
        assert [call.denied for call in transcript.tool_calls] == [True, True, False, False]

    def test_a_marker_on_the_enclosing_entry_denies_a_single_result(self, tmp_path):
        entry = _result("toolu_1")
        entry["tool_result_meta"] = [{"non_execution_kind": checks.NON_EXECUTION_PERMISSION_RULE}]
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Bash", {"command": "ls"})]), entry])
        assert transcript.permission_denials == 1
        assert transcript.tool_calls[0].denied

    def test_an_entry_marker_over_several_results_denies_none_of_them(self, tmp_path):
        entry = _result("toolu_1")
        entry["message"]["content"].append({"type": "tool_result", "tool_use_id": "toolu_2", "content": "synthetic"})
        entry["tool_result_meta"] = [{"non_execution_kind": checks.NON_EXECUTION_PERMISSION_RULE}]
        transcript = _transcript(tmp_path, [
            _assistant([_tool_use("toolu_1", "Bash", {"command": "ls"}), _tool_use("toolu_2", "Bash", {"command": "pwd"})]),
            entry,
        ])
        assert transcript.permission_denials == 0
        assert [call.answered for call in transcript.tool_calls] == [True, True]

    def test_a_block_marker_denies_only_its_own_result_among_several(self, tmp_path):
        entry = _result("toolu_1", denied=True)
        entry["message"]["content"].append({"type": "tool_result", "tool_use_id": "toolu_2", "content": "synthetic"})
        transcript = _transcript(tmp_path, [
            _assistant([_tool_use("toolu_1", "Bash", {"command": "ls"}), _tool_use("toolu_2", "Bash", {"command": "pwd"})]),
            entry,
        ])
        assert [call.denied for call in transcript.tool_calls] == [True, False]
        assert transcript.permission_denials == 1

    def test_a_denial_marker_on_a_result_matching_no_call_is_not_counted(self, tmp_path):
        assert _transcript(tmp_path, [_result("toolu_unmatched", denied=True)]).permission_denials == 0

    def test_a_call_with_no_id_is_never_answered_or_denied(self, tmp_path):
        no_id_call = _assistant([{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}])
        no_id_result = _result("", denied=True)
        transcript = _transcript(tmp_path, [no_id_call, no_id_result])
        assert transcript.tool_calls[0].answered is False
        assert transcript.permission_denials == 0


class TestTranscriptShapes:
    def test_parallel_tool_uses_in_one_entry_pair_with_their_own_results_and_a_string_content_prompt_is_ignored(
        self, tmp_path,
    ):
        prompt_entry = {"type": "user", "message": {"role": "user", "content": "synthetic prompt text"}}
        transcript = _transcript(tmp_path, [
            prompt_entry,
            _assistant([
                _tool_use("toolu_1", "Read", {"file_path": "a.py"}), _tool_use("toolu_2", "Read", {"file_path": "b.py"}),
            ]),
            _result("toolu_2", denied=True),
            _result("toolu_1"),
        ])
        assert [(call.tool_use_id, call.answered, call.denied) for call in transcript.tool_calls] == [
            ("toolu_1", True, False), ("toolu_2", True, True),
        ]

    def test_lines_that_are_not_json_objects_are_skipped_and_counted_while_the_rest_are_read(self, tmp_path):
        transcript = _transcript(tmp_path, [
            "[1, 2]", '"just a string"', "42", "", *_call_and_result("toolu_1", "Bash", {"command": "ls"}),
        ])
        assert transcript.skipped_lines == 3
        assert transcript.last_line_torn is False
        assert [call.tool_use_id for call in transcript.tool_calls] == ["toolu_1"]

    def test_an_integer_over_the_parser_limit_is_a_skipped_line_not_an_exception(self, tmp_path):
        digit_limit = sys.get_int_max_str_digits()
        if digit_limit == 0:
            pytest.skip("this interpreter sets no integer conversion limit, so no integer is too long to parse")
        huge_integer_line = '{"type": "assistant", "n": ' + "9" * (digit_limit + 1) + "}"
        transcript = _transcript(tmp_path, [huge_integer_line, *_call_and_result("toolu_1", "Bash", {"command": "ls"})])
        assert transcript.skipped_lines == 1
        assert [call.tool_use_id for call in transcript.tool_calls] == ["toolu_1"]

    def test_a_line_that_exhausts_the_json_recursion_limit_is_a_skipped_line_not_an_exception(
        self, tmp_path, monkeypatch,
    ):
        path = tmp_path / "subagent.jsonl"
        path.write_text(json.dumps(_assistant([])) + "\n")

        def exhaust_recursion(*_args, **_kwargs):
            raise RecursionError

        monkeypatch.setattr(checks.json, "loads", exhaust_recursion)

        transcript = checks.read_subagent_transcript(path)

        assert transcript.skipped_lines == 1
        assert transcript.last_line_torn is True

    def test_content_items_that_are_not_objects_are_ignored_beside_valid_calls(self, tmp_path):
        mixed_content = ["a string", None, 7, _tool_use("toolu_1", "Bash", {"command": "ls"})]
        transcript = _transcript(tmp_path, [_assistant(mixed_content), _result("toolu_1")])
        assert [call.tool_use_id for call in transcript.tool_calls] == ["toolu_1"]

    @pytest.mark.parametrize("usage", [ABSENT, None, "not an object", []], ids=repr)
    def test_a_first_assistant_entry_without_usable_usage_gives_a_null_size_without_raising(self, tmp_path, usage):
        entry = _assistant([_tool_use("toolu_1", "Bash", {"command": "ls"})])
        if usage is ABSENT:
            del entry["message"]["usage"]
        else:
            entry["message"]["usage"] = usage
        transcript = _transcript(tmp_path, [entry])
        assert transcript.first_turn_prompt_size is None
        assert len(transcript.tool_calls) == 1

    def test_a_tool_use_with_a_non_object_input_or_a_non_string_name_is_handled_without_raising(self, tmp_path):
        transcript = _transcript(tmp_path, [_assistant([
            _tool_use("toolu_1", "Bash", ["not", "an", "object"]),
            {"type": "tool_use", "id": "toolu_2", "name": 7, "input": {}},
            _tool_use("toolu_3", "Bash", {"command": "ls"}),
        ])])
        assert [(call.tool_use_id, call.tool_input) for call in transcript.tool_calls] == [
            ("toolu_1", {}), ("toolu_3", {"command": "ls"}),
        ]

    def test_a_bad_line_mid_file_followed_by_good_lines_is_skipped_without_a_torn_tail(self, tmp_path):
        transcript = _transcript(tmp_path, ["not json", *_call_and_result("toolu_1", "Bash", {"command": "ls"})])
        assert transcript.skipped_lines == 1
        assert transcript.last_line_torn is False

    def test_a_result_with_a_list_tool_use_id_is_read_without_raising_and_denies_nothing(self, tmp_path):
        odd_result = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": ["toolu_1"], "content": "x", "tool_result_meta": [
                {"non_execution_kind": checks.NON_EXECUTION_PERMISSION_RULE},
            ]},
        ]}}
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Bash", {"command": "ls"})]), odd_result])
        assert transcript.permission_denials == 0
        assert [call.answered for call in transcript.tool_calls] == [False]

    def test_a_result_meta_holding_non_objects_is_read_without_raising_and_denies_nothing(self, tmp_path):
        result = _result("toolu_1")
        result["message"]["content"][0]["tool_result_meta"] = ["x", 7, None]
        transcript = _transcript(tmp_path, [_assistant([_tool_use("toolu_1", "Bash", {"command": "ls"})]), result])
        assert transcript.permission_denials == 0
        assert [call.answered for call in transcript.tool_calls] == [True]

    def test_a_torn_last_line_is_reported_and_the_complete_lines_before_it_are_read(self, tmp_path):
        path = tmp_path / "subagent.jsonl"
        complete = "".join(json.dumps(entry) + "\n" for entry in _call_and_result("toolu_1", "Bash", {"command": "ls"}))
        path.write_text(complete + '{"type": "assistant", "message": {"content": [{"type": "tool_u')
        transcript = checks.read_subagent_transcript(path)
        assert transcript.last_line_torn is True
        assert transcript.skipped_lines == 1
        assert [call.tool_use_id for call in transcript.tool_calls] == ["toolu_1"]

    def test_invalid_utf8_on_one_line_is_skipped_without_losing_the_other_entries(self, tmp_path):
        path = tmp_path / "subagent.jsonl"
        lines = [json.dumps(entry).encode() for entry in _call_and_result("toolu_1", "Bash", {"command": "ls"})]
        path.write_bytes(b"\n".join([b"\xff\xfe not json", *lines]) + b"\n")
        transcript = checks.read_subagent_transcript(path)
        assert transcript.skipped_lines == 1
        assert [call.tool_use_id for call in transcript.tool_calls] == ["toolu_1"]

    @pytest.mark.parametrize("make_path", [lambda tmp_path: tmp_path / "absent.jsonl", lambda tmp_path: tmp_path])
    def test_a_missing_or_unreadable_transcript_is_a_value_with_a_detail_free_of_the_path(self, tmp_path, make_path):
        transcript = checks.read_subagent_transcript(make_path(tmp_path))
        assert transcript.unreadable_detail is not None
        assert str(tmp_path) not in transcript.unreadable_detail
        assert transcript.tool_calls == ()


class TestTranscriptIntegrity:
    def _failure_reasons(self, world, transcript):
        return _path_check(world, transcript).failure_reasons

    def test_a_clean_transcript_has_no_integrity_failure(self, world, tmp_path):
        clean = _transcript(tmp_path, _call_and_result("toolu_1", "Bash", {"command": "ls"}))
        assert self._failure_reasons(world, clean) == ()

    @pytest.mark.parametrize("damaged", [
        pytest.param(checks.SubagentTranscript((), 0, None, unreadable_detail="OSError: unreadable"), id="unreadable"),
        pytest.param(checks.SubagentTranscript((), 0, None, last_line_torn=True), id="torn-last-line"),
        pytest.param(checks.SubagentTranscript((), 0, None, skipped_lines=1), id="skipped-line"),
    ])
    def test_each_integrity_signal_alone_fails_the_run_as_a_transcript_that_cannot_be_read(self, world, damaged):
        assert self._failure_reasons(world, damaged) == (checks.REASON_TRANSCRIPT_UNREADABLE,)
        assert checks.REASON_TRANSCRIPT_UNREADABLE == "transcript-unreadable"

    def test_a_missing_transcript_file_fails_the_run_rather_than_reading_clean(self, world, tmp_path):
        missing = checks.read_subagent_transcript(tmp_path / "absent.jsonl")
        assert self._failure_reasons(world, missing) == (checks.REASON_TRANSCRIPT_UNREADABLE,)

    def test_the_integrity_reason_comes_before_a_path_failure(self, world, tmp_path):
        path = tmp_path / "subagent.jsonl"
        write = _call_and_result("toolu_1", "Write", {"file_path": str(world.root / "elsewhere.py")})
        path.write_text("".join(json.dumps(entry) + "\n" for entry in write) + "[1]\n")
        reasons = self._failure_reasons(world, checks.read_subagent_transcript(path))
        assert reasons == (checks.REASON_TRANSCRIPT_UNREADABLE, checks.REASON_OUT_OF_FIXTURE_WRITE)


class TestDeniedBashCalls:
    def test_a_denied_command_naming_a_guarded_path_is_recorded_and_fails_no_path_check(self, world, tmp_path):
        leak = str(world.live_checkout / "src" / "app.py")
        transcript = _transcript(tmp_path, _call_and_result("toolu_1", "Bash", {"command": f"cat {leak}"}, denied=True))
        result = _path_check(world, transcript)
        assert result.failure_reasons == ()
        assert result.bash_commands == (f"cat {leak}",)

    def test_the_same_command_when_it_ran_fails(self, world, tmp_path):
        leak = str(world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {leak}"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)

    def test_a_denied_toolkit_read_still_fails_bare_since_the_attempt_is_evidence_about_the_arm(self, world, tmp_path):
        reviewer = str(world.config_dir / "agents" / "reviewer.md")
        transcript = _transcript(tmp_path, _call_and_result("toolu_1", "Bash", {"command": f"cat {reviewer}"}, denied=True))
        reasons = checks.toolkit_reach_reasons(
            transcript, _bare(world), toolkit=world.toolkit, fixture_dir=world.fixture_dir,
        )
        assert reasons == (checks.REASON_ARM_LEAK,)


class TestDeniedCallsOfEveryKind:
    @pytest.mark.parametrize("tool_name, key", [
        ("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"),
    ])
    def test_a_denied_write_outside_the_fixture_adds_no_failure(self, world, tmp_path, tool_name, key):
        outside = str(world.root / "elsewhere.py")
        denied = _transcript(tmp_path, _call_and_result("toolu_1", tool_name, {key: outside}, denied=True))
        ran = _transcript(tmp_path, _call_and_result("toolu_1", tool_name, {key: outside}))
        assert _path_check(world, denied).failure_reasons == ()
        assert _path_check(world, ran).failure_reasons == (checks.REASON_OUT_OF_FIXTURE_WRITE,)

    @pytest.mark.parametrize("tool_name, tool_input_for", [
        ("Read", lambda world: {"file_path": str(world.config_dir / "agents" / "reviewer.md")}),
        ("Grep", lambda world: {"pattern": "x", "path": str(world.config_dir / "agents")}),
        ("Glob", lambda world: {"pattern": "*.md", "path": str(world.config_dir / "agents")}),
    ])
    def test_a_denied_read_grep_or_glob_of_the_toolkit_still_counts_as_a_bare_reach(
        self, world, tmp_path, tool_name, tool_input_for,
    ):
        denied = _transcript(tmp_path, _call_and_result("toolu_1", tool_name, tool_input_for(world), denied=True))
        reasons = checks.toolkit_reach_reasons(denied, _bare(world), toolkit=world.toolkit, fixture_dir=world.fixture_dir)
        assert reasons == (checks.REASON_ARM_LEAK,)


class TestCommandTextScan:
    """The contract in the module docstring: a directory above a guarded tree is never a hit."""

    def _both_arms_clean(self, world, tmp_path, command):
        transcript = _bash_transcript(tmp_path, command)
        reach = checks.toolkit_reach_reasons(transcript, _bare(world), toolkit=world.toolkit, fixture_dir=world.fixture_dir)
        return _path_check(world, transcript).failure_reasons, reach

    @pytest.mark.parametrize("command", [
        "cat /", "cat //", "cd ~", "echo ~", "echo $HOME", "cd ${HOME}", "ls /", "find / -name x", "echo a / b",
        "cat > t.py <<'EOF'\nroot = base / 'x'\nEOF", "echo $((a / b))",
        'cat "$PWD/src/app.py"', 'cd "$PWD"', "ls ${PWD}/src",
    ])
    def test_a_directory_above_a_guarded_tree_or_a_non_path_word_fails_nothing(
        self, world, tmp_path, monkeypatch, command,
    ):
        monkeypatch.setenv("HOME", str(world.home))
        monkeypatch.setenv("PWD", str(world.live_checkout))
        assert self._both_arms_clean(world, tmp_path, command) == ((), ())

    def test_pwd_expands_to_the_fixture_whatever_the_checkers_own_working_directory_is(self, world, tmp_path, monkeypatch):
        monkeypatch.setenv("PWD", str(world.live_checkout))
        result = _path_check(world, _bash_transcript(tmp_path, "cat $PWD/src/app.py"))
        assert result.failure_reasons == ()
        assert result.other_outside_fixture_tokens == ()

    @pytest.mark.parametrize("command_for", [
        pytest.param(lambda world: "ls ~/.claude", id="ls-config-dir-through-home"),
        pytest.param(lambda world: "cd ~/.claude", id="cd-config-dir-through-home"),
        pytest.param(lambda world: "cat $HOME/.claude/projects/x.jsonl", id="projects-through-home-variable"),
        pytest.param(lambda world: f"ls {world.projects_root}", id="projects-root"),
        pytest.param(lambda world: f"grep -r x {world.live_checkout}", id="live-checkout-root"),
        pytest.param(lambda world: f"ls {world.live_checkout}/src", id="directory-holding-a-changed-file"),
    ])
    def test_a_guarded_tree_or_a_directory_holding_a_guarded_path_inside_its_tree_fails(
        self, world, tmp_path, monkeypatch, command_for,
    ):
        monkeypatch.setenv("HOME", str(world.home))
        result = _path_check(world, _bash_transcript(tmp_path, command_for(world)))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)

    @pytest.mark.parametrize("tool_name, tool_input_for", [
        ("Grep", lambda world: {"pattern": "x", "path": str(world.config_dir)}),
        ("Bash", lambda world: {"command": "ls ~/.claude"}),
    ])
    def test_bare_listing_or_searching_the_config_dir_fails_arm_leak(
        self, world, tmp_path, monkeypatch, tool_name, tool_input_for,
    ):
        monkeypatch.setenv("HOME", str(world.home))
        transcript = _transcript(tmp_path, _call_and_result("toolu_1", tool_name, tool_input_for(world)))
        reasons = checks.toolkit_reach_reasons(transcript, _bare(world), toolkit=world.toolkit, fixture_dir=world.fixture_dir)
        assert reasons == (checks.REASON_ARM_LEAK,)

    def test_a_guarded_path_under_a_directory_named_with_shell_metacharacters_fails(self, world, tmp_path):
        weird_live = tmp_path / "R&D (Co)" / "o'brien=x" / "live"
        leak = _touch(weird_live / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {shlex.quote(str(leak))}"), live_checkout=weird_live)
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert str(leak) in result.out_of_fixture_tokens

    def test_a_toolkit_file_under_a_directory_named_with_shell_metacharacters_is_a_bare_reach(self, world, tmp_path):
        weird_config = tmp_path / "R&D (Co)" / "o'brien=x" / ".claude"
        agent_file = _touch(weird_config / "agents" / "reviewer.md")
        toolkit = checks.ToolkitPaths.from_config_dir(weird_config)
        transcript = _bash_transcript(tmp_path, f"cat {shlex.quote(str(agent_file))}")
        reasons = checks.toolkit_reach_reasons(transcript, _bare(world), toolkit=toolkit, fixture_dir=world.fixture_dir)
        assert reasons == (checks.REASON_ARM_LEAK,)

    def test_pwd_expands_to_the_fixture_so_a_path_climbing_out_of_it_is_seen(self, world, tmp_path):
        token = f"$PWD/../{world.other_fixture_dir.name}/src/app.py"
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {token}"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (token,)

    def test_a_braced_home_prefix_expands_so_the_path_behind_it_is_seen(self, world, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(world.home))
        result = _path_check(world, _bash_transcript(tmp_path, "cat ${HOME}/.claude/projects/p/other.jsonl"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)

    def test_a_braced_pwd_prefix_expands_to_the_fixture_so_a_path_climbing_out_of_it_is_seen(self, world, tmp_path):
        token = f"${{PWD}}/../{world.other_fixture_dir.name}/src/app.py"
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {token}"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (token,)

    @pytest.mark.parametrize("tree", ["live-checkout", "local-dir", "fixture-directories"])
    def test_a_directory_above_each_guarded_tree_fails_nothing_while_the_tree_and_a_directory_inside_it_fail(
        self, world, tmp_path, tree,
    ):
        parent, inside, tree_root = {
            "live-checkout": (world.live_checkout.parent, world.live_checkout / "src", world.live_checkout),
            "local-dir": (world.local_dir.parent, world.local_dir / "tasks", world.local_dir),
            "fixture-directories": (
                world.other_fixture_dir.parent, world.other_fixture_dir / "src", world.other_fixture_dir,
            ),
        }[tree]
        assert _path_check(world, _bash_transcript(tmp_path, f"ls {parent}")).failure_reasons == ()
        for guarded in (tree_root, inside):
            result = _path_check(world, _bash_transcript(tmp_path, f"ls {guarded}"))
            assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)

    def test_a_config_dir_reached_through_a_link_still_guards_another_sessions_files_by_either_name(
        self, world, tmp_path,
    ):
        real_config = tmp_path / "real-config"
        _touch(real_config / "projects" / "p" / "other.jsonl")
        link_config = _link(tmp_path / "link-config", real_config)
        for named in (link_config, real_config):
            for command in (f"cat {named}/projects/p/other.jsonl", f"ls {named}"):
                result = _path_check(world, _bash_transcript(tmp_path, command), config_dir=link_config)
                assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,), command

    def test_a_projects_directory_that_links_out_of_the_config_dir_is_still_guarded(self, world, tmp_path):
        relocated = _touch(tmp_path / "relocated" / "p" / "other.jsonl").parents[1]
        _link(world.config_dir / "projects", relocated)
        for command in (
            f"cat {world.config_dir}/projects/p/other.jsonl", f"cat {relocated}/p/other.jsonl", f"ls {relocated}",
            f"ls {world.config_dir}/projects",
        ):
            result = _path_check(world, _bash_transcript(tmp_path, command))
            assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,), command

    def test_a_changed_file_that_links_out_of_its_checkout_is_still_guarded_by_either_name(self, world, tmp_path):
        outside_copy = _touch(tmp_path / "outside" / "real-app.py")
        linking_checkout = tmp_path / "linking-live"
        _link(linking_checkout / "src" / "app.py", outside_copy)
        for command in (f"cat {linking_checkout}/src/app.py", f"cat {outside_copy}"):
            result = _path_check(world, _bash_transcript(tmp_path, command), live_checkout=linking_checkout)
            assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,), command

    @pytest.mark.parametrize("command_for", [
        pytest.param(lambda leak: f"# Let's look at the original\ncat {leak}   # it's the target", id="comment-apostrophes"),
        pytest.param(
            lambda leak: f"cat <<'EOF'\ndon't do this\nEOF\ncat {leak}\necho 'x' it's", id="heredoc-prose-apostrophe",
        ),
    ])
    def test_quotes_in_prose_do_not_pair_across_lines_to_hide_a_later_command(self, world, tmp_path, command_for):
        leak = str(world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, command_for(leak)))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert leak in result.out_of_fixture_tokens

    def test_quotes_in_prose_do_not_hide_a_toolkit_path_from_the_bare_reach(self, world, tmp_path):
        reviewer = world.config_dir / "agents" / "reviewer.md"
        command = f"# Let's look at the original\ncat {reviewer}   # it's the target"
        reasons = checks.toolkit_reach_reasons(
            _bash_transcript(tmp_path, command), _bare(world), toolkit=world.toolkit, fixture_dir=world.fixture_dir,
        )
        assert reasons == (checks.REASON_ARM_LEAK,)

    def test_a_quoted_word_holding_a_space_is_one_candidate_not_split_on_whitespace(self, world, tmp_path):
        result = _path_check(world, _bash_transcript(tmp_path, "ls '/usr/local/my dir'"))
        assert result.failure_reasons == ()
        assert result.other_outside_fixture_tokens == ("/usr/local/my dir",)

    def test_a_guarded_path_inside_a_word_longer_than_a_path_can_be_is_still_found_through_its_pieces(
        self, world, tmp_path,
    ):
        leak = str(world.live_checkout / "src" / "app.py")
        script = "x = 1; " * 800 + f"open('{leak}')"
        command = f'python3 -c "{script}"'
        assert len(command) > checks.MAX_PATH_CHARS
        transcript = _bash_transcript(tmp_path, command)

        result = _path_check(world, transcript)
        reaches = checks.find_toolkit_file_reaches(
            _bash_transcript(tmp_path, command.replace(leak, str(world.config_dir / "agents" / "reviewer.md"))),
            toolkit=world.toolkit, fixture_dir=world.fixture_dir,
        )

        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert leak in result.out_of_fixture_tokens
        assert any("longer than a path can be" in note for note in result.unparsed)
        assert reaches.paths == (str(world.config_dir / "agents" / "reviewer.md"),)

    def test_own_session_files_are_exempt_while_a_sibling_session_is_not(self, world, tmp_path):
        own_transcript, own_directory = world.own_session_paths
        sibling = own_transcript.parent / "other-session.jsonl"
        for command in (
            f"cat {own_transcript}", f"cat {own_directory}/tool-results/result-1.txt", f"ls {own_directory}",
        ):
            assert _path_check(world, _bash_transcript(tmp_path, command)).failure_reasons == (), command
        assert _path_check(world, _bash_transcript(tmp_path, f"cat {sibling}")).failure_reasons == (
            checks.REASON_OUT_OF_FIXTURE_PATH,
        )

    def test_a_flush_left_line_continuation_does_not_hide_a_guarded_path(self, world, tmp_path):
        leak = str(world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, "cat \\\n" + leak))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (leak,)
        assert result.unparsed == ()

    def test_a_guarded_path_cut_across_a_line_continuation_is_joined_before_the_scan(self, world, tmp_path):
        leak = str(world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {leak[:-4]}\\\n{leak[-4:]}"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (leak,)

    def test_a_word_longer_than_a_path_can_be_is_skipped_and_recorded(self, world, tmp_path):
        result = _path_check(world, _bash_transcript(tmp_path, "echo " + "a" * (checks.MAX_PATH_CHARS + 1)))
        assert result.failure_reasons == ()
        assert len(result.unparsed) == 1
        assert "longer than a path can be" in result.unparsed[0]


class TestToolkitReach:
    def _reasons(self, world, tmp_path, entries, expectation):
        return checks.toolkit_reach_reasons(
            _transcript(tmp_path, entries), expectation, toolkit=world.toolkit, fixture_dir=world.fixture_dir,
        )

    @pytest.mark.parametrize("tool_name, tool_input_for", [
        ("Read", lambda path: {"file_path": path}),
        ("Bash", lambda path: {"command": f"cat {path}"}),
    ])
    def test_bare_reading_a_toolkit_agent_file_fails_arm_leak_and_the_same_read_passes_toolkit(
        self, world, tmp_path, tool_name, tool_input_for,
    ):
        entries = _call_and_result("toolu_1", tool_name, tool_input_for(str(world.config_dir / "agents" / "reviewer.md")))

        assert self._reasons(world, tmp_path, entries, _bare(world)) == (checks.REASON_ARM_LEAK,)
        assert self._reasons(world, tmp_path, entries, _toolkit_arm(world)) == ()
        assert _path_check(world, _transcript(tmp_path, entries)).failure_reasons == ()

    def test_bare_searching_a_toolkit_directory_with_grep_or_glob_fails_arm_leak(self, world, tmp_path):
        grep = _call_and_result("toolu_1", "Grep", {"pattern": "x", "path": str(world.config_dir / "agents")})
        glob = _call_and_result("toolu_2", "Glob", {"pattern": "skills/**/SKILL.md", "path": str(world.config_dir)})
        for entries in (grep, glob):
            assert self._reasons(world, tmp_path, entries, _bare(world)) == (checks.REASON_ARM_LEAK,)
            assert self._reasons(world, tmp_path, entries, _toolkit_arm(world)) == ()

    def test_bare_reading_a_home_relative_toolkit_path_fails_arm_leak(self, world, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(world.home))
        entries = _call_and_result("toolu_1", "Bash", {"command": "cat ~/.claude/agents/extra.md"})
        assert self._reasons(world, tmp_path, entries, _bare(world)) == (checks.REASON_ARM_LEAK,)

    def test_bare_reaching_a_toolkit_path_through_an_unsplittable_command_fails_and_keeps_the_unparsed_record(
        self, world, tmp_path,
    ):
        command = f"cat {world.config_dir}/agents/reviewer.md  # it's here"
        transcript = _transcript(tmp_path, _call_and_result("toolu_1", "Bash", {"command": command}))
        reaches = checks.find_toolkit_file_reaches(transcript, toolkit=world.toolkit, fixture_dir=world.fixture_dir)
        assert reaches.paths == (f"{world.config_dir}/agents/reviewer.md",)
        assert len(reaches.unparsed) == 1

    @pytest.mark.parametrize("tool_name, tool_input", [
        ("Bash", {"command": "ls /usr/bin"}),
        ("Bash", {"command": "cat src/app.py"}),
        ("Grep", {"pattern": "x", "path": "src"}),
        ("Glob", {"pattern": "src/**/*.py"}),
    ])
    def test_bare_touching_nothing_in_the_toolkit_passes(self, world, tmp_path, tool_name, tool_input):
        entries = _call_and_result("toolu_1", tool_name, tool_input)
        assert self._reasons(world, tmp_path, entries, _bare(world)) == ()

    def test_bare_reading_a_toolkit_file_through_a_link_in_its_fixture_fails_arm_leak(self, world, tmp_path):
        link = _link(world.fixture_dir / "agent-link.md", world.config_dir / "agents" / "reviewer.md")
        entries = _call_and_result("toolu_1", "Read", {"file_path": str(link)})
        assert self._reasons(world, tmp_path, entries, _bare(world)) == (checks.REASON_ARM_LEAK,)

    def test_bare_reading_a_config_path_that_links_elsewhere_fails_through_its_unresolved_form(self, world, tmp_path):
        late_link = _link(world.config_dir / "agents" / "late.md", _touch(tmp_path / "outside" / "late.md"))
        entries = _call_and_result("toolu_1", "Read", {"file_path": str(late_link)})
        assert self._reasons(world, tmp_path, entries, _bare(world)) == (checks.REASON_ARM_LEAK,)

    def test_bare_reading_a_stowed_file_by_its_link_target_fails_through_the_resolved_form(self, tmp_path, world):
        target = _touch(tmp_path / "checkout" / "agents" / "stowed.md")
        stowed_config = tmp_path / "stowed-config"
        _link(stowed_config / "agents" / "stowed.md", target)
        toolkit = checks.ToolkitPaths.from_config_dir(stowed_config)
        entries = _call_and_result("toolu_1", "Read", {"file_path": str(target)})
        reasons = checks.toolkit_reach_reasons(
            _transcript(tmp_path, entries), _bare(world), toolkit=toolkit, fixture_dir=world.fixture_dir,
        )
        assert reasons == (checks.REASON_ARM_LEAK,)

    def test_bare_reading_a_fixture_file_passes(self, world, tmp_path):
        entries = _call_and_result("toolu_1", "Read", {"file_path": str(world.fixture_dir / "src" / "app.py")})
        assert self._reasons(world, tmp_path, entries, _bare(world)) == ()


class TestWritePaths:
    def test_an_edit_inside_the_fixture_passes(self, world, tmp_path):
        entries = [
            *_call_and_result("toolu_1", "Edit", {"file_path": str(world.fixture_dir / "src" / "app.py")}),
            *_call_and_result("toolu_2", "Write", {"file_path": "src/new.py"}),
        ]
        assert _path_check(world, _transcript(tmp_path, entries)).failure_reasons == ()

    @pytest.mark.parametrize("tool_name, key", [
        ("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"),
    ])
    def test_a_write_outside_the_fixture_fails_out_of_fixture_write(self, world, tmp_path, tool_name, key):
        outside = str(world.root / "elsewhere.py")
        result = _path_check(world, _transcript(tmp_path, _call_and_result("toolu_1", tool_name, {key: outside})))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_WRITE,)
        assert result.out_of_fixture_writes == (outside,)


class TestWriteTargetBoundary:
    @pytest.mark.parametrize("target_for", [
        pytest.param(lambda world: "../elsewhere.py", id="parent-relative"),
        pytest.param(lambda world: "src/../../elsewhere.py", id="descends-then-escapes"),
        pytest.param(lambda world: f"{world.fixture_dir}-evil/src/app.py", id="sibling-sharing-the-fixture-name-prefix"),
        pytest.param(
            lambda world: f"{world.fixture_dir}/../{world.other_fixture_dir.name}/src/app.py",
            id="through-the-fixture-to-another",
        ),
    ])
    def test_a_target_that_resolves_outside_the_fixture_fails(self, world, tmp_path, target_for):
        target = target_for(world)
        result = _path_check(world, _transcript(tmp_path, _call_and_result("toolu_1", "Write", {"file_path": target})))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_WRITE,)
        assert result.out_of_fixture_writes == (target,)

    def test_a_symlink_in_the_fixture_that_points_out_makes_a_write_through_it_fail(self, world, tmp_path):
        _link(world.fixture_dir / "escape", world.root)
        write = _call_and_result("toolu_1", "Write", {"file_path": str(world.fixture_dir / "escape" / "elsewhere.py")})
        result = _path_check(world, _transcript(tmp_path, write))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_WRITE,)


class TestBashPaths:
    @pytest.mark.parametrize("token_for", [
        pytest.param(lambda world: str(world.projects_root / "synthetic-project" / "other.jsonl"), id="projects-root"),
        pytest.param(lambda world: str(world.live_checkout / "src" / "app.py"), id="live-checkout-changed-path"),
        pytest.param(lambda world: str(world.local_dir / "tasks" / "set.json"), id="local-dir"),
        pytest.param(lambda world: str(world.other_fixture_dir / "src" / "app.py"), id="another-fixture"),
    ])
    def test_a_token_into_a_guarded_location_fails_out_of_fixture_path(self, world, tmp_path, token_for):
        token = token_for(world)
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {token}"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (token,)

    def test_an_absolute_token_into_the_runs_own_fixture_passes(self, world, tmp_path):
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {world.fixture_dir}/src/app.py"))
        assert result.failure_reasons == ()
        assert result.other_outside_fixture_tokens == ()

    def test_a_home_relative_token_resolves_against_the_home_and_fails_under_the_projects_root(
        self, world, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("HOME", str(world.home))
        result = _path_check(world, _bash_transcript(tmp_path, "cat ~/.claude/projects/synthetic-project/other.jsonl"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)

    def test_a_parent_relative_token_resolves_from_the_fixture_into_another_fixture_and_fails(self, world, tmp_path):
        token = f"../../tmp/{world.other_fixture_dir.name}/src/app.py"
        result = _path_check(world, _bash_transcript(tmp_path, f"cat {token}"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (token,)

    def test_an_unrelated_absolute_path_is_recorded_and_fails_nothing(self, world, tmp_path):
        result = _path_check(world, _bash_transcript(tmp_path, "ls /usr/bin"))
        assert result.failure_reasons == ()
        assert result.other_outside_fixture_tokens == ("/usr/bin",)
        assert result.bash_commands == ("ls /usr/bin",)

    def test_an_unsplittable_command_with_no_guarded_path_is_recorded_as_unparsed_and_fails_nothing(self, world, tmp_path):
        result = _path_check(world, _bash_transcript(tmp_path, "echo 'unterminated"))
        assert result.bash_commands == ("echo 'unterminated",)
        assert len(result.unparsed) == 1
        assert result.failure_reasons == ()

    @pytest.mark.parametrize("command_for", [
        pytest.param(lambda leak: f"cat <<'EOT'\nit's {leak}\nEOT", id="heredoc-body-with-an-apostrophe"),
        pytest.param(lambda leak: f"cat {leak}  # don't", id="comment-with-an-apostrophe"),
        pytest.param(lambda leak: f"cat {leak} \\", id="trailing-backslash"),
        pytest.param(lambda leak: f"cat '{leak}'  # don't", id="single-quoted-path-beside-a-comment"),
        pytest.param(lambda leak: f'cat "{leak}" <<\'EOT\'\nit\'s here\nEOT', id="double-quoted-path-beside-a-heredoc"),
    ])
    def test_an_unsplittable_command_holding_a_guarded_path_is_scanned_and_fails(self, world, tmp_path, command_for):
        leak = str(world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, command_for(leak)))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (leak,)
        assert len(result.unparsed) == 1

    @pytest.mark.parametrize("command_for", [
        pytest.param(lambda leak: f"cat {leak};true", id="semicolon"),
        pytest.param(lambda leak: f"cat {leak}|head", id="pipe"),
        pytest.param(lambda leak: f"cat {leak}&&true", id="and"),
        pytest.param(lambda leak: f"(cat {leak})", id="subshell"),
        pytest.param(lambda leak: f"$(cat {leak})", id="command-substitution"),
        pytest.param(lambda leak: f"cat<{leak}", id="input-redirect"),
        pytest.param(lambda leak: f"echo hi>{leak}", id="output-redirect"),
        pytest.param(lambda leak: f"diff --file={leak}", id="option-attached-path"),
        pytest.param(lambda leak: f"cat `echo {leak}`", id="backticks"),
    ])
    def test_a_guarded_path_glued_to_a_shell_operator_is_seen_and_fails(self, world, tmp_path, command_for):
        leak = str(world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, command_for(leak)))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (leak,)

    @pytest.mark.parametrize("relative", [".git", ".git/HEAD"])
    def test_a_live_checkouts_repository_directory_and_a_path_below_it_fail(self, world, tmp_path, relative):
        (world.live_checkout / ".git").mkdir()
        token = f"{world.live_checkout}/{relative}"
        result = _path_check(world, _bash_transcript(tmp_path, f"show --dir={token} HEAD"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == (token,)

    def test_a_command_through_a_persisting_link_in_the_fixture_to_a_guarded_file_fails(self, world, tmp_path):
        _link(world.fixture_dir / "escape.py", world.live_checkout / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, "cat escape.py"))
        assert result.failure_reasons == (checks.REASON_OUT_OF_FIXTURE_PATH,)
        assert result.out_of_fixture_tokens == ("escape.py",)

    def test_a_path_inside_a_nested_command_string_is_a_documented_limit_and_passes(self, world, tmp_path):
        nested = _path_check(world, _bash_transcript(tmp_path, f'bash -c "cat {world.live_checkout}/src/app.py"'))
        assert nested.failure_reasons == ()


class TestMalformedEntries:
    def test_a_bash_command_that_is_not_a_string_is_recorded_as_unparsed(self, world, tmp_path):
        result = _path_check(world, _bash_transcript(tmp_path, 7))
        assert len(result.unparsed) == 1
        assert result.bash_commands == ()

    def test_an_edit_with_no_file_path_is_recorded_as_unparsed(self, world, tmp_path):
        result = _path_check(world, _transcript(tmp_path, _call_and_result("toolu_1", "Edit", {})))
        assert len(result.unparsed) == 1
        assert result.failure_reasons == ()

    def test_a_token_holding_a_nul_byte_is_recorded_as_unparsed_without_blocking_the_other_tokens(self, world, tmp_path):
        outside_token = str(world.other_fixture_dir / "src" / "app.py")
        result = _path_check(world, _bash_transcript(tmp_path, f"cat a\x00b {outside_token}"))
        assert len(result.unparsed) == 1
        assert result.out_of_fixture_tokens == (outside_token,)
