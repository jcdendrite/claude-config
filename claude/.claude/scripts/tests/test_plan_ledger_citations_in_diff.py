"""Backstop for plan-review Step 3's ledger-citation check: every plan file the
current branch changes must have no orphaned ledger citation, so a Step 3 that
was abbreviated in-session is still caught by a `pull_request` CI run or a
local full-suite run.

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
  A missing ref fails rather than skips. When `select-tests.py` falls back to
  the full suite because git is unavailable, this test runs in that suite and
  fails.
- A changed path that git quotes (one with non-ASCII bytes, a tab, a double
  quote, or a backslash) fails rather than skips, because it cannot be
  classified as a plan file.
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
        pytest.fail(
            f"cannot determine which plan files this branch changed: {exc}. "
            "This test needs origin/main, so the CI checkout must fetch the full history (`fetch-depth: 0`).",
            pytrace=False,
        )
    # An unquoted path never starts with a double quote, so a leading one marks a path git quoted.
    quoted_paths = [path for path in changed_paths if path.startswith('"')]
    if quoted_paths:
        pytest.fail(
            f"git quoted these changed paths, so they cannot be classified as plan files: {quoted_paths}. "
            "Rename the file to plain ASCII without tabs, quotes, or backslashes.",
            pytrace=False,
        )
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


def _failure_message_of_changed_plan_files() -> str:
    """The message of the failure `_changed_plan_files` raises. A skip or a
    normal return is itself a test failure, since pytest.raises would let a
    skip pass through as a green SKIPPED."""
    try:
        _changed_plan_files()
    except pytest.fail.Exception as exc:
        return str(exc)
    except pytest.skip.Exception:
        pytest.fail("_changed_plan_files skipped where it had to fail", pytrace=False)
    pytest.fail("_changed_plan_files returned where it had to fail", pytrace=False)


class TestChangedPlanFilesSelection:
    """Pins the outcomes for the different conditions: git unavailable or a
    git-quoted path fails, and no changed plan file skips."""

    def test_git_diff_unavailable_fails_with_the_exceptions_own_message(self, monkeypatch):
        def raise_git_diff_unavailable(repo_root):
            raise _select_tests.GitDiffUnavailable("could not resolve merge-base against origin/main")

        monkeypatch.setattr(_select_tests, "compute_changed_paths", raise_git_diff_unavailable)

        assert "could not resolve merge-base against origin/main" in _failure_message_of_changed_plan_files()

    def test_git_diff_unavailable_message_names_the_full_history_checkout_requirement(self, monkeypatch):
        def raise_git_diff_unavailable(repo_root):
            raise _select_tests.GitDiffUnavailable("could not resolve merge-base against origin/main")

        monkeypatch.setattr(_select_tests, "compute_changed_paths", raise_git_diff_unavailable)

        assert "fetch-depth: 0" in _failure_message_of_changed_plan_files()

    @pytest.mark.parametrize(
        "quoted_path",
        [r'".claude/plans/caf\303\251.md"', r'".claude/plans/tab\there.md"', r'".claude/plans/say \"hi\".md"'],
        ids=["non-ascii-bytes", "tab", "double-quote"],
    )
    def test_git_quoted_changed_path_fails_instead_of_being_skipped(self, monkeypatch, quoted_path):
        monkeypatch.setattr(_select_tests, "compute_changed_paths", lambda repo_root: [quoted_path])

        assert "git quoted these changed paths" in _failure_message_of_changed_plan_files()

    def test_git_quoted_changed_path_message_asks_for_a_plain_ascii_rename(self, monkeypatch):
        monkeypatch.setattr(
            _select_tests, "compute_changed_paths", lambda repo_root: [r'".claude/plans/caf\303\251.md"']
        )

        message = _failure_message_of_changed_plan_files()

        assert "Rename the file to plain ASCII without tabs, quotes, or backslashes." in message
        assert "core.quotePath" not in message

    def test_git_quoted_path_outside_the_plans_directory_also_fails(self, monkeypatch):
        monkeypatch.setattr(
            _select_tests, "compute_changed_paths", lambda repo_root: [r'"docs/caf\303\251.md"']
        )

        assert "git quoted these changed paths" in _failure_message_of_changed_plan_files()

    def test_git_quoted_path_beside_an_unquoted_plan_file_still_fails(self, monkeypatch, tmp_path):
        plans_dir = _plans_dir_under(tmp_path, monkeypatch)
        (plans_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
        monkeypatch.setattr(
            _select_tests,
            "compute_changed_paths",
            lambda repo_root: [r'".claude/plans/caf\303\251.md"', ".claude/plans/plan.md"],
        )

        assert "git quoted these changed paths" in _failure_message_of_changed_plan_files()

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
