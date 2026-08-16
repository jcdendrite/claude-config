# Delegating the instrument, not just its execution: measured, and why the rule wasn't added

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** A parent session resuming an approved plan judged that it needed a ~210-line Python utility to reconstruct a hook's threshold logic across session transcripts. It announced the split explicitly: write the script itself, then delegate the broad multi-account execution and per-session inspection to a subagent, on the stated ground that a subagent would "re-derive" the threshold/window logic instead of faithfully porting it. It then ran the script itself and followed with four further inline heredoc probes and several `git log` bursts toward the same question — a second failure this case study returns to below.

Two reasoning gaps produced the split: "answer question X" and "build the tooling X needs" were treated as separable objectives, and no rule in `subagent-delegation` addresses who *writes* a new instrument as opposed to who reads or edits existing code; and a fidelity argument ("a subagent would re-derive it independently") went unrebutted, though naming the source path in a dispatch makes a subagent read the original rather than reinvent it.

**Question.** Does this repo's own corpus support adding a `subagent-delegation` rule that routes instrument-authoring to a subagent along with the objective it serves?

**Short answer.** No. The measurement's own go/no-go rule, fixed before the run, compares authored-payload mass in sessions that never dispatched a subagent against sessions that dispatched at least once. The rule was written to catch concentration in the zero-dispatch cohort — the signature of the failure mode above. The corpus shows the opposite: sessions that already dispatch account for 93.4% of inline-authoring mass, while sessions that never dispatch anything — the population where the observed failure would have to live — carry only 6.6%, despite being 30.4% of all sessions. Inline authoring is not tracking the split-objective failure the rule would exist to catch, so no rule ships.

## How this was measured

The `instrument-authoring` subcommand of `claude/.claude/scripts/transcript-analysis.py` does one pass over the transcript corpus. It classifies each main-thread and subagent `tool_use` block as inline instrument-authoring when it is a `Bash` call carrying a heredoc or an inline-interpreter invocation (`python3 -c`, `node -e`, `sh -c`, and similar — `-c`/`-e` matched only when bound to a recognized interpreter argv[0], never bare, since the flag is overloaded by `curl -c`/`tar -cf`/`ssh -c`), or a `Write` call targeting a scratchpad/temp path. Each session's own main-thread `Agent`/`Task` spawn count splits sessions into two cohorts — `zero_dispatch` and `dispatched` — and the subcommand reports authored-payload character mass per cohort.

```
transcript-analysis.py instrument-authoring
```

Snapshot taken 2026-08-16, default corpus scope (6 declared config-dir roots, no `--since`). Reproducible via the command above; the classification behavior itself is fixture-pinned by `TestInstrumentAuthoring` and `TestScanInstrumentAuthoringSession`.

Honest limits:

- **Shape, not intent.** The classifier sees a call's shape and payload size, never whether delegating it would have been correct. A large authored payload in a session that also dispatched heavily may be entirely appropriate — the census bounds the population, it does not adjudicate individual sessions.
- **Aggregate-only, by design.** The subcommand's output never includes raw command text, file content, file paths, or session identifiers — only size buckets, counts, and cohort totals — so no individual session behind these figures is inspectable from the report, and none was inspected to write this case study.
- **One engineer, one machine**, across every declared config-dir account. See "What this cannot tell you" in `targeted-read-discipline.md` for the same caveat's fuller treatment; it applies here unchanged.

## The numbers

**622 sessions scanned**, all 6 default-scope roots combined.

| cohort | sessions | share of sessions | main-thread authored chars | share of authored mass |
|---|---|---|---|---|
| `zero_dispatch` | 189 | 30.4% | ~312,000 | 6.6% |
| `dispatched` | 433 | 69.6% | ~4,426,000 | 93.4% |

Zero-dispatch sessions are *under*-represented in authored mass relative to their share of sessions (6.6% of mass from 30.4% of sessions), not over-represented — the opposite of what the failure hypothesis predicts.

**Call census** (main-thread + subagent, both shapes): 2,644 main-thread and 2,854 subagent `Bash` heredoc/inline-program calls; 652 main-thread and 661 subagent `Write`-to-scratchpad calls. Zero calls landed in the `unparsed_input` cohort (no `__unparsedToolInput`-only Bash/Write blocks in this corpus).

**Size distribution** (main-thread scope): concentrated in the middle buckets — 1,308 calls of 1,369,366 chars in the 500–1999 range and 533 calls of 2,197,513 chars in the 2000–9999 range are the two largest mass contributors; the 10000+ bucket holds only 47 calls but 833,502 chars, confirming a real large-instrument tail exists even though it isn't the tail driving the cohort split.

## What this cannot tell you

**The second trace failure is out of scope for this measurement, not fixed by it.** The observed session's inline authoring was followed by four further inline heredoc probes and several `git log` bursts toward the same already-answered question — behavior `subagent-delegation` Step 1's existing "second or third `Bash` command toward the same question" trigger already governs, and which also failed to bind in that session. This subcommand counts authoring calls, not repeat-probing toward a single question across turns; it has no way to measure that failure mode, and no other change in this branch addresses it either.

**A rule reinforcing itself does not fire twice in the same session for free.** Two delegation rules were already on the books when the observed trace occurred; the trace demonstrates that prose alone does not reliably bind at the decision point. This measurement says nothing about whether that binding problem is worse or better for instrument-authoring specifically than for any other delegation rule in the skill — it was never designed to.

**Corpus scope, not generalization.** Same caveat as every other transcript-corpus case study on this page: one engineer's accounts, one machine, six declared roots. A consumer whose session mix differs materially — heavier one-off scripting, lighter subagent use generally — is not described by the ratio above.

## Decision

**No `subagent-delegation` rule ships.** Recorded as a rejected lever in [`cost-levers-considered.md`](../cost-levers-considered.md) with the two cohort figures as the measured reason.

**The warn-only hook (reusing this subcommand's classifier) also does not ship.** The plan that produced this measurement designed a non-blocking `PreToolUse` hook as reinforcement for the routing rule above — nudging toward dispatching the instrument to a subagent at the moment a large authoring payload is about to be typed. Two technical unknowns about whether the hook could even work were independently confirmed against Anthropic's primary hooks reference before this measurement ran: a permitting `PreToolUse` hook can surface advisory text to the model via `hookSpecificOutput.additionalContext` (not stdout, which doesn't reach the model for `PreToolUse`), and a timed-out hook fails open — the tool call proceeds unblocked, consistent with the hook's intended non-blocking design. A third question, the hook's per-fire wall-time cost, was never measured, because it's moot: the hook's entire purpose was reinforcing the routing decision above, and that decision doesn't ship. With no rule advising a session to dispatch its instrument-authoring, there is nothing left for a nudge to reinforce, and firing shape-matched advisories at behavior the corpus does not show correlates with the failure mode this plan set out to fix would cost real context for no corresponding signal. The plan's own written design never named this as a build-gating condition — it falls out of the plan's own logic once the routing rule is rejected: the hook exists to reinforce that rule, and the rule didn't ship.

**The census subcommand and its classifier ship as-is.** `instrument-authoring` is a real, reproducible measurement tool independent of this decision, following the same aggregate-only disclosure discipline as its `edit-format`/`read-scope` siblings. A future re-run — after a corpus shift, or specifically scoped to sessions matching the observed trace's shape — can revisit this verdict without new instrumentation.

**Numeric revisit trigger.** Reconsider if either becomes true, re-derivable via `transcript-analysis.py instrument-authoring`:

- Zero-dispatch sessions' share of authored-payload mass exceeds their share of sessions (i.e., the cohort becomes over-, not under-, represented in authoring mass) — today's 6.6% mass against 30.4% of sessions is the baseline.
- The 10000+ size bucket's share of main-thread authored mass grows materially past today's ~17.6% (833,502 of 4,738,451 total main-thread chars) — a shift toward larger authored instruments even without a cohort-concentration change would be worth a second look at the threshold-based hook design, independent of the rule question.

## Sources

- `claude/.claude/scripts/transcript-analysis.py` — `instrument-authoring`, the subcommand producing every figure above.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — `TestInstrumentAuthoring`, `TestScanInstrumentAuthoringSession`.
- `.claude/plans/delegate-instrument-authoring.md` — the plan, including the assumption ledger, the go/no-go rule fixed before the run, and the warn-only hook's design.
- [`cost-levers-considered.md`](../cost-levers-considered.md) — "From `delegate-instrument-authoring.md`" — the rejected-lever record.
