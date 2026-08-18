# Unflagged-holdout sampler for turn-shape calibration recall

## Context

Add a `turn-shape-holdout-samples` subcommand to `transcript-analysis.py` so
a human rater can sample the *unflagged* population of assistant turns
(single-call streaks of length 1) and measure recall for the batching and
delegation tool-call-economy rules — the one half of Verification 10 in
`.claude/plans/tool-call-compliance-enforcement.md` that the already-shipped
`turn-shape-samples` cannot produce, because it only samples flagged streaks
(length ≥ 2).

Phase 0 (dedup bugfix, PR #688) and Phase 1 (`turn-shape` /
`turn-shape-samples`, PR #693) are merged to `main`. Verification 10's
precision half is unblocked and can proceed independently of this task. This
task unblocks the recall half by adding the missing sampling path; it does
not run the rating pass itself (that's a later, human-in-the-loop step) and
does not touch the two already-shipped subcommands.

## Approach

Add a new sibling subcommand, `turn-shape-holdout-samples`, modeled directly
on `cmd_turn_shape_samples` — same population-building calls, same
`_DO_NOT_PUBLISH_BANNER` stamping, same `--this-repo`/`--projects`/`--since`
flags — but selecting the exact-length-1 streaks `turn-shape-samples`
excludes, and adding one new flag, `--offset`, so a rater can page through
the shuffled population across repeated invocations without any tool-side
bookkeeping. The 30-true-violations-or-1,000-runs sizing rule (pre-registered
in the parent plan) is judged by the rater from their own running count as
they page — it can't be computed in code, since only a human classification
of each run as a genuine violation or not can produce it.

Unlike the flagged sibling, `--seed` cannot default to `None` here: paging
by `--offset` across repeated invocations is only coherent if every
invocation shuffles the *same* candidate list the *same* way, so this
subcommand defaults `--seed` to a fixed constant instead of reseeding from
OS entropy each run (an explicit `--seed` still overrides it). `--offset`
itself is validated — negative values are rejected, and a window past the
end of the population is distinguished on stderr from a genuinely empty
population — since silent Python slice semantics on either edge would
otherwise produce a wrong-but-unremarkable-looking empty or wrapped page.

### Assumption ledger

```
Root: Verification 10's recall half is unmeasurable today — no subcommand
samples the unflagged (length-1-streak) population; turn-shape-samples only
samples flagged streaks (length >= 2).
Givens: transcript JSONL layout (session records under
`~/.claude/projects/**`, `type`, `message.content` tool_use blocks,
`gitBranch`, `isSidechain` fields) is the Claude Code harness's own storage
format — beyond reach: owned by the harness, not this repo;
`_turn_shape_session_turns`/`_turn_shape_streaks` already encode reading it,
this plan only consumes those functions unchanged.

Row 1 [mechanism]: new `turn-shape-holdout-samples` subcommand, not a
`--population` flag on `turn-shape-samples` — anchors: root — lighter
alternatives considered from this file's own source: (a) a `--population
{flagged,unflagged}` flag on the existing subcommand, rejected because it
would require editing `cmd_turn_shape_samples`'s own signature and body,
which this task's brief scopes out; (b) a shared helper function with both
subcommands calling into it, rejected because the only shared logic (three
lines: seed, shuffle, slice) is smaller than the abstraction needed to share
it without touching the existing command.
Row 2 [assumption]: this repo's own convention is sibling subcommands, not
mode flags, for a shape/samples pair — of 34 total subcommands in
transcript-analysis.py, 0 take a `--population` or `--mode` flag, and two
shape/samples sibling pairs already exist (`audit-routing-shape` /
`audit-routing-samples`, `turn-shape` / `turn-shape-samples`)
[verified: claude/.claude/scripts/transcript-analysis.py, grepped
`sub.add_parser(` (34 hits) and `--population\|--mode` (0 hits)] —
anchors: row1
Row 3 [mechanism]: reuse `_turn_shape_session_turns` and `_turn_shape_streaks`
unchanged; filter on a new `_TURN_SHAPE_HOLDOUT_STREAK_LEN = 1` exact-length
constant, not a lowered `_TURN_SHAPE_SAMPLES_MIN_STREAK_LEN` — anchors: root
— a length-1 return from `_turn_shape_streaks` is exactly the unflagged
population: `turn-shape-samples`'s own filter is `len(streak) >= 2`, so
`== 1` is its exact complement over the same streaks list
[verified: claude/.claude/scripts/transcript-analysis.py:8761-8919,
cmd_turn_shape_samples's `_TURN_SHAPE_SAMPLES_MIN_STREAK_LEN = 2` filter] —
anchors: row1
Row 4 [mechanism]: `--offset` pages a shared-seed shuffle across repeated
invocations instead of mechanically enforcing the sizing rule in code —
anchors: root — two lighter alternatives considered: (a) no
pagination, rely on one `--sample 1000` run — rejected, forces one
unreviewable giant batch instead of incremental rating; (b) a stateful
"already-seen" file the tool writes and reads back — rejected as heavier
than needed, and it would itself be exactly the "shared file carrying raw
content" (if it records run identifiers) or performative dead weight (if it
doesn't) that Process discipline bars; a pure arithmetic window
(`candidates[offset:offset+sample_n]` after `rng.shuffle`) needs no
persisted state at all.
Row 5 [assumption]: the running 30-or-1,000 tally lives only in each
rater's own head/scratch notes across successive invocations, never written
to the repo, satisfying Process discipline
[verified: .claude/plans/tool-call-compliance-enforcement.md:333-340] —
anchors: row4
Row 6 [assumption]: two raters sharing one `--seed` value and paging in
lockstep (same `--offset`/`--sample` sequence) land on the exact same runs
at each step, because `random.Random(seed)` + `rng.shuffle` is deterministic
given an identical candidate population from the same corpus scope
[verified: claude/.claude/scripts/transcript-analysis.py:8902-8904 — the
existing flagged-sample subcommand already relies on this same determinism
for its own inter-rater kappa] — anchors: row4
Row 7 [assumption]: the candidate population stays stable across the
several invocations one rating pass makes (no new sessions landing in the
scanned window mid-pass) [unverified] — anchors: row6 — a real risk if a
rater's pass spans hours while new sessions are written; flagged as a
documented operational caveat (bound the pass with `--since`, don't span it
across days) rather than built as a corpus-freezing mechanism, since that
mechanism would be heavier than this task's scope calls for and the
existing flagged-sample precision measurement carries the identical,
already-accepted risk.
Row 8 [assumption]: `turn-shape-holdout-samples` (not
`turn-shape-unflagged-samples`) is the clearer sibling name, because
"holdout" is the term the parent plan already uses for this population
[verified: .claude/plans/tool-call-compliance-enforcement.md:317-318,
"unflagged holdout"] — anchors: root
Row 9 [mechanism]: `--seed` defaults to a fixed constant instead of `None`
for this subcommand only — anchors: row6 — Row 6's determinism claim only
holds if every invocation shuffles the same candidate list identically;
`cmd_turn_shape_samples`'s own `--seed=None` default (unchanged, out of
scope) is safe there because that subcommand never pages — a single
invocation's shuffle order never has to match a later one. This subcommand's
core value proposition (paging without persisted state) breaks silently
without a held-constant seed, so the default cannot mirror the sibling's.
Row 10 [assumption]: `--offset` needs explicit bounds handling — anchors:
row4 — bare `type=int` accepts a negative value, which Python slice
semantics would silently reinterpret as counting from the end of the
shuffled list rather than erroring, and `offset >= len(candidates)` would
silently print only the banner with no way to distinguish "paged past the
end" from "corpus has zero candidates in this scope"; both are validated
explicitly (Critical files, below) rather than left to fall through to
Python's default slice behavior.
```

## Critical files

- `claude/.claude/scripts/transcript-analysis.py`
  - New: `_TURN_SHAPE_HOLDOUT_STREAK_LEN = 1` constant, placed next to
    `_TURN_SHAPE_SAMPLES_MIN_STREAK_LEN` (line 8873).
  - New: `cmd_turn_shape_holdout_samples()`, modeled on
    `cmd_turn_shape_samples()` (line 8876) — **reuse**
    `_turn_shape_session_turns()` (line 8709) and `_turn_shape_streaks()`
    (line 8761) unchanged; **reuse** `_DO_NOT_PUBLISH_BANNER`,
    `_print_resolved_scope`, `_resolve_scan_roots`, `_resolve_project_scope`,
    `_parse_since_nd_arg`, `_fmt_usd` unchanged. Before shuffling, reject a
    negative `--offset` with the file's established
    `print(..., file=sys.stderr); sys.exit(2)` pattern (e.g. line 2302-2309);
    after slicing, if the resulting window is empty but `candidates` is not,
    print a stderr line distinguishing "offset past end of population" from
    the population-count line already planned below (so an all-zero-length
    population reads differently from a paged-past-the-end one).
  - New argparse block: `p_turn_shape_holdout_samples`, inserted directly
    after `p_turn_shape_samples.set_defaults(...)` (after line 11079, before
    `p_sessions` at line 11081) — same `_add_project_scope_args` and
    `--since`/`--sample` shape as `p_turn_shape_samples`, plus:
    - `--seed`, `type=int`, **default a fixed constant** (e.g. `0`), not
      `None` — see Approach and ledger Row 9.
    - `--offset`, `type=int, default=0` — bounds validated in
      `cmd_turn_shape_holdout_samples()` itself, not at the argparse layer
      (matching how `--sample`'s own bounds are handled today: no
      parser-level `type=` validator elsewhere in this file).
  - Stderr diagnostic (new, alongside `_print_resolved_scope`'s existing
    call): a line reporting `offset`, the window size actually returned, and
    the total candidate count — e.g. `(offset=30, window=12 of 42 unflagged
    candidates)` — giving the rater a way to notice they've reached the end
    of the population without inspecting output length by hand.
  - **Not touched:** `cmd_turn_shape`, `cmd_turn_shape_samples`,
    `_TURN_SHAPE_SAMPLES_MIN_STREAK_LEN`'s own value or use.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - New `_turn_shape_holdout_samples_args()` helper next to
    `_turn_shape_samples_args()` (line 13724), adding an `offset` field.
  - New `TestTurnShapeHoldoutSamples` class mirroring `TestTurnShapeSamples`
    (line 13993)'s three existing tests (banner-present/no-file-written;
    empty-population no-exception; candidate body renders
    rule/length/dollars/session/turn detail), re-targeted at the length-1
    population, plus new cases specific to this subcommand:
    - A length-2 streak (built the same way `TestTurnShapeSamples`'s own
      candidate-body test builds one) is excluded from the holdout
      population — `_TURN_SHAPE_HOLDOUT_STREAK_LEN`'s exact-length filter,
      not a range.
    - `--offset` pages the shuffled population without overlap: same seed,
      `offset=0,sample=1` and `offset=1,sample=1` yield two distinct
      sessions, and `offset=0,sample=2` yields both.
    - `--offset` past the end of a non-empty population (e.g. `offset=5`
      against 2 candidates) emits the banner and the distinguishing stderr
      diagnostic, no exception.
    - A partial final page: 3 candidates, `offset=2,sample=2` returns
      exactly 1 candidate (the boundary case the offset-pagination logic
      exists to get right, per Approach).
    - Negative `--offset` exits 2 with a stderr message, via the
      `sys.exit(2)` path above.
    - Dual-rule candidacy: a single isolated, non-mutating-git Bash turn
      qualifies as a length-1 streak under both `require_bash=False` and
      `require_bash=True`, so it renders as two candidates — one labeled
      `batching`, one `delegation` — for the same session/turn. Assert this
      explicitly rather than let it pass unnoticed (unlike
      `TestTurnShapeSamples`'s candidate-body test, which sidesteps this by
      using mutating-git calls so only one rule's streak forms).
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`
  - New `test_transcript_analysis_turn_shape_holdout_samples_help_exits_zero`
    and `test_transcript_analysis_turn_shape_holdout_samples_subprocess_finds_seeded_session`,
    mirroring the `turn-shape-samples` pair at lines 81–108.
- `docs/transcript-analysis.md`
  - New `## turn-shape-holdout-samples` section placed after
    `## turn-shape-samples` (after line 1103, currently end-of-file) —
    purpose, flags (including `--offset` and the fixed-`--seed`-default
    rationale), "when to reach for it" including the offset-pagination /
    no-persisted-tally protocol for two raters. Sample output uses synthetic
    session IDs and generic commands, matching the existing
    `turn-shape-samples` section's own convention (`abc12345-test`,
    `git log`) — never output pasted from a real invocation, since this is a
    public repo and the tool's real output can carry project-identifying
    content. Explicitly note the dual-rule-candidacy behavior (a single
    isolated Bash turn can render as both a `batching` and a `delegation`
    candidate) so raters recognize a repeated session/turn rather than
    double-counting it toward the 30-or-1,000 tally.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` from the worktree — full
   suite, including the new unit tests and the two new real-subprocess
   tests.
2. `../../../.venv/bin/ruff check claude/.claude/` from the worktree.
3. Manual smoke check: `transcript-analysis.py turn-shape-holdout-samples
   --this-repo --sample 5` against this repo's own corpus — confirms the
   banner prints, at least one candidate renders (this repo has isolated
   single-call turns), and a repeat run (relying on the fixed `--seed`
   default, no flag passed) reproduces identical output, while
   `--offset 5` reproduces a disjoint slice of the same shuffle.
4. `/code-review`, then dispatch `staff-backend-engineer` and `staff-sdet`
   per this plan's own established review pattern for its Phase 0/1 PRs
   (#688, #693).

## Out of scope

- Modifying `cmd_turn_shape` or `cmd_turn_shape_samples` themselves.
- Running Phase 2 (`PostToolBatch` advisory hook) — gated on completed
  calibration (precision *and* recall, both rules).
- Actually rating the sample or computing final precision/recall/κ numbers
  — this plan builds the tool only.
- Redesigning the sizing-rule numbers (30 violations / 1,000 runs) or the M8
  floors (precision ≥ 0.70, κ ≥ 0.61) — both are pre-registered in
  `.claude/plans/tool-call-compliance-enforcement.md` and, per that plan's
  own M8 rationale, are the engineer's to revise before a calibration run,
  not this plan's to touch mid-implementation.
- Loosening Verification 10's "Process discipline" constraint (no shared
  file carrying raw sample content committed, handed off, or pasted into a
  PR body) — this task's own `--offset` design (Approach, Row 4/5) exists to
  satisfy that constraint, not to renegotiate it.
- Recording the calibration outcome in `docs/cost-levers-considered.md` —
  that's a later step, contingent on the rating pass this plan doesn't run.
