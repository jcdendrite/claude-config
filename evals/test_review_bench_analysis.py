"""Tests for evals/review_bench/analysis.py. Offline throughout: every
statistic and check works from synthetic RunRecord/label data, never a real
campaign. No test launches `claude`.
"""
from __future__ import annotations

import pytest
from review_bench import analysis, runner
from review_bench.adjudicate import PrecisionFinding, RecallLabel

ARM_BASELINE = "current-rule"
ARM_X = "function-context"


def _run_record(
    defect_id: str, arm: str, opaque_run_id: str, *, cli_version: str = "2.0.0",
    ambient_config_commit: str = "deadbeef", missing_reason: str | None = None, status: str = "ok",
    out_of_session_paths: tuple[str, ...] = (),
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=0, opaque_run_id=opaque_run_id,
        status=status, missing_reason=missing_reason, observed_model="claude-sonnet-5",
        observed_tools=("Read",), out_of_session_paths=out_of_session_paths, findings_text="x",
        wall_clock_s=1.0, read_calls=1, read_tokens_est=10, partial_view_reads=0, paged_followups=0,
        whole_file_reads_of_changed_files=0, dispatch_prompt_verbatim=True, cli_version=cli_version,
        ambient_config_commit=ambient_config_commit,
    )


class TestNMin:
    def test_n_min_at_k_10_is_126(self) -> None:
        assert analysis.n_min(10) == 126

    def test_n_min_at_k_15_is_95(self) -> None:
        assert analysis.n_min(15) == 95

    def test_n_min_at_k_20_is_79(self) -> None:
        assert analysis.n_min(20) == 79


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
        rather than a reviewer arm (Approach > "Out-of-session reads")."""
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
