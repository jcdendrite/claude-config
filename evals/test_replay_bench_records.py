"""Tests for the on-disk run record and diff a block's runs leave under a campaign directory."""
from __future__ import annotations

import json

from replay_bench import records
from test_replay_bench_summary import record


class TestWriteRun:
    def test_a_rerun_with_no_diff_removes_the_earlier_diff(self, tmp_path):
        first_attempt = record("t1", "toolkit")
        records.write_run(tmp_path, first_attempt, "diff --git a/x b/x\n")
        stale_diff = records.diff_path(tmp_path, "t1", "toolkit", 0)
        assert stale_diff.exists()

        records.write_run(tmp_path, record("t1", "toolkit", ok=False, reason="launch-error"), None)

        assert not stale_diff.exists()
        assert records.record_path(tmp_path, "t1", "toolkit", 0).exists()

    def test_a_rerun_with_no_diff_keeps_the_diff_of_a_sibling_run(self, tmp_path):
        records.write_run(tmp_path, record("t1", "bare"), "diff --git a/y b/y\n")

        records.write_run(tmp_path, record("t1", "toolkit"), None)

        assert records.diff_path(tmp_path, "t1", "bare", 0).exists()

    def test_a_run_with_a_diff_writes_both_files(self, tmp_path):
        records.write_run(tmp_path, record("t1", "toolkit"), "diff --git a/x b/x\n")

        assert records.diff_path(tmp_path, "t1", "toolkit", 0).read_text(encoding="utf-8") == "diff --git a/x b/x\n"
        assert records.read_block_records(tmp_path, "t1") == [record("t1", "toolkit")]


class TestReadingARecordWrittenBeforeNewFieldsExisted:
    def test_a_record_without_the_runner_and_empty_text_fields_reads_with_their_defaults(self, tmp_path):
        records.write_run(tmp_path, record("t1", "toolkit"), None)
        path = records.record_path(tmp_path, "t1", "toolkit", 0)
        stored = json.loads(path.read_text(encoding="utf-8"))
        for key in ("runner_commit", "runner_dirty", "empty_final_text"):
            del stored[key]
        path.write_text(json.dumps(stored), encoding="utf-8")

        (read_back,) = records.read_block_records(tmp_path, "t1")

        assert (read_back.runner_commit, read_back.runner_dirty, read_back.empty_final_text) == (None, None, False)
