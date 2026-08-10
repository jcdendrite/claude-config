"""Tests for the transcript-config-dirs advisory in install.sh: a print-only
heads-up (never a prompt, never a write) that a profile's CLAUDE_CONFIG_DIR
diverges from ~/.claude, that ~/.claude is a symlink, or that the declared-
roots file exists but has no usable entries.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: transcript-config-dirs — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: transcript-config-dirs — end"


def _extract_block() -> str:
    """Return the check_transcript_config_dirs function body from install.sh.

    Delimited by explicit marker comments rather than located by matching its
    own shell syntax -- same rationale as
    test_install_sh_continuity_hardening.py's _extract_hardening_block:
    syntax-matching would silently pick up unrelated logic, or drop a guard,
    whenever the block is reordered, and a test extracting the wrong text
    would still pass.
    """
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "check_transcript_config_dirs" in block, (
        f"extracted block is missing the function definition; markers in "
        f"{_INSTALL_SH} are probably misplaced. Got: {block!r}"
    )
    return block


def _run(test_home: Path, config_dir: str | None) -> subprocess.CompletedProcess:
    """Run the extracted function body plus one call to it.

    `set -e` prepended to match install.sh's own line 2, so the block runs
    under the same abort-on-error semantics production does. `os.environ` is
    never mutated -- only the per-invocation subprocess env below -- matching
    test_install_sh_continuity_hardening.py's isolation rationale.
    """
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    if config_dir is None:
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return subprocess.run(
        [_BASH, "-c", "set -e\n" + _extract_block() + "\ncheck_transcript_config_dirs\n"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestInstallShTranscriptConfigDirs:
    def test_diverged_config_dir_prints_tip(self, tmp_path: Path) -> None:
        """CLAUDE_CONFIG_DIR resolving to a different real path than the
        default ~/.claude prints a TIP naming both resolved paths."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        other_profile_dir = tmp_path / "other-profile" / ".claude"
        other_profile_dir.mkdir(parents=True)

        result = _run(test_home, str(other_profile_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "TIP" in result.stdout
        assert os.path.realpath(other_profile_dir) in result.stdout

    def test_config_dir_resolving_to_same_path_is_silent(self, tmp_path: Path) -> None:
        """CLAUDE_CONFIG_DIR set but naming the same path as ~/.claude prints
        nothing about divergence."""
        test_home = tmp_path / "home"
        claude_dir = test_home / ".claude"
        claude_dir.mkdir(parents=True)

        result = _run(test_home, str(claude_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "", f"expected silence, got stdout={result.stdout!r}"
        assert result.stderr == ""

    def test_unset_config_dir_is_silent(self, tmp_path: Path) -> None:
        """CLAUDE_CONFIG_DIR unset (the common case) prints nothing."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)

        result = _run(test_home, None)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "", f"expected silence, got stdout={result.stdout!r}"
        assert result.stderr == ""

    def test_config_dir_pointing_to_nonexistent_path_does_not_abort(self, tmp_path: Path) -> None:
        """CLAUDE_CONFIG_DIR naming a directory that doesn't exist makes the
        inner `cd` fail; under install.sh's own `set -e` an unguarded
        assignment to a failed command substitution aborts the whole script,
        so this pins the fallback that keeps the function silent instead."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)

        result = _run(test_home, str(tmp_path / "does-not-exist"))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "", f"expected silence, got stdout={result.stdout!r}"
        assert result.stderr == ""

    def test_comments_only_roots_file_warns_exists_but_empty(self, tmp_path: Path) -> None:
        """~/.claude/transcript-config-dirs present but containing only
        comments and blank lines warns that it has no usable entries."""
        test_home = tmp_path / "home"
        claude_dir = test_home / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "transcript-config-dirs").write_text("# nothing declared yet\n\n")

        result = _run(test_home, None)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "WARNING" in result.stdout
        assert "transcript-config-dirs" in result.stdout
        assert "no usable" in result.stdout

    def test_populated_roots_file_is_silent_about_the_file(self, tmp_path: Path) -> None:
        """A roots file with at least one usable entry triggers no warning."""
        test_home = tmp_path / "home"
        claude_dir = test_home / ".claude"
        claude_dir.mkdir(parents=True)
        declared_root = tmp_path / "sibling-account-profile"
        (declared_root / "projects").mkdir(parents=True)
        (claude_dir / "transcript-config-dirs").write_text(f"{declared_root}\n")

        result = _run(test_home, None)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "", f"expected silence, got stdout={result.stdout!r}"
        assert result.stderr == ""

    def test_symlinked_claude_dir_names_resolved_target(self, tmp_path: Path) -> None:
        """~/.claude being a symlink prints a TIP naming the one-level
        readlink target -- creating the roots file there would write into
        that resolved location, not a plain ~/.claude directory."""
        test_home = tmp_path / "home"
        test_home.mkdir(parents=True)
        link_target = tmp_path / "checkout" / "claude" / ".claude"
        link_target.mkdir(parents=True)
        (test_home / ".claude").symlink_to(link_target)

        result = _run(test_home, None)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "TIP" in result.stdout
        assert "symlink" in result.stdout
        assert str(link_target) in result.stdout

    def test_advisory_never_prints_a_declared_root_path(self, tmp_path: Path) -> None:
        """The advisory reports only whether the roots file is populated,
        never the paths it declares -- a declared path identifies an
        engagement. Combines a diverged CLAUDE_CONFIG_DIR (to force the
        advisory to print something) with a populated roots file naming a
        private-looking path, and asserts that path never appears."""
        test_home = tmp_path / "home"
        (test_home / ".claude").mkdir(parents=True)
        other_profile_dir = tmp_path / "other-profile" / ".claude"
        other_profile_dir.mkdir(parents=True)
        declared_root = tmp_path / "private-engagement-corp" / "profile"
        (declared_root / "projects").mkdir(parents=True)
        (test_home / ".claude" / "transcript-config-dirs").write_text(f"{declared_root}\n")

        result = _run(test_home, str(other_profile_dir))

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "TIP" in result.stdout
        assert str(declared_root) not in result.stdout
        assert str(declared_root) not in result.stderr
        assert "private-engagement-corp" not in result.stdout
        assert "private-engagement-corp" not in result.stderr
