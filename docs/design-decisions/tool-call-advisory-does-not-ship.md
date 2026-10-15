# Tool-call batching/delegation advisory does not ship on the turn-shape detector design

*2026-10-15.*

`.claude/plans/tool-call-compliance-enforcement.md` planned a `PostToolBatch` advisory (its Phase 2) with two detectors: a cross-batch counter of consecutive size-1 batches attributed to the batching or delegation rule by `tool_name`, and a within-batch counter of repeated `Bash` calls. Its M8 row pre-registered two floors before any calibration ran: inter-rater Cohen's κ ≥ 0.61 for a label set to count at all, and per-rule precision ≥ 0.70 for that rule's detector to ship. M8 also fixed the contingency: re-rate once with a sharpened rubric if κ fails, and return Phase 2 to planning if it fails again.

The plan's precision-calibration step (its Verification item 10) ran one round over a sample of flagged streaks, and κ landed well below the floor. The planned second round was not run, because the round-1 labels already bound precision below the floor for both rules under any kappa. One rater marked every flagged batching streak legitimate. The other rater's violation count, taken as the best case for the detector, still leaves precision for each rule short of 0.70 even if the first rater agreed with every one of them. The sharpened rubric's four rules each move a label toward legitimate or ambiguous, never toward violation, so a second round could only lower precision further. Phase 2 therefore does not ship on this design, by the plan's own gate. The labeled sample and the raw streaks are local-only session content and are not reproduced here, so the figures stay unpublished.

The failure is structural, not a rubric problem. Two harness-forced shapes produce long runs of size-1 batches that no agent can batch: a deferred-tool `ToolSearch` followed by the call that consumes its schema, and `TaskOutput` polling of a background task. The cross-batch detector reads only batch size and `tool_name`, so it flags those runs by construction. That is the same false-positive shape the plan's A13 row named for the within-batch detector, where `pytest`, `ruff`, and `shellcheck` issued together are rule-compliant and still trip it. A threshold cannot separate forced serial calls from avoidable ones. A redesign would have to recognize harness-forced shapes in the detector itself, which is a design question the plan's assumption ledger did not cover.

The rubric sharpened between rounds is the one durable output of the calibration, kept here so a redesign does not rediscover it:

1. A `ToolSearch`, `Agent`, or `TaskCreate` call plus the very next call consuming its result never counts as a violation.
2. Judge against the rule's actual test, real independence for batching and a same-question `Bash` chase for delegation, not how busy a streak looks.
3. For a mixed streak, judge its dominant character, and call it a violation only when independent or avoidable calls make up roughly half or more of the streak.
4. Mark a streak ambiguous whenever the verdict depends on unseen `tool_result` content, such as whether a repeated call was a retry after a failure.

What stands: Phase 0's dedup fix (#688), Phase 1's `turn-shape` and `turn-shape-samples` subcommands (#693), and the unflagged-holdout sampler (#699) are merged and remain the retrospective measurement for both rules. `nudge-long-turn-subagent.sh` ([subagent-turn-count-nudge-threshold-and.md](subagent-turn-count-nudge-threshold-and.md)) now occupies `PostToolBatch` for a different signal. Its payload parsing confirms the top-level `session_id` and `agent_type` fields the plan's A11 row left unverified.

## Sources

- `.claude/plans/tool-call-compliance-enforcement.md` — the M8 floors and contingency, the A11 and A13 rows, and the precision-calibration step's rater note (Verification item 10).
- `docs/transcript-analysis.md` — the shipped `turn-shape` family of subcommands.
- [subagent-turn-count-nudge-threshold-and.md](subagent-turn-count-nudge-threshold-and.md) — the `PostToolBatch` hook that did ship, and the payload fields it reads.
