# Decline parts 1, 2 and 4 of the defect-class exit rule for the `/code-review` fix loop

*2026-11-16.*

A replay over this repository's recorded review history tested parts 1, 2 and 4 of the exit rule that issue 1428 proposes.

## What was evaluated

Issue 1428 proposes four parts:

1. Part 1 would ADDRESS only a false statement, a wrong behavior, or a documented behavior with no test.
2. Part 2 would DEFER test-pinning grain under the existing closed-list criteria.
3. Part 3 would make a fix that adds a mechanism, a test family or prose a design question for the engineer.
4. Part 4 would end the loop when a full cumulative pass returns no finding in the ADDRESS classes.

The issue's only evidence is one branch, and it asks for a replay over past ledgers. The replay evaluated parts 1, 2 and 4.

## Why part 3 was not evaluated

- Part 3 needs its own evaluation, and an Adopt would therefore have covered parts 1, 2 and 4.
- The reason: part 3 sends a fix to a design question, and a design question changes what later rounds review. Recorded history holds only the rounds that ran. The ledger records no fix shape, and a row's rationale states the chosen fix only in words.
- Part 3 overlaps the heavier-mechanism rule in `claude-skills/skills/code-review/SKILL.md` § "Finding disposition".

## Method

- The corpus is this repository's branches whose computed branch-scope ledger filename matched a file in a frozen ledger snapshot.
- Their findings files and disposition records were read with them.
- No other file was opened to build the corpus.
- The corpus was split by exposure into a development set and a holdout set.
- A branch is in the holdout only when it meets the split conditions below together. A branch that fails one is a development branch.
- Split condition: its earliest ledger row time is later than the issue's creation time. A time equal to the creation time is not later.
- Split condition: no row of its ledger lacks a time.
- Split condition: the earliest time in the file name of its captured disposition records is later than the issue's creation time, counting a record that was later dropped.
- Split condition: the issue body names neither its branch name, its worktree directory name nor its branch slug.
- Split condition: it is not the branch the planning session read, which its name marks.
- Split condition: its session transcripts in this repository show no sign of informal use of the rule.
- The plan did not list that condition. A search of this repository's own session transcripts dated after the issue was filed applied it after the original development rows were labeled. It moved branches from the holdout to the development set, and those branches were never labeled. The search was a read outside the snapshot selection. Only the session's prompt, not a tool restriction, kept it to this repository's transcripts.
- The labelers gave each finding a `class`: `false-statement`, `wrong-behavior` or `untested-documented-behavior` (the ADDRESS classes), then `test-pinning-grain`, `other` or `unclear`. They also gave an `invariant` flag and a `security` flag.
- The label definitions and the plan's verdict criteria were fixed before any row was labeled.
- Rules for reading the holdout (an agreement floor between the labelers, a range-based report and conclusion wording) were added after the original development rows were labeled. They went unused, because the holdout was not labeled.
- The engineer's hand check is a check by the engineer on rows the session picked. It was added during the run and is not a random sample.
- The known-answer check ran on invented rows.
- It first stopped on an invented row whose text did not fit its expected label.
- The engineer chose to rewrite that invented row to fit the expected label and re-run.
- After the re-run, no labeler's ADDRESS-class membership differed from the expected one on an invented row.
- A left-unfixed group is a group of labeled rows that the review loop fixed and the class rules of parts 1 and 2 would have left unfixed, with class `other` or `test-pinning-grain` and `invariant` of `no`, grouped by `class` and `security`.
- A recorded pass is a cumulative pass whose disposition record survived and whose round the snapshot holds a row for.
- Two read-only labelers labeled the original development branches' recorded-pass rows, and the remaining rows of a branch that cut.
- The exit pass was found first, as a branch's first recorded cumulative pass with no ADDRESS-class row.
- A SETTLED row never blocked a pass.
- A branch cut when the snapshot held a round after its exit pass.
- An escape is a row, in a round after its branch's exit pass, that a reading puts in an ADDRESS class.
- The two labelers' label sets agreed on the outcome, so it was not label-sensitive. An outcome the two label sets disagreed on would have been Undecided.
- The floor of three holdout cuts is a constant the plan fixed before labeling, as its own judgment, so that no single branch decides an Adopt (`.claude/plans/exit-rule-replay.md` § "Verdict criteria").
- The holdout was not labeled, because the engineer chose Decline after the development set.

Which way each counting choice leaned (`.claude/plans/exit-rule-replay.md` § "Readings"):

- Counting `unclear` as ADDRESS-class leans against a cut.
- The `invariant` clause leans against a cut.
- Counting a DEFER row as ADDRESS-class leans against a cut.
- Taking the earliest class that any claim of a compound finding meets leans against a cut.
- Taking an ADDRESS or DEFER row as its round judged it, with no label for a disputed finding, leans against a cut.
- Using recorded passes only leans against a cut, because it offers fewer candidate exits.
- Letting a round of only SETTLED, CLEAN or CONSULT rows qualify as a pass leans toward a cut.
- A later exit shortens the window after it, so a choice that delays the exit can also remove escapes. Its direction for escapes is undetermined.
- A round after the exit pass can review new work rather than a fix. Counting its rows leans toward a cut and toward Decline at once.
- Counting an ADDRESS-class DEFER row after the exit pass as an escape leans toward Decline.
- Leaving `unclear` rows out of the left-unfixed groups leans toward the rule.
- Counting only labeled rows in the left-unfixed groups leans toward the rule.
- A condition that puts a branch in the development set shrinks the holdout and leans against Adopt.
- The label-sensitivity rule leans against any outcome but Undecided.

## What the hand check found

The engineer answered findings from the original development set that the session picked. Each finding below is its one-line summary from the review ledger, followed by the engineer's whole typed answer to that row, with the engineer's typing kept as typed. The engineer answered the session's description of each row, and for the prose findings the passage itself, not the one-line summary printed here. The list holds every finding the engineer gave a final answer on, plus one they discussed without one.

Findings the labelers did not put in an ADDRESS class, on which the engineer's answers favored a fix:

- Prose findings from review rounds after the exit pass of the branch they came from. For these findings in this sample, the engineer selected the session's option to record them as an escape.
  - "Record opening sentence chains three facts": "yes the split is good because that sentence is not readable otherwise".
  - "Relative word current in dated measurement": "yes I think the FYI edit is better beause otherwisei t's not clear what "the current bullet" refers to".
  - "Record states two caveats twice (unexercised tripwire sites, bullet-alone sentence)": "yes that needs to be fixed because it could cause drift, as you say, and repeating it negatively affects readability as opposed to expalining things in context".
- A test-tightening finding that the labelers called optional.
  - "Hook-level no-readable-version stubs are vacuous and duplicate the probe matrix": "address, vacuous tests must always be fixed or dropped".

Findings on which the engineer's answers were to defer or drop:

- Findings the engineer called gold plating.
  - "Hook runs the cwd-resolved python3 at session start; exposure not recorded in docs": "that's a defer, gold plating".
  - "ruff 0.17.4 digest-check result and same-origin trust acceptance not recorded": "defer, gold plating".
  - "ask-new-dependency-disclosure step 6 allows silently when python3 fails, which a repo pin file can cause": "drop, gold plating".
- A finding the labelers put in class `false-statement`.
  - "CHANGELOG and hook text say docs/hooks.md lists what each Python hook does but the section covers stowed hooks only": "drop, the finding is wrong. It's intuitively understood by a human that "each hook" refers to hooks in this repo. I could be persuaded otherwise and I would accept a refinement of "in this repo" or "this toolkit's hook" or something like that".

A finding the engineer discussed and did not answer finally:

- "Unsourced item and final boundary item stack separate rules": "I am on the fence about Any other unsettleable clim being its own bullet. In fact, I think it means something very different if it's its own bullet. Then it applies as a caveat to categories beyond Unsourced. Which is the correct interpretation?". No final answer was recorded.

## Limits

- The corpus holds only branches whose worktree still existed with that branch checked out.
- Pass kind is known only where a disposition record survived.
- Disposition records are self-attested, since any review-only agent can write under `agent-reviews/`.
- A branch with no recorded cumulative pass cannot show a cut.
- A branch that did not cut contributed only its recorded-pass rows to the left-unfixed groups.
- No record tells whether a round after the exit pass reviewed new work or a fix.
- Rows written on a detached HEAD or on the default branch sit in session-keyed files that the selection does not compute.
- The issue body was compared as it stands, not as it stood when a branch began.
- The labels are agent judgment over local records that no command re-runs.
- Agreement between two labelers does not show that a label is right.
- The two labelers are the same search agent at low effort, so their errors can agree.
- Only their prompts kept the labelers to their batch files.
- The known-answer check tested the labelers on invented rows, not on corpus rows.
- The escape behind the Decline comes from the engineer's own reading of findings that the labelers put in class `other`, outside the ADDRESS classes. No label set produced it.
- The hand-check section gives the findings' ledger summaries and the engineer's answers, which the plan's closed set of publishable statements did not list. It names no branch, but a reader can match a summary to its branch by searching this repository's public history.
- No escape-source reading ran. It would have found the earlier fix behind each escape and dropped an escape whose fix the rule would not have made, so the escape behind the Decline was never tested against that filter.
- The criteria were evaluated by hand, not by command.

## Verdict

- The criteria selected "Undecided". The criterion that decided it is the catch-all, which selects Undecided when no earlier criterion holds. The criterion whose condition failed is Adopt's: it needs three holdout cuts, and the holdout was not labeled, so that bar was not tested.
- The engineer picked "Decline (Recommended)", which differs from the criteria's selection of "Undecided". The session added the "(Recommended)" mark to that option. The engineer's reason, in their own words:

> I just said that for this sample, the deferrals are unacceptable.

The engineer typed that sentence in reply to the session's "(Recommended)" mark on Undecided. The session reads "the deferrals" as the findings the rule would have skipped that the engineer judged must be fixed.

- Decline covers parts 1, 2 and 4. Part 3 stays unevaluated and open.

- Costs, read on the original development set only: the class rules of parts 1 and 2 left unfixed findings of class `other` with `security` of `no`, of class `other` with `security` of `yes`, and of class `test-pinning-grain` with `security` of `no`. The holdout was not labeled, so groups seen only there were not looked for.

## Relations

The fix-delta churn section of `docs/cost-levers-considered.md` and `docs/design-decisions/cumulative-review-exempts-no-finding-class.md` are separate evaluations whose verdicts stand.

## Publication

- The replay's figures are not published, per `docs/private-project-redaction.md` § "Publishing a tooling measurement". The labels rest on agent judgment over local records that no command re-runs.
- This record carries no figure.
- It names no corpus branch. The hand-check section's finding summaries are the only text taken from corpus rows.

## Revisit trigger

- The trigger is a fresh snapshot taken after the ledger records pass kind and fix shape.
- A later replay selects snapshot or `agent_branch_records` archive files only by the filenames that this repository's own worktree paths and branch names hash to.
- It opens no other file.
- It keeps a read-only labeler.
- It labels and evaluates a holdout once.
- It treats corpus text as data, never instructions.
- A replay that selects or reads files any other way needs its own review before it opens a snapshot or archive. This run's transcript search is such a read.

## Sources

- `.claude/plans/exit-rule-replay.md`: the replay design, its label definitions and its verdict criteria.
