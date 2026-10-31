# One consult process re-arms every three reviewed states

*2026-10-29.*

One `plan-architect MODE=consult` process serves every code review, `/ready-for-review` passes included.
`require-architect-consult.sh` triggers it, and `code-review/SKILL.md` § "Round-cap architect consult" routes its return.
The gate fires at entry to a branch's third reviewed state, then re-arms: each consult restarts the branch's count, and the gate demands the next consult after three more reviewed states.
`/ready-for-review`'s own Cap consult is retired. The Cap consulted before fixing a dirty pass once two consecutive disposition records were dirty ([the fix-loop convergence decision](ready-for-review-fix-loop-convergence.md)).
Every consult receives a per-round finding series, the plan's `Ask:` line and the branch's SETTLED and DEFER rows, with an instruction to judge the foundation rather than the latest findings.

## Why a consult can run again

[The round-3 gate decision](round3-plan-architect-consult-gate.md) fired once per branch on two grounds.
A latch keyed on the reviewed-state hash would deny again on the first post-consult fix, and a re-arming latch would be a compounding defensive layer.
Neither rules out a presence-only latch paired with a counter that restarts on a consult.
The latch stays content-free, so the first fix after a consult is state 1 of 3, not a re-denial.
No hook, state directory or file format is added: the gate's existing counter and latch drive every re-arm.

## The interval and the reset

- **Three reviewed states between consults.** With the default cap the gate demands a consult before states 3, 6, 9 and so on, and before 2, 5, 8 under the round-2 pilot. A consult from any source restarts the count.
- **Why three.**
  - The cadence is three rounds, and a consult's cost is weighed against the rounds it prevents.
  - No trend rule can be shown to beat a fixed interval, because no branch in the ledger is labelled converging or grinding. A backtest could therefore not show a trend rule firing more often on grinding branches.
  - Without a discriminating trigger, the trigger only sets the cadence and the judgment belongs to the consult. This is why every consult receives the series, in place of a hook computing a verdict from it.
  - Three caps the run left unchecked after a wrong "foundation is fine" verdict at two states, and matches the first firing, so one spacing describes the whole gate.
- **What would change the interval.** No instrument evaluates either condition today, so three is a standing default:
  - Lengthen it if second and later consults on a branch mostly return "foundation is fine" while the branch's rounds keep climbing. `transcript-analysis.py review-trace --this-repo` reports per-branch consult counts but not verdicts, so today the verdicts come only from reading transcripts.
  - Switch to a trend rule computed from `show --trend` if a labelled set of converging and grinding branches shows that rule firing on the grinding ones materially more often than the fixed interval does.
- **The counter is the gate's own state file, reset by deleting it.** On a consult, `log-reviewer-round.sh` removes the branch's state file with `rm -f --`, then touches the latch. It tolerates a failed delete and takes no lock. Only the two hooks read that file, so deleting it loses nothing.
- **Two windows the order of those steps leaves.**
  - A kill between the delete and a branch's first latch write leaves no latch, so the next consult comes one state early, two under the pilot. Later consults find the latch already present.
  - During a branch's first consult, a gate read that fails between the delete and the latch write denies, which costs at most one extra consult. Writing the latch first would turn that deny into an allow, but a kill between the steps would then leave the old window behind a fresh latch, so the next consult could come up to three states early after any consult. Delete-then-latch keeps the smaller cost.
- **No lock.** A lock changes no outcome. A reviewer append racing the delete either survives as the new window's first state or is lost, which moves the next denial by one state. On a config dir with no state directory, the lock would also spend every retry and print a warning before going ahead unlocked.
- **A read error defers to the latch.** grep's no-match status denies. Only grep's read-error status runs the latch-presence exit, which allows when the latch is a regular file. A state file that a later consult's reset deletes mid-read therefore allows. Counting a failed read as zero states, or re-testing the file after a failed count read, would allow an unreadable or vanished file on a branch with no latch.
- **Rejected counters.**
  - A count stored in the latch needs a file format, value validation, a rule for legacy zero-byte latches and a clamp for a swept state file, all to keep a pre-consult history that nothing reads.
  - A modulus over an uncapped state file never restarts on a consult from another source, so a contradiction-route consult could be followed by a gate consult one state later.
  - The ledger's `max_round` lags by a round and can skip one, because the session appends a round's rows only after dispositioning it. A `PreToolUse` gate would also parse the whole ledger on every reviewer spawn.
- **The recorder hashes the branch's state on every reviewer spawn for the branch's whole life outside a live `/plan-review`.** That is the cost of counting latched branches and `/ready-for-review` runs.
- **The latch directory's 30-day sweep** can drop a long-lived branch's latch. The first-firing cap then applies once more, one state earlier than the interval would by default and two under the round-2 pilot (default cap 2 against interval 3, pilot cap 1 against 3).
- **A regular state file the consult cannot delete** keeps the file at three lines, so the gate denies every new reviewed state, and a plain `rm` from the engineer's terminal fails for the same reason. A non-regular object at that path leaves the gate inert instead, since the gate allows it and the recorder cannot append to it. `docs/hooks.md` § "Gate deadlock recovery" names the repair, with the `round_consult_gate` kill switch as the human's fallback.
- **A revert is state-compatible.** No file format changes, and the reverted hooks ignore a state file of up to three lines once a latch exists.

## `/ready-for-review` reviews count

Both hooks exit only while a `/plan-review` marker is live.
That exit's cited rationale counts `/plan-review` branches only, while the case study's round unit counts every Skill-invoked `/code-review`, the ones `/ready-for-review` invokes included (`docs/case-studies/opus-frontload-review-rounds.md`).
Counting them realigns the hook with the unit its round-3 trigger was measured in.
With the Cap retired, a gate silent inside a run would also leave a `/ready-for-review` loop unchecked until the run ends.

A branch with one `/code-review` round and one dirty pass meets its first consult at the fix's staged review.

How often a run is interrupted is arithmetic on the design under one assumption, that a run starts at a uniformly random point in the three-state window.
It is not a corpus figure.
- A dirty pass adds up to two states, the pass and its fix's staged review, so a dirty streak meets a consult about every one and a half passes.
- A run whose first pass is clean adds one new state, since step 4 shares it, so about one such run in three meets a consult.
- A run with one dirty pass adds three states: the pass, its fix's staged review and the re-pass. Every such run meets at least one consult.

Each consult costs a denied reviewer spawn, an Opus dispatch and verdict routing.
Step 4's `skill-fidelity-reviewer` is a reviewer persona, so its spawn counts too.
It usually shares a state step 3 recorded and adds nothing, but when step 3 was a cache hit and no recorded state matches, a full window denies it.
Exempting that persona needs a second predicate in both hooks, with tests, because both share `_lib_is_reviewer_persona` and a test pins its set.
The `/ready-for-review` Overview routes the denial instead: dispatch the consult, then retry the spawn.

## The Cap retires

The Cap lived in prose only because both hooks returned early inside a run.
Once they count those reviews, the gate does the Cap's job, and a second trigger would only duplicate it.
Coordinating two consults by rule is compounding defensive engineering, so the Cap goes rather than learning to reuse the gate's verdict.
Its named residual closes too: dirty passes alternating with clean ones escaped it through its clean-pass reset, and the gate counts reviewed states whatever their outcome.

- **One verdict set.** The Cap's *proceed* maps onto the noise verdict or the one-defect verdict, and its *stop* maps onto re-plan. Its `no verdict` and disagreement stops map onto the round-cap section's blocking stop-and-ask for a return that fits none of the three, or one the orchestrator disagrees with.
- **Inside a run, a re-plan verdict is an ADDRESS finding of the pass it interrupts.** A re-plan verdict ends the round before any reviewer runs, so the run records the verdict as an ADDRESS row whose fix route is the re-plan. Only DEFER and SETTLED rows count as resolved, so by the session's own classification (see Accepted gaps) a pass holding that row is dirty, writes no marker, and the fix loop takes the row like any other finding. Where the row goes depends on where the gate denied:
  - at step 3's cumulative pass, in that pass's disposition record;
  - at a fix commit's staged review, in the record of the step-3 pass whose fix is under review, which is already dirty;
  - at step 4, as a step-4 finding.
- **The fix is a plan revision, not a `code-writer` dispatch.** The session revises the plan by the round-cap section's route, runs `/plan-review`, which the revised plan file requires before any code edit, implements the revision and commits it through the staged-diff `/code-review`. A fix staged when the verdict arrives is committed with the revision's implementation, under that commit's staged review, or discarded. The loop then returns to step 2. A revision that changes no code ends the run with a report to the engineer. The round-cap section's routing is unchanged and names no human stop, but `plan-it`'s revision can still ask the engineer an open decision.
- **A pass is clean only when every reviewer its Spawn decisions line lists as spawned has returned.** The ADDRESS row, not this conjunct, is what makes a re-plan pass dirty. The conjunct covers a reviewer that never returns, such as one a failed consult, a stop-and-ask or a persisting denial ended before it finished, so such a pass is never clean whatever ended it.
- **A denied step-4 spawn leaves step 4 owed.** The spawn reviewed nothing, so step 4 runs again after the fix loop. The Overview's "Step 4 does not re-run on its own output" does not apply, because step 4 produced no output.
- **A staged review the verdict ended writes no `code-review` marker**, since `/code-review` records completion only for a review it ran. Step 3's later full pass over the fixed bytes backstops it either way.
- **Accepted gaps.**
  - The dirty classification is the session's own reading of prose, with no independent check. This is accepted under the gate's `cooperative` tier. A later check would use the hook-observed record of a reviewer spawn at the reviewed state, which `log-reviewer-round.sh` already keeps.
  - The no-code exit ends the run without the final step, so the run's active marker keeps the push arm released until the session ends, as every other early exit does. Deactivating the marker at this one exit was set aside because every other exit leaves the same gap.
- **The disposition record stays, minus its counting role.** Every re-review spawn prompt carries it, spawns check each earlier ADDRESS row's Outcome against its text, and the ledger has no pass-kind, reviewer or Outcome column. Its clean-pass definition stays because the `cumulative-review` marker write keys on it.
- **No clean-pass reset.** The engineer's cadence counts rounds, not dirty rounds. The reset is what let the Cap's residual escape. The hook cannot see a pass's outcome without reading the ledger, which the counter above rejects as gate input. A consult on a converging branch costs one dispatch, and the series lets it return the noise verdict cheaply.
- **No separate human ladder.** Each consult's own input and routing serve in place of a mechanical one.
  - Each consult sees the series, so a loop still grinding after an earlier verdict shows in its input.
  - A consult that finds the foundation fine but the loop not converging, with no single defect to name, fits none of the three verdicts. `plan-architect` says so in its first line, and the round-cap section makes that a blocking stop-and-ask.
  - The noise verdict is evidence for the gold-plating DEFER criterion, and a DEFERred finding counts as resolved.
  - A mechanical ladder has no home. The gate sees a presence-only latch and a state count, but a second-trip rule needs the previous verdict and whether a clean pass followed it. The hook cannot observe either, and the routing prose would need a durable verdict record to carry them across a handoff. That record is the cross-consult bookkeeping the engineer rejected.

### What a run loses

- A foundation-wrong verdict inside a run is an ADDRESS finding whose fix revises the plan inside the run's fix loop, in place of stopping for the human.
- A dirty streak reaches the human only when a consult's judgment sends it there, not after one consult by rule. No rule sends a long streak to the human by its length.
- The Cap consulted before a dirty pass's fix, so its *stop* prevented the fix. The gate consults at a later state's first spawn, after up to two more states of work on the same foundation.
- The Cap was prose and ignored the kill switch. With `round_consult_gate` off, the gate no longer demands a consult in a run either, and with the fix-plan pilot on, a run's only foundation check is that pilot's per-round consult.
- The Cap ran wherever the skill ran. Where the gate allows on state failure, which is an unresolvable config dir or repo root or a detached HEAD, a run gets no consult and no signal. This is accepted under the gate's allow-on-state-failure posture, which treats the consult as a cost-and-quality interruption, not a security control.
- The Cap's `cap` row in the disposition record was the one per-branch artifact showing that a consult ran and what it ruled. What remains is the `architect-consult: <verdict>` tag on the report's *Spawn decisions:* line, and the transcript.

## What a consult receives

- **A finding series from `review-ledger.sh show --trend`.** The flag is accepted only with `--out` and adds a second stdout line, `trend: <series>`, after `changed: <path>`.
  - The series is comma-separated `round <N>: <count>` entries in round order, one for each round that has a row. A round with no findings reads `clean`.
  - `<count>` is the findings raised that round: its ADDRESS, DEFER and SETTLED rows, carries included. A carry is a finding raised again at a site already decided, so counting it is what shows a branch stuck on one settled finding. A row a later `--ref` retired still counts.
  - A clean round usually ends one diff's review, so the round after it usually reviews a different diff. This lets a consult tell a new diff's first findings from a relapse. `/code-review`'s already-reviewed skip logs a clean round for the same diff, which is the exception.
  - Only rounds 1 to 9999 count, the range `append` accepts, normalized to plain digits. With no counted row, no `trend:` line prints.
  - **The series has no byte cap.** Each entry comes from at least one row, so the output grows with the number of rounds that have rows, never with the size of a round number, at about 13 bytes a round. The series is the last element of the history line, so any truncation of that line costs only the series' newest entries.
- **Delivery.** `announce-branch-review-history.sh` passes `--trend` to the `show` call it already makes and appends the second stdout line to the `Review-ledger history:` line after a one-phrase legend. It does not check the value, because `show`'s own jq program builds it from integers and fixed words, so a check could only reject output that program cannot produce. Every `plan-architect` dispatch receives it, whichever skill or hook prescribed the dispatch.
- **The `Ask:` line and the SETTLED and DEFER rows.** Every prescribed consult site already passes the plan path, and the history file holds every SETTLED and DEFER row. The agent body's **Foundation questions.** paragraph tells the consult to find the `Ask:` line and those rows with Grep and to judge the foundation from the series, the `Ask:` line and those rows, not from the latest round's findings.
- **Rejected.**
  - A per-disposition split per round needs a field legend with caveats, and the live decisions already reach the consult through the history file.
  - A series entry for every round between the first and the last makes the output grow with the largest round number, which then needs a window, an elision marker and a byte budget.
  - A separate `trend` subcommand adds a sixth capped call to a hook whose timeout drops all of its output, and takes a second ledger snapshot that an append can split from the history file.
  - Inline jq in the hook puts the ledger's row semantics outside the ledger's own library.
  - Prose at each dispatch site restates the same inputs at every site and reaches no ad hoc dispatch.
  - The hook naming the plan path restates `plan-it`'s slug rule in bash.

## Accepted residuals

- **A narrow consult restarts the count without asking the foundation question.** Narrow means the contradiction route, the heavier-mechanism route or the fix-plan pilot. Such a consult still receives the series and the history, and with the pilot on the gate stays silent, because that consult already returns a `foundation:` line every round.
- **The gate fires before a pass's reviewers run**, so a consult can land on a pass that would have come out clean. That costs one consult, not a stop.
- **One-time upgrade effects** on branches latched before the re-arm and on a `/ready-for-review` run under way at upgrade are described in `CHANGELOG.md`.
- **The `round_consult_gate` schema row's label** still reads "Round-3 architect-consult gate". Retitling it for a label alone would bring `config-keys.psv` under hook review, so the label stays. The switch also silences the consult inside `/ready-for-review` runs.

## Out of scope

- Retiring the disposition record or folding it into the ledger. [The branch-scoped ledger decision](branch-scoped-review-ledger.md) owns that follow-up.
- Recording consult verdicts durably, which no ladder rule needs.
- Counting the `architect-consult: <verdict>` tag in transcripts, so the interval's first revisit condition can be evaluated, is a possible follow-up.
- A hook-observed precondition on writing the `cumulative-review` marker: requiring `log-reviewer-round.sh`'s record of a reviewer spawn at the reviewed state would check the session's dirty classification independently. The classification stays session-applied prose.
- Hook-driven ledger appends. The session numbers each `/code-review` round itself and appends its rows after dispositioning it, so a round's rows can be missing or late.
- Keying round state by branch alone. The round-state key and the ledger's branch key both hash the worktree root, so one branch checked out at two paths counts as two branches. Re-keying would orphan every existing state file, latch and branch-scoped ledger.
