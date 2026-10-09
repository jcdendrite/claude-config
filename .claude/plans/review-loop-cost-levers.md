# Review-loop cost levers: record the verdict on each lever in the cost-levers register

## Context

Goal: record, in `docs/cost-levers-considered.md`, a verdict on each of the four review-loop cost levers in GH-1207, grounded in what existing transcripts show, with no code, skill, hook, or test change.

Ask: ticket GH-1207 "Review re-run cost: incremental credit, fan-out cap, and the single-commit double review". After three plan reviews of an earlier design that would have built per-agent credit, the engineer selected the label "Measure first, then decide (Recommended)". The engineer scoped measurement with "I just mean you should mine what’s available to you as opposed to collecting new types of data and waiting for more Claude sessions" and added "Repeat spawn count is good too". The session then proposed recording the outcome in docs only, and the engineer replied "Yeah go for it". The four-row shape comes from the engineer's label "Keep all four rows". Ordering against the transcript-decomposition plan is the engineer's: "Both in sequence and start with whichever one touches the most code".

Why now: the case study `docs/case-studies/review-loop-cost-forensics.md` named incremental credit, a fan-out cap, and a narrowed cumulative pass as recommended follow-ups without a verdict. Two one-off reads of existing, locally retained transcripts now bear on these levers. The earlier design's reviewers also found it expensive to build safely, and those findings live only in gitignored `agent-reviews/` files.

## Approach

Add one new section at the end of `docs/cost-levers-considered.md` covering GH-1207's four levers. It has four rows and adopts none of them. Each row rests on what two scans of existing transcripts showed, written as direction words with no figure. Nothing else changes: no code, skill, hook, test, or design record. This replaces the prior lane-credit draft entirely.

**Verdicts.**
- **(a) Incremental per-agent review credit: not built (measured).** The scans show that repeats after a returned finding outnumbered repeats after a zero-finding return (rows 9–11). Building it safely is also expensive (rows 22–25).
- **(b) Narrowing `ready-for-review`'s cumulative pass to the increment since the last clean review: not pursued (measured).** On the dirty re-passes measured, findings placeable outside the fix delta outnumbered those inside it, and passes whose findings all sat outside the delta outnumbered passes whose findings all sat inside it (row 13). The pass stays unnarrowed (row 21).
- **(c) Fan-out cap: rejected, standing verdict.** The existing row at `:27` stands, and nothing measured here bears on it (row 27).
- **(d) GH-1057: rejected (falsified), reinforced.** The `--amend` falsification at `:26` stands. A further reason applies: the two passes run different reviewer sets, and the code-review marker records neither set (rows 16, 26). GH-1057 is closed, folded into GH-1207 (row 28), so the register uses its own verdict vocabulary and does not relabel the issue.

**Checking the session's reading.** The reading holds, with one addition and one confound.
- **Addition:** the incremental-credit verdict still holds if every unclassified verdict was zero-finding (row 11).
- **Confound:** `comment-discipline-reviewer`'s re-pass findings are partly a first read by construction. It never spawns in a staged-diff round, so only its outside-delta findings say anything about reviewer variance (row 17).
- **Relayed cycle:** the session relays a cycle as the engineer's proposal. It matches today's loop whenever a fix lands as one commit (row 20). The register records it under the narrowed-pass row rather than as a fifth lever, which keeps "Keep all four rows" intact.

**Placement: the register alone, with no design record.**
- The register is the canonical home for a cost-lever verdict (row 32). Nothing in the system changes, so there is no architecture for a design record to describe. A design record would restate the same evidence at a second site.
- Two register sections already carry a measured decline verdict and its reasoning inline, with no design record: `reviewer-instance-continuation.md` and `subagent-idle-gap-cache-rebuild-split.md`.
- The reviewer findings that make incremental credit expensive to build live only in gitignored `agent-reviews/` (row 35). The new section's reopening list becomes their one durable home.
- No pointer goes into the case study. It is a preserved record (CLAUDE.md Axis 3), and its Sources already link the register (row 34).
- No pointer goes into `ready-for-review-fix-loop-convergence.md`. The new row links to it, this change does not supersede it, and a one-way link is enough.
- This drops the design record that the session's option description proposed (row 2).

**Publication: direction words only.**
- The scans ran as ad hoc scripts. No subcommand that refuses a wider corpus computes these readings (rows 29–30). Under G3 the section therefore publishes no count, percentage, ratio, range, or share-like descriptor. It records ordinal comparisons only ("A outnumbered B"), plus the method and its limits (row 31).
- The engineer has ruled on no descriptor for this record, so words such as "most", "uncommon", "small minority", "substantial share", and "many" stay out of the section and the plan's ledger.
- The section states that the scans were one-off and cannot be rerun as a script, so a reader does not infer a rerunnable instrument.
- The same bar binds:
  - this plan file, its Context included;
  - the `code-writer` dispatch prompt;
  - the commit message;
  - the PR body.
- Publishing any figure would need the owner's per-figure authorization, which this plan does not seek.

**No engineer decision blocks implementation.** Two consequences are worth naming to them:
- The design record is dropped (row 2).
- Under their own ordering rule, this docs-only plan now goes after the transcript-decomposition plan (row 37).

### Assumption ledger

- **Root:** GH-1207 asks for a verdict on four review-loop cost levers. The register carries standing verdicts for the fan-out cap and the single-commit skip, and none for incremental credit or a narrowed cumulative pass. This plan records all four from existing transcripts, with no figures, and changes no mechanism.
- **Givens:**
  - **G1.** Reviewer agents are nondeterministic, so a re-spawn on unchanged bytes can return a finding that an earlier spawn did not. *Reason:* this is model behavior, imposed by the vendor.
  - **G2.** Transcripts self-delete on the vendor's default retention window, so any reading of existing data covers a rolling window that keeps shrinking (`docs/case-studies/review-loop-cost-forensics.md:5`). *Reason:* the default is vendor-set.
  - **G3.** `docs/private-project-redaction.md` § "Publishing a tooling measurement" bars publishing any figure from an instrument that does not refuse a wider corpus. *Reason:* it is a repo-wide rule. Relaxing it, or authorizing a figure, is the owner's decision and lies outside this plan.
- **Rows:**
  1. `[engineer-verified: "Measure first, then decide (Recommended)"]` The label the engineer selected for how to proceed after the three plan reviews. It covers measuring before deciding, and nothing more.
  2. `[unverified]` That option's description was the session's proposal, not the engineer's words. It read: re-scope to measuring with existing transcript tooling, add register rows and a design record, and build lane credit only if the measured rate justifies it. This plan drops the design record (Approach, Placement).
  3. `[engineer-verified: "Yeah go for it"]` The engineer's reply to the session's docs-only re-scope. It covers building no code and changing no skill.
  4. `[engineer-verified: "I just mean you should mine what’s available to you as opposed to collecting new types of data and waiting for more Claude sessions"]` Measurement uses existing data only.
  5. `[engineer-verified: "Repeat spawn count is good too"]` A repeat-spawn reading is an acceptable measure for the incremental-credit lever. The quote covers no threshold.
  6. `[engineer-verified: "Keep all four rows"]` The new section keeps one row each for incremental credit, a narrowed cumulative pass, a fan-out cap, and GH-1057.
  7. `[unverified]` A session-drafted change. The incremental-credit row's verdict now reads "Not built (measured)" instead of the prior draft's "adopted". It follows from rows 1, 3, and 9–11. It widens nothing beyond row 6: the four rows stay, and only that row's verdict changes.
  8. `[engineer-verified: "Both in sequence and start with whichever one touches the most code"]` This orders this plan against the transcript-decomposition plan only. The engineer confirmed it this session with the label "Yes, I said that".
  9. `[unverified]` Findings of the staged-diff scan, which was ad hoc and not re-run by the session. They are ordinal:
     - Same-agent repeat spawns on one commit that followed a returned finding outnumbered those that followed a zero-finding return.
     - Among repeat pairs whose diffs could be compared, changed file chunks outnumbered unchanged ones.
  10. `[unverified]` Limits of the same scan:
      - Some staged spawns have no classifiable verdict, because the verdict lived only in a since-deleted `agent-reviews/` findings file.
      - Diff text could not be recovered for every spawn.
  11. `[unverified — inference from rows 9–10]` The incremental-credit verdict survives row 10's gap. Suppose every unclassified repeat had followed a zero-finding return. Credit would still need the agent's whole lane unchanged, and changed file chunks outnumbered unchanged ones. The population credit could reach is bounded by that.
  12. `[unverified]` Limits of the cumulative re-pass scan, which was ad hoc and not re-run:
      - Some second-or-later passes could not be reconstructed because their marker diff file had already been garbage-collected, so the sample is non-random.
      - "Dirty" means at least one finding, not an addressed one.
      - Delta placement is a heuristic token match.
  13. `[unverified]` Findings of the same scan. They are ordinal:
      - Among findings that could be placed, those outside the fix delta outnumbered those inside it. Some findings could not be placed.
      - Passes whose findings all sat outside the delta outnumbered passes whose findings all sat inside it.
      - Only dirty re-passes were analysed. The scan therefore prices what narrowing would lose, not what it would save on clean re-passes.
  14. `[unverified]` Existing data cannot show whether an agent finds something new on bytes it already cleared (rows 10, 12, 15).
  15. `[verified: claude/.claude/hooks/log-reviewer-round.sh:12-15,107-119]` The only dedicated per-spawn record of staged-diff hashes is the round-state log. It holds a capped, deduplicated list of `<head-sha> <staged-diff-sha256>` entries per branch, swept after 30 days. It records no agent type and no lane. Transcripts also hold per-spawn text, but not the bytes handed over in a reliably recoverable form.
  16. `[verified: claude-skills/skills/code-review/SKILL.md:52]` Step 0.6 defers the comment/durable-doc-prose row out of every staged-diff round. The cumulative pass is therefore that row's only exhaustive pass.
  17. `[unverified — inference from rows 13, 16]` When `comment-discipline-reviewer` raises a finding inside a fix delta, it is reading that prose for the first time; it has not missed it before. Only its findings outside the delta say anything about variance. The scans did not split the two.
  18. `[verified: claude-skills/skills/ready-for-review/SKILL.md:15,82]` The fix loop runs as follows:
      - Step 3 dispatches one `code-writer` covering every ADDRESS row.
      - The fix commit goes through the staged-diff `/code-review` and marker gate.
      - The loop then returns to step 2 and a full step-3 pass.
  19. `[verified: docs/cost-levers-considered.md:26]` `require-code-review.sh` hashes the staged increment at every commit. A review between passes therefore cannot replace the per-commit gate; it can only coincide with it or add to it.
  20. `[unverified]` The session relays a cycle as the engineer's proposal: "cumulative → code-writer → delta review since the baseline until clean → next cumulative". It is not quoted from the engineer. Per rows 18–19:
      - It is today's loop when the fix lands as one commit.
      - It adds a review when a fix spans several commits.
      - It saves cost only if it narrows the next cumulative pass, which is the narrowed-pass lever.
  21. `[verified: claude-skills/skills/ready-for-review/SKILL.md:78-80; docs/design-decisions/ready-for-review-fix-loop-convergence.md:5]` The cumulative pass is unnarrowed under a pinned scope rule. The fix-loop decision states that nothing in it narrows that pass.
  22. `[verified: claude/.claude/hooks/require-ready-for-review.sh:5,52-53; docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md:9-12,21]` The cumulative-pass gate covers `gh pr create`, `gh pr ready`, and pushes to a branch with an open PR. It does not cover the first push of a branch with no open PR.
  23. `[verified: claude-skills/skills/code-review/SKILL.md:38-41]` Outside the staged-diff boundary, a spawned row keeps its directed causal-reach instruction and the causal-reach clause. Its verdict can therefore depend on files beyond its own lane.
  24. `[verified: claude/.claude/agents/skill-fidelity-reviewer.md:116-121]` The fidelity check accepts a spawn of the required type anywhere on the branch, so it cannot see a skipped re-spawn.
  25. `[verified: docs/design-decisions/ready-for-review-fix-loop-convergence.md:94-96]` `code-review/SKILL.md` has no line headroom under the repo's line-cap ratchet. The section links that decision and never restates the cap's number.
  26. `[verified: claude/.claude/scripts/marker.sh:414,429-431]` The code-review marker stores one staged-diff hash per repo and session. It records nothing about which reviewers ran.
  27. `[verified: docs/cost-levers-considered.md:26-27]` Two standing rows: the single-commit cumulative skip is rejected (falsified by `--amend`), and a fan-out cap is rejected.
  28. `[verified: gh issue view 1207, read by the session this session; GH-1057, GH-1196, GH-1197, GH-415 as relayed by an exploration subagent from gh issue view]` GH-1207 is open and folds GH-1196 (incremental credit), GH-1197 (fan-out cap plus a narrowed cumulative pass), GH-1057 (a live code-review marker satisfying the cumulative pass on a single-commit branch), and GH-415 (a lighter iteration-push path). All four folded issues are closed with a pointer to GH-1207. The session did not read GH-1057's own body. The four rows follow the engineer's "Keep all four rows", read against the option text the session showed ((a) credit, (b) narrowed pass, (c) fan-out cap, (d) GH-1057). That mapping is the session's.
  29. `[verified: docs/private-project-redaction.md:122-123,164-171; docs/transcript-analysis.md:1440-1441]` The publication rule:
      - A subcommand with no scope refusal is not a publication instrument.
      - A read from a non-instrument publishes no total, rate, share, count, or range. It records only which lever was decided and which way the reading pointed.
      - `cost-counts` refuses any corpus-wide count, so it cannot publish a repeat-spawn figure either.
  30. `[unverified]` No existing `transcript-analysis.py` subcommand computes byte identity between repeat spawns or the delta placement of re-pass findings. This is inferred from the session's report that the scans were ad hoc; the full subcommand list was not surveyed.
  31. `[unverified]` Share-like words such as "most", "uncommon", and "small minority" are not allowed here. The case study's owner rulings on coarse descriptors covered a read of a private branch, so they do not decide this case, and the engineer has ruled on none. Ordinal comparisons ("A outnumbered B") record which way a reading pointed without bounding a share, so the section uses only those.
  32. `[verified: docs/cost-levers-considered.md:3-17]` The register keeps a verdict plus the measured reason for each lever. Each section names its source plan, and the register indexes plans rather than restating them.
  33. `[verified: .claude/rules/design-decisions.md:9-39]` Design records are one file per decision. They cite rather than restate, and there is no index.
  34. `[verified: docs/case-studies/review-loop-cost-forensics.md:116-121,152]` The case study names incremental credit, a fan-out cap, and a narrowed cumulative pass as follow-ups. Its Sources already link the register.
  35. `[verified: claude-skills/skills/ready-for-review/SKILL.md:100]` `findings-path-suffix.sh` adds `agent-reviews/` to the repo's ignore list, so reviewer findings files are never committed.
  36. `[verified: claude-skills/skills/tests/test_skills.py:3676-3703,6164-6172; claude/.claude/hooks/tests/test_design_decision_files.py:471-483; claude/.claude/scripts/select-tests.py:267-275]` The tests that touch this change:
      - The register's citation of the redaction heading must resolve.
      - No doc may hold a literal per-account state path.
      - The relative-link test covers only `docs/design-decisions/`.
      - `select-tests.py` maps every `docs/` path through one blanket rule.
  37. `[unverified — inference from row 8]` This plan now touches no code, so the engineer's ordering rule puts the transcript-decomposition plan first. That plan's file count was not read.
  38. `[verified: claude-skills/skills/subagent-delegation/SKILL.md:156-158,174-177]` By default, an approved plan's implementation goes to `code-writer`, one dispatch per phase. The not-code carve-out names a deferred-findings block, a `respond-pr` reply, and a plan-file edit. It does not name a durable doc.
- **Mechanisms:** none. The plan adds no hook, script, skill clause, or test, so no over-powered-primitive check applies (anchors: root, row3).

## Critical files

**`docs/cost-levers-considered.md`** (modify). Append one section after the `pr-cost-forensics.md` section, which is currently the file's last. The section contains, in order:

1. **Heading, exact string, with no date:** ``## From `review-loop-cost-levers.md` — "Review-loop cost levers: record the verdict on each lever in the cost-levers register"``. The quoted title is this plan's own H1, as `pr-cost-forensics.md`'s heading quotes its plan.
2. **Intro paragraph**, carrying:
   - GH-1207 asked for a verdict on four levers. `docs/case-studies/review-loop-cost-forensics.md` § "Recommended, not implemented here" named three of them.
   - **Method:** two one-off scans of existing locally retained transcripts, collecting no new data. The scans cannot be rerun as a script. One covered repeat reviewer spawns across staged-diff `/code-review` rounds on one commit. The other covered `ready-for-review` cumulative re-passes after a fix. The section names no account, root count, or time window.
   - **Publication:** the scans are ad hoc, not a scope-refusing subcommand. Per `docs/private-project-redaction.md` § "Publishing a tooling measurement", this section names no figure from them and no share-like descriptor, only ordinal comparisons of which way each reading pointed. Write the citation on one line, with ASCII quotes.
   - **One defining sentence for `<config-dir>`:** it is the active `CLAUDE_CONFIG_DIR`, else the default Claude config directory, written with no home-rooted literal path.
   - **Limits, as a list:**
     - No record reliably keeps the bytes each spawn was handed. `<config-dir>/.reviewer-round-state.d/` keeps only a capped list of whole-staged-diff hashes per branch, with no agent or lane.
     - Some staged spawns' verdicts lived only in `agent-reviews/` findings files that no longer exist.
     - Diff text could not be recovered for every spawn.
     - Some cumulative re-passes could not be reconstructed because their `<config-dir>/cumulative-review-diff-markers/` file had already been garbage-collected, so that sample is non-random.
     - "Dirty" means at least one finding, not an addressed one, and only dirty re-passes were analysed.
     - Placing a finding inside or outside a fix delta is a heuristic token match, and some findings had no usable location.
3. **Table** `| Lever | Verdict | Measured reason |`, exactly four rows. A table row cannot hold a newline, so each Reason cell lists its items with `<br>- `, as the register's existing rows do:
   - **Incremental per-agent review credit for repeat staged-diff `/code-review` rounds** (skip re-spawning an agent whose files are byte-identical to files it already cleared with zero findings).
     - **Verdict:** Not built (measured).
     - **Reason:**
       - Repeat spawns that followed a returned finding outnumbered those that followed a zero-finding return.
       - The verdict holds even if every unclassified verdict were zero-finding, because changed file chunks outnumbered unchanged ones.
       - It ends by pointing to the reopening list below.
   - **Narrowing `ready-for-review`'s cumulative pass to the increment since the last clean review.**
     - **Verdict:** Not pursued (measured).
     - **Reason:**
       - Among placeable findings on dirty re-passes, those outside the fix delta outnumbered those inside it.
       - Passes whose findings all sat outside the delta outnumbered passes whose findings all sat inside it, so a narrowed pass would have missed findings the full pass raised.
       - Clean re-passes, where narrowing would save cost without loss, were not measured.
       - The pass stays unnarrowed per `claude-skills/skills/ready-for-review/SKILL.md` § "3. Code review (halt on findings)".
   - **Capping reviewer fan-out.**
     - **Verdict:** Rejected, standing verdict.
     - **Reason:** cite the `token-spend-reduction.md` section's "Capping reviewer-ownership fan-out in `/code-review`" row by name, and state that nothing measured here bears on it.
   - **Letting a live staged-diff code-review marker satisfy `ready-for-review`'s cumulative pass on a single-commit branch (GH-1057).**
     - **Verdict:** Rejected (falsified), reinforced. It uses the register's own vocabulary, and GH-1057 is closed, folded into GH-1207.
     - **Reason:**
       - Cite the same section's "Skipping cumulative `/code-review` for single-commit PRs" row by name.
       - Add the further reason. `claude-skills/skills/code-review/SKILL.md` § "Step 0.6 — Pre-judgment table check" defers the comment/durable-doc-prose row out of every staged-diff round, so the cumulative pass is that row's only exhaustive pass. The marker stores one staged-diff hash, with no record of which reviewers ran.

Every citation in the section takes the form `` `repo-root-relative/path.md` § "Heading" `` on one line, with ASCII quotes. The section uses no link-plus-`§` form, and a relative link, if one is wanted, is a separate construct.
4. **"What the scans could not settle" paragraph**, carrying:
   - **The open question:** is re-run cost driven by incomplete first passes or by reviewer variance? Existing data cannot separate a missed finding from a variance draw, for the reasons in the limits list.
   - **The `comment-discipline-reviewer` confound:** its findings inside a fix delta are a first read, so only its outside-delta findings say anything about variance.
   - **The relayed cycle:** a delta review since the last cumulative pass, run until clean, is today's loop when a fix lands as one commit. Step 3 dispatches one `code-writer`, whose fix commit already passes the staged-diff gate before the next full pass. That cycle would save cost only by narrowing the next pass, which is the second row.
5. **"Reopening incremental credit would need" list:**
   - A measurement of which spawns are creditable. That needs a per-spawn record of handed bytes and verdict, which nothing keeps today.
   - A credit key wider than the agent's own files, because directed causal-reach duties reach other lanes. Cite `claude-skills/skills/code-review/SKILL.md` § "Step 0.6 — Pre-judgment table check".
   - An audit record for each credited skip, because `skill-fidelity-reviewer` accepts a spawn anywhere on the branch.
   - An accurate backstop claim. The cumulative pass does not gate a first push with no open PR, per `docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md`. A credit design would widen that doc's accepted residual from one row to every credited row.
   - Line headroom in `code-review/SKILL.md`. Cite `docs/design-decisions/ready-for-review-fix-loop-convergence.md` § "The `code-review/SKILL.md` line cap".
6. **Verdict line:** no code, skill, hook, or test change ships from this investigation.

**The section must not contain:**
- plan-only labels, such as (a)–(d), scan names, or "lane credit" as a coined term;
- a restatement of the `:26`/`:27` rows, or a line-number citation into this file;
- any figure or share-like descriptor from either scan;
- a home-rooted state path (`~/.claude/<state-dir>`, `$HOME/...`), because `test_doc_has_no_state_path` bans those. `<config-dir>/...` stays allowed for state directories, and never for `scripts/` or `hooks/` paths, which must be literal.

**`.claude/plans/review-loop-cost-levers.md`** is this plan, committed per `plan-it` Step 7. Its Context must carry no scan figure.

**Dispatch split:** one `code-writer` dispatch (`model: sonnet`) for the register section.
- The prompt names this plan's path and Verification steps 1–4.
- The prompt carries no scan figure.
- There is no split, because only one file changes (row 38).

**Reuse rather than restate:**
- the two standing rows at `:26-27`;
- the comment-row deferral doc and the fix-loop convergence doc, for the backstop surface and the line cap;
- the case study's recommendation section.

## Verification

1. From the worktree root, run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
   - `docs/` maps through the blanket rule (row 36).
   - Three existing tests guard the new text: `test_every_citation_shaped_construct_is_extracted` (a no-space `§"` opener or a hard-wrapped heading fails), `test_doc_has_no_state_path`, and `test_doc_has_no_templated_stowed_path` (`<config-dir>/scripts/` or `/hooks/` fails).
   - Two things are manual only. No test resolves a `§ "Heading"` citation in `docs/` against its target, and no test checks links inside the register. Steps 3 and 4 cover them.
   - `test_tooling_measurement_citation_resolves_to_real_heading` already passes on an existing citation and cannot fail from this change, so it is not a guard here.
   - Ruff and ShellCheck are not run, because no `.py` or `.sh` file changes.
2. **Redaction screen, a complement shape, before commit and again before the PR.** List every added line of the new section with this command, then do the same for the plan's Context, the commit message (`git log -1 --format=%B`), and the PR body text before `gh pr create`:
   - `git diff origin/main -- docs/cost-levers-considered.md | grep -E '^\+' | grep -nE '[0-9]|\b(one|two|three|four|five|six|seven|eight|nine|ten|dozen|half|third|quarter|percent|ratio|most|many|few|minority|majority|substantial|uncommon|rare|all|every|none|only|each|any|never|outnumber[a-z]*|exceed[a-z]*)\b|out of'`
   - On the plan's Context, where row numbers and line cites would match every line, drop the `[0-9]|` alternative and read each remaining hit.
   - Every hit must be justified: a ticket ID (GH-1207, GH-1057), the cited heading text "Step 0.6" or "3. Code review (halt on findings)", or the "four" in the rows count.
   - In every screened artifact (the section, the plan's Context, Approach, and ledger, the commit message, the PR body), a hit is also justified when it is a plan-structure word (a row number, a count of plan items or reviews, a quoted engineer sentence), existence-only limit wording ("some" X could not be Y, a negated or hypothetical "every", "only" describing a record's format or the method), or a verdict-bearing "outnumbered" ordinal that states which way a reading pointed. A hit fails when it states a magnitude, or orders how much of the data was covered against how much was not. A hit fitting none of these fails.
   - Run the screen over the Approach and ledger as well as the Context, and check that every "rows N–M" cite still points at a row that carries the claim.
   - The pattern is a floor, not the rule. It matches shapes such as `78/216`, `1 in 4`, `3:1`, `six percent`, and `half`, but not every comparative. Read every comparative or ordinal sentence in each screened artifact against the allow rule, whether or not the pattern matched.
3. **Citations and paths by hand.** Neither is test-resolved here (step 1).
   - Run `ls` on every repo-root-relative path the section cites. Each must succeed. The paths are `docs/case-studies/review-loop-cost-forensics.md`, `docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md`, `docs/design-decisions/ready-for-review-fix-loop-convergence.md`, `docs/private-project-redaction.md`, `claude-skills/skills/code-review/SKILL.md`, and `claude-skills/skills/ready-for-review/SKILL.md`. The two standing rows live in the register itself, under a section named for its source plan, so check them by `grep -n` in `docs/cost-levers-considered.md`.
   - Each `` `path` § "Heading" `` in the new section must match a heading line in its target (`grep -n '^#' <target>`, with backticks stripped from both sides), on one line, with ASCII quotes, per `.claude/rules/citation-grammar.md`.
4. **Read the new section.** Check mechanically that:
   - the table has exactly four rows (count the table rows between the new heading and the end of the file);
   - both standing rows are cited by name, verbatim: "Capping reviewer-ownership fan-out in `/code-review`" and "Skipping cumulative `/code-review` for single-commit PRs";
   - none of `(a)`, `(b)`, `(c)`, `(d)`, `lane credit`, or `:26` or `:27` appears;
   - the heading is exactly the string in Critical files item 1;
   - `<config-dir>` is defined once, in a sentence with no home-rooted path.
5. Run `/code-review` on the staged diff before commit.
   - Its comment/durable-doc-prose row is deferred to `/ready-for-review`'s cumulative pass, which is where this docs-only diff gets its prose review (row 16).
   - No `/skill-review` is needed, because no `SKILL.md` changes.

## Out of scope

- **Building any of the levers:** incremental credit, a delta tier, a narrowed cumulative pass, a fan-out cap, or a GH-1057 skip. No code, skill, hook, or test changes (row 3).
- **New instrumentation:** for example, a per-spawn record of handed bytes and verdicts, or longer retention for marker diff files and findings files. The engineer scoped measurement to existing data (row 4). The reopening list names what an answer would need without prescribing it.
- **A standing `transcript-analysis.py` subcommand** for repeat-spawn byte identity or delta placement. It is code, which row 3 excludes.
- **Investigating first-pass completeness versus reviewer variance.** The section names it as the open question.
- **Publishing any scan figure.** That needs the owner's per-figure authorization (G3), which this plan does not seek.
- **A design record under `docs/design-decisions/`** (Approach, Placement).
- **Edits to two docs:**
  - `docs/case-studies/review-loop-cost-forensics.md`, which is a preserved record under CLAUDE.md Axis 3;
  - `docs/design-decisions/ready-for-review-fix-loop-convergence.md`, which the new row links to and this change does not supersede.
- **Closing or relabeling GH-1207 or GH-1057.** That is the engineer's call.
- **Sequencing against the transcript-decomposition plan.** Under row 8's rule, this docs-only plan now goes second (row 37). Whether to reorder is for the session to raise with the engineer.
