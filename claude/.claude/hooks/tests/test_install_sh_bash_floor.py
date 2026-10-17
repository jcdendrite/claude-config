"""Tests for install.sh's bash-floor-check block -- the earliest step of
install.sh, which sources `_config.sh` (whose first command is the bash floor
check) and ends the run with status 1 when the running bash is below the floor.

The block is extracted by its INSTALL_TEST_FIXTURE markers and run with `$0` set
to a tmp repo's install.sh, so the block's repo-relative `_config.sh` path lands
on a stub or a copy there. The harness adds no `set -e` and ends the script with a
sentinel line, so only the block's own `|| exit 1` can keep the sentinel from
printing. `HOME` is an empty tmp directory in every case, to prove the block
touches nothing under it.
"""
from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT

from .test_bash_floor import SUB_FLOOR_BASH, exact_output_env, requires_sub_floor_bash

_INSTALL_SH = REPO_ROOT / "install.sh"
_REAL_CONFIG_SH = HOOKS_DIR / "_config.sh"
_CONFIG_SH_REPO_PATH = Path("claude") / ".claude" / "hooks" / "_config.sh"
_BASH = shutil.which("bash") or "bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: bash-floor-check — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: bash-floor-check — end"

_STUB_MARKER = "stub _config.sh was sourced"
_AFTER_BLOCK_SENTINEL = "reached the line after the block"


def _extract_bash_floor_check_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(_FIXTURE_START) : end]


def _make_tmp_repo(tmp_path: Path) -> Path:
    tmp_repo = tmp_path / "repo"
    (tmp_repo / _CONFIG_SH_REPO_PATH.parent).mkdir(parents=True)
    return tmp_repo


def _write_config_sh(tmp_repo: Path, text: str) -> None:
    (tmp_repo / _CONFIG_SH_REPO_PATH).write_text(text)


def _config_sh_with_floor_99_0() -> str:
    text = _REAL_CONFIG_SH.read_text()
    for constant, value in (("_BASH_FLOOR_MAJOR=5", "_BASH_FLOOR_MAJOR=99"), ("_BASH_FLOOR_MINOR=2", "_BASH_FLOOR_MINOR=0")):
        assert text.count(constant) == 1, f"{constant} not found exactly once in {_REAL_CONFIG_SH}"
        text = text.replace(constant, value)
    return text


def _make_uname_stub_dir(tmp_path: Path, *, prints: str | None) -> Path:
    """A directory holding a `uname` that prints `prints`, or exits 1 when `prints` is None."""
    stub_dir = tmp_path / "uname-stub"
    stub_dir.mkdir()
    stub = stub_dir / "uname"
    stub.write_text("#!/bin/sh\nexit 1\n" if prints is None else f"#!/bin/sh\necho {prints}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub_dir


def _run_block(
    tmp_path: Path, tmp_repo: Path, *, uname_stub_dir: Path | None = None, bash: str = _BASH
) -> tuple[subprocess.CompletedProcess, Path]:
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in exact_output_env().items() if k != "CLAUDE_CONFIG_DIR"}
    env["HOME"] = str(home)
    if uname_stub_dir is not None:
        env["PATH"] = f"{uname_stub_dir}:{env['PATH']}"
    script = _extract_bash_floor_check_block() + f"\necho '{_AFTER_BLOCK_SENTINEL}'\n"
    result = subprocess.run(
        [bash, "-c", script, str(tmp_repo / "install.sh")],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_repo,
        env=env,
        timeout=10,
    )
    return result, home


def _running_bash_identity(bash: str = _BASH) -> tuple[str, str]:
    result = subprocess.run(
        [bash, "-c", 'printf "%s\\n%s\\n" "$BASH_VERSION" "$BASH"'],
        capture_output=True,
        text=True,
        check=True,
    )
    version, bash_path = result.stdout.splitlines()
    return version, bash_path


class TestBashFloorCheckBlock:
    def test_a_failing_config_sh_source_ends_the_run_with_status_1_and_its_own_stderr(self, tmp_path: Path) -> None:
        """The stub returns 7, so exiting 1 with no sentinel proves the block's own `|| exit 1`."""
        assert (REPO_ROOT / _CONFIG_SH_REPO_PATH).is_file(), "the block's repo-relative path must name a real _config.sh"
        tmp_repo = _make_tmp_repo(tmp_path)
        _write_config_sh(tmp_repo, f"printf '%s' '{_STUB_MARKER}' >&2\nreturn 7\n")

        result, home = _run_block(tmp_path, tmp_repo)

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stderr == _STUB_MARKER
        assert _AFTER_BLOCK_SENTINEL not in result.stdout
        assert list(home.iterdir()) == []

    @pytest.mark.parametrize(
        ("uname_prints", "present", "absent"),
        [
            pytest.param("Darwin", ("brew install bash", "--prefix"), ("Homebrew on Linux", "source build"), id="darwin"),
            pytest.param("Linux", ("Homebrew on Linux", "source build"), ("brew install bash", "--prefix"), id="linux"),
            pytest.param(
                None, ("Homebrew on Linux", "source build"), ("brew install bash", "--prefix"), id="uname-fails-falls-to-other"
            ),
        ],
    )
    def test_below_the_floor_prints_only_the_matching_platform_section(
        self, tmp_path: Path, uname_prints: str | None, present: tuple[str, ...], absent: tuple[str, ...]
    ) -> None:
        tmp_repo = _make_tmp_repo(tmp_path)
        _write_config_sh(tmp_repo, _config_sh_with_floor_99_0())
        uname_stub_dir = _make_uname_stub_dir(tmp_path, prints=uname_prints)
        running_version, running_bash = _running_bash_identity()

        result, home = _run_block(tmp_path, tmp_repo, uname_stub_dir=uname_stub_dir)

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _AFTER_BLOCK_SENTINEL not in result.stdout
        common_content = (
            "99.0",
            running_version,
            running_bash,
            'README.md § "Requirements"',
            "./install.sh",
            "from the repo root",
        )
        for expected in common_content:
            assert expected in result.stderr, f"{expected!r} missing from stderr: {result.stderr!r}"
        for expected in present:
            assert expected in result.stderr, f"{expected!r} missing from stderr: {result.stderr!r}"
        for unexpected in absent:
            assert unexpected not in result.stderr, f"{unexpected!r} leaked into stderr: {result.stderr!r}"
        assert list(home.iterdir()) == []

    def test_at_the_real_floor_passes_and_names_the_bash_it_used(self, tmp_path: Path) -> None:
        tmp_repo = _make_tmp_repo(tmp_path)
        _write_config_sh(tmp_repo, _REAL_CONFIG_SH.read_text())
        running_version, _ = _running_bash_identity()

        result, home = _run_block(tmp_path, tmp_repo)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert running_version in result.stdout
        assert "Claude Code's PATH can differ" in result.stdout
        assert _AFTER_BLOCK_SENTINEL in result.stdout
        assert list(home.iterdir()) == []

    @requires_sub_floor_bash
    def test_under_a_real_below_floor_bash_the_real_config_sh_ends_the_run(self, tmp_path: Path) -> None:
        tmp_repo = _make_tmp_repo(tmp_path)
        _write_config_sh(tmp_repo, _REAL_CONFIG_SH.read_text())
        running_version, running_bash = _running_bash_identity(SUB_FLOOR_BASH)

        result, home = _run_block(tmp_path, tmp_repo, bash=SUB_FLOOR_BASH)

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _AFTER_BLOCK_SENTINEL not in result.stdout
        assert running_version in result.stderr
        assert running_bash in result.stderr
        assert list(home.iterdir()) == []


class TestBashFloorCheckPlacement:
    def test_only_comments_set_e_and_the_banner_precede_the_block(self) -> None:
        install_text = _INSTALL_SH.read_text()
        assert install_text.count(_FIXTURE_START) == 1
        before_block = install_text[: install_text.index(_FIXTURE_START)].splitlines()
        assert "set -e" in before_block, "install.sh must run under set -e before the block"
        offenders = [
            line
            for line in before_block
            if line.strip() and not line.startswith("#") and line != "set -e" and not line.startswith('echo "===')
        ]
        assert offenders == [], f"commands before the bash-floor-check block: {offenders}"
