"""Tests for the WebFetch allowlist seed block in install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: webfetch-allowlist-seed — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: webfetch-allowlist-seed — end"

_ALLOWLIST_REL_PATH = ".claude/webfetch-allowed-domains.md"


def _extract_seed_block() -> str:
    """Same marker-delimited extraction strategy as
    test_install_sh_local_bin_path.py and test_install_sh_repo_relocation_support.py:
    delimited by marker comments rather than shell-syntax matching, so a
    future reorder can't silently pick up the wrong text while the test
    keeps passing.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "webfetch-allowed-domains.md" in block, (
        f"extracted block is missing the seed write; markers in {_INSTALL_SH} are "
        f"probably misplaced. Got: {block!r}"
    )
    return block


def _run_seed_block(test_home: Path) -> subprocess.CompletedProcess:
    """Run the extracted seed block with $HOME pointed at an isolated dir.
    `set -e` matches install.sh's own line 2, same rationale as the
    continuity-hardening and local-bin-path tests."""
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    script = "set -e\n" + _extract_seed_block()
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestInstallShWebfetchAllowlistSeed:
    def test_seeds_a_starter_file_when_absent(self, tmp_path: Path) -> None:
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)

        result = _run_seed_block(test_home)

        assert result.returncode == 0, f"seed block must exit 0; stderr={result.stderr!r}"
        allowlist = test_home / _ALLOWLIST_REL_PATH
        assert allowlist.exists()
        content = allowlist.read_text()
        assert "github.com" in content
        assert "*.github.com" in content

    def test_second_real_run_is_a_byte_for_byte_no_op(self, tmp_path: Path) -> None:
        """The actual production workflow: install.sh re-runs on every
        `git pull`. A second run against the first run's own real output
        must not overwrite it."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)

        first = _run_seed_block(test_home)
        assert first.returncode == 0, f"first run must exit 0; stderr={first.stderr!r}"
        after_first = (test_home / _ALLOWLIST_REL_PATH).read_text()

        second = _run_seed_block(test_home)
        assert second.returncode == 0, f"second run must exit 0; stderr={second.stderr!r}"
        assert (test_home / _ALLOWLIST_REL_PATH).read_text() == after_first, (
            "a second run must be a byte-for-byte no-op on the first run's real output"
        )

    def test_does_not_clobber_a_consumer_curated_allowlist(self, tmp_path: Path) -> None:
        """The load-bearing guarantee: an existing consumer's own additions
        to the allowlist must survive install.sh re-running on every
        `git pull` — this is what the file-existence guard exists for."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        curated_content = "docs.python.org\n*.internal.example.com\n"
        (test_home / _ALLOWLIST_REL_PATH).write_text(curated_content)

        result = _run_seed_block(test_home)

        assert result.returncode == 0, f"seed block must exit 0; stderr={result.stderr!r}"
        assert (test_home / _ALLOWLIST_REL_PATH).read_text() == curated_content
