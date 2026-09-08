"""Assert per-file shape, legacy-provenance completeness, no-revived-numbering,
intra-directory citation correctness, and relative-link resolution for
docs/design-decisions/.

docs/design-decisions.md was split into one file per decision under
docs/design-decisions/<slug>.md so two branches recording a decision never
collide on the same path. These are the permanent invariants that split
depends on:

  1. Per-file shape: filename matches the slug grammar, exactly one H1.
  2. The recorded legacy `§N` provenance values, read across the whole
     directory, form exactly {1..N} with no gaps, duplicates, or
     fabrications. A file recorded after the split carries no `Formerly §N`
     clause at all (permitted); one that carries the clause but fails to
     parse it is a malformed-provenance defect (not permitted).
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

Why hooks/tests/ instead of tests/ or skills/tests/:
  This module imports helpers.CLAUDE_DIR from the sibling helpers path. It
  lives in hooks/tests/ to match the co-location of test_doc_counts.py,
  which guards a related class of doc-vs-disk drift.
"""
from __future__ import annotations

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
# Looser than _PROVENANCE_RE: matches the clause's fixed prefix regardless
# of whether the §N suffix is well-formed, so _legacy_number_range_violations
# can tell "no Formerly clause at all" (a post-split decision, permitted)
# apart from "a Formerly clause that failed to parse" (malformed, a defect).
_FORMERLY_PREFIX_RE = re.compile(r"Formerly `docs/design-decisions\.md`")
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


def _legacy_number_range_violations(paths: list[Path]) -> list[str]:
    """A file with no 'Formerly §N' clause is not itself a violation --
    .claude/rules/design-decisions.md reserves that clause for content
    migrated from the pre-split file, so a decision recorded after the
    split legitimately carries none. Only the values that ARE recorded
    must form a contiguous range."""
    numbers: list[int] = []
    malformed_provenance: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        number = _provenance_number(text)
        if number is not None:
            numbers.append(number)
        elif _FORMERLY_PREFIX_RE.search(text):
            # A "Formerly `docs/design-decisions.md` §..." clause is present
            # but doesn't match the exact required shape -- a genuine
            # malformed-provenance defect, unlike a file recorded after the
            # split that carries no such clause at all (permitted per
            # .claude/rules/design-decisions.md's "A decision recorded after
            # the split carries no `Formerly §N` clause" convention).
            malformed_provenance.append(path.name)
    violations: list[str] = []
    if malformed_provenance:
        violations.append(
            "Files with a malformed 'Formerly `docs/design-decisions.md` "
            f"§N.' provenance line: {malformed_provenance}"
        )
    if numbers:
        highest = max(numbers)
        if sorted(numbers) != list(range(1, highest + 1)):
            violations.append(
                f"Legacy §N values do not form a contiguous {{1..{highest}}} range "
                f"with no gaps or duplicates: {sorted(numbers)}"
            )
    return violations


def test_legacy_numbers_form_contiguous_range() -> None:
    """Assertion 2: recorded provenance §N values, across every file, form
    exactly {1..N} -- no gaps, duplicates, or fabricated numbers -- where N
    is the highest legacy number any file records. N is read from the files
    themselves rather than hardcoded, since docs/design-decisions.md is now
    a stub carrying no section numbers of its own. A file with no `Formerly
    §N` clause at all is a legitimate post-split decision, not a violation
    -- only a present-but-malformed clause counts as missing provenance."""
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


class TestFaultInjection:
    """Negative-fixture coverage: each test builds a synthetic
    design-decisions/-shaped directory in tmp_path containing exactly the
    fault its paired assertion exists to catch, and asserts the checking
    function itself flags it -- proving the check's own logic, not just
    today's clean corpus, would catch a regression."""

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

    def test_legacy_numbers_detects_gap(self, tmp_path: Path) -> None:
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "second-decision.md").write_text(
            "# Second Decision\n\nFormerly `docs/design-decisions.md` §3.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "contiguous" in violations[0]

    def test_legacy_numbers_allows_new_decision_with_no_provenance_line(self, tmp_path: Path) -> None:
        """A post-split decision carries no `Formerly §N` clause at all --
        .claude/rules/design-decisions.md's documented convention for a
        decision recorded after the split. Not a violation."""
        (tmp_path / "first-decision.md").write_text(
            "# First Decision\n\nFormerly `docs/design-decisions.md` §1.\n",
            encoding="utf-8",
        )
        (tmp_path / "new-decision.md").write_text(
            "# New Decision\n\n*2026-09-08.*\n\nSome rationale.\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(_decision_files(tmp_path))
        assert violations == []

    def test_legacy_numbers_detects_malformed_formerly_clause(self, tmp_path: Path) -> None:
        """A `Formerly` clause present but not matching the exact required
        shape (e.g. a missing trailing period) is a genuine defect, unlike
        a file carrying no clause at all -- must still be flagged."""
        (tmp_path / "malformed-decision.md").write_text(
            "# Malformed Decision\n\nFormerly `docs/design-decisions.md` §1\n",
            encoding="utf-8",
        )
        violations = _legacy_number_range_violations(_decision_files(tmp_path))
        assert len(violations) == 1
        assert "malformed-decision.md" in violations[0]

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
