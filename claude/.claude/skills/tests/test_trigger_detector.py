"""Unit tests for the stream-json trigger detector in evals/run_trigger_evals.py.

Feeds committed fixture files (synthetic, hand-authored stream-json fixtures) to
detect_trigger_in_lines() and asserts the correct fired-skill name.
Deterministic, no claude -p call — CI-safe.

The fixtures live in evals/fixtures/. pyproject.toml adds 'evals' to the
pytest pythonpath so `import run_trigger_evals` resolves correctly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from run_trigger_evals import (
    detect_trigger_in_lines,
    format_skip_notice,
    load_case_file,
    partition_case_files,
    seed_temp_project_git,
)

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "evals" / "fixtures"
HARNESS = Path(__file__).resolve().parents[4] / "evals" / "run_trigger_evals.py"


def _lines(fixture_name: str) -> list[str]:
    path = FIXTURES_DIR / fixture_name
    return path.read_text().splitlines()  # each fixture is a synthetic hand-authored JSONL file


class TestDetectTriggerInLines:
    def test_skill_fired_code_review(self) -> None:
        """Fixture with Skill(code-review) call → detector returns 'code-review'."""
        lines = _lines("skill-fired-code-review.jsonl")
        fired, also = detect_trigger_in_lines(lines, "code-review", [])
        assert fired == "code-review"
        assert also == []

    def test_no_trigger_typo(self) -> None:
        """Fixture with no Skill call → detector returns None."""
        lines = _lines("no-trigger-typo.jsonl")
        fired, also = detect_trigger_in_lines(lines, "code-review", [])
        assert fired is None
        assert also == []

    def test_misfire_also_not_detected(self) -> None:
        """Fixture where plan-review fires unexpectedly → also_fired contains 'plan-review'."""
        lines = _lines("misfire-plan-review-instead-of-code-review.jsonl")
        fired, also = detect_trigger_in_lines(lines, "code-review", ["plan-review"])
        assert fired is None  # code-review didn't fire
        assert "plan-review" in also  # plan-review did fire (the misfire)

    def test_wrong_skill_name_no_match(self) -> None:
        """Skill fired fixture for code-review → no match when asking for 'plan-it'."""
        lines = _lines("skill-fired-code-review.jsonl")
        fired, also = detect_trigger_in_lines(lines, "plan-it", [])
        assert fired is None

    def test_empty_lines_returns_none(self) -> None:
        fired, also = detect_trigger_in_lines([], "code-review", [])
        assert fired is None
        assert also == []

    def test_read_of_skill_named_path_no_match(self) -> None:
        """Read tool_use whose path contains 'code-review' must NOT count as a skill fire.

        Before removing Read from TRIGGER_TOOL_NAMES this test would have returned
        ('code-review', []) — a false positive. Regression guard for that bug.
        """
        lines = _lines("no-trigger-read-skillmd.jsonl")
        fired, also = detect_trigger_in_lines(lines, "code-review", [])
        assert fired is None
        assert also == []


class TestSeedTempProjectGit:
    def test_calculator_staged_not_committed(self) -> None:
        """calculator.py must be staged (M ) — not unstaged ( M) — after seeding.

        A silently mis-applied patch yields an empty diff, which would make every
        code-review and test-evaluation sample fail for a reason unrelated to skill
        triggering — the exact false signal the fixture fix exists to kill.
        """
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            seed_temp_project_git(tmp)
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=True,
            )
            lines = result.stdout.splitlines()
            assert any(
                line.startswith("M ") and "calculator.py" in line for line in lines
            ), f"calculator.py should be staged (M ), got: {result.stdout!r}"
            assert not any(
                line.startswith(" M") and "calculator.py" in line for line in lines
            ), f"calculator.py should not appear unstaged ( M), got: {result.stdout!r}"


class TestCaseFileMethod:
    """The `method` field gates which harness measures a skill."""

    @staticmethod
    def _write_case_file(directory: Path, name: str, payload: dict) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload))
        return path

    def test_load_case_file_rejects_missing_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            path = self._write_case_file(
                Path(tmp_str), "missing.json", {"skill_name": "x", "cases": []}
            )
            with pytest.raises(ValueError, match="method"):
                load_case_file(path)

    def test_load_case_file_rejects_unknown_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            path = self._write_case_file(
                Path(tmp_str),
                "unknown.json",
                {"skill_name": "x", "method": "guesswork", "cases": []},
            )
            with pytest.raises(ValueError, match="method"):
                load_case_file(path)

    def test_load_case_file_accepts_valid_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            for method in ("runtime", "description-fidelity"):
                path = self._write_case_file(
                    Path(tmp_str),
                    f"{method}.json",
                    {"skill_name": "x", "method": method, "cases": []},
                )
                assert load_case_file(path)["method"] == method

    def test_partition_splits_by_method(self) -> None:
        """Runtime files run; description-fidelity files are reported as skipped."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            runtime = self._write_case_file(
                tmp, "runtime.json",
                {"skill_name": "rt", "method": "runtime", "cases": []},
            )
            descfid = self._write_case_file(
                tmp, "descfid.json",
                {"skill_name": "df", "method": "description-fidelity", "cases": []},
            )
            runtime_files, skipped = partition_case_files([runtime, descfid])
            assert runtime_files == [runtime]
            assert skipped == [("df", "description-fidelity")]

    def test_format_skip_notice_names_skills(self) -> None:
        notice = format_skip_notice(
            [
                ("test-evaluation", "description-fidelity"),
                ("test-conventions", "description-fidelity"),
            ]
        )
        assert "test-conventions" in notice
        assert "test-evaluation" in notice
        assert "README" in notice


class TestSkipNoticeWiring:
    def test_description_fidelity_skill_skipped_with_notice(self) -> None:
        """`--skill` on a description-fidelity skill prints the skip notice, exits 0.

        test-conventions declares method=description-fidelity, so the runtime
        harness runs no samples — this exercises the skip path end-to-end
        without spawning claude -p.
        """
        result = subprocess.run(
            [sys.executable, str(HARNESS), "--skill", "test-conventions"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Skipped" in result.stdout
        assert "test-conventions" in result.stdout
