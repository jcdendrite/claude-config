---
name: claude-hook-review
description: >
  Review and audit of Claude Code hooks (.claude/hooks/*.sh and settings.json hook entries).
  TRIGGER when: reviewing a .claude/hooks/*.sh script or
  settings.json hook entry, or designing a new hook.
  DO NOT TRIGGER when: editing permissions.allow rules
  (use review-permissions) or non-Claude hook systems.
allowed-tools: Read, Grep, Glob, Bash
user-invocable: false
---

## 1. Hook events and matchers

**Event type** selects when a hook fires: `PreToolUse` (fires before the tool call; can deny), `PostToolUse` (fires after; observation only), `SessionStart` (setup state at session open).

**Matcher** selects *which* tool calls fire the hook. `Bash` is the most common, but `Edit|Write|MultiEdit` is also in active use (`ask-review-permissions.sh` gates write-side tools, not Bash). Verify the matcher matches the operation surface being gated.

**`if`-dispatch** narrows further within a matcher (e.g., `"if": "Bash(git commit *)"`) and is **advisory** — zero-cost early exit, but the real gate is the script's internal filter. If `if`-dispatch and the internal regex diverge, you get silent coverage gaps.

## 2. Path resolution in `command` strings

Every `command` value in `settings.json` must resolve to a stable absolute path regardless of the hook's CWD. Hooks "run in the current directory with Claude Code's environment" (Anthropic hooks reference) — the CWD is the agent's session-persisted bash directory at firing time, which can be a subdirectory, a worktree path, or wherever a prior Bash call left the session anchored.

Required prefix by settings file scope:

- **Project settings** (`<repo>/.claude/settings.json`): `"$CLAUDE_PROJECT_DIR"/<rest>`. Quotes around the variable handle paths with spaces.
- **Plugin settings** (plugin-bundled `settings.json`): `${CLAUDE_PLUGIN_ROOT}/<rest>`. Resolves to the plugin's installation directory; changes on each plugin update.
- **User settings** (`~/.claude/settings.json`): `~/<rest>`, `$HOME/<rest>`, or a literal `/...` absolute path. Acceptable because user-scoped hooks live at a fixed user-level location. `claude-config`'s own `~/.claude/settings.json` uses `~/.claude/hooks/...` across all hook entries with no observed failures — this form is safe in shells that perform standard tilde expansion.

Rejected forms: bare `./<rest>`, `../<rest>`, or unprefixed script names. Concrete failure mode: `./.claude/hooks/foo.sh` produces `/bin/sh: 1: ./.claude/hooks/foo.sh: not found` whenever the agent's CWD has drifted off the project root — for example, in a worktree session after a `cd`.

## 3. Script skeleton

New gate hooks must follow the canonical pattern in Section 4 (`_lib_parse_tool_input_or_deny`) rather than reimplementing this inline form. The structural conventions — `set -uo pipefail`, `emit_deny` defined before sourcing `_lib.sh`, exit-0 contract — still apply:

<!-- HOOK_SCRIPT_CONTENT_EXAMPLE: this fenced block documents a hook script's own file content — never typed into an agent's Bash tool. test_skills.py's Trigger-A regression scan (see docs/worktree-bash-guard.md) excludes it by this comment. -->
```bash
#!/bin/bash
set -uo pipefail
# -e intentionally omitted: hook checks exit codes explicitly rather than aborting.
# Use this pattern in new gates.

emit_deny() {
  local reason="$1"
  local reason_json
  # Defined before sourcing _lib.sh so a failed source can still deny.
  # Use _lib_jq over jq when available for its timeout backstop.
  if declare -F _lib_jq >/dev/null 2>&1; then
    reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  else
    reason_json=$(printf '%s' "$reason" | jq -Rs . 2>/dev/null)
  fi
  if [ -z "$reason_json" ]; then
    # reason_json empty (jq missing/failed/timed out): exit 2 to use PreToolUse's stderr-based block, since exit 0 with a half-built payload parses as no-decision and lets the tool run.
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, which runs outside the tool-call path these hooks gate; in a headless or non-interactive run, ensure jq is installed in the execution environment beforehand. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by <gate-name> gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by <gate-name> gate: could not parse tool-input JSON."
# INPUT, TOOL_NAME, COMMAND now set. Gate logic follows.
```

Exit code is `0` for both allowing (no output) and the normal deny case (JSON payload on stdout, jq available to encode the reason). See §4's fail-open/fail-closed discussion for `emit_deny`'s `2`-exit fallback when it cannot encode the reason as JSON.

Two non-obvious constraints for new gates:
- `"permissionDecision"` must be exactly `"deny"` (lowercase) — the runtime is case-sensitive; `"Deny"`, `"block"`, or any variant silently allows.
- After `_lib_parse_tool_input_or_deny`, `COMMAND` may still be empty: `jq -r '... // empty'` exits **0** on missing fields (empty string, not non-zero). For allow-list-style gates (deny anything outside a known set), guard on `[ -z "$COMMAND" ]`. For deny-list-style gates (deny specific operations, allow all else), empty `COMMAND` correctly falls through.

## 4. Fail-open vs fail-closed posture

**Fail-closed** when the gate prevents a leak: parse failure → deny (a hook that can't read its input can't verify the operation is safe). All gate-class hooks use `_lib.sh::_lib_parse_tool_input_or_deny` as the canonical fail-closed pattern — source that function rather than reimplementing the inline JQ_EXIT check.

The helper uses a single `_lib_jq` call to extract both `.tool_name` and `.tool_input.command`. Three deny paths protect against silent-allow:
- **(a) jq non-zero exit** — parse failure, `timeout` exit=124, or missing jq binary; also fires when `.tool_input` is not an object (jq structural-type error). Per Anthropic's PreToolUse hook contract, `.tool_name` and `.tool_input` are always present on a real hook event; jq failure indicates malformed or spoofed input.
- **(b) empty INPUT** — stdin EOF, closed pipe, or harness misbehavior.
- **(c) empty TOOL_NAME** — valid JSON but `.tool_name` absent (e.g. `{}`), indicating the call did not originate from a real tool invocation.

The helper uses a 5s `timeout` backstop around every jq call (citing `guard-settings-session-keys.sh`'s `git_capped` precedent). On systems without `timeout` (BSD/macOS default), the helper falls back to bare `jq`; `install.sh` warns at onboarding time (including the `gtimeout` macOS alias case). This is a latency backstop, not a correctness boundary.

A fourth silent-allow path: `emit_deny` failing to JSON-encode its own reason (jq missing/failed/timed out) and exiting 0 anyway with a half-built payload the harness reads as no-decision — the Section 3 skeleton's two-path `emit_deny` (encode-or-exit-2) closes this.

The canonical pattern — define `emit_deny` **before** sourcing `_lib.sh`, then call the helper:

```bash
emit_deny() { ... }  # defined before sourcing so a missing _lib.sh can still deny

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by <gate-name> gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by <gate-name> gate: could not parse tool-input JSON."
```

After the helper call, `INPUT`, `TOOL_NAME`, and `COMMAND` are set as globals. Hooks that perform **post-validation** extracts from `$INPUT` (e.g. `.session_id`, `.tool_input.file_path`) still need `// empty` defaults and explicit presence checks for required fields — the helper validates parse-shape, not semantic completeness.

**Fail-open** when the gate is advisory and absence of input is normal: if `<config-dir>/private-projects.md` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) is missing, allow normally (`deny-private-project-refs.sh:259–260`).

State the chosen posture in the script header. Reviewers shouldn't have to re-derive it from the code.

**`# hook-class:` header** — every hook must declare its class on the second line:
- `# hook-class: gate` — fires PreToolUse and may deny. Required on all guard hooks.
- `# hook-class: informational` — fires PostToolUse/SessionStart/etc. and never denies. The label describes the hardening posture (cannot deny), not functional importance — an essential workflow hook (e.g., `capture-session-id.sh`) and a purely advisory one both carry this label if neither issues a PreToolUse denial.
- `# hook-class: turn-gate` — fires on `Stop` and may block the *turn* from ending (`{"decision":"block","reason":...}`) rather than a tool call from running. A distinct contract from `gate`, not a Stop hook mislabeled `gate`: the emission shape differs (see §9), and `GATE_HOOKS` — which drives every Layer-2 auto-parametrized behavior test in `test_hook_alignment.py` — excludes it entirely, so a turn-gate hook needs hand-written equivalents for cases like jq-absent (see §9's note on the reversed fail posture there).

Flipping a marker from `gate` to `informational`, or from either to `turn-gate`, is a security-class change that removes or reshapes a deny path; the commit message must state the rationale.

## 5. Dispatch design and drift risk

`settings.json` carries `if`-patterns for zero-cost early dispatch (e.g., `Bash(git commit *)`, `Bash(gh pr create *)`, `Bash(gh pr edit *)`). The script's internal regex is the authoritative gate. When both surfaces cover the same operation, they must stay in sync — document the pairing explicitly in the script header (`deny-private-project-refs.sh:9–14` is the model). A drift between `if`-pattern and internal regex creates silent coverage gaps.

## 6. Escape hatches and allowlists

Two distinct extension surfaces — don't conflate them:

- **`OSS_ALLOWLIST` in the committed script** (`deny-private-project-refs.sh:151`): open-source and standards-body references only. Adding a private identifier here commits it to a public repo — exactly the leak the hook prevents.
- **`<config-dir>/private-projects.md`**: private identifiers, user-local, never committed. Users who need to blocklist private project names use this path.

Prefer named variables over magic strings buried in a regex; future contributors need a clear place to extend and a clear signal about which surface is appropriate.

## 7. Performance budget

Hooks fire on every matching tool call. Budget <100ms per fire. Subprocess spawns (`jq`, `grep`) add up — avoid loops over them. No network calls. No unbounded file I/O. If the hook reads user-controlled input, cap the read before scanning. External commands that contact a daemon, socket, or network (`docker`, `systemctl`, `curl`, package managers) must run under an explicit timeout — a hanging external call blocks the entire tool invocation until the OS timeout fires.

The `_lib_jq` wrapper in `_lib.sh` applies a 5s `timeout` to every jq call inside `_lib_parse_tool_input_or_deny`. Hooks that call jq directly for post-validation extracts (e.g. `.session_id`, `.tool_input.file_path`) do not need an additional timeout on those calls — they operate on content already proven parseable by the helper, so the only failure mode is a slow or missing jq binary, both of which are benign (field is missing → `// empty` → gate falls through to allow).

## 8. Test patterns

`claude/.claude/hooks/tests/test_hooks.py` is the model. Each test feeds tool-input JSON on stdin and reads `permissionDecision` from stdout. Cover boundary cases: malformed JSON input, unreadable file paths, pseudo-file paths (`/dev/stdin`, `/dev/fd/*`). When a test needs synthetic project-shaped tokens, follow the file's existing precedent for invented prefixes — do **not** invent prefixes that resemble real private projects.

## 9. Review checklist

Flag these on a hook PR:

- Header documents purpose, scope, dispatch surfaces, known gaps, and fail posture.
- `if`-dispatch in `settings.json` matches the internal regex (no drift between the two).
- `set -uo pipefail` present for gates with non-trivial parsing; if absent, justified.
- JSON input parsed with `jq`; fail-closed on parse error.
- Emitted JSON shape matches the hook's class. `gate`: `hookSpecificOutput.permissionDecision` is exactly `"deny"` (lowercase, case-sensitive — `"Deny"` or `"block"` silently allows), `permissionDecisionReason` set. `turn-gate`: a top-level `{"decision":"block","reason":...}` pair (not nested under `hookSpecificOutput`) — the harness routes on those exact literal keys, so a typo silently no-ops. These are two distinct emission contracts; do not test or review one as though it were the other.
- Matcher (`Bash` vs `Edit|Write|MultiEdit` vs matcher-less `Stop`, which has no matcher support at all) matches the operation surface being gated.
- Scope is narrow enough to avoid false positives: matcher + internal filter + repo-url guard exclude operations outside the stated purpose; header documents what the hook deliberately does NOT gate.
- Tests cover new code paths; synthetic prefixes only, no real project names. For `turn-gate` hooks specifically, since `GATE_HOOKS` excludes them from `test_hook_alignment.py`'s auto-parametrization: hand-written jq-absent and required-external-tool-absent cases are present, and — the reversed posture from `gate` — both must assert **silent-allow** (exit 0, empty stdout), not a hard block. A `turn-gate` hook that hard-blocks on a missing dependency turns an infrastructure gap into a stuck turn, which is the opposite of a `gate` hook's correct fail-closed jq-absent behavior (Section 4).
- No unbounded loops, no network calls, no unbounded file I/O.
- Header lists known gaps the hook does not close.
- Each fact within a header category (each known gap, each scope exclusion, etc.) is one sentence — a category with multiple facts stays a multi-line bulleted list, one sentence per bullet (wrapping a long sentence across physical lines for width is fine; splicing in a second fact or its rationale is not); elaboration beyond the fact goes in docs/, cited by path, not inlined.
- **`command` path resolution**: every `command` starts with `"$CLAUDE_PROJECT_DIR"`, `${CLAUDE_PLUGIN_ROOT}`, or a stable user-level prefix (`~`, `$HOME`, literal `/`). Bare `./` or unprefixed names fail review — see Section 2.

## 10. Operational-footprint escalation

After the section-9 checklist, spawn `staff-platform-engineer` synchronously. Pass the hook script or diff (not this review's output — each agent reads the source fresh) with these specific questions:

- Does the per-fire latency hold across all system states (daemon present, absent, slow, unresponsive)?
- Are external commands (`docker`, `systemctl`, `curl`, package managers, sockets) guarded by an explicit timeout?
- What failure modes arise when the gated command is slow or absent, and does the hook handle them?
- Are there daemon, network, or process dependencies that could cause the hook to block indefinitely?

Return ≤2K tokens of structured findings keyed to these questions; if over budget, prioritize by severity and note omissions. After the agent returns, fold its findings into the review output.

Also spawn `ciso-reviewer` when the hook gates a security boundary — auth checks, secrets, env-var reads, private-data redaction, or credential handling. Pass the same source and ask: is the security boundary correctly enforced, and could sensitive data leak via the hook's stdout or failure path? Apply the same ≤2K-token constraint.
