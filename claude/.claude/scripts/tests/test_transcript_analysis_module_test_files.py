"""Pins that every transcript_analysis module has a test file named for it, or a MODULES_TESTED_ELSEWHERE entry naming the
file that tests it. The match is by file name only and does not show that the file covers the module."""
from __future__ import annotations

import pytest
from helpers import REPO_ROOT

PACKAGE_DIR = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis"
TESTS_DIR = REPO_ROOT / "claude" / ".claude" / "scripts" / "tests"

# Modules whose tests live in a file named for something else, mapped to that file.
MODULES_TESTED_ELSEWHERE = {
    "author_outcome": "test_author_outcome.py",
    "cache_rebuild_rules": "test_transcript_cache_rebuild_ttl_rules.py",
    "sessions": "test_transcript_analysis_multi_root_scope.py",
}


def _package_module_names() -> list[str]:
    return sorted(p.stem for p in PACKAGE_DIR.glob("*.py") if p.stem != "__init__")


def _transcript_test_stems() -> list[str]:
    return sorted(p.stem for p in TESTS_DIR.glob("test_transcript_*.py"))


def _test_file_names() -> list[str]:
    return sorted(p.name for p in TESTS_DIR.iterdir() if p.is_file())


def _owning_module(test_stem: str, module_names: list[str]) -> str | None:
    matching_modules = [
        module
        for module in module_names
        if test_stem == f"test_transcript_{module}" or test_stem.startswith(f"test_transcript_{module}_")
    ]
    return max(matching_modules, key=len, default=None)


def _owned_modules(test_stems: list[str], module_names: list[str]) -> set[str]:
    owners = (_owning_module(test_stem, module_names) for test_stem in test_stems)
    return {owner for owner in owners if owner is not None}


def _unnamed_module_violations(module_names: list[str], test_stems: list[str], entries: dict[str, str]) -> list[str]:
    owned = _owned_modules(test_stems, module_names)
    return [
        f"transcript_analysis/{module}.py has no test file named test_transcript_{module}[_<seam>].py and no "
        f"MODULES_TESTED_ELSEWHERE entry -- add a test file named for it, or map the module to the file that tests it"
        for module in module_names
        if module not in owned and module not in entries
    ]


def _entry_violations(
    module_names: list[str], test_stems: list[str], entries: dict[str, str], test_file_names: list[str]
) -> list[str]:
    owned = _owned_modules(test_stems, module_names)
    violations = []
    for module, test_file_name in entries.items():
        if module not in module_names:
            violations.append(f"MODULES_TESTED_ELSEWHERE key {module!r} is not a transcript_analysis module -- remove it")
        if module in owned:
            violations.append(
                f"MODULES_TESTED_ELSEWHERE key {module!r} already has a test file named for it -- remove the entry"
            )
        if test_file_name not in test_file_names:
            violations.append(
                f"MODULES_TESTED_ELSEWHERE value {test_file_name!r} (for {module!r}) is not a file in {TESTS_DIR}"
            )
    return violations


def test_every_package_module_has_a_named_test_file_or_an_entry():
    violations = _unnamed_module_violations(_package_module_names(), _transcript_test_stems(), MODULES_TESTED_ELSEWHERE)
    assert violations == []


def test_every_entry_names_a_module_with_no_named_file_and_an_existing_test_file():
    violations = _entry_violations(
        _package_module_names(), _transcript_test_stems(), MODULES_TESTED_ELSEWHERE, _test_file_names()
    )
    assert violations == []


@pytest.mark.parametrize(
    "module_names",
    [["cost", "cost_ledger", "pr_cost"], ["cost_ledger", "pr_cost", "cost"]],
    ids=["shorter-name-first", "shorter-name-last"],
)
def test_owning_module_takes_the_longest_matching_module_name(module_names):
    assert _owning_module("test_transcript_cost_ledger_record_gates", module_names) == "cost_ledger"
    assert _owning_module("test_transcript_cost_counts", module_names) == "cost"
    assert _owning_module("test_transcript_pr_cost", module_names) == "pr_cost"
    assert _owning_module("test_transcript_costly", module_names) is None
    assert _owning_module("test_transcript_analysis_branch_attribution", module_names) is None


def test_package_directory_is_not_empty():
    """Guards the real-tree tests above against a tautological pass: an empty package listing or an empty owned set
    would leave them comparing nothing to nothing."""
    module_names = _package_module_names()
    assert module_names, f"no modules found under {PACKAGE_DIR} -- PACKAGE_DIR is likely wrong"
    assert _owned_modules(_transcript_test_stems(), module_names), f"no test file under {TESTS_DIR} is named for a module"


def test_module_with_no_test_file_and_no_entry_is_reported():
    violations = _unnamed_module_violations(["alpha"], [], {})
    assert len(violations) == 1
    assert "alpha" in violations[0]


def test_module_whose_name_prefixes_an_owned_module_is_still_reported():
    violations = _unnamed_module_violations(["cost", "cost_ledger"], ["test_transcript_cost_ledger_record_gates"], {})
    assert len(violations) == 1
    assert "transcript_analysis/cost.py" in violations[0]
    assert "cost_ledger" not in violations[0]


def test_entry_whose_key_is_not_a_module_is_reported():
    violations = _entry_violations(
        ["alpha"],
        ["test_transcript_alpha"],
        {"beta": "test_beta_elsewhere.py"},
        ["test_transcript_alpha.py", "test_beta_elsewhere.py"],
    )
    assert len(violations) == 1
    assert "beta" in violations[0]


def test_entry_whose_key_is_owned_is_reported():
    violations = _entry_violations(
        ["alpha"],
        ["test_transcript_alpha"],
        {"alpha": "test_alpha_elsewhere.py"},
        ["test_transcript_alpha.py", "test_alpha_elsewhere.py"],
    )
    assert len(violations) == 1
    assert "alpha" in violations[0]


def test_entry_whose_test_file_is_missing_is_reported():
    violations = _entry_violations(["alpha"], [], {"alpha": "test_alpha_elsewhere.py"}, [])
    assert len(violations) == 1
    assert "test_alpha_elsewhere.py" in violations[0]
