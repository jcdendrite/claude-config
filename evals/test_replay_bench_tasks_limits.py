"""Tests for `build_task_fixture`'s optional settings file and for `SelectionLimits`' range checks. Kept apart from
`test_replay_bench_tasks.py`, which is already past the line cap. Every repository is synthetic, built under
`tmp_path` with real git. No test launches `claude`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from replay_bench import tasks
from test_replay_bench_tasks import BARE_WRITER_MARKDOWN, FIXTURE_TIMEOUT_S, _change, _git_text, _make_source_repo, _mine_one_task

SETTINGS_TEXT = '{"claudeMdExcludes": ["/synthetic/rules/a.md"]}'


def build_fixture_with(tmp_path: Path, settings_text: str | None) -> Path:
    repo = _make_source_repo(tmp_path)
    _change(repo)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    tasks.build_task_fixture(
        _mine_one_task(repo), fixture, tmp_path / "attempt" / "baseline.git", bare_writer_markdown=BARE_WRITER_MARKDOWN,
        timeout_s=FIXTURE_TIMEOUT_S, settings_text=settings_text,
    )
    return fixture


class TestFixtureSettingsFile:
    def test_settings_text_lands_byte_for_byte_and_stays_out_of_the_work_tree_status(self, tmp_path):
        fixture = build_fixture_with(tmp_path, SETTINGS_TEXT)

        assert (fixture / ".claude" / "settings.json").read_bytes() == SETTINGS_TEXT.encode()
        assert _git_text(fixture, "status", "--porcelain") == ""

    def test_no_settings_file_exists_without_settings_text(self, tmp_path):
        fixture = build_fixture_with(tmp_path, None)

        assert not (fixture / ".claude" / "settings.json").exists()


class TestSelectionLimitsRanges:
    INTEGER_FIELDS = (
        "max_count", "max_tasks", "max_files", "min_code_lines", "max_code_lines", "max_prompt_chars", "max_base_mb",
        "max_base_entries",
    )

    @pytest.mark.parametrize("bad_value", [0, -1])
    @pytest.mark.parametrize("field_name", INTEGER_FIELDS)
    def test_an_integer_limit_below_one_is_refused_naming_its_flag(self, field_name, bad_value):
        with pytest.raises(tasks.TaskSourceError, match="--" + field_name.replace("_", "-")):
            tasks.SelectionLimits(**{field_name: bad_value})

    def test_equal_code_line_bounds_are_accepted(self):
        assert tasks.SelectionLimits(min_code_lines=7, max_code_lines=7).min_code_lines == 7

    def test_a_minimum_one_above_the_maximum_is_refused_naming_both_flags(self):
        with pytest.raises(tasks.TaskSourceError, match=r"--min-code-lines.*--max-code-lines"):
            tasks.SelectionLimits(min_code_lines=11, max_code_lines=10)

    def test_a_maximum_below_the_default_minimum_is_refused(self):
        with pytest.raises(tasks.TaskSourceError, match="--max-code-lines"):
            tasks.SelectionLimits(max_code_lines=5)
