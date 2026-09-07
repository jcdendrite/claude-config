"""Tests for the settings.json render check: install.sh's rc-line wiring
(ensure_settings_render) and ensure-settings-render.sh itself -- the M1
repair-not-warn replacement for the deleted check-settings-render.sh.

The rc-file-mutation safety net (backup/undo/symlink-companion resolution)
lives in the shared _ensure_rc_block helper and is already exercised
exhaustively by the 18 tests in test_install_sh_local_bin_path.py against
that same helper -- these tests cover only what's new here: the rc line
content/idempotency, and ensure-settings-render.sh's own repair behavior.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_ENSURE_SCRIPT = SCRIPTS_DIR / "ensure-settings-render.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_RC_HELPERS_START = "# INSTALL_TEST_FIXTURE: rc-block-helpers — start\n"
_RC_HELPERS_END = "# INSTALL_TEST_FIXTURE: rc-block-helpers — end"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: settings-render-rc — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: settings-render-rc — end"


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


def _run_settings_render_rc_block(test_home: Path) -> subprocess.CompletedProcess:
    """Run ensure_settings_render with $HOME pointed at an isolated dir.

    Concatenates the rc-block-helpers block (which defines the shared
    _ensure_rc_block/_undo_rc_append/_file_has_active_reference helpers) with
    the settings-render-rc block (which defines ensure_settings_render) --
    the same pairing install.sh itself relies on at runtime, just extracted
    instead of stubbed.
    """
    rc_helpers_block = _extract_block(_RC_HELPERS_START, _RC_HELPERS_END, "_ensure_rc_block")
    render_rc_block = _extract_block(_FIXTURE_START, _FIXTURE_END, "ensure_settings_render")
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    script = "set -e\n" + rc_helpers_block + "\n" + render_rc_block + "\nensure_settings_render\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestInstallShSettingsRenderRc:
    def test_appends_render_invocation_to_bashrc_when_absent(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()

        result = _run_settings_render_rc_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        bashrc = (test_home / ".bashrc").read_text()
        assert "ensure-settings-render.sh" in bashrc
        assert "BEGIN claude-config: ensure settings.json render" in bashrc

    def test_second_run_is_a_byte_for_byte_no_op(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()

        first = _run_settings_render_rc_block(test_home)
        assert first.returncode == 0, f"first run must exit 0; stderr={first.stderr!r}"
        after_first = (test_home / ".bashrc").read_text()

        second = _run_settings_render_rc_block(test_home)
        assert second.returncode == 0, f"second run must exit 0; stderr={second.stderr!r}"
        assert (test_home / ".bashrc").read_text() == after_first, (
            "a second run must not duplicate the render invocation"
        )

    def test_preserves_existing_rc_content(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        test_home.mkdir()
        bashrc = test_home / ".bashrc"
        bashrc.write_text("alias ll='ls -la'\n")

        result = _run_settings_render_rc_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        content = bashrc.read_text()
        assert "alias ll='ls -la'" in content
        assert "ensure-settings-render.sh" in content

    def test_dangling_symlinked_bashrc_warns_and_skips(self, tmp_path: Path) -> None:
        """Pins that ensure_settings_render passes its own needle/description/
        hint-line arguments through _ensure_rc_block correctly, not just that
        _ensure_rc_block's safety net works in the abstract (already covered
        exhaustively for ensure_local_bin_on_path in
        test_install_sh_local_bin_path.py) -- a future edit that special-cases
        one caller's argument shape would otherwise go uncaught here."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        (test_home / ".bashrc").symlink_to(tmp_path / "nonexistent-target")

        result = _run_settings_render_rc_block(test_home)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        assert not (test_home / ".bashrc.local").exists(), (
            "a dangling symlink must not produce a companion write"
        )
        assert "ensure-settings-render.sh" in result.stderr
        assert "symlink" in result.stderr


def _make_home_with_base(tmp_path: Path, base_content: dict) -> Path:
    """An isolated $HOME with a real render-settings.sh (symlinked, not
    reimplemented, so these tests exercise the actual script under review)
    and a tracked settings.base.json."""
    home = tmp_path / "home"
    scripts_dir = home / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
    (home / ".claude" / "settings.base.json").write_text(json.dumps(base_content))
    return home


def _run_ensure_script(test_home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        [str(_ENSURE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestEnsureSettingsRenderScript:
    """M1's Verification: three broken states must each end with a real,
    base-carrying settings.json, and an unrenderable base must warn and
    leave no partial file -- see the plan's "Repairing rc block (M1)" entry.
    """

    def test_dangling_symlink_is_replaced_by_a_real_rendered_file(self, tmp_path: Path) -> None:
        base_content = {"otherKey": "base-value"}
        home = _make_home_with_base(tmp_path, base_content)
        (home / ".claude" / "settings.json").symlink_to(tmp_path / "nonexistent-target")

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = home / ".claude" / "settings.json"
        assert rendered.exists() and not rendered.is_symlink(), (
            "settings.json must end up a plain regenerated file, not a "
            f"symlink; stderr={result.stderr!r}"
        )
        assert json.loads(rendered.read_text()) == base_content

    def test_plain_missing_file_is_created(self, tmp_path: Path) -> None:
        base_content = {"otherKey": "base-value"}
        home = _make_home_with_base(tmp_path, base_content)
        # settings.json deliberately absent -- e.g. a fresh machine mid-install.

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = home / ".claude" / "settings.json"
        assert rendered.exists() and not rendered.is_symlink()
        assert json.loads(rendered.read_text()) == base_content

    def test_symlink_resolving_to_session_written_stub_is_replaced(self, tmp_path: Path) -> None:
        """The row-14 write-through shape: a pre-rename settings.json
        symlink that still resolves, but only to a stub a live session wrote
        through it (e.g. a /theme change), carrying none of base's own keys.
        """
        base_content = {"permissions": {"deny": ["x"]}}
        home = _make_home_with_base(tmp_path, base_content)
        stub_target = tmp_path / "stub-settings.json"
        stub_target.write_text(json.dumps({"theme": "dark"}))
        (home / ".claude" / "settings.json").symlink_to(stub_target)

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = home / ".claude" / "settings.json"
        assert rendered.exists() and not rendered.is_symlink()
        merged = json.loads(rendered.read_text())
        assert merged["permissions"] == {"deny": ["x"]}
        assert merged["theme"] == "dark", (
            "an unclaimed top-level key from the stub must still carry "
            "forward under rule 3"
        )

    def test_unrenderable_base_warns_and_leaves_no_partial_file(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render.

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert "settings.json render failed" in result.stderr
        assert not (home / ".claude" / "settings.json").exists()

    def test_failure_names_the_real_repo_path_when_manifest_present(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        repo_dir = tmp_path / "checkout" / "claude-config"
        (home / ".claude-config-source").write_text(f"{repo_dir}\n")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render.

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"must always exit 0; stderr={result.stderr!r}"
        assert str(repo_dir) in result.stderr

    def test_missing_render_script_is_silent(self, tmp_path: Path) -> None:
        """A mid-install state (render-settings.sh not yet stowed) must not
        warn -- install.sh itself is already responsible for that state."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_ensure_script(home)

        assert result.returncode == 0
        assert result.stderr == ""

    def test_successful_render_over_a_real_settings_json_is_silent(self, tmp_path: Path) -> None:
        """A well-formed settings.json (already rendered) must not warn --
        only a render failure adds ensure-settings-render.sh's own hint."""
        base_content = {"otherKey": "base-value"}
        home = _make_home_with_base(tmp_path, base_content)
        (home / ".claude" / "settings.json").write_text(json.dumps({"otherKey": "stale"}))

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "render failed" not in result.stderr
