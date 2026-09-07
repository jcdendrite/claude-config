# config-keys.psv schema provenance

For every one of the 14 keys in `config-keys.psv`, this table traces the
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
  `require-worktree-for-file-writes.sh:134`).
- Resolution: **config-dir-or-home**. `_lib_worktree_enforcement_active`
  checks `$config_dir/worktree-required` first; only when that arm's own
  `_lib_config_dir` call fails does it fall through to a raw
  `[ -f "$HOME/.claude/worktree-required" ]` probe — a union, not a swap
  (`_lib.sh`'s own comment: "a machine-wide `worktree-required` armed before
  `CLAUDE_CONFIG_DIR` adoption must not silently go dark").
- Legacy-probe-on-resolution-failure: **true**. The fallback `[ -f
  "$HOME/.claude/worktree-required" ]` line runs unconditionally after the
  resolved-config-dir arm, independent of whether `_lib_config_dir` itself
  failed — this is what makes the key resolve even when config-dir
  resolution fails outright.
- Legacy-import-locations: **config-dir-and-home**. The union above already
  reads both locations at runtime, so import naturally checks both too.
- Fail direction on resolution failure: falls through to the raw `$HOME`
  probe, matching every other unresolvable-config-dir case for this key —
  never a hard deny, never silently skips enforcement.

### `autonomous_shipping`

- Call site: `_lib.sh`'s `_lib_autonomous_shipping_active` /
  `_lib_autonomous_shipping_sentinel_present` (delegated to by
  `autonomous-shipping-active.sh` and `advance-past-commit-stall.sh:67`'s
  fast path).
- Resolution: **config-dir-or-home**.
  `_lib_autonomous_shipping_sentinel_present` checks `[ -f
  "$config_dir/autonomous-shipping-required" ] || [ -f
  "$HOME/.claude/autonomous-shipping-required" ]` — a union over both
  locations once `config_dir` is already resolved.
- Legacy-probe-on-resolution-failure: **false**. Unlike `worktree_required`,
  `_lib_autonomous_shipping_active` does `config_dir=$(_lib_config_dir) ||
  return 1` — it bails immediately on a resolution failure and never reaches
  the raw `$HOME` probe. Granting autonomous shipping on a resolution
  failure would be the wrong direction for a mechanism that removes a human
  checkpoint, so this asymmetry with `worktree_required` is intentional, not
  an oversight (see `_lib.sh`'s own comment on `_lib_autonomous_shipping_active`).
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

- Call site: `advance-past-commit-stall.sh:52,56`.
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`,
  then `[ -f "$CONFIG_DIR/.commit-stall-block-disabled" ] && exit 0` — no
  `$HOME` fallback arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists.
- Legacy-import-locations: **config-dir**. Same reasoning as
  `round_consult_gate` — never auto-written by `install.sh`.
- Fail direction on resolution failure: `exit 0` (allow the hook's own
  logic to proceed as if not blocked) — an unresolvable config dir means
  "no kill-switch location to check," so the hook does not block on the
  strength of an unreadable kill switch.

### `authorization_boundary_restore`

- Call site: `restore-authorization-boundary-on-compact.sh:39-40`.
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`,
  then `[ -f "$CONFIG_DIR/.authorization-boundary-disabled" ] && exit 0` —
  no `$HOME` fallback arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists.
- Legacy-import-locations: **config-dir**. Never inventoried in
  `SENTINEL_INVENTORY` at all pre-migration (Context section: "missing from
  `SENTINEL_INVENTORY` today") — a user hand-toggles it directly, so there
  is no install.sh writer bug to recover from.
- Fail direction on resolution failure: `exit 0` (advisory hook; an
  unresolvable config dir leaves nothing to restate, so it no-ops rather
  than guessing).

## Remaining nine keys

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

- Call site: `nudge-error-mode-analysis.sh:61,87`.
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`;
  `[ ! -f "$CONFIG_DIR/.error-mode-nudge-enabled" ]` gates the rest of the
  hook.
- Legacy-probe-on-resolution-failure: **false**.
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

- Call site: `nudge-handoff-near-context-cap.sh:91,422,523` (two read
  sites).
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || CONFIG_DIR=""`
  (empty-string fallback, not a raw-path probe); both kill-switch checks
  read `$CONFIG_DIR/.handoff-nudge-disabled`, which is simply never present
  when `CONFIG_DIR` is empty.
- Legacy-probe-on-resolution-failure: **false**. The empty-`CONFIG_DIR`
  fallback is not a `$HOME` probe — it makes the subsequent `[ -f
  "$CONFIG_DIR/..." ]` checks structurally false, which is a fail-open
  degrade, not a legacy-file probe.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: the kill switch reads as absent
  (nudge behaves as enabled/undisabled) — an unresolvable config dir does
  not block a resolved-earlier nudge.

### `consume_durable_continuity`

- Call site: `consume-durable-continuity-file-on-read.sh:93,95`.
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`;
  `[ -f "$CONFIG_DIR/.consume-durable-continuity-disabled" ]`.
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: `exit 0` — the hook takes no
  action (does not consume/inject the continuity file) rather than guess.

### `session_title_from_branch`

- Call site: `set-session-title-from-branch.sh:85-86` (machine check only —
  the separate repo-scope check at `:151` reads
  `$MAIN_WORKTREE_ROOT/.claude/session-title-disabled`, a committed repo
  marker, and is explicitly out of this migration's scope: the repo check
  stays a file).
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`;
  `[ -f "$CONFIG_DIR/.session-title-disabled" ] && exit 0`.
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: `exit 0` — "An unresolvable config
  dir leaves no kill-switch location to check, so this hook fails open
  (today's auto-titler behavior) rather than guess" (the hook's own
  comment).
