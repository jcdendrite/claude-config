"""Unit tests for the offline-testable parts of evals/run_skill_evals.py.

Covers the runtime-mode stream-json trigger detector, the case-file `method`
schema, the description-fidelity classification parser / scorer, and the
behavioral-dispatch command builder. All
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
    _build_dispatch_command,
    detect_dispatch_in_lines,
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
            for method in ("runtime", "description-fidelity", "behavioral-dispatch"):
                path = self._write_case_file(
                    Path(tmp_str),
                    f"{method}.json",
                    {"skill_name": "x", "method": method, "cases": []},
                )
                assert load_case_file(path)["method"] == method

    def test_partition_splits_by_method(self) -> None:
        """partition_case_files groups files by method string into a dict."""
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
            dispatch = self._write_case_file(
                tmp, "dispatch.json",
                {"skill_name": "bd", "method": "behavioral-dispatch", "cases": []},
            )
            result = partition_case_files([runtime, descfid, dispatch])
            assert result["runtime"] == [runtime]
            assert result["description-fidelity"] == [descfid]
            assert result["behavioral-dispatch"] == [dispatch]


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


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURES_DIR


class TestDetectDispatch:
    """Offline tests for the behavioral-dispatch dispatch-tool detector.

    Claude Code >=2.1.191 emits "Agent" for subagent dispatch; earlier versions
    emitted "Task". Both names are in DISPATCH_TOOL_NAMES; tests cover both.
    """

    def test_detects_dispatch_tool_from_fixture(self, fixture_dir: Path) -> None:
        """Fixture with Agent tool_use call → detector returns True."""
        lines = (fixture_dir / "dispatch-fired-explore.jsonl").read_text().splitlines()
        assert detect_dispatch_in_lines(lines) is True

    def test_no_dispatch_on_read_bash(self, fixture_dir: Path) -> None:
        """Fixture with only Read and Bash calls → detector returns False."""
        lines = (fixture_dir / "no-dispatch-inline.jsonl").read_text().splitlines()
        assert detect_dispatch_in_lines(lines) is False

    def test_empty_lines_returns_false(self) -> None:
        assert detect_dispatch_in_lines([]) is False

    def test_content_block_delta_does_not_fire(self) -> None:
        """content_block_delta events must not be mistaken for a Task dispatch."""
        # Guard that fires: evt.get("type") != "content_block_start" short-circuits
        # before any content_block inspection — the embedded "Task" string in
        # partial_json is irrelevant to the rejection.
        delta_event = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"name": "Task"}'},
            },
        })
        assert detect_dispatch_in_lines([delta_event]) is False

    def test_agent_in_content_block_delta_does_not_fire(self) -> None:
        """'Agent' embedded in a content_block_delta partial_json must not fire."""
        delta_event = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"name": "Agent"}'},
            },
        })
        assert detect_dispatch_in_lines([delta_event]) is False

    def test_skill_tool_use_does_not_fire(self) -> None:
        """A Skill tool_use must not register as a Task dispatch."""
        lines = [
            '{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_X","name":"Skill","input":{}}}}'
        ]
        assert detect_dispatch_in_lines(lines) is False

    def test_content_block_start_text_type_does_not_fire(self) -> None:
        """content_block_start with content_block.type 'text' (not 'tool_use') must not fire.

        Exercises the block.get("type") == "tool_use" guard specifically — distinct
        from the event-type check that test_content_block_delta_does_not_fire covers.
        """
        event = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": "Task"},
            },
        })
        assert detect_dispatch_in_lines([event]) is False

    def test_non_json_line_before_task_does_not_prevent_detection(self) -> None:
        """A malformed line mid-stream must not swallow a valid Task event that follows."""
        task_line = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_X", "name": "Task", "input": {}},
            },
        })
        assert detect_dispatch_in_lines(["not valid json", task_line]) is True

    def test_non_json_line_before_agent_does_not_prevent_detection(self) -> None:
        """A malformed line mid-stream must not swallow a valid Agent event that follows."""
        agent_line = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_X", "name": "Agent", "input": {}},
            },
        })
        assert detect_dispatch_in_lines(["not valid json", agent_line]) is True

    def test_detects_agent_tool_use(self) -> None:
        """Agent tool_use (Claude Code >=2.1.191 dispatch name) → detector returns True."""
        agent_line = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_X", "name": "Agent", "input": {}},
            },
        })
        assert detect_dispatch_in_lines([agent_line]) is True

    def test_detects_task_tool_use(self) -> None:
        """Task tool_use (backward-compat name for Claude Code <2.1.191) → detector returns True."""
        task_line = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_X", "name": "Task", "input": {}},
            },
        })
        assert detect_dispatch_in_lines([task_line]) is True

    def test_bytes_input_detects_dispatch_tool(self, fixture_dir: Path) -> None:
        """detect_dispatch_in_lines accepts bytes as emitted by detect_dispatch_in_stream."""
        raw = (fixture_dir / "dispatch-fired-explore.jsonl").read_bytes()
        lines = [line for line in raw.split(b"\n") if line.strip()]
        assert detect_dispatch_in_lines(lines) is True


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


class TestBuildDispatchCommand:
    """Verify that _build_dispatch_command produces the correct subprocess argument list."""

    def test_full_command_list(self) -> None:
        cmd = _build_dispatch_command("my query", "handoff text", "claude-sonnet-4-6")
        assert cmd == [
            "claude", "-p", "my query",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", "claude-sonnet-4-6",
            "--append-system-prompt", "handoff text",
        ]

    def test_empty_handoff_passes_through(self) -> None:
        # _build_dispatch_command has no validation; empty handoff is a caller concern.
        # Fixture guard (test_handoff_fixture_exists_and_nonempty) catches this in CI.
        cmd = _build_dispatch_command("q", "", "m")
        assert cmd == [
            "claude", "-p", "q",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", "m",
            "--append-system-prompt", "",
        ]

    def test_handoff_fixture_exists_and_nonempty(self) -> None:
        handoff_path = FIXTURES_DIR / "dispatch-session-handoff.md"
        assert handoff_path.exists(), f"Handoff fixture not found at {handoff_path}"
        assert handoff_path.read_text().strip(), "Handoff fixture is empty"

    def test_warm_dispatch_command_shape(self) -> None:
        """Warm mode uses --resume + --fork-session; exact list matches production path."""
        cmd = _build_dispatch_command("q", "handoff", "model-x", warm=True, session_id="abc-123")
        assert cmd == [
            "claude", "-p", "q",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", "model-x",
            "--resume", "abc-123",
            "--fork-session",
        ]

    def test_warm_dispatch_none_session_falls_through_to_cold(self) -> None:
        """warm=True with session_id=None silently falls back to cold path (--append-system-prompt).

        This pins the deliberate fallback: a priming failure that returns None causes samples
        to run cold rather than crashing. Callers must check that primed_session_id is not None
        before warm runs are meaningful.
        """
        cmd = _build_dispatch_command("q", "handoff", "model-x", warm=True, session_id=None)
        assert cmd == [
            "claude", "-p", "q",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", "model-x",
            "--append-system-prompt", "handoff",
        ]

    def test_cold_dispatch_command_shape_unchanged(self) -> None:
        """Cold mode (warm=False) keeps the original --append-system-prompt shape."""
        cmd = _build_dispatch_command("q", "handoff", "model-x", warm=False)
        assert cmd == [
            "claude", "-p", "q",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", "model-x",
            "--append-system-prompt", "handoff",
        ]

    def test_priming_prompt_fixture_exists_and_nonempty(self) -> None:
        """The warm-dispatch priming prompt fixture must exist and contain the forbid-delegation instruction."""
        priming_path = FIXTURES_DIR / "dispatch-priming-prompt.md"
        assert priming_path.exists(), f"Priming prompt fixture not found at {priming_path}"
        content = priming_path.read_text()
        assert content.strip(), "Priming prompt fixture is empty"
        # Delegation must be forbidden: the priming run must fill the parent's context,
        # not a subagent's. A delegating priming turn defeats the warming purpose.
        assert "Do NOT spawn" in content or "do not spawn" in content.lower(), (
            "Priming prompt must forbid delegation (no Agent/Task); "
            "a priming turn that delegates fills a subagent's context, not the parent's"
        )
