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
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

import _skill_md_helpers  # monkeypatch target for the helper module's own SKILLS_DIR and _PLUGINS_DIR
import pytest
import yaml

# pyproject.toml's pythonpath also puts claude/.claude/scripts on the import
# path, where the production config-keys.psv parser lives.
from _config import schema as config_schema

# pyproject.toml's pythonpath also puts claude/.claude/scripts on the import
# path, where the auxiliary-filename constant shared with select-tests.py lives.
from _skill_auxiliary_files import SKILL_AUXILIARY_MD_NAMES
from _skill_md_helpers import (
    _AGENTS_DIR,
    _GLOBAL_CLAUDE_MD,
    _IMPOSSIBLE_DATE_SKILL,
    _MALFORMED_YAML_SKILL,
    _PLUGINS_DIR,
    _SCALAR_ONLY_SKILL,
    _UNCLOSED_BLOCK_SKILL,
    _all_doc_paths,
    _all_skill_md_files,
    _all_skill_md_paths,
    _assert_citation_resolves_to_heading,
    _assert_pinned_clause_right_bounded,
    _blank_code_regions,
    _blank_frontmatter,
    _closed_fence_line_indices,
    _extract_citations,
    _fence_excluded_by_marker,
    _fenced_code_blocks,
    _heading_section_text,
    _raw_heading_section_text,
    _resolve_citation_target,
    _section_between,
    _skill_body,
    _skill_file,
    _write_skill_md_fixture,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    CLAUDE_DIR,
    REPO_ROOT,
    SCRIPTS_DIR,
    SKILLS_DIR,
    extract_skill_command,
    heading_texts,
    normalize_heading,
    run_skill_command,
)

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


class TestContinuityFileBucketCrosscheck:
    """Pin the autonomous-vs-authorization bucket cross-check in continuity skills.

    handoff (§3/§3.5) and brief (§6/§6.5) both split next steps into an
    autonomously-safe section and a pending-engineer-authorization section,
    each with a categorization rule. The rule only helps if the writer
    re-scans the safe section against it before writing the file; these
    pre-write checklist bullets are that trigger. Without them an
    irreversible step (a bulk delete, an external communication) can ship
    labeled autonomously-safe and be executed unprompted by a resuming
    session.
    """

    def test_handoff_prewrite_checklist_crosschecks_section3(self):
        """handoff's pre-write checklist must direct re-checking a §3 step
        against the §3.5 rule's underlying principle, not just the
        mechanically-scanned anchor shapes check-handoff.py's soft warning
        covers -- the script's anchor-shape scan is necessarily narrower than
        the full categorization rule, so this residual instruction is what
        keeps that gap from being a silent regression."""
        body = _skill_file("handoff").read_text()
        assert "can still belong in §3.5" in body
        assert "not the underlying principle" in body

    def test_brief_prewrite_checklist_crosschecks_section6(self):
        """brief's pre-write checklist must re-scan §6 against the §6.5 rule."""
        assert "re-checked against the §6.5 categorization rule" in _skill_file("brief").read_text()

    def test_handoff_section3_5_excepts_plain_ship_steps_when_shipping_is_active(self):
        """handoff §3.5 must carve out commit, push and PR creation when the
        resolver exits 0 and names the resumer's own resolver exit as the
        filed step's precondition, so a handoff does not file them as pending
        authorization on a machine where autonomous shipping is active."""
        match = re.search(r"## §3\.5.*?(?=\n## §4)", _skill_file("handoff").read_text(), re.DOTALL)
        assert match, "handoff/SKILL.md §3.5 section not found — has it been renamed?"
        assert "Exception:" in match.group(0), "handoff/SKILL.md §3.5 'Exception:' clause not found — has it been renamed?"
        exception = match.group(0).split("Exception:", 1)[1]
        assert "autonomous-shipping-active.sh" in exception
        assert "exit 0" in exception
        assert "only when" in exception, "the exception must be conditional, not unconditional"
        assert "precondition" in exception, "the filed step must carry the exit as a precondition"
        assert "resumer's own" in exception, "the resumer, not the writer, must evaluate the exit"
        assert "no hold" in exception
        assert "never to the default branch" in exception
        assert "§3.5" in exception, "a stated hold must be filed under §3.5"

    def test_brief_section6_5_excepts_plain_ship_steps_when_shipping_is_active(self):
        """brief §6.5 carries the same exception as handoff §3.5."""
        match = re.search(r"## §6\.5.*?(?=\n## §7 )", _skill_file("brief").read_text(), re.DOTALL)
        assert match, "brief/SKILL.md §6.5 section not found — has it been renamed?"
        assert "Exception:" in match.group(0), "brief/SKILL.md §6.5 'Exception:' clause not found — has it been renamed?"
        exception = match.group(0).split("Exception:", 1)[1]
        assert "autonomous-shipping-active.sh" in exception
        assert "exit 0" in exception
        assert "only when" in exception, "the exception must be conditional, not unconditional"
        assert "precondition" in exception, "the filed step must carry the exit as a precondition"
        assert "resumer's own" in exception, "the resumer, not the writer, must evaluate the exit"
        assert "no hold" in exception
        assert "never to the default branch" in exception
        assert "§6.5" in exception, "a stated hold must be filed under §6.5"

    def test_handoff_prewrite_checklist_points_at_section3_5_for_the_principle(self):
        """The checklist must send the reader to §3.5 for the principle rather than restate it, so it
        cannot contradict §3.5's exception."""
        anchor = "A §3 step the script did *not* warn on"
        checklist_lines = [line for line in _skill_file("handoff").read_text().splitlines() if anchor in line]
        assert checklist_lines, f"handoff/SKILL.md checklist line starting {anchor!r} not found — has it been renamed?"
        assert "including its exception" in checklist_lines[0]

    def test_handoff_prewrite_checklist_crosschecks_section2_6(self):
        """handoff's pre-write checklist must verify §2.6 was populated before writing."""
        assert (
            "§2.6 is populated — a faithful task-list serialization with per-item ordinal, "
            'status, and blocking edges, or "None." — and carries the resume directive'
            in _skill_file("handoff").read_text()
        )


class TestHandoffCommitMarkerCoveredWork:
    """Pin handoff's instruction to land marker-covered work before the boundary.

    §5 now delegates the live/historical/absent computation to
    `marker.sh status` (see claude/.claude/scripts/marker.sh and
    hooks/tests/test_marker_script.py's TestMarkerScriptStatus* classes for
    the mechanism itself) rather than explaining the marker-content-hash
    mechanics in prose — these tests pin that the delegation instruction and
    the commit-first directive survive, not the mechanics explanation that
    the deterministic script now owns.
    """

    def test_handoff_section5_directs_running_marker_status_and_committing_flagged_work(self):
        """§5 must direct running marker.sh status, describe its live/
        historical/absent labeling, and direct the writer to commit finished
        marker-covered work before writing the file when the reconciliation
        flag fired."""
        body = _skill_file("handoff").read_text()
        assert "Run `~/.claude/scripts/marker.sh status` and paste its output verbatim" in body
        assert "each labeled live, historical, or absent" in body
        assert "code-review or skill-review marker whose covered state has uncommitted" in body
        assert "commit it *before* writing this file" in body

    def test_handoff_section5_does_not_claim_markers_die_with_their_session(self):
        """Guard against reintroducing the session-keyed expiry claim.

        Gates authorize on a marker's stored hash, not on the session id in
        its filename, so text asserting that a marker is spent once its
        session ends is false — and costly, because it sends the writer down a
        re-review path that is not required. The claim gets its own assertion
        because it is plausible on its face and was true of an earlier gate
        implementation, which makes it the likely thing for a future edit to
        reintroduce.
        """
        body = _skill_file("handoff").read_text()
        assert "keyed to the session that wrote them" not in body
        assert "nothing listed here will satisfy a pre-commit gate" not in body

    def test_handoff_prewrite_checklist_verifies_marker_covered_work_committed(self):
        """The pre-write checklist must carry the commit-first verification, with the
        §3 fallback for work that is not commit-ready."""
        assert (
            "§5's script output shows no unresolved reconciliation flag; where one "
            "fired, §3 names the review skill the resuming session must re-run to "
            "commit the covered work first" in _skill_file("handoff").read_text()
        )


class TestHandoffTaskListPersistence:
    """Pin the §2.6 task-list serialization and resume directive in handoff.

    Without a faithful task-list serialization and an explicit resume
    directive, a session resumed from a handoff file starts with an empty
    task list and re-derives remaining work from prose instead of tracking
    it from the live state the prior session captured. Resumed sessions are
    typically non-TTY and expose no task-list tool (Task tools are gated on
    an interactive TTY upstream), so tracking must fall back to inline
    conversation state rather than hard-depending on the tool.
    """

    def test_handoff_task_list_reads_live_state_not_memory(self):
        """§2.6 must instruct reading the current task list from the tool if present,
        else from inline-tracked items — never reconstructed from memory."""
        assert (
            "Read the current task list — from your session's task-list tool if it "
            "exposes one, otherwise from the inline items you have been tracking (not "
            "reconstructed from memory) — and list each item with a stable ordinal, its "
            "status — `completed` / `in_progress` / `pending` — and, for pending/in_progress "
            "items, which ordinals block it and (for the in_progress item) its "
            "`activeForm`. Preserve order. Example: `3. [pending] Phase B: … (blocked by 2)`."
            in _skill_file("handoff").read_text()
        )

    def test_handoff_task_list_has_resume_directive(self):
        """§2.6's resume directive must cover authoritativeness (no plan-file/memory
        fallback), tracking order and dependency edges, the no-re-add-completed dedup
        rule, opportunistic (not required) tool use, and the inline fallback."""
        assert (
            "§2.6 is the authoritative source of remaining task state on resume — do not "
            "reconstruct the task list from the plan file or from memory. As you resume, "
            "track the `pending` and `in_progress` items below, preserving order and their "
            "`blockedBy`/`blocks` relationships (map the serialized ordinals to the items "
            "in that position); completed items are listed for context only — do not "
            "re-add them. If your session exposes a task-list tool (e.g. "
            "`TaskCreate`/`TaskUpdate`), mirror the items into it"
            in _skill_file("handoff").read_text()
        )
        assert (
            "If it does not — common for resumed sessions — track them inline. Tracking "
            "these items is a safe, reversible action, not gated by the artifact preamble's "
            "re-confirm-before-executing rule (which is scoped to irreversible/shared-state "
            "actions); a missing task-list tool is not a blocker."
            in _skill_file("handoff").read_text()
        )


class TestHandoffMidFlightNoteUnconditional:
    """Pin that §2.5's mid-flight note and its pre-write-checklist crosscheck fire
    unconditionally, not only on context-limit handoffs, so a phase-complete
    handoff can't silently strand a still-running background dispatch with no
    record of it.

    Mutation-tested: re-adding the "If the handoff reason is context-limit,"
    gate, or dropping the collected/stranded terms, flips these assertions red.
    """

    def test_handoff_section2_5_mid_flight_note_has_no_context_limit_gate(self):
        """§2.5's mid-flight sentence must not gate on the handoff reason, and
        must record each named dispatch as collected or stranded — pinned as one
        contiguous span so the two claims are proven adjacent, not merely
        present somewhere in the file."""
        body = _skill_file("handoff").read_text()
        assert "If the handoff reason is context-limit," not in body
        assert (
            "whatever the handoff reason: open tool calls — including any "
            "background subagent dispatch this session spawned, named by its "
            "`agent-<agentId>` (never its full transcript path, which embeds "
            "this session's own id) — and pending verifications (see §2.6 for "
            "task-list state). Record each named dispatch as **collected** "
            "(its output was folded into this handoff before writing — see "
            "\"Before writing: collect in-flight background dispatches\" above) "
            "or **stranded**"
            in body
        )

    def test_handoff_prewrite_checklist_mid_flight_line_has_no_context_limit_gate(self):
        """The pre-write checklist's mid-flight line must not gate on the
        handoff reason either, and must reference the collected/stranded label."""
        assert (
            "§2.5 names what was mid-flight at the time of the handoff, "
            "regardless of handoff reason — including any background subagent "
            "dispatch this session spawned, by its `agent-<agentId>`, marked "
            "collected or stranded"
            in _skill_file("handoff").read_text()
        )


class TestHandoffCollectStepPinsLoadBearingClauses:
    """Pin the "Before writing: collect in-flight background dispatches" section's
    load-bearing clauses, so a future edit can't silently drop the
    ListAgents-unavailable fallback, reintroduce the deprecated blocking-wait
    path, or lose the subagent/re-fire handling without a test failing.

    Mutation-tested: dropping the ListAgents-unavailable fallback clause,
    reintroducing polling/TaskOutput, or dropping the subagent-gate or
    repeat-block-is-expected clauses each flips a distinct assertion below red.
    """

    def test_collect_step_falls_through_to_stranded_when_listagents_unavailable(self):
        body = _skill_file("handoff").read_text()
        assert "## Before writing: collect in-flight background dispatches" in body
        assert (
            "If `ListAgents` errors or is unavailable, skip straight to "
            "recording every not-yet-returned dispatch as stranded"
            in body
        )

    def test_collect_step_forbids_polling_and_taskoutput(self):
        body = _skill_file("handoff").read_text()
        assert "do not poll and do not call `TaskOutput`" in body

    def test_collect_step_states_subagents_never_reach_it_via_their_own_block(self):
        body = _skill_file("handoff").read_text()
        assert (
            "a subagent never reaches it as remediation for its own hard "
            "block, since the nudge hook's subagent gate exits before any "
            "escalation logic runs"
            in body
        )

    def test_collect_step_treats_a_repeat_hard_block_as_expected(self):
        body = _skill_file("handoff").read_text()
        assert (
            "that's expected, not a new problem — it just means the "
            "session is still past its threshold while following the "
            "block's own remediation"
            in body
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


_LEGACY_TRIGGER_METHODS = {"runtime", "description-fidelity", "behavioral-dispatch"}


def _validate_disposition_fidelity_case(case: dict, prefix: str, repo_root: Path) -> None:
    """Field validation for a single `method: "disposition-fidelity"` case.

    Extracted from test_trigger_cases_files_well_formed's discovery loop so it
    is unit-testable against synthetic cases (see TestValidateDispositionFidelityCase)
    without waiting for a real *-cases.json fixture to land in the repo.
    """
    scenario_file = case.get("scenario_file")
    assert isinstance(scenario_file, str) and scenario_file, f"{prefix}: 'scenario_file' must be a non-empty string"
    assert (repo_root / scenario_file).exists(), (
        f"{prefix}: scenario_file {scenario_file!r} does not exist at {repo_root / scenario_file}"
    )
    assert isinstance(case.get("rule_anchor"), str) and case["rule_anchor"], (
        f"{prefix}: 'rule_anchor' must be a non-empty string"
    )
    assert isinstance(case.get("judge_rubric"), str) and case["judge_rubric"], (
        f"{prefix}: 'judge_rubric' must be a non-empty string"
    )


def test_trigger_cases_files_well_formed() -> None:
    """Every *-cases.json file found under skills/ or plugins/ must be valid.

    Discovery-based: no hardcoded skill list. Validates shape only — never
    invokes a model. Auto-extends as case files are added in follow-up PRs.
    CI-safe (pure static check). Field validation branches on `method`: the
    three legacy trigger/no-trigger methods validate query/should_trigger
    fields; disposition-fidelity validates its own
    scenario_file/rule_anchor/judge_rubric fields. Every method in
    run_skill_evals.VALID_METHODS must be handled explicitly here — an
    unrecognized method fails loudly rather than silently skipping validation.
    """
    repo_root = REPO_ROOT
    found_files: list[Path] = []
    for base in [
        repo_root / "claude-skills" / "skills",
        repo_root / "plugins",
    ]:
        for p in base.glob("*/evals/*-cases.json"):
            found_files.append(p)
        for p in base.glob("*/skills/*/evals/*-cases.json"):
            found_files.append(p)

    assert found_files, "No *-cases.json files found — run the pilot skills setup first"

    for path in found_files:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}: invalid JSON — {exc}") from exc

        assert "skill_name" in data, f"{path}: missing 'skill_name'"
        assert "cases" in data, f"{path}: missing 'cases'"
        assert isinstance(data["cases"], list) and data["cases"], f"{path}: 'cases' must be a non-empty list"

        method = data.get("method")

        # skill_name must match the parent skill directory name
        skill_dir_name = path.parts[-3]  # …/skills/<name>/evals/<name>-cases.json
        assert data["skill_name"] == skill_dir_name, (
            f"{path}: skill_name={data['skill_name']!r} does not match parent dir {skill_dir_name!r}"
        )

        ids_seen: set[str] = set()
        for i, case in enumerate(data["cases"]):
            prefix = f"{path} case[{i}]"
            assert isinstance(case.get("id"), str) and case["id"], f"{prefix}: 'id' must be a non-empty string"
            assert case["id"] not in ids_seen, f"{prefix}: duplicate id {case['id']!r}"
            ids_seen.add(case["id"])

            if method in _LEGACY_TRIGGER_METHODS:
                assert isinstance(case.get("query"), str) and case["query"], (
                    f"{prefix}: 'query' must be a non-empty string"
                )
                assert isinstance(case.get("should_trigger"), bool), f"{prefix}: 'should_trigger' must be a boolean"
                if "also_not_triggered" in case:
                    ant = case["also_not_triggered"]
                    assert isinstance(ant, list) and all(isinstance(s, str) for s in ant), (
                        f"{prefix}: 'also_not_triggered' must be a list of strings"
                    )
                    assert data["skill_name"] not in ant, (
                        f"{prefix}: 'also_not_triggered' must not contain the skill's own name "
                        f"({data['skill_name']!r}) — a skill cannot be a misfire of itself"
                    )
            elif method == "disposition-fidelity":
                _validate_disposition_fidelity_case(case, prefix, repo_root)
            else:
                raise AssertionError(f"{path}: unrecognized method {method!r} — add a validation branch here")


class TestValidateDispositionFidelityCase:
    """Synthetic-case coverage for _validate_disposition_fidelity_case.

    No real *-cases.json with method "disposition-fidelity" exists in the
    repo yet (that method ships with zero active cases — see
    evals/README.md), so without this class the branch is unexercised until
    the first real fixture lands. Uses an existing repo file as a stand-in
    scenario_file — the validator only checks existence, not content shape.
    """

    _REPO_ROOT = REPO_ROOT
    _EXISTING_SCENARIO_FILE = "evals/fixtures/dispatch-session-handoff.md"

    def test_valid_case_passes(self) -> None:
        case = {
            "scenario_file": self._EXISTING_SCENARIO_FILE,
            "rule_anchor": "some-anchor",
            "judge_rubric": "some rubric",
        }
        _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)  # must not raise

    def test_missing_scenario_file_key_raises(self) -> None:
        case = {"rule_anchor": "some-anchor", "judge_rubric": "some rubric"}
        with pytest.raises(AssertionError, match="scenario_file"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)

    def test_nonexistent_scenario_file_raises(self) -> None:
        case = {
            "scenario_file": "evals/fixtures/does-not-exist-xyz.md",
            "rule_anchor": "some-anchor",
            "judge_rubric": "some rubric",
        }
        with pytest.raises(AssertionError, match="does not exist"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)

    def test_empty_rule_anchor_raises(self) -> None:
        case = {"scenario_file": self._EXISTING_SCENARIO_FILE, "rule_anchor": "", "judge_rubric": "some rubric"}
        with pytest.raises(AssertionError, match="rule_anchor"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)

    def test_missing_judge_rubric_key_raises(self) -> None:
        case = {"scenario_file": self._EXISTING_SCENARIO_FILE, "rule_anchor": "some-anchor"}
        with pytest.raises(AssertionError, match="judge_rubric"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)


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


# Durable-handoff-location: /handoff and /brief write to a durable
# ~/.claude/ directory, not /tmp (lost on reboot). Both skills legitimately
# mention /tmp elsewhere for the consumed-tier destination (the temp path
# resume-context.sh moves the file to), so these tests anchor to the
# write-target construct — frontmatter description + the body "Write ... at"
# line — rather than a blanket "/tmp" presence/absence scan, which would
# false-positive on that correct content.
_DURABLE_WRITE_TARGETS = {
    "handoff": ("~/.claude/handoffs/", "-handoff.md"),
    "brief": ("~/.claude/briefs/", "-task.md"),
}


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_write_target_matches_durable_path(skill_name: str) -> None:
    """Anchored to the frontmatter description only — the one place this
    path is documented but never executed (the skill picker never runs the
    write recipe). The execution test below covers the literal recipe
    behavior; a text-match against the body's opening paragraph would be
    redundant with it and brittle to copy-only rewording (an SDET review
    round flagged this)."""
    skill_path = _skill_file(skill_name)
    directory, suffix = _DURABLE_WRITE_TARGETS[skill_name]
    frontmatter = parse_frontmatter(skill_path)
    description = frontmatter.get("description", "") or ""
    assert directory in description and suffix in description, (
        f"{skill_name}/SKILL.md frontmatter description does not name the durable "
        f"write target {directory}<slug>{suffix}"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_write_recipe_executes_to_durable_path(
    skill_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execution test, not just a text-match: runs the skill's own
    HOOK_TEST_FIXTURE recipe (mkdir -p) in an isolated $HOME and asserts the
    directory actually lands at the literal expected path — this is the
    real, literal instruction the skill always issues (not synthetic
    placeholder scaffolding). Owner-only protection for the directory comes
    from `~/.claude` being `700`, set once by `install.sh` — not from
    anything this recipe does. This test's isolated $HOME never runs
    install.sh, so the created directory's mode is whatever the ambient
    umask leaves it at; asserting a mode here would test the umask, not the
    recipe.
    """
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    skill_path = _skill_file(skill_name)
    command = extract_skill_command(skill_path, "write-target")
    run_skill_command(command, cwd=tmp_path, isolated_home=isolated_home)

    directory, _suffix = _DURABLE_WRITE_TARGETS[skill_name]
    expected_dir = isolated_home / directory.replace("~/", "")
    assert expected_dir.is_dir(), (
        f"{skill_name}/SKILL.md's write-target fixture did not create {expected_dir}"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_write_recipe_honors_config_dir_when_set(
    skill_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write half of the round trip that
    test_consumes_handoff_under_config_dir_when_set covers on the read side:
    with CLAUDE_CONFIG_DIR set, the write-target recipe must create the
    durable directory under it, not under $HOME/.claude."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    skill_path = _skill_file(skill_name)
    command = extract_skill_command(skill_path, "write-target")
    run_skill_command(command, cwd=tmp_path, isolated_home=isolated_home)

    directory, _suffix = _DURABLE_WRITE_TARGETS[skill_name]
    expected_dir = config_dir / directory.replace("~/.claude/", "")
    assert expected_dir.is_dir(), (
        f"{skill_name}/SKILL.md's write-target fixture did not honor "
        f"CLAUDE_CONFIG_DIR — expected {expected_dir}"
    )


@pytest.mark.parametrize(
    "script",
    sorted(p for p in SCRIPTS_DIR.glob("*.sh") if not p.name.startswith("_")),
    ids=lambda p: p.name,
)
def test_scripts_are_executable(script: Path) -> None:
    """Every script under claude/.claude/scripts/ is invoked by a hardcoded
    literal path (e.g. ~/.claude/scripts/foo.sh), never `bash <path>` — a
    script committed without the executable bit fails outright on first use
    for every stow consumer simultaneously. Generalized from a single-script
    check (resume-context.sh) so a newly-added script gets this coverage for
    free instead of needing its own copy of the same assertion. A leading
    underscore names a sourced library (e.g. _worktree-lib.sh), never invoked
    directly, so it carries no executable-bit expectation."""
    assert os.access(script, os.X_OK), (
        f"{script.name} must be committed with the executable bit set "
        "(git add --chmod=+x) so the stow symlink is runnable"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_reference_resume_context_literally(skill_name: str) -> None:
    """Doc-consistency check only, explicitly scoped as such (parallel to
    test_handoff_and_brief_write_target_matches_durable_path above) — proves
    the skill body names a literal resume-context invocation, not that the
    invocation actually works end-to-end (test_resume_context.py and the
    hook tests cover that). Kept separate from the executable-bit check
    above so a benign copy-edit to this prose can't fail that invariant, and
    vice versa (an SDET review round flagged the two being combined)."""
    body = _skill_file(skill_name).read_text()
    assert "resume-context <config-dir>/" in body, (
        f"{skill_name}/SKILL.md must give a literal resume-context invocation, "
        "not just mention the name in passing"
    )


# GH-474: consume-durable-continuity-file-on-read.sh moves a handoff/brief
# file the moment it's Read, even from the authoring session mid-draft. Both
# skills warn against re-Read-to-verify with the same fragment, deliberately
# duplicated (this repo forbids shared partials across skills) — this test is
# the only thing that can catch the two copies drifting apart, since two
# independently-worded per-file assertions would each keep passing right up
# to the point the wording diverges. Subject-first so a rewrite to "does not
# consume" breaks the match; stops short of the tool list so cat/grep/sed can
# still change; whitespace-normalized on both sides as forward-looking
# tolerance for a reflow that moves the fragment across a line break, not
# because it currently straddles one.
_READ_CONSUMES_FILE_PIN = "consumes the file — verify with a Bash"


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_warn_against_read_to_verify(skill_name: str) -> None:
    body = " ".join(_skill_file(skill_name).read_text().split())
    pin = " ".join(_READ_CONSUMES_FILE_PIN.split())
    assert pin in body, (
        f"{skill_name}/SKILL.md must warn that a Read of its continuity file "
        f"consumes it — expected the shared fragment {_READ_CONSUMES_FILE_PIN!r}"
    )


@pytest.mark.parametrize("skill_name", sorted(_DURABLE_WRITE_TARGETS))
def test_handoff_and_brief_reference_find_consumed_continuity_file(skill_name: str) -> None:
    """Content-consistency check only, not a behavioral test (test_resume_context.py
    and test_find_consumed_continuity_file.py cover the script's actual
    behavior) — the only guard previously named for the "does not name where
    the file went" clause was the line-count ceiling, which says nothing
    about whether the replacement prose itself references the new lookup
    script."""
    body = _skill_file(skill_name).read_text()
    assert "find-consumed-continuity-file" in body, (
        f"{skill_name}/SKILL.md must reference find-consumed-continuity-file "
        "as the recovery path for an already-consumed continuity file"
    )


# --- Citation placement: URLs live in REFERENCES.md, not in a SKILL.md body ---
#
# A SKILL.md body loads into the session on every skill fire; REFERENCES.md never
# does. A citation URL in a body is therefore re-read on every fire by a reader
# that cannot click it. URLs that are *functional* (a namespace URI a parser
# needs, an attack payload, a placeholder) are a different thing entirely, and in
# this corpus they are always inside a fenced code block or an inline code span —
# so stripping code regions first separates the two classes with no allowlist.

_URL_RE = re.compile(r"https?://")


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


def test_skill_bodies_carry_no_citation_urls() -> None:
    """No SKILL.md body may contain a URL outside a code fence or code span.

    Reports every violation at once rather than failing on the first, so a
    contributor fixes the whole set in one pass.

    Enforcement point: this is a pytest/CI check only, NOT a commit gate. The
    sibling SKILL.md rules in validate_skill_structure.py are enforced at commit
    time by require-skill-review.sh, and this one deliberately is not: that
    validator ships to downstream repos inside the skill-management plugin, so
    putting a claude-config-internal placement convention there would impose it
    on every consumer that installed the plugin for frontmatter validation. The
    tradeoff is that a leaked URL is caught by CI rather than at `git commit`.

    Two CommonMark forms are deliberately not handled: 4-space-indented code
    blocks, and inline code spans that cross a newline. No SKILL.md in this repo
    uses either form to hold a URL, before or after this convention was applied,
    so the machinery would be untested coverage for a shape that does not occur.
    Both gaps err toward over-flagging — the block reads as prose and the test
    fails with a line number pointing straight at it, whose fix is to fence the
    block rather than to widen the parser. Every deliberate gap here is biased
    that direction; see _closed_fence_line_indices for why under-flagging is
    treated as the unacceptable failure mode.
    """
    repo_root = REPO_ROOT
    skill_files = _all_skill_md_files()

    violations: list[str] = []
    for path in skill_files:
        for lineno in _prose_url_line_numbers(path.read_text()):
            violations.append(f"  {path.relative_to(repo_root)}:{lineno}")

    assert not violations, (
        "SKILL.md bodies must not carry citation URLs — a body is re-read on "
        "every skill fire by a reader that cannot follow a link. Move the URL "
        "(and any verbatim source quote) to a REFERENCES.md beside the SKILL.md, "
        "keeping any rule-generalizing rationale in the body. A bare authority "
        "name may stay where the claim is contestable. If the URL is functional "
        "rather than a citation, put it in a code fence or code span.\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("markdown", "url_survives"),
    [
        pytest.param("```\nhttps://example.test\n```\n", False, id="fence-backtick"),
        pytest.param("~~~\nhttps://example.test\n~~~\n", False, id="fence-tilde"),
        # The only fence shape actually present in this corpus. A helper matching
        # on "the opening line is exactly the delimiter" passes every other case
        # here and fails this one.
        pytest.param(
            '```python\nurl = "https://example.test"\n```\n', False, id="fence-info-string"
        ),
        pytest.param("Payload: `https://evil.test/x`\n", False, id="span-single-backtick"),
        pytest.param("Payload: ``https://evil.test/`x``\n", False, id="span-multi-backtick"),
        pytest.param("See https://example.test for details.\n", True, id="bare-url-in-prose"),
        pytest.param("See [the docs](https://example.test).\n", True, id="markdown-link"),
        # The three cases below pin under-flagging bugs — the failure direction
        # that silently exempts a real citation from the scan. Each one returned
        # False (URL swallowed) before the delimiter-run lookarounds and the
        # closed-fence requirement were added.
        pytest.param(
            "````see https://leaked.test``` end\n", True, id="span-mismatched-run-lengths"
        ),
        pytest.param(
            "````\nhttps://in-fence.test\n```\nstill fenced\n````\n",
            False,
            id="fence-longer-opener-ignores-shorter-closer",
        ),
        pytest.param(
            "```python\nx = 1\n\nUnclosed, so this is prose: https://leaked.test\n",
            True,
            id="fence-unterminated-falls-back-to-prose",
        ),
    ],
)
def test_blank_code_regions_distinguishes_functional_urls_from_citations(
    markdown: str, url_survives: bool
) -> None:
    """The helper must bite on both strip paths, not just the fenced one.

    A corpus that happens to pass proves nothing about the algorithm; these cases
    are what prove it. The span cases specifically guard `review-permissions`,
    whose attack payload is a URL inside an inline code span.
    """
    assert bool(_URL_RE.search(_blank_code_regions(markdown))) is url_survives


def test_blank_code_regions_handles_all_three_forms_in_one_document() -> None:
    """A fence, a span, and a real leaked citation in one file.

    This is the actual shape of ai-instruction-and-memory-files/SKILL.md. A
    two-pass bug — fence-stripping running past its own closing delimiter and
    swallowing a later line — would pass every single-form case above and only
    surface here. Also pins line-number preservation, which the corpus scan's
    file:line reporting depends on.
    """
    document = (
        "# Heading\n"  # 1
        "```python\n"  # 2
        'u = "https://in-fence.test"\n'  # 3
        "```\n"  # 4
        "Span: `https://in-span.test`\n"  # 5
        "Leak: [docs](https://leaked.test)\n"  # 6
    )
    prose_lines = _blank_code_regions(document).split("\n")

    flagged = [i for i, line in enumerate(prose_lines, start=1) if _URL_RE.search(line)]
    assert flagged == [6], f"expected only the markdown link on line 6 to survive, got {flagged}"


def test_blank_frontmatter_excludes_metadata_from_the_scan() -> None:
    """Frontmatter is metadata, not body prose — excluded so the rule is exact."""
    document = "---\nname: demo\nhomepage: https://example.test\n---\n\nBody prose.\n"
    assert not _URL_RE.search(_blank_frontmatter(document))
    assert "Body prose." in _blank_frontmatter(document)


# REFERENCES.md is the edit-time reference and is never loaded at runtime, so it
# is the one auxiliary name allowed to carry citation URLs.
# The glob covers claude-skills/skills/ only. Extend it if a runtime auxiliary
# file ever lands under plugins/ or .claude/skills/.
_RUNTIME_READ_AUXILIARY_NAMES = tuple(name for name in SKILL_AUXILIARY_MD_NAMES if name != "REFERENCES.md")
_RUNTIME_READ_AUXILIARY_FILES = sorted(
    path for name in _RUNTIME_READ_AUXILIARY_NAMES for path in SKILLS_DIR.glob(f"*/{name}")
)


def _prose_url_line_numbers(markdown_text: str) -> list[int]:
    """1-based line numbers holding a URL outside frontmatter and code regions."""
    prose = _blank_code_regions(_blank_frontmatter(markdown_text))
    return [lineno for lineno, line in enumerate(prose.split("\n"), start=1) if _URL_RE.search(line)]


@pytest.mark.parametrize("auxiliary_name", _RUNTIME_READ_AUXILIARY_NAMES)
def test_every_runtime_read_auxiliary_name_matches_a_skill_file(auxiliary_name: str) -> None:
    """A registered name with no file on disk would make the URL scan below
    pass vacuously for that name."""
    assert any(path.name == auxiliary_name for path in _RUNTIME_READ_AUXILIARY_FILES), (
        f"no skill directory holds a {auxiliary_name}; drop it from SKILL_AUXILIARY_MD_NAMES or add the file."
    )


@pytest.mark.parametrize(
    "auxiliary_path",
    _RUNTIME_READ_AUXILIARY_FILES,
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_runtime_read_auxiliary_files_carry_no_citation_urls(auxiliary_path: Path) -> None:
    """A runtime-read auxiliary file is re-read on every run that reaches it, by
    a reader that cannot follow a link, so it obeys the same URL rule as a
    SKILL.md body. Citation URLs belong in the skill's REFERENCES.md."""
    assert _prose_url_line_numbers(auxiliary_path.read_text()) == [], (
        f"{auxiliary_path.relative_to(REPO_ROOT)} carries a URL outside a code region; "
        "move it to the skill's REFERENCES.md."
    )


def test_prose_url_detector_flags_a_leaked_citation_and_spares_code() -> None:
    """Deny fixture: the runtime-auxiliary URL check must fire on prose URLs."""
    document = (
        "# Heading\n"  # 1
        "Cited at https://leaked.test in prose.\n"  # 2
        "Payload `https://in-span.test` only.\n"  # 3
        "```\nhttps://in-fence.test\n```\n"  # 4-6
        "[docs](https://leaked-link.test)\n"  # 7
    )
    assert _prose_url_line_numbers(document) == [2, 7]


def _citation_sources_for_skill_md(skill_md_path: Path) -> list[Path]:
    """A SKILL.md plus its co-located auxiliary siblings (SKILL_AUXILIARY_MD_NAMES), if present."""
    sources = [skill_md_path]
    for sibling_name in SKILL_AUXILIARY_MD_NAMES:
        sibling = skill_md_path.parent / sibling_name
        if sibling.exists():
            sources.append(sibling)
    return sources


def _citation_report(skill_md_paths: Iterable[Path], *, repo_root: Path) -> list[str]:
    """Glob (expand each SKILL.md into itself plus its auxiliary siblings) →
    extract → resolve → aggregate → report, for a given set of SKILL.md
    paths. Factored out from test_skill_citations_resolve_to_real_headings so
    a synthetic-corpus test can drive the exact same pipeline without
    depending on the real repo tree.
    """
    violations: list[str] = []
    for skill_md_path in skill_md_paths:
        for source_path in _citation_sources_for_skill_md(skill_md_path):
            text = source_path.read_text()
            relative_source = source_path.relative_to(repo_root)
            for citation in _extract_citations(text):
                resolved = _resolve_citation_target(
                    citation.target, citing_file=source_path, repo_root=repo_root
                )
                if resolved is None:
                    violations.append(
                        f"{relative_source}:{citation.line} -> "
                        f"unresolvable target {citation.target!r}"
                    )
                    continue
                if normalize_heading(citation.heading) not in heading_texts(resolved.read_text()):
                    violations.append(
                        f"{relative_source}:{citation.line} -> target "
                        f"{citation.target!r} resolved to "
                        f"{resolved.relative_to(repo_root)} but it has no heading "
                        f"matching {citation.heading!r}"
                    )
    return violations


def test_skill_citations_resolve_to_real_headings() -> None:
    """Every `` `target` § "Heading" `` citation in the skill tree resolves to
    a real file and an exact heading in it.

    Scanned corpus: every SKILL.md (`_all_skill_md_files`) plus every
    auxiliary sibling (SKILL_AUXILIARY_MD_NAMES) in those same skill
    directories — widened past SKILL.md alone because a stale citation this
    test guards against lives in review-permissions/REFERENCES.md.

    Reports every violation at once rather than failing on the first, so a
    contributor fixes the whole set in one pass — same convention as
    test_skill_bodies_carry_no_citation_urls above.
    """
    repo_root = REPO_ROOT
    violations = _citation_report(_all_skill_md_files(), repo_root=repo_root)

    assert not violations, (
        "Skill-tree citations (`target` § \"Heading\") must resolve to a real "
        "file and an exact heading in it — a rename on either side leaves a "
        "stale pointer with no reader-visible symptom.\n"
        + "\n".join(f"  {violation}" for violation in violations)
    )


def test_handoff_nudge_doc_cites_handoff_warrant_check_section() -> None:
    """docs/handoff-nudge.md's cross-reference to handoff/SKILL.md's
    warrant-check section resolves to a real heading there.

    `docs/*.md` sits outside `_all_skill_md_files`'s scanned corpus
    (SKILL.md plus its SKILL_AUXILIARY_MD_NAMES siblings only), so
    test_skill_citations_resolve_to_real_headings never sees this citation —
    targeted narrowly here instead of widening that corpus.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "handoff-nudge.md",
        "handoff/SKILL.md",
        "Before writing: is a handoff warranted?",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "CLAUDE.md",
        "docs/cost-levers-considered.md",
        "docs/design-decisions/schedulewakeup-misapplied-documented.md",
        "docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md",
        "docs/transcript-analysis.md",
    ],
)
def test_tooling_measurement_citation_resolves_to_real_heading(
    relative_path: str,
) -> None:
    """Each sibling withholding site's `docs/private-project-redaction.md`
    § "Publishing a tooling measurement" citation resolves to a real
    heading there.

    `docs/*.md` sits outside `_all_skill_md_files`'s scanned corpus (SKILL.md
    plus its SKILL_AUXILIARY_MD_NAMES siblings only), so
    test_skill_citations_resolve_to_real_headings never sees these citations
    — targeted narrowly here instead of widening that corpus.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / relative_path,
        "docs/private-project-redaction.md",
        "Publishing a tooling measurement",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "CLAUDE.md",
        "plugins/claude-hook-review/skills/claude-hook-review/SKILL.md",
        ".claude/skills/code-review-claude-config/SKILL.md",
        ".claude/skills/plan-review-claude-config/SKILL.md",
    ],
)
def test_threat_model_tiers_citation_resolves_to_real_heading(relative_path: str) -> None:
    """CLAUDE.md's '## Hook threat model' section, claude-hook-review's SKILL.md
    tier-header paragraph, and the claude-config code-review and plan-review
    layers each cite `docs/hooks.md` § "Threat-model tiers" — resolves to a
    real heading there.

    The layer and plugin entries pin that the citation is still present, since
    the repo-wide citation scan only checks that existing citations resolve.
    CLAUDE.md is outside that scan, so this test checks resolution for it.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / relative_path,
        "docs/hooks.md",
        "Threat-model tiers",
        repo_root=REPO_ROOT,
    )


# Tier-disposition clauses that keep the review layers fail-closed.
# The `hooks-doc-regression-rule` entry pins the regression-judging block in its single home.
# The `hooks-doc-debt-admission` entry pins the existing-debt admission rule, the disclosure-gate pointer to it, and the lead.
# The code-review-layer phrases for the DEFER ban, the ask and the Known-gaps recording pin the ask-and-record path.
# The layer entries pin the per-vector-gap versus genuinely-lax question.
# The layer entries pin that a regression stays blocking at every tier.
# The layer entries pin that a finding the tier section does not explicitly waive or route stays under the base rules.
# A tripwire for a wording trim that silently drops one of them.
_TIER_DISPOSITION_SECTIONS = [
    pytest.param(
        ".claude/skills/code-review-claude-config/SKILL.md",
        "## Finding disposition addition",
        [
            "per-vector gap",
            "genuinely-lax failure stays under the base rules",
            "lists `untrusted-input` stays under the base rules",
            "is an enforcement-invariant finding",
            "is not an enforcement-invariant finding",
            "at every tier",
            "does not explicitly waive or route stays under the base rules.",
            "No recording",
            "DEFERred under criterion 3 as a waived or routed finding against a gate",
            "that is not a regression is never DEFERred",
            "admits it as permanent existing debt",
            "takes a blocking stop-and-ask",
            "the engineer's own SETTLED row kept with `--carry-forward`",
            "Before the engineer answers, that stop also gives the base contradiction-route "
            "rule's human-keep disclosures.",
        ],
        id="code-review-layer",
    ),
    pytest.param(
        ".claude/skills/plan-review-claude-config/SKILL.md",
        "## Gate threat-model tiers (Domain: Security; Output format)",
        [
            "per-vector gap",
            "genuinely-lax failure stays under the base rules",
            "lists `untrusted-input` stays under the base rules",
            "stays an enforcement-invariant finding",
            "is not an enforcement-invariant finding",
            "at every tier",
            "does not explicitly waive or route stays under the base rules.",
            "No recording",
            "cover every regression",
        ],
        id="plan-review-layer",
    ),
    pytest.param(
        "docs/hooks.md",
        "## Threat-model tiers",
        [
            "A gate the change adds has no merge-base denials to regress from, "
            "so no finding that it dropped a denial is a regression; its tier decides. "
            "A gate that renames or replaces merge-base gates, wholly or in part, "
            "is not one the change adds; neither is a gate whose status is unclear. "
            "Compare such a gate against the merge-base behavior it takes over. "
            "For any gate that is not strictly added by the change, including a "
            "replacement or a gate of unclear status, treat the finding as a "
            "regression when any of these holds: "
            "- The comparison against the merge-base is unclear. "
            "- A fail-open path the change adds or alters has no deny test. "
            "- The change edits the gate's tier line, its tracking pointer, or this "
            "section's rules above its per-hook classification table. "
            "A `CLAUDE.md` or SKILL.md that applies this rule points here rather than "
            "restating it; this section is its only home.",
        ],
        id="hooks-doc-regression-rule",
    ),
    pytest.param(
        "docs/hooks.md",
        "## Threat-model tiers",
        [
            "Except for a gap that needs a shape a cooperative agent would never emit, "
            "a gap discovered later counts as recorded this way only once the human's answer "
            "to a blocking stop-and-ask accepts that gap as permanent debt, and the commit "
            "message or PR body that records it states the rationale.",
            # The predicate must be "does emit": a broader modal reading would override the
            # never-emit recordings the section deliberately allows.
            "Until then no text or pointer records as debt a gap discovered later that needs "
            "a shape a cooperative agent does emit, wherever it sits, even where another "
            "sentence of this section treats it as debt or routes it without a blocking finding.",
            "A routine encoding a cooperative agent emits, such as a base64 credential in a "
            "Kubernetes Secret manifest, is not a waivable shape, so the **Existing debt** rule "
            "above decides whether a finding on it may be recorded.",
            "**Existing debt** is a closed set",
        ],
        id="hooks-doc-debt-admission",
    ),
]


@pytest.mark.parametrize(
    ("relative_path", "section_heading", "required_phrases"),
    _TIER_DISPOSITION_SECTIONS,
)
def test_gate_tier_disposition_sections_keep_invariant_clauses(
    relative_path: str,
    section_heading: str,
    required_phrases: list[str],
) -> None:
    """Each entry's section, whitespace-normalized, still carries its clauses.

    Scoped to the section so the same words elsewhere in the file cannot
    satisfy it. Whitespace-normalized so a re-wrap does not break it. Pins
    that the text is present, not that a reviewer follows it.
    """
    section_file_path = REPO_ROOT / relative_path
    lines = section_file_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, section_heading, section_file_path)
    section_text = " ".join("".join(lines[start_idx:end_idx]).split())

    missing_phrases = [phrase for phrase in required_phrases if phrase not in section_text]
    assert not missing_phrases, (
        f"{section_file_path}'s {section_heading!r} section no longer carries "
        f"{missing_phrases}; each keeps the tier-disposition rule fail-closed. "
        "Restore the clause, or update this pin and say why in the commit message."
    )


# A single-backtick span made of letters, hyphens, and underscores only — no
# whitespace, colon, or other punctuation inside — the shape a bare tier
# token takes, so a near-miss spelling (`untrusted_input`, `Irreversible`)
# is extracted too. Excludes a multi-word span like
# `` `# tier-threat-model: <tiers>` `` or `` `hook-class: gate` ``, each of
# which contains a space or a colon and so can't satisfy this pattern for
# its full backtick-delimited content.
_TIER_TOKEN_SHAPED_BACKTICK_SPAN_RE = re.compile(r"`([A-Za-z][A-Za-z_-]*)`")

_TIER_DEFINITION_BULLET_RE = re.compile(r"^- `([a-z][a-z-]*)` — ", re.MULTILINE)


def _tier_token_shaped_backtick_spans(paragraph: str) -> set[str]:
    """Every distinct letters/hyphens/underscores-only single-backtick span in `paragraph`."""
    return set(_TIER_TOKEN_SHAPED_BACKTICK_SPAN_RE.findall(paragraph))


def _hooks_md_tier_vocabulary() -> set[str]:
    """The tier names defined by docs/hooks.md's '## Threat-model tiers'
    definition bullets — the authoritative vocabulary."""
    doc_text = (REPO_ROOT / "docs" / "hooks.md").read_text()
    section = re.search(
        r"^## Threat-model tiers\s*\n(.*?)(?=^### |^## |\Z)",
        doc_text,
        re.MULTILINE | re.DOTALL,
    )
    assert section, "docs/hooks.md has no '## Threat-model tiers' section"
    return set(_TIER_DEFINITION_BULLET_RE.findall(section.group(1)))


def _claude_hook_review_tier_paragraph() -> str:
    """The tier-header paragraph in claude-hook-review's SKILL.md (through
    the next blank line), isolated from the rest of the file so the
    backtick-span scan below doesn't also sweep unrelated code spans
    elsewhere in the document (hook names, `_lib.sh`, `hook-class` values,
    etc.)."""
    skill_md_text = _skill_file("claude-hook-review").read_text()
    match = re.search(
        r"^In a repo that adopts tier headers.*?(?=\n\s*\n|\Z)",
        skill_md_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "claude-hook-review/SKILL.md's tier-header paragraph not found"
    return match.group(0)


def test_claude_hook_review_tier_paragraph_backtick_tokens_equal_hooks_md_tiers() -> None:
    """The tier-token-shaped backtick spans in claude-hook-review SKILL.md's
    tier-header paragraph equal the tier set docs/hooks.md defines, so a
    misspelled, stale, or dropped tier there fails.
    """
    vocabulary = _hooks_md_tier_vocabulary()
    assert vocabulary, "docs/hooks.md's tier definition bullets were not found"
    spans = _tier_token_shaped_backtick_spans(_claude_hook_review_tier_paragraph())
    assert spans == vocabulary, (
        f"claude-hook-review/SKILL.md's tier-header paragraph backtick spans "
        f"{sorted(spans)} differ from docs/hooks.md's tiers {sorted(vocabulary)}"
    )


def test_tier_token_shaped_backtick_spans_ignores_multiword_and_finds_near_misses() -> None:
    """Meta-test for _tier_token_shaped_backtick_spans: a hyphenated
    non-tier span, an underscore spelling, and a capitalized spelling are
    all extracted (so equality against the vocabulary rejects them), while
    a multi-word span (`` `# tier-threat-model: <tiers>` ``) stays
    unextracted.
    """
    decoy_paragraph = (
        "A decoy naming `hook-class`, `untrusted_input`, and `Irreversible`, "
        "plus the real header shape `# tier-threat-model: <tiers>`, which "
        "must not be extracted as a bare span."
    )
    assert _tier_token_shaped_backtick_spans(decoy_paragraph) == {
        "hook-class",
        "untrusted_input",
        "Irreversible",
    }


def test_cost_trend_share_only_citation_resolves_to_real_heading() -> None:
    """docs/transcript-analysis.md's `cost-trend`/`--share-only` exclusion
    cites `docs/private-project-redaction.md` § "A wider corpus goes to
    the owner, never into a public artifact" for why a calendar-anchored
    series is barred outright — resolves to a real heading there.

    A prior wording of this same citation named the wrong section ("What it
    permits", a requirement `cost-trend` already satisfies) and shipped
    without any test catching it — this test exists so a future rename or
    mis-citation on this line fails here instead of requiring manual review.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "transcript-analysis.md",
        "docs/private-project-redaction.md",
        "A wider corpus goes to the owner, never into a public artifact",
        repo_root=REPO_ROOT,
    )


def test_owner_authorized_figure_citation_resolves_to_real_heading() -> None:
    """docs/cost-levers-considered.md's sleep-poll follow-up entry cites
    `docs/private-project-redaction.md` § "The owner can authorize one
    figure, case by case" for its publication authorization — resolves to
    a real heading there.

    This is the one citation in that entry carrying evidentiary weight for
    a security-relevant control (publication authorization). Per this
    repo's citation-grammar rule, a future heading rename or restructure
    would otherwise silently orphan it with no reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "cost-levers-considered.md",
        "docs/private-project-redaction.md",
        "The owner can authorize one figure, case by case",
        repo_root=REPO_ROOT,
    )


def test_pr_cost_export_aggregate_citation_resolves_to_real_heading() -> None:
    """docs/pr-cost.md cites `docs/private-project-redaction.md` § "The
    owner can authorize one figure, case by case" somewhere — resolves to a
    real heading there.

    A heading rename would silently orphan the pointer to the per-figure
    authorization gate, and docs/*.md sits outside the generic skill-citation
    sweep.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "pr-cost.md",
        "docs/private-project-redaction.md",
        "The owner can authorize one figure, case by case",
        repo_root=REPO_ROOT,
    )


def test_review_round_cost_citation_resolves_to_real_heading() -> None:
    """docs/private-project-redaction.md points to
    `docs/transcript-analysis.md` § "review-round-cost" for
    `review-round-cost --pooled`'s refusal list, withholding floors, and
    sample output.

    This test checks that the heading exists there.

    The redaction doc defers to that section instead of restating the
    contract, so a heading rename there would otherwise orphan the pointer
    with no reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "private-project-redaction.md",
        "docs/transcript-analysis.md",
        "review-round-cost",
        repo_root=REPO_ROOT,
    )


def test_routing_gate_design_record_cites_routing_guard_section() -> None:
    """The routing-gate design record points to
    `docs/transcript-analysis.md` § "routing-guard" for how to run the
    subcommand.

    The record defers to that section instead of restating how to run it,
    and docs/*.md sits outside the generic skill-citation sweep, so
    a heading rename would otherwise orphan the pointer with no
    reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "design-decisions" / "review-orchestrator-routing-gate.md",
        "docs/transcript-analysis.md",
        "routing-guard",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "pr_cost_heading",
    ["Data", "Residual replication paths the git-tree check doesn't close"],
)
def test_transcript_analysis_doc_cites_real_pr_cost_headings(pr_cost_heading: str) -> None:
    """docs/transcript-analysis.md cites `docs/pr-cost.md` § "Data" and
    § "Residual replication paths the git-tree check doesn't close" somewhere
    — each resolves to a real heading there.

    The doc defers to pr-cost's ledger rules instead of restating them, and
    docs/*.md sits outside the generic skill-citation sweep, so a heading
    rename in docs/pr-cost.md would otherwise orphan the pointer with no
    reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "transcript-analysis.md",
        "docs/pr-cost.md",
        pr_cost_heading,
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    ("target", "heading"),
    [
        ("docs/session-config-keys-log.md", "State at a transcript record"),
        ("docs/session-config-keys-log.md", "Coverage"),
        ("README.md", "Updating after git pull"),
    ],
)
def test_routing_guard_section_cites_real_key_state_headings(target: str, heading: str) -> None:
    """docs/transcript-analysis.md's `routing-guard` section cites the session
    config-keys log's reader rules and `README.md` § "Updating after git pull"
    for its key-state rules and next steps — each resolves to a real heading.

    The section defers to the reader doc instead of restating its rules, and
    docs/*.md sits outside the generic skill-citation sweep, so a heading
    rename there would otherwise orphan the pointer with no reader-visible
    symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "transcript-analysis.md",
        target,
        heading,
        repo_root=REPO_ROOT,
    )


_SHOW_WITHHELD_BARRED_LIST_FILES = (
    ".claude/skills/code-review-claude-config/SKILL.md",
    "docs/private-project-redaction.md",
)
_LIST_ITEM_START_RE = re.compile(r"^\s*[-*]\s")


def _list_items(markdown_text: str) -> list[str]:
    """Each markdown list item with its hard-wrapped continuation lines joined.

    A continuation line is a non-blank line indented under the item. A blank
    line or the next list item ends the item.
    """
    items: list[str] = []
    current_item_lines: list[str] | None = None
    for line in markdown_text.splitlines():
        if _LIST_ITEM_START_RE.match(line):
            if current_item_lines is not None:
                items.append(" ".join(current_item_lines))
            current_item_lines = [line.strip()]
        elif current_item_lines is not None and line.strip() and line[0].isspace():
            current_item_lines.append(line.strip())
        else:
            if current_item_lines is not None:
                items.append(" ".join(current_item_lines))
            current_item_lines = None
    if current_item_lines is not None:
        items.append(" ".join(current_item_lines))
    return items


def test_list_items_ends_each_item_at_the_next_item_a_blank_line_or_a_dedent() -> None:
    """`_list_items` bounds each item, so a word checked inside one item cannot
    be satisfied by a neighbouring item or by prose after the list.

    The text has two adjacent items, one item ended by a blank line, and one
    item ended by an unindented line.
    """
    markdown_text = (
        "- first item\n"
        "  wrapped continuation\n"
        "- second item\n"
        "\n"
        "  indented text after a blank line\n"
        "- third item\n"
        "unindented prose after the list\n"
    )

    assert _list_items(markdown_text) == [
        "- first item wrapped continuation",
        "- second item",
        "- third item",
    ]


@pytest.mark.parametrize("relative_path", _SHOW_WITHHELD_BARRED_LIST_FILES)
def test_show_withheld_output_stays_in_a_barred_list_item(relative_path: str) -> None:
    """Each barred-output list still has one item that names `--show-withheld`
    and says the output is barred.

    Pins the presence of the bar, not its wording or its authorization
    language. The word "barred" is checked inside that one item only, since
    each file uses it elsewhere, and deleting the item must fail this test.
    """
    items_naming_flag = [
        item for item in _list_items((REPO_ROOT / relative_path).read_text()) if "--show-withheld" in item
    ]
    assert len(items_naming_flag) == 1, (
        f"{relative_path}: expected exactly one list item naming --show-withheld, "
        f"found {len(items_naming_flag)}"
    )
    assert re.search(r"\bbarred\b", items_naming_flag[0]), (
        f"{relative_path}: the list item naming --show-withheld no longer says the output is barred"
    )


_CASE_STUDY_POOLED_FIGURE_MARKERS_RE = re.compile(
    r"pooled across|machine-wide|cross-machine|multi-account", re.IGNORECASE
)

# Known false-negative: a file disclosing an unscoped review-trace/
# subagent-mix/fail-seq/struggle/duration/pr-link/buckets figure without any
# of the four marker phrases above is invisible to this regex.
# docs/case-studies/worktree-enforcement.md's "The general philosophy"
# section is a live example: it matches no marker here, and is grandfathered
# by name in Remediation instead.
# A precise detector would flag a scope-unguaranteed-subcommand citation
# with no adjacent --this-repo/--config-dir disclosure.
# That detector is not implemented here.

# Case studies published after the single-account bar took effect that use a
# marker phrase without publishing a pooled figure under the old carve-out —
# extend this set, never delete a prior entry, when
# test_case_study_pooled_figure_markers_are_registered_or_new below flags a
# new false positive.
_CASE_STUDY_POOLED_MARKER_ALLOWLIST: frozenset[str] = frozenset()


def test_case_study_pooled_figure_markers_are_registered_or_new() -> None:
    """Every docs/case-studies/*.md file using a pooled-figure marker phrase
    is named in docs/private-project-redaction.md's Remediation section, or
    is explicitly allowlisted as a new-since-this-bar file.

    Heuristic completeness check, not a mechanical citation-resolution test
    — it catches the shape of gap where a published case study matches the
    grandfathered set's own description (a machine-wide, multi-account
    corpus) but is missing from Remediation's enumerated list, breaking the
    residual-narrowing check that list feeds.
    """
    remediation_text = (REPO_ROOT / "docs" / "private-project-redaction.md").read_text()
    remediation_section = remediation_text[remediation_text.index("### Remediation") :]

    case_studies_dir = REPO_ROOT / "docs" / "case-studies"
    unregistered = [
        path.name
        for path in sorted(case_studies_dir.glob("*.md"))
        if path.stem not in _CASE_STUDY_POOLED_MARKER_ALLOWLIST
        and _CASE_STUDY_POOLED_FIGURE_MARKERS_RE.search(path.read_text())
        and path.name not in remediation_section
    ]

    assert not unregistered, (
        "docs/case-studies/*.md file(s) use a pooled-figure marker phrase "
        "(pooled across / machine-wide / cross-machine / multi-account) but "
        "are not named in docs/private-project-redaction.md's Remediation "
        "section. Add each to that list if it predates the single-account "
        "bar, or to _CASE_STUDY_POOLED_MARKER_ALLOWLIST above if it's a new, "
        "compliant file using the phrase incidentally:\n"
        + "\n".join(f"  {name}" for name in unregistered)
    )


# A citation-shaped construct `_extract_citations` misses is a silent miss,
# not an error (both extractor regexes require `\s+` before the quote; a
# no-space opener or a line-wrapped heading yields zero matches instead of a
# wrong one).
# This regex uses `\s*` (looser than the extractor's `\s+`) so a candidate
# can be checked against what the extractor actually returned, rather than
# re-implementing its notion of a citation.
# ASCII straight quote only, per this repo's plain-ASCII convention — widen
# to `["""]` if that changes.
_CITATION_CANDIDATE_RE = re.compile(r'§\s*"')


def _unextracted_citation_candidate_report(paths: Iterable[Path], *, repo_root: Path) -> list[str]:
    """Every `§\\s*"` construct in `paths` that `_extract_citations` did not
    recognize as a citation. Excludes frontmatter and closed fenced code
    blocks, matching `_extract_citations`'s own exclusions. A bare `§4`
    section reference is never a candidate — it isn't followed by a quote —
    so no allowlist is needed for that shape.
    """
    violations: list[str] = []
    for path in paths:
        text = path.read_text()
        prose = _blank_frontmatter(text)
        lines = prose.split("\n")
        fenced_lines = _closed_fence_line_indices(lines)
        citation_spans = [citation.span for citation in _extract_citations(text)]
        for match in _CITATION_CANDIDATE_RE.finditer(prose):
            lineno = prose.count("\n", 0, match.start())
            if lineno in fenced_lines:
                continue
            if any(start <= match.start() < end for start, end in citation_spans):
                continue
            violations.append(f"{path.relative_to(repo_root)}:{lineno + 1}")
    return violations


def test_every_citation_shaped_construct_is_extracted() -> None:
    """Asserts every `` `target` § "Heading" `` (or bare `§ "Heading"`)
    construct in the skill/doc corpus was recognized by `_extract_citations`.

    Catches a hard-wrapped or no-space citation that
    `_CITATION_WITH_TARGET_RE`/`_BARE_CITATION_RE` silently miss.

    Complements test_skill_citations_resolve_to_real_headings and
    test_tooling_measurement_citation_resolves_to_real_heading, which
    never see such a construct since it was never extracted as a citation in
    the first place.

    Scanned corpus:

    - `_citation_sources_for_skill_md`'s expansion of every
      `_all_skill_md_files()` entry.
    - Every path `_all_citation_extraction_doc_paths()` covers:
      `_all_doc_paths()`'s corpus, plus both CLAUDE.md files, plus every
      `.claude/rules/*.md`/`claude/.claude/rules/*.md` file.

    This checks extraction only — the `docs/rules-references.md`
    external-URL citation is intentionally never asserted to resolve here.
    """
    repo_root = REPO_ROOT
    skill_md_sources: list[Path] = []
    for skill_md_path in _all_skill_md_files():
        skill_md_sources.extend(_citation_sources_for_skill_md(skill_md_path))

    violations = _unextracted_citation_candidate_report(
        skill_md_sources + _all_citation_extraction_doc_paths(), repo_root=repo_root
    )
    assert not violations, (
        "Citation-shaped construct not recognized by "
        "`_CITATION_WITH_TARGET_RE`/`_BARE_CITATION_RE` — check for a missing "
        "space before the opening quote or a heading hard-wrapped across a "
        "line break:\n" + "\n".join(f"  {violation}" for violation in violations)
    )


@pytest.mark.parametrize(
    ("source_text", "expected_violation_count"),
    [
        pytest.param(
            '## Real Heading\n\nCitation: `sibling.md` §"No Space Heading"\n',
            1,
            id="no-space-opener-goes-unextracted",
        ),
        pytest.param(
            'Wrapped: `sibling.md` § "Hard\nWrapped Heading"\n',
            1,
            # The heading class `[^"\n]+` can't span the newline, so this
            # candidate matches neither `_CITATION_WITH_TARGET_RE` nor
            # `_BARE_CITATION_RE` — a hard-wrap is a distinct failure shape
            # from the no-space case above, caught by the same guard.
            id="hard-wrapped-heading-goes-unextracted",
        ),
        pytest.param(
            '## Real Heading\n\nCitation: `sibling.md` § "Well Formed Heading"\n',
            0,
            id="well-formed-citation-is-extracted",
        ),
        pytest.param(
            "See §4 for the full rule text, not a citation.\n",
            0,
            id="bare-section-number-is-not-a-candidate",
        ),
        pytest.param(
            '## Real Heading\n\n```\nFenced: `sibling.md` §"No Space Heading"\n```\n',
            0,
            id="candidate-inside-closed-fence-not-scanned",
        ),
    ],
)
def test_unextracted_citation_candidate_report_cases(
    tmp_path: Path, source_text: str, expected_violation_count: int
) -> None:
    """`_unextracted_citation_candidate_report` flags a `§ "` construct only
    when `_extract_citations` did not return a matching citation for it —
    not merely when the construct spans a line break."""
    doc_path = tmp_path / "example.md"
    doc_path.write_text(source_text)
    violations = _unextracted_citation_candidate_report([doc_path], repo_root=tmp_path)
    assert len(violations) == expected_violation_count, violations


def test_tooling_measurement_code_review_skill_citation_is_still_present() -> None:
    """Pins presence of this specific citation: no other test asserts
    that this SKILL.md still cites this heading at all, only that any
    citation it does carry resolves correctly. A re-wrap that breaks
    extraction is also caught by
    test_every_citation_shaped_construct_is_extracted; this test's own
    unique coverage is the citation being deleted or paraphrased away
    with no `§ "..."` construct left for either test to see.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / ".claude/skills/code-review-claude-config/SKILL.md",
        "docs/private-project-redaction.md",
        "Publishing a tooling measurement",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    ("raw_heading", "normalized"),
    [
        pytest.param("## Plain Heading", "Plain Heading", id="strips-leading-hashes"),
        pytest.param(
            "### Debug-investigation probe → `general-purpose` or `Explore`",
            "Debug-investigation probe → general-purpose or Explore",
            id="strips-backticks-mid-heading",
        ),
        pytest.param("## *Emphasized* heading", "Emphasized heading", id="strips-asterisk-emphasis"),
        pytest.param("## _Emphasized_ heading", "Emphasized heading", id="strips-underscore-emphasis"),
        pytest.param("##   Extra   spaces  ", "Extra spaces", id="collapses-whitespace-runs"),
    ],
)
def test_normalize_heading(raw_heading: str, normalized: str) -> None:
    """The real heading "Debug-investigation probe → `general-purpose` or
    `Explore`" in subagent-delegation/SKILL.md pins the mid-heading-backtick
    case; the rest are synthetic but exercise the same normalization
    independently."""
    assert normalize_heading(raw_heading) == normalized


def test_heading_texts_excludes_headings_inside_a_fenced_code_block() -> None:
    """A stale citation could otherwise coincidentally string-match a
    fenced shell comment or sample-output line and pass despite citing
    nothing real -- the exact regression this helper's fence tracking
    exists to prevent, in _citation_report's own resolution check."""
    markdown = (
        "# Real heading\n"
        "\n"
        "```bash\n"
        "# Not a heading, just a fenced comment\n"
        "```\n"
        "\n"
        "## Another real heading\n"
    )
    assert heading_texts(markdown) == {"Real heading", "Another real heading"}


def _write_skill_files(tmp_path: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        file_path = tmp_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)


@pytest.mark.parametrize(
    ("skill_files", "expected_violation_count"),
    [
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Real Heading\n\n"
                    "```\n"
                    'Fenced: `example-skill/SKILL.md` § "Nonexistent Heading"\n'
                    "```\n"
                ),
            },
            0,
            # Would be 1 if the closed-fence exclusion broke — the fenced
            # citation's heading does not exist.
            id="citation-inside-closed-fence-not-scanned",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Real Heading\n\n"
                    'Wrapped: `example-skill/SKILL.md`\n§ "Nonexistent Heading"\n'
                ),
            },
            1,
            # Would be 0 if \s+ didn't span the newline — the bad citation
            # would never be extracted at all.
            id="newline-wrapped-citation-is-matched",
        ),
        pytest.param(
            {"example-skill/SKILL.md": "## Real Heading\n\n" 'Bad: `does-not-exist.md` § "Whatever"\n'},
            1,
            id="unresolvable-target-fails",
        ),
        pytest.param(
            {"example-skill/SKILL.md": "## Real Heading\n\n" 'Bare: § "Real Heading"\n'},
            0,
            id="bare-citation-with-no-adjacent-target-resolves-same-file",
        ),
        pytest.param(
            {"example-skill/SKILL.md": "## Real Heading\n\n" 'Bare: § "Nonexistent Heading"\n'},
            1,
            id="bare-citation-same-file-missing-heading-fails",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Home\n\n" 'Citation: `sibling.md` § "Config: "quoted" value"\n'
                ),
                "example-skill/sibling.md": '## Config: "quoted" value\n',
            },
            # The `[^"\n]+` heading class truncates at the first embedded
            # quote, so the citation must fail loudly rather than
            # prefix-match the real (longer) heading.
            1,
            id="heading-with-literal-quote-truncates-and-fails",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "---\nname: example-skill\ndescription: |\n"
                    '  See `sibling.md` § "Nonexistent Heading" for details.\n'
                    "---\n\n## Real Heading\n"
                ),
            },
            # Would be 1 if frontmatter weren't blanked before extraction —
            # pins _blank_frontmatter's exclusion for this extractor.
            0,
            id="citation-inside-frontmatter-is-excluded",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Real Heading\n\n" 'Citation: `sibling.md` § "Duplicate Heading"\n'
                ),
                "example-skill/sibling.md": "## Duplicate Heading\n\nFirst copy.\n\n## Duplicate Heading\n",
            },
            # Would be 1 if heading_texts started requiring headings to be
            # unique — today it returns a set, so two identical normalized
            # headings in the target file still resolve.
            0,
            id="duplicate-headings-in-target-still-resolve",
        ),
    ],
)
def test_citation_report_cases(
    tmp_path: Path, skill_files: dict[str, str], expected_violation_count: int
) -> None:
    _write_skill_files(tmp_path, skill_files)
    violations = _citation_report([tmp_path / "example-skill" / "SKILL.md"], repo_root=tmp_path)
    assert len(violations) == expected_violation_count, violations


@pytest.mark.parametrize("sibling_name", SKILL_AUXILIARY_MD_NAMES)
def test_citation_report_scans_every_auxiliary_sibling(tmp_path: Path, sibling_name: str) -> None:
    """Every name in SKILL_AUXILIARY_MD_NAMES must actually be walked: a name
    listed in the tuple but skipped by _citation_sources_for_skill_md would
    report 0 violations here, whichever name it is."""
    _write_skill_files(
        tmp_path,
        {
            "example-skill/SKILL.md": "## Real Heading\n",
            f"example-skill/{sibling_name}": "## Sibling\n\n" 'Bad: `does-not-exist.md` § "Whatever"\n',
        },
    )
    violations = _citation_report([tmp_path / "example-skill" / "SKILL.md"], repo_root=tmp_path)
    assert len(violations) == 1, violations


def test_citation_report_target_resolving_to_a_directory_fails(tmp_path: Path) -> None:
    """A citation target that resolves to a directory, not a file, must fail
    to resolve rather than raise when the resolver reads it for headings."""
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "not-a-file").mkdir()
    (skill_dir / "SKILL.md").write_text("## Home\n\n" 'Citation: `not-a-file` § "Whatever"\n')

    violations = _citation_report([skill_dir / "SKILL.md"], repo_root=tmp_path)

    assert len(violations) == 1
    assert "not-a-file" in violations[0]


def test_resolve_citation_target_cross_skill_directory_succeeds(tmp_path: Path) -> None:
    """`skill-name/file` resolves via the injected `skill_md_files` seam when
    a same-named skill directory holds the file, without touching the real
    repo tree — this is what `_all_skill_md_files()` would do in production.

    The real skill directory lives outside `repo_root` so the repo-relative
    tier (1) cannot resolve it first — only the cross-skill-directory tier
    (2), driven by the injected seam, can.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other_skill_md = tmp_path / "elsewhere" / "other-skill" / "SKILL.md"
    other_skill_md.parent.mkdir(parents=True)
    other_skill_md.write_text("## Home\n")
    (other_skill_md.parent / "REFERENCES.md").write_text("## Target Heading\n")

    resolved = _resolve_citation_target(
        "other-skill/REFERENCES.md",
        citing_file=repo_root / "example-skill" / "SKILL.md",
        repo_root=repo_root,
        skill_md_files=lambda: [other_skill_md],
    )

    assert resolved == other_skill_md.parent / "REFERENCES.md"


def test_resolve_citation_target_cross_skill_directory_fails_on_unknown_skill(
    tmp_path: Path,
) -> None:
    """A `skill-name/file` target whose skill name matches no directory
    returned by the injected `skill_md_files` seam is unresolvable."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other_skill_md = tmp_path / "elsewhere" / "other-skill" / "SKILL.md"
    other_skill_md.parent.mkdir(parents=True)
    other_skill_md.write_text("## Home\n")

    resolved = _resolve_citation_target(
        "nonexistent-skill/REFERENCES.md",
        citing_file=repo_root / "example-skill" / "SKILL.md",
        repo_root=repo_root,
        skill_md_files=lambda: [other_skill_md],
    )

    assert resolved is None


def test_citation_report_names_citing_file_line_and_target_for_unresolvable_citation(
    tmp_path: Path,
) -> None:
    """Drives the full glob → extract → resolve → aggregate → report path
    over a small synthetic corpus with one resolvable and one unresolvable
    citation. A later refactor that narrows the glob, swallows an exception,
    or drops an aggregation branch would pass every helper-level test above
    and only surface here.
    """
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "## Real Heading\n\n"
        'Resolvable: `example-skill/SKILL.md` § "Real Heading".\n'
        'Unresolvable: `missing-file.md` § "Some Heading".\n'
    )

    violations = _citation_report([skill_dir / "SKILL.md"], repo_root=tmp_path)

    assert len(violations) == 1
    assert violations[0].startswith("example-skill/SKILL.md:4")
    assert "missing-file.md" in violations[0]


_HANDOFF_WARRANT_CHECK_HEADING = "## Before writing: is a handoff warranted?"


# Each entry is one condition→action-bound bullet from handoff/SKILL.md's
# canonical "Before writing: is a handoff warranted?" section. Matched as a
# whole clause with a right-edge boundary check (_assert_pinned_clause_right_bounded)
# rather than by six `assert token in body` checks, because a token-presence
# check would still pass on a gutted section that lists the field names as a
# bare enumeration while dropping the binding between a status/field value
# and what it means.
_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES: dict[str, str] = {
    "status_ok": (
        '`"status":"ok"` — the session is past its threshold when `over_threshold` '
        "is `true` or `already_fired` is `true`; report `estimate` and `threshold` "
        "either way."
    ),
    "nudge_disabled": (
        "`nudge_disabled` is `true` — say so; the measurement still holds but no "
        "nudge will arrive on its own."
    ),
    "model_recognized_false": (
        '`"model_recognized":false` — the window fell back to the 1M default, '
        "so also report `model`/`context_window` and treat the result as a "
        "soft number; those two fields are what let the engineer judge how "
        "far off it is."
    ),
    "cannot_resolve_or_schema_drift": (
        '`"status":"cannot-resolve"` or `"status":"schema-drift"` — name the '
        "`reason` and fall back to judgment."
    ),
    # The fallback-to-judgment sentence is followed on the same line by more
    # prose, so this entry pins through the paragraph's own end (a real
    # structural boundary) instead of stopping mid-sentence.
    "fallback_to_judgment_and_closing": (
        "When `--check` can't resolve a measurement, weigh session length, "
        "how much of the task remains, and whether this is a natural seam. "
        "Any of these warrant a handoff without a cost argument at all: a "
        "§2 reason that applies on its own terms; an explicit engineer "
        "request; or the session ending anyway. Do not quote the raw "
        "`session_id` into prose that may reach a commit, PR body, or "
        "handoff file. If none of the above warrant writing, run "
        "`~/.claude/scripts/marker.sh "
        "deactivate handoff` before stopping — the marker activated above "
        "has no further purpose once the write itself doesn't happen."
    ),
}


class TestHandoffWarrantCheckCanonicalSection:
    """Pin handoff/SKILL.md's "Before writing: is a handoff warranted?"
    section as the single canonical statement of the `--check` reporting
    contract that `plan-it` Step 7 and `ready-for-review` step 1 cite rather
    than restate, per CLAUDE.md's single-source-of-truth rule.
    """

    @pytest.mark.parametrize("branch", sorted(_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES))
    def test_pinned_branch_clause_matches_live_text(self, branch: str) -> None:
        """Exact clause match per branch, mirroring
        test_pinned_scope_clause_matches_live_text. On failure, re-read the
        guarantee and update the constant only if the new wording preserves it.
        """
        raw_section = _raw_heading_section_text(
            _skill_file("handoff"), _HANDOFF_WARRANT_CHECK_HEADING
        )
        pinned_text = " ".join(_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES[branch].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            raw_section,
            context=f"handoff/SKILL.md: {branch!r} branch no longer matches its pinned clause.",
        )

    def test_plan_it_cites_the_canonical_section(self) -> None:
        """Uses the repo's own citation parser rather than a raw substring
        match, so a benign hard-wrap the parser tolerates doesn't false-fail
        this test. Citing-side presence only — whether the citation actually
        resolves against the target file's real headings is
        `test_skill_citations_resolve_to_real_headings`'s job.
        """
        citations = _extract_citations(_skill_file("plan-it").read_text())
        expected_heading = normalize_heading("Before writing: is a handoff warranted?")
        assert any(
            citation.target == "handoff/SKILL.md"
            and normalize_heading(citation.heading) == expected_heading
            for citation in citations
        ), "plan-it/SKILL.md no longer cites handoff/SKILL.md's warrant-check section"

    def test_ready_for_review_cites_the_canonical_section(self) -> None:
        """Uses the repo's own citation parser rather than a raw substring
        match, so a benign hard-wrap the parser tolerates doesn't false-fail
        this test. Citing-side presence only — whether the citation actually
        resolves against the target file's real headings is
        `test_skill_citations_resolve_to_real_headings`'s job.
        """
        citations = _extract_citations(_skill_file("ready-for-review").read_text())
        expected_heading = normalize_heading("Before writing: is a handoff warranted?")
        assert any(
            citation.target == "handoff/SKILL.md"
            and normalize_heading(citation.heading) == expected_heading
            for citation in citations
        ), "ready-for-review/SKILL.md no longer cites handoff/SKILL.md's warrant-check section"


# Clauses of resume-context/SKILL.md that keep the skill from consuming a file it
# cannot match to this session, or from running on its own.
# One clause has the skill set each relayed command off as a fenced code block.
_PINNED_RESUME_CONTEXT_CLAUSES: dict[str, str] = {
    "run_only_when_asked": (
        "Run only when the engineer asked to resume in this session. Never run the "
        "skill on your own, including right after writing a handoff or brief."
    ),
    "no_argument_rerun_guard": (
        "If this conversation already loaded a file through this skill, run the "
        "script again only with an explicit argument. With no argument, tell the "
        "engineer and stop."
    ),
    "refuse_and_tell_on_unsafe_arguments": (
        "If `$ARGUMENTS` starts with `-` or contains any character outside "
        "`[A-Za-z0-9._/@+-]`, do not run the script; tell the engineer the accepted "
        "forms: a slug, a file name, or an absolute path."
    ),
    "never_read_pending_path": (
        "Never `Read` a path under `<config-dir>/handoffs/` or `<config-dir>/briefs/`, "
        "because that `Read` consumes the file before any check runs."
    ),
    "session_target_comparison": (
        "First compare `SESSION_TARGET:` with the worktree this session is anchored in: "
        "the working directory the environment context shows, or the `EnterWorktree` "
        "target. The script keys on the Bash `$PWD`, so an earlier `cd` into another "
        "worktree yields a verdict for that worktree. When `SESSION_TARGET:` is `none`, "
        "they match only if the anchored directory is not inside a linked worktree of any "
        "repository. If they differ, say so and stop before any `Read`, without acting on "
        "the file. Give the recovery text below."
    ),
    "file_target_none_check_then_stop": (
        "Then check the target. When the script printed `FILE_TARGET: none` and the "
        "file's §4 header (§3 in a brief) names a directory other than this session's, "
        "say so and stop without acting on the file. Give the recovery text below."
    ),
    "consumed_file_recovery_text": (
        "Recovery text: the file is already consumed, so tell the engineer to `cd` to "
        "the directory named here and run `claude --append-system-prompt-file <LOAD path>`. "
        "Also tell them that `~/.claude/scripts/find-consumed-continuity-file.sh <slug>` "
        "finds the copy later. The directory is the `FILE_TARGET:` path when it is a path "
        "(on `LOAD:` it equals `SESSION_TARGET:`). When `FILE_TARGET:` is `none`, the "
        "script printed no directory for the file: say the directory is unknown and must "
        "come from the session that wrote the file; name none."
    ),
    "unreadable_file_target_relay": (
        "When `FILE_TARGET: unknown` appears, add that the script could not resolve one "
        "directory from the file, so the printed command has none. The engineer "
        "should check the resume command in the file itself, outside this session because "
        "a `Read` here consumes it, and run that command in place of the printed one."
    ),
    "slash_form_only_for_matching_candidates": (
        "Suggest `/resume-context <file name>` only for a candidate whose `target` is the "
        "same path as `SESSION_TARGET:`, or is `none` when `SESSION_TARGET: none`, and never "
        "when either is `unknown`. A candidate whose file name starts with `-` or `@` is "
        "never suggested as `/resume-context <name>`. Every candidate not suggested that way "
        "needs the shell command on its `CANDIDATE:` line, which you relay as a command for "
        "a new terminal."
    ),
    "unreadable_or_absent_candidate_target_caveat": (
        "Add step 3's caveats: its `SESSION_TARGET: unknown` caveat once for the list when "
        "that line appears, and, for each candidate with `target none` or `target unknown`, "
        "the caveat for that `FILE_TARGET:` value beside its shell command."
    ),
    "read_only_after_session_target_matches": (
        "Otherwise `Read` the `LOAD:` path in full. Tell the engineer which `SOURCE:` "
        "file was consumed and its `AGE:`."
    ),
    "skipped_unprintable_relay": (
        "Relay `SKIPPED_UNPRINTABLE:` whenever it appears, with or without candidates. "
        "It means n pending files under `<config-dir>/handoffs/` or `<config-dir>/briefs/` "
        "have names with a character outside `[A-Za-z0-9._/@+-]`, so the script does not "
        "print them. The engineer can resume one with the shell `resume-context` and its "
        "full path; do not list or name them yourself."
    ),
    "no_enter_worktree_no_section_seven": (
        "Do not call `EnterWorktree`, and do not run the file's §7/§7.5 command. The "
        "script already matched the file to this session."
    ),
    "absent_file_target_relay": (
        "When `FILE_TARGET: none` appears, add that the file names no worktree, so the "
        "command must be run from the directory the file was written in."
    ),
    "unknown_session_target_relay": (
        "When `SESSION_TARGET: unknown` appears, add that this session's worktree "
        "could not be determined (a path with a space or a failed git call), not that "
        "the file names another worktree."
    ),
    "shell_command_relay_and_stop": (
        "Relay the line verbatim as a command for a new terminal, not as a slash "
        "command. The file is still pending. Stop."
    ),
    "fence_each_relayed_command": (
        "In chat replies, put each command this skill has you relay or suggest alone "
        "in its own fenced code block."
    ),
}


class TestResumeContextSkill:
    """Pin the resume-context body's guards, so a reword that drops one fails here
    instead of drifting silently. The tests check wording and the clause's right edge,
    not behavior. On failure, re-read the guarantee and update the
    constant only if the new wording preserves it.
    """

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_RESUME_CONTEXT_CLAUSES))
    def test_pinned_clause_matches_live_text(self, clause_key: str) -> None:
        body = _skill_file("resume-context").read_text()
        pinned_text = " ".join(_PINNED_RESUME_CONTEXT_CLAUSES[clause_key].split())
        _assert_pinned_clause_right_bounded(
            pinned_text,
            body,
            context=f"resume-context/SKILL.md: the {clause_key!r} clause no longer matches its pinned text.",
        )

    def test_session_target_comparison_precedes_the_read(self) -> None:
        """The comparison must come before the `Read` clause, or a mismatched file is consumed first."""
        body = " ".join(_skill_file("resume-context").read_text().split())
        comparison = " ".join(_PINNED_RESUME_CONTEXT_CLAUSES["session_target_comparison"].split())
        read = " ".join(_PINNED_RESUME_CONTEXT_CLAUSES["read_only_after_session_target_matches"].split())
        assert 0 <= body.find(comparison) < body.find(read), (
            "resume-context/SKILL.md: the SESSION_TARGET comparison clause no longer starts before the `Read` clause."
        )


# Clauses of the handoff §7 and brief §7.5 resume-command sections that make the
# written `--cwd` the directory the resumed session starts in, which the announce
# message compares with the session's worktree to pick the resume route. Each
# section states the directory rule and the command forms (three bullets), then a
# second paragraph. The second paragraph holds the `cd <path> &&` prohibition and
# the instruction to relay the announce hook's resume route, so a trim at the
# line cap must fail here.
_PINNED_RESUME_DIRECTORY_CLAUSES: dict[str, tuple[str, str, str]] = {
    "handoff_directory_bullet": (
        "handoff",
        "## §7 Resume command",
        "The directory is the root of the worktree or checkout where §3's first step "
        "runs, or that directory itself outside any repository. For a step that creates "
        "a worktree it is the existing directory the step runs from. For a first step "
        "that names no directory or is only a question it is this session's own "
        "directory, as §4's header names it.",
    ),
    "handoff_cwd_form_bullet": (
        "handoff",
        "## §7 Resume command",
        "Write `resume-context --cwd <worktree path> <config-dir>/handoffs/<slug>-handoff.md`.",
    ),
    "handoff_bare_form_bullet": (
        "handoff",
        "## §7 Resume command",
        "Write the bare form `resume-context <config-dir>/handoffs/<slug>-handoff.md` "
        "instead only when that directory is the one this session runs in outside any "
        "linked worktree and §4's header names it too. In every other case write the "
        "first form alone.",
    ),
    "handoff_second_paragraph": (
        "handoff",
        "## §7 Resume command",
        "Relay the resume route the announce hook names after the write to the "
        "engineer. `--cwd` makes the launched session start "
        "in that directory regardless of where the resume command is actually run from "
        "— do not use a `cd <path> &&` prefix instead, which only works if the invoker "
        "happens to already be positioned to run it. The command moves the file to a fresh "
        "temp path and launches a new session with it loaded, so nothing depends on the resuming "
        "session remembering to read or delete the file.",
    ),
    "brief_directory_bullet": (
        "brief",
        "## §7.5 Resume command",
        "The directory is the root of the worktree or checkout where §6's first step "
        "runs, or that directory itself outside any repository. For a step that creates "
        "a worktree it is the existing directory the step runs from. For a first step "
        "that names no directory or is only a decision it is the worktree §3 names, "
        "else this session's own directory.",
    ),
    "brief_cwd_form_bullet": (
        "brief",
        "## §7.5 Resume command",
        "Write `resume-context --cwd <worktree path> <config-dir>/briefs/<slug>-task.md`.",
    ),
    "brief_bare_form_bullet": (
        "brief",
        "## §7.5 Resume command",
        "Write the bare form `resume-context <config-dir>/briefs/<slug>-task.md` "
        "instead only when that directory is the one this session runs in outside any "
        "linked worktree and §3 names no other directory. In every other case write the "
        "first form alone.",
    ),
    "brief_second_paragraph": (
        "brief",
        "## §7.5 Resume command",
        "Relay the resume route the announce hook names after the write to the "
        "engineer. `--cwd` makes the launched session start "
        "in that directory regardless of where the resume command is actually run from "
        "— do not use a `cd <path> &&` prefix instead, which only works if the invoker "
        "happens to already be positioned to run it. The command moves the file to a fresh "
        "temp path and launches a new session with it loaded, so picking up the brief doesn't depend "
        "on the fresh session remembering to read or delete the file.",
    ),
}

_HANDOFF_SECTION4_HEADING = "## §4 Files modified this session"
_HANDOFF_SECTION4_REENTER_PHRASE = (
    "Direct the resuming session to re-enter §7's directory (its `--cwd`, or this "
    "header's directory when §7 is bare) before"
)


class TestResumeCommandDirectoryClauses:
    """Pin the §7/§7.5 text that makes `--cwd` the directory the resumed session
    starts in. The tests check wording and the clause's right edge, not behavior.
    On failure, re-read the guarantee and update the constant only if the new
    wording preserves it.
    """

    @pytest.mark.parametrize("clause_key", sorted(_PINNED_RESUME_DIRECTORY_CLAUSES))
    def test_pinned_clause_matches_live_text(self, clause_key: str) -> None:
        skill_name, heading, pinned_text = _PINNED_RESUME_DIRECTORY_CLAUSES[clause_key]
        raw_section = _raw_heading_section_text(_skill_file(skill_name), heading)
        _assert_pinned_clause_right_bounded(
            " ".join(pinned_text.split()),
            raw_section,
            context=f"{skill_name}/SKILL.md: the {clause_key!r} clause no longer matches its pinned text.",
        )

    @pytest.mark.parametrize(
        "clause_key",
        [
            "handoff_directory_bullet",
            "handoff_cwd_form_bullet",
            "handoff_bare_form_bullet",
            "brief_directory_bullet",
            "brief_cwd_form_bullet",
            "brief_bare_form_bullet",
        ],
    )
    def test_directory_rule_clause_is_one_unwrapped_line(self, clause_key: str) -> None:
        """A hard wrap inside a clause that holds a command splits the command, and the
        template contract's scan joins lines only on a trailing backslash.
        """
        skill_name, heading, clause = _PINNED_RESUME_DIRECTORY_CLAUSES[clause_key]
        section_lines = [
            line.removeprefix("- ") for line in _raw_heading_section_text(_skill_file(skill_name), heading).splitlines()
        ]
        assert clause in section_lines, (
            f"{skill_name}/SKILL.md: the {clause_key!r} clause is not one unwrapped line of its section."
        )

    def test_handoff_section4_directs_reentry_to_the_resume_directory(self) -> None:
        section_text = _heading_section_text(_skill_file("handoff"), _HANDOFF_SECTION4_HEADING)
        assert _HANDOFF_SECTION4_REENTER_PHRASE in section_text, (
            "handoff/SKILL.md: §4 no longer directs the resuming session to re-enter §7's directory."
        )


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


# Pinned verbatim: transcript-analysis/SKILL.md's "Scope confirmation"
# section sentence. A prose caveat with no enforcement is the same failure
# class as the transcript-config-dirs-unaware caveat that preceded it -- this
# sentence must appear in the skill body exactly, not merely in spirit.
_SCOPE_CONFIRMATION_SENTENCE = (
    "Before quoting a corpus-wide statistic from this toolkit's output, include "
    "the resolved-scope header line verbatim in what you report, and if that "
    "line reads \"1 root (no ~/.claude/transcript-config-dirs declared)\", ask "
    "the user whether other Claude accounts exist before treating the number "
    "as complete."
)


class TestTranscriptAnalysisScopeConfirmationContract:
    """transcript-analysis/SKILL.md must carry its "Scope confirmation"
    section's pinned sentence verbatim -- see _SCOPE_CONFIRMATION_SENTENCE."""

    def test_pinned_scope_confirmation_sentence_present_verbatim(self):
        body = _skill_body("transcript-analysis")
        assert _SCOPE_CONFIRMATION_SENTENCE in body, (
            "transcript-analysis/SKILL.md is missing its pinned scope-"
            f"confirmation sentence verbatim:\n{_SCOPE_CONFIRMATION_SENTENCE!r}"
        )


# A literal ~/.claude/$HOME/.claude/${HOME}/.claude prefix on a per-account-
# state path — a state subdirectory, a log, or claude-config.toml (the one
# state file config-keys.psv's schema-driven keys resolve through) — is a
# functional bug under a non-personal CLAUDE_CONFIG_DIR account, unlike a
# stowed path (agents/, hooks/, rules/, scripts/, skills/), which resolves
# identically under every account; the directory alternation mirrors
# enforce-marker-script-shape.sh's own _marker_shape_match glob pair.
# worktree_required and autonomous_shipping are deliberately excluded —
# those two config-keys.psv keys carry resolution: config-dir-or-home, a
# union with the literal $HOME/.claude location rather than a fully
# migrated-away path (see docs/config-file.md and README's "Worktree
# enforcement").
_PER_ACCOUNT_STATE_PATH_RE = re.compile(
    r"(~|\$HOME|\$\{HOME\})/\.claude/"
    r"(handoffs/|briefs/|issue-triage/|plans/|projects/|sessions/"
    r"|[^/\s\"'`]*-markers/|\.[^/\s\"'`]*\.d/|output-preferences\.md"
    r"|pii-patterns\.md|credential-file-guard\.md|credential-value-patterns\.md"
    r"|data-file-read-guard\.md|private-projects\.md"
    r"|\.permission-prompt-log\.jsonl|\.error-mode-nudge\.log"
    r"|\.handoff-nudge\.log|\.commit-stall-block\.log"
    r"|claude-config\.toml)"
)

# The marker triple (settings.json permission rules, hook command strings,
# and these SKILL.md invocation sites) is deliberately excluded from the
# contract below — see plan-review/SKILL.md's marker.sh comment for why
# these six invocation sites stay literal rather than config-dir-aware.
_MARKER_TRIPLE_SITES = [
    ("respond-pr", "~/.claude/scripts/marker.sh activate respond-pr"),
    ("respond-pr", "~/.claude/scripts/marker.sh deactivate respond-pr"),
    ("plan-review", "~/.claude/scripts/marker.sh activate plan-review"),
    ("plan-review", "~/.claude/scripts/marker.sh resolve-session-id"),
    ("plan-review", "~/.claude/scripts/marker.sh deactivate plan-review"),
    ("plan-review", "~/.claude/scripts/marker.sh write plan-review"),
    ("ready-for-review", "~/.claude/scripts/marker.sh activate ready-for-review"),
    ("ready-for-review", "~/.claude/scripts/marker.sh write ready-for-review"),
    ("ready-for-review", "~/.claude/scripts/marker.sh deactivate ready-for-review"),
    ("ai-instruction-and-memory-files", "~/.claude/scripts/marker.sh activate memory-skill"),
    ("ai-instruction-and-memory-files", "~/.claude/scripts/marker.sh deactivate memory-skill"),
    ("code-review", "~/.claude/scripts/marker.sh write code-review"),
    ("handoff", "~/.claude/scripts/marker.sh activate handoff"),
    ("handoff", "~/.claude/scripts/marker.sh deactivate handoff"),
    ("handoff", "~/.claude/scripts/marker.sh status"),
]


def _all_agent_md_paths() -> list[Path]:
    """Every agent body under claude/.claude/agents/ and plugin agents/ dirs —
    _agent_body() in _skill_md_helpers.py is by-name only, so a corpus-wide scan needs this."""
    paths = sorted(_AGENTS_DIR.glob("*.md"))
    if _PLUGINS_DIR.exists():
        paths += sorted(_PLUGINS_DIR.glob("*/agents/*.md"))
    return paths


def _strip_yaml_frontmatter(text: str) -> str:
    """Drop a leading '---'-delimited frontmatter block so a SKILL.md's or
    agent's deliberately-literal frontmatter `description:` (loads standalone
    for trigger-matching before any body is read, so a <config-dir>
    placeholder would have no definition in scope) doesn't false-fail the
    state-path contract below."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def _all_citation_extraction_doc_paths() -> list[Path]:
    """`_all_doc_paths()`'s corpus, plus the always-loaded instruction files
    and lazy-loaded rule files where a citation-shaped construct could also
    hide unextracted: the repo-root CLAUDE.md, `_GLOBAL_CLAUDE_MD`
    (`claude/.claude/CLAUDE.md`), every `.claude/rules/*.md` file, and every
    `claude/.claude/rules/*.md` file. Kept separate from `_all_doc_paths()`
    so this wider corpus doesn't also widen TestPerAccountStatePathContract's
    scope, which parametrizes directly off that helper."""
    paths = list(_all_doc_paths())
    repo_root = REPO_ROOT
    root_claude_md = repo_root / "CLAUDE.md"
    if root_claude_md.exists():
        paths.append(root_claude_md)
    if _GLOBAL_CLAUDE_MD.exists():
        paths.append(_GLOBAL_CLAUDE_MD)
    project_rules_dir = repo_root / ".claude" / "rules"
    project_rules = sorted(project_rules_dir.glob("*.md"))
    assert project_rules, f"{project_rules_dir}/*.md matched no *.md files — this glob root is wrong"
    paths += project_rules

    global_rules_dir = CLAUDE_DIR / "rules"
    global_rules = sorted(global_rules_dir.glob("*.md"))
    assert global_rules, f"{global_rules_dir}/*.md matched no *.md files — this glob root is wrong"
    paths += global_rules

    return paths


def test_all_citation_extraction_doc_paths_raises_when_project_rules_dir_empty(tmp_path, monkeypatch):
    """Guards the `assert project_rules, ...` non-emptiness check itself — a
    typo checking an always-truthy variable (e.g. `project_rules_dir`
    instead of the glob result `project_rules`) would otherwise never fire
    under test."""
    import sys

    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="matched no"):
        _all_citation_extraction_doc_paths()


def test_all_citation_extraction_doc_paths_raises_when_global_rules_dir_empty(tmp_path, monkeypatch):
    """Same guard as above, for the sibling `assert global_rules, ...` check
    over `CLAUDE_DIR / "rules"`."""
    import sys

    monkeypatch.setattr(sys.modules[__name__], "CLAUDE_DIR", tmp_path)
    with pytest.raises(AssertionError, match="matched no"):
        _all_citation_extraction_doc_paths()


class TestPerAccountStatePathContract:
    """No SKILL.md body, agent body, prose doc, or claude/.claude/CLAUDE.md
    may hold a literal ~/.claude (or $HOME/.claude, ${HOME}/.claude)
    per-account-state path. Covers the $HOME-form bypass a bare
    '~/.claude/' substring check would miss entirely."""

    def test_flags_issue_triage_state_path(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("~/.claude/issue-triage/run.md")
        assert match is not None

    def test_does_not_flag_issue_triage_config_dir_prose(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("<config-dir>/issue-triage/run.md")
        assert match is None

    def test_flags_claude_config_toml_state_path(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("~/.claude/claude-config.toml")
        assert match is not None

    def test_does_not_flag_claude_config_toml_config_dir_prose(self):
        match = _PER_ACCOUNT_STATE_PATH_RE.search("<config-dir>/claude-config.toml")
        assert match is None

    @pytest.mark.parametrize("skill_md_path", _all_skill_md_paths(), ids=lambda p: str(p))
    def test_skill_body_has_no_state_path(self, skill_md_path):
        body = _strip_yaml_frontmatter(skill_md_path.read_text())
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{skill_md_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/... (skill/agent prose) "
            "or push resolution into the invoked script via its own "
            "config_dir()/_lib_config_dir call (a runnable command needing "
            "a config-dir-relative data path)"
        )

    @pytest.mark.parametrize("agent_md_path", _all_agent_md_paths(), ids=lambda p: str(p))
    def test_agent_body_has_no_state_path(self, agent_md_path):
        body = _strip_yaml_frontmatter(agent_md_path.read_text())
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{agent_md_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/..."
        )

    @pytest.mark.parametrize("doc_path", _all_doc_paths(), ids=lambda p: str(p))
    def test_doc_has_no_state_path(self, doc_path):
        body = doc_path.read_text()
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{doc_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/..., with one caveat "
            "sentence per file defining it"
        )

    def test_global_claude_md_has_no_state_path(self):
        body = _GLOBAL_CLAUDE_MD.read_text()
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"claude/.claude/CLAUDE.md contains a hardcoded per-account-state "
            f"path {match.group(0)!r} — use <config-dir>/..."
        )

    @pytest.mark.parametrize("skill_name,expected_substring", _MARKER_TRIPLE_SITES)
    def test_marker_triple_site_stays_unmigrated(self, skill_name, expected_substring):
        assert expected_substring in _skill_body(skill_name), (
            f"{skill_name}/SKILL.md no longer contains the literal marker.sh "
            f"invocation {expected_substring!r} — the marker triple "
            "(settings.json allow-rule + enforce-marker-script-shape.sh's "
            "anchor + this literal form) must change in lockstep or not at "
            "all; see plan-review/SKILL.md's marker.sh comment"
        )


# A templated $CLAUDE_CONFIG_DIR expansion, or a <config-dir> prose
# placeholder, landing immediately in front of a scripts/ or hooks/ path --
# every script and hook under claude/.claude/ is stow-shared and
# machine-invariant (resolves into the same checkout under every account), so
# its own call-site path never needs runtime templating. Anchored on the
# literal /scripts/ or /hooks/ segment so the ~25 legitimate per-account
# *state* templating sites (handoffs/, briefs/, sentinels, logs --
# _PER_ACCOUNT_STATE_PATH_RE's own domain) are left untouched.
_STOWED_PATH_TEMPLATING_RE = re.compile(
    r"CLAUDE_CONFIG_DIR[^}]*\}/(?:scripts|hooks)/"
    r"|CLAUDE_CONFIG_DIR/(?:scripts|hooks)/"
    r"|(?<=[<{])config-dir[>}]?/(?:scripts|hooks)/"
)


def _stowed_path_templating_matches(markdown_text: str) -> list[str]:
    """Every _STOWED_PATH_TEMPLATING_RE match outside a
    HOOK_TEST_FIXTURE/HOOK_SCRIPT_CONTENT_EXAMPLE-excluded fenced block.

    Mirrors _trigger_a_matches's fence-exclusion structure (not
    TestPerAccountStatePathContract's plain whole-body search): this
    contract's corpus includes SKILL.md bodies with pytest-executed
    HOOK_TEST_FIXTURE fenced blocks that would otherwise false-positive
    against the regex.
    """
    lines = markdown_text.split("\n")
    excluded_lines: set[int] = set()
    for open_index, content in _fenced_code_blocks(lines):
        if _fence_excluded_by_marker(lines, open_index):
            close_index = open_index + 1 + len(content)
            excluded_lines.update(range(open_index, close_index + 1))
    matches: list[str] = []
    for index, line in enumerate(lines):
        if index in excluded_lines:
            continue
        matches.extend(match.group(0) for match in _STOWED_PATH_TEMPLATING_RE.finditer(line))
    return matches


class TestStowedScriptPathContract:
    """No SKILL.md body, agent body, prose doc, or claude/.claude/CLAUDE.md
    may template $CLAUDE_CONFIG_DIR (or a <config-dir> prose placeholder) in
    front of a scripts/ or hooks/ path. The other half of
    the invariant TestPerAccountStatePathContract guards from the state-path
    direction: stowed paths must be literal, state paths must stay
    config-dir-relative."""

    def test_flags_templated_expansion_in_fenced_block(self) -> None:
        text = '```bash\npython3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/foo.py"\n```'
        assert _stowed_path_templating_matches(text) == [
            'CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/'
        ]

    def test_flags_prose_config_dir_placeholder_outside_any_fence(self) -> None:
        text = "Run `<config-dir>/scripts/marker.sh status` and paste its output."
        assert _stowed_path_templating_matches(text) == ["config-dir>/scripts/"]

    def test_flags_hooks_path_as_well_as_scripts(self) -> None:
        text = "Run `<config-dir>/hooks/nudge-handoff-near-context-cap.sh --check`."
        assert _stowed_path_templating_matches(text) == ["config-dir>/hooks/"]

    def test_flags_brace_free_config_dir_expansion(self) -> None:
        text = "Resolves so `$CLAUDE_CONFIG_DIR/scripts/foo.py` runs."
        assert _stowed_path_templating_matches(text) == ["CLAUDE_CONFIG_DIR/scripts/"]

    def test_does_not_flag_unrelated_path_merely_ending_in_config_dir(self) -> None:
        """"own-config-dir/scripts/build" has no preceding "<" or "${" --
        it isn't a <config-dir> prose placeholder, just a path segment that
        happens to end in the literal word "config-dir"."""
        text = "own-config-dir/scripts/build"
        assert _stowed_path_templating_matches(text) == []

    def test_does_not_flag_marker_excluded_fence(self) -> None:
        text = (
            "<!-- HOOK_TEST_FIXTURE: example -->\n\n"
            '```bash\npython3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/foo.py"\n```'
        )
        assert _stowed_path_templating_matches(text) == []

    def test_does_not_flag_per_account_state_path(self) -> None:
        """A state path (handoffs/, briefs/) templated the same way is the
        legitimate _PER_ACCOUNT_STATE_PATH_RE case, not this contract's --
        the regex is anchored on the literal scripts/ or hooks/ segment,
        never handoffs/ or briefs/."""
        text = 'mkdir -p "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/handoffs"'
        assert _stowed_path_templating_matches(text) == []

    @pytest.mark.parametrize("skill_md_path", _all_skill_md_paths(), ids=lambda p: str(p))
    def test_skill_body_has_no_templated_stowed_path(self, skill_md_path):
        matches = _stowed_path_templating_matches(skill_md_path.read_text())
        assert not matches, (
            f"{skill_md_path} templates $CLAUDE_CONFIG_DIR or <config-dir> in "
            f"front of a scripts/hooks path {matches!r} — every script/hook "
            "under claude/.claude/ is stow-shared and machine-invariant, so "
            "name it literally (~/.claude/scripts/<name> or "
            "~/.claude/hooks/<name>) instead"
        )

    @pytest.mark.parametrize("agent_md_path", _all_agent_md_paths(), ids=lambda p: str(p))
    def test_agent_body_has_no_templated_stowed_path(self, agent_md_path):
        matches = _stowed_path_templating_matches(agent_md_path.read_text())
        assert not matches, (
            f"{agent_md_path} templates $CLAUDE_CONFIG_DIR or <config-dir> in "
            f"front of a scripts/hooks path {matches!r} — name it literally "
            "(~/.claude/scripts/<name> or ~/.claude/hooks/<name>) instead"
        )

    @pytest.mark.parametrize("doc_path", _all_doc_paths(), ids=lambda p: str(p))
    def test_doc_has_no_templated_stowed_path(self, doc_path):
        matches = _stowed_path_templating_matches(doc_path.read_text())
        assert not matches, (
            f"{doc_path} templates $CLAUDE_CONFIG_DIR or <config-dir> in "
            f"front of a scripts/hooks path {matches!r} — name it literally "
            "(~/.claude/scripts/<name> or ~/.claude/hooks/<name>) instead"
        )

    def test_global_claude_md_has_no_templated_stowed_path(self):
        matches = _stowed_path_templating_matches(_GLOBAL_CLAUDE_MD.read_text())
        assert not matches, (
            f"claude/.claude/CLAUDE.md templates $CLAUDE_CONFIG_DIR or "
            f"<config-dir> in front of a scripts/hooks path {matches!r} — "
            "name it literally (~/.claude/scripts/<name> or "
            "~/.claude/hooks/<name>) instead"
        )
