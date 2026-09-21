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
