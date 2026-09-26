"""Three-layer hook alignment test suite.

Layer 0 — Docs coverage: every .sh hook in claude/.claude/hooks/ (excluding
_lib.sh and _config.sh) must have its own list-item entry in docs/hooks.md.

Layer 1 — Static checks: every .sh hook in claude/.claude/hooks/ and
plugins/*/hooks/ (excluding _lib.sh/_config.sh siblings) must declare a
`# hook-class: <value>` header on line 2 with a valid value, and hooks
matching gate-naming prefixes or the EXPLICIT_GATES set must declare
`# hook-class: gate`. Every `hook-class: gate` hook must also declare a
`# tier-threat-model: <tiers>` header on line 3, using only the tokens in
`_THREAT_MODEL_TIERS`, matching the value docs/hooks.md's `## Threat-model
tiers` table gives that hook. Layer 1 also pins each gate-backed review skill to the
hook that gates it — both files present, and the hook still wired into a
PreToolUse matcher group — asserts that same PreToolUse wiring for every
hook-class: gate hook regardless of skill pairing, and pins standalone
config-value invariants in settings.json unrelated to gate/skill pairing
(e.g. the plan-mode-entry deny/defaultMode declarations). Four further
static shape checks:
- Every `jq` invocation in claude/.claude/hooks/*.sh goes through a
  `_lib_*` wrapper (bare `jq` outside one reintroduces the per-hook
  duplicated timeout-handling this suite exists to prevent).
- Every `grep`-family command match in claude/.claude/hooks/*.sh resolves
  through the shared subcommand-matching helpers rather than a
  hand-rolled literal pattern. Bash's native `[[ ... =~ ... ]]` is a
  different, unswept mechanism for the same hand-rolled-matcher shape
  (e.g. require-respond-pr.sh's `PATTERN_*` family) -- out of scope for
  this check.
- No hook regex in claude/.claude/hooks/*.sh, plugins/*/hooks/*.sh, or
  either directory's _lib.sh uses GNU grep's `\\s` extension, which a
  POSIX-strict grep reads as a literal `s`.
- Every hook entry object inside `hooks.<Event>[].hooks[]` in
  claude/.claude/settings.json carries non-empty `type` and `command`
  fields — catches an entry left with only a `timeout` key and no `type`
  or `command`, the shape a scripted edit produces when it writes to the
  wrong object.
- record-session-end.sh's SessionEnd registration in
  claude/.claude/settings.json carries an integer `timeout` between 10 and
  60 — catches a deleted or corrupted `timeout` field silently
  reintroducing the "Hook cancelled" regression it exists to fix.

The first two carry a small, named exemption dict for a structural holdout
that resisted conversion. The `\\s` check has none: no live `\\s`
occurrence remains anywhere in its scope. See `_all_hook_files` for why
only the `\\s` check also sweeps each directory's _lib.sh.

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
from pathlib import Path

import pytest
from helpers import (
    assert_cap_engaged,
    bash_input,
    build_path_without,
    run_hook,
    scaled_shim_sleep,
    write_input,
    write_scaled_timeout_shim,
)

# ------------------------------------------------------------------ #
# Paths                                                               #
# ------------------------------------------------------------------ #

_REPO_ROOT = Path(__file__).resolve().parents[4]
_MAIN_HOOKS_DIR = _REPO_ROOT / "claude" / ".claude" / "hooks"
_PLUGIN_HOOKS_DIRS = list((_REPO_ROOT / "plugins").glob("*/hooks"))


# Shared helper libraries, not hooks -- sourced by hooks/scripts, never
# themselves registered on a PreToolUse/PostToolUse matcher or documented as
# a standalone hook in docs/hooks.md.
_HELPER_LIBRARY_NAMES: frozenset[str] = frozenset({"_lib.sh", "_config.sh"})


def _all_hook_files(*, include_lib: bool = False) -> list[Path]:
    """Return every .sh hook across claude/.claude/hooks/ and plugins/*/hooks/.

    Excludes both shared helper libraries (_lib.sh, _config.sh) by default.
    Pass include_lib=True to add _lib.sh back in -- used only by the `\\s`
    detector, which is safe against _lib.sh because it isn't defined there.
    _config.sh stays excluded even then: it exists only under
    claude/.claude/hooks/, so folding it into ALL_HOOKS_AND_LIBS would break
    test_all_hooks_and_libs_includes_every_lib_sh's one-_lib.sh-per-directory
    count invariant. The bare-jq and inline-matcher detectors stay excluded
    from _lib.sh because each is itself defined inside _lib.sh using the
    exact primitive it detects -- including it would be a guaranteed
    self-match.
    """
    excluded = {"_config.sh"} if include_lib else _HELPER_LIBRARY_NAMES
    hooks: list[Path] = []
    for sh in sorted(_MAIN_HOOKS_DIR.glob("*.sh")):
        if sh.name not in excluded:
            hooks.append(sh)
    for hooks_dir in _PLUGIN_HOOKS_DIRS:
        for sh in sorted(hooks_dir.glob("*.sh")):
            if sh.name not in excluded:
                hooks.append(sh)
    return hooks


# Shared with the tier-threat-model finder below: both header comments live
# within a file's first several lines, tolerating blank lines before either.
_HEADER_SCAN_LINE_COUNT = 6


def _hook_class(hook: Path) -> str | None:
    """Return the hook-class value from line 2, or None if absent."""
    lines = hook.read_text().splitlines()
    # Line 2 is index 1. Search first 5 lines to tolerate blank shebang lines.
    for line in lines[1:_HEADER_SCAN_LINE_COUNT]:
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
ALL_HOOKS_AND_LIBS = _all_hook_files(include_lib=True)
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
    claude/.claude/hooks/" — this test keeps that claim true. Its opening
    line also names one exception: the "## Threat-model tiers" table also
    covers the 4 plugin gates. That exception doesn't reach this test's own
    per-hook bullet requirement below, which stays main-dir-only; plugin hooks
    (plugins/*/hooks/) have no bullet of their own in docs/hooks.md.

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

_SKILLS_DIR = _REPO_ROOT / "claude-skills" / "skills"
# _SETTINGS_PATH means the stow-source file here, the opposite of what
# `SETTINGS_PATH` means in test_claude_md_excludes.py (repo-root) — don't
# assume the two modules share a convention.
_SETTINGS_PATH = _REPO_ROOT / "claude" / ".claude" / "settings.json"
_REPO_LOCAL_SETTINGS_PATH = _REPO_ROOT / ".claude" / "settings.json"
_ATTRIBUTION_SETTINGS_PATHS = (_SETTINGS_PATH, _REPO_LOCAL_SETTINGS_PATH)


def _tree_settings_paths() -> list[Path]:
    """Return the `settings*.json` files directly under `claude/.claude/` and `.claude/`.

    Reads the working tree. Excludes `*.local.json`.
    A settings file in a subdirectory or under another name is not found.
    """
    candidates = [
        *(_REPO_ROOT / "claude" / ".claude").glob("settings*.json"),
        *(_REPO_ROOT / ".claude").glob("settings*.json"),
    ]
    return sorted(path for path in candidates if not path.name.endswith(".local.json"))


_TREE_SETTINGS_PATHS = _tree_settings_paths()


def _pretooluse_entries_for(hook: Path) -> list[dict]:
    """Every PreToolUse hook-entry dict wired to `hook`, matched by exact
    equality on the command's last shell word — not a substring/endswith
    match, which would also match a hook name appearing as a non-final CLI
    argument to an unrelated script. Tokenized with shlex, which parses
    shell quoting, so the match stays correct regardless of a plugin
    author's quoting style — a bare whitespace split has no notion of
    quoting at all, so pairing it with an `expected_invocation` written to
    match today's quoting convention is a coincidence of current data, not
    a guarantee.
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
    matched: list[dict] = []
    for group in config.get("hooks", {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []):
            if not isinstance(entry, dict):
                continue
            command = entry.get("command", "")
            tokens = shlex.split(command)
            if tokens and tokens[-1] == expected_invocation:
                matched.append(entry)
    return matched


def _pretooluse_command_for(hook: Path) -> list[str]:
    """Every PreToolUse command string wired to `hook` — see
    _pretooluse_entries_for for the matching rules."""
    return [entry.get("command", "") for entry in _pretooluse_entries_for(hook)]


def test_every_registered_hook_entry_has_type_and_command() -> None:
    """Every hook entry under `hooks.<Event>[].hooks[]` must carry
    non-empty `type` and `command` — see the module docstring's static
    checks list for what this guards against."""
    settings = json.loads(_SETTINGS_PATH.read_text())
    for event_name, groups in settings.get("hooks", {}).items():
        for group in groups:
            if not isinstance(group, dict):
                continue
            for entry in group.get("hooks", []):
                assert isinstance(entry, dict), (
                    f"{event_name}: hook entry is not an object: {entry!r}"
                )
                assert entry.get("type"), (
                    f"{event_name}: hook entry missing non-empty 'type': {entry!r}"
                )
                assert entry.get("command"), (
                    f"{event_name}: hook entry missing non-empty 'command': {entry!r}"
                )


def test_record_session_end_timeout_stays_within_ceiling() -> None:
    """The declared config-value backing record-session-end.sh's raised
    SessionEnd execution budget.

    This proves the *declared* config state — the SessionEnd hook entry
    whose command ends in `record-session-end.sh` carries an integer
    `timeout` between 10 and 60 — not that the harness actually honors it
    at runtime. That live-session verification is unavailable outside a
    real SessionEnd fire; this test only pins the declaration so a future
    edit can't silently drop the `timeout` field and reintroduce the
    "Hook cancelled" regression record-session-end.sh's own header
    documents.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    session_end_entries = [
        entry
        for group in settings.get("hooks", {}).get("SessionEnd", [])
        if isinstance(group, dict)
        for entry in group.get("hooks", [])
        if isinstance(entry, dict)
    ]
    matching_entries = [
        entry
        for entry in session_end_entries
        if entry.get("command", "").endswith("record-session-end.sh")
    ]
    assert matching_entries, (
        "no SessionEnd hook entry with a command ending in "
        f"'record-session-end.sh' found in {_SETTINGS_PATH.relative_to(_REPO_ROOT)}"
    )
    timeout = matching_entries[0].get("timeout")
    # bool is an int subclass in Python, so a corrupted "timeout": true would
    # otherwise pass a bare isinstance(x, int) check.
    assert isinstance(timeout, int) and not isinstance(timeout, bool), (
        f"record-session-end.sh's SessionEnd 'timeout' is not an int: {timeout!r}"
    )
    # Floor-and-ceiling range, not an exact `== 10` match, so a legitimate
    # future retune isn't a test edit.
    assert 10 <= timeout <= 60, (
        f"record-session-end.sh's SessionEnd 'timeout' {timeout} is outside "
        f"the [10, 60] range -- 60 is the documented per-hook ceiling "
        f"(code.claude.com/docs/en/hooks)"
    )


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


def test_architect_consult_deny_message_points_at_a_live_skill_section() -> None:
    """Pins that the skill still has a heading matching the section name
    the hook file references.

    - Hook-side check: an unscoped substring scan, so it would still
      pass if the phrase survived only in a stale comment after the
      deny message itself dropped it.
    - That drift is caught by the sibling behavioral test,
      `test_require_architect_consult.py::test_deny_message_contents`,
      which executes the hook and asserts on the real emitted string.
    - Skill-side check: full-line match anchored to the exact `###`
      heading text and level — a substring match would silently accept
      a heading rename.
    - Does not prove the routing rule is followed, only that the names
      still agree.
    - Does not distinguish a real heading from one inside a code-fence
      example.
    - Does not match the closing-hash ATX form (`### heading ###`), a
      CommonMark-legal variant this corpus does not currently use.
    """
    hook_file = _MAIN_HOOKS_DIR / "require-architect-consult.sh"
    skill_file = _SKILLS_DIR / "code-review" / "SKILL.md"
    assert hook_file.is_file(), f"missing hook file: {hook_file}"
    assert skill_file.is_file(), f"missing skill file: {skill_file}"

    hook_text = hook_file.read_text(encoding="utf-8")
    assert "Round-cap architect consult" in hook_text, (
        "require-architect-consult.sh's deny message no longer names the "
        "'Round-cap architect consult' section it points a denied spawn at"
    )

    skill_text = skill_file.read_text(encoding="utf-8")
    heading_pattern = re.compile(r"^[ ]{0,3}### Round-cap architect consult\s*$", re.MULTILINE)
    assert heading_pattern.search(skill_text), (
        "code-review/SKILL.md no longer has a '### Round-cap architect "
        "consult' heading (exact title, level 3), but "
        "require-architect-consult.sh's deny message still points a "
        "denied reviewer spawn at it"
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


def _pretooluse_matcher_groups_for(hook: Path) -> list[str]:
    """The `matcher` string of every PreToolUse group containing an entry
    wired to `hook` -- one entry per matcher group, using the same
    exact-last-shell-word match _pretooluse_entries_for uses."""
    if hook.parent == _MAIN_HOOKS_DIR:
        config_path = _SETTINGS_PATH
        expected_invocation = f"~/.claude/hooks/{hook.name}"
    else:
        config_path = hook.parent / "hooks.json"
        expected_invocation = f"${{CLAUDE_PLUGIN_ROOT}}/hooks/{hook.name}"
    config = json.loads(config_path.read_text())
    matchers: list[str] = []
    for group in config.get("hooks", {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []):
            if not isinstance(entry, dict):
                continue
            command = entry.get("command", "")
            tokens = shlex.split(command)
            if tokens and tokens[-1] == expected_invocation:
                matchers.append(group.get("matcher", ""))
    return matchers


# enforce-marker-script-shape.sh declares, in its own header comment, that
# closing its bypass class requires being wired on both the Bash and an
# Edit|Write|MultiEdit PreToolUse matcher -- gating only the shell leaves a
# direct file write as an open path to the same state.
# test_gate_hook_registered_in_pretooluse_matcher above only proves "wired
# into at least one matcher," not both surfaces.
_DUAL_SURFACE_WRITE_GATE_HOOKS: tuple[str, ...] = ("enforce-marker-script-shape.sh",)


def _matchers_spanning_edit_write_multiedit(matchers: list[str]) -> list[str]:
    """Return the PreToolUse matchers that name Edit, Write and MultiEdit together."""
    return [matcher for matcher in matchers if {"Edit", "Write", "MultiEdit"} <= set(matcher.split("|"))]


@pytest.mark.parametrize("hook_name", _DUAL_SURFACE_WRITE_GATE_HOOKS)
def test_write_gate_hook_wired_on_both_bash_and_edit_write_multiedit(hook_name: str) -> None:
    """Both dual-surface write-gate hooks must carry a bare `Bash` PreToolUse
    entry AND an Edit|Write|MultiEdit-shaped one -- a settings.json edit
    that drops either surface would still pass
    test_gate_hook_registered_in_pretooluse_matcher (which only checks "at
    least one") while silently reopening the direct-file-write or
    shell-command bypass each hook's header names as the reason the second
    surface exists.
    """
    hook = _MAIN_HOOKS_DIR / hook_name
    matchers = _pretooluse_matcher_groups_for(hook)
    assert "Bash" in matchers, f"{hook_name}: not wired on a bare 'Bash' PreToolUse matcher"
    assert _matchers_spanning_edit_write_multiedit(matchers), (
        f"{hook_name}: no PreToolUse matcher spanning Edit|Write|MultiEdit "
        f"found -- closing this hook's file-write bypass class requires "
        f"both surfaces"
    )


def test_ask_review_permissions_wired_on_edit_write_multiedit() -> None:
    """settings.json must register ask-review-permissions.sh on a PreToolUse
    matcher spanning Edit, Write and MultiEdit -- the hook is `informational`,
    so the gate-only registration check does not cover it. The hook and the
    `permissions.ask` entry (pinned by
    `test_settings_file_edit_ask_rule_stays_declared_in_stow_source_settings`)
    are independent layers. Only the hook is known to cover MultiEdit.
    """
    matchers = _pretooluse_matcher_groups_for(_MAIN_HOOKS_DIR / "ask-review-permissions.sh")
    assert _matchers_spanning_edit_write_multiedit(matchers), (
        f"ask-review-permissions.sh: no PreToolUse matcher spanning "
        f"Edit|Write|MultiEdit in settings.json (found {matchers!r})"
    )


_SETTINGS_FILE_ASK_RULE = "Edit(//**/.claude/settings*.json)"


def test_settings_file_edit_ask_rule_stays_declared_in_stow_source_settings() -> None:
    """The declared `permissions.ask` entry backing the settings-file-edit ask rule.

    This proves the *declared* config state — `Edit(//**/.claude/settings*.json)`
    is in `permissions.ask` — not that the harness actually asks on the edit at
    runtime. Live-session observations and their limits are recorded in
    `docs/security-hardening.md`, in the section titled "WebFetch domain
    allowlisting — considered and rejected"; this test only pins the
    declaration so a future edit can't drop it silently.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    ask_rules = settings.get("permissions", {}).get("ask", [])
    assert isinstance(ask_rules, list) and _SETTINGS_FILE_ASK_RULE in ask_rules, (
        f"'{_SETTINGS_FILE_ASK_RULE}' missing from permissions.ask in "
        f"{_SETTINGS_PATH.relative_to(_REPO_ROOT)} — settings-file edits would no longer ask "
        f"through the harness's own rule matching"
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


def test_attribution_sessionurl_stays_false_in_stow_source_settings() -> None:
    """The declared config-value backing the session-URL trailer suppression.

    This proves the *declared* config state — `attribution.sessionUrl` is
    `false` in the stow-source settings file — not that the harness actually
    suppresses the trailer at runtime. That live-session verification lives
    outside pytest (see docs/design-decisions.md §63); this test only pins
    the declaration so a future edit can't drop it silently.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    assert settings.get("attribution", {}).get("sessionUrl") is False, (
        f"attribution.sessionUrl is not `false` in "
        f"{_SETTINGS_PATH.relative_to(_REPO_ROOT)} — the Claude-Session URL "
        f"trailer is no longer suppressed on this machine's commits"
    )


def test_attribution_sessionurl_stays_false_in_repo_local_settings() -> None:
    """The repo-local sibling of
    `test_attribution_sessionurl_stays_false_in_stow_source_settings`.

    This proves the *declared* config state only, in the repo-root
    `.claude/settings.json` that a non-stow clone or cloud container also
    sees. Whether project scope actually honors `attribution` there is
    unverified (docs/design-decisions.md §63); this test only pins the
    declaration.
    """
    settings = json.loads(_REPO_LOCAL_SETTINGS_PATH.read_text())
    assert settings.get("attribution", {}).get("sessionUrl") is False, (
        f"attribution.sessionUrl is not `false` in "
        f"{_REPO_LOCAL_SETTINGS_PATH.relative_to(_REPO_ROOT)} — "
        f"the Claude-Session URL trailer is no longer suppressed for clones "
        f"without the stow package"
    )


@pytest.mark.parametrize(
    "path",
    _ATTRIBUTION_SETTINGS_PATHS,
    ids=[str(p.relative_to(_REPO_ROOT)) for p in _ATTRIBUTION_SETTINGS_PATHS],
)
def test_attribution_commit_and_pr_stay_unset_in_both_settings(path: Path) -> None:
    """Guards against reintroducing the falsy-empty-string trap §63 names.

    `attribution.commit: ""` is not a no-op. An empty string is falsy, so
    the harness treats it the same as unset and ships the session trailer
    as the sole trailer instead of suppressing it. This is the exact
    regression anthropics/claude-code#77830's reporter hit. Pinning that
    `commit`/`pr` stay absent from `attribution` in both settings files
    catches a well-intentioned future edit that adds one, believing it
    also suppresses a trailer.
    """
    settings = json.loads(path.read_text())
    attribution_keys = set(settings.get("attribution", {}))
    assert attribution_keys <= {"sessionUrl"}, (
        f"attribution has key(s) {attribution_keys - {'sessionUrl'}} "
        f"beyond `sessionUrl` in {path.relative_to(_REPO_ROOT)} — "
        f"`commit`/`pr` must stay unset, since an empty `commit` makes "
        f"the session trailer the sole trailer instead of suppressing "
        f"it (docs/design-decisions.md §63)"
    )


def test_tree_settings_paths_include_the_known_settings_files() -> None:
    """Keeps the wildcard test from passing vacuously when one of the two
    `_ATTRIBUTION_SETTINGS_PATHS` files drops out of `_tree_settings_paths()`
    discovery. It does not catch a settings file appearing somewhere
    `_tree_settings_paths()` doesn't glob into -- see that function's own
    docstring for the discovery limits.
    """
    missing_paths = [path for path in _ATTRIBUTION_SETTINGS_PATHS if path not in _TREE_SETTINGS_PATHS]
    assert not missing_paths, (
        f"known settings file(s) {[str(path.relative_to(_REPO_ROOT)) for path in missing_paths]} "
        f"dropped out of _TREE_SETTINGS_PATHS discovery, so the wildcard test "
        f"would otherwise skip them silently"
    )


@pytest.mark.parametrize(
    "path",
    _TREE_SETTINGS_PATHS,
    ids=[str(p.relative_to(_REPO_ROOT)) for p in _TREE_SETTINGS_PATHS],
)
def test_permissions_allow_stays_wildcard_free_in_tree_settings(path: Path) -> None:
    """Pins the no-wildcards rule in `permissions.allow`.

    A wildcard widens an allow rule so that it accepts injected flags,
    chained commands and shell expansion.

    Only `permissions.allow` is checked:
    - `permissions.deny` legitimately carries wildcards, e.g. `Bash(sudo *)`.
    - `permissions.ask` rules carry globs too, as the shipped settings-file
      entry does.

    Checks the literal `*` only. `*` is the only glob metacharacter this
    repo's own docs name (`claude/.claude/rules/settings-json-conventions.md`,
    `claude-skills/skills/review-permissions/SKILL.md`). Whether Claude
    Code's own permission-rule grammar gives `[...]` or `?` glob meaning is
    unverified here, so widening this check to them would encode an
    unverified assumption rather than close a confirmed gap.
    """
    allow = json.loads(path.read_text()).get("permissions", {}).get("allow", [])
    non_string_entries = [entry for entry in allow if not isinstance(entry, str)]
    assert not non_string_entries, (
        f"permissions.allow in {path.relative_to(_REPO_ROOT)} has an allow entry "
        f"that is not a string: {non_string_entries}"
    )
    wildcard_entries = [entry for entry in allow if "*" in entry]
    assert not wildcard_entries, (
        f"wildcard entries in permissions.allow of {path.relative_to(_REPO_ROOT)}: "
        f"{wildcard_entries} — use exact-match rules "
        f"(claude/.claude/rules/settings-json-conventions.md)"
    )


def test_schedulewakeup_stays_denied_in_settings() -> None:
    """The declared config-value backing the ScheduleWakeup deny.

    This proves the *declared* config state — `"ScheduleWakeup"` is present
    in `permissions.deny` — not that the harness actually removes the tool
    from context at runtime. That live-session verification lives outside
    pytest (see `.claude/plans/prevent-non-loop-schedulewakeup-calls.md`'s
    pre-implementation gate); this test only pins the declaration so a
    future edit can't drop it silently. A membership check on the exact
    bare string also catches a later weakening into the parenthesized
    `"ScheduleWakeup(*)"` form, which leaves the tool visible in context.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    assert "ScheduleWakeup" in settings.get("permissions", {}).get("deny", []), (
        f"'ScheduleWakeup' missing from permissions.deny in "
        f"{_SETTINGS_PATH.name} — out-of-/loop wakeup scheduling is no "
        f"longer prevented"
    )


def test_schedulewakeup_adjacent_tools_stay_allowed_in_settings() -> None:
    """The allow-path sibling to `test_schedulewakeup_stays_denied_in_settings`.

    Guards against a future edit silently widening `permissions.deny` to
    swallow tools `.claude/plans/prevent-non-loop-schedulewakeup-calls.md`'s
    Context section requires to stay available, each on its own basis:

    - `CronCreate` is named in `docs/design-decisions.md` §49's
      Blast-radius section as unaffected by the deny.
    - `ListAgents` and `TaskOutput` are not argued there — they're guarded
      because the plan's pre-implementation gate (Verification step 1
      check 5) required them to remain available, and §49's Revisit list
      separately names them as a substitution-risk channel to watch, not
      as confirmed-unaffected.
    - `Agent` is guarded because the plan's Context section names it as
      the dispatch the misfire follows, and it's also one of the three
      tools the plan's pre-implementation gate (Verification step 1
      check 5) required to remain available.

    `CronCreate`'s presence in this list tracks §49's current
    Accepted-residual-risk stance (the substitution channel is unguarded,
    not unformable) — a future PR that deliberately closes that gap via
    this same bare-tool-name-deny mechanism removes it from this list on
    purpose, not as an accidental widening this test should catch.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    deny = settings.get("permissions", {}).get("deny", [])
    documented_unaffected = (
        "design-decisions.md §49's Blast-radius section claims this tool "
        "stays unaffected by the ScheduleWakeup deny"
    )
    gate_required_available = (
        "the plan's pre-implementation gate (Verification step 1 check 5) "
        "requires this tool to remain available, and design-decisions.md "
        "§49's Revisit list separately names it as a substitution-risk "
        "channel to watch, not as confirmed-unaffected"
    )
    dispatch_trigger = (
        "the plan's Context section names it as the dispatch the "
        "ScheduleWakeup misfire follows"
    )
    rationale = {
        "CronCreate": documented_unaffected,
        "ListAgents": gate_required_available,
        "TaskOutput": gate_required_available,
        "Agent": dispatch_trigger,
    }
    for tool_name, why in rationale.items():
        assert tool_name not in deny, (
            f"'{tool_name}' present in permissions.deny in "
            f"{_SETTINGS_PATH.name} — {why}"
        )


def test_syncclaudeaiskills_stays_disabled_in_stow_source_settings() -> None:
    """The declared config-value backing the claude.ai skill-sync default flip.

    This proves the *declared* config state — `syncClaudeAiSkills` is
    `false` in the stow-source settings file — not that the harness
    actually stops syncing at runtime. That live-session verification
    lives outside pytest (see
    docs/design-decisions/claude-ai-skill-sync-disabled-by-default.md);
    this test only pins the declaration so a future edit can't drop it
    silently.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    assert settings.get("syncClaudeAiSkills") is False, (
        f"syncClaudeAiSkills is not `false` in "
        f"{_SETTINGS_PATH.relative_to(_REPO_ROOT)} — claude.ai skill sync "
        f"is no longer disabled by default"
    )


def test_promptcachettl_stays_unset_in_stow_source_settings() -> None:
    """The declared config-value backing the main-bucket prompt-cache TTL verdict.

    This proves the *declared* config state — `promptCacheTtl` is absent from
    the stow-source settings file — not that the harness actually honors the
    key's absence at runtime. That live-session verification is not checkable
    pre-merge (see
    docs/design-decisions/main-bucket-prompt-cache-ttl-unset.md); this test
    only pins the declaration so a future edit can't reintroduce it silently.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    assert "promptCacheTtl" not in settings, (
        f"promptCacheTtl is present in "
        f"{_SETTINGS_PATH.relative_to(_REPO_ROOT)} — the main-conversation "
        f"prompt-cache bucket is no longer left at the vendor default"
    )


def test_subagentpromptcachettl_stays_unset_in_stow_source_settings() -> None:
    """Sibling to `test_promptcachettl_stays_unset_in_stow_source_settings`,
    pinning that the *subagent* bucket's own key also stays unset.

    `subagentPromptCacheTtl` stays deliberately unset: the vendor's TTL
    precedence chain ranks a bucket's own setting above per-agent
    `experimental.cacheTtl` frontmatter, so setting this key would silently
    outrank and disable that per-agent lever for every subagent dispatch.
    See docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md.
    """
    settings = json.loads(_SETTINGS_PATH.read_text())
    assert "subagentPromptCacheTtl" not in settings, (
        f"subagentPromptCacheTtl is present in "
        f"{_SETTINGS_PATH.relative_to(_REPO_ROOT)} — this outranks and "
        f"disables per-agent experimental.cacheTtl frontmatter for every "
        f"subagent dispatch, which was deliberately left available"
    )


def test_promptcachettl_stays_unset_in_repo_local_settings() -> None:
    """Guards against mirroring the machine-scoped `promptCacheTtl` verdict
    into this repo's contributor-shared settings; see
    docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md's opening
    paragraph for why this isn't a universal default like
    `attribution.sessionUrl`.
    """
    settings = json.loads(_REPO_LOCAL_SETTINGS_PATH.read_text())
    assert "promptCacheTtl" not in settings, (
        f"promptCacheTtl is present in "
        f"{_REPO_LOCAL_SETTINGS_PATH.relative_to(_REPO_ROOT)} — this "
        f"mirrors a single-machine, single-corpus verdict onto every "
        f"contributor of this repo, which "
        f"docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md's "
        f"opening paragraph argues against"
    )


# Gates whose headers declare intentional unconditional (no-`if`) PreToolUse
# dispatch: each self-filters on its own tool_input rather than relying on
# a settings.json `if`-condition glob for coverage. Unlike _EXPLICIT_GATES
# above (a static naming exception), this set is expected to grow — the
# cross-hook `if`-dispatch audit tracked in
# https://github.com/jcdendrite/claude-config/issues/774 is expected to add
# an entry here each time it lands another hook's own dispatch fix.
_SELF_FILTERING_BASH_GATES: tuple[str, ...] = (
    "block-gh-pr-merge.sh",
    "require-respond-pr.sh",
    "deny-private-project-refs.sh",
    "deny-pii-in-commits.sh",
    "require-ready-for-review.sh",
    "enforce-marker-script-shape.sh",
)


@pytest.mark.parametrize("hook_name", _SELF_FILTERING_BASH_GATES)
def test_self_filtering_bash_gate_has_no_if_matcher(hook_name: str) -> None:
    """Each self-filtering gate's PreToolUse entries carry no `if` key.

    This proves the *declared* config state: settings.json wires the hook
    with no `if`-condition. It does not prove the harness actually invokes
    the hook for every wrapped/indirected shape at runtime. A post-merge
    smoke check (attempt a gated command from a fresh session and confirm
    the deny fires) covers that runtime-honored gap. Mirrors
    test_plan_mode_entry_paths_stay_closed_in_settings's same
    declared-vs-honored distinction.
    """
    hook = _MAIN_HOOKS_DIR / hook_name
    entries = _pretooluse_entries_for(hook)
    assert entries, f"{hook_name}: expected at least one PreToolUse entry"
    for entry in entries:
        assert "if" not in entry, (
            f"{hook_name}: PreToolUse entry carries an 'if' key "
            f"({entry.get('if')!r}) — this gate's header declares "
            f"unconditional dispatch"
        )


# ------------------------------------------------------------------ #
# Layer 1 — Static checks                                            #
# ------------------------------------------------------------------ #

# Filename -> structural reason a bare-`jq` call at this hook stays
# unwrapped rather than converted to a _lib_* wrapper. A dict, not a
# frozenset/tuple, so the reason travels with the entry.
# Each test below asserts a listed hook still violates, so a stale entry
# fails loudly instead of outliving its reason.
_BARE_JQ_EXEMPT_HOOKS: dict[str, str] = {
    "check-branch-divergence.sh": (
        "does not source _lib.sh; carries its own TIMEOUT_CMD probe applied "
        "to the network call only"
    ),
}

# Filename -> structural reason an inline, hand-rolled command-matcher regex
# at this hook stays unconverted rather than routed through
# _lib_command_invokes_tool_subcmd / _lib_fragment_invokes_git.
_INLINE_COMMAND_MATCHER_EXEMPT_HOOKS: dict[str, str] = {
    "enforce-marker-script-shape.sh": (
        "raw-text arm OR-combined with _lib_command_invokes_tool_subcmd per "
        "that hook's own dual-detection design"
    ),
    "require-ready-for-review.sh": (
        "whole-fragment scan retained so a bash -c/eval wrapper stays "
        "covered, matching the git arm above. Cost: a flag interposed "
        "before the subcommand is missed, e.g. `gh --repo o/r pr create` "
        "and `gh --repo o/r pr ready` (adjacency-only). Tracked by GH-897 "
        "(covers this hook and require-respond-pr.sh together)."
    ),
}


def _strip_comment(line: str) -> str:
    """Strip a full-line or same-line trailing `#` comment from a shell
    line, for the three detectors below.

    Naive substring split on " #", not shell-aware.

    Blind spot: no line in the current hook set has a literal " #" inside
    a string. A future line that legitimately needs one would have its
    trailing content truncated -- a false negative in all three detectors
    below, not just a missed comment.
    """
    if line.lstrip().startswith("#"):
        return ""
    return line.split(" #", 1)[0]


def test_strip_comment_full_line() -> None:
    """A line whose first non-whitespace character is `#` strips to empty,
    regardless of leading indentation."""
    assert _strip_comment("  # a comment") == ""


def test_strip_comment_same_line_trailing() -> None:
    """A code line with a trailing ` #comment` keeps only the code prefix,
    up to but not including the space that starts the `" #"` separator."""
    assert _strip_comment("jq -n '{}' # inline note") == "jq -n '{}'"


def test_strip_comment_no_comment() -> None:
    """A code line with no `#` anywhere returns unchanged."""
    assert _strip_comment("jq -n '{}'") == "jq -n '{}'"


def test_strip_comment_string_interior_hash_is_a_known_blind_spot() -> None:
    """Pins the naive `" #"`-split's documented limitation as executable: a
    literal " #" inside a quoted string is indistinguishable from a real
    trailing comment, so the string's own content past that point is
    dropped rather than preserved. Fails loudly if this ever changes, since
    the three detectors above rely on this exact truncation behavior.
    """
    assert _strip_comment("grep -qE 'foo #bar\\s+baz'") == "grep -qE 'foo"


# jq in command position: immediately after start-of-line, `|`, `;`, `&`,
# `(`, `)` (a `case` pattern's close, e.g. `*) jq ...;;`), `$(`, `{`, or a
# then/else/elif/do keyword. Plus a hand-rolled `timeout [N] jq`, which
# duplicates rather than reuses _lib_jq's own timeout.
_BARE_JQ_COMMAND_POSITION_RE = re.compile(
    r"(?:^|[|;&()]|\$\(|\{|\b(?:then|else|elif|do))\s*jq\b"
)
_BARE_JQ_TIMEOUT_WRAPPED_RE = re.compile(r"\btimeout\s+(?:[0-9]+\s+)?jq\b")


def _bare_jq_hits(hook: Path) -> list[str]:
    """Return non-comment lines invoking `jq` in command position outside a
    _lib_* wrapper (_lib_jq, _lib_capped_for N jq).

    Blind spot: only catches the anchor set documented on
    _BARE_JQ_COMMAND_POSITION_RE above, plus a hand-rolled `timeout [N] jq`.
    - Misses `xargs jq`.
    - Misses `command jq` (bypasses a same-named function without
      `command -v`).
    - Misses jq reached through a variable-held command name.
    """
    hits = []
    for line in hook.read_text().splitlines():
        code = _strip_comment(line)
        if not code.strip():
            continue
        if _BARE_JQ_COMMAND_POSITION_RE.search(code) or _BARE_JQ_TIMEOUT_WRAPPED_RE.search(code):
            hits.append(line.strip())
    return hits


@pytest.mark.parametrize("hook", _MAIN_HOOKS, ids=[h.name for h in _MAIN_HOOKS])
def test_no_bare_jq_outside_lib_wrapper(hook: Path) -> None:
    """Every `jq` invocation in claude/.claude/hooks/*.sh goes through a
    _lib_* wrapper, so the shared timeout backstop covers every call. See
    _bare_jq_hits for the detector's blind spot.
    """
    hits = _bare_jq_hits(hook)
    if hook.name in _BARE_JQ_EXEMPT_HOOKS:
        assert hits, (
            f"{hook.name} is listed in _BARE_JQ_EXEMPT_HOOKS "
            f"({_BARE_JQ_EXEMPT_HOOKS[hook.name]!r}) but no bare-jq call "
            "remains — remove the stale allowlist entry"
        )
        pytest.skip(_BARE_JQ_EXEMPT_HOOKS[hook.name])
    assert not hits, (
        f"{hook.name}: bare `jq` call(s) outside a _lib_* wrapper:\n" + "\n".join(hits)
    )


def test_bare_jq_detector_flags_known_anchor_shapes(tmp_path: Path) -> None:
    """Meta-test for _bare_jq_hits's anchor set: one fixture line per anchor
    position _BARE_JQ_COMMAND_POSITION_RE documents (start-of-line, `|`,
    `;`, `&`, `(`, `)`, `$(`, `{`, and each of the then/else/elif/do
    keywords), plus the separate hand-rolled `timeout N jq` pattern. Each
    fixture line must be flagged before relying on the detector as a
    regression guard across 40+ hook files. Two negative-control lines
    (compliant `_lib_jq` and `_lib_capped_for N jq` calls) must stay
    unflagged, proving the detector distinguishes a wrapped call from a
    bare one rather than matching on the bare `jq` substring alone.
    """
    fixture = tmp_path / "fixture.sh"
    fixture.write_text(
        "#!/bin/bash\n"
        "jq -n '{}'\n"
        "echo x | jq -n '{}'\n"
        "true; jq -n '{}'\n"
        "false & jq -n '{}'\n"
        "(jq -n '{}')\n"
        "x=$(jq -n '{}')\n"
        "{ jq -n '{}' ; }\n"
        "if true; then jq -n '{}' ; fi\n"
        "if false; then :; else jq -n '{}'; fi\n"
        "if false; then :; elif jq -e . f >/dev/null; then :; fi\n"
        "for i in 1; do jq -n '{}'; done\n"
        "case x in *) jq -n '{}' ;; esac\n"
        "timeout 5 jq -n '{}'\n"
        "_lib_jq -n '{}'\n"
        "x=$(_lib_capped_for 2 jq -s \"$FILTER\")\n"
    )
    hits = _bare_jq_hits(fixture)
    assert len(hits) == 13, f"expected exactly the 13 positive fixture lines flagged, got {hits!r}"


def test_bare_jq_xargs_and_command_forms_are_known_blind_spots(tmp_path: Path) -> None:
    """Pins _bare_jq_hits's documented blind spot as executable: `xargs jq`
    and `command jq` are indistinguishable from a properly wrapped call to
    the anchor-based regex, so both stay silently unflagged.
    """
    fixture = tmp_path / "fixture.sh"
    fixture.write_text(
        "#!/bin/bash\n"
        "find . -name '*.json' | xargs jq '.'\n"
        "command jq -n '{}'\n"
    )
    assert _bare_jq_hits(fixture) == []


# grep-family invocation: -q/-c/-l among the flags, broad enough to catch
# `-qE`, `-Eq`, `-cE`, etc.
_GREP_FAMILY_RE = re.compile(r"grep\s+-[a-zA-Z]*[qcl][a-zA-Z]*\b")
# A tool token immediately followed by a whitespace-class atom -- the shape
# is hand-rolled command matching either way, independent of which form is
# used below.
# - Whitespace atom: accepts either GNU `\s` or POSIX `[[:space:]]`.
# - `marker\\?\.sh` matches both the escaped- and unescaped-dot spellings of
#   `marker.sh`.
# - A leading `\b` guards `git`/`gh` against matching as a substring of an
#   unrelated word (`digit`, `high`).
# - `marker\\?\.sh` has no such guard -- a substring collision (e.g.
#   `bookmarker.sh`) is accepted, since a collision is far less likely
#   against this multi-character coined identifier.
_TOOL_TOKEN_WHITESPACE_ATOM_RE = re.compile(r"(?:\bgit|\bgh|marker\\?\.sh)(?:\\s|\[\[:space:\]\])")


def _inline_command_matcher_hits(hook: Path) -> list[str]:
    """Return non-comment lines invoking a grep-family command whose literal
    pattern argument carries a tool token (`git`, `gh`, `marker.sh`)
    immediately followed by a whitespace-class atom -- the shape
    `_lib_command_invokes_tool_subcmd` and `_lib_fragment_invokes_git` exist
    to replace.

    Blind spot:
    - Misses a pattern hoisted into a variable before being passed to grep.
    - Misses a grep-family call piped through `[ -n ... ]` rather than
      using `-q`/`-c`/`-l` directly.
    - Misses Bash's native `[[ ... =~ ... ]]` entirely -- a different
      mechanism for the same hand-rolled-matcher shape, not a grep variant
      (e.g. require-respond-pr.sh's `PATTERN_*` family,
      require-worktree-for-git-writes.sh's git-token match).
    """
    hits = []
    for line in hook.read_text().splitlines():
        code = _strip_comment(line)
        if not code.strip():
            continue
        if _GREP_FAMILY_RE.search(code) and _TOOL_TOKEN_WHITESPACE_ATOM_RE.search(code):
            hits.append(line.strip())
    return hits


@pytest.mark.parametrize("hook", _MAIN_HOOKS, ids=[h.name for h in _MAIN_HOOKS])
def test_no_inline_command_matcher_regex(hook: Path) -> None:
    """Every grep-family command match in claude/.claude/hooks/*.sh resolves
    a tool subcommand through the shared _lib_command_invokes_tool_subcmd /
    _lib_fragment_invokes_git helpers rather than a hand-rolled literal
    pattern. See _inline_command_matcher_hits for the detector's blind spot.
    """
    hits = _inline_command_matcher_hits(hook)
    if hook.name in _INLINE_COMMAND_MATCHER_EXEMPT_HOOKS:
        assert hits, (
            f"{hook.name} is listed in _INLINE_COMMAND_MATCHER_EXEMPT_HOOKS "
            f"({_INLINE_COMMAND_MATCHER_EXEMPT_HOOKS[hook.name]!r}) but no "
            "inline command-matcher regex remains — remove the stale "
            "allowlist entry"
        )
        pytest.skip(_INLINE_COMMAND_MATCHER_EXEMPT_HOOKS[hook.name])
    assert not hits, (
        f"{hook.name}: hand-rolled command-matcher regex outside the shared "
        f"helpers:\n" + "\n".join(hits)
    )


def test_inline_command_matcher_detector_flags_known_variants(tmp_path: Path) -> None:
    """Meta-test for _inline_command_matcher_hits: every tool-token spelling
    (`git`, `gh`, escaped- and unescaped-dot `marker.sh`), whitespace-atom
    form (GNU `\\s`, POSIX `[[:space:]]`), and grep flag ordering (`-q`,
    `-Eq`, `-cE`, `-lE`) the detector claims to catch must actually be
    flagged before relying on it as a regression guard across 40+ hook
    files. Two negative-control lines must stay unflagged:
    - A tool token present but not immediately followed by a whitespace-
      class atom, proving atom-adjacency drives the match, not token
      presence alone.
    - A tool token embedded as a substring of an unrelated word immediately
      followed by an atom, one per `\\b`-guarded alternative (`git`, `gh`),
      proving the match requires a standalone token, not a substring.
    """
    fixture = tmp_path / "fixture.sh"
    fixture.write_text(
        "#!/bin/bash\n"
        "grep -q 'git\\s'\n"
        "grep -Eq 'gh[[:space:]]'\n"
        "grep -cE 'marker.sh\\s'\n"
        "grep -lE 'marker\\.sh[[:space:]]'\n"
        "grep -q 'git status'\n"
        "grep -qE 'digit[[:space:]]count'\n"
        "grep -qE 'high[[:space:]]five'\n"
    )
    hits = _inline_command_matcher_hits(fixture)
    assert len(hits) == 4, f"expected exactly the 4 positive fixture lines flagged, got {hits!r}"


def test_inline_command_matcher_variable_hoisted_pattern_is_a_known_blind_spot(tmp_path: Path) -> None:
    """Pins _inline_command_matcher_hits's documented blind spot as
    executable: a tool-token pattern hoisted into a variable before being
    passed to grep is indistinguishable from an unrelated pattern
    argument on the grep call's own source line, so it stays silently
    unflagged.
    """
    fixture = tmp_path / "fixture.sh"
    fixture.write_text(
        "#!/bin/bash\n"
        "pattern='git\\s'\n"
        'grep -q "$pattern"\n'
    )
    assert _inline_command_matcher_hits(fixture) == []


def _live_backslash_s_hits(hook: Path) -> list[str]:
    """Return non-comment lines containing a literal backslash-`s`, GNU
    grep's non-POSIX whitespace-class extension -- a POSIX-strict grep reads
    it as a literal `s`, silently turning the enclosing match into a
    fail-open.
    """
    hits = []
    for line in hook.read_text().splitlines():
        code = _strip_comment(line)
        if "\\s" in code:
            hits.append(line.strip())
    return hits


@pytest.mark.parametrize(
    "hook",
    ALL_HOOKS_AND_LIBS,
    ids=[str(h.relative_to(_REPO_ROOT)) for h in ALL_HOOKS_AND_LIBS],
)
def test_no_gnu_backslash_s_regex_extension(hook: Path) -> None:
    """No hook regex -- claude/.claude/hooks/*.sh, plugins/*/hooks/*.sh, or
    either directory's _lib.sh -- uses GNU grep's `\\s` extension -- not
    POSIX ERE, and a POSIX-strict grep reads it as a literal `s`. Use
    `[[:space:]]` instead.

    Parametrized over ALL_HOOKS_AND_LIBS, not _MAIN_HOOKS like the sibling
    matcher tests below, because this detector has zero
    plugins/*/hooks/*.sh hits and needs no allowlist. The bare-jq and
    inline-matcher checks stay on _MAIN_HOOKS because each has unresolved
    plugin-side hits requiring adjudication first. See `_all_hook_files`
    for why only this detector also sweeps each directory's _lib.sh. Ids
    are repo-relative paths, not bare filenames, since every hooks
    directory has a same-named _lib.sh.

    See _live_backslash_s_hits for the detector's blind spot (a same-line
    trailing comment mentioning `\\s` in prose would also be stripped
    before the scan, same as the two detectors above).
    """
    hits = _live_backslash_s_hits(hook)
    assert not hits, (
        f"{hook.name}: literal backslash-`s` outside a comment -- convert "
        f"to POSIX [[:space:]]:\n" + "\n".join(hits)
    )


def test_backslash_s_detector_flags_code_not_comments(tmp_path: Path) -> None:
    """Meta-test for _live_backslash_s_hits: a bare `\\s` in code is flagged,
    and the same literal inside a full-line or same-line-trailing comment is
    not, since all three Layer-1 detectors share _strip_comment.
    """
    fixture = tmp_path / "fixture.sh"
    fixture.write_text(
        "#!/bin/bash\n"
        "grep -qE '(^|\\s)--dry-run(\\s|$)'\n"
        "# see docs/hooks.md: convert \\s to [[:space:]]\n"
        "jq -n '{}' # note: don't use \\s here\n"
    )
    hits = _live_backslash_s_hits(fixture)
    assert len(hits) == 1, f"expected exactly 1 fixture line flagged, got {hits!r}"


def test_all_hooks_and_libs_includes_every_lib_sh() -> None:
    """ALL_HOOKS_AND_LIBS must contain exactly one _lib.sh per hooks
    directory, on top of every entry in ALL_HOOKS. Fails if a hooks
    directory is added, renamed, or loses its _lib.sh, catching what
    would otherwise be a silent drop in the backslash-s detector's
    parametrized case count.
    """
    expected_lib_count = 1 + len(_PLUGIN_HOOKS_DIRS)  # main dir + each plugin
    lib_files = [h for h in ALL_HOOKS_AND_LIBS if h.name == "_lib.sh"]
    assert len(lib_files) == expected_lib_count, (
        f"expected {expected_lib_count} _lib.sh files in ALL_HOOKS_AND_LIBS "
        f"(one per hooks directory), found {len(lib_files)}: {lib_files!r}"
    )
    assert len(ALL_HOOKS_AND_LIBS) == len(ALL_HOOKS) + expected_lib_count


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
            'gate hooks must source _lib.sh via \'. "${0%/*}/_lib.sh"\''
        )
        assert emit_deny_line < lib_source_line, (
            f"{hook.name}: emit_deny() defined at line {emit_deny_line + 1} but "
            f"_lib.sh sourced at line {lib_source_line + 1} — "
            "emit_deny must be defined BEFORE sourcing _lib.sh"
        )


# ------------------------------------------------------------------ #
# Layer 1 — Threat-model tier header (docs/hooks.md § "Threat-model      #
# tiers")                                                                #
# ------------------------------------------------------------------ #

# First entry is the intent tier, the rest the canonical elevation order —
# also the vocabulary claude-hook-review/SKILL.md's tier-header paragraph's
# tier-shaped backtick spans must equal (test_skills.py derives its own copy
# from docs/hooks.md's definition bullets).
_THREAT_MODEL_TIERS: tuple[str, ...] = ("cooperative", "untrusted-input", "irreversible")

_TIER_LINE_FINDER_RE = re.compile(r"^#\s*tier-threat-model\b")
_TIER_LINE_PREFIX = "# tier-threat-model: "
_TIER_LINE_WELLFORMED_PREFIX_RE = re.compile(r"^# tier-threat-model: \S")


def _find_tier_threat_model_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Every loosely-matching '# tier-threat-model' comment line within the
    same first-_HEADER_SCAN_LINE_COUNT-lines window _hook_class scans,
    paired with its 0-based index into `lines` -- a '#' comment beginning
    'tier-threat-model' after optional whitespace, matched with re.match.
    Loose on purpose: a near-miss delimiter shape (no space after '#', a
    space before the colon) is still found here and left for
    _tier_grammar_violation to classify as malformed rather than silently
    read as absent. The window bounds the position check (index 2); the
    exactly-one count is file-wide, via _count_tier_threat_model_lines.
    """
    return [
        (i, line)
        for i, line in enumerate(lines[:_HEADER_SCAN_LINE_COUNT])
        if _TIER_LINE_FINDER_RE.match(line)
    ]


def _count_tier_threat_model_lines(lines: list[str]) -> int:
    """Number of loosely-matching '# tier-threat-model' lines anywhere in
    `lines`, so a stale second line below the header window is counted."""
    return sum(1 for line in lines if _TIER_LINE_FINDER_RE.match(line))


def _tier_grammar_violation(line: str) -> str | None:
    """Return a named reason the matched tier-threat-model `line` is
    malformed, or None if it's well-formed.

    Reasons, checked in this precedence order -- an input violating two at
    once is reported under the first one that applies, e.g. an unknown
    token that is also out of canonical order is reported "unknown tier":
    1. "bad separator" -- the line doesn't match '#', one space,
       'tier-threat-model:', exactly one space, then a non-whitespace
       character (the start of a token, well-formed or not), or a ',' inside
       the value isn't followed by exactly one space then a non-whitespace
       character.
    2. "no intent tier" / "unknown tier" -- a comma-space-separated token
       isn't a member of _THREAT_MODEL_TIERS ("unknown tier"), or the first
       token isn't 'cooperative' ("no intent tier").
    3. "duplicate token" -- the same token appears twice.
    4. "out of order" -- the elevation tokens (everything after the intent
       tier) aren't in _THREAT_MODEL_TIERS' own canonical order.
    """
    if not _TIER_LINE_WELLFORMED_PREFIX_RE.match(line):
        return "bad separator"
    value = line[len(_TIER_LINE_PREFIX):].rstrip()
    if re.search(r",(?!\ \S)", value):
        return "bad separator"
    tokens = value.split(", ")
    if any(token not in _THREAT_MODEL_TIERS for token in tokens):
        return "unknown tier"
    if tokens[0] != _THREAT_MODEL_TIERS[0]:
        return "no intent tier"
    if len(tokens) != len(set(tokens)):
        return "duplicate token"
    elevations = tokens[1:]
    canonical_elevations = [t for t in _THREAT_MODEL_TIERS[1:] if t in elevations]
    if elevations != canonical_elevations:
        return "out of order"
    return None


def _hook_tier_value(hook: Path) -> str | None:
    """This hook's tier-threat-model value (the text after the colon,
    trailing whitespace stripped), or None if no single well-formed line is
    present. Malformed-but-present reads the same as absent here -- the
    dedicated grammar tests name a malformed line's own reason separately;
    this accessor only feeds the table/floor comparisons below, which need
    a trustworthy value or nothing.
    """
    lines = hook.read_text().splitlines()
    matches = _find_tier_threat_model_lines(lines)
    if len(matches) != 1:
        return None
    _, line = matches[0]
    if _tier_grammar_violation(line) is not None:
        return None
    return line[len(_TIER_LINE_PREFIX):].rstrip()


def _tier_hook_key(hook: Path) -> str:
    """The docs/hooks.md table's own key for `hook`: a bare filename for a
    claude/.claude/hooks/ gate, a repo-relative POSIX path for a plugin
    gate -- matching that table's own per-row spelling for each (the
    table is the one docs/hooks.md section that also covers the 4 plugin
    gates)."""
    if hook.parent == _MAIN_HOOKS_DIR:
        return hook.name
    return hook.relative_to(_REPO_ROOT).as_posix()


def _markdown_section_text(doc_text: str, heading: str) -> str:
    """The body of the `## {heading}` section in `doc_text`: from just after
    the heading line to (not including) the next top-level `## ` heading,
    or end of file. A `###`-or-deeper subheading inside the section does
    not end it -- only another `## ` does. Returns "" if the heading is
    absent entirely, which downstream callers must treat as a named
    failure (every expected row reads as missing), not a silent pass.
    """
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(doc_text)
    return m.group(1) if m else ""


# Anchored on the two leading backticked cells, keyed on the first cell
# ending in `.sh` so a non-hook row can't be mistaken for one. Greedy `.+?`
# up to the final `|$` rather than str.split("|"), so a Why cell carrying an
# escaped pipe doesn't get mis-split.
_TIER_TABLE_ROW_RE = re.compile(
    r"^\|\s*`(?P<key>[^`]+\.sh)`\s*\|\s*`(?P<tier>[^`]*)`\s*\|\s*(?P<why>.*?)\s*\|\s*$"
)


def _parse_tier_table(doc_text: str) -> list[tuple[str, str, str]]:
    """Parse the '## Threat-model tiers' section's markdown table rows into
    (hook_key, tier_value, why) triples.

    Scoped to that one section (see _markdown_section_text) -- a
    same-shaped row elsewhere in the file (outside the section) is ignored,
    and a `###` subheading inside the section does not end it. The Why
    cell's prose is never read, only required non-empty; a row with an
    empty Why cell is rejected (excluded from the returned list), not kept
    with a blank rationale. Duplicate keys are returned as separate
    entries -- deduplication is _diff_table_against_headers's job, not this
    parser's.
    """
    section = _markdown_section_text(doc_text, "Threat-model tiers")
    rows: list[tuple[str, str, str]] = []
    for line in section.splitlines():
        m = _TIER_TABLE_ROW_RE.match(line)
        if not m:
            continue
        why = m.group("why").strip()
        if not why:
            continue
        rows.append((m.group("key"), m.group("tier"), why))
    return rows


def _diff_table_against_headers(
    table_rows: list[tuple[str, str, str]],
    gate_keys: set[str],
    header_values: dict[str, str | None],
) -> dict[str, object]:
    """Pure diff between the table's (key, tier) pairs and the gate hooks'
    own header values.

    - missing: gate keys with no table row at all.
    - unexpected: table rows whose key isn't a current gate hook.
    - duplicated: keys with more than one table row.
    - mismatched: {key: (table_tier, header_tier)} for a key present in
      both with a well-formed header value that disagrees with its row. A
      key with no header value yet (None -- covered by the presence test
      instead) is left out of mismatched rather than reported here.
    """
    table_keys = [key for key, _tier, _why in table_rows]
    table_key_set = set(table_keys)
    duplicated = {key for key in table_key_set if table_keys.count(key) > 1}
    missing = gate_keys - table_key_set
    unexpected = table_key_set - gate_keys
    mismatched: dict[str, tuple[str, str]] = {}
    for key, tier, _why in table_rows:
        if key not in gate_keys:
            continue
        header_value = header_values.get(key)
        if header_value is not None and header_value != tier:
            mismatched[key] = (tier, header_value)
    return {
        "missing": missing,
        "unexpected": unexpected,
        "duplicated": duplicated,
        "mismatched": mismatched,
    }


_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
_HOOKS_DOC_TEXT = _HOOKS_DOC.read_text()
_TIER_TABLE_ROWS = _parse_tier_table(_HOOKS_DOC_TEXT)
_GATE_HOOK_TIER_KEYS = {_tier_hook_key(h) for h in GATE_HOOKS}
_GATE_HOOK_TIER_VALUES = {_tier_hook_key(h): _hook_tier_value(h) for h in GATE_HOOKS}
_TIER_TABLE_DIFF = _diff_table_against_headers(
    _TIER_TABLE_ROWS, _GATE_HOOK_TIER_KEYS, _GATE_HOOK_TIER_VALUES
)


@pytest.mark.parametrize("hook", GATE_HOOKS, ids=[h.name for h in GATE_HOOKS])
def test_tier_threat_model_header_present_and_well_formed(hook: Path) -> None:
    """Every hook-class: gate hook declares exactly one well-formed
    '# tier-threat-model:' line at line 3 (index 2)."""
    lines = hook.read_text().splitlines()
    matches = _find_tier_threat_model_lines(lines)
    file_wide_count = _count_tier_threat_model_lines(lines)
    assert len(matches) == 1 and file_wide_count == 1, (
        f"{hook.name}: expected exactly one '# tier-threat-model:' line in "
        f"the whole file, found {file_wide_count} ({len(matches)} in the "
        f"header window) -- keep one at line 3, spelled "
        f"'# tier-threat-model: <tiers>'; copy the value from "
        f'docs/hooks.md § "Threat-model tiers"'
    )
    index, line = matches[0]
    assert index == 2, (
        f"{hook.name}: '# tier-threat-model:' line found at line "
        f"{index + 1}, must be at line 3"
    )
    violation = _tier_grammar_violation(line)
    assert violation is None, (
        f"{hook.name}: malformed tier-threat-model line ({violation}): {line!r}"
    )


@pytest.mark.parametrize("hook", ALL_HOOKS, ids=[h.name for h in ALL_HOOKS])
def test_tier_threat_model_grammar_when_present(hook: Path) -> None:
    """Grammar applies uniformly regardless of hook-class -- a voluntary
    tier line on a non-gate hook is validated too, so a future addition
    doesn't silently ship malformed."""
    lines = hook.read_text().splitlines()
    matches = _find_tier_threat_model_lines(lines)
    if not matches:
        pytest.skip("no tier-threat-model line present")
    for _, line in matches:
        violation = _tier_grammar_violation(line)
        assert violation is None, (
            f"{hook.name}: malformed tier-threat-model line ({violation}): {line!r}"
        )


def test_tier_table_keys_match_gate_hooks_exhaustive() -> None:
    """docs/hooks.md's Threat-model tiers table lists exactly the current
    GATE_HOOKS keys -- no missing gate, no row for a hook that isn't one,
    no duplicated row."""
    assert not _TIER_TABLE_DIFF["missing"], (
        f"gate hook(s) with no table row: {sorted(_TIER_TABLE_DIFF['missing'])}"
    )
    assert not _TIER_TABLE_DIFF["unexpected"], (
        f"table row(s) for a hook that isn't a current gate: "
        f"{sorted(_TIER_TABLE_DIFF['unexpected'])}"
    )
    assert not _TIER_TABLE_DIFF["duplicated"], (
        f"duplicated table row(s): {sorted(_TIER_TABLE_DIFF['duplicated'])}"
    )


@pytest.mark.parametrize("hook", GATE_HOOKS, ids=[h.name for h in GATE_HOOKS])
def test_gate_header_tier_matches_table_row(hook: Path) -> None:
    """Each gate's own header value agrees with its docs/hooks.md table row
    -- a single parametrized case per gate reading the one comparator
    result computed once above (_TIER_TABLE_DIFF), not 31 independent
    comparator invocations, which would each run on a singleton list and
    lose the duplicate-/unexpected-detection the shared comparator exists
    to provide."""
    key = _tier_hook_key(hook)
    header_value = _GATE_HOOK_TIER_VALUES.get(key)
    if header_value is None:
        pytest.skip(
            f"{key}: no well-formed tier header yet -- covered by "
            "test_tier_threat_model_header_present_and_well_formed"
        )
    assert key not in _TIER_TABLE_DIFF["mismatched"], (
        f"{key}: header/table tier mismatch (table, header) = "
        f"{_TIER_TABLE_DIFF['mismatched'].get(key)}"
    )


# The pinned floor from docs/hooks.md § "Threat-model tiers": the seven
# gates that carry `untrusted-input`. One-sided by design: it fails loudly
# if a pinned name stops being a hook-class: gate hook, is renamed, or
# drops `untrusted-input` from its header, a security-class relaxation
# needing a stated rationale. It does not fail if a new gate later adds
# `untrusted-input` on its own -- the header stays the source of truth for
# that.
_UNTRUSTED_INPUT_FLOOR: frozenset[str] = frozenset(
    {
        "deny-env-reads.sh",
        "enforce-marker-script-shape.sh",
        "require-ready-for-review.sh",
        "block-gh-pr-merge.sh",
        "deny-credential-bash-reads.sh",
        "deny-credential-file-reads.sh",
        "deny-network-installs.sh",
    }
)


@pytest.mark.parametrize("hook_name", sorted(_UNTRUSTED_INPUT_FLOOR))
def test_untrusted_input_floor_pinned(hook_name: str) -> None:
    """Each pinned gate still exists, is still hook-class: gate, and still
    carries `untrusted-input` in its tier header -- see _UNTRUSTED_INPUT_FLOOR."""
    hook = _MAIN_HOOKS_DIR / hook_name
    assert hook.is_file(), (
        f"{hook_name}: no longer a hook file, but _UNTRUSTED_INPUT_FLOOR "
        "still names it -- update the floor and state why in the commit "
        "message (a security-class relaxation)"
    )
    assert _hook_class(hook) == "gate", (
        f"{hook_name}: no longer hook-class: gate, but _UNTRUSTED_INPUT_FLOOR "
        "still names it -- update the floor and state why in the commit "
        "message (a security-class relaxation)"
    )
    value = _hook_tier_value(hook)
    assert value is not None, f"{hook_name}: tier header missing or malformed"
    assert "untrusted-input" in value.split(", "), (
        f"{hook_name}: dropped 'untrusted-input' from its tier header -- this "
        'is a security-class relaxation (docs/hooks.md § "Threat-model '
        'tiers"); state the rationale in the commit message'
    )


# The pinned floor from docs/hooks.md § "Threat-model tiers": the 14 gates
# that carry `irreversible`. One-sided by design: it fails loudly if a
# pinned name stops being a hook-class: gate hook, is renamed, or drops
# `irreversible` from its header, a security-class relaxation needing a
# stated rationale. It does not fail if a new gate later adds
# `irreversible` on its own -- the header stays the source of truth for
# that.
_IRREVERSIBLE_FLOOR: frozenset[str] = frozenset(
    {
        "block-gh-pr-merge.sh",
        "deny-credential-bash-reads.sh",
        "deny-credential-file-reads.sh",
        "deny-data-file-reads.sh",
        "deny-env-reads.sh",
        "deny-invisible-commit-content.sh",
        "deny-network-installs.sh",
        "deny-pii-in-commits.sh",
        "deny-private-project-refs.sh",
        "deny-reviewer-tree-mutation.sh",
        "enforce-marker-script-shape.sh",
        "require-ready-for-review.sh",
        "require-worktree-for-file-writes.sh",
        "require-worktree-for-git-writes.sh",
    }
)


@pytest.mark.parametrize("hook_name", sorted(_IRREVERSIBLE_FLOOR))
def test_irreversible_floor_pinned(hook_name: str) -> None:
    """Each pinned gate still exists, is still hook-class: gate, and still
    carries `irreversible` in its tier header -- see _IRREVERSIBLE_FLOOR."""
    hook = _MAIN_HOOKS_DIR / hook_name
    assert hook.is_file(), (
        f"{hook_name}: no longer a hook file, but _IRREVERSIBLE_FLOOR "
        "still names it -- update the floor and state why in the commit "
        "message (a security-class relaxation)"
    )
    assert _hook_class(hook) == "gate", (
        f"{hook_name}: no longer hook-class: gate, but _IRREVERSIBLE_FLOOR "
        "still names it -- update the floor and state why in the commit "
        "message (a security-class relaxation)"
    )
    value = _hook_tier_value(hook)
    assert value is not None, f"{hook_name}: tier header missing or malformed"
    assert "irreversible" in value.split(", "), (
        f"{hook_name}: dropped 'irreversible' from its tier header -- this "
        'is a security-class relaxation (docs/hooks.md § "Threat-model '
        'tiers"); state the rationale in the commit message'
    )


_TIER_RATIONALE_HOOK_SENTENCES: dict[str, tuple[str, ...]] = {
    "block-gh-pr-merge.sh": (
        (
            "The eval/bash -c wrapper shapes are also plausible cooperative "
            "mistakes and stay live findings under this gate's plain-cooperative "
            "tier component."
        ),
    ),
    "deny-env-reads.sh": (
        (
            "deny-credential-bash-reads.sh's env-variant token match is this "
            "gate's own backstop against that Bash-side gap, including a "
            "steered attempt to read the file through it."
        ),
        (
            "threat model here (prompt-injection or accidental access, "
            "not a privileged"
        ),
    ),
    "deny-credential-file-reads.sh": (
        (
            "This zero-allowlist, no-bypass-valve design is deliberate against "
            "a steered agent, not only an accidental Read."
        ),
    ),
    "deny-network-installs.sh": (
        (
            "This gate's threat model includes a cooperative agent steered by "
            "injected content toward an install or curl-pipe-to-shell shape, "
            "not only an accidental one."
        ),
    ),
    "require-ready-for-review.sh": (
        "This gate's threat model includes adversarial input, not only a",
        "The backstop against deliberate evasion is block-gh-pr-merge.sh blocking",
    ),
}


_TIER_RATIONALE_HOOK_SENTENCE_CASES: list[tuple[str, str]] = sorted(
    (hook_name, sentence)
    for hook_name, sentences in _TIER_RATIONALE_HOOK_SENTENCES.items()
    for sentence in sentences
)


@pytest.mark.parametrize("hook_name,sentence", _TIER_RATIONALE_HOOK_SENTENCE_CASES)
def test_tier_rationale_hook_sentence_pinned(
    hook_name: str, sentence: str
) -> None:
    """Pins the exact sentence docs/hooks.md's tier table rests on for this
    hook -- a failure here means that sentence changed in this hook's
    header; see docs/hooks.md § "Threat-model tiers" before editing this
    text."""
    hook = _MAIN_HOOKS_DIR / hook_name
    assert sentence in hook.read_text(), (
        f"{hook_name}: the sentence docs/hooks.md's tier table rests on for "
        "this hook's header changed -- see docs/hooks.md § "
        '"Threat-model tiers" before editing this text'
    )


_TIER_RATIONALE_DOC_SENTENCES: dict[str, str] = {
    "enforce-marker-script-shape.sh": (
        "Defense-in-depth against prompt-injection escalation through the "
        "`marker.sh` allow rules."
    ),
}


@pytest.mark.parametrize(
    "hook_name,sentence", sorted(_TIER_RATIONALE_DOC_SENTENCES.items())
)
def test_tier_rationale_doc_sentence_pinned(
    hook_name: str, sentence: str
) -> None:
    """Pins the exact docs/hooks.md sentence this hook's tier classification
    rests on, when that evidentiary text lives in the doc rather than the
    hook's own header. Checked against the hook's own `- **`name`**` bullet
    only: the tier table's Why cell quotes the same sentence and would
    satisfy a whole-document check. A failure here means the sentence
    docs/hooks.md's tier table rests on for this hook changed; see
    docs/hooks.md § "Threat-model tiers" before editing this text."""
    bullet_prefix = f"- **`{hook_name}`**"
    bullets = [
        line for line in _HOOKS_DOC_TEXT.splitlines()
        if line.startswith(bullet_prefix)
    ]
    assert len(bullets) == 1, (
        f"{hook_name}: expected exactly one '{bullet_prefix}' bullet in "
        f"docs/hooks.md, found {len(bullets)}"
    )
    assert sentence in bullets[0], (
        f"{hook_name}: the sentence docs/hooks.md's tier table rests on for "
        "this hook changed in docs/hooks.md's prose -- see docs/hooks.md § "
        '"Threat-model tiers" before editing this text'
    )


def test_hook_threat_model_section_names_every_tier_token() -> None:
    """CLAUDE.md's '## Hook threat model' section names every token in
    _THREAT_MODEL_TIERS, so the two vocabularies can't drift silently."""
    section = _markdown_section_text(_CLAUDE_MD.read_text(), "Hook threat model")
    assert section, "CLAUDE.md has no '## Hook threat model' section"
    for tier in _THREAT_MODEL_TIERS:
        assert tier in section, (
            f"CLAUDE.md's '## Hook threat model' section never mentions '{tier}'"
        )


_HOOK_THREAT_MODEL_SECTION = (
    "This repo's hooks default to guarding a **cooperative** agent that makes "
    "honest mistakes, not one attacking the gate. A review finding that needs a "
    "command or staged-content shape a cooperative agent would never emit is not "
    "a defect in a gate whose `# tier-threat-model:` line is present but omits "
    "`untrusted-input`. A gate with no tier line at all is not yet classified and "
    "gets no waiver. Non-gate hooks and shared library code get no waiver from "
    "this framework. A gate that lists `untrusted-input` gets no such waiver, "
    "because content read from outside the session can steer a cooperative agent "
    "into any shape. A gate is at least as strict as any gate whose header names "
    "it as that gate's backstop against evasion. A shared helper function is "
    "never relaxed just because one of its many callers denies less. A gate that "
    "lists `irreversible` is never relaxed on false-positive cost alone. A tier "
    "scopes what a reviewer treats as a defect in *this* gate; it never licenses "
    "the agent this repo guards to use a shape the gate happens to miss. See "
    "`docs/hooks.md` § \"Threat-model tiers\" for the tier definitions and each "
    "gate's classification."
)


def test_hook_threat_model_section_matches_pinned_text() -> None:
    """CLAUDE.md's '## Hook threat model' section, whitespace-normalized,
    equals _HOOK_THREAT_MODEL_SECTION."""
    section = _markdown_section_text(_CLAUDE_MD.read_text(), "Hook threat model")
    assert " ".join(section.split()) == _HOOK_THREAT_MODEL_SECTION, (
        "CLAUDE.md's '## Hook threat model' section changed. The text is waiver "
        "scope: an edit needs _HOOK_THREAT_MODEL_SECTION updated and the "
        "rationale stated in the commit message"
    )


def test_hooks_doc_tier_bullets_match_tier_vocabulary() -> None:
    """The tier-definition bullets in docs/hooks.md name exactly
    _THREAT_MODEL_TIERS, so the docs and the header grammar cannot drift."""
    documented = tuple(
        match.group(1)
        for line in _markdown_section_text(
            _HOOKS_DOC_TEXT, "Threat-model tiers"
        ).splitlines()
        if (match := re.match(r"^- `([a-z-]+)` — ", line))
    )
    assert documented == _THREAT_MODEL_TIERS, (
        f"docs/hooks.md tier bullets {documented} != _THREAT_MODEL_TIERS "
        f"{_THREAT_MODEL_TIERS}"
    )


def test_count_tier_threat_model_lines_sees_a_stale_line_past_the_header_window() -> None:
    """A second tier line below the header window is invisible to
    _find_tier_threat_model_lines but counted by the file-wide count."""
    lines = [
        "#!/bin/bash",
        "# hook-class: gate",
        "# tier-threat-model: cooperative",
        "# a",
        "# b",
        "# c",
        "# d",
        "# tier-threat-model: cooperative, irreversible",
    ]
    assert len(_find_tier_threat_model_lines(lines)) == 1
    assert _count_tier_threat_model_lines(lines) == 2


def _classify_tier_header(lines: list[str]) -> str:
    """Run the full tier-header pipeline against a whole-file `lines` list:
    locate any tier-threat-model line via _find_tier_threat_model_lines,
    then grade it with _tier_grammar_violation. Returns "absent" if no line
    is found, "valid" if found and well-formed, or the grammar violation's
    reason string otherwise.
    """
    matches = _find_tier_threat_model_lines(lines)
    if not matches:
        return "absent"
    assert len(matches) == 1, (
        f"fixture must contain exactly one candidate line, found {len(matches)}"
    )
    _, line = matches[0]
    violation = _tier_grammar_violation(line)
    return violation if violation is not None else "valid"


_TIER_HEADER_FIXTURES: list[tuple[str, list[str], str]] = [
    ("no_line", ["#!/bin/bash", "# hook-class: gate", "echo ok"], "absent"),
    (
        "unknown_tier",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: bogus-tier"],
        "unknown tier",
    ),
    (
        "wrong_order",
        [
            "#!/bin/bash",
            "# hook-class: gate",
            "# tier-threat-model: cooperative, irreversible, untrusted-input",
        ],
        "out of order",
    ),
    (
        "duplicate_token",
        [
            "#!/bin/bash",
            "# hook-class: gate",
            "# tier-threat-model: cooperative, untrusted-input, untrusted-input",
        ],
        "duplicate token",
    ),
    (
        "elevation_with_no_intent_tier",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: untrusted-input"],
        "no intent tier",
    ),
    (
        "comma_without_following_space",
        [
            "#!/bin/bash",
            "# hook-class: gate",
            "# tier-threat-model: cooperative,untrusted-input",
        ],
        "bad separator",
    ),
    (
        # A single trailing space with no token after it fails the
        # well-formed-prefix check (colon, one space, then a non-whitespace
        # character), so this is a delimiter-shape failure, not a content one.
        "empty_value",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: "],
        "bad separator",
    ),
    (
        "no_space_after_hash",
        ["#!/bin/bash", "# hook-class: gate", "#tier-threat-model: cooperative"],
        "bad separator",
    ),
    (
        # Space before the colon, paired with an otherwise-legal tier value
        # so this pins delimiter-shape detection rather than passing
        # coincidentally via unknown-tier rejection.
        "space_before_colon",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model : cooperative"],
        "bad separator",
    ),
    (
        "comma_double_space",
        [
            "#!/bin/bash",
            "# hook-class: gate",
            "# tier-threat-model: cooperative,  untrusted-input",
        ],
        "bad separator",
    ),
    (
        "colon_double_space",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model:  cooperative"],
        "bad separator",
    ),
    (
        "trailing_whitespace_accepted",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: cooperative   "],
        "valid",
    ),
    (
        "valid_line_on_non_gate_hook_accepted",
        ["#!/bin/bash", "# hook-class: informational", "# tier-threat-model: cooperative, irreversible"],
        "valid",
    ),
    (
        # Unknown tier AND wrong order/missing intent tier at once --
        # pinned precedence: unknown-tier wins.
        "double_violation_unknown_tier_wins",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: untrusted-input, bogus-tier"],
        "unknown tier",
    ),
    (
        # A capitalized token starts with a non-whitespace character, so it
        # passes the well-formed-prefix check and falls through to the
        # token-membership check -- "unknown tier", not "bad separator".
        "capitalized_tier_token",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: Cooperative"],
        "unknown tier",
    ),
    (
        "accept_cooperative",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: cooperative"],
        "valid",
    ),
    (
        "accept_cooperative_untrusted_input",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: cooperative, untrusted-input"],
        "valid",
    ),
    (
        "accept_cooperative_irreversible",
        ["#!/bin/bash", "# hook-class: gate", "# tier-threat-model: cooperative, irreversible"],
        "valid",
    ),
    (
        "accept_cooperative_untrusted_input_irreversible",
        [
            "#!/bin/bash",
            "# hook-class: gate",
            "# tier-threat-model: cooperative, untrusted-input, irreversible",
        ],
        "valid",
    ),
]


@pytest.mark.parametrize(
    ("fixture_lines", "expected"),
    [pytest.param(lines, expected, id=fixture_id) for fixture_id, lines, expected in _TIER_HEADER_FIXTURES],
)
def test_tier_header_pipeline_fixtures(fixture_lines: list[str], expected: str) -> None:
    """Meta-test for the finder+grammar-validator pipeline: one parametrized
    function over every header-line case _find_tier_threat_model_lines and
    _tier_grammar_violation must classify correctly."""
    assert _classify_tier_header(fixture_lines) == expected


def _tier_table_doc(section_body: str, *, include_heading: bool = True) -> str:
    """Build a minimal docs/hooks.md-shaped fixture: an optional
    '## Threat-model tiers' heading followed by `section_body` verbatim,
    then a trailing '## Gate hooks' heading marking the section's end."""
    heading = "## Threat-model tiers\n\n" if include_heading else ""
    return f"# Hook reference\n\n{heading}{section_body}\n## Gate hooks\n\nunrelated content\n"


_ROW_TEMPLATE = "| `{key}` | `{tier}` | {why} |"

_TIER_TABLE_FIXTURES: list[tuple[str, str, set[str], dict[str, str | None], dict[str, object]]] = [
    (
        "absent_row",
        _tier_table_doc("No rows in this section yet.\n"),
        {"some-gate.sh"},
        {"some-gate.sh": "cooperative"},
        {"missing": {"some-gate.sh"}, "unexpected": set(), "duplicated": set(), "mismatched": {}},
    ),
    (
        "orphan_row",
        _tier_table_doc(_ROW_TEMPLATE.format(key="orphan-hook.sh", tier="cooperative", why="Some rationale.") + "\n"),
        set(),
        {},
        {"missing": set(), "unexpected": {"orphan-hook.sh"}, "duplicated": set(), "mismatched": {}},
    ),
    (
        "duplicated_row",
        _tier_table_doc(
            "\n".join(
                _ROW_TEMPLATE.format(key="dup-hook.sh", tier="cooperative", why="First copy.")
                for _ in range(2)
            )
            + "\n"
        ),
        {"dup-hook.sh"},
        {"dup-hook.sh": "cooperative"},
        {"missing": set(), "unexpected": set(), "duplicated": {"dup-hook.sh"}, "mismatched": {}},
    ),
    (
        "value_mismatch",
        _tier_table_doc(_ROW_TEMPLATE.format(key="mismatch-hook.sh", tier="cooperative", why="Rationale.") + "\n"),
        {"mismatch-hook.sh"},
        {"mismatch-hook.sh": "cooperative, irreversible"},
        {
            "missing": set(),
            "unexpected": set(),
            "duplicated": set(),
            "mismatched": {"mismatch-hook.sh": ("cooperative", "cooperative, irreversible")},
        },
    ),
    (
        "escaped_pipe_row",
        _tier_table_doc(
            _ROW_TEMPLATE.format(
                key="esc-hook.sh",
                tier="cooperative",
                why="Denies a shape containing a literal \\| character mid-sentence.",
            )
            + "\n"
        ),
        {"esc-hook.sh"},
        {"esc-hook.sh": "cooperative"},
        {"missing": set(), "unexpected": set(), "duplicated": set(), "mismatched": {}},
    ),
    (
        "plugin_path_row",
        _tier_table_doc(
            _ROW_TEMPLATE.format(
                key="plugins/example-plugin/hooks/example-hook.sh", tier="cooperative", why="Plugin gate."
            )
            + "\n"
        ),
        {"plugins/example-plugin/hooks/example-hook.sh"},
        {"plugins/example-plugin/hooks/example-hook.sh": "cooperative"},
        {"missing": set(), "unexpected": set(), "duplicated": set(), "mismatched": {}},
    ),
    (
        "heading_absent_entirely",
        _tier_table_doc("unused", include_heading=False),
        {"some-gate.sh"},
        {"some-gate.sh": "cooperative"},
        {"missing": {"some-gate.sh"}, "unexpected": set(), "duplicated": set(), "mismatched": {}},
    ),
]


@pytest.mark.parametrize(
    ("doc_text", "gate_keys", "header_values", "expected_diff"),
    [
        pytest.param(doc_text, gate_keys, header_values, expected_diff, id=fixture_id)
        for fixture_id, doc_text, gate_keys, header_values, expected_diff in _TIER_TABLE_FIXTURES
    ],
)
def test_tier_table_pipeline_fixtures(
    doc_text: str, gate_keys: set[str], header_values: dict, expected_diff: dict
) -> None:
    """Meta-test for _parse_tier_table + _diff_table_against_headers
    together: one parametrized function over every table case."""
    rows = _parse_tier_table(doc_text)
    diff = _diff_table_against_headers(rows, gate_keys, header_values)
    assert diff == expected_diff


def test_tier_table_row_outside_section_is_ignored() -> None:
    """A row shaped exactly like a valid table row, but placed after the
    section's closing '## Gate hooks' heading, is not parsed -- the section
    scope (heading to next '## ') excludes it."""
    doc_text = (
        "## Threat-model tiers\n\nno rows here.\n\n## Gate hooks\n\n"
        + _ROW_TEMPLATE.format(key="outside-hook.sh", tier="cooperative", why="Lives outside the section.")
        + "\n"
    )
    assert _parse_tier_table(doc_text) == []


def test_tier_table_subheading_inside_section_does_not_end_it() -> None:
    """A '###' subheading inside the '## Threat-model tiers' section does
    not end it -- a row following one is still parsed."""
    doc_text = (
        "## Threat-model tiers\n\n### Per-hook classification\n\n"
        + _ROW_TEMPLATE.format(key="sub-hook.sh", tier="cooperative", why="Follows an in-section subheading.")
        + "\n\n## Gate hooks\n"
    )
    rows = _parse_tier_table(doc_text)
    assert [key for key, _tier, _why in rows] == ["sub-hook.sh"]


def test_tier_table_empty_why_cell_is_rejected() -> None:
    """A row with an empty Why cell is excluded from the parsed rows rather
    than kept with a blank rationale."""
    doc_text = _tier_table_doc("| `empty-why.sh` | `cooperative` |  |\n")
    assert _parse_tier_table(doc_text) == []


# Matches the $0-relative _lib.sh source line in either of its two known
# forms: the current `${0%/*}` parameter expansion, or the obsolete
# `$(dirname "$0")` command substitution (matched too, so a hook that
# regresses to it stays inside this test's domain instead of silently
# exempting itself).
#
# Excludes plugins/lovable-cloud/hooks/validate-migration-filename.sh — see
# _SWEPT_GATE_HOOKS below for why.
_LIB_SOURCE_LINE_RE = re.compile(r'^if ! \. "(?:\$\{0%/\*\}|\$\(dirname "\$0"\))/_lib\.sh" 2>/dev/null; then$')


def _sources_lib_via_dollar_zero(hook: Path) -> bool:
    return any(_LIB_SOURCE_LINE_RE.match(ln.strip()) for ln in hook.read_text().splitlines())


_LIB_SOURCE_HOOKS = [h for h in ALL_HOOKS if _sources_lib_via_dollar_zero(h)]

# Hooks with no $0-relative _lib.sh source line at all, named so a hook's
# source line silently drifting to an unrecognized shape fails this count
# instead of quietly shrinking _LIB_SOURCE_HOOKS's parametrized case count.
_KNOWN_NON_SOURCING_HOOKS: frozenset[str] = frozenset(
    {
        "provision-validator-venv.sh",
        "consume-migration-token.sh",
        "validate-migration-filename.sh",
    }
)


def test_lib_source_hooks_exhaustive() -> None:
    """_LIB_SOURCE_HOOKS must equal ALL_HOOKS minus exactly the known
    non-sourcing hooks by name, not merely by count — a set check names the
    diverging hook directly instead of masking it against a compensating
    drift elsewhere."""
    expected_names = {h.name for h in ALL_HOOKS} - _KNOWN_NON_SOURCING_HOOKS
    actual_names = {h.name for h in _LIB_SOURCE_HOOKS}
    assert actual_names == expected_names, (
        f"_LIB_SOURCE_HOOKS diverges from ALL_HOOKS minus "
        f"{sorted(_KNOWN_NON_SOURCING_HOOKS)}: "
        f"missing={sorted(expected_names - actual_names)}, "
        f"unexpected={sorted(actual_names - expected_names)}"
    )


@pytest.mark.parametrize("hook", _LIB_SOURCE_HOOKS, ids=[h.name for h in _LIB_SOURCE_HOOKS])
def test_lib_sh_sourced_via_parameter_expansion(hook: Path) -> None:
    """Every $0-relative source line must use `${0%/*}`, not
    `$(dirname "$0")`; see _lib.sh's header for why."""
    source_lines = [
        ln.strip() for ln in hook.read_text().splitlines() if _LIB_SOURCE_LINE_RE.match(ln.strip())
    ]
    assert len(source_lines) == 1, (
        f"{hook.name}: expected exactly one $0-relative _lib.sh source line, found {len(source_lines)}"
    )
    line = source_lines[0]
    assert line == 'if ! . "${0%/*}/_lib.sh" 2>/dev/null; then', (
        f"{hook.name}: _lib.sh source line must use the ${{0%/*}} parameter expansion; got: {line!r}"
    )


# Files carrying a second, unrelated dirname "$0" site alongside their swept
# _lib.sh source line — both lines share the substring `dirname "$0")`, so a
# future sweep (or well-meaning cleanup) that widens past the single
# intended line would silently mutate these too without a positive check.
_SECOND_DIRNAME_SITE_HOOKS: dict[str, list[str]] = {
    "nudge-handoff-near-context-cap.sh": [
        '\' _ "$(dirname "$0")/_lib.sh" "$CONFIG_DIR" "$SESSION_ID" 2>/dev/null)',
    ],
    "require-worktree-for-git-writes.sh": [
        'PARSER="$(dirname "$0")/parse-git-command.py"',
    ],
    "ask-new-dependency-disclosure.sh": [
        'HELPER_SCRIPT="$(dirname "$0")/parse-manifest-dependencies.py"',
    ],
    "require-skill-review.sh": [
        'VALIDATOR_SCRIPT="$(dirname "$0")/../scripts/validate_skill_structure.py"',
        'HOOK_OWN_GIT_COMMON_DIR=$(git -C "$(dirname "$0")" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)',
    ],
}


@pytest.mark.parametrize(
    "hook_name", sorted(_SECOND_DIRNAME_SITE_HOOKS), ids=sorted(_SECOND_DIRNAME_SITE_HOOKS)
)
def test_second_dirname_site_not_swept(hook_name: str) -> None:
    """The sweep to ${0%/*} touches only the $0-relative _lib.sh source line.

    These four hooks carry one or more second, unrelated `dirname "$0"` uses
    (locating a sibling script or resolving git state, not _lib.sh) that
    must survive unchanged.
    """
    hook = next((h for h in ALL_HOOKS if h.name == hook_name), None)
    assert hook is not None, f"{hook_name} not found in ALL_HOOKS (renamed or removed?)"
    expected_lines = _SECOND_DIRNAME_SITE_HOOKS[hook_name]
    lines = [ln.strip() for ln in hook.read_text().splitlines()]
    for expected_line in expected_lines:
        assert expected_line in lines, (
            f"{hook_name}: expected unswept dirname \"$0\" line not found verbatim — "
            f"expected {expected_line!r}"
        )


# ------------------------------------------------------------------ #
# Layer 2 — Behavior checks                                          #
# ------------------------------------------------------------------ #


def _run_hook_raw(
    hook: Path,
    stdin_text: str,
    cwd: Path | None = None,
    env: dict | None = None,
    argv: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run `hook` with raw stdin. `env`, when given, is merged on top of the
    real environment (not a replacement) — every case below only means to
    override PATH and/or HOME, and the hook still needs the rest of the real
    environment (e.g. TERM, LANG) to behave normally. `argv`, when given,
    overrides the invocation argument list (default: the hook's own absolute
    path) — used by the slash-free-$0 behavioral test below to invoke
    `["bash", hook.name]` instead."""
    subprocess_env = {**os.environ, **env} if env is not None else None
    return subprocess.run(
        argv if argv is not None else [str(hook)],
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


# Gate hooks only: every other hook-class documents fail-open on a missing
# _lib.sh, so a fail-closed assertion doesn't apply to them. Excludes
# validate-migration-filename.sh: it is hook-class: gate but sources
# _lib.sh via "${CLAUDE_PLUGIN_ROOT}", a mechanism this sweep doesn't touch.
_SWEPT_GATE_HOOKS = [h for h in _LIB_SOURCE_HOOKS if _hook_class(h) == "gate"]

# The one hook-class: gate hook excluded above by construction, named so
# this count stays independently checkable rather than self-referential.
_NON_SWEPT_GATE_HOOKS: frozenset[str] = frozenset({"validate-migration-filename.sh"})


def test_swept_gate_hooks_exhaustive() -> None:
    """_SWEPT_GATE_HOOKS must equal GATE_HOOKS minus exactly the known
    excluded gate hook by name, not merely by count — a set check names the
    diverging hook directly instead of masking it against a compensating
    drift elsewhere."""
    expected_names = {h.name for h in GATE_HOOKS} - _NON_SWEPT_GATE_HOOKS
    actual_names = {h.name for h in _SWEPT_GATE_HOOKS}
    assert actual_names == expected_names, (
        f"_SWEPT_GATE_HOOKS diverges from GATE_HOOKS minus "
        f"{sorted(_NON_SWEPT_GATE_HOOKS)}: "
        f"missing={sorted(expected_names - actual_names)}, "
        f"unexpected={sorted(actual_names - expected_names)}"
    )


@pytest.mark.parametrize("hook", _SWEPT_GATE_HOOKS, ids=[h.name for h in _SWEPT_GATE_HOOKS])
def test_slash_free_dollar_zero_fails_closed(hook: Path) -> None:
    """`executable=` is invalid here: the kernel rewrites `argv[0]` before
    bash sees it, so use `bash <bare-name>` with `cwd=hook.parent` instead.
    Both invocations below fail at the `_lib.sh` source line before any
    git/worktree logic runs, so running against the live tree (not an
    isolated copy) is safe here.
    """
    payload = json.dumps(bash_input("echo hello"))

    control = _run_hook_raw(hook, payload, cwd=hook.parent)
    assert control.returncode == 0, (
        f"{hook.name} [control-absolute-path]: expected exit 0 (allow), got "
        f"{control.returncode}: stdout={control.stdout!r} stderr={control.stderr!r}"
    )
    assert not control.stdout.strip(), (
        f"{hook.name} [control-absolute-path]: expected silent allow, got stdout={control.stdout!r}"
    )

    bare_result = _run_hook_raw(hook, payload, cwd=hook.parent, argv=["bash", hook.name])
    _assert_blocks(bare_result, hook.name, "slash-free-dollar-zero", "could not source _lib.sh")


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
    skill_file = repo / "claude-skills" / "skills" / "test-skill" / "SKILL.md"
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


def test_ready_for_review_missing_command_allowed() -> None:
    """A Bash tool call with a missing/empty `command` field must exit 0
    (allow)."""
    hook = _MAIN_HOOKS_DIR / "require-ready-for-review.sh"
    payload = {"tool_name": "Bash", "tool_input": {}}
    assert run_hook(hook, payload) == "allow"


def test_ready_for_review_writes_completion_marker_before_creating_the_pr() -> None:
    """SKILL.md orders the completion-marker write before the PR create and
    the create before the deactivate, the hygiene recheck before the marker
    step, and the do-not-write list before the write."""
    text = (_SKILLS_DIR / "ready-for-review" / "SKILL.md").read_text()
    record_completion_pos = text.index("HOOK_TEST_FIXTURE: record-completion")
    create_anchor = "gh pr create --title"
    create_count = text.count(create_anchor)
    assert create_count == 1, (
        f"expected exactly one {create_anchor!r} occurrence, found "
        f"{create_count} -- the create-step prose changed shape."
    )
    create_pos = text.index(create_anchor)
    deactivate_pos = text.index("HOOK_TEST_FIXTURE: deactivate-gate")
    assert record_completion_pos < create_pos < deactivate_pos, (
        "ready-for-review/SKILL.md must record gate completion before "
        "creating the PR, and create the PR before deactivating the session"
    )
    hygiene_heading = "## 6. Final hygiene recheck"
    hygiene_pos = text.find(hygiene_heading)
    assert hygiene_pos != -1, (
        f"{hygiene_heading!r} heading not found -- the hygiene step was "
        "retitled or moved."
    )
    assert (
        hygiene_pos < record_completion_pos
    ), "the final hygiene recheck must precede the completion-marker step"
    assert (
        text.index("**Do NOT write the completion marker if:**")
        < record_completion_pos
    ), "step 7's do-not-write list must precede the completion-marker write"


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
    idiom verbatim: that idiom targets `require-code-review.sh`'s own
    single-jq-call harness shape, not this test's two-chained-call
    assertion on `require-code-review.sh` proper.
    """
    timeout_path = shutil.which("timeout") or shutil.which("gtimeout")
    if not timeout_path:
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")

    stub_bin = tmp_path / "stub_bin"
    stub_bin.mkdir()
    fake_jq = stub_bin / "jq"
    fake_jq.write_text(f"#!/bin/bash\nsleep {scaled_shim_sleep(10)}\n")
    fake_jq.chmod(0o755)
    (stub_bin / "timeout").symlink_to(timeout_path)
    (stub_bin / "bash").symlink_to(bash_path)
    for cmd in ("head", "tail", "cat", "cut", "printf", "sleep", "grep", "dirname", "git"):
        cmd_path = shutil.which(cmd)
        if cmd_path:
            (stub_bin / cmd).symlink_to(cmd_path)
    # write_scaled_timeout_shim replaces the timeout symlink above, since
    # Path.write_text follows a symlink rather than replacing it.
    write_scaled_timeout_shim(stub_bin)

    hook = _MAIN_HOOKS_DIR / "require-code-review.sh"
    with assert_cap_engaged(stub_bin, production_cap=5, killed_calls=2):
        result = _run_hook_raw(hook, "not json", env={"PATH": str(stub_bin)})

    assert result.returncode == 2, (
        f"expected exit 2 once both timeout backstops fire, got {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "jq" in result.stderr, repr(result.stderr)
    assert 'docs/hooks.md "Gate deadlock recovery"' in result.stderr, repr(result.stderr)


def test_blocks_when_timeout_rejects_dash_k_flag(tmp_path: Path) -> None:
    """A `timeout` that rejects `-k` (BusyBox 1.34.1 and older) makes every
    gate's jq call exit nonzero, so the gate must fail closed through
    _lib_emit_deny's exit-2 fallback and name the `-k` cause and the
    runbook, not allow. The fake prints a usage line and exits 1 when its
    argv contains `-k`, and otherwise execs the real timeout, so jq itself
    is real."""
    real_timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if not real_timeout:
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
    if not shutil.which("jq"):
        pytest.skip("jq not found in PATH")

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    fake_timeout = fake_bin / "timeout"
    fake_timeout.write_text(
        "#!/bin/bash\n"
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "-k" ]; then\n'
        '    echo "timeout: invalid option -- \'k\'" >&2\n'
        '    echo "Usage: timeout [-s SIG] SECS PROG ARGS" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "done\n"
        f'exec "{real_timeout}" "$@"\n'
    )
    fake_timeout.chmod(0o755)

    hook = _MAIN_HOOKS_DIR / "require-code-review.sh"
    payload = bash_input("git commit -m x", session_id="k-rejecting-timeout-test")
    env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    result = _run_hook_raw(hook, json.dumps(payload), env=env)

    assert result.returncode == 2, (
        f"expected exit 2 (fail closed), got {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not result.stdout.strip(), f"expected no allow on stdout, got {result.stdout!r}"
    assert "Hook gate could not encode its deny reason" in result.stderr, repr(result.stderr)
    assert "rejects -k" in result.stderr, repr(result.stderr)
    assert 'docs/hooks.md "Gate deadlock recovery"' in result.stderr, repr(result.stderr)
