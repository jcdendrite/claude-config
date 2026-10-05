"""Tests for subagent_mix.py's Actual $ and Counterfactual $ columns and _dispatch_usage_summary."""
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from ._subagent_helpers import (
    _subagent_mix_args,
)
from .conftest import (
    _agent_use,
    _asst,
    _bash_use,
    _cost_args,
    _extract_grand_total,
    _priced_sidechain_asst,
    _table_cols,
    _tool_result,
    _write_jsonl,
    _write_subagent_dispatch,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestSubagentMixDollars:
    """The model-mix table's Actual$/Counterfactual$/Delta columns
    (_dispatch_usage_summary), including --since-date/--until-date's
    per-record (not per-dispatch) window and --reprice-as's counterfactual
    pricing."""

    def test_actual_dollars_match_hand_computed_usage(self, fake_projects, capsys):
        """1,000,000 input tokens at claude-sonnet-4-6's $3.00/MTok base rate
        prices to exactly $3.00, with every other usage field at zero."""
        session_id = "sess-actual"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$3.00"

    def test_reprice_as_delta_arithmetic(self, fake_projects, capsys):
        """The same 1,000,000-input-token dispatch re-priced at
        claude-haiku-4-5-20251001's $1.00/MTok rate: Actual $3.00,
        Counterfactual $1.00, Delta (Actual − Counterfactual) $2.00."""
        session_id = "sess-reprice"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(reprice_as="claude-haiku-4-5-20251001"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=7)
        assert cols["Actual$"] == "$3.00"
        assert cols["Counterfactual$"] == "$1.00"
        assert cols["Delta"] == "$2.00"

    def test_reprice_as_same_model_yields_zero_delta(self, fake_projects, capsys):
        """--reprice-as set to the dispatch's own real model must not diverge
        from the actual-dollars path -- Delta is exactly $0.00, not merely
        close to it, since both columns price the identical usage at the
        identical model ID."""
        session_id = "sess-reprice-same"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=500)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(reprice_as="claude-sonnet-4-6"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=7)
        assert cols["Actual$"] == cols["Counterfactual$"]
        assert cols["Delta"] == "$0.00"

    def test_invalid_reprice_as_value_exits_nonzero_listing_valid_ids(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(reprice_as="not-a-real-model"))
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "subagent-mix: --reprice-as" in err
        assert "not-a-real-model" in err
        assert "claude-opus-5" in err  # one of _MODEL_BASE_INPUT_RATES' listed valid IDs

    def test_since_date_boundary_is_inclusive(self, fake_projects, capsys):
        """A sidechain record timestamped exactly at --since-date's own
        day-start instant is included, not excluded -- the [since_ts, ...)
        lower bound is inclusive."""
        session_id = "sess-since-boundary"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst(
                "claude-sonnet-4-6", input_tokens=1_000_000, ts="2026-07-01T00:00:00.000Z",
            )],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(since_date="2026-07-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$3.00"

    def test_until_date_boundary_is_exclusive(self, fake_projects, capsys):
        """A sidechain record timestamped exactly at --until-date's own
        day-after instant (the [..., until_ts) upper bound) is excluded, not
        included -- the dispatch itself still counts as a Run since window
        filtering scopes only the dollar columns, not Runs/Observed."""
        session_id = "sess-until-boundary"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst(
                "claude-sonnet-4-6", input_tokens=1_000_000, ts="2026-07-02T00:00:00.000Z",
            )],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(until_date="2026-07-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Runs"] == "1"
        assert cols["Actual$"] == "$0.00"

    def test_boundary_straddling_dispatch_prices_only_in_window_records(self, fake_projects, capsys):
        """A single dispatch's own sidechain straddles --until-date: one
        record before the cutoff, one after. Only the before-cutoff record's
        usage may be priced into Actual $ -- a per-dispatch (rather than
        per-record) filter would either price the whole $12.00 sidechain or
        none of it, never the correct $3.00 in-window slice. Direct
        regression test for _dispatch_usage_summary's per-deduped-turn filtering."""
        session_id = "sess-straddle"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [
                _priced_sidechain_asst(
                    "claude-sonnet-4-6", input_tokens=1_000_000, ts="2026-07-01T00:00:00.000Z",
                ),
                _priced_sidechain_asst(
                    "claude-sonnet-4-6", input_tokens=3_000_000, ts="2026-07-02T00:00:00.000Z",
                ),
            ],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(until_date="2026-07-01"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$3.00"

    def test_synthetic_only_sidechain_renders_zero_dollars(self, fake_projects, capsys):
        """A sidechain whose only recorded model is the literal "<synthetic>"
        has no priced usage at all -- Actual $ renders "$0.00", never a crash
        or a bare "None"."""
        session_id = "sess-synthetic-dollars"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_asst("<synthetic>", branch="main", sidechain=True)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())  # must not raise
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$0.00"

    def test_dollar_totals_not_merged_across_roots_under_multi_root_redaction(
        self, fake_projects, fake_config_dir_factory, capsys
    ):
        """The model-mix table is keyed on the redacted (root, subagent_type)
        label -- two accounts' same-named "staff-sdet" dispatches must each
        keep their own Actual $ total, never summed into one merged row that
        blends two accounts' dollar figures."""
        session_id = "sess-a"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        session_id_b = "sess-b"
        _write_jsonl(proj_b / f"{session_id_b}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("b1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            proj_b, session_id_b, "agent-b1", "b1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=2_000_000)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        # _redaction_ordinals sorts by resolved path, not scan/insertion order,
        # so which physical root lands on account-1 vs account-2 isn't asserted
        # here -- only that the two accounts' dollar totals stay distinct
        # (never summed into one merged $9.00 row).
        cols_a = _table_cols(
            out, header_contains="Actual$", row_contains="account-1/agent-type-1",
            row_startswith=True, max_labels=5,
        )
        cols_b = _table_cols(
            out, header_contains="Actual$", row_contains="account-2/agent-type-1",
            row_startswith=True, max_labels=5,
        )
        assert {cols_a["Actual$"], cols_b["Actual$"]} == {"$3.00", "$6.00"}

    def test_reprice_as_more_expensive_model_yields_negative_delta(self, fake_projects, capsys):
        """--reprice-as a model *pricier* than the dispatch's own real model
        (a realistic use case: "what would this have cost on Opus?") must
        render Delta with the conventional -$N.NN form, not $-N.NN -- covers
        _fmt_usd's negative branch, which every other reprice test in this
        class leaves unexercised since they all reprice to something
        cheaper or identical."""
        session_id = "sess-reprice-pricier"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args(reprice_as="claude-opus-5"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=7)
        assert cols["Actual$"] == "$3.00"
        assert cols["Counterfactual$"] == "$5.00"
        assert cols["Delta"] == "-$2.00"

    def test_actual_dollars_sum_across_multiple_dispatches_of_same_agent_type(self, fake_projects, capsys):
        """Two separate dispatches of the same agent_type under one root must
        accumulate into one row's Actual$ total (row["actual_dollars"] +=),
        not overwrite or double-count -- the multi-root test above never
        exercises this since it keeps exactly one dispatch per account."""
        session_id = "sess-multi-dispatch"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-sdet"), _agent_use("a2", "staff-sdet"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=2_000_000)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Runs"] == "2"
        assert cols["Actual$"] == "$9.00"

    def test_actual_dollars_sum_across_mixed_model_dispatches_equals_hand_computed_total(
        self, fake_projects, capsys,
    ):
        """Per-dispatch dollars are priced at each dispatch's own model rate
        and summed, across dispatches on different models sharing one
        agent_type."""
        session_id = "sess-mixed-model-dispatch"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[
                _agent_use("a1", "staff-sdet"), _agent_use("a2", "staff-sdet"),
            ]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst("claude-sonnet-4-6", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-2", "a2",
            [_priced_sidechain_asst("claude-opus-4-8", input_tokens=1_000_000)],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Runs"] == "2"
        # claude-sonnet-4-6 ($3.00/MTok) + claude-opus-4-8 ($5.00/MTok), each
        # dispatch priced independently at its own model's rate before
        # summing into the row -- not one rate applied to the combined total.
        assert cols["Actual$"] == "$8.00"

    def test_unpriced_turn_surfaced_not_silently_zero(self, fake_projects, capsys):
        """A turn whose model ID isn't in _MODEL_BASE_INPUT_RATES must not
        silently read as a genuinely zero-cost dispatch -- matches cost's own
        "(N unpriced turns / M tokens excluded ...)" convention. Before this
        fix, _dispatch_usage_summary discarded _price_turn's unpriced-tokens
        return value entirely."""
        session_id = "sess-unpriced"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            [_priced_sidechain_asst(
                "claude-unreleased-model", input_tokens=1_000_000, output_tokens=500,
            )],
            agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5)
        assert cols["Actual$"] == "$0.00"
        assert "1 unpriced turns / 1,000,500 tokens excluded" in out


class TestDispatchUsageSummaryDedupBeforePricing:
    """_dispatch_usage_summary dedups a same-requestId run into one turn
    before pricing it (dedup_turns_by_request_id), so a multi-block API
    response is priced once, not once per block. These regression tests
    hand-roll their fixtures via _asst, since _priced_sidechain_asst takes
    no request_id."""

    def _two_block_run(self, model: str, *, request_id: str = "req-1") -> list[dict]:
        """One API call's two content-block records sharing one requestId.
        output_tokens ascends non-identically across the two records,
        matching TestPrCostDedupBeforePricing's 3-then-50 pattern.
        input_tokens is identical across both records, the invariant
        _merge_assistant_run relies on to merge them. A correct pricing
        pass must price the merged turn's last-record usage once, not sum
        both blocks."""
        rec1 = _asst(
            model, branch="feature-a", sidechain=True, request_id=request_id,
            content=[{"type": "thinking", "thinking": "..."}],
        )
        rec1["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 3,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        rec2 = _asst(
            model, branch="feature-a", sidechain=True, request_id=request_id,
            content=[{"type": "text", "text": "done"}],
        )
        rec2["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        return [rec1, rec2]

    def test_two_block_run_prices_identically_via_subagent_mix_and_cost(self, fake_projects, capsys):
        """A dispatch's two-content-block, one-requestId run must price
        identically whether summed via subagent-mix's own
        _dispatch_usage_summary or via cost's --branches path -- the
        main-thread agent_use record carries no usage, so cost's grand
        total for the branch is entirely this one dispatch's dollars."""
        session_id = "sess-dispatch-dedup"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feature-a", content=[_agent_use("a1", "staff-sdet")]),
        ])
        _write_subagent_dispatch(
            fake_projects, session_id, "agent-1", "a1",
            self._two_block_run("claude-sonnet-4-6"), agent_type="staff-sdet",
        )
        _mod.subagent_mix.cmd_subagent_mix(_subagent_mix_args())
        cols = _table_cols(
            capsys.readouterr().out, header_contains="Actual$", row_contains="staff-sdet", max_labels=5,
        )
        dispatch_dollars = float(cols["Actual$"].lstrip("$").replace(",", ""))

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        cost_total = _extract_grand_total(capsys.readouterr().out)
        # abs= accounts for both figures' own 2-decimal-place rounding,
        # not slack in the expected computation itself.
        assert dispatch_dollars == pytest.approx(cost_total, abs=0.005)

    def test_summed_per_dispatch_dollars_equal_hand_computed_figure(self, tmp_path):
        """Two dispatches, each carrying the same two-content-block run:
        summed actual_dollars across both must equal the hand-computed
        figure derived from pricing only each run's final (billed) usage.
        This is an equality assertion, not an inequality against `cost`'s
        ceiling. A per-dispatch sum always undercuts `cost`'s ceiling
        regardless of whether dedup runs, so an inequality assertion here
        would pass even with a reverted dedup step."""
        jsonl_1 = tmp_path / "dispatch-1.jsonl"
        jsonl_2 = tmp_path / "dispatch-2.jsonl"
        _write_jsonl(jsonl_1, self._two_block_run("claude-sonnet-4-6", request_id="req-1"))
        _write_jsonl(jsonl_2, self._two_block_run("claude-sonnet-4-6", request_id="req-2"))
        _, dollars_1, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(jsonl_1, None, None, None, date(2026, 8, 2))
        _, dollars_2, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(jsonl_2, None, None, None, date(2026, 8, 2))
        # claude-sonnet-4-6: $3.00/MTok input, $15.00/MTok output (5x
        # multiplier). Each deduped turn prices only its last record's usage
        # (1,000,000 input + 50 output); two dispatches double that.
        per_dispatch = 1_000_000 / 1_000_000 * 3.00 + 50 / 1_000_000 * 15.00
        assert dollars_1 + dollars_2 == pytest.approx(2 * per_dispatch)

    def test_dollars_by_class_reflects_merged_cache_usage_not_summed_per_block(self, tmp_path):
        """A two-block run's cache_read_input_tokens and
        cache_creation_input_tokens are identical across both blocks, per
        _merge_assistant_run's own documented invariant, so pricing must
        take them once from the merged turn's usage, not sum them once per
        block. A per-block-pricing regression would double both cache-class
        dollar figures below. Both cache fields are nonzero here so that a
        double-count is visible in the result."""
        rec1 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-cache",
            content=[{"type": "thinking", "thinking": "..."}],
        )
        rec1["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 3,
            "cache_creation_input_tokens": 400_000, "cache_read_input_tokens": 200_000,
        }
        rec2 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-cache",
            content=[{"type": "text", "text": "done"}],
        )
        rec2["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 400_000, "cache_read_input_tokens": 200_000,
        }
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, [rec1, rec2])
        _, _, dollars_by_class, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, date(2026, 8, 2)
        )
        # claude-sonnet-4-6: cache_read $0.30/MTok, cache_write_5m $3.75/MTok.
        assert dollars_by_class["cache_read"] == pytest.approx(0.06)
        assert dollars_by_class["cache_write_5m"] == pytest.approx(1.50)

    def test_dollars_by_class_covers_cache_write_1h_via_nested_cache_creation_block(self, tmp_path):
        """_cache_write_split reads cache_write_1h only from the nested
        cache_creation.ephemeral_1h_input_tokens field, which
        test_dollars_by_class_reflects_merged_cache_usage_not_summed_per_block
        never sets."""
        rec1 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-cache-1h",
            content=[{"type": "thinking", "thinking": "..."}],
        )
        rec1["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 3,
            "cache_creation_input_tokens": 400_000, "cache_read_input_tokens": 200_000,
            "cache_creation": {"ephemeral_1h_input_tokens": 250_000, "ephemeral_5m_input_tokens": 150_000},
        }
        rec2 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-cache-1h",
            content=[{"type": "text", "text": "done"}],
        )
        rec2["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 400_000, "cache_read_input_tokens": 200_000,
            "cache_creation": {"ephemeral_1h_input_tokens": 250_000, "ephemeral_5m_input_tokens": 150_000},
        }
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, [rec1, rec2])
        _, _, dollars_by_class, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, date(2026, 8, 2)
        )
        # claude-sonnet-4-6: cache_read $0.30/MTok, cache_write_5m $3.75/MTok,
        # cache_write_1h $6.00/MTok. A per-block-pricing regression would
        # double all three, since each block carries the same nonzero counts.
        assert dollars_by_class["cache_read"] == pytest.approx(0.06)
        assert dollars_by_class["cache_write_5m"] == pytest.approx(0.5625)
        assert dollars_by_class["cache_write_1h"] == pytest.approx(1.50)

    def test_missing_path_returns_empty_summary(self, tmp_path):
        """A path that is not a readable file returns the documented empty
        tuple, the caller's dangling-dispatch exclusion signal."""
        result = _mod.subagent_mix._dispatch_usage_summary(
            tmp_path / "absent.jsonl", None, None, None, date(2026, 8, 2)
        )
        assert result == (None, 0.0, {}, None, 0, 0, set())

    def test_file_that_fails_to_open_returns_empty_summary(self, tmp_path, monkeypatch):
        """A file that passes is_file() but raises OSError on open takes the
        same empty-tuple return as a missing path rather than raising."""
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, self._two_block_run("claude-sonnet-4-6"))

        def _open_failing(*_args, **_kwargs):
            raise PermissionError("denied")

        # Shadows the builtin only inside the module under test.
        monkeypatch.setattr(_mod.subagent_mix, "open", _open_failing, raising=False)
        result = _mod.subagent_mix._dispatch_usage_summary(jsonl_path, None, None, None, date(2026, 8, 2))
        assert result == (None, 0.0, {}, None, 0, 0, set())

    def test_priced_model_past_rate_expiry_lands_in_stale_models(self, tmp_path):
        """A priced model is reported in stale_models only when the
        caller-supplied today is after its _MODEL_RATE_EXPIRES date."""
        model = "claude-sonnet-4-6"
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, self._two_block_run(model))
        expiry = _mod.pricing._MODEL_RATE_EXPIRES[model]
        *_, stale_on_expiry_day = _mod.subagent_mix._dispatch_usage_summary(jsonl_path, None, None, None, expiry)
        *_, stale_day_after_expiry = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, expiry + timedelta(days=1)
        )
        assert stale_on_expiry_day == set()
        assert stale_day_after_expiry == {model}

    def test_reprice_as_counterfactual_prices_deduped_turn_once_alongside_actual(self, tmp_path):
        """The reprice_as arm is a sibling of the actual-dollar arm, so a
        deduped two-block run must price once in both. Repricing sonnet-4-6
        usage as opus-4-8 pins counterfactual_dollars next to actual_dollars;
        a dedup applied to only one arm would skew the pair."""
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, self._two_block_run("claude-sonnet-4-6"))
        _, actual_dollars, _, counterfactual_dollars, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, "claude-opus-4-8", date(2026, 8, 2)
        )
        # Merged usage is the run's last record (1,000,000 input + 50 output).
        # claude-sonnet-4-6: $3.00/MTok input, $15.00/MTok output.
        # claude-opus-4-8: $5.00/MTok input, $25.00/MTok output.
        assert actual_dollars == pytest.approx(3.00075)
        assert counterfactual_dollars == pytest.approx(5.00125)

    def test_mixed_model_dispatch_prices_each_turn_at_its_own_model_rate(self, tmp_path):
        """One dispatch holding a sonnet-4-6 turn and an opus-4-8 turn, with
        distinct input counts and distinct requestIds, prices each turn at its
        own model's rate and reports the literal "mixed" bucket. A model
        hoisted out of the per-turn loop would price both turns at one rate."""
        sonnet_turn = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-sonnet",
            content=[{"type": "text", "text": "first"}],
        )
        sonnet_turn["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        opus_turn = _asst(
            "claude-opus-4-8", branch="feature-a", sidechain=True, request_id="req-opus",
            content=[{"type": "text", "text": "second"}],
        )
        opus_turn["message"]["usage"] = {
            "input_tokens": 2_000_000, "output_tokens": 100,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, [sonnet_turn, opus_turn])
        observed_bucket, actual_dollars, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, date(2026, 8, 2)
        )
        # claude-sonnet-4-6: 1,000,000 input at $3.00/MTok + 50 output at $15.00/MTok.
        # claude-opus-4-8: 2,000,000 input at $5.00/MTok + 100 output at $25.00/MTok.
        sonnet_turn_dollars = 1_000_000 / 1_000_000 * 3.00 + 50 / 1_000_000 * 15.00
        opus_turn_dollars = 2_000_000 / 1_000_000 * 5.00 + 100 / 1_000_000 * 25.00
        assert actual_dollars == pytest.approx(sonnet_turn_dollars + opus_turn_dollars)
        assert observed_bucket == "mixed"

    def test_non_contiguous_run_with_interleaved_tool_result_still_merges(self, tmp_path):
        """A same-requestId run whose two assistant records straddle an
        interleaved tool_result must still collapse into one priced turn, not
        two, when every usage field (including output_tokens) agrees across
        both records. (The harness executes one multi-tool_use response's
        tool calls one at a time, which produces this interleaving.)"""
        rec1 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-nc",
            content=[_bash_use("t1", "echo hi")],
        )
        rec1["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        rec2 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-nc",
            content=[{"type": "text", "text": "done"}],
        )
        rec2["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, [rec1, _tool_result("t1", "hi\n"), rec2])
        _, actual_dollars, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, date(2026, 8, 2)
        )
        # A merge prices this once (1,000,000 input + 50 output = $3.00075);
        # a failure to merge across the interleaved tool_result would double
        # it to $6.0015.
        assert actual_dollars == pytest.approx(3.00075)

    def test_corrupt_line_between_same_request_id_records_still_merges(self, tmp_path):
        """A corrupt JSONL line sitting between a same-requestId run's two
        records must be filtered out before dedup grouping runs, not after.
        Otherwise a corrupt line could split a contiguous run and
        misattribute the dedup grouping around it. Same shape as
        test_non_contiguous_run_with_interleaved_tool_result_still_merges,
        substituting the interleaved record for a malformed line."""
        rec1, rec2 = self._two_block_run("claude-sonnet-4-6", request_id="req-corrupt")
        jsonl_path = tmp_path / "dispatch.jsonl"
        jsonl_path.write_text(f"{json.dumps(rec1)}\nTHIS IS NOT JSON\n{json.dumps(rec2)}\n")
        _, actual_dollars, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, date(2026, 8, 2)
        )
        # A merge prices this once (1,000,000 input + 50 output = $3.00075);
        # the corrupt line contributes no dollars, and a failure to merge
        # around it would double the total to $6.0015.
        assert actual_dollars == pytest.approx(3.00075)

    def test_unpriced_turn_count_is_once_per_deduped_turn_not_per_raw_record(self, tmp_path):
        """A two-content-block, one-requestId run on an unpriced model
        surfaces as 1 unpriced turn, not 2. The diagnostic counts (and
        totals tokens for) the merged turn's final usage only."""
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, self._two_block_run("claude-unreleased-model"))
        _, actual_dollars, _, _, unpriced_turns, unpriced_tokens, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, None, None, None, date(2026, 8, 2)
        )
        assert actual_dollars == 0.0
        # Merged usage is the run's last record: 1,000,000 input + 50 output.
        assert unpriced_turns == 1
        assert unpriced_tokens == 1_000_050

    def test_run_excluded_when_first_block_timestamp_outside_window_even_if_last_block_inside(self, tmp_path):
        """A same-requestId run whose first record's timestamp sits before
        since_ts while its last record's timestamp sits inside [since_ts,
        until_ts) is excluded from actual_dollars entirely. Inclusion is
        decided by the merged turn's first-block timestamp, per
        _merge_assistant_run's run[0] convention. A run straddling the
        window's lower edge this way must not leak its in-window last
        block's usage into actual_dollars."""
        rec1 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-straddle",
            ts="2026-06-30T23:59:59.000Z", content=[{"type": "thinking", "thinking": "..."}],
        )
        rec1["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 3,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        rec2 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-straddle",
            ts="2026-07-01T00:00:01.000Z", content=[{"type": "text", "text": "done"}],
        )
        rec2["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, [rec1, rec2])
        since_ts = _mod._parse_ts("2026-07-01T00:00:00.000Z")
        until_ts = _mod._parse_ts("2026-07-02T00:00:00.000Z")
        _, actual_dollars, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, since_ts, until_ts, None, date(2026, 8, 2)
        )
        assert actual_dollars == 0.0

    def test_run_included_when_last_block_timestamp_outside_window_even_if_first_inside(self, tmp_path):
        """Mirror of the lower-edge test above: a same-requestId run whose
        first record's timestamp sits inside [since_ts, until_ts) while its
        last record's timestamp sits at or after until_ts is counted as
        fully in-window spend, not excluded or partially priced. Inclusion is
        decided by the merged turn's first-block timestamp, per
        _merge_assistant_run's run[0] convention, so a run straddling the
        window's upper edge this way has its full billed (last-block) usage
        counted in actual_dollars -- the over-inclusion this convention
        produces at the upper edge."""
        rec1 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-straddle-upper",
            ts="2026-07-01T23:59:59.000Z", content=[{"type": "thinking", "thinking": "..."}],
        )
        rec1["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 3,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        rec2 = _asst(
            "claude-sonnet-4-6", branch="feature-a", sidechain=True, request_id="req-straddle-upper",
            ts="2026-07-02T00:00:01.000Z", content=[{"type": "text", "text": "done"}],
        )
        rec2["message"]["usage"] = {
            "input_tokens": 1_000_000, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }
        jsonl_path = tmp_path / "dispatch.jsonl"
        _write_jsonl(jsonl_path, [rec1, rec2])
        since_ts = _mod._parse_ts("2026-07-01T00:00:00.000Z")
        until_ts = _mod._parse_ts("2026-07-02T00:00:00.000Z")
        _, actual_dollars, _, _, _, _, _ = _mod.subagent_mix._dispatch_usage_summary(
            jsonl_path, since_ts, until_ts, None, date(2026, 8, 2)
        )
        # Merged usage is the run's last record (1,000,000 input + 50
        # output), priced in full despite that record's own timestamp
        # falling after until_ts.
        assert actual_dollars == pytest.approx(3.00075)
