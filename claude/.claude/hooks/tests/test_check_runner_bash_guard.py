"""Tests for check-runner-bash-guard.sh."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
    run_hook_reason,
)

GUARD_HOOK = HOOKS_DIR / "check-runner-bash-guard.sh"


def cr_input(command: str) -> dict:
    """Bash payload as it appears when dispatched inside a check-runner subagent."""
    return bash_input(command, agent_type="check-runner")


class TestDiscriminator:
    """The hook short-circuits when the call did not originate inside a
    check-runner subagent. Parent payloads (no agent_type field) and
    other-subagent payloads (agent_type != "check-runner") pass
    through with no opinion."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m wip",
            "git push --force",
            "git reset --hard HEAD~1",
            "supabase db reset",
            "rm -rf /",
            "npm install",
        ],
    )
    def test_parent_payload_allowed(self, command):
        # No agent_type field — parent context.
        assert run_hook(GUARD_HOOK, bash_input(command)) == "allow"

    @pytest.mark.parametrize(
        "other_agent",
        ["code-writer", "general-purpose", "Explore", "staff-backend-engineer"],
    )
    def test_other_subagent_allowed(self, other_agent):
        # agent_type present but not check-runner — siblings legitimately
        # run package installs, container commands, git mutations, etc.
        assert run_hook(GUARD_HOOK, bash_input("supabase db reset", agent_type=other_agent)) == "allow"
        assert run_hook(GUARD_HOOK, bash_input("git push --force", agent_type=other_agent)) == "allow"


class TestCheckRunnerBashGuardAllows:
    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git diff HEAD~1",
            "git log --oneline -10",
            "git show HEAD",
            "git fetch origin",
            "git branch",
            "git rev-parse --show-toplevel",
            "git remote -v",
            "git blame file.txt",
            "git ls-files",
            "git ls-tree HEAD",
            "git ls-remote origin",
            "git reflog",
            "git shortlog -sn",
            "git describe --tags",
            "git for-each-ref",
            "git fsck",
            "git rev-list HEAD",
            "git verify-commit HEAD",
            "git version",
            "git worktree list",
            "git tag",
            "git var GIT_AUTHOR_IDENT",
        ],
    )
    def test_readonly_git_commands_allowed(self, command):
        assert run_hook(GUARD_HOOK, cr_input(command)) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "pytest claude/.claude/",
            "ruff check claude/.claude/",
            "npm run verify",
            "npm run lint",
            "echo hello",
            "cat file.txt",
            "ls -la",
            "python -m pytest",
            # Proves the global pattern is verb-pair-scoped — does not
            # collapse `<word> install` into a deny shape.
            "make install-test-deps",
            "meson install",
            # `rm -rf` of root or HOME is denied; subpath cleanup
            # legitimately happens during checks (clearing build
            # artifacts, removing scratch dirs under /tmp).
            "rm -rf /tmp/test-scratch",
            "rm -rf ./node_modules",
            "rm -rf build/",
            "rm -rf $TMPDIR/artifacts",
        ],
    )
    def test_safe_check_commands_pass_through(self, command):
        assert run_hook(GUARD_HOOK, cr_input(command)) == "allow"

    def test_chained_readonly_git_allowed(self):
        assert run_hook(GUARD_HOOK, cr_input("git status && git diff")) == "allow"

    def test_git_in_path_does_not_trigger(self):
        assert run_hook(GUARD_HOOK, cr_input("ls .github/workflows/")) == "allow"

    def test_gitignore_does_not_trigger(self):
        assert run_hook(GUARD_HOOK, cr_input("cat .gitignore")) == "allow"


class TestGlobalGenericShapeDenies:
    """The vendor-name-free global layer that ships to every stow user."""

    @pytest.mark.parametrize(
        "command",
        [
            # Database-CLI verb-pair convention — exercised against several
            # vendors in the fixture; the hook regex itself names none.
            "supabase db reset",
            "supabase db push",
            "supabase db migrate",
            "supabase db seed",
            "prisma db reset",
            "prisma db push",
            "drizzle db push",
            "atlas db migrate",
            "sqlx db reset",
        ],
    )
    def test_db_verb_pair_denied(self, command):
        assert run_hook(GUARD_HOOK, cr_input(command)) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "git push --force",
            "git push -f origin main",
            "git reset --hard HEAD~1",
            "git reset --hard origin/main",
        ],
    )
    def test_destructive_git_shapes_denied(self, command):
        assert run_hook(GUARD_HOOK, cr_input(command)) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /",
            "rm -rf $HOME",
            "rm -rf ~",
        ],
    )
    def test_categorical_rm_denied(self, command):
        assert run_hook(GUARD_HOOK, cr_input(command)) == "deny"

    def test_deny_message_cites_global_layer(self):
        reason = run_hook_reason(GUARD_HOOK, cr_input("supabase db reset"))
        assert reason is not None
        assert "global generic-shape pattern" in reason

    def test_deny_message_includes_advice(self):
        reason = run_hook_reason(GUARD_HOOK, cr_input("supabase db reset"))
        assert reason is not None
        assert "HOOK_BLOCK" in reason
        assert "Do NOT retry" in reason
        assert "do NOT propose an allow-rule" in reason

    def test_chained_safe_then_denied_blocks_whole_call(self):
        assert run_hook(GUARD_HOOK, cr_input("pytest && supabase db reset")) == "deny"

    def test_chained_denied_then_safe_blocks_whole_call(self):
        assert run_hook(GUARD_HOOK, cr_input("supabase db reset && pytest")) == "deny"


class TestGitAllowlistDenies:
    """The pre-existing read-only git allowlist still applies inside
    check-runner context."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'fix tests'",
            "git push origin main",
            "git checkout -- path/to/file",
            "git rebase origin/main",
            "git merge main",
            "git stash",
            "git apply patch.diff",
            "git cherry-pick abc123",
            "git clean -fd",
            "git add .",
            "git rm file.txt",
            "git mv old.txt new.txt",
        ],
    )
    def test_write_git_commands_denied(self, command):
        assert run_hook(GUARD_HOOK, cr_input(command)) == "deny"

    def test_deny_message_instructs_return_verdict(self):
        reason = run_hook_reason(GUARD_HOOK, cr_input("git commit -m fix"))
        assert reason is not None
        assert "return the verdict" in reason

    def test_deny_message_names_the_subcommand(self):
        reason = run_hook_reason(GUARD_HOOK, cr_input("git push origin main"))
        # `git push --force/-f` is matched by the global destructive-shape
        # layer first; this fixture uses a non-force push so it falls
        # through to the read-only-allowlist deny which names the subcmd.
        assert reason is not None
        assert "push" in reason

    def test_chained_write_after_readonly_denied(self):
        assert run_hook(GUARD_HOOK, cr_input("git status && git commit -m fix")) == "deny"

    def test_chained_write_before_readonly_denied(self):
        assert run_hook(GUARD_HOOK, cr_input("git commit -m fix && git status")) == "deny"


class TestProjectLayerExtension:
    """`<cwd>/.claude/check-runner-deny-patterns.txt` extends the deny
    set with stack-specific patterns. The hook reads it at decision
    time from the current working directory."""

    def _project_dir(self, tmp_path: Path, lines: list[str] | None) -> Path:
        repo = tmp_path / "proj"
        (repo / ".claude").mkdir(parents=True)
        if lines is not None:
            (repo / ".claude" / "check-runner-deny-patterns.txt").write_text(
                "\n".join(lines) + "\n"
            )
        return repo

    def test_project_pattern_matches_denied(self, tmp_path):
        repo = self._project_dir(tmp_path, [
            "# package installs",
            r"\bnpm install\b",
            r"\bpip install\b",
        ])
        assert run_hook(GUARD_HOOK, cr_input("npm install lodash"), cwd=repo) == "deny"
        assert run_hook(GUARD_HOOK, cr_input("pip install requests"), cwd=repo) == "deny"

    def test_project_pattern_citation_in_deny_message(self, tmp_path):
        repo = self._project_dir(tmp_path, [
            "# header comment",
            "",
            r"\bnpm install\b",
        ])
        reason = run_hook_reason(GUARD_HOOK, cr_input("npm install"), cwd=repo)
        assert reason is not None
        assert ".claude/check-runner-deny-patterns.txt" in reason
        # Patterns start on line 3 (1-indexed): comment + blank + the regex.
        assert "line 3" in reason

    def test_project_file_present_but_pattern_unmatched_allows(self, tmp_path):
        repo = self._project_dir(tmp_path, [
            r"\bdocker rm\b",
            r"\bnpm install\b",
        ])
        # File present but the command doesn't match any pattern.
        assert run_hook(GUARD_HOOK, cr_input("pytest"), cwd=repo) == "allow"

    def test_project_file_absent_global_only(self, tmp_path):
        # No project file present at all.
        repo = self._project_dir(tmp_path, None)
        # Global still fires.
        assert run_hook(GUARD_HOOK, cr_input("supabase db reset"), cwd=repo) == "deny"
        # And package-install-shapes (not in global) pass through.
        assert run_hook(GUARD_HOOK, cr_input("npm install lodash"), cwd=repo) == "allow"

    def test_malformed_regex_logs_and_skips_line(self, tmp_path):
        # An unmatched paren is a malformed POSIX ERE.
        repo = self._project_dir(tmp_path, [
            r"\bnpm install\b",
            r"[unclosed-bracket",
            r"\bpip install\b",
        ])
        result = subprocess.run(
            [str(GUARD_HOOK)],
            input=json.dumps(cr_input("pip install requests")),
            capture_output=True,
            text=True,
            cwd=repo,
            check=False,
        )
        assert "skipping malformed regex" in result.stderr
        # Sibling lines still apply — pip install still denied.
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_unreadable_project_file_logs_and_continues_with_global_only(self, tmp_path):
        repo = self._project_dir(tmp_path, [r"\bnpm install\b"])
        project_file = repo / ".claude" / "check-runner-deny-patterns.txt"
        project_file.chmod(0o000)
        try:
            result = subprocess.run(
                [str(GUARD_HOOK)],
                input=json.dumps(cr_input("npm install")),
                capture_output=True,
                text=True,
                cwd=repo,
                check=False,
            )
            assert "is not readable" in result.stderr
            # npm install not in the global layer; pass through.
            assert result.stdout.strip() == ""
            # Global pattern still fires under same broken project file.
            result2 = subprocess.run(
                [str(GUARD_HOOK)],
                input=json.dumps(cr_input("supabase db reset")),
                capture_output=True,
                text=True,
                cwd=repo,
                check=False,
            )
            assert result2.stdout.strip()
            payload = json.loads(result2.stdout)
            assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        finally:
            project_file.chmod(0o644)

    def test_blank_and_comment_lines_skipped(self, tmp_path):
        repo = self._project_dir(tmp_path, [
            "# comment 1",
            "",
            "   ",
            "# comment 2",
            r"\byarn add\b",
        ])
        assert run_hook(GUARD_HOOK, cr_input("yarn add lodash"), cwd=repo) == "deny"
        # Comments and blanks must not become deny patterns themselves.
        assert run_hook(GUARD_HOOK, cr_input("pytest"), cwd=repo) == "allow"


class TestCheckRunnerBashGuardFailClosed:
    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(GUARD_HOOK)],
            input="not-json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "expected deny JSON, got empty output (allow)"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_empty_command_allowed(self):
        assert run_hook(GUARD_HOOK, cr_input("")) == "allow"

    def test_missing_command_field_allowed(self):
        # agent_type present, but no command — nothing to enforce.
        payload = {"tool_name": "Bash", "tool_input": {}, "agent_type": "check-runner"}
        assert run_hook(GUARD_HOOK, payload) == "allow"
