# Test whether front-loading Opus into authoring reduces review rounds

## Context

Determine, with measured evidence, whether spending Opus earlier in the
authoring/planning path in claude-config — rather than only at the points
it is already used (`plan-architect` in `/plan-it` Step 5, and ad hoc
`consult`-mode dispatches) — reduces the number of review-and-fix rounds a
typical change needs before it lands, and record a verdict (adopted,
rejected, or inconclusive) in `docs/cost-levers-considered.md` either way.

A prior investigation (`docs/cost-levers-considered.md`, "Model routing
(reduce Opus usage)" row) established that Opus's current 15.7%-of-spend
share is correctly sized for its *existing* usage points. It never asked
whether adding Opus at a new, earlier authoring point would prevent
review-flagged defects from occurring in the first place. Reviewer-yield
data (GH-762, corrected pull 2026-08-30) shows this repo's reviewers are
catching mostly-real defects — found-edit rates 17.5-66.7%, zero-finding
edit rates mostly 22-33% — so the remaining cost lever is arriving at
review with fewer real defects already present, not spawning fewer or
cheaper reviewers.

The intended outcome is a recorded, evidence-backed verdict — adopted,
rejected, or inconclusive — with no `model:` pin changed unless the data
clearly supports it. If it does, that change ships as its own separate,
scoped plan (not bundled here).

## Approach

Run the measurement entirely retrospectively against instruments this repo already ships, gated by evidence bars written into this plan file *before* any scan runs, and publish the verdict whichever way it falls. No `model:` pin changes here — the corpus already contains the only variation worth reading. No forward-looking controlled pilot either — see Alternatives considered below for why (a pilot would cost weeks to produce an effect size this repo has previously measured as unresolvable in the available window).

**The three phases.**

*Phase 1 — baseline and proxy validation.* Phase 1's deliverable is the distribution of review-and-fix rounds for a typical merged change. Sampling method (delegated decision, settled here): **every branch in this repo's own corpus with a merged PR in the rolling transcript window — the whole population, not a sample.** A stratified sample buys nothing when the population already fits in one scan, and it adds a selection step that `plan-review` would rightly ask me to defend. The instrument is `review-trace --this-repo --skill code-review`, which counts literal `/code-review` Skill invocations per branch and — unlike `subagent-mix` — prints raw branch names, so it joins to `gh` on the real `headRefName`. Report median and interquartile range, never the mean: the round count is a small right-skewed integer (0–8 observed), and this register has already been burned once by a mean hiding a right-skewed tail (`docs/cost-levers-considered.md`, "Context cost root cause" — "A right-skewed distribution hides a tail a mean can't see").

The same joined table does double duty: it validates whether `commit_count` (pre-squash, from `gh`) tracks the literal round count. That validation is what licenses Phase 3, whose rows come from PRs whose transcripts have already aged out.

*Phase 2 — is there any within-agent model variation to read?* The one place the corpus could contain a near-clean natural experiment is model-pin slippage: `case-studies/plan-mode-model-resolution.md` established that under harness plan mode a subagent's `model:` pin is not honored (129/131 forced to Opus regardless of pin or explicit `model` param). A `code-writer` dispatch that resolved to Opus despite its `sonnet` pin is the same agent, same prompt shape, running on a better model, with assignment driven by a harness quirk rather than by task difficulty — the only variation in the corpus that is plausibly independent of how hard the change was. `subagent-mix`'s model-mix table reports exactly this as `Declared` vs. `Observed` per `agentType`. Phase 2 checks whether that population exists at usable size; if it does not, that absence is itself the reportable finding and it prices what a pilot would have to manufacture.

*Phase 3 — the powered observational screen, and the verdict.* The `pr-cost` ledger is the only frame carrying treatment (`plan_file_added`, and `opus_dollar_share_pct` as a continuous Opus dose), a rework outcome (`commit_count`, `review_comment_count`), and complexity covariates (`additions`, `deletions`, `changed_files`, `distinct_top_level_dirs`, `distinct_file_extensions`, `risk_surface_flag`) **in a single row**. That matters more than it looks: `subagent-mix`, `subagents`, and `pr-cost` all redact branch names to opaque, run-local labels once more than one root resolves, and this machine declares six — so no cross-tool join keyed on a branch name is available at all for those instruments. A self-contained row sidesteps the problem instead of fighting it. Stratify by `changed_files`, compare outcome across Opus-dose strata within each, and bootstrap the direction.

**Pre-registered gates.** Each is fixed before the first scan, in the committed plan file, so the verdict can be checked against the bar rather than against a story told afterwards. This follows the register's own house style — `delegate-instrument-authoring.md` "fixed its go/no-go rule before running the measurement," and `plan-boundary`'s five-part criterion is what made its null result reportable.

- **G0 (ledger arm feasibility).** The ledger holds ≥100 rows for `repo == jcdendrite/claude-config` with populated `plan_file_added` and `opus_dollar_share_pct` cells. Grounded in the known 145-row population, leaving headroom for repo-filtering. Fail → Phase 3 does not run; the study reports on the transcript window alone and names the reduced n.
- **G1 (proxy validation).** Across merged PRs in the overlap window, mean `commit_count` rises monotonically across `/code-review`-round buckets (0, 1, 2, 3+), with at least 10 PRs in each of two adjacent buckets. The monotonic-across-buckets form is `handoff-nudge-cap-recalibration.md`'s own standard; the 10 floor is `reviewer-yield`'s documented `insufficient` threshold (`docs/transcript-analysis.md:259`). Fail → `commit_count` is not a round proxy, Phase 3's outcome variable is withdrawn, and the verdict rests on the transcript window only.
- **G2 (within-agent arm).** ≥20 `code-writer` dispatches with an Opus `Observed` model against the `sonnet` `Declared` pin, spread over ≥10 distinct branches. The 20 floor is `plan-boundary`'s own explicit "20-session floor" (`docs/cost-levers-considered.md:182`). Fail → record that the corpus contains no usable within-agent variation.
- **G3 (verdict rule, and its deliberate asymmetry).** Because `/plan-it` selects for complexity by its own DO-NOT-TRIGGER criteria, the observational arm is confounded in a known direction, and the pre-registered reading is therefore asymmetric:
  - **Adopt-candidate** requires the higher-Opus-dose stratum to show a *lower* outcome than the lower-dose stratum *within the same `changed_files` stratum*, with the direction surviving a 2,000-resample bootstrap in ≥95% of resamples. Both the resample count and the robustness level come from `plan-boundary`, which reported 98.2% and 100% on the same instrument.
  - **Inconclusive** if the direction is unstable or the strata are too thin.
  - **Reject is reachable only from G2's within-agent arm, never from the observational arm alone** — "more Opus, more rounds" is precisely what the complexity confound predicts, so reading it as refutation would be reading the confound.

Committing this asymmetry in advance is the whole reason the observational arm is worth running; without it, the arm degenerates into post-hoc storytelling.

**Alternatives considered and set aside.**

*A forward-looking controlled pilot.* Set aside, not deferred. Three reasons, in descending weight. It requires flipping a `model:` pin for the treatment arm, which is the routing change step 5 explicitly puts outside this plan — a "temporary" flip is still a shipped routing change. `absolute-token-handoff-threshold.md` already closed a structurally identical pilot ("Delegation-discipline pilot") as unmeasurable in the available window on exactly this repo's throughput and noise floor. And the outcome is a small-count integer with high variance, so detecting anything under a full round would need an n this repo does not produce inside the 30-day retention window. Named in Out of scope with this reasoning rather than left implicit.

*Reading `plan-architect` presence off `subagent-mix`'s per-branch "Top subagent types" column* — a retrospective signal considered as an alternative. Set aside as unsound: `transcript-analysis.py:2423` renders `top[:5]` sorted by descending count, so a branch with a single `plan-architect` dispatch and six-plus subagent types drops it from the display. The omission is not random — it correlates with branch busyness, which is the confounder. `subagent-mix`'s untruncated `PR` column (`/plan-review` spawns) is the better in-table indicator, and `pr-cost`'s `plan_file_added` is better still.

*Adding a `transcript-analysis.py` subcommand to compute this join directly.* Set aside as heavier than the task requires. Two lighter primitives were checked and one is sufficient: (a) `review-trace` + `gh` + a direct ledger read covers every figure this study publishes, using only shipped, documented instruments; (b) `subagent-mix`'s first table alone is insufficient, for the truncation reason above. Precedent exists both ways — `instrument-authoring` and `plan-boundary` shipped as standing subcommands — but those measurements needed per-turn repricing that no combination of existing commands produced. This one does not.

*Extending the frame to every merged PR ever via `gh` alone (n ≈ 500+).* Set aside. More n does not fix confounding, and a window spanning PRs #1–#788 crosses several regime changes in the review pipeline itself (reviewers added, hooks added, two cap retunes), converting a static confounder into a time-varying one. Available as a bounded robustness check if Phase 3's strata come out thin; not the primary frame.

*Joining `subagent-mix`'s `CR` column to `pr-cost` rows by branch name.* Impossible, not merely awkward: both sides redact to `account-<K>/branch-<N>`, `subagent-mix`'s labels are "stable only within one run" (`docs/transcript-analysis.md:160`), and `pr-cost` has "deliberately no `--no-redact` escape hatch" (`docs/pr-cost.md:96`).

**Assumption ledger**

**Root problem.** This repo has no measurement of whether model capability at the *authoring* turn changes how many review-and-fix rounds a change needs, so any decision to front-load Opus would be anecdote-driven; the deliverable is a recorded verdict in `docs/cost-levers-considered.md`, not a routing change.

**Givens** (conditions the design treats as fixed and cannot reach):

- Local transcript retention is a rolling window (`cleanupPeriodDays`, default 30d) — harness-owned; nothing this plan does restores aged-out transcripts, which is why a durable ledger is the only path to n beyond one month.
- Rows captured under different `rate_stamp` values are not dollar-comparable — vendor rate tables change on the vendor's schedule (`docs/pr-cost.md:76`).
- Treatment assignment in every retrospective arm was decided historically, by the engineer and by harness behavior, and cannot be randomized after the fact — the past is not reachable by any design choice available here.

**Mechanisms.**

- *`pr-cost` ledger as the primary analysis frame* — `anchors: root, row7`. The only in-corpus frame carrying treatment, outcome, and complexity covariates in one row, so it survives the redaction that forecloses every cross-tool branch join, and it supplies the stratification that partially addresses the assignment confound.
- *`review-trace` + `gh` overlap-window join for proxy validation* — `anchors: row1`. Validates `commit_count` against literal round counts in the window where both still exist, before applying it to rows whose transcripts are gone. Uses unredacted instruments (`docs/transcript-analysis.md:47`) so the join key is a real branch name.
- *Pre-registered gates G0–G3 written into the committed plan* — `anchors: root`. Without a bar fixed in advance, an observational screen with a known confound produces a narrative rather than a verdict.
- *Read-only ledger use by default, `--record` conditional and non-blocking* — `anchors: root`, and the "Creating `<config-dir>/.pr-cost-enabled`" Out-of-scope bullet below. The lighter primitive (read the existing 145 rows) is sufficient for G0. The heavier one, `--record`, is a durable out-of-repo write that under six roots needs `--all-accounts` and would touch up to six accounts' ledgers, each gated by that account's own consent sentinel. It runs only if the sentinel already exists, and its absence degrades n rather than blocking the study.
- *Deliverable split across register row and case study* — `anchors: root`. The register states its own scope as "the verdict plus the measured reason, not the full investigation" (`docs/cost-levers-considered.md:9-10`), and the evidence volume here matches the rows that already carry a `case-studies/` companion.

**Rows.**

1. `commit_count` (pre-squash) tracks review-and-fix rounds closely enough to serve as the ledger-era outcome — `[unverified]`. G1 exists to test exactly this and to withdraw Phase 3's outcome variable if it fails; everything downstream of Phase 3 inherits the flag until G1 resolves.
2. Every current ledger row carries populated `plan_file_added` and `opus_dollar_share_pct` cells — `[unverified]`. Both are in the schema (`docs/pr-cost.md:25,31`) and the row parser is strict on column count (`docs/pr-cost.md:34`), but the schema alone does not show whether the values were populated at capture time. G0 checks it.
3. `subagent-mix`'s per-branch "Top subagent types" cannot serve as a `plan-architect` presence indicator — `[verified: claude/.claude/scripts/transcript-analysis.py:2422-2423]`, `top[:5]` after a descending-count sort.
4. `subagent-mix`'s `CR`/`PR`/`RR` columns count `/code-review`, `/plan-review`, `/ready-for-review` Skill invocations per branch and are not truncated — `[verified: transcript-analysis.py:2392-2395, 2424-2428]`.
5. `review-trace`, `buckets`, and `pr-link` print raw branch names under the default multi-root union; `subagent-mix`, `subagents`, and `pr-cost` do not — `[verified: docs/transcript-analysis.md:47; docs/pr-cost.md:96]`.
6. `pr-cost` refuses (exit 2) whenever more than one scan root resolves unless `--all-accounts` is passed, and this machine resolves six roots — `[verified: docs/pr-cost.md:80; six-root header observed in this session's `subagent-mix --this-repo` run]`. Consequence: the ledger read loops per account and must be filtered to this repo's own rows.
7. The ledger holds 145 rows, PRs #278–#698, merged 2026-05-20..2026-08-18, covering 44% of merged PRs in that range — `[verified: docs/case-studies/pr-cost-context-bucket.md:29-39]`. Its stated survivorship bias toward recent, longer-lived branches is inherited by this study and must be restated in the case study's own limits section.
8. Under harness plan mode a subagent's `model:` pin is not honored — 129/131 forced to Opus regardless of pin or explicit `model` param — `[verified: docs/cost-levers-considered.md:104-113; docs/case-studies/plan-mode-model-resolution.md]`. This is the only known source of within-agent model variation and is the entire basis for Phase 2.
9. Every on-disk agent file is pinned `model: sonnet` except `plan-architect` (`model: opus`), and `general-purpose`/`Plan` are harness built-ins with no file, so they inherit the parent — `[verified: grep of `claude/.claude/agents/*.md`, this session]`.
10. `/plan-it` Step 5 dispatches `plan-architect` with an explicit `model: "opus"` on every run, and Step 6 hands off to `/plan-review` — `[verified: claude/.claude/skills/plan-it/SKILL.md:49, 115]`. This is what makes `plan_file_added` and the `PR` column usable as plan-it-ran indicators.
11. `docs/case-studies/**` is exempt from the per-account state-path contract; `docs/cost-levers-considered.md` and `docs/case-studies.md` are covered by it — `[verified: claude/.claude/skills/tests/test_skills.py:2885-2898]`. The two covered files must write `<config-dir>`, never a literal `~/.claude`.
12. A docs-only diff selects the doc-dependent tests through `select-tests.py`'s blanket `DOCS_DIR` rule rather than a per-file constant — `[verified: claude/.claude/scripts/select-tests.py:90-97]`.
13. No test enforces `docs/case-studies.md` index completeness — `[unverified]`; a grep over `claude/.claude/**/*.py` found no such assertion, and absence of a match is weaker evidence than a positive read. Treat the index entry as a checklist item, not something CI will catch.
14. The engineer delegated the sampling method, the controlled-vs-natural comparison choice, and the "is any pin change warranted" call to this plan — `[engineer-verified]`.
15. `../../../.venv/bin/python3` is the working interpreter path for `transcript-analysis.py` from inside this worktree — `[verified: run in this worktree this session]`.
16. `gh` is authenticated in this worktree against `jcdendrite/claude-config` — `[verified: `gh issue view 762` and `gh pr list` run this session]`.

## Critical files

**Create**

- `docs/case-studies/opus-frontload-review-rounds.md` — the full empirical record. Follow `docs/case-studies/pr-cost-context-bucket.md`'s section shape exactly: *Why this measurement was needed* → *Method* (every command verbatim with its scope flags and run date) → *Results* (tables carrying per-cell n) → *Limits of this result*. The Limits section must carry, at minimum: the ledger's 44% coverage and its survivorship bias, the unrandomized assignment given, whichever gates failed, and the single-machine/single-repo scope. Exempt from the state-path contract (row 11), so a literal `~/.claude` path is permissible here.

**Modify**

- `docs/cost-levers-considered.md` — one new section, `## From \`opus-frontload-review-rounds.md\` — "Front-loading Opus into authoring: does it cut review rounds?"`, opening with a `Full empirical record: [\`case-studies/opus-frontload-review-rounds.md\`](case-studies/opus-frontload-review-rounds.md).` line per the hashline-edit-format / cost-attribution-integrity / handoff-threshold-impact convention, then a `| Lever | Verdict | Measured reason |` table. Expect three to four rows: the headline front-loading verdict, the `code-writer`-Opus-authoring candidate specifically, the forward-controlled-pilot decision, and (if G1 or G2 failed) the instrument limitation as its own row so the next plan does not re-derive it. Under the state-path contract — write `<config-dir>`, not `~/.claude`.
- `docs/case-studies.md` — one index bullet in the same bold-title-plus-question-plus-headline-figure form as the existing eight entries. Nothing enforces this (row 13); it will not fail CI if forgotten.
- `.claude/plans/opus-frontload-review-rounds.md` — committed per `plan-it` Step 7 *before* Phase 1 runs. Not a formality here: the committed plan is what makes G0–G3 pre-registered rather than retrofitted, and the case study cites it as the pre-registration artifact.

**No code changes.** No new `transcript-analysis.py` subcommand, no new ledger column, no agent `model:` pin edit.

**Reuse — call these, do not reimplement**

- `transcript-analysis.py review-trace --this-repo --skill code-review --since <run date − 30d>` — a per-session, per-event timeline (not a pre-aggregated per-branch table): each event carries its own resolved branch, and a branch's work can span multiple session files, so the per-branch round count is derived by grouping `skill=code-review` events by resolved branch across all matched sessions. Repeat with `--skill plan-review` for the treatment indicator. Not publish-safe raw; only aggregates leave the analysis.
- `transcript-analysis.py subagent-mix --this-repo --since 30d` — Phase 2's model-mix table. Output is large and carries a `DO NOT PUBLISH` banner; run it inside a `general-purpose` dispatch (`model: sonnet`) that returns only the `code-writer` and `plan-architect` rows' `Runs`/`Declared`/`Requested`/`Observed` cells.
- `transcript-analysis.py pr-cost --this-repo --all-accounts` in read mode (no `--record`), or a direct read of each declared account's `$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv`. **Filter to `repo == jcdendrite/claude-config` before computing anything** — the ledger read spans every repo the account has captured, and an unfiltered aggregate would mix private-project rows into a figure destined for a public doc. Never hand-edit the file (`docs/pr-cost.md:60`).
- `transcript-analysis.py buckets --this-repo` — per-branch Opus/Sonnet main-thread turn counts with real branch names, as the continuous Opus-dose covariate for the transcript-window arm.
- `gh pr list --repo jcdendrite/claude-config --state merged --search "merged:>=<window start>"` for the merged-PR frame, then `gh pr view N --json ...` for per-PR fields. **Confirm the available `--json` field set before scripting** — run `gh pr list --json` with no value to list valid fields rather than assuming `commits` or `files` is available on `pr list` as well as `pr view`.
- `docs/case-studies/pr-cost-context-bucket.md` — the closest structural sibling; reuse its Method/Coverage-check/Limits shape rather than inventing a layout.

**Dispatch split.** Two dispatches, sequenced, not parallel — the second consumes the first's output.

1. *Measurement* → `general-purpose`, `model: sonnet`. Writes no repository file. Runs Phases 1–3, returns per-gate pass/fail, the tables, and for every figure the exact command plus scope flags plus run date. Verification for this dispatch is the gate evaluations themselves, not a test command.
2. *Write-up* → **not delegated; runs in the session holding the measurement.** This is a deliberate departure from `plan-it`'s delegate-per-phase default, and the reason is CLAUDE.md's claim-verification rule: every quantitative claim must be re-derived from its source at the moment it is written, which a `code-writer` dispatch handed a table of numbers cannot do. The three docs files are a single coherent set (a register row, its case study, and the index entry must agree), so they do not partition into independently specifiable dispatches anyway.

## Verification

This plan changes no test-covered code, so verification has four parts and only the first is a test run.

1. **The project's own command, scoped to the diff.** From this worktree: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. A `docs/`-only diff hits `select-tests.py`'s blanket `DOCS_DIR` rule (`select-tests.py:90-97`), which selects the doc-dependent tests — `test_doc_counts.py`, `test_hook_alignment.py`, and `test_skills.py`'s state-path contract over `docs/**/*.md`. Do not widen to the full suite by hand; per this repo's CLAUDE.md, a path `select-tests.py` cannot map is a bug in its rule table, not a licence to widen.

2. **Claim re-derivation before each figure is written.** Every number reaching `docs/` is re-derived by the writing session at write time and shipped alongside the command that produced it — the same convention `pr-cost-context-bucket.md:29` follows when it prints its `gh pr list --search` string verbatim next to the coverage figure. Ledger-derived figures re-derive cheaply from a direct TSV read. Transcript-scan figures re-run with output redirected to a file, reading back only the tabulated summary lines rather than pulling the full scan into context.

3. **Gate-conformance check.** Before the register row is written, read the committed plan file's G0–G3 text and confirm the verdict matches the bar as stated, including G3's asymmetry — specifically, that no "reject" verdict is being drawn from the observational arm alone. A verdict that does not match a stated gate means either the gate or the verdict is wrong; resolve that before writing, not after.

4. **Publication-boundary check on the deliverable.** Three items the commit hook will not catch:
   - No raw `review-trace` or `subagent-mix` output reaches the docs — both print branch strings and carry a `DO NOT PUBLISH` banner under multi-root.
   - Every ledger aggregate was computed after filtering to `repo == jcdendrite/claude-config`, so no other repo's rows are folded into a published number.
   - `docs/cost-levers-considered.md` and `docs/case-studies.md` use `<config-dir>` rather than a literal `~/.claude` (row 11 — `docs/case-studies/**` is exempt, those two files are not).

   Provenance is clean by construction: every figure derives from this public repo's own branches and PRs under `--this-repo` scope, which is explicitly outside the private-corpus-provenance class this repo's CLAUDE.md defines.

Then `/code-review` before the commit, and `/plan-review` on this plan before it is presented — both hook-enforced.

## Out of scope

- Re-litigating whether Opus's current 15.7%-of-spend share is correctly
  sized for its *existing* usage points (settled, separate question).
- Acting on `skill-fidelity-reviewer`'s 6.2% zero-finding-edit-rate outlier
  (GH-762) — separate follow-up.
- Restructuring the reviewer roster, dispatch table, or fan-out rules in
  `/code-review` — a distinct, already-rejected lever ("Capping
  reviewer-ownership fan-out").
- The nested-CLAUDE.md duplicate-load fix — separate, independently
  briefed work.
- Changing any agent's or skill step's `model:` pin — `code-writer`'s authoring pass, `plan-it` Steps 2–4, or any other. Per the Context section's intended-outcome framing above, this plan ends at the recorded verdict; any pin change ships as its own separate, scoped `/plan-it` run regardless of which way the data falls.
- A forward-looking controlled pilot running comparable changes both ways. Declined, not deferred: it requires the pin flip the bullet above excludes, `absolute-token-handoff-threshold.md` already closed a structurally identical pilot as unmeasurable in this repo's available window, and the outcome variable's variance puts a sub-one-round effect out of reach at achievable n.
- Any change to `transcript-analysis.py` — a new subcommand, a new `pr-cost` ledger column, a per-branch model-mix breakout, or a redaction escape hatch on `subagent-mix`. The study runs on shipped instruments; if a gate fails for want of an instrument, that gap is recorded as a register row and a named follow-up, not built here.
- Creating `<config-dir>/.pr-cost-enabled` on any account. If the sentinel is absent, the ledger arm runs read-only on existing rows and the reduced n is a stated limit.
- Re-running `reviewer-yield`. Its corrected pull is from 2026-08-30 — recent enough that re-running it would not change this measurement's inputs.
- Extending the analysis to any repo other than `claude-config`, or to other accounts' non-`claude-config` work. Both are out of the `--this-repo` scope every command here uses, and both would put private-project data behind a published figure.
- Adding a review-round metric to `docs/cost-ledger.md`'s weekly schema. That file's rows are aggregate-only and per-ISO-week; the metric this study needs is per-PR.
- Re-litigating `plan-architect`'s own Opus pin (`design-decisions.md` §30) or `code-writer`'s deliberate `high` effort tier (§24). Both are settled decisions with their own recorded rationale; this measurement asks whether to add an Opus point, not whether the existing ones are correctly placed.
