from datetime import date

import pytest
from transcript_analysis import cost, pricing

from .conftest import _asst, _cost_args, _extract_grand_total, _priced, _tool_result, _user_msg, _write_jsonl


class TestPriceTurnArity:
    def test_price_turn_returns_exactly_three_values(self):
        """Pins _price_turn's (dollars_by_class, context_at_turn, unpriced_tokens)
        three-tuple return signature — three other call sites (audit-routing,
        context-distribution, cost-trend) consume it positionally; a future
        edit widening it for one caller would silently break the rest.
        audit-routing-shape and audit-routing-samples classify Opus turn
        shape only and never call _price_turn."""
        usage = _priced("claude-sonnet-5", input=100)["message"]["usage"]
        result = pricing._price_turn("claude-sonnet-5", usage)
        assert len(result) == 3
        dollars, context_at_turn, unpriced_tokens = result
        assert isinstance(dollars, dict)
        assert isinstance(context_at_turn, int)
        assert isinstance(unpriced_tokens, int)

    def test_context_at_turn_extraction_matches_price_turns_own_return_value(self):
        """_context_at_turn was extracted out of _price_turn as a pure
        refactor -- pins that the extracted computation still equals the sum
        _price_turn's own docstring specifies (input + cache_read +
        ephemeral_1h + ephemeral_5m) for a representative usage record."""
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 5},
        }
        _dollars, context_at_turn, _unpriced_tokens = pricing._price_turn("claude-sonnet-5", usage)
        assert context_at_turn == 100 + 50 + 10 + 5


class TestPriceTurnSpeedGeoMultipliers:
    """_price_turn's speed:"fast" (2x) and inference_geo:"us" (1.1x) dollar
    multipliers -- vendor-billed outcome fields, applied regardless of the
    model's own fast-mode/data-residency eligibility (see the regression-
    anchor test below)."""

    def test_speed_fast_multiplies_dollars_by_two(self):
        usage = _priced("claude-sonnet-5", input=1_000_000, speed="fast")["message"]["usage"]
        dollars, _context_at_turn, unpriced = pricing._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 2)

    def test_inference_geo_us_multiplies_dollars_by_1_1(self):
        usage = _priced("claude-sonnet-5", input=1_000_000, inference_geo="us")["message"]["usage"]
        dollars, _context_at_turn, unpriced = pricing._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 1.1)

    def test_speed_and_inference_geo_stack_multiplicatively(self):
        """Both multipliers present on the same turn compose to 2 * 1.1 = 2.2x,
        not just the larger of the two."""
        usage = _priced(
            "claude-sonnet-5", input=1_000_000, speed="fast", inference_geo="us",
        )["message"]["usage"]
        dollars, _context_at_turn, unpriced = pricing._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 2.0 * 2.2)

    def test_multiplier_applies_regardless_of_model_eligibility(self):
        """Regression anchor, not a new runtime check: _price_turn trusts the
        API's own reported speed/inference_geo outcome rather than
        hand-maintaining a per-model eligibility list, so a model that isn't
        actually fast-mode/data-residency-eligible still gets the multiplier
        if the usage record carries the field."""
        usage = _priced(
            "claude-sonnet-4-6", input=1_000_000, speed="fast", inference_geo="us",
        )["message"]["usage"]
        dollars, _context_at_turn, unpriced = pricing._price_turn("claude-sonnet-4-6", usage)
        assert unpriced == 0
        assert dollars["input"] == pytest.approx(1_000_000 / 1_000_000 * 3.0 * 2.2)


_PRICING_VARIANT_USAGE_CASES = [
    pytest.param({}, "standard", id="neither-field-present"),
    pytest.param({"speed": "fast"}, "fast", id="fast"),
    pytest.param({"inference_geo": "us"}, "us_geo", id="us-geo"),
    pytest.param({"speed": "fast", "inference_geo": "us"}, "fast_us_geo", id="fast-and-us-geo"),
    pytest.param({"speed": "standard"}, "standard", id="speed-standard"),
    pytest.param({"speed": None}, "standard", id="speed-none"),
    pytest.param({"speed": "FAST"}, "standard", id="speed-uppercase"),
    pytest.param({"inference_geo": "global"}, "standard", id="geo-global"),
    pytest.param({"inference_geo": "US"}, "standard", id="geo-uppercase"),
    pytest.param({"inference_geo": ""}, "standard", id="geo-empty"),
    pytest.param({"inference_geo": None}, "standard", id="geo-none"),
    pytest.param({"speed": "FAST", "inference_geo": "us"}, "us_geo", id="uppercase-speed-with-us-geo"),
    pytest.param({"speed": "fast", "inference_geo": "US"}, "fast", id="fast-with-uppercase-geo"),
]


class TestPricingVariant:
    """pricing._pricing_variant labels a turn by the multiplier conditions _price_turn applies, so
    pr-cost's per-model cells file each turn under the variant its dollars were priced with."""

    @pytest.mark.parametrize("extra_usage_fields,expected_label", _PRICING_VARIANT_USAGE_CASES)
    def test_label_for_each_speed_and_geo_combination_and_near_miss(self, extra_usage_fields, expected_label):
        usage = {**_priced("claude-sonnet-5", input=1_000)["message"]["usage"], **extra_usage_fields}
        assert pricing._pricing_variant(usage) == expected_label

    @pytest.mark.parametrize("extra_usage_fields,expected_label", _PRICING_VARIANT_USAGE_CASES)
    def test_price_turn_dollars_follow_the_label_implied_multiplier_under_the_listed_usage_perturbations(
        self, extra_usage_fields, expected_label,
    ):
        """For the listed perturbations, dollars equal standard dollars times the label's multiplier. A new
        multiplier keyed on one of those fields moves the dollars while the label stays unchanged, which
        fails this test. Dollars are compared with a relative tolerance, so a multiplication-order
        refactor does not."""
        label_multiplier = {
            "standard": 1,
            "fast": pricing._FAST_MODE_RATE_MULTIPLIER,
            "us_geo": pricing._INFERENCE_GEO_US_RATE_MULTIPLIER,
            "fast_us_geo": pricing._FAST_MODE_RATE_MULTIPLIER * pricing._INFERENCE_GEO_US_RATE_MULTIPLIER,
        }
        realistic_usage = {
            **_priced(
                "claude-sonnet-5", input=123_457, output=7_891, cache_read=3_333_333,
                ephemeral_1h=45_671, ephemeral_5m=98_765,
            )["message"]["usage"],
            "service_tier": "standard",
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        }
        standard_dollars, _context, _unpriced = pricing._price_turn("claude-sonnet-5", realistic_usage)
        perturbations = [
            {},
            {"service_tier": "priority"},
            {"service_tier": "batch"},
            {"service_tier": None},
            {"server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 2}},
            {"server_tool_use": None},
            {"not_a_known_usage_field": "some-future-value"},
            {"iterations": [{"type": "message", "input_tokens": 1, "output_tokens": 1}]},
        ]

        for perturbation in perturbations:
            usage = {**realistic_usage, **perturbation, **extra_usage_fields}
            assert pricing._pricing_variant(usage) == expected_label, perturbation
            actual_dollars, _context, _unpriced = pricing._price_turn("claude-sonnet-5", usage)
            for token_class in pricing._TOKEN_CLASSES:
                assert actual_dollars[token_class] == pytest.approx(
                    standard_dollars[token_class] * label_multiplier[expected_label], rel=1e-9,
                ), (perturbation, token_class)


class TestReportableUnpricedModelIds:
    """Direct unit tests for pricing._reportable_unpriced_model_ids, the
    shared predicate TestExcludedSpendBanner's _cost_report fixtures also
    exercise -- these pin its branches by direct call so a bug in the
    predicate itself is distinguishable from a print-wording bug in the
    banner that consumes it."""

    def test_empty_dict_reports_nothing(self):
        assert pricing._reportable_unpriced_model_ids({}) == []

    def test_priced_looking_key_with_nonzero_tokens_is_reportable(self):
        assert pricing._reportable_unpriced_model_ids({"claude-example-9": 500}) == ["claude-example-9"]

    def test_synthetic_at_zero_tokens_is_not_reportable(self):
        """A corpus with no synthetic records at all still seeds this key at
        0 in some callers -- a $0 entry is not spend excluded from anything."""
        assert pricing._reportable_unpriced_model_ids({pricing._SYNTHETIC_MODEL_ID: 0}) == []

    def test_synthetic_at_nonzero_tokens_is_reportable(self):
        """Forward-looking guard, not a case observed in a real corpus (see
        the production docstring) -- pins that a naive `!= _SYNTHETIC_MODEL_ID`
        filter, which would silently drop this branch, is rejected."""
        assert pricing._reportable_unpriced_model_ids({pricing._SYNTHETIC_MODEL_ID: 100}) == [
            pricing._SYNTHETIC_MODEL_ID
        ]

    def test_multiple_unpriced_ids_all_reported(self):
        result = pricing._reportable_unpriced_model_ids({"claude-example-9": 100, "claude-example-10": 200})
        assert sorted(result) == ["claude-example-10", "claude-example-9"]


class TestFormatDriftDetected:
    """Direct unit tests for pricing._format_drift_detected, the OR of the
    two module-level drift flags TestPricingIntegrityBanner's _cost_report
    fixtures also exercise through the full report."""

    def test_false_when_neither_flag_set(self, monkeypatch):
        monkeypatch.setattr(pricing, "_usage_drift_warned", False)
        monkeypatch.setattr(pricing, "_subagent_format_drift_detected", False)
        assert pricing._format_drift_detected() is False

    def test_true_when_only_usage_drift_flag_set(self, monkeypatch):
        monkeypatch.setattr(pricing, "_usage_drift_warned", True)
        monkeypatch.setattr(pricing, "_subagent_format_drift_detected", False)
        assert pricing._format_drift_detected() is True

    def test_true_when_only_subagent_drift_flag_set(self, monkeypatch):
        monkeypatch.setattr(pricing, "_usage_drift_warned", False)
        monkeypatch.setattr(pricing, "_subagent_format_drift_detected", True)
        assert pricing._format_drift_detected() is True


class TestDedupTurnsByRequestId:
    """Direct tests for pricing.dedup_turns_by_request_id, the shared turn iterator
    cmd_audit_routing, _cost_report, _context_distribution_report,
    _cost_trend_report, and cmd_subagents all apply to their own per-session
    records before their per-record loops -- Claude Code writes one JSONL
    record per assistant content block, and every record from one API call
    shares a requestId. input_tokens and the cache_* classes are identical
    across a run's records; output_tokens ascends within the run and
    completes only on the last record."""

    def test_single_record_request_is_a_no_op(self):
        """A lone assistant record (no run to merge) is returned unchanged."""
        rec = _priced("claude-sonnet-5", input=100, request_id="req-1")
        assert pricing.dedup_turns_by_request_id([rec]) == [rec]

    def test_multi_record_run_merges_content_in_order_and_keeps_last_usage(self):
        """Three records sharing one requestId collapse into one turn whose
        content is the concatenation of all three blocks in original order,
        and whose usage is the run's LAST record's usage -- so a caller
        pricing the merged record prices the run's final (billed) usage,
        not an earlier record's."""
        block_a = {"type": "thinking", "thinking": "..."}
        block_b = {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
        block_c = {"type": "text", "text": "done"}
        recs = [
            _asst("claude-sonnet-5", content=[block_a], request_id="req-1"),
            _asst("claude-sonnet-5", content=[block_b], request_id="req-1"),
            _asst("claude-sonnet-5", content=[block_c], request_id="req-1"),
        ]
        recs[0]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[1]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[2]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = pricing.dedup_turns_by_request_id(recs)
        assert len(result) == 1
        assert result[0]["message"]["content"] == [block_a, block_b, block_c]
        assert result[0]["message"]["usage"] == {"input_tokens": 100, "output_tokens": 50}

    def test_ascending_output_tokens_within_run_prices_using_last_record(self):
        """The run's output_tokens ascends record-to-record and completes on
        the last one (measured across 15,653 multi-record runs, 100% of
        which peak on the last record) -- a merged turn's usage must reflect
        that final, billed value. Taking the first record's stub value here
        would undercount output tokens, the regression this test guards."""
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        recs[0]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[1]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        recs[2]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3111}
        result = pricing.dedup_turns_by_request_id(recs)
        assert result[0]["message"]["usage"]["output_tokens"] == 3111

    def test_non_identical_input_usage_within_run_emits_stderr_warning(self, monkeypatch, capsys):
        """A future transcript format emitting non-identical input_tokens
        within one requestId run is a silent-mispricing risk with no signal
        today -- this canary fires a stderr WARNING when
        _merge_assistant_run's input/cache-invariant-usage assumption is
        violated, mirroring _warn_if_subagent_format_drift's pattern.
        _usage_drift_warned is reset here since the canary is rate-limited to
        one warning per process (see _warn_if_run_usage_drift) and other
        tests in this module-scoped process may have already tripped it."""
        monkeypatch.setattr(pricing, "_usage_drift_warned", False)
        rec1 = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 999, "output_tokens": 50}
        pricing.dedup_turns_by_request_id([rec1, rec2])
        assert "WARNING" in capsys.readouterr().err

    def test_ascending_output_tokens_within_run_emits_no_warning(self, monkeypatch, capsys):
        """The normal case -- output_tokens ascends within a run while
        input_tokens and the cache_* classes stay identical -- never fires
        the drift canary; an ascending output_tokens is the documented norm,
        not drift."""
        monkeypatch.setattr(pricing, "_usage_drift_warned", False)
        rec1 = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 3}
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        pricing.dedup_turns_by_request_id([rec1, rec2])
        assert "WARNING" not in capsys.readouterr().err

    def test_missing_request_id_records_each_count_separately(self):
        """Two assistant records with no requestId key at all never merge
        with each other — each is returned as its own one-record turn."""
        rec1 = _priced("claude-sonnet-5", input=100)
        rec2 = _priced("claude-sonnet-5", input=200)
        assert "requestId" not in rec1
        assert "requestId" not in rec2
        assert pricing.dedup_turns_by_request_id([rec1, rec2]) == [rec1, rec2]

    def test_null_and_empty_request_id_records_each_count_separately(self):
        """A null requestId and an empty-string requestId are both treated as
        'missing' — neither merges with the other or with a truly absent
        requestId, matching the never-merge-two-missing-ids requirement."""
        rec1 = _priced("claude-sonnet-5", input=100)
        rec1["requestId"] = None
        rec2 = _priced("claude-sonnet-5", input=200)
        rec2["requestId"] = ""
        assert pricing.dedup_turns_by_request_id([rec1, rec2]) == [rec1, rec2]

    def test_non_contiguous_run_with_matching_usage_merges_across_user_record(self):
        """A same-requestId assistant run interleaved with a user tool_result
        record -- the harness's own shape for a multi-tool_use response
        dispatched one tool call at a time -- merges into one turn when
        every record's usage matches, with the intervening user record
        passing through unchanged at its own position. Treating the
        intervening user record as ending the run and never revisiting the
        second assistant block would inflate turn counts and dollar totals
        for this shape, the regression this test guards. This is the
        canonical pin for dedup_turns_by_request_id's non-contiguous merge
        behavior -- extend it rather than adding a second pin elsewhere."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = pricing.dedup_turns_by_request_id([rec1, user, rec2])
        assert len(result) == 2
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]
        assert result[0]["message"]["usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert result[1] == user

    def test_non_contiguous_run_with_full_cache_usage_matching_merges(self):
        """The merge-approval path exercised above uses a 2-key usage dict
        (input_tokens/output_tokens only), where the two omitted keys
        (cache_creation_input_tokens/cache_read_input_tokens) trivially
        agree via None == None regardless of whether the comparison logic
        actually checks them. This test populates all four
        _NON_CONTIGUOUS_MERGE_REQUIRED_MATCH_KEYS with real matching values
        -- the shape every real transcript record carries per this
        function's own docstring -- so a regression that broke comparison
        specifically for populated cache values on the merge-approval path
        would fail here even though the 2-key tests above would not catch
        it."""
        usage = {
            "input_tokens": 100, "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = dict(usage)
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = dict(usage)
        result = pricing.dedup_turns_by_request_id([rec1, user, rec2])
        assert len(result) == 2
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]

    def test_non_contiguous_run_with_mismatched_output_tokens_does_not_merge(self):
        """Two assistant records share a requestId but diverge on
        output_tokens -- the signature of two genuinely separate API calls
        that happen to collide on requestId (e.g. a hook-denial retry),
        rather than one once-billed non-contiguous run. The corroboration
        bar rejects the merge and all three records stay separate turns in
        original order."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user = _user_msg([_tool_result("t1", "continue")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 999}
        result = pricing.dedup_turns_by_request_id([rec1, user, rec2])
        assert result == [rec1, user, rec2]

    def test_three_record_non_contiguous_run_merges(self):
        """The merge isn't hardcoded to pairs: a 3-record non-contiguous run
        with matching usage on every record merges into one turn, with both
        intervening user records passing through unchanged."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user1 = _user_msg([_tool_result("t1", "tool result 1")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        user2 = _user_msg([_tool_result("t2", "tool result 2")])
        rec3 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec3["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = pricing.dedup_turns_by_request_id([rec1, user1, rec2, user2, rec3])
        assert len(result) == 3
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}, {"type": "text", "text": "c"},
        ]
        assert result[1] == user1
        assert result[2] == user2

    def test_three_record_non_contiguous_run_with_one_divergent_member_does_not_merge(self):
        """Pins that _non_contiguous_run_usage_matches compares every member
        against the run's first record, not just adjacent pairs or a
        majority: a 3-record non-contiguous run where two members match and
        the third diverges on one invariant key (cache_read_input_tokens)
        fails the corroboration bar, so all three records stay separate
        turns regardless of the divergent member's position in the group."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec1["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        user1 = _user_msg([_tool_result("t1", "tool result 1")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        rec2["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 10, "output_tokens": 50,
        }
        user2 = _user_msg([_tool_result("t2", "tool result 2")])
        rec3 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec3["message"]["usage"] = {
            "input_tokens": 100, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 999, "output_tokens": 50,
        }
        result = pricing.dedup_turns_by_request_id([rec1, user1, rec2, user2, rec3])
        assert result == [rec1, user1, rec2, user2, rec3]

    def test_interleaved_different_request_id_group_leaves_other_run_record_untouched(self):
        """Records ordered [A req-1, B req-2, C req-1], where req-1's usage
        matches across A and C: the two-pass indexer groups by requestId
        across the full input, so B -- a different run's own record sitting
        between A and C -- passes through unchanged at its own position
        while A and C merge at A's original position, pinning that indexing
        by requestId across the full input doesn't disturb an unrelated
        run's record that happens to sit between this run's members."""
        rec_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        rec_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-2")
        rec_b["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        rec_c = _asst("claude-sonnet-5", content=[{"type": "text", "text": "c"}], request_id="req-1")
        rec_c["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        result = pricing.dedup_turns_by_request_id([rec_a, rec_b, rec_c])
        assert len(result) == 2
        assert result[0]["requestId"] == "req-1"
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "c"},
        ]
        assert result[1] == rec_b

    def test_two_multi_member_non_contiguous_groups_mutually_interleaved_both_merge(self):
        """Records ordered [A1 req-1, B1 req-2, A2 req-1, B2 req-2] -- two
        *each*-multi-member non-contiguous groups mutually interleaved,
        rather than one multi-member group interleaved with a single-record
        different-id record (the prior test's shape). Both groups merge
        independently at their own first member's position, pinning that
        merge_start_idx/skip_idx reconstruction handles two simultaneously
        in-progress groups rather than only one."""
        rec_a1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a1"}], request_id="req-1")
        rec_a1["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b1"}], request_id="req-2")
        rec_b1["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        rec_a2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a2"}], request_id="req-1")
        rec_a2["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        rec_b2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b2"}], request_id="req-2")
        rec_b2["message"]["usage"] = {"input_tokens": 200, "output_tokens": 20}
        result = pricing.dedup_turns_by_request_id([rec_a1, rec_b1, rec_a2, rec_b2])
        assert len(result) == 2
        assert result[0]["requestId"] == "req-1"
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a1"}, {"type": "text", "text": "a2"},
        ]
        assert result[1]["requestId"] == "req-2"
        assert result[1]["message"]["content"] == [
            {"type": "text", "text": "b1"}, {"type": "text", "text": "b2"},
        ]

    def test_non_contiguous_merge_and_rejection_notices_are_independently_rate_limited(
        self, monkeypatch, capsys
    ):
        """Both non-contiguous merge and rejection decisions log one stderr
        NOTICE per process, independently rate-limited by decision kind
        (_non_contiguous_merge_notices_logged) -- a merge NOTICE firing must
        not suppress a later rejection NOTICE, and each message names its
        own decision kind so the two are distinguishable by grep, not just a
        generic 'NOTICE' substring. Also asserts the interpolated requestId
        and record_count values, not just the decision-kind keyword -- the
        NOTICE's stated purpose is auditing which run triggered which
        decision, so a transposition bug in the f-string (wrong requestId,
        wrong count) must fail this test even though the keyword-only
        assertions would still pass.
        _non_contiguous_merge_notices_logged is reset here since it's
        rate-limited across the whole process and other tests in this
        module-scoped run may have already tripped either kind (mirrors
        test_non_identical_input_usage_within_run_emits_stderr_warning's
        reset of _usage_drift_warned)."""
        monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())

        merge_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        merge_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        merge_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        merge_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        pricing.dedup_turns_by_request_id([merge_a, _user_msg([_tool_result("t1", "tool result")]), merge_b])
        merge_stderr = capsys.readouterr().err
        assert "NOTICE" in merge_stderr
        assert "merged" in merge_stderr
        assert "rejected" not in merge_stderr
        assert "req-1" in merge_stderr
        assert "2" in merge_stderr

        reject_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "x"}], request_id="req-2")
        reject_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        reject_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "y"}], request_id="req-2")
        reject_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 999}
        pricing.dedup_turns_by_request_id([reject_a, _user_msg([_tool_result("t2", "tool result")]), reject_b])
        reject_stderr = capsys.readouterr().err
        assert "NOTICE" in reject_stderr
        assert "rejected" in reject_stderr
        assert "req-2" in reject_stderr
        assert "2" in reject_stderr

    def test_non_contiguous_merge_notice_suppressed_on_repeat_same_kind(self, monkeypatch, capsys):
        """A second non-contiguous merge (different requestId, same 'merged'
        decision kind) after the first must not print a second NOTICE --
        pinning the rate-limit half of 'independently rate-limited', not
        just the cross-kind independence the prior test covers."""
        monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())

        first_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        first_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        first_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        first_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        pricing.dedup_turns_by_request_id([first_a, _user_msg([_tool_result("t1", "tool result")]), first_b])
        capsys.readouterr()  # drain the first NOTICE

        second_a = _asst("claude-sonnet-5", content=[{"type": "text", "text": "x"}], request_id="req-2")
        second_a["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        second_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "y"}], request_id="req-2")
        second_b["message"]["usage"] = {"input_tokens": 100, "output_tokens": 50}
        pricing.dedup_turns_by_request_id([second_a, _user_msg([_tool_result("t2", "tool result")]), second_b])
        assert capsys.readouterr().err == ""

    def test_non_contiguous_run_with_missing_usage_does_not_merge(self):
        """Two same-requestId assistant records with no usage dict at all
        have zero evidence to corroborate on -- treating that absence as
        agreement (None == None on every key) would merge two records with
        no actual usage match, the failure mode this test guards against."""
        rec1 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}], request_id="req-1")
        del rec1["message"]["usage"]
        user = _user_msg([_tool_result("t1", "tool result")])
        rec2 = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}], request_id="req-1")
        del rec2["message"]["usage"]
        result = pricing.dedup_turns_by_request_id([rec1, user, rec2])
        assert result == [rec1, user, rec2]

    def test_request_id_does_not_merge_across_session_files(self, fake_projects, capsys):
        """The same requestId string appearing in two separate session files
        prices as two separate turns, not one merged turn — the dedup helper
        is applied fresh to each session's own records list, so a requestId
        collision across sessions (however unlikely with real UUIDs) can't
        collapse them."""
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, request_id="shared-req"),  # $2.00
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, request_id="shared-req"),  # $2.00
        ])
        cost._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(4.00)

    def test_non_contiguous_run_prices_once_through_cost_report(self, fake_projects, capsys):
        """The motivating production bug: a session file where the harness
        interleaves a tool_result between two same-requestId assistant
        records prices as one turn through an actual downstream call site
        (_cost_report), not two -- pinning the fix's real symptom end-to-end
        rather than only the shared function's branch logic in isolation.
        Before this fix, the interleaved second record priced as its own
        turn, double-counting both the turn count and the dollar total."""
        first = _priced("claude-sonnet-5", input=1_000_000, request_id="req-1")  # $2.00
        second = _priced("claude-sonnet-5", input=1_000_000, request_id="req-1")  # $2.00, same usage as first
        _write_jsonl(fake_projects / "sess.jsonl", [
            first, _user_msg([_tool_result("t1", "tool result")]), second,
        ])
        cost._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(2.00)

    def test_shared_request_id_merges_across_main_and_sidechain_records(self):
        """A requestId shared by a main-thread record and a sidechain record
        merges into one turn — the merge condition checks requestId equality
        alone, not isSidechain, relying on requestId's global uniqueness (see
        this function's own docstring). include_subagents=True concatenates a
        session's main file with its subagent files into one records list
        before dedup runs, so this boundary is reachable in that concatenated
        list even though it never occurs within a single raw JSONL file."""
        main_rec = _asst("claude-sonnet-5", content=[{"type": "text", "text": "a"}],
                          request_id="req-1", sidechain=False)
        side_rec = _asst("claude-sonnet-5", content=[{"type": "text", "text": "b"}],
                          request_id="req-1", sidechain=True)
        result = pricing.dedup_turns_by_request_id([main_rec, side_rec])
        assert len(result) == 1
        assert result[0]["message"]["content"] == [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]


class TestCacheMissReason:
    """_cache_miss_reason parses message.diagnostics.cache_miss_reason."""

    def test_returns_reason_when_present(self):
        message = {
            "diagnostics": {
                "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 12345},
            }
        }
        assert pricing._cache_miss_reason(message) == "model_changed"

    def test_returns_none_when_diagnostics_absent(self):
        assert pricing._cache_miss_reason({}) is None

    def test_returns_none_when_diagnostics_not_a_dict(self):
        assert pricing._cache_miss_reason({"diagnostics": "oops"}) is None

    def test_returns_none_when_cache_miss_reason_absent(self):
        assert pricing._cache_miss_reason({"diagnostics": {}}) is None

    def test_returns_none_when_cache_miss_reason_not_a_dict(self):
        assert pricing._cache_miss_reason({"diagnostics": {"cache_miss_reason": 42}}) is None

    def test_returns_none_when_cache_miss_reason_type_not_a_string(self):
        message = {"diagnostics": {"cache_miss_reason": {"type": 42}}}
        assert pricing._cache_miss_reason(message) is None

    def test_previous_message_not_found_reason_has_no_cache_missed_input_tokens_field(self):
        """The previous_message_not_found variant carries no
        cache_missed_input_tokens field at all -- the parser must not assume
        its presence."""
        message = {"diagnostics": {"cache_miss_reason": {"type": "previous_message_not_found"}}}
        assert pricing._cache_miss_reason(message) == "previous_message_not_found"

    def test_unavailable_reason_has_no_cache_missed_input_tokens_field(self):
        """The unavailable variant also carries no cache_missed_input_tokens
        field -- the parser must not assume its presence."""
        message = {"diagnostics": {"cache_miss_reason": {"type": "unavailable"}}}
        assert pricing._cache_miss_reason(message) == "unavailable"

    def test_merged_run_takes_cache_miss_reason_from_first_record(self):
        """_merge_assistant_run takes every non-content, non-usage field from
        a requestId run's first record unchanged -- a merged turn's own
        diagnostics survives with no extra collapsing logic added for it."""
        rec_a = _priced("claude-opus-5", output=10, request_id="req-1")
        rec_a["message"]["diagnostics"] = {
            "cache_miss_reason": {"type": "model_changed", "cache_missed_input_tokens": 999},
        }
        rec_b = _priced("claude-opus-5", output=20, request_id="req-1")
        rec_b["message"]["diagnostics"] = {"cache_miss_reason": {"type": "something_else"}}
        merged = pricing.dedup_turns_by_request_id([rec_a, rec_b])
        assert len(merged) == 1
        assert pricing._cache_miss_reason(merged[0]["message"]) == "model_changed"
