"""Structural invariants for claude/.claude/agents/*.md.

These tests enforce conventions that are stated as prose rules in
code-review/SKILL.md and agent documentation — specifically the file-based
output canary requirement. They catch the failure mode where a new reviewer
agent is added to the roster without the required frontmatter or output section.
"""
from __future__ import annotations

import re

import pytest
import yaml
from helpers import CLAUDE_DIR
from validate_skill_structure import parse_frontmatter

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
    "code-writer.md",   # implementer; self-reviews its own output, not a dispatcher-spawned reviewer
]

# Maximum description length for agent frontmatter.
# The full agent roster loads into every session's Agent-tool schema, so each
# description is a per-session token cost. 1000 is a regression guard with
# headroom above the current max (786 chars) — raise deliberately with rationale
# if a longer description is genuinely needed.
AGENT_DESCRIPTION_MAX_CHARS = 1000

# Expected model pin per agent. Reviewers are enforced sonnet by CLAUDE.md;
# non-reviewer model assignments are named here alongside their roster entry.
# When adding a new agent, add it to REVIEWER_AGENTS or NON_REVIEWER_AGENTS
# above AND add its expected model here — test_expected_model_map_is_complete
# will fail until both are updated.
NON_REVIEWER_MODELS = {
    "check-runner.md": "haiku",  # narrow executor
    "code-writer.md": "sonnet",  # implementer
}


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


class TestAgentFrontmatter:
    """Validates YAML frontmatter structure and model-pin conventions for all agent files."""

    # Evaluated at class-definition time (module import / collection phase) so
    # pytest.mark.parametrize receives a stable list before collection. Tests
    # that don't use parametrize re-glob at call time. A fixture that creates
    # synthetic agent files would not appear here — acceptable because no
    # fixture in this suite writes .md files into AGENTS_DIR. (tmp_path fixtures
    # must not write into AGENTS_DIR or the parametrize list will silently
    # exclude the synthetic file.)
    _AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))

    @pytest.mark.parametrize("agent_path", _AGENT_FILES, ids=lambda p: p.name)
    def test_frontmatter_parses_strictly(self, agent_path):
        """Each agent file must have frontmatter that parses as strict YAML.

        The most common failure: an unquoted description containing ': ' (colon-space).
        In a YAML plain scalar, ': ' is the mapping separator and causes a parse error
        in strict parsers (GitHub renders, loaders with SafeLoader). Fix by replacing
        the colon-space with ' — ' (em dash) or by double-quoting the whole value.
        """
        try:
            parse_frontmatter(agent_path)
        except (yaml.YAMLError, ValueError) as exc:
            pytest.fail(
                f"{agent_path.name}: frontmatter failed to parse: {exc}. "
                f"Common causes: missing closing '---' delimiter; "
                f"or a description containing ': ' (replace with ' — ' or double-quote the value)."
            )

    @pytest.mark.parametrize("agent_path", _AGENT_FILES, ids=lambda p: p.name)
    def test_required_fields_present(self, agent_path):
        """Every agent file must declare name, description, model, and tools."""
        fm = parse_frontmatter(agent_path)
        for field in ("name", "description", "model"):
            val = fm.get(field)
            if val is not None and not isinstance(val, str):
                pytest.fail(
                    f"{agent_path.name}: '{field}' field is {type(val).__name__} "
                    f"(got {val!r}), expected a string scalar."
                )
            assert val, (
                f"{agent_path.name}: required frontmatter field '{field}' is missing or empty."
            )
        tools_val = fm.get("tools")
        if tools_val is not None and not isinstance(tools_val, str):
            pytest.fail(
                f"{agent_path.name}: 'tools' field is {type(tools_val).__name__} "
                f"(got {tools_val!r}), expected a comma-separated string. "
                f"Declare as a scalar: tools: Read, Grep, Write"
            )
        assert tools_val and tools_val.strip(), (
            f"{agent_path.name}: 'tools' field is missing or empty. "
            f"Declare a comma-separated tools list."
        )

    @pytest.mark.parametrize("agent_path", _AGENT_FILES, ids=lambda p: p.name)
    def test_description_length(self, agent_path):
        """Description must not exceed AGENT_DESCRIPTION_MAX_CHARS characters."""
        fm = parse_frontmatter(agent_path)
        desc = fm.get("description") or ""
        assert len(desc) <= AGENT_DESCRIPTION_MAX_CHARS, (
            f"{agent_path.name}: description is {len(desc)} chars, "
            f"exceeds cap of {AGENT_DESCRIPTION_MAX_CHARS}. "
            f"Trim or raise the cap deliberately with a rationale comment."
        )

    @pytest.mark.parametrize("agent_path", _AGENT_FILES, ids=lambda p: p.name)
    def test_model_pinned_to_expected_value(self, agent_path):
        """Each agent must declare the exact model its role requires.

        Reviewers (REVIEWER_AGENTS) → 'sonnet' per CLAUDE.md.
        Non-reviewers → model declared in NON_REVIEWER_MODELS.
        """
        expected = {a: "sonnet" for a in REVIEWER_AGENTS} | NON_REVIEWER_MODELS
        fm = parse_frontmatter(agent_path)
        actual_model = fm.get("model")
        expected_model = expected.get(agent_path.name)
        assert actual_model == expected_model, (
            f"{agent_path.name}: model is '{actual_model}', expected '{expected_model}'. "
            f"Update the agent's frontmatter or, if the policy has changed, update "
            f"REVIEWER_AGENTS / NON_REVIEWER_MODELS in this file."
        )

    def test_expected_model_map_is_complete(self):
        """The expected-model map (REVIEWER_AGENTS + NON_REVIEWER_MODELS) must cover every agent file.

        Adding an agent without updating both the roster list and NON_REVIEWER_MODELS
        (or REVIEWER_AGENTS) will fail here. This mirrors test_no_uncategorized_agents.
        """
        all_agent_names = {p.name for p in AGENTS_DIR.glob("*.md")}
        mapped_names = set(REVIEWER_AGENTS) | set(NON_REVIEWER_MODELS)
        uncategorized = all_agent_names - mapped_names
        assert not uncategorized, (
            f"Agent files have no expected-model entry: {sorted(uncategorized)}. "
            f"Add each to REVIEWER_AGENTS (sonnet reviewer) or NON_REVIEWER_MODELS "
            f"(with its pinned model) in this test file."
        )

    def test_non_reviewer_roster_and_model_map_are_in_sync(self):
        """NON_REVIEWER_MODELS and NON_REVIEWER_AGENTS must name the same set of agents.

        Both lists serve as the reference for which agents are non-reviewers.
        Drift between them causes test_model_pinned_to_expected_value to use a
        stale model map while test_no_uncategorized_agents uses a stale roster —
        the two lists diverging silently is the failure mode this test closes.
        """
        assert set(NON_REVIEWER_MODELS) == set(NON_REVIEWER_AGENTS), (
            f"NON_REVIEWER_MODELS keys {sorted(NON_REVIEWER_MODELS)} differ from "
            f"NON_REVIEWER_AGENTS {sorted(NON_REVIEWER_AGENTS)}. Keep them in sync."
        )
