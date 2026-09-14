# Round-3-triggered `plan-architect MODE=consult` gate, with a condition-shaped CLAUDE.md carve-out

*2026-09-02. Formerly `docs/design-decisions.md` §45.*

A PR entering a third `/code-review` round is a non-converging review loop,
not a quality gradient: mean `commit_count` across a 213-PR sample moves
2.33 → 2.54 → 2.86 through rounds 0–2, then jumps to 6.31 at round 3+
(`docs/case-studies/opus-frontload-review-rounds.md`'s "Follow-up
diagnostic" section). Firing after round 1 would catch roughly half of all
PRs to address a 14% tail, so the trigger point is entry to round 3
specifically, and — per this repo's own convention that memory and skill
instructions cannot fulfill an automatic-trigger request — it is
hook-enforced rather than left to `code-review/SKILL.md` prose.

The gate is a `PreToolUse` hook, `require-architect-consult.sh`, paired
with a `PostToolUse` recorder, `log-reviewer-round.sh`, both registered on
the `Agent|Task` matcher and self-filtering internally to a
reviewer-persona spawn. Splitting gate from recorder, rather than writing
state on the gate's own allow path, preserves a distinction the gate alone
cannot: the recorder writes the architect-consult latch only on a
`plan-architect` dispatch that actually returns, so a consult aborted
mid-flight leaves the gate armed rather than silently satisfied.

The round counter is a per-branch state file of distinct `(HEAD sha,
staged-diff sha256)` pairs, capped at two entries, keyed at
`<config-dir>/.reviewer-round-state.d/<repo-hash>.<branch-hash>`. The pair,
not either half alone, is the unit that survives a whole parallel reviewer
fan-out (five reviewers dispatched against one state collapse to one
entry) while still telling a committed fix (HEAD moves, staged diff
empties) apart from a staged one. Four lighter counters were rejected:

- Branch commit count — correlated with round count but not equal to it, so
  a 3-commit branch at round 1 would fire on a converging PR.
- The existing `review-narrative-ledger` — session-keyed, disable-able,
  swept at 30 days, and already known not to reliably keep pace with
  `/code-review` runs.
- Counting `agent-reviews/` findings files — opt-in via the
  `findings_path:` convention, so the count tracks reviewer output opt-in,
  not rounds.
- Appending round rows to `code-review-markers/` — that file's content is
  itself the commit gate's authorization, and this repo's CLAUDE.md forbids
  hand-writing marker state outright.

The release condition is a content-free, presence-only latch at
`<config-dir>/.architect-consult-latch.d/<repo-hash>.<branch-hash>`,
deliberately unlike every completion marker elsewhere in this repo, whose
content is a hash of the reviewed state so the gate re-arms the moment
that state changes. Re-arming on state change is exactly wrong here:
rounds 3+ exist because the diff keeps changing, so a content-addressed
latch would re-deny on the first post-consult fix. The gate fires once per
branch and does not re-arm at round 5, 7, or 9 — a re-arming latch would
be a second defensive layer closing a gap the first created, the
compounding-layers shape this repo's CLAUDE.md already names as a
wrong-foundation tell. A branch still active 30 days after its one consult
can fire again once, because both state directories sweep on the same
30-day interval as every other self-sweeping state directory in this repo.

The gate ships on by default for every stow consumer, cleared only by a
presence-only disable sentinel, `<config-dir>/.round-consult-gate-disabled`
— the `handoff-nudge` precedent (on-by-default with a disable escape)
rather than the opt-in precedent `track-permission-prompts` /
`nudge-error-mode-analysis` use. The gate interrupts a cost-and-quality
problem general to every consumer's review pipeline, not a personal
preference or a scan with a false-positive cost, so defaulting it on is
closer to `handoff-nudge`'s shape than either opt-in nudge's.

`claude/.claude/CLAUDE.md`'s Model & Effort Routing Opus bullet's flat
"never dispatch it this way on your own initiative" prohibition needed a
carve-out for this gate's own prescribed dispatch, or the gate's deny
message would prescribe an action CLAUDE.md itself forbids. The carve-out
added to that clause names a precondition — a hook deny, or the
dispatching session's own recognition that the branch is entering a third
`/code-review` round — not an event.
An event-shaped carve-out ("unless a hook deny prescribes it") would
reward the worse outcome: a session that self-triggers early and correctly
would be technically in violation, while one that blundered into the deny
would be compliant, and disabling the gate would silently revoke the
option along with the enforcement, since no deny would ever exist to
prescribe it. Conditioning on the precondition itself keeps the standing
option alive independent of whether the mechanical gate is on.

That standing option is not a second enforcement layer for the same
trigger, and composes with the gate and recorder at no code cost: the
recorder writes the latch on any `plan-architect` dispatch whose prompt's
first line is not `MODE=plan-sections`, whether or not a deny preceded it.
A session that recognizes round 3 on its own and dispatches the consult
proactively writes the latch through the same door the gate's own
prescribed dispatch would use, and the fan-out that follows is never
denied. The two signals — the dispatching session's own visibility into
real review rounds, and the hook's admitted proxy count — disagree
harmlessly in both directions: over-counting costs one extra Opus dispatch
on a converging PR, and under-counting or inaction still gets caught by
the hook itself, denying at its own precise, mechanical count.

The latch's actual meaning is "an architect consult ran on this branch
recently," not "the gate's prescribed consult ran" — an explicit-user-ask
consult at round 1 ([§37](plan-architect-consult-mode.md)'s original case) writes the same latch and spends
the branch's one firing, after which rounds 3, 4, 5 get nothing further
from either path. That is intended coverage under the latch's stated
meaning, not a hole to patch: patching it would require content in the
latch, which would reintroduce the re-denial-on-first-fix failure this
design exists to avoid.

[§37](plan-architect-consult-mode.md)'s text is untouched. Its claim was narrower than this entry's trigger:
that a subagent cannot verify why it was dispatched, so the
explicit-user-ask gate is a documented-convention constraint on the
dispatching session's judgment rather than a mechanically detectable one.
That claim survives intact — this gate mechanizes a different
precondition, round count, observable only through tool-call payloads the
hook can see, and leaves the user-ask question exactly where [§37](plan-architect-consult-mode.md) left it.

## Sources

- `docs/case-studies/opus-frontload-review-rounds.md` lines 158–269 ("Follow-up diagnostic") — the round-bucket commit-count table and the round-3 trigger-point recommendation.
- `docs/cost-levers-considered.md`'s 4th register row — prior grounding for the cost lever this gate closes.
- `docs/design-decisions.md` [§37](plan-architect-consult-mode.md) — the ad hoc `plan-architect MODE=consult` mode and the explicit-user-ask gate this entry leaves untouched.
- `.claude/plans/round3-review-consult-trigger.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.
- `claude/.claude/hooks/require-architect-consult.sh`, `claude/.claude/hooks/log-reviewer-round.sh` — the gate and recorder.
- `claude/.claude/hooks/_lib.sh` — `_lib_reviewer_round_state_key`/`_value`, `_lib_is_reviewer_persona`, `_lib_round_consult_gate_disabled`, `_lib_append_line_locked`.
