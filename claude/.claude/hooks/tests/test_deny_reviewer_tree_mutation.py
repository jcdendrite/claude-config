"""Tests for deny-reviewer-tree-mutation.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
import uuid

import pytest
from helpers import (
    FINDING_CHECK_LABEL,
    HOOKS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    fresh_reviewer_findings_session,
    init_git_repo_with_commit,
    multiedit_input,
    review_only_agents,
    run_hook,
    run_hook_reason,
    run_hook_reason_and_stderr,
    write_input,
)

from .test_agent_roster import CANARY_AGENTS

HOOK = HOOKS_DIR / "deny-reviewer-tree-mutation.sh"

# The first four sentences below are the denial discipline deny-reviewer-tree-mutation.sh
# puts in its Bash-arm denial text. `Plan` is the only roster member that reaches
# that arm, so they are its only discipline. The Write arm repeats only the
# no-retry and read-tool sentences. DENIAL_INLINE_FALLBACK_SENTENCE is the Write arm's
# own; the Bash-arm text lacks it.
DENIAL_LINK_SENTENCE = (
    "A write through a symlink or hard link changes the linked file, "
    "wherever it lives, so a /tmp path can still change a file outside /tmp."
)

DENIAL_NEW_NAME_SENTENCE = (
    "Never overwrite or replace an existing path, even one you created; "
    "write a new file under a new name instead."
)

DENIAL_NO_RETRY_SENTENCE = (
    "Do not retry any other denied action through a script, another command "
    "form, or another tool."
)

DENIAL_READ_TOOL_SENTENCE = "Use Read, Grep, or Glob for a read the hook misjudges."

DENIAL_INLINE_FALLBACK_SENTENCE = "If that write is denied, return your findings inline and write nothing else."

pytestmark = pytest.mark.usefixtures("isolated_git_environment")


@pytest.fixture
def repo_ignoring_agent_reviews(tmp_path):
    """Git repo with a committed .gitignore that covers agent-reviews/ —
    the safe case an agent-reviews/* write should be exempted for."""
    # Distinct subdirectory name from repo_not_ignoring_agent_reviews below —
    # a test requesting both fixtures shares one tmp_path, and both used to
    # create "repo" under it, colliding.
    return init_git_repo_with_commit(tmp_path / "ignoring-repo", file_name=".gitignore", content="agent-reviews/\n")


@pytest.fixture
def repo_not_ignoring_agent_reviews(tmp_path):
    """Git repo with no ignore entry for agent-reviews/ reachable by
    check-ignore at all — from check-ignore's perspective this is
    indistinguishable from GH-512's actual failure mode (a stale
    worktree-local info/exclude), which lives in
    test_foreign_git_dir_env_does_not_launder_the_check instead, the one
    test that needs the real info/exclude-resident shape rather than this
    simpler "nothing ignores it" case."""
    return init_git_repo_with_commit(tmp_path / "not-ignoring-repo", file_name="README.md")


@pytest.fixture
def repo_ignoring_agent_reviews_and_build(repo_ignoring_agent_reviews):
    """repo_ignoring_agent_reviews with a second ignored directory, so a path
    that resolves out of agent-reviews/ into build/ is still ignored and only
    the hook's own parent and name rules can deny it."""
    (repo_ignoring_agent_reviews / ".gitignore").write_text("agent-reviews/\nbuild/\n")
    return repo_ignoring_agent_reviews


@pytest.fixture
def reviewer_findings_session():
    """A fresh uuid4 session id and the path of its /review-pr findings directory, which is not created."""
    with fresh_reviewer_findings_session() as session:
        yield session


# The Write, Edit and MultiEdit tools reach one case arm. Tests parametrized over these builders pin the rules
# that hold for all three; the /tmp findings-directory exemption is Write-only and is pinned separately.
_FILE_TOOL_BUILDERS = {"Write": write_input, "Edit": edit_input, "MultiEdit": multiedit_input}


def _assert_write_arm_denied(payload):
    """Assert the Write arm denies `payload` with its not-your-findings-file text and writes nothing to stderr
    (a backtick in the denial constant would run a shell command substitution whose error lands there)."""
    reason, stderr = run_hook_reason_and_stderr(HOOK, payload)
    assert reason is not None, f"expected a deny for {payload}"
    assert "which is not your own findings file" in reason, reason
    assert stderr == "", f"a successful deny wrote to stderr: {stderr!r}"


def _assert_clean_allow(payload):
    """Assert the hook allows `payload` with no deny reason and nothing on stderr, so a pass cannot come from
    a non-JSON or error-path output that run_hook reads as an allow."""
    assert run_hook_reason_and_stderr(HOOK, payload) == (None, "")


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
        """Accepted debt: the engineer answered "Accept nested depth as permanent debt (Recommended)", recorded in
        docs/design-decisions/reviewer-personas-hold-no-bash.md. findings-path-suffix.sh writes an unanchored
        ignore pattern, so a session started in a subdirectory relies on nested agent-reviews/ directories being
        ignored, and the reach is any directory of that name. The caller's own-name rule is what limits a persona
        to its own findings file there. Flip to deny only with a fresh stop-and-ask."""
        path = str(repo_ignoring_agent_reviews / "sub" / "agent-reviews" / "staff-sdet-1700000000-branch.md")
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd)) == "allow"

    def test_reviewer_write_to_decoy_agent_reviews_filename_denied(self):
        """A file literally named agent-reviews-notes.md is a substring
        match, not a /-delimited path segment — must not be exempted."""
        path = "/repo/agent-reviews-notes.md"
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet")) == "deny"

    def test_reviewer_write_under_tmp_denied(self):
        """A reviewer has no scratch directory: its only write is its own findings file."""
        _assert_write_arm_denied(write_input("/tmp/scratch/copy.py", agent_type="staff-sdet"))

    def test_reviewer_write_creating_own_review_pr_findings_file_allowed(self, reviewer_findings_session):
        """A Write creating the caller's own new file in its session's existing /review-pr findings directory is the
        one sanctioned /tmp target."""
        session_id, findings_dir = reviewer_findings_session
        findings_dir.mkdir()
        path = str(findings_dir / "staff-sdet-1700000000-branch.md")
        payload = write_input(path, agent_type="staff-sdet")
        payload["session_id"] = session_id
        _assert_clean_allow(payload)

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
            bash_input("sed -i s/a/b/ src/x", agent_type="staff-sdet"),
            bash_input("cat > src/x", agent_type="staff-sdet"),
            bash_input("~/.claude/scripts/worktree-ensure.sh x", agent_type="staff-sdet"),
        ],
        ids=["bash-arm", "raw-write-target-arm", "worktree-ensure-arm"],
    )
    def test_bash_arm_deny_reason_names_sanctioned_alternative(self, payload):
        reason = run_hook_reason(HOOK, payload)
        assert reason is not None
        assert DENIAL_LINK_SENTENCE in reason, "denial reason lost the link-hazard sentence"
        assert DENIAL_NEW_NAME_SENTENCE in reason, "denial reason lost the new-name sentence"
        assert DENIAL_NO_RETRY_SENTENCE in reason, "denial reason lost the no-retry sentence"
        assert DENIAL_READ_TOOL_SENTENCE in reason, "denial reason lost the read-tool sentence"
        assert "agent-reviews" in reason, "denial reason lost the findings-file path"
        assert "copy the file" not in reason, "denial reason sanctions a /tmp copy"

    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_write_arm_deny_reason_names_only_the_findings_file(self, tool_name):
        """The Write arm's denial keeps the no-retry and read-tool sentences, names the dispatch's `findings_path` and
        the inline fallback, and offers no scratch directory, link rule or `Check:` line, because a reviewer holds no
        Bash to use them with."""
        payload = _FILE_TOOL_BUILDERS[tool_name]("/repo/src/main.py", agent_type="staff-sdet")
        reason, stderr = run_hook_reason_and_stderr(HOOK, payload)
        assert reason is not None
        assert DENIAL_NO_RETRY_SENTENCE in reason
        assert DENIAL_READ_TOOL_SENTENCE in reason
        assert "findings_path" in reason
        assert DENIAL_INLINE_FALLBACK_SENTENCE in reason
        assert FINDING_CHECK_LABEL not in reason
        assert DENIAL_LINK_SENTENCE not in reason
        assert DENIAL_NEW_NAME_SENTENCE not in reason
        assert "fresh directory you created under /tmp" not in reason
        assert "Confirm a claim by reading and tracing the code before running anything" not in reason
        assert stderr == "", f"a successful deny wrote to stderr: {stderr!r}"

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


def _review_pr_findings_write_case(case, session_id, findings_dir, tmp_path):
    """Build one near-miss of the sanctioned /review-pr findings write and return (file_path, payload session id).

    Each case differs from the allowed write (existing directory, matching session id, own persona name, directly
    inside the directory, .md suffix, path not yet existing) in exactly one respect, with two exceptions.
    `invalid-session` differs in two respects: its path sits in `nested`, so with the session-id validator removed
    every other predicate would pass. `directory-path` is overdetermined: the empty name and the existing-path check
    each deny it on their own. A payload session id of None means the payload carries no session_id key."""
    own_name = "staff-sdet-1700000000-branch.md"
    if case == "another-session":
        findings_dir.mkdir()
        return str(findings_dir / own_name), str(uuid.uuid4())
    if case == "no-session":
        findings_dir.mkdir()
        return str(findings_dir / own_name), None
    if case == "empty-session":
        findings_dir.mkdir()
        return str(findings_dir / own_name), ""
    if case == "invalid-session":
        # The payload session id plus "/nested" names the directory the path sits in, so only the session-id
        # validator can deny.
        (findings_dir / "nested").mkdir(parents=True)
        return str(findings_dir / "nested" / own_name), f"{session_id}/nested"
    if case == "doubled-slash-after-directory":
        findings_dir.mkdir()
        return f"{findings_dir}//{own_name}", session_id
    if case == "doubled-slash-before-directory":
        findings_dir.mkdir()
        return f"/tmp//{findings_dir.name}/{own_name}", session_id
    if case == "dot-segment":
        findings_dir.mkdir()
        return f"{findings_dir}/./{own_name}", session_id
    if case == "directory-path":
        findings_dir.mkdir()
        return f"{findings_dir}/", session_id
    if case == "existing-file":
        findings_dir.mkdir()
        existing_file = findings_dir / own_name
        existing_file.write_text("earlier findings")
        return str(existing_file), session_id
    if case == "dangling-symlink":
        findings_dir.mkdir()
        (findings_dir / own_name).symlink_to(tmp_path / "does-not-exist")
        return str(findings_dir / own_name), session_id
    if case == "live-symlink":
        findings_dir.mkdir()
        link_target = tmp_path / "link-target.md"
        link_target.write_text("a file outside the findings directory")
        (findings_dir / own_name).symlink_to(link_target)
        return str(findings_dir / own_name), session_id
    if case == "extended-directory-name":
        # The real directory exists, so only an exact parent-directory comparison (not a prefix match) denies.
        findings_dir.mkdir()
        return f"{findings_dir}-x/{own_name}", session_id
    if case == "another-persona":
        findings_dir.mkdir()
        return str(findings_dir / "ciso-reviewer-1700000000-branch.md"), session_id
    if case == "subdirectory":
        (findings_dir / "nested").mkdir(parents=True)
        return str(findings_dir / "nested" / own_name), session_id
    if case == "missing-directory":
        return str(findings_dir / own_name), session_id
    if case == "symlinked-directory":
        real_directory = tmp_path / "real-findings-directory"
        real_directory.mkdir()
        findings_dir.symlink_to(real_directory)
        return str(findings_dir / own_name), session_id
    if case == "non-md-name":
        findings_dir.mkdir()
        return str(findings_dir / "staff-sdet-1700000000-branch.txt"), session_id
    if case == "own-type-without-dash":
        findings_dir.mkdir()
        return str(findings_dir / "staff-sdet.md"), session_id
    raise AssertionError(f"unknown case {case!r}")


class TestReviewPrFindingsDirectoryDenied:
    """Every near-miss of the sanctioned /tmp findings write must still reach the Write arm's final deny."""

    @pytest.mark.parametrize(
        "case",
        [
            "another-session",
            "no-session",
            "empty-session",
            "invalid-session",
            "another-persona",
            "subdirectory",
            "extended-directory-name",
            "missing-directory",
            "symlinked-directory",
            "non-md-name",
            "own-type-without-dash",
            "doubled-slash-after-directory",
            "doubled-slash-before-directory",
            "dot-segment",
            "directory-path",
        ],
    )
    def test_near_miss_of_the_findings_write_denied(self, reviewer_findings_session, tmp_path, case):
        """Write is the only tool the exemption can ever admit, so the matrix runs for Write alone; Edit and
        MultiEdit deny on the tool name whatever the path and are pinned once below."""
        session_id, findings_dir = reviewer_findings_session
        path, payload_session_id = _review_pr_findings_write_case(case, session_id, findings_dir, tmp_path)
        payload = write_input(path, agent_type="staff-sdet")
        if payload_session_id is not None:
            payload["session_id"] = payload_session_id
        _assert_write_arm_denied(payload)

    @pytest.mark.parametrize("case", ["existing-file", "dangling-symlink", "live-symlink"])
    def test_write_to_an_existing_path_denied(self, reviewer_findings_session, tmp_path, case):
        """The arm is create-only: a Write onto an existing file, a symlink to an existing file or a dangling
        symlink denies, which keeps a planted link from redirecting the write."""
        session_id, findings_dir = reviewer_findings_session
        path, payload_session_id = _review_pr_findings_write_case(case, session_id, findings_dir, tmp_path)
        payload = write_input(path, agent_type="staff-sdet")
        payload["session_id"] = payload_session_id
        _assert_write_arm_denied(payload)

    @pytest.mark.parametrize("tool_name", ["Edit", "MultiEdit"])
    def test_edit_tools_creating_own_findings_file_denied(self, reviewer_findings_session, tool_name):
        """Only Write creates a file: Edit and MultiEdit to the not-yet-existing own file deny, though the same path
        passes for Write."""
        session_id, findings_dir = reviewer_findings_session
        findings_dir.mkdir()
        path = str(findings_dir / "staff-sdet-1700000000-branch.md")
        payload = _FILE_TOOL_BUILDERS[tool_name](path, agent_type="staff-sdet")
        payload["session_id"] = session_id
        _assert_write_arm_denied(payload)


class TestAgentReviewsIgnoreVerification:
    """The own-findings-file exemption is conditional on `git check-ignore`
    confirming the path is actually ignored in the target repo (GH-512) — a
    stale worktree-local info/exclude, or a repo with no ignore entry at all,
    must deny rather than silently let an unignored findings file through."""

    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_not_ignored_path_denied(self, repo_not_ignoring_agent_reviews, tool_name):
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        payload = _FILE_TOOL_BUILDERS[tool_name](path, agent_type="staff-sdet", cwd=cwd)
        assert run_hook(HOOK, payload) == "deny"

    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_not_ignored_deny_reason_directs_to_inline_fallback(self, repo_not_ignoring_agent_reviews, tool_name):
        """The deny message must send the reviewer to its documented inline
        fallback, and must NOT read as an invitation to fix the ignore state
        itself (e.g. a raw `printf ... >> .git/info/exclude`) — exactly the
        unguarded raw-Bash-redirect vector this hook's header documents as a
        known gap. Locks the message shape, not just the decision."""
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        cwd = str(repo_not_ignoring_agent_reviews)
        reason = run_hook_reason(HOOK, _FILE_TOOL_BUILDERS[tool_name](path, agent_type="staff-sdet", cwd=cwd))
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

    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_missing_cwd_denied(self, tool_name):
        """No .cwd in the payload at all must deny, not silently check
        whatever directory the hook process happens to be running in. The
        hook's zero-length check denies it because, on bash 5.2, `cd ''`
        succeeds in place."""
        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        reason = run_hook_reason(HOOK, _FILE_TOOL_BUILDERS[tool_name](path, agent_type="staff-sdet"))
        assert reason is not None
        assert "carried no .cwd" in reason

    def test_tracked_own_findings_file_in_a_nested_directory_denied_as_not_ignored(
        self, repo_ignoring_agent_reviews
    ):
        """git check-ignore reports a tracked path as not ignored whatever the rules say, so a force-added own-name
        file in a nested agent-reviews/ directory still denies."""
        nested = repo_ignoring_agent_reviews / "sub" / "agent-reviews"
        nested.mkdir(parents=True)
        (nested / "staff-sdet-1700000000-branch.md").write_text("x\n")
        subprocess.run(
            ["git", "add", "-f", "sub/agent-reviews/staff-sdet-1700000000-branch.md"],
            cwd=repo_ignoring_agent_reviews,
            check=True,
        )
        payload = write_input(
            "sub/agent-reviews/staff-sdet-1700000000-branch.md",
            agent_type="staff-sdet",
            cwd=str(repo_ignoring_agent_reviews),
        )
        reason = run_hook_reason(HOOK, payload)
        assert reason is not None
        assert "not actually ignored" in reason

    def test_pathspec_mode_variable_in_the_environment_denies_with_exit_128(self, repo_ignoring_agent_reviews):
        """git check-ignore dies on a pathspec-mode variable, so the ignore check cannot confirm and the hook denies
        instead of treating the failure as an ignored path."""
        payload = write_input(
            "agent-reviews/staff-sdet-1700000000-branch.md",
            agent_type="staff-sdet",
            cwd=str(repo_ignoring_agent_reviews),
        )
        reason = run_hook_reason(HOOK, payload, extra_env={"GIT_LITERAL_PATHSPECS": "1"})
        assert reason is not None
        assert "exit 128" in reason

    def test_ignore_rule_in_a_linked_worktrees_shared_info_exclude_allows(self, tmp_path):
        """A linked worktree reads the common directory's info/exclude, the ignore source GH-512 is about."""
        main_repo = init_git_repo_with_commit(tmp_path / "main-repo", file_name="README.md")
        linked_worktree = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "linked", str(linked_worktree)], cwd=main_repo, check=True
        )
        (main_repo / ".git" / "info" / "exclude").write_text("agent-reviews/\n")

        path = "agent-reviews/staff-sdet-1700000000-branch.md"
        payload = write_input(path, agent_type="staff-sdet", cwd=str(linked_worktree))
        assert run_hook(HOOK, payload) == "allow"

    def test_foreign_git_dir_env_does_not_launder_the_check(self, repo_not_ignoring_agent_reviews, tmp_path):
        """A GIT_DIR pointed at a different repo must not make an unrelated
        target repo's write look safe — regression test for the `unset
        GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE` fix (the same statement
        require-worktree-for-git-writes.sh makes).

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
        foreign = init_git_repo_with_commit(tmp_path / "foreign", file_name="README.md")
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
        foreign = init_git_repo_with_commit(
            tmp_path / "foreign-work-tree", file_name=".gitignore", content="agent-reviews/\n"
        )

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
        repo = init_git_repo_with_commit(tmp_path / "repo", file_name=".gitignore", content="/agent-reviews/\n")
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

    def test_reviewer_nested_shell_git_write_denied(self):
        # Quote stripping exposes the git word inside `bash -c`.
        reason = run_hook_reason(
            HOOK, bash_input('bash -c "git checkout -- x"', agent_type="staff-sdet")
        )
        assert reason is not None
        assert "'git checkout' is not a read-only git subcommand" in reason

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
    @pytest.mark.parametrize(
        ("command", "reason_substring"),
        [
            ("sed -i 's/git log/x/' src/x", "rewrites the file in place"),
            ("black tools/git status.py", "'black' reformats files"),
        ],
        ids=["sed-dash-i", "formatter"],
    )
    def test_reviewer_in_place_edit_mentioning_git_subcommand_denied(self, command, reason_substring):
        # A word ending in `/git` routes the fragment to the git arm, which must not end its checks.
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert reason_substring in reason

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
        # Plain `ruff check` is read-only linting and stays allowed.
        assert run_hook(HOOK, bash_input("ruff check src/x.py", agent_type="staff-backend-engineer")) == "allow"

    def test_reviewer_ruff_check_fix_only_denied(self):
        # --fix-only writes to disk exactly like --fix.
        assert run_hook(HOOK, bash_input("ruff check --fix-only src/x.py", agent_type="staff-backend-engineer")) == "deny"

    @pytest.mark.parametrize(
        "add_noqa_flag",
        ["--add-noqa", "--add-noqa=legacy", '--add-noqa="legacy code"'],
    )
    def test_reviewer_ruff_check_add_noqa_denied(self, add_noqa_flag):
        # --add-noqa inserts `# noqa` comments into the linted files in place.
        # The `=REASON` cases use newer ruff syntax that the repo's pinned
        # ruff rejects, and the gate must still deny them.
        command = f"ruff check {add_noqa_flag} src/x.py"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-backend-engineer")) == "deny"

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
        # GH-783: confirms COMMAND_UNQUOTED doesn't affect fragment splitting
        # for a benign quoted argument with no token-boundary interaction.
        # Not an over-strip false-positive guard.
        # The quote strip ignores shell word grouping, so a quoted or escaped space can expose a -i token.
        # For example, `sed s/a/b/ "my -i file.txt"` denies, and that over-deny fails closed.
        # Backslash-newline continuations are covered by TestBackslashNewlineContinuation.
        assert run_hook(HOOK, bash_input('sed s/a/b/ "x.txt"', agent_type="staff-sdet")) == "allow"

    def test_command_unquoted_sed_absent_from_path_denied(self, tmp_path):
        # GH-783: COMMAND_UNQUOTED's sed/tr strip failure must fail closed
        # rather than let a missing sed silently collapse fragment
        # detection and fall through to this hook's normal allow path.
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
                bash_input("'git' checkout -- some/file.txt", agent_type="staff-sdet"),
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )


class TestWorktreeEnsureDeniedByName:
    """worktree-ensure.sh fetches refs and creates a branch and a worktree but
    contains no git word, so the gate denies it by command word. The worktree
    gate's main-tree deny message advertises it, which a review-only agent
    must not follow."""

    HINT_FORM = "~/.claude/scripts/worktree-ensure.sh 'GH-1/x'"

    @pytest.mark.parametrize(
        "command",
        [
            HINT_FORM,
            "/home/user/.claude/scripts/worktree-ensure.sh GH-1/x",
            "claude/.claude/scripts/worktree-ensure.sh GH-1/x",
            "worktree-ensure.sh GH-1/x",
            "cd /tmp/x && ~/.claude/scripts/worktree-ensure.sh GH-1/x",
            "env X=1 ~/.claude/scripts/worktree-ensure.sh GH-1/x",
        ],
        ids=["hint-form", "absolute-path", "in-repo-relative", "bare-name", "after-cd", "env-assignment-prefix"],
    )
    def test_reviewer_worktree_ensure_denied(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_reviewer_worktree_ensure_deny_reason_names_script_and_alternative(self):
        reason = run_hook_reason(HOOK, bash_input(self.HINT_FORM, agent_type="staff-sdet"))
        assert reason is not None
        assert "worktree-ensure.sh" in reason
        assert "does not apply to review-only agents" in reason
        assert DENIAL_NO_RETRY_SENTENCE in reason, "denial reason lost the sanctioned-alternative text"

    @pytest.mark.parametrize("agent_type", [None, "main", "code-writer", "general-purpose"])
    def test_non_review_only_caller_worktree_ensure_allowed(self, agent_type):
        assert run_hook(HOOK, bash_input(self.HINT_FORM, agent_type=agent_type)) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "cat ~/.claude/scripts/worktree-ensure.sh",
            "grep -n x ~/.claude/scripts/worktree-ensure.sh",
            "bash -n ~/.claude/scripts/worktree-ensure.sh",
        ],
        ids=["cat", "grep", "bash-n"],
    )
    def test_reviewer_reading_worktree_ensure_allowed(self, command):
        # Command-word match only: the script name as an argument is a read.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "bash ~/.claude/scripts/worktree-ensure.sh x",
            "timeout 600 ~/.claude/scripts/worktree-ensure.sh x",
        ],
        ids=["bash-wrapper", "timeout-wrapper"],
    )
    def test_reviewer_worktree_ensure_behind_unlisted_wrapper_allowed(self, command):
        """Examples of the accepted class, not a complete list: any command whose command
        word is not the script, such as a wrapper outside the shared runner skip list,
        the same class as the `bash -c` gap in TestKnownGapBypass. Both flip to "deny"
        only if the runner skip list changes."""
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"


class TestRawWriteTargetGap:
    """GH-751: a cp/mv/tee destination, or a `>`/`>>` shell redirect
    target, that is neither literally under /tmp/ nor /dev/null denies.
    Regression tests proving the three examples (`cp scratch src/x`,
    `sed ... > src/x`, `tee src/x`) are caught,
    paired with the /tmp and /dev/null exemptions each must still permit.
    A cp/mv/tee destination denies only
    when that command is the fragment's sole or first command. GH-811 tracks
    the residual gap where the same destination is hidden behind a bare `&`
    in the same fragment — see
    test_reviewer_raw_write_hidden_behind_bare_ampersand_allowed (cp pin) and
    test_reviewer_tee_hidden_behind_bare_ampersand_allowed (tee pin) below. A
    `>`/`>>` target behind a bare `&` still denies — see
    test_reviewer_redirect_hidden_behind_bare_ampersand_denied below."""

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
        # The Bash arm's /tmp scratch allowance: copying one file into a /tmp scratch directory must not deny.
        assert run_hook(HOOK, bash_input("cp src/x /tmp/scratch/x", agent_type="staff-sdet")) == "allow"

    def test_reviewer_redirect_to_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("sed 's/a/b/' src/x > /tmp/scratch/x", agent_type="staff-sdet")) == "allow"

    def test_reviewer_tee_to_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("echo x | tee /tmp/scratch/x", agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "grep -q x f > /dev/null",
            "grep -q x f >/dev/null",
            "echo x >>/tmp/scratch/x",
        ],
        ids=["spaced-redirect-to-dev-null", "attached-redirect-to-dev-null", "attached-append-redirect-to-tmp"],
    )
    def test_plan_redirect_to_exempt_target_allowed(self, command):
        """`Plan` is the roster member that reaches the Bash arm. Every fragment, git-led or not, reaches the
        raw-write-target scan, which exempts /dev/null (diagnostic noise, not a tracked-file write)
        and a /tmp target, including the attached `>>` form."""
        assert run_hook(HOOK, bash_input(command, agent_type="Plan")) == "allow"

    def test_plan_attached_append_redirect_to_tracked_path_denied(self):
        """The attached `>>` form (no space before the target) is judged like the spaced form: a tracked-path target denies."""
        assert run_hook(HOOK, bash_input("echo x >>src/x", agent_type="Plan")) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "git show HEAD:f > src/tracked.py",
            "git log >> src/tracked.py",
            "git log >src/tracked.py",
            "git log >>src/tracked.py",
            'bash -c "git show HEAD:f > src/tracked.py"',
        ],
        ids=["spaced", "append", "attached", "attached-append", "nested-shell"],
    )
    def test_reviewer_git_redirect_to_tracked_path_denied(self, command):
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert "writes to 'src/tracked.py', which is outside /tmp." in reason

    def test_reviewer_git_redirect_to_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("git show HEAD:f > /tmp/scratch/f", agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        ["git log -n 5 2>&1", "git diff 2>/dev/null", "git log > /dev/null 2>&1"],
        ids=["stderr-to-stdout", "stderr-to-dev-null", "spaced-dev-null-with-stderr"],
    )
    def test_reviewer_git_redirect_of_stderr_allowed(self, command):
        # Pins that an fd-numbered redirect (`2>&1`, `2>/dev/null`) and the spaced
        # `> /dev/null 2>&1` stay allowed, so a future fix of that header gap
        # must not start denying them.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "git rev-parse --verify HEAD >/dev/null 2>&1",
            "git diff --quiet >/dev/null",
            "git log -n 1 >/tmp/scratch/f",
        ],
        ids=["attached-dev-null-with-stderr", "attached-dev-null", "attached-tmp"],
    )
    def test_reviewer_git_attached_redirect_to_exempt_target_allowed(self, command):
        # Pins the attached (no-space) `>target` word-walk path for the exempt
        # `/dev/null` and `/tmp/*` targets, which the spaced cases above do not reach.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "git diff >&2",
            "git log > /dev/stderr",
            "git log > /dev/stdout",
            # The extracted target is `&`, so the `/dev/null` exemption never applies.
            "git log >& /dev/null",
            # The extracted target is `&`, so a `/tmp/` path after `>&` is not exempt either.
            "git log >& /tmp/scratch/x",
        ],
        ids=[
            "fd-duplication",
            "dev-stderr",
            "dev-stdout",
            "combined-redirect-dev-null",
            "combined-redirect-tmp",
        ],
    )
    def test_reviewer_git_redirect_over_denied(self, command):
        """An accepted false deny named in the hook header's Known gaps.
        `/dev/stderr` and `/dev/stdout` deny because only `/dev/null` and `/tmp/*` targets are exempt.
        `>&2` and `>& /dev/null` deny because the hook reads `>&` (or `>&2`) as a redirect whose target is `&` (or `&2`).
        The word after `>&` is never read as a target, so the `/dev/null` and `/tmp/*` exemptions never apply.
        The assertion pins the verdict and the shared `, which is outside /tmp.` tail, not the extracted target text."""
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert ", which is outside /tmp." in reason

    @pytest.mark.parametrize(
        "command",
        ["git log >& src/x", "git log >&src/x"],
        ids=["spaced", "attached"],
    )
    def test_reviewer_git_ampersand_redirect_to_tracked_path_denied(self, command):
        # The deny currently comes from the `>&` word yielding a target that starts with `&`, so a
        # fix that skips `>&` as fd duplication must keep a write onto a tracked path denied.
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert ", which is outside /tmp." in reason

    @pytest.mark.parametrize(
        "command",
        [
            "git log --grep 'a > b'",
            'git grep -n "x >= 5" src',
            "grep 'a > b' src/x",
            "cat > /tmp/scratch/check.sh <<'EOF'\nif true\nthen git diff > x\nfi\nEOF",
        ],
        ids=["git-fragment", "git-comparison-operator", "non-git-fragment", "heredoc-body"],
    )
    def test_reviewer_quoted_redirect_character_over_denied(self, command):
        """A `>` that starts a word inside quoted text or a heredoc body reads as a
        redirect. The false deny is accepted, and
        `docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md`
        records why. The assertion pins the verdict, not the extracted target text."""
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert ", which is outside /tmp." in reason

    def test_reviewer_redirect_traversal_out_of_tmp_denied(self):
        assert (
            run_hook(HOOK, bash_input("echo x > /tmp/../etc/passwd", agent_type="staff-sdet"))
            == "deny"
        )

    def test_reviewer_raw_write_to_agent_reviews_denied(self):
        """A raw-Bash `agent-reviews/*` write gets no exemption at all: the
        ignore-state confirmation the Write/Edit/MultiEdit arm's own
        exemption depends on (_lib_path_ignore_status) has no Bash-arm
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
        # unsplit fragment ("/tmp/x", from the `echo` after the `&`) as the
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

    def test_reviewer_tee_hidden_behind_bare_ampersand_allowed(self):
        # GH-811: pins the CURRENT (imperfect) behavior. The unsplit
        # fragment's command word is `echo`, so the `tee` destination read
        # never runs; flip to "deny" when the splitter limitation is fixed.
        assert (
            run_hook(HOOK, bash_input("echo hi & tee src/x", agent_type="staff-sdet"))
            == "allow"
        )

    def test_reviewer_redirect_hidden_behind_bare_ampersand_denied(self):
        # The `>` target scan walks every word of the unsplit fragment, so a
        # redirect after a bare `&` is still read, unlike a cp/mv/tee target.
        reason = run_hook_reason(
            HOOK,
            bash_input("echo hi & echo x > src/y", agent_type="staff-sdet"),
        )
        assert reason is not None
        assert "writes to 'src/y'" in reason


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
    traversal path could satisfy the agent-reviews/* prefix while resolving to
    a tracked repo file. The guard rejects any `..` segment. Only the
    `back-into-agent-reviews` case below isolates the guard: the other
    agent-reviews traversal paths here also fail the parent predicate, and the
    /tmp traversal path matches no exemption arm."""

    def test_tmp_traversal_path_matches_no_exemption_arm_and_denies(self):
        path = "/tmp/../home/user/repo/src/main.py"
        _assert_write_arm_denied(write_input(path, agent_type="staff-sdet"))

    def test_agent_reviews_traversal_path_denied_by_the_parent_predicate(self):
        path = "agent-reviews/../claude/.claude/hooks/deny-reviewer-tree-mutation.sh"
        _assert_write_arm_denied(write_input(path, agent_type="staff-sdet"))

    def test_nested_traversal_path_denied_by_the_parent_predicate(self):
        path = "/repo/agent-reviews/../../src/main.py"
        _assert_write_arm_denied(edit_input(path, agent_type="ciso-reviewer"))

    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    @pytest.mark.parametrize(
        "path",
        [
            "agent-reviews/../build/staff-sdet-x.md",
            "sub/agent-reviews/../build/staff-sdet-x.md",
            # Discriminating case for the traversal guard: git check-ignore exits 0 on this path and its parent and
            # name are both valid once `..` is resolved, so only the guard denies it. The parent rule also covers the
            # two above.
            "agent-reviews/../agent-reviews/staff-sdet-x.md",
        ],
        ids=["into-ignored-sibling", "nested-into-ignored-sibling", "back-into-agent-reviews"],
    )
    def test_traversal_into_an_ignored_directory_denied(self, repo_ignoring_agent_reviews_and_build, tool_name, path):
        payload = _FILE_TOOL_BUILDERS[tool_name](
            path, agent_type="staff-sdet", cwd=str(repo_ignoring_agent_reviews_and_build)
        )
        _assert_write_arm_denied(payload)

    def test_double_dot_in_filename_not_treated_as_traversal(self, repo_ignoring_agent_reviews):
        """`..` inside a filename (a..b), not as a path segment, is not a
        traversal — a legitimate findings write must still be allowed."""
        path = "agent-reviews/staff-sdet-a..b.md"
        cwd = str(repo_ignoring_agent_reviews)
        assert run_hook(HOOK, write_input(path, agent_type="staff-sdet", cwd=cwd)) == "allow"


class TestWriteArmFindingsFileRule:
    """The Write arm allows exactly the caller's own `<agent>-*.md` file directly inside an ignored
    agent-reviews directory. Each deny case below isolates one predicate."""

    @pytest.mark.parametrize(
        "path",
        [
            "agent-reviews/staff-backend-engineer-x.md",
            "agent-reviews/review-ledger-x.md",
            "agent-reviews/staff-sdet-x.index",
            "agent-reviews/staff-sdet.md",
            # The empty name after the trailing slash fails the name predicate.
            "agent-reviews/",
            # Valid own name in a wrong parent: the parent predicate's discriminating cases.
            "agent-reviews/sub/staff-sdet-y.md",
            "agent-reviews/staff-sdet-x/staff-sdet-y.md",
            "agent-reviews//staff-sdet-x.md",
        ],
        ids=[
            "other-agents-findings-file",
            "ledger-file",
            "lane-index-file",
            "name-without-suffix",
            "trailing-slash",
            "own-name-in-subdirectory",
            "own-name-nested-twice",
            "double-slash-parent",
        ],
    )
    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_write_arm_parent_and_name_predicates_deny(self, repo_ignoring_agent_reviews, tool_name, path):
        payload = _FILE_TOOL_BUILDERS[tool_name](
            path, agent_type="staff-sdet", cwd=str(repo_ignoring_agent_reviews)
        )
        _assert_write_arm_denied(payload)

    @pytest.mark.parametrize("path_location", ["literal-tmp", "second-checkout"])
    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_own_name_file_outside_the_cwd_repository_denied_at_the_ignore_check(
        self, repo_ignoring_agent_reviews, tmp_path, tool_name, path_location
    ):
        """The cwd's repository judges the ignore state, so an own-name path outside that repository makes
        git check-ignore exit 128 and the hook denies, even when the path's own repository ignores agent-reviews/."""
        if path_location == "literal-tmp":
            path = "/tmp/agent-reviews/staff-sdet-x.md"
        else:
            other_checkout = init_git_repo_with_commit(
                tmp_path / "other-checkout", file_name=".gitignore", content="agent-reviews/\n"
            )
            (other_checkout / "agent-reviews").mkdir()
            path = str(other_checkout / "agent-reviews" / "staff-sdet-1700000000-branch.md")
        payload = _FILE_TOOL_BUILDERS[tool_name](
            path, agent_type="staff-sdet", cwd=str(repo_ignoring_agent_reviews)
        )
        reason = run_hook_reason(HOOK, payload)
        assert reason is not None
        assert "not a git repo, or the check failed" in reason
        assert "exit 128" in reason

    @pytest.mark.parametrize("path_form", ["relative", "absolute"])
    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_own_findings_file_allowed_for_every_file_tool(self, repo_ignoring_agent_reviews, tool_name, path_form):
        """The production Write tool sends absolute paths, and the own-name rule allows that shape as well as the relative
        one, with no deny reason and nothing on stderr."""
        own_file = "agent-reviews/staff-sdet-1700000000-branch.md"
        path = own_file if path_form == "relative" else str(repo_ignoring_agent_reviews / own_file)
        payload = _FILE_TOOL_BUILDERS[tool_name](
            path,
            agent_type="staff-sdet",
            cwd=str(repo_ignoring_agent_reviews),
        )
        _assert_clean_allow(payload)

    @pytest.mark.parametrize("tool_name", sorted(_FILE_TOOL_BUILDERS))
    def test_other_agents_findings_file_denied_for_every_file_tool_given_an_absolute_path(
        self, repo_ignoring_agent_reviews, tool_name
    ):
        """The production Write tool sends absolute paths, and the own-name rule denies another agent's file in that shape."""
        other_agents_file = repo_ignoring_agent_reviews / "agent-reviews" / "staff-backend-engineer-1700000000-branch.md"
        payload = _FILE_TOOL_BUILDERS[tool_name](
            str(other_agents_file),
            agent_type="staff-sdet",
            cwd=str(repo_ignoring_agent_reviews),
        )
        _assert_write_arm_denied(payload)

    def test_roster_sweep_denies_tmp_writes_and_allows_own_findings_files(self, repo_ignoring_agent_reviews):
        cwd = str(repo_ignoring_agent_reviews)
        roster = review_only_agents()
        assert roster, "the review-only roster read from _lib.sh is empty"
        for agent_type in roster:
            _assert_write_arm_denied(write_input("/tmp/scratch/ok.py", agent_type=agent_type, cwd=cwd))
            own_file = f"agent-reviews/{agent_type}-1700000000-branch.md"
            decision = run_hook(HOOK, write_input(own_file, agent_type=agent_type, cwd=cwd))
            assert decision == "allow", f"{agent_type}: own findings file {own_file} was not allowed ({decision})"

    def test_no_roster_name_followed_by_a_dash_is_a_prefix_of_another_members_name(self):
        """The own-name predicate matches `<agent>-*.md`, so a roster name that begins with another member's name
        and a dash would let the longer-named agent write the shorter-named agent's findings file."""
        roster = review_only_agents()
        clashes = [(a, b) for a in roster for b in roster if a != b and b.startswith(f"{a}-")]
        assert not clashes, f"roster names where one prefixes another: {clashes}"

    def test_write_arm_not_ignored_deny_emits_one_json_document(self, repo_not_ignoring_agent_reviews):
        """run_hook parses the whole stdout as one JSON document; the extracted ignore helper must write nothing."""
        payload = write_input(
            "agent-reviews/staff-sdet-1700000000-branch.md",
            agent_type="staff-sdet",
            cwd=str(repo_not_ignoring_agent_reviews),
        )
        reason, stderr = run_hook_reason_and_stderr(HOOK, payload)
        assert reason is not None
        assert "not actually ignored" in reason
        assert stderr == ""


class TestExemptionPathsAllTools:
    """The own-findings-file exemption lives in one Write|Edit|MultiEdit case
    arm; assert each tool exercises the allow and deny directions so a future
    split of that arm can't silently regress Edit/MultiEdit."""

    def test_reviewer_edit_under_tmp_denied(self):
        _assert_write_arm_denied(edit_input("/tmp/scratch/copy.py", agent_type="staff-sdet"))

    def test_reviewer_multiedit_under_tmp_denied(self):
        _assert_write_arm_denied(multiedit_input("/tmp/scratch/copy.py", agent_type="staff-sdet"))

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

    def test_reviewer_git_grep_case_insensitive_flag_allowed(self):
        assert run_hook(HOOK, bash_input("git grep -i sed", agent_type="staff-sdet")) == "allow"

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

    # GH-1252: the gate strips the venv-run.sh wrapper word, so the tool behind it is the command word.
    def test_reviewer_venv_run_ruff_format_denied(self):
        assert run_hook(HOOK, bash_input("scripts/venv-run.sh ruff format x.py", agent_type="staff-sdet")) == "deny"

    def test_reviewer_sudo_venv_run_ruff_format_denied(self):
        command = "sudo scripts/venv-run.sh ruff format x.py"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    def test_reviewer_venv_run_ruff_check_documented_command_allowed(self):
        command = "scripts/venv-run.sh ruff check claude/.claude/ claude-skills/"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "cp /tmp/x scripts/venv-run.sh",
            "echo x > scripts/venv-run.sh",
            "tee scripts/venv-run.sh",
            ">scripts/venv-run.sh echo x",
            "scripts/venv-run.sh ruff check > scripts/venv-run.sh",
        ],
    )
    def test_reviewer_venv_run_non_command_word_keeps_write_target_denied(self, command):
        # GH-1252: a wrapper word that is not a real command word must not hide a write target.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            # The wrapper is the command word of its own fragment after a `cd` or a pipe.
            "cd /tmp && scripts/venv-run.sh ruff format x.py",
            "echo x | scripts/venv-run.sh ruff format x.py",
            # A mutating flag on a tool the wrapper otherwise allows.
            "scripts/venv-run.sh ruff check --fix x.py",
            # --add-noqa writes `# noqa` comments in place, so the wrapper does not hide it.
            "scripts/venv-run.sh ruff check --add-noqa x.py",
            # The bare wrapper spelling, with no directory prefix.
            "venv-run.sh ruff format x.py",
            # The wrapper-name match is case-insensitive, like _lib_fragment_invokes_tool.
            "scripts/VENV-RUN.SH ruff format x.py",
            # Deny twin of the documented shellcheck pipeline allow case below.
            "scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh ruff format",
        ],
    )
    def test_reviewer_venv_run_wrapped_mutating_tool_denied(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            # The documented shellcheck lint command and the documented select-tests command both stay usable.
            "scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck",
            "scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py",
        ],
    )
    def test_reviewer_venv_run_documented_read_only_command_allowed(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "2>scripts/venv-run.sh ruff format x.py",
            "<scripts/venv-run.sh ruff format x.py",
            "2>/dev/null scripts/venv-run.sh ruff format x.py",
            "2>/dev/null ruff format x.py",
            "FOO=a/venv-run.sh scripts/venv-run.sh ruff format x.py",
        ],
    )
    def test_reviewer_venv_run_known_gate_gap_commands_allowed(self, command):
        # Pins known gate gaps, not safety properties: the gate misses the `ruff format` each command runs.
        # The hook header records each gap's cause.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"


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
    """Pins the accepted known gaps in the hook header's Known gaps list and the
    deny boundaries beside them, so a future fix flips the matching case
    visibly instead of silently."""

    def test_reviewer_quoted_command_name_bypass_allowed(self):
        # `bash -c "sed -i ..."`: the Bash arm strips the quote characters, leaving a bare `sed`.
        # The fragment's command word resolves to `bash`, which is not a runner word, so the `sed -i` check never fires.
        # This is the nested-shell gap the header accepts under the cooperative model.
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

    # These pin the current allow verdicts for git write shapes the hook does not inspect.
    # The header's Known gaps list names them.
    # `_fragment_raw_write_targets`'s docstring names the fd-numbered and `&>` redirects.
    @pytest.mark.parametrize(
        "command",
        [
            "git show HEAD:f>src/x",
            "git show HEAD:f >| src/x",
            "git show HEAD:f > $(printf src/x)",
            "git show HEAD:f > `printf src/x`",
            "git diff --output=src/x",
            "git diff-tree --output=src/x HEAD",
            "git show HEAD:f 2>src/x",
            "git show HEAD:f &>src/x",
        ],
        ids=[
            "glued-redirect",
            "clobber-redirect",
            "substitution-target",
            "backtick-target",
            "output-flag",
            "output-flag-diff-tree",
            "fd-numbered-redirect",
            "ampersand-redirect",
        ],
    )
    def test_reviewer_git_write_shape_not_inspected_allowed(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    # Pins the header's waived entry for a command named by a flag of an allowed git subcommand.
    # Values are single words on purpose: a multi-word `-c` value is split by the quote strip and takes a different code path.
    @pytest.mark.parametrize(
        "command",
        [
            "git grep -Ocmd x",
            "git ls-remote --upload-pack=cmd origin",
            "git -c core.fsmonitor=cmd status",
        ],
        ids=[
            "grep-open-files-flag",
            "ls-remote-upload-pack-flag",
            "config-fsmonitor-setting",
        ],
    )
    def test_reviewer_git_flag_borne_command_allowed(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    # GH-811: the first two tests below pin the CURRENT (imperfect) allow
    # verdicts for the bare-`&` entry in the header's known-gaps list.
    # `_lib_split_fragments` does not split on a bare `&`. A fix to GH-811's
    # underlying limitation should make each allow assertion start failing;
    # update it to "deny" then, not silently accept it. The third test pins
    # the deny that holds when no earlier git word precedes the write.
    def test_reviewer_git_write_after_allowed_git_word_and_bare_ampersand_allowed(self):
        # The first git word is `log`, so the subcommand check reads `log`
        # and never sees the `git checkout` after the `&`.
        assert (
            run_hook(
                HOOK,
                bash_input("git log & git checkout -- x", agent_type="staff-sdet"),
            )
            == "allow"
        )

    def test_reviewer_command_word_arm_after_bare_ampersand_allowed(self):
        # The fragment's command word is `echo`, so the `sed -i` after the
        # `&` never reaches the in-place-edit check.
        assert (
            run_hook(
                HOOK,
                bash_input("echo hi & sed -i s/a/b/ x", agent_type="staff-sdet"),
            )
            == "allow"
        )

    def test_reviewer_git_write_after_non_git_prefix_and_bare_ampersand_denied(self):
        # Counterpart of
        # test_reviewer_git_write_after_allowed_git_word_and_bare_ampersand_allowed:
        # with no earlier git word, the first git word is the write itself, so
        # the subcommand check denies.
        reason = run_hook_reason(
            HOOK,
            bash_input("sleep 1 & git checkout -- x", agent_type="staff-sdet"),
        )
        assert reason is not None
        assert "is not a read-only git subcommand" in reason

    # The cases below pin the CURRENT (imperfect) allow verdicts for the
    # reserved-word entry in the header's known-gaps list: a command right after
    # a reserved word or brace on the same line is not split from it, so the
    # command-word arms never see it. Each should flip to "deny" when that
    # limitation is fixed.
    @pytest.mark.parametrize(
        "command",
        [
            'for f in a b; do sed -i s/x/y/ "$f"; done',
            "if true; then sed -i s/x/y/ f; fi",
            "if false; then :; else sed -i s/x/y/ f; fi",
            "{ sed -i s/x/y/ f; }",
            "if true; then cp scratch src/x; fi",
            "if true; then tee src/x; fi",
        ],
        ids=[
            "loop-body",
            "then-body",
            "else-body",
            "brace-body",
            "then-body-cp-destination",
            "then-body-tee-destination",
        ],
    )
    def test_reviewer_command_word_arm_in_keyword_body_allowed(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    def test_reviewer_redirect_in_keyword_body_denied(self):
        # Counterpart of test_reviewer_command_word_arm_in_keyword_body_allowed:
        # the redirect scan walks every word of the fragment, so it still denies.
        reason = run_hook_reason(HOOK, bash_input("for f in a; do echo x > src/x; done", agent_type="staff-sdet"))
        assert reason is not None
        assert "writes to 'src/x'" in reason

    def test_reviewer_git_write_in_keyword_body_denied(self):
        # Counterpart of test_reviewer_command_word_arm_in_keyword_body_allowed:
        # the git scan reads any word of the fragment, so a keyword does not hide a git write.
        reason = run_hook_reason(HOOK, bash_input("for f in a; do git checkout -- x; done", agent_type="staff-sdet"))
        assert reason is not None
        assert "is not a read-only git subcommand" in reason

    @pytest.mark.parametrize(
        "command",
        [
            # `time` is a runner word, so the command word resolves to `sed`.
            "time sed -i s/a/b/ x",
            # Only the first same-line command after the keyword evades the command-word arms.
            "do echo $f; sed -i s/a/b/ x",
            # A body command on its own line is a separate fragment, not a same-line keyword body.
            "for f in a; do\nsed -i s/a/b/ x\ndone",
        ],
        ids=["runner-word-before-command", "second-command-after-keyword", "body-command-on-own-line"],
    )
    def test_reviewer_command_word_arm_outside_keyword_body_denied(self, command):
        # Boundaries of test_reviewer_command_word_arm_in_keyword_body_allowed, per the header's reserved-word entry.
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert "rewrites the file in place" in reason

    @pytest.mark.parametrize("command", ["echo x | TEE src/x", "echo x | Tee src/x"], ids=["upper-case", "title-case"])
    def test_reviewer_non_lowercase_tee_to_tracked_path_allowed(self, command):
        # The tee destination walk matches the lowercase word only. Should
        # flip to "deny" when the walk becomes case-insensitive.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize("command", ["CP scratch src/x", "MV scratch src/x"], ids=["upper-case-cp", "upper-case-mv"])
    def test_reviewer_non_lowercase_cp_mv_to_tracked_path_denied(self, command):
        # Counterpart of test_reviewer_non_lowercase_tee_to_tracked_path_allowed:
        # the command-word gate ignores case, but the tee destination walk does not.
        reason = run_hook_reason(HOOK, bash_input(command, agent_type="staff-sdet"))
        assert reason is not None
        assert "writes to 'src/x'" in reason

    # GH-1295: the three tests below pin the CURRENT (imperfect) allow verdicts
    # for the /tmp link gap in the header's known-gaps list, for `Plan`, the
    # only roster member that reaches the Bash arm. That `Plan` holds Bash is a
    # harness premise this repo cannot pin: a consumer project's agent file
    # with a roster persona's name can give that persona Bash again. The hook
    # matches `/tmp/*` as literal text and never resolves links, so each form
    # launders a write onto a file outside /tmp. No link exists on disk in these pins, so only a
    # Bash arm that inspects `ln` flips the two `ln` assertions. The `cat > /tmp/d/file` assertion flips
    # on neither kind of fix, and a fix that resolves links needs its own real-link test.
    def test_plan_hard_link_into_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("ln src/x /tmp/y", agent_type="Plan")) == "allow"

    def test_plan_file_symlink_into_tmp_allowed(self):
        assert run_hook(HOOK, bash_input("ln -s src/x /tmp/y", agent_type="Plan")) == "allow"

    def test_plan_write_through_directory_symlink_allowed(self):
        assert run_hook(HOOK, bash_input("ln -s src /tmp/d", agent_type="Plan")) == "allow"
        assert run_hook(HOOK, bash_input("cat > /tmp/d/file", agent_type="Plan")) == "allow"

    def test_persona_write_to_tmp_path_denied_by_path_shape(self):
        """A persona's Write tool reaches the Write arm, which exempts no /tmp path except a new own-persona file in the
        session's /review-pr findings directory, which `/tmp/d/file` is not. Pins path shape only: no link is created."""
        _assert_write_arm_denied(write_input("/tmp/d/file", agent_type="ciso-reviewer"))

    # Pins the deny boundary for `Plan`, the roster member that reaches the Bash arm. This `>` redirect form is not
    # the `git diff --output=src/x` flag form that `test_reviewer_git_write_shape_not_inspected_allowed`'s
    # `output-flag` case pins as allowed.
    def test_plan_git_diff_redirect_to_tracked_path_denied(self):
        reason = run_hook_reason(HOOK, bash_input("git diff > src/x", agent_type="Plan"))
        assert reason is not None
        assert "writes to 'src/x', which is outside /tmp." in reason

    def test_reviewer_git_status_redirect_to_dev_null_allowed(self):
        assert run_hook(HOOK, bash_input("git status > /dev/null", agent_type="staff-sdet")) == "allow"


class TestBashArmTmpScratchAllowance:
    """The Bash arm's /tmp scratch allowance: a mktemp directory under /tmp,
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
    def test_scratch_allowance_command_allowed(self, command):
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "echo x > $SCRATCH/out.txt",
            "cd /tmp/staff-sdet.abc123 && echo x > out.txt",
        ],
        ids=["variable-target", "relative-target-after-cd"],
    )
    def test_unliteral_write_target_denied_as_outside_tmp(self, command):
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


class TestBackslashNewlineContinuation:
    """A tool word, write flag, or write target split across a backslash-newline continuation is still caught."""

    @pytest.mark.parametrize(
        "command",
        [
            "ruff check \\\n  --add-noqa src/x.py",
            "ruff check \\\n  --fix src/x.py",
            "sed \\\n-i s/a/b/ src/x.py",
            "eslint \\\n--fix src/x.js",
            "scripts/venv-run.sh ruff check \\\n  --fix x.py",
            # The join deletes each pair instead of replacing it with a space, so a tool word split mid-word still matches.
            "ru\\\nff check --fix x.py",
            # Every continuation in the command is joined, not only the first.
            "ruff check \\\n  --select E \\\n  --fix x.py",
        ],
    )
    def test_reviewer_write_flag_after_backslash_newline_denied(self, command):
        # A tool word or write flag split across a continuation still runs as
        # one command, because the shell deletes the backslash-newline pair.
        assert run_hook(HOOK, bash_input(command, agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_read_only_ruff_after_backslash_newline_allowed(self):
        command = "ruff check \\\n  src/x.py"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-backend-engineer")) == "allow"

    def test_reviewer_plain_newline_between_read_only_commands_allowed(self):
        # A plain newline separates two commands, so the join must leave it in place
        # rather than fuse them into the non-read-only subcommand git statusgit.
        command = "git status\ngit diff"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-backend-engineer")) == "allow"

    def test_reviewer_escaped_backslash_line_end_does_not_hide_next_line_denied(self):
        # The quote strip turns the escaped `\\` at the line end into a lone `\`.
        # Joining alone would fuse `x` onto `ruff`, so only the unjoined copy
        # still sees the `ruff check --fix` line.
        command = "echo x\\\\\nruff check --fix x.py"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-backend-engineer")) == "deny"

    def test_reviewer_write_target_traversal_after_backslash_newline_denied(self):
        # The unjoined first line's target `/tmp/\` passes the /tmp exemption,
        # so only the joined copy exposes the traversal.
        command = "echo x > /tmp/\\\n../etc/passwd"
        assert run_hook(HOOK, bash_input(command, agent_type="staff-sdet")) == "deny"


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
        # code-writer + Write to a source path would normally allow (non-reviewer, fast-path exit).
        payload = write_input("/repo/src/main.py", agent_type="code-writer")
        reason = _run_hook_with_jq_failing_on(payload, "file_path", tmp_path)
        assert reason is not None
        assert "could not parse tool-input JSON" in reason


class TestFullRoster:
    def test_every_review_only_agent_denies_a_mutation(self):
        """Every member of _LIB_REVIEW_ONLY_AGENTS must deny a representative
        mutation — catches an untested member and future array drift."""
        roster = review_only_agents()
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
        gate_roster = set(review_only_agents())
        file_backed_reviewers = {name.removesuffix(".md") for name in CANARY_AGENTS}
        missing = file_backed_reviewers - gate_roster
        assert not missing, (
            f"file-backed reviewer(s) {sorted(missing)} are registered in "
            f"CANARY_AGENTS but missing from _LIB_REVIEW_ONLY_AGENTS — they "
            f"would be un-gated and able to mutate the tree under review"
        )
