"""Tests for autonomous-shipping-active.sh.

This script is a thin forwarder over _lib_autonomous_shipping_active
(already exhaustively tested at hooks/tests/test_lib.py:1204-1344) — these
tests cover only the wrapper's own behavior: resolving the repo root,
forwarding the function's exit code unchanged, and failing loudly outside a
git repo. They deliberately do not re-exercise every branch inside
_lib_autonomous_shipping_active itself.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import _init_repo

_SCRIPT = Path(__file__).parent.parent / "autonomous-shipping-active.sh"


def _run_script(repo: Path, config_dir: Path, *, home: Path) -> subprocess.CompletedProcess:
    # HOME is pinned alongside CLAUDE_CONFIG_DIR: _lib_autonomous_shipping_active
    # unions the resolved config dir with the literal $HOME/.claude sentinel, so
    # leaving the contributor's real $HOME in place would let this machine's own
    # ~/.claude/autonomous-shipping-required leak into every "absent" case.
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir), "HOME": str(home)}
    return subprocess.run(
        [str(_SCRIPT)],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestAutonomousShippingActive:
    def test_exit_zero_when_machine_sentinel_present_and_no_optout(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "autonomous-shipping-required").touch()

        result = _run_script(repo, config_dir, home=home)

        assert result.returncode == 0

    def test_nonzero_when_machine_sentinel_absent(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        result = _run_script(repo, config_dir, home=home)

        assert result.returncode != 0

    def test_repo_optout_overrides_present_machine_sentinel(self, tmp_path: Path) -> None:
        """Regression guard: a re-derived existence-only check (`test -f
        .../autonomous-shipping-required`) would report active here,
        ignoring the repo's own opt-out — only delegating to
        _lib_autonomous_shipping_active catches that. Exit 1 specifically,
        matching the function's own optout return value."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "autonomous-shipping-optout").touch()
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "autonomous-shipping-required").touch()

        result = _run_script(repo, config_dir, home=home)

        assert result.returncode == 1

    def test_nonzero_with_stderr_message_when_not_inside_a_git_repo(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "not-a-repo"  # deliberately not a git repository
        not_a_repo.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "autonomous-shipping-required").touch()

        result = _run_script(not_a_repo, config_dir, home=home)

        assert result.returncode != 0
        assert result.stderr.strip() != ""
