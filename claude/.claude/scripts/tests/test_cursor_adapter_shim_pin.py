"""hook-shim.py: the modes beyond the hook run, and the directory pin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from ._cursor_shim_helpers import (
    CANDIDATE_SHELLS,
    SHELL_PROBE_TIMEOUT_SECONDS,
    SHIM_MODULE,
    _assert_deny_text_rules,
    _pin,
    _pin_check,
    _pin_prefix,
    _prefixed,
    _probe_ran,
    _run_probe,
    _scratch,
    _walkless_env,
    _workspace,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ShimEnv,
    build_spec,
    file_tool_payload,
    hook_args,
    make_shim_env,
    read_log_lines,
    recorded_tool_paths,
    recording_probe_script,
    run_shim,
    shell_pre_payload,
    shim_json,
    write_hook_script,
)

# --- the modes beyond the hook run ---


def _non_shell_pin_payloads(root: Path) -> dict[str, dict]:
    """A call the pin must leave alone, by case: three non-Shell tools and a Shell call on the wrong event."""
    mcp_call = shell_pre_payload(root, "unused")
    mcp_call["tool_name"] = "MCP:x"
    mcp_call["tool_input"] = {}
    return {
        "read": file_tool_payload(root, "Read", str(root / "src" / "a.txt")),
        "write": file_tool_payload(root, "Write", str(root / "src" / "a.txt"), content="x"),
        "mcp": mcp_call,
        "before-shell-execution": build_spec("shell_before_execution", root).cursor,
    }


NON_SHELL_PIN_CASES = ["read", "write", "mcp", "before-shell-execution"]


class TestPinModeLeavesNonShellCallsAlone:
    @pytest.mark.parametrize("case", NON_SHELL_PIN_CASES)
    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_a_call_that_is_not_a_pre_tool_use_shell_call_prints_empty_output_with_no_rewrite(
        self, tmp_path: Path, case: str, fail_closed: bool
    ) -> None:
        payload = _non_shell_pin_payloads(_workspace(tmp_path))[case]
        output, env = _run_probe(tmp_path, payload, mode="pin", fail_closed=fail_closed)
        assert output == {}
        assert "updated_input" not in output
        assert read_log_lines(env)[-1]["out"] == "not-shell"

    def test_a_pre_tool_use_shell_call_is_still_rewritten_as_the_polarity_control(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        output, env = _run_probe(tmp_path, shell_pre_payload(root, "ls"), env=_walkless_env(tmp_path), mode="pin")
        assert output["updated_input"]["command"] == _pin_prefix(env, root) + "ls"
        assert read_log_lines(env)[-1]["out"] == "pin-no-host"


class TestModesThatNeedNoWrapper:
    """Only a hook run needs `timeout` or `gtimeout`. The other modes run on a record that names neither."""

    def _wrapperless_env(self, tmp_path: Path) -> ShimEnv:
        paths = recorded_tool_paths()
        return make_shim_env(tmp_path, tool_paths={"jq": paths["jq"], "bash": paths["bash"]})

    def test_a_hook_run_on_a_record_with_no_wrapper_denies_as_the_polarity_control(self, tmp_path: Path) -> None:
        output, _ = _run_probe(
            tmp_path, shell_pre_payload(_workspace(tmp_path), "ls"), env=self._wrapperless_env(tmp_path), matcher="Bash"
        )
        assert output["permission"] == "deny"
        assert "install-record" in output["agent_message"]
        assert not _probe_ran(tmp_path)

    def test_pin_rewrites_a_shell_call_on_a_record_with_no_wrapper(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        output, env = _run_probe(tmp_path, shell_pre_payload(root, "ls"), env=self._wrapperless_env(tmp_path), mode="pin")
        assert output["updated_input"]["command"] == _pin_prefix(env, root) + "ls"
        assert read_log_lines(env)[-1]["out"] == "pin-no-host"

    def test_pin_check_passes_a_pinned_command_on_a_record_with_no_wrapper(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        payload = build_spec("shell_before_execution", root).cursor
        env = self._wrapperless_env(tmp_path)
        payload["command"] = _pin_prefix(env, root) + "ls"
        output, env = _run_probe(tmp_path, payload, env=env, mode="pin-check")
        assert output == {}
        assert read_log_lines(env)[-1]["out"] == "pin-check-ok"

    def test_classify_denies_an_unknown_tool_on_a_record_with_no_wrapper(self, tmp_path: Path) -> None:
        payload = shell_pre_payload(_workspace(tmp_path), "ls")
        payload["tool_name"] = "BrandNewTool"
        output, env = _run_probe(tmp_path, payload, env=self._wrapperless_env(tmp_path), mode="classify")
        assert output["permission"] == "deny"
        assert "unknown-tool" in output["agent_message"]
        assert read_log_lines(env)[-1]["out"] == "unknown-tool"


# --- the directory pin ---


DIRECTORY_INPUT_REFUSAL_TEXT = (
    "The Shell tool's directory input must be empty. To work in a directory inside a workspace root, start the command with "
    "`cd <absolute path> &&`, as its only leading `cd`, naming a directory whose path holds no `$`, `~` or backtick, holds no "
    "backslash unless single-quoted, and does not resolve outside the roots. If the call is refused again with the input "
    "empty, or the directory is outside every workspace root, stop and tell the engineer in chat."
)


class TestDirectoryPin:
    def test_a_multi_root_workspace_pins_the_first_root(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        first, second = (tmp_path / "one").resolve(), (tmp_path / "two").resolve()
        first.mkdir()
        second.mkdir()
        payload = shell_pre_payload(first, "pwd -P")
        payload["workspace_roots"] = [str(first), str(second)]
        command = _pin(env, payload, tmp_path)["updated_input"]["command"]
        assert command == _pin_prefix(env, first) + "pwd -P"

    def test_no_workspace_root_pins_home(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        payload = shell_pre_payload(tmp_path, "pwd -P")
        payload["workspace_roots"] = []
        command = _pin(env, payload, tmp_path)["updated_input"]["command"]
        assert command.startswith(_pin_prefix(env, env.home.resolve()))

    def test_a_non_absolute_first_root_takes_the_resolution_deny(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        payload = shell_pre_payload(tmp_path, "pwd -P")
        payload["workspace_roots"] = ["relative/root"]
        output = _pin(env, payload, tmp_path)
        assert output["permission"] == "deny"
        assert "no-absolute-dir" in output["agent_message"]
        assert "The engineer opens the folder as a workspace root." in output["agent_message"]
        assert "HOME is unset or not absolute" in output["agent_message"]
        _assert_deny_text_rules(output)

    def test_an_informational_pin_prints_empty_output_and_logs_the_cause(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        payload = shell_pre_payload(tmp_path, "pwd -P")
        payload["workspace_roots"] = ["relative/root"]
        assert _pin(env, payload, tmp_path, fail_closed=False) == {}
        assert read_log_lines(env)[-1]["out"] == "no-absolute-dir"

    @pytest.mark.parametrize(
        "name",
        ["with space", "it's", "-leading-dash", "tab\there", "new\nline", "ctl\x01byte", "del\x7fbyte"],
        ids=["space", "single-quote", "dash", "tab", "newline", "control-byte", "delete-byte"],
    )
    def test_a_pinned_directory_is_quoted_so_the_prefixed_command_starts_there(self, tmp_path: Path, name: str) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / name).resolve()
        root.mkdir()
        command = _prefixed(env, tmp_path, root, "pwd -P")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=False, cwd=elsewhere)
        assert result.stdout.rstrip("\n") == str(root).rstrip("\n")
        assert result.returncode == 0
        payload = shell_pre_payload(root, command)
        assert _pin(env, payload, tmp_path) == {}
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "hook.sh", saved)
        run_shim(env, payload, hook_args("PreToolUse", script=script, matcher="Bash"), cwd=_scratch(tmp_path))
        assert json.loads(saved.read_text())["tool_input"]["command"] == "pwd -P"

    def _sources(self, tmp_path: Path) -> dict[str, tuple[Path, str, list[str]]]:
        """pin source -> (the first root, the agent's command, the roots)."""
        first = (tmp_path / "first").resolve()
        later = (tmp_path / "later").resolve()
        inside_first = first / "pkg"
        inside_later = later / "pkg"
        spaced = first / "it's a dir"
        for directory in (first, later, inside_first, inside_later, spaced):
            directory.mkdir(parents=True)
        return {
            "first-root": (first, "make", [str(first)]),
            "home-fallback": (tmp_path, "make", []),
            "exception-inside-first": (first, f"cd '{inside_first}' && make", [str(first), str(later)]),
            "exception-inside-later": (first, f"cd '{inside_later}' && make", [str(first), str(later)]),
            "exception-with-space-and-quote": (first, f'cd "{spaced}" && make', [str(first)]),
        }

    @pytest.mark.parametrize(
        "source",
        ["first-root", "home-fallback", "exception-inside-first", "exception-inside-later", "exception-with-space-and-quote"],
    )
    def test_the_round_trip_prefixes_once_checks_strips_and_survives_a_double_fire(self, tmp_path: Path, source: str) -> None:
        env = _walkless_env(tmp_path)
        root, original, roots = self._sources(tmp_path)[source]
        payload = shell_pre_payload(root, original)
        payload["workspace_roots"] = roots
        prefixed = _pin(env, payload, tmp_path)["updated_input"]["command"]
        assert prefixed.endswith("\n" + original)
        assert prefixed.count("builtin cd --") == 1
        check_payload = build_spec("shell_before_execution", root).cursor
        check_payload["command"] = prefixed
        check_payload["workspace_roots"] = roots
        allowed = shim_json(run_shim(env, check_payload, hook_args("PreToolUse", mode="pin-check"), cwd=_scratch(tmp_path)))
        assert allowed == {}
        payload["tool_input"]["command"] = prefixed
        assert _pin(env, payload, tmp_path) == {}
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "hook.sh", saved)
        run_shim(env, payload, hook_args("PreToolUse", script=script, matcher="Bash"), cwd=_scratch(tmp_path))
        assert json.loads(saved.read_text())["tool_input"]["command"] == original

    def test_a_lookalike_pin_prefix_naming_another_directory_is_agent_text_and_is_prefixed_again(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        other = (tmp_path / "other").resolve()
        root.mkdir()
        other.mkdir()
        lookalike = f"builtin cd -- '{other}' || exit 1\nmake"
        prefixed = _prefixed(env, tmp_path, root, lookalike)
        assert prefixed == _pin_prefix(env, root) + lookalike
        payload = shell_pre_payload(root, prefixed)
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "hook.sh", saved)
        run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        assert json.loads(saved.read_text())["tool_input"]["command"] == lookalike
        assert _pin(env, payload, tmp_path) == {}

    @pytest.mark.parametrize("event_spec", ["shell_pre", "shell_post", "shell_before_execution"])
    @pytest.mark.parametrize("carry_prefix", [True, False], ids=["prefixed", "unprefixed"])
    def test_each_shell_events_hooks_receive_the_stripped_command(
        self, tmp_path: Path, event_spec: str, carry_prefix: bool
    ) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        spec = build_spec(event_spec, root)
        command = _pin_prefix(env, root) + "echo hello" if carry_prefix else "echo hello"
        payload = spec.cursor
        if "command" in payload:
            payload["command"] = command
        else:
            payload["tool_input"]["command"] = command
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "hook.sh", saved)
        args = hook_args(spec.claude_twin["hook_event_name"], script=script, matcher="Bash")
        run_shim(env, payload, args, cwd=_scratch(tmp_path))
        assert json.loads(saved.read_text())["tool_input"]["command"] == "echo hello"

    def test_pin_check_denies_an_unprefixed_command_a_lookalike_and_a_foreign_rewrite_and_allows_the_expected_prefix(
        self, tmp_path: Path
    ) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        other = (tmp_path / "other").resolve()
        other.mkdir()
        expected = _pin_prefix(env, root) + "make"
        assert _pin_check(env, expected, root, tmp_path) == {}
        for denied in ("make", _pin_prefix(env, other) + "make", "rewritten by a foreign entry"):
            output = _pin_check(env, denied, root, tmp_path)
            assert output["permission"] == "deny", denied
            assert "pin-check" in output["agent_message"]
            _assert_deny_text_rules(output)
        assert read_log_lines(env)[-1]["out"] == "pin-check"

    def test_the_pin_output_has_updated_input_and_no_permission_key_and_keeps_every_other_input_key(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        payload = shell_pre_payload(root, "make", timeout=30, description="build", run_in_background=False)
        output = _pin(env, payload, tmp_path)
        assert set(output) == {"updated_input"}
        assert set(output["updated_input"]) == set(payload["tool_input"])
        assert {k: v for k, v in output["updated_input"].items() if k != "command"} == {
            k: v for k, v in payload["tool_input"].items() if k != "command"
        }

    @pytest.mark.parametrize(
        "payload_cwd", [None, "", "relative/dir", "/some/absolute/dir"], ids=["absent", "empty", "relative", "absolute"]
    )
    def test_the_payload_cwd_never_changes_the_directory_a_hook_starts_in(self, tmp_path: Path, payload_cwd: str | None) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        payload = shell_pre_payload(root, "make")
        if payload_cwd is None:
            payload.pop("cwd", None)
        else:
            payload["cwd"] = payload_cwd
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "hook.sh", saved)
        run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        assert json.loads(saved.read_text())["cwd"] == str(root)

    @pytest.mark.parametrize("accepted", [None, "null", "", "pinned"], ids=["absent", "null", "empty", "absolute-equal-to-pin"])
    def test_an_absent_null_empty_or_pinned_directory_input_is_accepted_and_left_unchanged(
        self, tmp_path: Path, accepted: str | None
    ) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        payload = shell_pre_payload(root, "make")
        payload["tool_input"].pop("cwd", None)
        if accepted == "null":
            payload["tool_input"]["cwd"] = None
        elif accepted == "":
            payload["tool_input"]["cwd"] = ""
        elif accepted == "pinned":
            payload["tool_input"]["cwd"] = str(root)
        output = _pin(env, payload, tmp_path)
        assert "permission" not in output
        assert output["updated_input"].get("cwd") == payload["tool_input"].get("cwd")

    REFUSED_INPUTS = {
        "inside-other-root": "OTHER_ROOT/pkg",
        "outside-every-root": "/tmp",
        "relative": "pkg",
        "nonexistent": "/nonexistent/deep/dir",
        "number": 7,
        "false": False,
        "true": True,
        "empty-array": [],
        "object": {},
        "whitespace-only": "   ",
        "newline-and-escape": "line\nbreak\x1b[31m$(touch pwned)`id`",
        "64-KiB": "a" * (64 * 1024),
        "first-root-under-a-later-cd": "FIRST_ROOT",
    }

    @pytest.mark.parametrize("refused", list(REFUSED_INPUTS))
    def test_any_other_directory_input_takes_the_refusal_with_one_fixed_text(self, tmp_path: Path, refused: str) -> None:
        env = _walkless_env(tmp_path)
        first = (tmp_path / "first").resolve()
        other = (tmp_path / "other").resolve()
        (other / "pkg").mkdir(parents=True)
        first.mkdir()
        value = self.REFUSED_INPUTS[refused]
        if isinstance(value, str):
            value = value.replace("OTHER_ROOT", str(other)).replace("FIRST_ROOT", str(first))
        payload = shell_pre_payload(first, "make")
        payload["workspace_roots"] = [str(first), str(other)]
        payload["tool_input"]["cwd"] = value
        if refused == "first-root-under-a-later-cd":
            payload["tool_input"]["command"] = f"cd '{other}' && make"
        output = _pin(env, payload, tmp_path)
        assert output["permission"] == "deny"
        assert "updated_input" not in output
        assert DIRECTORY_INPUT_REFUSAL_TEXT in output["agent_message"]
        assert output["agent_message"] == output["user_message"]
        _assert_deny_text_rules(output)
        # No part of the value is echoed.
        if isinstance(value, str) and len(value) > 3:
            assert value not in output["agent_message"]

    def test_the_refusal_text_is_byte_identical_for_every_refused_value(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        first = (tmp_path / "first").resolve()
        first.mkdir()
        texts = set()
        for value in ("relative", 7, False, [], {}, "   ", "a" * 65536, "x\ny\x1b$(id)"):
            payload = shell_pre_payload(first, "make")
            payload["tool_input"]["cwd"] = value
            texts.add(_pin(env, payload, tmp_path)["agent_message"])
        assert len(texts) == 1

    def test_under_a_leading_cd_into_a_later_root_the_exception_directory_is_the_pin_and_the_first_root_is_refused(
        self, tmp_path: Path
    ) -> None:
        env = _walkless_env(tmp_path)
        first = (tmp_path / "first").resolve()
        later = (tmp_path / "later").resolve()
        first.mkdir()
        later.mkdir()
        payload = shell_pre_payload(first, f"cd '{later}' && make")
        payload["workspace_roots"] = [str(first), str(later)]
        payload["tool_input"]["cwd"] = str(later)
        assert "updated_input" in _pin(env, payload, tmp_path)
        payload["tool_input"]["cwd"] = str(first)
        assert _pin(env, payload, tmp_path)["permission"] == "deny"

    def _leading_cd_payload(self, tmp_path: Path, target: str, roots: list[Path]) -> dict:
        payload = shell_pre_payload(roots[0], f"cd '{target}' && make")
        payload["workspace_roots"] = [str(root) for root in roots]
        return payload

    @pytest.mark.parametrize("shape", ["symlink-to-outside", "dotdot-to-sibling"])
    def test_a_leading_cd_that_escapes_every_root_is_never_the_pin_and_its_directory_input_is_refused(
        self, tmp_path: Path, shape: str
    ) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        elsewhere = (tmp_path / "elsewhere").resolve()
        root.mkdir()
        elsewhere.mkdir()
        if shape == "symlink-to-outside":
            (root / "link").symlink_to(elsewhere)
            target = str(root / "link")
        else:
            target = f"{root}/../elsewhere"
        payload = self._leading_cd_payload(tmp_path, target, [root])
        # The pin falls back to the first root, so a hook runs there and not in the escaped directory.
        run_dir = tmp_path / "hook-run"
        run_dir.mkdir()
        where = run_dir / "where"
        script = write_hook_script(run_dir / "hook.sh", f"pwd -P > '{where}'\n")
        run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        assert where.read_text().strip() == str(root)
        # A directory input that names the escaped directory is refused by name, and no hook runs in pin mode.
        payload["tool_input"]["cwd"] = str(elsewhere)
        output, _ = _run_probe(tmp_path, payload, env=env, mode="pin")
        assert output["permission"] == "deny"
        assert "dir-input-refused" in output["agent_message"]
        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "dir-input-refused"

    def _directory_bash_enters(self, leading_cd: str, cwd: Path) -> str:
        """Where the recorded bash is after `leading_cd`; empty when the `cd` fails or the line is a syntax error."""
        # The startup-file variables are dropped so the caller's shell setup cannot change where `cd` lands or hang the run.
        scrubbed_env = {name: value for name, value in os.environ.items() if name not in ("BASH_ENV", "ENV")}
        result = subprocess.run(
            [CANDIDATE_SHELLS["bash"], "-c", f"{leading_cd} && pwd -P"],
            capture_output=True,
            check=False,
            cwd=cwd,
            env=scrubbed_env,
            stdin=subprocess.DEVNULL,
            timeout=SHELL_PROBE_TIMEOUT_SECONDS,
        )
        # Bytes, because text mode would turn a carriage return in the directory name into a newline.
        return result.stdout.decode().removesuffix("\n")

    def test_a_double_quoted_leading_cd_path_holding_a_backslash_takes_no_exception_and_a_plain_one_does(
        self, tmp_path: Path
    ) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        inside = root / "inside"
        # A shell removes a backslash-newline pair inside double quotes, so it reads this path as `inside/../..`.
        # The shim reads it as `inside` through a directory literally named `.\<newline>.`.
        (inside / ".\\\n.").mkdir(parents=True)
        backslash_leading_cd = f'cd "{inside}/.\\\n./.."'
        denied = _prefixed(env, tmp_path, root, f"{backslash_leading_cd} && make")
        assert denied.startswith(_pin_prefix(env, root))
        assert self._directory_bash_enters(backslash_leading_cd, root) not in ("", str(inside))
        # A shell reads `\"` as a literal quote and keeps the word going, while the shim's quoted group ends at the quote.
        # The shim reads the path as `inside/d\`, a directory that exists here.
        (inside / "d\\").mkdir()
        quote_leading_cd = f'cd "{inside}/d\\"'
        denied_at_quote = _prefixed(env, tmp_path, root, f"{quote_leading_cd} && make")
        assert denied_at_quote.startswith(_pin_prefix(env, root))
        assert self._directory_bash_enters(quote_leading_cd, root) != str(inside / "d\\")
        plain_leading_cd = f'cd "{inside}"'
        accepted = _prefixed(env, tmp_path, root, f"{plain_leading_cd} && make")
        assert accepted.startswith(_pin_prefix(env, inside))
        assert self._directory_bash_enters(plain_leading_cd, root) == str(inside)

    def test_a_single_quoted_leading_cd_path_holding_a_backslash_takes_the_exception(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        backslash_directory = root / "inside" / "d\\"
        backslash_directory.mkdir(parents=True)
        # A shell reads a single-quoted backslash literally, so the shim's path and the shell's path are the same.
        leading_cd = f"cd '{backslash_directory}'"
        pinned = _prefixed(env, tmp_path, root, f"{leading_cd} && make")
        assert pinned.startswith(_pin_prefix(env, backslash_directory))
        assert self._directory_bash_enters(leading_cd, root) == str(backslash_directory)

    def test_an_unquoted_leading_cd_path_holding_a_backslash_takes_no_exception(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        backslash_directory = root / "inside" / "d\\"
        backslash_directory.mkdir(parents=True)
        # A shell reads an unquoted `\ ` as an escaped space, so it reads this path as `inside/d `, not `inside/d\`.
        leading_cd = f"cd {backslash_directory}"
        denied = _prefixed(env, tmp_path, root, f"{leading_cd} && make")
        assert denied.startswith(_pin_prefix(env, root))
        assert self._directory_bash_enters(leading_cd, root) != str(backslash_directory)

    @pytest.mark.parametrize("blank", ["\r", "\x0b", "\x0c"], ids=["carriage-return", "vertical-tab", "form-feed"])
    def test_a_carriage_return_vertical_tab_or_form_feed_around_the_leading_cd_path_is_not_a_blank_and_takes_no_exception(
        self, tmp_path: Path, blank: str
    ) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        inside = root / "inside"
        inside.mkdir(parents=True)
        # A shell keeps the character in the path word, so `inside<blank>` names a directory that does not exist.
        for command in (f"cd {inside}{blank}&& make", f"cd{blank}{inside} && make"):
            assert _prefixed(env, tmp_path, root, command).startswith(_pin_prefix(env, root))

    @pytest.mark.parametrize("blank", [" ", "\t"], ids=["space", "tab"])
    def test_a_space_or_tab_around_the_leading_cd_path_takes_the_exception(self, tmp_path: Path, blank: str) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        inside = root / "inside"
        inside.mkdir(parents=True)
        pinned = _prefixed(env, tmp_path, root, f"cd{blank}{inside}{blank}&& make")
        assert pinned.startswith(_pin_prefix(env, inside))

    @pytest.mark.parametrize("blank", ["\r", "\x0b", "\x0c"], ids=["carriage-return", "vertical-tab", "form-feed"])
    def test_an_unquoted_leading_cd_path_ending_in_such_a_character_takes_the_exception_for_the_directory_the_shell_enters(
        self, tmp_path: Path, blank: str
    ) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "root").resolve()
        sibling = root / f"inside{blank}"
        sibling.mkdir(parents=True)
        (root / "inside").mkdir()
        leading_cd = f"cd {sibling}"
        pinned = _prefixed(env, tmp_path, root, f"{leading_cd}&& make")
        assert pinned.startswith(_pin_prefix(env, sibling))
        assert self._directory_bash_enters(leading_cd, root) == str(sibling)

    @pytest.mark.parametrize("name", ["déjà", "ファイル"], ids=["accented", "katakana"])
    def test_a_leading_cd_into_a_root_or_a_directory_inside_one_with_a_non_ascii_name_takes_the_exception(
        self, tmp_path: Path, name: str
    ) -> None:
        env = _walkless_env(tmp_path)
        first = (tmp_path / "first").resolve()
        non_ascii_root = (tmp_path / name).resolve()
        inside = first / name
        for directory in (first, non_ascii_root, inside):
            directory.mkdir(parents=True)
        for target, roots in ((non_ascii_root, [first, non_ascii_root]), (inside, [first])):
            payload = shell_pre_payload(first, f"cd '{target}' && make")
            payload["workspace_roots"] = [str(root) for root in roots]
            assert _pin(env, payload, tmp_path)["updated_input"]["command"].startswith(_pin_prefix(env, target))

    @pytest.mark.parametrize(
        ("root", "target", "contained"),
        [
            ("/", "/", True),
            ("/", "/any/deep/path", True),
            ("/a", "/a", True),
            ("/a", "/a/b", True),
            ("/a", "/ab", False),
            ("/a", "/", False),
            ("/a/b", "/a", False),
        ],
        ids=lambda value: str(value),
    )
    def test_the_containment_test_holds_a_path_by_component_and_slash_holds_every_absolute_path(
        self, root: str, target: str, contained: bool
    ) -> None:
        assert SHIM_MODULE.path_within(root, target) is contained

    def _pin_directory(self, roots: list[str], command: str) -> str:
        return SHIM_MODULE.Roots(roots, "").shell_pin_directory(command)

    def _file_tool_directory(self, roots: list[str], target: str) -> str:
        return SHIM_MODULE.Roots(roots, "").file_tool_directory(target)

    def test_under_a_slash_root_a_plain_existing_leading_cd_directory_is_the_pin(self, tmp_path: Path) -> None:
        first = (tmp_path / "first").resolve()
        elsewhere = (tmp_path / "elsewhere").resolve()
        first.mkdir()
        elsewhere.mkdir()
        assert self._pin_directory([str(first), "/"], f"cd '{elsewhere}' && make") == str(elsewhere)

    @pytest.mark.parametrize("shape", ["dollar", "tilde", "backtick", "relative", "missing"])
    def test_under_a_slash_root_a_leading_cd_target_the_exception_refuses_still_falls_back_to_the_first_root(
        self, tmp_path: Path, shape: str
    ) -> None:
        first = (tmp_path / "first").resolve()
        first.mkdir()
        existing_names = {"dollar": "a$b", "tilde": "a~b", "backtick": "a`b"}
        if shape in existing_names:
            (tmp_path / existing_names[shape]).mkdir()
            target = str(tmp_path.resolve() / existing_names[shape])
        elif shape == "relative":
            target = "first"
        else:
            target = str(tmp_path.resolve() / "missing")
        assert self._pin_directory([str(first), "/"], f"cd '{target}' && make") == str(first)

    def test_under_a_slash_root_a_symlinked_leading_cd_target_is_pinned_as_its_real_directory(self, tmp_path: Path) -> None:
        first = (tmp_path / "first").resolve()
        real = (tmp_path / "real").resolve()
        first.mkdir()
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        assert self._pin_directory([str(first), "/"], f"cd '{tmp_path.resolve() / 'link'}' && make") == str(real)

    def test_with_a_slash_root_after_the_first_a_file_tool_target_outside_the_first_root_starts_its_hook_in_slash(
        self, tmp_path: Path
    ) -> None:
        first = (tmp_path / "first").resolve()
        (first / "src").mkdir(parents=True)
        roots = [str(first), "/"]
        assert self._file_tool_directory(roots, str(first / "src" / "a.txt")) == str(first)
        assert self._file_tool_directory(roots, str(tmp_path.resolve() / "outside" / "a.txt")) == "/"

    @pytest.mark.parametrize(
        ("tool", "target_key", "extra"),
        [
            ("Read", "file_path", {}),
            ("Write", "file_path", {"content": "x"}),
            ("Delete", "file_path", {}),
            ("Grep", "file_path", {}),
            ("Grep", "path", {}),
        ],
        ids=["read", "write", "delete", "grep-file-path", "grep-path"],
    )
    def test_a_file_tool_target_in_the_second_root_starts_the_hook_in_that_root(
        self, tmp_path: Path, tool: str, target_key: str, extra: dict
    ) -> None:
        first = _workspace(tmp_path)
        second = (tmp_path / "second").resolve()
        (second / "src").mkdir(parents=True)
        payload = file_tool_payload(first, tool, str(second / "src" / "target.txt"), target_key=target_key, **extra)
        payload["workspace_roots"] = [str(first), str(second)]
        script = write_hook_script(tmp_path / "hook.sh", f"pwd -P > '{tmp_path / 'where'}'\n")
        run_shim(_walkless_env(tmp_path), payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        assert (tmp_path / "where").read_text().strip() == str(second)

    @pytest.mark.parametrize("spelling", ["//", "/.", "/..", "/../.."])
    def test_each_spelling_of_slash_is_the_same_root_and_accepts_the_same_leading_cd_directories(
        self, tmp_path: Path, spelling: str
    ) -> None:
        elsewhere = (tmp_path / "elsewhere").resolve()
        elsewhere.mkdir()
        assert SHIM_MODULE.Roots([spelling], "").canonical == ["/"]
        assert self._pin_directory([spelling], f"cd '{elsewhere}' && make") == str(elsewhere)

    def test_a_trailing_slash_on_a_root_names_the_same_directory_and_does_not_widen_the_exception(self, tmp_path: Path) -> None:
        repo = (tmp_path / "repo").resolve()
        elsewhere = (tmp_path / "elsewhere").resolve()
        repo.mkdir()
        elsewhere.mkdir()
        assert SHIM_MODULE.Roots([f"{repo}/"], "").canonical == [str(repo)]
        assert self._pin_directory([f"{repo}/"], f"cd '{elsewhere}' && make") == str(repo)

    def test_recognition_tries_at_most_the_newline_cap_of_candidate_pin_lines_however_many_lines_the_command_has(self) -> None:
        class CountingRoots:
            candidate_pin_lines_examined = 0

            def shell_pin_directory(self, original):
                self.candidate_pin_lines_examined += 1

        pin_shaped_line = SHIM_MODULE.PIN_LINE_HEAD + "x" + SHIM_MODULE.PIN_LINE_TAIL
        command = "\n".join([pin_shaped_line] * 1_000) + "\nmake"
        assert command.count("\n") > SHIM_MODULE.PIN_MAX_NEWLINES
        roots = CountingRoots()
        assert SHIM_MODULE.recognize_pin(command, roots) == (command, False)
        assert roots.candidate_pin_lines_examined == SHIM_MODULE.PIN_MAX_NEWLINES

    @pytest.mark.parametrize(
        ("newlines_in_name", "recognized"),
        [(SHIM_MODULE.PIN_MAX_NEWLINES - 1, True), (SHIM_MODULE.PIN_MAX_NEWLINES, False)],
        ids=["terminating-newline-is-the-cap-th", "terminating-newline-is-past-the-cap"],
    )
    def test_a_valid_pin_line_is_recognized_only_when_its_terminating_newline_is_within_the_newline_cap(
        self, tmp_path: Path, newlines_in_name: int, recognized: bool
    ) -> None:
        directory = (tmp_path / ("a" + "\n" * newlines_in_name + "b")).resolve()
        directory.mkdir()
        roots = SHIM_MODULE.Roots([str(directory)], "")
        command = SHIM_MODULE.pin_line(str(directory)) + "\nmake"
        assert SHIM_MODULE.recognize_pin(command, roots) == (("make", True) if recognized else (command, False))

    def test_a_pin_line_whose_directory_name_holds_a_few_newlines_is_still_recognized(self, tmp_path: Path) -> None:
        directory = (tmp_path / "one\ntwo\nthree\nfour").resolve()
        directory.mkdir()
        roots = SHIM_MODULE.Roots([str(directory)], "")
        command = SHIM_MODULE.pin_line(str(directory)) + "\nmake"
        assert SHIM_MODULE.recognize_pin(command, roots) == ("make", True)

    @pytest.mark.timing
    def test_a_200_kb_single_line_command_is_classified_within_the_payload_size_smoke_bound(self, tmp_path: Path) -> None:
        roots = SHIM_MODULE.Roots([str(tmp_path)], "")
        started = time.monotonic()
        assert SHIM_MODULE.recognize_pin("echo " + "a" * 200_000, roots)[1] is False
        assert time.monotonic() - started < 2.0

    @pytest.mark.timing
    def test_a_pin_prefixed_200_kb_command_is_recognized_within_the_payload_size_smoke_bound(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        original = "echo " + "a" * 200_000
        started = time.monotonic()
        prefixed = _prefixed(env, tmp_path, root, original)
        payload = shell_pre_payload(root, prefixed)
        assert _pin(env, payload, tmp_path) == {}
        assert time.monotonic() - started < 10.0
