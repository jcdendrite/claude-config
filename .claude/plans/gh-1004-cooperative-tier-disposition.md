# GH-1004: cooperative-tier disposition in the claude-config review layers

## Context

Goal: make the threat-model-tier disposition rule bind `/code-review` and `/plan-review` findings in this repo, covering only the remainder that PR #1086 (GH-1073, commit 783bca5c) left open.

Ask: GH-1004 "Promote proportionate-design judgment for cooperative guardrails into an enforced code-review mechanism". Engineer: "I honestly thought this was already factored into code-review-Claude-config so that's the first thing to check"; "code-review-claude-config and plan-review-Claude-config? If so I agree"; on scope, selected the label "Include both (Recommended)" for also editing `docs/hooks.md:26` and `claude-skills/skills/tests/test_skills.py`.

Why now: #1086 shipped the "Hook threat model" paragraph (`CLAUDE.md:105`), `docs/hooks.md` § "Threat-model tiers", and test-enforced `# tier-threat-model:` headers. No review skill, project layer, or agent mentions tiers, so the base enforcement-invariant rules and the tier waivers have no stated precedence.

Revision: after PR #1218 opened, the engineer said "It skews toward ADDRESS, not DEFER. There are no guidelines reinforcing the cooperative hook model... It feels like we went in the wrong direction with this PR." This revision replaces the first draft's design for the layer sections, the `docs/hooks.md` regression-judging block, and the pins.

## Approach

This revision makes three changes to the branch's change set:

- **Waived findings become DEFER.** A tier-waived finding against a gate is tagged DEFER under criterion 3. It is recorded twice: the PR body's rendered DEFER row is the in-PR record, and one batched Known-gaps line in the gate's header is the durable record.
- **A brand-new gate has no merge-base denials to regress from.** A finding that it dropped a denial is not a regression, and its tier decides how its other findings are handled.
- **Both layers start with GH-1004's own question.** Is the finding a per-vector gap or a genuinely-lax failure? A merge-base gate that stops denying a shape stays blocking at every tier.

All five files already exist on the branch. Every edit below replaces text from the branch's seven existing commits.

The shipped text turned the issue inside out. A waived finding became an in-change ADDRESS edit. Two regression conditions made every finding against a brand-new gate an un-carriable regression:

- "No merge-base counterpart."
- "Edits this section." Every new gate trips this one with its per-hook classification row.

That new-gate loop is exactly GH-1004's motivating case (row 15).

**Alternatives set aside.**
- **Keep the shipped ADDRESS-plus-recording mapping.** It adds an in-change edit and a custom resolved clause for every waived finding, and it leaves the new-gate inversion in place (row 21).
- **Record a waived finding in a new place, such as a ledger field or a docs list.** The engineer ruled this out (row 22).
- **Add a sixth DEFER criterion for tier waivers.** The closed list lives in the global `code-review` skill, which installs to every stow consumer. Criterion 3 already names the declared threat model (row 4).
- **Exempt a per-vector regression from the enforcement-invariant class.** A keep at its stop would then carry forward, and a consult could return keep-current-text, so a second dropped shape at the same block would pass without a stop. A merge-base refactor dropping a denial is rare, and one re-ask per re-raise within a PR is cheap (row 26).
- **Make a merge-base gate's dropped adversarial-only denial DEFER-eligible.** That loses the guard against a refactor silently dropping coverage (row 25).
- **Put the mapping only in `docs/hooks.md`.** Review orchestrators don't load that file (G1). DEFER criteria, ledger flags, and verdicts are review-skill mechanics.
- **Edit the global skills or reviewer agents, or add a hook.** See Out of scope. Disposition happens at no tool-call boundary, so no hook can enforce it.

**Assumption ledger**

Root problem: this branch's two review layers push every finding against a gate toward ADDRESS. A tier-waived finding becomes an in-change edit. Every finding against a brand-new gate becomes an un-carriable regression. Neither layer asks whether a finding is a per-vector gap or a genuinely-lax failure.

Givens:
- G1. A review orchestrator reads `docs/hooks.md` only when a surface it already loads points there. Reason: SKILL.md has no include/import field, and `@path` imports work only in CLAUDE.md. That is harness behavior outside any repo artifact. [unverified: rests on `.claude/rules/skill-and-agent-self-review.md`'s "No shared partials" statement, not a run]

Mechanisms:

- **M1: replace `.claude/skills/code-review-claude-config/SKILL.md`'s `## Finding disposition addition` section with this text.** anchors: root. `code-writer` inserts it verbatim. `/skill-review` may tighten the wording but must keep every bullet's substance:
  - the per-vector question and the genuinely-lax rule
  - a regression is an enforcement-invariant finding at every tier
  - the `untrusted-input` base-rules clause
  - DEFER under criterion 3 with `--source` and `--rationale`
  - the batched Known-gaps line
  - the fail-closed remainder

  It must also keep the trigger and the pointer lead-in.

  ```markdown
  ## Finding disposition addition

  Before dispositioning a finding against a `hook-class: gate` hook, read `docs/hooks.md` § "Threat-model tiers". That section decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

  - Ask first whether the finding is a per-vector gap or a genuinely-lax failure. A per-vector gap needs a shape a cooperative agent would never emit, against a gate whose tier line omits `untrusted-input`. A genuinely-lax failure is the gate's rule failing on a shape a cooperative agent does emit. That section's `cooperative` bullet and its mis-parse paragraph draw the line. A genuinely-lax failure stays under the base rules unless it is already in that section's closed existing-debt set.
  - A regression, as that section defines it, is an enforcement-invariant finding under the enforcement-invariant rule at every tier. No recording, in this diff or an earlier one, changes that.
  - A finding against a gate whose tier line lists `untrusted-input` stays under the base rules.
  - Any other finding that section waives or routes is not an enforcement-invariant finding under the enforcement-invariant rule. Tag it DEFER under criterion 3 (`gold-plating-beyond-declared-user-surface`): the gate's tier line, read with that section's regression-only rule, is its declared threat model. `--source` names the gate's header block. `--rationale` names the gate's tier and the shape.
  - Before the next `/ready-for-review`, add every shape this PR DEFERred against a gate to one line in that gate's header Known-gaps section, creating the section when the header has none and extending that line rather than adding another. That line is the recording that section requires; the PR body's rendered DEFER row is the in-PR record.
  - A finding that section does not explicitly waive or route stays under the base rules.
  ```

  "Enforcement-invariant rule" is the base skill's own name for the `:396` bullet. A DEFER already counts as resolved under `code-review/SKILL.md:473`, so the shipped custom resolved clause and its citation go (row 7).

- **M2: replace `.claude/skills/plan-review-claude-config/SKILL.md`'s `## Gate threat-model tiers (Domain: Security; Output format)` section with this text.** anchors: root. Insert it verbatim, under the same rule as M1.

  ```markdown
  ## Gate threat-model tiers (Domain: Security; Output format)

  Before dispositioning a finding against a `hook-class: gate` hook, read `docs/hooks.md` § "Threat-model tiers". That section decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

  - Ask first whether the finding is a per-vector gap or a genuinely-lax failure. A per-vector gap needs a shape a cooperative agent would never emit, against a gate whose tier line omits `untrusted-input`. A genuinely-lax failure is the gate's rule failing on a shape a cooperative agent does emit. That section's `cooperative` bullet and its mis-parse paragraph draw the line. A genuinely-lax failure stays under the base rules unless it is already in that section's closed existing-debt set.
  - A regression, as that section defines it, stays an enforcement-invariant finding under the fix-or-ask rule at every tier, including a shape the plan newly admits. No recording step changes that.
  - A finding against a gate whose tier line lists `untrusted-input` stays under the base rules.
  - Any other finding that section waives or routes is not an enforcement-invariant finding under the fix-or-ask rule. It still appears in the output and does not block the verdict. When the plan names no step recording it, the verdict's change list adds one step that records every such shape this review surfaced in one line of the gate's header Known-gaps section, creating the section when the header has none.
  - For a gate, S1's bypass-vector enumeration and S2's defense in depth cover every regression and otherwise only the shapes its tier treats as defects.
  - A finding that section does not explicitly waive or route stays under the base rules.
  ```

  "Still appears in the output" keeps `plan-review/SKILL.md:260`'s rule that every finding is rendered. Once M3 lands, a regression exists only against a merge-base gate, so the S1/S2 bullet still enumerates every regression in full while narrowing a brand-new gate to its tier's defect shapes.

- **M3: replace `docs/hooks.md`'s regression-judging block (lines 28–35, from "Judge a regression" through "this section is its only home.") with this text.** anchors: row35.

  ```markdown
  Judge a regression from the gate's behavior at the merge-base against its behavior after the change, covering its matcher, its hook registration (in `settings.json` or its plugin's `hooks/hooks.json`), its early-exit and error paths, and the helpers it calls. The judgment never rests on a reviewer's or a plan's wording. A gate the change adds has no merge-base denials to regress from, so no finding that it dropped a denial is a regression; its tier decides. A gate that renames or replaces merge-base gates, wholly or in part, is not one the change adds; neither is a gate whose status is unclear. Compare such a gate against the merge-base behavior it takes over. For any gate that is not strictly added by the change, including a replacement or a gate of unclear status, treat the finding as a regression when any of these holds:

  - The comparison against the merge-base is unclear.
  - A fail-open path the change adds or alters has no deny test.
  - The change edits the gate's tier line, its tracking pointer, or this section's rules above its per-hook classification table.

  A `CLAUDE.md` or SKILL.md that applies this rule points here rather than restating it; this section is its only home.
  ```

  The "no merge-base counterpart" bullet goes. The "this section" condition stops firing on the classification row every new gate must add (row 35). Treating an unclear status as a replacement fails closed against a refactor relabelled as a new gate.

- **M4: rewrite `_TIER_DISPOSITION_SECTIONS` in `claude-skills/skills/tests/test_skills.py` (lines 3734–3781).** anchors: row9. Reuse `test_gate_tier_disposition_sections_keep_invariant_clauses` and `_section_between` as they are.
  - **`code-review-layer` phrases:**
    - `"per-vector gap"`
    - `"genuinely-lax failure stays under the base rules"`
    - ``"lists `untrusted-input` stays under the base rules"``
    - `"is an enforcement-invariant finding"`
    - `"is not an enforcement-invariant finding"`
    - `"at every tier"`
    - `"does not explicitly waive or route stays under the base rules."`
    - `"No recording"`

    Drop the `` '`code-review/SKILL.md` § "Step — Record review completion"' `` pin.
  - **`plan-review-layer` phrases:**
    - `"per-vector gap"`
    - `"genuinely-lax failure stays under the base rules"`
    - ``"lists `untrusted-input` stays under the base rules"``
    - `"stays an enforcement-invariant finding"`
    - `"is not an enforcement-invariant finding"`
    - `"at every tier"`
    - `"does not explicitly waive or route stays under the base rules."`
    - `"No recording"`
    - `"cover every regression"`
  - **`hooks-doc-regression-rule`:** one contiguous whitespace-normalized string, running from M3's "A gate the change adds has no merge-base denials to regress from" through "this section is its only home.", with each bullet inlined as `- <text> `.
  - **The comment above the list:** replace its layer sentence with three one-fact sentences. The layer entries pin the per-vector question. They pin that a regression stays blocking at every tier. They pin that a finding the tier section does not explicitly waive or route stays under the base rules.

  The dispatch writes M4 first and shows all three pin cases failing against the current text before M1–M3 land.

- **M5: in `docs/hooks.md:44`, change "in the same change;" to "in the same PR;".** anchors: row28. No other wording on that line or on `:56` changes.

Assumptions:
1. The two project layers are the home for this rule. [engineer-verified: "code-review-claude-config and plan-review-Claude-config? If so I agree"] This covers the choice of home, not the wording.
2. Before this branch, neither layer, base skill, nor reviewer agent mentioned tiers. `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:116` mentions them only as a hook-authoring checklist. [verified: grep in the plan's first session; not re-run here]
3. The base rules these layers collide with are `code-review/SKILL.md` `:396`, `:393` and `:473`, and `plan-review/SKILL.md` `:274`, `:222` (S1) and `:224` (S2). [verified: read this session; the resolved list now sits at `:473`, not `:481`]
4. Criterion 3 names Step 1's declared threat model. The ledger accepts it as `gold-plating-beyond-declared-user-surface`. [verified: `code-review/SKILL.md:384`; `claude/.claude/scripts/_review-ledger-lib.sh:15`]
5. Both base invariant rules scope themselves to what "some mechanism currently makes unbypassable." A gate the change adds has no current mechanism, so its gaps fall outside the rule on the rule's own terms. [verified: the text; the reading is the architect's]
6. A project layer may add a section aimed at a named base step. The precedent is `plan-review-claude-config`'s `## User surface (Step 4, question 1)`. `plan-review/ROUTING.md:45` bars narrowing `ciso-reviewer`'s spawn triggers, which M2 does not touch. [verified]
7. A DEFER counts as resolved under the base list, so no layer-specific resolved clause is needed. [verified: `code-review/SKILL.md:473`, "A finding logged DEFER or SETTLED ... counts as resolved"]
8. `docs/hooks.md:35`'s "only home" sentence stays true. The layers point at the regression-only rule and add only its mapping onto base dispositions. [verified: read]
9. The branch's `hooks-doc-regression-rule` pin is the only test over the regression-judging block. `CLAUDE.md:105` is pinned verbatim by `test_hook_threat_model_section_matches_pinned_text`, one reason CLAUDE.md stays untouched. [verified: `test_skills.py:3767-3780`; the CLAUDE.md pin per the plan's first session]
10. `require-skill-review.sh` gates `.claude/skills/**/SKILL.md`, so both layers need a `/skill-review` marker. [verified: `plugins/skill-management/hooks/require-skill-review.sh:201`]
11. `select-tests.py` maps `.claude/skills/**` to SKILLS_TESTS_DIR, and `docs/**` to HOOKS_TESTS_DIR plus SKILLS_TESTS_DIR. [verified: `claude/.claude/scripts/select-tests.py:703`, `:710`]
12. The code-review layer's only § citation is now `docs/hooks.md` § "Threat-model tiers", which `test_threat_model_tiers_citation_resolves_to_real_heading` already covers. [verified: `test_skills.py:3707-3731`]
13. DEFER rows carry no Fix route. The batched Known-gaps line is an ordinary hook-file edit, routed under `subagent-delegation` like any other. [verified: `code-review/SKILL.md:351`; the routing of the header edit is the plan's reading]
14. Reviewer subagents load the repo `CLAUDE.md`, so they may filter some of these findings themselves. [unverified: for `ciso-reviewer` specifically] Not load-bearing.
15. [unverified] GH-1004's motivating case is a new hook whose review looped through many rounds of per-vector findings. The dispatching session relayed this; this agent has no `gh` access and did not read the issue. No review since #1086 has been measured for mis-disposition. This bears on urgency only.
16. `/ready-for-review` treats a DEFER as resolved. [verified: `ready-for-review/SKILL.md:129-130`, "a DEFERred or SETTLED finding counts as resolved"]
17. The round-3 consult gate counts reviewed states, not dispositions. Each header-recording commit therefore adds one reviewed state, and batching one line per PR keeps that cost low. [verified: `require-architect-consult.sh` keys on round state and never mentions DEFER or ADDRESS; the cost bound is the plan's reasoning]
18. [unverified] The orchestrator applies a layer section that isn't a checklist item at the step it names. Nothing in this plan checks it. Verification step 6 says the layers' effect is unmeasured, and the PR body names this as unverified.
19. The engineer settled the `docs/hooks.md` `:13`/`:26` overlap for merge-base gates: a gate that stops denying an adversarial-only shape is blocking (row 25). The layers apply this by pointing at the regression-only rule. `:13` is not reworded.
20. The layers are loaded from the tree under review, so each revised layer governs its own review. [verified: `code-review/SKILL.md:31` and `plan-review/SKILL.md:73` glob the layer from the repo toplevel, which in a linked worktree is the worktree]
21. The problem statement. [engineer-verified: "It skews toward ADDRESS, not DEFER. There are no guidelines reinforcing the cooperative hook model... It feels like we went in the wrong direction with this PR."] This covers the problem statement, not the design.
22. No new recording location. [engineer-verified: "this shouldn't be recorded in a new place"] A gate header with no Known-gaps section gets one created in the same header: [engineer-verified: "Same place, create it (Recommended)"] covers that reading as the question showed it.
23. The engineer selected the consult's recommended recording option. [engineer-verified: "Option 1 the recommended one"]
24. [unverified] This plan states Option 1's content as follows: DEFER under criterion 3; the rendered PR-body DEFER row as the in-PR record; one batched header Known-gaps line per gate per PR as the durable record; no amendment to `:44`/`:56` beyond a same-PR clarification if `:44` is ambiguous. That content is the architect's consult text as relayed by the dispatching session; this agent did not see the option as presented.
25. A merge-base gate that stops denying an adversarial-only shape stays blocking. [engineer-verified: "yes this is important 'guards against a refactor silently dropping coverage'"] The consult's earlier suggestion to call it non-invariant is reversed by row 26.
26. [engineer-verified: "Yes I approve reversal of row 26 and base rules for row 29"] Covers reversing the earlier draft's row 26, which exempted a per-vector regression from the enforcement-invariant class, as the question showed it. A regression of any kind is an enforcement-invariant finding at every tier. Reason: the exemption let a second dropped shape carry or be consult-kept without a stop (`code-review/SKILL.md:378`).
27. [engineer-verified: "Approve all four (Recommended)"] Covers approval of this decision as the question showed it. Plan decision: a pre-existing genuinely-lax gap outside the closed existing-debt set stays under the base rules (ADDRESS). This reads `docs/hooks.md:48`'s "stays in scope for every tier" over `:26`'s pre-existing-gap routing sentence, and it matches the issue's genuinely-lax carve-out as relayed.
28. [engineer-verified: "Approve all four (Recommended)"] Covers approval of the M5 wording change as the question showed it. The architect judges `docs/hooks.md:44`'s "same change" ambiguous next to `:26`'s "same commit". A batched line lands in a later commit than the round that DEFERred it, so M5 clarifies "same PR". The dispatch delegated this judgment.
29. [engineer-verified: "Yes I approve reversal of row 26 and base rules for row 29"] Covers base rules for a pre-existing, non-widened adversarial-only gap on a merge-base `untrusted-input` gate, as the question showed it. Reason: steered input is that gate's declared threat model, so criterion 3's premise is false; new `untrusted-input` gates already take the base rules in (n2). This reads `code-review/SKILL.md:393` over `docs/hooks.md:14`/`:26`, a genuine overlap the engineer settled.
30. `code-review/SKILL.md:399`'s 3+-DEFER heuristic prompts a re-read of the criteria; it does not block. It will fire on a new gate's review, and the per-vector question is that re-read. [verified: `:399`]
31. A same-failure-mode repeat at a live DEFER carries without a stop after the closed list is re-run. A repeat cited outside the DEFER's `--source` block takes a fresh disposition, which criterion 3 yields again. [verified: `code-review/SKILL.md:378`]
32. A round whose staged diff is only a header Known-gaps line bounds each spawn's exhaustive duty to that diff. The comment row defers to `/ready-for-review`'s cumulative pass. [verified: `code-review/SKILL.md:37-52`] [unverified: that in practice reviewers don't re-scan the matcher during that round]
33. [unverified] The issue's question as worded here, per-vector gap versus genuinely-lax failure, was relayed by the dispatch; GH-1004 was not read.
34. The rendered DEFER row reaches the PR body. When a PR is open, `render` runs in the round. Otherwise `/ready-for-review` step 8 runs it. [verified: `code-review/SKILL.md:416-420`]
35. A new gate must add a per-hook classification row because tests enforce agreement between header and table. The shipped "edits this section" condition therefore fired on every new-gate PR. [verified: `docs/hooks.md:52`; `test_skills.py:3767-3780` for the shipped condition]

## Critical files

- `.claude/skills/code-review-claude-config/SKILL.md`: replace `## Finding disposition addition` with M1's text, verbatim.
- `.claude/skills/plan-review-claude-config/SKILL.md`: replace `## Gate threat-model tiers (Domain: Security; Output format)` with M2's text, verbatim.
- `docs/hooks.md`: M3's replacement of lines 28–35 and M5's one-word change at line 44. No other line.
- `claude-skills/skills/tests/test_skills.py`: M4 only. That covers the three `_TIER_DISPOSITION_SECTIONS` entries and their comment. Reuse `test_gate_tier_disposition_sections_keep_invariant_clauses`, `_section_between`, and `_assert_citation_resolves_to_heading` as they are.
- `.claude/plans/gh-1004-cooperative-tier-disposition.md`: this plan revision, committed per `plan-it` Step 7.

Dispatch split: one `code-writer` dispatch covering all four implementation files. They share one vocabulary, and M4's pins depend on M1–M3's exact text. Verification command for the dispatch: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root.

No plugin version bump, because no file under `plugins/` changes. No stow or install step.

## Verification

The worktree has no `.venv` of its own; the venv is the main checkout's (`README.md` Tests section). From the worktree root, invoke tools as `../../../.venv/bin/<tool>`.

1. Run `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k "gate_tier_disposition_sections" -v` after M4 and before M1–M3. Expect all three pin cases to fail on the missing new phrases. Re-run after M1–M3 and M5: all three pass.
2. Run `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k "threat_model_tiers_citation or gate_tier_disposition_sections" -v`. Expect exactly seven cases collected and passing: four citation cases and three pin cases.
3. Run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Take its selection as computed (skills and hooks test directories expected) and expect every test to pass.
4. Run `../../../.venv/bin/ruff check claude-skills/`.
5. `/skill-review` on both layer diffs is hook-enforced at commit and is a static audit, not a fixture runner. Alongside it, read each drafted section against these synthetic scenarios, kept in a scratchpad. For each, state the expected disposition and check that the text yields it:
   - (a) A plain-`cooperative` merge-base gate leaves an adversarial-only shape uncaught, with no merge-base delta.
     - This is a per-vector gap, not a regression.
     - Tag DEFER with `--defer-criterion gold-plating-beyond-declared-user-surface`, `--source` set to the gate's header block, and `--rationale` naming `cooperative` and the shape.
     - The shape joins the PR's one Known-gaps line before the next `/ready-for-review`.
     - No stop-and-ask; it counts as resolved.
   - (b) The same shape is newly admitted on a `cooperative, untrusted-input, irreversible` merge-base gate. This is a regression but not a per-vector gap. ADDRESS or stop-and-ask under the enforcement-invariant rule; a keep is logged with `--enforcement-invariant`.
   - (c) A pre-existing adversarial-only gap in an `untrusted-input` merge-base gate the diff touches, not widened. Base rules (ADDRESS or a tracking issue), never criterion 3 (row 29). If the diff widens it, it is (b).
   - (d) (a)'s shape is raised again in a later round. It carries from the live DEFER; if cited outside that DEFER's `--source` block, it takes a fresh criterion-3 DEFER with `--ref`. Nothing is dispatched and the Known-gaps line does not change.
   - (e) A diff drops a deny pattern for an adversarial-only shape from a plain-`cooperative` merge-base gate and lists the shape in the header's Known gaps in the same diff. This is a regression and an enforcement-invariant finding: ADDRESS or a blocking stop-and-ask, never DEFER, and a keep is logged with `--enforcement-invariant` and without `--carry-forward`. The same-diff listing changes nothing.
   - (e2) As (e), but a later round drops a second shape at the same block while a keep is live. The second drop is asked again; it never carries and a consult never keeps current text.
   - (f) As (e), but the listing was added in an earlier round. Still (e).
   - (g) A gate with no `# tier-threat-model:` line. No waiver; base rules.
   - (h1) A finding on a non-gate hook. Base rules.
   - (h2) A new gap in shared library code a gate calls. Base rules for the library finding; the calling merge-base gate's changed behavior is judged as a regression.
   - (h3) A pre-existing residual gap in shared library code. Base rules, the stricter reading; this plan decision is unchanged from the first draft.
   - (h4) A gate elevated through another gate's backstop header. The elevated tier decides; once it carries `untrusted-input`, no finding against it is a per-vector gap.
   - (h5) A per-vector gap and a genuinely-lax failure on one gate in one diff. Only the first is DEFER; the second is ADDRESS.
   - (i) A `cooperative, irreversible` gate with an adversarial-only gap. DEFER as in (a). A relaxation argued on false-positive cost alone stays under the base rules.
   - (j) A plan-review S2 finding demands a second layer against adversarial-only shapes on a plain-`cooperative` gate. It is rendered but does not block. If the plan has no recording step, the verdict is Approve with changes, adding one step that records every such shape.
   - (k) A plan-review finding that is a regression. Request changes or a blocking stop-and-ask.
   - (l) A plan loosens a plain-`cooperative` merge-base gate so an adversarial-only shape is newly admitted. This is a regression; it stays in S1's enumeration and blocks.
   - (m) A diff edits a merge-base gate's tier line (dropping `untrusted-input`) and a reviewer raises a pre-existing gap. Treated as a regression.
   - (n) A brand-new plain-`cooperative` gate, including its new tier line and its per-hook classification row. No dropped-denial regression is possible; its tier decides. Adversarial-only gaps go as (a); genuinely-lax failures stay under the base rules. A new fail-open path it introduces stays a regression under the unedited `docs/hooks.md:26`.
   - (n2) A brand-new `cooperative, untrusted-input` gate with an adversarial-only gap. Not waived: `docs/hooks.md`'s `untrusted-input` bullet makes the gap live. Base rules apply (ADDRESS by default), never criterion 3.
   - (o) A gate that renames, replaces, or splits a merge-base gate. It is compared against the predecessor's behavior, and a denial the predecessor had but the successor lacks is a regression. A gate whose status as added or replacement is unclear gets the same comparison.
   - (p) Must-yield fixed point: a brand-new plain-`cooperative` gate.
     - Round N raises adversarial-only gap X. X is DEFERred and the Known-gaps line is written.
     - Round N+1 raises X again. It resolves as in (d), with no edit, and the round can close clean.
   - (q) On a plain-`cooperative` gate, a pre-existing genuinely-lax gap in a merge-base gate, outside its existing-debt set. Base rules, ADDRESS (row 27). If the gap is already in that set, it is routed and DEFERred under criterion 3.
6. The layers' effect is unmeasured. `CLAUDE.md:105` is already loaded, and no review has been measured for mis-disposition (row 15), so the scenarios above check the text, not reviewer behavior. The PR body states this rather than claiming an effect.
7. Run `/code-review` on the staged diff. Step 0.5 resolves the worktree toplevel, so the revised layer loads during the review of itself (row 20).
8. After the commit, which needs the `/skill-review` marker the hook requires, `git diff --stat origin/main...HEAD` lists exactly the Critical files.
9. `/pr-description` regenerates PR #1218's body. The body should state that:
   - the disposition is now DEFER under criterion 3, not ADDRESS;
   - a finding that a brand-new gate dropped a denial is not a regression, because it has no merge-base denials;
   - the layers narrow this repo's review strictness against `code-review/SKILL.md:393`/`:396` and `plan-review/SKILL.md:274` for tier-waived non-regression findings only, while a regression stays an enforcement-invariant finding under `:396` and `:274` at every tier;
   - the layers' effect is unmeasured.

## Out of scope

- **The global `claude-skills/skills/code-review` and `plan-review` bodies, including a new DEFER criterion.** They install to every stow consumer, and a carve-out would let any repo be read as outside the invariant. Tiers are this repo's convention.
- **`claude-skills/skills/ready-for-review/SKILL.md:129-130`.** It restates the resolved list that `code-review/SKILL.md:473` owns. That is a single-source defect independent of tiers. It is a candidate follow-up issue; ask the engineer before filing.
- **Reviewer agents (`ciso-reviewer` and others).** They keep reporting. `docs/hooks.md:26` says "route the finding, don't raise it as blocking — never 'don't look'". #1200 owns `ciso-reviewer`'s defense-in-depth posture.
- **`CLAUDE.md:105`.** A test pins it verbatim, and the orchestrator already loads it. That includes its "Non-gate hooks and shared library code get no waiver" sentence, so a new gate whose matcher lives in shared library code stays under the base rules there.
- **`plugins/claude-hook-review/.../SKILL.md:116`.** It is a hook-authoring checklist for consumer repos, and editing it would force a plugin version bump.
- **`DISPOSITION_RULE` anchors and Layer-2 eval cases.** The anchor scan excludes `.claude/skills/`, and no disposition-fidelity eval case ships.
- **Rewording `docs/hooks.md:13`, `:26`, `:48` or `:56`.** The layers point at them. Rows 19, 27 and 29 record how the layers read their overlaps.
- **#1199's admission rule.**
- **`docs/reports/2026-08-22-discovery-audit/findings.md`.** It is a dated report and stays as written.
- **Measuring whether any review since #1086 mis-dispositioned a waivable finding** (row 15).
