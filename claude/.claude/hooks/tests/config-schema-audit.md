# config-keys.psv schema provenance

For every one of the 21 keys in `config-keys.psv`, this table traces the
actual current call site(s), derives the `resolution`,
`legacy-probe-on-resolution-failure`, and `legacy-import-locations` column
values from that code, and records the current fail-direction on a
`_lib_config_dir`/`config_dir()` resolution failure. The four
enforcement-critical keys (`worktree_required`, `round_consult_gate`,
`commit_stall_block`, `authorization_boundary_restore`)
are each confirmed individually below across all three schema columns — not
asserted only in aggregate.

Column legend:
- **Call site(s)**: where the key is read today, file plus function or section.
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
  (delegated to by `require-worktree-for-git-writes.sh` and
  `require-worktree-for-file-writes.sh`). After its own
  committed-repo-sentinel check, this function calls `_config_enabled
  worktree_required` — a single call, no raw `[ -f ]` probe at the call
  site itself.
- Resolution: **config-dir-or-home**. `_config_enabled` (via `_config_lookup`
  and `_config_resolve` in `_config.sh`) reads `worktree_required`'s
  `resolution` column from `config-keys.psv` and unions the resolved config
  dir with the literal `$HOME/.claude` — a union, not a swap
  (`_config_resolve`'s header comment: "an explicit `false` row in the config
  dir's state file must not defeat a `true` produced by $HOME/.claude's
  legacy file").
- Legacy-probe-on-resolution-failure: **true**. `_config_resolve` reads this
  from `worktree_required`'s schema row. When config-dir resolution fails
  and `$HOME` is non-empty, resolution reads `$HOME/.claude` alone: that
  directory's state-file row, then its legacy `worktree-required` file, then
  the schema default. The key therefore still resolves. With `$HOME` unset
  or empty there is no location to read and `_config_resolve` returns 2 (see
  the fail direction below). `worktree_required` is the only key whose row
  carries `true` here.
- Legacy-import-locations: **config-dir-and-home**. The union above already
  reads both locations at runtime, so import naturally checks both too.
- Fail direction on resolution failure: depends on `$HOME`. The causes of
  the failure are a relative `CLAUDE_CONFIG_DIR`, or `CLAUDE_CONFIG_DIR`
  unset or empty with `$HOME` unset or empty.
  - With `$HOME` non-empty, resolution reads `$HOME/.claude` alone (state-file
    row, then legacy file, then default), so the key still resolves.
  - With `$HOME` unset or empty there is no location, so `_config_resolve`
    returns exit 2 and `_lib_worktree_enforcement_active` treats the
    machine-level key as not enforced. A committed `.claude/worktree-required`
    still enforces.

### `round_consult_gate`

- Call site: `_lib.sh`'s `_lib_round_consult_gate_disabled` (delegated to by
  `require-architect-consult.sh`). `_config_enabled round_consult_gate`,
  with an explicit `case "$?"` distinguishing exit 1 (disabled) from every
  other exit (armed) — no `$HOME` fallback arm exists.
- Resolution: **config-dir**. No `$HOME` union arm — `_lib_config_dir`
  alone.
- Legacy-probe-on-resolution-failure: **false**. No raw-path fallback probe
  exists in this function at all; every nonzero exit from `_config_enabled`
  other than 1 (disabled) falls through the `case`'s `*)` arm to `return 1`
  (armed).
- Legacy-import-locations: **config-dir**. Never auto-written by
  `install.sh` (not a machine-promptable row in the pre-migration
  inventory) — a user hand-toggles it directly at wherever this resolution
  already reads, so there is no divergent-write-path recovery need.
- Fail direction on resolution failure: `return 1` — the gate stays armed
  (fails toward requiring the consult), the safe direction for a gate this
  key's presence disables.

### `commit_stall_block`

- Call site: `advance-past-commit-stall.sh`. `CONFIG_DIR=$(_lib_config_dir)
  || exit 0` resolves the config dir directly (needed later in the script
  for its log/state paths), then `_config_enabled commit_stall_block` gates
  the rest of the hook via an explicit `case "$?"` — only exit 1
  (explicitly disabled) exits; every other code falls through and the hook
  proceeds — no raw `[ -f ]` probe against `$CONFIG_DIR` for this key at the
  call site itself.
- Resolution: **config-dir**. `commit_stall_block`'s schema row carries
  `config-dir`, so `_config_value` reads only the already-resolved
  `CONFIG_DIR` — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  the call site's own `exit 0` on `_lib_config_dir` failure means
  `_config_enabled` is never even reached on a config-dir resolution
  failure.
- Legacy-import-locations: **config-dir**. Same reasoning as
  `round_consult_gate` — never auto-written by `install.sh`.
- Fail direction: **two distinct failure modes, two distinct directions**.
  An unresolvable config dir (the `CONFIG_DIR=$(_lib_config_dir) || exit 0` line's own failure) exits
  0 before `commit_stall_block`'s schema row is ever read — "no kill-switch
  location to check," so the hook does not block on the strength of an
  unreadable kill switch. Config-keys.psv itself being unreadable (exit 3
  from the later `_config_enabled` call), or readable but missing
  `commit_stall_block`'s own row (exit 4), is different: `commit_stall_block`
  is one of the four enforcement-critical keys, and its fail-closed
  direction is "stays armed," the same class as `round_consult_gate` and
  `authorization_boundary_restore` — the `case "$?"` at the call site keeps
  the hook running rather than collapsing exit 3 or 4 into the same "off"
  outcome as exit 1 (explicitly disabled).
- This exit-3/4 direction is observable end to end: with `config-keys.psv`
  unreadable or missing `commit_stall_block`'s row, the hook still fires
  even when `claude-config.toml` carries `commit_stall_block = false`,
  because the key stays armed by design. The exit-3 and exit-4 case tests
  in `test_advance_past_commit_stall.py` pin it with such a row, and
  `docs/commit-stall-block.md` states the gap.

### `authorization_boundary_restore`

- Call site: `restore-authorization-boundary-on-compact.sh`: a bare
  `_config_enabled authorization_boundary_restore` whose status selects a
  `case`, with no raw `[ -f ]` probe at the call site itself.
- Resolution: **config-dir**. `authorization_boundary_restore`'s schema row
  carries `config-dir`, so `_config_value` reads only the resolved config
  dir — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  `_config_enabled`'s underlying `_lib_config_dir` failure propagates as
  exit 2, which this hook's `case` treats as "skip the boundary text."
- Legacy-import-locations: **config-dir**. Never inventoried in
  `SENTINEL_INVENTORY` at all pre-migration (Context section: "missing from
  `SENTINEL_INVENTORY` today") — a user hand-toggles it directly, so there
  is no install.sh writer bug to recover from.
- Fail direction on resolution failure: the boundary text is skipped
  (advisory hook; an unresolvable config dir leaves no kill-switch location
  to check, so it omits the text rather than guessing). The message has a
  second part, the autonomous-shipping statement, which depends only on the
  repo root guard and not on this key, so this key's kill switch silences
  only the boundary text. An unresolvable config dir therefore skips the
  boundary text and still emits the statement for an eligible root.

## Remaining seventeen keys

### `permission_prompt_tracking`

- Call site: `track-permission-prompts.sh`, gated by `_lib_permission_prompt_tracking_active`
  in `_lib.sh`.
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`;
  the opt-in check is `_config_enabled permission_prompt_tracking`, which
  reads only the resolved config dir.
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir-and-home**. One of the five
  pre-migration machine-promptable rows — `install.sh`'s
  `configure_machine_level_opt_ins` always wrote
  `$HOME/.claude/track-permission-prompts` regardless of
  `CLAUDE_CONFIG_DIR`, while this key's own read resolution is
  config-dir-only, so a diverged user's opt-in may be stranded at
  `$HOME/.claude`.
- Fail direction on resolution failure: `exit 0` (hook takes no action;
  tracking silently doesn't fire).

### `error_mode_nudge`

- Call site: `nudge-error-mode-analysis.sh`. `CONFIG_DIR=$(_lib_config_dir)
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

- Call site: `claude/.claude/scripts/transcript_analysis/cost_ledger.py`'s
  `_cost_ledger_report` (`--record` path), through
  `_config.config_enabled("cost_ledger_recording")`.
- Resolution: **config-dir**. `_config.config_enabled` has no separate
  `$HOME` union arm for this key — it reads only the one resolved config
  dir.
- Legacy-probe-on-resolution-failure: **false**. No fallback location is
  probed when the config dir cannot be resolved.
- Legacy-import-locations: **config-dir-and-home**. Same
  machine-promptable-writer-bug reasoning — `.cost-ledger-enabled` was one
  of the five rows `install.sh`'s old writer always wrote to
  `$HOME/.claude`.
- Fail direction on resolution failure: recording is refused and nothing is
  written, matching this key's presence-enables polarity (an unresolvable
  config dir is not treated as "enabled").

### `pr_cost_recording`

- Call site: `claude/.claude/scripts/transcript_analysis/pr_cost.py`'s
  `_pr_cost_report` (`--record` path), through
  `_config.config_enabled("pr_cost_recording", config_dir_override=account_config_dir)`
  once per root in `for root in roots`, in every mode, with
  `account_config_dir = root.parent`. `--all-accounts` only lets more than
  one root through and switches the not-opted-in outcome between skip and
  exit.
- Resolution: **config-dir**. Each iteration resolves against its own
  `account_config_dir` (no cross-account union), so the environment-derived
  `config_dir()` is never consulted at this call.
- Legacy-probe-on-resolution-failure: **false**. An unresolvable default
  config dir is refused earlier, in `scope._resolve_cost_roots` (called by
  `cmd_pr_cost`), which prints and exits 2 before `_pr_cost_report` runs.
  The `None` arm after the `config_enabled` call is defensive only, because
  `account_config_dir` is always a concrete directory. Neither path probes a
  fallback location.
- Legacy-import-locations: **config-dir-and-home**. Same
  machine-promptable-writer-bug reasoning — `.pr-cost-enabled` was the
  fifth of the five rows affected.
- Fail direction on resolution failure: recording is refused. An
  unresolvable default config dir exits 2 in `_resolve_cost_roots`. The
  defensive `None` arm skips the account under `--all-accounts` and exits 1
  otherwise.
- Second call site: `claude/.claude/scripts/transcript_analysis/routing_guard.py` (`routing_guard._read_opt_in`,
  the `routing-guard` subcommand's one read of this key, for both its capture and compute modes).
  - Resolution: **config-dir**. It always passes an explicit `config_dir_override`, so `config_value`
    reads that one directory with no `$HOME` union.
  - Legacy-probe-on-resolution-failure: **false**. The fallback probe runs only without an override.
  - Fail direction: **refused**. Either mode proceeds only when the read resolves opted in; any other
    outcome stops it.

### `pr_cost_disclosure`

- Call site: `claude/.claude/scripts/pr-cost-section.sh`.
- Resolution: **config-dir**. `mode=$(_config_value pr_cost_disclosure) || exit 1`;
  the mode check reads only the resolved config dir.
- Legacy-probe-on-resolution-failure: **false**. No fallback probe; a
  resolution failure exits 1 (disabled) directly.
- Legacy-import-locations: **config-dir-and-home**, for a different reason
  than the machine-promptable group above: this key was never
  machine-promptable (its pre-migration `_report_account_sentinel`
  resolution picks `$CLAUDE_CONFIG_DIR` when set and absolute, else
  `$HOME/.claude`, **never both** — in the pre-migration `install.sh`). A diverged
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

- Call site: `claude-skills/skills/pr-description/SKILL.md`'s "Prose
  tightening pass" section (prose, instructing the agent to run
  `config-get.sh pr_description_tighten_prose` — the same single
  config-dir resolution `_config_enabled` uses, with no separate `$HOME`
  union).
- Resolution: **config-dir**.
- Legacy-probe-on-resolution-failure: **false**. No fallback path is
  described.
- Legacy-import-locations: **config-dir**. Never machine-promptable and,
  unlike `pr_cost_disclosure`, the two-location recovery-check rationale
  above applies specifically to that key, not this one — a user opts into
  (or out of) prose tightening by hand-creating the file directly at
  wherever this resolution already reads, with no install.sh writer ever
  touching it.
- Fail direction on resolution failure: `config-get.sh`'s exit 3 (see that
  script's own header for the full exit-code table) skips the pass, per
  `pr-description/SKILL.md`'s "Prose tightening pass" section — failing
  toward off, the same direction every other config-dir-only key takes on
  resolution failure.

### `handoff_nudge`

- Call site: `nudge-handoff-near-context-cap.sh` (two read sites —
  a script-level `CONFIG_DIR` resolution, and `_config_enabled
  handoff_nudge` gating the nudge itself, both inside `run_check_mode`).
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || CONFIG_DIR=""`
  resolves the config dir directly (needed later for its transcript/session
  paths); `run_check_mode`'s own early `[ -n "$CONFIG_DIR" ] || check_refuse
  "config-dir-unresolved"` already refuses before `_config_enabled
  handoff_nudge` (later in the same function) is ever reached, so
  that call always sees an already-resolved config dir. `handoff_nudge`'s
  schema row carries `config-dir` — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  moot in practice here since `run_check_mode`'s early refusal already
  exits before `_config_enabled` runs on an unresolved config dir.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: the kill switch reads as absent
  (nudge behaves as enabled/undisabled) — an unresolvable config dir does
  not block a resolved-earlier nudge.

### `memory_audit_nudge`

- Call site: `nudge-memory-store-audit.sh` (two read sites — the
  script-level `CONFIG_DIR=$(_lib_config_dir)` resolution, and
  `_config_enabled memory_audit_nudge` gating the scan itself).
- Resolution: **config-dir**. `CONFIG_DIR=$(_lib_config_dir) || exit 0`
  resolves the config dir directly and exits open on failure before
  `_config_enabled memory_audit_nudge` (later in the same script)
  is ever reached, so that call always sees an already-resolved config dir.
  `memory_audit_nudge`'s schema row carries `config-dir` — no `$HOME`
  union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  moot in practice here since the script's own early `exit 0` already
  fires before `_config_enabled` runs on an unresolved config dir.
- Legacy-import-locations: **config-dir**. Never machine-promptable.
- Fail direction on resolution failure: the kill switch reads as absent
  (nudge behaves as enabled/undisabled) — an unresolvable config dir
  already exited the whole script before the kill switch is checked.
- Fail direction on schema-read failure: **enabled**. An unreadable
  `config-keys.psv`, an absent `memory_audit_nudge` row, or a truncated row
  keeps the nudge firing, since every `_config_enabled` status other than the
  disabled one falls through enabled.
- Malformed-value grammar: a non-bare value such as a quoted `"false"` is a
  skipped line, so the nudge keeps firing.

### `consume_durable_continuity`

- Call site: `consume-durable-continuity-file-on-read.sh`.
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

- Call site: `set-session-title-from-branch.sh` (machine check only —
  the separate repo-scope check reads
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

- Call site: `_lib.sh`'s `_lib_reviewer_round_state_cap`. A single
  `_config_enabled round_consult_round2_pilot` call with no raw probe.
- Resolution: **config-dir**. No `$HOME` union arm — `_lib_config_dir`
  alone.
- Legacy-probe-on-resolution-failure: **false**. No raw-path fallback probe
  exists; `_config_enabled`'s exit 2 (unresolvable config dir) falls to the
  default-cap arm, same as every other nonzero exit code. That arm prints
  `printf '%s\n' "$_LIB_REVIEWER_ROUND_STATE_CAP"` (the default cap of 2).
- Legacy-import-locations: **config-dir**. Never machine-promptable. Not
  part of pre-migration `SENTINEL_INVENTORY` — this key postdates the
  sentinel-file migration. A user hand-toggles it via `touch`.
- Fail direction on resolution failure: falls through to the default cap
  (`$_LIB_REVIEWER_ROUND_STATE_CAP`, currently 2) rather than the pilot's
  lowered cap of 1. This is the safe direction — an unresolvable config dir
  allows more review rounds before the gate fires, never fewer.

### `test_selection_tracking`

- Call site: `claude/.claude/scripts/select-tests.py`'s `record_selection`,
  called from `main()`.
- Resolution: **config-dir**. `record_selection` reads `config_enabled` and
  `config_dir()` directly, both of which resolve only the one config dir —
  no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  `record_selection`'s own `except ValueError` catches a `config_dir()`
  resolution failure and returns without falling through to a raw
  `$HOME/.claude` probe.
- Legacy-import-locations: **config-dir**. This key has no legacy
  predecessor at all: its `legacy-filename` and `legacy-polarity` columns
  are both empty, so there is no legacy file for `migrate-legacy-config.sh`
  to import from either location.
- Fail direction on resolution failure: on resolution failure, logging is
  skipped — matching this key's off-by-default polarity, an unresolvable
  config dir must not turn into a failed test run over a telemetry write.

### `architect_fix_plan_pilot`

- Call site: `code-review/SKILL.md`'s `code-review-fix-plan-pilot` region,
  which reads the key through `config-get.sh`.
- Resolution: **config-dir** (per account).
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir**. This key has no legacy
  predecessor at all, so there is no legacy file for
  `migrate-legacy-config.sh` to import from either location.
- Fail direction on resolution failure: only an observed exit 0 from
  `config-get.sh` enables the pilot. Any other outcome, including a call
  that yields no exit code, leaves it off, which leaves the default
  disposition procedure unchanged.

### `review_orchestrator_routing`

- Call site: `require-review-orchestrator-routing.sh`, through
  `_config_enabled`.
- Resolution: **config-dir** (per account).
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir**. This key has no legacy
  predecessor at all, so there is no legacy file for
  `migrate-legacy-config.sh` to import from either location.
- Fail direction: only exit 0 from `_config_enabled` routes. Any other
  exit, including an unresolvable config dir, an unreadable schema or a
  missing row, allows the call, which leaves the round inline.

### `architect_disposition_challenge_pilot`

- Call site: `code-review/SKILL.md`'s `code-review-disposition-challenge`
  region, which reads the key through `config-get.sh`.
- Resolution: **config-dir** (per account).
- Legacy-probe-on-resolution-failure: **false**.
- Legacy-import-locations: **config-dir**. This key has no legacy
  predecessor at all, so there is no legacy file for
  `migrate-legacy-config.sh` to import from either location.
- Fail direction on resolution failure: only an observed exit 0 from
  `config-get.sh` enables the challenge. Any other outcome, including a call
  that yields no exit code, leaves it off, which leaves the default
  disposition procedure unchanged.

### `plugin_bootstrap`

- Call site: `bootstrap-project-plugins.sh`, a single `_config_enabled
  plugin_bootstrap || exit 0` ahead of any repo read, then `CONFIG_DIR=$(_lib_config_dir)
  || exit 0` for the trust-record, registry, and audit-log paths.
- Resolution: **config-dir**. `_config_enabled` reads only the resolved
  config dir — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists;
  `_config_enabled`'s exit 2 (unresolvable config dir) takes the hook's own
  `|| exit 0`.
- Legacy-import-locations: **config-dir**. This key has no legacy
  predecessor at all, so there is no legacy file for
  `migrate-legacy-config.sh` to import from either location.
- Fail direction on resolution failure: `exit 0` — the hook installs
  nothing rather than guess.
- Fail direction on schema-read failure: **disabled**. An unreadable
  `config-keys.psv` or an absent `plugin_bootstrap` row exits the hook (the
  `_config_enabled` statuses 3 and 4). A malformed or wrong-type value in
  `claude-config.toml` falls back to the schema default `true`.

### `agent_branch_records`

- Call site: `branch_records_status` in `_branch-records-lib.sh`, a single
  `_config_enabled agent_branch_records` ahead of any archive-root read. Both
  cleanup scripts call it, and so does the retention sweep in `review-ledger.sh`.
- Resolution: **config-dir**. `_config_enabled` reads only the resolved
  config dir — no `$HOME` union arm.
- Legacy-probe-on-resolution-failure: **false**. No raw-path probe exists.
- Legacy-import-locations: **config-dir**. This key has no legacy
  predecessor at all, so there is no legacy file for
  `migrate-legacy-config.sh` to import from either location.
- Fail direction on resolution failure: **keep every item**. The
  `_config_enabled` statuses 2, 3 and 4 make the archive `unavailable`, so a
  cleanup script removes no worktree and exits 1. On statuses 3 and 4 the ledger
  sweep keeps every due ledger file, warns, and does not change its exit status.
  Status 2 never reaches the sweep, because `review-ledger.sh` exits 2 at its
  config-directory resolution first.
- Fail direction on a malformed or unreadable enabling line: **keep every
  item**. When `_config_enabled` reads the key as off, `branch_records_status`
  makes the archive `unavailable` unless `claude-config.toml` provably does not
  exist or was read and holds no offending line.
  `docs/agent-branch-records.md` § "Fail direction" holds the residuals, and
  the reason table's last row holds the condition list. The ledger sweep keeps
  every due ledger file, warns, and does not change its exit status.
- Check scope: the check does not run when `_config_enabled` reads the key as
  on, so a `= true` line followed by a malformed duplicate still archives.
- Fail direction on an internal resolver failure of `_config.sh`:
  **remove unarchived**. `_config_resolve_fail_safe` substitutes the schema
  default, false, so `_config_enabled` returns 1 and the key reads as off. The
  ledger sweep then deletes due ledger files with no copy.
