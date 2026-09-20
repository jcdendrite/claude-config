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
- The file filter is a cooperative one: case-sensitive `.md` files under
  `.claude/plans/` only.
- A modified plan is checked whole, so a pre-existing orphan in a plan the
  branch touches fails the run. The remedy is to fix the citation or leave the
  plan out of the diff. Plans are historical records and are meant to be left
  unedited.
- It needs `origin/main`, so it depends on the workflow's full-depth checkout.
  A missing ref fails rather than skips. When `select-tests.py` falls back to
  the full suite because git is unavailable, this test runs in that suite and
  fails.
"""
from __future__ import annotations

import functools
import importlib.util
import os
import subprocess
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


def _git_unavailable_failure_message(exc) -> str:
    """The advice fits the arm: the first git call reports every merge-base
    failure alike, so the merge-base arm lists the possible causes with the
    remedy for each environment instead of naming one."""
    if isinstance(exc, _select_tests.MergeBaseUnresolved):
        return (
            f"cannot determine which plan files this branch changed: {exc}. "
            "Possible causes: origin/main is missing, the history is shallow with no common ancestor, "
            "git is missing or timed out, the directory is not a git repository, "
            "or git refuses an unowned directory. "
            "In CI, the checkout must fetch the full history (`fetch-depth: 0`). "
            "Locally, run `git fetch origin`, and deepen a shallow clone with `git fetch --unshallow`."
        )
    return (
        f"cannot determine which plan files this branch changed: {exc}. "
        "origin/main resolved, so a later git call failed. "
        "A timeout under load is the leading suspect, then a failing `git diff` or `git ls-files`. "
        "Rerun the check."
    )


def _changed_plan_files() -> list[Path]:
    """Existing .md files under PLANS_DIR that this branch changed, or a skip
    when it changed none. Fails, rather than skips, when git cannot say what
    changed: a backstop that silently stops running is worse than none. The
    filter is cooperative: case-sensitive `.md` files under PLANS_DIR only."""
    try:
        changed_paths = _select_tests.compute_changed_paths(REPO_ROOT)
    except _select_tests.GitDiffUnavailable as exc:
        pytest.fail(_git_unavailable_failure_message(exc), pytrace=False)
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
    """One `file:line: citation 'token' ...` message per orphaned citation. The
    plan path is rendered ASCII-only, since a changed path is contributor-controlled
    and the report transport cannot encode a lone surrogate."""
    return [
        f"{_select_tests.printable_path(str(plan_file.relative_to(REPO_ROOT)))}:{line}: "
        f"citation '{token}' resolves to no defined ledger label"
        for plan_file in plan_files
        for token, line in _check_ledger_citations.find_orphan_citations(
            plan_file.read_text(encoding="utf-8-sig")
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

    def test_hostile_valid_utf8_plan_path_yields_one_inert_ascii_message(self, tmp_path, monkeypatch):
        """No file is created: the read is stubbed, so the path only has to
        reach the message builder."""
        hostile_relative_path = ".claude/plans/x\x1b]0;t\x07\x85\u2028\nPASS: forged.md"
        hostile_plan_file = tmp_path / hostile_relative_path
        monkeypatch.setattr(Path, "read_text", lambda self, encoding: "anchors: G9\n")

        messages = _orphan_messages([hostile_plan_file])

        assert len(messages) == 1
        message = messages[0]
        assert message.isascii()
        assert all(ord(char) >= 0x20 for char in message)
        assert message.startswith(".claude/plans/x\\x1b]0;t\\x07\\x85\\u2028\\nPASS: forged.md:1: citation 'g9'")

    def test_bom_led_plan_yields_the_orphan_the_cli_reports(self, tmp_path, capsys):
        """The CLI decodes utf-8-sig. Read as plain utf-8, the BOM hides the
        first-line fence opener, which pairs the later fences the wrong way
        round and masks the `anchors:` orphan between them."""
        plan_file = tmp_path / "bom-plan.md"
        plan_file.write_bytes(
            b"\xef\xbb\xbf```\nquoted example\n```\n\nanchors: G9\n\n```\nlater example\n```\n"
        )

        exit_code = _check_ledger_citations.main(["check-ledger-citations.py", str(plan_file)])
        cli_output = capsys.readouterr().out

        assert exit_code == 1
        assert f"{plan_file}:5: citation 'g9' resolves to no defined ledger label" in cli_output
        assert _orphan_messages([plan_file]) == [
            "bom-plan.md:5: citation 'g9' resolves to no defined ledger label"
        ]


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


def _returned_or_failed_on_skip(function):
    """Call `function`, turning a skip into a test failure. The mirror of
    `_failure_message_of_changed_plan_files` for the arms that must not skip:
    a green SKIPPED would otherwise mask a regression that empties the result."""
    try:
        return function()
    except pytest.skip.Exception:
        pytest.fail(f"{function.__name__} skipped where it had to run to completion", pytrace=False)


class TestChangedPlanFilesSelection:
    """Pins the outcomes for the different conditions: git unavailable fails,
    and no changed plan file skips."""

    def test_git_diff_unavailable_fails_with_the_exceptions_own_message(self, monkeypatch):
        def raise_git_diff_unavailable(repo_root):
            raise _select_tests.MergeBaseUnresolved("could not resolve merge-base against origin/main")

        monkeypatch.setattr(_select_tests, "compute_changed_paths", raise_git_diff_unavailable)

        assert "could not resolve merge-base against origin/main" in _failure_message_of_changed_plan_files()

    def test_unresolvable_merge_base_message_names_the_full_history_checkout_and_a_fetch(self, monkeypatch):
        def raise_merge_base_unresolved(repo_root):
            raise _select_tests.MergeBaseUnresolved("could not resolve merge-base against origin/main")

        monkeypatch.setattr(_select_tests, "compute_changed_paths", raise_merge_base_unresolved)

        message = _failure_message_of_changed_plan_files()

        assert "fetch-depth: 0" in message
        assert "git fetch origin" in message
        assert "git fetch --unshallow" in message
        assert "shallow with no common ancestor" in message
        assert "not a git repository" in message

    def test_failing_first_git_call_selects_the_merge_base_advice(self, monkeypatch):
        """The real exception from compute_changed_paths, not a hand-built one,
        must reach the merge-base arm."""

        def failing_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="")

        monkeypatch.setattr(
            _select_tests,
            "compute_changed_paths",
            functools.partial(_select_tests.compute_changed_paths, run=failing_run),
        )

        assert "fetch-depth: 0" in _failure_message_of_changed_plan_files()

    @pytest.mark.parametrize(
        "failure_text",
        [
            "git diff against the merge-base failed",
            "git diff against HEAD failed",
            "git ls-files for untracked files failed",
        ],
        ids=["diff-against-merge-base", "diff-against-head", "ls-files"],
    )
    def test_other_git_failures_name_the_exception_and_a_timeout_without_the_checkout_advice(
        self, monkeypatch, failure_text
    ):
        def raise_git_diff_unavailable(repo_root):
            raise _select_tests.GitDiffUnavailable(failure_text)

        monkeypatch.setattr(_select_tests, "compute_changed_paths", raise_git_diff_unavailable)

        message = _failure_message_of_changed_plan_files()

        assert f"changed: {failure_text}." in message
        assert "timeout under load is the leading suspect" in message
        assert "fetch-depth" not in message
        assert "git fetch" not in message

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

        assert _returned_or_failed_on_skip(_changed_plan_files) == [existing_plan]

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

        assert _returned_or_failed_on_skip(_changed_plan_files) == [markdown_plan]


class TestTopLevelBackstop:
    """Pins the composition in the top-level test: it must fail on an orphan in
    a changed plan and return on a clean one."""

    def test_changed_plan_with_an_orphan_fails_naming_file_line_and_token(self, monkeypatch, tmp_path):
        plans_dir = _plans_dir_under(tmp_path, monkeypatch)
        (plans_dir / "plan-with-orphan.md").write_text("# Plan\n\n## Ledger\n\nanchors: G9\n", encoding="utf-8")
        monkeypatch.setattr(
            _select_tests, "compute_changed_paths", lambda repo_root: [".claude/plans/plan-with-orphan.md"]
        )

        with pytest.raises(AssertionError) as failure:
            _returned_or_failed_on_skip(test_changed_plan_files_have_no_orphan_ledger_citations)

        assert ".claude/plans/plan-with-orphan.md:5: citation 'g9'" in str(failure.value)

    def test_changed_plan_with_only_resolving_citations_returns(self, monkeypatch, tmp_path):
        plans_dir = _plans_dir_under(tmp_path, monkeypatch)
        (plans_dir / "clean-plan.md").write_text(
            "## Ledger\nanchors: root\n\nG1. a claim\n\n## Rows\nanchors: G1\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            _select_tests, "compute_changed_paths", lambda repo_root: [".claude/plans/clean-plan.md"]
        )

        _returned_or_failed_on_skip(test_changed_plan_files_have_no_orphan_ledger_citations)

    def test_non_utf8_plan_filename_with_an_orphan_fails_with_an_ascii_encodable_message(
        self, monkeypatch, tmp_path
    ):
        """A lone surrogate from os.fsdecode cannot cross the xdist transport,
        so the failure message must be plain ASCII."""
        plans_dir = _plans_dir_under(tmp_path, monkeypatch)
        plan_path_bytes = os.fsencode(plans_dir) + b"/bad\xffplan.md"
        try:
            with open(plan_path_bytes, "wb") as plan_handle:
                plan_handle.write(b"# Plan\n\n## Ledger\n\nanchors: G9\n")
        except OSError as exc:
            pytest.skip(f"filesystem rejects a non-UTF-8 filename: {exc}")
        changed_path = os.fsdecode(b".claude/plans/bad\xffplan.md")
        monkeypatch.setattr(_select_tests, "compute_changed_paths", lambda repo_root: [changed_path])

        with pytest.raises(AssertionError) as failure:
            _returned_or_failed_on_skip(test_changed_plan_files_have_no_orphan_ledger_citations)

        message = str(failure.value)
        assert message.encode("ascii")
        assert ".claude/plans/bad\\udcffplan.md:5: citation 'g9'" in message
