"""Tests for findings-path-suffix.sh.

This script's only external dependency is git, and it sources no
../hooks/_lib.sh (unlike pr-cost-section.sh), so it is invoked directly
against a real repo built from conftest.py's helpers.

The script prints a bare `<epoch>-<slug>` suffix; the caller splices in the
agent name and `.md` extension to form the full `findings_path`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from helpers import SKILLS_DIR

from .conftest import _commit, _init_repo, _make_feature_branch, _make_repo_with_remote, _make_worktree

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "findings-path-suffix.sh"

# Requires a non-empty slug: the well-formed-branch shape every test below
# uses except TestNonUtf8BranchNameEntirelyInvalid, which has its own local
# pattern for the one case where the slug can legitimately be empty.
_BARE_SUFFIX_RE = re.compile(r"^[0-9]+-[A-Za-z0-9-]{1,20}$")

# A branch name stripped to zero `[A-Za-z0-9-]` bytes produces exactly this
# empty-slug shape -- legitimate only for TestNonUtf8BranchNameEntirelyInvalid,
# whose two inputs are provably all-stripped, not merely permitted to be.
_EMPTY_SLUG_SUFFIX_RE = re.compile(r"^[0-9]+-$")

# The findings_path template as it appears in code-review/SKILL.md's dispatch
# contract -- kept in sync with test_skills.py's _FINDINGS_PATH_RECIPE_TOKENS,
# which pins the same literal for the same reason.
_CODE_REVIEW_SKILL_MD = SKILLS_DIR / "code-review" / "SKILL.md"
_FINDINGS_PATH_TEMPLATE = "agent-reviews/<agent-name>-<suffix>.md"

# ready-for-review/SKILL.md hardcodes the agent name into its own template
# rather than reusing code-review's generic <agent-name> placeholder.
_READY_FOR_REVIEW_SKILL_MD = SKILLS_DIR / "ready-for-review" / "SKILL.md"
_READY_FOR_REVIEW_FINDINGS_PATH_TEMPLATE = "agent-reviews/skill-fidelity-reviewer-<suffix>.md"


def _run_script(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_SCRIPT)], cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _exclude_lines(repo: Path) -> list[str]:
    return (repo / ".git" / "info" / "exclude").read_text().splitlines()


class TestIdempotentAppendAndSuffixShape:
    """Two runs against a seeded repo: the append to info/exclude must be
    genuinely idempotent (exactly one `agent-reviews/` line survives), and
    the printed suffix must resolve to the documented `<epoch>-<slug>` shape.

    Seeded with one commit before invoking the script: on an unborn HEAD,
    `git rev-parse --verify HEAD` fails, so an unseeded repo would exercise
    the guard's failure path instead of the documented happy-path shape.
    """

    def test_two_runs_leave_exactly_one_ignore_line_and_valid_suffix(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        first = _run_script(repo)
        assert first.returncode == 0
        second = _run_script(repo)
        assert second.returncode == 0

        exclude_lines = _exclude_lines(repo)
        assert exclude_lines.count("agent-reviews/") == 1, (
            f"info/exclude carries {exclude_lines.count('agent-reviews/')} 'agent-reviews/' "
            f"lines after two runs — the append is not idempotent: {exclude_lines!r}"
        )

        suffix = second.stdout.strip()
        assert _BARE_SUFFIX_RE.fullmatch(suffix), (
            f"script output {suffix!r} does not match the documented <epoch>-<slug> shape"
        )


class TestSlugTruncationAcrossBranches:
    """A branch name longer than 20 characters and containing a `/` exercises
    two boundaries the default-branch case above leaves untested:

    1. Against the short, slash-free default branch, the `cut -c1-20`
       truncation boundary is never approached, so a regression to
       `-c1-21` or a dropped `cut` step would still pass.
    2. The default branch also never contains a `/`, so a regression that
       breaks or drops the `tr '/' '-'` step is likewise invisible.

    Also re-checks idempotency after a branch switch, since the append
    target does not change with the branch.
    """

    def test_truncates_and_stays_idempotent_on_long_slashed_branch(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")
        _run_script(repo)  # seed the ignore line, mirroring real first-round usage

        long_branch_name = "feature/a-very-long-branch-name-here"
        expected_slug = long_branch_name.replace("/", "-")[:20]
        subprocess.run(["git", "checkout", "-q", "-b", long_branch_name], cwd=repo, check=True)

        result = _run_script(repo)
        assert result.returncode == 0

        exclude_lines = _exclude_lines(repo)
        assert exclude_lines.count("agent-reviews/") == 1, (
            f"info/exclude carries {exclude_lines.count('agent-reviews/')} 'agent-reviews/' "
            f"lines after a run on a different branch — not idempotent across branches: {exclude_lines!r}"
        )

        suffix = result.stdout.strip()
        match = re.fullmatch(r"(\d+)-([A-Za-z0-9-]+)", suffix)
        assert match, f"script output {suffix!r} does not match the documented <epoch>-<slug> shape"
        assert match.group(2) == expected_slug, (
            f"derived slug {match.group(2)!r} from branch {long_branch_name!r}, expected the exact "
            f"20-character, slash-free truncation {expected_slug!r} — 'tr' and 'cut' must both run, "
            "in that order, on the full branch name"
        )


class TestRecombinationWithDispatcherTemplate:
    """The script prints a bare suffix; a dispatcher splices it into
    `agent-reviews/<agent-name>-<suffix>.md`. Unlike the tests above, which
    validate the script alone, this proves the two halves still compose --
    without it, a template edit (wrong separator, dropped `.md`, agent name
    after the suffix) or a stdout regression (trailing whitespace, an extra
    diagnostic line) would break only when an agent actually splices them,
    which no other test would see.

    The template half is read out of code-review/SKILL.md's own text rather
    than hand-typed here, so a real template edit in that file fails this
    test instead of leaving two independently-authored copies to drift.
    """

    def test_suffix_plus_template_matches_documented_findings_path_shape(self, tmp_path):
        code_review_text = _CODE_REVIEW_SKILL_MD.read_text()
        assert _FINDINGS_PATH_TEMPLATE in code_review_text, (
            f"{_CODE_REVIEW_SKILL_MD}: findings_path template {_FINDINGS_PATH_TEMPLATE!r} "
            "not found -- this test's template half would silently stop tracking the real contract"
        )

        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        result = _run_script(repo)
        assert result.returncode == 0
        suffix = result.stdout.strip()

        agent_name = "staff-backend-engineer"
        findings_path = _FINDINGS_PATH_TEMPLATE.replace("<agent-name>", agent_name).replace("<suffix>", suffix)
        assert re.fullmatch(
            rf"agent-reviews/{re.escape(agent_name)}-\d+-[A-Za-z0-9-]{{1,20}}\.md", findings_path
        ), f"composed path {findings_path!r} does not match the documented findings_path shape"


class TestRecombinationWithReadyForReviewTemplate:
    """ready-for-review/SKILL.md hardcodes `skill-fidelity-reviewer` into its
    own findings_path template rather than reusing code-review/SKILL.md's
    generic `<agent-name>` placeholder, so
    TestRecombinationWithDispatcherTemplate above never exercises this
    literal. A typo in ready-for-review's own template (wrong separator,
    `.mkd` instead of `.md`, `<sufix>`) would currently pass every other
    test in this file.

    The template half is read out of ready-for-review/SKILL.md's own text
    rather than hand-typed here, so a real template edit in that file fails
    this test instead of leaving two independently-authored copies to drift.
    """

    def test_suffix_plus_template_matches_documented_findings_path_shape(self, tmp_path):
        ready_for_review_text = _READY_FOR_REVIEW_SKILL_MD.read_text()
        assert _READY_FOR_REVIEW_FINDINGS_PATH_TEMPLATE in ready_for_review_text, (
            f"{_READY_FOR_REVIEW_SKILL_MD}: findings_path template "
            f"{_READY_FOR_REVIEW_FINDINGS_PATH_TEMPLATE!r} not found -- this test's template half "
            "would silently stop tracking the real contract"
        )

        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        result = _run_script(repo)
        assert result.returncode == 0
        suffix = result.stdout.strip()

        findings_path = _READY_FOR_REVIEW_FINDINGS_PATH_TEMPLATE.replace("<suffix>", suffix)
        assert re.fullmatch(
            r"agent-reviews/skill-fidelity-reviewer-\d+-[A-Za-z0-9-]{1,20}\.md", findings_path
        ), f"composed path {findings_path!r} does not match the documented findings_path shape"


class TestNotAGitRepo:
    def test_exit_one_and_no_stdout(self, tmp_path):
        cwd = tmp_path / "not-a-repo"
        cwd.mkdir()

        result = _run_script(cwd)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.strip() != ""


class TestUnbornHead:
    """Guards precede the append: an unborn-HEAD repo must fail before
    touching info/exclude, not after -- a failure that lands the append
    first would make a retried invocation appear idempotent for the wrong
    reason."""

    def test_exit_one_and_ignore_file_untouched(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)

        result = _run_script(repo)

        assert result.returncode == 1
        assert result.stdout == ""
        exclude_file = repo / ".git" / "info" / "exclude"
        contents = exclude_file.read_text() if exclude_file.exists() else ""
        assert "agent-reviews/" not in contents, (
            "info/exclude was written before the HEAD-resolves guard failed"
        )


class TestReadOnlyIgnoreFile:
    """A read-only info/exclude is the one failure whose mishandling is
    total, not degraded: no suffix means no dispatch. No skill body
    documents a fallback for the suffix step itself failing. A fallback
    exists only for a reviewer's own write failing. The append must warn
    and continue, not abort before printing the suffix."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_warns_on_stderr_and_still_prints_suffix(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")
        exclude_file = repo / ".git" / "info" / "exclude"
        exclude_file.chmod(0o444)
        try:
            result = _run_script(repo)
        finally:
            exclude_file.chmod(0o644)

        assert result.returncode == 0
        assert result.stderr.strip() != ""
        assert _BARE_SUFFIX_RE.fullmatch(result.stdout.strip())


class TestLinkedWorktree:
    """Every real invocation in this repo happens from inside a linked
    worktree, where `.git` is a file, not a directory. The append must land
    in the common repo's info/exclude (resolved via `git rev-parse
    --git-path info/exclude`, which follows `--git-common-dir` under a
    linked worktree), never a worktree-local path.

    git rev-parse --git-path resolution under a linked worktree is easy to
    get wrong by hand -- this test exercises the real path against the
    common repo's info/exclude, not a worktree-local one."""

    def test_append_lands_in_common_repo_ignore_file_not_worktree_local(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/worktree-suffix")
        wt_path = tmp_path / "worktree-suffix-tree"
        _make_worktree(local, "feat/worktree-suffix", wt_path)
        assert (wt_path / ".git").is_file(), "linked worktree's .git must be a file, not a directory"

        result = _run_script(wt_path)

        assert result.returncode == 0
        assert _BARE_SUFFIX_RE.fullmatch(result.stdout.strip())
        common_exclude_lines = _exclude_lines(local)
        assert common_exclude_lines.count("agent-reviews/") == 1, (
            f"expected exactly one 'agent-reviews/' line in the common repo's info/exclude, "
            f"got {common_exclude_lines!r}"
        )


class TestIgnoreFileMissingTrailingNewline:
    """A pre-existing info/exclude entry with no trailing newline must not
    merge with the appended `agent-reviews/` line into one broken pattern --
    e.g. a seed file containing `*.log` with no trailing newline would
    otherwise become `*.logagent-reviews/` after a bare append, breaking
    both patterns."""

    def test_preexisting_entry_and_appended_line_stay_separate(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")
        exclude_file = repo / ".git" / "info" / "exclude"
        exclude_file.write_text("*.log")  # deliberately no trailing newline

        result = _run_script(repo)

        assert result.returncode == 0
        exclude_lines = _exclude_lines(repo)
        assert "*.log" in exclude_lines, (
            f"pre-existing entry was corrupted by the append: {exclude_lines!r}"
        )
        assert "agent-reviews/" in exclude_lines, (
            f"appended entry was corrupted or missing: {exclude_lines!r}"
        )


class TestNonAsciiBranchNameTruncation:
    """`cut -c1-20` truncates by byte position, not Unicode codepoint, in
    this environment -- a branch name containing multibyte UTF-8 characters
    could be truncated mid-sequence if truncation ran before filtering. The
    script filters to `[A-Za-z0-9-]` with `tr -cd` before the `cut`
    truncation, so no multibyte byte ever reaches the truncation boundary."""

    def test_suffix_is_valid_utf8_when_branch_name_straddles_truncation_boundary(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        # `tr -cd 'A-Za-z0-9-'` strips every Hiragana byte before `cut` runs, so the
        # filtered slug is just "feature-ab" (10 chars), nowhere near the 20-char
        # truncation boundary. This test would catch a regression where `cut`
        # truncates before `tr -cd` filters, or where sanitization runs only after
        # truncation.
        branch_name = "feature/ab" + "あいうえお"
        subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)

        result = subprocess.run([str(_SCRIPT)], cwd=str(repo), capture_output=True, check=False)

        assert result.returncode == 0
        try:
            suffix = result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            pytest.fail(f"script emitted invalid UTF-8 in its suffix bytes {result.stdout!r}: {exc}")
        assert _BARE_SUFFIX_RE.fullmatch(suffix), (
            f"script output {suffix!r} does not match the documented <epoch>-<slug> shape"
        )


class TestNonUtf8BranchNameEntirelyInvalid:
    """Unlike TestNonAsciiBranchNameTruncation above, where only the bytes
    straddling the truncation boundary are non-ASCII, the two branch names
    below contain no byte at all inside `tr -cd`'s `A-Za-z0-9-` class: one
    is undecodable as UTF-8, the other is fully valid UTF-8 but entirely
    non-ASCII. The operative condition is byte-class membership, not UTF-8
    validity, so both hit the identical empty-slug path. A branch name
    stripped to nothing by the filter still produces a valid, well-shaped
    suffix rather than corrupted output."""

    def test_suffix_is_valid_utf8_when_branch_name_is_entirely_invalid_utf8(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        # 0xF5-0xFF are never valid UTF-8 lead or continuation bytes, so no
        # prefix of this name decodes -- `tr -cd 'A-Za-z0-9-'` strips it to nothing.
        branch_name = b"\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8"
        subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)

        result = subprocess.run([str(_SCRIPT)], cwd=str(repo), capture_output=True, check=False)

        assert result.returncode == 0
        try:
            suffix = result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            pytest.fail(f"script emitted invalid UTF-8 in its suffix bytes {result.stdout!r}: {exc}")
        assert _EMPTY_SLUG_SUFFIX_RE.fullmatch(suffix), (
            f"script output {suffix!r} does not match the documented <epoch>-<slug> shape"
        )

    def test_suffix_is_valid_utf8_when_branch_name_is_valid_utf8_but_entirely_non_ascii(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        # Fully valid UTF-8, but every character falls outside `A-Za-z0-9-`, so
        # this exercises the same empty-slug path as the invalid-UTF-8 case above
        # via a different route: valid-but-filtered rather than undecodable.
        branch_name = "ветка"
        subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)

        result = subprocess.run([str(_SCRIPT)], cwd=str(repo), capture_output=True, check=False)

        assert result.returncode == 0
        try:
            suffix = result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            pytest.fail(f"script emitted invalid UTF-8 in its suffix bytes {result.stdout!r}: {exc}")
        assert _EMPTY_SLUG_SUFFIX_RE.fullmatch(suffix), (
            f"script output {suffix!r} does not match the documented <epoch>-<slug> shape"
        )


class TestPunctuationAndAccentedCharacterStripping:
    """ASCII punctuation (underscores, dots) and complete non-ASCII
    characters both fall outside `tr -cd`'s `A-Za-z0-9-` class and are
    silently stripped, not just invalid trailing bytes. Real branch names
    commonly carry this shape (PROJ_123, v1.2.3, snake_case). The stripping
    is intentional per the character-class filter design, not an unverified
    side effect."""

    def test_underscore_dot_and_accented_character_are_stripped(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        branch_name = "fix_bug.123-café"
        expected_slug = "fixbug123-caf"
        subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)

        result = _run_script(repo)
        assert result.returncode == 0

        suffix = result.stdout.strip()
        match = re.fullmatch(r"(\d+)-([A-Za-z0-9-]+)", suffix)
        assert match, f"script output {suffix!r} does not match the documented <epoch>-<slug> shape"
        assert match.group(2) == expected_slug, (
            f"derived slug {match.group(2)!r} from branch {branch_name!r}, expected the underscore, "
            f"dot, and accented character (é) stripped by `tr -cd 'A-Za-z0-9-'`, leaving {expected_slug!r}"
        )
