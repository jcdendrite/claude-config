# Plan: require the human's answer before a cooperative-emitted gap becomes existing debt (GH-1004 follow-up)

## Context

Goal: close the one present lax path left by merged #1218 (GH-1004): the "closed" existing-debt set in `docs/hooks.md` can grow without the engineer, so a gap an honest agent can hit on an irreversible gate can be accepted.

Ask: engineer said, quote: "We can have it be my call for line 55." and, quote: "And yeah root A follow up". The change set was recommended by `plan-architect` in a consult (the architect's words, not the engineer's); the engineer approved starting the follow-up.

Why now: a `plan-architect` consult after the merge found it (Root A), and a later review finding disproved the premise of a DEFER row recorded in #1218's PR body.

Intended outcome: on any gate, a gap on a shape a cooperative agent emits becomes existing debt only after the engineer answers a blocking stop-and-ask. That holds wherever the gap is written, including a tracking pointer, and the engineer's answer must accept it as permanent debt. Line 55 points at that rule instead of letting an agent record alone. The code-review layer asks the engineer before a genuinely-lax gap is left unfixed (D3), and its Known-gaps step records only criterion-3 DEFERs and the engineer's own permanent keeps (D2). Pins cover the DEFER ban, the ask, the permanence disclosure, the criterion-3 qualifier, line 63's engineer-row clause, line 59's disclosure-pointer sentence, line 55's pointer sentence, the **Existing debt** lead, and D0's sentences. The other new layer sentences are unpinned, each with a backstop named in Out of scope.

Open at draft time: no decision is left for the engineer. The session asked "Which reading did you mean by 'my call' for line 55?" and the engineer selected the label "(a) I decide permanent debt (Recommended)". The option's description was the session's proposal, not the engineer's words. It said the decision applies on any gate through a blocking stop-and-ask, and that a per-PR base DEFER stays allowed under existing rules. The reach to every gate rests on that description and is unverified (rows 2-3). The engineer then asked "Am I wrong?" about the earlier draft's size. A `plan-architect` consult recommended the reduced edit list this plan carries, and the session reports that the engineer asked to proceed with it (row 5). Reading (b) was not chosen. It would have barred every unfixed outcome, a per-PR base DEFER included.

## Approach

Two sentences are appended to `docs/hooks.md`'s **Existing debt** paragraph. They make the human's answer to a blocking stop-and-ask, accepting the gap as permanent debt, the only way a gap on a shape a cooperative agent emits becomes existing debt, on any gate and wherever the gap is written. Line 55's routing sentence becomes a pointer to that rule. The code-review layer's Known-gaps step stops recording base-rules DEFERs. Pins guard the new and narrowed text.

- **D0 (line 20, with line 26).** Append two sentences that redefine when a later gap counts as "recorded this way". Line 20's two existing sentences stay byte-identical. Line 26 is untouched: the second appended sentence's "no text or pointer records it, wherever it sits" governs it from line 20, with no site list to go stale.
- **D1 (line 55).** Replace only the sentence that offers "or recorded" with a pointer to **Existing debt**.
- **D2 (code-review layer, line 63).** Record only criterion-3 DEFERs, and every genuinely-lax failure the engineer's own SETTLED row kept with `--carry-forward` (never a carry row or a regression), in a gate's Known-gaps line. A base-rules DEFER stays a per-PR record.
- **D3 (code-review layer, line 59).** Append five sentences: outside the closed set, a genuinely-lax failure that is not a regression is never DEFERred; leaving it unfixed takes a blocking stop-and-ask that tells the engineer a keep with no scope or time limit admits it as permanent existing debt; that stop also gives the base contradiction-route rule's human-keep disclosures before the engineer answers (including that the quote is published in the PR body); the engineer's keep is logged SETTLED `--decided-by engineer` as the base contradiction-route rule logs a human keep, with `--carry-forward` only under that rule's conditions, and any other keep is logged without it and asked again on each re-raise. This supplies the ask and the record that D0's rule needs, so an unanswered gap stops being re-raised on every later PR.

Alternatives weighed and set aside:

- **The earlier, fuller draft.** It had a merge-base re-anchor and in-change-listing bar at line 20, a quoted-answer requirement, a line-13 tie-break, whole-sentence layer re-pins, and a mutation red step. The engineer asked "Am I wrong?", and the consult found those parts redundant. Once only the human's answer admits an emit-shape gap, a listing the change adds without that answer is already barred. The rest guards text this change does not edit, or a misjudgment rather than D0-D2.
- **Patch line 55's three gates only.** That leaves D0 and D2 open. The defect is who decides, so one rule at the set's definition reaches every gate and every recording site.
- **Key the no-ask branch to "a shape this section waives".** Line 13 waives nothing on an `untrusted-input` gate. Line 14's pre-existing per-vector debt would then newly need an ask, which reading (a) does not cover (rows 2, 11).
- **Edit line 26.** The appended sentences already govern it from line 20 (row 12).
- **Delete line 63's step outright.** Line 43 still requires a waived finding to be recorded in the same PR, and line 63 is the layer's only step that does that.
- **Restate the rule in the layers.** Both layers already defer to "that section's closed existing-debt set". A restatement would duplicate the single home.
- **Append the new sentences to the one-string `hooks-doc-regression-rule` pin.** Any edit would fail the whole string without naming the missing clause.
- **Mechanical ledger refusal.** See G2.

### Prescribed text

Each edit stays inside its existing line, so no line number shifts.

- **`docs/hooks.md:20`.** Keep both existing sentences byte-identical. Append this after "...until it, too, is recorded this way.", on the same line:
  > Except for a gap that needs a shape a cooperative agent would never emit, a gap discovered later counts as recorded this way only once the human's answer to a blocking stop-and-ask accepts that gap as permanent debt, and the commit message or PR body that records it states the rationale. Until then no text or pointer records as debt a gap discovered later that needs a shape a cooperative agent does emit, wherever it sits, even where another sentence of this section treats it as debt or routes it without a blocking finding.
- **`docs/hooks.md:55`.** Replace only the sentence "A routine encoding a cooperative agent emits, such as a base64 credential in a Kubernetes Secret manifest, is not a waivable shape: such a finding is fixed in the change, or recorded in the gate's header Known-gaps section with the rationale stated in the commit message." with:
  > A routine encoding a cooperative agent emits, such as a base64 credential in a Kubernetes Secret manifest, is not a waivable shape, so the **Existing debt** rule above decides whether a finding on it may be recorded.

  Every other sentence of line 55 stays byte-identical.
- **`.claude/skills/code-review-claude-config/SKILL.md:59`.** Append after "...closed existing-debt set.":
  > Outside that set, a genuinely-lax failure that is not a regression is never DEFERred. Leaving it unfixed takes a blocking stop-and-ask that tells the engineer a keep with no scope or time limit admits it as permanent existing debt. Before the engineer answers, that stop also gives the base contradiction-route rule's human-keep disclosures. Log the engineer's keep SETTLED `--decided-by engineer` as the base contradiction-route rule logs a human keep, adding `--carry-forward` only under that rule's conditions for it. Log any other keep without it, and ask again on each re-raise.
- **`.claude/skills/code-review-claude-config/SKILL.md:63`.** The worktree already carries the criterion-3 edit to the first sentence, staged. Set the whole line to its final text, whatever its current state: the first sentence reads "Before the next `/ready-for-review`, add every shape this PR DEFERred under criterion 3 as a waived or routed finding against a gate, and every genuinely-lax failure the engineer's own SETTLED row kept with `--carry-forward` (never a carry row or a regression), to one line in that gate's header Known-gaps section." The next two sentences stay unchanged. The last sentence reads "...the PR body's rendered DEFER or SETTLED row is the in-PR record."
- **`claude-skills/skills/tests/test_skills.py` `_TIER_DISPOSITION_SECTIONS`.**
  - Append this entry after `hooks-doc-regression-rule`. The split literals keep each source line under ruff's 130 columns and join with exactly one space:
    ```python
        pytest.param(
            "docs/hooks.md",
            "## Threat-model tiers",
            [
                "Except for a gap that needs a shape a cooperative agent would never emit, "
                "a gap discovered later counts as recorded this way only once the human's answer "
                "to a blocking stop-and-ask accepts that gap as permanent debt, and the commit "
                "message or PR body that records it states the rationale.",
                # The predicate must be "does emit": a broader modal reading would override the
                # never-emit recordings the section deliberately allows.
                "Until then no text or pointer records as debt a gap discovered later that needs "
                "a shape a cooperative agent does emit, wherever it sits, even where another "
                "sentence of this section treats it as debt or routes it without a blocking finding.",
                "A routine encoding a cooperative agent emits, such as a base64 credential in a "
                "Kubernetes Secret manifest, is not a waivable shape, so the **Existing debt** rule "
                "above decides whether a finding on it may be recorded.",
                "**Existing debt** is a closed set",
            ],
            id="hooks-doc-debt-admission",
        ),
    ```
  - `code-review-layer` entry: append these six phrases after `"No recording",`, in this order:
    - `"DEFERred under criterion 3 as a waived or routed finding against a gate",`
    - `"that is not a regression is never DEFERred",`
    - `"admits it as permanent existing debt",`
    - `"takes a blocking stop-and-ask",`
    - ``"the engineer's own SETTLED row kept with `--carry-forward`",``
    - `"Before the engineer answers, that stop also gives the base contradiction-route rule's human-keep disclosures.",` (split across two literals in the file)
  - Keep every existing phrase in `code-review-layer`, `plan-review-layer`, and `hooks-doc-regression-rule` unchanged.
  - Comment block:
    - Change "# The docs/hooks.md entry pins the regression-judging block in its single home." to "# The `hooks-doc-regression-rule` entry pins the regression-judging block in its single home."
    - After it, add two lines:
      - "# The `hooks-doc-debt-admission` entry pins the existing-debt admission rule and the disclosure-gate pointer to it."
      - "# The code-review-layer phrases for the DEFER ban, the ask and the Known-gaps recording pin the ask-and-record path."

**Dispatch split.** One `code-writer` dispatch, because the three edited files must carry identical strings.

**This PR's own review.** The diff edits the section's rules above its per-hook classification table. Under `docs/hooks.md:32`, every finding against a gate in this PR's own review is therefore a regression: ADDRESS or a blocking stop-and-ask, never DEFER (row 25). No hook file is in Critical files, so ADDRESSing a finding against a hook widens the file list and needs the engineer's answer first.

### Assumption ledger

**Root:** on any gate, `docs/hooks.md`'s existing-debt closed set can grow on a shape a cooperative agent emits without the human's answer, through three present paths: D0, line 20's growth sentence plus line 26's unscoped routing (row 6); D1, line 55's agent-alone "or recorded" branch (row 7); D2, the code-review layer's line-63 step recording base-rules DEFERs (row 8).

**Givens:**
- G1. The base `code-review` disposition rules stay as written. They are a global skill installed to every stow consumer, and changing them needs a decision outside this repo-local follow-up.
- G2. A substring pin checks only that text is present, not that a reviewer follows it (`claude-skills/skills/tests/test_skills.py:3786-3788`, at merge-base 9eab0a52). The script that could refuse an unanswered recording, `claude/.claude/scripts/review-ledger.sh`, is stowed to every consumer, and changing it needs a decision outside this plan.

**Rows:**
1. The engineer settles the unfixed branch of line 55. [engineer-verified: "We can have it be my call for line 55."]
2. Asked "Which reading did you mean by 'my call' for line 55?", the engineer chose that they decide permanent debt. [engineer-verified: "(a) I decide permanent debt (Recommended)"]
3. The selected option's description was the session's proposal: the decision applies on any gate through a blocking stop-and-ask, and a per-PR base DEFER stays allowed under existing rules. It carries the reach to every gate. [unverified] When the session put the three deliverables (D0, D2, pins) to the engineer, they answered: "I think all three but I want their opinion". [engineer-verified: "I think all three but I want their opinion"] A `plan-architect` consult then recommended keeping all three (the architect's recommendation, not the engineer's words). It also called pinning line 55's sentence a minor call. This plan keeps that pin, as the prescribed entry, M4, and Verification step 1 state, until the engineer says otherwise. After code-review round 1 returned three regression-class findings, the engineer selected the label "Architect consult first (Recommended)", then selected "Apply the rewrite (Recommended)" to the question whether to apply the architect's single line-20 rewrite. The rewrite text is the architect's, not the engineer's words. The session then changed the second sentence's subject from "it" to "a gap discovered later that a cooperative agent could emit" and its "such a gap" to "it", because two plan-review reviewers found the architect's wording unscoped against lines 13, 14 and 43, and a code-review reviewer found the first adaptation dropped the "discovered later" bound. That adaptation is the session's and has no engineer approval beyond the quote below. [engineer-verified: "Apply the rewrite (Recommended)"] In the cumulative review the second sentence's "could emit" was replaced by "needs a shape a cooperative agent does emit", mirroring the first sentence's predicate, because "could emit" covers nearly every shape and so overrode the never-emit recordings at lines 13, 14, 43 and 55. The replacement wording is the architect's, not the engineer's. [engineer-verified: "Apply the architect's fix"]
4. The engineer approved starting this follow-up. [engineer-verified: "And yeah root A follow up"]
5. The engineer questioned the earlier draft's size. [engineer-verified: "Am I wrong?"] The reduced edit list is a `plan-architect` consult's recommendation. The session reports that the engineer then asked to proceed with it, and no quote of that request was relayed. [unverified]
6. D0: line 20 lets the set grow "until it, too, is recorded this way", and line 26 sends "A pre-existing gap a review happens to surface" to the tracking issue. Neither puts any bar on who decides. [verified: `docs/hooks.md:20,26`]
7. D1: line 55 lets an agent alone record an emit-shape finding in the gate's header Known-gaps section, which it calls the gate's existing-debt closed set. Layer line 59 sends a genuinely-lax failure to the base rules unless it is already in that set. Line 62 DEFERs "Any other finding that section waives or routes" under criterion 3, and line 63 records that DEFER. [verified: `docs/hooks.md:55`; `.claude/skills/code-review-claude-config/SKILL.md:59,62,63`]
8. D2: line 63 records "every shape this PR DEFERred against a gate", including a genuinely-lax failure DEFERred under base criterion 1, 2, or 5. Once merged, that line is header Known-gaps text, which line 55 calls the closed set and line 20 counts, and no human answered. [verified: `.claude/skills/code-review-claude-config/SKILL.md:63`; `docs/hooks.md:20,55`]
9. A genuinely-lax failure lies inside the declared threat model, so it never validly meets base criterion 3 ("the finding adds a layer beyond it"). It reaches criterion 3 only through layer line 62, once it is already in the set. Narrowing line 63 to criterion 3 therefore drops exactly the base-rules DEFERs. [verified: `claude-skills/skills/code-review/SKILL.md:380-386`; `.claude/skills/code-review-claude-config/SKILL.md:59,62`]
10. The plan-review layer's recording step records only "every such shape", and its line 23 has already sent a genuinely-lax failure to the base rules. It needs no edit. [verified: `.claude/skills/plan-review-claude-config/SKILL.md:23,26`]
11. Both appended sentences bind only a gap a cooperative agent does emit: the first through its "Except for a gap that needs a shape a cooperative agent would never emit", the second through "a gap discovered later that needs a shape a cooperative agent does emit". Both bind only gaps found after the commit that introduced the tier headers, so cooperative-emittable entries already in the original closed set (line 47's parenthetical, layer line 59) stay debt. Line 13's waived shapes, line 43's waived-finding recording, line 14's `untrusted-input` "pre-existing gap of that shape", and a per-PR DEFER row in the PR body are never-emit or per-PR records and sit outside both. None of them newly needs an ask. After D2, a pre-existing never-emit gap on an `untrusted-input` gate (layer line 61 sends it to the base rules, so it is never a criterion-3 DEFER) is no longer written to the header by line 63, though `docs/hooks.md:14` allows recording it there. That fails closed: the gap stays a live finding. Only the never-emit class is exempt, so a seldom-emitted shape that is not never-emit takes the ask. [verified: `docs/hooks.md:13,14,43`; `.claude/skills/code-review-claude-config/SKILL.md:61,63`]
12. The appended sentences redefine when a later gap counts as "recorded this way", so they carry no list of recording sites: lines 14, 20, 22, 26, 43, 55, a later tracking pointer, `docs/security-hardening.md`, and the layer's line 63 ("the recording that section requires") all fall under "no text or pointer records as debt a gap discovered later that needs a shape a cooperative agent does emit, wherever it sits" when the gap is one a cooperative agent does emit (lines 14 and 43 hold only never-emit gaps, so row 11 exempts them). Line 26 still says where to record, and the appended sentences say when. The condition is the engineer's answer accepting the gap as permanent debt, so a scoped "for now" keep does not count, and the commit message or PR body states the rationale, as line 24 asks for a relaxation. An emit-shape gap nobody asked about stays a live finding under line 20's second sentence, and the base rules dispose of it. [verified: `docs/hooks.md:14,20,26,43,55`; `.claude/skills/code-review-claude-config/SKILL.md:63`]
13. No recording changes a regression's status. Line 26 says "a diff cannot open a new bypass and legalize it by listing it in the same commit", and layer line 60 says "No recording, in this diff or an earlier one, changes that." [verified: `docs/hooks.md:26`; `.claude/skills/code-review-claude-config/SKILL.md:60`]
14. Line 20's existing sentences stay byte-identical. The first appended sentence adds a precondition on admission and the second withdraws routing that would admit a gap without it, so nothing loosens relative to the merged text. [verified: `docs/hooks.md:20`]
15. Pins are substring checks over the whitespace-normalized section that runs to the next line starting "## ". In `docs/hooks.md`, "## Threat-model tiers" runs from line 5 to line 95's "## Gate hooks". [verified at merge-base 9eab0a52: `claude-skills/skills/tests/test_skills.py:3790-3795,5029-5052`; `docs/hooks.md:5,95`]
16. None of the three new docs sentences (two appended at line 20, one at line 55) nor any of the six new `code-review-layer` phrases ("DEFERred under criterion 3 as a waived or routed finding against a gate", "that is not a regression is never DEFERred", "admits it as permanent existing debt", "takes a blocking stop-and-ask", "the engineer's own SETTLED row kept with `--carry-forward`", and the disclosure-pointer sentence) exists in its section at the merge-base, so each of those pins goes red before its edit. The fourth docs pin, "**Existing debt** is a closed set", guards existing line-55 text, so it passes at the merge-base by design. In the merge-base layer, "criterion 3" matches only line 62, and a grep of the merge-base layer (`git show 9eab0a52`) found no hit for the other five phrases, the pointer sentence included. [verified at merge-base 9eab0a52: `git show` of `docs/hooks.md` and `.claude/skills/code-review-claude-config/SKILL.md`]
17. Ruff's 130-column limit with `E` selected applies to the test file, so long phrases split into adjacent literals, as the `hooks-doc-regression-rule` entry already does. [verified at merge-base 9eab0a52: `pyproject.toml:2,6`; `claude-skills/skills/tests/test_skills.py:3755-3768`]
18. No test or durable doc outside `docs/hooks.md` restates line 20 or line 55. The concept appears elsewhere only by name, in layer lines 59 and 23. The `hooks-doc-regression-rule` id appears outside the test only in plans. [verified: Grep for "Existing debt", "existing-debt", "closed set", "waivable shape", and "hooks-doc-regression-rule"; every other hit is an unrelated use]
19. `require-skill-review.sh` gates `.claude/skills/**/SKILL.md`, so the line-59 and line-63 edits need `/skill-review` before commit. [verified: `plugins/skill-management/hooks/require-skill-review.sh:201`]
20. The orchestrator follows the appended line-20 rule through the layers' "closed existing-debt set" references, without a layer bullet restating it. [unverified: behavioral]
21. Line 35 of the merged #1218 body reads "None names a present fail-open path". Its DEFER row `4b37c3d23250` has a rationale saying the recorded option "is unreachable from the layers". [verified: `gh pr view 1218 --json body`, run by the session]
22. Since 2026-10-04, origin/main's only hook-header change is #1221, a redaction-hook change. [verified: `git log` on origin/main, run by the session] #1221 (`72830ca2`) sits below #1218 (`8c8d26c0`) in this branch's history, so it merged before line 63 existed and recorded nothing through it. [verified: `git merge-base --is-ancestor 72830ca2 8c8d26c0` exits 0, run by the `ciso-reviewer`; Verification step 5 re-runs it]
23. #1218 added no CHANGELOG entry. This change edits `docs/`, a repo-local layer outside both stow packages, and a test, and changes no consumer-visible behavior. [verified: Grep of `CHANGELOG.md` for "GH-1004" and "1218" finds no match; `claude/.claude/scripts/stow-packages.sh:24-27`]
24. The base rule's term is "a blocking stop-and-ask to the human", which names no tool. The appended sentences use the same term. [verified: `claude-skills/skills/code-review/SKILL.md:396`]
25. When a change edits the section's rules above the classification table, a finding against any gate the change does not strictly add is a regression, and a regression is "always ADDRESS or stop-and-ask, never deferred". This covers findings against a gate only. [verified: `docs/hooks.md:26,28,32`]

26. The engineer said "We need the fix. The re-raising is too much of a problem." [engineer-verified: "We need the fix. The re-raising is too much of a problem."] They then selected "Ask the architect", and selected "Apply it (Recommended)" to the question whether to apply the architect's lighter edit set, with a question text, written by the session, stating that it removes the orchestrator's own per-PR DEFER for a non-regression, genuinely-lax gap outside the set. [engineer-verified: "Apply it (Recommended)"] The edit set (D3 and the line-63 additions) is the architect's recommendation, not the engineer's words. The base skill adds `--carry-forward` only when the answer states no scope or time limit, neither declines carries nor asks to be asked again, and the enforcement-invariant rule does not apply. [verified: `claude-skills/skills/code-review/SKILL.md:378,396`]

27. On the code-review round-3 findings the engineer answered "Apply and freeze (Recommended)". [engineer-verified: "Apply and freeze (Recommended)"] The quote covers the freeze of rule text and leaving the per-hook rows, and line 55 beyond D1's pointer, unedited. It does not accept any listed entry as permanent debt.

28. In code-review round 4 a `ciso-reviewer` found that line 59's last two sentences stated only the scope-or-time-limit half of the base flag conditions. The engineer selected "Ask the architect"; they then said "Escalate to fable" after the architect's verdict; after both consults agreed they selected "Apply it (Recommended)". [engineer-verified: "Apply it (Recommended)"] The replacement text is the architect's, confirmed by a Fable review, not the engineer's words. The same round widened the `code-review-layer` pin to start at "the engineer's", a `staff-sdet` finding the session ADDRESSed.

**Mechanisms:**
- M1. Append two sentences to line 20 that redefine "recorded this way". They are the same for every gate and tier, keyed to who decides and to a permanent-debt answer, and reach every route into the set from its definition. anchors: root, row1, row2, row3, row6, row11, row12, row13, row14, row24.
- M2. Replace line 55's routing sentence with a pointer to M1's rule. anchors: root, row1, row7.
- M3. Narrow line 63's recording step to criterion-3 DEFERs and `--carry-forward` keeps. anchors: root, row8, row9, row10.
- M5. Append the ask-and-record sentences to layer line 59 (D3). The `--carry-forward` flag follows the base rule's conditions (no scope or time limit, no decline of carries, no ask-to-be-asked-again) and is forbidden where the enforcement-invariant rule applies, and layer line 60 makes every regression such a finding. anchors: root, row2, row26.
- M4. Add a new `hooks-doc-debt-admission` entry for the three docs sentences (two appended at line 20, one at line 55) plus the **Existing debt** lead guard, and six phrases on the existing `code-review-layer` entry. anchors: row15, row16, row17, row18.

## Critical files

- `docs/hooks.md`: append two sentences to line 20 and replace one sentence of line 55. Every other line stays byte-identical, lines 13, 26, and 47 included.
- `.claude/skills/code-review-claude-config/SKILL.md`: append five sentences to line 59, edit line 63's first and last sentences. No other line changes.
- `claude-skills/skills/tests/test_skills.py`: change `_TIER_DISPOSITION_SECTIONS` and its comment block only. Reuse `test_gate_tier_disposition_sections_keep_invariant_clauses` and `_section_between` unchanged.
- `.claude/plans/gh-1004-existing-debt-merge-base-anchor.md`: this plan.

## Verification

Run from the worktree root.

1. Red. Measure it against the merge-base content of `docs/hooks.md` and the layer (the worktree already carries the edits once implemented, so run it in a temporary worktree at the merge-base with only the `test_skills.py` change applied, created under `.claude/worktrees/` so that `../../../.venv` resolves). Run `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -p no:cacheprovider -k "threat_model_tiers_citation or gate_tier_disposition_sections"`. Expect exactly two failures:
   - `hooks-doc-debt-admission`, missing the three new phrases. Its fourth phrase, "**Existing debt** is a closed set", already exists at the merge-base, so it passes there by design as a guard against deleting the lead.
   - `code-review-layer`, missing exactly six phrases: the criterion-3 phrase, the four D3 and D2 phrases, and the disclosure-pointer sentence.
2. Green. Apply the `docs/hooks.md` edits and the layer edits at lines 59 and 63, then rerun step 1 in the worktree. Every case passes.
3. `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
4. `../../../.venv/bin/ruff check claude-skills/skills/tests/test_skills.py`.
5. `git merge-base --is-ancestor 72830ca2 8c8d26c0` (row 22). Exit 0 means #1221 predates line 63, and there is nothing to report. A nonzero exit means: read `git show 72830ca2 -- claude/.claude/hooks/` for a Known-gaps addition and report any to the engineer. Do not edit the hook.
6. Before committing, run `skill-management:skill-review` on the staged layer diff. `require-skill-review.sh` blocks the commit until it has run (row 19).
7. Run `/code-review`, and dispose of findings per "This PR's own review" above.
8. The commit message states the fail-closed rationale of each edit (the separate entry-provenance residual has no fail-closed default, so the message names it or points to the PR body disclosure) and why each pin was added. It quotes every `[engineer-verified]` quote in the ledger as theirs, "Apply the rewrite (Recommended)" included, and marks the (a) description, the reduced edit list, and the architect's rewrite, the session's scoping of its second sentence, and the D3 and line-63 edit set (the architect's recommendation) as the session's and the consult's. The D3 disclosure-pointer sentence was added after that consult in response to a review round, so it is the session's.
9. After committing:
   - `git diff --stat origin/main...HEAD` lists exactly the four Critical files.
   - `git diff -U0 origin/main...HEAD -- docs/hooks.md .claude/skills/` shows hunks only at `docs/hooks.md:20`, `docs/hooks.md:55`, and `.claude/skills/code-review-claude-config/SKILL.md:59,63`.
   - `grep -c "that stop also gives the base contradiction-route rule's human-keep disclosures" .claude/skills/code-review-claude-config/SKILL.md` prints 1, so the pinned pointer sentence is present exactly once.
10. The PR body carries a short list. Re-quote #1218 exactly from `gh pr view 1218 --json body` (row 21).
    - Merged #1218's body says "None names a present fail-open path", which is wrong for the existing-debt path.
    - Its DEFER row `4b37c3d23250` rests on the premise that the recorded option "is unreachable from the layers". That premise was disproved.
    - The code-review layer's line-63 step admitted base-rules DEFERs into existing debt. This diff narrows it to criterion 3.
    - `docs/hooks.md:32` applied to this PR's own review because the diff edits the section's rules above the classification table. It covers findings against a gate only.
    - D3 (layer line 59) removes the orchestrator's own per-PR base DEFER for a non-regression, genuinely-lax failure outside the closed set. Reading (b) was not chosen: an unfixed outcome still exists as the engineer's own keep. A scoped keep is asked again on each re-raise.
    - Line 63 now also writes into a gate's header Known-gaps line the shape of an engineer's `--carry-forward` keep. List each header line it wrote with the id of the SETTLED row that authorized it.
    - Entries recorded after the tier-headers commit with no engineer answer read as live findings under line 20 when they sit on a shape a cooperative agent does emit, and the first review that raises one takes D3's stop. The verified instance is the chained-`cd` entry in `deny-private-project-refs.sh`'s header (added after that commit; `git log -S` finds it in `17e090ac` on 2026-09-26, and `783bca5c`, dated 2026-09-24, is an ancestor). The engineer answered "Apply and freeze (Recommended)" on the round-3 findings (row 27), which is the keep for leaving the per-hook rows, and line 55 beyond D1's pointer, unedited. That answer does not accept the chained-`cd` entry or any other listed entry as permanent debt.
    - Each residual in Out of scope under "Plan-review round 1 residuals", including that D0's never-emit exception is tier-blind and is the engineer's call.

    The body contains no private identifiers or home paths.

## Out of scope

- **What the over-engineering consult dropped:**
  - the line-13 tie-break
  - the merge-base re-anchor and in-change-listing bar at line 20
  - the quoted-answer requirement
  - whole-sentence layer re-pins
  - the mutation red step
  - the `test_hook_alignment.py` run
  - mechanisms M4 and M6 of the earlier draft

  None closes D0, D1, or D2. The tie-break would cover an agent misjudging an emit shape as one it would never emit. That is a judgment error under row 20's behavioral assumption, not one of the three literal paths.
- **What the earlier consult dropped:**
  - the `claude-skills/skills/ready-for-review/SKILL.md:129-130` issue
  - mechanical ledger refusal (G2)
  - pins for the DEFER mapping and the Known-gaps line
  - pin failure-message wording
  - layer consolidation (Root B)
  - plan-review bullet-5 narrowing
  - the replacement, takeover, 'added', and table-row residuals
- **Reading (b).** It was not chosen. An unfixed outcome still exists as the engineer's own keep. D3 removes the orchestrator's own per-PR base DEFER for a non-regression, genuinely-lax failure outside the closed set. Any other base DEFER stays allowed and is recorded only in the PR body, never as existing debt.
- **Files left unedited:**
  - `docs/hooks.md:13,26,47`
  - the plan-review layer and its pins (row 10)
  - the base `code-review` skill (G1)
  - CLAUDE.md's "Hook threat model" paragraph
  - `.claude/plans/gh-1004-cooperative-tier-disposition.md` (historical)

  No CHANGELOG entry is added (row 23).
- **Plan-review round 1 residuals, not closed here.** Each is named in the PR body:
  - The plan-review layer gets no ask-and-record step. A gap only plan-review reviewers raise, and that no code-review round on the branch raises again, can come back at the next plan touching that gate. A code-review keep lands in the shared closed set both layers read.
  - The pins check presence only. They do not pin line 59's two log-shape sentences, line 63's "(never a carry row or a regression)" parenthetical, or line 63's "DEFER or SETTLED row" change. Each has a backstop: losing the flag sentence leaves the gap a live finding, line 59's "only under that rule's conditions for it" and "Log any other keep without it" limit the flag to the unlimited keep, the pinned "own SETTLED row" excludes carry rows, the script refuses the flag beside `--enforcement-invariant`, and line 60's pinned rule makes every regression such a finding. The `--carry-forward` flag is a proxy for "accepted permanent debt" whose base meaning is opt-out repeat suppression, so a keep whose answer never mentioned permanence can still be logged with it. Line 63's "DEFER or SETTLED row" change has a backstop too: the pinned permanence disclosure still reaches the engineer, and the script blocks a PR-body update that carries a private identifier. The disclosure-pointer sentence is pinned, and Verification step 9 also greps for it.
  - The base skill's two no-human routes (an architect keep-current-text, a same-failure-mode repeat at a live DEFER) are not addressed for a genuinely-lax failure outside the set. Neither writes the header, because line 63 reads only the engineer's own row.
  - If the engineer later withdraws a `--carry-forward` keep, the header entry D2 wrote goes stale and is removed by hand.
  - D0's never-emit exception is tier-blind. A strictly-added `untrusted-input` gate can list never-emit gaps in its own header, and line 14's protection covers that PR's review only. D0 preserves this behavior and does not widen it. It is the engineer's call whether to condition the exception on tier.
  - A header entry D2 writes carries no durable marker of the engineer's answer, because the ledger is branch-scoped. A later PR tells an answered entry from an unanswered one only from the commit message. An entry whose history cannot show whether it predates the tier-headers commit can read as in the original set (an entry shown to postdate it with no engineer answer stays a live finding), a residual with no fail-closed default. It is an orchestrator DEFER (row `5cacf8b40f91`), not an engineer acceptance, and the PR body discloses it.
  - The pins check presence, not that the removed line-55 branch or the old line-63 wording stays gone (G2). The pins do not bind D0's two sentences to each other or to line 20's second sentence, and "criterion 3" is an ordinal tied to the base skill's list, as line 62 already is.
- **No re-audit of earlier entries.** Known-gaps lines and tracking-issue entries recorded before this change are not re-audited. Step 5 checks the one PR that could have recorded through line 63. A Known-gaps or tracking entry recorded after the commit that introduced the tier headers, with no engineer answer, on a shape a cooperative agent does emit, reads as a live finding under line 20, so a review that raises it takes D3's stop unless the gap is fixed. The engineer's answer on the code-review round-3 findings ("Apply and freeze (Recommended)", row 27) is the keep for leaving the per-hook rows, and line 55 beyond D1's pointer, unedited. It does not accept any listed entry as permanent debt, so each such entry stays a live finding until the engineer answers it.
- **Text freeze.** After the D3 disclosure-pointer sentence, no further rule-text edit lands at `docs/hooks.md:20`, `docs/hooks.md:55`, the per-hook rows, or layer lines 59 and 63. A later finding there goes to the PR body as a named residual unless it shows a present fail-open path or a literal conflict that line 20's "even where another sentence of this section" clause does not settle. The freeze governs this plan's drafting and plan-review rounds only. `/code-review` dispositions of this PR's own diff stay under the base closed criterion list and `docs/hooks.md:32`.
