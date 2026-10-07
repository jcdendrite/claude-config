"""Tests for deny-reviewer-tree-mutation.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

from .test_agent_roster import (
    CANARY_AGENTS,
    SCRATCH_LINK_SENTENCE,
    SCRATCH_NEW_NAME_SENTENCE,
    SCRATCH_NO_RETRY_SENTENCE,
    SCRATCH_READ_TOOL_SENTENCE,
)

HOOK = HOOKS_DIR / "deny-reviewer-tree-mutation.sh"

@pytest.fixture
def repo_ignoring_agent_reviews(tmp_path):
    """Git repo with a committed .gitignore that covers agent-reviews/ —
    the safe case an agent-reviews/* write should be exempted for."""
    # Distinct subdirectory name from repo_not_ignoring_agent_reviews below —
    # a test requesting both fixtures shares one tmp_path, and both used to
    # create "repo" under it, colliding.
    repo = tmp_path / "ignoring-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("agent-reviews/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def repo_not_ignoring_agent_reviews(tmp_path):
    """Git repo with no ignore entry for agent-reviews/ reachable by
    check-ignore at all — from check-ignore's perspective this is
    indistinguishable from GH-512's actual failure mode (a stale
    worktree-local info/exclude), which lives in
    test_foreign_git_dir_env_does_not_launder_the_check instead, the one
    test that needs the real info/exclude-resident shape rather than this
    simpler "nothing ignores it" case."""
    repo = tmp_path / "not-ignoring-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class TestFileWriteTools:
    def test_reviewer_write_to_tracked_path_denied(self):
        assert run_hook(HOOK, write_input("/repo/src/main.py", agent_type="staff-sdet")) == "deny"

    def test_reviewer_write_to_findings_path_allowed(self, repo_ignoring_agent_reviews):
        """The sanctioned findings-file write must never be blocked — as long
        as agent-reviews/ is actually ignored in the target repo (GH-512)."""
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd)) == "allow"

    def test_reviewer_write_to_nested_agent_reviews_path_allowed(self, repo_ignoring_agent_reviews):
        path = str(repo_ignoring_agent_reviews / "sub" / "agent-reviews" / "staff-sdet-1700000000-branch.md")
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd)) == "allow"

    def test_reviewer_write_to_decoy_agent_reviews_filename_denied(self):
        """A file literally named agent-reviews-notes.md is a substring
        match, not a /-delimited path segment — must not be exempted."""
        path = "/repo/agent-reviews-notes.md"
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet")) == "deny"

    def test_reviewer_write_under_tmp_allowed(self):
        assert run_hook(HOOK, write_input("/tmp/scratch/copy.py", agent_type="staff-sdet")) == "allow"

    def test_reviewer_edit_to_tracked_path_denied(self):
        assert run_hook(HOOK, edit_input("/repo/src/main.py", agent_type="ciso-reviewer")) == "deny"

    def test_reviewer_multiedit_to_tracked_path_denied(self):
        assert run_hook(HOOK, multiedit_input("/repo/src/main.py", agent_type="staff-sdet")) == "deny"

    def test_agent_type_absent_allows_write(self):
        """No agent_type in the payload (main session) passes through."""
        assert run_hook(HOOK, write_input("/repo/src/main.py")) == "allow"

    def test_agent_type_main_allows_write(self):
        assert run_hook(HOOK, write_input("/repo/src/main.py", agent_type="main")) == "allow"

    def test_agent_type_code_writer_allows_write(self):
        assert run_hook(HOOK, write_input("/repo/src/main.py", agent_type="code-writer")) == "allow"

    def test_agent_type_general_purpose_allows_write(self):
        assert run_hook(HOOK, write_input("/repo/src/main.py", agent_type="general-purpose")) == "allow"

    @pytest.mark.parametrize(
        "payload",
        [
            write_input("/repo/src/main.py", agent_type="staff-sdet"),
            bash_input("sed -i s/a/b/ src/x", agent_type="staff-sdet"),
            bash_input("cat > src/x", agent_type="staff-sdet"),
        ],
        ids=["write-arm", "bash-arm", "raw-write-target-arm"],
    )
    def test_deny_reason_names_sanctioned_alternative(self, payload):
        reason = run_hook_reason(HOOK, payload)
        assert reason is not None
        assert SCRATCH_LINK_SENTENCE in reason, "denial reason lost the link-hazard sentence"
        assert SCRATCH_NEW_NAME_SENTENCE in reason, "denial reason lost the new-name sentence"
        assert SCRATCH_NO_RETRY_SENTENCE in reason, "denial reason lost the no-retry sentence"
        assert SCRATCH_READ_TOOL_SENTENCE in reason, "denial reason lost the read-tool sentence"
        assert "agent-reviews" in reason, "denial reason lost the findings-file path"
        assert "copy the file" not in reason, "denial reason sanctions a /tmp copy"

    def test_skill_fidelity_reviewer_write_to_findings_allowed(self, repo_ignoring_agent_reviews):
        """skill-fidelity-reviewer is a Write-only reviewer (no Bash/Edit) added
        to the roster; its findings-file Write is the pipeline-critical
        exemption that must never be blocked when agent-reviews/ is actually
        ignored in the target repo."""
        path = "agent-reviews/skill-fidelity-reviewer-1700000000-branch.md"
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="skill-fidelity-reviewer", cwd=cwd)) == "allow"

    def test_skill_fidelity_reviewer_write_to_tracked_denied(self):
        assert run_hook(HOOK, write_input("/repo/src/main.py", agent_type="skill-fidelity-reviewer")) == "deny"

    def test_reviewer_write_with_empty_file_path_allowed(self):
        """A file_path-less payload (e.g. NotebookEdit-shaped tool_input,
        or a malformed call) has nothing to judge — allow rather than
        deny on an empty string."""
        payload = {"tool_name": "Write", "tool_input": {}, "agent_type": "staff-sdet"}
        assert run_hook(HOOK, payload) == "allow"


class TestAgentReviewsIgnoreVerification:
    """The agent-reviews/* exemption is conditional on `git check-ignore`
    confirming the path is actually ignored in the target repo (GH-512) — a
    stale worktree-local info/exclude, or a repo with no ignore entry at all,
    must deny rather than silently let an unignored findings file through."""

    def test_not_ignored_path_denied(self, repo_not_ignoring_agent_reviews):
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd)) == "deny"

    def test_not_ignored_deny_reason_directs_to_inline_fallback(self, repo_not_ignoring_agent_reviews):
        """The deny message must send the reviewer to its documented inline
        fallback, and must NOT read as an invitation to fix the ignore state
        itself (e.g. a raw `printf ... >> .git/info/exclude`) — exactly the
        unguarded raw-Bash-redirect vector this hook's header documents as a
        known gap. Locks the message shape, not just the decision."""
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        reason = run_hook_reason(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd))
        assert reason is not None
        assert "not actually ignored" in reason
        assert "fall back to inline output" in reason.lower()
        assert "do not create or modify ignore rules yourself" in reason

    def test_cwd_outside_any_git_repo_denied(self, tmp_path):
        """A real, existing, non-repo directory makes `git check-ignore`
        itself fail (exit 128), landing in the catch-all `*` case — distinct
        from the cd-failure sentinel (exit 3) a nonexistent `.cwd` produces
        (test_cwd_does_not_exist_denied_distinctly_from_not_ignored). The
        assertion pins the catch-all's own distinguishing substring, not
        "could not confirm" alone — that phrase is shared with the sentinel-3
        message and wouldn't catch a regression that misclassified this case
        as a cd failure instead."""
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        reason = run_hook_reason(HOOK, write_input(path, agent_type="staff-sdet", cwd=str(outside)))
        assert reason is not None
        assert "not a git repo, or the check failed" in reason

    def test_missing_cwd_denied(self):
        """No .cwd in the payload at all must deny, not silently check
        whatever directory the hook process happens to be running in —
        regression test for macOS system /bin/bash 3.2 treating `cd ''` as a
        silent no-op (exit 0, stays put) rather than an error like bash 4+."""
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        reason = run_hook_reason(HOOK, write_input(path, agent_type="staff-sdet"))
        assert reason is not None
        assert "carried no .cwd" in reason

    def test_foreign_git_dir_env_does_not_launder_the_check(self, repo_not_ignoring_agent_reviews, tmp_path):
        """A GIT_DIR pointed at a different repo must not make an unrelated
        target repo's write look safe — regression test for the `unset
        GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE` fix (mirrors
        require-worktree-for-git-writes.sh:100).

        The foreign repo's ignore rule MUST live in `.git/info/exclude`, not
        a tracked `.gitignore` — verified empirically that `git check-ignore`
        reads `.gitignore` from the resolved working tree's filesystem
        (`$CWD`), never from wherever `GIT_DIR` points, so a tracked
        `.gitignore` in the foreign repo never launders the check regardless
        of the `unset` fix and would make this test pass unchanged even with
        that fix reverted. `info/exclude` is the one ignore source that
        actually lives inside `GIT_DIR` and can be laundered this way — it's
        also the exact mechanism GH-512 itself is about (a worktree-local
        `info/exclude` file)."""
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=foreign, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=foreign, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=foreign, check=True)
        (foreign / "README.md").write_text("x\n")
        subprocess.run(["git", "add", "README.md"], cwd=foreign, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=foreign, check=True)
        (foreign / ".git" / "info" / "exclude").write_text("agent-reviews/\n")

        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        foreign_git_dir = str(foreign / ".git")
        assert run_hook(
            HOOK,
            write_input(path, agent_type="staff-sdet", cwd=cwd),
            extra_env={"GIT_DIR": foreign_git_dir},
        ) == "deny"

    def test_foreign_git_work_tree_env_does_not_launder_the_check(self, repo_not_ignoring_agent_reviews, tmp_path):
        """GIT_WORK_TREE alone (no GIT_DIR) independently launders the check
        if left unset: `git check-ignore` reads a tracked `.gitignore` from
        whatever GIT_WORK_TREE points at rather than from `$CWD`, so a
        foreign repo's tracked (not info/exclude-resident) ignore rule is
        enough — no GIT_DIR override needed. Regression test for the same
        `unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE` fix as
        test_foreign_git_dir_env_does_not_launder_the_check, pinning the
        GIT_WORK_TREE vector specifically so a future edit that narrows the
        unset list to GIT_DIR alone doesn't silently reopen this bypass."""
        foreign = tmp_path / "foreign-work-tree"
        foreign.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=foreign, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=foreign, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=foreign, check=True)
        (foreign / ".gitignore").write_text("agent-reviews/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=foreign, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=foreign, check=True)

        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        assert run_hook(
            HOOK,
            write_input(path, agent_type="staff-sdet", cwd=cwd),
            extra_env={"GIT_WORK_TREE": str(foreign)},
        ) == "deny"

    def test_cwd_does_not_exist_denied_distinctly_from_not_ignored(self, tmp_path):
        """A `.cwd` naming a directory that doesn't exist must deny via the
        cd-failure sentinel, not be misreported as 'not actually ignored' —
        regression test for treating `cd "$CWD"` failure as a distinct
        outcome from git check-ignore's genuine exit 1. Distinct from
        test_cwd_outside_any_git_repo_denied, which `mkdir()`s a real
        (non-repo) directory first — here the path itself doesn't exist, so
        `cd` fails rather than `git check-ignore`."""
        does_not_exist = str(tmp_path / "never-created")
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        reason = run_hook_reason(HOOK, write_input(path, agent_type="staff-sdet", cwd=does_not_exist))
        assert reason is not None
        assert "could not confirm" in reason
        assert "not actually ignored" not in reason
        assert "does not resolve to a directory" in reason

    def test_subdirectory_cwd_resolves_path_relative_to_cwd_not_repo_root(self, tmp_path):
        """The ignore pattern here is anchored (`/agent-reviews/`, matching
        only at the exact directory the .gitignore lives in) — so a write
        actually landing in a subdirectory's own agent-reviews/ must NOT be
        treated as ignored just because the same relative path string would
        be ignored at the repo root. Regression test for `cd "$CWD"` (checks
        the same frame the Write tool resolves file_path against) versus `-C`
        against a separately-resolved repo root (would check the wrong
        location and false-allow here — verified empirically before this fix
        existed: -C repo reports the root-anchored path ignored regardless of
        which subdirectory the write actually targets)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / ".gitignore").write_text("/agent-reviews/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        sub = repo / "sub"
        sub.mkdir()
        path = "agent-reviews/staff-sdet-1700000000-branch.md"

        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=str(repo))) == "allow"
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=str(sub))) == "deny"

    def test_decoy_agent_reviews_filename_unaffected_by_ignore_check(self, repo_not_ignoring_agent_reviews):
        """A file literally named agent-reviews-notes.md never reaches the
        check-ignore gate at all — it fails the /-delimited-segment match
        before the agent-reviews/* case arm, so it denies regardless of
        whether the repo ignores agent-reviews/."""
        path = "/repo/agent-reviews-notes.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd)) == "deny"


class TestOtherTools:
    def test_reviewer_read_tool_allowed(self):
        """A reviewer's Read call is outside the Write/Edit/MultiEdit/Bash
        switch — the wildcard case arm must pass it through unconditionally."""
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/repo/src/main.py"},
            "agent_type": "staff-sdet",
        }
        assert run_hook(HOOK, payload) == "allow"


class TestBashGitWrites:
    def test_reviewer_git_checkout_denied(self):
        assert run_hook(HOOK, bash_input("git checkout -- x", agent_type="staff-platform-engineer")) == "deny"

    def test_reviewer_git_diff_allowed(self):
        assert run_hook(HOOK, bash_input("git diff", agent_type="staff-platform-engineer")) == "allow"

    def test_reviewer_git_status_allowed(self):
        assert run_hook(HOOK, bash_input("git status", agent_type="staff-platform-engineer")) == "allow"

    def test_reviewer_git_log_allowed(self):
        assert run_hook(HOOK, bash_input("git log", agent_type="staff-platform-engineer")) == "allow"

    def test_reviewer_npx_vitest_run_allowed(self):
        """Running the test suite is read-only review work."""
        assert run_hook(HOOK, bash_input("npx vitest run", agent_type="staff-sdet")) == "allow"

    def test_code_writer_sed_dash_i_allowed(self):
        """Non-reviewer + a mutating command: the gate keys on agent
        identity, not the command shape."""
        assert run_hook(HOOK, bash_input("sed -i src/x.ts", agent_type="code-writer")) == "allow"

    def test_code_writer_git_checkout_allowed(self):
        """Non-reviewer + a git write: the fast agent-type exit passes it
        through before the git-write branch ever runs, mirroring the sed -i
        allow on the git vector so a future refactor moving the agent-type
        check into the Bash arm would break this test, not ship silently."""
        assert run_hook(HOOK, bash_input("git checkout -- x", agent_type="code-writer")) == "allow"


class TestCommandInvokingGitFlagDenied:
    """A subcommand-word-only allowlist check treats 'git grep -O...' as safe
    because grep is otherwise read-only, while -O execs its argument as a
    command unconditionally -- these flags must deny regardless of
    subcommand for a review-only agent too."""

    def test_git_grep_open_files_in_pager_short_form_denied(self):
        command = 'git grep -O\'sh -c "touch /tmp/marker" #\' the README.md'
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_git_grep_open_files_in_pager_short_form_denied_with_no_embedded_dash_c(self):
        """Confound-free companion to the fixture above: that payload's own
        embedded 'sh -c "..."' produces a bare -c token that independently
        satisfies the -c arm, so it doesn't pin -O short-form detection on
        its own. This value carries no -c-shaped token anywhere."""
        command = "git grep -O'less' the README.md"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_git_log_open_files_in_pager_long_form_denied(self):
        assert run_hook(
            HOOK, bash_input("git log --open-files-in-pager=sh", agent_type="staff-sdet")
        ) == "deny"

    def test_git_log_bare_config_override_denied(self):
        assert run_hook(
            HOOK, bash_input("git -c core.pager=less log", agent_type="staff-sdet")
        ) == "deny"

    def test_git_diff_ext_diff_denied(self):
        assert run_hook(HOOK, bash_input("git diff --ext-diff", agent_type="staff-sdet")) == "deny"

    def test_git_show_textconv_denied(self):
        assert run_hook(
            HOOK, bash_input("git show --textconv HEAD:file.bin", agent_type="staff-sdet")
        ) == "deny"

    def test_git_config_env_denied(self):
        assert run_hook(
            HOOK, bash_input("git log --config-env=core.pager=SOME_ENV_VAR", agent_type="staff-sdet")
        ) == "deny"

    def test_plain_readonly_git_log_with_no_unsafe_flag_still_allowed(self):
        """Regression guard: the new flag scan must not false-deny an
        ordinary read-only git subcommand with none of the unsafe flags."""
        assert run_hook(HOOK, bash_input("git log --oneline", agent_type="staff-sdet")) == "allow"

    def test_git_log_textconv_ansi_c_hex_escape_bypass_denied(self):
        """Regression guard for the ANSI-C-escape bypass shared with
        require-review-orchestrator-bash.sh: bash's ANSI-C \\xHH hex escape
        ($'--tex\\x74conv' decodes \\x74 to 't' at exec time) reassembles the
        real --textconv flag, which _lib_strip_word_quotes does not decode
        (see docs/design-decisions.md §40's accepted-residual entry). The
        git-anchored $'.../${.../backslash check denies the fragment outright
        rather than relying on decoding the escape correctly."""
        command = "git log $'--tex\\x74conv' HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_git_log_textconv_ansi_c_octal_escape_bypass_denied(self):
        """Octal-escape variant of the hex-escape bypass above
        ($'--tex\\164conv' decodes \\164 to 't' at exec time) -- same
        blanket $'...' deny, see docs/design-decisions.md §40's
        accepted-residual entry for _lib_strip_word_quotes."""
        command = "git log $'--tex\\164conv' HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"


# --- _top_level_command_segments direct unit tests --------------------------
#
# _top_level_command_segments is a variant of _lib.sh's _lib_split_fragments
# that also splits on a bare `&` and keeps $(...)/backtick bytes intact rather
# than sub-splitting on them -- see the function's own comment in
# deny-reviewer-tree-mutation.sh for why.


def _extract_shell_function(source: Path, name: str) -> str:
    """Return NAME's function definition, delimited by its own closing brace
    line. Safe here because _top_level_command_segments has no nested braces
    in its body -- a real shell-syntax parser is unneeded."""
    text = source.read_text()
    start_marker = f"{name}() {{"
    start = text.index(start_marker)
    end = text.index("\n}", start)
    return text[start : end + len("\n}")]


def _top_level_command_segments(command: str) -> list[str]:
    function_src = _extract_shell_function(HOOK, "_top_level_command_segments")
    result = subprocess.run(
        ["bash", "-c", f'{function_src}\n_top_level_command_segments "$1"', "bash", command],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


class TestTopLevelCommandSegmentsDirect:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            # >&/<& fd duplication and &>/&>> combined redirect must stay
            # glued to their fragment -- a split here would separate the
            # redirect target from the command it applies to.
            ("cmd1 2>&1", ["cmd1 2>&1"]),
            ("cmd1 0<&1", ["cmd1 0<&1"]),
            ("cmd1 &> /dev/null", ["cmd1 &> /dev/null"]),
            ("cmd1 &>> /dev/null", ["cmd1 &>> /dev/null"]),
            # |& (combined stdout+stderr pipe) invokes two distinct
            # commands exactly like a plain `|` does, so it must split
            # cleanly -- with no stray operator byte glued onto either
            # side, which a naive protect-only-the-`&` approach produces.
            ("cmd1 |& cmd2", ["cmd1", "cmd2"]),
        ],
    )
    def test_glued_redirect_ampersand_forms_stay_whole_and_pipe_ampersand_splits(
        self, command: str, expected: list[str]
    ) -> None:
        assert _top_level_command_segments(command) == expected

    @pytest.mark.parametrize(
        "command",
        [
            "cmd1 & cmd2",
            "cmd1&cmd2",
        ],
    )
    def test_bare_ampersand_splits_into_two_segments(self, command: str) -> None:
        """A bare `&` backgrounds the left command and starts a new one, so it
        is a segment boundary -- unlike _lib_split_fragments, which leaves it
        unsplit."""
        assert _top_level_command_segments(command) == ["cmd1", "cmd2"]

    def test_subshell_paren_stripped(self) -> None:
        """Leading/trailing parens are stripped, same as _lib_split_fragments,
        so a subshell-wrapped command yields a clean git fragment rather than
        one carrying a stray trailing `)`."""
        assert _top_level_command_segments("(cd /x; git push)") == ["cd /x", "git push"]

    def test_dollar_paren_and_backtick_bytes_survive_the_split(self) -> None:
        """Unlike _lib_split_fragments, `$(` and a bare backtick are NOT split
        points here, so a git-invoking segment's own command-substitution
        bytes stay intact for the git-anchored $/backtick/brace check to
        see."""
        assert _top_level_command_segments("git log $(printf -- --textconv) HEAD") == [
            "git log $(printf -- --textconv) HEAD"
        ]
        assert _top_level_command_segments("git log `printf -- --textconv` HEAD") == [
            "git log `printf -- --textconv` HEAD"
        ]


class TestEscapeExpansionBypassRedesign:
    """Regression tests for the per-fragment escape/expansion redesign: a
    prior version paired two independently split arrays (one split on the
    raw command, one on its quote-stripped form) by index, which desynced
    whenever quote-stripping created a new adjacent-delimiter pair that
    wasn't adjacent in the raw text. The redesign uses a single coarse split
    (_top_level_command_segments) so there is nothing left to desync, and
    still catches brace expansion and command/backtick substitution, which
    the prior version's character-class check never covered at all."""

    def test_multi_fragment_index_desync_denied(self):
        """A bare `&` inside an ANSI-C string ($'&') plus a real `&`
        immediately after it splits into three raw fragments, but only two
        survive quote-stripping -- the exact desync that let the prior
        per-index-pairing design skip the real git fragment's own raw text
        entirely. Verified empirically against the unfixed hook before this
        test was written: the command below was allowed."""
        command = "echo $'&'& git log $'--tex\\x74conv' HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_brace_expansion_textconv_denied(self):
        """git show --te{x,}tconv reassembles --textconv via bash brace
        expansion, the sibling hook's own motivating example -- never
        covered by the prior $'.../${.../backslash-only character check."""
        command = "git show --te{x,}tconv HEAD:file.bin"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_command_substitution_textconv_denied(self):
        """$(printf -- --textconv) synthesizes --textconv at exec time.
        _lib_split_fragments itself splits on "$(", consuming those two
        bytes as the delimiter -- so a check that only inspects the
        resulting per-fragment text (fine-grained _lib_split_fragments
        output) can never see them. _top_level_command_segments does not
        split on "$(", so the git-invoking segment's own raw text still
        carries it."""
        command = "git log $(printf -- --textconv) HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_backtick_command_substitution_textconv_denied(self):
        """Backtick variant of the command-substitution bypass above --
        _lib_split_fragments also splits on a bare backtick, so the same
        coarser-split rationale applies."""
        command = "git log `printf -- --textconv` HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_nested_command_substitution_textconv_denied(self):
        """Nested $(...) inside $(...) is still just a $ character to this
        segment's raw text -- the git-anchored check denies on the presence
        of the construct, not on parsing its nesting depth."""
        command = "git log $(echo $(printf -- --textconv)) HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_subshell_wrapped_ansi_c_textconv_bypass_denied(self):
        """The ANSI-C hex-escape bypass still denies once wrapped in a
        subshell/group: _top_level_command_segments strips the leading '('
        and trailing ')', so the git-anchored check still sees the
        unwrapped fragment's own $'...' construct."""
        command = "(git log $'--tex\\x74conv' HEAD)"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_grep_pattern_with_legitimate_brace_denied(self):
        """Accepted over-deny: a benign literal brace in a search pattern
        (not brace-expansion syntax) still denies, because the git-anchored
        check denies on ANY brace in a git-invoking segment -- pinned as
        current behavior, not an unnoticed regression."""
        command = "git log --grep='{TODO}'"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_quoted_semicolon_metacharacter_over_deny_confound_denied(self):
        """Accepted quote-blind-split over-deny: _lib_split_fragments splits
        on a `;` even inside a quoted argument value, so a --grep pattern
        that literally contains ';sed -i ...' splits into its own fragment
        and denies via the in-place-edit family check below, even though the
        real shell would never execute that quoted text as a command. Pinned
        as current behavior, not a fix target here."""
        command = 'git log --grep="a;sed -i s/x/y/ file"'
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_quoted_ampersand_in_grep_pattern_allowed(self):
        """Confound-free companion: _lib_split_fragments does not split a bare
        `&`, so a quoted '&sed -i ...' stays inside the --grep value instead
        of becoming a fragment of its own."""
        command = 'git log --grep="a&sed -i s/x/y/ file"'
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    def test_dollar_expansion_before_bare_ampersand_git_status_allowed(self):
        """Confound-free companion: a `$` in a non-git command backgrounded
        with a bare `&` must not be attributed to the git-invoking segment
        after it. _top_level_command_segments splits on the `&`, so the
        git-anchored $/backtick/brace check sees only `git status`."""
        command = 'echo "$X" & git status'
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    def test_grep_pattern_with_legitimate_backslash_allowed(self):
        """Confound-free companion: a real git invocation with a backslash
        in an ordinary --grep pattern, and no $, backtick, {, or } anywhere,
        must not be false-denied. The prior design's bare `*\\\\*` alternative
        denied ANY backslash in a git-invoking fragment; the redesign drops
        that alternative because ANSI-C escape decoding needs a $' prefix a
        bare backslash can't supply on its own."""
        command = 'git log --grep="\\bTODO\\b"'
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"


class TestAmpersandInsideAWord:
    """An `&` inside a word (a sed replacement, a quoted path) is not a
    command separator, so it must neither hide a denied command nor
    false-deny a read-only one."""

    def test_sed_in_place_with_ampersand_replacement_denied(self):
        command = "sed 's/foo/&/' -i src/x.py"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_git_read_only_with_quoted_ampersand_path_allowed(self):
        command = 'git -C "R&D" log'
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"


class TestGitWriteTargetFlagDenied:
    """git diff/log/show (and the diff-machinery subcommands sharing their
    option parser) accept --output=<file> / --output <file>, writing the
    command's own content to a caller-chosen path with no shell redirect
    character for a `<`/`>` scan to see."""

    def test_git_diff_output_denied(self):
        command = "git diff --output=src/tracked_file.py HEAD~1..HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_git_log_output_denied(self):
        assert run_hook(
            HOOK, bash_input("git log --output=src/tracked_file.py", agent_type="staff-sdet")
        ) == "deny"

    def test_git_show_output_denied(self):
        command = "git show --output=src/tracked_file.py HEAD"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_git_log_output_indicator_flag_confound_free_companion_allowed(self):
        """Confound-free companion: --output-indicator-new shares the
        --output prefix but writes nothing anywhere -- must not false-deny."""
        assert run_hook(
            HOOK, bash_input("git log --output-indicator-new=+", agent_type="staff-sdet")
        ) == "allow"


class TestBareEnvAssignmentFragmentDenied:
    """A fragment that is ITSELF purely an environment-variable assignment
    denies regardless of whether it mentions git -- closing the cross-
    fragment path where splitting an assignment out via `;` from its
    eventual `git` invocation leaves neither fragment individually caught by
    _lib_fragment_has_env_assignment_before_git's git-anchored scan."""

    def test_git_config_env_var_mechanism_split_across_fragments_denied(self):
        """The exact multi-fragment GIT_CONFIG_* attack: each `export ...`
        fragment doesn't itself invoke git, and the final `git diff` fragment
        carries no assignment of its own -- only a per-fragment bare-
        assignment check catches this."""
        command = (
            "export GIT_CONFIG_COUNT=1; export GIT_CONFIG_KEY_0=diff.external; "
            "export GIT_CONFIG_VALUE_0=x; git diff"
        )
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_bare_export_fragment_alone_denied(self):
        assert run_hook(HOOK, bash_input("export SOME_VAR=x", agent_type="staff-sdet")) == "deny"

    def test_bare_assignment_fragment_without_export_denied(self):
        assert run_hook(HOOK, bash_input("SOME_VAR=x", agent_type="staff-sdet")) == "deny"

    def test_plain_command_with_no_bare_assignment_fragment_still_allowed(self):
        """Regression guard: the new bare-assignment scan must not false-deny
        a normal command with no env-assignment fragment anywhere."""
        assert run_hook(HOOK, bash_input("git status", agent_type="staff-sdet")) == "allow"


class TestEnvironmentVariableAssignmentBeforeGitDenied:
    """Git's own GIT_CONFIG_COUNT/GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n>
    mechanism (git-config(1) ENVIRONMENT) sets arbitrary config -- including
    diff.external -- with zero matching CLI flag token, entirely bypassing
    the flag-token scan above. A leading env-var assignment before the git
    word is denied as a blanket rule, not enumerated per variable name."""

    def test_git_config_env_var_mechanism_denied(self):
        command = (
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external "
            "GIT_CONFIG_VALUE_0='touch /tmp/marker #' git diff"
        )
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_non_git_prefixed_env_assignment_denied(self):
        """The rule is a blanket one on the WORD=value shape, not scoped to
        GIT_-prefixed names -- any env var could matter to some git
        mechanism now or in the future."""
        assert run_hook(HOOK, bash_input("FOO=bar git diff", agent_type="staff-sdet")) == "deny"

    def test_plain_command_with_no_env_assignment_still_allowed(self):
        """Regression guard: the new env-assignment scan must not false-deny
        an ordinary git command with no leading env-var assignment."""
        assert run_hook(HOOK, bash_input("git log --oneline", agent_type="staff-sdet")) == "allow"


class TestBashGitModeDependentWrites:
    """The shared _LIB_READONLY_GIT_SUBCMDS admits branch/tag/worktree/remote/
    fetch/reflog/symbolic-ref as read-only for require-worktree-for-git-writes.sh's
    working-tree-race invariant, but each writes git state with a flag. This
    hook's stricter "reviewers write no git state anywhere" invariant excludes
    them: the destructive forms deny, and the bare list forms are an accepted
    over-deny."""

    def test_reviewer_git_branch_delete_denied(self):
        assert run_hook(HOOK, bash_input("git branch -D stale", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_tag_delete_denied(self):
        assert run_hook(HOOK, bash_input("git tag -d v1.0.0", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_worktree_remove_denied(self):
        assert run_hook(HOOK, bash_input("git worktree remove ../wt", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_git_worktree_prune_denied(self):
        assert run_hook(HOOK, bash_input("git worktree prune", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_git_remote_set_url_denied(self):
        assert run_hook(HOOK, bash_input("git remote set-url origin git@x:y.git", agent_type="staff-platform-engineer")) == "deny"

    def test_reviewer_git_fetch_denied(self):
        assert run_hook(HOOK, bash_input("git fetch origin", agent_type="staff-platform-engineer")) == "deny"

    def test_reviewer_git_reflog_expire_denied(self):
        assert run_hook(HOOK, bash_input("git reflog expire --expire=now --all", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_symbolic_ref_write_denied(self):
        assert run_hook(HOOK, bash_input("git symbolic-ref HEAD refs/heads/x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_fsck_lost_found_denied(self):
        """`git fsck` is excluded from the reviewer's read-only subcommands at
        the subcommand level (it can write recovered objects into
        .git/lost-found/ with --lost-found), so EVERY `git fsck` denies
        regardless of flags — the same subcommand-level over-deny as `git
        branch`, not a --lost-found-specific flag gate."""
        assert run_hook(HOOK, bash_input("git fsck --lost-found", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_fsck_bare_over_denied(self):
        """Accepted false-positive: bare `git fsck` (read-only) also denies,
        because the exclusion is subcommand-level — pins that the over-deny is
        the whole subcommand, not just its --lost-found write flag."""
        assert run_hook(HOOK, bash_input("git fsck", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_branch_bare_list_over_denied(self):
        """Accepted false-positive: bare `git branch` (list) is read-only but
        denies, because the subcommand writes with a flag and the hook does not
        parse the second-level action. Pins the tradeoff as visible, tested
        behavior, not an accident."""
        assert run_hook(HOOK, bash_input("git branch", agent_type="staff-sdet")) == "deny"

    def test_reviewer_git_log_still_allowed(self):
        """An unconditionally-read-only subcommand is unaffected by the
        write-capable exclusion."""
        assert run_hook(HOOK, bash_input("git log --oneline", agent_type="staff-sdet")) == "allow"


class TestBashInPlaceEditFamily:
    def test_reviewer_sed_dash_i_denied(self):
        assert run_hook(HOOK, bash_input("sed -i src/x.ts", agent_type="staff-sdet")) == "deny"

    def test_reviewer_perl_dash_i_denied(self):
        assert run_hook(HOOK, bash_input("perl -i -pe 's/a/b/' src/x.ts", agent_type="staff-sdet")) == "deny"

    def test_reviewer_terraform_fmt_denied(self):
        assert run_hook(HOOK, bash_input("terraform fmt x.tf", agent_type="staff-platform-engineer")) == "deny"

    def test_reviewer_tofu_fmt_denied(self):
        # tofu shares the terraform code path via the `||` alternation; lock
        # it so a refactor collapsing or typoing that branch fails a test.
        assert run_hook(HOOK, bash_input("tofu fmt x.tf", agent_type="staff-platform-engineer")) == "deny"

    def test_reviewer_terraform_validate_allowed(self):
        # terraform/tofu gate on the fmt subcommand, so read-only subcommands
        # (validate, plan) stay available to a reviewer.
        assert run_hook(HOOK, bash_input("terraform validate", agent_type="staff-platform-engineer")) == "allow"

    def test_reviewer_gofmt_denied(self):
        # Pure formatter: denies unconditionally, no -w gating.
        assert run_hook(HOOK, bash_input("gofmt main.go", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_gofmt_dash_w_denied(self):
        assert run_hook(HOOK, bash_input("gofmt -w main.go", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_prettier_write_denied(self):
        assert run_hook(HOOK, bash_input("npx prettier --write src/x.ts", agent_type="staff-frontend-engineer")) == "deny"

    def test_reviewer_eslint_fix_denied(self):
        assert run_hook(HOOK, bash_input("npx eslint --fix src/x.ts", agent_type="staff-frontend-engineer")) == "deny"

    def test_reviewer_eslint_without_fix_allowed(self):
        # eslint is a linter, not a pure formatter — its read-only report form
        # stays allowed; only --fix denies.
        assert run_hook(HOOK, bash_input("npx eslint src/x.ts", agent_type="staff-frontend-engineer")) == "allow"

    def test_reviewer_ruff_format_denied(self):
        assert run_hook(HOOK, bash_input("ruff format src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_ruff_check_fix_denied(self):
        assert run_hook(HOOK, bash_input("ruff check --fix src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_ruff_check_without_fix_allowed(self):
        # ruff check is read-only linting — stays allowed; only ruff format
        # and any --fix* form deny.
        assert run_hook(HOOK, bash_input("ruff check src/x.py", agent_type="staff-backend-engineer")) == "allow"

    def test_reviewer_ruff_check_fix_only_denied(self):
        # --fix-only writes to disk exactly like --fix; the --fix token-prefix
        # match catches it (it did not under the old literal --fix check).
        assert run_hook(HOOK, bash_input("ruff check --fix-only src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_ruff_bare_fix_denied(self):
        # Bare `ruff --fix` (no explicit check subcommand) writes; gating on
        # the --fix flag rather than a literal `check` token catches it.
        assert run_hook(HOOK, bash_input("ruff --fix src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_ruff_fixable_without_fix_allowed(self):
        # `--fixable` filters which rules are fixable but writes nothing on its
        # own; exact --fix / --fix-only token matching (not a --fix* prefix)
        # keeps this read-only linter invocation allowed.
        assert run_hook(HOOK, bash_input("ruff check --fixable I001 src/x.py", agent_type="staff-backend-engineer")) == "allow"

    def test_reviewer_black_denied(self):
        assert run_hook(HOOK, bash_input("black src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_isort_denied(self):
        assert run_hook(HOOK, bash_input("isort src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_rustfmt_denied(self):
        assert run_hook(HOOK, bash_input("rustfmt src/x.rs", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_quoted_sed_dash_i_denied(self):
        # GH-783: a command name quoted directly in $COMMAND ('sed' -i file)
        # is caught -- $COMMAND is quote-stripped before splitting into
        # fragments, unlike the nested-shell-boundary gap
        # TestKnownGapBypass.test_reviewer_quoted_command_name_bypass_allowed
        # still documents (`bash -c "sed -i ..."`, which this scan never
        # executes).
        assert run_hook(HOOK, bash_input("'sed' -i s/a/b/ x.txt", agent_type="staff-sdet")) == "deny"

    def test_reviewer_quoted_argument_without_dash_i_allowed(self):
        # GH-783: confirms quote-stripping the command text doesn't affect
        # fragment splitting for a benign quoted argument with no
        # token-boundary interaction. Not an over-strip false-positive
        # guard: _lib_strip_shell_quotes only deletes quote/backslash
        # characters, so it can't merge, split, or relocate a token
        # boundary, and this hook's -i gate is an exact-token-prefix match
        # -- there is no constructible near-boundary input for this hook
        # that a broken over-strip implementation could flip from allow to
        # deny.
        assert run_hook(HOOK, bash_input('sed s/a/b/ "x.txt"', agent_type="staff-sdet")) == "allow"

    def test_command_unquoted_sed_absent_from_path_denied(self, tmp_path):
        # GH-783: a missing sed anywhere in the quote-strip/split pipeline
        # must fail closed rather than let a collapsed fragment detection
        # fall through to this hook's normal allow path.
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert (
            run_hook(
                HOOK,
                bash_input("'git' checkout -- some/file.txt", agent_type="staff-sdet"),
                extra_env={"PATH": restricted_path},
            )
            == "deny"
        )

    def test_fragments_split_sed_failure_denied(self, tmp_path):
        """GH-783: FRAGMENTS_SPLIT_EXIT must fail closed on its own, isolated
        from TOP_LEVEL_SEGMENTS_EXIT above -- both checks depend on the
        same sed binary, and _top_level_command_segments shares
        _lib_split_fragments's non-`-e` invocation shape, so a shim keyed
        only on the `-e` flag would trip the wrong check first. This shim
        instead fails only on _lib_split_fragments's first-stage sed call,
        identified by the `\\$\\(` substring in its script -- the only sed
        invocation in the file that splits on `$(`/backtick. Every other
        sed call, including all four inside _top_level_command_segments
        and _lib_strip_shell_quotes's own `-e`-flagged call, succeeds via
        the real sed."""
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        shim_dir = tmp_path / "sed-fails-only-on-lib-split-fragments-script"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            case "$2" in
              *'\\$\\('*) exit 1 ;;
            esac
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        # `git status` is allowed absent the shim, so a deny here can only
        # come from the fragment-split fail-closed path.
        read_only_command = "'git' status"
        assert run_hook(HOOK, bash_input(read_only_command, agent_type="staff-sdet")) == "allow"

        reason = run_hook_reason(
            HOOK,
            bash_input(read_only_command, agent_type="staff-sdet"),
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        assert reason is not None
        assert "could not split the command into fragments" in reason


class TestRawWriteTargetGap:
    """GH-751: a cp/mv/tee destination, or a `>`/`>>` shell redirect
    target, that is not literally under /tmp/ denies when the write is the
    fragment's sole or first command. Regression tests proving the header's
    own three named examples (`cp scratch src/x`, `sed ... > src/x`, `tee
    src/x`) are caught, paired with the /tmp exemption each must still
    permit. GH-811 tracks the residual gap where the same target is hidden
    behind a bare `&` in the same fragment — see
    test_reviewer_raw_write_hidden_behind_bare_ampersand_allowed below."""

    def test_reviewer_cp_to_tracked_path_denied(self):
        assert run_hook(HOOK, bash_input("cp scratch src/x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_mv_to_tracked_path_denied(self):
        assert run_hook(HOOK, bash_input("mv scratch src/x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_redirect_to_tracked_path_denied(self):
        assert run_hook(HOOK, bash_input("sed 's/a/b/' file > src/x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_append_redirect_to_tracked_path_denied(self):
        assert run_hook(HOOK, bash_input("echo x >> src/x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_tee_to_tracked_path_denied(self):
        assert run_hook(HOOK, bash_input("echo x | tee src/x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_cp_to_tmp_allowed(self):
        # Copying one file into a /tmp scratch directory is the persona scratch-execution workflow; this gate must never deny it.
        assert run_hook(HOOK, bash_input("cp src/x /tmp/scratch/x", agent_type="staff-sdet")) == "allow"

    def test_reviewer_redirect_to_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("sed 's/a/b/' src/x > /tmp/scratch/x", agent_type="staff-sdet")) == "allow"

    def test_reviewer_tee_to_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("echo x | tee /tmp/scratch/x", agent_type="staff-sdet")) == "allow"

    def test_reviewer_redirect_to_dev_null_allowed(self):
        # Common diagnostic-noise destination, not a tracked-file write —
        # must not false-deny an ordinary `... > /dev/null` invocation.
        assert run_hook(HOOK, bash_input("git status > /dev/null", agent_type="staff-sdet")) == "allow"

    def test_reviewer_redirect_traversal_out_of_tmp_denied(self):
        assert (
            run_hook(HOOK, bash_input("echo x > /tmp/../etc/passwd", agent_type="staff-sdet"))
            == "deny"
        )

    def test_reviewer_raw_write_to_agent_reviews_denied(self):
        """A raw-Bash `agent-reviews/*` write gets no exemption at all: the
        ignore-state confirmation the Write/Edit/MultiEdit arm's own
        exemption depends on (git check-ignore) has no Bash-arm
        counterpart, so this denies like any other non-/tmp target rather
        than reproducing an unchecked exemption."""
        assert (
            run_hook(HOOK, bash_input("echo findings > agent-reviews/x.md", agent_type="staff-sdet"))
            == "deny"
        )

    def test_reviewer_raw_write_to_git_info_exclude_denied(self):
        """The specific scenario test_not_ignored_deny_reason_directs_to_
        inline_fallback names as the unguarded vector: a raw redirect
        rewriting the target repo's own ignore state."""
        assert (
            run_hook(
                HOOK,
                bash_input("printf 'agent-reviews/\\n' >> .git/info/exclude", agent_type="staff-sdet"),
            )
            == "deny"
        )

    def test_code_writer_cp_to_tracked_path_allowed(self):
        # Non-reviewer + a raw write: the gate keys on agent identity, not
        # the command shape, same invariant TestBashGitWrites pins for git.
        assert run_hook(HOOK, bash_input("cp scratch src/x", agent_type="code-writer")) == "allow"

    def test_reviewer_raw_write_hidden_behind_bare_ampersand_allowed(self):
        # GH-811: pins the CURRENT (imperfect) behavior, not the desired
        # one. `_lib_split_fragments` does not split on a bare `&`, so
        # `_fragment_raw_write_targets` still resolves `cp` as this
        # fragment's command word and reads the last word of the whole
        # unsplit fragment ("/tmp/x", from the backgrounded `echo`) as the
        # destination — the real target (src/tracked_file.txt) is never
        # emitted, and the write is allowed. A fix to GH-811's underlying
        # `_lib_split_fragments` limitation should make this assertion
        # start failing; update it to "deny" then, not silently accept it.
        assert (
            run_hook(
                HOOK,
                bash_input(
                    "cp /tmp/scratch.txt src/tracked_file.txt & echo /tmp/x",
                    agent_type="staff-sdet",
                ),
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "cp /tmp/malicious.txt claude/.claude/hooks/_lib.sh",
            "printf malicious > claude/.claude/hooks/_lib.sh",
            "echo malicious | tee claude/.claude/hooks/_lib.sh",
        ],
    )
    def test_raw_bash_write_target_onto_tracked_file_denied(self, command):
        """`_fragment_raw_write_targets` resolves a cp/redirect/tee target
        from each fragment's own positional words, so the piped `tee`
        fragment's real target denies like the others. This closes the
        composed two-hop path require-review-orchestrator-agent-target.sh's
        own allowlist otherwise leaves open for a
        review-orchestrator-dispatched reviewer persona -- see
        docs/design-decisions.md §40."""
        assert run_hook(HOOK, bash_input(command, agent_type="ciso-reviewer")) == "deny"


class TestBuiltinAgents:
    """`Plan` is a harness built-in with no local agent file; `Explore` now
    has an override (`agents/Explore.md`) but is tested here too, since both
    identities must stay covered by the closed review-only set regardless of
    which grounds their membership rests on."""

    def test_explore_sed_dash_i_denied(self):
        assert run_hook(HOOK, bash_input("sed -i src/x.ts", agent_type="Explore")) == "deny"

    def test_plan_sed_dash_i_denied(self):
        assert run_hook(HOOK, bash_input("sed -i src/x.ts", agent_type="Plan")) == "deny"


class TestMalformedInput:
    def test_malformed_json_stdin_denies(self):
        result = subprocess.run(
            [str(HOOK)],
            input="not-json{{{",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip(), "Expected deny output on malformed JSON, got silent allow"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestTraversalGuard:
    """A case glob matches the literal string and does not resolve `..`, so a
    traversal path could satisfy the /tmp/* or agent-reviews/* prefix while
    resolving to a tracked repo file. The guard rejects any `..` segment."""

    def test_reviewer_write_tmp_traversal_denied(self):
        path = "/tmp/../home/user/repo/src/main.py"
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet")) == "deny"

    def test_reviewer_write_agent_reviews_traversal_denied(self):
        path = "agent-reviews/../claude/.claude/hooks/deny-reviewer-tree-mutation.sh"
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet")) == "deny"

    def test_reviewer_edit_nested_traversal_denied(self):
        path = "/repo/agent-reviews/../../src/main.py"
        assert run_hook(HOOK, edit_input(path, agent_type="ciso-reviewer")) == "deny"

    def test_double_dot_in_filename_not_treated_as_traversal(self):
        """`..` inside a filename (a..b), not as a path segment, is not a
        traversal — a legitimate /tmp write must still be allowed."""
        assert run_hook(HOOK, write_input("/tmp/a..b.py", agent_type="staff-sdet")) == "allow"


class TestExemptionPathsAllTools:
    """The /tmp and agent-reviews/ exemptions live in one Write|Edit|MultiEdit
    case arm; assert each tool exercises the allow direction so a future split
    of that arm can't silently regress Edit/MultiEdit."""

    def test_reviewer_edit_under_tmp_allowed(self):
        assert run_hook(HOOK, edit_input("/tmp/scratch/copy.py", agent_type="staff-sdet")) == "allow"

    def test_reviewer_multiedit_under_tmp_allowed(self):
        assert run_hook(HOOK, multiedit_input("/tmp/scratch/copy.py", agent_type="staff-sdet")) == "allow"

    def test_reviewer_edit_findings_path_allowed(self, repo_ignoring_agent_reviews):
        path = "agent-reviews/ciso-reviewer-1700000000-branch.md"
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, edit_input(path, agent_type="ciso-reviewer", cwd=cwd)) == "allow"

    def test_reviewer_multiedit_findings_path_allowed(self, repo_ignoring_agent_reviews):
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, multiedit_input(path, agent_type="staff-sdet", cwd=cwd)) == "allow"


class TestCommandWordResolution:
    """The in-place-edit family matches the fragment's COMMAND word, not any
    word — so a tool name appearing only as an argument (grep black) is a
    read-only command and must be allowed, while runner-wrapped invocations
    (npx prettier, python -m black) must still be caught."""

    # False positives that must NOT deny (the bug this resolution fixes).
    def test_reviewer_grep_toolname_argument_allowed(self):
        assert run_hook(HOOK, bash_input("grep -rn black .", agent_type="staff-sdet")) == "allow"

    def test_reviewer_echo_toolname_prose_allowed(self):
        assert run_hook(HOOK, bash_input("echo please run isort", agent_type="staff-sdet")) == "allow"

    def test_reviewer_git_log_grep_toolname_allowed(self):
        assert run_hook(HOOK, bash_input("git log --grep black", agent_type="staff-sdet")) == "allow"

    # Runner-wrapped invocations that MUST still be caught.
    def test_reviewer_python_dash_m_black_denied(self):
        assert run_hook(HOOK, bash_input("python -m black src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_xargs_sed_dash_i_denied(self):
        assert run_hook(HOOK, bash_input("xargs sed -i s/a/b/ {}", agent_type="staff-sdet")) == "deny"

    def test_reviewer_sudo_env_isort_denied(self):
        assert run_hook(HOOK, bash_input("sudo env X=1 isort src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_npx_flag_before_prettier_denied(self):
        assert run_hook(HOOK, bash_input("npx --yes prettier --write x.ts", agent_type="staff-frontend-engineer")) == "deny"

    def test_reviewer_absolute_path_black_denied(self):
        assert run_hook(HOOK, bash_input("/usr/bin/black src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_path_form_runner_wrapping_formatter_denied(self):
        # A path-form runner (/usr/bin/python) wrapping a formatter resolves
        # past the runner to the formatter, same as the bare-name form —
        # locks the basename runner match against silent removal.
        assert run_hook(HOOK, bash_input("/usr/bin/python -m black src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_path_form_non_subset_runner_denied(self):
        # pnpm is a runner covered only by basename (it had no path-qualified
        # alternation under the prior design) — exercises that ALL runners,
        # not a subset, resolve through their absolute path.
        assert run_hook(HOOK, bash_input("/usr/local/bin/pnpm exec prettier --write x.ts", agent_type="staff-sdet")) == "deny"

    # Runner + connector sub-token (run/exec): the connector must be skipped so
    # the command word resolves past it — the most complex branch in
    # _fragment_command_word, and the only one otherwise untested.
    def test_reviewer_poetry_run_black_denied(self):
        assert run_hook(HOOK, bash_input("poetry run black src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_pnpm_exec_eslint_fix_denied(self):
        assert run_hook(HOOK, bash_input("pnpm exec eslint --fix src/x.ts", agent_type="staff-frontend-engineer")) == "deny"


class TestPureFormatterCheckModesDenied:
    """Pure formatters (black, isort, gofmt, prettier, rustfmt, terraform/tofu
    fmt) deny on ANY invocation, including their read-only check/diff modes: a
    reviewer reads the diff, it does not run the formatter even to verify (see
    hook Grounding). Pins the deliberate over-deny so a future change re-adding
    a check-mode exemption is a visible, tested behavior change."""

    def test_reviewer_black_check_denied(self):
        assert run_hook(HOOK, bash_input("black --check src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_isort_diff_denied(self):
        assert run_hook(HOOK, bash_input("isort --diff src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_ruff_format_check_denied(self):
        assert run_hook(HOOK, bash_input("ruff format --check src/x.py", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_gofmt_diff_denied(self):
        assert run_hook(HOOK, bash_input("gofmt -d main.go", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_rustfmt_check_denied(self):
        assert run_hook(HOOK, bash_input("rustfmt --check src/x.rs", agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_prettier_check_denied(self):
        assert run_hook(HOOK, bash_input("npx prettier --check src/x.ts", agent_type="staff-frontend-engineer")) == "deny"

    def test_reviewer_terraform_fmt_check_denied(self):
        assert run_hook(HOOK, bash_input("terraform fmt -check x.tf", agent_type="staff-platform-engineer")) == "deny"

    def test_reviewer_terraform_fmt_write_false_denied(self):
        # The exact flag from the hook's motivating-incident comment — the old
        # exemption is gone, so -write=false now denies like every other fmt.
        assert run_hook(HOOK, bash_input("terraform fmt -write=false x.tf", agent_type="staff-platform-engineer")) == "deny"


class TestReadOnlyDualUseInvocationsAllowed:
    """Dual-use text tools without their write flag are read-only for a
    reviewer and must stay allowed — only the -i form denies. (Linter read
    forms, ruff check / eslint, are covered in TestBashInPlaceEditFamily.)"""

    def test_reviewer_sed_without_dash_i_allowed(self):
        assert run_hook(HOOK, bash_input("sed s/a/b/ x.txt", agent_type="staff-sdet")) == "allow"

    def test_reviewer_perl_without_dash_i_allowed(self):
        assert run_hook(HOOK, bash_input("perl -pe s/a/b/ x.txt", agent_type="staff-sdet")) == "allow"


class TestKnownGapBypass:
    """Locks the accepted known gap (a quoted command name bypasses the word
    scan) so a future change to that behavior is visible, not silent."""

    def test_reviewer_quoted_command_name_bypass_allowed(self):
        # `bash -c "sed -i ..."`: the scan sees the glued token `"sed`, not
        # `sed`. Documented as an accepted gap under the cooperative model.
        assert run_hook(HOOK, bash_input('bash -c "sed -i s/a/b/ x"', agent_type="staff-sdet")) == "allow"

    def test_reviewer_sed_combined_short_option_cluster_allowed(self):
        # Documented "Known gaps" miss: the -i-prefix check only matches a
        # token starting `-i`, so a combined cluster (`-ni`) is not caught.
        # Pin the accepted allow so a future change to the matcher is visible.
        assert run_hook(HOOK, bash_input("sed -ni s/a/b/p x.txt", agent_type="staff-sdet")) == "allow"

    def test_reviewer_sed_long_in_place_flag_allowed(self):
        # Same documented gap: GNU sed's `--in-place` long form starts `--i`,
        # not `-i`, so it is not caught. Pin the accepted allow.
        assert run_hook(HOOK, bash_input("sed --in-place s/a/b/ x.txt", agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "sed -i s/a/b/ x.txt & git diff",
            "git diff & black x.py",
            "sed -i s/a/b/ x.txt # git diff",
            "git diff > src/x",
        ],
        ids=[
            "write-then-git-partner",
            "git-partner-then-formatter",
            "write-then-git-word-in-comment",
            "git-fragment-raw-redirect",
        ],
    )
    def test_reviewer_git_word_fragment_skips_later_checks_allowed(self, command):
        """GH-811, GH-1208: pins the CURRENT (imperfect) behavior of an accepted
        gap, not the desired one. A fragment holding a `git` word anywhere gets
        only the git checks, so every later check is skipped for it, the raw
        write-target check included. These cases illustrate that mechanism;
        they do not list every shape it covers. Flip an allow case to "deny"
        when this mechanism changes."""
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command, expected_reason_fragment",
        [
            ("sed -i s/a/b/ x.txt # ls", "'sed -i'/'perl -i' rewrites the file in place."),
            ("ls & git checkout -- f", "'git checkout' is not a read-only git subcommand"),
        ],
        ids=["comment-without-git-word", "git-write-behind-non-git-head"],
    )
    def test_reviewer_git_word_mechanism_neighbor_shapes_denied(self, command, expected_reason_fragment):
        """Control for the git-word mechanism above: a trailing comment with no
        git word still reaches the in-place-edit check, and a git write
        sharing a fragment still reaches the git checks, so a regression that
        widens the skip fails here."""
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert expected_reason_fragment in reason

    @pytest.mark.parametrize(
        "command",
        [
            "ls & sed -i s/a/b/ x.txt",
            "ls & black x.py",
            "ls |& sed -i s/a/b/ x.txt",
        ],
        ids=[
            "write-after-bare-ampersand",
            "formatter-after-bare-ampersand",
            "write-after-pipe-ampersand",
        ],
    )
    def test_reviewer_non_git_checks_read_only_first_command_allowed(self, command):
        """GH-811, GH-1208: pins the CURRENT (imperfect) behavior of an accepted
        gap, not the desired one. The shared splitter does not split on a bare
        `&` and splits `|&` only at its `|`. A command after either one is not
        reliably checked. These cases illustrate that mechanism; they do not
        list every shape it covers. Flip an allow case to "deny" when this mechanism
        changes."""
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command, expected_reason_fragment",
        [
            ("sed -i s/a/b/ x.txt & ls", "'sed -i'/'perl -i' rewrites the file in place."),
            ("black x.py & ls", "'black' reformats files"),
            ("sed -i s/a/b/ x.txt |& ls", "'sed -i'/'perl -i' rewrites the file in place."),
        ],
        ids=[
            "write-before-bare-ampersand",
            "formatter-before-bare-ampersand",
            "write-before-pipe-ampersand",
        ],
    )
    def test_reviewer_non_git_checks_first_command_write_denied(self, command, expected_reason_fragment):
        """Control for the first-command mechanism above: the same write or
        formatter as the first command of a joined fragment is still denied, so
        a regression that stops reading the first command fails here."""
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert expected_reason_fragment in reason

    # GH-1103: the three tests below pin the CURRENT (imperfect) allow verdicts
    # for the /tmp link gap in the header's known-gaps list. The hook matches
    # `/tmp/*` as literal text and never resolves links, so each form launders
    # a write onto a file outside /tmp. When GH-1103 closes the gap, flip the
    # matching assertion to "deny" instead of silently accepting it.
    def test_reviewer_hard_link_into_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("ln src/x /tmp/y", agent_type="ciso-reviewer")) == "allow"

    def test_reviewer_file_symlink_into_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("ln -s src/x /tmp/y", agent_type="ciso-reviewer")) == "allow"

    def test_reviewer_write_through_directory_symlink_allowed(self):
        assert run_hook(HOOK, bash_input("ln -s src /tmp/d", agent_type="ciso-reviewer")) == "allow"
        assert run_hook(HOOK, write_input("/tmp/d/file", agent_type="ciso-reviewer")) == "allow"
        assert run_hook(HOOK, bash_input("cat > /tmp/d/file", agent_type="ciso-reviewer")) == "allow"


class TestScratchDirectoryWorkflow:
    """The persona scratch-execution workflow: a mktemp directory under /tmp,
    then writes spelled out literally under it."""

    @pytest.mark.parametrize(
        "command",
        [
            "mktemp -d /tmp/staff-sdet.XXXXXX",
            "echo x > /tmp/staff-sdet.abc123/out.txt",
            "cd /tmp/staff-sdet.abc123 && echo x > /tmp/staff-sdet.abc123/out.txt",
            "git --no-optional-locks status",
        ],
        ids=[
            "mktemp-directory",
            "literal-tmp-write",
            "literal-tmp-write-after-cd",
            "git-no-optional-locks-status",
        ],
    )
    def test_reviewer_scratch_workflow_command_allowed(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "echo x > $SCRATCH/out.txt",
            "cd /tmp/staff-sdet.abc123 && echo x > out.txt",
        ],
        ids=["variable-target", "relative-target-after-cd"],
    )
    def test_reviewer_unliteral_write_target_denied_as_outside_tmp(self, command):
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert "', which is outside /tmp." in reason


class TestChainOperators:
    """The shared fragment splitter must catch a mutation in a non-leading
    fragment, and must not false-deny a read-only fragment chain."""

    def test_reviewer_mutation_in_second_fragment_denied(self):
        assert run_hook(HOOK, bash_input("git status && sed -i s/a/b/ x", agent_type="staff-sdet")) == "deny"

    def test_reviewer_readonly_pipe_with_toolname_allowed(self):
        assert run_hook(HOOK, bash_input("git diff | grep black", agent_type="staff-sdet")) == "allow"

    def test_reviewer_empty_bash_command_allowed(self):
        assert run_hook(HOOK, bash_input("", agent_type="staff-sdet")) == "allow"


class TestAgentTypeMatchSemantics:
    """The fast-exit gate keyed on agent_type is the entire security boundary;
    pin its exact-match semantics so a harness change emitting a padded or
    re-cased agent_type can't silently downgrade a reviewer to unrestricted
    write access with nothing catching the regression."""

    def test_empty_string_agent_type_allows(self):
        """An explicit empty-string agent_type in the payload allows. At the
        hook level `jq -r '.agent_type // empty'` plus the `[ -n ]` guard
        collapse empty-string and absent-key to the same state, so this pins
        payload-construction correctness (the key is present but empty), not a
        distinct hook-level boundary beyond test_agent_type_absent_allows_write."""
        assert run_hook(HOOK, bash_input("sed -i s/a/b/ x", agent_type="")) == "allow"

    def test_casing_variant_agent_type_allows(self):
        """Match is case-sensitive exact; a re-cased near-miss does not match,
        so it falls through to allow rather than being treated as the reviewer."""
        assert run_hook(HOOK, bash_input("sed -i s/a/b/ x", agent_type="Staff-Sdet")) == "allow"

    def test_trailing_whitespace_agent_type_allows(self):
        """Trailing whitespace is not trimmed before matching; the padded value
        is not a roster member."""
        assert run_hook(HOOK, bash_input("sed -i s/a/b/ x", agent_type="staff-sdet ")) == "allow"


def _run_hook_with_jq_failing_on(payload: dict, fail_token: str, tmp_path) -> str | None:
    """Run the hook under a jq stub that exits non-zero whenever its filter
    arguments mention `fail_token`, delegating every other jq call to the real
    binary. Returns the deny reason (or `None` on silent allow) via
    run_hook_reason, so a caller can assert on the message rather than only the
    decision. `agent_type` and `file_path` both appear inside
    _lib_parse_tool_input_or_deny's own combined jq filter string (which reads
    `.agent_type // ""` and `.tool_input.file_path // ""` in the same call as
    every other extracted field), so failing on either token fails that shared
    parse-layer call — this hook issues no jq call of its own for either
    field. Mirrors test_lib.py's PATH-stub pattern; skips when the toolchain
    isn't available."""
    real_jq = shutil.which("jq")
    bash = shutil.which("bash")
    if not real_jq or not bash:
        pytest.skip("jq/bash not available in PATH")
    fake_jq = tmp_path / "jq"
    fake_jq.write_text(
        "#!/bin/bash\n"
        'for arg in "$@"; do\n'
        f'  case "$arg" in *{fail_token}*) exit 1 ;; esac\n'
        "done\n"
        f'exec {real_jq} "$@"\n'
    )
    fake_jq.chmod(0o755)
    for cmd in ("bash", "timeout", "cat", "printf", "head", "tail", "cut", "dirname", "sed", "grep", "tr"):
        cmd_path = shutil.which(cmd)
        if cmd_path:
            (tmp_path / cmd).symlink_to(cmd_path)
    return run_hook_reason(HOOK, payload, extra_env={"PATH": str(tmp_path)})


class TestFailClosedJqReads:
    """AGENT_TYPE and FILE_PATH are populated by _lib_parse_tool_input_or_deny's
    single shared jq call, and _lib.sh's own fail-closed handling denies with
    the generic parse-failure message on any jq failure in that call — before
    this hook's own AGENT_TYPE/FILE_PATH-consuming logic
    (_lib_is_review_only_agent, the Write/Edit/MultiEdit FILE_PATH branch)
    ever runs. This hook holds no per-site fail-closed fork of its own for
    either field, since one would be redundant with that shared guarantee.
    Each test forces jq to fail on a token that only appears in the shared
    parser's combined filter string, using a payload that would otherwise
    ALLOW, so the generic parse-failure deny proves the shared guard fired
    ahead of this hook's own logic rather than some incidental deny."""

    def test_agent_type_token_failure_denies_via_shared_parser(self, tmp_path):
        # code-writer + git status would normally allow (non-reviewer, read-only).
        payload = bash_input("git status", agent_type="code-writer")
        reason = _run_hook_with_jq_failing_on(payload, "agent_type", tmp_path)
        assert reason is not None
        assert "could not parse tool-input JSON" in reason

    def test_file_path_token_failure_denies_via_shared_parser(self, tmp_path):
        # reviewer + Write to /tmp would normally allow (exempt path).
        payload = write_input("/tmp/scratch/ok.py", agent_type="staff-sdet")
        reason = _run_hook_with_jq_failing_on(payload, "file_path", tmp_path)
        assert reason is not None
        assert "could not parse tool-input JSON" in reason


def _review_only_roster() -> list[str]:
    """Read the closed review-only set from _lib.sh — the single source of
    truth — so a persona rename/drop in _LIB_REVIEW_ONLY_AGENTS is caught."""
    lib = HOOKS_DIR / "_lib.sh"
    result = subprocess.run(
        ["bash", "-c", f". {lib} && _lib_review_only_agents"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


class TestFullRoster:
    def test_every_review_only_agent_denies_a_mutation(self):
        """Every member of _LIB_REVIEW_ONLY_AGENTS must deny a representative
        mutation — catches an untested member and future array drift."""
        roster = _review_only_roster()
        assert len(roster) == 12, f"roster changed: {roster}"
        for agent in roster:
            decision = run_hook(HOOK, bash_input("sed -i s/a/b/ src/x.ts", agent_type=agent))
            assert decision == "deny", f"{agent} did not deny a mutation"

    def test_file_backed_reviewers_are_all_gated(self):
        """Every file-backed reviewer agent must appear in the gate roster.

        The gate roster (_LIB_REVIEW_ONLY_AGENTS) and the file-backed reviewer
        roster (CANARY_AGENTS in test_agent_roster.py — the agents/*.md
        personas that emit findings output) are deliberately kept as separate
        sources of truth: the gate additionally carries the harness built-ins
        Explore/Plan — Plan has no .md file at all, and Explore's override
        file exists but isn't a code-review-dispatched findings-emitting
        persona, so it lives in NON_REVIEWER_AGENTS rather than
        CANARY_AGENTS — so the relation is subset, not equality. Without this
        cross-check the split is a silent-drift hazard: a new staff-*
        reviewer registered in CANARY_AGENTS (forced by
        test_doc_counts) but forgotten in _LIB_REVIEW_ONLY_AGENTS would be
        un-gated and free to mutate the tree under review. This test turns
        that omission into a loud failure."""
        gate_roster = set(_review_only_roster())
        file_backed_reviewers = {name.removesuffix(".md") for name in CANARY_AGENTS}
        missing = file_backed_reviewers - gate_roster
        assert not missing, (
            f"file-backed reviewer(s) {sorted(missing)} are registered in "
            f"CANARY_AGENTS but missing from _LIB_REVIEW_ONLY_AGENTS — they "
            f"would be un-gated and able to mutate the tree under review"
        )
