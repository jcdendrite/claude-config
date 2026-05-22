"""Structural validator for SKILL.md files.

Exposes a library interface (``validate(skill_file)``, ``corpus_budget_violations()``)
and a CLI entry point (``python3 validate_skill_structure.py <paths...>``) for
use by the ``require-skill-review.sh`` hook and the ``test_skills.py`` test suite.

The rules enforced here are the single source of truth for the plugin — the
hook invokes this module at commit time, and the test suite imports it and calls
``validate()`` and ``corpus_budget_violations()`` directly. Neither the hook nor
the tests re-implement the rules.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

import yaml

# Per https://code.claude.com/docs/en/skills.md: "the combined `description`
# and `when_to_use` text is truncated at 1,536 characters in the skill
# listing to reduce context usage." Configurable via maxSkillDescriptionChars
# setting; 1,536 is the default the harness applies when no override is set.
MAX_SKILL_DESCRIPTION_CHARS = 1536

# Skill-listing budget constants — extracted from the Claude Code binary
# v2.1.145. Function TM$(H, $=M77) computes:
#   budget = floor(context_window_tokens × bytesPerToken × fraction)
# unless SLASH_COMMAND_TOOL_CHAR_BUDGET is set, in which case that wins.
# Binary symbols (decompiled): Q3_=0.01 (fraction), M77=4 (bytesPerToken),
# g3_=200000 (fallback context tokens), d3_=1536 (per-skill cap).
#
# Claude Code orchestrators run on Opus 4.7 or Sonnet 4.6; both have 1M-token
# nominal context per the Anthropic model overview, but the binary applies
# the 200000-token fallback at runtime — the per-skill trim PRs (#203, #298)
# targeted the resulting 8000-char budget, so that is the operative bound
# this check enforces. The test class is named for Sonnet because the same
# 200000-token fallback applies to both Opus and Sonnet orchestrators, so
# the bound is identical regardless of which model is dispatching. Raising
# the bound to the nominal 1M context would require evidence that Claude
# Code passes the model's true window through the H parameter, which
# v2.1.145 does not.
SKILL_LISTING_FALLBACK_CONTEXT_TOKENS = 200_000
SKILL_LISTING_BYTES_PER_TOKEN = 4
SKILL_LISTING_BUDGET_FRACTION = 0.01
SKILL_LISTING_BUDGET_CHARS = int(
    SKILL_LISTING_FALLBACK_CONTEXT_TOKENS
    * SKILL_LISTING_BYTES_PER_TOKEN
    * SKILL_LISTING_BUDGET_FRACTION
)


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


def corpus_budget_violations(skill_files: Iterable[Path], budget: int) -> list[str]:
    """Check whether the aggregate description+when_to_use chars exceed budget.

    Pure function — no filesystem discovery. Caller supplies the file list and
    decides corpus scope (project-only, user-scope, plugin skills, etc.) so
    user-scope and project-scope listings are never inadvertently conflated.

    Skips files with disable-model-invocation: true (exact hyphenated key;
    only the literal string "disable-model-invocation" is recognized —
    underscore variants are not). Skips files with unparseable frontmatter
    rather than aborting — preserving best-effort posture for use in
    non-blocking hook warnings. Descriptions are suppressed from the harness
    listing for disabled skills.

    Returns a list with at most one violation string (naming total and the
    five largest offenders) or an empty list if the corpus is within budget.
    """
    per_skill_chars: dict[str, int] = {}
    for skill_file in skill_files:
        try:
            frontmatter = parse_frontmatter(skill_file)
        except (yaml.YAMLError, ValueError, OSError):  # ValueError covers UnicodeDecodeError
            continue
        if frontmatter.get("disable-model-invocation"):
            continue
        description = frontmatter.get("description", "") or ""
        when_to_use = frontmatter.get("when_to_use", "") or ""
        per_skill_chars[str(skill_file)] = len(description) + len(when_to_use)

    total = sum(per_skill_chars.values())
    if total <= budget:
        return []

    top_offenders = sorted(
        per_skill_chars.items(), key=lambda item: item[1], reverse=True
    )[:5]
    offender_lines = "\n".join(
        f"  {name}: {chars} chars" for name, chars in top_offenders
    )
    skill_count = len(per_skill_chars)
    return [
        f"Combined description+when_to_use across {skill_count} model-invokable "
        f"skills is {total} chars, exceeds Claude Code listing budget of "
        f"{budget} chars "
        f"(floor({SKILL_LISTING_FALLBACK_CONTEXT_TOKENS} tokens × "
        f"{SKILL_LISTING_BYTES_PER_TOKEN} chars/token × "
        f"{SKILL_LISTING_BUDGET_FRACTION})). Descriptions for least-used skills "
        f"will be dropped from the system-prompt listing. Trim the largest "
        f"offenders:\n{offender_lines}"
    ]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--corpus", nargs="+", metavar="FILE")
    args = parser.parse_args()

    if args.corpus is not None:
        violations = corpus_budget_violations(
            [Path(f) for f in args.corpus], SKILL_LISTING_BUDGET_CHARS
        )
        if violations:
            for v in violations:
                print(v, file=sys.stderr)
        sys.exit(0)  # --corpus is always best-effort; never exits non-zero

    # Default: per-file structural validation (hard-deny mode).
    all_violations: list[str] = []
    for arg in args.files:
        all_violations.extend(validate(Path(arg)))

    if all_violations:
        for violation in all_violations:
            print(violation, file=sys.stderr)
        sys.exit(1)

    sys.exit(0)
