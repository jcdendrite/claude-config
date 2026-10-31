"""Unit tests for the skill-management plugin's _lib_conflict_marker_deny_paths and the parity of the shared gate-base closure
between the stowed _lib.sh and the plugin copy."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ._lib_sh_helpers import (
    _LIB_SH,
    _SKILL_MANAGEMENT_PLUGIN_LIB,
)

# --- _lib_conflict_marker_deny_paths -----------------------------------------
#
# Plugin-only helper (no stowed-copy counterpart -- marker.sh, the write
# side, has no conflict-marker scan to share it with), so these source
# _SKILL_MANAGEMENT_PLUGIN_LIB directly rather than _LIB_SH.
# Pure set-difference: exact-line matching semantics only. The scan's
# git-dependent properties (index-vs-worktree read, diff-presentation-config
# immunity, the marker regex's own boundary behavior) stay pinned at the
# subprocess-fixture layer in test_require_skill_review.py's
# TestSkillReviewGateConflictMarkerHardDeny.


def _conflict_marker_deny_paths(
    candidates: str, marker_free: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", "-c",
            f'. {_SKILL_MANAGEMENT_PLUGIN_LIB}; _lib_conflict_marker_deny_paths "$1" "$2"',
            "bash", candidates, marker_free,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )


class TestConflictMarkerDenyPaths:
    def test_exact_line_match_is_excluded(self) -> None:
        result = _conflict_marker_deny_paths("skills/x/SKILL.md", "skills/x/SKILL.md")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_suffix_of_a_marker_free_entry_is_not_treated_as_marker_free(self) -> None:
        """A clean `plugins/p/skills/x/SKILL.md` ends with the conflicted
        `skills/x/SKILL.md` -- exact-line match must not let that suffix
        relationship clear the shorter path."""
        conflicted = "skills/x/SKILL.md"
        clean_sibling = "plugins/p/skills/x/SKILL.md"
        result = _conflict_marker_deny_paths(conflicted, clean_sibling)
        assert result.returncode == 0
        assert result.stdout == conflicted

    def test_prefix_of_a_marker_free_entry_is_not_treated_as_marker_free(self) -> None:
        """The reverse relationship: a clean `skills/x/SKILL.md.bak` starts
        with the conflicted `skills/x/SKILL.md` -- still not a match."""
        conflicted = "skills/x/SKILL.md"
        clean_sibling = "skills/x/SKILL.md.bak"
        result = _conflict_marker_deny_paths(conflicted, clean_sibling)
        assert result.returncode == 0
        assert result.stdout == conflicted

    def test_empty_candidates_produces_no_deny_paths(self) -> None:
        result = _conflict_marker_deny_paths("", "skills/x/SKILL.md")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_empty_marker_free_returns_candidates_unchanged(self) -> None:
        candidates = "skills/a/SKILL.md\nskills/b/SKILL.md"
        result = _conflict_marker_deny_paths(candidates, "")
        assert result.returncode == 0
        assert result.stdout == candidates

    def test_mixed_candidates_clear_only_the_marker_free_entries(self) -> None:
        """Order-preserving partial removal: the marker-free candidate drops
        out, the other two stay in their original order."""
        candidates = "skills/a/SKILL.md\nskills/b/SKILL.md\nskills/c/SKILL.md"
        marker_free = "skills/b/SKILL.md\nskills/z/SKILL.md"
        result = _conflict_marker_deny_paths(candidates, marker_free)
        assert result.returncode == 0
        assert result.stdout == "skills/a/SKILL.md\nskills/c/SKILL.md"

    def test_all_candidates_marker_free_produces_no_deny_paths(self) -> None:
        candidates = "skills/a/SKILL.md\nskills/b/SKILL.md"
        result = _conflict_marker_deny_paths(candidates, candidates)
        assert result.returncode == 0
        assert result.stdout == ""


# --- Cross-copy parity of the shared gate-base closure -----------------------

_SHARED_CLOSURE_FUNCTIONS = [
    "_lib_capped",
    "_lib_capped_for",
    "_lib_default_branch_from_origin_head",
    "_lib_default_branch_or_guess",
    "_lib_git_inprogress_state",
    "_lib_gate_diff_base",
    "_lib_skill_review_diff_base",
    "_lib_staged_diff_hash",
    "_lib_marker_value_present",
]


def _declared_function_body(lib_path: Path, function_name: str) -> str:
    result = subprocess.run(
        ["bash", "-c", '. "$1"; declare -f "$2"', "bash", str(lib_path), function_name],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and result.stdout, (
        f"{function_name} is not defined by {lib_path}: {result.stderr}"
    )
    return result.stdout


@pytest.mark.parametrize("function_name", _SHARED_CLOSURE_FUNCTIONS)
def test_shared_closure_function_is_identical_across_stowed_and_plugin_lib(
    function_name: str,
) -> None:
    """The plugin cannot source the stowed _lib.sh, so it carries copies of the
    gate-base closure. The marker recipe and the anchor check must not drift
    between them: a marker written under one recipe would never match the
    other side, or one copy would carry a weaker anchor."""
    assert _declared_function_body(_LIB_SH, function_name) == _declared_function_body(
        _SKILL_MANAGEMENT_PLUGIN_LIB, function_name
    )
