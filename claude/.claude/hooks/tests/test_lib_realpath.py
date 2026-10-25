"""Unit tests for _lib.sh's _lib_realpath_m and _lib_normalize_path_lexically."""
from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _forced_fallback_path_env,
    _sourced_value,
    assert_cap_engaged,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from ._lib_sh_helpers import _LIB_SH

# --- _lib_realpath_m ---------------------------------------------------
#
# GNU `realpath -m` is available natively in this test environment, so a
# bare call exercises only the fast path (native -m succeeds immediately).
# The fallback path (walk to nearest existing ancestor + reattach) only
# runs when BOTH native `-m` and `grealpath` are unavailable — forced here
# via a PATH built from a fake `realpath` shim (errors on -m, delegates to
# the real system realpath otherwise) plus /usr/bin:/bin only, deliberately
# excluding /usr/local/bin, where this dev machine's Homebrew `grealpath`
# actually lives. Without this, `command -v grealpath` would still find it
# and the fallback branch under test would never run. The shim itself is
# `helpers._FORCED_FALLBACK_REALPATH_SHIM`, shared with
# test_ask_review_permissions.py, which forces the same fast-path failure
# for the same reason.


_REALPATH_M_FALLBACK_MAX_DEPTH = int(_sourced_value("_LIB_REALPATH_M_FALLBACK_MAX_DEPTH"))
assert _REALPATH_M_FALLBACK_MAX_DEPTH >= 2, (
    "exact-boundary tests below compute dirs = _REALPATH_M_FALLBACK_MAX_DEPTH - 2 and - 1; "
    "a cap below 2 degenerates those to trivial near-zero-depth cases"
)


def _run_realpath_m(target: str, forced_fallback: bool = False, tmp_path: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if forced_fallback:
        assert tmp_path is not None, "forced_fallback requires tmp_path"
        env["PATH"] = _forced_fallback_path_env(tmp_path)
    script = f'set -uo pipefail; . {_LIB_SH}; _lib_realpath_m "$1"'
    return subprocess.run(
        ["bash", "-c", script, "bash", target],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        # A regressed `|| return 1` on a capped call inside the fallback
        # loop's `while true` retries forever against an always-hanging
        # stub instead of terminating -- bound it so that regression fails
        # the test fast instead of hanging the pytest worker.
        timeout=30,
    )


# --- _lib_normalize_path_lexically -------------------------------------


def _normalize_lexically(path: str, tmp_path: Path | None = None) -> str:
    """Run the normalizer on PATH and return `_LIB_NORMALIZED_PATH`."""
    script = (
        f'set -uo pipefail; . {_LIB_SH}; '
        '_lib_normalize_path_lexically "$1" || exit 9; printf "%s" "$_LIB_NORMALIZED_PATH"'
    )
    result = subprocess.run(
        ["bash", "-c", script, "bash", path],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _lexical_normalization_oracle(path: str) -> str:
    """`posixpath.normpath` with the two documented differences: an empty path
    stays empty, and a trailing slash is kept. normpath also keeps exactly two
    leading slashes (POSIX leaves that case implementation-defined), where Linux
    reads them as one and the normalizer collapses them."""
    if path == "":
        return ""
    result = posixpath.normpath(path)
    if result.startswith("//"):
        result = result[1:]
    if path.endswith("/") and not result.endswith("/"):
        result += "/"
    return result


_LEXICAL_NORMALIZATION_INPUTS = [
    pytest.param("/", id="root"),
    pytest.param("//", id="double-slash-root"),
    pytest.param("///", id="triple-slash-root"),
    pytest.param(".", id="dot"),
    pytest.param("./", id="dot-trailing-slash"),
    pytest.param("..", id="dotdot-relative"),
    pytest.param("../", id="dotdot-relative-trailing-slash"),
    pytest.param("a", id="relative-single"),
    pytest.param("a/b/c", id="relative-multi"),
    pytest.param("a/b/", id="relative-trailing-slash"),
    pytest.param("/a/b/c", id="absolute-multi"),
    pytest.param("/a/b/", id="absolute-trailing-slash"),
    pytest.param("//a/b", id="leading-double-slash"),
    pytest.param("///a/b", id="leading-triple-slash"),
    pytest.param("/a//b///c", id="interior-double-slashes"),
    pytest.param("/a/./b/./c", id="interior-dot-segments"),
    pytest.param("/a/b/.", id="trailing-dot-segment"),
    pytest.param("/a/b/./", id="trailing-dot-segment-with-slash"),
    pytest.param("/a/../b", id="dotdot-pops-one"),
    pytest.param("a/../b", id="relative-dotdot-pops-one"),
    pytest.param("a/..", id="relative-dotdot-pops-to-nothing"),
    pytest.param("a/../", id="relative-dotdot-pops-to-nothing-with-slash"),
    pytest.param("/a/b/..", id="trailing-dotdot"),
    pytest.param("/a/b/../", id="trailing-dotdot-with-slash"),
    pytest.param("/..", id="dotdot-at-root"),
    pytest.param("/../a", id="dotdot-at-root-then-segment"),
    pytest.param("/a/../..", id="dotdot-past-root"),
    pytest.param("//..", id="dotdot-at-double-slash-root"),
    pytest.param("../a", id="relative-leading-dotdot"),
    pytest.param("../../a", id="relative-leading-dotdot-run"),
    pytest.param("../../a/../b", id="relative-leading-dotdot-run-then-pop"),
    pytest.param("a/b/../../..", id="relative-pops-past-start"),
    pytest.param("/a/b/c/d/../../../x", id="deep-dotdot-chain"),
    pytest.param("/a/b/c/../../../../../../x", id="deep-dotdot-chain-past-root"),
    pytest.param("/x/ y/*/..z/..../w/.", id="spaces-glob-and-dot-lookalike-segments"),
    pytest.param("/x/tab\there/new\nline/../ok", id="tab-and-newline-in-segments"),
    pytest.param("/x/$HOME/`id`/;&|/..", id="shell-metacharacters-in-segments"),
    pytest.param("/home/u/.claude//review-narrative-ledger/f.jsonl", id="ledger-doubled-slash"),
    pytest.param("/home/u/.claude/./review-narrative-ledger/f.jsonl", id="ledger-dot-segment"),
    pytest.param("/home/u/.claude/x/../review-narrative-ledger/f.jsonl", id="ledger-dotdot-segment"),
]


class TestLibNormalizePathLexically:
    @pytest.mark.parametrize("path", _LEXICAL_NORMALIZATION_INPUTS)
    def test_matches_the_normpath_oracle(self, path: str) -> None:
        assert _normalize_lexically(path) == _lexical_normalization_oracle(path)

    def test_empty_path_yields_an_empty_result(self) -> None:
        assert _normalize_lexically("") == ""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            pytest.param("//a//b", "/a/b", id="leading-double-slash-collapses-to-one"),
            pytest.param("/..", "/", id="dotdot-at-root-is-dropped"),
            pytest.param("../a", "../a", id="relative-leading-dotdot-is-kept"),
            pytest.param("a/..", ".", id="relative-path-that-pops-to-nothing-is-dot"),
            pytest.param("/a/b/", "/a/b/", id="trailing-slash-is-kept"),
            pytest.param("/a/b/.", "/a/b", id="trailing-dot-segment-is-not-a-trailing-slash"),
            pytest.param("/a/b/..", "/a", id="trailing-dotdot-is-not-a-trailing-slash"),
            pytest.param("/a/x/../b//c/", "/a/b/c/", id="trailing-slash-survives-collapsing"),
            pytest.param("a/../", "./", id="relative-dot-result-keeps-the-trailing-slash"),
        ],
    )
    def test_documented_behavior(self, path: str, expected: str) -> None:
        assert _normalize_lexically(path) == expected

    def test_a_very_long_path_terminates_with_the_correct_result(self) -> None:
        """A candidate comes from untrusted command text, so a segment count in
        the tens of thousands must still finish and come out right. The
        subprocess timeout is only a hang guard, not a scaling bound."""
        path = "/" + "/".join(["a"] * 50000) + "/../../b"
        assert _normalize_lexically(path) == "/" + "/".join(["a"] * 49998) + "/b"

    def test_a_glob_segment_stays_literal(self, tmp_path: Path) -> None:
        (tmp_path / "matching-file").write_text("")
        assert _normalize_lexically("*/x/../y", tmp_path=tmp_path) == "*/y"

    def test_runs_no_external_command(self, tmp_path: Path) -> None:
        """Sourcing _lib.sh needs `dirname`; the call itself runs with an empty
        PATH, so any external command it ran would fail. A builtin-only
        command substitution would still pass."""
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        script = (
            f'. {_LIB_SH}; PATH="$1"; '
            '_lib_normalize_path_lexically "/a//b/./c/../d/"; printf "%s" "$_LIB_NORMALIZED_PATH"'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(empty_bin)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, "/a/b/d/", "")

    @pytest.mark.parametrize("caller_glob_state", ["set -f", "set +f"])
    def test_leaves_the_callers_ifs_and_glob_setting_unchanged(self, caller_glob_state: str) -> None:
        script = (
            f'. {_LIB_SH}; {caller_glob_state}; IFS=":"; '
            '_lib_normalize_path_lexically "/a/../b"; '
            'case "$-" in *f*) printf "noglob " ;; *) printf "glob " ;; esac; printf "%s" "$IFS"'
        )
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
        expected_glob = "noglob" if caller_glob_state == "set -f" else "glob"
        assert (result.returncode, result.stdout) == (0, f"{expected_glob} :")


class TestLibRealpathM:
    def test_native_fast_path_resolves_existing_dir(self) -> None:
        result = _run_realpath_m("/tmp")
        assert result.returncode == 0
        assert result.stdout.strip() in ("/tmp", "/private/tmp")

    def test_forced_fallback_resolves_nonexistent_leaf(self, tmp_path: Path) -> None:
        target = tmp_path / "does-not-exist-xyz123.txt"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)

    def test_forced_fallback_resolves_multi_level_nonexistent_path(self, tmp_path: Path) -> None:
        target = tmp_path / "newdir1" / "newdir2" / "newfile.txt"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)

    def test_forced_fallback_resolves_deep_nonexistent_ancestor_chain_under_cap(
        self, tmp_path: Path
    ) -> None:
        """Regression test: a realistic, if unusually deep, nonexistent-
        ancestor chain must still resolve, not fail closed.

        Twenty levels models a fresh nested `.claude/` tree, not an attack
        shape, and stays well under
        `_LIB_REALPATH_M_FALLBACK_MAX_DEPTH`'s iteration cap on the
        fallback loop's ancestor walk."""
        target = Path(tmp_path, *[f"level{i}" for i in range(20)], "newfile.txt")
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)

    def test_forced_fallback_ancestor_chain_beyond_cap_fails_closed(self, tmp_path: Path) -> None:
        """Regression test for `_LIB_REALPATH_M_FALLBACK_MAX_DEPTH` itself:
        a nonexistent-ancestor chain deeper than the cap must fail closed
        (nonzero, empty stdout), not resolve.

        70 levels clears the 64-deep cap by a comfortable margin, so this
        pins the deny side symmetric to
        test_forced_fallback_resolves_deep_nonexistent_ancestor_chain_under_cap's
        allow side."""
        target = Path(tmp_path, *[f"level{i}" for i in range(70)], "newfile.txt")
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode != 0, (
            f"a chain beyond the cap must fail closed, got rc=0 stdout={result.stdout!r}"
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"

    def test_forced_fallback_resolves_chain_at_exact_cap(self, tmp_path: Path) -> None:
        """Exact-boundary pin for `_LIB_REALPATH_M_FALLBACK_MAX_DEPTH` itself,
        distinct from the generous-margin tests above: the fallback loop's
        depth counter reaches exactly the cap on a chain two directory
        levels shorter than the cap (one iteration checks the leaf file,
        one checks the existing base), so this must still resolve."""
        dirs = _REALPATH_M_FALLBACK_MAX_DEPTH - 2
        target = Path(tmp_path, *[f"level{i}" for i in range(dirs)], "newfile.txt")
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)

    def test_forced_fallback_chain_one_level_beyond_exact_cap_fails_closed(self, tmp_path: Path) -> None:
        """Exact-boundary pin symmetric to
        test_forced_fallback_resolves_chain_at_exact_cap: one more directory
        level pushes the depth counter one past the cap, so this must fail
        closed."""
        dirs = _REALPATH_M_FALLBACK_MAX_DEPTH - 1
        target = Path(tmp_path, *[f"level{i}" for i in range(dirs)], "newfile.txt")
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode != 0, (
            f"a chain one level beyond the cap must fail closed, got rc=0 stdout={result.stdout!r}"
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"

    def test_forced_fallback_collapses_dot_in_unresolved_suffix(self, tmp_path: Path) -> None:
        """Required regression test: a lone `.` component in the not-yet-
        existing suffix must be dropped, not reattached verbatim, or a
        caller comparing the result against a literal substring (e.g.
        ask-review-permissions.sh's `.claude/settings` match) misses a path
        that is semantically identical but textually decorated with `./`.
        Uses an f-string rather than pathlib's `/` operator to build the
        target, since pathlib silently collapses a `.` segment on
        construction and would defeat the test."""
        target = f"{tmp_path}/newdir1/./newfile.txt"
        result = _run_realpath_m(target, forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"{tmp_path}/newdir1/newfile.txt"

    def test_forced_fallback_rejects_dotdot_in_unresolved_suffix(self, tmp_path: Path) -> None:
        """Required regression test for a High-severity finding: the
        fallback's not-yet-existing suffix must not be reattached verbatim
        when it contains a `..` component, or a boundary check comparing
        the result against a same-prefix glob (require-plan-review.sh's
        agent-reviews/ exemption, its repo-boundary check, and
        require-memory-skill.sh's memory-tree classification) can be
        satisfied by a path that is semantically outside the intended
        boundary. Fails closed (empty output, exit 1) instead."""
        target = tmp_path / "agent-reviews" / "newdir" / ".." / ".." / ".." / "etc" / "passwd_pwned"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode != 0 or not result.stdout.strip(), (
            f"expected fail-closed (empty/nonzero) for a `..`-bearing unresolved suffix, "
            f"got stdout={result.stdout!r} rc={result.returncode}"
        )

    def test_forced_fallback_fails_closed_on_dangling_symlink(self, tmp_path: Path) -> None:
        """A dangling symlink's own leaf is `[ -e ]`-false, so the fallback's
        ancestor walk would otherwise skip past it and reattach its name as
        a literal, unresolved suffix component -- letting a same-prefix
        boundary check (require-plan-review.sh's plan-file and
        agent-reviews/ exemptions) treat the symlink's own path as if it
        resolved inside the boundary, when its real target does not. Fails
        closed (empty output, exit 1) instead."""
        symlink_path = tmp_path / "plans" / "evil.md"
        symlink_path.parent.mkdir(parents=True)
        symlink_path.symlink_to(tmp_path / "outside" / "target.py")
        result = _run_realpath_m(str(symlink_path), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode != 0 or not result.stdout.strip(), (
            f"expected fail-closed (empty/nonzero) for a dangling symlink, "
            f"got stdout={result.stdout!r} rc={result.returncode}"
        )

    def test_forced_fallback_no_double_slash_when_ancestor_is_root(self, tmp_path: Path) -> None:
        """Required regression test: when the nearest existing ancestor is
        `/` itself, the reattached suffix must not produce a doubled
        leading slash (`//foo` instead of `/foo`), which could desync from
        a canonically-formed comparison path in a caller's boundary check."""
        target = "/zzz-totally-nonexistent-root-for-test/x/y/z"
        result = _run_realpath_m(target, forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        resolved = result.stdout.strip()
        assert resolved == target
        assert "//" not in resolved

    def test_forced_fallback_dirname_failure_fails_closed(self, tmp_path: Path) -> None:
        """Regression test: a failing fallback-loop `dirname` call must not
        corrupt the walk into resolving to the hook process's own CWD.

        `_lib.sh`'s `current=$(dirname -- "$current") || return 1` checks
        the exit status precisely so a failing call (empty stdout, nonzero
        exit) returns nonzero instead of being read as success. Left
        unchecked, the corruption chain is:

        - `current` becomes "" on the swallowed failure.
        - The loop's `/`/`.` termination guard never matches an empty
          string.
        - The next iteration's `test -e "."` resolves to the CWD with exit
          0 (GNU dirname's empty-input return value) -- a result
          indistinguishable from success to a caller that only guards
          against nonzero exit.

        The stub exits nonzero with empty stdout on the targeted
        `--`-prefixed argument shape instead of running the real
        `dirname`, which would let a broken/missing `|| return 1` pass
        regardless of whether the stub fired."""
        real_dirname = shutil.which("dirname")
        if not real_dirname:
            pytest.skip("dirname not found in PATH")

        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        stub_dirname = shim_dir / "dirname"
        # Scoped to the `--`-prefixed argument shape the fallback loop
        # passes, so it doesn't also intercept _lib.sh's and _config.sh's
        # own bare, load-time `dirname "${BASH_SOURCE[0]}"` bootstrap calls.
        stub_dirname.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "--" ]; then\n'
            "  exit 1\n"
            "fi\n"
            f'exec {real_dirname} "$@"\n'
        )
        stub_dirname.chmod(0o755)

        target = tmp_path / "does-not-exist-xyz123" / "newfile.txt"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)

        assert result.returncode != 0, (
            "a failing dirname must fail closed, not return a corrupted resolution"
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"

    def test_forced_fallback_basename_failure_fails_closed(self, tmp_path: Path) -> None:
        """Regression test for the fallback loop's `basename` call
        (`_lib.sh`'s `suffix_component=$(basename -- "$current") ||
        return 1`).

        Same target shape and PATH-stub technique as
        test_forced_fallback_dirname_failure_fails_closed: the first
        `basename -- "$current"` call in the walk is the one under test.

        Pinned at a different call site than that sibling test, so a
        future edit dropping just this site's `|| return 1` isn't left
        uncaught -- independent pin, not redundant with that sibling."""
        real_basename = shutil.which("basename")
        if not real_basename:
            pytest.skip("basename not found in PATH")

        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        stub_basename = shim_dir / "basename"
        # Scoped the same way as the dirname stub above, for consistency,
        # though basename has no load-time bootstrap caller today.
        stub_basename.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "--" ]; then\n'
            "  exit 1\n"
            "fi\n"
            f'exec {real_basename} "$@"\n'
        )
        stub_basename.chmod(0o755)

        target = tmp_path / "does-not-exist-xyz123" / "newfile.txt"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)

        assert result.returncode != 0, (
            "a failing basename must fail closed, not return a corrupted resolution"
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"

    def test_forced_fallback_dot_branch_dirname_failure_fails_closed(self, tmp_path: Path) -> None:
        """Regression test for the fallback loop's `.`-branch `dirname`
        call (`_lib.sh`'s `current=$(dirname -- "$current") || return 1`
        inside the `.` case).

        Reached only after a literal `.` path segment resolves to its own
        basename mid-walk. Same target shape as
        test_forced_fallback_collapses_dot_in_unresolved_suffix.

        Pins the `.`-branch's own `|| return 1` independently of
        test_forced_fallback_dirname_failure_fails_closed, which only
        exercises the bottom-of-loop dirname call one iteration earlier."""
        real_dirname = shutil.which("dirname")
        if not real_dirname:
            pytest.skip("dirname not found in PATH")

        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        stub_dirname = shim_dir / "dirname"
        # For this target, the walk's first dirname call (bottom-of-loop,
        # on the untouched target) must succeed to reach the `.`-branch at
        # all. The second dirname call, against "<tmp_path>/newdir1/.", is
        # the one under test.
        dot_branch_arg = f"{tmp_path}/newdir1/."
        stub_dirname.write_text(
            "#!/bin/bash\n"
            f'if [ "$1" = "--" ] && [ "$2" = "{dot_branch_arg}" ]; then\n'
            "  exit 1\n"
            "fi\n"
            f'exec {real_dirname} "$@"\n'
        )
        stub_dirname.chmod(0o755)

        target = f"{tmp_path}/newdir1/./newfile.txt"
        result = _run_realpath_m(target, forced_fallback=True, tmp_path=tmp_path)

        assert result.returncode != 0, (
            "a failing `.`-branch dirname must fail closed, not return a corrupted resolution"
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"

    def test_forced_fallback_test_e_hang_capped_fails_closed(self, tmp_path: Path) -> None:
        """Regression test: a cap-fired `test -e` (nonzero, but not the
        clean "does not exist" 1) must not be misread as "does not exist"
        and fall through to the decomposition logic below.

        `_lib.sh`'s `elif [ "$test_e_status" -ne 1 ]; then return 1` branch
        exists precisely to catch that ambiguous status and fail closed
        instead.

        The ambiguous component is a real, existing directory one level up
        from the nonexistent leaf, mirroring
        test_forced_fallback_dot_branch_dirname_failure_fails_closed's
        directory-walk setup.

        Unlike the basename/dirname stubs above, `test -e`/`test -L` omit
        `--` (the loop's own comment in _lib.sh explains why), so this stub
        keys on the bare `-e PATH` shape instead of a `--`-prefix check."""
        real_test = shutil.which("test")
        if not real_test:
            pytest.skip("test(1) not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        write_scaled_timeout_shim(shim_dir)
        ambiguous_dir = tmp_path / "realdir"
        ambiguous_dir.mkdir()
        stub_test = shim_dir / "test"
        # Scoped to the bare `-e PATH` shape and the exact ambiguous
        # component under test, so it doesn't also intercept every other
        # `test -e` call the walk makes at other path depths.
        stub_test.write_text(
            "#!/bin/bash\n"
            f'if [ "$1" = "-e" ] && [ "$2" = "{ambiguous_dir}" ]; then\n'
            # Ignoring SIGTERM forces timeout(1) to escalate to `-k 2`'s SIGKILL.
            # Only that SIGKILL path emits stderr signal-death text -- a plain SIGTERM
            # kill exits quietly, so it can't discriminate the `2>/dev/null` fix below.
            "  trap '' TERM\n"
            f"  sleep {scaled_shim_sleep(10)}\n"
            "fi\n"
            f'exec {real_test} "$@"\n'
        )
        stub_test.chmod(0o755)

        target = ambiguous_dir / "does-not-exist-xyz123.txt"
        with assert_cap_engaged(shim_dir, production_cap=5, command="test"):
            result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)

        assert result.returncode != 0, (
            "a capped-out `test -e` must fail closed, not fall through as a clean \"does not exist\""
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"
        assert "Terminated" not in result.stderr and "Killed" not in result.stderr, (
            "the capped call's `2>/dev/null` must suppress the cap-firing signal-death "
            f"line, got stderr={result.stderr!r}"
        )

    def test_forced_fallback_test_l_hang_capped_fails_closed(self, tmp_path: Path) -> None:
        """Regression test for the ambiguous-status branch at `_lib.sh`'s
        `elif [ "$test_l_status" -ne 1 ]; then return 1`.

        The ambiguous component is a real, existing dangling symlink one
        level up from the nonexistent leaf, so `test -e` on it legitimately
        returns false and the walk reaches the `test -L` check before the
        stub intercepts that second call.

        Pins the same ambiguity as
        test_forced_fallback_test_e_hang_capped_fails_closed,
        independently at the `test -L` call one branch below."""
        real_test = shutil.which("test")
        if not real_test:
            pytest.skip("test(1) not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        write_scaled_timeout_shim(shim_dir)
        ambiguous_symlink = tmp_path / "dangling_link"
        ambiguous_symlink.symlink_to(tmp_path / "does-not-exist-symlink-target-xyz")
        stub_test = shim_dir / "test"
        # Scoped to the bare `-L PATH` shape and the exact ambiguous
        # component, so the walk's earlier, legitimate `test -e`/`test -L`
        # calls on other path depths still run against the real binary.
        stub_test.write_text(
            "#!/bin/bash\n"
            f'if [ "$1" = "-L" ] && [ "$2" = "{ambiguous_symlink}" ]; then\n'
            # Ignoring SIGTERM forces timeout(1) to escalate to `-k 2`'s SIGKILL.
            # Only that SIGKILL path emits stderr signal-death text -- a plain SIGTERM
            # kill exits quietly, so it can't discriminate the `2>/dev/null` fix below.
            "  trap '' TERM\n"
            f"  sleep {scaled_shim_sleep(10)}\n"
            "fi\n"
            f'exec {real_test} "$@"\n'
        )
        stub_test.chmod(0o755)

        target = ambiguous_symlink / "leaf.txt"
        with assert_cap_engaged(shim_dir, production_cap=5, command="test"):
            result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)

        assert result.returncode != 0, (
            "a capped-out `test -L` must fail closed, not fall through as a clean \"not a symlink\""
        )
        assert not result.stdout.strip(), f"expected empty stdout on fail-closed, got {result.stdout!r}"
        assert "Terminated" not in result.stderr and "Killed" not in result.stderr, (
            "the capped call's `2>/dev/null` must suppress the cap-firing signal-death "
            f"line, got stderr={result.stderr!r}"
        )

    def test_bare_invocation_under_set_e_reaches_caller_line_after_call(self, tmp_path: Path) -> None:
        """Regression test scoped to the success path: a bare invocation of
        `_lib_realpath_m` (not wrapped in `$(...)` or an `if`) must not let
        the function's internal loop -- which runs as bare statements in
        the caller's own shell -- spuriously trip the caller's `set -e` on
        a resolution that ultimately succeeds. This does not make a bare
        invocation safe in general: a call whose resolution legitimately
        fails (nonzero return) still aborts the caller under `set -e`, by
        design. Every current caller (ask-review-permissions.sh,
        enforce-marker-script-shape.sh, require-memory-skill.sh,
        require-plan-review.sh) already guards its invocation with
        `$(...)`, `if`, or `||`, and must continue to. Unlike
        `_run_realpath_m`'s `set -uo pipefail` (no `-e`), this test sources
        `_lib.sh` under `set -e` itself so a regression is actually
        exercised. The target is a normal nonexistent leaf directly under
        an existing directory, so the fallback loop's first `test -e`
        legitimately returns its ordinary false status -- the cap-timeout/
        ambiguous case is already covered by
        test_forced_fallback_test_e_hang_capped_fails_closed and
        test_forced_fallback_test_l_hang_capped_fails_closed."""
        env = dict(os.environ)
        env["PATH"] = _forced_fallback_path_env(tmp_path)

        target = tmp_path / "does-not-exist-invocation-safety.txt"
        sentinel = "REACHED_LINE_AFTER_BARE_CALL"
        script = f'set -e\n. {_LIB_SH}\n_lib_realpath_m "$1"\necho "{sentinel}"\n'
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(target)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=30,
        )

        assert result.returncode == 0, (
            f"set -e aborted the caller before its line after the bare call: "
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert sentinel in result.stdout.splitlines(), (
            f"caller never reached the line after the bare call: stdout={result.stdout!r}"
        )

    def test_bare_invocation_under_set_e_aborts_caller_on_failed_resolution(self, tmp_path: Path) -> None:
        """Failure-side counterpart to
        test_bare_invocation_under_set_e_reaches_caller_line_after_call:
        that sibling test's docstring claims a legitimately-failing bare
        invocation still aborts the caller under `set -e`, but only
        exercises the success half. This exercises the failure half, using
        the same `..`-in-unresolved-suffix target as
        test_forced_fallback_rejects_dotdot_in_unresolved_suffix, which
        reaches the loop's unguarded `return 1` in the `..` case arm."""
        env = dict(os.environ)
        env["PATH"] = _forced_fallback_path_env(tmp_path)

        target = tmp_path / "agent-reviews" / "newdir" / ".." / ".." / ".." / "etc" / "passwd_pwned"
        sentinel = "REACHED_LINE_AFTER_BARE_CALL"
        script = f'set -e\n. {_LIB_SH}\n_lib_realpath_m "$1"\necho "{sentinel}"\n'
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(target)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=30,
        )

        assert result.returncode != 0, (
            f"set -e should have aborted the caller on a failed resolution: "
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert sentinel not in result.stdout.splitlines(), (
            f"caller reached the line after the bare call despite a failed resolution: "
            f"stdout={result.stdout!r}"
        )
