"""Tests for evals/run_review_bench.py's own CLI-level logic (dispatch 1c):
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
    out_of_session_paths: tuple[str, ...] = (),
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=0, opaque_run_id=opaque_run_id,
        status=status, missing_reason=None, observed_model="claude-sonnet-5", observed_tools=("Read",),
        out_of_session_paths=out_of_session_paths, findings_text=findings_text, wall_clock_s=1.0, read_calls=1,
        read_tokens_est=10, partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=0,
        dispatch_prompt_verbatim=True, cli_version="2.0.0", ambient_config_commit="deadbeef",
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


class TestCmdFreezeManifestFields:
    def test_writes_arm_dir_judge_agent_defects_json_and_prompt_template_hashes(
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
            last_smoke_manifest_hash=manifest_hash, smoke_full_k=10, main_commit_sha="", out=str(out_path),
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 0
        conditions = json.loads(out_path.read_text())

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
        self, tmp_path: Path,
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
        report = json.loads(out_path.read_text())
        assert report["out_of_session_read_counts_per_judge_kind"] == {JUDGE_ARM_RECALL: 1, JUDGE_ARM_PRECISION: 0}
