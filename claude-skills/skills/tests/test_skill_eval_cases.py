"""The trigger-case files' shape and the disposition-fidelity case validator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import REPO_ROOT

_LEGACY_TRIGGER_METHODS = {"runtime", "description-fidelity", "behavioral-dispatch"}


def _validate_disposition_fidelity_case(case: dict, prefix: str, repo_root: Path) -> None:
    """Field validation for a single `method: "disposition-fidelity"` case.

    Extracted from test_trigger_cases_files_well_formed's discovery loop so it
    is unit-testable against synthetic cases (see TestValidateDispositionFidelityCase)
    without waiting for a real *-cases.json fixture to land in the repo.
    """
    scenario_file = case.get("scenario_file")
    assert isinstance(scenario_file, str) and scenario_file, f"{prefix}: 'scenario_file' must be a non-empty string"
    assert (repo_root / scenario_file).exists(), (
        f"{prefix}: scenario_file {scenario_file!r} does not exist at {repo_root / scenario_file}"
    )
    assert isinstance(case.get("rule_anchor"), str) and case["rule_anchor"], (
        f"{prefix}: 'rule_anchor' must be a non-empty string"
    )
    assert isinstance(case.get("judge_rubric"), str) and case["judge_rubric"], (
        f"{prefix}: 'judge_rubric' must be a non-empty string"
    )


def test_trigger_cases_files_well_formed() -> None:
    """Every *-cases.json file found under skills/ or plugins/ must be valid.

    Discovery-based: no hardcoded skill list. Validates shape only — never
    invokes a model. Auto-extends as case files are added in follow-up PRs.
    CI-safe (pure static check). Field validation branches on `method`: the
    three legacy trigger/no-trigger methods validate query/should_trigger
    fields; disposition-fidelity validates its own
    scenario_file/rule_anchor/judge_rubric fields. Every method in
    run_skill_evals.VALID_METHODS must be handled explicitly here — an
    unrecognized method fails loudly rather than silently skipping validation.
    """
    repo_root = REPO_ROOT
    found_files: list[Path] = []
    for base in [
        repo_root / "claude-skills" / "skills",
        repo_root / "plugins",
    ]:
        for p in base.glob("*/evals/*-cases.json"):
            found_files.append(p)
        for p in base.glob("*/skills/*/evals/*-cases.json"):
            found_files.append(p)

    assert found_files, "No *-cases.json files found — run the pilot skills setup first"

    for path in found_files:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}: invalid JSON — {exc}") from exc

        assert "skill_name" in data, f"{path}: missing 'skill_name'"
        assert "cases" in data, f"{path}: missing 'cases'"
        assert isinstance(data["cases"], list) and data["cases"], f"{path}: 'cases' must be a non-empty list"

        method = data.get("method")

        # skill_name must match the parent skill directory name
        skill_dir_name = path.parts[-3]  # …/skills/<name>/evals/<name>-cases.json
        assert data["skill_name"] == skill_dir_name, (
            f"{path}: skill_name={data['skill_name']!r} does not match parent dir {skill_dir_name!r}"
        )

        ids_seen: set[str] = set()
        for i, case in enumerate(data["cases"]):
            prefix = f"{path} case[{i}]"
            assert isinstance(case.get("id"), str) and case["id"], f"{prefix}: 'id' must be a non-empty string"
            assert case["id"] not in ids_seen, f"{prefix}: duplicate id {case['id']!r}"
            ids_seen.add(case["id"])

            if method in _LEGACY_TRIGGER_METHODS:
                assert isinstance(case.get("query"), str) and case["query"], (
                    f"{prefix}: 'query' must be a non-empty string"
                )
                assert isinstance(case.get("should_trigger"), bool), f"{prefix}: 'should_trigger' must be a boolean"
                if "also_not_triggered" in case:
                    ant = case["also_not_triggered"]
                    assert isinstance(ant, list) and all(isinstance(s, str) for s in ant), (
                        f"{prefix}: 'also_not_triggered' must be a list of strings"
                    )
                    assert data["skill_name"] not in ant, (
                        f"{prefix}: 'also_not_triggered' must not contain the skill's own name "
                        f"({data['skill_name']!r}) — a skill cannot be a misfire of itself"
                    )
            elif method == "disposition-fidelity":
                _validate_disposition_fidelity_case(case, prefix, repo_root)
            else:
                raise AssertionError(f"{path}: unrecognized method {method!r} — add a validation branch here")


class TestValidateDispositionFidelityCase:
    """Synthetic-case coverage for _validate_disposition_fidelity_case.

    No real *-cases.json with method "disposition-fidelity" exists in the
    repo yet (that method ships with zero active cases — see
    evals/README.md), so without this class the branch is unexercised until
    the first real fixture lands. Uses an existing repo file as a stand-in
    scenario_file — the validator only checks existence, not content shape.
    """

    _REPO_ROOT = REPO_ROOT
    _EXISTING_SCENARIO_FILE = "evals/fixtures/dispatch-session-handoff.md"

    def test_valid_case_passes(self) -> None:
        case = {
            "scenario_file": self._EXISTING_SCENARIO_FILE,
            "rule_anchor": "some-anchor",
            "judge_rubric": "some rubric",
        }
        _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)  # must not raise

    def test_missing_scenario_file_key_raises(self) -> None:
        case = {"rule_anchor": "some-anchor", "judge_rubric": "some rubric"}
        with pytest.raises(AssertionError, match="scenario_file"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)

    def test_nonexistent_scenario_file_raises(self) -> None:
        case = {
            "scenario_file": "evals/fixtures/does-not-exist-xyz.md",
            "rule_anchor": "some-anchor",
            "judge_rubric": "some rubric",
        }
        with pytest.raises(AssertionError, match="does not exist"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)

    def test_empty_rule_anchor_raises(self) -> None:
        case = {"scenario_file": self._EXISTING_SCENARIO_FILE, "rule_anchor": "", "judge_rubric": "some rubric"}
        with pytest.raises(AssertionError, match="rule_anchor"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)

    def test_missing_judge_rubric_key_raises(self) -> None:
        case = {"scenario_file": self._EXISTING_SCENARIO_FILE, "rule_anchor": "some-anchor"}
        with pytest.raises(AssertionError, match="judge_rubric"):
            _validate_disposition_fidelity_case(case, "prefix", self._REPO_ROOT)
