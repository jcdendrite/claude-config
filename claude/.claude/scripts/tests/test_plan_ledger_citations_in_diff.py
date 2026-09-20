"""Backstop for plan-review Step 3's ledger-citation check: every plan file the
current branch changes must have no orphaned ledger citation, so a Step 3 that
was abbreviated in-session is still caught at push time.

Scoped to the diff on purpose. The committed plan corpus carries pre-existing
orphans that this check is not meant to reopen, and it never reads a plan file
outside the diff.

Behaviors a reader of a green or red run should know:

- It checks the plan files a diff against `origin/main` changes, whether
  committed, dirty, or untracked. On a push to main that diff is empty, so it
  skips.
- A plan file the branch modifies must itself be free of orphans, including a
  committed plan that carries pre-existing ones. The fix is resolving the
  citations. Committed plans are historical records and are meant to be left
  unedited.
- It needs `origin/main`, so it depends on the workflow's full-depth checkout.
  A missing ref fails rather than skips. That includes the full-suite fallback
  `select-tests.py` takes when git is unavailable.
- A path git quotes, such as a non-ASCII plan filename, is silently skipped.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from helpers import REPO_ROOT

PLANS_DIR = REPO_ROOT / ".claude" / "plans"

_SCRIPTS_DIR = Path(__file__).parent.parent


def _load_script(filename: str, module_name: str):
    script_path = _SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(script_path.parent))
    spec.loader.exec_module(module)
    return module


_select_tests = _load_script("select-tests.py", "select_tests")
_check_ledger_citations = _load_script("check-ledger-citations.py", "check_ledger_citations")


def _changed_plan_files() -> list[Path]:
    """Existing .md files under PLANS_DIR that this branch changed, or a skip
    when it changed none. Fails, rather than skips, when git cannot say what
    changed: a backstop that silently stops running is worse than none."""
    try:
        changed_paths = _select_tests.compute_changed_paths(REPO_ROOT)
    except _select_tests.GitDiffUnavailable as exc:
        pytest.fail(f"cannot determine which plan files this branch changed: {exc}", pytrace=False)
    plans_prefix = PLANS_DIR.relative_to(REPO_ROOT).as_posix() + "/"
    plan_files = [
        REPO_ROOT / path
        for path in changed_paths
        if path.startswith(plans_prefix) and path.endswith(".md") and (REPO_ROOT / path).is_file()
    ]
    if not plan_files:
        pytest.skip("no .claude/plans/*.md in the current diff")
    return plan_files


def _orphan_messages(plan_files: list[Path]) -> list[str]:
    """One `file:line: citation 'token' ...` message per orphaned citation."""
    return [
        f"{plan_file.relative_to(REPO_ROOT)}:{line}: citation '{token}' resolves to no defined ledger label"
        for plan_file in plan_files
        for token, line in _check_ledger_citations.find_orphan_citations(
            plan_file.read_text(encoding="utf-8")
        )
    ]


def test_changed_plan_files_have_no_orphan_ledger_citations():
    orphans = _orphan_messages(_changed_plan_files())
    assert not orphans, "\n".join(orphans)


class TestOrphanMessages:
    """Pins how find_orphan_citations output becomes the assertion message."""

    @pytest.fixture(autouse=True)
    def _repo_root_is_tmp_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    def test_plan_with_an_orphan_yields_file_line_and_token_in_the_message(self, tmp_path):
        plan_file = tmp_path / "plan-with-orphan.md"
        plan_file.write_text("# Plan\n\n## Ledger\n\nanchors: G9\n", encoding="utf-8")

        assert _orphan_messages([plan_file]) == [
            "plan-with-orphan.md:5: citation 'g9' resolves to no defined ledger label"
        ]

    def test_plan_whose_citations_all_resolve_yields_no_message(self, tmp_path):
        plan_file = tmp_path / "clean-plan.md"
        plan_file.write_text(
            "## Ledger\nanchors: root\n\nG1. a claim\n\n## Rows\nanchors: G1\n", encoding="utf-8"
        )

        assert _orphan_messages([plan_file]) == []


def _plans_dir_under(tmp_path: Path, monkeypatch) -> Path:
    """A plans directory under tmp_path, with this module's REPO_ROOT and PLANS_DIR
    pointed at it so nothing is read from or written into the real repo."""
    plans_dir = tmp_path / ".claude" / "plans"
    plans_dir.mkdir(parents=True)
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "PLANS_DIR", plans_dir)
    return plans_dir


class TestChangedPlanFilesSelection:
    """Pins the two outcomes for the two different conditions: git unavailable
    fails, and no changed plan file skips."""

    def test_git_diff_unavailable_fails_with_the_exceptions_own_message(self, monkeypatch):
        def raise_git_diff_unavailable(repo_root):
            raise _select_tests.GitDiffUnavailable("could not resolve merge-base against origin/main")

        monkeypatch.setattr(_select_tests, "compute_changed_paths", raise_git_diff_unavailable)
        with pytest.raises(pytest.fail.Exception, match="could not resolve merge-base against origin/main"):
            _changed_plan_files()

    def test_diff_with_no_plan_file_skips(self, monkeypatch):
        monkeypatch.setattr(_select_tests, "compute_changed_paths", lambda repo_root: ["README.md"])
        with pytest.raises(pytest.skip.Exception, match="no .claude/plans/"):
            _changed_plan_files()

    def test_deleted_plan_file_in_the_diff_is_not_returned(self, monkeypatch):
        deleted_plan = PLANS_DIR.relative_to(REPO_ROOT) / "plan-deleted-on-this-branch.md"
        monkeypatch.setattr(_select_tests, "compute_changed_paths", lambda repo_root: [deleted_plan.as_posix()])
        with pytest.raises(pytest.skip.Exception):
            _changed_plan_files()

    def test_existing_plan_file_in_the_diff_is_returned(self, monkeypatch, tmp_path):
        plans_dir = _plans_dir_under(tmp_path, monkeypatch)
        existing_plan = plans_dir / "existing-plan.md"
        existing_plan.write_text("# Plan\n", encoding="utf-8")
        monkeypatch.setattr(
            _select_tests, "compute_changed_paths", lambda repo_root: [".claude/plans/existing-plan.md"]
        )

        assert _changed_plan_files() == [existing_plan]

    def test_existing_non_markdown_file_under_plans_is_excluded_and_markdown_sibling_returned(
        self, monkeypatch, tmp_path
    ):
        plans_dir = _plans_dir_under(tmp_path, monkeypatch)
        markdown_plan = plans_dir / "plan.md"
        markdown_plan.write_text("# Plan\n", encoding="utf-8")
        (plans_dir / "attachment.png").write_bytes(b"\x89PNG\xff\xfe")
        monkeypatch.setattr(
            _select_tests,
            "compute_changed_paths",
            lambda repo_root: [".claude/plans/attachment.png", ".claude/plans/plan.md"],
        )

        assert _changed_plan_files() == [markdown_plan]
