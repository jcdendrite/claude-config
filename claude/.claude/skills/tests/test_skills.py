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

Run with: pytest claude/.claude/
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts on the import path.
from validate_skill_structure import (
    SKILL_LISTING_BUDGET_CHARS,
    corpus_budget_violations,
    validate,
)

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
COMMAND_SKILLS = ["handoff", "read-docx-comments", "transcript-analysis"]


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
        ("skill-review", "agent-review"),
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
    """Every disabled bundled skill in skillOverrides must have a row in docs/skills.md."""
    repo_root = Path(__file__).resolve().parents[4]
    settings = json.loads((repo_root / "claude/.claude/settings.json").read_text())
    docs_text = (repo_root / "docs/skills.md").read_text()
    for skill_name in settings.get("skillOverrides", {}):
        marker = f"| `/{skill_name}` |"
        assert marker in docs_text, (
            f"skillOverrides has {skill_name!r} but docs/skills.md has no "
            f"`{marker}` row. Every disabled bundled skill needs a rationale "
            'row in the "Bundled skills disabled by default" table.'
        )
