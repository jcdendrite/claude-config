"""Shared pytest fixtures auto-discovered across the hook test suite.

Only fixtures used by two or more test files live here. Class-local
fixtures stay with their class in the per-hook test file. `_seed_session`
is a plain helper function rather than a fixture (matching
scripts/tests/conftest.py's convention) since callers need to pass a
per-test session id, not a fixed injected value; import it directly with
`from .conftest import _seed_session`.
"""
from __future__ import annotations

import functools
import hashlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    git_toplevel,
    init_git_repo_with_commit,
    scaled_shim_sleep,
    symlink_hooks_lib_chain,
    write_scaled_timeout_shim,
)

# Shim sleep duration for the git/gh-timeout regression tests below: long
# enough that a broken (uncapped) call site never returns before the
# test's own timeout.
TIMEOUT_SHIM_SLEEP_SECONDS = 10


@functools.cache
def _real_timeout_is_gnu_coreutils() -> bool:
    """True when the `timeout`/`gtimeout` that write_scaled_timeout_shim wraps
    is GNU coreutils. BusyBox's has no `--version`, so its usage text lacks
    the marker. Only the sigterm_immune cases depend on GNU behavior. This
    guard skips them under a non-GNU timeout rather than fail misleadingly.
    It does not make the rest of the suite BusyBox-supported."""
    real_timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if real_timeout is None:
        return False
    result = subprocess.run([real_timeout, "--version"], capture_output=True, text=True, check=False)
    return "GNU coreutils" in result.stdout + result.stderr


def _seed_session(home: Path, session_id: str, pid: int | None = None) -> None:
    """Write $HOME/.claude/sessions/<pid> in the two-line format
    capture-session-id.sh writes: the session id, then that pid's
    `TZ=UTC LC_ALL=C ps -o lstart=` start time. The start time is captured
    the same way the writer's `$(...)` does — trailing newline stripped,
    other trailing whitespace preserved — so a seeded entry round-trips
    through marker.sh's _walk_session comparison exactly like a real
    capture-session-id.sh write would.

    pid defaults to this test process's own pid: marker.sh resolves its
    session id by walking process ancestors, and when it runs as a
    subprocess of pytest, that walk reaches the pytest process itself.
    """
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


def _dead_pid() -> int:
    """Return a pid that is guaranteed not to be running. Mirrors
    scripts/tests/conftest.py's helper of the same name, duplicated rather
    than shared so neither test tree's conftest imports the other's —
    spawns and reaps a real process so the returned pid is a genuine,
    just-exited one rather than a guessed high number that might collide
    with something still alive."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


_DEFAULT_BRANCH_CANDIDATES = ("main", "master", "develop")


def _git_stdout(repo: Path, *args: str) -> str:
    """Stripped stdout of `git *args` in `repo`, or "" when git exits nonzero."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _review_ledger_path(home: Path, repo: Path, session_id: str) -> Path:
    """The file review-ledger.sh appends to for `repo`'s current HEAD, from
    the documented key rules alone (not _lib_review_ledger_path, so a test
    using this doesn't check that function against itself).

    Branch scope, `<sha256(toplevel)>.<sha256(branch)>.jsonl`, applies on a
    named branch other than the default. Session scope,
    `<sha256(toplevel)>.<session_id>.jsonl`, applies on a detached HEAD and
    on the default branch. The default branch is origin/HEAD's target when
    it resolves, else the first candidate with an origin ref, else any
    branch named like a candidate.
    """
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    ledger_dir = home / ".claude" / "review-narrative-ledger"
    branch = _git_stdout(repo, "symbolic-ref", "-q", "--short", "HEAD")
    if not branch:
        return ledger_dir / f"{repo_hash}.{session_id}.jsonl"
    origin_head = _git_stdout(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    default_branch = None
    if origin_head and _git_stdout(repo, "rev-parse", "--verify", "--quiet", origin_head):
        default_branch = origin_head.removeprefix("refs/remotes/origin/")
    else:
        default_branch = next(
            (
                candidate for candidate in _DEFAULT_BRANCH_CANDIDATES
                if _git_stdout(repo, "rev-parse", "--verify", "--quiet", f"origin/{candidate}")
            ),
            None,
        )
    on_default_branch = branch == default_branch if default_branch else branch in _DEFAULT_BRANCH_CANDIDATES
    if on_default_branch:
        return ledger_dir / f"{repo_hash}.{session_id}.jsonl"
    branch_hash = hashlib.sha256(branch.encode()).hexdigest()
    return ledger_dir / f"{repo_hash}.{branch_hash}.jsonl"


@pytest.fixture
def live_pid():
    """Yield the pid of a real process this fixture owns for the test's
    duration, terminated on teardown. A safe stand-in for "some other live
    session" in a foreign-live-lock test — unlike os.getppid(), it doesn't
    depend on this test process's own ancestry staying stable for the
    assertion window, which a detached or reparented test-runner process
    supervision setup can't guarantee (see _dead_pid's analogous
    genuine-process-over-guessed-number rationale)."""
    proc = subprocess.Popen(["sleep", "3600"])
    try:
        yield proc.pid
    finally:
        proc.terminate()
        proc.wait()


def _worktree_lock_reason(worktree: Path) -> str | None:
    """Return the `locked <reason>` porcelain line for `worktree`, or None
    when it is unlocked. Test-only companion to _lib_worktree_lock_pid
    (_lib.sh), which parses the same `git worktree list --porcelain` text
    inside the hook; kept independent (not shelling out to the library
    function) so a test using this to assert lock state isn't checking the
    function under test against itself."""
    porcelain = subprocess.run(
        ["git", "-C", str(worktree), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    in_target = False
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            in_target = line[len("worktree "):] == str(worktree)
        elif line.startswith("locked") and in_target:
            return line
    return None


# A name review-pr-checkout.sh's worktree template could expand to.
REVIEW_WORKTREE_NAME = "review-pr-sess-1-42-abc123"


def _add_worktree_under_worktrees_dir(
    repo: Path, name: str, branch: str | None = None, worktrees_root: Path | None = None
) -> Path:
    """A worktree at <worktrees_root or repo>/.claude/worktrees/<name>: detached
    by default, as review-pr-checkout.sh creates one, or on a new branch."""
    worktree = (worktrees_root or repo) / ".claude" / "worktrees" / name
    worktree.parent.mkdir(parents=True, exist_ok=True)
    mode_args = ["-b", branch] if branch else ["--detach"]
    subprocess.run(
        ["git", "worktree", "add", *mode_args, str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return worktree


def _write_conditional_sleep_shim(
    bin_dir: Path,
    binary_name: str,
    real_binary: str,
    match_condition: str,
    fake_output: str | None = None,
    sleep_seconds: int = TIMEOUT_SHIM_SLEEP_SECONDS,
    sigterm_immune: bool = False,
    exit_status: int | None = None,
) -> None:
    """Write a fake `binary_name` under bin_dir that sleeps sleep_seconds
    (a production-cap-scale duration, scaled down by write_scaled_timeout_shim
    before it is written) past the cap under test when `match_condition`
    matches, and execs `real_binary` otherwise. Shared conditional-sleep logic
    behind git_timeout_shim and gh_timeout_shim. sleep_seconds defaults to
    TIMEOUT_SHIM_SLEEP_SECONDS (calibrated for the shared 5s _lib_capped
    cap); a caller testing a wider cap (e.g. _lib_cumulative_diff_hash's 15s)
    must pass a larger value or the shim returns before that cap fires.

    When `fake_output` is set, it replaces the exec-real-binary fallback
    after the sleep completes, so a broken cap is observably distinct from
    a working one instead of both converging on the real binary's result.

    sigterm_immune writes `trap '' TERM; exec sleep <n>` in place of a plain
    `sleep <n>`.
    A TERM disposition ignored via `trap ''` propagates to every command the
    shell subsequently invokes, exec'd or not (bash(1)'s TRAP builtin).
    The exec'd `sleep` therefore ignores `timeout`'s SIGTERM, and only the
    `-k` grace-expiry SIGKILL can end it.
    It is off by default, so every existing caller stays byte-identical.
    It is mutually exclusive with fake_output, because the exec replaces this
    shim's own process before any post-sleep block could run.

    exit_status writes `exit <n>` in place of the sleep, so a matching call
    fails at once with an arbitrary status and never engages the cap. It is
    mutually exclusive with fake_output and sigterm_immune, which both
    assume the shim sleeps."""
    assert not (sigterm_immune and fake_output is not None), (
        "sigterm_immune's exec makes the post-sleep fake_output block unreachable"
    )
    assert exit_status is None or (fake_output is None and not sigterm_immune), (
        "exit_status replaces the sleep that fake_output and sigterm_immune both build on"
    )
    if exit_status is not None:
        sleep_line = f"  exit {int(exit_status)}\n"
    else:
        if sleep_seconds > 0 and write_scaled_timeout_shim(bin_dir):
            sleep_seconds = scaled_shim_sleep(sleep_seconds)
        sleep_line = (
            f"  trap '' TERM; exec sleep {sleep_seconds}\n"
            if sigterm_immune
            else f"  sleep {sleep_seconds}\n"
        )
    post_sleep = (
        f"  echo {shlex.quote(fake_output)}\n  exit 0\n" if fake_output is not None else ""
    )
    fake_binary = bin_dir / binary_name
    fake_binary.write_text(
        f"#!/bin/bash\n"
        f"if {match_condition}; then\n"
        f"{sleep_line}"
        f"{post_sleep}"
        f"fi\n"
        f'exec {real_binary} "$@"\n'
    )
    fake_binary.chmod(0o755)


@pytest.fixture
def git_timeout_shim(tmp_path):
    """`install(match_condition)` writes a `git` shim that sleeps past the 5s
    _lib_capped cap when `match_condition` matches, execs the real binary
    otherwise, and returns a PATH-override dict. Shared by
    test_deny_pii_in_commits.py and test_require_ready_for_review.py.

    `match_condition` is a `[ ... ]`/`[[ ... ]]` test expression, e.g.
    `[ "$1" = "diff" ]` or `[ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]`. A
    `$N`-pinned predicate is coupled to the target call site's argv shape
    (e.g. a `-C <dir>` prefix shifts every position) and must be re-audited
    whenever that call site's argv shape changes.

    Skips when `git` is absent, or when neither `timeout(1)` nor
    `gtimeout(1)` is available (stock macOS ships neither without Homebrew
    coreutils).

    The PATH dict is built inside `install`, not at fixture setup, so it
    reads `os.environ["PATH"]` at the test's own call time. A caller that
    prepends its own bin dir via `monkeypatch.setenv` beforehand (e.g.
    fake_gh_pr_exists) must call `monkeypatch.setenv` before calling
    `install`, so that ordering is preserved.

    `install`'s optional `fake_output` passes through to
    _write_conditional_sleep_shim, for a call site whose real, uncapped
    result would otherwise coincidentally match the timed-out result.

    `install`'s optional `sleep_seconds` is in production-cap units;
    `write_scaled_timeout_shim` scales it down when it writes the shim.
    Pass a larger value than `TIMEOUT_SHIM_SLEEP_SECONDS` to test a wider
    cap.

    `install`'s optional `sigterm_immune` passes through to
    _write_conditional_sleep_shim, for a regression test that needs the
    grace-expiry SIGKILL path (exit 137) rather than the ordinary
    SIGTERM-honored one (exit 124). It skips on a non-GNU `timeout`: the
    scaled shim hands the real binary a sub-second grace, which BusyBox
    truncates to 0 and treats as no kill-after.

    `install`'s optional `exit_status` passes through to
    _write_conditional_sleep_shim, for a regression test that needs a matching
    call to fail with an arbitrary status without engaging the cap.
    """
    real_git = shutil.which("git")
    if not real_git:
        pytest.skip("git not found in PATH")
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    def install(
        match_condition: str,
        fake_output: str | None = None,
        sleep_seconds: int = TIMEOUT_SHIM_SLEEP_SECONDS,
        sigterm_immune: bool = False,
        exit_status: int | None = None,
    ) -> dict[str, str]:
        if sigterm_immune and not _real_timeout_is_gnu_coreutils():
            pytest.skip("scaled sub-second -k grace is truncated to 0 by a non-GNU timeout, so SIGKILL never fires")
        _write_conditional_sleep_shim(
            tmp_path, "git", real_git, match_condition, fake_output, sleep_seconds, sigterm_immune, exit_status
        )
        return {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    return install


@pytest.fixture
def sed_call_counting_shim(tmp_path):
    """`install(fail_after)` writes a `sed` shim that execs the real binary
    for the first `fail_after` invocations and fails (exit 1, no output) on
    every invocation after that.

    Each invocation claims one of the first `fail_after` call numbers by
    creating the lowest unused numbered directory under tmp_path. `mkdir` is
    atomic, so concurrent pipeline stages never claim the same number.

    Calls after the first `fail_after` find no unused number and exit 1.

    The failure is deterministic per call number, not per pipeline stage:
    which of two concurrent stages gets a given number is not fixed. Use
    `sed_split_stage_shim` to fail a specific stage.
    """
    real_sed = shutil.which("sed")
    if not real_sed:
        pytest.skip("sed not found in PATH")

    def install(fail_after: int) -> dict[str, str]:
        counter_dir = tmp_path / "sed-call-count"
        shutil.rmtree(counter_dir, ignore_errors=True)
        counter_dir.mkdir()
        fake_binary = tmp_path / "sed"
        fake_binary.write_text(
            "#!/bin/bash\n"
            "count=1\n"
            f'while [ "$count" -le {fail_after} ] && ! mkdir {shlex.quote(str(counter_dir))}/"$count" 2>/dev/null; do\n'
            "  count=$((count + 1))\n"
            "done\n"
            f"if [ \"$count\" -gt {fail_after} ]; then\n"
            "  exit 1\n"
            "fi\n"
            f'exec {real_sed} "$@"\n'
        )
        fake_binary.chmod(0o755)
        return {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    return install


@pytest.fixture
def sed_split_stage_shim(tmp_path):
    """`install(succeed_first_calls)` writes a `sed` shim that fails only the
    FIRST stage of `_lib_split_fragments`'s two-stage sed pipeline, and only
    after `succeed_first_calls` such invocations succeeded (0 fails the first
    one). Every other sed invocation execs the real binary.

    The shim keys on its script argument (the first stage's script starts
    `s/;/`), not on a global call count: the pipeline's two stages start
    concurrently, so a global counter's order across them is nondeterministic.
    First-stage invocations are one per split and never concurrent, so
    counting only those is deterministic.

    The second stage still succeeds on the empty input a failed first stage
    leaves, so the split reports the failure only under `set -o pipefail`.
    A test built on this shim goes red when a hook drops `pipefail`.
    """
    real_sed = shutil.which("sed")
    if not real_sed:
        pytest.skip("sed not found in PATH")

    def install(succeed_first_calls: int = 0) -> dict[str, str]:
        counter_file = tmp_path / "sed-split-stage-count"
        counter_file.write_text("0")
        fake_binary = tmp_path / "sed"
        fake_binary.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  case "$arg" in\n'
            "    's/;/'*)\n"
            f"      count=$(( $(cat {shlex.quote(str(counter_file))}) + 1 ))\n"
            f"      printf '%s' \"$count\" > {shlex.quote(str(counter_file))}\n"
            f'      if [ "$count" -gt {succeed_first_calls} ]; then\n'
            "        exit 1\n"
            "      fi\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
            f'exec {real_sed} "$@"\n'
        )
        fake_binary.chmod(0o755)
        return {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    return install


@pytest.fixture
def gh_timeout_shim(tmp_path):
    """`install(match_condition)` writes a `gh` shim with the same
    conditional-sleep contract as git_timeout_shim, for regression tests
    against require-ready-for-review.sh's `gh pr view` call. Same skip
    conditions as git_timeout_shim, checking `gh` in place of `git`.

    `install`'s optional `fake_output` passes through to
    _write_conditional_sleep_shim, for a call site whose real, uncapped
    result would otherwise coincidentally match the timed-out result.

    `install`'s optional `sleep_seconds` is in production-cap units;
    `write_scaled_timeout_shim` scales it down when it writes the shim.
    Pass a larger value than `TIMEOUT_SHIM_SLEEP_SECONDS` to test a wider
    cap.
    """
    real_gh = shutil.which("gh")
    if not real_gh:
        pytest.skip("gh not found in PATH")
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    def install(
        match_condition: str,
        fake_output: str | None = None,
        sleep_seconds: int = TIMEOUT_SHIM_SLEEP_SECONDS,
    ) -> dict[str, str]:
        _write_conditional_sleep_shim(
            tmp_path, "gh", real_gh, match_condition, fake_output, sleep_seconds
        )
        return {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    return install


@pytest.fixture(autouse=True)
def _clear_claude_pid_env(monkeypatch):
    """capture-session-id.sh accepts $CLAUDE_PID from its own environment. A
    real Claude Code session running this suite exports it, and the test
    runners below inherit the parent environment wholesale — without this,
    that real value would silently satisfy every test exercising the $PPID
    fallback, masking it. Absent on CI; raising=False matches the
    isolated_home precedent below for a var that may not be set.
    """
    monkeypatch.delenv("CLAUDE_PID", raising=False)


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Sandbox $HOME so the hooks' marker files don't collide with real state."""
    home = tmp_path / "home"
    (home / ".claude" / "code-review-markers").mkdir(parents=True)
    hooks_dir = home / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    symlink_hooks_lib_chain(hooks_dir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def git_repo(tmp_path):
    """Fresh git repo with one committed file and one staged change."""
    # Named explicitly: review-ledger scope depends on the branch name, so it
    # must not follow the machine's init.defaultBranch.
    repo = init_git_repo_with_commit(tmp_path / "repo", file_name="file.txt", content="first\n", branch="main")
    (repo / "file.txt").write_text("first\nsecond\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    return repo


@pytest.fixture
def opted_in_repo(tmp_path):
    """Git repo with .claude/worktree-required committed (opted into
    worktree enforcement)."""
    return init_git_repo_with_commit(
        tmp_path / "opted-in", file_name=".claude/worktree-required", content="# sentinel\n"
    )


@pytest.fixture
def stray_marker_repo(tmp_path):
    """Git repo with .claude/worktree-required present but NOT committed or
    staged — the GH-427 scenario. Enforcement still activates (existence-based
    check, unchanged), but the deny message should carry the stray-marker
    hint since a tracked-marker repo would not."""
    repo = init_git_repo_with_commit(tmp_path / "stray-marker")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-required").write_text("# stray, untracked\n")
    return repo


@pytest.fixture
def staged_marker_repo(tmp_path):
    """Git repo with .claude/worktree-required staged (git add) but not yet
    committed. _lib_stray_marker_hint uses `git ls-files --error-unmatch`,
    which succeeds for staged-not-committed files, not just committed ones —
    this fixture exercises that middle state so the hint's actual gate
    (index-tracked, not HEAD-committed) is what tests pin down."""
    repo = init_git_repo_with_commit(tmp_path / "staged-marker")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-required").write_text("# staged, not committed\n")
    subprocess.run(["git", "add", ".claude/worktree-required"], cwd=repo, check=True)
    return repo


@pytest.fixture
def non_opted_repo(tmp_path):
    """Git repo without the sentinel — enforcement should be a no-op."""
    return init_git_repo_with_commit(tmp_path / "non-opted")


@pytest.fixture
def user_marker_home(isolated_home):
    """Sandboxed $HOME with ~/.claude/worktree-required present.
    Builds on isolated_home so $HOME is set to a temp dir; this fixture
    adds the machine-level marker file. The assertion verifies the write
    succeeded so a typo in the path cannot yield a silently-inert marker.
    """
    marker = isolated_home / ".claude" / "worktree-required"
    marker.write_text("# machine-level sentinel\n")
    assert marker.exists(), f"user_marker_home: marker not written at {marker}"
    return isolated_home


@pytest.fixture
def repo_with_optout(tmp_path):
    """Git repo with .claude/worktree-optout present (but no .claude/worktree-required).
    Used to verify opt-out is an inert modulator, not a trigger."""
    repo = init_git_repo_with_commit(tmp_path / "optout-repo")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-optout").write_text("# opt-out\n")
    return repo


@pytest.fixture
def opted_in_with_worktree(opted_in_repo, tmp_path, isolated_home):
    """Opted-in repo with a linked worktree at a path that does NOT contain
    '/worktrees/' — verifies the hook's worktree check reads git-dir rather
    than pattern-matching the working-tree path. Seeds a session file so
    _lib_worktree_collision_guard can resolve this test process's own PID as
    the lock owner. Every write into this worktree runs the guard. A read
    runs it only when the worktree's lock is already present."""
    wt_path = tmp_path / "feature-tree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt_path)],
        cwd=opted_in_repo,
        check=True,
    )
    _seed_session(isolated_home, "opted-in-with-worktree-session")
    return opted_in_repo, wt_path
