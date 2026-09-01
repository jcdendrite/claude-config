"""Guards the `claudeMdExcludes` entry in the repo-root `.claude/settings.json`
that suppresses the nested-discovery duplicate load of `claude/.claude/CLAUDE.md`
(docs/design-decisions.md §39). The exclusion fails silently: nothing in a
running session reports that the glob stopped matching. The only observable
symptom is a larger context that nobody notices.
"""
from __future__ import annotations

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

    def test_claude_md_excludes_contains_the_exact_pattern(self):
        settings = json.loads(SETTINGS_PATH.read_text())
        assert EXCLUDED_CLAUDE_MD_PATTERN in settings.get("claudeMdExcludes", [])

    def test_excluded_pattern_matches_stow_source_but_not_repo_root_claude_md(self):
        # claudeMdExcludes patterns match against absolute paths (see
        # docs/design-decisions.md §39's Sources and the plan's assumption ledger).
        stow_source_claude_md = REPO_ROOT / "claude" / ".claude" / "CLAUDE.md"
        assert stow_source_claude_md.is_file()
        assert stow_source_claude_md.full_match(EXCLUDED_CLAUDE_MD_PATTERN)

        root_claude_md = REPO_ROOT / "CLAUDE.md"
        assert root_claude_md.is_file()
        assert not root_claude_md.full_match(EXCLUDED_CLAUDE_MD_PATTERN)

    def test_stow_source_settings_has_no_claude_md_excludes_entry(self):
        """docs/design-decisions.md §39: an entry in the stow-source settings.json
        would apply to every project on every consumer's machine, not just this repo."""
        stow_settings = json.loads(STOW_SOURCE_SETTINGS_PATH.read_text())
        assert "claudeMdExcludes" not in stow_settings
