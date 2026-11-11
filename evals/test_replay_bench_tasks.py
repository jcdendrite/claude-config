"""Tests for evals/replay_bench/tasks. Every repository is a synthetic one built under
`tmp_path` with real git. No test launches `claude`.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
from replay_bench import tasks
from replay_bench.comment_metrics import LineClass
from replay_bench.tasks import SkipReason
from review_bench import fixture_repo, identifiers, local_git
from test_review_bench_fixtures import _tar_of, _tar_with_link

TRUSTED = "dev@example.com"
UNTRUSTED = "outsider@example.com"
OTHER_COMMITTER = "committer@example.com"
BARE_WRITER_MARKDOWN = "---\nname: bare-writer\n---\nYou implement the change.\n"
FIXTURE_TIMEOUT_S = 123.0

_APP_BASE = "# existing note\nx = 0\n"


def _app_with(code_lines: int) -> str:
    """The base app file plus two comment lines (five words) and `code_lines` code lines."""
    return _APP_BASE + "# one two three\n# four five\n" + "".join(f"value_{n} = {n}\n" for n in range(code_lines))


QUALIFYING_CHANGE = {"src/app.py": _app_with(12)}


# --- helpers -----------------------------------------------------------------


def _git(
    repo: Path, *args: str, author: str = TRUSTED, committer: str | None = None, input_bytes: bytes | None = None,
) -> bytes:
    """`committer` defaults to `author`."""
    environment = {
        **local_git.isolated_git_environment(),
        "GIT_AUTHOR_NAME": "Author", "GIT_AUTHOR_EMAIL": author,
        "GIT_COMMITTER_NAME": "Committer", "GIT_COMMITTER_EMAIL": committer or author,
    }
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True,
        input=input_bytes, env=environment,
    ).stdout


def _write_files(repo: Path, files: dict[str, str | bytes | None]) -> None:
    for rel_path, content in files.items():
        target = repo / rel_path
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)


def _write_symlinks(repo: Path, symlinks: dict[str, str]) -> None:
    for rel_path, target in symlinks.items():
        (repo / rel_path).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, repo / rel_path)


def _make_repo(
    tmp_path: Path, base_files: dict[str, str | bytes] | None = None, *, symlinks: dict[str, str] | None = None,
    gitlinks: dict[str, str] | None = None, name: str = "repo", author: str = TRUSTED,
) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_files(repo, {"src/app.py": _APP_BASE, **(base_files or {})})
    _write_symlinks(repo, symlinks or {})
    _git(repo, "add", "-A")
    for gitlink_path, gitlink_sha in (gitlinks or {}).items():
        _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{gitlink_sha},{gitlink_path}")
    _git(repo, "commit", "-q", "-m", "base", author=author)
    return repo


def _change(
    repo: Path, files: dict[str, str | bytes | None] | None = None, message: str = "Add helpers", *,
    author: str = TRUSTED, committer: str | None = None,
) -> str:
    """Commits `files` (None deletes), staging only those paths so an unmodified gitlink stays."""
    files = QUALIFYING_CHANGE if files is None else files
    _write_files(repo, files)
    _git(repo, "--literal-pathspecs", "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", message, author=author, committer=committer)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _mine(*repos: Path, trusted: tuple[str, ...] = (TRUSTED,), **limit_overrides) -> tasks.MiningReport:
    return tasks.mine_tasks(
        list(repos), list(trusted), tasks.SelectionLimits(**limit_overrides), progress=lambda line: None,
    )


def _mine_one_task(repo: Path, **limit_overrides) -> tasks.ReplayTask:
    report = _mine(repo, **limit_overrides)
    assert len(report.tasks) == 1, report.skip_counts
    return report.tasks[0]


def _assert_only_skipped_as(report: tasks.MiningReport, reason: SkipReason) -> None:
    """The base commit is always skipped as a root commit, so it joins every expected count."""
    assert report.tasks == []
    assert dict(report.skip_counts) == {SkipReason.NO_PARENT: 1, reason: 1}


def _git_text(directory: Path, *args: str) -> str:
    return _git(directory, *args).decode()


GITLINK_BASE_SHA = "a" * 40
GITLINK_BUMPED_SHA = "b" * 40
RENAMED_FILE_CONTENT = "".join(f"renamed_{n} = {n}\n" for n in range(3))


def _repo_for_non_additive_commit(tmp_path: Path) -> Path:
    """A repository whose base holds what `_commit_with_non_additive_changes` deletes, renames, flips and bumps."""
    return _make_repo(
        tmp_path,
        {"docs/old.py": "x = 1\n", "scripts/run.sh": "echo run\n", "src/renamed_from.py": RENAMED_FILE_CONTENT},
        gitlinks={"vendor/lib": GITLINK_BASE_SHA},
    )


def _commit_with_non_additive_changes(repo: Path) -> str:
    """Commits `QUALIFYING_CHANGE` together with a deleted file, a renamed file (a delete plus an add of the
    same content), an executable-bit flip and a gitlink bump."""
    _git(repo, "update-index", "--chmod=+x", "scripts/run.sh")
    _git(repo, "update-index", "--cacheinfo", f"160000,{GITLINK_BUMPED_SHA},vendor/lib")
    return _change(
        repo, {**QUALIFYING_CHANGE, "docs/old.py": None, "src/renamed_from.py": None, "src/renamed_to.py": RENAMED_FILE_CONTENT},
    )


def _record_subprocess_run_calls(monkeypatch) -> list[dict]:
    """Records the keyword arguments of each `subprocess.run` call made after this returns."""
    calls: list[dict] = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def _repo_from_trees(tmp_path: Path, base_tree: dict, change_tree: dict) -> Path:
    """A repository whose HEAD commit holds `change_tree` over a root commit holding `base_tree`. A tree maps a
    name (str or bytes) to a str of blob content or a nested tree. `git add` refuses a `.git` path and APFS
    refuses a non-UTF-8 name, so the trees come from `git mktree`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    def blob(content: str) -> bytes:
        return _git(repo, "hash-object", "-w", "--stdin", input_bytes=content.encode()).strip()

    def tree(entries: dict) -> bytes:
        lines = [
            (b"040000 tree " + tree(value) if isinstance(value, dict) else b"100644 blob " + blob(value))
            + b"\t" + os.fsencode(name)
            for name, value in entries.items()
        ]
        return _git(repo, "mktree", input_bytes=b"\n".join(lines) + b"\n").strip()

    base_commit = _git(repo, "commit-tree", tree(base_tree).decode(), "-m", "base").decode().strip()
    change_commit = _git(
        repo, "commit-tree", tree(change_tree).decode(), "-p", base_commit, "-m", "Add helpers",
    ).decode().strip()
    _git(repo, "update-ref", "HEAD", change_commit)
    return repo


# --- selection: what qualifies -----------------------------------------------


class TestQualifyingCommit:
    def test_a_commit_adding_enough_code_lines_becomes_a_task_with_its_repo_commits_and_brief(self, tmp_path):
        repo = _make_repo(tmp_path)
        base = _git_text(repo, "rev-parse", "HEAD").strip()
        commit = _change(repo, message="Add the helpers\n\nThey compute values.")

        task = _mine_one_task(repo)

        assert task.source_repo == str(repo.resolve())
        assert (task.base_commit, task.commit) == (base, commit)
        assert task.brief == "Add the helpers\n\nThey compute values."
        assert task.changed_paths == ("src/app.py",)
        assert (task.author_email, task.committer_email) == (TRUSTED, TRUSTED)

    def test_task_id_is_eight_hex_digits_of_the_sha256_of_the_repo_path_a_hyphen_and_twelve_commit_digits(self, tmp_path):
        repo = _make_repo(tmp_path)
        commit = _change(repo)

        task_id = _mine_one_task(repo).id

        expected_head = hashlib.sha256(os.fsencode(repo.resolve())).hexdigest()[:8]
        assert task_id == f"{expected_head}-{commit[:12]}"

    def test_the_same_repository_gets_the_same_task_id_across_two_mines(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo)

        assert _mine_one_task(repo).id == _mine_one_task(repo).id

    def test_a_clone_at_another_path_gets_a_different_task_id_head_for_the_same_commit(self, tmp_path):
        repo = _make_repo(tmp_path)
        commit = _change(repo)
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", str(repo), str(clone))

        original_id = _mine_one_task(repo).id
        clone_id = _mine_one_task(clone).id

        assert original_id.split("-")[1] == clone_id.split("-")[1] == commit[:12]
        assert original_id.split("-")[0] != clone_id.split("-")[0]

    def test_a_commit_with_a_trusted_author_and_an_untrusted_committer_keeps_each_identity_in_its_own_field(
        self, tmp_path,
    ):
        repo = _make_repo(tmp_path)
        _change(repo, author=TRUSTED, committer=OTHER_COMMITTER)

        report = _mine(repo)

        (task,) = report.tasks
        assert (task.author_email, task.committer_email) == (TRUSTED, OTHER_COMMITTER)
        assert dict(report.author_counts) == {TRUSTED: 2}
        assert dict(report.committer_counts) == {TRUSTED: 1, OTHER_COMMITTER: 1}

    def test_a_commit_with_an_untrusted_author_and_a_trusted_committer_is_skipped_with_each_identity_counted_as_its_own(
        self, tmp_path,
    ):
        repo = _make_repo(tmp_path)
        _change(repo, author=UNTRUSTED, committer=TRUSTED)

        report = _mine(repo)

        _assert_only_skipped_as(report, SkipReason.UNTRUSTED_AUTHOR)
        assert dict(report.author_counts) == {TRUSTED: 1, UNTRUSTED: 1}
        assert dict(report.committer_counts) == {TRUSTED: 2}
        assert f"  {UNTRUSTED!r}: 1" in tasks.format_mining_summary(report)

    def test_a_non_ascii_changed_path_is_kept_with_every_added_line_classified(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {"src/日本語.py": "".join(f"value_{n} = {n}\n" for n in range(12))})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("src/日本語.py",)
        assert task.reference_metrics.line_counts["non_test"][LineClass.CODE.value] == 12
        assert task.reference_metrics.line_counts["non_test"][LineClass.UNCLASSIFIED.value] == 0

    def test_a_path_holding_glob_characters_never_pulls_in_a_path_it_would_match(self, tmp_path):
        repo = _make_repo(tmp_path, {"CLAUDE.md": "old\n", "[C]LAUDE.md": "old\n"})
        _change(repo, {**QUALIFYING_CHANGE, "CLAUDE.md": "new rules\n", "[C]LAUDE.md": "new notes\n"})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("[C]LAUDE.md", "src/app.py")
        assert task.reference_metrics.changed_paths == ["[C]LAUDE.md", "src/app.py"]

    def test_a_regular_file_named_dot_claude_stays_in_the_task_since_the_prune_keeps_it(self, tmp_path):
        repo = _make_repo(tmp_path, {".claude": "old\n"})
        _change(repo, {**QUALIFYING_CHANGE, ".claude": "new\n"})

        assert _mine_one_task(repo).changed_paths == (".claude", "src/app.py")

    def test_an_added_or_deleted_dot_claude_file_stays_and_an_added_dot_claude_symlink_is_dropped(self, tmp_path):
        def twelve_code_lines() -> str:
            return "".join(f"value_{n} = {n}\n" for n in range(12))

        repo = _make_repo(tmp_path)
        added_file_commit = _change(repo, {".claude": "rules\n", "src/first.py": twelve_code_lines()})
        deleted_file_commit = _change(repo, {".claude": None, "src/second.py": twelve_code_lines()})
        _write_symlinks(repo, {"pkg/.claude": "../src"})
        _git(repo, "--literal-pathspecs", "add", "--", "pkg/.claude")
        added_symlink_commit = _change(repo, {"src/third.py": twelve_code_lines()})

        task_by_commit = {task.commit: task for task in _mine(repo).tasks}

        assert task_by_commit[added_file_commit].changed_paths == (".claude", "src/first.py")
        assert task_by_commit[added_file_commit].reference_metrics.changed_paths == [".claude", "src/first.py"]
        assert task_by_commit[deleted_file_commit].changed_paths == (".claude", "src/second.py")
        assert task_by_commit[deleted_file_commit].reference_metrics.changed_paths == [".claude", "src/second.py"]
        assert task_by_commit[added_symlink_commit].changed_paths == ("src/third.py",)
        assert task_by_commit[added_symlink_commit].reference_metrics.changed_paths == ["src/third.py"]

    def test_a_commit_that_also_edits_a_nested_instruction_file_keeps_that_path_out_of_the_task(self, tmp_path):
        repo = _make_repo(tmp_path, {"sub/CLAUDE.md": "old\n"})
        _change(repo, {**QUALIFYING_CHANGE, "sub/CLAUDE.md": "new rules in prose\nmore\n"})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("src/app.py",)
        assert task.reference_metrics.changed_paths == ["src/app.py"]
        assert task.reference_metrics.line_counts["non_test"]["prose"] == 0

    def test_a_merge_commit_brief_carries_the_subjects_of_the_commits_it_merged(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "switch", "-q", "-c", "feature")
        _change(repo, message="Add the first helper")
        _git(repo, "switch", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

        task = _mine_one_task(repo)

        assert task.brief.startswith("Merge feature")
        assert "- Add the first helper" in task.brief

    def test_a_merge_commit_brief_lists_the_merged_subjects_newest_first_under_a_heading(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "switch", "-q", "-c", "feature")
        _change(repo, {"src/app.py": _app_with(11)}, message="Add the first helper")
        _change(repo, {"src/app.py": _app_with(12)}, message="Add the second helper")
        _git(repo, "switch", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

        task = _mine_one_task(repo)

        assert task.brief == "Merge feature\n\nMerged commits:\n- Add the second helper\n- Add the first helper"

    def test_reference_metrics_count_the_comment_words_and_code_lines_the_commit_added(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo)

        metrics = _mine_one_task(repo).reference_metrics

        assert metrics.line_counts["non_test"]["code"] == 12
        assert metrics.line_counts["non_test"]["comment"] == 2
        assert metrics.comment_words["non_test"] == 5


class TestNonAdditiveChanges:
    def test_a_commit_that_also_deletes_renames_flips_a_mode_and_bumps_a_gitlink_is_kept_with_its_added_lines_only(
        self, tmp_path,
    ):
        repo = _repo_for_non_additive_commit(tmp_path)
        _commit_with_non_additive_changes(repo)

        task = _mine_one_task(repo)

        expected_paths = [
            "docs/old.py", "scripts/run.sh", "src/app.py", "src/renamed_from.py", "src/renamed_to.py", "vendor/lib",
        ]
        assert list(task.changed_paths) == expected_paths
        assert task.reference_metrics.changed_paths == expected_paths
        # The renamed file's new side counts as added, so its three lines join the twelve of `QUALIFYING_CHANGE`.
        assert task.reference_metrics.line_counts["non_test"][LineClass.CODE.value] == 12 + 3
        assert task.reference_metrics.non_file_entries == 1

    def test_a_commit_that_turns_a_dot_claude_file_into_a_symlink_is_kept_without_that_path(self, tmp_path):
        repo = _make_repo(tmp_path, {".claude": "old\n"})
        (repo / ".claude").unlink()
        _write_symlinks(repo, {".claude": "src"})
        _git(repo, "--literal-pathspecs", "add", "--", ".claude")
        _change(repo)

        task = _mine_one_task(repo)

        assert task.changed_paths == ("src/app.py",)
        assert task.reference_metrics.changed_paths == ["src/app.py"]


class TestBaseDensity:
    def test_density_is_the_base_comment_words_per_base_code_line_of_the_changed_non_test_files(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo)

        assert _mine_one_task(repo).base_density == pytest.approx(2 / 1)

    def test_a_multi_line_python_docstring_in_the_base_adds_its_words_to_the_numerator(self, tmp_path):
        base_with_docstring = '"""Module summary\nspanning two lines here.\n"""\nx = 0\n'
        repo = _make_repo(tmp_path, {"src/app.py": base_with_docstring})
        _change(repo, {"src/app.py": base_with_docstring + "".join(f"value_{n} = {n}\n" for n in range(12))})

        # Six docstring words ("Module", "summary", "spanning", "two", "lines", "here.") over the one code line.
        assert _mine_one_task(repo).base_density == pytest.approx(6 / 1)

    def test_density_over_several_files_is_the_ratio_of_the_summed_words_and_code_lines_not_the_mean_of_ratios(
        self, tmp_path,
    ):
        wordy_base = "# one two three four\nx = 1\n"
        terse_base = "a = 1\nb = 2\nc = 3\n"
        repo = _make_repo(tmp_path, {"src/wordy.py": wordy_base, "src/terse.py": terse_base})
        added_code = "".join(f"value_{n} = {n}\n" for n in range(6))
        _change(repo, {"src/wordy.py": wordy_base + added_code, "src/terse.py": terse_base + added_code})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("src/terse.py", "src/wordy.py")
        # Four words over four code lines. Averaging the files' own ratios, 4 and 0, would give 2.
        assert task.base_density == pytest.approx(4 / 4)

    def test_a_base_test_file_counts_in_neither_the_numerator_nor_the_denominator(self, tmp_path):
        base_test_file = "# a long note of many words about this test\ny = 1\ny = 2\n"
        repo = _make_repo(tmp_path, {"tests/test_app.py": base_test_file})
        _change(repo, {**QUALIFYING_CHANGE, "tests/test_app.py": base_test_file + "y = 3\n"})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("src/app.py", "tests/test_app.py")
        assert task.base_density == pytest.approx(2 / 1)

    def test_a_commit_of_new_files_only_keeps_a_null_density_and_is_counted_in_the_summary(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {"src/new_module.py": "".join(f"value_{n} = {n}\n" for n in range(12))})

        report = _mine(repo)

        assert [task.base_density for task in report.tasks] == [None]
        assert "kept tasks with a null base density: 1" in tasks.format_mining_summary(report)

    def test_a_changed_file_whose_base_holds_no_code_line_keeps_a_null_density(self, tmp_path):
        repo = _make_repo(tmp_path, {"src/notes.py": "# only a comment\n"})
        _change(repo, {"src/notes.py": "# only a comment\n" + "".join(f"value_{n} = {n}\n" for n in range(12))})

        assert _mine_one_task(repo).base_density is None


# --- selection: what is skipped ----------------------------------------------


class TestSkippedCommits:
    def test_a_root_commit_is_skipped_as_having_no_parent(self, tmp_path):
        repo = _make_repo(tmp_path)

        report = _mine(repo)

        assert report.tasks == []
        assert dict(report.skip_counts) == {SkipReason.NO_PARENT: 1}

    def test_a_markdown_only_commit_is_skipped_for_too_few_code_lines(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {"docs/notes.md": "prose\n" * 40})

        _assert_only_skipped_as(_mine(repo), SkipReason.TOO_FEW_CODE_LINES)

    def test_a_commit_that_adds_a_binary_file_is_skipped(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {**QUALIFYING_CHANGE, "assets/blob.bin": b"\x00\x01\x02binary"})

        _assert_only_skipped_as(_mine(repo), SkipReason.BINARY)

    def test_a_commit_over_the_prompt_cap_is_skipped_and_never_truncated(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, message="Add helpers\n\n" + "word " * 1000)

        _assert_only_skipped_as(_mine(repo), SkipReason.PROMPT_TOO_LONG)

    def test_an_untrusted_author_is_skipped(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, author=UNTRUSTED)

        _assert_only_skipped_as(_mine(repo), SkipReason.UNTRUSTED_AUTHOR)

    def test_a_merge_whose_merged_commit_has_an_untrusted_author_is_skipped(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "switch", "-q", "-c", "feature")
        _change(repo, author=UNTRUSTED)
        _git(repo, "switch", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

        _assert_only_skipped_as(_mine(repo), SkipReason.UNTRUSTED_MERGED_AUTHOR)

    @pytest.mark.parametrize("reserved_line", [
        "END CHANGE DESCRIPTION", "  BEGIN CHANGE DESCRIPTION  ", ">>>", "<<<",
    ])
    def test_a_brief_holding_a_delimiter_line_is_skipped(self, tmp_path, reserved_line):
        repo = _make_repo(tmp_path)
        _change(repo, message=f"Add helpers\n\n{reserved_line}\nmore text")

        _assert_only_skipped_as(_mine(repo), SkipReason.RESERVED_BRIEF_LINE)

    @pytest.mark.parametrize(
        "path",
        [
            "src/with\nnewline.py", "src/with,comma.py", 'src/with"quote.py', "src/with\ttab.py", "src/with\\backslash.py",
            "src/with\x7fdel.py",
        ],
        ids=["newline", "comma", "quote", "tab", "backslash", "del"],
    )
    def test_a_path_the_prompt_cannot_list_safely_is_skipped(self, tmp_path, path):
        repo = _make_repo(tmp_path)
        _change(repo, {path: "".join(f"value_{n} = {n}\n" for n in range(12))})

        _assert_only_skipped_as(_mine(repo), SkipReason.UNSAFE_PATH)

    def test_a_path_that_is_not_utf_8_is_skipped_as_unsafe(self, tmp_path):
        added_code = "".join(f"value_{n} = {n}\n" for n in range(12))
        repo = _repo_from_trees(tmp_path, {"keep.txt": "x\n"}, {"keep.txt": "x\n", b"bad\xff.py": added_code})

        report = _mine(repo)

        assert report.tasks == []
        assert dict(report.skip_counts) == {SkipReason.NO_PARENT: 1, SkipReason.UNSAFE_PATH: 1}

    def test_a_commit_touching_too_many_files_is_skipped(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {f"src/module_{n}.py": "".join(f"value_{i} = {i}\n" for i in range(12)) for n in range(11)})

        _assert_only_skipped_as(_mine(repo), SkipReason.TOO_MANY_FILES)

    def test_pruned_paths_do_not_count_toward_the_file_bound(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {**QUALIFYING_CHANGE, "a/CLAUDE.md": "rules\n", "b/CLAUDE.md": "rules\n"})

        task = _mine_one_task(repo, max_files=2)

        assert task.changed_paths == ("src/app.py",)

    def test_a_pruned_path_holding_a_comma_does_not_make_the_commit_unsafe(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {**QUALIFYING_CHANGE, "notes,draft/CLAUDE.md": "rules\n"})

        assert _mine_one_task(repo).changed_paths == ("src/app.py",)

    def test_a_commit_that_only_edits_pruned_paths_is_skipped_for_too_few_code_lines(self, tmp_path):
        repo = _make_repo(tmp_path, {"CLAUDE.md": "old\n"})
        _change(repo, {"CLAUDE.md": "new\n"})

        _assert_only_skipped_as(_mine(repo), SkipReason.TOO_FEW_CODE_LINES)


class TestBaseTreeBounds:
    @staticmethod
    def _repo_with_base_blob_bytes(tmp_path: Path, total_bytes: int) -> Path:
        filler = b"f" * (total_bytes - len(_APP_BASE.encode()))
        repo = _make_repo(tmp_path, {"assets/filler.dat": filler})
        _change(repo)
        return repo

    def test_a_base_tree_of_exactly_one_megabyte_is_kept_when_the_bound_is_one_megabyte(self, tmp_path):
        repo = self._repo_with_base_blob_bytes(tmp_path, 1_000_000)

        task = _mine_one_task(repo, max_base_mb=1)

        assert task.base_tree_bytes == 1_000_000

    def test_a_base_tree_one_byte_past_a_megabyte_is_skipped(self, tmp_path):
        repo = self._repo_with_base_blob_bytes(tmp_path, 1_000_001)

        _assert_only_skipped_as(_mine(repo, max_base_mb=1), SkipReason.BASE_TREE_TOO_LARGE)

    def test_a_base_tree_at_the_entry_bound_is_kept_and_one_entry_past_it_is_skipped(self, tmp_path):
        kept_repo = _make_repo(tmp_path, {"a.txt": "a", "b.txt": "b"}, name="kept")
        _change(kept_repo)
        skipped_repo = _make_repo(tmp_path, {"a.txt": "a", "b.txt": "b", "c.txt": "c"}, name="skipped")
        _change(skipped_repo)

        assert _mine_one_task(kept_repo, max_base_entries=3).base_tree_entries == 3
        _assert_only_skipped_as(_mine(skipped_repo, max_base_entries=3), SkipReason.BASE_TREE_TOO_MANY_ENTRIES)


class TestExportAttributes:
    @pytest.mark.parametrize(
        ("attributes_path", "attributes_text"),
        [
            (".gitattributes", "tests export-ignore\n"),
            ("sub/.gitattributes", "*.txt export-subst\n"),
            (".gitattributes", "# export-ignore\n"),  # the deliberate over-skip: the text is matched anywhere on a line
        ],
        ids=["root-export-ignore", "nested-export-subst", "commented-line"],
    )
    def test_a_base_tree_whose_gitattributes_holds_an_export_attribute_is_skipped(
        self, tmp_path, attributes_path, attributes_text,
    ):
        repo = _make_repo(tmp_path, {attributes_path: attributes_text})
        _change(repo)

        _assert_only_skipped_as(_mine(repo), SkipReason.EXPORT_ATTRIBUTES)

    def test_a_base_tree_whose_gitattributes_holds_no_export_attribute_is_kept(self, tmp_path):
        repo = _make_repo(tmp_path, {".gitattributes": "*.py text eol=lf\n"})
        _change(repo)

        assert len(_mine(repo).tasks) == 1

    def test_premise_git_archive_drops_an_export_ignore_path(self, tmp_path):
        """Premise pin: this tests git, not `tasks`. The skip above exists because git behaves this way."""
        repo = _make_repo(tmp_path, {".gitattributes": "tests export-ignore\n", "tests/check.txt": "x\n"})

        archive = _git(repo, "archive", "--format=tar", "HEAD")

        with tarfile.open(fileobj=io.BytesIO(archive)) as archive_file:
            archived_names = archive_file.getnames()
        assert "src/app.py" in archived_names
        assert not any(name.startswith("tests") for name in archived_names)


class TestThresholdsAreInclusive:
    @pytest.mark.parametrize(("code_lines", "kept"), [(10, True), (9, False)])
    def test_min_code_lines(self, tmp_path, code_lines, kept):
        repo = _make_repo(tmp_path)
        _change(repo, {"src/app.py": _app_with(code_lines)})

        report = _mine(repo, min_code_lines=10)

        assert bool(report.tasks) is kept
        if not kept:
            _assert_only_skipped_as(report, SkipReason.TOO_FEW_CODE_LINES)

    @pytest.mark.parametrize(("code_lines", "kept"), [(12, True), (13, False)])
    def test_max_code_lines(self, tmp_path, code_lines, kept):
        repo = _make_repo(tmp_path)
        _change(repo, {"src/app.py": _app_with(code_lines)})

        report = _mine(repo, max_code_lines=12)

        assert bool(report.tasks) is kept
        if not kept:
            _assert_only_skipped_as(report, SkipReason.TOO_MANY_CODE_LINES)

    def test_code_lines_added_only_under_tests_do_not_count_toward_the_minimum(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {"tests/test_app.py": "".join(f"value_{n} = {n}\n" for n in range(12))})

        _assert_only_skipped_as(_mine(repo, min_code_lines=10), SkipReason.TOO_FEW_CODE_LINES)

    def test_code_lines_added_under_tests_do_not_count_toward_the_maximum(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {
            "src/app.py": _app_with(12), "tests/test_app.py": "".join(f"value_{n} = {n}\n" for n in range(20)),
        })

        task = _mine_one_task(repo, max_code_lines=12)

        assert task.reference_metrics.line_counts["non_test"][LineClass.CODE.value] == 12
        assert task.reference_metrics.line_counts["test"][LineClass.CODE.value] == 20

    @pytest.mark.parametrize(("file_count", "kept"), [(3, True), (4, False)])
    def test_max_files(self, tmp_path, file_count, kept):
        repo = _make_repo(tmp_path)
        _change(repo, {f"src/module_{n}.py": "".join(f"v{i} = {i}\n" for i in range(4)) for n in range(file_count)})

        report = _mine(repo, max_files=3, min_code_lines=1)

        assert bool(report.tasks) is kept
        if not kept:
            _assert_only_skipped_as(report, SkipReason.TOO_MANY_FILES)

    def test_max_prompt_chars(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo)
        prompt_length = len(tasks.build_inner_prompt(_mine_one_task(repo)))

        assert len(_mine(repo, max_prompt_chars=prompt_length).tasks) == 1
        _assert_only_skipped_as(_mine(repo, max_prompt_chars=prompt_length - 1), SkipReason.PROMPT_TOO_LONG)


# --- selection: base trees extraction refuses --------------------------------

_NON_ASCII_DIR = "日本語"


class TestBaseTreesExtractionRefuses:
    @pytest.mark.parametrize(
        ("symlinks", "files", "reason"),
        [
            ({".claude": "src"}, {}, SkipReason.CLAUDE_DIR_SYMLINK),
            ({f".claude/{_NON_ASCII_DIR}": "../src/app.py"}, {}, SkipReason.CLAUDE_DIR_SYMLINK),
            ({"link": "/etc/hosts"}, {}, SkipReason.ABSOLUTE_SYMLINK),
            ({"link": "../outside"}, {}, SkipReason.ESCAPING_SYMLINK),
            ({"link": ".git/hooks"}, {}, SkipReason.GIT_PATH_COMPONENT),
            ({}, {".bench/note.txt": "x"}, SkipReason.BENCH_DIR_ENTRY),
        ],
        ids=[
            "claude-root-symlink", "claude-dir-symlink-with-non-ascii-name", "absolute-target", "target-leaves-tree",
            "target-with-git-component", "entry-under-bench",
        ],
    )
    def test_the_commit_is_skipped_under_the_shapes_own_reason(self, tmp_path, symlinks, files, reason):
        repo = _make_repo(tmp_path, files, symlinks=symlinks)
        _change(repo)

        _assert_only_skipped_as(_mine(repo), reason)

    @pytest.mark.parametrize(
        ("symlink_path", "files", "reason"),
        [
            (".Claude", {}, SkipReason.CLAUDE_DIR_SYMLINK),
            (None, {".Bench/note.txt": "x"}, SkipReason.BENCH_DIR_ENTRY),
        ],
        ids=["claude-case-variant", "bench-case-variant"],
    )
    def test_the_case_variants_are_matched_case_insensitively(self, tmp_path, symlink_path, files, reason):
        repo = _make_repo(tmp_path, files, symlinks={symlink_path: "src"} if symlink_path else None)
        _change(repo)

        _assert_only_skipped_as(_mine(repo), reason)

    def test_a_non_link_entry_with_a_git_path_component_is_skipped(self, tmp_path):
        git_config_tree = {"config": "[core]\n"}
        repo = _repo_from_trees(
            tmp_path,
            {"src": {"app.py": _APP_BASE}, ".git": git_config_tree},
            {"src": {"app.py": _app_with(12)}, ".git": git_config_tree},
        )

        _assert_only_skipped_as(_mine(repo), SkipReason.GIT_PATH_COMPONENT)


class TestMissingObjects:
    """A partial clone answers `missing` for an object it lacks. Each read site skips the commit on its own."""

    @pytest.mark.parametrize(
        "missing_revision_path",
        ["HEAD:src/app.py", "HEAD:docs/reference", "HEAD~1:src/app.py"],
        ids=["post-change-blob", "base-tree-symlink-blob", "base-density-blob"],
    )
    def test_a_missing_object_at_one_read_site_skips_the_commit(self, tmp_path, monkeypatch, missing_revision_path):
        repo = _make_repo(tmp_path, symlinks={"docs/reference": "../src/app.py"})
        _change(repo)
        missing_oid = _git_text(repo, "rev-parse", missing_revision_path).strip()
        real_git = tasks._git

        def git_answering_missing_for_one_oid(repo_arg, *args, input_bytes=None):
            if args[:2] != ("cat-file", "--batch"):
                return real_git(repo_arg, *args, input_bytes=input_bytes)
            replies = []
            for oid in input_bytes.decode().split():
                replies.append(
                    f"{oid} missing\n".encode() if oid == missing_oid
                    else real_git(repo_arg, *args, input_bytes=f"{oid}\n".encode())
                )
            return b"".join(replies)

        monkeypatch.setattr(tasks, "_git", git_answering_missing_for_one_oid)

        _assert_only_skipped_as(_mine(repo), SkipReason.MISSING_OBJECT)


class TestBaseTreesExtractionAccepts:
    def test_a_nested_claude_symlink_and_an_in_tree_relative_symlink_are_kept(self, tmp_path):
        repo = _make_repo(
            tmp_path, {"shared/guide.txt": "guide\n"},
            symlinks={"pkg/.claude": "../shared", "docs/latest": "../src/app.py"},
        )
        _change(repo)

        assert len(_mine(repo).tasks) == 1

    def test_a_submodule_gitlink_adds_an_entry_but_no_bytes(self, tmp_path):
        repo = _make_repo(tmp_path, {"vendor/readme.txt": "12345"}, gitlinks={"vendor/sub": "a" * 40})
        _change(repo)

        task = _mine_one_task(repo)

        assert task.base_tree_bytes == len(_APP_BASE) + len("12345")
        assert task.base_tree_entries == 3

    def test_a_base_tree_holding_a_non_utf_8_path_is_kept_since_only_changed_paths_enter_the_task(self, tmp_path):
        legacy_tree = {"keep.txt": "x\n", b"legacy\xff.txt": "x\n"}
        repo = _repo_from_trees(tmp_path, legacy_tree, {**legacy_tree, "src": {"app.py": _app_with(12)}})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("src/app.py",)
        assert task.base_tree_entries == 2

    @pytest.mark.parametrize("directory", ["docs", _NON_ASCII_DIR], ids=["ascii", "non-ascii"])
    @pytest.mark.parametrize(
        ("target", "reason"),
        [("../src/app.py", None), ("../../outside", SkipReason.ESCAPING_SYMLINK)],
        ids=["in-tree", "leaves-tree"],
    )
    def test_a_symlink_is_classified_the_same_in_an_ascii_and_a_non_ascii_directory(
        self, tmp_path, directory, target, reason,
    ):
        repo = _make_repo(tmp_path, symlinks={f"{directory}/link": target})
        _change(repo)

        report = _mine(repo)

        if reason is None:
            assert len(report.tasks) == 1
        else:
            _assert_only_skipped_as(report, reason)


# --- extraction parity -------------------------------------------------------


def _tar_of_links(members: list[tuple[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, linkname in members:
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = linkname
            tar.addfile(member)
    return buffer.getvalue()


def _predicate_refuses(members: list[tuple[str, str | None]]) -> bool:
    return any(tasks.base_tree_refusal(name, linkname) is not None for name, linkname in members)


def _extraction_refuses(archive: bytes, dest_dir: Path) -> bool:
    try:
        fixture_repo._extract_archive(archive, dest_dir)
    except fixture_repo.UnsafeFixtureConfigError:
        return True
    return False


# Each row is a name, then its (path, link target or None) members, then whether both must refuse.
_SINGLE_MEMBER_PARITY_ROWS = [
    ("claude-root-symlink", (".claude", "x"), True),
    ("claude-case-variant-symlink", (".Claude", "x"), True),
    ("claude-dir-symlink", (".claude/skills", "x"), True),
    ("absolute-target", ("link", "/etc/hosts"), True),
    ("target-leaves-tree", ("link", "../outside"), True),
    ("target-with-git-component", ("link", "sub/.git"), True),
    ("target-with-git-case-variant", ("link", ".GIT/config"), True),
    ("entry-with-git-component", (".git/config", None), True),
    ("entry-with-git-case-variant", ("sub/.GIT/config", None), True),
    ("entry-under-bench", (".bench/note.txt", None), True),
    ("entry-under-bench-case-variant", (".Bench/note.txt", None), True),
    ("root-level-link-to-parent", ("link", ".."), True),
    ("depth-one-link-to-tree-root", ("d/link", ".."), False),
    ("depth-one-link-to-grandparent", ("d/link", "../.."), True),
    ("lookalike-github", (".github/workflows/ci.yml", None), False),
    ("lookalike-gitignore", (".gitignore", None), False),
    ("lookalike-gitattributes", (".gitattributes", None), False),
    ("lookalike-claude-plugin", (".claude-plugin/plugin.json", None), False),
    ("lookalike-benchmark", (".benchmark/note.txt", None), False),
    ("lookalike-nested-bench", ("docs/.bench/note.txt", None), False),
]


class TestExtractionParity:
    """The up-front skip must agree with what `fixture_repo._extract_archive` refuses."""

    @pytest.mark.parametrize(
        ("member", "refused"), [row[1:] for row in _SINGLE_MEMBER_PARITY_ROWS],
        ids=[row[0] for row in _SINGLE_MEMBER_PARITY_ROWS],
    )
    def test_the_skip_predicate_and_extraction_agree(self, tmp_path, member, refused):
        name, linkname = member
        archive = _tar_of([name]) if linkname is None else _tar_with_link(tarfile.SYMTYPE, name, linkname)
        dest_dir = tmp_path / "fixture"
        dest_dir.mkdir()

        assert _predicate_refuses([member]) is refused
        assert _extraction_refuses(archive, dest_dir) is refused

    def test_a_link_chain_through_a_dotdot_target_is_kept_by_the_skip_and_never_leaves_the_fixture(self, tmp_path):
        # `a` normalizes lexically to `d/e`, so the skip keeps it. In CPython 3.14.10 `tarfile._get_filtered_attrs`
        # replaces a link's target with its `os.path.normpath` form, `d/e`, on extraction. That behavior is read in
        # 3.14.10 only. A filter that does not would leave `a` resolving outside, which the prune refuses.
        members = [("d/e/f", "../.."), ("a", "d/e/f/..")]
        dest_dir = tmp_path / "fixture"
        dest_dir.mkdir()

        assert _predicate_refuses(members) is False
        if _extraction_refuses(_tar_of_links(members), dest_dir):
            return
        try:
            tasks.prune_instruction_files(dest_dir)
        except fixture_repo.UnsafeFixtureConfigError:
            return
        assert os.path.realpath(dest_dir / "a").startswith(os.path.realpath(dest_dir))


# --- source repos, authors, walking ------------------------------------------


class TestSourceRepos:
    def test_a_subdirectory_of_a_repository_is_refused_naming_the_path(self, tmp_path):
        repo = _make_repo(tmp_path)

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(str(repo / 'src'))} is not the top level"):
            _mine(repo / "src")

    def test_a_path_that_is_not_a_repository_is_refused_naming_the_path(self, tmp_path):
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(str(plain_dir))} is not the top level"):
            _mine(plain_dir)

    def test_a_bare_repository_is_refused_naming_the_path(self, tmp_path):
        bare = tmp_path / "bare.git"
        bare.mkdir()
        _git(bare, "init", "-q", "--bare")

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(str(bare))} is not the top level"):
            _mine(bare)

    def test_two_arguments_naming_one_repository_are_refused_before_any_commit_is_read(self, tmp_path):
        repo = _make_repo(tmp_path)
        alias = tmp_path / "alias"
        alias.symlink_to(repo)

        with pytest.raises(tasks.TaskSourceError, match=f"more than once: .*{re.escape(str(repo.resolve()))}"):
            _mine(repo, alias)

    def test_a_repository_recording_only_extensions_partial_clone_is_refused(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "config", "extensions.partialClone", "origin")

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(str(repo))} is a partial clone"):
            tasks._refuse_partial_clone(repo, repo)

    def test_a_linked_worktrees_top_level_is_accepted(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo)
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-q", "--detach", str(linked))

        assert len(_mine(linked).tasks) == 1

    def test_a_linked_worktree_and_its_main_checkout_are_refused_naming_the_shared_commit_and_both_repos(
        self, tmp_path,
    ):
        repo = _make_repo(tmp_path)
        commit = _change(repo)
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-q", "--detach", str(linked))
        both_repos = f"{re.escape(str(repo.resolve()))} and {re.escape(str(linked.resolve()))}"

        with pytest.raises(tasks.TaskSourceError, match=f"commit {commit} is examined from both {both_repos}"):
            _mine(repo, linked)

    def test_a_clone_and_its_origin_are_refused_naming_the_shared_commit_and_both_repos(self, tmp_path):
        repo = _make_repo(tmp_path)
        commit = _change(repo)
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", str(repo), str(clone))
        both_repos = f"{re.escape(str(repo.resolve()))} and {re.escape(str(clone.resolve()))}"

        with pytest.raises(tasks.TaskSourceError, match=f"commit {commit} is examined from both {both_repos}"):
            _mine(repo, clone)

    def test_a_failing_rev_parse_of_the_top_level_is_refused_with_gits_own_message(self, tmp_path):
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()

        refusal = f"{re.escape(str(plain_dir))} is not the top level .*git rev-parse exited 128"

        with pytest.raises(tasks.TaskSourceError, match=refusal):
            _mine(plain_dir)

    def test_a_partial_clone_is_refused_naming_the_path_and_a_full_clone_is_accepted(self, tmp_path):
        source = _make_repo(tmp_path)
        _change(source)
        _git(source, "config", "uploadpack.allowFilter", "true")
        partial_clone = tmp_path / "partial"
        full_clone = tmp_path / "full"
        _git(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{source}", str(partial_clone))
        _git(tmp_path, "clone", "-q", f"file://{source}", str(full_clone))

        with pytest.raises(tasks.TaskSourceError, match=rf"{re.escape(str(partial_clone))} is a partial clone"):
            _mine(partial_clone)
        assert len(_mine(full_clone).tasks) == 1

    def test_a_repository_whose_info_attributes_sets_an_export_attribute_is_refused_naming_the_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        info_attributes = repo / ".git" / "info" / "attributes"
        info_attributes.parent.mkdir(exist_ok=True)
        info_attributes.write_text("* export-ignore\n")

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(str(repo))}: .* sets an export attribute"):
            _mine(repo)

    def test_a_git_config_failure_other_than_no_match_is_refused_rather_than_read_as_no_match(self, tmp_path):
        repo = _make_repo(tmp_path)
        with (repo / ".git" / "config").open("a") as config_file:
            config_file.write("[remote \"origin\"\n")

        with pytest.raises(tasks.TaskSourceError, match=rf"{re.escape(str(repo))}: git config exited"):
            tasks._refuse_partial_clone(repo, repo)

    def test_an_info_attributes_that_cannot_be_read_is_refused_naming_the_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").mkdir()

        with pytest.raises(tasks.TaskSourceError, match=rf"{re.escape(str(repo))}: cannot read"):
            tasks._refuse_export_attributes_in_info_file(repo, repo)

    def test_a_failing_rev_parse_of_the_info_attributes_path_is_refused_naming_the_path(self, tmp_path):
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()

        with pytest.raises(tasks.TaskSourceError, match=rf"{re.escape(str(plain_dir))}: git rev-parse failed"):
            tasks._refuse_export_attributes_in_info_file(plain_dir, plain_dir)


class TestTrustedAuthors:
    @pytest.mark.parametrize("trusted", [(), ("",), (TRUSTED, "")], ids=["none", "empty", "one-empty"])
    def test_an_empty_or_absent_trusted_author_is_refused(self, tmp_path, trusted):
        repo = _make_repo(tmp_path)

        with pytest.raises(tasks.TaskSourceError, match="trusted-author"):
            _mine(repo, trusted=trusted)

    def test_a_mailmap_cannot_map_an_untrusted_email_onto_a_trusted_one(self, tmp_path):
        attacker = "attacker@example.com"
        repo = _make_repo(tmp_path, {".mailmap": f"Trusted Dev <{TRUSTED}> <{attacker}>\n"})
        _change(repo, author=attacker)

        report = _mine(repo)

        _assert_only_skipped_as(report, SkipReason.UNTRUSTED_AUTHOR)
        assert report.author_counts[attacker] == 1
        assert any(attacker in line for line in tasks.format_mining_summary(report))


    def test_a_mailmap_cannot_map_an_untrusted_merged_author_onto_a_trusted_one(self, tmp_path):
        attacker = "attacker@example.com"
        repo = _make_repo(tmp_path, {".mailmap": f"Trusted Dev <{TRUSTED}> <{attacker}>\n"})
        _git(repo, "switch", "-q", "-c", "feature")
        _change(repo, message="Attacker helper", author=attacker)
        _git(repo, "switch", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

        _assert_only_skipped_as(_mine(repo), SkipReason.UNTRUSTED_MERGED_AUTHOR)


class TestLogFields:
    def test_a_commit_message_holding_the_unit_separator_reaches_the_brief_unchanged(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, message="Add helpers\n\nunit\x1fseparator in the body")

        assert _mine_one_task(repo).brief == "Add helpers\n\nunit\x1fseparator in the body"

    def test_an_author_email_holding_a_control_character_is_refused_naming_the_repo_and_commit(self, tmp_path):
        repo = _make_repo(tmp_path)
        commit = _change(repo, author=f"{TRUSTED}\x1fattacker@example.com")

        with pytest.raises(
            tasks.TaskSourceError,
            match=rf"{re.escape(str(repo.resolve()))}: commit {commit} has a control character in its author",
        ):
            _mine(repo)

    def test_a_committer_email_holding_a_control_character_is_refused_naming_the_repo_and_commit(self, tmp_path):
        repo = _make_repo(tmp_path)
        commit = _change(repo, committer=f"{TRUSTED}\x1fattacker@example.com")

        with pytest.raises(tasks.TaskSourceError, match=rf"commit {commit} has a control character in its committer"):
            _mine(repo)

    def test_a_merged_commit_whose_author_holds_a_control_character_is_refused_naming_the_merge_commit(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "switch", "-q", "-c", "feature")
        _change(repo, message="Add the first helper", author=f"{TRUSTED}\x1fattacker@example.com")
        _git(repo, "switch", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
        merge_commit = _git_text(repo, "rev-parse", "HEAD").strip()

        with pytest.raises(tasks.TaskSourceError, match=rf"commit {merge_commit} has a control character in its merged_author"):
            _mine(repo)

    @pytest.mark.parametrize("control_character", ["\t", "\x1f"], ids=["tab", "unit-separator"])
    def test_a_merged_commit_subject_holding_a_control_character_is_kept_as_it_is(self, tmp_path, control_character):
        repo = _make_repo(tmp_path)
        _git(repo, "switch", "-q", "-c", "feature")
        _change(repo, message=f"Add{control_character}the first helper")
        _git(repo, "switch", "-q", "-")
        _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

        assert f"- Add{control_character}the first helper" in _mine_one_task(repo).brief


class TestLogFieldFraming:
    def test_log_output_whose_field_count_is_not_a_multiple_of_the_record_width_is_refused_naming_the_repo(self, tmp_path):
        refusal = f"{re.escape(str(tmp_path))}: git log output holds 3 fields, not a multiple of 2"

        with pytest.raises(tasks.TaskSourceError, match=refusal):
            tasks._log_fields(tmp_path, b"first\0second\0third\0", 2)


class TestRepoLocalDiffConfig:
    """`isolated_git_environment` strips only global and system config, so a repo's own diff settings still apply."""

    @pytest.mark.parametrize("setting", ["diff.noprefix", "diff.mnemonicPrefix"])
    def test_a_repo_local_diff_prefix_setting_leaves_the_measured_metrics_unchanged(self, tmp_path, setting):
        repo = _make_repo(tmp_path)
        _git(repo, "config", setting, "true")
        _change(repo, {"b/x.py": "".join(f"value_{n} = {n}\n" for n in range(12))})

        task = _mine_one_task(repo)

        assert task.changed_paths == ("b/x.py",)
        assert task.reference_metrics.changed_paths == ["b/x.py"]
        assert task.reference_metrics.line_counts["non_test"][LineClass.CODE.value] == 12

    def test_a_repo_local_inter_hunk_context_does_not_turn_unchanged_lines_into_added_ones(self, tmp_path):
        base_lines = [f"value_{n} = {n}" for n in range(8)]
        repo = _make_repo(tmp_path, {"src/lib.py": "\n".join(base_lines) + "\n"})
        _git(repo, "config", "diff.interHunkContext", "8")
        edited_lines = ["value_0 = 100", *base_lines[1:6], "value_6 = 600", base_lines[7]]
        _change(repo, {"src/lib.py": "\n".join(edited_lines) + "\n"})

        task = _mine_one_task(repo, min_code_lines=1)

        assert task.reference_metrics.line_counts["non_test"][LineClass.CODE.value] == 2

    def test_a_repo_local_ignore_submodules_setting_does_not_hide_a_gitlink_bump(self, tmp_path):
        repo = _repo_for_non_additive_commit(tmp_path)
        _git(repo, "config", "diff.ignoreSubmodules", "all")
        _commit_with_non_additive_changes(repo)

        task = _mine_one_task(repo)

        assert "vendor/lib" in task.changed_paths
        assert task.reference_metrics.changed_paths == list(task.changed_paths)
        assert task.reference_metrics.non_file_entries == 1

    def test_a_repo_local_diff_algorithm_does_not_change_which_lines_count_as_added(self, tmp_path):
        base_text = "".join(f"{line}\n" for line in ("e = 5", "e = 5", "b = 2", "c = 3", "e = 5", "d = 4"))
        edited_text = "".join(
            f"{line}\n" for line in ("a = 1", "e = 5", "a = 1", "d = 4", "c = 3", "e = 5", "b = 2", "b = 2", "f = 6")
        )
        repo = _make_repo(tmp_path, {"src/lib.py": base_text})
        _git(repo, "config", "diff.algorithm", "patience")
        commit = _change(repo, {"src/lib.py": edited_text})

        def diff_with(algorithm: str) -> str:
            return _git_text(repo, "diff", "-U0", "--indent-heuristic", f"--diff-algorithm={algorithm}", f"{commit}~1", commit)

        assert diff_with("myers") != diff_with("patience")

        task = _mine_one_task(repo, min_code_lines=1)

        # Myers adds six of the nine lines. Patience adds seven.
        assert task.reference_metrics.line_counts["non_test"][LineClass.CODE.value] == 6


class TestIsolatedGitEnvironment:
    def test_every_git_call_mining_makes_runs_under_the_isolated_environment_despite_an_inherited_git_dir(
        self, tmp_path, monkeypatch,
    ):
        repo = _make_repo(tmp_path)
        _change(repo)
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "decoy.git"))
        calls = _record_subprocess_run_calls(monkeypatch)

        _mine_one_task(repo)

        assert calls
        for call in calls:
            assert call.get("env") == local_git.isolated_git_environment()
            assert "GIT_DIR" not in call["env"]


class TestPerCommitFailures:
    def test_a_diff_the_metrics_cannot_parse_is_reported_with_the_repo_and_commit(self, tmp_path, monkeypatch):
        repo = _make_repo(tmp_path)
        commit = _change(repo)

        def measure_diff_rejecting_the_listing(diff_text, raw_listing, blob_by_path):
            raise ValueError("raw listing does not name src/app.py")

        monkeypatch.setattr(tasks.comment_metrics, "measure_diff", measure_diff_rejecting_the_listing)

        with pytest.raises(
            tasks.TaskSourceError, match=rf"{re.escape(str(repo.resolve()))}: commit {commit}: raw listing does not name",
        ):
            _mine(repo)

    def test_a_missing_tree_object_is_reported_with_the_repo_and_commit(self, tmp_path):
        repo = _make_repo(tmp_path)
        commit = _change(repo)
        tree_oid = _git_text(repo, "rev-parse", "HEAD^{tree}").strip()
        tree_object = repo / ".git" / "objects" / tree_oid[:2] / tree_oid[2:]
        tree_object.chmod(0o644)
        tree_object.unlink()

        with pytest.raises(tasks.TaskSourceError, match=rf"{re.escape(str(repo.resolve()))}: commit {commit}: git exited"):
            _mine(repo)


class TestRev:
    @pytest.mark.parametrize("rev", ["--all", "-n1", "a..b", ""])
    def test_a_rev_that_is_not_a_base_ref_is_refused_before_git_runs(self, tmp_path, rev):
        repo = _make_repo(tmp_path)

        with pytest.raises(tasks.TaskSourceError, match="invalid base ref"):
            _mine(repo, rev=rev)

    def test_a_rev_that_does_not_exist_is_refused_naming_the_repo_and_the_rev(self, tmp_path):
        repo = _make_repo(tmp_path)

        with pytest.raises(tasks.TaskSourceError, match=rf"{re.escape(str(repo.resolve()))}: git log no-such-branch failed"):
            _mine(repo, rev="no-such-branch")

    def test_a_rev_named_like_a_tracked_path_is_read_as_the_rev(self, tmp_path):
        repo = _make_repo(tmp_path, {"side": "tracked file\n"})
        _git(repo, "branch", "side")
        _change(repo)

        assert _mine(repo, rev="side").tasks == []

    def test_a_branch_name_is_walked_in_place_of_head(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "branch", "side")
        _change(repo)

        assert _mine(repo, rev="side").tasks == []
        assert len(_mine(repo, rev="HEAD").tasks) == 1


class TestWalk:
    @staticmethod
    def _repo_with_new_file_commits(tmp_path: Path, name: str, count: int) -> tuple[Path, list[str]]:
        # A base file named for the repo keeps two repos' commit ids apart whatever the commit clock reads.
        repo = _make_repo(tmp_path, {"owner.txt": name}, name=name)
        commits = [
            _change(repo, {f"src/module_{n}.py": "".join(f"value_{i} = {i}\n" for i in range(12))}, message=f"Add {n}")
            for n in range(count)
        ]
        return repo, commits

    def test_max_tasks_keeps_each_repos_own_newest_commits(self, tmp_path):
        first_repo, first_commits = self._repo_with_new_file_commits(tmp_path, "first", 3)
        second_repo, second_commits = self._repo_with_new_file_commits(tmp_path, "second", 3)

        report = _mine(first_repo, second_repo, max_tasks=2)

        assert [task.commit for task in report.tasks] == [
            first_commits[2], first_commits[1], second_commits[2], second_commits[1],
        ]

    @pytest.mark.parametrize("max_count", [3, 2])
    def test_max_count_bounds_the_commits_examined_to_the_newest_ones(self, tmp_path, max_count):
        repo, commits = self._repo_with_new_file_commits(tmp_path, "repo", 5)

        report = _mine(repo, max_count=max_count)

        assert [task.commit for task in report.tasks] == commits[::-1][:max_count]
        assert sum(report.author_counts.values()) == max_count
        assert dict(report.skip_counts) == {}

    def test_progress_lines_name_each_repo_every_interval_and_when_it_finishes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks, "PROGRESS_INTERVAL_COMMITS", 2)
        first_repo, _ = self._repo_with_new_file_commits(tmp_path, "first", 3)
        second_repo, _ = self._repo_with_new_file_commits(tmp_path, "second", 1)
        lines: list[str] = []

        tasks.mine_tasks([first_repo, second_repo], [TRUSTED], progress=lines.append)

        assert lines == [
            f"{first_repo.resolve()}: examined 2 commits, kept 2",
            f"{first_repo.resolve()}: examined 4 commits, kept 3",
            f"{first_repo.resolve()}: finished, examined 4 commits, kept 3",
            f"{second_repo.resolve()}: examined 2 commits, kept 1",
            f"{second_repo.resolve()}: finished, examined 2 commits, kept 1",
        ]

    def test_the_summary_names_the_largest_kept_task_by_size_and_by_entry_count(self, tmp_path):
        many_small_files = {f"small/file_{n}.txt": "x" for n in range(5)}
        entries_repo = _make_repo(tmp_path, many_small_files, name="entries")
        _change(entries_repo)
        size_repo = _make_repo(tmp_path, {"big.dat": "x" * 5000}, name="size")
        _change(size_repo)

        report = _mine(entries_repo, size_repo)
        summary = tasks.format_mining_summary(report)

        entries_task, size_task = report.tasks
        assert f"largest kept task by base-tree size: {size_task.id} ({size_task.base_tree_bytes} bytes, 2 entries)" in summary
        assert (
            f"largest kept task by base-tree entry count: {entries_task.id} "
            f"({entries_task.base_tree_bytes} bytes, 6 entries)"
        ) in summary

    def test_the_summary_counts_skips_by_reason_and_lists_raw_identities(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, author=UNTRUSTED)
        _change(repo, {"src/extra.py": "".join(f"value_{n} = {n}\n" for n in range(12))})

        summary = tasks.format_mining_summary(_mine(repo))

        assert "kept 1 tasks, skipped 2 commits" in summary
        assert "  skipped untrusted-author: 1" in summary
        assert f"  {UNTRUSTED!r}: 1" in summary
        assert f"  {TRUSTED!r}: 2" in summary


# --- task files --------------------------------------------------------------


class TestTaskFiles:
    @staticmethod
    def _mined_task(tmp_path: Path) -> tasks.ReplayTask:
        repo = _make_repo(tmp_path)
        _change(repo)
        return _mine_one_task(repo)

    def test_a_written_task_set_reads_back_unchanged(self, tmp_path):
        task = self._mined_task(tmp_path)
        tasks_dir = tmp_path / "tasks"

        path = tasks.write_task_set(tasks_dir, "first-set", [task])

        assert path == tasks_dir / "first-set.json"
        assert tasks.read_task_set(tasks_dir, "first-set") == [task]

    @pytest.mark.parametrize("name", ["../escape", "a/b", ".hidden", ""])
    def test_a_task_set_name_that_is_not_a_campaign_id_is_refused_on_write_and_read(self, tmp_path, name):
        with pytest.raises(identifiers.InvalidIdentifierError):
            tasks.write_task_set(tmp_path / "tasks", name, [])
        with pytest.raises(identifiers.InvalidIdentifierError):
            tasks.read_task_set(tmp_path / "tasks", name)

    @pytest.mark.parametrize(
        "damage",
        [
            lambda data: data.update(commit="z" * 40),
            lambda data: data.update(base_commit="abc"),
            lambda data: data.update(id="not-an-id"),
            lambda data: data.pop("brief"),
            lambda data: data.update(changed_paths="src/app.py"),
            lambda data: data.update(changed_paths=[1]),
            lambda data: data.pop("reference_metrics"),
            lambda data: data.update(reference_metrics={}),
            lambda data: data.update(source_repo="/another/repo"),
            lambda data: data.update(commit="f" * 40),
        ],
        ids=[
            "non-hex-commit", "short-base-commit", "bad-id-grammar", "missing-key", "paths-not-a-list", "path-not-a-string",
            "missing-metrics",
            "malformed-metrics", "id-not-derived-from-source-repo", "id-not-derived-from-commit",
        ],
    )
    def test_a_task_that_breaks_the_task_grammar_is_refused_naming_the_file(self, tmp_path, damage):
        task_dict = self._mined_task(tmp_path).to_dict()
        damage(task_dict)
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "broken.json").write_text(json.dumps([task_dict]))

        with pytest.raises(tasks.TaskFileError, match=re.escape(str(tasks_dir / "broken.json"))):
            tasks.read_task_set(tasks_dir, "broken")

    def test_a_nested_value_of_the_wrong_shape_is_refused_as_a_value_error_naming_the_task(self, tmp_path):
        task_dict = self._mined_task(tmp_path).to_dict()
        task_dict["reference_metrics"] = {}

        with pytest.raises(ValueError, match=f"task {task_dict['id']} holds a value of the wrong shape"):
            tasks.ReplayTask.from_dict(task_dict)

    def test_an_id_that_is_not_the_one_derived_from_source_repo_and_commit_is_refused(self, tmp_path):
        task_dict = self._mined_task(tmp_path).to_dict()
        derived_id = task_dict["id"]
        task_dict["source_repo"] = "/another/repo"

        with pytest.raises(ValueError, match=f"task ID {derived_id} is not "):
            tasks.ReplayTask.from_dict(task_dict)

    def test_a_task_set_that_does_not_exist_is_refused_naming_the_file(self, tmp_path):
        with pytest.raises(tasks.TaskFileError, match=re.escape(str(tmp_path / "tasks" / "typo.json"))):
            tasks.read_task_set(tmp_path / "tasks", "typo")

    def test_a_task_set_holding_two_tasks_with_one_id_is_refused_and_not_written(self, tmp_path):
        task = self._mined_task(tmp_path)
        tasks_dir = tmp_path / "tasks"

        with pytest.raises(tasks.TaskFileError, match=f"duplicate task IDs.*{task.id}"):
            tasks.write_task_set(tasks_dir, "twice", [task, task])

        assert not (tasks_dir / "twice.json").exists()

    def test_a_task_file_holding_two_tasks_with_one_id_is_refused_naming_the_file(self, tmp_path):
        task = self._mined_task(tmp_path)
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "twice.json").write_text(json.dumps([task.to_dict(), task.to_dict()]))

        task_file_path = re.escape(str(tasks_dir / "twice.json"))
        with pytest.raises(tasks.TaskFileError, match=f"{task_file_path}: .*duplicate task IDs.*{task.id}"):
            tasks.read_task_set(tasks_dir, "twice")

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("source_repo", None), ("brief", None), ("author_email", ["dev@example.com"]), ("committer_email", 7),
            ("base_tree_bytes", 1.5), ("base_tree_entries", True), ("base_tree_bytes", "10"),
            ("base_density", True), ("base_density", "2.5"), ("base_density", [1]),
        ],
        ids=[
            "null-source-repo", "null-brief", "list-author-email", "int-committer-email", "fractional-byte-count",
            "bool-entry-count", "string-byte-count", "bool-density", "string-density", "list-density",
        ],
    )
    def test_a_field_of_the_wrong_type_is_refused_naming_the_field(self, tmp_path, field_name, value):
        task_dict = self._mined_task(tmp_path).to_dict()
        task_dict[field_name] = value

        with pytest.raises(ValueError, match=f"{field_name} must be"):
            tasks.ReplayTask.from_dict(task_dict)

    @pytest.mark.parametrize("density", [None, 0, 2, 2.5], ids=["null", "int-zero", "int", "float"])
    def test_a_null_integer_or_float_density_is_accepted(self, tmp_path, density):
        task_dict = self._mined_task(tmp_path).to_dict()
        task_dict["base_density"] = density

        assert tasks.ReplayTask.from_dict(task_dict).base_density == density

    @pytest.mark.parametrize("content", ["not json", '{"tasks": []}', "[1]"], ids=["unparseable", "not-a-list", "not-an-object"])
    def test_a_task_file_of_the_wrong_overall_shape_is_refused(self, tmp_path, content):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "broken.json").write_text(content)

        with pytest.raises(tasks.TaskFileError, match=re.escape(str(tasks_dir / "broken.json"))):
            tasks.read_task_set(tasks_dir, "broken")


# --- prompt ------------------------------------------------------------------


class TestInnerPrompt:
    def test_the_prompt_lists_the_changed_paths_and_wraps_the_brief_in_begin_and_end_lines(self, tmp_path):
        repo = _make_repo(tmp_path)
        _change(repo, {**QUALIFYING_CHANGE, "src/second.py": "".join(f"v{i} = {i}\n" for i in range(3))}, message="Add helpers")

        prompt = tasks.build_inner_prompt(_mine_one_task(repo))

        assert "The original change touched these paths: src/app.py, src/second.py. " in prompt
        assert prompt.endswith("\n\nBEGIN CHANGE DESCRIPTION\nAdd helpers\nEND CHANGE DESCRIPTION")


# --- prune -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "pruned"),
    [
        ("CLAUDE.md", True), ("sub/claude.LOCAL.md", True), ("a/Agents.md", True), ("CLAUDE.md/x.txt", True),
        (".mcp.json", True), (".MCP.JSON", True), ("sub/.mcp.json", False), (".mcp.json/x.js", True),
        ("sub/.mcp.json/x.js", False),
        (".claude/settings.json", True), ("a/.Claude/agents/x.md", True), (".claude", False), ("sub/.claude", False),
        ("src/rules/x.md", False), ("agents/x.md", False), ("skills/x.md", False), (".claude-plugin/x.json", False),
    ],
)
def test_is_pruned_path_matches_what_the_prune_removes(path, pruned):
    assert tasks.is_pruned_path(path) is pruned


# Each row is a path, then the kind of its last component. Every directory above it is a plain directory.
_PRUNE_PARITY_ROWS = [
    ("CLAUDE.md", "file"), ("CLAUDE.md", "directory"), ("sub/claude.LOCAL.md", "file"), ("a/Agents.md", "symlink"),
    ("CLAUDE.md/x.txt", "file"),
    (".mcp.json", "file"), (".mcp.json", "directory"), (".MCP.JSON", "symlink"), (".mcp.json/x.js", "file"),
    ("sub/.mcp.json", "file"), ("sub/.mcp.json", "directory"), ("sub/.mcp.json", "symlink"),
    (".claude", "file"), (".claude", "directory"), (".claude", "symlink"),
    ("pkg/.Claude", "file"), ("pkg/.Claude", "directory"), ("pkg/.Claude", "symlink"),
    (".claude/settings.json", "file"), ("a/.Claude/agents/x.md", "file"),
    ("src/rules/x.md", "file"), ("agents/x.md", "file"), (".claude-plugin/x.json", "file"),
]


@pytest.mark.parametrize(("path", "kind"), _PRUNE_PARITY_ROWS, ids=[f"{path}-{kind}" for path, kind in _PRUNE_PARITY_ROWS])
def test_is_pruned_path_agrees_with_a_real_prune_for_a_file_a_directory_and_a_symlink(tmp_path, path, kind):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "keep.txt").write_text("keep\n")
    leaf = fixture / path
    leaf.parent.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        leaf.write_text("x\n")
    elif kind == "directory":
        leaf.mkdir()
    else:
        os.symlink(fixture / "keep.txt", leaf)

    tasks.prune_instruction_files(fixture)

    assert tasks.is_pruned_path(path, is_directory_or_link=kind != "file") is (not os.path.lexists(leaf))


class TestPruneOnAPlainDirectory:
    """Links here point at sentinels under `tmp_path`, which a mined base tree could not hold."""

    def test_symlinked_instruction_entries_lose_the_link_and_keep_the_target(self, tmp_path):
        sentinel_file = tmp_path / "sentinel.md"
        sentinel_file.write_text("keep\n")
        sentinel_dir = tmp_path / "sentinel-dir"
        sentinel_dir.mkdir()
        (sentinel_dir / "inside.md").write_text("keep\n")
        fixture = tmp_path / "fixture"
        (fixture / ".claude").mkdir(parents=True)
        os.symlink(sentinel_file, fixture / "CLAUDE.md")
        os.symlink(sentinel_dir, fixture / ".claude" / "skills")
        (fixture / "pkg").mkdir()
        os.symlink(sentinel_dir, fixture / "pkg" / ".claude")

        tasks.prune_instruction_files(fixture)

        assert sorted(path.name for path in fixture.iterdir()) == ["pkg"]
        assert list((fixture / "pkg").iterdir()) == []
        assert sentinel_file.read_text() == "keep\n"
        assert (sentinel_dir / "inside.md").read_text() == "keep\n"

    def test_a_link_chain_that_resolves_outside_the_fixture_makes_the_prune_raise(self, tmp_path):
        fixture = tmp_path / "fixture"
        (fixture / "d" / "e").mkdir(parents=True)
        os.symlink("../..", fixture / "d" / "e" / "f")
        os.symlink("d/e/f/..", fixture / "a")

        with pytest.raises(fixture_repo.UnsafeFixtureConfigError, match="resolves outside"):
            tasks.prune_instruction_files(fixture)

    def test_symlink_loops_inside_the_fixture_pass_the_realpath_check(self, tmp_path):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        os.symlink("self", fixture / "self")
        os.symlink("two", fixture / "one")
        os.symlink("one", fixture / "two")

        tasks.prune_instruction_files(fixture)

        assert sorted(path.name for path in fixture.iterdir()) == ["one", "self", "two"]

    def test_a_link_to_a_directory_holding_an_instruction_file_keeps_the_link_and_loses_the_file(self, tmp_path):
        fixture = tmp_path / "fixture"
        (fixture / "docs").mkdir(parents=True)
        (fixture / "docs" / "CLAUDE.md").write_text("rules\n")
        os.symlink("docs", fixture / "alias")

        tasks.prune_instruction_files(fixture)

        assert (fixture / "alias").is_symlink()
        assert list((fixture / "docs").iterdir()) == []

    def test_a_dangling_link_that_stays_inside_the_fixture_is_kept(self, tmp_path):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        os.symlink("missing/target", fixture / "dangling")

        tasks.prune_instruction_files(fixture)

        assert (fixture / "dangling").is_symlink()


# --- task fixture ------------------------------------------------------------


def _make_source_repo(tmp_path: Path, *args, **kwargs) -> Path:
    """A source repository under its own directory, so the fixture can be built beside it in `tmp_path`."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    return _make_repo(source_dir, *args, **kwargs)


def _build_fixture(tmp_path: Path, task: tasks.ReplayTask, timeout_s: float = FIXTURE_TIMEOUT_S) -> tuple[Path, Path, str]:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    baseline = tmp_path / "attempt" / "baseline.git"
    commit = tasks.build_task_fixture(
        task, fixture, baseline, bare_writer_markdown=BARE_WRITER_MARKDOWN, timeout_s=timeout_s,
    )
    return fixture, baseline, commit


def _tracked_paths(fixture: Path) -> list[str]:
    return _git_text(fixture, "ls-tree", "-r", "--name-only", "-z", "HEAD").split("\0")[:-1]


class TestBuildTaskFixture:
    @pytest.mark.parametrize(
        "pruned_name",
        [
            "CLAUDE.md", "claude.md", "CLAUDE.local.md", "claude.LOCAL.md", "AGENTS.md", "agents.md",
            ".mcp.json", ".MCP.JSON", ".Claude/settings.json", "sub/deep/.claude/agents/x.md", "sub/dir/CLAUDE.md",
        ],
    )
    def test_each_pruned_name_is_absent_from_the_work_tree_and_the_fixture_commit(self, tmp_path, pruned_name):
        repo = _make_source_repo(tmp_path, {pruned_name: "rules\n"})
        _change(repo)
        task = _mine_one_task(repo)

        fixture, _baseline, _commit = _build_fixture(tmp_path, task)

        assert not os.path.lexists(fixture / pruned_name)
        assert pruned_name not in _tracked_paths(fixture)
        assert "src/app.py" in _tracked_paths(fixture)

    def test_lookalike_paths_outside_the_prune_are_kept(self, tmp_path):
        lookalikes = {"src/rules/style.md": "r\n", "agents/helper.md": "a\n", "skills/helper.md": "s\n", "sub/.mcp.json": "{}\n"}
        repo = _make_source_repo(tmp_path, lookalikes)
        _change(repo)

        fixture, _baseline, _commit = _build_fixture(tmp_path, _mine_one_task(repo))

        assert set(lookalikes) <= set(_tracked_paths(fixture))

    def test_a_nested_claude_link_is_unlinked_and_an_in_tree_relative_link_stays(self, tmp_path):
        repo = _make_source_repo(
            tmp_path, {"shared/guide.txt": "guide\n"},
            symlinks={"pkg/.claude": "../shared", "docs/latest": "../src/app.py"},
        )
        _change(repo)

        fixture, _baseline, _commit = _build_fixture(tmp_path, _mine_one_task(repo))

        assert not os.path.lexists(fixture / "pkg" / ".claude")
        assert os.readlink(fixture / "docs" / "latest") == "../src/app.py"
        assert _git_text(fixture, "status", "--porcelain") == ""

    def test_the_fixture_holds_one_commit_and_writes_its_extras_outside_git(self, tmp_path):
        repo = _make_source_repo(tmp_path)
        change_commit = _change(repo)
        task = _mine_one_task(repo)

        fixture, baseline, commit = _build_fixture(tmp_path, task)

        assert _git_text(fixture, "log", "--all", "--format=%H").split() == [commit]
        assert change_commit not in _git_text(fixture, "log", "--all", "--format=%H")
        assert (fixture / ".claude" / "worktree-optout").is_file()
        assert (fixture / ".claude" / "agents" / "bare-writer.md").read_text() == BARE_WRITER_MARKDOWN
        assert _git_text(fixture, "status", "--porcelain") == ""
        assert (fixture / "src" / "app.py").read_text() == _APP_BASE
        baseline_head = _git_text(fixture, f"--git-dir={baseline}", "rev-parse", "HEAD").strip()
        assert baseline_head == commit

    def test_timeout_s_reaches_every_git_call_the_build_makes(self, tmp_path, monkeypatch):
        repo = _make_source_repo(tmp_path)
        _change(repo)
        task = _mine_one_task(repo)
        calls = _record_subprocess_run_calls(monkeypatch)

        _build_fixture(tmp_path, task, timeout_s=FIXTURE_TIMEOUT_S)

        timeouts = [call.get("timeout") for call in calls]
        assert timeouts
        assert set(timeouts) == {FIXTURE_TIMEOUT_S}

    def test_every_git_call_the_build_makes_runs_under_the_isolated_environment_despite_an_inherited_git_dir(
        self, tmp_path, monkeypatch,
    ):
        repo = _make_source_repo(tmp_path)
        _change(repo)
        task = _mine_one_task(repo)
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "decoy.git"))
        calls = _record_subprocess_run_calls(monkeypatch)

        _build_fixture(tmp_path, task)

        assert calls
        for call in calls:
            # The fixture commit call adds its fixed author and committer identity to the isolated environment.
            env_without_identity = {key: value for key, value in call["env"].items() if key not in fixture_repo._git_commit_env()}
            assert env_without_identity == local_git.isolated_git_environment()
            assert "GIT_DIR" not in call["env"]


    def test_a_root_dot_claude_regular_file_fails_the_build_as_an_os_error(self, tmp_path):
        repo = _make_source_repo(tmp_path, {".claude": "old\n"})
        _change(repo, {**QUALIFYING_CHANGE, ".claude": "new\n"})
        task = _mine_one_task(repo)

        with pytest.raises(OSError):
            _build_fixture(tmp_path, task)

    def test_a_source_repo_that_became_a_partial_clone_after_mining_is_refused_before_anything_is_built(self, tmp_path):
        repo = _make_source_repo(tmp_path)
        _change(repo)
        task = _mine_one_task(repo)
        _git(repo, "config", "remote.origin.promisor", "true")

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(task.source_repo)} is a partial clone"):
            _build_fixture(tmp_path, task)

        assert list((tmp_path / "fixture").iterdir()) == []

    def test_a_source_repo_that_gained_an_export_attribute_after_mining_is_refused_before_anything_is_built(self, tmp_path):
        repo = _make_source_repo(tmp_path)
        _change(repo)
        task = _mine_one_task(repo)
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("src export-ignore\n")

        with pytest.raises(tasks.TaskSourceError, match=f"{re.escape(task.source_repo)}: .* sets an export attribute"):
            _build_fixture(tmp_path, task)

        assert list((tmp_path / "fixture").iterdir()) == []


# --- private data stays out of the public repository -------------------------

_EVALS_DIR = Path(__file__).resolve().parent


class TestLocalDataIsIgnored:
    """Task files hold other repositories' commit text and run records hold code diffs, so only
    `evals/replay_bench/.local/` keeps them out of this public repository."""

    @staticmethod
    def _is_ignored(repo_relative_path: str) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", repo_relative_path], cwd=_EVALS_DIR.parent, capture_output=True,
            env=local_git.isolated_git_environment(),
        )
        assert result.returncode in (0, 1), result.stderr
        return result.returncode == 0

    @pytest.mark.parametrize("local_path", ["tasks/x", "tasks/x.json", "campaigns/x", "campaigns/x/attempts/y/baseline.git"])
    def test_every_path_the_runner_writes_under_local_is_ignored(self, local_path):
        assert self._is_ignored(f"evals/replay_bench/.local/{local_path}")

    def test_a_path_beside_local_is_not_ignored(self):
        assert not self._is_ignored("evals/replay_bench/x.json")
