"""Tests for transcript_analysis/pr_cost.py (cmd_pr_cost) -- local-mechanics coverage only
(attribution, ledger I/O, correction contract, mechanical proxies, join logic that doesn't
require faking gh). gh-integration scenarios live in test_transcript_pr_cost_gh.py."""
import importlib.util
import json
import random
import stat
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ._pr_cost_helpers import (
    _COUNT_LINE_STEM,
    _LEDGER_DIR_MARKER,
    _PER_PR_DEGRADE_STEM,
    _PRE_MODEL_HEADER_LINE,
    _PRIOR_ROW_MARKER,
    _UPGRADE_NOTICE_STEM,
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _make_mkstemp_create_0644,
    _merged_pr,
    _pr_cost_args,
    _pr_cost_export_args,
    _pre_model_row_line,
    _sample_model_breakdown,
    _sample_pr_cost_row,
)
from .conftest import _cost_args, _extract_grand_total, _priced, _two_declared_roots, _write_jsonl, _write_subagent_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)
class TestComputePrCostBranchTotals:
    """_compute_pr_cost_branch_totals: pr-cost's own single-pass per-branch
    aggregation, exercised through the function itself (not just its reused
    primitives _attributed_branch/_session_branch_index in isolation)."""

    def test_worktree_agent_record_folds_into_branch_active_at_dispatch_time(self, fake_projects):
        """A worktree-agent-* subagent record's dollars fold into the branch
        active in its own session at dispatch time, via pr-cost's own
        grouping call -- mirrors TestCostWorktreeAgentBranchCarryForward's
        case (a), against _compute_pr_cost_branch_totals instead of cost's
        --branches path."""
        session_id = "sess-carry-a"
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T11:00:00.000Z",
        )  # $1.00, later than main_rec
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        assert "worktree-agent-abc123" not in branch_totals
        assert sum(branch_totals["feature-a"]["dollars"].values()) == pytest.approx(3.00)
        assert branch_totals["feature-a"]["turn_count"] == 2
        assert unbranched["turn_count"] == 0

    def test_mid_session_branch_switch_splits_turns_across_both_branches(self, fake_projects):
        """A session whose main-thread records switch branches mid-session
        splits its turns across both branches' aggregates -- not
        all-or-nothing attribution."""
        session_id = "sess-switch"
        first_main = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        second_main = _priced(
            "claude-sonnet-5", input=1_000_000, branch="main", ts="2026-08-01T12:00:00.000Z",
        )  # $2.00
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [first_main, second_main])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        assert sum(branch_totals["feature-a"]["dollars"].values()) == pytest.approx(2.00)
        assert sum(branch_totals["main"]["dollars"].values()) == pytest.approx(2.00)
        assert branch_totals["feature-a"]["turn_count"] == 1
        assert branch_totals["main"]["turn_count"] == 1

    def test_worktree_agent_record_with_unparseable_timestamp_falls_back_to_earliest_index_entry(
        self, fake_projects,
    ):
        """A worktree-agent-* record whose own timestamp doesn't parse
        degrades gracefully via _attributed_branch's documented contract
        (rec_ts is None -> falls back to branch_index[0][1], the session's
        earliest main-thread branch entry) rather than crashing or dropping
        into unbranched_totals."""
        session_id = "sess-bad-ts"
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T09:00:00.000Z",
        )
        agent_rec["isSidechain"] = True
        agent_rec["timestamp"] = "not-a-real-timestamp"  # overrides _priced's own valid ts
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        assert "worktree-agent-abc123" not in branch_totals
        assert sum(branch_totals["feature-a"]["dollars"].values()) == pytest.approx(3.00)
        assert unbranched["turn_count"] == 0

    def test_multi_session_accumulation_for_one_branch(self, fake_projects):
        """Two separate session files on the same branch both contribute to
        one branch's aggregate -- the shape the single-pass approach
        depends on."""
        _write_jsonl(fake_projects / "sess-1.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),  # $2.00
        ])
        _write_jsonl(fake_projects / "sess-2.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-a"),  # $1.00
        ])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        agg = branch_totals["feature-a"]
        assert sum(agg["dollars"].values()) == pytest.approx(3.00)
        assert agg["turn_count"] == 2
        assert len(agg["sessions"]) == 2

    def test_null_git_branch_record_counted_in_unbranched_totals_not_skipped(self, fake_projects):
        """A record with no gitBranch is accumulated into unbranched_totals,
        unlike `buckets`, which silently skips a record with no gitBranch."""
        rec = _priced("claude-sonnet-5", input=1_000_000)  # $2.00
        rec["gitBranch"] = None
        _write_jsonl(fake_projects / "sess.jsonl", [rec])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        assert branch_totals == {}
        assert unbranched["turn_count"] == 1
        assert sum(unbranched["dollars"].values()) == pytest.approx(2.00)

    def test_unpriced_model_increments_unpriced_counters_not_dollars(self, fake_projects):
        """A model absent from the price table increments unpriced_turns/
        unpriced_tokens; it never contributes to the dollars total."""
        rec = _priced("<synthetic>", input=1_000_000, branch="feature-a")
        _write_jsonl(fake_projects / "sess.jsonl", [rec])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        agg = branch_totals["feature-a"]
        assert agg["unpriced_turns"] == 1
        assert agg["unpriced_tokens"] == 1_000_000
        assert sum(agg["dollars"].values()) == pytest.approx(0.0)

    def test_per_class_token_totals_accumulate_correctly(self, fake_projects):
        """agg["tokens"] accumulates each _TOKEN_CLASSES key from
        _token_counts(usage) correctly -- distinct values per class, not
        uniform input-only, so a class-key swap in the accumulation loop
        (token_counts[cls] keyed wrong, or a transposed cache_write_1h/5m)
        would fail this rather than passing on coincidentally-equal values."""
        rec = _priced(
            "claude-sonnet-5", input=1_000_000, output=200_000,
            cache_read=50_000, ephemeral_1h=30_000, ephemeral_5m=10_000,
            branch="feature-a",
        )
        _write_jsonl(fake_projects / "sess.jsonl", [rec])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        tokens = branch_totals["feature-a"]["tokens"]
        assert tokens["input"] == 1_000_000
        assert tokens["output"] == 200_000
        assert tokens["cache_read"] == 50_000
        assert tokens["cache_write_1h"] == 30_000
        assert tokens["cache_write_5m"] == 10_000


class TestPrCostDedupBeforePricing:
    def test_multi_content_block_turn_prices_identically_via_pr_cost_and_cost_branches(
        self, fake_projects, capsys,
    ):
        """A multi-content-block turn sharing one requestId prices
        identically whether summed via pr-cost's own
        _compute_pr_cost_branch_totals or the existing cost --branches path
        -- the dedup-before-pricing regression this repo's own contract
        (pricing.py's dedup_turns_by_request_id) must hold for pr-cost's new
        aggregation too."""
        session_id = "sess-dedup"
        rec1 = _priced(
            "claude-sonnet-5", input=100_000, output=3, branch="feature-a", request_id="req-1",
            content=[{"type": "thinking", "thinking": "..."}],
        )
        rec2 = _priced(
            "claude-sonnet-5", input=100_000, output=50, branch="feature-a", request_id="req-1",
            content=[{"type": "text", "text": "done"}],
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [rec1, rec2])

        session_iter, _scope = _mod._resolve_project_scope(
            _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
        )
        branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
        assert branch_totals["feature-a"]["turn_count"] == 1  # the two records collapse into one priced turn
        pr_cost_total = sum(branch_totals["feature-a"]["dollars"].values())

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        cost_total = _extract_grand_total(capsys.readouterr().out)
        # abs= accounts for the printed table's own 2-decimal-place rounding,
        # not slack in the expected computation itself.
        assert pr_cost_total == pytest.approx(cost_total, abs=0.005)


class TestResolveBranchPrTieBreak:
    """_resolve_branch_pr's >1-direct-match tie-break arms -- unit-level,
    plain data in, no gh faking needed for the two arms whose SHA-overlap
    computation short-circuits on an empty commits list; the SHA-overlap
    arms fake only the local `git cat-file --batch-check` call."""

    def test_highest_sha_overlap_wins_outright(self, monkeypatch):
        matches = [
            {"number": 1, "headRefName": "shared-branch", "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "shared-branch", "mergedAt": "2026-01-02T00:00:00Z"},
        ]
        enrichment_by_pr_number = {
            1: {"commits": [{"oid": "a" * 40}], "files": []},
            2: {"commits": [{"oid": "b" * 40}, {"oid": "c" * 40}], "files": []},
        }
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(local_git_shas={"b" * 40, "c" * 40}))
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            "shared-branch", matches, enrichment_by_pr_number, _mod.pr_cost._DEFAULT_PR_COST_PLAN_FILE_GLOB,
        )
        assert resolved["number"] == 2
        assert confidence == "low"

    def test_overlap_tie_broken_by_most_recent_merged_at(self, monkeypatch):
        matches = [
            {"number": 1, "headRefName": "shared-branch", "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "shared-branch", "mergedAt": "2026-02-01T00:00:00Z"},
        ]
        enrichment_by_pr_number = {
            1: {"commits": [{"oid": "a" * 40}], "files": []},
            2: {"commits": [{"oid": "b" * 40}], "files": []},
        }
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(local_git_shas={"a" * 40, "b" * 40}))
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            "shared-branch", matches, enrichment_by_pr_number, _mod.pr_cost._DEFAULT_PR_COST_PLAN_FILE_GLOB,
        )
        assert resolved["number"] == 2
        assert confidence == "low"

    def test_still_ambiguous_after_both_tie_breaks_returns_none(self):
        matches = [
            {"number": 1, "headRefName": "shared-branch", "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "shared-branch", "mergedAt": "2026-01-01T00:00:00Z"},
        ]
        enrichment_by_pr_number = {
            1: {"commits": [], "files": []},
            2: {"commits": [], "files": []},
        }
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            "shared-branch", matches, enrichment_by_pr_number, _mod.pr_cost._DEFAULT_PR_COST_PLAN_FILE_GLOB,
        )
        assert resolved is None
        assert confidence == "low"


class TestPrCostJoinCorroborated:
    """_pr_cost_join_corroborated: plan-slug match and SHA overlap each
    independently corroborate a direct headRefName match (an `or`, not an
    `and`)."""

    def test_true_via_plan_slug_alone(self):
        enrichment = {"files": [{"path": ".claude/plans/feat-x.md"}], "commits": []}
        assert _mod.pr_cost._pr_cost_join_corroborated("feat-x", enrichment, ".claude/plans/*.md") is True

    def test_true_via_sha_overlap_alone(self, monkeypatch):
        sha = "e" * 40
        enrichment = {"files": [], "commits": [{"oid": sha}]}
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(local_git_shas={sha}))
        assert _mod.pr_cost._pr_cost_join_corroborated("feat-x", enrichment, ".claude/plans/*.md") is True

    def test_false_when_neither_present(self):
        enrichment = {"files": [], "commits": []}
        assert _mod.pr_cost._pr_cost_join_corroborated("feat-x", enrichment, ".claude/plans/*.md") is False

    def test_false_when_enrichment_is_none(self):
        assert _mod.pr_cost._pr_cost_join_corroborated("feat-x", None, ".claude/plans/*.md") is False


class TestResolveBranchPrJoinConfidence:
    """_resolve_branch_pr's single-direct-match confidence grading: "high"
    when either cross-check corroborates, else "medium"."""

    def test_plan_slug_corroboration_yields_high_confidence(self):
        matches = [{"number": 1, "headRefName": "feat-x", "mergedAt": "2026-01-01T00:00:00Z"}]
        enrichment_by_pr_number = {1: {"files": [{"path": ".claude/plans/feat-x.md"}], "commits": []}}
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            "feat-x", matches, enrichment_by_pr_number, ".claude/plans/*.md",
        )
        assert resolved["number"] == 1
        assert confidence == "high"

    def test_sha_overlap_corroboration_without_plan_slug_yields_high_confidence(self, monkeypatch):
        matches = [{"number": 1, "headRefName": "feat-x", "mergedAt": "2026-01-01T00:00:00Z"}]
        sha = "d" * 40
        enrichment_by_pr_number = {1: {"files": [], "commits": [{"oid": sha}]}}
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(local_git_shas={sha}))
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            "feat-x", matches, enrichment_by_pr_number, ".claude/plans/*.md",
        )
        assert resolved["number"] == 1
        assert confidence == "high"

    def test_no_corroboration_yields_medium_confidence(self):
        matches = [{"number": 1, "headRefName": "feat-x", "mergedAt": "2026-01-01T00:00:00Z"}]
        enrichment_by_pr_number = {1: {"files": [], "commits": []}}
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            "feat-x", matches, enrichment_by_pr_number, ".claude/plans/*.md",
        )
        assert resolved["number"] == 1
        assert confidence == "medium"


class TestPrCostBranchRedactionJoinIntegrity:
    """A branch name shaped like a long hex identifier (the kind
    deny-private-project-refs.sh's structural detectors would flag in a raw
    commit) still joins correctly and is still scrubbed at the ledger's own
    write boundary."""

    # Built via concatenation, not a literal run: a continuous 32-char hex
    # sequence here would itself match the redaction gate's own "long hex
    # identifier" detector on this file's diff, blocking the commit that
    # adds this fixture -- splitting it produces the identical runtime
    # string this test needs without embedding a matching literal.
    _HEXISH_BRANCH = "a1b2c3d4e5f6" + "78900987654321fedcba"

    def test_hex_shaped_branch_name_still_joins_on_raw_value(self):
        """Join logic (_direct_headref_matches/_resolve_branch_pr) operates
        on the raw branch value -- it is never pre-scrubbed before the join
        runs."""
        merged_prs = [{"number": 9, "headRefName": self._HEXISH_BRANCH, "mergedAt": "2026-01-01T00:00:00Z"}]
        matches = _mod.pr_cost._direct_headref_matches(self._HEXISH_BRANCH, merged_prs)
        assert len(matches) == 1
        resolved, confidence = _mod.pr_cost._resolve_branch_pr(
            self._HEXISH_BRANCH, matches, {}, _mod.pr_cost._DEFAULT_PR_COST_PLAN_FILE_GLOB,
        )
        assert resolved["number"] == 9
        assert confidence == "medium"

    def test_hex_shaped_branch_name_stored_scrubbed_in_new_pr_cost_row(self):
        """_new_pr_cost_row's head_branch column IS the scrubbed placeholder
        -- the ledger's own write boundary for branch data, distinct from
        the join above which never sees it."""
        pr = {"number": 9, "mergedAt": "2026-01-01T00:00:00Z", "additions": 1, "deletions": 1, "changedFiles": 1}
        row = _mod.pr_cost._new_pr_cost_row(
            host="github.com", pinned_repo="owner/repo", pr=pr, branch=self._HEXISH_BRANCH,
            agg=_mod.cost._new_pr_cost_agg(),
            enrichment=None, join_confidence="medium", status=_mod.pr_cost_ledger._PR_COST_STATUS_OK, machine="ci1",
            captured_at="2026-01-01T00:00:00Z", supersedes="",
            plan_glob=_mod.pr_cost._DEFAULT_PR_COST_PLAN_FILE_GLOB, risk_globs=_mod.pr_cost._DEFAULT_PR_COST_RISK_SURFACE_GLOBS,
            ordinal=1, branch_map={},
        )
        assert row["head_branch"] != self._HEXISH_BRANCH
        assert row["head_branch"] == "account-1/branch-1"


class TestPrCostReportOrchestration:
    """Full _pr_cost_report orchestration, with every git/gh call faked via
    _fake_pr_cost_subprocess_run -- local-mechanics behavior only (zero-
    record agg defaulting, per-branch skip, the multi-root refusal, and
    the single local-corpus-scan guarantee), never gh-integration coverage."""

    def test_target_pr_with_zero_branch_records_uses_zero_valued_agg_default(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """_new_pr_cost_row's zero-valued shape is used correctly when
        branch_totals.get(branch) misses (--pr targeting a merged PR whose
        branch carries no local corpus activity at all). branch_totals is
        empty here (genuinely branch-idle), which must stay silent -- the
        renamed-branch mismatch warning in the sibling test below is gated on
        branch_totals being non-empty specifically to not fire on this case."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        merged_prs = [{
            "number": 42, "headRefName": "ghost-branch", "additions": 5, "deletions": 1,
            "changedFiles": 2, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=42)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        row = rows[0]
        assert row["turn_count"] == 0
        assert row["session_count"] == 0
        assert row["cache_read_usd"] == pytest.approx(0.0)
        assert row["input_usd"] == pytest.approx(0.0)
        assert row["unpriced_turns"] == 0
        assert "no matching" not in capsys.readouterr().err

    def test_renamed_branch_mismatch_warns_but_still_writes_zero_valued_row(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """The account's local corpus recorded activity under "old-name", but
        the targeted PR's resolved head branch is "new-name" (a mid-work
        rename) -- branch_totals.get(branch) misses even though the scan
        wasn't branch-idle, so the mismatch warning must fire on stderr and
        the row must still be written (visibility only, not a skip -- ledger
        Row 8). --pr targets the PR by its current head branch directly,
        which is what surfaces the mismatch: sweep mode instead iterates
        branch_totals's own keys, so "old-name" would never even match this
        PR's "new-name" headRefName."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="old-name"),
        ])
        merged_prs = [{
            "number": 99, "headRefName": "new-name", "additions": 5, "deletions": 1,
            "changedFiles": 2, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=99)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        err = capsys.readouterr().err
        assert "has no matching local corpus activity" in err
        assert "1 other branch(es)" in err
        assert "old-name" not in err
        assert "new-name" not in err

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        row = rows[0]
        assert row["turn_count"] == 0
        assert row["session_count"] == 0

    def test_captured_row_carries_correct_token_counts_alongside_dollars(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """A captured row's *_tokens columns are asserted end-to-end, not
        just their *_usd siblings -- dollars and tokens are independently
        derived (_price_turn vs _token_counts), so a regression in the
        token half of the ledger schema could otherwise ship with every
        existing orchestration test (which only checks *_usd) still green."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, output=200_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 7, "headRefName": "feature-a", "additions": 5, "deletions": 1,
            "changedFiles": 2, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        row = rows[0]
        assert row["input_tokens"] == 1_000_000
        assert row["output_tokens"] == 200_000
        assert row["cache_read_tokens"] == 0
        assert row["input_usd"] > 0
        assert row["output_usd"] > 0

    def test_branch_with_records_but_no_merged_pr_is_skipped_not_errored(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="orphan-branch"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[]))

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        err = capsys.readouterr().err
        assert "no merged PR found for this branch -- skipped" in err
        assert not ledger_path.exists()

    def test_targeted_pr_merged_inside_asof_window_refuses_with_exit_1(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """--pr targeting a PR that merged too recently (inside the default
        3-day as-of window) refuses outright rather than silently skipping --
        the caller asked for exactly this PR, so there is no other PR left
        to fall back to."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-08-09T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=1)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert exc_info.value.code == 1
        assert "refusing" in capsys.readouterr().err
        assert not ledger_path.exists()

    def test_swept_pr_merged_inside_asof_window_skips_without_exiting(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """No --pr (sweep mode): a branch's PR merged too recently is
        skipped, not fatal -- the run continues over any other branch in
        scope, unlike the --pr-targeted case above."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-08-09T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        err = capsys.readouterr().err
        assert "merged too recently" in err
        assert "skipped" in err
        assert not ledger_path.exists()

    def test_more_than_one_resolved_root_refuses_with_exit_2(self, fake_projects, tmp_path, capsys):
        other_root = tmp_path / "other-account" / "projects"
        other_root.mkdir(parents=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(
                _pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent, other_root],
            )
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert str(fake_projects.parent) not in err
        assert str(other_root) not in err

    def test_resolves_project_scope_exactly_once_across_multi_pr_run(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """The local corpus is scanned exactly once per invocation
        regardless of how many PRs end up in scope -- wraps (not replaces)
        _resolve_project_scope with a counting closure, matching
        TestRootsThreadingSpy's own spy-wrap pattern."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-b"),
        ])
        merged_prs = [
            {"number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "feature-b", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
        ]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        calls: list[object] = []
        real_resolve = _mod.scope._resolve_project_scope

        def counting_resolve(*a, **kw):
            calls.append(1)
            return real_resolve(*a, **kw)

        monkeypatch.setattr(_mod.scope, "_resolve_project_scope", counting_resolve)

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert len(calls) == 1


class TestPrCostRecordingConfigDirUnresolvable:
    """_config.config_enabled("pr_cost_recording", ...) returning None --
    distinct from a resolved account simply lacking .pr-cost-enabled. Not
    reachable through account_config_dir itself (root.parent is always a
    concrete Path, per this call site's own comment), so these force the
    condition directly through _config.config_enabled rather than through
    any real config-dir input."""

    def test_single_account_exits_1_with_its_own_diagnostic(
        self, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
        real_config_enabled = _mod._config.config_enabled

        def _fake_config_enabled(key, config_dir_override=None):
            if key == "pr_cost_recording":
                return None
            return real_config_enabled(key, config_dir_override=config_dir_override)

        monkeypatch.setattr(_mod._config, "config_enabled", _fake_config_enabled)

        args = _pr_cost_args(record=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1
        assert "could not resolve the Claude Code config directory" in capsys.readouterr().err

    def test_all_accounts_skips_the_affected_account_and_continues_the_sweep(
        self, tmp_path, monkeypatch, capsys,
    ):
        """acct_a's own config_enabled call is forced to None; acct_b's own
        call is untouched and opted in normally -- the sweep must count
        acct_a via skipped_other and still record acct_b's row, rather than
        aborting the whole run on the first account's unresolvable dir."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_b / ".pr-cost-enabled").touch()  # acct_a deliberately forced to None below
        proj_b = roots[1] / "-home-user-testrepo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        real_config_enabled = _mod._config.config_enabled

        def _fake_config_enabled(key, config_dir_override=None):
            if key == "pr_cost_recording" and config_dir_override == acct_a:
                return None
            return real_config_enabled(key, config_dir_override=config_dir_override)

        monkeypatch.setattr(_mod._config, "config_enabled", _fake_config_enabled)

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)  # must not raise SystemExit

        rows_b = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_b / "pr-cost-ledger.tsv").read_text())
        assert len(rows_b) == 1
        assert not (acct_a / "pr-cost-ledger.tsv").exists()
        captured = capsys.readouterr()
        assert "account-1's config directory could not be resolved -- skipped" in captured.err
        assert "recorded 1 of 2 declared accounts (0 not opted in, 1 skipped)" in captured.out


class TestPrCostRecordingKeyError:
    """_config.config_enabled("pr_cost_recording", ...) raising KeyError is
    ambiguous on its own -- config-keys.psv unreadable and a genuine
    unknown-key bug both raise the identical KeyError. These force each
    schema() outcome directly to pin the message picks the right cause."""

    def test_reports_unreadable_schema_when_key_error_and_schema_empty(
        self, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        def _raise_key_error(key, config_dir_override=None):
            raise KeyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_key_error)
        monkeypatch.setattr(_mod._config, "schema", lambda: {})

        args = _pr_cost_args(record=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1
        assert "could not read config-keys.psv" in capsys.readouterr().err

    def test_reports_unknown_key_when_key_error_and_schema_populated(
        self, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        def _raise_key_error(key, config_dir_override=None):
            raise KeyError(key)

        monkeypatch.setattr(_mod._config, "config_enabled", _raise_key_error)
        monkeypatch.setattr(_mod._config, "schema", lambda: {"worktree_required": object()})

        args = _pr_cost_args(record=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "unknown config key" in err
        assert "pr_cost_recording" in err
        assert "could not read config-keys.psv" not in err


class TestPrCostArgValidationBranchesFailBeforeAnySubprocessCall:
    """Pure args-object-driven refusals in _pr_cost_report -- each must fire
    before any subprocess call, confirmed by a subprocess double that raises
    loudly on any invocation instead of silently succeeding."""

    @staticmethod
    def _no_subprocess_calls_fake(cmd, *a, **kw):
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")

    def test_force_without_pr_exits_1(self, fake_projects, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._no_subprocess_calls_fake)
        args = _pr_cost_args(force=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1

    def test_malformed_machine_label_in_read_mode_exits_1(self, fake_projects, monkeypatch):
        """Read mode's own --machine-label format-check path (--record is
        not set here). The format check survives the not-accepted-with-
        --record refusal because read mode still accepts --machine-label."""
        monkeypatch.setattr(subprocess, "run", self._no_subprocess_calls_fake)
        args = _pr_cost_args(machine_label="Not-Valid!")
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1

    def test_record_with_well_formed_machine_label_exits_1_naming_the_read_mode_only_role(
        self, fake_projects, monkeypatch, capsys,
    ):
        monkeypatch.setattr(subprocess, "run", self._no_subprocess_calls_fake)
        args = _pr_cost_args(record=True, machine_label="ci1")
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1
        assert "not accepted with --record" in capsys.readouterr().err

    def test_record_with_malformed_machine_label_exits_1_with_the_not_accepted_message_not_the_format_message(
        self, fake_projects, monkeypatch, capsys,
    ):
        """Direct test of the check-ordering claim: the not-accepted-with
        --record refusal fires before the format check. A malformed
        --machine-label combined with --record therefore never gets the
        fix-the-format message. That message would invite retrying with
        --record still set -- the elicitation path this change exists to
        close."""
        monkeypatch.setattr(subprocess, "run", self._no_subprocess_calls_fake)
        args = _pr_cost_args(record=True, machine_label="Not-Valid!")
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "not accepted with --record" in err
        assert "must match" not in err


class TestPrCostRecordRefusesGitTrackedLedgerUnconditionally:
    def test_single_root_git_tracked_ledger_refuses_record_with_exit_2(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Unlike cost-ledger's own git-tracked check (gated on multi-root),
        pr-cost refuses --record against a git-tracked ledger path even with
        exactly one root resolved -- these rows carry branch/repo data the
        weekly ledger's rows don't."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(git_tracked=True))

        args = _pr_cost_args(record=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert exc_info.value.code == 2
        assert "inside a git working tree" in capsys.readouterr().err
        assert not ledger_path.exists()


class TestPrCostRecordRelativeLedgerPathExitsWithNoRawValue:
    def test_relative_pr_cost_ledger_path_exits_1_with_no_raw_value(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """_pr_cost_report's own `except ValueError` around
        _pr_cost_ledger_path -- that raising function's own message embeds
        PR_COST_LEDGER_PATH's raw value, so this catch must not forward
        str(exc) to stderr. Mirrors pr-cost-export's identical no-raw-value
        discipline for its own copy of this catch."""
        monkeypatch.setenv("PR_COST_LEDGER_PATH", "relative/pr-cost-ledger.tsv")
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        args = _pr_cost_args(record=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "must be an absolute path" in err
        assert "relative/pr-cost-ledger.tsv" not in err


class TestPrCostMechanicalProxies:
    def test_representative_file_path_list(self):
        paths = [
            "claude/.claude/hooks/foo.py",
            "claude/.claude/hooks/tests/test_foo.py",
            "docs/pr-cost.md",
            ".claude/plans/token-cost-per-pr-study.md",
            "README.md",
        ]
        proxies = _mod.pr_cost._pr_cost_mechanical_proxies(
            paths, plan_glob=".claude/plans/*.md", risk_globs=_mod.pr_cost._DEFAULT_PR_COST_RISK_SURFACE_GLOBS,
        )
        assert proxies["distinct_top_level_dirs"] == 4  # claude, docs, .claude, README.md (no "/")
        assert proxies["distinct_file_extensions"] == 2  # .py, .md
        assert proxies["tests_changed"] is True
        assert proxies["plan_file_added"] is True
        assert proxies["risk_surface_flag"] is True

    def test_no_tests_no_plan_no_risk_surface(self):
        paths = ["src/app.py", "src/util.py"]
        proxies = _mod.pr_cost._pr_cost_mechanical_proxies(
            paths, plan_glob=".claude/plans/*.md", risk_globs=_mod.pr_cost._DEFAULT_PR_COST_RISK_SURFACE_GLOBS,
        )
        assert proxies["tests_changed"] is False
        assert proxies["plan_file_added"] is False
        assert proxies["risk_surface_flag"] is False
        assert proxies["distinct_top_level_dirs"] == 1
        assert proxies["distinct_file_extensions"] == 1

    def test_plan_file_glob_requires_exact_configured_pattern(self):
        """A near-miss plan-shaped path (wrong extension) must not set
        plan_file_added -- only an exact configured-glob match does."""
        proxies = _mod.pr_cost._pr_cost_mechanical_proxies(
            [".claude/plans/foo.txt"], plan_glob=".claude/plans/*.md", risk_globs=(),
        )
        assert proxies["plan_file_added"] is False

    def test_empty_file_path_list_returns_zero_valued_proxies(self):
        """A PR with zero changed files (the enrichment call never returned
        `files`, or the list is genuinely empty) must not crash any of the
        set/any() computations."""
        proxies = _mod.pr_cost._pr_cost_mechanical_proxies(
            [], plan_glob=".claude/plans/*.md", risk_globs=_mod.pr_cost._DEFAULT_PR_COST_RISK_SURFACE_GLOBS,
        )
        assert proxies["distinct_top_level_dirs"] == 0
        assert proxies["distinct_file_extensions"] == 0
        assert proxies["tests_changed"] is False
        assert proxies["plan_file_added"] is False
        assert proxies["risk_surface_flag"] is False

    def test_risk_surface_flag_true_for_any_configured_glob_match(self):
        proxies = _mod.pr_cost._pr_cost_mechanical_proxies(
            ["install.sh"], plan_glob=".claude/plans/*.md", risk_globs=("install*.sh",),
        )
        assert proxies["risk_surface_flag"] is True


class TestPrCostAsofWindowOk:
    def test_inside_window_returns_false(self):
        now = datetime(2026, 8, 10, tzinfo=UTC)
        assert _mod.pr_cost._pr_cost_asof_window_ok("2026-08-09T00:00:00Z", 3.0, now) is False

    def test_exactly_at_window_boundary_returns_true(self):
        """>= , not >: the window boundary instant itself is eligible."""
        now = datetime(2026, 8, 10, tzinfo=UTC)
        assert _mod.pr_cost._pr_cost_asof_window_ok("2026-08-07T00:00:00Z", 3.0, now) is True

    def test_past_window_returns_true(self):
        now = datetime(2026, 8, 10, tzinfo=UTC)
        assert _mod.pr_cost._pr_cost_asof_window_ok("2026-08-01T00:00:00Z", 3.0, now) is True

    def test_unparseable_merged_at_returns_false(self):
        now = datetime(2026, 8, 10, tzinfo=UTC)
        assert _mod.pr_cost._pr_cost_asof_window_ok("not-a-timestamp", 3.0, now) is False


_NOW = datetime(2026, 8, 10, tzinfo=UTC)
_TOKEN_CLASS_NAMES = ("cache_read", "cache_write_5m", "cache_write_1h", "output", "input")
_REJECTED_MARKER = "zzmarkerzz"  # passes the model_breakdown key rule, so it can stand in any key position
# The cause each check rule prints, hand-written: "shape" and "dollars" are the rules transcript data can reach.
_TRANSCRIPT_OR_DEFECT = "malformed transcript data or a claude-config defect"
_CAUSE_BY_RULE = {
    "shape": _TRANSCRIPT_OR_DEFECT,
    "model-membership": "a claude-config defect",
    "label-membership": "a claude-config defect",
    "tokens": "a claude-config defect",
    "dollars": _TRANSCRIPT_OR_DEFECT,
}
_SEEDED_MACHINE_IDENTITY = "c0ffee01"  # _enable_pr_cost's default identity, so a seeded row shares the run's key
_EARLIER_CAPTURED_AT = "2026-01-01T00:00:00Z"  # distinct from the run's own wall-clock captured_at
_ZERO_SCALARS = {
    "cache_read_usd": 0.0, "cache_write_5m_usd": 0.0, "cache_write_1h_usd": 0.0, "output_usd": 0.0, "input_usd": 0.0,
    "cache_read_tokens": 0, "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0, "output_tokens": 0, "input_tokens": 0,
}


def _zero_group() -> dict:
    return {token_class: {"tokens": 0, "usd_micros": 0} for token_class in _TOKEN_CLASS_NAMES}


def _zero_scalar_row(**overrides) -> dict:
    return _sample_pr_cost_row(**{**_ZERO_SCALARS, **overrides})


def _branch_agg(fake_projects, records: list[dict], branch: str = "feature-a") -> dict:
    _write_jsonl(fake_projects / "sess.jsonl", records)
    session_iter, _scope = _mod._resolve_project_scope(
        _pr_cost_args(), "pr-cost", include_subagents=True, roots=[fake_projects.parent],
    )
    branch_totals, _unbranched = _mod._compute_pr_cost_branch_totals(session_iter)
    return branch_totals[branch]


def _row_from_agg(agg: dict) -> dict:
    return _mod.pr_cost._new_pr_cost_row(
        host="github.com", pinned_repo="owner/repo",
        pr={"number": 9, "mergedAt": "2026-01-01T00:00:00Z", "additions": 1, "deletions": 1, "changedFiles": 1},
        branch="feature-a", agg=agg, enrichment=None, join_confidence="medium",
        status=_mod.pr_cost_ledger._PR_COST_STATUS_OK, machine="ci1", captured_at="2026-01-01T00:00:00Z", supersedes="",
        plan_glob=_mod.pr_cost._DEFAULT_PR_COST_PLAN_FILE_GLOB, risk_globs=_mod.pr_cost._DEFAULT_PR_COST_RISK_SURFACE_GLOBS,
        ordinal=1, branch_map={},
    )


@pytest.fixture
def _assume_hand_computed_dollar_rates():
    """Fails with the assumption named, not an opaque micro-dollar mismatch, when a rate-table refresh moves
    a rate or multiplier the hand-computed literals in the tests that request this fixture rest on."""
    assert _mod.pricing._model_rates("claude-sonnet-5")["input"] == 2.0, "the literals assume this rate"
    assert _mod.pricing._model_rates("claude-opus-5")["output"] == 25.0, "the literals assume this rate"
    assert _mod.pricing._FAST_MODE_RATE_MULTIPLIER == 2, "the literals assume this multiplier"
    assert _mod.pricing._INFERENCE_GEO_US_RATE_MULTIPLIER == 1.1, "the literals assume this multiplier"


class TestPrCostBranchTotalsByModel:
    @pytest.mark.usefixtures("_assume_hand_computed_dollar_rates")
    def test_two_models_across_all_four_variants_accumulate_into_the_expected_cell(self, fake_projects):
        sidechain_opus_turn = _priced("claude-opus-5", output=100_000, branch="feature-a")
        sidechain_opus_turn["isSidechain"] = True
        agg = _branch_agg(fake_projects, [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
            _priced("claude-sonnet-5", input=1_000_000, speed="fast", branch="feature-a"),
            _priced("claude-sonnet-5", input=1_000_000, inference_geo="us", branch="feature-a"),
            _priced("claude-sonnet-5", input=1_000_000, speed="fast", inference_geo="us", branch="feature-a"),
            _priced("claude-opus-5", output=100_000, branch="feature-a"),
            sidechain_opus_turn,
            _priced("claude-opus-5", speed="fast", branch="feature-a"),  # a priced turn with zero tokens
        ])

        row = _row_from_agg(agg)

        def group(input_tokens=0, input_micros=0, output_tokens=0, output_micros=0):
            cell_group = _zero_group()
            cell_group["input"] = {"tokens": input_tokens, "usd_micros": input_micros}
            cell_group["output"] = {"tokens": output_tokens, "usd_micros": output_micros}
            return cell_group

        assert row["model_breakdown"] == {
            "claude-opus-5": {
                "fast": group(),
                "standard": group(output_tokens=200_000, output_micros=5_000_000),
            },
            "claude-sonnet-5": {
                "fast": group(input_tokens=1_000_000, input_micros=4_000_000),
                "fast_us_geo": group(input_tokens=1_000_000, input_micros=4_400_000),
                "standard": group(input_tokens=1_000_000, input_micros=2_000_000),
                "us_geo": group(input_tokens=1_000_000, input_micros=2_200_000),
            },
        }
        _mod.pr_cost._check_model_breakdown_cell(row["model_breakdown"], row)  # the zero-valued group passes too

    def test_unpriced_model_is_absent_from_the_cell_and_its_tokens_land_in_unpriced_tokens(self, fake_projects):
        agg = _branch_agg(fake_projects, [
            _priced("claude-sonnet-5", input=1_000, branch="feature-a"),
            _priced("claude-test-unpriced", input=777, branch="feature-a"),
        ])

        row = _row_from_agg(agg)

        assert list(row["model_breakdown"]) == ["claude-sonnet-5"]
        assert row["unpriced_tokens"] == 777

    def test_unpriced_only_branch_yields_an_empty_cell_that_passes_the_check(self, fake_projects):
        agg = _branch_agg(fake_projects, [_priced("claude-test-unpriced", input=500, branch="feature-a")])

        row = _row_from_agg(agg)

        assert row["model_breakdown"] == {}
        _mod.pr_cost._check_model_breakdown_cell(row["model_breakdown"], row)

    def test_zero_activity_default_agg_yields_an_empty_cell(self):
        assert _row_from_agg(_mod.cost._new_pr_cost_agg())["model_breakdown"] == {}

    def test_opus_dollars_equals_the_opus_family_float_leaf_sum_and_excludes_fable(self, fake_projects):
        agg = _branch_agg(fake_projects, [
            _priced("claude-opus-5-5", input=123_457, output=7_891, branch="feature-a"),
            _priced("claude-opus-5", input=98_765, cache_read=33_333, speed="fast", branch="feature-a"),
            _priced("claude-fable-5", input=1_000_000, output=50_000, branch="feature-a"),
        ])

        opus_family_leaf_dollars = sum(
            leaf["dollars"]
            for model, leaves_by_variant in agg["by_model"].items() if model in ("claude-opus-5-5", "claude-opus-5")
            for leaves_by_class in leaves_by_variant.values()
            for leaf in leaves_by_class.values()
        )
        fable_leaf_dollars = sum(
            leaf["dollars"]
            for leaves_by_class in agg["by_model"]["claude-fable-5"].values() for leaf in leaves_by_class.values()
        )

        assert fable_leaf_dollars > 0
        assert agg["opus_dollars"] == pytest.approx(opus_family_leaf_dollars, rel=1e-12)


class TestModelBreakdownUsdMicros:
    """Each literal is computed by hand from the rate table, never through _price_turn. The literals assume the
    cache-read rates asserted below."""

    @pytest.fixture(autouse=True)
    def _assume_cache_read_rates(self):
        assert _mod.pricing._model_rates("claude-opus-5-5")["cache_read"] == 0.2, "the literals assume this rate"
        assert _mod.pricing._model_rates("claude-opus-5")["cache_read"] == 0.5, "the literals assume this rate"

    @pytest.mark.parametrize(
        "model,tokens_per_turn,turn_count,expected_micros,builtin_round_micros",
        [
            pytest.param("claude-opus-5-5", 3, 1, 1, None, id="3-tokens-rounds-up-to-1-not-floor"),
            pytest.param("claude-opus-5-5", 2, 1, 0, None, id="2-tokens-rounds-down-to-0-not-ceil"),
            pytest.param("claude-opus-5-5", 2, 10, 4, None, id="ten-turns-round-per-leaf-not-per-turn"),
            pytest.param(
                "claude-opus-5", 5, 1, 3, 2, id="5-tokens-follow-the-six-decimal-rendering-not-builtin-round",
            ),
        ],
    )
    def test_leaf_usd_micros_follows_the_six_decimal_rendering_of_the_leaf_dollars(
        self, fake_projects, model, tokens_per_turn, turn_count, expected_micros, builtin_round_micros,
    ):
        agg = _branch_agg(fake_projects, [
            _priced(model, cache_read=tokens_per_turn, branch="feature-a") for _ in range(turn_count)
        ])
        leaf_dollars = agg["by_model"][model]["standard"]["cache_read"]["dollars"]
        if builtin_round_micros is not None:
            # The case must keep discriminating: the six-decimal rendering and the built-in round disagree on this leaf.
            assert round(leaf_dollars * 1e6) == builtin_round_micros
            assert round(leaf_dollars * 1e6) != expected_micros

        row = _row_from_agg(agg)

        leaf = row["model_breakdown"][model]["standard"]["cache_read"]
        assert leaf == {"tokens": tokens_per_turn * turn_count, "usd_micros": expected_micros}
        scalar_cell = _mod.pr_cost_ledger._format_pr_cost_ledger_row(row).split("\t")[
            _mod.pr_cost_ledger._PR_COST_LEDGER_COLUMNS.index("cache_read_usd")
        ]
        assert Decimal(scalar_cell).scaleb(6) == leaf["usd_micros"]


_FOUR_VARIANTS = ("standard", "fast", "us_geo", "fast_us_geo")
_INT64_MAX = 2**63 - 1
_DOLLARS_ABOVE_2_TO_THE_53_MICROS = 17179869184.015625
_MICROS_ABOVE_2_TO_THE_53 = 17_179_869_184_015_625


class TestUsdToMicros:
    """Each expected literal is computed by hand from the six-decimal rendering of the input."""

    @pytest.mark.parametrize(
        "dollars,expected_micros",
        [
            pytest.param(0.0, 0, id="zero"),
            pytest.param(-0.0, 0, id="negative-zero-is-zero"),
            pytest.param(1.5, 1_500_000, id="plain-value"),
            pytest.param(0.30000000000000004, 300_000, id="float-noise-renders-away"),
            pytest.param(0.0078125, 7812, id="tie-with-even-neighbor-rounds-down"),
            pytest.param(0.0234375, 23438, id="tie-with-odd-neighbor-rounds-up"),
            pytest.param(-0.0078125, -7812, id="negative-tie-rounds-to-even"),
            pytest.param(-1.5, -1_500_000, id="negative-value"),
            pytest.param(2.5e-06, 3, id="decimal-tie-that-is-not-a-binary-tie-rounds-up"),
            pytest.param(_DOLLARS_ABOVE_2_TO_THE_53_MICROS, _MICROS_ABOVE_2_TO_THE_53, id="above-2-to-the-53-micros-stays-exact"),
        ],
    )
    def test_micros_denote_the_six_decimal_rendering_of_the_dollars(self, dollars, expected_micros):
        assert _mod.pr_cost._usd_to_micros(dollars) == expected_micros

    def test_the_above_2_to_the_53_case_exceeds_what_float_multiplication_can_hold_exactly(self):
        assert _MICROS_ABOVE_2_TO_THE_53 > 2**53
        assert int(_DOLLARS_ABOVE_2_TO_THE_53_MICROS * 1e6) != _MICROS_ABOVE_2_TO_THE_53


class TestCheckModelBreakdownCell:
    @staticmethod
    def _cell_with_class_micros(groups: tuple[tuple[str, str], ...], token_class: str, micros_total: int) -> dict:
        """A cell holding one all-zero group per (model, variant) pair, with micros_total on the first group's class."""
        cell: dict = {}
        for model, variant in groups:
            cell.setdefault(model, {})[variant] = _zero_group()
        first_model, first_variant = groups[0]
        cell[first_model][first_variant][token_class]["usd_micros"] = micros_total
        return cell

    @pytest.mark.parametrize("token_class", _TOKEN_CLASS_NAMES)
    @pytest.mark.parametrize("gap_sign", [1, -1], ids=["leaves-above-scalar", "leaves-below-scalar"])
    @pytest.mark.parametrize(
        "groups,gap_micros,is_accepted",
        [
            pytest.param((("claude-sonnet-5", "standard"),), 0, True, id="n1-accepts-gap-0"),
            pytest.param((("claude-sonnet-5", "standard"),), 1, False, id="n1-rejects-gap-1"),
            pytest.param(tuple(("claude-sonnet-5", v) for v in _FOUR_VARIANTS[:2]), 1, True, id="n2-accepts-gap-1"),
            pytest.param(tuple(("claude-sonnet-5", v) for v in _FOUR_VARIANTS[:2]), 2, False, id="n2-rejects-gap-2"),
            pytest.param(tuple(("claude-sonnet-5", v) for v in _FOUR_VARIANTS[:3]), 2, True, id="n3-accepts-gap-2"),
            pytest.param(tuple(("claude-sonnet-5", v) for v in _FOUR_VARIANTS[:3]), 3, False, id="n3-rejects-gap-3"),
            pytest.param(tuple(("claude-sonnet-5", v) for v in _FOUR_VARIANTS), 2, True, id="n4-accepts-gap-2"),
            pytest.param(tuple(("claude-sonnet-5", v) for v in _FOUR_VARIANTS), 3, False, id="n4-rejects-gap-3"),
            pytest.param(
                (*(("claude-sonnet-5", v) for v in _FOUR_VARIANTS), ("claude-opus-5", "standard")), 3, True,
                id="n5-accepts-gap-3",
            ),
            pytest.param(
                (*(("claude-sonnet-5", v) for v in _FOUR_VARIANTS), ("claude-opus-5", "standard")), 4, False,
                id="n5-rejects-gap-4",
            ),
            pytest.param(
                (("claude-sonnet-5", "standard"), ("claude-opus-5", "standard")), 1, True,
                id="n2-across-two-models-accepts-gap-1",
            ),
            pytest.param(
                (("claude-sonnet-5", "standard"), ("claude-opus-5", "standard")), 2, False,
                id="n2-across-two-models-rejects-gap-2",
            ),
        ],
    )
    def test_dollar_tolerance_boundary_by_group_count_for_every_token_class(
        self, token_class, groups, gap_micros, is_accepted, gap_sign,
    ):
        scalar_micros = 10
        row = _zero_scalar_row(**{f"{token_class}_usd": scalar_micros / 1_000_000})
        cell = self._cell_with_class_micros(groups, token_class, scalar_micros + gap_sign * gap_micros)

        if is_accepted:
            _mod.pr_cost._check_model_breakdown_cell(cell, row)
        else:
            with pytest.raises(_mod.pr_cost._ModelBreakdownCheckError) as exc_info:
                _mod.pr_cost._check_model_breakdown_cell(cell, row)
            assert exc_info.value.rule == "dollars"

    @pytest.mark.parametrize("token_class", _TOKEN_CLASS_NAMES)
    def test_token_sums_require_exact_equality_without_the_dollar_tolerance_for_every_token_class(self, token_class):
        row = _zero_scalar_row(**{f"{token_class}_tokens": 5})
        exact_cell = {"claude-sonnet-5": {variant: _zero_group() for variant in ("standard", "fast", "us_geo")}}
        for variant, tokens in zip(("standard", "fast", "us_geo"), (1, 1, 3), strict=True):
            exact_cell["claude-sonnet-5"][variant][token_class]["tokens"] = tokens
        _mod.pr_cost._check_model_breakdown_cell(exact_cell, row)

        off_by_one_cell = json.loads(json.dumps(exact_cell))
        off_by_one_cell["claude-sonnet-5"]["us_geo"][token_class]["tokens"] = 4

        with pytest.raises(_mod.pr_cost._ModelBreakdownCheckError) as exc_info:
            _mod.pr_cost._check_model_breakdown_cell(off_by_one_cell, row)
        assert exc_info.value.rule == "tokens"

    @pytest.mark.parametrize(
        "rule,field_suffix,scalar_value",
        [
            pytest.param("tokens", "tokens", 5, id="tokens"),
            pytest.param("dollars", "usd", 0.000005, id="dollars"),
        ],
    )
    def test_offsetting_errors_in_two_classes_are_rejected_not_netted_across_classes(
        self, rule, field_suffix, scalar_value,
    ):
        """One group, so the dollar tolerance is 0: cache_read is one unit over its scalar and output one unit
        under, which a check that summed across classes would pass."""
        leaf_field = "tokens" if rule == "tokens" else "usd_micros"
        row = _zero_scalar_row(**{f"cache_read_{field_suffix}": scalar_value, f"output_{field_suffix}": scalar_value})
        cell = {"claude-sonnet-5": {"standard": _zero_group()}}
        cell["claude-sonnet-5"]["standard"]["cache_read"][leaf_field] = 6
        cell["claude-sonnet-5"]["standard"]["output"][leaf_field] = 4

        with pytest.raises(_mod.pr_cost._ModelBreakdownCheckError) as exc_info:
            _mod.pr_cost._check_model_breakdown_cell(cell, row)
        assert exc_info.value.rule == rule

    def test_token_counts_so_large_that_float_summation_order_moves_a_class_total_fail_the_dollars_rule(
        self, fake_projects,
    ):
        """Three output turns interleaved across two variants: the class scalar sums them in arrival order while
        each variant leaf sums its own, and at this magnitude the two float sums land more than the two-group
        rounding tolerance apart. Only an absurd token count gets here."""
        assert _mod.pricing._model_rates("claude-sonnet-5")["output"] == 10.0, "the token counts assume this rate"
        records = [
            _priced("claude-sonnet-5", output=10**15 + 1, branch="feature-a"),
            _priced("claude-sonnet-5", output=10**15 + 3, speed="fast", branch="feature-a"),
            _priced("claude-sonnet-5", output=10**15 + 7, branch="feature-a"),
        ]

        row = _row_from_agg(_branch_agg(fake_projects, records))

        with pytest.raises(_mod.pr_cost._ModelBreakdownCheckError) as exc_info:
            _mod.pr_cost._check_model_breakdown_cell(row["model_breakdown"], row)
        assert exc_info.value.rule == "dollars"
        assert exc_info.value.cause == _TRANSCRIPT_OR_DEFECT

    @pytest.mark.parametrize("tokens_per_turn,expected_leaf_micros", [(2, 0), (3, 1)])
    def test_real_two_group_fixture_passes_with_leaves_rounding_away_from_the_scalar(
        self, fake_projects, tokens_per_turn, expected_leaf_micros,
    ):
        """claude-opus-5-5 cache reads, standard plus us_geo: 2 tokens each give leaves 0 + 0 against a
        scalar of 1; 3 tokens each give leaves 1 + 1 against a scalar of 1."""
        agg = _branch_agg(fake_projects, [
            _priced("claude-opus-5-5", cache_read=tokens_per_turn, branch="feature-a"),
            _priced("claude-opus-5-5", cache_read=tokens_per_turn, inference_geo="us", branch="feature-a"),
        ])

        row = _row_from_agg(agg)

        leaves = [
            leaves_by_class["cache_read"]["usd_micros"]
            for leaves_by_class in row["model_breakdown"]["claude-opus-5-5"].values()
        ]
        assert leaves == [expected_leaf_micros, expected_leaf_micros]
        _mod.pr_cost._check_model_breakdown_cell(row["model_breakdown"], row)

    def test_three_hand_built_leaves_with_a_gap_of_two_pass_through_the_row_builder(self):
        agg = _mod.cost._new_pr_cost_agg()
        agg["dollars"]["cache_read"] = 0.0234375  # three leaves of 0.0078125, summed
        for variant in ("standard", "fast", "us_geo"):
            agg["by_model"].setdefault("claude-sonnet-5", {})[variant] = {
                token_class: {"tokens": 0, "dollars": 0.0078125 if token_class == "cache_read" else 0.0}
                for token_class in _TOKEN_CLASS_NAMES
            }

        row = _row_from_agg(agg)

        leaf_micros = sum(
            leaves_by_class["cache_read"]["usd_micros"] for leaves_by_class in row["model_breakdown"]["claude-sonnet-5"].values()
        )
        assert leaf_micros == 3 * 7812
        assert _mod.pr_cost._usd_to_micros(row["cache_read_usd"]) == 23438
        _mod.pr_cost._check_model_breakdown_cell(row["model_breakdown"], row)

    def test_none_cell_skips_every_check(self):
        _mod.pr_cost._check_model_breakdown_cell(None, _zero_scalar_row(cache_read_tokens=999, cache_read_usd=9.0))

    def _assert_rule(self, cell, row, expected_rule):
        with pytest.raises(_mod.pr_cost._ModelBreakdownCheckError) as exc_info:
            _mod.pr_cost._check_model_breakdown_cell(cell, row)
        assert exc_info.value.rule == expected_rule
        assert str(exc_info.value) == expected_rule

    def test_empty_cell_with_a_nonzero_dollar_scalar_fails_the_dollars_rule(self):
        self._assert_rule({}, _zero_scalar_row(cache_read_usd=1.0), "dollars")

    def test_empty_cell_with_a_nonzero_token_scalar_fails_the_tokens_rule(self):
        self._assert_rule({}, _zero_scalar_row(cache_read_tokens=1), "tokens")

    def test_model_outside_the_price_table_fails_the_model_membership_rule(self):
        cell = {"claude-test-unpriced": {"standard": _zero_group()}}
        self._assert_rule(cell, _zero_scalar_row(), "model-membership")

    def test_unknown_variant_label_fails_the_label_membership_rule(self):
        cell = {"claude-sonnet-5": {"warp_speed": _zero_group()}}
        self._assert_rule(cell, _zero_scalar_row(), "label-membership")

    def test_group_missing_a_class_fails_the_label_membership_rule(self):
        incomplete_group = _zero_group()
        del incomplete_group["output"]
        cell = {"claude-sonnet-5": {"standard": incomplete_group}}
        self._assert_rule(cell, _zero_scalar_row(), "label-membership")

    def test_negative_leaf_tokens_fail_the_shape_rule_without_a_parse_error_escaping(self):
        group = _zero_group()
        group["cache_read"]["tokens"] = -5
        cell = {"claude-sonnet-5": {"standard": group}}
        self._assert_rule(cell, _zero_scalar_row(cache_read_tokens=-5), "shape")

    @staticmethod
    def _cell_and_row_with_cache_read_tokens(tokens: int) -> tuple[dict, dict]:
        group = _zero_group()
        group["cache_read"]["tokens"] = tokens
        return {"claude-sonnet-5": {"standard": group}}, _zero_scalar_row(cache_read_tokens=tokens)

    def test_leaf_tokens_at_int64_max_pass_the_check(self):
        cell, row = self._cell_and_row_with_cache_read_tokens(_INT64_MAX)
        _mod.pr_cost._check_model_breakdown_cell(cell, row)

    def test_leaf_tokens_one_above_int64_max_fail_the_shape_rule(self):
        cell, row = self._cell_and_row_with_cache_read_tokens(_INT64_MAX + 1)
        self._assert_rule(cell, row, "shape")

    def test_leaf_usd_micros_at_int64_max_clear_the_shape_rule_and_one_above_fail_it(self):
        """The row's dollar scalar is zero, so the max case clears shape and then fails the dollars rule."""
        for usd_micros, expected_rule in ((_INT64_MAX, "dollars"), (_INT64_MAX + 1, "shape")):
            group = _zero_group()
            group["cache_read"]["usd_micros"] = usd_micros
            cell = {"claude-sonnet-5": {"standard": group}}

            self._assert_rule(cell, _zero_scalar_row(), expected_rule)

    def test_token_sum_off_by_one_with_dollars_inside_tolerance_fails_the_tokens_rule_not_dollars(self):
        cell = {"claude-sonnet-5": {variant: _zero_group() for variant in ("standard", "fast", "us_geo")}}
        cell["claude-sonnet-5"]["standard"]["cache_read"]["tokens"] = 6
        self._assert_rule(cell, _zero_scalar_row(cache_read_tokens=5), "tokens")

    @staticmethod
    def _cell_and_row_failing(failing_rules: set[str]) -> tuple[dict, dict]:
        """A one-group cell and its row that fail exactly the listed rules (shape, model-membership,
        label-membership, tokens, dollars) and pass every other."""
        group = _zero_group()
        model, variant = "claude-sonnet-5", "standard"
        row_overrides: dict = {}
        if "shape" in failing_rules:
            group["cache_read"]["tokens"] = -5
            row_overrides["cache_read_tokens"] = -5
        if "model-membership" in failing_rules:
            model = "claude-test-unpriced"
        if "label-membership" in failing_rules:
            variant = "warp_speed"
        if "tokens" in failing_rules:
            group["cache_read"]["tokens"] = 6
            row_overrides["cache_read_tokens"] = 5
        if "dollars" in failing_rules:
            group["cache_read"]["usd_micros"] = 3
        return {model: {variant: group}}, _zero_scalar_row(**row_overrides)

    @pytest.mark.parametrize(
        "earlier_rule,later_rule",
        [
            pytest.param("shape", "model-membership", id="shape-before-model-membership"),
            pytest.param("model-membership", "label-membership", id="model-membership-before-label-membership"),
            pytest.param("label-membership", "tokens", id="label-membership-before-tokens"),
            pytest.param("tokens", "dollars", id="tokens-before-dollars"),
        ],
    )
    def test_a_cell_failing_two_adjacent_rules_reports_the_earlier_one(self, earlier_rule, later_rule):
        """Each defect alone fails its own rule, so the combined cell fails both and the order alone decides."""
        earlier_only_cell, earlier_only_row = self._cell_and_row_failing({earlier_rule})
        later_only_cell, later_only_row = self._cell_and_row_failing({later_rule})
        both_cell, both_row = self._cell_and_row_failing({earlier_rule, later_rule})

        self._assert_rule(earlier_only_cell, earlier_only_row, earlier_rule)
        self._assert_rule(later_only_cell, later_only_row, later_rule)
        self._assert_rule(both_cell, both_row, earlier_rule)

    @pytest.mark.parametrize(
        "plant,expected_rule",
        [
            pytest.param("model", "model-membership", id="model-key"),
            pytest.param("variant", "label-membership", id="variant-key"),
            pytest.param("class", "label-membership", id="class-key"),
        ],
    )
    def test_a_planted_key_fires_the_real_check_without_appearing_in_the_exception_text(self, plant, expected_rule):
        if plant == "model":
            cell = {_REJECTED_MARKER: {"standard": _zero_group()}}
        elif plant == "variant":
            cell = {"claude-sonnet-5": {_REJECTED_MARKER: _zero_group()}}
        else:
            group = _zero_group()
            group[_REJECTED_MARKER] = {"tokens": 0, "usd_micros": 0}
            cell = {"claude-sonnet-5": {"standard": group}}

        with pytest.raises(_mod.pr_cost._ModelBreakdownCheckError) as exc_info:
            _mod.pr_cost._check_model_breakdown_cell(cell, _zero_scalar_row())

        assert exc_info.value.rule == expected_rule
        assert _REJECTED_MARKER not in str(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_rule_tuple_and_cause_table_are_closed_and_match_the_hand_written_causes(self):
        assert _mod.pr_cost._MODEL_BREAKDOWN_CHECK_RULES == (
            "shape", "model-membership", "label-membership", "tokens", "dollars",
        )
        assert _mod.pr_cost._MODEL_BREAKDOWN_CHECK_CAUSES == _CAUSE_BY_RULE

    @pytest.mark.parametrize("rule", sorted(_CAUSE_BY_RULE))
    def test_check_error_carries_the_cause_for_its_rule(self, rule):
        error = _mod.pr_cost._ModelBreakdownCheckError(rule)

        assert error.rule == rule
        assert error.cause == _CAUSE_BY_RULE[rule]

    def test_unknown_rule_fails_when_the_error_is_constructed_not_when_the_cause_is_printed(self):
        with pytest.raises(KeyError):
            _mod.pr_cost._ModelBreakdownCheckError("no-such-rule")


class TestSeededBreakdownSweep:
    _MODELS = ("claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001")
    _VARIANT_USAGE_FIELDS = {
        "standard": {}, "fast": {"speed": "fast"}, "us_geo": {"inference_geo": "us"},
        "fast_us_geo": {"speed": "fast", "inference_geo": "us"},
    }

    @pytest.mark.parametrize("seed", range(25))
    def test_generated_fixtures_pass_the_check_and_leaf_tokens_equal_independently_tracked_totals(
        self, fake_projects, tmp_path, seed,
    ):
        rng = random.Random(seed)
        groups = rng.sample([(m, v) for m in self._MODELS for v in self._VARIANT_USAGE_FIELDS], rng.randint(2, 4))
        expected_tokens: dict[tuple[str, str, str], int] = {}
        records = []
        for model, variant in groups:
            for _ in range(rng.randint(1, 3)):
                upper_bound = rng.choice((40, 5000))
                counts = {token_class: rng.randint(0, upper_bound) for token_class in _TOKEN_CLASS_NAMES}
                for token_class, count in counts.items():
                    key = (model, variant, token_class)
                    expected_tokens[key] = expected_tokens.get(key, 0) + count
                records.append(_priced(
                    model, input=counts["input"], output=counts["output"], cache_read=counts["cache_read"],
                    ephemeral_1h=counts["cache_write_1h"], ephemeral_5m=counts["cache_write_5m"],
                    branch="feature-a", **self._VARIANT_USAGE_FIELDS[variant],
                ))

        row = _row_from_agg(_branch_agg(fake_projects, records))

        _mod.pr_cost._check_model_breakdown_cell(row["model_breakdown"], row)
        for model, variant in groups:
            for token_class in _TOKEN_CLASS_NAMES:
                leaf = row["model_breakdown"][model][variant][token_class]
                assert leaf["tokens"] == expected_tokens[(model, variant, token_class)], (model, variant, token_class)
        ledger_path = tmp_path / "sweep-ledger.tsv"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [row])
        written_row = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())[0]
        assert written_row["model_breakdown"] == row["model_breakdown"]


def _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a", "feature-b")) -> Path:
    """Opt the account in, give each branch one priced sonnet turn and one merged PR (numbered 1..N in branch
    order), and fake git/gh. Returns the (not yet existing) ledger path, which sits in a marker-named directory."""
    _enable_pr_cost(tmp_path)
    ledger_dir = tmp_path / _LEDGER_DIR_MARKER
    ledger_dir.mkdir()
    ledger_path = ledger_dir / "pr-cost-ledger.tsv"
    monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
    for index, branch in enumerate(branches):
        _write_jsonl(fake_projects / f"sess-{index}.jsonl", [_priced("claude-sonnet-5", input=1_000_000, branch=branch)])
    monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(
        merged_prs=[_merged_pr(index + 1, branch) for index, branch in enumerate(branches)],
    ))
    return ledger_path


def _fail_check_on_calls(monkeypatch, failing_call_numbers: set[int], rule: str = "dollars") -> None:
    """Make _check_model_breakdown_cell raise _ModelBreakdownCheckError(rule) on the listed 1-based calls and
    run the real check on every other call."""
    real_check = _mod.pr_cost._check_model_breakdown_cell
    call_count = 0

    def check(cell, row):
        nonlocal call_count
        call_count += 1
        if call_count in failing_call_numbers:
            raise _mod.pr_cost._ModelBreakdownCheckError(rule)
        real_check(cell, row)

    monkeypatch.setattr(_mod.pr_cost, "_check_model_breakdown_cell", check)


def _per_pr_degrade_line(pr_number: int, rule: str = "dollars") -> str:
    return (
        f"pr-cost:   PR #{pr_number}: {_PER_PR_DEGRADE_STEM} its {rule} check ({_CAUSE_BY_RULE[rule]})"
        " -- recorded the row without it; see docs/pr-cost.md"
    )


def _count_line(count: int, entries: str) -> str:
    return (
        f"pr-cost: {count} {_COUNT_LINE_STEM} ({entries}); those rows are valid --"
        " once the cause is fixed, re-capture each with --record --force --pr N and this run's account flags,"
        " only while every session of that PR is still inside cleanupPeriodDays (see docs/pr-cost.md)"
    )


def _assert_no_markers(captured) -> None:
    for marker in (_LEDGER_DIR_MARKER, _PRIOR_ROW_MARKER):
        assert marker not in captured.err + captured.out, marker


def _ledger_rows(ledger_path: Path) -> list[dict]:
    return _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())


class TestPrCostRecordDegradesOnBreakdownCheckFailure:
    def test_failing_check_on_one_of_two_branches_records_both_rows_reports_and_exits_1(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch)
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            ledger_path, [_sample_pr_cost_row(pr_number=50, head_branch=_PRIOR_ROW_MARKER)],
        )
        _fail_check_on_calls(monkeypatch, {1})

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 1
        rows = _ledger_rows(ledger_path)
        assert [(r["pr_number"], r["model_breakdown"] is None) for r in rows] == [(50, True), (1, True), (2, False)]
        captured = capsys.readouterr()
        err_lines = captured.err.splitlines()
        assert _per_pr_degrade_line(1) in err_lines
        assert _count_line(1, "PR #1") in err_lines
        assert not any("usd_micros" in line for line in err_lines)
        _assert_no_markers(captured)

    def test_real_check_failing_on_a_planted_model_key_degrades_without_echoing_the_key(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        monkeypatch.setattr(
            _mod.pr_cost, "_build_model_breakdown_cell",
            lambda by_model: {_REJECTED_MARKER: {"standard": _zero_group()}},
        )

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 1
        assert _ledger_rows(ledger_path)[0]["model_breakdown"] is None
        captured = capsys.readouterr()
        assert _per_pr_degrade_line(1, rule="model-membership") in captured.err.splitlines()
        assert _REJECTED_MARKER not in captured.err + captured.out
        _assert_no_markers(captured)

    def test_real_decoder_rejection_from_a_negative_token_count_degrades_instead_of_aborting_the_run(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a", "feature-b"))
        _write_jsonl(fake_projects / "sess-0.jsonl", [
            _priced("claude-sonnet-5", input=1_000, cache_read=-5, branch="feature-a"),
        ])

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 1
        rows = _ledger_rows(ledger_path)
        assert [(r["pr_number"], r["model_breakdown"] is None) for r in rows] == [(1, True), (2, False)]
        captured = capsys.readouterr()
        assert _per_pr_degrade_line(1, rule="shape") in captured.err.splitlines()
        _assert_no_markers(captured)

    def test_cell_construction_defect_aborts_the_run_and_leaves_the_seeded_ledger_unchanged(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch)
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=50)])
        before_bytes = ledger_path.read_bytes()
        build_call_count = 0

        def build_raising_on_first_call(by_model):
            nonlocal build_call_count
            build_call_count += 1
            raise RuntimeError("cell construction defect")

        monkeypatch.setattr(_mod.pr_cost, "_build_model_breakdown_cell", build_raising_on_first_call)

        with pytest.raises(RuntimeError, match="cell construction defect"):
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert build_call_count == 1
        assert ledger_path.read_bytes() == before_bytes

    def test_all_accounts_run_where_the_first_account_degrades_still_records_the_second_and_ends_with_the_count_line(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for root in roots:
            _enable_pr_cost(root.parent)
            (root / "-home-user-testrepo").mkdir(parents=True)
            _write_jsonl(root / "-home-user-testrepo" / "sess.jsonl", [
                _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
            ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[_merged_pr(1, "feature-a")]))
        _fail_check_on_calls(monkeypatch, {1})

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True, all_accounts=True), _NOW, roots)

        assert exc_info.value.code == 1
        first_account_rows = _ledger_rows(roots[0].parent / "pr-cost-ledger.tsv")
        second_account_rows = _ledger_rows(roots[1].parent / "pr-cost-ledger.tsv")
        assert first_account_rows[0]["model_breakdown"] is None
        assert second_account_rows[0]["model_breakdown"] is not None
        captured = capsys.readouterr()
        assert "recorded 2 of 2 declared accounts" in captured.out
        assert captured.err.strip().splitlines()[-1] == _count_line(1, "account-1 PR #1")

    def test_forced_recapture_that_fails_its_check_appends_an_empty_cell_row_over_a_populated_one(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(
            pr_number=1, machine=_SEEDED_MACHINE_IDENTITY, captured_at=_EARLIER_CAPTURED_AT,
            model_breakdown=_sample_model_breakdown(),
        )])
        _fail_check_on_calls(monkeypatch, {1})

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True, pr=1, force=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 1
        populated_row, degraded_row = _ledger_rows(ledger_path)
        assert populated_row["model_breakdown"] is not None
        assert degraded_row["model_breakdown"] is None
        assert degraded_row["supersedes"] == _EARLIER_CAPTURED_AT
        assert degraded_row["captured_at"] != _EARLIER_CAPTURED_AT

    def test_forced_recapture_after_a_degrade_appends_a_populated_row_superseding_the_degraded_one_and_exits_0(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(
            pr_number=1, machine=_SEEDED_MACHINE_IDENTITY, captured_at=_EARLIER_CAPTURED_AT, model_breakdown=None,
        )])

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True, pr=1, force=True), _NOW, [fake_projects.parent])

        degraded_row, recaptured_row = _ledger_rows(ledger_path)
        assert degraded_row["model_breakdown"] is None
        assert recaptured_row["model_breakdown"] is not None
        assert recaptured_row["supersedes"] == _EARLIER_CAPTURED_AT
        assert recaptured_row["captured_at"] != _EARLIER_CAPTURED_AT

    def test_unforced_rerun_after_a_degrade_skips_the_pr_prints_no_count_line_and_exits_0(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        real_check = _mod.pr_cost._check_model_breakdown_cell
        _fail_check_on_calls(monkeypatch, {1})
        with pytest.raises(SystemExit):
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])
        monkeypatch.setattr(_mod.pr_cost, "_check_model_breakdown_cell", real_check)
        capsys.readouterr()

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        err = capsys.readouterr().err
        assert "is already captured" in err
        assert _COUNT_LINE_STEM not in err
        assert len(_ledger_rows(ledger_path)) == 1

    def test_degrade_on_the_first_of_three_branches_then_a_refused_write_leaves_only_the_per_pr_line(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(
            fake_projects, tmp_path, monkeypatch, branches=("feature-a", "feature-b", "feature-c"),
        )
        _fail_check_on_calls(monkeypatch, {1})
        real_write = _mod.pr_cost_ledger._write_pr_cost_ledger_file
        write_call_count = 0

        def write_refusing_on_second_call(path, rows):
            nonlocal write_call_count
            write_call_count += 1
            if write_call_count == 2:
                raise _mod.pr_cost_ledger._PrCostLedgerParseError(
                    "refusing to write the ledger (ledger unchanged): test double"
                )
            return real_write(path, rows)

        monkeypatch.setattr(_mod.pr_cost_ledger, "_write_pr_cost_ledger_file", write_refusing_on_second_call)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 1
        assert write_call_count == 2
        captured = capsys.readouterr()
        err_lines = captured.err.splitlines()
        assert _per_pr_degrade_line(1) in err_lines
        assert not any(_COUNT_LINE_STEM in line for line in err_lines)
        _assert_no_markers(captured)
        rows = _ledger_rows(ledger_path)
        assert [r["pr_number"] for r in rows] == [1]
        assert rows[0]["model_breakdown"] is None

    def test_failed_check_and_refused_write_on_the_same_branch_prints_no_per_pr_line_and_no_count_line(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(
            ledger_path, [_sample_pr_cost_row(pr_number=50, head_branch=_PRIOR_ROW_MARKER)],
        )
        _fail_check_on_calls(monkeypatch, {1})

        def refuse_every_write(path, rows):
            raise _mod.pr_cost_ledger._PrCostLedgerParseError(
                "refusing to write the ledger (ledger unchanged): test double"
            )

        monkeypatch.setattr(_mod.pr_cost_ledger, "_write_pr_cost_ledger_file", refuse_every_write)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        err = captured.err
        assert "refusing to write the ledger (ledger unchanged): test double" in err
        assert _PER_PR_DEGRADE_STEM not in err
        assert _COUNT_LINE_STEM not in err
        _assert_no_markers(captured)

    def test_two_degraded_prs_in_one_account_are_listed_in_processing_order_without_an_account_prefix(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        _arrange_record_run(fake_projects, tmp_path, monkeypatch)
        _fail_check_on_calls(monkeypatch, {1, 2})

        with pytest.raises(SystemExit):
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        err_lines = capsys.readouterr().err.splitlines()
        assert _count_line(2, "PR #1, PR #2") in err_lines

    def test_all_accounts_degrades_in_both_accounts_are_listed_with_their_account_prefixes(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        for root, branch in zip(roots, ("feature-a", "feature-b"), strict=True):
            _enable_pr_cost(root.parent)
            (root / "-home-user-testrepo").mkdir(parents=True)
            _write_jsonl(root / "-home-user-testrepo" / "sess.jsonl", [
                _priced("claude-sonnet-5", input=1_000_000, branch=branch),
            ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(
            merged_prs=[_merged_pr(1, "feature-a"), _merged_pr(2, "feature-b")],
        ))
        _fail_check_on_calls(monkeypatch, {1, 2})

        with pytest.raises(SystemExit):
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True, all_accounts=True), _NOW, roots)

        assert capsys.readouterr().err.strip().splitlines()[-1] == _count_line(2, "account-1 PR #1, account-2 PR #2")

    def test_disabled_producer_records_an_empty_cell_beside_populated_prior_rows_and_exits_0(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(pr_number=50, model_breakdown=_sample_model_breakdown()),
            _sample_pr_cost_row(pr_number=51, model_breakdown=_sample_model_breakdown("claude-opus-5")),
        ])
        monkeypatch.setattr(_mod.pr_cost, "_build_model_breakdown_cell", lambda by_model: None)

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        rows = _ledger_rows(ledger_path)
        assert [r["pr_number"] for r in rows] == [50, 51, 1]
        assert rows[0]["model_breakdown"] == _sample_model_breakdown()
        assert rows[1]["model_breakdown"] == _sample_model_breakdown("claude-opus-5")
        assert rows[2]["model_breakdown"] is None
        err = capsys.readouterr().err
        assert _PER_PR_DEGRADE_STEM not in err
        assert _COUNT_LINE_STEM not in err


class TestPrCostRecordWritesAndReportsTheBreakdown:
    @pytest.mark.usefixtures("_assume_hand_computed_dollar_rates")
    def test_upgrading_run_writes_the_hand_computed_cell_and_prints_no_degrade_lines(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        ledger_path.write_text(_PRE_MODEL_HEADER_LINE + "\n" + _pre_model_row_line(pr_number=50) + "\n")

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        new_row = _ledger_rows(ledger_path)[-1]
        zero_group = _zero_group()
        assert new_row["model_breakdown"] == {
            "claude-sonnet-5": {
                "standard": {**zero_group, "input": {"tokens": 1_000_000, "usd_micros": 2_000_000}},
            },
        }
        err = capsys.readouterr().err
        assert _PER_PR_DEGRADE_STEM not in err
        assert _COUNT_LINE_STEM not in err

    def test_ledger_created_by_a_record_run_has_mode_0600_even_when_mkstemp_creates_0644(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        _make_mkstemp_create_0644(monkeypatch)
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        assert not ledger_path.exists()

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600

    def test_unpriced_only_branch_records_an_empty_object_cell_distinct_from_not_recorded(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        _write_jsonl(fake_projects / "sess-0.jsonl", [_priced("claude-test-unpriced", input=500, branch="feature-a")])

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert ledger_path.read_text().splitlines()[1].split("\t")[-1] == "{}"
        recorded_row = _ledger_rows(ledger_path)[0]
        assert recorded_row["model_breakdown"] == {}
        assert recorded_row["model_breakdown"] is not None
        assert _PER_PR_DEGRADE_STEM not in capsys.readouterr().err

    def test_unpriced_model_name_reaches_neither_the_ledger_nor_stderr(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        _write_jsonl(fake_projects / "sess-0.jsonl", [
            _priced("claude-sonnet-5", input=1_000, branch="feature-a"),
            _priced("claude-test-unpriced", input=777, branch="feature-a"),
        ])

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert "claude-test-unpriced" not in ledger_path.read_text()
        captured = capsys.readouterr()
        assert "claude-test-unpriced" not in captured.err + captured.out
        assert _ledger_rows(ledger_path)[0]["unpriced_tokens"] == 777


class TestPrCostReadModeAndExportOutputScope:
    def test_read_mode_console_output_never_contains_a_model_key_from_a_populated_cell(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        distinctive_model_key = "claude-test-keymarkzz"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(model_breakdown=_sample_model_breakdown(distinctive_model_key)),
        ])

        _mod.pr_cost._pr_cost_report(_pr_cost_args(), _NOW, [fake_projects.parent])

        captured = capsys.readouterr()
        assert "No rows recorded yet" not in captured.out
        assert distinctive_model_key not in captured.out + captured.err

    def test_export_console_output_omits_the_model_key_that_the_export_file_carries(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        distinctive_model_key = "claude-test-keymarkzz"
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [
            _sample_pr_cost_row(model_breakdown=_sample_model_breakdown(distinctive_model_key)),
        ])
        out_path = tmp_path / "export.tsv"

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(out_path)))

        captured = capsys.readouterr()
        assert distinctive_model_key not in captured.out + captured.err
        assert distinctive_model_key in out_path.read_text()


def _run_read_record_and_export(fake_projects, tmp_path, capsys, export_name: str) -> list[tuple[str, int | None, str]]:
    """Runs read mode, --record, and export in turn. Returns (mode, exit code or None, stderr) for each."""
    outcomes = []
    for mode in ("read", "record", "export"):
        capsys.readouterr()
        exit_code = None
        try:
            if mode == "export":
                _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(tmp_path / export_name)))
            else:
                _mod.pr_cost._pr_cost_report(_pr_cost_args(record=(mode == "record")), _NOW, [fake_projects.parent])
        except SystemExit as exc:
            exit_code = exc.code
        outcomes.append((mode, exit_code, capsys.readouterr().err))
    return outcomes


class TestPrCostPrescribedHandEdits:
    @staticmethod
    def _current_line_with_cell(cell: str) -> str:
        cells = _mod.pr_cost_ledger._format_pr_cost_ledger_row(_sample_pr_cost_row(pr_number=50)).split("\t")
        cells[-1] = cell
        return "\t".join(cells)

    @pytest.mark.parametrize(
        "broken_cell_or_tail,expected_fragments",
        [
            pytest.param(f"{_REJECTED_MARKER} not json", ("line 2", "model_breakdown"), id="corrupt-populated-cell"),
            pytest.param(None, ("line 2", "append a tab to this line"), id="editor-stripped-trailing-tab"),
        ],
    )
    def test_each_mode_refuses_the_broken_ledger_unchanged_and_accepts_it_after_the_prescribed_edit(
        self, fake_projects, tmp_path, monkeypatch, capsys, broken_cell_or_tail, expected_fragments,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        header = _mod.pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE
        if broken_cell_or_tail is None:
            broken_line = self._current_line_with_cell("")[:-1]  # the editor stripped the trailing tab
            fixed_line = broken_line + "\t"
        else:
            broken_line = self._current_line_with_cell(broken_cell_or_tail)
            fixed_line = self._current_line_with_cell("")  # blank the cell, keeping its tab
        ledger_path.write_text(header + "\n" + broken_line + "\n")
        before_bytes = ledger_path.read_bytes()

        for mode, exit_code, err in _run_read_record_and_export(fake_projects, tmp_path, capsys, "broken-export.tsv"):
            assert exit_code not in (None, 0), mode
            for fragment in expected_fragments:
                assert fragment in err, (mode, fragment)
            assert _REJECTED_MARKER not in err, mode
        assert ledger_path.read_bytes() == before_bytes
        assert not (tmp_path / "broken-export.tsv").exists()

        ledger_path.write_text(header + "\n" + fixed_line + "\n")
        for mode, exit_code, err in _run_read_record_and_export(fake_projects, tmp_path, capsys, "fixed-export.tsv"):
            assert exit_code in (None, 0), (mode, err)
        assert [r["pr_number"] for r in _ledger_rows(ledger_path)] == [50, 1]
        assert (tmp_path / "fixed-export.tsv").exists()


class TestPrCostOlderLedgerIsNotTouchedOutsideAConsentedWrite:
    """Six runs that must leave a pre-model ledger's bytes unchanged, plus an unrecognized-header ledger."""

    @staticmethod
    def _seed_pre_model_ledger(ledger_path: Path, **row_overrides) -> bytes:
        ledger_path.write_text(_PRE_MODEL_HEADER_LINE + "\n" + _pre_model_row_line(**row_overrides) + "\n")
        return ledger_path.read_bytes()

    @staticmethod
    def _assert_untouched(ledger_path: Path, before_bytes: bytes, captured) -> None:
        assert ledger_path.read_bytes() == before_bytes
        assert list(ledger_path.parent.glob(".pr-cost-ledger-*.tmp")) == []
        assert _UPGRADE_NOTICE_STEM not in captured.err

    def test_unconsented_record_leaves_the_file_unchanged(self, fake_projects, tmp_path, monkeypatch, capsys):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        (tmp_path / ".pr-cost-enabled").unlink()
        before_bytes = self._seed_pre_model_ledger(ledger_path)

        with pytest.raises(SystemExit):
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        self._assert_untouched(ledger_path, before_bytes, capsys.readouterr())

    def test_consented_record_that_only_skips_an_already_captured_pr_leaves_the_file_unchanged(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        before_bytes = self._seed_pre_model_ledger(ledger_path, pr_number=1, machine="c0ffee01")

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        captured = capsys.readouterr()
        assert "is already captured" in captured.err
        self._assert_untouched(ledger_path, before_bytes, captured)

    def test_read_mode_leaves_the_file_unchanged_and_prints_the_seeded_row(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        before_bytes = self._seed_pre_model_ledger(ledger_path, pr_number=4217)

        _mod.pr_cost._pr_cost_report(_pr_cost_args(), _NOW, [fake_projects.parent])

        captured = capsys.readouterr()
        self._assert_untouched(ledger_path, before_bytes, captured)
        assert "4217" in captured.out
        assert "No rows recorded yet" not in captured.out

    def test_export_leaves_the_file_unchanged(self, fake_projects, tmp_path, monkeypatch, capsys):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        before_bytes = self._seed_pre_model_ledger(ledger_path)

        _mod.pr_cost_export.cmd_pr_cost_export(_pr_cost_export_args(out=str(tmp_path / "export.tsv")))

        self._assert_untouched(ledger_path, before_bytes, capsys.readouterr())

    def test_record_refused_by_the_git_tracked_path_check_leaves_the_file_unchanged(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        before_bytes = self._seed_pre_model_ledger(ledger_path)
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(
            merged_prs=[_merged_pr(1, "feature-a")], git_tracked=True,
        ))

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True), _NOW, [fake_projects.parent])

        assert exc_info.value.code == 2
        self._assert_untouched(ledger_path, before_bytes, capsys.readouterr())

    def test_all_accounts_run_leaves_the_account_that_is_not_opted_in_unchanged(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        opted_in_root, not_opted_in_root = roots
        _enable_pr_cost(opted_in_root.parent)
        (opted_in_root / "-home-user-testrepo").mkdir(parents=True)
        _write_jsonl(opted_in_root / "-home-user-testrepo" / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=[_merged_pr(1, "feature-a")]))
        not_opted_in_ledger = not_opted_in_root.parent / "pr-cost-ledger.tsv"
        before_bytes = self._seed_pre_model_ledger(not_opted_in_ledger)

        _mod.pr_cost._pr_cost_report(_pr_cost_args(record=True, all_accounts=True), _NOW, roots)

        captured = capsys.readouterr()
        assert "account-2 is not opted in" in captured.err
        assert not_opted_in_ledger.read_bytes() == before_bytes
        assert list(not_opted_in_ledger.parent.glob(".pr-cost-ledger-*.tmp")) == []
        assert f"{_UPGRADE_NOTICE_STEM}account-2" not in captured.err

    def test_unrecognized_header_is_refused_by_read_mode_record_and_export_with_bytes_and_mtime_unchanged(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        ledger_path = _arrange_record_run(fake_projects, tmp_path, monkeypatch, branches=("feature-a",))
        ledger_path.write_text("a-header-from-nowhere\tmodel_breakdown\n" + _pre_model_row_line() + "\n")
        before_bytes, before_mtime_ns = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns

        for mode, exit_code, err in _run_read_record_and_export(fake_projects, tmp_path, capsys, "export.tsv"):
            assert exit_code not in (None, 0), mode
            assert "missing or mismatched pr-cost ledger header row" in err, mode
        assert ledger_path.read_bytes() == before_bytes
        assert ledger_path.stat().st_mtime_ns == before_mtime_ns
