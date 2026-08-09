"""Structural invariants for claude/.claude/agents/*.md.

These tests enforce conventions that are stated as prose rules in
code-review/SKILL.md and agent documentation — specifically the file-based
output canary requirement. They catch the failure mode where a new reviewer
agent is added to the roster without the required frontmatter or output section.
"""
from __future__ import annotations

import re
import subprocess

import pytest
import yaml
from helpers import CLAUDE_DIR, HOOKS_DIR
from validate_skill_structure import parse_frontmatter

AGENTS_DIR = CLAUDE_DIR / "agents"

# The stack-specialist reviewer roster — the personas dispatched by /plan-review
# and /code-review off their Item-ownership tables. This list is ALSO the ground
# truth test_doc_counts.py locks the "N specialist personas" doc claims to, so it
# must stay equal to the count those docs assert. When a new *stack specialist* is
# added, add it here. A reviewer that carries the canary but is not a counted
# stack specialist (e.g. a branch-gate reviewer spawned by /ready-for-review) goes
# in CANARY_AGENTS below instead, so the doc counts do not drift.
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

# Every agent that must carry the file-based output canary (Write tool +
# "### File-based output" section + the byte-identical block). Superset of
# REVIEWER_AGENTS: the stack specialists PLUS other reviewers that write
# findings_path output but are not part of the counted stack-specialist roster.
# skill-fidelity-reviewer is spawned by /ready-for-review (not the two
# dispatchers) and is not a domain specialist, so it enforces the canary here
# without inflating the specialist-roster doc counts. Kept sorted so
# CANARY_AGENTS[0] is a stable canonical anchor for the byte-equivalence test.
CANARY_AGENTS = sorted(REVIEWER_AGENTS + ["skill-fidelity-reviewer.md"])

# Agents that exist in the directory but are not code-review dispatched
# reviewers — they do not receive findings_path and do not need the canary.
# When a new non-reviewer agent is added, add it here; the
# test_no_uncategorized_agents test will fail until it is categorized.
NON_REVIEWER_AGENTS = [
    "code-writer.md",   # implementer; self-reviews its own output, not a dispatcher-spawned reviewer
    "Explore.md",       # same-named override of the harness built-in; read-only search, not a reviewer
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
    "code-writer.md": "sonnet",  # implementer
    "Explore.md": "sonnet",      # same-named built-in override
}


class TestReviewerAgentRoster:
    def test_all_reviewer_agents_exist(self):
        """Every agent named in the canary roster must have a corresponding file."""
        for name in CANARY_AGENTS:
            path = AGENTS_DIR / name
            assert path.exists(), (
                f"Reviewer agent file missing: {path}. "
                "If the file was renamed or removed, update REVIEWER_AGENTS / "
                "CANARY_AGENTS in this test."
            )

    def test_all_reviewer_agents_have_write_tool(self):
        """Every reviewer agent must declare Write in its tools: frontmatter line.

        The file-based output canary requires the Write tool. Passing findings_path
        to an agent without Write causes a permissions error and full-inline fallback,
        defeating the context-saving mechanism. This is the structural half of the
        ordering rule stated in code-review/SKILL.md: add Write to the agent BEFORE
        updating the dispatcher to pass findings_path.
        """
        for name in CANARY_AGENTS:
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
        for name in CANARY_AGENTS:
            path = AGENTS_DIR / name
            content = path.read_text()
            assert "### File-based output" in content, (
                f"{name}: '### File-based output' section missing. "
                "All reviewer agents must contain this section for the file-based output canary."
            )

    def test_reviewer_agents_list_is_sorted(self):
        """REVIEWER_AGENTS must be sorted alphabetically.

        CANARY_AGENTS is sorted by construction (built via sorted()), so
        CANARY_AGENTS[0] is a stable canonical anchor for
        TestFileBasedOutputBlockConsistency regardless of REVIEWER_AGENTS order.
        Keeping REVIEWER_AGENTS itself sorted preserves readable, stable diffs and
        matches the sorted membership CANARY_AGENTS derives from.
        """
        assert sorted(REVIEWER_AGENTS) == REVIEWER_AGENTS, (
            "REVIEWER_AGENTS is not sorted alphabetically. Keep it sorted for "
            "stable diffs and to match the sorted CANARY_AGENTS membership."
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
        known = set(CANARY_AGENTS) | set(NON_REVIEWER_AGENTS)
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

        The block text is identical across all canary agents modulo the
        '# <agent-name>' H1 line (which is normalized before comparison). Any
        divergence means one agent has drifted from the shared protocol — the agent
        file must be updated to match the canonical form.

        The canonical reference is the first entry in CANARY_AGENTS (sorted order).
        When the canonical block changes, all other agents must be updated in the
        same commit.
        """
        blocks = {}
        for name in CANARY_AGENTS:
            path = AGENTS_DIR / name
            blocks[name] = self._extract_file_based_output_block(path)

        canonical_name = CANARY_AGENTS[0]
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

        Reviewers (CANARY_AGENTS) → 'sonnet' per CLAUDE.md.
        Non-reviewers → model declared in NON_REVIEWER_MODELS.
        """
        expected = {a: "sonnet" for a in CANARY_AGENTS} | NON_REVIEWER_MODELS
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
        mapped_names = set(CANARY_AGENTS) | set(NON_REVIEWER_MODELS)
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


# Members of _LIB_NO_GATE_RELEASE_AGENTS that are harness built-ins with no
# agents/*.md file in this repo. `Plan` is understood to carry the Skill tool,
# so its inclusion rests on mandate (dispatched read-only) rather than on tool
# absence, and the frontmatter assertions below cannot apply to it. Enumerated
# as a closed set so a typo'd or renamed roster entry fails the test rather
# than silently skipping it.
#
# `Explore` moved out of this set once `agents/Explore.md` shipped as a
# same-named override of the harness built-in: its `tools:` frontmatter is now
# on disk and checkable the same way as any other file-backed no-gate-release
# member (no `Skill`, no `Task`), so it no longer needs the mandate exemption
# below. If `Explore.md` is ever deleted, `Explore` goes back in this set.
#
# Two platform assumptions ride on the remaining exemption, neither checkable
# from this repo — record them here so they are searchable rather than silent:
#   - What tools the harness grants `Plan`. There is no registry to read, so
#     the mandate grounding is what the deny actually rests on.
#   - That a subagent cannot itself invoke Task. If that ever changes, `Plan`
#     could delegate a marker write to a full-tool-set agent and release a
#     gate, which is exactly what the Task assertion below closes for the
#     file-backed members. Re-derive this exemption if it does.
HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS = {"Plan"}


def _no_gate_release_agents() -> list[str]:
    """Read _LIB_NO_GATE_RELEASE_AGENTS from _lib.sh — the shipping source of truth."""
    lib = HOOKS_DIR / "_lib.sh"
    result = subprocess.run(
        ["bash", "-c", f". {lib}; _lib_no_gate_release_agents"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


class TestNoGateReleaseRosterSync:
    """The no-gate-release boundary is grounded in the agent roster's tool lists.

    enforce-marker-script-shape.sh denies `marker.sh write` / `activate` to
    these agents on the stated grounds that none of them could have run the
    review a gate demands, so any marker they write asserts a review that
    could not have happened. For the file-backed members that grounding is
    tool absence (no `Skill`, and no `Task` to delegate it with); for the
    harness built-ins it is mandate, which no frontmatter records. Only the
    first kind is checkable here, so it is asserted rather than verified by
    hand at review time, and the second is pinned as a closed exemption set.
    """

    def test_roster_members_have_agent_files_or_are_named_builtins(self):
        for name in _no_gate_release_agents():
            if name in HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS:
                continue
            assert (AGENTS_DIR / f"{name}.md").is_file(), (
                f"_LIB_NO_GATE_RELEASE_AGENTS names '{name}', but "
                f"agents/{name}.md does not exist. Either the roster entry is "
                f"misspelled, or it is a harness built-in that must be added to "
                f"HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS in this file with a "
                f"note on why the tools: assertion cannot apply to it."
            )

    @staticmethod
    def _declared_tools(name: str) -> set[str]:
        """Tools an agent file grants, via the strict-YAML frontmatter parser.

        Routed through parse_frontmatter rather than a `^tools:` regex so that
        YAML list form (`tools:\\n  - Skill`) and multi-line values are read
        correctly — a regex silently reports "Skill absent" for both, which
        would turn this assertion into a false pass on exactly the frontmatter
        shape it exists to catch.
        """
        fm = parse_frontmatter(AGENTS_DIR / f"{name}.md")
        declared = fm.get("tools")
        assert declared, f"{name}.md: no 'tools:' field found in frontmatter"
        if isinstance(declared, str):
            return {t.strip() for t in declared.split(",")}
        return {str(t).strip() for t in declared}

    def test_roster_members_do_not_carry_the_skill_tool(self):
        for name in _no_gate_release_agents():
            if name in HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS:
                continue
            declared = self._declared_tools(name)
            assert "Skill" not in declared, (
                f"{name}.md declares the Skill tool, but it is listed in "
                f"_LIB_NO_GATE_RELEASE_AGENTS — whose deny is justified in "
                f"enforce-marker-script-shape.sh by these agents being unable to "
                f"run a review skill at all. Granting Skill here makes that "
                f"justification false. Either drop Skill from this agent, or "
                f"remove it from the roster and re-derive the boundary."
            )

    def test_roster_members_do_not_carry_the_task_tool(self):
        """Task absence is what closes the delegate-to-a-full-tool-set escape.

        Denying these agents a direct marker write accomplishes nothing if they
        can dispatch a subagent that is allowed to make one — `general-purpose`
        is deliberately off the roster precisely because it can genuinely run a
        review. The boundary therefore rests on no roster member being able to
        dispatch at all, which is true today only by inspection of the same
        frontmatter the Skill assertion reads.
        """
        for name in _no_gate_release_agents():
            if name in HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS:
                continue
            declared = self._declared_tools(name)
            assert "Task" not in declared, (
                f"{name}.md declares the Task tool, but it is listed in "
                f"_LIB_NO_GATE_RELEASE_AGENTS. A member that can dispatch a "
                f"subagent can have that subagent write the marker instead, "
                f"which reopens the gate-release path this roster closes. "
                f"Either drop Task from this agent, or remove it from the "
                f"roster and re-derive the boundary."
            )

    def test_explore_tools_are_exactly_read_grep_glob(self):
        """Pins Explore.md's own no-Write/Edit/Bash design guarantee.

        The Skill/Task-absence checks above are roster-wide and can't assert
        this: code-writer.md is also on the roster and legitimately carries
        Write/Edit/Bash. Explore's read-only guarantee needs its own
        assertion, or a future edit adding Bash back passes every other test
        here while silently turning a read-only search agent into one that
        can mutate the tree it is dispatched read-only against.
        """
        assert self._declared_tools("Explore") == {"Read", "Grep", "Glob"}, (
            "Explore.md's tools: line changed. It must stay exactly "
            "Read, Grep, Glob — no Write, Edit, or Bash — per the agent's "
            "own stated design (see Explore.md's body)."
        )

    def test_harness_builtin_exemptions_have_no_agent_file(self):
        """The exemption list must stay an exemption, not a bypass.

        If an agent file appears for one of these names, the frontmatter
        assertion becomes applicable and the exemption should be dropped."""
        for name in HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS:
            assert not (AGENTS_DIR / f"{name}.md").is_file(), (
                f"agents/{name}.md now exists, so {name} is no longer a harness "
                f"built-in exemption. Remove it from "
                f"HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS so its tools: line is checked."
            )

    def test_exemptions_are_all_roster_members(self):
        """A stale exemption for an agent no longer on the roster is dead weight."""
        roster = set(_no_gate_release_agents())
        stale = HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS - roster
        assert not stale, (
            f"HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS names {sorted(stale)}, which "
            f"is no longer in _LIB_NO_GATE_RELEASE_AGENTS. Drop the stale entry."
        )

    def test_exemption_set_is_pinned(self):
        """Tripwire: the exemption set must not grow silently.

        Every other test in this class validates the exemption set against
        itself — "has no agent file" is true by construction for any name that
        does not have one yet, so adding a misspelled or fictitious agent to
        BOTH _LIB_NO_GATE_RELEASE_AGENTS and the exemption set in one diff
        would skip the frontmatter check with every test still green. There is
        no independent registry of harness built-ins in this repo to check
        against, so this pins the contents explicitly instead: growing the
        exemption set requires editing this assertion, which puts a reviewer in
        front of the claim that the new name really is a Skill-carrying harness
        built-in gated by mandate rather than by tool absence.
        """
        assert {"Plan"} == HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS, (
            "The harness-built-in exemption set changed. Each member is exempt "
            "from the no-Skill frontmatter assertion, so adding one removes real "
            "coverage. Confirm the new name is genuinely a harness built-in with "
            "no agents/*.md file, then update this assertion deliberately."
        )
