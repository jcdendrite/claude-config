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

    def test_reviewer_agents_list_is_sorted(self):
        """REVIEWER_AGENTS must be sorted alphabetically.

        TestFileBasedOutputBlockConsistency uses REVIEWER_AGENTS[0] as the canonical
        reference for byte-equivalence comparison. If the list is unsorted, the
        canonical anchor shifts silently when an entry is prepended, sending engineers
        to update the wrong agent file on a drift failure.
        """
        assert sorted(REVIEWER_AGENTS) == REVIEWER_AGENTS, (
            "REVIEWER_AGENTS is not sorted alphabetically. Keep it sorted so "
            "REVIEWER_AGENTS[0] is a stable canonical anchor for "
            "TestFileBasedOutputBlockConsistency."
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


class TestFileBasedOutputBlockConsistency:
    """Enforce byte-equivalence of the ### File-based output block across all
    reviewer agents.

    The block is intentionally duplicated (load-bearing instructional prose that
    must stand alone in each agent file — agent bodies are loaded verbatim with no
    @path/import mechanism). This test closes the only real downside of duplication:
    silent drift. Any divergence from the canonical form becomes a CI failure.
    """

    # Terminating sentinel: the block ends with this line. Using the terminator
    # rather than EOF is critical — staff-backend-engineer places the block
    # *before* a trailing ### Inline output section, so an EOF-bounded extraction
    # would pull extra content in for that agent and produce a false diff.
    _BLOCK_END_SENTINEL = (
        "When `findings_path` is absent, ignore this section and use the "
        "**Inline output** format."
    )

    @staticmethod
    def _extract_file_based_output_block(path) -> str:
        """Extract the ### File-based output block from an agent file.

        Extracts from the '### File-based output' heading line (inclusive) through
        the terminating sentinel line (inclusive). Returns the extracted text with
        the agent's own name normalized to 'AGENT_NAME' so all 8 blocks can be
        compared byte-for-byte.
        """
        content = path.read_text()
        # Normalize the agent-specific H1 title line before extraction so that
        # the one-line-per-agent difference does not cause a false inequality.
        # The H1 is inside the block: "   - `# <agent-name>` (H1 title)"
        agent_stem = path.stem  # e.g. "ciso-reviewer"
        # Assert the name appears before replacing — a no-op replace would
        # silently skip normalization and produce a false diff at comparison.
        assert f"# {agent_stem}" in content, (
            f"{path.name}: '# {agent_stem}' not found in file — normalization "
            "would be a no-op. The block structure may have changed."
        )
        content = content.replace(f"# {agent_stem}", "# AGENT_NAME")

        lines = content.splitlines(keepends=True)
        in_block = False
        block_lines = []
        sentinel = TestFileBasedOutputBlockConsistency._BLOCK_END_SENTINEL

        for line in lines:
            if line.rstrip("\n") == "### File-based output":
                in_block = True
            if in_block:
                block_lines.append(line)
                if sentinel in line:
                    break  # terminator included; stop here

        assert block_lines, (
            f"{path.name}: '### File-based output' block not found or terminator "
            f"'{sentinel}' missing — extraction failed."
        )
        return "".join(block_lines)

    def test_file_based_output_block_identical_across_reviewers(self):
        """All reviewer agents must carry a byte-identical ### File-based output block.

        The block text is identical across all 8 agents modulo the '# <agent-name>'
        H1 line (which is normalized before comparison). Any divergence means one
        agent has drifted from the shared protocol — the agent file must be updated
        to match the canonical form.

        The canonical reference is the first entry in REVIEWER_AGENTS (sorted order).
        When the canonical block changes, all other agents must be updated in the
        same commit.
        """
        blocks = {}
        for name in REVIEWER_AGENTS:
            path = AGENTS_DIR / name
            blocks[name] = self._extract_file_based_output_block(path)

        canonical_name = REVIEWER_AGENTS[0]
        canonical_block = blocks[canonical_name]

        mismatched = [
            name
            for name, block in blocks.items()
            if name != canonical_name and block != canonical_block
        ]
        assert not mismatched, (
            f"### File-based output block differs from canonical ({canonical_name}) "
            f"in: {sorted(mismatched)}. "
            "All reviewer agents must carry an identical block (modulo the H1 "
            "agent-name line, which is normalized before comparison). Update the "
            "diverging agent file(s) to match the canonical form, or update all "
            "agents atomically if the protocol is changing."
        )
