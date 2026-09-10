# Permit a dimensionless share of pooled spend in the redaction carve-out

## Context

`docs/private-project-redaction.md`'s "Publishing a pooled tooling
measurement" section is a reviewer-discipline carve-out letting an agent
publish a figure pooled across a corpus that mixes this repo's public
transcripts with the owner's private-engagement transcripts. Its
Cost/Duration bullet (`:118-125`) permits Cost "only as a rate per tool
call, session, or dispatch," which bars a dimensionless share of pooled
spend by omission — a form this repo already publishes in at least six
places. The same bullet's absolute phrasing, read without the section's
mixed-corpus precondition stated 18 lines earlier, also causes sessions
to flag `pr-cost-section.sh`'s provably in-repo-scoped output as a
violation. Outcome: permit the share form, state the precondition where
reviewers read it, and make three stale rule-restatements elsewhere in
the repo defer to the carve-out instead of paraphrasing a superseded
version of it.

## Approach

Two edits in `docs/private-project-redaction.md` and one sentence-level swap at each of three sibling withholding sites. The Cost/Duration bullet gains the dimensionless-share form for Cost alone; the "Scope — two closed lists" lead-in gains the mixed-corpus precondition plus one worked case that names why `pr-cost-section.sh`'s output was never in the class. The three withholding sites keep their withholding verbatim and replace only their stale rule paraphrase with a reference to the carve-out. No code, no test, no hook, no other prose file changes.

### One defect in the endorsed Option A wording — flagged, not silently reworded

Option A's fourth sentence reads "Report **each** as a rate per tool call, session, or dispatch (e.g., median cost per session), or as a dimensionless share of pooled spend…". "Each" is inherited from the preceding sentence's "Cost and Duration", so it distributes *both* permitted forms across both nouns. As drafted it grants a Duration share, contradicting decision 4. The phrase "share of pooled *spend*" hints Cost-only by its noun, but inference-by-noun against explicit grammatical distribution is precisely the ambiguity class this whole amendment exists to close (row 5).

Minimal repair, preserving every clause the engineer chose and changing only the distribution:

> Cost is dollar or token spend on running the tooling. Duration is wall-clock time spent running the tooling. Cost and Duration may never be reported as a client-billed, engagement-revenue, or billable-hours figure. Report each as a rate per tool call, session, or dispatch (e.g., median cost per session), never as a raw pooled total. Cost may also be reported as a dimensionless share of pooled spend, split along any dimension but project, account, or engagement (e.g., Opus dollars as a percentage of total). Duration may not: a share of pooled wall-clock is one hop from billable hours per deliverable. A raw total scales with pool volume and, unlike a rate or a share, can be converted to an engagement-value estimate using public day-rate references.

A second defect, same class, found by `ciso-reviewer` in the share clause rather than the Duration one: without the axis restriction now shown above, the worst literally-compliant figure is "this repo's own tooling spend is N% of my pooled AI-tooling spend across every engagement" — a per-project proportion, which is what `CLAUDE.md`'s absolute rule exists to bar. That rule is untouched and does bar it, so the figure was never permitted end to end. But the bar would sit one document away from the bullet a reviewer is reading, which is the same read-in-isolation failure row 8 fixes in the other direction. The restriction is a precision fix to the sentence already being edited, not a layer stacked on it.

### Assumption ledger

**Root problem.** The carve-out's Cost/Duration bullet omits the dimensionless-share form from its permitted-forms clause and reads as self-contained when its mixed-corpus precondition sits 18 lines above it, so the section simultaneously bars a form the repo already publishes and flags provably in-repo-scoped output as a violation.

**Givens:**

- **G1.** The rule has no mechanical enforcement surface to amend: the section is tier-3 reviewer discipline, and a hook cannot see how a figure was computed. `[verified: docs/private-project-redaction.md:93-95 — "This is the tier-3 companion to the two mechanical tiers above. It is reviewer discipline, not a hook. A pooled figure's safety depends on how it was computed, not what string it contains, and a hook can't see that."]`
- **G2.** Figures already published across a mixed corpus stay as published. The section assigns that remediation to the owner, not the agent. `[verified: docs/private-project-redaction.md:162-166]`
- **G3.** No figure the three withholding sites withhold can be published by this plan. The approval gate requires a citable owner approval tied to the exact figure and artifact, and no artifact on this branch carries one. `[verified: docs/private-project-redaction.md:141-151]`

**Rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Option A is the chosen wording, superseding PR 943's "completed PR as a second denominator" design. | `[engineer-verified]` |
| 2 | All three sibling withholding sites are aligned, using the transformation shape approved for `cost-levers-considered.md:501`. | `[engineer-verified]` |
| 3 | The share form is Cost-only; no symmetric Duration wording. | `[engineer-verified]` |
| 4 | Option A's "Report each as a rate…, or as a dimensionless share" distributes both forms over Cost and Duration, granting a Duration share and contradicting row 3. | `[verified: docs/private-project-redaction.md:118-125 — the existing sentence's "each" already binds both nouns introduced two sentences earlier]` |
| 5 | The share omission is a drafting asymmetry, not a stated restriction: the parallel Counts bullet permits "a total, a share, or a median" at any granularity, and no sentence anywhere gives a reason Cost should differ on the share axis specifically. | `[verified: docs/private-project-redaction.md:114-117 against :121-122]` |
| 6 | `--summary` is code-enforced single-repo, single-account, fail-closed. | `[verified: cost.py:466-498 — sys.exit(2) unless --this-repo with --projects unset or the literal default; refuses --by-project, --no-redact, and --config-dir; refuses a multi-root resolution as defense-in-depth]` |
| 7 | `pr-cost-section.sh` invokes exactly that path, further narrowed to one branch. | `[verified: pr-cost-section.sh:41 — `cost --this-repo --branches "$branch" --summary`]` |
| 8 | The doc states the mixed-corpus precondition only inside the "What it permits" paragraph, 18 lines above the Cost bullet, and nowhere in or adjacent to the two scope lists a reviewer actually greps. | `[verified: docs/private-project-redaction.md:91-139 read in full]` |
| 9 | The composition bullet needs no amendment, but its named example is not an exhaustive list of derivation routes. Rate × pool-size count is the route it names. A second exists: a share plus an independently-public absolute — `pr-cost-section.sh` publishes an exact single-repo dollar total per PR, outside the class per `CLAUDE.md`, and a project-axis share against it would recover the mixed-corpus total. M1's axis restriction closes that route at the source, and `code-review-claude-config`'s P1 item catches the general shape ("a figure that composes with another published figure into a barred total") rather than only rate × count. The bullet's own lead-in is general, not an enumeration. | `[verified: docs/private-project-redaction.md:132-139; .claude/skills/code-review-claude-config/SKILL.md P1 item; cost.py:466-498]` |
| 10 | This plan's three new references are **not** covered by any existing test, so this PR adds one. `_all_skill_md_files` globs only `claude-skills/skills/*/SKILL.md`, `.claude/skills/*/SKILL.md`, and `plugins/*/skills/*/SKILL.md`, plus REFERENCES.md/ROUTING.md siblings — `docs/*.md` is outside it by construction, so `test_skill_citations_resolve_to_real_headings` never sees a citation in a `docs/` file. The repo already hit this exact gap once and solved it with a narrowly-targeted test rather than by widening the corpus. | `[verified: test_skills.py:2392-2406 (the three globs); test_skills.py:2878-2886, whose docstring states "docs/*.md sits outside _all_skill_md_files's scanned corpus … targeted narrowly here instead of widening that corpus"]` |
| 11 | `.claude/skills/code-review-claude-config/SKILL.md:9-14` needs no edit. Its lead sentence already scopes the whole item to material "whose only known source is private engagement material", and its "a raw pooled cost or duration total" bullet stays true after the amendment. | `[verified: file read in full, :1-28]` |
| 12 | `pr-description/SKILL.md:94-99` needs no edit. Its "not neutral… engagement scale" caveat is about who should set the per-account disclosure sentinel and what a PR reader learns from it — not about redaction-class membership. The amendment falsifies nothing it claims. | `[verified: pr-description/SKILL.md:91-99]` |
| 13 | Axis 3 permits the three sibling edits. Axis 3 makes preserved records "read-only **unless the ticket specifically asks to update them**" — a scope gate, not an absolute bar — and this plan satisfies the exact condition `narrow-provenance-redaction-rule.md:158` named ("amending them needs a task that scopes them"). | `[verified: .claude/plans/narrow-provenance-redaction-rule.md:158-160]` |
| 14 | Every withheld figure stays withheld. Each site's *recorded act* of withholding is preserved verbatim; only the normative rule paraphrase becomes a reference. | `[engineer-verified]` |
| 15 | All three sibling sentences are affirmatively wrong today, not merely stale. Each bars counts — "any count, ratio, or percentage", "Counts and durations", "any count, ratio, median, or duration" — while the Counts bullet has permitted a total, a share, or a median at any granularity since the carve-out landed. | `[verified: docs/private-project-redaction.md:114-117; cost-levers-considered.md:501; schedulewakeup-misapplied-documented.md:14; schedulewakeup-denied-by-bare-tool-name.md:7]` |
| 16 | Naming `pr-cost-section.sh` in the redaction doc creates a doc→script coupling with two distinct failure modes, only one of which is self-announcing. A rename or deletion leaves a citation pointing at a nonexistent file — visibly wrong. A later loosening of `--summary`'s single-repo/single-account guarantee is **silently permissive**: `cost.py`'s own suite would be updated to match the new behavior and pass, while M2's sentence keeps asserting the old guarantee as grounds for treating that output as outside the class. Accepted rather than closed: the normative sentence stands without the worked example, so the residual is a stale illustration, not a stale rule. | `[verified: the only test referencing this doc is test_install_dev.py:544,575, which asserts the filename appears in install.sh's TIP stderr — no test reads the Scope paragraph's content]` |

### Mechanisms

**M1 — Rewrite the Cost/Duration bullet's permitted-forms clause** (`docs/private-project-redaction.md:118-125`) to the repaired Option A text above. `anchors: root, row1, row3, row4, row5`

**M2 — Extend the "Scope — two closed lists" lead-in (`:108-110`) with the precondition and one worked case.** This is the answer to where the precondition goes: the lead-in already carries the section's other binding condition ("Both have to be satisfied, and neither extends by analogy"), a precondition belongs with it, and it sits 3–15 lines above the bullets rather than 18 lines below the paragraph that currently states it (row 8). Proposed text, appended to that lead-in as a paragraph before the two list items:

> Both lists govern the mixed-corpus figure "What it permits" opens with, and nothing else. A figure the repo-root `CLAUDE.md` rule's closing sentence already places outside this class is not reached by any condition below. `pr-cost-section.sh`'s output is the worked case: it calls `transcript-analysis.py cost --this-repo --branches <branch> --summary`, and `--summary` exits non-zero unless the scope resolves to this repository and one account.

The second sentence is a pointer, not a restatement — it does not re-list CLAUDE.md's three excluded sources, satisfying single-source-of-truth. The section already links that rule at `:96-98` as `` [`CLAUDE.md`](../CLAUDE.md) ``; reuse that exact link form on first mention if the implementer judges the back-reference too distant, otherwise plain text is fine since the link is 12 lines up. `anchors: root, row6, row7, row8, row16`

**M3 — Swap the rule paraphrase for a reference at three sibling sites.** All three apply the identical shape, each keeping its own surrounding sentence intact:

- `cost-levers-considered.md:501` → "That corpus mixes private-project and public transcripts, so publishing a figure from it is governed by `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" and its approval gate, unexercised here." (the engineer's approved wording, quoted here so the keeps-every-withholding-intact claim is checkable before implementation rather than after)
- `schedulewakeup-misapplied-documented.md:14` → "Counts and durations are not reproduced here: the corpus mixes this owner's private-project and public-repo transcripts, so publishing a figure from it is governed by `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" and its approval gate, unexercised here. See the upstream reports below for the full evidence."
- `schedulewakeup-denied-by-bare-tool-name.md:7` → "The supporting counts and context medians are not reproduced here — the corpus mixes private-project and public transcripts, so publishing a figure from it is governed by `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" and its approval gate, unexercised here."

Leave `cost-levers-considered.md:501`'s following sentence ("The `--this-repo`-scoped ratio above is not subject to that withholding — it is content derived only from this repo's own history") unchanged: it applies the class exclusion to one specific figure rather than restating the rule, and it is still correct. `anchors: row2, row13, row14, row15`

**M4 — Deliberately edit nothing else.** The composition bullet (row 9), the P1 provenance checklist item (row 11), `pr-description/SKILL.md` (row 12), `pr-cost-section.sh` / `cost.py` / `--summary` enforcement (rows 6–7 — the code is correct and fail-closed), and the test tree (row 10). `anchors: row9, row10, row11, row12`

**Over-powered-primitive check on M2.** The heaviest candidate for closing the `pr-cost-section.sh` false positive is a mechanical detector in `deny-private-project-refs.sh`, or a new pytest asserting the doc's prose. Two lighter primitives were weighed first and one is adopted:

- *Rely on `CLAUDE.md`'s existing class-exclusion sentence alone, no doc edit.* Fails: that sentence is already always-loaded and did not prevent the observed misread, because the section's scope lists read as self-contained (row 8).
- *Put the precondition in the P1 checklist item instead of the doc.* Fails: the P1 item already carries it in its own lead sentence (row 11), and the misread happens while reading the doc, so the fix would sit in a file the flagging session was not in.

The detector is rejected outright by G1 and by the prior plan's own reasoning; a new pytest is redundant per row 10.

**M5 — Add one narrow citation-resolution test** to `claude-skills/skills/tests/test_skills.py`, modelled on `test_handoff_nudge_doc_cites_handoff_warrant_check_section` (`:2878-2910` — the full function, including the `_resolve_citation_target` / heading-match assertions at `:2901-2910`, not only the shallow citation-presence check at `:2878-2897`), asserting each of M3's three rewritten citations resolves to a real heading in `docs/private-project-redaction.md`. Reuse that precedent's existing helpers rather than a new corpus concept — `_citation_report` takes `Iterable[Path]`, not a SKILL.md-specific type. Without it, renaming the "Publishing a pooled tooling measurement" heading strands all four citations to it — the three new ones *and* the existing one at `.claude/skills/code-review-claude-config/SKILL.md:12`, which `_extract_citations`'s heading group (`[^"\n]+`, newline-excluding) never matches today because the quoted heading is hard-wrapped across a line break there — each still reading as a well-formed citation to a heading that no longer exists, with no test noticing. `anchors: root, row10`

**Answer to the test-enforcement question (row 10, and the repo's write-the-enforcing-test-in-the-same-PR practice).** One new test, M5 — the semantic content gets none. The split follows what is mechanically checkable: citation-target resolution is, and the repo already has both a precedent and the helpers for it; whether a published figure is safely pooled is not, per G1, and a test asserting prose content would be manufactured coverage. The P1 provenance item in `code-review-claude-config` remains the commit-time check that operationalizes the rule itself.

### Coordination with PR 927 — resolved, no scope change

Settled with the session holding PR 927 (`review-round-cost-first-run`, open and now held unmerged). This plan is unaffected; recorded because the interaction was checked, not because it changes anything.

- **M1 does not rescue PR 927's headline figure**, contrary to this session's first reading. `30.3%` is `round $ ÷ branch $` — numerator and denominator are both branch/round constructs, so a reporting-form fix doesn't reach it. The more serious defect is upstream of any unit or form question: PR 927's own body states its corpus is pooled "across two machines' full transcript corpora — every declared account on each, not scoped to one repo," which fails the carve-out's own "*What may be counted:* this repo's own tooling in use … Nothing else" restriction outright, independent of how the figure is reported. Permitting the share form here is orthogonal to that scope defect and doesn't legitimize the figure.
- **A review round is not an agent dispatch.** It is a compound unit — main-thread turns plus a varying number of dispatches — so the countable list's "neither extends by analogy" forecloses reading it as covered. A real gap, in neither PR's scope.
- **Widening the countable list goes to the engineer directly**, from neither branch. It is a scope decision on a list the engineer deliberately closed, and this plan's axis is reporting form, not countable units.
- **Merge order: this branch first.** One-way dependency — PR 927's own paraphrase cannot become a pointer until the carve-out section exists on a citable branch. Nothing in this plan waits on PR 927.
- **PR 927 absorbs its own fourth paraphrase** after this lands. No edit to that branch from here.

## Critical files

**One `code-writer` dispatch, `model: sonnet`, no `isolation: "worktree"`** (the session is already anchored in this branch's worktree, and the output must land here). Do not split. All six edits share one piece of state — the exact amended Cost bullet text and the exact citation string — and a second dispatch would have to restate both to write the sibling references and the test coherently, which is the skill's named non-split condition. The diff is five files and roughly two dozen lines.

- **`docs/private-project-redaction.md`** *(modify, two edits)* — Cost/Duration bullet at `:118-125` (M1); "Scope — two closed lists" lead-in at `:108-110` (M2).
- **`docs/cost-levers-considered.md`** *(modify)* — `:501`, the engineer's approved wording verbatim.
- **`docs/design-decisions/schedulewakeup-misapplied-documented.md`** *(modify)* — `:14`.
- **`docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md`** *(modify)* — `:7`.
- **`claude-skills/skills/tests/test_skills.py`** *(modify)* — add M5's citation-resolution test beside the precedent at `:2878-2897`.

**Reuse, not reinvention:**

- The citation string `` `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" `` already exists at `.claude/skills/code-review-claude-config/SKILL.md:12`. Copy it byte-for-byte into all three sibling sites — not because CI already resolves that instance (it doesn't: the quoted heading there is hard-wrapped across a line break, so `_extract_citations`'s heading regex never matches it), but because it's the wording every reader of the rule already sees, and M5 gives it real coverage at the three new sites. **The quoted heading must land on a single unbroken line at each of the three new sites** — no mid-quote line break — or M5's own citation-extraction assertion will fail to see it.
- The relative link form `` [`CLAUDE.md`](../CLAUDE.md) `` already exists at `docs/private-project-redaction.md:96-97`; reuse it rather than constructing a new relative path.

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

This resolves through the blanket `docs/` rule (`select-tests.py:139-147`, `:468`) to `claude/.claude/hooks/tests` and `claude-skills/skills/tests` — the pair that holds M5's new test, which is what covers this diff's new references. `test_skill_citations_resolve_to_real_headings` in that same pair does *not* cover them; its corpus excludes `docs/*.md` (row 10). Modifying `claude-skills/skills/tests/test_skills.py` itself also trips two of `select-tests.py`'s exact-match file constants (`_is_py_source_under_claude_or_plugins`, `_is_test_source_change`), adding `claude/.claude/hooks/tests/test_ticket_reference_discipline.py` and `claude/.claude/scripts/tests/test_select_tests.py` to the target set — legitimate additions from a different domain, not the GH-882 domain-dir-plus-contained-file shape (that bug needs the selected domain dir and its own contained file both selected; these two extra targets are neither). `is_full_suite` stays `False` either way. Confirmed by direct execution of `select_pytest_targets` against this diff's current five Critical-files paths, which returns `target_paths=('claude-skills/skills/tests', 'claude/.claude/hooks/tests', 'claude/.claude/hooks/tests/test_ticket_reference_discipline.py', 'claude/.claude/scripts/tests/test_select_tests.py')`, `is_full_suite=False`.

Then `/code-review`, which loads `code-review-claude-config`'s P1 provenance item — the surface that would catch a figure accidentally published by an over-eager sibling-site edit. Three read-back checks it should confirm, none of them mechanical:

1. No number that was withheld at a sibling site before this diff appears in it after (row 14).
2. The word "share" in the amended Cost bullet attaches to Cost alone and not to Duration (row 4 — this is the specific defect being repaired, so it is the specific thing to re-read).
3. The three sibling citation strings are byte-identical to `.claude/skills/code-review-claude-config/SKILL.md:12`'s.

## Out of scope

- **Remediating already-published raw pooled cost totals.** These are non-compliant with the Cost bullet's "never as a raw pooled total" both before and after this amendment, and G2 makes remediation the owner's call. Verified this session, machine-wide/cross-account scope confirmed at `handoff-threshold-impact.md:27` and `handoff-hard-block-position.md:29`: `handoff-threshold-impact.md:31`, `:42-47` (the Above-threshold $ and Total $ columns), `:83`, `:112-114`; `handoff-hard-block-position.md:113-116`, `:121-124`. The amendment does legalize the *share* columns co-published in several of those same tables. Flag the set in the PR body; change nothing.
- **The in-repo-scoped figures in those same case studies.** `handoff-threshold-impact.md:74-77` and `:115-117` (claude-config-only) and `handoff-hard-block-position.md:80-81` (claude-config PRs, corroborated by `:87`'s independent `gh pr list` returning exactly the pooled n of 19) are outside the class and need nothing.
- **Whether "this repo's own history" tolerates pooling across multiple accounts.** The claude-config-only components above are single-repo but multi-account; `--summary` refuses multi-account for its own conservatism, not because the redaction rule draws that line. M2's precondition sentence is deliberately a pointer to `CLAUDE.md` and does not adjudicate this, and the worked case (`pr-cost-section.sh`) is single-repo *and* single-account, so it lands under either reading. Nothing in this plan depends on the answer. Raise it separately if the engineer wants it settled.
- **Publishing any figure the three sibling sites withhold.** G3 — the approval gate is unexercised, and each replacement sentence says so.
- **PR 943 itself.** Reachable — its worktree could be unlocked — and deliberately not touched: another session holds it mid-review, and the engineer owns the decision to close it. This branch's PR names it as superseded.
- **Any change to `pr-cost-section.sh`, `cost.py`, or `--summary`'s scope enforcement.** Rows 6–7: the code is correct and fail-closed. The false positive is a doc gap.
- **`pr-description/SKILL.md` and `.claude/skills/code-review-claude-config/SKILL.md`.** Rows 11–12. Editing either would also pull hook-enforced `/skill-review` into a docs-only PR for no defect being fixed, and a "this is not a redaction violation" clause added to `pr-description/SKILL.md` would duplicate M2's precondition — a second prose layer closing a gap the first one already closes.
- **A share-only output mode in `cost.py`.** Every render path (`:326-374`) co-emits the raw dollar value and grand total alongside the percentage, and no flag suppresses the absolutes. So exercising the newly-permitted mixed-corpus share means running `cost` without `--this-repo`, receiving the barred absolutes into the agent's own context, and hand-copying only the percentage. This predates the plan — the already-permitted rate form has the identical profile — but this amendment is what will route a mixed corpus through that path for the first time. Reachable and declined: it is a code change in a docs-only PR, and it shapes tool output rather than the rule. Worth a follow-up issue; flagged in the PR body.
- **A `deny-private-project-refs.sh` numeric or ratio detector.** Reachable — the hook is in this repo — and declined: G1 establishes a hook cannot see how a figure was computed, and the prior plan rejected building one on the same grounds.
- **A new `docs/design-decisions/` entry recording this amendment.** The plan file is the provenance and the amended doc is the current-state description; a third site discussing the same rule is what this plan is removing, not adding. Overridable if the engineer wants the PR 943 supersession recorded outside the PR body.
- **Unwrapping the pre-existing citation at `.claude/skills/code-review-claude-config/SKILL.md:12`.** Discovered this round (`staff-sdet`): its quoted heading is hard-wrapped across a line break, so `_extract_citations` never matches it and no test today checks it against a real heading — row 11 stands regardless, since that row is about the bullet's semantic content staying true, not about citation-regex coverage. Declined here: fixing an unrelated file's pre-existing test-coverage gap is outside this plan's file boundary (row 11), and M5 already gives the three new sites real coverage. Worth a small follow-up.
