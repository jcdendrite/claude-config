"""Three-layer hook alignment test suite.

Layer 0 — Docs coverage: every .sh hook in claude/.claude/hooks/ (excluding
_lib.sh) must have its own list-item entry in docs/hooks.md.

Layer 1 — Static checks: every .sh hook in claude/.claude/hooks/ and
plugins/*/hooks/ (excluding _lib.sh siblings) must declare a
`# hook-class: <value>` header on line 2 with a valid value, and hooks
matching gate-naming prefixes or the EXPLICIT_GATES set must declare
`# hook-class: gate`. Layer 1 also pins each gate-backed review skill to the
hook that gates it — both files present, and the hook still wired into a
PreToolUse matcher group — asserts that same PreToolUse wiring for every
hook-class: gate hook regardless of skill pairing, and pins standalone
config-value invariants in settings.json unrelated to gate/skill pairing
(e.g. the plan-mode-entry deny/defaultMode declarations).

Layer 2 — Behavior checks: every gate-class hook must deny on malformed
input, empty stdin, non-object `.tool_input`, and missing `_lib.sh`; and
every deny envelope it emits must match the expected schema shape.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from helpers import bash_input, build_path_without, write_input

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
# Layer 1 — Gate/skill pairing                                       #
# ------------------------------------------------------------------ #

_SKILLS_DIR = _REPO_ROOT / "claude" / ".claude" / "skills"
_SETTINGS_PATH = _REPO_ROOT / "claude" / ".claude" / "settings.base.json"


def _pretooluse_command_for(hook: Path) -> list[str]:
    """Every PreToolUse command wired to `hook`, matched by exact equality on
    the command's last shell word — not a substring/endswith match, which
    would also match a hook name appearing as a non-final CLI argument to an
    unrelated script. Tokenized with shlex, which parses shell quoting, so
    the match stays correct regardless of a plugin author's quoting style —
    a bare whitespace split has no notion of quoting at all, so pairing it
    with an `expected_invocation` written to match today's quoting
    convention is a coincidence of current data, not a guarantee.
    """
    if hook.parent == _MAIN_HOOKS_DIR:
        config_path = _SETTINGS_PATH
        expected_invocation = f"~/.claude/hooks/{hook.name}"
    else:
        config_path = hook.parent / "hooks.json"
        expected_invocation = f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{hook.name}"

    assert config_path.is_file(), (
        f"{hook.name}: expected registration config {config_path} does not "
        f"exist"
    )
    config = json.loads(config_path.read_text())
    matched: list[str] = []
    for group in config.get("hooks", {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []):
            if not isinstance(entry, dict):
                continue
            command = entry.get("command", "")
            tokens = shlex.split(command)
            if tokens and tokens[-1] == expected_invocation:
                matched.append(command)
    return matched


# Review skills whose descriptions advertise a gate, paired with the hook that
# enforces it. Each of these skills states a gate fact in its own frontmatter
# description; that claim is only true while the named hook still exists under
# that name.
_GATE_RELEASING_SKILLS: list[tuple[str, str]] = [
    ("code-review", "require-code-review.sh"),
    ("plan-review", "require-plan-review.sh"),
    ("ready-for-review", "require-ready-for-review.sh"),
    ("respond-pr", "require-respond-pr.sh"),
]


@pytest.mark.parametrize(
    ("skill_name", "hook_name"),
    _GATE_RELEASING_SKILLS,
    ids=[s for s, _ in _GATE_RELEASING_SKILLS],
)
def test_gate_backed_skill_has_a_live_gate(skill_name: str, hook_name: str) -> None:
    """A gate-backed review skill's hook must exist AND still be wired.

    These four skills describe themselves as gates ("Also the gate on
    `git commit`", "gates Write/Edit/MultiEdit/ExitPlanMode until this runs").
    That wording is a promise about whether an operation will be allowed, so
    it goes stale if the hook is renamed, deleted, or quietly unwired from
    settings.json while the skill keeps advertising the gate.

    Checking the hook file exists is not enough on its own: a require-*.sh
    left on disk but absent from every PreToolUse matcher group never fires,
    and the skill's description would still claim it does. So this asserts
    presence of both files and that the hook's command appears in a
    PreToolUse group, via the same _pretooluse_command_for scan
    test_gate_hook_registered_in_pretooluse_matcher below uses for every
    gate hook.

    Matcher content is deliberately not asserted: these four gates span
    different surfaces (Bash for commit/push/PR-comment gates, an
    Edit/Write/ExitPlanMode group for plan-review), so there is no single
    correct matcher to pin.

    Scope limit worth stating plainly: this proves the gate is armed, not
    that the skill's DO NOT TRIGGER clauses stay honorable by that gate's
    predicate. Honorability is a judgment about a bash predicate and is not
    mechanically decidable — see docs/hooks.md, "What a gate-backed skill's
    description may promise", which states that rule for humans to apply.
    """
    skill_file = _SKILLS_DIR / skill_name / "SKILL.md"
    hook_file = _MAIN_HOOKS_DIR / hook_name
    assert skill_file.is_file(), (
        f"{skill_name}: SKILL.md missing at {skill_file}, but {hook_name} "
        f"still gates on its marker — the gate can no longer be released"
    )
    assert hook_file.is_file(), (
        f"{hook_name}: missing, but {skill_name}/SKILL.md still describes "
        f"itself as gate-backed — update that description or restore the hook"
    )

    wired = _pretooluse_command_for(hook_file)
    assert wired, (
        f"{hook_name}: present on disk but not wired into any PreToolUse "
        f"matcher group in {_SETTINGS_PATH.name} — the gate never fires, yet "
        f"{skill_name}/SKILL.md still describes itself as gate-backed"
    )


@pytest.mark.parametrize("hook", GATE_HOOKS, ids=[h.name for h in GATE_HOOKS])
def test_gate_hook_registered_in_pretooluse_matcher(hook: Path) -> None:
    """Every hook-class: gate hook must be wired into a PreToolUse matcher
    group in its owning config file — claude/.claude/settings.json for a
    main-hooks-dir hook, that plugin's own hooks/hooks.json for a
    plugin-dir hook.

    hook-class: gate declares intent to fire on PreToolUse (see
    TestHookClassHeader.test_hook_class_value_valid's docstring above), and
    Layer 2's behavior checks (TestGateHookBehavior) assume the hook
    actually receives a PreToolUse payload — neither catches a gate hook
    left unregistered after a rename or a config edit that drops its entry.
    test_gate_backed_skill_has_a_live_gate above proves this same wiring for
    the 4 hooks backing a gate-releasing skill's promise; this generalizes
    it to every gate hook, independent of whether a skill advertises it.
    """
    assert _pretooluse_command_for(hook), (
        f"{hook.name}: hook-class: gate but not wired into any PreToolUse "
        f"matcher group in its owning config file"
    )


def test_plan_mode_entry_paths_stay_closed_in_settings() -> None:
    """The two config-value declarations backing plan-mode-entry discipline.

    This proves the *declared* config state — `"EnterPlanMode"` is present
    in `permissions.deny`, and `permissions.defaultMode` is not `"plan"` —
    not that the harness actually honors either at runtime. That live-session
    verification lives outside pytest (see
    `.claude/plans/plan-mode-workflow-discipline.md`'s Pre-implementation
    gate); this test only pins the declaration so a future edit can't drop it
    silently.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    assert "EnterPlanMode" in settings.get("permissions", {}).get("deny", []), (
        f"'EnterPlanMode' missing from permissions.deny in "
        f"{_SETTINGS_PATH.name} — agent-initiated harness plan-mode entry "
        f"is no longer blocked"
    )
    assert settings.get("permissions", {}).get("defaultMode") != "plan", (
        f"permissions.defaultMode is 'plan' in {_SETTINGS_PATH.name} — this "
        f"reopens the same escalation state the EnterPlanMode deny closes, "
        f"via a config write rather than a tool call"
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
            f"add `# hook-class: gate`, `# hook-class: informational`, "
            f"`# hook-class: turn-gate`, or `# hook-class: batch-gate` header to {hook.name}"
        )

    def test_hook_class_value_valid(self, hook: Path) -> None:
        """hook-class value must be 'gate', 'informational', 'turn-gate', or 'batch-gate'.

        'gate' fires PreToolUse and may deny a tool call. 'informational'
        fires PostToolUse/SessionStart/SessionEnd/etc. and never denies.
        'turn-gate' fires on Stop and may block the *turn* from ending
        (decision: "block") rather than a tool call from running — a
        distinct contract from 'gate', which is why it is a separate value
        rather than a Stop hook being mislabeled 'gate' (Layer 2's
        PreToolUse-specific behavior checks, e.g.
        test_emit_deny_defined_before_lib_source, do not apply to it) or
        'informational' (which would be a false label on a hook that
        blocks). 'batch-gate' fires on PostToolBatch and may stop the
        agentic loop before the next model call (exit 2, reason on stderr)
        — PostToolBatch's own native block, distinct from both 'gate'
        (PreToolUse deny, JSON envelope) and 'turn-gate' (Stop
        block-to-force-continuation); Layer 2's PreToolUse-specific
        behavior checks don't apply to it either.
        """
        value = _hook_class(hook)
        if value is None:
            pytest.skip("header absent — tested by test_hook_class_header_present")
        assert value in ("gate", "informational", "turn-gate", "batch-gate"), (
            f"{hook.name}: expected one of: gate, informational, turn-gate, batch-gate; got '{value}'"
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


def _run_hook_raw(
    hook: Path, stdin_text: str, cwd: Path | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run `hook` with raw stdin. `env`, when given, is merged on top of the
    real environment (not a replacement) — every case below only means to
    override PATH and/or HOME, and the hook still needs the rest of the real
    environment (e.g. TERM, LANG) to behave normally."""
    subprocess_env = {**os.environ, **env} if env is not None else None
    return subprocess.run(
        [str(hook)],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=subprocess_env,
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
        a deny is emitted. If a future hook violates the define-emit_deny →
        source-lib → gate-logic ordering, this test may give a false pass on the
        missing-lib path while actually testing something else.

        The pre-source `emit_deny` bootstrap (see _lib.sh's _lib_emit_deny
        contract comment) is a minimal hard-block stub — it does not attempt
        jq encoding, since _lib.sh (and thus _lib_jq's timeout backstop) isn't
        available yet. So this path always exits 2 with the reason on stderr,
        never the exit-0 JSON envelope the post-source path produces; accept
        either shape via _assert_blocks, matching the jq-absent tests below.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / hook.name
            shutil.copy2(hook, tmp_hook)
            tmp_hook.chmod(0o755)
            # Run with a valid PreToolUse payload so the deny must come from
            # the missing _lib.sh path, not an unrelated guard.
            payload = '{"tool_name":"Bash","tool_input":{"command":"echo hello"}}'
            result = _run_hook_raw(tmp_hook, payload, cwd=Path(tmpdir))
        _assert_blocks(result, hook.name, "missing-lib-sh", "could not source _lib.sh")

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


# ------------------------------------------------------------------ #
# Layer 2 — GH-480: missing-binary behavior (jq / sha256sum / gh)    #
# ------------------------------------------------------------------ #


@pytest.fixture(scope="session")
def _path_without(tmp_path_factory: pytest.TempPathFactory):
    """Return a builder `_path_without(binary) -> str`: a PATH string built
    as a symlink farm mirroring the real PATH, with `binary` omitted.

    Farms are memoized per binary for the whole test session, since every
    case that asks to remove the same binary gets an identical farm. Farm
    construction itself (full-mirror rationale, dedup, unreadable-dir
    handling) lives in `helpers.build_path_without`, shared with
    `test_advance_past_commit_stall.py`'s own non-memoized caller.
    """
    cache: dict[str, str] = {}

    def _build(binary: str) -> str:
        if binary in cache:
            return cache[binary]
        farm_dir = tmp_path_factory.mktemp(f"path-without-{binary}")
        cache[binary] = build_path_without(binary, farm_dir)
        return cache[binary]

    return _build


def _assert_blocks(
    result: subprocess.CompletedProcess,
    hook_name: str,
    context: str,
    expected_reason_substring: str,
) -> None:
    """Accept either legitimate blocking shape a gate hook may take: exit 0
    with a valid deny envelope (the normal jq-present path), or exit 2 with
    the expected reason substring on stderr (emit_deny's jq-absent
    fallback).

    Non-empty stderr alone does not qualify as "blocked": a bash syntax
    error or a `set -e` command failure both exit 2 with stderr, so a hook
    mangled during the mechanical 24-file emit_deny edit would pass a bare
    stderr-non-empty check. Requiring the specific reason substring closes
    that gap. All 24 parse-failure reasons share the substring "parse
    tool-input JSON"; the jq-absent diagnostic adds "jq".
    """
    if result.returncode == 0:
        _assert_deny_schema(result, hook_name, context)
        payload = json.loads(result.stdout.strip())
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        assert expected_reason_substring in reason, (
            f"{hook_name} [{context}]: deny reason missing expected substring "
            f"{expected_reason_substring!r}: {reason!r}"
        )
        return
    assert result.returncode == 2, (
        f"{hook_name} [{context}]: expected exit 0 (deny JSON) or exit 2 (stderr "
        f"block), got {result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert expected_reason_substring in result.stderr, (
        f"{hook_name} [{context}]: exit 2 stderr missing expected substring "
        f"{expected_reason_substring!r}: {result.stderr!r}"
    )


@pytest.mark.parametrize("hook", GATE_HOOKS, ids=[h.name for h in GATE_HOOKS])
def test_blocks_when_jq_absent(hook: Path, _path_without) -> None:
    """GH-480: with jq entirely absent from PATH, every gate hook must hard-
    block (exit 2, diagnostic on stderr) rather than emit malformed deny
    JSON on exit 0 — which the harness reads as no decision and lets the
    tool call proceed."""
    result = _run_hook_raw(hook, "not json", env={"PATH": _path_without("jq")})
    _assert_blocks(result, hook.name, "jq-absent-malformed-input", "jq")


@pytest.mark.parametrize("hook", GATE_HOOKS, ids=[h.name for h in GATE_HOOKS])
def test_blocks_when_jq_absent_with_valid_payload(hook: Path, _path_without) -> None:
    """Same as test_blocks_when_jq_absent, but with a well-formed Bash
    payload — the realistic case: a legitimate tool call arrives while jq
    happens to be unavailable, not a malformed request that would deny for
    an unrelated reason regardless of jq. Every gate parses input with jq
    before any tool-specific dispatch, so a Bash payload exercises the same
    jq-absent path uniformly across all 24 gates, including the ones that
    gate a different tool (Edit/Write/MultiEdit/ExitPlanMode)."""
    payload = json.dumps(bash_input("echo hello"))
    result = _run_hook_raw(hook, payload, env={"PATH": _path_without("jq")})
    _assert_blocks(result, hook.name, "jq-absent-valid-payload", "jq")


def _init_repo_with_commit(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _sha256sum_case_code_review(tmp_path: Path) -> tuple[Path, Path, dict, str]:
    """require-code-review.sh: a staged change reaching the marker check via
    a `git commit` command. With sha256sum absent, both the repo-hash and
    the staged-diff-hash computations fail, so CURRENT_HASH comes back
    empty; _lib_marker_value_present's empty-expected-value guard then
    denies via the hook's own ordinary "not reviewed" message — not via
    emit_deny's jq fallback, since jq itself is untouched here."""
    repo = tmp_path / "code-review-repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    (repo / "file.txt").write_text("first\nsecond\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    hook = _MAIN_HOOKS_DIR / "require-code-review.sh"
    return hook, repo, bash_input("git commit -m test"), "have not been reviewed"


def _sha256sum_case_plan_review(tmp_path: Path) -> tuple[Path, Path, dict, str]:
    """require-plan-review.sh: an active (untracked) plan file, whose
    content hash requires sha256sum via _lib_active_plan_hash. With
    sha256sum absent, the hash fails per-file and the hook denies via its
    own "cannot read the active plan file" message."""
    repo = tmp_path / "plan-review-repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    plans_dir = repo / ".claude" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "impl-plan.md").write_text("# Implementation plan\n\nStep 1...\n")
    hook = _MAIN_HOOKS_DIR / "require-plan-review.sh"
    return hook, repo, write_input(str(repo / "some_file.py")), "cannot read the active plan file"


def _sha256sum_case_skill_review(tmp_path: Path) -> tuple[Path, Path, dict, str]:
    """require-skill-review.sh (skill-management plugin): a staged SKILL.md
    change reaching the marker check via a `git commit` command. Same
    empty-hash fail-closed path as the code-review case above."""
    repo = tmp_path / "skill-review-repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    skill_file = repo / "claude" / ".claude" / "skills" / "test-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("## test skill\n")
    subprocess.run(["git", "add", str(skill_file.relative_to(repo))], cwd=repo, check=True)
    hook = _REPO_ROOT / "plugins" / "skill-management" / "hooks" / "require-skill-review.sh"
    return hook, repo, bash_input("git commit -m test", session_id="test-session"), "have not been audited"


_SHA256SUM_MARKER_GATE_CASES = [
    _sha256sum_case_code_review,
    _sha256sum_case_plan_review,
    _sha256sum_case_skill_review,
]


@pytest.mark.parametrize(
    "build_case",
    _SHA256SUM_MARKER_GATE_CASES,
    ids=[fn.__name__ for fn in _SHA256SUM_MARKER_GATE_CASES],
)
def test_marker_gate_blocks_without_sha256sum(build_case, tmp_path: Path, _path_without) -> None:
    """GH-480 ledger row 4a: the marker-check gates that shell out to
    sha256sum (require-code-review.sh, require-plan-review.sh,
    plugins/skill-management/hooks/require-skill-review.sh) must still deny
    when it is absent from PATH — an unhashable diff/plan must never read as
    an authorized match. jq stays on PATH for this test: the deny comes from
    each hook's own hash-mismatch fail-closed logic, not from emit_deny's jq
    fallback, so the result is always the normal exit-0 deny envelope."""
    hook, cwd, payload, expected_substring = build_case(tmp_path)
    env = {"PATH": _path_without("sha256sum"), "HOME": str(tmp_path / "home")}
    result = _run_hook_raw(hook, json.dumps(payload), cwd=cwd, env=env)
    _assert_blocks(result, hook.name, "sha256sum-absent", expected_substring)


def test_ready_for_review_allows_when_gh_absent(tmp_path: Path, _path_without) -> None:
    """GH-480 ledger row 4b, corrected against measurement.

    require-ready-for-review.sh is the one gate that shells out to `gh`
    (`gh pr view`, guarding the PR-existence check at line ~183). The plan
    this test was specified from assumed gh's absence "fails closed"
    (ledger row 4b), by analogy with sha256sum. Measured directly — a valid
    `git push` payload, `gh` removed from PATH, jq present — it does not:
    PR_NUMBER comes back empty exactly as it would for "no open PR" or "gh
    not configured", both of which this hook already documents as fail-open
    ("gh pr view fails ... fail-open to keep the user unblocked"). This test
    pins that actual, deliberate behavior rather than force a "blocks"
    assertion the hook was never designed to satisfy; flagged to the
    dispatching session rather than resolved by changing the hook's fail
    posture, which is outside GH-480's jq-encoding scope.
    """
    repo = tmp_path / "ready-for-review-repo"
    repo.mkdir()
    _init_repo_with_commit(repo)

    hook = _MAIN_HOOKS_DIR / "require-ready-for-review.sh"
    payload = bash_input("git push", session_id="gh-absent-test")
    env = {"PATH": _path_without("gh"), "HOME": str(tmp_path / "home")}
    result = _run_hook_raw(hook, json.dumps(payload), cwd=repo, env=env)
    assert result.returncode == 0, (
        f"expected exit 0 (documented fail-open), got {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not result.stdout.strip(), f"expected silent allow, got stdout={result.stdout!r}"


@pytest.mark.timing
def test_blocks_when_jq_hangs(tmp_path: Path) -> None:
    """GH-480: a jq that hangs (never returns) must not hold the gate open
    indefinitely. Every gate calls _lib_jq twice on a hung binary: once
    inside _lib_parse_tool_input_or_deny (parsing the payload), which times
    out and calls emit_deny with a parse-failure reason; emit_deny then
    tries _lib_jq again to encode that reason, and also times out. Both
    calls share the same 5s backstop, so the two chain to ~10s rather than
    ~5s — measured directly against require-code-review.sh, not assumed.

    Builds its own fake-slow-jq PATH rather than reusing test_lib.py's
    idiom verbatim: that idiom's symlinked-tool list omits `sleep`, so its
    fake jq script (`sleep 10`) fails instantly with "command not found"
    there instead of actually hanging — it still passes (a crashed jq also
    denies), but it does not exercise the timeout backstop it's named for.
    """
    timeout_path = shutil.which("timeout")
    if not timeout_path:
        pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")

    stub_bin = tmp_path / "stub_bin"
    stub_bin.mkdir()
    fake_jq = stub_bin / "jq"
    fake_jq.write_text("#!/bin/bash\nsleep 10\n")
    fake_jq.chmod(0o755)
    (stub_bin / "timeout").symlink_to(timeout_path)
    (stub_bin / "bash").symlink_to(bash_path)
    for cmd in ("head", "tail", "cat", "cut", "printf", "sleep", "grep", "dirname", "git"):
        cmd_path = shutil.which(cmd)
        if cmd_path:
            (stub_bin / cmd).symlink_to(cmd_path)

    hook = _MAIN_HOOKS_DIR / "require-code-review.sh"
    start = time.monotonic()
    result = _run_hook_raw(hook, "not json", env={"PATH": str(stub_bin)})
    elapsed = time.monotonic() - start

    assert result.returncode == 2, (
        f"expected exit 2 once both timeout backstops fire, got {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "jq" in result.stderr, repr(result.stderr)
    # 12s = 2 x _lib_jq's 5s backstop (measured ~10.0s end-to-end) plus the
    # same ~20% buffer test_lib.py's single-call hung-jq test gives its own
    # 5s backstop (elapsed < 6).
    assert elapsed < 12, f"hung-jq test took {elapsed:.1f}s — timeout backstops did not fire as expected"
