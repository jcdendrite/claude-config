"""Tests for deny-repo-relocation.sh.

REPO_ROOT below is what the hook itself computes at runtime (readlink -f on
its own physical path, minus the /claude/.claude/hooks/deny-repo-relocation.sh
suffix) — this worktree's own root, since the hook file lives inside it and
these tests run it directly (unsymlinked), exactly like
test_hook_alignment.py's missing-_lib.sh case does. No command below is
actually executed; the hook only pattern-matches command TEXT, so exercising
it against this repo's own real root is safe.
"""
from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path

from helpers import HOOKS_DIR, bash_input, build_path_without, run_hook, run_hook_reason

HOOK = HOOKS_DIR / "deny-repo-relocation.sh"
REPO_ROOT = Path(__file__).resolve().parents[4]


class TestMvSourceIsRepoRoot:
    def test_mv_repo_root_denied(self):
        assert run_hook(HOOK, bash_input(f"mv {REPO_ROOT} /tmp/elsewhere")) == "deny"

    def test_mv_ancestor_of_repo_root_denied(self):
        assert run_hook(HOOK, bash_input(f"mv {REPO_ROOT.parent} /tmp/elsewhere")) == "deny"

    def test_deny_reason_names_relocate_claude_config(self):
        reason = run_hook_reason(HOOK, bash_input(f"mv {REPO_ROOT} /tmp/elsewhere"))
        assert reason is not None
        assert "relocate-claude-config" in reason

    def test_mv_quoted_repo_root_denied(self):
        """GH-783: a quoted source path must still deny -- without
        COMMAND_UNQUOTED, quote characters reaching readlink -f unresolved
        would fall through to this hook's documented fail-open."""
        assert run_hook(HOOK, bash_input(f'mv "{REPO_ROOT}" /tmp/elsewhere')) == "deny"

    def test_quoted_mv_word_denied(self):
        """GH-783: a quoted command word ('mv' src dst) must still be
        recognized -- $COMMAND is quote-stripped before splitting into
        fragments."""
        assert run_hook(HOOK, bash_input(f"'mv' {REPO_ROOT} /tmp/elsewhere")) == "deny"

    def test_quoted_argument_unrelated_to_repo_root_allowed(self):
        """GH-783: confirms COMMAND_UNQUOTED doesn't affect fragment
        splitting for a benign quoted argument with no token-boundary
        interaction. Not an over-strip false-positive guard:
        _lib_strip_shell_quotes only deletes quote/backslash characters, so
        it can't merge, split, or relocate a token boundary, and this
        hook's mv/rsync matcher is exact-token — there is no constructible
        near-boundary input for this hook that a broken over-strip
        implementation could flip from allow to deny."""
        assert run_hook(HOOK, bash_input('mv "/tmp/some-unrelated-dir" /tmp/elsewhere')) == "allow"

    def test_sed_absent_from_path_denied(self, tmp_path):
        """COMMAND_UNQUOTED's sed/tr strip is the earliest fork this hook
        reaches. A missing sed must deny (fail-closed) rather than let
        _lib_strip_shell_quotes's failure silently collapse fragment
        detection and fall through to this hook's normal allow path."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert (
            run_hook(
                HOOK,
                bash_input(f"mv {REPO_ROOT} /tmp/elsewhere"),
                extra_env={"PATH": restricted_path},
            )
            == "deny"
        )

    def test_fragments_split_sed_failure_denied(self, tmp_path):
        """GH-783: FRAGMENTS_SPLIT_EXIT must fail closed on its own, isolated
        from COMMAND_UNQUOTED_EXIT above -- both checks depend on the same
        sed binary, so a total sed-absent test (like the one above) can't
        tell which of the two is actually catching the failure. A sed shim
        fails on any invocation that isn't _lib_strip_shell_quotes's own
        `-e`-flagged shape, so COMMAND_UNQUOTED succeeds via the real sed
        while the later _lib_split_fragments call (a bare `sed -E
        's/.../g'`, no `-e` token) fails on its own."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        shim_dir = tmp_path / "sed-fails-outside-strip-shell-quotes-shape"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            if [ "$2" != "-e" ]; then
              exit 1
            fi
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        assert (
            run_hook(
                HOOK,
                bash_input(f"mv {REPO_ROOT} /tmp/elsewhere"),
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )


class TestMvAllowedCases:
    def test_mv_unrelated_dir_allowed(self):
        assert run_hook(HOOK, bash_input("mv /tmp/some-unrelated-dir /tmp/elsewhere")) == "allow"

    def test_mv_repo_subdirectory_rename_allowed(self):
        """Renaming a subdirectory of the repo (not the repo root itself)
        does not carry the whole checkout with it."""
        src = REPO_ROOT / "docs"
        dst = REPO_ROOT / "documentation"
        assert run_hook(HOOK, bash_input(f"mv {src} {dst}")) == "allow"

    def test_non_mv_rsync_command_allowed(self):
        assert run_hook(HOOK, bash_input("ls -la")) == "allow"

    def test_empty_bash_command_allowed(self):
        assert run_hook(HOOK, bash_input("")) == "allow"


class TestRsyncRemoveSourceFiles:
    def test_rsync_remove_source_files_repo_root_denied(self):
        cmd = f"rsync -a --remove-source-files {REPO_ROOT}/ /tmp/elsewhere/"
        assert run_hook(HOOK, bash_input(cmd)) == "deny"

    def test_plain_rsync_without_remove_source_files_allowed(self):
        """A copy (no source removal) is not a relocation."""
        cmd = f"rsync -a {REPO_ROOT}/ /tmp/elsewhere/"
        assert run_hook(HOOK, bash_input(cmd)) == "allow"


class TestKnownGapBypass:
    """row1c: source-argument resolution cannot see through shell variables
    or thread cwd across a preceding `cd` in the same command — both fail
    OPEN (allow) by design, not deny. Pinned here so a future change to
    either behavior is visible, not silent."""

    def test_variable_indirected_source_allowed(self):
        cmd = f'REPO={REPO_ROOT}; mv "$REPO" /tmp/elsewhere'
        assert run_hook(HOOK, bash_input(cmd)) == "allow"

    def test_cd_prefixed_relative_path_move_allowed(self):
        cmd = f"cd {REPO_ROOT.parent} && mv {REPO_ROOT.name} /tmp/elsewhere"
        assert run_hook(HOOK, bash_input(cmd)) == "allow"


class TestChainOperators:
    def test_relocation_in_second_fragment_denied(self):
        cmd = f"ls -la && mv {REPO_ROOT} /tmp/elsewhere"
        assert run_hook(HOOK, bash_input(cmd)) == "deny"

    def test_unrelated_pipe_allowed(self):
        assert run_hook(HOOK, bash_input("ls -la | grep mv")) == "allow"


class TestMultipleSources:
    def test_mv_multiple_sources_last_one_is_destination(self):
        """mv src1 src2 ... dir/ treats every argument but the last as a
        source; a benign leading source alongside the repo root as a later
        source must still deny."""
        cmd = f"mv /tmp/unrelated {REPO_ROOT} /tmp/dest-dir/"
        assert run_hook(HOOK, bash_input(cmd)) == "deny"

    def test_mv_repo_root_as_sole_destination_allowed(self):
        """The repo root appearing only as the FINAL (destination)
        argument must not be judged as a source."""
        cmd = f"mv /tmp/unrelated-source {REPO_ROOT}"
        assert run_hook(HOOK, bash_input(cmd)) == "allow"
