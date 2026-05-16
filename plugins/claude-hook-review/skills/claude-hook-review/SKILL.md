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

The idiomatic pattern from `deny-private-project-refs.sh` and `require-stow-reminder.sh`:

```bash
#!/bin/bash
set -uo pipefail   # explicit failure modes: unbound vars fail loudly,
                   # pipeline failures aren't masked. -e omitted: the
                   # hook inspects non-zero exits rather than aborting.
                   # Older hooks predate this convention; don't perpetuate
                   # the gap in new gates.

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_EXIT=$?

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked: could not parse tool-input JSON."
  exit 0
fi
```

Exit code is always `0`; the hook signals via stdout JSON, not via exit code.

Two non-obvious constraints for new gates:
- `"permissionDecision"` must be exactly `"deny"` (lowercase) — the runtime is case-sensitive; `"Deny"`, `"block"`, or any variant silently allows.
- `jq -r '... // empty'` exits **0** on missing fields (empty string, not a non-zero exit). `JQ_EXIT` guards against JSON parse failures only. For allow-list-style gates (deny anything outside a known set), also guard on `[ -z "$COMMAND" ]`. For deny-list-style gates (deny specific operations, allow all else), empty `COMMAND` correctly falls through.

## 4. Fail-open vs fail-closed posture

**Fail-closed** when the gate prevents a leak: parse failure → deny (a hook that can't read its input can't verify the operation is safe). Example: `deny-private-project-refs.sh:101–104`.

**Fail-open** when the gate is advisory and absence of input is normal: if `~/.claude/private-projects.md` is missing, allow normally (`deny-private-project-refs.sh:259–260`).

State the chosen posture in the script header. Reviewers shouldn't have to re-derive it from the code.

## 5. Dispatch design and drift risk

`settings.json` carries `if`-patterns for zero-cost early dispatch (e.g., `Bash(git commit *)`, `Bash(gh pr create *)`, `Bash(gh pr edit *)`). The script's internal regex is the authoritative gate. When both surfaces cover the same operation, they must stay in sync — document the pairing explicitly in the script header (`deny-private-project-refs.sh:9–14` is the model). A drift between `if`-pattern and internal regex creates silent coverage gaps.

## 6. Escape hatches and allowlists

Two distinct extension surfaces — don't conflate them:

- **`OSS_ALLOWLIST` in the committed script** (`deny-private-project-refs.sh:151`): open-source and standards-body references only. Adding a private identifier here commits it to a public repo — exactly the leak the hook prevents.
- **`~/.claude/private-projects.md`**: private identifiers, user-local, never committed. Users who need to blocklist private project names use this path.

Prefer named variables over magic strings buried in a regex; future contributors need a clear place to extend and a clear signal about which surface is appropriate.

## 7. Performance budget

Hooks fire on every matching tool call. Budget <100ms per fire. Subprocess spawns (`jq`, `grep`) add up — avoid loops over them. No network calls. No unbounded file I/O. If the hook reads user-controlled input, cap the read before scanning. External commands that contact a daemon, socket, or network (`docker`, `systemctl`, `curl`, package managers) must run under an explicit timeout — a hanging external call blocks the entire tool invocation until the OS timeout fires.

## 8. Test patterns

`claude/.claude/hooks/tests/test_hooks.py` is the model. Each test feeds tool-input JSON on stdin and reads `permissionDecision` from stdout. Cover boundary cases: malformed JSON input, unreadable file paths, pseudo-file paths (`/dev/stdin`, `/dev/fd/*`). When a test needs synthetic project-shaped tokens, follow the file's existing precedent for invented prefixes — do **not** invent prefixes that resemble real private projects.

## 9. Review checklist

Flag these on a hook PR:

- Header documents purpose, scope, dispatch surfaces, known gaps, and fail posture.
- `if`-dispatch in `settings.json` matches the internal regex (no drift between the two).
- `set -uo pipefail` present for gates with non-trivial parsing; if absent, justified.
- JSON input parsed with `jq`; fail-closed on parse error.
- `emit_deny` JSON shape: `hookSpecificOutput.permissionDecision` is exactly `"deny"` (lowercase, case-sensitive — `"Deny"` or `"block"` silently allows), `permissionDecisionReason` set.
- Matcher (`Bash` vs `Edit|Write|MultiEdit`) matches the operation surface being gated.
- Scope is narrow enough to avoid false positives: matcher + internal filter + repo-url guard exclude operations outside the stated purpose; header documents what the hook deliberately does NOT gate.
- Tests cover new code paths; synthetic prefixes only, no real project names.
- No unbounded loops, no network calls, no unbounded file I/O.
- Header lists known gaps the hook does not close.
- **`command` path resolution**: every `command` starts with `"$CLAUDE_PROJECT_DIR"`, `${CLAUDE_PLUGIN_ROOT}`, or a stable user-level prefix (`~`, `$HOME`, literal `/`). Bare `./` or unprefixed names fail review — see Section 2.

## 10. Operational-footprint escalation

After the section-9 checklist, spawn `staff-platform-engineer` synchronously. Pass the hook script or diff (not this review's output — each agent reads the source fresh) with these specific questions:

- Does the per-fire latency hold across all system states (daemon present, absent, slow, unresponsive)?
- Are external commands (`docker`, `systemctl`, `curl`, package managers, sockets) guarded by an explicit timeout?
- What failure modes arise when the gated command is slow or absent, and does the hook handle them?
- Are there daemon, network, or process dependencies that could cause the hook to block indefinitely?

Return ≤2K tokens of structured findings keyed to these questions; if over budget, prioritize by severity and note omissions. After the agent returns, fold its findings into the review output.

Also spawn `ciso-reviewer` when the hook gates a security boundary — auth checks, secrets, env-var reads, private-data redaction, or credential handling. Pass the same source and ask: is the security boundary correctly enforced, and could sensitive data leak via the hook's stdout or failure path? Apply the same ≤2K-token constraint.
