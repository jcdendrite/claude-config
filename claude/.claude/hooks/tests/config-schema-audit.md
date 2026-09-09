# config-keys.psv schema provenance

For every one of the 15 keys in `config-keys.psv`, this table traces the
actual current call site(s), derives the `resolution`,
`legacy-probe-on-resolution-failure`, and `legacy-import-locations` column
values from that code, and records the current fail-direction on a
`_lib_config_dir`/`config_dir()` resolution failure. The five
enforcement-critical keys (`worktree_required`, `autonomous_shipping`,
`round_consult_gate`, `commit_stall_block`, `authorization_boundary_restore`)
are each confirmed individually below across all three schema columns — not
asserted only in aggregate.

Column legend:
- **Call site(s)**: where the key is read today, file:line.
- **Resolution (derived)**: `config-dir` (resolved config dir only) or
  `config-dir-or-home` (union with the literal `$HOME/.claude`).
- **Legacy-probe-on-resolution-failure (derived)**: whether the call site
  still probes a raw `$HOME/.claude/<file>` path when `_lib_config_dir`
  itself fails to resolve.
- **Legacy-import-locations (derived)**: `config-dir` or
  `config-dir-and-home` — see `config-keys.psv`'s header comment for the
  full rule this column follows; briefly, a key needs
  `config-dir-and-home` when its value could physically exist only at
  `$HOME/.claude` due to `install.sh`'s pre-migration writer bug
  (`configure_machine_level_opt_ins` always wrote `$HOME/.claude/...`
  regardless of `CLAUDE_CONFIG_DIR`), or (`pr_cost_disclosure` only) because
  its pre-migration resolution picks exactly one of two locations with no
  union, so a diverged user's real governing value could be at either.
- **Fail direction on resolution failure**: what the call site does today
  when `_lib_config_dir`/`config_dir()` itself cannot resolve (relative
  `CLAUDE_CONFIG_DIR`, or empty/unset `$HOME` with `CLAUDE_CONFIG_DIR` also
  unset).

## Enforcement-critical keys (individually confirmed, all three columns)

### `worktree_required`

- Call site: `claude/.claude/hooks/_lib.sh`'s `_lib_worktree_enforcement_active`
  (delegated to by `require-worktree-for-git-writes.sh:218` and
  `require-worktree-for-file-writes.sh:134`). After its own
  committed-repo-sentinel check, this function calls `_config_enabled
  worktree_required` — a single call, no raw `[ -f ]` probe at the call
  site itself.
- Resolution: **config-dir-or-home**. `_config_enabled` (via `_config_value`
  in `_config.sh`) reads `worktree_required`'s `resolution` column from
  `config-keys.psv` and unions the resolved config dir with the literal
  `$HOME/.claude` — a union, not a swap (`_config_value`'s own comment:
  "an explicit `false` row in the config dir's state file must not defeat a
  `true` produced by `$HOME/.claude`'s legacy file").
- Legacy-probe-on-resolution-failure: **true**. `_config_value` reads this
  from `worktree_required`'s schema row and, when the primary config-dir
  resolution itself fails, falls through to `$HOME/.claude` unconditionally
  — this is what makes the key resolve even when config-dir resolution
  fails outright. `worktree_required` is the only key whose row carries
  `true` here (`_config_value`'s own comment on that line).
- Legacy-import-locations: **config-dir-and-home**. The union above already
  reads both locations at runtime, so import naturally checks both too.
- Fail direction on resolution failure: falls through to the raw `$HOME`
  probe, matching every other unresolvable-config-dir case for this key —
  never a hard deny, never silently skips enforcement.

### `autonomous_shipping`

- Call site: `_lib.sh`'s `_lib_autonomous_shipping_active` /
  `_lib_autonomous_shipping_sentinel_present` (delegated to by
  `autonomous-shipping-active.sh` and `advance-past-commit-stall.sh:67`'s
  fast path). `_lib_autonomous_shipping_sentinel_present` is a thin
  zero-arity wrapper over `_config_enabled autonomous_shipping`, with no
  raw-path probe of its own.
- Resolution: **config-dir-or-home**. `_config_enabled` reads this from
  `autonomous_shipping`'s `resolution` column and unions the resolved
  config dir with the literal `$HOME/.claude`, the same generic union
  `_config_value` runs for any `config-dir-or-home` key.
- Legacy-probe-on-resolution-failure: **false**. `autonomous_shipping`'s
  schema row carries `false` here, so when the primary config-dir
  resolution itself fails, `_config_value` returns exit 2 rather than
  falling through to a raw `$HOME` probe — `_lib_autonomous_shipping_active`
  then treats that nonzero exit as "not active" via `||`. Granting
  autonomous shipping on a resolution failure would be the wrong direction
  for a mechanism that removes a human checkpoint, so this asymmetry with
  `worktree_required` is intentional, not an oversight (see `_lib.sh`'s own
  comment on `_lib_autonomous_shipping_active`).
- Legacy-import-locations: **config-dir-and-home**. Same reasoning as
  `worktree_required` — the runtime union already reads both locations.
- Fail direction on resolution failure: hard-denies shipping (returns
  "not active"), never falls through to a raw probe.

### `round_consult_gate`

- Call site: `_lib.sh`'s `_lib_round_consult_gate_disabled` (delegated to by
  `require-architect-consult.sh:67`).
- Resolution: **config-dir**. `config_dir=$(_lib_config_dir) || return 1`,
  then `[ -f "$config_dir/.round-consult-gate-disabled" ]` — no `$HOME`
  fallback arm exists.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists
  in this function at all.
- Legacy-import-locations: **config-dir**. Never auto-written by
  `install.sh` (not a machine-promptable row in the pre-migration
  inventory) — a user hand-toggles it directly at wherever this resolution
  already reads, so there is no divergent-write-path recovery need.
- Fail direction on resolution failure: `return 1` — the gate stays armed
  (fails toward requiring the consult), the safe direction for a gate this
  key's presence disables.

### `commit_stall_block`

- Call site: `advance-past-commit-stall.sh:52,60`. `CONFIG_DIR=$(_lib_config_dir)
  || exit 0` resolves the config dir directly (needed later in the script
  for its log/state paths), then `_config_enabled commit_stall_block ||
  exit 0` gates the rest of the hook — no raw `[ -f ]` probe against
  `$CONFIG_DIR` for this key at the call site itself.
- Resolution: **config-dir**. `commit_stall_block`'s schema row carries
  `config-dir`, so `_config_value` reads only the already-resolved
  `CONFIG_DIR` — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  the call site's own `exit 0` on `_lib_config_dir` failure means
  `_config_enabled` is never even reached on a resolution failure.
- Legacy-import-locations: **config-dir**. Same reasoning as
  `round_consult_gate` — never auto-written by `install.sh`.
- Fail direction on resolution failure: `exit 0` (allow the hook's own
  logic to proceed as if not blocked) — an unresolvable config dir means
  "no kill-switch location to check," so the hook does not block on the
  strength of an unreadable kill switch.

### `authorization_boundary_restore`

- Call site: `restore-authorization-boundary-on-compact.sh:43`:
  `_config_enabled authorization_boundary_restore || exit 0` — no raw
  `[ -f ]` probe at the call site itself.
- Resolution: **config-dir**. `authorization_boundary_restore`'s schema row
  carries `config-dir`, so `_config_value` reads only the resolved config
  dir — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  `_config_enabled`'s underlying `_lib_config_dir` failure propagates as
  exit 2, which this hook's own `|| exit 0` treats as "not active."
- Legacy-import-locations: **config-dir**. Never inventoried in
  `SENTINEL_INVENTORY` at all pre-migration (Context section: "missing from
  `SENTINEL_INVENTORY` today") — a user hand-toggles it directly, so there
  is no install.sh writer bug to recover from.
- Fail direction on resolution failure: `exit 0` (advisory hook; an
  unresolvable config dir leaves nothing to restate, so it no-ops rather
  than guessing).

## Remaining ten keys

### `permission_prompt_tracking`

- Call site: `track-permission-prompts.sh:49`.
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`;
  the kill-switch/opt-in check reads only `$CONFIG_DIR`.
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir-and-home**. One of the six
  pre-migration machine-promptable rows — `install.sh`'s
  `configure_machine_level_opt_ins` always wrote
  `$HOME/.claude/track-permission-prompts` regardless of
  `CLAUDE_CONFIG_DIR`, while this key's own read resolution is
  config-dir-only, so a diverged user's opt-in may be stranded at
  `$HOME/.claude`.
- Fail direction on resolution failure: `exit 0` (hook takes no action;
  tracking silently doesn't fire).

### `error_mode_nudge`

- Call site: `nudge-error-mode-analysis.sh:61,88`. `CONFIG_DIR=$(_lib_config_dir)
  || exit 0` resolves the config dir directly (needed later for its
  marker/checkpoint/log paths), then `_config_enabled error_mode_nudge ||
  exit 0` gates the rest of the hook — no raw `[ -f ]` probe against
  `$CONFIG_DIR` for this key at the call site itself.
- Resolution: **config-dir**. `error_mode_nudge`'s schema row carries
  `config-dir`, so `_config_value` reads only the already-resolved
  `CONFIG_DIR` — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  the call site's own `exit 0` on `_lib_config_dir` failure means
  `_config_enabled` is never even reached on a resolution failure.
- Legacy-import-locations: **config-dir-and-home**. Same
  machine-promptable-writer-bug reasoning as `permission_prompt_tracking`.
- Fail direction on resolution failure: `exit 0` (nudge doesn't fire).

### `cost_ledger_recording`

- Call site: `claude/.claude/scripts/transcript-analysis.py:6607` (`config_dir()
  / ".cost-ledger-enabled"`, `_cost_ledger_report`'s `--record` path).
- Resolution: **config-dir**. Python's `config_dir()` has no separate
  `$HOME` union arm — it resolves `CLAUDE_CONFIG_DIR` when absolute, else
  `Path.home() / ".claude"`, and the sentinel check reads only that one
  resolved path.
- Legacy-probe-on-resolution-failure: **false**. A `ValueError` from
  `config_dir()` (relative `CLAUDE_CONFIG_DIR`) propagates to the caller's
  existing `try/except` (`transcript-analysis.py:6577-6584`), which prints
  and exits — no fallback probe.
- Legacy-import-locations: **config-dir-and-home**. Same
  machine-promptable-writer-bug reasoning — `.cost-ledger-enabled` was one
  of the six rows `install.sh`'s old writer always wrote to
  `$HOME/.claude`.
- Fail direction on resolution failure: exits 1 with a diagnostic (`cost-ledger:
  {exc}`) — recording is refused, matching this key's presence-enables
  polarity (an unresolvable config dir is not treated as "enabled").

### `pr_cost_recording`

- Call site: `claude/.claude/scripts/transcript-analysis.py:7823,7838` (per-account
  `account_config_dir / ".pr-cost-enabled"` check inside the `--all-accounts`
  loop; single-account path resolves the analogous way against the
  environment).
- Resolution: **config-dir**. Each loop iteration resolves against its own
  `account_config_dir` (no cross-account union); the single-account path
  resolves against the environment-derived `config_dir()` alone.
- Legacy-probe-on-resolution-failure: **false**. A `ValueError` from
  `_pr_cost_ledger_path` propagates to the existing `except ValueError`
  print-and-exit at that call site.
- Legacy-import-locations: **config-dir-and-home**. Same
  machine-promptable-writer-bug reasoning — `.pr-cost-enabled` was the
  sixth of the six rows affected.
- Fail direction on resolution failure: exits with a diagnostic; recording
  is refused (matches `cost_ledger_recording`'s direction).

### `pr_cost_disclosure`

- Call site: `claude/.claude/scripts/pr-cost-section.sh:16-24`.
- Resolution: **config-dir**. `config_dir=$(_lib_config_dir) || exit 1`;
  the mode check reads only `$config_dir/pr-cost-disclosure`.
- Legacy-probe-on-resolution-failure: **false**. No fallback probe; a
  resolution failure exits 1 (disabled) directly.
- Legacy-import-locations: **config-dir-and-home**, for a different reason
  than the machine-promptable group above: this key was never
  machine-promptable (its pre-migration `_report_account_sentinel`
  resolution picks `$CLAUDE_CONFIG_DIR` when set and absolute, else
  `$HOME/.claude`, **never both** — `install.sh:611-615`). A diverged
  user's real governing value — "whichever location was actually read at
  the time they opted in" — could be at either location depending on
  history, so the one-time import checks both and, on disagreement, the
  resolved-config-dir copy wins (it is the more specific and more likely
  currently-governing location for a diverged-`CLAUDE_CONFIG_DIR` user, and
  `_report_account_sentinel` itself already prefers it whenever
  `CLAUDE_CONFIG_DIR` is set and absolute).
- Fail direction on resolution failure: exits 1 (disabled) — matches the
  content-matches key's own default-disabled polarity.

### `pr_description_tighten_prose`

- Call site: `claude-skills/skills/pr-description/SKILL.md:92,103` (prose,
  instructing the agent to check `$config_dir/pr-description-tighten-prose-optout`
  "exactly as the Cost section's gate above" — i.e. the same single
  `_lib_config_dir`-equivalent resolution `pr-cost-section.sh` uses, with no
  separate `$HOME` union).
- Resolution: **config-dir**.
- Legacy-probe-on-resolution-failure: **false**. No fallback path is
  described.
- Legacy-import-locations: **config-dir**. Never machine-promptable and,
  unlike `pr_cost_disclosure`, the two-location recovery-check rationale
  above applies specifically to that key, not this one — a user opts into
  (or out of) prose tightening by hand-creating the file directly at
  wherever this resolution already reads, with no install.sh writer ever
  touching it.
- Fail direction on resolution failure: not explicitly specced in prose
  today; `config-get.sh`'s exit 3 (unresolvable) is documented in
  `pr-description/SKILL.md:103`'s edit to skip the pass — failing toward
  off, the same direction every other config-dir-only key takes on
  resolution failure.

### `handoff_nudge`

- Call site: `nudge-handoff-near-context-cap.sh:91,392` (two read sites —
  a script-level `CONFIG_DIR` resolution, and `_config_enabled
  handoff_nudge` gating the nudge itself, both inside `run_check_mode`).
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || CONFIG_DIR=""`
  resolves the config dir directly (needed later for its transcript/session
  paths); `run_check_mode`'s own early `[ -n "$CONFIG_DIR" ] || check_refuse
  "config-dir-unresolved"` (line 314) already refuses before `_config_enabled
  handoff_nudge` (line 392, later in the same function) is ever reached, so
  that call always sees an already-resolved config dir. `handoff_nudge`'s
  schema row carries `config-dir` — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  moot in practice here since `run_check_mode`'s early refusal already
  exits before `_config_enabled` runs on an unresolved config dir.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: the kill switch reads as absent
  (nudge behaves as enabled/undisabled) — an unresolvable config dir does
  not block a resolved-earlier nudge.

### `consume_durable_continuity`

- Call site: `consume-durable-continuity-file-on-read.sh:101,106`.
  `CONFIG_DIR=$(_lib_config_dir) || exit 0` resolves the config dir
  directly (needed later for its handoff/brief glob paths), then
  `_config_enabled consume_durable_continuity || exit 0` gates the rest of
  the hook — no raw `[ -f ]` probe against `$CONFIG_DIR` for this key at
  the call site itself.
- Resolution: **config-dir**. `consume_durable_continuity`'s schema row
  carries `config-dir`, so `_config_value` reads only the already-resolved
  `CONFIG_DIR` — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  the call site's own `exit 0` on `_lib_config_dir` failure means
  `_config_enabled` is never even reached on a resolution failure.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: `exit 0` — the hook takes no
  action (does not consume/inject the continuity file) rather than guess.

### `session_title_from_branch`

- Call site: `set-session-title-from-branch.sh:86` (machine check only —
  the separate repo-scope check at `:147` reads
  `$MAIN_WORKTREE_ROOT/.claude/session-title-disabled`, a committed repo
  marker, and is explicitly out of this migration's scope: the repo check
  stays a file). The machine check is a single `_config_enabled
  session_title_from_branch || exit 0` with no raw `[ -f ]` probe of its
  own — no separate `CONFIG_DIR` resolution even runs at this call site,
  since `_config_enabled` resolves it internally.
- Resolution: **config-dir**. `session_title_from_branch`'s schema row
  carries `config-dir`, so `_config_value` reads only the resolved config
  dir — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  `_config_enabled`'s underlying `_lib_config_dir` failure propagates as
  exit 2, which this hook's own `|| exit 0` treats as "run unchanged"
  (today's auto-titler behavior).
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: `exit 0` — "An unresolvable config
  dir leaves no kill-switch location to check, so this hook fails open
  (today's auto-titler behavior) rather than guess" (the hook's own
  comment).

### `round_consult_round2_pilot`

- Call site: `_lib.sh`'s `_lib_reviewer_round_state_cap` (delegated to by
  `require-architect-consult.sh` and `log-reviewer-round.sh`, both reading
  the cap it returns). `config_dir=$(_lib_config_dir) && [ -f
  "$config_dir/.round-consult-round2-pilot" ]` — a single presence check, no
  `_config_enabled`/`_config_value` call at all. The schema row exists only
  for `install.sh`'s schema-driven reporter, not for this function's own
  enforcement.
- Resolution: **config-dir**. No `$HOME` union arm — `_lib_config_dir`
  alone.
- Legacy-probe-on-resolution-failure: **false**. No raw-path fallback probe
  exists; a `_lib_config_dir` failure short-circuits the `&&` and the
  function falls through to `printf '%s\n' "$_LIB_REVIEWER_ROUND_STATE_CAP"`
  (the default cap of 2).
- Legacy-import-locations: **config-dir**. Never machine-promptable. Not
  part of pre-migration `SENTINEL_INVENTORY` — this key postdates the
  sentinel-file migration. A user hand-toggles it via `touch`.
- Fail direction on resolution failure: falls through to the default cap
  (`$_LIB_REVIEWER_ROUND_STATE_CAP`, currently 2) rather than the pilot's
  lowered cap of 1. This is the safe direction — an unresolvable config dir
  allows more review rounds before the gate fires, never fewer.
