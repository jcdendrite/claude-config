"""Unit tests for the offline-testable parts of evals/run_skill_evals.py.

Covers the runtime-mode stream-json trigger detector, the case-file `method`
schema, and the description-fidelity classification parser / scorer. All
deterministic — no claude -p call, CI-safe. Runtime-mode detector tests feed
committed synthetic stream-json fixtures from evals/fixtures/.

pyproject.toml adds 'evals' to the pytest pythonpath so `import run_skill_evals`
resolves correctly.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from run_skill_evals import (
    detect_trigger_in_lines,
    load_case_file,
    parse_classification_answer,
    parse_skill_frontmatter,
    partition_case_files,
    score_classification,
    seed_temp_project_git,
)

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "evals" / "fixtures"


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
        """partition_case_files separates runtime files from description-fidelity files."""
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
            runtime_files, description_fidelity_files = partition_case_files([runtime, descfid])
            assert runtime_files == [runtime]
            assert description_fidelity_files == [descfid]


class TestParseSkillFrontmatter:
    """assemble_skill_listing() relies on this minimal stdlib frontmatter parser."""

    @staticmethod
    def _write_skill(directory: Path, name: str, body: str) -> Path:
        skill_dir = directory / name
        skill_dir.mkdir()
        path = skill_dir / "SKILL.md"
        path.write_text(body)
        return path

    def test_inline_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            path = self._write_skill(
                Path(tmp_str), "my-skill",
                "---\nname: my-skill\ndescription: A one-line description.\n---\nbody\n",
            )
            name, desc = parse_skill_frontmatter(path)
            assert name == "my-skill"
            assert desc == "A one-line description."

    def test_folded_block_description_stops_at_next_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            path = self._write_skill(
                Path(tmp_str), "folded-skill",
                "---\nname: folded-skill\ndescription: >\n  First line of the\n"
                "  folded description.\nuser-invocable: false\n---\nbody\n",
            )
            name, desc = parse_skill_frontmatter(path)
            assert name == "folded-skill"
            assert desc == "First line of the folded description."
            assert "user-invocable" not in desc

    def test_missing_frontmatter_falls_back_to_dir_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            path = self._write_skill(Path(tmp_str), "bare-skill", "no frontmatter here\n")
            name, desc = parse_skill_frontmatter(path)
            assert name == "bare-skill"
            assert desc == ""


VALID_NAMES = frozenset({"code-review", "test-conventions", "test-evaluation", "plan-it"})


class TestParseClassificationAnswer:
    """Offline tests for the description-fidelity classifier-answer parser."""

    def test_bare_skill_name(self) -> None:
        assert parse_classification_answer("code-review", VALID_NAMES) == "code-review"

    def test_bare_skill_name_with_whitespace(self) -> None:
        assert parse_classification_answer("  test-conventions\n", VALID_NAMES) == "test-conventions"

    def test_literal_none(self) -> None:
        assert parse_classification_answer("none\n", VALID_NAMES) is None

    def test_empty_output(self) -> None:
        assert parse_classification_answer("", VALID_NAMES) is None

    def test_unknown_word(self) -> None:
        assert parse_classification_answer("refactoring", VALID_NAMES) is None

    def test_quoted_name(self) -> None:
        assert parse_classification_answer('"test-evaluation"', VALID_NAMES) == "test-evaluation"

    def test_prose_wrapped_name(self) -> None:
        """Best-effort fallback when the model ignores the one-line constraint."""
        answer = "I would use the code-review skill for this request."
        assert parse_classification_answer(answer, VALID_NAMES) == "code-review"


class TestScoreClassification:
    """description-fidelity scoring maps a named skill to the (fired, also_fired) shape."""

    def test_target_skill_named(self) -> None:
        fired, also = score_classification("test-conventions", "test-conventions", ["test-evaluation"])
        assert fired == "test-conventions"
        assert also == []

    def test_guarded_adjacent_skill_named_is_violation(self) -> None:
        """A should_trigger:false-style case where the model names a guarded skill.

        test-evaluation is the target; the model instead names test-conventions,
        which the case guards via also_not_triggered. It must score as a
        violation (also_fired), exactly as a runtime misfire does — not a pass.
        """
        fired, also = score_classification("test-conventions", "test-evaluation", ["test-conventions"])
        assert fired is None
        assert also == ["test-conventions"]

    def test_unrelated_skill_named_is_no_violation(self) -> None:
        fired, also = score_classification("plan-it", "test-evaluation", ["test-conventions"])
        assert fired is None
        assert also == []

    def test_none_named(self) -> None:
        fired, also = score_classification(None, "code-review", ["plan-review"])
        assert fired is None
        assert also == []
