"""Tests for deny-reviewer-tree-mutation.sh."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)
from test_agent_roster import CANARY_AGENTS

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

    def test_deny_reason_names_sanctioned_alternative(self):
        reason = run_hook_reason(HOOK, write_input("/repo/src/main.py", agent_type="staff-sdet"))
        assert reason is not None
        assert "/tmp" in reason
        assert "agent-reviews" in reason

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


def _run_hook_with_jq_failing_on(payload: dict, fail_token: str, tmp_path) -> str:
    """Run the hook under a jq stub that exits non-zero whenever its filter
    arguments mention `fail_token`, delegating every other jq call to the real
    binary. This lets the parse-layer jq succeed while forcing one specific
    downstream read (.agent_type or .tool_input.file_path) to fail, so the
    fail-closed deny at that call site is exercised directly — a regression
    guard against a future edit that reintroduces `local` on the assignment and
    silently flips the read back to fail-open. Mirrors test_lib.py's PATH-stub
    pattern; skips when the toolchain isn't available."""
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
    return run_hook(HOOK, payload, extra_env={"PATH": str(tmp_path)})


class TestFailClosedJqReads:
    """The .agent_type and .tool_input.file_path reads are wrapped so a jq
    failure denies rather than falling through to allow. Each test forces jq to
    fail on exactly that read (parse-layer jq still succeeds) and uses a payload
    that would otherwise ALLOW, so the deny proves the fail-closed branch — not
    an incidental deny."""

    def test_agent_type_read_failure_denies(self, tmp_path):
        # code-writer + git status would normally allow (non-reviewer, read-only);
        # with the agent_type read failing the hook can't clear the caller, so it denies.
        payload = bash_input("git status", agent_type="code-writer")
        assert _run_hook_with_jq_failing_on(payload, "agent_type", tmp_path) == "deny"

    def test_file_path_read_failure_denies(self, tmp_path):
        # reviewer + Write to /tmp would normally allow (exempt path); the
        # agent_type read succeeds (reviewer), then the file_path read fails, so
        # the hook denies at that read rather than allowing the /tmp write.
        payload = write_input("/tmp/scratch/ok.py", agent_type="staff-sdet")
        assert _run_hook_with_jq_failing_on(payload, "file_path", tmp_path) == "deny"


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
