# Disambiguate debug-probe inline fixes from review-finding code-writer dispatch

## Context

`subagent-delegation/SKILL.md` states two rules that look contradictory to a
reader scanning the file in isolation: the "Debug-investigation probe"
section has the parent apply a diagnosed verification-failure fix inline,
while the "Fix that follows `code-review`, `ready-for-review`, or
`respond-pr` feedback is also delegated by default" section mandates
`code-writer` for every review-finding fix regardless of size. Neither
section cross-references the other, so nothing in the file states that
these are two deliberately different regimes rather than one superseding
the other. `ready-for-review/SKILL.md` Step 2 (Verification) compounds this
by citing `subagent-delegation` only for how to *run* checks, saying
nothing about who fixes a failure once found — leaving Step 2 looking like
a silent gap next to Steps 3/4's explicit `code-writer` mandate.

This surfaced from a user hypothesis that main-agent sessions were making
inline edits during `/ready-for-review` that should have gone through
`code-writer`. Investigation confirmed the inline-fix behavior is
intentional and grounded (`docs/design-decisions/debug-investigation-read-only-probe.md`:
a write-capable debug-and-fix agent "will attempt to fix [a failing check],
re-introducing the verification failure the separation was designed to
foreclose" — the check-runner Incident 1 failure mode) — but the two rules'
lack of cross-reference is a real defect independent of that conclusion.
The intended outcome is that a reader landing in either section, or in
`ready-for-review` Step 2, can tell the two regimes apart and why, without
re-deriving it.

A second, unrelated finding from the same investigation — `transcript-analysis`
has no event stream capable of empirically confirming or denying which regime
a given session actually followed — is out of scope here and will be planned
separately (Phase 2, its own branch and PR).

## Approach

Name the boundary using the axis the file already uses everywhere else — the **Read-then-edit: decision-made test** — rather than asserting a new parallel rule. `subagent-delegation/SKILL.md` anchors every other routing call to that test's condition (1): the review-round default says condition (1) "holds by construction," the carve-outs say "fails condition (1), stays inline," and approved-plan implementation says it "holds by construction." The Debug-investigation probe section is the one routing call in the file that never names which condition it exercises. That omission — not a missing cross-reference per se — is why the two rules read as contradictory. Add one clause to each section naming the condition each exercises, pointing at the other, plus one pointer in `ready-for-review/SKILL.md` Step 2. No behavior change.

**Root problem:** `subagent-delegation/SKILL.md`'s Debug-investigation probe section states an inline-edit outcome without naming the rule that produces it, so a reader cannot tell it apart from the review-round `code-writer` mandate stated 30 lines later in the same file.

**Givens** (fixed conditions beyond this plan's reach):

1. The debug probe stays read-only and the parent retains the edit and the judgment — a recorded design decision (`docs/design-decisions/debug-investigation-read-only-probe.md`); dissolving the design's dependence on it needs a decision outside this plan.
2. The review-round `code-writer` default (dispatch regardless of size) is likewise settled in `subagent-delegation/SKILL.md`'s own body; changing it is a separate decision.
3. The `` `target` § "Heading" `` citation grammar and its resolver are owned by `.claude/rules/skill-and-agent-self-review.md` and `_resolve_citation_target`/`_normalize_heading` in `claude-skills/skills/tests/test_skills.py`. This plan conforms to them; it does not change them.
4. `REFERENCES.md` is never loaded at skill runtime, so a session executing either skill never sees it. Any fix a running session must act on lands in a `SKILL.md` body.

**Mechanisms:**

5. **Append one sentence to the Debug-investigation probe section**, after "The parent reasons over the returned diagnosis, designs the fix, applies the edit, and re-runs the check inline." It states that the parent normally designs the fix *while reading* the returned diagnosis — the decision-made test's condition (1) failing in the normal case, not by definition — so the edit normally stays inline, contrasts that with the review-round default via a bare `§ "Implementation work → code-writer"` citation, and cites the design decision for why the parent retains the edit in that normal case. *anchors: root, row1.* Over-powered-primitive check — two lighter alternatives, both rejected: (a) a new "Which regime applies" `###` section, rejected because both existing sections already state their own rule correctly, so a third would restate rather than disambiguate, and `.claude/rules/skill-and-agent-self-review.md` bars a shared partial as the alternative home; (b) record it in `REFERENCES.md` only, rejected by row 4 — a running session never loads it.
6. **Append one sentence to the review-round paragraph**, after "A finding surviving a second dispatch stops being delegated…". It states that a fix the parent designs itself while reading a probe's diagnosis is not the review-round's unconditional-dispatch default — not that a debug-probe fix can never be dispatched: a diagnosis the parent has already fully specified before the locating read still satisfies condition (1) and routes by the file's ordinary two-condition test like any other change. Cites `§ "Debug-investigation probe → general-purpose or Explore"`. *anchors: root, row2.* Bidirectionality is the requirement: the stated outcome is that a reader landing in *either* section can resolve the boundary without leaving it.
7. **Add one pointer line to `ready-for-review/SKILL.md` Step 2**, after the existing "Run the checks inline" line. It names who applies a verification-failure fix and cites `` `subagent-delegation/SKILL.md` § "Debug-investigation probe → general-purpose or Explore" `` rather than restating either rule. *anchors: root, row1.* Step 2 is where the ambiguity actually bites: the parent holds the failure output there, so the debug regime applies, but Steps 3 and 4 immediately below both mandate `code-writer` — leaving Step 2 looking like an omission.
8. **Repoint `REFERENCES.md`'s stale design-decision citation** from the retired monolith path to the per-decision file. *anchors: row3.* Incidental, recommended, not required for correctness — see Critical files for the Axis-1 rationale and the option to drop it.

**Assumptions:**

9. `**The fix that follows \`code-review\`, \`ready-for-review\`, or \`respond-pr\` feedback is also delegated by default.**` is a **bold paragraph lead-in, not a markdown heading**. `_HEADING_LINE_RE` is `^#{1,6}\s+.+$`, so a `§ "Fix that follows…"` citation resolves its target but finds no matching heading and fails. Mechanism 7 therefore cites the enclosing real heading `### Implementation work → \`code-writer\`` and names the rule in prose as "the review-round default" — the phrasing `ready-for-review/SKILL.md:83` already uses. `[verified: claude-skills/skills/tests/test_skills.py:2734; claude-skills/skills/subagent-delegation/SKILL.md:166]`
10. The debug-probe heading **is** `§`-citable, written backtick-free as `"Debug-investigation probe → general-purpose or Explore"`. `_normalize_heading` strips every backtick from both sides of the comparison, and its docstring names this exact heading as the worked example. Preserve the `→`; it is not stripped. `[verified: claude-skills/skills/tests/test_skills.py:2738-2752]`
11. A backticked span immediately preceding ` § ` is captured as the citation target by `_CITATION_WITH_TARGET_RE`. Writing `` …the `code-writer` § "Implementation work → code-writer" `` would set the target to `code-writer`, which resolves to no file and fails. Every bare `§` citation in mechanisms 5 and 6 must be preceded by plain prose ("under", "per"). `[verified: claude-skills/skills/tests/test_skills.py:2676]`
12. A trigger-based dichotomy ("failing check ⇒ inline, review finding ⇒ dispatch") would be **false and would introduce a contradiction**: `ready-for-review`'s CI-watch step 4 dispatches `code-writer` for a failing-check fix. `REFERENCES.md` § "Diagnosis-delegation: two variants, not one" already distinguishes that case by its preconditions — the parent never held the CI output and relays rather than reasons over the diagnosis. Anchoring on condition (1) classifies all three cases correctly; anchoring on the trigger does not. `[verified: claude-skills/skills/ready-for-review/SKILL.md:197-198; claude-skills/skills/subagent-delegation/REFERENCES.md:34-49]`
13. `test_skill_citations_resolve_to_real_headings` scans `REFERENCES.md` alongside each `SKILL.md`, so mechanism 8's edited line is inside the checked corpus once it uses the `§` grammar. `[verified: claude-skills/skills/tests/test_skills.py:2807-2819]`
14. `REFERENCES.md:53`'s current `` `docs/design-decisions.md §18` `` is **not** matched by the citation grammar — `_BARE_CITATION_RE` requires a double-quoted heading — so CI never caught it and will not catch a regression unless mechanism 8 converts it to the `§ "Heading"` form. `[verified: claude-skills/skills/tests/test_skills.py:2682]`
15. Whether `docs/design-decisions.md` still carries §18's text is not checked. The repoint is correct either way: the per-decision file is the current home per `.claude/rules/design-decisions.md`, and the monolith is retired. `[unverified]`
16. There is no SKILL.md **body**-length cap in the test suite — only `MAX_SKILL_DESCRIPTION_CHARS` on frontmatter, which neither file's frontmatter changes. `[verified: claude-skills/skills/tests/test_skills.py:1575-1592, and a grep of that file for body-length assertions returning none]`
17. Mechanism 7's line is prose outside every fenced block, so the three `HOOK_TEST_FIXTURE` fenced blocks in `ready-for-review/SKILL.md` that the hook-alignment suite re-reads verbatim are untouched. `[verified: claude-skills/skills/ready-for-review/SKILL.md:27, 140, 147]`
18. No third file restates or contradicts either rule — `code-review/SKILL.md`'s Fix-route line and `root-cause-analysis/SKILL.md` were both checked. `[verified: claude-skills/skills/code-review/SKILL.md, claude-skills/skills/root-cause-analysis/SKILL.md]`

## Critical files

One `code-writer` dispatch covers all of it — three files, four one-line additions, no ordering dependency between them. Review surface is one skill domain, no executable code.

- **`claude-skills/skills/subagent-delegation/SKILL.md`** — two additions.
  - After line 133 (end of the Debug-investigation probe section's "The parent reasons over the returned diagnosis…" paragraph, before the `root-cause-analysis` pointer): one sentence stating that the parent normally designs the fix while reading the returned diagnosis, which is the decision-made test's condition (1) failing in the normal case, so the edit normally stays inline — unlike the review-round default under `§ "Implementation work → code-writer"` — and citing `` `docs/design-decisions/debug-investigation-read-only-probe.md` § "Debug-investigation delegation: read-only probe over debug-and-fix agent" `` for why the parent retains the edit in that case. Do not restate that doc's rationale; row 1 is a given.
  - After line 180 ("A finding surviving a second dispatch stops being delegated…"): one sentence stating that a fix the parent designs itself while reading a debug probe's diagnosis is not the review-round's unconditional-dispatch default — not that a debug-probe fix can never be dispatched, since a diagnosis the parent has already fully specified before the locating read still satisfies condition (1) and routes by the file's ordinary two-condition test — citing `§ "Debug-investigation probe → general-purpose or Explore"`.
  - Both bare `§` citations must be preceded by plain prose, never a backticked span (row 11).
- **`claude-skills/skills/ready-for-review/SKILL.md`** — one addition after line 52 ("**Run the checks inline** — per `subagent-delegation/SKILL.md` § "Heavy command output — run inline"."): one sentence stating that a genuine failure's fix is the parent's own inline edit rather than a `code-writer` dispatch as in steps 3 and 4, and that the read-heavy diagnosis dispatches per `` `subagent-delegation/SKILL.md` § "Debug-investigation probe → general-purpose or Explore" ``. Place it before the "Scope exceptions" block so it sits with the run-the-checks instruction it qualifies. Leave the existing "Heavy command output — run inline" citation intact.
- **`claude-skills/skills/subagent-delegation/REFERENCES.md:53`** *(incidental — Axis 1 bucket 2)* — repoint `` `docs/design-decisions.md §18` `` to `` `docs/design-decisions/debug-investigation-read-only-probe.md` § "Debug-investigation delegation: read-only probe over debug-and-fix agent" ``. Rationale for keeping it in this PR: without it, the same skill directory carries two divergent pointers to one decision — the new `SKILL.md` line at the per-decision path, this one at the retired monolith — which is the single-source-of-truth defect this PR exists to remove. Converting it to the `§` grammar also brings it inside CI's reach for the first time (rows 13, 14).
- **Reuse:** the existing `` `target` § "Heading" `` convention and the decision-made test's existing condition-(1) vocabulary. No new mechanism, no new section, no new file.

## Verification

```
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

Scoped to this diff (`claude-skills/skills/subagent-delegation/SKILL.md`, `claude-skills/skills/ready-for-review/SKILL.md`, and `claude-skills/skills/subagent-delegation/REFERENCES.md`). This selects `claude-skills/skills/tests/test_skills.py`, which carries `test_skill_citations_resolve_to_real_headings` — the check that catches a mis-cited heading, an unresolvable target, and the row-11 backtick-before-`§` trap. `select-tests.py`'s `_is_skill_auxiliary_md_change` maps the `REFERENCES.md` path into the same domain, so the incidental needs no widening.

Do not run the full suite by hand. Neither of `CLAUDE.md`'s two carve-outs applies: this diff makes no whole-repo claim, and every touched path is one `select-tests.py` maps.

**Also required before commit:** `/skill-review` is hook-enforced for SKILL.md changes — `require-skill-review.sh` blocks `git commit` until the behavioral-equivalence marker is written (`.claude/rules/review-pipeline-dispatch.md`). Expect it to run and pass on a no-behavior-change diff; budget for it rather than discovering it at the commit boundary. `/code-review` dispatches it automatically.

**Manual check, one read:** confirm each of the four citations resolves as written — three against real headings, and that no backticked span sits immediately before a ` § `.

## Out of scope

- **The `transcript-analysis` instrumentation gap** — no event stream can join a Bash verification failure to the tool_use records that follow it, so no past session's actual regime is empirically confirmable. Planned separately as Phase 2, its own branch and PR. Nothing here is designed for it.
- **Revisiting either underlying rule** — the debug probe's read-only-ness and the review-round `code-writer` default are givens 1 and 2.
- **Adding a fourth pointer at `ready-for-review`'s CI-watch step 4.** It is the third instance of the pattern and the one most likely to be misread as a counterexample (a failing check whose fix *is* dispatched). Left alone deliberately: `REFERENCES.md` § "Diagnosis-delegation: two variants, not one" already carries that disambiguation, and step 4 already states its own route explicitly. A fourth pointer would be a defensive layer closing a gap the third one did not open.
- **Promoting the "Fix that follows…" lead-in to its own `###` heading** to make it directly citable. Rejected as heavier than the need: the paragraph sits under "Implementation work → `code-writer`" because it *is* an application of the decision-made test stated in that section, and a sibling heading would visually detach it from the test it anchors to — a structural change made for a citation-formatting reason. Mechanism 7's enclosing-heading citation costs nothing and matches the existing precedent at `ready-for-review/SKILL.md:83`.
