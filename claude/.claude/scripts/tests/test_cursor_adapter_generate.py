"""cursor-adapter.py generate: the entries, the command grammar and the launch guard that runs them.

The generated commands are plain POSIX sh text, so the launch-guard tests run them under every shell the machine has."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    IS_ROOT,
    SHIM_INTERPRETER_FLAGS,
    TOOL_MAP_JSON,
    adapter_under_empty_home,  # noqa: F401 (the `adapter` fixture)
    build_spec,
    file_tool_payload,
    load_adapter,
    load_json,
    load_shim,
    recorded_tool_paths,
    shim_argv,
    write_sh_script,
)

SHIM_MODULE = load_shim()


def all_entries(adapter: types.ModuleType) -> list:
    return adapter.generate_entries(python_path="/usr/bin/python3")


def shim_form_entries(adapter: types.ModuleType) -> list:
    return [item for item in all_entries(adapter) if adapter.parse_command(item.entry["command"]).form == "shim"]


class TestGeneratedEntries:
    def test_generation_is_byte_deterministic_across_hash_seeds(self, tmp_path: Path) -> None:
        # Each seed gets its own process, because one interpreter hashes every string the same way throughout.
        outputs = [
            subprocess.run(
                [sys.executable, "-S", str(ADAPTER_DIR / "cursor-adapter.py"), "generate", "--python", "/usr/bin/python3"],
                capture_output=True,
                env={"HOME": str(tmp_path / "home"), "PYTHONHASHSEED": seed},
                timeout=60,
                check=True,
            ).stdout
            for seed in ("1", "2", "3", "4", "5")
        ]
        assert outputs[0]
        assert all(output == outputs[0] for output in outputs)

    def test_generation_is_byte_deterministic_within_one_process(self, adapter: types.ModuleType) -> None:
        first = json.dumps([item.entry for item in all_entries(adapter)])
        second = json.dumps([item.entry for item in all_entries(adapter)])
        assert first == second

    def test_a_matcher_token_with_no_cursor_tool_name_fails_generation_naming_it_even_beside_a_mapped_one(
        self, adapter: types.ModuleType
    ) -> None:
        mixed = adapter.Registration("PreToolUse", "Bash|NotebookEdit", "x.sh", None, True)
        with pytest.raises(adapter.AdapterError, match="NotebookEdit"):
            adapter.generate_entries(python_path="/p", registrations=[mixed], listed_keys=set())

    def test_a_listed_registration_with_an_unmapped_token_never_reaches_the_matcher_mapping(
        self, adapter: types.ModuleType
    ) -> None:
        listed = adapter.Registration("PreToolUse", "Skill", "x.sh", None, True)
        emitted = adapter.generate_entries(python_path="/p", registrations=[listed], listed_keys={listed.key})
        assert listed.key not in {item.registration_key for item in emitted}

    def _settings(self, tmp_path: Path, hooks: dict) -> str:
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"hooks": hooks}))
        return str(path)

    def test_a_hook_that_is_not_a_command_hook_is_refused_by_event_and_matcher(
        self, adapter: types.ModuleType, tmp_path: Path
    ) -> None:
        settings = self._settings(
            tmp_path, {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "prompt", "prompt": "be careful"}]}]}
        )
        with pytest.raises(adapter.AdapterError, match=r"PreToolUse\|Bash"):
            adapter.read_registrations(settings)

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ({"onFailure": "block"}, {}),
            ({}, {"onFailure": "block"}),
            ({"timeout": 5}, {"timeout": 9}),
            ({"timeout": 5}, {}),
        ],
        ids=["block-then-open", "open-then-block", "timeouts-differ", "timeout-then-none"],
    )
    def test_two_registrations_of_one_key_that_disagree_are_refused(
        self, adapter: types.ModuleType, tmp_path: Path, first: dict, second: dict
    ) -> None:
        command = {"type": "command", "command": "/h/hooks/x.sh"}
        groups = [{"matcher": "Bash", "hooks": [{**command, **first}]}, {"matcher": "Bash", "hooks": [{**command, **second}]}]
        with pytest.raises(adapter.AdapterError, match=r"appears twice"):
            adapter.read_registrations(self._settings(tmp_path, {"PreToolUse": groups}))

    def test_two_registrations_of_one_key_that_agree_collapse_to_one(self, adapter: types.ModuleType, tmp_path: Path) -> None:
        command = {"type": "command", "command": "/h/hooks/x.sh", "onFailure": "block", "timeout": 5}
        groups = [{"matcher": "Bash", "hooks": [command]}, {"matcher": "Bash", "hooks": [command]}]

        registrations = adapter.read_registrations(self._settings(tmp_path, {"PreToolUse": groups}))

        assert [registration.key for registration in registrations] == ["PreToolUse|Bash|x.sh"]

    def test_the_default_deadline_is_the_shims_and_no_second_copy_of_it_is_held(self, adapter: types.ModuleType) -> None:
        assert not hasattr(adapter, "DEFAULT_DEADLINE_SECONDS")
        entry = next(item for item in all_entries(adapter) if item.label == "require-code-review")
        assert adapter.parse_command(entry.entry["command"]).argument_value("--deadline") == str(
            SHIM_MODULE.DEFAULT_DEADLINE_SECONDS
        )

    def test_the_generated_interpreter_flags_equal_the_test_builders(self, adapter: types.ModuleType) -> None:
        builder_flags = tuple(shim_argv(shim=Path("/shim.py"))[1:3])
        assert builder_flags == SHIM_INTERPRETER_FLAGS
        for item in all_entries(adapter):
            assert " ".join(("-I", "-S")) in item.entry["command"]
            assert " ".join(builder_flags) in item.entry["command"], item.label

    def test_every_generated_shim_form_command_parses_under_the_shims_parser(self, adapter: types.ModuleType) -> None:
        entries = shim_form_entries(adapter)
        assert entries
        for item in entries:
            parsed = adapter.parse_command(item.entry["command"])
            assert SHIM_MODULE.parse_arguments(parsed.arguments) is not None, item.label

    def test_the_parsers_kind_the_tail_and_the_entrys_fail_closed_field_agree_for_every_shim_form_entry(
        self, adapter: types.ModuleType
    ) -> None:
        for item in shim_form_entries(adapter):
            parsed = adapter.parse_command(item.entry["command"])
            shim_kind = SHIM_MODULE.parse_arguments(parsed.arguments).fail_closed
            assert shim_kind is parsed.fail_closed is (item.entry.get("failClosed") is True), item.label

    def test_the_grammars_flags_and_modes_equal_the_shims_in_both_directions(self, adapter: types.ModuleType) -> None:
        assert set(adapter.SHIM_VALUE_FLAGS) == set(SHIM_MODULE.VALUE_FLAGS)
        assert set(adapter.SHIM_SWITCH_FLAGS) == set(SHIM_MODULE.SWITCH_FLAGS)
        assert set(adapter.SHIM_MODES) == set(SHIM_MODULE.MODES)

    def test_a_seeded_unknown_flag_fails_both_the_grammar_and_the_shims_parser(self, adapter: types.ModuleType) -> None:
        arguments = ["--claude-event", "PreToolUse", "--unknown-flag"]
        assert adapter._shim_arguments_parse(arguments) is False
        assert SHIM_MODULE.parse_arguments(arguments) is None
        assert adapter._shim_arguments_parse(arguments[:2]) is True

    def test_the_scanned_set_holds_every_mode(self, adapter: types.ModuleType) -> None:
        modes = {adapter.parse_command(item.entry["command"]).argument_value("--mode") for item in shim_form_entries(adapter)}
        assert modes == set(SHIM_MODULE.MODES)

    def test_the_one_check_form_command_is_the_session_start_check_entry(self, adapter: types.ModuleType) -> None:
        checks = [item for item in all_entries(adapter) if adapter.parse_command(item.entry["command"]).form == "check"]
        assert [item.label for item in checks] == ["check"]
        parsed = adapter.parse_command(checks[0].entry["command"])
        assert parsed.arguments == ["check", "--hook-env"]
        assert parsed.fail_closed is False
        assert parsed.script_path == adapter.stow_adapter_path()
        assert checks[0].event == "sessionStart"
        assert "failClosed" not in checks[0].entry
        assert adapter.build_parser().parse_args(parsed.arguments).hook_env is True

    def test_each_entrys_deadline_follows_the_shims_range_and_every_fail_closed_timeout_exceeds_the_worst_case(
        self, adapter: types.ModuleType
    ) -> None:
        for item in shim_form_entries(adapter):
            parsed = adapter.parse_command(item.entry["command"])
            deadline = int(parsed.argument_value("--deadline"))
            assert SHIM_MODULE.MIN_DEADLINE_SECONDS <= deadline <= SHIM_MODULE.MAX_DEADLINE_SECONDS
            assert item.entry["timeout"] == SHIM_MODULE.entry_timeout_seconds(deadline)
            if item.fail_closed:
                assert item.entry["timeout"] > SHIM_MODULE.worst_case_wall_seconds(deadline), item.label

    def test_a_registration_with_no_timeout_gets_the_default_deadline_and_a_fifteen_second_entry_timeout(
        self, adapter: types.ModuleType
    ) -> None:
        unset = adapter.Registration("PreToolUse", "Bash", "x.sh", None, True)
        entry = adapter.generate_entries(python_path="/p", registrations=[unset], listed_keys=set())[0]
        assert entry.entry["timeout"] == 15
        assert adapter.parse_command(entry.entry["command"]).argument_value("--deadline") == "10"

    def test_the_two_ask_hooks_on_the_write_tools_run_fail_closed_and_a_hook_that_is_not_one_of_them_does_not(
        self, adapter: types.ModuleType
    ) -> None:
        registrations = [
            adapter.Registration("PreToolUse", "Write", script, None, False)
            for script in (*sorted(adapter.ASK_ON_WRITE_SCRIPTS), "plain-hook.sh")
        ]
        emitted = adapter.generate_entries(python_path="/p", registrations=registrations, listed_keys=set())
        by_label = {item.label: item for item in emitted}
        for script in adapter.ASK_ON_WRITE_SCRIPTS:
            item = by_label[script.removesuffix(".sh")]
            assert item.fail_closed
            assert item.entry["failClosed"] is True
        assert not by_label["plain-hook"].fail_closed
        assert "failClosed" not in by_label["plain-hook"].entry

    def test_the_adapter_only_entries_are_present_with_their_modes_and_events(self, adapter: types.ModuleType) -> None:
        by_label = {item.label: item for item in all_entries(adapter)}
        assert (by_label["classify"].event, by_label["classify"].entry.get("matcher")) == ("preToolUse", None)
        assert (by_label["pin"].event, by_label["pin"].entry.get("matcher")) == ("preToolUse", "Shell")
        assert (by_label["pin-check"].event, by_label["pin-check"].entry.get("matcher")) == ("beforeShellExecution", None)
        for label in ("classify", "pin", "pin-check"):
            assert by_label[label].fail_closed

    def test_an_if_filtered_pair_dedupes_to_one_entry_and_no_entry_carries_an_if_key(
        self, adapter: types.ModuleType, tmp_path: Path
    ) -> None:
        command = {"type": "command", "command": "/h/hooks/x.sh"}
        groups = [
            {"matcher": "Bash", "hooks": [{**command, "if": "Bash(first-form*)"}]},
            {"matcher": "Bash", "hooks": [{**command, "if": "Bash(second-form*)"}]},
        ]
        registrations = adapter.read_registrations(self._settings(tmp_path, {"PreToolUse": groups}))
        assert len(registrations) == 1

        emitted = adapter.generate_entries(python_path="/p", registrations=registrations, listed_keys=set())

        assert sum(1 for item in emitted if item.label == "x") == 1
        assert all("if" not in item.entry for item in emitted)

    @pytest.mark.parametrize(
        "hooks",
        [[], {"PreToolUse": {}}, {"PreToolUse": ["x"]}, {"PreToolUse": [{"hooks": {}}]}, {"PreToolUse": [{"hooks": ["x"]}]}],
        ids=["hooks-is-a-list", "event-is-an-object", "group-is-a-string", "group-hooks-is-an-object", "hook-is-a-string"],
    )
    def test_a_settings_hooks_member_of_the_wrong_shape_is_one_refusal_naming_the_file_from_generate(
        self,
        adapter: types.ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        hooks: object,
    ) -> None:
        settings = self._settings(tmp_path, hooks)
        monkeypatch.setattr(adapter, "settings_json_path", lambda: settings)

        status = adapter.main(["generate"])

        assert status == 1
        assert settings in capsys.readouterr().err

    def test_a_registration_that_drops_without_a_listing_fails_generation_naming_it(self, adapter: types.ModuleType) -> None:
        unlisted = adapter.Registration("Notification", "permission_prompt", "x.sh", None, False)
        with pytest.raises(adapter.AdapterError, match=r"Notification\|permission_prompt\|x\.sh"):
            adapter.generate_entries(python_path="/p", registrations=[unlisted], listed_keys=set())

    def test_a_listed_registration_emits_nothing(self, adapter: types.ModuleType) -> None:
        listed = adapter.Registration("Notification", "permission_prompt", "x.sh", None, False)
        emitted = adapter.generate_entries(python_path="/p", registrations=[listed], listed_keys={listed.key})
        assert [item.label for item in emitted] == ["classify", "pin", "pin-check", "check"]

    def test_every_settings_registration_is_emitted_or_listed_and_no_listed_one_is_emitted(
        self, adapter: types.ModuleType
    ) -> None:
        registrations = adapter.read_registrations(adapter.settings_json_path())
        listed = adapter.exception_keys(str(ADAPTER_DIR / "parity-exceptions.json"))
        emitted_keys = {item.registration_key for item in all_entries(adapter)}
        keys = {registration.key for registration in registrations}
        assert keys - listed == emitted_keys - {None}
        assert not emitted_keys & listed

    def test_every_label_fits_the_logs_registration_field(self, adapter: types.ModuleType) -> None:
        for item in all_entries(adapter):
            assert SHIM_MODULE.log_value("reg", item.label) == item.label

    def test_the_fault_text_ends_with_the_shims_recovery_sentence_and_names_no_uninstall_state_dir_or_log(
        self, adapter: types.ModuleType
    ) -> None:
        text = adapter.FAULT_TEXT
        assert text.endswith(SHIM_MODULE.RECOVERY_SENTENCE)
        assert text.startswith("The Cursor adapter's hook shim ended without deciding this call.")
        assert "claude-config repository" in text
        for forbidden in ("uninstall", adapter.STATE_DIR_NAME, adapter.LOG_FILE_NAME):
            assert forbidden not in text

    def test_print_entry_renders_one_registration_against_a_scratch_shim_and_writes_nothing(
        self, adapter: types.ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        key = "SessionStart|compact|restore-authorization-boundary-on-compact.sh"
        status = adapter.main(["generate", "--print-entry", key, "--shim", "/scratch/hook-shim.py"])
        document = json.loads(capsys.readouterr().out)
        assert status == 0
        assert list(document["hooks"]) == ["sessionStart"]
        assert "/scratch/hook-shim.py" in document["hooks"]["sessionStart"][0]["command"]
        assert not (tmp_path / "home").exists()

    def test_print_entry_with_an_unknown_key_refuses(self, adapter: types.ModuleType, capsys: pytest.CaptureFixture) -> None:
        assert adapter.main(["generate", "--print-entry", "PreToolUse|Nope|nothing.sh", "--shim", "/s"]) == 1
        assert "No settings.json registration" in capsys.readouterr().err

    def test_print_entry_without_a_shim_path_refuses_and_prints_no_document(
        self, adapter: types.ModuleType, capsys: pytest.CaptureFixture
    ) -> None:
        key = "SessionStart|compact|restore-authorization-boundary-on-compact.sh"

        assert adapter.main(["generate", "--print-entry", key]) == 1

        captured = capsys.readouterr()
        assert "--print-entry needs --shim PATH." in captured.err
        assert captured.out == ""


class TestGenerationGuards:
    """Each guard `generate` runs over a registration or the finished entries, fed a seeded violation and a passing control."""

    def _generate(self, adapter: types.ModuleType, registration) -> list:
        return adapter.generate_entries(python_path="/p", registrations=[registration], listed_keys=set())

    @pytest.mark.parametrize("timeout", [1, 61, True, "10"], ids=repr)
    def test_a_timeout_outside_the_shims_deadline_range_or_of_another_type_is_refused_naming_the_registration(
        self, adapter: types.ModuleType, timeout: object
    ) -> None:
        registration = adapter.Registration("PreToolUse", "Bash", "x.sh", timeout, True)

        with pytest.raises(adapter.AdapterError, match=r"PreToolUse\|Bash\|x\.sh has a timeout"):
            self._generate(adapter, registration)

    @pytest.mark.parametrize("timeout", [SHIM_MODULE.MIN_DEADLINE_SECONDS, 10, SHIM_MODULE.MAX_DEADLINE_SECONDS], ids=repr)
    def test_a_timeout_inside_the_deadline_range_is_emitted_as_the_deadline(
        self, adapter: types.ModuleType, timeout: int
    ) -> None:
        entry = self._generate(adapter, adapter.Registration("PreToolUse", "Bash", "x.sh", timeout, True))[0]

        assert adapter.parse_command(entry.entry["command"]).argument_value("--deadline") == str(timeout)

    def test_a_fail_closed_registration_on_an_event_with_no_deny_shape_is_refused_naming_the_entry_and_the_event(
        self, adapter: types.ModuleType
    ) -> None:
        registration = adapter.Registration("SessionStart", None, "x.sh", None, True)

        with pytest.raises(adapter.AdapterError, match=r"fail-closed entry x is on sessionStart"):
            self._generate(adapter, registration)

    def test_a_fail_open_registration_on_that_event_and_a_fail_closed_one_on_a_tool_event_are_emitted(
        self, adapter: types.ModuleType
    ) -> None:
        assert self._generate(adapter, adapter.Registration("SessionStart", None, "x.sh", None, False))[0].event == "sessionStart"
        assert self._generate(adapter, adapter.Registration("PreToolUse", "Bash", "x.sh", None, True))[0].event == "preToolUse"

    def test_a_label_past_the_log_registration_fields_maximum_is_refused_naming_it_and_one_at_the_maximum_is_emitted(
        self, adapter: types.ModuleType
    ) -> None:
        maximum = dict(SHIM_MODULE.LOG_FIELDS)["reg"]
        at_maximum = "a" * maximum
        past_maximum = "a" * (maximum + 1)

        assert self._generate(adapter, adapter.Registration("PreToolUse", "Bash", f"{at_maximum}.sh", None, False))[0].label == (
            at_maximum
        )
        with pytest.raises(adapter.AdapterError, match=f"The label {past_maximum} does not fit"):
            self._generate(adapter, adapter.Registration("PreToolUse", "Bash", f"{past_maximum}.sh", None, False))

    def test_a_fail_closed_timeout_that_does_not_exceed_the_worst_case_is_refused_naming_the_entry(
        self, adapter: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registration = adapter.Registration("PreToolUse", "Bash", "x.sh", None, True)
        assert self._generate(adapter, registration)[0].entry["timeout"] > 0
        shim = adapter.load_shim()
        monkeypatch.setattr(shim, "entry_timeout_seconds", shim.worst_case_wall_seconds)

        with pytest.raises(adapter.AdapterError, match=r"fail-closed entry x has a timeout below the shim's worst-case"):
            self._generate(adapter, registration)


class TestHooksJsonVocabulary:
    def test_the_generated_document_uses_only_pinned_events_and_keys(self, adapter: types.ModuleType) -> None:
        hooks = adapter.hooks_object(all_entries(adapter))
        assert adapter.hooks_vocabulary_problems(hooks) == []
        assert set(hooks) <= set(load_json(ADAPTER_DIR / "field-table.json")["events"])
        assert set(hooks) <= adapter.HOOKS_JSON_EVENTS

    def test_the_pinned_entry_keys_are_the_hooks_docs_five(self, adapter: types.ModuleType) -> None:
        assert {"command", "matcher", "timeout", "failClosed", "loop_limit"} == adapter.HOOKS_JSON_ENTRY_KEYS

    def test_every_key_a_generated_entry_holds_is_among_the_literal_five(self, adapter: types.ModuleType) -> None:
        keys = {key for item in all_entries(adapter) for key in item.entry}
        assert keys <= {"command", "matcher", "timeout", "failClosed", "loop_limit"}

    def test_the_pinned_events_are_exactly_the_field_tables(self, adapter: types.ModuleType) -> None:
        assert set(load_json(ADAPTER_DIR / "field-table.json")["events"]) == adapter.HOOKS_JSON_EVENTS

    def test_a_seeded_unknown_event_and_key_are_named(self, adapter: types.ModuleType) -> None:
        problems = adapter.hooks_vocabulary_problems({"onDance": [], "stop": [{"command": "x", "surprise": 1}]})
        assert any("onDance" in problem for problem in problems)
        assert any("surprise" in problem for problem in problems)

    def test_the_documents_version_is_the_one_the_hooks_doc_requires(
        self, adapter: types.ModuleType, capsys: pytest.CaptureFixture
    ) -> None:
        adapter.main(["generate"])
        assert json.loads(capsys.readouterr().out)["version"] == 1


class TestReadGateBothEvents:
    def test_every_pre_tool_use_read_gate_registers_on_pre_tool_use_and_before_read_file(self, adapter: types.ModuleType) -> None:
        registrations = [
            registration
            for registration in adapter.read_registrations(adapter.settings_json_path())
            if registration.event == "PreToolUse" and "Read" in registration.matcher.split("|")
        ]
        assert registrations
        entries = all_entries(adapter)
        for registration in registrations:
            events = sorted(item.event for item in entries if item.registration_key == registration.key)
            assert events == ["beforeReadFile", "preToolUse"], registration.key

    def test_the_before_read_file_entries_are_fail_closed_and_matcher_less(self, adapter: types.ModuleType) -> None:
        entries = [item for item in all_entries(adapter) if item.event == "beforeReadFile"]
        assert entries
        assert all(item.fail_closed and "matcher" not in item.entry for item in entries)


# --- the launch guard ---


SHELLS = {name: shutil.which(name) for name in ("sh", "bash", "zsh")}
SHELL_PARAMS = [pytest.param(name, id=name) for name in SHELLS]
ODD_HOME_NAME = "home it's here"


class GuardWorld:
    """A scratch home whose name holds a space and a quote, with a real copy of the adapter at its stow path."""

    def __init__(self, tmp_path: Path, adapter: types.ModuleType) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / ODD_HOME_NAME
        self.stow = self.home / ".claude" / "scripts" / "cursor-adapter"
        shutil.copytree(ADAPTER_DIR, self.stow, ignore=shutil.ignore_patterns("__pycache__"))
        (self.home / ".claude" / "hooks").mkdir()
        self.adapter = adapter

    def entry(self, label: str, *, python_path: str | None = None):
        emitted = self.adapter.generate_entries(python_path=python_path or sys.executable)
        item = next(item for item in emitted if item.label == label)
        return item

    def run(
        self, shell: str, command: str, *, payload: bytes = b"{}", extra_env: dict | None = None
    ) -> subprocess.CompletedProcess:
        environment = {"HOME": str(self.home), "PATH": os.environ["PATH"], **(extra_env or {})}
        return subprocess.run(
            [SHELLS[shell], "-c", command],
            input=payload,
            capture_output=True,
            env=environment,
            cwd=self.tmp_path,
            check=False,
            timeout=60,
        )


@pytest.fixture
def guard_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GuardWorld:
    monkeypatch.setenv("HOME", str(tmp_path / ODD_HOME_NAME))
    return GuardWorld(tmp_path, load_adapter())


def script_interpreter(tmp_path: Path, name: str, body: str) -> str:
    return str(write_sh_script(tmp_path / name, body))


FAULTY_INTERPRETERS = {
    "exits-one": "exit 1\n",
    "kills-itself": "kill -9 $$\n",
    "exits-zero-with-empty-stdout": "exit 0\n",
    "prints-an-open-brace-and-exits-one": "printf '{'\nexit 1\n",
    "writes-a-traceback-naming-its-path-and-exits-one": "echo 'Traceback: File \"/secret/path.py\", line 1' >&2\nexit 1\n",
}
LABELS = {"fail-closed": "classify", "informational": "nudge-worktree-anchor", "check": "check"}


def assert_fault_outcome(world: GuardWorld, result: subprocess.CompletedProcess, item) -> None:
    if item.fail_closed:
        assert result.returncode == 2
        document = json.loads(result.stdout)
        expected = SHIM_MODULE.deny_document(item.event, world.adapter.FAULT_TEXT)
        assert document == expected
        assert document["agent_message"] == document["user_message"] == world.adapter.FAULT_TEXT
        assert result.stderr.decode() == world.adapter.FAULT_TEXT
    else:
        assert result.returncode == 0
        assert result.stdout == b"{}"
        assert result.stderr == b""


class TestLaunchGuard:
    @pytest.mark.parametrize("kind", list(LABELS))
    def test_a_generated_command_is_read_as_owned(self, guard_world: GuardWorld, kind: str) -> None:
        item = guard_world.entry(LABELS[kind])

        assert guard_world.adapter.ownership(item.entry) == "owned"

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    def test_an_informational_shim_form_entry_hands_the_shim_its_exact_arguments_and_stdin_under_a_home_with_a_space_and_a_quote(
        self, guard_world: GuardWorld, tmp_path: Path, shell: str
    ) -> None:
        # The real shim prints `{}` for this form on a fault and on a real run, so only a recording shim tells them apart.
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        scratch = guard_world.home / "scratch shim.py"
        observed_file = tmp_path / "observed.json"
        scratch.write_text(
            "import json, sys\n"
            f"open({str(observed_file)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}))\n"
            "print(json.dumps({'observed': True}))\n"
        )
        emitted = guard_world.adapter.generate_entries(python_path=sys.executable, shim_path=str(scratch))
        item = next(item for item in emitted if item.label == LABELS["informational"])

        result = guard_world.run(shell, item.entry["command"], payload=b'{"payload": 1}')

        assert json.loads(result.stdout) == {"observed": True}
        observed = json.loads(observed_file.read_text())
        parsed = guard_world.adapter._parse_command(item.entry["command"], str(scratch), guard_world.adapter.stow_adapter_path())
        assert observed["argv"] == parsed.arguments
        assert any(ODD_HOME_NAME in argument for argument in observed["argv"])
        assert observed["stdin"] == '{"payload": 1}'

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    @pytest.mark.parametrize("kind", ["fail-closed", "informational"])
    @pytest.mark.parametrize("fault", ["missing-shim", "unreadable-shim", "shim-syntax-error"])
    def test_a_shim_form_entry_gives_its_kinds_outcome_when_the_shim_is_missing_unreadable_or_broken(
        self, guard_world: GuardWorld, shell: str, kind: str, fault: str
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        if fault == "unreadable-shim" and IS_ROOT:
            pytest.skip("absent: root reads a mode-000 file")
        shim = guard_world.stow / "hook-shim.py"
        if fault == "missing-shim":
            shim.unlink()
        elif fault == "unreadable-shim":
            shim.chmod(0)
        else:
            shim.write_text("def broken(:\n")
        item = guard_world.entry(LABELS[kind])

        result = guard_world.run(shell, item.entry["command"])

        assert_fault_outcome(guard_world, result, item)

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    @pytest.mark.parametrize("fault", ["missing-adapter", "unreadable-adapter", "adapter-syntax-error"])
    def test_the_check_entry_prints_the_informational_outcome_when_its_runtime_file_is_missing_unreadable_or_broken(
        self, guard_world: GuardWorld, shell: str, fault: str
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        if fault == "unreadable-adapter" and IS_ROOT:
            pytest.skip("absent: root reads a mode-000 file")
        adapter_file = guard_world.stow / "cursor-adapter.py"
        item = guard_world.entry("check")
        if fault == "missing-adapter":
            adapter_file.unlink()
        elif fault == "unreadable-adapter":
            adapter_file.chmod(0)
        else:
            adapter_file.write_text("def broken(:\n")

        result = guard_world.run(shell, item.entry["command"])

        assert_fault_outcome(guard_world, result, item)

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    @pytest.mark.parametrize("kind", list(LABELS))
    @pytest.mark.parametrize("interpreter", ["missing", "not-executable", *FAULTY_INTERPRETERS])
    def test_a_missing_or_faulty_recorded_interpreter_gives_each_kinds_outcome(
        self, guard_world: GuardWorld, tmp_path: Path, shell: str, kind: str, interpreter: str
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        if interpreter == "missing":
            python_path = str(tmp_path / "no-such-python")
        elif interpreter == "not-executable":
            python_path = str(tmp_path / "not-executable-python")
            Path(python_path).write_text("#!/bin/sh\nexit 0\n")
        else:
            python_path = script_interpreter(tmp_path, f"interp {interpreter}", FAULTY_INTERPRETERS[interpreter])
        item = guard_world.entry(LABELS[kind], python_path=python_path)

        result = guard_world.run(shell, item.entry["command"])

        assert_fault_outcome(guard_world, result, item)
        assert b"Traceback" not in result.stderr
        assert b"/secret/path.py" not in result.stderr + result.stdout

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    @pytest.mark.parametrize("size", ["small", "one-mebibyte"])
    @pytest.mark.parametrize("kind", ["fail-closed", "informational"])
    def test_a_document_the_interpreter_prints_passes_through_with_its_escapes_intact(
        self, guard_world: GuardWorld, tmp_path: Path, shell: str, size: str, kind: str
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        text = 'line1\nline2 "quoted" back\\slash \\c é %s %d 100%'
        if size == "one-mebibyte":
            text += "x" * (1024 * 1024)
        document_file = tmp_path / "document.json"
        document_file.write_text(json.dumps({"permission": "deny", "agent_message": text}, ensure_ascii=True), encoding="ascii")
        python_path = script_interpreter(tmp_path, "printing-interpreter", f"cat '{document_file}'\n")
        item = guard_world.entry(LABELS[kind], python_path=python_path)

        result = guard_world.run(shell, item.entry["command"])

        assert result.returncode == 0
        assert json.loads(result.stdout) == {"permission": "deny", "agent_message": text}

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    def test_every_shim_form_entry_with_a_scratch_shim_holding_a_syntax_error_gives_its_kinds_outcome(
        self, guard_world: GuardWorld, tmp_path: Path, shell: str
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        scratch = tmp_path / "scratch-shim.py"
        scratch.write_text("def broken(:\n")
        emitted = guard_world.adapter.generate_entries(python_path=sys.executable, shim_path=str(scratch))
        shim_entries = [item for item in emitted if str(scratch) in item.entry["command"]]
        assert shim_entries
        assert len(shim_entries) == len(emitted) - 1, "every entry but the sessionStart check runs the shim"

        for item in shim_entries:
            assert_fault_outcome(guard_world, guard_world.run(shell, item.entry["command"]), item)

    def test_the_fault_texts_deny_document_for_each_fail_closed_event_has_the_event_shape(self, guard_world: GuardWorld) -> None:
        for item in guard_world.adapter.generate_entries(python_path=sys.executable):
            if item.fail_closed:
                document = json.loads(guard_world.adapter.parse_command(item.entry["command"]).deny_document)
                assert document == SHIM_MODULE.deny_document(item.event, guard_world.adapter.FAULT_TEXT), item.label

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    def test_the_real_shim_through_the_real_generated_command_returns_a_deny_whose_text_holds_non_ascii_and_a_newline(
        self, guard_world: GuardWorld, tmp_path: Path, shell: str
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        world = guard_world
        record_dir = world.home / ".cursor" / "cursor-adapter"
        record_dir.mkdir(parents=True)
        config_dir = tmp_path / "config"
        (config_dir / "cursor-adapter-state").mkdir(parents=True, mode=0o700)
        (record_dir / "install.json").write_text(
            json.dumps(
                {
                    "routed_config_dir": str(config_dir),
                    "contract_version": SHIM_MODULE.CONTRACT_VERSION,
                    "tool_paths": {**recorded_tool_paths(), "python3": sys.executable},
                    "tool_map": load_json(TOOL_MAP_JSON),
                }
            )
        )
        reason = "résumé ☃\nsecond line"
        hook = world.home / ".claude" / "hooks" / "deny-env-reads.sh"
        hook.write_text(
            "#!/usr/bin/env bash\ncat >/dev/null\n"
            f'python3 -c \'import json; print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse", '
            f'"permissionDecision": "deny", "permissionDecisionReason": "{reason.encode("unicode_escape").decode()}"}}}}))\'\n'
        )
        hook.chmod(0o755)
        root = tmp_path / "workspace"
        root.mkdir()
        item = next(
            item
            for item in world.adapter.generate_entries(python_path=sys.executable, only_key="PreToolUse|Read|deny-env-reads.sh")
            if item.event == "preToolUse"
        )
        payload = file_tool_payload(root, "Read", str(root / ".env"))
        assert build_spec("read_pre", root).cursor["hook_event_name"] == "preToolUse"

        result = world.run(shell, item.entry["command"], payload=json.dumps(payload).encode())

        assert result.returncode == 0, result.stderr
        document = json.loads(result.stdout)
        assert document["permission"] == "deny"
        assert reason in document["agent_message"]
        assert document["agent_message"] == document["user_message"]

    @pytest.mark.parametrize("shell", SHELL_PARAMS)
    @pytest.mark.parametrize("state_dir_writable", [True, False], ids=["writable-state-dir", "unwritable-state-dir"])
    def test_the_real_check_entry_reports_a_removed_wrapper_and_exits_zero_even_when_the_log_cannot_be_written(
        self, guard_world: GuardWorld, tmp_path: Path, shell: str, state_dir_writable: bool
    ) -> None:
        if SHELLS[shell] is None:
            pytest.skip(f"absent: {shell}")
        if not state_dir_writable and IS_ROOT:
            pytest.skip("absent: root writes to a read-only directory")
        world = guard_world
        record_dir = world.home / ".cursor" / "cursor-adapter"
        record_dir.mkdir(parents=True)
        config_dir = tmp_path / "config"
        state_dir = config_dir / "cursor-adapter-state"
        state_dir.mkdir(parents=True, mode=0o700)
        gone = tmp_path / "gone" / "timeout"
        (record_dir / "install.json").write_text(
            json.dumps(
                {
                    "routed_config_dir": str(config_dir),
                    "contract_version": SHIM_MODULE.CONTRACT_VERSION,
                    "tool_paths": {
                        **{k: v for k, v in recorded_tool_paths().items() if k not in ("timeout", "gtimeout")},
                        "python3": sys.executable,
                        "timeout": str(gone),
                    },
                    "tool_map": load_json(TOOL_MAP_JSON),
                }
            )
        )
        if not state_dir_writable:
            state_dir.chmod(0o500)
        try:
            item = world.entry("check")

            result = world.run(shell, item.entry["command"])
        finally:
            state_dir.chmod(0o700)

        assert result.returncode == 0, result.stderr
        context = json.loads(result.stdout)["additional_context"]
        assert f"{gone} could not be started (cause ENOENT)" in context
