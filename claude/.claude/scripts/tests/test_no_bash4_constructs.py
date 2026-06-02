"""Regression guard: no shipped .sh file under claude/.claude/ may use bash-4+
constructs that break on macOS bash 3.2.57.

Guarded tokens: ``declare -A`` (including compound-flag forms like ``declare -xA``
or ``declare -Ax``), ``mapfile``, ``readarray``, ``sort -V`` / ``sort --version-sort``.

Scope: all *.sh files under claude/.claude/ that are NOT under a tests/
path segment (test fixtures run only under the CI interpreter and are never
stowed to a user's machine).

Limitations: this test scans file text; it does not parse heredoc or
string-literal bodies. A construct inside a heredoc body would produce a
false-positive failure. Suppress with ``# noqa-bash4`` on the offending line.
This test is a named-token creep guard, not a full bash-3.2 compatibility proof.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CLAUDE_DIR = _REPO_ROOT / "claude" / ".claude"

_BASH4_PATTERNS = re.compile(
    r'\bdeclare\b[^#\n]*\s-\w*A\w*'       # declare with -A flag in any position/combination
    r'|\b(?:mapfile|readarray)\b'
    r'|\bsort\b[^#\n]*(?:-V\b|--version-sort\b)'  # sort -V (GNU-only, not available on BSD/macOS)
)


def _is_tests_path(p: Path) -> bool:
    """Return True if any path segment is exactly 'tests'."""
    return "tests" in p.parts


def _discover_shell_files() -> list[Path]:
    return sorted(
        p for p in _CLAUDE_DIR.rglob("*.sh")
        if not _is_tests_path(p)
    )


_SHELL_FILES = _discover_shell_files()

# Sentinel: fail loudly if discovery is broken rather than vacuously passing.
# Floor chosen conservatively at the count of in-scope .sh files at the time
# of writing; update downward intentionally if scripts are removed.
_MIN_SHELL_FILE_COUNT = 10


def test_discovery_is_non_empty() -> None:
    """Guard against a broken rglob root silently collecting zero files."""
    assert len(_SHELL_FILES) >= _MIN_SHELL_FILE_COUNT, (
        f"Expected at least {_MIN_SHELL_FILE_COUNT} .sh files under {_CLAUDE_DIR}, "
        f"found {len(_SHELL_FILES)}. "
        "If the repo structure changed, update _MIN_SHELL_FILE_COUNT."
    )
    # Spot-anchor: verify a known-stable script is present so a restructure that
    # moves scripts to a different subtree can't reach the floor via unrelated files.
    assert any("deny-private-project-refs.sh" in str(p) for p in _SHELL_FILES), (
        "deny-private-project-refs.sh not found in discovered shell files — "
        f"check that _CLAUDE_DIR ({_CLAUDE_DIR}) resolves to the right location."
    )


@pytest.mark.parametrize("script", _SHELL_FILES, ids=[str(p.relative_to(_CLAUDE_DIR)) for p in _SHELL_FILES])
def test_no_bash4_constructs(script: Path) -> None:
    """Each shipped .sh file must be free of bash-4+ constructs."""
    violations = []
    for lineno, line in enumerate(script.read_text().splitlines(), start=1):
        if "# noqa-bash4" in line:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # skip full-comment lines (e.g. explanatory prose naming mapfile)
        if _BASH4_PATTERNS.search(line):
            violations.append(f"  line {lineno}: {line.rstrip()}")
    assert not violations, (
        f"{script.relative_to(_REPO_ROOT)} contains bash-4+ constructs:\n"
        + "\n".join(violations)
    )
