"""Pins that conftest's fake_projects, cost_ledger_file, and cost_ledger_enabled work in a
test file that loads no transcript-analysis.py shim. This file deliberately loads none, so a
fixture that comes to depend on the shim fails here first."""
import pytest
from transcript_analysis import cost_ledger, ledger_common, pr_cost_ledger, scope

from .conftest import _table_cols


def test_fake_projects_patches_every_config_dir_binding_without_a_shim(fake_projects, tmp_path):
    assert "_mod" not in globals()
    # Reading vars(scope) never triggers the lazy PROJECTS_DIR resolution that attribute access runs.
    # A cached lazy value and the fixture's setattr both leave a real entry.
    # Only a value equal to this test's projects directory therefore proves the patch.
    assert vars(scope)["PROJECTS_DIR"] == fake_projects.parent
    assert scope.config_dir() == tmp_path
    assert cost_ledger.config_dir() == tmp_path
    assert ledger_common.config_dir() == tmp_path
    assert pr_cost_ledger.config_dir() == tmp_path


def test_cost_ledger_fixtures_patch_their_targets_without_a_shim(
    cost_ledger_file, cost_ledger_enabled
):
    assert cost_ledger._cost_ledger_path() == cost_ledger_file
    assert cost_ledger.config_dir() == cost_ledger_enabled
    assert ledger_common.config_dir() == cost_ledger_enabled


class TestTableColsHelper:
    """Unit tests for the _table_cols helper — verifies the safety properties
    the refactor depends on."""

    def test_basic_column_lookup(self):
        out = "Branch Lead Main\nfeat   1    0  \n"
        cols = _table_cols(out, header_contains="Lead", row_contains="feat")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"

    def test_drop_leading_labels(self):
        out = "Branch Thread Opus Sonnet\nmain   main   1    2\n       sidechain 0  1\n"
        # sidechain row has Branch suppressed — one fewer leading token
        cols = _table_cols(out, header_contains="Thread", row_contains="sidechain",
                           drop_leading_labels=1)
        assert cols["Thread"] == "sidechain"
        assert cols["Opus"] == "0"
        assert cols["Sonnet"] == "1"

    def test_row_not_unique_raises(self):
        out = "Branch Lead\nfeat   1\nfeat   2\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_row_too_few_tokens_raises(self):
        out = "Branch Lead Main\nfeat   1\n"
        with pytest.raises(AssertionError, match="fewer cells"):
            _table_cols(out, header_contains="Lead", row_contains="feat")

    def test_max_labels_excludes_trailing_multiword_header(self):
        # "Top subagent types" is 3 header tokens but 1 data token (—); max_labels=4
        # limits to Branch/Sess/Spawns/CR so the assertion len(values)>=len(labels) passes.
        out = "Branch Sess Spawns CR Top subagent types\nfeat   1    0      2 —\n"
        cols = _table_cols(out, header_contains="Spawns", row_contains="feat", max_labels=4)
        assert cols["Spawns"] == "0"
        assert cols["CR"] == "2"

    def test_row_startswith_excludes_indented_lines(self):
        # "code-review" appears at column 0 in the data row and indented in the summary.
        out = "skill  top-level user-slash\ncode-review  1  0\n  code-review (1 top)\n"
        cols = _table_cols(out, header_contains="user-slash", row_contains="code-review",
                           row_startswith=True)
        assert cols["top-level"] == "1"
        assert cols["user-slash"] == "0"

    def test_header_not_unique_raises(self):
        # Two lines both contain "Lead" → header ambiguity must raise, not silently pick one.
        out = "Bin Lead Main\n2026-W20 1 0\nLead row duplicate 0 1\n"
        with pytest.raises(AssertionError, match="not unique"):
            _table_cols(out, header_contains="Lead", row_contains="2026-W20")

    def test_occurrence_scoping_excludes_content_after_the_selected_block(self):
        """occurrence=N must stop its row/header search at its own table's
        boundary (next blank line or next header-containing line), not
        search to the end of `out` — every existing occurrence= call site
        (reviewer-yield's Table 2, always the last table printed, with a
        composite row_contains already globally unique) would stay green
        even if that boundary were dropped, since there is nothing after
        Table 2 for an unscoped search to leak into. This fixture puts a
        second occurrence directly after the first's true boundary, with a
        row that would also satisfy `row_contains` if the boundary weren't
        enforced — occurrence=1 must select only the first table's row."""
        out = "\n".join([
            "Header      Count",
            "------------------",
            "alpha           1",
            "",
            "Header      Count",
            "------------------",
            "alpha           2",
        ])
        cols = _table_cols(out, header_contains="Header", row_contains="alpha", occurrence=1)
        assert cols["Count"] == "1"
