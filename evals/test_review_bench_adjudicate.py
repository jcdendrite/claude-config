"""Tests for evals/review_bench/adjudicate.py (dispatch 1c). Offline
throughout: every judge-input and parser test works from synthetic
findings/run-record text, never a real judge answer. No test launches
`claude`.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest
import run_review_bench
from review_bench import adjudicate, runner
from review_bench.defects import ConfirmedDefect
from test_review_bench_runner import INNER_PROMPT, _build_two_commit_source_repo, _load_scenario


def _run_record(defect_id: str, arm: str, opaque_run_id: str, findings_text: str, *, status: str = "ok") -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=0, opaque_run_id=opaque_run_id,
        status=status, missing_reason=None, observed_model="claude-sonnet-5", observed_tools=("Read",),
        out_of_session_paths=(), findings_text=findings_text, wall_clock_s=1.0, read_calls=1,
        read_tokens_est=10, partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=0,
        dispatch_prompt_verbatim=True, cli_version="2.0.0", ambient_config_commit="deadbeef",
    )


class TestNormalizeBenchPaths:
    def test_replaces_bench_path_with_neutral_token(self) -> None:
        text = "See .bench/change-function-context.diff for the function context."
        assert adjudicate.normalize_bench_paths(text) == "See [bench file] for the function context."

    def test_preserves_a_trailing_line_reference(self) -> None:
        text = "Cited at .bench/change.diff:42."
        assert adjudicate.normalize_bench_paths(text) == "Cited at [bench file]:42."

    def test_replaces_a_bare_basename_with_no_bench_prefix(self) -> None:
        # A finding narrating an arm-2-specific artifact by its bare
        # filename must still be blinded, not only its `.bench/`-prefixed
        # form.
        text = "Per change-function-context.diff, lines 10-20 replace the old context."
        assert adjudicate.normalize_bench_paths(text) == "Per [bench file], lines 10-20 replace the old context."

    def test_does_not_match_a_bare_basename_as_a_substring_of_a_longer_word(self) -> None:
        text = "change.diffs are not the same file."
        assert adjudicate.normalize_bench_paths(text) == text


class TestBlindOrdering:
    def test_deterministic_given_the_same_ids_and_seed(self) -> None:
        ids = ["run-a", "run-b", "run-c"]
        assert adjudicate.order_by_opaque_id(ids, seed=7) == adjudicate.order_by_opaque_id(list(reversed(ids)), seed=7)

    def test_carries_no_arm_signal_no_bench_path_in_recall_or_precision_input(self, tmp_path) -> None:
        # Permuting which arm produced which opaque ID must never change the
        # order the runs are presented in (Verification: "Permuting the arm
        # fields of a defect's runs leaves both judges' input order
        # unchanged"), and neither judge's input names an arm or a `.bench/`
        # path (Verification: "No arm name and no .bench path appears in
        # either judge's input").
        records_a = [
            _run_record("d1", "current-rule", "run-a", "Found it, see .bench/change.diff."),
            _run_record("d1", "function-context", "run-b", "No issues."),
        ]
        records_b = [
            _run_record("d1", "function-context", "run-a", "Found it, see .bench/change.diff."),
            _run_record("d1", "current-rule", "run-b", "No issues."),
        ]
        precision_a = adjudicate.build_precision_judge_input(records_a, seed=1)
        precision_b = adjudicate.build_precision_judge_input(records_b, seed=1)
        assert precision_a.order == precision_b.order
        assert "current-rule" not in precision_a.text
        assert "function-context" not in precision_a.text
        assert ".bench/" not in precision_a.text

        source_repo = tmp_path / "source"
        defect = _build_two_commit_source_repo(source_repo)
        recall_a = adjudicate.build_recall_judge_input(defect, records_a, source_repo=source_repo, seed=1)
        recall_b = adjudicate.build_recall_judge_input(defect, records_b, source_repo=source_repo, seed=1)
        assert recall_a.order == recall_b.order
        assert "current-rule" not in recall_a.text
        assert "function-context" not in recall_a.text
        assert ".bench/" not in recall_a.text


class TestRecallAnswerParser:
    def test_accepts_multi_id_answer(self) -> None:
        findings = {"r1": "Leaks a connection on error.", "r2": "Nothing wrong here."}
        answer = 'r1: FOUND -- "Leaks a connection"\nr2: NOT_FOUND'
        result = adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings)
        assert result["r1"].label == "FOUND"
        assert result["r1"].quoted_opening == "Leaks a connection"
        assert result["r2"].label == "NOT_FOUND"

    def test_accepts_single_id_answer(self) -> None:
        findings = {"r1": "Leaks a connection on error."}
        answer = 'r1: NOT_FOUND'
        result = adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings)
        assert result["r1"].label == "NOT_FOUND"

    def test_tolerates_formatting_variation(self) -> None:
        findings = {"r1": "Leaks a connection on error."}
        answer = '**r1** - FOUND -- `"Leaks a connection"`'
        result = adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings)
        assert result["r1"].label == "FOUND"
        assert result["r1"].quoted_opening == "Leaks a connection"

    def test_rejects_answer_that_omits_an_id(self) -> None:
        findings = {"r1": "text one.", "r2": "text two."}
        answer = "r1: NOT_FOUND"
        assert adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings) is None

    def test_rejects_answer_that_labels_one_id_twice(self) -> None:
        findings = {"r1": "text one."}
        answer = "r1: NOT_FOUND\nr1: NOT_FOUND"
        assert adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings) is None

    def test_rejects_answer_naming_an_id_not_in_the_input(self) -> None:
        findings = {"r1": "text one."}
        answer = 'r1: NOT_FOUND\nzz9: NOT_FOUND'
        assert adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings) is None

    def test_rejects_a_label_other_than_found_or_not_found(self) -> None:
        findings = {"r1": "text one."}
        answer = "r1: partially found"
        assert adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings) is None

    def test_rejects_found_quoting_an_opening_absent_from_that_ids_findings(self) -> None:
        findings = {"r1": "text one."}
        answer = 'r1: FOUND -- "this quote is not in the findings"'
        assert adjudicate.parse_recall_answer(answer, expected_ids=list(findings), normalized_findings_by_id=findings) is None


class TestPrecisionSplitCheck:
    def test_accepts_openings_found_in_order_multi_finding(self) -> None:
        text = "First problem here. Second problem here."
        findings = [
            adjudicate.PrecisionFinding(label="VALID", quoted_opening="First problem"),
            adjudicate.PrecisionFinding(label="INVALID", quoted_opening="Second problem"),
        ]
        assert adjudicate.check_precision_split(findings, text) is True

    def test_accepts_a_single_finding(self) -> None:
        text = "One problem here."
        findings = [adjudicate.PrecisionFinding(label="VALID", quoted_opening="One problem")]
        assert adjudicate.check_precision_split(findings, text) is True

    def test_accepts_zero_findings(self) -> None:
        assert adjudicate.check_precision_split([], "No issues at all.") is True

    def test_rejects_an_opening_absent_from_the_text(self) -> None:
        findings = [adjudicate.PrecisionFinding(label="VALID", quoted_opening="Not present anywhere")]
        assert adjudicate.check_precision_split(findings, "One problem here.") is False

    def test_rejects_openings_claimed_out_of_order(self) -> None:
        text = "First problem here. Second problem here."
        findings = [
            adjudicate.PrecisionFinding(label="VALID", quoted_opening="Second problem"),
            adjudicate.PrecisionFinding(label="VALID", quoted_opening="First problem"),
        ]
        # "First problem" only occurs before "Second problem" in the text, so
        # searching for it AFTER "Second problem"'s own match position fails.
        assert adjudicate.check_precision_split(findings, text) is False

    def test_rejects_a_repeat_of_an_opening_the_text_contains_only_once(self) -> None:
        text = "Only one problem here."
        findings = [
            adjudicate.PrecisionFinding(label="VALID", quoted_opening="Only one problem"),
            adjudicate.PrecisionFinding(label="VALID", quoted_opening="Only one problem"),
        ]
        assert adjudicate.check_precision_split(findings, text) is False

    def test_accepts_a_single_run_on_paragraph_with_no_real_split(self) -> None:
        text = "This whole run is one long run-on sentence about a single vague issue that never really separates."
        findings = [adjudicate.PrecisionFinding(label="INVALID", quoted_opening="This whole run")]
        assert adjudicate.check_precision_split(findings, text) is True


class TestPrecisionAnswerParser:
    def test_handles_multi_finding_answer(self) -> None:
        text = "First problem here. Second problem here."
        findings_by_id = {"r1": text}
        answer = '### Run r1\n1. VALID -- "First problem"\n2. INVALID -- "Second problem"\n'
        result = adjudicate.parse_precision_answer(answer, expected_ids=["r1"], normalized_findings_by_id=findings_by_id)
        assert [f.label for f in result["r1"]] == ["VALID", "INVALID"]

    def test_handles_single_finding_answer(self) -> None:
        findings_by_id = {"r1": "One problem here."}
        answer = '### Run r1\n1. VALID -- "One problem"\n'
        result = adjudicate.parse_precision_answer(answer, expected_ids=["r1"], normalized_findings_by_id=findings_by_id)
        assert len(result["r1"]) == 1

    def test_handles_zero_finding_answer(self) -> None:
        findings_by_id = {"r1": "No issues at all."}
        answer = "### Run r1\n"
        result = adjudicate.parse_precision_answer(answer, expected_ids=["r1"], normalized_findings_by_id=findings_by_id)
        assert result["r1"] == []

    def test_rejects_malformed_answer_missing_a_run_section(self) -> None:
        findings_by_id = {"r1": "text.", "r2": "other text."}
        answer = '### Run r1\n1. VALID -- "text"\n'
        assert (
            adjudicate.parse_precision_answer(answer, expected_ids=["r1", "r2"], normalized_findings_by_id=findings_by_id)
            is None
        )

    def test_rejects_malformed_finding_line_with_no_quoted_opening(self) -> None:
        findings_by_id = {"r1": "text."}
        answer = "### Run r1\n1. VALID with no quote at all\n"
        assert adjudicate.parse_precision_answer(answer, expected_ids=["r1"], normalized_findings_by_id=findings_by_id) is None

    def test_rejects_when_split_check_fails(self) -> None:
        findings_by_id = {"r1": "One problem here."}
        answer = '### Run r1\n1. VALID -- "not in the text"\n'
        assert adjudicate.parse_precision_answer(answer, expected_ids=["r1"], normalized_findings_by_id=findings_by_id) is None


class TestSpotCheckKappaAndSplitAgreement:
    def test_cohens_kappa_on_a_known_table(self) -> None:
        # Standard hand-computed example: 10 items, judge labels
        # [A,A,A,B,B,B,B,B,B,B], human labels [A,A,B,B,B,B,B,B,B,B].
        # p_o = 0.9, p_e = 0.3*0.2 + 0.7*0.8 = 0.62, kappa = 0.28/0.38.
        judge = ["A", "A", "A", "B", "B", "B", "B", "B", "B", "B"]
        human = ["A", "A", "B", "B", "B", "B", "B", "B", "B", "B"]
        assert adjudicate.cohens_kappa(judge, human) == pytest.approx(0.28 / 0.38)

    def test_cohens_kappa_perfect_agreement_is_one(self) -> None:
        assert adjudicate.cohens_kappa(["A", "B", "A"], ["A", "B", "A"]) == 1.0

    def test_split_agreement_reported_per_arm(self) -> None:
        candidates = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1:0", kind="precision", judge_label="VALID", display_text="x", arm="current-rule",
            ),
            adjudicate.SpotCheckCandidate(
                item_id="d1:r2:0", kind="precision", judge_label="VALID", display_text="y", arm="function-context",
            ),
        ]
        human_labels = [
            adjudicate.HumanSpotCheckLabel(item_id="d1:r1:0", human_label="VALID", split_ok=True),
            adjudicate.HumanSpotCheckLabel(item_id="d1:r2:0", human_label="VALID", split_ok=False),
        ]
        agreement = adjudicate.split_agreement_by_arm(candidates, human_labels)
        assert agreement == {"current-rule": 1.0, "function-context": 0.0}


class TestSpotCheckSampling:
    def test_returns_every_candidate_when_pool_is_smaller_than_sample_size(self) -> None:
        candidates = [
            adjudicate.SpotCheckCandidate(
                item_id=f"d1:r{i}", kind="recall", judge_label="FOUND", display_text="x", arm="current-rule",
            )
            for i in range(5)
        ]
        sample = adjudicate.select_spot_check_sample(candidates, sample_size=100, seed=0)
        assert len(sample) == 5

    def test_never_exceeds_the_requested_sample_size(self) -> None:
        candidates = [
            adjudicate.SpotCheckCandidate(
                item_id=f"d1:r{i}", kind="recall", judge_label="FOUND" if i % 2 == 0 else "NOT_FOUND",
                display_text="x", arm="current-rule",
            )
            for i in range(250)
        ]
        sample = adjudicate.select_spot_check_sample(candidates, sample_size=100, seed=0)
        assert len(sample) == 100

    def test_export_never_writes_judge_label_or_arm(self, tmp_path) -> None:
        candidates = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind="recall", judge_label="FOUND", display_text="x", arm="current-rule",
            ),
        ]
        out_path = tmp_path / "sheet.json"
        adjudicate.export_spot_check(candidates, out_path)
        text = out_path.read_text()
        assert "FOUND" not in text
        assert "current-rule" not in text


class TestJudgeRunRetry:
    """The judge-side analog of test_review_bench_runner.TestRetryThenMissing
    -- same fixture scenario and monkeypatch shape, driven through
    run_judge_with_retry instead of runner.run_one_with_retry."""

    def test_failing_judge_run_is_retried_once_then_recorded_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scenario = _load_scenario(tmp_path, "model-mismatch")
        inner_prompt = adjudicate.build_judge_inner_prompt(adjudicate.RECALL_DATA_FILE_NAME)
        session_jsonl = scenario / "session-1.jsonl"
        session_jsonl.write_text(session_jsonl.read_text().replace(INNER_PROMPT, inner_prompt))
        calls = {"n": 0}

        def fake_launch(cmd, cwd, timeout_s):
            calls["n"] += 1
            return [], False

        monkeypatch.setattr(
            runner, "find_session_jsonl_by_id", lambda projects_root, session_id: scenario / "session-1.jsonl",
        )

        ctx = adjudicate.JudgeRunContext(
            campaign_id="c1", defect_id="d1", judge_kind=adjudicate.JUDGE_ARM_RECALL,
            agent_name="bench-staff-backend-engineer", agent_declared_tools=frozenset({"Read", "Grep", "Glob"}),
            data_file_name=adjudicate.RECALL_DATA_FILE_NAME, fixture_dir=scenario, live_checkout_roots=(),
            changed_relpaths=(), model_id="claude-sonnet-5", budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False), expected_ids=(),
            normalized_findings_by_id={},
        )
        attempt = adjudicate.run_judge_with_retry(ctx, launch=fake_launch)

        assert calls["n"] == 2  # exactly one retry
        assert attempt.record.status == runner.STATUS_MISSING
        assert attempt.record.missing_reason == runner.VALIDITY_FAIL_MODEL_MISMATCH


class TestValidateJudgeAnswer:
    def test_invalid_recall_answer_downgrades_status_to_missing(self) -> None:
        record = _run_record("d1", adjudicate.JUDGE_ARM_RECALL, "j1", "not a valid recall answer at all")
        ctx = adjudicate.JudgeRunContext(
            campaign_id="c1", defect_id="d1", judge_kind=adjudicate.JUDGE_ARM_RECALL,
            agent_name=adjudicate.RECALL_JUDGE_AGENT_NAME, agent_declared_tools=adjudicate.RECALL_JUDGE_TOOLS,
            data_file_name=adjudicate.RECALL_DATA_FILE_NAME, fixture_dir=Path("/unused"), live_checkout_roots=(),
            changed_relpaths=(), model_id=runner.JUDGE_MODEL_ID, budget_cap_usd=1.0, timeout_s=1,
            environment=runner.EnvironmentRecord("v1", "sha1", False), expected_ids=("r1",),
            normalized_findings_by_id={"r1": "some finding"},
        )

        result = adjudicate._validate_judge_answer(record, ctx)

        assert result.status == runner.STATUS_MISSING
        assert result.missing_reason == runner.MISSING_REASON_INVALID_ANSWER


class TestCmdJudgeResume:
    def test_skips_already_completed_defect_and_judges_the_pending_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from review_bench import defects as defects_mod

        defect_ids = ["d1", "d2"]
        confirmed = [
            ConfirmedDefect(
                id=defect_id, source="szz", lens="staff-backend-engineer", base_commit="a" * 40,
                head_commit="b" * 40, fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            )
            for defect_id in defect_ids
        ]
        defects_path = tmp_path / "defects.json"
        defects_mod.save_confirmed_defects(defects_path, confirmed)

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record(defect_id, "current-rule", f"{defect_id}-run", "some finding") for defect_id in defect_ids
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_run_store_dir = tmp_path / "judge-run-store"
        run_store = runner.RunStore(judge_run_store_dir)
        run_store.mark_block_complete("d1")  # simulates a defect this campaign already judged

        judged_defect_ids: list[str] = []

        def fake_run_defect_judges(defect, records, **kwargs):
            judged_defect_ids.append(defect.id)
            return (
                _run_record(defect.id, adjudicate.JUDGE_ARM_RECALL, "j-recall", "r1: NOT_FOUND"),
                _run_record(defect.id, adjudicate.JUDGE_ARM_PRECISION, "j-precision", "### Run r1\n"),
            )

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            judge_run_store_dir=str(judge_run_store_dir), judge_records_dir=str(tmp_path / "judge-runs"),
            campaign_id="judge-resume-test", seed=0,
        )
        exit_code = run_review_bench.cmd_judge(args)

        assert exit_code == 0
        assert judged_defect_ids == ["d2"]  # d1 was already complete, never re-judged
        assert run_store.completed_block_ids() == {"d1", "d2"}

    def test_git_error_for_one_defect_skips_it_and_continues_to_the_next(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from review_bench import defects as defects_mod

        defect_ids = ["d1", "d2"]
        confirmed = [
            ConfirmedDefect(
                id=defect_id, source="szz", lens="staff-backend-engineer", base_commit="a" * 40,
                head_commit="b" * 40, fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            )
            for defect_id in defect_ids
        ]
        defects_path = tmp_path / "defects.json"
        defects_mod.save_confirmed_defects(defects_path, confirmed)

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record(defect_id, "current-rule", f"{defect_id}-run", "some finding") for defect_id in defect_ids
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        def fake_run_defect_judges(defect, records, **kwargs):
            if defect.id == "d1":
                raise subprocess.CalledProcessError(1, ["git", "show"])
            return (
                _run_record(defect.id, adjudicate.JUDGE_ARM_RECALL, "j-recall", "r1: NOT_FOUND"),
                _run_record(defect.id, adjudicate.JUDGE_ARM_PRECISION, "j-precision", "### Run r1\n"),
            )

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        judge_records_dir = tmp_path / "judge-runs"
        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            judge_run_store_dir=str(tmp_path / "judge-run-store"), judge_records_dir=str(judge_records_dir),
            campaign_id="git-error-test", seed=0,
        )

        exit_code = run_review_bench.cmd_judge(args)

        assert exit_code == 0  # one defect's unreachable commits never abort the whole campaign
        judged = runner.read_run_records(judge_records_dir / "git-error-test.jsonl")
        assert {record.defect_id for record in judged} == {"d2"}

    def test_precision_failure_after_recall_already_persisted_reports_cost_already_spent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_judge's own skip message must name a defect whose recall cost
        is already spent and recorded separately from one that failed
        before any judge was dispatched."""
        from review_bench import defects as defects_mod

        confirmed = [
            ConfirmedDefect(
                id="d1", source="szz", lens="staff-backend-engineer", base_commit="a" * 40, head_commit="b" * 40,
                fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            ),
        ]
        defects_path = tmp_path / "defects.json"
        defects_mod.save_confirmed_defects(defects_path, confirmed)

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        def fake_run_defect_judges(defect, records, *, judge_records_path, **kwargs):
            # Emulates run_defect_judges' own contract (adjudicate.py): the
            # recall record is persisted before a precision-side git failure
            # can be raised.
            runner.append_run_records(
                judge_records_path, (_run_record(defect.id, adjudicate.JUDGE_ARM_RECALL, "j-recall", "r1: NOT_FOUND"),),
            )
            raise subprocess.CalledProcessError(1, ["git", "show"])

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            judge_run_store_dir=str(tmp_path / "judge-run-store"), judge_records_dir=str(tmp_path / "judge-runs"),
            campaign_id="c1", seed=0,
        )

        exit_code = run_review_bench.cmd_judge(args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert "judge: skipped d1 -- recall already recorded, precision failed to build" in stderr


class TestResolveJudgeRunStoreDir:
    def test_none_nests_under_the_default_dir_by_campaign_id(self) -> None:
        result = run_review_bench._resolve_judge_run_store_dir(None, "campaign-abc")
        assert result == run_review_bench.DEFAULT_JUDGE_RUN_STORE_DIR / "campaign-abc"

    def test_explicit_override_is_returned_unchanged(self) -> None:
        result = run_review_bench._resolve_judge_run_store_dir("/some/custom/dir", "campaign-abc")
        assert result == Path("/some/custom/dir")

    def test_two_campaigns_sharing_the_default_dir_do_not_see_each_others_completed_defects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Guards against RunStore.completed_block_ids being keyed by
        defect_id alone within one store directory: two campaigns sharing
        one un-nested default path would have the second campaign silently
        skip every defect the first one already judged."""
        from review_bench import defects as defects_mod

        monkeypatch.setattr(run_review_bench, "DEFAULT_JUDGE_RUN_STORE_DIR", tmp_path / "judge-run-store")

        confirmed = [
            ConfirmedDefect(
                id="d1", source="szz", lens="staff-backend-engineer", base_commit="a" * 40, head_commit="b" * 40,
                fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
            ),
        ]
        defects_path = tmp_path / "defects.json"
        defects_mod.save_confirmed_defects(defects_path, confirmed)

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        judged_defect_ids_by_campaign: dict[str, list[str]] = {}

        def fake_run_defect_judges(defect, records, *, campaign_id, **kwargs):
            judged_defect_ids_by_campaign.setdefault(campaign_id, []).append(defect.id)
            return (
                _run_record(defect.id, adjudicate.JUDGE_ARM_RECALL, "j-recall", "r1: NOT_FOUND"),
                _run_record(defect.id, adjudicate.JUDGE_ARM_PRECISION, "j-precision", "### Run r1\n"),
            )

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        def _args(campaign_id: str) -> argparse.Namespace:
            return argparse.Namespace(
                defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
                judge_run_store_dir=None, judge_records_dir=str(tmp_path / "judge-runs"),
                campaign_id=campaign_id, seed=0,
            )

        assert run_review_bench.cmd_judge(_args("campaign-a")) == 0
        assert run_review_bench.cmd_judge(_args("campaign-b")) == 0

        assert judged_defect_ids_by_campaign == {"campaign-a": ["d1"], "campaign-b": ["d1"]}


class TestRunDefectJudgesRecallPersistence:
    """A precision-side git failure (an unreachable commit) must not discard
    the recall judge's own already-paid result."""

    @staticmethod
    def _fake_run_judge_with_retry(judge_kind: str, opaque_run_id: str):
        findings_text = "r1: NOT_FOUND" if judge_kind == adjudicate.JUDGE_ARM_RECALL else "### Run r1\n"

        def _fake(ctx, *, launch=None, run_store=None):
            assert ctx.judge_kind == judge_kind
            return adjudicate.JudgeRunAttempt(
                record=_run_record("d1", judge_kind, opaque_run_id, findings_text), session_id="fake-session",
            )

        return _fake

    def test_persists_recall_before_a_precision_fixture_failure_and_never_redispatches_it_on_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_repo = tmp_path / "source"
        defect = _build_two_commit_source_repo(source_repo)
        judge_records_path = tmp_path / "judge.jsonl"
        run_store = runner.RunStore(tmp_path / "judge-run-store")

        monkeypatch.setattr(runner, "read_environment_record", lambda: runner.EnvironmentRecord("v1", "sha1", False))
        monkeypatch.setattr(runner, "session_store_dir_for", lambda projects_root, session_id: None)
        temp_dir_calls = {"n": 0}

        def fake_temp_dir(prefix: str) -> Path:
            temp_dir_calls["n"] += 1
            d = tmp_path / f"temp-dir-{temp_dir_calls['n']}"
            d.mkdir(parents=True, exist_ok=True)
            return d

        monkeypatch.setattr(runner.msmr, "_resolved_temp_project_dir", fake_temp_dir)
        monkeypatch.setattr(
            adjudicate, "install_recall_judge_fixture", lambda *a, **k: adjudicate.JudgeInput(text="", order=("r1",)),
        )
        monkeypatch.setattr(
            adjudicate, "run_judge_with_retry", self._fake_run_judge_with_retry(adjudicate.JUDGE_ARM_RECALL, "j-recall"),
        )

        def raise_unreachable_commit(*args, **kwargs):
            raise subprocess.CalledProcessError(128, ["git", "show"])

        monkeypatch.setattr(adjudicate, "install_precision_judge_fixture", raise_unreachable_commit)

        with pytest.raises(subprocess.CalledProcessError):
            adjudicate.run_defect_judges(
                defect, [], source_repo=source_repo, campaign_id="c1", seed=0, live_checkout_roots=(),
                run_store=run_store, judge_records_path=judge_records_path,
            )

        persisted = runner.read_run_records(judge_records_path)
        assert [record.arm for record in persisted] == [adjudicate.JUDGE_ARM_RECALL]
        assert run_store.completed_block_ids() == set()  # precision never completed, so never marked complete

        # Resume: the recall judge must never be redispatched, and its
        # already-recorded result is reused as-is.
        def fail_if_recall_redispatched(*args, **kwargs):
            raise AssertionError("recall judge redispatched on resume despite an already-recorded result")

        monkeypatch.setattr(adjudicate, "install_recall_judge_fixture", fail_if_recall_redispatched)
        monkeypatch.setattr(
            adjudicate, "install_precision_judge_fixture",
            lambda *a, **k: adjudicate.JudgeInput(text="", order=("r1",)),
        )
        monkeypatch.setattr(
            adjudicate, "run_judge_with_retry",
            self._fake_run_judge_with_retry(adjudicate.JUDGE_ARM_PRECISION, "j-precision"),
        )

        recall_record, precision_record = adjudicate.run_defect_judges(
            defect, [], source_repo=source_repo, campaign_id="c1", seed=0, live_checkout_roots=(),
            run_store=run_store, judge_records_path=judge_records_path, existing_recall_record=persisted[0],
        )

        assert recall_record is persisted[0]
        assert precision_record.arm == adjudicate.JUDGE_ARM_PRECISION
        # run_defect_judges never re-persists an already-recorded recall record.
        assert [record.arm for record in runner.read_run_records(judge_records_path)] == [adjudicate.JUDGE_ARM_RECALL]
