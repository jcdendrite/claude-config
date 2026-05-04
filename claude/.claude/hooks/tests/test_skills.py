"""Static contract tests for specialist skill SKILL.md description fields.

Each specialist skill in this repo is auto-triggerable: the Claude Code harness
reads the frontmatter `description` field at session start and loads the skill
body when the description matches the current context. The TRIGGER when: /
DO NOT TRIGGER when: blocks inside that description are the routing contracts.

These tests verify the contracts without invoking Claude Code or the API:
- Both TRIGGER when: and DO NOT TRIGGER when: blocks are present in the description.
- The TRIGGER block covers the file surface or intent the skill owns.
- The DO NOT TRIGGER block names the adjacent skill whose surface overlaps.

A silent regression (dropping a surface glob or an adjacent-skill exclusion
from the description) means the skill either stops firing on its surface or
dual-fires with a neighbor. These tests catch that at CI time.

Run with: pytest claude/.claude/hooks/tests/test_skills.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


def _skill_description(skill_name: str) -> str:
    """Return the raw frontmatter text of a skill's SKILL.md.

    Returns only the text between the opening and closing --- delimiters.
    If frontmatter delimiters are absent the skill is structurally broken;
    returning empty string causes downstream assertions to fail cleanly.
    """
    skill_file = SKILLS_DIR / skill_name / "SKILL.md"
    content = skill_file.read_text()
    if not content.startswith("---"):
        return ""
    # Frontmatter lives between the opening --- and the closing ---.
    closing = content.index("---", 3)
    return content[3:closing]


class TestSpecialistSkillTriggerContracts:
    """TRIGGER / DO NOT TRIGGER contract tests for the five specialist skills.

    Specialist skills are non-user-invocable; they fire via description-based
    auto-trigger. The description is the only thing the harness sees before
    deciding to load the body, so these contracts are load-bearing.
    """

    SPECIALIST_SKILLS = [
        "claude-hook-review",
        "skill-review",
        "review-permissions",
        "test-conventions",
        "test-evaluation",
    ]

    @pytest.mark.parametrize("skill_name", SPECIALIST_SKILLS)
    def test_description_has_trigger_block(self, skill_name):
        """Description must contain a TRIGGER when: block."""
        desc = _skill_description(skill_name)
        assert "TRIGGER when:" in desc, (
            f"{skill_name}/SKILL.md description is missing 'TRIGGER when:' block"
        )

    @pytest.mark.parametrize("skill_name", SPECIALIST_SKILLS)
    def test_description_has_do_not_trigger_block(self, skill_name):
        """Description must contain a DO NOT TRIGGER when: block."""
        desc = _skill_description(skill_name)
        assert "DO NOT TRIGGER when:" in desc, (
            f"{skill_name}/SKILL.md description is missing 'DO NOT TRIGGER when:' block"
        )

    @pytest.mark.parametrize("skill_name,expected_surface", [
        ("claude-hook-review", ".claude/hooks/"),
        ("skill-review", ".claude/skills/"),
        ("review-permissions", "permissions.allow"),
        ("test-conventions", "new"),
        ("test-evaluation", "existing"),
    ])
    def test_trigger_covers_designated_surface(self, skill_name, expected_surface):
        """TRIGGER block must reference the file surface or intent the skill owns."""
        desc = _skill_description(skill_name)
        trigger_start = desc.index("TRIGGER when:")
        dnt_start = desc.index("DO NOT TRIGGER when:")
        trigger_section = desc[trigger_start:dnt_start]
        assert expected_surface in trigger_section, (
            f"{skill_name}/SKILL.md TRIGGER section does not mention '{expected_surface}'; "
            f"the harness won't fire this skill on its designated surface"
        )

    @pytest.mark.parametrize("skill_name,adjacent_skill", [
        ("claude-hook-review", "review-permissions"),
        ("skill-review", "skill-creator"),
        ("review-permissions", "claude-hook-review"),
        ("test-conventions", "test-evaluation"),
        ("test-evaluation", "test-conventions"),
    ])
    def test_do_not_trigger_names_adjacent_skill(self, skill_name, adjacent_skill):
        """DO NOT TRIGGER block must name the adjacent skill with overlapping surface."""
        desc = _skill_description(skill_name)
        dnt_start = desc.index("DO NOT TRIGGER when:")
        dnt_section = desc[dnt_start:]
        assert adjacent_skill in dnt_section, (
            f"{skill_name}/SKILL.md DO NOT TRIGGER section does not name '{adjacent_skill}'; "
            f"the two skills may dual-fire on overlapping surfaces"
        )
