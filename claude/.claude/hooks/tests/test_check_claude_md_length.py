"""Tests for check-claude-md-length.sh."""
from __future__ import annotations

import json
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

CHECK_CLAUDE_MD_LENGTH_HOOK = HOOKS_DIR / "check-claude-md-length.sh"
CLAUDE_MD_PATH = "claude/.claude/CLAUDE.md"

SETTINGS_PATH = Path(__file__).resolve().parents[4] / "claude/.claude/settings.json"


def make_lines(n: int, prefix: str = "line") -> str:
    """Return content with exactly n newline-terminated lines."""
    return "\n".join(f"{prefix} {i + 1}" for i in range(n)) + "\n"


def stub_bin_without_timeout(tmp_path: Path) -> Path:
    """Stub PATH with only the binaries this hook's code path invokes
    (`cat`/`jq` via _lib.sh's JSON parsing, `dirname` to locate _lib.sh,
    `sed`/`tr` for _lib_command_invokes_git_subcmd's git-commit match
    (GH-783), `grep` for the path-filter match, `awk` for the line
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


def make_repo_with_file(tmp_path: Path, target_path: str, head_lines: int) -> Path:
    """Git repo with `target_path` committed at `head_lines` lines."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    target = repo / target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(make_lines(head_lines))
    subprocess.run(["git", "add", target_path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class TestCheckClaudeMdLength:
    # --- Logic matrix (CLAUDE_MD_PATH fixture) ---

    def test_non_commit_command_allows(self, isolated_home, tmp_path):
        """Non-git-commit Bash command is allowed regardless of staged content."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(CHECK_CLAUDE_MD_LENGTH_HOOK, bash_input("git status"), cwd=repo)
            == "allow"
        )

    def test_non_bash_tool_allows(self, isolated_home, tmp_path):
        """Non-Bash tool inputs are passed through unconditionally."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        assert (
            run_hook(CHECK_CLAUDE_MD_LENGTH_HOOK, edit_input("/tmp/foo.txt"), cwd=repo)
            == "allow"
        )

    def test_outside_git_repo_allows(self, isolated_home, tmp_path):
        """Hook exits 0 silently when CWD is not inside a git repo."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_repo,
            )
            == "allow"
        )

    def test_no_staged_matching_file_allows(self, isolated_home, tmp_path):
        """A staged non-CLAUDE.md/AGENTS.md file does not trigger the gate."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / "other.txt").write_text("something\n")
        subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_claude_md_at_exactly_200_allows(self, isolated_home, tmp_path):
        """200 lines is at the limit — the gate is `> 200`, so 200 passes."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(200))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_claude_md_growing_to_201_denies(self, isolated_home, tmp_path):
        """HEAD at 190, staged at 201: new > 200 and new > old → deny."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_quoted_form_reaches_same_verdict_as_bare_form(self, isolated_home, tmp_path):
        """A quote-adjacent split (`"git" commit -m x`) must reach the same
        deny verdict as the unquoted form."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input('"git" commit -m foo'),
                cwd=repo,
            )
            == "deny"
        )

    def test_sed_absent_from_path_denies(self, isolated_home, tmp_path):
        """Status-2 propagation: the matcher could not determine whether
        this command invokes git commit, and this gate's own documented
        fail-closed posture means an undetermined match denies rather than
        silently falling through to allow. Asserts the distinguishing
        reason text, not just the verdict, so this test cannot be
        satisfied by an ordinary over-limit deny reaching "deny" for the
        wrong reason."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        reason = run_hook_reason(
            CHECK_CLAUDE_MD_LENGTH_HOOK,
            bash_input("git commit -m foo"),
            cwd=repo,
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "could not determine" in reason

    def test_new_claude_md_over_limit_denies(self, isolated_home, tmp_path):
        """New file with no HEAD version staged at 201 lines — old defaults to 0 → deny."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        target = repo / CLAUDE_MD_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_already_over_limit_growing_denies(self, isolated_home, tmp_path):
        """HEAD at 210, staged at 215: growing while over limit → deny."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 210)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(215))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_already_over_limit_reducing_allows(self, isolated_home, tmp_path):
        """HEAD at 210, staged at 205: reducing while over limit → allow."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 210)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(205))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_already_over_limit_same_size_allows(self, isolated_home, tmp_path):
        """HEAD at 210, staged at 210 (different content, same count): not growing → allow."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 210)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(210, prefix="row"))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_staged_deletion_of_claude_md_allows(self, isolated_home, tmp_path):
        """git rm-staged CLAUDE.md: git show ":$f" produces empty output → new=0, 0 > 200 is false → allow."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        subprocess.run(["git", "rm", "-q", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_deny_message_includes_filename_and_counts(self, isolated_home, tmp_path):
        """Deny reason must name the file, new line count, old line count, and limit."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        reason = run_hook_reason(
            CHECK_CLAUDE_MD_LENGTH_HOOK,
            bash_input("git commit -m foo"),
            cwd=repo,
        )
        assert reason is not None
        assert CLAUDE_MD_PATH in reason
        assert "201" in reason
        assert "190" in reason
        assert "200" in reason

    def test_cwd_not_repo_root_does_not_cause_false_negative(
        self, isolated_home, tmp_path
    ):
        """Hook run from a repo subdirectory must still catch over-limit CLAUDE.md.

        Regression test: `git diff --cached --name-only` emits repo-root-relative
        paths. An earlier version had `[ -f "$f" ] || continue` which resolved
        those paths against CWD — if CWD was a subdirectory the check failed
        and the file was silently skipped (false negative, bloated file slips
        through). The guard was removed; `git show ":$f"` reads from the index
        directly and doesn't depend on CWD.
        """
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        subdir = repo / "claude"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=subdir,
            )
            == "deny"
        )

    # --- Path-shape positive cases (must → deny) ---

    def test_root_claude_md_denies(self, isolated_home, tmp_path):
        """CLAUDE.md at the repo root matches the filter → deny."""
        repo = make_repo_with_file(tmp_path, "CLAUDE.md", 190)
        (repo / "CLAUDE.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_root_agents_md_denies(self, isolated_home, tmp_path):
        """AGENTS.md at the repo root matches the filter → deny."""
        repo = make_repo_with_file(tmp_path, "AGENTS.md", 190)
        (repo / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_dot_claude_claude_md_denies(self, isolated_home, tmp_path):
        """.claude/CLAUDE.md matches the filter → deny."""
        repo = make_repo_with_file(tmp_path, ".claude/CLAUDE.md", 190)
        (repo / ".claude" / "CLAUDE.md").write_text(make_lines(201))
        subprocess.run(["git", "add", ".claude/CLAUDE.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_stowed_source_path_denies(self, isolated_home, tmp_path):
        """claude/.claude/CLAUDE.md (stowed-source path) matches the filter → deny."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_stowed_source_agents_md_denies(self, isolated_home, tmp_path):
        """claude/.claude/AGENTS.md (stowed-source path) matches the filter → deny."""
        repo = make_repo_with_file(tmp_path, "claude/.claude/AGENTS.md", 190)
        (repo / "claude" / ".claude" / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "claude/.claude/AGENTS.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_dot_claude_agents_md_denies(self, isolated_home, tmp_path):
        """.claude/AGENTS.md matches the filter → deny."""
        repo = make_repo_with_file(tmp_path, ".claude/AGENTS.md", 190)
        (repo / ".claude" / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", ".claude/AGENTS.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    # --- AGENTS.md transition triad ---

    def test_agents_md_at_exactly_200_allows(self, isolated_home, tmp_path):
        """AGENTS.md at 200 lines is at the limit — the gate is `> 200`, so 200 passes."""
        repo = make_repo_with_file(tmp_path, "AGENTS.md", 190)
        (repo / "AGENTS.md").write_text(make_lines(200))
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_agents_md_growing_to_201_denies(self, isolated_home, tmp_path):
        """AGENTS.md HEAD at 190, staged at 201: new > 200 and new > old → deny."""
        repo = make_repo_with_file(tmp_path, "AGENTS.md", 190)
        (repo / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_new_agents_md_over_limit_denies(self, isolated_home, tmp_path):
        """New AGENTS.md staged at 201 lines — old defaults to 0 → deny."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    def test_first_commit_claude_md_over_limit_denies(self, isolated_home, tmp_path):
        """First-ever commit to a repo (no HEAD): CLAUDE.md staged at 201 lines → deny.

        Regression test: git show "HEAD:$f" fails when no commits exist; awk
        'END{print NR}' on empty output returns 0 for old, so deny fires correctly
        when new > 200 regardless of HEAD state.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        target = repo / CLAUDE_MD_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m init"),
                cwd=repo,
            )
            == "deny"
        )

    # --- Negative-path cases (must → allow because regex does not match) ---

    def test_claude_md_bak_allows(self, isolated_home, tmp_path):
        """CLAUDE.md.bak does not match the filter → allow."""
        repo = make_repo_with_file(tmp_path, "CLAUDE.md.bak", 190)
        (repo / "CLAUDE.md.bak").write_text(make_lines(201))
        subprocess.run(["git", "add", "CLAUDE.md.bak"], cwd=repo, check=True)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo).decode()
        assert "CLAUDE.md.bak" in staged, "file was not actually staged"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_not_claude_md_allows(self, isolated_home, tmp_path):
        """not-CLAUDE.md does not match the filter → allow."""
        repo = make_repo_with_file(tmp_path, "not-CLAUDE.md", 190)
        (repo / "not-CLAUDE.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "not-CLAUDE.md"], cwd=repo, check=True)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo).decode()
        assert "not-CLAUDE.md" in staged, "file was not actually staged"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_foo_slash_claude_md_allows(self, isolated_home, tmp_path):
        """foo/CLAUDE.md (CLAUDE.md outside root and outside .claude/) does not match → allow."""
        repo = make_repo_with_file(tmp_path, "foo/CLAUDE.md", 190)
        (repo / "foo" / "CLAUDE.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "foo/CLAUDE.md"], cwd=repo, check=True)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo).decode()
        assert "foo/CLAUDE.md" in staged, "file was not actually staged"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_docs_agents_claude_md_allows(self, isolated_home, tmp_path):
        """docs/agents/CLAUDE.md does not match the filter → allow."""
        repo = make_repo_with_file(tmp_path, "docs/agents/CLAUDE.md", 190)
        (repo / "docs" / "agents" / "CLAUDE.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "docs/agents/CLAUDE.md"], cwd=repo, check=True)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo).decode()
        assert "docs/agents/CLAUDE.md" in staged, "file was not actually staged"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_subfolder_agents_md_allows(self, isolated_home, tmp_path):
        """subfolder/AGENTS.md does not match the filter → allow."""
        repo = make_repo_with_file(tmp_path, "subfolder/AGENTS.md", 190)
        (repo / "subfolder" / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "subfolder/AGENTS.md"], cwd=repo, check=True)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo).decode()
        assert "subfolder/AGENTS.md" in staged, "file was not actually staged"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def test_lowercase_claude_md_allows(self, isolated_home, tmp_path):
        """claude.md (lowercase) does not match the case-sensitive filter → allow."""
        repo = make_repo_with_file(tmp_path, "claude.md", 190)
        (repo / "claude.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "claude.md"], cwd=repo, check=True)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo).decode()
        assert "claude.md" in staged, "file was not actually staged"
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    # --- Multi-file staged commit ---

    def test_multi_file_both_over_limit_denies_and_names_both(
        self, isolated_home, tmp_path
    ):
        """Both CLAUDE.md and AGENTS.md staged over limit → deny; both filenames in message."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "CLAUDE.md").write_text(make_lines(190))
        (repo / "AGENTS.md").write_text(make_lines(190))
        subprocess.run(["git", "add", "CLAUDE.md", "AGENTS.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        (repo / "CLAUDE.md").write_text(make_lines(201))
        (repo / "AGENTS.md").write_text(make_lines(201))
        subprocess.run(["git", "add", "CLAUDE.md", "AGENTS.md"], cwd=repo, check=True)
        result = subprocess.run(
            [str(CHECK_CLAUDE_MD_LENGTH_HOOK)],
            input=json.dumps(bash_input("git commit -m foo")),
            capture_output=True,
            text=True,
            cwd=repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = payload["hookSpecificOutput"].get("permissionDecisionReason", "")
        assert "CLAUDE.md" in reason
        assert "AGENTS.md" in reason
        assert "201" in reason

    def test_commit_amend_denies(self, isolated_home, tmp_path):
        """git commit --amend is still a commit command and must be caught."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit --amend --no-edit"),
                cwd=repo,
            )
            == "deny"
        )

    def test_chained_git_add_commit_denies(self, isolated_home, tmp_path):
        """Chained `git add ... && git commit` is caught by the internal
        _lib_command_invokes_git_subcmd check.

        The `if: "Bash(git commit *)"` predicate in settings.json matches
        chained and prefixed commands (a `true && git commit ...` with a
        real unreviewed staged diff got a genuine deny from
        require-code-review.sh). This test invokes the hook binary directly
        regardless, since the internal check is the authoritative gate
        either way — consistent with the hook header's note that the `if`
        field is a hint only.
        """
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git add . && git commit -m foo"),
                cwd=repo,
            )
            == "deny"
        )

    # --- Fail-open regression: neither timeout(1) nor gtimeout(1) present ---

    def test_growing_over_limit_denies_when_neither_timeout_nor_gtimeout_present(
        self, isolated_home, tmp_path
    ):
        """Fail-open regression: with neither binary present, _lib_capped
        runs the git show calls uncapped (see _lib.sh) rather than silently
        skipping — the gate must still catch a growing over-limit file."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(201))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        stub_bin = stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "deny"
        )

    def test_at_limit_allows_when_neither_timeout_nor_gtimeout_present(
        self, isolated_home, tmp_path
    ):
        """Companion allow case for the deny above: under the same PATH, a
        file at the limit (not growing past it) must still pass — without
        this, a fallback branch that always returns nonzero would
        masquerade as a working gate."""
        repo = make_repo_with_file(tmp_path, CLAUDE_MD_PATH, 190)
        (repo / CLAUDE_MD_PATH).write_text(make_lines(200))
        subprocess.run(["git", "add", CLAUDE_MD_PATH], cwd=repo, check=True)
        stub_bin = stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                CHECK_CLAUDE_MD_LENGTH_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "allow"
        )

    # --- Settings.json wiring ---

    def test_settings_json_contains_hook_entry(self):
        """settings.json must wire check-claude-md-length.sh to a Bash PreToolUse group.

        PreToolUse is a list of {matcher, hooks} objects. The hook must be in a
        group whose matcher includes "Bash" — a PostToolUse entry or a non-Bash
        matcher would register the command string but disable the gate.
        """
        settings = json.loads(SETTINGS_PATH.read_text())
        matcher_groups = settings.get("hooks", {}).get("PreToolUse", [])
        matches = [
            (group.get("matcher", ""), entry)
            for group in matcher_groups
            if isinstance(group, dict)
            for entry in group.get("hooks", [])
            if isinstance(entry, dict)
            and entry.get("command") == "~/.claude/hooks/check-claude-md-length.sh"
        ]
        assert matches, "No hook entry found for check-claude-md-length.sh in PreToolUse"
        matcher, _ = matches[0]
        assert "Bash" in matcher, (
            f"check-claude-md-length.sh must be in a Bash matcher group; found: {matcher!r}"
        )
