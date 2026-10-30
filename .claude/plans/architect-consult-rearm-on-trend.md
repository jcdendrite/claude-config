# Re-arm the architect consult on a finding-count trend, and give it the context to judge the foundation

## Context

Goal: have the `plan-architect` consult fire again whenever a branch's review findings stop shrinking, and hand it the material to ask whether the design is the wrong shape.

Ask: "I've been asking for the architect consult to check the design foundation every 3 rounds not just once per branch"; widened to "Can't just do the re-arm every 3 rounds but need to give the consult more context." Trigger first chosen via label "Windowed trend (Recommended)", which replaced the fixed 3-round interval. A backtest on today's ledger then showed that rule no longer separates converging from grinding branches; asked to pick a fixed re-arm interval instead, the engineer answered "Ask the architect", so the re-arm trigger is this plan's design decision. Consult input chosen via labels "Finding trend", "The plan's Ask line", "Settled and deferred rows", "Foundation framing". Coverage chosen via label "Count them (Recommended)". Widened again to "a single consult process that handles both of what the current RFR and currenet code review consults do but across all code reviews instead", after the engineer called a reuse rule between the two consults "compounding defensive engineering". Ticket: #997, under master plan #1211.

Problem. `require-architect-consult.sh` gates the first consult at entry to round 3, then goes permanently silent for the branch. Its header lists this as a known gap ("Once-per-branch, non-rearming"). `docs/design-decisions/round3-plan-architect-consult-gate.md` justifies the single firing on two grounds: a latch keyed on the reviewed-state hash would deny again on the first post-consult fix, and a re-arming latch would be a compounding defensive layer. Neither ground rules out a latch that re-arms on a round-count or finding trend. A long branch therefore goes unchecked for a wrong foundation after round 3, and the engineer is left as the only re-arm mechanism.

Second problem. #997 reports that a non-converging branch had 8 voluntary consults that each judged the current diff in isolation and none recommended stopping; the first consult handed the round-count and finding-volume trend did. The consult today receives no trend, no original ask, and no record of decisions already settled.

Why now. #997's prerequisite, a branch-scoped review ledger (#1182), has merged, so per-branch finding counts per round now exist in `review-ledger.sh`. #997's windowed-trend thresholds were backtested before #1182's SETTLED disposition changed the finding series, so they need re-checking against the current ledger.

Rounds run while a `/ready-for-review` marker is live are skipped by the gate and its recorder today; #997's field evidence names that as a gap, and the engineer chose to count them.

Backtest against the current branch-scoped ledger: #997's 5-round windowed rule now fires on most short branches that converged, fires on a long branch whose late rounds were nearly clean, and fires at roughly regular intervals on long branches. Per-round counts are spiky and include SETTLED and carry rows, and no branch in the set is labelled as non-converging, so the backtest measures firing frequency, not correctness.

Intended outcome: one consult process serves every code review, replacing both the gate's consult and `/ready-for-review`'s Cap consult. It fires at round 3 as today, re-arms on a trigger this plan decides, counts `/ready-for-review` cumulative passes, and every consult it forces receives the finding trend, the plan's `Ask:` line, the branch's SETTLED and DEFER rows, and an explicit instruction to judge the foundation rather than the latest findings.

## Approach

Make one consult process serve every code review, `/ready-for-review` passes included. `require-architect-consult.sh` triggers it, and `code-review/SKILL.md` § "Round-cap architect consult" routes its return. `/ready-for-review`'s Cap and its own consult retire. The gate re-arms instead of firing once: every `plan-architect` consult resets the branch's reviewed-state count, and the gate demands the next consult after three more reviewed states. Every `plan-architect` dispatch also receives a per-round finding series from a new `--trend` flag on `review-ledger.sh show`, plus an agent-body rule to judge the foundation from that series, the plan's `Ask:` line and the branch's SETTLED and DEFER rows.

**Re-arm trigger: three reviewed states since the last consult (rows 1, 3, 4, 7).** With the default cap the gate demands a consult before rounds 3, 6, 9 and so on. A consult from any other source restarts the count.

- No trend rule can be shown to beat a fixed interval on this ledger. No branch in it is labelled converging or grinding (G4), so no backtest here can show a trend rule firing on grinding branches more often than on converging ones. That holds even without the relayed figures. Those figures add that #997's windowed rule fires on most long branches at about the same round, which is how a fixed interval behaves (row 7).
- With no discriminating trigger, the trigger only sets the cadence, and the judgment belongs to the consult. #997's field case is a consult that recommended stopping once it was handed the trend (Context). So this plan hands every consult the trend instead of computing a verdict from it in a hook.
- Three, not five or eight. The engineer named three rounds (row 1) and weighed a consult's cost against the rounds it prevents (row 3). Without discrimination data, nothing buys a longer window. Three caps the unchecked run after a wrong "foundation is fine" verdict at two rounds, against four at an interval of five and seven at eight. It also matches the gate's first firing, so one spacing describes the whole gate. In the default configuration a branch of n reviewed states meets at most n/3 gate-forced consults, rounded down.
- What would change this decision. No instrument evaluates either condition today, so three is a standing default until one exists:
  - Lengthen the interval to five if second and later consults on a branch mostly return "foundation is fine" while the branch's rounds keep climbing. `transcript-analysis.py review-trace --this-repo` reports per-branch consult counts (row 23) but not verdicts, so today the verdicts come only from reading transcripts. Counting the `architect-consult: <verdict>` tag on each report's *Spawn decisions:* line is a cheap possible follow-up (**Out of scope**).
  - Switch to a trend rule computed from the `show --trend` series if a labelled set of converging and grinding branches shows that rule firing on the grinding ones materially more often than the fixed interval does. No such set exists (G4).

**Counter: the gate's own state file, reset by deleting it (rows 10, 11, 12, 20, 30, 43, 48, 49, 58).**

- The latch stays content-free and presence-only. The first fix after a consult is therefore state 1 of 3, not a re-denial, which answers the first ground `docs/design-decisions/round3-plan-architect-consult-gate.md` gives for a single firing (row 11).
- No hook, state directory or file format is added. The one counter and latch now fire periodically, which answers that file's second ground, that a re-arm would be a compounding defensive layer.
- On a consult, the recorder removes the branch's state file with `rm -f --`, which is silent when the file is absent, then touches the latch. It tolerates a failed delete and takes no lock.
- A lock changes no outcome, so the plan sets aside deleting under the state file's append lock (row 20). A reviewer append racing the delete either survives as the new window's first state or is lost, which moves the next denial by one state. On a config dir with no state directory, the lock would also spend every retry and print a warning before going ahead unlocked (row 49).
- The order of the two steps changes one outcome: a gate read that fails during the reset.
  - On a branch that already holds a latch, that read allows under either order.
  - During a branch's first consult, a read failing between the delete and the latch write denies. The merge-base gate denies the same way for any state file that vanishes mid-read on a branch with no latch. The deny costs at most one extra consult.
  - Writing the latch first would turn that deny into an allow. A kill between the steps would then leave the old window behind a fresh latch, so after any consult, not only a branch's first, the next one could come up to three states early. The plan keeps delete-then-latch.
- A kill between the delete and a branch's first latch write leaves no latch, so the next consult comes one state early, two under the round-2 pilot. Later consults find the latch already present.
- The gate keeps the merge-base's count test and grep, so no merge-base denial goes away (row 43).
  - A failed count read falls through to the grep, as today (row 48). grep's no-match status denies.
  - grep exits 2 when it cannot read the file (row 58). Only then does the latch-presence exit run: it allows when the latch is a regular file and denies otherwise, as at the merge-base.
  - A file that a later consult's reset deletes mid-read therefore allows, since that branch holds a latch. A file that exists but cannot be read keeps its merge-base outcome.
  - Two other read rules were set aside. Counting a failed read as zero states allows an unreadable file on a branch with no latch. Re-testing `-f` after a failed count read allows a file that vanishes mid-read on such a branch, and still needs the latch test for an unreadable one. The merge-base denies both.
- Only the two hooks read the state file, so deleting it loses nothing (row 30).
- Lighter or simpler primitives rejected:
  - *A count stored in the latch*, the way the handoff nudge stores its last-fired value (row 19). It needs a file format, value validation, a rule for legacy zero-byte latches and a clamp for a swept state file, all to keep a pre-consult history that nothing reads.
  - *A modulus over an uncapped state file.* It never restarts on a consult from another source, so a contradiction-route consult could be followed by a gate consult one state later.
  - *The ledger's `max_round`.* The session appends a round's rows only after dispositioning it, so `max_round` lags by a round and can skip one, under an append contract this plan keeps (**Out of scope**). Counting from the last consult would need that consult's round, either stored in the latch or replaced by a modulus, and both are rejected above. A `PreToolUse` gate would also have to parse the whole ledger on every reviewer spawn.

**`/ready-for-review` reviews count and can trip the gate (rows 6, 8, 9, 31).** Both hooks drop their `.ready-for-review-active.d` exit and keep the `.plan-review-active.d` one.

- The exit's cited rationale counts `/plan-review` branches only (row 9). The case study's round unit already counts every Skill-invoked `/code-review`, the ones `/ready-for-review` invokes included (row 8). Counting those reviews therefore realigns the hook with the unit its round-3 trigger was measured in.
- The gate fires inside a run, not only counts. With the Cap retired, a gate that stayed silent while the marker is live would leave a `/ready-for-review` loop unchecked until the run ends.
- This changes when the first consult arrives, and the decision file and changelog say so. A branch with one `/code-review` round and one dirty RFR pass now meets its first consult at the fix's staged review, where today it meets none.
- A dirty pass adds up to two states, the pass and its fix's staged review. A dirty streak inside a run therefore meets a consult about every one and a half passes, where the Cap consulted once per streak.
- How often a run is interrupted. Each consult costs a denied reviewer spawn, an Opus dispatch and verdict routing.
  - A run whose first pass is clean adds one new state, since step 4 shares it. A run with one dirty pass adds three: the pass, its fix's staged review and the re-pass.
  - Assume a run starts at a uniformly random point in the window. Then about one run in three whose first pass is clean meets a consult, and every run with a dirty pass meets at least one. The Cap consulted only from the second dirty pass in a row.
  - This is arithmetic on the design under a stated assumption, not a corpus figure, and the decision file states it that way.

**One consult process: `/ready-for-review`'s Cap retires (rows 17, 34, 35, 42).** The Cap lives in prose only because both hooks return early inside a run (row 17). Once they count those reviews, the gate does the Cap's job, and a second trigger would only duplicate it. The engineer rejected coordinating two consults by rule (row 35), so the Cap goes rather than learning to reuse the gate's verdict. The Cap's named residual closes too. Dirty passes alternating with clean ones escaped it through its clean-pass reset (row 42), and the gate counts reviewed states whatever their outcome.

- *One trigger:* the gate's count above.
- *One evidence set:* what the round-cap site already passes, which is the plan path, the ledger digest and the previous round's findings files (row 14), plus the series, history and framing under **Consult inputs** below. The disposition records stop being consult input.
- *One verdict set:* the round-cap section's three verdicts.
  - The Cap's *proceed* maps onto the noise verdict or the one-defect verdict.
  - Its *stop* maps onto re-plan.
  - Its `no verdict` and disagreement stops map onto the section's own blocking stop-and-ask for a return that fits none of the three, or one the orchestrator disagrees with (row 39).
- *Inside a run, a re-plan verdict is an ADDRESS finding of the pass it interrupts* (rows 32, 38, 40, 53, 59, 61, 65).
  - A re-plan verdict ends the round before any reviewer runs, and a pass with no findings records one `none` row, which counts as clean (row 40). Read literally, the run would write the `cumulative-review` marker over a diff no reviewer saw and go on to step 6's push.
  - So the run records the verdict as an ADDRESS row, with the re-plan as its fix route (row 65). An ADDRESS row is unresolved, so a step-3 pass holding it is dirty under the existing clean/dirty definition, and the fix loop takes it like any other finding. Where the row goes depends on where the gate denied:
    - at step 3's cumulative pass, in that pass's disposition record;
    - at a fix commit's staged review, in the record of the step-3 pass whose fix is under review, which is already dirty. The staged fix is written but not committed (row 59), and is either committed with the revision's implementation, under that commit's staged review, or discarded;
    - at step 4, as a step-4 finding, which step 4's own halt sends through the fix loop.
  - One level up, the same rule covers every round a gate denial ends: a pass is clean only when every reviewer its *Spawn decisions:* line lists as spawned has returned (row 70). Step 3's clean-pass sentence carries that conjunct, so a pass ended by a re-plan, a failed consult, a stop-and-ask or a denial that persists after the consult is never clean, and neither is a partly reviewed pass. A pass that lists no reviewer is clean when its rows resolve, as at HEAD. The `none` sentence keeps HEAD's wording, because the clean-pass sentence alone decides.
  - The re-plan row's fix is not step 3's `code-writer` dispatch. The session revises the plan by the round-cap section's route, runs `/plan-review`, which the revised plan file requires before any code edit (row 53), implements the revision and commits it through the staged-diff `/code-review`. The loop then returns to step 2. A revision that changes no code ends the run with a report to the engineer.
  - The route splits on whether the branch has a plan file: `plan-it` Step 5's revision when it does, `/plan-it` when it does not. The paragraph defers to that split rather than naming Step 5, which would narrow the routing it leaves alone.
  - The dirty classification is the session's own reading of prose, with no independent check, accepted under the gate's `cooperative` tier. A staged review the verdict ended writes no `code-review` marker, since `/code-review` records completion only for a review it ran, and step 3's later full pass backstops it either way. A hook-observed precondition, such as requiring `log-reviewer-round.sh`'s record of a reviewer spawn at the reviewed state before `marker.sh write cumulative-review`, would be the independent check. That is a possible follow-up (**Out of scope**).
  - The run's active marker keeps the push arm released for the whole fix loop (row 61), as for any dirty pass. The no-code exit ends the run without step 9, so the push arm stays released until the session ends, the same gap every other halt leaves. Deactivating it at this one exit was set aside for that reason.
  - This replaces the halt-and-restart tail of row 60. It defines how a run carries the verdict, not the verdict's routing, which stays the round-cap section's (row 47). The routing prose names no human stop, but `plan-it`'s revision can still ask the engineer an open decision (row 32).
- *A step-4 denial resolves the same way* (row 52). The gate can deny step 4's `skill-fidelity-reviewer` spawn (**Accepted residuals**). The run-exit paragraph M4 adds to `ready-for-review/SKILL.md` routes it: dispatch the consult, then retry the spawn. A named defect, or a re-plan verdict, is a step-4 finding, and the fix loop takes it as above. A denied spawn reviewed nothing, so step 4 runs again after that fix loop. The Overview's "Step 4 does not re-run on its own output" does not apply, because step 4 produced no output.
  - Exempting that persona from counting was set aside. Both hooks share `_lib_is_reviewer_persona`, and a test pins its set to the review-only roster minus Explore and Plan (row 52). An exemption therefore needs a second predicate in both hooks, with tests.
  - The denial needs a full window and a state no earlier spawn recorded, and the consult it demands is the one the branch's next reviewer spawn would demand anyway. One clause in a paragraph this plan already adds costs less.
- *The disposition record stays, minus its counting role.* Every re-review spawn prompt carries it, and spawns check each earlier ADDRESS row's Outcome against the text (row 36). The ledger has no pass-kind, reviewer or Outcome column to replace it (row 37). Its clean-pass definition stays because the `cumulative-review` marker write keys on it (row 38). Only the clauses that served the Cap change:
  - "canonical only for the Cap's pass accounting" names instead what the record stays canonical for, the pass kind, reviewer and Outcome the ledger lacks;
  - the clean/dirty sentence drops "but the Cap counts records, so a record's clean or dirty status can relax the Cap";
  - the loop-back sentence drops "within the cap below".
- *No clean-pass reset.*
  - The engineer's cadence counts rounds, not dirty rounds (row 1).
  - The reset is what let the Cap's residual escape (row 42).
  - The hook cannot see a pass's outcome without reading the ledger, which **Counter** rejects as gate input.
  - A consult on a converging branch costs one dispatch, and the series lets it return the noise verdict cheaply.
- *No separate human ladder.* Today a second Cap trip in one dirty streak goes to the human with no second consult. The ladder exists so that a human sees a loop one consult did not end. Each consult's own input and routing now serve that purpose:
  - Each consult sees the series, so a loop still grinding after an earlier verdict shows in its input (row 2).
  - A consult may find the foundation fine but the loop not converging, with no single defect to name. That fits none of the three verdicts. `plan-architect` then says so in its first line, and the round-cap section makes that a blocking stop-and-ask (rows 39, 46).
  - The noise verdict drives convergence where *proceed* did not. It is evidence for the gold-plating DEFER criterion, and a DEFERred finding counts as resolved (row 41).
- Rejected homes for a mechanical ladder:
  - *The gate.* The hook sees a presence-only latch and a state count. A second-trip rule needs the previous verdict and whether a clean pass followed it, which is the cap row rebuilt as hook state. The `cooperative` tier is not the obstacle (row 43), since a deny can say "ask the human" like any other deny text. The obstacle is that the hook cannot observe either fact.
  - *The routing prose.* The engineer chose one routing for every firing over restoring the dirty-streak stop (row 47). The rule would also need the previous verdict after a handoff, and so a durable verdict record. That record is the cross-consult bookkeeping row 35 rejects.
- *What a run loses* (rows 32, 47, 54, 55). The decision file names each, and the changelog names the first two and the kill switch:
  - A foundation-wrong verdict inside a run is now an ADDRESS finding whose fix revises the plan inside the run's fix loop, instead of stopping for the human.
  - A dirty streak reaches the human only when a consult's judgment sends it there, not after one consult by rule. No rule sends a long streak to the human by its length.
  - The Cap consulted before a dirty pass's fix, so its *stop* prevented the fix. The gate consults at a later state's first spawn, after up to two more states of work on the same foundation.
  - The Cap was prose and ignored the kill switch. With `round_consult_gate` off, a run now gets no consult either. With the fix-plan pilot on, a run's only foundation check is that pilot's per-round consult.
  - The Cap ran wherever the skill ran. Where the gate allows on state failure (an unresolvable config dir or repo root, or detached HEAD), a run gets no consult and no signal. This is accepted under the gate's allow-on-state-failure posture, which treats the consult as a cost-and-quality interruption, not a security control.
  - The Cap's `cap` row in the disposition record was the one per-branch artifact showing that a consult ran and what it ruled. What remains is the `architect-consult: <verdict>` tag on the report's *Spawn decisions:* line, and the transcript.

**Consult inputs (rows 2, 5, 21, 22, 50, 51).**

- *Finding trend.* `review-ledger.sh show` gains a `--trend` flag, accepted only with `--out` (row 5).
  - After a successful write, `show` prints a second stdout line below `changed: <path>`: `trend: <series>` (row 50).
  - The series reads as plain text, so it needs no field legend (row 63). It is comma-separated `round <N>: <count>` entries in round order, one for each round that has a row, for example `round 1: clean, round 2: 9, round 7: 11`.
  - `<count>` is the number of findings raised that round: its ADDRESS, DEFER and SETTLED rows, carries included. A carry is logged when a reviewer raises the same failure again at a site already decided (row 64), so it is a finding raised that round, and counting it is what shows a branch stuck on one settled finding. A row a later `--ref` retired still counts, because it was raised in its round.
  - A round that raised none prints `clean`. A clean round is usually where a reviewed diff passes, so the round after it usually reviews a different diff: the next commit's, a later fix's, or `/ready-for-review`'s cumulative one. The exception is `/code-review`'s already-reviewed skip, which logs a clean round for the same diff (row 64). This lets a consult tell a new diff's first findings from a relapse (row 63).
  - A row counts only when its round is an integer from 1 to 9999, the range `append` accepts (row 51). The round is normalized with `floor` before grouping and printing, so a stored `1.0` joins round 1 and prints as `1`; jq 1.7 and later otherwise print a number literal as written. Every printed `<N>` is therefore plain digits. With no counted row, `show` prints no `trend:` line.
  - There is no window, no gap entry and no elision. Each entry comes from at least one row, so the output grows with the number of rounds that have rows, never with the size of a round number (row 63). An entry takes about 13 bytes, so 60 rounds add about 0.8 KB. The series is the last element of the history line, so any truncation of that line costs only the series' newest entries.
- *Seam.* `_review_ledger_trend` in `_review-ledger-lib.sh` reads rows on stdin and prints the series. Its jq program sits beside `_REVIEW_LEDGER_JQ_RENDER` (row 21).
  - `show` pipes it the merged, sorted rows it already holds, so the trend, the history file and the branch-plus-session file assembly share one snapshot (row 50).
  - A trend computation that fails omits the `trend:` line and leaves `show`'s file and exit status as they would be without the flag.
  - One that stalls costs the announce hook the whole history line, as a stall in any other step of `show` does, because the hook's cap on `show` drops all of its output (row 22). A nested cap on the trend step was set aside: the step is one jq pass over rows `show` already holds, no likelier to stall than `show`'s existing jq steps.
- *Delivery.* `announce-branch-review-history.sh` passes `--trend` to the `show` call it already makes, so its capped calls stay at five (row 22).
  - In the `changed:` arm, the hook takes exactly the text between stdout's first and second newline. When that text starts with `trend: ` and holds at least one character after it, the hook appends the rest to the `Review-ledger history:` line after the legend `Findings raised per review round (a clean round usually ends one commit's review, and the round after it reviews a different diff):`. Otherwise the history line reads as it does today. Taking one line is line selection, not a check on the series: it keeps any later stdout line `show` might add out of the subagent's context.
  - The hook does not check the value. `show`'s own jq program builds it from integers and fixed words, so a check could only reject output that program cannot produce (row 62).
  - Every `plan-architect` dispatch therefore receives the series, whichever skill or hook prescribed it (row 13).
- *The plan's `Ask:` line and the SETTLED and DEFER rows.* Every prescribed consult site already passes the plan path, and the round-cap site also passes the ledger digest (row 14). The history file holds every SETTLED and DEFER row. These need an instruction to read them, not new plumbing.
- *Foundation framing.* `plan-architect.md`'s MODE=consult section gains a **Foundation questions.** paragraph after its Review-ledger history guard. When a consult asks whether a branch's foundation is wrong or its review loop is converging, the agent judges from three inputs, not from the latest round's findings alone:
  - the per-round series on the history line, read by the legend that line carries;
  - the `Ask:` line of the plan file the dispatch names, found with Grep;
  - the SETTLED and DEFER rows in the history file, found with Grep rather than a whole-file read, since the file grows with the branch.

  Any of the three it could not read goes under what it did not verify. The paragraph refers to the series by the legend's lead phrase, "Findings raised per review round", and does not restate its format.
- Rejected:
  - *A per-disposition split (`<round>:<address>/<defer>/<settled>/<carry>`).* It needs a field legend, and the legend needs caveats, since a DEFER count is not the number of live DEFERs. The live decisions already reach the consult through the history file (row 63).
  - *A series with an entry for every round between the first and the last.* Filling the gaps makes the output grow with the largest round number, which then needs a window, an elision marker and a byte budget. A round that logs no row, such as one ended by a re-plan verdict, shows as a jump in the printed round numbers, which loses nothing (row 63).
  - *Validating the series in the hook.* See row 62.
  - *A separate `trend` subcommand.* It adds a sixth capped call to a hook whose timeout drops all of its output (row 22). It takes a second ledger snapshot that an append can split from the history file. It also needs `show`'s file assembly moved into a shared function.
  - *The consult counting rows from the history file itself.* Counting by reading is error-prone over hundreds of rows, and #997's failure was a consult that did not weigh the trend.
  - *Inline jq in the hook.* It would put the ledger's row semantics outside the ledger's own library. Its input matrix could then be tested only through a hook subprocess, which `.claude/rules/shell-script-conventions.md` directs to direct tests instead.
  - *Prose at each dispatch site.* Every prescribed site would restate the same inputs, a defect under `claude/.claude/CLAUDE.md`'s single-source-of-truth rule. An ad hoc dispatch would also get none of them, while the hook and the agent body reach every `plan-architect` dispatch (row 13).
  - *The hook naming the plan path.* It would restate plan-it's slug rule in bash with no shared helper (row 15), while every prescribed site already passes the path.

**Records (rows 11, 24, 25, 26, 27, 28, 44, 45, 54, 55, 56, 61).** A new `docs/design-decisions/round-cap-consult-rearm.md` records:

- the interval and the reset, with the crash window and the first-consult read race;
- the counting of `/ready-for-review` reviews, and how often a run is interrupted, with its assumption;
- the Cap's retirement and its verdict mapping;
- why there is no clean-pass reset and no human ladder;
- everything a run loses, all six items above;
- that a re-plan verdict inside a run is an ADDRESS finding, that a pass is clean only when every reviewer its *Spawn decisions:* line lists as spawned has returned, and that a denied step-4 spawn leaves step 4 owed. The dirty classification is session-applied prose with no independent check, accepted under the `cooperative` tier, and the hook-observed reviewer-spawn record is the primitive a later check would use. The no-code exit leaves the push arm released until the session ends, as every other halt does (rows 61, 65);
- the consult inputs and their bounds;
- what would change the interval, and that no instrument evaluates it today;
- that a branch latched before this change meets its first re-armed consult two states early, one under the round-2 pilot, under deny text that counts its pre-consult states as recorded since the last consult;
- that a revert is state-compatible: no file format changes, and the reverted hooks ignore a state file of up to three lines once a latch exists.

It cites no ledger figure (row 28). `docs/hooks.md` states that the kill switch now disables every consult, `/ready-for-review` runs included, and its § "Gate deadlock recovery" gains the repair for a state file the consult cannot delete, with the kill switch as the human's fallback. Supersession notes go under the provenance lines of six files in `docs/design-decisions/`:

- `round3-plan-architect-consult-gate.md`: the single firing, and the paragraph on the latch's meaning.
- `round3-consult-verdict-routing.md`: "the gate's one per-branch firing". Its routing now serves every firing, `/ready-for-review` passes included.
- `ready-for-review-fix-loop-convergence.md`: "The cap" section, the hook's blindness inside a run, the mutually exclusive trigger windows, the Cap's stops under "How often the new stops interrupt a run", and "The keep/cap interaction". The disposition record and the contradiction route stand.
- `branch-scoped-review-ledger.md`: the record's canonical role for the Cap, and its cap rows. The record stays for the pass kind, reviewer and Outcome.
- `architect-authored-fix-plan-pilot.md`: its Known-gaps latch bullet (row 24) and its Cap-overlap bullet.
- `review-orchestrator-gate-release-decoupling.md`: "The round-cap consult fires once per branch".

The PR body names this change as a confound for #1211's before-and-after rounds-per-PR comparison (row 25). `code-review/SKILL.md` and `claude/.claude/CLAUDE.md` need no edit (rows 26, 27, 45).

**Accepted residuals.**

- A narrow consult restarts the count without asking the foundation question (row 29). Narrow means the contradiction route, the heavier-mechanism route or the fix-plan pilot. Such a consult still receives the series and the history, and its review stance asks whether a local patch sits on a wrong foundation. With the fix-plan pilot on, the gate stays silent, because that consult already returns a `foundation:` line every round.
- The gate fires before a pass's reviewers run, so a consult can land on a pass that would have come out clean. The Cap fired only after a dirty pass. That costs one consult, not a stop.
- Step 4's `skill-fidelity-reviewer` is a reviewer persona, so its spawn now counts too (row 52). It usually shares a state step 3 recorded and adds nothing. When step 3 was a cache hit and no recorded state matches the current one, a full window denies it, and **One consult process** above routes that denial.
- A branch that already holds a latch when this ships keeps its pre-consult states: two by default, one under the round-2 pilot (row 56). With a threshold of three, its first re-armed consult comes two states early by default and one under the pilot. That denial's text says its states were recorded since the last consult, which is untrue for the pre-consult ones. The error is one-time and limited to branches latched at pull time, so the CHANGELOG names the early consult and the text keeps its two wordings.
- The latch directory's 30-day sweep can drop the latch of a long-lived branch. The first-firing cap then applies once more, one state earlier than the interval would.
- A state file the consult cannot delete, such as one in a read-only state directory, leaves the gate denying after every consult. A plain `rm` from the engineer's terminal fails for the same reason, since it runs as the same user. `docs/hooks.md` § "Gate deadlock recovery" therefore names the repair before the removal, and the `round_consult_gate` kill switch, which the gate reads before any state (row 55), as the human's fallback.

**Assumption ledger.**

**Root:** After its one firing, `require-architect-consult.sh` goes silent for the branch and never counts reviews run under `/ready-for-review`. `/ready-for-review` therefore runs a second consult with its own trigger, evidence and verdicts. Neither consult gets a finding trend, `Ask:` line or decision history. A branch that keeps grinding past round 3 therefore gets no further foundation check outside a run, and one per dirty streak inside it.

**Givens:**

- **G4** — No branch in this repository's ledger is labelled converging or grinding. A label is the engineer's judgment of how one branch went, which no instrument records and no repository edit can supply for the branches already in the ledger.

**Mechanisms:**

- **M1** — On a consult, the recorder removes the branch's state file with `rm -f --`, tolerating failure and taking no lock, then touches the presence-only latch. Lighter primitives and the lock are set aside under **Counter** above. `anchors: root, row11, row12, row20, row30, row49`.
- **M2** — `_lib.sh` gains a `_LIB_ARCHITECT_CONSULT_REARM_INTERVAL=3` constant and one threshold helper taking the config dir and state key. `anchors: row1, row3, row4, row7, row10, row18, row33, row43, row48, row58`.
  - The helper always prints an integer: the interval when that branch's latch is a regular file, else `_lib_reviewer_round_state_cap`'s value, including for an empty argument or an unresolvable config dir.
  - The gate keeps the merge-base's count test and grep, against the threshold. grep's no-match status denies. When the grep cannot read the file, the latch-presence exit decides, as at the merge-base. The recorder never grows the file past the threshold and loses its latch short-circuit.
  - The deny text reports the threshold in effect. With a latch it says the states were recorded since the branch's last architect consult, and without one that no architect consult is recorded for this branch. Its noun is singular at a threshold of 1, so the word "states" appears nowhere in that text. It keeps naming "Round-cap architect consult".
- **M3** — Both hooks drop the `/ready-for-review` active-marker exit and keep the `/plan-review` one. `anchors: row6, row8, row9, row31, row43`.
- **M4** — `ready-for-review/SKILL.md` loses its Cap paragraph and the clauses that served only the Cap. It gains one six-sentence Overview paragraph, worded under Dispatch 3, that routes a gate denial inside a run. A re-plan verdict is an ADDRESS row or a step-4 finding, and the fix loop carries the revision through `/plan-review`, not a `code-writer` dispatch. A fix already staged is committed with the revision's implementation or discarded. A revision that changes no code ends the run with a report. A denied step-4 spawn leaves step 4 owed after the fix loop. Step 3's clean-pass sentence gains the conjunct that every reviewer its *Spawn decisions:* line lists as spawned has returned, and its `none` sentence keeps HEAD's wording. The disposition record stays. `anchors: row16, row17, row32, row34, row35, row36, row37, row38, row40, row42, row52, row53, row57, row59, row61, row65, row67, row68, row70`.
- **M5** — `review-ledger.sh show --trend`, computed by `_review_ledger_trend` in `_review-ledger-lib.sh`, printing the findings raised in each round that has a row, as `round <N>: <count>` or `round <N>: clean`, counting rounds 1 to 9999. `anchors: row2, row5, row21, row50, row51, row63, row64`.
- **M6** — `announce-branch-review-history.sh` passes `--trend` on its existing `show` call and appends the series, unchecked, after a one-phrase legend. `anchors: row5, row13, row22, row62, row63`.
- **M7** — `plan-architect.md` gains the Foundation questions paragraph, which finds the `Ask:` line and the SETTLED and DEFER rows by Grep. `anchors: row2, row5, row14`.
- **M8** — The new decision file, six supersession notes, the `docs/hooks.md` kill-switch and recovery lines, and a PR-body confound note, with no ledger figure. `anchors: row11, row24, row25, row28, row44, row54, row55, row56, row61`.
- **M9** — No edit to `code-review/SKILL.md` or `claude/.claude/CLAUDE.md`. The round-cap section's three verdicts, and its stop-and-ask for any other return, serve every firing. No ladder rule or clean-pass reset is added. `anchors: row26, row27, row39, row41, row45, row46, row47`.

**Rows:**

1. The consult re-arms after its first firing, and the engineer's stated cadence is three rounds. `[engineer-verified: "I've been asking for the architect consult to check the design foundation every 3 rounds not just once per branch"]`
2. The re-arm alone is not enough; the consult needs more context than the current diff. `[engineer-verified: "Can't just do the re-arm every 3 rounds but need to give the consult more context."]`
3. The engineer weighs a consult's cost against the rounds it prevents. `[engineer-verified: "that cost argument if flawed if the foundation check would prevent excess rounds"]`
4. The re-arm trigger is this plan's decision. The earlier label "Windowed trend (Recommended)" was handed back to the architect by this answer to the fixed-interval follow-up. `[engineer-verified: "Ask the architect"]`
5. The consult receives four inputs. `[engineer-verified: "Finding trend", "The plan's Ask line", "Settled and deferred rows", "Foundation framing"]`
6. Reviews run under `/ready-for-review` count toward the re-arm. `[engineer-verified: "Count them (Recommended)"]`
7. On the current ledger, #997's windowed rule fires on most long branches at about the same round, and the series are spiky. `[unverified: relayed from the dispatching session's backtest; its scratch scripts sit outside the repository and plan-architect has no Bash to re-run them]`
8. The case study's round unit counts every Skill-invoked `/code-review`, and `/ready-for-review` step 3 invokes `/code-review` through the Skill tool. `[verified: claude/.claude/scripts/transcript_analysis/review_trace.py:302-317; docs/case-studies/opus-frontload-review-rounds.md:35; claude-skills/skills/ready-for-review/SKILL.md:82]` Whether the 2026-09-01 corpus's `/ready-for-review` invoked it the same way is `[unverified]`.
9. The live-marker exit's cited rationale is the count of `/plan-review` branches only. `[verified: claude/.claude/hooks/require-architect-consult.sh:74-77; docs/case-studies/opus-frontload-review-rounds.md:36]`
10. One distinct `(HEAD, staged-diff)` state corresponds to one review round, and a parallel fan-out at one state records one line. `[verified: docs/design-decisions/round3-plan-architect-consult-gate.md:26-32; claude/.claude/hooks/_lib.sh:4718-4738]`
11. The single firing rests on two grounds: a content-addressed latch would re-deny on the first post-consult fix, and a re-arming latch would be a compounding layer. `[verified: docs/design-decisions/round3-plan-architect-consult-gate.md:46-58]`
12. Any `plan-architect` dispatch whose first line is not `MODE=plan-sections` writes the latch. `[verified: claude/.claude/hooks/log-reviewer-round.sh:123-140]`
13. `announce-branch-review-history.sh` runs on every `plan-architect` dispatch and adds one context line chosen from `show`'s stdout. `[verified: claude/.claude/hooks/announce-branch-review-history.sh:29-69; claude/.claude/settings.json:206-207]`
14. The round-cap consult site passes the plan path and the ledger digest. The heavier-mechanism site and the RFR Cap pass the plan path. `[verified: claude-skills/skills/code-review/SKILL.md:313,358; claude-skills/skills/ready-for-review/SKILL.md:86]`
15. No helper resolves a branch's plan path; plan-it's slug rule is prose. `[verified: claude-skills/skills/plan-it/SKILL.md:23; consult-inputs exploration report relayed in the dispatch]`
16. The RFR Cap paragraph, step 3's clean/dirty sentence and its loop-back sentence are each pinned verbatim in one parametrized test. `[verified: claude-skills/skills/tests/test_skills.py:6248-6327]`
17. `/ready-for-review`'s Cap lives in prose only because both hooks return early while the run's active marker is live. `[verified: docs/design-decisions/ready-for-review-fix-loop-convergence.md:55; claude/.claude/hooks/require-architect-consult.sh:81-84; claude/.claude/hooks/log-reviewer-round.sh:78-81]`
18. A behavioral test requires the emitted deny text to name "Round-cap architect consult" and to contain "2 distinct reviewed states ". A pilot test requires "1 distinct reviewed state " and no "states" anywhere. A separate source scan checks the hook file's text. `[verified: claude/.claude/hooks/tests/test_require_architect_consult.py:317-342,434-448; claude/.claude/hooks/tests/test_hook_alignment.py:449-467]`
19. The handoff nudge stores its last-fired value in its marker and validates it with a `case` before use. `[verified: claude/.claude/hooks/nudge-handoff-near-context-cap.sh:180-190]`
20. `_lib_acquire_append_lock` takes a lock path, holds the lock until the process exits, and is what `_lib_append_line_locked` uses for the state file. `[verified: claude/.claude/hooks/_lib.sh:4690-4699,4725-4738]`
21. `_review-ledger-lib.sh` holds the jq programs over ledger rows, and it has a direct test file. `[verified: claude/.claude/scripts/_review-ledger-lib.sh:399,665; claude/.claude/scripts/tests/test_review_ledger_lib.py]`
22. The announce hook already makes five sequential 5-second capped calls under a 10-second registration timeout: two `_lib_jq` calls and two `git` calls besides `show`. A timeout drops all of its output without blocking the dispatch. `[verified: claude/.claude/hooks/_lib.sh:39-41,50-52; claude/.claude/hooks/announce-branch-review-history.sh:31,37,42,47,57; claude/.claude/settings.json:207; docs/hooks.md:196]`
23. `review-trace` already reports architect-consult dispatches per branch. `[verified: claude/.claude/scripts/transcript_analysis/review_trace.py:331-337,712-722]`
24. The engineer accepted the fix-plan pilot's consult writing the latch, when the latch fired once per branch. `[verified: .claude/plans/architect-authored-fix-plan.md:205; docs/design-decisions/architect-authored-fix-plan-pilot.md:181-192]`
25. #1211 schedules no fixed baseline measurement. Its Measurement section compares rounds per PR before and after each step, so this change confounds that comparison rather than blocking it. `[verified: #1211 Measurement section]`
26. `claude/.claude/CLAUDE.md` already covers a hook-prescribed consult, and its round-3 carve-out states no single-firing claim. `[verified: claude/.claude/CLAUDE.md:162]`
27. `code-review/SKILL.md`'s round-cap section makes no single-firing claim and handles every deny the same way. `[verified: claude-skills/skills/code-review/SKILL.md:310-320]`
28. A published figure from this repository's own corpus must have a command beside it that refuses a wider corpus. `[verified: docs/private-project-redaction.md:108-124]`
29. A narrow consult writes the latch whatever it asked, and every `plan-architect` dispatch gets the history line. `[verified: claude/.claude/hooks/log-reviewer-round.sh:127-130; claude/.claude/hooks/announce-branch-review-history.sh:29-31]`
30. Only `require-architect-consult.sh` and `log-reviewer-round.sh` read `.reviewer-round-state.d`. Its other mentions are `_lib.sh`, test fixtures and docs. `[verified: grep for reviewer-round-state.d across the repository, plans excluded]`
31. `/ready-for-review`'s cumulative passes and its fix commits' staged reviews both run while its active marker is live. `[verified: docs/design-decisions/ready-for-review-fix-loop-convergence.md:13; claude-skills/skills/ready-for-review/SKILL.md:82]`
32. The round-cap section's re-plan routing names no human stop, and an RFR *stop* cap row is a blocking stop-and-ask. `plan-it`'s revision re-dispatch still asks the engineer any open decision the revision returns. `[verified: claude-skills/skills/code-review/SKILL.md:315; claude-skills/skills/ready-for-review/SKILL.md:86; claude-skills/skills/plan-it/SKILL.md:77]`
33. A helper two hooks share belongs in `_lib.sh`. `[verified: .claude/rules/bash-unit-test-seams.md]`
34. The deliverable is one consult process that replaces both consults and covers every code review. `[engineer-verified: "a single consult process that handles both of what the current RFR and currenet code review consults do but across all code reviews instead"]`
35. A rule reusing one consult's verdict in the other is a compounding defensive layer. `[engineer-verified: "Wait, that's compounding defensive engineering."]`
36. Besides the Cap, the disposition records' one reader is the re-review carry-forward. Every re-review spawn prompt carries this branch's records, and a spawn confirms that each earlier ADDRESS row's Outcome fix landed. Tests name a record only as a peer file that `render --out` refuses. `[verified: claude-skills/skills/code-review/SKILL.md:270; claude-skills/skills/tests/test_skills.py:6855-6877; grep for code-review-dispositions and "disposition record" across the repository, plans excluded]`
37. The ledger has no pass-kind, reviewer or Outcome column, and the record carries all three. `[verified: docs/design-decisions/branch-scoped-review-ledger.md:184-188,236]`
38. Step 3's clean-pass definition also decides when the `cumulative-review` marker is written. `[verified: claude-skills/skills/ready-for-review/SKILL.md:79,82,84]`
39. The round-cap section makes a return that reads as none of its three verdicts, or one the orchestrator disagrees with, a blocking stop-and-ask. `[verified: claude-skills/skills/code-review/SKILL.md:319]`
40. A re-plan verdict ends the round before any reviewer runs and dispatches no fix. Step 3 records a pass with no findings as one `none` row, which counts as clean. `[verified: claude-skills/skills/code-review/SKILL.md:313,315; claude-skills/skills/ready-for-review/SKILL.md:84]`
41. The noise verdict is evidence, per finding, for the gold-plating DEFER criterion, and a DEFERred finding counts as resolved. `[verified: claude-skills/skills/code-review/SKILL.md:316; docs/design-decisions/ready-for-review-fix-loop-convergence.md:41]`
42. The Cap's named residual is dirty passes alternating with clean ones, which escape it through its clean-pass reset. `[verified: docs/design-decisions/ready-for-review-fix-loop-convergence.md:92]`
43. `require-architect-consult.sh` is tiered `cooperative`, and a change to a gate is reviewed for inputs it denied at the merge-base that it now allows. M1 to M3 remove none. Every merge-base state denial passes the latch-presence test, so it needs a latch that is not a regular file. For such a latch the threshold helper prints `_lib_reviewer_round_state_cap`'s value, which is the merge-base threshold, and it prints that value too when it cannot resolve its arguments. With that threshold, the count test, the grep and the latch test keep their merge-base outcomes, error paths included: a state file that cannot be read, or vanishes mid-read, reaches the latch test and is denied (row 58). With a latch that is a regular file, the merge-base gate denies nothing. Dropping the `/ready-for-review` exit only adds denials. `[verified: claude/.claude/hooks/require-architect-consult.sh:3,81-84,99-124; claude/.claude/hooks/_lib.sh:4502-4515; docs/hooks.md:26,28,81]`
44. Beyond the three decision files the earlier notes cover, `round3-consult-verdict-routing.md:10` and `review-orchestrator-gate-release-decoupling.md:11` state the single firing. `branch-scoped-review-ledger.md:183-191,236` and `architect-authored-fix-plan-pilot.md:200-201` describe the Cap. `[verified: grep for the single firing's and the Cap's wording across docs/, plans excluded]`
45. `code-review/SKILL.md` mentions neither the Cap nor the hooks' `/ready-for-review` exit. `[verified: grep for "ready-for-review", "RFR" and "cap" in claude-skills/skills/code-review/SKILL.md]`
46. In consult mode `plan-architect` opens with one of the caller's verdicts, or says in its first line that none fits rather than bending one. `[verified: claude/.claude/agents/plan-architect.md:120]`
47. The engineer accepts that a run loses two blocking stops. A foundation-wrong verdict re-plans instead of stopping, and a dirty streak no longer reaches the human after one consult by rule. `[engineer-verified: "A: one routing (Recommended)"]`, the label selected when asked whether to keep the round-cap routing unchanged everywhere or restore either stop.
48. The gate tests the count as `[ "$(wc -l < file)" -lt CAP ]` after a `-f` check. An empty operand makes `[` fail, which falls through toward the deny, and the latch-presence exit is the last allow before `emit_deny`. `[verified: claude/.claude/hooks/require-architect-consult.sh:104,115-124; the empty-operand failure relayed from runs in this plan's staff-platform-engineer and staff-sdet reviews]`
49. `_lib_acquire_append_lock` retries a failed noclobber write with a sleep, then prints an "exhausted ... retries" line and returns unlocked, which is what a missing state directory produces. `[verified: claude/.claude/hooks/_lib.sh:4690-4716]`
50. `show` merges the branch and session ledger files into one sorted row set before any output. Under `--out` its stdout is `changed: <path>`, printed by `_review_ledger_write_out` only after a successful write, or `unchanged` when there are no rows or no ledger. `[verified: claude/.claude/scripts/review-ledger.sh:105-128,664-698,731-737; claude/.claude/scripts/_review-ledger-lib.sh:945-954]`
51. `append` accepts a round of at most four digits. `[verified: claude/.claude/scripts/review-ledger.sh:25]`
52. `skill-fidelity-reviewer` is a reviewer persona for both hooks, which share `_lib_is_reviewer_persona`, and a test pins that set to the review-only roster minus Explore and Plan. `[verified: claude/.claude/hooks/_lib.sh:4337-4350,4461-4481; claude/.claude/hooks/tests/test_lib_payload_matchers.py:308-322]`
53. A modified plan file under `.claude/plans/` blocks Write and Edit outside the plan files until a `/plan-review` marker covers its state. `[verified: claude/.claude/hooks/require-plan-review.sh:4-8]`
54. The Cap consulted before a dirty pass's fix was dispatched, and recorded the verdict as a `cap` row in the disposition record. `[verified: claude-skills/skills/ready-for-review/SKILL.md:86]`
55. The gate checks its kill switch before any state read, and allows on an unresolvable config dir or repo root and on detached HEAD. `[verified: claude/.claude/hooks/require-architect-consult.sh:15-22,72,88-95]`
56. Once a latch exists, the current recorder stops appending, so a branch latched before this change holds at most the cap's worth of states: two by default, one under the round-2 pilot. `[verified: claude/.claude/hooks/log-reviewer-round.sh:85-89,98-111; claude/.claude/hooks/_lib.sh:4492,4508-4515]`
57. Comments outside the quoted strings of the ready-for-review Step 3 pins name the Cap. `[verified: grep for "the Cap" and "loop cap" in claude-skills/skills/tests/test_skills.py, lines 6248, 6265-6267 and 6279]` The class docstring at 6294-6300 naming the cap consult is `[unverified]`, relayed from this plan's staff-sdet review.
58. grep exits 2, not its no-match status 1, when it cannot open its file, whether the file is missing or lacks read permission. `[unverified: plan-architect cannot run grep; claude/.claude/hooks/require-code-review.sh:186-190 already relies on the missing-file case]` The vanished-file and unreadable-file gate tests pin both cases, since each would deny with a latch if grep returned 1.
59. A dirty pass's fix commit goes through the staged-diff `/code-review`, and step 1 halts unless the working tree is clean. `[verified: claude-skills/skills/ready-for-review/SKILL.md:34,82]`
60. The run-exit tail falls within one routing. Inside `/ready-for-review`, a re-plan verdict halts the run with no push, and the session revises the plan, runs `/plan-review`, implements the revision and starts a fresh run, adding no question to the engineer. `[engineer-verified: "Yes, halt and restart (Recommended)"]`, the label selected when asked whether that tail fits "one routing". The question did not cover a fix left staged when the verdict arrives, which is this plan's decision (row 59). Row 65 replaces the halt-and-restart tail.
61. The run's active marker releases the push arm and is removed only by step 9, or by PID-liveness eviction once the session's process ends. `[verified: claude/.claude/hooks/require-ready-for-review.sh:16-23; claude-skills/skills/ready-for-review/SKILL.md:154-161]`
62. The hook does not check the series. `[engineer-verified: "Oh yeah so the check is completely flawed."]`, said of a check that could only reject output `show`'s own jq program cannot produce. That the redesigned program prints only integers and fixed words is this plan's design (M5).
63. The series is self-describing findings raised per round, one entry per round that has a row, with a clean round marking where one diff's review ends. It replaces the per-disposition split, the 30-round window and the gap entries. `[engineer-verified: "yes I agree with the shape of your recommednations"]`, said in answer to the session's proposal of a phrase-form count per round, clean rounds as the boundary, no window or gap entries, and no hook check.
64. A same-failure-mode repeat at a live DEFER decision, or at a live engineer SETTLED logged `--carry-forward`, is logged as a carry. `/code-review`'s already-reviewed skip logs a CLEAN row for a staged diff a clean marker already covers. `[verified: claude-skills/skills/code-review/SKILL.md:27,380]`
65. Inside a run, a re-plan verdict is an ADDRESS finding, so its pass is dirty and the fix loop carries the revision. `[engineer-verified: "Wait but wouldn’t the replan verdict itself be an ADDRESS finding?"]`, said of the two invariant findings that the halt's marker and push guards were prose only, then `[engineer-verified: "Yes make this change"]` in answer to the session's proposal. That proposal recorded the verdict as an ADDRESS row and dropped the halt clause. The run would continue through the fix loop, with a revision that changes no code still ending in a report, and the decision file and pin would change to match. The no-reviewer `none` bound and the step-4 re-run came later, from this plan's review (rows 66, 67). A pass with an ADDRESS row is dirty, because only DEFER and SETTLED rows count as resolved, and only a clean pass writes the `cumulative-review` marker. `[verified: claude-skills/skills/code-review/SKILL.md:472; claude-skills/skills/ready-for-review/SKILL.md:84]`
66. A pass no reviewer finished is dirty, so step 3's `none` row means reviewers ran and found nothing. `[engineer-verified: "Yes, add it (Recommended)"]`, the label selected when asked whether a pass no reviewer finished should always be dirty, not only one a re-plan ended.
67. A denied step-4 spawn leaves step 4 owed after the fix loop. `[engineer-verified: "Yes, re-run it (Recommended)"]`, the label selected when asked whether step 4 should run again after the gate denied its reviewer and the fix loop ran.
68. The run continues through the revision, a revision that changes no code ends the run with a report, and the push arm left released at that exit is an accepted gap. `[engineer-verified: "Yes, as proposed (Recommended)"]`, the label selected when asked whether the earlier approval covered these.
69. A pass that any owed reviewer did not finish is dirty, which covers a partly reviewed pass as well as an empty one. `[engineer-verified: "Reword to "any owed" (Recommended)"]`, the label selected when asked whether a pass in which any owed reviewer did not finish should also be dirty. The asked wording was "…and a pass any owed reviewer did not finish is dirty." Row 70 replaces rows 66 and 69's wording.
70. Step 3's clean-pass sentence carries the condition: a pass is clean only when every reviewer its *Spawn decisions:* line lists as spawned has returned and every row is resolved. The `none` sentence reverts to HEAD's wording. `[engineer-verified: "Move it there (Recommended)"]`, the label selected when asked whether to move the every-reviewer-finished condition into that single sentence, keyed on the *Spawn decisions:* line, with the `none` sentence reverted. This came after the delta review found that "any owed" still let the clean-pass sentence call a partly reviewed pass clean, and that "owed" had no definition.
71. A no-output `git grep` for "fresh run" and "halts this run" in Verification replaces the negative Overview test. The wording never reached `main` (`git grep -n -e "fresh run" -e "halts this run" origin/main -- claude-skills/skills` prints nothing), so a durable test comment could only describe it as retired. Round 10's sdet R1 accepted either a negative test or a one-time grep. `[verified]`

## Critical files

Three `code-writer` dispatches, run in order: 1, then 2, then 3.

- Dispatch 2's subprocess tests source dispatch 1's `_lib.sh`, and the repo's shellcheck command lints every tracked shell file. A parallel run would therefore check one dispatch against the other's half-finished files.
- Dispatch 3 describes both, and its Cap retirement is correct only once dispatch 1's hooks count reviews inside a run.
- The PR is the revert unit. Reverting dispatch 1 alone after dispatch 3 would leave `/ready-for-review` with no consult.
- Each dispatch verifies with the repo's own commands. `select-tests.py` already scopes to the diff, and the shellcheck command has no narrower documented form.

**Dispatch 1 — gate and recorder.** Verify with `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, then `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`. Also run the new cadence test once against the merge-base hooks, in a temporary `git worktree` at `git merge-base HEAD origin/main` with the test file copied in, and confirm it fails at state 6, or 5 under the pilot. Run the vanished-file and unreadable-file cases there too, and confirm they pass unchanged.

- `claude/.claude/hooks/_lib.sh` — the interval constant and threshold helper (M2). Correct the `_LIB_REVIEWER_ROUND_STATE_CAP` comment, which says the recorder never appends past the cap. Reuse `_lib_reviewer_round_state_cap`.
- `claude/.claude/hooks/require-architect-consult.sh`:
  - Take the threshold from the helper and keep the count test and grep as they are. Deny on grep's no-match status. Run the latch-presence exit only when grep reports an error, so it no longer allows a new state on a full window.
  - Drop the `/ready-for-review` exit and restate the remaining exit's rationale for `/plan-review` alone.
  - Word the deny text per M2.
  - The header's Known gaps lose "Once-per-branch, non-rearming".
- `claude/.claude/hooks/log-reviewer-round.sh` — drop the `/ready-for-review` exit and the latch short-circuit, and cap recording at the helper's threshold. In `_maybe_write_consult_latch`, after the `MODE=plan-sections` check and context resolution, `rm -f --` the branch's state file, tolerating failure and taking no lock, then `touch` the latch. Update the header.
- `claude/.claude/tests/helpers.py` — only if a fixture for a latch plus a seeded state file is needed.
- `claude/.claude/hooks/tests/config-schema-audit.md` — delete the `round_consult_round2_pilot` entry's parenthetical naming the cap function's callers, which this change makes false. `[engineer-verified: "yes you should put the comment cuts including deleting the parenthetical into the same single code-writer fix as the five address rows."]`
- `claude/.claude/hooks/tests/test_lib_reviewer_round_state.py` — the helper's contract. Each case asserts a literal integer on stdout and exit 0, never the constant:
  - no latch, 2;
  - a zero-byte latch, 3;
  - a latch under the pilot, 3;
  - no latch under the pilot, 1;
  - a directory at the latch path, 2;
  - an empty config-dir argument, and an unresolvable config dir, 2;
  - an empty state-key argument, as on detached HEAD, 2;
  - `config-keys.psv` missing, 2.
- `claude/.claude/hooks/tests/test_require_architect_consult.py`:
  - Rebuild `test_at_cap_new_state_with_latch_allows` as latch-present threshold tests. Two states plus a new one allows, three states plus a new one denies, and three states plus a repeated one allows. The deny case also runs under the pilot. `test_at_cap_new_state_denies` stays as the no-latch cap guard.
  - Turn `test_live_ready_for_review_active_marker_allows` and `test_live_ready_for_review_active_marker_allows_under_pilot` into deny tests.
  - Update `test_deny_message_contents` and `test_deny_message_singular_noun_under_pilot` to the new wording.
  - Add a latch-present deny-message test. It reports 3, says the states were recorded since the last architect consult, reads plural with and without the pilot, and still names "Round-cap architect consult".
  - Add a case with a latch, three states, a new state and the kill switch present, expecting allow.
  - Add a vanished-state-file case on a seeded three-line state file. A `git` stub on `PATH`, built the way this file's `jq` stub is, deletes the file during the state-value call, after the count read and before the grep. It allows with a latch and denies without one.
  - Add an unreadable-state-file case on the same three-line file. It allows with a latch and denies without one, the merge-base outcome. Skip it when the tests run as root.
  - Add the cadence test under Verification.
- `claude/.claude/hooks/tests/test_log_reviewer_round.py`:
  - Replace `test_latch_short_circuits_further_appends` and `test_latch_short_circuit_unchanged_under_pilot` with latch-present threshold tests: the file grows to three states and no further, with and without the pilot. `test_file_never_exceeds_cap` stays as the no-latch cap guard.
  - Turn `test_live_ready_for_review_active_marker_skips_recording` and `test_live_ready_for_review_active_marker_skips_recording_below_pilot_cap` into recording tests.
  - Delete `test_live_ready_for_review_active_marker_preserves_state_file_under_pilot`. Its setup already sits at the cap, so it cannot record, and the below-cap test covers the pilot.
  - Extend `test_latch_classification_matches_table` with a seeded state file: the file is deleted exactly when the row's `expect_consult` is true.
  - Add reset cases. Another branch's state file stays byte-identical after a consult. Two consults in a row exit 0 and leave the latch. A consult with no state file, and one with no state directory, writes the latch, creates no state directory and prints nothing on stderr. A consult on detached HEAD deletes nothing.

**Dispatch 2 — consult inputs.** Verify with `select-tests.py`, the shellcheck command above, and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`. Run `/agent-review` on the `plan-architect.md` diff.

- `claude/.claude/scripts/_review-ledger-lib.sh` — `_review_ledger_trend` and its jq program, built on `_REVIEW_LEDGER_JQ_COMMON`.
- `claude/.claude/scripts/review-ledger.sh` — the `--trend` flag on `show`, parsed in its argument loop and used only after the rows are read. Document it in the top `Usage:` line and in `usage()`. Reword `usage()`'s "A consumer acts on the stdout line" (`:168`) so the `changed:` or `unchanged` line stays authoritative and `trend:` reads as an additional line after `changed:`. `show`'s file assembly and exit matrix stay as they are.
- `claude/.claude/hooks/announce-branch-review-history.sh` — pass `--trend` and append the series after its legend, unchecked (M6).
- `claude/.claude/agents/plan-architect.md` — the Foundation questions paragraph (M7), naming the lead phrase "Findings raised per review round".
- `claude/.claude/scripts/tests/test_review_ledger_lib.py` — the trend matrix through `_review_ledger_trend`, each case with its expected stdout written out:
  - rows in input order round 1, round 2, round 1, where round 1 holds two ADDRESS rows, a DEFER, a SETTLED, a DEFER carry and a CLEAN, print one round 1 entry with a count of 5;
  - a SETTLED carry counts, a retired row counts, and a CLEAN-only round prints `clean`;
  - rows logged for rounds 1 and 3 print only those two entries;
  - rows for rounds 100, 9 and 10, in that input order, print in numeric order;
  - a round stored as `1.0` beside one stored as `1` prints a single `round 1` entry counting both;
  - beside a valid round, a round of `"2"`, of a string holding a newline and `trend:`, of null, of `true`, of 0, of -1, of 2.5 and of 10000, and a row with no round, are each skipped;
  - no rows, and rows none of which has a counted round, print nothing;
  - with a failing jq, `_review_ledger_trend` returns non-zero and prints nothing (the sibling tests' `path_with_failing_tool_shim`).
- `claude/.claude/hooks/tests/test_review_ledger_script.py`:
  - `show --out <path> --trend` with rows prints both lines. A branch-file row and a session-file row, each with round 1 and a counted disposition, print `trend: round 1: 2`; the existing `seeded_repo` session row has neither, so this case seeds its own. No ledger prints `unchanged` alone.
  - Rows none of which has a counted round, such as the `seeded_repo` session row alone, print `changed: <path>` with no `trend:` line.
  - `--trend` without `--out`, and `--trend` given twice, exit 2.
  - The session-chokepoint parametrizations at `:244` and `:264` gain no row. `--trend` adds no path before session resolution, and an `--out` row there would exit 2 at the path check and pin nothing. The existing exit-status cases pass unedited.
- `claude/.claude/hooks/tests/test_announce_branch_review_history.py`:
  - Real-hook cases write their expected series as a literal from the seeded rows, with no helper that recounts them, which updates the exact-equality assertions at `:171-176` and `:189`.
  - A seeded-row variant of `test_plan_architect_guard_names_the_lead_phrase_the_hook_emits` seeds three ADDRESS rows in round 1, a CLEAN row in round 2 and five DEFER rows in round 7. It checks the emitted legend and the literal series `round 1: 3, round 2: clean, round 7: 5`, and that `plan-architect.md` names the legend's lead phrase.
  - Stub cases for `show`'s stdout, each with its expected context written out. A `trend:` line's value, one the lib could never emit, is appended verbatim after the legend. A third stdout line is not appended. `trend: ` with an empty value, and an absent `trend:` line, leave today's line with no trailing fragment.

**Dispatch 3 — retire the Cap, and records.** Verify with `select-tests.py`, and run `/skill-review` on the `ready-for-review/SKILL.md` diff.

- `claude-skills/skills/ready-for-review/SKILL.md` (M4):
  - The baseline is the staged tree, which already implements an earlier halt-and-restart design for this paragraph and its pin. Delete the Cap paragraph. Add the run-exit paragraph to the Overview, after its halt-routing paragraph, as these six sentences in this order, the last one pinned. Sentences 1 and 2 carry code-review round 11's fix, which gave the retry a re-plan exception and a stop when the retry is denied again:
    1. When `require-architect-consult.sh` denies a reviewer spawn in this run, step 4's included, dispatch the consult per `code-review/SKILL.md` § "Round-cap architect consult" and retry the spawn unless the verdict is re-plan, which ends that round with no further spawns.
    2. When a retried spawn is denied again after the consult, stop and report to the engineer instead of consulting again.
    3. A re-plan verdict's fix is not a `code-writer` dispatch: revise the plan by that section's route, run `/plan-review`, then implement the revision and commit it through the staged-diff `/code-review`, together with any fix staged when the verdict arrived or after discarding that fix.
    4. A revision that changes no code ends the run with a report to the engineer.
    5. A step-4 spawn the gate denied reviewed nothing, so step 4 runs again after the fix loop.
    6. A re-plan verdict is an ADDRESS row in the disposition record of the step-3 pass under review or whose fix is under review, or a step-4 finding, and the fix loop takes it like any other finding.

    No sentence in the paragraph names a halt, a fresh run, or a missing record, marker or push.
  - In step 3's Disposition-record paragraph, revert the `none` clause to HEAD's "a pass with no findings records one row whose finding cell reads `none`". The clean-pass sentence becomes "The pass is clean when every reviewer its *Spawn decisions:* line lists as spawned has returned and every row is resolved as `code-review/SKILL.md` § "Step — Record review completion" counts it (a `none` row counts as resolved), and dirty otherwise."
  - In the Disposition-record paragraph, replace "canonical only for the Cap's pass accounting", and drop the Cap clause from the clean/dirty sentence.
  - Drop "within the cap below" from the loop-back sentence.
  - The file has no more lines than HEAD's 200.
- `claude-skills/skills/tests/test_skills.py`. The staged tree already holds `_PINNED_RE_PLAN_ADDRESS_CLAUSE` (sentence 6, right-bounded), `_PINNED_STEP4_DENIED_RERUN_CLAUSE` (sentence 5, containment), `_OVERVIEW_RUN_END_PHRASES` with its negative test, and `_PINNED_STEP3_NONE_ROW_CLAUSE` with `test_step3_none_row_sentence_marks_an_unfinished_pass_dirty`. The remaining change moves the no-unfinished-reviewer pin and drops the negative test:
  - Delete `_OVERVIEW_RUN_END_PHRASES`, `test_overview_omits_the_retired_halt_and_restart_wording` and the class-docstring clause about them. The wording they guard never reached `main`, and their comment can only describe it as retired, which §Durable text's self-test rejects. Verification's no-output grep replaces them (row 71).
  - Delete `_PINNED_STEP3_NONE_ROW_CLAUSE` and its test. Edit `_PINNED_STEP3_CLEAN_DIRTY_CLAUSE` to the new clean-pass sentence, so its right-bounded check carries the every-listed-reviewer-returned conjunct. Edit the constant first and confirm it fails against the staged sentence before editing SKILL.md.
  - Restate the comment above that constant and the class docstring so they name the conjunct, and drop "no reviewer finished".
  - `_PINNED_STEP3_LOOP_BACK_CLAUSE` stays as staged.
  - The `code-review-round-cap-consult-verdict` region, `_PINNED_RIPPLE_CARRY_FORWARD_CLAUSE` and the findings-file exclusion test stay as they are.
- `docs/design-decisions/round-cap-consult-rearm.md` — new. The staged draft's "A re-plan verdict ends the run" and "no-push guard" bullets describe the halt design. Rewrite them to the ADDRESS rule, the clean-pass conjunct (its "A pass no reviewer finished is dirty" bullet becomes "A pass is clean only when every reviewer its Spawn decisions line lists as spawned has returned", and credits the ADDRESS row, not that conjunct, with making a re-plan pass dirty), the step-4 re-run, the staged-review backstop and the accepted gaps under **Approach**. Rewrite the staged `CHANGELOG.md` Cap bullet the same way.
- Supersession notes only, all under `docs/design-decisions/`: `round3-plan-architect-consult-gate.md`, `round3-consult-verdict-routing.md`, `ready-for-review-fix-loop-convergence.md`, `branch-scoped-review-ledger.md`, `architect-authored-fix-plan-pilot.md`, `review-orchestrator-gate-release-decoupling.md`.
- `docs/hooks.md`:
  - The `require-architect-consult.sh`, `announce-branch-review-history.sh` and `log-reviewer-round.sh` bullets (lines 161, 196, 303). The first states that the kill switch now disables every consult, `/ready-for-review` runs included.
  - The `announce-branch-review-history.sh` bullet drops "one fixed" for the history-file line and says that line carries the legend and the series. Its "To diagnose" step 3 adds `--trend` to the `show --out` command and says the series is the second stdout line. Its Known gaps add that the hook passes the series on unchecked and that the series grows with the number of rounds that have rows.
  - § "Gate deadlock recovery" gains a short entry for when every consult leaves the gate denying. From a separate terminal, first repair the cause: restore write permission on `<config-dir>/.reviewer-round-state.d`, or remove whatever object sits at `<repo-hash>.<branch-hash>` inside it with the tool that fits it. Then delete the state file. When that still fails, the human sets `round_consult_gate = false`, which disables every consult; an agent never sets it.
- `docs/scripts.md` — `--trend` beside the `review-ledger.sh` `show` bullet.
- `README.md` — lines 81 and 191.
- `CHANGELOG.md` — an Unreleased entry naming:
  - the re-arm every three reviewed states;
  - that `/ready-for-review` reviews now count, so a consult interrupts a run at a denied reviewer spawn, routinely once the run needs a fix, and can land on a pass that would have come out clean;
  - what each consult now receives, and `show --trend`;
  - the Cap's retirement and the two stops a run loses. A foundation-wrong verdict inside a run is an ADDRESS finding whose fix revises the plan inside the run's fix loop. A dirty streak reaches the human only when a consult sends it there, and no rule sends a long one there by its length;
  - that the kill switch now silences the consult inside runs too;
  - that a branch already latched at pull time meets its first re-armed consult two states early, one under the round-2 pilot, under deny text that counts its earlier states as recorded since the last consult;
  - that a `/ready-for-review` run already under way at pull time keeps the old Cap text while the hooks already count its reviews, so it can run both consults until it ends.

## Verification

1. Run each dispatch's commands above, in dispatch order. After the last dispatch, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` and the shellcheck and ruff commands once more over the whole diff.
2. The tests pin these behaviors:
   - **Cadence.** One test, parametrized over the pilot off and on, drives the recorder and the gate through ten new states.
     - At each denial it dispatches a consult through the recorder, then retries the denied state, which must be allowed and recorded as the new window's first state.
     - It never runs the recorder for a denied spawn, since a denied spawn runs no `PostToolUse`.
     - It asserts denials at the literal states 3, 6 and 9, and 2, 5 and 8 under the pilot, and reads no constant from `_lib.sh`. It uses no wall-clock reads.
     - Against the merge-base hooks it fails at state 6, or 5 under the pilot.
   - **Threshold helper.** Each contract case prints its literal integer and exits 0.
   - **Markers.** A live `/ready-for-review` marker is recorded and gated. A live `/plan-review` marker is neither.
   - **Reset.** A consult removes only its own branch's state file and writes the latch. A `MODE=plan-sections` dispatch and a detached HEAD do neither. A consult with no state file or directory, or a second consult in a row, exits 0 with no stderr. The order of the two steps is not tested, and there is no lock to test, because no outcome depends on either (**Counter**).
   - **Gate reads.** A state file that vanishes mid-read, or cannot be read, allows with a latch and denies without one, against the merge-base hooks and the changed ones alike. With a latch and a full window, the kill switch still allows.
   - **Fan-out.** The existing concurrency test still yields one line per state.
   - **Deny text.** The threshold in effect, the since-last-consult wording with a latch, the singular noun at a threshold of 1, and "Round-cap architect consult".
   - **Trend.** The lib matrix and the script cases under Dispatch 2, each with its expected stdout.
   - **Announce hook.** The seeded legend and series appear, and the agent body names the legend's lead phrase. Only `show`'s second stdout line is appended, and an empty or absent series leaves today's line.
   - **Run-exit pins.** The re-plan pin binds the verdict to an ADDRESS row or step-4 finding and to the fix loop in one clause, and fails against the staged halt text. The step-4 re-run sentence is pinned, and the clean/dirty pin carries the every-listed-reviewer-returned conjunct. `git grep -n "no reviewer finished" -- claude-skills docs/design-decisions/round-cap-consult-rearm.md` prints nothing. `git grep -n -e "fresh run" -e "halts this run" -- claude-skills/skills/ready-for-review/SKILL.md` prints nothing. The re-plan, step-4, clean/dirty and loop-back pins match the new text, and no pin names the Cap.
3. This command prints nothing: ``git grep -n -F -e "**Cap.**" -e "Cap's pass accounting" -e "within the cap below" -e "the Cap" -e "loop cap" -e "non-rearming" -e "does not re-arm" -e "Fires once per branch" -e "gone permanently silent" -e "Skips recording entirely once the latch" -e '/ready-for-review` active-bypass' -- claude claude-skills README.md docs/hooks.md docs/scripts.md``. Decision-file bodies under their supersession notes and past `CHANGELOG.md` entries are records and keep their wording.
4. `/agent-review` on `plan-architect.md` and `/skill-review` on `ready-for-review/SKILL.md` come back clean.

## Out of scope

- **Retiring the disposition record, or folding it into the ledger.** The re-review carry-forward still reads it (row 36), and `branch-scoped-review-ledger.md` owns the fold-in follow-up.
- **Changing what a re-plan verdict routes to.** The engineer chose the round-cap section's routing for every firing (row 47). The fix-plan pilot's `foundation: wrong` stop-and-ask at `code-review/SKILL.md:386` stays as it is.
- **A per-round architect review of dispositions (#1187).** #1211 keeps it deferred pending measurement. This plan adds no per-round consult, and the fix-plan pilot (#1213) stays the per-round route.
- **A config key for the interval.** A constant suffices until `review-trace` data argues for another value.
- **Telling narrow consults apart from foundation consults when restarting the count.** That would need a prompt convention parsed in two runtimes; the residual is stated under Approach.
- **Recording consult verdicts durably.** No ladder rule needs them, and a record that a later consult rule reads is the cross-consult bookkeeping row 35 rejects. Counting the `architect-consult: <verdict>` tag in transcripts, so the interval's first revisit condition can be evaluated, is a possible follow-up.
- **A hook-observed precondition on `marker.sh write cumulative-review`.** Requiring `log-reviewer-round.sh`'s record of a reviewer spawn at the reviewed state would check the session's dirty classification independently. This change reuses the existing classification instead (row 65), so it stays a possible follow-up.
- **Retitling the `round_consult_gate` schema row.** Its "Round-3 architect-consult gate" label still names the first firing. A retitle would bring `config-keys.psv` under hook review and a `config-schema-audit.md` sync for a label alone. `docs/hooks.md` states the switch's wider reach instead.
- **Counting `/plan-review` fan-outs.** Their exit keeps its measured rationale.
- **Editing earlier plan files** such as `.claude/plans/round3-review-consult-trigger.md` and `.claude/plans/round3-gate-executable-plan.md`. They are records, and the supersession lives in the decision files.
- **Hook-driven ledger appends.** The session numbers each `/code-review` round itself and appends its rows after dispositioning it (`claude-skills/skills/code-review/SKILL.md:25`), so a round's rows can be missing or late. This plan keeps that contract. A ledger row carries the session's disposition, which no hook has at reviewer spawn, and the gate's state file already counts reviewed states there (row 10).
- **Keying round state by branch alone.** The round-state key and the ledger's branch key both hash the worktree root (`claude/.claude/hooks/_lib.sh:4530-4550,4919-4924`), so one branch checked out at two paths counts as two branches. Re-keying would orphan every existing state file, latch and branch-scoped ledger, and the split already applies to today's first firing, so it is a change of its own.
- **Growing `code-review/SKILL.md` or its line cap.** No part of this design needs text there (M9). #1351 owns restoring the file's 500-line cap from the temporary 525 (`CHANGELOG.md`, Unreleased).
