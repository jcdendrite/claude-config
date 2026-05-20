"""Structural validator for SKILL.md files.

Exposes a library interface (``validate(skill_file)``) and a CLI entry point
(``python3 validate_skill_structure.py <paths...>``) for use by the
``require-skill-review.sh`` hook and the ``test_skills.py`` test suite.

The two rules enforced here are the single source of truth for the
plugin — the hook invokes this module at commit time, and the test suite
imports it and calls ``validate()`` directly. Neither the hook nor the tests
re-implement the rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Per https://code.claude.com/docs/en/skills.md: "the combined `description`
# and `when_to_use` text is truncated at 1,536 characters in the skill
# listing to reduce context usage." Configurable via maxSkillDescriptionChars
# setting; 1,536 is the default the harness applies when no override is set.
MAX_SKILL_DESCRIPTION_CHARS = 1536


def parse_frontmatter(skill_file: Path) -> dict:
    """Return the parsed YAML frontmatter of a SKILL.md file.

    Reads ``skill_file``, slices between the opening and closing ``---``
    delimiters, and returns the result of ``yaml.safe_load``.  Returns ``{}``
    when the file does not begin with ``---`` (no frontmatter present).

    Raises ``yaml.YAMLError`` on invalid frontmatter — callers that want a
    human-readable violation message should catch it; see ``validate()``.
    """
    content = skill_file.read_text()
    if not content.startswith("---"):
        return {}
    closing = content.index("---", 3)
    return yaml.safe_load(content[3:closing]) or {}


def validate(skill_file: Path) -> list[str]:
    """Return a list of human-readable violation messages for ``skill_file``.

    An empty list means the file passes all structural checks.  Two checks
    are applied in order:

    1. Strict-YAML frontmatter — the frontmatter between the ``---`` delimiters
       must parse with ``yaml.safe_load`` without raising.
    2. Description length cap — ``len(description) + len(when_to_use)`` must
       not exceed ``MAX_SKILL_DESCRIPTION_CHARS``.
    """
    violations: list[str] = []

    # Check 1: strict-YAML frontmatter.
    try:
        frontmatter = parse_frontmatter(skill_file)
    except yaml.YAMLError as exc:
        violations.append(
            f"{skill_file}: frontmatter is not strict YAML: {exc}. "
            f"If a value contains ': ', block-fold (`description: >`) or "
            f"double-quote it."
        )
        # Cannot compute length without a parsed frontmatter — stop here.
        return violations

    # Check 2: description + when_to_use length cap.
    description = frontmatter.get("description", "") or ""
    when_to_use = frontmatter.get("when_to_use", "") or ""
    rendered = len(description) + len(when_to_use)
    if rendered > MAX_SKILL_DESCRIPTION_CHARS:
        violations.append(
            f"{skill_file}: description+when_to_use is {rendered} chars, "
            f"exceeds harness cap of {MAX_SKILL_DESCRIPTION_CHARS}; the tail "
            f"will be truncated from the system-prompt listing"
        )

    return violations


if __name__ == "__main__":
    all_violations: list[str] = []
    for arg in sys.argv[1:]:
        all_violations.extend(validate(Path(arg)))

    if all_violations:
        for violation in all_violations:
            print(violation, file=sys.stderr)
        sys.exit(1)

    sys.exit(0)
