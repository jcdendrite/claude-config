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
import shlex
import shutil
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


# An inherited CDPATH makes a bare `cd <relative-name>` resolve elsewhere and print
# the hit; cases exercising path canonicalization pin it empty.
_ENV_WITHOUT_CDPATH = {**os.environ, "CDPATH": ""}


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
        from .conftest import _make_feature_branch, _make_repo_with_remote, _make_worktree

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
        from .conftest import _make_feature_branch, _make_repo_with_remote

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
        from .conftest import _dead_pid, _make_feature_branch, _make_repo_with_remote, _make_worktree

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


class TestCollectAllWorktrees:
    """collect_all_worktrees, sourced and called directly (no consumer
    script), against real repos built from the shared conftest helpers.

    A worktree path itself is never quoted by `git worktree list
    --porcelain` (verified: even a path containing a literal `"` character
    passes through unquoted on git 2.43), so there is no cheap fixture for
    a quoted *path* -- only the `locked <reason>` line is ever C-style
    quoted, exercised by test_quoted_lock_reason_is_stored_verbatim below.
    """

    def test_multiple_records_capture_distinct_lock_and_prunable_state(self, tmp_path):
        from .conftest import _make_feature_branch, _make_repo_with_remote, _make_worktree

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/plain")
        plain_path = tmp_path / "plain-tree"
        _make_worktree(local, "feat/plain", plain_path)
        _make_feature_branch(local, "feat/locked")
        locked_path = tmp_path / "locked-tree"
        _make_worktree(local, "feat/locked", locked_path)
        subprocess.run(
            ["git", "worktree", "lock", str(locked_path), "--reason", "held for review"],
            cwd=local, check=True,
        )
        _make_feature_branch(local, "feat/prunable")
        prunable_path = tmp_path / "prunable-tree"
        _make_worktree(local, "feat/prunable", prunable_path)
        shutil.rmtree(prunable_path)

        result = _run_bash(f'''
cd "{local}"
collect_all_worktrees
for i in "${{!ALL_WT_PATHS[@]}}"; do
  p=1
  [ -z "${{ALL_WT_PRUNABLE_REASONS[$i]}}" ] && p=0
  echo "path:${{ALL_WT_PATHS[$i]}}|locked:${{ALL_WT_LOCKED[$i]}}|reason:${{ALL_WT_LOCK_REASONS[$i]}}|prunable_nonempty:$p"
done
''')
        assert result.returncode == 0, result.stderr
        assert f"path:{plain_path}|locked:0|reason:|prunable_nonempty:0" in result.stdout
        assert f"path:{locked_path}|locked:1|reason:held for review|prunable_nonempty:0" in result.stdout
        assert f"path:{prunable_path}|locked:0|reason:|prunable_nonempty:1" in result.stdout

    def test_locked_with_no_reason_leaves_lock_reason_empty(self, tmp_path):
        from .conftest import _make_feature_branch, _make_repo_with_remote, _make_worktree

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-bare")
        wt_path = tmp_path / "locked-bare-tree"
        _make_worktree(local, "feat/locked-bare", wt_path)
        subprocess.run(["git", "worktree", "lock", str(wt_path)], cwd=local, check=True)

        result = _run_bash(f'''
cd "{local}"
collect_all_worktrees
for i in "${{!ALL_WT_PATHS[@]}}"; do
  [ "${{ALL_WT_PATHS[$i]}}" = "{wt_path}" ] && echo "locked:${{ALL_WT_LOCKED[$i]}}|reason:[${{ALL_WT_LOCK_REASONS[$i]}}]"
done
''')
        assert result.returncode == 0, result.stderr
        assert "locked:1|reason:[]" in result.stdout

    def test_lock_reason_with_non_pid_digits_leaves_lock_pid_empty(self, tmp_path):
        """A lock reason carrying digits with no `pid` token (e.g. an issue
        number) must not be mistaken for a pid -- the pid regex requires
        the literal `pid` substring, not just any digits."""
        from .conftest import _make_feature_branch, _make_repo_with_remote, _make_worktree

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/locked-digits")
        wt_path = tmp_path / "locked-digits-tree"
        _make_worktree(local, "feat/locked-digits", wt_path)
        reason = "issue 1234 blocked"
        subprocess.run(["git", "worktree", "lock", str(wt_path), "--reason", reason], cwd=local, check=True)

        result = _run_bash(f'''
cd "{local}"
collect_all_worktrees
for i in "${{!ALL_WT_PATHS[@]}}"; do
  [ "${{ALL_WT_PATHS[$i]}}" = "{wt_path}" ] && echo "reason:${{ALL_WT_LOCK_REASONS[$i]}}|pid:[${{ALL_WT_LOCK_PIDS[$i]}}]"
done
''')
        assert result.returncode == 0, result.stderr
        assert f"reason:{reason}|pid:[]" in result.stdout

    def test_quoted_lock_reason_is_stored_verbatim(self, tmp_path):
        """A lock reason containing characters that trigger git's C-style
        quoting (embedded quote and backslash characters) is stored exactly
        as the porcelain line reports it -- collect_all_worktrees never
        attempts to unescape it. The expected text is read back from a real
        `git worktree list --porcelain` call rather than hand-encoding
        git's quoting rules a second time."""
        from .conftest import _make_feature_branch, _make_repo_with_remote, _make_worktree

        local, _ = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/quoted-reason")
        wt_path = tmp_path / "quoted-reason-tree"
        _make_worktree(local, "feat/quoted-reason", wt_path)
        reason = 'reason with "quotes" and \\ backslash'
        subprocess.run(["git", "worktree", "lock", str(wt_path), "--reason", reason], cwd=local, check=True)

        porcelain = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=local,
            capture_output=True, text=True, check=True,
        ).stdout
        locked_lines = [line for line in porcelain.splitlines() if line.startswith("locked")]
        assert len(locked_lines) == 1
        expected_reason = locked_lines[0][len("locked"):].lstrip(" ")
        assert expected_reason.startswith('"'), "test assumes this reason triggers git's C-style quoting"

        result = _run_bash(f'''
cd "{local}"
collect_all_worktrees
for i in "${{!ALL_WT_PATHS[@]}}"; do
  [ "${{ALL_WT_PATHS[$i]}}" = "{wt_path}" ] && printf 'reason:%s\\n' "${{ALL_WT_LOCK_REASONS[$i]}}"
done
''')
        assert result.returncode == 0, result.stderr
        assert f"reason:{expected_reason}" in result.stdout


class TestWorktreeCanonPath:
    """worktree_canon_path resolves an existing directory to its symlink-free
    form and falls back to the raw input for anything it cannot cd into."""

    def test_symlinked_component_resolves_to_real_path(self, tmp_path):
        real_dir = tmp_path / "real-dir"
        (real_dir / "nested").mkdir(parents=True)
        symlink = tmp_path / "symlink-to-real"
        symlink.symlink_to(real_dir)
        result = _run_bash(
            f'printf "%s\\n" "$(worktree_canon_path "{symlink}/nested")"', env=_ENV_WITHOUT_CDPATH
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{(real_dir / 'nested').resolve()}\n"

    def test_path_containing_a_space_resolves_through_symlink(self, tmp_path):
        real_dir = tmp_path / "real dir"
        real_dir.mkdir()
        symlink = tmp_path / "link to real"
        symlink.symlink_to(real_dir)
        result = _run_bash(
            f'printf "%s\\n" "$(worktree_canon_path "{symlink}")"', env=_ENV_WITHOUT_CDPATH
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{real_dir.resolve()}\n"

    def test_nonexistent_path_falls_back_to_raw_input_silently(self, tmp_path):
        missing = tmp_path / "no-such-dir"
        result = _run_bash(
            f'printf "%s\\n" "$(worktree_canon_path "{missing}")"', env=_ENV_WITHOUT_CDPATH
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{missing}\n"
        assert result.stderr == ""

    def test_relative_path_resolves_against_cwd(self, tmp_path):
        real_dir = tmp_path / "real-dir"
        real_dir.mkdir()
        result = _run_bash(
            f'cd "{tmp_path}"\nprintf "%s\\n" "$(worktree_canon_path "./real-dir/../real-dir")"',
            env=_ENV_WITHOUT_CDPATH,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{real_dir.resolve()}\n"

    def test_non_path_string_falls_back_to_raw_input_silently(self, tmp_path):
        result = _run_bash(
            f"cd \"{tmp_path}\"\nprintf '%s\\n' \"$(worktree_canon_path 'feat/some-branch')\"",
            env=_ENV_WITHOUT_CDPATH,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "feat/some-branch\n"
        assert result.stderr == ""

    def test_option_shaped_input_is_returned_raw_not_read_as_a_cd_option(self, tmp_path):
        """`-P` names no directory, so it comes back verbatim with no stderr;
        without `--`, cd would take it as an option and land in HOME."""
        home_dir = tmp_path / "home-dir"
        home_dir.mkdir()
        env = {**_ENV_WITHOUT_CDPATH, "HOME": str(home_dir)}
        result = _run_bash(f'cd "{tmp_path}"\nprintf "%s\\n" "$(worktree_canon_path -P)"', env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "-P\n"
        assert result.stderr == ""

    def test_lone_dash_is_returned_raw_not_read_as_oldpwd(self, tmp_path):
        """A bare `-` is cd's OLDPWD shorthand, which would print the previous
        directory and resolve to it; it must come back as the literal `-`."""
        previous_dir = tmp_path / "previous-dir"
        previous_dir.mkdir()
        result = _run_bash(
            f'cd "{previous_dir}"\ncd "{tmp_path}"\nprintf "%s\\n" "$(worktree_canon_path -)"',
            env=_ENV_WITHOUT_CDPATH,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "-\n"

    def test_empty_input_is_returned_empty_not_resolved_to_cwd(self, tmp_path):
        """bash 3.2's `cd ""` succeeds in the current directory; bash 4+ (Homebrew
        bash, the CI runner) fails it, making the guard a no-op there.
        `_run_bash` runs whichever `bash` is first on PATH, so this test only
        discriminates on a system where bash 3.2 is first (stock macOS)."""
        result = _run_bash(
            f'cd "{tmp_path}"\nprintf "[%s]\\n" "$(worktree_canon_path "")"', env=_ENV_WITHOUT_CDPATH
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "[]\n"

    def test_inherited_cdpath_does_not_resolve_a_relative_name(self, tmp_path):
        """A relative name absent from cwd but present under an inherited
        CDPATH entry is returned raw: cd would otherwise resolve it there and
        print the hit as an extra output line."""
        cdpath_root = tmp_path / "cdpath-root"
        (cdpath_root / "branch-like-name").mkdir(parents=True)
        unrelated_cwd = tmp_path / "unrelated-cwd"
        unrelated_cwd.mkdir()
        env = {**os.environ, "CDPATH": str(cdpath_root)}
        result = _run_bash(
            f'cd "{unrelated_cwd}"\nprintf "%s\\n" "$(worktree_canon_path branch-like-name)"', env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "branch-like-name\n"


class TestWorktreeInUseCanonicalizationFallback:
    """worktree_in_use compares a process cwd against the target's canonical
    path when the target resolves, and against the raw target string when it
    does not (e.g. a worktree whose directory is gone)."""

    def test_unresolvable_target_matches_process_cwd_by_raw_string(self, tmp_path):
        missing = tmp_path / "gone-worktree"
        result = _run_bash(f'''
PROCESS_CWD_SCAN=ok
PROCESS_CWDS=("{missing}" "/elsewhere")
RC=0
worktree_in_use "{missing}" || RC=$?
echo "exit:$RC"
PROCESS_CWDS=("{missing}/sub")
RC=0
worktree_in_use "{missing}" || RC=$?
echo "subdir-exit:$RC"
PROCESS_CWDS=("/elsewhere")
RC=0
worktree_in_use "{missing}" || RC=$?
echo "other-exit:$RC"
''')
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["exit:0", "subdir-exit:0", "other-exit:1"]

    def test_resolvable_target_matches_process_cwd_by_canonical_string(self, tmp_path):
        real_dir = tmp_path / "real-dir"
        real_dir.mkdir()
        symlink = tmp_path / "symlink-to-real"
        symlink.symlink_to(real_dir)
        result = _run_bash(f'''
PROCESS_CWD_SCAN=ok
PROCESS_CWDS=("{real_dir.resolve()}")
RC=0
worktree_in_use "{symlink}" || RC=$?
echo "exit:$RC"
PROCESS_CWDS=("{symlink}")
RC=0
worktree_in_use "{symlink}" || RC=$?
echo "raw-symlink-exit:$RC"
''')
        assert result.returncode == 0, result.stderr
        # The raw symlink string is not what the kernel reports, so it must not match.
        assert result.stdout.splitlines() == ["exit:0", "raw-symlink-exit:1"]


class TestWorktreeMatchesFilter:
    """worktree_matches_filter reads the caller-populated FILTER_ARGS array and
    matches a worktree by exact branch name or canonicalized path."""

    def test_empty_filter_args_match_everything(self, tmp_path):
        result = _run_bash(f'''
FILTER_ARGS=()
RC=0
worktree_matches_filter "feat/any" "{tmp_path}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout

    def test_branch_name_match(self, tmp_path):
        result = _run_bash(f'''
cd "{tmp_path}"
FILTER_ARGS=("feat/target")
RC=0
worktree_matches_filter "feat/target" "{tmp_path}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout

    def test_canonical_path_match_through_symlink(self, tmp_path):
        real_dir = tmp_path / "real-dir"
        real_dir.mkdir()
        symlink = tmp_path / "symlink-to-real"
        symlink.symlink_to(real_dir)
        result = _run_bash(f'''
FILTER_ARGS=("{symlink}")
RC=0
worktree_matches_filter "feat/unrelated" "{real_dir.resolve()}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout

    def test_relative_path_match_canonicalizes_against_cwd(self, tmp_path):
        real_dir = tmp_path / "real-dir"
        real_dir.mkdir()
        result = _run_bash(f'''
cd "{tmp_path}"
FILTER_ARGS=("./real-dir")
RC=0
worktree_matches_filter "feat/unrelated" "{real_dir.resolve()}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout

    def test_any_matching_arg_among_several_matches(self, tmp_path):
        result = _run_bash(f'''
cd "{tmp_path}"
FILTER_ARGS=("feat/other" "feat/target")
RC=0
worktree_matches_filter "feat/target" "{tmp_path}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert "exit:0" in result.stdout

    def test_no_arg_matching_branch_or_path_returns_one(self, tmp_path):
        other_dir = tmp_path / "other-dir"
        other_dir.mkdir()
        result = _run_bash(f'''
cd "{tmp_path}"
FILTER_ARGS=("feat/other" "{other_dir}")
RC=0
worktree_matches_filter "feat/target" "{tmp_path}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert "exit:1" in result.stdout

    def test_branch_name_matches_even_when_it_also_names_a_relative_directory(self, tmp_path):
        """A filter arg equal to the branch string still matches when it also
        names an existing directory under cwd: the branch comparison uses the
        raw arg, never its canonicalized form."""
        (tmp_path / "docs").mkdir()
        unrelated_worktree = tmp_path / "unrelated-tree"
        unrelated_worktree.mkdir()
        result = _run_bash(f'''
cd "{tmp_path}"
FILTER_ARGS=("docs")
RC=0
worktree_matches_filter "docs" "{unrelated_worktree.resolve()}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["exit:0"]

    @pytest.mark.parametrize(
        "filter_arg",
        ["feat", "feat/tar", "feat/*", "feat*", "*"],
        ids=["strict-prefix", "longer-strict-prefix", "glob-slash-star", "glob-star", "bare-star"],
    )
    def test_branch_filter_arg_must_equal_branch_exactly(self, tmp_path, filter_arg):
        """A strict prefix or glob of the branch name is not an exact match."""
        result = _run_bash(f'''
cd "{tmp_path}"
FILTER_ARGS=('{filter_arg}')
RC=0
worktree_matches_filter "feat/target" "{tmp_path}/wt" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["exit:1"]

    def test_ancestor_directory_of_worktree_path_does_not_match(self, tmp_path):
        """A filter arg naming a directory that contains the worktree is not
        the worktree's canonical path."""
        ancestor = tmp_path / "ancestor"
        worktree = ancestor / "nested" / "wt"
        worktree.mkdir(parents=True)
        result = _run_bash(f'''
FILTER_ARGS=("{ancestor}")
RC=0
worktree_matches_filter "feat/unrelated" "{worktree.resolve()}" || RC=$?
echo "exit:$RC"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["exit:1"]

    def test_unassigned_filter_args_aborts_under_set_u(self, tmp_path):
        """FILTER_ARGS is a caller-assigned global: calling the function
        without assigning it aborts rather than matching everything."""
        result = _run_bash(f'''
cd "{tmp_path}"
worktree_matches_filter "feat/target" "{tmp_path}"
echo "reached"
''', env=_ENV_WITHOUT_CDPATH)
        assert result.returncode != 0
        assert "FILTER_ARGS" in result.stderr
        assert "reached" not in result.stdout


# ---------------------------------------------------------------------------
# worktree-ensure.sh decision functions
#
# worktree-ensure.sh is the only caller of the five functions below. Its
# subprocess tests (test_worktree_ensure.py) cover one case per I/O branch;
# the input matrix lives here, against synthetic ALL_WT_* arrays, stdin text
# and tmp_path directories whose names carry shell-special characters.
# ---------------------------------------------------------------------------

_SPECIAL_DIR_NAME = "dir with [x] and *"
_ENSURE_FAKE_SHA = "a" * 40


def _record(path, branch="", *, lock_reason=None, prunable_reason=""):
    """One synthetic porcelain record. `lock_reason=None` means unlocked; an empty string is a bare `locked`."""
    return {
        "path": str(path),
        "branch": branch,
        "lock_reason": lock_reason,
        "prunable_reason": prunable_reason,
    }


def _arrays_bash(records):
    """Bash text assigning the ALL_WT_* arrays collect_all_worktrees would fill from `records`."""

    def array(name, values):
        return f"{name}=({' '.join(shlex.quote(value) for value in values)})\n"

    return (
        array("ALL_WT_PATHS", [r["path"] for r in records])
        + array("ALL_WT_BRANCHES", [r["branch"] for r in records])
        + array("ALL_WT_LOCKED", ["0" if r["lock_reason"] is None else "1" for r in records])
        + array("ALL_WT_LOCK_PIDS", ["" for _ in records])
        + array("ALL_WT_LOCK_REASONS", [r["lock_reason"] or "" for r in records])
        + array("ALL_WT_PRUNABLE_REASONS", [r["prunable_reason"] for r in records])
    )


def _text_without_paths(line, *paths):
    """`line` with each path removed in its raw and realpath forms, longest first, so a word
    asserted on the remainder cannot come from a tmp_path directory name."""
    forms = {str(path) for path in paths} | {os.path.realpath(path) for path in paths}
    for form in sorted(forms, key=len, reverse=True):
        line = line.replace(form, "")
    return line


def _checkout_layout(tmp_path):
    """A main checkout at `<tmp_path>/<special name>/m` with its worktrees directory present."""
    main = tmp_path / _SPECIAL_DIR_NAME / "m"
    (main / ".claude" / "worktrees").mkdir(parents=True)
    return main


def _make_registered_dir(path, *, with_git=True):
    """A directory standing in for a registered worktree, holding a `.git` file unless told not to."""
    path.mkdir(parents=True, exist_ok=True)
    if with_git:
        (path / ".git").write_text("gitdir: /nowhere\n")
    return path


class TestEnsureNameCheck:
    @staticmethod
    def _check(name, env_overrides=None):
        env = {**os.environ, "BRANCH_NAME": name, **(env_overrides or {})}
        result = _run_bash('worktree_ensure_name_check "$BRANCH_NAME"', env=env)
        assert result.returncode == 0, result.stderr
        return result.stdout

    @pytest.mark.parametrize("name", ["GH-1/new", "feature_x.y/a-b", "A9"])
    def test_accepts_names_within_the_ascii_set(self, name):
        assert self._check(name) == "ok"

    @pytest.mark.parametrize(
        "name",
        [
            "-x", "a..b", "a b", "x.lock", "a/", "/x", "",
            "a;b", 'a"b', "a'b", "a}b", "$x", "a`b", "a b", "é", "@{-1}",
            "a\nb", "a\n",
            "a*b", "a?b", "a[b", "a\\b", "a~b", "a^b", "a:b", "a//b", "a/.b",
        ],
        ids=[
            "leading-dash", "double-dot", "space", "lock-suffix", "trailing-slash", "leading-slash", "empty",
            "semicolon", "double-quote", "single-quote", "close-brace", "dollar", "backtick", "no-break-space",
            "e-acute", "reflog-expansion",
            "embedded-newline", "trailing-newline",
            "asterisk", "question-mark", "open-bracket", "backslash", "tilde", "caret", "colon",
            "empty-component", "component-leading-dot",
        ],
    )
    def test_rejects_names_outside_the_set_or_invalid_to_git(self, name):
        assert self._check(name) == "bad-name"

    @pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
    def test_rejects_e_acute_under_the_c_and_utf_8_locales(self, locale):
        assert self._check("é", {"LC_ALL": locale}) == "bad-name"

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(128, "bad-name"), (127, "git-failed 127")],
        ids=["invalid-name-status", "git-could-not-run"],
    )
    def test_git_status_decides_between_bad_name_and_git_failed(self, tmp_path, status, expected):
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        shim.write_text(f"#!/usr/bin/env bash\nexit {status}\n")
        shim.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "BRANCH_NAME": "GH-1/new"}

        result = _run_bash('worktree_ensure_name_check "$BRANCH_NAME"', env=env)

        assert result.stdout == expected, result.stderr


class TestEnsureClassify:
    @staticmethod
    def _classify(records, branch, main, cwd):
        result = _run_bash(
            _arrays_bash(records) + f"worktree_ensure_classify {shlex.quote(branch)} {shlex.quote(str(main))} "
            f"{shlex.quote(str(cwd))}"
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_absent_when_no_record_holds_the_branch(self, tmp_path):
        main = _checkout_layout(tmp_path)
        records = [_record(main, "main"), _record(_make_registered_dir(main / ".claude/worktrees/other"), "other")]

        assert self._classify(records, "GH-1/new", main, main) == "absent"

    def test_in_main_when_the_main_record_holds_the_branch(self, tmp_path):
        main = _checkout_layout(tmp_path)

        assert self._classify([_record(main, "GH-1/new")], "GH-1/new", main, main) == "in-main 0"

    def test_existing_when_cwd_is_elsewhere_and_current_when_cwd_equals_the_record(self, tmp_path):
        main = _checkout_layout(tmp_path)
        worktree = _make_registered_dir(main / ".claude/worktrees/GH-1/new")
        records = [_record(main, "main"), _record(worktree, "GH-1/new")]

        assert self._classify(records, "GH-1/new", main, main) == "existing 1"
        assert self._classify(records, "GH-1/new", main, worktree) == "current 1"

    def test_subdirectory_cwd_is_existing_because_enter_worktree_accepts_it(self, tmp_path):
        main = _checkout_layout(tmp_path)
        worktree = _make_registered_dir(main / ".claude/worktrees/GH-1/new")
        (worktree / "sub").mkdir()
        records = [_record(main, "main"), _record(worktree, "GH-1/new")]

        assert self._classify(records, "GH-1/new", main, worktree / "sub") == "existing 1"

    def test_record_path_reaching_the_cwd_through_a_symlink_is_current(self, tmp_path):
        main = _checkout_layout(tmp_path)
        real_worktree = _make_registered_dir(main / ".claude/worktrees/GH-1/real")
        link = main / ".claude/worktrees/GH-1/new"
        link.symlink_to(real_worktree)
        records = [_record(main, "main"), _record(link, "GH-1/new")]

        assert self._classify(records, "GH-1/new", main, real_worktree) == "current 1"

    @pytest.mark.parametrize("with_git", [True, False], ids=["git-file-present", "git-file-absent"])
    @pytest.mark.parametrize("cwd_is_record", [False, True], ids=["cwd-elsewhere", "cwd-is-the-record"])
    def test_add_unfinished_when_lock_reason_is_initializing(self, tmp_path, with_git, cwd_is_record):
        main = _checkout_layout(tmp_path)
        worktree = _make_registered_dir(main / ".claude/worktrees/GH-1/new", with_git=with_git)
        records = [_record(main, "main"), _record(worktree, "GH-1/new", lock_reason="initializing")]

        cwd = worktree if cwd_is_record else main
        assert self._classify(records, "GH-1/new", main, cwd) == "add-unfinished 1"

    @pytest.mark.parametrize(
        "lock_reason",
        ["claude-code pid 1 session x", "", "initializing-not-exactly"],
        ids=["collision-guard-reason", "bare-lock", "reason-merely-starting-with-initializing"],
    )
    def test_other_lock_reasons_do_not_block(self, tmp_path, lock_reason):
        main = _checkout_layout(tmp_path)
        worktree = _make_registered_dir(main / ".claude/worktrees/GH-1/new")
        records = [_record(main, "main"), _record(worktree, "GH-1/new", lock_reason=lock_reason)]

        assert self._classify(records, "GH-1/new", main, main) == "existing 1"

    @pytest.mark.parametrize(
        "layout",
        ["directory-gone-locked", "directory-without-git", "dangling-git-symlink"],
    )
    def test_missing_when_the_record_has_no_usable_directory(self, tmp_path, layout):
        main = _checkout_layout(tmp_path)
        worktree = main / ".claude/worktrees/GH-1/new"
        if layout == "directory-without-git":
            worktree.mkdir(parents=True)
        elif layout == "dangling-git-symlink":
            worktree.mkdir(parents=True)
            (worktree / ".git").symlink_to(tmp_path / "does-not-exist")
        lock_reason = "claude-code pid 1 session x" if layout == "directory-gone-locked" else None
        records = [_record(main, "main"), _record(worktree, "GH-1/new", lock_reason=lock_reason)]

        assert self._classify(records, "GH-1/new", main, main) == "missing 1"

    @pytest.mark.parametrize(
        "relative_path",
        [".claude/worktrees-old/x", "elsewhere"],
        ids=["sibling-with-shared-prefix", "outside-claude-directory"],
    )
    @pytest.mark.parametrize("cwd_is_record", [False, True], ids=["cwd-elsewhere", "cwd-is-the-record"])
    def test_outside_when_the_record_is_not_under_the_worktrees_directory(self, tmp_path, relative_path, cwd_is_record):
        main = _checkout_layout(tmp_path)
        outside = _make_registered_dir(main / relative_path)
        records = [_record(main, "main"), _record(outside, "GH-1/new")]

        cwd = outside if cwd_is_record else main
        assert self._classify(records, "GH-1/new", main, cwd) == "outside 1"

    @pytest.mark.parametrize(
        ("record_main_name", "expected"),
        [("m*", "existing 1"), ("mX", "outside 1")],
        ids=["record-under-the-literal-main", "record-under-a-glob-lookalike-main"],
    )
    def test_main_path_is_compared_literally_not_as_a_glob(self, tmp_path, record_main_name, expected):
        main = tmp_path / "m*"
        (main / ".claude/worktrees").mkdir(parents=True)
        worktree = _make_registered_dir(tmp_path / record_main_name / ".claude/worktrees/foo")
        records = [_record(main, "main"), _record(worktree, "GH-1/new")]

        assert self._classify(records, "GH-1/new", main, main) == expected

    @pytest.mark.parametrize(
        ("record_branch", "query", "expected"),
        [
            ("foo", "foo", "existing 1"),
            ("foo", "foobar", "absent"),
            ("a.b", "a.b", "existing 1"),
            ("axb", "a.b", "absent"),
        ],
        ids=["exact", "control-query-extends-record", "dot-exact", "control-dot-is-not-a-wildcard"],
    )
    def test_branch_is_matched_exactly(self, tmp_path, record_branch, query, expected):
        main = _checkout_layout(tmp_path)
        worktree = _make_registered_dir(main / ".claude/worktrees/foo")
        records = [_record(main, "main"), _record(worktree, record_branch)]

        assert self._classify(records, query, main, main) == expected


class TestEnsureRefCheck:
    @staticmethod
    def _check(branch, existing_branches):
        listing = "\n".join(f"{_ENSURE_FAKE_SHA} refs/heads/{name}" for name in existing_branches)
        result = _run_bash(f"worktree_ensure_ref_check {shlex.quote(branch)} <<'LISTING'\n{listing}\nLISTING\n")
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_exact_match_reports_the_tip_sha(self):
        assert self._check("GH-1/new", ["main", "GH-1/new"]) == f"branch-without-worktree {_ENSURE_FAKE_SHA}"

    @pytest.mark.parametrize(
        ("branch", "existing", "expected"),
        [
            ("GH-1/new", "GH-1", "ref-conflict GH-1"),
            ("a/b/c", "a/b", "ref-conflict a/b"),
            ("GH-8", "GH-8/new", "ref-conflict GH-8/new"),
            ("GH-1/new", "GH-10", ""),
            ("foo", "foo-bar", ""),
            ("a.b", "axb", ""),
        ],
        ids=[
            "existing-is-a-prefix", "existing-is-a-deeper-prefix", "existing-lies-beneath",
            "control-shared-digit-prefix", "control-shared-name-prefix", "control-dot-is-not-a-wildcard",
        ],
    )
    def test_ref_name_conflicts(self, branch, existing, expected):
        assert self._check(branch, ["main", existing]) == expected

    def test_empty_listing_prints_nothing(self):
        assert self._check("GH-1/new", []) == ""

    def test_blank_lines_are_skipped(self):
        result = _run_bash(
            "worktree_ensure_ref_check GH-1/new <<'LISTING'\n\n"
            f"{_ENSURE_FAKE_SHA} refs/heads/GH-1/new\n\nLISTING\n"
        )
        assert result.stdout == f"branch-without-worktree {_ENSURE_FAKE_SHA}"


class TestEnsureTargetCheck:
    @staticmethod
    def _check(records, main, relative_target, env=None):
        target = main / ".claude/worktrees" / relative_target
        result = _run_bash(
            _arrays_bash(records) + f"worktree_ensure_target_check {shlex.quote(str(main))} {shlex.quote(str(target))}",
            env=env,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    @staticmethod
    def _token_and_text(output, *paths):
        token, _, message = output.partition(" ")
        return token, _text_without_paths(message, *paths)

    @pytest.mark.parametrize(
        ("blocker_present", "expected_token"),
        [(True, "path-in-use"), (False, "")],
        ids=["record-equals-target", "control-only-the-main-record-and-an-absent-target"],
    )
    def test_linked_record_equal_to_the_target_is_refused_and_a_lone_main_record_is_not(
        self, tmp_path, blocker_present, expected_token
    ):
        main = _checkout_layout(tmp_path)
        records = [_record(main, "main")]
        if blocker_present:
            records.append(_record(main / ".claude/worktrees/GH-1/new", "GH-1/new"))

        token, _ = self._token_and_text(self._check(records, main, "GH-1/new"))

        assert token == expected_token

    @pytest.mark.parametrize(
        "layout",
        ["claude-is-a-symlink", "worktrees-is-a-symlink", "intermediate-is-a-symlink", "leaf-is-a-dangling-symlink"],
    )
    def test_symlinked_component_is_refused(self, tmp_path, layout):
        main = tmp_path / "m"
        outside = tmp_path / "outside"
        outside.mkdir()
        worktrees = main / ".claude" / "worktrees"
        if layout == "claude-is-a-symlink":
            main.mkdir()
            (main / ".claude").symlink_to(outside)
        elif layout == "worktrees-is-a-symlink":
            (main / ".claude").mkdir(parents=True)
            worktrees.symlink_to(outside)
        elif layout == "intermediate-is-a-symlink":
            worktrees.mkdir(parents=True)
            (worktrees / "GH-1").symlink_to(outside)
        else:
            (worktrees / "GH-1").mkdir(parents=True)
            (worktrees / "GH-1" / "new").symlink_to(tmp_path / "does-not-exist")

        token, _ = self._token_and_text(self._check([_record(main, "main")], main, "GH-1/new"))

        assert token == "symlinked-path"

    @pytest.mark.parametrize("with_decoy", [False, True], ids=["no-decoy", "decoy-record-first"])
    @pytest.mark.parametrize(
        ("request_name", "blocker_relative", "blocker_branch", "directory_present"),
        [
            ("GH-1/new", "GH-1/new", "GH-1/new", True),
            ("GH-1/new", "GH-1/new", "GH-1/new", False),
            ("foo/baz", "foo", "bar", True),
        ],
        ids=["record-equals-target", "record-equals-target-directory-missing", "record-at-ancestor-unrelated-branch"],
    )
    def test_registered_record_on_the_path_is_refused_naming_that_record(
        self, tmp_path, with_decoy, request_name, blocker_relative, blocker_branch, directory_present
    ):
        main = _checkout_layout(tmp_path)
        blocker = main / ".claude/worktrees" / blocker_relative
        if directory_present:
            blocker.mkdir(parents=True)
        decoy = _make_registered_dir(main / ".claude/worktrees/GH-9/decoy")
        records = [_record(main, "main")]
        if with_decoy:
            records.append(_record(decoy, "GH-9/decoy"))
        records.append(_record(blocker, blocker_branch))

        output = self._check(records, main, request_name)
        token, text = self._token_and_text(output, tmp_path)

        assert token == "path-in-use"
        assert str(blocker) in output
        assert str(decoy) not in output
        assert f"branch {blocker_branch}" in text
        if not directory_present:
            assert "directory missing" in text

    def test_detached_record_on_the_path_is_described_as_detached(self, tmp_path):
        main = _checkout_layout(tmp_path)
        blocker = main / ".claude/worktrees/GH-1/new"
        blocker.mkdir(parents=True)

        token, text = self._token_and_text(
            self._check([_record(main, "main"), _record(blocker, "")], main, "GH-1/new"), tmp_path
        )

        assert token == "path-in-use"
        assert "detached" in text
        assert tmp_path.name not in text

    @pytest.mark.parametrize(
        "git_entry",
        ["file", "directory", "dangling-symlink", None],
        ids=["git-file", "git-directory", "dangling-git-symlink", "control-plain-ancestor-without-git"],
    )
    def test_unregistered_ancestor_holding_a_git_entry_is_refused_as_stray(self, tmp_path, git_entry):
        main = _checkout_layout(tmp_path)
        ancestor = main / ".claude/worktrees/stale"
        ancestor.mkdir()
        if git_entry == "file":
            (ancestor / ".git").write_text("gitdir: /nowhere\n")
        elif git_entry == "directory":
            (ancestor / ".git").mkdir()
        elif git_entry == "dangling-symlink":
            (ancestor / ".git").symlink_to(tmp_path / "does-not-exist")

        token, text = self._token_and_text(self._check([_record(main, "main")], main, "stale/inner"), tmp_path)

        if git_entry is None:
            assert token == ""
            return
        assert token == "path-in-use"
        assert "no registered worktree" in text
        assert "mov" not in text.casefold()

    @pytest.mark.parametrize("holder", ["target", "claude-directory", "worktrees-directory"])
    def test_git_entry_on_the_target_or_on_claude_or_worktrees_is_refused(self, tmp_path, holder):
        main = _checkout_layout(tmp_path)
        target = main / ".claude/worktrees/GH-1/new"
        target.mkdir(parents=True)
        if holder == "target":
            (target / ".git").write_text("gitdir: /nowhere\n")
        else:
            holder_directory = main / ".claude" if holder == "claude-directory" else main / ".claude/worktrees"
            (holder_directory / ".git").mkdir()

        token, text = self._token_and_text(self._check([_record(main, "main")], main, "GH-1/new"), tmp_path)

        assert token == "path-in-use"
        assert "no registered worktree" in text
        assert "mov" not in text.casefold()

    @pytest.mark.parametrize(
        "layout",
        ["file-at-worktrees-directory", "file-as-middle-component", "file-at-target"],
    )
    def test_component_that_is_not_a_directory_is_refused(self, tmp_path, layout):
        main = tmp_path / "m"
        (main / ".claude").mkdir(parents=True)
        worktrees = main / ".claude" / "worktrees"
        if layout == "file-at-worktrees-directory":
            worktrees.write_text("x\n")
        elif layout == "file-as-middle-component":
            worktrees.mkdir()
            (worktrees / "GH-1").write_text("x\n")
        else:
            (worktrees / "GH-1").mkdir(parents=True)
            (worktrees / "GH-1" / "new").write_text("x\n")

        token, text = self._token_and_text(self._check([_record(main, "main")], main, "GH-1/new"), tmp_path)

        assert token == "path-in-use"
        assert "not a directory" in text

    @pytest.mark.parametrize(
        ("entry_name", "expected_token"),
        [("file.txt", "target-not-empty"), (".keep", "target-not-empty"), (None, "")],
        ids=["regular-file", "dotfile-not-named-git", "control-existing-empty-target"],
    )
    def test_target_directory_must_be_empty(self, tmp_path, entry_name, expected_token):
        main = _checkout_layout(tmp_path)
        target = main / ".claude/worktrees/GH-1/new"
        target.mkdir(parents=True)
        if entry_name is not None:
            (target / entry_name).write_text("x\n")

        token, _ = self._token_and_text(self._check([_record(main, "main")], main, "GH-1/new"))

        assert token == expected_token

    def test_target_directory_that_cannot_be_listed_is_refused_even_when_empty(self, tmp_path):
        """A failing `find` must read as "cannot show empty", never as an empty directory."""
        main = _checkout_layout(tmp_path)
        (main / ".claude/worktrees/GH-1/new").mkdir(parents=True)
        shim_dir = tmp_path / "find-shim"
        shim_dir.mkdir()
        shim = shim_dir / "find"
        shim.write_text("#!/usr/bin/env bash\necho 'synthetic find failure' >&2\nexit 1\n")
        shim.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}

        token, text = self._token_and_text(
            self._check([_record(main, "main")], main, "GH-1/new", env=env), tmp_path
        )

        assert token == "target-not-empty"
        assert "cannot be listed" in text

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode 000 directory")
    def test_unreadable_target_directory_is_refused(self, tmp_path):
        main = _checkout_layout(tmp_path)
        target = main / ".claude/worktrees/GH-1/new"
        target.mkdir(parents=True)
        target.chmod(0o000)
        try:
            token, text = self._token_and_text(self._check([_record(main, "main")], main, "GH-1/new"), tmp_path)
        finally:
            target.chmod(0o700)

        assert token == "target-not-empty"
        assert "cannot be listed" in text

    @pytest.mark.parametrize(
        ("record_name", "request_name", "expected_token"),
        [
            ("foo", "foo", "path-in-use"),
            ("foo", "foobar", ""),
            ("a[b]", "a[b]", "path-in-use"),
            ("a[b]", "ab/x", ""),
        ],
        ids=["exact", "control-prefix-sibling", "bracket-name-exact", "control-bracket-is-not-a-glob"],
    )
    def test_record_path_is_compared_literally(self, tmp_path, record_name, request_name, expected_token):
        main = _checkout_layout(tmp_path)
        blocker = _make_registered_dir(main / ".claude/worktrees" / record_name)

        token, _ = self._token_and_text(
            self._check([_record(main, "main"), _record(blocker, "GH-1/other")], main, request_name)
        )

        assert token == expected_token


class TestEnsureDescribeRecord:
    @staticmethod
    def _describe(record, tmp_path):
        result = _run_bash(_arrays_bash([record]) + "worktree_ensure_describe_record 0")
        assert result.returncode == 0, result.stderr
        assert "\n" not in result.stdout.strip()
        return result.stdout, _text_without_paths(result.stdout, tmp_path, record["path"])

    def test_detached_record_with_a_registered_git_file(self, tmp_path):
        worktree = _make_registered_dir(tmp_path / "wt")

        line, text = self._describe(_record(worktree, ""), tmp_path)

        assert str(worktree) in line
        assert "detached" in text
        assert "branch" not in text
        assert "locked" not in text
        assert "missing" not in text
        assert "no .git" not in text

    def test_branch_record_names_the_branch(self, tmp_path):
        worktree = _make_registered_dir(tmp_path / "wt")

        _, text = self._describe(_record(worktree, "GH-1/new"), tmp_path)

        assert "branch GH-1/new" in text
        assert "detached" not in text

    def test_locked_record_whose_directory_is_missing(self, tmp_path):
        _, text = self._describe(_record(tmp_path / "gone", "GH-1/new", lock_reason="held"), tmp_path)

        assert text.index("branch GH-1/new") < text.index("locked") < text.index("directory missing")

    def test_directory_without_git_is_reported_as_such(self, tmp_path):
        worktree = _make_registered_dir(tmp_path / "wt", with_git=False)

        _, text = self._describe(_record(worktree, "GH-1/new"), tmp_path)

        assert "no .git" in text
        assert "directory missing" not in text

    def test_prunable_record_shows_git_reason_and_the_prune_remedy(self, tmp_path):
        worktree = _make_registered_dir(tmp_path / "wt")
        reason = "gitdir file points to non-existent location"

        _, text = self._describe(_record(worktree, "GH-1/new", prunable_reason=reason), tmp_path)

        assert reason in text
        assert "`git worktree prune` removes this record but not its directory or branch" in text
        assert "`branch-without-worktree`" in text
