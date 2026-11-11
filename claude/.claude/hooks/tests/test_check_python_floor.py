"""Tests for check-python-floor.sh.

The hook is an informational SessionStart hook: it prints one JSON notice when the harness PATH's `python3` is
missing, reports no version, or is below the floor `_config.sh` holds, and nothing otherwise.

Every run puts a stub directory ahead of `build_path_without("python3", ...)`'s mirror of the real PATH, so the
real `python3` never answers the probe and a hook that needs another tool does not fail for the wrong reason.
Each stub logs its calls, so a case that expects the hook to stop early can assert that `python3` was never run.
"""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    assert_cap_engaged,
    build_path_without,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from .conftest import requires_jq
from .test_bash_floor import HOOKS_DOC_PYTHON_VERSION, README_REQUIREMENTS, SUB_FLOOR_BASH, requires_sub_floor_bash
from .test_git_floor import _output_of, python_floor_fields
from .test_python_floor import _link_real_python3, _write_python3_stub

HOOK = HOOKS_DIR / "check-python-floor.sh"
REGISTERED_COMMAND = "~/.claude/hooks/check-python-floor.sh"
SETTINGS_JSON = CLAUDE_DIR / "settings.json"
REGISTERED_MATCHER = "startup|resume"
REGISTERED_SOURCES = REGISTERED_MATCHER.split("|")
# Candidate sources the hook must ignore; any the matcher registers is dropped from the deny examples and runs as an allow case.
OTHER_SESSION_START_SOURCES = ["clear", "compact", "fork"]
REGISTERED_TIMEOUT_SECONDS = 10

HOOK_RUN_TIMEOUT_SECONDS = 30
# The probe's cap in the hook; the scaled timeout shim records calls under this duration.
PROBE_CAP_SECONDS = 2

# The status-2 notice names the timeout case and tells the engineer to check before installing.
NO_VERSION_STATE_PHRASE = f"did not answer within {PROBE_CAP_SECONDS} seconds"
CHECK_BEFORE_INSTALLING_COMMAND = "env python3 --version"
# The only guard against the agent reacting to the notice by trying to install Python; the screen text is for the engineer.
REPORT_ONLY_DIRECTIVE = "Do nothing else about it unless the engineer asks"


def _floor_version() -> str:
    major, minor = python_floor_fields()
    return f"{major}.{minor}"


def _version_relative_to_floor(minor_offset: int) -> tuple[str, str]:
    """The probe's `<major> <minor>` stub output and the `<major>.<minor>` notice text, `minor_offset` minors from the floor."""
    major, minor = python_floor_fields()
    shifted_minor = int(minor) + minor_offset
    return f"{major} {shifted_minor}", f"{major}.{shifted_minor}"


def _session_start_payload(**overrides: object) -> dict:
    payload: dict = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-1"}
    payload.update(overrides)
    return payload


class _HookRig:
    """A stub directory ahead of a PATH mirror that omits `omitted_binary`, plus a log of the `python3` stub's calls."""

    def __init__(self, tmp_path: Path, *, omitted_binary: str = "python3") -> None:
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        farm_dir = tmp_path / "farm"
        farm_dir.mkdir()
        self.path = f"{self.bin_dir}:{build_path_without(omitted_binary, farm_dir)}"
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.calls_log = tmp_path / "python3-calls.log"

    def write_python3_stub(self, body: str) -> None:
        _write_python3_stub(self.bin_dir, body, calls_log=self.calls_log)

    def link_real_python3(self) -> None:
        _link_real_python3(self.bin_dir)

    def python3_calls(self) -> list[str]:
        return self.calls_log.read_text().splitlines() if self.calls_log.exists() else []

    def run(self, stdin: str | dict, *, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
        stdin_text = stdin if isinstance(stdin, str) else json.dumps(stdin)
        path = self.path if path_prefix is None else f"{path_prefix}:{self.path}"
        return subprocess.run(
            [str(HOOK)],
            input=stdin_text,
            capture_output=True,
            text=True,
            check=False,
            env={"HOME": str(self.home), "PATH": path},
            timeout=HOOK_RUN_TIMEOUT_SECONDS,
        )


def _assert_silent(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, _output_of(result)
    assert result.stdout == "", _output_of(result)
    assert result.stderr == "", _output_of(result)


def _notice_texts(result: subprocess.CompletedProcess) -> tuple[str, str]:
    """The screen and agent texts of the one-object notice, after asserting its envelope."""
    assert result.returncode == 0, _output_of(result)
    assert result.stderr == "", _output_of(result)
    notice = json.loads(result.stdout)
    assert set(notice) == {"systemMessage", "hookSpecificOutput"}, _output_of(result)
    assert set(notice["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}, _output_of(result)
    assert notice["hookSpecificOutput"]["hookEventName"] == "SessionStart", _output_of(result)
    return notice["systemMessage"], notice["hookSpecificOutput"]["additionalContext"]


@requires_jq
class TestAtOrAboveTheFloor:
    @pytest.mark.parametrize(
        "minor_offset",
        [pytest.param(0, id="exactly-the-floor"), pytest.param(1, id="minor-above")],
    )
    def test_stub_at_or_above_the_floor_prints_nothing(self, tmp_path: Path, minor_offset: int) -> None:
        rig = _HookRig(tmp_path)
        reported, _ = _version_relative_to_floor(minor_offset)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(_session_start_payload())

        _assert_silent(result)
        assert rig.python3_calls() != [], "the probe never ran, so silence proves nothing"

    def test_the_running_interpreter_prints_nothing(self, tmp_path: Path) -> None:
        rig = _HookRig(tmp_path)
        rig.link_real_python3()

        result = rig.run(_session_start_payload())

        _assert_silent(result)


@requires_jq
class TestBelowTheFloor:
    @pytest.mark.parametrize("source", REGISTERED_SOURCES)
    def test_notice_names_the_found_version_the_floor_and_the_readme(self, tmp_path: Path, source: str) -> None:
        rig = _HookRig(tmp_path)
        reported, found_version = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(_session_start_payload(source=source))

        screen_text, agent_text = _notice_texts(result)
        for text in (screen_text, agent_text):
            assert found_version in text, text
            assert _floor_version() in text, text
            assert README_REQUIREMENTS.text in text, text
            assert NO_VERSION_STATE_PHRASE not in text, text
            assert CHECK_BEFORE_INSTALLING_COMMAND not in text, text
        assert HOOKS_DOC_PYTHON_VERSION.text in agent_text, agent_text

    def test_only_the_agent_text_carries_the_reporting_only_directive(self, tmp_path: Path) -> None:
        rig = _HookRig(tmp_path)
        reported, _ = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(_session_start_payload())

        screen_text, agent_text = _notice_texts(result)
        assert REPORT_ONLY_DIRECTIVE in agent_text, agent_text
        assert REPORT_ONLY_DIRECTIVE not in screen_text, screen_text

    def test_a_junk_second_line_does_not_reach_the_notice(self, tmp_path: Path) -> None:
        rig = _HookRig(tmp_path)
        reported, found_version = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'; echo 'ZQXJ line two'")

        result = rig.run(_session_start_payload())

        for text in _notice_texts(result):
            assert found_version in text, text
            assert "ZQXJ" not in text, text


@requires_jq
class TestNoReadableVersion:
    def _assert_no_version_notice(self, result: subprocess.CompletedProcess, *, stub_output: str = "") -> None:
        for text in _notice_texts(result):
            assert NO_VERSION_STATE_PHRASE in text, text
            assert CHECK_BEFORE_INSTALLING_COMMAND in text, text
            assert _floor_version() in text, text
            assert README_REQUIREMENTS.text in text, text
            if stub_output:
                assert stub_output not in text, text

    def test_no_python3_on_the_path(self, tmp_path: Path) -> None:
        rig = _HookRig(tmp_path)

        result = rig.run(_session_start_payload())

        self._assert_no_version_notice(result)

    @pytest.mark.parametrize(
        ("stub_body", "stub_output"),
        [
            pytest.param("exit 1", "", id="python3-fails"),
            pytest.param("echo garbage", "garbage", id="garbage"),
            pytest.param("echo 'ZQXJ 14'", "ZQXJ", id="non-numeric-major"),
            pytest.param(f"echo '3 {'9' * 1000}'", "9" * 10, id="thousand-digit-minor"),
        ],
    )
    def test_a_python3_that_reports_no_readable_version(self, tmp_path: Path, stub_body: str, stub_output: str) -> None:
        rig = _HookRig(tmp_path)
        rig.write_python3_stub(stub_body)

        result = rig.run(_session_start_payload())

        self._assert_no_version_notice(result, stub_output=stub_output)

    @pytest.mark.timing
    def test_a_python3_that_hangs_past_the_cap_reads_as_no_version(self, tmp_path: Path) -> None:
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        rig = _HookRig(tmp_path)
        write_scaled_timeout_shim(rig.bin_dir)
        rig.write_python3_stub(f"exec sleep {scaled_shim_sleep(PROBE_CAP_SECONDS * 3)}")

        with assert_cap_engaged(rig.bin_dir, production_cap=PROBE_CAP_SECONDS):
            result = rig.run(_session_start_payload())

        self._assert_no_version_notice(result)


@requires_jq
class TestEventAndSourceFilter:
    """Each case runs a below-floor stub, so a hook that skips its filter prints a notice and calls the stub."""

    def _assert_filtered_out(self, tmp_path: Path, stdin: str | dict) -> None:
        rig = _HookRig(tmp_path)
        reported, _ = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(stdin)

        _assert_silent(result)
        assert rig.python3_calls() == [], f"the probe ran for a payload the filter should have dropped: {stdin!r}"

    def test_another_event_is_ignored(self, tmp_path: Path) -> None:
        self._assert_filtered_out(tmp_path, _session_start_payload(hook_event_name="UserPromptSubmit"))

    @pytest.mark.parametrize("source", [source for source in OTHER_SESSION_START_SOURCES if source not in REGISTERED_SOURCES])
    def test_a_source_other_than_startup_and_resume_is_ignored(self, tmp_path: Path, source: str) -> None:
        self._assert_filtered_out(tmp_path, _session_start_payload(source=source))

    def test_an_absent_source_is_ignored(self, tmp_path: Path) -> None:
        payload = _session_start_payload()
        del payload["source"]

        self._assert_filtered_out(tmp_path, payload)

    def test_an_array_source_is_ignored(self, tmp_path: Path) -> None:
        self._assert_filtered_out(tmp_path, _session_start_payload(source=["startup"]))

    def test_non_json_stdin_is_ignored(self, tmp_path: Path) -> None:
        self._assert_filtered_out(tmp_path, "this is not json")

    def test_empty_stdin_is_ignored(self, tmp_path: Path) -> None:
        self._assert_filtered_out(tmp_path, "")


class TestMissingJq:
    def test_without_jq_the_hook_prints_nothing_and_exits_0(self, tmp_path: Path) -> None:
        rig = _HookRig(tmp_path, omitted_binary="jq")
        reported, _ = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(_session_start_payload())

        _assert_silent(result)
        assert rig.python3_calls() == [], "the probe ran although the filter could not"


@requires_jq
@requires_sub_floor_bash
class TestBelowTheBashFloor:
    def test_a_first_bash_below_the_floor_prints_nothing_and_exits_0(self, tmp_path: Path) -> None:
        rig = _HookRig(tmp_path)
        reported, _ = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'")
        payload = _session_start_payload()
        # Liveness control: the same payload prints the notice under the ambient bash, so silence below is the below-floor bash.
        control = rig.run(payload)
        _notice_texts(control)
        sub_floor_bin = tmp_path / "sub-floor-bin"
        sub_floor_bin.mkdir()
        (sub_floor_bin / "bash").symlink_to(SUB_FLOOR_BASH)

        result = rig.run(payload, path_prefix=sub_floor_bin)

        assert result.returncode == 0, _output_of(result)
        assert result.stdout == "", _output_of(result)


class TestRegistration:
    def test_one_session_start_group_registers_the_hook_for_startup_and_resume_with_a_timeout(self) -> None:
        hooks_by_event = json.loads(SETTINGS_JSON.read_text())["hooks"]

        events_registering_hook = [
            event
            for event, groups in hooks_by_event.items()
            if any(entry.get("command") == REGISTERED_COMMAND for group in groups for entry in group["hooks"])
        ]
        matching_groups = [
            group
            for group in hooks_by_event["SessionStart"]
            if any(entry.get("command") == REGISTERED_COMMAND for entry in group["hooks"])
        ]

        assert events_registering_hook == ["SessionStart"]
        [group] = matching_groups
        assert group.get("matcher") == REGISTERED_MATCHER
        [hook_entry] = group["hooks"]
        assert hook_entry["type"] == "command"
        assert "if" not in hook_entry
        assert hook_entry.get("timeout") == REGISTERED_TIMEOUT_SECONDS

    def test_the_registered_hook_file_exists_and_is_executable(self) -> None:
        assert HOOK.is_file()
        assert HOOK.stat().st_mode & stat.S_IXUSR
