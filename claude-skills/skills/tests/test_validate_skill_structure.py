"""Unit tests for plugins/skill-management/scripts/validate_skill_structure.py over synthetic SKILL.md files."""
from __future__ import annotations

from pathlib import Path

import pytest
from _skill_md_helpers import (
    _EMPTY_SEQUENCE_SKILL,
    _IMPOSSIBLE_DATE_SKILL,
    _MALFORMED_YAML_SKILL,
    _SCALAR_ONLY_SKILL,
    _UNCLOSED_BLOCK_SKILL,
    _write_skill_md_fixture,
)

# Single source of truth for SKILL.md structural rules — the commit-gate hook
# shells out to the same module. pyproject.toml's [tool.pytest.ini_options]
# pythonpath puts plugins/skill-management/scripts and evals/ on the import path.
from validate_skill_structure import (
    FrontmatterError,
    corpus_budget_violations,
    parse_frontmatter,
    validate,
)


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


class TestValidateContextForkRequiresExplicitBackground:
    """Unit tests for validate()'s third check — uses tmp_path fixtures."""

    def _make_skill(self, tmp_path, name: str, frontmatter_lines: list[str]) -> Path:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        lines = ["---", *frontmatter_lines, "---", ""]
        skill_file.write_text("\n".join(lines))
        return skill_file

    def _background_violations(self, violations: list[str]) -> list[str]:
        return [v for v in violations if "background" in v]

    def test_bare_context_fork_with_no_background_key_fails(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["context: fork"])
        assert self._background_violations(validate(f))

    def test_background_null_fails(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["context: fork", "background:"])
        assert self._background_violations(validate(f))

    def test_background_non_boolean_string_fails(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["context: fork", 'background: "flase"'])
        assert self._background_violations(validate(f))

    def test_background_true_literal_passes(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["context: fork", "background: true"])
        assert not self._background_violations(validate(f))

    def test_background_false_literal_passes(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["context: fork", "background: false"])
        assert not self._background_violations(validate(f))

    @pytest.mark.parametrize("spelling", ["yes", "on", "True", "TRUE"])
    def test_yaml_coerced_boolean_spellings_pass(self, tmp_path, spelling):
        """PyYAML's safe_load coerces yes/on/True (any case) to real
        booleans, so Check 3's isinstance(background, bool) check accepts
        them today — pinning this so a future change to the accepted-input
        semantics doesn't silently narrow (or the message doesn't silently
        drift from behavior) without a test noticing."""
        f = self._make_skill(tmp_path, "a", ["context: fork", f"background: {spelling}"])
        assert not self._background_violations(validate(f)), (
            f"background: {spelling} should currently pass (PyYAML coerces it to bool)"
        )

    def test_context_not_fork_skips_the_check_regardless_of_background(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["context: something-else"])
        assert not self._background_violations(validate(f))

    def test_no_context_key_skips_the_check(self, tmp_path):
        f = self._make_skill(tmp_path, "a", ["description: 'x'"])
        assert validate(f) == []

    def test_near_miss_cased_context_value_is_not_flagged(self, tmp_path):
        """Whether the harness's own frontmatter parser is case-sensitive on
        `context:` is unestablished, so this check is deliberately exact-match
        only — pinning today's behavior rather than guessing the harness's
        tolerance."""
        f = self._make_skill(tmp_path, "a", ["context: Fork"])
        assert not self._background_violations(validate(f))


class TestParseFrontmatterRejectsUnclosedAndNonMappingBlocks:
    @pytest.mark.parametrize(
        "skill_text",
        [_UNCLOSED_BLOCK_SKILL, _SCALAR_ONLY_SKILL, _EMPTY_SEQUENCE_SKILL],
        ids=["unclosed-block", "scalar-only-block", "empty-sequence-block"],
    )
    def test_parse_frontmatter_raises_frontmatter_error(self, tmp_path, skill_text):
        skill_file = _write_skill_md_fixture(tmp_path, "broken-skill", skill_text)

        with pytest.raises(FrontmatterError):
            parse_frontmatter(skill_file)

    @pytest.mark.parametrize(
        "skill_text",
        ["---\n---\nbody\n", "---\nnull\n---\nbody\n", "---\n# only a comment\n---\nbody\n"],
        ids=["empty-block", "null-block", "comment-only-block"],
    )
    def test_a_block_yaml_reads_as_null_parses_to_an_empty_mapping(self, tmp_path, skill_text):
        skill_file = _write_skill_md_fixture(tmp_path, "null-block-skill", skill_text)

        assert parse_frontmatter(skill_file) == {}

    def test_a_dash_run_inside_a_value_closes_the_block(self, tmp_path):
        """A ``---`` inside a value ends the block, as it does in Claude Code's skill loader."""
        skill_file = _write_skill_md_fixture(
            tmp_path,
            "dash-run-skill",
            "---\nname: dash-run-skill\ndescription: a --- b\nuser-invocable: false\n---\nbody\n",
        )

        assert parse_frontmatter(skill_file) == {"name": "dash-run-skill", "description": "a"}

    def test_a_bom_prefixed_block_parses_to_its_mapping(self, tmp_path):
        """Claude Code's skill loader strips a leading U+FEFF before matching the delimiters."""
        skill_file = _write_skill_md_fixture(
            tmp_path,
            "bom-skill",
            "\ufeff---\nname: bom-skill\ndescription: Has a BOM.\n---\nbody\n",
        )

        assert parse_frontmatter(skill_file) == {"name": "bom-skill", "description": "Has a BOM."}

    def test_validate_reports_one_violation_for_a_bom_prefixed_unclosed_block(self, tmp_path):
        """``validate()`` runs its checks on a BOM-prefixed file, as the loader reads it."""
        skill_file = _write_skill_md_fixture(tmp_path, "bom-unclosed-skill", "\ufeff" + _UNCLOSED_BLOCK_SKILL)

        violations = validate(skill_file)

        assert len(violations) == 1
        assert violations[0].count(str(skill_file)) == 1
        assert "no closing" in violations[0]

    def test_only_one_leading_bom_is_stripped(self, tmp_path):
        skill_file = _write_skill_md_fixture(
            tmp_path,
            "double-bom-skill",
            "\ufeff\ufeff---\nname: double-bom-skill\ndescription: Has two BOMs.\n---\nbody\n",
        )

        assert parse_frontmatter(skill_file) == {}

    def test_a_bom_inside_a_value_survives(self, tmp_path):
        skill_file = _write_skill_md_fixture(
            tmp_path,
            "inner-bom-skill",
            "---\nname: inner-bom-skill\ndescription: a\ufeffb\n---\nbody\n",
        )

        assert parse_frontmatter(skill_file) == {"name": "inner-bom-skill", "description": "a\ufeffb"}

    @pytest.mark.parametrize(
        ("skill_text", "message_token"),
        [
            (_UNCLOSED_BLOCK_SKILL, "no closing"),
            (_SCALAR_ONLY_SKILL, "not a YAML mapping"),
            (_EMPTY_SEQUENCE_SKILL, "not a YAML mapping"),
        ],
        ids=["unclosed-block", "scalar-only-block", "empty-sequence-block"],
    )
    def test_validate_reports_one_violation_naming_the_file(self, tmp_path, skill_text, message_token):
        skill_file = _write_skill_md_fixture(tmp_path, "broken-skill", skill_text)

        violations = validate(skill_file)

        assert len(violations) == 1
        assert violations[0].count(str(skill_file)) == 1
        assert message_token in violations[0]

    @pytest.mark.parametrize(
        "skill_text",
        [_SCALAR_ONLY_SKILL, _EMPTY_SEQUENCE_SKILL, _MALFORMED_YAML_SKILL, _IMPOSSIBLE_DATE_SKILL, _UNCLOSED_BLOCK_SKILL],
        ids=["scalar-only-block", "empty-sequence-block", "malformed-yaml", "impossible-date", "unclosed-block"],
    )
    def test_corpus_budget_skips_an_unparseable_block(self, tmp_path, skill_text):
        unparseable_file = _write_skill_md_fixture(tmp_path, "unparseable-skill", skill_text)
        counted_file = _write_skill_md_fixture(
            tmp_path,
            "counted-skill",
            "---\nname: counted-skill\ndescription: Counted.\n---\nbody\n",
        )

        violations = corpus_budget_violations([unparseable_file, counted_file], 0)

        assert len(violations) == 1
        assert "across 1 model-invokable skills" in violations[0]

    def test_validate_reports_a_quoted_dash_run_as_not_strict_yaml(self, tmp_path):
        """A quoted ``---`` ends the block inside the quote, which check 1 reports as not strict YAML."""
        skill_file = _write_skill_md_fixture(
            tmp_path,
            "quoted-dash-run-skill",
            '---\nname: quoted-dash-run-skill\ndescription: "a --- b"\ndisable-model-invocation: true\n---\nbody\n',
        )

        violations = validate(skill_file)

        assert len(violations) == 1
        assert "not strict YAML" in violations[0]

    def test_validate_reports_no_violation_for_an_empty_block(self, tmp_path):
        skill_file = _write_skill_md_fixture(tmp_path, "empty-block-skill", "---\n---\nbody\n")

        assert validate(skill_file) == []

    def test_validate_reports_not_strict_yaml_when_a_later_rule_closes_the_block_over_body_text(self, tmp_path):
        skill_file = _write_skill_md_fixture(
            tmp_path,
            "later-rule-skill",
            _UNCLOSED_BLOCK_SKILL + "\n---\n\nMore body text.\n",
        )

        violations = validate(skill_file)

        assert len(violations) == 1
        assert "not strict YAML" in violations[0]
