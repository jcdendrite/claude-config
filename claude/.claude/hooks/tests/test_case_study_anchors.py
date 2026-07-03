"""Assert that source anchors cited in the worktree-enforcement case study
are still present in both the referenced hook scripts and the doc itself.

Each registered anchor is either:
  - A symbol anchor (function name or flag) — asserts presence ≥ 1 in
    the script, since symbols recur across definition and call sites.
  - A quote anchor (distinctive phrase from a verbatim-quoted string) —
    asserts exactly one occurrence in the script; zero means the string
    was reworded, more than one means the anchor is not distinctive enough.

Both kinds assert presence ≥ 1 in the doc (the citation is still there).

Whitespace is normalized before matching to absorb the terminal
line-wrapping in the case study's code blocks.

Why hooks/tests/:
  This module imports helpers.CLAUDE_DIR from the sibling helpers path.
  It lives in hooks/tests/ to match the co-location of test_doc_counts.py,
  which guards a related class of doc-vs-disk drift.
"""
from __future__ import annotations

from typing import NamedTuple

import pytest
from helpers import CLAUDE_DIR

# CLAUDE_DIR is defined in helpers.py as Path(__file__).resolve().parent.parent,
# anchored to the stow-source path, not the symlink target (~/.claude/).
# Chain: CLAUDE_DIR (claude/.claude/) → .parent (claude/) → .parent (repo root).
REPO_ROOT = CLAUDE_DIR.parent.parent


class CaseStudyAnchor(NamedTuple):
    """A symbol name or distinctive quote-slice cited by the case study."""

    label: str           # stable test ID; never reorder entries (ids= uses this)
    doc_rel_path: str    # path to the doc, relative to REPO_ROOT
    script_rel_path: str # path to the hook script, relative to REPO_ROOT
    anchor_text: str     # the symbol name or distinctive phrase to grep
    kind: str            # "symbol" or "quote"


_DOC = "docs/case-studies/worktree-enforcement.md"
_GIT_WRITES = "claude/.claude/hooks/require-worktree-for-git-writes.sh"
_FILE_WRITES = "claude/.claude/hooks/require-worktree-for-file-writes.sh"

_REGISTERED_ANCHORS: list[CaseStudyAnchor] = [
    # --- git-writes hook: detection pair (each is distinctive in context) ---
    # The cwd_anchor_note_if_chained, command_chains_cd_then_git, and
    # git_C_note_if_present symbol anchors (and their two associated quote
    # anchors below) were removed here when GH-421 deleted those mechanisms
    # from require-worktree-for-git-writes.sh — see the "Superseded (GH-421)"
    # callout in the case study doc, which is the doc-side counterpart of
    # this registry change.
    CaseStudyAnchor(
        label="git-writes:symbol:absolute-git-dir",
        doc_rel_path=_DOC,
        script_rel_path=_GIT_WRITES,
        anchor_text="--absolute-git-dir",
        kind="symbol",
    ),
    CaseStudyAnchor(
        label="git-writes:symbol:git-common-dir",
        doc_rel_path=_DOC,
        script_rel_path=_GIT_WRITES,
        anchor_text="--git-common-dir",
        kind="symbol",
    ),
    # --- git-writes hook: quote-slice anchors ---
    # Each phrase is unique in the script (exactly-one guard fires on reword).
    CaseStudyAnchor(
        label="git-writes:quote:header-race",
        doc_rel_path=_DOC,
        script_rel_path=_GIT_WRITES,
        anchor_text="isolates each session's state",
        kind="quote",
    ),
    # --- file-writes hook: detection pair ---
    CaseStudyAnchor(
        label="file-writes:symbol:absolute-git-dir",
        doc_rel_path=_DOC,
        script_rel_path=_FILE_WRITES,
        anchor_text="--absolute-git-dir",
        kind="symbol",
    ),
    CaseStudyAnchor(
        label="file-writes:symbol:git-common-dir",
        doc_rel_path=_DOC,
        script_rel_path=_FILE_WRITES,
        anchor_text="--git-common-dir",
        kind="symbol",
    ),
    # --- file-writes hook: deny-text quote-slice ---
    CaseStudyAnchor(
        label="file-writes:quote:deny-worktree-path",
        doc_rel_path=_DOC,
        script_rel_path=_FILE_WRITES,
        anchor_text="Write the file at its worktree path instead",
        kind="quote",
    ),
]


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace to a single space and strip.

    Normalizes the line-wrapped quoted blocks in the doc against the
    single-line strings they cite in the hook scripts.
    """
    return " ".join(text.split())


class TestCaseStudyAnchors:
    """Assert that each anchor cited in the worktree-enforcement case study
    still exists in both the referenced hook script and the doc itself.

    For quote-kind anchors, assert exactly one occurrence in the script
    (a reword → 0; accidental duplication → 2+; both signal drift).
    For symbol-kind anchors, assert presence ≥ 1 in the script — function
    names legitimately recur across definition and call sites, so exactly-one
    would be too tight.

    On the doc side, both kinds assert presence ≥ 1 (the anchor is still cited).
    """

    @pytest.mark.parametrize("anchor", _REGISTERED_ANCHORS, ids=lambda a: a.label)
    def test_anchor_present_in_script(self, anchor: CaseStudyAnchor) -> None:
        assert anchor.kind in {"quote", "symbol"}, (
            f"Unrecognised kind {anchor.kind!r} for label {anchor.label!r}. "
            "Valid values are 'quote' (exactly-one guard) and 'symbol' (presence ≥ 1). "
            "A misspelled kind silently falls into the symbol branch and weakens the guard."
        )
        script_text = _normalize_whitespace(
            (REPO_ROOT / anchor.script_rel_path).read_text(encoding="utf-8")
        )
        count = script_text.count(anchor.anchor_text)
        if anchor.kind == "quote":
            assert count == 1, (
                f"{anchor.script_rel_path}: expected exactly one occurrence of "
                f"{anchor.anchor_text!r} (label={anchor.label!r}), got {count}. "
                "Zero means the string was reworded or removed — update the doc "
                "and re-sync this registry entry. More than one means it was "
                "duplicated — tighten the anchor_text to a more distinctive slice."
            )
        else:
            assert count >= 1, (
                f"{anchor.script_rel_path}: symbol {anchor.anchor_text!r} "
                f"(label={anchor.label!r}) not found — it was likely renamed. "
                "Update the symbol name in both the doc and this registry entry."
            )

    @pytest.mark.parametrize("anchor", _REGISTERED_ANCHORS, ids=lambda a: a.label)
    def test_anchor_cited_in_doc(self, anchor: CaseStudyAnchor) -> None:
        # Doc-side check is presence ≥ 1 for both kinds, not exactly-one.
        # Quote anchors use count == 1 on the script side (distinctiveness guard);
        # on the doc side, a phrase may intentionally appear in the body and again
        # in the Sources list, so exactly-one would be too tight.
        # The shared-flag anchors (--absolute-git-dir, --git-common-dir) are registered
        # once per hook script but the doc may cite the flag once for both hooks
        # (shared detection behaviour) — all four doc-side checks pass from one
        # occurrence, which is correct and intentional.
        doc_text = _normalize_whitespace(
            (REPO_ROOT / anchor.doc_rel_path).read_text(encoding="utf-8")
        )
        assert anchor.anchor_text in doc_text, (
            f"{anchor.doc_rel_path}: anchor {anchor.anchor_text!r} "
            f"(label={anchor.label!r}) not found in the case study. "
            "The doc citation was reworded or removed — update the doc to "
            "restore the anchor, or remove this entry from the registry if "
            "the citation is intentionally gone."
        )


class TestNormalizeWhitespace:
    """Unit tests for the _normalize_whitespace helper."""

    def test_single_line_unchanged_except_strip(self) -> None:
        assert _normalize_whitespace("  hello world  ") == "hello world"

    def test_multi_line_collapsed_to_single_space(self) -> None:
        assert _normalize_whitespace("foo\nbar\nbaz") == "foo bar baz"

    def test_mixed_whitespace_collapsed(self) -> None:
        assert _normalize_whitespace("a  \t  b\n\nc") == "a b c"
