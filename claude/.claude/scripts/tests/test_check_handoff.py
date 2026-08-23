"""Tests for check-handoff.py."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "check-handoff.py"
_spec = importlib.util.spec_from_file_location("check_handoff", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

CANONICAL_PREAMBLE = "PREAMBLE LINE ONE.\nPREAMBLE LINE TWO."


def _doc(overrides: dict[int, str] | None = None, omit: tuple[int, ...] = ()) -> str:
    """Build a minimal §1-§7 document. `overrides` replaces a section's body
    (empty string tests the empty-body case); `omit` drops a header entirely
    (tests the missing-header case)."""
    overrides = overrides or {}
    parts = []
    for n in range(1, 8):
        if n in omit:
            continue
        body = overrides.get(n, "content.")
        parts.append(f"## §{n} Body\n{body}\n")
    return "\n".join(parts)


class TestExtractFixture:
    def test_returns_none_when_no_matching_fixture_id_exists(self):
        skill_text = (
            "<!-- HOOK_TEST_FIXTURE: other-id -->\n```\nbody\n```\n"
        )
        assert _mod.extract_fixture(skill_text, "artifact-preamble") is None

    def test_returns_none_when_the_fixture_id_is_duplicated(self):
        skill_text = (
            "<!-- HOOK_TEST_FIXTURE: artifact-preamble -->\n```\nfirst\n```\n\n"
            "<!-- HOOK_TEST_FIXTURE: artifact-preamble -->\n```\nsecond\n```\n"
        )
        assert _mod.extract_fixture(skill_text, "artifact-preamble") is None


class TestCheckFenceMarkersBalanced:
    def test_passes_when_no_fences_are_present(self):
        assert _mod.check_fence_markers_balanced(_doc()) == []

    def test_passes_when_fence_count_is_even(self):
        draft = _doc(overrides={3: "```\ncode\n```"})
        assert _mod.check_fence_markers_balanced(draft) == []

    def test_fails_when_a_fence_is_left_unterminated(self):
        draft = _doc(overrides={3: "```\ncode that never closes"})
        problems = _mod.check_fence_markers_balanced(draft)
        assert any("odd number" in p for p in problems)

    def test_unterminated_fence_followed_by_later_placeholder_is_still_flagged(self):
        """The failure mode this check guards against: an unterminated fence
        in one section pairs with the next ``` anywhere later in the
        document, silently stripping a genuine placeholder between them from
        check_placeholder_text's view. Either check firing proves the draft
        is never silently accepted."""
        draft = _doc(overrides={
            3: "See below:\n```\nsome quoted diff\n",
            5: "TODO: still need to fill this in.\n```\nend of a real quoted block\n```",
        })
        fence_problems = _mod.check_fence_markers_balanced(draft)
        placeholder_problems = _mod.check_placeholder_text(draft)
        assert fence_problems or placeholder_problems, (
            "neither check flagged a draft with an unterminated fence hiding "
            "a placeholder -- the draft would be silently accepted"
        )


class TestCheckPreamble:
    def test_passes_when_draft_opens_with_the_canonical_preamble(self):
        draft = CANONICAL_PREAMBLE + "\n\n" + _doc()
        assert _mod.check_preamble(draft, CANONICAL_PREAMBLE) == []

    def test_passes_when_only_whitespace_differs(self):
        """Whitespace-normalized, not byte-for-byte -- a draft may re-wrap
        the preamble's lines."""
        reflowed = "PREAMBLE LINE ONE.   \n  PREAMBLE LINE TWO.\n\n"
        draft = reflowed + _doc()
        assert _mod.check_preamble(draft, CANONICAL_PREAMBLE) == []

    def test_fails_when_preamble_text_differs(self):
        draft = "Some other opening text.\n\n" + _doc()
        assert _mod.check_preamble(draft, CANONICAL_PREAMBLE) != []

    def test_fails_when_preamble_is_not_at_the_top(self):
        draft = "## §1 Goal\nsomething first\n\n" + CANONICAL_PREAMBLE
        assert _mod.check_preamble(draft, CANONICAL_PREAMBLE) != []


class TestCheckSectionsPresentAndNonempty:
    def test_passes_when_every_section_is_present_and_nonempty(self):
        assert _mod.check_sections_present_and_nonempty(_doc()) == []

    def test_fails_when_a_section_header_is_missing(self):
        problems = _mod.check_sections_present_and_nonempty(_doc(omit=(6,)))
        assert any("§6" in p and "missing" in p for p in problems)

    def test_fails_when_a_section_body_is_empty(self):
        problems = _mod.check_sections_present_and_nonempty(_doc(overrides={3: ""}))
        assert any("§3" in p and "empty" in p for p in problems)

    def test_does_not_mistake_a_dotted_subsection_for_its_parent(self):
        """§2.5's header must not satisfy §2's presence check -- the two are
        distinct sections with distinct bodies."""
        draft = (
            "## §1 Body\ncontent.\n\n"
            "## §2.5 Incomplete prerequisites\nNone.\n\n"
            "## §3 Body\ncontent.\n\n"
            "## §4 Body\ncontent.\n\n"
            "## §5 Body\ncontent.\n\n"
            "## §6 Body\ncontent.\n\n"
            "## §7 Body\ncontent.\n"
        )
        problems = _mod.check_sections_present_and_nonempty(draft)
        assert any("§2" in p and "missing" in p for p in problems)


class TestCheckPlaceholderText:
    def test_passes_when_no_placeholder_token_appears(self):
        assert _mod.check_placeholder_text(_doc()) == []

    def test_fails_when_todo_appears_in_prose(self):
        draft = _doc(overrides={3: "TODO: figure this out."})
        problems = _mod.check_placeholder_text(draft)
        assert any("TODO" in p for p in problems)

    def test_fails_when_tbd_appears_in_prose(self):
        draft = _doc(overrides={2: "Status is TBD."})
        problems = _mod.check_placeholder_text(draft)
        assert any("TBD" in p for p in problems)

    def test_fails_when_fill_in_later_appears_in_prose(self):
        draft = _doc(overrides={5: "Details -- fill in later."})
        problems = _mod.check_placeholder_text(draft)
        assert any("fill in later" in p for p in problems)

    def test_does_not_fire_on_todo_inside_inline_code_span(self):
        """Citing another file's `TODO:` marker as an inline code span is a
        quote, not a planned placeholder -- must not fire."""
        draft = _doc(overrides={3: "The old file had a `TODO:` marker here."})
        assert _mod.check_placeholder_text(draft) == []

    def test_does_not_fire_on_todo_inside_fenced_code_block(self):
        draft = _doc(overrides={3: "```\nTODO: legacy comment\n```"})
        assert _mod.check_placeholder_text(draft) == []

    def test_same_token_outside_code_formatting_still_fires(self):
        """The code-span skip is scoped, not a blanket exemption -- the same
        literal string in plain prose (no backticks) still fires."""
        draft = _doc(overrides={3: "TODO: legacy comment, still unresolved"})
        problems = _mod.check_placeholder_text(draft)
        assert any("TODO" in p for p in problems)


class TestCheckSection7ResolvedAndNamed:
    def test_passes_when_resolved_and_names_the_checked_file(self):
        draft = _doc(overrides={7: "resume-context /home/<username>/.claude/handoffs/my-task-handoff.md"})
        assert _mod.check_section7_resolved_and_named(draft, Path("my-task-handoff.md")) == []

    def test_fails_on_unresolved_config_dir_token(self):
        draft = _doc(overrides={7: "resume-context <config-dir>/handoffs/my-task-handoff.md"})
        problems = _mod.check_section7_resolved_and_named(draft, Path("my-task-handoff.md"))
        assert any("<config-dir>" in p for p in problems)

    def test_fails_on_unresolved_slug_token(self):
        draft = _doc(overrides={7: "resume-context /home/<username>/.claude/handoffs/<slug>-handoff.md"})
        problems = _mod.check_section7_resolved_and_named(draft, Path("<slug>-handoff.md"))
        assert any("<slug>" in p for p in problems)

    def test_fails_when_section7_names_a_different_file(self):
        draft = _doc(overrides={7: "resume-context /home/<username>/.claude/handoffs/other-task-handoff.md"})
        problems = _mod.check_section7_resolved_and_named(draft, Path("my-task-handoff.md"))
        assert any("does not name" in p for p in problems)

    def test_returns_no_problems_when_section7_is_missing(self):
        """A missing §7 header is already reported by
        check_sections_present_and_nonempty -- this check must not
        double-report it."""
        draft = _doc(omit=(7,))
        assert _mod.check_section7_resolved_and_named(draft, Path("anything.md")) == []


class TestCheckSection3AnchorShapes:
    def test_fires_for_a_plain_anchor_shape_match(self):
        draft = _doc(overrides={3: "Run rm -rf on the scratch dir."})
        warnings = _mod.check_section3_anchor_shapes(draft)
        assert any("rm -rf" in w for w in warnings)

    def test_does_not_fire_when_no_anchor_shape_is_present(self):
        draft = _doc(overrides={3: "Run the test suite and report results."})
        assert _mod.check_section3_anchor_shapes(draft) == []

    def test_does_not_fire_on_anchor_shape_inside_inline_code_span(self):
        """A §3 line quoting `rm -rf` as something explicitly *not* to run
        must not fire."""
        draft = _doc(overrides={3: "Do not run `rm -rf` here -- see §3.5 for why."})
        assert _mod.check_section3_anchor_shapes(draft) == []

    def test_does_not_fire_on_anchor_shape_inside_fenced_code_block(self):
        draft = _doc(overrides={3: "```\nrm -rf /tmp/scratch\n```"})
        assert _mod.check_section3_anchor_shapes(draft) == []

    def test_same_shape_outside_code_formatting_still_fires(self):
        """The code-span skip is scoped, not a blanket exemption."""
        draft = _doc(overrides={3: "gh pr merge 123 once tests pass"})
        warnings = _mod.check_section3_anchor_shapes(draft)
        assert any("gh pr merge" in w for w in warnings)


class TestCheckConfidenceTags:
    def test_fires_when_a_section_carries_no_confidence_tag(self):
        draft = _doc(overrides={2: "in-flight, no tag here"})
        warnings = _mod.check_confidence_tags(draft)
        assert any("§2" in w for w in warnings)

    def test_does_not_fire_when_the_section_carries_a_confidence_tag(self):
        draft = _doc(overrides={2: "in-flight [verified: ran tests]"})
        warnings = _mod.check_confidence_tags(draft)
        assert not any("§2" in w for w in warnings)

    @pytest.mark.parametrize("tag", ["[engineer-confirmed]", "[verified: ran tests]", "[assumed]"])
    def test_each_recognized_tag_satisfies_the_check(self, tag):
        draft = _doc(overrides={6: f"Some claim {tag}"})
        warnings = _mod.check_confidence_tags(draft)
        assert not any("§6" in w for w in warnings)

    def test_only_checks_sections_2_3_and_6(self):
        """§1/§4/§5/§7 carry no confidence-tag requirement at all -- leaving
        them untagged must not add a warning, as long as §2/§3/§6 (the only
        sections this check covers) each carry a tag."""
        draft = _doc(overrides={
            1: "untagged", 4: "untagged", 5: "untagged", 7: "untagged",
            2: "tagged [assumed]", 3: "tagged [assumed]", 6: "tagged [assumed]",
        })
        warnings = _mod.check_confidence_tags(draft)
        assert warnings == []


class TestCli:
    """Subprocess-level, end-to-end wiring: argv handling, exit codes, and
    the combined PASS/FAIL/WARN printout. Individual check logic is covered
    above at the unit level -- these exercise the CLI only."""

    def _clean_draft(self, home_relative_name: str) -> str:
        skill_text = _mod.SKILL_MD_PATH.read_text(encoding="utf-8")
        preamble = _mod.extract_fixture(skill_text, "artifact-preamble")
        return (
            preamble
            + "\n\n"
            + _doc(overrides={
                2: "in-flight [verified: ran tests]",
                3: "Run the test suite. [assumed]",
                6: "None. [verified: checked]",
                7: f"resume-context /home/<username>/.claude/handoffs/{home_relative_name}",
            })
        )

    def test_exits_2_for_nonexistent_file(self, tmp_path):
        missing = tmp_path / "does-not-exist-handoff.md"
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(missing)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "no such file" in result.stderr.lower()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_exits_2_for_unreadable_file(self, tmp_path):
        draft = tmp_path / "unreadable-handoff.md"
        draft.write_text("content.")
        draft.chmod(0o000)
        try:
            result = subprocess.run(
                [sys.executable, str(_SCRIPT), str(draft)],
                capture_output=True, text=True,
            )
        finally:
            draft.chmod(0o644)
        assert result.returncode == 2
        assert "cannot read" in result.stderr.lower()

    def test_exits_2_for_non_utf8_content(self, tmp_path):
        draft = tmp_path / "binary-handoff.md"
        draft.write_bytes(b"\xff\xfe\x00 not valid utf-8")
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(draft)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "utf-8" in result.stderr.lower()

    def test_exits_2_for_wrong_argument_count(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_clean_fixture_passes_every_hard_check_and_exits_0(self, tmp_path):
        draft = tmp_path / "my-task-handoff.md"
        draft.write_text(self._clean_draft("my-task-handoff.md"))
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(draft)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout

    def test_hard_failure_and_warning_both_print_and_exit_is_nonzero(self, tmp_path):
        draft = tmp_path / "my-task-handoff.md"
        text = self._clean_draft("my-task-handoff.md")
        # A hard failure (placeholder text) that leaves §2's confidence tag
        # intact, and a soft warning (§3.5 anchor shape) that leaves §3's
        # confidence tag intact -- so exactly one FAIL and one WARN fire,
        # not an incidental extra from removing a tag along the way.
        text = text.replace(
            "in-flight [verified: ran tests]", "TODO in-flight [verified: ran tests]"
        )
        text = text.replace(
            "Run the test suite. [assumed]", "Run rm -rf on the scratch dir. [assumed]"
        )
        draft.write_text(text)
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(draft)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "FAIL: placeholder text found: 'TODO'" in result.stdout
        assert "WARN: §3 matches a §3.5 anchor shape ('rm -rf')" in result.stdout
        assert "Not checked by this script" in result.stdout


class TestResidualChecklistItemsDriftGuard:
    """RESIDUAL_CHECKLIST_ITEMS restates, in its own independently-worded
    prose, the same "script cannot check this" bullets handoff/SKILL.md's
    Pre-write checklist section states separately. Nothing else keeps the
    two in sync -- this is a loose sync check, not exact-string equality, so
    a minor rewording on either side doesn't break it."""

    # Each keyword was hand-verified against handoff/SKILL.md's "The script
    # cannot check these" bullet list at the time this test was written, in
    # the same order as RESIDUAL_CHECKLIST_ITEMS.
    RESIDUAL_ITEM_KEYWORDS = (
        "consistent with §3 Next concrete step and §6 Open questions",
        "verification is still pending",
        "§2.6",
        "pr-description",
        "sed -n",
    )

    def _checklist_section_text(self) -> str:
        skill_text = _mod.SKILL_MD_PATH.read_text(encoding="utf-8")
        after_header = skill_text.split("## Pre-write checklist", 1)[1]
        return after_header.split("\n## ", 1)[0]

    def test_residual_checklist_items_count_matches_keyword_count(self):
        """A keyword-only check can't catch an item added to one tuple and
        forgotten in the other -- pin the two lengths together so a mismatch
        fails loudly instead of silently under-covering."""
        assert len(_mod.RESIDUAL_CHECKLIST_ITEMS) == len(self.RESIDUAL_ITEM_KEYWORDS)

    @pytest.mark.parametrize("keyword", RESIDUAL_ITEM_KEYWORDS)
    def test_keyword_appears_in_skill_md_pre_write_checklist_section(self, keyword):
        assert keyword in self._checklist_section_text(), (
            f"{keyword!r} not found in handoff/SKILL.md's Pre-write checklist "
            "section -- RESIDUAL_CHECKLIST_ITEMS and SKILL.md's prose may "
            "have drifted apart"
        )
