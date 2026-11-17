"""Helpers shared across the claude-skills/skills/tests test files.

Keep this basename unique across every unpackaged test tree and pythonpath entry.
The select-tests completeness scan parses only test_*.py, so a path constant
added here needs its own selection test.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    CLAUDE_DIR,
    REPO_ROOT,
    SKILLS_DIR,
    change_type_table_rows,
    heading_section_bounds,
    heading_texts,
    normalize_heading,
)

# Plugins live at the repo root, alongside claude/ and claude-skills/: <repo>/plugins/<name>/skills/<skill>/SKILL.md
_PLUGINS_DIR = REPO_ROOT / "plugins"
# The stowed global instruction file, installed to ~/.claude/CLAUDE.md.
_GLOBAL_CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"


_AGENTS_DIR = CLAUDE_DIR / "agents"


def _agent_body(agent_name: str) -> str:
    """Read a claude/.claude/agents/<name>.md body by name."""
    return (_AGENTS_DIR / f"{agent_name}.md").read_text()


def _skill_body(skill_name: str) -> str:
    """Read a stowed skill's SKILL.md body by name."""
    return (SKILLS_DIR / skill_name / "SKILL.md").read_text()


def _skill_file(skill_name: str) -> Path:
    """Locate a SKILL.md by name — stowed skills first, then plugin skills."""
    candidate = SKILLS_DIR / skill_name / "SKILL.md"
    if candidate.exists():
        return candidate
    if _PLUGINS_DIR.exists():
        for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
            plugin_candidate = plugin_dir / "skills" / skill_name / "SKILL.md"
            if plugin_candidate.exists():
                return plugin_candidate
    return candidate  # let read_text() surface the FileNotFoundError


def _all_skill_md_paths() -> list[Path]:
    """Every SKILL.md under the stowed skills dir and plugin skill dirs."""
    paths = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if _PLUGINS_DIR.exists():
        paths += sorted(_PLUGINS_DIR.glob("*/skills/*/SKILL.md"))
    return paths


_TRIGGER_A_EXCLUSION_MARKER_RE = re.compile(r"<!-- (HOOK_TEST_FIXTURE|HOOK_SCRIPT_CONTENT_EXAMPLE):")


def _fenced_code_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Every *closed* fenced code block as (open_index, content_lines).

    Mirrors _closed_fence_line_indices's own closing-fence detection (a bare
    run of the opener's character, at least as long as the opener, with no
    trailing info string) but returns each block's own content instead of a
    flat index set — the Trigger-A scan needs to look inside each block for
    the assign-then-reference shape, not just which lines are code.
    """
    blocks: list[tuple[int, list[str]]] = []
    open_index: int | None = None
    open_run = ""
    for index, line in enumerate(lines):
        if open_index is None:
            fence_match = _FENCE_OPEN_RE.match(line)
            if fence_match:
                open_index, open_run = index, fence_match.group(1)
            continue
        candidate = line.strip()
        if candidate and set(candidate) == {open_run[0]} and len(candidate) >= len(open_run):
            blocks.append((open_index, lines[open_index + 1 : index]))
            open_index = None
    return blocks


def _fence_excluded_by_marker(lines: list[str], open_index: int) -> bool:
    """True when the nearest non-blank line before the fence opener is a
    HOOK_TEST_FIXTURE/HOOK_SCRIPT_CONTENT_EXAMPLE marker comment — an
    explicit, structural opt-in a pytest-executed fixture or a documentation
    example (never typed into an agent's Bash tool) uses to exempt itself."""
    for index in range(open_index - 1, -1, -1):
        stripped = lines[index].strip()
        if stripped == "":
            continue
        return bool(_TRIGGER_A_EXCLUSION_MARKER_RE.search(stripped))
    return False


# An opening fence: up to 3 leading spaces, then 3+ backticks or 3+ tildes.
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# An inline code span: a run of N backticks, content, then a matching run of N.
# The lookarounds pin both delimiters to *whole* backtick runs. Without them the
# opener backtracks to a shorter run when no equal-length closer exists — so
# ````text``` would match as a 3-tick span, and any URL between the delimiters
# would be blanked even though CommonMark leaves the whole thing as literal text.
# That direction of error is the dangerous one: it exempts a real citation from
# the scan. `.` excludes newlines, so a span stays on one line.
_INLINE_CODE_RE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`)(?P<body>.+?)(?<!`)(?P=ticks)(?!`)")


def _blank_frontmatter(markdown_text: str) -> str:
    """Blank the leading YAML frontmatter block, preserving the line count.

    No current `description` holds a URL, but frontmatter is metadata rather than
    body prose, so excluding it makes the invariant unambiguous rather than
    accidentally true.

    Deliberately does not reuse validate_skill_structure.parse_frontmatter, which
    sibling test modules import: that function answers "what are the frontmatter
    values" and returns a parsed dict, while this one answers "which lines are
    frontmatter" and must return text with the line count intact so violation
    line numbers stay accurate. Same delimiter, different questions.
    """
    lines = markdown_text.split("\n")
    if not lines or lines[0].strip() != "---":
        return markdown_text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join([""] * (index + 1) + lines[index + 1 :])
    return markdown_text


def _closed_fence_line_indices(lines: list[str]) -> set[int]:
    """Indices of every line belonging to a *closed* fenced code block.

    An unterminated fence contributes nothing. CommonMark says an unclosed fence
    runs to the end of the document, and this scan deliberately diverges: under
    that rule a contributor who forgets one closing fence would silently exempt
    every line after it — including real citation URLs — with no visible symptom.
    Treating the unterminated remainder as prose errs toward over-flagging, which
    surfaces as a test failure pointing at the malformed fence. Every gap in this
    scan is deliberately biased that direction.
    """
    inside: set[int] = set()
    open_index: int | None = None
    open_run = ""

    for index, line in enumerate(lines):
        if open_index is None:
            fence_match = _FENCE_OPEN_RE.match(line)
            if fence_match:
                open_index, open_run = index, fence_match.group(1)
            continue
        # A closing fence is a bare run of the opener's character, at least as
        # long as the opener, with no trailing info string.
        candidate = line.strip()
        if candidate and set(candidate) == {open_run[0]} and len(candidate) >= len(open_run):
            inside.update(range(open_index, index + 1))
            open_index = None

    return inside


def _blank_code_regions(markdown_text: str) -> str:
    """Blank fenced code blocks and inline code spans, preserving the line count.

    Fences are resolved before inline spans so a ``` delimiter is never misread as
    three consecutive single-backtick span openers. Blanked regions become empty
    lines rather than being deleted, so a violation's reported line number still
    matches the real file.
    """
    lines = markdown_text.split("\n")
    fenced = _closed_fence_line_indices(lines)
    return "\n".join(
        "" if index in fenced else _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)
        for index, line in enumerate(lines)
    )


def _all_skill_md_files() -> list[Path]:
    """Every SKILL.md in the repo, across all three roots that hold one.

    Single-level globs only — never rglob or `**`. `.claude/worktrees/` holds
    full checkouts of this repo whose SKILL.md files carry live pre-fix URLs, and
    a recursive glob from the repo root would scan them and fail on another
    branch's content.
    """
    repo_root = REPO_ROOT
    found: list[Path] = []
    for base, pattern in [
        (repo_root / "claude-skills" / "skills", "*/SKILL.md"),
        (repo_root / ".claude" / "skills", "*/SKILL.md"),
        (repo_root / "plugins", "*/skills/*/SKILL.md"),
    ]:
        matched = list(base.glob(pattern))
        # Per-root, not just in aggregate: Path.glob on a missing directory
        # returns empty rather than raising, so a rename that relocates one root
        # would otherwise shrink the scanned corpus silently while the other two
        # roots kept the suite green.
        assert matched, f"{base}/{pattern} matched no SKILL.md — this glob root is wrong"
        found.extend(matched)
    return sorted(found)


# --- Cross-reference integrity: `target` § "Heading" citations resolve ---
#
# A citation like `subagent-delegation/SKILL.md` § "Heavy command output" lets
# one skill point at a section of another (or of a REFERENCES.md/doc) instead
# of restating it — CLAUDE.md's single-source-of-truth rule applied to skill
# prose. Nothing else in the repo checks that the target file still exists or
# that its heading still reads exactly as quoted, so a rename on either side
# leaves a stale pointer with no reader-visible symptom until someone follows
# it. test_skill_citations_resolve_to_real_headings resolves every such citation
# in the skill tree against a real file and a real heading in it.

# The backticked target must be adjacent to `§`, with only whitespace (which
# may include a single newline, so hard-wrapped prose still matches) between
# the closing backtick and the section mark, and between the mark and the
# opening quote.
_CITATION_WITH_TARGET_RE = re.compile(r'`(?P<target>[^`\n]+)`\s+§\s+"(?P<heading>[^"\n]+)"')
# A bare `§ "Heading"` with no adjacent backticked target resolves against the
# citing file's own headings. Every with-target match above also satisfies
# this pattern (the `§ "..."` tail is a substring of it), so callers must
# discard a bare match whose span falls inside a with-target match's span —
# see _extract_citations.
_BARE_CITATION_RE = re.compile(r'§\s+"(?P<heading>[^"\n]+)"')

# Known limit, deliberately not closed: this extractor can't tell a live
# citation from an illustrative example of the citation grammar, so an
# example inside a scanned SKILL.md or SKILL_AUXILIARY_MD_NAMES sibling will
# false-fail. Currently safe only because the grammar's own explanation lives
# outside the scanned corpus (`.claude/rules/skill-and-agent-self-review.md`).


class _Citation(NamedTuple):
    line: int
    target: str | None  # None means "resolve against the citing file itself"
    heading: str
    span: tuple[int, int]  # match.span() in the (frontmatter-blanked) prose


def _extract_citations(markdown_text: str) -> list[_Citation]:
    """Every `` `target` § "Heading" `` (or bare `§ "Heading"`) citation in a
    markdown document.

    Excludes frontmatter (`_blank_frontmatter`) and any citation whose match
    starts on a line inside a *closed* fenced code block
    (`_closed_fence_line_indices`) — deliberately not `_blank_code_regions`,
    which blanks inline code spans and would blank the citation's own target.
    """
    prose = _blank_frontmatter(markdown_text)
    lines = prose.split("\n")
    fenced_lines = _closed_fence_line_indices(lines)

    def _lineno(start: int) -> int:
        return prose.count("\n", 0, start) + 1

    citations: list[_Citation] = []
    consumed_spans: list[tuple[int, int]] = []

    for match in _CITATION_WITH_TARGET_RE.finditer(prose):
        consumed_spans.append(match.span())
        lineno = _lineno(match.start())
        if (lineno - 1) in fenced_lines:
            continue
        citations.append(
            _Citation(lineno, match.group("target"), match.group("heading"), match.span())
        )

    for match in _BARE_CITATION_RE.finditer(prose):
        if any(start <= match.start() < end for start, end in consumed_spans):
            continue
        lineno = _lineno(match.start())
        if (lineno - 1) in fenced_lines:
            continue
        citations.append(_Citation(lineno, None, match.group("heading"), match.span()))

    return citations


def _resolve_citation_target(
    target: str | None,
    *,
    citing_file: Path,
    repo_root: Path,
    skill_md_files: Callable[[], list[Path]] = _all_skill_md_files,
) -> Path | None:
    """Resolve a citation's target to a real file, or None if unresolvable.

    Resolution order: (1) a repo-root-relative path that exists; (2)
    `<skill-dir>/<file>` under any of `skill_md_files()`'s three roots; (3) a
    bare filename with no `/`, as a same-directory sibling of the citing
    file. `target is None` (a bare `§ "..."` citation) always resolves to
    the citing file itself. A candidate that exists but is a directory does
    not count as resolved — a directory has no headings to satisfy the
    citation.

    `skill_md_files` defaults to the real, repo-wide `_all_skill_md_files`
    but is overridable so a `tmp_path`-rooted test can exercise the
    cross-skill-directory branch without touching the real repo tree.
    """
    if target is None:
        return citing_file

    root_relative = repo_root / target
    if root_relative.is_file():
        return root_relative

    if "/" in target:
        skill_name, filename = target.split("/", 1)
        for skill_md_path in skill_md_files():
            if skill_md_path.parent.name == skill_name:
                candidate = skill_md_path.parent / filename
                if candidate.is_file():
                    return candidate
    else:
        same_dir_candidate = citing_file.parent / target
        if same_dir_candidate.is_file():
            return same_dir_candidate

    return None


def _assert_citation_resolves_to_heading(
    doc_path: Path,
    expected_target: str,
    expected_heading_raw: str,
    *,
    repo_root: Path,
) -> None:
    """Shared body for the doc → SKILL.md-heading citation tests that import it:
    find the citation, resolve its target file, and confirm the target
    heading actually exists there."""
    expected_heading = normalize_heading(expected_heading_raw)
    citations = [
        citation
        for citation in _extract_citations(doc_path.read_text())
        if citation.target == expected_target
        and normalize_heading(citation.heading) == expected_heading
    ]
    assert citations, (
        f"{doc_path} no longer cites {expected_target}'s "
        f'"{expected_heading_raw}" section'
    )

    resolved = _resolve_citation_target(
        citations[0].target, citing_file=doc_path, repo_root=repo_root
    )
    assert resolved is not None, (
        f"{doc_path}'s citation target {expected_target!r} failed to resolve"
    )
    assert expected_heading in heading_texts(resolved.read_text()), (
        f"{doc_path}'s citation resolved to {resolved} but it has no "
        f"heading matching {expected_heading!r}"
    )


def _extract_scope_anchor_region(skill_md_path: Path, marker_name: str) -> str:
    """Extract text strictly between a `<!-- <marker_name> start/end -->` pair.

    Mirrors extract_governing_rule()'s (evals/run_skill_evals.py) presence/
    order/duplicate validation, parameterized over the marker name instead of
    that function's hard-coded `DISPOSITION_RULE:` prefix — reusing that
    prefix would silently enroll a scope rule in the disposition-fidelity
    eval, which is why this anchor namespace needs its own small parser
    rather than calling extract_governing_rule() directly.
    """
    text = skill_md_path.read_text()
    start_marker = f"<!-- {marker_name} start -->"
    end_marker = f"<!-- {marker_name} end -->"
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count > 1 or end_count > 1:
        raise ValueError(
            f"{marker_name!r} anchor in {skill_md_path}: appears more than once "
            f"(start x{start_count}, end x{end_count}) — duplicate anchor names "
            "are not supported, rename one of them"
        )
    start_idx = text.find(start_marker)
    end_idx = text.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        raise ValueError(
            f"{marker_name!r} anchor not found (or incomplete) in {skill_md_path} "
            f"— start present: {start_idx != -1}, end present: {end_idx != -1}"
        )
    if end_idx < start_idx:
        raise ValueError(
            f"{marker_name!r} anchor in {skill_md_path}: end marker appears before start marker"
        )
    return text[start_idx + len(start_marker) : end_idx].strip()


def _normalized_anchor_text(skill_md_path: Path, marker_name: str) -> str:
    """Whitespace-collapsed text of a SCOPE_RULE/SCOPE_EXEMPT_ROW anchor region.

    Collapsing internal whitespace on both sides of a comparison keeps a
    markdown reflow of the enclosed paragraph from false-failing an
    exact-text pin against it.
    """
    return " ".join(_extract_scope_anchor_region(skill_md_path, marker_name).split())


def _change_type_table_left_columns(skill_md_path: Path) -> list[str]:
    """Left-column shorthand of every data row in the Change-type table.

    Truncates a left-column cell at its first embedded `|`. Not exercised
    by any current Change-type row. A future row using pipe-containing
    inline-code shorthand would truncate here. At the current call sites
    (`test_scope_exempt_row_resolves_to_real_change_type_row`,
    `test_scope_exempt_row_excludes_security_controls_row`), a resulting
    mismatch surfaces as a loud assertion failure, not silently.
    """
    return [row.split("|")[1].strip() for row in change_type_table_rows(skill_md_path)]


def _scope_exempt_change_type_row_text(skill_md_path: Path) -> str:
    """Full row-line text of the Change-type row SCOPE_EXEMPT_ROW's shorthand
    resolves to.

    Shared row-lookup for the tests that check the comment/prose row for a
    literal string. They need the row's full text, not just the left-column
    shorthand `_change_type_table_left_columns` returns.
    """
    exempt_shorthand = _extract_scope_anchor_region(skill_md_path, "SCOPE_EXEMPT_ROW")
    for line in change_type_table_rows(skill_md_path):
        # line.split("|")[1] truncates at an embedded pipe in the left cell,
        # same caveat as _change_type_table_left_columns's identical parse.
        if line.split("|")[1].strip() == exempt_shorthand:
            return line
    raise AssertionError(
        f"{skill_md_path}: no Change-type row's left column matches "
        f"SCOPE_EXEMPT_ROW's shorthand {exempt_shorthand!r}"
    )


_STEP4_HEADING = "## Step 4 — Design-fitness gate"


def _section_between(
    lines: list[str], start_heading: str, path: Path
) -> tuple[int, int]:
    """Return (start_idx, end_idx) bounding the section under `start_heading`.

    end_idx is exclusive, at the next line starting with '## ' (or len(lines)
    if the section runs to EOF — unlike test_reconciliation_block_consistency.py's
    extractor, EOF is not itself a failure here, so a section that is last in
    its file is bounded correctly). The start heading is asserted found, not
    inferred — a renamed or deleted heading would otherwise extract as empty
    and compare equal to another empty extraction.
    """
    bounds = heading_section_bounds(lines, start_heading)
    assert bounds is not None, f"{path}: {start_heading!r} heading not found."
    return bounds


def _heading_section_text(skill_md_path: Path, heading: str) -> str:
    """Whitespace-collapsed text of the markdown section under `heading`.

    Mirrors _normalized_anchor_text's whitespace-collapse comparison but
    bounds the region by markdown heading (`_section_between`) rather than an
    HTML-comment anchor pair — handoff/SKILL.md's warrant-check section
    carries no such anchor today.
    """
    lines = skill_md_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, heading, skill_md_path)
    return " ".join("".join(lines[start_idx:end_idx]).split())


def _raw_heading_section_text(skill_md_path: Path, heading: str) -> str:
    """Whitespace-preserving companion to _heading_section_text.

    Used where the section's structure — blank lines, bullet markers,
    heading markers — must survive for a right-edge boundary check; the
    collapsed form loses that structure.
    """
    lines = skill_md_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, heading, skill_md_path)
    return "".join(lines[start_idx:end_idx])


def _assert_pinned_clause_right_bounded(
    pinned_text: str, raw_section_text: str, *, context: str
) -> None:
    """Assert `pinned_text`'s right edge in `raw_section_text` lands on a
    structural boundary (section end, blank line, or a `-` bullet or `#`
    heading marker on the next line) rather than mid-sentence, so a
    contradicting clause appended right after the pinned text is caught
    instead of silently matching as a substring.
    """
    pattern = r"\s+".join(re.escape(word) for word in pinned_text.split())
    match = re.search(pattern, raw_section_text)
    assert match is not None, (
        f"{context}\n  pinned:  {pinned_text!r}\n  section: {raw_section_text!r}"
    )
    remainder = raw_section_text[match.end() :]
    same_line_rest = remainder.lstrip(" \t")
    boundary_ok = (
        remainder.strip() == ""
        or same_line_rest.startswith("\n\n")
        or (same_line_rest.startswith("\n") and same_line_rest[1:2] in ("-", "#"))
    )
    assert boundary_ok, (
        f"{context}: pinned clause is followed by unbounded trailing "
        f"content instead of a structural boundary.\n"
        f"  trailing: {remainder[:120]!r}"
    )


_CODE_REVIEW_CONTRADICTION_ROUTE_ANCHOR = "DISPOSITION_RULE:code-review-contradiction-route"


_CODE_REVIEW_DEFER_INVARIANT_ANCHOR = "DISPOSITION_RULE:code-review-defer-invariant"


_CODE_REVIEW_FINDING_DISPOSITION_HEADING = "## Finding disposition"
_CODE_REVIEW_NEW_PRIMITIVE_ROUTE_ANCHOR = "DISPOSITION_RULE:code-review-new-primitive-route"


_CODE_REVIEW_DEFER_CRITERIA_HEADING = "**DEFER criteria (closed list).**"
_CODE_REVIEW_INVALID_DEFER_HEADING = "**Invalid DEFER rationales.**"
_CODE_REVIEW_HEAVIER_MECHANISM_RULE_OPENING = (
    "**A fix that would introduce a mechanism heavier, more privileged, or "
    "wider-scope than the surface already under review is a design question, "
    "not a fix.**"
)


_CODE_REVIEW_RIPPLE_HEADING = "## Ripple effect triage"


_CODE_REVIEW_STEP_0_1_HEADING = "## Step 0.1 — Short-circuit already-reviewed diff"


def _all_doc_paths() -> list[Path]:
    """Every prose doc file the state-path contract covers: docs/**/*.md,
    every co-located skill REFERENCES.md, evals/README.md, and the
    repo-root README/CONTRIBUTING/SECURITY files. docs/reports/** and
    docs/case-studies/** hold preserved historical records (CLAUDE.md
    Axis 3) and are excluded."""
    repo_root = REPO_ROOT
    docs_root = repo_root / "docs"
    paths = [
        path
        for path in sorted(docs_root.glob("**/*.md"))
        if "reports" not in path.relative_to(docs_root).parts
        and "case-studies" not in path.relative_to(docs_root).parts
    ]
    paths += sorted(SKILLS_DIR.glob("**/REFERENCES.md"))
    if _PLUGINS_DIR.exists():
        paths += sorted(_PLUGINS_DIR.glob("*/skills/**/REFERENCES.md"))
    for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md"):
        candidate = repo_root / name
        if candidate.exists():
            paths.append(candidate)
    evals_readme = repo_root / "evals" / "README.md"
    if evals_readme.exists():
        paths.append(evals_readme)
    return paths


_PLAN_ARCHITECT_PATH = _AGENTS_DIR / "plan-architect.md"
_PLAN_ARCHITECT_CONSULT_HEADING = "## MODE=consult"
