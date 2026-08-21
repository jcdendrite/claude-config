"""Tests for require-review-orchestrator-agent-target.sh."""
from __future__ import annotations

import pytest
from helpers import HOOKS_DIR, run_hook, run_hook_reason

REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK = HOOKS_DIR / "require-review-orchestrator-agent-target.sh"


def _agent_dispatch(subagent_type: str, agent_type: str | None = "review-orchestrator") -> dict:
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": subagent_type, "description": "x", "prompt": "y"},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


class TestAllowedDispatchTargets:
    def test_dispatch_to_code_writer_allowed(self):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, _agent_dispatch("code-writer")
        ) == "allow"

    @pytest.mark.parametrize(
        "subagent_type",
        ["ciso-reviewer", "staff-backend-engineer", "staff-sdet", "skill-fidelity-reviewer"],
    )
    def test_dispatch_to_a_review_only_agents_member_allowed(self, subagent_type):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, _agent_dispatch(subagent_type)
        ) == "allow"


class TestDeniedDispatchTargets:
    def test_dispatch_to_general_purpose_denied(self):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, _agent_dispatch("general-purpose")
        ) == "deny"

    def test_dispatch_to_claude_denied(self):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, _agent_dispatch("claude")
        ) == "deny"

    def test_dispatch_to_an_unrecognized_type_denied(self):
        assert run_hook(
            REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, _agent_dispatch("some-other-agent")
        ) == "deny"

    def test_denial_names_the_closed_allowlist(self):
        reason = run_hook_reason(
            REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, _agent_dispatch("general-purpose")
        )
        assert reason is not None
        assert "code-writer" in reason


class TestRestrictionScopedToReviewOrchestratorCaller:
    @pytest.mark.parametrize(
        "agent_type", ["code-writer", "general-purpose", "staff-sdet", None]
    )
    def test_dispatch_to_general_purpose_allowed_for_every_other_caller(self, agent_type):
        """The restriction does not fire for any caller other than
        review-orchestrator -- None means the main session (agent_type absent)."""
        payload = _agent_dispatch("general-purpose", agent_type=agent_type)
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, payload) == "allow"


class TestMalformedPayloadHandledWithoutCrashing:
    def test_missing_subagent_type_field_denied(self):
        payload = {
            "tool_name": "Agent",
            "tool_input": {"description": "x", "prompt": "y"},
            "agent_type": "review-orchestrator",
        }
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, payload) == "deny"

    def test_non_string_subagent_type_denied(self):
        """jq -r renders a non-string value rather than failing, so TARGET
        becomes that rendering -- the predicate is exact-match against a
        closed set, so no rendering of a structured value can match it."""
        payload = _agent_dispatch({"unexpected": "object"})
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, payload) == "deny"

    def test_non_agent_tool_allowed_regardless_of_agent_type(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_type": "review-orchestrator"}
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, payload) == "allow"

    def test_empty_payload_does_not_crash(self):
        assert run_hook(REQUIRE_REVIEW_ORCHESTRATOR_AGENT_TARGET_HOOK, {}) == "deny"
