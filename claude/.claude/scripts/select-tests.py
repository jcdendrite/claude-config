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

# Hang-detection backstop, not a measured worst case.
# Sized between post-crash-sessions.py's 5.0s and 25.0s timeouts.
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
AGENTS_DIR = "claude/.claude/agents"
RULES_DIR = "claude/.claude/rules"
LOVABLE_CLOUD_DIR = "plugins/lovable-cloud"
LOVABLE_CLOUD_TESTS_DIR = "plugins/lovable-cloud/tests"
LOVABLE_CLOUD_HOOKS_DIR = "plugins/lovable-cloud/hooks"
LOVABLE_CLOUD_SKILLS_DIR = "plugins/lovable-cloud/skills"
LOVABLE_CLOUD_AGENTS_DIR = "plugins/lovable-cloud/agents"
LOVABLE_CLOUD_SCRIPTS_DIR = "plugins/lovable-cloud/scripts"
LOVABLE_CLOUD_LIB_DIR = "plugins/lovable-cloud/lib"
SKILL_MANAGEMENT_SCRIPTS_DIR = "plugins/skill-management/scripts"
SKILL_EVALS_RUNNER = "evals/run_skill_evals.py"

# test_transcript_analysis.py and its two siblings shell into specific hook
# scripts and read specific SKILL.md files by path, not by import.
# Domain-narrowing can't see that dependency, so it's declared here as a
# cross-domain exception rather than folded into the scripts domain rule.
TRANSCRIPT_ANALYSIS_TEST_GLOB = "claude/.claude/scripts/tests/test_transcript_analysis*.py"

SELECT_TESTS_SCRIPT = "claude/.claude/scripts/select-tests.py"

# test_plugin_manifests.py globs every plugin's .claude-plugin/plugin.json
# by path, not by import.
# Same undeclared-dependency shape as TRANSCRIPT_ANALYSIS_TEST_GLOB.
# Only lovable-cloud needs an explicit exception, because its DOMAIN_RULES
# entry is the only one broad enough to otherwise claim this path ahead of
# the unmatched-path fallback.
LOVABLE_CLOUD_PLUGIN_MANIFEST = "plugins/lovable-cloud/.claude-plugin/plugin.json"

# check-handoff.py hardcodes this path.
# test_check_handoff.py reads it directly by path, not by import.
# Same undeclared-dependency shape as TRANSCRIPT_ANALYSIS_TEST_GLOB and
# LOVABLE_CLOUD_PLUGIN_MANIFEST.
HANDOFF_SKILL_MD = "claude/.claude/skills/handoff/SKILL.md"

# test_reconciliation_block_consistency.py reads this exact file by path to
# diff its Reconciliation block against plan-review/ROUTING.md.
CODE_REVIEW_SKILL_MD = "claude/.claude/skills/code-review/SKILL.md"

# test_ci_path_filter.py reads this exact file by path.
GITHUB_ACTIONS_WORKFLOWS_RULE_MD = "claude/.claude/rules/github-actions-workflows.md"

# No test reads any file under this directory by path or subprocess.
PLANS_DIR = ".claude/plans"

# test_ci_path_filter.py's only reference to this literal string is a static
# CI ignore-paths allowlist entry, independent of the file's content.
CHANGELOG_MD = "CHANGELOG.md"

# test_transcript_analysis_architecture_doc.py (SCRIPTS_TESTS_DIR) reads
# this exact file by path.
TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD = "docs/transcript-analysis-architecture.md"

# No test reads this file's content by path.
TRANSCRIPT_ANALYSIS_DOC_MD = "docs/transcript-analysis.md"

# Directory names directly under claude/.claude/ that DOMAIN_RULES or
# CROSS_DOMAIN_EXCEPTIONS predicates reference. Backs
# TestRuleTablePathFidelity's exhaustiveness check: a real top-level
# directory absent from both this set and DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS
# means some test's cross-domain file-path or subprocess read into it was
# never audited into this table.
MAPPED_TOP_LEVEL_DIRS: frozenset[str] = frozenset({
    Path(HOOKS_DIR).name,
    Path(SCRIPTS_DIR).name,
    Path(SKILLS_DIR).name,
    Path(AGENTS_DIR).name,
    Path(RULES_DIR).name,
})

# claude/.claude/tests/ reads no file outside the paths GLOBAL_TRIGGER_PATHS
# already covers, so it needs no CROSS_DOMAIN_EXCEPTIONS entry.
DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS: frozenset[str] = frozenset({"tests"})

# Matches CI's own collectible pytest scope verbatim (see
# .github/workflows/tests.yml's `pytest claude/.claude/ plugins/` step).
# Targeting plugins/ instead of enumerating individual plugin subtrees means
# a new plugin gaining a tests/ directory is covered automatically.
FULL_SUITE_TARGETS: tuple[str, ...] = ("claude/.claude/", "plugins/")

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
    # Scoped to .py files, matching SKILL_EVALS_RUNNER's own precision.
    # A directory-wide match would foreclose the unmatched-path fallback
    # that protects every other plugin's scripts/ directory once this one
    # gains a non-.py file (e.g. a shell script) needing its own check.
    # Misses a wrongly-extensioned or dotfile Python script under this
    # directory, same trade-off as _is_scripts_dir_shell_script_change below.
    return (
        _is_under(path, SKILL_MANAGEMENT_SCRIPTS_DIR) and path.endswith(".py")
    ) or path == SKILL_EVALS_RUNNER


def _is_lovable_cloud_hooks_change(path: str) -> bool:
    return _is_under(path, LOVABLE_CLOUD_HOOKS_DIR)


def _is_lovable_cloud_skills_or_agents_change(path: str) -> bool:
    return _is_under(path, LOVABLE_CLOUD_SKILLS_DIR) or _is_under(path, LOVABLE_CLOUD_AGENTS_DIR)


def _is_lovable_cloud_shell_script_change(path: str) -> bool:
    return _is_under(path, LOVABLE_CLOUD_SCRIPTS_DIR) or _is_under(path, LOVABLE_CLOUD_LIB_DIR)


def _is_scripts_dir_shell_script_change(path: str) -> bool:
    # Misses a wrongly-extensioned or dotfile shell script under SCRIPTS_DIR.
    return _is_under(path, SCRIPTS_DIR) and (path.endswith(".sh") or "." not in Path(path).name)


# test_no_bash4_constructs.py's rglob("*.sh") is suffix-only.
# Unlike test_shellcheck.py's shebang-based discovery, it never matches an
# extensionless script, so this predicate omits the extensionless branch
# _is_scripts_dir_shell_script_change has.
def _is_hooks_dir_shell_script_change(path: str) -> bool:
    return _is_under(path, HOOKS_DIR) and path.endswith(".sh")


# (predicate, target paths added when it matches) — a plain domain rule.
DOMAIN_RULES: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (lambda p: _is_under(p, HOOKS_DIR), (HOOKS_TESTS_DIR,)),
    (lambda p: _is_under(p, SCRIPTS_DIR), (SCRIPTS_TESTS_DIR,)),
    (_is_skill_md_change, (SKILLS_TESTS_DIR,)),
    (lambda p: _is_under(p, LOVABLE_CLOUD_DIR), (LOVABLE_CLOUD_TESTS_DIR,)),
    (lambda p: _is_under(p, PLANS_DIR), ()),
    (lambda p: p == CHANGELOG_MD, ()),
    (lambda p: p == TRANSCRIPT_ANALYSIS_DOC_MD, ()),
)

# (predicate, target paths added when it matches) — a cross-domain exception.
# Nothing here checks completeness against real cross-domain file reads in
# the test suite. When a test starts reading a file outside its own
# domain-rule tree by path or subprocess, audit this table by hand and add
# the matching entry.
#
# _is_hooks_or_skills_change: TRANSCRIPT_ANALYSIS_TEST_GLOB shells into hook
# scripts and reads SKILL.md files by path.
# _is_skill_management_or_evals_change: SKILLS_TESTS_DIR covers the skill
# validator scripts and eval runner it exercises.
# LOVABLE_CLOUD_PLUGIN_MANIFEST: test_plugin_manifests.py (SKILLS_TESTS_DIR)
# globs every plugin's plugin.json by path.
# _is_lovable_cloud_hooks_change: test_hook_alignment.py and test_lib.py
# (HOOKS_TESTS_DIR) glob plugins/*/hooks/*.sh.
# _is_lovable_cloud_skills_or_agents_change: test_skills.py (SKILLS_TESTS_DIR)
# globs plugins/*/skills/*/SKILL.md and plugins/*/agents/*.md.
# _is_lovable_cloud_shell_script_change: test_shellcheck.py (HOOKS_TESTS_DIR)
# lints every tracked shell script in the repo, not only claude/.claude/hooks/.
# _is_scripts_dir_shell_script_change: same test_shellcheck.py dependency as
# above, for a shell script under claude/.claude/scripts/ rather than
# plugins/lovable-cloud/scripts/ or /lib/.
# test_skills.py's test_scripts_are_executable (SKILLS_TESTS_DIR) globs
# SCRIPTS_DIR for a .sh executable-bit check, non-recursively; the
# corresponding exception below over-selects for a nested SCRIPTS_DIR script
# that glob wouldn't catch, since over-selection is the safe direction.
# CODE_REVIEW_SKILL_MD: test_reconciliation_block_consistency.py
# (HOOKS_TESTS_DIR) reads this exact file by path.
# HANDOFF_SKILL_MD: test_check_handoff.py (SCRIPTS_TESTS_DIR) and
# test_restore_authorization_boundary_on_compact.py (HOOKS_TESTS_DIR) each
# read this exact file by path.
# LOVABLE_CLOUD_AGENTS_DIR: test_agent_roster.py (HOOKS_TESTS_DIR) globs
# plugins/*/agents/*.md for a cross-scope agent-name-collision check.
# _is_hooks_dir_shell_script_change: test_no_bash4_constructs.py
# (SCRIPTS_TESTS_DIR) recursively globs claude/.claude/ for *.sh files,
# picking up claude/.claude/hooks/ in addition to its own SCRIPTS_DIR.
# lovable-cloud is the only plugin whose own DOMAIN_RULES entry is broad
# enough to otherwise claim these paths ahead of the cross-plugin scans that
# actually read them.
# See TestSelectPytestTargets' unmatched-path cases for the other plugins,
# which rely on no DOMAIN_RULES entry matching them at all.
# AGENTS_DIR: test_agent_roster.py (HOOKS_TESTS_DIR) and test_skills.py
# (SKILLS_TESTS_DIR) both read claude/.claude/agents/*.md by path.
# RULES_DIR: test_rules_frontmatter.py (SKILLS_TESTS_DIR) rglobs
# claude/.claude/rules/*.md by path.
# GITHUB_ACTIONS_WORKFLOWS_RULE_MD: test_ci_path_filter.py (HOOKS_TESTS_DIR)
# reads this exact file by path.
# TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD: test_transcript_analysis_architecture_doc.py
# (SCRIPTS_TESTS_DIR) reads this exact file by path.
CROSS_DOMAIN_EXCEPTIONS: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (_is_hooks_or_skills_change, (TRANSCRIPT_ANALYSIS_TEST_GLOB,)),
    (_is_skill_management_or_evals_change, (SKILLS_TESTS_DIR,)),
    (lambda p: p == LOVABLE_CLOUD_PLUGIN_MANIFEST, (SKILLS_TESTS_DIR,)),
    (_is_lovable_cloud_hooks_change, (HOOKS_TESTS_DIR,)),
    (_is_lovable_cloud_skills_or_agents_change, (SKILLS_TESTS_DIR,)),
    (_is_lovable_cloud_shell_script_change, (HOOKS_TESTS_DIR,)),
    (_is_scripts_dir_shell_script_change, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == CODE_REVIEW_SKILL_MD, (HOOKS_TESTS_DIR,)),
    (lambda p: p == HANDOFF_SKILL_MD, (SCRIPTS_TESTS_DIR, HOOKS_TESTS_DIR)),
    (lambda p: _is_under(p, LOVABLE_CLOUD_AGENTS_DIR), (HOOKS_TESTS_DIR,)),
    (_is_hooks_dir_shell_script_change, (SCRIPTS_TESTS_DIR,)),
    (lambda p: _is_under(p, AGENTS_DIR), (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: _is_under(p, RULES_DIR), (SKILLS_TESTS_DIR,)),
    (lambda p: p == GITHUB_ACTIONS_WORKFLOWS_RULE_MD, (HOOKS_TESTS_DIR,)),
    (lambda p: p == TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD, (SCRIPTS_TESTS_DIR,)),
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
    path, relative to repo_root.

    Raises GitDiffUnavailable if any underlying git call fails. Most
    commonly: a detached HEAD with no origin remote configured, so
    origin/main can't be found.
    """
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
    """A plain directory/file target passes through unchanged.

    A glob-pattern target (e.g. TRANSCRIPT_ANALYSIS_TEST_GLOB) expands to
    its concrete repo-root-relative matches, sorted for deterministic argv.
    """
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
