"""Direct, no-subprocess-under-test unit tests for _worktree-lib.sh.

Both cleanup-merged-branches.sh and cleanup-idle-open-pr-worktrees.sh
exercise this library only incidentally, through a full script invocation
against a real git repo. A defect isolated to the library itself — a bug in
collect_process_cwds's OS-detection branch, or in worktree_in_use's
path-matching — would only be caught today if it happened to manifest
identically through both consumers' full test paths. These tests source
_worktree-lib.sh standalone (never through either consumer script) and pin
its contract directly, against synthetic filesystem paths rather than a
real git worktree — collect_process_cwds and worktree_in_use have no git
awareness at all.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_LIB = Path(__file__).parent.parent / "_worktree-lib.sh"


def _run_bash(script_body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source _worktree-lib.sh, then run script_body under `set -euo pipefail`.

    script_body is responsible for capturing any nonzero return from a
    sourced function itself (e.g. `RC=0; worktree_in_use "$p" || RC=$?`) —
    a bare call that returns nonzero would otherwise abort the bash -c
    invocation under set -e before the rest of script_body runs.

    env defaults to None, which subprocess.run passes straight through as
    "inherit the current process environment" — the same behavior every
    pre-existing call site here relies on. Tests that need a PATH-shimmed
    tool pass an explicit env built from os.environ.
    """
    full_script = f'set -euo pipefail\n. "{_LIB}"\n{script_body}\n'
    return subprocess.run(
        ["bash", "-c", full_script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ---------------------------------------------------------------------------
# PATH shims forcing collect_process_cwds's OS-detection branches
# ---------------------------------------------------------------------------

_ALWAYS_FAILS_SOURCE = textwrap.dedent("""\
    #!/usr/bin/env bash
    exit 1
""")


@pytest.fixture()
def readlink_always_fails_path(tmp_path):
    """PATH-prepend directory whose `readlink` shim always fails, forcing
    collect_process_cwds's /proc-based branch (`readlink /proc/self/cwd`)
    to be skipped in favor of the lsof branch — this suite runs on Linux,
    where /proc is otherwise always present, so the lsof branch would
    ship as dead code from CI's perspective without this shim."""
    shim_dir = tmp_path / "readlink_shim"
    shim_dir.mkdir()
    shim = shim_dir / "readlink"
    shim.write_text(_ALWAYS_FAILS_SOURCE)
    shim.chmod(0o755)
    return shim_dir


@pytest.fixture()
def lsof_always_fails_path(tmp_path):
    """PATH-prepend directory whose `lsof` shim always fails (no output,
    non-zero exit), so `command -v lsof` still finds it (the elif branch
    is entered) but it reports zero process cwds — the same observable
    outcome as lsof genuinely being absent."""
    shim_dir = tmp_path / "lsof_shim"
    shim_dir.mkdir()
    shim = shim_dir / "lsof"
    shim.write_text(_ALWAYS_FAILS_SOURCE)
    shim.chmod(0o755)
    return shim_dir


class TestWorktreeInUseIdlePath:
    """A path with no live process cwd'd inside it is reported idle."""

    def test_idle_path_reports_idle(self, tmp_path):
        target = tmp_path / "idle-dir"
        target.mkdir()
        result = _run_bash(f'''
collect_process_cwds
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''')
        assert result.returncode == 0, result.stderr
        assert "exit:1" in result.stdout


class TestWorktreeInUseLiveProcess:
    """A path holding a live process's cwd is reported in use."""

    def test_path_with_live_process_reports_in_use(self, tmp_path):
        target = tmp_path / "live-dir"
        target.mkdir()
        holder = subprocess.Popen(["sleep", "30"], cwd=str(target))
        try:
            result = _run_bash(f'''
collect_process_cwds
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''')
        finally:
            holder.terminate()
            holder.wait()
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout

    def test_subdirectory_of_live_process_reports_in_use(self, tmp_path):
        """A process cwd'd into a subdirectory of the target also counts."""
        target = tmp_path / "live-dir"
        subdir = target / "nested"
        subdir.mkdir(parents=True)
        holder = subprocess.Popen(["sleep", "30"], cwd=str(subdir))
        try:
            result = _run_bash(f'''
collect_process_cwds
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''')
        finally:
            holder.terminate()
            holder.wait()
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout


class TestWorktreeInUseSymlinkCanonicalization:
    """worktree_in_use canonicalizes its target so a symlinked path still
    matches the kernel-canonical cwd string a live process reports."""

    def test_symlinked_target_still_matches_real_cwd(self, tmp_path):
        real_dir = tmp_path / "real-dir"
        real_dir.mkdir()
        symlink = tmp_path / "symlink-to-real"
        symlink.symlink_to(real_dir)
        holder = subprocess.Popen(["sleep", "30"], cwd=str(real_dir))
        try:
            result = _run_bash(f'''
collect_process_cwds
RC=0
worktree_in_use "{symlink}" || RC=$?
echo "exit:$RC"
''')
        finally:
            holder.terminate()
            holder.wait()
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout


class TestCollectProcessCwdsExcludesSelf:
    """collect_process_cwds must exclude its own pid — otherwise a script
    would always read its own worktree as 'in use', even though nothing
    else is using it."""

    def test_scans_ok_and_does_not_self_report(self, tmp_path):
        target = tmp_path / "self-dir"
        target.mkdir()
        result = _run_bash(f'''
cd "{target}"
collect_process_cwds
echo "scan:$PROCESS_CWD_SCAN"
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''')
        assert result.returncode == 0, result.stderr
        assert "scan:ok" in result.stdout
        assert "exit:1" in result.stdout, (
            "the invoking shell's own cwd must not read as 'in use by a live process'"
        )

    def test_scans_ok_and_does_not_self_report_via_lsof_branch(
        self, tmp_path, readlink_always_fails_path
    ):
        """Same self-exclusion requirement, forced through the lsof branch
        specifically (via the readlink shim, mirroring
        TestCollectProcessCwdsLsofFallback below). lsof is itself a running
        process at scan time and inherits the caller's cwd at fork —
        excluding only the scanning shell's own $$ is not sufficient; lsof's
        own PID must be excluded too, or the scanning shell's own cwd is
        wrongly read back as 'in use' by lsof's self-report."""
        target = tmp_path / "self-dir-lsof"
        target.mkdir()
        env = {**os.environ, "PATH": f"{readlink_always_fails_path}:{os.environ['PATH']}"}
        result = _run_bash(f'''
cd "{target}"
collect_process_cwds
echo "scan:$PROCESS_CWD_SCAN"
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''', env=env)
        assert result.returncode == 0, result.stderr
        assert "scan:ok" in result.stdout
        assert "exit:1" in result.stdout, (
            "the invoking shell's own cwd must not read as 'in use' by lsof's own self-report"
        )


class TestCollectProcessCwdsLsofFallback:
    """When /proc is unavailable (forced here via a failing readlink shim),
    collect_process_cwds falls back to `lsof -d cwd -F pn` and still
    detects a live process's cwd correctly via the p*/n* line parsing."""

    def test_live_process_detected_via_lsof_when_proc_unavailable(
        self, tmp_path, readlink_always_fails_path
    ):
        target = tmp_path / "lsof-live-dir"
        target.mkdir()
        holder = subprocess.Popen(["sleep", "30"], cwd=str(target))
        try:
            env = {**os.environ, "PATH": f"{readlink_always_fails_path}:{os.environ['PATH']}"}
            result = _run_bash(f'''
collect_process_cwds
echo "scan:$PROCESS_CWD_SCAN"
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''', env=env)
        finally:
            holder.terminate()
            holder.wait()
        assert result.returncode == 0, result.stderr
        assert "scan:ok" in result.stdout
        assert "exit:0" in result.stdout


class TestCollectProcessCwdsBothProbesUnavailable:
    """When neither /proc (readlink shimmed to fail) nor lsof (shimmed to
    fail) can report process cwds, collect_process_cwds must leave
    PROCESS_CWD_SCAN as "unavailable" rather than reporting an empty scan
    as "ok" — worktree_in_use must then report "could not determine" (2),
    not "idle" (1), so a caller doesn't remove a worktree it never
    actually verified as idle."""

    def test_scan_unavailable_worktree_in_use_returns_could_not_determine(
        self, tmp_path, readlink_always_fails_path, lsof_always_fails_path
    ):
        target = tmp_path / "unavailable-dir"
        target.mkdir()
        env = {
            **os.environ,
            "PATH": f"{readlink_always_fails_path}:{lsof_always_fails_path}:{os.environ['PATH']}",
        }
        result = _run_bash(f'''
collect_process_cwds
echo "scan:$PROCESS_CWD_SCAN"
RC=0
worktree_in_use "{target}" || RC=$?
echo "exit:$RC"
''', env=env)
        assert result.returncode == 0, result.stderr
        assert "scan:unavailable" in result.stdout
        assert "exit:2" in result.stdout


class TestResolveWorktreeForBranch:
    """resolve_worktree_for_branch, sourced and called directly (no consumer
    script), against a real repo built from the shared conftest helpers."""

    def test_branch_with_worktree_resolves_path(self, tmp_path):
        from conftest import _make_feature_branch, _make_repo_with_remote, _make_worktree

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/has-worktree")
        wt_path = tmp_path / "has-worktree-tree"
        _make_worktree(local, "feat/has-worktree", wt_path)

        result = _run_bash(f'''
cd "{local}"
resolve_worktree_for_branch "feat/has-worktree"
echo "path:$WORKTREE_PATH"
echo "locked:$WORKTREE_LOCKED"
''')
        assert result.returncode == 0, result.stderr
        assert f"path:{wt_path}" in result.stdout
        assert "locked:0" in result.stdout

    def test_branch_without_worktree_resolves_empty(self, tmp_path):
        from conftest import _make_feature_branch, _make_repo_with_remote

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-worktree")

        result = _run_bash(f'''
cd "{local}"
resolve_worktree_for_branch "feat/no-worktree"
echo "path:[$WORKTREE_PATH]"
''')
        assert result.returncode == 0, result.stderr
        assert "path:[]" in result.stdout

    def test_locked_worktree_resolves_lock_flag_and_pid(self, tmp_path):
        """A worktree locked with `git worktree lock --reason "... (pid N)"`
        resolves WORKTREE_LOCKED=1 and WORKTREE_LOCK_PID matching the pid
        embedded in the lock reason — the `locked`-line parsing this
        function does independently of either consumer script's full-run
        tests (which exercise this only incidentally, via cleanup-merged-
        branches.sh's own locked-worktree scenarios)."""
        from conftest import _dead_pid, _make_feature_branch, _make_repo_with_remote, _make_worktree

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-direct")
        wt_path = tmp_path / "locked-direct-tree"
        _make_worktree(local, "feat/locked-direct", wt_path)
        dead = _dead_pid()
        subprocess.run(
            ["git", "worktree", "lock", str(wt_path), "--reason", f"test (pid {dead})"],
            cwd=local, check=True,
        )

        result = _run_bash(f'''
cd "{local}"
resolve_worktree_for_branch "feat/locked-direct"
echo "path:$WORKTREE_PATH"
echo "locked:$WORKTREE_LOCKED"
echo "pid:$WORKTREE_LOCK_PID"
''')
        assert result.returncode == 0, result.stderr
        assert f"path:{wt_path}" in result.stdout
        assert "locked:1" in result.stdout
        assert f"pid:{dead}" in result.stdout
