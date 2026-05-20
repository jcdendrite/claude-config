"""Tests for require-skill-review.sh."""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    bash_input,
    edit_input,
    extract_skill_command,
    run_hook,
    run_skill_command,
    skill_review_marker_path,
    write_skill_review_marker,
)

_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"
SKILL_REVIEW_HOOK = _PLUGINS_DIR / "skill-review" / "hooks" / "require-skill-review.sh"
SKILL_REVIEW_SKILL = _PLUGINS_DIR / "skill-review" / "skills" / "skill-review" / "SKILL.md"
_PLUGIN_LIB = _PLUGINS_DIR / "skill-review" / "hooks" / "_lib.sh"
_STOWED_LIB = HOOKS_DIR / "_lib.sh"


def _stage_skill_change(git_repo):
    """Stage a SKILL.md change so the hook has a non-empty skill diff to check."""
    skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("## test skill\n")
    subprocess.run(
        ["git", "add", str(skill_file.relative_to(git_repo))],
        cwd=git_repo,
        check=True,
    )


def _stage_plugin_skill_change(git_repo):
    """Stage a SKILL.md change inside a plugin directory (plugins/*/skills/**/SKILL.md)."""
    skill_file = git_repo / "plugins" / "skill-review" / "skills" / "skill-review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("## test plugin skill\n")
    subprocess.run(
        ["git", "add", str(skill_file.relative_to(git_repo))],
        cwd=git_repo,
        check=True,
    )


def _stage_stowed_agent_change(git_repo):
    """Stage an agent change at claude/.claude/agents/<name>.md."""
    agent_file = git_repo / "claude" / ".claude" / "agents" / "test-agent.md"
    agent_file.parent.mkdir(parents=True, exist_ok=True)
    agent_file.write_text("---\nname: test-agent\ndescription: x\n---\n\nbody\n")
    subprocess.run(
        ["git", "add", str(agent_file.relative_to(git_repo))],
        cwd=git_repo,
        check=True,
    )


def _stage_plugin_agent_change(git_repo):
    """Stage an agent change at plugins/<name>/agents/<name>.md."""
    agent_file = git_repo / "plugins" / "some-plugin" / "agents" / "test-agent.md"
    agent_file.parent.mkdir(parents=True, exist_ok=True)
    agent_file.write_text("---\nname: test-agent\ndescription: x\n---\n\nbody\n")
    subprocess.run(
        ["git", "add", str(agent_file.relative_to(git_repo))],
        cwd=git_repo,
        check=True,
    )


class TestRequireSkillReview:
    # The marker layout is ~/.claude/skill-review-markers/<repo-hash>.<session_id>.
    # The hook reads session_id from its JSON payload and checks the
    # matching session's marker. Tests below thread session_id through
    # `bash_input` and `write_skill_review_marker` for paths that exercise
    # the marker check. Tests that exit early (non-bash tool, non-commit command,
    # outside-repo, no SKILL.md staged, empty staged diff) don't need
    # session_id — the hook returns before reaching the marker logic.

    def test_no_marker_denies_commit(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_wrong_hash_marker_denies(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        marker = skill_review_marker_path(isolated_home, git_repo)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("0" * 64 + "\n")
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_correct_hash_marker_allows(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_allowed_when_marker_current(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "git add file.txt && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_restaging_invalidates_marker(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        # Modify and re-stage the SKILL.md to change the skill diff hash
        skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
        skill_file.write_text("## test skill\n## new content\n")
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_chained_add_commit_denied_when_marker_stale(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        # Change the skill content so the marker hash is stale
        skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
        skill_file.write_text("## test skill\n## new content\n")
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "git add file.txt && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_refreshed_marker_allows(self, isolated_home, git_repo):
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
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
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo, session_id="session-A")
        # Session B's commit, same staged diff, but B has no marker.
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
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
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(SKILL_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "deny"
        )

    def test_skill_marker_write_command_matches_hook_path(self, isolated_home, git_repo):
        """Regression guard against the SKILL command and HOOK getting out
        of sync on path derivation.

        Reads the marker-write recipe directly from skill-review SKILL.md
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

        markers_dir = isolated_home / ".claude" / "skill-review-markers"
        if markers_dir.exists():
            for f in markers_dir.glob("*"):
                f.unlink()

        _stage_skill_change(git_repo)
        skill_command = extract_skill_command(SKILL_REVIEW_SKILL, "skill-review-marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)
        # Sanity check: the recipe wrote a marker at the path the hook checks.
        assert skill_review_marker_path(isolated_home, git_repo, session_id=sid).exists(), (
            "SKILL.md marker-write recipe ran but no marker landed at the "
            "path the hook computes — the skill and hook disagree on layout."
        )
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
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
                SKILL_REVIEW_HOOK,
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
        assert run_hook(SKILL_REVIEW_HOOK, bash_input(command), cwd=git_repo) == "allow"

    def test_non_bash_tool_allowed(self, isolated_home, git_repo):
        assert run_hook(SKILL_REVIEW_HOOK, edit_input("/tmp/foo.txt"), cwd=git_repo) == "allow"

    def test_outside_git_repo_allowed(self, isolated_home, tmp_path):
        """Hook should bail rather than false-deny when git can't resolve a repo."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(SKILL_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    def test_no_skill_in_staged_diff_allows(self, git_repo, isolated_home):
        """Commits that don't touch any SKILL.md are never gated."""
        # Stage a non-SKILL.md file only (git_repo already has file.txt staged)
        # No marker — but should still allow because no SKILL.md is staged
        result = run_hook(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m test", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert result == "allow"

    def test_marker_survives_non_skill_restaging(self, git_repo, isolated_home):
        """Re-staging a non-SKILL.md file does not invalidate the skill-review marker."""
        # Stage a SKILL.md change
        _stage_skill_change(git_repo)
        # Write a valid marker for the current SKILL.md-only diff
        write_skill_review_marker(isolated_home, git_repo)
        # Now stage an additional non-SKILL.md file
        settings = git_repo / "claude" / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"additional": true}')
        subprocess.run(["git", "add", "claude/.claude/settings.json"], cwd=git_repo, check=True)
        # Marker should still be valid (path-scoped hash unchanged)
        result = run_hook(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m test", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert result == "allow"

    def test_plugin_skill_no_marker_denies_commit(self, isolated_home, git_repo):
        """Plugin-path SKILL.md (plugins/*/skills/**/SKILL.md) is gated like stowed skills."""
        _stage_plugin_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_plugin_skill_correct_hash_marker_allows(self, isolated_home, git_repo):
        """Plugin-path SKILL.md allows when the marker covers the plugin diff hash."""
        _stage_plugin_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_mixed_stowed_and_plugin_skill_stale_stowed_only_marker_denies(
        self, isolated_home, git_repo
    ):
        """A marker written for a stowed-only diff is stale when a plugin SKILL.md is later
        staged — the combined hash differs from the stowed-only hash, so the gate must deny."""
        _stage_skill_change(git_repo)
        # Write marker that covers only the stowed SKILL.md diff.
        write_skill_review_marker(isolated_home, git_repo)
        # Stage an additional plugin SKILL.md; combined hash now differs from stored marker.
        _stage_plugin_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_stowed_agent_no_marker_denies_commit(self, isolated_home, git_repo):
        """Stowed agent file (claude/.claude/agents/*.md) is gated like SKILL.md."""
        _stage_stowed_agent_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_stowed_agent_correct_hash_marker_allows(self, isolated_home, git_repo):
        """Stowed agent file allows when the marker covers the agent diff hash."""
        _stage_stowed_agent_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_plugin_agent_no_marker_denies_commit(self, isolated_home, git_repo):
        """Plugin-path agent file (plugins/*/agents/*.md) is gated like SKILL.md."""
        _stage_plugin_agent_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_plugin_agent_correct_hash_marker_allows(self, isolated_home, git_repo):
        """Plugin-path agent file allows when the marker covers the agent diff hash."""
        _stage_plugin_agent_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_mixed_skill_and_agent_stale_skill_only_marker_denies(
        self, isolated_home, git_repo
    ):
        """A marker written for a skill-only diff is stale when an agent file is later
        staged — the combined hash differs from the skill-only hash, so the gate must deny."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        # Stage an additional agent file; combined hash now differs from stored marker.
        _stage_stowed_agent_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_agent_marker_survives_non_agent_restaging(self, git_repo, isolated_home):
        """Re-staging a non-reviewed file does not invalidate an agent-file marker."""
        _stage_stowed_agent_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        # Stage an unrelated file outside the gated pathspecs.
        unrelated = git_repo / "claude" / ".claude" / "settings.json"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text('{"additional": true}')
        subprocess.run(["git", "add", "claude/.claude/settings.json"], cwd=git_repo, check=True)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m test", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_plugin_lib_sh_matches_stowed_lib_sh(self):
        """_lib.sh in the plugin hooks dir must be byte-identical to the stowed copy.

        Both the plugin hook (require-skill-review.sh) and marker.sh source different
        copies of _lib.sh. If they diverge, _marker_lib_repo_hash may produce different
        hashes on the read side vs the write side, permanently breaking the gate.
        """
        assert _PLUGIN_LIB.read_bytes() == _STOWED_LIB.read_bytes(), (
            "plugins/skill-review/hooks/_lib.sh has drifted from claude/.claude/hooks/_lib.sh. "
            "These files must stay byte-identical — update the plugin copy when the stowed copy changes."
        )
