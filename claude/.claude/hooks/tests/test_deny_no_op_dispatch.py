"""Tests for deny-no-op-dispatch.sh.

Decision rule: see the hook's own header comment and
docs/design-decisions/no-op-dispatch-hook-gate.md. Malformed-input,
empty-stdin, non-object-tool_input, and missing-_lib.sh deny cases are not
repeated here -- test_hook_alignment.py's Layer 2 already parametrizes
every gate-class hook over them.
"""
from __future__ import annotations

import pytest
from helpers import HOOKS_DIR, agent_input, bash_input, run_hook, run_hook_reason

DENY_NO_OP_DISPATCH_HOOK = HOOKS_DIR / "deny-no-op-dispatch.sh"

# Every NOOP_STUB_TOKEN_RE and NOOP_PHRASE_RE alternative not already
# exercised by a dedicated test below, as a standalone or minimally-wrapped
# short prompt, plus one mixed-case fixture pinning grep -qiE's
# case-insensitivity.
NOOP_IDIOM_COVERAGE_TABLE: list[tuple[str, str]] = [
    ("wait", "stub_wait"),
    ("nothing", "stub_nothing"),
    ("standby", "stub_standby"),
    ("stand by", "stub_stand_by"),
    ("ack", "stub_ack"),
    ("no-op", "stub_bare_no_op"),
    ("just wait", "phrase_just_wait"),
    ("report back immediately", "phrase_report_back_immediately"),
    ("no action", "phrase_no_action"),
    ("do not read any", "phrase_do_not_read_any"),
    ("do not run any", "phrase_do_not_run_any"),
    ("do not investigate any", "phrase_do_not_investigate_any"),
    ("exists only so", "phrase_exists_only_so"),
    ("exists only to", "phrase_exists_only_to"),
    ("Report Back Immediately", "phrase_mixed_case"),
]

# The 2026-09-08 incident prompt (302 characters), verbatim from this
# repo's own transcript history, with the real agentId replaced by a
# same-length synthetic placeholder -- the idiom-bearing text is preserved
# exactly, per docs/design-decisions/no-op-dispatch-hook-gate.md's
# provenance requirement.
INCIDENT_PROMPT = (
    "Do nothing except wait. This turn exists only so the parent session "
    "yields control back cleanly while the ciso-reviewer background "
    "dispatch (agentId a0000000000000000) finishes. Reply with a one-line "
    "acknowledgment and stop — do not read any files, do not run any "
    "commands, do not investigate anything."
)

# A synthesized multi-paragraph structured task prompt (775 characters),
# well over NOOP_MAX_PROMPT_LEN (600) -- pins that a genuine task
# specification is allowed through regardless of its own content.
STRUCTURED_TASK_PROMPT = (
    "Implement the retry-with-backoff logic described in "
    "docs/http-retry-policy.md for the internal HTTP client, which "
    "currently retries every failed request immediately with no delay and "
    "no cap, hammering the upstream service during an outage.\n\n"
    "Your job: add exponential backoff with jitter to "
    "src/http_client.py's request() method. Cap the retry count at 5 "
    "attempts and the per-attempt delay at 30 seconds, and cover the "
    "boundary between the 4th and 5th retry with a new unit test in "
    "tests/test_http_client.py. Match the existing test file's fixture "
    "and mocking conventions rather than introducing a new pattern.\n\n"
    "Report back: which files you changed, the exact backoff formula and "
    "constants you chose, and the exact test command you ran to confirm "
    "the new boundary case passes."
)

# A long, over-ceiling prompt that deliberately contains two idioms
# ("do nothing", "report back immediately") inside a legitimate
# conditional clause -- pins that the length conjunct, not the idiom list,
# is what makes a real task specification unreachable by this gate.
ADVERSARIAL_OVER_CEILING_PROMPT = (
    "Check the feature-flag rollout status before doing anything else. If "
    "the flag is still in the 'paused' state, do nothing further this "
    "turn — leave the rollout exactly as it is and report back "
    "immediately once you've confirmed the paused state, since the "
    "on-call engineer needs to know within the minute rather than at the "
    "end of a longer investigation. If the flag has already moved to "
    "'active', proceed with the full verification checklist in "
    "docs/rollout-checklist.md instead: confirm the canary cohort's error "
    "rate is still within the 0.5% budget, confirm the rollback runbook "
    "link in the on-call channel still resolves, and only then report "
    "your findings in the usual end-of-task summary, citing the exact "
    "dashboard query you used to confirm the error-rate figure."
)

assert len(INCIDENT_PROMPT) == 302
assert len(STRUCTURED_TASK_PROMPT) > 600
assert len(ADVERSARIAL_OVER_CEILING_PROMPT) > 600


def _padded_idiom_prompt(length: int) -> str:
    """Build an idiom-bearing prompt ("do nothing" followed by filler)
    padded to exactly `length` characters, for the boundary tests below."""
    base = "do nothing "
    assert length >= len(base)
    return base + "x" * (length - len(base))


class TestDenyNoOpDispatch:
    # ------------------------------------------------------------------ #
    # Deny                                                                #
    # ------------------------------------------------------------------ #

    def test_incident_prompt_denied(self, isolated_home):
        """The verbatim 2026-09-08 incident prompt (subagent_type=fork)."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt=INCIDENT_PROMPT, subagent_type="fork"),
                home=isolated_home,
            )
            == "deny"
        )

    def test_noop_prompt_with_description_tell_denied(self, isolated_home):
        """prompt="noop" alone trips the anchored stub arm; the
        description replays the 2026-09-06 occurrence's own tell text,
        which this gate does not need to read to deny the dispatch."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(
                    prompt="noop",
                    description="Wait handling — no action, agents still running",
                    subagent_type="general-purpose",
                ),
                home=isolated_home,
            )
            == "deny"
        )

    def test_placeholder_prompt_no_subagent_type_denied(self, isolated_home):
        """No subagent_type field at all -- one pre-fix occurrence recorded
        none, so the gate must not depend on the field being present.
        agent_input() omits subagent_type from tool_input whenever it is
        not passed, so this payload already carries no such field."""
        payload = agent_input(prompt="placeholder", description="noop placeholder")
        assert "subagent_type" not in payload["tool_input"]
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, payload, home=isolated_home) == "deny"

    def test_task_tool_name_no_op_prompt_denied(self, isolated_home):
        """The Agent|Task matcher union covers a Task-dispatched spawn too."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(tool_name="Task", prompt="noop"),
                home=isolated_home,
            )
            == "deny"
        )

    def test_idiom_spanning_a_newline_denied(self, isolated_home):
        """Whitespace-collapse means "do\\nnothing" still matches the same
        as "do nothing" on one line."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt="do\nnothing"),
                home=isolated_home,
            )
            == "deny"
        )

    def test_occupy_the_turn_idiom_denied(self, isolated_home):
        """claude/.claude/CLAUDE.md's Agent Briefing bullet names this
        phrase verbatim as a prohibited shape."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt="This dispatch exists only to occupy the turn while the other agent finishes."),
                home=isolated_home,
            )
            == "deny"
        )

    def test_hold_while_idiom_denied(self, isolated_home):
        """The same CLAUDE.md Agent Briefing bullet names this phrase too."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt="Hold while the other dispatch wraps up, then end your turn."),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "prompt",
        [pytest.param(p, id=tid) for p, tid in NOOP_IDIOM_COVERAGE_TABLE],
    )
    def test_stub_token_and_phrase_idiom_coverage_denied(self, isolated_home, prompt):
        """Direct deny coverage for every NOOP_STUB_TOKEN_RE and
        NOOP_PHRASE_RE alternative not already pinned by a dedicated test
        above."""
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, agent_input(prompt=prompt), home=isolated_home) == "deny"

    def test_deny_message_names_compliance_and_escalation_paths(self, isolated_home):
        """Deny message names the CLAUDE.md rule, the end-the-turn
        compliance path, the rephrase path, and the subagent-report clause
        -- and never instructs anyone to disable the gate (there is no
        sentinel to disable)."""
        reason = run_hook_reason(DENY_NO_OP_DISPATCH_HOOK, agent_input(prompt="noop"), home=isolated_home)
        assert reason is not None
        assert "Agent Briefing" in reason
        assert "end the turn without a tool call" in reason
        assert "state that work in the prompt and retry" in reason
        assert "report this denial to your dispatcher" in reason
        assert "disable" not in reason.lower()

    # ------------------------------------------------------------------ #
    # Allow                                                               #
    # ------------------------------------------------------------------ #

    def test_structured_task_prompt_over_ceiling_allowed(self, isolated_home):
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt=STRUCTURED_TASK_PROMPT),
                home=isolated_home,
            )
            == "allow"
        )

    def test_adversarial_idiom_bearing_prompt_over_ceiling_allowed(self, isolated_home):
        """The length conjunct, not the idiom list, is what protects a
        real task specification that happens to use idiom vocabulary in a
        legitimate conditional clause."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt=ADVERSARIAL_OVER_CEILING_PROMPT),
                home=isolated_home,
            )
            == "allow"
        )

    def test_short_legitimate_prompt_no_idiom_allowed(self, isolated_home):
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt="Find where _lib_config_dir is defined."),
                home=isolated_home,
            )
            == "allow"
        )

    def test_bare_default_payload_allowed(self, isolated_home):
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, agent_input(), home=isolated_home) == "allow"

    def test_no_op_substring_in_non_anchored_position_allowed(self, isolated_home):
        """"no-op" appearing as a word inside a longer, legitimate prompt
        must not trip the anchored stub arm, which requires the entire
        prompt to be the token."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(prompt="Find every no-op dispatch fixture"),
                home=isolated_home,
            )
            == "allow"
        )

    def test_wrong_tool_name_allowed(self, isolated_home):
        """Defense-in-depth: a Bash call is never denied by this gate."""
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, bash_input("noop"), home=isolated_home) == "allow"

    def test_no_prompt_and_no_description_keys_allowed(self, isolated_home):
        """Missing prompt/description fields entirely -- not merely empty
        strings -- must not deny: the anchored stub arm requires at least
        one token, so an absent-and-thus-empty prompt matches nothing."""
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, {"tool_name": "Agent", "tool_input": {}}, home=isolated_home) == "allow"

    def test_just_wait_in_description_with_genuine_prompt_allowed(self, isolated_home):
        """Both phrase arms match `prompt` alone -- an idiom appearing only
        in `description` does not trip this gate."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(
                    prompt="Poll the deploy status endpoint.",
                    description="Just wait for the deploy to finish, then report status",
                ),
                home=isolated_home,
            )
            == "allow"
        )

    def test_no_action_in_description_with_genuine_prompt_allowed(self, isolated_home):
        """Same as above for the "no action" idiom -- a routine
        "no action needed from you" framing confined to `description`."""
        assert (
            run_hook(
                DENY_NO_OP_DISPATCH_HOOK,
                agent_input(
                    prompt="Confirm the merge went through.",
                    description="No action needed from the user, just confirming the merge",
                ),
                home=isolated_home,
            )
            == "allow"
        )

    # ------------------------------------------------------------------ #
    # Boundary                                                            #
    # ------------------------------------------------------------------ #

    def test_idiom_bearing_prompt_one_under_ceiling_denied(self, isolated_home):
        prompt = _padded_idiom_prompt(599)
        assert len(prompt) == 599
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, agent_input(prompt=prompt), home=isolated_home) == "deny"

    def test_idiom_bearing_prompt_at_ceiling_allowed(self, isolated_home):
        prompt = _padded_idiom_prompt(600)
        assert len(prompt) == 600
        assert run_hook(DENY_NO_OP_DISPATCH_HOOK, agent_input(prompt=prompt), home=isolated_home) == "allow"
