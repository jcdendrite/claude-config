# Stop committing cost-ledger to the public repo; store it locally per account, with an override

## Context

**Goal:** `cost-ledger --record` stops appending its weekly row to
`docs/cost-ledger.md`, a file this repo commits and ships publicly, and
instead writes to a local, per-account file that never enters the git tree —
configurable via a new environment variable for an operator who wants the
data somewhere else (a private repo, a synced location), defaulting to
`$CLAUDE_CONFIG_DIR/cost-ledger.md` for everyone who doesn't set it.

`cost-ledger --record` (PR #617) computes a durable, once-per-week
efficiency snapshot from data Claude Code prunes after `cleanupPeriodDays`
(default 30 days) — a week not recorded while still observable can't be
recovered later, which is why the feature exists at all. But its storage
target, `docs/cost-ledger.md`, is a file this public `claude-config` repo
commits and ships to every clone. The union figure the row carries is
correct and intended by design (the schema's own `machine` column exists so
rows from different machines can be told apart) — the defect is that the
row lands in a public git history at all, not what the row contains.

This became blocking, not just theoretical, when `.claude/plans/cost-summary-account-scope.md`
(merged as PR #635) added a defense-in-depth refusal: `--record` now exits 2
whenever more than one Claude-account config dir is in scope, specifically
because a companion piece of work — generating
`~/.claude/transcript-config-dirs` from an operator's full account roster —
would otherwise let `--record` silently commit a multi-account-union dollar
figure to this public file the moment that roster file existed. That guard
is a stopgap for the storage location, not a fix for it: on a multi-account
machine, `--record` now simply refuses outright rather than recording
anything, and stays that way until storage moves off the public repo. This
plan is that fix — a prerequisite for the roster-file generator to land
safely, tracked separately.

## Approach

**Root problem:** `_cost_ledger_path()` hardcodes `docs/cost-ledger.md` —
a path inside this repo's own git tree, resolved from `transcript-analysis.py`'s
`__file__` location three parents up. Every `--record` invocation commits
its row to a file this public repo ships to every clone.

**Givens** (fixed beyond this plan's reach):

- `claude-config` is a public repo other people clone and stow; a fix that
  hardcodes a path or repo name specific to one operator's own private
  infrastructure would silently break the feature (or need its own
  fallback logic) for every other consumer. *Reason: this repo has no way
  to know what private infrastructure, if any, a given stow consumer has.*
  `[verified: this repo's own CLAUDE.md — "This repository is public — every
  commit, skill body, commit message, and PR description ships to anyone
  with the URL"]`
- Cross-machine merging (rows from different machines ending up in one
  merged view via git) is **not** a design goal this plan needs to
  preserve. `[engineer-verified]` The `machine` column stays in the schema
  for a human comparing files by hand across machines, but nothing in this
  plan needs to make that automatic.

Two conditions this plan treats as settled are deliberate scope decisions
this plan *could* reach, not external constraints — recorded in **Out of
scope** instead of here, with their own reasons: leaving the ledger's
schema/content untouched (reachable — same script, same PR — declined
because PR #617 already designed it deliberately and this plan's job is
storage location only), and keeping mechanism 8's multi-root `--record`
refusal unconditional rather than narrowing it to "only when the resolved
path is inside a public repo" (reachable — same function this plan already
edits — declined because the storage path is operator-configurable and this
plan cannot know at write time whether a given configured destination is
actually private).

**Mechanisms 1 and 2 are a hard bundle, not two independent phases.** A
fresh `$CLAUDE_CONFIG_DIR/cost-ledger.md` default (mechanism 1) with no
auto-create (mechanism 2) would make `--record` fail-closed for every
consumer, on every machine — the new default path has never had anything
written to it, unlike the old `docs/cost-ledger.md`, which shipped
pre-populated with its schema header via this repo's own git checkout.
Landing mechanism 1 without mechanism 2 is a broken intermediate state, not
a smaller safe increment; they ship in one PR, one commit if convenient.

**Mechanisms:**

| # | Mechanism | Anchors | Why |
|---|---|---|---|
| 1 | New `COST_LEDGER_PATH` env var overrides `_cost_ledger_path()`; default becomes `config_dir() / "cost-ledger.md"` | root | Generalizes the storage location without hardcoding any one operator's private infrastructure — every other stow consumer gets a safe, working local default instead of a broken feature |
| 2 | `_cost_ledger_report` creates the ledger file (directory + schema preamble) on first `--record` instead of requiring pre-existence, positioned after every reject-with-no-side-effect validation guard | row 1 | The old "must already exist" check made sense when the file was a committed doc shipped with the repo; a fresh per-account local file has no reason to pre-exist. Must run last among the guards so a `--record` that fails validation (e.g. missing `--machine-label`) never leaves a stray empty ledger file as a side effect |
| 3 | `docs/cost-ledger.md` repurposed as schema/design-rationale documentation only — states where a local ledger actually lives and how to override it, drops the now-meaningless "## Data" table | row 1 | The file's storage role is gone; leaving a permanently-empty "## Data" table in a doc a reader might mistake for the real thing is actively misleading |

**Assumptions:**

- `_cost_ledger_path()` is the single call site every other function funnels
  through (`_cost_ledger_report`'s read and `--record` paths both call it
  once, at the top). No other function reads or writes `docs/cost-ledger.md`
  directly. `[verified: grep of _cost_ledger_path across
  transcript-analysis.py this session — one definition, one call site]`
- `cost-trend` (a separate subcommand computing trend data live from
  transcripts) never reads the ledger file — `_compute_cost_trend_data`
  has no reference to `_cost_ledger_path`. `[verified: grep this session]`
  This plan's code blast radius is `cost-ledger` only — but see the doc/prose
  consumer bullet below, which is a *different* blast radius the module-level
  grep above does not cover.
- **Three prose/doc files describe `--record`'s target today and go stale
  the moment mechanism 1 lands — none are `_cost_ledger_path` call sites, so
  the grep above does not surface them:**
  `claude/.claude/skills/transcript-analysis/SKILL.md` ("`cost-ledger
  --record` writes to `docs/cost-ledger.md`"), `docs/transcript-analysis.md`
  (three sites, including "a durable, committed, append-only record"), and
  `install.sh`'s `SENTINEL_INVENTORY` description of `.cost-ledger-enabled`
  ("Lets cost-ledger --record append this repo's weekly cost/efficiency
  figures to docs/cost-ledger.md"). The `install.sh` copy is user-facing at
  first-run setup time — shipping it unchanged tells a new stow consumer
  their figures land in the public repo, which becomes false the moment this
  plan merges. `[verified: staff-backend-engineer grep this session]` See
  Critical files.
- `docs/cost-ledger.md`'s "## Data" table is empty today — no rows have
  ever been recorded — so repurposing the file loses no historical data.
  `[verified: read this session]`
- The codebase already has two precedents for an env-var override with a
  local-file fallback default: `config_dir()` reads `CLAUDE_CONFIG_DIR`
  (validated absolute, raises otherwise) and falls back to `Path.home() /
  ".claude"`; `declared_roots_file()` reads `TRANSCRIPT_CONFIG_DIRS_FILE`
  and falls back to `Path.home() / ".claude" / "transcript-config-dirs"`
  (`claude/.claude/scripts/_config_dir.py:13-21` and `:35-40`, quoted
  verbatim in Critical files below). `COST_LEDGER_PATH` follows the same
  shape: read the env var, validate absolute if set, else compute the
  default. `[verified: read this session]`
- Existing tests fixture `_cost_ledger_path` by monkeypatching the function
  itself (`monkeypatch.setattr(_mod, "_cost_ledger_path", lambda: ...)`),
  not by setting an env var — this plan's new env-var seam does not need to
  replace that pattern; the existing fixture keeps working unmodified
  because it bypasses the function's internal resolution logic entirely.
  `[verified: staff-sdet read of the cost_ledger_file fixture this session
  — confirmed the monkeypatch is a full function replacement, not a partial
  one]`
- `_write_cost_ledger_file`'s permission-preservation step
  (`os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))`) assumes
  `ledger_path` already exists — its own docstring says so explicitly
  ("`ledger_path` is confirmed to exist by `_cost_ledger_report`'s own
  check before this function is ever called"). Mechanism 2 breaks that
  precondition on purpose for the first-ever `--record` against a fresh
  path; `.stat()` on a nonexistent path raises `FileNotFoundError`.
  `[verified: ciso-reviewer read of _write_cost_ledger_file this session]`
  See Critical files for the fix.
- The mechanism-8 refusal's own inline comment (`_cost_ledger_report`,
  immediately above `sys.exit(2)`) currently names `docs/cost-ledger.md`
  specifically as the reason the guard exists. Left unedited, this comment
  becomes misleading for every operator using the new default and could
  read as evidence the guard is now moot to a future contributor, who could
  then remove a still-live protection (`COST_LEDGER_PATH` can still be
  pointed at a public repo). `[verified: ciso-reviewer read this session]`
  See Critical files.

**Alternatives weighed:**

- **Hardcode the destination to a specific private repo** (e.g. this
  operator's `workstation-setup`) — rejected per the public-repo given
  above; breaks for every other stow consumer.
- **Local-only, no override, always `$CLAUDE_CONFIG_DIR/cost-ledger.md`,
  no env var** — rejected: simpler, but forecloses an operator who *does*
  want the row synced somewhere (their own private repo, a dotfiles-sync
  target) from getting that without patching the script themselves. The
  env var costs one small, precedented function; keeping it open costs
  nothing extra for the local-only default case.
- **Keep committing to the repo, but redact the row before commit** —
  rejected outright by the engineer's own prior direction (see the plan's
  Context): the union figure is correct and intended, the storage location
  is the defect, not the content.

## Critical files

**Reuse, don't reimplement:**

Model `COST_LEDGER_PATH`'s resolution directly on `config_dir()`'s own body:

```python
def config_dir() -> Path:
    """Return the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set (must be absolute), else ~/.claude."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"CLAUDE_CONFIG_DIR must be an absolute path, got: {override!r}")
        return path
    return Path.home() / ".claude"
```

(`claude/.claude/scripts/_config_dir.py:13-21`)

Build the auto-created file's schema section from the **existing** module
constants `_COST_LEDGER_HEADER_LINE` and `_COST_LEDGER_SEPARATOR_LINE`
(`claude/.claude/scripts/transcript-analysis.py:7302-7303`) — do not
re-spell the header text. `_parse_cost_ledger_file_text` requires an
**exact** literal match against `_COST_LEDGER_HEADER_LINE`
(`line.strip() == _COST_LEDGER_HEADER_LINE`), so any hand-written header
text that isn't byte-identical to the constant fails to parse on the very
next `--record` or read.

- `claude/.claude/scripts/transcript-analysis.py` `_cost_ledger_path()`
  (currently `:7333-7342`, **line numbers unreliable across sessions —
  relocate by symbol name**) — rewrite to read `COST_LEDGER_PATH` first
  (validated absolute, raising `ValueError` on a relative value, same shape
  as `config_dir()` above), falling back to `config_dir() / "cost-ledger.md"`
  (`config_dir` is already imported into this module — no new import
  needed). Update the docstring: it currently explains why the path is
  resolved from `__file__` rather than cwd — `[verified:
  staff-backend-engineer this session]` that rationale is fully obsolete for
  the new default (which depends on neither `__file__` nor cwd), not merely
  supplemented — replace it, don't append to it.

- `claude/.claude/scripts/transcript-analysis.py` `_cost_ledger_report()`
  (currently `:7677-7864` end to end) — three coordinated changes:

  1. **Split the top-level `if not ledger_path.exists()` check by mode.**
     Today it gates both read and `--record` uniformly, before either
     branch runs (currently around `:7712-7721`). Move it into the
     `if not record:` branch only, with new wording distinguishing "no
     `--record` has ever run against this path yet" from a misconfigured
     or missing file (exit code can stay 1). The `--record` branch gets no
     early exit on absence at all — it falls through the full validation
     gauntlet below unchanged.

  2. **Add a small preamble-building helper** (e.g.
     `_default_cost_ledger_preamble() -> str`), used only when creating a
     ledger fresh: a short title/description line or two, then
     `_COST_LEDGER_HEADER_LINE`, then `_COST_LEDGER_SEPARATOR_LINE`, each
     on its own line, ending in `"\n"` — matching the exact shape
     `_parse_cost_ledger_file_text`'s `preamble = "\n".join(lines[:
     header_idx + 2]) + "\n"` produces for a real file, so a freshly
     created ledger round-trips through the parser identically to an
     already-canonical one.

  3. **Create the file at the right point in the `--record` sequence, not
     at the top.** The current guard order (verified this session,
     `_cost_ledger_report` currently `:7735-7823`) is: `.cost-ledger-enabled`
     sentinel exists → `--machine-label` present → matches
     `^[a-z0-9]{1,8}$` → mechanism 8's `len(roots) > 1` refusal → machine
     label doesn't collide with this machine's hostname → `--note` passes
     `_cost_ledger_note_violation` → the current week has priced turns (not
     a blank/zero row) → no clock skew between the corpus's latest week and
     today's computed week. **Every one of these can reject with no side
     effect today, and must keep doing so** — auto-create must not run
     until all of them have passed. Concretely: immediately before
     `lock_path = ledger_path.with_name(...)` (currently `:7842`), add
     `ledger_path.parent.mkdir(parents=True, exist_ok=True)` — needed even
     when `ledger_path` itself will be freshly created, because the
     **lock file** (`lock_path`, opened immediately after) lives in the
     same, possibly-not-yet-existing, directory. Then, inside the
     lock-protected block, where `_parse_cost_ledger_file_text(ledger_path.read_text())`
     is called (currently `:7847`), catch `FileNotFoundError` alongside the
     existing `_CostLedgerParseError` handling and treat it as `(preamble,
     existing_rows) = (_default_cost_ledger_preamble(), [])` rather than
     letting the exception propagate.

- `claude/.claude/scripts/transcript-analysis.py` `_write_cost_ledger_file()`
  (currently `:7541-7576`) — the permission-preservation line
  (`os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))`,
  currently `:7570`) crashes with `FileNotFoundError` on a not-yet-existent
  `ledger_path`, and its own rationale (preserve `docs/cost-ledger.md`'s
  git-checkout permission bits across a `--record`) doesn't apply to a
  brand-new local file in the first place. Guard it: `if ledger_path.exists():
  os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))` — when the
  path doesn't exist yet, this simply leaves `tempfile.mkstemp`'s own 0600
  default in place (already the restrictive mode a fresh, potentially
  sensitive local file should have — no explicit chmod needed for that
  case, just skipping the override). Update the docstring's "ledger_path is
  confirmed to exist..." sentence, which becomes false for the first-write
  case.

- `claude/.claude/scripts/transcript-analysis.py` — the mechanism-8
  refusal's inline comment (immediately above its `sys.exit(2)`, currently
  naming `docs/cost-ledger.md` as the reason the guard exists) — reword to
  name "wherever `_cost_ledger_path()` resolves to" generically, not the
  specific pre-this-plan default path, so the comment doesn't read as
  evidence the guard is moot once the default changes.

- `docs/cost-ledger.md` — keep the "## Schema" table (still accurate
  documentation of the row shape) and the design-rationale pointer to
  `.claude/plans/cost-trend-ledger.md`. Replace the "## Data" section
  entirely: state that ledger data lives at `$CLAUDE_CONFIG_DIR/cost-ledger.md`
  by default, name `COST_LEDGER_PATH` as the override, and drop the
  literal empty table.

- `claude/.claude/skills/transcript-analysis/SKILL.md` — correct the "writes
  to `docs/cost-ledger.md`" claim to describe the new default + override.

- `docs/transcript-analysis.md` — correct the three sites describing
  `--record`'s target as `docs/cost-ledger.md`, including the "durable,
  committed, append-only record" framing, which is no longer accurate for
  the default case.

- `install.sh` — the `SENTINEL_INVENTORY` row describing
  `.cost-ledger-enabled` currently says `--record` appends "this repo's
  weekly cost/efficiency figures to docs/cost-ledger.md" — update to match
  the new default/override, since this text is shown to an operator at
  first-run setup time.

- `claude/.claude/scripts/tests/test_transcript_analysis.py` — the
  `cost_ledger_file` fixture (currently `:9782-9793`) and its consuming
  tests need no structural change (see Assumptions above), but the suite
  needs new tests for the mechanisms this plan adds — see **Tests** below.

## Tests

- `_cost_ledger_path()`: `COST_LEDGER_PATH` set to an absolute path is
  honored; set to a relative path raises `ValueError`; unset resolves to
  `config_dir() / "cost-ledger.md"` (assert this against a monkeypatched
  `CLAUDE_CONFIG_DIR`, not the real `$HOME`).
- `--record` against a path with **no file and an existing parent
  directory**: creates a fresh file (preamble via
  `_default_cost_ledger_preamble()`, containing `_COST_LEDGER_HEADER_LINE`/
  `_COST_LEDGER_SEPARATOR_LINE` verbatim) and appends the new row — assert
  the resulting file round-trips through `_parse_cost_ledger_file_text`
  and contains exactly one row.
- **A second, distinct test** for `--record` against a path whose **parent
  directory also does not exist yet** (e.g. `tmp_path / "fresh-config-dir"
  / "cost-ledger.md"` where `fresh-config-dir` is never created by the
  test) — asserts both the directory and file are created and the row
  lands. `[required per staff-sdet this session: a test using only
  tmp_path / "cost-ledger.md" directly would pass even if the
  implementation used a non-recursive `mkdir()`, leaving the plan's own
  named real-world trigger — a brand-new `$CLAUDE_CONFIG_DIR` — unverified]`
- Read mode (no `--record`) against a path with no file yet, **two
  assertions, not one**: (a) a **behavioral** test — call
  `_cost_ledger_report()` directly, capture stderr via `capsys`, assert the
  new "never recorded here yet" wording is what's actually printed on that
  code path (proves the message is reachable, not just present in source);
  (b) a **separate, explicitly-labeled source-grep tripwire** asserting the
  literal old string ("ledger file not found") no longer appears anywhere
  in `transcript-analysis.py` — a tripwire against reintroduction, not a
  behavioral guarantee, and not a substitute for (a). `[required per
  staff-sdet this session: folding both into one grep-based assertion would
  let a reachability bug — correct wording in source, never actually
  printed due to a control-flow error — pass]`
- One test driving the new default-path resolution through the full CLI
  path — `cmd_cost_ledger(args)` (or `main()`) with `COST_LEDGER_PATH`
  unset and `CLAUDE_CONFIG_DIR` monkeypatched to a fresh `tmp_path` —
  confirming the row lands at `config_dir() / "cost-ledger.md"`
  end-to-end, not just via a direct `_cost_ledger_report()` call. `[required
  per staff-sdet this session: every other proposed test calls
  _cost_ledger_path()/_cost_ledger_report() directly, so a dispatch-wiring
  regression in cmd_cost_ledger/main() itself would go uncaught by every
  other test in this list]`
- `_write_cost_ledger_file()`: a direct unit test asserting the temp file's
  mode is `tempfile.mkstemp`'s own 0600 default when `ledger_path` does not
  yet exist (no `FileNotFoundError`, no chmod-to-nonexistent-file crash),
  and that the existing-file case (chmod to the existing file's own mode)
  is unaffected by the new guard.
- Mechanism 8's existing multi-root `--record` refusal test: confirm it
  still passes with zero assertion changes — this plan does not touch that
  guard's logic, only its neighboring comment text.

## Verification

```bash
.venv/bin/pytest claude/.claude/                             # full suite
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

**Explicit before/after checkpoint for the existing `cost_ledger_file`-fixtured
suite** (not just "the full suite passed once," which proves nothing about
whether this plan's rewrite of `_cost_ledger_path()`'s internals changed
behavior the fixture's full-function monkeypatch happens to mask): run
`.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k cost_ledger -v`
against the pre-change tree and again post-change, and diff the two runs'
pass/fail output — zero changes expected. `[required per staff-sdet this
session: "run unmodified" as a bare plan assertion is decorative without a
named diff step distinct from the Verification section's own full-suite
run]`

End-to-end manual check: with `CLAUDE_CONFIG_DIR` unset (or pointed at a
throwaway `tmp` directory) and `COST_LEDGER_PATH` unset, run
`touch <config-dir>/.cost-ledger-enabled && python3 claude/.claude/scripts/transcript-analysis.py cost-ledger --record --machine-label test`
twice — the first creates `<config-dir>/cost-ledger.md` fresh and records
one row; the second detects the existing (week, machine) row and records
nothing new (existing dedupe behavior, unchanged by this plan). Confirm
`git status` shows no change to `docs/cost-ledger.md`'s tracked content.
Then set `COST_LEDGER_PATH` to a different absolute path and re-run —
confirm the row lands there instead.

## Out of scope

- **Cross-machine merging of ledger rows.** `[engineer-verified]` Explicitly
  not a goal of this plan — see Approach's Givens. If this changes later,
  it's a separate plan, not a revision of this one.
- **Changing the ledger's schema, the union-figure semantics, the
  `--machine-label`/hostname-collision check, or the `--note` content
  restrictions.** Reachable — same script, same PR — but declined: all
  correct as designed by PR #617; this plan only relocates where the row
  is written, not what's written.
- **Provisioning `COST_LEDGER_PATH` for this operator's own machines** (e.g.
  a `workstation-setup` change pointing it at a private repo). That is a
  separate, per-operator follow-up outside this public repo — this plan
  only builds the override mechanism, not any particular operator's use of
  it.
- **Relaxing mechanism 8's multi-root `--record` refusal.** Reachable —
  the same function this plan already edits — but declined: the storage
  path is operator-configurable, and this plan cannot know at write time
  whether a given configured destination is actually private, so it does
  not narrow a guard that currently holds regardless of destination.
- **A migration or backfill step for existing `docs/cost-ledger.md`
  content.** Unnecessary — the table has always been empty (see
  Assumptions).
- **Warning when `COST_LEDGER_PATH` resolves inside another git-tracked
  directory.** `[ciso-reviewer, Low severity, this session]` An operator
  could self-misconfigure the override to point inside a different repo's
  working directory, re-introducing the accidental-public-exposure mode
  this plan otherwise closes for the default case. This is a
  self-configuration surface, not attacker-controlled input, and a
  deliberately-tracked private repo is this plan's own documented intended
  use of the override (see Alternatives weighed) — a blanket warning would
  fire on the legitimate case as often as the mistaken one. Declined rather
  than silently unaddressed: if this becomes a real operator complaint, a
  follow-up can add a narrower check (e.g. warn only when the resolved
  path's containing repo has a remote, distinguishing "your own private
  repo" from "a project checkout").
