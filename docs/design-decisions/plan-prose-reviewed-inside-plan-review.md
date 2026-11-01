# A plan's prose is reviewed inside `/plan-review`, before approval

*2026-10-27.*

`/plan-review` spawns `comment-discipline-reviewer` on every round with the plan file as `plan_path:`. The reviewer checks Comment verbosity, Multi-fact comment structure, and Paragraph readability, and a plan file inside a `/code-review` diff is out of its scope. The rule for how its findings enter the verdict is in `claude-skills/skills/plan-review/SKILL.md` § "Output format".

## Why before approval

- A prose fix made after approval changes the plan's content hash and re-arms `require-plan-review.sh`, so it costs a full `/plan-review` round. The engineer therefore deferred those findings, and committed plans stayed hard to read.
- `/plan-it` was set aside as the home because its findings would need their own edit dispatch. `/plan-review` batches them with the round's other edits.
- The findings are non-blocking so that plan prose adds no round. A prose-only round still ends approved.
- Edits applied under **Approve with changes** go un-reviewed for prose fixes exactly as for every other such edit.

## Why `plan_path:` and not a synthesized diff

A whole-file diff would need a `Bash` step per round, and the agent would still need a signal to tell it from a `/code-review` diff, because the two get opposite plan rules.

## Why the fidelity check drops these spawns in code

`/ready-for-review`'s fidelity check counts a reviewer spawn made anywhere on the branch, so a `/plan-review` spawn of `comment-discipline-reviewer` would hide a skipped `/code-review` spawn of it. `review-trace --omit-plan-review-spawns`, passed by `skill-fidelity-report.sh`, leaves those spawns out of the timeline the check reads. A rule in the fidelity reviewer's prompt would leave a false-halt rate that only sampled runs can bound, on a gate where `[SILENT-SKIP]` halts with no disclosure path.

A `plan-review` round label ends at that run's own `marker.sh deactivate plan-review` call, which `claude-skills/skills/plan-review/SKILL.md` § "Record review completion + deactivate" describes. Without that end, a session that keeps dispatching reviewers after `/plan-review` finishes, with no human message, leaves the round open, and the fidelity view drops those spawns. The end lives in `review_trace.py`, not `review_rounds.detect_round_windows`, because the fidelity view is the only consumer that drops a spawn by its round.

Round labels cover main-thread spawns only. Every `/plan-review` spawn is one, and labeling a subagent's spawns would add a drop surface no consumer needs.

## Measurement note

The cells ran through a `general-purpose` dispatch at `model: sonnet` carrying the edited agent body, because the stowed agent resolves to the main checkout's file until the change merges and is pulled. Each reviewer cell used K=5, with each must-flag site flagged in at least 4 of 5 runs and each must-pass site cited in at most 1 of 5.

| Cell | What it checks | K | Bar | Outcome |
|---|---|---|---|---|
| P | A plan handed as `plan_path:`: three must-flag sites, ten must-pass sites in the excluded angles and quoted text | 5 | 4 of 5 flagged, 1 of 5 cited; at most 1 of 5 runs with a finding naming another angle | Pass: each must-flag site 5 of 5, each must-pass site 0 of 5, no other angle |
| D | A diff holding two plan files and a `docs/` positive control | 5 | `docs/` site flagged in 4 of 5; each plan-file site cited in at most 1 of 5 | Pass: `docs/` site 5 of 5, plan-file sites 0 of 5 |
| RR | A re-review round: a declined finding's site | 5 | Returned as a new finding in at most 1 of 5 | Pass: 1 of 5 |
| V | The `/plan-review` session's verdict with one finding labeled `BLOCKER` | 5 | All 5 end **Approve with changes** with a Plan prose section | Missed: all 5 have the section, and 4 of 5 end **Approve with changes**. The fifth ends **Request changes** on the Self-referential findings tripwire |
| V control | Cell V with Cell P's sentence naming a prior round's finding replaced by a neutral sentence | 5 | Same as V | Pass: 5 of 5 |
| R | Two committed plans, read finding by finding | 1 each | At most one out-of-scope finding per run | Pass: one run had 14 findings with 1 out of scope; the other run had 7 findings with 1 out of scope |

Cell P first cited the must-pass sentence naming a prior round's finding in 3 of 5 runs. The Scope plan rule's list of content a plan properly carries then gained "references to its own review rounds and findings". P, D, and R were re-run on the agent text as committed, and the outcomes above are from those runs.

Cell V's miss comes from the fixture, not the Plan prose paragraph. The tripwire fired on that same sentence, and the missed run still kept the `BLOCKER` finding out of its verdict. The Plan prose paragraph was therefore left unrevised, and later V runs use the control's plan.

No test re-runs these cells. An edit to the agent's Scope plan rule or to the Plan prose paragraph re-runs them by hand.

## Sources

- `.claude/plans/plan-prose-pass.md`.
- `claude-skills/skills/plan-review/ROUTING.md` § "Reviewer roles".
- `claude/.claude/agents/comment-discipline-reviewer.md` § "Scope".
- `claude-skills/skills/plan-review/SKILL.md` § "Record review completion + deactivate".
- `docs/transcript-analysis.md` § "review-trace".
