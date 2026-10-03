# GH-1004: cooperative-tier disposition in the claude-config review layers

## Context

Goal: make the threat-model-tier disposition rule bind `/code-review` and `/plan-review` findings in this repo, covering only the remainder that PR #1086 (GH-1073, commit 783bca5c) left open.

Ask: GH-1004 "Promote proportionate-design judgment for cooperative guardrails into an enforced code-review mechanism". Engineer: "I honestly thought this was already factored into code-review-Claude-config so that's the first thing to check"; "code-review-claude-config and plan-review-Claude-config? If so I agree"; on scope, selected the label "Include both (Recommended)" for also editing `docs/hooks.md:26` and `claude-skills/skills/tests/test_skills.py`.

Why now: #1086 shipped the "Hook threat model" paragraph (`CLAUDE.md:105`), `docs/hooks.md` § "Threat-model tiers", and test-enforced `# tier-threat-model:` headers. No review skill, project layer, or agent mentions tiers, so the base enforcement-invariant rules and the tier waivers have no stated precedence.

## Approach

Add one pointer-only section to each claude-config review layer. Each section takes a finding that `docs/hooks.md` § "Threat-model tiers" waives or routes and maps it onto the base skill's disposition rules. Such a finding is outside the enforcement-invariant rule. In `/code-review` it is tagged ADDRESS, the in-change action is the recording that section requires, and the finding counts as resolved once that recording exists. Every other finding stays under the base rules. Also reword one sentence in `docs/hooks.md` so its single-source claim stays literally true, and extend the existing citation test and add a section-scoped presence pin so both new pointers and the load-bearing clauses are pinned.

The rule was not already in `code-review-claude-config`. That layer holds only the P1 private-corpus provenance item.

**Alternatives set aside.**
- Do nothing and rely on `CLAUDE.md:105`: leaves the written-rule conflict. An issue-comment recording also leaves the staged diff unchanged, so the marker is never written and reviewers re-raise the finding (row 7).
- Put the mapping only in `docs/hooks.md`: review orchestrators don't load that file (G1), and ledger/`Fix route:`/resolved-counting are `/code-review` mechanics.
- DEFER under criterion 3 instead of ADDRESS: `docs/hooks.md:35` requires an in-change recording, DEFER carries no action, and the "3+ DEFER" heuristic (`:399`) would fire on waiver-heavy PRs.
- Edit the global skills, reviewer agents, or add a hook: see Out of scope. Disposition happens at no tool-call boundary, so no hook can enforce it.

**Assumption ledger**

Root problem: in this repo, `/code-review`'s and `/plan-review`'s disposition rules give `docs/hooks.md` § "Threat-model tiers" no precedence. A finding that section waives or routes therefore has no defined disposition; the base enforcement-invariant rules push it toward fix-or-stop-and-ask, and `/code-review`'s resolved list has no slot for an outcome that is only a recording.

Givens:
- G1. A review orchestrator reads `docs/hooks.md` only when a surface it already loads points there. Reason: SKILL.md has no include/import field and `@path` imports work only in CLAUDE.md; that is harness behavior outside any repo artifact. [unverified: rests on `.claude/rules/skill-and-agent-self-review.md`'s "No shared partials" statement, not a run]

Mechanisms:

- **M1: `.claude/skills/code-review-claude-config/SKILL.md` gains a section after `## Base checklist addition`.** anchors: root. `code-writer` inserts this text verbatim; `/skill-review` may tighten wording but must keep every bullet's substance (regression carve-out, regression test and tie-break, not-an-invariant statement, ADDRESS plus recording plus `--rationale`, resolved clause, fail-closed remainder) and the trigger and pointer lead-in.

  ```markdown
  ## Finding disposition addition

  Before dispositioning a finding against a `hook-class: gate` hook, read `docs/hooks.md` § "Threat-model tiers". That section decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

  - A regression, as that section defines it, stays under the base rules at every tier. No recording, in this diff or an earlier one, changes that.
  - Judge regression from the gate's matcher and its deny tests at the merge-base against the staged state, never from the reviewer's wording or the header's Known gaps. When that comparison is unclear, or the diff edits the gate's tier line or tracking pointer, treat the finding as a regression.
  - Any other finding that section waives or routes is not an enforcement-invariant finding under the enforcement-invariant rule.
  - Tag it ADDRESS. Its in-change action is the recording that section requires, not a fix. `--rationale` names the gate's tier, the recording's location, and the shape it covers.
  - It counts as resolved under `code-review/SKILL.md` § "Step — Record review completion" once that recording exists.
  - A finding that section does not explicitly waive or route stays under the base rules.
  ```

  "Enforcement-invariant rule" is the base skill's own name for the `:396` bullet; the anchor label `defer-invariant` exists only in an HTML comment. "Explicitly" makes the remainder fail closed. The regression bullet is a pointer, not a restatement: `docs/hooks.md:26` owns the definition (a merge-base delta), and the bullet makes an adversarial-only regression on a plain-`cooperative` gate stay blocking, the stricter reading of the `:13`/`:26` ambiguity (row 19). The "already existed before this round" wording is dropped so an earlier-round or same-diff recording cannot legalize a regression.

- **M2: `.claude/skills/plan-review-claude-config/SKILL.md` gains a section after `## User surface (Step 4, question 1)`.** anchors: root. Insert verbatim, same rule as M1.

  ```markdown
  ## Gate threat-model tiers (Domain: Security; Output format)

  For a finding against a `hook-class: gate` hook, `docs/hooks.md` § "Threat-model tiers" decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

  - A regression, as that section defines it, stays under the base rules at every tier, including a shape the plan newly admits. No recording step changes that. When the comparison against the merge-base is unclear, or the plan edits the gate's tier line or tracking pointer, treat the finding as a regression.
  - Any other finding that section waives or routes is not an enforcement-invariant finding under the fix-or-ask rule. It still appears in the output. It blocks the verdict only while the plan names no step making the recording that section requires. When the plan names one, the verdict is Approve with changes and lists that step; when it names none, the verdict names the step required.
  - Subject to the first bullet, S1's bypass-vector enumeration and S2's defense in depth, for a gate, cover only the shapes its tier treats as defects.
  - A finding that section does not explicitly waive or route stays under the base rules.
  ```

  "Still appears in the output" keeps `plan-review/SKILL.md:260`'s every-finding-rendered rule intact. A shape a plan newly admits is a regression, so the S1/S2 narrowing never removes it from the enumeration; the narrowing only drops shapes in pre-existing, unchanged gate behavior.

- **M3: reword `docs/hooks.md:26`'s final sentence.** anchors: row M1. Replace "No `CLAUDE.md` or SKILL.md edit carries this rule; this section is its only home." with "A `CLAUDE.md` or SKILL.md that applies this rule points here rather than restating it; this section is its only home." After M1 and M2, two SKILL.md files apply the rule by pointer, so the literal "carries" reading would conflict with this change. No change to `docs/hooks.md`'s waiver scope. The commit message and PR body also state that M1/M2 narrow this repo's review strictness against `code-review/SKILL.md:393`/`:396` and `plan-review/SKILL.md:274` for non-regression tier-waived findings. [engineer-verified: "Include both (Recommended)"] covers including this edit; the wording is the architect's proposal.

- **M4: add both layer paths to `test_threat_model_tiers_citation_resolves_to_real_heading`'s parametrize list** (`claude-skills/skills/tests/test_skills.py:3706-3723`), update its docstring to say its unique value over the repo-wide citation tests is presence, and add one small test, `test_gate_tier_disposition_sections_keep_invariant_clauses`, a presence pin scoped to each layer's new section (not the whole file): the phrases `explicitly waive or route`, `at every tier` (M1 and M2), `changes that` (M1 and M2), and for M1 the `Step — Record review completion` citation (precedent for pinning prose: `claude/.claude/hooks/tests/test_hook_alignment.py:1893`). anchors: row M1. Reuse `_assert_citation_resolves_to_heading` as-is. The test whitespace-normalizes (`" ".join(text.split())`) before the substring checks, so a benign re-wrap does not break it, and it also pins presence of M1's `code-review/SKILL.md` citation. It is a tripwire, deliberately weaker than the verbatim `CLAUDE.md:105` pin, because `/skill-review` may tighten the layers' wording. The dispatch writes it first and shows it failing before M1/M2 land. The pin keeps a later wording trim from silently dropping the fail-closed or regression clauses. [engineer-verified: "Include both (Recommended)"] covers including this edit.

Assumptions:
1. The two project layers are the home for this rule. [engineer-verified: "code-review-claude-config and plan-review-Claude-config? If so I agree"] Covers the choice of home, not the wording.
2. Neither layer, base skill, nor reviewer agent mentions tiers today; `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:116` mentions them only as a hook-authoring checklist. [verified: grep for `tier-threat-model|Threat-model tiers|cooperative agent` returns no SKILL.md or agent file under `claude-skills/skills/` or `claude/.claude/agents/`; it also hits the existing citation test in `claude-skills/skills/tests/test_skills.py`]
3. Colliding base rules: `code-review/SKILL.md` `:396`, `:393`, `:481`; `plan-review/SKILL.md` `:274`, `:222` (S1), `:224` (S2). [verified: read]
4. Precedent for ADDRESS with a non-fix action: `code-review/SKILL.md:378`'s *keep current text* is logged as ADDRESS with the verdict in `--rationale`. [verified]
5. Both base invariant rules scope themselves to what "some mechanism currently makes unbypassable"; `docs/hooks.md:13` and `:26` define what this repo's gates treat as defects. M1/M2 apply the base rule's own term rather than exempting anything. [verified: the text; the reading is the architect's]
6. A project layer may add a section aimed at a named base step; precedent is `plan-review-claude-config`'s `## User surface (Step 4, question 1)`. `plan-review/ROUTING.md:45` bars narrowing `ciso-reviewer`'s spawn triggers, which M2 does not touch. [verified]
7. Without M1's resolved clause, an issue-comment recording leaves the ADDRESS row unresolved under `:481` and the review loops. [verified: `code-review/SKILL.md:481`, `:488`]
8. `docs/hooks.md:26`'s "only home" sentence is literally true today; `CLAUDE.md:105` does not state the regression-only rule. [verified]
9. No test pins `docs/hooks.md:26`'s wording; `CLAUDE.md:105` is pinned verbatim by `test_hook_threat_model_section_matches_pinned_text`, one reason `CLAUDE.md` stays untouched. [verified: grep]
10. `require-skill-review.sh` gates `.claude/skills/**/SKILL.md`, so both layers need a `/skill-review` marker. [verified: `plugins/skill-management/hooks/require-skill-review.sh:177`]
11. `select-tests.py` maps `.claude/skills/**` to SKILLS_TESTS_DIR and `docs/**` to HOOKS_TESTS_DIR plus SKILLS_TESTS_DIR. [verified: `claude/.claude/scripts/select-tests.py:701`, `:708`]
12. M1's citation `` `code-review/SKILL.md` § "Step — Record review completion" `` resolves from a `.claude/skills/` layer; the heading exists at `code-review/SKILL.md:479`. [verified: `test_skills.py:3516-3556`]
13. Fix route: a tracking-issue comment is not code and stays inline; a hook-header Known-gaps edit takes `code-writer`. [verified: `subagent-delegation/SKILL.md:176` lists a plan-file edit and a `respond-pr` reply as "Not code"; the tracking-issue comment is the plan's reading of that list]
14. Reviewer subagents load the repo `CLAUDE.md`, so they may filter some of these findings themselves. [unverified: for `ciso-reviewer` specifically] Not load-bearing.
15. [unverified] No review since #1086 has been seen mis-dispositioning a waivable finding; this closes a written-rule conflict, not an observed failure. Bears on urgency only.
16. [unverified] `/ready-for-review` honors M1's resolved clause; its `:131` parenthetical restates the base list. Worst case is one extra pass or a Cap consult.
17. [unverified] Whether a round whose only findings are recordings counts toward the round-3 consult trigger in `require-architect-consult.sh`.
18. [unverified] The orchestrator applies a layer section that isn't a checklist item at the step it names. Nothing in this plan checks it; step 5's unmeasured statement covers it, and the PR body names it as unverified.
19. `docs/hooks.md` is ambiguous on whether a merge-base regression admitting only an adversarial-only shape on a plain-`cooperative` gate is waived under `:13` or blocking under `:26`/`:39`. Plan decision, the stricter reading: M1/M2's regression bullet keeps it under the base rules. Settling the docs ambiguity itself stays out of scope. [unverified: the stricter reading is the plan's choice, not the engineer's]
20. Both `ciso-reviewer` and `staff-sdet` (round 1) confirmed the layers are loaded from the tree under review, so the layer governs its own review (F7). Accepted: the PR body states it. [verified: `code-review/SKILL.md:31` and `plan-review/SKILL.md:73` glob the layer from the repo toplevel, which in a linked worktree is the worktree]

## Critical files

- `.claude/skills/code-review-claude-config/SKILL.md`: add M1's section verbatim after `## Base checklist addition`.
- `.claude/skills/plan-review-claude-config/SKILL.md`: add M2's section verbatim after `## User surface (Step 4, question 1)`.
- `docs/hooks.md`: M3's one-sentence replacement at line 26 only.
- `claude-skills/skills/tests/test_skills.py`: M4, two parametrize entries, the docstring update, and the presence pin. Reuse `_assert_citation_resolves_to_heading`.
- `.claude/plans/gh-1004-cooperative-tier-disposition.md`: this plan, committed per `plan-it` Step 7.

Dispatch split: one `code-writer` dispatch covering all four implementation files; they share one vocabulary and M4 depends on M1/M2's exact citation text. Verification command for the dispatch: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root.

No plugin version bump (no file under `plugins/` changes). No stow or install step.

## Verification

The worktree has no `.venv` of its own; the venv is the main checkout's (`README.md` Tests section). From the worktree root, invoke tools as `../../../.venv/bin/<tool>`.

1. `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`; expect it to select the skills and hooks test directories (take the selection as `select-tests.py` computes it) and all passing, including the citation tests over M1's new `code-review/SKILL.md` citation.
2. `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k "threat_model_tiers_citation or gate_tier_disposition_sections" -v`: exactly four citation cases plus two pin cases (one per layer) collected and passing, two of the citation cases being the new layer paths.
3. `../../../.venv/bin/ruff check claude-skills/`.
4. `/skill-review` on both layer diffs (hook-enforced at commit) is a static audit, not a fixture runner. Alongside it, read each drafted section against these synthetic scenarios (kept in a scratchpad), stating the expected disposition and checking the text yields it:
   - (a) Adversarial-only shape left uncaught in a plain-`cooperative` gate, no merge-base delta: ADDRESS, header Known-gaps recording, `--rationale` naming `cooperative`, no stop-and-ask.
   - (b) Same shape introduced on a `cooperative, untrusted-input, irreversible` gate: base rule (ADDRESS fix or stop-and-ask).
   - (c) Pre-existing adversarial-only gap in an `untrusted-input` gate the diff touches, no merge-base delta, the gap not widened: ADDRESS plus recording, per `docs/hooks.md:14` and `:26` (accepted debt, not a blocking finding). A gap the diff widens is a regression.
   - (d) Same gap as (a) raised again after its recording landed: resolved, nothing dispatched.
   - (e) A diff drops a deny pattern and lists the shape in the header's Known gaps in the same diff: regression, base rules, not resolved.
   - (f) The same regression with the recording added in an earlier round: regression, base rules.
   - (g) A gate with no `# tier-threat-model:` line: no waiver, base rules.
   - (h1) A finding on a non-gate hook: base rules.
   - (h2) A new gap in shared library code a gate calls: base rules.
   - (h3) A pre-existing residual gap in shared library code: base rules, the stricter reading. `docs/hooks.md`'s dependency-invariant paragraph treats it as accepted debt; the plan keeps M1's gate-only trigger rather than add a shared-code branch (a plan decision beside row 19, not the engineer's).
   - (h4) A gate whose header names another gate as its backstop: elevated per `CLAUDE.md:105`; the elevated tier decides.
   - (h5) A waived finding and a real finding on the same gate in one diff: only the waived one takes M1's path.
   - (i) A `cooperative, irreversible` gate with an adversarial-only gap: waived as in (a), but never relaxed on false-positive cost.
   - (j) plan-review S2 finding demanding a second layer against adversarial-only shapes on a plain-`cooperative` gate: appears in the output; blocks only while no recording step is named.
   - (k) plan-review finding that is a regression: base rules.
   - (l) A plan loosens a plain-`cooperative` gate so an adversarial-only shape is newly admitted: the shape stays in the S1 enumeration and is a regression.
   - (m) A diff edits a gate's tier line (drops `untrusted-input`) and a reviewer raises a pre-existing gap in the same diff: treated as a regression, base rules.
   - (n) A replacement or brand-new gate: comparison unclear, treated as a regression.
5. The layers' effect is unmeasured: `CLAUDE.md:105` is already loaded and no mis-disposition has been observed (row 15), so the scenarios above cannot show the layer is the cause. The PR body states this rather than claiming an effect.
6. `/code-review` on the staged diff; Step 0.5 resolves the worktree toplevel, so the new layer loads during review of itself.
7. After the commit (with the `/skill-review` marker the hook requires), `git diff --stat main...HEAD` lists exactly the Critical files.

## Out of scope

- Global `claude-skills/skills/code-review` and `plan-review` bodies: they install to every stow consumer, and a carve-out would let any repo be read as out of the invariant; tiers are this repo's convention.
- `claude-skills/skills/ready-for-review/SKILL.md:131`: restates the resolved list `code-review/SKILL.md:481` owns, a single-source defect independent of tiers (row 16). Candidate follow-up issue; ask the engineer before filing.
- Reviewer agents (`ciso-reviewer` and others): they keep reporting; `docs/hooks.md:26` says "route the finding, don't raise it as blocking — never 'don't look'". #1200 owns `ciso-reviewer`'s defense-in-depth posture.
- `CLAUDE.md:105`: pinned verbatim by a test and already loaded by the orchestrator.
- `plugins/claude-hook-review/.../SKILL.md:116`: a hook-authoring checklist for consumer repos; editing it would force a plugin version bump.
- `DISPOSITION_RULE` anchors and Layer-2 eval cases: the anchor scan excludes `.claude/skills/`, and no disposition-fidelity eval case ships.
- Evidence for a recording that lives only in an issue comment (PR-body link, `code-review/SKILL.md:416`'s DEFER persistence): the `docs/hooks.md:35` obligation is pre-existing and unenforced; M1's `--rationale` names the recording's location and shape as the only added evidence.
- The PR-body link `docs/hooks.md:35` requires for an issue-comment recording: pre-existing obligation, unchanged.
- Resolving the regression-versus-waiver question for adversarial-only regressions on plain-`cooperative` gates (row 19): a waiver-scope change to `docs/hooks.md`.
- #1199's admission rule, and the "one collapsing rule" design principle (global `CLAUDE.md` compounding-layers bullet).
- `docs/reports/2026-08-22-discovery-audit/findings.md`: a dated report, stays as written.
- Measuring whether any review since #1086 mis-dispositioned a waivable finding (row 15).
