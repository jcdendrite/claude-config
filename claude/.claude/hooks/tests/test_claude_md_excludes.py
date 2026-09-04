"""Guards the `claudeMdExcludes` entries in the repo-root `.claude/settings.json`.
The first suppresses the nested-discovery duplicate load of
`claude/.claude/CLAUDE.md` (docs/design-decisions.md §39). The second
suppresses the nested-discovery duplicate load of each stow-source rule file
under `claude/.claude/rules/` for a session anchored in a linked worktree of
this repo, where the user-scope symlink target and the nested worktree path
are different absolute paths (docs/design-decisions.md §47). Both exclusions
fail silently: nothing in a running session reports that a glob stopped
matching. The only observable symptom is a larger context that nobody
notices. The second pattern's positive match behavior — including a
worktree slug containing `/` — has no automated regression coverage in this
suite. No `fnmatch`-based test could close that gap either: `fnmatch.translate`
does not distinguish `*` from `**`, so a positive assertion could not tell
a correct `**` middle segment from one narrowed to a single `*` that would
silently miss a multi-segment slug. Only a manual check, reading a file in
a fresh interactive session anchored in a worktree and inspecting that
session's on-disk transcript for `nested_memory` attachment records, would
catch a future edit that silently narrows it. A second, separate gap: the
pattern's `/**` tail has only ever been observed matching a direct child, not
a rule file in a subdirectory — no rule file occupies one today. See
docs/design-decisions.md §47.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
STOW_SOURCE_SETTINGS_PATH = REPO_ROOT / "claude" / ".claude" / "settings.json"
STOW_SOURCE_RULES_DIR = REPO_ROOT / "claude" / ".claude" / "rules"
PROJECT_SCOPE_RULES_DIR = REPO_ROOT / ".claude" / "rules"
EXCLUDED_CLAUDE_MD_PATTERN = "**/claude/.claude/CLAUDE.md"
EXCLUDED_WORKTREE_RULES_PATTERN = "**/.claude/worktrees/**/claude/.claude/rules/**"
EXPECTED_CLAUDE_MD_EXCLUDES = [
    EXCLUDED_CLAUDE_MD_PATTERN,
    EXCLUDED_WORKTREE_RULES_PATTERN,
]

# Globs the stow-source rules directory rather than naming a rule file, so a
# rule-file rename cannot break this module (docs/design-decisions.md §47).
_STOW_SOURCE_RULE_FILES = sorted(STOW_SOURCE_RULES_DIR.rglob("*.md"))
_STOW_SOURCE_RULE_IDS = [str(f.relative_to(REPO_ROOT)) for f in _STOW_SOURCE_RULE_FILES]

# Same rationale as _STOW_SOURCE_RULE_FILES, applied to the sibling
# project-scope directory: globs rather than names a rule file, so a
# rule-file rename cannot break this module. This does not close
# select-tests.py's routing gap for this file (CROSS_DOMAIN_EXCEPTIONS
# routes a change under this directory to SKILLS_TESTS_DIR only, never
# HOOKS_TESTS_DIR, regardless of how this module discovers the files) —
# an agent's scoped select-tests.py run won't locally catch a regression
# here; only full CI will. Same accepted, pre-existing cost as the sibling
# _STOW_SOURCE_RULE_FILES glob.
_PROJECT_SCOPE_RULE_FILES = sorted(PROJECT_SCOPE_RULES_DIR.rglob("*.md"))
_PROJECT_SCOPE_RULE_IDS = [str(f.relative_to(REPO_ROOT)) for f in _PROJECT_SCOPE_RULE_FILES]


class TestClaudeMdExcludes:
    def test_claude_md_excludes_matches_expected_entries(self):
        settings = json.loads(SETTINGS_PATH.read_text())
        # Order-independent (sorted(), not set()): nothing in the vendor docs
        # suggests claudeMdExcludes array order is semantically meaningful,
        # but an accidental duplicate entry should still fail this assertion.
        assert sorted(settings.get("claudeMdExcludes", [])) == sorted(EXPECTED_CLAUDE_MD_EXCLUDES), (
            "claudeMdExcludes should hold exactly these two patterns; an extra "
            "entry would silently accumulate unrelated excludes"
        )

    def test_excluded_pattern_matches_stow_source_but_not_repo_root_claude_md(self):
        # claudeMdExcludes patterns match against absolute paths (see
        # docs/design-decisions.md §39's Sources and the plan's assumption ledger).
        stow_source_claude_md = REPO_ROOT / "claude" / ".claude" / "CLAUDE.md"
        assert stow_source_claude_md.is_file()
        # fnmatch approximates the real matcher, not proof of it — see docs/design-decisions.md §39 for the authoritative check.
        assert fnmatch.fnmatch(str(stow_source_claude_md), EXCLUDED_CLAUDE_MD_PATTERN)

        root_claude_md = REPO_ROOT / "CLAUDE.md"
        assert root_claude_md.is_file()
        # fnmatch approximates the real matcher, not proof of it — see docs/design-decisions.md §39 for the authoritative check.
        assert not fnmatch.fnmatch(str(root_claude_md), EXCLUDED_CLAUDE_MD_PATTERN)

    def test_rule_files_exist(self):
        """Sanity check both discovery globs aren't silently empty."""
        assert _STOW_SOURCE_RULE_FILES, (
            f"Expected at least one rule file under {STOW_SOURCE_RULES_DIR}"
        )
        assert _PROJECT_SCOPE_RULE_FILES, (
            f"Expected at least one rule file under {PROJECT_SCOPE_RULES_DIR}"
        )

    @pytest.mark.parametrize(
        "rule_file", _STOW_SOURCE_RULE_FILES, ids=_STOW_SOURCE_RULE_IDS
    )
    def test_claude_md_pattern_does_not_match_a_claude_rules_file(self, rule_file):
        # fnmatch approximates the real matcher, not proof of it — see docs/design-decisions.md §39 for the authoritative check.
        assert not fnmatch.fnmatch(str(rule_file), EXCLUDED_CLAUDE_MD_PATTERN)

    @pytest.mark.parametrize(
        "rule_file", _STOW_SOURCE_RULE_FILES, ids=_STOW_SOURCE_RULE_IDS
    )
    def test_worktree_rules_pattern_does_not_match_a_main_checkout_rules_file(
        self, rule_file
    ):
        # Negative-only: every `**` in EXCLUDED_WORKTREE_RULES_PATTERN compiles
        # to an unrestricted `.*` under fnmatch.translate, so fnmatch is
        # strictly more permissive than Claude Code's real matcher here, and a
        # non-match under fnmatch implies a non-match under the real one. That
        # asymmetry holds only for a pattern built from `**` tokens and
        # literal segments, as this one is — it does not generalize to a
        # pattern using `?` or `[...]`. See docs/design-decisions.md §47 for
        # the authoritative empirical check of the positive direction.
        #
        # Built against a synthetic main-checkout root, not the real REPO_ROOT:
        # this suite is itself conventionally run from inside a worktree, so
        # REPO_ROOT can carry the very `.claude/worktrees/` segment being
        # tested for, which would make the assertion's outcome depend on
        # where the test happens to be anchored.
        main_checkout_relative_path = rule_file.relative_to(REPO_ROOT)
        main_checkout_rule_file = Path("/main-checkout") / main_checkout_relative_path
        assert not fnmatch.fnmatch(
            str(main_checkout_rule_file), EXCLUDED_WORKTREE_RULES_PATTERN
        )

    @pytest.mark.parametrize(
        "rule_file", _PROJECT_SCOPE_RULE_FILES, ids=_PROJECT_SCOPE_RULE_IDS
    )
    def test_worktree_rules_pattern_does_not_match_a_project_scope_rules_file(
        self, rule_file
    ):
        # See the comment on the sibling negative test above for why a
        # negative-only fnmatch assertion is sound for this pattern. This one
        # guards against over-match onto the worktree's own repo-root
        # project-scope `.claude/rules/` (no `claude/` segment before
        # `.claude/rules/`), which is correctly project-scoped and must keep
        # loading exactly once even inside a worktree. REPO_ROOT is already a
        # worktree root when this suite runs its own documented way (see the
        # sibling test above), so no extra `.claude/worktrees/<slug>` prefix
        # is added here; that would model a worktree nested inside another
        # worktree instead.
        assert not fnmatch.fnmatch(str(rule_file), EXCLUDED_WORKTREE_RULES_PATTERN)

    def test_stow_source_settings_has_no_claude_md_excludes_entry(self):
        """docs/design-decisions.md §39: an entry in the stow-source settings.json
        would apply to every project on every consumer's machine, not just this repo."""
        stow_settings = json.loads(STOW_SOURCE_SETTINGS_PATH.read_text())
        assert "claudeMdExcludes" not in stow_settings
