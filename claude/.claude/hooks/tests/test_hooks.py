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
DENY_PRIVATE_PROJECT_REFS_HOOK = HOOKS_DIR / "deny-private-project-refs.sh"
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
        ],
        ids=[
            "cve", "cwe", "pep", "rfc", "gh", "bug", "iso", "ietf",
            "w3c", "nist", "ecma", "ansi", "jep", "jdk", "llvm", "gcc",
            "sha", "md", "http", "tls",
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
