# Hook PreToolUse registration test

## Context

Close a deferred review finding: no test asserts that a hook is actually
registered in a `PreToolUse` matcher group, so a `hook-class: gate` hook
that gets renamed, deleted from its matcher entry, or never wired in the
first place can silently stop firing with nothing failing red. The finding
was raised as orthogonal, repo-wide, pre-existing scope — not tied to any
specific hook change — so the fix is a standalone test addition, not a
behavior change to any hook.

## Approach

Add one parametrized pytest test, `test_gate_hook_registered_in_pretooluse_matcher`,
to `test_hook_alignment.py`. It asserts every `hook-class: gate` hook (the
module's existing `GATE_HOOKS` list, already spanning both
`claude/.claude/hooks/*.sh` and `plugins/*/hooks/*.sh`) has its command
wired into a `PreToolUse` matcher group — `claude/.claude/settings.json` for
main-dir hooks, that plugin's own `hooks/hooks.json` for plugin-dir hooks —
via a shared helper, `_pretooluse_command_for(hook)`, that both the new test
and the existing `test_gate_backed_skill_has_a_live_gate` (lines 207–220)
call, so the matching semantics live in one place rather than two
independently-maintained copies of the same scan (flagged by `staff-sdet`
in review; the shared helper resolves it, not just the duplication note).

Match strength, tightened past a bare substring `endswith` after
`staff-sdet` review flagged that a hook name appearing as a trailing CLI
argument to an unrelated dispatcher script would false-positive "wired":
- **Main-dir hooks**: every current `settings.json` entry is a bare
  `~/.claude/hooks/<name>.sh` with no arguments
  [verified: `claude/.claude/settings.json`, dumped in-session], so the
  helper requires exact string equality against
  `f"~/.claude/hooks/{hook.name}"` — not a substring match.
- **Plugin-dir hooks**: every current plugin `hooks.json` entry is zero or
  more `VAR="${VAR}"`-shaped env assignments followed by a final
  `"${CLAUDE_PLUGIN_ROOT}"/hooks/<name>.sh` token
  [verified: all 4 `plugins/*/hooks/hooks.json`, dumped in-session], so the
  helper splits the command on whitespace and requires the **last** token to
  equal `f'"${{CLAUDE_PLUGIN_ROOT}}"/hooks/{hook.name}'` exactly — ruling out
  the hook name appearing as a non-final argument.

This still can't distinguish "invoked as the final argument to a wrapper
script" from "invoked directly" — that distinction needs execution, not
config parsing, and building a shell-argument-aware invocation checker to
close it would be disproportionate to a config-invariant test (the existing
Layer 2 behavior checks already exercise real hook execution; this test's
job is the static-registration invariant only). Named here rather than left
implicit, per the `staff-sdet` finding.

Covering plugin hooks alongside main-dir hooks is a structural-sibling
extension, not scope creep: `GATE_HOOKS` already unifies both directories
for every other check in this file, so branching the config-file lookup by
directory is less code than re-filtering `GATE_HOOKS` down to only the 4
main-dir hooks the finding's wording named.

**Assumption ledger**

```
Root: a hook-class: gate hook that exists on disk but was never wired into
a PreToolUse matcher group silently never fires, and no test in the suite
catches that for any hook outside the 4 already covered by
test_gate_backed_skill_has_a_live_gate.
Givens: plugin hooks register in their own plugins/<name>/hooks/hooks.json,
not claude/.claude/settings.json — beyond reach: harness convention for
where plugin hook registration lives, this plan doesn't touch it.

Row 1 [mechanism]: one parametrized test over GATE_HOOKS, branching the
config-file lookup (settings.json vs. that plugin's hooks.json) by which
hooks dir the hook lives in — anchors: root — reuses the existing wired-
check scan shape and hook inventory instead of adding either a new inventory
or a new scan.
Row 2 [assumption]: hook-class: gate is the correct predicate for "must
fire on PreToolUse" [verified: test_hook_alignment.py TestHookClassHeader's
test_hook_class_value_valid docstring] — anchors: row1
Row 3 [assumption]: plugin PreToolUse registration uses the same
{matcher, hooks: [{command}]} shape as claude/.claude/settings.json, so one
scan helper serves both [verified:
plugins/skill-management/hooks/hooks.json] — anchors: row1
Row 4 [assumption]: every current gate hook's command string is either a
bare `~/.claude/hooks/<name>.sh` (main dir) or ends, as its last
whitespace-separated token, in `"${CLAUDE_PLUGIN_ROOT}"/hooks/<name>.sh`
(plugin dir) — no current command takes CLI arguments after the hook path
[verified: claude/.claude/settings.json + all 4
plugins/*/hooks/hooks.json, dumped in-session] — anchors: row1
```

## Critical files

- `claude/.claude/hooks/tests/test_hook_alignment.py`:
  - Add `_pretooluse_command_for(hook) -> Path` helper (or equivalent) that
    picks `_SETTINGS_PATH` for a hook under `_MAIN_HOOKS_DIR` and
    `hook.parent / "hooks.json"` for a hook under any `_PLUGIN_HOOKS_DIRS`
    entry, then returns the matched command strings per the exact/
    last-token matching rules above.
  - Refactor `test_gate_backed_skill_has_a_live_gate`'s inline wired-scan
    (lines 207–220, currently `settings.json`-only, `endswith`-matched) to
    call the same helper, so main-dir matching semantics live in one place.
  - Add `test_gate_hook_registered_in_pretooluse_matcher`, parametrized over
    `GATE_HOOKS`, asserting the helper returns a non-empty match for every
    hook.
  Reuse: `GATE_HOOKS`, `_MAIN_HOOKS_DIR`, `_PLUGIN_HOOKS_DIRS`,
  `_SETTINGS_PATH` (all already defined in this file) — no new
  module-level inventory.

## Verification

`../../../.venv/bin/pytest claude/.claude/hooks/tests/test_hook_alignment.py -k test_gate_hook_registered_in_pretooluse_matcher -v`
— new test passes for every current gate hook. Then the full file
(`../../../.venv/bin/pytest claude/.claude/hooks/tests/test_hook_alignment.py`)
to confirm no regressions in the existing Layer 0–2 checks, including
`test_gate_backed_skill_has_a_live_gate` after its refactor onto the shared
helper.

## Out of scope

- Extending the same registration check to `turn-gate` (Stop) or
  `batch-gate` (PostToolBatch) hooks — those fire on different events with
  different matcher shapes than `PreToolUse`; the deferred finding named
  `PreToolUse` specifically.
- A synthetic fixture proving the per-plugin-scoped lookup can't cross-match
  a same-named-but-unwired hook in a different plugin (raised by
  `staff-sdet`): no such name collision exists among today's 4 plugin gate
  hooks, so a synthetic fixture would test a hypothetical alternate
  implementation rather than this one. The collision-safety comes from
  scoping the lookup to `hook.parent / "hooks.json"` per hook rather than a
  merged cross-plugin table — noted here so a future refactor that flattens
  that lookup has to consciously drop this note, not silently regress past
  an unrelated diff.
