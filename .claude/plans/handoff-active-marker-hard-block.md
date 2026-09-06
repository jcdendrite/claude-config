# Handoff hard-block re-fire: active-bypass marker

## Context

Sessions running `/handoff` can get truncated mid-write by
`nudge-handoff-near-context-cap.sh`'s hard-block escalation, because the
escalation's only reset (`handoff-record-conversion.sh`) runs at the very
end of a process that can span many turns (notably the "collect in-flight
background dispatches" `ListAgents` wait loop) — leaving no way to signal
"the session is now complying" until it's already finished, and no way out
of a re-fired block inside the session. This needs fixing now because it
actively strands sessions faithfully following the hook's own remediation
instruction, sometimes repeatedly.

The intended outcome: `/handoff`'s own multi-turn writing process is
protected from a re-fired hard block once it has started, via a new
`handoff` active-bypass marker mirroring the existing
`plan-review`/`ready-for-review`/`respond-pr`/`memory-skill` two-marker
pattern. The marker activates once a handoff is confirmed warranted — after
`/handoff`'s own "is a handoff warranted?" check, not as the skill's literal
first step. Activating earlier would leave a live bypass marker in a
session that checks and decides not to write a handoff at all, silently
disarming the escalation ladder for the rest of that session's life (a
live-PID marker can't be evicted by `clear-stale`). Sessions that haven't
started `/handoff`, or that decline to, remain fully subject to the
escalation ladder — only the mid-write re-block goes away.

## Approach

Add a `handoff` active-bypass marker to the existing four-skill marker
family, and make `nudge-handoff-near-context-cap.sh`'s hard-block branch
fall through to its advisory path while that marker is live. `/handoff`
writes the marker once it has decided a handoff is warranted and removes it
after recording the conversion signal, so a session that is actively
complying with the block cannot be re-blocked mid-write, while a session
that has not started `/handoff` stays fully subject to the escalation
ladder.

### Assumption ledger

**Root problem:** the escalation ladder's hard block is sticky within a
session and its only reset (`handoff-record-conversion.sh`, run from
`handoff/SKILL.md`'s last section) fires after the whole handoff completes,
so a session has no way to signal "I am complying" during the multi-turn
window the block itself creates — and a re-fire inside that window
truncates the handoff write.

**Givens** (conditions this design treats as fixed, each beyond its own
reach):

- **G1 — `PostToolBatch` exit 2 ends the turn outright; a hook cannot
  defer, soften, or partially block.** Harness-imposed contract, and one
  this repo documents as confirmed by live capture rather than by vendor
  docs (`docs/handoff-nudge.md`, "PostToolBatch has thin official
  documentation"). Any fix therefore has to decide *not to block*, before
  blocking. `[verified: claude/.claude/hooks/nudge-handoff-near-context-cap.sh`
  block branch, and `docs/handoff-nudge.md` Known limitations]`
- **G2 — the harness's `.session_id` on a hook payload and the id
  `capture-session-id.sh` records under `sessions/<pid>` are the same
  value.** Harness-owned; the entire active-bypass marker family already
  rests on it (`marker.sh` names the marker from the ancestor walk, every
  `require-*.sh` reads it from the payload). `[verified:
  claude/.claude/scripts/marker.sh` `_walk_session`/`_resolve_session_id`,
  `claude/.claude/hooks/require-memory-skill.sh`'s bypass call]`
- **G3 — `marker.sh` attributes a marker to the parent session even when a
  subagent runs it**, because the session id comes from a process-ancestor
  walk. Harness process model; it is why `enforce-marker-script-shape.sh`
  denies `marker.sh activate` from every agent in
  `_LIB_NO_GATE_RELEASE_AGENTS` at all. `[verified:
  claude/.claude/hooks/enforce-marker-script-shape.sh` gate-release-authority
  comment; `claude/.claude/hooks/_lib.sh` `_LIB_NO_GATE_RELEASE_AGENTS`]`

**Assumption rows:**

1. `[engineer-confirmed]` The new marker carries no staleness/TTL bound
   beyond PID liveness, matching the existing four markers exactly;
   `marker.sh clear-stale` is the only cleanup path. Do not add expiry
   logic.
2. `[verified: claude/.claude/hooks/_lib.sh` `_lib_active_bypass_marker_live`,
   signature `MARKER_DIR_NAME SESSION_ID`]` The helper re-derives the config
   dir itself, validates the session id, requires the stored value to match
   `^[0-9]+$` and pass `kill -0`, self-evicts an orphan, and returns 1 for
   "withhold the exception". No new argument plumbing and no new `source`
   line are needed in the nudge hook — it already sources `_lib.sh` and
   `SESSION_ID` is already validated upstream of the block branch.
3. `[verified: claude/.claude/hooks/nudge-handoff-near-context-cap.sh]` The
   subagent gate (`[ -n "$AGENT_TYPE" ] && exit 0`) runs long before any
   escalation logic, so a subagent can never hard-block. Consequence: a
   failed or denied `activate handoff` inside a subagent costs nothing,
   which is why this skill's activate step must be **non-fatal**, unlike
   `ai-instruction-and-memory-files`' Step 0 ("If the command fails …
   abort"), where a failed activate blocks every subsequent write.
4. `[verified: claude/.claude/hooks/enforce-marker-script-shape.sh`
   `MARKER_SHAPE`; `claude/.claude/settings.json` `permissions.allow`;
   `claude/.claude/skills/tests/test_skills.py` `_MARKER_TRIPLE_SITES`]` A
   marker command is a **triple** — the `permissions.allow` rule, the
   hook's shape regex, and the literal form in a SKILL.md — and a test pins
   all three to move in lockstep. Adding `handoff` is not a `marker.sh`-only
   edit.
5. `[verified: claude/.claude/scripts/handoff-record-conversion.sh]` That
   script touches only `.handoff-nudge-fired.d/<session>-ignored` and
   resolves its session id through `_lib_resolve_claude_pid` rather than
   `marker.sh` conventions. It is a different namespace from
   `.handoff-active.d` and gets no new responsibility here.
6. `[verified: claude/.claude/skills/tests/test_skills.py`
   `_PER_ACCOUNT_STATE_PATH_RE`]` The state-path contract's alternation
   covers `~/.claude/<kind>-markers/` and `~/.claude/.<kind>.d/` but not
   `~/.claude/scripts/`, so the literal `~/.claude/scripts/marker.sh
   activate handoff` recipe is permitted in a SKILL.md body (and required —
   the permissions rule and hook regex are tilde-literal), while any prose
   naming the marker directory must use the `<config-dir>/.handoff-active.d/`
   form.
7. `[verified: claude/.claude/scripts/select-tests.py` `DOMAIN_RULES`/
   `CROSS_DOMAIN_EXCEPTIONS` and `select_pytest_targets`' unmatched-path
   branch]` `claude/.claude/settings.json` matches no rule, so any change
   set containing it makes `select-tests.py` fail open to the full suite.
   That is the expected outcome for this change, not a mapping defect to
   fix.
8. `[unverified]` Re-blocks during a handoff are plausible but not
   measured: a re-arm needs the estimate to advance
   `HANDOFF_NUDGE_REARM_SPACING` (default 80000) past the last fire, which a
   long `ListAgents` collect loop with several large subagent returns can
   reach. The fix's value scales with how often that happens; nothing
   downstream in this plan depends on the frequency.

**Mechanisms and why each is not heavier than the task needs:**

- **Reuse `_lib_active_bypass_marker_live` with a new `.handoff-active.d`
  directory, rather than any new coordination primitive.** This adds no
  mechanism: it is the same PID-liveness file four skills already use, read
  through the same shared helper, with the hook's own already-validated
  `SESSION_ID`. Lighter primitives considered and rejected:
  - *Run `/handoff` in a subagent* (zero code — the subagent gate already
    exits before escalation). Fails: a subagent does not hold the parent's
    context, task list, or in-flight dispatch state, which is the entire
    content of a handoff.
  - *Reset the `-ignored` marker at the start of `/handoff` instead of the
    end* (no new namespace, no permissions entry, no shape-regex change).
    Fails on two counts: the counter keeps incrementing on every re-arm
    during the handoff, so at the shipped `HANDOFF_NUDGE_BLOCK_AFTER=1` a
    single further re-arm re-blocks — the exact truncation being fixed; and
    it destroys the conversion signal's meaning, since the reset would no
    longer record that a handoff completed.
  - *Toggle the `.handoff-nudge-disabled` kill-switch around the handoff.*
    Fails: machine-global rather than session-scoped, self-heals never (a
    skill that halts leaves every session on the machine un-nudged), and
    the closing `rm` would clobber an engineer's own deliberate opt-out.
  - *Make the block non-sticky (fire once per session, then revert to
    advisory).* Fails: it weakens the ladder identically for a session that
    is ignoring the block and one that is complying, which is the
    distinction the whole change exists to draw — and the stickiness is a
    recorded prior decision (`docs/handoff-nudge.md`, "Recovering from a
    hard block").
  - *Have `/handoff` overwrite `FIRED_MARKER` with an inflated estimate to
    push the next re-arm band out.* Fails: it writes a false fire history
    into the file `transcript-analysis.py` reads, buys only one band, and
    never self-heals if the handoff aborts — strictly worse than a
    PID-liveness marker on the one axis (bounded lifetime) that matters
    here.
- **Extend the existing block condition rather than adding an early exit.**
  Appending `&& ! _lib_active_bypass_marker_live ".handoff-active.d"
  "$SESSION_ID"` as the *last* clause keeps the per-fire cost at zero for
  every invocation that was not about to block (the earlier `[ ... ]` tests
  short-circuit first), and lets a suppressed block fall through to the
  advisory path — which keeps `FIRED_MARKER`/log bookkeeping advancing
  exactly as it does today, so re-arm spacing stays correct once the marker
  clears. The considered alternative, an early `exit 0` that suppresses the
  advisory too, would stall that bookkeeping and cause an immediate fire the
  moment the marker is removed.
- **Activate after the warrant check, deactivate after the conversion
  record.** `/handoff` is the only marker-writing skill with a genuine
  early-exit branch ("is a handoff warranted?" can conclude *no*).
  Activating before that branch would leave a live marker in a session that
  never writes a handoff, silently disarming the escalation ladder for the
  rest of that session's life — and `clear-stale` cannot evict it, because
  the PID is alive. Deactivating after `handoff-record-conversion.sh` means
  the ladder is already reset before suppression lifts; the reverse order
  reopens a one-turn window where a re-arm could block a session that has
  already finished.

### Implementation steps

1. **`claude/.claude/scripts/marker.sh` — add the `handoff` arms.** In the
   `activate` case (the `memory-skill` arm is the template), add a
   `handoff)` arm resolving `SESSION_ID`/`CLAUDE_PID`, `mkdir -p
   "$CONFIG_DIR/.handoff-active.d"`, and writing the PID to
   `$CONFIG_DIR/.handoff-active.d/$SESSION_ID`. Add the mirror `handoff)`
   arm to `deactivate` (`rm -f` the same path). Update, in the same file:
   the `usage()` heredoc's `status` description and its
   `activate`/`deactivate` valid-combination lines, both invalid-skill
   error messages (`'activate %s' is not valid…` / `'deactivate %s' is not
   valid…`), and `status`'s active-bypass block with a fifth
   `_status_report_active_bypass handoff ".handoff-active.d" "$SESSION_ID"`
   line. `clear-stale` needs no change — it walks every `.*-active.d`
   directory generically.
2. **`claude/.claude/hooks/enforce-marker-script-shape.sh` — widen the
   shape allowlist.** Add `handoff` to `MARKER_SHAPE`'s
   `(activate|deactivate)` target alternation. Add the two new
   `~/.claude/scripts/marker.sh activate handoff` / `… deactivate handoff`
   lines to the denial message's "Valid shapes" list. Correct the two
   hardcoded counts in the same file: the header's "must match one of the
   16 single-command shapes" and the marker-chain comment's "the 14 shapes
   in permissions.allow" (→ 18 and 16). No change to the gate-release-
   authority arm: `activate handoff` staying denied for
   `_LIB_NO_GATE_RELEASE_AGENTS` is correct and harmless, per assumption
   row 3.
3. **`claude/.claude/settings.json` — two exact-match allow entries.** Add
   `"Bash(~/.claude/scripts/marker.sh activate handoff)"` and
   `"Bash(~/.claude/scripts/marker.sh deactivate handoff)"` alongside the
   existing marker rules in `permissions.allow`. Exact-match only, no
   globs. **Triggers `review-permissions`.**
4. **`claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — suppress
   the block while the marker is live.** Append `&& !
   _lib_active_bypass_marker_live ".handoff-active.d" "$SESSION_ID"` as the
   final clause of the hard-block `if`, so a live marker falls through to
   the advisory fire path below. Add one sentence to the header comment
   block that already describes the escalation ladder, stating that a live
   `/handoff` active-bypass marker keeps a qualifying re-arm advisory. Do
   not touch `MARKER_DIR` / `IGNORED_MARKER` / `FIRED_MARKER` — those are
   the escalation-ladder namespace and are unrelated to `.handoff-active.d`.
5. **`claude/.claude/skills/handoff/SKILL.md` — bracket the handoff with
   activate/deactivate.** Insert a new section immediately after "Before
   writing: is a handoff warranted?" and before "Before writing: collect
   in-flight background dispatches", carrying a `<!-- HOOK_TEST_FIXTURE:
   activate-gate … -->` anchor comment (same wording convention as the
   existing `write-target`/`artifact-preamble` anchors: name the test that
   re-reads it and forbid duplicating the block) followed by a fenced block
   containing exactly `~/.claude/scripts/marker.sh activate handoff`. Its
   prose states what the marker does (suppresses the nudge hook's hard
   block for this session while the handoff is being written) and that
   **failure is non-fatal** — continue the handoff; the block may still
   fire, in which case the existing "If the hard block fires again before
   or during this wait" paragraph already says what to do. Add a final
   section after "After writing: record the conversion signal", with a
   `deactivate-gate` anchor and a fenced `~/.claude/scripts/marker.sh
   deactivate handoff` block, stating that it runs after the conversion
   record and that an interrupted skill's marker is evicted once the
   session's process ends. Update §5's parenthetical list of active-bypass
   markers to include `handoff`.
   **Line budget:** `claude/.claude/hooks/check-skill-length.sh` hard-caps
   `claude/.claude/skills/handoff/SKILL.md` at 200 lines (`limit_for()`
   carries no override entry for this file, unlike `code-review`/
   `plan-review`/`pr-description`) and blocks `git commit` on overage. The
   file is 184 lines today; write both new sections as tightly as the
   `memory-skill` template's own (8 lines each, excluding header/blank)
   to minimize growth, then run `wc -l claude/.claude/skills/handoff/SKILL.md`
   before committing — if it exceeds 200, trim elsewhere in the file (e.g.
   "You may drop" or "Slug naming") rather than shortening the new
   activate/deactivate prose below the template's own level of detail.
6. **`docs/handoff-nudge.md` — update both affected sections.**
   - *"Recovering from a hard block"*: the current text asserts there is
     "no in-session escape short of" the kill-switch or removing the
     `-ignored` marker. That becomes false — describe the third route:
     while `/handoff` itself is running, its active-bypass marker
     (`<config-dir>/.handoff-active.d/<session_id>`) keeps a qualifying
     re-arm advisory instead of blocking, so the multi-turn
     collect-dispatches wait can no longer be truncated by a re-block. Keep
     the existing statement that the ladder's *count* is still monotonic
     and still resets only by those two routes — the marker suppresses the
     block, it does not reset the counter.
   - *"Known limitations"*: add one bullet for the residual this
     introduces — the suppression is bounded only by PID liveness, so a
     `/handoff` that halts between its activate and deactivate steps leaves
     the hard block suppressed for the remainder of that session, and
     `marker.sh clear-stale` will not evict it because the owning process
     is still alive. Name the manual remedy (`marker.sh deactivate
     handoff`) and note that the advisory nudge still fires throughout, so
     the session is not left unwarned.
7. **`docs/hooks.md` — one clause on the `nudge-handoff-near-context-cap.sh`
   entry**, after the sentence describing the hard block: a live `/handoff`
   active-bypass marker keeps that re-arm advisory. Depth stays in
   `docs/handoff-nudge.md`, which that entry already points to. No change
   to the `enforce-marker-script-shape.sh` entry (it describes the
   allowlist generically, without enumerating skill names).
8. **`CHANGELOG.md` — one `### Changed` bullet under `[Unreleased]`.** This
   is a behavior change every stow consumer receives on `git pull`: the
   hard block no longer fires while `/handoff` is running in that session.
   State the mechanism, the two new `permissions.allow` entries, and that
   no migration is required (the marker is created and removed by the
   skill itself).
9. **Tests — see Verification for the case list.** Four test files change;
   treat this as part of the same dispatch, not a follow-up.

**Dispatch:** one `code-writer` dispatch for all nine steps. The marker
name, the directory name, and the literal command string appear in six
files that a triple-pinning test compares against each other; splitting
would require restating the same shared background in every prompt and
risks two agents choosing different literals.

## Critical files

- `claude/.claude/scripts/marker.sh` — the only writer of active-bypass
  markers; needs `handoff` arms in `activate`/`deactivate`, plus its usage
  text, both invalid-skill error messages, and the `status` report.
  **Reuse:** `_resolve_session_id`, `_resolve_claude_pid`,
  `_status_report_active_bypass` — copy the `memory-skill` arms verbatim
  with the name changed; write no new resolution logic.
- `claude/.claude/hooks/enforce-marker-script-shape.sh` — its
  `MARKER_SHAPE` regex is a closed allowlist of `(op, target)` pairs, so an
  unlisted `handoff` target is denied at the tool-call boundary regardless
  of `permissions.allow`. Also carries two hardcoded shape counts that go
  stale.
- `claude/.claude/settings.json` — `permissions.allow` needs the two
  exact-match entries; without them the recipe surfaces as a permission
  prompt inside every `/handoff`. Triggers `review-permissions`.
- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — the single
  behavioral edit: one clause appended to the hard-block condition.
  **Reuse:** `_lib_active_bypass_marker_live` (already in scope via the
  existing `_lib.sh` source) and the already-validated `SESSION_ID`; add no
  new config-dir resolution and no new source line.
- `claude/.claude/skills/handoff/SKILL.md` — carries the
  activate/deactivate recipes and their `HOOK_TEST_FIXTURE` anchors, and
  the §5 marker enumeration. **Reuse:**
  `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md`'s Step 0
  / Final step sections as the structural template — but with non-fatal
  failure prose, not its abort-on-failure prose.
- `docs/handoff-nudge.md` — canonical home for the escalation ladder's
  contract; both "Recovering from a hard block" and "Known limitations"
  make claims this change falsifies or extends.
- `docs/hooks.md` — the per-hook entry summarizing the hard block; one
  clause, with depth left in `handoff-nudge.md`.
- `CHANGELOG.md` — `[Unreleased]` / `### Changed`; stow consumers get this
  on `git pull` with no re-install.
- `claude/.claude/hooks/tests/test_marker_script.py` —
  `ALL_MARKER_SUBCOMMAND_ARGS` and
  `TestMarkerScriptStatusActiveBypass.ACTIVE_BYPASS_KINDS` are literal
  enumerations of the marker set (plus that class's docstring naming the
  four skills). **Reuse:** `_seed_session`, `_run`, and the two
  `memory-skill` activate/deactivate tests as templates.
- `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` —
  `TILDE_MARKER_SHAPES` is the single source of truth shared by
  `test_valid_shapes_allowed` and `TestPrescriptionAllowlistAlignment`;
  adding the two shapes there simultaneously pins hook acceptance and
  `permissions.allow` coverage. Its "16 shapes" comments need the same
  count correction as the hook.
- `claude/.claude/skills/tests/test_skills.py` — `_MARKER_TRIPLE_SITES`
  gets two `("handoff", "~/.claude/scripts/marker.sh activate|deactivate
  handoff")` rows, which is what keeps the SKILL.md literal, the allow
  rule, and the hook regex from drifting apart.
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` —
  home for the new bypass behavior tests. **Reuse:** its own `_run_hook`,
  `_base_payload`, `_seed_session`, `_ignored_marker_path`, `_log_path`,
  and `TRAVERSAL_SESSION_ID`/`plant_traversal_canary` from `helpers`; adapt
  `test_require_plan_review.py`'s four marker shapes to this file's
  subprocess/`returncode` idiom rather than importing its string-returning
  `run_hook`.
- `claude/.claude/hooks/tests/test_lib.py` — optional but recommended
  one-line comment fix: the `_lib_active_bypass_marker_live` section opens
  by calling the helper's consumers "the four bypass-shaped gates
  (`require-{…}.sh`)", which becomes an incomplete census once the nudge
  hook reads it too.

## Verification

**Required reviewer/skill dispatches:**

- **`review-permissions`** — mandatory: this change edits
  `permissions.allow` in `claude/.claude/settings.json`.
  `ask-review-permissions.sh` will also raise an ask on the edit itself.
  Both new entries must be exact-match (`Bash(~/.claude/scripts/marker.sh
  activate handoff)`), never globbed.
- **`/skill-review`** — mandatory and hook-enforced:
  `claude/.claude/skills/handoff/SKILL.md` is staged, so
  `require-skill-review.sh` blocks `git commit` until the
  behavioral-equivalence marker is written
  (`.claude/rules/review-pipeline-dispatch.md`). `/code-review` dispatches
  it automatically.
- **`claude-hook-review:claude-hook-review`** — recommended: two hook
  files change (`nudge-handoff-near-context-cap.sh`,
  `enforce-marker-script-shape.sh`), and `CLAUDE.md` routes hook design and
  review there. Invoke by fully-qualified plugin name, unprefixed.
- **`/code-review`** before commit, **`/plan-review`** on this plan,
  **`/ready-for-review`** before pushing — the standard pipeline.

**New pytest cases (all required):**

In `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`, a
new class covering the block-suppression behavior. Every case drives the
ladder to the block point exactly as
`test_escalation_ladder_blocks_once_block_after_ignored_rearms_reached`
does (`HANDOFF_NUDGE_BLOCK_AFTER: "2"`, three fires at `LARGE_THRESHOLD` +
`DEFAULT_REARM_SPACING` steps), then varies the planted marker at
`tmp_path/.claude/.handoff-active.d/`:

1. **Live-PID marker suppresses the block** — marker holds
   `str(os.getpid())`; the fire that would block instead returns
   `returncode == 0` with non-empty stdout parsing as the advisory payload,
   and its `nudged` log line carries no `action=block`.
2. **Dead-PID marker does not suppress, and is evicted** — marker holds
   `"99999999"`; `returncode == 2`, empty stdout, stderr containing
   `/handoff` and `HANDOFF_NUDGE_BLOCK_AFTER=`, and the marker file no
   longer exists afterward.
3. **Another session's marker does not suppress** — marker planted under a
   different session id; the payload's session still hard-blocks.
4. **Traversal session id** — reuse `TRAVERSAL_SESSION_ID` and
   `plant_traversal_canary`; the hook must reject the id upstream (it
   already exits at `_lib_valid_session_id_component`) and leave the canary
   byte-identical.
5. **No marker at all** — the existing block behavior is unchanged (guards
   against the new clause inverting).
6. **SKILL.md recipe alignment** — seed `sessions/<pytest pid>` with
   `_seed_session`, run
   `run_skill_command(extract_skill_command(HANDOFF_SKILL, "activate-gate"), …)`
   in the isolated `$HOME`, assert the marker lands at the exact path the
   hook reads, then assert the would-be-blocking fire is advisory; repeat
   with the `deactivate-gate` recipe and assert the block returns. This is
   what stops the skill recipe and the hook's directory name from drifting.
   (`marker.sh activate` never calls `_resolve_repo_root`, so the isolated
   `cwd` need not be a git repo.)

In `claude/.claude/hooks/tests/test_marker_script.py`: two new rows in
`ALL_MARKER_SUBCOMMAND_ARGS`; `test_activate_handoff_creates_active_marker`
/ `test_deactivate_handoff_removes_active_marker` mirroring the
`memory-skill` pair; a `("handoff", ".handoff-active.d")` row in
`TestMarkerScriptStatusActiveBypass.ACTIVE_BYPASS_KINDS` plus its docstring
update.

In `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`: two
new entries in `TILDE_MARKER_SHAPES` (which automatically extends both
`test_valid_shapes_allowed` and the `permissions.allow` alignment check)
and the three "16 shapes" comment counts.

In `claude/.claude/skills/tests/test_skills.py`: two new
`_MARKER_TRIPLE_SITES` rows.

**Commands:**

- `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — expect it to
  report a full-suite run: `claude/.claude/settings.json` matches no domain
  rule, and an unmatched path makes `select_pytest_targets` fail open.
  That is the correct outcome for this change set, not a mapping gap to
  patch.
- `.venv/bin/ruff check claude/.claude/`
- `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — covers
  both edited hooks and `marker.sh`.
- `wc -l claude/.claude/skills/handoff/SKILL.md` — must stay at or under
  200 (`check-skill-length.sh`'s default cap, hard-enforced at `git commit`;
  see step 5's line-budget note).

**Optional live check** (not a substitute for the tests above): in a real
session, run `~/.claude/scripts/marker.sh activate handoff`, then
`~/.claude/scripts/marker.sh status`, and confirm the active-bypass block
reports `handoff: live`; follow with `deactivate` and confirm it reports
`absent`.

## Out of scope

- **The escalation ladder's own counting, reset, and decay logic.** The
  per-re-arm increment of `<session>-ignored`, the 30-day marker sweep,
  `HANDOFF_NUDGE_REARM_SPACING`/`HANDOFF_NUDGE_BLOCK_AFTER` and their
  defaults, and the count's monotonicity all stay exactly as they are.
  This change suppresses a block; it never resets a counter.
- **The block's stickiness within a session.** Making the hard block fire
  once rather than on every subsequent re-arm is a real alternative
  design, deliberately not taken: it would weaken the ladder for
  non-complying sessions too, and the stickiness is a recorded decision in
  `docs/handoff-nudge.md`.
- **Any TTL, expiry, or age bound on the new marker.** Engineer-decided:
  PID liveness only, `clear-stale` as the sole cleanup, matching the
  existing four markers. `_lib_active_bypass_marker_live`'s own comment
  observes that an age bound would close the halt-between-activate-and-
  deactivate window for the whole family — that is a family-wide change,
  not this plan's.
- **`claude/.claude/scripts/handoff-record-conversion.sh`.** It keeps
  resetting only the `-ignored` marker, keeps its own
  `_lib_resolve_claude_pid` resolution, and gains no `marker.sh deactivate`
  call; deactivation lives in the skill's own final step.
- **The `--check` JSON contract.** No new field reporting the marker.
  `--check` is a measurement query; the marker is a suppression signal, and
  `plan-it` Step 7 and the skill's own warrant check both consume the
  documented shape.
- **The `.handoff-nudge.log` format.** No new `action=` value for a
  suppressed block. Adding one would force a semantic decision in
  `transcript-analysis.py`'s `_operator_response_lag_from_log` (which
  excludes `action == "block"` exactly) and ripple into that module's tests
  and the log-format tables in two docs. The completed-handoff signal
  already lands in the same log as a `handoff session=<id>` line.
- **`claude/.claude/hooks/session-marker-dashboard.sh`.** It reports three
  of the four existing active markers at `SessionStart`; a session-scoped
  marker from a dead session is never visible to a differently-identified
  resumed session anyway, so adding a fifth line buys nothing.
- **Any carve-out in `enforce-marker-script-shape.sh`'s gate-release-
  authority deny.** `activate handoff` will keep being denied for
  `_LIB_NO_GATE_RELEASE_AGENTS`; the skill's non-fatal activate prose is the
  correct resolution, and weakening a fail-closed security check for one
  skill name is not.
- **The `Stop`-event coverage gap.** A session that only crosses re-arm
  bands on toolless turns still never hard-blocks; that documented
  limitation is unrelated and unchanged.
- **`marker.sh clear-stale`.** It already walks every `.*-active.d`
  directory generically and needs no `handoff` awareness.
