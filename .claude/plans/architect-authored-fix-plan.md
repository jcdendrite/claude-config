# Architect-authored fix plan (pilot, default-off)

## Context

Goal: pilot, behind a default-off config key, a per-round `plan-architect` dispatch that authors the fix plan from reviewer findings, which the orchestrator then executes (GitHub issue #1213, which would supersede #1187's review-only role if adopted).

Ask: "/plan-it github issue 1213". Sequencing: accepted via label "Plan it now, gated by a config key (Recommended)". Scope: "Let’s park this until I work on the separate issue to reduce the size of the code review skill". Skill-size placement: "I guess a disposition rule… but don’t we want to gate it by a config key??" Unblocked: "You are now unblocked. Another session has made headroom in code review skill."

Why now: #1213 asks for the pilot design to be reviewable before measurement exists. Enabling waits on #1211 steps 1-3 and stays out of this plan.

## Approach

The #1213 pilot ships default-off behind a new per-account config key, `architect_fix_plan_pilot`. With the key on, a `/code-review` round's remaining reviewer-spawned findings go, numbered, to one `plan-architect` consult. Its numbered rows become the round's disposition when the return passes a mechanical shape check, and every other return stops for the human.

The new region's wording exists in one place only: the draft block in Critical files, Phase B item 8. The rest of this plan gives the rationale and points there.

The `code-review/SKILL.md` change is that one `DISPOSITION_RULE` region, 8 lines and at most 3,500 bytes, gated at runtime by the key. The rest of the change covers:
- The key's schema and docs surface.
- Three test additions: an anchor-set entry, a whole-region pin and a key-wiring test.
- A new decision record.
- A CHANGELOG bullet.

There is no hook, agent, mode or ledger-script change. The reviewed plan is committed first (plan-it Step 7, row 59). A headroom measurement (dispatch 0) follows, then two implementation commits, all in one PR.

Why this shape, and what was set aside:

- **Reuse `MODE=consult`. Add no new mode.** `round3-consult-verdict-routing.md` already rejected a new `plan-architect` mode, because a second grammar has to stay in lockstep with `code-review`'s reading rule (row 12). The row grammar lives in the dispatch prompt, where the contradiction route already asks one consult for one verdict per finding (row 13). The consult contract asks for prose but does not bar rows inside it (row 64). `plan-architect.md` is untouched, because its description already admits a skill-prescribed consult (row 21).
- **Placement: a base-file region, as the engineer chose (row 32).** Two other placements were weighed:
  - *The project layer (lighter, repo-scoped).* `/code-review` Step 0.5 conditionally loads one `.claude/skills/code-review-*/SKILL.md`, and this repo's layer already carries a `## Finding disposition addition` (row 38). That would have kept the pilot out of other consumers' skill bodies and off the 500-line cap. The engineer rejected it (row 37).
  - *A runtime auxiliary file (heavier).* It would need a `_skill_auxiliary_files.py` entry, a `check-skill-length.sh` regex edit shipped to every consumer, and either a read-gate hook or a waiver (row 26).

  The region has three costs, all recorded in the decision record:
  - Every stow consumer loads its bytes on every `/code-review`.
  - Each round with a reviewer-spawned finding makes one `config-get.sh` call, which may prompt (row 20).
  - An opted-in account runs the pilot in every repo it reviews, while the evaluation counts this repo only (row 29).
- **Region shape: the contradiction region's own shape, with one wording authority.** The region has five parts:
  - a start-anchor line;
  - one paragraph line;
  - a blank line;
  - four bullet items, with the end anchor inline on the last;
  - a blank line before `**DEFER criteria (closed list).**`.

  That spends 8 of the 12 lines of headroom (row 6). The four items are parallel outcomes with no precedence, so they are bullets, not a numbered list. They are the valid table, the `foundation: wrong` stop, the barred-row stop and the stop for any other return. `docs/skills.md` forbids flattening a list to save lines (row 45). Round 3 found the previous revision's two statements of the region disagreeing on 8 of 17 pinned phrases. The draft block is now the only statement. The gate counts lines, not bytes, so the plan sets a 3,500-byte ceiling as its own cost choice (row 49). This is the architect's call.
- **Validity is positive and mechanical, and every other return stops.** The dispatch numbers the payload findings. Deciding whether a return is a valid table then takes three checks:
  - The row numbers match the payload numbers.
  - Each row carries a valid verdict word and that verdict's required parts.
  - Exactly one `foundation:` line exists.

  The draft's *Valid table* item is the rule. Text outside the rows and the foundation line decides nothing (row 61). A prose-wrapped table is therefore valid, which matters because a consult's return is prose by contract (row 64). A finding the architect cannot commit to gets no row, so the round stops. Every return is classified as valid or stop without judging intent. The one judgment left is the barred-row check. It is the same classification the default procedure makes for the same finding, and it carries no extra doubt clause, so the two arms classify alike. Two stops stay explicit although the catch-all covers them:
  - `foundation: wrong`, with or without rows, asks a different question: whether to re-plan, as the round-cap consult's first verdict does.
  - A DEFER or KEEP row on a finding that the enforcement-invariant rule or a loaded project layer bars passes every shape check. The barred-row item names it as a stop, and the valid-table item excludes it by name.

  At any stop the orchestrator never re-dispatches or falls back on its own. The human can re-dispatch, and the new return is read under the same list. The human can also send the payload findings to the default procedure (the region's "the rest of this section"), re-plan, or decide the findings directly.

  Provenance:
  - The engineer rejected the fall-back-and-tag posture (row 52).
  - The engineer confirmed the stop-on-everything rule (row 53).
  - Reading 2 is the engineer's (row 61).
  - The numbered-row grammar is the architect's design choice, adopted from round-3 review. The engineer leans toward it (row 62).

  The architect's reasons:
  - Treatment stays consistent within each PR. A key-on round either ran a valid table or a human chose otherwise, and the tag records which.
  - There is no contradiction between falling back and stopping, no precedence order between checks, and no mixed-treatment round.
  - Matching rows to findings by free text would be a fresh judgment each round. Too strict, it causes spurious stops. Too lax, it lets a merged or invented row through. Numbers remove that judgment.
  - The latch gap cannot pass silently (M8).

  Here the consult is the treatment, so the pilot stops where the contradiction route and ready-for-review's Cap already stop (row 48). The cost is that a malformed return interrupts the human. There is no automatic retry, so no retry count needs grounding. CLAUDE.md requires a primary-source citation for a retry literal, and none exists. Each stop names the condition the return failed, which leaves the evidence for any later retry decision in the transcript.
- **Decision (A): a loaded project layer binds the table's rows.** The dispatch carries the project layer's path when Step 0.5 loaded one, and the existing barred-row item names two sources: the enforcement-invariant rule and a loaded project layer.
  - Reason, arm parity: Step 0.5 loads the layer into every review in this repo (row 38). This repo's layer bars DEFER of a genuinely-lax, non-regression gate failure outside its closed existing-debt set (row 63). Without (A), the control arm's orchestrator applies that rule, but the pilot's architect never sees it. The evaluation would then measure the architect plus a loosened rule in the one repo it counts (row 29).
  - Cost: the two clauses that carry (A) and the logging clause binding table rows to the layer, about 105 bytes of the draft (row 49), loaded by every stow consumer.
  - Placement: (A) respects a layer's rules but does not put the pilot in a layer, so the engineer's placement rejection stands (row 37).
  - (A) is the architect's design choice (row 62).
- **Three places this plan departs from the ticket.**
  1. The contradiction route's settled-site stop and carries still run *before* the dispatch. A finding at a site an earlier verdict settled goes to the human on a repeat, unless a DEFER or an engineer carry applies (row 13). So the architect never re-settles a site, and drift at one site stays bounded.
  2. The fix goes out as one `code-writer` per round carrying the architect's fix shapes, not one dispatch per file group. `subagent-delegation` prescribes one dispatch per round (row 24), and parallel dispatches in one worktree clobber each other silently.
  3. The issue says "only a foundation-wrong verdict is a stop-and-ask". This plan keeps that for re-litigation: a valid table's rows never stop on their merits. It also stops on every return that is not a valid table (rows 52, 53, 61) and on a barred row. It overrides neither the pinned enforcement-invariant rule nor a loaded project layer.
- **The round-cap consult folds in through the latch, not through a text edit.** The first pilot dispatch on a branch writes the once-per-branch latch (rows 5, 9, 10). That leaves the round-cap section unreachable on that branch, and every later round's foundation line asks the round-cap re-plan question again. The section's text stays, because it still governs key-off consumers.

  The round-cap consult's second verdict ("stop escalating") has no pilot counterpart beyond per-finding gold-plating DEFER rows.

  Known gap: `_maybe_write_consult_latch` reads only the prompt's first line, so any pilot dispatch that returns writes the latch, whatever it returned (row 9). A dispatch that ends in a tool error depends on row 41. A return with no foundation verdict can therefore latch the branch. The engineer accepted this (row 60). The stop makes it visible but does not prevent it.
- **Ledger: existing flags, plus a `fix-plan:` prefix on every table-decided row's `--rationale`.** The merged schema rejects `--decided-by` on ADDRESS and allows only `carry` on DEFER (row 8). Rationale text is therefore the only per-row marker available without a script change. Widening the flag would be a script change every consumer receives, for a default-off pilot. The prefix also separates pilot KEEP rows from contradiction-consult keeps, since both log `--decided-by plan-architect`. It costs 10 of the 300 rationale characters. An over-cap append already has a defined retry path (row 47), and the prefix rule binds that retry too.

  Table-decided rows log as the ledger section prescribes. That includes the `--ref <id>` the contradiction route requires on an ADDRESS or fresh DEFER of a repeat, with ids taken from the digest the dispatch carries (row 55). Without `--ref`, a non-carrying repeat at a live DEFER site leaves the old DEFER live. A later same-failure-mode repeat would then carry under it, with no fix and no stop.
- **Observability: exactly one tag per round with a pilot dispatch, on the *Fix route:* line.** The draft defines the three values `fix-plan: dispatched`, `fix-plan: fell-back` and `fix-plan: stopped`. Each records the round's final outcome.
  - Why the *Fix route:* line and not *Spawn decisions:*: § Output format writes *Spawn decisions:* before any finding, so that line comes before the disposition and the human's answer. The *Fix route:* line follows the disposition and already records consult routes (row 65). Under the pilot, payload findings have no disposition until a valid table or the human's answer exists, so the line can carry the final outcome. This placement rests on section order, not on an observed transcript.
  - A valid table with no ADDRESS row dispatches no `code-writer` and is still tagged `dispatched`, because "dispatched" names the architect dispatch.
  - A round with no payload makes no dispatch, carries no tag and runs as it would key-off.
  - Each stop names the condition the return failed, as the contradiction route's settled-site stop names its trigger.
  - The reader belongs to the pre-registration follow-up.
- **Tests: the region is pinned whole.** Until the adopt/revert decision the region is the treatment, so editing it mid-evaluation changes the experiment. A whole-region exact pin makes every such edit a visible change to the test constant. Adopt rewrites the region and the constant once, and revert deletes both (M13). The pin follows the contradiction and defer-invariant pins (row 67). An anchor-set entry and a key-wiring test complete the set. The draft feeds both the region and the constant, so a transcription error could be copied into both. Verification 8 catches that by comparing the committed region with the draft. Behavior stays unproven until the evidence the pre-registration names exists.
- **Revert keeps the key row.** Deleting a schema row makes every scaffolded `claude-config.toml` warn on every read (row 40). The revert contract is:
  - Delete the region, its pin constant and class, its anchor-set entry and its wiring test.
  - Set the audit's Call site to none.
  - Keep the key inert at default `false`.
  - Keep the decision record, so the docs-anchor never dangles.

  The round-2 pilot's revert also kept its key row (row 40). The PR is the merge unit, but a `git revert` of it is not the revert path.
- **Enabling is not this PR, and the hold is open-ended.** The key stays off on every account until three things exist:
  - The #1211 steps 1-3 baseline.
  - A pre-registered adopt/revert rule with a stop-rate threshold. It also names the behavior evidence required before enabling, meaning the scope of any Layer-2 run or cited transcript (row 58).
  - The evaluation's reader, run on a synthetic fixture before any account enables the key. Ledger and transcript retention floors would otherwise lose rounds the reader needs (row 46).

  Fixing gates before enabling follows the round-2 pilot's precedent (row 28). That pilot also shipped a stopping rule, and this one does not. The engineer accepted an open-ended hold, with no removal backstop if the key is never enabled (row 54). A default-off pilot overturns nothing, so no design record is edited to say it is superseded (row 22).
- **Sequencing.** The plan's own commit comes first. Dispatch 0 then measures headroom on the rebased tree. Dispatches 1 and 2 run back to back as two commits in the same PR. There is no dependency on any other open PR (rows 3, 7).

### Assumption ledger

**Root problem:** Today the orchestrating session (Sonnet by default) decides a round's dispositions. `plan-architect` is consulted only on three narrow triggers: heavier mechanism, contradiction, and round cap. #1213's hypothesis is that an Opus-authored per-round fix plan makes review loops converge with fewer disposition errors. Nobody has tested that, and §42 rejected this volume as a default. So the pilot has three requirements:
- It ships default-off.
- Its footprint in every stow consumer's `code-review/SKILL.md` fits the file's 500-line gate and a stated byte budget.
- Once it applies, every failure mode stops for a human, and a failed key read leaves it off.

**Givens:**

- **G1 — `code-review/SKILL.md`'s headroom is shared with every other branch that edits the file, and whichever merges first spends it.** Those branches' owners control their timing. This is why dispatch 0 measures, and why the size check re-runs after any rebase.
- **G2 — The Claude Code skill format has no include field.** Every line of `code-review/SKILL.md` loads for every stow consumer on every `/code-review`, and the skill can see a config value only through a runtime tool call. The vendor's skill format imposes this. Step 0.5's repo-scoped project layer is a conditional load this plan *could* use. It is not a given (rows 37, 38).
- **G3 — Everything under `claude/**` and `claude-skills/**` goes live for every stow consumer on `git pull`.** The `install.sh`/stow distribution model owns this.
- **G4 — The #1211 baseline that the pilot's evaluation must compare against does not exist yet.** #1211 owns it.

**Mechanisms:**

- **M1 — New config key `architect_fix_plan_pilot`.** Schema: `bool`, default `false`, `config-dir` resolution (per account), no legacy columns, not promptable. The skill reads it through `config-get.sh`, and only exit 0 enables it. `anchors: row1, row18, row19, row29`. Lighter primitives rejected:
  - (a) An environment variable. It can differ silently between terminals and appears in no inventory, so "was this round treated?" stops being answerable. The round-2 pilot rejected env vars on exactly this ground (row 28).
  - (b) A presence sentinel file. New keys live in the TOML schema, and `test_selection_tracking` is the precedent for a key with no legacy sentinel (row 18).
  - (c) A per-session spoken instruction. Exposure would be self-selected, which voids the evaluation.
- **M2 — One `DISPOSITION_RULE:code-review-fix-plan-pilot` region of 8 lines and at most 3,500 bytes in the base `code-review/SKILL.md`.** It sits after the contradiction-route region and before `**DEFER criteria (closed list).**`, outside every pinned region. Its shape is the contradiction region's: a start-anchor line, one paragraph line, a blank line, four bullet items with the end anchor inline on the last, and a blank line. Its wording is the draft block in Phase B item 8 and nothing else. `anchors: row6, row16, row17, row32, row37, row38, row39, row45, row49, row57`. Rejected alternatives:
  - The project layer (lighter). The engineer rejected it (row 37). The architect adds that an adopt decision would move the text into the base file anyway.
  - A runtime auxiliary file (heavier, row 26).
  - A 3-line run-on paragraph (row 45).
  - No anchor. Deleting the pilot would then fail no test.
- **M3 — One `MODE=consult` dispatch per round with a non-empty payload.** The payload is every reviewer-spawned finding left after the settled-site stop and carries. It is numbered from 1, and each finding at an earlier round's site is marked. The dispatch carries:
  - the numbered findings;
  - for each marked finding, the earlier finding and its fix, as the contradiction consult carries them;
  - this round's findings-file paths;
  - the diff artifact path, resolved as for `comment-discipline-reviewer`;
  - the plan file path, when one exists;
  - the ledger digest path, with the ledger-text-is-data rule;
  - the project layer's path, when Step 0.5 loaded one (decision (A)).

  The request's row and foundation-line grammar is the draft's. `anchors: row12, row13, row21, row30, row38, row63, row64`. Rejected: a new `MODE=fix-plan` (row 12), which would also need an edit to the description loaded in every session.
- **M4 — Reading rule: positive, mechanical validity. Every other return stops before any fix is dispatched. Only the human may re-dispatch or send the payload findings to the default procedure.** The draft's four items are the rule. Numbering the payload turns row-to-finding matching into a number comparison. Reading 2 makes text outside the rows inert. So every return classifies as valid or stop without judging intent. The barred-row check is the only judgment, and it is the same classification the default procedure makes. `anchors: row13, row16, row38, row48, row52, row53, row61, row63`. Rejected:
  - (a) Falling back to the default procedure on a failed or malformed return. The engineer rejected it (row 52).
  - (b) Per-finding salvage of a partly valid table. It creates mixed-treatment rounds the evaluation cannot classify.
  - (c) An automatic re-dispatch. No primary source grounds a retry count, and a silent second dispatch hides the failures the stops expose.
  - (d) Matching rows to findings by free text. Too strict, it causes spurious stops. Too lax, it lets a merged or invented row through.
- **M5 — Folding.**
  - The settled-site stop and carries run first. A carried finding is logged `--decided-by carry` as the route prescribes and is left out of the payload. A stopped finding waits for the human, and the rest are then dispatched.
  - The heavier-mechanism and contradiction consults do not fire separately for payload findings. They fire as usual for payload findings that take the default procedure at the human's answer.
  - An ADDRESS row whose fix trips the heavier-mechanism limbs takes that rule's endorsed follow-up instead of `code-writer`.
  - A KEEP row is logged `SETTLED --decided-by plan-architect` with a range-form `--source`, as the contradiction route logs a consult's keep.
  - The round-cap section is untouched.

  `anchors: row5, row9, row10, row11, row13, row14`.
- **M6 — Ledger: existing flags, plus the `fix-plan:` rationale prefix on every table-decided row.**
  - ADDRESS rows carry the prefix, plus `--ref <id>` where the contradiction route requires one for a repeat.
  - DEFER rows add `--defer-criterion`, a block-range `--source`, and the same `--ref` rule.
  - KEEP rows are `SETTLED --decided-by plan-architect`.
  - Rows decided by the default procedure, by a carry or by the human carry no prefix.

  `anchors: row4, row8, row47, row55`. Rejected:
  - (a) Widening `--decided-by` to ADDRESS and non-carry DEFER rows. That is a validation, schema and render change every consumer receives, for a default-off pilot.
  - (b) A new pilot flag. It has the same cost.
  - (c) No marker. Table-decided rows would then be inseparable from the rest, and pilot KEEP rows from contradiction-consult keeps.
- **M7 — One `code-writer` per round.** The row it carries verbatim is the architect's row, and the architect's fix shape stands where the reviewer's Suggested fix would. `subagent-delegation`'s "verbatim ADDRESS row" wording does not foresee that substitution. This plan accepts the quiet exception and makes no `subagent-delegation` edit. A valid table with no ADDRESS row dispatches no `code-writer`. `anchors: row24`.
- **M8 — No hook change for the latch.** The engineer accepted the interaction, so nothing needs changing (rows 5, 60). Preventing it would need content in the latch, which the round-3 decision forbids (row 10). Any pilot dispatch that returns writes the latch (row 9). A dispatch that ends in a tool error depends on row 41. Every such case is a stop under M4, so none passes silently. `anchors: row5, row9, row10, row41, row60`.
- **M9 — New decision record `docs/design-decisions/architect-authored-fix-plan-pilot.md`.** It is the docs-anchor target. It cites the region rather than restating it, and its bullets point to this plan's rows (Phase B item 10). No supersession edits. `anchors: root, row15, row22, row23, row40, row46, row54`.
- **M10 — Issue-body edits after merge, by the main session.** It edits #1213's and #1187's bodies and never adds comments. `anchors: row22, row36`.
- **M11 — Exactly one outcome tag per round with a pilot dispatch, on the *Fix route:* line.** The draft defines the three values and their final-outcome meanings. A valid table with no ADDRESS row is still `dispatched`. A round with no payload has no dispatch and no tag. When the human decides the findings directly at a stop, the tag is `fix-plan: stopped`. `anchors: row9, row46, row52, row65`. Rejected:
  - (a) Prefixing the default-procedure rows of key-on rounds too. That costs region bytes and touches rows the pilot did not decide.
  - (b) A ledger row per round. It needs a script change.
  - (c) The *Spawn decisions:* line. It is written before the disposition and the human's answer, so it cannot carry a final outcome (row 65).

  The reader is deferred to the pre-registration follow-up.
- **M12 — Tests: an anchor-set entry, one whole-region exact pin and a key-wiring test, plus a read-back that the region equals the draft.**
  - The pin takes the shape of `TestCodeReviewContradictionRouteRegionPin` (row 67). Its docstring lists the region's load-bearing rules.
  - Why pin the whole region: until the adopt/revert decision the region is the treatment, and editing it changes the experiment. A whole-region pin makes every such edit a visible change to the test constant. Adopt rewrites the region and the constant once, and revert deletes both (M13).
  - The wiring test keeps the schema-membership half: the key the region's `config-get.sh` call names must be a key in `config-keys.psv` (row 43).
  - Verification 8 compares the region with the draft. The pin alone would pass if one transcription error reached both the region and the constant.

  `anchors: row16, row17, row43, row58, row67`. Rejected:
  - (a) Clause-presence phrase pins. Round 3 found their wording disagreeing with the plan's prose on 8 of 17 phrases, and claiming more than they guarded.
  - (b) A Layer-2 case. None ships for this method, so the pre-registration names the behavior evidence instead (row 58).
- **M13 — Revert contract: delete the region and its test entries (pin constant and class, anchor-set entry, wiring test), set the audit's Call site to none, and keep the key row inert.** The record states this contract and the operational follow-up (set the key `false` on opted-in accounts). `anchors: row28, row40`.
- **M14 — Dispatch 0: a headroom measurement with the gate's own counter, before any implementation commit.** The precondition is a committed count of at most 492, so the region's 8 lines keep the staged count at or under 500. Above that, stop and ask. The plan funds no deletions on its own. `anchors: row6, row7, row45, row66`.

**Material assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Plan this now, and gate it behind a config key | `[engineer-verified: "Plan it now, gated by a config key (Recommended)"]` |
| 2 | Implementation was to wait on the code-review size-reduction work. Row 3 lifts this | `[engineer-verified: "Let’s park this until I work on the separate issue to reduce the size of the code review skill"]` |
| 3 | The hold in row 2 is lifted and headroom exists, so implementation proceeds now. This replaces the session's earlier reading, which tied the hold to PR #1217's merge | `[engineer-verified: "You are now unblocked. Another session has made headroom in code review skill."]` |
| 4 | How the returned table is recorded in the ledger is this plan's call | `[engineer-verified: "Let the planning process decide. Let the architect figure it out"]` |
| 5 | A fix-plan consult that writes the once-per-branch round-3 latch is accepted and documented. "No hook change" was the session's option description; this plan reaches it independently in M8 | `[engineer-verified: "Yes, accept it; document it (Recommended)"]` |
| 6 | `code-review/SKILL.md` is 488 lines on the rebased tree, 12 under its 500-line limit. The gate denies a commit only when the staged count exceeds the limit AND exceeds the committed count | `[verified: Read of this tree ends at line 488, re-read this revision; check-skill-length.sh limit_for and policy header, re-read earlier]` (`_lib_length_ratchet_exceeded`'s `awk 'END{print NR}'` counter and `new > limit && new > old` comparison: re-read by both round-2 reviewers, not by this revision) |
| 7 | The headroom came from GH-1079's merged change (#1230, commit `b2afb9b1`), which took `code-review/SKILL.md` from 495 to 488 lines. This branch's base is `688dedd0` (origin/main), and the file is unchanged since `b2afb9b1`. No step depends on another open PR | `[verified: Read of this tree ends at line 488, re-read this revision]` (495-to-488 and unchanged since `b2afb9b1`: per both round-3 reviewers' `git show` and `git diff`, not re-run) |
| 8 | #1182 is merged. `--decided-by engineer\|plan-architect\|carry` exists. ADDRESS and CLEAN reject it, DEFER accepts only `carry`, and SETTLED requires one of the three. DEFER requires `--defer-criterion` from a closed list of five names. DEFER and SETTLED require `--source`. `--rationale` is capped at 300 characters | `[verified: _review-ledger-lib.sh _review_ledger_validate_flags; review-ledger.sh usage text and _LEDGER_RATIONALE_MAX_CHARS, re-read]` |
| 9 | `log-reviewer-round.sh` writes the latch for any `plan-architect` dispatch whose first line is not exactly `MODE=plan-sections`. It reads only the prompt's first line, so any dispatch that returns writes it whatever the return says. `review-trace` classifies such a dispatch as a consult the same way | `[verified: log-reviewer-round.sh _maybe_write_consult_latch — earlier session; transcript_analysis/review_trace.py _is_architect_consult_dispatch — re-read by both round-2 reviewers]` |
| 10 | The latch means "an architect consult ran on this branch recently". Narrowing that meaning would need content in the latch, which the design rejects | `[verified: round3-plan-architect-consult-gate.md:44-56,95-102 — earlier session, not re-read]` |
| 11 | The round-cap consult fires only on a `require-architect-consult.sh` denial | `[verified: code-review/SKILL.md DISPOSITION_RULE:code-review-round-cap-consult-verdict, re-read]` |
| 12 | A new `plan-architect` mode or grammar was rejected. The routing rule stays on the reading session's side | `[verified: round3-consult-verdict-routing.md:17-22 — earlier session]` |
| 13 | The merged contradiction route works as follows. It asks one consult for one verdict per finding, and a failed, empty, hedged or partial return is a blocking stop. A finding at a settled or twice-rewritten site goes to the human with no consult unless it carries. A same-failure-mode repeat at a live DEFER, or at a live engineer SETTLED logged `--carry-forward`, carries without a stop and is logged `--decided-by carry`. A consult's keep is logged `SETTLED --decided-by plan-architect` with a range-form `--source`, and is never available to an enforcement-invariant finding | `[verified: code-review/SKILL.md DISPOSITION_RULE:code-review-contradiction-route, re-read]` |
| 14 | The heavier-mechanism route's endorsed follow-ups (a `plan-it` Step 5 re-dispatch, or `/plan-it`) are the hook-backed part of that route | `[verified: code-review/SKILL.md DISPOSITION_RULE:code-review-new-primitive-route, re-read; code-reviews-fix-route-unifies-42s.md:9 — earlier session]` |
| 15 | §42 rejected an unconditional consult on every ADDRESS finding on three grounds: volume, relocating the scope argument, and a third genre inside consult | `[verified: code-review-fix-route-plan-architect.md:13-17 — earlier session]` |
| 16 | The enforcement-invariant rule is absolute. It and the contradiction-route region are each pinned word for word | `[verified: code-review/SKILL.md DISPOSITION_RULE:code-review-defer-invariant, re-read; test_skills.py TestCodeReviewDeferInvariantRegionPin and TestCodeReviewContradictionRouteRegionPin, re-read this revision]` |
| 17 | `_EXPECTED_DISPOSITION_RULE_ANCHORS` is compared as an exact set. `test_disposition_rule_anchors_present` enforces `len(enclosed) >= 20` over each region's stripped text. `extract_governing_rule()` raises only on a missing, duplicated or misordered anchor | `[verified: test_skills.py test_disposition_rule_anchors_present, re-read earlier]` (the floor's location and `extract_governing_rule`'s raise conditions: per staff-sdet round 2) |
| 18 | `test_selection_tracking` is the only key with no legacy columns. Three `TestConfigScaffold` tests assert exact scaffold content against the real schema (`test_existing_hand_edited_row_survives_unchanged`, `test_presence_and_content_matches_polarity_keys_stay_absent`, `test_exclude_list_key_and_every_other_key_both_stay_absent`); two others also run the real schema but assert no exact scaffold content, and the rest use isolated fixture schemas. `test_doc_counts` pins the key count in `install.sh`, `migrate-legacy-config.sh`, `docs/config-file.md` (three places) and `config-schema-audit.md` (`Remaining N keys`) | `[verified: config-keys.psv, re-read this revision; test_config_lib.py TestConfigScaffold; install.sh "reports all seventeen"; migrate-legacy-config.sh header; docs/config-file.md § Migration and § Per-account isolation — re-read earlier]` (config-schema-audit.md: per staff-platform-engineer, not re-read) |
| 19 | `config-get.sh` exits 0 for enabled, 1 for disabled, 2 for an unknown key and 3 for an environment failure, so every failure reads as off. Skill precedent: `pr-description`'s `## Prose tightening pass` | `[verified: config-get.sh header, re-read]` |
| 20 | Neither `config-get.sh` nor `review-ledger.sh` has a `permissions.allow` entry. `code-review` already calls `review-ledger.sh` every round, so the extra call matches the current permission posture | `[verified: claude/.claude/settings.json grep — earlier session]` |
| 21 | `plan-architect`'s description and CLAUDE.md's Opus bullet already admit a skill-prescribed consult | `[verified: plan-architect.md:3; CLAUDE.md Model & Effort Routing — earlier session]` |
| 22 | No repo design record states #1187's "architect reviews, does not write". The architect's read-only tool set is unchanged by this plan | `[verified: grep for 1187 shows no design-record hit; plan-architect.md:4 — earlier session]` |
| 23 | #1213 waits on #1211 steps 1-3, and those steps produce the pilot's baseline | `[verified: gh issue view 1213 and 1211 bodies — earlier session]` |
| 24 | `subagent-delegation` prescribes one `code-writer` per round, carrying every ADDRESS row verbatim (finding, `file:line`, suggested fix). Under the pilot the row carried verbatim is the architect's row, whose fix shape stands where the reviewer's Suggested fix would. That substitution is a quiet exception to the skill's wording, which this plan accepts without editing it | `[verified: subagent-delegation/SKILL.md:165-177 — earlier session]` (the exception: this plan's reading, `[unverified]`) |
| 25 | Ready-for-review step 3 runs `/code-review`, so the pilot applies there too. Its Cap consult is a separate dispatch | `[verified: ready-for-review/SKILL.md:82-86 — earlier session]` |
| 26 | A runtime auxiliary file needs a `_skill_auxiliary_files.py` entry and a `check-skill-length.sh` regex entry. If the model can choose to skip the read mid-task, the file also needs a read-gate hook. The repo's guidance is "Shorten first, do not extract" | `[verified: _skill_auxiliary_files.py:18-26; no-shared-skill-partials.md:7-9 — earlier session; docs/skills.md § Skill architecture notes, re-read]` |
| 27 | SKILL.md frontmatter has no inclusion field, and `@path` imports are CLAUDE.md-only | `[verified: docs/skills.md § Skill architecture notes, re-read]` |
| 28 | The round-2 pilot ran default-off with pre-registered gates and was reverted. Its record fixed those gates before its sentinel existed and also carried a stopping rule | `[verified: round2-consult-trigger-pilot-revert.md:5-11 — earlier session]` (the stopping rule, `round2-consult-trigger-pilot.md` § Pre-registration: per staff-platform-engineer round 2, not re-read) |
| 29 | A `config-dir` key resolves per config dir, which means per account, not per machine. An opted-in account runs the pilot in every repo it reviews. The evaluation counts this repo only, with `--this-repo` | `[verified: config-keys.psv header resolution comment; docs/config-file.md § Per-account isolation, re-read]` |
| 30 | The volume ceiling is one Opus dispatch per key-on round with a non-empty payload, plus any re-dispatch the human sends, plus any default-procedure consult a human-chosen fallback triggers | `[unverified — derived from M3, M4 and plan-architect.md's model pin]` |
| 31 | The issue's "round-1 diff artifact" means the diff artifact of the round under review | `[unverified — interpretation of #1213's wording]` |
| 32 | Pilot prose goes in a `DISPOSITION_RULE` region of SKILL.md, gated at runtime by the config key | `[engineer-verified: "I guess a disposition rule… but don’t we want to gate it by a config key??"]` |
| 33 | The new decision record `architect-authored-fix-plan-pilot.md` stays in scope | `[engineer-verified: "Decision record (architect-authored-fix-plan-pilot.md)"]` |
| 34 | The `CHANGELOG.md` `[Unreleased]` bullet stays in scope | `[engineer-verified: "CHANGELOG.md [Unreleased] bullet"]` |
| 35 | The comment-only edit in `test_migrate_legacy_config.py` stays in scope | `[engineer-verified: "Comment-only edit in test_migrate_legacy_config.py"]` |
| 36 | The post-merge edits to #1213's and #1187's bodies stay in scope | `[engineer-verified: "Post-merge edits to #1213 and #1187 bodies"]` |
| 37 | The project-layer placement is rejected | `[engineer-verified: "The project layer is a terrible idea for the record. It is biased by the line cap."]` (the earlier quote with "solely" removed at the engineer's instruction: "Just remove 'solely' - that was imprecise.") |
| 38 | `/code-review` Step 0.5 conditionally loads exactly one repo-scoped `.claude/skills/code-review-*/SKILL.md`. This repo's layer already carries a `## Finding disposition addition`. A layer under `.claude/skills/` gets the 200-line default cap | `[verified: code-review/SKILL.md § Step 0.5; .claude/skills/code-review-claude-config/SKILL.md; check-skill-length.sh limit_for and staged-path regex — re-read]` |
| 39 | The contradiction region's end anchor closes its last paragraph line, and a blank line follows before `**DEFER criteria (closed list).**`. The new region starts on its own line after that blank | `[verified: code-review/SKILL.md, re-read this revision]` |
| 40 | Deleting a schema row leaves scaffolded `claude-config.toml` rows that print `_config.sh: warning: skipping line for unrecognized key` on every read. The round-2 pilot's revert kept its key row | `[verified: _config.sh state-file read loop; config-keys.psv round_consult_round2_pilot row — re-read]` (the Python reader's matching warning: `[unverified]`) |
| 41 | Whether `PostToolUse` fires on an Agent call that ends in a tool error. That decides whether such a pilot dispatch writes the latch. Any dispatch that returns writes it (row 9). Either way the round stops for the human (M4). Residual: a branch whose pilot dispatches never return a foundation verdict can lose its round-3 gate. The stop makes that visible but does not prevent it. The Claude Code hooks documentation settles the open half, and no run is needed | `[unverified]` (per staff-platform-engineer: the repo registers the latch writer on `PostToolUse` only, matcher `Agent\|Task`, and has no `PostToolUseFailure` hook) |
| 42 | The repo's clause-presence tripwire scopes to one section, whitespace-normalizes it, and asserts each listed phrase is present. `extract_governing_rule()` is the production parser for a region's text | `[verified: test_skills.py _TIER_DISPOSITION_SECTIONS, test_gate_tier_disposition_sections_keep_invariant_clauses, test_disposition_rule_anchors_present — re-read]` |
| 43 | A skill section that reads a config key already has a test asserting that `config-get.sh <key>` appears in that section | `[verified: test_skills.py test_declares_config_get_invocation, re-read]` |
| 44 | `_config.schema()` (`claude/.claude/scripts/_config.py`) is a Python schema reader, separate from bash `_config_scaffold`, and the hooks test tree already imports it | `[verified: test_doc_counts.py imports; config-keys.psv header "Read by both _config.sh (bash) and _config.py (Python)" — re-read]` |
| 45 | "At a line cap, shorten by deleting restated text, never by flattening a list" | `[verified: docs/skills.md § Skill architecture notes, re-read]` |
| 46 | Ledger rows are swept at `cleanupPeriodDays`, floored at 30 days, and transcripts roll over at 30 days. An evaluation started late loses early rounds | `[verified: _lib.sh _LEDGER_SWEEP_FLOOR_DAYS=30 and the review-ledger.sh append-path sweep — per staff-platform-engineer round 2]` (transcript rollover at Claude Code's `cleanupPeriodDays`: not re-read) |
| 47 | An over-cap or rejected ledger append is retried in tighter form, and a DEFER or SETTLED append that still fails is a blocking stop | `[verified: code-review/SKILL.md § Review-narrative ledger failure-case list, re-read]` |
| 48 | The contradiction route and ready-for-review's Cap treat a failed or empty consult as a blocking stop. In both, the consult is the decision, and no complete fallback exists | `[verified: code-review/SKILL.md contradiction region; test_skills.py _PINNED_STEP3_CAP_CLAUSE — re-read]` |
| 49 | Phase B item 8's draft region is 8 lines (7 plus the trailing blank) and fits the 3,500-byte ceiling. Round 4's edits took it from 3,259 to about 3,429 bytes; the two clauses carrying decision (A) were 79 bytes before the logging clause. Round 3's measured drafts of the earlier required content were 2,896 and 2,957 bytes. The ceiling is this plan's cost choice, because `check-skill-length.sh` counts lines only | `[verified: awk 'END{print NR}' and wc -c on a scratch copy of the draft block (the 8 lines inside Phase B item 8's fence) gave 8 lines and 3,259 bytes before round 4's edits; the revised fence measured 7 lines and 3,428 bytes without the trailing blank, so 3,429 with it, 71 under the ceiling. Re-measure after any further edit]` (round-3 figures: measured by staff-platform-engineer and staff-sdet; the lines-only gate: per staff-sdet round 3) |
| 50 | The schema-derived rewrite of `TestConfigScaffold`'s three exact-content asserts (Phase A item 6) is in scope | `[engineer-verified: "In scope (Recommended)"]` (selected label, asked at /plan-review round 2) |
| 51 | The quotes rows 1, 2, 4, 5 and 32–36 attribute to the engineer from the earlier session are accurate | `[engineer-verified: "Yes, accurate"]` (selected label, asked at /plan-review round 2) |
| 52 | The fall-back-and-tag posture for a failed or malformed pilot return is rejected. It leaves unknown, per PR, whether the architect ran consistently | `[engineer-verified: "The default is a complete fallback does NOT constitute a good reason. That just adds instability to the experiment. Now I don't know for any given PR running the experiment when the architect ran consistently."]` |
| 53 | Every outcome except a fully valid table is a blocking stop-and-ask, with no automatic re-dispatch. Only the human may re-dispatch or send the payload findings to the default procedure | `[engineer-verified: "I confirm this"]` (the engineer's explicit reply to the dispatching session's failure-rule question, quoted back by them after an earlier "Yes I accept 1 and 2"; the rule itself is a plan-architect consult's recommendation) |
| 54 | The plan accepts an open-ended hold, with no removal backstop if the key is never enabled | `[engineer-verified: "accept as open ended hold"]` |
| 55 | A repeat that does not carry takes a fresh disposition at a DEFER site. An ADDRESS or fresh DEFER of a repeat carries `--ref <id>` so the old row retires, and another finding at the site leaves it live. Ids come from the digest's Id column | `[verified: code-review/SKILL.md DISPOSITION_RULE:code-review-contradiction-route, re-read this revision]` |
| 56 | The contributor `.venv` exists only in the main checkout. A worktree for a branch whose name contains `/` sits four levels below it, so README's `../../../.venv` misses and `../../../../.venv` resolves | `[verified: ls -d from this worktree — per staff-platform-engineer round 2, not re-run]` |
| 57 | `test_review_ledger_script.py`'s `TestReviewLedgerEnumParity` splits `code-review/SKILL.md` on the literal `**DEFER criteria (closed list).**`. It then matches bold-led numbered items (`^\d\. \*\*`) after it, up to `**Invalid DEFER rationales.**` | `[verified: per staff-sdet round 2, not re-read]` |
| 58 | The Layer-2 disposition-fidelity harness passes a case on `treatment_block_rate >= 0.8` and has no permissive case type. Its treatment arm sees only the single `rule_anchor` region text. So only block-shaped scenarios fit | `[verified: evals/run_skill_evals.py; evals/README.md § disposition-fidelity — per staff-sdet round 2, not re-read]` |
| 59 | plan-it Step 7 commits the reviewed plan to the implementation branch before implementation begins, when Critical files names at least one file | `[verified: plan-it/SKILL.md Step 7, re-read this revision]` |
| 60 | The accepted latch interaction (row 5) also covers a pilot dispatch that writes the round-3 latch but returns no foundation verdict (a failed, empty or hedged return). It stays a known gap in the record, made visible by the stop rule in row 53 | `[engineer-verified: "I accept this"]` (the engineer's explicit reply to the dispatching session's latch-gap question, quoted back by them after an earlier "Yes I accept 1 and 2") |
| 61 | When a return's rows are valid but prose surrounds them, the rows decide. Text outside the rows and the foundation line is relayed verbatim and decides nothing. A finding the architect cannot commit to gets no row, which stops the round | `[engineer-verified: "Reading 2 yes"]` (the engineer's reply to the dispatching session's question on what "fully valid table" means when prose surrounds valid rows; "Reading 2" names the session's wording of this row's claim) |
| 62 | The engineer leans toward the restructure this revision adopts: a plan-architect consult's version, with five modifications, of an independent reviewer's recommendations. The quote does not cover this plan's text. The architect's own choices within it are: bullets rather than a numbered list; the KEEP verdict word; the "Barred row" label and the valid-table item's reference to it; the row-start rule (a row is a line starting `<n>. ADDRESS`, `<n>. DEFER` or `<n>. KEEP`); the carried contradiction-route judgment standard; the logging clause binding table rows to a loaded layer's logging rules; "the rest of this section" for the default procedure; sign-off and fenced foundation lines classed valid; no doubt clause on the barred-row check; and Verification 8's plain-`diff` check | `[engineer-verified: "This updated version sounds good"]` (a leaning, said of the consult's version; the listed architect choices are `[unverified]` as engineer decisions) |
| 63 | This repo's project layer, as of `688dedd0` (#1231), bars DEFER of a genuinely-lax, non-regression failure in a `hook-class: gate` hook outside its closed existing-debt set. Leaving such a finding unfixed takes a blocking stop-and-ask. A regression is an enforcement-invariant finding at every tier | `[verified: .claude/skills/code-review-claude-config/SKILL.md § "Finding disposition addition", re-read this revision]` (the merge commit: per both round-3 reviewers) |
| 64 | `plan-architect`'s `MODE=consult` asks for conversational prose that commits to a recommendation and names what was not verified, and it bars plan-section headings. It does not bar numbered rows and a foundation line inside that prose, so a prose-wrapped table is the expected return | `[verified: claude/.claude/agents/plan-architect.md § MODE=consult, re-read this revision]` |
| 65 | § Output format writes the mandatory *Spawn decisions:* line before any finding. § Finding disposition writes the mandatory *Fix route:* line after the disposition, and the heavier-mechanism and contradiction regions already record consult routes on it. The heavier-mechanism region writes its entry at disposition time, before that consult returns | `[verified: code-review/SKILL.md § Output format, § Finding disposition, DISPOSITION_RULE:code-review-new-primitive-route — re-read this revision]` (that a real pilot round writes its tag after the final outcome: section order only, no transcript, `[unverified]`) |
| 66 | The 500-line limit is enforced only by the commit-time `check-skill-length.sh` hook. No CI test asserts an absolute `SKILL.md` length, so two branches can each pass the gate and merge main past 500 | `[verified: per staff-platform-engineer round 3 (grep of test_skills.py and test_check_skill_length.py; tests.yml runs pytest and shellcheck only), not re-run]` |
| 67 | `TestCodeReviewContradictionRouteRegionPin` and `TestCodeReviewDeferInvariantRegionPin` each pair a module constant holding the region's text, as adjacent string literals, with a class whose one test compares `_normalized_anchor_text(skill_md_path, anchor)` with the constant whitespace-collapsed. The contradiction pin's docstring lists the region's load-bearing rules | `[verified: claude-skills/skills/tests/test_skills.py, re-read this revision]` |

## Critical files

The plan's own commit comes first (plan-it Step 7, row 59). Three dispatches follow. The session runs dispatch 0 itself. Two sequential `code-writer` dispatches follow it, one commit each, in the same PR. Their file sets don't overlap. Phase B names the key Phase A adds, so B runs after A. Dispatch prompts cite edit sites by symbol or quoted text, never by line number.

Every dispatch prompt names the venv tools resolved for this worktree, written below as `<venv>/bin/<tool>` (row 56). From a worktree at `.claude/worktrees/<prefix>/<slug>/`, four levels below the main checkout, `<venv>` is `../../../../.venv`. `<main-checkout>/.venv`, given as an absolute path, also works. The prompt says to stop and report if a tool is missing, and never to install anything or run `./install-dev.sh`.

**Dispatch 0 — headroom check (the session, before dispatch 1).** On the rebased tree, run `awk 'END{print NR}' claude-skills/skills/code-review/SKILL.md` and `wc -c claude-skills/skills/code-review/SKILL.md` as separate calls.
- Proceed only if the line count is at most 492. Record the byte count as dispatch 2's baseline.
- If the count is above 492, stop and ask the engineer. Name candidate same-commit deletions of text already stated elsewhere in the file (row 45), and do not trim anything on your own.

**Phase A — config-key surface (dispatch 1, commit 1).**

1. `claude/.claude/hooks/config-keys.psv`. Append this row after the `test_selection_tracking` row. It has 10 pipes, the same shape as `test_selection_tracking`'s row:
   `architect_fix_plan_pilot|bool|false|config-dir|false|config-dir|||Architect-authored fix-plan pilot|docs/design-decisions/architect-authored-fix-plan-pilot.md|`
2. `install.sh`. In the reporter comment, change `so one function reports all seventeen.` to `eighteen`.
3. `claude/.claude/scripts/migrate-legacy-config.sh`. In the header, change `all` / `seventeen keys` to `eighteen`.
4. `docs/config-file.md`.
   - Add a per-key table row after `test_selection_tracking`'s, linking the new decision record.
   - In § "Migration", change `For each of` / `the 17 keys:` to 18.
   - Rewrite the scaffold sentence that begins `None exist among today's 17 keys besides`. It must name both no-legacy keys and say the scaffold writes both keys' default rows. Keep `None exist among today's 18 keys` on one line, matching exactly once.
   - In § "Per-account isolation", change `merges *all 17` / `keys*` to 18.
   - Leave "the eleven non-enforcement-critical keys" as it is: 18 keys, minus 5 enforcement-critical, minus 2 with no legacy import, is 11.
5. `claude/.claude/hooks/tests/config-schema-audit.md`.
   - Change the opening "the 17 keys" to 18.
   - Change `## Remaining twelve keys` to `thirteen`.
   - Add an `### architect_fix_plan_pilot` section after `test_selection_tracking`'s, with these entries:
     - Call site: `code-review/SKILL.md`'s `code-review-fix-plan-pilot` region, which reads the key through `config-get.sh`.
     - Resolution: config-dir (per account).
     - Legacy-probe: false.
     - Legacy-import-locations: config-dir, with no legacy predecessor.
     - Fail direction: any nonzero exit reads as off, which leaves the default disposition procedure unchanged.
6. `claude/.claude/hooks/tests/test_config_lib.py`.
   - Derive the expected rows in `TestConfigScaffold`'s three real-schema exact-content asserts from the schema, not from observed output. Read `config-keys.psv` through `_config.schema()` (row 44). Keep the rows with an empty `legacy-polarity`, in file order, rendered as `<key> = <default>`. Prepend the hand-edited row where the test writes one. The next no-legacy key then needs no test edit.
   - Make `test_exclude_list_key_and_every_other_key_both_stay_absent` discriminate: exclude one of the two no-legacy keys and assert the derived list minus that key. Fix the docstrings and comments that call `test_selection_tracking` the one or only such key: in `_run_with_schema`, in the three asserting tests, and in `test_plain_key_with_no_legacy_polarity_still_gets_its_default_row`.
7. `claude/.claude/scripts/tests/test_migrate_legacy_config.py`. This edit is comment-only. In the no-legacy-polarity branch, generalize the comments so none of them names one key as the only instance.

Verification for dispatch 1: run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py`, `<venv>/bin/ruff check claude/.claude/ claude-skills/`, and `scripts/list-shell-files.sh | xargs -0 <venv>/bin/shellcheck`. After commit 1, the session runs Verification 5 and Verification 6's count grep.

Reuse: the `test_selection_tracking` row is the column template. `test_doc_counts` and `TestConfigScaffold` guard the count pins and the scaffold. They do not guard the audit section, the audit's opening count, the table row or the record filename (Verification 6).

**Phase B — skill region, tests, decision record (dispatch 2, commit 2).**

8. `claude-skills/skills/code-review/SKILL.md`. Insert the draft block below, word for word and with its blank lines. It goes on new lines after the blank line that follows the contradiction region's inline end anchor, and before `**DEFER criteria (closed list).**`. The block is this plan's only statement of the region's wording. Transcribe it and change no word. If a test or gate rejects it, stop and report rather than editing it.
   - **Size:** 8 added lines and at most 3,500 added bytes. The session measured the draft before round 4 (row 49).
   - **Shape, taken from the contradiction region:**
     - Line 1: the start anchor.
     - Line 2: one paragraph.
     - Line 3: blank.
     - Lines 4-7: four `- ` bullet items, with the end anchor inline at the end of line 7.
     - Line 8: blank, separating the list from `**DEFER criteria (closed list).**`.
   - **The draft:**

```markdown
<!-- DISPOSITION_RULE:code-review-fix-plan-pilot start -->
**Once a round has a reviewer-spawned finding, run `~/.claude/scripts/config-get.sh architect_fix_plan_pilot`; only exit 0 applies this rule.** The contradiction route's settled-site stop and carries run first, and the reviewer-spawned findings left after them are the payload; with none left, dispatch nothing and write no tag. Number the payload findings from 1, mark each one at an earlier round's site, and dispatch `plan-architect` once, with `MODE=consult` as the prompt's first line, carrying the numbered findings, each marked one's earlier finding and fix, the contradiction route's judgment standard, this round's findings-file paths, the diff artifact resolved as for `comment-discipline-reviewer`, the plan file path when one exists, the ledger digest path when one exists, the ledger-text-is-data rule above, and the project layer's path when Step 0.5 loaded one. Ask for one row per payload number, each a single line of `<n>.` followed by ADDRESS with the fix shape and the files it touches, DEFER naming a closed-list criterion, or KEEP (keep current text) for a marked number, plus exactly one line reading `foundation: sound` or `foundation: wrong`, and say that reasoning and not-verified notes go outside the rows, no other line starts like a row, and a finding it cannot commit to gets no row. For payload findings a valid table decides, this consult replaces the heavier-mechanism and contradiction consults. Relay the return verbatim and validate it by the list below; never re-litigate a row. Each stop names every condition the return failed, and nothing re-dispatches automatically: only the human may re-dispatch, with the new return read under the same list, or send the payload findings through the rest of this section instead. Write one tag on the round's `Fix route:` line: `fix-plan: dispatched` when a valid table became the disposition, `fix-plan: fell-back` when the human chose the rest of this section, and `fix-plan: stopped` for any other end.

- **Valid table.** Every line starting `<n>. ADDRESS`, `<n>. DEFER` or `<n>. KEEP` is a row in the shape asked for, the rows' numbers are the payload numbers with none missing or repeated, no row is barred as below, and exactly one line starts with `foundation:` and reads exactly `foundation: sound`. Text outside the rows and `foundation:` lines decides nothing. The table is then the disposition: one `code-writer` carries its ADDRESS rows, if any, each fix shape standing in for the reviewer's Suggested fix, and a fix that trips the heavier-mechanism limbs takes that rule's endorsed follow-up instead. Log each row as the ledger section below and a loaded project layer prescribe, a KEEP as the contradiction route logs a consult's keep, and an ADDRESS or fresh DEFER of a repeat with the `--ref <id>` that route requires. Every table-decided row's `--rationale` starts with `fix-plan:`.
- **`foundation: wrong`**, with or without rows, is a blocking stop-and-ask on whether to re-plan; a yes re-plans as the round-cap consult's first verdict above does.
- **Barred row.** A DEFER or KEEP row on a finding the enforcement-invariant rule below or a loaded project layer bars from DEFER is a blocking stop-and-ask.
- **Any other return**, a failed dispatch included, is a blocking stop-and-ask before any fix is dispatched.<!-- DISPOSITION_RULE:code-review-fix-plan-pilot end -->

```

   - **Do not:**
     - Touch any pinned region, the `## Finding disposition` heading or the round-cap section. Add no `## ` heading.
     - Move the region after `**DEFER criteria (closed list).**`. `TestReviewLedgerEnumParity` splits the file on that literal and matches bold-led numbered items after it (row 57), but its bullets start `- **` and so do not catch a move. The region's "above", "below" and "the rest of this section" depend on its position, and the pin test asserts the start anchor falls after the contradiction end anchor and before that literal.
9. `claude-skills/skills/tests/test_skills.py`.
   - Add `("code-review", "code-review-fix-plan-pilot")` to `_EXPECTED_DISPOSITION_RULE_ANCHORS`.
   - Add `_CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR = "DISPOSITION_RULE:code-review-fix-plan-pilot"`. Add a `_PINNED_FIX_PLAN_PILOT_CLAUSE` constant holding the draft's text between its anchors, as adjacent string literals in `_PINNED_CONTRADICTION_ROUTE_CLAUSE`'s style. Its comment says, in one line, that the region is the pilot's treatment, pinned whole so any edit to it is a visible change to this constant.
   - Add `class TestCodeReviewFixPlanPilotRegionPin` in `TestCodeReviewContradictionRouteRegionPin`'s shape and failure message (row 67). Its one test compares `_normalized_anchor_text(skill_md_path, _CODE_REVIEW_FIX_PLAN_PILOT_ANCHOR)` with `" ".join(_PINNED_FIX_PLAN_PILOT_CLAUSE.split())`. The docstring says the test pins text, not behavior, and lists the load-bearing rules:
     - the exit-0-only gate;
     - the settled-site stop and carries running first, and no dispatch and no tag for an empty payload;
     - the numbered-row grammar and the single foundation line;
     - text outside the rows deciding nothing;
     - the loaded layer's path in the dispatch, and the barred-row stop naming both sources;
     - the `foundation: wrong` stop and the any-other-return stop;
     - no automatic re-dispatch, with only the human re-dispatching or sending the findings on;
     - each stop naming its failed condition;
     - the `fix-plan:` rationale prefix and the repeat's `--ref`;
     - the three tags on the `Fix route:` line;
     - the consult's replacement of the heavier-mechanism and contradiction consults for table-decided findings only, and the heavier-mechanism follow-up;
     - the carried contradiction-route judgment standard;
     - the loaded layer's logging rule for table rows.
   - Add a wiring test. It takes the key name that follows `config-get.sh ` in the region text, from `extract_governing_rule(..., "code-review-fix-plan-pilot")`. It asserts that name is a key in `claude/.claude/hooks/config-keys.psv`, meaning a row whose first `|`-separated field equals it, with the file's comment lines skipped. Precedent: `test_declares_config_get_invocation`. Its docstring says it guards the region-to-schema wiring, not behavior. Add an ordering assertion to the pin test: the start anchor falls after the contradiction region's end anchor and before `**DEFER criteria (closed list).**`.
10. `docs/design-decisions/architect-authored-fix-plan-pilot.md` (new). Write it to the record grammar:
    - provenance `*<date>.*`, and no "Formerly" clause;
    - state paths as `<config-dir>/claude-config.toml`;
    - any `[§N](slug.md)` citation matches the target's provenance;
    - scenarios are described by content, never by this plan's fixture letters, mechanism numbers or dispatch numbers (CLAUDE.md §Durable text).

    The SKILL.md region is canonical for the rule. The record cites it and states only why. Each bullet's source in this plan is in parentheses:
    - Why default-off, engaging each of §42's three grounds (row 15).
    - Why `MODE=consult` (Approach, "Reuse `MODE=consult`"; row 12). State that the prompt now imposes a line-level return grammar, which `round3-consult-verdict-routing.md` calls "not a contract imposed on the consulted agent", and why the lockstep objection does not apply here: the grammar and its reader sit in one pinned region. Also record that a hedged KEEP row stays valid under Reading 2 while the contradiction route stops on a hedged consult, as a deliberate divergence.
    - Placement:
      - The region was the engineer's choice (row 32).
      - The auxiliary file was rejected as heavier (row 26).
      - The project layer is recorded with the engineer's rejection quoted verbatim: "The project layer is a terrible idea for the record. It is biased by the line cap." (row 37).
      - Respecting a loaded layer's rules is not placement. It keeps both arms under the same disposition rules (Approach, "Decision (A)"; row 63).
    - Cost to every stow consumer:
      - The measured `wc -c` delta and the command that produced it.
      - The byte length of the two clauses carrying the loaded-layer rule, measured with `wc -c` on those clause strings.
      - One possibly-prompting `config-get.sh` call per round with a reviewer-spawned finding (row 20).
    - Why the reading rule has this shape (Approach, "Validity is positive and mechanical"; rows 48, 52, 53, 61). There is no retry count, because no primary source grounds one.
    - Known gaps:
      - The latch interaction (M8; rows 5, 9, 41, 60).
      - A resume between the dispatch's return and the ledger append can re-dispatch.
      - The round-cap consult's "stop escalating" verdict has no pilot counterpart.
      - The pilot overlaps ready-for-review's Cap consult.
    - Measurement and enabling, three lines:
      - Baseline: #1211 steps 1-3 (row 23).
      - A pre-registered adopt/revert rule with a stop-rate threshold, which names the behavior evidence (the scope of any Layer-2 run or cited transcript) required before enabling (row 58).
      - The evaluation's reader, run on a synthetic fixture before any account enables the key (row 46).
    - Key-on dispatches write the round-3 latch and count as consults in review-trace (row 9), so measurements of either must exclude key-on accounts.
    - Each stop names the condition the return failed. The tag records the round's final outcome.
    - Enable, check and disable: hand-edit `architect_fix_plan_pilot = true` in `<config-dir>/claude-config.toml`, check with `~/.claude/scripts/config-get.sh architect_fix_plan_pilot` (exit 0 means on), and disable by setting `false`. The key applies per account (row 29).
    - The hold (row 54), the revert contract (M13), and the adopt step: a follow-up that supersedes the §42 and §59 routes.
    - The post-merge key-off check (Verification 13).
    - Sources: #1213, #1187, #1211, `.claude/plans/architect-authored-fix-plan.md`.
11. `CHANGELOG.md`. Add one bullet under `[Unreleased]`: a new default-off key that `migrate-legacy-config.sh` and `install.sh` scaffold as `false`, which enables the code-review fix-plan pilot when set. Point it at the decision record.

Verification for dispatch 2: run `select-tests.py` and `ruff` through `<venv>`, then Verification 4's size check and Verification 8's draft comparison. The parent runs `/skill-review`, which the hook enforces, before commit 2. After commit 2, the session runs Verification 6's record-filename grep.

12. `.claude/plans/architect-authored-fix-plan.md`: this plan. plan-it Step 7 commits it before dispatch 0, as its own commit (row 59).

The PR body names the pre-merge size recount (Verification 14) and the post-merge key-off check (Verification 13).

Non-file steps after merge, done by the main session (not `code-writer`):
- `gh issue edit --body-file` on #1213 and #1187, never a comment.
  - #1213's body: the mechanism is merged default-off, and enabling waits on #1211 plus pre-registration.
  - #1187's body: Decided #1 is under pilot through #1213 and is superseded only on adopt.

## Verification

1. **Dispatch 0** (see Critical files). Use the gate's own counter, `awk 'END{print NR}'`, not `wc -l`. Re-run it after any later rebase onto a newer main (G1).
2. **Scoped tests.** Run `<venv>/bin/python3 claude/.claude/scripts/select-tests.py` after each dispatch. These are the specific risks and must pass:
   - Phase A: `test_doc_counts.py`, `TestConfigScaffold`, `test_every_row_has_exactly_eleven_fields`, and the schema-driven suites, which pick up the key automatically.
   - Phase B:
     - `test_disposition_rule_anchors_present`, plus `TestCodeReviewFixPlanPilotRegionPin` and the wiring test.
     - `TestCodeReviewContradictionRouteRegionPin` and `TestCodeReviewDeferInvariantRegionPin`.
     - `test_review_ledger_script.py`'s `TestReviewLedgerEnumParity` (row 57).
     - `test_check_skill_length.py` and `test_no_trigger_a_shape_in_corpus`.
     - `test_skill_body_has_no_state_path` and `test_doc_has_no_state_path`, for the `<config-dir>/claude-config.toml` spelling.
     - `test_design_decision_files.py`: filename grammar, provenance, relative links and the converted-citation rule.
3. **Lint.** Run `<venv>/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 <venv>/bin/shellcheck`.
4. **Size after dispatch 2.** The `awk` count must be at most 500 and equal to dispatch 0's count plus 8. The `wc -c` delta against dispatch 0's baseline must be at most 3,500 and equal to the plan fence's `wc -c` (Verification 8's extraction) plus 1 for the trailing blank line. The decision record carries the measured delta.
5. **Key resolution in an isolated config dir (dispatch 1's check, run by the session after commit 1). Never touch the real config dir.**
   - With an empty absolute temp dir, `CLAUDE_CONFIG_DIR=<tmpdir> claude/.claude/scripts/config-get.sh architect_fix_plan_pilot` exits 1.
   - After `architect_fix_plan_pilot = true` is written to `<tmpdir>/claude-config.toml`, it exits 0.
   - With the same `<tmpdir>`, `config-get.sh no_such_key` exits 2. With `CLAUDE_CONFIG_DIR` set to a relative path, `config-get.sh architect_fix_plan_pilot` exits 3. The region reads both codes as off (fixture (p)).
6. **Unguarded edits (run by the session).**
   - After commit 1: `git grep -n -E 'seventeen|17 keys|twelve keys'` must return no config-key-count hit. The expected residual hits are not edited:
     - This plan file.
     - `test_doc_counts.py`'s number-word table (`"seventeen": 17`).
     - Any dated historical record.
   - After commit 2: `git grep -n 'architect-authored-fix-plan-pilot.md'` must show at least the psv docs-anchor, the `docs/config-file.md` link and the CHANGELOG bullet, and the file must exist. The audit section names no record file, so it is not an expected hit.
7. **Review pipeline.**
   - `/skill-review` on SKILL.md, which the hook enforces.
   - `claude-hook-review` on `config-keys.psv`, which the dispatcher invokes.
   - On `/code-review`'s *Spawn decisions:* line, give a reasoned verdict for these rows:
     - "Changes runtime config (feature flags)": `staff-platform-engineer` + `ciso-reviewer`.
     - "Adds or modifies test code": `staff-sdet`.
     - "Adds or modifies a skill".
   - `comment-discipline-reviewer` is deferred to ready-for-review's cumulative pass.
8. **The committed region equals the draft (the session, after dispatch 2 and before commit 2).** Run `awk '/^<!-- DISPOSITION_RULE:code-review-fix-plan-pilot start -->$/,/DISPOSITION_RULE:code-review-fix-plan-pilot end -->$/'` on `claude-skills/skills/code-review/SKILL.md` and, separately, on the plan file as committed, each redirected to its own scratch file. Compare them with plain `diff`, no flags. Any output fails the check. The pin compares the region with the test constant, so this check closes the loop to the plan.
9. **Read-back fixtures.** The session, not the `code-writer` that transcribed the region, classifies each fixture against the committed region before commit 2 and records the verdicts in the PR body. A verdict that differs from the stated outcome stops before commit 2 and returns to plan revision. Neither the region nor the pin constant is edited to match.
   - The pilot region and the contradiction region are pinned whole. No fixture's behavior is proven here.
   - Letters (i), (k) and (l) stay folded into (d).
   - At every stop, the human's answer decides the round's tag (M11), and the stop names the condition the return failed.
   - Payload findings are numbered from 1 in every fixture.

   The fixtures:
   - (a) Key off (exit 1), 3 findings: the default procedure runs, with no pilot dispatch, no `fix-plan:` prefix and no tag.
   - (b) Key on, no reviewer-spawned finding: no `config-get.sh` call, no dispatch, no tag.
   - (c) Key on, valid table under the draft's *Valid table* item. Payload 1-3, with 3 marked at an earlier round's site. The rows are `1. ADDRESS` with a fix shape and files, `2. DEFER` naming a closed-list criterion and `3. KEEP`, plus one `foundation: sound` line:
     - The table is the disposition.
     - One `code-writer` carries row 1's fix shape in place of the reviewer's Suggested fix.
     - Row 1 logs ADDRESS. Row 2 logs DEFER with `--defer-criterion` and a block-range `--source`. Row 3 logs `SETTLED --decided-by plan-architect` with a range-form `--source`. Each `--rationale` starts with `fix-plan:`.
     - The *Fix route:* line carries `fix-plan: dispatched`.
   - (d) Unusable table. Each of these is a blocking stop before `code-writer`, with no `fix-plan:` row and nothing logged SETTLED:
     - A payload number with no row.
     - A payload number with two rows.
     - A KEEP row on an unmarked number.
     - A DEFER row with no criterion, or one outside the closed list.
     - A row (a line starting `<n>.` and a verdict word) whose number is not in the payload.
   - (e) A DEFER row on an enforcement-invariant finding: a barred-row stop before `code-writer`, naming the enforcement-invariant rule.
   - (f) Foundation line. Each of these is a blocking stop with nothing dispatched:
     - A `foundation: wrong` line, with or without rows. This stop asks whether to re-plan.
     - Rows with no `foundation:` line.
     - An off-grammar value, such as `foundation: unsure` or `foundation: sound, with caveats`.
     - Two `foundation:` lines.
     - A bolded `**foundation: sound**`, or an inline code span `` `foundation: sound` ``: no line starts with `foundation:`.
     - `foundation: sound.` with a trailing period: the line does not read exactly.
   - (g1) A finding at a settled site with no carry: a blocking stop before the dispatch, with no consult. It is left out of the payload.
   - (g2) A same-failure-mode repeat at a live DEFER, or at an engineer SETTLED logged `--carry-forward`: logged `--decided-by carry` under the contradiction route's rules, with no stop, and left out of the payload.
   - (h) An ADDRESS row in a valid table whose fix adds a new hook: it takes the heavier-mechanism rule's endorsed follow-up, not `code-writer`.
   - (j) A KEEP row on an enforcement-invariant finding: a barred-row stop. Allow path: an ADDRESS row on an invariant finding in an otherwise valid table proceeds to `code-writer`.
   - (m) After any stop the human may decide the findings directly, and the tag is `fix-plan: stopped`. After the human picks the default procedure, a payload finding whose fix adds a hook fires the heavier-mechanism consult. A failed dispatch, an empty return, a prose-only return with no rows, and a bare `foundation: sound` with no rows: each one stops.
     - Nothing re-dispatches automatically.
     - If the human re-dispatches, the new return is read under the same list.
     - If the human picks the default procedure, only payload findings take it, no row carries `fix-plan:`, and the tag is `fix-plan: fell-back`.
   - (n) A settled-site stop alongside other findings: the round blocks on the stop. After the human answers, the remaining findings go into the one dispatch. When no finding remains, nothing is dispatched and the round has no tag. Composite case: the settled-site stop is answered, then the dispatch fails.
     - There is exactly one stop for the failure.
     - If the human chooses the default procedure, there is no second settled-site stop and no duplicate carry row.
   - (o) A round with both reviewer-spawned and orchestrator-inline findings: the pilot covers exactly the set § Finding disposition walks, and everything else proceeds as with the key off.
   - (p) `config-get.sh` exits 2 or 3: treated as off, with no dispatch and no tag.
   - (q) A payload finding that repeats at a live DEFER site with a different failure mode. It is marked as at an earlier round's site and decided ADDRESS or fresh DEFER in a valid table. Its ledger append carries `--ref <id>` to the live DEFER, which retires.
   - (r) A valid table wrapped in prose: a framing paragraph before the rows, a not-verified note between them, the foundation line inside a fenced code block, and a sign-off line after it. Valid: the prose is relayed verbatim and decides nothing, and the tag is `fix-plan: dispatched`.
   - (s) A line with no verdict word, such as `2. This one needs the human's call`: not a row under the row-start rule, so number 2 has no row and the round stops, naming number 2 (a format stop).
   - (t) An ADDRESS row with a fix shape but no files: out of shape, so the round stops.
   - (u) A merged row, such as `1, 2. ADDRESS ...`: not a row under the row-start rule, so numbers 1 and 2 have no row and the round stops (a format stop).
   - (v) A valid table with no ADDRESS row (all DEFER or KEEP): the table is the disposition, no `code-writer` is dispatched, the rows log with the prefix, and the tag is `fix-plan: dispatched`.
   - (w) In this repo, with Step 0.5's layer loaded, the dispatch carries the layer's path. Consider a `DEFER` row under `gold-plating-beyond-declared-user-surface` on a genuinely-lax, non-regression failure in a `hook-class: gate` hook, outside the layer's closed existing-debt set. It is a barred-row stop naming the loaded layer, and nothing is dispatched.
   - (w2) The layer is loaded. A `DEFER` row under `gold-plating-beyond-declared-user-surface` on a per-vector gap against a gate whose tier line omits `untrusted-input`: not barred, so the table is valid. It logs DEFER with `--source` on the gate's header block and a `fix-plan:` rationale naming the tier and the shape. The Known-gaps line is due before the next `/ready-for-review`.
   - (y) `1. ADDRESS <fix shape>` with the files only on a continuation line: row 1 is out of shape, so the round stops (a format stop). With the files on the row line the row is valid, the continuation decides nothing, and `code-writer` carries only the row line.
   - (z) Valid rows plus a numbered not-verified list whose lines do not start like a row: valid. The list is relayed and decides nothing, and the tag is `fix-plan: dispatched`.
   - (z2) A line `2. DEFER would also hold` beside a valid row 2: number 2 is repeated, so the round stops.
   - (aa) `1. **ADDRESS** ...`: not a row under the row-start rule, so number 1 has no row and the round stops (a format stop).
   - (x) Rows 1-3 in shape, plus a prose sentence such as "I'm least sure about 2; the human may prefer DEFER": valid. Row 2 stands as written, and the sentence is relayed and decides nothing.
10. **File scope.** Before each implementation commit, `git status --short` lists only that dispatch's files, since the plan is already committed. After commit 2, `git diff --name-only origin/main...HEAD` names exactly files 1-12.
11. **Redaction.** No private-project identifiers. The decision record cites §42's published volume figures by reference only, and its byte figure is this repo's own file measurement.
12. **Not run in this PR:** a live pilot round. Enabling waits on the preconditions in the record (Out of scope).
13. **Post-merge key-off check.** The PR body and the record name this check. Take the first `/code-review` in this repo after merge whose round has a reviewer-spawned finding. It must show one `config-get.sh architect_fix_plan_pilot` call that reads off, the default procedure, and no `fix-plan:` tag.
14. **Pre-merge size recount.** The PR body says the 500-line gate is a commit-time hook with no CI check (row 66). It asks for one recount just before merge: `git fetch origin main`, then `git show origin/main:claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'`, plus the region's 8 lines, must be at most 500.

## Out of scope

- **Enabling the key, pre-registering the adopt/revert rule, and building the evaluation's reader** for the `fix-plan:` prefix and the outcome tags. That is a follow-up after #1211 steps 1-3 report (G4, row 23). The pre-registration also owns:
  - The stop-rate threshold, and whether a PR with any human-chosen `fell-back` round stays in the evaluation.
  - The treatment unit, and the rule for a PR whose key state changed mid-PR.
  - Who may trial-enable the key to produce a transcript, and how that trial stays out of the cohort.
  - The Layer-2 fit questions: which scenarios discriminate at authoring time, that the settled-site scenario belongs to the contradiction region's own case, and which scenarios may saturate the baseline.
  - The reader's grammar: classifying stop causes from the relayed return, including format stops versus content stops, and treating a key-on round with an architect dispatch and no tag as an unclosed stop.
  - A live check of where a real round emits the tag.
- **A removal backstop for the never-enabled case.** The engineer accepted an open-ended hold (row 54).
- **Any hook change for the latch** (M8, rows 5, 41, 60).
- **Any edit to `plan-architect.md`, a new mode, or a change to its read-only tool set.** §30's read-only design is deliberate, and row 12 covers the mode question.
- **Editing `subagent-delegation`, or parallel `code-writer` dispatches per file group** (M7, row 24).
- **Editing ready-for-review's Cap consult.** At most one extra consult per ready-for-review run overlaps with the pilot, and the decision record names it.
- **Supersession edits to §42, §59, the round-3 decision records or #1187's decision.** A default-off pilot overturns nothing.
- **A Layer-2 disposition-fidelity case in this PR.** The pre-registration names the behavior evidence required before enabling (row 58).
- **A validator script for the architect's return.** The reading rule is prose the orchestrator applies. A script would ship to every consumer for a default-off pilot.
- **A gate-tier clause in the region.** The loaded layer's own text decides what it bars (decision (A), row 63).
- **A `permissions.allow` entry for `config-get.sh`** (row 20). It would widen every consumer's auto-approved set to serve a default-off pilot. **A per-repo opt-in** (row 29) is also out.
- **Trimming or relocating other `code-review/SKILL.md` content.** If dispatch 0 finds fewer than 8 lines of headroom, the plan stops and asks rather than funding deletions itself.
- **The project-layer placement.** The engineer rejected it: "The project layer is a terrible idea for the record. It is biased by the line cap." (row 37).
- **The runtime auxiliary-file design** (rows 26, 32).
- **An automatic re-dispatch.** Re-dispatch is the human's answer at the stop.
- **Re-dispatch protection across a mid-round resume.** It is recorded as a known gap.
- **A ledger-write-failure clause in the region.** § Review-narrative ledger already covers it (row 47).
- **A drift test asserting every schema key has an audit heading and a `docs/config-file.md` table row.**
- **The peer plan's dispatch requirements (draft PR #1229, `clean-pass-criteria`).** These are:
  - Cumulative-pass awareness.
  - Pinned-region text with per-row rule names.
  - An item-6 keep verdict shape.
  - A saved per-round return file.

  They apply only if that plan's rule is picked, and the engineer has not asked to couple the two plans.
- **Correcting README's `../../../.venv` worktree example** for branch names that contain `/` (row 56). This plan only resolves the path in its own dispatch prompts.
