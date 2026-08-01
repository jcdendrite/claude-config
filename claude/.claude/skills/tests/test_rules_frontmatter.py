"""Structural validation for `.claude/rules/*.md` path-scoped rule files.

Covers both the project-scoped rules directory (`.claude/rules/`, repo-root
contributor conventions) and the stowed user-scope rules directory
(`claude/.claude/rules/`, installs to `~/.claude/rules/` and applies to every
repo the user opens). This is a structural/shape check only — it verifies each
rule file has parseable YAML frontmatter with a `paths` key holding a
non-empty list of strings. It does NOT verify that any individual glob pattern
is well-formed or matches a real target path; a syntactically-valid but
wrong/typo'd glob (e.g. `"cluade/.claude/rules/**"`) passes this check while
still silently matching nothing at runtime. What this check does catch — the
much more common failure — is `paths` being entirely absent, empty, the wrong
type, or containing a non-string entry: mistakes that would otherwise ship a
rule that either loads unconditionally by accident or breaks Claude Code's
frontmatter parsing outright, dropping the rule's guidance across every one of
the user's repos with no visible error.

Run with: pytest claude/.claude/
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from validate_skill_structure import parse_frontmatter

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PROJECT_RULES_DIR = _REPO_ROOT / ".claude" / "rules"
_STOWED_RULES_DIR = _REPO_ROOT / "claude" / ".claude" / "rules"


def _discover_rule_files() -> list[Path]:
    files: list[Path] = []
    for rules_dir in (_PROJECT_RULES_DIR, _STOWED_RULES_DIR):
        if rules_dir.is_dir():
            files.extend(sorted(rules_dir.rglob("*.md")))
    return files


def rule_frontmatter_violations(rule_file: Path) -> list[str]:
    """Return violation messages for rule_file's `paths` frontmatter.

    Empty list means the file passes. A rule file with no `paths` key loads
    unconditionally (every session) per Claude Code's own semantics — that's
    a legitimate choice in general, but every rule in the two directories
    this module discovers is, by this repo's convention, meant to be
    path-scoped. A missing or malformed `paths` field here is always a
    mistake, not a deliberate unconditional rule.
    """
    content = rule_file.read_text()
    if not content.startswith("---"):
        return [f"{rule_file} has no YAML frontmatter (must start with '---')"]

    try:
        frontmatter = parse_frontmatter(rule_file)
    except (yaml.YAMLError, ValueError) as exc:
        # ValueError covers an unterminated frontmatter block (missing closing
        # '---'), which parse_frontmatter's content.index() raises on directly
        # — yaml.YAMLError alone doesn't catch that case.
        return [f"{rule_file} has invalid or unterminated YAML frontmatter: {exc}"]

    if "paths" not in frontmatter:
        return [
            f"{rule_file} frontmatter is missing a `paths` key — path-scope "
            "it as a real rule, or move edit-time reference material to "
            "`docs/` instead"
        ]

    paths = frontmatter["paths"]
    if not (isinstance(paths, list) and paths):
        return [f"{rule_file} `paths` must be a non-empty list, got: {paths!r}"]

    non_string_entries = [p for p in paths if not isinstance(p, str)]
    if non_string_entries:
        return [
            f"{rule_file} `paths` entries must all be strings, "
            f"found non-string entries: {non_string_entries!r}"
        ]

    return []


_RULE_FILES = _discover_rule_files()
_RULE_IDS = [str(f.relative_to(_REPO_ROOT)) for f in _RULE_FILES]


def test_rule_files_exist():
    """Sanity check the discovery glob itself isn't silently empty."""
    assert _RULE_FILES, (
        f"Expected at least one rule file under {_PROJECT_RULES_DIR} or "
        f"{_STOWED_RULES_DIR}"
    )


@pytest.mark.parametrize("rule_file", _RULE_FILES, ids=_RULE_IDS)
def test_rule_has_parseable_paths_frontmatter(rule_file: Path):
    """Every discovered rule file has a non-empty `paths` list of strings."""
    violations = rule_frontmatter_violations(rule_file)
    assert not violations, "; ".join(violations)


class TestRuleFrontmatterViolations:
    """Unit tests for rule_frontmatter_violations() — uses tmp_path fixtures.

    The parametrized test above only ever sees this repo's current,
    presumably-well-formed rule files — it has never observed its own
    assertions fail on a broken frontmatter shape. These fixtures prove the
    validation logic actually discriminates good from bad input, independent
    of what the current repo's rule files happen to contain (mirrors
    TestCorpusBudgetFunction in test_skills.py).
    """

    def _write_rule(self, tmp_path: Path, content: str) -> Path:
        rule_file = tmp_path / "rule.md"
        rule_file.write_text(content)
        return rule_file

    def test_well_formed_rule_passes(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "**/*.sql"\n---\n\nbody\n')
        assert rule_frontmatter_violations(f) == []

    def test_missing_frontmatter_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "# heading, no frontmatter\n")
        violations = rule_frontmatter_violations(f)
        assert violations and "no YAML frontmatter" in violations[0]

    def test_unterminated_frontmatter_block_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "**/*.sql"\nno closing delimiter\n')
        violations = rule_frontmatter_violations(f)
        assert violations and "invalid or unterminated" in violations[0]

    def test_invalid_yaml_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\npaths: [unterminated\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f)
        assert violations and "invalid or unterminated" in violations[0]

    def test_missing_paths_key_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\nother_key: x\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f)
        assert violations and "missing a `paths` key" in violations[0]
        assert "move edit-time reference material to `docs/`" in violations[0]

    def test_empty_paths_list_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\npaths: []\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f)
        assert violations and "non-empty list" in violations[0]

    def test_paths_as_string_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths: "**/*.sql"\n---\n\nbody\n')
        violations = rule_frontmatter_violations(f)
        assert violations and "non-empty list" in violations[0]

    def test_non_string_path_entry_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\npaths:\n  - 42\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f)
        assert violations and "non-string entries" in violations[0]
