"""hook-shim.py: the pin prefix's environment line and its host-PID part, and the prefix under each candidate shell."""

from __future__ import annotations

import json
import os
import shutil
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
    _pin,
    _pin_check,
    _pin_prefix,
    _prefixed,
    _run_fault_program,
    _scratch,
    _walkless_env,
    _workspace,
)
from .conftest import require_zsh

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_identity_fixtures import (  # noqa: E402 (the import needs the sys.path entry above)
    HOST_PROCESS_ID,
    HOST_SHOWING_PS_BODY,
    PID_GRAMMAR_EDGE_TABLE,
    REJECTED_PID_GRAMMAR_ROWS,
    write_stub_tool_directory,
)
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    CHILD_CONVERSATION_ID,
    CONVERSATION_ID,
    LIB_SH,
    PARENT_CONVERSATION_ID,
    ShimEnv,
    build_spec,
    hook_args,
    make_shim_env,
    read_log_lines,
    recorded_tool_paths,
    recording_probe_script,
    run_shim,
    shell_pre_payload,
    shim_json,
)


def _has_builtin(path: str) -> bool | None:
    """A runtime probe, never the platform: whether `builtin cd .` exits 0 under this shell; None when the probe timed out."""
    try:
        completed = subprocess.run(
            [path, "-c", "builtin cd ."], capture_output=True, check=False, timeout=SHELL_PROBE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return None
    return completed.returncode == 0


SHELL_PROBES = {name: _has_builtin(path) for name, path in CANDIDATE_SHELLS.items()}
BUILTIN_SHELL_PARAMS = [pytest.param(name, id=name) for name, ok in SHELL_PROBES.items() if ok is True]
NO_BUILTIN_SHELL_PARAMS = [pytest.param(name, id=name) for name, ok in SHELL_PROBES.items() if ok is False]
BEHAVIOUR_ROW_IDS = ["pin-failure-runs-nothing", "trailing-backslash", "extglob-line", "cd-shadowed-by-function", "chpwd-hook"]
# A row needs a shell that has the construct it tests: a `cd` function and `export -f` are bash and POSIX-mode sh,
# and a `chpwd` hook is zsh's.
ZSH_ONLY_ROWS = {"chpwd-hook"}
_ROW_SHELLS = {
    "pin-failure-runs-nothing": [name for name, ok in SHELL_PROBES.items() if ok is True],
    "trailing-backslash": [name for name, ok in SHELL_PROBES.items() if ok is True],
    "extglob-line": [name for name, ok in SHELL_PROBES.items() if ok is True],
    "cd-shadowed-by-function": [name for name, ok in SHELL_PROBES.items() if ok is True and name in ("bash", "sh")],
    "chpwd-hook": [name for name, ok in SHELL_PROBES.items() if ok is True and name == "zsh"],
}
BEHAVIOUR_PARAMS_BY_ROW_ORDER = [(row, shell) for row in BEHAVIOUR_ROW_IDS for shell in _ROW_SHELLS[row]]
BEHAVIOUR_PARAMS = [pytest.param(row, shell, id=f"{row}-{shell}") for row, shell in BEHAVIOUR_PARAMS_BY_ROW_ORDER]


def _stand_in_bash_env(tmp_path: Path, body: str) -> ShimEnv:
    """An install record whose bash is a stand-in running `body`, the program `pin` mode's host walk starts."""
    stand_in = write_stub_tool_directory(tmp_path, "walk-bin", "bash", body) / "bash"
    return make_shim_env(tmp_path, tool_paths={**recorded_tool_paths(), "bash": str(stand_in)})


class TestPinEnvironmentLineAndShells:
    # --- the environment line and its host-PID part ---

    def _walk_calls(self, tmp_path: Path) -> list[str]:
        calls = tmp_path / "walk-calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def _recording_walk_env(self, tmp_path: Path, printed: bytes = b"4242\n") -> ShimEnv:
        """A record whose bash is a stand-in that logs its start, its arguments and its directory, then prints `printed`."""
        printed_file = tmp_path / "walk-printed"
        printed_file.write_bytes(printed)
        body = (
            f"echo started >> '{tmp_path / 'walk-calls'}'\n"
            f"printf '%s\\n' \"$@\" > '{tmp_path / 'walk-arguments'}'\n"
            f"pwd -P > '{tmp_path / 'walk-directory'}'\n"
            f"exec cat '{printed_file}'\n"
        )
        return _stand_in_bash_env(tmp_path, body)

    def test_pin_mode_exports_the_pid_its_host_walk_prints(self, tmp_path: Path) -> None:
        env = self._recording_walk_env(tmp_path)
        root = _workspace(tmp_path)
        command = _prefixed(env, tmp_path, root, "make")
        assert command == (
            f"unset CLAUDECODE; export CLAUDE_CONFIG_DIR='{env.config_dir}' "
            f"CLAUDE_CONFIG_ADAPTER_SESSION_ID='{CONVERSATION_ID}' CLAUDE_CONFIG_ADAPTER_HOST_PID=4242\n"
            f"builtin cd -- '{root}' || exit 1\nmake"
        )
        walk_arguments = (tmp_path / "walk-arguments").read_text().splitlines()
        assert walk_arguments == ["-c", '. "$1" && _lib_cursor_host_pid', "walk", str(LIB_SH)]
        assert (tmp_path / "walk-directory").read_text().strip() == str(root)

    def test_a_pin_prefix_of_a_subagent_call_carries_its_parents_session_id(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        payload = build_spec("shell_pre_child", root).cursor
        payload["tool_input"] = {"command": "make"}
        command = _pin(env, payload, tmp_path)["updated_input"]["command"]
        assert command == _pin_prefix(env, root, session_id=PARENT_CONVERSATION_ID) + "make"
        assert CHILD_CONVERSATION_ID not in command

    @pytest.mark.parametrize("row", [row for row in PID_GRAMMAR_EDGE_TABLE if row.accepted], ids=lambda row: row.row_id)
    def test_the_walks_output_is_exported_when_the_pid_grammar_accepts_it(self, tmp_path: Path, row) -> None:
        env = self._recording_walk_env(tmp_path, (row.value + "\n").encode())
        root = _workspace(tmp_path)
        assert _prefixed(env, tmp_path, root, "make") == _pin_prefix(env, root, row.value) + "make"
        assert len(self._walk_calls(tmp_path)) == 1

    @pytest.mark.parametrize("row", REJECTED_PID_GRAMMAR_ROWS, ids=lambda row: row.row_id)
    def test_a_walk_output_the_pid_grammar_rejects_is_never_exported_and_still_pins(self, tmp_path: Path, row) -> None:
        env = self._recording_walk_env(tmp_path, (row.value + "\n").encode())
        root = _workspace(tmp_path)
        assert _prefixed(env, tmp_path, root, "make") == _pin_prefix(env, root) + "make"
        assert len(self._walk_calls(tmp_path)) == 1

    def _walk_failure_env(self, tmp_path: Path, failure: str) -> ShimEnv:
        if failure == "no-bash":
            return _walkless_env(tmp_path)
        if failure == "start-error":
            tool_paths = recorded_tool_paths()
            tool_paths.pop("gtimeout", None)
            tool_paths["timeout"] = str(tmp_path / "no-such-wrapper")
            return make_shim_env(tmp_path, tool_paths=tool_paths)
        body = {
            "nonzero-status": "exit 3\n",
            "hook-output-cap": f"head -c {SHIM_MODULE.HOOK_OUTPUT_CAP_BYTES * 2} /dev/zero\n",
            "signal": "kill -TERM $$\n",
        }[failure]
        return _stand_in_bash_env(tmp_path, body)

    @pytest.mark.parametrize("failure", ["no-bash", "start-error", "nonzero-status", "hook-output-cap", "signal"])
    def test_each_walk_failure_unsets_the_variable_exports_none_and_still_pins(self, tmp_path: Path, failure: str) -> None:
        env = self._walk_failure_env(tmp_path, failure)
        root = _workspace(tmp_path)
        command = _prefixed(env, tmp_path, root, "make")
        assert command == _pin_prefix(env, root) + "make"
        assert "CLAUDE_CONFIG_ADAPTER_HOST_PID=" not in command
        assert read_log_lines(env)[-1]["out"] == "pin-no-host"

    def test_a_walk_whose_time_is_already_spent_unsets_the_variable(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        record = {"tool_paths": recorded_tool_paths(), "routed_config_dir": str(tmp_path)}
        assert SHIM_MODULE.host_walk_pid(record, str(root), SHIM_MODULE.time.monotonic()) is None

    @pytest.mark.parametrize(
        ("kind", "fault_class"),
        [("oserror", "OSError"), ("subprocess-error", "SubprocessError")],
        ids=["oserror", "subprocess-error"],
    )
    def test_a_walk_run_that_raises_an_exception_that_is_no_refusal_is_a_shim_fault_and_no_pin_outcome(
        self, tmp_path: Path, kind: str, fault_class: str
    ) -> None:
        root = _workspace(tmp_path)
        result, env = _run_fault_program(
            tmp_path,
            {"function": "run_wrapped_hook", "raise": kind},
            shell_pre_payload(root, "make"),
            hook_args("PreToolUse", mode="pin"),
        )
        assert (result.returncode, result.stdout, result.stderr) == (1, b"", b"")
        assert [(line["out"], line["fault"]) for line in read_log_lines(env)] == [("shim-fault", fault_class)]

    def test_a_walk_run_that_raises_a_refusal_unsets_the_variable_and_still_pins(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        result, env = _run_fault_program(
            tmp_path,
            {"function": "run_wrapped_hook", "raise": "refusal"},
            shell_pre_payload(root, "make"),
            hook_args("PreToolUse", mode="pin"),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["updated_input"]["command"] == _pin_prefix(env, root) + "make"
        assert read_log_lines(env)[-1]["out"] == "pin-no-host"

    def test_a_second_fire_over_a_prefixed_command_runs_no_walk(self, tmp_path: Path) -> None:
        env = self._recording_walk_env(tmp_path)
        root = _workspace(tmp_path)
        prefixed = _prefixed(env, tmp_path, root, "make")
        assert len(self._walk_calls(tmp_path)) == 1
        assert _pin(env, shell_pre_payload(root, prefixed), tmp_path) == {}
        assert len(self._walk_calls(tmp_path)) == 1
        assert read_log_lines(env)[-1]["out"] == "pin-unchanged"

    @staticmethod
    def _ps_stub_env(tmp_path: Path, ps_body: str) -> ShimEnv:
        """A record that puts a `ps` stub first on the PATH the shim fixes, so the real walk reads the stub's answers."""
        stub = write_stub_tool_directory(tmp_path, "walk-ps", "ps", ps_body) / "ps"
        return make_shim_env(tmp_path, tool_paths={"ps": str(stub), **recorded_tool_paths()})

    def test_the_real_walk_under_a_ps_stub_that_shows_a_cursor_host_exports_that_hosts_pid(self, tmp_path: Path) -> None:
        env = self._ps_stub_env(tmp_path, HOST_SHOWING_PS_BODY)
        root = _workspace(tmp_path)
        assert _prefixed(env, tmp_path, root, "make") == _pin_prefix(env, root, HOST_PROCESS_ID) + "make"
        assert read_log_lines(env)[-1]["out"] == "pin"

    @pytest.mark.timing
    def test_a_ps_that_stalls_past_the_walks_time_still_pins_with_the_unset_form_and_returns_before_the_deadline(
        self, tmp_path: Path
    ) -> None:
        deadline = 6
        started_file = tmp_path / "ps-started"
        env = self._ps_stub_env(tmp_path, f"echo started >> '{started_file}'\nexec sleep 30\n")
        root = _workspace(tmp_path)
        walk_time_left = deadline - SHIM_MODULE.GRACE_PERIOD_SECONDS - SHIM_MODULE.DRAIN_BOUND_SECONDS
        began = time.monotonic()
        result = run_shim(
            env,
            shell_pre_payload(root, "make"),
            hook_args("PreToolUse", mode="pin", deadline=deadline),
            cwd=_scratch(tmp_path),
        )
        elapsed = time.monotonic() - began
        assert result.returncode == 0, result.stderr
        assert started_file.exists(), "the walk never reached the ps stub"
        assert elapsed >= walk_time_left - 0.5
        assert elapsed < deadline
        assert shim_json(result)["updated_input"]["command"] == _pin_prefix(env, root) + "make"
        line = read_log_lines(env)[-1]
        assert line["out"] == "pin-no-host"
        assert int(line["ms"]) >= (walk_time_left - 0.5) * 1000

    def _stripped_command(self, tmp_path: Path, env: ShimEnv, root: Path, command: str) -> str:
        saved = tmp_path / "saved.json"
        saved.unlink(missing_ok=True)
        script = recording_probe_script(tmp_path / "hook.sh", saved)
        run_shim(env, shell_pre_payload(root, command), hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        return json.loads(saved.read_text())["tool_input"]["command"]

    @pytest.mark.parametrize("host_pid", ["4242", None], ids=["export-form", "unset-form"])
    def test_pin_check_passes_and_the_strip_removes_either_form(self, tmp_path: Path, host_pid: str | None) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        assert _pin_check(env, _pin_prefix(env, root, host_pid) + "make", root, tmp_path) == {}
        assert self._stripped_command(tmp_path, env, root, _pin_prefix(env, root, host_pid) + "make") == "make"

    @pytest.mark.parametrize(
        "row", [row for row in PID_GRAMMAR_EDGE_TABLE if row.applies_to_line_forms], ids=lambda row: row.row_id
    )
    def test_pin_check_and_the_strip_take_an_export_line_only_when_its_value_fits_the_grammar(self, tmp_path: Path, row) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        command = _pin_prefix(env, root, row.value) + "make"
        verdict = _pin_check(env, command, root, tmp_path)
        pinned = _pin(env, shell_pre_payload(root, command), tmp_path)
        stripped = self._stripped_command(tmp_path, env, root, command)
        if row.accepted:
            assert (verdict, pinned, stripped) == ({}, {}, "make")
        else:
            assert verdict["permission"] == "deny"
            assert pinned["updated_input"]["command"] == _pin_prefix(env, root) + command
            assert stripped == command

    def test_an_export_line_that_fits_the_grammar_under_a_pin_line_naming_another_directory_is_still_denied(
        self, tmp_path: Path
    ) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        other = (tmp_path / "other").resolve()
        other.mkdir()
        output = _pin_check(env, _pin_prefix(env, other, "4242") + "make", root, tmp_path)
        assert output["permission"] == "deny"
        assert "pin-check" in output["agent_message"]

    @pytest.mark.parametrize("which", ["session-id", "config-dir"])
    def test_an_environment_line_naming_another_session_id_or_config_dir_is_agent_text(self, tmp_path: Path, which: str) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        if which == "session-id":
            command = _pin_prefix(env, root, "4242", session_id="other-session") + "make"
        else:
            command = _pin_prefix(env, root, "4242").replace(str(env.config_dir), str(tmp_path / "elsewhere")) + "make"
        assert _pin_check(env, command, root, tmp_path)["permission"] == "deny"
        assert self._stripped_command(tmp_path, env, root, command) == command

    def test_the_strip_leaves_a_later_host_pid_export_in_the_agents_own_command_untouched(self, tmp_path: Path) -> None:
        env = _walkless_env(tmp_path)
        root = _workspace(tmp_path)
        agent_command = "export CLAUDE_CONFIG_ADAPTER_HOST_PID=7\nmake"
        assert self._stripped_command(tmp_path, env, root, _pin_prefix(env, root, "4242") + agent_command) == agent_command

    @pytest.mark.parametrize("shell", [pytest.param(name, id=name) for name, ok in SHELL_PROBES.items() if ok is True])
    @pytest.mark.parametrize("host_pid", ["4242", None], ids=["export-form", "unset-form"])
    def test_the_prefix_sets_the_routed_environment_and_the_host_variable_under_each_shell(
        self, tmp_path: Path, shell: str, host_pid: str | None
    ) -> None:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "pinned").resolve()
        root.mkdir()
        probe = (
            'printf "%s|%s|%s|%s" "${CLAUDECODE-unset}" "$CLAUDE_CONFIG_DIR" "$CLAUDE_CONFIG_ADAPTER_SESSION_ID" '
            '"${CLAUDE_CONFIG_ADAPTER_HOST_PID-unset}"'
        )
        inherited = {"CLAUDECODE": "1", "CLAUDE_CONFIG_ADAPTER_HOST_PID": "9999", "CLAUDE_CONFIG_DIR": "/inherited"}
        result = self._run_in(shell, _pin_prefix(env, root, host_pid) + probe, tmp_path, inherited)
        assert result.returncode == 0, result.stderr
        assert result.stdout.decode() == f"unset|{env.config_dir}|{CONVERSATION_ID}|{host_pid or 'unset'}"

    def test_zsh_is_a_candidate_shell_here_or_the_rows_that_need_it_fail_in_ci(self) -> None:
        require_zsh()
        assert SHELL_PROBES.get("zsh") is True

    @pytest.mark.parametrize(
        ("github_actions", "expected"),
        [("true", pytest.fail.Exception), (None, pytest.skip.Exception)],
        ids=["in-ci-it-fails", "elsewhere-it-skips"],
    )
    def test_require_zsh_fails_in_ci_and_skips_elsewhere_when_zsh_is_absent(
        self, monkeypatch: pytest.MonkeyPatch, github_actions: str | None, expected: type
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: None)
        if github_actions is None:
            monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        else:
            monkeypatch.setenv("GITHUB_ACTIONS", github_actions)
        with pytest.raises(expected):
            require_zsh()

    def test_require_zsh_returns_none_when_zsh_is_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/zsh")
        assert require_zsh() is None

    # --- behaviour under each shell that has `builtin` ---

    def _pinned_command(self, tmp_path: Path, command: str) -> tuple[str, Path]:
        env = _walkless_env(tmp_path)
        root = (tmp_path / "pinned").resolve()
        root.mkdir(exist_ok=True)
        return _prefixed(env, tmp_path, root, command), root

    def _run_in(self, shell: str, command: str, cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [CANDIDATE_SHELLS[shell], "-c", command],
            capture_output=True,
            check=False,
            cwd=cwd,
            env={**os.environ, **(env_extra or {})},
            timeout=SHELL_PROBE_TIMEOUT_SECONDS,
        )

    def _same_as_unprefixed(self, tmp_path: Path, shell: str, command: str) -> None:
        prefixed, root = self._pinned_command(tmp_path, command)
        unprefixed = self._run_in(shell, command, root)
        pinned = self._run_in(shell, prefixed, tmp_path)
        assert (pinned.stdout, pinned.returncode) == (unprefixed.stdout, unprefixed.returncode)

    def _row_pin_failure_runs_nothing(self, tmp_path: Path, shell: str) -> None:
        command = "echo RAN || echo ORED; echo SEMI; { echo BRACED; }\ncat <<'EOF'\nHEREDOC RAN\nEOF\ntrue # trailing comment"
        prefixed, root = self._pinned_command(tmp_path, command)
        root.rmdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = self._run_in(shell, prefixed, elsewhere)
        assert result.stdout == b""
        assert result.returncode == 1

    def _row_trailing_backslash(self, tmp_path: Path, shell: str) -> None:
        self._same_as_unprefixed(tmp_path, shell, "echo hello \\")

    def _row_extglob_line(self, tmp_path: Path, shell: str) -> None:
        self._same_as_unprefixed(tmp_path, shell, "shopt -s extglob\necho @(a|b)")

    def _row_cd_shadowed_by_function(self, tmp_path: Path, shell: str) -> None:
        prefixed, root = self._pinned_command(tmp_path, "pwd -P")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        shadowed = "cd() { return 0; }\nexport -f cd 2>/dev/null\n" + prefixed
        assert self._run_in(shell, shadowed, elsewhere).stdout.decode().strip() == str(root)

    def _row_chpwd_hook(self, tmp_path: Path, shell: str) -> None:
        """zsh runs a chpwd function after the pinned cd and adds its output to the result, a residual of the pin."""
        prefixed, root = self._pinned_command(tmp_path, "pwd -P")
        zdotdir = tmp_path / "zdot"
        zdotdir.mkdir()
        (zdotdir / ".zshenv").write_text("chpwd() { print -r -- CHPWD-RAN; }\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = self._run_in(shell, prefixed, elsewhere, {"ZDOTDIR": str(zdotdir)})
        assert result.stdout.decode().splitlines()[-1] == str(root)
        assert "CHPWD-RAN" in result.stdout.decode()

    @pytest.mark.parametrize(("row", "shell"), BEHAVIOUR_PARAMS)
    def test_the_command_behaves_as_unprefixed_or_stays_pinned(self, tmp_path: Path, row: str, shell: str) -> None:
        getattr(self, f"_row_{row.replace('-', '_')}")(tmp_path, shell)

    @pytest.mark.skipif(not NO_BUILTIN_SHELL_PARAMS, reason="absent: no candidate shell lacks `builtin`")
    @pytest.mark.parametrize("shell", NO_BUILTIN_SHELL_PARAMS)
    def test_on_a_shell_with_no_builtin_the_pin_fails_closed_and_runs_none_of_the_command(
        self, tmp_path: Path, shell: str
    ) -> None:
        prefixed, root = self._pinned_command(tmp_path, "echo RAN")
        result = self._run_in(shell, prefixed, tmp_path)
        assert result.stdout == b""
        assert result.returncode == 1


def test_a_shell_probe_that_times_out_is_neither_a_builtin_shell_nor_a_no_builtin_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hanging_shell = tmp_path / "hanging-shell"
    hanging_shell.write_text("#!/bin/sh\nexec sleep 30\n")
    hanging_shell.chmod(0o755)
    monkeypatch.setattr(sys.modules[__name__], "SHELL_PROBE_TIMEOUT_SECONDS", 0.2)
    assert _has_builtin(str(hanging_shell)) is None


def test_the_recorded_bash_is_probed_as_having_builtin_and_runs_every_shell_independent_behaviour_row() -> None:
    """Under the repo's default `-n auto` a test cannot see sibling tests' outcomes, so the matrix is checked at collection.

    A probe failure on the recorded bash must fail here, not silently move it to the no-`builtin` row."""
    assert SHELL_PROBES["bash"] is True
    assert set(SHELL_PROBES) <= {"bash", "sh", "zsh", "dash"}
    rows_with_a_shell = [row for row in BEHAVIOUR_ROW_IDS if row not in ZSH_ONLY_ROWS or SHELL_PROBES.get("zsh") is True]
    assert list(dict.fromkeys(row for row, _ in BEHAVIOUR_PARAMS_BY_ROW_ORDER)) == rows_with_a_shell
    rows_for_bash = {row for row, shell in BEHAVIOUR_PARAMS_BY_ROW_ORDER if shell == "bash"}
    assert rows_for_bash == set(BEHAVIOUR_ROW_IDS) - ZSH_ONLY_ROWS
    assert BEHAVIOUR_PARAMS, "no behaviour row has a shell to run under"
