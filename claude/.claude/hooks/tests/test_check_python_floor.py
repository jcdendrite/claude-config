"""Tests for check-python-floor.sh.

The hook is an informational SessionStart hook: it prints one JSON notice when the harness PATH's `python3` is
missing, reports no version, or is below the floor `_config.sh` holds, and nothing otherwise.

Every run puts a stub directory ahead of `build_path_without("python3", ...)`'s mirror of the real PATH, so the
real `python3` never answers the probe and a hook that needs another tool does not fail for the wrong reason.
Each stub logs its calls, so a case that expects the hook to stop early can assert that `python3` was never run.
The stub directory also holds a pass-through `timeout`, so the probe's 2-second cap cannot fire under load in a
case that is not about the cap. The no-timeout rig omits it and its PATH mirror lacks `timeout` and `gtimeout`.
"""
from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
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
from .test_git_floor import _output_of, _write_executable, python_floor_fields
from .test_python_floor import _link_real_python3, _write_python3_stub

HOOK = HOOKS_DIR / "check-python-floor.sh"
REGISTERED_COMMAND = "~/.claude/hooks/check-python-floor.sh"
SETTINGS_JSON = CLAUDE_DIR / "settings.json"
REGISTERED_MATCHER = "startup|resume"
REGISTERED_SOURCES = REGISTERED_MATCHER.split("|")
OTHER_SESSION_START_SOURCES = ["clear", "compact", "fork"]
REGISTERED_TIMEOUT_SECONDS = 10

HOOK_RUN_TIMEOUT_SECONDS = 30
# The probe's cap in the hook; the scaled timeout shim records calls under this duration.
PROBE_CAP_SECONDS = 2

# The status-2 notice names the timeout case and tells the engineer to check before installing.
NO_VERSION_STATE_PHRASE = f"did not answer within {PROBE_CAP_SECONDS} seconds"
CHECK_BEFORE_INSTALLING_COMMAND = "env python3 --version"
# The agent text states the agent's whole duty in the third person; the screen text is for the engineer.
REPORT_DUTY = "tell the engineer in its next reply"
REPORT_ONLY_DIRECTIVE = "does nothing else about this unless the engineer asks"
# Control only: the screen text's remedy opens with one of these, so the derived remedy sentences are the right ones.
ENGINEER_IMPERATIVES = ("Install Python", "Run `env python3")
# Parametrize values stay lazy so a failed floor read fails one test, not collection.
BELOW_FLOOR_AND_NO_VERSION_STUB_BODIES = [
    pytest.param(lambda: f"echo '{_version_relative_to_floor(-1)[0]}'", id="below-the-floor"),
    pytest.param(lambda: "exit 1", id="no-readable-version"),
]


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


@pytest.fixture(scope="module")
def path_mirror_without_python3(tmp_path_factory: pytest.TempPathFactory) -> str:
    return build_path_without("python3", tmp_path_factory.mktemp("path-mirror-without-python3"))


@pytest.fixture(scope="module")
def path_mirror_without_python3_or_timeout(tmp_path_factory: pytest.TempPathFactory) -> str:
    farm_dir = tmp_path_factory.mktemp("path-mirror-without-python3-or-timeout")
    path_mirror = build_path_without("python3", farm_dir)
    # The farm is test-owned, so dropping its symlinks to the capping binaries leaves a PATH that cannot cap.
    for capping_binary in ("timeout", "gtimeout"):
        (farm_dir / capping_binary).unlink(missing_ok=True)
        assert shutil.which(capping_binary, path=path_mirror) is None, f"{capping_binary} is still on the built PATH"
    return path_mirror


@pytest.fixture(scope="module")
def path_mirror_without_jq(tmp_path_factory: pytest.TempPathFactory) -> str:
    return build_path_without("jq", tmp_path_factory.mktemp("path-mirror-without-jq"))


class _HookRig:
    """A stub directory ahead of a PATH mirror, plus a log of the `python3` stub's calls."""

    def __init__(self, tmp_path: Path, path_mirror: str, *, with_timeout_stub: bool = True) -> None:
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        if with_timeout_stub:
            # `timeout -k <grace> <duration> <command>...` becomes `<command>...`. _lib_capped_for finds `timeout` first on PATH.
            _write_executable(self.bin_dir / "timeout", '#!/bin/sh\n[ "$1" = "-k" ] && shift 2\nshift\nexec "$@"\n')
        self.path = f"{self.bin_dir}:{path_mirror}"
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.calls_log = tmp_path / "python3-calls.log"

    def write_python3_stub(self, body: str) -> None:
        _write_python3_stub(self.bin_dir, body, calls_log=self.calls_log)

    def link_real_python3(self) -> None:
        _link_real_python3(self.bin_dir)

    def python3_calls(self) -> list[str]:
        return self.calls_log.read_text().splitlines() if self.calls_log.exists() else []

    def run(
        self, stdin: str | dict, *, path_prefix: Path | None = None, cwd: Path | None = None
    ) -> subprocess.CompletedProcess:
        stdin_text = stdin if isinstance(stdin, str) else json.dumps(stdin)
        path = self.path if path_prefix is None else f"{path_prefix}:{self.path}"
        return subprocess.run(
            [str(HOOK)],
            input=stdin_text,
            capture_output=True,
            text=True,
            check=False,
            env={"HOME": str(self.home), "PATH": path},
            cwd=cwd,
            timeout=HOOK_RUN_TIMEOUT_SECONDS,
        )


@pytest.fixture
def rig(tmp_path: Path, path_mirror_without_python3: str) -> _HookRig:
    return _HookRig(tmp_path, path_mirror_without_python3)


@pytest.fixture
def rig_without_timeout(tmp_path: Path, path_mirror_without_python3_or_timeout: str) -> _HookRig:
    return _HookRig(tmp_path, path_mirror_without_python3_or_timeout, with_timeout_stub=False)


@pytest.fixture
def rig_without_jq(tmp_path: Path, path_mirror_without_jq: str) -> _HookRig:
    return _HookRig(tmp_path, path_mirror_without_jq)


def _assert_silent(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, _output_of(result)
    assert result.stdout == "", _output_of(result)
    assert result.stderr == "", _output_of(result)


def _screen_only_sentences(screen_text: str, agent_text: str) -> list[str]:
    """The screen text's sentences after its opening condition sentence, minus the README pointer both texts carry."""
    condition, *later_sentences = re.split(r"(?<=\.) ", screen_text)
    assert agent_text.startswith(condition), f"the texts no longer open with one shared condition sentence: {condition!r}"
    return [sentence for sentence in later_sentences if README_REQUIREMENTS.text not in sentence]


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
    def test_stub_at_or_above_the_floor_prints_nothing(self, rig: _HookRig, minor_offset: int) -> None:
        reported, _ = _version_relative_to_floor(minor_offset)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(_session_start_payload())

        _assert_silent(result)
        assert rig.python3_calls() != [], "the probe never ran, so silence proves nothing"

    def test_the_running_interpreter_prints_nothing(self, rig: _HookRig) -> None:
        rig.link_real_python3()

        result = rig.run(_session_start_payload())

        _assert_silent(result)


@requires_jq
class TestBelowTheFloor:
    @pytest.mark.parametrize("source", REGISTERED_SOURCES)
    def test_notice_names_the_found_version_the_floor_and_the_readme(self, rig: _HookRig, source: str) -> None:
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

    @pytest.mark.parametrize("stub_body", BELOW_FLOOR_AND_NO_VERSION_STUB_BODIES)
    def test_the_agent_text_carries_the_report_duty_the_readme_pointer_and_the_reporting_only_limit(
        self, rig: _HookRig, stub_body: Callable[[], str]
    ) -> None:
        rig.write_python3_stub(stub_body())

        result = rig.run(_session_start_payload())

        screen_text, agent_text = _notice_texts(result)
        assert REPORT_DUTY in agent_text, agent_text
        assert README_REQUIREMENTS.text in agent_text, agent_text
        assert REPORT_ONLY_DIRECTIVE in agent_text, agent_text
        assert REPORT_ONLY_DIRECTIVE not in screen_text, screen_text

    @pytest.mark.parametrize("stub_body", BELOW_FLOOR_AND_NO_VERSION_STUB_BODIES)
    def test_the_agent_text_carries_no_remedy_sentence_from_the_screen_text(
        self, rig: _HookRig, stub_body: Callable[[], str]
    ) -> None:
        rig.write_python3_stub(stub_body())

        result = rig.run(_session_start_payload())

        screen_text, agent_text = _notice_texts(result)
        remedy_sentences = _screen_only_sentences(screen_text, agent_text)
        assert any(imperative in sentence for imperative in ENGINEER_IMPERATIVES for sentence in remedy_sentences), (
            f"the sentences taken from the screen text hold no remedy: {remedy_sentences!r}"
        )
        for sentence in remedy_sentences:
            assert sentence.rstrip(".").lower() not in agent_text.lower(), agent_text

    def test_a_junk_second_line_does_not_reach_the_notice(self, rig: _HookRig) -> None:
        reported, found_version = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'; echo 'ZQXJ line two'")

        result = rig.run(_session_start_payload())

        for text in _notice_texts(result):
            assert found_version in text, text
            assert "ZQXJ" not in text, text


@requires_jq
class TestWithoutTimeoutOrGtimeout:
    """Without a capping binary the probe runs uncapped, so the hook must not depend on `timeout` to run it."""

    def test_a_below_floor_python3_prints_the_notice_naming_the_found_version(self, rig_without_timeout: _HookRig) -> None:
        reported, found_version = _version_relative_to_floor(-1)
        rig_without_timeout.write_python3_stub(f"echo '{reported}'")

        result = rig_without_timeout.run(_session_start_payload())

        for text in _notice_texts(result):
            assert found_version in text, text
        assert rig_without_timeout.python3_calls() != [], "the probe never ran"

    def test_an_at_floor_python3_prints_nothing(self, rig_without_timeout: _HookRig) -> None:
        reported, _ = _version_relative_to_floor(0)
        rig_without_timeout.write_python3_stub(f"echo '{reported}'")

        result = rig_without_timeout.run(_session_start_payload())

        _assert_silent(result)
        assert rig_without_timeout.python3_calls() != [], "the probe never ran, so silence proves nothing"


@requires_jq
class TestNoReadableVersion:
    def _assert_no_version_notice(self, result: subprocess.CompletedProcess) -> None:
        screen_text, agent_text = _notice_texts(result)
        for text in (screen_text, agent_text):
            assert NO_VERSION_STATE_PHRASE in text, text
            assert _floor_version() in text, text
            assert README_REQUIREMENTS.text in text, text
        assert CHECK_BEFORE_INSTALLING_COMMAND in screen_text, screen_text
        assert CHECK_BEFORE_INSTALLING_COMMAND not in agent_text, agent_text

    def test_no_python3_on_the_path(self, rig: _HookRig) -> None:
        result = rig.run(_session_start_payload())

        self._assert_no_version_notice(result)

    def test_a_python3_that_reports_no_version(self, rig: _HookRig) -> None:
        """Status 2 maps to the notice. How the probe reads the output is covered in test_python_floor.py."""
        rig.write_python3_stub("exit 1")

        result = rig.run(_session_start_payload())

        self._assert_no_version_notice(result)
        assert rig.python3_calls() != [], "the python3 stub never ran, so the notice says nothing about its output"

    @pytest.mark.timing
    def test_a_python3_that_hangs_past_the_cap_reads_as_no_version(self, rig: _HookRig) -> None:
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        write_scaled_timeout_shim(rig.bin_dir)
        rig.write_python3_stub(f"exec sleep {scaled_shim_sleep(PROBE_CAP_SECONDS * 10)}")

        with assert_cap_engaged(rig.bin_dir, production_cap=PROBE_CAP_SECONDS):
            result = rig.run(_session_start_payload())

        self._assert_no_version_notice(result)


@requires_jq
class TestProbeWorkingDirectory:
    def test_the_probe_runs_in_the_directory_the_hook_was_started_in(self, rig: _HookRig, tmp_path: Path) -> None:
        """A version-manager shim resolves `python3` per directory, so the hook must not change directory before probing."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        probe_cwd_record = tmp_path / "probe-cwd"
        reported, _ = _version_relative_to_floor(0)
        rig.write_python3_stub(f"pwd -P > '{probe_cwd_record}'\necho '{reported}'")

        result = rig.run(_session_start_payload(), cwd=project_dir)

        _assert_silent(result)
        assert probe_cwd_record.read_text().strip() == str(project_dir.resolve()), "the probe ran in another directory"


@requires_jq
class TestEventAndSourceFilter:
    """Each case runs a below-floor stub, so a hook that skips its filter prints a notice and calls the stub."""

    def _assert_filtered_out(self, rig: _HookRig, stdin: str | dict) -> None:
        reported, _ = _version_relative_to_floor(-1)
        rig.write_python3_stub(f"echo '{reported}'")

        result = rig.run(stdin)

        _assert_silent(result)
        assert rig.python3_calls() == [], f"the probe ran for a payload the filter should have dropped: {stdin!r}"

    def test_another_event_is_ignored(self, rig: _HookRig) -> None:
        self._assert_filtered_out(rig, _session_start_payload(hook_event_name="UserPromptSubmit"))

    @pytest.mark.parametrize("source", [source for source in OTHER_SESSION_START_SOURCES if source not in REGISTERED_SOURCES])
    def test_a_source_other_than_startup_and_resume_is_ignored(self, rig: _HookRig, source: str) -> None:
        self._assert_filtered_out(rig, _session_start_payload(source=source))

    def test_an_absent_source_is_ignored(self, rig: _HookRig) -> None:
        payload = _session_start_payload()
        del payload["source"]

        self._assert_filtered_out(rig, payload)

    def test_an_array_source_is_ignored(self, rig: _HookRig) -> None:
        self._assert_filtered_out(rig, _session_start_payload(source=["startup"]))

    def test_non_json_stdin_is_ignored(self, rig: _HookRig) -> None:
        self._assert_filtered_out(rig, "this is not json")

    def test_empty_stdin_is_ignored(self, rig: _HookRig) -> None:
        self._assert_filtered_out(rig, "")


class TestMissingJq:
    def test_without_jq_the_hook_prints_nothing_and_exits_0(self, rig_without_jq: _HookRig) -> None:
        reported, _ = _version_relative_to_floor(-1)
        rig_without_jq.write_python3_stub(f"echo '{reported}'")

        result = rig_without_jq.run(_session_start_payload())

        _assert_silent(result)
        assert rig_without_jq.python3_calls() == [], "the probe ran although the filter could not"


@requires_jq
@requires_sub_floor_bash
class TestBelowTheBashFloor:
    def test_a_first_bash_below_the_floor_prints_nothing_and_exits_0(self, rig: _HookRig, tmp_path: Path) -> None:
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
