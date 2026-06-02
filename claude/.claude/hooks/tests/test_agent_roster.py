"""Structural invariants for claude/.claude/agents/*.md.

These tests enforce conventions that are stated as prose rules in
code-review/SKILL.md and agent documentation — specifically the file-based
output canary requirement. They catch the failure mode where a new reviewer
agent is added to the roster without the required frontmatter or output section.
"""
from __future__ import annotations

import re

from helpers import CLAUDE_DIR

AGENTS_DIR = CLAUDE_DIR / "agents"

# The set of reviewer agent files that must carry the file-based output canary.
# This list is the authoritative roster. When a new reviewer is added, add it
# here — the test will then enforce the structural requirements automatically.
REVIEWER_AGENTS = [
    "ciso-reviewer.md",
    "staff-analytics-engineer.md",
    "staff-backend-engineer.md",
    "staff-data-engineer.md",
    "staff-frontend-engineer.md",
    "staff-platform-engineer.md",
    "staff-product-engineer.md",
    "staff-sdet.md",
]

# Agents that exist in the directory but are not code-review dispatched
# reviewers — they do not receive findings_path and do not need the canary.
# When a new non-reviewer agent is added, add it here; the
# test_no_uncategorized_agents test will fail until it is categorized.
NON_REVIEWER_AGENTS = [
    "check-runner.md",  # executor-style; dispatches check suites, does not review
    "code-writer.md",   # implementer; writes code, does not review
]


class TestReviewerAgentRoster:
    def test_all_reviewer_agents_exist(self):
        """Every agent named in the roster list must have a corresponding file."""
        for name in REVIEWER_AGENTS:
            path = AGENTS_DIR / name
            assert path.exists(), (
                f"Reviewer agent file missing: {path}. "
                "If the file was renamed or removed, update REVIEWER_AGENTS in this test."
            )

    def test_all_reviewer_agents_have_write_tool(self):
        """Every reviewer agent must declare Write in its tools: frontmatter line.

        The file-based output canary requires the Write tool. Passing findings_path
        to an agent without Write causes a permissions error and full-inline fallback,
        defeating the context-saving mechanism. This is the structural half of the
        ordering rule stated in code-review/SKILL.md: add Write to the agent BEFORE
        updating the dispatcher to pass findings_path.
        """
        for name in REVIEWER_AGENTS:
            path = AGENTS_DIR / name
            content = path.read_text()
            tools_match = re.search(r"^tools:\s*(.+)$", content, re.MULTILINE)
            assert tools_match, f"{name}: no 'tools:' line found in frontmatter"
            tools_value = tools_match.group(1)
            assert "Write" in tools_value, (
                f"{name}: 'Write' not found in tools: {tools_value!r}. "
                "All reviewer agents must declare Write for the file-based output canary."
            )

    def test_all_reviewer_agents_have_file_based_output_section(self):
        """Every reviewer agent must contain a '### File-based output' section.

        The file-based output section contains the contract that drives the canary
        behavior. Without it, the agent ignores findings_path and returns full inline
        output. This is the prose half of the ordering rule: add the section BEFORE
        updating the dispatcher.
        """
        for name in REVIEWER_AGENTS:
            path = AGENTS_DIR / name
            content = path.read_text()
            assert "### File-based output" in content, (
                f"{name}: '### File-based output' section missing. "
                "All reviewer agents must contain this section for the file-based output canary."
            )

    def test_no_uncategorized_agents(self):
        """Every agent file in agents/ must appear in REVIEWER_AGENTS or NON_REVIEWER_AGENTS.

        This inverse check ensures that a new agent added to the directory cannot
        silently escape the canary enforcement. Without it, a contributor who adds
        a new reviewer agent without updating REVIEWER_AGENTS gets no test failure —
        the canary structural tests simply never run for that agent.

        When a new agent is added:
        - If it is a code-review dispatched reviewer → add to REVIEWER_AGENTS
        - If it is not a reviewer → add to NON_REVIEWER_AGENTS with a comment
        """
        known = set(REVIEWER_AGENTS) | set(NON_REVIEWER_AGENTS)
        actual = {path.name for path in AGENTS_DIR.glob("*.md")}
        uncategorized = actual - known
        assert not uncategorized, (
            f"Uncategorized agent file(s): {sorted(uncategorized)}. "
            "Add each to REVIEWER_AGENTS (if it should carry the canary) or "
            "NON_REVIEWER_AGENTS (if not) in this test file."
        )
