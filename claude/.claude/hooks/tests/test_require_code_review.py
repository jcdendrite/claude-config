"""Tests for require-code-review.sh."""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    SKILLS_DIR,
    bash_input,
    edit_input,
    extract_skill_command,
    marker_path,
    run_hook,
    run_hook_reason,
    run_skill_command,
    staged_diff_hash,
    write_marker,
)

CODE_REVIEW_HOOK = HOOKS_DIR / "require-code-review.sh"
CODE_REVIEW_SKILL = SKILLS_DIR / "code-review" / "SKILL.md"


class TestRequireCodeReview:
    # The marker layout is ~/.claude/review-markers/<repo-hash>.<session_id>.
    # The hook reads session_id from its JSON payload and checks the
    # matching session's marker. Tests below thread session_id through
    # `bash_input` and `write_marker` for paths that exercise the marker
    # check. Tests that exit early (non-bash tool, non-commit command,
    # outside-repo, empty staged diff) don't need session_id — the hook
    # returns before reaching the marker logic.

    def test_no_marker_denies_commit(self, isolated_home, git_repo):
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_wrong_hash_marker_denies(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, "0" * 64)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_correct_hash_marker_allows(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_allowed_when_marker_current(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "git add file.txt && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_restaging_invalidates_marker(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_chained_add_commit_denied_when_marker_stale(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "git add file.txt && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_refreshed_marker_allows(self, isolated_home, git_repo):
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_other_sessions_marker_does_not_authorize_commit(self, isolated_home, git_repo):
        """Regression: session A's marker must NOT authorize session B's
        commit, even when B's staged diff has the same hash as A's reviewed
        diff. The gate's bypass requires THIS session's marker — review is
        per-session, not per-diff.

        This is the load-bearing safety property of session-keyed markers.
        Without it, a future refactor that "simplifies" by accepting any
        matching hash would silently re-introduce cross-session leakage,
        the failure mode that motivated this design."""
        diff_hash = staged_diff_hash(git_repo)
        write_marker(isolated_home, git_repo, diff_hash, session_id="session-A")
        # Session B's commit, same staged diff, but B has no marker.
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="session-B"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_no_session_id_in_input_denies(self, isolated_home, git_repo):
        """Without session_id in the hook payload, no per-session marker can
        be keyed — deny even if a marker file happens to exist on disk.
        Older Claude Code versions or payload-schema drift land here; the
        gate fail-closes rather than silently bypassing."""
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "deny"
        )

    def test_skill_marker_write_command_matches_hook_path(self, isolated_home, git_repo):
        """Regression guard against the SKILL command and HOOK getting out
        of sync on path derivation.

        Reads the marker-write recipe directly from code-review SKILL.md
        via the HOOK_TEST_FIXTURE marker, executes it, and verifies the
        hook accepts the result. SKILL.md is the source of truth — if
        the recipe drifts from what the hook expects, this test fails.
        """
        sid = "test-session-skill-cmd"
        # Set up the session_id lookup file at the path the skill reads.
        # The skill computes its filename from $PPID inside the bash
        # subshell; subprocess.run spawns bash as a child of this pytest
        # process, so $PPID resolves to os.getpid().
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        markers_dir = isolated_home / ".claude" / "review-markers"
        if markers_dir.exists():
            for f in markers_dir.glob("*"):
                f.unlink()

        skill_command = extract_skill_command(CODE_REVIEW_SKILL, "marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)
        # Sanity check: the recipe wrote a marker at the path the hook checks.
        assert marker_path(isolated_home, git_repo, session_id=sid).exists(), (
            "SKILL.md marker-write recipe ran but no marker landed at the "
            "path the hook computes — the skill and hook disagree on layout."
        )
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=sid),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_empty_staged_diff_allows(self, isolated_home, git_repo):
        """Amend-message, --allow-empty, or nothing-to-commit has no new content."""
        subprocess.run(["git", "commit", "-q", "-m", "tmp"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit --amend -m new-message"),
                cwd=git_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git commit-tree abc123",
        ],
    )
    def test_non_commit_git_commands_allowed(self, isolated_home, git_repo, command):
        assert run_hook(CODE_REVIEW_HOOK, bash_input(command), cwd=git_repo) == "allow"

    def test_non_bash_tool_allowed(self, isolated_home, git_repo):
        assert run_hook(CODE_REVIEW_HOOK, edit_input("/tmp/foo.txt"), cwd=git_repo) == "allow"

    def test_outside_git_repo_allowed(self, isolated_home, tmp_path):
        """Hook should bail rather than false-deny when git can't resolve a repo."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    # The marker-chain note is appended to the deny reason ONLY when
    # the command shows a redirect into ~/.claude/review-markers/...
    # chained before `git commit`. This is the precise failure mode
    # where the agent chains marker-seed && git commit in one Bash
    # call: PreToolUse hooks fire before the chained subshell runs,
    # so the hook reads the marker file from disk before the chained
    # marker-write executes. Three positive cases (each chain
    # operator), two negative cases (plain commit, and commit message
    # mentioning `review-markers` literally), and one realistic case
    # using the canonical marker-recipe shape from SKILL.md.

    def test_chained_marker_amp_commit_appends_note(self, isolated_home, git_repo):
        cmd = "echo abc > ~/.claude/review-markers/foo.bar && git commit -m work"
        reason = run_hook_reason(CODE_REVIEW_HOOK, bash_input(cmd), cwd=git_repo)
        assert reason is not None
        assert "PreToolUse hooks evaluate" in reason
        assert "Submit the marker-seed as its own Bash call" in reason

    def test_chained_marker_semicolon_commit_appends_note(self, isolated_home, git_repo):
        cmd = "echo abc > ~/.claude/review-markers/foo.bar ; git commit -m work"
        reason = run_hook_reason(CODE_REVIEW_HOOK, bash_input(cmd), cwd=git_repo)
        assert reason is not None
        assert "PreToolUse hooks evaluate" in reason

    def test_chained_marker_or_commit_appends_note(self, isolated_home, git_repo):
        """`||` chain (run-if-fail) is unusual but parses the same way —
        note still appended so the agent gets the hint."""
        cmd = "echo abc > ~/.claude/review-markers/foo.bar || git commit -m work"
        reason = run_hook_reason(CODE_REVIEW_HOOK, bash_input(cmd), cwd=git_repo)
        assert reason is not None
        assert "PreToolUse hooks evaluate" in reason

    def test_plain_commit_no_marker_chain_note(self, isolated_home, git_repo):
        """No chained redirect → note not appended; deny stays compact."""
        reason = run_hook_reason(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
        assert reason is not None
        assert "PreToolUse hooks evaluate" not in reason
        assert "Submit the marker-seed" not in reason

    def test_review_markers_in_commit_message_no_note(self, isolated_home, git_repo):
        """Commit message mentioning `review-markers` literally must NOT
        trigger the note — pattern requires `>` redirect *before* git commit,
        which this command doesn't have. Critical false-positive guard."""
        cmd = 'git commit -m "fix review-markers leak"'
        reason = run_hook_reason(CODE_REVIEW_HOOK, bash_input(cmd), cwd=git_repo)
        assert reason is not None
        assert "PreToolUse hooks evaluate" not in reason

    def test_canonical_marker_recipe_chained_with_commit_appends_note(self, isolated_home, git_repo):
        """Realistic positive case: the actual canonical marker-seed recipe
        from SKILL.md (multi-step pipeline ending in a redirect to a
        review-markers path) chained to `git commit`. This mirrors the
        exact shape an agent would produce by following the documented
        recipe and then chaining their commit, which is the failure
        mode the gate exists to teach against."""
        cmd = (
            'SESSION_ID=abc && mkdir -p ~/.claude/review-markers && '
            'REPO_HASH=$(git rev-parse --show-toplevel | tr -d "\\n" | sha256sum | awk \'{print $1}\') && '
            'git diff --cached | sha256sum | awk \'{print $1}\' '
            '> ~/.claude/review-markers/$REPO_HASH.$SESSION_ID && '
            'git commit -m "work"'
        )
        reason = run_hook_reason(CODE_REVIEW_HOOK, bash_input(cmd), cwd=git_repo)
        assert reason is not None
        assert "PreToolUse hooks evaluate" in reason
