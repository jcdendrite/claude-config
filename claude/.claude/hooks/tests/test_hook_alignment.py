"""Three-layer hook alignment test suite.

Layer 0 — Docs coverage: every .sh hook in claude/.claude/hooks/ (excluding
_lib.sh) must have its own list-item entry in docs/hooks.md.

Layer 1 — Static checks: every .sh hook in claude/.claude/hooks/ and
plugins/*/hooks/ (excluding _lib.sh siblings) must declare a
`# hook-class: <value>` header on line 2 with a valid value, and hooks
matching gate-naming prefixes or the EXPLICIT_GATES set must declare
`# hook-class: gate`.

Layer 2 — Behavior checks: every gate-class hook must deny on malformed
input, empty stdin, non-object `.tool_input`, and missing `_lib.sh`; and
every deny envelope it emits must match the expected schema shape.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# ------------------------------------------------------------------ #
# Paths                                                               #
# ------------------------------------------------------------------ #

_REPO_ROOT = Path(__file__).resolve().parents[4]
_MAIN_HOOKS_DIR = _REPO_ROOT / "claude" / ".claude" / "hooks"
_PLUGIN_HOOKS_DIRS = list((_REPO_ROOT / "plugins").glob("*/hooks"))


def _all_hook_files() -> list[Path]:
    """Return every .sh hook across claude/.claude/hooks/ and plugins/*/hooks/,
    excluding _lib.sh files."""
    hooks: list[Path] = []
    for sh in sorted(_MAIN_HOOKS_DIR.glob("*.sh")):
        if sh.name != "_lib.sh":
            hooks.append(sh)
    for hooks_dir in _PLUGIN_HOOKS_DIRS:
        for sh in sorted(hooks_dir.glob("*.sh")):
            if sh.name != "_lib.sh":
                hooks.append(sh)
    return hooks


def _hook_class(hook: Path) -> str | None:
    """Return the hook-class value from line 2, or None if absent."""
    lines = hook.read_text().splitlines()
    # Line 2 is index 1. Search first 5 lines to tolerate blank shebang lines.
    for line in lines[1:6]:
        m = re.match(r"#\s*hook-class:\s*(\S+)", line)
        if m:
            return m.group(1)
    return None


# ------------------------------------------------------------------ #
# Gate-classification rules                                           #
# ------------------------------------------------------------------ #

# Filename-prefix patterns that mandate hook-class: gate.
_GATE_PREFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^deny-.*\.sh$"),
    re.compile(r"^require-.*\.sh$"),
    re.compile(r"^enforce-.*\.sh$"),
    re.compile(r"^guard-.*\.sh$"),
    re.compile(r"^block-.*\.sh$"),
    re.compile(r"^check-.*-guard\.sh$"),
]

# Hooks that are gates by behavior but don't match the prefix patterns.
_EXPLICIT_GATES: frozenset[str] = frozenset(
    {
        "check-claude-md-length.sh",
        "check-skill-length.sh",
    }
)

ALL_HOOKS = _all_hook_files()
GATE_HOOKS = [h for h in ALL_HOOKS if _hook_class(h) == "gate"]

# Hooks documented in docs/hooks.md only for the main hooks dir — that doc's
# stated scope excludes plugins/*/hooks/ (plugin hooks are documented in
# their own plugin's docs instead).
_MAIN_HOOKS = [h for h in ALL_HOOKS if h.parent == _MAIN_HOOKS_DIR]
_HOOKS_DOC = _REPO_ROOT / "docs" / "hooks.md"


def _is_gate_by_naming(hook: Path) -> bool:
    name = hook.name
    if name in _EXPLICIT_GATES:
        return True
    return any(p.match(name) for p in _GATE_PREFIX_PATTERNS)


# ------------------------------------------------------------------ #
# Layer 0 — Docs coverage                                            #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("hook", _MAIN_HOOKS, ids=[h.name for h in _MAIN_HOOKS])
def test_hook_documented_in_hooks_md(hook: Path) -> None:
    """Every hook in claude/.claude/hooks/ must have its own entry in
    docs/hooks.md.

    docs/hooks.md opens with "Full descriptions for every hook in
    claude/.claude/hooks/" — this test keeps that claim true. Plugin hooks
    (plugins/*/hooks/) are out of scope; docs/hooks.md documents the main
    hooks dir only.

    Requires a line-start `- **`{name}`**` bullet (docs/hooks.md's
    established entry convention) rather than a bare substring match: a
    bare match would false-pass a hook whose own bullet was deleted but
    whose name survives as a cross-reference elsewhere in the file (e.g.
    `require-memory-skill.sh` and `require-code-review.sh` are both named
    again, outside their own bullets, in the "Gate deadlock recovery"
    section below). The bullet-anchored regex assumes hooks are referenced
    by bare filename (docs/hooks.md's convention today, with no path
    prefix); a future path-qualified reference wouldn't match this
    pattern, but that only produces a loud false-negative failure, not a
    silent false-pass.
    """
    doc_text = _HOOKS_DOC.read_text()
    bullet_pattern = re.compile(
        rf"^- \*\*`{re.escape(hook.name)}`\*\*", re.MULTILINE
    )
    assert bullet_pattern.search(doc_text), (
        f"{hook.name}: not documented in docs/hooks.md — add an entry "
        f"under Gate hooks or Utility hooks"
    )


# ------------------------------------------------------------------ #
# Layer 1 — Static checks                                            #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("hook", ALL_HOOKS, ids=[h.name for h in ALL_HOOKS])
class TestHookClassHeader:
    def test_hook_class_header_present(self, hook: Path) -> None:
        """Every hook must declare # hook-class: <value>."""
        value = _hook_class(hook)
        assert value is not None, (
            f"add `# hook-class: gate` or `# hook-class: informational` "
            f"header to {hook.name}"
        )

    def test_hook_class_value_valid(self, hook: Path) -> None:
        """hook-class value must be 'gate' or 'informational'."""
        value = _hook_class(hook)
        if value is None:
            pytest.skip("header absent — tested by test_hook_class_header_present")
        assert value in ("gate", "informational"), (
            f"{hook.name}: expected one of: gate, informational; got '{value}'"
        )

    def test_gate_naming_convention_enforced(self, hook: Path) -> None:
        """Hooks matching gate-naming patterns must declare hook-class: gate."""
        if not _is_gate_by_naming(hook):
            pytest.skip("filename does not match gate-naming patterns")
        value = _hook_class(hook)
        assert value == "gate", (
            f"{hook.name} matches gate convention (prefix or explicit-gates set) "
            f"but declared '{value}'"
        )

    def test_emit_deny_defined_before_lib_source(self, hook: Path) -> None:
        """Gate hooks must define emit_deny before sourcing _lib.sh.

        _lib_parse_tool_input_or_deny calls emit_deny at source time if parse
        fails. If emit_deny is not yet defined when _lib.sh is sourced, all
        three deny paths silently no-op (bash 'command not found' to stderr,
        exit 0 without deny JSON). Static ordering check closes this gap.
        """
        if _hook_class(hook) != "gate":
            pytest.skip("not a gate hook")
        lines = hook.read_text().splitlines()
        emit_deny_line = next(
            (
                i for i, ln in enumerate(lines)
                if re.search(r"emit_deny\s*\(\s*\)", ln) and not ln.strip().startswith("#")
            ),
            None,
        )
        lib_source_line = next(
            (
                i for i, ln in enumerate(lines)
                if re.search(r'[.]\s+.*_lib\.sh', ln) and not ln.strip().startswith("#")
            ),
            None,
        )
        assert emit_deny_line is not None, (
            f"{hook.name}: emit_deny() definition not found — "
            "gate hooks must define emit_deny before sourcing _lib.sh"
        )
        assert lib_source_line is not None, (
            f"{hook.name}: _lib.sh source line not found — "
            "gate hooks must source _lib.sh via '. \"$(dirname \"$0\")/_lib.sh\"'"
        )
        assert emit_deny_line < lib_source_line, (
            f"{hook.name}: emit_deny() defined at line {emit_deny_line + 1} but "
            f"_lib.sh sourced at line {lib_source_line + 1} — "
            "emit_deny must be defined BEFORE sourcing _lib.sh"
        )


# ------------------------------------------------------------------ #
# Layer 2 — Behavior checks                                          #
# ------------------------------------------------------------------ #


def _run_hook_raw(hook: Path, stdin_text: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(hook)],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def _assert_deny_schema(result: subprocess.CompletedProcess, hook_name: str, context: str) -> None:
    """Assert stdout is valid JSON with the expected deny schema."""
    stdout = result.stdout.strip()
    assert stdout, (
        f"{hook_name} [{context}]: hook must emit deny JSON on stdout, not silent exit"
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{hook_name} [{context}]: stdout is not valid JSON: {exc!r}\n  stdout={stdout!r}")
    hso = payload.get("hookSpecificOutput", {})
    assert hso.get("hookEventName") == "PreToolUse", (
        f"{hook_name} [{context}]: hookEventName must be 'PreToolUse'"
    )
    assert hso.get("permissionDecision") == "deny", (
        f"{hook_name} [{context}]: permissionDecision must be 'deny'"
    )
    reason = hso.get("permissionDecisionReason", "")
    assert isinstance(reason, str) and reason, (
        f"{hook_name} [{context}]: permissionDecisionReason must be a non-empty string"
    )


@pytest.mark.parametrize("hook", GATE_HOOKS, ids=[h.name for h in GATE_HOOKS])
class TestGateHookBehavior:
    def test_malformed_input_denied(self, hook: Path) -> None:
        """Gate hook must deny on malformed JSON input (not silent exit)."""
        result = _run_hook_raw(hook, "not json")
        assert result.returncode == 0, f"{hook.name}: exit code must be 0, got {result.returncode}"
        _assert_deny_schema(result, hook.name, "malformed-input")

    def test_empty_stdin_denied(self, hook: Path) -> None:
        """Gate hook must deny on empty stdin (CISO S1.a)."""
        result = _run_hook_raw(hook, "")
        assert result.returncode == 0, f"{hook.name}: exit code must be 0, got {result.returncode}"
        _assert_deny_schema(result, hook.name, "empty-stdin")

    def test_non_object_tool_input_denied(self, hook: Path) -> None:
        """Gate hook must deny when .tool_input is a string, not an object.

        This catches the structural-type error path in _lib_parse_tool_input_or_deny:
        jq '.tool_input.command // empty' against {"tool_input":"a string"} raises
        'Cannot index string with string "command"' and returns non-zero.
        """
        result = _run_hook_raw(hook, '{"tool_name":"Bash","tool_input":"a string"}')
        assert result.returncode == 0, f"{hook.name}: exit code must be 0, got {result.returncode}"
        _assert_deny_schema(result, hook.name, "non-object-tool-input")

    def test_missing_lib_sh_denied(self, hook: Path) -> None:
        """Gate hook must deny when _lib.sh is absent (rollback test).

        The hook is COPIED (not symlinked) into a temp directory so that
        dirname($0) resolves to the temp dir — a symlink would resolve to the
        original location where _lib.sh IS present, defeating the test.

        Convention relied on: every gate hook must define emit_deny and attempt
        to source _lib.sh BEFORE any other logic that could emit a deny for a
        different reason. The test cannot structurally distinguish "denied due to
        missing _lib.sh" from "denied for another reason" — it only verifies that
        a deny is emitted and that exit code is 0. If a future hook violates the
        define-emit_deny → source-lib → gate-logic ordering, this test may give
        a false pass on the missing-lib path while actually testing something else.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / hook.name
            shutil.copy2(hook, tmp_hook)
            tmp_hook.chmod(0o755)
            # Run with a valid PreToolUse payload so the deny must come from
            # the missing _lib.sh path, not an unrelated guard.
            payload = '{"tool_name":"Bash","tool_input":{"command":"echo hello"}}'
            result = _run_hook_raw(tmp_hook, payload, cwd=Path(tmpdir))
        assert result.returncode == 0, f"{hook.name}: exit code must be 0, got {result.returncode}"
        _assert_deny_schema(result, hook.name, "missing-lib-sh")

    def test_deny_envelope_schema_shape(self, hook: Path) -> None:
        """Every deny envelope must match the required JSON schema shape.

        Redundant with the schema assertions in the other tests, but kept as
        a dedicated parametrized case so schema-drift regressions produce a
        clear signal naming the exact hook and field.
        """
        result = _run_hook_raw(hook, "not json")
        if not result.stdout.strip():
            pytest.skip("hook did not emit output on malformed input — tested by test_malformed_input_denied")
        _assert_deny_schema(result, hook.name, "schema-shape")
