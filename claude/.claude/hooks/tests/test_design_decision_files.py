"""Assert per-file shape, legacy-provenance completeness, no-revived-numbering,
intra-directory citation correctness, and relative-link resolution for
docs/design-decisions/.

docs/design-decisions.md was split into one file per decision under
docs/design-decisions/<slug>.md so two branches recording a decision never
collide on the same path. These are the permanent invariants that split
depends on:

  1. Per-file shape: filename matches the slug grammar, exactly one H1.
  2. Line 3 of every file is an italic provenance line (`*...*`, not
     `**...**`) carrying an ISO-8601 date, a `Formerly §N` clause, or
     both -- a file with no `Formerly` clause (a decision recorded after
     the split) must carry a date instead. Read across the whole
     directory, the recorded `Formerly §N` values equal exactly the
     closed set {1..63} -- no gaps, duplicates, or fabrications. That set
     can never grow: the pre-split monolith is retired.
  3. No `## N.` heading survives anywhere -- the retired monolith numbering
     cannot be revived by copying an old section as a template.
  4. Every converted `[§N](slug.md)` intra-file cross-reference resolves to
     an existing file, and that file's own provenance line records the same
     legacy number -- proving the bulk link conversion didn't transpose a
     target, which a resolution-only check would miss.
  5. Every other relative markdown link (not `§N` citations, covered by 4;
     not `http(s)://` or bare `#anchor` links, which need no filesystem
     resolution) resolves to an existing file relative to its source file's
     own directory -- catches a link left one directory level too shallow
     after a file's directory depth changed.
  6. No external `docs/design-decisions.md §N` or `#N` citation, in the
     hooks, scripts, and agents that cite into this directory, still
     targets a legacy number a prior migration reassigned to a different
     decision -- that citation shape resolves to unrelated content instead
     of erroring, so a stranding rename ships silently rather than failing
     loudly.

Checking logic for assertions 1 through 6 is factored into standalone
`_*_violations()` functions that take a file list rather than reading
DESIGN_DECISIONS_DIR directly, so TestFaultInjection below can exercise the
same logic against a synthetic tmp_path corpus instead of only the real one.

Assertion 7 is unrelated to the split described above: it pins
code-review/SKILL.md's item 12a sub-bullet list to
comment-discipline-reviewer.md's core-review-angle list, name-for-name and
in order, so the index can't silently drift stale when the agent's angle
list gains, loses, renames, or reorders an angle. It lives here because
this module already carries the REPO_ROOT/CLAUDE_DIR resolution and
pure-function violations idiom the check needs.

Assertion 8 checks that each name in `_SCOPE_CITED_ANGLE_NAMES` appears in
comment-discipline-reviewer.md's Scope section and resolves to a
core-review-angle header. It lives here because it reuses assertion 7's
angle-header parser.

Assertion 9 checks that the committed regression fixture
claude/.claude/hooks/tests/fixtures/paragraph-readability.json still names a
current comment-discipline-reviewer.md angle header, still hashes to that
angle's current text (so a rewording is caught rather than silently
drifting from what Phase 2 measured), and still carries every literal and
carve-out excerpt that keeps its must_flag/must_pass pair non-vacuous. It
lives here because it reuses assertion 7's angle-header parser and
angle-block boundaries.

Why hooks/tests/ instead of tests/ or skills/tests/:
  This module imports helpers.CLAUDE_DIR from the sibling helpers path. It
  lives in hooks/tests/ to match the co-location of test_doc_counts.py,
  which guards a related class of doc-vs-disk drift.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

# CLAUDE_DIR is defined in helpers.py as Path(__file__).resolve().parent.parent,
# anchored to the stow-source path, not the symlink target (~/.claude/).
# Chain: CLAUDE_DIR (claude/.claude/) → .parent (claude/) → .parent (repo root).
REPO_ROOT = CLAUDE_DIR.parent.parent
DESIGN_DECISIONS_DIR = REPO_ROOT / "docs" / "design-decisions"

_FILENAME_RE = re.compile(r"^[a-z][a-z0-9-]*\.md$")
_H1_RE = re.compile(r"^# .+$", re.MULTILINE)
_PROVENANCE_RE = re.compile(r"Formerly `docs/design-decisions\.md` §(\d+)\.")
# Whole-text guard: the bare prefix, unanchored to line 3, so a garbled
# fragment that landed elsewhere in a file's body (copy-paste mistake,
# botched edit) is still found even though it can't satisfy the full,
# well-formed _PROVENANCE_RE shape.
_FORMERLY_PREFIX_RE = re.compile(r"Formerly `docs/design-decisions\.md`")
# Matches a single-asterisk italic line (`*...*`) and rejects a double-asterisk
# bold one (`**...**`) via the lookahead/lookbehind pinning the outer
# delimiters to exactly one asterisk each. Needed because at least two real
# files carry a **bold** body line elsewhere (not on line 3) that a loose
# `^\*.*\*$` would also match: ui-notification-defaults-in-stow-source.md:31
# and attribution-in-skill-prose-and-hook.md:14,32,38.
_ITALIC_PROVENANCE_LINE_RE = re.compile(r"^\*(?!\*)(?P<body>.*)(?<!\*)\*$")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The pre-split monolith is retired, so this set can never grow past §63.
# See .claude/rules/design-decisions.md's Format bullet for the same bound;
# test_rule_file_closed_legacy_upper_bound_matches_module_constant pins the
# two together.
_CLOSED_LEGACY_NUMBERS: frozenset[int] = frozenset(range(1, 64))
_LEGACY_HEADING_RE = re.compile(r"^## \d+\.", re.MULTILINE)
# Phase A's own conversion format for an intra-file cross-reference, e.g.
# "[§49](schedulewakeup-denied-by-bare-tool-name.md)" -- distinguishes a
# converted citation from an unrelated relative link to a sibling doc (e.g.
# a link to docs/hooks.md), whose link text never starts with "§".
# A citation-shaped link whose bracket text isn't exactly "§N" (e.g.
# "[§11: some title](...)") doesn't match this pair and falls through to
# assertion 5's resolution-only check instead. That silently loses
# assertion 4's provenance-match protection. No live instance today.
_CONVERTED_CITATION_RE = re.compile(r"\[§(\d+)\]\(([^)]+)\)")
# Any markdown link, converted citation or not -- used to find every relative
# link target so it can be resolved against its own source file's directory.
# Known gaps, none live in the corpus today:
# - doesn't exclude fenced-code-block/inline-code content
# - doesn't exclude image syntax (![alt](...))
# - doesn't exclude reference-style links ([text][ref])
# - doesn't handle a link target containing a literal ")"
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_CITATION_LINK_TEXT_RE = re.compile(r"^§\d+$")

# §59 and §60 now resolve to docs/design-decisions/code-reviews-fix-route-unifies-42s.md
# and code-reviews-marker-short-circuit-left.md respectively -- a stale
# citation to either number for the decision that formerly held it would
# still resolve, just to the wrong file, so assertion 4's resolution-only
# check can't catch this shape.
_REASSIGNED_LEGACY_NUMBERS: tuple[int, ...] = (59, 60)
# Matches both citation glyphs seen across the corpus for the same legacy
# number -- e.g. guard-settings-session-keysshs-default-branch.md (legacy
# §54) is cited as both `§54` (_lib.sh) and `#54` (test_lib.py).
_EXTERNAL_CITATION_NUMBER_RE = re.compile(r"[§#](\d+)")
# A bare §N/#N is ambiguous -- handoff/SKILL.md's own §1-§7 section numbers,
# and test-conventions/test-evaluation's §N anchors, are cited throughout
# this same live-code surface. A citation only counts as targeting
# docs/design-decisions.md when this token appears on the same line as the
# number or an adjacent one -- the corpus wraps a citation across a line
# break (e.g. "docs/design-decisions.md\n    §44's motivating scenario)")
# but never across more than one.
_DESIGN_DECISIONS_ANCHOR_RE = re.compile(r"design-decisions\.md")
# Live code/test/agent surface where an external §N/#N citation into this
# directory is load-bearing. Recursive **/*.py under hooks/ and scripts/
# reaches each directory's tests/ subdirectory and
# claude/.claude/scripts/transcript_analysis/'s subpackage in one entry --
# no nested *.sh files exist under either, so those globs stay non-recursive.
# claude-skills/skills/tests/test_skills.py and repo-root install.sh are
# named explicitly rather than swept in by a directory glob: each is the
# sole citing file in its tree, and neither tree is otherwise live code.
# Excludes CLAUDE.md, skills, docs/, and .claude/rules|plans -- prose
# surfaces where a citation cluster like "§37, §42, §45" needs a
# citation-list parser this check doesn't attempt.
_LIVE_CODE_CITATION_GLOBS: tuple[tuple[Path, str], ...] = (
    (CLAUDE_DIR / "hooks", "*.sh"),
    (CLAUDE_DIR / "hooks", "**/*.py"),
    (CLAUDE_DIR / "scripts", "*.sh"),
    (CLAUDE_DIR / "scripts", "**/*.py"),
    (CLAUDE_DIR / "agents", "*.md"),
    (REPO_ROOT / "claude-skills" / "skills" / "tests", "*.py"),
    (REPO_ROOT, "install.sh"),
)


def _decision_files(directory: Path = DESIGN_DECISIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.md"))


def _provenance_number(text: str) -> int | None:
    match = _PROVENANCE_RE.search(text)
    return int(match.group(1)) if match else None


def _assert_corpus_non_empty(paths: list[Path]) -> None:
    assert paths, (
        "docs/design-decisions/ contains no .md files -- DESIGN_DECISIONS_DIR "
        "may be misconfigured, or the parametrized per-file tests below would "
        "silently collect zero cases instead of failing"
    )


def test_corpus_is_non_empty() -> None:
    """Guards TestPerFileShape's parametrized tests: pytest.mark.parametrize
    over an empty list collects zero test cases and reports all-green rather
    than failing, so this standalone check is what actually catches a
    misconfigured or empty DESIGN_DECISIONS_DIR."""
    _assert_corpus_non_empty(_decision_files())


_RULE_FILENAME_GRAMMAR_RE = re.compile(r"Filename grammar\.\*\* `([^`]+)`")


def test_rule_file_filename_grammar_matches_enforced_regex() -> None:
    """Pins .claude/rules/design-decisions.md's stated filename-grammar
    regex to _FILENAME_RE, the regex actually enforced below, so the two
    can't silently drift apart the way they did before this test existed."""
    rule_path = REPO_ROOT / ".claude" / "rules" / "design-decisions.md"
    rule_text = rule_path.read_text(encoding="utf-8")
    match = _RULE_FILENAME_GRAMMAR_RE.search(rule_text)
    assert match, (
        f"{rule_path}: 'Filename grammar.' bullet not found or reworded -- "
        "update _RULE_FILENAME_GRAMMAR_RE to match its current phrasing."
    )
    assert match.group(1) == _FILENAME_RE.pattern, (
        f"{rule_path} states {match.group(1)!r} but _FILENAME_RE enforces "
        f"{_FILENAME_RE.pattern!r} -- keep both in sync."
    )


_RULE_CLOSED_LEGACY_UPPER_BOUND_RE = re.compile(r"closed at `§(\d+)`")


def test_rule_file_closed_legacy_upper_bound_matches_module_constant() -> None:
    """Pins .claude/rules/design-decisions.md's stated closed-legacy-set
    upper bound to max(_CLOSED_LEGACY_NUMBERS), the constant
    _legacy_number_range_violations actually enforces below.

    Mirrors test_rule_file_filename_grammar_matches_enforced_regex's
    drift-prevention shape."""
    rule_path = REPO_ROOT / ".claude" / "rules" / "design-decisions.md"
    rule_text = rule_path.read_text(encoding="utf-8")
    match = _RULE_CLOSED_LEGACY_UPPER_BOUND_RE.search(rule_text)
    assert match, (
        f"{rule_path}: closed-legacy-set upper-bound clause not found or "
        "reworded -- update _RULE_CLOSED_LEGACY_UPPER_BOUND_RE to match its "
        "current phrasing."
    )
    assert int(match.group(1)) == max(_CLOSED_LEGACY_NUMBERS), (
        f"{rule_path} states §{match.group(1)} but _CLOSED_LEGACY_NUMBERS caps "
        f"at §{max(_CLOSED_LEGACY_NUMBERS)} -- keep both in sync."
    )


def _filename_grammar_violations(paths: list[Path]) -> list[str]:
    return [path.name for path in paths if not _FILENAME_RE.match(path.name)]


def _h1_count_violations(paths: list[Path]) -> list[str]:
    return [
        path.name
        for path in paths
        if len(_H1_RE.findall(path.read_text(encoding="utf-8"))) != 1
    ]


class TestPerFileShape:
    """Assertion 1: filename grammar and exactly-one-H1, per file."""

    @pytest.mark.parametrize("path", _decision_files(), ids=lambda p: p.name)
    def test_filename_matches_slug_grammar(self, path: Path) -> None:
        assert _FILENAME_RE.match(path.name), (
            f"{path.name}: filename must match ^[a-z][a-z0-9-]*\\.md$ -- lowercase, "
            "digits, and hyphens only, no leading digit, no date or number prefix."
        )

    @pytest.mark.parametrize("path", _decision_files(), ids=lambda p: p.name)
    def test_exactly_one_h1(self, path: Path) -> None:
        headings = _H1_RE.findall(path.read_text(encoding="utf-8"))
        assert len(headings) == 1, (
            f"{path.name}: expected exactly one H1, found {len(headings)} -- "
            "a second H1 would smuggle a second decision into one file."
        )


def _provenance_line_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        line3 = lines[2] if len(lines) >= 3 else ""
        match = _ITALIC_PROVENANCE_LINE_RE.match(line3)
        if not match:
            violations.append(
                f"{path.name}: line 3 must be an italic provenance line "
                f"(`*...*`, single asterisks -- not `**...**`) -- found {line3!r}"
            )
            continue
        body = match.group("body")
        has_formerly = _PROVENANCE_RE.search(body) is not None
        date_match = _ISO_DATE_RE.search(body)
        if not has_formerly and date_match is None:
            violations.append(
                f"{path.name}: provenance line carries neither an ISO-8601 date "
                "nor a 'Formerly `docs/design-decisions.md` §N.' clause"
            )
        elif date_match is not None and not has_formerly and "Formerly" in body:
            violations.append(
                f"{path.name}: provenance line has a date but its 'Formerly' "
                "clause doesn't match the expected "
                "'Formerly `docs/design-decisions.md` §N.' shape"
            )
        if date_match is not None:
            try:
                datetime.date.fromisoformat(date_match.group(0))
            except ValueError:
                violations.append(
                    f"{path.name}: provenance line date {date_match.group(0)!r} "
                    "does not parse as ISO-8601"
                )
    return violations


def test_provenance_line_is_well_formed() -> None:
    """Assertion 2 (shape half): line 3 of every file is an italic
    provenance line carrying an ISO-8601 date, a 'Formerly §N' clause, or
    both. A file with no Formerly clause -- a decision recorded after the
    split -- must carry a date instead. Absence of a Formerly clause is not
    itself a violation here; test_legacy_numbers_form_contiguous_range's
    closed-set check is what catches a dropped or fabricated one."""
    paths = _decision_files()
    _assert_corpus_non_empty(paths)
    violations = _provenance_line_violations(paths)
    assert not violations, "\n".join(violations)


def _malformed_formerly_fragment_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        line3 = lines[2] if len(lines) >= 3 else ""
        line3_is_well_formed = _PROVENANCE_RE.search(line3) is not None
        for match in _FORMERLY_PREFIX_RE.finditer(text):
            on_line3 = text.count("\n", 0, match.start()) == 2
            if on_line3 and line3_is_well_formed:
                continue
            violations.append(
                f"{path.name}: found a 'Formerly `docs/design-decisions.md`' "
                "fragment that isn't part of a well-formed line-3 provenance "
                "match -- a corpus-corruption artifact outside the one line "
                "provenance is recorded on"
            )
    return violations


def test_no_malformed_formerly_fragment_outside_provenance_line() -> None:
    """Assertion 2 (whole-text guard): a 'Formerly `docs/design-decisions.md`'
    -shaped fragment anywhere in a file's body, not just line 3, must resolve
    to a well-formed provenance match on line 3 itself. Distinct from
    test_provenance_line_is_well_formed, which inspects only line 3 and so
    can't see a garbled fragment landed elsewhere in the file."""
    paths = _decision_files()
    _assert_corpus_non_empty(paths)
    violations = _malformed_formerly_fragment_violations(paths)
    assert not violations, "\n".join(violations)


def _legacy_number_range_violations(
    paths: list[Path],
    expected_legacy_numbers: frozenset[int] = _CLOSED_LEGACY_NUMBERS,
) -> list[str]:
    numbers = [
        number
        for number in (
            _provenance_number(path.read_text(encoding="utf-8")) for path in paths
        )
        if number is not None
    ]
    violations: list[str] = []
    missing = sorted(expected_legacy_numbers - set(numbers))
    if missing:
        violations.append(
            f"Legacy §N values are missing {missing} from the expected closed "
            f"set {{1..{max(expected_legacy_numbers)}}}: {sorted(numbers)}"
        )
    extra = sorted(set(numbers) - expected_legacy_numbers)
    if extra:
        violations.append(
            f"Legacy §N values include {extra}, outside the expected closed "
            f"set {{1..{max(expected_legacy_numbers)}}}: {sorted(numbers)}"
        )
    if len(numbers) != len(set(numbers)):
        duplicated = sorted({number for number in numbers if numbers.count(number) > 1})
        violations.append(f"Legacy §N values contain duplicates: {duplicated}")
    return violations


def test_legacy_numbers_form_contiguous_range() -> None:
    """Assertion 2 (closure half): recorded provenance §N values, across
    every file, equal exactly the closed set {1..63} -- no gaps,
    duplicates, or fabrications. The set is fixed rather than derived from
    max(numbers) -- a derived max would silently accept losing §63 itself,
    since removing the top element leaves the rest trivially contiguous."""
    paths = _decision_files()
    _assert_corpus_non_empty(paths)
    violations = _legacy_number_range_violations(paths)
    assert not violations, "\n".join(violations)


def _numbered_heading_violations(paths: list[Path]) -> list[str]:
    return [
        path.name
        for path in paths
        if _LEGACY_HEADING_RE.search(path.read_text(encoding="utf-8"))
    ]


def test_no_numbered_heading_survives() -> None:
    """Assertion 3: no `## N.` heading anywhere in the directory."""
    paths = _decision_files()
    _assert_corpus_non_empty(paths)
    violations = _numbered_heading_violations(paths)
    assert not violations, (
        f"Found a revived '## N.' heading in: {violations} -- the retired "
        "monolith numbering must not be reintroduced by copying an old "
        "section as a template."
    )


def _citation_provenance_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in _CONVERTED_CITATION_RE.finditer(text):
            cited_number = int(match.group(1))
            target_raw = match.group(2)
            target_name = target_raw.split("#", 1)[0]
            target_path = path.parent / target_name
            if not target_path.is_file():
                violations.append(
                    f"{path.name}: [§{cited_number}]({target_raw}) does not "
                    "resolve to an existing file"
                )
                continue
            target_number = _provenance_number(target_path.read_text(encoding="utf-8"))
            if target_number != cited_number:
                violations.append(
                    f"{path.name}: [§{cited_number}]({target_raw}) points at "
                    f"a file whose own provenance line records §{target_number}"
                )
    return violations


def test_converted_citations_resolve_and_match_provenance() -> None:
    """Assertion 4: every converted `[§N](slug.md)` citation resolves to an
    existing file, and that file's own on-disk provenance line records the
    same legacy number N. Checked against each file's own provenance line at
    test-run time, not a static slug table, since a section numbered beyond
    the plan's fixed Slug map has no row there. A resolution-only check would
    pass a citation pointed at the wrong file as readily as the right one;
    the provenance-number match is what makes the bulk conversion of these
    citations checkable rather than merely resolvable."""
    paths = _decision_files()
    _assert_corpus_non_empty(paths)
    violations = _citation_provenance_violations(paths)
    assert not violations, "\n".join(violations)


def _relative_link_targets(text: str) -> list[str]:
    """Return every markdown link target in `text` requiring filesystem
    resolution -- excludes http(s) URLs, bare `#anchor` links, and `§N`-style
    citations already checked by
    test_converted_citations_resolve_and_match_provenance."""
    targets: list[str] = []
    for link_text, target in _MARKDOWN_LINK_RE.findall(text):
        if target.startswith(("http://", "https://")):
            continue
        if target.startswith("#"):
            continue
        if _CITATION_LINK_TEXT_RE.match(link_text):
            continue
        targets.append(target)
    return targets


def _relative_link_violations(paths: list[Path]) -> tuple[int, list[str]]:
    violations: list[str] = []
    checked = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for target in _relative_link_targets(text):
            checked += 1
            target_path = path.parent / target.split("#", 1)[0]
            if not target_path.is_file():
                violations.append(
                    f"{path.name}: link target {target!r} does not resolve "
                    f"to an existing file relative to {path.parent}"
                )
    return checked, violations


def test_relative_links_resolve_to_existing_files() -> None:
    """Every relative markdown link in a docs/design-decisions/*.md file
    resolves to an existing file when resolved against that file's own
    directory -- catches a link left one directory level too shallow after
    Phase A's split moved a file deeper (e.g. `[x](hooks.md)` where the file
    now lives one level below docs/ and needs `[x](../hooks.md)`). Distinct
    from test_converted_citations_resolve_and_match_provenance, which covers
    only the `[§N](slug.md)` intra-directory citation form and additionally
    checks the target's provenance number; this test covers every other
    relative link shape and checks resolution only."""
    checked, violations = _relative_link_violations(_decision_files())
    assert checked > 0, "no relative links found -- test may be miswired"
    assert not violations, "\n".join(violations)


def _live_code_citation_files() -> list[Path]:
    this_file = Path(__file__).resolve()
    files: list[Path] = []
    for directory, pattern in _LIVE_CODE_CITATION_GLOBS:
        files.extend(path for path in directory.glob(pattern) if path.is_file())
    return [path for path in files if path.resolve() != this_file]


def _design_decisions_citation_numbers(text: str) -> set[int]:
    """Every §N/#N in `text` that cites docs/design-decisions.md, per the
    same-line-or-adjacent-line proximity anchor described above
    _DESIGN_DECISIONS_ANCHOR_RE -- excludes a §N/#N belonging to an
    unrelated document's own section numbering."""
    lines = text.splitlines()
    anchor_lines = {
        index for index, line in enumerate(lines) if _DESIGN_DECISIONS_ANCHOR_RE.search(line)
    }
    numbers: set[int] = set()
    for index, line in enumerate(lines):
        if not anchor_lines & {index - 1, index, index + 1}:
            continue
        numbers.update(int(m.group(1)) for m in _EXTERNAL_CITATION_NUMBER_RE.finditer(line))
    return numbers


def _reassigned_citation_violations(
    paths: list[Path],
    reassigned_numbers: tuple[int, ...] = _REASSIGNED_LEGACY_NUMBERS,
) -> list[str]:
    stale_numbers = set(reassigned_numbers)
    violations: list[str] = []
    for path in paths:
        cited_numbers = _design_decisions_citation_numbers(path.read_text(encoding="utf-8"))
        for number in sorted(cited_numbers & stale_numbers):
            violations.append(f"{path}: cites reassigned legacy §{number}")
    return violations


def test_no_live_citation_to_reassigned_legacy_numbers() -> None:
    """Assertion 6: no hook, script, or agent citing into
    docs/design-decisions/ still targets §59 or §60, the two legacy numbers
    the 2026-08-24 split reassigned to a different decision each -- see the
    comment above _REASSIGNED_LEGACY_NUMBERS for why a resolution-only
    check would miss this."""
    paths = _live_code_citation_files()
    assert paths, "no live-code citation files found -- scan scope may be misconfigured"
    violations = _reassigned_citation_violations(paths)
    assert not violations, "\n".join(violations)


_ITEM_12A_HEADING = (
    "12a. **Comment/prose discipline on added or modified text**"
)
_ITEM_12A_SECTION_RE = re.compile(re.escape(_ITEM_12A_HEADING) + r"[^\n]*\n((?:   - .+\n)+)")
_ITEM_12A_BULLET_RE = re.compile(r"^   - (.+)$", re.MULTILINE)
_CORE_REVIEW_ANGLES_SECTION_RE = re.compile(
    r"## Core review angles\n(.*?)(?=\n## |\Z)", re.DOTALL
)
# The bold run must be followed by " — " on the same line -- the literal
# separator every real angle header uses (e.g. "**Comment verbosity** — a
# comment..."). The name excludes "—" so it splits identically to the bullet
# side, which takes the text before the first " — ".
_CORE_REVIEW_ANGLE_HEADER_RE = re.compile(r"^\*\*([^*—]+)\*\* — ", re.MULTILINE)


def _item_12a_bullets(skill_text: str) -> list[str]:
    match = _ITEM_12A_SECTION_RE.search(skill_text)
    if not match:
        return []
    return _ITEM_12A_BULLET_RE.findall(match.group(1))


def _core_review_angle_headers(agent_text: str) -> list[str]:
    match = _CORE_REVIEW_ANGLES_SECTION_RE.search(agent_text)
    if not match:
        return []
    return _CORE_REVIEW_ANGLE_HEADER_RE.findall(match.group(1))


_NO_ANGLE_HEADERS_VIOLATION = (
    "comment-discipline-reviewer.md: 'Core review angles' section "
    "not found, or it holds no '**Name** — ' angle headers -- update "
    "_CORE_REVIEW_ANGLES_SECTION_RE/_CORE_REVIEW_ANGLE_HEADER_RE to "
    "match the current heading and header shape."
)


def _item_12a_index_violations(skill_text: str, agent_text: str) -> list[str]:
    bullets = _item_12a_bullets(skill_text)
    headers = _core_review_angle_headers(agent_text)
    if not bullets:
        return [
            "code-review/SKILL.md: item 12a's sub-bullet list not found or "
            "reworded -- update _ITEM_12A_HEADING/_ITEM_12A_SECTION_RE to "
            "match its current phrasing."
        ]
    if not headers:
        return [_NO_ANGLE_HEADERS_VIOLATION]
    violations: list[str] = []
    # Checked independently because duplicate headers plus matching duplicate bullets satisfy sequence equality.
    duplicated_headers = sorted({header for header in headers if headers.count(header) > 1})
    if duplicated_headers:
        violations.append(
            "comment-discipline-reviewer.md's 'Core review angles' section "
            f"repeats header(s) {duplicated_headers} -- a duplicated header "
            "can hold the sequence steady while silently standing in for a "
            "distinct angle."
        )
    bullet_names = [bullet.split(" — ", 1)[0].strip() for bullet in bullets]
    if bullet_names != headers:
        violations.append(
            f"code-review/SKILL.md's item 12a bullet names {bullet_names} "
            "don't match comment-discipline-reviewer.md's core review angle "
            f"headers {headers}, in order -- keep the index bullet list in "
            "lockstep with the agent's angle list, in the same order."
        )
    return violations


def test_item_12a_index_matches_agent_angle_headers() -> None:
    """Pins each item 12a bullet name, in order, to the agent's angle headers; rejects duplicate headers.

    A bold run followed by ' — ' at the start of a wrapped body line parses
    as a header (known limitation; the mismatch violation fails closed).
    Angle headers placed under a new '## ' heading inside the agent file fall
    outside the parsed section, and so outside the index."""
    skill_path = REPO_ROOT / "claude-skills" / "skills" / "code-review" / "SKILL.md"
    agent_path = CLAUDE_DIR / "agents" / "comment-discipline-reviewer.md"
    violations = _item_12a_index_violations(
        skill_path.read_text(encoding="utf-8"),
        agent_path.read_text(encoding="utf-8"),
    )
    assert not violations, "\n".join(violations)


_SCOPE_SECTION_RE = re.compile(r"^## Scope$\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE)
# The angle names the agent's Scope section cites.
# This tuple is maintained by hand.
# Update it whenever the Scope section cites a new angle.
# The check runs only from the tuple to the file, so an angle the Scope section cites but the tuple omits goes unchecked.
_SCOPE_CITED_ANGLE_NAMES = (
    "Multi-fact comment structure",
    "PR-defined terminology",
    "Prose at the wrong altitude",
)


def _scope_cited_angle_violations(
    agent_text: str,
    cited_angle_names: tuple[str, ...] = _SCOPE_CITED_ANGLE_NAMES,
) -> list[str]:
    scope_match = _SCOPE_SECTION_RE.search(agent_text)
    if not scope_match:
        return [
            "comment-discipline-reviewer.md: 'Scope' section not found -- "
            "update _SCOPE_SECTION_RE to match the current heading."
        ]
    headers = _core_review_angle_headers(agent_text)
    if not headers:
        return [_NO_ANGLE_HEADERS_VIOLATION]
    scope_text = " ".join(scope_match.group(1).split())
    violations: list[str] = []
    for angle_name in cited_angle_names:
        if angle_name not in headers:
            violations.append(
                f"comment-discipline-reviewer.md: cited angle "
                f"'{angle_name}' is not a header in 'Core review "
                f"angles' (found {headers}) -- if the angle was renamed, "
                "update the Scope section and _SCOPE_CITED_ANGLE_NAMES; if "
                "it was removed, drop the name from _SCOPE_CITED_ANGLE_NAMES."
            )
        if angle_name not in scope_text:
            violations.append(
                f"comment-discipline-reviewer.md's Scope section does not "
                f"cite angle '{angle_name}' -- restore the reference or drop "
                "the name from _SCOPE_CITED_ANGLE_NAMES."
            )
    return violations


def test_scope_cited_angle_names_resolve_to_angle_headers() -> None:
    """Guards each angle name in _SCOPE_CITED_ANGLE_NAMES.
    An angle rename would otherwise leave the Scope section citing a name
    that no longer exists.
    The item 12a lockstep test cannot see that, because it parses only
    'Core review angles'.
    A name counts as cited when it appears anywhere in the Scope text, in any
    clause."""
    assert _SCOPE_CITED_ANGLE_NAMES, (
        "no Scope-cited angle names configured -- _SCOPE_CITED_ANGLE_NAMES may be misconfigured"
    )
    agent_path = CLAUDE_DIR / "agents" / "comment-discipline-reviewer.md"
    violations = _scope_cited_angle_violations(agent_path.read_text(encoding="utf-8"))
    assert not violations, "\n".join(violations)


def test_item_12a_bullets_parse_annotated_bare_and_quoted_names() -> None:
    """Pins the parsed bullet names, in order, for the real SKILL.md bullet
    shape (annotated and bare bullets, a name containing quotes), plus a
    trailing paragraph that isn't part of the list."""
    skill_text = (
        "12a. **Comment/prose discipline on added or modified text** — "
        "Does a new or modified comment violate CLAUDE.md?\n"
        "   - Comment verbosity — a multi-paragraph rationale where one line suffices\n"
        "   - Prose at the wrong altitude\n"
        '   - "Used to be X" framing\n'
        "   - Restated canonical rule — a record-style doc paragraph restating a rule\n\n"
        "   Distinct from item 12, which covers comment *deletion*.\n"
    )
    assert _item_12a_bullets(skill_text) == [
        "Comment verbosity — a multi-paragraph rationale where one line suffices",
        "Prose at the wrong altitude",
        '"Used to be X" framing',
        "Restated canonical rule — a record-style doc paragraph restating a rule",
    ]


def test_core_review_angle_headers_parse_wrapped_bodies_and_nested_list() -> None:
    """Pins the parsed header names, in order, for the real agent shape:
    header bodies wrapped over several lines, a nested sublist under one
    angle, a '### ' subheading between angle headers that does not end the
    section, a header name containing quotes, and a following section whose
    bold text is excluded."""
    agent_text = (
        "## Core review angles\n\n"
        "Each angle below is a rule, applied per-site.\n\n"
        "**Comment verbosity** — a comment or doc paragraph stating a\n"
        "non-obvious constraint in more than one sentence when one line\n"
        "would carry the same fact.\n\n"
        "**PR-defined terminology** — a label meaningful only inside this\n"
        "PR's own narrative. Two shapes realize this:\n\n"
        "- A tracker ID carrying a phase or step qualifier.\n"
        '- An invented codename ("Defense A", "Action 6").\n\n'
        "### Group\n\n"
        '**"Used to be X" framing** — any prior-version comparison inside\n'
        "a comment or durable doc.\n\n"
        "## How to work\n\n"
        "**Not an angle** — belongs to the following section.\n"
    )
    assert _core_review_angle_headers(agent_text) == [
        "Comment verbosity",
        "PR-defined terminology",
        '"Used to be X" framing',
    ]


def _index_mismatch_message(bullet_names: list[str], headers: list[str]) -> str:
    """The mismatch fragment naming which list holds which names, so a test
    asserting on it fails if the bullet and header lists are swapped."""
    return (
        f"item 12a bullet names {bullet_names} "
        "don't match comment-discipline-reviewer.md's core review angle "
        f"headers {headers}, in order"
    )


_PARAGRAPH_READABILITY_FIXTURE_PATH = (
    CLAUDE_DIR / "hooks" / "tests" / "fixtures" / "paragraph-readability.json"
)
_BACKTICK_SPAN_RE = re.compile(r"`[^`]+`")
_DIGIT_RUN_RE = re.compile(r"\d+")
# Anchored at the start of the excerpt string itself (not re.MULTILINE) --
# the excerpt is checked as a standalone string, not a line inside a larger
# text.
_BOLD_LABEL_CARVE_OUT_RE = re.compile(r"^- \*\*[^*]+\*\* — ")
# A closed fixture-authoring convention, not a natural-language
# classifier -- an excerpt shape these regexes don't list fails loudly,
# as a flagged violation, instead of passing silently:
# - Case-sensitivity follows comment-discipline-reviewer.md's violation
#   shape (lines 97-100: "Because X, do Y", the instruction after its own
#   rationale) versus its carve-out (lines 122-125: instruction-or-claim-
#   first, "do X because Y"). A capitalized sentence-initial
#   "Because"/"Though" matches the violation shape, never the carve-out,
#   so only a lowercase, instruction-first excerpt can pass.
# - "because" matches anywhere on a bare word boundary, including
#   mid-sentence with no comma -- the carve-out's own "do X because Y".
# - "so"/"though"/"but" must instead follow a comma, since each is also a
#   bare intensifier or exception sense elsewhere in a sentence (e.g. "so
#   elegant", "as though", "nothing but the best").
# - Whether an excerpt genuinely demonstrates its named shape beyond
#   matching this regex is left to the fixture author and reviewer, not
#   this regex.
_CAUSE_AND_EFFECT_MARKER_RE = re.compile(r"\bbecause\b|,\s*so\b")
_CLAIM_AND_HEDGE_MARKER_RE = re.compile(r",\s*(?:though|but)\b")
_MUST_PASS_CARVE_OUT_SHAPES = frozenset(
    {"cause_and_effect", "claim_and_hedge", "bold_label_and_definition"}
)


def _angle_block_text(agent_text: str, angle_name: str) -> str | None:
    """The named angle's own block within 'Core review angles' -- from its
    header line (inclusive, starting at its leading `**`) to the line
    before the next angle header or the end of the section. Assertion 9's
    hash check pins this slice: it is whitespace-collapsed and hashed with
    SHA-256. Rewording the block therefore changes the hash on purpose.
    Returns None when the angle isn't found or the section is missing.

    A genuine angle header always opens a new paragraph, so it is preceded
    by the section start or a blank line. Requiring that excludes a decoy:
    a bold-dash line that reads like a header but is actually a nested
    list item whose author forgot its leading '- '. Such a line sits
    directly after other body text with no blank line before it."""
    section_match = _CORE_REVIEW_ANGLES_SECTION_RE.search(agent_text)
    if not section_match:
        return None
    section_text = section_match.group(1)
    # _CORE_REVIEW_ANGLES_SECTION_RE consumes only one of the two newlines
    # after the '## Core review angles' heading, so a header with no prose
    # paragraph in front of it leaves a single leading newline in
    # section_text. That lone "\n" must count as a valid start alongside ""
    # (match.start() == 0) and a real "\n\n".
    header_matches = []
    for match in _CORE_REVIEW_ANGLE_HEADER_RE.finditer(section_text):
        preceding = section_text[: match.start()]
        if preceding in ("", "\n") or preceding.endswith("\n\n"):
            header_matches.append(match)
    for index, header_match in enumerate(header_matches):
        if header_match.group(1) != angle_name:
            continue
        start = header_match.start()
        end = (
            header_matches[index + 1].start()
            if index + 1 < len(header_matches)
            else len(section_text)
        )
        return section_text[start:end]
    return None


def _literal_occurs_outside_longer_run(literal: str, haystack: str) -> bool:
    """True when `literal` occurs in `haystack` without being embedded
    inside a longer alphanumeric run -- e.g. '30' inside '130' doesn't
    count. Bare substring containment would let a renumbered digit literal
    (must_flag's '30 days' corrupted to '130 days' in must_pass) pass
    undetected. This check applies the same alnum-boundary rule to a
    backtick span too, for consistency. In practice that extension is
    redundant, since a backtick span's own delimiters already make it
    boundary-safe."""
    pattern = r"(?<![0-9A-Za-z])" + re.escape(literal) + r"(?![0-9A-Za-z])"
    return re.search(pattern, haystack) is not None


def _paragraph_readability_fixture_violations(
    fixture: dict, agent_text: str
) -> list[str]:
    violations: list[str] = []
    headers = _core_review_angle_headers(agent_text)
    if not headers:
        return [_NO_ANGLE_HEADERS_VIOLATION]

    angle_name = fixture.get("angle", "")
    if angle_name not in headers:
        return [
            f"paragraph-readability.json: 'angle' {angle_name!r} is not a "
            f"current angle header (found {headers}) -- the angle was "
            "renamed or removed."
        ]

    block = _angle_block_text(agent_text, angle_name)
    if block is None:
        # angle_name can match the loose, blank-line-agnostic header scan
        # above while failing _angle_block_text()'s stricter one.
        # That happens when angle_name names a decoy bold-dash line.
        return [
            f"paragraph-readability.json: 'angle' {angle_name!r} matches a "
            "bold-dash line that isn't preceded by a blank line, so it "
            "isn't a genuine angle header."
        ]
    expected_hash = hashlib.sha256(" ".join(block.split()).encode("utf-8")).hexdigest()
    actual_hash = fixture.get("angle_text_sha256", "")
    if actual_hash != expected_hash:
        violations.append(
            f"paragraph-readability.json: 'angle_text_sha256' {actual_hash!r} "
            f"does not match the angle's current text (recomputed: "
            f"{expected_hash!r}) -- the angle was reworded. Before updating "
            "the hash, re-run the three Phase 2 fixture cells: old body on "
            "must_flag <= 1/5, new body on must_flag >= 4/5, new body on "
            "must_pass <= 1/5 (see "
            "docs/design-decisions/paragraph-readability-review-angle.md "
            "for the full re-run procedure). The fixture's own 'note' field "
            "records which of those three bars the last measurement "
            "actually met -- not all three."
        )

    # `.get(key, default)` only falls back when the key is absent, not when
    # its stored value is `None`. A JSON `null` value leaves the key present
    # but `None`, so `or` is needed to also catch that case.
    must_flag = fixture.get("must_flag") or ""
    must_pass = fixture.get("must_pass") or ""
    flag_backticks = _BACKTICK_SPAN_RE.findall(must_flag)
    flag_digits = _DIGIT_RUN_RE.findall(must_flag)
    if not flag_backticks:
        violations.append(
            "paragraph-readability.json: 'must_flag' carries no backticked span."
        )
    if not flag_digits:
        violations.append(
            "paragraph-readability.json: 'must_flag' carries no digit run."
        )
    missing_literals = sorted(
        {
            literal
            for literal in flag_backticks + flag_digits
            if not _literal_occurs_outside_longer_run(literal, must_pass)
        }
    )
    if missing_literals:
        violations.append(
            f"paragraph-readability.json: must_flag literal(s) {missing_literals} "
            "do not also appear in 'must_pass' -- must_pass would pass "
            "trivially by dropping a fact must_flag carries."
        )

    carve_outs = fixture.get("must_pass_carve_outs") or {}
    carve_out_names = set(carve_outs)
    if carve_out_names != _MUST_PASS_CARVE_OUT_SHAPES:
        violations.append(
            f"paragraph-readability.json: 'must_pass_carve_outs' names "
            f"{sorted(carve_out_names)}, expected exactly "
            f"{sorted(_MUST_PASS_CARVE_OUT_SHAPES)}."
        )
    for shape_name, excerpt in carve_outs.items():
        if excerpt not in must_pass:
            violations.append(
                f"paragraph-readability.json: must_pass_carve_outs[{shape_name!r}] "
                "excerpt does not occur verbatim in 'must_pass'."
            )
        if shape_name == "bold_label_and_definition" and not _BOLD_LABEL_CARVE_OUT_RE.match(
            excerpt
        ):
            violations.append(
                f"paragraph-readability.json: must_pass_carve_outs"
                f"['bold_label_and_definition'] excerpt {excerpt!r} does not "
                f"match {_BOLD_LABEL_CARVE_OUT_RE.pattern!r}."
            )
        elif shape_name == "cause_and_effect" and not _CAUSE_AND_EFFECT_MARKER_RE.search(
            excerpt
        ):
            violations.append(
                f"paragraph-readability.json: must_pass_carve_outs"
                f"['cause_and_effect'] excerpt {excerpt!r} carries no causal "
                f"marker matching {_CAUSE_AND_EFFECT_MARKER_RE.pattern!r}."
            )
        elif shape_name == "claim_and_hedge" and not _CLAIM_AND_HEDGE_MARKER_RE.search(
            excerpt
        ):
            violations.append(
                f"paragraph-readability.json: must_pass_carve_outs"
                f"['claim_and_hedge'] excerpt {excerpt!r} carries no hedge "
                f"marker matching {_CLAIM_AND_HEDGE_MARKER_RE.pattern!r}."
            )
    return violations


def test_paragraph_readability_fixture_matches_agent_angle() -> None:
    """Assertion 9: the committed paragraph-readability.json regression
    fixture still names a current comment-discipline-reviewer.md angle
    header, still hashes to that angle's current whitespace-collapsed text,
    carries every must_flag literal inside must_pass too, and names exactly
    the three must_pass_carve_outs shapes with each excerpt occurring
    verbatim in must_pass."""
    fixture = json.loads(
        _PARAGRAPH_READABILITY_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    agent_path = CLAUDE_DIR / "agents" / "comment-discipline-reviewer.md"
    violations = _paragraph_readability_fixture_violations(
        fixture, agent_path.read_text(encoding="utf-8")
    )
    assert not violations, "\n".join(violations)


class TestFaultInjection:
    """Negative-fixture coverage: each test builds a synthetic
    design-decisions/-shaped directory in tmp_path containing exactly the
    fault its paired assertion exists to catch, and asserts the checking
    function itself flags it -- proving the check's own logic, not just
    today's clean corpus, would catch a regression. The item-12a-index
    cross-file-index-consistency checks and the scope-cited-angle checks
    below deviate from that shape: the item-12a-index checks parse two
    unrelated doc files' text directly, and the scope-cited-angle checks
    parse one agent file's text directly, so both groups' fixtures are
    plain strings with no tmp_path directory built."""

    def test_filename_grammar_rejects_leading_digit(self, tmp_path: Path) -> None:
        (tmp_path / "1-leading-digit.md").write_text(
            "# Some Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        violations = _filename_grammar_violations(_decision_files(tmp_path))
        assert violations == ["1-leading-digit.md"]

    def test_corpus_guard_fails_on_empty_directory(self, tmp_path: Path) -> None:
        with pytest.raises(AssertionError):
            _assert_corpus_non_empty(_decision_files(tmp_path))

    def test_h1_count_detects_second_heading(self, tmp_path: Path) -> None:
        (tmp_path / "two-headings.md").write_text(
            "# First Decision\n\n# Second Decision\n\n"
            "Formerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        violations = _h1_count_violations(_decision_files(tmp_path))
        assert violations == ["two-headings.md"]

    def test_h1_count_detects_zero_headings(self, tmp_path: Path) -> None:
        (tmp_path / "no-heading.md").write_text(
            "No H1 at all, just body prose.\n\n"
            "Formerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        violations = _h1_count_violations(_decision_files(tmp_path))
        assert violations == ["no-heading.md"]

    def test_legacy_numbers_detects_gap(self, tmp_path: Path) -> None:
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "second-decision.md").write_text(
            "# Second Decision\n\nFormerly `docs/design-decisions.md` §3.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(
            _decision_files(tmp_path), expected_legacy_numbers=frozenset({1, 2, 3})
        )
        assert len(violations) == 1
        assert "missing [2] from the expected closed set" in violations[0]

    def test_provenance_line_accepts_bare_date_no_formerly(self, tmp_path: Path) -> None:
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n*2026-09-07.*\n\nBody text.\n", encoding="utf-8"
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert violations == []

    def test_provenance_line_accepts_formerly_no_date(self, tmp_path: Path) -> None:
        """The shape 15 real files have.

        A decision that predates the split but whose original section
        carried no recorded date."""
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n*Formerly `docs/design-decisions.md` §1.*\n\n"
            "Body text.\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert violations == []

    def test_provenance_line_accepts_date_and_formerly(self, tmp_path: Path) -> None:
        """The shape 48/63 real files have -- a date and a Formerly clause
        together.

        Otherwise exercised only by the live-corpus test."""
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n*2026-01-01. Formerly "
            "`docs/design-decisions.md` §1.*\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert violations == []

    def test_provenance_line_rejects_missing_line(self, tmp_path: Path) -> None:
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\nNo provenance line at all -- just prose.\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "some-decision.md" in violations[0]

    def test_provenance_line_rejects_file_under_three_lines(self, tmp_path: Path) -> None:
        """Exercises the `lines[2] if len(lines) >= 3 else ""` fallback --
        a file too short to even have a line 3 must still fail the shape
        check rather than raise an IndexError."""
        (tmp_path / "short-decision.md").write_text("# Title\n", encoding="utf-8")
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "found ''" in violations[0]

    def test_provenance_line_rejects_bold_line(self, tmp_path: Path) -> None:
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n**2026-01-01. Formerly "
            "`docs/design-decisions.md` §1.**\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "must be an italic provenance line" in violations[0]

    def test_provenance_line_rejects_neither_date_nor_formerly(self, tmp_path: Path) -> None:
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n*Just italic prose, no provenance data at all.*\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "carries neither" in violations[0]

    def test_provenance_line_rejects_malformed_date(self, tmp_path: Path) -> None:
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n*2026-13-45. Formerly "
            "`docs/design-decisions.md` §1.*\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "2026-13-45" in violations[0]

    def test_provenance_line_rejects_garbled_formerly_with_date(self, tmp_path: Path) -> None:
        """A co-occurring valid date must not excuse a 'Formerly' clause that
        fails to match _PROVENANCE_RE -- has_formerly stays load-bearing even
        when a date is also present on the line."""
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n*2026-01-01. Formerly section one of the "
            "old doc.*\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "has a date but its 'Formerly' clause doesn't match" in violations[0]

    def test_legacy_closure_detects_dropped_highest_number(self, tmp_path: Path) -> None:
        """A fixed expected set catches losing the top element, which a
        max()-derived range would silently accept -- removing the highest
        number leaves the rest trivially contiguous under that scheme."""
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "second-decision.md").write_text(
            "# Second Decision\n\nFormerly `docs/design-decisions.md` §2.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(
            _decision_files(tmp_path), expected_legacy_numbers=frozenset({1, 2, 3})
        )
        assert len(violations) == 1
        assert "missing [3] from the expected closed set" in violations[0]

    def test_legacy_closure_detects_duplicate_within_full_coverage(
        self, tmp_path: Path
    ) -> None:
        """Recorded numbers cover the expected set in full ({1, 2, 3}) while
        §2 is claimed twice, forcing the cardinality check rather than
        coincidentally re-testing the gap path above."""
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "second-decision.md").write_text(
            "# Second Decision\n\nFormerly `docs/design-decisions.md` §2.\n",
            encoding="utf-8",
        )
        (tmp_path / "third-decision.md").write_text(
            "# Third Decision\n\nFormerly `docs/design-decisions.md` §2.\n",
            encoding="utf-8",
        )
        (tmp_path / "fourth-decision.md").write_text(
            "# Fourth Decision\n\nFormerly `docs/design-decisions.md` §3.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(
            _decision_files(tmp_path), expected_legacy_numbers=frozenset({1, 2, 3})
        )
        assert len(violations) == 1
        assert "duplicate" in violations[0].lower()

    def test_legacy_closure_rejects_number_beyond_closed_set(self, tmp_path: Path) -> None:
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "second-decision.md").write_text(
            "# Second Decision\n\nFormerly `docs/design-decisions.md` §2.\n",
            encoding="utf-8",
        )
        (tmp_path / "third-decision.md").write_text(
            "# Third Decision\n\nFormerly `docs/design-decisions.md` §3.\n",
            encoding="utf-8",
        )
        (tmp_path / "fourth-decision.md").write_text(
            "# Fourth Decision\n\nFormerly `docs/design-decisions.md` §64.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(
            _decision_files(tmp_path), expected_legacy_numbers=frozenset({1, 2, 3})
        )
        assert len(violations) == 1
        assert "outside the expected closed set" in violations[0]
        assert "include [64]," in violations[0]

    def test_provenance_line_rejects_garbled_formerly_no_date(self, tmp_path: Path) -> None:
        """has_formerly must come from an anchored _PROVENANCE_RE match, not
        a loose 'Formerly' substring check -- this fixture is properly
        italicized and contains the word 'Formerly', so a substring-based
        has_formerly would wrongly treat it as well-formed. With no date
        present either, an anchored match correctly falls through to the
        'carries neither' branch. Distinct from
        test_provenance_line_rejects_garbled_formerly_with_date, which
        requires a co-occurring date to reach the `elif` branch instead."""
        (tmp_path / "malformed-decision.md").write_text(
            "# Malformed Decision\n\n*Formerly `docs/design-decisions.md` §1*\n",
            encoding="utf-8",
        )
        violations = _provenance_line_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "carries neither" in violations[0]

    def test_malformed_formerly_fragment_detected_outside_line_three(
        self, tmp_path: Path
    ) -> None:
        """A garbled 'Formerly `docs/design-decisions.md`'-shaped fragment
        landed outside line 3 -- a corpus-corruption artifact that repeats a
        number already legitimately recorded by an unrelated file -- must
        still be caught even though the file's own line 3 is a clean,
        well-formed provenance line (a bare date, no Formerly clause).
        Covers a gap _provenance_line_violations' line-3-only shape check
        can't reach, since it never inspects the rest of the file."""
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §5.\n",
            encoding="utf-8",
        )
        (tmp_path / "second-decision.md").write_text(
            "# Second Decision\n\n*2026-01-01.*\n\n"
            "Body prose that garbles the record: Formerly "
            "`docs/design-decisions.md` §5, no trailing period or italics.\n",
            encoding="utf-8",
        )
        violations = _malformed_formerly_fragment_violations(
            _decision_files(tmp_path)
        )
        assert len(violations) == 1
        assert "second-decision.md" in violations[0]

    def test_legacy_closure_accepts_full_default_closed_set(self, tmp_path: Path) -> None:
        """Exercises _legacy_number_range_violations against its actual
        default _CLOSED_LEGACY_NUMBERS (1..63) rather than an overridden
        small set, unlike every other TestFaultInjection case above."""
        for number in range(1, 64):
            (tmp_path / f"decision-{number}.md").write_text(
                f"# Decision {number}\n\n"
                f"Formerly `docs/design-decisions.md` §{number}.\n",
                encoding="utf-8",
            )
        violations = _legacy_number_range_violations(_decision_files(tmp_path))
        assert violations == []

    def test_legacy_closure_rejects_number_beyond_default_closed_set(
        self, tmp_path: Path
    ) -> None:
        for number in range(1, 64):
            (tmp_path / f"decision-{number}.md").write_text(
                f"# Decision {number}\n\n"
                f"Formerly `docs/design-decisions.md` §{number}.\n",
                encoding="utf-8",
            )
        (tmp_path / "decision-64.md").write_text(
            "# Decision 64\n\nFormerly `docs/design-decisions.md` §64.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "outside the expected closed set" in violations[0]
        assert "include [64]," in violations[0]

    def test_numbered_heading_detects_revived_heading(self, tmp_path: Path) -> None:
        (tmp_path / "some-decision.md").write_text(
            "# Some Decision\n\n## 3. Old Section\n\n"
            "Formerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        violations = _numbered_heading_violations(_decision_files(tmp_path))
        assert violations == ["some-decision.md"]

    def test_citation_provenance_mismatch_detected(self, tmp_path: Path) -> None:
        (tmp_path / "citing-decision.md").write_text(
            "# Citing Decision\n\nSee [§2](cited-decision.md).\n\n"
            "Formerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "cited-decision.md").write_text(
            "# Cited Decision\n\nFormerly `docs/design-decisions.md` §5.\n",
            encoding="utf-8",
        )
        violations = _citation_provenance_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "records §5" in violations[0]

    def test_relative_link_one_directory_too_shallow_fails_resolution(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "hooks.md").write_text(
            "# Hooks\n\nFormerly `docs/design-decisions.md` §2.\n", encoding="utf-8"
        )
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "some-decision.md").write_text(
            "# Some Decision\n\nSee [hooks](hooks.md) for details.\n\n"
            "Formerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        checked, violations = _relative_link_violations(_decision_files(nested))
        assert checked == 1
        assert violations and "hooks.md" in violations[0]

    def test_reassigned_citation_detected(self, tmp_path: Path) -> None:
        stale_hook = tmp_path / "some-hook.sh"
        stale_hook.write_text(
            "# see docs/design-decisions.md §59 for the corpus basis, and\n"
            "# docs/design-decisions.md §54 for the unrelated invariant.\n",
            encoding="utf-8",
        )
        violations = _reassigned_citation_violations(
            [stale_hook], reassigned_numbers=(59,)
        )
        # §54 is a real, recognized design-decisions.md citation too (not a
        # reassigned one) -- proves the check reports only the reassigned
        # number it was asked about, not every citation it sees.
        assert len(violations) == 1
        assert "§59" in violations[0]

    def test_reassigned_citation_detected_hash_form(self, tmp_path: Path) -> None:
        stale_hook = tmp_path / "another-hook.sh"
        stale_hook.write_text(
            "# see docs/design-decisions.md #59 for the corpus basis.\n",
            encoding="utf-8",
        )
        violations = _reassigned_citation_violations(
            [stale_hook], reassigned_numbers=(59,)
        )
        assert len(violations) == 1
        assert "§59" in violations[0]

    def test_unrelated_document_section_number_not_flagged(self, tmp_path: Path) -> None:
        """A `§N`-shaped citation into a document other than
        docs/design-decisions.md -- e.g. handoff/SKILL.md's own §3.5
        subsection numbering -- must not be treated as a design-decisions
        citation just because the digit matches a reassigned number."""
        unrelated_hook = tmp_path / "some-other-hook.sh"
        unrelated_hook.write_text(
            "# Verbatim copy of handoff/SKILL.md's §3.5 categorization-rule "
            "anchor shapes.\n",
            encoding="utf-8",
        )
        violations = _reassigned_citation_violations(
            [unrelated_hook], reassigned_numbers=(3,)
        )
        assert violations == []

    def test_item_12a_index_detects_shorter_bullet_list(self) -> None:
        """A bullet list one entry shorter than the header list -- an angle
        added to the agent with no corresponding bullet authored."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Multi-fact comment structure — several facts chained\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Multi-fact comment structure** — several facts chained.\n\n"
            "**Restated canonical rule** — a rule stated elsewhere.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert _index_mismatch_message(
            bullet_names=["Comment verbosity", "Multi-fact comment structure"],
            headers=[
                "Comment verbosity",
                "Multi-fact comment structure",
                "Restated canonical rule",
            ],
        ) in violations[0]

    def test_item_12a_index_detects_longer_bullet_list(self) -> None:
        """The reverse drift direction from
        test_item_12a_index_detects_shorter_bullet_list: a bullet added to
        the SKILL.md index with no corresponding agent angle."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Multi-fact comment structure — several facts chained\n"
            "   - Restated canonical rule — a rule stated elsewhere\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert _index_mismatch_message(
            bullet_names=[
                "Comment verbosity",
                "Multi-fact comment structure",
                "Restated canonical rule",
            ],
            headers=["Comment verbosity"],
        ) in violations[0]

    def test_item_12a_index_detects_duplicated_bullet(self) -> None:
        """A future edit could copy-paste an existing sub-bullet instead of
        authoring a new one for a newly added angle, holding the list
        length steady. The duplicated name still diverges from the header
        at that position, which the sequence-equality check catches."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Multi-fact comment structure — several facts chained\n"
            "   - Comment verbosity — a multi-paragraph rationale\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Multi-fact comment structure** — several facts chained.\n\n"
            "**Restated canonical rule** — a rule stated elsewhere.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert _index_mismatch_message(
            bullet_names=[
                "Comment verbosity",
                "Multi-fact comment structure",
                "Comment verbosity",
            ],
            headers=[
                "Comment verbosity",
                "Multi-fact comment structure",
                "Restated canonical rule",
            ],
        ) in violations[0]

    def test_item_12a_index_detects_renamed_angle_with_stale_bullet(self) -> None:
        """A header renamed on the agent side leaves the SKILL.md bullet
        pointed at the old name. Both lists stay the same length, so only a
        position-by-position name comparison -- not a count check -- catches
        it."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Multi-fact comment structure — several facts chained\n"
            "   - Restated canonical rule — a rule stated elsewhere\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Multi-fact prose structure** — several facts chained.\n\n"
            "**Restated canonical rule** — a rule stated elsewhere.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert _index_mismatch_message(
            bullet_names=[
                "Comment verbosity",
                "Multi-fact comment structure",
                "Restated canonical rule",
            ],
            headers=[
                "Comment verbosity",
                "Multi-fact prose structure",
                "Restated canonical rule",
            ],
        ) in violations[0]

    def test_item_12a_index_detects_reordered_bullets(self) -> None:
        """The same bullets and the same headers, in different relative
        order -- a length- or set-equality check would pass this fixture.
        Ordered sequence equality is what catches it."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Multi-fact comment structure — several facts chained\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Restated canonical rule — a rule stated elsewhere\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Multi-fact comment structure** — several facts chained.\n\n"
            "**Restated canonical rule** — a rule stated elsewhere.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert _index_mismatch_message(
            bullet_names=[
                "Multi-fact comment structure",
                "Comment verbosity",
                "Restated canonical rule",
            ],
            headers=[
                "Comment verbosity",
                "Multi-fact comment structure",
                "Restated canonical rule",
            ],
        ) in violations[0]

    def test_item_12a_index_detects_duplicated_header(self) -> None:
        """Duplicate agent headers fail even when bullets repeat them identically, so sequence equality holds."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Multi-fact comment structure — several facts chained\n"
            "   - Comment verbosity — a multi-paragraph rationale\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Multi-fact comment structure** — several facts chained.\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert "repeats header(s) ['Comment verbosity']" in violations[0]

    def test_item_12a_index_rejects_missing_skill_section(self) -> None:
        """A future rewording of the item 12a heading in SKILL.md must fail
        loud through this guard, instead of _item_12a_bullets silently
        returning []."""
        skill_text = "12a. This heading no longer matches _ITEM_12A_SECTION_RE.\n"
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert "item 12a's sub-bullet list not found or reworded" in violations[0]

    def test_item_12a_index_rejects_missing_agent_section(self) -> None:
        """A future rewording of the 'Core review angles' heading in the
        agent file must fail loud through this guard, instead of
        _core_review_angle_headers silently returning []."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = "## Review angles, renamed\n\n**Comment verbosity** — a comment.\n\n## How to work\n"
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert "'Core review angles' section not found" in violations[0]

    def test_item_12a_index_rejects_section_without_name_dash_headers(self) -> None:
        """A present 'Core review angles' section holding no '**Name** — '
        headers takes the same not-found guard as a missing section."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = "## Core review angles\n\nComment verbosity: no bold header.\n"
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert "holds no '**Name** — ' angle headers" in violations[0]

    def test_core_review_angles_section_ends_at_any_following_heading(self) -> None:
        """The section ends at the next '## ' heading whatever it is named,
        so renaming the following heading neither misdirects the parse nor
        pulls that section's bold headers into the angle list."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "## Workflow, renamed\n\n"
            "**Not an angle** — belongs to the following section.\n"
        )
        assert _core_review_angle_headers(agent_text) == ["Comment verbosity"]
        assert _item_12a_index_violations(skill_text, agent_text) == []

    def test_item_12a_index_accepts_matched_sequence(self) -> None:
        """Mixes an annotated bullet with a bare one (no ' — '), as the real
        SKILL.md does."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Restated canonical rule\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Restated canonical rule** — a rule stated elsewhere.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert violations == []

    def test_item_12a_index_rejects_header_name_containing_em_dash(self) -> None:
        """A header name containing ' — ' can't be matched by a bullet, whose
        name is the text before the first ' — '. The header fails the
        '**Name** — ' shape and drops out of the parsed list, so the bullet
        and header lists differ and the mismatch violation fails closed."""
        skill_text = (
            "12a. **Comment/prose discipline on added or modified text** — "
            "Does a new or modified comment or durable-doc paragraph violate "
            "CLAUDE.md?\n"
            "   - Comment verbosity — a multi-paragraph rationale\n"
            "   - Restated canonical rule — a rule stated elsewhere\n\n"
            "   Distinct from item 12, which covers comment deletion.\n"
        )
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Restated canonical rule — dupes** — a rule stated elsewhere.\n\n"
            "## How to work\n"
        )
        violations = _item_12a_index_violations(skill_text, agent_text)
        assert len(violations) == 1
        assert _index_mismatch_message(
            bullet_names=["Comment verbosity", "Restated canonical rule"],
            headers=["Comment verbosity"],
        ) in violations[0]

    def test_scope_cited_angles_rejects_renamed_angle_header(self) -> None:
        """Renaming a cited angle's header leaves the Scope section citing a
        name that no longer resolves."""
        agent_text = (
            "## Scope\n\n"
            "Plan files are in scope for Multi-fact comment structure only;\n"
            "PR-defined terminology still applies elsewhere.\n\n"
            "## Core review angles\n\n"
            "**Multi-fact comment structure, renamed** — several facts.\n\n"
            "**PR-defined terminology** — a label defined in the PR.\n\n"
            "## How to work\n"
        )
        violations = _scope_cited_angle_violations(
            agent_text,
            cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
        )
        assert len(violations) == 1
        assert "cited angle 'Multi-fact comment structure' is not a header" in violations[0]

    def test_scope_cited_angles_rejects_name_removed_from_scope(self) -> None:
        """Deleting a cited name from the Scope section fails even though the
        angle header still exists."""
        agent_text = (
            "## Scope\n\n"
            "Plan files are in scope for Multi-fact comment structure only.\n\n"
            "## Core review angles\n\n"
            "**Multi-fact comment structure** — several facts.\n\n"
            "**PR-defined terminology** — a label defined in the PR.\n\n"
            "## How to work\n"
        )
        violations = _scope_cited_angle_violations(
            agent_text,
            cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
        )
        assert len(violations) == 1
        assert "does not cite angle 'PR-defined terminology'" in violations[0]

    def test_scope_cited_angles_accepts_cited_name_split_across_lines(self) -> None:
        """A line reflow that splits a cited name between words does not fail."""
        agent_text = (
            "## Scope\n\n"
            "Plan files are in scope for Multi-fact\ncomment structure only;\n"
            "PR-defined\nterminology still applies elsewhere.\n\n"
            "## Core review angles\n\n"
            "**Multi-fact comment structure** — several facts.\n\n"
            "**PR-defined terminology** — a label defined in the PR.\n\n"
            "## How to work\n"
        )
        assert (
            _scope_cited_angle_violations(
                agent_text,
                cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
            )
            == []
        )

    def test_scope_cited_angles_ignores_decoy_heading_and_spans_subheadings(self) -> None:
        """A '### Scope' heading before the real '## Scope' is not parsed as
        the section start.
        A '### ' subheading inside '## Scope' does not end the section, so
        names cited after it still count."""
        agent_text = (
            "## Overview\n\n"
            "### Scope\n\n"
            "A decoy subheading that cites no angle names.\n\n"
            "## Scope\n\n"
            "### Plan files\n\n"
            "Plan files are in scope for Multi-fact comment structure only;\n"
            "PR-defined terminology still applies elsewhere.\n\n"
            "## Core review angles\n\n"
            "**Multi-fact comment structure** — several facts.\n\n"
            "**PR-defined terminology** — a label defined in the PR.\n\n"
            "## How to work\n"
        )
        assert (
            _scope_cited_angle_violations(
                agent_text,
                cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
            )
            == []
        )

    def test_scope_cited_angles_rejects_missing_scope_section(self) -> None:
        """A future rewording of the 'Scope' heading must fail loud instead of
        the Scope text silently reading as empty."""
        agent_text = (
            "## Review scope, renamed\n\n"
            "Multi-fact comment structure and PR-defined terminology.\n\n"
            "## Core review angles\n\n"
            "**Multi-fact comment structure** — several facts.\n\n"
            "**PR-defined terminology** — a label defined in the PR.\n\n"
            "## How to work\n"
        )
        violations = _scope_cited_angle_violations(
            agent_text,
            cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
        )
        assert len(violations) == 1
        assert "'Scope' section not found" in violations[0]

    def test_scope_cited_angles_rejects_missing_angles_section(self) -> None:
        """A future rewording of the 'Core review angles' heading must fail
        loud through this guard, instead of every cited name reading as
        unresolved."""
        agent_text = (
            "## Scope\n\n"
            "Multi-fact comment structure and PR-defined terminology.\n\n"
            "## Review angles, renamed\n\n"
            "**Multi-fact comment structure** — several facts.\n\n"
            "## How to work\n"
        )
        violations = _scope_cited_angle_violations(
            agent_text,
            cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
        )
        assert len(violations) == 1
        assert "'Core review angles' section not found" in violations[0]

    def test_scope_cited_angles_rejects_name_missing_from_headers_and_scope(self) -> None:
        """A cited name absent from both the angle headers and the Scope text
        yields one violation per failed check."""
        agent_text = (
            "## Scope\n\n"
            "Plan files are in scope for Multi-fact comment structure only.\n\n"
            "## Core review angles\n\n"
            "**Multi-fact comment structure** — several facts.\n\n"
            "## How to work\n"
        )
        violations = _scope_cited_angle_violations(
            agent_text,
            cited_angle_names=("Multi-fact comment structure", "PR-defined terminology"),
        )
        assert len(violations) == 2
        assert "cited angle 'PR-defined terminology' is not a header" in violations[0]
        assert "does not cite angle 'PR-defined terminology'" in violations[1]

    # Assertion 9: paragraph-readability.json fixture checks. Plain-string
    # fixtures with no tmp_path directory, mirroring the item-12a-index and
    # scope-cited-angle groups above -- the checked function parses a
    # fixture dict and an agent-file string directly.
    _PARAGRAPH_READABILITY_AGENT_TEXT = (
        "## Core review angles\n\n"
        "**Comment verbosity** — a comment stating too much.\n\n"
        "**Paragraph readability** — a paragraph whose arrangement hides the instruction.\n\n"
        "**Prose at the wrong altitude** — content placed where its reader doesn't match.\n\n"
        "## How to work\n"
    )
    _PARAGRAPH_READABILITY_ANGLE_HASH = hashlib.sha256(
        "**Paragraph readability** — a paragraph whose arrangement hides the instruction.".encode()
    ).hexdigest()
    _VALID_PARAGRAPH_READABILITY_FIXTURE = {
        "angle": "Paragraph readability",
        "angle_text_sha256": _PARAGRAPH_READABILITY_ANGLE_HASH,
        "must_flag": "The cache stores a `token`. It expires after 30 days.",
        "must_pass": (
            "The cache stores a `token`. It expires after 30 days.\n\n"
            "A write must hold the lock, because two writers can otherwise corrupt it.\n\n"
            "The sweep runs nightly, though an emergency can trigger it early.\n\n"
            "- **Dry run** — a mode that validates inputs without writing any entry.\n"
        ),
        "must_pass_carve_outs": {
            "cause_and_effect": "A write must hold the lock, because two writers can otherwise corrupt it.",
            "claim_and_hedge": "The sweep runs nightly, though an emergency can trigger it early.",
            "bold_label_and_definition": "- **Dry run** — a mode that validates inputs without writing any entry.",
        },
    }

    def test_paragraph_readability_fixture_violations_accepts_valid_fixture(self) -> None:
        """The hand-built valid fixture and agent text pair, used as the
        mutation base for every fault test below, itself produces no
        violations -- proves the fault tests' single mutation is what
        trips each one, not a baseline already in error."""
        violations = _paragraph_readability_fixture_violations(
            self._VALID_PARAGRAPH_READABILITY_FIXTURE,
            self._PARAGRAPH_READABILITY_AGENT_TEXT,
        )
        assert violations == []

    def test_paragraph_readability_fixture_detects_renamed_angle(self) -> None:
        """The agent's header is renamed while the fixture's 'angle' field
        still names the old text -- the name no longer resolves to a
        current header."""
        agent_text = self._PARAGRAPH_READABILITY_AGENT_TEXT.replace(
            "**Paragraph readability**", "**Paragraph clarity**"
        )
        violations = _paragraph_readability_fixture_violations(
            self._VALID_PARAGRAPH_READABILITY_FIXTURE, agent_text
        )
        assert len(violations) == 1
        assert "is not a current angle header" in violations[0]

    def test_paragraph_readability_fixture_detects_removed_angle(self) -> None:
        """The angle is deleted from the agent file entirely -- the
        fixture's 'angle' field names a header that no longer exists at
        all, not merely a renamed one."""
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Prose at the wrong altitude** — content placed where its reader doesn't match.\n\n"
            "## How to work\n"
        )
        violations = _paragraph_readability_fixture_violations(
            self._VALID_PARAGRAPH_READABILITY_FIXTURE, agent_text
        )
        assert len(violations) == 1
        assert "is not a current angle header" in violations[0]

    def test_paragraph_readability_fixture_detects_reworded_angle_text(self) -> None:
        """The header name survives but its body text changes -- the stored
        hash no longer matches the recomputed one, which is the rewording
        drift the hash pin exists to catch."""
        agent_text = self._PARAGRAPH_READABILITY_AGENT_TEXT.replace(
            "a paragraph whose arrangement hides the instruction.",
            "a paragraph whose arrangement buries the instruction.",
        )
        violations = _paragraph_readability_fixture_violations(
            self._VALID_PARAGRAPH_READABILITY_FIXTURE, agent_text
        )
        assert len(violations) == 1
        assert "does not match the angle's current text" in violations[0]

    def test_paragraph_readability_fixture_accepts_block_through_nested_bold_dash_line(
        self,
    ) -> None:
        """A bullet inside the angle's own body written as '**Note** — ...'
        instead of '- **Note** — ...' is header-shaped but not preceded by a
        blank line, so the block still extends through it to the real next
        angle header. This proves `_angle_block_text` doesn't truncate early
        at a false-positive bold-dash line."""
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Paragraph readability** — a paragraph whose arrangement hides the instruction.\n"
            "**Note** — a bullet missing its leading '- ', so it reads like a header.\n\n"
            "**Prose at the wrong altitude** — content placed where its reader doesn't match.\n\n"
            "## How to work\n"
        )
        correctly_bounded_block = (
            "**Paragraph readability** — a paragraph whose arrangement hides the instruction. "
            "**Note** — a bullet missing its leading '- ', so it reads like a header."
        )
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["angle_text_sha256"] = hashlib.sha256(
            correctly_bounded_block.encode("utf-8")
        ).hexdigest()
        violations = _paragraph_readability_fixture_violations(fixture, agent_text)
        assert violations == []

    def test_paragraph_readability_fixture_resolves_angle_as_sections_first_header(
        self,
    ) -> None:
        """The queried angle is the section's literal first line, with no
        prose paragraph before it. _CORE_REVIEW_ANGLES_SECTION_RE consumes
        only one of the two newlines after the '## Core review angles'
        heading, so section_text still opens with a single leading newline
        rather than "". That leading newline must count as a valid header
        boundary too, or _angle_block_text wrongly treats the section's own
        first header as an unbounded decoy."""
        agent_text = (
            "## Core review angles\n\n"
            "**Paragraph readability** — a paragraph whose arrangement hides the instruction.\n\n"
            "**Prose at the wrong altitude** — content placed where its reader doesn't match.\n\n"
            "## How to work\n"
        )
        violations = _paragraph_readability_fixture_violations(
            self._VALID_PARAGRAPH_READABILITY_FIXTURE, agent_text
        )
        assert violations == []

    def test_paragraph_readability_fixture_resolves_angle_with_no_newline_before_header(
        self,
    ) -> None:
        """The '## Core review angles' heading is followed by a single
        newline, not two, so section_text opens directly with the header's
        leading '**' and match.start() is 0 for it -- no leading newline at
        all. That empty-string boundary must count as valid alongside a
        single leading newline and a real blank-line gap, or
        _angle_block_text wrongly treats the section's own first header as an
        unbounded decoy."""
        agent_text = (
            "## Core review angles\n"
            "**Paragraph readability** — a paragraph whose arrangement hides the instruction.\n\n"
            "**Prose at the wrong altitude** — content placed where its reader doesn't match.\n\n"
            "## How to work\n"
        )
        violations = _paragraph_readability_fixture_violations(
            self._VALID_PARAGRAPH_READABILITY_FIXTURE, agent_text
        )
        assert violations == []

    def test_paragraph_readability_fixture_rejects_angle_matching_only_decoy_header(
        self,
    ) -> None:
        """The fixture's 'angle' names the same decoy bold-dash line
        test_paragraph_readability_fixture_accepts_block_through_nested_bold_dash_line
        builds -- header-shaped per the loose header scan but not preceded
        by a blank line, so _angle_block_text returns None for it. The
        function must report a clean violation instead of raising when it
        tries to hash that None block."""
        agent_text = (
            "## Core review angles\n\n"
            "**Comment verbosity** — a comment stating too much.\n\n"
            "**Paragraph readability** — a paragraph whose arrangement hides the instruction.\n"
            "**Note** — a bullet missing its leading '- ', so it reads like a header.\n\n"
            "**Prose at the wrong altitude** — content placed where its reader doesn't match.\n\n"
            "## How to work\n"
        )
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["angle"] = "Note"
        violations = _paragraph_readability_fixture_violations(fixture, agent_text)
        assert len(violations) == 1
        assert "isn't a genuine angle header" in violations[0]

    def test_paragraph_readability_fixture_detects_dropped_literal(self) -> None:
        """must_pass drops the digit run must_flag carries -- a vacuous
        must_pass could otherwise pass by dropping a fact must_flag states."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass"] = fixture["must_pass"].replace("30 days", "some days")
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "do not also appear in 'must_pass'" in violations[0]
        assert "30" in violations[0]

    def test_paragraph_readability_fixture_detects_renumbered_digit_literal(self) -> None:
        """must_pass's digit run is renumbered rather than dropped -- '30
        days' corrupted to '130 days'. Bare substring containment would let
        '30' match inside '130' and miss this; the boundary-aware check
        must still catch it."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass"] = fixture["must_pass"].replace("30 days", "130 days")
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "do not also appear in 'must_pass'" in violations[0]
        assert "30" in violations[0]

    def test_paragraph_readability_fixture_detects_must_flag_with_no_literal(self) -> None:
        """must_flag carries neither a backticked span nor a digit run --
        two separate violations, one per missing literal class."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_flag"] = "The cache stores data and expires it eventually."
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 2
        assert "carries no backticked span" in violations[0]
        assert "carries no digit run" in violations[1]

    def test_paragraph_readability_fixture_detects_renamed_carve_out_key(self) -> None:
        """A must_pass_carve_outs key renamed (or, equivalently, dropped and
        a differently-named one added) no longer matches the expected
        exact shape set -- the set equality guards against a silently
        vacuous or mismatched carve-out collection."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        fixture["must_pass_carve_outs"]["cause_effect"] = fixture[
            "must_pass_carve_outs"
        ].pop("cause_and_effect")
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "'must_pass_carve_outs' names" in violations[0]
        assert "cause_effect" in violations[0]

    def test_paragraph_readability_fixture_detects_carve_out_excerpt_absent_from_must_pass(
        self,
    ) -> None:
        """A must_pass_carve_outs excerpt that doesn't occur verbatim in
        must_pass -- the carve-out shape it claims to demonstrate isn't
        actually present."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        fixture["must_pass_carve_outs"]["claim_and_hedge"] = (
            "The sweep runs weekly, though an emergency can trigger it early."
        )
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "does not occur verbatim in 'must_pass'" in violations[0]

    def test_paragraph_readability_fixture_detects_bold_label_carve_out_wrong_shape(
        self,
    ) -> None:
        """A bold_label_and_definition excerpt that occurs verbatim in
        must_pass but uses a colon instead of the leading '- ' and em dash
        the carve-out's own bold-label-dash shape requires."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        wrong_shape_excerpt = (
            "**Dry run**: a mode that validates inputs without writing any entry."
        )
        fixture["must_pass_carve_outs"]["bold_label_and_definition"] = wrong_shape_excerpt
        fixture["must_pass"] = fixture["must_pass"] + wrong_shape_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "does not match" in violations[0]

    def test_paragraph_readability_fixture_detects_dropped_backtick_literal(self) -> None:
        """must_pass drops the backtick-quoted literal must_flag carries --
        the direct backtick analogue of
        test_paragraph_readability_fixture_detects_dropped_literal's
        digit-run case."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass"] = fixture["must_pass"].replace("`token`", "a cache key")
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "do not also appear in 'must_pass'" in violations[0]
        assert "`token`" in violations[0]

    def test_paragraph_readability_fixture_treats_null_must_flag_as_absent(self) -> None:
        """A JSON `null` must_flag value leaves the key present but None.
        It must fall back to the same empty-string default an absent key
        gets, rather than raising TypeError out of
        `_BACKTICK_SPAN_RE.findall`."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_flag"] = None
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 2
        assert "carries no backticked span" in violations[0]
        assert "carries no digit run" in violations[1]

    def test_paragraph_readability_fixture_treats_null_must_pass_as_absent(self) -> None:
        """A JSON `null` must_pass value leaves the key present but None.
        It must fall back to the same empty-string default an absent key
        gets, rather than raising TypeError out of the `excerpt not in
        must_pass` carve-out check."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass"] = None
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 4
        assert "do not also appear in 'must_pass'" in violations[0]
        assert all(
            "does not occur verbatim in 'must_pass'" in violation
            for violation in violations[1:]
        )

    def test_paragraph_readability_fixture_treats_null_must_pass_carve_outs_as_absent(
        self,
    ) -> None:
        """A JSON `null` must_pass_carve_outs value leaves the key present
        but None. It must fall back to the same empty-dict default an
        absent key gets, rather than raising TypeError out of
        `set(carve_outs)`."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = None
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "'must_pass_carve_outs' names" in violations[0]
        assert "expected exactly" in violations[0]

    def test_paragraph_readability_fixture_detects_vacuous_cause_and_effect_carve_out(
        self,
    ) -> None:
        """A cause_and_effect excerpt that occurs verbatim in must_pass but
        carries no causal marker -- plain substring containment alone would
        accept any excerpt, including one that isn't actually a
        cause-and-effect construction."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        vacuous_excerpt = "Dogs."
        fixture["must_pass_carve_outs"]["cause_and_effect"] = vacuous_excerpt
        fixture["must_pass"] = fixture["must_pass"] + vacuous_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no causal marker" in violations[0]

    def test_paragraph_readability_fixture_detects_vacuous_claim_and_hedge_carve_out(
        self,
    ) -> None:
        """A claim_and_hedge excerpt that occurs verbatim in must_pass but
        carries no hedge marker -- the same vacuous-excerpt gap as the
        cause_and_effect carve-out above, for the hedge shape instead."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        vacuous_excerpt = "Cats."
        fixture["must_pass_carve_outs"]["claim_and_hedge"] = vacuous_excerpt
        fixture["must_pass"] = fixture["must_pass"] + vacuous_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no hedge marker" in violations[0]

    def test_paragraph_readability_fixture_detects_sentence_initial_cause_and_effect_carve_out(
        self,
    ) -> None:
        """A cause_and_effect excerpt opening with the capitalized
        sentence-initial marker ("Because X, Y.") is rejected -- that is
        the rationale-before-instruction violation shape
        comment-discipline-reviewer.md lines 97-100 name, not the
        instruction-or-claim-first carve-out at lines 122-123."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        sentence_initial_excerpt = "Because two writers can otherwise corrupt it, a write must hold the lock."
        fixture["must_pass_carve_outs"]["cause_and_effect"] = sentence_initial_excerpt
        fixture["must_pass"] = fixture["must_pass"] + sentence_initial_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no causal marker" in violations[0]

    def test_paragraph_readability_fixture_detects_sentence_initial_claim_and_hedge_carve_out(
        self,
    ) -> None:
        """A claim_and_hedge excerpt opening with the capitalized
        sentence-initial marker ("Though X, Y.") is rejected -- the same
        rationale-before-instruction violation shape as the
        cause_and_effect carve-out above (comment-discipline-reviewer.md
        lines 97-100 and 122-123), for the hedge shape instead."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        sentence_initial_excerpt = "Though an emergency can trigger it early, the sweep runs nightly."
        fixture["must_pass_carve_outs"]["claim_and_hedge"] = sentence_initial_excerpt
        fixture["must_pass"] = fixture["must_pass"] + sentence_initial_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no hedge marker" in violations[0]

    def test_paragraph_readability_fixture_detects_cause_and_effect_carve_out_using_so_as_intensifier(
        self,
    ) -> None:
        """A cause_and_effect excerpt carrying the word "so" only as a bare
        intensifier ("so elegant"), not a comma-led causal marker --
        a bare word-presence check would wrongly accept this shape."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        non_causal_excerpt = "The design is so elegant."
        fixture["must_pass_carve_outs"]["cause_and_effect"] = non_causal_excerpt
        fixture["must_pass"] = fixture["must_pass"] + non_causal_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no causal marker" in violations[0]

    def test_paragraph_readability_fixture_detects_claim_and_hedge_carve_out_using_but_as_exception(
        self,
    ) -> None:
        """A claim_and_hedge excerpt carrying the word "but" only in its
        "except" sense ("nothing but the best"), not a comma-led
        hedge marker -- the same bare word-presence gap as the
        cause_and_effect carve-out above, for the hedge shape instead."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        non_hedge_excerpt = "Nothing but the best will do."
        fixture["must_pass_carve_outs"]["claim_and_hedge"] = non_hedge_excerpt
        fixture["must_pass"] = fixture["must_pass"] + non_hedge_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no hedge marker" in violations[0]

    def test_paragraph_readability_fixture_accepts_mid_sentence_cause_and_effect_carve_out(
        self,
    ) -> None:
        """A cause_and_effect excerpt using the mid-sentence, no-comma
        "do X because Y" form passes -- the marker regex must not require
        the marker to open the excerpt or follow a comma."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        mid_sentence_excerpt = (
            "A write must hold the lock because two writers can otherwise "
            "corrupt it."
        )
        fixture["must_pass_carve_outs"]["cause_and_effect"] = mid_sentence_excerpt
        fixture["must_pass"] = fixture["must_pass"] + mid_sentence_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert violations == []

    def test_paragraph_readability_fixture_detects_claim_and_hedge_carve_out_using_as_though(
        self,
    ) -> None:
        """A claim_and_hedge excerpt carrying "though" only inside "as
        though" ("as if"), not a comma-led hedge marker -- a bare
        word-presence check would wrongly accept this shape."""
        fixture = dict(self._VALID_PARAGRAPH_READABILITY_FIXTURE)
        fixture["must_pass_carve_outs"] = dict(fixture["must_pass_carve_outs"])
        non_hedge_excerpt = "The cache behaves as though every write were atomic."
        fixture["must_pass_carve_outs"]["claim_and_hedge"] = non_hedge_excerpt
        fixture["must_pass"] = fixture["must_pass"] + non_hedge_excerpt + "\n"
        violations = _paragraph_readability_fixture_violations(
            fixture, self._PARAGRAPH_READABILITY_AGENT_TEXT
        )
        assert len(violations) == 1
        assert "carries no hedge marker" in violations[0]
