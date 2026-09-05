# Per-PR cost forensics: dissecting a $100-class pull request

## Context

A small, single-purpose pull request in a private project's monorepo — a CI workflow addition and its plan file — consumed a $100-class amount of Claude Code spend at list price. The engineer suspects a foundational defect in this claude-config harness and/or in that project's own setup, rather than a one-off. This run dissects every non-trivial cause, fixes the instrumentation gaps that blocked parts of the dissection, and records the result durably so the next cost investigation starts from evidence rather than re-measurement. Intended outcome: a `docs/case-studies/` forensic record plus targeted `transcript-analysis.py` fixes, with pipeline-behaviour changes recommended but deferred to a separate reviewed plan.

Why now: the branch's transcripts self-delete on the default 30-day `cleanupPeriodDays` window, so the evidence for this study expires roughly four weeks from the study's start. After that the answer is unrecoverable.

## Approach

Two decisions carry this plan. First, the branch's cost is not a cache-TTL problem: only **16.1%** of the branch's spend is attributed to a named causal mechanism at all, and the dominant term is review-loop iteration count carried through context-prefix amplification, not through anything the engineer's hypothesis reaches. Second, before the case study can be written, one printed dollar figure in the tooling is wrong by roughly 1.8x — `_dispatch_usage_summary` prices without requestId dedup — so the instrument fix is a prerequisite for the record, not a parallel deliverable.

The plan therefore runs in three sequenced phases: correct one pricing defect and one mislabelled column; add the two attribution surfaces whose absence blocked the dissection; re-measure and author the forensic record. No pipeline behaviour changes.

Per the engineer's decision (row 5), the case study reports ratios and shares, not absolute dollars, for any figure with private-only provenance. The test is whether a figure is a property of the work or a property of the artifact. Session, dispatch, review-round, and turn counts measure a process — they are what the study is about, they vary with how the work was done, and verifying them requires the transcripts, so they are retained deliberately as the study's own substance. Byte sizes, file counts, PR shape, and path fragments measure a repo — they contribute nothing to the study's argument, verifying them needs only repo read access, and unlike a rotating session log they persist in git history permanently, so any figure of that kind is generalized to an order of magnitude or a qualitative form.

The retained counts are accepted as residual because the study is worthless without them, and any audience able to exploit them as a match/no-match oracle against the private project's own transcripts already possesses those transcripts. The transcript retention window is not the load-bearing control here: the case study will carry a public commit date, and the urgency that opened this plan (Context, above) puts publication within weeks of the work, so an approximate window is re-established regardless of what the text withholds.

A round-1 reviewer proposed a lighter foundation: ground the published case study in claude-config's own `pr-cost`/`workstream-cost` corpus and treat the private PR only as an unquantified motivating anecdote, which would have removed most of this plan's redaction burden. Rejected: the study's question is why *this* PR cost what it did, and a different corpus cannot answer that question. The alternative is partially absorbed rather than fully rejected — Baseline Leg 1 (below) is claude-config's own ledger, is the primary baseline leg, and is the only leg permitted to publish absolute dollars.

### Ranked causal decomposition

Three partitions of the branch's spend are available and they are **orthogonal, not additive** — do not sum across them.

| Partition | Slices | Exhaustive? |
|---|---|---|
| Token class | cache_write_5m 44.0% / cache_read 36.1% / output 20.0% / input ~0% | yes |
| Thread | main 59.6% / subagent 40.4% | yes |
| Causal mechanism | idle-gap rebuild excess 16.1% (a sub-slice of cache_write_5m) | **no — this is the whole of it** |

Sources: the token-class shares (44.0% / 36.1% / 20.0%) and the thread split (59.6% / 40.4%) both come from `transcript-analysis.py cost --branches <B>`. The idle-gap figures (16.1%, 13.6%) come from `transcript-analysis.py cache-rebuild --projects <glob> --since 30d`. The per-band split comes from a scratch script reusing `_classify_cache_rebuild_cause` and `_cache_rebuild_excess_dollars`, validated against the CLI's own combined total. The unattributed-residual figures (27.8%, 63.3%) are derived, not measured: the `cost` cache-write share minus the `cache-rebuild` idle-gap portion.

Ranked, with what each is grounded in:

1. **Review-round count and reviewer fan-out.** Floor: 40.4% — the entire subagent thread, of which 71 typed dispatches are reviewer/writer work. Very likely the majority of the 59.6% main thread as well, since 33 skill invocations (10 `/code-review`, 7 `/plan-review`, 4 `/ready-for-review`, 7 `handoff`, 5 others) each load a large body into a prefix that is then re-read every subsequent turn. **No existing instrument attributes a dollar to a review round** — that is the gap Phase 2 closes, and until it closes this rank rests on a thread bound, not a cause measurement.
2. **No incremental review credit.** Not a separate cost bucket — the mechanism that gives #1 its count. `marker.sh:285` hashes the whole staged diff, `:355-405` the whole cumulative diff; N edits force N full re-runs at full fan-out. `ready-for-review/SKILL.md:65-81`'s unnarrowed cumulative pass re-invalidates on its own fix commits.
3. **Unattributed cache-write residual: 27.8% of branch spend.** Cache-write share minus the portion classified as idle-gap. The prior is `cold-cache-attribution.md`: 40–48% of cache-write spend corpus-wide is sub-60-second prefix invalidation, ~2/3 of it with no attributable harness cause. 63.3% of this branch's cache-write sits in that regime, so the judgment is that **the residual is predominantly the already-documented, already-unexplained cold population — not a branch-specific defect.** Two sub-hypotheses are testable with existing code (see Verification).
4. **Idle-gap cache rebuild: 16.1%;** 1h-TTL-addressable slice 13.6%. The engineer's hypothesis, real and third-ranked. Unreachable on this account regardless of config: it has never once recorded a non-zero `ephemeral_1h_input_tokens`. One genuinely new finding here — 0 of 39 rebuilds had a concurrent session, **inverting** the corpus-wide 92.9%-concurrent result.
5. **Fixed per-turn context floor** — ~15,469 estimated tokens (claude-config) plus a further low-thousands estimated-token addition from the private project's several dozen skill descriptions, on each of 1,517 turns. Not separately priceable; it is the multiplicand that makes turn count expensive.

**Ruled out, each with the figure that forecloses it:** effort pins (all output on the entire branch is 20.0% of spend — halving every `xhigh` reviewer's output cannot reach the top rank); model routing (Opus 5%); compaction (zero on this branch); startup burn (2.4% corpus-wide); subagent cold-start (~1/10 of subagent cache-write); private-repo size (the branch touched none of it).

### Assumption ledger

**Root problem.** A single small PR cost a $100-class amount and no instrument in this repo can say why; the record of the answer must survive the 30-day transcript retention that will otherwise delete the evidence.

**Givens** (fixed beyond this design's reach):

- The API returns no per-source decomposition of the context prefix, so per-turn tax by source (CLAUDE.md vs. tool schemas vs. skill bodies) is not reconstructable — the vendor owns the response shape.
- The transcript JSONL carries no session-resume field — the vendor/harness owns the schema.
- The account under study never receives the 1-hour cache tier; `cost-levers-considered.md` classifies this as a plan-tier or usage-overage property to resolve with the vendor, not a config gap.

Two conditions that read like givens are not: transcript retention and the private project's own artifacts are both reachable from this repo, and are declined deliberately — see **Out of scope**.

| # | Assumption | Tag |
|---|---|---|
| 1 | Deliverable is case study + instrumentation fixes; harness behaviour changes are explicitly excluded and land as a separate reviewed plan. | `[engineer-verified]` |
| 2 | The transcript-analysis figure supersedes the PR body's own cost report — same tool, same 8 sessions, later snapshot (1,517 vs 1,349 priced turns). | `[engineer-verified]` |
| 3 | Baseline compares this branch against both the account's other branches and claude-config's own pr-cost ledger. | `[engineer-verified]` |
| 4 | Review-pipeline recommendations are in bounds to propose, out of bounds to change here. | `[engineer-verified]` |
| 5 | The case study reports ratios and shares for any figure with private-only provenance; absolute dollars only for claude-config's own corpus. | `[engineer-verified]` |
| 6 | `_dispatch_usage_summary` (`transcript-analysis.py:2709-2818`) reads its JSONL line-by-line and never calls `dedup_turns_by_request_id`, while every other pricing path does (`cost.py:120`, `cost.py:242`, and 12 further call sites). Cache classes are counted once per content block instead of once per API call, and `output_tokens` is summed across a run whose values ascend to the billed figure only on the last record (`pricing.py:225-246`). This over-counts, and is the mechanism behind the `subagent-mix`-vs-`cost` Opus contradiction. The 1.89x ratio matches the independently-observed 1.79x raw-sum inflation, though the citations establish the mechanism, not the magnitude. Phase 3's gate 5 re-derives both before the case study cites them. | `[verified: transcript-analysis.py:2761-2801, transcript_analysis/pricing.py:162-246, transcript_analysis/cost.py:120]` |
| 7 | `duration`'s "Sessions" column is `len(idle_gaps) + 1` — a count of activity bursts separated by `--gap-minutes`, not distinct session files. The 8-vs-14 disagreement is a label defect, not a data defect, and nothing downstream consumes it as a session count. | `[verified: transcript-analysis.py:773-776]` |
| 8 | The 1,517-vs-818 turn-count gap is a denominator difference, not a contradiction: `cost` counts priced turns across main **and** sidechain and skips assistant records carrying no `usage` block (`_cost_report`, `cost.py:618, 627-628`); `subagents` counts every post-dedup assistant record and splits by thread. 818 + 815 = 1,633, leaving ~116 usage-less records. The residual arithmetic is verified in code but not yet re-run against the branch. | `[verified: transcript_analysis/cost.py:404, 595, 618, 627-628 (_cost_report), 94-159 (_compute_pr_cost_branch_totals, corroborating mirror), transcript-analysis.py:871-873]` |
| 9 | `subagents` (`:10531`) and `duration` (`:10519`) both carry `--branches`. Only `cache-rebuild`, `reviewer-yield`, and `context-distribution` lack it. Every discovery figure sourced from `subagents` was therefore an unnecessary `--projects`-glob approximation and must be re-run branch-scoped before the case study cites it. | `[verified: transcript-analysis.py:10519, 10531, 10598-10625, 11042-11081]` |
| 10 | `REVIEW_TRACE_SKILLS` holds six names — `code-review`, `plan-review`, `ready-for-review`, `skill-review`, `agent-review`, `plan-it` — not three. Zero `handoff`/`pr-description` events is the subcommand's designed scope (it traces *review* events), not a detection bug. Record as a scope note; do not "fix." | `[verified: transcript-analysis.py:999-1002]` |
| 11 | The stowed harness was not constant across the study window. The case study cannot describe "the pipeline" as fixed, and must pin the commit the branch actually ran against. | `[verified: docs/design-decisions.md]` |
| 12 | Those same ref moves are the one *confirmed* cold-cache mechanism (5.5–8.4x lift on straddling turn pairs), which makes this an unusually ref-move-dense window and gives the unattributed residual a named, testable candidate — bounded small (~4% of cold tokens corpus-wide), so a bounded contributor, not the answer. | `[verified: docs/case-studies/cold-cache-attribution.md:148-181]` |
| 13 | `subagent-mix` discloses `subagent_type` only under `--this-repo` **and** only for repo-tracked names, and `--this-repo` prints branch names raw with no attestation gate. Running it with `--this-repo` from claude-config against another repo's branch would disclose that branch name — so the correct handling is to not use it cross-repo, not to widen disclosure. | `[verified: transcript-analysis.py:2320-2329, 2506, 2513-2522]` |
| 14 | All token figures in the evidence (15,469 / 15,928 …) are bytes÷4 estimates, not tokenizer counts. Every one must be labelled as an estimate in the case study. | `[verified: arithmetic against the byte counts gathered in discovery]` |
| 15 | The private project's nested instruction file, several times the size of this repo's own root instruction file, loads on a touch under a subtree the branch's own changes do not touch, so it plausibly never loaded. **Untested** — a reviewer or explorer read under that tree would make it a first-order cause. One grep settles it. | `[unverified]` |
| 16 | Main-thread cost is predominantly review-loop-driven rather than baseline session cost. Load-bearing for rank #1; the Phase 2 instrument exists to test it, and the rank must be restated if it fails. | `[unverified]` |

### Mechanisms

- **Add `dedup_turns_by_request_id` to `_dispatch_usage_summary`** — *anchors: row 6.* The function must buffer its assistant records and dedup before pricing; `pricing.py:178-184` explicitly permits this on a single dispatch's own file.

  - **Affected — pricing outputs:** `actual_dollars`/`dollars_by_class`/`counterfactual_dollars` move downward.
  - **Affected — diagnostic count:** the `total_unpriced_turns`/`total_unpriced_tokens` diagnostic moves from once per raw record to once per deduped turn.
  - **Unaffected:** `runs`/`dangling`/`declared_seen`/`requested` — none derive from `_price_turn`.

  Lighter primitives rejected: (a) a caller-side correction factor in `subagent-mix` — leaves the defect live for every other future caller of the primitive; (b) a docstring caveat only — the number stays wrong on screen. Neither is lighter than a four-line fix at the source.
- **Rename `duration`'s `Sessions` column and correct its docstring** — *anchors: row 7.* A one-word printed-label fix at the site that produced the misreading. Not a behaviour change; the computed value is unchanged.
- **`subagent-mix --per-dispatch`** — *anchors: row 1.* `_dispatch_usage_summary` is already called per dispatch at `:2468`; only the aggregation step discards the per-dispatch row. Adding a flag that prints instead of aggregating is the minimum change. Must carry the same multi-root refusal `--per-session` already has. Lighter primitives rejected: (a) a standalone subcommand duplicating the dispatch-pairing index; (b) a `--verbose` mode on the existing table — conflates two output shapes in one renderer.
- **Per-review-round cost, modifying `_review_trace_session_events` and joining through the existing dispatch index** — *anchors: row 1, row 16.* `review-trace` emits only skill *start* events, never a completion, which drives the boundary rule and the modification below:

  - **Window definition.** Round *i*'s window is `[skill_start_i, skill_start_{i+1})` — the only rule consistent with no closing event existing.
  - **Last-round handling.** The last round extends to session end (no `skill_start_{n+1}`), so trailing dispatches fall inside it under the same rule.
  - **Pre-loop-dispatch handling.** Dispatches before the first skill event fall in no round (there is no round 0) and report as an unattributed pre-review-loop total.
  - **Resolution method.** A dispatch's round is resolved from its spawning `Agent`/`Task` block's own timestamp, left-closed at each boundary.
  - **Missing dispatch id.** `reviewer-spawn` events discard `block.get("id")`, the key `_index_subagent_dispatches` needs. Add a `tool_use_id` field to close the gap.
  - **Excluded dispatch types.** `_is_reviewer_subagent_type` excludes the `code-writer`/`general-purpose` dispatches row 1 counts as reviewer/writer work. Broaden spawn detection, or add a distinct dispatch-event kind, to cover them.
  - **Shared-consumer constraint.** The function is shared with `_compute_deny_summary_data`, so the change must not alter that consumer's output.
  - **Dedup-before-pricing requirement.** Price the main-thread window with `_dedup_turns_by_request_id` then `_price_turn` — `cmd_review_trace`'s records aren't deduped today, since block-counting is dedup-insensitive but dollar-summing is not.

  Lighter/heavier primitives weighed: (a) **rejected as heavier** — `include_subagents=True` on `review-trace`'s own scope resolution, re-reading every sidechain file on every invocation at machine-wide default scope for a cost mode most runs won't use; (b) **rejected as insufficient** — main-thread-only pricing, which omits the 40.4% subagent half and would misrank the very question the instrument exists to answer.
- **A `docs/case-studies/` page plus an index row** — *anchors: root.* The lightest durable surface that survives transcript retention. Heavier alternatives rejected: a new subcommand encoding the finding (a one-off study is not a re-runnable instrument), and a hook (nothing here is an automatic-trigger request).

### Baseline: where the comparison numbers come from

Three legs, each from an instrument that already works at the required scope and none requiring a cross-repo redaction bypass.

1. **Primary — claude-config's own `pr-cost` ledger** (~145–186 rows). Already populated, local, no new capture, no redaction exposure, and the one leg permitted to publish absolute dollars. It carries `changed_files`, `plan_file_added`, `risk_surface_flag` and `commit_count`, so the comparable sub-population is directly selectable: small `changed_files`, plan file added, risk surface true (`.github/workflows/**` is a built-in risk-surface glob). Constraint the study must honour: rows under different `rate_stamp` values are compared only by re-deriving dollars from retained token counts under one rate table (`docs/pr-cost.md`, "Comparing rows across rate stamps").
2. **The account's own branch distribution — `duration`,** scoped to that account's root, is the subcommand that actually produces a per-branch ranking: `workstream-cost`'s default output has no per-branch table at all, only an aggregate `Branches: N` count, a corpus-wide mean/median sessions-per-branch line, and one aggregate startup-burn share, so no rank is derivable from it. `duration` prints a full per-branch table with the raw branch name in column 1, unconditionally — unlike `cost`, `subagents`, `subagent-mix`, `context-distribution`, and `cache-rebuild`, it carries no code-level redaction gate of any kind. **Only the rank and the ratio transcribe into the public record — never a branch name, never an absolute dollar figure** — but that control is procedural, not tool-enforced: the operator redacts the branch name before transcribing (see Verification → Manual).
3. **Class decomposition — `cost --branches <B>`** re-run for the two or three nearest-neighbour branches only, since `workstream-cost` gives no cache-write/cache-read/output split. Reported as shares.

Explicitly **not** used, and named as identification bounds in the study rather than worked around: `subagent-mix`'s agent-type table cross-repo (row 13), and any `pr-cost` ledger row for the private repo (`repo`/`host` are stored raw at rest per `docs/pr-cost.md`, an already-documented unmitigated gap).

### What this plan closes vs. records, and why the line falls there

**Cut line: fix what makes a printed number wrong or a needed attribution impossible; record what only makes a future question cheaper.**

**Closed (3):** the dedup defect (row 6 — a wrong number on screen), per-dispatch cost rows, and per-review-round cost (the two attributions whose absence actually blocked this dissection).

**Recorded, not closed** — with the reason each stayed out:

- **`pr-cost --record` on an open PR.** The nearest miss, and deliberately excluded. `merged_at` would be empty and `_parse_pr_cost_ledger_row_cells` is strict on column count, so this is a schema migration on an append-only ledger with a `supersedes` chain and a no-hand-edit rule — a separate plan, not a phase of this one. The actionable substitute: capture the row through the existing merged path once the PR merges, **before the retention deadline**, and state that deadline in the case study.
- **`--branches` on `cache-rebuild` / `reviewer-yield` / `context-distribution`** (three, not four — row 9). Three separate scan paths plus their redaction and scope tests, against a characterized ~8%-of-branch-dollars approximation error. Record the bound.
- **Denial cost, cross-session repeat reads, per-project-dir attribution.** Convenience surfaces; none blocked a conclusion here.
- **Compaction cost.** Buildable, but unvalidatable on this corpus — zero compactions on this branch.
- **Session resume** (no field exists) and **per-turn context tax by source** (vendor returns no decomposition). Both are Givens, not gaps.
- **`review-trace`'s six-skill scope** (row 10) — designed behaviour, recorded as a scope note.
- **`subagent-mix` cross-repo redaction** (row 13) — the correct handling is non-use, not a widened disclosure rule.

### Do the tooling contradictions block the case study?

**One blocks, one does not.**

- **Blocking: the dedup defect.** Any per-dispatch or per-agent-type dollar figure printed today is ~1.8x high. Fix in Phase 1, re-measure in Phase 3, then write. A case study whose stated purpose is that the next investigation trusts these numbers cannot ship citing one that is wrong.
- **Not blocking: the count disagreements.** Both are definitional (rows 7 and 8), verified in code, and consumed by nothing. They get the one-word column rename, a docstring correction, and an "instrument corrections" section in the study.

### Is the private project's own surface a cause or a red herring?

**Mostly red herring, with one real small term and one untested conditional.**

- **Red herring:** a multi-megabyte committed dependency bundle and lockfile, a low-thousands file count in the tens of megabytes, and a few dozen on-demand rule files. The branch touched none of them and repo size does not enter the prompt. `lsp-token-reduction-feasibility.md` already refuted this exact inference shape ("portfolio composition does not predict read composition; measure the transcripts, not the tree").
- **Real, small, unconditional:** the private project's several dozen skill descriptions' always-resident token cost — roughly a 17% addition to claude-config's own ~15,469-estimated-token floor, on every one of 1,517 turns. The single item unconditionally on the wire.
- **Untested conditional (row 15):** a nested instruction file several times the size of this repo's own root instruction file. One grep decides whether it is zero or a first-order cause.
- **Real but not a cost cause — do not let it drift into the cost narrative:** well over a hundred accumulated one-off `Bash(...)` entries in that repo's `.claude/settings.local.json`. Settings are not in the prompt. It is a live finding against the global CLAUDE.md permissions rule and belongs in the study labelled as a security observation (see Verification → Manual for the content constraint).
- **Useful negative:** the account already pins `"model": "sonnet"`, consistent with the observed 95% Sonnet share. No model-routing lever remains.

## Critical files

Three sequenced dispatches. Phases 1 and 2 touch the same two files, so they are sequenced rather than parallel; Phase 3 consumes their output.

Before Phase 1 begins — decoupled from Phase 1/2's own review-cycle time, which is what this study measures — an interim snapshot copies the branch's session JSONL files and the current baseline measurement outputs to a local, gitignored directory outside this repo (e.g. `~/tmp/pr-cost-forensics-evidence/`), uncommitted, so a review-round slip in Phase 1/2 cannot cost the study its evidence before the retention window closes. Create the directory `0700` and its files `0600`, matching the restrictive permissions `docs/pr-cost.md` already applies to its own ledger file for a less sensitive extract of the same class of data. The destination must not sit inside a cloud-sync folder or a bare-repo-dotfile-managed tree — see `docs/pr-cost.md`'s residual-replication-paths section for why those two are git-invisible. Delete the snapshot once Phase 3's case study lands, so it does not outlive the evidence-gathering it was created to bridge.

**Phase 1 — instrument correctness** (`code-writer`)

- `claude/.claude/scripts/transcript-analysis.py` — add `dedup_turns_by_request_id` to `_dispatch_usage_summary` (`:2709-2818`; the function currently streams line-by-line, so it must buffer assistant records first). Rename `cmd_duration`'s `Sessions` column and correct its docstring (`:744-780`).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — `_dispatch_usage_summary` regression tests: (1) a multi-record-per-requestId fixture with ascending, non-identical `output_tokens` (matching `TestPrCostDedupBeforePricing`'s 3-then-50 pattern; hand-rolled via `_asst`, since `_priced_sidechain_asst` takes no `request_id`) prices identically through `_dispatch_usage_summary` and `cost`'s path; (2) the same fixture, with zero dangling dispatches, asserts summed per-dispatch dollars **equal** a hand-computed figure rather than an inequality against `cost`'s ceiling, which per-dispatch sums undercut even pre-fix; (3) a non-contiguous run with a `tool_result` interleaved between two same-requestId assistant records; (4) an unpriced-turn-count case, since that diagnostic also shifts from once-per-raw-record to once-per-deduped-turn; (5) a `_table_cols` test for `cmd_duration`'s renamed header, since `TestDurationGapSplit` never calls `cmd_duration` itself. Reserve the inequality for the manual Phase 1 gate below, against real branch data where dangling dispatches make equality infeasible.
- **Reuse:** `transcript_analysis.pricing.dedup_turns_by_request_id`, `_price_turn`, `_token_counts` — do not reimplement.
- **Verification:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`

**Phase 2 — attribution surfaces** (`code-writer`)

- `claude/.claude/scripts/transcript-analysis.py` — `subagent-mix --per-dispatch` (print per-dispatch rows from the existing `:2468` call site; carry `--per-session`'s multi-root refusal). Per-review-round cost on `review-trace` per the Mechanisms section: modify `_review_trace_session_events` (add `tool_use_id`; broaden spawn detection to cover writer dispatches) and join through `_index_subagent_dispatches`; price each round's main-thread window with `_dedup_turns_by_request_id` before `_price_turn`.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — coverage for both flags: `--per-dispatch` with two same-`agent_type` dispatches carrying different totals, asserting two distinct rows; a per-round boundary-probe fixture (two skill events; dispatches inside round 1, inside round 2, in the gap between, before the first event, after the last) asserting which round gets each dispatch's dollars; the multi-root refusal path; and an independent dedup-invariant assertion against both `--per-dispatch` and per-round output, so a Phase 1 revert alone is still caught.
- `docs/transcript-analysis.md` — document both flags, the corrected `duration` column semantics, the `cost`-vs-`subagents` turn-count denominators (row 8), and the round-boundary rule.
- **Reuse:** `_index_subagent_dispatches` (already imported, `:135`), `_dispatch_usage_summary`, `_branch_filter`.
- **Modify:** `_review_trace_session_events` — add a `tool_use_id` field and broaden spawn detection beyond `_is_reviewer_subagent_type` (or add a distinct dispatch-event kind) per the Mechanisms section; shared with `_compute_deny_summary_data`, whose output the change must not alter.
- **Verification:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` and `.venv/bin/ruff check claude/.claude/`

**Phase 3 — re-measure and record** (main session or `code-writer`; authoring, not code)

- `docs/case-studies/review-loop-cost-forensics.md` — **new.** Pin the harness commit the branch ran against (row 11). Carry the ranked decomposition, the explicit statement that only 16.1% is causally attributed, the instrument-corrections section, the baseline comparison, the private-project-surface verdict, and a "Recommended, not implemented here" section feeding the separate pipeline plan.
- `docs/case-studies.md` — index row, newest-consistent with the existing format.
- `docs/cost-levers-considered.md` — one row for the lever this study genuinely closes: the 1-hour TTL, now with a branch-specific 13.6% figure and the 0-of-39-concurrent inversion of the corpus finding, verdict unchanged and the reason sharper (the account cannot receive the tier at all, so the figure is unreachable regardless of config).
- `docs/design-decisions.md` — one note at §37 (line 544) marking the Phase 1 fix-landing commit as a discontinuity in `subagent-mix`'s `Actual$` history, so a future run of that protocol doesn't misattribute the ~1.8x instrumentation correction to real `plan-architect` spend growth.
- **Do not touch:** `code-review/SKILL.md`, `ready-for-review/SKILL.md`, `marker.sh`, any agent `effort:`/`model:` frontmatter — row 4 puts all of these out of bounds.

## Verification

**Automated.** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` after each of Phases 1 and 2; `.venv/bin/ruff check claude/.claude/` after Phase 2. Do not widen to the full suite — `select-tests.py` widens on its own when the diff warrants it.

**Instrument correctness (Phase 1 gate).** Re-run `subagent-mix --branches <B>` after the dedup fix and confirm the per-agent-type Opus total no longer exceeds `cost --branches <B>`'s branch-wide Opus figure. That single inequality is the pass condition; it is currently violated.

**Evidence re-derivation (Phase 3 gate).** Before the case study cites any number:

1. Re-run `subagents --branches <B>` (row 9) — the discovery-phase subagent figures were `--projects`-glob approximations that did not need to be. Confirm the 1,633-vs-1,517 gap resolves to usage-less assistant records (row 8).
2. Grep the branch's 8 session files for any `Read` under the subtree the branch's own changes do not touch (row 15). If present, promote the nested instruction file to a ranked cause and restate the decomposition.
3. Test the residual (row 12) two ways: re-run `cache-rebuild` at a threshold below 100,000 to establish whether the unattributed cache-write is many small rebuilds or few large ones; and count default-branch ref moves in the study window against straddling turn pairs, using `cold-cache-attribution.md`'s own validated method, to bound the live-stow-mutation contribution. The ref-move analysis may inform the residual bound, but the case study must not publish a ref-move count paired with a study-window claim, since ref moves are events on this repo's own public git log and are independently searchable.
4. Re-run the per-round cost instrument and confirm or refute row 16. If main-thread cost is not predominantly review-loop-driven, rank #1 is restated before the study ships.
5. Re-derive every quantitative claim in the case study against the command that produces it at the moment of writing, and name that command alongside the claim.

**Manual.** Confirm no branch name, repo name, org, tracker ID, or private-provenance absolute dollar figure from the private project reaches any committed file — the plan file itself ships in the same PR and is subject to the same redaction rules.

- **Structural fingerprints.** Scan for exact counts, byte sizes, PR-shape descriptors, and file/skill counts sourced from the private project — CLAUDE.md flags this tier as reviewer-discipline-only, not hook-caught.
- **`settings.local.json` content.** The private repo's accumulated `Bash(...)` allow-entry finding ships as an aggregate characterization only — no quoted command strings, path fragments, hostnames, or account identifiers drawn from that file.
- **Baseline Leg 2's branch name.** `duration`'s per-branch table prints the raw branch name with no code-level redaction gate (see Baseline, above); any branch name read off a terminal is redacted by the operator before transcription — a procedural control with a single point of failure, not a tool-enforced one. Populating `~/.claude/private-projects.md` with the private branch name and repo identifier before the measurement runs arms `deny-private-project-refs.sh` as a mechanical backstop against that exact string reaching a committed file.

## Out of scope

- **Every pipeline behaviour change** (row 4): incremental/delta review credit in `marker.sh`, narrowing `ready-for-review`'s cumulative pass, capping reviewer fan-out in `code-review`, retuning any `effort:` or `model:` pin, and trimming `code-review/SKILL.md`'s 63,714 B body. All are in bounds to *recommend* in the case study's follow-up section and out of bounds to *implement* here.
- **`pr-cost --record` open-PR support.** A ledger schema migration on a strict-parse, append-only file with a `supersedes` chain — its own plan. Substituted by the merge-then-capture deadline named above.
- **`--branches` on `cache-rebuild`, `reviewer-yield`, `context-distribution`** — three scan paths and their redaction tests, against a characterized ~8% approximation error.
- **Denial cost, compaction cost, cross-session repeat reads, per-project-dir dollar attribution** — recorded as named gaps with their data-availability status, not built.
- **Raising `cleanupPeriodDays` in `claude/.claude/settings.json` to widen the transcript-retention window.** Reachable from this repo — the key is unset today, so retention sits at the default named in Context. Declined here on blast radius: the setting is stow-shared, so it would raise every consumer's transcript disk usage, and it does nothing for this study, whose transcripts already exist and whose write completes well inside the window. Recommended as a follow-up decision in the case study, not taken here.
- **Any change to the private project's own repo, settings, skills, or instruction files.** Reachable — third-party ownership alone would not put it out of bounds — but declined: this study needs to read that surface, not change it, and a cost investigation is not a mandate to edit another party's configuration. The well-over-a-hundred `Bash(...)` allow entries are reported to the engineer as a security observation, not remediated.
- **Widening `review-trace`'s `REVIEW_TRACE_SKILLS`** to cover `handoff`/`pr-description`/`git-feature-branch-sync`/`tighten-prose` (row 10). Designed scope; changing it would alter every existing consumer of that timeline for no benefit to this study.
