"""Tests for require-skill-review.sh."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    SCRIPTS_DIR,
    _make_git_exiting_with_status,
    _make_git_recording_argv,
    absolute_git_dir,
    assert_cap_engaged,
    bare_remote_with_default_branch,
    bash_input,
    build_conflicted_cherry_pick,
    build_conflicted_merge,
    build_conflicted_merge_via_origin_with_upstream_skill_edit,
    build_conflicted_revert,
    build_conflicted_revert_with_clean_gated_removal,
    build_octopus_merge_conflict,
    edit_input,
    extract_skill_command,
    push_conflicting_edit_to_origin,
    revert_subtraction_base,
    run_hook,
    run_hook_reason,
    run_skill_command,
    skill_review_marker_path,
    staged_diff_hash_at_base,
    write_skill_review_marker,
)

from .conftest import _seed_session

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
    skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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


def _stage_repo_root_skill_change(git_repo):
    """Stage a SKILL.md change at repo-root skills/<name>/SKILL.md (skills/**/SKILL.md),
    the layout used by a repo whose plugin root is the repo root."""
    skill_file = git_repo / "skills" / "skill-review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("## test repo-root skill\n")
    subprocess.run(
        ["git", "add", str(skill_file.relative_to(git_repo))],
        cwd=git_repo,
        check=True,
    )


def _stage_project_layer_skill_change(git_repo):
    """Stage a SKILL.md change under this repo's own project-layer skills
    (.claude/skills/**/SKILL.md), distinct from the stowed and
    plugin-scoped pathspecs."""
    skill_file = git_repo / ".claude" / "skills" / "test-project-layer" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("## test project-layer skill\n")
    subprocess.run(
        ["git", "add", str(skill_file.relative_to(git_repo))],
        cwd=git_repo,
        check=True,
    )


def _stage_routing_md_change(git_repo, body: str = ""):
    """Stage a plan-review/ROUTING.md change so the hook has a non-empty
    ROUTING.md diff to check. `body` appends extra content, so a second call
    stages a diff with a different hash than the first."""
    routing_file = git_repo / "claude-skills" / "skills" / "plan-review" / "ROUTING.md"
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
        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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
        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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
        assert _MARKER_GATE_TOKEN in reason
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
        of the three SKILL.md-content pathspecs the write side scopes its
        hash to (stowed, plugin, repo-root).

        The write side hashes `claude-skills/skills/**/SKILL.md`,
        `plugins/*/skills/**/SKILL.md`, and `skills/**/SKILL.md`. A drift in
        the plugin pathspec is invisible to every stowed-path case, because
        dropping a pathspec that matches nothing in the fixture leaves the
        hash unchanged — both sides go on computing it from the same empty
        diff and agree on a value that proves nothing. Staging a
        plugin-located SKILL.md is what makes this pathspec load-bearing for
        the assertion."""
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

    def test_skill_marker_write_command_covers_a_repo_root_skill_diff(
        self, isolated_home, git_repo
    ):
        """The same recipe-vs-hook agreement as the test above, for the third
        of the four SKILL.md-content pathspecs the write side scopes its
        hash to (stowed, plugin, repo-root, project-layer).

        Staging a repo-root-located SKILL.md (skills/**/SKILL.md) is what
        makes this pathspec load-bearing for the assertion — a drift here
        would leave both sides computing the hash from the same empty diff
        and agreeing on a value that proves nothing."""
        sid = "test-session-skill-cmd-repo-root"
        _seed_session(isolated_home, sid)

        _stage_repo_root_skill_change(git_repo)
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
            "a marker written for a repo-root-located SKILL.md must satisfy "
            "the hook — write and read side disagree on the repo-root pathspec"
        )

    def test_skill_marker_write_command_covers_a_project_layer_skill_diff(
        self, isolated_home, git_repo
    ):
        """The same recipe-vs-hook agreement as the test above, for the
        fourth of the four SKILL.md-content pathspecs the write side scopes
        its hash to.

        The write side also hashes `.claude/skills/**/SKILL.md`, this repo's
        own project-layer skills. Dropping that pathspec from both sides
        would not surface here: `require-skill-review.sh`'s earlier
        `SKILL_DIFF` early-exit also misses `.claude/skills/**/SKILL.md`
        pre-fix, so it returns `allow` before ever reaching the
        `CURRENT_HASH` comparison this test targets. Staging a project-layer
        SKILL.md is what makes the fourth pathspec load-bearing for the
        assertion. The deny-path test
        (`test_project_layer_skill_no_marker_denies_commit`) is what actually
        pins the `SKILL_DIFF` early-exit against this pathspec."""
        sid = "test-session-skill-cmd-project-layer"
        _seed_session(isolated_home, sid)

        _stage_project_layer_skill_change(git_repo)
        skill_command = extract_skill_command(SKILL_REVIEW_SKILL, "skill-review-marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)

        # Sanity check: catches "the recipe never wrote anything" (a
        # crash-shaped failure) by name, but not a pathspec that silently
        # never fires — see the deny-path sibling test for that.
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
            "a marker written for a project-layer SKILL.md must satisfy the "
            "hook — write and read side disagree on the project-layer pathspec"
        )

    def test_skill_marker_write_command_covers_a_routing_md_diff(
        self, isolated_home, git_repo
    ):
        """The same recipe-vs-hook agreement as the tests above, for the
        fifth of the five pathspecs the write side scopes its hash to.

        Staging a ROUTING.md-only diff is what makes this pathspec
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

    def test_skill_marker_write_command_excludes_unrelated_staged_file(
        self, isolated_home, git_repo
    ):
        """The write-side recipe's hash must stay scoped to the SKILL.md/ROUTING.md
        pathspecs even with an unrelated file also staged (git_repo already stages
        file.txt). An allow/deny assertion alone can't catch a bug that empties the
        pathspec array on both the read and write side identically: `git diff
        --cached --` with nothing following the `--` matches the entire staged
        diff rather than nothing, so both sides would compute the same widened
        hash and still agree with each other. This test instead recomputes the
        expected scoped hash independently and compares it against what the real
        marker.sh recipe wrote."""
        sid = "test-session-skill-cmd-excludes-unrelated"
        _seed_session(isolated_home, sid)

        _stage_repo_root_skill_change(git_repo)
        skill_command = extract_skill_command(SKILL_REVIEW_SKILL, "skill-review-marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)

        marker_path = skill_review_marker_path(isolated_home, git_repo, session_id=sid)
        assert marker_path.exists(), (
            "SKILL.md marker-write recipe ran but no marker landed at the "
            "path the hook computes — the skill and hook disagree on layout."
        )
        actual_hash = marker_path.read_text().strip()

        scoped_diff = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--",
                "claude-skills/skills/**/SKILL.md",
                "plugins/*/skills/**/SKILL.md",
                "skills/**/SKILL.md",
                ".claude/skills/**/SKILL.md",
                "claude-skills/skills/plan-review/ROUTING.md",
            ],
            capture_output=True,
            check=True,
            cwd=git_repo,
        ).stdout
        expected_hash = hashlib.sha256(scoped_diff).hexdigest()
        assert actual_hash == expected_hash, (
            "marker.sh's write-side hash must match hashing only the scoped "
            "SKILL.md/ROUTING.md diff"
        )

        unscoped_diff = subprocess.run(
            ["git", "diff", "--cached"], capture_output=True, check=True, cwd=git_repo
        ).stdout
        unscoped_hash = hashlib.sha256(unscoped_diff).hexdigest()
        assert actual_hash != unscoped_hash, (
            "the marker hash must differ from hashing the full staged diff — if "
            "these are equal, file.txt's unrelated staged change leaked into the "
            "scoped hash, which is exactly the symptom an emptied pathspec array "
            "would produce"
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

    def test_project_layer_skill_no_marker_denies_commit(self, isolated_home, git_repo):
        """Project-layer SKILL.md (.claude/skills/**/SKILL.md) is gated like stowed skills."""
        _stage_project_layer_skill_change(git_repo)
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

    def test_repo_root_skill_no_marker_denies_commit(self, isolated_home, git_repo):
        """Repo-root SKILL.md (skills/**/SKILL.md) is gated like stowed skills."""
        _stage_repo_root_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_repo_root_skill_correct_hash_marker_allows(self, isolated_home, git_repo):
        """Repo-root SKILL.md allows when the marker covers the repo-root diff hash."""
        _stage_repo_root_skill_change(git_repo)
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

    def test_mixed_repo_root_and_stowed_skill_stale_repo_root_only_marker_denies(
        self, isolated_home, git_repo
    ):
        """A marker written for a repo-root-only diff is stale when a stowed SKILL.md is later
        staged — the combined hash differs from the repo-root-only hash, so the gate must deny."""
        _stage_repo_root_skill_change(git_repo)
        # Write marker that covers only the repo-root SKILL.md diff.
        write_skill_review_marker(isolated_home, git_repo)
        # Stage an additional stowed SKILL.md; combined hash now differs from stored marker.
        _stage_skill_change(git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_mixed_project_layer_and_stowed_skill_stale_project_layer_only_marker_denies(
        self, isolated_home, git_repo
    ):
        """A marker written for a project-layer-only diff is stale when a stowed SKILL.md is
        later staged — the combined hash differs from the project-layer-only hash, so the gate must deny."""
        _stage_project_layer_skill_change(git_repo)
        # Write marker that covers only the project-layer SKILL.md diff.
        write_skill_review_marker(isolated_home, git_repo)
        # Stage an additional stowed SKILL.md; combined hash now differs from stored marker.
        _stage_skill_change(git_repo)
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
        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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
        assert "claude-skills/skills/skill-review/SKILL.md" in reason
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
        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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
        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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

    @pytest.mark.timing
    def test_structural_validator_timeout_denies_with_timeout_message(
        self, isolated_home, git_repo, tmp_path, monkeypatch
    ):
        """A validator that hangs past the 10s cap must deny with a
        timeout-specific message, not the generic wrapped-stderr message --
        timeout(1) SIGTERMs the hung process before it can flush stderr, so
        the generic message would otherwise render an empty/misleading
        reason."""
        import shutil
        import time

        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available in PATH")

        plugin_data = tmp_path / "plugin-data-with-hanging-venv"
        venv_bin = plugin_data / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        # Sleeps well past the 10s cap so the assertion below proves the cap
        # actually fired rather than the process finishing on its own.
        fake_python.write_text("#!/bin/bash\nsleep 60\n")
        fake_python.chmod(0o755)
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))

        _stage_skill_change(git_repo)

        start = time.monotonic()
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        elapsed = time.monotonic() - start

        assert reason is not None, "hook allowed silently; expected deny"
        assert "validator killed (exit " in reason, reason
        assert elapsed < 30, f"validator call took {elapsed:.1f}s — the 10s cap did not fire"

    @pytest.mark.parametrize("validator_status", [137, 143])
    def test_structural_validator_signal_death_status_denies_with_killed_message(
        self, isolated_home, git_repo, tmp_path, monkeypatch, validator_status
    ):
        """137 (SIGKILL after the -k grace) and 143 (BusyBox SIGTERM) must deny
        with the killed-validator message, not fall through the case arm and
        allow the commit. The fake interpreter exits with the status
        immediately, so no cap or timing is engaged."""
        plugin_data = tmp_path / "plugin-data-with-exiting-venv"
        venv_bin = plugin_data / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text(f"#!/bin/bash\nexit {validator_status}\n")
        fake_python.chmod(0o755)
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))

        _stage_skill_change(git_repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )

        assert reason is not None, "hook allowed silently; expected deny"
        assert f"validator killed (exit {validator_status})" in reason, reason

    def test_structural_validator_denies_real_violation_when_timeout_binaries_absent(
        self, isolated_home, git_repo, tmp_path
    ):
        """Without timeout(1) or gtimeout(1) on PATH, _lib_capped_for's
        uncapped fallback still runs the validator, which must still catch
        and deny a real structural violation -- not crash with "command not
        found" (the pre-fix regression on a bare `timeout 10s` call) and not
        silently skip validation."""
        import shutil

        bin_dir = tmp_path / "bin-without-timeout"
        bin_dir.mkdir()
        for cmd in ("git", "jq", "sha256sum", "awk", "grep", "mktemp", "dirname", "mkdir", "rm", "cat", "python3"):
            cmd_path = shutil.which(cmd)
            if not cmd_path:
                pytest.skip(f"{cmd} not found in PATH")
            (bin_dir / cmd).symlink_to(cmd_path)

        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        # Broken YAML (unclosed flow sequence fails yaml.safe_load) — a real
        # structural violation, not a hang.
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
            extra_env={"PATH": str(bin_dir)},
        )
        assert reason is not None, "hook allowed silently; expected deny"
        assert "structural validator" in reason
        assert "timed out" not in reason
        assert "claude-skills/skills/skill-review/SKILL.md" in reason

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
        skill_file = git_repo / "claude-skills" / "skills" / "skill-review" / "SKILL.md"
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

    def test_plugin_lib_sh_repo_hash_fails_closed_like_stowed_lib_sh_on_broken_sha256sum(
        self, tmp_path, monkeypatch
    ):
        """_marker_lib_repo_hash must return the same nonzero exit code (and
        empty stdout) from both copies when sha256sum is broken.

        The stowed copy delegates to _lib_hash_diff_text, which returns 1 on
        an empty digest; the plugin copy duplicates that check inline rather
        than sourcing it (plugin boundary — see this file's header). Only
        asserting stdout equality on a happy-path input (the test above)
        would miss a divergence confined to this failure path, so this pins
        exit-code parity on a broken-sha256sum input directly.
        """
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_sha256sum = fake_bin / "sha256sum"
        fake_sha256sum.write_text("#!/bin/bash\nexit 1\n")
        fake_sha256sum.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

        harness = '. "{lib}"; _marker_lib_repo_hash "/some/repo/toplevel"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.returncode == stowed_result.returncode, (
            "plugins/skill-management/hooks/_lib.sh's _marker_lib_repo_hash "
            "returns a different exit code than the stowed "
            "claude/.claude/hooks/_lib.sh copy on a broken sha256sum — "
            f"plugin: {plugin_result.returncode!r}, stowed: {stowed_result.returncode!r}"
        )
        assert plugin_result.returncode != 0
        assert plugin_result.stdout == stowed_result.stdout == ""

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
        # dirname is required too: the stowed _lib.sh's own sourcing of
        # _config.sh resolves its path via `$(dirname "${BASH_SOURCE[0]}")`,
        # and a failed source now aborts _lib.sh's own sourcing entirely.
        for cmd in ["head", "tail", "cat", "cut", "printf", "dirname"]:
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

    # Mirrors test_lib.py's four test_lib_capped_for_* functions, split the
    # same way: each case gets its own skip guard, since pytest.skip()
    # aborts the whole function and would otherwise let a PATH missing
    # timeout(1) also skip the neither-present uncapped-fallback case below.

    @pytest.mark.timing
    def test_plugin_lib_sh_capped_for_enforces_cap_when_timeout_present_matches_stowed_lib_sh(self, tmp_path):
        """timeout(1) present: both _lib_capped_for copies cap a hung command at exit 124."""
        import shutil

        harness = '. "{lib}"; _lib_capped_for "$1" "${{@:2}}"'
        bash_path = shutil.which("bash")
        sleep_path = shutil.which("sleep")
        timeout_path = shutil.which("timeout")
        dirname_path = shutil.which("dirname")
        if not bash_path or not sleep_path or not dirname_path:
            pytest.skip("bash, sleep, or dirname not found in PATH")
        if not timeout_path:
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        # dirname must be on PATH too: the stowed _lib.sh sources _config.sh via
        # `$(dirname "${BASH_SOURCE[0]}")`, so a PATH stripped down to just
        # timeout/bash/sleep fails that source step before _lib_capped_for is
        # even defined, surfacing as a spurious "command not found" (127) here
        # rather than the fallback behavior this case actually targets.
        timeout_bin_dir = tmp_path / "bin-with-timeout"
        timeout_bin_dir.mkdir()
        (timeout_bin_dir / "timeout").symlink_to(timeout_path)
        (timeout_bin_dir / "bash").symlink_to(bash_path)
        (timeout_bin_dir / "sleep").symlink_to(sleep_path)
        (timeout_bin_dir / "dirname").symlink_to(dirname_path)
        env = {"PATH": str(timeout_bin_dir), "HOME": str(timeout_bin_dir)}

        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", "1", "sleep", "5"],
            capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", "1", "sleep", "5"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.returncode == stowed_result.returncode == 124, (
            "plugins/skill-management/hooks/_lib.sh's _lib_capped_for disagrees with "
            "the stowed claude/.claude/hooks/_lib.sh copy when timeout(1) is present — "
            f"plugin: {plugin_result.returncode!r}, stowed: {stowed_result.returncode!r}"
        )

    @pytest.mark.timing
    def test_plugin_lib_sh_capped_for_enforces_cap_via_gtimeout_when_timeout_absent_matches_stowed_lib_sh(
        self, tmp_path
    ):
        """timeout(1) absent, gtimeout(1) present (Homebrew coreutils naming): both copies still cap at exit 124."""
        import shutil

        harness = '. "{lib}"; _lib_capped_for "$1" "${{@:2}}"'
        bash_path = shutil.which("bash")
        sleep_path = shutil.which("sleep")
        timeout_path = shutil.which("timeout")
        dirname_path = shutil.which("dirname")
        if not bash_path or not sleep_path or not dirname_path:
            pytest.skip("bash, sleep, or dirname not found in PATH")
        if not timeout_path:
            pytest.skip("timeout(1) not available to alias as gtimeout — BSD/macOS without coreutils")

        # Alias the real timeout binary under the gtimeout name and omit timeout
        # from PATH entirely, simulating a Homebrew-coreutils-only machine.
        gtimeout_bin_dir = tmp_path / "bin-with-gtimeout"
        gtimeout_bin_dir.mkdir()
        (gtimeout_bin_dir / "gtimeout").symlink_to(timeout_path)
        (gtimeout_bin_dir / "bash").symlink_to(bash_path)
        (gtimeout_bin_dir / "sleep").symlink_to(sleep_path)
        (gtimeout_bin_dir / "dirname").symlink_to(dirname_path)
        env = {"PATH": str(gtimeout_bin_dir), "HOME": str(gtimeout_bin_dir)}

        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", "1", "sleep", "5"],
            capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", "1", "sleep", "5"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.returncode == stowed_result.returncode == 124, (
            "plugins/skill-management/hooks/_lib.sh's _lib_capped_for disagrees with "
            "the stowed claude/.claude/hooks/_lib.sh copy when only gtimeout(1) is "
            f"present — plugin: {plugin_result.returncode!r}, stowed: {stowed_result.returncode!r}"
        )

    def test_plugin_lib_sh_capped_for_runs_uncapped_when_neither_timeout_nor_gtimeout_present_matches_stowed_lib_sh(
        self, tmp_path
    ):
        """Neither timeout(1) nor gtimeout(1) on PATH: both copies run the command uncapped, not "command not found".

        This is the actual subject of the plugin's _lib_capped_for hardening:
        stock macOS without Homebrew coreutils has neither binary, and must
        not skip alongside the timeout(1)-requiring cases above.
        """
        import shutil

        harness = '. "{lib}"; _lib_capped_for "$1" "${{@:2}}"'
        bash_path = shutil.which("bash")
        sleep_path = shutil.which("sleep")
        dirname_path = shutil.which("dirname")
        if not bash_path or not sleep_path or not dirname_path:
            pytest.skip("bash, sleep, or dirname not found in PATH")

        no_timeout_bin_dir = tmp_path / "bin-without-timeout"
        no_timeout_bin_dir.mkdir()
        (no_timeout_bin_dir / "bash").symlink_to(bash_path)
        (no_timeout_bin_dir / "sleep").symlink_to(sleep_path)
        (no_timeout_bin_dir / "dirname").symlink_to(dirname_path)
        env = {"PATH": str(no_timeout_bin_dir), "HOME": str(no_timeout_bin_dir)}

        # seconds (0.2) is well under the sleep duration (0.6) -- a real cap would
        # kill this early, so both copies exiting 0 proves both ran uncapped.
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", "0.2", "sleep", "0.6"],
            capture_output=True, text=True, check=False, env=env,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", "0.2", "sleep", "0.6"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert plugin_result.returncode == stowed_result.returncode == 0, (
            "plugins/skill-management/hooks/_lib.sh's _lib_capped_for disagrees with "
            "the stowed claude/.claude/hooks/_lib.sh copy when neither timeout(1) nor "
            f"gtimeout(1) is present — plugin: {plugin_result.returncode!r}, "
            f"stowed: {stowed_result.returncode!r}"
        )

    def test_plugin_lib_sh_capped_for_aborts_on_unset_seconds_argument_matches_stowed_lib_sh(self):
        """Unset SECONDS hard-aborts both copies via ${1:?msg} rather than falling through to run the command uncapped."""
        guard_harness = '. "{lib}"; _lib_capped_for "$UNSET_VAR" echo should-not-run; echo SHOULD_NOT_REACH'
        plugin_result = subprocess.run(
            ["bash", "-c", guard_harness.format(lib=_PLUGIN_LIB)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", guard_harness.format(lib=_STOWED_LIB)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.returncode != 0 and stowed_result.returncode != 0, (
            "plugins/skill-management/hooks/_lib.sh's _lib_capped_for disagrees with "
            "the stowed claude/.claude/hooks/_lib.sh copy on an unset seconds argument — "
            f"plugin: {plugin_result.returncode!r}, stowed: {stowed_result.returncode!r}"
        )
        assert "SHOULD_NOT_REACH" not in plugin_result.stdout
        assert "should-not-run" not in plugin_result.stdout
        assert "_lib_capped_for requires a seconds argument" in plugin_result.stderr, repr(plugin_result.stderr)

    @pytest.mark.timing
    @pytest.mark.parametrize("lib_path", [_PLUGIN_LIB, _STOWED_LIB], ids=["plugin", "stowed"])
    def test_capped_for_sigterm_immune_child_is_killed_by_the_grace_and_reports_137(self, tmp_path, lib_path):
        """A SIGTERM-immune child (see `_write_conditional_sleep_shim` in
        conftest.py) must return status 137 within cap+grace from each copy of
        `_lib_capped_for`, not hang to the fixture's own much longer sleep."""
        import shutil

        harness = '. "{lib}"; _lib_capped_for "$1" "${{@:2}}"'
        bash_path = shutil.which("bash")
        sleep_path = shutil.which("sleep")
        timeout_path = shutil.which("timeout")
        dirname_path = shutil.which("dirname")
        if not bash_path or not sleep_path or not dirname_path:
            pytest.skip("bash, sleep, or dirname not found in PATH")
        if not timeout_path:
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        timeout_bin_dir = tmp_path / "bin-with-timeout"
        timeout_bin_dir.mkdir()
        (timeout_bin_dir / "timeout").symlink_to(timeout_path)
        (timeout_bin_dir / "bash").symlink_to(bash_path)
        (timeout_bin_dir / "sleep").symlink_to(sleep_path)
        (timeout_bin_dir / "dirname").symlink_to(dirname_path)
        env = {"PATH": str(timeout_bin_dir), "HOME": str(timeout_bin_dir)}

        args = ["_", "1", "bash", "-c", 'trap "" TERM; exec sleep 30']

        start = time.monotonic()
        result = subprocess.run(
            ["bash", "-c", harness.format(lib=lib_path), *args],
            capture_output=True, text=True, check=False, env=env,
        )
        elapsed = time.monotonic() - start

        assert result.returncode == 137, repr(result)
        # Nominal 3s (cap 1 + grace 2). The bound sits well inside the fixture's 30s sleep,
        # so it separates a fired grace from a hang, and its headroom over nominal absorbs subprocess-spawn contention.
        assert elapsed < 15, f"{lib_path} took {elapsed:.1f}s — the -k grace did not fire"

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

    # -- Fixed-input parity for the six closure functions this plugin's
    # trimmed _lib.sh copies from the stowed _lib.sh. declare -f definition-equality
    # (TestSharedGateDiffBaseClosureDefinitionEquality below) is the primary
    # drift guard; these pin one concrete behavioral output per function in
    # the same shape as the parity tests above.

    def test_plugin_lib_sh_capped_matches_stowed_lib_sh(self):
        """_lib_capped must run a command under the same cap wrapper in both
        copies — its body is copied byte-identical from the stowed
        claude/.claude/hooks/_lib.sh."""
        harness = '. "{lib}"; _lib_capped echo hello; printf "RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout == "hello\nRC:0\n"

    def test_plugin_lib_sh_default_branch_from_origin_head_matches_stowed_lib_sh(
        self, tmp_path
    ):
        """_lib_default_branch_from_origin_head must resolve the same branch
        name from both copies for a repo with origin/HEAD set."""
        _bare, clone = bare_remote_with_default_branch(tmp_path)
        harness = '. "{lib}"; _lib_default_branch_from_origin_head "$1"; printf ":RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", str(clone)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", str(clone)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout == "main:RC:0\n"

    def test_plugin_lib_sh_default_branch_or_guess_matches_stowed_lib_sh(self, tmp_path):
        """_lib_default_branch_or_guess must fall back to the same candidate
        guess in both copies once origin/HEAD is unset — the branch this
        function adds over _lib_default_branch_from_origin_head above."""
        _bare, clone = bare_remote_with_default_branch(tmp_path)
        subprocess.run(
            ["git", "symbolic-ref", "--delete", "refs/remotes/origin/HEAD"],
            cwd=clone, check=True,
        )
        harness = '. "{lib}"; _lib_default_branch_or_guess "$1"; printf ":RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", str(clone)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", str(clone)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout == "main:RC:0\n"

    def test_plugin_lib_sh_git_inprogress_state_matches_stowed_lib_sh(self, git_repo):
        """_lib_git_inprogress_state must detect the same in-progress state
        from both copies for the same repo."""
        build_conflicted_cherry_pick(git_repo)
        harness = '. "{lib}"; _lib_git_inprogress_state "$1"; printf ":RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", str(git_repo)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", str(git_repo)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout == "cherry-pick:RC:0\n"

    def test_plugin_lib_sh_gate_diff_base_matches_stowed_lib_sh(self, git_repo):
        """_lib_gate_diff_base must agree on the correct, common-case answer
        from both copies: outside any in-progress state, no base at all."""
        harness = '. "{lib}"; _lib_gate_diff_base "$1"; printf ":RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", str(git_repo)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", str(git_repo)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout == ":RC:1\n"

    def test_plugin_lib_sh_staged_diff_hash_matches_stowed_lib_sh(self, git_repo):
        """_lib_staged_diff_hash must hash git_repo's staged change to the
        same digest from both copies."""
        harness = '. "{lib}"; _lib_staged_diff_hash "$1" ""; printf ":RC:%s\\n" "$?"'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB), "_", str(git_repo)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB), "_", str(git_repo)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout == stowed_result.stdout
        assert plugin_result.stdout.endswith(":RC:0\n")
        assert len(plugin_result.stdout.split(":RC:")[0]) == 64, plugin_result.stdout


class TestSharedGateDiffBaseClosureDefinitionEquality:
    """declare -f definition-equality over the seven functions this
    plugin's trimmed _lib.sh copies byte-identical from the stowed
    claude/.claude/hooks/_lib.sh, standing rather than introduction-time —
    the primary guard against the two copies drifting apart. Each lib is
    sourced in its own bash -c subshell — sourcing both into one shell
    would let the second definition clobber the first and turn the
    comparison into a function against itself."""

    @pytest.mark.parametrize(
        "function_name",
        [
            "_lib_capped",
            "_lib_default_branch_from_origin_head",
            "_lib_default_branch_or_guess",
            "_lib_git_inprogress_state",
            "_lib_gate_diff_base",
            "_lib_staged_diff_hash",
            "_lib_skill_review_diff_base",
        ],
    )
    def test_definition_matches_stowed_lib_sh(self, function_name):
        harness = '. "{lib}" >/dev/null 2>&1; declare -f {fn}'
        plugin_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_PLUGIN_LIB, fn=function_name)],
            capture_output=True, text=True, check=False,
        )
        stowed_result = subprocess.run(
            ["bash", "-c", harness.format(lib=_STOWED_LIB, fn=function_name)],
            capture_output=True, text=True, check=False,
        )
        assert plugin_result.stdout, f"{function_name} not defined in the plugin's _lib.sh"
        assert plugin_result.stdout == stowed_result.stdout, (
            f"plugins/skill-management/hooks/_lib.sh's {function_name} definition "
            "diverges from the stowed claude/.claude/hooks/_lib.sh copy"
        )


def _build_conflicted_merge_via_origin(tmp_path):
    """Local copy of test_marker_script.py's fixture of the same name (DAMP
    test code): a conflicted merge whose MERGE_HEAD is trusted via the
    origin/<default> anchor, resolved and staged."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    (clone / "f").write_text("ours-edit\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits f"], cwd=clone, check=True)
    push_conflicting_edit_to_origin(tmp_path, bare, "f", "origin-edit\n")
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    result = subprocess.run(
        ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    (clone / "f").write_text("resolved\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    return clone


def _merge_tree_base(repo):
    """Local copy of test_marker_script.py's helper of the same name:
    independently computes the reference tree _lib_gate_diff_base's merge
    row computes. The literal MERGE_HEAD OID (not the ref name) is passed --
    git embeds a merge-tree argument's own textual form into the conflict
    marker label, so the ref name would compute a byte-different tree than
    production's `merge-tree --write-tree HEAD "$state_oid"`."""
    merge_head_oid = (absolute_git_dir(repo) / "MERGE_HEAD").read_text().strip()
    out = subprocess.run(
        ["git", "merge-tree", "--write-tree", "HEAD", merge_head_oid],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return out.strip().splitlines()[0]


def _run_lib_fn(lib_path, function_name, *args, env=None):
    """Source `lib_path` and call `function_name` with `args`, returning the
    CompletedProcess — the shared subprocess shape the residual tests below
    use to drive both copies identically."""
    harness = f'. "{lib_path}"; {function_name} "$@"'
    return subprocess.run(
        ["bash", "-c", harness, "_", *args],
        capture_output=True, text=True, check=False,
        env=env if env is not None else dict(os.environ),
    )


class TestSharedGateDiffBaseClosureResidual:
    """Four residual cases the declare -f definition-equality above can't
    reach: identical bodies prove identity for every input, but two seams
    sit outside that proof — each lib's own `_lib_capped_for` dependency,
    and the plugin lib actually sourcing cleanly with the functions
    callable from it. (Reaching them from a real hook run is exercised by
    TestSkillReviewGateMergeAwareVerdict, not pinned here.) Three further cases pin
    `_lib_skill_review_diff_base`'s own wrapper behavior — exit-status
    propagation and pass-through — rather than a closure residual. Each
    case here asserts both copies' exit status and stdout against an
    independently computed expectation, never mutual agreement alone."""

    def test_via_origin_anchored_merge_matches_independent_merge_tree_oracle(
        self, tmp_path
    ):
        repo = _build_conflicted_merge_via_origin(tmp_path)
        expected_tree = _merge_tree_base(repo)
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            result = _run_lib_fn(lib, "_lib_gate_diff_base", str(repo))
            assert result.returncode == 0, f"{lib}: {result.stderr}"
            assert result.stdout == expected_tree, (
                f"{lib}'s _lib_gate_diff_base returned {result.stdout!r}, expected "
                f"the independent merge-tree oracle {expected_tree!r}"
            )

    def test_head_anchored_revert_excluded_only_by_the_wrapper(self, git_repo):
        """Pins the revert exclusion as living in _lib_skill_review_diff_base,
        not in the copied _lib_gate_diff_base closure: the same fixture must
        return a real tree from one and 1/empty from the other, in both
        libs."""
        build_conflicted_revert(git_repo)
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            gate_result = _run_lib_fn(lib, "_lib_gate_diff_base", str(git_repo))
            assert gate_result.returncode == 0, f"{lib}: {gate_result.stderr}"
            assert gate_result.stdout != "", (
                f"{lib}'s _lib_gate_diff_base returned no tree for a HEAD-anchored revert"
            )
            wrapper_result = _run_lib_fn(lib, "_lib_skill_review_diff_base", str(git_repo))
            assert wrapper_result.returncode == 1, f"{lib}: {wrapper_result.stdout!r}"
            assert wrapper_result.stdout == "", (
                f"{lib}'s _lib_skill_review_diff_base leaked a base during a revert"
            )

    def test_unanchored_local_branch_merge_both_exit_1_empty(self, git_repo):
        build_conflicted_merge(git_repo)  # local "theirs" branch, never pushed: untrusted
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            result = _run_lib_fn(lib, "_lib_gate_diff_base", str(git_repo))
            assert result.returncode == 1, f"{lib}: rc={result.returncode} stdout={result.stdout!r}"
            assert result.stdout == ""

    def test_exit_status_shim_both_copies_report_undetermined(self, git_repo, tmp_path):
        """A capped call reporting a cap-kill status without waiting for the
        cap must read as undetermined (exit 2), never as "no override" —
        pinned identically for both copies via the promoted exit-status git
        shim rather than a real cap firing (no new test may depend on a cap
        actually firing)."""
        import shutil

        build_conflicted_revert(git_repo)
        bin_dir = tmp_path / "bin-exiting-with-status"
        _make_git_exiting_with_status(bin_dir, "merge-tree", 137)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            result = _run_lib_fn(lib, "_lib_gate_diff_base", str(git_repo), env=env)
            assert result.returncode == 2, f"{lib}: rc={result.returncode} stdout={result.stdout!r}"
            assert result.stdout == ""

    def test_exit_status_shim_wrapper_propagates_undetermined(self, tmp_path):
        """Pins _lib_skill_review_diff_base's own
        `[ "$base_status" -eq 0 ] || return "$base_status"` propagation
        line: a future edit changing that to `return 1` would silently turn
        "undetermined, fail closed" into "no in-progress state". Uses the
        anchored-merge fixture, not build_conflicted_revert: a revert state
        is excluded at the wrapper's own pre-sample before
        _lib_gate_diff_base -- and therefore before the merge-tree cap-kill
        below -- is ever reached, so it cannot exercise this propagation
        line. An anchored merge falls through the pre-sample unexcluded and
        reaches the same cap-kill inside _lib_gate_diff_base."""
        import shutil

        repo = _build_conflicted_merge_via_origin(tmp_path)
        bin_dir = tmp_path / "bin-exiting-with-status"
        _make_git_exiting_with_status(bin_dir, "merge-tree", 137)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            wrapper_result = _run_lib_fn(
                lib, "_lib_skill_review_diff_base", str(repo), env=env
            )
            assert wrapper_result.returncode == 2, (
                f"{lib}: rc={wrapper_result.returncode} stdout={wrapper_result.stdout!r}"
            )
            assert wrapper_result.stdout == ""

    def test_wrapper_hot_path_matches_gate_diff_base_outside_any_state(self, git_repo):
        """Outside any in-progress state -- the overwhelming common case --
        the wrapper returns _lib_gate_diff_base's own answer (1, empty)
        without needing a second, in-progress-state-only probe."""
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            result = _run_lib_fn(lib, "_lib_skill_review_diff_base", str(git_repo))
            assert result.returncode == 1, f"{lib}: rc={result.returncode} stdout={result.stdout!r}"
            assert result.stdout == ""

    def test_wrapper_passes_through_unchanged_outside_revert(self, tmp_path):
        """The revert exclusion is the wrapper's only deviation from
        _lib_gate_diff_base -- an anchored merge must pass through
        unchanged in both libs, proving the wrapper doesn't also touch the
        states it isn't meant to."""
        repo = _build_conflicted_merge_via_origin(tmp_path)
        for lib in (_PLUGIN_LIB, _STOWED_LIB):
            gate_result = _run_lib_fn(lib, "_lib_gate_diff_base", str(repo))
            wrapper_result = _run_lib_fn(lib, "_lib_skill_review_diff_base", str(repo))
            assert wrapper_result.returncode == gate_result.returncode == 0, (
                f"{lib}: gate rc={gate_result.returncode} wrapper rc={wrapper_result.returncode}"
            )
            assert wrapper_result.stdout == gate_result.stdout != "", (
                f"{lib}: wrapper diverged from _lib_gate_diff_base outside a revert"
            )


def _three_commit_stateless_repo(tmp_path):
    """A plain repo, no in-progress git state, with three linear commits --
    HEAD~1 is both an ancestor of HEAD and has its own parent, the OID
    shape the double-flip residual test below needs for a REVERT_HEAD
    stand-in."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    for message in ("commit one", "commit two", "commit three"):
        (repo / "f").write_text(message + "\n")
        subprocess.run(["git", "add", "f"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return repo


class TestSkillReviewDiffBaseRevertBracket:
    """Pins _lib_skill_review_diff_base's bracket shape (pre-sample, call
    _lib_gate_diff_base unchanged, post-sample) against the trailing-only
    single-probe design it replaces, and documents the one residual the
    bracket does not close. Stowed lib only: the declare -f
    definition-equality in TestSharedGateDiffBaseClosureDefinitionEquality already carries this function's behavior to the
    plugin copy."""

    def test_wrong_arity_returns_could_not_determine(self):
        result = _run_lib_fn(_STOWED_LIB, "_lib_skill_review_diff_base")
        assert result.returncode == 2
        assert result.stdout == ""

    def test_pre_sample_short_circuits_before_any_base_computation_work(
        self, git_repo, tmp_path
    ):
        """Mid-revert, the wrapper must return before _lib_gate_diff_base
        spawns any git process of its own -- not merely without a
        merge-tree call, which a regression that enters _lib_gate_diff_base
        and exits early for an unrelated reason (a failed trust-anchor
        check, say) would also satisfy. Asserted against the whole argv
        log, not the absence of merge-tree alone."""
        import shutil

        build_conflicted_revert(git_repo)
        bin_dir = tmp_path / "bin-recording-argv"
        log_file = tmp_path / "argv.log"
        _make_git_recording_argv(bin_dir, log_file)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _run_lib_fn(
            _STOWED_LIB, "_lib_skill_review_diff_base", str(git_repo), env=env
        )
        assert result.returncode == 1, f"rc={result.returncode} stdout={result.stdout!r}"
        assert result.stdout == ""
        log_lines = log_file.read_text().splitlines()
        assert log_lines == [f"-C {git_repo} rev-parse --absolute-git-dir"], (
            f"expected only the wrapper's own gitdir resolution, got {log_lines!r}"
        )

    def test_pre_sample_regression_guard_against_trailing_only_probe(
        self, git_repo, tmp_path
    ):
        """The verdict-level counterpart to the short-circuit test above,
        and the one that goes red -- rather than merely slower -- if the
        pre-sample is dropped for a trailing-only probe. The shim deletes
        REVERT_HEAD the moment _lib_gate_diff_base would first read the
        state OID's trust anchor (merge-base --is-ancestor, the first git
        call after its own internal state probe and ref read); if the
        pre-sample never entered _lib_gate_diff_base at all, that trigger
        never fires and REVERT_HEAD survives untouched. With the pre-sample
        removed, the internal probe would read revert, the subtraction tree
        would be built from the already-read ref, the shim's deletion would
        land, and a trailing-only probe would then read no state -- printing
        that tree instead of excluding it, the pre-fix bug verbatim."""
        import shutil

        build_conflicted_revert(git_repo)
        gitdir = absolute_git_dir(git_repo)
        bin_dir = tmp_path / "bin-recording-argv"
        log_file = tmp_path / "argv.log"
        action = (
            'case "$*" in\n'
            '  *"merge-base --is-ancestor"*)\n'
            '    if [ "$(grep -c "merge-base --is-ancestor" "$LOG_FILE")" -eq 1 ]; then\n'
            f'      rm -f "{gitdir}/REVERT_HEAD"\n'
            '    fi\n'
            '    ;;\n'
            'esac'
        )
        _make_git_recording_argv(bin_dir, log_file, action=action)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _run_lib_fn(
            _STOWED_LIB, "_lib_skill_review_diff_base", str(git_repo), env=env
        )
        assert result.returncode == 1, f"rc={result.returncode} stdout={result.stdout!r}"
        assert result.stdout == ""
        assert (gitdir / "REVERT_HEAD").exists(), (
            "REVERT_HEAD was deleted -- the trailing-only probe's trigger fired, "
            "meaning the pre-sample never short-circuited before "
            "_lib_gate_diff_base was entered"
        )
        log_lines = log_file.read_text().splitlines()
        assert not any("merge-tree" in line for line in log_lines), (
            f"_lib_gate_diff_base spawned merge-tree: {log_lines!r}"
        )

    def test_stateless_double_flip_is_pinned_closed_by_the_no_state_early_out(
        self, tmp_path
    ):
        """Pins the pre-sample's no-state early-out: from a
        stateless fixture, a shim that plants a REVERT_HEAD-shaped OID on
        the second `rev-parse --absolute-git-dir` call
        (_lib_gate_diff_base's own, the wrapper's being the first) and
        deletes it again on the first `merge-base --is-ancestor` call must
        never fire its plant, because the pre-sample reads no state and
        returns before _lib_gate_diff_base resolves its own gitdir. The
        whole-log assertion is what makes this a pin rather than a weaker
        "no merge-tree call" check: with the early-out removed, this exact
        fixture reproduces the stateless double flip verbatim and the
        wrapper would exit 0 with the subtraction tree instead."""
        import shutil

        repo = _three_commit_stateless_repo(tmp_path)
        planted_oid = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        gitdir = absolute_git_dir(repo)
        bin_dir = tmp_path / "bin-recording-argv"
        log_file = tmp_path / "argv.log"
        action = (
            'case "$*" in\n'
            '  *"rev-parse --absolute-git-dir"*)\n'
            '    if [ "$(grep -c "rev-parse --absolute-git-dir" "$LOG_FILE")" -eq 2 ]; then\n'
            f'      printf "%s\\n" "{planted_oid}" > "{gitdir}/REVERT_HEAD"\n'
            '    fi\n'
            '    ;;\n'
            '  *"merge-base --is-ancestor"*)\n'
            '    if [ "$(grep -c "merge-base --is-ancestor" "$LOG_FILE")" -eq 1 ]; then\n'
            f'      rm -f "{gitdir}/REVERT_HEAD"\n'
            '    fi\n'
            '    ;;\n'
            'esac'
        )
        _make_git_recording_argv(bin_dir, log_file, action=action)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _run_lib_fn(
            _STOWED_LIB, "_lib_skill_review_diff_base", str(repo), env=env
        )

        assert result.returncode == 1, (
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == ""
        log_lines = log_file.read_text().splitlines()
        assert log_lines == [f"-C {repo} rev-parse --absolute-git-dir"], (
            f"expected only the wrapper's own gitdir resolution, got {log_lines!r}"
        )

    def test_in_state_double_flip_residual_is_disclosed_not_closed(self, tmp_path):
        """The disclosed residual, stated in executable form -- not a
        pin on the fix. A revert that both starts and ends inside the
        bracket's own window, while a union state (merge, cherry-pick or
        rebase) was already in progress at the pre-sample, still yields the
        subtraction tree. A later tightening that closes this shows up as a
        deliberate change to this test, not a silent break.

        From the stateless fixture plus a planted MERGE_HEAD (so the
        pre-sample reads `merge`, not `revert`, and falls through), the shim
        deletes MERGE_HEAD and then writes a REVERT_HEAD-shaped OID on the
        second `rev-parse --absolute-git-dir` call (_lib_gate_diff_base's
        own internal probe) -- in that order, since revert is last in
        _lib_git_inprogress_state's precedence and a surviving MERGE_HEAD
        would make that probe read `merge` instead -- and deletes
        REVERT_HEAD again on the first `merge-base --is-ancestor` call, so
        the wrapper's own post-sample also reads no state. A conflict-free
        revert needing no timing, and a committer able to write into the
        gitdir able to forge a state file outright, both dominate this
        residual -- which is why it is disclosed rather than closed. If the
        ordering assumption this test relies on ever proves
        unconstructible, delete this test rather than adding more shim
        machinery to force it."""
        import shutil

        repo = _three_commit_stateless_repo(tmp_path)
        planted_oid = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        gitdir = absolute_git_dir(repo)
        (gitdir / "MERGE_HEAD").write_text(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout
        )
        bin_dir = tmp_path / "bin-recording-argv"
        log_file = tmp_path / "argv.log"
        action = (
            'case "$*" in\n'
            '  *"rev-parse --absolute-git-dir"*)\n'
            '    if [ "$(grep -c "rev-parse --absolute-git-dir" "$LOG_FILE")" -eq 2 ]; then\n'
            f'      rm -f "{gitdir}/MERGE_HEAD"\n'
            f'      printf "%s\\n" "{planted_oid}" > "{gitdir}/REVERT_HEAD"\n'
            '    fi\n'
            '    ;;\n'
            '  *"merge-base --is-ancestor"*)\n'
            '    if [ "$(grep -c "merge-base --is-ancestor" "$LOG_FILE")" -eq 1 ]; then\n'
            f'      rm -f "{gitdir}/REVERT_HEAD"\n'
            '    fi\n'
            '    ;;\n'
            'esac'
        )
        _make_git_recording_argv(bin_dir, log_file, action=action)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _run_lib_fn(
            _STOWED_LIB, "_lib_skill_review_diff_base", str(repo), env=env
        )

        expected_tree = subprocess.run(
            [
                "git", "merge-tree", "--write-tree",
                f"--merge-base={planted_oid}", "HEAD", f"{planted_oid}^",
            ],
            cwd=repo, capture_output=True, text=True, check=False,
        ).stdout.strip().splitlines()[0]

        assert result.returncode == 0, (
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == expected_tree, (
            f"expected the independent merge-tree oracle {expected_tree!r}, "
            f"got {result.stdout!r}"
        )

    def test_post_sample_independently_excludes_a_revert_that_persists(
        self, tmp_path
    ):
        """Regression guard for the post-sample's own check, distinct from
        the pre-sample and from the in-state double flip's re-deletion:
        REVERT_HEAD appears via the shim on _lib_gate_diff_base's internal
        probe and is never removed, so it is still present when the
        wrapper's own post-sample runs afterward. Proves the post-sample
        independently excludes a revert that appeared after the pre-sample
        and persists through it -- deleting the post-sample block entirely
        would leak the subtraction tree here even though every other
        wrapper-bracket test still passes."""
        import shutil

        repo = _three_commit_stateless_repo(tmp_path)
        planted_oid = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        gitdir = absolute_git_dir(repo)
        (gitdir / "MERGE_HEAD").write_text(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout
        )
        bin_dir = tmp_path / "bin-recording-argv"
        log_file = tmp_path / "argv.log"
        action = (
            'case "$*" in\n'
            '  *"rev-parse --absolute-git-dir"*)\n'
            '    if [ "$(grep -c "rev-parse --absolute-git-dir" "$LOG_FILE")" -eq 2 ]; then\n'
            f'      rm -f "{gitdir}/MERGE_HEAD"\n'
            f'      printf "%s\\n" "{planted_oid}" > "{gitdir}/REVERT_HEAD"\n'
            '    fi\n'
            '    ;;\n'
            'esac'
        )
        _make_git_recording_argv(bin_dir, log_file, action=action)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        result = _run_lib_fn(
            _STOWED_LIB, "_lib_skill_review_diff_base", str(repo), env=env
        )

        assert result.returncode == 1, (
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stdout == ""


class TestRequireSkillReviewHonorsConfigDir:
    """CLAUDE_CONFIG_DIR relocates the skill-review marker directory the same
    way for marker.sh (write) and this hook (read) -- see marker.sh and the
    cross-account bypass this closes.

    Also the regression guard for a sessions_dir/config_dir mismatch bug in
    helpers.py's write_skill_review_marker: if that bug recurs, marker.sh write fails and these tests surface it as an
    unlabeled subprocess.CalledProcessError from write_skill_review_marker's
    `check=True` rather than a named assertion failure here."""

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

    def test_write_skill_review_marker_seeds_session_under_config_dir_not_home(
        self, isolated_home, git_repo, tmp_path
    ):
        """Direct unit check for the sessions_dir/config_dir invariant this
        class's docstring names: write_skill_review_marker(config_dir=...)
        must seed its session-seed file under <config_dir>/sessions/<pid>,
        not under home/.claude/sessions/<pid>. A regression here is exactly
        the bug the docstring above says surfaces as an unlabeled
        subprocess.CalledProcessError rather than a named assertion."""
        profile = tmp_path / "profile"
        _stage_skill_change(git_repo)
        write_skill_review_marker(isolated_home, git_repo, config_dir=profile)
        pid = os.getpid()
        assert (profile / "sessions" / str(pid)).exists(), (
            "write_skill_review_marker(config_dir=...) must seed the session-seed "
            f"file under config_dir's sessions/ dir ({profile / 'sessions' / str(pid)}); "
            "sessions_dir must track config_dir, not home, when config_dir is passed"
        )
        assert not (isolated_home / ".claude" / "sessions" / str(pid)).exists(), (
            "write_skill_review_marker(config_dir=...) must not also seed the "
            "session-seed file under home/.claude/sessions/ -- that would be "
            "the sessions_dir/config_dir mismatch this class's docstring warns about"
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
        skill_file = git_repo / "claude-skills" / "skills" / f"corpus-skill-{i}" / "SKILL.md"
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


def _stage_oversized_project_layer_corpus(
    git_repo, num_skills: int = 6, chars_each: int = 1500
) -> None:
    """Stage multiple project-layer SKILL.md files (.claude/skills/**/SKILL.md)
    whose combined description chars exceed the 8000-char budget, with no
    stowed or plugin-scoped skill staged alongside them. `chars_each` stays
    under the 1536-char per-skill structural-validator cap so only the
    corpus-budget check (not the structural validator) fires."""
    for i in range(num_skills):
        skill_file = git_repo / ".claude" / "skills" / f"corpus-skill-{i}" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        description = "x" * chars_each
        skill_file.write_text(f"---\ndescription: {description!r}\n---\n# body\n")
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

    def test_project_layer_skills_alone_count_toward_corpus_budget(
        self, isolated_home, git_repo
    ):
        """Project-layer SKILL.md files (.claude/skills/**/SKILL.md) are
        included in the corpus-budget pathspec, not just stowed and
        plugin-scoped skills."""
        _stage_oversized_project_layer_corpus(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        assert result.returncode == 0 and not result.stdout.strip(), (
            f"unexpected deny: {result.stdout}"
        )
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

    def test_corpus_scan_allows_commit_when_timeout_binaries_absent(
        self, isolated_home, git_repo, tmp_path
    ):
        """Without timeout(1) or gtimeout(1) on PATH, _lib_capped_for's
        uncapped fallback still runs the corpus scan to completion rather than
        failing with "command not found" (exit 127) -- the commit must still
        be allowed, mirroring the structural validator's sibling test above."""
        import shutil

        bin_dir = tmp_path / "bin-without-timeout"
        bin_dir.mkdir()
        for cmd in ("git", "jq", "sha256sum", "awk", "grep", "mktemp", "dirname", "mkdir", "rm", "cat", "python3"):
            cmd_path = shutil.which(cmd)
            if not cmd_path:
                pytest.skip(f"{cmd} not found in PATH")
            (bin_dir / cmd).symlink_to(cmd_path)

        _stage_oversized_corpus(git_repo)
        write_skill_review_marker(isolated_home, git_repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"PATH": str(bin_dir)},
            )
            == "allow"
        )


def _build_upstream_skill_edit_merge_in_linked_worktree(tmp_path):
    """Linked-worktree variant of
    build_conflicted_merge_via_origin_with_upstream_skill_edit (helpers.py):
    the local commit, the fetch/merge, and the resolution all happen inside
    a linked worktree rather than the main clone. Duplicated rather than
    adding a workdir parameter to the shared helper --
    push_conflicting_edit_to_origin only rewrites one file per call, and
    this fixture's upstream commit edits two (the conflict file and
    SKILL.md) in the same commit, so the push side can't be expressed
    through that shared helper either way.

    The worktree's own local branch is named "wt-branch" because "main" is
    already checked out in `clone`. Every push below still targets origin's
    "main" ref (`wt-branch:main`), not a same-named "wt-branch" ref on
    origin, because _lib_default_branch_or_guess resolves "main" as the
    default branch (bare_remote_with_default_branch's own origin/HEAD) and
    the anchor check needs content reachable from origin/main specifically."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    worktree = tmp_path / "linked-worktree-upstream-skill-edit"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "wt-branch", str(worktree)], cwd=clone, check=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=worktree, check=True)

    skill_rel_path = "claude-skills/skills/example-skill/SKILL.md"
    skill_path = worktree / skill_rel_path
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("base skill\n")
    subprocess.run(["git", "add", skill_rel_path], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "seed SKILL.md"], cwd=worktree, check=True)
    subprocess.run(["git", "push", "-q", "origin", "wt-branch:main"], cwd=worktree, check=True)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=worktree, check=True)

    (worktree / "f").write_text("ours-edit\n")
    subprocess.run(["git", "add", "f"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits f"], cwd=worktree, check=True)

    push_clone = tmp_path / "_push_upstream_skill_edit_linked_worktree"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
    (push_clone / "f").write_text("origin-edit\n")
    (push_clone / skill_rel_path).write_text("upstream edited skill\n")
    subprocess.run(["git", "add", "f", skill_rel_path], cwd=push_clone, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "origin edits f and SKILL.md"], cwd=push_clone, check=True
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=push_clone, check=True)

    subprocess.run(["git", "fetch", "-q", "origin"], cwd=worktree, check=True)
    result = subprocess.run(
        ["git", "merge", "-q", "origin/main"], cwd=worktree, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(worktree) / "MERGE_HEAD").exists()
    (worktree / "f").write_text("resolved\n")
    subprocess.run(["git", "add", "f"], cwd=worktree, check=True)
    return worktree


def _build_armed_fixture(tmp_path):
    """The armed fixture: gated
    file Y (claude-skills/skills/untouched-skill/SKILL.md) arrives
    untouched from upstream, so its base-relative diff is empty, while the
    resolution adds a different gated file X
    (claude-skills/skills/resolved-skill/SKILL.md), entirely absent from
    both sides of the merge and so genuinely novel relative to the base --
    the HEAD-relative gated set is {Y, X} and the base-relative one is {X},
    provably differing. The conflict itself is engineered in an unrelated
    file ("f"), so MERGE_HEAD persists to a resolvable state."""
    repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(
        tmp_path, skill_name="untouched-skill"
    )
    x_dir = repo / "claude-skills" / "skills" / "resolved-skill"
    x_dir.mkdir(parents=True)
    (x_dir / "SKILL.md").write_text("---\nname: resolved-skill\ndescription: ok\n---\n# body\n")
    subprocess.run(
        ["git", "add", "claude-skills/skills/resolved-skill/SKILL.md"], cwd=repo, check=True
    )
    return repo


def _assert_armed_fixture_preimages_differ(repo, pathspecs=None):
    """Precondition every marker-behavior test on this fixture asserts:
    oracle(base="") != oracle(base)."""
    pathspecs = pathspecs or ()
    base = _merge_tree_base(repo)
    head_relative = staged_diff_hash_at_base(repo, "", *pathspecs)
    base_relative = staged_diff_hash_at_base(repo, base, *pathspecs)
    assert head_relative != base_relative, (
        "armed fixture is not actually armed: HEAD-relative and "
        "base-relative preimages are identical"
    )


_WELL_FORMED_SKILL_MD = "---\nname: clean\ndescription: ok\n---\n# body\n"
_BROKEN_SKILL_MD = "---\nname: broken\ndescription: [unclosed\n---\n# body\n"

_MARKER_PATHSPECS = (
    "claude-skills/skills/**/SKILL.md",
    "plugins/*/skills/**/SKILL.md",
    "skills/**/SKILL.md",
    ".claude/skills/**/SKILL.md",
    "claude-skills/skills/plan-review/ROUTING.md",
)


class TestSkillReviewGateMergeAwareVerdict:
    """The gate's verdict, end to end, mid-merge (GH-1076): an
    upstream-reviewed SKILL.md edit allows with no marker, plus the
    still-armed and marker-agreement proofs that pin it does not
    over-disarm."""

    @pytest.mark.parametrize("fixture_kind", ["plain_clone", "linked_worktree"])
    def test_upstream_skill_edit_mid_merge_allows_with_no_marker(
        self, isolated_home, tmp_path, fixture_kind
    ):
        """Upstream edits a gated SKILL.md the
        local branch never touched, the conflict is engineered in an
        unrelated file, and the commit that completes the resolved merge
        allows with no marker on disk. Three preconditions checked through
        primitives so this cannot pass vacuously. Parametrized over a plain
        clone and a linked worktree, since this repo enforces worktree
        discipline (CLAUDE.md's worktree-enforcement section) and
        contributors hit GH-1076 from inside one, not the main checkout."""
        repo = (
            build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
            if fixture_kind == "plain_clone"
            else _build_upstream_skill_edit_merge_in_linked_worktree(tmp_path)
        )

        # Precondition 1: the fixture reaches a trusted anchor.
        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
        assert base_result.returncode == 0 and base_result.stdout.strip(), (
            f"fixture did not reach a trusted anchor: rc={base_result.returncode} "
            f"stdout={base_result.stdout!r}"
        )
        # Precondition 2: the HEAD-relative gated set is non-empty -- a
        # HEAD-relative gate would fire here.
        head_relative_diff = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--",
             "claude-skills/skills/**/SKILL.md"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert head_relative_diff.strip(), "precondition failed: HEAD-relative gated set is empty"
        # Precondition 3: the base-relative one is empty.
        base_relative_diff = subprocess.run(
            ["git", "diff", "--cached", "--name-only", base_result.stdout.strip(), "--",
             "claude-skills/skills/**/SKILL.md"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert base_relative_diff.strip() == "", (
            "precondition failed: base-relative gated set is not empty"
        )

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="upstream-skill-edit-session"),
                cwd=repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "resolution", ["restore_head_blob", "remove_upstream_added_file"]
    )
    def test_resolution_discarding_upstream_gated_content_denies(
        self, isolated_home, tmp_path, resolution
    ):
        """Still armed where it must be: a resolution that discards
        upstream's gated SKILL.md content leaves the HEAD-relative gated set
        empty while the base-relative one is not, so a gate keyed on the
        HEAD-relative set alone would release it. Two arms: `git checkout
        HEAD --` restores an upstream-edited file to HEAD's blob, and
        `git rm` deletes an upstream-added file."""
        skill_rel = "claude-skills/skills/example-skill/SKILL.md"
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(
            tmp_path, upstream_adds_skill=(resolution == "remove_upstream_added_file")
        )
        if resolution == "restore_head_blob":
            subprocess.run(["git", "checkout", "HEAD", "--", skill_rel], cwd=repo, check=True)
        else:
            subprocess.run(["git", "rm", "-qf", skill_rel], cwd=repo, check=True)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
        assert base_result.returncode == 0 and base_result.stdout.strip(), (
            f"fixture did not reach a trusted anchor: rc={base_result.returncode} "
            f"stdout={base_result.stdout!r}"
        )
        for base_argument, expect_gated_names in (([], False), ([base_result.stdout.strip()], True)):
            gated_names = subprocess.run(
                ["git", "diff", "--cached", "--name-only", *base_argument, "--",
                 "claude-skills/skills/**/SKILL.md"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout.strip()
            assert bool(gated_names) is expect_gated_names, (
                f"precondition failed: gated set against base {base_argument or 'HEAD'} "
                f"is {gated_names!r}"
            )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="discarded-upstream-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_resolution_editing_a_second_skill_md_still_denies(self, isolated_home, tmp_path):
        """Still armed where it must be: a resolution that edits a gated
        SKILL.md (even one entirely separate from the untouched-upstream
        file) denies."""
        repo = _build_armed_fixture(tmp_path)
        _assert_armed_fixture_preimages_differ(repo, _MARKER_PATHSPECS)
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="still-armed-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_resolution_editing_routing_md_still_denies(self, isolated_home, tmp_path):
        """Still armed where it must be, ROUTING_DIFF arm: ROUTING_DIFF and
        SKILL_DIFF are separate variables feeding different downstream
        consumers, so this is checked independently of the SKILL.md arm
        above."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        routing_dir = repo / "claude-skills" / "skills" / "plan-review"
        routing_dir.mkdir(parents=True)
        (routing_dir / "ROUTING.md").write_text("# routing\n")
        subprocess.run(
            ["git", "add", "claude-skills/skills/plan-review/ROUTING.md"], cwd=repo, check=True
        )
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="still-armed-routing-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_revert_stays_armed_with_gated_removal(self, isolated_home, git_repo):
        """Mid-conflicted-revert, a gated SKILL.md removal that applied
        cleanly (the conflict is in an unrelated file) denies with the
        marker-gate reason. The removal equals the revert's own synthesized
        subtraction tree, so a hook that resolved its base through
        _lib_gate_diff_base rather than _lib_skill_review_diff_base would
        see an empty gated diff and allow; the preimages-differ
        precondition and the deny together pin the wrapper."""
        build_conflicted_revert_with_clean_gated_removal(git_repo)

        subtraction_base = revert_subtraction_base(git_repo)
        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(git_repo))
        assert base_result.returncode == 0 and base_result.stdout.strip() == subtraction_base, (
            "precondition failed: _lib_gate_diff_base did not return the subtraction tree mid-revert"
        )
        assert staged_diff_hash_at_base(
            git_repo, "", *_MARKER_PATHSPECS
        ) != staged_diff_hash_at_base(git_repo, subtraction_base, *_MARKER_PATHSPECS), (
            "precondition failed: HEAD-relative and subtraction-relative gated preimages are identical"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m revert", session_id="revert-armed-session"),
            cwd=git_repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_revert_gated_removal_allows_once_the_marker_is_written(
        self, isolated_home, git_repo
    ):
        """Allow path of the mid-revert deny above: the marker writer and the
        hook must hash the same HEAD-relative preimage, so a recorded marker
        releases the commit."""
        build_conflicted_revert_with_clean_gated_removal(git_repo)
        write_skill_review_marker(isolated_home, git_repo)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m revert", session_id="revert-allowed-session"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_a_cherry_pick_allow_deny_pair(self, isolated_home, tmp_path):
        """A cherry-pick allow/deny pair, since the hook's code past the
        base is state-agnostic and one non-merge state suffices to prove
        cherry-pick reaches the same disarm/arm machinery as merge."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        skill_rel = "claude-skills/skills/cherry-skill/SKILL.md"
        skill_path = clone / skill_rel
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("base skill\n")
        subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-qm", "seed skill"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)

        (clone / "local.txt").write_text("ours\n")
        subprocess.run(["git", "add", "local.txt"], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-qm", "local commit"], cwd=clone, check=True)

        push_clone = tmp_path / "_push_cherry_pick_skill_edit"
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
        )
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
        (push_clone / skill_rel).write_text("upstream edited skill\n")
        subprocess.run(["git", "add", skill_rel], cwd=push_clone, check=True)
        subprocess.run(["git", "commit", "-qm", "origin edits SKILL.md"], cwd=push_clone, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=push_clone, check=True)

        subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
        # Allow arm: `-e` with a failing GIT_EDITOR stops the pick after it
        # cleanly applies and stages the change, leaving CHERRY_PICK_HEAD in
        # place the way an interrupted pick does -- plain `-n` does NOT
        # leave CHERRY_PICK_HEAD behind on a clean, non-conflicting apply
        # (confirmed empirically), so it cannot anchor this fixture.
        pick_result = subprocess.run(
            ["git", "cherry-pick", "-e", "origin/main"],
            cwd=clone, capture_output=True, text=True,
            env={**os.environ, "GIT_EDITOR": "false"},
        )
        assert pick_result.returncode != 0, pick_result.stdout + pick_result.stderr
        assert (absolute_git_dir(clone) / "CHERRY_PICK_HEAD").exists()
        assert (clone / skill_rel).read_text() == "upstream edited skill\n"

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m cherry-pick-allow", session_id="cherry-pick-allow"),
                cwd=clone,
            )
            == "allow"
        )

        # Deny arm: same state, plus a second, genuinely novel gated file
        # added to the staged resolution.
        second_dir = clone / "claude-skills" / "skills" / "cherry-second"
        second_dir.mkdir(parents=True)
        (second_dir / "SKILL.md").write_text("# second\n")
        subprocess.run(
            ["git", "add", "claude-skills/skills/cherry-second/SKILL.md"], cwd=clone, check=True
        )
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m cherry-pick-deny", session_id="cherry-pick-deny"),
            cwd=clone,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason


class TestSkillReviewGateMarkerAgreement:
    """Write / read agreement on the armed fixture: a marker built from the
    base-relative oracle allows, one built from the HEAD-relative oracle
    denies, and outside any in-progress state a HEAD-relative marker allows."""

    def test_head_relative_marker_denies_mid_merge(self, isolated_home, tmp_path):
        repo = _build_armed_fixture(tmp_path)
        _assert_armed_fixture_preimages_differ(repo, _MARKER_PATHSPECS)
        head_relative_value = staged_diff_hash_at_base(repo, "", *_MARKER_PATHSPECS)
        marker = skill_review_marker_path(isolated_home, repo, session_id="head-relative-marker-session")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_relative_value + "\n")
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="head-relative-marker-session"),
                cwd=repo,
            )
            == "deny"
        )

    def test_base_relative_marker_allows_mid_merge(self, isolated_home, tmp_path):
        repo = _build_armed_fixture(tmp_path)
        _assert_armed_fixture_preimages_differ(repo, _MARKER_PATHSPECS)
        base = _merge_tree_base(repo)
        base_relative_value = staged_diff_hash_at_base(repo, base, *_MARKER_PATHSPECS)
        marker = skill_review_marker_path(isolated_home, repo, session_id="base-relative-marker-session")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(base_relative_value + "\n")
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="base-relative-marker-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_head_relative_marker_allows_outside_any_state(
        self, isolated_home, git_repo
    ):
        """Outside any trusted in-progress state BASE resolves empty, so a
        marker over the HEAD-relative gated diff matches."""
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


def _build_merge_with_malformed_untouched_upstream_skill_and_novel_well_formed_resolution(
    tmp_path,
):
    """Direction 1's armed fixture: gated file Y arrives malformed from
    upstream, untouched by the resolution (its base-relative diff must
    stay empty, so it cannot be malformed by editing it after the merge --
    the malformed content has to originate in the upstream commit itself);
    gated file X is well-formed and novel to the resolution."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    y_rel = "claude-skills/skills/malformed-untouched-skill/SKILL.md"
    y_path = clone / y_rel
    y_path.parent.mkdir(parents=True)
    y_path.write_text(_WELL_FORMED_SKILL_MD)
    subprocess.run(["git", "add", y_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "seed SKILL.md"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)

    (clone / "f").write_text("ours-edit\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits f"], cwd=clone, check=True)

    push_clone = tmp_path / "_push_malformed_untouched_skill"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
    (push_clone / "f").write_text("origin-edit\n")
    (push_clone / y_rel).write_text(_BROKEN_SKILL_MD)
    subprocess.run(["git", "add", "f", y_rel], cwd=push_clone, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "origin edits f and malforms SKILL.md"], cwd=push_clone, check=True
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=push_clone, check=True)

    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    result = subprocess.run(
        ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    (clone / "f").write_text("resolved\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)

    x_dir = clone / "claude-skills" / "skills" / "novel-well-formed-skill"
    x_dir.mkdir(parents=True)
    (x_dir / "SKILL.md").write_text(_WELL_FORMED_SKILL_MD)
    subprocess.run(
        ["git", "add", "claude-skills/skills/novel-well-formed-skill/SKILL.md"],
        cwd=clone, check=True,
    )
    return clone


class TestSkillReviewGateStructuralValidatorScoping:
    """The structural validator's input is scoped to the base-relative
    path list, both directions, non-vacuously."""

    def test_malformed_untouched_upstream_file_does_not_deny(self, isolated_home, tmp_path):
        """Direction 1: gated file Y arrives malformed from upstream and
        untouched by the resolution; gated file X is well-formed and novel
        to the resolution. With an oracle-seeded marker covering X's own
        base-relative content, the commit allows -- Y's malformed content
        never reaches the validator since it isn't in the base-relative
        path list, and it also can't reach the marker hash (which is also
        base-relative), so X's own marker is what has to authorize this."""
        repo = _build_merge_with_malformed_untouched_upstream_skill_and_novel_well_formed_resolution(
            tmp_path
        )
        base = _merge_tree_base(repo)
        marker = skill_review_marker_path(isolated_home, repo, session_id="malformed-y-session")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(staged_diff_hash_at_base(repo, base, *_MARKER_PATHSPECS) + "\n")

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="malformed-y-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_malformed_untouched_upstream_file_with_no_marker_denies_the_gate_not_the_validator(
        self, isolated_home, tmp_path
    ):
        """Same fixture, no marker: the deny still names the skill-review
        gate, not the structural validator -- proving Y's malformed
        content is excluded from the validator's input regardless of
        marker state."""
        repo = _build_merge_with_malformed_untouched_upstream_skill_and_novel_well_formed_resolution(
            tmp_path
        )
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="malformed-y-no-marker"),
            cwd=repo,
        )
        assert reason is not None
        assert _MARKER_GATE_TOKEN in reason
        assert "structural validator" not in reason

    def test_malformed_resolution_edit_denies_naming_only_that_file(
        self, isolated_home, tmp_path
    ):
        """Direction 2: the resolution also malforms a *different* gated
        file X; the deny names X's repo-relative path and does not name Y
        -- Y's absence is what distinguishes base-relative from
        HEAD-relative validator input."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        x_dir = repo / "claude-skills" / "skills" / "malformed-resolution-skill"
        x_dir.mkdir(parents=True)
        (x_dir / "SKILL.md").write_text(_BROKEN_SKILL_MD)
        subprocess.run(
            ["git", "add", "claude-skills/skills/malformed-resolution-skill/SKILL.md"],
            cwd=repo, check=True,
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="malformed-x-session"),
            cwd=repo,
        )
        assert reason is not None
        assert "structural validator" in reason
        assert "claude-skills/skills/malformed-resolution-skill/SKILL.md" in reason
        assert "claude-skills/skills/example-skill/SKILL.md" not in reason


class TestSkillReviewGateStatusVisibility:
    """Status 2 (the base could not be computed) is visible in the deny
    text and changes no verdict."""

    def test_undetermined_base_denies_and_names_it_mid_merge(self, isolated_home, tmp_path):
        import shutil

        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        x_dir = repo / "claude-skills" / "skills" / "undetermined-base-skill"
        x_dir.mkdir(parents=True)
        (x_dir / "SKILL.md").write_text("# novel\n")
        subprocess.run(
            ["git", "add", "claude-skills/skills/undetermined-base-skill/SKILL.md"],
            cwd=repo, check=True,
        )
        bin_dir = tmp_path / "bin-exiting-with-status"
        _make_git_exiting_with_status(bin_dir, "merge-tree", 137)
        extra_env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="undetermined-base-session"),
            cwd=repo,
            extra_env=extra_env,
        )
        assert reason is not None
        assert _MARKER_GATE_TOKEN in reason
        assert "gated against HEAD" in reason

    def test_undetermined_base_allows_with_head_relative_marker(self, isolated_home, tmp_path):
        import shutil

        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        x_dir = repo / "claude-skills" / "skills" / "undetermined-base-skill-allow"
        x_dir.mkdir(parents=True)
        (x_dir / "SKILL.md").write_text("# novel\n")
        subprocess.run(
            ["git", "add", "claude-skills/skills/undetermined-base-skill-allow/SKILL.md"],
            cwd=repo, check=True,
        )
        marker = skill_review_marker_path(
            isolated_home, repo, session_id="undetermined-base-allow-session"
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(staged_diff_hash_at_base(repo, "", *_MARKER_PATHSPECS) + "\n")

        bin_dir = tmp_path / "bin-exiting-with-status"
        _make_git_exiting_with_status(bin_dir, "merge-tree", 137)
        extra_env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
        }
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "git commit -m merge", session_id="undetermined-base-allow-session"
                ),
                cwd=repo,
                extra_env=extra_env,
            )
            == "allow"
        )


class TestSkillReviewGateNoCapBinaryAndHashFailure:
    """With neither timeout(1) nor gtimeout(1) on PATH, the
    base resolves uncapped and the disarm still fires. A hash
    computation failure denies, never allows."""

    def test_no_cap_binary_on_path_mid_merge_allows(self, isolated_home, tmp_path):
        import shutil

        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        bin_dir = tmp_path / "bin-without-timeout"
        bin_dir.mkdir()
        for cmd in (
            "git", "jq", "sha256sum", "awk", "grep", "mktemp", "dirname",
            "mkdir", "rm", "cat", "python3",
        ):
            cmd_path = shutil.which(cmd)
            if not cmd_path:
                pytest.skip(f"{cmd} not found in PATH")
            (bin_dir / cmd).symlink_to(cmd_path)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="no-cap-binary-session"),
                cwd=repo,
                extra_env={"PATH": str(bin_dir)},
            )
            == "allow"
        )

    def test_broken_sha256sum_denies_mid_merge(self, isolated_home, tmp_path):
        repo = _build_armed_fixture(tmp_path)
        bin_dir = tmp_path / "bin-broken-sha256sum"
        bin_dir.mkdir()
        broken = bin_dir / "sha256sum"
        broken.write_text("#!/bin/bash\nexit 1\n")
        broken.chmod(0o755)
        extra_env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="broken-sha256sum-session"),
            cwd=repo,
            extra_env=extra_env,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason


def _build_unpushed_local_branch_merge_with_untouched_gated_skill(
    tmp_path, skill_name="anchor-skill"
):
    """Same shape as build_conflicted_merge_via_origin_with_upstream_skill_edit
    (helpers.py) -- gated content arrives untouched by the resolution, the
    conflict is engineered in an unrelated file -- except "theirs" is a
    LOCAL branch that is never pushed. _lib_gate_diff_base's anchor check
    requires the in-progress state's OID to be an ancestor of
    origin/<default> or of HEAD; an unpushed local branch tip is neither,
    so this reaches no trusted anchor. If the anchor check were skipped,
    this fixture would disarm exactly like the trusted via-origin one --
    proving the allow in that sibling test depends on the anchor check,
    not merely on "the gate is off mid-merge"."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    skill_rel = f"claude-skills/skills/{skill_name}/SKILL.md"
    skill_path = clone / skill_rel
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("base skill\n")
    subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "seed skill"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)

    subprocess.run(["git", "checkout", "-qb", "theirs"], cwd=clone, check=True)
    (clone / "f").write_text("theirs-edit\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    skill_path.write_text("theirs edited skill\n")
    subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "theirs edits f and skill"], cwd=clone, check=True)

    subprocess.run(["git", "checkout", "-q", "main"], cwd=clone, check=True)
    (clone / "f").write_text("ours-edit\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits f"], cwd=clone, check=True)

    result = subprocess.run(
        ["git", "merge", "-q", "theirs"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    (clone / "f").write_text("resolved\n")
    subprocess.run(["git", "add", "f"], cwd=clone, check=True)
    return clone


class TestSkillReviewGateAnchorRejection:
    """Anchor rejection at verdict level: the deny counter to the allow
    proven by test_upstream_skill_edit_mid_merge_allows_with_no_marker,
    and the only place the closure-copy claim (that a forged or
    untrusted anchor cannot release the gate) is tested end to end rather
    than against the primitive directly."""

    def test_unpushed_local_branch_merge_denies_with_untouched_gated_skill(
        self, isolated_home, tmp_path
    ):
        repo = _build_unpushed_local_branch_merge_with_untouched_gated_skill(tmp_path)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
        assert base_result.returncode == 1 and base_result.stdout == "", (
            f"precondition failed: unpushed local-branch merge unexpectedly "
            f"reached a trusted anchor: rc={base_result.returncode} "
            f"stdout={base_result.stdout!r}"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="unpushed-branch-anchor-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_fabricated_remote_tracking_ref_denies_with_gated_content_staged(
        self, isolated_home, tmp_path
    ):
        """A fabricated refs/remotes/origin/<name> ref pointing at an
        unreachable commit must not anchor the gate --
        _lib_default_branch_or_guess's candidate probe is deliberately
        narrow (exactly main/master/develop) and never resolves to this
        name. Mirrors
        test_lib.py::TestGateDiffBaseUntrustedAnchor.test_reachable_only_from_fabricated_remote_tracking_ref_still_falls_back
        at verdict level, with genuinely staged gated content in the index
        so the deny proves the anchor rejection rather than an
        accidentally-empty diff."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        subprocess.run(["git", "checkout", "-qb", "side"], cwd=clone, check=True)
        skill_rel = "claude-skills/skills/fabricated-anchor-skill/SKILL.md"
        skill_path = clone / skill_rel
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("side content\n")
        subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-qm", "side commit"], cwd=clone, check=True)
        side_oid = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", "main"], cwd=clone, check=True)
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/totally-not-the-default", side_oid],
            cwd=clone,
            check=True,
        )
        (absolute_git_dir(clone) / "MERGE_HEAD").write_text(side_oid + "\n")
        _stage_skill_change(clone)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(clone))
        assert base_result.returncode == 1 and base_result.stdout == "", (
            f"precondition failed: fabricated remote-tracking ref unexpectedly "
            f"reached a trusted anchor: rc={base_result.returncode} "
            f"stdout={base_result.stdout!r}"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="fabricated-anchor-session"),
            cwd=clone,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    @pytest.mark.parametrize("shadow_kind", ["branch", "tag"])
    @pytest.mark.parametrize("has_remote_tracking_ref", [True, False])
    def test_local_ref_named_like_remote_tracking_ref_denies_with_gated_content_staged(
        self, isolated_home, tmp_path, shadow_kind, has_remote_tracking_ref
    ):
        """A local branch or tag named `origin/main` resolves ahead of the
        real remote-tracking ref under git's short-name rules, so an anchor
        spelled `origin/<default>` would trust a MERGE_HEAD pointing at an
        unreviewed commit. Mirrors
        test_lib.py::TestGateDiffBaseAnchorNamespaceShadow at verdict level."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "f").write_text("seed\n")
        subprocess.run(["git", "add", "f"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-qb", "side"], cwd=repo, check=True)
        # The side commit carries the exact skill content later staged on
        # main, so a trusted shadow anchor would read it as already reviewed
        # and disarm the gate.
        _stage_skill_change(repo)
        subprocess.run(["git", "commit", "-qm", "unreviewed skill commit"], cwd=repo, check=True)
        unreviewed_oid = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
        if has_remote_tracking_ref:
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True
            )
        subprocess.run(["git", shadow_kind, "origin/main", unreviewed_oid], cwd=repo, check=True)
        (absolute_git_dir(repo) / "MERGE_HEAD").write_text(unreviewed_oid + "\n")
        _stage_skill_change(repo)

        shadowed_oid = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert shadowed_oid == unreviewed_oid, "precondition: the short name must resolve to the shadow"
        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
        assert base_result.returncode == 1 and base_result.stdout == "", (
            f"precondition failed: shadow ref reached a trusted anchor: "
            f"rc={base_result.returncode} stdout={base_result.stdout!r}"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="shadow-anchor-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_non_hex_state_ref_denies_with_gated_content_staged(self, isolated_home, git_repo):
        """A state ref file is plain, unauthenticated content anyone with
        filesystem access could write directly. Writing the literal text
        "HEAD" -- syntactically a valid revision, trivially its own
        ancestor -- must not anchor the gate absent state_oid's own
        40/64-hex shape check. Mirrors
        test_lib.py::TestGateDiffBaseUntrustedAnchor.test_resolvable_non_hex_state_ref_falls_back
        at verdict level."""
        (absolute_git_dir(git_repo) / "MERGE_HEAD").write_text("HEAD\n")
        _stage_skill_change(git_repo)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(git_repo))
        assert base_result.returncode == 1 and base_result.stdout == "", (
            f"precondition failed: non-hex state ref unexpectedly reached a "
            f"trusted anchor: rc={base_result.returncode} stdout={base_result.stdout!r}"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="non-hex-anchor-session"),
            cwd=git_repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_octopus_merge_head_denies_with_gated_content_staged(self, isolated_home, git_repo):
        """A genuine two-line MERGE_HEAD, the shape a real octopus-merge
        attempt leaves, must not anchor the gate -- state_oid's shape
        validation rejects the multi-line value outright. Mirrors
        test_lib.py::TestGateDiffBaseTopologyFallback.test_octopus_merge_falls_back_to_empty_base
        at verdict level."""
        build_octopus_merge_conflict(git_repo)
        _stage_skill_change(git_repo)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(git_repo))
        assert base_result.returncode == 1 and base_result.stdout == "", (
            f"precondition failed: octopus MERGE_HEAD unexpectedly reached a "
            f"trusted anchor: rc={base_result.returncode} stdout={base_result.stdout!r}"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="octopus-anchor-session"),
            cwd=git_repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason


def _build_hand_forged_anchor_no_real_merge_skill_review(tmp_path, name="repo"):
    """Reproduces the forged-anchor residual with git-CLI-only forgery: no
    real `git merge`, no `commit-tree`, no object the actor didn't already
    have -- the same construction
    test_require_plan_review.py::_build_hand_forged_anchor_no_real_merge uses
    for the plan-review gate's own forged-anchor residual, adapted for a
    base-relative git-diff gate rather than an active-plan-set one.
    MERGE_HEAD and refs/remotes/origin/main are hand-written to the same
    sibling-branch commit OID, so "is $state_oid an ancestor of origin/main"
    holds trivially (a commit is its own ancestor) with no real merge, push,
    or fetch involved. The sibling commit edits only an unrelated file, so
    the forged base carries no gated content of its own -- the deny arm
    stages a genuinely novel gated SKILL.md edit on top of this same
    fixture."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f").write_text("seed\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "-q", "-b", "sibling"], cwd=repo, check=True)
    (repo / "f").write_text("sibling edits an unrelated file\n")
    subprocess.run(["git", "commit", "-qam", "sibling edits f"], cwd=repo, check=True)
    sibling_oid = subprocess.run(
        ["git", "rev-parse", "sibling"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    (absolute_git_dir(repo) / "MERGE_HEAD").write_text(sibling_oid + "\n")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", sibling_oid], cwd=repo, check=True
    )
    return repo


class TestSkillReviewGateForgedAnchorResidual:
    """The accepted forged-anchor residual, pinned both ways in
    test_require_plan_review.py::test_hand_forged_anchor_no_real_merge_allows_unrelated_write's
    style: the same forged anchor allows absent any gated content, and
    denies the moment gated content is genuinely staged, so a later
    tightening or loosening of the residual shows up as a visible test
    change rather than a silent one."""

    def test_hand_forged_anchor_no_real_merge_no_gated_content_allows(
        self, isolated_home, tmp_path
    ):
        repo = _build_hand_forged_anchor_no_real_merge_skill_review(tmp_path)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
        assert base_result.returncode == 0 and base_result.stdout.strip(), (
            f"precondition failed: forged anchor did not reach a trusted "
            f"anchor: rc={base_result.returncode} stdout={base_result.stdout!r}"
        )

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="forged-anchor-allow-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_hand_forged_anchor_no_real_merge_with_gated_edit_still_denies(
        self, isolated_home, tmp_path
    ):
        """Guards against the allow above passing only because the gate is
        simply off for this fixture's state: a gated SKILL.md staged on top
        of the same forged anchor is novel relative to it and still demands
        a review."""
        repo = _build_hand_forged_anchor_no_real_merge_skill_review(tmp_path)
        _stage_skill_change(repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id="forged-anchor-deny-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason


def _build_conflicted_merge_via_origin_with_unresolved_skill_conflict(tmp_path):
    """The conflict IS the gated SKILL.md itself, left unresolved
    and unstaged (a `UU` index entry) rather than resolved and re-staged --
    proving gated_paths_at_base's `--name-only` listing still reports the
    gated path while it is an unmerged index entry, keeping the gate armed
    until it is resolved and staged. Unlike
    build_conflicted_merge_via_origin_with_upstream_skill_edit (helpers.py),
    whose conflict is engineered in an unrelated file so SKILL.md
    auto-merges cleanly, this fixture puts the conflict in SKILL.md
    itself."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    skill_rel = "claude-skills/skills/unresolved-conflict-skill/SKILL.md"
    skill_path = clone / skill_rel
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("base skill\n")
    subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "seed SKILL.md"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)

    skill_path.write_text("ours-edit\n")
    subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits SKILL.md"], cwd=clone, check=True)

    push_clone = tmp_path / "_push_unresolved_skill_conflict"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
    (push_clone / skill_rel).write_text("origin-edit\n")
    subprocess.run(["git", "add", skill_rel], cwd=push_clone, check=True)
    subprocess.run(["git", "commit", "-qm", "origin edits SKILL.md"], cwd=push_clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=push_clone, check=True)

    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    result = subprocess.run(
        ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    # Deliberately left unresolved and unstaged -- the point of this fixture.
    return clone


class TestSkillReviewGateUnmergedGatedConflictStaysArmed:
    """Pinned at two layers: the git-primitive fact that
    `--name-only` still lists an unmerged path against a tree argument (not
    just bare HEAD), and the hook-verdict consequence -- a conflicted gated
    file that is still unmerged and unstaged keeps the gate armed."""

    def test_unmerged_gated_path_still_lists_under_name_only_against_a_tree(self, tmp_path):
        """Unit layer, modeled on
        test_lib.py::test_git_diff_cached_against_unresolved_conflict_git_primitive_fact:
        gated_paths_at_base's own invocation shape (`--name-only`, a tree
        argument, a pathspec) still reports an unmerged path, so a
        conflicted SKILL.md is not silently dropped from the base-relative
        listing merely because it carries a tree argument."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        skill_rel = "claude-skills/skills/conflicted-skill/SKILL.md"
        skill_path = repo / skill_rel
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("base\n")
        subprocess.run(["git", "add", skill_rel], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
        base_oid = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        subprocess.run(["git", "checkout", "-qb", "theirs"], cwd=repo, check=True)
        skill_path.write_text("theirs\n")
        subprocess.run(["git", "commit", "-qam", "theirs edits skill"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
        skill_path.write_text("ours\n")
        subprocess.run(["git", "commit", "-qam", "ours edits skill"], cwd=repo, check=True)
        result = subprocess.run(
            ["git", "merge", "-q", "theirs"], cwd=repo, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert (absolute_git_dir(repo) / "MERGE_HEAD").exists()

        listing = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--name-only", base_oid, "--", skill_rel],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert skill_rel in listing.splitlines()

    def test_unmerged_gated_conflict_stays_armed_and_denies(self, isolated_home, tmp_path):
        """Verdict layer: mid-conflicted-merge with a gated file itself
        conflicted and unstaged, the gate denies -- with the trusted-anchor
        precondition asserted, so the deny is the unmerged-path listing
        keeping the gate armed rather than the anchor check itself
        failing."""
        repo = _build_conflicted_merge_via_origin_with_unresolved_skill_conflict(tmp_path)

        base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
        assert base_result.returncode == 0 and base_result.stdout.strip(), (
            f"precondition failed: fixture did not reach a trusted anchor: "
            f"rc={base_result.returncode} stdout={base_result.stdout!r}"
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="unmerged-conflict-session"),
            cwd=repo,
        )
        assert reason is not None and _UNREADABLE_PATH_TOKEN in reason


_CONFLICT_MARKER_TOKEN = "still contain unresolved conflict-marker lines"
_CONFLICT_MARKER_SCAN_FAILURE_TOKEN = "could not scan the staged skill files"
# `git diff -G` (candidates) then `git grep -L` (clean-blob proof).
_CONFLICT_MARKER_SCAN_CAPPED_CALLS = 2
_TWO_MARKER_LINES_BODY = "```\n<<<<<<< HEAD\nexample\n```\n```\n>>>>>>> other\n```"


def _skill_body(name, body_line):
    return f"---\nname: {name}\ndescription: ok\n---\n{body_line}\n"


def _plain_body(name, body_line):
    return f"# {name}\n{body_line}\n"


def _assert_trusted_anchor(repo):
    """The conflict-marker scan runs only with a non-empty base, so an
    allow-side test that lost its anchor would pass without the scan running."""
    base_result = _run_lib_fn(_PLUGIN_LIB, "_lib_gate_diff_base", str(repo))
    assert base_result.returncode == 0 and base_result.stdout.strip(), (
        f"precondition failed: fixture did not reach a trusted anchor: "
        f"rc={base_result.returncode} stdout={base_result.stdout!r}"
    )


def _build_merge_conflicting_in_two_gated_skills(
    tmp_path, *, merge_target, gated_paths=None, render_body=_skill_body, gitattributes=None
):
    """Both sides edit the same body line of two gated files, so the merge
    conflicts in both. `merge_target` is "ref" (`git merge origin/main`)
    or "oid" (the full 40-hex OID of origin/main), which decides whether git's
    conflict labels equal the literal OIDs `merge-tree --write-tree` uses.
    `gated_paths` maps a name to a repo-relative path (default: two
    claude-skills SKILL.md files), `render_body(name, line)` renders each
    file, and `gitattributes` is committed as the seed `.gitattributes`.
    Returns (clone, {name: repo-relative path}); every file is left with its
    conflict markers and unstaged."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    skill_paths = gated_paths or {
        name: f"claude-skills/skills/{name}/SKILL.md" for name in ("skill-a", "skill-b")
    }
    if gitattributes is not None:
        (clone / ".gitattributes").write_text(gitattributes)
        subprocess.run(["git", "add", ".gitattributes"], cwd=clone, check=True)
    for name, skill_rel in skill_paths.items():
        (clone / skill_rel).parent.mkdir(parents=True)
        (clone / skill_rel).write_text(render_body(name, "base line"))
        subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "seed skills"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)

    for name, skill_rel in skill_paths.items():
        (clone / skill_rel).write_text(render_body(name, "ours line"))
        subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits skills"], cwd=clone, check=True)

    push_clone = tmp_path / "_push_two_gated_skill_conflicts"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
    for name, skill_rel in skill_paths.items():
        (push_clone / skill_rel).write_text(render_body(name, "origin line"))
        subprocess.run(["git", "add", skill_rel], cwd=push_clone, check=True)
    subprocess.run(["git", "commit", "-qm", "origin edits skills"], cwd=push_clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=push_clone, check=True)

    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    merge_argument = "origin/main"
    if merge_target == "oid":
        merge_argument = subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=clone, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert len(merge_argument) == 40
    result = subprocess.run(
        ["git", "merge", "--no-commit", merge_argument], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    _assert_trusted_anchor(clone)
    return clone, skill_paths


def _build_conflict_free_merge_over_seeded_skill(tmp_path, skill_rel, seeded_content):
    """A gated file seeded on both sides at `seeded_content`, a local commit
    and an upstream commit touching unrelated files, and a conflict-free
    `git merge --no-commit` left in progress, so the base is non-empty and
    the gated file is untouched by the merge itself."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    (clone / skill_rel).parent.mkdir(parents=True)
    (clone / skill_rel).write_text(seeded_content)
    subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "seed skill"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)
    (clone / "local-only.txt").write_text("ours\n")
    subprocess.run(["git", "add", "local-only.txt"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "ours edits unrelated file"], cwd=clone, check=True)
    push_conflicting_edit_to_origin(tmp_path, bare, "upstream-only.txt", "theirs\n")
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    subprocess.run(["git", "merge", "--no-commit", "origin/main"], cwd=clone, check=True)
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    _assert_trusted_anchor(clone)
    return clone


def _git_exiting_with_status_env(tmp_path, arg_pattern, exit_status):
    """PATH override whose `git` exits `exit_status` at once on an argument
    matching `arg_pattern`, needing no timeout binary."""
    bin_dir = tmp_path / "bin-scan-exit-status"
    _make_git_exiting_with_status(bin_dir, arg_pattern, exit_status)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}


class TestSkillReviewGateConflictMarkerHardDeny:
    """A staged gated blob that still carries conflict-marker lines denies
    mid-merge regardless of how the merge named its target: with a full OID,
    git's conflict labels equal the ones `merge-tree --write-tree` writes into
    the base tree, so the unresolved blob reads as identical to the base."""

    @pytest.mark.parametrize("chained", [False, True], ids=["plain-commit", "chained-marker-write"])
    @pytest.mark.parametrize("merge_target", ["ref", "oid"])
    def test_unresolved_markers_staged_with_add_all_deny(
        self, isolated_home, tmp_path, merge_target, chained
    ):
        """The chained form must deny too: an unresolved file equal to the base
        blob leaves the marker diff empty, so `marker.sh` exits 0 and the `&&`
        chain would otherwise reach the commit."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target=merge_target
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        assert "<<<<<<<" in (repo / skill_paths["skill-a"]).read_text()

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input(_commit_command(chained), session_id=f"markers-{merge_target}-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-a"] in reason and skill_paths["skill-b"] in reason

    def test_mixed_resolved_and_unresolved_denies_despite_a_recorded_marker(
        self, isolated_home, tmp_path
    ):
        """Gated A is resolved (novel relative to the base) and gated B is left
        unresolved and equal to the base. The base-relative diff hides B, so a
        marker recorded for the staged state would otherwise release the
        commit."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid"
        )
        (repo / skill_paths["skill-a"]).write_text(_skill_body("skill-a", "resolved line"))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-mixed-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-b"] in reason and skill_paths["skill-a"] not in reason

    def test_failed_scan_denies_with_the_scan_reason(self, isolated_home, tmp_path):
        """The scan's own git call failing must deny rather than skip the
        check. The disarm fixture allows unshimmed."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        env = _git_exiting_with_status_env(tmp_path, "-G", 1)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-scan-failure-session"),
            cwd=repo,
            extra_env=env,
        )
        assert reason is not None and _CONFLICT_MARKER_SCAN_FAILURE_TOKEN in reason

    def test_failed_second_scan_call_denies_with_the_scan_reason(self, isolated_home, tmp_path):
        """Only the `git grep` call fails (status 128, not the 0/1 it uses to
        report listed/unlisted), on a fixture where the `git diff -G` call has
        candidates. It must deny rather than read the empty listing as no
        marker."""
        repo, _ = _build_merge_conflicting_in_two_gated_skills(tmp_path, merge_target="oid")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        env = _git_exiting_with_status_env(tmp_path, "grep", 128)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-grep-failure-session"),
            cwd=repo,
            extra_env=env,
        )
        assert reason is not None and _CONFLICT_MARKER_SCAN_FAILURE_TOKEN in reason

    @pytest.mark.parametrize(
        "presentation_config",
        ["color-ui-always", "diff-external-config", "git-external-diff-env", "diff-driver-command"],
    )
    def test_unresolved_markers_deny_under_diff_presentation_config(
        self, isolated_home, tmp_path, presentation_config
    ):
        """The verdict must not depend on how `git diff` renders a patch:
        color, an external diff program, or a diff driver command each replace
        or decorate patch text, and none may hide a marker line."""
        silent_external_diff = shutil.which("true") or "/usr/bin/true"
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path,
            merge_target="oid",
            gitattributes="*.md diff=silent-driver\n" if presentation_config == "diff-driver-command" else None,
        )
        extra_env = None
        config = {
            "color-ui-always": [("color.ui", "always")],
            "diff-external-config": [("diff.external", silent_external_diff)],
            "git-external-diff-env": [],
            "diff-driver-command": [("diff.silent-driver.command", silent_external_diff)],
        }[presentation_config]
        for key, value in config:
            subprocess.run(["git", "config", key, value], cwd=repo, check=True)
        if presentation_config == "git-external-diff-env":
            extra_env = {"GIT_EXTERNAL_DIFF": silent_external_diff}
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-presentation-config-session"),
            cwd=repo,
            extra_env=extra_env,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-a"] in reason and skill_paths["skill-b"] in reason

    @pytest.mark.parametrize(
        "gated_path",
        [
            "claude-skills/skills/skill with space/SKILL.md",
            "claude-skills/skills/skill-caf\u00e9/SKILL.md",
        ],
        ids=["space-in-name", "non-ascii-nfc-name"],
    )
    def test_unresolved_markers_in_an_unusually_named_gated_path_deny_despite_a_recorded_marker(
        self, isolated_home, tmp_path, gated_path
    ):
        """An unresolved file under a name with a space or non-ASCII character
        still denies: the deny set is the `git diff --name-only` listing minus
        the `git grep -L` listing by exact line match."""
        repo, gated = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid", gated_paths={"gated": gated_path}
        )
        (repo / gated["gated"]).write_text(_skill_body("gated", "<<<<<<< HEAD"))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-odd-name-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason

    def test_conflict_marker_scan_issues_exactly_the_documented_number_of_capped_git_calls(
        self, isolated_home, tmp_path
    ):
        """Pins the scan's invocation count only, not that each call is
        capped (the hang tests pin the caps). `_DOCUMENTED_HOOK_WORST_CASE_SECONDS`
        budgets one cap per scan call (`_CONFLICT_MARKER_SCAN_CAPPED_CALLS`), so
        an added or removed scan call fails here until the constant and the
        design doc's "Latency" section are updated."""
        repo, _ = _build_merge_conflicting_in_two_gated_skills(tmp_path, merge_target="oid")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        invocation_log = tmp_path / "git-invocations.log"
        bin_dir = tmp_path / "bin-counting-git"
        bin_dir.mkdir()
        shim = bin_dir / "git"
        shim.write_text(
            "#!/bin/bash\n"
            f'if {_CONFLICT_MARKER_SCAN} || {_CONFLICT_MARKER_BLOB_SCAN}; then echo scan >> "{invocation_log}"; fi\n'
            'exec "$REAL_GIT" "$@"\n'
        )
        shim.chmod(0o755)
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-call-count-session"),
            cwd=repo,
            extra_env=env,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert len(invocation_log.read_text().splitlines()) == _CONFLICT_MARKER_SCAN_CAPPED_CALLS

    def test_resolved_conflict_with_a_recorded_marker_still_allows(self, isolated_home, tmp_path):
        """Control for the deny cases: once every marker is resolved, the
        marker-based allow path is untouched."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid"
        )
        for name, skill_rel in skill_paths.items():
            (repo / skill_rel).write_text(_skill_body(name, "resolved line"))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-resolved-session"),
                cwd=repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "gated_path",
        [
            "claude-skills/skills/plan-review/ROUTING.md",
            "plugins/demo-plugin/skills/demo-skill/SKILL.md",
            "skills/demo-skill/SKILL.md",
            ".claude/skills/demo-skill/SKILL.md",
        ],
        ids=["routing-md", "plugin-skill", "root-skills", "dot-claude-skills"],
    )
    def test_unresolved_markers_in_each_gated_pathspec_arm_deny(
        self, isolated_home, tmp_path, gated_path
    ):
        """The scan reuses every gated pathspec: ROUTING.md is a separate arm
        from the SKILL.md layouts, and a scan narrowed to SKILL.md alone would
        let an unresolved ROUTING.md that equals the base blob disarm."""
        render_body = _plain_body if gated_path.endswith("ROUTING.md") else _skill_body
        repo, gated = _build_merge_conflicting_in_two_gated_skills(
            tmp_path,
            merge_target="oid",
            gated_paths={"gated": gated_path},
            render_body=render_body,
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-pathspec-arm-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert gated_path in reason

    def test_unresolved_markers_in_a_no_diff_attributed_file_deny(self, isolated_home, tmp_path):
        """`*.md -diff` makes `git diff -G` skip a path as binary unless the
        scan passes `-a`, which would disarm the gate on an unresolved merge."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid", gitattributes="*.md -diff\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-no-diff-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-a"] in reason

    def test_conflict_free_merge_adding_a_column_zero_example_denies_with_the_way_out(
        self, isolated_home, tmp_path
    ):
        """Accepted residual: upstream legitimately adds a fenced example whose
        line starts `<<<<<<< HEAD` at column 0, and no conflict exists. The
        deny reason says so and names indenting as the way out."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        (clone / skill_rel).parent.mkdir(parents=True)
        (clone / skill_rel).write_text(_skill_body("documented-skill", "base line"))
        subprocess.run(["git", "add", skill_rel], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-qm", "seed skill"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)
        (clone / "f").write_text("ours-edit\n")
        subprocess.run(["git", "add", "f"], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-qm", "ours edits f"], cwd=clone, check=True)
        push_conflicting_edit_to_origin(
            tmp_path,
            bare,
            skill_rel,
            _skill_body("documented-skill", "```\n<<<<<<< HEAD\nexample\n```"),
        )
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
        subprocess.run(["git", "merge", "--no-commit", "origin/main"], cwd=clone, check=True)
        assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-legitimate-example-session"),
            cwd=clone,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_rel in reason
        assert "legitimate content" in reason and "indent" in reason

        # Follow the way out: indent the example, restage, record a marker.
        _stage_gated_file(
            clone,
            skill_rel,
            _skill_body("documented-skill", "```\n <<<<<<< HEAD\nexample\n```"),
        )
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-legitimate-example-session"),
            cwd=clone,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert _CONFLICT_MARKER_TOKEN not in reason
        write_skill_review_marker(isolated_home, clone)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-legitimate-example-session"),
                cwd=clone,
            )
            == "allow"
        )

    def test_marker_lines_in_a_non_gated_staged_file_do_not_deny(self, isolated_home, tmp_path):
        """The scan is scoped to the gated pathspecs: a docs or fixture file
        that shows a conflict at column 0 must not hard-deny a merge whose
        gated files are resolved."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid"
        )
        for name, skill_rel in skill_paths.items():
            (repo / skill_rel).write_text(_skill_body(name, "resolved line"))
        (repo / "notes.txt").write_text("<<<<<<< HEAD\nexample\n>>>>>>> other\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-non-gated-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_unresolved_markers_in_a_textconv_driven_file_deny(self, isolated_home, tmp_path):
        """A textconv driver that indents column-0 marker lines hides them from
        `-G` unless the scan passes `--no-textconv`."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid", gitattributes="*.md diff=indent-markers\n"
        )
        subprocess.run(
            [
                "git", "config", "diff.indent-markers.textconv",
                r"sed 's/^\([<>]\)/ \1/'",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-textconv-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-a"] in reason

    def test_a_lone_closing_marker_line_in_an_edited_file_denies(self, isolated_home, tmp_path):
        """The `>>>>>>>` alternative: a half-resolved file that kept only the
        closing line differs from the base, so only the scan can stop it."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid"
        )
        (repo / skill_paths["skill-a"]).write_text(_skill_body("skill-a", "resolved line"))
        (repo / skill_paths["skill-b"]).write_text(
            _skill_body("skill-b", ">>>>>>> 0123456789abcdef")
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-closing-only-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-b"] in reason and skill_paths["skill-a"] not in reason

    def test_a_bare_opening_marker_line_in_an_edited_file_denies(self, isolated_home, tmp_path):
        """The `$` alternative of `( |$)`: a `<<<<<<<` line with no label still
        counts as a marker line."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid"
        )
        (repo / skill_paths["skill-a"]).write_text(_skill_body("skill-a", "resolved line"))
        (repo / skill_paths["skill-b"]).write_text(_skill_body("skill-b", "<<<<<<<"))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-bare-opening-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-b"] in reason and skill_paths["skill-a"] not in reason

    def test_an_eight_character_marker_run_is_not_a_marker_line(self, isolated_home, tmp_path):
        """The `( |$)` boundary: a `<<<<<<<<` heading is not a conflict marker."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid"
        )
        (repo / skill_paths["skill-a"]).write_text(_skill_body("skill-a", "<<<<<<<< heading"))
        (repo / skill_paths["skill-b"]).write_text(_skill_body("skill-b", ">>>>>>>>"))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write_skill_review_marker(isolated_home, repo)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-eight-char-session"),
                cwd=repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "restaged_body",
        [
            "```\n <<<<<<< HEAD\nexample\n```",
            "```\nexample\n```",
        ],
        ids=["indented", "removed"],
    )
    def test_a_marker_line_head_already_carries_does_not_deny_once_restaged(
        self, isolated_home, tmp_path, restaged_body
    ):
        """A column-0 marker line HEAD already has takes the ordinary marker
        path once the staged blob indents or removes it, and allows once a
        marker is recorded."""
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path,
            skill_rel,
            _skill_body("documented-skill", "```\n<<<<<<< HEAD\nexample\n```"),
        )
        _stage_gated_file(repo, skill_rel, _skill_body("documented-skill", restaged_body))

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-head-carries-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert _CONFLICT_MARKER_TOKEN not in reason

        write_skill_review_marker(isolated_home, repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-head-carries-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_indenting_one_of_two_head_marker_lines_denies_until_both_are_indented(
        self, isolated_home, tmp_path
    ):
        """The staged blob still carries a column-0 marker line after the
        first is indented, so the deny stands; indenting the second converges
        on the ordinary marker path."""
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path, skill_rel, _skill_body("documented-skill", _TWO_MARKER_LINES_BODY)
        )
        _stage_gated_file(
            repo,
            skill_rel,
            _skill_body("documented-skill", _TWO_MARKER_LINES_BODY.replace("<<<<<<< HEAD", " <<<<<<< HEAD")),
        )
        write_skill_review_marker(isolated_home, repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-two-head-lines-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_rel in reason

        _stage_gated_file(
            repo,
            skill_rel,
            _skill_body(
                "documented-skill",
                _TWO_MARKER_LINES_BODY.replace("<<<<<<< HEAD", " <<<<<<< HEAD").replace(
                    ">>>>>>> other", " >>>>>>> other"
                ),
            ),
        )
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-two-head-lines-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert _CONFLICT_MARKER_TOKEN not in reason
        write_skill_review_marker(isolated_home, repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-two-head-lines-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_removing_one_of_two_head_marker_lines_while_the_other_stays_denies(
        self, isolated_home, tmp_path
    ):
        """`git diff -G` selects the file through the removed marker line, but
        the staged blob still carries the other column-0 one."""
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path, skill_rel, _skill_body("documented-skill", _TWO_MARKER_LINES_BODY)
        )
        _stage_gated_file(
            repo,
            skill_rel,
            _skill_body("documented-skill", _TWO_MARKER_LINES_BODY.replace("<<<<<<< HEAD\n", "")),
        )
        write_skill_review_marker(isolated_home, repo)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-remove-one-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_rel in reason

    def test_deleting_a_gated_file_whose_head_version_has_marker_lines_is_allowed(
        self, isolated_home, tmp_path
    ):
        """`--diff-filter=d` keeps a staged deletion out of the scan: the
        removed lines that select the file under `-G` leave no staged blob
        to carry a marker line."""
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path, skill_rel, _skill_body("documented-skill", _TWO_MARKER_LINES_BODY)
        )
        subprocess.run(["git", "rm", "-q", skill_rel], cwd=repo, check=True)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-delete-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert _CONFLICT_MARKER_TOKEN not in reason

        write_skill_review_marker(isolated_home, repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-delete-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_a_newly_added_column_zero_marker_line_still_denies(self, isolated_home, tmp_path):
        """Counterpart of the HEAD-carries case: a marker line the staged
        file adds denies even though HEAD carries a different marker line."""
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path,
            skill_rel,
            _skill_body("documented-skill", "```\n<<<<<<< HEAD\nexample\n```"),
        )
        _stage_gated_file(
            repo,
            skill_rel,
            _skill_body(
                "documented-skill",
                "```\n<<<<<<< HEAD\nexample\n```\n```\n>>>>>>> other\n```",
            ),
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-added-marker-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_rel in reason

    def test_plain_commit_with_marker_like_line_is_not_hard_denied_by_the_scan(
        self, isolated_home, git_repo
    ):
        """The scan runs only with a non-empty base. A plain commit staging a
        column-0 marker-like line takes the ordinary marker path instead."""
        _stage_gated_file(
            git_repo,
            "claude-skills/skills/documented-skill/SKILL.md",
            _skill_body("documented-skill", "```\n<<<<<<< HEAD\nexample\n```"),
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m docs", session_id="markers-plain-commit-session"),
            cwd=git_repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert _CONFLICT_MARKER_TOKEN not in reason

    @pytest.mark.timing
    def test_hung_scan_is_capped_and_denies_with_the_scan_reason(
        self, isolated_home, tmp_path, git_timeout_shim
    ):
        """Only the scan hangs past the 5s cap. Without _lib_capped on it, the
        shim's sleep ends, git runs for real, the scan finds nothing, and the
        disarm fixture allows."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        env = git_timeout_shim(_CONFLICT_MARKER_SCAN)

        with assert_cap_engaged(tmp_path, production_cap=5, killed_calls=1):
            reason = run_hook_reason(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-hung-scan-session"),
                cwd=repo,
                extra_env=env,
            )
        assert reason is not None and _CONFLICT_MARKER_SCAN_FAILURE_TOKEN in reason

    @pytest.mark.timing
    def test_hung_blob_scan_is_capped_and_denies_with_the_scan_reason(
        self, isolated_home, tmp_path, git_timeout_shim
    ):
        """Only the `git grep` call hangs past the 5s cap, on a fixture where
        the `git diff -G` call has candidates. Without _lib_capped on it, the
        shim's sleep ends, git grep runs for real, and the verdict becomes the
        conflict-marker deny instead of the scan-failure deny."""
        repo, _ = _build_merge_conflicting_in_two_gated_skills(tmp_path, merge_target="oid")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        env = git_timeout_shim(_CONFLICT_MARKER_BLOB_SCAN)

        with assert_cap_engaged(tmp_path, production_cap=5, killed_calls=1):
            reason = run_hook_reason(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-hung-blob-scan-session"),
                cwd=repo,
                extra_env=env,
            )
        assert reason is not None and _CONFLICT_MARKER_SCAN_FAILURE_TOKEN in reason

    def test_editing_an_unrelated_line_of_a_file_head_carries_a_marker_line_in_takes_the_marker_path(
        self, isolated_home, tmp_path
    ):
        """Call A's `-G` narrows candidates to files whose change touches a
        marker line. A file whose HEAD blob carries a column-0 marker line and
        whose staged change edits a different line must reach the ordinary
        marker check, not the conflict-marker deny."""
        skill_rel = "claude-skills/skills/documented-skill/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path,
            skill_rel,
            _skill_body("documented-skill", "```\n<<<<<<< HEAD\nexample\n```\nunrelated line"),
        )
        _stage_gated_file(
            repo,
            skill_rel,
            _skill_body("documented-skill", "```\n<<<<<<< HEAD\nexample\n```\nedited line"),
        )

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-untouched-marker-session"),
            cwd=repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert _CONFLICT_MARKER_TOKEN not in reason

        write_skill_review_marker(isolated_home, repo)
        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-untouched-marker-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_indented_marker_line_in_a_non_ascii_named_gated_path_allows(
        self, isolated_home, tmp_path
    ):
        """Both scan calls must render a non-ASCII gated name identically
        (`core.quotepath=false`), or the clean file never clears the deny set
        and the deny message's own remedy cannot release the commit."""
        skill_rel = "claude-skills/skills/skill-caf\u00e9/SKILL.md"
        repo = _build_conflict_free_merge_over_seeded_skill(
            tmp_path,
            skill_rel,
            _skill_body("skill-caf\u00e9", "```\n<<<<<<< HEAD\nexample\n```"),
        )
        _stage_gated_file(
            repo,
            skill_rel,
            _skill_body("skill-caf\u00e9", "```\n <<<<<<< HEAD\nexample\n```"),
        )
        write_skill_review_marker(isolated_home, repo)

        assert (
            run_hook(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="markers-non-ascii-allow-session"),
                cwd=repo,
            )
            == "allow"
        )

    def test_crlf_terminated_conflict_marker_lines_deny(self, isolated_home, tmp_path):
        """A conflict blob whose marker lines end in CRLF still matches both
        scan calls, so the deny stands. `-text` keeps git from normalising the
        CRs away when the file is staged. The fixture uses labelled marker
        lines. It guards a line-end-anchored regex that fails to tolerate a
        trailing CR after a label. The bare-CR shape stays the accepted
        residual and is not covered."""
        repo, skill_paths = _build_merge_conflicting_in_two_gated_skills(
            tmp_path, merge_target="oid", gitattributes="*.md -text\n"
        )
        for skill_rel in skill_paths.values():
            conflicted_bytes = (repo / skill_rel).read_bytes()
            (repo / skill_rel).write_bytes(conflicted_bytes.replace(b"\n", b"\r\n"))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        staged_blob = subprocess.run(
            ["git", "show", f":{skill_paths['skill-a']}"], cwd=repo, capture_output=True, check=True
        ).stdout
        assert b"\r\n" in staged_blob, "precondition failed: staged blob lost its CRs"

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="markers-crlf-session"),
            cwd=repo,
        )
        assert reason is not None and _CONFLICT_MARKER_TOKEN in reason
        assert skill_paths["skill-a"] in reason and skill_paths["skill-b"] in reason


def _listing_only_failure_shim_env(tmp_path):
    """A git shim that fails only a `--name-only` call without `-G` --
    gated_paths_at_base's own invocation. The `-G` exclusion keeps the
    conflict-marker scan out of it. `_lib_skill_review_diff_base`'s
    own resolution (rev-parse, merge-base --is-ancestor, merge-tree
    --write-tree) and `_lib_staged_diff_hash`'s later hash call never pass
    `--name-only`, so neither is affected by this shim."""
    import shutil

    bin_dir = tmp_path / "bin-name-only-failure"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        "#!/bin/bash\n"
        f"if {_NAME_ONLY_LISTING}; then exit 1; fi\n"
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}


_LISTING_FAILURE_TOKEN = "could not list the staged skill files"

_NAME_ONLY_LISTING = '[[ " $* " == *" --name-only "* ]] && [[ " $* " != *" -G "* ]]'
_CONFLICT_MARKER_SCAN = '[[ " $* " == *" -G "* ]]'
_CONFLICT_MARKER_BLOB_SCAN = '[[ " $* " == *" grep "* ]]'
_ROUTING_PATHSPEC_LISTING = f'{_NAME_ONLY_LISTING} && [[ "$*" == *ROUTING.md* ]]'
_SKILL_PATHSPEC_LISTING = f'{_NAME_ONLY_LISTING} && [[ "$*" != *ROUTING.md* ]]'


class TestSkillReviewGateListingOnlyFailureBackstop:
    """A listing-only failure -- gated_paths_at_base's own git call fails,
    independent of _lib_skill_review_diff_base's resolution and the later
    hash call -- must deny, never silently disarm the gate or skip the
    structural validator."""

    def test_non_chained_commit_denies_with_listing_failure_reason(
        self, isolated_home, tmp_path
    ):
        """Reusing the ordinary disarm fixture proves this non-vacuously:
        unshimmed it allows, and under this shim the same fixture denies."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        extra_env = _listing_only_failure_shim_env(tmp_path)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="listing-failure-session"),
            cwd=repo,
            extra_env=extra_env,
        )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason

    def test_chained_form_denies_and_marker_write_still_computes_correct_marker(
        self, isolated_home, tmp_path
    ):
        """The listing-failure deny precedes the in-chain marker-write
        bypass, so the chained form denies too. The marker script's `write
        skill-review`, run under the identical shim, still computes the same
        base-relative digest an unshimmed run would: its own git calls never
        pass `--name-only`, so this listing failure cannot weaken it into
        writing an empty or wrong marker."""
        repo = _build_armed_fixture(tmp_path)
        _assert_armed_fixture_preimages_differ(repo, _MARKER_PATHSPECS)
        extra_env = _listing_only_failure_shim_env(tmp_path)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input(
                "~/.claude/scripts/marker.sh write skill-review && git commit -m merge",
                session_id="listing-failure-chained-session",
            ),
            cwd=repo,
            extra_env=extra_env,
        )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason

        sid = "listing-failure-chained-write-session"
        _seed_session(isolated_home, sid)
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "marker.sh"), "write", "skill-review"],
            cwd=repo,
            env={**os.environ, "HOME": str(isolated_home), **extra_env},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

        base = _merge_tree_base(repo)
        expected = staged_diff_hash_at_base(repo, base, *_MARKER_PATHSPECS)
        marker = skill_review_marker_path(isolated_home, repo, session_id=sid)
        assert marker.read_text().strip() == expected

    @pytest.mark.parametrize(
        ("failing_listing", "listing_predicate"),
        [("routing", _ROUTING_PATHSPEC_LISTING), ("skill", _SKILL_PATHSPEC_LISTING)],
    )
    def test_either_listing_failing_alone_denies_instead_of_disarming(
        self, isolated_home, tmp_path, git_timeout_shim, failing_listing, listing_predicate
    ):
        """Each listing's status is checked on its own: the other listing
        succeeds and reports empty, so only the failed one's status clause
        keeps the disarm fixture (which allows unshimmed) from disarming."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        env = git_timeout_shim(listing_predicate, exit_status=1)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id=f"{failing_listing}-listing-session"),
            cwd=repo,
            extra_env=env,
        )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason

    @pytest.mark.parametrize(
        ("failing_listing", "listing_predicate"),
        [
            ("routing", _ROUTING_PATHSPEC_LISTING),
            ("skill", _SKILL_PATHSPEC_LISTING),
            ("both", _NAME_ONLY_LISTING),
        ],
    )
    def test_listing_failure_denies_invalid_frontmatter_despite_matching_marker(
        self, isolated_home, git_repo, git_timeout_shim, failing_listing, listing_predicate
    ):
        """An empty STAGED_SKILL_PATHS would skip the structural validator,
        and the matching marker would then release the commit. The listing
        token in the reason distinguishes this deny from the validator's."""
        broken_skill = git_repo / "claude-skills" / "skills" / "broken" / "SKILL.md"
        broken_skill.parent.mkdir(parents=True)
        broken_skill.write_text(_BROKEN_SKILL_MD)
        subprocess.run(
            ["git", "add", "claude-skills/skills/broken/SKILL.md"], cwd=git_repo, check=True
        )
        session_id = f"{failing_listing}-listing-marker-session"
        marker = skill_review_marker_path(isolated_home, git_repo, session_id=session_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(staged_diff_hash_at_base(git_repo, "", *_MARKER_PATHSPECS) + "\n")
        env = git_timeout_shim(listing_predicate, exit_status=1)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m broken", session_id=session_id),
            cwd=git_repo,
            extra_env=env,
        )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason

    @pytest.mark.parametrize("chained", [False, True], ids=["plain", "chained"])
    def test_non_ascii_skill_path_with_invalid_frontmatter_denies_despite_matching_marker(
        self, isolated_home, git_repo, chained
    ):
        """A directory name with non-ASCII bytes lists C-quoted under the
        default core.quotepath, `git show :<quoted>` fails, and a skipped
        validator would let a matching marker (or the in-chain marker write)
        release invalid frontmatter. The same file under an ASCII directory
        denies from the validator, so this must too."""
        skill_rel = "claude-skills/skills/caf\u00e9/SKILL.md"
        broken_skill = git_repo / skill_rel
        broken_skill.parent.mkdir(parents=True)
        broken_skill.write_text(_BROKEN_SKILL_MD)
        subprocess.run(["git", "add", skill_rel], cwd=git_repo, check=True)
        session_id = f"non-ascii-{'chained' if chained else 'plain'}-session"
        marker = skill_review_marker_path(isolated_home, git_repo, session_id=session_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(staged_diff_hash_at_base(git_repo, "", *_MARKER_PATHSPECS) + "\n")
        command = "git commit -m broken"
        if chained:
            command = f"~/.claude/scripts/marker.sh write skill-review && {command}"

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input(command, session_id=session_id),
            cwd=git_repo,
        )
        assert reason is not None
        # A token only the validator emits: with core.quotepath unset the
        # reason is the unreadable-path deny instead.
        assert _VALIDATOR_YAML_TOKEN in reason

    @pytest.mark.parametrize("chained", [False, True], ids=["plain", "chained"])
    def test_non_ascii_skill_path_with_valid_frontmatter_allows_with_matching_marker(
        self, isolated_home, git_repo, chained
    ):
        """Only core.quotepath=false makes a non-ASCII directory list
        unquoted; without it the valid skill would be falsely denied as
        unreadable."""
        skill_rel = "claude-skills/skills/café/SKILL.md"
        _stage_gated_file(git_repo, skill_rel, _WELL_FORMED_SKILL_MD)
        session_id = f"non-ascii-valid-{'chained' if chained else 'plain'}-session"
        _write_matching_marker(isolated_home, git_repo, session_id)

        verdict = run_hook(
            SKILL_REVIEW_HOOK,
            bash_input(_commit_command(chained), session_id=session_id),
            cwd=git_repo,
        )
        assert verdict == "allow"

    @pytest.mark.parametrize("chained", [False, True], ids=["plain", "chained"])
    def test_tab_in_skill_directory_name_denies_as_unreadable_despite_matching_marker(
        self, isolated_home, git_repo, chained
    ):
        """git lists a tab-bearing name C-quoted even under
        core.quotepath=false. Without the unreadable-path deny, that path
        would skip the validator and a matching marker would release the
        invalid frontmatter."""
        skill_rel = "claude-skills/skills/ta\tb/SKILL.md"
        _stage_gated_file(git_repo, skill_rel, _BROKEN_SKILL_MD)
        session_id = f"tab-{'chained' if chained else 'plain'}-session"
        _write_matching_marker(isolated_home, git_repo, session_id)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input(_commit_command(chained), session_id=session_id),
            cwd=git_repo,
        )
        assert reason is not None and _UNREADABLE_PATH_TOKEN in reason

    def test_non_deleted_listing_failing_alone_denies_despite_matching_marker(
        self, isolated_home, git_repo, git_timeout_shim
    ):
        """The `--diff-filter=d` listing feeds the validator's path list, so
        its failure must deny rather than leave the list empty."""
        _stage_gated_file(git_repo, "claude-skills/skills/broken/SKILL.md", _BROKEN_SKILL_MD)
        _write_matching_marker(isolated_home, git_repo, "non-deleted-listing-session")
        env = git_timeout_shim('[[ "$*" == *--diff-filter=d* ]]', exit_status=1)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m broken", session_id="non-deleted-listing-session"),
            cwd=git_repo,
            extra_env=env,
        )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason

    @pytest.mark.timing
    def test_hung_listings_are_capped_and_deny(
        self, isolated_home, tmp_path, git_timeout_shim
    ):
        """Both `--name-only` listings hang past the 5s cap. Without
        _lib_capped on gated_paths_at_base the shim's sleep ends, git runs
        for real, both listings report empty, and the disarm fixture allows."""
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        env = git_timeout_shim(_NAME_ONLY_LISTING)

        with assert_cap_engaged(tmp_path, production_cap=5, killed_calls=2):
            reason = run_hook_reason(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m merge", session_id="hung-listing-session"),
                cwd=repo,
                extra_env=env,
            )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason

    @pytest.mark.timing
    def test_hung_per_path_git_show_is_capped_and_denies_despite_matching_marker(
        self, isolated_home, tmp_path, git_repo, git_timeout_shim
    ):
        """Only the per-path `git show` hangs. Without _lib_capped on it, the
        shim's sleep ends, git runs for real, and the invalid-YAML path
        reaches the validator instead of the unreadable deny."""
        _stage_gated_file(git_repo, "claude-skills/skills/broken/SKILL.md", _BROKEN_SKILL_MD)
        _write_matching_marker(isolated_home, git_repo, "hung-show-session")
        env = git_timeout_shim('[[ " $* " == *" show "* ]]')

        with assert_cap_engaged(tmp_path, production_cap=5, killed_calls=1):
            reason = run_hook_reason(
                SKILL_REVIEW_HOOK,
                bash_input("git commit -m broken", session_id="hung-show-session"),
                cwd=git_repo,
                extra_env=env,
            )
        assert reason is not None and _UNREADABLE_PATH_TOKEN in reason

    @pytest.mark.timing
    def test_hung_non_deleted_listing_is_capped_and_denies_despite_matching_marker(
        self, isolated_home, tmp_path, git_repo, git_timeout_shim
    ):
        """Only the `--diff-filter=d` listing hangs, so the two `--name-only`
        listings that run first succeed. Without _lib_capped on that call
        alone, the shim's sleep ends, git runs for real, and the invalid-YAML
        path reaches the validator instead of the listing-failure deny."""
        _stage_gated_file(git_repo, "claude-skills/skills/broken/SKILL.md", _BROKEN_SKILL_MD)
        _write_matching_marker(isolated_home, git_repo, "hung-non-deleted-listing-session")
        env = git_timeout_shim('[[ "$*" == *--diff-filter=d* ]]')

        with assert_cap_engaged(tmp_path, production_cap=5, killed_calls=1):
            reason = run_hook_reason(
                SKILL_REVIEW_HOOK,
                bash_input(
                    "git commit -m broken", session_id="hung-non-deleted-listing-session"
                ),
                cwd=git_repo,
                extra_env=env,
            )
        assert reason is not None and _LISTING_FAILURE_TOKEN in reason


_UNREADABLE_PATH_TOKEN = "could not read the staged content"
_VALIDATOR_YAML_TOKEN = "frontmatter is not strict YAML"
_MARKER_GATE_TOKEN = "have not been audited"


def _stage_gated_file(repo, skill_rel, content):
    path = repo / skill_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", skill_rel], cwd=repo, check=True)


def _write_matching_marker(isolated_home, repo, session_id):
    marker = skill_review_marker_path(isolated_home, repo, session_id=session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(staged_diff_hash_at_base(repo, "", *_MARKER_PATHSPECS) + "\n")


def _commit_command(chained):
    command = "git commit -m change"
    if chained:
        return f"~/.claude/scripts/marker.sh write skill-review && {command}"
    return command


class TestSkillReviewGateStagedRemovalReachesMarkerCheck:
    """A staged deletion or move-out of a gated SKILL.md has no index entry,
    so there is no blob for the structural validator; the removal is covered
    by the marker hash instead of being denied as unreadable."""

    _SKILL_REL = "claude-skills/skills/doomed/SKILL.md"

    def _commit_gated_skill_then_stage_removal(self, repo, removal):
        _stage_gated_file(repo, self._SKILL_REL, _WELL_FORMED_SKILL_MD)
        subprocess.run(["git", "commit", "-qm", "add skill"], cwd=repo, check=True)
        if removal == "rm":
            subprocess.run(["git", "rm", "-q", self._SKILL_REL], cwd=repo, check=True)
        else:
            (repo / "docs").mkdir()
            subprocess.run(
                ["git", "mv", self._SKILL_REL, "docs/doomed.md"], cwd=repo, check=True
            )

    @pytest.mark.parametrize("chained", [False, True], ids=["plain", "chained"])
    @pytest.mark.parametrize("removal", ["rm", "mv_out_of_gated_dir"])
    def test_removal_with_matching_marker_allows(
        self, isolated_home, git_repo, removal, chained
    ):
        self._commit_gated_skill_then_stage_removal(git_repo, removal)
        session_id = f"removal-{removal}-{'chained' if chained else 'plain'}-session"
        _write_matching_marker(isolated_home, git_repo, session_id)

        verdict = run_hook(
            SKILL_REVIEW_HOOK,
            bash_input(_commit_command(chained), session_id=session_id),
            cwd=git_repo,
        )
        assert verdict == "allow"

    @pytest.mark.parametrize("removal", ["rm", "mv_out_of_gated_dir"])
    def test_removal_without_marker_denies_at_marker_gate_not_as_unreadable(
        self, isolated_home, git_repo, removal
    ):
        self._commit_gated_skill_then_stage_removal(git_repo, removal)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m change", session_id=f"removal-{removal}-nomarker"),
            cwd=git_repo,
        )
        assert reason is not None
        assert _MARKER_GATE_TOKEN in reason
        assert _UNREADABLE_PATH_TOKEN not in reason



def _git_precomposes_argv(probe_repo):
    """True when git, under core.precomposeunicode=true, rewrites an NFD path
    argument to NFC (Apple's git does; upstream Linux builds do not)."""
    probe_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=probe_repo, check=True)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=probe_repo,
        input="probe\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git", "-c", "core.precomposeunicode=true",
            "update-index", "--add", "--cacheinfo", f"100644,{blob},cafe\u0301",
        ],
        cwd=probe_repo,
        check=True,
    )
    listed = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=probe_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return listed == "caf\u00e9"


class TestSkillReviewGateValidatorSkipsOnlyDiffDeletions:
    """The validator skips a path only on the diff's own D status; every other
    listed path must be readable, and an unreadable one denies."""

    _DELETED_REL = "claude-skills/skills/doomed/SKILL.md"
    _BROKEN_REL = "claude-skills/skills/broken/SKILL.md"

    def _deny_reason(self, isolated_home, repo, session_id):
        _write_matching_marker(isolated_home, repo, session_id)
        return run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m change", session_id=session_id),
            cwd=repo,
        )

    def _index_info(self, repo, *lines):
        subprocess.run(
            ["git", "update-index", "--index-info"],
            cwd=repo,
            input="".join(f"{line}\n" for line in lines),
            text=True,
            check=True,
        )

    def _hash_object(self, repo, content):
        return subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=content,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def test_nfd_named_index_entry_under_precomposeunicode_denies_despite_matching_marker(
        self, isolated_home, tmp_path, git_repo
    ):
        """core.precomposeunicode rewrites the argv name to NFC, so a
        name-keyed lookup of an NFD index entry finds nothing. The path must
        not read as a deletion: it denies as unreadable. Only git builds that
        precompose argv (Apple's) reproduce this, so the test skips elsewhere
        rather than passing on the validator branch, which does not exercise
        the name-keyed lookup."""
        if not _git_precomposes_argv(tmp_path / "precompose-probe"):
            pytest.skip("this git build does not precompose argv under core.precomposeunicode")
        nfd_rel = "claude-skills/skills/cafe\u0301/SKILL.md"
        subprocess.run(
            ["git", "config", "core.precomposeunicode", "true"], cwd=git_repo, check=True
        )
        blob = self._hash_object(git_repo, _BROKEN_SKILL_MD)
        self._index_info(git_repo, f"100644 {blob} 0\t{nfd_rel}")

        reason = self._deny_reason(isolated_home, git_repo, "nfd-session")

        assert reason is not None and _UNREADABLE_PATH_TOKEN in reason

    def test_mixed_deleted_and_invalid_yaml_staging_denies_at_validator(
        self, isolated_home, git_repo
    ):
        _stage_gated_file(git_repo, self._DELETED_REL, _WELL_FORMED_SKILL_MD)
        subprocess.run(["git", "commit", "-qm", "add skill"], cwd=git_repo, check=True)
        subprocess.run(["git", "rm", "-q", self._DELETED_REL], cwd=git_repo, check=True)
        _stage_gated_file(git_repo, self._BROKEN_REL, _BROKEN_SKILL_MD)

        reason = self._deny_reason(isolated_home, git_repo, "mixed-session")

        assert reason is not None and _VALIDATOR_YAML_TOKEN in reason

    def test_rename_to_invalid_content_gated_destination_denies_at_validator(
        self, isolated_home, git_repo
    ):
        """Overwriting the moved file drops similarity below git's rename
        threshold, so this stages a D plus an A rather than an R; the two
        rename-status tests below cover R."""
        _stage_gated_file(git_repo, self._DELETED_REL, _WELL_FORMED_SKILL_MD)
        subprocess.run(["git", "commit", "-qm", "add skill"], cwd=git_repo, check=True)
        (git_repo / "claude-skills" / "skills" / "moved").mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "mv", self._DELETED_REL, "claude-skills/skills/moved/SKILL.md"],
            cwd=git_repo,
            check=True,
        )
        _stage_gated_file(git_repo, "claude-skills/skills/moved/SKILL.md", _BROKEN_SKILL_MD)

        reason = self._deny_reason(isolated_home, git_repo, "rename-session")

        assert reason is not None and _VALIDATOR_YAML_TOKEN in reason

    _RENAMED_REL = "claude-skills/skills/moved/SKILL.md"

    def _stage_move(self, repo):
        (repo / "claude-skills" / "skills" / "moved").mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", self._DELETED_REL, self._RENAMED_REL], cwd=repo, check=True)

    def _assert_reported_as_rename(self, repo):
        name_status = subprocess.run(
            ["git", "diff", "--cached", "--name-status", "--", "claude-skills"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert name_status.startswith("R"), f"precondition failed: not a rename: {name_status!r}"

    def test_exact_rename_of_invalid_skill_to_gated_destination_denies_at_validator(
        self, isolated_home, git_repo
    ):
        """R100: the destination blob is byte-identical to the source, so only
        a listing that keeps R status reaches the validator with it."""
        _stage_gated_file(git_repo, self._DELETED_REL, _BROKEN_SKILL_MD)
        subprocess.run(["git", "commit", "-qm", "add invalid skill"], cwd=git_repo, check=True)
        self._stage_move(git_repo)
        self._assert_reported_as_rename(git_repo)

        reason = self._deny_reason(isolated_home, git_repo, "exact-rename-session")

        assert reason is not None and _VALIDATOR_YAML_TOKEN in reason

    def test_near_exact_rename_with_invalid_frontmatter_denies_at_validator(
        self, isolated_home, git_repo
    ):
        """A large body with only the frontmatter broken stays above the
        rename threshold, so git reports R for the destination."""
        long_body = "".join(f"body line {number}\n" for number in range(200))
        _stage_gated_file(
            git_repo, self._DELETED_REL, _WELL_FORMED_SKILL_MD.replace("# body\n", long_body)
        )
        subprocess.run(["git", "commit", "-qm", "add long skill"], cwd=git_repo, check=True)
        self._stage_move(git_repo)
        _stage_gated_file(
            git_repo, self._RENAMED_REL, _BROKEN_SKILL_MD.replace("# body\n", long_body)
        )
        self._assert_reported_as_rename(git_repo)

        reason = self._deny_reason(isolated_home, git_repo, "near-rename-session")

        assert reason is not None and _VALIDATOR_YAML_TOKEN in reason

    @pytest.mark.parametrize(
        "stages", [(1, 2, 3), (2,)], ids=["stages-1-2-3", "stage-2-only"]
    )
    def test_unmerged_index_entry_denies_as_unreadable_despite_matching_marker(
        self, isolated_home, git_repo, stages
    ):
        blob = self._hash_object(git_repo, _BROKEN_SKILL_MD)
        self._index_info(
            git_repo, *(f"100644 {blob} {stage}\t{self._BROKEN_REL}" for stage in stages)
        )

        reason = self._deny_reason(isolated_home, git_repo, "unmerged-session")

        assert reason is not None and _UNREADABLE_PATH_TOKEN in reason


class TestSkillReviewGateAliasedIndexEntryResidual:
    """A documented residual, not a control. The validator reads each listed
    path's staged content by re-resolving the name string, so a hand-built
    index holding an invalid entry plus a valid twin whose name equals the
    invalid entry's C-quoted rendering makes `git show` return the twin's blob
    and skips the invalid entry's validation. Pinned as the current allow so a
    later change to how blobs are read shows up as a visible test change."""

    _INVALID_REL = "claude-skills/skills/ta\tb/SKILL.md"
    # A real path whose literal name is the invalid entry's C-quoted form; it
    # starts with a quote, so it matches no gated pathspec and is never listed.
    _TWIN_REL = '"claude-skills/skills/ta\\tb/SKILL.md"'

    def _cacheinfo(self, repo, content, name):
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=content,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}"],
            cwd=repo,
            check=True,
        )

    def test_invalid_entry_with_quoted_name_twin_allows_with_matching_marker(
        self, isolated_home, git_repo
    ):
        self._cacheinfo(git_repo, _BROKEN_SKILL_MD, self._INVALID_REL)
        self._cacheinfo(git_repo, _WELL_FORMED_SKILL_MD, self._TWIN_REL)
        _write_matching_marker(isolated_home, git_repo, "aliased-index-session")

        verdict = run_hook(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m aliased", session_id="aliased-index-session"),
            cwd=git_repo,
        )
        assert verdict == "allow"

    def test_invalid_entry_alone_still_denies(self, isolated_home, git_repo):
        """Guards the allow above against passing because the gate is off:
        without the twin the same invalid entry denies as unreadable."""
        self._cacheinfo(git_repo, _BROKEN_SKILL_MD, self._INVALID_REL)
        _write_matching_marker(isolated_home, git_repo, "aliased-index-control-session")

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m aliased", session_id="aliased-index-control-session"),
            cwd=git_repo,
        )
        assert reason is not None and _UNREADABLE_PATH_TOKEN in reason

    def test_editing_only_the_invalid_entry_after_review_denies_at_marker_gate(
        self, isolated_home, git_repo
    ):
        """The marker hash is the residual's compensating control: the twin
        hides the invalid entry from the validator, so an edit to that entry
        alone must still invalidate the marker written for the reviewed
        state."""
        session_id = "aliased-index-edit-session"
        self._cacheinfo(git_repo, _BROKEN_SKILL_MD, self._INVALID_REL)
        self._cacheinfo(git_repo, _WELL_FORMED_SKILL_MD, self._TWIN_REL)
        _write_matching_marker(isolated_home, git_repo, session_id)
        hook_input = bash_input("git commit -m aliased", session_id=session_id)
        assert run_hook(SKILL_REVIEW_HOOK, hook_input, cwd=git_repo) == "allow"

        self._cacheinfo(git_repo, _BROKEN_SKILL_MD + "\nchanged\n", self._INVALID_REL)

        reason = run_hook_reason(SKILL_REVIEW_HOOK, hook_input, cwd=git_repo)
        assert reason is not None and _MARKER_GATE_TOKEN in reason

    def test_marker_hash_changes_when_invalid_entrys_own_content_changes(
        self, isolated_home, git_repo
    ):
        """Changing only the invalid entry's blob must move the digest the
        production marker writer records, independent of the hook verdict."""
        session_id = "aliased-index-hash-session"
        marker = skill_review_marker_path(isolated_home, git_repo, session_id=session_id)

        digests = []
        for invalid_entry_content in (_BROKEN_SKILL_MD, _BROKEN_SKILL_MD + "\nchanged\n"):
            self._cacheinfo(git_repo, invalid_entry_content, self._INVALID_REL)
            write_skill_review_marker(isolated_home, git_repo, session_id=session_id)
            digests.append(marker.read_text().strip())

        assert all(digests)
        assert digests[0] != digests[1]


class TestSkillReviewGateDenyAndTraceText:
    """Distinctive tokens, not full strings, so wording edits elsewhere in
    the text do not break these."""

    def test_armed_mid_merge_deny_carries_novel_content_base_note(
        self, isolated_home, tmp_path
    ):
        repo = _build_armed_fixture(tmp_path)
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="armed-note-session"),
            cwd=repo,
        )
        assert reason is not None and "novel-content base" in reason
        assert "gated against HEAD" not in reason

    def test_armed_mid_merge_deny_recovery_names_git_pull_not_plugin_install(
        self, isolated_home, tmp_path
    ):
        """Only a hook that already carries the novel-content base can print
        this note, so the remaining skew is a stale stowed marker.sh, which
        `git pull` in the claude-config checkout fixes. A plugin install
        step would tell the reader to refresh the copy that just printed it."""
        repo = _build_armed_fixture(tmp_path)
        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="armed-recovery-session"),
            cwd=repo,
        )
        assert reason is not None
        assert "git pull" in reason
        assert "plugin install" not in reason

    def test_ordinary_no_state_deny_carries_neither_base_note(self, isolated_home, git_repo):
        """Status 1 (no in-progress state) is neither an armed base nor an
        undetermined one, so neither note may appear; the undetermined-base
        note would tell the reader a real review gap is expected."""
        skill_rel = "claude-skills/skills/plain-unreviewed/SKILL.md"
        skill_file = git_repo / skill_rel
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\nname: plain-unreviewed\ndescription: x\n---\n\n# x\n")
        subprocess.run(["git", "add", skill_rel], cwd=git_repo, check=True)

        reason = run_hook_reason(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m plain", session_id="ordinary-deny-session"),
            cwd=git_repo,
        )
        assert reason is not None and _MARKER_GATE_TOKEN in reason
        assert "gated against HEAD" not in reason
        assert "novel-content base" not in reason

    def test_disarm_under_non_empty_base_traces_to_stderr(self, isolated_home, tmp_path):
        repo = build_conflicted_merge_via_origin_with_upstream_skill_edit(tmp_path)
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m merge", session_id="disarm-trace-session"),
            repo,
        )
        assert result.returncode == 0 and result.stdout == ""
        assert "gate disarmed" in result.stderr

    def test_ordinary_no_state_exit_is_silent(self, isolated_home, git_repo):
        result = _run_hook_with_stderr(
            SKILL_REVIEW_HOOK,
            bash_input("git commit -m plain", session_id="silent-exit-session"),
            git_repo,
        )
        assert result.returncode == 0 and result.stdout == ""
        assert result.stderr == ""


class TestSkillReviewPathspecParity:
    """The hook's MARKER_PATHSPECS and the marker script's
    SKILL_REVIEW_PATHSPECS are separate literals; a drift makes markers for
    the changed layout never match. That the hook hashes with MARKER_PATHSPECS
    is covered by the routing-diff marker verdict tests, not here."""

    @staticmethod
    def _evaluated_array(script, assignment_names, array_name):
        """Evaluates the script's own single-line top-level assignments in
        bash and returns the named array's elements. An empty result (a
        multi-line or appended assignment the grep cannot capture) fails with
        a message naming the script and array."""
        harness = (
            f"eval \"$(grep -E '^({assignment_names})=' \"$1\")\" || exit 1; "
            f"printf '%s\\n' \"${{{array_name}[@]}}\""
        )
        result = subprocess.run(
            ["bash", "-c", harness, "_", str(script)],
            capture_output=True, text=True,
        )
        elements = result.stdout.splitlines()
        assert result.returncode == 0 and elements, (
            f"could not evaluate {array_name} from {script.name}: "
            f"rc={result.returncode} stderr={result.stderr!r}"
        )
        return elements

    def test_hook_and_marker_script_pathspec_sets_are_equal(self):
        """Dropping ROUTING_PATHSPEC from the hook's composition fails here."""
        hook_content_pathspecs = self._evaluated_array(
            SKILL_REVIEW_HOOK, "SKILL_CONTENT_PATHSPECS", "SKILL_CONTENT_PATHSPECS"
        )
        hook_pathspecs = self._evaluated_array(
            SKILL_REVIEW_HOOK,
            "SKILL_CONTENT_PATHSPECS|ROUTING_PATHSPEC|MARKER_PATHSPECS",
            "MARKER_PATHSPECS",
        )
        marker_pathspecs = self._evaluated_array(
            SCRIPTS_DIR / "marker.sh", "SKILL_REVIEW_PATHSPECS", "SKILL_REVIEW_PATHSPECS"
        )

        routing_pathspecs = [
            pathspec for pathspec in hook_pathspecs if pathspec not in hook_content_pathspecs
        ]
        assert len(routing_pathspecs) == 1
        assert hook_pathspecs == marker_pathspecs


# The whole-hook worst case documented in the design-decision doc's "Latency"
# section, excluding the per-staged-SKILL.md `git show` term. A capped git
# call that hits its cap costs 7s (5s cap + 2s kill grace). Update the terms
# with that section when a cap or a capped call changes.
_DOCUMENTED_HOOK_WORST_CASE_SECONDS = (
    5  # `_lib_jq` input parse
    + 70  # base resolution: 5 cap hits x 7s + 7 non-hit calls x 5s
    + _CONFLICT_MARKER_SCAN_CAPPED_CALLS * 7  # conflict-marker scan calls, 7s each
    + 3 * 7  # staged-path listings
    + 12  # structural validator: 10s cap + 2s grace
    + 12  # corpus-budget scan: 10s cap + 2s grace
    + 7  # marker-hash diff
    + 5  # `_lib_jq` deny encoding
)


def test_hooks_json_pretooluse_timeout_covers_the_documented_worst_case():
    """A timed-out PreToolUse command hook does not block the call, so a
    `timeout` in hooks.json below the documented worst case would turn a slow
    but correct deny into an allow. Absent means the harness default of 600s."""
    hooks_config = json.loads((_PLUGINS_DIR / "skill-management" / "hooks" / "hooks.json").read_text())
    gate_entries = [
        hook
        for matcher_group in hooks_config["hooks"]["PreToolUse"]
        for hook in matcher_group["hooks"]
        if "require-skill-review.sh" in hook["command"]
    ]
    assert len(gate_entries) == 1
    configured_timeout = gate_entries[0].get("timeout")
    assert configured_timeout is None or configured_timeout >= _DOCUMENTED_HOOK_WORST_CASE_SECONDS
