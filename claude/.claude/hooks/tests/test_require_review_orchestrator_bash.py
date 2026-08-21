"""Tests for require-review-orchestrator-bash.sh."""
from __future__ import annotations

import pytest
from helpers import HOOKS_DIR, bash_input, run_hook, run_hook_reason

REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK = HOOKS_DIR / "require-review-orchestrator-bash.sh"

AGENT = "review-orchestrator"

# The closed verification-command allowlist: exactly the forms root
# CLAUDE.md's own Commands section names, plus their worktree-relative forms.
CLOSED_VERIFICATION_COMMANDS = [
    ".venv/bin/pytest claude/.claude/",
    ".venv/bin/ruff check claude/.claude/",
    "scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck",
    "../../../.venv/bin/pytest claude/.claude/",
    "../../../.venv/bin/ruff check claude/.claude/",
    "scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck",
]


class TestClosedVerificationCommandsAllowed:
    @pytest.mark.parametrize("command", CLOSED_VERIFICATION_COMMANDS)
    def test_verification_command_allowed(self, command):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "allow"


class TestReadOnlyGitSubcommandsAllowed:
    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log -5",
            "git diff HEAD",
            "git show HEAD",
        ],
    )
    def test_readonly_git_subcommand_allowed(self, command):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "allow"


class TestHelperScriptInvocationsAllowed:
    @pytest.mark.parametrize(
        "command",
        [
            "~/.claude/scripts/marker.sh write code-review",
            "~/.claude/scripts/review-ledger.sh show",
            "~/.claude/scripts/orchestrator-checkpoint.sh read code-review-my-branch-1700000000-abcd1234",
        ],
    )
    def test_helper_script_invocation_allowed(self, command):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "allow"

    def test_trailing_dev_null_stderr_suppression_still_allowed(self):
        """The '2>/dev/null' suffix enforce-marker-script-shape.sh itself
        blesses must not be caught by the redirect-denial check below --
        only a redirect to something other than /dev/null is unsafe."""
        command = "~/.claude/scripts/marker.sh write code-review 2>/dev/null"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "allow"


class TestMutatingCommandsDenied:
    @pytest.mark.parametrize(
        "command",
        [
            "echo hi > file.txt",
            "sed -i 's/a/b/' file.txt",
            "git commit -m foo",
            "git add file.txt",
            "rm file.txt",
            "mv a.txt b.txt",
            "cp a.txt b.txt",
        ],
    )
    def test_mutating_command_denied(self, command):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "git branch attacker-branch",
            "git branch",
            "git tag -d v1",
            "git tag newtag",
            "git tag",
            "git symbolic-ref HEAD refs/heads/other",
            "git fetch https://example.invalid/repo.git refs/heads/main",
            "git remote add x https://example.invalid/repo.git",
            "git ls-remote https://example.invalid/repo.git",
        ],
    )
    def test_git_state_mutation_or_network_egress_capable_subcommand_denied(self, command):
        """These six subcommands can mutate ref state or issue network
        egress under their own bare/flagged form, so the strict allowlist
        excludes them entirely -- including their otherwise-harmless bare
        listing forms (a plain 'git branch'/'git tag'), which is the
        intended, documented over-denial rather than a regression."""
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "git worktree add ../other-checkout",
            "git worktree remove ../other-checkout",
            "git reflog expire --expire=now --all",
            "git fsck --lost-found",
            "git worktree list",
            "git reflog show",
            "git fsck",
        ],
    )
    def test_worktree_reflog_fsck_denied(self, command):
        """git worktree can create or delete another live worktree's
        checkout (this repo runs many parallel worktrees for concurrent
        sessions), git reflog can permanently expire otherwise-recoverable
        history, and git fsck can write dangling objects into
        .git/lost-found/ -- all under their own bare/flagged form, so the
        strict allowlist excludes them entirely, including their
        otherwise-harmless bare/listing forms ('git worktree list', 'git
        reflog show', a bare 'git fsck'), matching how the six subcommands
        above are handled rather than a regression."""
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_chained_verification_command_with_a_mutating_tail_denied(self):
        """A verification command is matched against the WHOLE command text,
        not per-fragment -- chaining a mutating command onto it must still
        fall through to the fragment-based check and be denied, per this
        hook's own documented 'no chaining' rule."""
        command = ".venv/bin/pytest claude/.claude/ && rm file.txt"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "~/.claude/scripts/review-ledger.sh show > src/tracked_file.py",
            "~/.claude/scripts/orchestrator-checkpoint.sh read run-id-123 >> src/tracked_file.py",
            "git diff HEAD > src/tracked_file.py",
        ],
    )
    def test_redirect_to_a_real_path_appended_to_an_otherwise_allowed_command_denied(self, command):
        """A redirect isn't a _lib_split_fragments split point, so it rides
        along inside an otherwise-allowed fragment -- 'review-ledger.sh
        show > src/tracked_file.py' would truncate a tracked file despite its
        leading command word matching a sanctioned helper script."""
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_denial_names_code_writer_as_the_alternative(self):
        reason = run_hook_reason(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK,
            bash_input("rm file.txt", agent_type=AGENT),
        )
        assert reason is not None
        assert "code-writer" in reason


class TestCommandInvokingGitFlagDenied:
    """A subcommand-word-only allowlist check treats 'git grep -O...' as safe
    because grep is read-only, while -O execs its argument as a command
    unconditionally -- these flags must deny regardless of subcommand."""

    def test_git_grep_open_files_in_pager_short_form_denied(self):
        command = 'git grep -O\'sh -c "touch /tmp/marker" #\' the README.md'
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_git_grep_open_files_in_pager_short_form_denied_with_no_embedded_dash_c(self):
        """Confound-free companion to the fixture above: that payload's own
        embedded 'sh -c "..."' produces a bare -c token that independently
        satisfies the -c arm, so it doesn't pin -O short-form detection on
        its own. This value carries no -c-shaped token anywhere."""
        command = "git grep -O'less' the README.md"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_git_log_open_files_in_pager_long_form_denied(self):
        command = "git log --open-files-in-pager=sh"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_git_log_bare_config_override_denied(self):
        command = "git -c core.pager=less log"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_git_diff_ext_diff_denied(self):
        command = "git diff --ext-diff"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_git_show_textconv_denied(self):
        command = "git show --textconv HEAD:file.bin"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_git_config_env_denied(self):
        command = "git log --config-env=core.pager=SOME_ENV_VAR"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_plain_readonly_git_log_with_no_unsafe_flag_still_allowed(self):
        """Regression guard: the new flag scan must not false-deny an
        ordinary read-only git subcommand with none of the unsafe flags."""
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input("git log -5", agent_type=AGENT)
        ) == "allow"


class TestEnvironmentVariableAssignmentBeforeGitDenied:
    """Git's own GIT_CONFIG_COUNT/GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n>
    mechanism (git-config(1) ENVIRONMENT) sets arbitrary config -- including
    diff.external -- with zero matching CLI flag token, entirely bypassing
    the flag-token scan above. A leading env-var assignment before the git
    word is denied as a blanket rule, not enumerated per variable name."""

    def test_git_config_env_var_mechanism_denied(self):
        command = (
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external "
            "GIT_CONFIG_VALUE_0='touch /tmp/marker #' git diff"
        )
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_non_git_prefixed_env_assignment_denied(self):
        """The rule is a blanket one on the WORD=value shape, not scoped to
        GIT_-prefixed names -- any env var could matter to some git
        mechanism now or in the future."""
        command = "FOO=bar git diff"
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input(command, agent_type=AGENT)
        ) == "deny"

    def test_plain_command_with_no_env_assignment_still_allowed(self):
        """Regression guard: the new env-assignment scan must not false-deny
        an ordinary git command with no leading env-var assignment."""
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, bash_input("git log --oneline", agent_type=AGENT)
        ) == "allow"


class TestRestrictionScopedToReviewOrchestrator:
    @pytest.mark.parametrize(
        "agent_type",
        ["code-writer", "general-purpose", "staff-sdet", "ciso-reviewer", None],
    )
    def test_mutating_command_allowed_for_every_other_agent_type(self, agent_type):
        """The restriction does not fire for any agent type other than
        review-orchestrator -- None means the main session (agent_type absent)."""
        payload = bash_input("rm file.txt", agent_type=agent_type)
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, payload) == "allow"


class TestMalformedPayloadHandledWithoutCrashing:
    def test_missing_command_field_does_not_crash(self):
        payload = {"tool_name": "Bash", "tool_input": {}, "agent_type": AGENT}
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, payload) == "deny"

    def test_non_string_agent_type_does_not_match_the_restricted_roster(self):
        """jq -r renders a non-string value rather than failing, so AGENT_TYPE
        becomes that rendering -- the predicate is exact-match against a
        closed set, so no rendering of a structured value can match it."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm file.txt"},
            "agent_type": {"unexpected": "object"},
        }
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, payload) == "allow"

    def test_empty_payload_does_not_crash(self):
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, {}) == "deny"

    def test_non_bash_tool_allowed_regardless_of_agent_type(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "x"}, "agent_type": AGENT}
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_BASH_HOOK, payload) == "allow"
