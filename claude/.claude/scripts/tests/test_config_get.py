"""Tests for config-get.sh's own exit-code contract.

config-get.sh sources _config.sh via a path relative to its own location
(`$(dirname "$0")/../hooks/_config.sh`), so isolating its schema file needs
a matching directory layout: a `scripts/` dir holding a config-get.sh
symlink, and a sibling `hooks/` dir holding the _config.sh and
config-keys.psv it resolves against.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import HOOKS_DIR, SCRIPTS_DIR

_CONFIG_GET_SH = SCRIPTS_DIR / "config-get.sh"
_CONFIG_SH = HOOKS_DIR / "_config.sh"
_CONFIG_KEYS_PSV = HOOKS_DIR / "config-keys.psv"


def _isolated_scripts_dir(tmp_path: Path, schema_content: str) -> Path:
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "_config.sh").symlink_to(_CONFIG_SH)
    (hooks_dir / "config-keys.psv").write_text(schema_content)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "config-get.sh").symlink_to(_CONFIG_GET_SH)
    return scripts_dir


def _run(scripts_dir: Path, key: str, tmp_path: Path) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        [str(scripts_dir / "config-get.sh"), key],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        check=False,
    )


class TestExitCodeContract:
    def test_known_key_default_resolves_exit_1(self, tmp_path: Path) -> None:
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        result = _run(scripts_dir, "worktree_required", tmp_path)
        assert result.returncode == 1
        assert result.stdout == "false\n"

    def test_row_truncated_after_resolution_column_returns_exit_3_not_a_silent_enable(
        self, tmp_path: Path
    ) -> None:
        """Security regression: config-get.sh must not treat _config_value's
        new exit 4 (row present but truncated after an earlier column) as an
        unrecognized status that falls through to printing an empty VALUE and
        exiting 0 -- "" != "false" would silently report an enforcement-
        critical key as enabled. worktree_required is the key this matters
        most for: its row is the one real row whose
        legacy-probe-on-resolution-failure column is `true`, so this is the
        exact truncation shape _config.sh's own exit-4 fix guards against."""
        lines = [
            "worktree_required|bool|false|config-dir-or-home" if line.startswith("worktree_required|") else line
            for line in _CONFIG_KEYS_PSV.read_text().splitlines()
        ]
        scripts_dir = _isolated_scripts_dir(tmp_path, "\n".join(lines) + "\n")

        result = _run(scripts_dir, "worktree_required", tmp_path)

        assert result.returncode == 3
        assert result.stdout == ""
        assert "worktree_required" in result.stderr

    def test_unknown_key_returns_exit_2(self, tmp_path: Path) -> None:
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        result = _run(scripts_dir, "not_a_real_key", tmp_path)
        assert result.returncode == 2
