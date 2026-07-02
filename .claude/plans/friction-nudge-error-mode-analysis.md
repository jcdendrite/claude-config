# Plan: Friction-triggered nudge to run `/error-mode-analysis`

## Context

**Goal:** when the current session accumulates enough mechanical friction signals,
surface a one-time, non-blocking suggestion to run `/error-mode-analysis` — modeled
on the handoff nudge, which reliably fires off a single session-intrinsic number
(context tokens used) rather than trying to detect an external state.

`error-mode-analysis` (PR #419, issue #414) is name-only invoked, so nothing
surfaces it, and #414's premise is that the methodology gets forgotten between
engagements. Earlier designs tried to detect "the engagement/ticket is done" — an
external, async, judgment-laden state. Every such design collapsed under its own
machinery (tracker adapters, per-ticket markers, checkpoint durability, a
model-mediated done-check that reintroduced the noise it was meant to prevent);
plan-review flagged this as a wrong-foundation tell.

The reframe (engineer's direction): key off a **session-intrinsic** signal, the way
`nudge-handoff-near-context-cap.sh` keys off context %. This deletes the entire
external-state problem class — no tracker, no per-repo/per-ticket state, no
durability question.

**Scope honesty (this is a deliberate trade, not full coverage of #414).** The
trigger fires on *high-friction sessions*. It does **not** cover a clean, low-friction
multi-session / multi-PR delivery — which is exactly the "many sessions and PRs" body
of work the skill is designed for, and arguably where forgetting is *most* likely
(no friction to jog memory). That cohort remains dependent on name-only invocation.
We accept this narrowing in exchange for a reliable, testable, near-zero-noise
trigger; covering the smooth-delivery case would require re-opening the
completion-detection problem this reframe deliberately abandoned. Surfaced for the
approver as an explicit non-goal.

**Intended outcome:** a reliable, near-zero-noise, fire-once-per-session nudge that
appears only in genuinely high-friction sessions, in any repo, with no external
lookups.

## Approach

A new `UserPromptSubmit` hook `nudge-error-mode-analysis.sh`, a near-clone of
`nudge-handoff-near-context-cap.sh`, plus a **minimal** predicate-sharing change to
`transcript-analysis.py` so the *definition* of each friction signal has one home.

### Friction signals (three, session-intrinsic, mechanical)

`transcript-analysis.py` already defines all three predicates as module-level
constants; v1 composites them. The count is **flat per single transcript file** — no
branch filter, no date filter, skipping `isSidechain` records (matching the
subcommands' sidechain skip):

1. **Hook denials** — a record is a denial if it matches *either* shape
   `cmd_review_trace` detects: the legacy `attachment` / `hook_blocking_error` shape,
   or the current `is_error` `tool_result` whose text matches `_HOOK_DENIAL_SIGNATURE`.
   Deduped by `tool_use_id` within the file (same dedup `cmd_review_trace` does via
   `seen_denial_ids`).
2. **Failed test runs** — a `tool_result` whose paired `tool_use` Bash command matches
   `TEST_RUNNER_RE` and whose result text matches `FAILED_RE` with count > 0.
3. **User-correction phrases** — a user turn whose lowercased text contains any
   `STRUGGLE_PHRASES` entry (the engineer's "count how the user corrected it" — a
   fixed mechanical list, no semantic judgment).

PR review comments are deliberately excluded from the trigger — they live on GitHub
(`pr-link` needs `gh`/network), which would break the "check one cheap local thing"
property. They remain a data source for the skill itself (its Step 3), not the trigger.

### Single source of truth — share the *predicate*, do not "extract the count"

Plan-review established that the three subcommands emit grouped/filtered structures,
not scalar counts (`cmd_fail_seq` mandatorily branch-filtered and per-model;
`cmd_review_trace` denial detection woven into a timeline pass with shared dedup;
`cmd_struggle` per-(branch, family) and drops empty-`gitBranch` turns). So "extract
the count and have subcommands call it" would force behavior drift. Instead:

- **Denials:** extract only a small pure predicate `hook_denial_key(record) -> str |
  None` (returns the `tool_use_id` dedup key if the record is a denial in either
  shape, else `None`). `cmd_review_trace` is refactored to call it *for its denial
  detection only* — same detection, same caller-side `seen_denial_ids` dedup, so its
  rendered output is byte-identical. `friction-count` calls the same predicate with
  its own per-file seen-set.
- **Failed tests + struggle:** reuse the *existing module-level constants*
  (`TEST_RUNNER_RE`, `FAILED_RE`, `STRUGGLE_PHRASES`) directly. `cmd_fail_seq` /
  `cmd_struggle` are **not modified** — they keep their grouping/filtering.
  `friction-count` does its own flat pairing/matching against those shared constants.
- Net: the only subcommand touched is `cmd_review_trace` (minimal predicate lift);
  the friction *definitions* (regexes / phrase list / denial-shape predicate) have one
  home; the flat aggregation loop lives in `friction-count`. This is the lighter
  primitive — sharing definitions, not refactoring three woven functions.

### `friction-count` subcommand

`friction-count --transcript <path> [--checkpoint <path>]` reads a JSONL file (no
`iter_sessions`, no `gh`), prints the composite integer to stdout, and supports
`--json` for the per-signal breakdown. **Pinned semantics** (so the threshold test is
deterministic): each signal counts *distinct events*; `composite = denials +
failed_test_runs + struggle_turns` with **all-1 weights** (stated, not implied); the
`--json` breakdown is the tested contract and `composite == sum(signals)` is an
asserted invariant.

**Incremental checkpoint (added after `claude-hook-review` flagged the naive
full-reparse-every-prompt cost — see "Why incremental" below).** When `--checkpoint
<path>` is given, `friction-count` seeks to the byte offset stored in the checkpoint
(absent/malformed checkpoint → offset 0, i.e. full scan), parses only complete JSONL
lines appended since that offset, adds the per-signal deltas to the checkpoint's
persisted running totals, writes the new offset + running totals back to the
checkpoint, and prints the **cumulative** composite (not just the delta). No
cross-call dedup state is needed beyond the offset: a given transcript line is a
self-contained JSONL record, is read at most once across the lifetime of a checkpoint
(each call starts exactly where the previous one stopped), and is therefore counted
exactly once by construction — the same guarantee the current single-file whole-scan
path already relies on within one call. Checkpoint file is a small JSON blob (offset +
three integers); corrupt/unreadable checkpoint fails open to a full rescan from
offset 0, never to an error.

**Known limitation (documented, not fixed):** if a single hook-denial event is
represented by both the legacy `attachment` shape and the current `is_error`
`tool_result` shape for the same `tool_use_id` (as `cmd_review_trace`'s
`seen_denial_ids` dedup already anticipates), and those two records are somehow split
across two different checkpoint calls, the denial could be double-counted. In
practice both records are emitted within the same assistant turn, before the next
user prompt (and therefore the next hook fire) is even possible, so this is a
theoretical edge case, not an observed one — noted rather than engineered around, to
avoid re-adding the cross-call dedup-set complexity the checkpoint design exists to
avoid.

### Hook shape (mirror the handoff nudge)

Read `session_id`, `agent_type`, `permission_mode`, `transcript_path` from the
`UserPromptSubmit` payload in one `jq` pass (as the handoff nudge does). **Gate order
— cheapest first, so the interpreter is never spawned unnecessarily:**

1. kill-switch file present → exit 0
2. `agent_type` non-empty (inside a subagent) → exit 0
3. `permission_mode == plan` → exit 0
4. no `session_id` / no transcript file → exit 0
5. **per-session fired-marker present → exit 0** (one-shot suppresses the *spawn*, not
   just the output — this is the whole-session hot-path fix)
6. **`python3` preflight** (bash-side fail-open — see below); on any failure → exit 0
7. `timeout <N> python3 <script> friction-count --transcript "$TRANSCRIPT_PATH"
   --checkpoint "$CHECKPOINT_FILE"`; validate stdout is an integer (`2>/dev/null` guard
   on the arithmetic compare, as the handoff nudge guards its compare); non-integer /
   non-zero exit / timeout → exit 0
8. if integer ≥ `FRICTION_THRESHOLD`: emit
   `jq -n '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:…}}'`,
   `touch` the fired-marker, append one line to the log. Always `exit 0`.

`CHECKPOINT_FILE` is `$HOME/.claude/.error-mode-nudge-checkpoint.d/<session_id>` —
sibling to the fired-marker dir, same 30-day eviction. It persists for the session's
lifetime regardless of whether the nudge ever fires (unlike the fired-marker, which is
written only on fire); this is what turns per-fire cost from
O(current transcript size) into O(lines appended since the last prompt).

**Why incremental.** `claude-hook-review`'s operational-footprint escalation
(mandatory Section 10 spawn to `staff-platform-engineer`) measured the naive
full-reparse design at ~180ms on the largest real transcript found in testing, and
found the cost is paid on **every prompt for the session's entire remaining
lifetime** in any session that never crosses `FRICTION_THRESHOLD` — the backtest
found that's the majority case (47.4% of the 654 sampled sessions never fired).
Because the fired-marker only engages after threshold is crossed, and transcript size
grows every turn, cumulative added latency across a long non-firing session is
roughly quadratic in turn count, not a fixed tax — a confirmed violation of this
project's own hook-review performance budget (<100ms/fire, no unbounded file I/O).
The incremental checkpoint is the platform reviewer's top-ranked fix: it preserves the
"session-intrinsic, no external lookups" design intent exactly (still purely local,
still no new dependency) while turning the per-fire cost into O(new lines only).
Rejected alternative: a time/size throttle (skip re-computation unless N seconds
elapsed) — cheaper to build but only reduces *fire frequency*, not per-fire
complexity, so it masks the cost rather than removing it; not worth the added state
for a problem the checkpoint design solves directly at comparable implementation
cost.

**Bash-side Python guard (not script-side).** `transcript-analysis.py` does
`from datetime import UTC` at module top, so on `python3 < 3.11` it raises
`ImportError` before any subcommand runs — a script-side guard is impossible. The hook
must check `command -v python3`, then delegate the version compare to Python itself
rather than parsing a version string in bash: `python3 -c 'import sys;
sys.exit(0 if sys.version_info >= (3, 11) else 1)'` and branch on exit code — this
avoids a hand-rolled `python3 --version` string parse. Treat absence / old version /
non-zero exit / non-integer stdout all as no-nudge. Fail-open everywhere; no `set -e`
(mirror the handoff nudge's `set -uo pipefail`). The `timeout <N>` wrapper around the
`friction-count` call (gate step 7) must have `N` grounded at implementation time —
measure `friction-count`'s wall time against the largest realistic transcript in
`~/.claude/projects` (not guessed), and record that measurement in the PR description.

### Global, with a kill-switch (like handoff), not opt-in

Mirrors the accepted handoff-nudge posture. The switch away from opt-in is
*correctly forced*: with no committed marker there is no opt-in surface, and the
committed-marker fingerprint risk from earlier designs disappears. **Residual noise
class (stated honestly):** a high-friction *throwaway / spike* session (in any repo,
incl. claude-config) can fire a nudge with no useful retrospective target. This is
*bounded by the kill-switch and the once-per-session marker*, not *eliminated* — there
is no repo-type gate. If throwaway-repo noise proves common in practice, per-session +
kill-switch is the only mitigation without reintroducing a committed opt-in marker.

### Exact paths (mirror the handoff nudge's naming)

- fired-marker dir: `$HOME/.claude/.error-mode-nudge-fired.d/<session_id>`
  (evicted `find … -mtime +30 -delete`, as the handoff nudge does).
- checkpoint dir: `$HOME/.claude/.error-mode-nudge-checkpoint.d/<session_id>`
  (same eviction; persists whether or not the nudge ever fires).
- kill-switch: `$HOME/.claude/.error-mode-nudge-disabled`.
- log: `$HOME/.claude/.error-mode-nudge.log` (append-only; growth is slow — one line
  per *fired* session — so unbounded-append is acceptable, matching
  `.handoff-nudge.log`; noted, not rotated).
- The handoff nudge's schema-drift marker branch is intentionally dropped —
  `friction-count` has no all-zero schema-drift analogue.

## Critical files

**Modify — `claude/.claude/scripts/transcript-analysis.py`** (stow-shipped; needs
`/code-review`):
- Extract `hook_denial_key(record) -> str | None`; refactor `cmd_review_trace`'s
  denial detection to call it (its rendered output must stay byte-identical).
- Add `friction-count --transcript <path> [--checkpoint <path>]` (+ `--json`) per the
  pinned semantics above, including the incremental-checkpoint behavior. Do **not**
  modify `cmd_fail_seq` / `cmd_struggle`; `friction-count` reuses their module-level
  constants directly.

**Create — `claude/.claude/hooks/nudge-error-mode-analysis.sh`**: the UserPromptSubmit
hook. Reuse the structure of `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`
(payload jq-parse, marker dedup dir + 30-day eviction, subagent/plan-mode skips,
kill-switch, log, fail-open, additionalContext emission), adding the bash-side
`python3` preflight + `timeout` + integer-validation + `--checkpoint` pass-through
described above.

**Modify — `claude/.claude/settings.json`**: add `nudge-error-mode-analysis.sh` to the
existing `UserPromptSubmit` array (currently just the handoff nudge).

**Create — tests** (reuse the existing `test_transcript_analysis.py` fixture builders
`_write_jsonl`, `_tool_result`, `_user_msg`, `fake_projects` — do not hand-synthesize
JSONL):
- **Regression gate:** the existing `cmd_review_trace` / `cmd_fail_seq` / `cmd_struggle`
  e2e tests must stay green **unedited** (characterization guarantee for the
  predicate lift). State this explicitly.
- **`friction-count` unit tests:** both denial shapes (legacy `attachment` +
  current `is_error`); dedup-by-`tool_use_id` in the single-file path; a mixed-branch
  fixture asserting the count-all-branches semantics are intentional; failed-test
  pairing; struggle-phrase match; empty transcript; malformed line → graceful;
  `--json` breakdown and `composite == sum` invariant; exact boundary (one below
  `FRICTION_THRESHOLD` stays quiet, `FRICTION_THRESHOLD` itself fires).
- **Cross-path equality tests (both signals that fork logic, not just constants):**
  - Denials: `friction-count`'s denial count over one file equals `cmd_review_trace`'s
    denial count over that same session — pins the shared `hook_denial_key` predicate.
  - Failed-test runs: `cmd_fail_seq` and `friction-count` both re-implement the
    `tool_use_id` pairing state machine (sharing only `TEST_RUNNER_RE`/`FAILED_RE`, not
    the pairing algorithm), so this fork needs its own equality test — assert
    `friction-count`'s failed-test count equals `cmd_fail_seq`'s **failing-run
    subtotal** (`f > 0`, not total matched runs — `cmd_fail_seq` records every matched
    run including passing ones) over the same fixture. Without this, "single source of
    truth" holds only for denials and struggle (a trivial phrase-in-text check, low
    drift risk), not for the failed-test signal, which carries the most duplicated
    logic of the three.
- **`--checkpoint` incremental unit tests:** first call with no checkpoint file scans
  from offset 0 and creates one; a second call after appending new lines to the
  transcript re-scans only the appended bytes and returns the *cumulative* composite
  (assert this against a full-rescan baseline over the same final file, to catch
  incremental/full-scan drift); a corrupt/malformed checkpoint file falls back to a
  full rescan rather than erroring; checkpoint offset advances monotonically and never
  re-reads already-consumed bytes (assert via a fixture where re-reading would double
  a signal if the offset weren't respected).
- **Hook subprocess tests** (`claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`,
  mirror `test_nudge_handoff_near_context_cap.py`): fires above threshold; quiet below;
  quiet when fired-marker present **and asserts `python3` was not spawned** (PATH-shim
  recording invocation, as the handoff test shims); skip in subagent; skip in plan
  mode; kill-switch honored; `python3` missing → quiet + exit 0; `python3` < 3.11 →
  quiet; non-integer stdout → quiet; timeout → quiet; always `exit 0`; the
  `--checkpoint` flag is passed with the session-scoped checkpoint path on every
  invocation.

**Not modified:** `claude/.claude/skills/error-mode-analysis/SKILL.md` — the nudge is
external to the skill, so no skill edit and no `/skill-review`.

## Decisions made (flag at approval)

- **`FRICTION_THRESHOLD` set from a pre-ship backtest, not a guess.** All-1 weights.
  Because this fires globally for every stow user, the threshold must be validated
  *before* shipping, not calibrated live on the install base. The tooling already
  exists (`iter_sessions` over `~/.claude/projects`): a backtest step runs
  `friction-count` across a sample of real historical transcripts, reports the
  per-signal distribution, and sets the constant at a high percentile of *clean*
  sessions (e.g. 99th) so ordinary TDD churn (several red test runs + a couple of
  corrections) does not trip it. This is a Verification gate below, not post-ship
  "observation."
- **Composite of all three signals** (vs. a narrower subset) — covers "errors" and
  "user corrections." The refactor cost is now minimal (only `cmd_review_trace`
  touched) because the other two reuse existing constants.
- **Global + kill-switch** (vs. opt-in) — mirrors the handoff nudge; residual
  non-engagement-repo noise class noted above.

## Verification

- **Threshold backtest (gate, pre-ship):** run `friction-count` (or a small loop over
  `iter_sessions`) across a sample of historical transcripts; record the per-signal
  distribution; set `FRICTION_THRESHOLD` from the clean-session percentile. Capture the
  numbers in the PR description.
- `.venv/bin/pytest` for the new hook test + the transcript-analysis contract /
  cross-path / `friction-count` tests, and confirm the pre-existing subcommand tests
  pass unedited; `.venv/bin/ruff check claude/.claude/`.
- `claude-hook-review:claude-hook-review` on the new hook.
- `/code-review` on the `transcript-analysis.py` change (shared, stow-shipped utility).
- Manual smoke: fixture transcript with several denial / failed-test / struggle
  records fed as `transcript_path`; confirm the nudge fires once, is silent on a
  second prompt (marker + no `python3` re-spawn), silent below threshold, silent in a
  subagent and in plan mode, silent when `python3` is missing/old.

## Steps to ship

1. File a GH issue capturing the design history (why session-intrinsic friction beat
   the tracker/checkpoint foundations, and the explicit clean-delivery non-goal) —
   `gh issue list` first; none tracks this.
2. Implement `transcript-analysis.py` changes (predicate lift + `friction-count`) and
   all tests, including both cross-path equality tests.
3. **Run the threshold backtest before writing `FRICTION_THRESHOLD` into the hook.**
   Execute `friction-count --json` (or equivalent) across a sample of historical
   transcripts under `~/.claude/projects`, record the per-signal distribution, and set
   the constant from the clean-session percentile (e.g. 99th). This step is mandatory
   and gates step 4 — `FRICTION_THRESHOLD` may not be hardcoded to a guess. Paste the
   distribution and chosen percentile into the PR description.
4. Implement `nudge-error-mode-analysis.sh` using the backtested `FRICTION_THRESHOLD`,
   register it in `settings.json`, and add the hook subprocess tests.
5. `/code-review` (+ `claude-hook-review`) → `/ready-for-review`. Reviewer/approver
   checklist: PR description contains the backtest distribution + chosen percentile —
   do not approve a PR that sets the threshold without it. Merge stays the engineer's
   call.

## Out of scope

- The clean multi-session / multi-PR low-friction delivery cohort (explicit non-goal
  above) — remains name-only invocation; a completion-detection trigger for it was
  deliberately abandoned as a wrong foundation.
- Cross-session / per-repo cumulative friction (v1 is per-session one-shot).
- PR-comment signals in the trigger (network; stays a skill data source only).
- Any change to `block-gh-pr-merge.sh` or a merge-time hook (verified non-viable:
  agent merges are blocked pre-exec; human `!`/web/terminal merges bypass the hook
  pipeline).
- Editing the `error-mode-analysis` skill body.
