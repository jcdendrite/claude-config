"""Tests for `summarize`, over synthetic run records under `tmp_path`. Printed values are parsed back as numbers."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from replay_bench import records, summary
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
    def test_it_is_a_ratio_of_sums_over_a_tasks_valid_runs_and_not_a_mean_of_ratios(self, campaign):
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

        assert "95% interval [" in line


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
    def test_a_partial_incomplete_blocks_records_are_left_out_and_counted_as_incomplete(self, campaign):
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


class TestSecondaryAndDiagnostics:
    def test_each_arm_prints_docstring_lines_per_100_code_lines_and_its_diagnostics(self, campaign):
        campaign.add_block(
            "t1",
            record(
                "t1", "toolkit", run_metrics=metrics(code=50, docstring=10, unclassified=5, ast_rejected=2, non_file=1),
                denials=3,
            ),
            record("t1", "bare", run_metrics=metrics(code=100, docstring=5, undecodable=4), ignored=7),
        )

        lines = campaign.summarize()
        text = "\n".join(lines)

        assert number_after(lines, "  toolkit docstring lines per 100 code lines:") == pytest.approx(20.0)
        assert number_after(lines, "  bare docstring lines per 100 code lines:") == pytest.approx(5.0)
        assert "runs with a permission-rule denial 1" in text
        assert "python files ast.parse rejected 2" in text
        assert re.search(r"toolkit: unclassified-line share 0\.0\d+", text)
        assert "non_file_entries 1" in text and "undecodable_files 4" in text and "ignored_paths 7" in text

    def test_a_run_with_no_non_test_code_line_enters_its_tasks_sums_and_a_separate_tally(self, campaign):
        campaign.add_block(
            "t1",
            record("t1", "toolkit", run_metrics=metrics(words=10, code=10)),
            record("t1", "toolkit", run_index=1, run_metrics=metrics(words=5, code=0)),
            record("t1", "bare", run_metrics=metrics(words=0, code=10)),
        )

        lines = campaign.summarize()

        assert number_after(lines, "paired difference toolkit - bare:") == pytest.approx(15 / 10)
        assert "runs with no non-test code line 1" in "\n".join(lines)


class TestSpendByAccount:
    def test_an_unparseable_spend_line_counts_at_the_cap_against_every_account_and_is_said_so(self, campaign):
        campaign.add_block("t1", record("t1", "toolkit"), record("t1", "bare"))
        spend = SpendFile(campaign.dir / "spend.jsonl", budget_cap_usd=CAP)
        spend.append(SpendLine("t1", "bare", 0, 1, ACCOUNT, "s", 2.0, False))
        spend.append(SpendLine("t1", "bare", 0, 1, "/other/account", "s", 1.0, False))
        with spend.path.open("a") as handle:
            handle.write("not json\n")

        lines = campaign.summarize()

        assert f"  {ACCOUNT!r}: $5.00" in lines and "  '/other/account': $4.00" in lines
        assert any("did not parse" in line and "$3.00 each" in line for line in lines)

