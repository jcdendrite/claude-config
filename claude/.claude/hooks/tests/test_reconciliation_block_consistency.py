"""Enforce consistency of the ## Reconciliation block across code-review/SKILL.md
and plan-review/ROUTING.md.

The block is intentionally duplicated (no shared partials — root CLAUDE.md;
design-decisions.md §4): `ROUTING.md` is a co-located auxiliary file, not a
shared include, so the two Reconciliation sections must each stand alone.
`ROUTING.md` carries the same length cap (`check-skill-length.sh`) and
review-marker requirement (`require-skill-review.sh`) as `SKILL.md` files,
but neither mechanism diffs `ROUTING.md`'s content against
`code-review/SKILL.md`'s — a length cap and a review marker each look at one
file at a time, not at cross-file agreement. Semantic drift between the two
Reconciliation blocks is still invisible to both, which is what this test
watches for.

Modeled on `TestFileBasedOutputBlockConsistency` (test_agent_roster.py), but
differs in four ways the template does not transfer:

1. Both casings of the per-file wrong-shape token are normalized (the
   capitalized bullet label and the lowercase branch-sentence form) — the
   template only normalizes one token form per file.
2. Extraction is bounded at the next `^## ` heading rather than a literal
   sentinel string, because the two files' last Reconciliation line carries a
   different per-file token (`Step 1` / `Step 4`) and the following heading
   differs (`## Finding disposition` vs `## Item ownership`) — no literal
   sentinel works for both. A terminating heading must actually be found;
   falling off EOF is a failure, not a silent pass, since a Reconciliation
   section relocated to end-of-file would otherwise extract cleanly and
   compare wrong.
3. Each file carries its own token map (`{path: {token: canonical}}`) rather
   than one token derived the same way for every file, because the two files'
   token sets differ (`ROUTING.md` has no `Step 1`/`Implementation-wrong-shape`
   at all). Assert-before-replace is scoped per file and per token.
4. Byte-equivalence alone passes if a future edit deletes the collapsing rule,
   the discriminator, or the co-ownership disclaimer from both files
   symmetrically — so this suite also asserts each is present in the
   extracted block, not only that the two blocks match each other
   (test-conventions §5, regression-test intent). Each marker anchors text
   unique to the rule it guards, not a phrase shared with adjacent prose: a
   marker satisfied by unrelated text elsewhere in the block would still pass
   after the guarded rule itself is deleted, defeating the presence check.
"""
from __future__ import annotations

from pathlib import Path

from helpers import CLAUDE_DIR

REPO_ROOT = CLAUDE_DIR.parent.parent

_SKILL_MD = REPO_ROOT / "claude" / ".claude" / "skills" / "code-review" / "SKILL.md"
_ROUTING_MD = REPO_ROOT / "claude" / ".claude" / "skills" / "plan-review" / "ROUTING.md"

_SECTION_START_HEADING = "## Reconciliation"

# Per-file token → canonical-placeholder map. Each file carries a different
# token set (ROUTING.md has no "Step 1" or "Implementation-wrong-shape" at
# all), so the map is keyed by path rather than derived uniformly the way the
# template derives one token from path.stem.
_TOKEN_MAP: dict[Path, dict[str, str]] = {
    _SKILL_MD: {
        "Implementation-wrong-shape": "WRONG_SHAPE_LABEL",
        "implementation-wrong-shape": "wrong_shape_label",
        "Step 1": "STEP_N",
    },
    _ROUTING_MD: {
        "Design-wrong-shape": "WRONG_SHAPE_LABEL",
        "design-wrong-shape": "wrong_shape_label",
        "Step 4": "STEP_N",
    },
}

# Presence markers asserted on the raw (pre-normalization) extracted block —
# file-invariant substrings so the same assertion applies to both files. Each
# anchors text unique to the specific rule it guards, not a phrase shared with
# adjacent prose — "distinct failure modes on one surface" alone would also
# match the general discriminator sentence, so a symmetric revert of the
# co-ownership disclaimer (back to the round-2 blanket-exclusion bug) would
# still pass. "does not disqualify escalation" appears nowhere else.
_COLLAPSING_RULE_MARKER = "is never a reason to skip a spawn"
_FAILURE_MODE_DISTINCTNESS_MARKER = "fail to name a consequence traceable in this code"
_CO_OWNERSHIP_DISCLAIMER_MARKER = "does not disqualify escalation"


def _extract_reconciliation_block(path: Path) -> str:
    """Extract the '## Reconciliation' section from `path`.

    Bounded at the next line matching '^## ' (exclusive) rather than a literal
    sentinel, since the two files' last Reconciliation line and following
    heading both differ. Asserts a terminating heading was actually found —
    falling off EOF silently would let a relocated section extract cleanly
    and compare wrong.
    """
    lines = path.read_text().splitlines(keepends=True)

    start_idx = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == _SECTION_START_HEADING:
            start_idx = i
            break
    assert start_idx is not None, (
        f"{path}: {_SECTION_START_HEADING!r} heading not found."
    )

    end_idx = None
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break
    assert end_idx is not None, (
        f"{path}: no terminating '## ' heading found after "
        f"{_SECTION_START_HEADING!r} — the section may have been relocated "
        "to end-of-file, which this extraction cannot bound."
    )

    return "".join(lines[start_idx:end_idx])


def _normalize_tokens(block: str, path: Path) -> str:
    """Replace every per-file token with its canonical placeholder.

    Asserts each token is present before replacing — a no-op replace would
    silently skip normalization and produce a false inequality (or a false
    equality, if both files happened to already read identically) at
    comparison time.
    """
    normalized = block
    for token, canonical in _TOKEN_MAP[path].items():
        assert token in normalized, (
            f"{path}: token {token!r} not found in the extracted Reconciliation "
            "block — normalization would be a no-op. The block structure may "
            "have changed."
        )
        normalized = normalized.replace(token, canonical)
    return normalized


class TestReconciliationBlockConsistency:
    """Enforce that code-review/SKILL.md and plan-review/ROUTING.md carry the
    same Reconciliation rule, modulo the two known per-file token pairs."""

    def test_reconciliation_blocks_identical_modulo_known_tokens(self):
        skill_block = _normalize_tokens(
            _extract_reconciliation_block(_SKILL_MD), _SKILL_MD
        )
        routing_block = _normalize_tokens(
            _extract_reconciliation_block(_ROUTING_MD), _ROUTING_MD
        )
        assert skill_block == routing_block, (
            "code-review/SKILL.md and plan-review/ROUTING.md Reconciliation "
            "blocks have diverged beyond the known per-file token pairs "
            "(Implementation-wrong-shape/Design-wrong-shape, "
            "Step 1/Step 4). Update both files to match, or extend "
            "_TOKEN_MAP if a new deliberate per-file difference is introduced."
        )

    def test_reconciliation_block_contains_collapsing_rule_and_discriminator(self):
        """Presence, not only equality — a future edit that deletes the
        collapsing rule, the discriminator, or the co-ownership disclaimer
        from both files symmetrically would still pass byte-equivalence."""
        for path in (_SKILL_MD, _ROUTING_MD):
            block = _extract_reconciliation_block(path)
            assert _COLLAPSING_RULE_MARKER in block, (
                f"{path}: collapsing rule sentence "
                f"({_COLLAPSING_RULE_MARKER!r}) missing from the "
                "Reconciliation block."
            )
            assert _FAILURE_MODE_DISTINCTNESS_MARKER in block.lower(), (
                f"{path}: discriminator's consequence-phrase "
                f"({_FAILURE_MODE_DISTINCTNESS_MARKER!r}) missing from the "
                "Reconciliation block."
            )
            assert _CO_OWNERSHIP_DISCLAIMER_MARKER in block.lower(), (
                f"{path}: co-ownership disclaimer "
                f"({_CO_OWNERSHIP_DISCLAIMER_MARKER!r}) missing from the "
                "Reconciliation block — a symmetric revert to a blanket "
                "co-ownership exclusion would zero escalation across every "
                "prescribed-co-ownership item and pass undetected otherwise."
            )
