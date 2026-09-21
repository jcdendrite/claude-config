"""Tests for check-ledger-citations.py."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "check-ledger-citations.py"
_spec = importlib.util.spec_from_file_location("check_ledger_citations", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

# Two real rows under a level-2 heading, so an `anchors:` line puts the
# section in the ledger region. Row 2 cites row 1.
_CLEAN_LEDGER = (
    "## Approach\n"
    "Row 1 [mechanism]: cache widgets — anchors: root — fewer lookups.\n"
    "Row 2 [assumption]: widgets are immutable [unverified] — anchors: row1\n"
)

# A ledger with one undefined citation at each site: an `anchors:` value
# (line 4), a `row <N>` prose reference (line 5), and a bracketed label (line 6).
_ORPHAN_AT_EACH_SITE = (
    "## Approach\n"
    "Row 1 [mechanism]: cache widgets — anchors: root — fewer lookups.\n"
    "\n"
    "Row 2 [assumption]: eviction is constant time [author-inferred] — anchors: row8\n"
    "Eviction cost is settled, see row 9 for the derivation.\n"
    "The invalidation path is covered by [G7] as well.\n"
)


# The only `anchors:` clause is a quoted example inside a fence, while the
# unfenced prose carries citation-shaped tokens.
_ANCHORS_ONLY_IN_A_FENCE = (
    "## Approach\n"
    "```\n"
    "anchors: root\n"
    "```\n"
    "See row 4 and [G2] for detail.\n"
)


# One unique substring per RESIDUAL_CHECKLIST_ITEMS entry, keyed by a short id.
_RESIDUAL_FRAGMENTS_BY_ID = {
    "definition-not-a-real-row": "the citation and a line to receive it passes",
    "tag-provenance": "provenance",
    "outside-this-file": "outside this file",
    "fenced-citation": "fenced code block",
    "list-position": "list position",
    "other-separator": "separator other than a comma, semicolon, or the word",
    "anchors-key-spelling": "keys not spelled",
    "long-letter-prefix": "four or more letters",
    "hard-wrapped-span": "hard-wrapped across two lines",
    "wrapped-anchors-value": "wraps onto the next line",
    "html-block-fence": "inside an HTML block",
    "escaped-backtick": "escaped backtick",
    "no-anchors-clause": "not scanned at all",
    "non-label-word": "follows a non-label word",
    "punctuation-wrapped": "punctuation-wrapped",
    "spaced-letter-label": "spaced letter label",
    "html-comment-definition": "inside an HTML comment",
    "non-ascii-character": "zero-width character",
}


def _run_cli(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, **kwargs,
    )


class TestNormalizeLabel:
    def test_spaced_and_unspaced_row_forms_are_one_label(self):
        assert _mod.normalize_label("Row 1") == _mod.normalize_label("row1") == "row1"

    def test_row_word_before_a_letter_label_is_dropped(self):
        assert _mod.normalize_label("row G2") == _mod.normalize_label("G2") == "g2"

    def test_alphanumeric_suffix_is_kept(self):
        assert _mod.normalize_label("row12a") == "row12a"

    def test_sentence_final_period_is_stripped(self):
        assert _mod.normalize_label("row12a.") == "row12a"

    def test_bare_number_and_plural_row_word_read_as_a_row(self):
        assert _mod.normalize_label("3") == "row3"
        assert _mod.normalize_label("rows 2") == "row2"

    def test_mismatched_suffixes_are_distinct_labels(self):
        assert _mod.normalize_label("row12a") != _mod.normalize_label("row12b")
        text = "## Approach\nRow 12b [mechanism]: x — anchors: root — y\nRow 13 [assumption]: z — anchors: row12a\n"
        assert ("row12a", 3) in _mod.find_orphan_citations(text)


class TestStripping:
    def test_strip_fenced_blocks_removes_citation_shaped_token_inside_a_fence(self):
        text = "before\n```\nanchors: row99\n```\nafter\n"
        stripped = _mod.strip_fenced_blocks(text)
        assert "row99" not in stripped
        assert "before" in stripped and "after" in stripped

    def test_strip_fenced_blocks_keeps_the_line_count(self):
        text = "one\n```\nanchors: row99\n```\ntwo\n"
        assert _mod.strip_fenced_blocks(text).count("\n") == text.count("\n")

    def test_citation_after_a_stripped_fence_reports_the_files_real_line(self):
        text = _CLEAN_LEDGER + "```\nexample\nlines\n```\nAn orphan, see row 9.\n"
        assert _mod.find_orphan_citations(text) == [("row9", 8)]

    def test_citations_between_and_after_two_closed_fences_are_reported_and_fenced_content_is_not(self):
        text = (
            _CLEAN_LEDGER
            + "```\nanchors: row90\n```\n"
            + "Between the fences, see row 9.\n"
            + "```\nanchors: row91\n```\n"
            + "After both fences, see row 8.\n"
        )
        assert _mod.find_orphan_citations(text) == [("row9", 7), ("row8", 11)]

    def test_unterminated_fence_leaves_following_text_scanned(self):
        text = _CLEAN_LEDGER + "```\nAn orphan, see row 9.\n"
        assert _mod.find_orphan_citations(text) == [("row9", 5)]

    def test_tilde_fence_is_blanked_and_keeps_the_line_count(self):
        text = "before\n~~~\nanchors: row99\n~~~\nafter\n"
        stripped = _mod.strip_fenced_blocks(text)
        assert stripped.splitlines() == ["before", "   ", " " * 14, "   ", "after"]

    @pytest.mark.parametrize(
        ("text", "expected_visible"),
        [
            ("````\nx\n```\ny\n````\nz\n", ["z"]),
            ("```\nx\n`````\nz\n", ["z"]),
            ("```\nx\n~~~\ny\n```\nz\n", ["z"]),
            ("~~~~\nx\n~~~\ny\n~~~~~\nz\n", ["z"]),
            ("~~~\nx\n```\ny\n~~~\nz\n", ["z"]),
        ],
        ids=[
            "shorter-backtick-closer-does-not-close",
            "longer-backtick-closer-closes",
            "tilde-line-does-not-close-a-backtick-fence",
            "shorter-tilde-closer-does-not-close",
            "backtick-line-does-not-close-a-tilde-fence",
        ],
    )
    def test_fence_closes_only_on_its_own_character_at_least_as_long_as_the_opener(
        self, text, expected_visible
    ):
        stripped = _mod.strip_fenced_blocks(text)
        assert [line for line in stripped.splitlines() if line.strip()] == expected_visible

    @pytest.mark.parametrize(
        "opener_line",
        ["```x``` quoted", "``` foo `bar`", "````x`"],
        ids=["closes-on-its-own-line", "info-string-with-backtick", "long-run-info-with-backtick"],
    )
    def test_backtick_line_with_a_backtick_in_its_info_string_opens_no_fence(self, opener_line):
        text = _CLEAN_LEDGER + opener_line + "\nAn orphan, see row 9.\n```\nplain\n```\n"
        assert _mod.find_orphan_citations(text) == [("row9", 5)]

    def test_tilde_fence_may_carry_a_backtick_in_its_info_string(self):
        text = _CLEAN_LEDGER + "~~~ md `x`\nSee row 9.\n~~~\n"
        assert _mod.find_orphan_citations(text) == []

    def test_four_backtick_fence_quoting_a_three_backtick_example_does_not_close_early(self):
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x — anchors: root\n"
            "````md\n"
            "```\n"
            "Row 3 [mechanism]: quoted — anchors: row77\n"
            "```\n"
            "````\n"
            "An orphan, see row 9.\n"
            "```\n"
            "plain\n"
            "```\n"
        )
        assert _mod.collect_defined_labels(text) == {"row1"}
        assert _mod.find_orphan_citations(text) == [("row9", 8)]

    @pytest.mark.parametrize(
        "fenced_example",
        [
            " ```\nanchors: row99\n ```\n",
            "   ```\nanchors: row99\n   ```\n",
            "  ```md\n  anchors: row99\n  ```\n",
            "```\nanchors: row99\n```  \t\n",
            "~~~\nanchors: row99\n  ~~~\n",
        ],
        ids=[
            "one-space-indent",
            "three-space-indent",
            "list-nested-indent-with-info-string",
            "closer-with-trailing-whitespace",
            "indented-tilde-closer",
        ],
    )
    def test_indented_or_space_trailed_fence_is_blanked_and_the_citation_after_it_reports_its_real_line(
        self, fenced_example
    ):
        text = _CLEAN_LEDGER + fenced_example + "An orphan, see row 9.\n"
        assert _mod.find_orphan_citations(text) == [("row9", 7)]

    def test_crlf_fence_is_closed_and_the_citation_after_it_reports_its_real_line(self):
        text = (
            "## Approach\r\n"
            "Row 1 [mechanism]: x — anchors: root\r\n"
            "```\r\n"
            "anchors: row99\r\n"
            "```\r\n"
            "An orphan, see row 9.\r\n"
        )
        assert _mod.find_orphan_citations(text) == [("row9", 6)]

    def test_fence_opened_and_closed_at_four_spaces_is_still_blanked(self):
        """The residual checklist documents this lenient pairing: Markdown
        reads a four-space-indented fence as an indented code block, but this
        script blanks it, so a quoted `anchors:` clause inside it is not a
        citation."""
        text = _CLEAN_LEDGER + "    ```\n    anchors: row99\n    ```\nAn orphan, see row 9.\n"
        assert _mod.find_orphan_citations(text) == [("row9", 7)]

    def test_fence_prefixed_by_a_list_marker_is_not_recognized_as_an_opener(self):
        """Residual: `_fence_opener` needs only leading spaces or tabs before
        a backtick run, so a fence prefixed by a list marker (`- ` + backticks)
        never opens one. The next bare backtick-only line opens a fence in its
        place, and because it is never closed, both `anchors: row99` and the
        later `row 9` stay live rather than quoted."""
        text = _CLEAN_LEDGER + "- ```\nanchors: row99\n```\nSee row 9.\n"
        assert _mod.find_orphan_citations(text) == [("row99", 5), ("row9", 7)]

    def test_fence_opened_inside_a_list_item_stays_open_across_an_outdented_line(self):
        """Residual: Markdown ends a list item, and any block nested inside
        it, at a line that outdents below the item's own continuation
        indentation. This script tracks only the fence character and run
        length, so it keeps blanking past that outdent until it reaches a
        later matching closer."""
        text = (
            _CLEAN_LEDGER
            + "1. See below.\n\n"
            + "   ```\n"
            + "   anchors: row99\n"
            + "outdented, see row 9\n"
            + "   ```\n"
        )
        assert _mod.find_orphan_citations(text) == []

    def test_fence_inside_an_html_block_is_still_blanked(self):
        """Residual: Markdown does not parse nested syntax inside an HTML
        block, so a backtick-fence pair inside one quotes nothing there. This
        script has no HTML-block awareness, so it still pairs the two
        backtick lines as a fence and blanks the citation between them."""
        text = _CLEAN_LEDGER + "<div>\n```\nanchors: row99\n```\n</div>\nSee row 9.\n"
        assert _mod.find_orphan_citations(text) == [("row9", 9)]

    def test_strip_inline_spans_removes_a_bracketed_label_inside_a_span(self):
        stripped = _mod.strip_inline_spans("quoted `[G7]` here")
        assert "[G7]" not in stripped

    def test_strip_inline_spans_keeps_line_numbers_and_length(self):
        text = "first `[G7]` line\nsecond line\nthird `` `row9` `` line\n"
        stripped = _mod.strip_inline_spans(text)
        assert len(stripped) == len(text)
        assert stripped.count("\n") == text.count("\n")
        assert "row9" not in stripped
        assert stripped.splitlines()[1] == "second line"

    def test_strip_inline_spans_closes_a_run_only_on_a_run_of_the_same_length(self):
        stripped = _mod.strip_inline_spans("``a`b`` tail `c`")
        assert stripped == " " * 7 + " tail " + " " * 3

    def test_strip_inline_spans_leaves_an_unclosed_run_as_text(self):
        text = "one ` two ``` three"
        assert _mod.strip_inline_spans(text) == text

    def test_strip_inline_spans_never_crosses_a_line(self):
        text = "`see row 9\nmore`"
        assert _mod.strip_inline_spans(text) == text


class TestSiteDiscrimination:
    """Pins that backticks mean different things at different sites: they
    quote a token at the `row <N>` and bracketed sites, and are a common
    wrapper for a real `anchors:` clause in plan files."""

    def test_backtick_wrapped_anchors_clause_with_undefined_target_is_an_orphan(self):
        text = (
            "## Approach\n"
            "**Row 1** [mechanism]: cache widgets — `anchors: root`\n"
            "**Row 2** [assumption]: eviction is cheap — `anchors: row9`\n"
        )
        assert _mod.find_orphan_citations(text) == [("row9", 3)]

    def test_backtick_wrapped_anchors_clause_with_defined_target_resolves(self):
        text = (
            "## Approach\n"
            "**Row 1** [mechanism]: cache widgets — `anchors: root`\n"
            "**Row 2** [assumption]: eviction is cheap — `anchors: row1`\n"
        )
        assert _mod.find_orphan_citations(text) == []

    @pytest.mark.parametrize(
        "anchors_value",
        ["root, G10", "givens; G10"],
        ids=["after-root-and-comma", "after-givens-and-semicolon"],
    )
    def test_backtick_wrapped_anchors_value_reports_an_undefined_label_after_root_or_givens(
        self, anchors_value
    ):
        text = f"## Approach\n**Row 1** [mechanism]: cache widgets — `anchors: {anchors_value}`\n"
        assert _mod.find_orphan_citations(text) == [("g10", 2)]

    def test_bracketed_label_and_row_prose_inside_inline_spans_are_not_reported(self):
        text = _CLEAN_LEDGER + "The grammar quotes `[G7]` and `see row 9` as examples.\n"
        assert _mod.find_orphan_citations(text) == []


class TestCollectDefinedLabels:
    @pytest.mark.parametrize(
        ("definition_line", "expected_label"),
        [
            ("- **G1** widgets are immutable — beyond reach: upstream owns it", "g1"),
            ("- **G2 — widgets never resize.** beyond reach: upstream owns it", "g2"),
            ("| G3 | widgets are cached | beyond reach |", "g3"),
            ("Row 4 [mechanism]: evict on write — anchors: root — cheap", "row4"),
            ("G5 — widgets are cached in memory", "g5"),
            ("- **row6.** eviction is constant time", "row6"),
            ("### M7 — anchors: G1. Add an eviction hook.", "m7"),
            ("> G1 — a given quoted in a blockquote", "g1"),
            ("__G2__ widgets are cached", "g2"),
            ("+ G3 — widgets are cached", "g3"),
            ("* G4: widgets are cached", "g4"),
            ("G5(widgets are cached)", "g5"),
            ("G6`code` widgets are cached", "g6"),
            ("G7", "g7"),
            ("- **ABC1** widgets are immutable — beyond reach: upstream owns it", "abc1"),
            ("G1 - widgets are cached", "g1"),
            ("G7\r", "g7"),
            ("| A10a | widgets are cached |", "a10a"),
            ("G4a [assumption]: widgets are cached", "g4a"),
        ],
        ids=[
            "bolded-list-item",
            "bolded-with-inner-em-dash",
            "markdown-table-cell",
            "bare-line-leading-grammar-form",
            "plain-letter-label-lead",
            "bolded-label-with-trailing-period",
            "heading-lead",
            "blockquote-marker",
            "underscore-bold",
            "plus-list-marker",
            "star-list-marker-with-colon-boundary",
            "open-paren-boundary",
            "backtick-boundary",
            "end-of-line-boundary",
            "three-letter-label",
            "hyphen-boundary",
            "carriage-return-end-of-line-boundary",
            "suffixed-letter-label-in-a-table-cell",
            "suffixed-letter-label-with-a-tag",
        ],
    )
    def test_each_written_definition_shape_is_collected(self, definition_line, expected_label):
        text = f"## Ledger\n{definition_line}\nRow 9 [mechanism]: x — anchors: root — y\n"
        assert expected_label in _mod.collect_defined_labels(text)

    @pytest.mark.parametrize(
        "non_definition_line",
        [
            "x86 - note about the widget",
            "v2: text about the widget",
            "G1foo is not a label",
            "See G4: the widget",
            "- **ABCD5** four letters are no label",
            "1. **Widgets are immutable**: upstream owns it",
        ],
        ids=[
            "lowercase-token-with-hyphen-boundary",
            "lowercase-token-with-colon-boundary",
            "identifier-longer-than-the-label",
            "label-not-at-the-line-start",
            "four-letter-prefix",
            "ordinal-list-item",
        ],
    )
    def test_line_that_only_resembles_a_label_defines_nothing(self, non_definition_line):
        text = f"## Ledger\n{non_definition_line}\nRow 9 [mechanism]: x — anchors: root — y\n"
        assert _mod.collect_defined_labels(text) == {"row9"}

    def test_label_defined_below_a_level_three_heading_stays_in_the_ledger_region(self):
        text = "## Approach\nRow 1 [mechanism]: x — anchors: root\n### Notes\n- **G9** a given\n"
        assert "g9" in _mod.collect_defined_labels(text)

    def test_a_row_written_only_inside_a_fence_defines_nothing(self):
        text = "## Approach\nRow 1 [mechanism]: x — anchors: root\n```\nRow 5 [mechanism]: quoted\n```\n"
        assert _mod.collect_defined_labels(text) == {"row1"}

    def test_an_anchors_line_only_inside_a_fence_does_not_make_a_ledger_region(self):
        text = "## Approach\n```\nanchors: root\n```\nRow 1 [mechanism]: x\n"
        assert _mod.collect_defined_labels(text) == set()

    def test_a_heading_shaped_line_inside_a_fence_does_not_fragment_the_ledger_region(self):
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x\n"
            "```\n"
            "## Quoted heading\n"
            "```\n"
            "Row 2 [assumption]: y — anchors: row1\n"
        )
        assert _mod.collect_defined_labels(text) == {"row1", "row2"}

    def test_three_letter_label_can_be_both_defined_and_cited(self):
        text = "## Approach\n- **ABC1** a given — anchors: root\nRow 2 [assumption]: x — anchors: ABC1\n"
        assert _mod.find_orphan_citations(text) == []

    def test_label_defined_outside_the_ledger_region_is_not_collected(self):
        text = (
            "## Approach\n"
            "- **C6** real ledger row — anchors: root\n"
            "\n"
            "## Critical files\n"
            "- **C7**: `widgets.py:10`, an unrelated audit finding\n"
        )
        defined = _mod.collect_defined_labels(text)
        assert "c6" in defined
        assert "c7" not in defined

    def test_a_fabricated_citation_to_an_out_of_region_label_is_an_orphan(self):
        text = (
            "## Approach\n"
            "- **C6** real ledger row — anchors: root\n"
            "- **C8** another row — anchors: c7\n"
            "\n"
            "## Critical files\n"
            "- **C7**: `widgets.py:10`, an unrelated audit finding\n"
        )
        assert _mod.find_orphan_citations(text) == [("c7", 3)]

    def test_a_line_that_merely_looks_like_a_definition_within_a_ledger_region_still_resolves_as_a_residual(
        self,
    ):
        """Residual (see RESIDUAL_CHECKLIST_ITEMS's first entry): any line
        inside a section carrying an `anchors:` clause defines a label if it
        starts with one, so an author who writes both a fabricated citation
        and a bare line shaped like its definition defeats this check."""
        text = (
            "## Rows\n"
            "\n"
            "Row 1 [mechanism]: real content — anchors: root\n"
            "\n"
            "- S103: an unrelated ruff finding noted for context, not a ledger row\n"
            "\n"
            "## Later section\n"
            "\n"
            "This fabricated inference is cited as [S103] and should be an orphan.\n"
        )
        assert "s103" in _mod.collect_defined_labels(text)
        assert _mod.find_orphan_citations(text) == []

    def test_a_bare_entry_contiguous_within_a_real_givens_list_is_the_same_disclosed_residual(self):
        """Residual, same class as above from a different angle: a fabricated
        entry inserted directly within a real `Givens:` list still qualifies,
        since the line itself -- not its position relative to a marker -- is
        what this script reads."""
        text = (
            "## Approach\n"
            "Givens:\n"
            "- G1: widgets are immutable\n"
            "- S103: an unrelated ruff finding, indistinguishable in position "
            "from a real given\n"
            "Row 1 [mechanism]: x — anchors: G1\n"
        )
        assert "s103" in _mod.collect_defined_labels(text)

    def test_a_bare_entry_with_its_own_fabricated_anchors_clause_is_the_same_disclosed_residual(self):
        """Residual, same class again: a bare entry carrying a fabricated
        `anchors:` clause of its own is collected exactly as a real row is."""
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x — anchors: root\n"
            "- S103: an unrelated ruff finding — anchors: root\n"
        )
        assert "s103" in _mod.collect_defined_labels(text)

    def test_bare_givens_block_defines_its_bare_g_labels(self):
        """Regression pin: a bolded `**Givens:**` block's bare G-labels are
        real corpus content (this exact shape -- a bolded marker with
        unbolded label rows -- is the more common form across committed
        plans), even though a naive per-line filter would exclude bare,
        unbolded label lines -- section-scoping alone must keep collecting
        them."""
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x — anchors: root\n"
            "\n"
            "**Givens:**\n"
            "- G1: widgets are cached in memory\n"
            "\n"
            "Row 2 [assumption]: y — anchors: G1\n"
        )
        assert "g1" in _mod.collect_defined_labels(text)
        assert _mod.find_orphan_citations(text) == []

    def test_bare_mechanism_row_with_its_own_anchors_clause_resolves(self):
        """Regression pin: a bare mechanism/assumption row carrying its own
        `anchors:` clause (the exact shape a real corpus plan uses -- see
        `relocate-global-claude-md.md`'s M1-M4) is real content that a naive
        per-line filter would exclude -- section-scoping alone must keep
        collecting it."""
        text = (
            "## Approach\n"
            "- M1: evict widgets on write, not on read — anchors: row4, row5\n"
            "Row 4 [mechanism]: x — anchors: root\n"
            "Row 5 [assumption]: y — anchors: row4\n"
            "\n"
            "## Later section\n"
            "This cites [M1] again.\n"
        )
        assert "m1" in _mod.collect_defined_labels(text)
        assert _mod.find_orphan_citations(text) == []

    def test_anchors_line_before_the_first_heading_makes_the_preamble_a_ledger_region(self):
        text = "Row 1 [mechanism]: cache widgets — anchors: root — y\n\n## Later\nprose\n"
        assert _mod.collect_defined_labels(text) == {"row1"}

    def test_ordinal_list_ledger_defines_no_row_label(self):
        text = (
            "## Approach\n"
            "### Assumption ledger\n"
            "1. **Widgets are immutable**: upstream owns it — anchors: root\n"
            "2. **Eviction is cheap**: measured — anchors: row1\n"
        )
        assert _mod.collect_defined_labels(text) == set()
        assert _mod.find_orphan_citations(text) == [("row1", 4)]

    def test_ordinal_prefix_before_a_written_row_label_defines_nothing_and_orphans_every_row(self):
        text = (
            "## Approach\n"
            "1. **Row 1** [mechanism]: cache widgets — anchors: root\n"
            "2. **Row 2** [assumption]: eviction is cheap — anchors: row1\n"
        )
        assert _mod.collect_defined_labels(text) == set()
        assert _mod.find_orphan_citations(text) == [("row1", 2), ("row1", 3), ("row2", 3)]

    def test_plan_without_an_anchors_line_defines_nothing(self):
        assert _mod.collect_defined_labels("## Approach\nRow 1 [mechanism]: x\n") == set()

    def test_definition_line_inside_an_html_comment_still_counts_as_defined(self):
        """Residual: `_DEFINITION_LINE_RE` has no HTML-comment awareness, so a
        definition line with no comment delimiters on it is collected even
        though it sits between an HTML comment's open and close tags, which
        Markdown does not render."""
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x — anchors: root\n"
            "<!--\n"
            "G9 — a given, commented out\n"
            "-->\n"
        )
        assert "g9" in _mod.collect_defined_labels(text)


class TestFindCitations:
    def test_all_three_sites_resolve_to_a_normalized_token(self):
        citations = _mod.find_citations(_ORPHAN_AT_EACH_SITE)
        assert ("row8", 4) in citations  # site (a): anchors value
        assert ("row9", 5) in citations  # site (b): row <N> prose
        assert ("g7", 6) in citations  # site (c): bracketed label

    def test_root_and_givens_are_skipped_without_ending_the_anchors_value(self):
        text = "## Approach\nEvery claim — anchors: root, givens, G10 — y\n"
        assert _mod.find_citations(text) == [("g10", 2)]

    @pytest.mark.parametrize(
        "anchors_value",
        ["row1, G4", "row1; G4", "row1 and G4", "row1, and G4", "row1 ,G4"],
        ids=["comma", "semicolon", "the-word-and", "comma-then-and", "space-before-comma"],
    )
    def test_anchors_value_continues_across_each_separator(self, anchors_value):
        text = f"## Approach\nEvery claim — anchors: {anchors_value} — y\n"
        assert _mod.find_citations(text) == [("g4", 2), ("row1", 2)]

    @pytest.mark.parametrize(
        "anchors_clause",
        ["Anchors: row1, G4", "ANCHORS: row1, G4", "anchors: row1 AND G4", "anchors: row1, And G4"],
        ids=["capitalized-key", "uppercase-key", "uppercase-and", "capitalized-and-after-comma"],
    )
    def test_anchors_key_and_and_separator_match_case_insensitively(self, anchors_clause):
        text = f"## Approach\nEvery claim — {anchors_clause} — y\n"
        assert _mod.find_citations(text) == [("g4", 2), ("row1", 2)]

    @pytest.mark.parametrize(
        ("clause", "expected_tokens"),
        [
            ("anchors: row1 — G9", ["row1"]),
            ("anchors: row1. G9", ["row1"]),
            ("`anchors: row1` G9", ["row1"]),
            ("anchors: rows 1, 2] G9", ["row1", "row2"]),
            ("anchors: value G9", []),
        ],
        ids=["em-dash", "sentence-final-period", "closing-backtick", "closing-bracket", "non-label-word"],
    )
    def test_label_shaped_token_after_a_stop_is_not_a_citation(self, clause, expected_tokens):
        text = f"## Approach\nEvery claim — {clause}\n"
        assert sorted(token for token, _ in _mod.find_citations(text)) == expected_tokens

    def test_anchors_value_does_not_continue_across_a_word_that_merely_starts_with_and(self):
        text = "## Approach\nEvery claim — anchors: row1 andG4 — y\n"
        assert _mod.find_citations(text) == [("row1", 2)]

    @pytest.mark.parametrize(
        "anchors_value",
        ["row1, andG4", "row1; andrew1"],
        ids=["comma-then-and-prefixed-label", "semicolon-then-and-prefixed-word"],
    )
    def test_anchors_value_does_not_continue_across_a_separator_before_a_word_that_starts_with_and(
        self, anchors_value
    ):
        text = f"## Approach\nEvery claim — anchors: {anchors_value}\n"
        assert _mod.find_citations(text) == [("row1", 2)]

    def test_bare_number_after_and_continues_the_anchors_value_as_a_row(self):
        text = "## Approach\nEvery claim — anchors: G4 and 2 others\n"
        assert _mod.find_citations(text) == [("g4", 2), ("row2", 2)]

    def test_row_inside_a_longer_word_is_not_a_row_citation(self):
        text = _CLEAN_LEDGER + "Then throw 3 widgets away.\n"
        assert _mod.find_orphan_citations(text) == []

    def test_anchors_value_stops_at_a_slash_or_a_paren(self):
        text = "## Approach\nEvery claim — anchors: row1/G4 (G5)\n"
        assert _mod.find_citations(text) == [("row1", 2)]

    def test_three_letter_label_in_an_anchors_value_is_a_citation(self):
        text = "## Approach\nEvery claim — anchors: ABC1 — y\n"
        assert _mod.find_citations(text) == [("abc1", 2)]

    def test_four_letter_prefix_is_not_a_citation_at_any_site(self):
        text = "## Approach\nEvery claim — anchors: ABCD5 — see [ABCD5]\n"
        assert _mod.find_citations(text) == []

    def test_bracketed_label_may_carry_one_space_between_letters_and_digits(self):
        text = _CLEAN_LEDGER + "The invalidation path is covered by [G 7] as well.\n"
        assert ("g7", 4) in _mod.find_citations(text)

    def test_anchors_value_truncated_by_label_end_yields_no_shorter_label(self):
        text = "## Approach\nEvery claim — anchors: row1foo, row12ab — y\n"
        assert _mod.find_citations(text) == []

    def test_spaced_letter_label_in_an_anchors_value_is_not_a_citation(self):
        """Residual: a spaced letter label (`G 9`) is only read at the
        bracketed-citation site, not in an `anchors:` value."""
        text = "## Approach\nEvery claim — anchors: G 9 — y\n"
        assert _mod.find_citations(text) == []

    @pytest.mark.parametrize(
        "anchors_clause",
        ["anchors: `G9`", "anchors: (G9)"],
        ids=["backtick-wrapped-label", "paren-wrapped-label"],
    )
    def test_punctuation_wrapped_label_in_an_anchors_value_is_not_a_citation(self, anchors_clause):
        """Residual: `_ANCHOR_TARGET_RE` matches a label starting right after
        the `anchors:` key, so a label wrapped in punctuation there (`` `G9` ``,
        `(G9)`) is not read as a citation."""
        text = f"## Approach\nEvery claim — {anchors_clause}\n"
        assert _mod.find_citations(text) == []

    @pytest.mark.parametrize(
        "anchors_clause",
        ["**anchors:** G9", "anchors : G9"],
        ids=["bolded-key", "space-before-colon"],
    )
    def test_anchors_key_not_spelled_anchors_colon_yields_no_citation(self, anchors_clause):
        """Residual: only `anchors:` directly before the value is read as the
        key -- a bolded key or a space before the colon yields no citation."""
        text = f"## Approach\nEvery claim — {anchors_clause} — y\n"
        assert _mod.find_citations(text) == []

    def test_anchors_value_wrapped_onto_the_next_line_is_not_a_citation(self):
        """Residual: the value must follow the key on the same line -- a
        wrapped continuation is never read."""
        text = "## Approach\nEvery claim — anchors:\nG9\n"
        assert _mod.find_citations(text) == []

    @pytest.mark.parametrize(
        "citation_text",
        ["[G 7]", "[А7]"],
        ids=["non-breaking-space-between-letter-and-digit", "cyrillic-homoglyph-letter-prefix"],
    )
    def test_non_ascii_character_inside_a_bracketed_citation_is_not_read(self, citation_text):
        """Residual: non-ASCII whitespace (here NBSP, not the ASCII space the
        bracketed-citation site tolerates) or a homoglyph letter inside a
        citation is not recognized, so the citation itself is not checked."""
        text = _CLEAN_LEDGER + f"The invalidation path is covered by {citation_text} as well.\n"
        assert _mod.find_citations(text) == [("row1", 2), ("row1", 3), ("row2", 3)]

    def test_escaped_backtick_pairs_with_a_later_span_and_hides_a_citation(self):
        """Residual: `_strip_inline_spans_in_line` has no backslash-escape
        awareness, so a backslash-escaped backtick still opens a span, and it
        can pair with a later, unescaped backtick and blank a real citation
        between them."""
        text = _CLEAN_LEDGER + "A quoted literal \\` here and see row 9 for detail` more text.\n"
        assert _mod.find_citations(text) == [("row1", 2), ("row1", 3), ("row2", 3)]

    def test_row_citation_inside_a_hard_wrapped_inline_span_is_read_as_live(self):
        """Residual: Markdown reads a code span's opener and closer as one
        span even when they sit on different lines, but `strip_inline_spans`
        never crosses a line, so an opener with no closer on its own line
        leaves the next line's content unquoted and its citation live."""
        text = _CLEAN_LEDGER + "`a code span that wraps\nonto a second line, citing row 9 here`\n"
        assert _mod.find_citations(text) == [("row1", 2), ("row1", 3), ("row2", 3), ("row9", 5)]

    def test_plural_row_word_with_bare_numbers_yields_each_label(self):
        text = "## Approach\nRow 3 [mechanism]: x — anchors: rows 1, 2]\n"
        assert {token for token, _ in _mod.find_citations(text)} >= {"row1", "row2"}

    def test_anchors_clause_matching_two_sites_is_returned_once(self):
        text = "## Approach\nRow 3 [mechanism]: x — anchors: row2 — y\n"
        assert _mod.find_citations(text).count(("row2", 2)) == 1

    def test_anchors_value_stops_at_a_non_label_word(self):
        text = "## Approach\nEvery row's anchors: value is either root or a lower row.\n"
        assert _mod.find_citations(text) == []

    def test_anchors_value_stops_at_the_end_of_the_line(self):
        text = "## Approach\nRow 3 [mechanism]: x — anchors: row1\nG9\n"
        assert {token for token, _ in _mod.find_citations(text)} == {"row1", "row3"}

    def test_anchors_value_stops_at_the_end_of_the_line_after_a_trailing_separator(self):
        text = "## Approach\nEvery claim — anchors: row1,\nG9\n"
        assert _mod.find_citations(text) == [("row1", 2)]

    def test_sentence_final_period_after_a_suffixed_label_is_not_part_of_it(self):
        text = "## Approach\nRow 3 [mechanism]: x — anchors: row12a.\n"
        assert ("row12a", 2) in _mod.find_citations(text)

    def test_anchors_clause_inside_a_fenced_block_is_not_a_citation(self):
        text = "## Approach\nRow 1 [mechanism]: x — anchors: root\n```\nanchors: row99\n```\n"
        assert _mod.find_citations(text) == [("row1", 2)]

    def test_external_lint_and_checklist_ids_in_prose_are_not_citations(self):
        """Regression pin: S103, SC1, and B5 are external lint codes and
        checklist IDs, not ledger labels, and must never be read as citations."""
        text = _CLEAN_LEDGER + "Ruff S103 and shellcheck SC1 and checklist B5 apply here.\n"
        assert {token for token, _ in _mod.find_citations(text)} <= {"row1", "row2"}

    def test_plan_without_an_anchors_line_cites_nothing(self):
        assert _mod.find_citations("Some prose, see row 4 and [G2].\n") == []

    def test_plan_whose_only_anchors_clause_is_inside_a_fence_cites_nothing(self):
        assert _mod.find_citations(_ANCHORS_ONLY_IN_A_FENCE) == []
        assert _mod.find_orphan_citations(_ANCHORS_ONLY_IN_A_FENCE) == []


@pytest.mark.timing
class TestBoundedRuntime:
    """Each input below is a shape that makes a backtracking regex superlinear
    in the line length. The script runs in a child process with its own
    timeout, so a superlinear regression fails in seconds instead of stalling
    the suite. The bound is far above a linear pass and far below the
    seconds-to-minutes a cubic or quadratic pass takes at these sizes."""

    _RUNTIME_BOUND_SECONDS = 5
    _LONG_LINE_LENGTH = 4000
    _DISTINCT_RUN_COUNT = 1100

    def _run_within_bound(self, tmp_path, ledger_text: str) -> subprocess.CompletedProcess:
        plan = tmp_path / "widget-plan.md"
        plan.write_text(ledger_text)
        try:
            return _run_cli(str(plan), timeout=self._RUNTIME_BOUND_SECONDS)
        except subprocess.TimeoutExpired:
            pytest.fail(f"script exceeded {self._RUNTIME_BOUND_SECONDS}s on a long adversarial line")

    @pytest.mark.parametrize(
        "line_shape",
        ["{ws}x", "> {ws}x", "- {ws}x", "# {ws}x", "| {ws}x", "**{ws}x", "row{ws}x", "G1{ws}x"],
        ids=[
            "leading-whitespace-then-text",
            "blockquote-marker-then-whitespace",
            "list-marker-then-whitespace",
            "heading-marker-then-whitespace",
            "table-pipe-then-whitespace",
            "bold-marker-then-whitespace",
            "row-word-then-whitespace",
            "label-then-whitespace-then-text",
        ],
    )
    def test_unfenced_long_whitespace_line_is_checked_in_linear_time(self, tmp_path, line_shape):
        long_line = line_shape.format(ws=" " * self._LONG_LINE_LENGTH)
        result = self._run_within_bound(tmp_path, _CLEAN_LEDGER + long_line + "\n")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_long_lines_inside_a_fence_are_checked_in_linear_time(self, tmp_path):
        long_line = "x" * self._LONG_LINE_LENGTH
        blank_line = " " * self._LONG_LINE_LENGTH
        result = self._run_within_bound(
            tmp_path, _CLEAN_LEDGER + f"```\n{long_line}\n{blank_line}\n \t \n```\n"
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_line_of_distinct_length_backtick_runs_is_checked_in_linear_time(self, tmp_path):
        distinct_backtick_runs = "".join("`" * length + "x" for length in range(1, self._DISTINCT_RUN_COUNT))
        result = self._run_within_bound(tmp_path, _CLEAN_LEDGER + distinct_backtick_runs + "\n")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_anchors_value_with_many_separators_is_checked_in_linear_time(self, tmp_path):
        """`_anchor_targets`'s own while loop runs once per token in an
        `anchors:` value, distinct from the line-prefix and backtick-run
        cases above, whose iteration count scales with the line length
        rather than the value's own separator count. `root` keeps every
        token resolving, so this pins runtime rather than a citation count."""
        many_tokens = ", ".join(["root"] * self._DISTINCT_RUN_COUNT)
        result = self._run_within_bound(tmp_path, _CLEAN_LEDGER + f"anchors: {many_tokens}\n")
        assert result.returncode == 0, result.stdout + result.stderr


class TestFindOrphanCitations:
    def test_anchors_value_citing_an_undefined_row_names_the_token(self):
        assert ("row8", 4) in _mod.find_orphan_citations(_ORPHAN_AT_EACH_SITE)

    def test_prose_and_bracketed_orphans_are_both_reported(self):
        orphans = _mod.find_orphan_citations(_ORPHAN_AT_EACH_SITE)
        assert ("row9", 5) in orphans
        assert ("g7", 6) in orphans

    def test_citation_between_two_sparse_defined_labels_is_an_orphan(self):
        """Resolution is set membership: a range check would accept row3
        because row1 and row5 are both defined."""
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x — anchors: root — y\n"
            "Row 5 [mechanism]: x — anchors: row1 — y\n"
            "Row 6 [assumption]: x — anchors: row3\n"
        )
        assert _mod.find_orphan_citations(text) == [("row3", 4)]

    def test_clean_ledger_with_every_resolvable_form_has_no_orphans(self):
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x — anchors: root — y\n"
            "- **G2** a given\n"
            "Row 2 [assumption]: x — anchors: G2\n"
            "Row 3 [assumption]: x — anchors: rows 1, 2\n"
            "Row 4 [assumption]: x — anchors: row 3 — see row G2 and [G2]\n"
        )
        assert _mod.find_orphan_citations(text) == []

    def test_defined_and_cited_suffixed_label_resolves(self):
        text = (
            "## Approach\n"
            "Row 4a [mechanism]: x — anchors: root\n"
            "Row 5 [assumption]: y — anchors: row4a\n"
        )
        assert _mod.collect_defined_labels(text) == {"row4a", "row5"}
        assert _mod.find_orphan_citations(text) == []

    def test_defined_and_cited_suffixed_letter_label_resolves(self):
        text = (
            "## Ledger\n"
            "| A10a | widgets are cached |\n"
            "G4a [assumption]: widgets are immutable\n"
            "Row 1 [mechanism]: x — anchors: A10a, G4a\n"
        )
        assert _mod.collect_defined_labels(text) == {"a10a", "g4a", "row1"}
        assert _mod.find_orphan_citations(text) == []

    def test_undefined_suffixed_letter_label_in_an_anchors_value_is_an_orphan(self):
        text = "## Ledger\n| A10 | widgets are cached |\nRow 1 [mechanism]: x — anchors: A10a\n"
        assert _mod.find_orphan_citations(text) == [("a10a", 3)]

    def test_undefined_suffixed_prose_citation_is_an_orphan(self):
        assert _mod.find_orphan_citations(_CLEAN_LEDGER + "See row 4a for detail.\n") == [("row4a", 4)]

    def test_undefined_row_letter_label_in_an_anchors_value_is_an_orphan(self):
        text = _CLEAN_LEDGER + "Row 3 [assumption]: x — anchors: row G9\n"
        assert _mod.find_orphan_citations(text) == [("g9", 4)]

    def test_undefined_row_letter_label_in_prose_is_an_orphan(self):
        assert _mod.find_orphan_citations(_CLEAN_LEDGER + "See row G9 for detail.\n") == [("g9", 4)]

    def test_citation_in_a_later_section_without_an_anchors_line_is_scanned_and_orphaned(self):
        text = _CLEAN_LEDGER + "\n## Critical files\nSee row 4 for the derivation.\n"
        assert _mod.find_orphan_citations(text) == [("row4", 6)]

    def test_clean_ledger_returns_no_orphans(self):
        assert _mod.find_orphan_citations(_CLEAN_LEDGER) == []

    def test_citation_to_a_row_defined_before_a_fenced_heading_shaped_line_resolves(self):
        """A heading-shaped line quoted inside a fence (e.g. a plan citing
        another doc's excerpt) must not split the section in two -- a false
        split would put Row 1's definition on the anchors-line-less side and
        report this citation as an orphan even though Row 1 is defined."""
        text = (
            "## Approach\n"
            "Row 1 [mechanism]: x\n"
            "```\n"
            "## Quoted heading\n"
            "```\n"
            "Row 2 [assumption]: y — anchors: row1\n"
        )
        assert _mod.find_orphan_citations(text) == []


class TestCli:
    """Subprocess-level wiring: argv handling, exit codes, and the printout.
    The parsing logic is covered above at the unit level."""

    def test_orphan_at_each_site_exits_1_with_one_fail_line_per_orphan(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_ORPHAN_AT_EACH_SITE)
        result = _run_cli(str(plan))
        assert result.returncode == 1, result.stdout + result.stderr
        for line, token in ((4, "row8"), (5, "row9"), (6, "g7")):
            assert (
                f"FAIL: {plan}:{line}: citation '{token}' resolves to no defined ledger label"
            ) in result.stdout.splitlines()
        assert "Defined labels: givens, root, row1, row2" in result.stdout.splitlines()

    _HOSTILE_PLAN_NAME = "x\nPASS: every ledger citation resolves\x1b]0;t\x07.md"

    def test_hostile_plan_filename_is_rendered_inert_and_the_exit_code_stays_the_verdict(self, tmp_path):
        """The plan path comes from a contributor's PR, so a newline or escape
        in its name must not forge a second output line or reach the terminal
        raw."""
        plan = tmp_path / self._HOSTILE_PLAN_NAME
        plan.write_text(_ORPHAN_AT_EACH_SITE)

        result = _run_cli(str(plan))

        assert result.returncode == 1, result.stdout + result.stderr
        assert (result.stdout + result.stderr).isascii()
        assert all(char >= " " or char == "\n" for char in result.stdout + result.stderr)
        assert not [line for line in result.stdout.splitlines() if line.startswith("PASS:")]
        assert "x\\nPASS: every ledger citation resolves\\x1b]0;t\\x07.md:" in result.stdout

    def test_hostile_missing_plan_filename_is_rendered_inert_on_stderr(self, tmp_path):
        result = _run_cli(str(tmp_path / self._HOSTILE_PLAN_NAME))

        assert result.returncode == 2
        assert result.stderr.isascii()
        assert all(char >= " " or char == "\n" for char in result.stderr)
        assert len(result.stderr.splitlines()) == 1

    def test_hostile_leading_dash_argument_is_rendered_inert_in_the_unknown_flag_message(self):
        result = _run_cli("-x\nPASS: forged\x1b]0;t\x07\xe9")

        assert result.returncode == 2
        assert result.stderr.isascii()
        assert all(char >= " " or char == "\n" for char in result.stderr)
        assert len(result.stderr.splitlines()) == 1
        assert "-x\\nPASS: forged\\x1b]0;t\\x07\\xe9" in result.stderr

    @pytest.mark.parametrize(
        "exit_2_arm",
        ["cannot-read", "not-valid-utf8", "unexpected-exception"],
    )
    def test_hostile_plan_filename_is_rendered_inert_at_each_exit_2_arm(
        self, tmp_path, monkeypatch, capsys, exit_2_arm
    ):
        # OSError's own message repr()s the name, which leaves a printable non-ASCII
        # character raw, so the cannot-read arm's name carries none.
        plan_name = "x\nPASS: forged\x1b]0;t\x07" + ("" if exit_2_arm == "cannot-read" else "\xe9") + ".md"
        plan = tmp_path / plan_name
        if exit_2_arm == "cannot-read":
            plan.mkdir()
        elif exit_2_arm == "not-valid-utf8":
            plan.write_bytes(b"\xff\xfe\x00 not valid utf-8")
        else:
            plan.write_text(_CLEAN_LEDGER)

            def fail_unexpectedly(_text):
                raise RuntimeError("simulated defect")

            monkeypatch.setattr(_mod, "collect_defined_labels", fail_unexpectedly)

        assert _mod.main([_SCRIPT.name, str(plan)]) == 2

        stderr = capsys.readouterr().err
        assert stderr.isascii()
        assert all(char >= " " or char == "\n" for char in stderr)
        assert len(stderr.splitlines()) == 1
        assert "x\\nPASS: forged\\x1b]0;t\\x07" in stderr

    def test_fail_output_prints_every_not_checked_item(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_ORPHAN_AT_EACH_SITE)
        stdout = _run_cli(str(plan)).stdout
        assert "Not checked by this script" in stdout
        for item in _mod.RESIDUAL_CHECKLIST_ITEMS:
            assert f"  - {item}" in stdout.splitlines()

    def test_clean_ledger_exits_0_with_the_pass_line_and_the_not_checked_block(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        result = _run_cli(str(plan))
        assert result.returncode == 0, result.stdout + result.stderr
        pass_line = result.stdout.splitlines()[0]
        # Three citations: each row's own `Row N` prose plus row 2's `anchors: row1`.
        assert pass_line == "PASS: every ledger citation resolves (2 labels defined, 3 citations checked)"
        assert "Not checked by this script" in result.stdout

    def test_plan_without_an_anchors_line_exits_0(self, tmp_path):
        plan = tmp_path / "no-ledger-plan.md"
        plan.write_text("# Widget plan\n\nSee row 4 and [G2] for detail.\n")
        result = _run_cli(str(plan))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout

    def test_plan_whose_only_anchors_clause_is_inside_a_fence_exits_0(self, tmp_path):
        plan = tmp_path / "fence-only-anchors-plan.md"
        plan.write_text(_ANCHORS_ONLY_IN_A_FENCE)
        result = _run_cli(str(plan))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout

    def test_quiet_prints_nothing_on_a_pass(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        result = _run_cli("--quiet", str(plan))
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_quiet_leaves_failure_output_unchanged(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_ORPHAN_AT_EACH_SITE)
        loud = _run_cli(str(plan))
        quiet = _run_cli("--quiet", str(plan))
        assert quiet.returncode == 1
        assert quiet.stdout == loud.stdout

    def test_exits_2_for_no_arguments(self):
        result = _run_cli()
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_exits_2_for_extra_arguments(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        result = _run_cli(str(plan), str(plan))
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_exits_2_for_a_trailing_quiet_flag(self, tmp_path):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        assert _run_cli(str(plan), "--quiet").returncode == 2

    def test_exits_2_for_an_unknown_flag(self, tmp_path):
        result = _run_cli("--verbose")
        assert result.returncode == 2
        assert "unknown flag" in result.stderr.lower()
        assert "--verbose" in result.stderr

    def test_exits_2_for_a_missing_file(self, tmp_path):
        missing = tmp_path / "does-not-exist-plan.md"
        result = _run_cli(str(missing))
        assert result.returncode == 2
        assert "no such file" in result.stderr.lower()
        assert str(missing) in result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_exits_2_for_an_unreadable_file(self, tmp_path):
        plan = tmp_path / "unreadable-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        plan.chmod(0o000)
        try:
            result = _run_cli(str(plan))
        finally:
            plan.chmod(0o644)
        assert result.returncode == 2
        assert "cannot read" in result.stderr.lower()

    def test_exits_2_for_a_path_that_is_a_directory(self, tmp_path):
        """Reading a directory fails for every user, root included, so this pins
        the `OSError` arm without depending on file permission bits."""
        result = _run_cli(str(tmp_path))
        assert result.returncode == 2
        assert "cannot read" in result.stderr.lower()
        assert str(tmp_path) in result.stderr

    def test_exits_2_for_a_symlink_to_a_device_node(self, tmp_path):
        """A plan committed as a symlink to a device node (git tracks symlinks)
        must be rejected before read_bytes() ever runs: a symlink to a
        memory-mapped device like /dev/zero would otherwise grow the read
        unboundedly and raise MemoryError, which isn't an OSError subclass and
        so isn't caught -- an uncaught traceback with exit code 1, colliding
        with the documented "1 = orphan found" contract. /dev/null is safe to
        point at here since reading it returns EOF immediately; the point is
        that the guard trips before any read is attempted, not the target's size."""
        plan = tmp_path / "device-symlink-plan.md"
        plan.symlink_to("/dev/null")
        result = _run_cli(str(plan))
        assert result.returncode == 2
        assert "not a regular file" in result.stderr.lower()
        assert str(plan) in result.stderr
        assert len(result.stderr.splitlines()) == 1

    def test_exits_2_for_a_symlink_to_a_fifo(self, tmp_path):
        """The guard must reject a non-regular target generally, not only a
        character device: a symlink to a FIFO with no writer would otherwise
        hang read_bytes() indefinitely rather than raising, an uncaught-hang
        DoS distinct from the device-node case's uncaught MemoryError."""
        fifo_path = tmp_path / "a-fifo"
        os.mkfifo(fifo_path)
        plan = tmp_path / "fifo-symlink-plan.md"
        plan.symlink_to(fifo_path)
        result = _run_cli(str(plan))
        assert result.returncode == 2
        assert "not a regular file" in result.stderr.lower()
        assert str(plan) in result.stderr

    def test_exits_2_for_a_symlink_loop(self, tmp_path):
        """A symlink cycle (a -> b -> a) is another non-regular target
        is_file()/is_dir() must reject before read_bytes() ever runs, rather
        than propagating the OSError that resolving the cycle would raise.
        exists() swallows that ELOOP OSError and returns False, so this
        buckets with "no such file" rather than "not a regular file"
        (the bucket device/FIFO symlinks fall into, since exists() reports
        True for those)."""
        loop_a = tmp_path / "loop-a"
        loop_b = tmp_path / "loop-b"
        loop_a.symlink_to(loop_b)
        loop_b.symlink_to(loop_a)
        result = _run_cli(str(loop_a))
        assert result.returncode == 2
        assert "no such file" in result.stderr.lower()
        assert str(loop_a) in result.stderr

    def test_symlink_to_a_regular_file_is_read_normally(self, tmp_path):
        """The guard must reject by target type, not by is_symlink() alone:
        a symlink to an ordinary plan file is exactly as valid a plan path as
        the file itself, and must resolve through the normal PASS/FAIL path."""
        real_plan = tmp_path / "real-plan.md"
        real_plan.write_text("Row 1 [mechanism]: x — anchors: root\n")
        plan = tmp_path / "symlink-plan.md"
        plan.symlink_to(real_plan)
        result = _run_cli(str(plan))
        assert result.returncode == 0
        assert "pass" in result.stdout.lower()

    def test_leading_utf8_bom_before_a_first_line_row_definition_is_ignored(self, tmp_path):
        plan = tmp_path / "bom-plan.md"
        plan.write_bytes(
            b"\xef\xbb\xbf" + "Row 1 [mechanism]: x — anchors: root\nRow 2 [assumption]: y — anchors: row1\n".encode()
        )
        result = _run_cli(str(plan))
        assert result.returncode == 0, result.stdout + result.stderr

    def test_exits_2_for_non_utf8_content(self, tmp_path):
        plan = tmp_path / "binary-plan.md"
        plan.write_bytes(b"\xff\xfe\x00 not valid utf-8")
        result = _run_cli(str(plan))
        assert result.returncode == 2
        assert "utf-8" in result.stderr.lower()
        assert str(plan) in result.stderr

    def test_unexpected_exception_exits_2_with_a_one_line_message_not_1(self, tmp_path, monkeypatch, capsys):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)

        def fail_unexpectedly(_text):
            raise RuntimeError("simulated defect")

        monkeypatch.setattr(_mod, "collect_defined_labels", fail_unexpectedly)
        assert _mod.main([_SCRIPT.name, str(plan)]) == 2
        stderr_lines = capsys.readouterr().err.splitlines()
        assert len(stderr_lines) == 1
        assert "RuntimeError" in stderr_lines[0]
        assert "simulated defect" in stderr_lines[0]

    def test_unencodable_output_exits_2_with_nothing_on_stdout(self, tmp_path):
        """The only non-encodable text is a defined label, which prints after the
        ASCII FAIL line, so emitting the report line by line would leave that
        FAIL line on stdout. `\\d` in the definition pattern matches the Arabic-Indic
        digit, which is what lets the label reach the output."""
        plan = tmp_path / "widget-plan.md"
        plan.write_text(
            "## Approach\nRow \u0669 [mechanism]: x — anchors: root\nSee row 9 for detail.\n",
            encoding="utf-8",
        )
        result = _run_cli(str(plan), env={**os.environ, "PYTHONIOENCODING": "ascii"})
        assert result.returncode == 2
        assert result.stdout == ""
        assert "UnicodeEncodeError" in result.stderr

    def test_every_residual_item_has_exactly_one_pinned_fragment(self):
        """Ties the pinned fragments to the tuple, so an item added without a
        fragment fails here instead of going unpinned."""
        fragments = list(_RESIDUAL_FRAGMENTS_BY_ID.values())
        assert len(fragments) == len(_mod.RESIDUAL_CHECKLIST_ITEMS)
        for item in _mod.RESIDUAL_CHECKLIST_ITEMS:
            assert len([fragment for fragment in fragments if fragment in item]) == 1, item

    @pytest.mark.parametrize(
        "residual_fragment", list(_RESIDUAL_FRAGMENTS_BY_ID.values()), ids=list(_RESIDUAL_FRAGMENTS_BY_ID)
    )
    def test_not_checked_block_names_each_residual_item(self, tmp_path, capsys, residual_fragment):
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        assert _mod.main([_SCRIPT.name, str(plan)]) == 0
        assert residual_fragment in capsys.readouterr().out

    def test_runs_by_bare_path_with_no_interpreter_prefix(self, tmp_path):
        """Production invokes the script by bare path, so this is the only
        check that exercises the shebang and the executable bit."""
        plan = tmp_path / "widget-plan.md"
        plan.write_text(_CLEAN_LEDGER)
        result = subprocess.run([str(_SCRIPT), str(plan)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.startswith("PASS:")
