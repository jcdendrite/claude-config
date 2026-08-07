# Permission-prompt tracker

## Context

Developers running Claude Code in `auto` permission mode have no way to see
how often — or for which commands — Claude Code still shows an interactive
permission-approval dialog, so they can't tell which `permissions.allow`
rules would actually cut down on interruptions. A prior investigation in
this same work session (see conversation, and `/verify-sources` pass against
[`permissions.md`](https://code.claude.com/docs/en/permissions.md),
[`debug-your-config`](https://code.claude.com/docs/en/debug-your-config),
[`cli-reference`](https://code.claude.com/docs/en/cli-reference),
[`env-vars`](https://code.claude.com/docs/en/env-vars), local JSONL session
transcripts, `~/.claude/telemetry/`, `~/.claude/debug/`, and the macOS
unified log) confirmed there is no retroactive source for this — it has to
be captured going forward. This plan adds an opt-in hook that captures it,
shipped to every stow user of this repo but inert until a developer
explicitly turns it on.

## Approach

**Chosen mechanism:** a `Notification` hook (matcher `permission_prompt`)
that appends the raw hook payload to a local, gitignored JSONL log when a
per-developer sentinel file exists.

**Why this hook event, not PreToolUse:** [https://code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)
documents `Notification`'s matcher values verbatim as `permission_prompt`,
`idle_prompt`, `auth_success`, `elicitation_dialog`, `elicitation_complete`,
`elicitation_response`, `agent_needs_input`, `agent_completed`, and
separately lists the events with *no* matcher support (`UserPromptSubmit`,
`PostToolBatch`, `Stop`, `TeammateIdle`, `TaskCreated`, `TaskCompleted`,
`WorktreeCreate`, `WorktreeRemove`, `MessageDisplay`, `CwdChanged`) —
`Notification` is absent from that list, so its matcher is live. This is
also the *minimal* signal available: `PreToolUse` fires for every tool call
regardless of whether it will prompt, so using it here would mean
re-implementing Claude Code's own permission-resolution logic (allow rules
+ auto-mode classifier) just to guess whether a prompt is coming — a
heavier, duplicative mechanism. `Notification`/`permission_prompt` fires
only at the moment the real decision to prompt has already been made, so
there is no lighter or heavier alternative to weigh — it's the
purpose-built event for exactly this signal.

**Payload schema is genuinely undocumented — logged as raw JSON, not a
guessed field list.** The docs give a schema example for `PreToolUse`
(`session_id`, `tool_name`, `tool_input`, `permission_mode`, ...) but
explicitly do **not** give a field-level example for `Notification`,
and do not state whether a `permission_prompt` notification carries
`tool_name`/`tool_input` or only a free-text `message`. Rather than assume a
field list that might not exist on the real payload (which would silently
lose data), the hook appends the **entire stdin payload it receives**, with
one locally-added field (`logged_at`, an ISO 8601 timestamp) merged in. This
is deliberately schema-tolerant: whatever fields Claude Code actually sends
get captured, and analysis code written after the first real capture can
target the real shape instead of an assumed one.

**Opt-in mechanism: committed hook + sentinel file, not the personal-scope
`settings` override file (`settings` + `.local` + `.json`).**
That file is gitignored, so it can't be "shippable" — every
developer would have to hand-write their own hook registration. This repo's
existing pattern for "ships to everyone, active for no one by default" is a
committed hook gated by a sentinel file the hook checks for at runtime,
exactly as `_lib_autonomous_shipping_active` does for
`~/.claude/autonomous-shipping-required`
(`claude/.claude/hooks/_lib.sh:625-654`). This plan adds a same-shaped
`_lib_permission_prompt_tracking_active` rather than inventing a new gating
idiom.

**No per-repo optout.** `autonomous-shipping-optout` exists because that
feature *changes git/PR behavior* per repo, and a developer may want it on
their machine generally but not in one particular repo. This feature only
appends to a local file (see "Sensitive-data handling" below for exactly
what "local" does and doesn't guarantee) and changes no git/PR/tool
behavior — and its entire purpose is a *cross-repo* frequency view (the
exact question the user asked this session spanned several of their other,
unrelated repos). A per-repo optout would fragment the aggregate the
feature exists to produce, for no privacy benefit the sentinel file doesn't
already provide (nothing is opted in until the developer touches the
sentinel).

**Log file location and naming:** `~/.claude/.permission-prompt-log.jsonl`
— dot-prefixed, config-dir root, matching the existing `.commit-stall-block.log`
/ `.handoff-nudge.log` convention exactly (`claude/.claude/hooks/advance-past-commit-stall.sh:85`,
`claude/.claude/hooks/nudge-handoff-near-context-cap.sh:130`), appended via
plain `>>` guarded with `2>/dev/null || true` (fail-open: a log-write
failure must never break the hook or surface to the user).

**Sensitive-data handling — revised after `ciso-reviewer` + `claude-hook-review`
plan review found the first draft logged raw, unredacted payloads to a
world-readable file inside a git-tracked directory.** The docs the plan
cites show sibling hook events carry `tool_input` (the triggering command
text) in their payload — a `permission_prompt` notification plausibly does
too, and `tool_input` for `Bash` is exactly where inline secrets
(`curl -H "Authorization: Bearer …"`, a connection string) live. Three
concrete fixes, each closing a distinct exposure vector the review named
(not layered hardening on one mechanism):

1. **Redact before writing, don't log raw.** This repo already has a
   schema-agnostic answer for "strip credential-shaped substrings from an
   arbitrary JSON tree regardless of field names" —
   `redact-credential-values.sh`'s `walk(if type == "string" then
   gsub($pattern; "[REDACTED-CREDENTIAL]") else . end)` over
   `${_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX}|${_LIB_CREDENTIAL_VALUE_REGEX}` plus
   optional user additions from `credential-value-patterns.md`
   (`claude/.claude/hooks/redact-credential-values.sh:39-71`). "Schema
   undocumented" was the first draft's reason to skip redaction, but this
   walk needs no field names — it string-matches anywhere in the tree, so
   the undocumented-schema problem doesn't excuse skipping it. Extract this
   walk (currently inlined in one call site) into a new `_lib.sh` function,
   `_lib_redact_credential_shaped_strings <json>` (assembles the pattern
   including user additions internally, echoes redacted JSON or the
   original on any resolution/parse failure — fail-open, matching the
   existing hook's posture), and have both `redact-credential-values.sh`
   and the new hook call it. Two callers is exactly this repo's own
   documented threshold for extracting a shared helper rather than
   duplicating security-sensitive logic.
2. **`chmod 600` the log on every append.** `install.sh` hardens `~/.claude`
   to `700` specifically against other local accounts, but the stowed
   checkout directory that physically holds this file is `755` and a
   plain `>>`-created file inherits `644` — world-readable on a shared
   machine, undoing that hardening for this one file. One `chmod 600
   "$LOG_FILE" 2>/dev/null || true` after the append closes this
   independent of the redaction fix.
3. **The "never leaves the machine" claim gets an honest caveat, not a
   new mechanism.** `~/.claude/.permission-prompt-log.jsonl` resolves
   through the same stow symlink as every other per-machine hook log, into
   this git-tracked checkout, protected from `git add -A` by `.gitignore`
   alone — identical to `.handoff-nudge.log`'s and
   `.commit-stall-block.log`'s existing exposure shape. Rather than add a
   new path-specific commit-time blocklist (a second layer duplicating
   what already exists), note that `deny-pii-in-commits.sh`'s always-on
   credential-value scan (`claude/.claude/hooks/deny-pii-in-commits.sh:400`,
   same `_LIB_CREDENTIAL_VALUE_REGEX`) already denies any `git commit`
   whose staged diff matches a credential shape, regardless of which file
   it's in or whether `.gitignore` was bypassed (`git add -f`) — this is a
   real, independent, pre-existing second layer for the git-exfiltration
   vector specifically, not new work. The genuinely open residual, named
   here rather than papered over: this covers `git commit` only, not a
   local backup/dotfile-sync tool (Time Machine, iCloud Drive, chezmoi)
   snapshotting `$HOME` directly off disk. `docs/permission-prompt-tracking.md`
   states this as a known limitation rather than the plan asserting a
   guarantee it can't back.

**Retention: documented manual policy, no automatic deletion in this plan.**
An automatic rolling-window delete (the shape `cleanup-commit-stall-marker.sh`
uses for marker files) would need a retention-window number, and this
feature's stated purpose — a longitudinal, cross-repo frequency view — means
picking that number trades away exactly the value the feature exists to
produce. That's a call for whoever is deciding how long to keep it, not a
default to bake in silently. `docs/permission-prompt-tracking.md` documents
the log's unbounded growth and gives a manual trim one-liner
(`jq` filtering by `logged_at`, or delete-and-let-it-regrow); automatic
rotation is named as a deferred follow-up in **Out of scope**, not a gap
left unaddressed.

### Assumption ledger

**Root problem:** developers in `auto` mode can't see which commands still
trigger an interactive permission prompt, or how often — `permissions.allow`
tuning is currently guesswork.

| # | Assumption | Tag | Anchor |
|---|---|---|---|
| 1 | `Notification` hook + `permission_prompt` matcher is the correct, minimal capture point | `[verified: https://code.claude.com/docs/en/hooks]` | root |
| 2 | Exact JSON field names in a `permission_prompt` `Notification` payload (beyond the common `session_id`/`cwd`/`hook_event_name` fields shown for other events) | `[unverified — docs do not give a Notification field example]` | root |
| 3 | Sentinel-file-gated committed hook is the right shape for "shippable + opt-in" (vs. the personal-scope `settings` override file) | `[verified: claude/.claude/hooks/_lib.sh:625-654 _lib_autonomous_shipping_active]` | root |
| 4 | `_lib_config_dir` must resolve the sentinel/log path (never a hardcoded `~/.claude`) | `[verified: claude/.claude/hooks/_lib.sh:93-107, and commit 399ce6c "Honor CLAUDE_CONFIG_DIR across hooks, scripts, and a plugin"]` | row 3 |
| 5 | No per-repo optout is needed | `[unverified — my own scope call, not put to the engineer; rationale above]` | root |
| 6 | Hook must self-check `hook_event_name == "Notification"` rather than trust the settings.json matcher alone | `[verified: repo CLAUDE.md "Hook defense-in-depth"; pattern at claude/.claude/hooks/consume-durable-continuity-file-on-read.sh:83-113]` | root |
| 7 | The payload may carry `tool_input` (command text) and therefore inline secrets, requiring redaction before write | `[verified: ciso-reviewer + claude-hook-review plan-review round 1 — see Sensitive-data handling]` | row 2 |
| 8 | The shared credential-value walk (`_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX`/`_LIB_CREDENTIAL_VALUE_REGEX` + `walk(...)`) needs no field names, so schema uncertainty (row 2) doesn't excuse skipping it | `[verified: claude/.claude/hooks/redact-credential-values.sh:39-71]` | row 7 |
| 9 | `deny-pii-in-commits.sh`'s always-on credential-value scan already denies a `git commit` containing a credential-shaped string, independent of file path or `.gitignore` | `[verified: claude/.claude/hooks/deny-pii-in-commits.sh:400]` | row 5 |
| 10 | The log directory's real permissions (`755` dir / `644` file) undo `install.sh`'s `700 ~/.claude` hardening for this file specifically | `[verified: ciso-reviewer plan-review round 1 — empirical `ls -la`/`stat` on this machine's equivalent existing log]` | root |

Row 2 is load-bearing for the "what does the log actually contain" question
and is flagged again in `docs/permission-prompt-tracking.md`'s "Known
limitations" section — a follow-up pass after the first real capture should
confirm the payload shape and, if useful fields exist beyond raw JSON,
consider adding a small parser (not a hook change). Rows 7-10 are the
plan-review round-1 findings that changed the design between the first and
current draft — see "Sensitive-data handling" above.

## Critical files

**New:**
- `claude/.claude/hooks/track-permission-prompts.sh` — the hook. Shebang +
  `# hook-class: informational` header matching
  `advance-past-commit-stall.sh`'s full convention (Dispatch / Known-gaps /
  Kill-switch sections, not just the one-line hook-class comment) — must
  state in the header, not only in the plan: (a) this hook logs, gates
  nothing, and has no deny primitive; (b) the defense-in-depth self-check
  only reaches `hook_event_name`, not a `permission_prompt` sub-type field
  (ledger row 2 — undocumented), so a future Claude Code version firing
  `Notification` more broadly than expected would pollute the log with
  unrelated notification types until this header comment is revisited.
  Behavior: reads stdin once via `INPUT=$(cat) || exit 0`; self-checks
  `.hook_event_name == "Notification"` (`_lib_jq`, defense-in-depth, ledger
  item 6) and exits 0 otherwise; resolves `CONFIG_DIR` via `_lib_config_dir`
  (source `_lib.sh`, same relative-path pattern every other hook uses) and
  exits 0 if unresolved; exits 0 if `_lib_permission_prompt_tracking_active`
  (new zero-argument helper, below) doesn't hold; otherwise runs the raw
  input through the new `_lib_redact_credential_shaped_strings` (ledger row
  7/8), merges in `{"logged_at": <ISO 8601 via _lib_jq, not a bare `jq`
  spawn>}`, appends to `$CONFIG_DIR/.permission-prompt-log.jsonl` via
  `>> ... 2>/dev/null || true` (fail-open), then `chmod 600
  "$CONFIG_DIR/.permission-prompt-log.jsonl" 2>/dev/null || true` (ledger
  row 10).
- `claude/.claude/hooks/tests/test_track_permission_prompts.py` — sentinel
  present/absent, `CLAUDE_CONFIG_DIR` honored, non-`Notification` event
  ignored, malformed/non-JSON stdin doesn't crash the hook (fail-open),
  log-write failure (e.g. unwritable `CONFIG_DIR`) doesn't raise a non-zero
  exit, **appended line equals `<input JSON> + {logged_at}` with no other
  transformation** (the schema-tolerant-passthrough design decision gets
  its own named assertion, not just implied by "sentinel present"),
  **a credential-shaped string in the input is redacted before append**
  (a fixture mirroring `redact-credential-values.sh`'s own test pattern —
  this is the test that would have caught round-1's missing redaction),
  and **the log file's mode is `0o600` after append**. No existing
  `helpers.py` runner fits a pure side-effect/no-output hook
  (`run_hook`/`run_hook_advisory`/`run_hook_stop`/`run_hook_session_start`
  all parse a stdout decision payload this hook never emits) — invoke via
  `subprocess.run` directly in this test file; don't add a
  `run_hook_side_effect` abstraction to `helpers.py` for one caller.
- `docs/permission-prompt-tracking.md` — modeled on
  `docs/commit-stall-block.md`'s structure (Activation / What the hook does
  / How to disable / Log location and format / Known limitations). Must
  state, as named limitations rather than guarantees: the row-2 schema
  caveat (show 1-2 real example log lines once available per Verification
  step 4); that redaction is regex-shape-based and can miss a credential
  with no fixed shape (same caveat `redact-credential-values.sh`'s own
  header already carries — inherited, not new); that "never leaves the
  machine" means "no committed-git path" (backed by `.gitignore` +
  `deny-pii-in-commits.sh`'s independent scan), not "immune to local
  backup/sync tooling"; and the manual-trim retention policy (no automatic
  rotation — see Approach).

**Modified:**
- `claude/.claude/hooks/_lib.sh` — add two functions:
  - `_lib_permission_prompt_tracking_active` (zero-arity — no repo-root
    parameter, since item 5 means there's no per-repo optout to check
    against; explicit `# Usage:` comment per this file's convention),
    same shape as `_lib_autonomous_shipping_active` minus the optout
    check.
  - `_lib_redact_credential_shaped_strings <json>` — extracted from
    `redact-credential-values.sh` lines 39-71 (pattern assembly including
    optional `credential-value-patterns.md` additions, then the `walk(...)`
    call); echoes redacted JSON, or the original input on any
    resolution/parse failure (fail-open, matching the existing call site's
    posture). Two callers after this change (the existing hook + the new
    one) meets this repo's own stated DRY threshold for extracting shared
    logic rather than duplicating it.
- `claude/.claude/hooks/redact-credential-values.sh` — replace the inlined
  pattern-assembly-and-walk block (lines 39-71) with a call to the new
  `_lib_redact_credential_shaped_strings`. No behavior change: same
  regexes, same user-additions file, same fail-open posture — this is
  extraction, not a redesign.
- `claude/.claude/settings.json` — add a new top-level `"Notification"` key
  (none exists today — confirmed via `python3 -c "import json; print(json.load(open('settings.json'))['hooks'].keys())"`,
  which lists `SessionStart, SubagentStart, UserPromptSubmit, Stop,
  SessionEnd, PreToolUse, PostToolUse` and no `Notification`):
  ```json
  "Notification": [
    {
      "matcher": "permission_prompt",
      "hooks": [{ "type": "command", "command": "~/.claude/hooks/track-permission-prompts.sh" }]
    }
  ]
  ```
  Command path stays `~`-relative, matching every existing entry (no
  existing entry uses `$CLAUDE_CONFIG_DIR` or a resolved path in
  `settings.json` itself — resolution happens inside the script).
- `.gitignore` — add `claude/.claude/.permission-prompt-log.jsonl` next to
  the other per-machine hook logs (`claude/.claude/.handoff-nudge.log` is at
  line 108 today).
- `README.md` — new TOC entry and `### Permission-prompt tracking` section
  under `## Configuration`, sibling to `### Autonomous shipping` (same
  shape: one-sentence symptom/behavior, driving hook + event, opt-in
  command, pointer to the new doc file), plus a new hooks-table row:
  `| \`track-permission-prompts.sh\` | — (Notification, \`informational\`, opt-in) | ... |`.

**Reuse, not reimplement:** `_lib_config_dir` (path resolution),
`_lib.sh`'s existing sentinel-check shape, `_lib_jq` (hang backstop on every
jq spawn — the hook must not use a bare `jq` call anywhere), the credential
redaction walk (moved to `_lib.sh` rather than duplicated), and the
`>> ... 2>/dev/null || true` append idiom.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` — full suite green, including
   the new test file and `redact-credential-values.sh`'s existing tests
   (must still pass unchanged after the extraction — the redaction/test
   fixture assertions there are the regression check that the `_lib.sh`
   extraction preserved exact behavior).
2. `../../../.venv/bin/ruff check claude/.claude/` — no new lint findings.
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
   both the new and the modified hook script clean per this repo's
   `.shellcheckrc` flags.
4. Manual smoke test: `touch ~/.claude/track-permission-prompts`, start a
   session, run a Bash command not covered by any allow rule, confirm the
   dialog appears, confirm one line was appended to
   `~/.claude/.permission-prompt-log.jsonl`, and **read that line** to
   confirm what Claude Code actually sent — this is the check that resolves
   assumption-ledger row 2 from `[unverified]` to a documented fact in
   `docs/permission-prompt-tracking.md`. In the same pass, confirm the
   file's mode is `600` (`stat -f %A` / `ls -la`) and, using a command whose
   arguments contain an obviously credential-shaped string, confirm the
   logged line shows `[REDACTED-CREDENTIAL]` rather than the raw value.
5. Confirm no-op behavior: without the sentinel, trigger a prompt, confirm
   the log file is not created/modified.
6. `claude-hook-review` and `/code-review` per this repo's standard pipeline
   for any `.claude/hooks/*.sh` + `settings.json` hook-entry change —
   `claude-hook-review` in particular should re-check the redaction
   extraction, since it's now security-load-bearing for a second call site.

## Rollback

Fully additive and inert-by-default: reverting the commit (or just removing
the `Notification` key from `settings.json`) removes all new behavior for
every stow user immediately, with no data migration to undo — the only
persistent artifact is the opted-in developer's own local log file, which a
revert doesn't touch and doesn't need to.

## Out of scope

- Any built-in analysis/reporting command (a `/permission-prompt-report`
  slash command, aggregation script) — this plan only captures the data.
  Once the real payload shape is confirmed (Verification step 4), a
  follow-up can add a small parser/report script against the log.
- Changing `permissions.allow` rules based on findings — a separate,
  data-driven follow-up once the tracker has run for a while.
- Automatic log rotation/retention (see Approach's "Retention" note) — the
  window-size decision needs the engineer's input, not a value this plan
  picks unilaterally.
