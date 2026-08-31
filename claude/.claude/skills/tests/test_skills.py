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
from pathlib import Path

import pytest

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import SCRIPTS_DIR, extract_skill_command, run_skill_command

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from run_skill_evals import extract_governing_rule
from validate_skill_structure import (
    SKILL_LISTING_BUDGET_CHARS,
    corpus_budget_violations,
    parse_frontmatter,
    validate,
)

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
# Plugins live two levels above the .claude/ dir: <repo>/plugins/<name>/skills/<skill>/SKILL.md
_PLUGINS_DIR = SKILLS_DIR.parent.parent.parent / "plugins"
# The stowed global instruction file, installed to ~/.claude/CLAUDE.md.
_GLOBAL_CLAUDE_MD = SKILLS_DIR.parent / "CLAUDE.md"


_AGENTS_DIR = SKILLS_DIR.parent / "agents"


def _agent_body(agent_name: str) -> str:
    """Read a claude/.claude/agents/<name>.md body by name."""
    return (_AGENTS_DIR / f"{agent_name}.md").read_text()


def _skill_body(skill_name: str) -> str:
    """Read a stowed skill's SKILL.md body by name."""
    return (SKILLS_DIR / skill_name / "SKILL.md").read_text()


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
        if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
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
                if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
                    continue
                frontmatter = _skill_description(skill_dir.name)
                if frontmatter and "user-invocable: false" in frontmatter and skill_dir.name not in skills:
                    skills.append(skill_dir.name)
    return skills


def _settings_skill_overrides() -> dict[str, str]:
    """Read skillOverrides from the stowed settings.json.

    Returns the override map keyed by skill name. Skills absent from the map
    default to "on" (fully model-invokable with description in budget).
    """
    settings_path = Path(__file__).resolve().parents[4] / "claude/.claude/settings.json"
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
                if skill_dir.name in _NAME_DISPATCHED_NO_TRIGGER:
                    continue
                qualified_name = f"{plugin_dir.name}:{skill_dir.name}"
                if skill_dir.name in budget_excluded or qualified_name in budget_excluded:
                    continue
                # Description fetch uses the bare name — correct for single-plugin repos.
                # If two plugins ever ship a skill with the same bare name, the first-alphabetically
                # found plugin's frontmatter would be used. Revisit to pass qualified_name when
                # a second same-named plugin skill is added.
                frontmatter = _skill_description(skill_dir.name)
                if (
                    frontmatter
                    and "disable-model-invocation: true" not in frontmatter
                    and skill_dir.name not in skills
                ):
                    skills.append(skill_dir.name)
    return skills


# Explicit registry of user-only command skills that use the frontmatter
# disable-model-invocation: true flag. Currently empty — the four former command
# skills (brief, handoff, read-docx-comments, transcript-analysis) now use
# skillOverrides: name-only, making them model-invokable by name while keeping
# their descriptions out of the budget. Add to this list only when a new skill
# needs strict slash-only access (no model invocation at all) via frontmatter
# rather than skillOverrides.
COMMAND_SKILLS: list[str] = []

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
        desc = _skill_description(skill_name)
        assert "disable-model-invocation: true" not in desc, (
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
            f"TRIGGER discipline) but its frontmatter contains 'TRIGGER when:'. "
            f"Remove the TRIGGER blocks — this skill is always dispatched by name, "
            f"never by description auto-trigger — or remove it from _NAME_DISPATCHED_NO_TRIGGER."
        )


class TestConventionSkillWiring:
    """Assert that name-dispatched skills are explicitly wired into consumers.

    test-conventions, sql-query-conventions, and pr-description rely on
    explicit Read/invoke pointers rather than description-based auto-trigger
    (which never fires). These tests prevent silent regression of the wiring.
    """

    def _agent_body(self, name: str) -> str:
        return _agent_body(name)

    def _skill_body(self, name: str) -> str:
        return _skill_body(name)

    def test_code_writer_references_test_conventions(self):
        """code-writer must tell the writer to consult test-conventions for test code."""
        assert "test-conventions/SKILL.md" in self._agent_body("code-writer")

    def test_code_writer_references_sql_query_conventions(self):
        """code-writer must tell the writer to consult sql-query-conventions for read-path SQL."""
        assert "sql-query-conventions/SKILL.md" in self._agent_body("code-writer")

    def test_staff_sdet_reads_test_conventions_body(self):
        """staff-sdet must Read test-conventions/SKILL.md to ground its §N citations."""
        body = self._agent_body("staff-sdet")
        assert "test-conventions/SKILL.md" in body
        assert "test-conventions §4" not in body  # §4 is Test isolation; §5 is naming/regression-test intent

    def test_staff_backend_reads_sql_query_conventions_body(self):
        """staff-backend-engineer must Read sql-query-conventions/SKILL.md."""
        body = self._agent_body("staff-backend-engineer")
        assert "sql-query-conventions/SKILL.md" in body

    def test_code_review_invokes_test_conventions(self):
        """code-review must have an actionable invoke pointer to test-conventions."""
        assert "invoke `test-conventions`" in self._skill_body("code-review")

    def test_code_review_invokes_sql_query_conventions(self):
        """code-review must have an actionable invoke pointer to sql-query-conventions."""
        assert "invoke the `sql-query-conventions`" in self._skill_body("code-review")

    def test_ready_for_review_invokes_pr_description(self):
        """ready-for-review step 5 must have an actionable invoke pointer to pr-description."""
        assert "Invoke the `pr-description`" in self._skill_body("ready-for-review")

    def test_handoff_runs_pr_description(self):
        """handoff's pre-write checklist must have an actionable run pointer to pr-description."""
        assert "run the `pr-description`" in self._skill_body("handoff")


class TestSkillFidelityReviewerUndecidableDismissal:
    """Pin the decidability-keyed dismissal rule and its visible output slot.

    skill-fidelity-reviewer's only evidence is the diff text and the plan
    path; it has no Bash. A rule keyed on artifact *location* rather than
    *decidability* mis-fires in both directions: it would evaluate
    respond-pr's diff-visible commit (whose correctness depends on review
    comments the reviewer never sees), and it would sweep plan-it's plan
    file into a bare ~/.claude/-prefix dismissal (plan-it writes there in
    plan mode before staging the file onto the branch). The dismissal must
    also be visible: without an Output-format slot, a dismissed skill
    produced no signal at all -- silent non-coverage in place of the false
    positive the rule exists to fix.
    """

    # Shared with test_undecidable_examples_exclude_a_known_decidable_skill,
    # which uses this same literal as its enumeration-clause slice-end anchor
    # -- rewording it fails both tests; the other test's ValueError on a
    # missing anchor is expected collateral, not a separate defect.
    _DISK_HUNT_PROHIBITION = "do not go looking for it on disk"

    def _body(self):
        return _agent_body("skill-fidelity-reviewer")

    def test_declares_decidability_test(self):
        """The dismissal rule must be keyed on decidability, not artifact presence."""
        assert "decidable from your evidence" in self._body()

    def test_prohibits_disk_hunt_for_dismissed_artifacts(self):
        """The reviewer must not search disk for a dismissed skill's artifact."""
        assert self._DISK_HUNT_PROHIBITION in self._body()

    def test_undecidable_examples_exclude_a_known_decidable_skill(self):
        """plan-it's plan file is staged onto the branch and is decidable --
        a location-keyed rule (bare ~/.claude/ prefix) would wrongly sweep it
        into the undecidable-example list alongside handoff/brief. Scope the
        check to the enumeration clause itself (elsewhere in the body,
        `plan-it` legitimately appears as a skill-invocation-label example
        and in the anti-adoption guard) and pin its absence there, so a
        future edit widening the illustrative examples can't reintroduce
        that false dismissal silently."""
        body = self._body()
        start = body.index("Undecidable when")
        end = body.index(self._DISK_HUNT_PROHIBITION)
        assert end > start, (
            "anchor order inverted -- a duplicate/earlier occurrence of one "
            "anchor would otherwise silently degrade this to a vacuous "
            "always-pass empty-slice check"
        )
        enumeration_clause = body[start:end]
        assert "plan-it" not in enumeration_clause

    def test_pointer_line_reports_dismissed_count(self):
        """The pointer line must report dismissals, not just carry the
        step-2 instruction to record them. A single `in body` check on the
        bare phrase is satisfied by the step-2 occurrence alone, so this
        pins the fuller pointer-line fragment specifically."""
        assert "issues, <M> dismissed as undecidable" in self._body()

    def test_file_based_output_relaxes_heading_to_suggestion(self):
        """The file-based output structure must still tell the model where
        to place dismissals, but stop mandating the exact heading string --
        this pins the placement guidance and the relaxation language
        together, so an edit that keeps placement but re-imposes an exact
        string (or vice versa) fails here rather than passing silently."""
        body = self._body()
        assert "between the per-finding H2s and" in body
        assert (
            "the exact wording is not required: nothing downstream parses "
            "it mechanically" in body
        )

    def test_dismissed_section_drops_literal_heading_mandate(self):
        """The retracted verbatim-heading mandate must be gone, and replaced
        by explicit acceptance of a self-chosen heading -- pinning only the
        absence would pass on an edit that dropped the mandate without ever
        saying self-composed headings are fine, silently reintroducing the
        original failure mode under different wording."""
        body = self._body()
        assert "does not satisfy this" not in body
        assert (
            "prose explaining the same conclusion under a self-chosen "
            "heading" in body
        )

    def test_pointer_line_claim_is_corrected(self):
        """The false claim that the pointer line is the only surface
        /ready-for-review reads must be retracted and replaced with the
        true one -- /ready-for-review reads the whole findings file."""
        body = self._body()
        assert "the only surface `/ready-for-review` reads" not in body
        assert "reads the whole findings file after every dispatch" in body

    def test_step_two_records_dismissal_without_exact_heading_mandate(self):
        """Step 2's own recording instruction must drop its
        "(exact structure below)" pointer to the now-relaxed Output-format
        mandate and instead state the semantic requirement inline -- this
        covers step 2 independently of the Output-format section edit, so
        a revert of step 2 alone can't silently reintroduce a pointer to a
        section that no longer requires exact structure."""
        body = self._body()
        assert (
            "name the skill and the one-line reason, grouped with any "
            "other dismissals" in body
        )
        assert "(exact structure below)" not in body

    def test_inline_output_lists_dismissals_before_verdict(self):
        """Inline mode must list each dismissal with a reason, not just carry
        it in the opening count -- confirmed by mutation testing that the
        other four assertions stay green even if this sentence is dropped."""
        assert "list each with a one-line reason" in self._body()

    def test_inline_output_reports_dismissed_separately(self):
        """Inline mode's opening count line must separate dismissed skills
        from in-scope ones -- the only feasible verification for that
        branch, since findings_path dispatch never reaches inline mode."""
        assert "how many were dismissed as undecidable" in self._body()


class TestPrDescriptionTwoModeDispatch:
    """Pin pr-description's author/sync branching, in both directions.

    The wiring tests above only prove that /ready-for-review and /handoff name
    the skill; they stay green if its body collapses back to sync-only. That
    collapse is the regression that matters: with no author mode, step 5 has
    nothing to do on a branch without a PR, and the first PR body — the one
    reviewers read first — goes out unchecked again.

    The negative assertion is not redundant with the positive one. A body that
    describes author mode while still carrying the old "no open PR, stop"
    precondition is self-contradicting, and a presence-only check passes it.
    """

    def test_pr_description_declares_author_mode(self):
        """pr-description must dispatch to an authoring mode when no PR is open."""
        assert "No open PR → author mode." in _skill_file("pr-description").read_text()

    def test_pr_description_declares_sync_mode(self):
        """pr-description must still dispatch to the sync mode when a PR is open."""
        assert "Open PR → sync mode." in _skill_file("pr-description").read_text()

    def test_pr_description_has_no_stop_on_missing_pr_precondition(self):
        """pr-description must not retain a precondition that halts when no PR exists."""
        assert "no PR to sync" not in _skill_file("pr-description").read_text()


class TestPrDescriptionExternalStateCheck:
    """Pin the Flag-and-fix bullet that catches claims about state outside
    the repo (GH-476) -- a follow-up ticket said to be pending, a promise
    made as `will create`.

    Content-claim verification (the neighboring bullet) only re-reads files
    in this repo at HEAD; the marker-word bullet below only pattern-matches
    literal `TBD`/`pending` text. Neither catches "will be filed" or "not
    yet confirmed" going stale without a marker word, which is the gap this
    bullet closes.
    """

    def _body(self):
        return _skill_file("pr-description").read_text()

    def test_declares_source_verification_mechanism(self):
        """Mutation-tested: dropping this sentence collapses the bullet to a
        heading with no instruction on *how* to re-check a claim, which is
        indistinguishable from the bullet never having been added."""
        assert "at its own source" in self._body()

    def test_bounds_identifier_carry_to_named_trackers(self):
        """Mutation-tested independently of the mechanism assertion above --
        dropping only this clause leaves the re-check instruction intact but
        removes the guard against writing an internal tracker identifier
        into a body that never named that tracker (ciso-reviewer finding,
        GH-476 plan review)."""
        assert "already names that tracker" in self._body()

    def test_bullet_falls_between_content_claim_and_marker_checks(self):
        """Order-sensitive: a plain `in body` substring check would stay
        green even if the bullet were moved elsewhere in the list, silently
        breaking its own cross-references to the neighboring bullets it
        names by heading."""
        body = self._body()
        start = body.index("**Content-claim verification.**")
        end = body.index('`TBD` / `pending` / "to be updated" markers')
        assert end > start, "marker-bullet anchor must follow the content-claim bullet"
        segment = body[start:end]
        assert "External-state claims" in segment
        assert "at its own source" in segment

    def test_disambiguates_ci_wiring_from_ci_status(self):
        """Mutation-tested independently of the other three assertions in
        this class: dropping only this sentence leaves the re-check-and-
        rewrite instruction intact but removes the boundary against the
        neighboring Reviewer-action-items bullet, which strips CI-status
        placeholders outright -- without it, an implementer has no signal
        that this bullet and that one disagree on what to do with a CI
        claim, and can misclassify a CI-passing claim as re-verify-and-
        rewrite instead of strip (staff-sdet finding, GH-476 code review)."""
        assert "whether CI is *passing* is not" in self._body()


class TestPrDescriptionCostSectionWiring:
    """Wiring tripwire, not a behavioral test: the `## Cost` section's actual
    runtime behavior -- sentinel absent or mode not "dollars" -> block
    deleted if present; mode "dollars" -> sync regenerates; detached HEAD ->
    section omitted -- is validated behaviorally by
    claude/.claude/scripts/tests/test_pr_cost_section.py (real subprocess
    execution against pr-cost-section.sh), not here. The account-scoped
    mode-grammar gate and the config-dir resolution this skill body used to
    inline directly now live in that script instead (docs/worktree-bash-guard.md),
    so pinning their exact source shape a second time here would be
    redundant with that behavioral suite -- same reasoning this class
    already applies to install.sh's own _report_account_sentinel. This class
    only proves the delimiters and the script-call wiring are present in the
    skill body's source text.
    """

    def _body(self):
        return _skill_file("pr-description").read_text()

    def test_declares_pr_cost_delimiters(self):
        body = self._body()
        assert "<!-- pr-cost:start -->" in body
        assert "<!-- pr-cost:end -->" in body

    def test_declares_account_scoped_mode_gate(self):
        body = self._body()
        assert "pr-cost-disclosure" in body
        assert "~/.claude/scripts/pr-cost-section.sh" in body


class TestPrDescriptionProseTighteningPassWiring:
    """Wiring tripwire for the `## Prose tightening pass` section, mirroring
    TestPrDescriptionCostSectionWiring's treatment of the neighboring `##
    Cost section` above it -- pr-description has no behavioral test suite
    in this repo, so these assertions only prove the section is present,
    correctly placed, and dispatches by name; not that it executes
    correctly (execution requires manual verification)."""

    def _body(self):
        return _skill_file("pr-description").read_text()

    def _prose_section(self):
        """The `## Prose tightening pass` section's own text, isolated from
        the rest of the body -- an assertion against the whole body can pass
        on unrelated text from a neighboring section (e.g. `## Cost
        section`'s own CLAUDE_CONFIG_DIR reference) even when the new
        section itself dropped what's being checked for."""
        body = self._body()
        start = body.index("## Prose tightening pass")
        end = body.index("## Checks", start)
        return body[start:end]

    def test_declares_account_scoped_opt_out_sentinel(self):
        section = self._prose_section()
        assert "pr-description-tighten-prose-optout" in section
        assert "Cost section's gate above" in section

    def test_section_placed_before_checks(self):
        """Order-sensitive: the pass must run before ## Checks so the
        reader-coherence pass and content-claim verification validate the
        final, already-tightened bytes rather than pre-rewrite text."""
        body = self._body()
        prose_pass_index = body.index("## Prose tightening pass")
        checks_index = body.index("## Checks")
        assert prose_pass_index < checks_index, (
            "## Prose tightening pass must precede ## Checks"
        )
        cost_section_index = body.index("## Cost section")
        assert cost_section_index < prose_pass_index, (
            "## Prose tightening pass must follow ## Cost section"
        )

    def test_dispatches_tighten_prose_by_name(self):
        assert "dispatch `tighten-prose` by name" in self._body()


class TestRespondPrPromiseRedemption:
    """Pin the Guidelines bullet requiring a filed `will create` ticket to
    update every place that promise was already published (GH-476).

    Placed beside the existing stale-SHA rule (:111), which governs the
    identical shape -- a claim in an already-posted reply going stale
    during the session -- and prescribes the same correction-reply remedy,
    not respond-pr's separate in-place-PATCH path (:101), which is scoped
    to typos and factual errors in Claude's own comments.
    """

    def _body(self):
        return _skill_file("respond-pr").read_text()

    def test_requires_sweeping_already_published_artifacts(self):
        """Mutation-tested: dropping this clause leaves the heading with no
        obligation to act on it -- indistinguishable from no rule at all."""
        assert "correct every place that promise was already published" in self._body()

    def test_requires_correction_reply_for_prior_replies(self):
        """Mutation-tested independently of the other two assertions in this
        class: dropping only this clause leaves 'correct every place' with
        no named remedy for the already-posted-reply half of the sweep --
        regressing to only the PR-body half via the /pr-description pointer,
        with no test signal (staff-sdet finding, GH-476 code review)."""
        assert "post a correction reply for earlier replies" in self._body()

    def test_points_at_pr_description_for_the_body_surface(self):
        """Mutation-tested independently of the sweep assertion above --
        dropping only the pointer leaves 'correct every place' with no
        named mechanism for the PR-body half of that sweep, which
        respond-pr itself has no tool to perform."""
        assert "re-running `/pr-description`" in self._body()


class TestReadyForReviewBodyFileGuard:
    """Pin that step 6 rejects a whitespace-only body file, not merely an empty one.

    The guard protects an unrecoverable state: once a PR exists carrying an
    empty body, step 5 takes its sync path, which checks a body against branch
    state rather than authoring one from nothing. A bare `-s` test passes a
    file holding only a newline -- the shape a truncated write leaves behind --
    so the check must strip whitespace before testing for content.

    Reverting to `-s` is a silent regression: it reads as equivalent and passes
    every other test in this suite.
    """

    def test_body_file_check_strips_whitespace_before_testing_content(self):
        """step 6's guard must strip whitespace rather than rely on a byte-size test."""
        assert "tr -d '[:space:]'" in _skill_file("ready-for-review").read_text()


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

    def test_handoff_prewrite_checklist_crosschecks_section2_6(self):
        """handoff's pre-write checklist must verify §2.6 was populated before writing."""
        assert (
            "§2.6 is populated — a faithful task-list serialization with per-item ordinal, "
            'status, and blocking edges, or "None." — and carries the resume directive'
            in _skill_file("handoff").read_text()
        )


class TestTightenProseScopeCarveOutPhrases:
    """Regression guard on tighten-prose's scope rule, mirroring
    TestContinuityFileBucketCrosscheck's guard on brief's critical-rule
    text. REFERENCES.md is never loaded at runtime, so the durable-doc/
    plan-file scope boundary must live in the SKILL.md body itself -- this
    pins those carve-out phrases so a future edit can't silently drop them.
    """

    def test_declares_references_md_carve_out(self):
        assert "REFERENCES.md" in _skill_file("tighten-prose").read_text()

    def test_declares_plan_file_carve_out(self):
        assert ".claude/plans/" in _skill_file("tighten-prose").read_text()


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
        assert "Run `<config-dir>/scripts/marker.sh status` and paste its output verbatim" in body
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


class TestGlobalInstructionsDescribeMarkerGatesAsContentAddressed:
    """Pin the global instruction file's account of how review gates authorize.

    Every gate recomputes a hash of the state it guards and allows only on an
    exact match against a marker's stored content; the marker file's existence
    authorizes nothing. The distinction is not academic — an agent holding the
    presence model treats any marker on disk as a live gate, so it reads a
    stale marker as permission and a disarmed gate as an unexplained anomaly.

    This file is stowed to ~/.claude/CLAUDE.md and loads in every session, so
    a wrong account here is the most expensive place in the repo to carry one.
    """

    def test_global_instructions_state_gates_match_on_marker_content(self):
        """The §Safety marker bullet must describe content-matching and the deny
        outcome, not mere marker presence."""
        body = _GLOBAL_CLAUDE_MD.read_text()
        assert "Gates match on a marker's **content**" in body
        assert "a hash of the exact state that was reviewed" in body
        # The deny outcome is asserted separately from the match rule: text
        # that says gates match on content but stops short of naming what
        # happens when they don't leaves "stale marker" and "no marker"
        # looking like different outcomes, when both deny.
        assert "the stored hash stops matching and the gate denies" in body
        # Pinned positively as well as negatively. The guard below can only
        # catch a literal revert of the sentence this bullet replaced; any
        # paraphrase of the presence claim slips past it. Requiring the
        # contrast clause to survive leaves a reintroduced presence claim
        # nowhere to sit that does not read as self-contradiction.
        assert "not on the file's presence" in body

    def test_global_instructions_state_markers_outlive_their_session(self):
        """The bullet must say a still-covering marker counts across sessions.

        Separate from the deny rule above because the two are opposite faces
        of content-addressing and regress independently: drop the deny clause
        and a stale marker reads as permission; drop this one and a valid
        marker reads as expired, sending the next session to re-run a review
        the gate would already have released. The handoff skill carries a
        dedicated guard against the same session-keyed-expiry misconception,
        which is what marks it as recurring enough to pin in both files.
        """
        body = _GLOBAL_CLAUDE_MD.read_text()
        assert "keeps counting across sessions" in body

    def test_global_instructions_do_not_claim_gates_check_marker_presence(self):
        """Guard against reintroducing the presence-checked claim.

        It described an earlier gate implementation, which is what makes it
        the likely thing for a future edit to restore — the same reasoning
        that earns the handoff skill's session-expiry claim its own guard.
        A bare "presence" search would false-positive on the corrected text,
        which names presence to deny it, so pin the false predicate instead.
        This catches a literal revert only; the paraphrase class is held out
        by the positive contrast clause asserted above, not by this list.
        """
        body = _GLOBAL_CLAUDE_MD.read_text()
        assert "gate on their presence" not in body
        assert "gates on their presence" not in body


class TestGlobalInstructionsRequireMemoryBodyBeforeActing:
    """Pin the §Safety bullet distinguishing recalling a memory from acting on
    one (GH-429).

    A `MEMORY.md` index line is always loaded; the body it links to loads only
    on recall. Compressing an action-prescribing memory's body into an index
    line routinely drops the body's trigger condition, leaving a bare
    imperative that reads as a standing directive. Without this bullet, a
    session can act on the index line alone -- the exact gap this pins shut.
    """

    def _body(self):
        return _GLOBAL_CLAUDE_MD.read_text()

    def test_requires_reading_the_body_before_acting_on_it(self):
        """Mutation-tested: dropping this clause leaves the routing claim
        intact but removes the obligation to read the body and the
        prohibition on acting when its trigger isn't met -- regressing to a
        bullet that only describes the failure mode, not one that closes it."""
        body = self._body()
        assert "read the body file" in body
        assert "if its trigger condition is not met by what the user actually said this session, do not act" in body

    def test_distinguishes_citation_from_execution(self):
        """Mutation-tested independently of the assertion above -- dropping
        only this clause leaves the read-before-act obligation in place but
        removes the boundary that lets a session cite a memory from its
        index line without triggering an unnecessary body read."""
        assert "Citing a memory may rely on the index line; executing one may not" in self._body()


class TestMemorySkillPreservesActionPrescribingTrigger:
    """Pin the index-discipline bullet requiring an action-prescribing
    memory's index line to carry its body's trigger condition (GH-429).

    The existing compression-diff audit in this skill only fires on a diff
    that removes or shortens lines, so it never catches a newly authored
    index line dropping a trigger it never had a chance to preserve. This
    bullet closes that gap at first-authoring time.
    """

    def _body(self):
        return _skill_file("ai-instruction-and-memory-files").read_text()

    def test_requires_preserving_the_trigger_condition(self):
        """Mutation-tested: dropping this clause leaves the surrounding list
        with a format rule (character cap) but no content rule, so a
        newly authored index line that keeps the imperative and drops the
        guard passes review."""
        assert 'keep the body\'s guard: "run X when asked", never bare "run X"' in self._body()

    def test_requires_a_tiebreak_when_the_guard_will_not_fit(self):
        """Mutation-tested independently of the assertion above -- dropping
        only this clause leaves the trigger-preservation rule in direct
        conflict with the neighboring ≤150-character format bullet with no
        stated resolution, and the likely resolution under that pressure is
        silently dropping the guard -- the original defect this bullet
        exists to close (staff-sdet finding, GH-429 code review)."""
        assert "if the guard will not fit the character cap, the entry is not indexable as action-prescribing" in self._body()


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


class TestCorpusBudgetFunction:
    """Unit tests for corpus_budget_violations() — uses tmp_path fixtures."""

    def _make_skill(self, tmp_path, name: str, description: str = "", when_to_use: str = "", disable: bool = False) -> Path:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        lines = ["---"]
        if description:
            lines.append(f"description: {description!r}")
        if when_to_use:
            lines.append(f"when_to_use: {when_to_use!r}")
        if disable:
            lines.append("disable-model-invocation: true")
        lines.append("---")
        lines.append("")
        skill_file.write_text("\n".join(lines))
        return skill_file

    def test_under_budget_passes(self, tmp_path):
        f = self._make_skill(tmp_path, "a", description="x" * 100)
        assert corpus_budget_violations([f], 200) == []

    def test_over_budget_fails(self, tmp_path):
        f = self._make_skill(tmp_path, "a", description="x" * 100)
        violations = corpus_budget_violations([f], 99)
        assert len(violations) == 1
        assert "100 chars" in violations[0]

    def test_exact_boundary_passes(self, tmp_path):
        f = self._make_skill(tmp_path, "a", description="x" * 100)
        assert corpus_budget_violations([f], 100) == []

    def test_one_over_boundary_fails(self, tmp_path):
        f = self._make_skill(tmp_path, "a", description="x" * 101)
        assert corpus_budget_violations([f], 100) != []

    def test_empty_corpus_passes(self, tmp_path):
        assert corpus_budget_violations([], 8000) == []

    def test_disable_model_invocation_excluded(self, tmp_path):
        f = self._make_skill(tmp_path, "a", description="x" * 100, disable=True)
        # With disable=True the skill is excluded; should pass a budget of 0.
        assert corpus_budget_violations([f], 0) == []

    def test_sums_description_and_when_to_use(self, tmp_path):
        f = self._make_skill(tmp_path, "a", description="x" * 50, when_to_use="y" * 60)
        # Total is 110 chars. Budget of 109 should fail; 110 should pass.
        assert corpus_budget_violations([f], 110) == []
        assert corpus_budget_violations([f], 109) != []

    def test_names_top_offenders_on_failure(self, tmp_path):
        files = [
            self._make_skill(tmp_path, f"skill_{i}", description="x" * (100 + i * 10))
            for i in range(6)
        ]
        violations = corpus_budget_violations(files, 1)
        assert violations
        # Violation message should name at most 5 offenders.
        assert violations[0].count("chars") >= 5  # each offender line ends "N chars"


_DISPOSITION_RULE_ANCHOR_RE = re.compile(r"<!-- DISPOSITION_RULE:(\S+) (start|end) -->")

# The two DISPOSITION_RULE anchor regions in the corpus. Asserted as an exact
# set, not just "each found anchor is non-trivial" — a corpus scan alone
# passes vacuously if an entire anchor pair is deleted.
_EXPECTED_DISPOSITION_RULE_ANCHORS = {
    ("code-review", "code-review-defer-invariant"),
    ("plan-review", "plan-review-fix-or-ask"),
}


def _all_skill_md_paths() -> list[Path]:
    """Every SKILL.md under the stowed skills dir and plugin skill dirs."""
    paths = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if _PLUGINS_DIR.exists():
        paths += sorted(_PLUGINS_DIR.glob("*/skills/*/SKILL.md"))
    return paths


# --- Trigger A regression: a $(...)-assigned variable referenced later in the
# same fenced block. See docs/worktree-bash-guard.md for the full trigger
# taxonomy this guards one shape of. Fence detection reuses the module-level
# _FENCE_OPEN_RE (defined further below, keyed on the fence delimiter itself,
# not a bash/sh language tag) so an untagged ``` fence is scanned the same as
# a tagged ```bash one.

# Matches an assignment at the start of a statement -- line start, or after a
# statement separator (;, &&, ||) -- optionally preceded by local/export/
# declare/readonly, so `local FOO=$(cmd)` and `echo x; FOO=$(cmd)` are found
# the same as a bare leading `FOO=$(cmd)`. Not a full shell parser (a `;`
# inside a string literal would also split here); over-flagging is the safe
# direction for this scan, matching _closed_fence_line_indices's own bias.
_TRIGGER_A_ASSIGN_RE = re.compile(
    r"(?:^|[;&|]\s*)\s*(?:local|export|declare|readonly)?\s*([A-Za-z_][A-Za-z0-9_]*)=\$\("
)
_TRIGGER_A_EXCLUSION_MARKER_RE = re.compile(r"<!-- (HOOK_TEST_FIXTURE|HOOK_SCRIPT_CONTENT_EXAMPLE):")


def _fenced_code_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Every *closed* fenced code block as (open_index, content_lines).

    Mirrors _closed_fence_line_indices's own closing-fence detection (a bare
    run of the opener's character, at least as long as the opener, with no
    trailing info string) but returns each block's own content instead of a
    flat index set — the Trigger-A scan needs to look inside each block for
    the assign-then-reference shape, not just which lines are code.
    """
    blocks: list[tuple[int, list[str]]] = []
    open_index: int | None = None
    open_run = ""
    for index, line in enumerate(lines):
        if open_index is None:
            fence_match = _FENCE_OPEN_RE.match(line)
            if fence_match:
                open_index, open_run = index, fence_match.group(1)
            continue
        candidate = line.strip()
        if candidate and set(candidate) == {open_run[0]} and len(candidate) >= len(open_run):
            blocks.append((open_index, lines[open_index + 1 : index]))
            open_index = None
    return blocks


def _fence_excluded_by_marker(lines: list[str], open_index: int) -> bool:
    """True when the nearest non-blank line before the fence opener is a
    HOOK_TEST_FIXTURE/HOOK_SCRIPT_CONTENT_EXAMPLE marker comment — an
    explicit, structural opt-in a pytest-executed fixture or a documentation
    example (never typed into an agent's Bash tool) uses to exempt itself."""
    for index in range(open_index - 1, -1, -1):
        stripped = lines[index].strip()
        if stripped == "":
            continue
        return bool(_TRIGGER_A_EXCLUSION_MARKER_RE.search(stripped))
    return False


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
    repo_root = Path(__file__).resolve().parents[4]
    found_files: list[Path] = []
    for base in [
        repo_root / "claude" / ".claude" / "skills",
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

    _REPO_ROOT = Path(__file__).resolve().parents[4]
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
    repo_root = Path(__file__).resolve().parents[4]
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
    repo_root = Path(__file__).resolve().parents[4]
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


# --- Citation placement: URLs live in REFERENCES.md, not in a SKILL.md body ---
#
# A SKILL.md body loads into the session on every skill fire; REFERENCES.md never
# does. A citation URL in a body is therefore re-read on every fire by a reader
# that cannot click it. URLs that are *functional* (a namespace URI a parser
# needs, an attack payload, a placeholder) are a different thing entirely, and in
# this corpus they are always inside a fenced code block or an inline code span —
# so stripping code regions first separates the two classes with no allowlist.

_URL_RE = re.compile(r"https?://")

# An opening fence: up to 3 leading spaces, then 3+ backticks or 3+ tildes.
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# An inline code span: a run of N backticks, content, then a matching run of N.
# The lookarounds pin both delimiters to *whole* backtick runs. Without them the
# opener backtracks to a shorter run when no equal-length closer exists — so
# ````text``` would match as a 3-tick span, and any URL between the delimiters
# would be blanked even though CommonMark leaves the whole thing as literal text.
# That direction of error is the dangerous one: it exempts a real citation from
# the scan. `.` excludes newlines, so a span stays on one line.
_INLINE_CODE_RE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`)(?P<body>.+?)(?<!`)(?P=ticks)(?!`)")


def _blank_frontmatter(markdown_text: str) -> str:
    """Blank the leading YAML frontmatter block, preserving the line count.

    No current `description` holds a URL, but frontmatter is metadata rather than
    body prose, so excluding it makes the invariant unambiguous rather than
    accidentally true.

    Deliberately does not reuse validate_skill_structure.parse_frontmatter, which
    is imported in this module: that function answers "what are the frontmatter
    values" and returns a parsed dict, while this one answers "which lines are
    frontmatter" and must return text with the line count intact so violation
    line numbers stay accurate. Same delimiter, different questions.
    """
    lines = markdown_text.split("\n")
    if not lines or lines[0].strip() != "---":
        return markdown_text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join([""] * (index + 1) + lines[index + 1 :])
    return markdown_text


def _closed_fence_line_indices(lines: list[str]) -> set[int]:
    """Indices of every line belonging to a *closed* fenced code block.

    An unterminated fence contributes nothing. CommonMark says an unclosed fence
    runs to the end of the document, and this scan deliberately diverges: under
    that rule a contributor who forgets one closing fence would silently exempt
    every line after it — including real citation URLs — with no visible symptom.
    Treating the unterminated remainder as prose errs toward over-flagging, which
    surfaces as a test failure pointing at the malformed fence. Every gap in this
    scan is deliberately biased that direction.
    """
    inside: set[int] = set()
    open_index: int | None = None
    open_run = ""

    for index, line in enumerate(lines):
        if open_index is None:
            fence_match = _FENCE_OPEN_RE.match(line)
            if fence_match:
                open_index, open_run = index, fence_match.group(1)
            continue
        # A closing fence is a bare run of the opener's character, at least as
        # long as the opener, with no trailing info string.
        candidate = line.strip()
        if candidate and set(candidate) == {open_run[0]} and len(candidate) >= len(open_run):
            inside.update(range(open_index, index + 1))
            open_index = None

    return inside


def _blank_code_regions(markdown_text: str) -> str:
    """Blank fenced code blocks and inline code spans, preserving the line count.

    Fences are resolved before inline spans so a ``` delimiter is never misread as
    three consecutive single-backtick span openers. Blanked regions become empty
    lines rather than being deleted, so a violation's reported line number still
    matches the real file.
    """
    lines = markdown_text.split("\n")
    fenced = _closed_fence_line_indices(lines)
    return "\n".join(
        "" if index in fenced else _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)
        for index, line in enumerate(lines)
    )


def _all_skill_md_files() -> list[Path]:
    """Every SKILL.md in the repo, across all three roots that hold one.

    Single-level globs only — never rglob or `**`. `.claude/worktrees/` holds
    full checkouts of this repo whose SKILL.md files carry live pre-fix URLs, and
    a recursive glob from the repo root would scan them and fail on another
    branch's content.
    """
    repo_root = Path(__file__).resolve().parents[4]
    found: list[Path] = []
    for base, pattern in [
        (repo_root / "claude" / ".claude" / "skills", "*/SKILL.md"),
        (repo_root / ".claude" / "skills", "*/SKILL.md"),
        (repo_root / "plugins", "*/skills/*/SKILL.md"),
    ]:
        matched = list(base.glob(pattern))
        # Per-root, not just in aggregate: Path.glob on a missing directory
        # returns empty rather than raising, so a rename that relocates one root
        # would otherwise shrink the scanned corpus silently while the other two
        # roots kept the suite green.
        assert matched, f"{base}/{pattern} matched no SKILL.md — this glob root is wrong"
        found.extend(matched)
    return sorted(found)


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
    repo_root = Path(__file__).resolve().parents[4]
    skill_files = _all_skill_md_files()

    violations: list[str] = []
    for path in skill_files:
        prose = _blank_code_regions(_blank_frontmatter(path.read_text()))
        for lineno, line in enumerate(prose.split("\n"), start=1):
            if _URL_RE.search(line):
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


_INVALID_SKIP_HEADING = "**Invalid skip rationales.**"
_SKIP_RATIONALE_LABEL_RE = re.compile(r'^[-*] \*\*"(?P<label>[^"]+)"\*\*')
_TOP_LEVEL_LIST_ITEM_RE = re.compile(r"^[-*] ")


def _invalid_skip_rationale_labels(path: Path) -> set[str]:
    """Bolded-and-quoted labels under a file's "Invalid skip rationales" list.

    Scoped to that one list rather than the whole file: code-review/SKILL.md
    carries further bolded-quoted labels in its DEFER-criteria section, which an
    unscoped scan would silently fold in and make the comparison meaningless.

    The list is delimited by indentation, not by "first line that isn't a
    bullet". Blank lines and indented continuations or sub-bullets stay inside
    the list; only an unindented non-list line closes it. Ending the scan at the
    first indented line instead would truncate both files at the same point and
    let genuinely divergent labels below it compare equal — a silent pass on the
    exact drift this guards against.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_index = next(
        (i for i, line in enumerate(lines) if line.startswith(_INVALID_SKIP_HEADING)),
        None,
    )
    if heading_index is None:
        raise AssertionError(f"{path} no longer contains {_INVALID_SKIP_HEADING!r}")

    labels: set[str] = set()
    for line in lines[heading_index + 1 :]:
        if not line.strip() or line[0].isspace():
            continue
        match = _SKIP_RATIONALE_LABEL_RE.match(line)
        if match:
            labels.add(match.group("label"))
        elif not _TOP_LEVEL_LIST_ITEM_RE.match(line):
            break
    return labels


def test_invalid_skip_rationale_labels_match_across_review_skills() -> None:
    """/code-review and /plan-review must refuse the same set of skip rationales.

    Both lists exist so a session reaching the spawn-skip decision through
    either skill meets the same refusals. The rebuttal sentence after each label
    is skill-specific by design and is not compared; the label set is shared, and
    nothing else keeps the two files in step when one is edited on its own.
    """
    code_review_labels = _invalid_skip_rationale_labels(_skill_file("code-review"))
    routing_labels = _invalid_skip_rationale_labels(
        _skill_file("plan-review").parent / "ROUTING.md"
    )

    # Guards against both extractions returning empty — a bullet-format change
    # would otherwise make set equality pass vacuously.
    assert code_review_labels, "extracted no skip rationales from code-review/SKILL.md"

    assert code_review_labels == routing_labels, (
        "Invalid skip rationale labels drifted between code-review/SKILL.md and "
        "plan-review/ROUTING.md — both lists must name the same rationales.\n"
        f"  only in code-review/SKILL.md:    {sorted(code_review_labels - routing_labels)}\n"
        f"  only in plan-review/ROUTING.md:  {sorted(routing_labels - code_review_labels)}"
    )


_SCOPE_RULE_ANCHOR_RE = re.compile(r"<!-- SCOPE_RULE:(\S+) (start|end) -->")
_SCOPE_EXEMPT_ROW_ANCHOR_RE = re.compile(r"<!-- SCOPE_EXEMPT_ROW (start|end) -->")

# The three anchor regions the staged-diff scope-boundary redesign introduced.
# Asserted as an exact set, not just "each found anchor is non-trivial" — a
# corpus scan alone passes vacuously if an entire anchor pair is deleted.
_EXPECTED_SCOPE_ANCHORS = {
    ("code-review", "SCOPE_RULE:code-review-staged-diff-only"),
    ("code-review", "SCOPE_EXEMPT_ROW"),
    ("ready-for-review", "SCOPE_RULE:ready-for-review-cumulative-unnarrowed"),
}

_SECURITY_CONTROLS_ROW = "Adds/modifies security controls"


def _extract_scope_anchor_region(skill_md_path: Path, marker_name: str) -> str:
    """Extract text strictly between a `<!-- <marker_name> start/end -->` pair.

    Mirrors extract_governing_rule()'s (evals/run_skill_evals.py) presence/
    order/duplicate validation, parameterized over the marker name instead of
    that function's hard-coded `DISPOSITION_RULE:` prefix — reusing that
    prefix would silently enroll a scope rule in the disposition-fidelity
    eval, which is why this anchor namespace needs its own small parser
    rather than calling extract_governing_rule() directly.
    """
    text = skill_md_path.read_text()
    start_marker = f"<!-- {marker_name} start -->"
    end_marker = f"<!-- {marker_name} end -->"
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count > 1 or end_count > 1:
        raise ValueError(
            f"{marker_name!r} anchor in {skill_md_path}: appears more than once "
            f"(start x{start_count}, end x{end_count}) — duplicate anchor names "
            "are not supported, rename one of them"
        )
    start_idx = text.find(start_marker)
    end_idx = text.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        raise ValueError(
            f"{marker_name!r} anchor not found (or incomplete) in {skill_md_path} "
            f"— start present: {start_idx != -1}, end present: {end_idx != -1}"
        )
    if end_idx < start_idx:
        raise ValueError(
            f"{marker_name!r} anchor in {skill_md_path}: end marker appears before start marker"
        )
    return text[start_idx + len(start_marker) : end_idx].strip()


class TestExtractScopeAnchorRegion:
    """Direct branch coverage for _extract_scope_anchor_region's three raise
    branches, exercised with tiny inline fixtures rather than waiting for the
    real corpus to happen to contain such a defect. Mirrors
    TestTriggerAFenceScan's pattern of testing a small parsing helper
    branch-by-branch with literal string fixtures before the corpus-wide
    sweep runs.
    """

    _MARKER = "SCOPE_RULE:example"

    def _write(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "SKILL.md"
        path.write_text(content)
        return path

    def test_happy_path_extracts_enclosed_text(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} start -->\nsome rule text\n<!-- {self._MARKER} end -->",
        )
        assert _extract_scope_anchor_region(path, self._MARKER) == "some rule text"

    def test_duplicate_start_marker_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} start -->\nfirst\n<!-- {self._MARKER} start -->\n"
            f"second\n<!-- {self._MARKER} end -->",
        )
        with pytest.raises(ValueError, match="more than once"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_duplicate_end_marker_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} start -->\ntext\n<!-- {self._MARKER} end -->\n"
            f"<!-- {self._MARKER} end -->",
        )
        with pytest.raises(ValueError, match="more than once"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_start_present_end_missing_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, f"<!-- {self._MARKER} start -->\ntext")
        with pytest.raises(ValueError, match="not found"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_end_present_start_missing_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, f"text\n<!-- {self._MARKER} end -->")
        with pytest.raises(ValueError, match="not found"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_end_before_start_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} end -->\ntext\n<!-- {self._MARKER} start -->",
        )
        with pytest.raises(ValueError, match="before start"):
            _extract_scope_anchor_region(path, self._MARKER)


def test_scope_rule_anchors_present() -> None:
    """Every SCOPE_RULE:/SCOPE_EXEMPT_ROW anchor pair is present, ordered, and
    non-trivial in length — the SCOPE_RULE:/SCOPE_EXEMPT_ROW-namespace mirror
    of test_disposition_rule_anchors_present above.
    """
    MIN_SCOPE_TEXT_CHARS = 20  # a single stripped char would pass a bare "non-empty" check

    found: set[tuple[str, str]] = set()
    for skill_md_path in _all_skill_md_paths():
        text = skill_md_path.read_text()
        skill_name = skill_md_path.parent.name

        marker_names = {f"SCOPE_RULE:{m.group(1)}" for m in _SCOPE_RULE_ANCHOR_RE.finditer(text)}
        if _SCOPE_EXEMPT_ROW_ANCHOR_RE.search(text):
            marker_names.add("SCOPE_EXEMPT_ROW")

        for marker_name in marker_names:
            found.add((skill_name, marker_name))
            enclosed = _extract_scope_anchor_region(skill_md_path, marker_name)
            assert len(enclosed) >= MIN_SCOPE_TEXT_CHARS, (
                f"{skill_md_path}: {marker_name} encloses only {len(enclosed)} "
                f"non-whitespace chars after stripping — looks deleted or gutted"
            )

    assert found == _EXPECTED_SCOPE_ANCHORS, (
        "Scope-boundary anchors drifted from the expected set — a region was "
        "added, renamed, or deleted.\n"
        f"  expected but missing: {sorted(_EXPECTED_SCOPE_ANCHORS - found)}\n"
        f"  found but unexpected: {sorted(found - _EXPECTED_SCOPE_ANCHORS)}"
    )


def test_scope_exempt_row_nested_within_outer_rule() -> None:
    """SCOPE_EXEMPT_ROW must be textually nested inside
    SCOPE_RULE:code-review-staged-diff-only's start/end pair, not merely
    present somewhere in the file alongside it — the design (see the
    SCOPE_RULE region's own prose) is that the exempt row's match-narrowing
    carve-out lives inside the outer rule, not as an unrelated sibling anchor.
    """
    skill_md_path = _skill_file("code-review")
    text = skill_md_path.read_text()

    outer_start = text.find("<!-- SCOPE_RULE:code-review-staged-diff-only start -->")
    outer_end = text.find("<!-- SCOPE_RULE:code-review-staged-diff-only end -->")
    exempt_start = text.find("<!-- SCOPE_EXEMPT_ROW start -->")
    assert -1 not in (outer_start, outer_end, exempt_start), (
        f"{skill_md_path}: one of the SCOPE_RULE/SCOPE_EXEMPT_ROW start/end anchors is missing"
    )
    assert outer_start < exempt_start < outer_end, (
        f"{skill_md_path}: SCOPE_EXEMPT_ROW (offset {exempt_start}) is not nested inside "
        f"SCOPE_RULE:code-review-staged-diff-only (offsets {outer_start}-{outer_end})"
    )


_CAUSAL_REACH_KEYWORDS = ("causes", "activates", "newly reaches")

# Keyword presence alone would pass a reword that keeps all three verbs but
# flips the clause from mandatory ("stays in scope") to advisory ("may
# optionally be flagged") — the modal-verb and disqualifier checks below
# catch that flip.
_MANDATORY_MODAL_TOKENS = ("stays", "is", "remains")
# Word-boundary regex, not a bare substring check — "is" as a raw substring
# would match inside unrelated words ("this", "responsibility") and pass
# almost any sentence regardless of actual modality.
_MANDATORY_MODAL_RE = re.compile(
    r"\b(?:" + "|".join(_MANDATORY_MODAL_TOKENS) + r")\b"
)
_ADVISORY_DISQUALIFIER_RE = re.compile(
    r"may optionally|at\s+\S+'?s?\s+discretion|if\s+\S+\s+chooses", re.IGNORECASE
)
# Catches a reword that keeps the mandatory modal ("is", "stays") but negates
# it into an exception, e.g. "...is not automatically in scope... unless
# separately raised" — _ADVISORY_DISQUALIFIER_RE's discretionary-softening
# phrasings don't match that shape.
# Known false-positive class: a legitimate strengthening reword that uses one
# of these words as reinforcement rather than a carve-out (e.g. "...stays in
# scope, not merely as a suggestion") also trips this regex, since the match
# isn't bound to the negated clause. No current SKILL.md content hits this;
# tighten the pattern if a real strengthening reword starts failing here.
_NEGATION_DISQUALIFIER_RE = re.compile(
    r"\bnot\b|\bno longer\b|\bunless\b", re.IGNORECASE
)


# Naive sentence-boundary regex that already mis-splits on abbreviations
# like `(e.g.` elsewhere in the SCOPE_RULE region it parses. A future edit
# adding a parenthetical inside the isolated causal-reach sentence could
# corrupt this check.
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def test_scope_rule_region_states_causal_reach() -> None:
    """The causal-reach guarantee is the one thing separating "boundary
    bounds the sweep" from "boundary bounds everything" — the region-level
    non-trivial-length check above would not by itself catch this guarantee
    being deleted or reworded into weakness while the rest of the region
    stays intact, so it needs its own content-level assertion. Checks for
    the three causal verbs rather than the full sentence verbatim, so a
    legitimate reword (a copy-edit pass, a clause reorder) does not
    false-fail this test — see test_disposition_rule_anchors_present's
    docstring for the same discipline applied to DISPOSITION_RULE anchors.
    The mandatory-modal and advisory-disqualifier checks below are a narrow
    tripwire for one anticipated reword shape (a discretionary softening),
    not general grammatical-mood detection. Expect the disqualifier list
    to need broadening as new phrasings surface.
    """
    skill_md_path = _skill_file("code-review")
    region = _extract_scope_anchor_region(skill_md_path, "SCOPE_RULE:code-review-staged-diff-only")
    missing = [kw for kw in _CAUSAL_REACH_KEYWORDS if kw not in region]
    assert not missing, (
        f"{skill_md_path}: SCOPE_RULE:code-review-staged-diff-only no longer "
        f"states the causal-reach guarantee — missing keyword(s) {missing!r}"
    )

    causal_reach_sentence = next(
        (s for s in _sentences(region) if any(kw in s for kw in _CAUSAL_REACH_KEYWORDS)),
        None,
    )
    assert causal_reach_sentence is not None, (
        f"{skill_md_path}: could not isolate the sentence containing the "
        "causal-reach keywords for modal-verb checking"
    )
    assert _MANDATORY_MODAL_RE.search(causal_reach_sentence), (
        f"{skill_md_path}: causal-reach sentence {causal_reach_sentence!r} has no "
        f"mandatory modal verb {_MANDATORY_MODAL_TOKENS!r} — it may have been "
        "reworded from mandatory to advisory"
    )
    advisory_match = _ADVISORY_DISQUALIFIER_RE.search(causal_reach_sentence)
    assert advisory_match is None, (
        f"{skill_md_path}: causal-reach sentence contains advisory phrasing "
        f"{advisory_match.group()!r} — the clause must stay mandatory, not discretionary"
    )
    negation_match = _NEGATION_DISQUALIFIER_RE.search(causal_reach_sentence)
    assert negation_match is None, (
        f"{skill_md_path}: causal-reach sentence contains negation phrasing "
        f"{negation_match.group()!r} — the clause must stay affirmative, not carved "
        "into an exception"
    )


class TestNegationDisqualifierMatchesBypassPhrasing:
    """Direct coverage for _NEGATION_DISQUALIFIER_RE's match branch, mirroring
    TestChangeTypeTableLeftColumns's literal-fixture pattern — real SKILL.md
    content never triggers this regex, so its positive-match path needs a
    dedicated fixture.
    """

    def test_negated_causal_reach_sentence_matches(self, tmp_path: Path) -> None:
        """Pins the bypass this regex closes: a reworded clause that keeps
        the mandatory modal and causal-reach keywords but negates them into
        an exception ("is not ... unless ...").
        """
        path = tmp_path / "SKILL.md"
        path.write_text(
            "<!-- SCOPE_RULE:code-review-staged-diff-only start -->\n"
            "A change that causes, activates, or newly reaches new behavior "
            "is not automatically in scope unless separately raised.\n"
            "<!-- SCOPE_RULE:code-review-staged-diff-only end -->"
        )
        region = _extract_scope_anchor_region(path, "SCOPE_RULE:code-review-staged-diff-only")
        causal_reach_sentence = next(
            (s for s in _sentences(region) if any(kw in s for kw in _CAUSAL_REACH_KEYWORDS)),
            None,
        )
        assert causal_reach_sentence is not None
        assert _NEGATION_DISQUALIFIER_RE.search(causal_reach_sentence) is not None


def _change_type_table_left_columns(skill_md_path: Path) -> list[str]:
    """Left-column shorthand of every data row in the Change-type table.

    Truncates a left-column cell at its first embedded `|`. Not exercised
    by any current Change-type row. A future row using pipe-containing
    inline-code shorthand would truncate here. At the current call sites
    (`test_scope_exempt_row_resolves_to_real_change_type_row`,
    `test_scope_exempt_row_excludes_security_controls_row`), a resulting
    mismatch surfaces as a loud assertion failure, not silently.
    """
    lines = skill_md_path.read_text().splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "| Change type | Spawn / invoke |"),
        None,
    )
    assert header_index is not None, f"{skill_md_path}: Change-type table header not found."

    rows = []
    for line in lines[header_index + 2 :]:  # skip header row and the "|---|---|" separator
        if not line.startswith("|"):
            break
        rows.append(line.split("|")[1].strip())
    return rows


class TestChangeTypeTableLeftColumns:
    """Direct coverage for _change_type_table_left_columns's `line.split("|")`
    parse, mirroring TestExtractScopeAnchorRegion's literal-fixture pattern.
    """

    def test_embedded_pipe_in_left_column_truncates(self, tmp_path: Path) -> None:
        """A left-column cell containing inline code with an embedded `|`
        (`` `a | b` ``) truncates at that first `|` — this documents the
        helper's current split-based behavior rather than changing it.
        """
        path = tmp_path / "SKILL.md"
        path.write_text(
            "| Change type | Spawn / invoke |\n"
            "|-------------|----------------|\n"
            "| Uses inline code `a | b` in shorthand | `some-reviewer` |\n"
        )
        assert _change_type_table_left_columns(path) == ["Uses inline code `a"]


def test_scope_exempt_row_resolves_to_real_change_type_row() -> None:
    """SCOPE_EXEMPT_ROW's enclosed shorthand must exact-match a real
    Change-type table row's left column — guards against silent drift if
    that row is reworded without updating the anchor.
    """
    skill_md_path = _skill_file("code-review")
    exempt_shorthand = _extract_scope_anchor_region(skill_md_path, "SCOPE_EXEMPT_ROW")
    real_rows = _change_type_table_left_columns(skill_md_path)
    assert exempt_shorthand in real_rows, (
        f"SCOPE_EXEMPT_ROW's shorthand {exempt_shorthand!r} does not exact-match "
        f"any Change-type table row in {skill_md_path}"
    )


def test_scope_exempt_row_excludes_security_controls_row() -> None:
    """Guards against a future edit sliding the *Adds/modifies security
    controls* row's shorthand into SCOPE_EXEMPT_ROW, which would narrow
    security spawn decisions — the match-narrowing carve-out is prose-only
    by design.
    """
    skill_md_path = _skill_file("code-review")
    exempt_shorthand = _extract_scope_anchor_region(skill_md_path, "SCOPE_EXEMPT_ROW")
    assert exempt_shorthand != _SECURITY_CONTROLS_ROW

    real_rows = _change_type_table_left_columns(skill_md_path)
    assert _SECURITY_CONTROLS_ROW in real_rows, (
        f"{skill_md_path}: expected Change-type row {_SECURITY_CONTROLS_ROW!r} not "
        "found — test fixture drifted from the table"
    )


_PLAN_REVIEW_REFERENCES_MD = _skill_file("plan-review").parent / "REFERENCES.md"

_STEP4_HEADING = "## Step 4 — Design-fitness gate"
_MAPPING_TABLE_HEADING = "## Tripwire → CLAUDE.md principle mapping"
_FOUNDATION_RULES_HEADING = "## Foundation-tripwire rules — surfacing incident"

_TRIPWIRE_BULLET_RE = re.compile(r"^\s{2,}-\s+\*\*(?P<name>[^*]+)\.\*\*")
_MAPPING_ROW_RE = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|")

# Bumping this is the point of the ratchet: an eighth tripwire must also
# gain a mapping-table row and a provenance paragraph, or this constant
# stays stale and the count assertion below catches the omission.
_EXPECTED_TRIPWIRE_COUNT = 7


def _section_between(
    lines: list[str], start_heading: str, path: Path
) -> tuple[int, int]:
    """Return (start_idx, end_idx) bounding the section under `start_heading`.

    end_idx is exclusive, at the next line starting with '## ' (or len(lines)
    if the section runs to EOF — unlike test_reconciliation_block_consistency.py's
    extractor, EOF is not itself a failure here, since none of the four
    headings this module bounds is currently last in its file). The start
    heading is asserted found, not inferred — a renamed or deleted heading
    would otherwise extract as empty and compare equal to another empty
    extraction.
    """
    start_idx = next(
        (i for i, line in enumerate(lines) if line.rstrip("\n") == start_heading),
        None,
    )
    assert start_idx is not None, f"{path}: {start_heading!r} heading not found."

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break
    return start_idx, end_idx


_READY_FOR_REVIEW_STEP3_HEADING = "## 3. Code review (halt on findings)"


def test_ready_for_review_step3_never_produces_a_staged_diff() -> None:
    """Structural regression for SCOPE_RULE:ready-for-review-cumulative-unnarrowed's
    premise. Step 3 must always dispatch via `pr-diff-against-base.sh`. It must
    never contain a `git diff --cached` invocation, so the diff it hands
    `/code-review` can never satisfy the narrowing precondition's staged-diff
    half. A fixture-based structural check, not a live model call.
    """
    skill_md_path = _skill_file("ready-for-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _READY_FOR_REVIEW_STEP3_HEADING, skill_md_path)
    section_text = "".join(lines[start_idx:end_idx])
    assert "pr-diff-against-base.sh" in section_text, (
        f"{skill_md_path}: Step 3 no longer dispatches via pr-diff-against-base.sh"
    )
    assert "git diff --cached" not in section_text, (
        f"{skill_md_path}: Step 3 must never produce a staged-diff (git diff "
        "--cached) review basis — that's the commit-gate pass's job, not the "
        "cumulative sweep's"
    )


def _step4_tripwire_names() -> set[str]:
    """Bolded tripwire names from plan-review/SKILL.md's Step 4 bullet list.

    `_TRIPWIRE_BULLET_RE` requires 2+ leading spaces, which the tripwire
    bullets carry (nested under item 3's "Are foundation-correctness
    tripwires clean?") and Step 4's other bulleted list ("Markers of
    over-elaboration") does not — that list is flush-left today, but the
    indentation requirement holds even if a future bold bullet is added to
    it, unlike a check that merely assumes today's bullets stay non-bold.
    """
    path = _skill_file("plan-review")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _STEP4_HEADING, path)
    names = {
        match.group("name")
        for line in lines[start_idx:end_idx]
        if (match := _TRIPWIRE_BULLET_RE.match(line))
    }
    assert names, f"{path}: no bolded tripwire bullets found under {_STEP4_HEADING!r}."
    return names


def _mapping_table_names() -> set[str]:
    """First-column names from REFERENCES.md's tripwire → principle table."""
    path = _PLAN_REVIEW_REFERENCES_MD
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _MAPPING_TABLE_HEADING, path)
    names = set()
    for line in lines[start_idx:end_idx]:
        match = _MAPPING_ROW_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        if name in ("SKILL.md Step 4 tripwire", "---"):
            continue
        names.add(name)
    assert names, f"{path}: no rows found in the table under {_MAPPING_TABLE_HEADING!r}."
    return names


def _foundation_rules_section_text() -> str:
    """Raw text of REFERENCES.md's 'Foundation-tripwire rules' section."""
    path = _PLAN_REVIEW_REFERENCES_MD
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, _FOUNDATION_RULES_HEADING, path)
    return "".join(lines[start_idx:end_idx])


def _has_provenance_citation(name: str, section_text: str) -> bool:
    """True if `name` appears inside a "tripwire(s) (...)" citation.

    Every existing tripwire mention in this section follows one shape —
    "the sixth tripwire (unjustified given)", "foundation tripwires
    (over-powered primitive, compounding layers, self-referential
    findings)" — so matching that shape specifically (name inside
    parentheses immediately preceded by "tripwire"/"tripwires") rejects a
    stray mention of the name elsewhere in the section that isn't a real
    citation, which a bare substring search would accept.
    """
    pattern = re.compile(
        r"\btripwires?\s*\([^)]*\b" + re.escape(name.lower()) + r"\b[^)]*\)",
        re.IGNORECASE,
    )
    return bool(pattern.search(section_text))


class TestStep4TripwireProvenanceParity:
    """Every Step 4 foundation tripwire in plan-review/SKILL.md must have a
    matching row in REFERENCES.md's mapping table and a provenance paragraph
    naming it. REFERENCES.md states both requirements in prose, but nothing
    previously enforced them — a tripwire added to SKILL.md without the
    paired REFERENCES.md updates reads as "this rule has no recorded origin,"
    and the resulting drift across three sites is invisible until someone
    compares the files line by line.
    """

    def test_tripwire_count_matches_expected(self):
        names = _step4_tripwire_names()
        assert len(names) == _EXPECTED_TRIPWIRE_COUNT, (
            f"Expected {_EXPECTED_TRIPWIRE_COUNT} Step 4 tripwires, found "
            f"{len(names)}: {sorted(names)}. If a tripwire was intentionally "
            "added or removed, update _EXPECTED_TRIPWIRE_COUNT alongside the "
            "mapping-table row and provenance paragraph this test also checks."
        )

    def test_tripwire_names_match_mapping_table(self):
        step4_names = _step4_tripwire_names()
        table_names = _mapping_table_names()
        assert step4_names == table_names, (
            "plan-review/SKILL.md's Step 4 tripwires and REFERENCES.md's "
            "mapping table have diverged.\n"
            f"  only in SKILL.md:      {sorted(step4_names - table_names)}\n"
            f"  only in REFERENCES.md: {sorted(table_names - step4_names)}"
        )

    def test_every_tripwire_has_a_provenance_paragraph(self):
        step4_names = _step4_tripwire_names()
        section_text = _foundation_rules_section_text()
        missing = [
            name
            for name in sorted(step4_names)
            if not _has_provenance_citation(name, section_text)
        ]
        assert not missing, (
            "Step 4 tripwire(s) with no 'tripwire (name)' citation under "
            f"{_FOUNDATION_RULES_HEADING!r} in REFERENCES.md: {missing}"
        )


# Scoped to the three SKILL.md files the transcript-corpus multi-account-scope
# plan touches, by name -- not a repo-wide grep. ready-for-review/SKILL.md
# also contains the stale interpreter path and is deliberately out of scope
# for that plan, so a repo-wide contract would red-fail on it for an
# unrelated reason.
_TRANSCRIPT_TOOLKIT_SKILLS: tuple[str, ...] = (
    "transcript-narrative",
    "transcript-analysis",
    "error-mode-analysis",
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


class TestTranscriptToolkitInterpreterPathContract:
    """transcript-analysis.py's interpreter path is wrong under a relocated
    config dir (CLAUDE_CONFIG_DIR pointed somewhere other than ~/.claude) --
    every one of the three skills this plan touches must resolve it via
    ${CLAUDE_CONFIG_DIR:-$HOME/.claude}, never a hardcoded ~/.claude/scripts/
    literal."""

    @pytest.mark.parametrize("skill_name", _TRANSCRIPT_TOOLKIT_SKILLS)
    def test_skill_body_contains_no_hardcoded_claude_scripts_path(self, skill_name):
        body = _skill_body(skill_name)
        assert "~/.claude/scripts/" not in body, (
            f"{skill_name}/SKILL.md still contains the hardcoded interpreter path; "
            'use "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/transcript-analysis.py" instead'
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
# state path — a state subdirectory, or a log/sentinel this diff migrates —
# is a functional bug under a non-personal CLAUDE_CONFIG_DIR account, unlike
# a stowed path (agents/, hooks/, rules/, scripts/, skills/), which resolves
# identically under every account; the directory alternation mirrors
# enforce-marker-script-shape.sh:314. worktree-required and
# autonomous-shipping-required are deliberately excluded — those two sentinels
# keep a literal ~/.claude mention by design, unioned with the config-dir form
# rather than migrated (see CLAUDE.md's Shipping section and README's
# "Worktree enforcement").
_PER_ACCOUNT_STATE_PATH_RE = re.compile(
    r"(~|\$HOME|\$\{HOME\})/\.claude/"
    r"(handoffs/|briefs/|plans/|projects/|sessions/"
    r"|[^/\s\"'`]*-markers/|\.[^/\s\"'`]*\.d/|output-preferences\.md"
    r"|pii-patterns\.md|credential-file-guard\.md|credential-value-patterns\.md"
    r"|data-file-read-guard\.md|private-projects\.md|track-permission-prompts"
    r"|\.permission-prompt-log\.jsonl"
    r"|\.error-mode-nudge-enabled|\.error-mode-nudge\.log"
    r"|\.handoff-nudge-disabled|\.handoff-nudge\.log"
    r"|\.commit-stall-block-disabled|\.commit-stall-block\.log"
    r"|\.cost-ledger-enabled|\.consume-durable-continuity-disabled"
    r"|\.session-title-disabled)"
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
]


def _all_agent_md_paths() -> list[Path]:
    """Every agent body under claude/.claude/agents/ and plugin agents/ dirs —
    _agent_body() above is by-name only, so a corpus-wide scan needs this."""
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


def _all_doc_paths() -> list[Path]:
    """Every prose doc file the state-path contract covers: docs/**/*.md,
    every co-located skill REFERENCES.md, evals/README.md, and the
    repo-root README/CONTRIBUTING/SECURITY files. docs/reports/** and
    docs/case-studies/** hold preserved historical records (CLAUDE.md
    Axis 3) and are excluded, matching the plan's Out of scope list."""
    repo_root = SKILLS_DIR.parent.parent.parent
    docs_root = repo_root / "docs"
    paths = [
        path
        for path in sorted(docs_root.glob("**/*.md"))
        if "reports" not in path.relative_to(docs_root).parts
        and "case-studies" not in path.relative_to(docs_root).parts
    ]
    paths += sorted(SKILLS_DIR.glob("**/REFERENCES.md"))
    if _PLUGINS_DIR.exists():
        paths += sorted(_PLUGINS_DIR.glob("*/skills/**/REFERENCES.md"))
    for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md"):
        candidate = repo_root / name
        if candidate.exists():
            paths.append(candidate)
    evals_readme = repo_root / "evals" / "README.md"
    if evals_readme.exists():
        paths.append(evals_readme)
    return paths


class TestPerAccountStatePathContract:
    """No SKILL.md body, agent body, prose doc, or claude/.claude/CLAUDE.md
    may hold a literal ~/.claude (or $HOME/.claude, ${HOME}/.claude)
    per-account-state path — see
    .claude/plans/normalize-config-dir-paths.md. Guards the state-path class
    from drifting back in, including the $HOME-form bypass a bare
    '~/.claude/' substring check would miss entirely."""

    @pytest.mark.parametrize("skill_md_path", _all_skill_md_paths(), ids=lambda p: str(p))
    def test_skill_body_has_no_state_path(self, skill_md_path):
        body = _strip_yaml_frontmatter(skill_md_path.read_text())
        match = _PER_ACCOUNT_STATE_PATH_RE.search(body)
        assert match is None, (
            f"{skill_md_path} contains a hardcoded per-account-state path "
            f"{match.group(0)!r} — use <config-dir>/... (skill/agent prose) "
            'or "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..." (a runnable command)'
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
