"""Guards the `claudeMdExcludes` entry in the repo-root `.claude/settings.json`
that suppresses the nested-discovery duplicate load of `claude/.claude/CLAUDE.md`
(docs/design-decisions.md §39). The exclusion fails silently: nothing in a
running session reports that the glob stopped matching. The only observable
symptom is a larger context that nobody notices.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
STOW_SOURCE_SETTINGS_PATH = REPO_ROOT / "claude" / ".claude" / "settings.json"
EXCLUDED_CLAUDE_MD_PATTERN = "**/claude/.claude/CLAUDE.md"


class TestClaudeMdExcludes:
    def test_claude_md_excludes_contains_exactly_one_entry(self):
        settings = json.loads(SETTINGS_PATH.read_text())
        assert settings.get("claudeMdExcludes") == [EXCLUDED_CLAUDE_MD_PATTERN], (
            "claudeMdExcludes should hold exactly this one pattern; an extra "
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

    def test_excluded_pattern_does_not_match_a_claude_rules_file(self):
        # fnmatch approximates the real matcher, not proof of it — see docs/design-decisions.md §39 for the authoritative check.
        rules_file = REPO_ROOT / "claude" / ".claude" / "rules" / "dockerfile-conventions.md"
        assert rules_file.is_file()
        assert not fnmatch.fnmatch(str(rules_file), EXCLUDED_CLAUDE_MD_PATTERN)

    def test_stow_source_settings_has_no_claude_md_excludes_entry(self):
        """docs/design-decisions.md §39: an entry in the stow-source settings.json
        would apply to every project on every consumer's machine, not just this repo."""
        stow_settings = json.loads(STOW_SOURCE_SETTINGS_PATH.read_text())
        assert "claudeMdExcludes" not in stow_settings
