"""Tests for evals/run_review_bench.py's own CLI-level logic:
`_hash_directory`, `cmd_freeze`'s written manifest fields, and
`_build_spot_check_samples`'s reviewer/judge join. Offline throughout --
`cmd_freeze`'s own git calls run against a throwaway tmp-path repo, never
this repo's own history. No test launches `claude`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import run_review_bench
from review_bench import arms as arms_mod
from review_bench import defects, runner
from review_bench.adjudicate import JUDGE_ARM_PRECISION, JUDGE_ARM_RECALL
from review_bench.defects import ConfirmedDefect


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _write(repo: Path, rel_path: str, content: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _run_record(
    defect_id: str, arm: str, opaque_run_id: str, findings_text: str, *, status: str = "ok",
    out_of_session_paths: tuple[str, ...] = (), over_read_cap: bool = False,
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=0, opaque_run_id=opaque_run_id,
        status=status, missing_reason=None, observed_model="claude-sonnet-5", observed_tools=("Read",),
        out_of_session_paths=out_of_session_paths, findings_text=findings_text, wall_clock_s=1.0, read_calls=1,
        read_tokens_est=10, partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=0,
        over_read_cap=over_read_cap, dispatch_prompt_verbatim=True, cli_version="2.0.0",
        ambient_config_commit="deadbeef",
    )


class TestHashDirectory:
    def test_deterministic_for_the_same_contents(self, tmp_path: Path) -> None:
        directory = tmp_path / "dir"
        directory.mkdir()
        (directory / "a.txt").write_text("hello")
        (directory / "b.txt").write_text("world")
        assert run_review_bench._hash_directory(directory) == run_review_bench._hash_directory(directory)

    def test_sensitive_to_a_content_change(self, tmp_path: Path) -> None:
        directory = tmp_path / "dir"
        directory.mkdir()
        (directory / "a.txt").write_text("hello")
        (directory / "b.txt").write_text("world")
        before = run_review_bench._hash_directory(directory)
        (directory / "b.txt").write_text("world!")
        after = run_review_bench._hash_directory(directory)
        assert before != after


# Every field evals/README.md's "Frozen conditions and invalidation" section
# requires cmd_freeze to record in conditions.json.
_EXPECTED_FREEZE_CONDITIONS_FIELDS: frozenset[str] = frozenset({
    "reviewer_model_id", "judge_model_id", "k", "delta", "alpha_one_sided", "n_min",
    "planning_variance", "bootstrap_resamples", "bootstrap_seed", "campaign_seed",
    "kappa_floor", "missing_run_retry_rule", "later_arm_gate", "defect_ids",
    "harness_closure_hash", "harness_closure", "arm_dir_hashes", "judge_agent_hashes",
    "defects_json_hash", "prompt_template_hashes", "environment", "main_commit_sha",
})


class TestCmdFreezeManifestFields:
    def test_writes_every_plan_required_field_including_arm_dir_judge_agent_defects_json_and_prompt_template_hashes(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        head_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        defects_path = tmp_path / "defects.json"
        confirmed = [
            ConfirmedDefect(
                id="d1", source="szz", lens="staff-backend-engineer", base_commit=head_sha, head_commit=head_sha,
                fix_commit=fix_sha, fix_date="2024-01-01", description="x changed its stale initial value.",
            ),
        ]
        defects.save_confirmed_defects(defects_path, confirmed)

        # An empty candidates shortlist is enough to satisfy the
        # local-excerpts-present precondition without contributing any
        # excerpt text for the provenance check to compare against.
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        (local_dir / "szz_candidates.json").write_text("[]")

        arms_root = tmp_path / "arms"
        for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT):
            arm_dir = arms_root / arm
            arm_dir.mkdir(parents=True)
            (arm_dir / "bench-staff-backend-engineer.md").write_text(f"{arm} agent body\n")

        from review_bench import analysis

        manifest_hash = analysis.closure_manifest_hash(analysis.compute_harness_closure())
        out_path = tmp_path / "conditions.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), local_dir=str(local_dir), arms_root=str(arms_root), k=10,
            campaign_seed=7, last_smoke_manifest_hash=manifest_hash, smoke_full_k=10, main_commit_sha="",
            out=str(out_path),
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 0
        conditions = json.loads(out_path.read_text())

        # Completeness, not just the hand-picked subset asserted below: a
        # field the plan requires but cmd_freeze drops (or an extra field it
        # adds) fails here even if that field's own value is never checked.
        assert set(conditions.keys()) == _EXPECTED_FREEZE_CONDITIONS_FIELDS
        assert conditions["campaign_seed"] == 7

        from review_bench import adjudicate

        expected_arm_dir_hashes = {
            arm: run_review_bench._hash_directory(arms_root / arm)
            for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT)
        }
        assert conditions["arm_dir_hashes"] == expected_arm_dir_hashes
        # The two arms' own snapshot files differ in content (different arm
        # names above), so a copy-paste bug hashing one arm's directory for
        # both would collide here.
        assert expected_arm_dir_hashes[arms_mod.ARM_CURRENT_RULE] != expected_arm_dir_hashes[arms_mod.ARM_FUNCTION_CONTEXT]

        assert conditions["judge_agent_hashes"] == {
            "bench-judge-recall": hashlib.sha256(adjudicate.RECALL_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
            "bench-judge-precision": hashlib.sha256(adjudicate.PRECISION_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
        }
        assert conditions["defects_json_hash"] == hashlib.sha256(defects_path.read_bytes()).hexdigest()
        assert conditions["prompt_template_hashes"] == {
            "review_prompt": hashlib.sha256(runner.REVIEW_PROMPT_TEMPLATE.encode()).hexdigest(),
            "dispatch_prompt": hashlib.sha256(runner.DISPATCH_PROMPT_TEMPLATE.encode()).hexdigest(),
            "judge_inner_prompt": hashlib.sha256(adjudicate.JUDGE_INNER_PROMPT_TEMPLATE.encode()).hexdigest(),
        }


class TestBuildSpotCheckSamples:
    def test_joins_reviewer_and_judge_records_by_defect_and_filters_missing_status(self, tmp_path: Path) -> None:
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", "current-rule", "run-a", "Leaks a connection on error."),
            _run_record("d1", "function-context", "run-b", "Nothing to report."),
            # A missing reviewer run never reaches the judge's own input,
            # so it must never surface as a spot-check candidate either.
            _run_record("d1", "current-rule", "run-c", "A missing run.", status="missing"),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        args = argparse.Namespace(
            reviewer_records_path=str(reviewer_records_path), judge_records_path=str(judge_records_path), seed=0,
        )
        recall_sample, precision_sample = run_review_bench._build_spot_check_samples(args)

        assert {c.item_id for c in recall_sample} == {"d1:run-a", "d1:run-b"}
        assert {c.item_id for c in precision_sample} == {"d1:run-a:0"}

    def test_excludes_judge_record_for_a_defect_with_no_reviewer_record_at_all(self, tmp_path: Path) -> None:
        """A crash between writing a reviewer campaign's own records and a
        later judge campaign's can leave a judge record naming a defect_id
        no reviewer record was ever written for. The join must drop it
        rather than raise or fabricate a candidate with no findings text."""
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", "current-rule", "run-a", "Leaks a connection on error."),
            _run_record("d1", "function-context", "run-b", "Nothing to report."),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
            # "d2" has a judge record but no reviewer record at all.
            _run_record("d2", JUDGE_ARM_RECALL, "j-recall-orphan", "run-z: FOUND -- \"orphaned\""),
            _run_record("d2", JUDGE_ARM_PRECISION, "j-precision-orphan", '### Run run-z\n1. VALID -- "orphaned"\n'),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        args = argparse.Namespace(
            reviewer_records_path=str(reviewer_records_path), judge_records_path=str(judge_records_path), seed=0,
        )
        recall_sample, precision_sample = run_review_bench._build_spot_check_samples(args)

        assert {c.item_id for c in recall_sample} == {"d1:run-a", "d1:run-b"}
        assert {c.item_id for c in precision_sample} == {"d1:run-a:0"}
        assert not any(c.item_id.startswith("d2:") for c in recall_sample + precision_sample)


def _confirmed_single_defect(defect_id: str = "d1") -> ConfirmedDefect:
    return ConfirmedDefect(
        id=defect_id, source="szz", lens="staff-backend-engineer", base_commit="a" * 40, head_commit="b" * 40,
        fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
    )


def _run_or_smoke_args(tmp_path: Path, defects_path: Path, *, subcommand: str) -> argparse.Namespace:
    return argparse.Namespace(
        subcommand=subcommand, defects_path=str(defects_path), defect_id=[], arms_root=str(tmp_path / "arms"),
        run_store_dir=str(tmp_path / "run-store"), records_dir=str(tmp_path / "runs"), campaign_id="c1",
        k=1, seed=0, workers=1, inject_fault=None,
    )


class TestRunAndSmokeOutOfSessionReporting:
    """Mirrors TestCmdJudgeOutOfSessionReporting below: `run` and `smoke`
    share `_run_or_smoke`, the highest-volume dispatcher of real `claude -p`
    sessions, so its out-of-session reporting is exercised for both public
    entry points."""

    def _stub_run_campaign(self, monkeypatch) -> runner.RunRecord:
        record = _run_record(
            "d1", arms_mod.ARM_CURRENT_RULE, "run-a", "finding", out_of_session_paths=("secret/path.txt",),
        )
        block_result = runner.BlockResult(
            records=(record,), representative_session_id_by_arm={arms_mod.ARM_CURRENT_RULE: "sess-1"},
        )

        def fake_run_campaign(defect_ids, *, campaign_id, **kwargs):
            return runner.CampaignResult(campaign_id=campaign_id, block_results={"d1": block_result})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        return record

    def test_run_prints_each_out_of_session_path_and_the_per_arm_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        record = self._stub_run_campaign(monkeypatch)

        exit_code = run_review_bench.cmd_run(_run_or_smoke_args(tmp_path, defects_path, subcommand="run"))

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"run: out-of-session read in {arms_mod.ARM_CURRENT_RULE} run run-a: secret/path.txt" in stderr

        from review_bench import analysis

        expected_counts = analysis.out_of_session_counts_by_arm([record])
        assert f"run: out-of-session read counts per arm = {expected_counts}" in stderr

    def test_smoke_prints_each_out_of_session_path_and_the_per_arm_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        record = self._stub_run_campaign(monkeypatch)

        exit_code = run_review_bench.cmd_smoke(_run_or_smoke_args(tmp_path, defects_path, subcommand="smoke"))

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"smoke: out-of-session read in {arms_mod.ARM_CURRENT_RULE} run run-a: secret/path.txt" in stderr

        from review_bench import analysis

        expected_counts = analysis.out_of_session_counts_by_arm([record])
        assert f"smoke: out-of-session read counts per arm = {expected_counts}" in stderr


class TestRunAndSmokeEnvironmentDriftExceeded:
    """Mirrors the `analysis.HarnessInvalidatedError` catch: `run` and `smoke`
    share `_run_or_smoke`'s `runner.EnvironmentDriftExceededError` catch, so
    both public entry points must surface it as exit code 2, not a
    traceback."""

    def _stub_run_campaign_raising_drift(self, monkeypatch, message: str) -> None:
        def fake_run_campaign(defect_ids, *, campaign_id, **kwargs):
            raise runner.EnvironmentDriftExceededError(message)

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)

    def test_run_returns_exit_code_2_and_prints_the_error_to_stderr(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        self._stub_run_campaign_raising_drift(monkeypatch, "d1 still drifted after retries")

        exit_code = run_review_bench.cmd_run(_run_or_smoke_args(tmp_path, defects_path, subcommand="run"))

        assert exit_code == 2
        assert "d1 still drifted after retries" in capsys.readouterr().err

    def test_smoke_returns_exit_code_2_and_prints_the_error_to_stderr(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        self._stub_run_campaign_raising_drift(monkeypatch, "d1 still drifted after retries")

        exit_code = run_review_bench.cmd_smoke(_run_or_smoke_args(tmp_path, defects_path, subcommand="smoke"))

        assert exit_code == 2
        assert "d1 still drifted after retries" in capsys.readouterr().err


class TestCmdJudgeCampaignIdEchoedBeforeLock:
    def test_prints_the_resolved_campaign_id_and_store_dir_before_acquiring_the_lock(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """Regression guard: the resolved --campaign-id must be printed
        before the lock is acquired, so a crash before completion still
        leaves the operator a store name to resume."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        def raise_before_any_work(self, *a, **k):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(runner.RunStore, "acquire_lock", raise_before_any_work)

        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            judge_run_store_dir=str(tmp_path / "judge-run-store"), judge_records_dir=str(tmp_path / "judge-runs"),
            campaign_id=None, seed=0,
        )

        with pytest.raises(RuntimeError):
            run_review_bench.cmd_judge(args)

        stderr = capsys.readouterr().err
        assert "judge: campaign judge-" in stderr
        assert "run store at" in stderr


class TestCmdJudgeOutOfSessionReporting:
    def test_prints_each_out_of_session_path_and_the_per_judge_kind_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        captured_judge_records: list[runner.RunRecord] = []

        def fake_run_defect_judges(defect, records, **kwargs):
            recall = _run_record(
                defect.id, JUDGE_ARM_RECALL, "j-recall", "r1: NOT_FOUND", out_of_session_paths=("secret/path.txt",),
            )
            precision = _run_record(defect.id, JUDGE_ARM_PRECISION, "j-precision", "### Run r1\n")
            captured_judge_records.extend((recall, precision))
            return recall, precision

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            judge_run_store_dir=str(tmp_path / "judge-run-store"), judge_records_dir=str(tmp_path / "judge-runs"),
            campaign_id="c1", seed=0,
        )

        exit_code = run_review_bench.cmd_judge(args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"judge: out-of-session read in {JUDGE_ARM_RECALL} run j-recall: secret/path.txt" in stderr

        from review_bench import analysis

        expected_counts = analysis.out_of_session_counts_by_arm(captured_judge_records)
        assert f"out-of-session read counts per judge kind = {expected_counts}" in stderr


class TestCmdAnalyzeOutOfSessionReport:
    def test_written_report_carries_a_populated_per_judge_kind_count(
        self, tmp_path: Path, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "Leaks a connection on error."),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "Nothing to report."),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
                out_of_session_paths=("secret/path.txt",),
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        out_path = tmp_path / "report.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None, baseline_conditions_path=None,
            out=str(out_path),
        )

        exit_code = run_review_bench.cmd_analyze(args)

        assert exit_code == 0
        report_text = out_path.read_text()
        report = json.loads(report_text)
        assert report["out_of_session_read_counts_per_judge_kind"] == {JUDGE_ARM_RECALL: 1, JUDGE_ARM_PRECISION: 0}
        assert "secret/path.txt" not in report_text

        # The raw path is terminal-only -- the committed --out report above
        # carries only the count.
        stderr = capsys.readouterr().err
        assert f"analyze: out-of-session read in {JUDGE_ARM_RECALL} run j-recall: secret/path.txt" in stderr


class TestCmdAnalyzeMalformedBaselineConditions:
    """Mirrors TestRunAndSmokeEnvironmentDriftExceeded's contract: a
    designed invalidation path must surface as exit code 2, not a raw
    traceback, for a missing file and for each way the file's content can
    fail to parse into the expected shape."""

    def _args(self, tmp_path: Path, defects_path: Path, baseline_conditions_path: Path) -> argparse.Namespace:
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "")])
        judge_records_path = tmp_path / "judge.jsonl"
        runner.append_run_records(judge_records_path, [])
        return argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None,
            baseline_conditions_path=str(baseline_conditions_path), out=str(tmp_path / "report.json"),
        )

    def test_invalid_json_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text("not json")

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_missing_environment_key_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text(json.dumps({"harness_closure": {}}))

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_non_dict_top_level_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        """A top-level JSON array parses without error, so this is the
        TypeError branch, not the KeyError branch the other two cases hit."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text(json.dumps(["not", "a", "dict"]))

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_nonexistent_path_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        """A typo'd CLI arg hits this branch: read_text() raises
        FileNotFoundError, an OSError subclass, before json.loads ever runs."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "does-not-exist.json"

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr


class TestCmdAnalyzeReportCompleteness:
    def test_written_report_carries_every_never_gating_secondary_column(self, tmp_path: Path) -> None:
        """Regression guard: a per-function unit test on analysis.py's own
        secondary-column functions cannot catch cmd_analyze never calling
        one of them. This asserts the wiring -- every column is present in
        the --out report -- not each column's own math, which
        test_review_bench_analysis.py already covers.

        d1 and d2 carry opposite detection patterns (d1 found only in
        current-rule, d2 found only in function-context) and only d2 is
        over_read_cap -- so the over-cap-restricted stratum's recall diff
        (+1.0, d2 alone) diverges from what a broken over_read_cap
        extraction would silently produce instead (0.0, arm_recall's own
        empty-defect-list default): the specific-value assertion below
        can't pass by accident the way a bare key-presence check can."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(
            defects_path, [_confirmed_single_defect("d1"), _confirmed_single_defect("d2")],
        )

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "Leaks a connection on error."),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "Nothing to report."),
            _run_record("d2", arms_mod.ARM_CURRENT_RULE, "run-c", "Nothing to report.", over_read_cap=True),
            _run_record("d2", arms_mod.ARM_FUNCTION_CONTEXT, "run-d", "Leaks a null pointer.", over_read_cap=True),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall-1", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision-1",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
            _run_record(
                "d2", JUDGE_ARM_RECALL, "j-recall-2",
                'run-c: NOT_FOUND\nrun-d: FOUND -- "Leaks a null pointer"',
            ),
            _run_record("d2", JUDGE_ARM_PRECISION, "j-precision-2", "### Run run-c\n### Run run-d\n"),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        out_path = tmp_path / "report.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None, baseline_conditions_path=None,
            out=str(out_path),
        )

        exit_code = run_review_bench.cmd_analyze(args)

        assert exit_code == 0
        report = json.loads(out_path.read_text())
        # Every never-gating secondary column analysis.py exposes, except
        # split agreement (produced by `spot-check import`, not `analyze`).
        for column in (
            "read_tokens_per_arm", "partial_and_paged_counts_per_arm", "whole_file_read_adherence_per_arm",
            "missing_runs_by_reason_per_arm", "out_of_session_read_counts_per_arm",
            "out_of_session_read_counts_per_judge_kind", "recall_by_fix_date_half_per_arm",
            "recall_diff_over_read_cap_stratum", "observed_sigma_d",
        ):
            assert column in report, f"cmd_analyze's --out report is missing the {column!r} secondary column"
        # d2 is the sole over_read_cap defect and is found only in
        # function-context (ARM_FUNCTION_CONTEXT - ARM_CURRENT_RULE = 1 - 0).
        assert report["recall_diff_over_read_cap_stratum"] == 1.0


def _candidate(**overrides) -> defects.Candidate:
    kwargs = dict(
        id="c1", source="szz", lens="staff-backend-engineer", base_commit="a" * 40, head_commit="b" * 40,
        fix_commit="c" * 40, fix_date="2024-01-01", lines_exist_at_introducing_head=True,
        reviewer_could_have_caught_it=True, file_is_markdown=False,
    )
    kwargs.update(overrides)
    return defects.Candidate(**kwargs)


class TestCmdMineSzz:
    def test_writes_the_mined_candidates_and_passes_base_ref_through(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_szz

        candidate = _candidate(id="szz-1")
        captured_calls: list[tuple[Path, str]] = []

        def fake_mine(repo_root, *, base_ref):
            captured_calls.append((repo_root, base_ref))
            return [candidate]

        monkeypatch.setattr(mine_szz, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = argparse.Namespace(base_ref="origin/main", local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_szz(args)

        assert exit_code == 0
        assert captured_calls == [(tmp_path / "repo", "origin/main")]
        out_path = tmp_path / "local" / "szz_candidates.json"
        assert defects.load_candidates(out_path) == [candidate]

    def test_prints_the_written_candidate_count_and_path(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from review_bench import mine_szz

        monkeypatch.setattr(mine_szz, "mine", lambda repo_root, *, base_ref: [_candidate(), _candidate(id="c2")])

        out_path = tmp_path / "local" / "szz_candidates.json"
        args = argparse.Namespace(base_ref="origin/main", local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_szz(args)

        assert exit_code == 0
        assert f"mine-szz: wrote 2 candidate(s) to {out_path}" in capsys.readouterr().err


class TestCmdMineRounds:
    def test_writes_the_mined_candidates(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_review_rounds

        candidate = _candidate(id="round-1", source="review-round")
        captured_calls: list[Path] = []

        def fake_mine(repo_root):
            captured_calls.append(repo_root)
            return [candidate]

        monkeypatch.setattr(mine_review_rounds, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = argparse.Namespace(local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_rounds(args)

        assert exit_code == 0
        assert captured_calls == [tmp_path / "repo"]
        out_path = tmp_path / "local" / "review_round_candidates.json"
        assert defects.load_candidates(out_path) == [candidate]

    def test_prints_the_written_candidate_count_and_path(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from review_bench import mine_review_rounds

        monkeypatch.setattr(mine_review_rounds, "mine", lambda repo_root: [])

        out_path = tmp_path / "local" / "review_round_candidates.json"
        args = argparse.Namespace(local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_rounds(args)

        assert exit_code == 0
        assert f"mine-rounds: wrote 0 candidate(s) to {out_path}" in capsys.readouterr().err


class TestCmdSnapshotArms:
    def test_writes_both_arms_under_the_given_root_and_reports_each(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        captured_calls: list[tuple[str, Path]] = []
        monkeypatch.setattr(
            arms_mod, "write_arm_snapshot", lambda arm, dest_dir: captured_calls.append((arm, dest_dir)),
        )

        arms_root = tmp_path / "arms"
        exit_code = run_review_bench.cmd_snapshot_arms(argparse.Namespace(arms_root=str(arms_root)))

        assert exit_code == 0
        assert captured_calls == [
            (arms_mod.ARM_CURRENT_RULE, arms_root / arms_mod.ARM_CURRENT_RULE),
            (arms_mod.ARM_FUNCTION_CONTEXT, arms_root / arms_mod.ARM_FUNCTION_CONTEXT),
        ]
        stderr = capsys.readouterr().err
        lens_count = len(arms_mod.LENS_READ_CLAUSES)
        assert (
            f"snapshot-arms: wrote {lens_count} lens file(s) for {arms_mod.ARM_CURRENT_RULE} "
            f"under {arms_root / arms_mod.ARM_CURRENT_RULE}"
        ) in stderr
        assert (
            f"snapshot-arms: wrote {lens_count} lens file(s) for {arms_mod.ARM_FUNCTION_CONTEXT} "
            f"under {arms_root / arms_mod.ARM_FUNCTION_CONTEXT}"
        ) in stderr


def _spot_check_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    kwargs = dict(
        reviewer_records_path=str(tmp_path / "reviewer.jsonl"), judge_records_path=str(tmp_path / "judge.jsonl"),
        seed=0,
    )
    kwargs.update(overrides)
    return argparse.Namespace(**kwargs)


class TestCmdSpotCheckExport:
    def test_writes_the_sample_without_judge_label_or_arm_and_reports_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        precision_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1:0", kind=adjudicate.SPOT_CHECK_KIND_PRECISION, judge_label="VALID",
                display_text="precision text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(
            run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, precision_sample),
        )

        out_path = tmp_path / "export.json"
        args = _spot_check_args(tmp_path, out=str(out_path))
        exit_code = run_review_bench.cmd_spot_check_export(args)

        assert exit_code == 0
        exported = json.loads(out_path.read_text())
        assert exported == [
            {"item_id": "d1:r1", "kind": adjudicate.SPOT_CHECK_KIND_RECALL, "display_text": "recall text"},
            {"item_id": "d1:r1:0", "kind": adjudicate.SPOT_CHECK_KIND_PRECISION, "display_text": "precision text"},
        ]
        stderr = capsys.readouterr().err
        assert f"spot-check export: wrote 1 recall item(s) and 1 precision item(s) to {out_path}" in stderr


class TestCmdSpotCheckImport:
    def test_scores_kappa_per_kind_and_split_agreement_per_arm(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        precision_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1:0", kind=adjudicate.SPOT_CHECK_KIND_PRECISION, judge_label="VALID",
                display_text="precision text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(
            run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, precision_sample),
        )

        labels_path = tmp_path / "labels.json"
        labels_path.write_text(json.dumps([
            {"item_id": "d1:r1", "human_label": "FOUND"},
            {"item_id": "d1:r1:0", "human_label": "VALID", "split_ok": True},
        ]))

        args = _spot_check_args(tmp_path, labels_path=str(labels_path))
        exit_code = run_review_bench.cmd_spot_check_import(args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"spot-check import: {adjudicate.SPOT_CHECK_KIND_RECALL} kappa = 1.000 -- validated" in stderr
        assert f"spot-check import: {adjudicate.SPOT_CHECK_KIND_PRECISION} kappa = 1.000 -- validated" in stderr
        assert f"spot-check import: {arms_mod.ARM_CURRENT_RULE} split agreement = 1.000" in stderr
