"""Unit tests for the stream-json trigger detector in evals/run_trigger_evals.py.

Feeds committed fixture files (synthetic, hand-authored stream-json fixtures) to
detect_trigger_in_lines() and asserts the correct fired-skill name.
Deterministic, no claude -p call — CI-safe.

The fixtures live in evals/fixtures/. pyproject.toml adds 'evals' to the
pytest pythonpath so `import run_trigger_evals` resolves correctly.
"""

from __future__ import annotations

from pathlib import Path

from run_trigger_evals import detect_trigger_in_lines

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
