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
# pythonpath puts plugins/skill-management/scripts on the import path.
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

    AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents"
    SKILLS_DIR_ROOT = Path(__file__).resolve().parent.parent.parent / "skills"

    def _agent_body(self, name: str) -> str:
        return (self.AGENTS_DIR / f"{name}.md").read_text()

    def _skill_body(self, name: str) -> str:
        return (self.SKILLS_DIR_ROOT / name / "SKILL.md").read_text()

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
        """handoff's pre-write checklist must re-scan §3 against the §3.5 rule."""
        assert "re-checked against the §3.5 categorization rule" in _skill_file("handoff").read_text()

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


class TestHandoffCommitMarkerCoveredWork:
    """Pin handoff's instruction to land marker-covered work before the boundary.

    A completion marker records a hash of the exact state that was reviewed,
    and the gate authorizes on that stored hash, so the marker outlives the
    session that wrote it. What it does not outlive is a change to the state:
    the gate recomputes that state's hash when it fires, and the marker's
    stored value stops matching it. Which state each marker covers varies by
    skill — staged diff, active plan set, HEAD sha — so the assertion below
    pins the state-scoped phrasing rather than any one skill's state.
    Finished-but-uncommitted work therefore survives the boundary only while
    the resuming session leaves it untouched, which is why handoff tells the
    writer to commit it rather than merely describe it.
    """

    def test_handoff_section5_directs_committing_marker_covered_work(self):
        """§5 must scope marker validity to the unchanged state and direct the
        writer to commit finished marker-covered work before writing the file."""
        body = _skill_file("handoff").read_text()
        # The affirmative clause is the load-bearing one — it replaced the
        # false "markers die with their session" claim — so assert it from the
        # start of its sentence. A bare "stays valid past the session boundary"
        # substring would still match a negated rewrite ("never stays valid
        # past the session boundary"); including the subject leaves nowhere to
        # insert the negation without breaking the match. The invalidation
        # clause below is pinned subject-first for the same reason: "any
        # further change to that state" sits directly against "invalidates",
        # so "does not invalidate" cannot be slipped in without breaking it.
        assert "A completion marker stays valid past the session boundary" in body
        assert "only while the state it covers is unchanged" in body
        assert "any further change to that state invalidates it" in body
        assert "commit it *before* writing this file" in body

    def test_handoff_section5_maps_each_marker_to_the_state_it_covers(self):
        """§5 must name which state each skill's completion marker hashes.

        The four gates hash different things, and an agent that assumes the
        staged diff universally will mis-read a plan-review or
        ready-for-review marker. The mapping is asserted per skill so a future
        edit cannot swap two entries — a mis-map reads as authoritative and
        would reinstate exactly the wrong model this section exists to give.
        """
        body = _skill_file("handoff").read_text()
        assert "the state whose hash it stores" in body
        assert "staged diff for `/code-review` and `/skill-review`" in body
        assert "active plan set for `/plan-review`" in body
        assert "HEAD SHA for `/ready-for-review`" in body

    def test_handoff_section5_directs_labelling_disarmed_gates_historical(self):
        """§5 must distinguish a marker whose gate disarmed from one that expired.

        Committing a plan file empties the active-plan set, so the plan-review
        gate stops arming at all and the marker it left behind can never match
        again. That is a different outcome from a marker invalidated by a
        changed state, and §5 lists both kinds under one header — so without
        an explicit label, a disarmed gate's marker reads to the resuming
        session as still load-bearing.
        """
        body = _skill_file("handoff").read_text()
        assert "committing the plan leaves its marker on disk gating nothing" in body
        assert "Label such a marker historical" in body

    def test_handoff_prewrite_checklist_verifies_marker_live_or_historical(self):
        """The pre-write checklist must carry the live/historical verification.

        Split from the §5-body assertion above because the two regress
        independently: losing the body text means the writer is never told to
        label, while losing the checklist line means the writer is told but
        never verifies before writing. The failure modes differ, so the tests
        do too — matching how the commit-first rule is covered by a body test
        and a checklist test rather than one bundled assertion.
        """
        body = _skill_file("handoff").read_text()
        assert "Every §5 marker is labelled live or historical" in body
        assert "committed or superseded is not listed as if it still gates" in body

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
            "§5 is reconciled: finished work a live completion marker covers is committed "
            "before this file is written; where it is not, §3 names the review skill the "
            "resuming session must re-run to commit it" in _skill_file("handoff").read_text()
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

    PARENT_SKILLS = ["code-review", "plan-it", "plan-review", "test-conventions"]

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


def test_trigger_cases_files_well_formed() -> None:
    """Every trigger-cases.json file found under skills/ or plugins/ must be valid.

    Discovery-based: no hardcoded skill list. Validates shape only — never
    invokes a model. Auto-extends as trigger-cases.json files are added in
    follow-up PRs. CI-safe (pure static check).
    """
    repo_root = Path(__file__).resolve().parents[4]
    found_files: list[Path] = []
    for base in [
        repo_root / "claude" / ".claude" / "skills",
        repo_root / "plugins",
    ]:
        for p in base.glob("*/evals/trigger-cases.json"):
            found_files.append(p)
        for p in base.glob("*/skills/*/evals/trigger-cases.json"):
            found_files.append(p)

    assert found_files, "No trigger-cases.json files found — run the pilot skills setup first"

    for path in found_files:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}: invalid JSON — {exc}") from exc

        assert "skill_name" in data, f"{path}: missing 'skill_name'"
        assert "cases" in data, f"{path}: missing 'cases'"
        assert isinstance(data["cases"], list) and data["cases"], f"{path}: 'cases' must be a non-empty list"

        # skill_name must match the parent skill directory name
        skill_dir_name = path.parts[-3]  # …/skills/<name>/evals/trigger-cases.json
        assert data["skill_name"] == skill_dir_name, (
            f"{path}: skill_name={data['skill_name']!r} does not match parent dir {skill_dir_name!r}"
        )

        ids_seen: set[str] = set()
        for i, case in enumerate(data["cases"]):
            prefix = f"{path} case[{i}]"
            assert isinstance(case.get("query"), str) and case["query"], f"{prefix}: 'query' must be a non-empty string"
            assert isinstance(case.get("should_trigger"), bool), f"{prefix}: 'should_trigger' must be a boolean"
            assert isinstance(case.get("id"), str) and case["id"], f"{prefix}: 'id' must be a non-empty string"
            assert case["id"] not in ids_seen, f"{prefix}: duplicate id {case['id']!r}"
            ids_seen.add(case["id"])
            if "also_not_triggered" in case:
                ant = case["also_not_triggered"]
                assert isinstance(ant, list) and all(isinstance(s, str) for s in ant), (
                    f"{prefix}: 'also_not_triggered' must be a list of strings"
                )
                assert data["skill_name"] not in ant, (
                    f"{prefix}: 'also_not_triggered' must not contain the skill's own name "
                    f"({data['skill_name']!r}) — a skill cannot be a misfire of itself"
                )


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

    skill_path = _skill_file(skill_name)
    command = extract_skill_command(skill_path, "write-target")
    run_skill_command(command, cwd=tmp_path, isolated_home=isolated_home)

    directory, _suffix = _DURABLE_WRITE_TARGETS[skill_name]
    expected_dir = isolated_home / directory.replace("~/", "")
    assert expected_dir.is_dir(), (
        f"{skill_name}/SKILL.md's write-target fixture did not create {expected_dir}"
    )


def test_resume_context_script_exists_and_executable() -> None:
    script = SCRIPTS_DIR / "resume-context.sh"
    assert script.exists(), "claude/.claude/scripts/resume-context.sh must exist"
    assert os.access(script, os.X_OK), (
        "resume-context.sh must be committed with the executable bit set "
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
    assert "resume-context ~/.claude/" in body, (
        f"{skill_name}/SKILL.md must give a literal resume-context invocation, "
        "not just mention the name in passing"
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
