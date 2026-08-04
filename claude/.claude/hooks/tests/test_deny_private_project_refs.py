"""Tests for deny-private-project-refs.sh.

Fake placeholders used in these tests — chosen to be obviously synthetic
so the test file itself doesn't violate the rule it's testing:
  WIDGET-123, FOOCORP-42, NULLPROJ-999, EXAMPLECO-7, BARCORP-22, FAKEPROJ-42
All six prefixes are invented; none correspond to a real tracker that
any known organization uses. The hook's allowlist matches real OSS
reference prefixes only (CVE / RFC / PEP / ISO / GH / BUG / IETF).
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
    run_hook_reason,
)

DENY_PRIVATE_PROJECT_REFS_HOOK = HOOKS_DIR / "deny-private-project-refs.sh"


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
            "Uses OSC-8 escape sequences",
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
            "w3c", "nist", "ecma", "ansi", "osc", "jep", "jdk", "llvm", "gcc",
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
    # Deny message behavior: names each matched blocklist entry and quotes
    # the offending line(s) so the agent can locate and remove it in one pass.

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

    def test_blocklist_deny_message_names_matched_entry(self, claude_config_repo, private_projects_file):
        """Deny message must name the matched blocklist entry verbatim.

        The matched token is the user's own private-project name, already
        in the staged content. Naming it in the deny lets the agent remove
        it in one pass rather than bisecting the diff manually. The user
        is also pointed at their blocklist file for context.
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

        # Entry is named so the agent knows what to remove.
        assert "Acme Corp" in reason

        # User is pointed at their own blocklist file.
        assert "private-projects.md" in reason

    def test_blocklist_deny_quotes_offending_line(self, claude_config_repo, private_projects_file):
        """Deny message includes the offending line, not just the entry name."""
        private_projects_file("Acme Corp\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input("git commit -m 'Acme Corp quarterly report'")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" in reason
        # "quarterly report" only appears because the offending line was quoted.
        assert "quarterly report" in reason

    def test_blocklist_deny_names_all_matched_entries(self, claude_config_repo, private_projects_file):
        """When multiple blocklist entries match, all are reported in one message."""
        private_projects_file("Acme Corp\nFoo Bar Inc\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input("git commit -m 'fix Acme Corp and Foo Bar Inc integration'")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" in reason
        assert "Foo Bar Inc" in reason

    def test_blocklist_deny_truncates_long_offending_line(self, claude_config_repo, private_projects_file, tmp_path):
        """An offending line longer than 200 chars is truncated with an ellipsis."""
        private_projects_file("Acme Corp\n")
        long_line = "Acme Corp " + "x" * 220
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text(long_line + "\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"git commit -F {msg_file}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" in reason
        assert "…" in reason

    def test_blocklist_deny_offending_lines_capped_at_three(self, claude_config_repo, private_projects_file, tmp_path):
        """When an entry matches more than 3 lines, at most 3 are quoted."""
        private_projects_file("Acme Corp\n")
        msg_file = tmp_path / "commit-msg.txt"
        msg_file.write_text(
            "line1 Acme Corp\nline2 Acme Corp\nline3 Acme Corp\nline4 Acme Corp\nline5 Acme Corp\n"
        )
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(f"git commit -F {msg_file}")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" in reason
        # Count lines in reason that are indented quoted lines (4-space prefix)
        # containing the entry — must be at most 3.
        quoted_lines = [ln for ln in reason.split("\n") if ln.startswith("    ") and "Acme Corp" in ln]
        assert len(quoted_lines) <= 3

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

    def test_git_commit_F_blocklist_match_denied_names_entry(
        self, claude_config_repo, private_projects_file, tmp_path,
    ):
        """User-local blocklist applies to -F file content; entry is named in deny."""
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
        assert "Acme Corp" in reason
        assert "private-projects.md" in reason

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

    def test_gh_api_blocklist_match_in_input_file_denied_names_entry(
        self, claude_config_repo, private_projects_file, tmp_path,
    ):
        """User-local blocklist applies to --input file content; entry is named in deny."""
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
        assert "Acme Corp" in reason
        assert "private-projects.md" in reason

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
        blocklist entry must surface the tracker-ID deny message only.
        The blocklist scan is skipped when the tracker-ID scan fires,
        so the blocklist entry does not appear in the output."""
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
        # Blocklist scan is skipped when tracker fires, so entry absent.
        assert "Acme Corp" not in reason
        assert "acme corp" not in reason.lower()

    # -- Structural-shape scan (six always-on detectors) --------------------
    # Ported from the deleted scan-issue-body.sh's test suite (see git
    # history for claude/.claude/scripts/tests/test_scan_issue_body.py) and
    # exercised through the real PreToolUse hook path rather than a
    # standalone script — these are full `git commit`/`gh pr` calls, not
    # direct-subprocess-on-a-bare-file invocations.

    def test_structural_clean_aggregate_message_allowed(self, claude_config_repo):
        """A corpus-aggregate-only message with no identifying-shape content
        is safe to publish."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "git commit -m 'Cost audit: 268 sessions, 56358 priced turns, 5906 dollars at list price'"
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    # IPv4 literal

    def test_structural_ipv4_no_literal_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Cache read is 51.4 percent of spend across 268 sessions'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_near_miss_two_dot_version_string_allowed(self, claude_config_repo):
        """A two-dot version-like string is one dot-group short of the
        detector's four-group shape — pins the group count against a
        regression that loosens it (e.g. `{2,}` in place of `{3}`), which
        the single-dot allow-case above can't detect."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Already on v2.4.1 today, no upgrade needed'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_literal_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The internal service lives at 10.20.30.40 in the VPC'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ipv4_public_address_allowed(self, claude_config_repo):
        """A public IPv4 outside every RFC 1918 private range and the
        loopback range must not match — proof of the narrowing from the
        old any-dotted-quad regex to a range-scoped one."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Resolved against the public resolver at 8.8.8.8'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_private_10_range_upper_boundary_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Static route covers up to 10.255.255.255 in that VPC'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ipv4_just_above_10_range_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Public allocation begins at 11.0.0.0 per the registry'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_private_172_16_range_lower_boundary_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The bastion host answers on 172.16.0.1 today'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ipv4_just_above_172_16_range_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Public allocation begins at 172.32.0.1 per the registry'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_private_192_168_range_upper_boundary_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Broadcast address for that subnet is 192.168.255.255'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ipv4_just_above_192_168_range_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Public allocation begins at 192.169.0.0 per the registry'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_loopback_upper_boundary_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Top of the loopback range is 127.255.255.255'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ipv4_just_above_loopback_range_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Public allocation begins at 128.0.0.0 per the registry'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ipv4_zero_padded_octets_denied(self, claude_config_repo):
        """A zero-padded private-range literal (legacy tooling/log shape)
        must still match — proof of the `0*` leading-zero allowance."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The internal service lives at 010.000.000.001 in the VPC'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # SSH key path reference

    def test_structural_ssh_key_path_no_reference_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Denials include exact-match rules for git commit and git push'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ssh_key_dot_ssh_path_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Reproduced with the key at ~/.ssh/id_ed25519 loaded'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ssh_key_algorithm_name_as_substring_allowed(self, claude_config_repo):
        """A word merely containing 'id_rsa'/'id_dsa' as a substring (not a
        boundary-delimited key filename) must not match — same
        boundary-safety class as the internal-hostname prefix-word test
        below."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "git commit -m 'The config field is called invalid_rsa_token; renamed avoid_dsa_warnings too'"
                ),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ssh_key_ecdsa_algorithm_name_as_substring_allowed(self, claude_config_repo):
        """Same substring-boundary safety as the rsa/dsa case above,
        exercised for ecdsa — the detector regex already lists all four
        algorithms, this closes a test-coverage gap only."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The config field is called invalid_ecdsa_token'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ssh_key_ed25519_algorithm_name_as_substring_allowed(self, claude_config_repo):
        """Same substring-boundary safety as the rsa/dsa case above,
        exercised for ed25519 — the detector regex already lists all four
        algorithms, this closes a test-coverage gap only."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The config field renamed avoid_ed25519_warnings too'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ssh_key_leading_boundary_violation_alone_allowed(self, claude_config_repo):
        """Isolates the leading-boundary alternative from the trailing one:
        'invalid_ecdsa' has a word char ('d') immediately before 'id_'
        (leading boundary violated) but a comma right after 'ecdsa'
        (trailing boundary satisfied on its own). The substring-allow
        tests above violate both boundaries at once, so a regression that
        silently weakens only the leading-boundary alternative would still
        pass them — this proves the leading-boundary alternative is
        load-bearing by itself."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Renamed the invalid_ecdsa, obsolete now'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_ssh_key_bare_key_name_denied(self, claude_config_repo):
        """A bare key filename with no .ssh/ segment must still match via
        the id_ boundary group on its own."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The rotated key is id_ed25519 going forward'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_ssh_key_hyphen_suffixed_name_denied(self, claude_config_repo):
        """A hyphen-suffixed key reference (a backup/rotation naming style)
        must still match — the trailing boundary treats hyphen as a
        terminator, not a word-continuation character."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The old backup file id_rsa-old was never deleted'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # Home-rooted path

    def test_structural_home_rooted_path_no_reference_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The tool lives at claude/.claude/scripts/transcript-analysis.py'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_home_rooted_path_near_miss_prefix_not_slash_terminated_allowed(
        self, claude_config_repo
    ):
        """`/homebrew/...` and `/UsersGuide...` share the `/Users`/`/home`
        substring but lack the trailing-slash-plus-segment shape the
        detector requires — pins that requirement against a regression that
        drops it, which the no-substring-at-all allow-case above can't
        detect."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Installed via /homebrew/bin/foo, see /UsersGuide.md for setup'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_home_rooted_path_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Session data was read from /Users/alice/.claude/projects'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_home_rooted_path_without_trailing_slash_denied(self, claude_config_repo):
        """A bare username reference with no following path segment must
        still match."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'My home directory is /Users/jared, nothing else'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # Long hex identifier

    def test_structural_long_hex_identifier_no_reference_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m '268 sessions, 56358 priced turns, 5906 dollars at list price'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_long_hex_identifier_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Session 875cfbeb-f03e-4a12-9876-abcdef012345 drove the spike'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_long_hex_identifier_31_chars_below_threshold_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Short id abcdef0123456789abcdef012345678 stays under the fencepost'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_long_hex_identifier_32_chars_at_threshold_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Long id abcdef0123456789abcdef0123456789 hits the fencepost exactly'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # Internal hostname

    def test_structural_internal_hostname_no_reference_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'See platform.claude.com/docs/en/about-claude/pricing for rates'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_internal_hostname_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'The dashboard is hosted at metrics.eng.corp for this team'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_internal_hostname_at_end_of_message_denied(self, claude_config_repo):
        """The trailing-boundary check must not require a POSIX \\b
        extension (not portable across grep implementations) — a bare
        '(...|$)' alternation covers end-of-line the same way a word
        boundary would."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Reachable internally at db.internal'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_internal_hostname_prefix_word_not_flagged_allowed(self, claude_config_repo):
        """A word that merely starts with a TLD-like label ('internally')
        must not match — the regex requires a literal dot immediately
        before the TLD word, and this fixture has no preceding dot at all,
        so the match fails at the leading clause, not the trailing
        boundary (see the continuation-suffix test below for that)."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'This is handled internally, not by some other mechanism'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_internal_hostname_continuation_suffix_not_flagged_allowed(self, claude_config_repo):
        """A dot-prefixed TLD word immediately followed by more word
        characters ('db.internaltools') reaches the TLD match but must not
        match overall — the trailing boundary requires a non-word
        character or end of line after the TLD, not just its presence."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Provisioned via db.internaltools, not a real hostname'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    # Slack-channel shape

    def test_structural_slack_channel_no_reference_allowed(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Cost audit findings. See F1 through F4 below'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_slack_channel_shape_denied(self, claude_config_repo):
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Discussed in #eng-platform-alerts before filing'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_structural_slack_github_issue_reference_not_flagged_allowed(self, claude_config_repo):
        """A plain issue reference (#421) is all-digits and must not
        collide with the Slack-channel shape."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'Posting the misparse note as a comment on #421'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_structural_slack_markdown_anchor_link_denied(self, claude_config_repo):
        """A markdown anchor fragment (#word-word) is indistinguishable
        from a real Slack channel by shape alone — deliberately still
        blocked; loosening the charset to admit hyphenated words would
        defeat the detector's actual purpose."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git commit -m 'See docs/skills.md#skill-architecture-notes for the breakdown'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # -- Structural scan: fail-closed on a grep engine error ----------------

    def test_structural_grep_engine_error_fails_closed(self, claude_config_repo, tmp_path):
        """The structural-detector fast-path pre-check's own grep call
        erroring (rc>=2) must fail closed — exercises the branch distinct
        from the tracker-ID scan above, whose `|| true` swallows the same
        error and fails open.

        The stub shadows `grep` on PATH for the whole hook invocation, not
        just the fast-path call — safe today only because every earlier
        `grep` call in the script (fragment/chain detection, the
        tracker-ID scan) is `|| true`-guarded or used as a bare `if`
        condition under `set -uo pipefail` (no `-e`), so the stub degrades
        those to a harmless "no match" instead of crashing the script
        before reaching the target branch. If an earlier, unguarded `grep`
        call is ever added, this test's global stub would exercise that
        branch instead — re-scope the shadow to the specific fast-path
        call if that happens."""
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        stub_grep = fake_bin / "grep"
        stub_grep.write_text("#!/usr/bin/env bash\nexit 2\n")
        stub_grep.chmod(stub_grep.stat().st_mode | stat.S_IEXEC)
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'Anything at all — the stub grep errors before matching'"),
            cwd=claude_config_repo,
            extra_env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        )
        assert reason is not None
        assert "failing closed" in reason
        # Pins the fast-path pre-check (not a per-detector loop entry) as
        # the branch that fires: with the stub erroring unconditionally,
        # the single combined-pattern grep call ahead of the per-detector
        # loop is the first grep this script reaches after the tracker-ID
        # scan clears, so it is the branch that must report the failure.
        assert "fast-path pre-check" in reason

    def test_structural_combined_pattern_matches_but_no_detector_confirms_fails_closed(
        self, claude_config_repo, tmp_path
    ):
        """A fast-path/per-detector composition mismatch — the combined
        pre-check matches but the follow-up per-detector loop finds
        nothing — must fail closed rather than silently allow. Should be
        structurally unreachable (the combined pattern is derived from the
        same six patterns the loop iterates), but per the standing rule an
        untested security branch is indistinguishable from an absent one.

        The stub discriminates by the grep pattern's length: the combined
        pre-check pattern (all detector patterns `|`-joined) is
        comfortably longer than the longest individual detector pattern
        (currently the IPv4 literal detector, since RFC 1918 range-scoping
        expands it well past the other detectors' length) — so the stub
        matches (`exit 0`) only when its pattern argument exceeds a
        threshold between the two, and reports "no match" (`exit 1`) for
        every individual detector call in the per-detector loop. The
        threshold is derived from the real STRUCTURAL_DETECTORS patterns in
        _lib.sh rather than a hardcoded literal, so it tracks any future
        change to those patterns' lengths instead of silently drifting out
        of the gap between them."""
        detector_script = (HOOKS_DIR / "deny-private-project-refs.sh").read_text()
        lib_script = (HOOKS_DIR / "_lib.sh").read_text()
        detector_array_match = re.search(r"STRUCTURAL_DETECTORS=\((.*?)\n\)", detector_script, re.DOTALL)
        assert detector_array_match, "STRUCTURAL_DETECTORS array not found in deny-private-project-refs.sh"
        detector_var_names = re.findall(r"\$\{(_LIB_[A-Z0-9_]+)\}", detector_array_match.group(1))
        assert detector_var_names, "no detector pattern variables referenced in STRUCTURAL_DETECTORS"
        pattern_lengths = []
        for var_name in detector_var_names:
            var_match = re.search(rf"^{var_name}='(.*)'$", lib_script, re.MULTILINE)
            assert var_match, f"{var_name} not defined in _lib.sh"
            pattern_lengths.append(len(var_match.group(1)))
        max_individual_length = max(pattern_lengths)
        # Mirrors deny-private-project-refs.sh's `structural_combined_pattern`
        # loop: each pattern wrapped "(...)" (2 chars) and "|"-joined (1 char
        # per pattern but the first), so overhead is 3*n - 1 for n patterns.
        combined_length = sum(pattern_lengths) + 3 * len(pattern_lengths) - 1
        threshold = (max_individual_length + combined_length) // 2
        assert max_individual_length < threshold < combined_length
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        stub_grep = fake_bin / "grep"
        stub_grep.write_text(
            "#!/usr/bin/env bash\n"
            "for arg in \"$@\"; do\n"
            f"  if [ ${{#arg}} -gt {threshold} ]; then\n"
            "    exit 0\n"
            "  fi\n"
            "done\n"
            "exit 1\n"
        )
        stub_grep.chmod(stub_grep.stat().st_mode | stat.S_IEXEC)
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'Anything at all — the stub grep discriminates by pattern length'"),
            cwd=claude_config_repo,
            extra_env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        )
        assert reason is not None
        assert "composition mismatch" in reason

    # -- Structural scan: chained-command denial -----------------------------

    def test_structural_chained_command_leak_in_second_command_denied(self, claude_config_repo):
        """A single detector match anywhere in the combined SCAN_TARGET
        denies the whole chain, even when the leak is in the second
        command — the behavior the hook's 'one combined buffer' design
        depends on."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input(
                    "git commit -m 'Generic refactor' && gh pr create --body 'Reproduced at 10.20.30.40 in the VPC'"
                ),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    # -- Structural scan: deny message never echoes the matched value -------
    # Regression guard against reintroducing the adjacent tracker-ID and
    # blocklist branches' echo convention (HIT_LIST / matched_lines) for
    # the six new detectors.

    def test_structural_ipv4_deny_message_omits_matched_value(self, claude_config_repo):
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'The internal service lives at 10.20.30.40 in the VPC'"),
            cwd=claude_config_repo,
        )
        assert reason is not None
        assert "IPv4 literal" in reason
        assert "10.20.30.40" not in reason

    def test_structural_ssh_key_path_deny_message_omits_matched_value(self, claude_config_repo):
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'The rotated key is id_ed25519 going forward'"),
            cwd=claude_config_repo,
        )
        assert reason is not None
        assert "SSH key path reference" in reason
        assert "id_ed25519" not in reason

    def test_structural_home_rooted_path_deny_message_omits_matched_value(self, claude_config_repo):
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'Session data was read from /Users/alice/.claude/projects'"),
            cwd=claude_config_repo,
        )
        assert reason is not None
        assert "home-rooted path" in reason
        assert "/Users/alice" not in reason

    def test_structural_long_hex_identifier_deny_message_omits_matched_value(self, claude_config_repo):
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'Session 875cfbeb-f03e-4a12-9876-abcdef012345 drove the spike'"),
            cwd=claude_config_repo,
        )
        assert reason is not None
        assert "long hex identifier" in reason
        assert "875cfbeb-f03e-4a12-9876-abcdef012345" not in reason

    def test_structural_internal_hostname_deny_message_omits_matched_value(self, claude_config_repo):
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'The dashboard is hosted at metrics.eng.corp for this team'"),
            cwd=claude_config_repo,
        )
        assert reason is not None
        assert "internal hostname" in reason
        assert "metrics.eng.corp" not in reason

    def test_structural_slack_channel_deny_message_omits_matched_value(self, claude_config_repo):
        reason = run_hook_reason(
            DENY_PRIVATE_PROJECT_REFS_HOOK,
            bash_input("git commit -m 'Discussed in #eng-platform-alerts before filing'"),
            cwd=claude_config_repo,
        )
        assert reason is not None
        assert "Slack-channel shape" in reason
        assert "#eng-platform-alerts" not in reason

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

    # -- Shape-aware chain-detection hint ------------------------------------
    # When the command chains operations with && or ||, deny messages
    # append a hint suggesting the agent split the chain into separate
    # Bash calls. Detection is best-effort grep — ; is excluded to avoid
    # false positives on prose semicolons.

    def test_tracker_id_chained_command_deny_includes_chain_hint(self, claude_config_repo):
        """Chained cd && git commit: chain detected, hint appended to deny."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(
                "cd /home/username/mycode && git commit -m 'WIDGET-123 fix'"
            )),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Tip: this command chains" in reason

    def test_blocklist_chained_command_deny_includes_chain_hint(self, claude_config_repo, private_projects_file):
        """Chained cd && gh pr create: blocklist match named, chain hint appended.

        The cd target deliberately avoids the /home/ or /Users/ shape — the
        structural-shape scan (which now runs before this blocklist check)
        would otherwise fire on the cd prefix itself and deny with the
        structural message instead of the blocklist message this test pins."""
        private_projects_file("Acme Corp\n")
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(
                "cd /opt/build/mycode && gh pr create --body 'Acme Corp integration work'"
            )),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Acme Corp" in reason
        assert "Tip: this command chains" in reason

    def test_tracker_id_unchained_command_deny_omits_chain_hint(self, claude_config_repo):
        """Unchained command: deny fires for tracker-ID, but no chain hint."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(
                "git commit -m 'Fix WIDGET-123 regression'"
            )),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Tip: this command chains" not in reason

    def test_chain_hint_not_emitted_for_semicolon_in_message(self, claude_config_repo):
        """Semicolons in prose (commit message body) do not trigger the chain hint.
        Best-effort: the helper detects && and || only, excluding ; to avoid
        false positives on prose semicolons. This is a known design tradeoff;
        see the chain_split_hint_if_chained helper comment."""
        result = subprocess.run(
            [str(DENY_PRIVATE_PROJECT_REFS_HOOK)],
            input=json.dumps(bash_input(
                "git commit -m 'Fix WIDGET-123; also update docs'"
            )),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Tip: this command chains" not in reason

    # -- Command-detection evasion resistance ------------------------------
    # Surface detection word-walks shell fragments instead of matching
    # `git commit` / `gh pr` as a literal substring, so a global git flag,
    # an env-var prefix, an absolute path, or a `$()` wrapper between the
    # command word and its subcommand cannot hide a gated call. Mirrors
    # test_deny_pii_in_commits.py's git-flag detection cases.

    @pytest.mark.parametrize(
        "command",
        [
            "git -c user.name=ci commit -m 'Fix WIDGET-123'",
            "git -C /tmp commit -m 'Fix WIDGET-123'",
            "git --git-dir=/tmp/g --work-tree=/tmp/w commit -m 'Fix WIDGET-123'",
            "GIT_DIR=/tmp/g git commit -m 'Fix WIDGET-123'",
        ],
        ids=["c-config-flag", "C-path-flag", "git-dir-equals-flag", "env-var-prefix"],
    )
    def test_git_commit_flag_evasion_forms_denied(self, claude_config_repo, command):
        """A commit with a global flag or env-var prefix between `git` and
        `commit` must still be detected and its message scanned. Detection
        keys on the command shape; for the `-C` form the staged-diff scan
        still targets the session repo, not the `-C` path (a documented
        known gap), so these cases deny on the message token alone."""
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_git_commit_config_flag_clean_message_allowed(self, claude_config_repo):
        """A `git -c` commit with a clean message is not falsely flagged —
        the regex-to-word-walk swap preserves the allow path."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git -c core.pager=cat commit -m 'Refactor the parser'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_git_non_commit_subcommand_with_flag_and_token_allowed(self, claude_config_repo):
        """A non-commit git subcommand carrying a tracker-shaped token is
        not gated — the word-walk extracts the real subcommand (`log`),
        not `commit`, so a global flag cannot cause a false dispatch."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("git -c core.pager=cat log --grep WIDGET-123"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "GH_TOKEN=ci gh pr create --body 'Fixes WIDGET-123'",
            "/usr/bin/gh pr edit 1 --body 'Fixes WIDGET-123'",
            "OUT=$(gh pr create --body 'Fixes WIDGET-123')",
            "OUT=`gh pr create --body 'Fixes WIDGET-123'`",
        ],
        ids=["env-var-prefix", "absolute-path", "command-substitution", "backtick-substitution"],
    )
    def test_gh_pr_evasion_forms_denied(self, claude_config_repo, command):
        """gh pr forms with an env-var prefix, an absolute path, or wrapped
        in `$()` / backticks must still dispatch the PR-body scan. `$()` and
        backticks are separate fragment-split branches in _lib_split_fragments,
        so both are exercised."""
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_gh_api_command_substitution_form_denied(self, claude_config_repo):
        """A mutating `gh api` call wrapped in `$()` is split into its own
        fragment and detected — the literal regex over the whole command
        string would not have matched the `gh api` inside `$()`."""
        command = "OUT=$(gh api repos/x/y/issues/1/comments -X POST -f body='Fixes WIDGET-123')"
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_gh_non_gated_subcommand_with_env_prefix_allowed(self, claude_config_repo):
        """An env-var-prefixed non-gated gh subcommand (`gh pr comment`)
        carrying a tracker token is not gated — fragment_gh_gated_surface
        keys on the command path (`pr` followed by `create`/`edit`), so
        `pr comment` resolves to no gated surface."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("GH_TOKEN=ci gh pr comment 1 --body 'has WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "gh -X POST api repos/x/y/issues/1/comments -f body='Fixes WIDGET-123'",
            "gh --method POST api repos/x/y/issues/1/comments -f body='Fixes WIDGET-123'",
        ],
        ids=["method-short-flag-before-api", "method-long-flag-before-api"],
    )
    def test_gh_api_flag_before_subcommand_denied(self, claude_config_repo, command):
        """cobra lets `gh api`'s own flags be written before the `api`
        subcommand word. A mutating `gh api` whose `-X` / `--method`
        precedes `api` must still dispatch the body scan — detection keys
        on the `api` command word, not on `gh` being adjacent to it."""
        assert run_hook(DENY_PRIVATE_PROJECT_REFS_HOOK, bash_input(command), cwd=claude_config_repo) == "deny"

    def test_gh_pr_flag_before_subcommand_denied(self, claude_config_repo):
        """`gh pr create` accepts `--repo` written before the `pr create`
        command path. A PR-body tracker token must still be detected — the
        `pr`/`create` pair stays contiguous, so adjacency detection holds."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh --repo owner/repo pr create --body 'Fixes WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "deny"
        )

    def test_gh_non_gated_subcommand_mentioning_pr_allowed(self, claude_config_repo):
        """A non-gated gh subcommand whose argument text merely contains the
        word `pr` (here `gh issue create`, with `pr` in the title) is not
        falsely gated — `gh pr` detection requires `pr` immediately followed
        by `create`/`edit`, and here `create` precedes the stray `pr`."""
        assert (
            run_hook(
                DENY_PRIVATE_PROJECT_REFS_HOOK,
                bash_input("gh issue create --title 'open a pr' --body 'tracking WIDGET-123'"),
                cwd=claude_config_repo,
            )
            == "allow"
        )

    def test_missing_lib_sh_fails_closed(self, claude_config_repo, tmp_path):
        """Command detection depends on _lib.sh. If the hook cannot source
        it, it must deny (fail-closed), not exit 0 — a broken _lib.sh must
        not silently turn the redaction gate into a no-op. Exercised by
        running a copy of the hook from a directory with no _lib.sh
        alongside it, so `. "$(dirname "$0")/_lib.sh"` fails.

        The pre-source `emit_deny` bootstrap (see _lib.sh's _lib_emit_deny
        contract comment) is a minimal hard-block stub — it exits 2 with the
        reason on stderr rather than the exit-0 JSON envelope the post-source
        path produces, since _lib_jq isn't available yet to encode one."""
        isolated_hook = tmp_path / "deny-private-project-refs.sh"
        isolated_hook.write_bytes(DENY_PRIVATE_PROJECT_REFS_HOOK.read_bytes())
        isolated_hook.chmod(0o755)
        result = subprocess.run(
            [str(isolated_hook)],
            input=json.dumps(bash_input("git commit -m 'Fix WIDGET-123'")),
            capture_output=True,
            text=True,
            cwd=claude_config_repo,
            check=False,
        )
        assert result.returncode == 2, f"expected hard-block exit 2, got {result.returncode}"
        assert not result.stdout.strip(), f"expected no stdout, got {result.stdout!r}"
        assert "_lib.sh" in result.stderr
