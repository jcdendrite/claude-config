# Per-PR cost forensics: dissecting a disproportionately expensive pull request

## Context

A small, single-purpose pull request in a private project consumed a disproportionate amount of Claude Code spend at list price. The engineer suspects a foundational defect in this claude-config harness and/or in that project's own setup, rather than a one-off. This run dissects every non-trivial cause, fixes the instrumentation gaps that blocked parts of the dissection, and records the result durably so the next cost investigation starts from evidence rather than re-measurement. Intended outcome: a `docs/case-studies/` forensic record plus targeted `transcript-analysis.py` fixes, with pipeline-behaviour changes recommended but deferred to a separate reviewed plan.

Why now: the branch's transcripts self-delete on the default 30-day `cleanupPeriodDays` window, so the evidence for this study expires roughly four weeks from the study's start. After that the answer is unrecoverable.

## Approach

Two decisions carry this plan. First, the branch's cost is not a cache-TTL problem: most of the branch's spend is not attributed to any named causal mechanism at all, and the dominant attributed term is review-loop iteration count carried through context-prefix amplification, not through anything the engineer's hypothesis reaches. Second, before the case study can be written, one printed dollar figure in the tooling is materially wrong — `_dispatch_usage_summary` prices without requestId dedup — so the instrument fix is a prerequisite for the record, not a parallel deliverable.

The plan therefore runs in three sequenced phases: correct one pricing defect and one mislabelled column; add the two attribution surfaces whose absence blocked the dissection; re-measure and author the forensic record. No pipeline behaviour changes.

**No branch-specific figures:** The published case study follows CLAUDE.md's "Also redact structural fingerprints and provenance" section and `docs/private-project-redaction.md` directly: even a ratio- or share-form figure computed from one private branch's own transcripts still carries a per-project, per-account, or per-engagement dimension, which that rule bars absolutely. It publishes no ratio, share, or count tied to the one private branch at all — only qualitative findings, structural facts, and figures pooled or sourced from this repo's own public corpus, backed by "Why this generalizes" and Baseline Leg 1 in the case study.

The published case study grounds itself in claude-config's own `pr-cost`/`workstream-cost` corpus — Baseline Leg 1 and the "Why this generalizes" section, both below — with the private branch's own findings carried forward as an unquantified, qualitative account rather than as a source of published figures. The branch's own transcripts remain the evidence base used to answer why *this* PR cost what it did; the published record does not need to carry that branch's own numbers to state the answer qualitatively.

### Ranked causal decomposition

Three partitions of the branch's spend are available and they are **orthogonal, not additive** — do not sum across them:

- Token class: cache-write, cache-read, output, input share.
- Thread: main vs. subagent.
- Causal mechanism: idle-gap cache rebuild, a sub-slice of cache-write — the only mechanism this plan could isolate and name.

Sources: the token-class shares and the thread split both come from `transcript-analysis.py cost --branches <B>`. The idle-gap figures come from `transcript-analysis.py cache-rebuild --projects <glob> --since 30d`. The per-band split comes from a scratch script reusing `_classify_cache_rebuild_cause` and `_cache_rebuild_excess_dollars`, validated against the CLI's own combined total. The unattributed-residual is derived, not measured: the `cost` cache-write share minus the `cache-rebuild` idle-gap portion.

No figure in this section publishes as an absolute count, percentage, or ratio tied to the one private branch — see the "No branch-specific figures" note under Approach, above, and CLAUDE.md's "Also redact structural fingerprints and provenance" section. Every number this study computed against the branch's own transcripts was used to reach the qualitative rank below, not published alongside it.

Ranked, with what each is grounded in:

1. **Review-round count and reviewer fan-out.** Floor: the entire subagent thread, a majority of which is reviewer/writer dispatch work. Very likely a further share of the main thread too, since every skill invocation (`/code-review`, `/plan-review`, `/ready-for-review`, and others) loads a large body into a prefix that is then re-read every subsequent turn. `review-round-cost` already attributes a dollar to a review round per branch: a real but minority share falls in-round, most falls outside it. Rank #1 rests on the subagent-thread floor, not on this share (see the case study for the corpus-level version of this same finding).
2. **No incremental review credit.** Not a separate cost bucket — the mechanism that gives #1 its count. `marker.sh`'s `_hash_staged_diff` hashes the whole staged diff. `_lib_cumulative_diff_hash` (in `claude/.claude/hooks/_lib.sh` — note the full path, not a bare `_lib.sh`, since no file by that bare name exists under `claude/.claude/scripts/`) hashes the whole cumulative diff. N edits therefore force N full re-runs at full fan-out. The unnarrowed cumulative pass documented at `ready-for-review/SKILL.md` § "3. Code review (halt on findings)" re-invalidates on its own fix commits.
3. **Unattributed cache-write residual.** Cache-write share minus the portion classified as idle-gap. The prior is `cold-cache-attribution.md`: a substantial share of cache-write spend corpus-wide is sub-60-second prefix invalidation, most of it with no attributable harness cause. This branch's own cache-write residual sits predominantly in that already-documented, already-unexplained regime, so the judgment is that **the residual is predominantly the already-documented, already-unexplained cold population — not a branch-specific defect.** Two sub-hypotheses are testable with existing code (see Verification).
4. **Idle-gap cache rebuild.** The engineer's hypothesis, real and third-ranked. This account has never once recorded a non-zero `ephemeral_1h_input_tokens` — the one-hour tier is selectable by config (`promptCacheTtl`/`ENABLE_PROMPT_CACHING_1H`) but not selected here. One genuinely new finding here: none of the branch's idle-gap rebuilds had a concurrent session active, **inverting** the corpus-wide concurrent-session-driven result (`cost-levers-considered.md`, "Context cost root cause").
5. **Fixed per-turn context floor** — this repo's own always-loaded instruction and skill surface, plus a further addition from a project's own skill descriptions on every turn. Not separately priceable; it is the multiplicand that makes turn count expensive.

**Ruled out, each with the reasoning that forecloses it:** effort pins (output is a small share of total branch spend — halving every `xhigh` reviewer's output cannot reach the top rank); model routing (Opus is a small minority of spend); compaction (zero on this branch); startup burn (a small corpus-wide share); subagent cold-start (a small fraction of subagent cache-write); private-repo size (the branch touched none of it).

### Assumption ledger

**Root problem.** A single small PR cost a disproportionate amount and no instrument in this repo can say why; the record of the answer must survive the 30-day transcript retention that will otherwise delete the evidence.

**Givens** (fixed beyond this design's reach):

- The API returns no per-source decomposition of the context prefix, so per-turn tax by source (CLAUDE.md vs. tool schemas vs. skill bodies) is not reconstructable — the vendor owns the response shape.
- The transcript JSONL carries no session-resume field — the vendor/harness owns the schema.

Three conditions that read like givens are not: transcript retention, the private project's own artifacts, and the one-hour cache tier on the account this branch ran on are all reachable, and are declined deliberately — see **Out of scope**.

| # | Assumption | Tag |
|---|---|---|
| 1 | Deliverable is case study + instrumentation fixes; harness behaviour changes are explicitly excluded and land as a separate reviewed plan. | `[engineer-verified]` |
| 2 | The transcript-analysis figure supersedes the PR body's own cost report — same tool, same 8 sessions, a later, higher-priced-turn-count snapshot. | `[engineer-verified]` |
| 3 | Baseline compares this branch against claude-config's own pr-cost ledger and this repo's own single-account review-round history. | `[engineer-verified]` |
| 4 | Review-pipeline recommendations are in bounds to propose, out of bounds to change here. | `[engineer-verified]` |
| 5 | The case study publishes no figure tied to the one private branch, in any form — only qualitative findings, structural facts, and figures pooled or sourced from this repo's own public corpus. | `[engineer-verified]` |
| 6 | `_dispatch_usage_summary` (`transcript-analysis.py`, function definition — cite by name, not line: this file's line numbers drift as it grows) has three defects:<br>- a dedup gap: never calls `dedup_turns_by_request_id`, while every other pricing path does (`_compute_pr_cost_branch_totals`, `_compute_workstream_dollars`, and further call sites).<br>- a cache-class double-count: cache classes are counted once per content block instead of once per API call.<br>- an output-token oversum: `output_tokens` is summed across a run whose values ascend to the billed figure only on the last record (`pricing.py`, `_merge_assistant_run` docstring).<br>This over-counting is the mechanism behind the `subagent-mix`-vs-`cost` Opus contradiction. `docs/design-decisions/plan-architect-consult-mode.md` records the resulting overstatement's correction qualitatively, not quantified. | `[verified: transcript-analysis.py, _dispatch_usage_summary; transcript_analysis/pricing.py, dedup_turns_by_request_id and _merge_assistant_run; transcript_analysis/cost.py, _compute_pr_cost_branch_totals]` |
| 7 | `duration`'s "Sessions" column is `len(idle_gaps) + 1` — a count of activity bursts separated by `--gap-minutes`, not distinct session files. This disagreed with the branch's own real session-file count, a label defect, not a data defect, and nothing downstream consumes it as a session count. | `[verified: transcript-analysis.py, cmd_duration (burst_count = len(idle_gaps) + 1)]` |
| 8 | The two instruments' turn-count gap is a denominator difference, not a contradiction. `cost` counts priced turns across main **and** sidechain, skipping assistant records with no `usage` block (`_cost_report`). `subagents` counts every post-dedup assistant record and splits by thread, so the residual is usage-less records `cost` correctly excludes. The residual arithmetic is verified in code but not yet re-run against the branch. | `[verified: transcript_analysis/cost.py, _cost_report (total_sidechain_turns tally, usage-block skip); _compute_pr_cost_branch_totals (corroborating mirror); transcript-analysis.py, cmd_subagents]` |
| 9 | `subagents` and `duration` both carry `--branches` (their `add_argument("--branches", ...)` registrations — cite by subcommand name, not line: this file's line numbers drift as it grows). Only `cache-rebuild`, `reviewer-yield`, and `context-distribution` lack it. Every discovery figure sourced from `subagents` was therefore an unnecessary `--projects`-glob approximation and must be re-run branch-scoped before the case study cites it. | `[verified: transcript-analysis.py, p_sub/p_duration --branches registrations]` |
| 10 | `REVIEW_TRACE_SKILLS` holds six names — `code-review`, `plan-review`, `ready-for-review`, `skill-review`, `agent-review`, `plan-it` — not three. Zero `handoff`/`pr-description` events is the subcommand's designed scope (it traces *review* events), not a detection bug. Record as a scope note; do not "fix." | `[verified: transcript-analysis.py, REVIEW_TRACE_SKILLS]` |
| 11 | The stowed harness was not constant across the study window. The case study cannot describe "the pipeline" as fixed, and must pin the commit the branch actually ran against. | `[verified: docs/design-decisions.md]` |
| 12 | Those same ref moves are the one *confirmed* cold-cache mechanism (5.5–8.4x lift on straddling turn pairs), which makes this an unusually ref-move-dense window and gives the unattributed residual a named, testable candidate — bounded small (~4% of cold tokens corpus-wide), so a bounded contributor, not the answer. | `[verified: docs/case-studies/cold-cache-attribution.md:148-181]` |
| 13 | `subagent-mix` discloses `subagent_type` only under `--this-repo` **and** only for repo-tracked names, and `--this-repo` prints branch names raw with no attestation gate. Running it with `--this-repo` from claude-config against another repo's branch would disclose that branch name — so the correct handling is to not use it cross-repo, not to widen disclosure. | `[verified: transcript-analysis.py, cmd_subagent_mix (docstring)]` |
| 14 | All token figures in the evidence are bytes÷4 estimates, not tokenizer counts. Every one must be labelled as an estimate in the case study. | `[verified: arithmetic against the byte counts gathered in discovery]` |
| 15 | The private project's nested instruction file loads on a touch under a subtree the branch's own changes do not touch, so it plausibly never loaded. **Untested** — a reviewer or explorer read under that tree would make it a first-order cause. One grep settles it. | `[unverified]` |
| 16 | Main-thread cost is predominantly review-loop-driven rather than baseline session cost. Load-bearing for rank #1; the Phase 2 instrument exists to test it, and the rank must be restated if it fails. Tested: `review-round-cost` finds a real but minority in-round share. That refutes "predominantly in-window," not "predominantly review-loop-driven" — out-of-window spend includes review-caused work the instrument cannot separate. Rank #1 restated to "largest attributed mechanism" on the subagent-thread floor, not overturned. | `[verified: docs/case-studies/review-loop-cost-forensics.md, Gate result 4]` |

### Mechanisms

- **Add `dedup_turns_by_request_id` to `_dispatch_usage_summary`** — *anchors: row 6.* The function must buffer its assistant records and dedup before pricing; `pricing.py`'s `dedup_turns_by_request_id` docstring explicitly permits this on a single dispatch's own file (concatenating one session's main transcript with its own subagent transcripts is safe; mixing in another session's records is not).

  - **Affected — pricing outputs:** `actual_dollars`/`dollars_by_class`/`counterfactual_dollars` move downward.
  - **Affected — diagnostic count:** the `total_unpriced_turns`/`total_unpriced_tokens` diagnostic moves from once per raw record to once per deduped turn.
  - **Unaffected:** `runs`/`dangling`/`declared_seen`/`requested` — none derive from `_price_turn`.

  Lighter primitives rejected: (a) a caller-side correction factor in `subagent-mix` — leaves the defect live for every other future caller of the primitive; (b) a docstring caveat only — the number stays wrong on screen. Neither is lighter than a four-line fix at the source.
- **Rename `duration`'s `Sessions` column and correct its docstring** — *anchors: row 7.* A one-word printed-label fix at the site that produced the misreading. Not a behaviour change; the computed value is unchanged.
- **Per-review-round cost: lighter primitive reused, not built** — *anchors: row 1, row 16.* `review-round-cost` already existed and was reused instead of building a new instrument. Its boundary rule — close at the next round-open or the next fresh user prompt — approximates a review-*pass* cost claim that excludes implementation work done between passes, though same-window fix-application turns still land inside the window it draws.
- **A `docs/case-studies/` page plus an index row** — *anchors: root.* The lightest durable surface that survives transcript retention. Heavier alternatives rejected: a new subcommand encoding the finding (a one-off study is not a re-runnable instrument), and a hook (nothing here is an automatic-trigger request).

### Baseline: where the comparison numbers come from

Three legs, each from an instrument that already works at the required scope and none requiring a cross-repo redaction bypass.

1. **Primary — claude-config's own `pr-cost` ledger.** Already populated, local, no new capture, no redaction exposure, and the one leg permitted to publish absolute dollars, since it is this repo's own public corpus rather than a private one. Use the full population of this repo's own recorded rows, not a shape-filtered subset matching this specific PR: once the leg is anchored in this repo's own full corpus rather than a narrow comparable-population framing, it no longer needs to match this branch's own shape, and a shape-based filter (a changed-file-count bound, a workflow-risk-surface flag) would itself re-disclose PR shape. Constraint the study must honour: rows under different `rate_stamp` values are compared only by re-deriving dollars from retained token counts under one rate table (`docs/pr-cost.md`, "Comparing rows across rate stamps").
2. **The account's own branch distribution — deferred.** `duration`, scoped to the account this branch ran on, is the subcommand that would produce a per-branch ranking: `workstream-cost`'s default output has no per-branch table at all, only an aggregate `Branches: N` count, a corpus-wide mean/median sessions-per-branch line, and one aggregate startup-burn share, so no rank is derivable from it. `duration` prints a full per-branch table with the raw branch name in column 1, unconditionally — unlike `cost`, `subagents`, `subagent-mix`, `context-distribution`, and `cache-rebuild`, it carries no code-level redaction gate of any kind. This leg was explored during the study but is not published: a branch-level rank or ratio still carries a per-account dimension CLAUDE.md's redaction rule bars absolutely (see the "No branch-specific figures" note under Approach, above). Deferred to future cross-account pooled-aggregate tooling.
3. **Class decomposition — deferred.** `cost --branches <B>` re-run for the account's own nearest-neighbour branches was explored the same way, for the same reason: a branch-level token-class comparison still carries a per-account dimension. Deferred alongside leg 2.

A fourth leg — this repo's own single-account review-round history, backing "Why this generalizes" — was added later, during Phase 3's case-study authoring, not designed here alongside the three legs above; adding it there is in bounds since Phase 3 is authoring, not code. Its own methodology is documented in the case study's "Why this generalizes" and "Sources" sections, not re-elaborated here.

Explicitly **not** used, and named as identification bounds in the study rather than worked around: `subagent-mix`'s agent-type table cross-repo (row 13), and any `pr-cost` ledger row for the private repo (`repo`/`host` are stored raw at rest per `docs/pr-cost.md`, an already-documented unmitigated gap).

### What this plan closes vs. records, and why the line falls there

**Cut line: fix what makes a printed number wrong or a needed attribution impossible; record what only makes a future question cheaper.**

**Closed (2):** the dedup defect (row 6 — a wrong number on screen) and per-review-round cost (the two attributions whose absence actually blocked this dissection).

**Recorded, not closed** — with the reason each stayed out:

- **`pr-cost --record` on an open PR.** The nearest miss, and deliberately excluded. `merged_at` would be empty and `_parse_pr_cost_ledger_row_cells` is strict on column count, so this is a schema migration on an append-only ledger with a `supersedes` chain and a no-hand-edit rule — a separate plan, not a phase of this one. The actionable substitute: capture the row through the existing merged path once the PR merges, **before the retention deadline**, and state that deadline in the case study.
- **`--branches` on `cache-rebuild` / `reviewer-yield` / `context-distribution`** (three, not four — row 9). Three separate scan paths plus their redaction and scope tests, against a characterized small approximation error. Record the bound.
- **Denial cost, cross-session repeat reads, per-project-dir attribution.** Convenience surfaces; none blocked a conclusion here.
- **Compaction cost.** Buildable, but unvalidatable on this corpus — zero compactions on this branch.
- **Session resume** (no field exists) and **per-turn context tax by source** (vendor returns no decomposition). Both are Givens, not gaps.
- **`review-trace`'s six-skill scope** (row 10) — designed behaviour, recorded as a scope note.
- **`subagent-mix` cross-repo redaction** (row 13) — the correct handling is non-use, not a widened disclosure rule.

### Do the tooling contradictions block the case study?

**One blocks, one does not.**

- **Blocking: the dedup defect.** Any per-dispatch or per-agent-type dollar figure printed today is materially high. Fix in Phase 1, re-measure in Phase 3, then write. A case study whose stated purpose is that the next investigation trusts these numbers cannot ship citing one that is wrong.
- **Not blocking: the count disagreements.** Both are definitional (rows 7 and 8), verified in code, and consumed by nothing. They get the one-word column rename, a docstring correction, and an "instrument corrections" section in the study.

### Is the private project's own surface a cause or a red herring?

**Mostly red herring, with one real small term and one untested conditional.**

- **Red herring:** the private project's own bulk repo surface — its dependency footprint, file count, and on-demand rule files. The branch touched none of them and repo size does not enter the prompt. `lsp-token-reduction-feasibility.md` already refuted this exact inference shape on this repo's own transcripts ("portfolio composition does not predict read composition; measure the transcripts, not the tree").
- **Real, small, unconditional:** a project's own skill descriptions carry an always-resident token cost, adding to claude-config's own always-loaded floor on every turn — true generically, and verifiable from this repo alone. The single item unconditionally on the wire.
- **Untested conditional (row 15):** a nested instruction file under a subtree the branch's own changes do not touch. One grep decides whether it is zero or a first-order cause.
- **Useful negative:** the account this branch ran on already pins `"model": "sonnet"`. No model-routing lever remains.

## Critical files

Three sequenced dispatches. Phases 1 and 2 touch the same two files, so they are sequenced rather than parallel; Phase 3 consumes their output.

Before Phase 1 begins — decoupled from Phase 1/2's own review-cycle time, which is what this study measures — an interim snapshot copies the branch's session JSONL files and the current baseline measurement outputs to a local, gitignored directory outside this repo (e.g. `~/tmp/pr-cost-forensics-evidence/`), uncommitted, so a review-round slip in Phase 1/2 cannot cost the study its evidence before the retention window closes. Create the directory `0700` and its files `0600`, matching the restrictive permissions `docs/pr-cost.md` already applies to its own ledger file for a less sensitive extract of the same class of data. The destination must not sit inside a cloud-sync folder or a bare-repo-dotfile-managed tree — see `docs/pr-cost.md`'s residual-replication-paths section for why those two are git-invisible. Delete the snapshot once Phase 3's case study lands, so it does not outlive the evidence-gathering it was created to bridge.

**Phase 1 — instrument correctness** (`code-writer`)

- `claude/.claude/scripts/transcript-analysis.py` — add `dedup_turns_by_request_id` to `_dispatch_usage_summary` (function name, not line range — this file's line numbers drift as it grows; the function currently streams line-by-line, so it must buffer assistant records first). `cmd_duration`'s `Sessions`-column rename and docstring correction are already shipped (fixed; `cmd_duration`, function name — this file's line numbers drift as it grows).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — `_dispatch_usage_summary` regression tests: (1) a multi-record-per-requestId fixture with ascending, non-identical `output_tokens` (matching `TestPrCostDedupBeforePricing`'s 3-then-50 pattern; hand-rolled via `_asst`, since `_priced_sidechain_asst` takes no `request_id`) prices identically through `_dispatch_usage_summary` and `cost`'s path; (2) the same fixture, with zero dangling dispatches, asserts summed per-dispatch dollars **equal** a hand-computed figure rather than an inequality against `cost`'s ceiling, which per-dispatch sums undercut even pre-fix; (3) a non-contiguous run with a `tool_result` interleaved between two same-requestId assistant records; (4) an unpriced-turn-count case, since that diagnostic also shifts from once-per-raw-record to once-per-deduped-turn; (5) a `_table_cols` test for `cmd_duration`'s renamed header, since `TestDurationGapSplit` never calls `cmd_duration` itself. Reserve the inequality for the manual Phase 1 gate below, against real branch data where dangling dispatches make equality infeasible.
- **Reuse:** `transcript_analysis.pricing.dedup_turns_by_request_id`, `_price_turn`, `_token_counts` — do not reimplement.
- **Verification:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`

**Phase 2 — attribution surfaces** (`code-writer`)

- `docs/transcript-analysis.md` — document the corrected `duration` column semantics, and the `cost`-vs-`subagents` turn-count denominators (row 8).
- **Reuse:** `_index_subagent_dispatches` (already imported), `_dispatch_usage_summary`, `_branch_filter`.
- **Verification:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` and `.venv/bin/ruff check claude/.claude/`

**Phase 3 — re-measure and record** (main session or `code-writer`; authoring, not code)

- `docs/case-studies/review-loop-cost-forensics.md` — **new.** Pin the harness commit the branch ran against (row 11). Carry the ranked decomposition, the explicit statement that most of the branch's spend is not causally attributed, the instrument-corrections section, the baseline comparison (including a "Why this generalizes" section grounding the finding in this repo's own corpus), the private-project-surface verdict, and a "Recommended, not implemented here" section feeding the separate pipeline plan.
- `docs/case-studies.md` — index row, newest-consistent with the existing format.
- `docs/cost-levers-considered.md` — two rows: the 1-hour TTL lever this study re-examines (not pursued: the account has never recorded a one-hour cache write — selectable by config via `promptCacheTtl`/`ENABLE_PROMPT_CACHING_1H`, not structurally blocked — and idle-gap rebuilds rank below review-loop dispatch volume on this branch), and the idle-gap-rebuild-cause inversion this branch's own zero-concurrent finding produces against the corpus-wide pattern. Both stated qualitatively, with no branch-specific count or percentage.
- `docs/design-decisions/plan-architect-consult-mode.md` — one note marking the Phase 1 fix-landing commit as a discontinuity in `subagent-mix`'s `Actual$` history, stated qualitatively (no dollar figure or ratio ships, since `subagent-mix` has no this-repo-only, single-account instrument), so a future run of that protocol doesn't misattribute the instrumentation correction to real `plan-architect` spend growth.
- **Do not touch:** `code-review/SKILL.md`, `ready-for-review/SKILL.md`, `marker.sh`, any agent `effort:`/`model:` frontmatter — row 4 puts all of these out of bounds.

## Verification

**Automated.** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` after each of Phases 1 and 2; `.venv/bin/ruff check claude/.claude/` after Phase 2. Do not widen to the full suite — `select-tests.py` widens on its own when the diff warrants it.

**Instrument correctness (Phase 1 gate).** Re-run `subagent-mix --branches <B>` after the dedup fix and confirm the per-agent-type Opus total no longer exceeds `cost --branches <B>`'s branch-wide Opus figure. That single inequality is the pass condition; it is currently violated.

**Evidence re-derivation (Phase 3 gate).** Before the case study cites any number:

1. Re-run `subagents --branches <B>` (row 9) — the discovery-phase subagent figures were `--projects`-glob approximations that did not need to be. Confirm the gap between the two instruments' turn counts resolves to usage-less assistant records (row 8).
2. Grep the branch's session files for any `Read` under the subtree the branch's own changes do not touch (row 15). If present, promote the nested instruction file to a ranked cause and restate the decomposition.
3. Test the residual (row 12) two ways: re-run `cache-rebuild` at a threshold below 100,000 to establish whether the unattributed cache-write is many small rebuilds or few large ones; and count default-branch ref moves in the study window against straddling turn pairs, using `cold-cache-attribution.md`'s own validated method, to bound the live-stow-mutation contribution. The ref-move analysis may inform the residual bound, but the case study must not publish a ref-move count as a per-branch figure — not because it would date the study window (this repo's harness-pin commit already fixes that publicly, per row 11), but for the same reason every other per-engagement count is omitted from the published record.
4. Re-run `review-round-cost --branches <B>` and confirm or refute row 16. If main-thread cost is not predominantly review-loop-driven, rank #1 is restated before the study ships.
5. Re-derive every quantitative claim in the case study against the command that produces it at the moment of writing, and name that command alongside the claim.
6. Sync this branch with `origin/main` before citing `cost-levers-considered.md`'s "Cold prompt-cache measurement and root cause" row: this branch's own copy of that row predates `origin/main`'s follow-ups documenting `promptCacheTtl`/`ENABLE_PROMPT_CACHING_1H` as the config lever, so every cross-reference this study adds to that row is accurate only after the sync lands.

**Manual.** Confirm no branch name, repo name, org, tracker ID, or private-provenance figure — absolute dollar, share, ratio, or count — from the private project reaches any committed file — the plan file itself ships in the same PR and is subject to the same redaction rules.

- **Structural fingerprints.** Scan for exact counts, byte sizes, PR-shape descriptors, and file/skill counts sourced from the private project — CLAUDE.md flags this tier as reviewer-discipline-only, not hook-caught.
- **`settings.local.json` content.** No content, count, or characterization drawn from the private repo's `settings.local.json` reaches any committed file. That repo's own accumulated `Bash(...)` allow-entry finding is reported to the engineer separately, out of band — it does not ship in this study in any form.
- **Baseline Leg 2 exploration's branch name.** `duration`'s per-branch table prints every branch that account has ever worked, not just the target branch, with no code-level redaction gate (see Baseline, above). Leg 2 itself is not published (see Baseline, above), but the exploration still read that full raw table during measurement, exposing every branch name in it. Populating `~/.claude/private-projects.md` with the private branch name and repo identifier before the measurement ran armed `deny-private-project-refs.sh` as a mechanical backstop for that specific string; the same population must cover every other branch name the full table displayed before any of them can safely reach a committed file.
- **Ratio or magnitude figures with private-branch-only provenance.** Any per-dispatch or per-agent-type dollar ratio whose only known derivation is diffing pricing against the private branch's own transcripts does not ship in any committed file, even rounded. `subagent-mix` has no this-repo-only, single-account instrument (see `docs/private-project-redaction.md` § "This repository, one account"), so `docs/design-decisions/plan-architect-consult-mode.md`'s note on the pricing-fix discontinuity stays qualitative — no dollar figure or ratio ships from it either.
- **Evidence-snapshot directory.** `~/tmp/pr-cost-forensics-evidence/` (Critical files, above) is confirmed deleted, and no committed file cites its path or contents.

## Out of scope

- **Every pipeline behaviour change** (row 4): incremental/delta review credit in `marker.sh`, narrowing `ready-for-review`'s cumulative pass, capping reviewer fan-out in `code-review`, retuning any `effort:` or `model:` pin, and trimming `code-review/SKILL.md`'s large body. All are in bounds to *recommend* in the case study's follow-up section and out of bounds to *implement* here.
- **`pr-cost --record` open-PR support.** A ledger schema migration on a strict-parse, append-only file with a `supersedes` chain — its own plan. Substituted by the merge-then-capture deadline named above.
- **`--branches` on `cache-rebuild`, `reviewer-yield`, `context-distribution`** — three scan paths and their redaction tests, against a characterized small approximation error.
- **Denial cost, compaction cost, cross-session repeat reads, per-project-dir dollar attribution** — recorded as named gaps with their data-availability status, not built.
- **Raising `cleanupPeriodDays` in `claude/.claude/settings.json` to widen the transcript-retention window.** Reachable from this repo — the key is unset today, so retention sits at the default named in Context. Declined here on blast radius: the setting is stow-shared, so it would raise every consumer's transcript disk usage, and it does nothing for this study, whose transcripts already exist and whose write completes well inside the window. Recommended as a follow-up decision in the case study, not taken here.
- **Any change to the private project's own repo, settings, skills, or instruction files.** Reachable — third-party ownership alone would not put it out of bounds — but declined: this study needs to read that surface, not change it, and a cost investigation is not a mandate to edit another party's configuration.
- **Widening `review-trace`'s `REVIEW_TRACE_SKILLS`** to cover `handoff`/`pr-description`/`git-feature-branch-sync`/`tighten-prose` (row 10). Designed scope; changing it would alter every existing consumer of that timeline for no benefit to this study.
- **Selecting the one-hour cache tier for the account this branch ran on.** It's reachable by config: the account has never recorded a one-hour write, which is the account-level tier difference `cost-levers-considered.md` records, not a structural block. It's declined because it falls outside this plan's deliverable (row 1), and idle-gap rebuilds rank below review-loop cost.
