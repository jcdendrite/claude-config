"""Tests for evals/review_bench/analysis.py. Offline throughout: every
statistic and check works from synthetic RunRecord/label data, never a real
campaign. No test launches `claude`.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest
from review_bench import analysis, runner
from review_bench.adjudicate import PrecisionFinding, RecallLabel

ARM_BASELINE = "current-rule"
ARM_X = "function-context"


def _run_record(
    defect_id: str, arm: str, opaque_run_id: str, *, cli_version: str = "2.0.0",
    ambient_config_commit: str = "deadbeef", missing_reason: str | None = None, status: str = "ok",
    out_of_session_paths: tuple[str, ...] = (), read_tokens_est: int = 10, partial_view_reads: int = 0,
    paged_followups: int = 0, whole_file_reads_of_changed_files: int = 0, over_read_cap: bool = False,
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=0, opaque_run_id=opaque_run_id,
        status=status, missing_reason=missing_reason, observed_model="claude-sonnet-5",
        observed_tools=("Read",), out_of_session_paths=out_of_session_paths, findings_text="x",
        wall_clock_s=1.0, read_calls=1, read_tokens_est=read_tokens_est,
        partial_view_reads=partial_view_reads, paged_followups=paged_followups,
        whole_file_reads_of_changed_files=whole_file_reads_of_changed_files, over_read_cap=over_read_cap,
        dispatch_prompt_verbatim=True, cli_version=cli_version, ambient_config_commit=ambient_config_commit,
    )


class TestNMin:
    def test_n_min_at_k_10_is_126(self) -> None:
        assert analysis.n_min(10) == 126

    def test_n_min_at_k_15_is_95(self) -> None:
        assert analysis.n_min(15) == 95

    def test_n_min_at_k_20_is_79(self) -> None:
        assert analysis.n_min(20) == 79


class TestPercentile:
    def test_non_boundary_quantile_interpolates_linearly(self) -> None:
        """Matches numpy's documented "linear" method, which the docstring
        claims to mirror: index = 0.25 * (4 - 1) = 0.75, so the result is
        1.0 + (2.0 - 1.0) * 0.75."""
        assert analysis._percentile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.75

    def test_quantile_landing_exactly_on_an_element_skips_interpolation(self) -> None:
        """q=0.0 makes index = 0, so lower_index == upper_index -- the
        short-circuit branch, exercised here against a non-constant list so
        an off-by-one index couldn't still return the right value by luck."""
        assert analysis._percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0


class TestRecallCountsAndDropRule:
    def test_detection_rate_is_found_over_found_plus_not_found(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1"),
            _run_record("d1", ARM_BASELINE, "r2"),
            _run_record("d1", ARM_BASELINE, "r3"),
            _run_record("d1", ARM_BASELINE, "r4", status="missing", missing_reason="timeout"),
        ]
        labels = {
            "d1": {
                "r1": RecallLabel("r1", "FOUND", "x"),
                "r2": RecallLabel("r2", "NOT_FOUND", None),
                "r3": RecallLabel("r3", "FOUND", "x"),
                # r4 is missing -- never labeled, counts in neither numerator nor denominator.
            }
        }
        counts = analysis.compute_recall_counts(records, labels)
        assert counts["d1"].detection_rate(ARM_BASELINE) == pytest.approx(2 / 3)

    def test_defect_below_k_over_2_in_either_arm_is_dropped_from_both(self) -> None:
        k = 10
        # 4 completed in one arm, 9 in the other -- dropped from both.
        records = (
            [_run_record("d1", ARM_BASELINE, f"a{i}") for i in range(4)]
            + [_run_record("d1", ARM_X, f"b{i}") for i in range(9)]
        )
        labels = {
            "d1": {
                **{f"a{i}": RecallLabel(f"a{i}", "FOUND", None) for i in range(4)},
                **{f"b{i}": RecallLabel(f"b{i}", "FOUND", None) for i in range(9)},
            }
        }
        counts = analysis.compute_recall_counts(records, labels)
        kept = analysis.kept_recall_defect_ids(counts, (ARM_BASELINE, ARM_X), k=k)
        assert kept == []

    def test_defect_with_exactly_k_over_2_completed_runs_is_kept(self) -> None:
        k = 10
        records = (
            [_run_record("d1", ARM_BASELINE, f"a{i}") for i in range(5)]
            + [_run_record("d1", ARM_X, f"b{i}") for i in range(5)]
        )
        labels = {
            "d1": {
                **{f"a{i}": RecallLabel(f"a{i}", "FOUND", None) for i in range(5)},
                **{f"b{i}": RecallLabel(f"b{i}", "FOUND", None) for i in range(5)},
            }
        }
        counts = analysis.compute_recall_counts(records, labels)
        kept = analysis.kept_recall_defect_ids(counts, (ARM_BASELINE, ARM_X), k=k)
        assert kept == ["d1"]

    def test_drop_count_and_effective_n_move_by_one(self) -> None:
        k = 10
        defect_ids = [f"d{i}" for i in range(3)]
        records = []
        labels: dict[str, dict[str, RecallLabel]] = {}
        for defect_id in defect_ids:
            for arm, count in ((ARM_BASELINE, 5), (ARM_X, 5)):
                for i in range(count):
                    run_id = f"{defect_id}-{arm}-{i}"
                    records.append(_run_record(defect_id, arm, run_id))
                    labels.setdefault(defect_id, {})[run_id] = RecallLabel(run_id, "FOUND", None)
        counts_before = analysis.compute_recall_counts(records, labels)
        kept_before = analysis.kept_recall_defect_ids(counts_before, (ARM_BASELINE, ARM_X), k=k)
        assert len(kept_before) == 3

        # Drop one defect's baseline-arm completed count to 4 (below k/2=5).
        records = [r for r in records if not (r.defect_id == "d0" and r.arm == ARM_BASELINE)]
        records += [_run_record("d0", ARM_BASELINE, f"d0-{ARM_BASELINE}-new{i}") for i in range(4)]
        labels["d0"] = {
            run_id: label for run_id, label in labels["d0"].items() if not run_id.startswith(f"d0-{ARM_BASELINE}")
        }
        for i in range(4):
            run_id = f"d0-{ARM_BASELINE}-new{i}"
            labels["d0"][run_id] = RecallLabel(run_id, "FOUND", None)
        counts_after = analysis.compute_recall_counts(records, labels)
        kept_after = analysis.kept_recall_defect_ids(counts_after, (ARM_BASELINE, ARM_X), k=k)
        assert len(kept_after) == len(kept_before) - 1
        assert "d0" not in kept_after


class TestArmRecallAndSensitivityVerdict:
    def _counts(self, rate_baseline: float, rate_x: float, n_defects: int = 20) -> dict[str, analysis.DefectRecallCounts]:
        return {
            f"d{i}": analysis.DefectRecallCounts(
                defect_id=f"d{i}", found_by_arm={ARM_BASELINE: round(rate_baseline * 10), ARM_X: round(rate_x * 10)},
                completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            )
            for i in range(n_defects)
        }

    def test_identical_arms_give_an_interval_around_zero(self) -> None:
        counts = self._counts(0.7, 0.7)
        ids = list(counts)
        lower, upper = analysis.bootstrap_interval(
            ids, lambda rs: analysis.arm_recall(counts, rs, ARM_BASELINE) - analysis.arm_recall(counts, rs, ARM_X),
            resamples=500, seed=1,
        )
        assert lower == pytest.approx(0.0, abs=1e-9)
        assert upper == pytest.approx(0.0, abs=1e-9)

    def test_a_known_shift_flips_the_recall_verdict_at_delta(self) -> None:
        # arm_x much worse than baseline -- non-inferiority must fail.
        counts = self._counts(0.9, 0.3)
        ids = list(counts)
        verdict, _ = analysis.recall_noninferiority_verdict(counts, ids, ARM_BASELINE, ARM_X, resamples=500, seed=1)
        assert verdict == analysis.NONINFERIORITY_FAIL

        # arm_x equal to baseline -- non-inferiority must pass.
        counts_equal = self._counts(0.9, 0.9)
        verdict_equal, _ = analysis.recall_noninferiority_verdict(
            counts_equal, list(counts_equal), ARM_BASELINE, ARM_X, resamples=500, seed=1,
        )
        assert verdict_equal == analysis.NONINFERIORITY_PASS


class TestPrecisionAndCertification:
    def _precision_counts(self, valid_baseline: int, total_baseline: int, valid_x: int, total_x: int, n: int = 20):
        return {
            f"d{i}": analysis.DefectPrecisionCounts(
                defect_id=f"d{i}", valid_by_arm={ARM_BASELINE: valid_baseline, ARM_X: valid_x},
                total_by_arm={ARM_BASELINE: total_baseline, ARM_X: total_x},
            )
            for i in range(n)
        }

    def test_a_known_shift_in_pooled_precision_flips_the_verdict_at_delta(self) -> None:
        counts = self._precision_counts(9, 10, 3, 10)
        ids = list(counts)
        verdict, _ = analysis.precision_noninferiority_verdict(counts, ids, ARM_BASELINE, ARM_X, resamples=500, seed=1)
        assert verdict == analysis.NONINFERIORITY_FAIL

    def test_later_arm_passing_only_one_gate_is_not_certified(self) -> None:
        assert analysis.certify_later_arm(analysis.NONINFERIORITY_PASS, analysis.NONINFERIORITY_FAIL) == (
            analysis.CERTIFICATION_NOT_CERTIFIED
        )
        assert analysis.certify_later_arm(analysis.NONINFERIORITY_FAIL, analysis.NONINFERIORITY_PASS) == (
            analysis.CERTIFICATION_NOT_CERTIFIED
        )

    def test_later_arm_passing_both_gates_is_certified(self) -> None:
        assert analysis.certify_later_arm(analysis.NONINFERIORITY_PASS, analysis.NONINFERIORITY_PASS) == (
            analysis.CERTIFICATION_CERTIFIED
        )

    def test_defect_with_missing_precision_judge_run_is_dropped_from_both_arms(self) -> None:
        recall_kept = ["d0", "d1", "d2"]
        precision_counts = {
            "d0": analysis.DefectPrecisionCounts(defect_id="d0", valid_by_arm={}, total_by_arm={}),
            "d1": analysis.DefectPrecisionCounts(defect_id="d1", valid_by_arm={}, total_by_arm={}),
            # d2's precision judge run never succeeded, so it has no entry at all.
        }
        kept = analysis.kept_precision_defect_ids(recall_kept, precision_counts)
        assert kept == ["d0", "d1"]


class TestHarnessClosure:
    def test_closure_includes_the_three_extra_files_and_excludes_transcript_analysis(self) -> None:
        closure = analysis.compute_harness_closure()
        assert "evals/measure_subagent_model_resolution.py" in closure
        assert "evals/run_skill_evals.py" in closure
        assert "claude/.claude/scripts/_config_dir.py" in closure
        assert not any("transcript_analysis" in path for path in closure)
        assert not any("mine_szz" in path or "mine_review_rounds" in path for path in closure)


class TestManifestAndFreezeChecks:
    def test_manifest_mismatch_exits_raises_naming_the_field(self) -> None:
        frozen = {"a.py": "hash1", "b.py": "hash2"}
        current = {"a.py": "hash1-changed", "b.py": "hash2"}
        with pytest.raises(analysis.HarnessInvalidatedError) as excinfo:
            analysis.check_manifest_matches(current, frozen)
        assert "a.py" in str(excinfo.value)

    def test_manifest_match_raises_nothing(self) -> None:
        manifest = {"a.py": "hash1"}
        analysis.check_manifest_matches(manifest, manifest)  # must not raise

    def test_freeze_exits_when_manifest_differs_from_last_smoke(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="manifest"):
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="different", k_to_freeze=10,
                smoke_full_k=10, provenance_failures=(), local_excerpts_present=True,
            )

    def test_freeze_exits_when_k_differs_from_smoke_full_k(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="K"):
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="abc", k_to_freeze=15,
                smoke_full_k=10, provenance_failures=(), local_excerpts_present=True,
            )

    def test_freeze_exits_when_a_record_fails_provenance(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="provenance"):
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="abc", k_to_freeze=10,
                smoke_full_k=10, provenance_failures=[("d1", "shares word run 'x'")], local_excerpts_present=True,
            )

    def test_freeze_exits_when_local_excerpts_are_absent(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="excerpts"):
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="abc", k_to_freeze=10,
                smoke_full_k=10, provenance_failures=(), local_excerpts_present=False,
            )

    def test_editing_judge_model_id_after_a_passing_smoke_makes_freeze_exit_on_manifest(self) -> None:
        # runner.py's own source hash changes whenever JUDGE_MODEL_ID's
        # literal changes, since it is one of the closure's own files -- a
        # smoke-recorded manifest from before that edit no longer matches.
        closure_before = {"evals/review_bench/runner.py": "hash-with-old-judge-id"}
        closure_after = {"evals/review_bench/runner.py": "hash-with-new-judge-id"}
        manifest_before = analysis.closure_manifest_hash(closure_before)
        manifest_after = analysis.closure_manifest_hash(closure_after)
        assert manifest_before != manifest_after
        with pytest.raises(analysis.HarnessInvalidatedError):
            analysis.check_freeze_preconditions(
                current_manifest_hash=manifest_after, last_smoke_manifest_hash=manifest_before, k_to_freeze=10,
                smoke_full_k=10, provenance_failures=(), local_excerpts_present=True,
            )


class TestLoadBaselineConditions:
    def test_loads_environment_and_harness_closure_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "conditions.json"
        path.write_text(json.dumps({
            "environment": {"cli_version": "2.1.0", "ambient_config_commit": "cafebabe"},
            "harness_closure": {"a.py": "hash1"},
        }))

        cli_version, ambient_config_commit, harness_closure = analysis.load_baseline_conditions(path)

        assert cli_version == "2.1.0"
        assert ambient_config_commit == "cafebabe"
        assert harness_closure == {"a.py": "hash1"}

    def test_missing_environment_key_raises_naming_the_path(self, tmp_path: Path) -> None:
        path = tmp_path / "conditions.json"
        path.write_text(json.dumps({"harness_closure": {}}))

        with pytest.raises(analysis.HarnessInvalidatedError, match="unreadable or missing an expected field"):
            analysis.load_baseline_conditions(path)

    def test_non_dict_harness_closure_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "conditions.json"
        path.write_text(json.dumps({
            "environment": {"cli_version": "2.0.0", "ambient_config_commit": "deadbeef"},
            "harness_closure": "corrupt",
        }))

        with pytest.raises(analysis.HarnessInvalidatedError, match="harness_closure must be an object"):
            analysis.load_baseline_conditions(path)


class TestEnvironmentChecks:
    def test_later_arm_campaign_with_different_environment_invalidates_even_with_matching_hashes(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1", cli_version="2.1.0", ambient_config_commit="cafebabe")]
        with pytest.raises(analysis.HarnessInvalidatedError, match="invalidated -- rerun all arms"):
            analysis.check_environment_matches_baseline(
                records, baseline_cli_version="2.0.0", baseline_ambient_config_commit="deadbeef",
            )

    def test_matching_environment_raises_nothing(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1", cli_version="2.0.0", ambient_config_commit="deadbeef")]
        analysis.check_environment_matches_baseline(
            records, baseline_cli_version="2.0.0", baseline_ambient_config_commit="deadbeef",
        )  # must not raise

    def test_campaign_with_two_environments_exits_naming_each(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", cli_version="2.0.0", ambient_config_commit="deadbeef"),
            _run_record("d2", ARM_BASELINE, "r2", cli_version="2.1.0", ambient_config_commit="cafebabe"),
        ]
        with pytest.raises(analysis.HarnessInvalidatedError) as excinfo:
            analysis.check_campaign_environment_consistency(records)
        assert "d1" in str(excinfo.value)
        assert "d2" in str(excinfo.value)

    def test_campaign_with_one_environment_raises_nothing(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", cli_version="2.0.0", ambient_config_commit="deadbeef"),
            _run_record("d2", ARM_BASELINE, "r2", cli_version="2.0.0", ambient_config_commit="deadbeef"),
        ]
        analysis.check_campaign_environment_consistency(records)  # must not raise


class TestOutOfSessionCountsNeverCarryPaths:
    def test_reports_counts_per_arm_only(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", out_of_session_paths=("/etc/hostname",)),
            _run_record("d1", ARM_X, "r2", out_of_session_paths=()),
        ]
        counts = analysis.out_of_session_counts_by_arm(records)
        assert counts == {ARM_BASELINE: 1, ARM_X: 0}
        assert "/etc/hostname" not in str(counts)

    def test_aggregates_judge_records_keyed_by_judge_kind(self) -> None:
        """out_of_session_counts_by_arm only ever keys off `record.arm`, so
        it aggregates a judge campaign's records the same way -- a judge
        RunRecord's own `arm` field holds JUDGE_ARM_RECALL/JUDGE_ARM_PRECISION
        rather than a reviewer arm."""
        from review_bench.adjudicate import JUDGE_ARM_PRECISION, JUDGE_ARM_RECALL

        records = [
            _run_record("d1", JUDGE_ARM_RECALL, "j1", out_of_session_paths=("/etc/hostname",)),
            _run_record("d1", JUDGE_ARM_PRECISION, "j2", out_of_session_paths=()),
        ]
        counts = analysis.out_of_session_counts_by_arm(records)
        assert counts == {JUDGE_ARM_RECALL: 1, JUDGE_ARM_PRECISION: 0}


class TestPrecisionCounts:
    def test_pooled_precision_is_valid_over_all_adjudicated_findings(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1"), _run_record("d1", ARM_BASELINE, "r2")]
        labels = {
            "d1": {
                "r1": [
                    PrecisionFinding(label="VALID", quoted_opening="x"),
                    PrecisionFinding(label="INVALID", quoted_opening="y"),
                ],
                "r2": [PrecisionFinding(label="VALID", quoted_opening="z")],
            }
        }
        counts = analysis.compute_precision_counts(records, labels)
        assert analysis.pooled_precision(counts, ["d1"], ARM_BASELINE) == pytest.approx(2 / 3)


class TestSecondaryReportColumns:
    """Never-gating columns the go/no-go call still reads (README's "Reading
    the report" section)."""

    def test_read_token_stats_means_per_arm(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", read_tokens_est=100),
            _run_record("d1", ARM_BASELINE, "r2", read_tokens_est=200),
            _run_record("d1", ARM_X, "r3", read_tokens_est=50),
        ]
        assert analysis.read_token_stats(records) == {ARM_BASELINE: 150.0, ARM_X: 50.0}

    def test_read_token_stats_empty_input_returns_empty_mapping(self) -> None:
        assert analysis.read_token_stats([]) == {}

    def test_partial_and_paged_counts_sums_per_arm(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", partial_view_reads=2, paged_followups=1),
            _run_record("d1", ARM_BASELINE, "r2", partial_view_reads=3, paged_followups=0),
        ]
        assert analysis.partial_and_paged_counts(records) == {
            ARM_BASELINE: {"partial_view_reads": 5, "paged_followups": 1},
        }

    def test_partial_and_paged_counts_empty_input_returns_empty_mapping(self) -> None:
        assert analysis.partial_and_paged_counts([]) == {}

    def test_whole_file_read_adherence_means_per_arm(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", whole_file_reads_of_changed_files=2),
            _run_record("d1", ARM_BASELINE, "r2", whole_file_reads_of_changed_files=4),
        ]
        assert analysis.whole_file_read_adherence(records) == {ARM_BASELINE: 3.0}

    def test_whole_file_read_adherence_empty_input_returns_empty_mapping(self) -> None:
        assert analysis.whole_file_read_adherence([]) == {}

    def test_missing_run_counts_by_reason_groups_non_ok_records_per_arm(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", status="missing", missing_reason="timeout"),
            _run_record("d1", ARM_BASELINE, "r2", status="missing", missing_reason="timeout"),
            _run_record("d1", ARM_BASELINE, "r3", status=runner.STATUS_OK),
            _run_record("d1", ARM_X, "r4", status="missing", missing_reason=None),
        ]
        assert analysis.missing_run_counts_by_reason(records) == {
            ARM_BASELINE: {"timeout": 2}, ARM_X: {"unknown": 1},
        }

    def test_missing_run_counts_by_reason_empty_input_returns_empty_mapping(self) -> None:
        assert analysis.missing_run_counts_by_reason([]) == {}

    def test_recall_by_fix_date_half_splits_at_the_median_fix_date(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 8}, completed_by_arm={ARM_BASELINE: 10},
            ),
            "d2": analysis.DefectRecallCounts(
                defect_id="d2", found_by_arm={ARM_BASELINE: 2}, completed_by_arm={ARM_BASELINE: 10},
            ),
        }
        fix_dates = {"d1": "2024-01-01", "d2": "2024-06-01"}
        result = analysis.recall_by_fix_date_half(counts, ["d1", "d2"], fix_dates, ARM_BASELINE)
        assert result == {"earlier_half": pytest.approx(0.8), "later_half": pytest.approx(0.2)}

    def test_recall_by_fix_date_half_empty_kept_defects_returns_zero_both_halves(self) -> None:
        result = analysis.recall_by_fix_date_half({}, [], {}, ARM_BASELINE)
        assert result == {"earlier_half": 0.0, "later_half": 0.0}

    def test_recall_by_fix_date_half_odd_count_puts_the_extra_defect_in_the_later_half(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 8}, completed_by_arm={ARM_BASELINE: 10},
            ),
            "d2": analysis.DefectRecallCounts(
                defect_id="d2", found_by_arm={ARM_BASELINE: 5}, completed_by_arm={ARM_BASELINE: 10},
            ),
            "d3": analysis.DefectRecallCounts(
                defect_id="d3", found_by_arm={ARM_BASELINE: 2}, completed_by_arm={ARM_BASELINE: 10},
            ),
        }
        fix_dates = {"d1": "2024-01-01", "d2": "2024-03-01", "d3": "2024-06-01"}
        result = analysis.recall_by_fix_date_half(counts, ["d1", "d2", "d3"], fix_dates, ARM_BASELINE)
        assert result == {"earlier_half": pytest.approx(0.8), "later_half": pytest.approx(0.35)}

    def test_recall_by_fix_date_half_sorts_by_chronological_instant_not_by_lexicographic_string(self) -> None:
        """d1's fix_date string sorts lexicographically before d2's, but a
        -08:00 offset on d1 and a +05:00 offset on d2 put d1's instant
        (2024-01-16T07:00:00Z) after d2's (2024-01-15T20:00:00Z) -- a raw
        string sort (the pre-fix behavior) would put d1 in the earlier
        half instead of d2, asserting the wrong values below."""
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 2}, completed_by_arm={ARM_BASELINE: 10},
            ),
            "d2": analysis.DefectRecallCounts(
                defect_id="d2", found_by_arm={ARM_BASELINE: 8}, completed_by_arm={ARM_BASELINE: 10},
            ),
        }
        fix_dates = {"d1": "2024-01-15T23:00:00-08:00", "d2": "2024-01-16T01:00:00+05:00"}
        assert fix_dates["d1"] < fix_dates["d2"]  # lexicographically, the reverse of chronological order

        result = analysis.recall_by_fix_date_half(counts, ["d1", "d2"], fix_dates, ARM_BASELINE)

        assert result == {"earlier_half": pytest.approx(0.8), "later_half": pytest.approx(0.2)}

    def test_recall_diff_over_read_cap_stratum_is_arm_x_minus_baseline_on_the_stratum(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 5, ARM_X: 9}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
            "d2": analysis.DefectRecallCounts(
                defect_id="d2", found_by_arm={ARM_BASELINE: 5, ARM_X: 5}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
        }
        diff = analysis.recall_diff_over_read_cap_stratum(counts, ["d1", "d2"], ["d1"], ARM_BASELINE, ARM_X)
        assert diff == pytest.approx(0.4)

    def test_recall_diff_over_read_cap_stratum_empty_kept_defects_is_zero(self) -> None:
        diff = analysis.recall_diff_over_read_cap_stratum({}, [], [], ARM_BASELINE, ARM_X)
        assert diff == 0.0

    def test_observed_sigma_d_matches_stdev_of_per_defect_differences(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 5, ARM_X: 9}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
            "d2": analysis.DefectRecallCounts(
                defect_id="d2", found_by_arm={ARM_BASELINE: 5, ARM_X: 5}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
            "d3": analysis.DefectRecallCounts(
                defect_id="d3", found_by_arm={ARM_BASELINE: 2, ARM_X: 6}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
        }
        result = analysis.observed_sigma_d(counts, ["d1", "d2", "d3"], ARM_BASELINE, ARM_X)
        assert result == pytest.approx(statistics.stdev([0.4, 0.0, 0.4]))

    def test_observed_sigma_d_single_kept_defect_is_zero(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 5, ARM_X: 9}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
        }
        result = analysis.observed_sigma_d(counts, ["d1"], ARM_BASELINE, ARM_X)
        assert result == 0.0

    def test_observed_sigma_d_empty_kept_defects_is_zero(self) -> None:
        assert analysis.observed_sigma_d({}, [], ARM_BASELINE, ARM_X) == 0.0
