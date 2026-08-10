"""Tests for install.sh's un-adopt loop -- the generic, dynamically-derived
counterpart to the plans/handoffs/briefs migration block covering every
other name a prior `stow --adopt` pulled into claude/.claude/ (session
transcripts, .claude.json, plugin caches, review markers).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: un-adopt-loop — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: un-adopt-loop — end"


def _extract_un_adopt_loop_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files -- syntax-matching the loop would silently
    pick up an edited command, or miss one, on reordering."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "stow_unadopt_entry" in block, (
        f"extracted block is missing the un-adopt call; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _build_fake_repo(tmp_path: Path) -> Path:
    """Build a throwaway checkout at tmp_path/repo whose
    claude/.claude/scripts/_stow_migration_lib.sh is a symlink to the real
    file -- exercises the actual library under test, not a copy that could
    silently drift from it. A real git repo, since stow_untracked_package_entries
    tells adopted content apart from package content via `git ls-files`, not
    .gitignore."""
    repo = tmp_path / "repo"
    scripts_dir = repo / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(SCRIPTS_DIR / "_stow_migration_lib.sh")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "claude/.claude/scripts"], cwd=repo, check=True)
    return repo


def _run_un_adopt_loop(
    test_home: Path, repo_dir: Path, *, concurrency_stub: str | None = None
) -> subprocess.CompletedProcess:
    """Run the extracted un-adopt loop with $HOME and $REPO_DIR pointed at
    isolated fixtures, and $CLAUDE_SESSION_MAY_BE_ACTIVE forced empty --
    both are normally computed earlier in install.sh (outside the extracted
    block), so the test supplies them directly.

    _claude_session_is_active_now is normally defined in install.sh's
    separate session-concurrency-check block (not extracted here) and calls
    the real `pgrep` -- stub it to "no session" (return 1) by default so
    this block's own loop logic is what's under test, not this machine's
    process list; test_install_sh_session_concurrency_check.py covers the
    real function's own pgrep-driven behavior directly. concurrency_stub
    overrides that default for tests that need call-by-call behavior."""
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    env["REPO_DIR"] = str(repo_dir)
    env["CLAUDE_SESSION_MAY_BE_ACTIVE"] = ""
    stub = concurrency_stub if concurrency_stub is not None else "_claude_session_is_active_now() { return 1; }\n"
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        + stub
        + _extract_un_adopt_loop_block()
    )
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _adopt(repo: Path, home: Path, name: str, files: dict[str, str]) -> Path:
    """Create repo/claude/.claude/<name> with `files` and symlink
    home/.claude/<name> to it -- mirrors what a stow --adopt run leaves
    behind for a name this loop targets. Returns the repo-side source."""
    source = repo / "claude" / ".claude" / name
    source.mkdir(parents=True)
    for rel_path, content in files.items():
        (source / rel_path).write_text(content)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / name).symlink_to(source)
    return source


class TestInstallShUnAdoptLoop:
    def test_one_failure_does_not_abort_processing_of_a_later_entry(
        self, tmp_path: Path
    ) -> None:
        """A failure un-adopting one entry (projects) is reported in the
        summary without aborting the other (sessions)."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = _build_fake_repo(tmp_path)

        # "sessions": adopted (symlink into the package) -- succeeds normally.
        ok_source = _adopt(repo, home, "sessions", {"s.json": "{}"})

        # "projects": physically present (untracked) but its target has
        # independently reacquired real content since a prior interrupted
        # run -- stow_unadopt_entry's documented refuse-rather-than-clobber
        # path (see TestStowUnadoptEntry.test_resumed_rename_refuses_to_
        # clobber_content_that_reappeared_since_interruption in
        # scripts/tests/test_stow_migration_lib.py).
        fail_source = repo / "claude" / ".claude" / "projects"
        fail_source.mkdir(parents=True)
        (fail_source / "p.json").write_text("{}")
        run_dir = home / ".claude-config-relocate-backup" / "projects.20260101000000-unadopt"
        run_dir.mkdir(parents=True)
        fail_target = home / ".claude" / "projects"
        fail_target.mkdir(parents=True)
        (fail_target / "live.json").write_text('{"live": true}')

        result = _run_un_adopt_loop(home, repo)

        assert result.returncode == 0, (
            f"the loop wrapper must not abort on a per-entry failure; stderr={result.stderr!r}"
        )
        assert "projects" in result.stderr, (
            f"the failure summary must name the failed entry; stderr={result.stderr!r}"
        )
        ok_target = home / ".claude" / "sessions"
        assert ok_target.is_dir() and not ok_target.is_symlink(), (
            "the surviving entry must still be un-adopted despite the other's failure"
        )
        assert (ok_target / "s.json").read_text() == "{}"
        assert not ok_source.exists()
        assert (fail_target / "live.json").read_text() == '{"live": true}', (
            "the failed entry's independently-reacquired content must be untouched"
        )
        assert fail_source.exists(), (
            "the failed entry's package-side source must be left untouched"
        )

    def test_per_entry_concurrency_recheck_stops_the_loop_mid_run(
        self, tmp_path: Path
    ) -> None:
        """_claude_session_is_active_now is re-checked before each entry
        specifically to narrow the concurrency race window from "once up
        front" to "once per entry" -- a stateful stub that flips from "no
        session" to "session found" between calls proves the loop actually
        acts on a mid-run detection. Entry order from `find` isn't
        guaranteed, so this asserts on whichever entry ends up processed
        rather than a fixed name."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = _build_fake_repo(tmp_path)
        names = ["alpha", "beta"]
        for name in names:
            _adopt(repo, home, name, {"f.md": f"# {name}\n"})

        stub = (
            "_concurrency_check_calls=0\n"
            "_claude_session_is_active_now() {\n"
            "  _concurrency_check_calls=$((_concurrency_check_calls + 1))\n"
            '  [ "$_concurrency_check_calls" -gt 1 ]\n'
            "}\n"
        )

        result = _run_un_adopt_loop(home, repo, concurrency_stub=stub)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        processed = [n for n in names if not (home / ".claude" / n).is_symlink()]
        stopped_before = [n for n in names if (home / ".claude" / n).is_symlink()]
        assert len(processed) == 1 and len(stopped_before) == 1, (
            f"exactly one entry must be processed before the flip; "
            f"processed={processed} stopped_before={stopped_before}"
        )
        assert f"stopping before '{stopped_before[0]}'" in result.stderr, (
            f"the warning must name the entry the loop stopped before; stderr={result.stderr!r}"
        )
