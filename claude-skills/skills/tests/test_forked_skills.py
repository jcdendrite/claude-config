"""Skills carrying context: fork."""
from __future__ import annotations

import pytest
from _skill_md_helpers import (
    _all_skill_md_files,
    _skill_body,
    _skill_file,
)

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from validate_skill_structure import parse_frontmatter

# Skills carrying context: fork. The selection criterion is working set (how
# much bulk content a skill's body pulls into the parent conversation), not
# body size — see docs/skills.md's Skill architecture notes section. Extending
# this set requires re-running that criterion against the candidate skill, not
# just adding its name here.
_FORKED_SKILLS = frozenset({"transcript-narrative", "error-mode-analysis"})


class TestForkedSkillRoster:
    """`context: fork` is reserved for a skill whose body instructs reading
    bulk working-set content — raw transcripts, full PR-comment payloads —
    directly into the parent conversation, with no residual parent work after
    the artifact is written. A skill that already keeps its working set out
    of the parent by dispatching subagents (code-review, plan-review) gains
    only its body from forking and loses capability — the exact mistake this
    roster test exists to catch before it lands.
    """

    def test_context_fork_roster_matches_working_set_criterion(self):
        forked = {
            path.parent.name
            for path in _all_skill_md_files()
            if parse_frontmatter(path).get("context") == "fork"
        }
        assert forked == _FORKED_SKILLS, (
            "context: fork roster changed. Forking is a win only for a skill "
            "whose body directs bulk working-set content into the parent "
            "conversation with no residual parent work after the artifact is "
            "written — not for a skill that is merely long. A skill that "
            "already dispatches subagents to keep its working set out of the "
            "parent (code-review, plan-review) gains only its body from "
            "forking and loses capability. Read docs/skills.md's Skill "
            "architecture notes section and re-run its criterion against the "
            f"candidate skill before adding or removing a name here. Found: "
            f"{sorted(forked)}, expected: {sorted(_FORKED_SKILLS)}"
        )

    @pytest.mark.parametrize("skill_name", sorted(_FORKED_SKILLS))
    def test_forked_skill_declares_background_false_literal(self, skill_name):
        frontmatter = parse_frontmatter(_skill_file(skill_name))
        assert frontmatter.get("background") is False, (
            f"{skill_name}: context: fork must pair with a literal "
            f"background: false — omitting it defaults background to true, "
            f"a background fork with a narrowed tool set and no /rewind "
            f"checkpoint coverage."
        )

    @pytest.mark.parametrize("skill_name", sorted(_FORKED_SKILLS))
    def test_forked_skill_carries_no_agent_key(self, skill_name):
        frontmatter = parse_frontmatter(_skill_file(skill_name))
        assert "agent" not in frontmatter, (
            f"{skill_name}: must carry no agent: key, keeping it on the "
            f"default general-purpose agent type — pinning a review-only "
            f"agent type here would categorically forbid marker writes for "
            f"any future forked skill."
        )


_NEGATIVE_GATE_RELEASE_INSTRUCTION = (
    "This skill never invokes `marker.sh` and never invokes a review skill, "
    "directly or by dispatching a subagent to do either on its behalf."
)

# Review-only skill names a forked skill's body must never invoke, matching
# the personas _LIB_NO_GATE_RELEASE_AGENTS protects against releasing a gate.
_REVIEW_SKILL_NAMES = (
    "code-review",
    "plan-review",
    "skill-review",
    "ready-for-review",
    "agent-review",
    "claude-hook-review",
)


class TestForkedSkillsDeclareNoGateReleaseAuthority:
    """A forked skill runs unsupervised, in the parent's process identity,
    with the full tool set. Both forked skills ingest content the session
    owner did not write — raw transcripts, GitHub PR comments — so both must
    state plainly that they never touch a review gate.
    """

    @pytest.mark.parametrize("skill_name", sorted(_FORKED_SKILLS))
    def test_body_states_it_never_releases_a_gate(self, skill_name):
        body = _skill_body(skill_name)
        assert _NEGATIVE_GATE_RELEASE_INSTRUCTION in body, (
            f"{skill_name}: forked skill body must state plainly that it "
            f"never invokes marker.sh and never invokes a review skill."
        )

    @pytest.mark.parametrize("skill_name", sorted(_FORKED_SKILLS))
    def test_body_does_not_actually_invoke_marker_or_a_review_skill(self, skill_name):
        body = _skill_body(skill_name)
        assert "marker.sh write" not in body
        assert "marker.sh activate" not in body
        for review_skill in _REVIEW_SKILL_NAMES:
            assert f'skill="{review_skill}"' not in body
            assert f"Invoke the `{review_skill}`" not in body
            assert f"run the `{review_skill}`" not in body


class TestForkedSkillsCarryReturnAndGuardInstructions:
    """A partial revert of either mechanism must fail loudly here rather than
    silently reintroduce the parent-context cost forking exists to avoid.
    """

    @pytest.mark.parametrize("skill_name", sorted(_FORKED_SKILLS))
    def test_body_defaults_its_output_location_to_mktemp(self, skill_name):
        body = _skill_body(skill_name)
        assert "mktemp -d" in body, (
            f"{skill_name}: must default its output location to a mktemp -d "
            f"directory when no output path/directory argument is given."
        )

    @pytest.mark.parametrize("skill_name", sorted(_FORKED_SKILLS))
    def test_body_states_the_bash_availability_guard(self, skill_name):
        body = _skill_body(skill_name)
        assert "v2.1.218" in body, (
            f"{skill_name}: must stop and name the Claude Code v2.1.218 "
            f"background: false floor when Bash is unavailable."
        )
