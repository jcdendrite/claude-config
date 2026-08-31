"""Tests for require-ready-for-review.sh."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import _seed_session
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    assert_gate_handles_traversal_session_id,
    bash_input,
    edit_input,
    extract_skill_command,
    git_toplevel,
    head_sha,
    run_hook,
    run_skill_command,
)

READY_FOR_REVIEW_HOOK = HOOKS_DIR / "require-ready-for-review.sh"
READY_FOR_REVIEW_SKILL = SKILLS_DIR / "ready-for-review" / "SKILL.md"


@pytest.fixture
def fake_gh_pr_exists(tmp_path, monkeypatch):
    """Inject a fake gh that reports an open PR (`pr view ...` returns 42)."""
    bin_dir = tmp_path / "fake-bin-pr"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/bin/bash\n'
        'case "$*" in *"pr view"*) echo 42 ;; *) exit 1 ;; esac\n'
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return gh


@pytest.fixture
def fake_gh_no_pr(tmp_path, monkeypatch):
    """Inject a fake gh that reports no open PR (any subcommand exits 1)."""
    bin_dir = tmp_path / "fake-bin-nopr"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/bash\nexit 1\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return gh


@pytest.fixture
def repo_on_feature_branch(tmp_path):
    """Repo with `main` configured as default and a `feature` branch checked
    out. Fakes the origin/main ref so the hook's default-branch detection
    works without a real remote."""
    repo = tmp_path / "rfr-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f").write_text("a\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    return repo


def rfr_active_marker(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".ready-for-review-active.d" / session_id


def rfr_completion_marker(
    home: Path, repo: Path, session_id: str, config_dir: Path | None = None
) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    config_dir = config_dir if config_dir is not None else home / ".claude"
    return config_dir / "ready-for-review-markers" / f"{repo_hash}.{session_id}"


class TestRequireReadyForReview:
    """Push gate: requires /ready-for-review to have run before pushing to a
    branch with an open PR (or marking a draft PR ready). Two-marker layout:
    active (presence-only, bypasses during skill execution) and completion
    (HEAD SHA, allows pushes against the recorded HEAD)."""

    # -- Bypass shapes (no marker required) ------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git commit -m foo",
            "echo git push",
        ],
    )
    def test_non_push_commands_allowed(
        self, isolated_home, repo_on_feature_branch, command
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(command, session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_non_bash_tool_allowed(self, isolated_home):
        assert (
            run_hook(READY_FOR_REVIEW_HOOK, edit_input("/tmp/foo.txt")) == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "git push --dry-run",
            "git push origin feature --dry-run",
            "git push origin --delete feature",
            "git push origin -d feature",
            "git push origin :feature",
        ],
    )
    def test_destructive_or_dry_push_shapes_allowed(
        self,
        isolated_home,
        repo_on_feature_branch,
        fake_gh_pr_exists,
        command,
    ):
        """Even on a PR branch, --dry-run / --delete / colon-deletion don't
        publish a reviewable artifact change. Bypass without checking marker."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(command, session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_tags_only_push_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin --tags", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_tags_with_branch_still_gated(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """`git push --tags origin feature` pushes BOTH tags AND a branch ref —
        the branch is reviewable, so gate."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push --tags origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_default_branch_push_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Push from default branch has no PR review semantics — bypass."""
        subprocess.run(
            ["git", "checkout", "-q", "main"],
            cwd=repo_on_feature_branch,
            check=True,
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_branch_with_no_pr_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """Branch hasn't had a PR opened yet — nothing to gate."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_outside_git_repo_allowed(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push", session_id="s"),
                cwd=non_repo,
            )
            == "allow"
        )

    # -- Active-marker bypass --------------------------------------------

    def test_fresh_active_marker_allows(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """During /ready-for-review's own iteration (step 3 fix → push →
        loop), the active marker bypasses the gate so the skill doesn't
        self-deny."""
        sid = "session-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_alive_pid_active_marker_bypasses(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Active marker whose stored PID is alive bypasses the gate."""
        sid = "session-alive-pid"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_dead_pid_active_marker_evicts_and_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Orphaned marker with a dead PID is evicted; gate denies."""
        sid = "session-dead-pid"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.write_text("99999999")  # PID outside Linux/macOS max range → always dead
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"

    def test_other_sessions_active_marker_does_not_bypass(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Per-session keying: A's active marker must NOT authorize B's push."""
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").write_text(str(os.getpid()))
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="session-B"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_active_marker_empty_content_denies_bypass(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Empty marker content (non-numeric) fails the PID regex — fails closed."""
        sid = "session-empty"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text("")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_traversal_session_id_denies_and_does_not_touch_marker_dir(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """A session_id of '../canary' must not read through the traversal:
        ACTIVE_MARKER concatenates it into .ready-for-review-active.d/../canary,
        which resolves to a file one level up ($HOME/.claude/canary). The
        invalid id must skip the active-marker bypass entirely and fall
        through to the completion-marker check, which finds no match here
        and denies."""
        assert_gate_handles_traversal_session_id(
            READY_FOR_REVIEW_HOOK,
            lambda sid: bash_input("git push origin feature", session_id=sid),
            isolated_home,
            expected_decision="deny",
            cwd=repo_on_feature_branch,
        )

    # -- Completion-marker check ------------------------------------------

    def test_no_marker_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_completion_marker_matching_head_allows(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_completion_marker_stale_head_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """New commit after gate ran → HEAD moves → marker stale → deny."""
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        (repo_on_feature_branch / "f").write_text("b\n")
        subprocess.run(
            ["git", "add", "f"], cwd=repo_on_feature_branch, check=True
        )
        subprocess.run(
            ["git", "commit", "-qm", "more"],
            cwd=repo_on_feature_branch,
            check=True,
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    @pytest.mark.timing
    def test_current_head_git_timeout_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists, tmp_path
    ):
        """Required regression test: the CURRENT_HEAD `git rev-parse HEAD`
        call's _lib_capped exit status must fail closed on timeout, the same
        way an unresolvable HEAD already denies per the completion-marker
        check's own fail-closed comment — a stalled filesystem must not hang
        the gate indefinitely. Seeds a completion marker for the branch's
        real HEAD so an uncapped, fully-resolved CURRENT_HEAD would match it
        and allow — making `decision == "deny"` actually discriminate a
        working cap (empty CURRENT_HEAD, no match) from a broken one, rather
        than passing on every path because no marker exists at all."""
        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")

        fake_git = tmp_path / "git"
        fake_git.write_text(
            f'#!/bin/bash\nif [ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]; then sleep 10; fi\n'
            f'exec {real_git} "$@"\n'
        )
        fake_git.chmod(0o755)

        env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
        start = time.monotonic()
        decision = run_hook(
            READY_FOR_REVIEW_HOOK,
            bash_input("git push origin feature", session_id=sid),
            cwd=repo_on_feature_branch,
            extra_env=env,
        )
        elapsed = time.monotonic() - start
        assert decision == "deny"
        assert elapsed > 4, (
            f"expected the 5s _lib_capped timeout to fire (shim sleeps 10s "
            f"if it does not), took only {elapsed:.1f}s"
        )

    def test_other_sessions_completion_marker_authorizes_at_same_head(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """A's completion marker authorizes B's push at the same HEAD SHA.

        The stored SHA names the exact artifact the gate ran against, so it is
        the authorization; the filename's session suffix only keeps parallel
        sessions from overwriting each other's markers."""
        head = head_sha(repo_on_feature_branch)
        marker_a = rfr_completion_marker(isolated_home, repo_on_feature_branch, "A")
        marker_a.parent.mkdir(parents=True, exist_ok=True)
        marker_a.write_text(head + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="B"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_other_sessions_completion_marker_does_not_authorize_moved_head(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """The negative half: acceptance is by HEAD SHA, not by marker existence.

        A new commit moves HEAD past what the gate actually reviewed, so the
        marker must stop authorizing — otherwise dropping the session key would
        degrade the gate to an existence check."""
        marker_a = rfr_completion_marker(isolated_home, repo_on_feature_branch, "A")
        marker_a.parent.mkdir(parents=True, exist_ok=True)
        marker_a.write_text(head_sha(repo_on_feature_branch) + "\n")

        (repo_on_feature_branch / "after_gate.py").write_text("print('ungated')\n")
        subprocess.run(["git", "add", "after_gate.py"], cwd=repo_on_feature_branch, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "commit after the gate ran"],
            cwd=repo_on_feature_branch, check=True,
        )

        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="B"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_marker_under_another_repo_hash_does_not_authorize(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists, tmp_path
    ):
        """The repo-hash prefix stays part of the read predicate.

        Only the session suffix is globbed. All four gates share this read
        shape, so this invariant is pinned per-gate rather than once — a change
        that widens the glob for this hook alone must fail here."""
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=other_repo, check=True)

        # Correct HEAD sha, filed under the other repo's hash prefix.
        decoy = rfr_completion_marker(isolated_home, other_repo, "A")
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text(head_sha(repo_on_feature_branch) + "\n")

        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="B"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_no_session_id_denies_when_pr_exists(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Fail-closed: no session_id and no gate run covering HEAD → deny.

        session_id is needed only for the active-bypass marker; the completion
        read no longer consults it. This pins that an unparseable payload still
        denies rather than becoming an allow path."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    # -- gh pr ready ------------------------------------------------------

    def test_gh_pr_ready_no_marker_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Marking a draft PR ready is a handoff signal — gate it the same
        way as push."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr ready", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_ready_with_completion_marker_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr ready", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    # -- gh pr create -------------------------------------------------------

    def test_gh_pr_create_no_marker_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """Creating a PR is the publication boundary — gate it even though
        the branch has no open PR yet (fake_gh_no_pr, not fake_gh_pr_exists:
        a PR being created does not exist at gh-pr-view time, so a fixture
        that reports one existing would pass regardless of whether the
        PR-existence early-return is actually skipped for this arm)."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr create", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_create_with_completion_marker_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr create", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_gh_pr_create_stale_marker_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """A completion marker present but pointing at a stale (non-current)
        HEAD must still deny. This distinguishes 'the marker-check code path
        was reached and correctly rejected a mismatch' from
        test_gh_pr_create_no_marker_denies, which (using fake_gh_no_pr) would
        also deny via the pre-existing fail-open branch even if the
        marker-check code were unreachable for this arm — a stale marker
        only denies if that code actually runs."""
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("0" * 40 + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr create", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_create_with_flags_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(
                    'gh pr create --title "foo" --body "bar"', session_id="s"
                ),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_create_chained_after_push_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """A chained `git push && gh pr create` must still gate on the
        gh pr create fragment even though the push fragment (no PR yet)
        would bypass on its own."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(
                    "git push origin feature && gh pr create", session_id="s"
                ),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_create_chained_after_dry_run_push_bypasses_known_gap(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """Known, documented gap (see hook header): the --dry-run bypass
        greps the WHOLE $COMMAND string before any per-fragment check runs,
        so a --dry-run push chained ahead of gh pr create exits the gate
        early regardless of the gh-pr-create arm. Pre-existing in the
        --dry-run bypass block (unchanged by this diff — the identical shape
        already bypasses a second real `git push` chained the same way);
        inherited, not introduced, by the new arm. Pinned here as a known-bad
        case so a future accidental fix or accidental worsening doesn't pass
        silently — see the hook header and the plan's Part 3 residuals."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(
                    "git push --dry-run && gh pr create", session_id="s"
                ),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "eval gh pr create",
            "xargs gh pr create",
            "gh pr create;",
            "(cd /wt; gh pr create)",
            "GH_TOKEN=x gh pr create",
        ],
    )
    def test_gh_pr_create_wrapper_shapes_denied(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr, command
    ):
        """Mirrors test_command_shapes_that_escaped_old_regex_are_denied's
        git-push coverage for the gh-pr-create detection regex."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(command, session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_create_echo_false_positive_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """Known, pre-existing limitation shared with is_gh_pr_ready: the
        detection regex scans raw fragment text rather than command
        position, so a command that merely mentions "gh pr create" (without
        invoking it) is denied too. Fail-closed, not a security gap — pins
        the current behavior so a future regex change doesn't silently flip
        it to a false negative instead."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("echo gh pr create", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_deny_message_names_pr_create(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """The deny message must name gh pr create specifically, not fall
        into the push-worded branch (the bug this arm fixes)."""
        env = os.environ.copy()
        env["HOME"] = str(isolated_home)
        result = subprocess.run(
            [str(READY_FOR_REVIEW_HOOK)],
            input=json.dumps(bash_input("gh pr create", session_id="s")),
            capture_output=True,
            text=True,
            cwd=repo_on_feature_branch,
            env=env,
            check=False,
        )
        payload = json.loads(result.stdout)
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "PR creation blocked" in reason
        assert "Push to a branch" not in reason

    # -- gh failure → fail-open ------------------------------------------

    def test_gh_failure_fails_open(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """If gh pr view errors (network glitch, gh not configured), the gate
        does not fire — keeps the user unblocked. The skill's prose triggers
        still cover this case."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    # -- Skill ↔ hook alignment ------------------------------------------

    def test_skill_activate_command_creates_active_marker(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """SKILL.md activate-gate fixture must produce a marker the hook
        recognizes as a fresh active-session bypass."""
        sid = "test-session-rfr-activate"
        _seed_session(isolated_home, sid)

        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        ), "precondition: gated push must deny before activation"

        cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "activate-gate")
        run_skill_command(cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)

        marker = rfr_active_marker(isolated_home, sid)
        assert marker.exists(), (
            "SKILL.md activate-gate recipe ran but no marker landed at the "
            "path the hook checks — skill and hook disagree on layout."
        )
        assert marker.read_text().strip().isdigit(), (
            "activate-gate marker must contain a unix epoch timestamp "
            f"(parseable integer), got: {marker.read_text().strip()!r}"
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_skill_record_completion_command_creates_marker(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """SKILL.md record-completion fixture must produce a marker keyed by
        repo-hash + session-id with HEAD SHA as content, recognized by the
        hook for HEAD-matching pushes."""
        sid = "test-session-rfr-complete"
        _seed_session(isolated_home, sid)

        cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "record-completion")
        run_skill_command(cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)

        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        assert marker.exists(), (
            "SKILL.md record-completion recipe ran but no marker landed at "
            "the path the hook checks — skill and hook disagree on layout."
        )
        assert marker.read_text().strip() == head_sha(repo_on_feature_branch), (
            "completion marker must hold the local HEAD SHA"
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    # -- Command-shape coverage (regex-gap fixes) -------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "git -C /some/wt push origin feature",
            "git --git-dir=/some/wt/.git --work-tree=/some/wt push",
            "GIT_DIR=/some/wt/.git git push",
            "eval git push",
            "xargs git push",
            "git push;",
            "(cd /wt; git push)",
        ],
    )
    def test_command_shapes_that_escaped_old_regex_are_denied(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists, command
    ):
        """Command forms that the old regex missed must now be detected and gated."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(command, session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_cwd_json_field_used_for_branch_resolution(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Hook must read .cwd from the JSON payload, not the inherited process CWD,
        for branch detection. When the payload's cwd points at a feature-branch
        repo but the hook process runs from an unrelated directory, the gate
        must still fire (not bypass via the default-branch check)."""
        import tempfile

        sid = "s"
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin feature"},
            "session_id": sid,
            "cwd": str(repo_on_feature_branch),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            assert (
                run_hook(
                    READY_FOR_REVIEW_HOOK,
                    payload,
                    cwd=Path(tmpdir),
                )
                == "deny"
            )

    def test_skill_deactivate_command_removes_active_marker(
        self, isolated_home, repo_on_feature_branch
    ):
        """SKILL.md deactivate-gate fixture must remove this session's active
        marker so a subsequent push (without completion marker) is re-gated."""
        sid = "test-session-rfr-deactivate"
        _seed_session(isolated_home, sid)

        activate_cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "activate-gate")
        run_skill_command(activate_cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)
        marker = rfr_active_marker(isolated_home, sid)
        assert marker.exists(), "activate-gate setup did not create the marker"

        deactivate_cmd = extract_skill_command(
            READY_FOR_REVIEW_SKILL, "deactivate-gate"
        )
        run_skill_command(deactivate_cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)
        assert not marker.exists(), (
            "SKILL.md deactivate-gate recipe ran but the marker is still "
            "present — skill and hook disagree on the marker path."
        )


class TestRequireReadyForReviewHonorsConfigDir:
    """CLAUDE_CONFIG_DIR relocates the ready-for-review marker directory the
    same way for marker.sh (write) and this hook (read) -- see marker.sh and
    the cross-account bypass this closes (ledger row 7)."""

    def test_marker_under_matching_config_dir_allows(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists, tmp_path
    ):
        """CLAUDE_CONFIG_DIR-set happy path: a marker written under the
        resolved config dir satisfies the gate when the session runs under
        the same value."""
        profile = tmp_path / "profile"
        sid = "session-config-dir-match"
        marker = rfr_completion_marker(
            isolated_home, repo_on_feature_branch, sid, config_dir=profile
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
                extra_env={"CLAUDE_CONFIG_DIR": str(profile)},
            )
            == "allow"
        )

    def test_marker_under_different_config_dir_does_not_authorize(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists, tmp_path
    ):
        """Cross-account bypass regression: a marker written under one
        CLAUDE_CONFIG_DIR value must not satisfy the gate when the session
        runs under a different one."""
        profile_a = tmp_path / "profile-a"
        profile_b = tmp_path / "profile-b"
        sid = "session-config-dir-mismatch"
        marker = rfr_completion_marker(
            isolated_home, repo_on_feature_branch, sid, config_dir=profile_a
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
                extra_env={"CLAUDE_CONFIG_DIR": str(profile_b)},
            )
            == "deny"
        )

    def test_unresolvable_config_dir_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Fail closed: a relative CLAUDE_CONFIG_DIR (unresolvable) must deny
        the gate outright, even with a valid marker at the default location."""
        sid = "session-config-dir-unresolvable"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
                extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
            )
            == "deny"
        )
