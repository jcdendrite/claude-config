"""Review skills' skip rationales, SCOPE_RULE and CACHE_RULE anchors, Change-type table and reviewer-input rules."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from _skill_md_helpers import (
    _all_skill_md_paths,
    _change_type_table_left_columns,
    _extract_scope_anchor_region,
    _heading_section_text,
    _normalized_anchor_text,
    _scope_exempt_change_type_row_text,
    _section_between,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import SCRIPTS_DIR

_INVALID_SKIP_HEADING = "**Invalid skip rationales.**"
_SKIP_RATIONALE_LABEL_RE = re.compile(r'^[-*] \*\*"(?P<label>[^"]+)"\*\*')
_TOP_LEVEL_LIST_ITEM_RE = re.compile(r"^[-*] ")


def _invalid_skip_rationale_labels(path: Path) -> set[str]:
    """Bolded-and-quoted labels under a file's "Invalid skip rationales" list.

    Scoped to that one list rather than the whole file: code-review/SKILL.md
    carries further bolded-quoted labels in its DEFER-criteria section, which an
    unscoped scan would silently fold in and make the comparison meaningless.

    The list is delimited by indentation, not by "first line that isn't a
    bullet". Blank lines and indented continuations or sub-bullets stay inside
    the list; only an unindented non-list line closes it. Ending the scan at the
    first indented line instead would truncate both files at the same point and
    let genuinely divergent labels below it compare equal — a silent pass on the
    exact drift this guards against.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_index = next(
        (i for i, line in enumerate(lines) if line.startswith(_INVALID_SKIP_HEADING)),
        None,
    )
    if heading_index is None:
        raise AssertionError(f"{path} no longer contains {_INVALID_SKIP_HEADING!r}")

    labels: set[str] = set()
    for line in lines[heading_index + 1 :]:
        if not line.strip() or line[0].isspace():
            continue
        match = _SKIP_RATIONALE_LABEL_RE.match(line)
        if match:
            labels.add(match.group("label"))
        elif not _TOP_LEVEL_LIST_ITEM_RE.match(line):
            break
    return labels


def test_invalid_skip_rationale_labels_match_across_review_skills() -> None:
    """/code-review and /plan-review must refuse the same set of skip rationales.

    Both lists exist so a session reaching the spawn-skip decision through
    either skill meets the same refusals. The rebuttal sentence after each label
    is skill-specific by design and is not compared; the label set is shared, and
    nothing else keeps the two files in step when one is edited on its own.
    """
    code_review_labels = _invalid_skip_rationale_labels(_skill_file("code-review"))
    routing_labels = _invalid_skip_rationale_labels(
        _skill_file("plan-review").parent / "ROUTING.md"
    )

    # Guards against both extractions returning empty — a bullet-format change
    # would otherwise make set equality pass vacuously.
    assert code_review_labels, "extracted no skip rationales from code-review/SKILL.md"

    assert code_review_labels == routing_labels, (
        "Invalid skip rationale labels drifted between code-review/SKILL.md and "
        "plan-review/ROUTING.md — both lists must name the same rationales.\n"
        f"  only in code-review/SKILL.md:    {sorted(code_review_labels - routing_labels)}\n"
        f"  only in plan-review/ROUTING.md:  {sorted(routing_labels - code_review_labels)}"
    )


_SCOPE_RULE_ANCHOR_RE = re.compile(r"<!-- SCOPE_RULE:(\S+) (start|end) -->")
_SCOPE_EXEMPT_ROW_ANCHOR_RE = re.compile(r"<!-- SCOPE_EXEMPT_ROW (start|end) -->")

# The anchor regions the staged-diff scope-boundary redesign introduced,
# plus the clauses nested inside the first one.
# Asserted as an exact set, not just "each found anchor is non-trivial" — a
# corpus scan alone passes vacuously if an entire anchor pair is deleted.
_EXPECTED_SCOPE_ANCHORS = {
    ("code-review", "SCOPE_RULE:code-review-staged-diff-only"),
    ("code-review", "SCOPE_EXEMPT_ROW"),
    ("code-review", "SCOPE_RULE:code-review-causal-reach"),
    ("code-review", "SCOPE_RULE:code-review-disclosure-not-suppressed"),
    ("code-review", "SCOPE_RULE:code-review-comment-row-deferred"),
    ("ready-for-review", "SCOPE_RULE:ready-for-review-cumulative-unnarrowed"),
}

_SECURITY_CONTROLS_ROW = "Adds/modifies security controls"


class TestExtractScopeAnchorRegion:
    """Direct branch coverage for _extract_scope_anchor_region's three raise
    branches, exercised with tiny inline fixtures rather than waiting for the
    real corpus to happen to contain such a defect. Mirrors
    TestTriggerAFenceScan's pattern of testing a small parsing helper
    branch-by-branch with literal string fixtures before the corpus-wide
    sweep runs.
    """

    _MARKER = "SCOPE_RULE:example"

    def _write(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "SKILL.md"
        path.write_text(content)
        return path

    def test_happy_path_extracts_enclosed_text(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} start -->\nsome rule text\n<!-- {self._MARKER} end -->",
        )
        assert _extract_scope_anchor_region(path, self._MARKER) == "some rule text"

    def test_duplicate_start_marker_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} start -->\nfirst\n<!-- {self._MARKER} start -->\n"
            f"second\n<!-- {self._MARKER} end -->",
        )
        with pytest.raises(ValueError, match="more than once"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_duplicate_end_marker_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} start -->\ntext\n<!-- {self._MARKER} end -->\n"
            f"<!-- {self._MARKER} end -->",
        )
        with pytest.raises(ValueError, match="more than once"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_start_present_end_missing_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, f"<!-- {self._MARKER} start -->\ntext")
        with pytest.raises(ValueError, match="not found"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_end_present_start_missing_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, f"text\n<!-- {self._MARKER} end -->")
        with pytest.raises(ValueError, match="not found"):
            _extract_scope_anchor_region(path, self._MARKER)

    def test_end_before_start_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            f"<!-- {self._MARKER} end -->\ntext\n<!-- {self._MARKER} start -->",
        )
        with pytest.raises(ValueError, match="before start"):
            _extract_scope_anchor_region(path, self._MARKER)


def test_scope_rule_anchors_present() -> None:
    """Every SCOPE_RULE:/SCOPE_EXEMPT_ROW anchor pair is present, ordered, and
    non-trivial in length — the SCOPE_RULE:/SCOPE_EXEMPT_ROW-namespace mirror
    of test_disposition_rule_anchors_present in test_skills.py.
    """
    MIN_SCOPE_TEXT_CHARS = 20  # a single stripped char would pass a bare "non-empty" check

    found: set[tuple[str, str]] = set()
    for skill_md_path in _all_skill_md_paths():
        text = skill_md_path.read_text()
        skill_name = skill_md_path.parent.name

        marker_names = {f"SCOPE_RULE:{m.group(1)}" for m in _SCOPE_RULE_ANCHOR_RE.finditer(text)}
        if _SCOPE_EXEMPT_ROW_ANCHOR_RE.search(text):
            marker_names.add("SCOPE_EXEMPT_ROW")

        for marker_name in marker_names:
            found.add((skill_name, marker_name))
            enclosed = _extract_scope_anchor_region(skill_md_path, marker_name)
            assert len(enclosed) >= MIN_SCOPE_TEXT_CHARS, (
                f"{skill_md_path}: {marker_name} encloses only {len(enclosed)} "
                f"non-whitespace chars after stripping — looks deleted or gutted"
            )

    assert found == _EXPECTED_SCOPE_ANCHORS, (
        "Scope-boundary anchors drifted from the expected set — a region was "
        "added, renamed, or deleted.\n"
        f"  expected but missing: {sorted(_EXPECTED_SCOPE_ANCHORS - found)}\n"
        f"  found but unexpected: {sorted(found - _EXPECTED_SCOPE_ANCHORS)}"
    )


@pytest.mark.parametrize(
    "skill_name,inner_marker,outer_marker",
    [
        ("code-review", "SCOPE_EXEMPT_ROW", "SCOPE_RULE:code-review-staged-diff-only"),
        (
            "code-review",
            "SCOPE_RULE:code-review-causal-reach",
            "SCOPE_RULE:code-review-staged-diff-only",
        ),
        (
            "code-review",
            "SCOPE_RULE:code-review-disclosure-not-suppressed",
            "SCOPE_RULE:code-review-staged-diff-only",
        ),
        (
            "code-review",
            "SCOPE_RULE:code-review-comment-row-deferred",
            "SCOPE_RULE:code-review-staged-diff-only",
        ),
    ],
)
def test_nested_anchor_fully_contained_within_outer_rule(
    skill_name: str, inner_marker: str, outer_marker: str
) -> None:
    """A nested anchor must be textually contained inside its outer
    SCOPE_RULE's start/end pair, not merely present somewhere in the file
    alongside it — the design (see the SCOPE_RULE region's own prose) is
    that a nested anchor's narrowing lives inside the outer rule, not as an
    unrelated sibling anchor. Checks both the inner start and the inner end
    offset, not just the start — an inner end marker that drifts outside the
    outer region while the inner start stays correctly nested would pass a
    start-only check silently.
    """
    skill_md_path = _skill_file(skill_name)
    # Validates presence and rejects a duplicated marker before offsets are
    # computed below — text.find() alone would silently resolve a
    # duplicated marker to its first occurrence.
    _extract_scope_anchor_region(skill_md_path, outer_marker)
    _extract_scope_anchor_region(skill_md_path, inner_marker)
    text = skill_md_path.read_text()

    outer_start = text.find(f"<!-- {outer_marker} start -->")
    outer_end = text.find(f"<!-- {outer_marker} end -->")
    inner_start = text.find(f"<!-- {inner_marker} start -->")
    inner_end = text.find(f"<!-- {inner_marker} end -->")
    assert outer_start < inner_start and inner_end < outer_end, (
        f"{skill_md_path}: {inner_marker} (offsets {inner_start}-{inner_end}) is not fully "
        f"contained inside {outer_marker} (offsets {outer_start}-{outer_end})"
    )


_PINNED_SCOPE_CLAUSES: dict[tuple[str, str], str] = {
    ("code-review", "SCOPE_RULE:code-review-causal-reach"): (
        "A defect outside the boundary that the change causes, activates, or "
        "newly reaches stays in scope for that spawn's flagging duty."
    ),
    # The defer-invariant bar's reach into earlier commits of the PR in a
    # staged round depends in part on this sentence.
    ("code-review", "SCOPE_RULE:code-review-disclosure-not-suppressed"): (
        "This narrows only speculative, undirected search for unrelated "
        "defects — it never suppresses disclosure of a defect a spawn "
        "already noticed during the full-file read required elsewhere in "
        "this skill."
    ),
    ("code-review", "SCOPE_RULE:code-review-comment-row-deferred"): (
        "whenever this boundary applies, that row does not spawn, whatever "
        "prose the boundary carries. Its exhaustive pass runs "
        'in the cumulative unnarrowed review at `ready-for-review/SKILL.md` '
        '§ "3. Code review (halt on findings)" rather than once per '
        "commit-gate round. Still enumerate the row in "
        "this step and report it on the `Spawn decisions:` line as "
        "`skipped: <row> — deferred to ready-for-review's cumulative pass`, "
        "per the Output format section's own convention. Every context "
        "where this boundary does not apply spawns the row as it spawns "
        "any other."
    ),
    ("ready-for-review", "SCOPE_RULE:ready-for-review-cumulative-unnarrowed"): (
        "This pass reviews the cumulative diff with no responsibility-boundary "
        "narrowing — see `code-review/SKILL.md`'s Step 0.6 for the rule and why. "
        "Decisions from earlier in this branch's fix loop — per-commit rounds "
        "and prior cumulative passes alike — feed in as context per "
        '`code-review/SKILL.md` § "Ripple effect triage", never as a '
        "substitute for this pass. The cache marker is written "
        "only from a clean pass of this step's own cumulative `/code-review`, "
        "never from a fix commit's staged-diff pass."
    ),
}


@pytest.mark.parametrize("skill_name,marker_name", sorted(_PINNED_SCOPE_CLAUSES))
def test_pinned_scope_clause_matches_live_text(skill_name: str, marker_name: str) -> None:
    """The pin is exact because a clause's mandatory-vs-advisory reading is not
    deterministically derivable from its text, and the model-based check that
    could derive it is local-only by cost. On failure, re-read the guarantee
    and update the constant only if the new wording preserves it.
    """
    skill_md_path = _skill_file(skill_name)
    live_text = _normalized_anchor_text(skill_md_path, marker_name)
    pinned_text = " ".join(_PINNED_SCOPE_CLAUSES[(skill_name, marker_name)].split())
    assert live_text == pinned_text, (
        f"{skill_md_path}: {marker_name} no longer matches its pinned text.\n"
        f"  live:   {live_text!r}\n"
        f"  pinned: {pinned_text!r}"
    )


class TestNormalizedAnchorText:
    """Direct coverage for _normalized_anchor_text's comparison machinery,
    mirroring TestExtractScopeAnchorRegion's literal-fixture pattern. The
    `_PINNED_SCOPE_CLAUSES` regions currently sit on one unwrapped line, so
    the reflow path is not exercised by the corpus-wide pin test above.
    """

    _MARKER = "SCOPE_RULE:example"

    def test_mismatched_text_fails_equality(self, tmp_path: Path) -> None:
        """Confirms the extracted text matches exactly, not just
        non-emptily — a truncated or partially-extracted result would
        satisfy an inequality-only check against an unrelated string
        while still defeating the pin test's equality comparison.
        """
        path = tmp_path / "SKILL.md"
        path.write_text(
            f"<!-- {self._MARKER} start -->\nthe live clause\n<!-- {self._MARKER} end -->"
        )
        extracted = _normalized_anchor_text(path, self._MARKER)
        assert extracted == "the live clause"
        assert extracted != "a mismatched pinned clause"

    def test_reflowed_text_normalizes_to_match(self, tmp_path: Path) -> None:
        """Confirms whitespace-collapsing normalizes text reflowed across
        multiple lines to match its single-line pinned form.
        """
        path = tmp_path / "SKILL.md"
        path.write_text(
            f"<!-- {self._MARKER} start -->\nthe pinned\nclause  reflowed\nacross lines\n"
            f"<!-- {self._MARKER} end -->"
        )
        assert (
            _normalized_anchor_text(path, self._MARKER)
            == "the pinned clause reflowed across lines"
        )


_CACHE_RULE_ANCHOR_RE = re.compile(r"<!-- CACHE_RULE:(\S+) (start|end) -->")

# Each anchor region a review-cache feature introduced.
# Asserted as an exact set for the same reason as _EXPECTED_SCOPE_ANCHORS: a
# corpus scan alone passes vacuously if an anchor pair is deleted.
_EXPECTED_CACHE_ANCHORS = {
    ("ready-for-review", "CACHE_RULE:ready-for-review-cumulative-diff-cache"),
    ("ready-for-review", "CACHE_RULE:ready-for-review-verification-cache"),
}


def test_cache_rule_anchors_present() -> None:
    """Every CACHE_RULE: anchor pair is present, ordered, and non-trivial in
    length — the CACHE_RULE:-namespace mirror of test_scope_rule_anchors_present
    above.
    """
    MIN_CACHE_TEXT_CHARS = 20  # a single stripped char would pass a bare "non-empty" check

    found: set[tuple[str, str]] = set()
    for skill_md_path in _all_skill_md_paths():
        text = skill_md_path.read_text()
        skill_name = skill_md_path.parent.name

        marker_names = {f"CACHE_RULE:{m.group(1)}" for m in _CACHE_RULE_ANCHOR_RE.finditer(text)}

        for marker_name in marker_names:
            found.add((skill_name, marker_name))
            enclosed = _extract_scope_anchor_region(skill_md_path, marker_name)
            assert len(enclosed) >= MIN_CACHE_TEXT_CHARS, (
                f"{skill_md_path}: {marker_name} encloses only {len(enclosed)} "
                f"non-whitespace chars after stripping — looks deleted or gutted"
            )

    assert found == _EXPECTED_CACHE_ANCHORS, (
        "Cache-rule anchors drifted from the expected set — a region was "
        "added, renamed, or deleted.\n"
        f"  expected but missing: {sorted(_EXPECTED_CACHE_ANCHORS - found)}\n"
        f"  found but unexpected: {sorted(found - _EXPECTED_CACHE_ANCHORS)}"
    )


_PINNED_CACHE_CLAUSES: dict[tuple[str, str], str] = {
    ("ready-for-review", "CACHE_RULE:ready-for-review-cumulative-diff-cache"): (
        "Before computing anything, run `~/.claude/scripts/marker.sh status`: "
        "if its `cumulative-review` line reads `live`, this diff content already "
        "passed a full unnarrowed cumulative review — skip the diff computation "
        "and the `/code-review` invocation below, report the cache hit in the "
        "Completion summary, and continue to step 4. Content type is never a "
        "skip reason on its own — on `historical` or `absent`, markdown, skill, "
        "and config diffs get the same pass as everything else."
    ),
    ("ready-for-review", "CACHE_RULE:ready-for-review-verification-cache"): (
        "Before selecting any commands, run `~/.claude/scripts/marker.sh check "
        "verification`. `match` means this exact tree already passed a clean "
        "verification pass inside the freshness window. On a match: skip the "
        "commands below, report the cache hit in the Completion summary, and "
        "continue to step 3. On `no-match`, run the step normally, then write "
        "`~/.claude/scripts/marker.sh write verification` only after every "
        "selected command has run and passed. Do not write it after the "
        "scope-exception skip below — that path runs no commands, so nothing "
        "has passed."
    ),
}


@pytest.mark.parametrize("skill_name,marker_name", sorted(_PINNED_CACHE_CLAUSES))
def test_pinned_cache_clause_matches_live_text(skill_name: str, marker_name: str) -> None:
    """The pin is exact for the same reason as test_pinned_scope_clause_matches_live_text:
    the skip-vs-run reading is not deterministically derivable from the text alone.
    On failure, re-read the guarantee and update the constant only if the new
    wording preserves it.
    """
    skill_md_path = _skill_file(skill_name)
    live_text = _normalized_anchor_text(skill_md_path, marker_name)
    pinned_text = " ".join(_PINNED_CACHE_CLAUSES[(skill_name, marker_name)].split())
    assert live_text == pinned_text, (
        f"{skill_md_path}: {marker_name} no longer matches its pinned text.\n"
        f"  live:   {live_text!r}\n"
        f"  pinned: {pinned_text!r}"
    )


class TestChangeTypeTableLeftColumns:
    """Direct coverage for _change_type_table_left_columns's `line.split("|")`
    parse, mirroring TestExtractScopeAnchorRegion's literal-fixture pattern.
    """

    def test_embedded_pipe_in_left_column_truncates(self, tmp_path: Path) -> None:
        """A left-column cell containing inline code with an embedded `|`
        (`` `a | b` ``) truncates at that first `|` — this documents the
        helper's current split-based behavior rather than changing it.
        """
        path = tmp_path / "SKILL.md"
        path.write_text(
            "| Change type | Spawn / invoke |\n"
            "|-------------|----------------|\n"
            "| Uses inline code `a | b` in shorthand | `some-reviewer` |\n"
        )
        assert _change_type_table_left_columns(path) == ["Uses inline code `a"]


class TestScopeExemptChangeTypeRowText:
    """Direct coverage for _scope_exempt_change_type_row_text's no-match raise
    branch, mirroring TestChangeTypeTableLeftColumns's edge-case-only
    literal-fixture pattern.
    """

    def test_no_matching_row_raises(self, tmp_path: Path) -> None:
        """SCOPE_EXEMPT_ROW's shorthand ("Some other row") matches no
        Change-type row's left column ("A different row entirely") —
        AssertionError names both the path and the shorthand.
        """
        path = tmp_path / "SKILL.md"
        path.write_text(
            "<!-- SCOPE_EXEMPT_ROW start -->Some other row<!-- SCOPE_EXEMPT_ROW end -->\n"
            "| Change type | Spawn / invoke |\n"
            "|-------------|----------------|\n"
            "| A different row entirely | `some-reviewer` |\n"
        )
        with pytest.raises(AssertionError) as excinfo:
            _scope_exempt_change_type_row_text(path)
        assert str(path) in str(excinfo.value)
        assert "Some other row" in str(excinfo.value)


def test_scope_exempt_row_resolves_to_real_change_type_row() -> None:
    """SCOPE_EXEMPT_ROW's enclosed shorthand must exact-match a real
    Change-type table row's left column — guards against silent drift if
    that row is reworded without updating the anchor.
    """
    skill_md_path = _skill_file("code-review")
    exempt_shorthand = _extract_scope_anchor_region(skill_md_path, "SCOPE_EXEMPT_ROW")
    real_rows = _change_type_table_left_columns(skill_md_path)
    assert exempt_shorthand in real_rows, (
        f"SCOPE_EXEMPT_ROW's shorthand {exempt_shorthand!r} does not exact-match "
        f"any Change-type table row in {skill_md_path}"
    )


def test_scope_exempt_row_excludes_security_controls_row() -> None:
    """Guards against a future edit sliding the *Adds/modifies security
    controls* row's shorthand into SCOPE_EXEMPT_ROW, which would narrow
    security spawn decisions — the match-narrowing carve-out is prose-only
    by design.
    """
    skill_md_path = _skill_file("code-review")
    exempt_shorthand = _extract_scope_anchor_region(skill_md_path, "SCOPE_EXEMPT_ROW")
    assert exempt_shorthand != _SECURITY_CONTROLS_ROW

    real_rows = _change_type_table_left_columns(skill_md_path)
    assert _SECURITY_CONTROLS_ROW in real_rows, (
        f"{skill_md_path}: expected Change-type row {_SECURITY_CONTROLS_ROW!r} not "
        "found — test fixture drifted from the table"
    )


def test_claude_hook_review_section10_handoff_rules_present() -> None:
    """Section 10 hands the reviewers a hook path plus its diff, never a branch
    or range name, and says so when the diff is unavailable."""
    skill_md_path = _skill_file("claude-hook-review")
    lines = skill_md_path.read_text().splitlines(keepends=True)
    section_start, section_end = _section_between(
        lines, "## 10. Operational-footprint escalation", skill_md_path
    )
    section_text = "".join(lines[section_start:section_end])
    for literal in (
        "Never pass a branch or range name",
        "tell the reviewer the diff is unavailable instead of passing the path alone",
    ):
        assert literal in section_text, (
            f"{skill_md_path}: section 10 no longer carries the exact literal {literal!r}"
        )


def test_code_review_staged_diff_instruction_lives_in_its_own_note_only() -> None:
    """The comment-discipline-reviewer diff-artifact-resolution procedure
    lives in its own subsection directly below the Change-type table, not
    inside the row itself and not in Step 0. A file-wide `in` check would
    pass just as well if a later edit moved the instruction back into the
    row or into Step 0, so this locates the note's own text before also
    checking the file-wide occurrence count. It also pins the note's
    `> /dev/null` redirect, with no further redirect after it, so the staged
    diff stays out of the session's context while the script's stderr signals
    still reach it.
    """
    skill_md_path = _skill_file("code-review")
    text = skill_md_path.read_text()
    row_text = _scope_exempt_change_type_row_text(skill_md_path)

    note_heading = "**Resolving `comment-discipline-reviewer`'s diff artifact.**"
    next_heading = "**Invalid skip rationales.**"
    assert note_heading in text, (
        f"{skill_md_path}: the diff-artifact-resolution note heading is missing"
    )
    note_start = text.index(note_heading)
    note_end = text.index(next_heading, note_start)
    note_text = text[note_start:note_end]

    staged_diff_literal = "pr-diff-against-base.sh --staged --diff-file"
    assert staged_diff_literal not in row_text, (
        f"{skill_md_path}: the comment-discipline-reviewer row should point to "
        "the resolution note below the table, not restate the procedure inline"
    )
    assert staged_diff_literal in note_text, (
        f"{skill_md_path}: the resolution note no longer carries the "
        "diff-artifact-resolution instruction"
    )
    assert text.count(staged_diff_literal) == 1, (
        f"{skill_md_path}: {staged_diff_literal!r} must appear exactly once, inside "
        "the resolution note only -- not duplicated into Step 0 or the row"
    )

    discarding_command = "~/.claude/scripts/pr-diff-against-base.sh --staged --diff-file > /dev/null"
    assert discarding_command + "`" in note_text, (
        f"{skill_md_path}: the resolution note must run {discarding_command!r} "
        "as a code span that ends right after the redirect, so the diff stays "
        "out of the session and stderr still reaches it"
    )

    for literal in (
        "DIFF_EMPTY:",
        "pasted-snippet or ad-hoc review with no git diff behind it at all",
        "quote the script's own stderr line",
        "halt the review",
    ):
        assert literal in note_text, (
            f"{skill_md_path}: the resolution note no longer carries "
            f"the exact literal {literal!r}"
        )

    pr_diff_script_source = (SCRIPTS_DIR / "pr-diff-against-base.sh").read_text()
    assert "--staged)" in pr_diff_script_source, (
        "pr-diff-against-base.sh: expected the --staged case-arm label, not just "
        "the flag name in the usage line or header comment"
    )


def test_comment_discipline_row_points_to_its_deferral() -> None:
    """The comment/prose Change-type row must carry its own pointer to Step
    0.6's deferral clause — a rule stated only in Step 0.6 is invisible to a
    reader who consults the table without also reading Step 0.6 itself.
    """
    skill_md_path = _skill_file("code-review")
    row_text = _scope_exempt_change_type_row_text(skill_md_path)
    assert "Step 0.6 defers this row out of staged-diff commit-gate rounds." in row_text, (
        f"{skill_md_path}: the comment/prose row no longer points to its own "
        "deferral out of staged-diff commit-gate rounds"
    )


_CODE_REVIEW_STEP_0_6_HEADING = "## Step 0.6 — Pre-judgment table check"


def test_comment_discipline_row_states_its_whole_diff_lane() -> None:
    """The comment/prose row keeps its any-language, whole-diff lane
    statement, the guard against narrowing by file type dropping shell or
    TOML comments.
    """
    skill_md_path = _skill_file("code-review")
    row_text = _scope_exempt_change_type_row_text(skill_md_path)
    for literal in (
        "Its lane is every comment and durable-doc prose site in the diff, "
        "whatever the file's language or type.",
        "Hand it the whole diff, never a per-spawn file subset",
    ):
        assert literal in row_text, (
            f"{skill_md_path}: the comment/prose row no longer carries "
            f"the exact literal {literal!r}"
        )


def test_step_0_6_narrowing_keys_lanes_by_change_not_file_type() -> None:
    """Step 0.6 keeps its statements that lanes key on what a change does and
    that the comment/prose row is exempt from per-spawn narrowing, the guard
    against narrowing by file type dropping a reviewer's in-lane files.
    """
    skill_md_path = _skill_file("code-review")
    section_text = _heading_section_text(skill_md_path, _CODE_REVIEW_STEP_0_6_HEADING)
    for literal in (
        "Judge a lane by what each file's change does, the way the Change type "
        "column keys its rows, never by file type.",
        "The comment-discipline row's spawn instruction exempts that row from "
        "this per-spawn narrowing.",
    ):
        assert literal in section_text, (
            f"{skill_md_path}: Step 0.6 no longer carries the exact literal {literal!r}"
        )
