"""Static contract tests for skill SKILL.md description fields.

Skills in this repo fall into three invocation categories:
- Model-invokable (default): the Claude Code harness reads the frontmatter
  `description` at session start and auto-loads the skill body when the
  description matches context. These skills MUST carry TRIGGER when: /
  DO NOT TRIGGER when: discipline so the harness fires at the right time.
- User-only commands (disable-model-invocation: true): descriptions are
  suppressed from the always-loaded listing budget; the user invokes directly
  via /skill-name. These skills must be registered in COMMAND_SKILLS.
- Model-only reference (user-invocable: false): invoked by model auto-trigger
  only; same TRIGGER discipline requirement as default.

Three invocation paths exist for skills — user slash, model auto-load from
description, and parent-skill glob dispatch via Skill tool. The third path
(F-05 / F-07) is the designed invocation mechanism for project-layer add-on
skills (code-review-*, plan-review-*, test-conventions-*) and is the reason a
skill can legitimately carry both user-invocable: false and
disable-model-invocation: true simultaneously — it is NOT "uninvocable".
See code-review/REFERENCES.md F-05 and F-07 for the empirical record.

Contracts enforced:
(a) Every model-invokable skill has TRIGGER when: and DO NOT TRIGGER when:.
(b) Every registered command skill carries disable-model-invocation: true.

Run with: pytest claude/.claude/
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
# Plugins live two levels above the .claude/ dir: <repo>/plugins/<name>/skills/<skill>/SKILL.md
_PLUGINS_DIR = SKILLS_DIR.parent.parent.parent / "plugins"


def _skill_file(skill_name: str) -> Path:
    """Locate a SKILL.md by name — stowed skills first, then plugin skills."""
    candidate = SKILLS_DIR / skill_name / "SKILL.md"
    if candidate.exists():
        return candidate
    if _PLUGINS_DIR.exists():
        for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
            plugin_candidate = plugin_dir / "skills" / skill_name / "SKILL.md"
            if plugin_candidate.exists():
                return plugin_candidate
    return candidate  # let read_text() surface the FileNotFoundError


def _skill_description(skill_name: str) -> str:
    """Return the raw frontmatter text of a skill's SKILL.md.

    Returns only the text between the opening and closing --- delimiters.
    If frontmatter delimiters are absent the skill is structurally broken;
    returning empty string causes downstream assertions to fail cleanly.
    """
    skill_file = _skill_file(skill_name)
    content = skill_file.read_text()
    if not content.startswith("---"):
        return ""
    # Frontmatter lives between the opening --- and the closing ---.
    closing = content.index("---", 3)
    return content[3:closing]


def _specialist_skills() -> list[str]:
    """Discover non-user-invocable skills from the stowed skills directory and plugins.

    Any skill with user-invocable: false in its frontmatter is expected to
    fire via description-based auto-trigger and must carry TRIGGER when: /
    DO NOT TRIGGER when: blocks. New skills automatically get structural
    coverage without a code change here.
    """
    skills = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        frontmatter = _skill_description(skill_dir.name)
        if frontmatter and "user-invocable: false" in frontmatter:
            skills.append(skill_dir.name)
    if _PLUGINS_DIR.exists():
        for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
            plugin_skills_dir = plugin_dir / "skills"
            if not plugin_skills_dir.is_dir():
                continue
            for skill_dir in sorted(plugin_skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                if not (skill_dir / "SKILL.md").exists():
                    continue
                frontmatter = _skill_description(skill_dir.name)
                if frontmatter and "user-invocable: false" in frontmatter and skill_dir.name not in skills:
                    skills.append(skill_dir.name)
    return skills


def _model_invokable_skills() -> list[str]:
    """Discover all skills the model can auto-load — i.e., NOT disable-model-invocation: true.

    Any skill without disable-model-invocation: true is presented to the model
    in the always-loaded skill listing and must carry TRIGGER when: / DO NOT
    TRIGGER when: discipline so the harness fires it at the right time.
    """
    skills = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        frontmatter = _skill_description(skill_dir.name)
        if frontmatter and "disable-model-invocation: true" not in frontmatter:
            skills.append(skill_dir.name)
    if _PLUGINS_DIR.exists():
        for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
            plugin_skills_dir = plugin_dir / "skills"
            if not plugin_skills_dir.is_dir():
                continue
            for skill_dir in sorted(plugin_skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                if not (skill_dir / "SKILL.md").exists():
                    continue
                frontmatter = _skill_description(skill_dir.name)
                if (
                    frontmatter
                    and "disable-model-invocation: true" not in frontmatter
                    and skill_dir.name not in skills
                ):
                    skills.append(skill_dir.name)
    return skills


# Explicit registry of user-only command skills. Each must carry
# disable-model-invocation: true in its frontmatter so the description is
# suppressed from the model's always-loaded listing budget. Add to this list
# whenever a new command-style skill (user-invoked slash workflow, no model
# auto-discovery needed) is created under skills/.
COMMAND_SKILLS = ["handoff", "read-docx-comments"]


class TestSpecialistSkillTriggerContracts:
    """TRIGGER / DO NOT TRIGGER contract tests for non-user-invocable skills.

    Structural tests (trigger/do-not-trigger block presence) auto-discover
    every skill with user-invocable: false in its frontmatter. New specialist
    skills get structural coverage automatically.

    Semantic tests (designated surface, adjacent-skill exclusion) are
    explicitly registered per-skill — they encode the routing contract for
    skills whose surfaces overlap. Add an entry to each parametrize list when
    a new specialist skill is introduced.
    """

    SPECIALIST_SKILLS = _specialist_skills()

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

    @pytest.mark.parametrize("skill_name", COMMAND_SKILLS)
    def test_command_skill_has_disable_model_invocation(self, skill_name):
        """User-only command skills must carry disable-model-invocation: true."""
        desc = _skill_description(skill_name)
        assert "disable-model-invocation: true" in desc, (
            f"{skill_name}/SKILL.md is registered as a command skill but is missing "
            f"disable-model-invocation: true; add it or remove from COMMAND_SKILLS"
        )


class TestModelInvokableSkillTriggerContracts:
    """TRIGGER / DO NOT TRIGGER contract tests for all model-invokable skills.

    Widens the specialist-skill contracts above to every skill the model can
    auto-load — i.e., any skill whose frontmatter does NOT contain
    disable-model-invocation: true. This includes both user-invocable: false
    reference skills and default-mode workflow skills (plan-it, plan-review,
    code-review, ready-for-review, respond-pr, etc.).

    Without this coverage, a maintainer removing TRIGGER discipline from a
    first-class workflow skill goes undetected until the harness stops firing
    the skill at the right time.
    """

    MODEL_INVOKABLE_SKILLS = _model_invokable_skills()

    @pytest.mark.parametrize("skill_name", MODEL_INVOKABLE_SKILLS)
    def test_model_invokable_skill_has_trigger_block(self, skill_name):
        """Description must contain a TRIGGER when: block."""
        desc = _skill_description(skill_name)
        assert "TRIGGER when:" in desc, (
            f"{skill_name}/SKILL.md description is missing 'TRIGGER when:' block; "
            f"add it or set disable-model-invocation: true if this skill should not auto-load"
        )

    @pytest.mark.parametrize("skill_name", MODEL_INVOKABLE_SKILLS)
    def test_model_invokable_skill_has_do_not_trigger_block(self, skill_name):
        """Description must contain a DO NOT TRIGGER when: block."""
        desc = _skill_description(skill_name)
        assert "DO NOT TRIGGER when:" in desc, (
            f"{skill_name}/SKILL.md description is missing 'DO NOT TRIGGER when:' block; "
            f"add it or set disable-model-invocation: true if this skill should not auto-load"
        )
