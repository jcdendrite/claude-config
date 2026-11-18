"""Tests for `summarize`, over synthetic run records under `tmp_path`."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from replay_bench import records, summary, tasks
from replay_bench.campaign import task_set_hash
from replay_bench.comment_metrics import CommentMetrics
from replay_bench.spend import SpendFile, SpendLine
from review_bench.runner import HarnessInvalidatedError, RunStore

ACCOUNT = "/synthetic/account"
CAP = 3.0


def metrics(*, words=10, code=10, docstring=0, unclassified=0, ast_rejected=0, non_file=0, undecodable=0) -> dict:
    synthetic = CommentMetrics()
    synthetic.comment_words["non_test"] = words
    synthetic.line_counts["non_test"].update(code=code, docstring=docstring, unclassified=unclassified)
    synthetic.python_files_ast_rejected = ast_rejected
    synthetic.non_file_entries = non_file
    synthetic.undecodable_files = undecodable
    return synthetic.to_dict()


def record(task_id, arm, *, run_index=0, ok=True, reason=None, run_metrics=None, denials=0, cli="2.1.322", ignored=0):
    return records.ReplayRunRecord(
        campaign_id="c1", task_id=task_id, arm=arm, run_index=run_index, status="ok" if ok else "missing",
        missing_reason=None if ok else reason, missing_detail=None, attempts=1, account_key=ACCOUNT, session_id="s",
        cli_version=cli, ambient_config_commit="commit", wall_clock_s=1.0, total_cost_usd=1.0,
        metrics=(run_metrics or metrics()) if ok else None, permission_denials=denials, ignored_paths_count=ignored,
    )


class Campaign:
    def __init__(self, tmp_path: Path) -> None:
        self.local_dir = tmp_path / ".local"
        self.dir = self.local_dir / "campaigns" / "c1"
        self.store = RunStore(self.dir / "store")

    def add_block(self, task_id, *run_records, complete=True) -> None:
        for run in run_records:
            records.write_run(self.dir, run, None)
        if complete:
            self.store.mark_block_complete(task_id)

    def summarize(self) -> list[str]:
        return summary.summarize(self.dir, local_dir=self.local_dir, budget_cap_usd=CAP)


@pytest.fixture
def campaign(tmp_path):
    return Campaign(tmp_path)


def number_after(lines: list[str], prefix: str) -> float:
    line = next(line for line in lines if line.startswith(prefix))
    return float(re.search(r"-?\d+\.\d+|-?\d+", line[len(prefix):]).group())


class TestThePrimaryOutcome:
    def test_it_is_a_ratio_of_sums_over_a_tasks_runs(self, campaign):
        campaign.add_block(
            "t1",
            record("t1", "toolkit", run_metrics=metrics(words=10, code=10)),
            record("t1", "toolkit", run_index=1, run_metrics=metrics(words=30, code=90)),
            record("t1", "bare", run_metrics=metrics(words=0, code=10)),
        )

        lines = campaign.summarize()

        assert number_after(lines, "paired difference toolkit - bare:") == pytest.approx(40 / 100)

    def test_one_paired_task_prints_no_interval(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))

        line = next(line for line in campaign.summarize() if line.startswith("paired difference"))

        assert "no interval for fewer than two paired tasks" in line

    def test_two_paired_tasks_print_an_interval(self, campaign):
        for task_id, words in (("t1", 20), ("t2", 30)):
            campaign.add_block(task_id, record(task_id, "toolkit", run_metrics=metrics(words=words)), record(task_id, "bare"))

        line = next(line for line in campaign.summarize() if line.startswith("paired difference"))

        assert re.fullmatch(r"paired difference toolkit - bare: -?\d+\.\d{4}, 95% interval \[-?\d+\.\d{4}, -?\d+\.\d{4}\]", line)


class TestThePairedOutcomeAsValues:
    def blocks(self, *differences_in_words):
        """The bare arm writes no words, so a test over these blocks passes whether or not the bare ratio is subtracted."""
        return {
            f"t{index}": [
                record(f"t{index}", "toolkit", run_metrics=metrics(words=words, code=10)),
                record(f"t{index}", "bare", run_metrics=metrics(words=0, code=10)),
            ]
            for index, words in enumerate(differences_in_words)
        }

    def test_each_tasks_difference_is_its_toolkit_ratio_less_its_bare_ratio(self):
        outcome = summary.paired_outcome(self.blocks(20, 30, 70))

        assert outcome.differences == {"t0": pytest.approx(2.0), "t1": pytest.approx(3.0), "t2": pytest.approx(7.0)}
        assert outcome.mean == pytest.approx(4.0)

    def test_a_nonzero_bare_ratio_is_subtracted_and_a_two_run_bare_arm_is_a_ratio_of_sums(self):
        """Kills: dropping or adding the bare term, and averaging a two-run arm's per-run ratios."""
        outcome = summary.paired_outcome({
            "two-run-bare": [
                record("two-run-bare", "toolkit", run_metrics=metrics(words=20, code=10)),
                record("two-run-bare", "bare", run_metrics=metrics(words=6, code=10)),
                record("two-run-bare", "bare", run_index=1, run_metrics=metrics(words=4, code=30)),
            ],
            "one-run-bare": [
                record("one-run-bare", "toolkit", run_metrics=metrics(words=30, code=10)),
                record("one-run-bare", "bare", run_metrics=metrics(words=9, code=6)),
            ],
        })

        toolkit_ratio, bare_ratio = 20 / 10, (6 + 4) / (10 + 30)
        assert bare_ratio != pytest.approx((6 / 10 + 4 / 30) / 2), "the fixture tells a ratio of sums from a mean of ratios"
        assert outcome.differences == {
            "two-run-bare": pytest.approx(toolkit_ratio - bare_ratio),
            "one-run-bare": pytest.approx(30 / 10 - 9 / 6),
        }
        assert outcome.mean == pytest.approx(((toolkit_ratio - bare_ratio) + (30 / 10 - 9 / 6)) / 2)

    def test_the_interval_of_two_paired_tasks_spans_exactly_their_two_differences(self):
        """Kills: a bootstrap statistic that ignores the resampled IDs, which would collapse the interval to the mean."""
        outcome = summary.paired_outcome(self.blocks(20, 30))

        assert outcome.interval == (pytest.approx(2.0), pytest.approx(3.0))

    def test_the_interval_of_several_tasks_lies_within_their_differences(self):
        outcome = summary.paired_outcome(self.blocks(20, 30, 70, 10))

        low, high = outcome.interval
        assert min(outcome.differences.values()) <= low < high <= max(outcome.differences.values())

    def test_one_paired_task_has_no_interval(self):
        single = summary.paired_outcome(self.blocks(20))
        nothing = summary.paired_outcome({})

        assert single.mean == pytest.approx(2.0) and single.interval is None
        assert nothing.mean is None and nothing.interval is None and nothing.differences == {}

    def test_each_exclusion_is_tallied_with_its_records(self):
        blocks = {
            "paired": [record("paired", "toolkit"), record("paired", "bare")],
            "no-toolkit": [record("no-toolkit", "toolkit", ok=False, reason="timeout"), record("no-toolkit", "bare")],
            "no-bare": [record("no-bare", "toolkit"), record("no-bare", "bare", ok=False, reason="arm-leak")],
            "neither": [
                record("neither", "toolkit", ok=False, reason="timeout"),
                record("neither", "bare", ok=False, reason="result-error"),
            ],
            "no-code": [record("no-code", "toolkit"), record("no-code", "bare", run_metrics=metrics(words=5, code=0))],
        }

        outcome = summary.paired_outcome(blocks)

        assert list(outcome.differences) == ["paired"]
        assert outcome.excluded_counts == {"toolkit": 1, "bare": 1, "both": 1, "code": 1}
        assert [run.missing_reason for run in outcome.excluded_records["toolkit"]] == ["timeout"]
        assert [run.missing_reason for run in outcome.excluded_records["bare"]] == ["arm-leak"]
        assert sorted(run.missing_reason for run in outcome.excluded_records["both"]) == ["result-error", "timeout"]


class TestTheExclusionLine:
    def test_it_counts_each_exclusion_with_its_missing_reasons(self, campaign):
        campaign.add_block("paired", record("paired", "toolkit"), record("paired", "bare"))
        campaign.add_block(
            "no-toolkit", record("no-toolkit", "toolkit", ok=False, reason="timeout"), record("no-toolkit", "bare"),
        )
        for task_id in ("no-bare-1", "no-bare-2"):
            campaign.add_block(task_id, record(task_id, "toolkit"), record(task_id, "bare", ok=False, reason="arm-leak"))
        campaign.add_block(
            "neither", record("neither", "toolkit", ok=False, reason="timeout"),
            record("neither", "bare", ok=False, reason="result-error"),
        )
        campaign.add_block(
            "no-code", record("no-code", "toolkit"), record("no-code", "bare", run_metrics=metrics(words=5, code=0)),
        )

        lines = campaign.summarize()

        assert "paired 1 of 6 complete tasks" in lines
        excluded = {
            label: int(count)
            for label, count in (
                re.fullmatch(r"  excluded, (.+?): (\d+).*", line).groups() for line in lines if line.startswith("  excluded")
            )
        }
        assert excluded == {
            "toolkit with no valid run": 1, "bare with no valid run": 2, "both arms with no valid run": 1,
            "no non-test code line in an arm": 1,
        }
        by_label = {line.split(": ")[0]: line for line in lines if line.startswith("  excluded")}
        assert "timeout x1" in by_label["  excluded, toolkit with no valid run"]
        assert "arm-leak x2" in by_label["  excluded, bare with no valid run"]
        assert "result-error x1" in by_label["  excluded, both arms with no valid run"]
        assert "result-error" not in by_label["  excluded, toolkit with no valid run"]


class TestCompleteAndIncompleteBlocks:
    def test_an_incomplete_blocks_records_are_left_out(self, campaign):
        campaign.add_block("done", record("done", "toolkit"), record("done", "bare"))
        campaign.add_block("partial", record("partial", "toolkit", run_metrics=metrics(words=999)), complete=False)

        lines = campaign.summarize()

        assert "1 complete blocks, 1 incomplete blocks" in lines[0]
        assert "paired 1 of 1 complete tasks" in lines
        assert "toolkit: 1 ok" in "\n".join(lines)

    def test_a_campaign_that_does_not_exist_is_refused(self, tmp_path):
        with pytest.raises(HarnessInvalidatedError, match="holds no campaign"):
            summary.summarize(tmp_path / "absent", local_dir=tmp_path, budget_cap_usd=CAP)

    def test_records_from_two_environment_readings_of_one_account_are_refused(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare", cli="2.9.9"))

        with pytest.raises(HarnessInvalidatedError, match="readings"):
            campaign.summarize()


def unparseable_record_texts() -> dict[str, str]:
    complete = record("t1", "toolkit").to_dict()
    without_arm = {key: value for key, value in complete.items() if key != "arm"}
    return {
        "invalid-json": "{not json",
        "null": "null",
        "a-list": "[]",
        "an-unknown-key": json.dumps({**complete, "unknown_key": 1}),
        "a-missing-required-key": json.dumps(without_arm),
    }


class TestARecordThatDoesNotParse:
    @pytest.fixture(params=sorted(unparseable_record_texts()))
    def unparseable_record_path(self, request, campaign) -> Path:
        path = records.record_path(campaign.dir, "t1", "toolkit", 0)
        path.parent.mkdir(parents=True)
        path.write_text(unparseable_record_texts()[request.param], encoding="utf-8")
        return path

    def test_reading_the_block_raises(self, campaign, unparseable_record_path):
        with pytest.raises(HarnessInvalidatedError) as raised:
            records.read_block_records(campaign.dir, "t1")

        assert str(unparseable_record_path) in str(raised.value)
        assert "cannot read the run record" in str(raised.value)

    def test_summarize_refuses_a_complete_block_holding_it(self, campaign, unparseable_record_path):
        campaign.store.mark_block_complete("t1")

        with pytest.raises(HarnessInvalidatedError) as raised:
            campaign.summarize()

        assert str(unparseable_record_path) in str(raised.value)

    def test_a_well_formed_record_in_the_same_place_reads_back(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"))

        assert [run.arm for run in records.read_block_records(campaign.dir, "t1")] == ["toolkit"]


def tree_snapshot(directory: Path) -> dict[str, tuple[int, bytes | None]]:
    """The bytes join the mtime so a rewrite within one timestamp tick still differs."""
    return {
        str(path.relative_to(directory)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
        for path in [directory, *directory.rglob("*")]
    }


class TestSummarizeCreatesNothing:
    def test_a_campaign_directory_is_left_exactly_as_it_was(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))
        campaign.add_block("t2", record("t2", "toolkit"), complete=False)
        spend = SpendFile(campaign.dir / "spend.jsonl", budget_cap_usd=CAP)
        spend.append(SpendLine("t1", "bare", 0, 1, ACCOUNT, "s", 2.0, False))
        (campaign.dir / "manifest.json").write_text(json.dumps({"budget_cap_usd": CAP}))
        before = tree_snapshot(campaign.dir)
        assert {"manifest.json", "spend.jsonl", "store", "runs"} <= set(before), "the snapshot sees each kind of file"

        lines = campaign.summarize()

        assert "1 complete blocks, 1 incomplete blocks" in lines[0]
        assert tree_snapshot(campaign.dir) == before

    def test_a_campaign_with_no_store_directory_gets_none(self, tmp_path):
        campaign_dir = tmp_path / ".local" / "campaigns" / "c1"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "manifest.json").write_text(json.dumps({"budget_cap_usd": CAP}))
        before = tree_snapshot(campaign_dir)

        lines = summary.summarize(campaign_dir, local_dir=tmp_path / ".local", budget_cap_usd=CAP)

        assert "0 complete blocks, 0 incomplete blocks" in lines[0]
        assert set(before) == {".", "manifest.json"}
        assert tree_snapshot(campaign_dir) == before


class TestSecondaryAndDiagnostics:
    def test_each_arm_prints_its_secondary_outcome_lines(self, campaign):
        campaign.add_block(
            "t1",
            record(
                "t1", "toolkit", run_metrics=metrics(code=50, docstring=10, unclassified=5, ast_rejected=2, non_file=1),
                denials=3,
            ),
            record("t1", "bare", run_metrics=metrics(code=100, docstring=5, unclassified=3, undecodable=4), ignored=7),
        )

        lines = campaign.summarize()
        text = "\n".join(lines)

        assert number_after(lines, "  toolkit docstring lines per 100 code lines:") == pytest.approx(20.0)
        assert number_after(lines, "  bare docstring lines per 100 code lines:") == pytest.approx(5.0)
        assert "runs with a permission-rule denial 1" in text
        assert "python files ast.parse rejected 2" in text
        assert "toolkit: unclassified-line share 0.0769," in text, "5 of 65 lines"
        assert "bare: unclassified-line share 0.0278," in text, "3 of 108 lines"
        assert "non_file_entries 1" in text and "undecodable_files 4" in text and "ignored_paths 7" in text

    def test_a_missing_run_still_counts_its_denials_and_ignored_paths(self, campaign):
        """The two counts are diagnostics of a run that went wrong, which a missing run is the likeliest to be."""
        campaign.add_block(
            "t1",
            record("t1", "toolkit"),
            record("t1", "bare"),
            record("t1", "bare", run_index=1, ok=False, reason="timeout", denials=2, ignored=3),
        )

        lines = campaign.summarize()

        bare_line = next(line for line in lines if line.startswith("  bare: unclassified-line share"))
        assert "runs with a permission-rule denial 1," in bare_line
        assert "ignored_paths 3," in bare_line

    def test_a_run_with_no_non_test_code_line_enters_its_tasks_sums(self, campaign):
        campaign.add_block(
            "t1",
            record("t1", "toolkit", run_metrics=metrics(words=10, code=10)),
            record("t1", "toolkit", run_index=1, run_metrics=metrics(words=5, code=0)),
            record("t1", "bare", run_metrics=metrics(words=0, code=10)),
        )

        lines = campaign.summarize()

        assert number_after(lines, "paired difference toolkit - bare:") == pytest.approx(15 / 10)
        assert "runs with no non-test code line 1" in "\n".join(lines)


class TestAnUnavailableReferenceNamesItsCause:
    def reference_line(self, campaign):
        return next(line for line in campaign.summarize() if line.startswith("  reference unavailable"))

    def test_a_campaign_with_no_manifest_says_so(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))

        assert self.reference_line(campaign) == "  reference unavailable: the campaign has no manifest"

    def test_an_unparseable_manifest_says_it_could_not_be_read(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))
        (campaign.dir / "manifest.json").write_text("not json")

        assert "the manifest could not be read: JSONDecodeError" in self.reference_line(campaign)

    def test_a_manifest_that_is_not_a_json_object_says_so(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))
        (campaign.dir / "manifest.json").write_text("[]")

        assert self.reference_line(campaign) == "  reference unavailable: the manifest is not a JSON object"

    @pytest.mark.parametrize("task_file_text", [None, "not json", '{"not": "a list"}'], ids=["gone", "unparseable", "not-a-list"])
    def test_a_task_set_that_cannot_be_read_gives_the_readers_cause(self, campaign, task_file_text):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))
        (campaign.dir / "manifest.json").write_text(json.dumps({"tasks_name": "damaged"}))
        tasks_dir = campaign.local_dir / "tasks"
        if task_file_text is not None:
            tasks_dir.mkdir()
            (tasks_dir / "damaged.json").write_text(task_file_text)
        with pytest.raises(tasks.TaskFileError) as cause:
            tasks.read_task_set(tasks_dir, "damaged")

        line = self.reference_line(campaign)

        assert line == f"  reference unavailable: task set 'damaged' could not be read: {cause.value}"


class TestAReadableReferenceIsReportedWithoutAWarning:
    def reference_task(self, *, code, docstring) -> tasks.ReplayTask:
        source_repo, commit = "/synthetic/source-repo", "a" * 40
        return tasks.ReplayTask(
            id=tasks.make_task_id(Path(source_repo), commit), source_repo=source_repo, base_commit="b" * 40, commit=commit,
            brief="synthetic brief", changed_paths=("src/app.py",), author_email="author@example.invalid",
            committer_email="committer@example.invalid",
            reference_metrics=CommentMetrics.from_dict(metrics(code=code, docstring=docstring)),
            base_density=None, base_tree_bytes=0, base_tree_entries=0,
        )

    def test_the_reference_rate_is_the_task_sets_docstring_lines_per_100_code_lines(self, campaign):
        task = self.reference_task(code=40, docstring=10)
        tasks.write_task_set(campaign.local_dir / "tasks", "readable", [task])
        (campaign.dir / "manifest.json").write_text(
            json.dumps({"tasks_name": "readable", "task_set_hash": task_set_hash((task,))}),
        )
        campaign.add_block(task.id, record(task.id, "toolkit"), record(task.id, "bare"))

        lines = campaign.summarize()

        counts = task.reference_metrics.line_counts["non_test"]
        expected_rate = 100 * counts["docstring"] / counts["code"]
        assert expected_rate == pytest.approx(25.0), "the fixture's metrics reach the task as written"
        assert number_after(lines, "  reference (the merged commits) docstring lines per 100 code lines:") == pytest.approx(
            expected_rate,
        )
        assert not any("reference unavailable" in line for line in lines)

    @pytest.mark.parametrize(
        ("pinned_hash", "reason"),
        [("0" * 64, "task set 'rewritten' is not the one this campaign pinned"), (None, "the manifest records no task set hash")],
        ids=["different-hash", "no-hash"],
    )
    def test_a_task_set_that_is_not_the_one_the_campaign_pinned_leaves_the_reference_unavailable(
        self, campaign, pinned_hash, reason,
    ):
        task = self.reference_task(code=40, docstring=10)
        tasks.write_task_set(campaign.local_dir / "tasks", "rewritten", [task])
        manifest = {"tasks_name": "rewritten"}
        if pinned_hash is not None:
            manifest["task_set_hash"] = pinned_hash
        (campaign.dir / "manifest.json").write_text(json.dumps(manifest))
        campaign.add_block(task.id, record(task.id, "toolkit"), record(task.id, "bare"))

        lines = campaign.summarize()

        assert f"  reference unavailable: {reason}" in lines
        assert "  reference (the merged commits) docstring lines per 100 code lines: n/a" in lines
        assert any(line.startswith("paired difference toolkit - bare:") for line in lines)
        assert "paired 1 of 1 complete tasks" in lines


class TestSpendByAccount:
    def spend_with_one_unparsed_line(self, campaign) -> None:
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))
        spend = SpendFile(campaign.dir / "spend.jsonl", budget_cap_usd=CAP)
        spend.append(SpendLine("t1", "bare", 0, 1, ACCOUNT, "s", 2.0, False))
        spend.append(SpendLine("t1", "bare", 0, 1, "/other/account", "s", 1.0, False))
        with spend.path.open("a") as handle:
            handle.write("not json\n")

    def test_an_unparseable_spend_line_counts_at_the_flag_when_the_manifest_pins_no_cap(self, campaign):
        self.spend_with_one_unparsed_line(campaign)

        lines = campaign.summarize()

        assert f"  {ACCOUNT!r}: $5.00" in lines and "  '/other/account': $4.00" in lines
        assert any("did not parse" in line and "$3.00 each" in line and "manifest pins no usable cap" in line for line in lines)

    def test_an_unparseable_spend_line_counts_at_the_cap_the_campaign_pinned(self, campaign):
        self.spend_with_one_unparsed_line(campaign)
        (campaign.dir / "manifest.json").write_text(json.dumps({"budget_cap_usd": 2.0}))

        lines = campaign.summarize()

        assert f"  {ACCOUNT!r}: $4.00" in lines and "  '/other/account': $3.00" in lines
        assert any("did not parse" in line and "$2.00 each" in line and "campaign pinned" in line for line in lines)

    @pytest.mark.parametrize("unusable_cap", ["three", 0, -1.0, True], ids=["string", "zero", "negative", "bool"])
    def test_a_pinned_cap_that_is_not_a_positive_number_falls_back_to_the_flag(self, campaign, unusable_cap):
        self.spend_with_one_unparsed_line(campaign)
        (campaign.dir / "manifest.json").write_text(json.dumps({"budget_cap_usd": unusable_cap}))

        lines = campaign.summarize()

        assert any("did not parse" in line and "$3.00 each" in line and "manifest pins no usable cap" in line for line in lines)

