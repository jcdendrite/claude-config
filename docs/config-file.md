# The config file

`<config-dir>/claude-config.toml` (`<config-dir>` means `$CLAUDE_CONFIG_DIR`
when set and absolute, else `$HOME/.claude`) is the single, hand-editable
state file for every machine- and account-scope toggle this repo ships —
worktree enforcement, autonomous shipping, the PR-cost ledger and its
disclosure mode, and the various advisory-nudge kill switches. No consumer
re-derives its own presence/content check for a given toggle from a
standalone sentinel file. This file, `config-keys.psv`'s schema, and one
reader per language runtime (`_config.sh` for bash,
`_config.py` for Python) are this repo's single canonical home for that
whole mechanism — every other doc, skill body, and hook comment in this
repo references this page rather than restating its format or resolution
rules.

## Format

A restricted TOML subset, hand-editable with any text editor:

```toml
worktree_required = true
autonomous_shipping = false
# a comment
pr_cost_disclosure = dollars
```

- One `key = value` per line. Leading-`#` lines and blank lines are
  skipped by both readers.
- The value grammar is exactly `true`, `false`, or a bare `[a-z0-9_-]+`
  token (an enum literal, e.g. `dollars`) — no quotes, no escapes, no
  arrays, no tables. A line outside this grammar is skipped with a
  stderr warning naming the file and the line, not treated as a
  whole-document parse failure — a hand-edit typo affects only that one
  key.
- Values are matched case-insensitively (`TRUE`/`True`/`FALSE` all
  resolve), but keys are matched case-sensitively; this repo's own key
  names are already lowercase snake_case.
- `tomllib` (Python's stdlib TOML reader) is used only in this repo's own
  test suite, to confirm the file stays genuine TOML that a linter or
  editor's syntax highlighting understands correctly — never as a second
  decoder in the actual read path.

## Schema

`claude/.claude/hooks/config-keys.psv` is the schema every reader consults:
one pipe-delimited row per key, columns `key|type|default|resolution
|legacy-probe-on-resolution-failure|legacy-import-locations|legacy-filename
|legacy-polarity|human-name|docs-anchor|prompt-description`. A non-empty
`prompt-description` is what makes a key promptable by `install.sh`'s
interactive opt-in loop — there is no separate scope field. See that
file's own header comment for what each column means; it is authoritative
over any paraphrase here.

## Per-key table

| Key | Type | Default | Resolution | Promptable | Docs |
|---|---|---|---|---|---|
| `worktree_required` | bool | `false` | config-dir-or-home | yes | [README § Worktree enforcement](../README.md#worktree-enforcement) |
| `autonomous_shipping` | bool | `false` | config-dir-or-home | yes | [README § Autonomous shipping](../README.md#autonomous-shipping) |
| `permission_prompt_tracking` | bool | `false` | config-dir | yes | [permission-prompt-tracking.md](permission-prompt-tracking.md) |
| `error_mode_nudge` | bool | `false` | config-dir | yes | [error-mode-nudge.md](error-mode-nudge.md) |
| `cost_ledger_recording` | bool | `false` | config-dir | yes | [cost-ledger.md](cost-ledger.md) |
| `pr_cost_recording` | bool | `false` | config-dir | yes | [pr-cost.md](pr-cost.md) |
| `pr_cost_disclosure` | `enum:dollars` | `false` | config-dir | no | [README § PR cost disclosure](../README.md#pr-cost-disclosure) |
| `pr_description_tighten_prose` | bool | `true` | config-dir | no | [hooks.md § Prose tightening opt-out](hooks.md#prose-tightening-opt-out) |
| `handoff_nudge` | bool | `true` | config-dir | no | [handoff-nudge.md](handoff-nudge.md) |
| `consume_durable_continuity` | bool | `true` | config-dir | no | [hooks.md § Utility hooks](hooks.md#utility-hooks) |
| `commit_stall_block` | bool | `true` | config-dir | no | [commit-stall-block.md](commit-stall-block.md) |
| `session_title_from_branch` | bool | `true` | config-dir | no | [hooks.md § Utility hooks](hooks.md#utility-hooks) |
| `round_consult_gate` | bool | `true` | config-dir | no | [hooks.md § Gate hooks](hooks.md#gate-hooks) |
| `authorization_boundary_restore` | bool | `true` | config-dir | no | [hooks.md § Utility hooks](hooks.md#utility-hooks) |

`worktree_required`, `autonomous_shipping`, `round_consult_gate`,
`commit_stall_block`, and `authorization_boundary_restore` are the five
**enforcement-critical** keys `config-keys.psv`'s own header names: any
change to that file's `resolution`, `legacy-probe-on-resolution-failure`,
or `legacy-import-locations` column for one of their rows requires a
`claude-hook-review` pass, since each controls a mechanism that removes a
human checkpoint (a review-process requirement, not a hook, since the
schema file is git-tracked and every change already lands via a
PR-visible diff).

## Resolution precedence

For a `resolution: config-dir` key, reading it walks one location — the
resolved config dir — through three tiers, stopping at the first that
applies:

1. A conforming row for that key in `claude-config.toml` is authoritative.
2. Only when the key is entirely absent from that file is its legacy
   sentinel file consulted, interpreted per the key's `legacy-polarity`
   (`presence-enables`, `presence-disables`, or `content-matches`).
3. Only when that legacy file is also absent does the schema `default`
   apply.

A legacy file re-created or hand-edited after the key already has a
state-file row never wins — once a key has any row, whether from a
hand-edit, a migration import, or a scaffold default, that row is
authoritative until it is itself changed.

`worktree_required` and `autonomous_shipping` (`resolution:
config-dir-or-home`) add a union on top of that: the effective value is
the OR of the resolved config dir's own three-tier value and
`$HOME/.claude`'s own three-tier value, so an explicit `false` row in one
location can never defeat a `true` produced by the other — the same
invariant a sentinel armed before `CLAUDE_CONFIG_DIR` adoption already
relied on. If `CLAUDE_CONFIG_DIR` is set but relative — so the config dir
itself cannot be resolved — resolution falls to one of these outcomes:

- `$HOME` available, key is `worktree_required`: still probes the literal
  `$HOME/.claude/worktree-required` file directly
  (`legacy-probe-on-resolution-failure: true` — matching this key's
  pre-migration fail-closed behavior).
- `$HOME` available, any other key (including `autonomous_shipping`):
  reports "unresolvable" instead of granting on a resolution failure — the
  wrong direction for a mechanism that removes a human checkpoint.
- `$HOME` also unset or empty: no location is left to probe, so every key,
  `worktree_required` included, reports "unresolvable."

## Migration

`claude/.claude/scripts/migrate-legacy-config.sh` (mode 755) is a
standalone entry point, not glue code with no life of its own: it is safe
to run directly (`~/.claude/scripts/migrate-legacy-config.sh`) any time you
want `claude-config.toml` materialized or refreshed from legacy files
without running the rest of `install.sh` — useful because `claude/.claude/**`
goes live on `git pull` with no automatic `install.sh` re-run, so a
machine can be running this migration's hook code well before it has ever
run the migration itself. `install.sh` also executes it (as a subprocess,
never sourced) before its own interactive opt-in prompts, with a
`[install] warning: ...` fallback rather than aborting the rest of the
installer on failure.

It runs two phases, always in this order:

1. **Non-interactive import, then schema-default scaffold.** For each of
   the 15 keys, it checks for a legacy value at the location(s)
   `legacy-import-locations` names — the resolved config dir alone, or
   (for the six keys `install.sh`'s pre-migration writer always wrote to
   `$HOME/.claude` regardless of `CLAUDE_CONFIG_DIR`, plus `pr_cost_disclosure`,
   whose own pre-migration resolution could land at either location
   depending on when a diverged user set `CLAUDE_CONFIG_DIR`) both
   `$HOME/.claude/<legacy-filename>` and the resolved config dir's own
   copy, with the resolved-config-dir value winning on disagreement.
   Import writes a key's legacy-derived value only the first time that key
   has no existing state-file row — a hand-edit or an earlier import is
   never overwritten by a later run. For the ten non-enforcement-critical
   keys this happens fully non-interactively. For the five
   enforcement-critical keys, import is direction-aware rather than
   TTY-gated: it compares the legacy-derived value against that key's
   fail-closed value (`worktree_required=true`, `autonomous_shipping=false`,
   `round_consult_gate=true`, `commit_stall_block=true`,
   `authorization_boundary_restore=true`) and writes it, with no
   confirmation of any kind, only when the two match — turning enforcement
   further on can only make the machine more restrictive, so it needs no
   gate. A permissive-direction legacy value is never written; it is
   deferred the same way a read failure is (below) and printed for a human
   to hand-paste into `claude-config.toml`. See
   [`sentinel-config-consolidation.md`](design-decisions/sentinel-config-consolidation.md)
   for the full design rationale. A legacy-file read failure for one key
   (unreadable file, or content that fails `pr_cost_disclosure`'s
   `content-matches` grammar) does not abort the run; the remaining keys
   still import normally. After every key has been processed, a
   schema-default scaffold fills in a default row only for a key with no
   legacy fallback mechanism at all — none exist among today's 15 keys, so
   scaffold currently writes nothing. A key with a legacy-polarity
   (`presence-enables`/`presence-disables`/`content-matches`) stays absent
   from the state file unless this run's import path already gave it an
   explicit row, so its legacy file remains a live override rather than
   being permanently shadowed by a locked-in default.
2. **Interactive per-file delete offer**, gated once on `[ -t 0 ]` for the
   whole phase — hang-prevention only, not a security control, since
   nothing here stops an agent from running `rm` on a legacy file
   directly. For each legacy file this run actually imported a value from
   (not one that lost precedence to the other legacy location, and not one
   whose key already had a state-file row before this run — offering
   deletion there would be offering a behavior change dressed as cleanup),
   a `[y/N]`-gated prompt (default No) names the file, the key, and the
   imported value, and asks whether to delete it now. On a non-TTY
   invocation every legacy file is left in place with a message to re-run
   from a terminal. **`$HOME/.claude/worktree-required` is never offered,
   regardless of TTY state or outcome** — `worktree_required`'s
   `legacy-probe-on-resolution-failure` column keeps that one file
   load-bearing as the resolution-failure fallback after migration, so
   deleting it would flip a fail-closed degraded state to fail-open for
   the one key designed to fail closed. It is instead printed, at the end
   of the run, with a copy-pasteable `rm` for anyone who wants to remove it
   by hand once they've confirmed they no longer need the fallback.

## Writing a value

`~/.claude/scripts/config-get.sh` has no `set` subcommand and no other
writing verb — it is read-only, full stop, so shipping it to every stow
consumer opens no new write path. The only two sanctioned writers are
`install.sh`'s interactive `[y/N]` opt-in prompt (for the six promptable
keys) and `migrate-legacy-config.sh`'s import phase, described above.
`enforce-config-write-shape.sh` (see [`hooks.md`](hooks.md)) denies every
Claude-Code-tool-mediated `Write`/`Edit`/`MultiEdit` targeting
`claude-config.toml`, and every `Bash` command invoking `_config_set` by
name or whose redirect/utility-write candidates resolve to the same path —
this is a blanket denial with no agent-type carve-out, bounded to the
fixed set of write utilities `_LIB_WRITE_UTILITIES` recognizes (see that
hook's header for the current list and its open residuals), not a
guarantee against every possible write mechanism.

A human editing the file directly, with their own editor or a shell
command run outside Claude Code's own tool calls (e.g. the `!` shell
escape, which never reaches the Bash tool), is unaffected — that is the
whole point of a hand-editable file. Appending a new `key = value` line
for a key with no existing row is safe; appending a second line for a key
that already has one still resolves correctly (the last occurrence wins),
but leaves a redundant line behind — edit the existing line in place
instead once you know it's there.

## Querying a value

```bash
~/.claude/scripts/config-get.sh <key>
```

Exit 0 = enabled, exit 1 = disabled, exit 2 = unknown key, exit 3 = config
dir unresolvable. The exit code is the sole authority — the effective
value is also printed to stdout for a human, but never trust stdout alone.
This is the query surface skill prose uses instead of learning the file's
internal format (`pr-description/SKILL.md`, `transcript-analysis/SKILL.md`).

## Per-account isolation

Because every key now lives in one file, symlinking `claude-config.toml`
itself from one Claude account's config dir to another's merges *all 15
keys* between the two accounts at once — a wider blast radius than the
single-sentinel symlink risk this repo already warned about for
`pr-cost.md`'s `.pr-cost-enabled` file. A symlinked `claude-config.toml`
silently opts both accounts into each other's worktree enforcement,
autonomous shipping, PR-cost disclosure mode, and every advisory nudge
kill switch together, defeating the per-account isolation each of those
features is designed around. Create each account's `claude-config.toml`
as its own regular file — never a symlink to another account's copy.

## A hand-edit-loss fragility for enforcement-critical keys

A hand-edited row for one of the five enforcement-critical keys is the
sole record of that key's value: nothing mirrors it into a legacy file.
If the code that reads `claude-config.toml` for one of these keys is
ever reverted to a version that only knows the legacy per-key file, that
reverted code reads only that key's old per-key legacy file. That file
was never written for a value that only ever lived in the state file.
The reverted code therefore silently resolves to the key's old default
— in whichever direction that default happens to be — discarding the
hand-edited row with no warning.
