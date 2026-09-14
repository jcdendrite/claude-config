#!/usr/bin/env python3
"""check-handoff.py -- mechanically check a draft handoff file against the
checkable half of handoff/SKILL.md's Pre-write checklist.

Usage: check-handoff.py <path-to-draft-handoff-file>

Prints PASS/FAIL for each mechanical check, WARN for each soft check, and
(always, at the end) the fixed list of checklist items this script cannot
check -- the model still owes those a manual verification. Exits non-zero
only when a hard check fails; a CLI-level error (missing file, non-UTF-8
content) also exits non-zero, with an actionable message on stderr.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Repo-root-relative, not config_dir()-derived: this script always reads the
# SKILL.md that ships in the same checkout's claude-skills/ package,
# regardless of which CLAUDE_CONFIG_DIR profile invoked it.
SKILL_MD_PATH = Path(__file__).resolve().parents[3] / "claude-skills" / "skills" / "handoff" / "SKILL.md"

PLACEHOLDER_TOKENS = ("TBD", "TODO", "fill in later")

UNRESOLVED_SECTION7_TOKENS = ("<config-dir>", "<slug>")

# Verbatim copy of handoff/SKILL.md's §3.5 categorization-rule anchor shapes
# (the substrings, not the underlying principle -- the principle itself is
# the residual judgment call listed in RESIDUAL_CHECKLIST_ITEMS below).
SECTION_3_5_ANCHOR_SHAPES = (
    "gh pr merge",
    "git push --force",
    "git push -f",
    "gh pr close",
    "git branch -d",
    "migrate",
    "db push",
    "db reset",
    "gh release create",
    "rm -rf",
    "Slack",
    "email",
    "GitHub issue",
    "GitHub PR comment",
)

CONFIDENCE_TAGS = ("[engineer-confirmed]", "[verified:", "[assumed]")

# Checklist items this script cannot mechanize -- printed at the end of every
# run so the model knows exactly what judgment is still on it.
RESIDUAL_CHECKLIST_ITEMS = (
    "§2 Status is consistent with §3 Next concrete step and §6 Open questions",
    'no "done" claim for a step whose verification is still pending',
    "§2.5/§2.6 content fidelity (checked here only for non-emptiness, not for faithfulness)",
    "if this session pushed commits to a branch with an open PR and /ready-for-review "
    "did not run this session, the pr-description skill was run before writing this file",
    "draft verification used Bash (cat/grep/sed -n/wc -l), not Read",
)

# Mirrors claude/.claude/tests/helpers.py's _SKILL_FIXTURE_RE (same regex
# shape, independently implemented) -- importing a test helper into
# production code would be a heavier coupling than duplicating this
# ~10-line extraction mechanism.
_FIXTURE_RE = re.compile(
    r"<!--\s*HOOK_TEST_FIXTURE:\s*(?P<id>[A-Za-z0-9_-]+)\b[^>]*-->\s*"
    r"```[a-z]*\n(?P<body>.*?)\n[ \t]*```",
    re.DOTALL,
)

_H2_HEADER_RE = re.compile(r"^##[ \t]+(.*)$", re.MULTILINE)
# (?!\.) excludes a dotted sub-section header (§2.5, §3.5) from matching its
# parent's number -- \d+ greedily consumes "2" in "§2.5", leaving "." next,
# which the lookahead rejects.
_SECTION_NUM_RE = re.compile(r"^§(\d+)(?!\.)\b")

_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# Anchored to line start (optionally indented), not a bare ``` substring
# search -- avoids miscounting a ``` that appears mid-line (e.g. quoted in
# prose) as a fence marker.
_FENCE_MARKER_RE = re.compile(r"^[ \t]*```", re.MULTILINE)


def extract_fixture(skill_text: str, fixture_id: str) -> str | None:
    """Return the body of the fenced code block tagged with `fixture_id`, or
    None if it's missing or not unique."""
    matches = [m for m in _FIXTURE_RE.finditer(skill_text) if m.group("id") == fixture_id]
    if len(matches) != 1:
        return None
    return matches[0].group("body").strip()


def strip_code_spans(text: str) -> str:
    """Remove fenced code blocks and inline code spans -- the natural way a
    draft would quote a literal string it's citing rather than proposing as a
    real next step, so a placeholder or anchor-shape scan does not fire on
    it."""
    return _INLINE_CODE_RE.sub("", _FENCED_BLOCK_RE.sub("", text))


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def extract_h2_sections(text: str) -> dict[int, str]:
    """Map §N (N restricted to a bare integer, never a dotted sub-section
    like §2.5 or §3.5) to its body text -- everything between that header
    line and the next level-2 (##) header, or end of file."""
    matches = list(_H2_HEADER_RE.finditer(text))
    sections: dict[int, str] = {}
    for i, m in enumerate(matches):
        header = m.group(1).strip()
        num_match = _SECTION_NUM_RE.match(header)
        if not num_match:
            continue
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[int(num_match.group(1))] = text[body_start:body_end]
    return sections


def check_preamble(draft_text: str, canonical_preamble: str) -> list[str]:
    """The draft must open with the canonical artifact preamble, compared
    whitespace-normalized (not byte-for-byte, since a draft may re-wrap
    lines) -- SKILL.md's own instruction is "open this file with this block
    verbatim"."""
    if normalize_whitespace(draft_text).startswith(normalize_whitespace(canonical_preamble)):
        return []
    return [
        "preamble does not match handoff/SKILL.md's artifact-preamble fixture "
        "(whitespace-normalized), or is not at the top of the file"
    ]


def check_sections_present_and_nonempty(draft_text: str) -> list[str]:
    sections = extract_h2_sections(draft_text)
    problems = []
    for n in range(1, 8):
        body = sections.get(n)
        if body is None:
            problems.append(f"§{n} header is missing")
        elif not body.strip():
            problems.append(f"§{n} is present but empty")
    return problems


def check_fence_markers_balanced(draft_text: str) -> list[str]:
    """An odd count means one ``` fence was left unterminated -- the
    non-greedy _FENCED_BLOCK_RE that every other check relies on to skip
    quoted content then pairs it with the NEXT fence anywhere later in the
    document, silently stripping real prose (including a genuine
    placeholder) from between them."""
    count = len(_FENCE_MARKER_RE.findall(draft_text))
    if count % 2 == 0:
        return []
    return [
        f"draft contains an odd number of ``` fence markers ({count}) -- an "
        "unterminated code fence can hide placeholder text or anchor shapes "
        "from every other check in this script"
    ]


def check_placeholder_text(draft_text: str) -> list[str]:
    stripped = strip_code_spans(draft_text)
    return [
        f"placeholder text found: {token!r}"
        for token in PLACEHOLDER_TOKENS
        if token in stripped
    ]


def check_section7_resolved_and_named(draft_text: str, checked_path: Path) -> list[str]:
    body = extract_h2_sections(draft_text).get(7)
    if body is None:
        return []  # already reported by check_sections_present_and_nonempty
    problems = [
        f"§7 contains an unresolved token: {token!r}"
        for token in UNRESOLVED_SECTION7_TOKENS
        if token in body
    ]
    if checked_path.name not in body:
        problems.append(f"§7 does not name the file being checked ({checked_path.name!r})")
    return problems


def check_section3_anchor_shapes(draft_text: str) -> list[str]:
    body = extract_h2_sections(draft_text).get(3)
    if body is None:
        return []
    stripped = strip_code_spans(body)
    return [
        f"§3 matches a §3.5 anchor shape ({shape!r}) -- verify categorization"
        for shape in SECTION_3_5_ANCHOR_SHAPES
        if shape in stripped
    ]


def check_confidence_tags(draft_text: str) -> list[str]:
    sections = extract_h2_sections(draft_text)
    warnings = []
    for n in (2, 3, 6):
        body = sections.get(n)
        if body is None:
            continue
        if not any(tag in body for tag in CONFIDENCE_TAGS):
            warnings.append(
                f"§{n} carries no confidence tag "
                "([engineer-confirmed], [verified: ...], or [assumed])"
            )
    return warnings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0] if argv else 'check-handoff.py'} <path-to-draft-handoff-file>", file=sys.stderr)
        return 2
    draft_path = Path(argv[1])

    try:
        raw = draft_path.read_bytes()
    except FileNotFoundError:
        print(f"check-handoff.py: no such file: {draft_path}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"check-handoff.py: cannot read {draft_path}: {exc}", file=sys.stderr)
        return 2

    try:
        draft_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"check-handoff.py: {draft_path} is not valid UTF-8: {exc}", file=sys.stderr)
        return 2

    try:
        skill_text = SKILL_MD_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check-handoff.py: cannot read {SKILL_MD_PATH}: {exc}", file=sys.stderr)
        return 2

    canonical_preamble = extract_fixture(skill_text, "artifact-preamble")
    if canonical_preamble is None:
        print(
            "check-handoff.py: HOOK_TEST_FIXTURE 'artifact-preamble' not found "
            f"(or found more than once) in {SKILL_MD_PATH}",
            file=sys.stderr,
        )
        return 2

    hard_checks = [
        ("``` fence markers balanced", check_fence_markers_balanced(draft_text)),
        ("preamble", check_preamble(draft_text, canonical_preamble)),
        ("§1-§7 present and non-empty", check_sections_present_and_nonempty(draft_text)),
        ("no placeholder text", check_placeholder_text(draft_text)),
        ("§7 resolved and names the checked file", check_section7_resolved_and_named(draft_text, draft_path)),
    ]
    soft_checks = [
        ("§3 vs §3.5 anchor shapes", check_section3_anchor_shapes(draft_text)),
        ("confidence tags in §2/§3/§6", check_confidence_tags(draft_text)),
    ]

    any_hard_failure = False
    for label, problems in hard_checks:
        if problems:
            any_hard_failure = True
            for problem in problems:
                print(f"FAIL: {problem}")
        else:
            print(f"PASS: {label}")

    for label, warnings in soft_checks:
        if warnings:
            for warning in warnings:
                print(f"WARN: {warning}")
        else:
            print(f"PASS: {label}")

    print()
    print("Not checked by this script -- verify yourself before writing:")
    for item in RESIDUAL_CHECKLIST_ITEMS:
        print(f"  - {item}")

    return 1 if any_hard_failure else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
