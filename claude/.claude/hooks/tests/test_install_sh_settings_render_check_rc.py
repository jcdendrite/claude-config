"""Tests for the settings.json render check: install.sh's rc-line wiring
(ensure_settings_render_check) and check-settings-render.sh itself.

The rc-file-mutation safety net (backup/undo/symlink-companion resolution)
lives in the shared _ensure_rc_block helper and is already exercised
exhaustively by the 18 tests in test_install_sh_local_bin_path.py against
that same helper -- these tests cover only what's new here: the rc line
content/idempotency, and check-settings-render.sh's own detection logic.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_CHECK_SCRIPT = SCRIPTS_DIR / "check-settings-render.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_LOCAL_BIN_PATH_START = "# INSTALL_TEST_FIXTURE: local-bin-path — start\n"
_LOCAL_BIN_PATH_END = "# INSTALL_TEST_FIXTURE: local-bin-path — end"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: settings-render-check-rc — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: settings-render-check-rc — end"


def _extract_block(start_marker: str, end_marker: str, required_substring: str) -> str:
    """Return the text between a start/end INSTALL_TEST_FIXTURE marker pair.

    Delimited by explicit marker comments rather than shell-syntax matching,
    same rationale as test_install_sh_local_bin_path.py's own extraction --
    a future reorder can't silently pick up the wrong text while the test
    keeps passing.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(start_marker) : end]
    assert required_substring in block, (
        f"extracted block is missing {required_substring!r}; markers in "
        f"{_INSTALL_SH} are probably misplaced. Got: {block!r}"
    )
    return block


def _run_settings_render_check_rc_block(test_home: Path) -> subprocess.CompletedProcess:
    """Run ensure_settings_render_check with $HOME pointed at an isolated dir.

    Concatenates the local-bin-path block (which defines the shared
    _ensure_rc_block/_undo_rc_append/_file_has_active_reference helpers) with
    the settings-render-check-rc block (which defines
    ensure_settings_render_check) -- this is the same pairing install.sh
    itself relies on at runtime, just extracted instead of stubbed.
    """
    local_bin_path_block = _extract_block(_LOCAL_BIN_PATH_START, _LOCAL_BIN_PATH_END, "_ensure_rc_block")
    render_check_block = _extract_block(_FIXTURE_START, _FIXTURE_END, "ensure_settings_render_check")
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    script = "set -e\n" + local_bin_path_block + "\n" + render_check_block + "\nensure_settings_render_check\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestInstallShSettingsRenderCheckRc:
    def test_appends_check_invocation_to_bashrc_when_absent(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()

        result = _run_settings_render_check_rc_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        bashrc = (test_home / ".bashrc").read_text()
        assert "check-settings-render.sh" in bashrc
        assert "BEGIN claude-config: check settings.json render" in bashrc

    def test_second_run_is_a_byte_for_byte_no_op(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()

        first = _run_settings_render_check_rc_block(test_home)
        assert first.returncode == 0, f"first run must exit 0; stderr={first.stderr!r}"
        after_first = (test_home / ".bashrc").read_text()

        second = _run_settings_render_check_rc_block(test_home)
        assert second.returncode == 0, f"second run must exit 0; stderr={second.stderr!r}"
        assert (test_home / ".bashrc").read_text() == after_first, (
            "a second run must not duplicate the check invocation"
        )

    def test_preserves_existing_rc_content(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        bashrc.write_text("alias ll='ls -la'\n")

        result = _run_settings_render_check_rc_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        content = bashrc.read_text()
        assert "alias ll='ls -la'" in content
        assert "check-settings-render.sh" in content


def _run_check_script(test_home: Path, *, config_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        [str(_CHECK_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestCheckSettingsRenderScript:
    def test_warns_when_settings_json_is_missing(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)

        result = _run_check_script(test_home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert "install.sh" in result.stderr

    def test_warns_when_settings_json_is_a_dangling_symlink(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        (test_home / ".claude" / "settings.json").symlink_to(tmp_path / "nonexistent-target")

        result = _run_check_script(test_home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert "install.sh" in result.stderr

    def test_warns_when_settings_json_is_a_resolving_symlink(self, tmp_path: Path) -> None:
        """A symlink that resolves fine still means render-settings.sh's
        mktemp+mv replacement never ran -- must warn same as a dangling one."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        real_target = tmp_path / "elsewhere-settings.json"
        real_target.write_text("{}")
        (test_home / ".claude" / "settings.json").symlink_to(real_target)

        result = _run_check_script(test_home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert "install.sh" in result.stderr

    def test_silent_when_settings_json_is_a_real_file(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        (test_home / ".claude" / "settings.json").write_text("{}")

        result = _run_check_script(test_home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert result.stderr == "", f"a real settings.json must not warn; stderr={result.stderr!r}"

    def test_names_the_real_repo_path_when_manifest_present(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        repo_dir = tmp_path / "checkout" / "claude-config"
        (test_home / ".claude-config-source").write_text(f"{repo_dir}\n")

        result = _run_check_script(test_home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert str(repo_dir) in result.stderr

    def test_generic_instruction_when_manifest_absent(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        # Deliberately no ~/.claude-config-source.

        result = _run_check_script(test_home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert "install.sh" in result.stderr

    def test_respects_claude_config_dir_override(self, tmp_path: Path) -> None:
        """A diverged CLAUDE_CONFIG_DIR profile is checked at its own
        location, not the default ~/.claude -- matches render-settings.sh's
        own config_dir resolution."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()
        (profile_dir / "settings.json").write_text("{}")
        # A dangling ~/.claude/settings.json must be ignored once
        # CLAUDE_CONFIG_DIR points elsewhere.
        (test_home / ".claude").mkdir(parents=True)
        (test_home / ".claude" / "settings.json").symlink_to(tmp_path / "nonexistent-target")

        result = _run_check_script(test_home, config_dir=profile_dir)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert result.stderr == "", (
            f"a real settings.json in the CLAUDE_CONFIG_DIR profile must not warn; stderr={result.stderr!r}"
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission checks")
    def test_unreadable_manifest_falls_back_to_generic_instruction(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        manifest = test_home / ".claude-config-source"
        manifest.write_text("/some/repo\n")
        manifest.chmod(0o000)
        try:
            result = _run_check_script(test_home)
        finally:
            manifest.chmod(0o644)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert "install.sh" in result.stderr
        assert "/some/repo" not in result.stderr
