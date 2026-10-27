# Review plan prose inside /plan-review, not after approval

## Context

Goal: review a plan's prose with `comment-discipline-reviewer` inside `/plan-review`, before the plan is approved, and stop reviewing plan prose after approval.

Ask: "the time to run comment discipline reviewer is when the plan is being written before it's approved and committed to the repo" — with the constraint "I really hope it doesn't like trigger a bunch of churn in the plan review process." Widened to place it in `/plan-review` itself: "if comet disciplinary is is included then edits can be batched together and comments occur the pros comments Medicare at the same place as other comments" (verbatim voice transcription; read as "if comment-discipline-reviewer is included, edits can be batched together, and the prose comments occur in the same place as the other comments"). Widened again via label "Fix it in this PR", selected in answer to how the plan should handle a `/plan-review` spawn satisfying `skill-fidelity-reviewer`'s check that `/code-review` spawned the reviewer.

Today `comment-discipline-reviewer` first sees a plan after `/plan-review` has approved it. `/code-review` hands it the whole diff, which includes a newly created plan under `.claude/plans/`, and at the commit gate that spawn is deferred to `/ready-for-review`'s cumulative pass. Fixing a finding there means editing an already-reviewed plan. That edit changes the plan's content hash and re-arms the plan-review gate, so a prose-only fix costs a full `/plan-review` round. The engineer therefore defers these findings, and committed plans stay hard to read for the engineer and for other harness users. The intended outcome is that prose findings on a plan arrive alongside the other `/plan-review` findings and are applied in the same edit pass. A plan with prose-only findings still ends its round approved, so the check adds no review rounds, and a fix never lands on an already-approved plan.

## Approach

`/plan-review` spawns `comment-discipline-reviewer` on every round to review the plan file's prose, and its findings never by themselves make the verdict **Request changes**. A plan file inside a `/code-review` diff leaves that reviewer's scope, so plan prose is reviewed once, before approval. `review-trace` also labels each main-thread reviewer spawn with the review round it ran in. The timeline `/ready-for-review`'s fidelity check reads then leaves out `/plan-review`'s spawns, so the check stops counting one as a spawn `/code-review` made.

**How it works.**

- `plan-review/ROUTING.md` gains a third always-spawn rule (M1). The reviewer is handed the plan file through a new `plan_path:` input (M2), not a diff.
- Given a `plan_path:`, the reviewer reviews the whole plan file rather than a diff (M2). It checks three angles only: Comment verbosity, Multi-fact comment structure, and Paragraph readability (M3). Quoted text is out of its scope. That covers the engineer's quoted words and any text the plan copies from a file or prescribes for one.
- `/plan-review` lists the findings in their own **Plan prose** section. It applies the accepted ones in the same edit pass as the round's other changes (M4). A round whose only findings are plan prose therefore ends approved, as **Approve with changes** when any finding is accepted, and the completion marker is written as for any other approved round (row 5).
- The spawn is skipped under `/review-pr`'s standing override. That run reviews someone's already-approved plan and applies no fixes (row 15).
- `review-trace` prints `round=` on every reviewer row, naming the review skill whose main-thread round window holds the spawn, or `?` when none does (M8).
- A new `review-trace --omit-plan-review-spawns` flag leaves out the `round=plan-review` rows, and `skill-fidelity-report.sh` passes it. The fidelity reviewer therefore never sees `/plan-review`'s spawns and needs no new rule (M9).

**Why `/plan-review`, and why not the other homes.**

- `/plan-it`, as a spawn before the `/plan-review` handoff: its fixes would need their own edit dispatch (row 1), and the engineer's later message moved the reviewer into `/plan-review` (row 2).
- `/code-review`, as today, with prose-only plan edits exempted from the plan-review gate: listed in Out of scope, because the gate cannot tell a prose edit from a substantive one.
- The `/plan-review` session's own inline prose check: it loses the fresh-context property that is the reason this reviewer exists (row 18).
- `/tighten-prose`: it rewrites rather than flags, it never drops content, and it never edits a plan file in place (`tighten-prose/SKILL.md:15-28`).

**Why these three angles.** The remaining six angles judge whether durable text belongs where it sits, and a plan properly carries what they flag:

- PR-defined terminology, "Used to be X" framing, and the Survives-the-PR self-test flag PR-scoped labels and narration, which a plan is the home for.
- Prose at the wrong altitude relocates text between files. Section placement inside a plan is governed by `plan-it`'s section grammar instead, which `plan-shape.py` and `/plan-review` Step 3 already check.
- Prose restating the code would flag a plan's account of current code, which is its evidence under B5.
- Restated canonical rule would flag the rules a plan quotes to justify its design.

A plan's design rationale and its alternatives set aside are content the plan must carry, so the reviewer does not count them as Comment verbosity. Without that carve-out its fixes would cut text that B14 then asks to restore.

**Every round, not only the first.** A **Request changes** revision rewrites prose that a first-round-only spawn would leave unchecked. Because the findings never block, the extra spawn per round adds no round.

**Known cost.** Each round adds one Sonnet spawn for every stow consumer (row 24). The change also widens a gap in `/ready-for-review`'s fidelity check. That check counts a reviewer spawn made anywhere on the branch, so a `/plan-review` spawn already satisfies a `/code-review` row naming `staff-sdet` or `ciso-reviewer` (row 27). Once `/plan-review` spawns `comment-discipline-reviewer` every round, the gap would also hide a skipped `/code-review` spawn of it (row 28). The engineer chose to close the gap in this PR (row 35). The fix below serves every reviewer the two skills share.

**Telling `/plan-review`'s spawns from `/code-review`'s.** A spawn's round is the review round window that holds it on the main thread, by the rule `review-round-cost` already applies (row 36):

- A window opens on a `code-review`, `plan-review`, or `ready-for-review` invocation, whether a `Skill` call, a `/slash` command, or a `review-orchestrator` dispatch.
- It ends at the next human message or the next opening, so a nested invocation takes over. A spawn after `/ready-for-review` invokes `/code-review` reads `code-review`.
- `skill-review` and `agent-review` open no window, which matters because `/code-review` invokes them mid-round (row 40).

`review_trace.py` dedupes the session's main-thread records with `pricing.dedup_turns_by_request_id`, as `review-round-cost` does, then calls `review_rounds.detect_round_windows` unchanged over them (rows 37, 38, 45). Only a main-thread spawn can take a label, and that covers every `/plan-review` spawn, because `/plan-review` runs in the session that invokes it (row 39). A spawn read from a subagent's own transcript reads `?`, and so does a main-thread spawn outside every window.

**Where the drop happens.** In code, at the producer the fidelity check consumes. `/ready-for-review` step 4 hands its reviewer the `review-trace` half of `skill-fidelity-report.sh`'s output (row 48), and that script now passes `--omit-plan-review-spawns`. The flag drops `round=plan-review` reviewer rows and prints the rest without `round=`. Every row left counts the same for the check, so the reviewer's input keeps the shape its spawn-dispatch step 4 already reads, and its body changes only to name the flag. The default timeline keeps every row, labeled, for the other skills that read it (row 47).

**Which rows drop.** Only `round=plan-review` rows. A `?` row stays, because it can be a `/code-review` spawn whose window a human message closed, and a wrong `[SILENT-SKIP]` halts `/ready-for-review` (row 42). A `ready-for-review` row stays too, because that skill's own spawn is `skill-fidelity-reviewer`, which no Change-type row names (row 41). A real `/code-review` spawn reads `plan-review` only when the session makes it after invoking `/plan-review` on the main thread, with no human message and no `/code-review` invocation between. A `/code-review` invocation in the spawn's own response counts as between, whatever the block order, because deduplication merges that response into one record (rows 37, 44).

**The dropped rows stay out of the fidelity reviewer's input.**

- They cannot change its verdict, since a dropped row never satisfies a Change-type row.
- Listing them would bring back the reviewer-side rule this design removes, a prompt instruction not to count them.
- The reader who must tell a mislabeled spawn from a missing one is whoever handles a `[SILENT-SKIP]` halt. A run without the flag shows that reader every reviewer row with its `round=` label. `/ready-for-review` step 4's halt paragraph points that reader to the run, and `docs/transcript-analysis.md` says so beside the flag.

Alternatives set aside:

- Round windows per subagent transcript file, so a spawn inside `review-orchestrator` reads `code-review`. No consumer reads a label on a subagent's spawn, since every `/plan-review` spawn is main-thread (row 39). The per-file windows would need a keyword on a function four cost consumers share, giving `review_rounds.py` a second definition of a round (row 45). They would also split records by file position, and they would add a false-drop surface in any subagent file that invokes `/plan-review`. Main-thread labeling is therefore strictly safer, not merely as safe.
- The drop as a rule in the fidelity reviewer's step 4. A Sonnet reviewer would apply a deterministic filter, and only sampled runs could bound its false-halt rate. A bar of at most 1 `[SILENT-SKIP]` in 5 runs passes a reviewer that falsely halts 20% of the time with probability about 0.74 (binomial: 0.8^5 + 5 × 0.2 × 0.8^4).
- A `grep` filter in `skill-fidelity-report.sh`. It would match on a display format, and testing it would need the filter extracted into a shell lib. The flag is unit-tested where the label is computed.
- Leaving `plan-review` rows out of the default timeline. Three other skills read that timeline for every spawn (row 47).
- A generic `--exclude-round SKILL` flag. One consumer needs one value, and the other values would ship untested.
- `REVIEW_TRACE_SKILLS` as the opening set. Its `skill-review` and `agent-review` members would relabel the rest of a `/code-review` round's spawns, and the check would drop them (row 40).
- A first-line tag such as `SPAWNED-BY=plan-review` on every `/plan-review` spawn prompt, read the way `_is_architect_consult_dispatch` reads `MODE=` (`review_trace.py:127-135`). It labels only spawns made after it ships, and a session that drops the tag silently reopens the gap. The window rule labels every transcript already on disk and adds no prompt contract.

### Assumption ledger

**Root:** Plan prose first meets `comment-discipline-reviewer` after `/plan-review` has approved the plan. Each fix then re-arms the plan-review gate and costs a full round, so the engineer defers the findings and committed plans stay hard to read.

**Givens:**

- **G1 — A spawned subagent sees only its agent file, the loaded `CLAUDE.md` files, and its dispatch prompt, never the dispatching session's conversation.** This is the harness's subagent model, outside any repository artifact. So the reviewer can learn it is reviewing a whole plan only from its agent file or its prompt. `[verified: plan-it/SKILL.md:57; plan-review/SKILL.md:114 ("a spawned reviewer, which cannot see this session's messages")]`

**Rows:**

1. If the reviewer runs in `/plan-it`, its edits need an extra dispatch. `[engineer-verified: "is included in plan it then we still have to have the extra dispatch to make the edits"]` `anchors: root`
2. With the reviewer included in `/plan-review`, its edits are batched with the other findings' edits, and its comments land where the other comments do. `[engineer-verified: "not just including comment discipline reviewer in plan review"]` `[engineer-verified: "for plan review if comet disciplinary is is included then edits can be batched together and comments occur the pros comments Medicare at the same place as other comments"]` `anchors: root`
3. The design must not add churn to the plan review process. `[engineer-verified: "I really hope it doesn't like trigger a bunch of churn in the plan review process"]` `anchors: root`
4. Prose review belongs before the plan is approved. `[engineer-verified: "the time to run comment discipline reviewer is when the plan is being written before it's approved and committed to the repo"]` `anchors: root`
5. **Approve with changes** ends a round. The completion marker is written once the listed changes are applied, with no re-review, so a non-blocking finding adds no round. `[verified: plan-review/SKILL.md:277, 288]` `anchors: row3`
6. Edits applied under **Approve with changes** are covered by a marker over bytes no reviewer read. This holds today for every such edit, so prose fixes inherit the property rather than introduce it. `[verified: plan-review/SKILL.md:288; tighten-prose/SKILL.md:23-28]` `anchors: row5, out-of-scope`
7. The reviewer's input is a diff only, as a path or as literal text. It holds no `Bash`, and a site is in scope only as an added or modified diff line. `[verified: comment-discipline-reviewer.md:6, 17-22, 187-189]` `anchors: M2`
8. Only four shared Input-contract sentences are pinned, and the test checks each one on its own as a whitespace-normalized substring of the section. New sentences may therefore sit between them, and the unpinned opening and "Judge scope only" sentences may change. `[verified: test_agent_roster.py:60-74, 263-270]` `anchors: M2`
9. Today's plan rule puts a newly created plan in scope for Multi-fact comment structure only and puts a hunk against an existing plan out of scope. `[verified: comment-discipline-reviewer.md:46-54]` `anchors: M3`
10. Every name in `_SCOPE_CITED_ANGLE_NAMES` must appear in the agent's `## Scope` section. The tuple is maintained by hand and checked only from the tuple to the file. `[verified: test_design_decision_files.py:633-676]` `anchors: M6`
11. Assertion 9 hashes the Paragraph readability angle's text. This plan edits no angle text, so the hash and its fixture stay valid. `[verified: test_design_decision_files.py:54-62]` `anchors: M3`
12. Every spawn criterion lives in `ROUTING.md`, which the session must `Read` before any spawn. `[verified: plan-review/ROUTING.md:28-31; plan-review/REFERENCES.md:30; require-routing-read.sh:71]` `anchors: M1`
13. The reviewer carries `Write` and the file-based-output contract, so `ROUTING.md`'s existing `findings_path` recipe applies to it unchanged. `[verified: plan-review/ROUTING.md:50-52; comment-discipline-reviewer.md:6, 225-266]` `anchors: M1`
14. Domain spawns carry plan scope, section, question, and Item ownership routing, plus the Ledger cross-check on a re-review of a ledger-carrying plan. `ROUTING.md:58` and `SKILL.md:118` both send that cross-check to "every spawned reviewer". `[verified: plan-review/ROUTING.md:35, 58; plan-review/SKILL.md:118]` `anchors: M5`
15. `/review-pr` runs `/plan-review` under an override that changes nothing and writes no marker. It may hand the plan over as text rather than as a file. `[verified: review-pr/SKILL.md:9, 78]` `anchors: M1`
16. The file-based contract already accepts prior findings. It names confirmed fixes on one line and raises only new sites, contradictions, or missing fixes. `[verified: comment-discipline-reviewer.md:248-254]` `anchors: M1`
17. A live `/plan-review` session exempts reviewer spawns from the round-consult gate, so the new spawn consumes no round state. `[verified: require-architect-consult.sh:74-82]` `anchors: M1`
18. Extending an existing persona is the roster's default move, and this reviewer exists to be a fresh-context observer. `[verified: docs/design-decisions/reviewer-persona-roster-operations.md:5, 7, 21]` `anchors: M2, root`
19. `/plan-review`'s Exclusions list bars style preferences unless they cause ambiguity. A session could read that as licence to drop every prose finding. `[verified: plan-review/SKILL.md:244-250]` `anchors: M4`
20. `/code-review` hands this reviewer the whole diff and relies on it to scope itself, and that row's sentences are pinned. `[verified: code-review/SKILL.md:287; test_skills.py:5380-5395]` `anchors: M3, out-of-scope`
21. Step 1 of `/plan-review` can identify a plan held only in conversation context, which has no file to hand over. `[verified: plan-review/SKILL.md:47-54]` `anchors: M1`
22. Handed a `plan_path:`, the reviewer reviews the whole file against the three angles only, and it honors the quoted-text exclusion and the rationale carve-out. Handed a diff, it skips plan files and still flags other prose. On a re-review it does not re-raise a declined finding. `[unverified]` This is prompt behavior, and Verification step 4's Cells P, D, RR, and R check it. `anchors: M2, M3`
23. The stowed `comment-discipline-reviewer` resolves to the main checkout's agent file until this branch merges and is pulled. `[unverified]` The basis is `CLAUDE.md` § "Working in this repo" ("go live on `git pull`"). Verification step 4 avoids the question by running the worktree's agent body through a `general-purpose` dispatch. `anchors: row22`
24. The user surface is every stow consumer who runs `/plan-review`, each paying one more Sonnet spawn per round. `[verified: comment-discipline-reviewer.md:2; CLAUDE.md "Plans in this repo affect all stow users"]` `anchors: root`
25. The new design-decision file stays in scope, though the Ask did not name it. `[engineer-verified: "Keep the doc (Recommended)"]` The option's description was the session's wording, so this row covers only the file staying in scope. `anchors: M7`
26. A `reviewer-spawn` row in the `review-trace` timeline carries the agent type, timestamp, branch, model, and thread, but not the skill whose run made the spawn. `[verified: claude/.claude/scripts/transcript_analysis/review_trace.py:318-330, 751-752]` `anchors: M8`
27. The fidelity check counts a spawn of the required type made anywhere on the branch. A `/plan-review` spawn of `staff-sdet` or `ciso-reviewer` therefore already satisfies `/code-review` rows naming that reviewer, such as test code and security controls. `[verified: skill-fidelity-reviewer.md:116-121; plan-review/ROUTING.md:30, 88, 90; code-review/SKILL.md:279-280]` `anchors: M9`
28. `/code-review` spawns this reviewer only outside staged-diff commit-gate rounds, such as in `/ready-for-review` step 3's cumulative pass. `/plan-review` spawns it nowhere today. `[verified: code-review/SKILL.md:287; ready-for-review/SKILL.md:82; plan-review/ROUTING.md:28-46]` `anchors: M9`
29. `reviewer-yield` pools dispatches by agent type, with no split by the skill that dispatched them. Its cited-path table drops any citation of a plan file. `[verified: claude/.claude/scripts/transcript_analysis/reviewer_yield.py:20-24, 417-427, 620-633]` `anchors: out-of-scope`
30. Three agent sentences assume a diff: the Input contract's "Judge scope only from the diff you were handed", How-to-work step 2's "against every angle above", and step 3's added-or-modified-line rule. `[verified: comment-discipline-reviewer.md:19, 182-192]` `anchors: M2`
31. `ROUTING.md:54` asks every reviewer for checklist-item-keyed findings, whether it writes a findings file or falls back to inline output. `[verified: plan-review/ROUTING.md:54]` `anchors: M5`
32. This reviewer's paragraph-readability fixture was measured at K=5 through a `general-purpose` wrapper at `model: sonnet`. Its bars were at least 4 of 5 runs on must-flag and at most 1 of 5 on must-pass. `[verified: claude/.claude/hooks/tests/fixtures/paragraph-readability.json:11]` `anchors: row22`
33. The `general-purpose` wrapper stands in for the shipped agent closely enough for the cells' bars to carry over. `[unverified]` The wrapper holds more tools, inherits the session's effort instead of the agent's `effort: high`, and carries the body as a prompt (comment-discipline-reviewer.md:2-6). The paragraph-readability fixture used the same wrapper (row 32). `anchors: row22, row23`
34. The `/plan-review` session keeps a plan prose finding out of the verdict even when the reviewer labels it `BLOCKER`. Three things pull the other way: the Exclusions list, that label, and Output format's rule that every returned finding must appear. `[unverified]` The basis is `plan-review/SKILL.md:244-250, 260`, and Verification step 4's Cell V checks it. `anchors: M4`
35. The fidelity-check fix belongs in this PR. `[engineer-verified: "Fix it in this PR"]` The option's description, including its estimate of about four files, was the session's wording, so this row covers only the fix being in this PR's scope. `anchors: M8, M9`
36. `review-round-cost`'s round window opens on a `code-review`, `plan-review`, or `ready-for-review` invocation, as a `Skill` block, a `/slash` tag, or a `review-orchestrator` spawn. It ends at the next fresh human message or the next opening. Today only a main-thread record opens one. `[verified: claude/.claude/scripts/transcript_analysis/review_rounds.py:41, 68-69, 126-160, 176-214]` `anchors: M8`
37. `detect_round_windows` documents its input as deduped, main-thread-only records. `review-round-cost`, `cost-counts`, and `author-outcome` each run `pricing.dedup_turns_by_request_id` before it, and only the review-bench miner passes undeduped records, as its docstring says. Deduplication merges each same-`requestId` run of assistant records into one record at the run's first member's position, drops the run's later members, and passes every other record through in place. Claude Code writes one record per content block, so a response that spawns a reviewer and then invokes `/code-review` is two records, spawn first. Undeduped, the spawn reads the earlier window's skill, which can be `plan-review`. Deduped, the merged record opens the `code-review` window, which holds the spawn. M8 therefore dedupes the main-thread records first, so each spawn reads the round `review-round-cost` charges its dispatch to. `[verified: claude/.claude/scripts/transcript_analysis/review_rounds.py:176-178, 392-394, 409-448, 502-503; claude/.claude/scripts/transcript_analysis/author_outcome.py:228, 755-757; evals/review_bench/mine_review_rounds.py:187-192; claude/.claude/scripts/transcript_analysis/pricing.py:232-258, 305-325]` `anchors: M8`
38. A timeline event's `line_no` is its record's 1-based position in the session's flat record list, assigned before the chronological sort and the date filter, so `line_no - 1` indexes the record that produced it. The event's `thread` comes from that record's own `isSidechain`, the same test M8 uses to pick main-thread records. `[verified: claude/.claude/scripts/transcript_analysis/review_trace.py:244-257, 281-287]` `anchors: M8`
39. `/plan-review` runs in the session that invokes it, because `review-orchestrator`'s description reads "DO NOT TRIGGER for /plan-review". A `/plan-review` run inside some other subagent would have its spawns read `?`, and they would count as every spawn counts today. `[verified: claude/.claude/agents/review-orchestrator.md:3]` `anchors: M8`
40. `/code-review` invokes `skill-review` and `agent-review` through `Skill` from inside its round. `REVIEW_TRACE_SKILLS` contains both, and `REVIEW_SKILLS` contains neither. `[verified: code-review/SKILL.md:205, 288; review_trace.py:20-22; review_rounds.py:41]` `anchors: M8`
41. `/ready-for-review` step 3 invokes `/code-review` through `Skill`, and step 4 spawns only `skill-fidelity-reviewer`. The spawn-dispatch check scopes itself to `staff-*`, `ciso-reviewer`, and `comment-discipline-reviewer`. `[verified: ready-for-review/SKILL.md:82, 100; skill-fidelity-reviewer.md:108-115]` `anchors: M9`
42. A spawn-dispatch `[SILENT-SKIP]` halts `/ready-for-review`, and `[DISCLOSED]` does not apply to that check. `[verified: ready-for-review/SKILL.md:104; skill-fidelity-reviewer.md:122-127]` `anchors: M9`
43. No test pins the printed reviewer row or the `reviewer-spawn` key set. `test_skills.py` pins four Input-contract clauses of `skill-fidelity-reviewer.md` that the edit keeps. `test_skill_fidelity_report.py`'s stand-in for `transcript-analysis.py` reads only the subcommand and the value after `--branches`, so an added flag leaves its three tests passing. `[verified: Grep of claude/.claude/scripts/tests and claude-skills/skills/tests; test_skills.py:1323-1327, 5286-5290; claude/.claude/scripts/tests/test_skill_fidelity_report.py:23-42]` `anchors: M8, M9`
44. A real `/code-review` spawn reads `round=plan-review` only when the session makes it after a `/plan-review` invocation on the main thread, with no human message and no `/code-review` invocation between. A `/code-review` invocation in the spawn's own response counts as between, whatever the block order, because M8 dedupes that response into one record (row 37). How often sessions do that is `[unverified]`. Verification step 6 bounds it from below over this checkout's worktrees, and only when its denominator is above zero. Step 5 reads this branch's own trace. `anchors: M9`
45. `detect_round_windows` serves `review-round-cost`, `cost-counts`, `author-outcome`, and the review-bench miner. M8 calls it unchanged, so all four stay as they are. `[verified: review_rounds.py:188-197, 394, 503; author_outcome.py:228; docs/transcript-analysis-architecture.md:284-296]` `anchors: M8`
46. `reviewer-yield` counts only main-thread dispatches. It keys both its tables, and the per-week `reviewer_gap_pp` that `cost-ledger` reads from the same accumulation, by agent type. `[verified: reviewer_yield.py:475-477, 493-498, 523-559]` `anchors: out-of-scope`
47. Three skills run the default `review-trace` timeline and read its reviewer spawns: `review-loop-cost-audit`, `transcript-narrative`, and `error-mode-analysis`. `[verified: review-loop-cost-audit/SKILL.md:62; transcript-narrative/SKILL.md:77; error-mode-analysis/SKILL.md:145]` `anchors: M9`
48. `skill-fidelity-report.sh` produces the timeline the fidelity reviewer reads. `/ready-for-review` step 4 runs it and hands the reviewer its `review-trace` output. `[verified: claude/.claude/scripts/skill-fidelity-report.sh:21-25; ready-for-review/SKILL.md:90-100]` `anchors: M9`
49. `cmd_review_trace` reads each of its flags through `getattr` with a default, and the tests hand it the object `_review_trace_args` builds. A flag the parser never registers therefore fails no existing test and silently does nothing. `[verified: claude/.claude/scripts/transcript_analysis/review_trace.py:654-657; claude/.claude/scripts/tests/conftest.py:1402-1422]` `anchors: M9`
50. Before this change, `/plan-review` spawns neither `comment-discipline-reviewer` nor `skill-fidelity-reviewer`, and only `/ready-for-review` spawns the second. A row of either type on disk today that reads `round=plan-review` is therefore a spawn `/plan-review` did not make: a dropped `/code-review` or `/ready-for-review` spawn, or an ad hoc dispatch inside a `/plan-review` window. `[verified: plan-review/ROUTING.md:28-46; skill-fidelity-reviewer.md:5; ready-for-review/SKILL.md:100]` `anchors: row44`
51. The fidelity fix stays in this PR at its revised size of 11 files. `[engineer-verified: "Keep in this PR (Recommended)"]` The question named the 11 files. The option's description was the session's wording, so this row covers only keeping the fix in this PR at that size. `anchors: M8, M9`
52. The one-sentence halt pointer in `claude-skills/skills/ready-for-review/SKILL.md` stays in Dispatch 1, making the fidelity fix 12 files. `[engineer-verified: "Keep the sentence (Recommended)"]` The option's description was the session's wording, so this row covers only keeping that sentence and file. `anchors: M9`

**Mechanisms:**

- **M1 — A third always-spawn rule in `ROUTING.md`.** It fires on every round that reaches Step 5 with a plan file and is skipped under `/review-pr`'s override. The reviewer gets only `plan_path:`, its `findings_path:`, and, on a re-review round, its own prior findings with their dispositions. `anchors: row2, row4, row12, row13, row15, row16, row21`
- **M2 — A `plan_path:` input form in the agent's Input contract, with matching lines in its description, intro, and How-to-work steps 2 and 3.** Each sentence that assumes a diff gains a "with a diff" scope, and the contract's paging rule covers a plan file as it covers a diff file (row 30). Lighter primitives considered and set aside:
  - The session synthesizes a whole-file diff (`git diff --no-index /dev/null <plan>`). It fails because it adds a `Bash` step per round, and the agent would still need a signal to tell a `/plan-review` diff from a `/code-review` one, since the two get opposite plan rules (row 9).
  - The plan text goes inline in the prompt. It fails because plan text is not a diff under the contract (row 7), and every round's prompt would carry the whole plan.
  - The `/plan-review` prompt alone tells the reviewer to review the file, with no agent edit. It fails because that instruction contradicts the agent's own diff-only contract and its plan rule (rows 7, 9).
  - A new dedicated plan-prose reviewer. It fails the roster's bias against spawning a persona where extending one suffices (row 18).

  `anchors: G1, row7, row8, row18, row30`
- **M3 — Rewrite the agent's plan-file Scope rule.** The rewrite puts a handed plan in scope as a whole and sets the three-angle subset, the rationale carve-out, and the quoted-text exclusion. It puts a plan inside a diff out of scope and keeps the existing label rule. `anchors: row3, row4, row9, row11, row20`
- **M4 — A Plan prose paragraph in `plan-review/SKILL.md`'s Output format.** The paragraph gives the findings their own section and exempts them from the Exclusions list. It makes them non-blocking whatever their severity, has them applied in the same edit pass, and requires any decline to be named. `anchors: row3, row5, row6, row19, row34`
- **M5 — Say "domain reviewer" at `ROUTING.md:35`, `ROUTING.md:54`, `ROUTING.md:58`, and `SKILL.md:118`.** The question-and-Item-ownership prompt, the checklist-item keying, and the Ledger cross-check then skip this reviewer, whose findings key by angle and whose lane is prose, not ledger consistency. The re-review findings-file handoff later in line 35 stays general, so it still reaches this reviewer. The term already names the Reviewer roles table's agents (`ROUTING.md:9`). `anchors: row14, row31`
- **M6 — Add the two newly cited angle names to `_SCOPE_CITED_ANGLE_NAMES`, and add two tests.** The tuple's own comment requires the names. The first test checks that `plan_path:` appears in `ROUTING.md`'s Reviewer roles section and in the agent's Input contract, the two places that tie the dispatcher to the agent. It also checks that the Reviewer roles section keeps the `/review-pr` skip, whose loss would spawn the reviewer on an already-approved plan whose findings no one can apply. The second pins the two sentences whose loss reverses the design: the Plan prose paragraph's non-blocking sentence, and the Scope bullet putting a plan inside a diff out of scope. A prose record in the decision file cannot stop a later editor from undoing either. `anchors: row3, row10, row15, row30`
- **M7 — Docs: `README.md:264`, two `CHANGELOG.md` bullets, and a new design-decision file.** README line 264 is the reviewer's description, which M1 makes incomplete. The decision file is the durable home for why the findings are non-blocking and why the reviewer takes `plan_path:`, which a later editor of `/plan-review` would otherwise undo. It also records the Verification step 4 measurement, because no test re-runs those cells, and why the fidelity check's timeline drops `/plan-review`'s spawns in code. `anchors: root, row25, row32, row35, row42`
- **M8 — `review-trace` labels each main-thread reviewer spawn with its round.** `review_trace.py` gains `_attach_reviewer_spawn_rounds`, which dedupes the session's main-thread records with `pricing.dedup_turns_by_request_id`, runs `review_rounds.detect_round_windows` unchanged over them, and sets each `reviewer-spawn` event's `round_skill`. `cmd_review_trace` calls it on its timeline path only, after the `--deny-summary` arm has returned, so `--deny-summary` and `cost-ledger` never pay for it. The default timeline prints the label as `round=`. Reusing `detect_round_windows` keeps one definition of the window rule, which a separate walk in `review_trace.py` would restate. Deduping first, as its other consumers do, gives each spawn the round `review-round-cost` charges it to. The heavier per-file windows and the prompt tag are set aside in the alternatives list above. `anchors: row26, row35, row36, row37, row38, row39, row40, row45`
- **M9 — `review-trace --omit-plan-review-spawns`, passed by `skill-fidelity-report.sh`.** The flag drops `round=plan-review` reviewer rows before the session header's counts, and it prints the rest without `round=`. `skill-fidelity-reviewer.md`'s Input contract names the flag in the command it describes, and no step of that reviewer changes. `/ready-for-review` step 4's halt paragraph gains one sentence pointing whoever handles a spawn-dispatch `[SILENT-SKIP]` to the run without the flag. A parser test and a `skill-fidelity-report.sh` test hold the wiring, because a flag that never reaches `cmd_review_trace` would fail nothing else (row 49). The lighter primitives, a rule in the reviewer's prompt and a `grep` filter in the script, are set aside in the alternatives list above, along with dropping the rows from the default timeline. `anchors: row27, row28, row35, row41, row42, row43, row44, row47, row48, row49`

### Prescribed text

**`plan-review/ROUTING.md`.** Change line 28 to `The three always-spawn rules:`. Append this bullet after line 31:

```
- Always spawn `comment-discipline-reviewer` on every round that reaches Step 5 with a plan file from Step 1, so the plan's prose is reviewed before approval. Skip it under `/review-pr`'s standing override, which reviews an already-approved plan and applies no fixes. It is not a domain reviewer, so its prompt carries only these:
  - `plan_path:` with the plan file's absolute path
  - its `findings_path:`
  - on a re-review round, its own prior findings file and which of those findings were applied or declined

  It gets no Item ownership routing and no Ledger cross-check instruction. It keys its findings by angle name, not checklist item.
```

At line 35, change "When you spawn: pick the specialist" to "When you spawn a domain reviewer: pick the specialist". Leave the rest of that line as it is.

At line 54, make two changes:

- Change "structured findings (checklist-item-keyed bullets)" to "structured findings (checklist-item-keyed bullets from a domain reviewer)".
- Change "Findings written to a file must still be checklist-item-keyed" to "A domain reviewer's findings written to a file must still be checklist-item-keyed".

At line 58, change "include this instruction in every spawned reviewer's prompt" to "include this instruction in every spawned domain reviewer's prompt".

**`plan-review/SKILL.md`.** At line 118, change "every spawned reviewer also gets" to "every spawned domain reviewer also gets". Insert this paragraph after line 271, before the `DISPOSITION_RULE` comment:

```
**Plan prose findings.** Put `comment-discipline-reviewer`'s findings in their own **Plan prose** section, each naming its angle in place of a checklist item. List the reviewer among the spawned ones as `comment-discipline-reviewer: Plan prose`. The Exclusions list does not apply to these findings. A plan prose finding never makes the verdict **Request changes**, whatever severity the reviewer gave it, so plan prose alone never adds a review round. Apply each one you accept in the same edit pass as the round's other changes, and name any you decline with the reason.
```

**`comment-discipline-reviewer.md` Scope.** This replaces lines 46-54:

```
A plan file is reviewed only when `/plan-review` hands one over as `plan_path:`, before the plan is approved:

- That plan file is in scope as a whole.
- Only Comment verbosity, Multi-fact comment structure, and Paragraph readability apply to it. The other angles judge text meant to outlive the PR, while a plan is the PR's own record and properly carries its labels, before-and-after accounts, descriptions of current code, and the rules it cites.
- A plan's design rationale and the alternatives it set aside are content the plan must carry, not Comment verbosity.
- Quoted text in that plan is out of scope: the engineer's words on its `Ask:` line and inside `[engineer-verified: "<quote>"]` tags, and fenced or block-quoted text the plan copies from a file or prescribes for one.
- A plan file under `.claude/plans/` that appears in a diff is out of scope, because `/plan-review` already reviewed its prose.
- A label a plan defines is still PR-defined terminology in any other file.
```

**`comment-discipline-reviewer.md`, the other sites:**

- Input contract, line 19. Make three edits, and leave the four pinned sentences byte-identical (row 8):
  - After the first sentence, insert: ``From `/plan-review`, a `plan_path:` line instead names one plan file to review, with no diff.`` The pinned "For a path, `Read` it" sentence that follows then governs paging that file.
  - After "Never review a partial diff.", insert: ``Never review a partial plan either.``
  - Change "Judge scope only from the diff you were handed" to "With a diff, judge scope only from the diff you were handed".
- How to work step 2, line 182. Replace its first sentence with: ``With a `plan_path:` input, walk every paragraph and list item of the plan against the three angles the Scope section's plan rule names. With a diff, walk every added or modified comment and every added or substantially rewritten durable-doc paragraph against every angle above.``
- How to work step 3, line 187. Insert before its first sentence: ``With a `plan_path:` input, the Scope section's plan rule sets scope and this step does not apply.`` Then change "Judge scope from the diff you were handed" to "With a diff, judge scope from the diff you were handed".
- Intro, line 9. Change "checking a diff against" to "checking a diff, or a plan file `/plan-review` hands you, against".
- Description, line 5. Change "— dispatched by /code-review's Change-type table." to "— dispatched by /code-review's Change-type table, and by /plan-review on every round against the plan file."

**`test_design_decision_files.py`.** The tuple at lines 638-643 becomes this, in the order Python's `sorted()` gives:

```python
_SCOPE_CITED_ANGLE_NAMES = (
    "Comment verbosity",
    "Multi-fact comment structure",
    "PR-defined terminology",
    "Paragraph readability",
    "Prose at the wrong altitude",
    "Restated canonical rule",
)
```

**`skill-fidelity-reviewer.md`.** In the Input contract, the `review-trace` command at lines 23-24 becomes `transcript-analysis.py review-trace --this-repo --branches <branch> --omit-plan-review-spawns`. No other line of the file changes.

**`skill-fidelity-report.sh`.** The `review-trace` call at line 22 passes the flag, under this one-line comment above its `if`:

```bash
# A spawn /plan-review made never satisfies one of code-review's dispatch rows.
if ! "$SCRIPT_DIR/transcript-analysis.py" review-trace --this-repo --branches "$BRANCH" --omit-plan-review-spawns; then
```

**`ready-for-review/SKILL.md`.** Append this sentence to step 4's **Halt on a silent-abbreviation finding.** paragraph (line 104):

```
Before acting on a spawn-dispatch `[SILENT-SKIP]`, rerun `python3 ~/.claude/scripts/transcript-analysis.py review-trace --this-repo --branches <branch>` without `--omit-plan-review-spawns`, and report to the human any row there of the missing reviewer type that reads `round=plan-review`, since the fidelity view dropped it.
```

**`review-trace` reviewer row.** In the default timeline, `round=` sits between the subagent type and the parenthetical:

```
  [2026-05-20T10:17:30.000Z] line   62  reviewer     staff-sdet  round=plan-review  (branch=my-feature model=sonnet)
```

Under `--omit-plan-review-spawns`, a kept reviewer row prints as it does today, with no `round=`.

## Critical files

One phase, two `code-writer` dispatches with disjoint file sets. Dispatch 1 changes the transcript tooling, the fidelity report script, the fidelity reviewer's Input contract, and `/ready-for-review`'s halt paragraph, which the `--omit-plan-review-spawns` flag ties together. Dispatch 2 makes the plan-prose change, which the `plan_path:` token ties together, and owns both `CHANGELOG.md` bullets. Neither dispatch reads the other's output, so either may run first. They run one at a time only so that each one's `select-tests.py` run sees a tree no other dispatch is editing. Each dispatch writes its new tests first and runs them red, and every new test fails before its change lands. After both return, the main session runs Verification steps 3 and 4. It runs steps 5 and 6, which read this machine's transcripts, after this branch's first `/code-review` round, because step 5 checks that round's spawns.

**Dispatch 1, round labels and the fidelity view.** Its check is Verification step 1's Dispatch 1 run, plus step 2's `ruff check` and shell lint.

- **`claude/.claude/scripts/transcript_analysis/review_trace.py`**:
  - Add `_attach_reviewer_spawn_rounds(events, records)`, with a one-line docstring. It picks the records whose `isSidechain` is false, keeping each one's index in `records`. It dedupes them with `pricing.dedup_turns_by_request_id`, as `review-round-cost` does before `detect_round_windows` (`review_rounds.py:392-394`), and runs `review_rounds.detect_round_windows` over the result. It then sets `round_skill` on every `reviewer-spawn` event: for a `thread=main` event, the skill of the window holding the deduped record that holds `records[line_no - 1]`, and otherwise `None` (rows 37, 38).
    - The deduped record that holds a main-thread record is that record itself when dedupe passed it through. For a member of a merged `requestId` run, it is the merged record at the run's first member's position (`pricing.py:242-247`).
    - Build that map from dedupe's output rather than re-deriving its merge decision. A `requestId` merged when fewer deduped records carry it than main-thread assistant records do. Walk the main-thread records in order beside a pointer into the deduped list. A record whose merged `requestId` already has a position takes that position, and every other record takes the pointer's position and advances the pointer.
  - In `cmd_review_trace`'s per-session loop, call it right after `_review_trace_session_events` returns, and only when some event is a `reviewer-spawn`. It reads the unfiltered `records`, so `--since`, `--until`, `--branches`, and `--skill` never change a spawn's round.
  - Read the flag as `bool(getattr(args, "omit_plan_review_spawns", False))`, as the other flags are read. Under it, drop every `reviewer-spawn` event whose `round_skill` is `plan-review`, before the `if not events` check and the header counts.
  - Print `round=<skill>`, or `round=?` for `None`, on each reviewer row as prescribed, and leave the field out under the flag.
  - Import `pricing` by module, as the module's other imports are. Update the module docstring's import list, its list of cross-module calls, which gains `pricing.dedup_turns_by_request_id` and `review_rounds.detect_round_windows`, and `cmd_review_trace`'s `reviewer` bullet, one fact per sentence.
- **`claude/.claude/scripts/transcript_analysis/review_rounds.py`**: in `detect_round_windows`' docstring, add `review_trace.py` to the sentence naming its further consumers. No code changes (row 45).
- **`claude/.claude/scripts/transcript-analysis.py`**: register `--omit-plan-review-spawns` (`action="store_true"`) on `review-trace`, after `--skill`, with the help text "Leave out reviewer spawns made inside a /plan-review round, and print reviewer rows without round=."
- **`claude/.claude/scripts/skill-fidelity-report.sh`**: the flag and its comment, as prescribed.
- **`claude/.claude/agents/skill-fidelity-reviewer.md`**: the Input-contract command, as prescribed. Keep the four Input-contract clauses `test_skills.py` pins, and the diff bullet, byte-identical (row 43).
- **`claude-skills/skills/ready-for-review/SKILL.md`**: append the prescribed sentence to step 4's halt paragraph (line 104). It is a `SKILL.md` edit, so `/code-review` runs `/skill-review` on it (`.claude/rules/review-pipeline-dispatch.md`).
- **`claude/.claude/scripts/tests/conftest.py`**: `_review_trace_args` gains `omit_plan_review_spawns: bool = False`.
- **`claude/.claude/scripts/tests/test_transcript_review_trace.py`**: add `_slash_user` to the conftest import block, then add these tests, each with a one-line docstring. Every assertion on a label reads `event["round_skill"]` by subscript, so a missing key fails instead of reading as `None`.
  - The first eleven build a record list, call `_review_trace_session_events(records, None, None, None)`, then call `review_trace._attach_reviewer_spawn_rounds(events, records)`:
    - A main-thread `code-review` `Skill` call, then a `staff-sdet` spawn: `code-review`.
    - A `plan-review` `Skill` call, a `ciso-reviewer` spawn, a `_user_msg` human message, then a `staff-sdet` spawn: `plan-review`, then `None`.
    - A `plan-review` `Skill` call, a `_slash_user("code-review")` record, then a spawn: `code-review`.
    - A `code-review` `Skill` call, a `skill-review` `Skill` call, then a spawn: `code-review`.
    - A `ready-for-review` `Skill` call, a `code-review` `Skill` call, then a `comment-discipline-reviewer` spawn: `code-review`.
    - A `plan-review` `Skill` call, then one assistant record carrying both a `code-review` `Skill` block and a `staff-sdet` spawn block: `code-review`. The record before it reads `plan-review`, so this pins the `line_no - 1` lookup.
    - A `plan-review` `Skill` call, then a spawn on an `_asst(..., sidechain=True)` record in the same list: `None`.
    - A `plan-review` `Skill` call, then one response written as two `request_id="r1"` records, a `staff-sdet` spawn first and a `code-review` `Skill` call second: `code-review`. This is the split shape Claude Code writes, and undeduped windows would read `plan-review` (row 37).
    - A `plan-review` `Skill` call, two `request_id="r2"` text-only records, a `staff-sdet` spawn, then two `request_id="r3"` records, a `code-review` `Skill` call first and a `ciso-reviewer` spawn second: `plan-review`, then `code-review`. The `r2` merge shifts every later deduped position by one, and the `ciso-reviewer` record is a dropped later member, so this pins the map from a record to the deduped record that holds it.
    - A `plan-review` `Skill` call, an `_asst(..., sidechain=True)` record holding a `staff-sdet` spawn, then a main-thread `ciso-reviewer` spawn: `None` for the `staff-sdet` spawn and `plan-review` for the `ciso-reviewer` one. A sidechain record before a main-thread spawn pins the thread guard and the index translation.
    - An `_asst(..., sidechain=True)` record, a `plan-review` `Skill` call, then a main-thread `staff-sdet` spawn: `plan-review`.
  - The last six write real files with `_write_jsonl` and `_write_subagent_dispatch`, and run `cmd_review_trace` as `test_review_trace_sees_the_code_review_skill_call_inside_a_merged_orchestrator_transcript` (line 598) does:
    - The main thread holds a `plan-review` `Skill` call, a `ciso-reviewer` spawn, and two `general-purpose` dispatches. The first dispatch's file holds a `code-review` `Skill` call and a `staff-sdet` spawn, and the second's holds only a `staff-backend-engineer` spawn. The default output carries `ciso-reviewer  round=plan-review`, `staff-sdet  round=?`, and `staff-backend-engineer  round=?`.
    - The same files under `_review_trace_args(omit_plan_review_spawns=True)`: no `ciso-reviewer` row, both sidechain reviewer rows, `reviewer-spawns=2` in the header, and no `round=` anywhere in the output.
    - Under the flag, a `plan-review` `Skill` call, a `staff-sdet` spawn, a `code-review` `Skill` call, then a second `staff-sdet` spawn: exactly one `staff-sdet` row, carrying the second spawn's timestamp.
    - A `plan-review` `Skill` call on branch `other`, dated before the run's `since`, then a `staff-sdet` spawn on branch `feat` after it. Run with that `since`, `branches="feat"`, and `skill="code-review"`, the output carries `staff-sdet  round=plan-review`.
    - Under the flag, a `plan-review` `Skill` call, a `ciso-reviewer` spawn on a record whose branch and model family no other record carries, a `ready-for-review` `Skill` call, then a `skill-fidelity-reviewer` spawn. The output carries exactly one reviewer row, `skill-fidelity-reviewer`, and the header's `branches=` and `models=` leave out the dropped spawn's branch and model family.
    - Under the flag and `skill="code-review"`, a `plan-review` `Skill` call, then a `staff-sdet` spawn. The spawn is the session's only event and is dropped, so the output carries no `###` header for the session.
- **`claude/.claude/scripts/tests/test_skill_fidelity_report.py`**: the stand-in's `review-trace` arm also prints `review-trace omit-plan-review-spawns=<True|False>`, from whether its argv holds the flag. Add `test_review_trace_runs_with_omit_plan_review_spawns`, asserting `review-trace omit-plan-review-spawns=True` in stdout.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**: in `TestBuildParser`, add `test_review_trace_parses_omit_plan_review_spawns`. It asserts that `_mod.build_parser().parse_args(["review-trace", "--omit-plan-review-spawns"])` sets `omit_plan_review_spawns` to `True`. It also asserts that `parse_args(["review-trace"])` sets it to `False`, so the default timeline's readers keep every row (row 47).
- **`docs/transcript-analysis.md`**, § "review-trace":
  - After the `--skill NAME` flag bullet, add one for `--omit-plan-review-spawns`. It leaves out reviewer rows whose `round=` reads `plan-review` and prints the rest without `round=`. `/ready-for-review`'s fidelity check reads this view through `skill-fidelity-report.sh`, because a spawn `/plan-review` made never satisfies a `/code-review` dispatch row. A run without the flag shows what the view left out.
  - After the `line_no` paragraph, add a `round=` paragraph. It cites "review-round-cost" below for the window rule rather than restating it. It then states that only main-thread spawns take a label and that a nested invocation takes over. It also states that a `thread=sidechain` spawn, or a spawn outside every window, prints `round=?`.
  - In the sample, the reviewer rows at lines 496 and 497 gain `round=plan-review`, and the one at line 498 gains `round=?`.
  - Line 503 says the sidechain row came from inside the `staff-sdet` dispatch, but `staff-sdet` holds no `Agent`. Say instead that it came from inside a dispatched subagent, which is why it reads `round=?`.
- **`docs/transcript-analysis-architecture.md`**: in `review_trace.py`'s section (lines 346-350), add `pricing` to its import list, and add its `pricing.dedup_turns_by_request_id` and `review_rounds.detect_round_windows` calls, made for reviewer-spawn round labels. In `review_rounds.py`'s section (lines 294-296), name `review_trace.py` as a further `detect_round_windows` consumer.

**Dispatch 2, the plan-prose change.** Its check is Verification step 1's Dispatch 2 run, plus step 2's `ruff check` on its two test files. Add the tuple names and the two prose tests first and run them red. Then make the agent, `ROUTING.md`, and `SKILL.md` edits.

- **`claude/.claude/agents/comment-discipline-reviewer.md`**: as prescribed, the Scope replacement at lines 46-54, the three Input-contract edits at line 19, How to work steps 2 and 3, and the one-line edits at lines 5 and 9. Do not touch any text under `## Core review angles`, because assertion 9 hashes the Paragraph readability block (row 11) and `code-review` item 12a mirrors the angle headers.
- **`claude-skills/skills/plan-review/ROUTING.md`**: line 28, the new third bullet after line 31, and lines 35, 54, and 58, as prescribed. Reuse the existing `findings-path-suffix.sh` recipe at lines 50-52 and the re-review findings-file handoff at line 35. Do not add a Reviewer roles table row: a row there would make the reviewer a domain reviewer and pull in the Item ownership and Ledger cross-check machinery M1 excludes.
- **`claude-skills/skills/plan-review/SKILL.md`**: line 118 and the Plan prose paragraph after line 271, as prescribed. The file is 293 lines against its 500-line cap.
- **`claude/.claude/hooks/tests/test_design_decision_files.py`**: replace `_SCOPE_CITED_ANGLE_NAMES` (lines 638-643) with the prescribed tuple.
- **`claude-skills/skills/tests/test_skills.py`**: add two tests after `test_step_0_6_narrowing_keys_lanes_by_change_not_file_type` (ends line 5413), beside the other comment-discipline lane pins. Both read sections through `_heading_section_text` (line 5419), which collapses whitespace and works on any markdown path. Model them on `test_comment_discipline_row_states_its_whole_diff_lane` (line 5380). Each docstring is one line.
  - `test_plan_review_hands_comment_discipline_reviewer_a_plan_path` asserts that `` `plan_path:` `` and "Skip it under `/review-pr`'s standing override" both appear in `_ROUTING_MD_PATH`'s `## Reviewer roles` section. It also asserts that `` `plan_path:` `` appears in the `## Input contract` section of `_AGENTS_DIR / "comment-discipline-reviewer.md"`. Docstring: the dispatcher and the agent name the same input token, and the spawn keeps its `/review-pr` skip.
  - `test_plan_prose_findings_never_block_and_plan_diffs_stay_out_of_lane` asserts that "A plan prose finding never makes the verdict **Request changes**" appears in the `## Output format` section of `_skill_file("plan-review")`. It also asserts that "A plan file under `.claude/plans/` that appears in a diff is out of scope" appears in the agent's `## Scope` section. Docstring: these sentences keep plan prose non-blocking and reviewed once, before approval.
- **`README.md`**: at line 264, add that `/plan-review` also spawns the reviewer on every round to review the plan's own prose before approval. Change "the task is reading a diff against a fixed rule set" to "the task is reading a diff or a plan file against a fixed rule set".
- **`CHANGELOG.md`**: two new first bullets under `## [Unreleased]` → `### Changed`. In the first, state that `/plan-review` now spawns `comment-discipline-reviewer` every round on the plan file and that it checks only the three angles. State that its findings never by themselves make the verdict Request changes and are applied with the round's other changes. State that it no longer reviews a plan file inside a `/code-review` or `/ready-for-review` diff, and that the change is live on `git pull`. In the second, state that `review-trace` prints `round=` on each reviewer row, naming the review round a main-thread spawn ran in, or `?`. State that its new `--omit-plan-review-spawns` flag leaves out `/plan-review`'s spawns, and that `/ready-for-review`'s skill-fidelity check passes it, so a `/plan-review` spawn no longer counts toward a `/code-review` dispatch row. State that the change is live on `git pull`. Line 246 is a past record; leave it.
- **`docs/design-decisions/plan-prose-reviewed-inside-plan-review.md`** (new): an H1, a blank line, then the landing date as an italic ISO line 3 with no `Formerly` clause, per `.claude/rules/design-decisions.md`. Record only what a reader cannot re-derive from the skill text:
  - A post-approval prose fix re-arms `require-plan-review.sh`, which is why plan prose moved before approval.
  - `/plan-it` was set aside because its findings would need their own edit dispatch.
  - The findings are non-blocking so plan prose adds no round. Cite `plan-review/SKILL.md` § "Output format" for the rule rather than restating it.
  - Approve-with-changes edits go un-reviewed for prose fixes exactly as for every other such edit.
  - The reviewer takes `plan_path:` rather than a synthesized diff, because a diff would need a `Bash` step per round and still a mode signal.
  - The fidelity check's timeline leaves out `/plan-review`'s spawns in code, through `review-trace --omit-plan-review-spawns`, not through a rule in the fidelity reviewer's prompt. A prompt rule would leave a false-halt rate that only sampled runs can bound, on a gate where `[SILENT-SKIP]` halts with no disclosure path.
  - Round labels cover main-thread spawns only. Every `/plan-review` spawn is one, and labeling a subagent's spawns would add a drop surface no consumer needs.
  - A measurement note: the date, the `general-purpose` wrapper at `model: sonnet`, and each Verification step 4 cell's K, bar, and outcome. State that no test re-runs the cells, so an edit to the agent's Scope plan rule or to the Plan prose paragraph re-runs them by hand.

  End with a `Sources` section citing this plan, `plan-review/ROUTING.md` § "Reviewer roles", `comment-discipline-reviewer.md`'s Scope section, and `docs/transcript-analysis.md` § "review-trace".
- **`.claude/plans/plan-prose-pass.md`**: this plan, committed with the change.

## Verification

Run these from the worktree root.

1. After each dispatch, `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` exits 0, and its `select-tests: running` line on stderr names at least the paths below. A missing path is a `select-tests.py` rule-table bug to report, not a licence to widen the run by hand.
   - After Dispatch 1: `claude/.claude/scripts/tests`, `claude/.claude/hooks/tests`, `claude-skills/skills/tests`, and a review-bench test under `evals/`, since `review_rounds.py` is a review-bench dependency (`select-tests.py:84-98`). The run covers the new trace, parser, and fidelity-report tests, the existing tests of `detect_round_windows`'s consumers, and the four skill-fidelity Input-contract pins in `test_skills.py`.
   - After Dispatch 2: `claude/.claude/hooks/tests` and `claude-skills/skills/tests`. The run covers the pinned Input-contract sentences and the description length cap in `test_agent_roster.py`. It also covers the Scope-cited angle names and the unchanged item 12a lockstep and assertion 9 hash in `test_design_decision_files.py`, and the two new tests in `test_skills.py`.
2. `scripts/venv-run.sh ruff check claude/.claude/scripts/transcript_analysis/review_trace.py claude/.claude/scripts/transcript_analysis/review_rounds.py claude/.claude/scripts/transcript-analysis.py claude/.claude/scripts/tests/conftest.py claude/.claude/scripts/tests/test_transcript_review_trace.py claude/.claude/scripts/tests/test_skill_fidelity_report.py claude/.claude/scripts/tests/test_transcript_analysis.py claude/.claude/hooks/tests/test_design_decision_files.py claude-skills/skills/tests/test_skills.py` passes. So does the shell lint `CLAUDE.md` § "Commands" names, `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`, which covers `skill-fidelity-report.sh`.
3. `Grep` under `claude-skills/skills/plan-review/` for `every spawned reviewer`, for `Findings written to a file`, and for `When you spawn:` returns no match.
4. Behavioral cells, run by the main session after both `code-writer` dispatches return. Every cell shares this setup:
   - Fixtures and findings files live in a scratchpad outside the repo, per `.claude/rules/skill-and-agent-self-review.md`.
   - Each run is a `general-purpose` dispatch with `model: sonnet` (rows 23, 33).
   - A reviewer cell's prompt carries the worktree's edited agent body as its instructions and tells it to use only `Read`, `Grep`, `Glob`, and `Write`. The input lines `ROUTING.md` prescribes follow, with a scratch `findings_path:`.
   - Reviewer cells take the paragraph-readability precedent's numbers (row 32): K=5, each must-flag site flagged in at least 4 of 5 runs, and each must-pass site cited in at most 1 of 5. A 2-of-3 bar passes a site the reviewer flags half the time in half of all trials. The 4-of-5 bar passes it in under a fifth (binomial at p=0.5: 4/8 against 6/32).
   - On a missed bar, revise the text the cell exercises: the agent's Scope plan rule or the Plan prose paragraph. Re-run every cell that reads the revised text. Never loosen a bar to pass.
   - After two revisions without every bar passing, stop and return every cell's results to the engineer. Repeated misses point at the prompt design, not at its wording.

   The cells:

   - **Cell P, a plan handed as `plan_path:`.** The scratch plan's Approach carries three must-flag sites:
     - a three-fact run-on sentence, for Multi-fact comment structure
     - an instruction placed after its rationale, for Paragraph readability
     - a paragraph narrating the session's search steps rather than design rationale, for Comment verbosity

     It also carries ten must-pass sites:
     - a run-on `Ask:` quote
     - a run-on `[engineer-verified: "..."]` quote
     - a fenced, prescribed comment that is itself a run-on
     - a design-rationale paragraph naming two rejected alternatives
     - a `Phase 2` label, for PR-defined terminology
     - a "Today X. After this change, Y." account, for "Used to be X" framing
     - a paragraph describing current code as evidence, for Prose restating the code
     - a `CLAUDE.md` rule restated in the plan's own words, for Restated canonical rule
     - an implementation-detail paragraph in Context, for Prose at the wrong altitude
     - a sentence citing "round 1's finding R3", for the Survives-the-PR self-test

     Apart from the three quoted run-ons, each must-pass site is clean under Multi-fact comment structure and Paragraph readability, so a citation of one points at the exclusion it probes. A further bar: at most 1 of 5 runs carries a finding that names any other angle.
   - **Cell D, a diff handed as a diff-file path.** The diff carries three files:
     - a new plan under `.claude/plans/`, with `new file mode`, carrying Cell P's must-flag sites
     - a hunk against an existing plan that adds one more three-fact run-on
     - a new `docs/` file with one three-fact run-on, as the positive control

     Bars: the `docs/` site is flagged in at least 4 of 5 runs, so a diff mode that returns nothing fails. Each plan-file site is cited in at most 1 of 5.
   - **Cell RR, a re-review round.** Take Cell P's plan and one run's findings file. Apply one finding to the plan, and leave another finding's site unchanged. The prompt adds that findings file's path and the dispositions, the first applied and the second declined with a reason, as `ROUTING.md`'s re-review handoff prescribes. Bar: the declined site returns as a new `R<n>` finding in at most 1 of 5 runs.
   - **Cell V, the session's verdict.** The dispatch reads the worktree's edited `plan-review/SKILL.md` whole, along with Cell P's plan. It is told it is writing Output format, with `comment-discipline-reviewer` the only spawned reviewer and no other finding. It gets one Cell P run's findings file, with one finding relabeled `BLOCKER`. Bar: all 5 runs put the findings in a Plan prose section and end **Approve with changes**. One **Request changes** run fails the cell, because each one costs a full extra round (rows 3, 34).
   - **Cell R, real plans.** Run the reviewer once with `plan_path:` on this plan and once on `.claude/plans/comment-discipline-once.md`. Both are committed plans with a ledger, `[engineer-verified]` tags, and fenced prescribed text. Read every finding. Bar: at most one finding per run falls outside the three angles or lands on quoted text, a fenced block, or design rationale. In-scope findings are the feature working and carry no bar. Record each run's finding count and angle split.

   Record each cell's K, bar, and outcome for the decision file's measurement note.
5. After this branch's first `/code-review` round, run `python3 claude/.claude/scripts/transcript-analysis.py review-trace --this-repo --branches plan-prose-pass` from the worktree root, so the worktree's `review_trace.py` runs. Keep its output in this session, because it is this machine's transcript data. The bar:
   - At least one main-thread reviewer row reads `round=plan-review`. Without one, the label never fired on a real record, and the step fails.
   - Every `thread=sidechain` reviewer row reads `round=?`.
   - Every main-thread reviewer row spawned during one of this branch's `/plan-review` rounds reads `round=plan-review`, and every one that `/code-review` round spawned reads `round=code-review`.
   - A main-thread row reading `?` in place of either label passes only when the engineer sent a message between that round's opening `skill` row and the spawn. To check, `Read` the main transcript named in the session's `###` header, from the `skill` row's `line` to the spawn row's `line`, for a `user` record carrying the engineer's text rather than a `tool_result`. List each accepted `?` row with the `line` of the engineer message that justifies it.
   - If that `/code-review` round ran through `review-orchestrator`, its reviewer spawns are `thread=sidechain` and read `?`, so no main-thread row can read `code-review`. Say so in the step's result rather than pass that half silently, because only the unit tests then check the `code-review` label.
   - Any other label is an M8 defect to fix, not one to explain (row 44).

   Then rerun with `--omit-plan-review-spawns`. Its reviewer rows must be exactly the first run's rows minus those reading `round=plan-review`, with no `round=` field.
6. Before the PR opens, run `python3 claude/.claude/scripts/transcript-analysis.py review-trace --this-repo` from the worktree root, over every branch. Its scope is this checkout's worktrees, as `--this-repo` matches them, not every transcript on the machine. Report two counts of its `comment-discipline-reviewer` and `skill-fidelity-reviewer` rows:
   - The denominator: rows of the two types whose session shows a `plan-review` `skill` row earlier in its timeline. Only these rows can take a stale `plan-review` label.
   - The mislabel count: rows of the two types that read `round=plan-review`. Each one is a spawn `/plan-review` did not make (row 50).

   Then:
   - A zero mislabel count is evidence that the window rule mislabeled no spawn of either type only when the denominator is above zero. With a zero denominator, say that the step inspected no exposed row, and row 44 stays `[unverified]`.
   - On a nonzero mislabel count, stop and give the engineer each row's session path, `line`, and branch, because each is a spawn the fidelity check would have dropped.
   - Keep the output and both counts in this session, out of every committed or published artifact.

## Out of scope

- **Exempting prose-only plan edits from `require-plan-review.sh` after approval.** The gate keys on a content hash. Telling a prose edit from a substantive one needs a judgment no hash makes, and a gate that trusts the editor's own classification opens a path around plan review.
- **Narrowing `/code-review`'s Change-type trigger so a plan file alone does not spawn the reviewer.** The row hands the reviewer the whole diff and relies on it to scope itself, and that wording is pinned (row 20). The cost is one no-finding spawn on a cumulative diff whose only prose is a plan file.
- **Re-reviewing edits applied under Approve with changes.** That gap already applies to every such edit (row 6), and closing it for prose alone would add the round the engineer asked to avoid (row 3).
- **Backfilling prose review on plans already committed.** The Ask covers plans before approval (row 4).
- **`plan-it/SKILL.md`.** Step 6 already hands off to `/plan-review` and addresses its findings, so it needs no edit.
- **`docs/skills.md:8`, `docs/design-decisions/reviewer-persona-roster-operations.md:21`, and `plan-review/REFERENCES.md:30, 67`.** All remain accurate:
  - The first is a one-line summary.
  - The second records why the persona exists.
  - The third names the two existing always-spawn rules after "including", which stays true with a third rule.
- **Splitting `reviewer-yield`'s tables by round.** `reviewer-yield` still pools this reviewer's `/plan-review` and `/code-review` dispatches, as it already does for `staff-sdet` and `ciso-reviewer` (row 29). M8's `round_skill` field does not reach it, because `reviewer-yield` never reads `review-trace` events. A split would reshape both its tables and `cost-ledger`'s per-week `reviewer_gap_pp`, which read the same per-agent-type accumulation (row 46), and the fidelity fix needs neither. `reviewer-yield` counts main-thread dispatches only, so a follow-up can call `detect_round_windows` directly.
- **A round label on `skill` and `architect-consult` rows.** No consumer reads one. The fidelity view drops reviewer spawns only.
- **Re-running the cells against the named agent.** The stowed agent resolves to the main checkout's file until this branch merges and is pulled (row 23), so the pre-merge cells use the `general-purpose` wrapper (row 33).
