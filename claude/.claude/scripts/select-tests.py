#!/usr/bin/env python3
"""select-tests.py — runs only the pytest test domains implicated by what
changed, instead of the whole suite, for a faster local dev loop. See
.claude/plans/selective-test-runs.md for the mechanism rationale and its
primary-source citations.

Usage: select-tests.py [pytest args...]
"""
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from _config import ConfigSchemaEmptyError, ConfigSchemaRowTruncatedError, config_enabled
from _config_dir import config_dir
from _skill_auxiliary_files import SKILL_AUXILIARY_MD_NAMES

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
SKILLS_DIR = "claude-skills/skills"
SKILLS_TESTS_DIR = "claude-skills/skills/tests"
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
# Doubles as its own domain: unlike the source-tree/test-dir pairs above, any
# path under it maps to itself rather than to a separate test directory.
CLAUDE_TESTS_DIR = "claude/.claude/tests"

# Common ancestor for the repo-wide-scan cross-domain exception below,
# mirroring PLUGINS_DIR's role for the plugin-generic predicates.
CLAUDE_TOP_LEVEL_DIR = "claude"
# Top-level stow package for the skills tree, mirroring CLAUDE_TOP_LEVEL_DIR's
# role in the repo-wide-scan predicate below (see root CLAUDE.md's repo-layout
# bullet for why it's a separate package).
CLAUDE_SKILLS_TOP_LEVEL_DIR = "claude-skills"
# Directory name .gitignore excludes at both worktree roots. pyproject.toml's
# norecursedirs prunes it from collection, so no test under one is ever
# collected.
WORKTREES_DIR_NAME = "worktrees"

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
# test_*.py under HOOKS_TESTS_DIR, SCRIPTS_TESTS_DIR, SKILLS_TESTS_DIR,
# CLAUDE_TESTS_DIR, and plugins/*/tests/ for module-level repo-path
# constants, so a change to any of those files can introduce a read this
# table hasn't declared yet.
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
HANDOFF_SKILL_MD = "claude-skills/skills/handoff/SKILL.md"

# Import dependency of test_skills.py; see _skill_auxiliary_files.py's docstring.
SKILL_AUXILIARY_FILES_MODULE = "claude/.claude/scripts/_skill_auxiliary_files.py"

CODE_REVIEW_SKILL_MD = "claude-skills/skills/code-review/SKILL.md"
PLAN_REVIEW_ROUTING_MD = "claude-skills/skills/plan-review/ROUTING.md"
PLAN_REVIEW_SKILL_MD = "claude-skills/skills/plan-review/SKILL.md"
RESPOND_PR_SKILL_MD = "claude-skills/skills/respond-pr/SKILL.md"
ERROR_MODE_ANALYSIS_SKILL_MD = "claude-skills/skills/error-mode-analysis/SKILL.md"
READY_FOR_REVIEW_SKILL_MD = "claude-skills/skills/ready-for-review/SKILL.md"
AI_INSTRUCTION_AND_MEMORY_FILES_SKILL_MD = "claude-skills/skills/ai-instruction-and-memory-files/SKILL.md"
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

# test_config_py.py and test_config_parser_parity.py (SCRIPTS_TESTS_DIR) read
# this exact file by path to drive _config.py against it; test_config_lib.py
# (HOOKS_TESTS_DIR) reads it via _config.sh's own schema reader. Already
# covered for HOOKS_TESTS_DIR by the blanket HOOKS_DIR domain rule -- this
# row exists so a change here (e.g. a resolution-mode or legacy-polarity
# column edit) also selects SCRIPTS_TESTS_DIR and SKILLS_TESTS_DIR, which
# otherwise have no path to it.
CONFIG_KEYS_PSV = "claude/.claude/hooks/config-keys.psv"

# test_hook_alignment.py (HOOKS_TESTS_DIR) reads this file's permissions.allow
# entries by path. test_doc_counts.py (HOOKS_TESTS_DIR) reads its
# skillOverrides counts. test_skills.py (SKILLS_TESTS_DIR) reads its
# skillOverrides map at line 153, its docs/skills.md cross-check at line 1686,
# and its destructive-cleanup permissions check at line 1724.
# test_claude_enable_tool.py (SCRIPTS_TESTS_DIR) reads it by path to assert
# which settings payload backs a re-enabled session.
CLAUDE_SETTINGS_JSON = "claude/.claude/settings.json"

# test_plan_ledger_citations_in_diff.py reads whichever files under this
# directory the current diff changed, by path, so a plan change selects it.
PLANS_DIR = ".claude/plans"

# Names the one dependent test file rather than its containing domain, like
# TICKET_REFERENCE_DISCIPLINE_TEST_PATH. Its own path change is already covered
# by the SCRIPTS_DIR row.
PLAN_LEDGER_CITATIONS_IN_DIFF_TEST_PATH = (
    "claude/.claude/scripts/tests/test_plan_ledger_citations_in_diff.py"
)

# test_skills.py (SKILLS_TESTS_DIR) loads this script and runs it over the
# plan-it worked example. Its own path change is already covered by the
# SCRIPTS_DIR row, which does not select SKILLS_TESTS_DIR.
LEDGER_CITATION_CHECKER_SCRIPT = "claude/.claude/scripts/check-ledger-citations.py"

# test_ci_path_filter.py's only reference to this literal string is a static
# CI ignore-paths allowlist entry, independent of the file's content.
CHANGELOG_MD = "CHANGELOG.md"

# test_transcript_analysis_architecture_doc.py (SCRIPTS_TESTS_DIR) reads
# this exact file by path.
TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD = "docs/transcript-analysis-architecture.md"

# Blanket for every file under docs/, rather than one exact-match constant
# per file: test_hook_alignment.py reads docs/hooks.md, test_doc_counts.py
# reads docs/design-decisions/specialist-reviewer-roster.md,
# docs/design-decisions/reviewer-findings-path-output.md, docs/skills.md, and
# docs/handoff-nudge.md, and test_skills.py's test_doc_has_no_state_path
# parametrizes over nearly every docs/**/*.md file for a per-account
# state-path contract. A per-file constant list would silently under-select
# the day a new doc gains a test dependency; this rule can't.
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
# test_claude_md_excludes.py (HOOKS_TESTS_DIR) rglobs both directories as
# well.
ROOT_RULES_DIR = ".claude/rules"

# test_skills.py's _all_skill_md_files() (SKILLS_TESTS_DIR) globs
# .claude/skills/*/SKILL.md by path, one of three SKILL.md-glob roots
# alongside SKILLS_DIR and plugins/*/skills/*/SKILL.md.
ROOT_SKILLS_DIR = ".claude/skills"

# test_claude_md_excludes.py (HOOKS_TESTS_DIR) reads this exact file's
# claudeMdExcludes entry by path.
ROOT_SETTINGS_JSON = ".claude/settings.json"

# test_statusline_command.py (CLAUDE_TESTS_DIR) reads this file by path.
# test_shellcheck.py (HOOKS_TESTS_DIR) also lints it as part of its
# tracked-shell-script sweep. test_no_bash4_constructs.py and
# test_default_branch_resolution_is_shared.py (both SCRIPTS_TESTS_DIR) pick
# it up via their own recursive *.sh globs.
STATUSLINE_COMMAND_SH = "claude/.claude/statusline-command.sh"

# Directory names directly under claude/.claude/ that DOMAIN_RULES or
# CROSS_DOMAIN_EXCEPTIONS predicates reference. Backs
# TestRuleTablePathFidelity's exhaustiveness check: a real top-level
# directory absent from this set means some test's cross-domain file-path or
# subprocess read into it was never audited into this table. SKILLS_DIR has
# no member here: it points at claude-skills/skills, outside
# claude/.claude/.
MAPPED_TOP_LEVEL_DIRS: frozenset[str] = frozenset({
    Path(HOOKS_DIR).name,
    Path(SCRIPTS_DIR).name,
    Path(AGENTS_DIR).name,
    Path(RULES_DIR).name,
    Path(CLAUDE_TESTS_DIR).name,
})

# Directory names directly under root .claude/ that DOMAIN_RULES or
# CROSS_DOMAIN_EXCEPTIONS predicates reference by path (PLANS_DIR,
# ROOT_RULES_DIR, ROOT_SKILLS_DIR). Mirrors MAPPED_TOP_LEVEL_DIRS's role for
# claude/.claude/, but for the separate root .claude/ tree.
MAPPED_ROOT_CLAUDE_DIRS: frozenset[str] = frozenset({
    Path(PLANS_DIR).name,
    Path(ROOT_RULES_DIR).name,
    Path(ROOT_SKILLS_DIR).name,
})

# Matches CI's own collectible pytest scope verbatim (see
# .github/workflows/tests.yml's `pytest claude/.claude/ claude-skills/ plugins/` step).
# Targeting plugins/ instead of enumerating individual plugin subtrees means
# a new plugin gaining a tests/ directory is covered automatically.
FULL_SUITE_TARGETS: tuple[str, ...] = ("claude/.claude/", "claude-skills/", "plugins/")

# Each path below forces a full-suite run rather than a domain selection:
# - claude/.claude/tests/helpers.py's own DOMAIN_RULES match alone would
#   under-select it to claude/.claude/tests plus TICKET_REFERENCE_DISCIPLINE_TEST_PATH.
#   This entry is what forces every importing domain's tests to run instead.
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
# auxiliary sibling of a SKILL.md, not just SKILL.md itself. The filenames
# live in _skill_auxiliary_files.py, shared with that test.
def _is_skill_auxiliary_md_change(path: str) -> bool:
    return _is_under(path, SKILLS_DIR) and Path(path).name in SKILL_AUXILIARY_MD_NAMES


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


# See TICKET_REFERENCE_DISCIPLINE_TEST_PATH's own comment above for what
# that test scans. This predicate is deliberately .py-only. That test's .sh
# coverage is achieved today only incidentally, through the existing
# hooks/scripts shell-script domain rules.
# Selects TICKET_REFERENCE_DISCIPLINE_TEST_PATH directly rather than the
# HOOKS_TESTS_DIR domain it lives in.
# Also selects CLAUDE_TESTS_DIR: TestConftestModuleNamesAreUnique in
# test_pytest_collection_config.py resolves every tracked conftest.py
# repo-wide via git ls-files, with no root scoping, so a .py file anywhere
# under this predicate's three roots can be a new conftest.py that needs
# that pairwise-uniqueness check to actually run.
def _is_py_source_under_claude_or_plugins(path: str) -> bool:
    return (
        path.endswith(".py")
        and (
            _is_under(path, CLAUDE_TOP_LEVEL_DIR)
            or _is_under(path, CLAUDE_SKILLS_TOP_LEVEL_DIR)
            or _is_under(path, PLUGINS_DIR)
        )
    )


# Matches exactly the corpus SELECT_TESTS_TEST_PATH's own comment describes:
# a test_*.py file directly inside a tests/ directory under claude/ or
# plugins/. A stricter subset of _is_py_source_under_claude_or_plugins,
# since only a test file can introduce a new module-level repo-path
# constant for that scanner to miss. Excludes any path under a worktrees/
# directory, since no test corpus root ever resolves into one.
def _is_test_source_change(path: str) -> bool:
    return (
        _is_py_source_under_claude_or_plugins(path)
        and WORKTREES_DIR_NAME not in Path(path).parts
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
    (lambda p: _is_under(p, PLANS_DIR), (PLAN_LEDGER_CITATIONS_IN_DIFF_TEST_PATH,)),
    (lambda p: p == CHANGELOG_MD, ()),
    (lambda p: _is_under(p, CLAUDE_TESTS_DIR), (CLAUDE_TESTS_DIR,)),
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
# SKILL_AUXILIARY_FILES_MODULE: SKILLS_TESTS_DIR's test_skills.py imports the
# module, and that import is invisible to path-constant scanning.
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
# CODE_REVIEW_SKILL_MD (second row): test_findings_path_suffix.py
# (SCRIPTS_TESTS_DIR) also reads code-review/SKILL.md by path, for the
# findings_path template text. Stays a standalone row rather than joining
# SKILL_FILES_READ_BY_HOOK_TESTS -- that set's shared (HOOKS_TESTS_DIR,)
# target doesn't cover this file's SCRIPTS_TESTS_DIR need. Its enforcing
# equality test (test_skill_files_read_by_hook_tests_equals_known_reads_under_hooks_tests_dir)
# is also scoped to HOOKS_TESTS_DIR readers only, so adding this row there
# would break that test's invariant too.
# READY_FOR_REVIEW_SKILL_MD (second row): test_findings_path_suffix.py
# (SCRIPTS_TESTS_DIR) also reads ready-for-review/SKILL.md by path, for its
# own findings_path template text. Same standalone-row rationale as
# CODE_REVIEW_SKILL_MD's second row above.
# HANDOFF_SKILL_MD: test_check_handoff.py (SCRIPTS_TESTS_DIR) and
# test_restore_authorization_boundary_on_compact.py (HOOKS_TESTS_DIR) each
# read this exact file by path.
# _is_hooks_dir_shell_script_change: test_no_bash4_constructs.py and
# test_default_branch_resolution_is_shared.py (both SCRIPTS_TESTS_DIR)
# recursively glob claude/.claude/ for *.sh files, picking up
# claude/.claude/hooks/ in addition to their own SCRIPTS_DIR.
# _is_plugin_hooks_change, _is_plugin_skills_change, and
# _is_plugin_agents_change match every plugin under plugins/, not only
# lovable-cloud -- the test globs cited above are plugin-generic, so the
# predicate has to be too.
# AGENTS_DIR: test_agent_roster.py (HOOKS_TESTS_DIR) and test_skills.py
# (SKILLS_TESTS_DIR) both read claude/.claude/agents/*.md by path.
# RULES_DIR: test_rules_frontmatter.py (SKILLS_TESTS_DIR) and
# test_claude_md_excludes.py (HOOKS_TESTS_DIR) each rglob
# claude/.claude/rules/*.md by path.
# GITHUB_ACTIONS_WORKFLOWS_RULE_MD: test_ci_path_filter.py (HOOKS_TESTS_DIR)
# reads this exact file. Subsumed by the RULES_DIR row above. Kept anyway
# because its declaration is narrower and independent of that row.
# TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD: test_transcript_analysis_architecture_doc.py
# (SCRIPTS_TESTS_DIR) reads this exact file by path, in addition to the
# DOCS_DIR blanket below.
# DOCS_DIR, README_MD, INSTALL_SH, and CLAUDE_SETTINGS_JSON: see each
# constant's own comment above for its citation.
# GLOBAL_CLAUDE_MD, ROOT_CLAUDE_MD, ROOT_RULES_DIR, ROOT_SKILLS_DIR, and
# ROOT_SETTINGS_JSON: see each constant's own comment above for its citation.
# STATUSLINE_COMMAND_SH: see its own comment above for citation.
# CONFIG_KEYS_PSV: see its own comment above for citation.
# LEDGER_CITATION_CHECKER_SCRIPT: see its own comment above for citation.
# _is_py_source_under_claude_or_plugins: see its own comment above for
# citation. Selects TICKET_REFERENCE_DISCIPLINE_TEST_PATH and
# CLAUDE_TESTS_DIR directly.
# _is_test_source_change: see SELECT_TESTS_TEST_PATH's own comment above for
# citation. A strict subset of _is_py_source_under_claude_or_plugins, since
# only a test file under one of the five selectable test directories can
# introduce a constant TestCrossDomainReadCompleteness's own scan would need
# to see.
CROSS_DOMAIN_EXCEPTIONS: tuple[tuple[Callable[[str], bool], tuple[str, ...]], ...] = (
    (_is_hooks_or_skills_change, (TRANSCRIPT_ANALYSIS_TEST_GLOB,)),
    (_is_skill_management_or_evals_change, (SKILLS_TESTS_DIR,)),
    (lambda p: p == SKILL_AUXILIARY_FILES_MODULE, (SKILLS_TESTS_DIR,)),
    (lambda p: p == LOVABLE_CLOUD_PLUGIN_MANIFEST, (SKILLS_TESTS_DIR,)),
    (_is_plugin_hooks_change, (HOOKS_TESTS_DIR,)),
    (_is_plugin_skills_change, (SKILLS_TESTS_DIR,)),
    (_is_plugin_agents_change, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (_is_lovable_cloud_shell_script_change, (HOOKS_TESTS_DIR,)),
    (_is_scripts_dir_shell_script_change, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p in SKILL_FILES_READ_BY_HOOK_TESTS, (HOOKS_TESTS_DIR,)),
    (lambda p: p == CODE_REVIEW_SKILL_MD, (SCRIPTS_TESTS_DIR,)),
    (lambda p: p == READY_FOR_REVIEW_SKILL_MD, (SCRIPTS_TESTS_DIR,)),
    (lambda p: p == HANDOFF_SKILL_MD, (SCRIPTS_TESTS_DIR, HOOKS_TESTS_DIR)),
    (_is_hooks_dir_shell_script_change, (SCRIPTS_TESTS_DIR,)),
    (lambda p: _is_under(p, AGENTS_DIR), (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: _is_under(p, RULES_DIR), (SKILLS_TESTS_DIR, HOOKS_TESTS_DIR)),
    (lambda p: p == GITHUB_ACTIONS_WORKFLOWS_RULE_MD, (HOOKS_TESTS_DIR,)),
    (lambda p: p == TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD, (SCRIPTS_TESTS_DIR,)),
    (lambda p: _is_under(p, DOCS_DIR), (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == README_MD, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == INSTALL_SH, (HOOKS_TESTS_DIR,)),
    (lambda p: p == CLAUDE_SETTINGS_JSON, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR, SCRIPTS_TESTS_DIR)),
    (lambda p: p == GLOBAL_CLAUDE_MD, (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == ROOT_CLAUDE_MD, (HOOKS_TESTS_DIR,)),
    (lambda p: _is_under(p, ROOT_RULES_DIR), (SKILLS_TESTS_DIR, HOOKS_TESTS_DIR)),
    (lambda p: _is_under(p, ROOT_SKILLS_DIR), (SKILLS_TESTS_DIR,)),
    (lambda p: p == ROOT_SETTINGS_JSON, (HOOKS_TESTS_DIR,)),
    (lambda p: p == STATUSLINE_COMMAND_SH, (HOOKS_TESTS_DIR, SCRIPTS_TESTS_DIR, CLAUDE_TESTS_DIR)),
    (lambda p: p == CONFIG_KEYS_PSV, (HOOKS_TESTS_DIR, SCRIPTS_TESTS_DIR, SKILLS_TESTS_DIR)),
    (lambda p: p == LEDGER_CITATION_CHECKER_SCRIPT, (SKILLS_TESTS_DIR,)),
    (_is_py_source_under_claude_or_plugins, (TICKET_REFERENCE_DISCIPLINE_TEST_PATH, CLAUDE_TESTS_DIR)),
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


class MergeBaseUnresolved(GitDiffUnavailable):
    """The merge-base lookup itself failed, so no diff was attempted.
    Distinguishes that first call's failure from the later diff and
    ls-files failures, which all raise plain GitDiffUnavailable.
    isinstance-checked by tests/test_plan_ledger_citations_in_diff.py to pick
    CI-checkout-depth advice over generic git-failure advice."""


def _run_git(args: list[str], *, cwd: Path, run, decode: bool = True) -> str | bytes | None:
    """Return git's stdout, or None if git is missing, times out, or exits
    nonzero. decode=False returns the raw bytes, with no newline translation
    or strict decoding."""
    # The default text mode decodes strictly, so rev-parse and merge-base output
    # with a non-UTF-8 byte raises. Contributors do not control those bytes,
    # so this is an accepted residual.
    try:
        result = run(
            ["git", *args], cwd=cwd, capture_output=True, text=decode,
            timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def printable_path(path: str) -> str:
    """Render a changed path inertly for output: ASCII only, with control
    characters, non-ASCII characters, and lone surrogates shown as escapes.
    A changed path is contributor-controlled, so it must never reach a
    terminal or a log line raw."""
    return ascii(path)[1:-1]


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

    A run= double returns str stdout for the merge-base call and bytes stdout
    for the three -z calls.
    """
    merge_base_output = _run_git(["merge-base", "HEAD", "origin/main"], cwd=repo_root, run=run)
    if merge_base_output is None or not merge_base_output.strip():
        raise MergeBaseUnresolved("could not resolve merge-base against origin/main")
    merge_base = merge_base_output.strip()

    committed = _run_git(
        ["diff", "--name-only", "-z", "--no-renames", f"{merge_base}...HEAD"],
        cwd=repo_root,
        run=run,
        decode=False,
    )
    if committed is None:
        raise GitDiffUnavailable("git diff against the merge-base failed")
    dirty = _run_git(
        ["diff", "--name-only", "-z", "--no-renames", "HEAD"], cwd=repo_root, run=run, decode=False
    )
    if dirty is None:
        raise GitDiffUnavailable("git diff against HEAD failed")
    untracked = _run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_root, run=run, decode=False
    )
    if untracked is None:
        raise GitDiffUnavailable("git ls-files for untracked files failed")

    # -z emits each path verbatim and NUL-terminated, so core.quotePath and
    # filenames containing newlines cannot alter a path. --no-renames lists
    # both sides of a rename whatever diff.renames is configured to.
    changed = {
        os.fsdecode(record)
        for output in (committed, dirty, untracked)
        for record in output.split(b"\0")
        if record
    }
    return sorted(changed)


# --- pytest invocation ----------------------------------------------------

XDIST_WORKER_ENV_VAR = "PYTEST_XDIST_AUTO_NUM_WORKERS"

# Below two workers, xdist's own per-worker spawn/IPC overhead outweighs
# any parallelism gained.
# A one-line change here if field data contradicts it.
_MIN_LOAD_AWARE_WORKERS = 2

# Discriminates pytest_subprocess_env's three outcomes, so callers don't have
# to infer which one occurred by re-inspecting the returned env dict.
WORKER_SIZING_ALREADY_SET = "already-set"
WORKER_SIZING_COMPUTED = "computed"
WORKER_SIZING_UNAVAILABLE = "unavailable"


def _expand_target(target: str, *, repo_root: Path) -> list[str]:
    """A plain directory/file target passes through unchanged.

    A glob-pattern target (e.g. TRANSCRIPT_ANALYSIS_TEST_GLOB) expands to
    its concrete repo-root-relative matches, sorted for deterministic argv.
    """
    if "*" not in target:
        return [target]
    return sorted(str(match.relative_to(repo_root)) for match in repo_root.glob(target))


def _covers(container: str, candidate: str) -> bool:
    """True when container's own pytest walk already collects candidate --
    i.e. candidate sits strictly inside container. Reuses _is_under for the
    prefix test rather than restating it.

    FULL_SUITE_TARGETS entries end in "/", so container's trailing slash is
    stripped first to avoid a never-matching `claude/.claude//` prefix in
    _is_under's `directory + "/"` concatenation."""
    normalized = container.rstrip("/")
    return candidate != normalized and _is_under(candidate, normalized)


def resolve_target_paths(target_paths: Iterable[str], *, repo_root: Path) -> list[str]:
    """Turn a selection's target_paths into the concrete paths pytest
    receives.

    Pytest collects nothing from a directory argument when another
    argument names a path inside it. To avoid that, every target is
    expanded through _expand_target, then passed through two distinct
    filters applied in order. First, exact duplicates are dropped, keeping
    the first occurrence. Second, any path another entry in the expanded
    list _covers is dropped. The duplicate filter can't fold into the
    containment filter because _covers excludes equality by definition, so
    containment alone never removes a repeat. Each candidate in the
    containment filter is checked against the whole expanded list rather
    than a progressively-shrinking one, so a multi-level chain (e.g. A,
    A/B, A/B/c.py) collapses to its outermost container in one pass.

    Output is sorted, matching select_pytest_targets' own
    tuple(sorted(targets)) contract and the sortedness glob expansion
    already carries. That's incidental for today's callers -- the
    filtering above doesn't depend on argv order at all."""
    expanded: list[str] = []
    for target in target_paths:
        expanded.extend(_expand_target(target, repo_root=repo_root))

    deduped: list[str] = []
    seen: set[str] = set()
    for path in expanded:
        if path not in seen:
            deduped.append(path)
            seen.add(path)

    # Order-independent: pytest's Session.collect() runs the matching walk
    # for every initial argument, mutating the shared collection cache,
    # before genitems() runs on any of them, so the enclosing directory
    # ends up cached whichever argument comes first.
    survivors = [
        path for path in deduped
        if not any(_covers(other, path) for other in deduped if other != path)
    ]
    return sorted(survivors)


def build_pytest_argv(
    target_paths: Iterable[str], passthrough_args: Iterable[str], *, repo_root: Path,
) -> list[str]:
    """Only target_paths are containment-resolved; passthrough_args reach
    pytest verbatim, so a path given on the command line can still shadow a
    resolved target."""
    return [*resolve_target_paths(target_paths, repo_root=repo_root), *list(passthrough_args)]


def _resolve_pytest_executable() -> str:
    """Resolves pytest from the sys.executable sibling (e.g. .venv/bin/pytest)
    rather than trusting PATH, since this script is normally invoked with a
    venv-prefixed interpreter and that sibling is the environment actually
    running it. Falls back to a bare "pytest" lookup on PATH when no such
    sibling exists."""
    sibling = Path(sys.executable).with_name("pytest")
    return str(sibling) if sibling.exists() else "pytest"


def _cpu_budget() -> int:
    # Mirrors pytest-xdist's own `-n auto` count on the non-psutil path, so
    # an idle machine still gets exactly `-n auto`'s own worker count.
    try:
        from os import sched_getaffinity
    except ImportError:
        n = os.cpu_count()
    else:
        n = len(sched_getaffinity(0))
    return n if n else 1


def compute_worker_count(*, cpu_budget: int, load_one_minute: float) -> int:
    return max(
        min(_MIN_LOAD_AWARE_WORKERS, cpu_budget),
        min(cpu_budget, round(cpu_budget - load_one_minute)),
    )


class WorkerSizingResult(NamedTuple):
    env: dict[str, str]
    outcome: str
    worker_count: int | None = None
    load_average: float | None = None


def pytest_subprocess_env(base_env: dict[str, str], *, getloadavg) -> WorkerSizingResult:
    """Injects XDIST_WORKER_ENV_VAR into base_env, sized from the current
    1-minute load average, and reports which outcome occurred.

    Leaves base_env's XDIST_WORKER_ENV_VAR untouched, reporting the
    matching outcome, when:
    - the caller already set it to a non-empty value (WORKER_SIZING_ALREADY_SET)
    - the load average can't be read (WORKER_SIZING_UNAVAILABLE)

    getloadavg has no default, since Python binds a default once at
    def-time -- a default of os.getloadavg would capture the pre-monkeypatch
    function and defeat tests that patch os.getloadavg after import.
    """
    if base_env.get(XDIST_WORKER_ENV_VAR):
        return WorkerSizingResult(dict(base_env), WORKER_SIZING_ALREADY_SET)
    try:
        load_one_minute = getloadavg()[0]
    except OSError:
        return WorkerSizingResult(dict(base_env), WORKER_SIZING_UNAVAILABLE)
    workers = compute_worker_count(cpu_budget=_cpu_budget(), load_one_minute=load_one_minute)
    env = {**base_env, XDIST_WORKER_ENV_VAR: str(workers)}
    return WorkerSizingResult(env, WORKER_SIZING_COMPUTED, worker_count=workers, load_average=load_one_minute)


def run_pytest(
    pytest_argv: list[str], *, cwd: Path, run=subprocess.run, env: dict[str, str] | None = None,
) -> int:
    executable = _resolve_pytest_executable()
    result = run([executable, *pytest_argv], cwd=cwd, check=False, env=env)
    return result.returncode


# --- Fallback-reason instrumentation -------------------------------------

SELECTION_LOG_FILENAME = ".test-selection-log.jsonl"

# Bounds one JSON line to well under one write() syscall's atomic-write size
# limit. An unbounded triggering_paths list could otherwise make two
# concurrent appends interleave instead of landing as separate atomic writes.
_TRIGGERING_PATHS_LOG_CAP = 20


def record_selection(
    selection: SelectionResult,
    resolved_targets: list[str],
    size_result: WorkerSizingResult | None = None,
) -> None:
    """Appends one JSON line describing this invocation's selection outcome
    to <config-dir>/.test-selection-log.jsonl, gated by the off-by-default
    test_selection_tracking config key.

    worker_count and load_average are added to the record only when
    size_result's outcome is WORKER_SIZING_COMPUTED.

    Best-effort: swallows a log-append OSError, a config_dir() resolution
    ValueError, or a config-keys.psv truncation error from config_enabled()
    with one stderr warning, since a full disk, an unresolvable config dir,
    or a torn schema row must not turn into a failed test run.
    """
    try:
        if not config_enabled("test_selection_tracking"):
            return
        triggering_paths = list(selection.triggering_paths)
        record = {
            "logged_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": selection.reason,
            "is_full_suite": selection.is_full_suite,
            "triggering_paths": triggering_paths[:_TRIGGERING_PATHS_LOG_CAP],
            "target_count": len(resolved_targets),
        }
        if len(triggering_paths) > _TRIGGERING_PATHS_LOG_CAP:
            record["triggering_paths_truncated"] = True
        if size_result is not None and size_result.outcome == WORKER_SIZING_COMPUTED:
            record["worker_count"] = size_result.worker_count
            record["load_average"] = size_result.load_average
        log_path = config_dir() / SELECTION_LOG_FILENAME
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except (OSError, ValueError, ConfigSchemaEmptyError, ConfigSchemaRowTruncatedError) as exc:
        print(f"select-tests: could not record test selection to the log ({exc})", file=sys.stderr)


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

    resolved_targets = resolve_target_paths(selection.target_paths, repo_root=repo_root)

    # No sizing decision is made for the nothing-to-run early return below:
    # pytest never runs, so a getloadavg() syscall would be wasted.
    will_run_pytest = selection.is_full_suite or bool(selection.target_paths)
    size_result = (
        pytest_subprocess_env(dict(os.environ), getloadavg=os.getloadavg) if will_run_pytest else None
    )
    record_selection(selection, resolved_targets, size_result)

    if selection.is_full_suite:
        if selection.triggering_paths:
            # Uncapped, unlike the log's path cap: a one-shot diagnostic at PR scale.
            paths = ", ".join(printable_path(path) for path in selection.triggering_paths)
            print(f"select-tests: running the full suite ({selection.reason}: {paths})", file=sys.stderr)
        else:
            print(f"select-tests: running the full suite ({selection.reason})", file=sys.stderr)
    elif not selection.target_paths:
        print(f"select-tests: nothing to run ({selection.reason})", file=sys.stderr)
        return 0
    else:
        rendered_targets = ", ".join(printable_path(target) for target in resolved_targets)
        print(f"select-tests: running {rendered_targets}", file=sys.stderr)

    # size_result is never None past this point: either branch above that
    # returns early corresponds exactly to will_run_pytest being False.
    if size_result.outcome == WORKER_SIZING_ALREADY_SET:
        print(
            f"select-tests: {XDIST_WORKER_ENV_VAR} already set to "
            f"{size_result.env[XDIST_WORKER_ENV_VAR]}; leaving it alone",
            file=sys.stderr,
        )
    elif size_result.outcome == WORKER_SIZING_COMPUTED:
        print(
            f"select-tests: {XDIST_WORKER_ENV_VAR}={size_result.worker_count} "
            f"(1-minute load average {size_result.load_average})",
            file=sys.stderr,
        )
    else:
        print(
            f"select-tests: could not read the 1-minute load average; "
            f"leaving {XDIST_WORKER_ENV_VAR} unset",
            file=sys.stderr,
        )

    # build_pytest_argv resolves resolved_targets again internally; safe
    # because resolve_target_paths is idempotent on its own output, pinned
    # by test_idempotent_on_its_own_output.
    pytest_argv = build_pytest_argv(resolved_targets, passthrough_args, repo_root=repo_root)
    return run_pytest(pytest_argv, cwd=repo_root, env=size_result.env)


if __name__ == "__main__":
    sys.exit(main())
