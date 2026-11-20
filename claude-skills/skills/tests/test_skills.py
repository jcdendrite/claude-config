"""Static contract tests for skill SKILL.md description fields.

Skills in this repo fall into four invocation categories:
- Model-invokable (default): the Claude Code harness reads the frontmatter
  `description` at session start and auto-loads the skill body when the
  description matches context. These skills MUST carry TRIGGER when: /
  DO NOT TRIGGER when: discipline so the harness fires at the right time.
- name-only (skillOverrides: name-only in settings.json): description excluded
  from the always-loaded listing budget; the model can still invoke by exact
  name when referenced in conversation. No TRIGGER discipline required (no
  description to match on). Also slash-invocable by the user. Controlled via
  settings.json, not frontmatter — must NOT carry disable-model-invocation: true.
  Two bundled Claude Code skills (loop, simplify) use this mode with no repo
  SKILL.md — see BUILTIN_NAME_ONLY_SKILLS.
- User-only commands (disable-model-invocation: true): description excluded from
  the listing budget; only the user can invoke via /skill-name. Must be
  registered in COMMAND_SKILLS.
- Model-only reference (user-invocable: false): invoked by model auto-trigger
  only; same TRIGGER discipline requirement as default.

Two invocation paths exist for skills — user slash and model auto-load from
description. Project-layer add-on skills (code-review-*, plan-review-*,
plan-it-*, test-conventions-*) are a third surface but not a third invocation
path: parent skills glob for the layer file and read it via the Read tool,
incorporating the layer's content into the parent's reasoning pass. The layer
does not need to be invocable at all — it is a file that is read, not a skill
that is dispatched. See docs/design-decisions.md §8 and
docs/skills.md "Project-specific layers" for rationale.

Contracts enforced:
(a) Every model-invokable skill has TRIGGER when: and DO NOT TRIGGER when:.
(b) Every registered command skill carries disable-model-invocation: true.
(c) Every name-only skill does NOT carry disable-model-invocation: true.

Run with: pytest claude/.claude/
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import _skill_md_helpers  # monkeypatch target for the helper module's own SKILLS_DIR and _PLUGINS_DIR
import pytest
import yaml

# pyproject.toml's pythonpath also puts claude/.claude/scripts on the import
# path, where the production config-keys.psv parser lives.
from _config import schema as config_schema
from _skill_md_helpers import (
    _IMPOSSIBLE_DATE_SKILL,
    _MALFORMED_YAML_SKILL,
    _PLUGINS_DIR,
    _SCALAR_ONLY_SKILL,
    _UNCLOSED_BLOCK_SKILL,
    _all_skill_md_paths,
    _fence_excluded_by_marker,
    _fenced_code_blocks,
    _skill_file,
    _write_skill_md_fixture,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import REPO_ROOT, SKILLS_DIR

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from run_skill_evals import extract_governing_rule
from validate_skill_structure import (
    SKILL_LISTING_BUDGET_CHARS,
    FrontmatterError,
    corpus_budget_violations,
    parse_frontmatter,
    validate,
)


def _discovery_frontmatter(skill_file: Path) -> dict:
    """Frontmatter for collection-time discovery: {} when it cannot be read as a mapping.

    Discovery runs at import, so a raise would abort collection of the whole module.
    A skill read as {} stays in the model-invokable lists, whose parametrized tests fail under its name.
    """
    try:
        frontmatter = parse_frontmatter(skill_file)
    except (yaml.YAMLError, ValueError):
        return {}
    return frontmatter


def _disables_model_invocation(frontmatter: dict) -> bool:
    """Only a YAML boolean true counts, so a quoted or misspelled flag keeps the skill under the TRIGGER sweeps."""
    return frontmatter.get("disable-model-invocation") is True


def _listing_text(frontmatter: dict) -> str:
    """The `description` followed by `when_to_use`, the two keys the harness lists."""
    description = frontmatter.get("description", "") or ""
    when_to_use = frontmatter.get("when_to_use", "") or ""
    return f"{description}\n{when_to_use}" if when_to_use else description


def _skill_description(skill_name: str) -> str:
    """Return the listing text of a skill: its parsed `description` followed by `when_to_use`.

    A skill with no frontmatter block returns an empty string, which fails
    downstream assertions cleanly.
    """
    return _listing_text(parse_frontmatter(_skill_file(skill_name)))


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
        if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
            continue
        frontmatter = _discovery_frontmatter(_skill_file(skill_dir.name))
        if frontmatter.get("user-invocable") is False:
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
                if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
                    continue
                frontmatter = _discovery_frontmatter(_skill_file(skill_dir.name))
                if frontmatter.get("user-invocable") is False and skill_dir.name not in skills:
                    skills.append(skill_dir.name)
    return skills


def _settings_skill_overrides() -> dict[str, str]:
    """Read skillOverrides from the stowed settings.json.

    Returns the override map keyed by skill name. Skills absent from the map
    default to "on" (fully model-invokable with description in budget).
    """
    settings_path = REPO_ROOT / "claude/.claude/settings.json"
    settings = json.loads(settings_path.read_text())
    return settings.get("skillOverrides", {})


def _name_only_skills() -> list[str]:
    """Skills with skillOverrides: name-only in settings.json.

    These skills are model-invokable by exact name but their descriptions are
    excluded from the always-loaded listing budget — the harness shows only the
    skill name to the model, so auto-triggering from description matching is
    disabled. Also slash-invocable by the user. No TRIGGER discipline required.
    """
    overrides = _settings_skill_overrides()
    return [name for name, state in overrides.items() if state == "name-only"]


def _model_invokable_skills() -> list[str]:
    """Discover all skills whose descriptions auto-load into the model's context budget.

    A skill auto-loads when its frontmatter does NOT contain
    disable-model-invocation: true AND it is not listed as name-only in
    skillOverrides. These skills must carry TRIGGER when: / DO NOT TRIGGER
    when: discipline so the harness fires them at the right time.

    name-only skills are excluded: their descriptions are not in the budget, so
    description-based auto-triggering is disabled and no TRIGGER blocks apply.
    """
    budget_excluded = set(_name_only_skills())
    skills = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        if skill_dir.name in budget_excluded:
            continue
        if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
            continue
        frontmatter = _discovery_frontmatter(_skill_file(skill_dir.name))
        if not _disables_model_invocation(frontmatter):
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
                if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
                    continue
                qualified_name = f"{plugin_dir.name}:{skill_dir.name}"
                if skill_dir.name in budget_excluded or qualified_name in budget_excluded:
                    continue
                # Description fetch uses the bare name — correct for single-plugin repos.
                # If two plugins ever ship a skill with the same bare name, the first-alphabetically
                # found plugin's frontmatter would be used. Revisit to pass qualified_name when
                # a second same-named plugin skill is added.
                frontmatter = _discovery_frontmatter(_skill_file(skill_dir.name))
                if not _disables_model_invocation(frontmatter) and skill_dir.name not in skills:
                    skills.append(skill_dir.name)
    return skills


# Explicit registry of user-only command skills that use the frontmatter
# disable-model-invocation: true flag. Plugin skills that need the listing-
# budget exemption register here instead of via skillOverrides, which does
# not apply to plugin skills — see docs/skills.md for the full mechanism and
# the /skill-review exception.
COMMAND_SKILLS: list[str] = ["issue-triage"]

# Skills that are user-invocable: false (model/hook-dispatched) but deliberately
# carry no TRIGGER blocks because they are always dispatched by name — never by
# description auto-trigger. These are exempt from TRIGGER-block structural contracts.
#
# Maintenance protocol: when adding a name here, also verify it has NO entries
# in the test_trigger_covers_designated_surface and
# test_do_not_trigger_names_adjacent_skill parametrize lists — those hardcoded
# entries assert TRIGGER block content and must be removed for the exempted skill.
_NAME_DISPATCHED_NO_TRIGGER: frozenset[str] = frozenset({"skill-review"})

# Bundled Claude Code skills set to skillOverrides: name-only. These skills ship
# in the Claude Code binary and have no repo SKILL.md by design. Exempted from the
# three repo-SKILL.md-dependent contracts in TestNameOnlySkillContracts.
# Note: the test cannot verify that a name here is a genuine bundled skill — a
# typo'd bundled name in settings is silently ignored by Claude Code rather than
# erroring; the set-equality test test_builtin_name_only_allowlist_matches_settings
# below provides the drift guard.
#
# Invariant: built-ins never enter _model_invokable_skills() — that function only
# iterates repo SKILLS_DIR and plugin directories, so loop/simplify (having no
# SKILLS_DIR entry) are never passed to _skill_description() from that path.
# budget_excluded membership for them is a no-op but harmless. This is what
# prevents a FileNotFoundError cascade through the model-invokable test classes.
BUILTIN_NAME_ONLY_SKILLS: set[str] = {"loop", "simplify"}


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
        ("agent-review", ".claude/agents/"),
        ("review-permissions", "permissions.allow"),
        ("test-conventions", "new"),
        ("test-evaluation", "existing"),
        ("feature-flags", "toggle"),
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
        ("review-permissions", "claude-hook-review"),
        ("test-conventions", "test-evaluation"),
        ("test-evaluation", "test-conventions"),
        ("agent-review", "skill-review"),
        # No reciprocal row: name-only means feature-flags won't normally
        # auto-fire, so config-environments should keep firing on
        # toggle-adjacent questions rather than exclude it.
        ("feature-flags", "config-environments"),
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
        frontmatter = parse_frontmatter(_skill_file(skill_name))
        assert _disables_model_invocation(frontmatter), (
            f"{skill_name}/SKILL.md is registered as a command skill but is missing "
            f"disable-model-invocation: true; add it or remove from COMMAND_SKILLS"
        )

    @pytest.mark.parametrize("skill_name", COMMAND_SKILLS)
    def test_command_skill_excluded_from_model_invokable_corpus(self, skill_name):
        """A registered command skill must not count toward the listing budget.

        Asserted directly against _model_invokable_skills(), independent of
        the aggregate budget's momentary headroom — TestTotalListingBudgetUnderSonnet
        would only catch a regression here if the corpus happened to be close
        to the cap at the time.
        """
        assert skill_name not in _model_invokable_skills(), (
            f"{skill_name} is registered in COMMAND_SKILLS (disable-model-invocation: "
            f"true) but still appears in _model_invokable_skills() — check that flag "
            f"is actually present and the plugin-scanning loop is reading it"
        )


class TestNameOnlySkillContracts:
    """Contract tests for skills with skillOverrides: name-only.

    These skills are model-invokable by exact name (the harness lists them by
    name only, no description) but their descriptions are excluded from the
    always-loaded listing budget. They must NOT carry disable-model-invocation:
    true — skillOverrides: name-only is the single source of truth for their
    invocation mode, and stacking the frontmatter flag would create ambiguous
    precedence. See docs/skills.md "Skills available by name" for rationale.
    """

    NAME_ONLY_SKILLS = _name_only_skills()

    @pytest.mark.parametrize("skill_name", NAME_ONLY_SKILLS)
    def test_name_only_skill_has_skill_file(self, skill_name):
        """name-only skills listed in skillOverrides must have an existing SKILL.md."""
        if skill_name in BUILTIN_NAME_ONLY_SKILLS:
            pytest.skip(f"{skill_name!r} is a bundled Claude Code skill — no repo SKILL.md by design")
        skill_path = _skill_file(skill_name)
        assert skill_path.exists(), (
            f"{skill_name} is listed as name-only in skillOverrides but has no "
            f"SKILL.md at {skill_path}. Either: remove the skillOverrides entry, "
            f"create the SKILL.md, or add to BUILTIN_NAME_ONLY_SKILLS if this is "
            f"a bundled Claude Code skill with no repo SKILL.md."
        )

    @pytest.mark.parametrize("skill_name", NAME_ONLY_SKILLS)
    def test_name_only_skill_does_not_carry_disable_flag(self, skill_name):
        """name-only skills must not carry disable-model-invocation: true."""
        if skill_name in BUILTIN_NAME_ONLY_SKILLS:
            pytest.skip(f"{skill_name!r} is a bundled Claude Code skill — no frontmatter to check")
        frontmatter = parse_frontmatter(_skill_file(skill_name))
        assert not _disables_model_invocation(frontmatter), (
            f"{skill_name}/SKILL.md carries disable-model-invocation: true but is also "
            f"set to name-only in skillOverrides. Remove the frontmatter flag — "
            f"skillOverrides: name-only is the single source of truth for invocation control."
        )


    def test_builtin_name_only_allowlist_matches_settings(self):
        """BUILTIN_NAME_ONLY_SKILLS must exactly match name-only settings entries that have no SKILL.md.

        Fails if:
        - A bundled skill is added to name-only in settings but omitted from BUILTIN_NAME_ONLY_SKILLS
        - A BUILTIN_NAME_ONLY_SKILLS entry is no longer name-only in settings
        - A BUILTIN_NAME_ONLY_SKILLS entry actually has a repo SKILL.md (repo skills use the
          regular contract and do not belong in the allowlist)
        """
        computed = {n for n in _name_only_skills() if not _skill_file(n).exists()}
        assert computed == BUILTIN_NAME_ONLY_SKILLS, (
            f"BUILTIN_NAME_ONLY_SKILLS is out of sync with settings.json. "
            f"In settings (no SKILL.md) but not in allowlist: {computed - BUILTIN_NAME_ONLY_SKILLS!r}. "
            f"In allowlist but not in settings or has a SKILL.md now: {BUILTIN_NAME_ONLY_SKILLS - computed!r}."
        )


class TestNameDispatchedNoTriggerContracts:
    """Contract tests for skills in _NAME_DISPATCHED_NO_TRIGGER.

    These skills are user-invocable: false and model/hook-dispatched by name,
    but deliberately carry no TRIGGER blocks. This class enforces the invariant:
    each exempted skill's frontmatter must NOT contain a TRIGGER when: block.
    Without this check, a contributor adding TRIGGER blocks to an exempted skill
    would pass the structural tests silently — the exemption would grant a pass
    precisely when the contract was violated.
    """

    NAME_DISPATCHED_SKILLS = list(_NAME_DISPATCHED_NO_TRIGGER)

    @pytest.mark.parametrize("skill_name", NAME_DISPATCHED_SKILLS)
    def test_name_dispatched_skill_has_no_trigger_block(self, skill_name):
        """Name-dispatched skills must NOT carry TRIGGER when: in their frontmatter."""
        desc = _skill_description(skill_name)
        assert "TRIGGER when:" not in desc, (
            f"{skill_name}/SKILL.md is in _NAME_DISPATCHED_NO_TRIGGER (exempted from "
            f"TRIGGER discipline) but its description or when_to_use contains 'TRIGGER when:'. "
            f"Remove the TRIGGER blocks — this skill is always dispatched by name, "
            f"never by description auto-trigger — or remove it from _NAME_DISPATCHED_NO_TRIGGER."
        )


def _write_flagged_skills(skills_dir: Path, name_infix: str) -> None:
    for name_stem, flag_line in (
        ("disabled", "disable-model-invocation: true"),
        ("enabled-explicit", "disable-model-invocation: false"),
        ("quoted-false", 'disable-model-invocation: "false"'),
        ("user-invocable", "user-invocable: true"),
    ):
        skill_name = f"{name_stem}-{name_infix}skill"
        _write_skill_md_fixture(
            skills_dir,
            skill_name,
            f"---\nname: {skill_name}\ndescription: Carries a flag.\n{flag_line}\n---\nbody\n",
        )


class TestFrontmatterReaders:
    @pytest.fixture
    def skills_tree(self, tmp_path, monkeypatch) -> Path:
        """A temporary skills tree that discovery reads in place of the repo's.

        Discovery reads the module-level SKILLS_DIR and _PLUGINS_DIR, so both are patched
        here and in _skill_md_helpers, whose functions read their own copies.
        """
        skills_dir = tmp_path / "skills"
        _write_skill_md_fixture(
            skills_dir,
            "valid-skill",
            "---\nname: valid-skill\ndescription: Does a thing.\n"
            "when_to_use: 'TRIGGER when: only the when_to_use key says so.'\n"
            "user-invocable: false\n---\nbody\n",
        )
        _write_skill_md_fixture(skills_dir, "malformed-skill", _MALFORMED_YAML_SKILL)
        _write_skill_md_fixture(skills_dir, "frontmatterless-skill", "no frontmatter here\n")
        _write_flagged_skills(skills_dir, "")
        plugin_skills_dir = tmp_path / "plugins" / "sample-plugin" / "skills"
        _write_skill_md_fixture(
            plugin_skills_dir,
            "valid-plugin-skill",
            "---\nname: valid-plugin-skill\ndescription: Does a plugin thing.\n"
            "user-invocable: false\n---\nbody\n",
        )
        _write_skill_md_fixture(plugin_skills_dir, "malformed-plugin-skill", _MALFORMED_YAML_SKILL)
        _write_skill_md_fixture(plugin_skills_dir, "frontmatterless-plugin-skill", "no frontmatter here\n")
        _write_flagged_skills(plugin_skills_dir, "plugin-")
        module = sys.modules[__name__]
        for patched_module in (module, _skill_md_helpers):
            monkeypatch.setattr(patched_module, "SKILLS_DIR", skills_dir)
            monkeypatch.setattr(patched_module, "_PLUGINS_DIR", tmp_path / "plugins")
        return skills_dir

    @pytest.mark.parametrize(
        "skill_text",
        [_MALFORMED_YAML_SKILL, _UNCLOSED_BLOCK_SKILL, _SCALAR_ONLY_SKILL, _IMPOSSIBLE_DATE_SKILL],
        ids=["malformed-yaml", "unclosed-block", "scalar-only-block", "impossible-date"],
    )
    def test_discovery_frontmatter_returns_empty_mapping_for_unreadable_frontmatter(
        self, tmp_path, skill_text
    ):
        skill_file = _write_skill_md_fixture(tmp_path, "broken-skill", skill_text)

        assert _discovery_frontmatter(skill_file) == {}

    def test_discovery_survives_malformed_and_frontmatterless_skills(self, skills_tree):
        specialist_skills = _specialist_skills()
        model_invokable_skills = _model_invokable_skills()

        assert specialist_skills == ["valid-skill", "valid-plugin-skill"]
        assert model_invokable_skills == [
            "enabled-explicit-skill",
            "frontmatterless-skill",
            "malformed-skill",
            "quoted-false-skill",
            "user-invocable-skill",
            "valid-skill",
            "enabled-explicit-plugin-skill",
            "frontmatterless-plugin-skill",
            "malformed-plugin-skill",
            "quoted-false-plugin-skill",
            "user-invocable-plugin-skill",
            "valid-plugin-skill",
        ]

    def test_skill_description_sees_a_trigger_block_that_is_only_in_when_to_use(self, skills_tree):
        # The name-dispatched negative check reads this same string, so a TRIGGER
        # block moved into when_to_use must not slip past it.
        assert "TRIGGER when:" in _skill_description("valid-skill")

    @pytest.mark.parametrize(
        ("skill_text", "expected_exception"),
        [
            (_MALFORMED_YAML_SKILL, yaml.YAMLError),
            (_UNCLOSED_BLOCK_SKILL, FrontmatterError),
            (_SCALAR_ONLY_SKILL, FrontmatterError),
        ],
        ids=["malformed-yaml", "unclosed-block", "scalar-only-block"],
    )
    def test_skill_description_raises_for_a_malformed_skill(
        self, skills_tree, skill_text, expected_exception
    ):
        """A malformed listed skill raises instead of reading as an empty description."""
        _write_skill_md_fixture(skills_tree, "broken-skill", skill_text)

        with pytest.raises(expected_exception):
            _skill_description("broken-skill")

    def test_skill_description_is_empty_for_a_skill_without_frontmatter(self, skills_tree):
        assert _skill_description("frontmatterless-skill") == ""


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


class TestProjectLayerUsesReadNotSkill:
    """Project-layer loading must use the Read tool, not the Skill tool.

    Parent skills that support project-layer composition glob for a layer
    file and read it via the Read tool. Skill() invocation is broken when
    the layer carries disable-model-invocation: true — the harness blocks
    the invocation. Reading the file is the correct primitive: the parent
    incorporates the layer's content, not its invocation context.
    """

    PARENT_SKILLS = ["code-review", "plan-it", "plan-review", "pr-description", "test-conventions"]

    def _project_layer_section(self, skill_name: str) -> str:
        """Extract the project-layer loading paragraph from a skill body."""
        skill_file = _skill_file(skill_name)
        content = skill_file.read_text()
        if content.startswith("---"):
            closing = content.index("---", 3)
            body = content[closing + 3:]
        else:
            body = content
        lines = body.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.startswith("## ") and "project-specific layer" in line.lower():
                start = i
                break
        assert start is not None, (
            f"{skill_name}/SKILL.md has no '## ... project-specific layer ...' heading"
        )
        section_lines = []
        for line in lines[start + 1:]:
            if line.startswith("## "):
                break
            section_lines.append(line)
        return "\n".join(section_lines)

    @pytest.mark.parametrize("skill_name", PARENT_SKILLS)
    def test_project_layer_uses_read_tool(self, skill_name):
        """Project-layer section must instruct Read tool usage."""
        section = self._project_layer_section(skill_name)
        assert "Read tool" in section, (
            f"{skill_name}/SKILL.md project-layer section does not mention 'Read tool'; "
            f"parent skills must read the layer file via the Read tool, not invoke via Skill()"
        )

    @pytest.mark.parametrize("skill_name", PARENT_SKILLS)
    def test_project_layer_does_not_use_skill_tool(self, skill_name):
        """Project-layer section must not instruct Skill tool invocation."""
        section = self._project_layer_section(skill_name)
        assert "Skill tool" not in section, (
            f"{skill_name}/SKILL.md project-layer section still mentions 'Skill tool'; "
            f"Skill() invocation is blocked when the layer carries disable-model-invocation: true"
        )


class TestModelInvokableSkillFrontmatterIsStrictYaml:
    """Every model-invokable skill's frontmatter must parse as strict YAML.

    The Claude Code harness uses a lenient YAML parser that accepts inline
    unquoted scalars containing `: ` (e.g., `description: TRIGGER when:
    ...`). Relying on that leniency is fragile — any parser-strictness
    change in the harness would silently truncate the description at the
    first `: `, dropping the TRIGGER / DO NOT TRIGGER blocks. Block-fold
    (`description: >`) or double-quote any value containing `: `.
    validate() reports unclosed and non-mapping frontmatter without the text `not strict YAML`.
    """

    MODEL_INVOKABLE_SKILLS = _model_invokable_skills()

    @pytest.mark.parametrize("skill_name", MODEL_INVOKABLE_SKILLS)
    def test_frontmatter_parses_strictly(self, skill_name):
        violations = validate(_skill_file(skill_name))
        yaml_violations = [v for v in violations if "not strict YAML" in v]
        assert not yaml_violations, "\n".join(yaml_violations)


class TestModelInvokableDescriptionLength:
    """Enforce the harness's per-skill description-length cap.

    The Claude Code harness truncates each skill listing entry's combined
    `description` + `when_to_use` at MAX_SKILL_DESCRIPTION_CHARS. A skill
    that exceeds the cap has the tail of its description silently dropped
    from the system prompt — losing TRIGGER / DO NOT TRIGGER discipline if
    that block is what gets cut. User-only command skills
    (disable-model-invocation: true) are excluded because their descriptions
    are suppressed from the listing entirely.
    validate() returns at check 1 for unclosed and non-mapping frontmatter, so no length is computed.
    """

    MODEL_INVOKABLE_SKILLS = _model_invokable_skills()

    @pytest.mark.parametrize("skill_name", MODEL_INVOKABLE_SKILLS)
    def test_description_within_harness_cap(self, skill_name):
        violations = validate(_skill_file(skill_name))
        length_violations = [v for v in violations if "exceeds harness cap" in v]
        assert not length_violations, "\n".join(length_violations)


class TestTotalListingBudgetUnderSonnet:
    """Pin the aggregate skill-listing total under Claude Code's char budget.

    The per-skill cap (``TestModelInvokableDescriptionLength``) catches one
    description exceeding 1,536 chars but not the case where many short
    descriptions push the total over the listing budget. When that happens,
    Claude Code drops descriptions for the least-used skills entirely; the
    user-facing symptom is silent — Claude stops auto-loading affected
    skills until the user notices and runs ``/doctor``.
    """

    MODEL_INVOKABLE_SKILLS = _model_invokable_skills()

    def test_total_within_listing_budget(self):
        skill_paths = [_skill_file(name) for name in self.MODEL_INVOKABLE_SKILLS]
        violations = corpus_budget_violations(skill_paths, SKILL_LISTING_BUDGET_CHARS)
        assert not violations, "\n".join(violations)


_DISPOSITION_RULE_ANCHOR_RE = re.compile(r"<!-- DISPOSITION_RULE:(\S+) (start|end) -->")

# The DISPOSITION_RULE anchor regions in the corpus. Asserted as an
# exact set, not just "each found anchor is non-trivial" — a corpus scan
# alone passes vacuously if an entire anchor pair is deleted.
_EXPECTED_DISPOSITION_RULE_ANCHORS = {
    ("code-review", "code-review-contradiction-route"),
    ("code-review", "code-review-defer-invariant"),
    ("code-review", "code-review-disposition-challenge"),
    ("code-review", "code-review-fix-plan-pilot"),
    ("code-review", "code-review-new-primitive-route"),
    ("code-review", "code-review-round-cap-consult-verdict"),
    ("plan-review", "plan-review-fix-or-ask"),
}


# --- Trigger A regression: a $(...)-assigned variable referenced later in the
# same fenced block. See docs/worktree-bash-guard.md for the full trigger
# taxonomy this guards one shape of. Fence detection comes from
# _fenced_code_blocks in _skill_md_helpers.py, which keys _FENCE_OPEN_RE on the
# fence delimiter itself, not a bash/sh language tag, so an untagged ``` fence
# is scanned the same as a tagged ```bash one.

# Matches an assignment at the start of a statement -- line start, or after a
# statement separator (;, &&, ||) -- optionally preceded by local/export/
# declare/readonly, so `local FOO=$(cmd)` and `echo x; FOO=$(cmd)` are found
# the same as a bare leading `FOO=$(cmd)`. Not a full shell parser (a `;`
# inside a string literal would also split here); over-flagging is the safe
# direction for this scan, matching _closed_fence_line_indices's own bias.
_TRIGGER_A_ASSIGN_RE = re.compile(
    r"(?:^|[;&|]\s*)\s*(?:local|export|declare|readonly)?\s*([A-Za-z_][A-Za-z0-9_]*)=\$\("
)


def _trigger_a_matches(markdown_text: str) -> list[str]:
    """Variable names assigned via `$(...)` and referenced again in a later
    statement in the same fenced block — the worktree-isolation Bash-tool
    guard's Trigger A shape. "Later statement" includes the remainder of the
    assignment's own line (a same-line `&&`/`;`-chained reference) as well as
    every subsequent line. An assignment never referenced again (ordinary
    documentation snippets corpus-wide) is not flagged."""
    lines = markdown_text.split("\n")
    matches: list[str] = []
    for open_index, content in _fenced_code_blocks(lines):
        if _fence_excluded_by_marker(lines, open_index):
            continue
        for i, line in enumerate(content):
            for assign_match in _TRIGGER_A_ASSIGN_RE.finditer(line):
                var_name = assign_match.group(1)
                reference_re = re.compile(r"\$\{?" + re.escape(var_name) + r"\b")
                remainder = line[assign_match.end() :]
                later_lines = content[i + 1 :]
                if reference_re.search(remainder) or any(reference_re.search(later_line) for later_line in later_lines):
                    matches.append(var_name)
    return matches


class TestTriggerAFenceScan:
    """Regression guard for the worktree-isolation Bash-tool guard's Trigger A
    shape (docs/worktree-bash-guard.md): a $(...)-assigned variable used in a
    later statement in the same fenced block. After this repo's script
    migration, no SKILL.md in the corpus should carry this shape any more —
    every multi-step recipe now calls a single dedicated script instead."""

    def test_flags_tagged_fence(self) -> None:
        text = "```bash\nFOO=$(echo hi)\necho \"$FOO\"\n```"
        assert _trigger_a_matches(text) == ["FOO"]

    def test_flags_untagged_fence(self) -> None:
        text = "```\nFOO=$(echo hi)\necho \"$FOO\"\n```"
        assert _trigger_a_matches(text) == ["FOO"]

    def test_does_not_flag_marker_excluded_fence(self) -> None:
        text = (
            "<!-- HOOK_TEST_FIXTURE: example -->\n\n"
            "```bash\nFOO=$(echo hi)\necho \"$FOO\"\n```"
        )
        assert _trigger_a_matches(text) == []

    def test_flags_same_line_chained_reference(self) -> None:
        text = '```bash\nFOO=$(git rev-parse HEAD) && echo "deploying $FOO"\n```'
        assert _trigger_a_matches(text) == ["FOO"]

    def test_flags_local_prefixed_assignment(self) -> None:
        text = '```bash\nlocal FOO=$(cmd)\necho "$FOO"\n```'
        assert _trigger_a_matches(text) == ["FOO"]

    def test_flags_semicolon_joined_same_line(self) -> None:
        text = '```bash\necho start; FOO=$(cmd); echo "$FOO"\n```'
        assert _trigger_a_matches(text) == ["FOO"]

    def test_does_not_flag_unused_assignment(self) -> None:
        text = "```bash\nFOO=$(echo hi)\necho unrelated\n```"
        assert _trigger_a_matches(text) == []

    @pytest.mark.parametrize("skill_md_path", _all_skill_md_paths(), ids=lambda p: str(p))
    def test_no_trigger_a_shape_in_corpus(self, skill_md_path: Path) -> None:
        matches = _trigger_a_matches(skill_md_path.read_text())
        assert not matches, (
            f"{skill_md_path}: fenced block assigns and later references "
            f"{matches!r} via $(...) — the worktree-isolation Bash-tool "
            "guard's Trigger A shape (see docs/worktree-bash-guard.md); "
            "replace with a single dedicated script call, or exclude with a "
            "HOOK_TEST_FIXTURE/HOOK_SCRIPT_CONTENT_EXAMPLE marker comment if "
            "this block is never typed into an agent's Bash tool"
        )


def test_disposition_rule_anchors_present() -> None:
    """Every DISPOSITION_RULE:<name> anchor pair exists, is ordered, and is non-trivial.

    Layer 1 of the disposition-fidelity eval (see evals/README.md): a
    deterministic, zero-flake guard against a governing rule being deleted
    outright. Deliberately asserts presence and a minimal non-whitespace
    length, not exact text — a legitimate reword must not false-fail this
    test. Catching a reword-into-weakness is Layer 2's (the live
    disposition-fidelity method's) job, not this test's.

    Delegates the actual extraction and validation (missing/misordered/
    duplicated anchors) to extract_governing_rule() — the same parser
    evals/run_skill_evals.py uses at eval time — rather than re-deriving
    anchor positions with a second, independent regex. Two parsers over the
    same anchor syntax could silently diverge (this test passing on a
    SKILL.md the live eval harness would then reject with a ValueError, or
    vice versa); calling the production parser here closes that gap. The
    regex below is retained only to discover which anchor *names* exist in
    each file — extract_governing_rule() needs a name to look up.

    The found set is also compared against _EXPECTED_DISPOSITION_RULE_ANCHORS
    exactly, not just "each found anchor is non-trivial." A scan that only
    validates names it finds passes vacuously if an entire anchor pair is
    deleted, since no name is left to fail the length check.
    """
    MIN_RULE_TEXT_CHARS = 20  # a single stripped char would pass a bare "non-empty" check

    found: set[tuple[str, str]] = set()
    for skill_md_path in _all_skill_md_paths():
        text = skill_md_path.read_text()
        skill_name = skill_md_path.parent.name
        anchor_names = {m.group(1) for m in _DISPOSITION_RULE_ANCHOR_RE.finditer(text)}

        for name in anchor_names:
            found.add((skill_name, name))
            enclosed = extract_governing_rule(skill_md_path, name)
            assert len(enclosed) >= MIN_RULE_TEXT_CHARS, (
                f"{skill_md_path}: DISPOSITION_RULE:{name} encloses only {len(enclosed)} "
                f"non-whitespace chars after stripping — looks deleted or gutted"
            )

    assert found == _EXPECTED_DISPOSITION_RULE_ANCHORS, (
        "DISPOSITION_RULE anchors drifted from the expected set — a region was "
        "added, renamed, or deleted.\n"
        f"  expected but missing: {sorted(_EXPECTED_DISPOSITION_RULE_ANCHORS - found)}\n"
        f"  found but unexpected: {sorted(found - _EXPECTED_DISPOSITION_RULE_ANCHORS)}"
    )


def test_skill_overrides_documented_in_docs_skills_md() -> None:
    """Every non-on skillOverride must have a table row in docs/skills.md.

    Covers both "off" entries (bundled skills disabled) and "name-only" entries
    (repo or bundled skills available by name without description budget cost). Each must
    appear as a | `/<name>` | table row so its rationale is visible to contributors.
    """
    repo_root = REPO_ROOT
    settings = json.loads((repo_root / "claude/.claude/settings.json").read_text())
    docs_text = (repo_root / "docs/skills.md").read_text()
    for skill_name, state in settings.get("skillOverrides", {}).items():
        if state == "on":
            continue
        marker = f"| `/{skill_name}` |"
        assert marker in docs_text, (
            f"skillOverrides has {skill_name!r} ({state!r}) but docs/skills.md has no "
            f"`{marker}` row. Every non-on skillOverride entry needs a rationale row "
            "in docs/skills.md."
        )


# Destructive-forms-require-ask: the bare and absolute-path invocations of
# cleanup-merged-branches.sh and cleanup-idle-open-pr-worktrees.sh, without
# --dry-run, must live in permissions.ask rather than permissions.allow --
# an allow entry ran the destructive form silently, with no per-run
# confirmation at the Claude Code layer (GH-429). Pinned so a later edit
# can't silently re-merge these back into allow (ciso-reviewer finding,
# GH-429 code review).
_DESTRUCTIVE_CLEANUP_FORMS = [
    "Bash(~/.claude/scripts/cleanup-merged-branches.sh)",
    "Bash(cleanup-merged-branches)",
    "Bash(~/.claude/scripts/cleanup-idle-open-pr-worktrees.sh)",
    "Bash(cleanup-idle-open-pr-worktrees)",
]
_DRY_RUN_CLEANUP_FORMS = [
    "Bash(~/.claude/scripts/cleanup-merged-branches.sh --dry-run)",
    "Bash(cleanup-merged-branches --dry-run)",
    "Bash(~/.claude/scripts/cleanup-idle-open-pr-worktrees.sh --dry-run)",
    "Bash(cleanup-idle-open-pr-worktrees --dry-run)",
]


def test_destructive_cleanup_forms_require_ask_not_allow() -> None:
    """Destructive cleanup invocations must be in permissions.ask, absent
    from permissions.allow. --dry-run siblings must stay in permissions.allow."""
    repo_root = REPO_ROOT
    settings = json.loads((repo_root / "claude/.claude/settings.json").read_text())
    permissions = settings.get("permissions", {})
    allow = permissions.get("allow", [])
    ask = permissions.get("ask", [])
    for form in _DESTRUCTIVE_CLEANUP_FORMS:
        assert form in ask, f"{form!r} must be in permissions.ask"
        assert form not in allow, f"{form!r} must not be in permissions.allow"
    for form in _DRY_RUN_CLEANUP_FORMS:
        assert form in allow, f"{form!r} must stay in permissions.allow"


def test_every_config_get_key_named_in_a_skill_is_in_the_config_schema() -> None:
    """Every `config-get.sh <key>` a shipped SKILL.md invokes names a schema key."""
    schema_keys = config_schema()
    call_sites: list[tuple[Path, str]] = []
    for skill_md_path in _all_skill_md_paths():
        for key_match in re.finditer(
            r"config-get\.sh ([a-z0-9_]+)", skill_md_path.read_text()
        ):
            call_sites.append((skill_md_path, key_match.group(1)))
    # pr-description and transcript-analysis each carry one call site.
    # The two pilot regions add two more; the floor excludes them so removing
    # either region leaves this test green. Removing or redirecting a pilot region fails
    # test_disposition_rule_anchors_present and that region's pin test.
    minimum_call_sites = 2
    assert len(call_sites) >= minimum_call_sites, (
        f"found {len(call_sites)} config-get.sh call sites across shipped skills; "
        f"expected at least {minimum_call_sites}, so the pattern may have drifted"
    )
    unknown_keys = [
        f"{skill_md_path}: {key}"
        for skill_md_path, key in call_sites
        if key not in schema_keys
    ]
    assert not unknown_keys, (
        f"config-get.sh keys not in config-keys.psv: {unknown_keys}"
    )
