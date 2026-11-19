"""Cross-references from docs, `CLAUDE.md` and skill bodies to headings, threat-model tier wording, redaction-policy items."""
from __future__ import annotations

import re

import pytest
from _skill_md_helpers import (
    _assert_citation_resolves_to_heading,
    _section_between,
    _skill_file,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import REPO_ROOT


def test_handoff_nudge_doc_cites_handoff_warrant_check_section() -> None:
    """docs/handoff-nudge.md's cross-reference to handoff/SKILL.md's
    warrant-check section resolves to a real heading there.

    `docs/*.md` sits outside `_all_skill_md_files`'s scanned corpus
    (SKILL.md plus its SKILL_AUXILIARY_MD_NAMES siblings only), so
    test_skill_citations_resolve_to_real_headings never sees this citation —
    targeted narrowly here instead of widening that corpus.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "handoff-nudge.md",
        "handoff/SKILL.md",
        "Before writing: is a handoff warranted?",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "CLAUDE.md",
        "docs/cost-levers-considered.md",
        "docs/design-decisions/schedulewakeup-misapplied-documented.md",
        "docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md",
        "docs/transcript-analysis.md",
    ],
)
def test_tooling_measurement_citation_resolves_to_real_heading(
    relative_path: str,
) -> None:
    """Each sibling withholding site's `docs/private-project-redaction.md`
    § "Publishing a tooling measurement" citation resolves to a real
    heading there.

    `docs/*.md` sits outside `_all_skill_md_files`'s scanned corpus (SKILL.md
    plus its SKILL_AUXILIARY_MD_NAMES siblings only), so
    test_skill_citations_resolve_to_real_headings never sees these citations
    — targeted narrowly here instead of widening that corpus.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / relative_path,
        "docs/private-project-redaction.md",
        "Publishing a tooling measurement",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "CLAUDE.md",
        "plugins/claude-hook-review/skills/claude-hook-review/SKILL.md",
        ".claude/skills/code-review-claude-config/SKILL.md",
        ".claude/skills/plan-review-claude-config/SKILL.md",
    ],
)
def test_threat_model_tiers_citation_resolves_to_real_heading(relative_path: str) -> None:
    """CLAUDE.md's '## Hook threat model' section, claude-hook-review's SKILL.md
    tier-header paragraph, and the claude-config code-review and plan-review
    layers each cite `docs/hooks.md` § "Threat-model tiers" — resolves to a
    real heading there.

    The layer and plugin entries pin that the citation is still present, since
    the repo-wide citation scan only checks that existing citations resolve.
    CLAUDE.md is outside that scan, so this test checks resolution for it.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / relative_path,
        "docs/hooks.md",
        "Threat-model tiers",
        repo_root=REPO_ROOT,
    )


# Tier-disposition clauses that keep the review layers fail-closed.
# The `hooks-doc-regression-rule` entry pins the regression-judging block in its single home.
# The `hooks-doc-debt-admission` entry pins the existing-debt admission rule, the disclosure-gate pointer to it, and the lead.
# The code-review-layer phrases for the DEFER ban, the ask and the Known-gaps recording pin the ask-and-record path.
# The layer entries pin the per-vector-gap versus genuinely-lax question.
# The layer entries pin that a regression stays blocking at every tier.
# The layer entries pin that a finding the tier section does not explicitly waive or route stays under the base rules.
# A tripwire for a wording trim that silently drops one of them.
_TIER_DISPOSITION_SECTIONS = [
    pytest.param(
        ".claude/skills/code-review-claude-config/SKILL.md",
        "## Finding disposition addition",
        [
            "per-vector gap",
            "genuinely-lax failure stays under the base rules",
            "lists `untrusted-input` stays under the base rules",
            "is an enforcement-invariant finding",
            "is not an enforcement-invariant finding",
            "at every tier",
            "does not explicitly waive or route stays under the base rules.",
            "No recording",
            "DEFERred under criterion 3 as a waived or routed finding against a gate",
            "that is not a regression is never DEFERred",
            "admits it as permanent existing debt",
            "takes a blocking stop-and-ask",
            "the engineer's own SETTLED row kept with `--carry-forward`",
            "Before the engineer answers, that stop also gives the base contradiction-route "
            "rule's human-keep disclosures.",
        ],
        id="code-review-layer",
    ),
    pytest.param(
        ".claude/skills/plan-review-claude-config/SKILL.md",
        "## Gate threat-model tiers (Domain: Security; Output format)",
        [
            "per-vector gap",
            "genuinely-lax failure stays under the base rules",
            "lists `untrusted-input` stays under the base rules",
            "stays an enforcement-invariant finding",
            "is not an enforcement-invariant finding",
            "at every tier",
            "does not explicitly waive or route stays under the base rules.",
            "No recording",
            "cover every regression",
        ],
        id="plan-review-layer",
    ),
    pytest.param(
        "docs/hooks.md",
        "## Threat-model tiers",
        [
            "A gate the change adds has no merge-base denials to regress from, "
            "so no finding that it dropped a denial is a regression; its tier decides. "
            "A gate that renames or replaces merge-base gates, wholly or in part, "
            "is not one the change adds; neither is a gate whose status is unclear. "
            "Compare such a gate against the merge-base behavior it takes over. "
            "For any gate that is not strictly added by the change, including a "
            "replacement or a gate of unclear status, treat the finding as a "
            "regression when any of these holds: "
            "- The comparison against the merge-base is unclear. "
            "- A fail-open path the change adds or alters has no deny test. "
            "- The change edits the gate's tier line, its tracking pointer, or this "
            "section's rules above its per-hook classification table. "
            "A `CLAUDE.md` or SKILL.md that applies this rule points here rather than "
            "restating it; this section is its only home.",
        ],
        id="hooks-doc-regression-rule",
    ),
    pytest.param(
        "docs/hooks.md",
        "## Threat-model tiers",
        [
            "Except for a gap that needs a shape a cooperative agent would never emit, "
            "a gap discovered later counts as recorded this way only once the human's answer "
            "to a blocking stop-and-ask accepts that gap as permanent debt, and the commit "
            "message or PR body that records it states the rationale.",
            # The predicate must be "does emit": a broader modal reading would override the
            # never-emit recordings the section deliberately allows.
            "Until then no text or pointer records as debt a gap discovered later that needs "
            "a shape a cooperative agent does emit, wherever it sits, even where another "
            "sentence of this section treats it as debt or routes it without a blocking finding.",
            "A routine encoding a cooperative agent emits, such as a base64 credential in a "
            "Kubernetes Secret manifest, is not a waivable shape, so the **Existing debt** rule "
            "above decides whether a finding on it may be recorded.",
            "**Existing debt** is a closed set",
        ],
        id="hooks-doc-debt-admission",
    ),
]


@pytest.mark.parametrize(
    ("relative_path", "section_heading", "required_phrases"),
    _TIER_DISPOSITION_SECTIONS,
)
def test_gate_tier_disposition_sections_keep_invariant_clauses(
    relative_path: str,
    section_heading: str,
    required_phrases: list[str],
) -> None:
    """Each entry's section, whitespace-normalized, still carries its clauses.

    Scoped to the section so the same words elsewhere in the file cannot
    satisfy it. Whitespace-normalized so a re-wrap does not break it. Pins
    that the text is present, not that a reviewer follows it.
    """
    section_file_path = REPO_ROOT / relative_path
    lines = section_file_path.read_text().splitlines(keepends=True)
    start_idx, end_idx = _section_between(lines, section_heading, section_file_path)
    section_text = " ".join("".join(lines[start_idx:end_idx]).split())

    missing_phrases = [phrase for phrase in required_phrases if phrase not in section_text]
    assert not missing_phrases, (
        f"{section_file_path}'s {section_heading!r} section no longer carries "
        f"{missing_phrases}; each keeps the tier-disposition rule fail-closed. "
        "Restore the clause, or update this pin and say why in the commit message."
    )


# A single-backtick span made of letters, hyphens, and underscores only — no
# whitespace, colon, or other punctuation inside — the shape a bare tier
# token takes, so a near-miss spelling (`untrusted_input`, `Irreversible`)
# is extracted too. Excludes a multi-word span like
# `` `# tier-threat-model: <tiers>` `` or `` `hook-class: gate` ``, each of
# which contains a space or a colon and so can't satisfy this pattern for
# its full backtick-delimited content.
_TIER_TOKEN_SHAPED_BACKTICK_SPAN_RE = re.compile(r"`([A-Za-z][A-Za-z_-]*)`")

_TIER_DEFINITION_BULLET_RE = re.compile(r"^- `([a-z][a-z-]*)` — ", re.MULTILINE)


def _tier_token_shaped_backtick_spans(paragraph: str) -> set[str]:
    """Every distinct letters/hyphens/underscores-only single-backtick span in `paragraph`."""
    return set(_TIER_TOKEN_SHAPED_BACKTICK_SPAN_RE.findall(paragraph))


def _hooks_md_tier_vocabulary() -> set[str]:
    """The tier names defined by docs/hooks.md's '## Threat-model tiers'
    definition bullets — the authoritative vocabulary."""
    doc_text = (REPO_ROOT / "docs" / "hooks.md").read_text()
    section = re.search(
        r"^## Threat-model tiers\s*\n(.*?)(?=^### |^## |\Z)",
        doc_text,
        re.MULTILINE | re.DOTALL,
    )
    assert section, "docs/hooks.md has no '## Threat-model tiers' section"
    return set(_TIER_DEFINITION_BULLET_RE.findall(section.group(1)))


def _claude_hook_review_tier_paragraph() -> str:
    """The tier-header paragraph in claude-hook-review's SKILL.md (through
    the next blank line), isolated from the rest of the file so the
    backtick-span scan below doesn't also sweep unrelated code spans
    elsewhere in the document (hook names, `_lib.sh`, `hook-class` values,
    etc.)."""
    skill_md_text = _skill_file("claude-hook-review").read_text()
    match = re.search(
        r"^In a repo that adopts tier headers.*?(?=\n\s*\n|\Z)",
        skill_md_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "claude-hook-review/SKILL.md's tier-header paragraph not found"
    return match.group(0)


def test_claude_hook_review_tier_paragraph_backtick_tokens_equal_hooks_md_tiers() -> None:
    """The tier-token-shaped backtick spans in claude-hook-review SKILL.md's
    tier-header paragraph equal the tier set docs/hooks.md defines, so a
    misspelled, stale, or dropped tier there fails.
    """
    vocabulary = _hooks_md_tier_vocabulary()
    assert vocabulary, "docs/hooks.md's tier definition bullets were not found"
    spans = _tier_token_shaped_backtick_spans(_claude_hook_review_tier_paragraph())
    assert spans == vocabulary, (
        f"claude-hook-review/SKILL.md's tier-header paragraph backtick spans "
        f"{sorted(spans)} differ from docs/hooks.md's tiers {sorted(vocabulary)}"
    )


def test_tier_token_shaped_backtick_spans_ignores_multiword_and_finds_near_misses() -> None:
    """Meta-test for _tier_token_shaped_backtick_spans: a hyphenated
    non-tier span, an underscore spelling, and a capitalized spelling are
    all extracted (so equality against the vocabulary rejects them), while
    a multi-word span (`` `# tier-threat-model: <tiers>` ``) stays
    unextracted.
    """
    decoy_paragraph = (
        "A decoy naming `hook-class`, `untrusted_input`, and `Irreversible`, "
        "plus the real header shape `# tier-threat-model: <tiers>`, which "
        "must not be extracted as a bare span."
    )
    assert _tier_token_shaped_backtick_spans(decoy_paragraph) == {
        "hook-class",
        "untrusted_input",
        "Irreversible",
    }


def test_cost_trend_share_only_citation_resolves_to_real_heading() -> None:
    """docs/transcript-analysis.md's `cost-trend`/`--share-only` exclusion
    cites `docs/private-project-redaction.md` § "A wider corpus goes to
    the owner, never into a public artifact" for why a calendar-anchored
    series is barred outright — resolves to a real heading there.

    A prior wording of this same citation named the wrong section ("What it
    permits", a requirement `cost-trend` already satisfies) and shipped
    without any test catching it — this test exists so a future rename or
    mis-citation on this line fails here instead of requiring manual review.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "transcript-analysis.md",
        "docs/private-project-redaction.md",
        "A wider corpus goes to the owner, never into a public artifact",
        repo_root=REPO_ROOT,
    )


def test_owner_authorized_figure_citation_resolves_to_real_heading() -> None:
    """docs/cost-levers-considered.md's sleep-poll follow-up entry cites
    `docs/private-project-redaction.md` § "The owner can authorize one
    figure, case by case" for its publication authorization — resolves to
    a real heading there.

    This is the one citation in that entry carrying evidentiary weight for
    a security-relevant control (publication authorization). Per this
    repo's citation-grammar rule, a future heading rename or restructure
    would otherwise silently orphan it with no reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "cost-levers-considered.md",
        "docs/private-project-redaction.md",
        "The owner can authorize one figure, case by case",
        repo_root=REPO_ROOT,
    )


def test_pr_cost_export_aggregate_citation_resolves_to_real_heading() -> None:
    """docs/pr-cost.md cites `docs/private-project-redaction.md` § "The
    owner can authorize one figure, case by case" somewhere — resolves to a
    real heading there.

    A heading rename would silently orphan the pointer to the per-figure
    authorization gate, and docs/*.md sits outside the generic skill-citation
    sweep.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "pr-cost.md",
        "docs/private-project-redaction.md",
        "The owner can authorize one figure, case by case",
        repo_root=REPO_ROOT,
    )


def test_review_round_cost_citation_resolves_to_real_heading() -> None:
    """docs/private-project-redaction.md points to
    `docs/transcript-analysis.md` § "review-round-cost" for
    `review-round-cost --pooled`'s refusal list, withholding floors, and
    sample output.

    This test checks that the heading exists there.

    The redaction doc defers to that section instead of restating the
    contract, so a heading rename there would otherwise orphan the pointer
    with no reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "private-project-redaction.md",
        "docs/transcript-analysis.md",
        "review-round-cost",
        repo_root=REPO_ROOT,
    )


def test_routing_gate_design_record_cites_routing_guard_section() -> None:
    """The routing-gate design record points to
    `docs/transcript-analysis.md` § "routing-guard" for how to run the
    subcommand.

    The record defers to that section instead of restating how to run it,
    and docs/*.md sits outside the generic skill-citation sweep, so
    a heading rename would otherwise orphan the pointer with no
    reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "design-decisions" / "review-orchestrator-routing-gate.md",
        "docs/transcript-analysis.md",
        "routing-guard",
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "pr_cost_heading",
    ["Data", "Residual replication paths the git-tree check doesn't close"],
)
def test_transcript_analysis_doc_cites_real_pr_cost_headings(pr_cost_heading: str) -> None:
    """docs/transcript-analysis.md cites `docs/pr-cost.md` § "Data" and
    § "Residual replication paths the git-tree check doesn't close" somewhere
    — each resolves to a real heading there.

    The doc defers to pr-cost's ledger rules instead of restating them, and
    docs/*.md sits outside the generic skill-citation sweep, so a heading
    rename in docs/pr-cost.md would otherwise orphan the pointer with no
    reader-visible symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "transcript-analysis.md",
        "docs/pr-cost.md",
        pr_cost_heading,
        repo_root=REPO_ROOT,
    )


@pytest.mark.parametrize(
    ("target", "heading"),
    [
        ("docs/session-config-keys-log.md", "State at a transcript record"),
        ("docs/session-config-keys-log.md", "Coverage"),
        ("README.md", "Updating after git pull"),
    ],
)
def test_routing_guard_section_cites_real_key_state_headings(target: str, heading: str) -> None:
    """docs/transcript-analysis.md's `routing-guard` section cites the session
    config-keys log's reader rules and `README.md` § "Updating after git pull"
    for its key-state rules and next steps — each resolves to a real heading.

    The section defers to the reader doc instead of restating its rules, and
    docs/*.md sits outside the generic skill-citation sweep, so a heading
    rename there would otherwise orphan the pointer with no reader-visible
    symptom.
    """
    _assert_citation_resolves_to_heading(
        REPO_ROOT / "docs" / "transcript-analysis.md",
        target,
        heading,
        repo_root=REPO_ROOT,
    )


_SHOW_WITHHELD_BARRED_LIST_FILES = (
    ".claude/skills/code-review-claude-config/SKILL.md",
    "docs/private-project-redaction.md",
)
_LIST_ITEM_START_RE = re.compile(r"^\s*[-*]\s")


def _list_items(markdown_text: str) -> list[str]:
    """Each markdown list item with its hard-wrapped continuation lines joined.

    A continuation line is a non-blank line indented under the item. A blank
    line or the next list item ends the item.
    """
    items: list[str] = []
    current_item_lines: list[str] | None = None
    for line in markdown_text.splitlines():
        if _LIST_ITEM_START_RE.match(line):
            if current_item_lines is not None:
                items.append(" ".join(current_item_lines))
            current_item_lines = [line.strip()]
        elif current_item_lines is not None and line.strip() and line[0].isspace():
            current_item_lines.append(line.strip())
        else:
            if current_item_lines is not None:
                items.append(" ".join(current_item_lines))
            current_item_lines = None
    if current_item_lines is not None:
        items.append(" ".join(current_item_lines))
    return items


def test_list_items_ends_each_item_at_the_next_item_a_blank_line_or_a_dedent() -> None:
    """`_list_items` bounds each item, so a word checked inside one item cannot
    be satisfied by a neighbouring item or by prose after the list.

    The text has two adjacent items, one item ended by a blank line, and one
    item ended by an unindented line.
    """
    markdown_text = (
        "- first item\n"
        "  wrapped continuation\n"
        "- second item\n"
        "\n"
        "  indented text after a blank line\n"
        "- third item\n"
        "unindented prose after the list\n"
    )

    assert _list_items(markdown_text) == [
        "- first item wrapped continuation",
        "- second item",
        "- third item",
    ]


@pytest.mark.parametrize("relative_path", _SHOW_WITHHELD_BARRED_LIST_FILES)
def test_show_withheld_output_stays_in_a_barred_list_item(relative_path: str) -> None:
    """Each barred-output list still has one item that names `--show-withheld`
    and says the output is barred.

    Pins the presence of the bar, not its wording or its authorization
    language. The word "barred" is checked inside that one item only, since
    each file uses it elsewhere, and deleting the item must fail this test.
    """
    items_naming_flag = [
        item for item in _list_items((REPO_ROOT / relative_path).read_text()) if "--show-withheld" in item
    ]
    assert len(items_naming_flag) == 1, (
        f"{relative_path}: expected exactly one list item naming --show-withheld, "
        f"found {len(items_naming_flag)}"
    )
    assert re.search(r"\bbarred\b", items_naming_flag[0]), (
        f"{relative_path}: the list item naming --show-withheld no longer says the output is barred"
    )


_CASE_STUDY_POOLED_FIGURE_MARKERS_RE = re.compile(
    r"pooled across|machine-wide|cross-machine|multi-account", re.IGNORECASE
)

# Known false-negative: a file disclosing an unscoped review-trace/
# subagent-mix/fail-seq/struggle/duration/pr-link/buckets figure without any
# of the four marker phrases above is invisible to this regex.
# docs/case-studies/worktree-enforcement.md's "The general philosophy"
# section is a live example: it matches no marker here, and is grandfathered
# by name in Remediation instead.
# A precise detector would flag a scope-unguaranteed-subcommand citation
# with no adjacent --this-repo/--config-dir disclosure.
# That detector is not implemented here.

# Case studies published after the single-account bar took effect that use a
# marker phrase without publishing a pooled figure under the old carve-out —
# extend this set, never delete a prior entry, when
# test_case_study_pooled_figure_markers_are_registered_or_new below flags a
# new false positive.
_CASE_STUDY_POOLED_MARKER_ALLOWLIST: frozenset[str] = frozenset()


def test_case_study_pooled_figure_markers_are_registered_or_new() -> None:
    """Every docs/case-studies/*.md file using a pooled-figure marker phrase
    is named in docs/private-project-redaction.md's Remediation section, or
    is explicitly allowlisted as a new-since-this-bar file.

    Heuristic completeness check, not a mechanical citation-resolution test
    — it catches the shape of gap where a published case study matches the
    grandfathered set's own description (a machine-wide, multi-account
    corpus) but is missing from Remediation's enumerated list, breaking the
    residual-narrowing check that list feeds.
    """
    remediation_text = (REPO_ROOT / "docs" / "private-project-redaction.md").read_text()
    remediation_section = remediation_text[remediation_text.index("### Remediation") :]

    case_studies_dir = REPO_ROOT / "docs" / "case-studies"
    unregistered = [
        path.name
        for path in sorted(case_studies_dir.glob("*.md"))
        if path.stem not in _CASE_STUDY_POOLED_MARKER_ALLOWLIST
        and _CASE_STUDY_POOLED_FIGURE_MARKERS_RE.search(path.read_text())
        and path.name not in remediation_section
    ]

    assert not unregistered, (
        "docs/case-studies/*.md file(s) use a pooled-figure marker phrase "
        "(pooled across / machine-wide / cross-machine / multi-account) but "
        "are not named in docs/private-project-redaction.md's Remediation "
        "section. Add each to that list if it predates the single-account "
        "bar, or to _CASE_STUDY_POOLED_MARKER_ALLOWLIST above if it's a new, "
        "compliant file using the phrase incidentally:\n"
        + "\n".join(f"  {name}" for name in unregistered)
    )
