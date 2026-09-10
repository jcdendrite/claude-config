# Redaction cost-share carve-out: follow-up items from PR #950

## Context

PR #950 ("Redaction cost share denominator," merged as `bb24fccb`)
amended the pooled-tooling-measurement carve-out to permit a Cost
figure expressed as a dimensionless share of pooled spend, split along
any dimension but project, account, or engagement. Its own "Out of
scope, flagged for the reviewer" section named four follow-up items the
engineer called "important... top of mind" but explicitly deferred from
that PR. This plan covers those four, now that PR #950 has merged:

1. Two already-published case-study docs (`docs/case-studies/handoff-threshold-impact.md`,
   `docs/case-studies/handoff-hard-block-position.md`) carry raw
   pooled-cost figures the amended Cost bullet still bars ("never as a
   raw pooled total"). Design a remediation proposal — do not execute
   any edit to these two files' figures without a fresh, explicit
   engineer go-ahead even after this plan is approved, per the redaction
   doc's own remediation paragraph (`docs/private-project-redaction.md:177-181`),
   which routes discovery of an already-published non-compliant figure
   through "stop and report," not a unilateral agent rewrite.
2. A second hard-wrapped, untested citation to the "Publishing a pooled
   tooling measurement" heading exists at `CLAUDE.md:164-165`, alongside
   the already-known one at
   `.claude/skills/code-review-claude-config/SKILL.md:12-13`. Both quote
   the heading split across a line break, so the citation-extraction
   regex added by PR #950 never matches either. Unwrap both onto one
   line and add test coverage.
3. `cost.py` has no share-only output mode — every render path
   co-emits the raw dollar value and grand total alongside the
   percentage, so exercising the newly-permitted mixed-corpus share
   today means running `cost` without `--this-repo`, receiving the
   barred absolutes into the agent's own context, and hand-copying only
   the percentage. Design a share-only mode.
4. Whether "this repo's own history" (`CLAUDE.md:169-170`) tolerates
   pooling across multiple `CLAUDE_CONFIG_DIR` accounts is untouched —
   the new precondition paragraph PR #950 added deliberately points to
   `CLAUDE.md`'s existing rule rather than adjudicating the question.
   Scope what adjudicating it would require (who decides, what
   evidence, where the answer gets recorded) — this plan does not
   answer the question itself.

Why now: PR #950 is merged, so nothing blocks starting on these, and
the engineer asked for this plan in the same session the merge was
confirmed. The four items are independent of each other and are
treated as four phases within this one plan file, each separately
dispatchable to `code-writer` where implementation work exists.

Per an explicit engineer decision this session: scope is these four
items only — two related items PR #950 flagged in a separate "Deferred
review findings" table (the composition-bar worked example not naming the new
`share` primitive, and missing test coverage for the substantive
carve-out conditions) are excluded from this plan.

## Approach

Four phases, sequenced by dependency rather than by the Context's numbering: **Phase A (item 2)** unwraps and test-covers the two citations, **Phase B (item 3)** adds a `--share-only` mode to `cost` that never reaches a dollar-printing site, **Phase C (item 4)** files the multi-account "own history" question as a decision request naming who decides and where the answer lands, and **Phase D (item 1)** produces a per-figure remediation *proposal* for the owner across a file set wider than the two case studies. The Context's claim that the four items are independent does not hold for the D↔C pair: several published figures are claude-config-scoped but pooled across accounts, and their verdict *is* the answer to item 4's question. C therefore precedes D, and D's inventory carries a third bucket for rows whose verdict C has not yet unblocked.

Phase B's design point worth stating up front: share-only takes an **early return before the shared render path**, rather than threading a suppression flag through the existing printers. That makes it fail-closed — a dollar-emitting print site added to `_cost_report` later cannot leak into share-only output, because share-only never reaches it.

Phase D's design point worth stating up front: the naive fix ("delete the raw-total column, keep the share") is insufficient on its own, for three reasons the inventory has to apply — a co-published rate beside its own `n` reconstructs the total exactly, raw pooled *token* totals are barred by the same Cost bullet as dollars, and several published counts are not on the carve-out's closed countable list. Details in rows 6–10 below.

### Assumption ledger

**Root problem.** PR #950 amended the pooled-measurement carve-out and left four gaps unclosed: two published doc surfaces carry figures the amended Cost bullet bars, two citations to the amending doc's own heading are invisible to the test PR #950 added, the tool that produces the newly-permitted share cannot emit it without also emitting the barred absolutes, and the "own history" boundary the amendment defers to is undefined for the multi-account pooling this repo's own published studies actually used.

**Givens** (fixed beyond this plan's reach):

- **G1.** Remediation of an already-published wrongly-scoped figure is the owner's call, not an agent's — `docs/private-project-redaction.md:177-181` assigns the decision to the owner by name, so no plan approval can transfer it. [verified: `docs/private-project-redaction.md:177-181`]
- **G2.** Git history retains every published figure regardless of what the tip says, so any remediation is forward-looking only and is not a retraction — the same source states this and bars an agent-run rewrite. [verified: `docs/private-project-redaction.md:179-181`]
- **G3.** The pooled-measurement tier is reviewer discipline with no hook behind it; a pooled figure's safety depends on how it was computed, which a hook cannot see. No mechanical enforcement of the raw-total bar exists, and this plan adds none. [verified: `docs/private-project-redaction.md:93-95`]
- **G4.** The two "Deferred review findings" items PR #950 flagged stay out of this plan. [engineer-verified]

**Assumption rows:**

1. `.claude/skills/code-review-claude-config/SKILL.md` is inside `test_skill_citations_resolve_to_real_headings`'s scanned corpus, so unwrapping its citation is sufficient there and needs no new test — `_all_skill_md_files()` globs `.claude/skills/*/SKILL.md` as one of its three roots. [verified: `claude-skills/skills/tests/test_skills.py:2392-2414`]
2. Repo-root `CLAUDE.md` is outside every scanned corpus, so unwrapping alone leaves it uncovered; the existing parametrized `test_pooled_tooling_measurement_citation_resolves_to_real_heading` takes repo-root-relative paths and `_assert_citation_resolves_to_heading` resolves them against `REPO_ROOT`, so `"CLAUDE.md"` slots into that param list unchanged. [verified: `claude-skills/skills/tests/test_skills.py:2878-2954`]
3. A hard-wrapped citation is a **silent** miss, not a failure: both citation regexes capture the heading with `[^"\n]+`, so a heading split across a line break yields zero matches rather than an assertion. Adding the two sites to a test closes those two, but the silent-miss *class* stays open for any future wrap. [verified: `claude-skills/skills/tests/test_skills.py:2676,2682`]
4. `_cost_report` has no share-only path: every render site co-emits dollars, and `--summary` is a scope-narrowing mode, not a value-suppression one — it still calls `_print_token_class_table` and `_print_thread_table` unconditionally. [verified: `claude/.claude/scripts/transcript_analysis/cost.py:826-891`]
5. `--summary` cannot express the mixed corpus item 3 needs: it requires `--this-repo` and refuses `--projects`, `--by-project`, `--no-redact`, and `--config-dir`. [verified: `claude/.claude/scripts/transcript-analysis.py:11482-11490`; `claude/.claude/scripts/transcript_analysis/cost.py:466-481`]
6. **Raw pooled token totals are barred exactly as dollar totals are.** The doc defines Cost as "dollar or token spend on running the tooling," and the same bullet bars a raw pooled total. `_print_token_class_table`'s `Tokens` column is therefore not safe to keep in share-only output, and `handoff-threshold-impact.md:81`'s token decomposition is a violation of the same class as its dollar columns — a figure neither the Context nor the Step 3 evidence listed. [verified: `docs/private-project-redaction.md:119-131`; `claude/.claude/scripts/transcript_analysis/cost.py:326-346`]
7. **A published mean beside its own `n` *is* the barred raw total, exactly.** The composition bullet bars a permitted rate times a permitted pool-size count, whether in one artifact or across two. `handoff-threshold-impact.md:132` states publishing "a rate or a ratio alongside the raw count" as the study's deliberate design principle, and its Tier 1 table plus `handoff-hard-block-position.md:78-81` both carry `n` and a mean in the same row. Deleting the Total column alone does not remediate those rows. [verified: `docs/private-project-redaction.md:138-145`; `docs/case-studies/handoff-threshold-impact.md:74-77,132`; `docs/case-studies/handoff-hard-block-position.md:78-81`]
8. **A share beside a count is not the same hazard.** A share is dimensionless, so share × count yields no dollar- or token-denominated figure; the composition bar binds rate × count. This is why Phase B's share-only output needs no count-suppression rule, and why Phase D's recommended replacement keeps `n`. [verified: `docs/private-project-redaction.md:122-131,138-145`]
9. **Several published counts are not on the carve-out's countable list.** The list closes at "Claude Code tool calls, sessions, and agent dispatches. Nothing else," and the doc says neither list extends by analogy. Sessions and reviewer dispatches qualify; branch counts, PR counts, findings counts, hook-denial counts, and nudge-log line counts do not — and in a machine-wide row they describe activity inside private-engagement repos, which `CLAUDE.md:152-156`'s provenance rule reaches independently of datatype. Read literally, this expands bucket-C remediation past the dollar columns into most of `handoff-threshold-impact.md:89-104` and `handoff-hard-block-position.md:93-105`. Flagged as the inventory's largest scope question, not settled here. [verified: `docs/private-project-redaction.md:108-117,147-150`; `CLAUDE.md:152-156`]
10. **Whether a three-era before/after split is the barred "time series" is genuinely open.** The doc permits a pooled figure "for the whole period covered only, never as a time series," with cadence re-exposure as the stated reason. Both studies are era splits of machine-wide pooled figures. If this resolves "barred," Phase D's remediation is structural (retract or re-pitch the studies), not columnar. Unresolved, and it changes the shape of the whole proposal. [verified: `docs/private-project-redaction.md:132-137`; `docs/case-studies/handoff-threshold-impact.md:27`; `docs/case-studies/handoff-hard-block-position.md:29`]
11. **Item 1's file set is narrower than the bug shape.** `docs/cost-levers-considered.md` republishes several of the same case-study figures and carries its own machine-wide raw pooled totals at `:207` and `:452-454`; `docs/transcript-analysis.md` carries 25 dollar-bearing lines that mix synthetic sample output with what appear to be real-corpus figures. Scoping the inventory to the two named case studies is the narrow-fix defect `CLAUDE.md`'s structural-siblings rule names. [verified: ripgrep over `docs/` for a `$N.NN` shape — 4 files, 61 occurrences; `docs/cost-levers-considered.md:207,452-454`; `docs/transcript-analysis.md:588,1199-1201,1324-1343`]
12. **The Axis-3 precedent does not settle item 1 either way.** `_all_doc_paths()` excludes `docs/case-studies/**` as "preserved historical records (CLAUDE.md Axis 3)." Axis 3 protects a record of what happened from cleanup edits; `CLAUDE.md:158-161` states the redaction bar as absolute and binding on "every artifact." The two rules collide, which is precisely why `docs/private-project-redaction.md:177-181` routes the call to the owner. Phase D presents the collision rather than resolving it by citing either rule alone. [verified: `claude-skills/skills/tests/test_skills.py:4262-4275`; `CLAUDE.md:158-161`]
13. Several published figures are claude-config-scoped but pooled across accounts or across two machines, so their verdict is the answer to item 4's question rather than an application of the current rule. `handoff-hard-block-position.md` pools two machines — a dimension neither `CLAUDE.md:158-170` nor the redaction doc names at all, making it a second open question beside the multi-account one. [verified: `docs/case-studies/handoff-hard-block-position.md:22,34,151`; `docs/case-studies/handoff-threshold-impact.md:23,27`]
14. No forum adjudicates item 4 — tier 3 is reviewer discipline with no named reviewer role, and `CLAUDE.md`'s only explicit human-escalation path is scoped to secrets. It falls to the repo owner directly. [verified: Step 3 exploration of `README.md:13,434-436` and `CONTRIBUTING.md:43-70`; `docs/private-project-redaction.md:93-95`; `CLAUDE.md:172-181`]
15. The nearest existing precedent for item 4 points toward "yes, own history" but is not dispositive: `docs/pr-cost.md:84-88` reasons that cross-account correlation risk is "specific to genuinely multi-tenant declared accounts, not a single operator's own machine" — a conclusion about correlating two accounts' raw rows, not about whether pooling counts as own history. [verified: Step 3 exploration of `docs/pr-cost.md:84-88`]
16. Phase D's own output is public-repo content. A plan or PR body that quotes the values it proposes to remove republishes them; `CLAUDE.md`'s project instructions state that a plan under `.claude/plans/` ships in the same PR and its cited evidence carries the same redaction rules. The inventory therefore cites file and line only. [verified: `CLAUDE.md` § "Plans in this repo affect all stow users"]

### Mechanisms

**M-A — unwrap both citations onto one line; add `CLAUDE.md` to the existing parametrized test; add a wrapped-citation guard.** `anchors: row1, row2, row3`. Unwrapping is the fix because the defect is in the prose, not the extractor: a citation is a single `` `target` § "Heading" `` token, and keeping it unbroken is what makes it greppable by a human as well as by the regex.

Over-powered-primitive check on the tempting alternative — loosening `_CITATION_WITH_TARGET_RE`'s heading capture to tolerate a newline. Two lighter primitives exist and one is adopted: (i) **unwrap the two sites** — adopted, zero corpus-wide behavior change, and `_normalize_heading` already handles everything else; (ii) **a separate line-scoped lint that fails on a citation whose opening quote does not close on its own line** — adopted, because it closes the silent-miss *class* row 3 names without touching the extractor. Loosening the extractor is rejected: an unanchored `"…"` span across lines can over-match on a stray unpaired quote, it changes behavior for every existing citation in the corpus, and it would silently bless the wrapped form the lint exists to prevent. The guard reuses `_closed_fence_line_indices` so a fenced example does not false-fire, and runs over `_citation_sources_for_skill_md`'s expansion of `_all_skill_md_files()` plus **`_all_doc_paths()`** — not an ad hoc "explicit doc list." `_all_doc_paths()` is the repo's existing full-doc-corpus sweep (it already excludes `docs/case-studies/**` and `docs/reports/**` per its own Axis-3 comment); the guard and the parametrized `test_pooled_tooling_measurement_citation_resolves_to_real_heading` list both draw from this one named helper so "the guard's corpus" is a single grep-able definition, not two independently-maintained lists that can drift apart. (`/plan-review`, staff-sdet finding: the plan's first draft named an unspecified "explicit doc list" whose only concrete referent was the 3–4-file parametrize list — narrower than `_all_doc_paths()` and missing at least `docs/handoff-nudge.md` and `docs/transcript-analysis.md`, which carry citations to other headings. Naming `_all_doc_paths()` directly closes that gap.)

**M-B — `--share-only` on the `cost` subcommand, implemented as a dedicated printer plus an early return in `_cost_report` placed before the first dollar-emitting call site.** `anchors: row4, row5, row6, row8`.

Refusals, each with its own reason, following `--summary`'s existing exit-2-on-stderr precedent at `cost.py:466-498` (all three of `--summary`'s own refusal blocks — `:471-477`, `:478-488`, `:489-498` — not only the first): `--by-project`, because the Cost share bullet permits a split "along any dimension but project, account, or engagement"; `--no-redact`, because it reintroduces project labels and session IDs into output whose whole purpose is publishability; `--summary`, because its scope is this repo on one account — the uncontested "own history" case where `pr-cost-section.sh` already publishes raw dollars into PR bodies, so suppression there buys nothing and the combination would create a render matrix with no consumer; **`--top`**, because it selects rows for the top-N-sessions table, which sits entirely after the early return and is never rendered under `--share-only` — accepting a value that never reads back is a dead combination by the same rationale that governs the other three refusals, not silent-accept (`/plan-review`, staff-backend-engineer finding: the plan's first draft left `--top`'s contract unstated despite the plan's own precedent for refusing dead combinations).

Suppressed rather than refused: the multi-root per-account section, the per-project section, the top-N-sessions table, the per-model unpriced-token detail block, and the `--branches` exclusion diagnostic. All sit after the early return. Multi-root scope is *not* refused — it is exactly the mixed corpus this mode exists to serve.

What share-only emits, as an explicit allowlist rather than a suppression list: a scope-and-period caption carrying a one-line pointer to `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" for the approval gate (a pointer, not a restatement — the gate has one canonical home); the STALE PRICING and PRICING INTEGRITY warnings unchanged, neither of which carries a figure; a share-only variant of the excluded-spend banner stating only that some spend was excluded as unpriced/unrecognized, with **no count** — a count of distinct model IDs is not on the carve-out's closed countable list ("Claude Code tool calls, sessions, and agent dispatches. Nothing else," `docs/private-project-redaction.md:108-117`) once `--share-only` runs under the multi-root/multi-account scope it's designed to serve, and the mode drops the count unconditionally rather than conditioning the banner's shape on scope (`/plan-review`, ciso-reviewer finding: the plan's first draft kept the model-ID count in the share-only banner, which the plan's own row-9 closed-list literalism — applied to case-study counts — would also reach here); and four two-column share tables — `Class | Share`, `Model | Share`, `Thread | Share`, `Bucket | Share` — computed from the same accumulators and `render._pct_of`, with no `$` column, no grand-total row, and no `Tokens` column.

**Test-assertion strategy (not a suppression-list change, a verification-design change):** `TestCostShareOnly` must not assert "no `$` or `Tokens` substring in stdout" as its leak check. This codebase's own markdown table renderers never attach `$` or the word `Tokens` to a data cell — only to the column header (`_print_token_class_table`'s pinned output is `"| input | 3.00 | 100.0% | 1,500,000 |"`, no `$`, no "Tokens") — so a malformed share-only table could leak a bare, mislabeled dollar or token figure and still pass a substring-absence check. Assert on parsed table structure instead: each of the four tables' header row is exactly `{Class, Share}` / `{Model, Share}` / `{Thread, Share}` / `{Bucket, Share}`, and every data cell in the non-label column matches a percentage shape (`\d+\.\d%`), never a raw-float shape. State this as the share-only invariant explicitly in the test class's docstring, next to the early-return line's own comment in `cost.py` — both should say the guarantee depends on (a) no dollar/token-emitting section being added to `_cost_report` ahead of the return and (b) this structural assertion being extended if a fifth share-only table or a differently-labeled column is ever added (`/plan-review`, staff-sdet and ciso-reviewer convergent finding: both independently flagged the substring-absence check as a lexical, not semantic, backstop — sdet from a current false-negative angle, ciso from a forward-drift angle; the fix is the same for both).

Over-powered-primitive check. Two heavier options rejected: a general `--format`/output-mode enum plumbed through every printer multiplies render branches and is fail-open for any print site added later; threading a `share_only` keyword through the three existing printers plus the two inline print blocks touches `TestCostMarkdownTablePrinters`' pinned assertions and is fail-open for the same reason. Two lighter options rejected: post-filtering existing output through a shell pipeline fails because the barred absolutes have already entered the agent's context by the time any filter runs — the requirement is suppression at the source, not at the sink; and reusing `--summary` fails on row 5. Not extended to `cost-trend`: a share-only time series is still a time series, barred outright by `docs/private-project-redaction.md:132-137`, so adding the flag there would create output with no permitted use.

**M-C — file the multi-account question as a GitHub issue naming both sub-questions, the decision-maker, the evidence needed, and the recording location.** `anchors: row13, row14, row15`. Two sub-questions, separated because they can resolve differently: Q1, does "this repo's own history" cover claude-config-scoped tooling usage pooled across several `CLAUDE_CONFIG_DIR` accounts on one machine; Q2, does it cover the same pooled across machines. Q1 gates Phase D's bucket-B rows; Q2 is unnamed anywhere in the current rule text and is surfaced by `handoff-hard-block-position.md`'s two-machine pooling. Evidence the decision needs: whether an account-pooled, `--this-repo`-scoped figure can be inverted to disclose anything about the non-claude-config repos those accounts also serve, plus the `docs/pr-cost.md:84-88` precedent and why it is not dispositive (row 15). Recording location: repo-root `CLAUDE.md`'s "Also redact structural fingerprints and provenance" section, as a clause on the sentence at `:169-170` — because `docs/private-project-redaction.md:96-98` and README already treat `CLAUDE.md` as canonical for this rule, and the redaction doc's own scope note at `:147-150` disclaims reaching anything `CLAUDE.md`'s exclusion already placed outside the class. Recording it in the doc instead would split one rule across two homes.

An issue rather than a note in this plan file: the plan file is written once and never re-read, so an unanswered question parked there is lost at merge; the repo's tracker is where a pending decision stays visible. The issue body states the question abstractly and cites no figure value, and includes a one-line reminder that the tier-3 carve-out's rules bind replies on the thread too — `deny-private-project-refs.sh` scans Claude Code's own `gh issue create`/`comment`/`edit` calls but not a reply typed directly in the GitHub web UI, so an informal numeric answer posted there bypasses the mechanical hook entirely (`/plan-review`, ciso-reviewer FYI).

**M-D — a per-figure remediation proposal, classified into three buckets, over a widened file set.** `anchors: row7, row9, row10, row11, row12, row13, row16`.

Buckets, applied per figure rather than per document: **A** — derived only from claude-config's own history on a single account, outside the class per `CLAUDE.md:169-170`, no action; **B** — claude-config-scoped but pooled across accounts or machines, verdict blocked on M-C's Q1/Q2, listed as pending rather than remediated; **C** — machine-wide, cross-account, or cross-repo, inside the carve-out and required to satisfy both closed lists.

Files inventoried: `docs/case-studies/handoff-threshold-impact.md`, `docs/case-studies/handoff-hard-block-position.md`, `docs/cost-levers-considered.md`, and a classification-only pass over `docs/transcript-analysis.md` separating synthetic sample output from real-corpus figures (row 11).

Five barred classes the inventory applies to each bucket-C figure: raw pooled dollar totals; raw pooled token totals (row 6); rate-beside-`n` composition (row 7); counts absent from the closed countable list (row 9); and the era-split time-series question (row 10).

Recommended replacement, presented for the owner to accept or reject rather than assumed: **keep `n` and the shares, drop the dollar-denominated rate columns.** Rationale — `n` is the credibility anchor both studies' own small-n framing depends on (`handoff-threshold-impact.md:137`, `handoff-hard-block-position.md:140`), the shares carry the comparative claim each study actually makes, and the dollar rate is the one term that composes with `n` into the barred total. The cost is real and is why this is the owner's call: Tier 1's effect size stops being expressible in dollars at all. The alternative cut — keep the rates, drop `n` — was set aside because it strips the studies of the denominator their own statistical-framing sections commit to publishing.

Whole-document options the proposal must also carry, because row 10 could force them: retract both studies from `docs/case-studies.md` and the tree, or re-pitch them at a permitted altitude. Recommendation is replace-in-place at the tip, no history rewrite (G2 forecloses it), no retraction — conditional on row 10 resolving that an era split is not the barred series.

The proposal cites every figure by file and line and quotes no value (row 16). It does not edit any file.

## Critical files

**Phase A (Context item 2)** — one `code-writer` dispatch; the three files are edited together and the test change is meaningless without the prose change.

- `CLAUDE.md` (modify) — unwrap the citation currently split across `:164-165` so the quoted heading sits on one line. Preserved-content check: this is a rule description, not a record, so Axis 3 does not apply.
- `.claude/skills/code-review-claude-config/SKILL.md` (modify) — unwrap the citation currently split across `:12-13`.
- `claude-skills/skills/tests/test_skills.py` (modify) — add `"CLAUDE.md"` to `test_pooled_tooling_measurement_citation_resolves_to_real_heading`'s param list at `:2929-2936`; add the wrapped-citation guard test. **Reuse:** `_assert_citation_resolves_to_heading` (`:2878`) for the param addition — do not write a new resolution helper. **Reuse:** `_closed_fence_line_indices`, `_blank_frontmatter`, `_citation_sources_for_skill_md` (`:2807`), and `_all_skill_md_files` (`:2392`) for the guard's corpus and fence handling. No new test is needed for the SKILL.md site — row 1.

**Phase B (Context item 3)** — one `code-writer` dispatch; the flag and the printer are one change and the tests pin it.

- `claude/.claude/scripts/transcript_analysis/cost.py` (modify) — add `_print_share_only_tables`; add the refusal block and the early return in `_cost_report`, positioned before the `_print_token_class_table` call at `:866`; add the `share_only` variant to `_print_excluded_spend_banner` (`:377`, no count — see M-B). **Reuse:** `render._pct_of` for every share cell, `pricing._TOKEN_CLASSES` for row order, and the existing `class_totals`/`model_totals`/`bucket_totals`/`main_total`/`subagent_total` accumulators — compute nothing new. Do not reuse `pricing._reportable_unpriced_model_ids` for a share-only count — M-B's design drops the count entirely.
- `claude/.claude/scripts/transcript_analysis/scope.py` (**read-only**) — `_resolve_cost_roots` (`:488-524`) is the CLI-level gate that resolves `--summary` to a single-account root set via an `args.summary`-specific check at `:523-524`. Do not extend this check to `args.share_only`: `--share-only`'s multi-root design depends on falling through to the normal declared-roots union at `:526+` unchanged. An implementer generalizing the `--summary` gate by analogy (`args.summary or args.share_only`) silently collapses `--share-only` to single-root, contradicting M-B's stated multi-root intent (`/plan-review`, staff-backend-engineer finding: this function was absent from the plan's first Critical-files draft).
- `claude/.claude/scripts/transcript-analysis.py` (modify) — add `--share-only` to the `p_cost` parser at `:11439-11491`, adjacent to `--summary`.
- `claude/.claude/scripts/tests/test_transcript_cost.py` (modify) — a `TestCostShareOnly` class covering: each refusal's exit code (including `--top`); the four share tables' header-set and percentage-shaped-cell structure per M-B's test-assertion-strategy note (not `$`/`Tokens` substring absence); suppression of the per-account and top-N sections under a multi-root scope, using the `tmp_path`-rooted synthetic-corpus pattern `TestCostMultiRootReport`/`TestCostByAccount` already use (not a manual/by-eye check — see Verification); the banner variant's count-free text under both a priced-only and an all-unpriced corpus; a zero-session multi-root scope (`render._pct_of` is zero-safe, but assert the caption/warning-only content renders with no stale-total artifact); and one `build_parser().parse_args(["cost", "--share-only", ...])`-level test for the flag's argparse wiring, following the existing pattern in `test_by_project_flag_composes_with_this_repo_at_argparse_level` and `cost-trend`'s `--no-redact` refusal test. **Do not modify** `TestCostMarkdownTablePrinters` (`:1708`) — it pins `_print_token_class_table`/`_print_model_id_table` as standalone functions called directly, never through `_cost_report`, so it cannot regress from an early return placed inside `_cost_report`; it is not, however, the guard for `_print_excluded_spend_banner`. **Do not modify `TestExcludedSpendBanner`** (`:2682`) either — it pins that function's existing two branches (`markdown=True`/`False`) and is the actual regression guard for the function Phase B adds a third branch to (`/plan-review`, staff-backend-engineer finding: the plan's first draft named only `TestCostMarkdownTablePrinters` as the guard for a function `TestCostMarkdownTablePrinters` doesn't cover).
- `docs/transcript-analysis.md` (modify) — document the flag, its four refusals (`--by-project`, `--no-redact`, `--summary`, `--top`) with reasons, and that it is not available on `cost-trend`.

**Phase C (Context item 4)** — no repository file changes. Deliverable is a GitHub issue. `CLAUDE.md` is named read-only, as the recorded landing point for a future answer at `:169-170`; this plan writes nothing there.

**Phase D (Context item 1)** — no repository file changes. Deliverable is a written proposal to the owner. Read-and-propose targets: `docs/case-studies/handoff-threshold-impact.md`, `docs/case-studies/handoff-hard-block-position.md`, `docs/cost-levers-considered.md`, `docs/transcript-analysis.md`, with `docs/private-project-redaction.md:91-181` and `CLAUDE.md:145-170` as the rule text applied. Not dispatched to `code-writer` — there is no code to write, and the classification in rows 9, 10, and 12 is judgment the dispatching session should hold rather than fan out.

Phases A and B are disjoint file sets and can run in parallel; both share this worktree, so no `isolation: "worktree"`. C precedes D.

## Verification

Repo-documented command, scoped to the diff, per `CLAUDE.md`'s Commands section:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

Targeted runs while iterating:

```bash
.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k citation          # Phase A
.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_cost.py -k Cost   # Phase B
.venv/bin/ruff check claude/.claude/ claude-skills/                             # Phase B lint
```

Phase A's guard test is its own completeness check: it must fail before the two unwraps and pass after, confirming no third wrapped site exists anywhere in `_all_doc_paths()` ∪ `_all_skill_md_files()` — the guard's named corpus, not an unscoped "anywhere in the repo" claim. Confirm that ordering explicitly rather than only observing a green run.

Phase B's multi-root suppression and share-table content are covered by the same `tmp_path`-rooted synthetic-corpus pattern `TestCostMultiRootReport`/`TestCostByAccount` already use — `capsys`-captured stdout from a direct `_cost_report(...)` call is the same stream a terminal invocation produces, so no manual "by eye" step is needed for that surface (`/plan-review`, staff-sdet finding: the plan's first draft asked for a manual multi-root check reasoning that a captured-stdout test "does not exercise the path a human or agent actually invokes" — that reasoning doesn't hold for this codebase's existing fixture pattern). The one surface the automated suite genuinely can't reach is argparse-level wiring — a typo in the flag name or a `dest=` mismatch that only `build_parser().parse_args(...)` (not the bare `_cost_args()` Namespace helper most tests use) would catch; that's why Critical Files adds one `build_parser()`-based test for `--share-only` rather than relying on `_cost_args()` alone.

Confirm `select-tests.py` selected the skills domain for Phase A and the transcript-analysis domain for Phase B before trusting a green result — GH-882's under-collection bug drops a domain directory when that directory and a file inside it are both selected.

Review-pipeline dispatches these phases trigger, per `.claude/rules/review-pipeline-dispatch.md`: Phase A edits a `SKILL.md`, so `/skill-review` is hook-enforced on commit; Phase A also edits `CLAUDE.md`, so `ai-instruction-and-memory-files` applies.

Phases C and D produce no code and have no test. Their verification is a review pass: every inventory row cites a file and line and quotes no value (row 16); every bucket assignment names which corpus statement in the source document grounds it; and the proposal reaches the owner with the row 9 and row 10 questions stated as open rather than answered.

## Out of scope

- **Executing any edit to a published figure in `docs/case-studies/handoff-threshold-impact.md`, `docs/case-studies/handoff-hard-block-position.md`, `docs/cost-levers-considered.md`, or `docs/transcript-analysis.md`.** Approval of *this plan* authorizes producing the proposal and nothing else. Any edit to those documents' data requires a fresh, separate, explicit go-ahead from the engineer in a live session, per `docs/private-project-redaction.md:177-181` (G1). A general "ship it" on this plan is not that go-ahead.
- **Rewriting git history to remove an already-published figure.** Barred outright by the same paragraph, and futile: a public repo's history can be cloned, forked, or cached, so a rewrite is not a retraction (G2).
- **Answering item 4's Q1 or Q2.** Phase C scopes and files the question; it does not decide it. Until it is decided, Phase D's bucket-B rows stay listed as pending.
- **Resolving row 9 (are branch, PR, findings, denial, and log-line counts publishable?) or row 10 (is a three-era split the barred time series?).** Both are readings of the rule text that change the size and shape of the remediation, and both go to the owner inside the Phase D proposal.
- **Mechanical enforcement of the raw-total bar.** G3 records that tier 3 is deliberately reviewer discipline; a hook cannot see how a figure was computed. This plan does not add a detector, and the Phase A guard is a citation-wrapping lint, not a redaction check — do not let one be read as the other.
- **A `"share"` value for the `pr-cost-disclosure` sentinel, and any change to `claude/.claude/scripts/pr-cost-section.sh` or `claude-skills/skills/pr-description/SKILL.md`.** The automated PR-body path already operates inside `--summary`'s single-repo, single-account scope, which is the uncontested "own history" case; switching what it publishes is a separate decision with its own blast radius across every stow consumer.
- **`--share-only` on `cost-trend`.** A share-only time series is still a time series (`docs/private-project-redaction.md:132-137`).
- **Loosening `_CITATION_WITH_TARGET_RE` to tolerate a wrapped heading.** Rejected on the M-A grounds above; recorded here rather than as a given because the plan could change it and deliberately will not.
- **The two "Deferred review findings" items PR #950 flagged** (G4).
- **Widening `_all_doc_paths()` to include `docs/case-studies/**`.** Its exclusion is a state-path-contract scoping decision, not a redaction one, and changing it would pull an unrelated test's corpus into this plan.
