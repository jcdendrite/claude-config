# Handoff-nudge hook: replace the flaky wall-clock latency assertion

## Context

`test_latency_under_500ms` in
`claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` asserts
that `nudge-handoff-near-context-cap.sh` completes in under 500ms against a
10,000-line transcript, and fails intermittently on a quiet developer
machine (observed 0.569s in one of three isolated runs) even though nothing
about the hook has changed recently and CI (a differently-loaded
environment) passes reliably. The goal is a suite that passes reliably on a
developer machine, with the hook's runtime either shown to be within an
intentional budget or brought back inside one.

## Approach

Replace the absolute 500ms bound with a same-run relative comparison: run
the hook against a 200-line transcript (the `tail -n 200` window) and a
150,000-line (~29MB) transcript, and assert the two runtimes stay within
`TRANSCRIPT_SCALING_RATIO = 3.0` / `TRANSCRIPT_SCALING_SLACK_SECONDS = 1.0`
of each other — the exact constants this repo's own sibling pattern already
uses for the identical "cost shouldn't scale with N" property. The hook
itself is not modified; direct measurement below shows its runtime is
dominated by process-spawn/jq-startup cost that is already bounded
independent of transcript size, not by a defect worth fixing.

**Root problem:** a fixed absolute wall-clock bound cannot distinguish "the
machine is under real load" from "the hook regressed to an O(file_size)
read," because on this developer machine the two produce overlapping
magnitudes of slowdown.

**Givens:**
- Claude Code's hook timeout mechanism (30s default for `UserPromptSubmit`
  command hooks, 600s default for `Stop`'s) is vendor-controlled platform
  behavior this repo cannot change — the only condition here genuinely
  outside the plan's own reach.

(`pyproject.toml`'s `-n auto` default and the hook's own subprocess-count
design are both inside this repo's reach; the plan declines to touch either
deliberately, so they're recorded in **Out of scope** below with their
reasons rather than listed here as givens.)

**Mechanism 1 — replace the absolute bound with a relative (ratio + slack)
comparison.** `anchors: root`
- *Alternative: raise the constant.* Rejected — direct measurement (below)
  shows swings up to ~13.8s under real background load on this machine, far
  above any constant that would still catch a real regression. The actual
  regression signal (Mechanism 2 below) is smaller than observed legitimate
  load jitter, so no fixed number safely separates the two.
- *Alternative: skip/xfail the test when load is high (e.g. gate on
  `os.getloadavg()`).* Rejected — stacks a second unverified heuristic
  threshold on top of an already-noisy signal instead of fixing the
  measurement itself (the compounding-defensive-layers smell CLAUDE.md warns
  against).
- *Alternative: static check — grep the hook script for the literal
  `tail -n 200` line.* Seriously considered; rejected as the sole mechanism
  because it pins implementation shape rather than the observable property
  (a future equally-correct rewrite of `read_latest_usage` — e.g. `head`
  plus a different line-count variable — would falsely fail a literal-string
  match). Not needed anyway: this repo already has a working relative-timing
  convention for exactly this property (below).
- **Chosen:** relative comparison, mirroring
  `claude/.claude/hooks/tests/test_require_plan_review.py`'s
  `MARKER_SCALING_RATIO` / `MARKER_SCALING_SLACK_SECONDS` pattern
  (`allowed = baseline * RATIO + SLACK_SECONDS`) and
  `test_enforce_marker_script_shape.py`'s `test_large_write_cost_stays_near_the_parse_floor`
  — both already test "cost doesn't scale with N" this way. `git grep
  pytest.mark.timing` found 10 files; these 2 use the ratio+slack pattern,
  the rest test unrelated timeout/deny behavior (see Critical files).

**Mechanism 2 — grow the large fixture from 10,000 lines (~2MB) to 150,000
lines (~29MB).** `anchors: row1`
- *Alternative: keep 10,000 lines.* Rejected — measured regression signal at
  this size (with vs. without `tail -n 200`, same file) is only ~1.7x
  (315ms → 533ms median over 8 runs), too close to the ~1.5x mean ratio
  measured as ordinary load noise between differently-sized transcripts to
  set a safe threshold.
- *Alternative: 50,000 lines (~9.5MB) — the number this plan first
  proposed.* Rejected on `staff-sdet` review: the ~7x figure that justified
  it was measured on the isolated `tail | jq -s` step alone, not the full
  hook. The full hook adds ~400-600ms of fixed overhead (`dirname`, `cat`,
  two more `jq` calls) ahead of that step, which dilutes the end-to-end
  ratio to ~2.5-2.6x at 50,000 lines — too close to the ≤1.5x noise ceiling
  to safely threshold (row 4 below), and RATIO/SLACK sized to tolerate that
  noise (see Mechanism 1) would let the regression pass through undetected.
  Confirmed by injecting the regression into a scratch copy of the hook and
  timing the *full* hook end-to-end (row 9 below).
- *Alternative: go larger still (e.g. 300,000+ lines / ~57MB+).* Rejected —
  150,000 lines already produces a clean, full-hook ~6.0x regression signal
  (row 9) against a ≤1.5x full-hook noise ceiling (row 4); a bigger fixture
  adds write/read I/O cost to every suite run for no added discriminating
  power the chosen RATIO/SLACK (Mechanism 1) needs.
- **Chosen:** 150,000 lines (~29MB).

**Mechanism 3 — median of 3 hook invocations per transcript size, not
single-shot.** `anchors: row1`
- *Alternative: single-shot per arm, matching the sibling tests exactly.*
  Rejected — single-sample runs against this hook swung 1021ms–5446ms at a
  fixed transcript size in one 12-sample window on this machine, wider than
  what the sibling tests (a single hash check / grep) likely see, because
  this hook forks more subprocesses per invocation (~7–8: `dirname`, `cat`,
  three `jq` calls, `timeout`+`tail`) than either sibling's hot path.
  Median-of-3 measurably tightens the estimate for two extra invocations per
  arm — cheap against the risk of spurious failure it removes. Samples are
  taken interleaved (small, large, small, large, small, large), not
  block-sequential per arm — `staff-sdet` review noted a block-sequential
  order lets one load burst bias an entire arm's three-sample window in the
  same direction, which interleaving spreads across both arms instead.

**Mechanism 4 — do not modify the hook.** `anchors: root`
- *Alternative: consolidate the hook's subprocess forks to cut typical
  latency.* Rejected — typical latency (~130–300ms quiet) sits roughly two
  orders of magnitude under the platform's actual enforced budget (30s
  default `UserPromptSubmit` timeout: 30s / 0.13-0.30s ≈ 100-231x), so
  there's no correctness or compliance reason to invest here now, and it
  would pull an unrelated implementation change into a test-methodology fix.
  Worth a future follow-up, not this task — recorded as a new row in
  `docs/cost-levers-considered.md` (Critical files) so a later session
  doesn't re-measure this.

**Assumption ledger:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Hook is registered on both `UserPromptSubmit` and `Stop` (`claude/.claude/settings.json:143-181`), so it runs on effectively every conversational turn. | `[verified: claude/.claude/settings.json]` |
| 2 | Claude Code's documented default hook timeout is 30s for `UserPromptSubmit` command hooks and 600s for `Stop`'s (not lowered for `Stop`) — no documented sub-second latency budget exists anywhere in the platform docs. | `[verified: https://code.claude.com/docs/en/hooks, fetched this session]` |
| 3 | Bare hook invocation (bypassing pytest) against a below-threshold transcript: ~130–240ms on this machine when quiet, 400ms–13.8s under real background load (load average measured 14–18 on a 16-logical-CPU machine during this session). Both `timeout`(1) and `gtimeout`(1) are present on this machine, so all measurements in this ledger exercise `_lib_capped_for`'s capped branch, not the uncapped fallback (`_lib.sh:38-48`). | `[verified: direct measurement, this session]` |
| 4 | Full-hook runtime does not scale with transcript size under correct behavior: median runtime for 200-line vs. 150,000-line (~29MB) transcripts stayed at ~0.8–1.5x of each other across multiple measurement windows, including under heavy load. | `[verified: direct measurement, this session]` |
| 5 | Removing `tail -n 200` (simulating the regression the test guards against), measured on the isolated `tail \| jq -s` step alone at the 50,000-line size: ~7x slower (150ms → 1030ms median, 10 runs each arm). This isolated-step number is not what the test thresholds against — see row 9. | `[verified: direct measurement, this session]` |
| 6 | Two existing repo tests already use the ratio+slack pattern for "cost shouldn't scale with N": `test_require_plan_review.py`'s `MARKER_SCALING_RATIO`/`_SLACK_SECONDS` (3.0 / 1.0s), `test_enforce_marker_script_shape.py`'s floor-relative allowance (2.5x / 0.5s). | `[verified: git grep pytest.mark.timing, this session]` |
| 7 | `docs/cost-levers-considered.md`'s one other "<500ms hook hot path" reference sits inside a historical rejected-lever record (Axis 3 preserved content per CLAUDE.md) and stays accurate regardless of this test's methodology, so it is not edited. | `[verified: docs/cost-levers-considered.md, this session]` |
| 8 | Two merged plans (`noble-sauteeing-dream.md`, `check-walk-stops-at-claude-process.md`) already scoped this test's flakiness as "pre-existing and unrelated" to their own work — corroborating, not conflicting, with fixing it now. | `[verified: git grep test_latency_under_500ms, this session]` |
| 9 | Full-hook (not isolated-step) regression injection, done by copying the hook to a scratch path and dropping `tail -n 200` from `read_latest_usage`, then timing both the real and the scratch-regressed hook end-to-end against the 200-line and 150,000-line fixtures (8 runs each, same load window): real hook ratio (150,000-line/200-line) = 0.81x median (523ms vs. 424ms); regressed-hook 150,000-line vs. real-hook 200-line baseline = 5.99x median (523ms vs. 3134ms). This is the number `TRANSCRIPT_SCALING_RATIO = 3.0` / `_SLACK_SECONDS = 1.0` (Mechanism 1) is thresholded against — `allowed = 523*3.0+1000 = 2569ms`, comfortably cleared by the correct-behavior arm and comfortably tripped by the regressed one (3134ms). | `[verified: direct measurement, this session — scratch hook at /private/tmp/.../nudge-timing/nudge-hook-regressed.sh, not committed]` |

## Critical files

- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` —
  replace `test_latency_under_500ms` (line 870) with
  `test_latency_does_not_scale_with_transcript_size`; add
  `TRANSCRIPT_SCALING_RATIO = 3.0`, `TRANSCRIPT_SCALING_SLACK_SECONDS = 1.0`,
  `SMALL_TRANSCRIPT_LINES = 200`, `LARGE_TRANSCRIPT_LINES = 150_000`
  constants near the existing "Fixture helpers" block (each with the
  one-line grounding from ledger row 9, mirroring
  `test_require_plan_review.py`'s inline-comment convention for its own
  `MARKER_SCALING_*` constants), and a single
  `_interleaved_median_seconds(small_transcript, large_transcript, tmp_path,
  runs=3) -> tuple[float, float]` helper near `_run_hook` that samples both
  arms in one interleaved loop (small/large/small/large/small/large) and
  returns `(small_median, large_median)` — a per-arm helper can't interleave
  across arms, so one helper taking both transcripts is required, not two
  (Mechanism 3). **Reuse:** `_assistant_record`, `_base_payload`,
  `_run_hook` — no new subprocess-invocation logic needed.
- `docs/cost-levers-considered.md` — append a new `## From
  handoff-nudge-latency-assertion.md` section recording Mechanism 4's
  rejected lever (consolidating the hook's subprocess forks), using this
  plan's own measured numbers (ledger rows 3-4, Mechanism 4's ~100-231x
  headroom figure), so a later session doesn't re-measure it. Existing
  sections (e.g. the `noble-sauteeing-dream.md` one) are Axis-3 preserved
  historical record and are not edited.
- No other file changes. `nudge-handoff-near-context-cap.sh` is not edited,
  so the shellcheck step is not applicable.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` from the worktree — full
   suite green. This confirms correctness (the new test passes once), not
   stability — step 2 covers that.
2. `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py -q -n0 -k test_latency_does_not_scale_with_transcript_size`
   in a loop ≥10 times, confirming no failures (replacing the brief's own
   ad hoc three-run check that first surfaced the flake) — this is the
   stability evidence, matching `pyproject.toml`'s own documented `-m timing
   -n0` convention for this marker.
3. Spot-check under deliberate load (a few concurrent `yes >/dev/null &`
   or similar) that the new test still passes, since the old one's failure
   mode was specifically load-sensitivity.
4. Red/green already confirmed during planning (ledger row 9): the same
   `RATIO`/`SLACK` formula, evaluated against a scratch copy of the hook
   with `tail -n 200` removed, correctly fails (regressed 150,000-line
   runtime 3134ms > allowed 2569ms) while the real hook correctly passes
   (424-523ms medians, well under the same allowance). Re-run this
   calibration check once the real test is written, to confirm the
   implemented assertion matches the formula validated here — the scratch
   hook itself is not committed.

## Out of scope

- Modifying the hook's own implementation, including consolidating its
  subprocess forks to cut typical latency (Mechanism 4, considered and
  rejected above) — this repo's own artifact, deliberately not touched
  because the brief and the evidence both point at test methodology, not
  hook behavior, as the defect.
- Changing pytest-xdist's `-n auto` default in `pyproject.toml` — also this
  repo's own artifact, deliberately not touched because it's a test-suite-wide
  decision affecting all ~10 `pytest.mark.timing` files, not scoped to this
  one hook; the marker's own documented `-m timing -n0` convention is the
  existing escape hatch for whoever needs serial determinism.
- `HANDOFF_NUDGE_ABS_CAP` / rearm-spacing retuning — settled per
  `docs/cost-levers-considered.md`; brief explicitly excludes.
- Making the nudge binding rather than informational — also settled/rejected
  elsewhere; brief explicitly excludes.
- The numeric-fingerprint redaction check (separate brief, no shared code).
- Broad test-suite performance work (~15 min full suite under xdist is a
  real observation, not this task).
- The other 8 files using `pytest.mark.timing` for unrelated
  timeout/deny-on-hang behavior — different property (bounded-by-timeout,
  not scales-with-N), not touched here. If any turn out to share this
  flakiness pattern, that's for the reviewer to flag, not this task to fix.
- `docs/cost-levers-considered.md`'s historical rejected-lever record —
  Axis 3 preserved content; its "<500ms hot path" characterization stays
  true regardless of the test-methodology change.
