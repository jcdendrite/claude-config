"""Regression guard: refs/remotes/origin/HEAD is read from exactly one
shell file under claude/.claude/ -- claude/.claude/hooks/_lib.sh, the
shared default-branch-resolution helper (docs/design-decisions.md §54).
Every other site resolves the default branch by calling that helper's two
layers (_lib_default_branch_from_origin_head /
_lib_default_branch_or_guess) rather than re-reading the ref itself.

Scope: all *.sh files under claude/.claude/ that are NOT under a tests/
path segment (test fixtures run only under the CI interpreter and are
never stowed) -- mirrors test_no_bash4_constructs.py's scope rationale.

Limitations: this test scans file text and skips full-comment lines, so a
doc-comment naming the literal in prose does not trip it. Suppress a
genuine false positive with ``# noqa-origin-head`` on the offending line.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

_TARGET_LITERAL = "refs/remotes/origin/HEAD"
_EXPECTED_HOME = CLAUDE_DIR / "hooks" / "_lib.sh"


def _is_tests_path(p: Path) -> bool:
    """Return True if any path segment is exactly 'tests'."""
    return "tests" in p.parts


def _discover_shell_files() -> list[Path]:
    return sorted(
        p for p in CLAUDE_DIR.rglob("*.sh")
        if not _is_tests_path(p)
    )


_SHELL_FILES = _discover_shell_files()

# Sentinel: fail loudly if discovery is broken rather than vacuously
# passing. Floor chosen conservatively below the count of .sh files under
# claude/.claude/ at the time of writing (70).
_MIN_SHELL_FILE_COUNT = 50


def test_discovery_is_non_empty() -> None:
    """Guard against a broken rglob root silently collecting zero files."""
    assert len(_SHELL_FILES) >= _MIN_SHELL_FILE_COUNT, (
        f"Expected at least {_MIN_SHELL_FILE_COUNT} .sh files under {CLAUDE_DIR}, "
        f"found {len(_SHELL_FILES)}. If the repo structure changed, update "
        "_MIN_SHELL_FILE_COUNT."
    )
    assert _EXPECTED_HOME in _SHELL_FILES, (
        f"{_EXPECTED_HOME} not found in discovered shell files -- check that "
        f"CLAUDE_DIR ({CLAUDE_DIR}) resolves to the right location."
    )


def test_lib_sh_carries_the_literal() -> None:
    """Sanity check for the exclusivity test below: if _lib.sh ever stopped
    carrying the literal, that test would pass vacuously."""
    assert _TARGET_LITERAL in _EXPECTED_HOME.read_text()


_OTHER_SHELL_FILES = [p for p in _SHELL_FILES if p != _EXPECTED_HOME]


def _scan_for_violations(script: Path) -> list[str]:
    """Return one formatted line per non-comment, non-suppressed reference
    to _TARGET_LITERAL in script."""
    violations = []
    for lineno, line in enumerate(script.read_text().splitlines(), start=1):
        if "# noqa-origin-head" in line:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # skip full-comment lines (e.g. explanatory prose naming the ref)
        if _TARGET_LITERAL in line:
            violations.append(f"  line {lineno}: {line.rstrip()}")
    return violations


@pytest.mark.parametrize(
    "script", _OTHER_SHELL_FILES, ids=[str(p.relative_to(CLAUDE_DIR)) for p in _OTHER_SHELL_FILES]
)
def test_literal_confined_to_lib_sh(script: Path) -> None:
    """Every shell file under claude/.claude/ other than _lib.sh must not
    reference refs/remotes/origin/HEAD directly -- default-branch
    resolution goes through _lib.sh's two layers instead."""
    violations = _scan_for_violations(script)
    assert not violations, (
        f"{script.relative_to(CLAUDE_DIR)} references {_TARGET_LITERAL!r} directly:\n"
        + "\n".join(violations)
        + "\nroute default-branch resolution through _lib.sh's "
        "_lib_default_branch_from_origin_head / _lib_default_branch_or_guess instead"
    )


def test_noqa_origin_head_suppresses_only_the_marked_line(tmp_path: Path) -> None:
    """The `# noqa-origin-head` escape hatch is unused in shipped code, so
    this pins that a marked line is excluded from violations while an
    unmarked violation elsewhere in the same file is still caught."""
    fixture = tmp_path / "synthetic.sh"
    fixture.write_text(
        f'echo "{_TARGET_LITERAL}"  # noqa-origin-head\n'
        f'echo "{_TARGET_LITERAL}"\n'
    )
    violations = _scan_for_violations(fixture)
    assert len(violations) == 1
    assert "line 2" in violations[0]
