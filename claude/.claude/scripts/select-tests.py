#!/usr/bin/env python3
"""select-tests.py — runs only the pytest test domains implicated by what
changed, instead of the whole suite, for a faster local dev loop. See
.claude/plans/selective-test-runs.md for the mechanism rationale and its
primary-source citations.

Usage: select-tests.py [pytest args...]
"""
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

# Hang-detection backstop, not a measured worst case — sized between
# post-crash-sessions.py's 5.0s and 25.0s timeouts.
_GIT_TIMEOUT_SECONDS = 15.0

# --- Domain rule table -------------------------------------------------
# One directory pair per test domain (source tree -> its own test directory).
# A cross-domain exception covers a source change that isn't under a
# domain's own directory but still needs that domain's tests re-run. Any
# rename here must keep the corresponding literal path in sync;
# test_select_tests.py's rule-table path-fidelity tests enforce that.

HOOKS_DIR = "claude/.claude/hooks"
HOOKS_TESTS_DIR = "claude/.claude/hooks/tests"
SCRIPTS_DIR = "claude/.claude/scripts"
SCRIPTS_TESTS_DIR = "claude/.claude/scripts/tests"
SKILLS_DIR = "claude/.claude/skills"
SKILLS_TESTS_DIR = "claude/.claude/skills/tests"
LOVABLE_CLOUD_DIR = "plugins/lovable-cloud"
LOVABLE_CLOUD_TESTS_DIR = "plugins/lovable-cloud/tests"
SKILL_MANAGEMENT_SCRIPTS_DIR = "plugins/skill-management/scripts"
SKILL_EVALS_RUNNER = "evals/run_skill_evals.py"

# test_transcript_analysis.py and its two siblings shell into specific hook
# scripts and read specific SKILL.md files by path, not by import.
# Domain-narrowing can't see that dependency, so it's declared here as a
# cross-domain exception rather than folded into the scripts domain rule.
TRANSCRIPT_ANALYSIS_TEST_GLOB = "claude/.claude/scripts/tests/test_transcript_analysis*.py"

SELECT_TESTS_SCRIPT = "claude/.claude/scripts/select-tests.py"

# test_plugin_manifests.py globs every plugin's .claude-plugin/plugin.json
# by file path, not by import — the same undeclared-dependency shape as
# TRANSCRIPT_ANALYSIS_TEST_GLOB above, but only lovable-cloud needs an
# explicit exception: it's the only plugin with a DOMAIN_RULES entry broad
# enough to otherwise "claim" this path and suppress the safe unmatched-path
# fallback to the full suite.
LOVABLE_CLOUD_PLUGIN_MANIFEST = "plugins/lovable-cloud/.claude-plugin/plugin.json"

# Mirrors CI's own collectible pytest scope (see pyproject.toml's pythonpath
# and .github/workflows/tests.yml's `pytest claude/.claude/ plugins/` step).
# plugins/lovable-cloud/ is the only plugins/ subtree with a tests/ directory.
FULL_SUITE_TARGETS: tuple[str, ...] = ("claude/.claude/", "plugins/lovable-cloud/")

# Each path below forces a full-suite run rather than a domain selection:
# - claude/.claude/tests/helpers.py is imported by every domain's own test dir
# - pyproject.toml governs collection for all of them
# - this script's own table can't be trusted to correctly select tests for
#   itself once changed
GLOBAL_TRIGGER_PATHS: frozenset[str] = frozenset({
    "claude/.claude/tests/helpers.py",
    "pyproject.toml",
    SELECT_TESTS_SCRIPT,
})


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory + "/")


def _is_skill_md_change(path: str) -> bool:
    return _is_under(path, SKILLS_DIR) and Path(path).name == "SKILL.md"


def _is_hooks_or_skills_change(path: str) -> bool:
    return _is_under(path, HOOKS_DIR) or _is_skill_md_change(path)


def _is_skill_management_or_evals_change(path: str) -> bool:
    return _is_under(path, SKILL_MANAGEMENT_SCRIPTS_DIR) or path == SKILL_EVALS_RUNNER


# (predicate, target paths added when it matches) — a plain domain rule.
DOMAIN_RULES: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (lambda p: _is_under(p, HOOKS_DIR), (HOOKS_TESTS_DIR,)),
    (lambda p: _is_under(p, SCRIPTS_DIR), (SCRIPTS_TESTS_DIR,)),
    (_is_skill_md_change, (SKILLS_TESTS_DIR,)),
    (lambda p: _is_under(p, LOVABLE_CLOUD_DIR), (LOVABLE_CLOUD_TESTS_DIR,)),
)

# (predicate, target paths added when it matches) — a cross-domain exception.
# Nothing here checks completeness against real cross-domain file reads in
# the test suite: when a test starts reading a file outside its own
# domain-rule tree by path or subprocess (as TRANSCRIPT_ANALYSIS_TEST_GLOB
# and LOVABLE_CLOUD_PLUGIN_MANIFEST both already needed), audit this table
# by hand and add the matching entry.
CROSS_DOMAIN_EXCEPTIONS: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (_is_hooks_or_skills_change, (TRANSCRIPT_ANALYSIS_TEST_GLOB,)),
    (_is_skill_management_or_evals_change, (SKILLS_TESTS_DIR,)),
    (lambda p: p == LOVABLE_CLOUD_PLUGIN_MANIFEST, (SKILLS_TESTS_DIR,)),
)


class SelectionResult(NamedTuple):
    target_paths: tuple[str, ...]
    is_full_suite: bool
    reason: str


def select_pytest_targets(changed_paths: Iterable[str]) -> SelectionResult:
    """Map a changed-path set to pytest targets via DOMAIN_RULES/CROSS_DOMAIN_EXCEPTIONS.

    Fails open to FULL_SUITE_TARGETS when:
    - the diff is empty
    - a global-trigger path is present (checked before domain matching, so a
      domain match can never suppress it)
    - any changed path matches no rule at all
    """
    changed = list(changed_paths)
    if not changed:
        return SelectionResult(FULL_SUITE_TARGETS, True, "empty-diff")
    if any(path in GLOBAL_TRIGGER_PATHS for path in changed):
        return SelectionResult(FULL_SUITE_TARGETS, True, "global-trigger")

    targets: set[str] = set()
    for path in changed:
        matched = False
        for predicate, domain_targets in DOMAIN_RULES:
            if predicate(path):
                targets.update(domain_targets)
                matched = True
        for predicate, exception_targets in CROSS_DOMAIN_EXCEPTIONS:
            if predicate(path):
                targets.update(exception_targets)
                matched = True
        if not matched:
            return SelectionResult(FULL_SUITE_TARGETS, True, "unmatched-path")

    return SelectionResult(tuple(sorted(targets)), False, "domain-selected")


# --- Git plumbing -------------------------------------------------------


class GitDiffUnavailable(Exception):
    """Raised when the merge-base or diff lookup against origin/main fails;
    the caller falls back to the full suite rather than a silent bad
    selection."""


def _run_git(args: list[str], *, cwd: Path, run) -> str | None:
    try:
        result = run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def resolve_repo_root(*, cwd: Path, run=subprocess.run) -> Path:
    """Return the repo/worktree root containing cwd, or cwd itself if git
    can't resolve one (e.g. not a git checkout at all)."""
    output = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd, run=run)
    if output is None or not output.strip():
        return cwd
    return Path(output.strip())


def compute_changed_paths(repo_root: Path, *, run=subprocess.run) -> list[str]:
    """Return the sorted union of every path changed on HEAD since
    diverging from origin/main plus every dirty or untracked working-tree
    path, relative to repo_root. Raises GitDiffUnavailable if any of the
    underlying git calls fails (most commonly: no origin/main to diverge
    from, e.g. a detached HEAD with no origin remote configured)."""
    merge_base_output = _run_git(["merge-base", "HEAD", "origin/main"], cwd=repo_root, run=run)
    if merge_base_output is None or not merge_base_output.strip():
        raise GitDiffUnavailable("could not resolve merge-base against origin/main")
    merge_base = merge_base_output.strip()

    committed = _run_git(["diff", "--name-only", f"{merge_base}...HEAD"], cwd=repo_root, run=run)
    if committed is None:
        raise GitDiffUnavailable("git diff against the merge-base failed")
    dirty = _run_git(["diff", "--name-only", "HEAD"], cwd=repo_root, run=run)
    if dirty is None:
        raise GitDiffUnavailable("git diff against HEAD failed")
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"], cwd=repo_root, run=run)
    if untracked is None:
        raise GitDiffUnavailable("git ls-files for untracked files failed")

    # git's default core.quotePath=true escapes non-ASCII path bytes in this
    # output, and _run_git does not override it. A non-ASCII changed path
    # therefore falls open to the full suite instead of matching its domain.
    changed = set(committed.splitlines()) | set(dirty.splitlines()) | set(untracked.splitlines())
    return sorted(path for path in changed if path)


# --- pytest invocation ----------------------------------------------------


def _expand_target(target: str, *, repo_root: Path) -> list[str]:
    """A plain directory/file target passes through unchanged; a
    glob-pattern target (e.g. TRANSCRIPT_ANALYSIS_TEST_GLOB) expands to its
    concrete repo-root-relative matches, sorted for deterministic argv."""
    if "*" not in target:
        return [target]
    return sorted(str(match.relative_to(repo_root)) for match in repo_root.glob(target))


def build_pytest_argv(
    target_paths: Iterable[str], passthrough_args: Iterable[str], *, repo_root: Path,
) -> list[str]:
    expanded: list[str] = []
    for target in target_paths:
        expanded.extend(_expand_target(target, repo_root=repo_root))
    return [*expanded, *list(passthrough_args)]


def _resolve_pytest_executable() -> str:
    """Resolves pytest from the sys.executable sibling (e.g. .venv/bin/pytest)
    rather than trusting PATH, since this script is normally invoked with a
    venv-prefixed interpreter and that sibling is the environment actually
    running it. Falls back to a bare "pytest" lookup on PATH when no such
    sibling exists."""
    sibling = Path(sys.executable).with_name("pytest")
    return str(sibling) if sibling.exists() else "pytest"


def run_pytest(pytest_argv: list[str], *, cwd: Path, run=subprocess.run) -> int:
    executable = _resolve_pytest_executable()
    result = run([executable, *pytest_argv], cwd=cwd, check=False)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    passthrough_args = sys.argv[1:] if argv is None else argv
    repo_root = resolve_repo_root(cwd=Path.cwd())

    try:
        changed_paths = compute_changed_paths(repo_root)
    except GitDiffUnavailable as exc:
        print(f"select-tests: {exc}; falling back to the full suite", file=sys.stderr)
        selection = SelectionResult(FULL_SUITE_TARGETS, True, "git-unavailable")
    else:
        selection = select_pytest_targets(changed_paths)

    if selection.is_full_suite:
        print(f"select-tests: running the full suite ({selection.reason})", file=sys.stderr)
    else:
        print(f"select-tests: running {', '.join(selection.target_paths)}", file=sys.stderr)

    pytest_argv = build_pytest_argv(selection.target_paths, passthrough_args, repo_root=repo_root)
    return run_pytest(pytest_argv, cwd=repo_root)


if __name__ == "__main__":
    sys.exit(main())
