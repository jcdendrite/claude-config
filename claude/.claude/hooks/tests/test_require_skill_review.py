"""Tests for require-skill-review.sh."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from conftest import _seed_session
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    bash_input,
    edit_input,
    extract_skill_command,
    run_hook,
    run_hook_reason,
    run_skill_command,
    skill_review_marker_path,
    write_skill_review_marker,
)

_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"
SKILL_REVIEW_HOOK = _PLUGINS_DIR / "skill-management" / "hooks" / "require-skill-review.sh"
SKILL_REVIEW_SKILL = _PLUGINS_DIR / "skill-management" / "skills" / "skill-review" / "SKILL.md"
_PLUGIN_LIB = _PLUGINS_DIR / "skill-management" / "hooks" / "_lib.sh"
_STOWED_LIB = HOOKS_DIR / "_lib.sh"


def _stage_skill_change(git_repo, body: str = ""):
    """Stage a SKILL.md change so the hook has a non-empty skill diff to check.

    `body` appends extra content, so a second call stages a diff with a
    different hash than the first.
    """
    skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("## test skill\n" + body)
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


def _stage_routing_md_change(git_repo, body: str = ""):
    """Stage a plan-review/ROUTING.md change so the hook has a non-empty
    ROUTING.md diff to check. `body` appends extra content, so a second call
    stages a diff with a different hash than the first."""
    routing_file = git_repo / "claude" / ".claude" / "skills" / "plan-review" / "ROUTING.md"
    routing_file.parent.mkdir(parents=True, exist_ok=True)
    routing_file.write_text("## test routing\n" + body)
    subprocess.run(
        ["git", "add", str(routing_file.relative_to(git_repo))],
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

    def test_other_sessions_marker_authorizes_identical_staged_diff(self, isolated_home, git_repo):
        """Session A's marker authorizes session B's commit of the identical diff.

        The marker's stored hash proves a review covered exactly this staged
        skill diff; the filename's session suffix only keeps parallel sessions
        from overwriting each other's markers. Keying the read on it denies a
        resumed session (new session_id) a review it already completed."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo, session_id="session-A")
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="session-B"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_other_sessions_marker_does_not_authorize_a_changed_diff(self, isolated_home, git_repo):
        """The negative half: acceptance is by diff hash, not by marker existence."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo, session_id="session-A")
        # Stage a further skill change the recorded hash cannot describe.
        _stage_skill_change(git_repo, body="\n\nAn unreviewed addition.\n")
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="session-B"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_no_session_id_in_input_reads_marker(self, isolated_home, git_repo):
        """A payload with no session_id still finds a marker covering this diff.

        This gate reads no session-scoped state at all, so a payload that
        cannot be session-keyed is not thereby unreviewed."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(SKILL_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "allow"
        )

    def test_no_session_id_and_no_matching_marker_denies(self, isolated_home, git_repo):
        """Fail-closed still holds: no session_id and no covering review → deny."""
        _stage_skill_change(git_repo)
        assert (
            run_hook(SKILL_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "deny"
        )

    def test_routing_md_only_no_marker_denies_with_marker_gate_reason(
        self, isolated_home, git_repo
    ):
        """A ROUTING.md-only staged change with no marker must deny via the
        skill-review marker gate, not the structural validator. A bare
        `== "deny"` assertion can't distinguish "correctly gated, no marker
        yet" from "incorrectly fed to the frontmatter validator" — the latter
        would make ROUTING.md (which has no frontmatter) permanently
        uncommittable, proving STAGED_SKILL_PATHS was wrongly widened."""
        _stage_routing_md_change(git_repo)
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert reason is not None, "hook allowed silently; expected deny"
        assert "Commit blocked by skill-review gate" in reason
        assert "structural validator" not in reason

    def test_marker_under_another_repo_hash_does_not_authorize(
        self, isolated_home, git_repo, tmp_path
    ):
        """The repo-hash prefix stays part of the read predicate.

        Only the session suffix is globbed. All four gates share this read
        shape, so this invariant is pinned per-gate rather than once — a change
        that widens the glob for this hook alone must fail here."""
        _stage_skill_change(git_repo)
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=other_repo, check=True)

        # Produce the correct marker value, then relocate it under the other
        # repo's hash prefix so only the prefix differs.
        write_skill_review_marker(isolated_home, git_repo, "s")
        correct_marker = skill_review_marker_path(isolated_home, git_repo, "s")
        correct_value = correct_marker.read_text()
        correct_marker.unlink()

        decoy = skill_review_marker_path(isolated_home, other_repo, "s")
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text(correct_value)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="s"),
                cwd=git_repo,
            )
            == "deny"
        )

    # -- Skill ↔ hook alignment ------------------------------------------

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
        _seed_session(isolated_home, sid)

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

    def test_skill_marker_write_command_covers_a_plugin_skill_diff(
        self, isolated_home, git_repo
    ):
        """The same recipe-vs-hook agreement as the test above, for the second
        of the two pathspecs the write side scopes its hash to.

        The write side hashes `claude/.claude/skills/**/SKILL.md` *and*
        `plugins/*/skills/**/SKILL.md`. A drift in the second is invisible to
        every stowed-path case, because dropping a pathspec that matches
        nothing in the fixture leaves the hash unchanged — both sides go on
        computing it from the same empty diff and agree on a value that proves
        nothing. Staging a plugin-located SKILL.md is what makes the second
        pathspec load-bearing for the assertion."""
        sid = "test-session-skill-cmd-plugin"
        _seed_session(isolated_home, sid)

        _stage_plugin_skill_change(git_repo)
        skill_command = extract_skill_command(SKILL_REVIEW_SKILL, "skill-review-marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)

        # Sanity check, same as the sibling test: separates "the recipe never
        # wrote anything" from "it wrote a value the hook rejects", so a
        # regression here names its own cause.
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
        ), (
            "a marker written for a plugin-located SKILL.md must satisfy the "
            "hook — write and read side disagree on the plugin pathspec"
        )

    def test_skill_marker_write_command_covers_a_routing_md_diff(
        self, isolated_home, git_repo
    ):
        """The same recipe-vs-hook agreement as the two tests above, for the
        third of the three pathspecs the write side scopes its hash to.

        Staging a ROUTING.md-only diff is what makes the third pathspec
        load-bearing for the assertion — a drift here would leave both sides
        computing the hash from the same empty diff and agreeing on a value
        that proves nothing."""
        sid = "test-session-skill-cmd-routing"
        _seed_session(isolated_home, sid)

        _stage_routing_md_change(git_repo)
        skill_command = extract_skill_command(SKILL_REVIEW_SKILL, "skill-review-marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)

        # Sanity check, same as the sibling tests: separates "the recipe never
        # wrote anything" from "it wrote a value the hook rejects", so a
        # regression here names its own cause.
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
        ), (
            "a marker written for a plan-review/ROUTING.md diff must satisfy "
            "the hook — write and read side disagree on the ROUTING.md pathspec"
        )

    def test_mixed_skill_and_routing_stale_skill_only_marker_denies(
        self, isolated_home, git_repo
    ):
        """A marker written for a SKILL.md-only diff is stale once ROUTING.md
        is also staged — the combined hash differs from the SKILL.md-only
        hash, so the gate must deny."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        _stage_routing_md_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_mixed_skill_and_routing_stale_routing_only_marker_denies(
        self, isolated_home, git_repo
    ):
        """A marker written for a ROUTING.md-only diff no longer matches once
        a SKILL.md change is also staged, so the hook denies. This direction
        doesn't isolate the ROUTING.md pathspec specifically the way the
        sibling test above does (the marker and current diff differ here
        regardless of which pathspec changed) — it exists for symmetry with
        the sibling and to pin the expected behavior in this direction too."""
        _stage_routing_md_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
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

    def test_non_bash_tool_allowed_even_with_unreviewed_skill_staged(
        self, isolated_home, git_repo
    ):
        """The tool-name filter must short-circuit before the gate logic.

        This is the state where the hook WOULD deny a `git commit`: SKILL.md
        changes staged, no marker written. A non-Bash payload must still be
        allowed, which pins the filter itself rather than the incidental fact
        that a non-Bash payload yields an empty COMMAND — the gate would also
        return "allow" here if the filter silently stopped running, so the
        staged-and-unreviewed setup is what makes this assertion load-bearing.
        """
        _stage_skill_change(git_repo)
        assert (
            run_hook(SKILL_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "deny"
        ), "precondition: this state must deny a git commit, or the test proves nothing"
        assert (
            run_hook(SKILL_REVIEW_HOOK, edit_input("/tmp/foo.txt"), cwd=git_repo) == "allow"
        )

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

    def test_structural_validator_reads_staged_blob_not_working_tree(
        self, isolated_home, git_repo
    ):
        """Structural validation must use the staged blob, not the working-tree file.

        A user who stages a malformed SKILL.md and then fixes the working
        copy without re-staging is shipping the malformed version on commit.
        The hook materializes staged blobs via `git show :<path>` into a tmp
        tree to validate the actual commit payload; this test guards that
        path. It also verifies the tmp-dir prefix is stripped from the deny
        reason so the user sees the original repo-relative path.
        """
        skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        # Stage broken YAML (unclosed flow sequence fails yaml.safe_load).
        skill_file.write_text("---\nname: broken\ndescription: [unclosed\n---\n# body\n")
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )
        # Fix the working tree without re-staging — staged blob still broken.
        skill_file.write_text("---\nname: clean\ndescription: ok\n---\n# body\n")

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert reason is not None, "hook allowed silently; expected deny"
        assert "structural validator" in reason
        assert "claude/.claude/skills/skill-review/SKILL.md" in reason
        # `mktemp -d` paths look like /tmp/tmp.XXXXXXXX/... — if the prefix
        # strip regresses, that token will leak into the user-facing reason.
        assert "/tmp/tmp." not in reason

    def test_structural_validator_allows_when_staged_clean_but_working_tree_broken(
        self, isolated_home, git_repo
    ):
        """Complement to the previous test: staged-blob isolation works in both directions.

        If the hook ever silently fell back to reading the working-tree file,
        the previous test would still pass (both states are broken at assertion
        time). This test stages clean YAML, then clobbers the working tree
        with malformed YAML — the validator must see only the staged blob and
        let the commit proceed to the marker check, which passes here because
        a current marker is written.
        """
        skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text("---\nname: clean\ndescription: ok\n---\n# body\n")
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )
        write_skill_review_marker(isolated_home, git_repo)
        # Clobber working tree with broken YAML — staged blob still clean.
        skill_file.write_text("---\nname: broken\ndescription: [unclosed\n---\n# body\n")

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_falls_back_to_system_python3_when_no_venv(
        self, isolated_home, git_repo, tmp_path, monkeypatch
    ):
        """When CLAUDE_PLUGIN_DATA points at a dir without venv/bin/python,
        the script must invoke the system `python3` so the contributor pytest
        path and the brief window before SessionStart first-runs still work."""
        plugin_data = tmp_path / "plugin-data-no-venv"
        plugin_data.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))

        # Stage broken YAML — system python3 (which has pyyaml installed for
        # the test environment) should detect it and the hook should deny with
        # the structural-validator's message. Reaching that message proves the
        # validator ran, which proves the script picked an executable python.
        skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text("---\nname: broken\ndescription: [unclosed\n---\n# body\n")
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert reason is not None, "hook allowed silently; expected deny"
        assert "structural validator" in reason

    def test_prefers_venv_python_when_present(
        self, isolated_home, git_repo, tmp_path, monkeypatch
    ):
        """When ${CLAUDE_PLUGIN_DATA}/venv/bin/python exists and is executable,
        the script must invoke that path instead of system python3 — proving
        the SessionStart-provisioned venv is the validator's runtime."""
        plugin_data = tmp_path / "plugin-data-with-venv"
        venv_bin = plugin_data / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        # Stub stand-in for the venv python: writes a marker file when invoked
        # and exits 0 (pretending validation passed). The marker's existence
        # is the proof that the script selected this path.
        marker_file = tmp_path / "fake-python-ran"
        fake_python = venv_bin / "python"
        fake_python.write_text(
            f"#!/bin/bash\necho ran > {marker_file}\nexit 0\n"
        )
        fake_python.chmod(0o755)
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))

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
        assert marker_file.exists(), (
            "fake venv python was not invoked; the script did not select "
            "${CLAUDE_PLUGIN_DATA}/venv/bin/python as expected"
        )

    def test_chained_marker_write_then_commit_allowed_without_existing_marker(
        self, isolated_home, git_repo
    ):
        """PreToolUse fires once per Bash tool call before the chain runs, so
        an on-disk marker check finds nothing for naturally-typed forms like
        `marker.sh write skill-review && git commit`. The chain itself will
        write the marker before commit, and marker.sh is the only sanctioned
        writer in either case — trust the in-chain write and allow."""
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "~/.claude/scripts/marker.sh write skill-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_chained_marker_write_does_not_skip_structural_validator(
        self, isolated_home, git_repo
    ):
        """The in-chain marker-write bypass must NOT skip the structural
        validator. A malformed SKILL.md must still be denied even when the
        chain claims to write a marker — the validator gate is independent
        of the marker-hash gate."""
        skill_file = git_repo / "claude" / ".claude" / "skills" / "skill-review" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text("---\nname: broken\ndescription: [unclosed\n---\n# body\n")
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input(
                "~/.claude/scripts/marker.sh write skill-review && git commit -m foo",
                session_id=DEFAULT_TEST_SESSION_ID,
            ),
            cwd=git_repo,
        )
        assert reason is not None, "hook allowed silently; expected deny from validator"
        assert "structural validator" in reason

    def test_chained_non_canonical_marker_path_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """A bogus marker.sh path (not under /.claude/scripts/) must not
        trigger the skill-review bypass even when chained correctly. Closes
        the gap where enforce-marker-script-shape's leading-anchor check
        would not fire on a non-leading marker.sh fragment in a chain."""
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "git add . && /home/evil/marker.sh write skill-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_echo_wrapping_marker_text_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """`echo ~/.claude/scripts/marker.sh write skill-review && git commit`
        text-matches a marker write but doesn't actually invoke marker.sh.
        Parallel to the code-review gate; anchor must reject wrapper commands."""
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "echo ~/.claude/scripts/marker.sh write skill-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_heredoc_pipe_with_marker_text_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """Heredoc body text containing marker.sh write must not wedge the
        skill-review gate open, even if a piped bash subshell would execute
        the body — the outer shape is not a sanctioned chained form."""
        _stage_skill_change(git_repo)
        cmd = (
            "cat <<EOF | bash\n"
            "~/.claude/scripts/marker.sh write skill-review\n"
            "EOF\n"
            "git commit -m foo"
        )
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(cmd, session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_chained_code_review_marker_does_not_authorize_skill_review(
        self, isolated_home, git_repo
    ):
        """Chaining `marker.sh write code-review` (wrong skill) before
        `git commit` must NOT authorize a skill-review-gated commit. Each
        gate's bypass is scoped to its own skill name."""
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "~/.claude/scripts/marker.sh write code-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "env_overrides",
        [
            {},
            {"CLAUDE_CONFIG_DIR": "/some/profile/dir"},
            {"CLAUDE_CONFIG_DIR": "relative/path"},
        ],
    )
    def test_plugin_lib_sh_config_dir_matches_stowed_lib_sh(self, env_overrides, tmp_path):
        """_lib_config_dir must produce identical output (and exit status)
        from the plugin's trimmed _lib.sh and the stowed copy for the same
        input.

        marker.sh (the write side) always sources the stowed
        $HOME/.claude/hooks/_lib.sh directly — never a plugin-bundled copy —
        so this hook (the read side) resolving a different config directory
        for the same environment would break the gate: markers written by
        one side would never land where the other looks.
        """
        harness = '. "{lib}"; _lib_config_dir; printf "RC:%s\\n" "$?"'
        env = {**os.environ, "HOME": str(tmp_path), **env_overrides}
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/skill-management/hooks/_lib.sh's _lib_config_dir "
            "produces different output than the stowed "
            "claude/.claude/hooks/_lib.sh copy for env_overrides="
            f"{env_overrides!r} — plugin: {plugin_result.stdout!r}, "
            f"stowed: {stowed_result.stdout!r}"
        )

    def test_plugin_lib_sh_repo_hash_matches_stowed_lib_sh(self):
        """_marker_lib_repo_hash must produce identical output from the plugin's
        trimmed _lib.sh and the stowed copy for the same input.

        marker.sh (the write side) always sources the stowed
        $HOME/.claude/hooks/_lib.sh directly — never a plugin-bundled copy — so
        this hook (the read side) computing a different hash for the same
        repo-toplevel path would permanently break the gate: markers written
        by one side would never be found by the other.

        A behavioral check on this one function — not a whole-file byte
        comparison — is the right invariant: the plugin's _lib.sh is a trimmed
        copy (see its header) containing only what require-skill-review.sh
        actually sources; whole-file identity would force this plugin to
        carry, and re-sync on every change to, worktree/git-enforcement code
        it never calls.
        """
        harness = '. "{lib}"; _marker_lib_repo_hash "/some/repo/toplevel"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout, "expected a non-empty hash"
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/skill-management/hooks/_lib.sh's _marker_lib_repo_hash "
            "produces a different hash than the stowed claude/.claude/hooks/_lib.sh "
            f"copy — plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )

    def test_plugin_lib_sh_marker_value_present_matches_stowed_lib_sh(self, tmp_path):
        """_lib_marker_value_present must behave identically in the plugin's
        trimmed _lib.sh and the stowed copy.

        This function is the read side of every content-addressed gate. A
        security-relevant fix applied to only one copy — tightening the
        whole-line match, say — would leave the other releasing gates on
        values it should reject, and require-skill-review.sh's own behavioral
        tests would not necessarily probe the exact edge the fix addressed.
        Same reasoning as the _marker_lib_repo_hash parity test above, applied
        to the other shared function both copies carry.
        """
        markers = tmp_path / "markers"
        markers.mkdir()
        (markers / "abc123.session-one").write_text("deadbeef\n")
        (markers / "other999.session-two").write_text("cafebabe\n")

        harness = '. "$1"; _lib_marker_value_present "$2" "$3" "$4"; echo "exit=$?"'
        # (expected_value, prefix, must_authorize)
        cases = [
            ("deadbeef", "abc123.", True),
            ("deadbee", "abc123.", False),  # substring must not match
            ("deadbeefextra", "abc123.", False),  # superstring must not match
            ("cafebabe", "abc123.", False),  # value sits under another prefix
            ("deadbeef", "nosuch.", False),  # prefix matches nothing
        ]
        for expected_value, prefix, must_authorize in cases:
            outputs = [
                subprocess.run(
                    ["bash", "-c", harness, "_", str(lib), str(markers), expected_value, prefix],
                    capture_output=True, text=True, check=False,
                ).stdout
                for lib in (_PLUGIN_LIB, _STOWED_LIB)
            ]
            assert outputs[0] == outputs[1], (
                "plugins/skill-management/hooks/_lib.sh's _lib_marker_value_present "
                "disagrees with the stowed claude/.claude/hooks/_lib.sh copy for "
                f"value={expected_value!r} prefix={prefix!r} — "
                f"plugin: {outputs[0]!r}, stowed: {outputs[1]!r}"
            )
            # Pin the shared verdict too, so the copies agreeing on a WRONG
            # answer still fails rather than passing as "identical".
            expected_exit = "exit=0\n" if must_authorize else "exit=1\n"
            assert outputs[0] == expected_exit, (
                f"both _lib.sh copies returned {outputs[0]!r} for "
                f"value={expected_value!r} prefix={prefix!r}; expected {expected_exit!r}"
            )

    def test_plugin_lib_sh_parses_tool_input_same_as_stowed_lib_sh(self):
        """_lib_parse_tool_input_or_deny (and the _lib_jq it calls) must behave
        identically between the plugin's trimmed copy and the stowed copy."""
        harness = (
            'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
            '. "{lib}"; '
            '_lib_parse_tool_input_or_deny "test-msg"; '
            'printf "OK:%s:%s\\n" "$TOOL_NAME" "$COMMAND"'
        )
        payload = '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"}}'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            input=payload, capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            input=payload, capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/skill-management/hooks/_lib.sh's _lib_parse_tool_input_or_deny "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy — "
            f"plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )

    def test_plugin_lib_sh_jq_fallback_matches_stowed_lib_sh(self, tmp_path):
        """Without timeout(1) in PATH, _lib_jq's bare-jq fallback branch must
        behave identically between the plugin's copy and the stowed copy.

        The default-PATH parity test above never exercises this branch — jq
        and bash's own timeout(1) is present on the test runner's PATH, so
        _lib_jq's `if command -v timeout` always takes the wrapped branch.
        Mirrors test_lib.py::test_timeout_absent_fallback_valid_payload_returns_ok's
        technique: build a PATH with jq/bash/coreutils symlinked in but
        timeout deliberately omitted.
        """
        import shutil

        jq_path = shutil.which("jq")
        bash_path = shutil.which("bash")
        if not jq_path or not bash_path:
            pytest.skip("jq or bash not found in PATH")
        (tmp_path / "jq").symlink_to(jq_path)
        (tmp_path / "bash").symlink_to(bash_path)
        for cmd in ["head", "tail", "cat", "cut", "printf"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (tmp_path / cmd).symlink_to(cmd_path)
        env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}

        harness = (
            'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
            '. "{lib}"; '
            '_lib_parse_tool_input_or_deny "test-msg"; '
            'printf "OK:%s:%s\\n" "$TOOL_NAME" "$COMMAND"'
        )
        payload = '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"}}'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            input=payload, capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            input=payload, capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/skill-management/hooks/_lib.sh's _lib_jq timeout-absent fallback "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy — "
            f"plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "~/.claude/scripts/marker.sh write skill-review && git commit -m foo",
            # Also exercises the Step-2 skill-mismatch exclusion: this chain's
            # skill is plan-review, but the call below targets skill-review,
            # so Step 1 (shape) passes while Step 2 (skill match) must fail.
            "~/.claude/scripts/marker.sh write plan-review && git commit -m foo",
            "git commit -m foo",
        ],
    )
    def test_plugin_lib_sh_chains_marker_write_same_as_stowed_lib_sh(self, command):
        """_lib_chains_marker_write_before_commit must return the same verdict
        from the plugin's trimmed copy and the stowed copy for the same input."""
        harness = '. "{lib}"; _lib_chains_marker_write_before_commit "$1" skill-review; printf "RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", command],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", command],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout, (
            "plugins/skill-management/hooks/_lib.sh's _lib_chains_marker_write_before_commit "
            "behaves differently than the stowed claude/.claude/hooks/_lib.sh copy for "
            f"{command!r} — plugin: {plugin_result.stdout!r}, stowed: {stowed_result.stdout!r}"
        )


class TestRequireSkillReviewHonorsConfigDir:
    """CLAUDE_CONFIG_DIR relocates the skill-review marker directory the same
    way for marker.sh (write) and this hook (read) -- see marker.sh and the
    cross-account bypass this closes (ledger row 7)."""

    def test_marker_under_matching_config_dir_allows(self, isolated_home, git_repo, tmp_path):
        """CLAUDE_CONFIG_DIR-set happy path: a marker written under the
        resolved config dir satisfies the gate when the session runs under
        the same value."""
        profile = tmp_path / "profile"
        _stage_skill_change(git_repo)
        write_skill_review_marker(
            isolated_home, git_repo, session_id=DEFAULT_TEST_SESSION_ID, config_dir=profile
        )
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(profile)},
            )
            == "allow"
        )

    def test_marker_under_different_config_dir_does_not_authorize(
        self, isolated_home, git_repo, tmp_path
    ):
        """Cross-account bypass regression: a marker written under one
        CLAUDE_CONFIG_DIR value must not satisfy the gate when the session
        runs under a different one."""
        profile_a = tmp_path / "profile-a"
        profile_b = tmp_path / "profile-b"
        _stage_skill_change(git_repo)
        write_skill_review_marker(
            isolated_home, git_repo, session_id=DEFAULT_TEST_SESSION_ID, config_dir=profile_a
        )
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(profile_b)},
            )
            == "deny"
        )

    def test_unresolvable_config_dir_denies(self, isolated_home, git_repo):
        """Fail closed: a relative CLAUDE_CONFIG_DIR (unresolvable) must deny
        the gate outright, even with a valid marker at the default location."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo, session_id=DEFAULT_TEST_SESSION_ID)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
            )
            == "deny"
        )


def _run_hook_with_stderr(hook, tool_input: dict, cwd) -> subprocess.CompletedProcess:
    """Run a hook and return the full CompletedProcess so callers can inspect both stdout and stderr."""
    return subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def _stage_oversized_corpus(git_repo, num_skills: int = 6, chars_each: int = 1500) -> None:
    """Stage multiple SKILL.md files whose combined description chars exceed the 8000-char budget."""
    for i in range(num_skills):
        skill_file = git_repo / "claude" / ".claude" / "skills" / f"corpus-skill-{i}" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        description = "x" * chars_each
        skill_file.write_text(
            f"---\ndescription: {description!r}\n---\n# body\n"
        )
        subprocess.run(
            ["git", "add", str(skill_file.relative_to(git_repo))],
            cwd=git_repo,
            check=True,
        )


class TestCorpusBudgetWarning:
    """Corpus budget check emits a non-blocking stderr warning on skill-touching commits.

    The corpus check fires only when at least one SKILL.md is staged. It is
    intentionally non-blocking — the commit is allowed regardless of whether the
    aggregate description total exceeds the Claude Code listing budget. Hard
    enforcement lives in pytest/CI (test_total_within_listing_budget).
    """

    def test_corpus_over_budget_emits_warning_but_allows_commit(
        self, isolated_home, git_repo
    ):
        """When staged skills push total descriptions over 8000 chars, a warning
        appears on stderr but the commit decision is allow (no deny JSON on stdout)."""
        _stage_oversized_corpus(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        # Hook must allow (no JSON deny on stdout).
        assert not result.stdout.strip() or (
            "deny" not in result.stdout
        ), f"unexpected deny: {result.stdout}"
        # Warning must appear on stderr.
        assert "corpus budget warning" in result.stderr, (
            f"expected corpus budget warning on stderr; got: {result.stderr!r}"
        )

    def test_corpus_under_budget_no_warning(self, isolated_home, git_repo):
        """A single small skill staged does not trigger the corpus budget warning."""
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert not result.stdout.strip() or "deny" not in result.stdout
        assert "corpus budget warning" not in result.stderr

    def test_corpus_warning_does_not_fire_when_no_skill_staged(self, isolated_home, git_repo):
        """The corpus check does not warn when no SKILL.md is staged.

        The hook exits early at the SKILL_DIFF check (no staged skills), so the
        corpus block is never reached."""
        # git_repo has only file.txt staged — no SKILL.md.
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        # Hook must allow (early exit before corpus block).
        assert not result.stdout.strip() or "deny" not in result.stdout
        assert "corpus budget warning" not in result.stderr

    def test_corpus_warning_allows_even_when_validator_python_missing(
        self, isolated_home, git_repo, tmp_path, monkeypatch
    ):
        """When the validator python cannot be found, the corpus block must not deny.

        A misconfigured environment must not block commits — the corpus check is
        best-effort and non-blocking by design.
        """
        # Point CLAUDE_PLUGIN_DATA at a dir without a venv; system python3 will be used.
        # Then shadow python3 with a stub that exits 0 but writes nothing — simulating
        # a missing pyyaml in a constrained environment where the corpus check silently
        # does nothing.
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_python = fake_bin / "python3"
        fake_python.write_text("#!/bin/bash\nexit 0\n")
        fake_python.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
        monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)

        _stage_oversized_corpus(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        # Must not deny even though corpus would exceed budget.
        assert not result.stdout.strip() or "deny" not in result.stdout
