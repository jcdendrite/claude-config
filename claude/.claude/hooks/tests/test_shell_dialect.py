"""Dialect lint over the shipped shell corpus: env-bash shebangs and no `sort -V`.

Scope: every file `scripts/list-shell-files.sh` returns, minus paths with a
`tests/` segment (test fixtures run only under the CI interpreter and are never
stowed).

Two rules:

- Shebang: a line 1 that names bash must be exactly `#!/usr/bin/env bash`.
  `/bin/bash` is 3.2 on macOS, so only an `env` lookup can reach a newer bash
  on the PATH. Files with no shebang, or a non-bash one, are exempt.
- `sort -V` / `--version-sort` is banned on any non-comment line. This is a
  BSD-userland rule, not a bash-version rule: BSD `sort` has no version sort.
  It carries no escape comment.

The predicates are pure functions of (relative path, text) so the control
table can feed them violating and clean samples without touching the tree.
"""

from __future__ import annotations

import functools
import re
import subprocess

import pytest
from helpers import REPO_ROOT

DISCOVERY_SCRIPT = REPO_ROOT / "scripts" / "list-shell-files.sh"

REQUIRED_BASH_SHEBANG = "#!/usr/bin/env bash"

_BASH_SHEBANG_NAME = re.compile(r"\bbash\b")

# `sort` followed on the same line (before any `#`) by a long option, a short
# flag cluster containing V (`-V`, `-rV`, `-Vr`), or a key spec attached to
# `-k` and containing V (`-k1,1V`). A flag token ends at whitespace, end of
# line, or a shell metacharacter, so `$(... | sort -V)` and `sort -V|tail -1`
# match.
# A key spec detached from `-k` is not matched: `-k 1,1V`, `--key=1V` and
# `--key 1,1V`.
_SORT_VERSION_FLAG = re.compile(
    r"\bsort\b[^#\n]*(?:--version-sort\b"
    r"|\s-[A-Za-z]*V[A-Za-z]*(?=[\s)`|;&\"'<>]|$)"
    r"|\s-k\S*V\S*(?=[\s)`|;&\"'<>]|$))"
)


def _is_in_scope(rel_path: str) -> bool:
    return "tests" not in rel_path.split("/")


def shebang_violation(rel_path: str, text: str) -> str | None:
    """Return line 1 when it is a bash shebang other than the required one.

    The comparison is exact: the kernel hands `env` everything after the
    interpreter path, so a trailing space, extra argument or `\\r` changes the
    program `env` looks up.
    """
    if not _is_in_scope(rel_path):
        return None
    first_line = text.split("\n", 1)[0]
    if not first_line.startswith("#!"):
        return None
    if not _BASH_SHEBANG_NAME.search(first_line):
        return None
    return None if first_line == REQUIRED_BASH_SHEBANG else first_line


def sort_version_violation_lines(rel_path: str, text: str) -> list[int]:
    """Return 1-based numbers of non-comment lines that version-sort."""
    if not _is_in_scope(rel_path):
        return []
    return [
        lineno
        for lineno, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith("#") and _SORT_VERSION_FLAG.search(line)
    ]


@functools.cache
def _shipped_shell_files() -> list[str]:
    out = subprocess.run(
        ["bash", str(DISCOVERY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(p for p in out.split("\0") if p and _is_in_scope(p))


def _read_text(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="replace")


SHEBANG_CONTROL_TABLE = [
    pytest.param("claude/.claude/hooks/x.sh", "#!/bin/bash\necho hi\n", "#!/bin/bash", id="bin-bash-violates"),
    pytest.param("claude/.claude/hooks/x.sh", "#!/usr/bin/bash\n", "#!/usr/bin/bash", id="usr-bin-bash-violates"),
    pytest.param(
        "claude/.claude/hooks/x.sh", "#!/usr/bin/env -S bash -e\n", "#!/usr/bin/env -S bash -e", id="env-with-flags-violates"
    ),
    pytest.param("claude/.claude/hooks/x.sh", "#!/bin/bash", "#!/bin/bash", id="bin-bash-without-newline-violates"),
    pytest.param(
        "claude/.claude/hooks/x.sh", "#!/usr/bin/env bash -e\n", "#!/usr/bin/env bash -e", id="env-bash-with-argument-violates"
    ),
    pytest.param(
        "claude/.claude/hooks/x.sh", "#!/usr/bin/env bash \n", "#!/usr/bin/env bash ", id="env-bash-trailing-space-violates"
    ),
    pytest.param(
        "claude/.claude/hooks/x.sh",
        "#!/usr/bin/env bash\r\necho hi\r\n",
        "#!/usr/bin/env bash\r",
        id="env-bash-crlf-violates",
    ),
    pytest.param("claude/.claude/hooks/x.sh", "#!/usr/bin/env bash\necho hi\n", None, id="env-bash-clean"),
    pytest.param("claude/.claude/hooks/x.sh", "# shellcheck shell=bash\necho hi\n", None, id="shellcheck-directive-exempt"),
    pytest.param("claude/.claude/hooks/x.sh", "echo hi\n", None, id="no-shebang-exempt"),
    pytest.param("claude/.claude/hooks/x.sh", "", None, id="empty-file-exempt"),
    pytest.param("claude/.claude/hooks/x.sh", "#!/bin/sh\necho hi\n", None, id="posix-sh-exempt"),
    pytest.param("claude/.claude/hooks/x.sh", "# comment\n#!/bin/bash\n", None, id="shebang-not-on-line-1-exempt"),
    pytest.param("claude/.claude/hooks/tests/x.sh", "#!/bin/bash\n", None, id="tests-segment-exempt"),
    pytest.param("claude/.claude/hooks/attests/x.sh", "#!/bin/bash\n", "#!/bin/bash", id="tests-substring-violates"),
]


SORT_VERSION_CONTROL_TABLE = [
    pytest.param("a/x.sh", "sort -V file\n", [1], id="sort-V-violates"),
    pytest.param("a/x.sh", "ls | sort --version-sort\n", [1], id="version-sort-long-option-violates"),
    pytest.param("a/x.sh", "ls | sort -rV\n", [1], id="sort-rV-violates"),
    pytest.param("a/x.sh", "ls | sort -uV\n", [1], id="sort-uV-violates"),
    pytest.param("a/x.sh", "ls | sort -k1,1V\n", [1], id="sort-key-modifier-violates"),
    pytest.param("a/x.sh", "ls | sort -Vr\n", [1], id="sort-Vr-violates"),
    pytest.param("a/x.sh", "ls | sort -uVr\n", [1], id="sort-uVr-violates"),
    pytest.param("a/x.sh", "x=$(ls | sort -V)\n", [1], id="sort-V-in-command-substitution-violates"),
    pytest.param("a/x.sh", "ls | sort -V|tail -1\n", [1], id="sort-V-before-pipe-without-space-violates"),
    *[
        pytest.param("a/x.sh", f"ls | sort {flag}{terminator}\n", [1], id=f"sort-{flag}-before-{name}-violates")
        for flag in ("-V", "-rV", "-k1,1V", "--version-sort")
        for name, terminator in (("paren", ")"), ("backtick", "`"), ("pipe", "|"), ("semicolon", ";"), ("quote", '"'))
    ],
    pytest.param("a/x.sh", "echo ok\nls | sort -V\n", [2], id="reports-line-number"),
    pytest.param("a/x.sh", "# sort -V is unavailable on BSD\n", [], id="comment-only-mention-clean"),
    pytest.param("a/x.sh", "    # sort --version-sort\n", [], id="indented-comment-clean"),
    pytest.param("a/x.sh", "ls | sort -u\n", [], id="sort-without-version-flag-clean"),
    pytest.param("a/x.sh", "ls | sort -k1,1\n", [], id="sort-key-without-modifier-clean"),
    pytest.param("a/x.sh", "ls | sort -rn\n", [], id="sort-numeric-clean"),
    pytest.param("a/tests/x.sh", "sort -V file\n", [], id="tests-segment-exempt"),
    pytest.param("a/attests/x.sh", "sort -V file\n", [1], id="tests-substring-violates"),
]


@pytest.mark.parametrize(("rel_path", "text", "expected"), SHEBANG_CONTROL_TABLE)
def test_shebang_predicate_control_table(rel_path: str, text: str, expected: str | None):
    assert shebang_violation(rel_path, text) == expected


@pytest.mark.parametrize(("rel_path", "text", "expected"), SORT_VERSION_CONTROL_TABLE)
def test_sort_version_predicate_control_table(rel_path: str, text: str, expected: list[int]):
    assert sort_version_violation_lines(rel_path, text) == expected


def test_discovery_covers_install_script_hooks_and_an_extensionless_script():
    """A broken discovery must fail here rather than let both rules pass vacuously."""
    files = _shipped_shell_files()
    assert files, "Discovery returned nothing — the corpus cannot be empty"
    assert "install.sh" in files
    assert "claude/.claude/hooks/_lib.sh" in files
    assert "plugins/lovable-cloud/hooks/validate-migration-filename.sh" in files
    assert "plugins/lovable-cloud/scripts/new-migration" in files, "extensionless shell scripts are not being discovered"


def test_every_bash_shebang_is_env_bash():
    violations = {}
    for rel_path in _shipped_shell_files():
        shebang = shebang_violation(rel_path, _read_text(rel_path))
        if shebang is not None:
            violations[rel_path] = shebang
    assert not violations, (
        f"{len(violations)} shipped shell file(s) with a bash shebang other than "
        f"{REQUIRED_BASH_SHEBANG!r} — macOS /bin/bash is 3.2, so only an env lookup reaches a newer bash:\n"
        + "\n".join(f"  {path}: {shebang}" for path, shebang in violations.items())
    )


def test_no_sort_version_sort_in_shipped_shell():
    violations = {}
    for rel_path in _shipped_shell_files():
        lines = sort_version_violation_lines(rel_path, _read_text(rel_path))
        if lines:
            violations[rel_path] = lines
    assert not violations, (
        "`sort -V` / `--version-sort` is unavailable in BSD sort (macOS); "
        "compare versions another way:\n" + "\n".join(f"  {path}: lines {lines}" for path, lines in violations.items())
    )
