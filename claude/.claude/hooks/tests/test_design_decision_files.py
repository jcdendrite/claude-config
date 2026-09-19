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

Why hooks/tests/ instead of tests/ or skills/tests/:
  This module imports helpers.CLAUDE_DIR from the sibling helpers path. It
  lives in hooks/tests/ to match the co-location of test_doc_counts.py,
  which guards a related class of doc-vs-disk drift.
"""
from __future__ import annotations

import datetime
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
        return [
            "comment-discipline-reviewer.md: 'Core review angles' section "
            "not found, or it holds no '**Name** — ' angle headers -- update "
            "_CORE_REVIEW_ANGLES_SECTION_RE/_CORE_REVIEW_ANGLE_HEADER_RE to "
            "match the current heading and header shape."
        ]
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
    as a header (accepted limit; the mismatch violation fails closed).
    Angle headers placed under a new '## ' heading inside the agent file fall
    outside the parsed section, and so outside the index."""
    skill_path = REPO_ROOT / "claude-skills" / "skills" / "code-review" / "SKILL.md"
    agent_path = CLAUDE_DIR / "agents" / "comment-discipline-reviewer.md"
    violations = _item_12a_index_violations(
        skill_path.read_text(encoding="utf-8"),
        agent_path.read_text(encoding="utf-8"),
    )
    assert not violations, "\n".join(violations)


def test_item_12a_bullets_parse_annotated_bare_and_quoted_names() -> None:
    """Pins the parsed bullet names, in order, for the real SKILL.md shape:
    annotated and bare bullets, a name containing quotes, and a trailing
    paragraph that isn't part of the list."""
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


class TestFaultInjection:
    """Negative-fixture coverage: each test builds a synthetic
    design-decisions/-shaped directory in tmp_path containing exactly the
    fault its paired assertion exists to catch, and asserts the checking
    function itself flags it -- proving the check's own logic, not just
    today's clean corpus, would catch a regression. The item-12a-index
    cross-file-index-consistency checks below deviate from that shape: they
    parse two unrelated doc files' text directly, so their fixtures are
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
