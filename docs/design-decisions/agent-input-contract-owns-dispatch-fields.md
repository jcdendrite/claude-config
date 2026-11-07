# A dispatched agent's `## Input contract` owns its dispatch prompt's fields

*2026-11-06.*

A dispatched agent's `## Input contract` lists the fields its dispatch prompt may carry, each marked derived, free-form or fixed. The agent's own file decides what it reviews, and it names any prompt text it sets aside. The rule for authors is `claude-skills/skills/agent-review/SKILL.md` § "8. Review checklist", item 19. `comment-discipline-reviewer` is the first agent to adopt it, and `/code-review` and `/plan-review` are its two dispatch sites.

## Why

- **A closed list in the skills alone failed.** `/code-review` already told the dispatcher to hand this reviewer the whole diff, and a hand-written prompt still listed a plan file as a prose site. The reviewer followed the prompt and raised a finding its own Scope section excludes.
- **A hook cannot judge the failure.** A `PreToolUse` hook can match field tokens in a prompt. It cannot judge whether free-form text contradicts the agent's scope, which is the actual failure.
- **A site that only cites the contract is not enough.** The dispatcher would have to `Read` the agent file on every dispatch, and a test could check nothing at the site but the citation. A site therefore names the tokens it sends and cites the contract, and a test holds the tokens to the contract's field list.
- **Free-form text cannot be tested.** No static test sees a runtime prompt. The shared sentences in the contract cover that layer, and the tests cover the specifications that name the fields.

## Measurement note

The cells ran through `general-purpose` dispatches at `model: sonnet`, because the installed agent resolves to the main checkout's file until the change merges and is pulled. The control arm carried the merge-base body, `cmp` reported it identical to the installed copy, and the treatment arm carried the edited body. The fixture was a two-hunk diff: a plan file under `.claude/plans/` gaining a three-paragraph rationale block, and a `docs/` file gaining "this used to be a cron job" framing. The contradicting prompt was a `diff:` line plus "Prose sites to review: .claude/plans/example-plan.md and docs/example.md". A set-aside line is a line before the first finding that names text the prompt gave as not followed.

| Arm | K | Bar | Outcome |
|---|---|---|---|
| Baseline, installed agent | 1 | None | The plan file was not flagged |
| Control, merge-base body | 5 | Set-aside line in at most 1 of 5 | Pass: set-aside line 0 of 5, plan-file finding 0 of 5, `docs/` finding 5 of 5 |
| Treatment, edited body | 5 | Set-aside line in at least 4 of 5; plan-file finding in at most 1 of 5; `docs/` finding in at least 4 of 5 | Pass: set-aside line 5 of 5, plan-file finding 0 of 5, `docs/` finding 5 of 5 |
| Benign prompts, edited body | 5 | No set-aside line and the `docs/` finding in at least 4 of 5 | Pass: set-aside line 0 of 5, `docs/` finding 5 of 5 |

- **The control did not reproduce the bug.** The old body did not flag the plan file in 0 of 5 control runs or in the baseline run, so the plan-file criterion did not tell the bodies apart. This result shows the new sentence is obeyed. It does not show the original bug fixed.
- **Benign runs wrote a null set-aside line.** In 4 of 5 benign runs the reviewer opened with "Set aside: nothing", and the fifth opened without one. Those lines name no prompt text, so they do not count as set-aside lines. The cells do not isolate the cause. The "name the text you set aside" sentence is the likely one. The effect costs one line per run and no finding.
- **One prompt shape.** The cells cover the one contradicting prompt above. They give no rate for other contradicting prompts.

No test re-runs these cells. An edit to the shared sentences in `comment-discipline-reviewer`'s Input contract re-runs them by hand.

## Sources

- `.claude/plans/dispatch-prompt-contract.md`.
- `claude/.claude/agents/comment-discipline-reviewer.md` § "Input contract".
- `claude-skills/skills/agent-review/SKILL.md` § "8. Review checklist", item 19.
