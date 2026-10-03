"""Tests for the settings.json render check: install.sh's rc-line wiring
(ensure_settings_render) and ensure-settings-render.sh itself, which
repairs the resolved profile's settings.json on every new shell rather than
only warning that a render is needed.

The rc-file-mutation safety net (backup/undo/symlink-companion resolution)
lives in the shared _ensure_rc_block helper and is already exercised
exhaustively by the 18 tests in test_install_sh_local_bin_path.py against
that same helper -- these tests cover only what's new here: the rc line
content/idempotency, and ensure-settings-render.sh's own repair behavior.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    SCRIPTS_DIR,
    assert_cap_engaged,
    scaled_shim_sleep,
    symlink_hooks_lib_chain,
    write_scaled_timeout_shim,
)

from .conftest import TIMEOUT_SHIM_SLEEP_SECONDS

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


def _run_settings_render_rc_block(
    test_home: Path, path_prefix: Path | None = None
) -> subprocess.CompletedProcess:
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
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    script = "set -e\n" + rc_helpers_block + "\n" + render_rc_block + "\nensure_settings_render\n"
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=15,
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

    def test_zshrc_receives_the_same_block_as_bashrc(self, tmp_path: Path) -> None:
        """The executed zsh case skips when zsh is not installed, so this
        check keeps the .zshrc write asserted everywhere. install.sh writes an
        rc file only for a shell found on PATH, so a stub zsh stands in."""
        test_home = tmp_path / "home"
        test_home.mkdir()
        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        stub_zsh = stub_bin / "zsh"
        stub_zsh.write_text("#!/bin/sh\nexit 0\n")
        stub_zsh.chmod(0o755)

        result = _run_settings_render_rc_block(test_home, path_prefix=stub_bin)

        assert result.returncode == 0, f"block must exit 0; stderr={result.stderr!r}"
        zshrc = (test_home / ".zshrc").read_text()
        assert "BEGIN claude-config: ensure settings.json render" in zshrc
        assert zshrc == (test_home / ".bashrc").read_text()

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


def _run_ensure_script(
    test_home: Path, extra_path_dir: Path | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_path_dir is not None:
        env["PATH"] = f"{extra_path_dir}:{env['PATH']}"
    return subprocess.run(
        [str(_ENSURE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=15,
    )


class TestEnsureSettingsRenderScript:
    """Three broken states must each end with a real, base-carrying
    settings.json, and an unrenderable base must warn and leave no partial
    file.
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
        """A write-through shape: a pre-rename settings.json symlink that
        still resolves, but only to a stub a live session wrote through it
        (e.g. a /theme change), carrying none of base's own keys.
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
        assert f"render of {home}/.claude/settings.json failed" in result.stderr
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


class TestEnsureSettingsRenderTimeoutGuard:
    """A stalled render-settings.sh must not hang shell startup indefinitely."""

    def test_hung_render_is_bounded_by_timeout(self, tmp_path: Path) -> None:
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available")
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_scaled_timeout_shim(bin_dir)
        stub = scripts_dir / "render-settings.sh"
        stub.write_text(f"#!/bin/bash\nsleep {scaled_shim_sleep(TIMEOUT_SHIM_SLEEP_SECONDS)}\n")
        stub.chmod(0o755)
        (home / ".claude" / "settings.base.json").write_text(json.dumps({"otherKey": "v"}))

        with assert_cap_engaged(bin_dir, production_cap=5, command="render-settings.sh"):
            result = _run_ensure_script(home, extra_path_dir=bin_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "did not finish within its 5s cap" in result.stderr, (
            "a render killed by the timeout must exit with a cap-kill status, "
            "triggering the cap hint -- an uncapped render would instead "
            "succeed silently after sleeping the full duration"
        )


class TestEnsureSettingsRenderRepeatRuns:
    """Every run renders; render-settings.sh itself skips the write when the
    result is unchanged, so ensure-settings-render.sh keeps no cache."""

    def _make_home_with_counting_stub(self, tmp_path: Path, base_content: dict) -> tuple[Path, Path]:
        """Home whose render-settings.sh wrapper counts invocations (one
        line per call to invocations.log) before delegating to the real
        script."""
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        invocations = home / "invocations.log"
        real_render = SCRIPTS_DIR / "render-settings.sh"
        stub = scripts_dir / "render-settings.sh"
        stub.write_text(
            f"#!/bin/bash\n"
            f'echo invoked >> "{invocations}"\n'
            f'exec "{real_render}" "$@"\n'
        )
        stub.chmod(0o755)
        (home / ".claude" / "settings.base.json").write_text(json.dumps(base_content))
        return home, invocations

    def test_unchanged_inputs_render_again_without_rewriting_settings_json(self, tmp_path: Path) -> None:
        home, invocations = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})
        rendered_path = home / ".claude" / "settings.json"

        first = _run_ensure_script(home)
        assert first.returncode == 0, f"stderr={first.stderr!r}"
        os.utime(rendered_path, ns=(1_000_000_000, 1_000_000_000))

        second = _run_ensure_script(home)

        assert second.returncode == 0, f"stderr={second.stderr!r}"
        assert invocations.read_text().count("invoked\n") == 2
        assert rendered_path.stat().st_mtime_ns == 1_000_000_000, (
            "an unchanged render must leave settings.json untouched"
        )

    def test_no_cache_file_is_written(self, tmp_path: Path) -> None:
        home, _ = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})

        _run_ensure_script(home)

        assert sorted(path.name for path in (home / ".claude").iterdir() if path.name.startswith(".")) == []

    def test_changed_base_file_is_rendered(self, tmp_path: Path) -> None:
        home, _ = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})

        _run_ensure_script(home)
        (home / ".claude" / "settings.base.json").write_text(json.dumps({"otherKey": "changed"}))
        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = json.loads((home / ".claude" / "settings.json").read_text())
        assert rendered == {"otherKey": "changed"}

    def test_changed_overlay_file_is_rendered(self, tmp_path: Path) -> None:
        home, _ = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})
        overlay_file = home / ".claude" / "settings.overlay.json"
        overlay_file.write_text(json.dumps({"skillListingBudgetFraction": 0.2}))

        _run_ensure_script(home)
        overlay_file.write_text(json.dumps({"skillListingBudgetFraction": 0.4}))
        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = json.loads((home / ".claude" / "settings.json").read_text())
        assert rendered["skillListingBudgetFraction"] == 0.4

    def test_corrupted_target_is_repaired(self, tmp_path: Path) -> None:
        home, _ = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})
        _run_ensure_script(home)
        rendered_path = home / ".claude" / "settings.json"
        rendered_path.write_text("{not valid json")

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert json.loads(rendered_path.read_text()) == {"otherKey": "v"}

    def test_zero_byte_target_is_repaired_without_a_failure_hint(self, tmp_path: Path) -> None:
        """A settings.json truncated to zero bytes is the corruption shape the
        repair most needs to handle."""
        home, _ = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})
        _run_ensure_script(home)
        rendered_path = home / ".claude" / "settings.json"
        rendered_path.write_text("")

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "render of" not in result.stderr
        assert json.loads(rendered_path.read_text()) == {"otherKey": "v"}

    def test_write_through_symlink_is_replaced_with_a_regular_file(self, tmp_path: Path) -> None:
        home, _ = self._make_home_with_counting_stub(tmp_path, {"otherKey": "v"})
        _run_ensure_script(home)
        stub_target = tmp_path / "stub-settings.json"
        stub_target.write_text(json.dumps({"theme": "dark"}))
        rendered_path = home / ".claude" / "settings.json"
        rendered_path.unlink()
        rendered_path.symlink_to(stub_target)

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not rendered_path.is_symlink()
        assert "render of" not in result.stderr

    def test_failing_render_warns_on_every_run(self, tmp_path: Path) -> None:
        """A failed render must not suppress the next shell's warning."""
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        # No settings.base.json, so every render fails.

        first = _run_ensure_script(home)
        second = _run_ensure_script(home)

        assert "render of" in first.stderr
        assert "render of" in second.stderr


class TestEnsureSettingsRenderProfileResolution:
    """The script follows an inherited CLAUDE_CONFIG_DIR and stays silent for
    a profile this repo never stowed into."""

    def _run_with_config_dir(self, home: Path, config_dir: Path | str) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir)}
        return subprocess.run(
            [str(_ENSURE_SCRIPT)], capture_output=True, text=True, check=False, env=env, timeout=15
        )

    def _make_home_with_render_script(self, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        return home

    def test_profile_with_a_base_file_is_rendered_in_place(self, tmp_path: Path) -> None:
        home = self._make_home_with_render_script(tmp_path)
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()
        (profile_dir / "settings.base.json").write_text(json.dumps({"otherKey": "profile-value"}))

        result = self._run_with_config_dir(home, profile_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert json.loads((profile_dir / "settings.json").read_text()) == {"otherKey": "profile-value"}
        assert not (home / ".claude" / "settings.json").exists()

    def test_profile_without_a_base_file_is_left_alone_and_silent(self, tmp_path: Path) -> None:
        home = self._make_home_with_render_script(tmp_path)
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()

        result = self._run_with_config_dir(home, profile_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stderr == ""
        assert not (profile_dir / "settings.json").exists()

    def test_default_profile_spelled_through_config_dir_still_reports_a_missing_base(
        self, tmp_path: Path
    ) -> None:
        home = self._make_home_with_render_script(tmp_path)

        result = self._run_with_config_dir(home, home / ".claude")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"render of {home}/.claude/settings.json failed" in result.stderr

    def test_failed_profile_render_hint_names_the_profile_and_a_working_command(
        self, tmp_path: Path
    ) -> None:
        home = self._make_home_with_render_script(tmp_path)
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()
        (profile_dir / "settings.base.json").write_text("{not valid json")

        result = self._run_with_config_dir(home, profile_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"render of {profile_dir}/settings.json failed" in result.stderr
        assert f"CLAUDE_CONFIG_DIR={profile_dir} ~/.claude/scripts/render-settings.sh" in result.stderr
        assert "./install.sh" not in result.stderr

    def test_failed_profile_render_hint_quotes_a_profile_path_with_a_space(
        self, tmp_path: Path
    ) -> None:
        home = self._make_home_with_render_script(tmp_path)
        profile_dir = tmp_path / "my profile"
        profile_dir.mkdir()
        (profile_dir / "settings.base.json").write_text("{not valid json")

        result = self._run_with_config_dir(home, profile_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        suggested_command = result.stderr.rsplit("run: ", 1)[1]
        assert shlex.split(suggested_command) == [
            f"CLAUDE_CONFIG_DIR={profile_dir}",
            "~/.claude/scripts/render-settings.sh",
        ]

    @pytest.mark.parametrize("spelling", ["trailing-slash", "symlink-alias"])
    def test_default_profile_spelled_differently_still_reports_a_missing_base(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """The profile gate compares files, not strings: a spelling of the
        default profile is the default profile, whose missing base is the
        dangling pre-migration state the hint exists to report."""
        home = self._make_home_with_render_script(tmp_path)
        default_profile = home / ".claude"
        config_dir: Path | str
        if spelling == "trailing-slash":
            # A str, not a Path: pathlib drops the trailing slash.
            config_dir = f"{default_profile}/"
        else:
            config_dir = tmp_path / "alias-of-default"
            config_dir.symlink_to(default_profile)

        result = self._run_with_config_dir(home, config_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "settings.json failed" in result.stderr
        assert "re-run install.sh from your claude-config checkout" in result.stderr

    def test_default_profile_spelled_with_a_trailing_slash_is_rendered_in_place(
        self, tmp_path: Path
    ) -> None:
        home = self._make_home_with_render_script(tmp_path)
        (home / ".claude" / "settings.base.json").write_text(json.dumps({"otherKey": "base-value"}))

        result = self._run_with_config_dir(home, f"{home / '.claude'}/")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert json.loads((home / ".claude" / "settings.json").read_text()) == {"otherKey": "base-value"}


class TestEnsureSettingsRenderFailureStatus:
    """The failure hint says what happened: a cap-kill status names the cap
    (the render printed nothing), any other status points at the render's own
    error."""

    def _make_home_with_stub_render(self, tmp_path: Path, exit_status: int) -> Path:
        home = tmp_path / "home"
        scripts_dir = home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        stub = scripts_dir / "render-settings.sh"
        stub.write_text(f"#!/bin/bash\nexit {exit_status}\n")
        stub.chmod(0o755)
        return home

    @pytest.mark.parametrize("cap_kill_status", [124, 137, 143])
    def test_cap_kill_status_names_the_cap_and_does_not_point_at_an_absent_error(
        self, tmp_path: Path, cap_kill_status: int
    ) -> None:
        home = self._make_home_with_stub_render(tmp_path, cap_kill_status)

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"did not finish within its 5s cap (exit {cap_kill_status})" in result.stderr
        assert "see the error above" not in result.stderr

    @pytest.mark.parametrize("ordinary_failure_status", [1, 2, 125])
    def test_ordinary_failure_status_points_at_the_renders_own_error(
        self, tmp_path: Path, ordinary_failure_status: int
    ) -> None:
        home = self._make_home_with_stub_render(tmp_path, ordinary_failure_status)

        result = _run_ensure_script(home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"render of {home}/.claude/settings.json failed -- see the error above" in result.stderr
        assert "5s cap" not in result.stderr


class TestRcBlockExecution:
    """The block install.sh writes into an rc file must work when a new shell
    sources it, not only contain the script's name."""

    def _make_stowed_home(self, tmp_path: Path) -> Path:
        """An isolated $HOME shaped like a stowed install: scripts and the
        hooks/_lib.sh chain symlinked in, plus a settings.base.json."""
        home = tmp_path / "home"
        claude_dir = home / ".claude"
        scripts_dir = claude_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        for script_name in ("ensure-settings-render.sh", "render-settings.sh", "_capped-for-lib.sh"):
            (scripts_dir / script_name).symlink_to(SCRIPTS_DIR / script_name)
        hooks_dir = claude_dir / "hooks"
        hooks_dir.mkdir()
        symlink_hooks_lib_chain(hooks_dir)
        (claude_dir / "settings.base.json").write_text(json.dumps({"otherKey": "base-value"}))
        return home

    @pytest.mark.parametrize("shell_name", ["bash", "zsh"])
    def test_sourcing_the_written_rc_file_renders_settings_json(
        self, tmp_path: Path, shell_name: str
    ) -> None:
        """install.sh appends the identical block to both rc files, so each
        shell's startup file is sourced by its own shell."""
        shell_path = shutil.which(shell_name)
        if shell_path is None:
            pytest.skip(f"{shell_name} not installed")
        home = self._make_stowed_home(tmp_path)
        written = _run_settings_render_rc_block(home)
        assert written.returncode == 0, f"stderr={written.stderr!r}"
        env = {**os.environ, "HOME": str(home)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        result = subprocess.run(
            [shell_path, "-c", f'. "$HOME/.{shell_name}rc"'],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=30,
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert json.loads((home / ".claude" / "settings.json").read_text()) == {"otherKey": "base-value"}
