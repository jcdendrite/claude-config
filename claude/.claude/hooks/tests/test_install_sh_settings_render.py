"""Tests for install.sh's stow + render-settings.sh invocation sequence: pins
CLAUDE_CONFIG_DIR resolved to $HOME/.claude regardless of the invoking shell's
own value, that a dangling-symlink settings.json is safely replaced rather
than written through, and that a render failure aborts install.sh with a
diagnostic after the recovery tooling it must not skip.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, symlink_hooks_lib_chain

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_STOW = shutil.which("stow")


@pytest.fixture
def require_stow() -> None:
    """Skip locally when stow is absent, but fail under CI, where
    .github/workflows/tests.yml installs it: a silent skip there would drop the
    only test that runs real stow over the stale dangling symlink."""
    if _STOW is None:
        if os.environ.get("CI"):
            pytest.fail("stow missing in CI -- .github/workflows/tests.yml must install it")
        pytest.skip("stow binary not on PATH")


_STOW_START = "# INSTALL_TEST_FIXTURE: stow-adopt-ignore — start\n"
_STOW_END = "# INSTALL_TEST_FIXTURE: stow-adopt-ignore — end"

_RENDER_START = "# INSTALL_TEST_FIXTURE: render-settings-invoke — start\n"
_RENDER_END = "# INSTALL_TEST_FIXTURE: render-settings-invoke — end"

_HARDENING_START = "# INSTALL_TEST_FIXTURE: continuity-hardening — start\n"

_RC_HELPERS_START = "# INSTALL_TEST_FIXTURE: rc-block-helpers — start\n"
_RC_HELPERS_END = "# INSTALL_TEST_FIXTURE: rc-block-helpers — end"

_MANIFEST_START = "# INSTALL_TEST_FIXTURE: repo-relocation-manifest — start\n"

_REAL_SETTINGS_BASE = Path(__file__).resolve().parents[2] / "settings.base.json"


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
    call site, deliberately left unwrapped by any fixture marker -- see
    TestRcInvocationPrecedesRenderCall below), preserving their
    original relative order rather than concatenating separately-extracted
    strings that would drop it."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start : end + len(end_marker)]


def _write_stow_packages_stub(scripts_dir: Path) -> None:
    """A stub stow-packages.sh emitting only the mandatory "claude" package
    row install.sh's stow-adopt-ignore block requires (GH-849) -- not a
    symlink to the real script, which also demands a claude-skills package
    directory this fixture doesn't create."""
    stub = scripts_dir / "stow-packages.sh"
    stub.write_text("#!/usr/bin/env bash\nprintf 'claude\\t.\\n'\n")
    stub.chmod(0o755)


def _make_package(pkg_root: Path, base_content: dict) -> None:
    """A throwaway stow package mirroring this repo's real shape closely
    enough to exercise the stow + render sequence: a tracked
    settings.base.json plus the real _stow_migration_lib.sh, render-settings.sh,
    and _capped-for-lib.sh (symlinked, not reimplemented, so the test exercises
    the actual scripts under review, not a copy of them), plus a stub
    stow-packages.sh (see _write_stow_packages_stub). _capped-for-lib.sh
    sources hooks/_lib.sh (BASH_SOURCE-relative to its own package-root
    location, not the real repo's), so the hooks/_lib.sh chain must be
    present in the package too -- see symlink_hooks_lib_chain."""
    scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(SCRIPTS_DIR / "_stow_migration_lib.sh")
    (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
    (scripts_dir / "_capped-for-lib.sh").symlink_to(SCRIPTS_DIR / "_capped-for-lib.sh")
    symlink_hooks_lib_chain(pkg_root / "claude" / ".claude" / "hooks")
    _write_stow_packages_stub(scripts_dir)
    (pkg_root / "claude" / ".claude" / "settings.base.json").write_text(json.dumps(base_content))

    subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True, timeout=10)
    subprocess.run(
        [
            "git",
            "add",
            "claude/.claude/scripts",
            "claude/.claude/hooks",
            "claude/.claude/settings.base.json",
        ],
        cwd=pkg_root,
        check=True,
        timeout=10,
    )


def _make_render_repo(repo_dir: Path) -> Path:
    """A $REPO_DIR holding only what the render-settings-invoke block and the
    blocks around it source: render-settings.sh, _capped-for-lib.sh, the
    hooks/_lib.sh chain that _capped-for-lib.sh needs, and a stub
    relocate-claude-config.sh. settings.base.json is not here: the render
    reads it from $HOME/.claude, where stow places it (see _make_home)."""
    claude_dir = repo_dir / "claude" / ".claude"
    scripts_dir = claude_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "render-settings.sh").symlink_to(SCRIPTS_DIR / "render-settings.sh")
    (scripts_dir / "_capped-for-lib.sh").symlink_to(SCRIPTS_DIR / "_capped-for-lib.sh")
    (scripts_dir / "relocate-claude-config.sh").write_text("#!/bin/bash\nexit 0\n")
    symlink_hooks_lib_chain(claude_dir / "hooks")
    return repo_dir


def _make_home(home: Path, base_content: dict | None = None) -> Path:
    """An isolated $HOME/.claude, holding settings.base.json as stow would
    link it when base_content is given."""
    (home / ".claude").mkdir(parents=True)
    if base_content is not None:
        (home / ".claude" / "settings.base.json").write_text(json.dumps(base_content))
    return home


def _run_blocks(script_body: str, home: Path, repo_dir: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "REPO_DIR": str(repo_dir)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", "set -e\n" + script_body, "run_blocks"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )


def _run_stow_and_render(pkg_root: Path, home: Path) -> subprocess.CompletedProcess:
    """Runs the real extracted stow-adopt-ignore and render-settings-invoke
    blocks back to back (the stow block, then the render block), against an
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
        timeout=30,
    )


@pytest.mark.usefixtures("require_stow")
class TestDanglingSymlinkedSettingsUpgrade:
    def test_dangling_symlink_is_replaced_by_a_fresh_render(
        self, tmp_path: Path
    ) -> None:
        """`$HOME/.claude/settings.json` is a symlink into a
        `claude/.claude/settings.json` path that does not exist. The stow +
        render sequence must leave settings.json as a plain, regenerated file
        matching a fresh render of settings.base.json, and must not write
        anything through the dangling symlink's target."""
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
    settings.base.json."""

    def test_render_invoke_block_exits_non_zero_when_base_is_missing(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render.
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode != 0, (
            "a failed render-settings.sh must abort the extracted "
            f"render-settings-invoke block; got exit 0, stderr={result.stderr!r}"
        )
        assert "settings.base.json not found" in result.stderr, (
            "the abort must come from the render's own diagnostic, not from a "
            f"missing library; stderr={result.stderr!r}"
        )
        assert not (home / ".claude" / "settings.json").exists()

    def test_failure_names_the_step_the_exit_status_and_what_was_skipped(self, tmp_path: Path) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 1
        assert "[install] error: render-settings.sh failed (exit 1)" in result.stderr
        assert "stopped before" in result.stderr
        assert "Fix the error above, then re-run ./install.sh" in result.stderr

    def test_failure_without_a_settings_json_says_no_deny_rules_or_hooks_are_active(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 1
        assert "no deny rules or hooks are active until the render succeeds" in result.stderr

    def test_failure_over_an_existing_settings_json_does_not_claim_the_floor_is_missing(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(
            json.dumps({"permissions": {"deny": ["Bash(sudo *)"]}})
        )

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 1
        assert "no deny rules or hooks are active" not in result.stderr

    @pytest.mark.parametrize("cap_kill_status", [124, 137, 143])
    def test_cap_kill_status_is_reported_as_the_cap_firing_without_pointing_at_an_absent_error(
        self, tmp_path: Path, cap_kill_status: int
    ) -> None:
        """A render killed by the timeout wrapper exits 124, 137, or 143 with
        no output of its own, so install.sh must say what happened and must
        not send the user to an error above that was never printed."""
        repo_dir = _make_render_repo(tmp_path / "repo")
        stub = repo_dir / "claude" / ".claude" / "scripts" / "render-settings.sh"
        stub.unlink()
        stub.write_text(f"#!/bin/bash\nexit {cap_kill_status}\n")
        stub.chmod(0o755)
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == cap_kill_status
        assert f"did not finish within its 5s cap (exit {cap_kill_status})" in result.stderr
        assert "Fix the error above" not in result.stderr
        assert "Re-run ./install.sh" in result.stderr

    @pytest.mark.parametrize("cap_kill_status", [124, 137, 143])
    def test_cap_kill_without_a_settings_json_says_no_deny_rules_or_hooks_are_active(
        self, tmp_path: Path, cap_kill_status: int
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        stub = repo_dir / "claude" / ".claude" / "scripts" / "render-settings.sh"
        stub.unlink()
        stub.write_text(f"#!/bin/bash\nexit {cap_kill_status}\n")
        stub.chmod(0o755)
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == cap_kill_status
        assert "no deny rules or hooks are active until the render succeeds" in result.stderr

    def test_success_prints_the_rendered_path(self, tmp_path: Path) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert f"[install] rendered {home}/.claude/settings.json" in result.stdout
        assert "warning" not in result.stderr

    def test_diverged_config_dir_holding_a_base_file_warns_that_only_the_default_profile_was_rendered(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})
        other_profile = tmp_path / "other-profile"
        other_profile.mkdir()
        (other_profile / "settings.base.json").write_text("{}")

        result = _run_blocks(
            _extract_block(_RENDER_START, _RENDER_END),
            home,
            repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": str(other_profile)},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (home / ".claude" / "settings.json").exists()
        assert not (other_profile / "settings.json").exists()
        assert f"CLAUDE_CONFIG_DIR={other_profile} ~/.claude/scripts/render-settings.sh" in result.stderr

    def test_diverged_config_dir_warning_quotes_a_profile_path_with_a_space(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})
        other_profile = tmp_path / "other profile"
        other_profile.mkdir()
        (other_profile / "settings.base.json").write_text("{}")

        result = _run_blocks(
            _extract_block(_RENDER_START, _RENDER_END),
            home,
            repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": str(other_profile)},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        suggested_command = result.stderr.rsplit("run: ", 1)[1]
        assert shlex.split(suggested_command) == [
            f"CLAUDE_CONFIG_DIR={other_profile}",
            "~/.claude/scripts/render-settings.sh",
        ]

    def test_diverged_config_dir_without_a_base_file_does_not_warn(self, tmp_path: Path) -> None:
        """The suggested render command would fail for a profile that holds no
        settings.base.json, and ensure-settings-render.sh stays silent for
        the same profile."""
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})
        other_profile = tmp_path / "other-profile"
        other_profile.mkdir()

        result = _run_blocks(
            _extract_block(_RENDER_START, _RENDER_END),
            home,
            repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": str(other_profile)},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "warning" not in result.stderr

    def test_config_dir_spelling_the_default_profile_differently_does_not_warn(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})
        (tmp_path / "alias-of-default").symlink_to(home / ".claude")

        result = _run_blocks(
            _extract_block(_RENDER_START, _RENDER_END),
            home,
            repo_dir,
            extra_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "alias-of-default")},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "warning" not in result.stderr


class TestRecoveryToolingPrecedesRenderAbort:
    """The relocation manifest and wrapper are the tooling a broken install
    needs, so a render abort must not skip them."""

    def test_manifest_and_relocate_wrapper_exist_after_a_render_abort(self, tmp_path: Path) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".local" / "bin").mkdir(parents=True)

        result = _run_blocks(_extract_span(_MANIFEST_START, _RENDER_END), home, repo_dir)

        assert result.returncode != 0, "the render step must still abort"
        assert (home / ".claude-config-source").read_text().strip() == str(repo_dir)
        assert os.access(home / ".local" / "bin" / "relocate-claude-config", os.X_OK)


class TestRelocateWrapperCopyFailureDoesNotBlockTheRender:
    """The wrapper is recovery tooling, not the security floor: a failed copy
    warns, and the render that delivers permissions.deny still runs."""

    def test_uncreatable_wrapper_destination_warns_and_the_render_still_runs(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})
        # No $HOME/.local/bin, so `install` cannot create the destination.
        assert not (home / ".local").exists()

        result = _run_blocks(_extract_span(_MANIFEST_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "warning: could not install relocate-claude-config" in result.stderr
        assert json.loads((home / ".claude" / "settings.json").read_text()) == {"otherKey": "base-value"}


class TestStraySettingsJsonIsLeftInPlace:
    """A regular claude/.claude/settings.json in the checkout is gitignored
    and left where it is; install.sh never moves or deletes it."""

    def test_stray_file_the_dangling_symlink_resolves_to_is_carried_into_the_render_and_left_untouched(
        self, tmp_path: Path
    ) -> None:
        """The symlink at ~/.claude/settings.json resolves to the stray, so the
        render reads it: its app-written keys carry forward, and the keys the
        render drops are named. The stray itself keeps its exact bytes."""
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home", {"otherKey": "base-value"})
        stray = repo_dir / "claude" / ".claude" / "settings.json"
        stray_text = json.dumps({"theme": "dark", "env": {"KEEP_ME": "1"}})
        stray.write_text(stray_text)
        (home / ".claude" / "settings.json").symlink_to(stray)

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rendered = home / ".claude" / "settings.json"
        assert not rendered.is_symlink()
        assert json.loads(rendered.read_text()) == {"otherKey": "base-value", "theme": "dark"}
        assert "carried forward top-level keys: theme" in result.stderr
        assert "dropped top-level keys: env" in result.stderr
        assert stray.read_text() == stray_text


class TestFirstRenderFromADanglingSymlinkedSettingsJson:
    """A dangling-symlink settings.json has no content to carry forward, so
    the first render is exactly base. This pins that outcome as a decision
    rather than an accident (see
    docs/design-decisions/settings-base-ships-no-session-ui-keys.md)."""

    def test_dangling_symlink_renders_exactly_the_real_base_with_nothing_dropped(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home")
        shutil.copy(_REAL_SETTINGS_BASE, home / ".claude" / "settings.base.json")
        # The symlink targets a tracked-file path that does not exist.
        (home / ".claude" / "settings.json").symlink_to(repo_dir / "claude" / ".claude" / "settings.json")

        result = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "dropped" not in result.stderr
        rendered = json.loads((home / ".claude" / "settings.json").read_text())
        real_base = json.loads(_REAL_SETTINGS_BASE.read_text())
        assert rendered == real_base

    def test_value_chosen_after_the_first_render_persists_across_later_renders(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_render_repo(tmp_path / "repo")
        home = _make_home(tmp_path / "home")
        shutil.copy(_REAL_SETTINGS_BASE, home / ".claude" / "settings.base.json")
        first = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)
        assert first.returncode == 0, f"stderr={first.stderr!r}"
        rendered_path = home / ".claude" / "settings.json"
        rendered = json.loads(rendered_path.read_text())
        rendered["model"] = "opus"
        rendered_path.write_text(json.dumps(rendered))

        second = _run_blocks(_extract_block(_RENDER_START, _RENDER_END), home, repo_dir)

        assert second.returncode == 0, f"stderr={second.stderr!r}"
        assert json.loads(rendered_path.read_text())["model"] == "opus"


class TestAbortsOnRenderFailure:
    def test_continuity_hardening_runs_even_when_render_fails(self, tmp_path: Path) -> None:
        """Pins install.sh's ordering: continuity-hardening (chmod 700
        ~/.claude, chmod 600 ~/.claude.json) sits ahead of the render step so
        it always runs, even when a subsequent render failure aborts the
        rest of the script."""
        repo_dir = _make_render_repo(tmp_path / "repo")
        # No settings.base.json written -- render-settings.sh's own
        # missing-base check fails the render, after hardening has run.
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        claude_json = home / ".claude.json"
        claude_json.write_text("{}")
        claude_json.chmod(0o664)

        result = _run_blocks(_extract_span(_HARDENING_START, _RENDER_END), home, repo_dir)

        assert result.returncode != 0, "the render step must still abort"
        assert "settings.base.json not found" in result.stderr
        assert oct((home / ".claude").stat().st_mode)[-3:] == "700"
        assert oct(claude_json.stat().st_mode)[-3:] == "600"


class TestRcInvocationPrecedesRenderCall:
    """A failed first render must still leave the repairing rc block installed,
    so the rc-invocation function runs before render-settings-invoke. The
    relevant INSTALL_TEST_FIXTURE markers wrap only the function
    *definitions*; the span test below runs them in file order, including the
    bare call site between them."""

    def test_rc_block_is_installed_even_when_the_render_fails(self, tmp_path: Path) -> None:
        """Runs the real rc-block-helpers, settings-render-rc, and
        render-settings-invoke blocks concatenated in their original file
        order -- including the bare ensure_settings_render call site between
        them -- against a base file that forces the render to fail. The rc
        block must still be installed despite the subsequent abort: a fresh
        install whose first render fails must not also lose its own repair
        mechanism."""
        home = tmp_path / "home"
        home.mkdir()
        # No settings.base.json -- render-settings.sh's own missing-base
        # check fails the render.
        repo_dir = _make_render_repo(tmp_path / "repo")

        result = _run_blocks(_extract_span(_RC_HELPERS_START, _RENDER_END), home, repo_dir)

        assert result.returncode != 0, "the render step must still abort"
        assert "settings.base.json not found" in result.stderr
        bashrc = (home / ".bashrc").read_text()
        assert "ensure-settings-render.sh" in bashrc, (
            f"the rc block must be installed despite the render failure; stderr={result.stderr!r}"
        )
        assert not (home / ".claude" / "settings.json").exists()
