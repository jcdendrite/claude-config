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


def _run(
    scripts_dir: Path, key: str, tmp_path: Path, claude_config_dir: Path | None = None
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)
    return subprocess.run(
        [str(scripts_dir / "config-get.sh"), key],
        capture_output=True,
        text=True,
        env=env,
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


class TestArchitectFixPlanPilotGate:
    """code-review/SKILL.md enables the architect fix-plan pilot only on a
    config-get.sh exit 0, so the exit code is the contract these pin against
    the real schema."""

    _KEY = "architect_fix_plan_pilot"

    def _config_dir(self, tmp_path: Path) -> Path:
        config_dir = tmp_path / "home" / ".claude"
        config_dir.mkdir(parents=True)
        return config_dir

    def test_fresh_home_with_no_state_file_exits_1(self, tmp_path: Path) -> None:
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        result = _run(scripts_dir, self._KEY, tmp_path)
        assert result.returncode == 1
        assert result.stdout == "false\n"

    def test_hand_written_true_row_exits_0(self, tmp_path: Path) -> None:
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        (self._config_dir(tmp_path) / "claude-config.toml").write_text(f"{self._KEY} = true\n")
        result = _run(scripts_dir, self._KEY, tmp_path)
        assert result.returncode == 0
        assert result.stdout == "true\n"

    def test_scaffolded_false_row_exits_1(self, tmp_path: Path) -> None:
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        config_dir = self._config_dir(tmp_path)
        # Explicit config-dir argument and a scrubbed env keep the scaffold
        # from writing to the invoking account's own config dir.
        scaffold = subprocess.run(
            ["bash", "-c", f'set -uo pipefail; . "{_CONFIG_SH}"; _config_scaffold "" "{config_dir}"'],
            capture_output=True,
            text=True,
            env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"},
            check=False,
        )
        assert scaffold.returncode == 0, scaffold.stderr
        assert f"{self._KEY} = false" in (config_dir / "claude-config.toml").read_text().splitlines()

        result = _run(scripts_dir, self._KEY, tmp_path)

        assert result.returncode == 1
        assert result.stdout == "false\n"

    def test_true_only_in_home_claude_dir_exits_1_when_claude_config_dir_is_another_account(
        self, tmp_path: Path
    ) -> None:
        """The pilot is per-account: a home-level file must not enable it for an
        account whose CLAUDE_CONFIG_DIR points elsewhere."""
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        (self._config_dir(tmp_path) / "claude-config.toml").write_text(f"{self._KEY} = true\n")
        account_dir = tmp_path / "acct"
        account_dir.mkdir()

        result = _run(scripts_dir, self._KEY, tmp_path, claude_config_dir=account_dir)

        assert result.returncode == 1
        assert result.stdout == "false\n"

    def test_true_in_claude_config_dir_exits_0(self, tmp_path: Path) -> None:
        scripts_dir = _isolated_scripts_dir(tmp_path, _CONFIG_KEYS_PSV.read_text())
        account_dir = tmp_path / "acct"
        account_dir.mkdir()
        (account_dir / "claude-config.toml").write_text(f"{self._KEY} = true\n")

        result = _run(scripts_dir, self._KEY, tmp_path, claude_config_dir=account_dir)

        assert result.returncode == 0
        assert result.stdout == "true\n"
