# Context hygiene guidance + nested env.* guard gap

## Context

This is Phase 4 and Phase 5a of `.claude/plans/token-cost-reduction.md`
(Phases 1–3 already shipped: PR #622, #630, #636). Both remaining phases
are small, independent, mechanical edits the engineer confirmed doing
together in one PR this session; Phase 5b (an `opusplan` routing
investigation) and Phase 6 (dropped — superseded by PR #617's
`cost-ledger` subcommand) are explicitly out of scope here.

**Phase 4** adds two guidance lines to `claude/.claude/CLAUDE.md` — a
phase-boundary `/clear` nudge and a pre-idle `/compact` nudge — because
the parent plan's own cost analysis identifies session length (average
context, `C_bar`) as the dominant cost lever, and CLAUDE.md currently
gives the engineer/agent no explicit guidance on when to reset it.

**Phase 5a** closes a real bypass in `guard-settings-session-keys.sh`:
the hook's own parent plan (Approach section) states that committing
`env.CLAUDE_CODE_EFFORT_LEVEL` or `env.ANTHROPIC_MODEL` to
`claude/.claude/settings.json` would ship one machine's local
model/effort override as everyone's shipped config — exactly the harm
the existing top-level `model`/`effortLevel` guard exists to prevent —
but the guard's key match is top-level-only, so the nested form
currently evades it silently.

Intended outcome: CLAUDE.md carries the two reviewed guidance lines, and
`guard-settings-session-keys.sh` blocks the nested `env.*` forms with
test coverage proving it, without changing behavior for any
already-guarded top-level key.

## Approach

### Assumption ledger

```
Root: two independently-scoped gaps in this repo's cost/config hygiene
remain unaddressed by token-cost-reduction.md's shipped phases — CLAUDE.md
gives no explicit guidance on when to reset session context, and
guard-settings-session-keys.sh's key-matching is bypassable via a nested
env.* path.
Givens: which settings.json keys have a documented CLAUDE_CODE_*/
ANTHROPIC_* env-var equivalent is fixed by Anthropic's own settings.json
and environment-variables reference docs — beyond reach: this repo
cannot add or remove which keys the Claude Code CLI itself treats as
env-overridable; it can only guard the ones vendor docs confirm exist
today (see Row 4).

Row 1 [mechanism]: dot-split path-traversal restructure of guarded_value
(reduce-based, presence tracked separately from value) — anchors: root —
closes the nested env.* bypass while preserving identical behavior for
existing top-level keys (a single-segment path degenerates to the
original has()/[] check).
Row 2 [assumption]: guard-settings-session-keys.sh's guarded_value only
matches top-level keys today (has($key) on $settings directly)
[verified: read claude/.claude/hooks/guard-settings-session-keys.sh:107-119
this session] — anchors: row1
Row 3 [assumption]: env.CLAUDE_CODE_EFFORT_LEVEL and env.ANTHROPIC_MODEL
are the two real env-var equivalents worth guarding, not a hypothetical
pair [verified: docs/hooks.md:25 already documents these as the env-var
equivalents of the guarded model/effortLevel keys] — anchors: row1
Row 4 [assumption]: no other guarded top-level key
(skipAutoPermissionPrompt, skipWorkflowUsageWarning, theme, tui) has a
documented Claude-Code env-var equivalent that would also need guarding
in this same pass [verified: Anthropic's settings.md "Settings and
Environment Variable Equivalents" table and env-vars.md reference,
checked this session — none of the four have one] — anchors: row1
Row 5 [assumption]: claude/.claude/hooks/tests/test_guard_settings_session_keys.py
already exists (473 lines, added by PR #573) — the parent
token-cost-reduction.md plan's claim that "no test file exists for this
hook today" is stale [verified: file present at that path this session;
git log shows PR #573 added it, predating the parent plan] — anchors: row1
Row 6 [mechanism]: two new Working Style bullets in CLAUDE.md
(phase-boundary /clear, pre-idle /compact) — anchors: root — gives
explicit session-hygiene guidance the parent plan's cost analysis
identifies as the dominant lever (session length / C_bar) but CLAUDE.md
currently doesn't state.
Row 7 [assumption]: the "review markers survive across a /clear"
mechanism is already stated under CLAUDE.md's existing "## Safety"
section, so restating it in the new Phase 4 bullets would be duplication
[verified: ai-instruction-and-memory-files review this session flagged
and trimmed exactly this duplication from an earlier draft] — anchors: row6
Row 8 [engineer-verified]: bundling Phase 4 and Phase 5a together into
one PR, in this order, with Phase 5b and Phase 6 deferred — anchors: root
— the engineer confirmed this ordering and scope explicitly this session.
```

### Phase 4 — CLAUDE.md guidance lines

Two bullets under the existing `## Working Style` section, alongside the
file's other single-bullet behavioral rules (`Default-consider
delegation`, `Locate before a whole-file read`). Drafted and reviewed via
the `ai-instruction-and-memory-files` skill this session before being
finalized here — its review flagged one duplication (an earlier draft's
justification clause restated the "review markers survive across a
`/clear`" mechanism already stated under this same file's `## Safety`
section) and one redundancy in the `/compact` bullet's phrasing, both
trimmed in the text below.

Final bullet text:

> **Clear at phase boundaries.** Run `/clear` when a PR ships or before
> starting unrelated work rather than continuing in the same session —
> a fresh session starts near the input-token floor instead of carrying
> finished work's context forward at cost-inflating scale.
>
> **Compact before idling.** Before letting a session sit idle, run
> `/compact` unless a review fix-loop is still open — a cache-cold
> resume otherwise reprocesses full context at full price instead of a
> cheap cached read, and compacting mid-loop risks losing findings not
> yet settled.

**Alternative considered and set aside:** a new `## Context Management`
section header. Two bullets don't warrant a new header when
`## Working Style` already holds behaviorally-similar single-bullet
rules; a new section would add a heading line for no added clarity.

**Alternative considered and set aside:** lowering the auto-compaction
threshold instead of a manual pre-idle nudge. The parent plan's Approach
section already declined this explicitly (review-narrative continuity
is a real casualty of a lossy mid-loop summary); this phase's `/compact`
bullet is deliberately scoped to a manual, pre-idle action, not a
config change to the automatic trigger point.

### Phase 5a — Nested env.* guard

**Root problem:** `guarded_value`'s `$settings | has($key)` only ever
tests a top-level key on `$settings`. A literal dotted string like
`"env.CLAUDE_CODE_EFFORT_LEVEL"` in `GUARDED_KEYS_JSON` is itself just
another top-level key name to `has()` — it will never match a value
actually living at `.env.CLAUDE_CODE_EFFORT_LEVEL`, so appending it to
the list as-is is a silent no-op. [verified: read
`claude/.claude/hooks/guard-settings-session-keys.sh:107-119` this
session]

**Fix:** restructure `guarded_value` to split the key on `.` and walk
the resulting path, tracking presence separately from value at each
step (so an explicit `null`/`false` mid-path is still distinguishable
from "key absent," matching the existing top-level test coverage's
guarantee):

```jq
def guarded_value($settings; $key):
  ($key | split(".")) as $path
  | reduce $path[] as $seg
      ({present: true, value: $settings};
       if .present and (.value | type) == "object" and (.value | has($seg))
       then {present: true, value: .value[$seg]}
       else {present: false, value: null}
       end)
  | if .present then [.value] else [] end;
```

A single-segment path (any existing top-level key with no `.`) behaves
identically to today's `has($key)` check — the `reduce` runs once over
the one segment, so no existing guarded key's behavior changes. This is
the reason to restructure the one shared function rather than add a
second, nested-only code path: two code paths for "is this key
guarded" would let them silently drift, which is exactly the shape
CLAUDE.md's "audit structural siblings" rule warns about.

**Alternative considered and set aside:** jq's built-in `getpath`.
`getpath($path)` on a missing path segment returns `null` directly,
which is indistinguishable from a genuinely-set `null` value — the
same ambiguity `guarded_value` was already written to avoid for
top-level keys (`test_guarded_key_set_to_null_against_absent_denies`).
Using `getpath` bare would silently regress that guarantee for the new
nested keys; the explicit `reduce`-with-presence-flag above is the
smallest change that keeps it.

**`GUARDED_KEYS_JSON` addition:** append `"env.CLAUDE_CODE_EFFORT_LEVEL"`
and `"env.ANTHROPIC_MODEL"` — the two keys the parent plan names as the
bypass path for the guard's existing `model`/`effortLevel` entries.
[verified: `docs/hooks.md:25` already documents `ANTHROPIC_MODEL`/
`CLAUDE_CODE_EFFORT_LEVEL` as the env-var equivalents of the guarded
`model`/`effortLevel` settings keys, corroborating these are the two
real nested paths worth guarding, not a hypothetical pair] A
`/plan-review` round raised whether the other four guarded top-level
keys (`skipAutoPermissionPrompt`, `skipWorkflowUsageWarning`, `theme`,
`tui`) have their own undocumented env-var equivalents that would need
guarding in this same pass. [verified: Anthropic's `settings.md`
"Settings and Environment Variable Equivalents" table and `env-vars.md`
reference, checked this session — none of the four have a documented
env-var equivalent.] No further `GUARDED_KEYS_JSON` entries needed.

**Test file:** `claude/.claude/hooks/tests/test_guard_settings_session_keys.py`
already exists (473 lines) — the parent plan's citation that "no test
file exists for this hook today" is stale against current `main` and is
corrected here rather than repeated. [verified: file present at that
path this session, `git log` shows it added by PR #573, predating this
plan] Add cases to the existing file, following its established
`settings_repo` fixture + `stage_settings`/`run_hook` helper pattern.
The first six mirror this file's own existing per-invariant top-level
tests (`test_guarded_key_added_where_main_lacks_it_denies`,
`test_guarded_key_set_to_null_against_absent_denies`,
`test_guarded_key_set_to_false_against_absent_denies`,
`test_both_changed_denies_commit`) at the nested level — a
`/plan-review` round flagged their earlier absence as an inconsistent
gap against this file's own house style, not a deliberate narrowing:
- nested `env.CLAUDE_CODE_EFFORT_LEVEL` added where main lacks `env`
  entirely → deny (the realistic shape: a fresh `env` block written by
  `/effort`)
- nested `env.CLAUDE_CODE_EFFORT_LEVEL` changed where main already has
  a different value → deny
- nested `env.ANTHROPIC_MODEL` added → deny
- both nested keys changed in the same commit → deny (mirrors
  `test_both_changed_denies_commit` at the nested level)
- nested `env.CLAUDE_CODE_EFFORT_LEVEL` explicitly `null` where main's
  `env` object lacks that leaf entirely → deny (mirrors
  `test_guarded_key_set_to_null_against_absent_denies`; pins the
  presence-vs-value distinction the `reduce` design exists to preserve)
- the whole `env` key staged as a non-object (e.g. a string) where main
  has a real `env.CLAUDE_CODE_EFFORT_LEVEL` value → deny (pins the
  `(.value | type) == "object"` guard: a corrupted intermediate shape
  must fall through to "absent," which still produces a deny when the
  other side has a real value, not a silent allow)
- an unrelated `env.SOME_OTHER_VAR` change, in two shapes: (a) added to
  an `env` object that already exists on main, and (b) added via a
  freshly-created `env` object main lacks entirely → both allow (must
  not over-guard the whole `env` block, only the two named leaves; the
  two shapes exercise different branches of the presence walk)
- existing top-level-only test cases are re-run unmodified as the
  regression check that the `guarded_value` restructure preserves
  current behavior

**Docs:** `docs/hooks.md:25`'s one-line description of this hook
currently only names the top-level keys it guards. Update it in the
same sentence to note the nested `env.*` equivalents are guarded too —
in-file scope on a file this phase's own critical-files list already
touches, and a current-behavior description (CLAUDE.md Axis 3), not a
historical record.

## Critical files

| Path | Change |
| --- | --- |
| `claude/.claude/CLAUDE.md` | Phase 4. Two new bullets under `## Working Style`, text above. |
| `claude/.claude/hooks/guard-settings-session-keys.sh` | Phase 5a. Restructure `guarded_value` for path traversal; append the two nested keys to `GUARDED_KEYS_JSON`. |
| `claude/.claude/hooks/tests/test_guard_settings_session_keys.py` | Phase 5a. Add the eight new cases above to the existing `TestGuardSettingsSessionKeys` class (the ninth bullet is the existing-tests regression check, not a new test); reuse `settings_repo`/`stage_settings`/`run_hook`, no new fixtures needed. |
| `docs/hooks.md` | Phase 5a. One-sentence update to this hook's existing description line (`:25`) noting the nested `env.*` forms are also guarded. |

**Reuse:** the existing `settings_repo` fixture and `stage_settings`/
`run_hook`/`run_hook_reason` helpers in the test file cover everything
the new cases need — no new test infrastructure.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/ -k GuardSettingsSessionKeys -q`
   — all existing cases still pass (regression check on the
   `guarded_value` restructure) and the new nested-key cases pass.
2. Full suite: `../../../.venv/bin/pytest claude/.claude/ -q`.
3. `../../../.venv/bin/ruff check claude/.claude/` — no Python changes
   expected to trip anything, run for completeness.
4. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
   — covers the hook's shell edit.
5. Manual: stage a `claude/.claude/settings.json` with
   `{"env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}` against a `main`
   lacking that key, attempt `git commit`, confirm deny; confirm an
   unrelated `env` key still allows.
6. `ai-instruction-and-memory-files` re-read of the final CLAUDE.md diff
   as part of `/code-review`'s normal dispatch for CLAUDE.md changes,
   confirming the trims from this session's pre-review pass landed.

## Out of scope

- Phase 5b (`opusplan` model-routing investigation) and Phase 6 (cost
  ledger) — separate phases, scheduled/dropped per the engineer's
  decision this session, not touched here.
- Any change to the auto-compaction threshold itself — declined by the
  parent plan; Phase 4's `/compact` bullet is a manual pre-idle nudge
  only.
- Guarding any `env.*` key beyond the two named here. The parent plan
  names exactly `CLAUDE_CODE_EFFORT_LEVEL` and `ANTHROPIC_MODEL` as the
  bypass path for existing guarded keys; guarding the entire `env`
  block would block legitimate unrelated env-var commits with no
  stated harm to prevent.
- Redesigning `guard-settings-session-keys.sh`'s overall mechanism (a
  git-commit gate diffing staged `settings.json` against `main` on a
  fixed key allowlist). This plan extends the guard's key-matching only;
  the gate's trigger/diff shape is this repo's existing, working
  control and unrelated to closing the nested-key bypass.
