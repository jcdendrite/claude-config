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
# Common ancestor for the plugin-generic hooks/skills/agents predicates below.
PLUGINS_DIR = "plugins"
LOVABLE_CLOUD_DIR = "plugins/lovable-cloud"
LOVABLE_CLOUD_TESTS_DIR = "plugins/lovable-cloud/tests"
LOVABLE_CLOUD_SCRIPTS_DIR = "plugins/lovable-cloud/scripts"
LOVABLE_CLOUD_LIB_DIR = "plugins/lovable-cloud/lib"
SKILL_MANAGEMENT_SCRIPTS_DIR = "plugins/skill-management/scripts"
SKILL_EVALS_RUNNER = "evals/run_skill_evals.py"

# Common ancestor for the repo-wide-scan cross-domain exception below,
# mirroring PLUGINS_DIR's role for the plugin-generic predicates.
CLAUDE_TOP_LEVEL_DIR = "claude"

# test_transcript_analysis.py and its two siblings shell into specific hook
# scripts and read specific SKILL.md files by path, not by import.
# Domain-narrowing can't see that dependency, so it's declared here as a
# cross-domain exception rather than folded into the scripts domain rule.
TRANSCRIPT_ANALYSIS_TEST_GLOB = "claude/.claude/scripts/tests/test_transcript_analysis*.py"

# test_ticket_reference_discipline.py statically scans every tracked .py and
# .sh file under claude/ and plugins/ for ticket-prefixed identifiers,
# independent of any import graph. Same undeclared-dependency shape as
# TRANSCRIPT_ANALYSIS_TEST_GLOB, naming the one dependent test file rather
# than its containing domain.
TICKET_REFERENCE_DISCIPLINE_TEST_PATH = "claude/.claude/hooks/tests/test_ticket_reference_discipline.py"

SELECT_TESTS_SCRIPT = "claude/.claude/scripts/select-tests.py"

# test_select_tests.py's own TestCrossDomainReadCompleteness parses every
# test_*.py under HOOKS_TESTS_DIR, SCRIPTS_TESTS_DIR, SKILLS_TESTS_DIR, and
# plugins/*/tests/ for module-level repo-path constants, so a change to any
# of those files can introduce a read this table hasn't declared yet.
SELECT_TESTS_TEST_PATH = "claude/.claude/scripts/tests/test_select_tests.py"

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
# LOVABLE_CLOUD_PLUGIN_MANIFEST. Stays outside SKILL_FILES_READ_BY_HOOK_TESTS
# below because test_check_handoff.py lives in SCRIPTS_TESTS_DIR, not
# HOOKS_TESTS_DIR -- that set's shared (HOOKS_TESTS_DIR,) row doesn't carry
# this file's second target.
HANDOFF_SKILL_MD = "claude/.claude/skills/handoff/SKILL.md"

CODE_REVIEW_SKILL_MD = "claude/.claude/skills/code-review/SKILL.md"
PLAN_REVIEW_ROUTING_MD = "claude/.claude/skills/plan-review/ROUTING.md"
PLAN_REVIEW_SKILL_MD = "claude/.claude/skills/plan-review/SKILL.md"
RESPOND_PR_SKILL_MD = "claude/.claude/skills/respond-pr/SKILL.md"
ERROR_MODE_ANALYSIS_SKILL_MD = "claude/.claude/skills/error-mode-analysis/SKILL.md"
READY_FOR_REVIEW_SKILL_MD = "claude/.claude/skills/ready-for-review/SKILL.md"
AI_INSTRUCTION_AND_MEMORY_FILES_SKILL_MD = "claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md"
SKILL_REVIEW_SKILL_MD = "plugins/skill-management/skills/skill-review/SKILL.md"

# Every SKILL.md a HOOKS_TESTS_DIR test reads by exact path rather than by
# domain membership. TestCrossDomainReadCompleteness
# (claude/.claude/scripts/tests/test_select_tests.py) derives and enforces
# this set from each reading test's own module-level path constant, so no
# per-member citation comment is kept here. HANDOFF_SKILL_MD stays a
# standalone exception rather than joining this set -- see its own comment
# above for why.
SKILL_FILES_READ_BY_HOOK_TESTS: frozenset[str] = frozenset({
    CODE_REVIEW_SKILL_MD,
    PLAN_REVIEW_ROUTING_MD,
    PLAN_REVIEW_SKILL_MD,
    RESPOND_PR_SKILL_MD,
    ERROR_MODE_ANALYSIS_SKILL_MD,
    READY_FOR_REVIEW_SKILL_MD,
    AI_INSTRUCTION_AND_MEMORY_FILES_SKILL_MD,
    SKILL_REVIEW_SKILL_MD,
})

# test_ci_path_filter.py reads this exact file by path.
GITHUB_ACTIONS_WORKFLOWS_RULE_MD = "claude/.claude/rules/github-actions-workflows.md"

# test_hook_alignment.py (HOOKS_TESTS_DIR) reads this file's permissions.allow
# entries by path. test_doc_counts.py (HOOKS_TESTS_DIR) reads its
# skillOverrides counts. test_skills.py (SKILLS_TESTS_DIR) reads its
# skillOverrides map at line 153, its docs/skills.md cross-check at line 1686,
# and its destructive-cleanup permissions check at line 1724.
# test_claude_enable_tool.py (SCRIPTS_TESTS_DIR) reads it by path to assert
# which settings payload backs a re-enabled session.
CLAUDE_SETTINGS_JSON = "claude/.claude/settings.json"

# No test reads any file under this directory by path or subprocess.
PLANS_DIR = ".claude/plans"

# test_ci_path_filter.py's only reference to this literal string is a static
# CI ignore-paths allowlist entry, independent of the file's content.
CHANGELOG_MD = "CHANGELOG.md"

# test_transcript_analysis_architecture_doc.py (SCRIPTS_TESTS_DIR) reads
# this exact file by path.
TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD = "docs/transcript-analysis-architecture.md"

# Blanket for every file under docs/, rather than one exact-match constant
# per file: test_hook_alignment.py reads docs/hooks.md, test_doc_counts.py
# reads docs/design-decisions.md, docs/skills.md, and docs/handoff-nudge.md,
# and test_skills.py's test_doc_has_no_state_path parametrizes over nearly
# every docs/**/*.md file for a per-account state-path contract. A per-file
# constant list would silently under-select the day a new doc gains a test
# dependency; this rule can't.
DOCS_DIR = "docs"

# test_doc_counts.py (HOOKS_TESTS_DIR) pins reviewer-agent and token-cap
# counts here. test_skills.py's test_doc_has_no_state_path (SKILLS_TESTS_DIR)
# also reads it for the per-account state-path contract.
# test_output_preferences_layering.py (HOOKS_TESTS_DIR) reads its Output
# preferences section's template block and its pointer to GLOBAL_CLAUDE_MD.
README_MD = "README.md"

# The test_install_sh_*.py family and test_shellcheck.py, both in
# HOOKS_TESTS_DIR, read this file by path.
INSTALL_SH = "install.sh"

# test_skills.py (SKILLS_TESTS_DIR) and test_doc_counts.py (HOOKS_TESTS_DIR)
# each read it by path. test_nudge_transcript_toolkit.py's
# TestNeverFiresOnMarkdown (HOOKS_TESTS_DIR) also picks it up via its
# repo-wide rglob("*.md") content scan. test_output_preferences_layering.py
# (HOOKS_TESTS_DIR) reads it for the "## Prose and Output Format" heading.
GLOBAL_CLAUDE_MD = "claude/.claude/CLAUDE.md"

# test_nudge_transcript_toolkit.py's TestNeverFiresOnMarkdown (HOOKS_TESTS_DIR)
# builds its corpus via REPO_ROOT.rglob("*.md") reading file content, the
# same dependency GLOBAL_CLAUDE_MD cites above. Unlike that file, no test
# reads this one by path, so only HOOKS_TESTS_DIR is implicated.
ROOT_CLAUDE_MD = "CLAUDE.md"

# test_rules_frontmatter.py (SKILLS_TESTS_DIR) rglobs both this directory
# and RULES_DIR (claude/.claude/rules/) for frontmatter validation —
# distinct from RULES_DIR's own exception below, since the two directories
# are separate trees with the same test dependency.
ROOT_RULES_DIR = ".claude/rules"

# test_skills.py's _all_skill_md_files() (SKILLS_TESTS_DIR) globs
# .claude/skills/*/SKILL.md by path, one of three SKILL.md-glob roots
# alongside SKILLS_DIR and plugins/*/skills/*/SKILL.md.
ROOT_SKILLS_DIR = ".claude/skills"

# test_claude_md_excludes.py (HOOKS_TESTS_DIR) reads this exact file's
# claudeMdExcludes entry by path.
ROOT_SETTINGS_JSON = ".claude/settings.json"

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

# claude/.claude/tests/test_statusline_command.py reads
# claude/.claude/statusline-command.sh by path, and its sibling helpers.py
# reads .github/workflows/tests.yml by path. Both paths deliberately fall
# open to the full suite instead of getting a CROSS_DOMAIN_EXCEPTIONS entry,
# because claude/.claude/tests/ itself has no selectable pytest target.
DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS: frozenset[str] = frozenset({"tests"})

# Directory names directly under root .claude/ that DOMAIN_RULES or
# CROSS_DOMAIN_EXCEPTIONS predicates reference by path (PLANS_DIR,
# ROOT_RULES_DIR, ROOT_SKILLS_DIR). Mirrors MAPPED_TOP_LEVEL_DIRS's role for
# claude/.claude/, but for the separate root .claude/ tree. Unlike that
# sibling, root .claude/ has no directory-with-no-selectable-pytest-target
# case, so it needs no DELIBERATELY_UNMAPPED counterpart.
MAPPED_ROOT_CLAUDE_DIRS: frozenset[str] = frozenset({
    Path(PLANS_DIR).name,
    Path(ROOT_RULES_DIR).name,
    Path(ROOT_SKILLS_DIR).name,
})

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


# test_skill_citations_resolve_to_real_headings (SKILLS_TESTS_DIR) scans every
# REFERENCES.md and ROUTING.md sibling of a SKILL.md, not just SKILL.md itself.
# This set must stay in sync with _citation_sources_for_skill_md's sibling
# names in test_skills.py — a shared constant would be warranted if a third
# auxiliary filename type is ever added.
def _is_skill_auxiliary_md_change(path: str) -> bool:
    return _is_under(path, SKILLS_DIR) and Path(path).name in {"REFERENCES.md", "ROUTING.md"}


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


def _is_plugin_subpath(path: str, subdirectory: str) -> bool:
    parts = Path(path).parts
    return len(parts) > 3 and parts[0] == PLUGINS_DIR and parts[2] == subdirectory


def _is_plugin_hooks_change(path: str) -> bool:
    return _is_plugin_subpath(path, "hooks")


def _is_plugin_skills_change(path: str) -> bool:
    return _is_plugin_subpath(path, "skills")


def _is_plugin_agents_change(path: str) -> bool:
    return _is_plugin_subpath(path, "agents")


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


def _is_under_deliberately_unmapped_claude_dir(path: str) -> bool:
    return any(
        _is_under(path, f"{CLAUDE_TOP_LEVEL_DIR}/.claude/{name}")
        for name in DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS
    )


# See TICKET_REFERENCE_DISCIPLINE_TEST_PATH's own comment above for what
# that test scans. This predicate is deliberately .py-only. That test's .sh
# coverage is achieved today only incidentally, through the existing
# hooks/scripts shell-script domain rules.
# Selects TICKET_REFERENCE_DISCIPLINE_TEST_PATH directly rather than the
# HOOKS_TESTS_DIR domain it lives in. Excludes
# DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS (claude/.claude/tests/):
# - test_statusline_command.py and test_pytest_collection_config.py live there
# - excluding them keeps those two files falling open to the full suite via
#   unmatched-path, instead of narrowing to a domain that doesn't contain them
def _is_py_source_under_claude_or_plugins(path: str) -> bool:
    return (
        path.endswith(".py")
        and (_is_under(path, CLAUDE_TOP_LEVEL_DIR) or _is_under(path, PLUGINS_DIR))
        and not _is_under_deliberately_unmapped_claude_dir(path)
    )


# Matches exactly the corpus SELECT_TESTS_TEST_PATH's own comment describes:
# a test_*.py file directly inside a tests/ directory under claude/ or
# plugins/. A stricter subset of _is_py_source_under_claude_or_plugins,
# since only a test file can introduce a new module-level repo-path
# constant for that scanner to miss.
def _is_test_source_change(path: str) -> bool:
    return (
        _is_py_source_under_claude_or_plugins(path)
        and Path(path).parent.name == "tests"
        and Path(path).name.startswith("test_")
    )


# (predicate, target paths added when it matches) — a plain domain rule.
DOMAIN_RULES: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (lambda p: _is_under(p, HOOKS_DIR), (HOOKS_TESTS_DIR,)),
    (lambda p: _is_under(p, SCRIPTS_DIR), (SCRIPTS_TESTS_DIR,)),
    (_is_skill_md_change, (SKILLS_TESTS_DIR,)),
    (_is_skill_auxiliary_md_change, (SKILLS_TESTS_DIR,)),
    (lambda p: _is_under(p, SKILLS_TESTS_DIR), (SKILLS_TESTS_DIR,)),
    (lambda p: _is_under(p, LOVABLE_CLOUD_DIR), (LOVABLE_CLOUD_TESTS_DIR,)),
    (lambda p: _is_under(p, PLANS_DIR), ()),
    (lambda p: p == CHANGELOG_MD, ()),
)

# (predicate, target paths added when it matches) — a cross-domain exception.
# TestCrossDomainReadCompleteness (claude/.claude/scripts/tests/test_select_tests.py)
# derives this table's required entries by scanning test sources, so a new
# undeclared cross-domain read fails CI.
# It resolves only module-level constants built as a Path chain of string
# literals rooted at __file__ or at a path constant from
# claude/.claude/tests/helpers.py. A path assembled inside a function body,
# from a plain string, or from any other call still needs a hand-added entry
# here.
# It matches a constant by its presence, not by checking that the name is
# later passed to a read call, so a constant left behind by a refactor keeps
# its row alive with no signal to prune it.
# It verifies precision, not recall: a read whose constant the resolver
# cannot see is invisible to both the scan and the hand-written audit list.
# A green run therefore means no known read is unmapped, not that none
# exists.
#
# _is_hooks_or_skills_change: TRANSCRIPT_ANALYSIS_TEST_GLOB shells into hook
# scripts and reads SKILL.md files by path.
# _is_skill_management_or_evals_change: SKILLS_TESTS_DIR covers the skill
# validator scripts and eval runner it exercises.
# LOVABLE_CLOUD_PLUGIN_MANIFEST: test_plugin_manifests.py (SKILLS_TESTS_DIR)
# globs every plugin's plugin.json by path.
# _is_plugin_hooks_change: test_hook_alignment.py and test_lib.py
# (HOOKS_TESTS_DIR) glob plugins/*/hooks/*.sh.
# _is_plugin_skills_change: test_skills.py (SKILLS_TESTS_DIR) globs
# plugins/*/skills/*/SKILL.md.
# _is_plugin_agents_change: test_agent_roster.py (HOOKS_TESTS_DIR) globs
# plugins/*/agents/*.md for a cross-scope agent-name-collision check.
# test_skills.py (SKILLS_TESTS_DIR) globs the same path for its state-path
# contract.
# _is_lovable_cloud_shell_script_change: test_shellcheck.py (HOOKS_TESTS_DIR)
# lints every tracked shell script in the repo, not only claude/.claude/hooks/.
# _is_scripts_dir_shell_script_change: same test_shellcheck.py dependency as
# above, for a shell script under claude/.claude/scripts/ rather than
# plugins/lovable-cloud/scripts/ or /lib/.
# test_skills.py's test_scripts_are_executable (SKILLS_TESTS_DIR) globs
# SCRIPTS_DIR for a .sh executable-bit check, non-recursively; the
# corresponding exception below over-selects for a nested SCRIPTS_DIR script
# that glob wouldn't catch, since over-selection is the safe direction.
# SKILL_FILES_READ_BY_HOOK_TESTS: see that frozenset's own comment above for
# what it covers and why HANDOFF_SKILL_MD isn't a member.
# HANDOFF_SKILL_MD: test_check_handoff.py (SCRIPTS_TESTS_DIR) and
# test_restore_authorization_boundary_on_compact.py (HOOKS_TESTS_DIR) each
# read this exact file by path.
# _is_hooks_dir_shell_script_change: test_no_bash4_constructs.py
# (SCRIPTS_TESTS_DIR) recursively globs claude/.claude/ for *.sh files,
# picking up claude/.claude/hooks/ in addition to its own SCRIPTS_DIR.
# _is_plugin_hooks_change, _is_plugin_skills_change, and
# _is_plugin_agents_change match every plugin under plugins/, not only
# lovable-cloud -- the test globs cited above are plugin-generic, so the
# predicate has to be too.
# AGENTS_DIR: test_agent_roster.py (HOOKS_TESTS_DIR) and test_skills.py
# (SKILLS_TESTS_DIR) both read claude/.claude/agents/*.md by path.
# RULES_DIR: test_rules_frontmatter.py (SKILLS_TESTS_DIR) rglobs
# claude/.claude/rules/*.md by path.
# GITHUB_ACTIONS_WORKFLOWS_RULE_MD: test_ci_path_filter.py (HOOKS_TESTS_DIR)
# reads this exact file by path.
# TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD: test_transcript_analysis_architecture_doc.py
# (SCRIPTS_TESTS_DIR) reads this exact file by path, in addition to the
# DOCS_DIR blanket below.
# DOCS_DIR, README_MD, INSTALL_SH, and CLAUDE_SETTINGS_JSON: see each
# constant's own comment above for its citation.
# GLOBAL_CLAUDE_MD, ROOT_CLAUDE_MD, ROOT_RULES_DIR, ROOT_SKILLS_DIR, and
# ROOT_SETTINGS_JSON: see each constant's own comment above for its citation.
# _is_py_source_under_claude_or_plugins: see its own comment above for
# citation. Selects TICKET_REFERENCE_DISCIPLINE_TEST_PATH directly, not a
# domain directory.
# _is_test_source_change: see SELECT_TESTS_TEST_PATH's own comment above for
# citation. A strict subset of _is_py_source_under_claude_or_plugins, since
# only a test file under one of the four selectable test directories can
# introduce a constant TestCrossDomainReadCompleteness's own scan would need
# to see.
CROSS_DOMAIN_EXCEPTIONS: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (_is_hooks_or_skills_change, (TRANSCRIPT_ANALYSIS_TEST_GLOB,)),
    (_is_skill_management_or_evals_change, (SKILLS_TESTS_DIR,)),
    (lambda p: p == LOVABLE_CLOUD_PLUGIN_MANIFEST, (SKILLS_TESTS_DIR,)),
    (_is_plugin_hooks_change, (HOOKS_TESTS_DIR,)),
    (_is_plugin_skills_change, (SKILLS_TESTS_DIR,)),
    (_is_plugin_agents_change, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (_is_lovable_cloud_shell_script_change, (HOOKS_TESTS_DIR,)),
    (_is_scripts_dir_shell_script_change, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p in SKILL_FILES_READ_BY_HOOK_TESTS, (HOOKS_TESTS_DIR,)),
    (lambda p: p == HANDOFF_SKILL_MD, (SCRIPTS_TESTS_DIR, HOOKS_TESTS_DIR)),
    (_is_hooks_dir_shell_script_change, (SCRIPTS_TESTS_DIR,)),
    (lambda p: _is_under(p, AGENTS_DIR), (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: _is_under(p, RULES_DIR), (SKILLS_TESTS_DIR,)),
    (lambda p: p == GITHUB_ACTIONS_WORKFLOWS_RULE_MD, (HOOKS_TESTS_DIR,)),
    (lambda p: p == TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD, (SCRIPTS_TESTS_DIR,)),
    (lambda p: _is_under(p, DOCS_DIR), (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == README_MD, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == INSTALL_SH, (HOOKS_TESTS_DIR,)),
    (lambda p: p == CLAUDE_SETTINGS_JSON, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR, SCRIPTS_TESTS_DIR)),
    (lambda p: p == GLOBAL_CLAUDE_MD, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == ROOT_CLAUDE_MD, (HOOKS_TESTS_DIR,)),
    (lambda p: _is_under(p, ROOT_RULES_DIR), (SKILLS_TESTS_DIR,)),
    (lambda p: _is_under(p, ROOT_SKILLS_DIR), (SKILLS_TESTS_DIR,)),
    (lambda p: p == ROOT_SETTINGS_JSON, (HOOKS_TESTS_DIR,)),
    (_is_py_source_under_claude_or_plugins, (TICKET_REFERENCE_DISCIPLINE_TEST_PATH,)),
    (_is_test_source_change, (SELECT_TESTS_TEST_PATH,)),
)


class SelectionResult(NamedTuple):
    target_paths: tuple[str, ...]
    is_full_suite: bool
    reason: str
    # Populated for "global-trigger" and "unmatched-path" so the caller can
    # name the offending path(s) instead of only the reason code. Left empty
    # for "empty-diff" and "git-unavailable", neither of which has one.
    triggering_paths: tuple[str, ...] = ()


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
    global_trigger_paths = tuple(path for path in changed if path in GLOBAL_TRIGGER_PATHS)
    if global_trigger_paths:
        return SelectionResult(FULL_SUITE_TARGETS, True, "global-trigger", global_trigger_paths)

    targets: set[str] = set()
    unmatched_paths: list[str] = []
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
            unmatched_paths.append(path)

    if unmatched_paths:
        return SelectionResult(FULL_SUITE_TARGETS, True, "unmatched-path", tuple(unmatched_paths))

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
        if selection.triggering_paths:
            paths = ", ".join(selection.triggering_paths)
            print(f"select-tests: running the full suite ({selection.reason}: {paths})", file=sys.stderr)
        else:
            print(f"select-tests: running the full suite ({selection.reason})", file=sys.stderr)
    elif not selection.target_paths:
        print(f"select-tests: nothing to run ({selection.reason})", file=sys.stderr)
        return 0
    else:
        print(f"select-tests: running {', '.join(selection.target_paths)}", file=sys.stderr)

    pytest_argv = build_pytest_argv(selection.target_paths, passthrough_args, repo_root=repo_root)
    return run_pytest(pytest_argv, cwd=repo_root)


if __name__ == "__main__":
    sys.exit(main())
