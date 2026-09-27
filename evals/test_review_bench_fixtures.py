"""Tests for evals/review_bench/fixture_repo.py and evals/review_bench/arms.py.
Offline throughout: every fixture_repo test builds a throwaway two-commit
tmp_path git repo as its source_repo (the same pattern
test_review_bench_mining.py's own git-repo helpers use); most arms tests
render from a synthetic production agent file under tmp_path. The
TestRenderArmAgentAgainstRealProductionAgentFiles contract test is the one
exception, rendering from the real claude/.claude/agents/*.md files
instead. No test launches `claude`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from review_bench import arms, fixture_repo
from review_bench.defects import ConfirmedDefect

# --- git-repo fixture helpers (mirrors test_review_bench_mining.py's own) ---


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _write(repo: Path, rel_path: str, content: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _confirmed_defect(*, base_commit: str, head_commit: str, lens: str = "staff-backend-engineer") -> ConfirmedDefect:
    return ConfirmedDefect(
        id="defect-1", source="szz", lens=lens, base_commit=base_commit, head_commit=head_commit,
        fix_commit=head_commit, fix_date="2024-01-01", description="test defect",
    )


class TestBuildTwoCommitRepo:
    def test_holds_exactly_two_commits_with_no_later_commit_reachable(self, tmp_path: Path) -> None:
        source_repo = _init_repo(tmp_path / "source")
        _write(source_repo, "changed_file.py", "x = 1\n")
        base_commit = _commit(source_repo, "base")
        _write(source_repo, "changed_file.py", "x = 2\n")
        head_commit = _commit(source_repo, "fix: bug")
        _write(source_repo, "later-marker.txt", "later\n")
        later_commit = _commit(source_repo, "a later commit the fixture must never reach")

        defect = _confirmed_defect(base_commit=base_commit, head_commit=head_commit)
        dest_dir = tmp_path / "fixture"
        dest_dir.mkdir()
        fixture_repo.build_two_commit_repo(source_repo, defect, dest_dir)

        log = _git(dest_dir, "log", "--format=%H").strip().splitlines()
        assert len(log) == 2
        assert not (dest_dir / "later-marker.txt").exists()
        # The later commit's object is never fetched into the fixture's own
        # store -- git archive extracts a tree, never history.
        cat_file = subprocess.run(
            ["git", "cat-file", "-e", later_commit], cwd=dest_dir, capture_output=True, text=True,
        )
        assert cat_file.returncode != 0


class TestWriteBenchArtifacts:
    def _build_fixture(self, tmp_path: Path) -> Path:
        source_repo = _init_repo(tmp_path / "source")
        _write(source_repo, "changed_file.py", "x = 1\n")
        base_commit = _commit(source_repo, "base")
        _write(source_repo, "changed_file.py", "x = 2\n")
        head_commit = _commit(source_repo, "fix: bug")
        defect = _confirmed_defect(base_commit=base_commit, head_commit=head_commit)
        dest_dir = tmp_path / "fixture"
        dest_dir.mkdir()
        fixture_repo.build_defect_fixture(source_repo, defect, dest_dir)
        return dest_dir

    def test_bench_dir_is_excluded_from_git(self, tmp_path: Path) -> None:
        dest_dir = self._build_fixture(tmp_path)
        exclude_text = (dest_dir / ".git" / "info" / "exclude").read_text()
        assert "/.bench/\n" in exclude_text
        check_ignore = subprocess.run(
            ["git", "check-ignore", ".bench/change.diff"], cwd=dest_dir, capture_output=True, text=True,
        )
        assert check_ignore.returncode == 0

    def test_function_context_diff_uses_git_dash_w(self, tmp_path: Path) -> None:
        """-W ("show whole function as context") must actually be the flag
        used -- verified by independently invoking `git diff -W` on the
        same repo and comparing, rather than trusting the file merely
        exists."""
        source_repo = _init_repo(tmp_path / "source")
        long_function = "".join(f"    line_{i} = {i}\n" for i in range(20))
        _write(source_repo, "changed_file.py", f"def f():\n{long_function}    return line_0\n")
        base_commit = _commit(source_repo, "base")
        long_function_changed = long_function.replace("line_10 = 10", "line_10 = 999")
        _write(source_repo, "changed_file.py", f"def f():\n{long_function_changed}    return line_0\n")
        head_commit = _commit(source_repo, "fix: bug")
        defect = _confirmed_defect(base_commit=base_commit, head_commit=head_commit)
        dest_dir = tmp_path / "fixture"
        dest_dir.mkdir()
        fixture_repo.build_defect_fixture(source_repo, defect, dest_dir)

        expected = subprocess.run(
            ["git", "diff", "-W", "HEAD~1", "HEAD"], cwd=dest_dir, capture_output=True, text=True, check=True,
        ).stdout
        actual = (dest_dir / ".bench" / "change-function-context.diff").read_text()
        default_diff = (dest_dir / ".bench" / "change.diff").read_text()
        assert actual == expected
        assert actual != default_diff  # -W actually pulled in more context than the default diff

    def test_over_read_cap_flag_follows_the_chars_divided_by_four_threshold(self, tmp_path: Path) -> None:
        source_repo = _init_repo(tmp_path / "source")
        _write(source_repo, "at_cap.py", "a\n")
        _write(source_repo, "over_cap.py", "a\n")
        base_commit = _commit(source_repo, "base")
        # 100_000 chars // 4 == 25_000 (fixture_repo._OVER_READ_CAP_TOKENS)
        # exactly, so over_read_cap is False; one more 4-char group tips it.
        _write(source_repo, "at_cap.py", "a" * 100_000)
        _write(source_repo, "over_cap.py", "a" * 100_004)
        head_commit = _commit(source_repo, "fix: bug")
        defect = _confirmed_defect(base_commit=base_commit, head_commit=head_commit)
        dest_dir = tmp_path / "fixture"
        dest_dir.mkdir()
        fixture_repo.build_two_commit_repo(source_repo, defect, dest_dir)

        stats_by_path = {stat.path: stat for stat in fixture_repo.write_bench_artifacts(dest_dir)}
        assert stats_by_path["at_cap.py"].estimated_tokens == 25_000
        assert stats_by_path["at_cap.py"].over_read_cap is False
        assert stats_by_path["over_cap.py"].estimated_tokens == 25_001
        assert stats_by_path["over_cap.py"].over_read_cap is True


class TestPrecisionJudgeFixtureAndRecallJudgeDir:
    def test_precision_judge_fixture_holds_no_bench_lens_file(self, tmp_path: Path) -> None:
        source_repo = _init_repo(tmp_path / "source")
        _write(source_repo, "changed_file.py", "x = 1\n")
        base_commit = _commit(source_repo, "base")
        _write(source_repo, "changed_file.py", "x = 2\n")
        head_commit = _commit(source_repo, "fix: bug")
        defect = _confirmed_defect(base_commit=base_commit, head_commit=head_commit)
        dest_dir = tmp_path / "judge-fixture"
        dest_dir.mkdir()

        fixture_repo.build_precision_judge_fixture(source_repo, defect, dest_dir)

        assert not list(dest_dir.rglob("bench-*.md"))

    def test_recall_judge_dir_holds_no_bench_lens_file(self, tmp_path: Path) -> None:
        dest_dir = tmp_path / "recall-judge-dir"
        result = fixture_repo.build_recall_judge_dir(dest_dir)
        assert result == dest_dir
        assert dest_dir.is_dir()
        assert not any(dest_dir.iterdir())


# --- arms.py -----------------------------------------------------------------

_LENS = "staff-backend-engineer"
_READ_CLAUSE = arms.LENS_READ_CLAUSES[_LENS]


def _write_production_agent(
    agents_dir: Path, *, lens: str, tools: str, extra_frontmatter: str = "", clause_count: int = 1,
) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    clause_text = (arms.LENS_READ_CLAUSES[lens] + " ") * clause_count
    body = f"You are `{lens}`.\n\n## Review\n\n{clause_text}and check style.\n"
    text = f"---\nname: {lens}\nmodel: sonnet\ntools: {tools}\n{extra_frontmatter}---\n{body}"
    (agents_dir / f"{lens}.md").write_text(text)


class TestRenderArmAgent:
    def test_current_rule_arm_differs_from_production_only_in_name_model_and_tools(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _write_production_agent(
            agents_dir, lens=_LENS, tools="Read, Grep, Glob, Bash, Write",
            extra_frontmatter="description: A test lens.\n",
        )
        original_text = (agents_dir / f"{_LENS}.md").read_text()
        original_frontmatter, original_body = arms._split_frontmatter(original_text)

        rendered = arms.render_arm_agent(arms.ARM_CURRENT_RULE, _LENS, agents_dir=agents_dir)

        frontmatter, body = arms._split_frontmatter(rendered)
        assert f"name: bench-{_LENS}" in frontmatter
        assert f"model: {arms.ARM_MODEL_FRONTMATTER_VALUE}" in frontmatter
        assert arms._parse_tools_field(frontmatter) == frozenset(arms.ARM_TOOLS)
        # A field render_arm_agent never touches survives byte-identical.
        assert "description: A test lens." in frontmatter
        # Body is untouched for arm 1 -- no clause substitution.
        assert body == original_body

    def test_function_context_arm_differs_from_production_only_in_name_model_tools_and_clause(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _write_production_agent(
            agents_dir, lens=_LENS, tools="Read, Grep, Glob, Bash, Write",
            extra_frontmatter="description: A test lens.\n",
        )
        original_text = (agents_dir / f"{_LENS}.md").read_text()
        original_frontmatter, original_body = arms._split_frontmatter(original_text)

        rendered = arms.render_arm_agent(arms.ARM_FUNCTION_CONTEXT, _LENS, agents_dir=agents_dir)
        frontmatter, body = arms._split_frontmatter(rendered)

        assert f"name: bench-{_LENS}" in frontmatter
        assert f"model: {arms.ARM_MODEL_FRONTMATTER_VALUE}" in frontmatter
        assert arms._parse_tools_field(frontmatter) == frozenset(arms.ARM_TOOLS)
        assert "description: A test lens." in frontmatter
        assert body == original_body.replace(_READ_CLAUSE, arms.FUNCTION_CONTEXT_CLAUSE, 1)
        assert _READ_CLAUSE not in body
        assert arms.FUNCTION_CONTEXT_CLAUSE in body

    def test_every_arm_files_tools_field_is_exactly_read_grep_glob(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _write_production_agent(agents_dir, lens=_LENS, tools="Read, Grep, Glob, Bash, Write, WebFetch")
        for arm in arms.KNOWN_ARMS:
            rendered = arms.render_arm_agent(arm, _LENS, agents_dir=agents_dir)
            frontmatter, _ = arms._split_frontmatter(rendered)
            assert arms._parse_tools_field(frontmatter) == frozenset(arms.ARM_TOOLS)

    def test_clause_substitution_happens_exactly_once_per_lens(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _write_production_agent(agents_dir, lens=_LENS, tools="Read, Grep, Glob, Bash, Write", clause_count=1)
        rendered = arms.render_arm_agent(arms.ARM_FUNCTION_CONTEXT, _LENS, agents_dir=agents_dir)
        _, body = arms._split_frontmatter(rendered)
        assert body.count(arms.FUNCTION_CONTEXT_CLAUSE) == 1

    def test_fails_loudly_when_the_read_clause_is_missing(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        text = (
            f"---\nname: {_LENS}\nmodel: sonnet\ntools: Read, Grep, Glob, Bash, Write\n---\n"
            "You are the reviewer. No read clause appears in this body at all.\n"
        )
        (agents_dir / f"{_LENS}.md").write_text(text)

        with pytest.raises(arms.ArmSnapshotError):
            arms.render_arm_agent(arms.ARM_FUNCTION_CONTEXT, _LENS, agents_dir=agents_dir)

    def test_fails_loudly_when_the_read_clause_appears_twice(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _write_production_agent(agents_dir, lens=_LENS, tools="Read, Grep, Glob, Bash, Write", clause_count=2)

        with pytest.raises(arms.ArmSnapshotError):
            arms.render_arm_agent(arms.ARM_FUNCTION_CONTEXT, _LENS, agents_dir=agents_dir)

    def test_fails_loudly_when_production_tools_lacks_one_of_read_grep_glob(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        # Missing Grep -- an arm can never gain a tool its lens lacks.
        _write_production_agent(agents_dir, lens=_LENS, tools="Read, Glob, Bash, Write")

        with pytest.raises(arms.ArmSnapshotError):
            arms.render_arm_agent(arms.ARM_CURRENT_RULE, _LENS, agents_dir=agents_dir)


class TestRenderArmAgentAgainstRealProductionAgentFiles:
    """Contract test: every other TestRenderArmAgent case above renders from
    a synthetic stand-in, so none of them would notice a wording edit to a
    real claude/.claude/agents/*.md file breaking LENS_READ_CLAUSES's exact-
    match assumption. This is the only test exercising the real files."""

    @pytest.mark.parametrize("lens", sorted(arms.LENS_READ_CLAUSES))
    def test_current_rule_arm_renders_from_the_real_production_agent_file(self, lens: str) -> None:
        arms.render_arm_agent(arms.ARM_CURRENT_RULE, lens)

    @pytest.mark.parametrize("lens", sorted(arms.LENS_READ_CLAUSES))
    def test_function_context_arm_renders_from_the_real_production_agent_file(self, lens: str) -> None:
        arms.render_arm_agent(arms.ARM_FUNCTION_CONTEXT, lens)


class TestSnapshotArmAndInstallArm:
    def test_snapshot_arm_renders_every_requested_lens(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _write_production_agent(agents_dir, lens=_LENS, tools="Read, Grep, Glob, Bash, Write")
        rendered = arms.snapshot_arm(arms.ARM_CURRENT_RULE, [_LENS], agents_dir=agents_dir)
        assert set(rendered) == {_LENS}

    def test_install_arm_writes_bench_prefixed_files(self, tmp_path: Path) -> None:
        dest_dir = tmp_path / "fixture-agents"
        arms.install_arm({_LENS: "rendered text\n"}, dest_dir)
        assert (dest_dir / f"bench-{_LENS}.md").read_text() == "rendered text\n"
