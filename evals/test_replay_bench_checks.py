"""Tests for evals/replay_bench/checks: the instruction-load log, load classification, arm outcomes,
the account-hook check and the block calibration. Shared helpers live in test_replay_bench_checks_support.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from replay_bench import checks
from replay_bench.checks import (
    InstructionLog,
    LoadClass,
    LoadExpectation,
    ToolkitPaths,
)
from test_replay_bench_checks_support import (
    ABSENT,
    AGENT_ID,
    OTHER_SESSION_ID,
    PATH_GLOB_MATCH,
    SESSION_ID,
    SESSION_START,
    World,
    _bare,
    _bash_transcript,
    _call_and_result,
    _check,
    _dispatcher_claude_md,
    _line,
    _link,
    _parse,
    _path_check,
    _toolkit_arm,
    _touch,
    _transcript,
    call_with_deadline,
)


class TestInstrumentMissing:
    def test_an_empty_log_is_instrument_missing(self, checks_world):
        log = _parse(checks_world, [], trailing_newline=False)
        assert log.reason == checks.REASON_INSTRUMENT_MISSING

    def test_an_absent_log_is_instrument_missing(self, checks_world):
        log = checks.parse_instruction_log(checks_world.root / "never-written.jsonl", session_id=SESSION_ID)
        assert log.reason == checks.REASON_INSTRUMENT_MISSING

    def test_a_log_with_no_dispatcher_claude_md_load_is_instrument_missing(self, checks_world):
        subagent_only = [_line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID)]
        assert _check(checks_world, subagent_only, _toolkit_arm(checks_world)).reasons == (checks.REASON_INSTRUMENT_MISSING,)
        assert _check(checks_world, subagent_only, _bare(checks_world)).reasons == (
            checks.REASON_INSTRUMENT_MISSING, checks.REASON_ARM_LEAK,
        )

    @pytest.mark.parametrize("control_line, bare_reasons, toolkit_reasons", [
        pytest.param(
            lambda world: _line("/synthetic/managed/CLAUDE.md"),
            (checks.REASON_INSTRUMENT_MISSING, checks.REASON_ARM_LEAK), (checks.REASON_INSTRUMENT_MISSING,),
            id="dispatcher-session-start-of-another-claude-md",
        ),
        pytest.param(
            lambda world: _line(str(world.claude_md), PATH_GLOB_MATCH),
            (checks.REASON_INSTRUMENT_MISSING,), (checks.REASON_INSTRUMENT_MISSING,),
            id="expected-claude-md-with-another-load-reason",
        ),
    ])
    def test_an_unexpected_dispatcher_load_is_instrument_missing(
        self, checks_world, control_line, bare_reasons, toolkit_reasons,
    ):
        assert _check(checks_world, [control_line(checks_world)], _bare(checks_world)).reasons == bare_reasons
        assert _check(checks_world, [control_line(checks_world)], _toolkit_arm(checks_world)).reasons == toolkit_reasons


class TestInstrumentLogInvalid:
    def test_a_torn_last_line_is_invalid(self, checks_world):
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world), '{"session_id": "synth'], trailing_newline=False)
        assert log.reason == checks.REASON_INSTRUMENT_LOG_INVALID
        assert log.loads == ()

    def test_a_foreign_session_id_is_invalid(self, checks_world):
        log = _parse(
            checks_world, [_dispatcher_claude_md(checks_world), _line(str(checks_world.claude_md), session_id=OTHER_SESSION_ID)],
        )
        assert log.reason == checks.REASON_INSTRUMENT_LOG_INVALID

    def test_two_agent_ids_are_invalid(self, checks_world):
        log = _parse(checks_world, [
            _dispatcher_claude_md(checks_world),
            _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id="agent-one"),
            _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id="agent-two"),
        ])
        assert log.reason == checks.REASON_INSTRUMENT_LOG_INVALID

    def test_a_line_that_is_not_a_json_object_is_invalid(self, checks_world):
        assert _parse(checks_world, ["[1, 2]"]).reason == checks.REASON_INSTRUMENT_LOG_INVALID

    @pytest.mark.parametrize("mutation", [
        {"agent_id": None}, {"agent_id": ""}, {"agent_id": 7},
        {"session_id": ABSENT}, {"session_id": 7},
        {"file_path": ABSENT}, {"file_path": 7},
        {"load_reason": ABSENT}, {"load_reason": 7},
        {"file_path": "/synthetic/with\x00nul.md"},
        {"file_path": "relative/CLAUDE.md"},
        {"file_path": "/" + "a" * checks.MAX_PATH_CHARS},
        {"file_path": "/synthetic/lone-surrogate-\ud800.md"},
    ], ids=["agent_id-null", "agent_id-empty", "agent_id-number", "session_id-absent", "session_id-number",
            "file_path-absent", "file_path-number", "load_reason-absent", "load_reason-number", "file_path-nul",
            "file_path-relative", "file_path-too-long", "file_path-lone-surrogate"])
    def test_a_field_mutation_is_invalid(
        self, checks_world, monkeypatch, mutation,
    ):
        line = _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        for key, value in mutation.items():
            if value is ABSENT:
                del line[key]
            else:
                line[key] = value
        realpath_calls: list[str] = []
        real_realpath = os.path.realpath

        def recording_realpath(path, *args, **kwargs):
            realpath_calls.append(str(path))
            return real_realpath(path, *args, **kwargs)

        monkeypatch.setattr(os.path, "realpath", recording_realpath)

        outcome = _check(checks_world, [_dispatcher_claude_md(checks_world), line], _bare(checks_world))

        assert outcome.reasons == (checks.REASON_INSTRUMENT_LOG_INVALID,)
        assert realpath_calls == []

    def test_a_file_path_of_exactly_the_maximum_length_is_accepted(self, checks_world):
        edge_path = "/" + "a" * (checks.MAX_PATH_CHARS - 1)
        assert len(edge_path) == checks.MAX_PATH_CHARS
        assert _parse(checks_world, [_line(edge_path, PATH_GLOB_MATCH, agent_id=AGENT_ID)]).reason is None


class TestHostileLogContent:
    def test_an_integer_over_the_parser_limit_fails_the_log(self, checks_world):
        digit_limit = sys.get_int_max_str_digits()
        if digit_limit == 0:
            pytest.skip("this interpreter sets no integer conversion limit, so no integer is too long to parse")
        huge_integer = "9" * (digit_limit + 1)
        line = json.dumps(_dispatcher_claude_md(checks_world))[:-1] + f', "extra": {huge_integer}}}'
        assert _parse(checks_world, [line]).reason == checks.REASON_INSTRUMENT_LOG_INVALID

    def test_a_line_exhausting_the_recursion_limit_fails_the_log(self, checks_world, monkeypatch):
        checks_world.log_path.write_text(json.dumps(_dispatcher_claude_md(checks_world)) + "\n")

        def exhaust_recursion(*_args, **_kwargs):
            raise RecursionError

        monkeypatch.setattr(checks.json, "loads", exhaust_recursion)

        log = checks.parse_instruction_log(checks_world.log_path, session_id=SESSION_ID)

        assert log.reason == checks.REASON_INSTRUMENT_LOG_INVALID

    def test_a_directory_log_path_fails_the_log(self, checks_world):
        log = checks.parse_instruction_log(checks_world.root, session_id=SESSION_ID)
        assert log.reason == checks.REASON_INSTRUMENT_LOG_INVALID
        assert str(checks_world.root) not in (log.detail or "")

    def test_a_log_that_is_not_utf8_fails_the_log(self, checks_world):
        checks_world.log_path.write_bytes(b"\xff\xfe\n")
        log = checks.parse_instruction_log(checks_world.log_path, session_id=SESSION_ID)
        assert log.reason == checks.REASON_INSTRUMENT_LOG_INVALID


class TestTriggerPath:
    def test_a_trigger_path_on_a_line_that_is_not_a_path_glob_match_is_dropped(self, checks_world):
        log = _parse(checks_world, [_line(checks_world.rule(), SESSION_START, trigger_file_path="/synthetic/a.py")])
        assert log.reason is None
        assert log.loads[0].trigger_file_path is None

    def test_a_string_trigger_path_is_kept_on_a_path_glob_match_line(self, checks_world):
        log = _parse(
            checks_world, [_line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID, trigger_file_path="/synthetic/a.py")],
        )
        assert log.loads[0].trigger_file_path == "/synthetic/a.py"

    @pytest.mark.parametrize("trigger", [ABSENT, 7, None, ["x"], "/synthetic/a\x00b.py"], ids=repr)
    def test_an_unusable_trigger_path_is_dropped(self, checks_world, trigger):
        log = _parse(checks_world, [_line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID, trigger_file_path=trigger)])
        assert log.reason is None
        assert log.loads[0].trigger_file_path is None


class TestAttribution:
    def test_a_subagent_session_start_load_is_credited_to_the_subagent(self, checks_world):
        outcome = _check(checks_world, [_line(str(checks_world.claude_md), agent_id=AGENT_ID)], _toolkit_arm(checks_world))
        assert outcome.reasons == (checks.REASON_INSTRUMENT_MISSING,)
        assert outcome.subagent_loads_by_class == {"toolkit": {"CLAUDE.md": 1}}

    def test_a_duplicate_dispatcher_load_passes(self, checks_world):
        outcome = _check(
            checks_world, [_dispatcher_claude_md(checks_world), _dispatcher_claude_md(checks_world)], _bare(checks_world),
        )
        assert outcome.reasons == ()
        assert outcome.subagent_loads_by_class == {}

    def test_a_duplicate_subagent_load_is_counted_never_failed(self, checks_world):
        rule_load = _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        outcome = _check(checks_world, [_dispatcher_claude_md(checks_world), rule_load, rule_load], _toolkit_arm(checks_world))
        assert outcome.reasons == ()
        assert outcome.subagent_loads_by_class == {"toolkit": {"rules/python.md": 2}}


class TestStowedFiles:
    def test_a_stowed_claude_md_and_rules_directory_reported_by_link_target_are_toolkit(self, tmp_path):
        checkout = tmp_path / "checkout"
        _touch(checkout / "CLAUDE.md")
        _touch(checkout / "rules" / "python.md")
        config_dir = tmp_path / "stowed-config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").symlink_to(checkout / "CLAUDE.md")
        (config_dir / "rules").symlink_to(checkout / "rules")
        toolkit = ToolkitPaths.from_config_dir(config_dir)
        log = InstructionLog((
            checks.InstructionLoad(str(checkout / "CLAUDE.md"), SESSION_START, None, None),
            checks.InstructionLoad(str(checkout / "rules" / "python.md"), PATH_GLOB_MATCH, AGENT_ID, None),
        ))

        classified = checks.classify_loads(log, toolkit=toolkit, fixture_dir=tmp_path / "fixture")

        assert [(item.load_class, item.display_path) for item in classified] == [
            (LoadClass.TOOLKIT, "CLAUDE.md"), (LoadClass.TOOLKIT, "rules/python.md"),
        ]

    def test_a_load_of_a_file_on_the_read_allow_list_is_toolkit(self, checks_world):
        log = _parse(checks_world, [_line(str(checks_world.allow_file), PATH_GLOB_MATCH, agent_id=AGENT_ID)])
        (classified,) = checks.classify_loads(log, toolkit=checks_world.toolkit, fixture_dir=checks_world.fixture_dir)
        assert classified.load_class is LoadClass.TOOLKIT


class TestLinkedPaths:
    def test_a_symlinked_expected_claude_md_passes_when_the_log_reports_the_link_target(self, tmp_path):
        checkout = tmp_path / "checkout"
        _touch(checkout / "CLAUDE.md")
        config_dir = tmp_path / "stowed-config"
        _link(config_dir / "CLAUDE.md", checkout / "CLAUDE.md")
        toolkit = ToolkitPaths.from_config_dir(config_dir)
        log = InstructionLog((checks.InstructionLoad(str(checkout / "CLAUDE.md"), SESSION_START, None, None),))

        for excludes_toolkit in (True, False):
            expectation = LoadExpectation(config_dir / "CLAUDE.md", excludes_toolkit)
            outcome = checks.check_instruction_loads(log, expectation, toolkit=toolkit, fixture_dir=tmp_path / "fixture")
            assert outcome.reasons == ()

    def test_a_fixture_reached_through_a_symlinked_parent_classes_a_resolved_log_path_as_fixture(self, tmp_path):
        real_parent = tmp_path / "real-tmp"
        fixture_real = _touch(real_parent / "review-bench-replay-ccc" / "docs" / "NOTES.md").parents[1]
        linked_parent = _link(tmp_path / "linked-tmp", real_parent)
        fixture_dir = linked_parent / fixture_real.name
        toolkit = ToolkitPaths.from_config_dir(tmp_path / "config")
        log = InstructionLog((checks.InstructionLoad(str(fixture_real / "docs" / "NOTES.md"), SESSION_START, None, None),))

        (classified,) = checks.classify_loads(log, toolkit=toolkit, fixture_dir=fixture_dir)

        assert (classified.load_class, classified.display_path) == (LoadClass.FIXTURE, "docs/NOTES.md")

    def test_an_in_fixture_write_passes_when_the_transcript_reports_the_resolved_fixture_path(self, checks_world, tmp_path):
        linked_fixture = _link(tmp_path / "linked-fixture", checks_world.fixture_dir)
        write = _call_and_result("toolu_1", "Write", {"file_path": str(checks_world.fixture_dir / "src" / "new.py")})
        result = _path_check(checks_world, _transcript(tmp_path, write), fixture_dir=linked_fixture)
        assert result.failure_reasons == ()

    def test_a_path_lexically_in_the_fixture_is_a_fixture_load_even_when_it_links_to_a_rule(self, checks_world):
        linked_rule = _link(checks_world.fixture_dir / ".claude" / "rules" / "py.md", Path(checks_world.rule()))
        log = _parse(checks_world, [
            _dispatcher_claude_md(checks_world), _line(str(linked_rule), PATH_GLOB_MATCH, agent_id=AGENT_ID),
        ])

        outcome = checks.check_instruction_loads(
            log, _toolkit_arm(checks_world), toolkit=checks_world.toolkit, fixture_dir=checks_world.fixture_dir,
        )

        assert outcome.reasons == (checks.REASON_FIXTURE_INSTRUCTION_LOAD,)
        assert outcome.subagent_loads_by_class == {"fixture": {".claude/rules/py.md": 1}}
        assert _verdict(checks_world, log) == (checks.REASON_INSTRUMENT_MISSING,)


class TestResolvedFixtureForms:
    def test_a_log_path_in_link_form_classes_as_fixture_when_the_fixture_dir_is_given_resolved(self, tmp_path):
        real_fixture = _touch(tmp_path / "real" / "review-bench-replay-ddd" / "docs" / "NOTES.md").parents[1]
        link_form = _link(tmp_path / "link-form", real_fixture)
        toolkit = ToolkitPaths.from_config_dir(tmp_path / "config")
        log = InstructionLog((checks.InstructionLoad(str(link_form / "docs" / "NOTES.md"), SESSION_START, None, None),))

        (classified,) = checks.classify_loads(log, toolkit=toolkit, fixture_dir=real_fixture)

        assert (classified.load_class, classified.display_path) == (LoadClass.FIXTURE, "docs/NOTES.md")


class TestLexicalFixtureForms:
    def test_a_link_form_fixture_dir_classes_an_in_fixture_link_to_a_rule_as_fixture(self, checks_world, tmp_path):
        real_fixture = tmp_path / "real" / "review-bench-replay-eee"
        _link(real_fixture / ".claude" / "rules" / "py.md", Path(checks_world.rule()))
        link_fixture = _link(tmp_path / "link-fixture", real_fixture)
        loaded = _line(str(link_fixture / ".claude" / "rules" / "py.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world), loaded])

        outcome = checks.check_instruction_loads(
            log, _toolkit_arm(checks_world), toolkit=checks_world.toolkit, fixture_dir=link_fixture,
        )

        assert outcome.reasons == (checks.REASON_FIXTURE_INSTRUCTION_LOAD,)
        assert outcome.subagent_loads_by_class == {"fixture": {".claude/rules/py.md": 1}}


class TestPerFileStow:
    """A toolkit subdirectory that is a real directory of per-file links into the checkout."""

    @pytest.mark.parametrize("subdirectory, file_name", [
        ("rules", "python.md"), ("agents", "reviewer.md"), ("skills", "demo/SKILL.md"),
    ])
    def test_a_file_reported_by_its_link_target_is_toolkit_with_its_config_relative_name(
        self, tmp_path, subdirectory, file_name,
    ):
        target = _touch(tmp_path / "checkout" / subdirectory / file_name)
        config_dir = tmp_path / "config"
        _link(config_dir / subdirectory / file_name, target)
        toolkit = ToolkitPaths.from_config_dir(config_dir)
        log = InstructionLog((checks.InstructionLoad(str(target), PATH_GLOB_MATCH, AGENT_ID, None),))

        (classified,) = checks.classify_loads(log, toolkit=toolkit, fixture_dir=tmp_path / "fixture")

        assert (classified.load_class, classified.display_path) == (LoadClass.TOOLKIT, f"{subdirectory}/{file_name}")
        assert toolkit.is_rule_file(classified.resolved) is (subdirectory == "rules")

    def test_a_linked_directory_inside_a_real_skills_directory_holds_its_files(self, tmp_path):
        target = _touch(tmp_path / "checkout" / "skills" / "demo" / "SKILL.md")
        config_dir = tmp_path / "config"
        _link(config_dir / "skills" / "demo", target.parent)
        toolkit = ToolkitPaths.from_config_dir(config_dir)
        assert toolkit.contains(target)
        assert toolkit.display_path(target) == "skills/demo/SKILL.md"

    def test_a_rule_reported_by_its_link_target_satisfies_the_calibration_control(self, tmp_path, checks_world):
        target = _touch(tmp_path / "checkout" / "rules" / "python.md")
        stowed_config = tmp_path / "stowed-config"
        _link(stowed_config / "rules" / "python.md", target)
        toolkit = ToolkitPaths.from_config_dir(stowed_config)
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world), _line(str(target), PATH_GLOB_MATCH, agent_id=AGENT_ID)])
        assert _verdict(checks_world, log, toolkit=toolkit) == ()


class _NoIterationFrozenSet(frozenset):
    def __iter__(self):
        raise AssertionError("membership iterated a collection")


class _NoIterationDict(dict):
    def __iter__(self):
        raise AssertionError("membership iterated a collection")


class TestToolkitMembershipCost:
    def test_contains_and_is_rule_file_never_iterate_a_toolkit_collection(self, tmp_path):
        config_dir = tmp_path / "config"
        for number in range(60):
            _touch(config_dir / "agents" / f"agent-{number}.md")
            _touch(config_dir / "rules" / f"rule-{number}.md")
            _touch(config_dir / "skills" / f"skill-{number}" / "SKILL.md")
        built = ToolkitPaths.from_config_dir(config_dir, [tmp_path / "personal" / "extra.md"])
        toolkit = dataclasses.replace(
            built,
            claude_md_forms=_NoIterationFrozenSet(built.claude_md_forms), roots=_NoIterationFrozenSet(built.roots),
            rules_roots=_NoIterationFrozenSet(built.rules_roots), rule_files=_NoIterationFrozenSet(built.rule_files),
            listed_files=_NoIterationDict(built.listed_files), anchors=_NoIterationFrozenSet(built.anchors),
            ancestors=_NoIterationFrozenSet(built.ancestors),
            read_allow_files=_NoIterationFrozenSet(built.read_allow_files),
        )
        forms = {
            Path("/usr/bin"): False, config_dir.parent: False, config_dir: True,
            config_dir / "agents": True, config_dir / "agents" / "agent-0.md": True,
            config_dir / "skills" / "skill-0" / "missing.md": True, tmp_path / "personal" / "extra.md": True,
        }
        assert {form: toolkit.contains(form) for form in forms} == forms
        assert toolkit.is_rule_file(config_dir / "rules" / "rule-0.md")
        assert not toolkit.is_rule_file(config_dir / "agents" / "agent-0.md")


class TestLinkedConfigDir:
    def test_a_linked_config_dir_holds_both_forms(
        self, tmp_path,
    ):
        real_config = tmp_path / "real-config"
        (real_config / "agents").mkdir(parents=True)
        link_config = _link(tmp_path / "link-config", real_config)

        toolkit = ToolkitPaths.from_config_dir(link_config)

        assert toolkit.contains(link_config)
        assert toolkit.contains(real_config)
        assert toolkit.contains(real_config / "agents")
        assert not toolkit.contains(real_config.parent)


class TestToolkitMembership:
    def test_a_directory_holding_a_toolkit_root_is_toolkit(self, checks_world):
        assert checks_world.toolkit.contains(checks_world.config_dir)
        assert not checks_world.toolkit.contains(checks_world.fixture_dir)

    def test_a_sibling_directory_sharing_a_root_name_prefix_is_not_toolkit(self, checks_world):
        assert not checks_world.toolkit.contains(checks_world.config_dir / "rules-extra" / "x.md")


class TestArmOutcomes:
    @pytest.mark.parametrize("leaking_line", [
        pytest.param(lambda world: _line(world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID), id="subagent-toolkit-rule"),
        pytest.param(lambda world: _line("/synthetic/elsewhere/CLAUDE.md", agent_id=AGENT_ID), id="subagent-other-class"),
        pytest.param(lambda world: _line(world.rule(), "include"), id="dispatcher-rule"),
        pytest.param(lambda world: _line("/synthetic/managed/CLAUDE.md"), id="dispatcher-other-class-claude-md"),
    ])
    def test_bare_fails_arm_leak(
        self, checks_world, leaking_line,
    ):
        outcome = _check(checks_world, [_dispatcher_claude_md(checks_world), leaking_line(checks_world)], _bare(checks_world))
        assert outcome.reasons == (checks.REASON_ARM_LEAK,)

    def test_bare_whose_only_loads_are_the_dispatcher_claude_md_passes(self, checks_world):
        assert _check(checks_world, [_dispatcher_claude_md(checks_world)], _bare(checks_world)).reasons == ()

    def test_toolkit_with_subagent_rule_loads_passes(self, checks_world):
        outcome = _check(checks_world, [
            _dispatcher_claude_md(checks_world),
            _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID),
            _line("/synthetic/elsewhere/CLAUDE.md", PATH_GLOB_MATCH, agent_id=AGENT_ID),
        ], _toolkit_arm(checks_world))
        assert outcome.reasons == ()
        assert outcome.subagent_loads_by_class["toolkit"] == {"rules/python.md": 1}
        assert outcome.subagent_loads_by_class["other"] == {str(Path("/synthetic/elsewhere/CLAUDE.md").resolve()): 1}

    def test_toolkit_with_no_subagent_load_passes(self, checks_world):
        outcome = _check(checks_world, [_dispatcher_claude_md(checks_world)], _toolkit_arm(checks_world))
        assert outcome.reasons == ()
        assert outcome.subagent_loads_by_class == {}

    @pytest.mark.parametrize("make_expectation", [_bare, _toolkit_arm])
    def test_a_fixture_file_load_its_arm_does_not_allow_fails_fixture_instruction_load(self, checks_world, make_expectation):
        fixture_load = _line(str(checks_world.fixture_dir / "docs" / "NOTES.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        outcome = _check(checks_world, [_dispatcher_claude_md(checks_world), fixture_load], make_expectation(checks_world))
        assert outcome.reasons == (checks.REASON_FIXTURE_INSTRUCTION_LOAD,)
        assert outcome.subagent_loads_by_class == {"fixture": {"docs/NOTES.md": 1}}

    def test_a_fixture_file_load_the_arm_allows_passes(self, checks_world):
        fixture_load = _line(str(checks_world.fixture_dir / "docs" / "NOTES.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        outcome = _check(
            checks_world, [_dispatcher_claude_md(checks_world), fixture_load],
            _bare(checks_world, allowed_fixture_loads=frozenset({"docs/NOTES.md"})),
        )
        assert outcome.reasons == ()

    @pytest.mark.parametrize(("make_expectation", "expected_reasons"), [
        pytest.param(_toolkit_arm, (checks.REASON_FIXTURE_INSTRUCTION_LOAD,), id="toolkit"),
        pytest.param(_bare, (checks.REASON_ARM_LEAK, checks.REASON_FIXTURE_INSTRUCTION_LOAD), id="bare"),
    ])
    def test_a_dispatcher_load_of_a_fixture_claude_md_fails_fixture_instruction_load(
        self, checks_world, make_expectation, expected_reasons,
    ):
        fixture_claude_md_load = _line(str(checks_world.fixture_dir / "CLAUDE.md"), SESSION_START)
        outcome = _check(
            checks_world, [_dispatcher_claude_md(checks_world), fixture_claude_md_load], make_expectation(checks_world),
        )
        assert outcome.reasons == expected_reasons

    def test_a_fixture_file_load_outside_a_non_empty_allow_list_still_fails(self, checks_world):
        listed_load = _line(str(checks_world.fixture_dir / "docs" / "NOTES.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        unlisted_load = _line(str(checks_world.fixture_dir / "docs" / "OTHER.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        outcome = _check(
            checks_world, [_dispatcher_claude_md(checks_world), listed_load, unlisted_load],
            _bare(checks_world, allowed_fixture_loads=frozenset({"docs/NOTES.md"})),
        )
        assert outcome.reasons == (checks.REASON_FIXTURE_INSTRUCTION_LOAD,)
        assert outcome.subagent_loads_by_class == {"fixture": {"docs/NOTES.md": 1, "docs/OTHER.md": 1}}

    def test_each_instrument_reason_is_emitted_by_some_scenario(self, checks_world, tmp_path):
        emitted: set[str] = set()
        emitted.add(_parse(checks_world, []).reason or "")
        emitted.add(_parse(checks_world, ["{"]).reason or "")
        leaking_rule_load = _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID)
        emitted.update(
            _check(checks_world, [_dispatcher_claude_md(checks_world), leaking_rule_load], _bare(checks_world)).reasons
        )
        fixture_note_load = _line(str(checks_world.fixture_dir / "docs" / "NOTES.md"), agent_id=AGENT_ID)
        emitted.update(
            _check(checks_world, [_dispatcher_claude_md(checks_world), fixture_note_load], _toolkit_arm(checks_world)).reasons
        )
        emitted.add(checks.check_account_hooks(checks_world.config_dir, SESSION_ID) or "")
        reviewer_read = {"file_path": str(checks_world.config_dir / "agents" / "reviewer.md")}
        reach = _transcript(tmp_path, _call_and_result("toolu_1", "Read", reviewer_read))
        emitted.update(checks.toolkit_reach_reasons(
            reach, _bare(checks_world), toolkit=checks_world.toolkit, fixture_dir=checks_world.fixture_dir,
        ))
        emitted.discard("")
        assert emitted == checks.INSTRUMENT_REASONS

    def test_other_failure_reasons_are_not_instrument_reasons(self, checks_world, tmp_path):
        calibration = _verdict(checks_world, gap=0)
        paths = _path_check(
            checks_world, _bash_transcript(tmp_path, f"cat {checks_world.other_fixture_dir}/src/app.py > /elsewhere.txt"),
        )
        writes = _path_check(
            checks_world, _transcript(tmp_path, _call_and_result("toolu_1", "Write", {"file_path": "/elsewhere.py"})),
        )
        failures = {*calibration, *paths.failure_reasons, *writes.failure_reasons}
        assert failures == {
            checks.REASON_ARM_CONFIG_MISSING, checks.REASON_OUT_OF_FIXTURE_PATH, checks.REASON_OUT_OF_FIXTURE_WRITE,
        }
        assert failures.isdisjoint(checks.INSTRUMENT_REASONS)


CLAUDE_MD_CHARS = 8000
BARE_SIZE = 5000


def _control_log(checks_world: World) -> InstructionLog:
    return _parse(
        checks_world, [_dispatcher_claude_md(checks_world), _line(checks_world.rule(), PATH_GLOB_MATCH, agent_id=AGENT_ID)],
    )


def _verdict(
    checks_world: World, log: InstructionLog | None = None, *, gap: int | None = 1000, bare_size: int | None = BARE_SIZE,
    code_writer_extra_chars: int = 0, dispatcher_rule_chars: tuple[int, ...] = (), toolkit: ToolkitPaths | None = None,
) -> tuple[str, ...]:
    toolkit_size = None if gap is None else BARE_SIZE + gap
    return checks.block_calibration_verdict(
        _control_log(checks_world) if log is None else log, toolkit_first_turn_size=toolkit_size, bare_first_turn_size=bare_size,
        toolkit=toolkit or checks_world.toolkit, fixture_dir=checks_world.fixture_dir, claude_md_chars=CLAUDE_MD_CHARS,
        code_writer_body_chars=3000 + code_writer_extra_chars, bare_writer_body_chars=3000,
        dispatcher_rule_chars=dispatcher_rule_chars,
    )


class TestBlockCalibration:
    def test_a_passing_block_returns_no_reason(self, checks_world):
        assert _verdict(checks_world) == ()

    def test_a_toolkit_log_with_no_subagent_rule_load_fails_instrument_missing(self, checks_world):
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world)])
        assert _verdict(checks_world, log) == (checks.REASON_INSTRUMENT_MISSING,)

    @pytest.mark.parametrize("load", [
        pytest.param(lambda world: _line(world.rule(), PATH_GLOB_MATCH), id="path-glob-match-on-a-dispatcher-line"),
        pytest.param(lambda world: _line(world.rule(), SESSION_START, agent_id=AGENT_ID), id="subagent-session-start"),
        pytest.param(lambda world: _line(world.rule(), "include", agent_id=AGENT_ID), id="subagent-include"),
        pytest.param(
            lambda world: _line(str(world.fixture_dir / "docs" / "NOTES.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID),
            id="fixture-class-file",
        ),
        pytest.param(lambda world: _line("/synthetic/other/x.md", PATH_GLOB_MATCH, agent_id=AGENT_ID), id="other-class-file"),
        pytest.param(
            lambda world: _line(str(world.config_dir / "agents" / "reviewer.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID),
            id="agents-file",
        ),
        pytest.param(
            lambda world: _line(str(world.config_dir / "skills" / "demo" / "SKILL.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID),
            id="skills-file",
        ),
        pytest.param(lambda world: _line(str(world.claude_md), PATH_GLOB_MATCH, agent_id=AGENT_ID), id="account-claude-md"),
        pytest.param(
            lambda world: _line(str(world.config_dir / "rules-extra" / "x.md"), PATH_GLOB_MATCH, agent_id=AGENT_ID),
            id="sibling-dir-a-string-prefix-match-would-pass",
        ),
    ])
    def test_a_load_that_only_looks_like_a_subagent_rule_load_fails_instrument_missing(self, checks_world, load):
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world), load(checks_world)])
        assert _verdict(checks_world, log) == (checks.REASON_INSTRUMENT_MISSING,)

    def test_a_nested_rule_load_passes_in_either_form(self, tmp_path, checks_world):
        checkout = tmp_path / "checkout"
        _touch(checkout / "rules" / "nested" / "deep.md")
        stowed_config = tmp_path / "stowed-config"
        stowed_config.mkdir()
        (stowed_config / "rules").symlink_to(checkout / "rules")
        toolkit = ToolkitPaths.from_config_dir(stowed_config)
        for loaded in (checkout / "rules" / "nested" / "deep.md", stowed_config / "rules" / "nested" / "deep.md"):
            log = _parse(
                checks_world, [_dispatcher_claude_md(checks_world), _line(str(loaded), PATH_GLOB_MATCH, agent_id=AGENT_ID)],
            )
            assert _verdict(checks_world, log, toolkit=toolkit) == ()

    def test_the_inheritance_gap_equal_to_the_margin_passes(self, checks_world):
        assert _verdict(checks_world, gap=1000) == ()

    def test_a_gap_one_token_short_of_the_margin_fails_arm_config_missing(self, checks_world):
        assert _verdict(checks_world, gap=999) == (checks.REASON_ARM_CONFIG_MISSING,)

    def test_a_longer_code_writer_body_raises_the_needed_gap_by_the_body_term(self, checks_world):
        # 4,000 extra characters are 1,000 tokens, so the needed gap is 1000 + 1000.
        assert _verdict(checks_world, gap=2000, code_writer_extra_chars=4000) == ()
        assert _verdict(checks_world, gap=1999, code_writer_extra_chars=4000) == (checks.REASON_ARM_CONFIG_MISSING,)

    def test_a_dispatcher_loaded_rule_raises_the_margin_by_its_full_character_count_over_four(self, checks_world):
        # A 400-character rule adds 100 tokens, not the 50 that halving it would add.
        assert _verdict(checks_world, gap=1100, dispatcher_rule_chars=(400,)) == ()
        assert _verdict(checks_world, gap=1099, dispatcher_rule_chars=(400,)) == (checks.REASON_ARM_CONFIG_MISSING,)

    def test_several_dispatcher_rules_are_summed_not_maxed(self, checks_world):
        # 400 + 800 characters add 300 tokens to the 1000-token margin.
        assert _verdict(checks_world, gap=1300, dispatcher_rule_chars=(400, 800)) == ()
        assert _verdict(checks_world, gap=1299, dispatcher_rule_chars=(400, 800)) == (checks.REASON_ARM_CONFIG_MISSING,)

    def test_a_code_writer_body_shorter_than_the_bare_writer_body_lowers_the_needed_gap(self, checks_world):
        # 2,000 fewer characters are a body term of -500 tokens, so the needed gap is 1000 - 500.
        assert _verdict(checks_world, gap=500, code_writer_extra_chars=-2000) == ()
        assert _verdict(checks_world, gap=499, code_writer_extra_chars=-2000) == (checks.REASON_ARM_CONFIG_MISSING,)

    @pytest.mark.parametrize("gap, bare_size", [(None, BARE_SIZE), (1000, None)], ids=["toolkit-size-null", "bare-size-null"])
    def test_a_null_size_in_either_run_fails_arm_config_missing(self, checks_world, gap, bare_size):
        assert _verdict(checks_world, gap=gap, bare_size=bare_size) == (checks.REASON_ARM_CONFIG_MISSING,)

    def test_a_control_failure_with_a_null_size_returns_both_reasons(self, checks_world):
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world)])
        assert _verdict(checks_world, log, gap=None) == (checks.REASON_INSTRUMENT_MISSING, checks.REASON_ARM_CONFIG_MISSING)

    def test_a_control_failure_with_a_short_gap_returns_both_reasons(self, checks_world):
        log = _parse(checks_world, [_dispatcher_claude_md(checks_world)])
        assert _verdict(checks_world, log, gap=999) == (checks.REASON_INSTRUMENT_MISSING, checks.REASON_ARM_CONFIG_MISSING)

    def test_dispatcher_rule_files_lists_each_session_start_rule_once(self, checks_world):
        log = _parse(checks_world, [
            _dispatcher_claude_md(checks_world),
            _line(checks_world.rule(), SESSION_START),
            _line(checks_world.rule(), SESSION_START),
            _line(checks_world.rule("nested/deep.md"), PATH_GLOB_MATCH),
            _line(checks_world.rule("nested/deep.md"), SESSION_START, agent_id=AGENT_ID),
        ])
        rule_files = checks.dispatcher_rule_files(log, checks_world.toolkit, fixture_dir=checks_world.fixture_dir)
        assert rule_files == (Path(checks_world.rule()).resolve(),)


class TestAccountHooks:
    def _session_file(self, checks_world: World, name: str, text: str) -> Path:
        return _touch(checks_world.config_dir / "sessions" / name, text)

    def test_a_file_whose_first_line_is_the_session_id_passes(self, checks_world):
        self._session_file(checks_world, "4242", f"{SESSION_ID}\nsynthetic second line\n")
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) is None

    def test_an_absent_file_fails_account_hooks_missing(self, checks_world):
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_an_absent_sessions_directory_fails_account_hooks_missing(self, checks_world):
        assert checks.check_account_hooks(checks_world.root / "no-config", SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_a_symlink_counts_as_absent(self, checks_world):
        real_file = _touch(checks_world.root / "real-session-file", f"{SESSION_ID}\n")
        (checks_world.config_dir / "sessions" / "4242").symlink_to(real_file)
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_a_file_for_another_session_counts_as_absent(self, checks_world):
        self._session_file(checks_world, "4242", f"{OTHER_SESSION_ID}\n")
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_a_session_id_only_on_a_later_line_counts_as_absent(self, checks_world):
        self._session_file(checks_world, "4242", f"{OTHER_SESSION_ID}\n{SESSION_ID}\n")
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_a_first_line_that_only_starts_with_the_session_id_counts_as_absent(self, checks_world):
        self._session_file(checks_world, "4242", f"{SESSION_ID}-extended\n")
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_a_first_line_ending_in_a_carriage_return_passes(self, checks_world):
        self._session_file(checks_world, "4242", f"{SESSION_ID}\r\nsynthetic\n")
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) is None

    def test_a_directory_entry_counts_as_absent(self, checks_world):
        (checks_world.config_dir / "sessions" / "4242").mkdir()
        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING

    def test_a_non_regular_entry_whose_first_line_is_the_session_id_counts_as_absent(self, checks_world, monkeypatch):
        """A FIFO with a live writer can present the session ID, which a test cannot stage without a race.
        So a matching file is reported as a FIFO through fstat."""
        self._session_file(checks_world, "4242", f"{SESSION_ID}\n")
        real_fstat = os.fstat
        fstat_calls = []

        def fstat_reporting_a_fifo(descriptor):
            fstat_calls.append(descriptor)
            real_result = real_fstat(descriptor)
            return os.stat_result((stat.S_IFIFO | 0o600, *tuple(real_result)[1:]))

        monkeypatch.setattr(os, "fstat", fstat_reporting_a_fifo)

        assert checks.check_account_hooks(checks_world.config_dir, SESSION_ID) == checks.REASON_ACCOUNT_HOOKS_MISSING
        assert fstat_calls, "the entry's type was never read through fstat"

    @pytest.mark.timing
    def test_a_fifo_entry_counts_as_absent(self, checks_world):
        fifo = checks_world.config_dir / "sessions" / "4242"
        os.mkfifo(fifo)
        reason = call_with_deadline(
            lambda: checks.check_account_hooks(checks_world.config_dir, SESSION_ID),
            deadline_s=30, hang_message=f"check_account_hooks blocked opening the FIFO {fifo.name}",
        )
        assert reason == checks.REASON_ACCOUNT_HOOKS_MISSING

    @pytest.mark.timing
    @pytest.mark.parametrize("fifo_listed_first", [True, False], ids=["fifo-listed-first", "match-listed-first"])
    def test_a_matching_file_still_passes_beside_a_fifo_entry(self, checks_world, monkeypatch, fifo_listed_first):
        sessions_dir = checks_world.config_dir / "sessions"
        fifo = sessions_dir / "0001"
        os.mkfifo(fifo)
        self._session_file(checks_world, "4242", f"{SESSION_ID}\n")
        real_scandir = os.scandir
        ordered_listings = []

        @contextlib.contextmanager
        def scandir_in_a_set_order(path):
            ordered_listings.append(Path(path))
            with real_scandir(path) as entries:
                # The directory yields `fifo` first or last, as a filesystem may.
                yield sorted(entries, key=lambda entry: entry.name, reverse=not fifo_listed_first)

        def scandir_of_the_sessions_dir_in_a_set_order(path):
            if not isinstance(path, (str, os.PathLike)) or Path(path) != sessions_dir:
                return real_scandir(path)
            return scandir_in_a_set_order(path)

        monkeypatch.setattr(os, "scandir", scandir_of_the_sessions_dir_in_a_set_order)
        reason = call_with_deadline(
            lambda: checks.check_account_hooks(checks_world.config_dir, SESSION_ID),
            deadline_s=30, hang_message=f"check_account_hooks blocked opening the FIFO {fifo.name}",
        )
        assert reason is None
        assert ordered_listings == [sessions_dir], "the sessions directory was not listed through the ordered stub"

    @pytest.mark.parametrize("file_text", ["", "\n", "synthetic\n"])
    def test_an_empty_session_id_matches_no_file(self, checks_world, file_text):
        self._session_file(checks_world, "4242", file_text)
        assert checks.check_account_hooks(checks_world.config_dir, "") == checks.REASON_ACCOUNT_HOOKS_MISSING
