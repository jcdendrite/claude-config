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
     fabrications.
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

Checking logic for assertions 1 through 5 is factored into standalone
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
    numbers: list[int] = []
    missing_provenance: list[str] = []
    for path in paths:
        number = _provenance_number(path.read_text(encoding="utf-8"))
        if number is None:
            missing_provenance.append(path.name)
        else:
            numbers.append(number)
    violations: list[str] = []
    if missing_provenance:
        violations.append(
            "Files with no 'Formerly `docs/design-decisions.md` §N.' provenance "
            f"line: {missing_provenance}"
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
    a stub carrying no section numbers of its own."""
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
