"""Tests for install.sh's stow + render-settings.sh invocation sequence: pins
that render-settings.sh runs after the stow, with CLAUDE_CONFIG_DIR resolved
to $HOME/.claude regardless of the invoking shell's own value, and that a
pre-migration symlinked settings.json is safely replaced rather than written
through.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_STOW = shutil.which("stow")

_STOW_START = "# INSTALL_TEST_FIXTURE: stow-adopt-ignore — start\n"
_STOW_END = "# INSTALL_TEST_FIXTURE: stow-adopt-ignore — end"

_RENDER_START = "# INSTALL_TEST_FIXTURE: render-settings-invoke — start\n"
_RENDER_END = "# INSTALL_TEST_FIXTURE: render-settings-invoke — end"

_HARDENING_START = "# INSTALL_TEST_FIXTURE: continuity-hardening — start\n"
_HARDENING_END = "# INSTALL_TEST_FIXTURE: continuity-hardening — end"

_RC_HELPERS_START = "# INSTALL_TEST_FIXTURE: rc-block-helpers — start\n"
_RC_HELPERS_END = "# INSTALL_TEST_FIXTURE: rc-block-helpers — end"


def _extract_block(start_marker: str, end_marker: str) -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files -- syntax-matching would silently pick up an
    edited invocation, or miss one, on reordering."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(start_marker) : end]


def _extract_span(start_marker: str, end_marker: str) -> str:
    """Return install.sh's raw text from start_marker's own line through the
    end of end_marker's line, inclusive of both markers and everything
    between them. Unlike _extract_block, which returns only the content
    *between* one marker pair, this spans across several fixture blocks plus
    whatever bare statements sit between them (e.g. the ensure_settings_render
    call site, which M2 deliberately leaves unwrapped by any fixture marker
    -- see TestRcInvocationPrecedesRenderCall below), preserving their
    original relative order rather than concatenating separately-extracted
    strings that would drop it."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start : end + len(end_marker)]


def _make_package(pkg_root: Path, base_content: dict) -> None:
    """A throwaway stow package mirroring this repo's real shape closely
    enough to exercise the stow + render sequence: a tracked
    settings.base.json plus the real _stow_migration_lib.sh and
    render-settings.sh (symlinked, not reimplemented, so the test exercises
    the actual scripts under review, not a copy of them)."""
    scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(SCRIPTS_DIR / "_stow_migration_lib.sh")
    (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
    (pkg_root / "claude" / ".claude" / "settings.base.json").write_text(json.dumps(base_content))

    subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
    subprocess.run(
        ["git", "add", "claude/.claude/scripts", "claude/.claude/settings.base.json"],
        cwd=pkg_root,
        check=True,
    )


def _run_stow_and_render(pkg_root: Path, home: Path) -> subprocess.CompletedProcess:
    """Runs the real extracted stow-adopt-ignore and render-settings-invoke
    blocks back to back, exactly as install.sh sequences them, against an
    isolated $HOME. A decoy CLAUDE_CONFIG_DIR is set in the subprocess env to
    prove the render invocation pins its own value rather than inheriting
    the shell's."""
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        'cd "$1"\n'
        + _extract_block(_STOW_START, _STOW_END)
        + _extract_block(_RENDER_START, _RENDER_END)
    )
    return subprocess.run(
        ["bash", "-c", script, "run_stow_and_render", str(pkg_root)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "REPO_DIR": str(pkg_root),
            "CLAUDE_CONFIG_DIR": str(home / "decoy-config-dir"),
        },
    )


@pytest.mark.skipif(_STOW is None, reason="stow binary not on PATH")
class TestPreMigrationSymlinkedSettingsUpgrade:
    def test_dangling_pre_rename_symlink_is_replaced_by_a_fresh_render(
        self, tmp_path: Path
    ) -> None:
        """The exact state every existing stowed machine is in right after
        pulling the settings.json -> settings.base.json rename:
        `$HOME/.claude/settings.json` is a real symlink into the old,
        now-renamed `claude/.claude/settings.json` path. The stow + render
        sequence must leave settings.json as a plain, regenerated file
        matching a fresh render of settings.base.json, and must not write
        anything through the stale symlink's original target."""
        pkg_root = tmp_path / "pkg"
        pkg_root.mkdir()
        base_content = {"otherKey": "base-value"}
        _make_package(pkg_root, base_content)

        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        old_target = pkg_root / "claude" / ".claude" / "settings.json"
        (home / ".claude" / "settings.json").symlink_to(old_target)

        result = _run_stow_and_render(pkg_root, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = home / ".claude" / "settings.json"
        assert rendered.exists() and not rendered.is_symlink(), (
            "settings.json must end up a plain regenerated file, not a "
            f"symlink; stow+render output: {result.stderr!r}"
        )
        assert json.loads(rendered.read_text()) == base_content
        assert not old_target.exists(), (
            "nothing must be written through the stale symlink's original "
            f"target; found content at {old_target}"
        )


class TestRenderInvokeBlockAbortsOnMissingBase:
    """The render-abort invariant doesn't depend on stow at all -- runs only
    the render-settings-invoke block against a $REPO_DIR with no
    settings.base.json, so unlike TestAbortsOnRenderFailure below this has no
    skip condition."""

    def test_render_invoke_block_exits_non_zero_when_base_is_missing(
        self, tmp_path: Path
    ) -> None:
        repo_dir = tmp_path / "repo"
        scripts_dir = repo_dir / "claude" / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render.

        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        script = "set -e\n" + _extract_block(_RENDER_START, _RENDER_END)
        result = subprocess.run(
            ["bash", "-c", script, "run_render_invoke"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(home), "REPO_DIR": str(repo_dir)},
        )

        assert result.returncode != 0, (
            "a failed render-settings.sh must abort the extracted "
            f"render-settings-invoke block; got exit 0, stderr={result.stderr!r}"
        )
        assert not (home / ".claude" / "settings.json").exists()


@pytest.mark.skipif(_STOW is None, reason="stow binary not on PATH")
class TestAbortsOnRenderFailure:
    def test_render_failure_aborts_the_sequence_non_zero(self, tmp_path: Path) -> None:
        """install.sh's render-settings-invoke block is a bare, unguarded
        command under `set -e`: a render failure must abort the sequence,
        not warn-and-continue like most other install.sh steps. Pins that
        propagation through the real extracted blocks, not just
        render-settings.sh's own exit code in isolation."""
        pkg_root = tmp_path / "pkg"
        pkg_root.mkdir()
        scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "_stow_migration_lib.sh").symlink_to(
            SCRIPTS_DIR / "_stow_migration_lib.sh"
        )
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render.
        subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
        subprocess.run(["git", "add", "claude/.claude/scripts"], cwd=pkg_root, check=True)

        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_stow_and_render(pkg_root, home)

        assert result.returncode != 0, (
            "a failed render-settings.sh must abort the extracted install.sh "
            f"sequence; got exit 0, stderr={result.stderr!r}"
        )
        assert not (home / ".claude" / "settings.json").exists()

    def test_continuity_hardening_runs_even_when_render_fails(self, tmp_path: Path) -> None:
        """Pins install.sh's ordering: continuity-hardening (chmod 700
        ~/.claude, chmod 600 ~/.claude.json) sits ahead of the render step so
        it always runs, even when a subsequent render failure aborts the
        rest of the script."""
        pkg_root = tmp_path / "pkg"
        pkg_root.mkdir()
        scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "_stow_migration_lib.sh").symlink_to(
            SCRIPTS_DIR / "_stow_migration_lib.sh"
        )
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render, after hardening has run.
        subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
        subprocess.run(["git", "add", "claude/.claude/scripts"], cwd=pkg_root, check=True)

        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        claude_json = home / ".claude.json"
        claude_json.write_text("{}")
        claude_json.chmod(0o664)

        script = (
            f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
            "set -e\n"
            'cd "$1"\n'
            + _extract_block(_STOW_START, _STOW_END)
            + _extract_block(_HARDENING_START, _HARDENING_END)
            + _extract_block(_RENDER_START, _RENDER_END)
        )
        result = subprocess.run(
            ["bash", "-c", script, "run_stow_harden_and_render", str(pkg_root)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
        )

        assert result.returncode != 0, "the render step must still abort"
        assert oct((home / ".claude").stat().st_mode)[-3:] == "700"
        assert oct(claude_json.stat().st_mode)[-3:] == "600"


class TestRcInvocationPrecedesRenderCall:
    """M2's acceptance criterion: the renamed rc-invocation function's call
    site sits immediately before render-settings-invoke -- adjacency, not
    merely "somewhere above" -- so a failed first render still gets the
    repairing rc block installed. The relevant INSTALL_TEST_FIXTURE markers
    wrap only the function *definitions*; the call site itself is found by
    text search, not by reusing a definition fixture's own marker."""

    def test_call_site_sits_immediately_before_the_render_invoke_marker(self) -> None:
        install_text = _INSTALL_SH.read_text()
        call_needle = "\nensure_settings_render\n"
        call_index = install_text.index(call_needle)
        marker_index = install_text.index(_RENDER_START)

        assert call_index < marker_index, (
            "ensure_settings_render's call site must sit before the "
            "render-settings-invoke start marker"
        )

        between = install_text[call_index + len(call_needle) : marker_index]
        non_comment_lines = [
            line for line in between.splitlines() if line.strip() and not line.strip().startswith("#")
        ]
        assert not non_comment_lines, (
            "no statement other than the call itself may sit between "
            f"ensure_settings_render and render-settings-invoke; found {non_comment_lines!r}"
        )

    def test_rc_block_is_installed_even_when_the_render_fails(self, tmp_path: Path) -> None:
        """Runs the real rc-block-helpers, settings-render-rc, and
        render-settings-invoke blocks concatenated in their original file
        order -- including the bare ensure_settings_render call site between
        them -- against a base file that forces the render to fail. The rc
        block must still be installed despite the subsequent abort, which is
        the whole point of M2's reordering: a fresh install whose first
        render fails must not also lose its own repair mechanism."""
        home = tmp_path / "home"
        home.mkdir()
        repo_dir = tmp_path / "repo"
        scripts_dir = repo_dir / "claude" / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
        # No settings.base.json -- render-settings.sh's own missing-base
        # check fails the render.

        script = "set -e\n" + _extract_span(_RC_HELPERS_START, _RENDER_END)
        result = subprocess.run(
            ["bash", "-c", script, "run_rc_block_then_render"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(home), "REPO_DIR": str(repo_dir)},
        )

        assert result.returncode != 0, "the render step must still abort"
        bashrc = (home / ".bashrc").read_text()
        assert "ensure-settings-render.sh" in bashrc, (
            f"the rc block must be installed despite the render failure; stderr={result.stderr!r}"
        )
        assert not (home / ".claude" / "settings.json").exists()
