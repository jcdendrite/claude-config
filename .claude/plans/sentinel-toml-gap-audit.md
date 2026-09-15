# Sentinel/TOML enforcement gap: `round_consult_round2_pilot`

## Context

Two independent sessions flagged, via a cross-session message, that
`_lib_reviewer_round_state_cap()` (`claude/.claude/hooks/_lib.sh:3082-3090`)
never checks the `round_consult_round2_pilot` TOML key that the recent
sentinel/marker migration (commit 8a303840, "Migrate sentinel/marker scheme
to claude-config.toml (#991)") introduced for it — only the legacy file
`.round-consult-round2-pilot`. The goal is to confirm the gap, audit every
other migrated sentinel for the same failure shape, and close whatever is
confirmed real.

The claim checks out, but not as an accidental miss: `claude/.claude/hooks/tests/config-schema-audit.md:343-364`
documents the raw-file-only check as deliberate, on the theory that
`round_consult_round2_pilot`'s schema row exists "only for `install.sh`'s
schema-driven reporter." That theory is itself wrong — `install.sh`'s
`_report_config_key` (install.sh:606) is generic over every `config-keys.psv`
row, so it reports this key's TOML value like any other, while
`_lib_reviewer_round_state_cap()` ignores that same TOML value when actually
setting the cap. Setting `round_consult_round2_pilot = true` in
`claude-config.toml` therefore makes `install.sh`'s reporter say "enabled"
while the gate's round cap stays unaffected. That is a reporter/enforcement
divergence, per
`docs/design-decisions/sentinel-config-consolidation.md`.

A full audit of the other 14 migrated keys' legacy filenames against every
non-test call site in the repo (bash and Python) found no sibling instances:
every other enforcement/behavioral call site already delegates to
`_config_enabled`/`_config_value`/`_config.config_enabled`. Two look-alike
raw file checks (`.claude/worktree-required` and
`.claude/session-title-disabled`, both at the repo root) are a distinct,
intentionally file-only mechanism — a committed per-repo opt-in, not part of
the machine-level `claude-config.toml` scheme — and are not gaps.

## Approach

Make `_lib_reviewer_round_state_cap` resolve the pilot flag through
`_config_enabled round_consult_round2_pilot` instead of probing
`<config-dir>/.round-consult-round2-pilot` directly, so the cap honors
`claude-config.toml` exactly as `install.sh`'s reporter already does. The
legacy sentinel keeps working unchanged — it is `_config_value`'s own
fallback arm, not a second code path this function has to keep — so both
hooks' existing legacy-file fixtures stay valid; two new unit tests pin
the TOML arm and the TOML-wins-over-legacy precedence, and
`config-schema-audit.md`'s row for this key is rewritten to describe the
delegation instead of asserting the raw-file check is intentional.

**Root problem.** The reporter/enforcement divergence described in Context
above — resolved here by making both read the same source.

**Givens** (fixed conditions this design does not reach):

- **G1.** The legacy sentinel must keep resolving the pilot after the
  fix. `config-keys.psv`'s own header (lines 70-73) fixes `legacy-filename`
  fallback as "permanently readable, since this fallback has no removal
  date and `claude/.claude/**` goes live on `git pull` with no guaranteed
  `install.sh` re-run" — retiring it is a schema decision owned elsewhere.
- **G2.** Delegating necessarily drops the `_lib_capped` wrapper around
  the presence probe: `_config.sh:7-12` requires that file stay
  dependency-free of `_lib.sh` (no `_lib_realpath_m`/`_lib_capped`) so
  `install.sh` can source it before `_lib.sh` is stowed, so its reads are
  plain `[ -f ]`/file reads by construction.
- **G3.** The function's stdout contract — always exactly one valid
  integer, exit 0 — is imposed by its two consumers'
  `[ "$(wc -l …)" -lt "$CAP" ]` (`require-architect-consult.sh:103`) and
  `[ "$existing_count" -ge "$cap" ]` (`log-reviewer-round.sh:109`)
  integer comparisons, in hooks this change does not touch.

**Assumption rows:**

1. `_config_enabled` returns 0 only when the key genuinely resolves
   enabled; 1 (false), 2 (config dir unresolvable), 3 (schema unreadable),
   and 4 (row missing) are propagated distinctly and never collapsed into
   1. `[verified: claude/.claude/hooks/_config.sh:691-713]`
2. A conforming `claude-config.toml` row for the key is authoritative;
   the legacy file is consulted only when the key is entirely absent from
   that file, and the schema default applies only when both are absent.
   `[verified: _config.sh:447-457 (contract comment) and 481-505 (the
   code implementing it)]`
3. The key's schema row is
   `round_consult_round2_pilot|bool|false|config-dir|false|config-dir|.round-consult-round2-pilot|presence-enables`,
   so resolution reads the resolved config dir only — no `$HOME` union arm
   — and legacy presence means `true`.
   `[verified: claude/.claude/hooks/config-keys.psv:104]`
4. The three existing `TestLibReviewerRoundStateCap` tests stay green
   after the change: the legacy-file test writes
   `<CLAUDE_CONFIG_DIR>/.round-consult-round2-pilot`, which is exactly the
   path row 2's fallback arm reads, and the unresolvable-config-dir test
   exercises exit code 2, which the new `else` arm treats identically to
   today. The same-shaped sibling `_lib_round_consult_gate_disabled`
   already resolves its own legacy file this way under test.
   `[verified: claude/.claude/hooks/tests/test_lib.py:4011-4021 and
   test_lib_reviewer_round_state.py:248-269 — read, not executed; the
   Verification command is what confirms it]`
5. The hook-level fixtures that `touch
   <isolated_home>/.claude/.round-consult-round2-pilot` (six in
   `test_require_architect_consult.py`, six in `test_log_reviewer_round.py`)
   also stay green for the same reason — they set `HOME` with no
   `CLAUDE_CONFIG_DIR`, and a `config-dir` key resolves `$HOME/.claude`
   there, the path the sibling gate's own `HOME`-only tests already prove
   reachable through `_config_enabled`.
   `[verified: test_lib.py:4012-4021; test_require_architect_consult.py:356,385,404,421,438,454;
   test_log_reviewer_round.py:355,373,402,426,451,468]`
6. `docs/hooks.md` already documents the post-fix behavior verbatim — the
   cap "is 1 under the time-boxed round-2 pilot (`round_consult_round2_pilot`
   config key, falling back to its legacy sentinel
   `<config-dir>/.round-consult-round2-pilot` when absent…)" — so this fix
   brings code into line with the current-behavior doc rather than
   requiring a doc edit there. `[verified: docs/hooks.md:54 and :81]`
7. `config-schema-audit.md` is prose only: no test, script, or hook parses
   it, so its correction is verified by review rather than mechanically.
   `[verified: repo-wide grep for "config-schema-audit" returns four
   non-plan files, all citing it in prose — config-keys.psv:64,
   advance-past-commit-stall.sh, nudge-handoff-near-context-cap.sh,
   test_advance_past_commit_stall.py]`
8. That file's current `round_consult_round2_pilot` "Call site" bullet is
   inaccurate in a second way beyond the one that motivated this plan: it
   shows `[ -f "$config_dir/.round-consult-round2-pilot" ]`, while the code
   actually runs `_lib_capped find … -maxdepth 0`. The rewrite corrects
   both. `[verified: config-schema-audit.md:347-348 vs _lib.sh:3085]`
9. The constant's trailing comment ("Pilot override: see
   `_lib_reviewer_round_state_cap` below and
   `docs/design-decisions/round2-consult-trigger-pilot.md`") names no
   sentinel file and does not go stale; only the function header's
   "Returns 1 if `<config-dir>/.round-consult-round2-pilot` exists" line
   does. `[verified: _lib.sh:3068-3069 vs :3074-3075]`
10. Both consumers are unaffected by construction: each calls
    `$(_lib_reviewer_round_state_cap)` and compares the printed integer,
    and neither reads the sentinel path itself.
    `[verified: require-architect-consult.sh:98,103;
    log-reviewer-round.sh:98,109]`
11. The added per-call cost is one `_config_enabled` invocation on the
    reviewer-persona dispatch path only — no new cost class, since
    `require-architect-consult.sh:71` already pays the identical cost for
    `_lib_round_consult_gate_disabled` before any git call runs. In that
    hook, the cap call sits at line 98, already past three capped git
    calls. `[verified: require-architect-consult.sh:70-98]`
12. Stderr behavior on a torn schema (a `_config.sh` warning line) is
    likewise already present in `require-architect-consult.sh` via the
    line-71 call, so the fix introduces no new stderr surface in either
    hook. `[verified: _config.sh:367, require-architect-consult.sh:71]`
13. The sibling audit is settled, not an open task: all 15 `config-keys.psv`
    rows were checked against every non-test call site, 14 already delegate
    to `_config_enabled`/`_config_value`/`_config.config_enabled`, and the
    two look-alike raw checks (`.claude/worktree-required`,
    `.claude/session-title-disabled`, both repo-root committed opt-ins) are
    a deliberately separate per-repo mechanism. Implementation must not
    re-run this sweep. `[verified: Step 3 audit reported in this plan's
    Context; the session-title half is corroborated by
    config-schema-audit.md:322-326, which records the repo check as
    "explicitly out of this migration's scope: the repo check stays a
    file"]`
14. The engineer asked for fix + audit, not audit-only — the confirmed gap
    gets closed in this branch. `[engineer-verified]`

**Mechanisms:**

- **M1 — Replace the raw probe with `_config_enabled round_consult_round2_pilot`.**
  `[anchors: root; rows 1, 2, 3, 10]` This is the only primitive that owns
  the whole precedence chain (TOML row → legacy file → schema default) in
  one place, which is what makes the reporter and the gate agree by
  construction rather than by two call sites staying in sync. Two lighter
  primitives were checked and rejected: (a) keep the `find` probe and add
  a direct read of `claude-config.toml` next to it — that creates a second
  resolution implementation, reintroducing the exact divergence class this
  fix removes, and would have to re-derive precedence by hand; (b) call
  `_config_value round_consult_round2_pilot` and compare the result to
  `true` — that re-implements `_config_enabled`'s documented "any resolved
  value other than the literal `false` counts as enabled" rule at a second
  site, and silently mis-handles exit codes 2/3/4 unless the comparison
  also branches on `$?`. A heavier option — a new named wrapper such as
  `_lib_round2_pilot_active`, mirroring
  `_lib_round_consult_gate_disabled`'s shape — is rejected because nothing
  but this one function reads the key, so the wrapper would add an
  indirection with a single caller.
  **New capability this introduces:** TOML `round_consult_round2_pilot = false`
  now overrides a stale legacy-file touch, since `_config_value` is
  TOML-first (`_config.sh:447-457`) — intentional, not a regression.
- **M2 — A plain `if`, not the sibling's `case "$?"` statement.**
  `[anchors: row 1]` `_lib_round_consult_gate_disabled` needs an explicit
  case because it must return 0 for exit code 1 alone and 1 for 2/3/4,
  a distinction `!` would collapse wrongly; here every nonzero code takes
  the same default-cap arm, so `if _config_enabled …; then printf '1\n';
  else printf '%s\n' "$_LIB_REVIEWER_ROUND_STATE_CAP"; fi` is both correct
  and the lighter construct. Copying the `case` would be cargo-culting a
  guard against a collapse that cannot happen here.
- **M3 — Two new unit tests on the function, none at either hook's level.**
  `[anchors: rows 4, 5, 7]` The resolution change is entirely inside
  `_lib_reviewer_round_state_cap`, and both hooks already have six
  legacy-file fixtures each proving the cap's *effect* end-to-end;
  duplicating those twelve fixtures with a TOML variant would test
  `_config_value`'s precedence twelve more times through the slowest
  available path. The two new tests are (1) TOML `round_consult_round2_pilot
  = true` with no legacy file present, expecting `1` — the arm that is
  broken today; and (2) TOML `round_consult_round2_pilot = false` *with*
  the legacy file present, expecting `2` — the precedence rule from row 2,
  and the case where an explicit opt-out would otherwise be overridden by a
  stale `touch`-ed file. A third test for exit codes 3 and 4 (unreadable
  schema, pruned row) is deliberately not added: the `else` arm is uniform
  across 1/2/3/4, and `test_default_cap_on_unresolvable_config_dir`
  already pins that arm through code 2, so the extra fixtures would need
  local copies of `test_lib.py`'s isolated-hooks-dir machinery to assert
  nothing new.

**Comment content.** The function header must state, after the edit: that
the cap is 1 when `round_consult_round2_pilot` resolves enabled per its
`config-keys.psv` schema row (presence-enables legacy polarity), else
`$_LIB_REVIEWER_ROUND_STATE_CAP`; that every nonzero `_config_enabled`
exit takes the default-cap arm, so no `case` is needed unlike
`_lib_round_consult_gate_disabled` below; and — kept verbatim from today's
header — the always-echoes-a-valid-integer stdout contract and the reason
for it. Delete the stale "Returns 1 if
`<config-dir>/.round-consult-round2-pilot` exists" line. Leave the
constant's own trailing comment at `_lib.sh:3068-3069` untouched (row 9).

**`config-schema-audit.md` rewrite.** Keep the section's five-bullet
structure (Call site / Resolution / Legacy-probe-on-resolution-failure /
Legacy-import-locations / Fail direction on resolution failure) and match
the `round_consult_gate` and `session_title_from_branch` sections' phrasing
for a delegating call site. Specifically: the Call-site bullet becomes a
single `_config_enabled round_consult_round2_pilot` call with no raw
probe; the sentence "The schema row exists only for `install.sh`'s
schema-driven reporter, not for this function's own enforcement" is
deleted outright rather than softened; the Legacy-probe bullet stays
**false** but its reason changes from "a `_lib_config_dir` failure
short-circuits the `&&`" to "`_config_enabled`'s exit 2 falls to the
default-cap arm"; the Legacy-import-locations bullet is unchanged; and the
Fail-direction bullet keeps its existing, correct rationale — an
unresolvable config dir allows more review rounds before the gate fires,
never fewer — re-anchored to the new mechanism. The file's opening
paragraph ("For every one of the 15 keys…") needs no change.

**Dispatch split.** One `code-writer` dispatch covering all three files.
The test asserts against the fixed function and the doc bullet describes
it, so neither can be specified without the other's context; splitting
would make each agent re-derive the same resolution semantics and risk two
different readings of the precedence rule.

## Critical files

Single `code-writer` dispatch; verification command below applies to the
whole set.

- `claude/.claude/hooks/_lib.sh` — rewrite `_lib_reviewer_round_state_cap`
  (lines 3082-3090) to delegate to `_config_enabled`, and update the
  function's header comment (lines 3071-3081) per the comment-content
  paragraph above. Leave lines 3058-3069 (the constant and its trailing
  comment) as-is.
  **Reuse:** `_config_enabled` (`claude/.claude/hooks/_config.sh:705`),
  already sourced at `_lib.sh:21`; `_lib_round_consult_gate_disabled`
  (`_lib.sh:3203-3216`) is the naming/exit-code/comment-style model to
  mirror — but not its `case` statement (M2). Do not add a new wrapper
  function, and do not keep `_lib_config_dir` in this function:
  `_config_enabled` resolves the config dir internally, the same way
  `set-session-title-from-branch.sh:86` relies on it
  (config-schema-audit.md:326-329).
- `claude/.claude/hooks/tests/config-schema-audit.md` — rewrite the
  `### round_consult_round2_pilot` section (lines 343-364) per the rewrite
  paragraph above. No other section changes.
  **Reuse:** the `### round_consult_gate` and `### session_title_from_branch`
  sections in the same file as the phrasing template for a delegating call
  site.
- `claude/.claude/hooks/tests/test_lib_reviewer_round_state.py` — add the
  two tests from M3 to `TestLibReviewerRoundStateCap` (lines 243-269).
  **Reuse:** the file's existing `_state_cap` helper (lines 63-79), which
  already sets `CLAUDE_CONFIG_DIR` and strips an ambient one — no new
  helper and no new fixture machinery. For the TOML fixture, follow
  `test_config_lib.py:44-47`'s convention: write
  `<config_dir>/claude-config.toml` with a single
  `round_consult_round2_pilot = true` (or `= false`) line. Keep the three
  existing tests; give the legacy-file one a docstring line naming its arm
  as the legacy fallback, so the three arms (TOML, legacy, default) read
  distinctly.

No change to `claude/.claude/hooks/config-keys.psv`,
`claude/.claude/hooks/_config.sh`,
`claude/.claude/hooks/require-architect-consult.sh`,
`claude/.claude/hooks/log-reviewer-round.sh`, `install.sh`, or
`docs/hooks.md`.

## Verification

Run from the worktree root:

```
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
```

`select-tests.py` domain-selects rather than widening for this diff: every
changed path is under `claude/.claude/hooks/`, which matches
`DOMAIN_RULES`' first row (`_is_under(p, HOOKS_DIR)` →
`claude/.claude/hooks/tests`) at `select-tests.py:365`, and the plan file
under `.claude/plans/` matches the `PLANS_DIR` row at `:371`, so no path
falls through to the `unmatched-path` full-suite fallback (`:541-542`).
None of the changed paths is in `GLOBAL_TRIGGER_PATHS` (`:252-256`).
Confirm the run reports `domain-selected`, not a full-suite reason — a
full-suite report for this diff is a rule-table bug to raise, not a
licence to widen by hand (root `CLAUDE.md`, Commands).

Three specific expectations inside that run:

- The two new `TestLibReviewerRoundStateCap` tests pass, and the three
  existing ones still pass unchanged (assumption row 4).
- `test_require_architect_consult.py` and `test_log_reviewer_round.py`'s
  twelve legacy-sentinel fixtures still pass, proving the legacy arm
  survives the delegation (assumption row 5).
- ShellCheck on `_lib.sh` runs inside the selection via
  `test_shellcheck.py` (`claude/.claude/hooks/tests/`), so no separate
  `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` invocation
  is needed for this diff.

Manual check, since nothing parses the file (assumption row 7): re-read
the rewritten `config-schema-audit.md` section against the final
`_lib.sh` code and confirm each of the five bullets describes the code as
merged — in particular that no sentence survives claiming the schema row
is reporter-only.

Then `/code-review` before commit, per root `CLAUDE.md`. This diff
contains no `SKILL.md`, agent file, rule file, plugin file, or
`config-keys.psv` change, so `.claude/rules/review-pipeline-dispatch.md`
routes no additional required review.

## Out of scope

- **The other 14 migrated keys.** The audit is settled (assumption row
  13); every other enforcement call site already delegates. Do not re-run
  the sweep and do not "fix" a key that is already correct.
- **`.claude/worktree-required` and `.claude/session-title-disabled`.**
  Committed per-repo opt-ins, deliberately file-only and outside the
  machine-level `claude-config.toml` scope — `config-schema-audit.md:322-326`
  records the session-title one as explicitly out of the migration's
  scope. Converting either is a separate design decision.
- **Retiring the legacy sentinel or adding a migration for it.** Given
  G1 — the fallback is permanent by schema policy, and this fix depends on
  it continuing to work.
- **`enforce-marker-script-shape.sh` write-gating**, descoped per
  `docs/design-decisions/sentinel-config-consolidation.md`. Unchanged by
  this plan.
- **Any broader `_lib.sh` refactor.** One function body and one comment
  block change; the neighbouring round-state functions, the capped-git-call
  accounting, and the `_LIB_REVIEWER_ROUND_STATE_CAP` constant's value all
  stay as they are.
- **`docs/design-decisions/round2-consult-trigger-pilot.md`.** Its
  Mechanism section (lines 19-21) still describes the pilot as a
  presence-only sentinel file. The plan could edit it and deliberately
  does not: it is a dated (2026-09-08) decision record governed by
  `.claude/rules/design-decisions.md`, the legacy file it names still
  resolves the pilot so the record is not false, and the current-behavior
  surface for this key — `docs/hooks.md:54,81` — already documents the
  post-fix resolution correctly (assumption row 6). Re-pointing that
  record at the TOML key belongs with a supersession pass over the
  migration commit's records, not here.
- **Test coverage for `_config_enabled` exit codes 3 and 4** at this call
  site, per M3's stated reason.
- **Restoring a capped (`_lib_capped`) probe** around the pilot lookup.
  Given G2, that would mean reintroducing a second resolution path;
  the uncapped read matches all 14 sibling call sites, including
  `_lib_round_consult_gate_disabled` in the same hook.
