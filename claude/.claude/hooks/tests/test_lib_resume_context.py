"""Unit tests for _lib.sh's resume-context helpers (_lib_resume_context_tmpdir_root,
_lib_resume_context_index_dir, _lib_print_recovery_hint, _lib_linked_worktree_root,
_lib_continuity_file_resume_cwd, _lib_resume_context_verdict and
_lib_sanitize_for_terminal).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _make_git_exiting_with_status,
    _make_git_recording_argv,
    assert_cap_engaged,
    write_continuity_body,
)

from ._lib_sh_helpers import _LIB_SH, _run_lib_call


def _run_resume_context_tmpdir_root(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_resume_context_tmpdir_root"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestLibResumeContextTmpdirRoot:
    def test_uses_resume_context_tmpdir_when_set(self) -> None:
        result = _run_resume_context_tmpdir_root(
            {"RESUME_CONTEXT_TMPDIR": "/custom-root", "TMPDIR": "/other-tmp", "PATH": os.environ["PATH"]}
        )
        assert result.stdout.strip() == "/custom-root"

    def test_falls_back_to_tmpdir_when_resume_context_tmpdir_unset(self) -> None:
        result = _run_resume_context_tmpdir_root({"TMPDIR": "/other-tmp", "PATH": os.environ["PATH"]})
        assert result.stdout.strip() == "/other-tmp"

    def test_falls_back_to_slash_tmp_when_both_unset(self) -> None:
        result = _run_resume_context_tmpdir_root({"PATH": os.environ["PATH"]})
        assert result.stdout.strip() == "/tmp"


def _run_resume_context_index_dir(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_resume_context_index_dir"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestLibResumeContextIndexDir:
    """Different-owner pre-creation can't be reproduced in single-uid CI:
    mkdir(2)'s atomicity plus the `[ -O ]` check closes the race regardless
    (see `_lib_resume_context_index_dir`), so it's recorded here rather than
    exercised. A future edit weakening the guard (e.g. `[ -O ]` -> `[ -w ]`)
    should break this documented intent instead of silently reopening the
    gap."""

    def test_creates_0700_directory_and_prints_it(self, tmp_path: Path) -> None:
        result = _run_resume_context_index_dir(
            {"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]}
        )
        assert result.returncode == 0, result.stderr
        expected_dir = tmp_path / f"resume-context-index-{os.geteuid()}"
        assert result.stdout.strip() == str(expected_dir)
        assert stat.S_IMODE(expected_dir.stat().st_mode) == 0o700

    def test_returns_1_when_tmpdir_root_is_world_writable_without_sticky_bit(
        self, tmp_path: Path
    ) -> None:
        """A world-writable, non-sticky root breaks the mkdir-to-chmod
        race-closure argument the guard rests on: another local user could
        unlink/rename the directory entry in that window. See the guard's
        own comment in _lib_resume_context_index_dir for the mechanism."""
        tmp_path.chmod(0o777)
        result = _run_resume_context_index_dir(
            {"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]}
        )
        assert result.returncode != 0
        assert result.stdout == ""

    def test_succeeds_when_tmpdir_root_is_world_writable_with_sticky_bit(
        self, tmp_path: Path
    ) -> None:
        """Mirrors production /tmp's 1777 mode -- the sticky bit is what
        keeps a world-writable root safe for this guard."""
        tmp_path.chmod(0o1777)
        result = _run_resume_context_index_dir(
            {"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]}
        )
        assert result.returncode == 0, result.stderr
        expected_dir = tmp_path / f"resume-context-index-{os.geteuid()}"
        assert result.stdout.strip() == str(expected_dir)

    def test_returns_1_when_tmpdir_root_is_group_writable_without_sticky_bit(
        self, tmp_path: Path
    ) -> None:
        """A group-writable, non-sticky root presents the same
        unlink/rename race to a same-group different-uid attacker as the
        world-writable case above. See the guard's own comment in
        _lib_resume_context_index_dir for the mechanism."""
        tmp_path.chmod(0o770)
        result = _run_resume_context_index_dir(
            {"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]}
        )
        assert result.returncode != 0
        assert result.stdout == ""

    def test_returns_1_and_prints_nothing_when_directory_is_a_symlink(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "elsewhere"
        real_dir.mkdir()
        symlinked = tmp_path / f"resume-context-index-{os.geteuid()}"
        symlinked.symlink_to(real_dir)
        result = _run_resume_context_index_dir(
            {"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]}
        )
        assert result.returncode != 0
        assert result.stdout == ""

    def test_repairs_a_preexisting_0755_directory_to_0700(self, tmp_path: Path) -> None:
        """Mirrors the file-level 0644->0600 repair pass a pre-existing
        looser-mode day-file gets from record_consumed_destination -- the
        directory's own `chmod 700` is unconditional, not gated on the mode
        already being wrong, so it needs the same symmetric coverage."""
        preexisting_dir = tmp_path / f"resume-context-index-{os.geteuid()}"
        preexisting_dir.mkdir()
        preexisting_dir.chmod(0o755)
        result = _run_resume_context_index_dir(
            {"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]}
        )
        assert result.returncode == 0, result.stderr
        assert stat.S_IMODE(preexisting_dir.stat().st_mode) == 0o700

    def test_second_call_under_set_e_does_not_abort_via_command_substitution(self, tmp_path: Path) -> None:
        """The function's doc comment states its unguarded `mkdir` is safe
        under `set -e` only because every call happens inside a `$(...)`
        command substitution, never called directly -- the second call's
        expected EEXIST must not abort the calling script. Calls the
        function twice via `x=$(...)` under `set -euo pipefail`, matching
        resume-context.sh's own `set -euo pipefail` then `. _lib.sh` order,
        and asserts both calls succeed."""
        script = (
            "set -euo pipefail\n"
            f". {_LIB_SH}\n"
            "first=$(_lib_resume_context_index_dir)\n"
            'printf "first:%s\\n" "$first"\n'
            "second=$(_lib_resume_context_index_dir)\n"
            'printf "second:%s\\n" "$second"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"RESUME_CONTEXT_TMPDIR": str(tmp_path), "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        expected_dir = tmp_path / f"resume-context-index-{os.geteuid()}"
        assert f"first:{expected_dir}" in result.stdout
        assert f"second:{expected_dir}" in result.stdout


def test_lib_print_recovery_hint_prints_reload_command_to_stderr_only() -> None:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_print_recovery_hint "$1"', "bash", "/tmp/some-dest-path"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
        check=False,
    )
    assert result.stdout == ""
    assert result.stderr.strip() == "reload with: claude --append-system-prompt-file /tmp/some-dest-path"


def _linked_worktree_root(
    directory: str, env_extra: dict[str, str] | None = None
) -> tuple[int, str]:
    """(status, stdout) of `_lib_linked_worktree_root DIR` under `set -euo pipefail`,
    captured the way production callers do: `if root=$(...); then ... else rc=$?; fi`."""
    script = (
        "set -euo pipefail\n"
        f". {_LIB_SH}\n"
        'if root=$(_lib_linked_worktree_root "$1"); then\n'
        '  printf "0\\n%s" "$root"\n'
        "else\n"
        '  printf "%s\\n%s" "$?" "$root"\n'
        "fi\n"
    )
    env = {**os.environ, **(env_extra or {})}
    result = subprocess.run(
        ["bash", "-c", script, "bash", directory], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    status, _, root = result.stdout.partition("\n")
    return int(status), root


def _add_linked_worktree(repo: Path, worktree: Path, branch: str) -> Path:
    subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(worktree)], cwd=repo, check=True)
    return worktree


_REV_PARSE_ARGV_SHAPES = {
    "git-dir": "--git-dir",
    "absolute-git-dir": "--absolute-git-dir",
    "git-common-dir": "--git-common-dir",
    "show-toplevel": "--show-toplevel",
}
# `git -C <dir> rev-parse <flag>` puts the flag at $4, except --git-common-dir,
# whose --path-format=absolute prefix takes $4 and pushes the flag to $5.
_REV_PARSE_SHIM_PREDICATES = {
    "git-dir": '[ "$3" = "rev-parse" ] && [ "$4" = "--git-dir" ]',
    "absolute-git-dir": '[ "$3" = "rev-parse" ] && [ "$4" = "--absolute-git-dir" ]',
    "git-common-dir": '[ "$3" = "rev-parse" ] && [ "$5" = "--git-common-dir" ]',
    "show-toplevel": '[ "$3" = "rev-parse" ] && [ "$4" = "--show-toplevel" ]',
}


class TestLibLinkedWorktreeRoot:
    """Exit 0 with a root is a linked worktree, exit 0 with empty output is
    "not linked", and exit 1 with empty output is "unknown"."""

    def test_empty_dir_is_unknown_and_runs_no_git(self, tmp_path: Path) -> None:
        git_log = tmp_path / "git-argv.log"
        stub_bin = tmp_path / "recording-bin"
        _make_git_recording_argv(stub_bin, git_log)
        status, root = _linked_worktree_root(
            "", {"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"}
        )
        assert (status, root) == (1, "")
        assert not git_log.exists() or git_log.read_text() == ""

    def test_directory_outside_any_repository_is_not_linked(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        status, root = _linked_worktree_root(str(outside), {"GIT_CEILING_DIRECTORIES": str(tmp_path)})
        assert (status, root) == (0, "")

    def test_main_checkout_is_not_linked(self, git_repo: Path) -> None:
        assert _linked_worktree_root(str(git_repo)) == (0, "")

    def test_main_checkout_with_separate_git_dir_is_not_linked(self, tmp_path: Path) -> None:
        checkout = tmp_path / "separate-checkout"
        checkout.mkdir()
        subprocess.run(
            ["git", "init", "-q", f"--separate-git-dir={tmp_path / 'detached-git-dir'}", str(checkout)],
            check=True,
        )
        assert _linked_worktree_root(str(checkout)) == (0, "")

    def test_linked_worktree_prints_its_root(self, git_repo: Path, tmp_path: Path) -> None:
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        assert _linked_worktree_root(str(worktree)) == (0, str(worktree.resolve()))

    def test_subdirectory_of_a_linked_worktree_prints_the_worktree_root(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        subdirectory = worktree / "nested" / "deeper"
        subdirectory.mkdir(parents=True)
        assert _linked_worktree_root(str(subdirectory)) == (0, str(worktree.resolve()))

    def test_git_common_dir_with_an_extra_absolute_line_is_unknown(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """An output that starts with `/` but spans two lines passes the leading-`/` check,
        so the newline check is what must reject it."""
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        stub_bin = tmp_path / "stray-line-git-bin"
        stub_bin.mkdir()
        stray_line_git = stub_bin / "git"
        stray_line_git.write_text(
            "#!/bin/bash\n"
            'if [ "$4" = "--path-format=absolute" ]; then\n'
            "  printf '%s\\n' /stray\n"
            "fi\n"
            'exec "$REAL_GIT" "$@"\n'
        )
        stray_line_git.chmod(0o755)
        env = {"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"}
        assert _linked_worktree_root(str(worktree), env) == (1, "")

    def test_linked_root_failing_the_path_allowlist_is_unknown(self, git_repo: Path, tmp_path: Path) -> None:
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked wt with space", "spaced-branch")
        assert _linked_worktree_root(str(worktree)) == (1, "")

    @pytest.mark.parametrize("shape", sorted(_REV_PARSE_ARGV_SHAPES))
    def test_failing_rev_parse_shape_is_unknown_with_empty_stdout(
        self, shape: str, git_repo: Path, tmp_path: Path
    ) -> None:
        """Each rev-parse call must fail closed on its own: the
        --absolute-git-dir leg in particular must not read as "not linked"."""
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        stub_bin = tmp_path / "failing-git-bin"
        _make_git_exiting_with_status(stub_bin, _REV_PARSE_ARGV_SHAPES[shape], 1)
        env = {"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"}
        assert _linked_worktree_root(str(worktree), env) == (1, "")

    @pytest.mark.parametrize(
        "shape", ["absolute-git-dir", "git-common-dir", "show-toplevel"]
    )
    def test_rev_parse_shape_exiting_0_with_empty_output_is_unknown(
        self, shape: str, git_repo: Path, tmp_path: Path
    ) -> None:
        """An empty answer with a success status must not read as "linked" or "not linked"."""
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        stub_bin = tmp_path / "empty-output-git-bin"
        _make_git_exiting_with_status(stub_bin, _REV_PARSE_ARGV_SHAPES[shape], 0)
        env = {"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"}
        assert _linked_worktree_root(str(worktree), env) == (1, "")

    def test_git_dir_exiting_128_is_not_linked(self, git_repo: Path, tmp_path: Path) -> None:
        """Pins the accepted residual: 128 is git's status for "not a repository" and for
        other fatal errors (dubious ownership, a removed directory, a pruned worktree)."""
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        stub_bin = tmp_path / "fatal-git-bin"
        _make_git_exiting_with_status(stub_bin, "--git-dir", 128)
        env = {"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"}
        assert _linked_worktree_root(str(worktree), env) == (0, "")

    @pytest.mark.timing
    @pytest.mark.parametrize("shape", sorted(_REV_PARSE_SHIM_PREDICATES))
    def test_timed_out_rev_parse_shape_is_unknown_with_empty_stdout(
        self, shape: str, git_repo: Path, tmp_path: Path, git_timeout_shim
    ) -> None:
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        env = git_timeout_shim(_REV_PARSE_SHIM_PREDICATES[shape])
        with assert_cap_engaged(tmp_path, production_cap=5):
            assert _linked_worktree_root(str(worktree), env) == (1, "")


def _continuity_file_resume_cwd(continuity_file: Path) -> tuple[int, str]:
    script = f'set -euo pipefail\n. {_LIB_SH}\n_lib_continuity_file_resume_cwd "$1"\n'
    result = subprocess.run(
        ["bash", "-c", script, "bash", str(continuity_file)],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    return result.returncode, result.stdout


def _write_continuity_file_body(tmp_path: Path, body: str) -> Path:
    """Writes `body` as handoffs/own-handoff.md, substituting {file} with its own path."""
    return write_continuity_body(tmp_path / "handoffs" / "own-handoff.md", body)


_UNUSABLE = "unknown"


class TestLibContinuityFileResumeCwd:
    """Fixtures copy the shapes handoff/SKILL.md §7 and brief/SKILL.md §7.5 prescribe.
    The answer is empty (the file holds no `--cwd` text), a path (usable), or "unknown"
    (`--cwd` text that no own command turns into one path)."""

    def test_backticked_command_with_cwd_returns_the_cwd(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path, "## §7 Resume command\n`resume-context --cwd /work/tree-a {file}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/tree-a")

    def test_command_without_cwd_returns_empty(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(tmp_path, "## §7 Resume command\n`resume-context {file}`\n")
        assert _continuity_file_resume_cwd(continuity_file) == (0, "")

    def test_brief_resume_section_after_out_of_scope_section_is_found(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "## §7 Out of scope\nDo not touch resume-context.\n\n"
            "## §7.5 Resume command\n`resume-context --cwd /work/tree-a {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/tree-a")

    def test_cwd_text_only_on_a_command_naming_another_file_is_unusable(self, tmp_path: Path) -> None:
        """The file holds `--cwd` text but no own command carries it, so "absent" is not proven."""
        continuity_file = _write_continuity_file_body(
            tmp_path, "`resume-context --cwd /work/other-tree /handoffs/other-handoff.md`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_own_command_followed_by_another_files_command_keeps_the_own_cwd(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/mine {file}`\n"
            "`resume-context --cwd /work/theirs /handoffs/other-handoff.md`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/mine")

    def test_a_line_naming_the_own_file_after_another_file_behind_a_cwd_is_unusable(self, tmp_path: Path) -> None:
        """A line is an own command when it holds a `resume-context` word and names FILE's
        basename anywhere. With `--cwd`, the basename must directly follow the token, so
        here the token is not applied to FILE and the file reads unusable, beside a valid own line."""
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/mine {file}`\n`resume-context --cwd /x other.md {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_a_line_that_never_names_the_own_file_is_ignored_beside_a_valid_own_line(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/mine {file}`\n`resume-context --cwd /x other.md`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/mine")

    def test_a_later_own_command_without_cwd_does_not_override_an_earlier_cwd(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/first {file}`\nRun resume-context {file} when ready.\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/first")

    def test_two_own_commands_naming_the_same_cwd_agree(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/tree-a {file}`\n`resume-context --cwd /work/tree-a/ {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/tree-a")

    def test_two_own_commands_naming_different_cwds_are_unusable(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/first {file}`\n`resume-context --cwd /work/second {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_one_trailing_slash_is_stripped(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path, "`resume-context --cwd /work/tree-a/ {file}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/tree-a")

    @pytest.mark.parametrize(
        "cwd_token",
        [".", "../W", "~", "~/work/a", '"/work/a"', "'/work/a'", "/work/a;rm", "/work/$(id)", "/*", "<worktree", "-elsewhere"],
        ids=[
            "dot", "relative-parent", "tilde", "tilde-path", "double-quoted", "single-quoted",
            "semicolon", "command-substitution", "asterisk", "template-placeholder", "leading-dash",
        ],
    )
    def test_unusable_cwd_token_is_unusable(self, tmp_path: Path, cwd_token: str) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path, f"`resume-context --cwd {cwd_token} {{file}}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_a_cwd_token_is_never_glob_expanded(self, tmp_path: Path) -> None:
        """`?` fails the allowlist, so only a glob expansion could make the token usable."""
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        continuity_file = _write_continuity_file_body(
            tmp_path, f"`resume-context --cwd {tmp_path}/workdir {{file}}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, str(workdir))

        continuity_file = _write_continuity_file_body(
            tmp_path, f"`resume-context --cwd {tmp_path}/workdi? {{file}}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    @pytest.mark.parametrize(
        "body",
        [
            "`resume-context --cwd=/work/a {file}`\n",
            "`resume-context {file} --cwd`\n",
            "`resume-context --cwd {file}`\n",
            "`resume-context --cwd /work/a {file} --cwd /work/b`\n",
        ],
        ids=["equals-form", "flag-without-token", "file-name-as-token", "two-tokens-on-one-line"],
    )
    def test_malformed_cwd_flag_is_unusable(self, tmp_path: Path, body: str) -> None:
        continuity_file = _write_continuity_file_body(tmp_path, body)
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    @pytest.mark.parametrize(
        "body",
        [
            "`resume-context --cwd /work/a {file}`.\n",
            "`resume-context --cwd /work/a {file}`,\n",
            "(`resume-context --cwd /work/a {file}`)\n",
            '"resume-context --cwd /work/a {file}"\n',
            "`resume-context --cwd /work/a \\\n  {file}`\n",
            "`resume-context --cwd /work/a {file}`\r\n",
            "resume-context --cwd /work/a {file}",
        ],
        ids=[
            "trailing-period", "trailing-comma", "parenthesized", "double-quoted-command",
            "backslash-continuation", "crlf", "bare-line-without-newline",
        ],
    )
    def test_surrounding_punctuation_and_line_shapes_still_yield_the_cwd(self, tmp_path: Path, body: str) -> None:
        continuity_file = _write_continuity_file_body(tmp_path, body)
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/a")

    def test_file_with_no_resume_command_returns_empty(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(tmp_path, "Just prose, no command here.\n")
        assert _continuity_file_resume_cwd(continuity_file) == (0, "")

    def test_unreadable_file_is_unusable(self, tmp_path: Path) -> None:
        assert _continuity_file_resume_cwd(tmp_path / "handoffs" / "absent-handoff.md") == (0, _UNUSABLE)

    def test_nul_byte_in_the_file_is_read_without_a_bash_warning_on_stderr(self, tmp_path: Path) -> None:
        """Bash warns "ignored null byte in input" when a command substitution captures a NUL."""
        continuity_file = _write_continuity_file_body(
            tmp_path, "\x00\n`resume-context --cwd /work/a {file}`\n"
        )
        script = f'set -euo pipefail\n. {_LIB_SH}\n_lib_continuity_file_resume_cwd "$1"\n'

        result = subprocess.run(
            ["bash", "-c", script, "bash", str(continuity_file)],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            check=False,
        )

        assert result.returncode == 0
        assert result.stdout == "/work/a"
        assert result.stderr == ""

    @pytest.mark.parametrize(
        ("shell_override", "body", "expected_cwd", "expected_verdict"),
        [
            ("grep() { return 2; }", "Just prose, no command here.\n", _UNUSABLE, "shell"),
            ("grep() { return 127; }", "Just prose, no command here.\n", _UNUSABLE, "shell"),
            ("tail() { return 1; }", "`resume-context --cwd /work/a {file}`\n", _UNUSABLE, "shell"),
            ("tail() { cat; return 1; }", "`resume-context --cwd /work/a {file}`\n", _UNUSABLE, "shell"),
            ("", "Just prose, no command here.\n", "", "load"),
        ],
        ids=[
            "search-exits-2", "search-cannot-run", "tail-fails", "tail-fails-after-printing-an-own-command",
            "no-override-names-no-worktree",
        ],
    )
    def test_only_a_whole_file_search_finding_no_cwd_text_reads_as_naming_no_worktree(
        self, tmp_path: Path, shell_override: str, body: str, expected_cwd: str, expected_verdict: str
    ) -> None:
        """A present file whose search or tail read fails must reach a `not-linked` session
        as `shell`, never `load`. A function override stands in for the failing command so the
        case does not depend on file modes, which a root-run suite ignores."""
        continuity_file = _write_continuity_file_body(tmp_path, body)
        script = (
            f"set -euo pipefail\n. {_LIB_SH}\n{shell_override}\n"
            't=$(_lib_continuity_file_resume_cwd "$1")\n'
            "printf '%s\\n' \"$t\"\n"
            '_lib_resume_context_verdict not-linked "" "$t"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(continuity_file)],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [expected_cwd, expected_verdict]

    def test_command_inside_the_scanned_tail_of_a_long_file_is_found(self, tmp_path: Path) -> None:
        filler = "x " * 20000 + "\n"
        continuity_file = _write_continuity_file_body(
            tmp_path, filler + "## §7 Resume command\n`resume-context --cwd /work/tree-a {file}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/tree-a")

    def test_command_before_the_scanned_tail_of_a_long_file_is_unusable_not_absent(self, tmp_path: Path) -> None:
        """A file longer than the scanned tail that shows no own command there may still
        hold one earlier, so it must not read as "names no worktree"."""
        filler = "x " * 20000 + "\n"
        continuity_file = _write_continuity_file_body(
            tmp_path, "`resume-context --cwd /work/tree-a {file}`\n" + filler
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_long_file_with_a_bare_own_command_in_the_tail_and_no_cwd_text_is_absent(self, tmp_path: Path) -> None:
        filler = "x " * 20000 + "\n"
        continuity_file = _write_continuity_file_body(
            tmp_path, filler + "## §7 Resume command\n`resume-context {file}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "")

    def test_cwd_command_before_the_tail_with_a_bare_own_line_in_it_is_unusable(self, tmp_path: Path) -> None:
        filler = "x " * 20000 + "\n"
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "`resume-context --cwd /work/tree-a {file}`\n" + filler + "## §7 Resume command\n`resume-context {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    @pytest.mark.parametrize(
        "body",
        [
            "**resume-context --cwd /w/a {file}**\n",
            "resume-context.sh --cwd /w/a {file}\n",
            "`resume-context --cwd /work/my tree {file}`\n",
        ],
        ids=["markdown-bold-decoration", "script-name-as-command-word", "unquoted-space-in-the-directory"],
    )
    def test_cwd_text_that_no_own_command_carries_as_a_whole_is_unusable(self, tmp_path: Path, body: str) -> None:
        """A line the parser cannot read as `--cwd TOKEN FILE` must not read as "names no worktree"."""
        continuity_file = _write_continuity_file_body(tmp_path, body)
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_cwd_prose_beside_a_bare_own_command_is_unusable(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path, "Run `yarn --cwd pkg build` first.\n## §7 Resume command\n`resume-context {file}`\n"
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, _UNUSABLE)

    def test_cwd_prose_beside_an_own_cwd_command_returns_the_own_cwd(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "Run `yarn --cwd pkg build` first.\n## §7 Resume command\n`resume-context --cwd /work/tree-a {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/tree-a")

    def test_an_earlier_own_command_without_cwd_does_not_hide_a_later_one_with_it(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(
            tmp_path,
            "Run resume-context {file} when ready.\n`resume-context --cwd /work/later {file}`\n",
        )
        assert _continuity_file_resume_cwd(continuity_file) == (0, "/work/later")

    def test_short_file_with_no_command_is_absent_not_unusable(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(tmp_path, "short body\n")
        assert _continuity_file_resume_cwd(continuity_file) == (0, "")

    def test_caller_ifs_does_not_change_the_tokenizing(self, tmp_path: Path) -> None:
        continuity_file = _write_continuity_file_body(tmp_path, "`resume-context --cwd /work/tree-a {file}`\n")
        script = (
            f"set -euo pipefail\n. {_LIB_SH}\nIFS=/\n_lib_continuity_file_resume_cwd \"$1\"\n"
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(continuity_file)],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            check=False,
        )
        assert (result.returncode, result.stdout) == (0, "/work/tree-a")


def _resume_context_verdict(state: str, root: str, file_target: str) -> str:
    script = f'set -euo pipefail\n. {_LIB_SH}\n_lib_resume_context_verdict "$1" "$2" "$3"\n'
    result = subprocess.run(
        ["bash", "-c", script, "bash", state, root, file_target],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("state", "root", "file_target", "expected"),
    [
        ("linked", "/w/a", "/w/a", "load"),
        ("linked", "/w/a", "/w/b", "shell"),
        ("linked", "/w/a", "", "shell"),
        ("not-linked", "", "", "load"),
        ("not-linked", "", "/w/a", "shell"),
        ("unknown", "", "", "shell"),
        ("unknown", "", "/w/a", "shell"),
        ("linked", "", "", "shell"),
        ("linked", "", "/w/a", "shell"),
        ("linked", "/w/a", _UNUSABLE, "shell"),
        ("not-linked", "", _UNUSABLE, "shell"),
        ("unknown", "", _UNUSABLE, "shell"),
        ("unrecognized-state", "/w/a", "/w/a", "shell"),
        ("linked", "/w/a", "/w/a/nested", "shell"),
        ("linked", "/w/a", "/w/ab", "shell"),
        ("linked", "/w/ab", "/w/a", "shell"),
    ],
    ids=[
        "linked-file-targets-this-root",
        "linked-file-targets-another-root",
        "linked-file-names-no-cwd",
        "not-linked-file-names-no-cwd",
        "not-linked-file-names-a-cwd",
        "unknown-file-names-no-cwd",
        "unknown-file-names-a-cwd",
        "linked-with-empty-root-and-no-cwd",
        "linked-with-empty-root-and-a-cwd",
        "linked-file-cwd-unreadable",
        "not-linked-file-cwd-unreadable",
        "unknown-file-cwd-unreadable",
        "unrecognized-state",
        "linked-file-targets-a-directory-inside-this-root",
        "linked-file-target-extends-this-root-name",
        "linked-file-target-is-a-prefix-of-this-root-name",
    ],
)
def test_resume_context_verdict_matrix(state: str, root: str, file_target: str, expected: str) -> None:
    assert _resume_context_verdict(state, root, file_target) == expected


# _lib_sanitize_for_terminal — direct unit coverage of all three documented
# stripped ranges (0x01-0x08, 0x0a-0x1f, 0x7f) plus the tab exemption. The
# channel-level tests in test_resume_context.py and
# test_find_consumed_continuity_file.py only exercise \x1b (middle range);
# this pins the other two ranges' boundary bytes and the tab carve-out
# directly against the helper, independent of any caller.
def test_lib_sanitize_for_terminal_strips_all_three_ranges_and_preserves_tab() -> None:
    result = _run_lib_call(
        r"_lib_sanitize_for_terminal $'a\x01b\x08c\x0ad\x1fe\x7ff\tg'",
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "abcdef\tg"


# Multi-byte UTF-8 must pass through unchanged -- the property that makes
# the byte-wise C0/DEL strip safe on a filesystem path that isn't
# guaranteed to be valid UTF-8. See _lib_sanitize_for_terminal's own header
# comment for why `local LC_ALL=C` byte-wise indexing never splits a
# multi-byte sequence at a strip point.
def test_lib_sanitize_for_terminal_passes_through_multibyte_utf8() -> None:
    result = _run_lib_call(
        "_lib_sanitize_for_terminal 'café-handoff.md'",
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "café-handoff.md"


# _lib_sanitize_for_terminal's C1 range (0x80-0x9f) pass-through is an
# accepted residual, not an oversight -- see docs/scripts.md's
# find-consumed-continuity-file.sh entry for the full rationale.
# Pinned here so a future edit that starts (or stops) stripping it shows up
# as a visible test diff instead of a silent behavior change.
# A raw 0x9b byte is not valid standalone UTF-8, so this bypasses
# _run_lib_call's text-mode decoding (which would raise UnicodeDecodeError
# on it) and captures stdout as bytes instead.
def test_lib_sanitize_for_terminal_does_not_strip_c1_bytes() -> None:
    result = subprocess.run(
        ["bash", "-c", rf". {_LIB_SH}; _lib_sanitize_for_terminal $'a\x9bb'"],
        capture_output=True,
        env=dict(os.environ),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"a\x9bb"
