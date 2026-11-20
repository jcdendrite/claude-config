"""Every § citation in the skill tree is extracted and resolves, and the citation pipeline's own unit tests."""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import pytest

# pyproject.toml's pythonpath also puts claude/.claude/scripts on the import
# path, where the auxiliary-filename constant shared with select-tests.py lives.
from _skill_auxiliary_files import SKILL_AUXILIARY_MD_NAMES
from _skill_md_helpers import (
    _GLOBAL_CLAUDE_MD,
    _all_doc_paths,
    _all_skill_md_files,
    _assert_citation_resolves_to_heading,
    _blank_frontmatter,
    _closed_fence_line_indices,
    _extract_citations,
    _resolve_citation_target,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    CLAUDE_DIR,
    REPO_ROOT,
    heading_texts,
    normalize_heading,
)


def _citation_sources_for_skill_md(skill_md_path: Path) -> list[Path]:
    """A SKILL.md plus its co-located auxiliary siblings (SKILL_AUXILIARY_MD_NAMES), if present."""
    sources = [skill_md_path]
    for sibling_name in SKILL_AUXILIARY_MD_NAMES:
        sibling = skill_md_path.parent / sibling_name
        if sibling.exists():
            sources.append(sibling)
    return sources


def _citation_report(skill_md_paths: Iterable[Path], *, repo_root: Path) -> list[str]:
    """Glob (expand each SKILL.md into itself plus its auxiliary siblings) →
    extract → resolve → aggregate → report, for a given set of SKILL.md
    paths. Factored out from test_skill_citations_resolve_to_real_headings so
    a synthetic-corpus test can drive the exact same pipeline without
    depending on the real repo tree.
    """
    violations: list[str] = []
    for skill_md_path in skill_md_paths:
        for source_path in _citation_sources_for_skill_md(skill_md_path):
            text = source_path.read_text()
            relative_source = source_path.relative_to(repo_root)
            for citation in _extract_citations(text):
                resolved = _resolve_citation_target(
                    citation.target, citing_file=source_path, repo_root=repo_root
                )
                if resolved is None:
                    violations.append(
                        f"{relative_source}:{citation.line} -> "
                        f"unresolvable target {citation.target!r}"
                    )
                    continue
                if normalize_heading(citation.heading) not in heading_texts(resolved.read_text()):
                    violations.append(
                        f"{relative_source}:{citation.line} -> target "
                        f"{citation.target!r} resolved to "
                        f"{resolved.relative_to(repo_root)} but it has no heading "
                        f"matching {citation.heading!r}"
                    )
    return violations


def test_skill_citations_resolve_to_real_headings() -> None:
    """Every `` `target` § "Heading" `` citation in the skill tree resolves to
    a real file and an exact heading in it.

    Scanned corpus: every SKILL.md (`_all_skill_md_files`) plus every
    auxiliary sibling (SKILL_AUXILIARY_MD_NAMES) in those same skill
    directories — widened past SKILL.md alone because a stale citation this
    test guards against lives in review-permissions/REFERENCES.md.

    Reports every violation at once rather than failing on the first, so a
    contributor fixes the whole set in one pass — same convention as
    test_skill_bodies_carry_no_citation_urls in test_skill_url_placement.py.
    """
    repo_root = REPO_ROOT
    violations = _citation_report(_all_skill_md_files(), repo_root=repo_root)

    assert not violations, (
        "Skill-tree citations (`target` § \"Heading\") must resolve to a real "
        "file and an exact heading in it — a rename on either side leaves a "
        "stale pointer with no reader-visible symptom.\n"
        + "\n".join(f"  {violation}" for violation in violations)
    )


# A citation-shaped construct `_extract_citations` misses is a silent miss,
# not an error (both extractor regexes require `\s+` before the quote; a
# no-space opener or a line-wrapped heading yields zero matches instead of a
# wrong one).
# This regex uses `\s*` (looser than the extractor's `\s+`) so a candidate
# can be checked against what the extractor actually returned, rather than
# re-implementing its notion of a citation.
# ASCII straight quote only, per this repo's plain-ASCII convention — widen
# to `["""]` if that changes.
_CITATION_CANDIDATE_RE = re.compile(r'§\s*"')


def _unextracted_citation_candidate_report(paths: Iterable[Path], *, repo_root: Path) -> list[str]:
    """Every `§\\s*"` construct in `paths` that `_extract_citations` did not
    recognize as a citation. Excludes frontmatter and closed fenced code
    blocks, matching `_extract_citations`'s own exclusions. A bare `§4`
    section reference is never a candidate — it isn't followed by a quote —
    so no allowlist is needed for that shape.
    """
    violations: list[str] = []
    for path in paths:
        text = path.read_text()
        prose = _blank_frontmatter(text)
        lines = prose.split("\n")
        fenced_lines = _closed_fence_line_indices(lines)
        citation_spans = [citation.span for citation in _extract_citations(text)]
        for match in _CITATION_CANDIDATE_RE.finditer(prose):
            lineno = prose.count("\n", 0, match.start())
            if lineno in fenced_lines:
                continue
            if any(start <= match.start() < end for start, end in citation_spans):
                continue
            violations.append(f"{path.relative_to(repo_root)}:{lineno + 1}")
    return violations


def test_every_citation_shaped_construct_is_extracted() -> None:
    """Asserts every `` `target` § "Heading" `` (or bare `§ "Heading"`)
    construct in the skill/doc corpus was recognized by `_extract_citations`.

    Catches a hard-wrapped or no-space citation that
    `_CITATION_WITH_TARGET_RE`/`_BARE_CITATION_RE` silently miss.

    Complements test_skill_citations_resolve_to_real_headings and
    test_tooling_measurement_citation_resolves_to_real_heading, which
    never see such a construct since it was never extracted as a citation in
    the first place.

    Scanned corpus:

    - `_citation_sources_for_skill_md`'s expansion of every
      `_all_skill_md_files()` entry.
    - Every path `_all_citation_extraction_doc_paths()` covers:
      `_all_doc_paths()`'s corpus, plus both CLAUDE.md files, plus every
      `.claude/rules/*.md`/`claude/.claude/rules/*.md` file.

    This checks extraction only — the `docs/rules-references.md`
    external-URL citation is intentionally never asserted to resolve here.
    """
    repo_root = REPO_ROOT
    skill_md_sources: list[Path] = []
    for skill_md_path in _all_skill_md_files():
        skill_md_sources.extend(_citation_sources_for_skill_md(skill_md_path))

    violations = _unextracted_citation_candidate_report(
        skill_md_sources + _all_citation_extraction_doc_paths(), repo_root=repo_root
    )
    assert not violations, (
        "Citation-shaped construct not recognized by "
        "`_CITATION_WITH_TARGET_RE`/`_BARE_CITATION_RE` — check for a missing "
        "space before the opening quote or a heading hard-wrapped across a "
        "line break:\n" + "\n".join(f"  {violation}" for violation in violations)
    )


@pytest.mark.parametrize(
    ("source_text", "expected_violation_count"),
    [
        pytest.param(
            '## Real Heading\n\nCitation: `sibling.md` §"No Space Heading"\n',
            1,
            id="no-space-opener-goes-unextracted",
        ),
        pytest.param(
            'Wrapped: `sibling.md` § "Hard\nWrapped Heading"\n',
            1,
            # The heading class `[^"\n]+` can't span the newline, so this
            # candidate matches neither `_CITATION_WITH_TARGET_RE` nor
            # `_BARE_CITATION_RE` — a hard-wrap is a distinct failure shape
            # from the no-space case above, caught by the same guard.
            id="hard-wrapped-heading-goes-unextracted",
        ),
        pytest.param(
            '## Real Heading\n\nCitation: `sibling.md` § "Well Formed Heading"\n',
            0,
            id="well-formed-citation-is-extracted",
        ),
        pytest.param(
            "See §4 for the full rule text, not a citation.\n",
            0,
            id="bare-section-number-is-not-a-candidate",
        ),
        pytest.param(
            '## Real Heading\n\n```\nFenced: `sibling.md` §"No Space Heading"\n```\n',
            0,
            id="candidate-inside-closed-fence-not-scanned",
        ),
    ],
)
def test_unextracted_citation_candidate_report_cases(
    tmp_path: Path, source_text: str, expected_violation_count: int
) -> None:
    """`_unextracted_citation_candidate_report` flags a `§ "` construct only
    when `_extract_citations` did not return a matching citation for it —
    not merely when the construct spans a line break."""
    doc_path = tmp_path / "example.md"
    doc_path.write_text(source_text)
    violations = _unextracted_citation_candidate_report([doc_path], repo_root=tmp_path)
    assert len(violations) == expected_violation_count, violations


def test_tooling_measurement_code_review_skill_citation_is_still_present() -> None:
    """Pins presence of this specific citation: no other test asserts
    that this SKILL.md still cites this heading at all, only that any
    citation it does carry resolves correctly. A re-wrap that breaks
    extraction is also caught by
    test_every_citation_shaped_construct_is_extracted; this test's own
    unique coverage is the citation being deleted or paraphrased away
    with no `§ "..."` construct left for either test to see.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / ".claude/skills/code-review-claude-config/SKILL.md",
        "docs/private-project-redaction.md",
        "Publishing a tooling measurement",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    ("raw_heading", "normalized"),
    [
        pytest.param("## Plain Heading", "Plain Heading", id="strips-leading-hashes"),
        pytest.param(
            "### Debug-investigation probe → `general-purpose` or `Explore`",
            "Debug-investigation probe → general-purpose or Explore",
            id="strips-backticks-mid-heading",
        ),
        pytest.param("## *Emphasized* heading", "Emphasized heading", id="strips-asterisk-emphasis"),
        pytest.param("## _Emphasized_ heading", "Emphasized heading", id="strips-underscore-emphasis"),
        pytest.param("##   Extra   spaces  ", "Extra spaces", id="collapses-whitespace-runs"),
    ],
)
def test_normalize_heading(raw_heading: str, normalized: str) -> None:
    """The real heading "Debug-investigation probe → `general-purpose` or
    `Explore`" in subagent-delegation/SKILL.md pins the mid-heading-backtick
    case; the rest are synthetic but exercise the same normalization
    independently."""
    assert normalize_heading(raw_heading) == normalized


def test_heading_texts_excludes_headings_inside_a_fenced_code_block() -> None:
    """A stale citation could otherwise coincidentally string-match a
    fenced shell comment or sample-output line and pass despite citing
    nothing real -- the exact regression this helper's fence tracking
    exists to prevent, in _citation_report's own resolution check."""
    markdown = (
        "# Real heading\n"
        "\n"
        "```bash\n"
        "# Not a heading, just a fenced comment\n"
        "```\n"
        "\n"
        "## Another real heading\n"
    )
    assert heading_texts(markdown) == {"Real heading", "Another real heading"}


def _write_skill_files(tmp_path: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        file_path = tmp_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)


@pytest.mark.parametrize(
    ("skill_files", "expected_violation_count"),
    [
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Real Heading\n\n"
                    "```\n"
                    'Fenced: `example-skill/SKILL.md` § "Nonexistent Heading"\n'
                    "```\n"
                ),
            },
            0,
            # Would be 1 if the closed-fence exclusion broke — the fenced
            # citation's heading does not exist.
            id="citation-inside-closed-fence-not-scanned",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Real Heading\n\n"
                    'Wrapped: `example-skill/SKILL.md`\n§ "Nonexistent Heading"\n'
                ),
            },
            1,
            # Would be 0 if \s+ didn't span the newline — the bad citation
            # would never be extracted at all.
            id="newline-wrapped-citation-is-matched",
        ),
        pytest.param(
            {"example-skill/SKILL.md": "## Real Heading\n\n" 'Bad: `does-not-exist.md` § "Whatever"\n'},
            1,
            id="unresolvable-target-fails",
        ),
        pytest.param(
            {"example-skill/SKILL.md": "## Real Heading\n\n" 'Bare: § "Real Heading"\n'},
            0,
            id="bare-citation-with-no-adjacent-target-resolves-same-file",
        ),
        pytest.param(
            {"example-skill/SKILL.md": "## Real Heading\n\n" 'Bare: § "Nonexistent Heading"\n'},
            1,
            id="bare-citation-same-file-missing-heading-fails",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Home\n\n" 'Citation: `sibling.md` § "Config: "quoted" value"\n'
                ),
                "example-skill/sibling.md": '## Config: "quoted" value\n',
            },
            # The `[^"\n]+` heading class truncates at the first embedded
            # quote, so the citation must fail loudly rather than
            # prefix-match the real (longer) heading.
            1,
            id="heading-with-literal-quote-truncates-and-fails",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "---\nname: example-skill\ndescription: |\n"
                    '  See `sibling.md` § "Nonexistent Heading" for details.\n'
                    "---\n\n## Real Heading\n"
                ),
            },
            # Would be 1 if frontmatter weren't blanked before extraction —
            # pins _blank_frontmatter's exclusion for this extractor.
            0,
            id="citation-inside-frontmatter-is-excluded",
        ),
        pytest.param(
            {
                "example-skill/SKILL.md": (
                    "## Real Heading\n\n" 'Citation: `sibling.md` § "Duplicate Heading"\n'
                ),
                "example-skill/sibling.md": "## Duplicate Heading\n\nFirst copy.\n\n## Duplicate Heading\n",
            },
            # Would be 1 if heading_texts started requiring headings to be
            # unique — today it returns a set, so two identical normalized
            # headings in the target file still resolve.
            0,
            id="duplicate-headings-in-target-still-resolve",
        ),
    ],
)
def test_citation_report_cases(
    tmp_path: Path, skill_files: dict[str, str], expected_violation_count: int
) -> None:
    _write_skill_files(tmp_path, skill_files)
    violations = _citation_report([tmp_path / "example-skill" / "SKILL.md"], repo_root=tmp_path)
    assert len(violations) == expected_violation_count, violations


@pytest.mark.parametrize("sibling_name", SKILL_AUXILIARY_MD_NAMES)
def test_citation_report_scans_every_auxiliary_sibling(tmp_path: Path, sibling_name: str) -> None:
    """Every name in SKILL_AUXILIARY_MD_NAMES must actually be walked: a name
    listed in the tuple but skipped by _citation_sources_for_skill_md would
    report 0 violations here, whichever name it is."""
    _write_skill_files(
        tmp_path,
        {
            "example-skill/SKILL.md": "## Real Heading\n",
            f"example-skill/{sibling_name}": "## Sibling\n\n" 'Bad: `does-not-exist.md` § "Whatever"\n',
        },
    )
    violations = _citation_report([tmp_path / "example-skill" / "SKILL.md"], repo_root=tmp_path)
    assert len(violations) == 1, violations


def test_citation_report_target_resolving_to_a_directory_fails(tmp_path: Path) -> None:
    """A citation target that resolves to a directory, not a file, must fail
    to resolve rather than raise when the resolver reads it for headings."""
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "not-a-file").mkdir()
    (skill_dir / "SKILL.md").write_text("## Home\n\n" 'Citation: `not-a-file` § "Whatever"\n')

    violations = _citation_report([skill_dir / "SKILL.md"], repo_root=tmp_path)

    assert len(violations) == 1
    assert "not-a-file" in violations[0]


def test_resolve_citation_target_cross_skill_directory_succeeds(tmp_path: Path) -> None:
    """`skill-name/file` resolves via the injected `skill_md_files` seam when
    a same-named skill directory holds the file, without touching the real
    repo tree — this is what `_all_skill_md_files()` would do in production.

    The real skill directory lives outside `repo_root` so the repo-relative
    tier (1) cannot resolve it first — only the cross-skill-directory tier
    (2), driven by the injected seam, can.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other_skill_md = tmp_path / "elsewhere" / "other-skill" / "SKILL.md"
    other_skill_md.parent.mkdir(parents=True)
    other_skill_md.write_text("## Home\n")
    (other_skill_md.parent / "REFERENCES.md").write_text("## Target Heading\n")

    resolved = _resolve_citation_target(
        "other-skill/REFERENCES.md",
        citing_file=repo_root / "example-skill" / "SKILL.md",
        repo_root=repo_root,
        skill_md_files=lambda: [other_skill_md],
    )

    assert resolved == other_skill_md.parent / "REFERENCES.md"


def test_resolve_citation_target_cross_skill_directory_fails_on_unknown_skill(
    tmp_path: Path,
) -> None:
    """A `skill-name/file` target whose skill name matches no directory
    returned by the injected `skill_md_files` seam is unresolvable."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other_skill_md = tmp_path / "elsewhere" / "other-skill" / "SKILL.md"
    other_skill_md.parent.mkdir(parents=True)
    other_skill_md.write_text("## Home\n")

    resolved = _resolve_citation_target(
        "nonexistent-skill/REFERENCES.md",
        citing_file=repo_root / "example-skill" / "SKILL.md",
        repo_root=repo_root,
        skill_md_files=lambda: [other_skill_md],
    )

    assert resolved is None


def test_citation_report_names_citing_file_line_and_target_for_unresolvable_citation(
    tmp_path: Path,
) -> None:
    """Drives the full glob → extract → resolve → aggregate → report path
    over a small synthetic corpus with one resolvable and one unresolvable
    citation. A later refactor that narrows the glob, swallows an exception,
    or drops an aggregation branch would pass every helper-level test above
    and only surface here.
    """
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "## Real Heading\n\n"
        'Resolvable: `example-skill/SKILL.md` § "Real Heading".\n'
        'Unresolvable: `missing-file.md` § "Some Heading".\n'
    )

    violations = _citation_report([skill_dir / "SKILL.md"], repo_root=tmp_path)

    assert len(violations) == 1
    assert violations[0].startswith("example-skill/SKILL.md:4")
    assert "missing-file.md" in violations[0]


def _all_citation_extraction_doc_paths() -> list[Path]:
    """`_all_doc_paths()`'s corpus, plus the always-loaded instruction files
    and lazy-loaded rule files where a citation-shaped construct could also
    hide unextracted: the repo-root CLAUDE.md, `_GLOBAL_CLAUDE_MD`
    (`claude/.claude/CLAUDE.md`), every `.claude/rules/*.md` file, and every
    `claude/.claude/rules/*.md` file. Kept separate from `_all_doc_paths()`
    so this wider corpus doesn't also widen TestPerAccountStatePathContract's
    scope, which parametrizes directly off that helper."""
    paths = list(_all_doc_paths())
    repo_root = REPO_ROOT
    root_claude_md = repo_root / "CLAUDE.md"
    if root_claude_md.exists():
        paths.append(root_claude_md)
    if _GLOBAL_CLAUDE_MD.exists():
        paths.append(_GLOBAL_CLAUDE_MD)
    project_rules_dir = repo_root / ".claude" / "rules"
    project_rules = sorted(project_rules_dir.glob("*.md"))
    assert project_rules, f"{project_rules_dir}/*.md matched no *.md files — this glob root is wrong"
    paths += project_rules

    global_rules_dir = CLAUDE_DIR / "rules"
    global_rules = sorted(global_rules_dir.glob("*.md"))
    assert global_rules, f"{global_rules_dir}/*.md matched no *.md files — this glob root is wrong"
    paths += global_rules

    return paths


def test_all_citation_extraction_doc_paths_raises_when_project_rules_dir_empty(tmp_path, monkeypatch):
    """Guards the `assert project_rules, ...` non-emptiness check itself — a
    typo checking an always-truthy variable (e.g. `project_rules_dir`
    instead of the glob result `project_rules`) would otherwise never fire
    under test."""
    import sys

    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="matched no"):
        _all_citation_extraction_doc_paths()


def test_all_citation_extraction_doc_paths_raises_when_global_rules_dir_empty(tmp_path, monkeypatch):
    """Same guard as above, for the sibling `assert global_rules, ...` check
    over `CLAUDE_DIR / "rules"`."""
    import sys

    monkeypatch.setattr(sys.modules[__name__], "CLAUDE_DIR", tmp_path)
    with pytest.raises(AssertionError, match="matched no"):
        _all_citation_extraction_doc_paths()
