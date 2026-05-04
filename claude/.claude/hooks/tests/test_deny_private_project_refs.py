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
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
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
