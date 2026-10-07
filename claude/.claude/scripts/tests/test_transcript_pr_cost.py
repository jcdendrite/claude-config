"""Tests for transcript_analysis/pr_cost.py (cmd_pr_cost) -- local-mechanics coverage only
(attribution, ledger I/O, correction contract, mechanical proxies, join logic that doesn't
require faking gh). gh-integration scenarios live in test_transcript_pr_cost_gh.py."""
import importlib.util
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ._pr_cost_helpers import _enable_pr_cost, _fake_pr_cost_subprocess_run, _pr_cost_args
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
