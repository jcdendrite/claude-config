"""Tests for check-skill-length.sh."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    run_hook,
    run_hook_reason,
)

CHECK_SKILL_LENGTH_HOOK = HOOKS_DIR / "check-skill-length.sh"
SKILL_PATH = "claude/.claude/skills/my-skill/SKILL.md"


def stub_bin_without_timeout(tmp_path: Path) -> Path:
    """Stub PATH with only the binaries this hook's code path invokes
    (`cat`/`jq` via _lib.sh's JSON parsing, `dirname` to locate _lib.sh,
    `sed`/`tr` for _lib_command_invokes_git_subcmd's git-commit match
    (GH-783 Phase 2), `grep` for the path-filter match, `awk` for the line
    count, `git` for the _lib_capped-wrapped show calls), omitting both
    timeout(1) and gtimeout(1). Mirrors
    test_require_worktree_for_git_writes.py's test_python3_absent_denies
    shape; skips (does not silently under-symlink) when a needed real
    binary is itself absent from the test machine."""
    stub_bin = tmp_path / "_stub_bin"
    stub_bin.mkdir()
    for tool in ("awk", "cat", "dirname", "git", "grep", "jq", "sed", "tr"):
        real_path = shutil.which(tool)
        if not real_path:
            pytest.skip(f"{tool} not found in PATH")
        (stub_bin / tool).symlink_to(real_path)
    return stub_bin


def make_skill_content(n: int, prefix: str = "line") -> str:
    """Return content with exactly n newline-terminated lines."""
    return "\n".join(f"{prefix} {i + 1}" for i in range(n)) + "\n"


def make_repo_with_skill(tmp_path: Path, head_lines: int) -> Path:
    """Git repo with SKILL.md committed at `head_lines` lines."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    skill_dir = repo / "claude" / ".claude" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (repo / SKILL_PATH).write_text(make_skill_content(head_lines))
    subprocess.run(["git", "add", SKILL_PATH], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def skill_repo(tmp_path):
    """Git repo with SKILL.md committed at 190 lines."""
    return make_repo_with_skill(tmp_path, 190)


@pytest.fixture
def new_skill_repo(tmp_path):
    """Git repo with no committed SKILL.md — SKILL.md will be a new staged file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    (repo / "claude" / ".claude" / "skills" / "my-skill").mkdir(parents=True)
    return repo


class TestCheckSkillLength:
    def test_non_commit_command_allows(self, isolated_home, skill_repo):
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        assert (
            run_hook(CHECK_SKILL_LENGTH_HOOK, bash_input("git status"), cwd=skill_repo)
            == "allow"
        )

    def test_non_bash_tool_allows(self, isolated_home, skill_repo):
        assert (
            run_hook(CHECK_SKILL_LENGTH_HOOK, edit_input("/tmp/foo.txt"), cwd=skill_repo)
            == "allow"
        )

    def test_outside_git_repo_allows(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_repo,
            )
            == "allow"
        )

    def test_no_staged_skill_files_allows(self, isolated_home, skill_repo):
        (skill_repo / "other.txt").write_text("something\n")
        subprocess.run(["git", "add", "other.txt"], cwd=skill_repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=skill_repo,
            )
            == "allow"
        )

    def test_skill_at_exactly_200_allows(self, isolated_home, skill_repo):
        """200 lines is at the limit — the gate is `> 200`, so 200 passes."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(200))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=skill_repo,
            )
            == "allow"
        )

    def test_skill_growing_to_201_denies(self, isolated_home, skill_repo):
        """HEAD at 190, staged at 201: new > 200 and new > old → deny."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=skill_repo,
            )
            == "deny"
        )

    def test_quoted_form_reaches_same_verdict_as_bare_form(self, isolated_home, skill_repo):
        """GH-783 Phase 2: a quote-adjacent split (`"git" commit -m x`) must
        reach the same deny verdict as the unquoted form."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input('"git" commit -m foo'),
                cwd=skill_repo,
            )
            == "deny"
        )

    def test_sed_absent_from_path_denies(self, isolated_home, skill_repo, tmp_path):
        """Status-2 propagation: the matcher could not determine whether
        this command invokes git commit, and this gate's own documented
        fail-closed posture means an undetermined match denies rather than
        silently falling through to allow. Asserts the distinguishing
        reason text, not just the verdict, so this test cannot be
        satisfied by an ordinary over-limit deny reaching "deny" for the
        wrong reason."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        reason = run_hook_reason(
            CHECK_SKILL_LENGTH_HOOK,
            bash_input("git commit -m foo"),
            cwd=skill_repo,
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "could not determine" in reason

    def test_new_skill_over_limit_denies(self, isolated_home, new_skill_repo):
        """New file with no HEAD version staged at 201 lines — old defaults to 0 → deny."""
        (new_skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=new_skill_repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=new_skill_repo,
            )
            == "deny"
        )

    def test_already_over_limit_growing_denies(self, isolated_home, tmp_path):
        """HEAD at 210, staged at 215: growing while over limit → deny."""
        repo = make_repo_with_skill(tmp_path, 210)
        (repo / SKILL_PATH).write_text(make_skill_content(215))
        subprocess.run(["git", "add", SKILL_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_already_over_limit_reducing_allows(self, isolated_home, tmp_path):
        """HEAD at 210, staged at 205: reducing while over limit → allow."""
        repo = make_repo_with_skill(tmp_path, 210)
        (repo / SKILL_PATH).write_text(make_skill_content(205))
        subprocess.run(["git", "add", SKILL_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_already_over_limit_same_size_allows(self, isolated_home, tmp_path):
        """HEAD at 210, staged at 210 (different content, same count): not growing → allow."""
        repo = make_repo_with_skill(tmp_path, 210)
        (repo / SKILL_PATH).write_text(make_skill_content(210, prefix="row"))
        subprocess.run(["git", "add", SKILL_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_staged_deletion_of_skill_allows(self, isolated_home, skill_repo):
        """git rm-staged SKILL.md: git show ":$f" produces empty output → new=0, 0 > 200 is false → allow."""
        subprocess.run(["git", "rm", "-q", SKILL_PATH], cwd=skill_repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=skill_repo,
            )
            == "allow"
        )

    def test_deny_message_includes_filename_and_counts(self, isolated_home, skill_repo):
        """Deny reason must name the file, new line count, old line count, and limit."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        reason = run_hook_reason(
            CHECK_SKILL_LENGTH_HOOK,
            bash_input("git commit -m foo"),
            cwd=skill_repo,
        )
        assert reason is not None
        assert SKILL_PATH in reason
        assert "201" in reason
        assert "190" in reason
        assert "200" in reason

    def test_code_review_over_default_under_override_allows(
        self, isolated_home, tmp_path
    ):
        """code-review/SKILL.md gets a 500-line cap; 300 lines (over 200, under 500) → allow."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        cr_path = "claude/.claude/skills/code-review/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "code-review").mkdir(parents=True)
        (repo / cr_path).write_text(make_skill_content(290))
        subprocess.run(["git", "add", cr_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / cr_path).write_text(make_skill_content(300))
        subprocess.run(["git", "add", cr_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_code_review_over_override_denies(self, isolated_home, tmp_path):
        """code-review/SKILL.md over the 500-line override and growing → deny."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        cr_path = "claude/.claude/skills/code-review/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "code-review").mkdir(parents=True)
        (repo / cr_path).write_text(make_skill_content(490))
        subprocess.run(["git", "add", cr_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / cr_path).write_text(make_skill_content(501))
        subprocess.run(["git", "add", cr_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_plan_review_uses_override(self, isolated_home, tmp_path):
        """plan-review/SKILL.md also gets the 500-line cap."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        pr_path = "claude/.claude/skills/plan-review/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "plan-review").mkdir(parents=True)
        (repo / pr_path).write_text(make_skill_content(290))
        subprocess.run(["git", "add", pr_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / pr_path).write_text(make_skill_content(300))
        subprocess.run(["git", "add", pr_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_plan_review_routing_md_uses_override(self, isolated_home, tmp_path):
        """plan-review/ROUTING.md also gets the 500-line cap: at/under it, growing → allow."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        routing_path = "claude/.claude/skills/plan-review/ROUTING.md"
        (repo / "claude" / ".claude" / "skills" / "plan-review").mkdir(parents=True)
        (repo / routing_path).write_text(make_skill_content(290))
        subprocess.run(["git", "add", routing_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / routing_path).write_text(make_skill_content(300))
        subprocess.run(["git", "add", routing_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_plan_review_routing_md_over_override_denies(self, isolated_home, tmp_path):
        """plan-review/ROUTING.md over the 500-line override and growing → deny."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        routing_path = "claude/.claude/skills/plan-review/ROUTING.md"
        (repo / "claude" / ".claude" / "skills" / "plan-review").mkdir(parents=True)
        (repo / routing_path).write_text(make_skill_content(490))
        subprocess.run(["git", "add", routing_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / routing_path).write_text(make_skill_content(501))
        subprocess.run(["git", "add", routing_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_pr_description_over_default_under_override_allows(
        self, isolated_home, tmp_path
    ):
        """pr-description/SKILL.md gets a 210-line cap; 205 lines (over 200, under 210) → allow."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        pr_path = "claude/.claude/skills/pr-description/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "pr-description").mkdir(parents=True)
        (repo / pr_path).write_text(make_skill_content(195))
        subprocess.run(["git", "add", pr_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / pr_path).write_text(make_skill_content(205))
        subprocess.run(["git", "add", pr_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_pr_description_over_override_denies(self, isolated_home, tmp_path):
        """pr-description/SKILL.md over the 210-line override and growing → deny."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        pr_path = "claude/.claude/skills/pr-description/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "pr-description").mkdir(parents=True)
        (repo / pr_path).write_text(make_skill_content(205))
        subprocess.run(["git", "add", pr_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / pr_path).write_text(make_skill_content(211))
        subprocess.run(["git", "add", pr_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_memory_files_skill_over_default_under_override_allows(
        self, isolated_home, tmp_path
    ):
        """ai-instruction-and-memory-files/SKILL.md gets a 215-line cap; 210 lines (over 200, under 215) → allow."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        memory_path = "claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "ai-instruction-and-memory-files").mkdir(
            parents=True
        )
        (repo / memory_path).write_text(make_skill_content(195))
        subprocess.run(["git", "add", memory_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / memory_path).write_text(make_skill_content(210))
        subprocess.run(["git", "add", memory_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_memory_files_skill_over_override_denies(self, isolated_home, tmp_path):
        """ai-instruction-and-memory-files/SKILL.md over the 215-line override and growing → deny."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        memory_path = "claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md"
        (repo / "claude" / ".claude" / "skills" / "ai-instruction-and-memory-files").mkdir(
            parents=True
        )
        (repo / memory_path).write_text(make_skill_content(210))
        subprocess.run(["git", "add", memory_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / memory_path).write_text(make_skill_content(216))
        subprocess.run(["git", "add", memory_path], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_cwd_not_repo_root_does_not_cause_false_negative(
        self, isolated_home, skill_repo
    ):
        """Hook run from a repo subdirectory must still catch over-limit SKILL.md.

        Regression test: `git diff --cached --name-only` emits repo-root-relative
        paths. An earlier version had `[ -f "$f" ] || continue` which resolved
        those paths against CWD — if CWD was a subdirectory the check failed
        and the file was silently skipped (false negative, bloated skill slips
        through). The guard was removed; `git show ":$f"` reads from the index
        directly and doesn't depend on CWD.
        """
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        subdir = skill_repo / "claude"
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=subdir,
            )
            == "deny"
        )

    # --- Fail-open regression: neither timeout(1) nor gtimeout(1) present ---

    def test_growing_over_limit_denies_when_neither_timeout_nor_gtimeout_present(
        self, isolated_home, skill_repo, tmp_path
    ):
        """Fail-open regression: with neither binary present, _lib_capped
        runs the git show calls uncapped (see _lib.sh) rather than silently
        skipping — the gate must still catch a growing over-limit SKILL.md."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(201))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        stub_bin = stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=skill_repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "deny"
        )

    def test_at_limit_allows_when_neither_timeout_nor_gtimeout_present(
        self, isolated_home, skill_repo, tmp_path
    ):
        """Companion allow case for the deny above: under the same PATH, a
        SKILL.md at the limit (not growing past it) must still pass —
        without this, a fallback branch that always returns nonzero would
        masquerade as a working gate."""
        (skill_repo / SKILL_PATH).write_text(make_skill_content(200))
        subprocess.run(["git", "add", SKILL_PATH], cwd=skill_repo, check=True)
        stub_bin = stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                CHECK_SKILL_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=skill_repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "allow"
        )
