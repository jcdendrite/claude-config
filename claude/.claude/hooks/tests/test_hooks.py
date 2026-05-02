"""End-to-end tests for the three Claude Code PreToolUse hooks.

Each hook is a black box: feed it tool-input JSON on stdin, read the
permissionDecision off stdout. Silent exit (exit 0, no output) means "allow".

Ported from hook-tests.sh. Uses pytest fixtures to sandbox $HOME so marker
files never touch the user's real `~/.claude/` state.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
CODE_REVIEW_HOOK = HOOKS_DIR / "require-code-review.sh"
RESPOND_PR_HOOK = HOOKS_DIR / "require-respond-pr.sh"
READY_FOR_REVIEW_HOOK = HOOKS_DIR / "require-ready-for-review.sh"
REVIEW_PERMS_HOOK = HOOKS_DIR / "ask-review-permissions.sh"
DENY_PRIVATE_PROJECT_REFS_HOOK = HOOKS_DIR / "deny-private-project-refs.sh"
STOW_REMINDER_HOOK = HOOKS_DIR / "require-stow-reminder.sh"
WORKTREE_HOOK = HOOKS_DIR / "require-worktree-for-git-writes.sh"
FILE_WRITES_HOOK = HOOKS_DIR / "require-worktree-for-file-writes.sh"
CAPTURE_SESSION_ID_HOOK = HOOKS_DIR / "capture-session-id.sh"
GUARD_SETTINGS_MODEL_EFFORT_HOOK = HOOKS_DIR / "guard-settings-model-effort.sh"
REQUIRE_PLAN_REVIEW_HOOK = HOOKS_DIR / "require-plan-review.sh"

SKILLS_DIR = HOOKS_DIR.parent / "skills"
CODE_REVIEW_SKILL = SKILLS_DIR / "code-review" / "SKILL.md"
RESPOND_PR_SKILL = SKILLS_DIR / "respond-pr" / "SKILL.md"
READY_FOR_REVIEW_SKILL = SKILLS_DIR / "ready-for-review" / "SKILL.md"

# SKILL.md fences may be indented when the fixture sits inside a
# numbered list (e.g. respond-pr's "0. **Enable hook bypass.**"). The
# closing-fence match has to tolerate the same leading whitespace as
# the opening, otherwise the non-greedy body capture runs past every
# indented fence until it finds an unindented one elsewhere in the file.
_SKILL_FIXTURE_RE = re.compile(
    r"<!--\s*HOOK_TEST_FIXTURE:\s*(?P<id>[A-Za-z0-9_-]+)\b[^>]*-->\s*"
    r"```[a-z]*\n(?P<body>.*?)\n[ \t]*```",
    re.DOTALL,
)


def extract_skill_command(skill_path: Path, fixture_id: str) -> str:
    """Return the body of the fenced code block tagged with `fixture_id`.

    SKILL.md files mark hook-alignment fixtures with
    `<!-- HOOK_TEST_FIXTURE: <id> -->` immediately followed by a fenced
    code block. Reading the recipe from SKILL.md at test time (rather
    than embedding a hardcoded copy in the test source) makes SKILL.md
    the single source of truth — drift between the documented recipe
    and what the test executes can't happen silently.
    """
    text = skill_path.read_text()
    matches = [m for m in _SKILL_FIXTURE_RE.finditer(text) if m.group("id") == fixture_id]
    if not matches:
        raise AssertionError(
            f"HOOK_TEST_FIXTURE '{fixture_id}' not found in {skill_path} — "
            "either the marker was removed or the immediately-following "
            "fenced block is missing."
        )
    if len(matches) > 1:
        raise AssertionError(
            f"HOOK_TEST_FIXTURE '{fixture_id}' appears {len(matches)} times in "
            f"{skill_path} — fixture ids must be unique so the test runs the "
            "intended block."
        )
    return matches[0].group("body").strip()


def run_hook(hook: Path, tool_input: dict, cwd: Path | None = None) -> str:
    """Invoke `hook` with `tool_input` as JSON stdin. Return the decision.

    Silent exit (exit 0, empty stdout) maps to "allow" to match the hook
    protocol, where absence of output means "no opinion".
    """
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if not result.stdout.strip():
        return "allow"
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["permissionDecision"]


def run_hook_reason(hook: Path, tool_input: dict, cwd: Path | None = None) -> str | None:
    """Like `run_hook` but returns the deny `permissionDecisionReason` string
    (or `None` if the hook allowed silently). Used by tests that need to
    assert on the contents of the deny message, not just the decision."""
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"].get("permissionDecisionReason")


def bash_input(command: str, session_id: str | None = None) -> dict:
    payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def edit_input(file_path: str) -> dict:
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path, "old_string": "a", "new_string": "b"},
    }


def write_input(file_path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}}


def multiedit_input(file_path: str) -> dict:
    return {"tool_name": "MultiEdit", "tool_input": {"file_path": file_path, "edits": []}}


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Sandbox $HOME so the hooks' marker files don't collide with real state."""
    home = tmp_path / "home"
    (home / ".claude" / "review-markers").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def git_repo(tmp_path):
    """Fresh git repo with one committed file and one staged change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\nsecond\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    return repo


def git_toplevel(repo: Path) -> str:
    """Return what `git rev-parse --show-toplevel` sees — this is what the
    hook hashes, and it may differ from `str(repo)` when /tmp is a symlink."""
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


DEFAULT_TEST_SESSION_ID = "test-session-default"


def marker_path(home: Path, repo: Path, session_id: str = DEFAULT_TEST_SESSION_ID) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "review-markers" / f"{repo_hash}.{session_id}"


def staged_diff_hash(repo: Path) -> str:
    diff = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, capture_output=True, check=True
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def write_marker(
    home: Path,
    repo: Path,
    diff_hash: str,
    session_id: str = DEFAULT_TEST_SESSION_ID,
) -> Path:
    marker = marker_path(home, repo, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(diff_hash + "\n")
    return marker


# ---------------------------------------------------------------------------
# require-code-review.sh
# ---------------------------------------------------------------------------


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
        subprocess.run(
            ["bash", "-c", skill_command],
            cwd=git_repo,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )
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

    # -- WIP opt-out (F-10) ------------------------------------------------
    # Commits whose message starts with `wip:`, `fixup!`, or `[skip-review]`
    # bypass the per-commit review gate. The cumulative /ready-for-review pass
    # still fires before PR handoff — the per-commit gate is the only thing
    # skipped. Case-sensitive: `WIP:` is NOT an opt-out prefix.

    @pytest.mark.parametrize(
        "commit_msg",
        [
            "wip: incomplete feature",
            "fixup! previous commit",
            "[skip-review] trivial typo fix",
        ],
    )
    def test_wip_prefix_allows_without_marker(self, isolated_home, git_repo, commit_msg):
        """WIP-prefixed commits are allowed through even without a review marker."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(f'git commit -m "{commit_msg}"', session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_non_wip_commit_still_blocked_without_marker(self, isolated_home, git_repo):
        """A non-WIP commit with the same staged diff is blocked without a review marker."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input('git commit -m "feat: add new feature"', session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_wip_prefix_case_sensitive_uppercase_blocked(self, isolated_home, git_repo):
        """WIP opt-out is case-sensitive; `WIP:` (uppercase) is not an opt-out prefix."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input('git commit -m "WIP: not an opt-out"', session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )


# ---------------------------------------------------------------------------
# require-respond-pr.sh
# ---------------------------------------------------------------------------


@pytest.fixture
def current_repo_foo_bar(tmp_path):
    """Git repo whose origin is https://github.com/foo/bar.git.

    Most respond-pr tests target `foo/bar` in the command URL. The
    cross-repo bypass compares COMMAND_REPO against the current git origin,
    so we need the current repo to also be `foo/bar` for the gate to fire
    as expected on same-repo commands.
    """
    repo = tmp_path / "foo-bar-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/foo/bar.git"],
        cwd=repo,
        check=True,
    )
    return repo


class TestRequireRespondPr:
    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh api repos/foo/bar/pulls/5/reviews",
            "gh api repos/foo/bar/issues/5/comments",
            "gh pr comment 5 --body test",
            "gh pr review 5 --approve",
            "gh api repos/foo/bar/pulls/5/comments -F body=hi",
        ],
    )
    def test_matching_commands_denied(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr view 5",
            "gh pr list",
            "gh api user",
            "gh pr checkout 5",
            "echo foo",
            "git status",
        ],
    )
    def test_non_matching_commands_allowed(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    # -- Bypass-marker behavior --------------------------------------------
    # Marker layout: ~/.claude/.respond-pr-active.d/<session_id>. The skill
    # writes one file per session at entry; the hook checks the file matching
    # THIS request's session_id (from JSON payload). Per-session keying
    # prevents two failure modes the prior singleton path had:
    #   (1) Cleanup thrash — session A's `rm -f` deleting B's marker.
    #   (2) Bypass leak — session A's marker silently authorizing B's
    #       gh calls even though B never ran /respond-pr.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh pr comment 5 --body test",
        ],
    )
    def test_fresh_bypass_marker_allows(self, isolated_home, current_repo_foo_bar, command):
        sid = "test-session-fresh"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command, session_id=sid), cwd=current_repo_foo_bar)
            == "allow"
        )

    def test_stale_bypass_marker_denies(self, isolated_home, current_repo_foo_bar):
        sid = "test-session-stale"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        # Backdate 90 minutes — past the hook's 60-minute staleness cutoff.
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_other_sessions_marker_does_not_leak_bypass(self, isolated_home, current_repo_foo_bar):
        """Regression: a marker for session A must NOT bypass session B's
        gated calls. This is the silent failure mode of the prior singleton
        design — while any session held the marker, every other session's
        matching gh calls bypassed the gate."""
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id="session-B"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_no_session_id_in_input_denies(self, isolated_home, current_repo_foo_bar):
        """Without session_id in the hook payload, bypass is impossible —
        deny even if a marker file exists in the dir."""
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "any-session").touch()
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_marker_dir_missing_denies(self, isolated_home, current_repo_foo_bar):
        """Sessions dir entirely absent (e.g., capture-session-id.sh never
        ran) → deny. The skill is responsible for failing loudly in that
        case; the hook just denies safely."""
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id="some-session"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_bypass_refreshes_marker_mtime(self, isolated_home, current_repo_foo_bar):
        """Long-running skill mitigation: the hook touches the marker on
        each bypass so a respond-pr session approaching the 60-minute
        staleness cutoff doesn't get blocked mid-execution."""
        sid = "long-run-session"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        fifty_min_ago = time.time() - 50 * 60
        os.utime(marker, (fifty_min_ago, fifty_min_ago))
        pre_mtime = marker.stat().st_mtime
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )
        post_mtime = marker.stat().st_mtime
        assert post_mtime > pre_mtime, (
            "marker mtime must be refreshed on bypass to keep long skill runs alive"
        )

    def test_non_bash_tool_allowed(self, isolated_home):
        assert run_hook(RESPOND_PR_HOOK, edit_input("/tmp/foo.txt")) == "allow"

    # -- Cross-repo bypass --------------------------------------------------
    # Regression: the gate originally fired on any `(pulls|issues)/N/...`
    # URL regardless of repo, which false-positived on cross-repo research
    # reads like `gh api repos/anthropics/claude-code/issues/12962/comments`
    # from inside an unrelated project.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/other/repo/pulls/5/comments",
            "gh api repos/other/repo/pulls/5/reviews",
            "gh api repos/other/repo/issues/5/comments",
            "gh pr comment 5 -R other/repo --body test",
            "gh pr review 5 --repo other/repo --approve",
            "gh pr comment 5 --repo=other/repo --body test",
        ],
    )
    def test_cross_repo_commands_allowed(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    def test_ssh_origin_cross_repo_allowed(self, isolated_home, tmp_path):
        """SSH-form origin (git@github.com:owner/repo.git) must parse too."""
        repo = tmp_path / "ssh-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:foo/bar.git"],
            cwd=repo,
            check=True,
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/other/repo/issues/5/comments"),
                cwd=repo,
            )
            == "allow"
        )

    def test_ssh_origin_same_repo_denied(self, isolated_home, tmp_path):
        repo = tmp_path / "ssh-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:foo/bar.git"],
            cwd=repo,
            check=True,
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/issues/5/comments"),
                cwd=repo,
            )
            == "deny"
        )

    # -- Skill ↔ hook alignment -------------------------------------------
    # These execute the enable/disable bypass recipes verbatim from
    # respond-pr SKILL.md. If the skill body drifts from the marker layout
    # require-respond-pr.sh expects, these fail.

    def test_skill_enable_command_creates_bypass_marker(
        self, isolated_home, current_repo_foo_bar
    ):
        """Run the SKILL.md enable-bypass recipe and verify the resulting
        marker authorizes a previously-gated gh command."""
        sid = "test-session-respond-pr-enable"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        gated_command = "gh api repos/foo/bar/pulls/5/comments"
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(gated_command, session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        ), "precondition: gh comment fetch must be gated before bypass is enabled"

        enable_command = extract_skill_command(RESPOND_PR_SKILL, "enable-bypass")
        subprocess.run(
            ["bash", "-c", enable_command],
            cwd=current_repo_foo_bar,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )

        marker = isolated_home / ".claude" / ".respond-pr-active.d" / sid
        assert marker.exists(), (
            "SKILL.md enable-bypass recipe ran but no marker landed at the "
            "path the hook checks — the skill and hook disagree on layout."
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(gated_command, session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    def test_skill_disable_command_removes_bypass_marker(
        self, isolated_home, current_repo_foo_bar
    ):
        """Run enable then disable from SKILL.md; verify the disable recipe
        removes the marker and the hook re-gates."""
        sid = "test-session-respond-pr-disable"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        enable_command = extract_skill_command(RESPOND_PR_SKILL, "enable-bypass")
        subprocess.run(
            ["bash", "-c", enable_command],
            cwd=current_repo_foo_bar,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )
        marker = isolated_home / ".claude" / ".respond-pr-active.d" / sid
        assert marker.exists(), "enable-bypass setup did not create the marker"

        disable_command = extract_skill_command(RESPOND_PR_SKILL, "disable-bypass")
        subprocess.run(
            ["bash", "-c", disable_command],
            cwd=current_repo_foo_bar,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )

        assert not marker.exists(), (
            "SKILL.md disable-bypass recipe ran but the marker is still "
            "present — the skill and hook disagree on the marker path."
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )


# ---------------------------------------------------------------------------
# capture-session-id.sh
# ---------------------------------------------------------------------------


class TestCaptureSessionId:
    """SessionStart hook that bootstraps the session_id ↔ claude-PID
    lookup file. Skill bodies running as Bash tool calls don't see the
    hook payload; they read ~/.claude/sessions/$PPID to learn their own
    session_id (where $PPID is the claude main process PID).

    The hook must never block session startup, so every error path exits 0.
    """

    def _sessions_files(self, home: Path) -> list[Path]:
        sessions_dir = home / ".claude" / "sessions"
        if not sessions_dir.exists():
            return []
        return list(sessions_dir.iterdir())

    def test_valid_input_writes_lookup_file(self, isolated_home):
        sid = "abc-123-session"
        run_hook(CAPTURE_SESSION_ID_HOOK, {"session_id": sid})
        files = self._sessions_files(isolated_home)
        assert len(files) == 1, f"expected one lookup file, got {files}"
        assert files[0].read_text().strip() == sid
        # Filename is the claude_pid the hook resolved via `ps -o ppid=`.
        # We don't pin the exact value (depends on test runner topology),
        # but it must be a positive integer.
        assert files[0].name.isdigit() and int(files[0].name) > 0

    def _run_capturing_stderr(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(CAPTURE_SESSION_ID_HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_empty_session_id_writes_nothing_with_stderr_diagnostic(self, isolated_home):
        result = self._run_capturing_stderr(json.dumps({"session_id": ""}))
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "no session_id" in result.stderr

    def test_missing_session_id_field_writes_nothing_with_stderr_diagnostic(self, isolated_home):
        result = self._run_capturing_stderr(json.dumps({"some_other_field": "value"}))
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "no session_id" in result.stderr

    def test_empty_stdin_does_not_block_and_emits_stderr(self, isolated_home):
        """Empty payload must not block session start, but must leave a
        diagnostic trail on stderr (not stdout — stdout would pollute
        Claude's context)."""
        result = self._run_capturing_stderr("")
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "empty stdin" in result.stderr
        assert result.stdout == ""

    def test_malformed_json_does_not_block_and_emits_stderr(self, isolated_home):
        """SessionStart hook must never fail-closed on payload corruption —
        a broken hook would prevent the session from starting. Malformed
        JSON is treated as missing session_id."""
        result = self._run_capturing_stderr("not valid json {{")
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert result.stdout == ""

    def test_happy_path_emits_no_stderr(self, isolated_home):
        """Successful runs must be silent — stderr noise on every session
        start would condition the user to ignore it."""
        result = self._run_capturing_stderr(json.dumps({"session_id": "abc-123"}))
        assert result.returncode == 0
        assert len(self._sessions_files(isolated_home)) == 1
        assert result.stderr == ""


# ---------------------------------------------------------------------------
# ask-review-permissions.sh
# ---------------------------------------------------------------------------


class TestAskReviewPermissions:
    @pytest.mark.parametrize(
        "tool_input",
        [
            edit_input("/some/project/.claude/settings.json"),
            edit_input("/some/project/.claude/settings.local.json"),
            write_input("/some/project/.claude/settings.json"),
        ],
        ids=["edit-settings", "edit-settings-local", "write-settings"],
    )
    def test_settings_edits_ask(self, tool_input):
        assert run_hook(REVIEW_PERMS_HOOK, tool_input) == "ask"

    @pytest.mark.parametrize(
        "path",
        [
            "/some/project/package.json",
            "/some/project/.claude/CLAUDE.md",
            "/some/project/.claude/skills/foo.md",
        ],
    )
    def test_non_settings_paths_allowed(self, path):
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(path)) == "allow"

    def test_bash_tool_allowed(self):
        assert run_hook(REVIEW_PERMS_HOOK, bash_input("cat /some/project/.claude/settings.json")) == "allow"


# ---------------------------------------------------------------------------
# deny-private-project-refs.sh
# ---------------------------------------------------------------------------
#
# Fake placeholders used in these tests — chosen to be obviously synthetic
# so the test file itself doesn't violate the rule it's testing:
#   WIDGET-123, FOOCORP-42, NULLPROJ-999, EXAMPLECO-7, BARCORP-22, FAKEPROJ-42
# All six prefixes are invented; none correspond to a real tracker that
# any known organization uses. The hook's allowlist matches real OSS
# reference prefixes only (CVE / RFC / PEP / ISO / GH / BUG / IETF).


@pytest.fixture
def claude_config_repo(git_repo):
    """git_repo with a `claude-config`-shaped origin URL so the scoping
    check lets the redaction gate run. The hook short-circuits on any
    repo whose origin URL doesn't contain `claude-config`, so this fixture
    is required for any test that expects deny behavior."""
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:jcdendrite/claude-config.git"],
        cwd=git_repo,
        check=True,
    )
    return git_repo


@pytest.fixture
def unrelated_remote_repo(git_repo):
    """git_repo with an origin URL that does NOT match claude-config.
    Used to verify the scoping short-circuit: the hook must let commits
    through in every repo other than claude-config, regardless of diff
    content or commit message."""
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someone/unrelated-app.git"],
        cwd=git_repo,
        check=True,
    )
    return git_repo


class TestDenyPrivateProjectRefs:
    @pytest.fixture(autouse=True)
    def _isolate_home_for_blocklist(self, monkeypatch, tmp_path):
        """Isolate $HOME for the entire class so the developer's real
        ~/.claude/private-projects.md never bleeds into tests.

        Without this, a developer with "the parser" or any other
        generic substring in their real blocklist could fail tests
        like test_clean_commit_message_allowed nondeterministically.
        Subprocess inherits this monkeypatched env (run_hook doesn't
        override it), so the hook reads the isolated $HOME at
        runtime.
        """
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        return home

    @pytest.fixture
    def private_projects_file(self, _isolate_home_for_blocklist):
        """Writer for ~/.claude/private-projects.md inside the
        isolated $HOME established by the autouse fixture above.

        Returns a function that takes the file's content (a string)
        and writes it. Tests that don't call this writer get a
        nonexistent blocklist file (the fail-open path)."""
        home = _isolate_home_for_blocklist
        blocklist = home / ".claude" / "private-projects.md"

        def _write(content: str) -> Path:
            blocklist.write_text(content)
            return blocklist

        return _write

    def test_non_commit_command_allowed(self, claude_config_repo):
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input("git status"), cwd=claude_config_repo) == "allow"

    def test_non_git_command_allowed(self, claude_config_repo):
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input("echo WIDGET-123"), cwd=claude_config_repo) == "allow"

    def test_clean_commit_message_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Refactor the parser'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "Fix CVE-2024-12345",
            "Map to CWE-79",
            "Apply PEP-8 formatting",
            "Per RFC-7231 section 6.5",
            "Address GH-123 from upstream",
            "Fix BUG-4242 in parser",
            "Reference ISO-8601 dates",
            "Per IETF-draft handling",
            "Conform to W3C-REC",
            "Map to NIST-800-53",
            "Per ECMA-262",
            "Per ANSI-89 spec",
            "Implement JEP-394",
            "Fix JDK-12345",
            "Upstream LLVM-123",
            "GCC-456 workaround",
            "Require SHA-256",
            "Deprecate MD-5",
            "Support HTTP-2",
            "Disable TLS-1",
            "See PROJ-123 for the placeholder convention",
            "See TICKET-456 for the placeholder convention",
        ],
        ids=[
            "cve", "cwe", "pep", "rfc", "gh", "bug", "iso", "ietf",
            "w3c", "nist", "ecma", "ansi", "jep", "jdk", "llvm", "gcc",
            "sha", "md", "http", "tls",
            "proj_placeholder", "ticket_placeholder",
        ],
    )
    def test_allowlisted_references_allowed(self, claude_config_repo, message):
        assert (
            run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(f"git commit -m '{message}'"), cwd=claude_config_repo)
            == "allow"
        )

    def test_synthetic_tracker_id_in_message_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix WIDGET-123 regression'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "Fix MYPROJ-7 regression",
            "Address SUPERTICKET-1 review",
            "Bump BIGPROJ-99 dep",
            "Land OURTICKET-42 follow-up",
        ],
        ids=["myproj", "superticket", "bigproj", "ourticket"],
    )
    def test_placeholder_prefix_substring_still_denied(self, claude_config_repo, message):
        """Anchor (`^`) on OSS_ALLOWLIST must keep prefixes that *contain*
        but don't *equal* PROJ / TICKET in the deny path. Without this
        test, a refactor that drops the anchor would pass CI silently."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"git commit -m '{message}'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_multiple_tracker_ids_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Handle FOOCORP-42 and BARCORP-22'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_tracker_id_in_staged_diff_denied(self, claude_config_repo):
        """Hook must scan staged content, not just the command string."""
        (claude_config_repo / "file.txt").write_text("first\nsecond\n// NULLPROJ-999 fixed\n")
        subprocess.run(["git", "add", "file.txt"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Generic refactor'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_mixed_allowed_and_suspect_denied(self, claude_config_repo):
        """A CVE plus a project-looking token: still deny on the project token."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix CVE-2024-1234 via EXAMPLECO-7 changes'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_heredoc_commit_message_scanned(self, claude_config_repo):
        """Heredoc-style commit messages get scanned via the command string."""
        cmd = (
            "git commit -m \"$(cat <<'EOF'\n"
            "Subject line\n"
            "\n"
            "Body referencing FOOCORP-12 incident\n"
            "EOF\n"
            ")\""
        )
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(cmd), cwd=claude_config_repo) == "deny"

    def test_lowercase_token_allowed(self, claude_config_repo):
        """Lowercase `widget-123` doesn't match the uppercase-only regex.

        Ticket IDs are conventionally uppercase; a lowercase hyphenated
        token is more likely to be a package name or slug, not a tracker
        reference. Explicitly allowed to avoid false positives on common
        code patterns.
        """
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix widget-123 styling'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_with_suspect_token_denied(self, claude_config_repo):
        """Chained `git add && git commit` is still gated by this hook."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git add . && git commit -m 'Fix WIDGET-1 issue'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_removing_a_tracker_id_is_allowed(self, claude_config_repo):
        """A redaction commit that *removes* a tracker ID must not be blocked.

        If the hook scanned removed lines, the staged deletion of a token
        would match and block the cleanup itself — making the hook hostile
        to its own maintenance flow.
        """
        # Seed a committed file that already contains a suspect token.
        (claude_config_repo / "legacy.txt").write_text("Old notes about WIDGET-999.\n")
        subprocess.run(["git", "add", "legacy.txt"], cwd=claude_config_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=claude_config_repo, check=True)
        # Now stage a deletion of the token — the diff contains `-WIDGET-999`.
        (claude_config_repo / "legacy.txt").write_text("Old notes.\n")
        subprocess.run(["git", "add", "legacy.txt"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Redact legacy notes'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_empty_staged_diff_allows_commit(self, claude_config_repo):
        """No staged changes — let git decide (empty-commit, amend, etc.).

        Even though the command mentions a suspect token, there is no new
        content being introduced; the hook shouldn't block an amend-only
        or --allow-empty flow.
        """
        subprocess.run(["git", "reset", "HEAD"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Refers to WIDGET-123 but nothing staged'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    # -- Scoping ------------------------------------------------------------
    # Regression: the hook originally had no repo-identity check and fired
    # on every `git commit` in every repo where the user had this config
    # installed. It blocked legitimate tracker IDs in the user's own
    # projects that happened to match `[A-Z]{2,}-\d+`. The gate must only
    # activate in the claude-config repo, where accidental references to
    # private projects would leak publicly.

    def test_unrelated_remote_suspect_token_allowed(self, unrelated_remote_repo):
        """A suspect tracker ID in a repo whose origin URL does NOT contain
        `claude-config` must pass — it's the repo's own legitimate ID."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix WIDGET-123 regression'"),
                cwd=unrelated_remote_repo,
            )
            == "allow"
        )

    def test_unrelated_remote_suspect_token_in_diff_allowed(self, unrelated_remote_repo):
        """Scoping must also short-circuit the staged-diff scan, not just
        the commit-message scan."""
        (unrelated_remote_repo / "file.txt").write_text("first\nsecond\n// WIDGET-123 fixed\n")
        subprocess.run(["git", "add", "file.txt"], cwd=unrelated_remote_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix regression'"),
                cwd=unrelated_remote_repo,
            )
            == "allow"
        )

    def test_no_remote_suspect_token_allowed(self, git_repo):
        """A repo with no `origin` remote configured (brand-new `git init`)
        must short-circuit cleanly via the substring check against an empty
        string. `git config --get` returns empty (not an error code) on a
        missing key, so the `*claude-config*` match falls through to exit 0."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix WIDGET-123 regression'"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_claude_config_fork_origin_still_gates(self, git_repo):
        """Substring match on `claude-config` is deliberately loose: a fork
        whose URL is `.../someone-else/claude-config.git` should still be
        gated, because the redaction concerns apply to any clone of this
        public repo."""
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:forker/claude-config.git"],
            cwd=git_repo,
            check=True,
        )
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Fix WIDGET-123 regression'"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_test_dir_changes_exempt_from_scan(self, claude_config_repo):
        """The hook's own test directory is excluded from the staged-diff
        scan. Without this, every commit that adds a new test case to this
        file would trip the hook on its own synthetic test data — making
        the hook hostile to its own test-authoring flow.

        Guard scope: exemption applies only to `claude/.claude/hooks/tests/**`,
        not to any other directory, and not to the commit-message string
        itself. See test_tracker_id_in_staged_diff_denied for the complement."""
        test_dir = claude_config_repo / "claude" / ".claude" / "hooks" / "tests"
        test_dir.mkdir(parents=True)
        # A new test case authored inside the hook's own test file, with
        # a fresh synthetic tracker token that is NOT on the allowlist.
        (test_dir / "test_new_case.py").write_text(
            'def test_x():\n'
            '    bash_input("git commit -m FAKEPROJ-42")\n'
        )
        subprocess.run(["git", "add", "claude/.claude/hooks/tests/test_new_case.py"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Add new hook test case'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_test_dir_exemption_does_not_mask_non_test_file(self, claude_config_repo):
        """The test-dir exemption is narrow: a fake token in a *non-test*
        file, staged alongside a test-dir change, still blocks the commit.
        Guard against an accidental over-broad pathspec."""
        test_dir = claude_config_repo / "claude" / ".claude" / "hooks" / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_new_case.py").write_text('bash_input("FAKEPROJ-42")\n')
        # Non-test file at repo root with the same synthetic token.
        (claude_config_repo / "other.txt").write_text("Touches FAKEPROJ-42 unexpectedly\n")
        subprocess.run(
            ["git", "add", "claude/.claude/hooks/tests/test_new_case.py", "other.txt"],
            cwd=claude_config_repo,
            check=True,
        )
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Mixed change'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_scoping_reason_message_still_present_when_blocked(self, claude_config_repo):
        """The deny reason shown to the user must still reference the
        `Redact private-project-identifying content` section so reviewers know where
        to look. Guard against an accidental message change during scoping
        refactors."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input("git commit -m 'Fix WIDGET-123 regression'")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Commit blocked by redaction gate" in reason
        assert "Redact private-project-identifying content" in reason
        assert "WIDGET-123" in reason

    # -- gh pr create / gh pr edit surfaces --------------------------------
    # Regression: a prior PR in this repo leaked a tracker ID via
    # `gh pr create --body-file` because the hook originally gated only
    # `git commit`. PR bodies, titles, and body-file contents are now
    # in scope too.

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create --body 'Fixes WIDGET-123'",
            "gh pr create --title 'Fix WIDGET-123'",
            "gh pr edit 42 --title 'Fix WIDGET-123'",
            "gh pr edit 42 --body 'Fixes WIDGET-123'",
            "echo prep && gh pr create --body 'has WIDGET-123'",
        ],
        ids=[
            "create-body-inline",
            "create-title-inline",
            "edit-title-inline",
            "edit-body-inline",
            "chained-after-echo",
        ],
    )
    def test_gh_pr_inline_tracker_denied(self, claude_config_repo, command):
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_gh_pr_create_body_file_with_tracker_denied(self, claude_config_repo, tmp_path):
        """The canonical leak pattern: --body-file pointing at a file whose
        contents never appear in the command string. The hook must read
        and scan the file, not just the command."""
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("## Summary\n\nFixes FOOCORP-42 regression.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr create --body-file {body_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_pr_create_body_file_equals_form_denied(self, claude_config_repo, tmp_path):
        """Equals form `--body-file=<path>` must parse identically to the
        space-delimited form."""
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("Refs NULLPROJ-999.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr create --body-file={body_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_pr_edit_body_file_with_tracker_denied(self, claude_config_repo, tmp_path):
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("Updated scope: addresses EXAMPLECO-7.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr edit 42 --body-file {body_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create --body 'Fixes CVE-2024-9999'",
            "gh pr create --body 'Clean body, no refs at all'",
            "gh pr create --title 'Refactor parser'",
            "gh pr edit 42 --state merged",
            "gh pr edit 42 --add-label needs-review",
            "gh pr edit 42 --add-reviewer alice",
        ],
        ids=[
            "create-body-cve-allowlisted",
            "create-body-clean",
            "create-title-clean",
            "edit-state-flag",
            "edit-label-flag",
            "edit-reviewer-flag",
        ],
    )
    def test_gh_pr_clean_or_allowlisted_allowed(self, claude_config_repo, command):
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "allow"

    def test_gh_pr_body_file_allowlisted_only_allowed(self, claude_config_repo, tmp_path):
        """A body file that references only allowlisted tokens passes."""
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("Implements RFC-7231 and mitigates CVE-2024-1234.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr create --body-file {body_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_pr_body_file_missing_fails_closed(self, claude_config_repo, tmp_path):
        """Nonexistent --body-file path: hook must deny, not silently treat
        as empty. Unscanned content is exactly the leak vector this hook
        guards against, so the fail-closed branch is load-bearing."""
        missing = tmp_path / "does-not-exist.md"
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"gh pr create --body-file {missing}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict on unreadable body-file"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "body-source file" in reason
        assert str(missing) in reason

    def test_gh_pr_unrelated_remote_allowed(self, unrelated_remote_repo):
        """Scoping short-circuit (origin URL doesn't contain `claude-config`)
        must apply to gh pr too — the hook must not block PRs in any other
        repo even if they reference a tracker ID."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh pr create --body 'Fix WIDGET-123 regression'"),
                cwd=unrelated_remote_repo,
            )
            == "allow"
        )

    def test_non_gated_gh_subcommand_allowed(self, claude_config_repo):
        """Only `gh pr create` and `gh pr edit` are gated. Other gh subcommands
        that might carry text (e.g., `gh pr comment`) are out of scope for
        this hook and must pass."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh pr comment 42 --body 'has WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    # -- Short-form and template body sources ------------------------------
    # Regression: the initial implementation only handled the long-form
    # --body-file flag. `gh pr create -F <path>` is documented as the short
    # form of --body-file and is the exact same leak vector. `--template`
    # / `-T` is a separate gh-documented body-text source that also needs
    # scanning. Missing any of these means the plan's stated goal (close
    # PR-body leak vectors in gh pr create/edit) is not actually met.

    @pytest.mark.parametrize(
        "flag_form",
        ["-F", "-F="],
        ids=["dash-F-space", "dash-F-equals"],
    )
    def test_gh_pr_short_F_flag_with_tracker_denied(self, claude_config_repo, tmp_path, flag_form):
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("Fixes BARCORP-22.\n")
        separator = "" if flag_form.endswith("=") else " "
        cmd = f"gh pr create {flag_form}{separator}{body_file}"
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(cmd), cwd=claude_config_repo) == "deny"

    @pytest.mark.parametrize(
        "flag_form",
        ["--template", "--template=", "-T", "-T="],
        ids=["long-space", "long-equals", "short-space", "short-equals"],
    )
    def test_gh_pr_template_flag_with_tracker_denied(self, claude_config_repo, tmp_path, flag_form):
        template = tmp_path / "pr-template.md"
        template.write_text("## Starting template\n\nLeaked NULLPROJ-999 goes here.\n")
        separator = "" if flag_form.endswith("=") else " "
        cmd = f"gh pr create {flag_form}{separator}{template}"
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(cmd), cwd=claude_config_repo) == "deny"

    def test_gh_pr_template_clean_allowed(self, claude_config_repo, tmp_path):
        """Template flag with only allowlisted refs must pass — the scan
        treats template content identically to --body-file content."""
        template = tmp_path / "pr-template.md"
        template.write_text("Follows RFC-7231 section 6.5.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr create --template {template}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    # -- Pseudo-file paths fail closed -------------------------------------
    # `--body-file=/dev/stdin` / `--body-file=-` would cause the hook's
    # `cat` to read the hook's OWN stdin (the tool-input JSON), while gh
    # would read its own different stdin at invocation time. The mismatch
    # is a bypass. Same for `/dev/fd/N` and `/proc/*/fd/N` — process-local
    # fd references that the hook cannot resolve to gh's future state.

    @pytest.mark.parametrize(
        "pseudo_path",
        ["-", "/dev/stdin", "/dev/fd/1", "/proc/self/fd/0"],
        ids=["bare-dash", "dev-stdin", "dev-fd", "proc-fd"],
    )
    def test_gh_pr_pseudo_file_body_source_denied(self, claude_config_repo, pseudo_path):
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"gh pr create --body-file={pseudo_path}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), f"expected deny on pseudo-file path {pseudo_path}"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "pseudo-file" in reason.lower()

    # -- Fail-closed on malformed input ------------------------------------
    # jq parse failure must deny, not silently allow. Without this, a
    # broken jq binary (or malformed JSON from the harness) would disable
    # the gate entirely — the worst possible failure mode for a hook
    # whose purpose is to prevent a leak.

    def test_malformed_json_stdin_denies(self, claude_config_repo):
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input="not valid json{",
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny on malformed JSON input"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    # -- Allow-path lock-ins for load-bearing existing behaviors -----------
    # The refactor that added gh pr coverage also restructured the git-
    # commit branch. These tests lock in the behaviors that must survive
    # future refactors: equals-form body-file passes when clean, amend-
    # message-only passes even with a tracker in the message (historical
    # exit-0 on empty staged diff), and the test-dir pathspec exclusion
    # holds on the added side of the diff.

    def test_gh_pr_equals_form_clean_body_file_allowed(self, claude_config_repo, tmp_path):
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("Refactor parser, no tracker refs.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr create --body-file={body_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_amend_message_only_with_tracker_allowed(self, claude_config_repo):
        """Historical behavior: empty staged diff + tracker in message -> allow.
        Reason at lines 119-123 of the hook: `--amend` / `--allow-empty` /
        nothing staged has no new content, so the gate lets git decide.
        A refactor that reorders the staged-diff check and the command-
        string scan must not regress this."""
        subprocess.run(["git", "reset", "HEAD"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit --amend -m 'Fix WIDGET-123 regression'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_test_dir_pathspec_exclusion_allow_path_locked_in(self, claude_config_repo):
        """Mirror of test_test_dir_changes_exempt_from_scan, framed as the
        allow-path pair for the exclusion behavior. Adding a synthetic
        tracker inside the hook's own test tree must pass; without the
        pathspec exclusion, every new test case commit would be blocked
        by the hook under test — hostile to its own maintenance flow."""
        test_dir = claude_config_repo / "claude" / ".claude" / "hooks" / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_another_case.py").write_text(
            "# synthetic token for testing: FAKEPROJ-777\n"
        )
        subprocess.run(
            ["git", "add", "claude/.claude/hooks/tests/test_another_case.py"],
            cwd=claude_config_repo,
            check=True,
        )
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Add test'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    # -- User-local private-projects blocklist -----------------------------
    # Second mechanical defense alongside the tracker-ID scan. Reads
    # ~/.claude/private-projects.md as a literal, case-insensitive
    # substring blocklist. Fails open if the file is absent or unreadable.
    # Critical invariant: the deny message NEVER names the matched entry.

    def test_blocklist_match_in_commit_message_denied(self, claude_config_repo, private_projects_file):
        private_projects_file("Acme Corp\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Working on Acme Corp integration'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_match_case_insensitive_denied(self, claude_config_repo, private_projects_file):
        """Blocklist entry `Initech`; commit has lowercase `initech`."""
        private_projects_file("Initech\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Migrate initech config to new schema'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_match_multi_word_entry_denied(self, claude_config_repo, private_projects_file):
        """Multi-word entries match — line-by-line read, not word-split."""
        private_projects_file("Project Bluebird\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Update project bluebird notes'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_match_in_gh_pr_inline_body_denied(self, claude_config_repo, private_projects_file):
        private_projects_file("Acme Corp\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh pr create --body 'Refactor for Acme Corp release'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_match_in_gh_pr_body_file_denied(self, claude_config_repo, private_projects_file, tmp_path):
        """Blocklist applies to body-file content, not just the inline command."""
        private_projects_file("Acme Corp\n")
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("## Summary\n\nAcme Corp integration polish.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh pr create --body-file {body_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_match_in_staged_diff_denied(self, claude_config_repo, private_projects_file):
        """Added lines in the staged diff are scanned against the blocklist."""
        private_projects_file("Acme Corp\n")
        (claude_config_repo / "file.txt").write_text("first\nsecond\n# Acme Corp section\n")
        subprocess.run(["git", "add", "file.txt"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Generic refactor'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_comments_and_blanks_ignored(self, claude_config_repo, private_projects_file):
        """File with `#` comments and blank lines + a real entry must
        skip the noise and still match on the real entry."""
        private_projects_file("# Engagements\n\n# More\nAcme Corp\n\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Working on Acme Corp release'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_entry_whitespace_trimmed(self, claude_config_repo, private_projects_file):
        """Leading/trailing whitespace on a blocklist line is stripped
        before matching, so a stray indent doesn't silently disable the entry."""
        private_projects_file("   Acme Corp   \n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Working on Acme Corp release'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_blocklist_absent_allows(self, claude_config_repo):
        """No ~/.claude/private-projects.md → fail-open. Existing behavior
        for users who haven't opted in must be unchanged."""
        # The autouse fixture leaves $HOME without a blocklist file.
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Working on Acme Corp release'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_blocklist_only_comments_and_blanks_allows(self, claude_config_repo, private_projects_file):
        """File exists but has no usable entries → fail-open."""
        private_projects_file("# Just a header\n\n# Nothing real\n\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Working on Acme Corp release'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_blocklist_no_match_allows(self, claude_config_repo, private_projects_file):
        private_projects_file("Acme Corp\nProject Bluebird\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Refactor the parser module'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_blocklist_unrelated_remote_short_circuits(self, unrelated_remote_repo, private_projects_file):
        """The blocklist scan must respect the same origin.url short-
        circuit as the tracker-ID scan. A repo that isn't claude-config
        gets no scanning at all, even if the content matches a blocklist
        entry — the user's blocklist is for THEIR private projects, but
        the gate only fires in this public repo."""
        private_projects_file("Acme Corp\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Working on Acme Corp release'"),
                cwd=unrelated_remote_repo,
            )
            == "allow"
        )

    def test_blocklist_removed_line_in_diff_allows(self, claude_config_repo, private_projects_file):
        """Removing a blocklisted name in the staged diff is the legitimate
        cleanup flow — the hook must not block it. Mirror of
        test_removing_a_tracker_id_is_allowed."""
        private_projects_file("Acme Corp\n")
        # Seed: file with the name committed.
        (claude_config_repo / "legacy.txt").write_text("Old notes about Acme Corp.\n")
        subprocess.run(["git", "add", "legacy.txt"], cwd=claude_config_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=claude_config_repo, check=True)
        # Stage the removal — diff has `-Old notes about Acme Corp.`
        # which is NOT in ADDED_LINES, and the commit message is generic.
        (claude_config_repo / "legacy.txt").write_text("Old notes.\n")
        subprocess.run(["git", "add", "legacy.txt"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Redact legacy notes'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_blocklist_substring_within_word_does_not_match(self, claude_config_repo, private_projects_file):
        """Whole-word match: `Pulse` blocklist entry must NOT match
        `impulse` in a commit message — `impulse` is one word, no
        boundary at the `Pulse` substring. This is the load-bearing
        false-positive avoidance that motivated whole-word matching."""
        private_projects_file("Pulse\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Add impulse handler for events'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_blocklist_concatenated_identifier_does_not_match(self, claude_config_repo, private_projects_file):
        """Whole-word match: `AcmeCorp` does NOT match `AcmeCorpService`.
        The trailing `S` is a word character so no boundary exists
        after `AcmeCorp`. Documented behavior — users who need to
        catch concatenated forms add the concatenated form as its own
        blocklist entry."""
        private_projects_file("AcmeCorp\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Refactor AcmeCorpService auth flow'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_blocklist_match_at_punctuation_boundary(self, claude_config_repo, private_projects_file):
        """Whole-word match: punctuation is a non-word boundary. So
        `AcmeCorp` matches `AcmeCorp.` (period), `AcmeCorp,` (comma),
        and `AcmeCorp's` (apostrophe before non-word `s`-content...
        wait, `'` is non-word so `\\bAcmeCorp\\b` matches before the
        apostrophe). Verifies the common case where the project name
        appears at the end of a sentence or in possessive form."""
        private_projects_file("AcmeCorp\n")
        for punct_form in ["Working with AcmeCorp.", "AcmeCorp's release notes", "Refactor for AcmeCorp, finally"]:
            assert (
                run_hook(
                    DENY_PRIVATE_PROJECT_REFS_HOOK,
                    bash_input(f"git commit -m '{punct_form}'"),
                    cwd=claude_config_repo,
                )
                == "deny"
            ), f"expected deny for {punct_form!r}"

    def test_blocklist_deny_message_does_not_name_entry(self, claude_config_repo, private_projects_file):
        """LOAD-BEARING: the deny message must NOT echo the matched entry.

        Echoing a name the user explicitly flagged as sensitive would
        re-expose it in terminal output, screenshots, CI logs, and
        Claude's own conversation context — exactly the surfaces this
        gate exists to protect. This invariant is documented in the
        hook header and must hold across refactors.
        """
        private_projects_file("Acme Corp\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input("git commit -m 'Working on Acme Corp release'")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]

        # Bright-line: no case variant of the matched entry appears.
        assert "Acme Corp" not in reason
        assert "acme corp" not in reason.lower()

        # Lock in the explanation so a refactor that drops it fails fast.
        assert "deliberately does not name which entry matched" in reason

        # Sanity: the user is pointed at their own blocklist file.
        assert "private-projects.md" in reason

    # -- git commit -F / --file commit-message-source files ----------------
    # Parallel to gh pr's --body-file: the commit-message file's contents
    # never appear in the command string. Without this scan, a tracker
    # token in the file slips through the same way it slipped through
    # gh pr --body-file before that hole was closed.

    def test_git_commit_F_flag_with_tracker_denied(self, claude_config_repo, tmp_path):
        """The canonical -F leak pattern: -F pointing at a file whose
        contents never appear in the command string. The hook must read
        and scan the file, not just the command."""
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("Subject\n\nBody mentioning WIDGET-123 incident.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"git commit -F {msg_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_git_commit_F_flag_clean_message_allowed(self, claude_config_repo, tmp_path):
        """Tracker-clean -F file: scan reads the file, finds nothing,
        passes. Lock-in for the allow path."""
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("Refactor parser to use streaming reads.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"git commit -F {msg_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_git_commit_long_file_form_with_tracker_denied(self, claude_config_repo, tmp_path):
        """Long form `--file=<path>` parses identically to `-F`."""
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("Land FOOCORP-42 follow-up.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"git commit --file={msg_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_git_commit_m_clean_F_with_tracker_still_denied(self, claude_config_repo, tmp_path):
        """`git commit -m "msg" -F <file>` — git concatenates both as
        the commit message. A clean -m must NOT mask a tracker in the
        -F file."""
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("Trailing reference: NULLPROJ-999.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"git commit -m 'Subject is clean' -F {msg_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "pseudo_path",
        ["-", "/dev/stdin", "/dev/fd/1", "/proc/self/fd/0"],
        ids=["bare-dash", "dev-stdin", "dev-fd", "proc-fd"],
    )
    def test_git_commit_F_pseudo_file_denied(self, claude_config_repo, pseudo_path):
        """Pseudo-file paths can't be statically scanned: '-' / '/dev/stdin'
        / '/dev/fd/*' / '/proc/*/fd/*' resolve to the hook's stdin or a
        process-specific fd, not git's future stdin. Same fail-closed
        posture as gh pr's pseudo-file branch."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"git commit -F {pseudo_path}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), f"expected deny on pseudo-file path {pseudo_path}"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "pseudo-file" in reason.lower()

    def test_git_commit_F_unreadable_path_fails_closed(self, claude_config_repo, tmp_path):
        """Nonexistent -F path: hook denies with a recognizable reason.
        Unscanned content is exactly the leak vector this hook guards
        against, so fail-closed is load-bearing."""
        missing = tmp_path / "does-not-exist.txt"
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"git commit -F {missing}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny on unreadable -F path"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "message-source file" in reason
        assert str(missing) in reason

    def test_git_commit_F_allowlisted_token_passes(self, claude_config_repo, tmp_path):
        """OSS_ALLOWLIST tokens (CVE / RFC / etc.) in a -F file pass.
        Cross-cutting check that the new scan path inherits the same
        allowlist, not a parallel hardcoded one."""
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("Implements RFC-9110 section 9.3.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"git commit -F {msg_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_git_commit_F_blocklist_match_denied_with_generic_message(
        self, claude_config_repo, private_projects_file, tmp_path,
    ):
        """User-local blocklist must apply to -F file content. Deny
        message must NOT name the matched entry — the generic-message-
        only invariant is load-bearing on the new scan path too."""
        private_projects_file("Acme Corp\n")
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("Polish Acme Corp release flow.\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"git commit -F {msg_file}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny on blocklist match in -F file"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" not in reason
        assert "acme corp" not in reason.lower()
        assert "deliberately does not name which entry matched" in reason

    # -- gh api mutating-call surfaces -------------------------------------
    # `gh api repos/.../pulls/N/comments`, `.../comments/M/replies`,
    # `.../issues/N/comments`, etc. carry user-authored bodies via
    # `-f body=` / `-F body=` field flags or via `--input <path>`. None
    # of these were previously dispatched to this hook because the
    # dispatcher only matched `gh pr (create|edit)`. Defaults-to-GET
    # reads are not gated; only POST / PATCH / PUT / DELETE.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/x/y/pulls/1/comments -X POST -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1/comments/2/replies -X POST -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/issues/1/comments -X POST -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1/reviews -X POST -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1 -X PATCH -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1 -X PUT -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1/comments/2 -X DELETE -f body='Trailing WIDGET-123 in audit log'",
        ],
        ids=[
            "post-pr-review-comment",
            "post-review-thread-reply",
            "post-issue-comment",
            "post-pr-review",
            "patch-pr",
            "put-pr",
            "delete-with-body",
        ],
    )
    def test_gh_api_mutating_inline_body_with_tracker_denied(self, claude_config_repo, command):
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_gh_api_method_long_form_with_tracker_denied(self, claude_config_repo):
        """`--method POST` is the long form of `-X POST`; the dispatch
        must match both."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments --method POST -f body='Fixes WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_method_equals_form_with_tracker_denied(self, claude_config_repo):
        """`--method=POST` (equals form) parses identically."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments --method=POST -f body='Fixes WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_X_equals_form_with_tracker_denied(self, claude_config_repo):
        """`-X=POST` (equals form on short flag) parses identically."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments -X=POST -f body='Fixes WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_default_get_not_dispatched(self, claude_config_repo):
        """Default method is GET — read-only calls don't carry user
        content and are intentionally not gated. Without this allow,
        every `gh api repos/...` read would pay the hook cost."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_explicit_get_with_tracker_in_query_allowed(self, claude_config_repo):
        """An explicit `-X GET` is still a read; not gated. A tracker
        token appearing in the URL or query string of a GET passes,
        because GET requests don't author content into anything the
        receiver re-publishes."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api 'repos/x/y/issues?labels=WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_post_clean_body_allowed(self, claude_config_repo):
        """Mutating call with a clean body: dispatch fires, scan finds
        nothing, allow. Lock-in for the allow path on the new branch."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments -X POST -f body='Looks good, shipping.'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_post_input_file_with_tracker_denied(self, claude_config_repo, tmp_path):
        """`--input <path>` reads a JSON body from a file. The hook
        must read the file and scan it, not just the command string."""
        body_file = tmp_path / "comment.json"
        body_file.write_text('{"body": "Fixes EXAMPLECO-7 incident"}\n')
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input {body_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_post_input_equals_form_with_tracker_denied(self, claude_config_repo, tmp_path):
        """`--input=<path>` (equals form) parses identically."""
        body_file = tmp_path / "comment.json"
        body_file.write_text('{"body": "Fixes BARCORP-22"}\n')
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input={body_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_post_input_file_clean_allowed(self, claude_config_repo, tmp_path):
        """Tracker-clean --input file passes."""
        body_file = tmp_path / "comment.json"
        body_file.write_text('{"body": "Looks good."}\n')
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input {body_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_post_input_allowlisted_token_passes(self, claude_config_repo, tmp_path):
        """OSS_ALLOWLIST tokens in --input file content pass — the new
        scan path inherits the same allowlist."""
        body_file = tmp_path / "comment.json"
        body_file.write_text('{"body": "Implements RFC-7231 section 6.5"}\n')
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input {body_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "pseudo_path",
        ["-", "/dev/stdin", "/dev/fd/1", "/proc/self/fd/0"],
        ids=["bare-dash", "dev-stdin", "dev-fd", "proc-fd"],
    )
    def test_gh_api_post_input_pseudo_file_denied(self, claude_config_repo, pseudo_path):
        """Pseudo-file --input paths fail closed, same posture as the
        gh-pr body-source pseudo-file branch."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input={pseudo_path}")
            ),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), f"expected deny on pseudo-file path {pseudo_path}"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "pseudo-file" in reason.lower()

    def test_gh_api_post_input_unreadable_path_fails_closed(self, claude_config_repo, tmp_path):
        """Nonexistent --input path: deny with recognizable reason."""
        missing = tmp_path / "does-not-exist.json"
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input {missing}")
            ),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny on unreadable --input path"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "--input file" in reason
        assert str(missing) in reason

    def test_gh_api_unrelated_remote_allowed(self, unrelated_remote_repo):
        """Scoping short-circuit applies to gh api too — the hook must
        not block API calls in any other repo even on mutating writes."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments -X POST -f body='Fixes WIDGET-123'"),
                cwd=unrelated_remote_repo,
            )
            == "allow"
        )

    def test_gh_api_blocklist_match_in_input_file_denied_with_generic_message(
        self, claude_config_repo, private_projects_file, tmp_path,
    ):
        """User-local blocklist applies to --input file content too,
        with the generic-message-only invariant preserved."""
        private_projects_file("Acme Corp\n")
        body_file = tmp_path / "comment.json"
        body_file.write_text('{"body": "Acme Corp release polish"}\n')
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST --input {body_file}")
            ),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny on blocklist match in --input file"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" not in reason
        assert "acme corp" not in reason.lower()
        assert "deliberately does not name which entry matched" in reason

    # -- gh api implicit POST and `@<path>` field-from-file bypass paths ---
    # Two bypasses surfaced in security review: (1) `gh api` auto-promotes
    # to POST whenever any -f / -F / --field / --raw-field / --input flag
    # is supplied, so requiring an explicit -X POST in dispatch let
    # `gh api foo -f body=WIDGET-123` ship unscanned; (2) the `key=@<path>`
    # field-value form reads file contents at gh-invocation time, so
    # `-F body=@/tmp/leak.txt` carried tracker tokens into the request
    # body without ever appearing in the command string.

    @pytest.mark.parametrize(
        "command",
        [
            # No -X at all — gh auto-POSTs because -f is present.
            "gh api repos/x/y/issues/1/comments -f body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1/comments -F body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1/comments --field body='Fixes WIDGET-123'",
            "gh api repos/x/y/pulls/1/comments --raw-field body='Fixes WIDGET-123'",
            # --input alone (no -X) also auto-POSTs.
            "gh api repos/x/y/pulls/1/comments --input /dev/null && gh api repos/x/y/pulls/1/comments -f body='WIDGET-123'",
        ],
        ids=[
            "implicit-post-via-f",
            "implicit-post-via-F",
            "implicit-post-via-long-field",
            "implicit-post-via-raw-field",
            "implicit-post-chained",
        ],
    )
    def test_gh_api_implicit_post_with_tracker_denied(self, claude_config_repo, command):
        """gh api auto-POSTs when any field flag is present even
        without -X. The dispatch must catch this — explicit-method
        gating alone leaves a real bypass."""
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_gh_api_implicit_post_clean_body_allowed(self, claude_config_repo):
        """Implicit-POST dispatch fires, scan finds nothing, allow.
        Lock-in for the allow path on the implicit-POST branch."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments -f body='Looks good, shipping.'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_XPOST_concatenated_with_tracker_denied(self, claude_config_repo):
        """gh accepts `-XPOST` with no separator (cobra/pflag short-flag
        concatenation). Dispatch must match this form too — requiring
        `-X` followed by space or `=` leaves a documented-form bypass."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh api repos/x/y/pulls/1/comments -XPOST -f body='Fixes WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "flag",
        ["-f", "-F", "--field", "--raw-field"],
        ids=["short-f", "short-F", "long-field", "long-raw-field"],
    )
    def test_gh_api_field_at_path_with_tracker_denied(
        self, claude_config_repo, tmp_path, flag,
    ):
        """`-f key=@<path>` and friends read the field value from a
        file at gh-invocation time. Without scanning the file, the
        literal `body=@/tmp/leak.txt` in the command string passes
        the tracker scan trivially while the file content ships."""
        leak_file = tmp_path / "leak.txt"
        leak_file.write_text("Trailing reference: WIDGET-123.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST {flag} body=@{leak_file}"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_field_at_path_clean_allowed(self, claude_config_repo, tmp_path):
        """A tracker-clean @<path> field-file passes."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("Looks good.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST -F body=@{body_file}"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "pseudo_path",
        ["-", "/dev/stdin", "/dev/fd/1", "/proc/self/fd/0"],
        ids=["bare-dash", "dev-stdin", "dev-fd", "proc-fd"],
    )
    def test_gh_api_field_at_pseudo_file_denied(self, claude_config_repo, pseudo_path):
        """`-F body=@-` reads from gh's stdin, which the hook cannot
        statically verify. Same fail-closed posture as --input."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST -F body=@{pseudo_path}")
            ),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), f"expected deny on pseudo-file path {pseudo_path}"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "pseudo-file" in reason.lower()

    def test_gh_api_field_at_unreadable_path_fails_closed(self, claude_config_repo, tmp_path):
        """Nonexistent @<path>: deny with recognizable reason."""
        missing = tmp_path / "does-not-exist.txt"
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(
                bash_input(f"gh api repos/x/y/pulls/1/comments -X POST -F body=@{missing}")
            ),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny on unreadable @<path>"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "field-value file" in reason
        assert str(missing) in reason

    def test_gh_api_chained_after_other_command_with_tracker_denied(self, claude_config_repo):
        """Regression-pin: dispatcher's chain-prefix alternation
        (`(^|&&?|;|\\|\\|?)`) must let `gh api` after a leading echo or
        any other command still fire. A refactor narrowing dispatch to
        `^\\s*gh api` would silently bypass chained mutating calls."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("echo prep && gh api repos/x/y/pulls/1/comments -X POST -f body='Fixes WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # -- Tracker-vs-blocklist priority -------------------------------------
    # Header invariant (hook lines 83-84): "Tracker-ID matches take
    # priority — a commit with both gets the tracker-ID deny message."
    # Without a test pinning this, a refactor reordering the two scans
    # could silently swap which deny message ships, including potentially
    # leaking the matched blocklist entry name from the tracker-ID code
    # path's HIT_LIST echo.

    def test_tracker_id_takes_priority_over_blocklist_match(
        self, claude_config_repo, private_projects_file,
    ):
        """A commit message containing BOTH a tracker token AND a
        blocklist entry must surface the tracker-ID deny message. The
        blocklist entry must NOT appear in the deny output — preserves
        both the documented priority order AND the generic-message-only
        invariant on the blocklist code path."""
        private_projects_file("Acme Corp\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(
                bash_input("git commit -m 'Fix WIDGET-123 in Acme Corp module'")
            ),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected deny verdict"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        # Tracker-ID branch fired (priority): its specific marker phrase.
        assert "Commit blocked by redaction gate" in reason
        assert "WIDGET-123" in reason
        # Blocklist entry must NOT appear — preserves generic-message
        # invariant even when both scans would have matched.
        assert "Acme Corp" not in reason
        assert "acme corp" not in reason.lower()

    # --- Quote-aware flag extraction: false-positive locks ---

    def test_body_source_fp_flag_in_title_allowed(self, claude_config_repo):
        """extract_body_source_paths must not false-positive on '-F' that
        appears inside a quoted --title value. Without the quote-stripping
        fix, this command was blocked by the hook with 'body-source file at
        and'."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "gh pr create"
                    " --title \"close git commit -F and gh api scan gaps\""
                    " --body \"no tracker IDs here\""
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_body_source_file_with_tracker_id_still_denied(
        self, claude_config_repo, tmp_path
    ):
        """Positive control: quote-stripping must not disable extraction of a
        real --body-file flag. A body file containing a tracker token must
        still be caught after the fix."""
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("See WIDGET-123 for context.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f"gh pr create --title \"ordinary title\""
                    f" --body-file {body_file}"
                ),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_commit_msg_source_fp_flag_in_message_allowed(self, claude_config_repo):
        """extract_commit_message_source_paths must not false-positive on
        '-F' that appears inside a quoted -m message value. A clean file is
        staged so the diff-gated branch that calls the extractor is entered."""
        (claude_config_repo / "clean.txt").write_text("some content\n")
        subprocess.run(["git", "add", "clean.txt"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "git commit -m \"refactor: use -F flag for config files\""
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_input_fp_flag_in_field_value_allowed(self, claude_config_repo):
        """extract_gh_api_input_paths must not false-positive on '--input'
        that appears inside a quoted field value. Without quote-stripping,
        the hook would extract the next token after '--input' as a file
        path and fail-close on it."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "gh api -X POST"
                    " -f body=\"see --input flag in the api docs\""
                    " repos/owner/repo/issues"
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_gh_api_field_at_fp_path_in_field_value_allowed(self, claude_config_repo):
        """extract_gh_api_field_at_paths must not false-positive on a
        'key=@path' pattern that appears inside a quoted field value. Without
        quote-stripping, the '-F query=@./schema.json' substring inside the
        body value would be extracted, then the hook would deny because the
        path doesn't exist (fail-closed on unreadable path)."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "gh api -X POST"
                    " -f body=\"pass -F query=@./schema.json for reference\""
                    " repos/owner/repo/issues"
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_body_source_file_clean_still_allowed(self, claude_config_repo, tmp_path):
        """Positive control: a legitimate --body-file with clean content must
        still be extracted and allowed after the quote-stripping fix (i.e.
        the strip does not disable extraction of real flags outside quotes)."""
        body_file = tmp_path / "clean-body.md"
        body_file.write_text("This PR fixes the login flow. No tracker IDs.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f"gh pr create --title \"see -F flag\""
                    f" --body-file {body_file}"
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_body_source_quoted_path_with_tracker_denied(
        self, claude_config_repo, tmp_path
    ):
        """Quoted file-path argument must still be extracted and scanned. A
        body file referenced as --body-file "/path/file.md" (outer quotes
        present) containing a tracker token must be caught — the xargs
        tokenizer strips outer quotes and emits the bare path, so the file
        is read and scanned identically to the unquoted form."""
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("See WIDGET-123 for context.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f'gh pr create --title "ordinary title"'
                    f' --body-file "{body_file}"'
                ),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_body_source_quoted_path_clean_allowed(
        self, claude_config_repo, tmp_path
    ):
        """Quoted file-path argument with clean content must be allowed.
        Mirrors test_body_source_quoted_path_with_tracker_denied but
        with no tracker token — confirms the quoted-path extraction does
        not introduce false denials when the file content is clean."""
        body_file = tmp_path / "clean-body.md"
        body_file.write_text("This PR fixes the login flow. No tracker IDs.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f'gh pr create --title "ordinary title"'
                    f' --body-file "{body_file}"'
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_body_source_equals_form_with_tracker_denied(
        self, claude_config_repo, tmp_path
    ):
        """The =form (--body-file=path) routes through a distinct awk branch
        from the space-separated form and must also extract and scan the file.
        A body file referenced via --body-file=/path containing a tracker
        token must be caught."""
        body_file = tmp_path / "pr-body.md"
        body_file.write_text("See WIDGET-123 for context.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f'gh pr create --title "ordinary title"'
                    f' --body-file={body_file}'
                ),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_commit_msg_source_quoted_path_with_tracker_denied(
        self, claude_config_repo, tmp_path
    ):
        """Quoted file-path argument to git commit -F must still be extracted
        and scanned. A commit-message file referenced as -F "/path/msg.txt"
        (outer quotes present) containing a tracker token must be caught.
        A clean file is staged so the diff-gated branch that calls the
        extractor is entered."""
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text("See WIDGET-123 for context.\n")
        (claude_config_repo / "clean.txt").write_text("some content\n")
        subprocess.run(["git", "add", "clean.txt"], cwd=claude_config_repo, check=True)
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(f'git commit -F "{msg_file}"'),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_input_quoted_path_with_tracker_denied(
        self, claude_config_repo, tmp_path
    ):
        """Quoted file-path argument to gh api --input must still be extracted
        and scanned. A request body file referenced as --input "/path/body.json"
        (outer quotes present) containing a tracker token must be caught."""
        input_file = tmp_path / "body.json"
        input_file.write_text('{"body": "See WIDGET-123 for context."}\n')
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f'gh api -X POST --input "{input_file}"'
                    f" repos/owner/repo/issues"
                ),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_api_field_at_quoted_path_with_tracker_denied(
        self, claude_config_repo, tmp_path
    ):
        """Quoted path in a gh api field-at expression (-f body=@"/path")
        must still be extracted and scanned. The outer quotes around the
        path are stripped by xargs tokenization, so the bare path is
        extracted and the file is read and scanned."""
        field_file = tmp_path / "field-value.txt"
        field_file.write_text("See WIDGET-123 for context.\n")
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    f'gh api -X POST -f body=@"{field_file}"'
                    f" repos/owner/repo/issues"
                ),
                cwd=claude_config_repo,
            )
            == "deny"
        )


# ---------------------------------------------------------------------------
# require-worktree-for-git-writes.sh
# ---------------------------------------------------------------------------


@pytest.fixture
def opted_in_repo(tmp_path):
    """Git repo with .claude/worktree-required committed (opted into
    worktree enforcement)."""
    repo = tmp_path / "opted-in"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktree-required").write_text("# sentinel\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def non_opted_repo(tmp_path):
    """Git repo without the sentinel — enforcement should be a no-op."""
    repo = tmp_path / "non-opted"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def opted_in_with_worktree(opted_in_repo, tmp_path):
    """Opted-in repo with a linked worktree at a path that does NOT contain
    '/worktrees/' — verifies the hook's worktree check reads git-dir rather
    than pattern-matching the working-tree path."""
    wt_path = tmp_path / "feature-tree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt_path)],
        cwd=opted_in_repo,
        check=True,
    )
    return opted_in_repo, wt_path


class TestRequireWorktreeForGitWrites:
    def test_no_sentinel_allows_commit(self, non_opted_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_opted_repo) == "allow"

    def test_no_sentinel_allows_push(self, non_opted_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git push origin main"), cwd=non_opted_repo) == "allow"

    def test_opted_in_main_tree_denies_commit(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_push(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git push origin main"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_rebase(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git rebase origin/main"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_reset(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git reset --hard HEAD~1"), cwd=opted_in_repo) == "deny"

    def test_opted_in_main_tree_denies_checkout(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("git checkout main"), cwd=opted_in_repo) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git diff HEAD~1",
            "git show HEAD",
            "git fetch origin",
            "git branch",
            "git rev-parse --show-toplevel",
            "git remote -v",
            "git blame file.txt",
        ],
    )
    def test_opted_in_main_tree_allows_readonly(self, opted_in_repo, command):
        assert run_hook(WORKTREE_HOOK, bash_input(command), cwd=opted_in_repo) == "allow"

    def test_opted_in_chained_write_denies(self, opted_in_repo):
        """A read-only fragment followed by a write still denies — the
        write fragment alone is enough."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status && git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_opted_in_chained_readonly_allows(self, opted_in_repo):
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status && git log --oneline"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_opted_in_worktree_allows_commit(self, opted_in_with_worktree):
        _, worktree = opted_in_with_worktree
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=worktree) == "allow"

    def test_opted_in_worktree_allows_push(self, opted_in_with_worktree):
        _, worktree = opted_in_with_worktree
        assert run_hook(WORKTREE_HOOK, bash_input("git push origin feature"), cwd=worktree) == "allow"

    def test_non_git_command_allowed(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("ls -la"), cwd=opted_in_repo) == "allow"

    def test_outside_git_repo_allowed(self, tmp_path):
        """Not in a git repo — nothing to enforce."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    def test_git_dash_C_flag_stripped(self, opted_in_repo):
        """`git -C /tmp commit` should parse as `commit` — flag and path stripped."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -C /tmp commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_git_no_pager_log_allowed(self, opted_in_repo):
        """`git --no-pager log` parses as `log` — flag stripped."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git --no-pager log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_parse_failure_denies(self, opted_in_repo):
        """Fail-closed: if we can't identify the subcommand, deny with a
        recognizable reason (distinguishable from an allowlist miss)."""
        result = subprocess.run(
            [str(WORKTREE_HOOK)],
            input=json.dumps(bash_input("git -C /tmp")),
            capture_output=True,
            text=True,
            cwd=opted_in_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "could not determine the git subcommand" in payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_worktree_add_allowed_on_main_tree(self, opted_in_repo):
        """`git worktree add` is the bootstrap for this whole mechanism.
        Denying it would strand users whose only escape hatch is creating
        a worktree from the main tree. Explicitly allowlisted."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git worktree add .claude/worktrees/feature -b feature"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_git_config_denied_on_main_tree(self, opted_in_repo):
        """`git config` can install malicious aliases, pagers, credential
        helpers that execute arbitrary code on next git invocation. Not
        safe as 'read-only' even though it doesn't touch the working tree."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git config --get user.email"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_env_prefix_command_denied(self, opted_in_repo):
        """`env FOO=1 git commit` — after-git strip still yields `commit`."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("env FOO=1 git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_sudo_prefix_command_denied(self, opted_in_repo):
        """Sudo prefix doesn't change subcommand extraction."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("sudo git commit -m foo"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_pipe_readonly_allowed(self, opted_in_repo):
        """Pipe-chained read-only commands pass; each fragment parsed separately."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git log --oneline | grep foo"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_pipe_then_write_denied(self, opted_in_repo):
        """A write after a pipe+&& is still caught — pipe and && both split."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git status | head && git commit -m x"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_background_write_denied(self, opted_in_repo):
        """`git push &` — the & isn't split but `push` is still extracted."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git push &"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_empty_command_allowed(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input(""), cwd=opted_in_repo) == "allow"

    def test_whitespace_only_command_allowed(self, opted_in_repo):
        assert run_hook(WORKTREE_HOOK, bash_input("   "), cwd=opted_in_repo) == "allow"

    def test_git_dash_c_inline_config_allowed(self, opted_in_repo):
        """`git -c key=val log` — the -c inline config flag consumes the
        next word; subcommand `log` is on the allowlist."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -c user.email=t@t.com log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_git_dir_flag_allowed(self, opted_in_repo):
        """`git --git-dir /tmp/.git log` — --git-dir consumes next word."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git --git-dir /tmp/.git log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_sentinel_as_directory_treated_as_unopted(self, tmp_path):
        """`-f` is false for directories, so a directory at
        .claude/worktree-required leaves the repo effectively unopted."""
        repo = tmp_path / "weird"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "worktree-required").mkdir()
        (repo / "f.txt").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        assert run_hook(WORKTREE_HOOK, bash_input("git commit -m foo"), cwd=repo) == "allow"

    def test_malformed_json_stdin_denies(self, opted_in_repo):
        """jq parse failure → fail-closed deny. We skip `run_hook` and feed
        raw non-JSON directly."""
        result = subprocess.run(
            [str(WORKTREE_HOOK)],
            input="this is not JSON at all{",
            capture_output=True,
            text=True,
            cwd=opted_in_repo,
            check=False,
        )
        assert result.stdout.strip(), "expected a deny verdict on malformed JSON"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_git_dir_env_var_does_not_bypass(self, opted_in_repo):
        """GIT_DIR=/anything/worktrees/x must NOT make the main tree look
        like a linked worktree. The hook unsets GIT_DIR defensively."""
        env = {**os.environ, "GIT_DIR": "/tmp/fake/worktrees/spoofed"}
        result = subprocess.run(
            [str(WORKTREE_HOOK)],
            input=json.dumps(bash_input("git commit -m foo")),
            capture_output=True,
            text=True,
            cwd=opted_in_repo,
            env=env,
            check=False,
        )
        assert result.stdout.strip(), "expected deny despite GIT_DIR spoof"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_non_bash_tool_allowed(self, opted_in_repo):
        """Edit tool inputs have no .tool_input.command — hook no-ops."""
        assert run_hook(WORKTREE_HOOK, edit_input("/tmp/foo.txt"), cwd=opted_in_repo) == "allow"

    # -- Word-boundary false-positive regression ----------------------------
    # Regression: the hook originally used `*git*` substring checks that
    # matched `.github`, `.gitignore`, `github.com`, and similar, blocking
    # harmless `ls .github/workflows/` reads. The fix requires `git` to
    # appear as a command word (bounded by non-alnum or string edges),
    # and each fragment must have a word equal to `git` or ending in
    # `/git` to be treated as a git invocation.

    @pytest.mark.parametrize(
        "command",
        [
            "ls .github/workflows/",
            "cat .gitignore",
            "grep -r github.com /src",
            "find . -name '*.git'",
            "./git-foo",
            "gitk master",
        ],
        ids=[
            "ls-dotgithub",
            "cat-dotgitignore",
            "grep-githubcom",
            "find-dotgit",
            "git-foo-extension",
            "gitk-alnum-trailing",
        ],
    )
    def test_git_substring_in_non_git_command_allowed(self, opted_in_repo, command):
        """Commands that mention `git` only as a path/URL/prefix substring
        must not be treated as git invocations. `gitk` pins the regex's
        both-sides non-alnum requirement — a change that only kept the
        leading boundary would regress this case."""
        assert run_hook(WORKTREE_HOOK, bash_input(command), cwd=opted_in_repo) == "allow"

    def test_chained_dotgithub_read_and_git_log_allowed(self, opted_in_repo):
        """Read-only fragment touching `.github` followed by a read-only
        git command: both fragments must resolve correctly."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("ls .github/workflows/ && git log --oneline"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_chained_dotgitignore_read_and_git_commit_denied(self, opted_in_repo):
        """Fragment mentioning `.gitignore` must not mask a real `git
        commit` in a later fragment."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("cat .gitignore && git commit -m x"),
                cwd=opted_in_repo,
            )
            == "deny"
        )

    def test_git_log_with_dotgithub_path_arg_allowed(self, opted_in_repo):
        """Real read-only git command whose arguments reference a `.github`
        path must still parse as its subcommand — `git log -- .github/...`
        is `log`, not denied."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git log -- .github/workflows/hooks.yml"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    # The cwd-anchor note is appended to the deny reason ONLY when the
    # command shows a `cd ... && git ...` (or `;` / `||`) pattern. This
    # is the precise failure mode where the agent expected its inline cd
    # to put it in a worktree, but the hook reads cwd from the JSON
    # tool_input — Claude Code's persisted bash cwd from prior calls,
    # not the cwd the inline cd would produce after this hook returns.
    # Tests cover three positive cases (each chain operator) and a
    # negative case (no chained cd → no note appended).

    def test_chained_cd_amp_git_appends_anchor_note(self, opted_in_repo):
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp && git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" in reason
        assert "Anchor cwd" in reason

    def test_chained_cd_semicolon_git_appends_anchor_note(self, opted_in_repo):
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp; git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" in reason

    def test_chained_cd_or_git_appends_anchor_note(self, opted_in_repo):
        """`||` chain (run-if-fail) is unusual but parses the same way —
        cwd note still appended so the agent gets the hint."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp || git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" in reason

    def test_plain_git_no_anchor_note(self, opted_in_repo):
        """No chained cd → cwd note not appended; deny message stays
        short for the common case."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" not in reason
        assert "Anchor cwd" not in reason

    def test_cd_after_git_no_anchor_note(self, opted_in_repo):
        """`git ... && cd ...` is the reverse of the trigger pattern —
        the cd is AFTER the git, not before. The note targets the
        chained-cd-before-git mistake, so this case must NOT match."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo && cd /tmp"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "session-persisted" not in reason

    # Tests for git_C_note_if_present: the corrective note appended when
    # the agent used `git -C <path> <write-op>` from the main tree and
    # expected the -C path to be treated as the working directory.
    # Assertion phrases:
    #   chained-cd note (existing) → unique substring: "chained 'cd"
    #   -C note (new)              → unique substring: "-C path"

    def test_git_dash_C_write_appends_C_note(self, opted_in_repo):
        """`git -C /tmp commit` → denied; -C note appended."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git -C /tmp commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "-C path" in reason

    def test_git_dash_C_readonly_no_C_note(self, opted_in_repo):
        """`git -C /tmp log` is read-only → allowed; no deny reason."""
        assert (
            run_hook(
                WORKTREE_HOOK,
                bash_input("git -C /tmp log"),
                cwd=opted_in_repo,
            )
            == "allow"
        )

    def test_plain_git_no_C_note(self, opted_in_repo):
        """Plain `git commit` without -C → denied; -C note NOT appended."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "-C path" not in reason

    def test_subcommand_dash_C_no_C_note(self, opted_in_repo):
        """`git commit -C HEAD` uses -C as commit's reuse-message flag,
        not as the global working-dir flag. The note must NOT fire —
        the hint about working directories doesn't apply here."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("git commit -C HEAD"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "-C path" not in reason

    def test_chained_cd_and_git_C_appends_both_notes(self, opted_in_repo):
        """Command with both patterns → both notes appended independently."""
        reason = run_hook_reason(
            WORKTREE_HOOK,
            bash_input("cd /tmp && git -C /tmp commit -m foo"),
            cwd=opted_in_repo,
        )
        assert reason is not None
        assert "chained 'cd" in reason   # chained-cd note
        assert "-C path" in reason        # -C note


# --- require-stow-reminder.sh ----------------------------------------
#
# The hook fires only on `gh pr create` / `gh pr edit` in a repo whose
# origin URL contains `claude-config`, and only when the diff against
# `main` introduces a brand-new immediate child of `claude/.claude/`.
# The marker that satisfies the gate is a case-insensitive substring
# match for `install.sh` or `stow` in the inline command, any
# referenced body-source file, or — if `--fill` is in play — the commit
# messages reachable on the branch.


@pytest.fixture
def stow_repo(tmp_path):
    """A claude-config-shaped repo with `main` containing one already-
    stowed top-level entry under `claude/.claude/`, and a `feature`
    branch checked out for tests to add new content on top of.

    Tests that want a new top-level entry to be detected should add it
    on `feature` and commit there — the hook diffs `main...HEAD` from
    inside the repo's cwd."""
    repo = tmp_path / "stow-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:jcdendrite/claude-config.git"],
        cwd=repo,
        check=True,
    )
    # Existing top-level entry on main: claude/.claude/skills/foo.md.
    # Tests can add files inside `skills/` without tripping the gate
    # (skills/ is not a new top-level), or add a sibling like
    # `agents/` to trip it.
    skills = repo / "claude" / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "foo.md").write_text("# existing skill\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    return repo


def commit_new_toplevel_dir(repo: Path, name: str) -> None:
    """Create `claude/.claude/<name>/file.md` and commit on the current
    branch. Used to simulate adding a brand-new top-level directory."""
    target_dir = repo / "claude" / ".claude" / name
    target_dir.mkdir(parents=True)
    (target_dir / "file.md").write_text(f"# {name}\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=repo, check=True)


def commit_new_toplevel_file(repo: Path, name: str) -> None:
    """Create `claude/.claude/<name>` (top-level file) and commit."""
    (repo / "claude" / ".claude" / name).write_text("data\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=repo, check=True)


def commit_inside_existing_toplevel(repo: Path) -> None:
    """Add a file inside the already-stowed `skills/` directory. Should
    NOT trip the gate — `skills/` already exists on main."""
    (repo / "claude" / ".claude" / "skills" / "bar.md").write_text("# new skill\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add skill bar"], cwd=repo, check=True)


class TestRequireStowReminder:
    def test_non_pr_command_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        assert run_hook(STOW_REMINDER_HOOK, bash_input("git status"), cwd=stow_repo) == "allow"

    def test_unrelated_remote_repo_allowed(self, tmp_path):
        """The gate is scoped to claude-config. Other repos may legitimately
        add top-level directories without any stow workflow."""
        repo = tmp_path / "other-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:someone/other-app.git"],
            cwd=repo,
            check=True,
        )
        (repo / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
        (repo / "claude").mkdir()
        (repo / "claude" / ".claude").mkdir()
        (repo / "claude" / ".claude" / "agents").mkdir()
        (repo / "claude" / ".claude" / "agents" / "foo.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add agents"], cwd=repo, check=True)
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=repo) == "allow"

    def test_no_new_toplevel_allowed(self, stow_repo):
        """File added inside an already-stowed directory does not need
        a stow re-run; gate must not fire."""
        commit_inside_existing_toplevel(stow_repo)
        cmd = "gh pr create --title T --body 'just a new skill'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_new_toplevel_dir_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title 'Add agents' --body 'Adds reviewer agents.'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_new_toplevel_file_without_marker_denied(self, stow_repo):
        """A new file directly under claude/.claude/ also requires
        re-stow — stow links each top-level child individually."""
        commit_new_toplevel_file(stow_repo, "newfile.md")
        cmd = "gh pr create --title T --body 'add file'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_marker_install_sh_in_body_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'After merging, run ./install.sh'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_marker_stow_in_body_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'remember to re-stow'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_marker_case_insensitive_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'Run INSTALL.SH after merge'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_body_file_with_marker_allowed(self, stow_repo, tmp_path):
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text("Adds agents/.\n\nPost-merge: run ./install.sh.\n")
        cmd = f"gh pr create --title T --body-file {body}"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_body_file_without_marker_denied(self, stow_repo, tmp_path):
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text("Adds agents/. No reminder here.\n")
        cmd = f"gh pr create --title T --body-file {body}"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_fill_with_marker_in_commit_message_allowed(self, stow_repo):
        """`gh pr create --fill` sources body from commits — a marker
        in any commit message on the branch satisfies the gate."""
        # Commit the new top-level with a message that mentions install.sh.
        target = stow_repo / "claude" / ".claude" / "agents"
        target.mkdir(parents=True)
        (target / "foo.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=stow_repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add agents (post-merge: run install.sh)"],
            cwd=stow_repo,
            check=True,
        )
        cmd = "gh pr create --fill"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_fill_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")  # commit msg: "add agents"
        cmd = "gh pr create --fill"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_pr_edit_label_only_allowed(self, stow_repo):
        """gh pr edit without any body-modifying flag must not be gated.
        The create-time check already enforced the marker initially; a
        non-body edit can't remove it."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --add-label needs-review"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_pr_edit_body_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --body 'rewritten body, no marker'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_pr_edit_body_with_marker_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --body 'updated: post-merge run ./install.sh'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_no_main_ref_fails_open(self, tmp_path):
        """Fresh-clone state without a local `main` ref: hook must not
        block PR creation. Documented as a known fail-open in the
        header."""
        repo = tmp_path / "no-main"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:jcdendrite/claude-config.git"],
            cwd=repo,
            check=True,
        )
        (repo / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        cmd = "gh pr create --title T --body 'no marker'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=repo) == "allow"

    def test_malformed_input_denied(self, stow_repo):
        """Fail-closed on unparseable JSON, parallel to the other gates."""
        result = subprocess.run(
            [str(STOW_REMINDER_HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            cwd=stow_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# require-ready-for-review.sh
# ---------------------------------------------------------------------------


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


def rfr_completion_marker(home: Path, repo: Path, session_id: str) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return (
        home / ".claude" / "ready-for-review-markers" / f"{repo_hash}.{session_id}"
    )


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


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
        (marker_dir / sid).touch()
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_stale_active_marker_falls_through(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """>60min old marker doesn't bypass; with no completion marker, deny."""
        sid = "session-stale"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_other_sessions_active_marker_does_not_bypass(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Per-session keying: A's active marker must NOT authorize B's push."""
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="session-B"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_active_marker_mtime_refreshed_on_bypass(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Long-running skill mitigation: hook touches marker on each bypass
        so a session approaching the 60-min cutoff doesn't get blocked."""
        sid = "session-long"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        fifty_min_ago = time.time() - 50 * 60
        os.utime(marker, (fifty_min_ago, fifty_min_ago))
        pre_mtime = marker.stat().st_mtime
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )
        assert marker.stat().st_mtime > pre_mtime, (
            "active marker mtime must be refreshed on bypass"
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

    def test_other_sessions_completion_marker_does_not_authorize(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Per-session keying: A's completion marker must NOT authorize B's
        push, even with the same HEAD SHA."""
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
            == "deny"
        )

    def test_no_session_id_denies_when_pr_exists(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Without session_id in the hook payload, no per-session marker can
        be keyed — deny when a PR exists."""
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
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        ), "precondition: gated push must deny before activation"

        cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "activate-gate")
        subprocess.run(
            ["bash", "-c", cmd],
            cwd=repo_on_feature_branch,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )

        marker = rfr_active_marker(isolated_home, sid)
        assert marker.exists(), (
            "SKILL.md activate-gate recipe ran but no marker landed at the "
            "path the hook checks — skill and hook disagree on layout."
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
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "record-completion")
        subprocess.run(
            ["bash", "-c", cmd],
            cwd=repo_on_feature_branch,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )

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

    def test_skill_deactivate_command_removes_active_marker(
        self, isolated_home, repo_on_feature_branch
    ):
        """SKILL.md deactivate-gate fixture must remove this session's active
        marker so a subsequent push (without completion marker) is re-gated."""
        sid = "test-session-rfr-deactivate"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        activate_cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "activate-gate")
        subprocess.run(
            ["bash", "-c", activate_cmd],
            cwd=repo_on_feature_branch,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )
        marker = rfr_active_marker(isolated_home, sid)
        assert marker.exists(), "activate-gate setup did not create the marker"

        deactivate_cmd = extract_skill_command(
            READY_FOR_REVIEW_SKILL, "deactivate-gate"
        )
        subprocess.run(
            ["bash", "-c", deactivate_cmd],
            cwd=repo_on_feature_branch,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )
        assert not marker.exists(), (
            "SKILL.md deactivate-gate recipe ran but the marker is still "
            "present — skill and hook disagree on the marker path."
        )


# ---------------------------------------------------------------------------
# guard-settings-model-effort.sh
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_repo(tmp_path):
    """Git repo with a main branch and a staged settings.json change.

    Mirrors the structure the hook sees at commit time: a committed
    baseline on `main`, then a staged modification in the working tree.
    The repo path matches `claude/.claude/settings.json` — the exact
    path the hook checks for.
    """
    repo = tmp_path / "settings-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=repo, check=True, capture_output=True,
    )
    # Create the settings.json at the repo-relative path the hook checks.
    settings_dir = repo / "claude" / ".claude"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text('{"model": "sonnet", "effortLevel": "normal"}\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo, settings_file


def stage_settings(repo: Path, settings_file: Path, content: str) -> None:
    """Write `content` to `settings_file` and stage it."""
    settings_file.write_text(content)
    subprocess.run(
        ["git", "add", "claude/.claude/settings.json"],
        cwd=repo, check=True,
    )


class TestGuardSettingsModelEffort:
    def test_model_change_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_effort_level_change_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "sonnet", "effortLevel": "high"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_both_changed_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "high"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'routing change'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_unrelated_settings_change_allows(self, settings_repo):
        """Changing a key other than model/effortLevel must not block."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "sonnet", "effortLevel": "normal", "theme": "dark"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'add theme'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_settings_not_staged_allows(self, settings_repo):
        """If settings.json is not staged, the hook has no opinion."""
        repo, settings_file = settings_repo
        # Stage a different file, not settings.json.
        other = repo / "other.txt"
        other.write_text("change\n")
        subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'other change'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_non_commit_command_allows(self, settings_repo):
        """Hook only fires on git commit; other commands pass through."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git status"),
                cwd=repo,
            )
            == "allow"
        )

    def test_non_bash_tool_allows(self, settings_repo):
        """Edit/Write tool calls pass through — hook is Bash-only."""
        repo, settings_file = settings_repo
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                edit_input(str(settings_file)),
                cwd=repo,
            )
            == "allow"
        )

    def test_deny_message_mentions_settings_json(self, settings_repo):
        """Deny reason must reference settings.json so the agent knows what to unstage."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        reason = run_hook_reason(
            GUARD_SETTINGS_MODEL_EFFORT_HOOK,
            bash_input("git commit -m 'update settings'"),
            cwd=repo,
        )
        assert reason is not None
        assert "settings.json" in reason
        assert "model" in reason or "effortLevel" in reason

    def test_outside_git_repo_allows(self, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_with_model_change_denies(self, settings_repo):
        """Chained `git add ... && git commit` is still gated."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "haiku", "effortLevel": "normal"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git add . && git commit -m update"),
                cwd=repo,
            )
            == "deny"
        )

    def test_empty_staged_diff_allows(self, settings_repo):
        """No staged changes → let git decide (nothing staged case)."""
        repo, settings_file = settings_repo
        # Ensure nothing is staged.
        subprocess.run(["git", "reset", "HEAD", "--", "."], cwd=repo, check=True)
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )


# ---------------------------------------------------------------------------
# require-plan-review.sh
# ---------------------------------------------------------------------------


@pytest.fixture
def plan_review_repo(tmp_path):
    """Git repo with .claude/plans/ populated.

    The gate is globally applied — no opt-in required.
    """
    repo = tmp_path / "plan-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    plans_dir = repo / ".claude" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "impl-plan.md").write_text("# Implementation plan\n\nStep 1...\n")
    return repo


@pytest.fixture
def plan_review_home(isolated_home):
    """Isolated $HOME with the plan-review-markers directory pre-created."""
    (isolated_home / ".claude" / "plan-review-markers").mkdir(parents=True, exist_ok=True)
    return isolated_home


def plan_review_marker_path(home: Path, repo: Path, session_id: str) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "plan-review-markers" / f"{repo_hash}.{session_id}"


def write_plan_review_marker(home: Path, repo: Path, session_id: str) -> Path:
    marker = plan_review_marker_path(home, repo, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("reviewed\n")
    return marker


class TestRequirePlanReview:
    def test_plan_exists_no_marker_denies_write(self, plan_review_repo, plan_review_home):
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_plan_exists_no_marker_denies_edit(self, plan_review_repo, plan_review_home):
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input("/tmp/foo.py"), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_plan_exists_with_marker_allows_write(self, plan_review_repo, plan_review_home):
        sid = "test-session-prt-allowed"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_plan_exists_with_marker_allows_edit(self, plan_review_repo, plan_review_home):
        sid = "test-session-prt-allowed-edit"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_other_sessions_marker_does_not_authorize(self, plan_review_repo, plan_review_home):
        """Marker for session A must NOT bypass session B's gate."""
        write_plan_review_marker(plan_review_home, plan_review_repo, "session-A")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": "session-B"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_no_plans_dir_allows(self, tmp_path):
        """No .claude/plans/ directory → gate is inactive."""
        repo = tmp_path / "no-plans"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=repo,
            )
            == "allow"
        )

    def test_empty_plans_dir_allows(self, tmp_path):
        """Empty .claude/plans/ directory → no plans present, gate inactive."""
        repo = tmp_path / "empty-plans"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / ".claude" / "plans").mkdir(parents=True)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=repo,
            )
            == "allow"
        )

    def test_bash_tool_allows_always(self, plan_review_repo):
        """Bash tool calls are not gated — only Write and Edit."""
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                bash_input("git commit -m foo"),
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_outside_git_repo_allows(self, tmp_path):
        """Outside a git repo, the hook cannot key a marker — allow through."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=non_repo,
            )
            == "allow"
        )

    def test_no_session_id_in_input_denies(self, plan_review_repo, plan_review_home):
        """Without session_id in the hook payload, no per-session marker can be
        keyed — deny even if a marker directory exists. Mirrors the same invariant
        in require-code-review.sh and require-respond-pr.sh: missing session_id
        must fail-closed, not silently allow. This is a load-bearing safety
        property of the per-session marker design."""
        # Write a marker for a known session so the marker dir exists, but the
        # payload has no session_id — the hook must not accept the existing marker.
        write_plan_review_marker(plan_review_home, plan_review_repo, "some-other-session")
        # write_input() uses no session_id field.
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_deny_message_mentions_plan_review(self, plan_review_repo, plan_review_home):
        """Deny reason must reference /plan-review so the agent knows what to run."""
        reason = run_hook_reason(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**write_input("/tmp/foo.py"), "session_id": "session-for-reason"},
            cwd=plan_review_repo,
        )
        assert reason is not None
        assert "/plan-review" in reason
        assert "plan-review-markers" in reason


# ---------------------------------------------------------------------------
# require-worktree-for-file-writes.sh
# ---------------------------------------------------------------------------


class TestRequireWorktreeForFileWrites:
    def test_no_sentinel_allows_edit(self, non_opted_repo):
        """Repo without the sentinel: Edit passes through unconditionally."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_no_sentinel_allows_write(self, non_opted_repo):
        """Repo without the sentinel: Write passes through unconditionally."""
        path = str(non_opted_repo / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_main_tree_denies_edit(self, opted_in_repo):
        """Edit targeting an existing file in the main tree is denied."""
        path = str(opted_in_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "deny"

    def test_opted_in_main_tree_denies_write(self, opted_in_repo):
        """Write targeting the main tree is denied even for a new file."""
        path = str(opted_in_repo / "newfile.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_opted_in_main_tree_denies_multiedit(self, opted_in_repo):
        """MultiEdit targeting the main tree is denied."""
        path = str(opted_in_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "deny"

    def test_opted_in_worktree_allows_edit(self, opted_in_with_worktree):
        """Edit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_opted_in_worktree_allows_write(self, opted_in_with_worktree):
        """Write targeting a new file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_worktree_allows_multiedit(self, opted_in_with_worktree):
        """MultiEdit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "allow"

    def test_non_git_path_allows_edit(self, tmp_path):
        """Edit to a path outside any git repo is allowed."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        path = str(non_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_new_file_nested_path_denied_in_main_tree(self, opted_in_repo):
        """Write to a not-yet-existing nested path whose ancestor is in the
        main tree is denied — the hook must walk up to the existing dir."""
        path = str(opted_in_repo / "subdir" / "deeply" / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_bash_tool_allowed(self, opted_in_repo):
        """Non-file-write tool (Bash) passes through: the hook is scoped to
        Edit/Write/MultiEdit only."""
        assert run_hook(FILE_WRITES_HOOK, bash_input("echo hi")) == "allow"

    def test_deny_message_names_relative_path(self, opted_in_repo):
        """Deny message should include the relative worktree path hint."""
        path = str(opted_in_repo / "src" / "main.sh")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "src/main.sh" in reason

    def test_deny_message_names_tool(self, opted_in_repo):
        """Deny message should name the tool that was blocked."""
        path = str(opted_in_repo / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, write_input(path))
        assert reason is not None
        assert "Write" in reason

    def test_malformed_json_stdin_denies(self):
        """Malformed JSON input must produce a deny, not a silent allow."""
        result = subprocess.run(
            [str(FILE_WRITES_HOOK)],
            input="not-json{{{",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip(), "Expected deny output on malformed JSON, got silent allow"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
