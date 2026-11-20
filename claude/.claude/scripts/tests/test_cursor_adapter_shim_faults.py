"""hook-shim.py: shim faults and self_check."""

from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import CLAUDE_DIR

from ._cursor_shim_helpers import (
    CANARY,
    SHIM_MODULE,
    SHIM_MODULE_EVENTS,
    _recorded_wrapper,
    _run_fault_program,
    _scratch,
    _workspace,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    copy_adapter,
    hook_args,
    load_shim,
    make_shim_env,
    read_log_lines,
    run_audited,
    shell_pre_payload,
    shim_argv,
    shim_process_env,
    write_hook_script,
)

# --- shim faults ---


FAULT_CLASSES = {
    "exception": "CanaryError",
    "system-exit": "SystemExit",
    "recursion": "RecursionError",
    "memory": "MemoryError",
}


def _canary_payload(root: Path) -> dict:
    payload = shell_pre_payload(root, f"echo {CANARY}", description=CANARY)
    payload["user_email"] = CANARY
    payload["model"] = CANARY
    return payload


class TestShimFaults:
    @pytest.mark.parametrize("function", ["map_hook_output", "build_claude_payload"])
    @pytest.mark.parametrize("kind", list(FAULT_CLASSES))
    def test_a_fault_ends_the_run_with_exit_one_no_output_and_one_shim_fault_log_line(
        self, tmp_path: Path, function: str, kind: str
    ) -> None:
        root = _workspace(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", "cat > /dev/null\n")
        args = hook_args("PreToolUse", script=script, matcher="Bash")
        result, env = _run_fault_program(tmp_path, {"function": function, "raise": kind}, _canary_payload(root), args)
        assert result.returncode == 1
        assert (result.stdout, result.stderr) == (b"", b"")
        log_text = env.log_path.read_text()
        assert CANARY not in log_text
        lines = read_log_lines(env)
        assert len(lines) == 1
        assert lines[0]["out"] == "shim-fault"
        assert lines[0]["fault"] == FAULT_CLASSES[kind]
        assert lines[0]["py"] == f"{sys.version_info.major}.{sys.version_info.minor}"

    def test_a_short_write_to_the_output_descriptor_still_writes_the_whole_document(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read_end, write_end = os.pipe()
        real_write = os.write

        def short_write(descriptor, data):
            return real_write(descriptor, bytes(data)[:2])

        monkeypatch.setattr(SHIM_MODULE.os, "write", short_write)
        try:
            SHIM_MODULE.write_all(write_end, b'{"permission":"deny"}\n')
        finally:
            monkeypatch.undo()
        os.close(write_end)
        assert os.read(read_end, 100) == b'{"permission":"deny"}\n'
        os.close(read_end)

    def test_a_write_that_raises_after_a_short_count_ends_with_exit_one(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        args = hook_args("PreToolUse", mode="classify")
        payload = shell_pre_payload(root, "ls")
        payload["tool_name"] = "BrandNewTool"
        result, _ = _run_fault_program(tmp_path, {"short_write_then_raise": True}, payload, args)
        assert result.returncode == 1
        assert result.stderr == b""

    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_a_closed_reader_ends_with_exit_one_not_one_hundred_twenty(self, tmp_path: Path, fail_closed: bool) -> None:
        env = make_shim_env(tmp_path)
        args = hook_args("PreToolUse", mode="classify", fail_closed=fail_closed)
        read_end, write_end = os.pipe()
        os.close(read_end)
        process = subprocess.run(
            shim_argv(*args),
            input=json.dumps(shell_pre_payload(_workspace(tmp_path), "ls")).encode(),
            stdout=write_end,
            stderr=subprocess.PIPE,
            cwd=_scratch(tmp_path),
            env=shim_process_env(env),
            check=False,
            timeout=60,
        )
        os.close(write_end)
        assert process.returncode == 1
        assert process.stderr == b""

    # --- self_check's in-process rows ---

    @pytest.fixture
    def wrapper(self) -> str:
        return _recorded_wrapper()

    def test_a_healthy_install_passes_every_check(self, wrapper: str) -> None:
        assert SHIM_MODULE.self_check(wrapper) == []

    @pytest.mark.parametrize(
        ("file_name", "damage", "check_name", "error_class"),
        [
            ("field-table.json", "absent", "field-table", "FileNotFoundError"),
            ("field-table.json", "malformed", "field-table", "ValueError"),
            ("parity-exceptions.json", "absent", "parity-exceptions", "FileNotFoundError"),
            ("parity-exceptions.json", "malformed", "parity-exceptions", "ValueError"),
        ],
    )
    def test_a_missing_or_malformed_data_file_fails_its_own_check_naming_the_class(
        self, tmp_path: Path, wrapper: str, file_name: str, damage: str, check_name: str, error_class: str
    ) -> None:
        copied = copy_adapter(tmp_path)
        if damage == "absent":
            (copied.parent / file_name).unlink()
        else:
            (copied.parent / file_name).write_text("{not json")
        failures = load_shim(copied).self_check(wrapper)
        assert f"{check_name}: {error_class}" in failures
        assert all(not failure.startswith("hook-run") for failure in failures)

    def test_a_translation_that_raises_fails_the_translation_check(self, monkeypatch: pytest.MonkeyPatch, wrapper: str) -> None:
        def raising(*arguments, **keywords):
            raise RuntimeError(CANARY)

        monkeypatch.setattr(SHIM_MODULE, "build_claude_payload", raising)
        failures = SHIM_MODULE.self_check(wrapper)
        assert failures == ["translation: RuntimeError"]

    @pytest.mark.parametrize("returned", [{}, {"tool_name": "Shell"}], ids=["no-tool-name", "cursor-tool-name"])
    def test_a_translation_that_returns_another_tool_name_than_the_records_fails_the_translation_check(
        self, monkeypatch: pytest.MonkeyPatch, wrapper: str, returned: dict
    ) -> None:
        monkeypatch.setattr(SHIM_MODULE, "build_claude_payload", lambda *arguments, **keywords: returned)
        assert SHIM_MODULE.self_check(wrapper) == ["translation: SelfCheckMismatch"]

    def test_a_tool_resolution_that_returns_the_cursor_name_as_the_claude_name_fails_the_translation_check(
        self, monkeypatch: pytest.MonkeyPatch, wrapper: str
    ) -> None:
        real_resolve_tool = SHIM_MODULE.resolve_tool
        monkeypatch.setattr(
            SHIM_MODULE,
            "resolve_tool",
            lambda name, record, parity: {**real_resolve_tool(name, record, parity), "claude": name},
        )
        assert SHIM_MODULE.self_check(wrapper) == ["translation: SelfCheckMismatch"]

    def test_a_hook_run_that_raises_fails_the_hook_run_check(self, monkeypatch: pytest.MonkeyPatch, wrapper: str) -> None:
        def raising(*arguments, **keywords):
            raise RuntimeError(CANARY)

        monkeypatch.setattr(SHIM_MODULE, "run_wrapped_hook", raising)
        assert SHIM_MODULE.self_check(wrapper) == ["hook-run: RuntimeError"]

    @pytest.mark.parametrize(
        ("returned", "expected"),
        [
            ((4, b"out-token", b"err-token"), "hook-run: SelfCheckMismatch status 4"),
            ((0, b"out-token", b"err-token"), "hook-run: SelfCheckMismatch status 0"),
            ((3, b"other", b"err-token"), "hook-run: SelfCheckMismatch streams"),
            ((3, b"out-token", b""), "hook-run: SelfCheckMismatch streams"),
            ((3, b"out-token", b"err-token!"), "hook-run: SelfCheckMismatch streams"),
        ],
        ids=["other-status", "status-zero", "other-stdout", "no-stderr", "extra-stderr"],
    )
    def test_a_hook_run_that_returns_another_status_or_other_output_fails_the_hook_run_check_naming_its_outcome(
        self, monkeypatch: pytest.MonkeyPatch, wrapper: str, returned: tuple, expected: str
    ) -> None:
        monkeypatch.setattr(SHIM_MODULE, "run_wrapped_hook", lambda *arguments, **keywords: returned)
        assert SHIM_MODULE.self_check(wrapper) == [expected]

    # One row per member of HOOK_RUN_OUTCOMES, each reached through the real run_wrapped_hook with a stand-in wrapper, clock
    # or Popen error. The ids are literals, so a member added to the constant without a row fails the equality row below.
    HOOK_RUN_OUTCOME_ROWS = (
        ("status", "hook-run: SelfCheckMismatch status 0"),
        ("streams", "hook-run: SelfCheckMismatch streams"),
        ("start-failed", "hook-run: Refusal start-failed ENOENT"),
        ("start-failed", "hook-run: Refusal start-failed status:125"),
        ("timeout", "hook-run: Refusal timeout"),
        ("signal", "hook-run: Refusal signal SIGTERM"),
        ("hook-output-cap", "hook-run: Refusal hook-output-cap"),
        ("unenterable-dir", "hook-run: Refusal unenterable-dir"),
    )

    def _stand_in_wrapper(self, tmp_path: Path, body: str) -> str:
        return str(write_hook_script(tmp_path / "stand-in-wrapper", body))

    @pytest.mark.parametrize(("outcome", "expected"), HOOK_RUN_OUTCOME_ROWS, ids=[row[1] for row in HOOK_RUN_OUTCOME_ROWS])
    def test_each_hook_run_outcome_is_reached_through_the_real_run_and_named_in_the_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str, expected: str
    ) -> None:
        wrapper = {
            "hook-run: SelfCheckMismatch status 0": lambda: self._stand_in_wrapper(tmp_path, "exit 0\n"),
            "hook-run: SelfCheckMismatch streams": lambda: self._stand_in_wrapper(tmp_path, "printf x\nexit 3\n"),
            "hook-run: Refusal start-failed ENOENT": lambda: str(tmp_path / "no-such-wrapper"),
            "hook-run: Refusal start-failed status:125": lambda: self._stand_in_wrapper(tmp_path, "exit 125\n"),
            "hook-run: Refusal timeout": lambda: self._stand_in_wrapper(tmp_path, "exit 3\n"),
            "hook-run: Refusal signal SIGTERM": lambda: self._stand_in_wrapper(tmp_path, "kill -TERM $$\n"),
            "hook-run: Refusal hook-output-cap": lambda: self._stand_in_wrapper(tmp_path, "printf out-token\nexit 3\n"),
            "hook-run: Refusal unenterable-dir": lambda: self._stand_in_wrapper(tmp_path, "exit 3\n"),
        }[expected]()
        if outcome == "timeout":
            # A deadline already in the past, so the run times out whatever order the shim reads the clock in.
            monkeypatch.setattr(SHIM_MODULE, "MIN_DEADLINE_SECONDS", -1000)
        if outcome == "hook-output-cap":
            monkeypatch.setattr(SHIM_MODULE, "HOOK_OUTPUT_CAP_BYTES", 4)
        if outcome == "unenterable-dir":

            def popen_naming_the_directory(*arguments, **keywords):
                raise FileNotFoundError(errno.ENOENT, "No such file or directory", "/")

            monkeypatch.setattr(
                SHIM_MODULE,
                "subprocess",
                SimpleNamespace(
                    Popen=popen_naming_the_directory, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired
                ),
            )
        failures = SHIM_MODULE.self_check(wrapper)
        assert [failure for failure in failures if failure.startswith("hook-run")] == [expected]

    def test_the_rows_above_reach_exactly_the_exported_outcome_set(self) -> None:
        assert {outcome for outcome, _ in self.HOOK_RUN_OUTCOME_ROWS} == set(SHIM_MODULE.HOOK_RUN_OUTCOMES)

    def test_a_refusal_carries_the_cause_that_start_failed_and_the_signal_raises_set(self) -> None:
        assert SHIM_MODULE.start_failed("wrapper", "EAGAIN").cause == "EAGAIN"
        with pytest.raises(SHIM_MODULE.Refusal) as raised:
            SHIM_MODULE.interpret_status(-signal.SIGTERM, deadline_reached=False)
        assert raised.value.cause == "SIGTERM"
        with pytest.raises(SHIM_MODULE.Refusal) as raised:
            SHIM_MODULE.interpret_status(128 + signal.SIGHUP, deadline_reached=False)
        assert raised.value.cause == "SIGHUP"
        assert SHIM_MODULE.Refusal("timeout", "late").cause == ""

    @pytest.mark.parametrize(
        ("field_table_present", "expected_names"),
        [
            (False, ["field-table", "hook-run", "parity-exceptions"]),
            (True, ["hook-run", "parity-exceptions", "translation"]),
        ],
        ids=["without-field-table", "with-field-table"],
    )
    def test_the_failure_names_are_distinct_per_check(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wrapper: str,
        tmp_path: Path,
        field_table_present: bool,
        expected_names: list[str],
    ) -> None:
        copied = copy_adapter(tmp_path)
        if not field_table_present:
            (copied.parent / "field-table.json").unlink()
        (copied.parent / "parity-exceptions.json").unlink()
        module = load_shim(copied)
        monkeypatch.setattr(module, "build_claude_payload", lambda *arguments, **keywords: {})
        monkeypatch.setattr(module, "run_wrapped_hook", lambda *arguments, **keywords: (4, b"", b""))
        names = [failure.split(":")[0] for failure in module.self_check(wrapper)]
        assert sorted(names) == expected_names

    def test_a_passing_run_starts_exactly_one_process_opens_no_log_for_writing_and_writes_no_line(
        self, tmp_path: Path, wrapper: str
    ) -> None:
        env = make_shim_env(tmp_path)
        result, events, extra = run_audited(
            [],
            mode="call",
            function="self_check",
            arguments=(wrapper,),
            events=(*SHIM_MODULE_EVENTS, "open"),
            env=shim_process_env(env),
        )
        assert result.returncode == 0, result.stderr
        assert extra["result"] == []
        assert [event["event"] for event in events if event["event"] != "open"] == ["subprocess.Popen"]
        opens = [event for event in events if event["event"] == "open"]
        assert not [event for event in opens if event["path"].endswith("shim.log")]
        assert list(env.state_dir.iterdir()) == []
