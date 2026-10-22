"""Unit tests for _lib.sh's worktree-collision-guard functions:
_lib_worktree_collision_guard, _lib_worktree_lock_pid,
_lib_resolve_claude_pid, _lib_worktree_lock_is_mine, and
_lib_registered_live_sid. See .claude/plans/worktree-collision-guard.md and
.claude/plans/worktree-lock-self-pid-after-clear.md.

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
from helpers import HOOKS_DIR, init_git_repo_with_commit

from .conftest import _dead_pid, _seed_session, _worktree_lock_reason

LIB_SH = HOOKS_DIR / "_lib.sh"

# A `TZ=UTC LC_ALL=C ps -o lstart=` shaped value that no live process carries.
_FIXED_START_TIME = "Mon Jan  1 00:00:00 1970"

# The caller-side pid argument for direct _lib_worktree_lock_is_mine rows.
# The predicate takes it as data and never inspects the process behind it.
_OTHER_CALLER_PID = "424242"

# Generous but bounded wait for one racer in the 20-way concurrency tests
# below: under heavy 20-way subprocess contention the guard's own
# git/bash subprocess chain (each individually capped at 5s by
# _lib_capped) can take several seconds end to end, and a racer that
# won also waits out the barrier below before exiting -- see
# _RACER_BARRIER_MAX_WAIT_SECONDS. Set with real margin above the
# session-file poll's 5s bound plus the 40s barrier cap plus the guard
# call's own several-seconds-under-contention cost, so a loaded CI runner
# doesn't misdiagnose a slow-but-correct run as hung. A racer that blows
# past this combined budget is genuinely hung, not slow.
_RACER_TIMEOUT_SECONDS = 70

# How long a winning racer's barrier wait (see _launch_collision_guard_racer)
# polls before giving up and exiting anyway. A real Claude Code session
# stays alive long after acquiring a worktree lock, but a racer that exits
# immediately makes its own pid genuinely dead within milliseconds, which
# the auto-reclaim path this repo now ships
# (.claude/plans/auto-clear-dead-worktree-locks.md) treats exactly like a
# real crash -- a straggler racer still mid-diagnosis could then
# legitimately reclaim it, breaking an "exactly one winner across the
# whole run" assertion with no atomicity violation at any single instant.
# Bounded, not a fixed sleep: the barrier itself (a "done" marker per
# racer) is what actually synchronizes the wait to the cohort's real
# completion time under whatever contention this run happens to hit; this
# cap only bounds a broken-barrier hang, so its exact value isn't
# load-bearing the way a fixed sleep duration would be.
_RACER_BARRIER_MAX_WAIT_SECONDS = 40


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
    target_path: Path,
    repo_git_common_dir: str,
    home: Path,
    extra_env: dict | None = None,
    prelude: str = "",
) -> subprocess.CompletedProcess:
    """`prelude` is shell text run before sourcing _lib.sh, e.g. `set -u;`."""
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash", "-c",
            f'{prelude} . "{LIB_SH}"; _lib_worktree_collision_guard "$1" "$2"',
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


def _run_lock_is_mine(
    home: Path,
    locked_pid: str,
    locked_session_id: str,
    my_pid: str,
    my_session_id: str,
    config_dir_env: str | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run _lib_worktree_lock_is_mine under `set -eu`, exiting with its own
    return code. The return code separates only a missing function (exit 127)
    from a real return. Each row's empty-stderr assertion is what separates an
    unbound-variable abort from `return 1`."""
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir_env is not None:
        env["CLAUDE_CONFIG_DIR"] = config_dir_env
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash", "-c",
            f'set -eu; . "{LIB_SH}"; _lib_worktree_lock_is_mine "$@" && rc=0 || rc=$?; exit "$rc"',
            "_lib_worktree_lock_is_mine", locked_pid, locked_session_id, my_pid, my_session_id,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _run_registered_live_sid(
    home: Path, config_dir: Path, pid: int, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run _lib_registered_live_sid under `set -eu`, exiting with its own
    return code. The return code separates only a missing function (exit 127)
    from a real return. Each row's empty-stderr assertion is what separates an
    unbound-variable abort from `return 1`."""
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash", "-c",
            f'set -eu; . "{LIB_SH}"; _lib_registered_live_sid "$@" && rc=0 || rc=$?; exit "$rc"',
            "_lib_registered_live_sid", str(config_dir), str(pid),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _locked_file_bytes(worktree: Path) -> bytes:
    return Path(_git_dir(worktree), "locked").read_bytes()


def _install_failing_ps(tmp_path: Path) -> tuple[dict, Path]:
    """Put a `ps` stub first on PATH that exits 1 with empty stdout and
    touches a marker file. Returns the extra_env and the marker, so a row can
    assert the stub was the `ps` that ran rather than pass vacuously."""
    fake_bin = tmp_path / "_failing_ps_bin"
    fake_bin.mkdir()
    marker = tmp_path / "_failing_ps_invoked"
    stub = fake_bin / "ps"
    stub.write_text(f"""#!/bin/bash
: > "{marker}"
exit 1
""")
    stub.chmod(0o755)
    return {"PATH": f"{fake_bin}:{os.environ['PATH']}"}, marker


def _install_porcelain_counting_git(
    tmp_path: Path,
    wt_path: Path,
    lock_rewrite: str | None = None,
    lock_rewrite_at_read: int = 1,
    rewriting_call_sees_rewrite: bool = False,
) -> tuple[dict, Path]:
    """Put a `git` wrapper first on PATH that counts `worktree list
    --porcelain` calls against WT_PATH. Returns the extra_env for
    _run_collision_guard and the file holding the running count.

    LOCK_REWRITE, when given, is written verbatim (plus a newline) to
    WT_PATH's `locked` file during the LOCK_REWRITE_AT_READ-th counted call.
    That models a racing writer landing between two of the guard's reads. By
    default the rewriting call echoes the output it captured before the
    rewrite, so the rewrite first shows on the next call.
    REWRITING_CALL_SEES_REWRITE makes the rewriting call itself report the
    rewritten lock instead. None leaves every call untouched apart from the
    count."""
    real_git = shutil.which("git")
    assert real_git is not None, "git must be on PATH to build the wrapper"
    fake_bin = tmp_path / "_fake_bin"
    fake_bin.mkdir()
    counter_file = tmp_path / "_porcelain_read_count"
    counter_file.write_text("0")
    rewrite_block = ""
    if lock_rewrite is not None:
        capture_before_rewrite = "" if rewriting_call_sees_rewrite else f'    output=$("{real_git}" "$@")\n'
        echo_before_rewrite = "" if rewriting_call_sees_rewrite else '    echo "$output"\n    exit 0\n'
        rewrite_block = (
            f'  if [ "$count" -eq {lock_rewrite_at_read} ]; then\n'
            f"{capture_before_rewrite}"
            f"""    printf '%s' "{lock_rewrite}" > "{_git_dir(wt_path)}/locked"\n"""
            f"""    printf '\\n' >> "{_git_dir(wt_path)}/locked"\n"""
            f"{echo_before_rewrite}"
            "  fi\n"
        )
    wrapper = fake_bin / "git"
    wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-C" ] && [ "$2" = "{wt_path}" ] && [ "$3" = "worktree" ] && [ "$4" = "list" ] && [ "$5" = "--porcelain" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
{rewrite_block}fi
exec "{real_git}" "$@"
""")
    wrapper.chmod(0o755)
    return {"PATH": f"{fake_bin}:{os.environ['PATH']}"}, counter_file


def _launch_collision_guard_racer(
    target_path: Path, repo_git_common_dir: str, home: Path, concurrency: int
) -> subprocess.Popen:
    """Start one racer against _lib_worktree_collision_guard without
    blocking, so N racers can be in flight concurrently. The racer waits
    on its own session file — keyed to this outer bash process's own pid,
    which Python knows synchronously as the returned Popen's `.pid` —
    before calling the guard; the caller MUST seed that session
    immediately after this returns; the racer blocks forever otherwise.
    CONCURRENCY must equal the total number of racers the caller launches
    in this same batch — it sizes the barrier below.

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

    Every racer, win or lose, writes a "done" marker to
    $HOME/.claude/racer-done/<pid> once its own guard call returns. A
    racer that WON (guard exit 0) then blocks until every racer in the
    cohort has written its own marker before exiting -- a barrier, not a
    fixed sleep, so the winner's pid stays observably alive for exactly as
    long as any straggler might still be mid-diagnosis under whatever
    contention this run hits, rather than guessing a duration. Without
    this, a winner that exits immediately makes its own pid genuinely dead
    within milliseconds, which the auto-reclaim path this repo now ships
    (.claude/plans/auto-clear-dead-worktree-locks.md) treats exactly like
    a real crash -- a straggler still mid-diagnosis could then legitimately
    reclaim it, breaking an "exactly one winner across the whole run"
    assertion with no atomicity violation at any single instant. The
    barrier poll is capped at _RACER_BARRIER_MAX_WAIT_SECONDS with its own
    distinct exit code, for the same fail-fast-not-hang reason as the
    session-file poll above.
    """
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    barrier_max_tries = int(_RACER_BARRIER_MAX_WAIT_SECONDS / 0.01)
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
        'status=$?\n'
        'mkdir -p "$HOME/.claude/racer-done"\n'
        ': > "$HOME/.claude/racer-done/$$"\n'
        'if [ "$status" -eq 0 ]; then\n'
        '  barrier_tries=0\n'
        f'  while [ "$(ls -1 "$HOME/.claude/racer-done" | wc -l)" -lt {concurrency} ]; do\n'
        '    barrier_tries=$((barrier_tries + 1))\n'
        f'    if [ "$barrier_tries" -gt {barrier_max_tries} ]; then\n'
        f'      echo "racer $$: barrier never satisfied after {_RACER_BARRIER_MAX_WAIT_SECONDS}s" >&2\n'
        '      exit 4\n'
        '    fi\n'
        '    sleep 0.01\n'
        '  done\n'
        'fi\n'
        'exit $status\n'
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
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "race-worktree"
        _add_worktree(repo, wt_path, "race")
        common_dir = _git_common_dir(repo)

        concurrency = 20
        procs = []
        for i in range(concurrency):
            proc = _launch_collision_guard_racer(wt_path, common_dir, isolated_home, concurrency)
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
                    f"handshake or a genuinely hung guard call, not a race "
                    f"outcome (a racer's own barrier timeout exits normally "
                    f"with code 4 rather than triggering this outer kill)"
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

    def test_n_way_concurrent_eviction_race_exactly_one_winner(self, isolated_home, tmp_path):
        """The exact race the prior design was rejected over
        (.claude/plans/worktree-collision-guard.md:86-108: an evict-then-
        relock sequence racing a second evictor), now closed by the claim
        file's own O_EXCL exclusivity: N racers all diagnosing the SAME
        dead lock concurrently must produce exactly one reclaim winner, not
        a double-holder. Deterministic under the claim's O_EXCL create, so
        100% across repeated runs is the expected result -- see
        test_20_way_concurrent_lock_race_exactly_one_winner's docstring
        above for why a single pass proves nothing about a race fix."""
        repo = tmp_path / "evict-race-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "evict-race-worktree"
        _add_worktree(repo, wt_path, "evict-race")
        common_dir = _git_common_dir(repo)
        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session evict-race-dead-session")

        concurrency = 20
        procs = []
        for i in range(concurrency):
            proc = _launch_collision_guard_racer(wt_path, common_dir, isolated_home, concurrency)
            _seed_session(isolated_home, f"evict-racer-{i}-session", pid=proc.pid)
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
                    f"handshake or a genuinely hung guard call, not a race "
                    f"outcome (a racer's own barrier timeout exits normally "
                    f"with code 4 rather than triggering this outer kill)"
                )
        returncodes = [p.returncode for p in procs]

        successes = [rc for rc in returncodes if rc == 0]
        assert len(successes) == 1, (
            f"expected exactly one reclaim winner among {concurrency} "
            f"racers, got {len(successes)} (returncodes={returncodes})"
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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

    def test_verification_reread_naming_own_pid_with_other_session_still_denies(self, isolated_home, tmp_path):
        """Acquire-verify confirms the caller's own just-written content, so
        it compares the lock's session_id exactly and never applies the
        pid-based recognition of _lib_worktree_lock_is_mine. A lock that
        names this process's pid with another session_id at the verification
        reread is not the content this call wrote, and the guard denies.
        Forced via a `git` wrapper that rewrites `locked` at the SECOND
        `worktree list --porcelain` call (the verification reread, after the
        guard's own write succeeded)."""
        repo = tmp_path / "verify-other-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "verify-other-session-worktree"
        _add_worktree(repo, wt_path, "verify-other-session")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "verify-other-session-session")

        counting_env, counter_file = _install_porcelain_counting_git(
            tmp_path, wt_path,
            lock_rewrite=f"claude-code pid {os.getpid()} session some-other-session",
            lock_rewrite_at_read=2,
            rewriting_call_sees_rewrite=True,
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)
        assert result.returncode == 1
        assert "could not be confirmed" in result.stdout, result.stdout
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the verification reread for this to "
            "be a real test of the verification-reread branch"
        )

    def test_verification_reread_naming_own_pid_without_session_allows(self, isolated_home, tmp_path):
        """Acquire-verify falls back to a pid comparison only for a lock with
        no session_id. The session-less branch of the post-write reread judges
        the lock by pid, so a session-less lock naming this process's pid is
        accepted and the guard allows. Forced via a `git` wrapper that
        rewrites `locked` at the SECOND `worktree list --porcelain` call (the
        verification reread, after the guard's own write succeeded)."""
        repo = tmp_path / "verify-no-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "verify-no-session-worktree"
        _add_worktree(repo, wt_path, "verify-no-session")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "verify-no-session-session")

        counting_env, counter_file = _install_porcelain_counting_git(
            tmp_path, wt_path,
            lock_rewrite=f"claude-code pid {os.getpid()}",
            lock_rewrite_at_read=2,
            rewriting_call_sees_rewrite=True,
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)
        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the verification reread for this to "
            "be a real test of the verification-reread branch"
        )

    def test_verification_reread_naming_other_pid_without_session_denies(self, isolated_home, tmp_path, live_pid):
        """Acquire-verify falls back to a pid comparison only for a lock with
        no session_id. A session-less lock naming a pid other than the
        caller's at the verification reread is not the caller's own
        just-written content, so the guard denies. The deny counterpart of
        test_verification_reread_naming_own_pid_without_session_allows."""
        repo = tmp_path / "verify-no-session-other-pid-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "verify-no-session-other-pid-worktree"
        _add_worktree(repo, wt_path, "verify-no-session-other-pid")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "verify-no-session-other-pid-session")

        counting_env, counter_file = _install_porcelain_counting_git(
            tmp_path, wt_path,
            lock_rewrite=f"claude-code pid {live_pid}",
            lock_rewrite_at_read=2,
            rewriting_call_sees_rewrite=True,
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)
        assert result.returncode == 1, result.stdout
        assert "could not be confirmed after acquiring it" in result.stdout, result.stdout
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the verification reread for this to "
            "be a real test of the verification-reread branch"
        )

    def test_cleared_session_self_race_allows_via_diagnostic_reread(self, isolated_home, tmp_path):
        """The same-process counterpart of
        test_self_race_on_write_attempt_still_allows_via_diagnostic_reread
        for a cleared session: the racing lock names this process's pid with
        the session_id the session carried before `/clear`. The diagnostic
        reread must recognize it as this caller's own lock, as the first
        read does, and leave the racing writer's content untouched."""
        repo = tmp_path / "cleared-self-race-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "cleared-self-race-worktree"
        _add_worktree(repo, wt_path, "cleared-self-race")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "cleared-successor-session")

        counting_env, counter_file = _install_porcelain_counting_git(
            tmp_path, wt_path,
            lock_rewrite=f"claude-code pid {os.getpid()} session cleared-predecessor-session",
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)
        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert _worktree_lock_reason(wt_path) == (
            f"locked claude-code pid {os.getpid()} session cleared-predecessor-session"
        )
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the diagnostic reread for this to be "
            "a real test of the self-race branch, not an accidental early "
            "exit via the first read's own self-lock fast path"
        )

    def test_resumed_session_lock_naming_dead_pid_recognized_on_diagnostic_reread(
        self, isolated_home, tmp_path
    ):
        """The diagnostic reread after a failed write applies the same
        recognition as the first read, session_id match included, not a bare
        pid comparison. A racing writer lands a lock naming an exited pid
        and the caller's own session_id between the guard's first read and
        its write. The reread must allow it and leave the racing writer's
        content untouched, with no reclaim: the claim file is what tells
        recognition from a reclaim of the dead pid's lock."""
        repo = tmp_path / "reread-resumed-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "reread-resumed-session-worktree"
        _add_worktree(repo, wt_path, "reread-resumed-session")
        common_dir = _git_common_dir(repo)
        dead_pid = _dead_pid()
        _seed_session(
            isolated_home, "cleared-successor-session", pid=dead_pid, start_time=_FIXED_START_TIME
        )
        _seed_session(isolated_home, "resumed-session")
        rewritten_lock = f"claude-code pid {dead_pid} session resumed-session"
        counting_env, counter_file = _install_porcelain_counting_git(
            tmp_path, wt_path, lock_rewrite=rewritten_lock
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)

        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert _locked_file_bytes(wt_path) == f"{rewritten_lock}\n".encode()
        claims = list(Path(_git_dir(wt_path)).glob("claude-evicted-lock-*"))
        assert claims == [], f"recognizing the lock must not reclaim it, found {claims}"
        assert int(counter_file.read_text()) == 2, (
            "the wrapper's own counter must have observed both the "
            "self-lock-check read and the diagnostic reread for this to be "
            "a real test of the reread branch, not an accidental early exit "
            "via the first read's own self-lock fast path"
        )

    def test_reclaim_reread_shows_live_foreign_lock_still_denies(self, isolated_home, tmp_path, live_pid):
        """A race that opens only after the guard's diagnosis read, unlike
        the earlier races in this class, which land at or before it: the window
        between _lib_worktree_collision_guard's own diagnosis read and
        _lib_worktree_reclaim_dead_lock's merged raw-lock-file
        read-compare-unlink, which re-reads and validates the lock's
        content in the same subprocess that removes it (see
        .claude/plans/auto-clear-dead-worktree-locks.md for the full design
        rationale). If the lock this caller diagnosed as dead is replaced
        by a genuinely live lock in exactly that window, the reclaim must
        refuse to remove it -- fail closed -- rather than evicting a lock
        it never actually diagnosed. Forced
        deterministically via a `git` wrapper that swaps the pre-locked
        dead-pid lock for a live foreign one immediately after the guard's
        SECOND `worktree list --porcelain` call (the diagnosis read) returns
        its real, pre-swap output -- the same trigger point
        test_reread_shows_unlocked_still_denies and
        test_verification_reread_mismatch_after_successful_write_denies use
        via their own count-2 wrappers. The diagnosis read must still report
        the genuine dead-pid lock so the guard actually proceeds into
        reclaim, so this wrapper captures that call's output before
        performing the swap and echoes it unchanged afterward, mirroring
        test_self_race_on_write_attempt_still_allows_via_diagnostic_reread's
        capture-then-mutate-then-echo pattern rather than the swap-then-exec
        pattern the other count-2 wrappers use. The merged reclaim reads the
        raw lock file directly rather than re-reading porcelain, so no third
        porcelain call happens on this path at all: the merged read itself
        is what catches the swap, and the guard denies without ever
        reaching _lib_worktree_acquire_lock's own porcelain-confirmed
        re-acquisition, so the total call count stays at 2."""
        repo = tmp_path / "reclaim-reread-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "reclaim-reread-worktree"
        _add_worktree(repo, wt_path, "reclaim-reread")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "reclaim-reread-session")

        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session reclaim-reread-dead-session")

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
    output=$("{real_git}" "$@")
    "{real_git}" -C "{wt_path}" worktree unlock "{wt_path}"
    "{real_git}" -C "{wt_path}" worktree lock "{wt_path}" --reason "claude-code pid {live_pid}"
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
        assert result.returncode == 1
        assert "could not be cleared automatically" in result.stdout, result.stdout
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {live_pid}" in reason, (
            "the live lock swapped in mid-reread must survive intact, not "
            "be evicted by a reclaim that only ever diagnosed the earlier "
            "dead pid"
        )
        assert int(counter_file.read_text()) == 2, (
            "the merged reclaim path makes exactly 2 porcelain calls "
            "(self-lock check, diagnosis) on this scenario, since the "
            "raw-file read-compare-unlink that replaces the old in-claim "
            "porcelain reread catches the swap and denies before "
            "_lib_worktree_acquire_lock's own porcelain-confirmed "
            "re-acquisition is ever reached -- an unasserted off-by-one "
            "here could silently intercept the wrong call and pass for "
            "unrelated reasons"
        )

    def test_reclaim_merged_read_compare_unlink_race_destroys_fresh_lock(self, isolated_home, tmp_path, live_pid):
        """A canary for the residual race docs/design-decisions.md §36
        discloses rather than closes: a manual unlock plus a third party's
        fresh acquisition landing inside _lib_worktree_reclaim_dead_lock's
        own merged read-compare-unlink subprocess (_lib.sh's single `cat`-
        then-`rm` call on the raw lock file) still gets evicted, since that
        subprocess only compares against the content it read before the
        swap, not the content actually on disk at unlink time. Forced
        deterministically via an `rm` wrapper placed first on PATH that,
        only when invoked as the exact `rm -f <wt_git_dir>/locked` call
        inside that subprocess, first writes a fresh lock for a different
        live pid (simulating the manual unlock plus fresh acquisition)
        before delegating to the real `rm` -- which then deletes that fresh
        lock unconditionally, since the compare against the stale content
        already succeeded earlier in the same subprocess, before this
        wrapper ever ran. See docs/design-decisions.md §36 for why no source
        fix closes this window; this test only pins the already-disclosed
        outcome, not a fix."""
        repo = tmp_path / "reclaim-inner-race-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "reclaim-inner-race-worktree"
        _add_worktree(repo, wt_path, "reclaim-inner-race")
        common_dir = _git_common_dir(repo)
        wt_git_dir = _git_dir(wt_path)
        _seed_session(isolated_home, "reclaim-inner-race-session")

        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session reclaim-inner-race-dead-session")

        real_rm = shutil.which("rm")
        assert real_rm is not None, "rm must be on PATH to build the wrapper"
        fake_bin = tmp_path / "_fake_bin"
        fake_bin.mkdir()
        counter_file = tmp_path / "_rm_interception_count"
        counter_file.write_text("0")
        wrapper = fake_bin / "rm"
        wrapper.write_text(f"""#!/bin/bash
if [ "$1" = "-f" ] && [ "$2" = "{wt_git_dir}/locked" ]; then
  count=$(cat "{counter_file}")
  count=$((count + 1))
  printf '%s' "$count" > "{counter_file}"
  printf 'claude-code pid {live_pid} session reclaim-inner-race-live-session\\n' > "{wt_git_dir}/locked"
fi
exec "{real_rm}" "$@"
""")
        wrapper.chmod(0o755)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )

        assert int(counter_file.read_text()) == 1, (
            "the wrapper's own counter must show the targeted `rm -f "
            "<locked>` call was actually intercepted exactly once -- "
            "otherwise the assertions below would also pass if the "
            "interception never fired at all"
        )
        assert result.returncode == 0, result.stdout
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {live_pid}" not in reason, (
            "the fresh lock swapped in inside the merged read-compare-unlink "
            "subprocess must survive if this residual race were closed -- it "
            "does not survive today, which is what this canary pins"
        )
        assert f"pid {os.getpid()}" in reason, (
            "the reclaimer must actually hijack the worktree for its own "
            "pid, not merely destroy the swapped-in lock and leave the "
            "worktree unlocked -- the latter is a distinct, separately "
            "disclosed failure mode this assertion must not conflate with "
            "the race this test pins"
        )


class TestCollisionGuardBranches:
    """Coverage for _lib_worktree_collision_guard's decision branches that
    a black-box hook test can also reach (duplicated here at the function
    level, with the same case list as the two hook test files' collision-
    guard classes, per the plan's 'Critical files' section)."""

    def test_acquires_lock_when_unlocked(self, isolated_home, tmp_path):
        repo = tmp_path / "acquire-repo"
        init_git_repo_with_commit(repo)
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
        guard proves the read-only fast path. A guard that re-attempted the
        write would fail the noclobber create, then recognize the lock on its
        diagnostic reread and still exit 0, so the exit code alone cannot
        tell the fast path from that fallback. The exactly-one-porcelain-read
        assertion does: the fast path reads once, and the fallback reads
        again."""
        repo = tmp_path / "selflock-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "selflock-worktree"
        _add_worktree(repo, wt_path, "selflock")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "selflock-session")
        _lock_worktree(wt_path, f"claude-code pid {os.getpid()}")
        counting_env, counter_file = _install_porcelain_counting_git(tmp_path, wt_path)

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)
        assert result.returncode == 0
        assert result.stdout == ""
        assert int(counter_file.read_text()) == 1

    def test_self_lock_not_last_worktree_still_recognized(self, isolated_home, tmp_path):
        """The exact shape of the incident this guard's own porcelain-parse
        bug caused: a second worktree added *after* the target means the
        target is no longer the last record in `git worktree list
        --porcelain`'s output. If the target's captured lock state were
        lost (the original bug), the guard would misdiagnose its own lock
        as unlocked, attempt a doomed re-lock, and deny. The same parse bug
        repeats on the diagnostic reread, which reports "already clear
        again", so the deny is real and the exit code and empty stdout
        suffice here. A second worktree in the repo exercises the porcelain
        parser's cross-record behavior."""
        repo = tmp_path / "notlast-repo"
        init_git_repo_with_commit(repo)
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

    def test_resumed_session_dead_pid_self_recognized_via_session_id(self, isolated_home, tmp_path):
        """A resumed session (`claude --continue`/`--resume`) keeps its
        session_id but gets a new PID. A lock naming the dead pre-resume pid,
        with no registry entry for it and the caller's own session_id, is
        recognized by session_id. A dead-pid lock that reached `kill -0` would
        be reclaimed and the guard would also exit 0, so the exit code and
        empty stdout alone cannot tell recognition from reclaim. The
        byte-identical lock and the absent claim file do."""
        repo = tmp_path / "resume-dead-pid-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "resume-dead-pid-worktree"
        _add_worktree(repo, wt_path, "resume-dead-pid")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "resume-dead-pid-session")
        pre_resume_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {pre_resume_pid} session resume-dead-pid-session")
        lock_before = _locked_file_bytes(wt_path)

        result = _run_collision_guard(wt_path, common_dir, isolated_home)

        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert _locked_file_bytes(wt_path) == lock_before
        claims = list(Path(_git_dir(wt_path)).glob("claude-evicted-lock-*"))
        assert claims == [], f"recognizing the lock must not reclaim it, found {claims}"

    def test_resumed_session_reused_live_pid_self_recognized_via_session_id(
        self, isolated_home, tmp_path, live_pid
    ):
        """The other half of the resume regression: the pre-resume PID
        gets reused by an unrelated live process (live_pid stands in for
        it). Self-recognition via session_id must short-circuit before
        the kill -0 check, which would otherwise misreport this
        live-but-foreign PID as 'already in use by a live Claude Code
        session'."""
        repo = tmp_path / "resume-live-pid-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "resume-live-pid-worktree"
        _add_worktree(repo, wt_path, "resume-live-pid")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "resume-live-pid-session")
        _lock_worktree(wt_path, f"claude-code pid {live_pid} session resume-live-pid-session")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_cleared_session_same_pid_lock_recognized_without_rewrite(self, isolated_home, tmp_path):
        """A lock recording the caller's own pid is the caller's, whatever
        session_id it names: `/clear` gives the same process a new
        session_id, so the pid is the identity that survives it. The lock is
        read, never rewritten. The byte-identical lock shows nothing was
        rewritten, and the single porcelain read shows the first read
        recognized the lock, with no write attempt and no diagnostic reread.
        The known-gaps bullet on PID reuse in _lib.sh names the input this
        rule allows wrongly. Closing that bullet has to change this test."""
        repo = tmp_path / "cleared-same-pid-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "cleared-same-pid-worktree"
        _add_worktree(repo, wt_path, "cleared-same-pid")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "cleared-successor-session")
        _lock_worktree(wt_path, f"claude-code pid {os.getpid()} session cleared-predecessor-session")
        lock_before = _locked_file_bytes(wt_path)
        counting_env, counter_file = _install_porcelain_counting_git(tmp_path, wt_path)

        result = _run_collision_guard(wt_path, common_dir, isolated_home, extra_env=counting_env)

        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert _locked_file_bytes(wt_path) == lock_before
        assert int(counter_file.read_text()) == 1

    def test_resumed_session_denied_while_old_pid_runs_another_session(
        self, isolated_home, tmp_path, live_pid
    ):
        """A lock whose pid is a live registered Claude Code process is not
        the caller's by session_id alone, even when the caller carries the
        lock's session_id. Here the old process was cleared and still runs a
        different session. The resumed session is denied while the lock's old
        PID is a live Claude Code process, as the known-gaps list in _lib.sh
        records; `git worktree unlock` clears it once no live process works in
        the worktree."""
        repo = tmp_path / "resume-old-pid-other-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "resume-old-pid-other-session-worktree"
        _add_worktree(repo, wt_path, "resume-old-pid-other-session")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "cleared-successor-session", pid=live_pid)
        _seed_session(isolated_home, "resumed-session")
        _lock_worktree(wt_path, f"claude-code pid {live_pid} session resumed-session")
        lock_before = _locked_file_bytes(wt_path)

        result = _run_collision_guard(wt_path, common_dir, isolated_home)

        assert result.returncode == 1
        assert f"live Claude Code session (pid {live_pid})" in result.stdout, result.stdout
        assert _locked_file_bytes(wt_path) == lock_before

    def test_resumed_session_denied_while_old_pid_runs_same_session(
        self, isolated_home, tmp_path, live_pid
    ):
        """The old process still runs the lock's own conversation, and a
        second process resumes it. Two live processes on one conversation
        is the other shape of that denial, and the guard denies the resumed one
        naming the old pid."""
        repo = tmp_path / "resume-old-pid-same-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "resume-old-pid-same-session-worktree"
        _add_worktree(repo, wt_path, "resume-old-pid-same-session")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "resumed-session", pid=live_pid)
        _seed_session(isolated_home, "resumed-session")
        _lock_worktree(wt_path, f"claude-code pid {live_pid} session resumed-session")
        lock_before = _locked_file_bytes(wt_path)

        result = _run_collision_guard(wt_path, common_dir, isolated_home)

        assert result.returncode == 1
        assert f"live Claude Code session (pid {live_pid})" in result.stdout, result.stdout
        assert _locked_file_bytes(wt_path) == lock_before

    def test_resumed_session_with_leftover_entry_for_exited_pid_keeps_lock_without_reclaim(
        self, isolated_home, tmp_path
    ):
        """Nothing deletes `sessions/<pid>` on exit, so a resumed session's
        old pid is typically dead with its registry entry still on disk. The
        entry's two lines are non-empty and its only defect is the dead pid,
        so the session_id match still recognizes the lock. The claim file is
        what tells recognition from a reclaim: none may exist, and the lock
        stays byte-identical. The single porcelain read shows the first read
        recognized the lock, with no write attempt and no diagnostic reread."""
        repo = tmp_path / "resume-leftover-entry-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "resume-leftover-entry-worktree"
        _add_worktree(repo, wt_path, "resume-leftover-entry")
        common_dir = _git_common_dir(repo)
        dead_pid = _dead_pid()
        _seed_session(
            isolated_home, "cleared-successor-session", pid=dead_pid, start_time=_FIXED_START_TIME
        )
        _seed_session(isolated_home, "resumed-session")
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session resumed-session")
        lock_before = _locked_file_bytes(wt_path)
        counting_env, counter_file = _install_porcelain_counting_git(tmp_path, wt_path)

        result = _run_collision_guard(
            wt_path, common_dir, isolated_home, extra_env=counting_env
        )

        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert _locked_file_bytes(wt_path) == lock_before
        claims = list(Path(_git_dir(wt_path)).glob("claude-evicted-lock-*"))
        assert claims == [], f"recognizing the lock must not reclaim it, found {claims}"
        assert int(counter_file.read_text()) == 1

    def test_resumed_session_with_old_pid_unregistered_allows_under_nounset(
        self, isolated_home, tmp_path, live_pid
    ):
        """A lock from another PID carrying the caller's session_id makes the
        guard read the registry for a pid that has no entry. The
        git-writes hook runs `set -u` before sourcing _lib.sh, so the read must
        not touch an unset variable: the lock stays the caller's, with no
        error output."""
        repo = tmp_path / "resume-unregistered-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "resume-unregistered-worktree"
        _add_worktree(repo, wt_path, "resume-unregistered")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "resumed-session")
        _lock_worktree(wt_path, f"claude-code pid {live_pid} session resumed-session")

        result = _run_collision_guard(wt_path, common_dir, isolated_home, prelude="set -u;")

        assert result.returncode == 0, result.stdout
        assert result.stdout == ""
        assert result.stderr == ""

    def test_old_format_lock_no_session_field_falls_back_to_pid_match(self, isolated_home, tmp_path):
        """A pre-upgrade lock reason with no ` session <ID>` field still
        self-recognizes via PID match -- the fallback this fix must
        preserve for a lock acquired before this fix shipped."""
        repo = tmp_path / "old-format-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "old-format-worktree"
        _add_worktree(repo, wt_path, "old-format")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "old-format-session")
        _lock_worktree(wt_path, f"claude-code pid {os.getpid()}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_truncated_session_token_lock_denies_with_manual_remedy(self, isolated_home, tmp_path):
        """A lock reason whose session field is truncated to just the
        `session` keyword (e.g. a `locked` file write killed mid-flight
        by the 5s _lib_capped timeout) is unparseable -- denies with the
        same manual remedy as any other malformed reason, never a false
        self-match even though the pid field alone matches this
        session's own pid."""
        repo = tmp_path / "truncated-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "truncated-session-worktree"
        _add_worktree(repo, wt_path, "truncated-session")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "truncated-session-session")
        _lock_worktree(wt_path, f"claude-code pid {os.getpid()} session")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert "git worktree unlock" in result.stdout
        assert _worktree_lock_reason(wt_path) is not None, "guard must not auto-evict"

    def test_foreign_live_lock_denies_naming_pid(self, isolated_home, tmp_path, live_pid):
        repo = tmp_path / "foreign-live-repo"
        init_git_repo_with_commit(repo)
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

    def test_new_format_foreign_session_id_lock_denies_naming_pid(self, isolated_home, tmp_path, live_pid):
        """A different security invariant than the old-format case above:
        a new-format lock (carries a session_id) belonging to a genuinely
        different, live session must still deny -- proves the session_id
        equality check doesn't degrade into a presence check that would
        let any session self-match any other session's new-format lock."""
        repo = tmp_path / "foreign-session-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "foreign-session-worktree"
        _add_worktree(repo, wt_path, "foreign-session")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "foreign-session-my-session")
        foreign_pid = live_pid
        _lock_worktree(wt_path, f"claude-code pid {foreign_pid} session foreign-session-their-session")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert str(foreign_pid) in result.stdout
        assert "live" in result.stdout

    def test_foreign_dead_lock_auto_evicted_and_reclaimed(self, isolated_home, tmp_path):
        """A lock naming a pid that is no longer running is auto-evicted
        and re-acquired for this session within the same guard call. See
        .claude/plans/worktree-collision-guard.md:86-108 for the
        counterexample the prior no-auto-evict design was rejected over,
        and .claude/plans/auto-clear-dead-worktree-locks.md for the
        release-free claim that closes that race now."""
        repo = tmp_path / "foreign-dead-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "foreign-dead-worktree"
        _add_worktree(repo, wt_path, "foreign-dead")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "foreign-dead-session")
        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session foreign-dead-their-session")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {os.getpid()}" in reason

    def test_unparseable_reason_lock_denies_with_manual_remedy(self, isolated_home, tmp_path):
        repo = tmp_path / "unparseable-repo"
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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
        init_git_repo_with_commit(repo)
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


class TestDeadLockReclaim:
    """Coverage for the claim-file protocol behind a dead lock's auto-
    eviction (_lib_worktree_reclaim_dead_lock, _lib.sh) beyond the ordinary
    success/deny cases already covered in TestCollisionGuardBranches and
    the race cases in TestConcurrentLockRace/TestCollisionGuardRereadRace.
    See .claude/plans/auto-clear-dead-worktree-locks.md 'Critical files'
    for the case list this class covers."""

    def test_old_format_foreign_dead_lock_auto_evicted(self, isolated_home, tmp_path):
        """The evictable half of must-not-regress #3: an old-format lock
        (no session field, predating session-id keying) naming a dead pid
        is auto-evicted and reclaimed exactly like a new-format one, via
        the `nosession` claim-filename branch."""
        repo = tmp_path / "old-format-dead-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "old-format-dead-worktree"
        _add_worktree(repo, wt_path, "old-format-dead")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "old-format-dead-session")
        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {os.getpid()}" in reason
        wt_git_dir = _git_dir(wt_path)
        assert Path(wt_git_dir, f"claude-evicted-lock-{dead_pid}-nosession").exists()

    def test_new_format_dead_lock_with_pre_existing_claim_denies_lock_untouched(self, isolated_home, tmp_path):
        """The once-only property (M1): a claim already on disk for this
        exact (pid, session_id) identity is never reused. Pre-creating the
        claim file before the guard ever runs proves the guard's own
        noclobber create is what enforces the once-only bound, not
        in-process state carried from a single call."""
        repo = tmp_path / "preclaim-new-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "preclaim-new-worktree"
        _add_worktree(repo, wt_path, "preclaim-new")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "preclaim-new-session")
        dead_pid = _dead_pid()
        dead_session_id = "preclaim-new-dead-session"
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session {dead_session_id}")
        wt_git_dir = _git_dir(wt_path)
        Path(wt_git_dir, f"claude-evicted-lock-{dead_pid}-{dead_session_id}").write_text(
            "claimed by claude-code pid 1\n"
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert "could not be cleared automatically" in result.stdout, result.stdout
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {dead_pid}" in reason, "a burnt claim must leave the original lock untouched"

    def test_old_format_dead_lock_with_pre_existing_claim_denies_lock_untouched(self, isolated_home, tmp_path):
        """The `nosession` claim-filename branch's own denial path --
        untested by the new-format case above, whose claim file is named
        with a real session id rather than the `nosession` placeholder an
        old-format lock's claim uses."""
        repo = tmp_path / "preclaim-old-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "preclaim-old-worktree"
        _add_worktree(repo, wt_path, "preclaim-old")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "preclaim-old-session")
        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid}")
        wt_git_dir = _git_dir(wt_path)
        Path(wt_git_dir, f"claude-evicted-lock-{dead_pid}-nosession").write_text(
            "claimed by claude-code pid 1\n"
        )

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        assert "could not be cleared automatically" in result.stdout, result.stdout
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {dead_pid}" in reason, "a burnt claim must leave the original lock untouched"

    def test_sequential_reclaim_then_repeat_lock_with_same_dead_identity_denies(self, isolated_home, tmp_path):
        """The claim is enforced by a fresh invocation reading disk state,
        not only checked in-process during the original reclaim: a real
        reclaim runs to completion, then the worktree is re-locked with the
        EXACT same (dead-pid, dead-session) identity the first reclaim
        already burnt a claim for -- not the reclaiming caller's own lock.
        A second guard call must deny, since the once-only right for that
        identity was already spent."""
        repo = tmp_path / "sequential-reclaim-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "sequential-reclaim-worktree"
        _add_worktree(repo, wt_path, "sequential-reclaim")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "sequential-reclaim-session")
        dead_pid = _dead_pid()
        dead_session_id = "sequential-reclaim-dead-session"
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session {dead_session_id}")

        first_result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert first_result.returncode == 0
        wt_git_dir = _git_dir(wt_path)
        claim_path = Path(wt_git_dir, f"claude-evicted-lock-{dead_pid}-{dead_session_id}")
        assert claim_path.exists(), "the first reclaim must leave its claim file behind"

        subprocess.run(
            ["git", "-C", str(wt_path), "worktree", "unlock", str(wt_path)], check=True
        )
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session {dead_session_id}")

        second_result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert second_result.returncode == 1
        assert "could not be cleared automatically" in second_result.stdout, second_result.stdout
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {dead_pid}" in reason

    def test_foreign_live_lock_creates_no_claim_file(self, isolated_home, tmp_path, live_pid):
        """Must-not-regress #2 at the claim-file level: a live-foreign-lock
        deny never reaches the claim-gated eviction branch at all, so no
        claim file is ever created for it."""
        repo = tmp_path / "live-noclaim-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "live-noclaim-worktree"
        _add_worktree(repo, wt_path, "live-noclaim")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "live-noclaim-session")
        foreign_pid = live_pid
        _lock_worktree(wt_path, f"claude-code pid {foreign_pid}")

        result = _run_collision_guard(wt_path, common_dir, isolated_home)
        assert result.returncode == 1
        wt_git_dir = _git_dir(wt_path)
        claims = list(Path(wt_git_dir).glob("claude-evicted-lock-*"))
        assert claims == [], f"a live-foreign deny must create no claim file, found {claims}"

    def test_claim_file_present_does_not_affect_git_worktree_interop(self, isolated_home, tmp_path):
        """git treats a claim file as an unknown file in the worktree's
        admin dir and ignores it entirely -- `list --porcelain`, `unlock`,
        `prune`, and `remove` all behave identically whether or not one is
        present."""
        repo = tmp_path / "interop-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "interop-worktree"
        _add_worktree(repo, wt_path, "interop")
        wt_git_dir = _git_dir(wt_path)
        _lock_worktree(wt_path, "claude-code pid 999999")
        claim_path = Path(wt_git_dir, "claude-evicted-lock-999999-nosession")
        claim_path.write_text("claimed by claude-code pid 1\n")

        porcelain = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert str(wt_path) in porcelain
        assert "locked" in porcelain

        subprocess.run(
            ["git", "-C", str(wt_path), "worktree", "unlock", str(wt_path)], check=True
        )
        assert _worktree_lock_reason(wt_path) is None

        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=True)
        assert claim_path.exists(), "prune must not touch an unrelated file in the admin dir"

        subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(wt_path)], check=True)
        final_porcelain = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert str(wt_path) not in final_porcelain

    def test_reclaim_confirm_reread_mismatch_denies_naming_stale_dead_pid(self, isolated_home, tmp_path):
        """Pins the Known-gaps bullet in _lib.sh's header comment above
        _lib_worktree_collision_guard (search "collapses"): when the
        reclaim's own post-reacquire confirm-reread (the third porcelain
        call on this path, after the self-lock check and the diagnosis
        read) reports a lock naming neither this caller's own pid nor
        session, the raw lock file on disk has already been evicted and
        rewritten as this caller's own, unconfirmed lock.
        _lib_worktree_reclaim_dead_lock's exit-code collapse
        (`_lib_worktree_acquire_lock ... && return 0; return 1`) reports
        this identically to a genuine write failure, so the caller's deny
        message names the original stale dead pid rather than reflecting
        that its own lock was actually just written. Forced deterministically
        via a `git` wrapper that overrides only the THIRD `worktree list
        --porcelain` call's output with an unrelated lock reason, leaving
        the real noclobber write (which this wrapper never touches) to
        succeed for real."""
        repo = tmp_path / "confirm-mismatch-repo"
        init_git_repo_with_commit(repo)
        wt_path = tmp_path / "confirm-mismatch-worktree"
        _add_worktree(repo, wt_path, "confirm-mismatch")
        common_dir = _git_common_dir(repo)
        _seed_session(isolated_home, "confirm-mismatch-session")

        dead_pid = _dead_pid()
        _lock_worktree(wt_path, f"claude-code pid {dead_pid} session confirm-mismatch-dead-session")

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
  if [ "$count" -eq 3 ]; then
    printf 'worktree {wt_path}\\nlocked claude-code pid 424242 session unrelated-third-party-session\\n'
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
        assert result.returncode == 1
        assert "could not be cleared automatically" in result.stdout, result.stdout
        assert f"pid {dead_pid}" in result.stdout, (
            "the deny message must name the original stale dead pid, not "
            "the unconfirmed lock the reclaim actually just wrote"
        )
        reason = _worktree_lock_reason(wt_path)
        assert reason is not None
        assert f"pid {os.getpid()}" in reason, (
            "the raw lock file was actually evicted and rewritten as this "
            "caller's own lock, despite the deny message above claiming "
            "otherwise -- exactly the misleading-message gap this test pins"
        )
        assert int(counter_file.read_text()) == 3, (
            "the wrapper's own counter must have observed all three "
            "porcelain calls (self-lock check, diagnosis, reclaim's own "
            "confirm-reread) for this to target the intended call"
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

    def test_locked_with_old_format_pid_only_prints_trailing_separator_space(self):
        """The exact-string counterpart to test_locked_with_parseable_pid
        above (which only checks the stripped pid): an old-format reason
        with no session field still prints the separating space before an
        empty session_id, so a caller's `read -r pid session_id <<< "$out"`
        never depends on the writer having emitted a trailing token."""
        porcelain = (
            "worktree /some/path\n"
            "HEAD abc123\n"
            "branch refs/heads/feature\n"
            "locked claude-code pid 4242\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 0
        assert result.stdout == "4242 "

    def test_locked_with_pid_and_session_id(self):
        """The new-format reason parses both fields, distinct from
        test_locked_with_parseable_pid above (old format, no session
        field)."""
        porcelain = (
            "worktree /some/path\n"
            "HEAD abc123\n"
            "branch refs/heads/feature\n"
            "locked claude-code pid 4242 session abc-123_DEF\n"
        )
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 0
        assert result.stdout == "4242 abc-123_DEF"

    def test_locked_with_truncated_session_token_returns_2(self):
        """A `session` keyword with no id after it (e.g. a `locked` file
        write killed mid-flight by the 5s _lib_capped timeout) fails the
        session-id character-class capture -- the whole reason is
        unparseable, not a partial session match."""
        porcelain = "worktree /some/path\nlocked claude-code pid 4242 session\n"
        result = _run_lock_pid("/some/path", porcelain)
        assert result.returncode == 2
        assert result.stdout == ""

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

    def test_invalid_entry_at_parent_and_valid_entry_at_grandparent_prints_grandparent_pid(
        self, isolated_home
    ):
        """The walk skips an untrusted entry at the immediate ancestor and
        prints the pid of the deeper ancestor that validates, not the
        starting $PPID. Same shape as test_marker_script.py's
        test_skips_invalid_immediate_ancestor_and_resolves_deeper_one, at the
        function level with the printed pid asserted."""
        grandparent_pid = os.getppid()
        if grandparent_pid <= 1:
            pytest.skip("test runner was reparented; the resolver walk stops at pid 1")
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / str(os.getpid())).write_text(f"skipped-mismatch\n{_FIXED_START_TIME}\n")
        sid = "grandparent-session"
        _seed_session(isolated_home, sid, pid=grandparent_pid)

        result = _run_resolve_claude_pid(isolated_home)

        assert result.returncode == 0
        assert result.stdout == f"{sid} {grandparent_pid}"


def _assert_lock_is_mine_result(result: subprocess.CompletedProcess, expected_returncode: int) -> None:
    """Exact return code with empty stdout and stderr: under `set -u` an
    unbound-variable abort also exits 1, so a bare return-code check cannot
    tell a deliberate "not mine" from a crash."""
    assert result.returncode == expected_returncode, (result.returncode, result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr == ""


class TestWorktreeLockIsMine:
    """Each row of _lib_worktree_lock_is_mine's decision matrix, run through
    _run_lock_is_mine with a fresh $HOME. A row that needs a live "other"
    pid uses this test process's own pid as the lock's pid, because the
    predicate takes the caller's pid as an argument."""

    def test_same_pid_with_different_session_is_mine(self, isolated_home):
        own_pid = str(os.getpid())
        result = _run_lock_is_mine(isolated_home, own_pid, "lock-session", own_pid, "caller-session")
        _assert_lock_is_mine_result(result, 0)

    def test_same_pid_with_old_format_lock_without_session_is_mine(self, isolated_home):
        own_pid = str(os.getpid())
        result = _run_lock_is_mine(isolated_home, own_pid, "", own_pid, "caller-session")
        _assert_lock_is_mine_result(result, 0)

    def test_other_pid_dead_unregistered_with_empty_sessions_is_not_mine(self, isolated_home):
        result = _run_lock_is_mine(isolated_home, str(_dead_pid()), "", _OTHER_CALLER_PID, "")
        _assert_lock_is_mine_result(result, 1)

    def test_other_pid_with_different_session_is_not_mine(self, isolated_home):
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "lock-session", _OTHER_CALLER_PID, "caller-session"
        )
        _assert_lock_is_mine_result(result, 1)

    def test_same_session_other_pid_dead_with_leftover_entry_is_mine(self, isolated_home):
        dead_pid = _dead_pid()
        _seed_session(isolated_home, "leftover-owner-session", pid=dead_pid, start_time=_FIXED_START_TIME)
        result = _run_lock_is_mine(
            isolated_home, str(dead_pid), "shared-session", _OTHER_CALLER_PID, "shared-session"
        )
        _assert_lock_is_mine_result(result, 0)

    def test_same_session_other_pid_live_registered_to_same_session_is_not_mine(self, isolated_home):
        _seed_session(isolated_home, "shared-session")
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session"
        )
        _assert_lock_is_mine_result(result, 1)

    def test_same_session_other_pid_live_registered_to_another_session_is_not_mine(self, isolated_home):
        _seed_session(isolated_home, "cleared-successor-session")
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session"
        )
        _assert_lock_is_mine_result(result, 1)

    def test_same_session_other_pid_live_with_stale_start_time_is_mine(self, isolated_home):
        _seed_session(isolated_home, "stale-owner-session", start_time=_FIXED_START_TIME)
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session"
        )
        _assert_lock_is_mine_result(result, 0)

    def test_same_session_other_pid_live_with_one_line_entry_is_mine(self, isolated_home):
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text("one-line-entry-session\n")
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session"
        )
        _assert_lock_is_mine_result(result, 0)

    def test_same_session_other_pid_live_registered_but_ps_fails_is_mine(
        self, isolated_home, tmp_path, live_pid
    ):
        """A `ps` failure leaves liveness unconfirmed, so the registered live
        holder reads as gone. This is the fail-open outcome the known-gaps
        list in _lib.sh records."""
        _seed_session(isolated_home, "cleared-successor-session", pid=live_pid)
        failing_ps_env, ps_invoked_marker = _install_failing_ps(tmp_path)
        result = _run_lock_is_mine(
            isolated_home, str(live_pid), "shared-session", _OTHER_CALLER_PID, "shared-session",
            extra_env=failing_ps_env,
        )
        _assert_lock_is_mine_result(result, 0)
        assert ps_invoked_marker.exists(), "the stub ps must have been the one the registry read ran"

    def test_registry_under_non_default_config_dir_is_read(self, isolated_home, tmp_path):
        alt_base = tmp_path / "alt-base"
        _seed_session(alt_base, "shared-session")
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session",
            config_dir_env=str(alt_base / ".claude"),
        )
        _assert_lock_is_mine_result(result, 1)

    def test_registry_only_under_home_is_not_read_when_config_dir_is_non_default(
        self, isolated_home, tmp_path
    ):
        _seed_session(isolated_home, "shared-session")
        alt_base = tmp_path / "alt-base"
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session",
            config_dir_env=str(alt_base / ".claude"),
        )
        _assert_lock_is_mine_result(result, 0)

    def test_relative_config_dir_with_same_session_other_pid_is_not_mine(self, isolated_home):
        """_lib_config_dir fails for a relative CLAUDE_CONFIG_DIR, and the
        predicate treats a failed config-dir resolution as not mine."""
        result = _run_lock_is_mine(
            isolated_home, str(os.getpid()), "shared-session", _OTHER_CALLER_PID, "shared-session",
            config_dir_env="relative/config-dir",
        )
        _assert_lock_is_mine_result(result, 1)

    def test_relative_config_dir_with_same_pid_is_mine_because_own_pid_check_precedes_config_read(
        self, isolated_home
    ):
        own_pid = str(os.getpid())
        result = _run_lock_is_mine(
            isolated_home, own_pid, "lock-session", own_pid, "caller-session",
            config_dir_env="relative/config-dir",
        )
        _assert_lock_is_mine_result(result, 0)

    def test_both_pids_empty_is_not_mine(self, isolated_home):
        result = _run_lock_is_mine(isolated_home, "", "shared-session", "", "shared-session")
        _assert_lock_is_mine_result(result, 1)

    def test_empty_caller_pid_with_matching_session_and_dead_unregistered_lock_pid_is_not_mine(
        self, isolated_home
    ):
        result = _run_lock_is_mine(isolated_home, str(_dead_pid()), "shared-session", "", "shared-session")
        _assert_lock_is_mine_result(result, 1)

    def test_empty_lock_pid_with_matching_session_is_not_mine(self, isolated_home):
        result = _run_lock_is_mine(isolated_home, "", "shared-session", _OTHER_CALLER_PID, "shared-session")
        _assert_lock_is_mine_result(result, 1)


class TestRegisteredLiveSid:
    """_lib_registered_live_sid prints the registered session_id and returns
    0 only for a live, start-time-valid entry, and returns 1 with no stdout
    for every case it cannot confirm."""

    def test_valid_entry_for_live_pid_prints_session_id(self, isolated_home):
        _seed_session(isolated_home, "registered-session")
        result = _run_registered_live_sid(isolated_home, isolated_home / ".claude", os.getpid())
        assert result.returncode == 0, result.stderr
        assert result.stdout == "registered-session"
        assert result.stderr == ""

    def test_no_entry_returns_1(self, isolated_home):
        result = _run_registered_live_sid(isolated_home, isolated_home / ".claude", os.getpid())
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_one_line_entry_for_dead_pid_returns_1(self, isolated_home):
        dead_pid = _dead_pid()
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(dead_pid)).write_text("one-line-entry-session\n")
        result = _run_registered_live_sid(isolated_home, isolated_home / ".claude", dead_pid)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_stale_start_time_for_live_pid_returns_1(self, isolated_home):
        _seed_session(isolated_home, "stale-start-session", start_time=_FIXED_START_TIME)
        result = _run_registered_live_sid(isolated_home, isolated_home / ".claude", os.getpid())
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_empty_session_id_with_live_start_time_returns_1(self, isolated_home):
        _seed_session(isolated_home, "")
        result = _run_registered_live_sid(isolated_home, isolated_home / ".claude", os.getpid())
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_valid_entry_confirmed_under_non_utc_caller_timezone(self, isolated_home):
        """The registry read pins the `ps` timezone to UTC, so a caller
        running under another TZ still matches the start time the entry
        recorded."""
        _seed_session(isolated_home, "non-utc-session")
        result = _run_registered_live_sid(
            isolated_home, isolated_home / ".claude", os.getpid(), extra_env={"TZ": "PST8"}
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "non-utc-session"
        assert result.stderr == ""

    def test_leftover_entry_for_dead_pid_returns_1(self, isolated_home):
        dead_pid = _dead_pid()
        _seed_session(isolated_home, "leftover-session", pid=dead_pid, start_time=_FIXED_START_TIME)
        result = _run_registered_live_sid(isolated_home, isolated_home / ".claude", dead_pid)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_ps_failure_for_live_registered_pid_returns_1(self, isolated_home, tmp_path, live_pid):
        _seed_session(isolated_home, "registered-session", pid=live_pid)
        failing_ps_env, ps_invoked_marker = _install_failing_ps(tmp_path)
        result = _run_registered_live_sid(
            isolated_home, isolated_home / ".claude", live_pid, extra_env=failing_ps_env
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""
        assert ps_invoked_marker.exists(), "the stub ps must have been the one the registry read ran"
