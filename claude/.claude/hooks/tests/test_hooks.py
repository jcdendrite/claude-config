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
import subprocess
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
CODE_REVIEW_HOOK = HOOKS_DIR / "require-code-review.sh"
RESPOND_PR_HOOK = HOOKS_DIR / "require-respond-pr.sh"
REVIEW_PERMS_HOOK = HOOKS_DIR / "ask-review-permissions.sh"
DENY_CLIENT_REFS_HOOK = HOOKS_DIR / "deny-client-refs.sh"
WORKTREE_HOOK = HOOKS_DIR / "require-worktree-for-git-writes.sh"


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


def bash_input(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def edit_input(file_path: str) -> dict:
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path, "old_string": "a", "new_string": "b"},
    }


def write_input(file_path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}}


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


def marker_path(home: Path, repo: Path) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "review-markers" / repo_hash


def staged_diff_hash(repo: Path) -> str:
    diff = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, capture_output=True, check=True
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def write_marker(home: Path, repo: Path, diff_hash: str) -> Path:
    marker = marker_path(home, repo)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(diff_hash + "\n")
    return marker


# ---------------------------------------------------------------------------
# require-code-review.sh
# ---------------------------------------------------------------------------


class TestRequireCodeReview:
    def test_no_marker_denies_commit(self, isolated_home, git_repo):
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_wrong_hash_marker_denies(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, "0" * 64)
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_correct_hash_marker_allows(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    def test_chained_add_commit_allowed_when_marker_current(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git add file.txt && git commit -m foo"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_restaging_invalidates_marker(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "deny"

    def test_chained_add_commit_denied_when_marker_stale(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git add file.txt && git commit -m foo"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_refreshed_marker_allows(self, isolated_home, git_repo):
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

    def test_skill_marker_write_command_matches_hook_path(self, isolated_home, git_repo):
        """Regression guard for the trailing-newline drift bug.

        Runs the exact shell pipeline from code-review SKILL.md that writes the
        marker, then verifies the hook accepts it. If SKILL.md and the hook
        ever disagree on how they derive the repo-hash path, this fails.
        """
        markers_dir = isolated_home / ".claude" / "review-markers"
        for f in markers_dir.glob("*"):
            f.unlink()
        skill_command = (
            'mkdir -p "$HOME/.claude/review-markers" && '
            "git diff --cached | sha256sum | awk '{print $1}' > "
            '"$HOME/.claude/review-markers/$(git rev-parse --show-toplevel | tr -d \'\\n\' | sha256sum | awk \'{print $1}\')"'
        )
        subprocess.run(
            ["bash", "-c", skill_command],
            cwd=git_repo,
            env={**os.environ, "HOME": str(isolated_home)},
            check=True,
        )
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo) == "allow"

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

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh pr comment 5 --body test",
        ],
    )
    def test_fresh_bypass_marker_allows(self, isolated_home, current_repo_foo_bar, command):
        (isolated_home / ".claude" / ".respond-pr-active").touch()
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    def test_stale_bypass_marker_denies(self, isolated_home, current_repo_foo_bar):
        marker = isolated_home / ".claude" / ".respond-pr-active"
        marker.touch()
        # Backdate 90 minutes — past the hook's 60-minute staleness cutoff.
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
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
# deny-client-refs.sh
# ---------------------------------------------------------------------------
#
# Fake placeholders used in these tests — chosen to be obviously synthetic
# so the test file itself doesn't violate the rule it's testing:
#   WIDGET-123, FOOCORP-42, NULLCLIENT-999, EXAMPLECO-7, BARCORP-22
# All six prefixes are invented; none correspond to a real tracker that
# any known organization uses. The hook's allowlist matches real OSS
# reference prefixes only (CVE / RFC / PEP / ISO / GH / BUG / IETF).


class TestDenyClientRefs:
    def test_non_commit_command_allowed(self, git_repo):
        assert run_hook(DENY_CLIENT_REFS_HOOK, bash_input("git status"), cwd=git_repo) == "allow"

    def test_non_git_command_allowed(self, git_repo):
        assert run_hook(DENY_CLIENT_REFS_HOOK, bash_input("echo WIDGET-123"), cwd=git_repo) == "allow"

    def test_clean_commit_message_allowed(self, git_repo):
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Refactor the parser'"),
                cwd=git_repo,
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
        ],
        ids=[
            "cve", "cwe", "pep", "rfc", "gh", "bug", "iso", "ietf",
            "w3c", "nist", "ecma", "ansi", "jep", "jdk", "llvm", "gcc",
            "sha", "md", "http", "tls",
        ],
    )
    def test_allowlisted_references_allowed(self, git_repo, message):
        assert (
            run_hook(DENY_CLIENT_REFS_HOOK, bash_input(f"git commit -m '{message}'"), cwd=git_repo)
            == "allow"
        )

    def test_synthetic_tracker_id_in_message_denied(self, git_repo):
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Fix WIDGET-123 regression'"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_multiple_tracker_ids_denied(self, git_repo):
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Handle FOOCORP-42 and BARCORP-22'"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_tracker_id_in_staged_diff_denied(self, git_repo):
        """Hook must scan staged content, not just the command string."""
        (git_repo / "file.txt").write_text("first\nsecond\n// NULLCLIENT-999 fixed\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Generic refactor'"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_mixed_allowed_and_suspect_denied(self, git_repo):
        """A CVE plus a client-looking token: still deny on the client token."""
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Fix CVE-2024-1234 via EXAMPLECO-7 changes'"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_heredoc_commit_message_scanned(self, git_repo):
        """Heredoc-style commit messages get scanned via the command string."""
        cmd = (
            "git commit -m \"$(cat <<'EOF'\n"
            "Subject line\n"
            "\n"
            "Body referencing FOOCORP-12 incident\n"
            "EOF\n"
            ")\""
        )
        assert run_hook(DENY_CLIENT_REFS_HOOK, bash_input(cmd), cwd=git_repo) == "deny"

    def test_lowercase_token_allowed(self, git_repo):
        """Lowercase `widget-123` doesn't match the uppercase-only regex.

        Ticket IDs are conventionally uppercase; a lowercase hyphenated
        token is more likely to be a package name or slug, not a tracker
        reference. Explicitly allowed to avoid false positives on common
        code patterns.
        """
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Fix widget-123 styling'"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_with_suspect_token_denied(self, git_repo):
        """Chained `git add && git commit` is still gated by this hook."""
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git add . && git commit -m 'Fix WIDGET-1 issue'"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_removing_a_tracker_id_is_allowed(self, git_repo):
        """A redaction commit that *removes* a tracker ID must not be blocked.

        If the hook scanned removed lines, the staged deletion of a token
        would match and block the cleanup itself — making the hook hostile
        to its own maintenance flow.
        """
        # Seed a committed file that already contains a suspect token.
        (git_repo / "legacy.txt").write_text("Old notes about WIDGET-999.\n")
        subprocess.run(["git", "add", "legacy.txt"], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=git_repo, check=True)
        # Now stage a deletion of the token — the diff contains `-WIDGET-999`.
        (git_repo / "legacy.txt").write_text("Old notes.\n")
        subprocess.run(["git", "add", "legacy.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Redact legacy notes'"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_empty_staged_diff_allows_commit(self, git_repo):
        """No staged changes — let git decide (empty-commit, amend, etc.).

        Even though the command mentions a suspect token, there is no new
        content being introduced; the hook shouldn't block an amend-only
        or --allow-empty flow.
        """
        subprocess.run(["git", "reset", "HEAD"], cwd=git_repo, check=True)
        assert (
            run_hook(
                DENY_CLIENT_REFS_HOOK,
                bash_input("git commit -m 'Refers to WIDGET-123 but nothing staged'"),
                cwd=git_repo,
            )
            == "allow"
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
