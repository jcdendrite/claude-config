# Round-3 architect-consult gate: route the consult's return by verdict

## Context

Give the round-3 `plan-architect`-consult gate's own consult a place to land, so a session that complies with the gate's deny message has a rule for what the answer means and where it routes, instead of free-standing prose it must interpret unaided. Today `require-architect-consult.sh` denies a reviewer-persona spawn once a branch is at its round cap without a recent consult, and its deny message tells the session to dispatch `plan-architect MODE=consult` and retry — but `code-review/SKILL.md` never mentions this gate at all, so the consult's return has no prescribed routing and the gate spends its one per-branch firing while the underlying review loop resumes unchanged. This work follows from the merged `round2-pilot-config-key-fix` branch, whose two `plan-architect` consults converged on making the gate's output actionable. A fresh consult on this branch, relayed to and endorsed by the engineer, determined the right fix is a disposition-routing rule added to `code-review/SKILL.md`, not a new `plan-architect` mode. The outcome is that rule plus its minimal supporting edits; `plan-architect` itself and the latch mechanism are unchanged.

## Approach

Close the gap in the `code-review` skill body and in the gate's own deny message, with no new `plan-architect` mode and no change to the latch: a new anchored `DISPOSITION_RULE` block at the end of *Ripple effect triage* tells the session to read the prescribed consult's prose as exactly one of three verdicts — re-plan, stop escalating, or one concrete fix — and routes each to a mechanism that already exists (`plan-it` Step 5's revision re-dispatch, an in-round disposition instruction, an ordinary ADDRESS row to `code-writer`). The deny message gains one clause pointing at that block, so a reviewer spawn denied outside a running `/code-review` can still find the routing rule.

Placement is *Ripple effect triage*, not *Finding disposition*, because the gate fires at spawn time (`require-architect-consult.sh:60-68`) and one of the three verdicts ends the round before any finding exists — a rule under *Finding disposition* would be read too late to act on. The `DISPOSITION_RULE:` namespace still applies: it is the repo's deletion-guard for a single governing paragraph inside a long skill, and both `_EXPECTED_DISPOSITION_RULE_ANCHORS` and `evals/run_skill_evals.py:extract_governing_rule` key on the anchor name, never on position.

Two places where the design refines the endorsed consult rather than transcribing it, both because a literal reading would open a hole:

- **Verdict 2 ("stop reviewing") is scoped to escalation, not to disposition.** A blanket "stop reviewing" reading would function as a sixth DEFER criterion granted by a subagent's prose, which `DISPOSITION_RULE:code-review-defer-invariant` (lines 375-377) forbids absolutely for an enforcement-invariant finding. So verdict 2 means: finish this round on the rows already enumerated, open no new round, and treat the consult's foundation-is-fine judgment as admissible evidence for the *existing* gold-plating-beyond-declared-user-surface DEFER criterion, applied per finding. The closed list stays closed.
- **No verdict discharges a matched Change-type row's spawn obligation.** "The architect consult covered it" is non-specialist scrutiny substituting for a dispatch, which the invalid-skip-rationale class at line 287 already forecloses by its own stated principle. So the denied spawn is retried in verdicts 2 and 3; under verdict 1 the round ends and those rows spawn again against the replaced surface, which is Reconciliation's existing implementation-wrong-shape behavior. Without this sentence the new rule would silently create an escape hatch around the near-mandatory `ciso-reviewer`/`staff-product-engineer` spawns.

The binding constraint on the whole change is length: `check-skill-length.sh` caps `code-review/SKILL.md` at 500 lines and denies `git commit` when a staged SKILL.md is over its limit *and* longer than HEAD. The file is at 483, so the block has a hard 17-line headroom and is designed to 12.

**Assumption ledger**

*Root problem:* the round-cap gate prescribes a consult and prescribes nothing about its return, so a session that complies still has no rule for what the answer means or where it routes — the gate spends its one per-branch firing and the loop resumes unchanged.

*Givens:* none. Every condition this plan depends on and doesn't change — the latch mechanism, `plan-architect`'s mode grammar, the SKILL.md length cap, and the deferred eval-case policy — lives inside this same repo and is in reach (editable by this same PR), so none qualifies as a given per the "beyond this design's own reach" test; each is recorded instead under **Out of scope** below, with why it's in reach and what the design would become without that exclusion.

*Rows:*

1. `_EXPECTED_DISPOSITION_RULE_ANCHORS` is compared as an exact set, so a new anchor fails the suite until registered. `[verified: claude-skills/skills/tests/test_skills.py:1944-1948, 2131-2135]`
2. `code-review/SKILL.md` is 483 lines; its cap is 500; the gate denies only when over-limit **and** grown vs HEAD. `[verified: line count of the file; claude/.claude/hooks/check-skill-length.sh:5-8, 100-110]`
3. The round-cap deny lands at spawn time, inside *Ripple effect triage*'s reviewer dispatch (lines 299-305). `[verified: claude/.claude/hooks/require-architect-consult.sh:60-68; claude-skills/skills/code-review/SKILL.md:299-305]`
4. The gate self-filters on tool name and `subagent_type` only — it also denies a reviewer spawn made with no `/code-review` running, where the skill body is not loaded. `[verified: require-architect-consult.sh:57-68]`
5. The latch releases the gate before the retry, so a retried spawn after the consult is allowed. `[verified: require-architect-consult.sh:116-119; handed-over read of log-reviewer-round.sh:123-140]`
6. The invalid-skip-rationale list's preamble already forecloses any rationale asserting non-specialist scrutiny substitutes for a dispatch, "whether or not it matches the wording below verbatim" — so no sixth bullet is needed for the consult case. `[verified: code-review/SKILL.md:287]`
7. Verdict 3 needs no new delegation text: a `code-review` ADDRESS row naming finding, `file:line`, and a concrete fix is already `code-writer`'s by default, dispatched once per round. `[verified: subagent-delegation/SKILL.md:165-179]`
8. The debug-probe carve-out governs root-causing a failing check, not consuming a recommendation, so it does not pull verdict 3 inline — the operative test is condition (1) of the decision-made test, which holds only when the return names the fix. `[verified: docs/design-decisions/debug-investigation-read-only-probe.md:5; subagent-delegation/SKILL.md:132, 146-154, 179]`
9. Verdict 2 as a blanket DEFER would collide with the closed criteria list and with the never-DEFER invariant rule. `[verified: code-review/SKILL.md:360-377]` The three-verdict shape itself is `[engineer-verified]`; the escalation-only narrowing is this plan's reasoning, flagged here for `/plan-review` to test.
10. The *Spawn decisions:* line already carries additive short tags beyond the per-row verdicts, so an `architect-consult:` tag needs no format change. `[verified: code-review/SKILL.md:234]`
11. `docs/hooks.md:54` enumerates exactly which clauses the deny message names, so it goes stale the moment a clause is added. `[verified: docs/hooks.md:54]`
12. Hook `.sh` files sit in neither citation corpus, so a `§ "Heading"` construct inside the deny string would be unvalidated and free to rot; a plain quoted section name plus an alignment test is the enforceable form. `[verified: test_skills.py:2592-2606 (`_all_skill_md_files`), 3057-3064, 3304-3310 (`_all_doc_paths` + rules + CLAUDE.md)]`
13. `claude/.claude/hooks/tests/test_design_decision_files.py`'s exact assertions were not read; the format requirements below come from `.claude/rules/design-decisions.md`. `[unverified]`
14. The Reconciliation-consistency extractor starts at the literal `## Reconciliation` heading, so inserting a `###` subsection above it cannot perturb that comparison. `[verified: claude/.claude/hooks/tests/test_reconciliation_block_consistency.py:53, 85-88]`

*Mechanisms:*

- **M1 — Anchored `DISPOSITION_RULE:code-review-round-cap-consult-verdict` block, as a `### Round-cap architect consult` subsection ending *Ripple effect triage*.** `anchors: root, row2, row3, row14`. Reader-timing and subject matter both put it at the spawn step, and the anchor is the mechanism this repo already uses to keep one governing paragraph from being deleted out of a 483-line skill. Two lighter homes rejected: a co-located `code-review/REFERENCES.md` entry — that file is edit-time only and never loaded at runtime, so the rule would never reach the session that needs it; a new runtime co-located file on the `plan-review/ROUTING.md` precedent — a third runtime auxiliary file is a heavier structural primitive than 12 lines of prose, and `docs/skills.md`'s architecture note reserves the pattern for content that is load-bearing and cannot be shortened.
- **M2 — Register the anchor and correct the count in its comment.** `anchors: row1`. The exact-set assertion is the enforcement; the "three DISPOSITION_RULE anchor regions" comment above it becomes wrong on the same edit.
- **M3 — One clause in the deny message, pinned by two tests.** `anchors: row4, row12`. Lighter alternative rejected: no hook edit at all — the message is the only surface a reviewer spawn denied outside `/code-review` ever sees, and it currently ends at "retry this reviewer spawn," which is silent on the return the whole change is about. Second lighter alternative rejected: naming the skill without the section — a bare file name does not locate a rule inside a 495-line file and gives the alignment test nothing to pin.
- **M4 — Extend `docs/hooks.md`'s deny-message clause enumeration.** `anchors: row11`. The doc enumerates; leaving it at three clauses is drift, not brevity.
- **M5 — New `docs/design-decisions/round3-consult-verdict-routing.md`.** `anchors: root, row13`. Records the trichotomy, the rejected new-`plan-architect`-mode alternative, the rejected sixth-DEFER-criterion alternative, and the never-discharges-a-spawn-obligation rule. A new file rather than an edit to the gate's own entry: this decides what the gate's output routes into, it overturns nothing in that entry, and the supersession convention is reserved for decisions that do.

**Exact insertion points**

*`claude-skills/skills/code-review/SKILL.md`* — insert at line 306 (after the "Other spawned specialists must return ≤2K tokens…" paragraph at line 305 and its blank line, immediately before `## Reconciliation` at line 307). Twelve new lines, taking the file to 495:

```markdown
### Round-cap architect consult

<!-- DISPOSITION_RULE:code-review-round-cap-consult-verdict start -->
**When `require-architect-consult.sh` denies a spawn, dispatch `plan-architect` with `MODE=consult` as the prompt's first line, relay its return verbatim, and read that return as exactly one of three verdicts.** The consult answers whether this branch's foundation is wrong — never whether a matched Change-type row is covered.

- **Foundation is wrong — re-plan.** The round ends: no further spawns, no fix dispatch. Revise the branch's `.claude/plans/<slug>.md` through `plan-it` Step 5's revision re-dispatch, or run `/plan-it` when the branch has no plan file. The rows enumerated this round spawn again against the replaced surface.
- **Foundation is fine, the review loop is the noise — stop escalating.** Finish this round on the rows already enumerated and open no new one. The verdict re-affirms Step 1's implementation-fitness judgment, so it is evidence a given finding meets the gold-plating-beyond-declared-user-surface DEFER criterion — never a blanket DEFER, and never a new criterion.
- **Foundation is fine, one concrete defect remains — fix it.** Only when the return names the defect's `file:line` and a concrete fix: add it as an ordinary ADDRESS row, routed by `subagent-delegation`'s "Implementation work → `code-writer`" rule like any other. A return that leaves the fix still to be designed is not this verdict.

A verdict never discharges a matched Change-type row's spawn obligation — "the architect consult covered it" is non-specialist scrutiny substituting for the dispatch, which the invalid-skip-rationale list above already forecloses — so retry the denied spawn unless the round ended above. A return that reads as none of the three, or one the orchestrator disagrees with, is a blocking stop-and-ask to the human. When the gate fired this round, report the verdict as an `architect-consult: <verdict>` tag on the *Spawn decisions:* line.
<!-- DISPOSITION_RULE:code-review-round-cap-consult-verdict end -->
```

Authoring constraints on that text: no new `` `target` § "Heading" `` construct — mirror the un-`§` form the two adjacent anchored blocks already use at lines 334 and 353 (`` `plan-it` Step 5's revision re-dispatch ``, `` `subagent-delegation`'s "Implementation work → `code-writer`" rule ``), which cite a step ordinal and a named rule rather than a heading and are already green in the citation tests. Keep the DEFER criterion referenced by its label, not by its number.

*`claude-skills/skills/tests/test_skills.py`* — line 1944-1948, add to the set, and change "three" to "four" in the comment at line 1941:

```python
    ("code-review", "code-review-round-cap-consult-verdict"),
```

*`claude/.claude/hooks/require-architect-consult.sh`* — line 123, replace the clause `then retry this reviewer spawn once it returns.` with:

```
then route its return by the three verdicts in code-review/SKILL.md's 'Round-cap architect consult' section before retrying this reviewer spawn.
```

Single quotes, no backticks: a backtick inside this double-quoted string is command substitution, and a `§` construct here would be validated by nothing (row 12). Nothing else in the message, the header comment block, or the control flow changes.

## Critical files

One `code-writer` dispatch, not several. The file sets are not independently specifiable — the hook test pins a literal that lives in the hook, and the alignment test pins a heading that lives in the skill — so splitting would force the same shared context into two prompts.

- **`claude-skills/skills/code-review/SKILL.md`** *(modify)* — the 12-line block above at line 306. Reuse: the two adjacent `DISPOSITION_RULE` blocks (336-358, 375-377) are the style and structure template; the invalid-skip-rationale class at 287 and the *Spawn decisions:* additive-tag convention at 234 are referenced, not restated.
- **`claude-skills/skills/tests/test_skills.py`** *(modify)* — line 1941 comment count, lines 1944-1948 set entry. No other change: the corpus scan, the ordering check, and the non-triviality check at 2091-2135 all cover the new anchor as-is.
- **`claude/.claude/hooks/require-architect-consult.sh`** *(modify)* — line 123 only.
- **`claude/.claude/hooks/tests/test_require_architect_consult.py`** *(modify)* — `test_deny_message_contents` (line 322): add `assert "Round-cap architect consult" in reason` and extend the docstring's content-requirement list. Leave `test_deny_message_singular_noun_under_pilot` (line 431) alone.
- **`claude/.claude/hooks/tests/test_hook_alignment.py`** *(modify)* — new `test_architect_consult_deny_message_points_at_a_live_skill_section`: read `_MAIN_HOOKS_DIR / "require-architect-consult.sh"`, assert it contains `'Round-cap architect consult'`, and assert `_SKILLS_DIR / "code-review" / "SKILL.md"` contains `### Round-cap architect consult`. Reuse `_SKILLS_DIR` (line 203) and the hook↔skill alignment shape of `test_gate_backed_skill_has_a_live_gate` (325-382), including its "what this does not prove" docstring convention.
- **`docs/hooks.md`** *(modify)* — line 54, extend the deny-message clause enumeration with the verdict-routing pointer, cited as `` `code-review/SKILL.md` § "Round-cap architect consult" `` (proper citation grammar: this is markdown prose citing a skill section).
- **`docs/design-decisions/round3-consult-verdict-routing.md`** *(create)* — H1, blank line, italic provenance line dated with no `Formerly §N` clause, body, `## Sources`. Sources cite `round3-plan-architect-consult-gate.md`, the SKILL.md anchor, `subagent-delegation/SKILL.md`'s `code-writer` default, and this plan file. Filename matches `^[a-z][a-z0-9-]*\.md$`.

Not touched, deliberately: `claude/.claude/agents/plan-architect.md`, `claude/.claude/hooks/log-reviewer-round.sh`, `docs/design-decisions/round3-plan-architect-consult-gate.md`, `claude-skills/skills/subagent-delegation/SKILL.md`, `claude-skills/skills/plan-it/SKILL.md`.

## Verification

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped command. It must select at least `claude-skills/skills/tests/test_skills.py`, `claude/.claude/hooks/tests/test_require_architect_consult.py`, `claude/.claude/hooks/tests/test_hook_alignment.py`, and `claude/.claude/hooks/tests/test_design_decision_files.py`. If it maps none of the changed paths to one of these, that is a bug in its rule table — report it; do not widen the run by hand.
2. Line-count gate, before staging: `code-review/SKILL.md` must land at ≤500 lines (495 expected). This is the check most likely to fail the commit, since `check-skill-length.sh` denies on over-limit-and-grown.
3. `.venv/bin/ruff check claude/.claude/ claude-skills/` for the two test-file edits.
4. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` for the hook edit — the changed line is a single long double-quoted string, so quoting and escaping are the failure modes.
5. Deny-path behavior is covered by `test_deny_message_contents`; no manual hook invocation needed.
6. `/skill-review` on the `code-review/SKILL.md` diff — hook-enforced by `require-skill-review.sh`, blocks the commit until the behavioral-equivalence marker is written. Per `.claude/rules/skill-and-agent-self-review.md`, run a fresh allow/deny fixture pair against the new block: a consult return that names a `file:line` and a concrete fix (must route to verdict 3 / `code-writer`), and one that says the foundation is fine with no concrete defect (must route to verdict 2 and must not produce a DEFER by itself).
7. `claude-hook-review` on the `require-architect-consult.sh` diff — invoked by `/code-review`'s dispatcher for a hook edit, not hook-enforced.
8. `/code-review`. Expect the "Adds or modifies a skill, agent, instruction-file rule, or hook" row to fire, and expect the reviewer-ownership question to be raised and answered on the record: the new rule states that a verdict never discharges a spawn obligation, so it does not reshape reviewer ownership — say so on the `Spawn decisions:` line rather than leaving it implicit.

## Out of scope

- **Any change to `claude/.claude/agents/plan-architect.md`.** In reach — it's this same repo's file, editable in this PR — but declined: the engineer explicitly rejected a new mode/grammar in favor of routing the existing free-prose return, so the trichotomy stays the reading session's rule, not a contract imposed on the agent. Without this exclusion, the design would need a second grammar to maintain in lockstep with `code-review/SKILL.md`'s routing rule, doubling the surface for the same job.
- **Any change to `log-reviewer-round.sh` or the latch.** In reach, but declined: `docs/design-decisions/round3-plan-architect-consult-gate.md` lines 44-56 already rejected a re-arming latch as a compounding-layers tell, and dissolving this exclusion would reintroduce that same rejected re-denial-on-first-fix failure. A draft that finds itself editing `_maybe_write_consult_latch` has drifted off this design.
- **Raising `code-review/SKILL.md`'s 500-line cap, or authoring a `disposition-cases.json` case for the new anchor.** Both are in reach (the cap lives in `check-skill-length.sh`, the eval policy in `evals/README.md`, both this repo's own files) but are separate decisions this plan doesn't need: the new block fits in the existing 17-line headroom without raising the cap, and `evals/README.md` already defers the `disposition-fidelity` corpus to real-transcript mining, so this change ships enforcement via the exact-set anchor assertion alone. Without these exclusions, this plan would be litigating two unrelated repo-wide policies instead of adding one routing rule.
- **Rewriting or superseding `docs/design-decisions/round3-plan-architect-consult-gate.md`**, including adding a forward pointer to the new entry. Nothing in it is overturned, and the new entry cites it in Sources — `git grep` is the documented way across, per the no-index convention.
- **Adding a sixth DEFER criterion, or a sixth invalid-skip-rationale bullet.** Both are deliberate: verdict 2 routes through the existing gold-plating criterion, and the invalid-skip class already covers the consult case by its stated principle. Adding either would be a second site for one rule.
- **Updating `claude/.claude/agents/skill-fidelity-reviewer.md`'s architect-consult check.** Its input is a timeline of reviewer-spawn and architect-consult dispatch rows, which records that a consult was dispatched and cannot observe what the session did with the return — the new obligation is not mechanically checkable from that contract. `[verified: claude-skills/skills/tests/test_skills.py:1061-1074, which pins those Input-contract clauses]`
- **Trimming elsewhere in `code-review/SKILL.md` to buy headroom.** The 12-line block fits inside the existing 17; trimming unrelated prose in a 483-line dispatcher is an Axis-1 scope violation and risks silent behavior change.
