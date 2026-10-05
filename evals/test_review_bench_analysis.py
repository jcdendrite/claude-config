"""Tests for evals/review_bench/analysis.py. Offline throughout: every
statistic and check works from synthetic RunRecord/label data, never a real
campaign. No test launches `claude`.
"""
from __future__ import annotations

import json
import shutil
import statistics
from pathlib import Path

import pytest
from review_bench import adjudicate, analysis, defects, runner
from review_bench.adjudicate import PrecisionFinding, RecallLabel

ARM_BASELINE = "current-rule"
ARM_X = "function-context"


def _own_fixture_each(defect_ids) -> dict[str, tuple[str, str]]:
    """Every defect on a fixture of its own, so a cluster bootstrap resamples
    per defect."""
    return {defect_id: (defect_id, defect_id) for defect_id in defect_ids}


def _run_record(
    defect_id: str, arm: str, opaque_run_id: str, *, cli_version: str = "2.0.0",
    ambient_config_commit: str = "deadbeef", missing_reason: str | None = None, status: str = "ok",
    out_of_session_paths: tuple[str, ...] = (), read_tokens_est: int = 10, partial_view_reads: int = 0,
    paged_followups: int = 0, whole_file_reads_of_changed_files: int = 0, over_read_cap: bool = False,
    total_cost_usd: float | None = None,
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=0, opaque_run_id=opaque_run_id,
        status=status, missing_reason=missing_reason, observed_model="claude-sonnet-5",
        observed_tools=("Read",), out_of_session_paths=out_of_session_paths, findings_text="x",
        wall_clock_s=1.0, read_calls=1, read_tokens_est=read_tokens_est,
        partial_view_reads=partial_view_reads, paged_followups=paged_followups,
        whole_file_reads_of_changed_files=whole_file_reads_of_changed_files, over_read_cap=over_read_cap,
        dispatch_prompt_verbatim=True, cli_version=cli_version, ambient_config_commit=ambient_config_commit,
        total_cost_usd=total_cost_usd,
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
            cluster_by_defect=_own_fixture_each(ids), resamples=500, seed=1,
        )
        assert lower == pytest.approx(0.0, abs=1e-9)
        assert upper == pytest.approx(0.0, abs=1e-9)

    def test_a_known_shift_flips_the_recall_verdict_at_delta(self) -> None:
        # arm_x much worse than baseline -- non-inferiority must fail.
        counts = self._counts(0.9, 0.3)
        ids = list(counts)
        verdict, _ = analysis.recall_noninferiority_verdict(
            counts, ids, ARM_BASELINE, ARM_X, resamples=500, seed=1, cluster_by_defect=_own_fixture_each(counts),
        )
        assert verdict == analysis.NONINFERIORITY_FAIL

        # arm_x equal to baseline -- non-inferiority must pass.
        counts_equal = self._counts(0.9, 0.9)
        verdict_equal, _ = analysis.recall_noninferiority_verdict(
            counts_equal, list(counts_equal), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts_equal),
        )
        assert verdict_equal == analysis.NONINFERIORITY_PASS


    def test_baseline_sensitivity_is_sensitive_only_when_the_first_arm_recalls_more(self) -> None:
        counts = self._counts(0.9, 0.3)
        ids = list(counts)
        verdict, (lower, upper) = analysis.baseline_sensitivity_verdict(
            counts, ids, ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert verdict == analysis.SENSITIVITY_SENSITIVE
        assert lower > analysis.DELTA
        assert upper >= lower
        swapped_verdict, _ = analysis.baseline_sensitivity_verdict(
            counts, ids, ARM_X, ARM_BASELINE, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert swapped_verdict == analysis.SENSITIVITY_NOT_SENSITIVE

    def test_baseline_sensitivity_is_not_sensitive_when_the_arms_recall_the_same(self) -> None:
        counts = self._counts(0.7, 0.7)
        verdict, _ = analysis.baseline_sensitivity_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert verdict == analysis.SENSITIVITY_NOT_SENSITIVE


class TestVerdictsReadTheIntervalLowerLimit:
    """Two-defect tables make the bootstrap interval hand-computable. A
    resample draws two defects with replacement: (a, a) with probability
    1/4, (a, b) or (b, a) with 1/2, (b, b) with 1/4. Each resample's
    statistic is the mean of its two per-defect differences, so it takes the
    value a, (a+b)/2 or b. The 2.5th percentile falls inside the a-mass
    (a lies below the 25th percentile), and the 97.5th inside the b-mass.
    So the interval is exactly [min(a, b), max(a, b)] while the point
    estimate over the full defect list is (a+b)/2. A verdict that reads the
    point estimate or the upper limit instead of the lower limit gives the
    opposite answer on the failing tables below."""

    COMPLETED = 100

    def _recall_counts(self, found_baseline_and_x_per_defect: list[tuple[int, int]]):
        return {
            f"d{i}": analysis.DefectRecallCounts(
                defect_id=f"d{i}", found_by_arm={ARM_BASELINE: found_baseline, ARM_X: found_x},
                completed_by_arm={ARM_BASELINE: self.COMPLETED, ARM_X: self.COMPLETED},
            )
            for i, (found_baseline, found_x) in enumerate(found_baseline_and_x_per_defect)
        }

    def _precision_counts(self, valid_baseline_and_x_per_defect: list[tuple[int, int]]):
        # Every defect carries COMPLETED adjudicated findings per arm, so the
        # pooled ratio reduces to the mean of per-defect valid rates.
        return {
            f"d{i}": analysis.DefectPrecisionCounts(
                defect_id=f"d{i}", valid_by_arm={ARM_BASELINE: valid_baseline, ARM_X: valid_x},
                total_by_arm={ARM_BASELINE: self.COMPLETED, ARM_X: self.COMPLETED},
            )
            for i, (valid_baseline, valid_x) in enumerate(valid_baseline_and_x_per_defect)
        }

    def test_recall_noninferiority_fails_when_only_the_point_estimate_clears_the_margin(self) -> None:
        """Per-defect recall_X - recall_1: d0 = 0.60 - 0.80 = -0.20 and
        d1 = 0.70 - 0.50 = +0.20. Interval = [-0.20, +0.20], point estimate =
        0. The point estimate and the upper limit both clear -delta (-0.05);
        the lower limit -0.20 does not, so the verdict is fail."""
        counts = self._recall_counts([(80, 60), (50, 70)])
        verdict, (lower, upper) = analysis.recall_noninferiority_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert lower == pytest.approx(-0.20)
        assert upper == pytest.approx(0.20)
        assert verdict == analysis.NONINFERIORITY_FAIL

    def test_recall_noninferiority_passes_when_the_lower_limit_clears_the_margin(self) -> None:
        """d0 = 0.76 - 0.80 = -0.04 and d1 = 0.60 - 0.50 = +0.10. Interval =
        [-0.04, +0.10]; the lower limit -0.04 exceeds -delta (-0.05), so the
        verdict is pass."""
        counts = self._recall_counts([(80, 76), (50, 60)])
        verdict, (lower, upper) = analysis.recall_noninferiority_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert lower == pytest.approx(-0.04)
        assert upper == pytest.approx(0.10)
        assert verdict == analysis.NONINFERIORITY_PASS

    def test_precision_noninferiority_fails_when_only_the_point_estimate_clears_the_margin(self) -> None:
        """Per-defect precision_X - precision_1: d0 = 0.70 - 0.90 = -0.20 and
        d1 = 0.70 - 0.50 = +0.20. Interval = [-0.20, +0.20], point estimate =
        0; only the lower limit -0.20 misses -delta, so the verdict is fail."""
        counts = self._precision_counts([(90, 70), (50, 70)])
        verdict, (lower, upper), _ = analysis.precision_noninferiority_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert lower == pytest.approx(-0.20)
        assert upper == pytest.approx(0.20)
        assert verdict == analysis.NONINFERIORITY_FAIL

    def test_precision_noninferiority_passes_when_the_lower_limit_clears_the_margin(self) -> None:
        """d0 = 0.76 - 0.80 = -0.04 and d1 = 0.60 - 0.50 = +0.10. Interval =
        [-0.04, +0.10]; the lower limit -0.04 exceeds -delta, so the verdict
        is pass."""
        counts = self._precision_counts([(80, 76), (50, 60)])
        verdict, (lower, upper), _ = analysis.precision_noninferiority_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert lower == pytest.approx(-0.04)
        assert upper == pytest.approx(0.10)
        assert verdict == analysis.NONINFERIORITY_PASS

    def test_baseline_sensitivity_is_not_sensitive_when_only_the_point_estimate_clears_delta(self) -> None:
        """Per-defect recall_1 - recall_2: d0 = 0.52 - 0.50 = +0.02 and
        d1 = 0.80 - 0.50 = +0.30. Interval = [+0.02, +0.30], point estimate =
        0.16. The point estimate and the upper limit both exceed delta
        (0.05); the lower limit 0.02 does not, so the baseline is
        not-sensitive."""
        counts = self._recall_counts([(52, 50), (80, 50)])
        verdict, (lower, upper) = analysis.baseline_sensitivity_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert lower == pytest.approx(0.02)
        assert upper == pytest.approx(0.30)
        assert verdict == analysis.SENSITIVITY_NOT_SENSITIVE

    def test_baseline_sensitivity_is_sensitive_when_the_lower_limit_exceeds_delta(self) -> None:
        """d0 = 0.60 - 0.50 = +0.10 and d1 = 0.80 - 0.50 = +0.30. Interval =
        [+0.10, +0.30]; the lower limit 0.10 exceeds delta (0.05), so the
        baseline is sensitive."""
        counts = self._recall_counts([(60, 50), (80, 50)])
        verdict, (lower, upper) = analysis.baseline_sensitivity_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert lower == pytest.approx(0.10)
        assert upper == pytest.approx(0.30)
        assert verdict == analysis.SENSITIVITY_SENSITIVE


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
        verdict, _, _ = analysis.precision_noninferiority_verdict(
            counts, ids, ARM_BASELINE, ARM_X, resamples=500, seed=1, cluster_by_defect=_own_fixture_each(counts),
        )
        assert verdict == analysis.NONINFERIORITY_FAIL

    def test_equal_pooled_precision_passes_noninferiority(self) -> None:
        counts = self._precision_counts(9, 10, 9, 10)
        verdict, _, _ = analysis.precision_noninferiority_verdict(
            counts, list(counts), ARM_BASELINE, ARM_X, resamples=500, seed=1,
            cluster_by_defect=_own_fixture_each(counts),
        )
        assert verdict == analysis.NONINFERIORITY_PASS

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

    def test_closure_includes_every_review_bench_module_the_run_judge_and_analyze_paths_import(self) -> None:
        closure = analysis.compute_harness_closure()
        for module in ("adjudicate", "analysis", "arms", "defects", "fixture_repo", "identifiers", "runner"):
            assert f"evals/review_bench/{module}.py" in closure


class TestHarnessClosureOverASyntheticTree:
    """The import walk, run over a tree it can be pointed at, resolves each
    import form and terminates on a cycle."""

    @pytest.fixture
    def synthetic_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        package_dir = tmp_path / "evals" / "review_bench"
        package_dir.mkdir(parents=True)
        (tmp_path / "claude" / ".claude" / "scripts").mkdir(parents=True)
        files = {
            "__init__.py": "",
            "root_mod.py": (
                "from review_bench import from_package_import\n"
                "from review_bench.from_module_import import name\n"
                "from . import relative_sibling\n"
                "import top_level_helper\n"
                "import os\n"
            ),
            "from_package_import.py": "from review_bench import root_mod\n",  # cycle back to the root
            "from_module_import.py": "name = 1\n",
            "relative_sibling.py": "value = 1\n",
            "unreachable.py": "value = 2\n",
        }
        for filename, source in files.items():
            (package_dir / filename).write_text(source)
        (tmp_path / "evals" / "top_level_helper.py").write_text("helper = 1\n")
        monkeypatch.setattr(analysis, "EVALS_DIR", tmp_path / "evals")
        monkeypatch.setattr(analysis, "REVIEW_BENCH_DIR", package_dir)
        monkeypatch.setattr(analysis, "CONFIG_SCRIPTS_DIR", tmp_path / "claude" / ".claude" / "scripts")
        monkeypatch.setattr(analysis, "_CLOSURE_ROOTS", ("review_bench.root_mod",))
        return tmp_path

    def test_resolves_from_package_import_from_module_import_relative_and_top_level_imports(
        self, synthetic_tree: Path,
    ) -> None:
        closure = analysis.compute_harness_closure(repo_root=synthetic_tree)
        assert set(closure) == {
            "evals/review_bench/__init__.py",
            "evals/review_bench/root_mod.py",
            "evals/review_bench/from_package_import.py",
            "evals/review_bench/from_module_import.py",
            "evals/review_bench/relative_sibling.py",
            "evals/top_level_helper.py",
        }

    def test_editing_a_closure_member_changes_the_manifest_hash_and_fails_the_freeze_precondition(
        self, synthetic_tree: Path,
    ) -> None:
        manifest_before = analysis.closure_manifest_hash(analysis.compute_harness_closure(repo_root=synthetic_tree))
        (synthetic_tree / "evals" / "review_bench" / "from_module_import.py").write_text("name = 2\n")
        manifest_after = analysis.closure_manifest_hash(analysis.compute_harness_closure(repo_root=synthetic_tree))

        assert manifest_after != manifest_before
        with pytest.raises(analysis.HarnessInvalidatedError, match="manifest"):
            analysis.check_freeze_preconditions(
                current_manifest_hash=manifest_after, last_smoke_manifest_hash=manifest_before, k_to_freeze=10,
                smoke_full_k=10, provenance_failures=(), review_round_defects_without_excerpt=(),
            )

    def test_editing_a_module_outside_the_closure_leaves_the_manifest_hash_unchanged(
        self, synthetic_tree: Path,
    ) -> None:
        manifest_before = analysis.closure_manifest_hash(analysis.compute_harness_closure(repo_root=synthetic_tree))
        (synthetic_tree / "evals" / "review_bench" / "unreachable.py").write_text("value = 3\n")
        manifest_after = analysis.closure_manifest_hash(analysis.compute_harness_closure(repo_root=synthetic_tree))

        assert manifest_after == manifest_before


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
                smoke_full_k=10, provenance_failures=(), review_round_defects_without_excerpt=(),
            )

    def test_freeze_exits_when_k_differs_from_smoke_full_k(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="K"):
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="abc", k_to_freeze=15,
                smoke_full_k=10, provenance_failures=(), review_round_defects_without_excerpt=(),
            )

    def test_freeze_exits_when_a_record_fails_provenance(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="provenance"):
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="abc", k_to_freeze=10,
                smoke_full_k=10, provenance_failures=[("d1", "shares word run 'x'")],
                review_round_defects_without_excerpt=(),
            )

    def test_freeze_exits_naming_each_review_round_record_with_no_local_excerpt(self) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="excerpt") as excinfo:
            analysis.check_freeze_preconditions(
                current_manifest_hash="abc", last_smoke_manifest_hash="abc", k_to_freeze=10,
                smoke_full_k=10, provenance_failures=(), review_round_defects_without_excerpt=("rr-1", "rr-2"),
            )
        assert "rr-1, rr-2" in str(excinfo.value)


class TestLoadFrozenConditions:
    def test_loads_environment_and_harness_closure_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "conditions.json"
        path.write_text(json.dumps({
            "environment": {"cli_version": "2.1.0", "ambient_config_commit": "cafebabe"},
            "harness_closure": {"a.py": "hash1"},
        }))

        conditions = analysis.load_frozen_conditions(path)

        assert conditions["environment"] == {"cli_version": "2.1.0", "ambient_config_commit": "cafebabe"}
        assert conditions["harness_closure"] == {"a.py": "hash1"}

    def test_missing_environment_key_raises_naming_the_path(self, tmp_path: Path) -> None:
        path = tmp_path / "conditions.json"
        path.write_text(json.dumps({"harness_closure": {}}))

        with pytest.raises(analysis.HarnessInvalidatedError, match="unreadable or missing an expected field"):
            analysis.load_frozen_conditions(path)

    def test_non_dict_harness_closure_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "conditions.json"
        path.write_text(json.dumps({
            "environment": {"cli_version": "2.0.0", "ambient_config_commit": "deadbeef"},
            "harness_closure": "corrupt",
        }))

        with pytest.raises(analysis.HarnessInvalidatedError, match="harness_closure must be an object"):
            analysis.load_frozen_conditions(path)


class TestHashDirectory:
    def test_missing_directory_raises_instead_of_hashing_nothing(self, tmp_path: Path) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="missing or holds no files"):
            analysis.hash_directory(tmp_path / "absent")

    def test_directory_with_no_files_raises_instead_of_hashing_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "empty" / "nested").mkdir(parents=True)

        with pytest.raises(analysis.HarnessInvalidatedError, match="missing or holds no files"):
            analysis.hash_directory(tmp_path / "empty")


def _frozen_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A defects file, both arm snapshot dirs, and private copies of the two
    judge agent files, so a test can edit each without touching the repo."""
    defects_path = tmp_path / "defects.json"
    defects.save_confirmed_defects(defects_path, [
        defects.ConfirmedDefect(
            id=defect_id, source="szz", lens="staff-backend-engineer", base_commit="a" * 40, head_commit="b" * 40,
            fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            path="app.py", file_is_markdown=False,
        )
        for defect_id in ("d1", "d2")
    ])
    arms_root = tmp_path / "arms"
    for arm in (ARM_BASELINE, ARM_X):
        (arms_root / arm).mkdir(parents=True)
        (arms_root / arm / "bench-staff-backend-engineer.md").write_text(f"{arm} body\n")
    for attribute, name in (
        ("RECALL_JUDGE_AGENT_FILE", "bench-judge-recall.md"), ("PRECISION_JUDGE_AGENT_FILE", "bench-judge-precision.md"),
    ):
        judge_file = tmp_path / name
        judge_file.write_text(f"{name} rubric\n")
        monkeypatch.setattr(adjudicate, attribute, judge_file)
    return defects_path, arms_root


def _frozen(fields: dict) -> dict:
    return {**fields, "k": 10, "environment": {"cli_version": "2.0.0", "ambient_config_commit": "deadbeef"}}


class TestCheckAgainstFrozenConditions:
    def test_unchanged_files_and_matching_k_raise_nothing(self, tmp_path: Path, monkeypatch) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))

        analysis.check_against_frozen_conditions(
            frozen, analysis.compute_frozen_fields(defects_path, arms_root), k=10,
        )  # must not raise

    def test_frozen_fields_name_the_defect_ids_and_each_arm_judge_prompt_and_defects_hash(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)

        fields = analysis.compute_frozen_fields(defects_path, arms_root)

        assert fields["defect_ids"] == ["d1", "d2"]
        assert set(fields["arm_dir_hashes"]) == {ARM_BASELINE, ARM_X}
        assert set(fields["judge_agent_hashes"]) == {"bench-judge-recall", "bench-judge-precision"}
        assert set(fields["prompt_template_hashes"]) == {"review_prompt", "dispatch_prompt", "judge_inner_prompt"}
        assert fields["defects_json_hash"]

    @pytest.mark.parametrize(
        ("mutate", "expected_field"),
        [
            pytest.param(
                lambda root: (root / "arms" / ARM_BASELINE / "bench-staff-backend-engineer.md").write_text("edited\n"),
                f"arm_dir_hashes[{ARM_BASELINE}]", id="baseline-arm-body",
            ),
            pytest.param(
                lambda root: (root / "arms" / ARM_X / "bench-new-lens.md").write_text("added\n"),
                f"arm_dir_hashes[{ARM_X}]", id="file-added-to-an-arm-dir",
            ),
            pytest.param(
                lambda root: (root / "bench-judge-precision.md").write_text("edited rubric\n"),
                "judge_agent_hashes[bench-judge-precision]", id="judge-rubric",
            ),
            pytest.param(
                lambda root: (root / "defects.json").write_text("[]\n"), "defects_json_hash", id="defects-json",
            ),
        ],
    )
    def test_a_file_edited_after_the_freeze_invalidates_naming_the_field(
        self, tmp_path: Path, monkeypatch, mutate, expected_field: str,
    ) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))

        mutate(tmp_path)

        with pytest.raises(analysis.HarnessInvalidatedError, match="invalidated -- rerun all arms") as excinfo:
            analysis.check_against_frozen_conditions(
                frozen, analysis.compute_frozen_fields(defects_path, arms_root), k=10,
            )
        assert expected_field in str(excinfo.value)

    def test_an_edited_prompt_template_invalidates_naming_the_field(self, tmp_path: Path, monkeypatch) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))

        monkeypatch.setattr(runner, "REVIEW_PROMPT_TEMPLATE", runner.REVIEW_PROMPT_TEMPLATE + " Be thorough.")

        with pytest.raises(analysis.HarnessInvalidatedError, match=r"prompt_template_hashes\[review_prompt\]"):
            analysis.check_against_frozen_conditions(
                frozen, analysis.compute_frozen_fields(defects_path, arms_root), k=10,
            )

    def test_a_different_k_invalidates(self, tmp_path: Path, monkeypatch) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))

        with pytest.raises(analysis.HarnessInvalidatedError, match=r"--k 5 differs from the frozen k 10") as excinfo:
            analysis.check_against_frozen_conditions(
                frozen, analysis.compute_frozen_fields(defects_path, arms_root), k=5,
            )

        assert "-- pass --k 10" in str(excinfo.value)
        assert "rerun all arms" not in str(excinfo.value)

    def test_a_frozen_record_missing_a_field_invalidates_rather_than_passing(self, tmp_path: Path, monkeypatch) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))
        del frozen["arm_dir_hashes"]

        with pytest.raises(analysis.HarnessInvalidatedError, match="arm_dir_hashes"):
            analysis.check_against_frozen_conditions(
                frozen, analysis.compute_frozen_fields(defects_path, arms_root), k=10,
            )

    def test_a_missing_defects_file_fails_the_recompute(self, tmp_path: Path, monkeypatch) -> None:
        _defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)

        with pytest.raises(analysis.HarnessInvalidatedError, match="defects file .* is unreadable"):
            analysis.compute_frozen_fields(tmp_path / "absent.json", arms_root)

    def test_a_missing_arm_directory_fails_the_recompute(self, tmp_path: Path, monkeypatch) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        shutil.rmtree(arms_root / ARM_X)

        with pytest.raises(analysis.HarnessInvalidatedError, match="missing or holds no files"):
            analysis.compute_frozen_fields(defects_path, arms_root)


class TestCheckRecordDefectIdsMatch:
    def test_matching_ids_raise_nothing(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1"), _run_record("d2", ARM_BASELINE, "r2")]
        analysis.check_record_defect_ids_match(records, ["d1", "d2"])  # must not raise

    def test_a_record_for_an_unfrozen_defect_invalidates(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1"), _run_record("d9", ARM_BASELINE, "r2")]

        with pytest.raises(analysis.HarnessInvalidatedError, match=r"unfrozen defects: \['d9'\]"):
            analysis.check_record_defect_ids_match(records, ["d1"])

    def test_a_frozen_defect_with_no_record_invalidates(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1")]

        with pytest.raises(analysis.HarnessInvalidatedError, match=r"no records: \['d2'\]"):
            analysis.check_record_defect_ids_match(records, ["d1", "d2"])


class TestCheckEnvironmentReadingMatchesFrozen:
    frozen = {"environment": {"cli_version": "2.0.0", "ambient_config_commit": "deadbeef"}}

    def test_matching_reading_raises_nothing(self) -> None:
        analysis.check_environment_reading_matches_frozen(
            runner.EnvironmentRecord("2.0.0", "deadbeef"), self.frozen,
        )  # must not raise

    @pytest.mark.parametrize("reading", [
        runner.EnvironmentRecord("2.1.0", "deadbeef"), runner.EnvironmentRecord("2.0.0", "cafebabe"),
    ])
    def test_a_different_cli_version_or_commit_invalidates(self, reading: runner.EnvironmentRecord) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="invalidated -- rerun all arms"):
            analysis.check_environment_reading_matches_frozen(reading, self.frozen)


class TestEnvironmentChecks:
    def test_later_arm_campaign_with_different_environment_invalidates(self) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1", cli_version="2.1.0", ambient_config_commit="cafebabe")]
        with pytest.raises(analysis.HarnessInvalidatedError, match="invalidated -- rerun all arms"):
            analysis.check_environment_matches_baseline(
                records, baseline_cli_version="2.0.0", baseline_ambient_config_commit="deadbeef",
            )

    @pytest.mark.parametrize(
        ("cli_version", "ambient_config_commit"), [("2.1.0", "deadbeef"), ("2.0.0", "cafebabe")],
        ids=["cli_version-alone", "ambient_config_commit-alone"],
    )
    def test_a_later_arm_record_differing_in_one_field_alone_invalidates(
        self, cli_version: str, ambient_config_commit: str,
    ) -> None:
        records = [_run_record("d1", ARM_BASELINE, "r1", cli_version=cli_version, ambient_config_commit=ambient_config_commit)]
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

    def test_cost_totals_by_arm_sums_priced_runs_and_reports_how_many_were_priced(self) -> None:
        records = [
            _run_record("d1", ARM_BASELINE, "r1", total_cost_usd=0.5),
            _run_record("d1", ARM_BASELINE, "r2", total_cost_usd=0.25),
            _run_record("d1", ARM_BASELINE, "r3", total_cost_usd=None),
            _run_record("d1", ARM_X, "r4", total_cost_usd=None),
        ]
        assert analysis.cost_totals_by_arm(records) == {
            ARM_BASELINE: {"total_cost_usd": 0.75, "runs_priced": 2, "runs": 3},
            ARM_X: {"total_cost_usd": 0.0, "runs_priced": 0, "runs": 1},
        }

    def test_cost_totals_by_arm_empty_input_returns_empty_mapping(self) -> None:
        assert analysis.cost_totals_by_arm([]) == {}

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

    def test_recall_by_fix_date_half_empty_kept_defects_returns_null_both_halves(self) -> None:
        result = analysis.recall_by_fix_date_half({}, [], {}, ARM_BASELINE)
        assert result == {"earlier_half": None, "later_half": None}

    def test_recall_by_fix_date_half_single_kept_defect_leaves_the_earlier_half_null(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 8}, completed_by_arm={ARM_BASELINE: 10},
            ),
        }
        result = analysis.recall_by_fix_date_half(counts, ["d1"], {"d1": "2024-01-01"}, ARM_BASELINE)
        assert result == {"earlier_half": None, "later_half": pytest.approx(0.8)}

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
        string sort would put d1 in the earlier half instead of d2."""
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

    def test_recall_diff_over_read_cap_stratum_empty_kept_defects_is_null(self) -> None:
        assert analysis.recall_diff_over_read_cap_stratum({}, [], [], ARM_BASELINE, ARM_X) is None

    def test_recall_diff_over_read_cap_stratum_with_no_flagged_defect_is_null_not_zero_minus_zero(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 5, ARM_X: 9}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
        }
        assert analysis.recall_diff_over_read_cap_stratum(counts, ["d1"], [], ARM_BASELINE, ARM_X) is None

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
        ids = ["d1", "d2", "d3"]
        result = analysis.observed_sigma_d(counts, ids, ARM_BASELINE, ARM_X, cluster_by_defect=_own_fixture_each(ids))
        assert result == pytest.approx(statistics.stdev([0.4, 0.0, 0.4]))

    def test_observed_sigma_d_single_kept_defect_is_null(self) -> None:
        counts = {
            "d1": analysis.DefectRecallCounts(
                defect_id="d1", found_by_arm={ARM_BASELINE: 5, ARM_X: 9}, completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            ),
        }
        result = analysis.observed_sigma_d(
            counts, ["d1"], ARM_BASELINE, ARM_X, cluster_by_defect=_own_fixture_each(["d1"]),
        )
        assert result is None

    def test_observed_sigma_d_empty_kept_defects_is_null(self) -> None:
        assert analysis.observed_sigma_d({}, [], ARM_BASELINE, ARM_X, cluster_by_defect={}) is None

    def test_observed_sigma_d_with_several_defects_on_one_fixture_is_null(self) -> None:
        counts = {
            defect_id: analysis.DefectRecallCounts(
                defect_id=defect_id, found_by_arm={ARM_BASELINE: found, ARM_X: 5},
                completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            )
            for defect_id, found in (("d1", 5), ("d2", 9))
        }
        one_fixture = {"d1": ("base", "head"), "d2": ("base", "head")}
        assert analysis.observed_sigma_d(counts, ["d1", "d2"], ARM_BASELINE, ARM_X, cluster_by_defect=one_fixture) is None


_PASS, _FAIL = analysis.NONINFERIORITY_PASS, analysis.NONINFERIORITY_FAIL
_SENSITIVE, _NOT_SENSITIVE = analysis.SENSITIVITY_SENSITIVE, analysis.SENSITIVITY_NOT_SENSITIVE


def _gate_result(cell: str) -> analysis.GateResult:
    """A gate's later-arm evidence for one cell of the certification table. A
    cell with a `+` combines a failing test with a second attribute."""
    cells = {
        "pass": analysis.GateResult(_PASS, _PASS, _SENSITIVE, short=False),
        "fail": analysis.GateResult(_FAIL, _PASS, _SENSITIVE, short=False),
        "short": analysis.GateResult(_PASS, _PASS, _SENSITIVE, short=True),
        "not-sensitive": analysis.GateResult(_PASS, _PASS, _NOT_SENSITIVE, short=False),
        "fail+short": analysis.GateResult(_FAIL, _PASS, _SENSITIVE, short=True),
        "fail+not-sensitive": analysis.GateResult(_PASS, _FAIL, _NOT_SENSITIVE, short=False),
    }
    return cells[cell]


class TestCertifyLaterArm:
    """Certification precedence, one rule at both levels: a failing test
    dominates at any N, then a gate that is short or not sensitive, then pass."""

    @pytest.mark.parametrize("inconclusive_cell", ["short", "not-sensitive"])
    @pytest.mark.parametrize(("code_cell", "markdown_cell", "expected"), [
        pytest.param("pass", "pass", analysis.CERTIFICATION_CERTIFIED, id="pass-pass"),
        pytest.param("pass", "fail", analysis.CERTIFICATION_NOT_CERTIFIED, id="pass-fail"),
        pytest.param("pass", "inconclusive", analysis.CERTIFICATION_INCONCLUSIVE, id="pass-inconclusive"),
        pytest.param("fail", "pass", analysis.CERTIFICATION_NOT_CERTIFIED, id="fail-pass"),
        pytest.param("fail", "fail", analysis.CERTIFICATION_NOT_CERTIFIED, id="fail-fail"),
        pytest.param("fail", "inconclusive", analysis.CERTIFICATION_NOT_CERTIFIED, id="fail-inconclusive"),
        pytest.param("inconclusive", "pass", analysis.CERTIFICATION_INCONCLUSIVE, id="inconclusive-pass"),
        pytest.param("inconclusive", "fail", analysis.CERTIFICATION_NOT_CERTIFIED, id="inconclusive-fail"),
        pytest.param("inconclusive", "inconclusive", analysis.CERTIFICATION_INCONCLUSIVE, id="inconclusive-inconclusive"),
    ])
    def test_the_nine_gate_outcome_cells(
        self, code_cell: str, markdown_cell: str, expected: str, inconclusive_cell: str,
    ) -> None:
        cell_of = {"pass": "pass", "fail": "fail", "inconclusive": inconclusive_cell}
        result = analysis.certify_later_arm({
            analysis.GATE_CODE: _gate_result(cell_of[code_cell]),
            analysis.GATE_MARKDOWN: _gate_result(cell_of[markdown_cell]),
        })
        assert result == expected

    @pytest.mark.parametrize("failing_gate", [analysis.GATE_CODE, analysis.GATE_MARKDOWN])
    @pytest.mark.parametrize("failing_cell", ["fail+short", "fail+not-sensitive"])
    @pytest.mark.parametrize("other_cell", ["pass", "short", "not-sensitive"])
    def test_a_failing_gate_that_is_also_short_or_not_sensitive_is_not_certified(
        self, failing_gate: str, failing_cell: str, other_cell: str,
    ) -> None:
        other_gate = analysis.GATE_MARKDOWN if failing_gate == analysis.GATE_CODE else analysis.GATE_CODE
        result = analysis.certify_later_arm({failing_gate: _gate_result(failing_cell), other_gate: _gate_result(other_cell)})
        assert result == analysis.CERTIFICATION_NOT_CERTIFIED

    @pytest.mark.parametrize("failing_gate", [analysis.GATE_CODE, analysis.GATE_MARKDOWN])
    @pytest.mark.parametrize(("recall_verdict", "precision_verdict"), [
        pytest.param(_FAIL, _PASS, id="recall"), pytest.param(_PASS, _FAIL, id="precision"),
    ])
    def test_each_of_the_four_components_failing_alone_is_not_certified(
        self, failing_gate: str, recall_verdict: str, precision_verdict: str,
    ) -> None:
        other_gate = analysis.GATE_MARKDOWN if failing_gate == analysis.GATE_CODE else analysis.GATE_CODE
        result = analysis.certify_later_arm({
            failing_gate: analysis.GateResult(recall_verdict, precision_verdict, _SENSITIVE, short=False),
            other_gate: _gate_result("pass"),
        })
        assert result == analysis.CERTIFICATION_NOT_CERTIFIED

    @pytest.mark.parametrize(("recall_verdict", "precision_verdict"), [
        pytest.param(None, None, id="null-interval"),
        pytest.param(_PASS, None, id="null-precision-verdict-only"),
        pytest.param(None, _PASS, id="null-recall-verdict-only"),
    ])
    def test_a_gate_with_a_null_verdict_is_short_and_never_fails(
        self, recall_verdict: str | None, precision_verdict: str | None,
    ) -> None:
        null_gate = analysis.GateResult(recall_verdict, precision_verdict, _SENSITIVE, short=False)
        assert analysis.gate_outcome(null_gate) == analysis.GATE_OUTCOME_INCONCLUSIVE
        result = analysis.certify_later_arm({analysis.GATE_CODE: null_gate, analysis.GATE_MARKDOWN: _gate_result("pass")})
        assert result == analysis.CERTIFICATION_INCONCLUSIVE

    def test_a_null_precision_verdict_does_not_hide_a_failing_recall_test(self) -> None:
        result = analysis.certify_later_arm({
            analysis.GATE_CODE: analysis.GateResult(_FAIL, None, _SENSITIVE, short=False),
            analysis.GATE_MARKDOWN: _gate_result("pass"),
        })
        assert result == analysis.CERTIFICATION_NOT_CERTIFIED

    def test_a_null_baseline_verdict_makes_the_gate_inconclusive_never_sensitive(self) -> None:
        null_baseline = analysis.GateResult(_PASS, _PASS, None, short=False)
        assert analysis.gate_outcome(null_baseline) == analysis.GATE_OUTCOME_INCONCLUSIVE

    @pytest.mark.parametrize("gate_names", [
        pytest.param((analysis.GATE_CODE,), id="markdown-missing"),
        pytest.param((analysis.GATE_CODE, analysis.GATE_MARKDOWN, analysis.STRATUM_SECONDARY), id="stratum-carried"),
    ])
    def test_the_gates_alone_decide_so_any_other_key_set_is_refused(self, gate_names: tuple[str, ...]) -> None:
        with pytest.raises(ValueError, match="needs exactly the gates"):
            analysis.certify_later_arm({name: _gate_result("pass") for name in gate_names})


# The assignment of every (source, file_is_markdown) cell. Written independently of
# `analysis.GATING_RULE`, so an edit to the rule fails this table.
_EXPECTED_GATE_OF_CELL = {
    ("szz", False): "code",
    ("review-round", False): "code",
    ("review-round", True): "markdown",
    ("pr-comment", True): "markdown",
    ("pr-comment", False): "secondary",
    ("szz", True): "secondary",
}


def _defect_in_cell(source: str, file_is_markdown: bool) -> defects.ConfirmedDefect:
    return defects.ConfirmedDefect(
        id=f"{source}-{'md' if file_is_markdown else 'code'}", source=source, lens="staff-backend-engineer",
        base_commit="a" * 40, head_commit="b" * 40, fix_commit="c" * 40, fix_date="2024-01-01",
        description="test defect", path="notes.md" if file_is_markdown else "app.py",
        file_is_markdown=file_is_markdown,
    )


class TestGatingRulePartition:
    def test_the_expected_map_covers_every_cell_of_known_sources_by_kind(self) -> None:
        assert set(_EXPECTED_GATE_OF_CELL) == {
            (source, is_markdown) for source in defects.KNOWN_SOURCES for is_markdown in (False, True)
        }

    def test_the_rule_holds_exactly_the_expected_cells_and_names_no_unknown_source(self) -> None:
        assert analysis.GATING_RULE == _EXPECTED_GATE_OF_CELL
        assert {source for source, _is_markdown in analysis.GATING_RULE} <= defects.KNOWN_SOURCES
        analysis.validate_gating_rule()

    @pytest.mark.parametrize("is_markdown", [False, True])
    @pytest.mark.parametrize("source", sorted(defects.KNOWN_SOURCES))
    def test_each_cell_lands_in_exactly_the_expected_gate_or_the_secondary_stratum(
        self, source: str, is_markdown: bool,
    ) -> None:
        defect = _defect_in_cell(source, is_markdown)

        assert analysis.gate_of(defect) == _EXPECTED_GATE_OF_CELL[(source, is_markdown)]
        placed = analysis.gate_defect_ids([defect], [defect.id])
        assert [name for name, ids in placed.items() if defect.id in ids] == [_EXPECTED_GATE_OF_CELL[(source, is_markdown)]]

    def test_the_two_gate_sets_are_disjoint_over_a_record_set_holding_all_six_cells(self) -> None:
        cells = sorted(_EXPECTED_GATE_OF_CELL)
        confirmed = [_defect_in_cell(source, is_markdown) for source, is_markdown in cells]

        placed = analysis.gate_defect_ids(confirmed, [defect.id for defect in confirmed])

        assert not set(placed[analysis.GATE_CODE]) & set(placed[analysis.GATE_MARKDOWN])
        assert sorted(sum(placed.values(), [])) == sorted(defect.id for defect in confirmed)
        assert sorted(placed[analysis.GATE_CODE]) == ["review-round-code", "szz-code"]
        assert sorted(placed[analysis.GATE_MARKDOWN]) == ["pr-comment-md", "review-round-md"]
        assert sorted(placed[analysis.STRATUM_SECONDARY]) == ["pr-comment-code", "szz-md"]

    def test_a_source_the_rule_does_not_list_is_refused_as_unclassified(self, monkeypatch) -> None:
        rule_without_the_cell = {cell: gate for cell, gate in analysis.GATING_RULE.items() if cell != ("szz", True)}
        monkeypatch.setattr(analysis, "GATING_RULE", rule_without_the_cell)

        with pytest.raises(analysis.HarnessInvalidatedError, match="no cell of the gating rule"):
            analysis.gate_of(_defect_in_cell("szz", True))
        with pytest.raises(analysis.HarnessInvalidatedError, match="unclassified cells"):
            analysis.validate_gating_rule()

    def test_a_rule_naming_a_source_outside_known_sources_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(analysis, "GATING_RULE", {**analysis.GATING_RULE, ("pr-body", False): "code"})

        with pytest.raises(analysis.HarnessInvalidatedError, match="unknown sources: \\['pr-body'\\]"):
            analysis.validate_gating_rule()

    def test_the_record_lists_each_gates_cells_the_secondary_cells_and_the_cluster_key(self) -> None:
        record = analysis.gating_rule_record()

        assert record["cells_by_gate"] == {
            "code": [["review-round", False], ["szz", False]],
            "markdown": [["pr-comment", True], ["review-round", True]],
        }
        assert record["secondary_cells"] == [["pr-comment", False], ["szz", True]]
        assert record["cluster_key"] == ["base_commit", "head_commit"]
        json.dumps(record)

    def test_source_defect_ids_splits_by_source_and_keeps_every_known_source_present(self) -> None:
        confirmed = [_defect_in_cell("szz", False), _defect_in_cell("pr-comment", True)]

        split = analysis.source_defect_ids(confirmed, [defect.id for defect in confirmed])

        assert split == {"pr-comment": ["pr-comment-md"], "review-round": [], "szz": ["szz-code"]}


class TestFixtureClusterBootstrap:
    @staticmethod
    def _recall_counts_with_differences(differences_in_tenths: list[int]) -> dict[str, analysis.DefectRecallCounts]:
        """Defect i recalls (10 + difference) / 20 in the baseline arm and 0.5 in arm X."""
        return {
            f"d{i}": analysis.DefectRecallCounts(
                defect_id=f"d{i}", found_by_arm={ARM_BASELINE: 5 + difference, ARM_X: 5},
                completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            )
            for i, difference in enumerate(differences_in_tenths)
        }

    def test_singleton_clusters_in_defect_order_draw_exactly_what_a_per_defect_bootstrap_draws(self) -> None:
        import random

        ids = [f"d{i}" for i in range(7)]
        rng = random.Random(7)
        per_defect_resamples = [[ids[rng.randrange(len(ids))] for _ in range(len(ids))] for _ in range(300)]
        clustered_resamples: list[list[str]] = []

        def recording_statistic(resample_ids) -> float:
            clustered_resamples.append(list(resample_ids))
            return float(len(clustered_resamples))

        analysis.bootstrap_interval(
            ids, recording_statistic, cluster_by_defect=_own_fixture_each(ids), resamples=300, seed=7,
        )

        assert clustered_resamples == per_defect_resamples

    def test_a_drawn_fixture_brings_all_its_member_defects_and_only_them(self) -> None:
        clusters = {"a1": ("base-a", "head-a"), "a2": ("base-a", "head-a"), "b": ("base-b", "head-b")}
        seen: list[list[str]] = []

        def recording_statistic(resample_ids) -> float:
            seen.append(list(resample_ids))
            return 0.0

        analysis.bootstrap_interval(["a1", "a2", "b"], recording_statistic, cluster_by_defect=clusters, resamples=50, seed=3)

        assert len(seen) == 50
        assert all(resample.count("a1") == resample.count("a2") for resample in seen)
        assert any(resample.count("a1") != 1 for resample in seen)

    def test_five_defects_on_one_fixture_give_a_null_interval(self) -> None:
        counts = self._recall_counts_with_differences([1, 2, 3, 4, 5])
        ids = list(counts)
        one_fixture = {defect_id: ("base", "head") for defect_id in ids}

        interval = analysis.bootstrap_interval(
            ids, lambda resample_ids: analysis.arm_recall(counts, resample_ids, ARM_BASELINE),
            cluster_by_defect=one_fixture, resamples=50, seed=1,
        )

        assert interval is None

    def test_one_fixture_of_identical_defects_beside_singletons_widens_the_interval(self) -> None:
        """Four defects share one fixture and one outcome (+0.8 in the baseline arm's favor); four
        singletons show none. Treating each defect as its own fixture understates the variance."""
        counts = self._recall_counts_with_differences([4, 4, 4, 4, 0, 0, 0, 0])
        ids = list(counts)
        shared = {f"d{i}": ("base-shared", "head-shared") for i in range(4)}
        clustered_keys = {**shared, **{f"d{i}": (f"base-{i}", f"head-{i}") for i in range(4, 8)}}

        def statistic(resample_ids) -> float:
            return analysis.arm_recall(counts, resample_ids, ARM_BASELINE) - analysis.arm_recall(counts, resample_ids, ARM_X)

        clustered = analysis.bootstrap_interval(ids, statistic, cluster_by_defect=clustered_keys, resamples=2000, seed=5)
        per_defect = analysis.bootstrap_interval(ids, statistic, cluster_by_defect=_own_fixture_each(ids), resamples=2000, seed=5)

        assert clustered[1] - clustered[0] > per_defect[1] - per_defect[0]

    def test_fixture_count_and_largest_cluster_size_count_distinct_fixtures_not_defects(self) -> None:
        clusters = {"a1": ("base-a", "head-a"), "a2": ("base-a", "head-a"), "a3": ("base-a", "head-a"), "b": ("base-b", "head-b")}

        assert analysis.fixture_count(["a1", "a2", "a3", "b"], clusters) == 2
        assert analysis.largest_cluster_size(["a1", "a2", "a3", "b"], clusters) == 3
        assert (analysis.fixture_count([], clusters), analysis.largest_cluster_size([], clusters)) == (0, 0)

    def test_the_same_head_under_a_different_base_is_another_fixture(self) -> None:
        confirmed = [
            defects.ConfirmedDefect(
                id=defect_id, source="szz", lens="staff-backend-engineer", base_commit=base_commit,
                head_commit="b" * 40, fix_commit="c" * 40, fix_date="2024-01-01", description="test defect",
                path="app.py", file_is_markdown=False,
            )
            for defect_id, base_commit in (("d1", "a" * 40), ("d2", "e" * 40), ("d3", "a" * 40))
        ]

        clusters = analysis.fixture_clusters(confirmed)

        assert analysis.fixture_count(["d1", "d2", "d3"], clusters) == 2

    def test_the_short_gate_test_counts_fixtures_and_an_empty_set_is_below_n_min(self) -> None:
        clusters = {"a1": ("base-a", "head-a"), "a2": ("base-a", "head-a"), "b": ("base-b", "head-b")}

        assert analysis.effective_n_meets_n_min(["a1", "a2", "b"], clusters, n_min_value=2)
        assert not analysis.effective_n_meets_n_min(["a1", "a2", "b"], clusters, n_min_value=3)
        assert not analysis.effective_n_meets_n_min(["a1", "a2"], clusters, n_min_value=2)
        assert not analysis.effective_n_meets_n_min([], clusters, n_min_value=1)


class TestUndefinedFiguresAreNull:
    """Sizes 0, 1, and 2 through each function whose figure is undefined on a small set. Counts are
    of fixtures wherever an interval is concerned."""

    @staticmethod
    def _recall_counts(*detection_rates_in_tenths: int) -> dict[str, analysis.DefectRecallCounts]:
        return {
            f"d{i}": analysis.DefectRecallCounts(
                defect_id=f"d{i}", found_by_arm={ARM_BASELINE: found, ARM_X: found // 2},
                completed_by_arm={ARM_BASELINE: 10, ARM_X: 10},
            )
            for i, found in enumerate(detection_rates_in_tenths)
        }

    def test_arm_recall_is_null_for_no_defect_and_a_real_zero_for_an_arm_that_finds_nothing(self) -> None:
        counts = self._recall_counts(0)
        assert analysis.arm_recall(counts, [], ARM_BASELINE) is None
        assert analysis.arm_recall(counts, ["d0"], ARM_BASELINE) == 0.0

    @pytest.mark.parametrize(("fixtures", "expect_interval"), [(0, False), (1, False), (2, True)])
    def test_bootstrap_interval_is_null_below_two_fixtures_and_never_raises(
        self, fixtures: int, expect_interval: bool,
    ) -> None:
        counts = self._recall_counts(*([6, 8][:fixtures]))
        ids = list(counts)

        interval = analysis.bootstrap_interval(
            ids, lambda resample_ids: analysis.arm_recall(counts, resample_ids, ARM_BASELINE),
            cluster_by_defect=_own_fixture_each(ids), resamples=50, seed=1,
        )

        assert (interval is not None) is expect_interval

    @pytest.mark.parametrize(("fixtures", "expect_defined"), [(0, False), (1, False), (2, True)])
    def test_the_three_verdicts_are_null_below_two_fixtures(self, fixtures: int, expect_defined: bool) -> None:
        recall_counts = self._recall_counts(*([6, 8][:fixtures]))
        precision_counts = {
            defect_id: analysis.DefectPrecisionCounts(defect_id, {ARM_BASELINE: 5, ARM_X: 5}, {ARM_BASELINE: 10, ARM_X: 10})
            for defect_id in recall_counts
        }
        ids, clusters = list(recall_counts), _own_fixture_each(recall_counts)

        sensitivity = analysis.baseline_sensitivity_verdict(
            recall_counts, ids, ARM_BASELINE, ARM_X, cluster_by_defect=clusters, resamples=50, seed=1,
        )
        recall = analysis.recall_noninferiority_verdict(
            recall_counts, ids, ARM_BASELINE, ARM_X, cluster_by_defect=clusters, resamples=50, seed=1,
        )
        precision = analysis.precision_noninferiority_verdict(
            precision_counts, ids, ARM_BASELINE, ARM_X, cluster_by_defect=clusters, resamples=50, seed=1,
        )

        assert (sensitivity[0] is not None) is expect_defined
        assert (recall[0] is not None) is expect_defined
        assert (precision[0] is not None) is expect_defined
        if not expect_defined:
            assert sensitivity == (None, None)
            assert recall == (None, None)
            assert precision == (None, None, 0)

    @pytest.mark.parametrize(("defect_count", "expected_halves"), [
        (0, {"earlier_half": None, "later_half": None}),
        (1, {"earlier_half": None, "later_half": pytest.approx(0.6)}),
        (2, {"earlier_half": pytest.approx(0.6), "later_half": pytest.approx(0.8)}),
    ])
    def test_recall_by_fix_date_half_leaves_an_empty_half_null(self, defect_count: int, expected_halves: dict) -> None:
        counts = self._recall_counts(*([6, 8][:defect_count]))
        fix_dates = {"d0": "2024-01-01", "d1": "2024-06-01"}

        result = analysis.recall_by_fix_date_half(counts, list(counts), fix_dates, ARM_BASELINE)

        assert result == expected_halves

    def test_observed_sigma_d_counts_fixtures_so_two_fixtures_define_it_and_one_does_not(self) -> None:
        counts = self._recall_counts(6, 8, 4)
        ids = list(counts)
        two_fixtures = {"d0": ("base-a", "head-a"), "d1": ("base-a", "head-a"), "d2": ("base-b", "head-b")}
        one_fixture = {defect_id: ("base-a", "head-a") for defect_id in ids}

        assert analysis.observed_sigma_d(counts, ids, ARM_BASELINE, ARM_X, cluster_by_defect=two_fixtures) is not None
        assert analysis.observed_sigma_d(counts, ids, ARM_BASELINE, ARM_X, cluster_by_defect=one_fixture) is None

    def test_pooled_precision_is_null_over_a_zero_denominator_and_a_real_zero_over_no_valid_finding(self) -> None:
        counts = {
            "d1": analysis.DefectPrecisionCounts("d1", {ARM_BASELINE: 0, ARM_X: 0}, {ARM_BASELINE: 3, ARM_X: 0}),
        }
        assert analysis.pooled_precision(counts, [], ARM_BASELINE) is None
        assert analysis.pooled_precision(counts, ["d1"], ARM_X) is None
        assert analysis.pooled_precision(counts, ["d1"], ARM_BASELINE) == 0.0

    def test_a_resample_with_a_zero_precision_denominator_is_dropped_and_counted(self) -> None:
        """Arm X has no adjudicated finding on d2, so a resample that draws only d2 has no
        precision difference."""
        import random

        counts = {
            "d1": analysis.DefectPrecisionCounts("d1", {ARM_BASELINE: 5, ARM_X: 5}, {ARM_BASELINE: 10, ARM_X: 10}),
            "d2": analysis.DefectPrecisionCounts("d2", {ARM_BASELINE: 5, ARM_X: 0}, {ARM_BASELINE: 10, ARM_X: 0}),
        }
        resamples, seed = 400, 11
        rng = random.Random(seed)
        expected_dropped = sum(
            1 for _ in range(resamples) if {rng.randrange(2), rng.randrange(2)} == {1}
        )

        verdict, interval, dropped = analysis.precision_noninferiority_verdict(
            counts, ["d1", "d2"], ARM_BASELINE, ARM_X, cluster_by_defect=_own_fixture_each(counts),
            resamples=resamples, seed=seed,
        )

        assert 0 < dropped < resamples
        assert dropped == expected_dropped
        assert interval is not None
        assert verdict in (_PASS, _FAIL)

    def test_a_gate_where_every_resample_drops_has_a_null_interval_and_verdict(self) -> None:
        counts = {
            defect_id: analysis.DefectPrecisionCounts(
                defect_id, {ARM_BASELINE: 5, ARM_X: 0}, {ARM_BASELINE: 10, ARM_X: 0},
            )
            for defect_id in ("d1", "d2")
        }

        result = analysis.precision_noninferiority_verdict(
            counts, ["d1", "d2"], ARM_BASELINE, ARM_X, cluster_by_defect=_own_fixture_each(counts),
            resamples=40, seed=1,
        )

        assert result == (None, None, 40)


class TestBaselineReportFreezeIdentity:
    def test_records_the_closure_hash_every_frozen_digest_k_and_the_campaign_environment(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen_fields = analysis.compute_frozen_fields(defects_path, arms_root)
        records = [_run_record("d1", ARM_BASELINE, "r1", cli_version="2.1.0", ambient_config_commit="cafe")]

        identity = analysis.baseline_report_freeze_identity(defects_path, arms_root, records, k=10)

        assert identity == {
            "harness_closure_hash": frozen_fields["harness_closure_hash"],
            **{field: frozen_fields[field] for field in analysis._FROZEN_DIGEST_FIELDS},
            "k": 10,
            "environment": {"cli_version": "2.1.0", "ambient_config_commit": "cafe"},
        }

    def test_an_empty_record_set_records_no_environment(self, tmp_path: Path, monkeypatch) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)

        identity = analysis.baseline_report_freeze_identity(defects_path, arms_root, [], k=10)

        assert identity["environment"] is None


class TestLoadBaselineGateVerdicts:
    @pytest.fixture
    def frozen_and_identity(self, tmp_path: Path, monkeypatch) -> tuple[dict, dict]:
        """A conditions.json record and the identity of the baseline campaign frozen as it."""
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        frozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))
        identity = analysis.baseline_report_freeze_identity(
            defects_path, arms_root, [_run_record("d1", ARM_BASELINE, "r1")], k=10,
        )
        return frozen, identity

    @staticmethod
    def _report(identity: dict, **gate_overrides) -> dict:
        gates = {
            gate: {"baseline_sensitivity": {"verdict": _SENSITIVE}, "fixture_count": 130}
            for gate in analysis.GATES
        }
        for gate, override in gate_overrides.items():
            gates[gate] = override
        return {"gates": gates, "freeze_identity": identity}

    def _load(self, tmp_path: Path, frozen_and_identity, report=None, **gate_overrides) -> dict:
        frozen, identity = frozen_and_identity
        report = self._report(identity, **gate_overrides) if report is None else report
        path = tmp_path / "baseline.json"
        path.write_text(report if isinstance(report, str) else json.dumps(report))
        return analysis.load_baseline_gate_verdicts(path, frozen)

    def test_reads_each_gates_verdict(self, tmp_path: Path, frozen_and_identity) -> None:
        markdown = {"baseline_sensitivity": {"verdict": _NOT_SENSITIVE}, "fixture_count": 130}

        assert self._load(tmp_path, frozen_and_identity, markdown=markdown) == {
            "code": _SENSITIVE, "markdown": _NOT_SENSITIVE,
        }

    @pytest.mark.parametrize("fixtures", [0, 1])
    def test_a_null_verdict_beside_fewer_than_two_fixtures_is_valid(
        self, tmp_path: Path, frozen_and_identity, fixtures: int,
    ) -> None:
        markdown = {"baseline_sensitivity": {"verdict": None}, "fixture_count": fixtures}

        assert self._load(tmp_path, frozen_and_identity, markdown=markdown) == {"code": _SENSITIVE, "markdown": None}

    @pytest.mark.parametrize("fixtures", [2, 130, None, "2"])
    def test_a_null_verdict_beside_two_or_more_fixtures_or_an_unreadable_count_is_refused(
        self, tmp_path: Path, frozen_and_identity, fixtures,
    ) -> None:
        code = {"baseline_sensitivity": {"verdict": None}, "fixture_count": fixtures}

        with pytest.raises(analysis.HarnessInvalidatedError, match="null verdict"):
            self._load(tmp_path, frozen_and_identity, code=code)

    def test_a_missing_file_is_refused(self, tmp_path: Path, frozen_and_identity) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="unreadable"):
            analysis.load_baseline_gate_verdicts(tmp_path / "absent.json", frozen_and_identity[0])

    @pytest.mark.parametrize("malformed", ["not json", "[]", "{}", '{"gates": {}}'])
    def test_unparseable_or_gateless_content_is_refused(
        self, tmp_path: Path, frozen_and_identity, malformed: str,
    ) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="unreadable, or is missing or malformed"):
            self._load(tmp_path, frozen_and_identity, report=malformed)

    def test_a_missing_verdict_key_is_refused(self, tmp_path: Path, frozen_and_identity) -> None:
        code = {"baseline_sensitivity": {}, "fixture_count": 130}

        with pytest.raises(analysis.HarnessInvalidatedError, match="verdict"):
            self._load(tmp_path, frozen_and_identity, code=code)

    def test_a_verdict_that_is_neither_sensitive_nor_not_sensitive_is_refused(
        self, tmp_path: Path, frozen_and_identity,
    ) -> None:
        code = {"baseline_sensitivity": {"verdict": "mostly-sensitive"}, "fixture_count": 130}

        with pytest.raises(analysis.HarnessInvalidatedError, match="not a known verdict"):
            self._load(tmp_path, frozen_and_identity, code=code)

    def test_a_report_computed_under_the_frozen_identity_is_read(self, tmp_path: Path, frozen_and_identity) -> None:
        assert self._load(tmp_path, frozen_and_identity) == {"code": _SENSITIVE, "markdown": _SENSITIVE}

    @pytest.mark.parametrize(
        ("mutate", "expected_field"),
        [
            pytest.param(lambda identity: identity.update(harness_closure_hash="stale"), "harness_closure_hash",
                         id="closure-hash"),
            pytest.param(lambda identity: identity.update(defects_json_hash="stale"), "defects_json_hash",
                         id="defects-json-hash"),
            pytest.param(lambda identity: identity.update(k=15), "k", id="k"),
            pytest.param(lambda identity: identity["arm_dir_hashes"].update({ARM_X: "stale"}),
                         f"arm_dir_hashes[{ARM_X}]", id="an-arm-dir-hash"),
            pytest.param(lambda identity: identity["judge_agent_hashes"].update({"bench-judge-recall": "stale"}),
                         "judge_agent_hashes[bench-judge-recall]", id="a-judge-hash"),
            pytest.param(lambda identity: identity["prompt_template_hashes"].update({"review_prompt": "stale"}),
                         "prompt_template_hashes[review_prompt]", id="a-prompt-template-hash"),
            pytest.param(lambda identity: identity.update(defect_ids=["d1"]), "defect_ids", id="defect-ids"),
            pytest.param(lambda identity: identity["environment"].update(cli_version="1.0.0"),
                         "environment[cli_version]", id="cli-version"),
            pytest.param(lambda identity: identity["environment"].update(ambient_config_commit="0ld"),
                         "environment[ambient_config_commit]", id="ambient-config-commit"),
            pytest.param(lambda identity: identity.update(environment=None), "environment", id="no-environment"),
        ],
    )
    def test_a_report_computed_under_a_different_freeze_is_refused_naming_the_field_and_the_regeneration(
        self, tmp_path: Path, frozen_and_identity, mutate, expected_field: str,
    ) -> None:
        _frozen_record, identity = frozen_and_identity
        mutate(identity)

        with pytest.raises(analysis.HarnessInvalidatedError, match="invalidated -- rerun all arms") as excinfo:
            self._load(tmp_path, frozen_and_identity)

        assert expected_field in str(excinfo.value)
        assert "rerun all arms under the new freeze, then regenerate the baseline report" in str(excinfo.value)

    def test_a_refreeze_that_changes_only_the_closure_refuses_the_report_of_the_earlier_freeze(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        defects_path, arms_root = _frozen_workspace(tmp_path, monkeypatch)
        earlier_identity = analysis.baseline_report_freeze_identity(
            defects_path, arms_root, [_run_record("d1", ARM_BASELINE, "r1")], k=10,
        )
        closure = analysis.compute_harness_closure()
        monkeypatch.setattr(analysis, "compute_harness_closure", lambda: {**closure, "edited-harness-file": "0" * 64})
        refrozen = _frozen(analysis.compute_frozen_fields(defects_path, arms_root))

        with pytest.raises(analysis.HarnessInvalidatedError, match=r"differing: \['harness_closure_hash'\]"):
            self._load(tmp_path, (refrozen, earlier_identity))

    @pytest.mark.parametrize("missing_key", ["harness_closure_hash", "k", "environment", "arm_dir_hashes"])
    def test_an_identity_missing_a_key_is_refused_as_malformed(
        self, tmp_path: Path, frozen_and_identity, missing_key: str,
    ) -> None:
        del frozen_and_identity[1][missing_key]

        with pytest.raises(analysis.HarnessInvalidatedError, match="missing or malformed"):
            self._load(tmp_path, frozen_and_identity)

    @pytest.mark.parametrize("identity", [None, "digest", ["k"]])
    def test_a_report_with_a_missing_or_non_object_identity_is_refused_as_malformed(
        self, tmp_path: Path, frozen_and_identity, identity,
    ) -> None:
        with pytest.raises(analysis.HarnessInvalidatedError, match="missing or malformed"):
            self._load(tmp_path, frozen_and_identity, report=self._report(identity))
