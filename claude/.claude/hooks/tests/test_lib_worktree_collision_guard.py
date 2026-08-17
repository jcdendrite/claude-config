"""Unit tests for _lib.sh's worktree-collision-guard trio:
_lib_worktree_collision_guard, _lib_worktree_lock_pid, and
_lib_resolve_claude_pid. See .claude/plans/worktree-collision-guard.md.

Hook-level (subprocess-of-a-hook) coverage of the same guard lives in
test_require_worktree_for_git_writes.py's and
test_require_worktree_for_file_writes.py's TestWorktreeCollisionGuard
classes; this file covers the branches those two can't reach without a
live race or a git-mocking seam.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import _dead_pid, _seed_session, _worktree_lock_reason
from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"

# Generous but bounded wait for one racer in the 20-way concurrency test
# below: the guard's own worst-case subprocess chain is a handful of
# 5s-capped git calls plus _launch_collision_guard_racer's own 5s
# session-poll cap, so a racer that blows past this is genuinely hung, not
# slow.
_RACER_TIMEOUT_SECONDS = 30


def _init_opted_in_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _add_worktree(repo: Path, wt_path: Path, branch: str) -> None:
    subprocess.run(["git", "worktree", "add", "-b", branch, str(wt_path)], cwd=repo, check=True)


def _git_common_dir(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _git_dir(worktree: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _lock_worktree(worktree: Path, reason: str) -> None:
    subprocess.run(
        ["git", "-C", str(worktree), "worktree", "lock", str(worktree), "--reason", reason],
        check=True,
    )


def _run_collision_guard(
    target_path: Path, repo_git_common_dir: str, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash", "-c",
            f'. "{LIB_SH}"; _lib_worktree_collision_guard "$1" "$2"',
            "_lib_worktree_collision_guard", str(target_path), repo_git_common_dir,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _run_lock_pid(worktree_root: str, porcelain_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", "-c",
            f'. "{LIB_SH}"; _lib_worktree_lock_pid "$1" "$2"',
            "_lib_worktree_lock_pid", worktree_root, porcelain_text,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_resolve_claude_pid(home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_resolve_claude_pid'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _launch_collision_guard_racer(
    target_path: Path, repo_git_common_dir: str, home: Path
) -> subprocess.Popen:
    """Start one racer against _lib_worktree_collision_guard without
    blocking, so N racers can be in flight concurrently. The racer waits
    on its own session file — keyed to this outer bash process's own pid,
    which Python knows synchronously as the returned Popen's `.pid` —
    before calling the guard; the caller MUST seed that session
    immediately after this returns; the racer blocks forever otherwise.

    The guard call itself runs in a backgrounded, explicitly-waited-on
    child rather than as the outer `-c` string's own tail statement: a
    plain nested `bash -c '...'` there would be collapsed by bash's
    tail-call exec optimization into the SAME os process as this outer
    one, giving every racer launched this way an identical $PPID and
    silently defeating the whole test (verified empirically — see
    .claude/plans/atomic-worktree-lock-acquisition.md). The trailing
    `exit $?` is required too: `wait` alone does not propagate the
    backgrounded child's exit code as this script's own.

    The session-file poll loop is capped at 5s (500 tries * 0.01s) with a
    distinct exit code: an uncapped `while` here would hang forever, not
    fail fast, if a future change to _seed_session or the session-file
    naming convention broke the handshake.
    """
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    script = (
        'tries=0\n'
        'while [ ! -f "$HOME/.claude/sessions/$$" ]; do\n'
        '  tries=$((tries + 1))\n'
        '  if [ "$tries" -gt 500 ]; then\n'
        '    echo "racer $$: session file never appeared after 5s" >&2\n'
        '    exit 3\n'
        '  fi\n'
        '  sleep 0.01\n'
        'done\n'
        'bash -c \'. "$1"; _lib_worktree_collision_guard "$2" "$3"\' _ "$1" "$2" "$3" &\n'
        'inner=$!\n'
        'wait "$inner"\n'
        'exit $?\n'
    )
    return subprocess.Popen(
        ["bash", "-c", script, "_", str(LIB_SH), str(target_path), repo_git_common_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


class TestConcurrentLockRace:
    def test_20_way_concurrent_lock_race_exactly_one_winner(self, isolated_home, tmp_path):
        """The exclusivity property the whole collision-guard design leans
        on, proven against the guard's own O_EXCL acquisition write — not
        raw `git worktree lock`, which is confirmed non-atomic at git's
        own source and was the actual cause of a CI flake in an earlier
        version of this test (see
        .claude/plans/atomic-worktree-lock-acquisition.md). N real
        concurrent processes each call _lib_worktree_collision_guard
        against the same worktree; exactly one succeeds. Under the O_EXCL
        rewrite this is a deterministic OS guarantee, not a probabilistic
        one, so 100% pass across repeated runs is the actual proof the fix
        works — a single pass proves nothing about a race fix."""
        repo = tmp_path / "race-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "race-worktree"
        _add_worktree(repo, wt_path, "race")
        common_dir = _git_common_dir(repo)

        concurrency = 20
        procs = []
        for i in range(concurrency):
            proc = _launch_collision_guard_racer(wt_path, common_dir, isolated_home)
            _seed_session(isolated_home, f"racer-{i}-session", pid=proc.pid)
            procs.append(proc)

        for p in procs:
            try:
                p.communicate(timeout=_RACER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()
                pytest.fail(
                    f"racer pid {p.pid} did not exit within "
                    f"{_RACER_TIMEOUT_SECONDS}s -- broken session-file "
                    f"handshake or a hung guard call, not a race outcome"
                )
        returncodes = [p.returncode for p in procs]

        successes = [rc for rc in returncodes if rc == 0]
        assert len(successes) == 1, (
            f"expected exactly one winner among {concurrency} racers, "
            f"got {len(successes)} (returncodes={returncodes})"
        )
        losers = [rc for rc in returncodes if rc != 0]
        assert losers == [1] * (concurrency - 1), (
            f"every loser must be a real deny (exit 1), not an unrelated "
            f"resolution failure: {returncodes}"
        )

        winner_pid = procs[returncodes.index(0)].pid
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {winner_pid}" in reason


class TestCollisionGuardRereadRace:
    def test_reread_shows_unlocked_still_denies(self, isolated_home, tmp_path):
        """The window between the guard's own failed `lock` attempt and its
        diagnosis re-read is a genuine, narrow race: if the original
        holder's unlock lands in exactly that window, the guard still
        denies (the fail-closed direction) rather than reading the
        now-unlocked state as newly safe. Forced deterministically here via
        a `git` wrapper placed first on PATH that performs the racing
        unlock the instant the guard's SECOND `worktree list --porcelain`
        call (the diagnosis re-read, distinguishable from the first,
        self-lock-check read by call order) is made — a seam not available
        from a full-hook black-box test, which is why this scenario is
        unit-tested here instead of in test_require_worktree_for_git_writes.py."""
        repo = tmp_path / "race-read-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "race-read-worktree"
        _add_worktree(repo, wt_path, "race-read")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "reread-race-session")

        # Genuinely locked by a foreign (dead) pid, so the guard's own lock
        # attempt fails for real, not by construction.
        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid}")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        counter_file = tmp_path / "_porcelain_read_count"
        counter_file.write_text("0")
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
  if [ "$count" -eq 2 ]; then
    "{real_git}" -C "{wt_path}" worktree unlock "{wt_path}"
  fi
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1
        assert "already clear again" in result.stdout, result.stdout
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the diagnosis re-read for this to be "
            "a real test of the re-read branch, not an accidental early exit"
        )

    def test_verification_reread_mismatch_after_successful_write_denies(self, isolated_home, tmp_path):
        """A distinct, narrow race from the one above: the window between
        the guard's own successful noclobber write and its post-write
        verification reread (mechanism 2 of
        .claude/plans/atomic-worktree-lock-acquisition.md). If a human's
        `git worktree unlock` lands in exactly that window, the guard must
        still deny (fail closed) rather than return 0 for a lock it can no
        longer confirm holds — this is the property mechanism 2 exists to
        provide, and without this test it was only asserted in the plan's
        prose. Forced deterministically via a `git` wrapper that performs
        the racing unlock the instant the guard's SECOND `worktree list
        --porcelain` call (the verification reread, distinguishable from
        the first, self-lock-check read by call order) is made — the write
        itself succeeds for real, so the reread is what's under test, not
        the write."""
        repo = tmp_path / "verify-reread-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "verify-reread-worktree"
        _add_worktree(repo, wt_path, "verify-reread")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "verify-reread-session")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        counter_file = tmp_path / "_porcelain_read_count"
        counter_file.write_text("0")
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
  if [ "$count" -eq 2 ]; then
    "{real_git}" -C "{wt_path}" worktree unlock "{wt_path}"
  fi
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1
        assert result.stdout != ""
        assert "could not be confirmed after acquiring it" in result.stdout, result.stdout
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the verification reread for this to "
            "be a real test of the verification-reread branch"
        )

    def test_self_race_on_write_attempt_still_allows_via_diagnostic_reread(self, isolated_home, tmp_path):
        """A concurrent invocation from this SAME live session (two parallel
        subagents, or a backgrounded Bash write, both writing into this
        worktree with no `isolation: worktree` between them -- a pattern
        this repo's own CLAUDE.md Agent Briefing describes as normal) can
        lose the noclobber-write race to an earlier call from its own pid,
        not a foreign one. The diagnostic re-read must recognize its own
        pid there and allow, exactly like the first read's self-lock check
        -- not misreport the caller's own pid as a foreign collision.
        Forced deterministically via a `git` wrapper that, on the FIRST
        `worktree list --porcelain` call (the self-lock check, which must
        still report the accurate pre-race unlocked state), also creates
        the worktree's own `locked` file as a side effect -- simulating a
        racing same-pid write landing in the window between that read and
        the guard's own write attempt, so the guard's real write genuinely
        fails (file already exists) and the diagnosis reread is actually
        reached, not skipped via the first read's own self-lock fast
        path."""
        repo = tmp_path / "self-race-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "self-race-worktree"
        _add_worktree(repo, wt_path, "self-race")
        common_dir = _git_common_dir(repo)
        wt_git_dir = _git_dir(wt_path)
        _seed_session(isolated_home, "self-race-session")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        counter_file = tmp_path / "_porcelain_read_count"
        counter_file.write_text("0")
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
  if [ "$count" -eq 1 ]; then
    output=$("{real_git}" "$@")
    printf '%s' "claude-code pid {os.getpid()}" > "{wt_git_dir}/locked"
    printf '\n' >> "{wt_git_dir}/locked"
    echo "$output"
    exit 0
  fi
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {os.getpid()}" in reason
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the diagnostic reread for this to be "
            "a real test of the self-race branch, not an accidental early "
            "exit via the first read's own self-lock fast path"
        )


class TestCollisionGuardBranches:
    """Coverage for _lib_worktree_collision_guard's decision branches that
    a black-box hook test can also reach (duplicated here at the function
    level, with the same case list as the two hook test files' collision-
    guard classes, per the plan's 'Critical files' section)."""

    def test_acquires_lock_when_unlocked(self, isolated_home, tmp_path):
        repo = tmp_path / "acquire-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "acquire-worktree"
        _add_worktree(repo, wt_path, "acquire")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "acquire-session")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {os.getpid()}" in reason

    def test_self_lock_returns_immediately_without_reattempting_lock(self, isolated_home, tmp_path):
        """Pre-locking as self (bypassing the guard) and then calling the
        guard proves the read-only fast path: if the guard mistakenly tried
        to re-write an already self-held lock file, that noclobber write
        would itself fail (file already exists) and the guard would
        misdiagnose its own lock as a live foreign one — this would show up
        as a nonzero exit here, not just as a wasted subprocess call."""
        repo = tmp_path / "selflock-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "selflock-worktree"
        _add_worktree(repo, wt_path, "selflock")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "selflock-session")
        _lock_worktree(wt_path, f"claude-code pid {os.getpid()}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_self_lock_not_last_worktree_still_recognized(self, isolated_home, tmp_path):
        """The exact shape of the incident this guard's own porcelain-parse
        bug caused: a second worktree added *after* the target means the
        target is no longer the last record in `git worktree list
        --porcelain`'s output. If the target's captured lock state were
        lost (the original bug), the guard would misdiagnose its own lock
        as unlocked, attempt a doomed re-lock, and deny — same reasoning as
        test_self_lock_returns_immediately_without_reattempting_lock above,
        just with a second worktree in the repo to exercise the porcelain
        parser's cross-record behavior."""
        repo = tmp_path / "notlast-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "notlast-worktree"
        _add_worktree(repo, wt_path, "notlast")
        later_wt_path = tmp_path / "notlast-later-worktree"
        _add_worktree(repo, later_wt_path, "notlast-later")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "notlast-session")
        _lock_worktree(wt_path, f"claude-code pid {os.getpid()}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_foreign_live_lock_denies_naming_pid(self, isolated_home, tmp_path, live_pid):
        repo = tmp_path / "foreign-live-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "foreign-live-worktree"
        _add_worktree(repo, wt_path, "foreign-live")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "foreign-live-session")
        foreign_pid = live_pid
        _lock_worktree(wt_path, f"claude-code pid {foreign_pid}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert str(foreign_pid) in result.stdout
        assert "live" in result.stdout

    def test_foreign_dead_lock_denies_with_manual_remedy(self, isolated_home, tmp_path):
        repo = tmp_path / "foreign-dead-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "foreign-dead-worktree"
        _add_worktree(repo, wt_path, "foreign-dead")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "foreign-dead-session")
        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert str(dead_pid) in result.stdout
        assert "no longer running" in result.stdout
        assert "git worktree unlock" in result.stdout
        assert _worktree_lock_reason(wt_path) is not None, "guard must not auto-evict"

    def test_unparseable_reason_lock_denies_with_manual_remedy(self, isolated_home, tmp_path):
        repo = tmp_path / "unparseable-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "unparseable-worktree"
        _add_worktree(repo, wt_path, "unparseable")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "unparseable-session")
        _lock_worktree(wt_path, "reviewing")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert "git worktree unlock" in result.stdout
        assert _worktree_lock_reason(wt_path) is not None, "guard must not auto-evict"

    def test_missing_worktree_root_fails_closed(self, isolated_home, tmp_path):
        """A target_path that resolves to nothing (never existed, or was
        removed out from under the guard) denies rather than crashing or
        falling through to 'unlocked' — direct-call coverage of the branch
        neither hook test file can reach without the caller's own,
        identically-shaped resolution having already failed first (see the
        two hook test files' TestWorktreeCollisionGuard docstrings)."""
        missing_path = tmp_path / "never-existed"
        result = _run_collision_guard(missing_path, "/irrelevant/common/dir", isolated_home)
        assert result.returncode == 1
        assert result.stdout != ""


class TestCollisionGuardResolutionFailures:
    """Coverage for _lib_worktree_collision_guard's early resolution-failure
    branches -- distinct from the lock-state deny branches in
    TestCollisionGuardBranches, these fire before the guard ever reads lock
    state. Each was previously unreachable from any of this repo's existing
    tests; branch 2 below is a named security cross-check (the function's
    own header calls it "a defense against the target having changed
    underneath the caller between its own check and this call") that had no
    test proving it actually fires."""

    def test_foreign_repo_common_dir_denies(self, isolated_home, tmp_path):
        """A REPO_GIT_COMMON_DIR that doesn't match the worktree's own
        resolved common-dir denies unconditionally -- the guard refuses to
        evaluate lock state for a target outside the caller's own
        already-verified repository, rather than trusting the caller's
        argument at face value."""
        repo = tmp_path / "foreign-common-dir-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "foreign-common-dir-worktree"
        _add_worktree(repo, wt_path, "foreign-common-dir")

        result = _run_collision_guard(wt_path, "/some/unrelated/common/dir", isolated_home)
        assert result.returncode == 1
        assert "does not belong to this repository" in result.stdout

    def test_unresolvable_session_denies(self, isolated_home, tmp_path):
        """No session file anywhere in this process's ancestry (the
        SessionStart hook never ran, or CLAUDE_CONFIG_DIR points somewhere
        with no sessions/ entry) denies rather than proceeding as if no
        session existed to protect."""
        repo = tmp_path / "no-session-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "no-session-worktree"
        _add_worktree(repo, wt_path, "no-session")
        common_dir = _git_common_dir(repo)

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert "own process identity" in result.stdout

    def test_wt_common_dir_resolution_failure_denies(self, isolated_home, tmp_path):
        """A failure of the SECOND `rev-parse` (confirming the worktree's
        own common-dir, distinct from the first call's toplevel resolution)
        denies rather than proceeding with an unconfirmed common-dir. Forced
        via a `git` wrapper that fails only that specific call."""
        repo = tmp_path / "wt-common-dir-fail-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "wt-common-dir-fail-worktree"
        _add_worktree(repo, wt_path, "wt-common-dir-fail")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "wt-common-dir-fail-session")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] \\
  && [ "$3" = "rev-parse" ] && [ "$4" = "--path-format=absolute" ] \\
  && [ "$5" = "--git-common-dir" ]; then
  exit 1
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1
        assert "could not confirm" in result.stdout
        assert "belongs to this repository" in result.stdout

    def test_main_working_tree_target_denies(self, isolated_home, tmp_path):
        """A target_path that resolves to the main working tree, not a
        linked worktree, denies via the main-tree precondition check
        (mechanism 3 of .claude/plans/atomic-worktree-lock-acquisition.md):
        the main tree's own `--git-dir` and `--git-common-dir` resolve to
        the same path, which no linked worktree's own git-dir ever does.
        Both existing callers already exclude the main tree before calling
        this guard, so this branch is unreachable through them today --
        it exists as a backstop against a future caller regression, and
        without a direct test it could silently break."""
        repo = tmp_path / "main-tree-repo"
        _init_opted_in_repo(repo)
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "main-tree-session")

        result = _run_collision_guard(repo, common_dir, isolated_home)
        assert result.returncode == 1
        assert "is the main working tree" in result.stdout

    def test_wt_git_dir_resolution_failure_denies(self, isolated_home, tmp_path):
        """A failure of the NEW `--git-dir` rev-parse call (the main-tree
        precondition check's own resolution step, distinct from the
        `--git-common-dir` call covered above) denies rather than
        proceeding to the noclobber write with an unresolved git-dir.
        Forced via a `git` wrapper that fails only that specific call."""
        repo = tmp_path / "wt-git-dir-fail-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "wt-git-dir-fail-worktree"
        _add_worktree(repo, wt_path, "wt-git-dir-fail")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "wt-git-dir-fail-session")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] \\
  && [ "$3" = "rev-parse" ] && [ "$4" = "--path-format=absolute" ] \\
  && [ "$5" = "--git-dir" ]; then
  exit 1
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1
        assert "could not resolve the worktree-specific git-dir" in result.stdout

    def test_first_porcelain_read_failure_denies(self, isolated_home, tmp_path):
        """A failure of the first `worktree list --porcelain` read (the
        self-lock check) denies rather than proceeding to attempt a lock
        without knowing the current state."""
        repo = tmp_path / "first-porcelain-fail-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "first-porcelain-fail-worktree"
        _add_worktree(repo, wt_path, "first-porcelain-fail")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "first-porcelain-fail-session")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  exit 1
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1
        assert "could not read worktree lock state" in result.stdout

    def test_second_porcelain_read_failure_denies(self, isolated_home, tmp_path):
        """A failure of the SECOND `worktree list --porcelain` read (the
        post-failed-lock diagnosis, distinguishable from the first,
        self-lock-check read by call order) denies with a distinct message
        from the first-read failure, rather than crashing or misreporting
        state. The lock attempt itself fails for real (genuinely locked by
        a foreign dead pid), so the diagnosis re-read is actually reached."""
        repo = tmp_path / "second-porcelain-fail-repo"
        _init_opted_in_repo(repo)
        wt_path = tmp_path / "second-porcelain-fail-worktree"
        _add_worktree(repo, wt_path, "second-porcelain-fail")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "second-porcelain-fail-session")

        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid}")

        real_git = shutil.which("git")
        assert real_git is not None, "git must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        counter_file = tmp_path / "_porcelain_read_count"
        counter_file.write_text("0")
        wrapper = fake_bin / "git"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
  if [ "$count" -eq 2 ]; then
    exit 1
  fi
fi
exec "{real_git}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1
        assert "could not confirm the worktree lock holder" in result.stdout
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both reads for "
            "this to be a real test of the second-read-failure branch"
        )


class TestWorktreeLockPid:
    """Every branch of _lib_worktree_lock_pid, fed a synthetic porcelain
    capture directly so each state is exercised without needing a real
    lock/unlock sequence against a repo."""

    def test_locked_with_parseable_pid(self):
        porcelain = (
            "worktree /some/path\n"
            "HEAD abc123\n"
            "branch refs/heads/feature\n"
            "locked claude-code pid 4242\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 0
        assert result.stdout.strip() == "4242"

    def test_unlocked_returns_1(self):
        porcelain = (
            "worktree /some/path\n"
            "HEAD abc123\n"
            "branch refs/heads/feature\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_worktree_absent_from_capture_returns_1(self):
        """A stale capture that no longer lists the target worktree at all
        is treated the same as 'present but unlocked' — not found is not
        found, regardless of why."""
        porcelain = "worktree /other/path\nHEAD abc123\n"
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_locked_with_unparseable_reason_returns_2(self):
        porcelain = "worktree /some/path\nlocked reviewing\n"
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 2
        assert result.stdout == ""

    def test_locked_target_not_last_record_still_found(self):
        """The target's lock state must survive a later, unrelated worktree
        record in the same capture — a repo with more than one worktree
        puts the contended one anywhere in the listing, not just last."""
        porcelain = (
            "worktree /main\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /some/path\n"
            "HEAD def456\n"
            "branch refs/heads/feature\n"
            "locked claude-code pid 4242\n"
            "\n"
            "worktree /later/path\n"
            "HEAD ghi789\n"
            "branch refs/heads/later\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 0
        assert result.stdout.strip() == "4242"

    def test_unlocked_target_not_contaminated_by_later_locked_record(self):
        """The inverse of the above: a genuinely unlocked target followed by
        a *locked* later worktree must not have that later lock bleed into
        the target's result."""
        porcelain = (
            "worktree /some/path\n"
            "HEAD abc123\n"
            "branch refs/heads/feature\n"
            "\n"
            "worktree /later/path\n"
            "HEAD def456\n"
            "branch refs/heads/later\n"
            "locked claude-code pid 9999\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_locked_target_not_last_with_trailing_unparseable_reason(self):
        """A locked, pid-parseable target followed by a worktree with an
        unparseable-reason lock — exercises the trailing record's own
        pid="" no-match branch, not just the parseable-pid branch the
        not-last case above covers."""
        porcelain = (
            "worktree /some/path\n"
            "HEAD abc123\n"
            "branch refs/heads/feature\n"
            "locked claude-code pid 4242\n"
            "\n"
            "worktree /later/path\n"
            "HEAD def456\n"
            "branch refs/heads/later\n"
            "locked reviewing\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 0
        assert result.stdout.strip() == "4242"


class TestResolveClaudePid:
    def test_finds_session_file_for_immediate_ancestor(self, isolated_home):
        """Run as `bash -c '... _lib_resolve_claude_pid'` from this test
        process, the resolved ancestor is this test process itself (bash's
        $PPID) — matching how _seed_session's default pid (os.getpid())
        already keys every other test in this suite that seeds a session."""
        _seed_session(isolated_home, "resolve-success-session")
        result = _run_resolve_claude_pid(isolated_home)
        assert result.returncode == 0
        assert result.stdout == f"resolve-success-session {os.getpid()}"

    def test_no_session_file_anywhere_in_ancestry_returns_2(self, isolated_home):
        result = _run_resolve_claude_pid(isolated_home)
        assert result.returncode == 2
        assert result.stdout == ""
