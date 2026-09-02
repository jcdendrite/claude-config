"""Tests for ensure-account-dir.sh.

Creates the active account's handoffs/ or briefs/ directory, exiting
non-zero on any resolution failure or unrecognized argument. Mirrors
test_handoff_record_conversion.py's subprocess-against-isolated-env
convention -- see that file for the shared shape.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "ensure-account-dir.sh"


def _run(env: dict, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _env_with_config_dir(config_dir: Path) -> dict:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def test_handoffs_argument_creates_handoffs_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(_env_with_config_dir(config_dir), ["handoffs"], cwd=tmp_path)

    assert result.returncode == 0
    assert (config_dir / "handoffs").is_dir()


def test_briefs_argument_creates_briefs_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(_env_with_config_dir(config_dir), ["briefs"], cwd=tmp_path)

    assert result.returncode == 0
    assert (config_dir / "briefs").is_dir()


def test_invalid_name_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(_env_with_config_dir(config_dir), ["plans"], cwd=tmp_path)

    assert result.returncode != 0
    assert list(config_dir.iterdir()) == []


def test_empty_argument_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(_env_with_config_dir(config_dir), [""], cwd=tmp_path)

    assert result.returncode != 0
    assert list(config_dir.iterdir()) == []


def test_traversal_shaped_argument_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    """Proves a path-traversal-shaped argument is rejected, not interpolated
    into mkdir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(_env_with_config_dir(config_dir), ["../etc"], cwd=tmp_path)

    assert result.returncode != 0
    assert list(config_dir.iterdir()) == []
    assert not (tmp_path / "etc").exists()


def test_extra_argument_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    """A second positional argument is rejected, not silently ignored --
    the script's own header comment documents exactly one argument."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(
        _env_with_config_dir(config_dir), ["handoffs", "extra-garbage-arg"], cwd=tmp_path
    )

    assert result.returncode != 0
    assert list(config_dir.iterdir()) == []


def test_no_argument_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(_env_with_config_dir(config_dir), [], cwd=tmp_path)

    assert result.returncode != 0
    assert list(config_dir.iterdir()) == []


def test_relative_config_dir_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    """Proves the capture-and-check on the config_dir line is what actually
    rejects a relative CLAUDE_CONFIG_DIR, not the case-statement argument
    validation below it."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = "relative/config/dir"

    result = _run(env, ["handoffs"], cwd=tmp_path)

    assert result.returncode != 0
    assert not (tmp_path / "relative").exists()


def test_unresolvable_home_exits_nonzero_and_creates_nothing(tmp_path: Path) -> None:
    """Proves the same capture-and-check rejects the unset-HOME failure mode
    of _lib_config_dir (see _lib.sh)."""
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env["HOME"] = ""

    result = _run(env, ["handoffs"], cwd=tmp_path)

    assert result.returncode != 0
    assert not (tmp_path / "handoffs").exists()
