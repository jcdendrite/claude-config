"""Tests for claude-enable-tool.sh's per-session tool re-enable and refusal.

The script has no launcher seam of its own — it calls `claude` by name — so
every test prepends a stub `claude` to PATH that records its argv, the same
recorder-stub-on-PATH harness test_claude_auto.py uses (`_RECORDER_STUB`,
`_run`), adapted with an `expect_launch` flag since this script's refusal
path must never reach the stub at all.

The property under test is which settings payload backs the session — the
right key, flipped the right way, with the tool token itself never leaking
into the `claude` invocation — and that a caller-supplied `--settings` is
refused rather than silently merged or overridden, since either of those
would leave the caller's intent ambiguous.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "claude-enable-tool.sh"
_CLAUDE_DIR = Path(__file__).parent.parent.parent  # claude/.claude
_SETTINGS_JSON = _CLAUDE_DIR / "settings.json"
_LOCAL_BIN = _CLAUDE_DIR.parent / ".local" / "bin"  # claude/.local/bin

_RECORDER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "{recorder}"
"""


def _run(
    args: list[str], tmp_path: Path, *, expect_launch: bool = True
) -> list[str] | subprocess.CompletedProcess:
    """Run claude-enable-tool.sh with a stubbed `claude`.

    When `expect_launch` is True, returns the argv the stub was handed (the
    current behavior). When False, asserts a non-zero exit and that the
    stub was never invoked at all, and returns the completed process instead
    of reading a recorder file that was never written.
    """
    recorder = tmp_path / "recorder.txt"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "claude"
    stub.write_text(_RECORDER_STUB.format(recorder=recorder))
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(_SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )
    if expect_launch:
        assert result.returncode == 0, (
            f"claude-enable-tool.sh failed: {result.stdout}{result.stderr}"
        )
        return recorder.read_text().splitlines()
    assert result.returncode != 0, (
        f"expected a refusal but claude-enable-tool.sh exited 0: "
        f"{result.stdout}{result.stderr}"
    )
    assert not recorder.exists(), (
        "claude was launched despite an expected refusal: "
        f"{recorder.read_text()}"
    )
    return result


def _settings_value(argv: list[str]) -> dict:
    """Parse the JSON payload injected as --settings."""
    index = argv.index("--settings")
    return json.loads(argv[index + 1])


class TestToolTokenSelectsItsOwnSettingsKey:
    def test_artifact_token_enables_only_artifact(self, tmp_path: Path) -> None:
        settings = _settings_value(_run(["artifact"], tmp_path))
        assert settings == {"disableArtifact": False}

    def test_workflow_token_enables_only_workflow(self, tmp_path: Path) -> None:
        settings = _settings_value(_run(["workflow"], tmp_path))
        assert settings == {"disableWorkflows": False}

    @pytest.mark.parametrize("token", ["artifact", "workflow"])
    def test_tool_token_is_consumed_and_not_passed_to_claude(
        self, token: str, tmp_path: Path
    ) -> None:
        argv = _run([token], tmp_path)
        assert token not in argv, f"tool token leaked into claude's argv: {argv}"

    def test_settings_flag_appears_exactly_once(self, tmp_path: Path) -> None:
        argv = _run(["artifact", "--verbose"], tmp_path)
        assert argv.count("--settings") == 1, f"duplicate --settings in {argv}"


class TestArgumentPassthrough:
    def test_extra_flags_and_a_positional_prompt_pass_through_intact(
        self, tmp_path: Path
    ) -> None:
        argv = _run(
            ["workflow", "--verbose", "summarize the open PRs"], tmp_path
        )
        assert "--verbose" in argv, f"flag dropped: {argv}"
        assert "summarize the open PRs" in argv, f"prompt dropped or split: {argv}"

    def test_near_miss_flag_is_not_treated_as_a_refusal_trigger(
        self, tmp_path: Path
    ) -> None:
        """Guards against a future edit widening the --settings match to a
        prefix glob, which would start refusing unrelated flags."""
        argv = _run(["workflow", "--settings-foo", "value"], tmp_path)
        assert "--settings-foo" in argv, f"flag was consumed: {argv}"
        assert "value" in argv, f"flag's value was dropped: {argv}"


class TestCallerSettingsIsRefused:
    """A caller-supplied --settings can't be merged automatically, so the
    script refuses rather than silently overriding or combining it."""

    def test_bare_settings_flag_before_double_dash_refuses(
        self, tmp_path: Path
    ) -> None:
        _run(["workflow", "--settings", '{"foo": true}'], tmp_path, expect_launch=False)

    def test_equals_form_before_double_dash_refuses(self, tmp_path: Path) -> None:
        _run(["workflow", '--settings={"foo": true}'], tmp_path, expect_launch=False)

    def test_refusal_message_names_the_merged_by_hand_escape_hatch(
        self, tmp_path: Path
    ) -> None:
        result = _run(
            ["workflow", "--settings", '{"foo": true}'], tmp_path, expect_launch=False
        )
        assert "claude --settings" in result.stderr, (
            f"refusal message doesn't name the raw escape hatch: {result.stderr}"
        )

    def test_settings_after_a_double_dash_is_positional_and_not_refused(
        self, tmp_path: Path
    ) -> None:
        """`--` makes everything after it positional text, so a literal
        `--settings` there is not a caller flag to refuse on."""
        argv = _run(["workflow", "--", "--settings"], tmp_path)
        assert argv[-2:] == ["--", "--settings"], (
            f"trailing positional args were altered: {argv}"
        )


class TestUnknownOrMissingToolToken:
    def test_unknown_token_exits_non_zero(self, tmp_path: Path) -> None:
        _run(["nonsense"], tmp_path, expect_launch=False)

    def test_missing_token_exits_non_zero(self, tmp_path: Path) -> None:
        _run([], tmp_path, expect_launch=False)


class TestSettingsJsonRegression:
    """Phase 1's whole point is that both keys are true in the shared,
    tracked settings.json every stow consumer inherits."""

    def test_both_disable_keys_are_true_in_the_shared_settings_json(self) -> None:
        settings = json.loads(_SETTINGS_JSON.read_text())
        assert settings["disableArtifact"] is True
        assert settings["disableWorkflows"] is True


class TestWrapperDispatchesItsOwnToken:
    """Behavioral, not a source-text scan: the token substring being present
    in a wrapper's file doesn't prove it execs with the *right* one."""

    @staticmethod
    def _run_wrapper(wrapper_name: str, args: list[str], tmp_path: Path) -> list[str]:
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        recorder = tmp_path / "recorder.txt"
        stub = scripts_dir / "claude-enable-tool.sh"
        stub.write_text(_RECORDER_STUB.format(recorder=recorder))
        stub.chmod(0o755)

        wrapper = _LOCAL_BIN / wrapper_name
        env = dict(os.environ)
        env["HOME"] = str(home)

        result = subprocess.run(
            [str(wrapper), *args], capture_output=True, text=True, env=env, check=False
        )
        assert result.returncode == 0, (
            f"{wrapper_name} failed: {result.stdout}{result.stderr}"
        )
        return recorder.read_text().splitlines()

    def test_claude_workflow_dispatches_the_workflow_token(
        self, tmp_path: Path
    ) -> None:
        argv = self._run_wrapper("claude-workflow", ["--verbose"], tmp_path)
        assert argv[0] == "workflow", f"wrong token dispatched: {argv}"
        assert argv[1:] == ["--verbose"], f"remaining args altered: {argv}"

    def test_claude_artifact_dispatches_the_artifact_token(
        self, tmp_path: Path
    ) -> None:
        argv = self._run_wrapper("claude-artifact", ["--verbose"], tmp_path)
        assert argv[0] == "artifact", f"wrong token dispatched: {argv}"
        assert argv[1:] == ["--verbose"], f"remaining args altered: {argv}"
