# Plan: pr-cost ledger schema migration (prerequisite 1 of #1239)

## Context

Goal: let the pr-cost ledger record per-model, per-class token and dollar data going forward, and read every existing row unchanged in meaning, so a later combining subcommand can re-price mixed-model rows under a common rate table.

Ask: "Migration only I think" — the engineer's answer when asked whether to plan the schema migration alone or all of #1239, after "Plan-it on 1239?".

Why now: it is prerequisite 1 of GitHub issue #1239 and blocks the durable combining subcommand. Per-model data can be captured only while transcripts survive, so every `--record` before this lands loses the dimension for good. The change alters `--record`'s write contract, so the user surface and threat model are every stow consumer, not the session owner alone.

Design decisions D1-D5 were put to the engineer, who answered "Ask staff data engineer and the architect" (D1), "Same response as for prior question" (D2, D3) and "Ask the architect" (D4, D5). Round 1 of `/plan-review` then returned Request changes. The engineer answered the resulting four decisions with "Your recommendations make sense to me" (rows 35-38) and the write-time failure policy with "Ask the architect" (row 50). The consultants' recommendations on D1-D4 are not engineer decisions beyond those quotes. Round 2 of `/plan-review` returned Approve with concerns from all five reviewers. The engineer then chose "(c) Every write, floats at 6 decimals (Recommended)" for the value postcondition's scope (row 36). On a proposed carve-out that would skip a forced re-capture whose breakdown fails its check, the engineer said "Skip that PR? That doesn’t seem right. Can you have fable check?". Fable's read-only check recommended degrading every time. Round 3 of `/plan-review` returned Approve with concerns from all five reviewers. The engineer then answered four questions: on D1-D3, "Confirm D1, D2, D3 (Recommended)"; on the exit code, "Degrade always, exit 1 (Recommended)"; on the downgrade command, "Ask fable"; on the remaining round-3 concerns, "Ask the architect". "Ask fable" and "Ask the architect" are delegations, not choices. Fable's read-only check recommended dropping the downgrade command, and the engineer then selected "Accept the drop (Recommended)". Decisions records the outcomes.

## Approach

**Design.** Append one column, `model_breakdown`, to the end of the pr-cost ledger. It holds a canonical-JSON cell with tokens and integer micro-dollars for each priced model, pricing variant, and token class. Rows written before the column existed parse under a frozen pre-model header and read the cell as empty, meaning "not recorded". The next consented `--record` rewrites the file under the current header, the same way the shipped `host`-column precedent works. Every write refuses to publish unless every prior row survives value-for-value, with floats compared at the ledger's six decimals. The parser checks only the cell's structure. Every cross-column invariant runs in the producer before the write, and a cell that fails that check is recorded empty instead of costing the scalar row. No migrator runs, and reads never write.

**Scope check.** "Migration only" is the right cut. Each inclusion below is forced by the schema, the threat, or an engineer decision, not invited:
- **The pricing-variant level (M4).** This plan fixes the cell's shape. Adding fast-mode and US-geo later would be a second migration, and rows captured in between would lose that dimension once their transcripts age out (G1).
- **The producer-side check and the write-time failure policy (M5, M6).** Decision 1 moves the sum check to the producer. Without a stated policy, a breakdown defect would abort the run and cost every remaining branch its scalar row (row 40).
- **The value-level write postcondition (M6).** Decision 2 and the engineer's every-write, six-decimal choice (row 36).
- **Export redaction triage (M7).** The export carries every ledger column, and its triage test fails until the new column is classified (rows 7-8).
- **Docs, the CHANGELOG entry, and the upgrade notice (M6, M8).** The upgrade has no supported downgrade and locks older checkouts out. These three channels are the only way an operator learns why and what to do (row 33).

This is not dead schema. Its value is recording the data while transcripts still exist, and that happens at `--record` time. A combiner landing months later reads every row recorded from this merge forward, using only the documented cell semantics, so no slice of that subcommand belongs here.

**Who this reaches.**
- **Consumers with `pr_cost_recording` on.**
  - After `git pull`, their next `--record` that writes a row rewrites the ledger under the current header and prints one stderr notice (M6).
  - Any checkout from before this change then refuses that ledger on read mode, `--record`, and export, failing loud without writing (G3). Every worktree on an older base shares the same ledger.
  - Nothing is written by the tool outside the existing consent gate and lock (row 6).
  - New data at rest is per-PR priced-model IDs, pricing-variant labels, and per-model splits. It sits in a file whose mode is preserved (0600 on creation; row 42).
  - Read mode prints no new field. The export gains one row-level, DO-NOT-PUBLISH column (G4).
  - Unrecognized model IDs are never written by name, and the producer check asserts that (row 10, M5).
  - `--record` can now exit 1 after finishing every branch, when a row was recorded without its breakdown. Today exit 1 always means the run stopped early or refused (row 51, M6). A row with a degraded `status` still exits 0.
- **Consumers with recording off.** No visible change. Read mode runs the same new accumulation (`pr_cost.py:432`), so a defect there would abort it too (M5). Nothing creates or rewrites a ledger, and an existing pre-model file stays pre-model and parses. `cost` dollars are unchanged because `_price_turn` is not edited (row 38).
- **Every `workstream-cost` user, recording on or off.** It shares the changed accumulator and runs behind no consent gate. It reads none of the new keys, so its output is unchanged, but a defect in the new accumulation would abort it too (row 56, M5).

**Root problem.** The ledger sums per-class tokens and dollars across every priced model. A later reader therefore cannot re-price a mixed-model row under a common rate table, and the strict parser has no recognized shape for a widened row.

**Threat.** A schema change could fail in three ways, and each reaches every stow consumer whose `--record` runs after a pull:
- It could silently drop, shift, or misread an existing row, which is the sole surviving record once transcripts age out.
- A cell that fails its write-time check could block the scalar row it rides with. That PR then loses its whole row once its transcripts age out. A code defect that raises any other exception while building the cell aborts the run, as any defect in that code path does today (M5).
- It could write a model identifier or per-model figure somewhere the export's redaction triage hasn't classified.

**Rollback (forward-only).**
- Before any consumer's first write by post-change code, upgrade or creation, `git revert` is clean. A ledger first created by post-change code carries the current header, so old code refuses it too.
- After that write, reverted code is pre-change code, and pre-change code refuses the upgraded ledger on every path (G3). Rollback therefore keeps the multi-header reader and the cell codec (M1, M3) and reverts or disables only the producer (M4, M5). A producer that writes `None` keeps every ledger valid, and `_check_model_breakdown_cell` skips a `None` cell (nothing recorded, nothing to check; M5). Roll forward, never revert; the PR body and the frozen tuple's comment say so. A full revert deletes that comment and the CHANGELOG entry, so the PR body is the surviving warning.
- **Canonical recovery statement (every other channel quotes it).** An upgraded ledger cannot be read by older checkouts, and there is no supported downgrade or way to run pre-change code against it. An older checkout that refuses the ledger recovers by updating (`git pull` for a stow clone; a rebase or merge for a worktree on an older base). The ledger is intact; nobody deletes or recreates it, and the only hand edits are the two cell-level ones in M8 (blank an undecodable `model_breakdown` cell, restore a stripped trailing tab). If a current checkout's recording is blocked by a defect, turning `pr_cost_recording` off stops every write while read mode still parses the ledger; PRs still inside `cleanupPeriodDays` can be captured after the fix.

**Givens:**
- **G1 (changed).** Per-model data for a PR can be captured only while its transcripts survive (`cleanupPeriodDays`, default 30 days). Inside that window, `--record --force --pr N` appends a correcting row that carries the breakdown (row 45). That row recomputes every scalar from the transcripts that survive and becomes the PR's current row, so it is safe only while every session of the PR is still inside the window. Once the transcripts age out, a row captured before this change lands can never gain per-model values. Reason: Claude Code's transcript retention is vendor-defaulted and operator-owned (`docs/pr-cost.md:3`).
- **G2.** Rates differ per model inside a family, and the fast-mode and US-inference-geo multipliers apply per turn. Reason: vendor-imposed (`pricing.py:40-44`, `:52-77`, citing the vendor pricing pages).
- **G3 (changed).** Code already on consumers' disks parses only today's current (39-column) and pre-host (38-column) headers. That covers worktrees on older bases and machines not yet pulled. Reason: deployed code is out of this plan's reach, and each operator decides when to pull (`pr_cost_ledger.py:197-202`; row 39).
- **G4.** Export rows are row-level DO-NOT-PUBLISH data. Reason: this is the owner's redaction policy (`CLAUDE.md` "Redact private-project-identifying content"; `docs/pr-cost.md:163`).

**Assumption ledger.**

| # | Assumption | Tag |
|---|---|---|
| 1 | Plan only the ledger-schema migration. The pricing-history module, the era direction-word definition, recapture retirement, and the combining subcommand are each a later plan. | `[engineer-verified: "Migration only I think"]` |
| 2 | The prerequisite names per-model per-class token and dollar columns plus a models-used column. | `[verified: .claude/plans/pr-cost-aggregation-methodology.md:323; gh issue view 1239 Prerequisites section]` |
| 3 | The engineer sent the legacy-row, shape, variant and safeguard questions to consults. This covers only the instruction to consult, not any design choice. Every option in D1-D5 is this plan's proposal. | `[engineer-verified: "Ask staff data engineer and the architect"]` for D1; `[engineer-verified: "Same response as for prior question"]` for D2 and D3; `[engineer-verified: "Ask the architect"]` for D4 and D5 |
| 4 | The parser is strict on width, and line 1 must equal the current or pre-host header. The pre-host tuple is `COLUMNS[1:]` of the live tuple, so appending a column silently redefines it. | `[verified: pr_cost_ledger.py:49-53, :118-121, :197-210]` |
| 5 | The writer always rewrites the whole file under the current header and re-parses it before `os.replace`. | `[verified: pr_cost_ledger.py:297-310]` |
| 6 (changed) | Only `--record` writes. It writes after the `pr_cost_recording` gate and the git-tracked-path refusal, inside the per-branch lock. Read mode and export only parse. Read mode prints named fields only. | `[verified: pr_cost.py:450-462, :464-553 (gate), :554-572 (git-tracked refusal), :670-741, :279-293; pr_cost_export.py:211; _write_pr_cost_ledger_file has no non-test caller outside pr_cost.py:736]` |
| 7 | Export columns derive from ledger columns, and every non-identity key passes through the shared formatter. | `[verified: pr_cost_export.py:27-30, :85, :231]` |
| 8 | The triage test fails on any untriaged ledger column. | `[verified: test_transcript_pr_cost_export.py:179-200]` |
| 9 | The formatter renders non-bool, non-float values with `str()`, so a decoded dict would render as a Python repr. | `[verified: pr_cost_ledger.py:226-234]` |
| 10 (changed) | Per-class dollars and tokens accumulate only for priced turns. An unpriced turn's tokens go only to `unpriced_tokens`. So the leaves summed across models equal the per-class token columns exactly, and every breakdown key is a `_MODEL_BASE_INPUT_RATES` key. Unpriced model identity is deliberately never recorded, because a `message.model` string can carry an account-identifying pre-announcement codename. | `[verified: pricing.py:534-536; cost.py:160-168, :455-458]` |
| 11 | `_price_turn` multiplies every class by 2 when `usage.speed == "fast"`, then by 1.1 when `usage.inference_geo == "us"`. | `[verified: pricing.py:43-44, :549-552]` |
| 12 | `_fam` folds Fable into `other`. Rates differ inside a family: `claude-opus-5-5` $4.00 vs `claude-opus-5` $5.00, and `claude-sonnet-5` $2.00 vs `claude-sonnet-4-6` $3.00. | `[verified: render.py:13-21; pricing.py:52-58]` |
| 13 | `_price_turn` has 18 non-test call sites across 10 modules, so its return signature stays as it is. | `[verified: grep "_price_turn\(" outside tests]` |
| 14 (changed) | The multiplier-condition pair appears at five non-test sites: `pricing.py:549-552`, `cache_rebuild_rules.py:297-300`, `:330-333`, `:370-373`, and `transcript-analysis.py:2707-2710`. `_pricing_variant` makes a sixth. | `[verified: grep for usage.get("speed") == "fast" outside tests]` |
| 15 (changed) | `_legacy_row_line` slices the live formatter's output (`[1:]`). After the append it would emit 39 cells under the 38-column pre-host header, so it must be rebuilt from a literal. | `[verified: tests/_pr_cost_helpers.py:206-211]` |
| 16 | `test_mismatched_header_raises` builds its bad header as `COLUMNS[:-1]`, which becomes the valid pre-model header. | `[verified: test_transcript_pr_cost_ledger.py:253-257]` |
| 17 (changed) | `test_transcript_cli_bootstrap.py` seeds a literal 39-column header and row, which is today's current header. After this change that is the pre-model header, so the test becomes an independent pre-model regression check through a subprocess, with no edit. | `[verified: test_transcript_cli_bootstrap.py:255-269, :299-311]` |
| 18 | The combining procedure reads by column name and refuses an input that lacks a column it needs. | `[verified: docs/pr-cost.md:169, :189]` |
| 19 | The re-record text says every prior row stays byte-identical. The upgrading write appends an empty cell to every pre-model row, and the host precedent already alters pre-host rows. | `[verified: docs/pr-cost.md:82; pr_cost_ledger.py:209-210, :297]` |
| 20 | `docs/pr-cost.md:9` and `:140` point `_PR_COST_LEDGER_COLUMNS` / `_PR_COST_EXPORT_COLUMNS` at `transcript-analysis.py`. They actually live in `pr_cost_ledger.py` and `pr_cost_export.py`. | `[verified]` |
| 21 (changed) | Parse-then-format is the identity on every cell this tool writes: ints via `str`, floats as `.6f`, bools as `true`/`false`, strings as-is. On a hand-edited cell it preserves the value at the ledger's declared precision: `1.5` re-renders as `1.500000`, `0.1234567` as `0.123457`, and `-1e-7` as `-0.000000`. M3 extends this to `model_breakdown`, and M6's postcondition compares floats at that precision. | `[verified: pr_cost_ledger.py:153-170, :226-234]` |
| 22 | The export's `corpus=` digest reads the first data row's `(captured_at, machine)`, and the upgrade moves no row. | `[verified: pr_cost_export.py:221]` |
| 23 | How often fast-mode or US-geo turns occur in any consumer's corpus. | `[unverified]`. Absent them, the variant level costs one `standard` key per model. |
| 24 | The repo has no generic schema-version convention. | `[verified: Step 3 exploration, not reopened]` |
| 25 | The merged methodology plan already treats old rows as carrying no per-model split, permanently. | `[verified: .claude/plans/pr-cost-aggregation-methodology.md:35]` |
| 26 | `CHANGELOG.md` records consumer-visible pr-cost changes under `[Unreleased]`, with a `**Migration:**` sub-bullet convention. | `[verified: CHANGELOG.md:5, :147, :149]` |
| 27 (changed) | `pr_cost_ledger.py` gains no package-module import. The codec needs only stdlib `json` and `re`. The producer check needs `pricing`, so it lives in `pr_cost.py`, which already imports `pricing`. `docs/transcript-analysis-architecture.md:271` ("Imports `ledger_common` by module") therefore stays true. | `[verified: pr_cost_ledger.py:20-21; pr_cost.py:23; docs/transcript-analysis-architecture.md:267-273]` |
| 28 | This worktree sits four directories below the repo root because the branch name contains `/`. `README.md:522` says three. The main-root `.venv` serves it at `../../../../.venv`. | `[verified: worktree path; README.md:522; earlier test runs this session used the main-root .venv]` |
| 29 | D1: upgrade on the next `--record` under a recognized pre-model header. Staff data engineer and architect agree. | `[engineer-verified: "Confirm D1, D2, D3 (Recommended)"]` |
| 30 (changed) | D2: one `model_breakdown` cell, with "models used" derived from its keys (the sorted priced keys). The consultants no longer diverge: the staff data engineer withdrew the stored `models_used` proposal in round 1. | `[engineer-verified: "Confirm D1, D2, D3 (Recommended)"]` |
| 31 | D3: record a pricing-variant level. The architect recommends it, and the staff data engineer named the multiplier gap as the biggest open item. | `[engineer-verified: "Confirm D1, D2, D3 (Recommended)"]` |
| 32 (changed) | D4: no backup copy, from the tool or from any documented command. The operative reason is that every write refuses to publish unless every prior row survives value-for-value (row 36), and rollback is forward-only (Rollback's canonical recovery statement). No downgrade command is documented or shipped. The postcondition does not cover a parser-side misread in M1's reorder or default fill; the hand-written literal rows in Verification 3 do. Row 36's recommendation text also states "No backup copy (D4 stands)". D5 resolves through row 36. | `[engineer-verified: "Your recommendations make sense to me"]` for no backup copy, through row 36's recommendation text; dropping the downgrade command is `[engineer-verified: "Accept the drop (Recommended)"]`, on Fable's recommendation |
| 33 (changed) | The engineer was asked which of four extras stay: the variant level M4, the CHANGELOG entry, the two stale doc-pointer fixes, and the `_LEGACY_` to `_PRE_HOST_` rename. The answer was conditional on the architect having called them all needed. Decision 3 (row 37) cuts the rename. The architect now calls the CHANGELOG entry needed, because it is the only pre-pull channel that carries G3's refusal string and its recovery. M4 and the stale-pointer fixes stay as before. | `[engineer-verified: "If plan architect said all are needed then I’m inclined to agree"]` covers only that conditional inclination; the rename is settled by row 37 |
| 34 | The engineer asked for an adversarial review of the plan by Fable. | `[engineer-verified: "Ask fable for an adversarial review"]` |
| 35 (new) | Decision 1. Dollars stay in the cell: leaves carry `tokens` and `usd_micros`. The sum check moves to the producer, raising before the write, plus a unit test. The read path does no tolerance arithmetic. | `[engineer-verified: "Your recommendations make sense to me"]` |
| 36 (changed) | Decision 2. The upgrading write gets a typed-value check. It parses the pre-upgrade file, applies column defaults by name, and refuses to publish unless the rewritten file's first N parsed rows equal them. The check is value-level, not byte-level. No backup copy (D4 stands). This resolves D5. The engineer then scoped it to every write, with float columns compared at the ledger's six decimals (M6). Reasoning: after the first upgrading write every row on disk was rendered by this tool, and every value the parser accepts survives a re-render at that precision (row 21), so the check guards the formatter, the writer and the re-parse. A refusal then means a writer defect, or an edit made outside the lock during the run, since the caller and the writer read the file under one lock. The check cannot see a parser-side misread (M1's reorder or default fill), which yields the same wrong value on both sides. | `[engineer-verified: "Your recommendations make sense to me"]` for the typed check, no backup and D5; `[engineer-verified: "(c) Every write, floats at 6 decimals (Recommended)"]` for the every-write scope and six-decimal floats; the shared lock is `[verified: pr_cost.py:670-741]`; the rest of the reasoning is `[unverified]` |
| 37 (new) | Decision 3. Cut the `_LEGACY_` to `_PRE_HOST_` rename. Name the new frozen header `_PRE_MODEL_*`, leave the old names, and put a one-line comment on each. `pr_cost_export.py` and the two accounts-test lines (`:461`, `:474`) are not touched for the rename. | `[engineer-verified: "Your recommendations make sense to me"]` |
| 38 (new) | Decision 4. Cut the `_price_turn` body rewrite. Add `_pricing_variant`, call it only from `cost.py`, and leave `_price_turn` alone. Add a consistency test: for each variant, `_price_turn` dollars equal the base-rate dollars times the multiplier the label implies, pinned with exact `==`. Keep `_pricing_variant` on exact `"fast"` and `"us"` comparisons, with no lower or strip. | `[engineer-verified: "Your recommendations make sense to me"]` |
| 39 (new) | The live tuple has 39 columns and the pre-host header 38. After this change: current 40, pre-model 39, pre-host 38. | `[verified: pr_cost_ledger.py:23-41 counted; the session's len() check]` |
| 40 (new) | Today, a write-path `_PrCostLedgerParseError` reaches `sys.exit(1)` inside the per-branch loop. That skips every later branch and, under `--all-accounts`, every later account. The ledger stays intact: `os.replace` is never reached, the temp file is unlinked, and the lock is released. | `[verified: pr_cost.py:735-741; pr_cost_ledger.py:311-314]` |
| 41 (changed) | `json.loads` accepts duplicate keys (last wins) and `NaN`/`Infinity` by default. It raises `RecursionError` on deep nesting (`'{"a":' * 100000`) and `ValueError` on an over-long integer literal (5000 digits), through CPython's default 4300-digit int string-conversion limit, which `PYTHONINTMAXSTRDIGITS` can change. | `[verified: CPython 3.12.3, the local venv and the reviewers' round-2 and round-3 scratch runs; CI pins '3.12' (.github/workflows/tests.yml:127)]` |
| 42 (new) | A user-loosened ledger mode is preserved across a write by tested intent, not reset to 0600. | `[verified: test_transcript_pr_cost_ledger.py:312-319; pr_cost_ledger.py:306-309]` |
| 43 (new) | `test_default_columns_rendering_is_unchanged` holds a hand-written full-row golden that gains a trailing empty cell. `_parse_pr_cost_ledger_row_cells` has a direct two-argument test caller. | `[verified: test_transcript_pr_cost_ledger.py:363-378, :135]` |
| 44 (new) | "The disclosed fields are not neutral" in `docs/transcript-analysis.md` is a bold run-in paragraph, not a heading, so the `§` citation form cannot target it. | `[verified: docs/transcript-analysis.md:671; .claude/rules/citation-grammar.md]` |
| 45 (changed) | `--record --force --pr N` appends a correcting row instead of overwriting. The row recomputes every scalar from the transcripts that survive and becomes the PR's current row, with the prior row as its `supersedes`. A PR captured before this change can therefore gain a breakdown, but only while every session of that PR is inside the transcript window; otherwise the new row understates the PR. Under `--all-accounts` it re-captures the PR in every account whose corpus touched the branch. | `[verified: docs/pr-cost.md:82; pr_cost_ledger.py:268-285; pr_cost.py:684-692, :700-706, :721, :727]` |
| 46 (new) | `legacy_header_accounts` counts only pre-host-header ledgers, and `docs/pr-cost.md` never names the field. | `[verified: pr_cost_export.py:208; grep of docs/pr-cost.md, no hits]` |
| 47 (changed) | No hook, skill, or committed script runs `pr-cost --record` or `pr-cost-export`. Scripted and cron `--record` callers may exist outside the repo. | `[verified: staff-backend-engineer round-1 consumer inventory, not reopened]` for the first sentence; `[unverified]` for the second, which `CHANGELOG.md:146-147`'s advice to "any scripted or cron caller" gives reason to expect |
| 48 (new) | Model and variant splits are the same class of data (per-PR spend) behind the same `pr_cost_recording` gate, so no re-consent is needed. The CHANGELOG entry carries the notice. | `[unverified]`: this plan's judgment (the CISO reviewer concurs); `claude/.claude/hooks/config-keys.psv:96` |
| 49 (new) | The ledger renders every float column with `.6f`. | `[verified: pr_cost_ledger.py:232]` |
| 50 (changed) | Write-time failure policy (M6): when the producer check fails for a PR, record the scalar row with an empty cell, finish the run, then exit 1. The engineer delegated the choice to the architect. The policy holds with no carve-out, including a `--force` re-capture over a row with a populated cell: the empty-cell row becomes the latest for that key, and the populated row stays in the ledger history. Skipping that PR instead would leave the prior row's scalars, under its old `rate_stamp`, as the current row. It would also give `model_breakdown` a different selection rule from the sibling `status` column, whose export collapse always takes the latest capture and never prefers an older `ok` row. | `[engineer-verified: "Ask the architect"]` covers only the delegation; `[engineer-verified: "Skip that PR? That doesn’t seem right. Can you have fable check?"]` covers only the engineer's doubt about a skip carve-out and the request for Fable's check; degrade-always with exit 1 is `[engineer-verified: "Degrade always, exit 1 (Recommended)"]`, resting on Fable's read-only check; the collapse rule is `[verified: docs/pr-cost.md:173]` |
| 51 (new) | Today `--record` exits 1 only when it stops early or refuses. A recorded row with a degraded `status` exits 0, and an unforced rerun without `--pr` skips an already-captured PR with exit 0. | `[verified: pr_cost.py:603-605, :614-615, :662-664, :677-679, :684-692, :732-739 (each early exit); :653-655 and :742-744 (a degraded-status row is recorded and the loop continues)]` |
| 52 (new) | The `--record` write call site catches only `_PrCostLedgerParseError`, so each new writer refusal must raise that type to exit 1 cleanly instead of with a traceback. | `[verified: pr_cost.py:735-739]` |
| 53 (new) | `docs/pr-cost.md:60` forbids hand-editing the ledger with no exception, which contradicts the two documented hand-edit recoveries: blanking an undecodable `model_breakdown` cell, and restoring a trailing tab an editor stripped (M1, M8). | `[verified: docs/pr-cost.md:60]` |
| 54 (removed) | Row retired with the downgrade command; the number is kept so later rows keep theirs. | n/a |
| 55 (new) | A populated cell costs about 0.3 KB per (model, variant) group, against a row of roughly 400 bytes today: about 1-2 KB for a PR touching two or three models, and about 12 KB at the ceiling of 10 table models times 4 variants. Each write parses the whole ledger three times under the per-branch lock, and a concurrent `--record` waits 30 s for that lock before exiting. At 5,000 rows, stand-in codec benchmarks put the parsing at about 0.5-1.5 s per write for typical cells and about 6-8 s with a ceiling-size cell on every row; the 30 s wait is reached near 18,000 ceiling-size rows. | `[verified: pricing.py:52-77 (10 keys); ledger_common.py:36 (30 s)]`; the byte sizes are this plan's arithmetic; the timings are staff-backend-engineer's and staff-data-engineer's round-3 scratch measurements with a stand-in codec, `[unverified]` |
| 56 (new) | `_compute_pr_cost_branch_totals` and `_new_pr_cost_agg` have a second caller, `cost._compute_workstream_dollars` (`cost.py:266`), behind the `workstream-cost` subcommand. It has no consent gate and reads none of the new keys. `test_transcript_workstream_cost.py:305` calls the accumulator directly, and `select-tests.py` selects that file through its `SCRIPTS_DIR` rule. | `[verified: staff-backend-engineer and staff-product-engineer round 3, not reopened; select-tests.py:35, :548]` |

**Mechanisms.**

**M1: Frozen headers and one header table.** `anchors: row4, row15, row16, row37, row39, G3`
- Freeze today's 39-column tuple as the literal `_PR_COST_LEDGER_PRE_MODEL_COLUMNS`. Define `_PR_COST_LEDGER_COLUMNS` as that tuple plus `"model_breakdown"`.
- Derive `_PR_COST_LEDGER_LEGACY_COLUMNS` from the frozen tuple (`[1:]`), not the live one, and move the `[0] == "host"` guard onto the frozen tuple. Every `_LEGACY_` name stays (decision 3).
- Replace the two-way header `if`/`elif` (`pr_cost_ledger.py:197-202`) with two structures:
  - one table mapping each recognized header line to its column tuple;
  - one map of each added column's default: `host` -> `_PR_COST_LEDGER_LEGACY_HOST_DEFAULT`, `model_breakdown` -> `""`.
- The file parser checks each row's width against its file's own header, and the message names that header's width. A row exactly one cell short under the current header reads `line N: expected 40 columns, got 39 (if an editor trimmed trailing whitespace, append a tab to this line)`, because an editor that trims trailing whitespace produces exactly that row from every row without a breakdown. It then reorders cells by name into current column order, fills defaults, and calls `_parse_pr_cost_ledger_row_cells(cells, line_no)`. That function keeps its signature and its full-current-width contract (row 43).
- The unrecognized-header message becomes `missing or mismatched pr-cost ledger header row (if a newer claude-config wrote this file, update this checkout -- see docs/pr-cost.md in the claude-config repo)`. It keeps the substring that `test_transcript_pr_cost_ledger.py:250,256` match.
- Update the comment at `:44-48` and the docstring at `:179-181`, which call the pre-host header "the one" exception.
- Frozen headers are kept indefinitely, with no sunset: a ledger whose owner never records again stays at its old header and must keep parsing.

Prescribed comments:
- On the frozen tuple, one line as decision 3 asks (row 37): "Exact column order of pre-model_breakdown ledgers; never edit, and never revert to code that lacks it."
- On the `_LEGACY_` block: "'Legacy' in these names means the pre-host header only; the pre-model header is _PR_COST_LEDGER_PRE_MODEL_COLUMNS."

This is no heavier than the alternatives it replaces:
- (a) A third `elif` with positional padding would pad the pre-host branch twice by position, which is the shape that produced row 4's trap.
- (b) Ragged rows under one header would make the export KeyError on a missing key and let a truncated row parse silently.

**M2: The cell.** `anchors: root, row9, row10, row12, row35, row49`
- **Shape.** The cell is `{model: {variant: {class: {"tokens": int, "usd_micros": int}}}}`, encoded with `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`. The producer writes every class for each present (model, variant).
- **Meaning.** An empty cell parses to `None`, meaning not recorded: the row predates the column, or its breakdown failed the write-time check (M6). `{}` means recorded with no priced turns. A literal `null` cell is malformed, so "not recorded" has exactly one spelling.
- **Units.** `usd_micros` is the integer that the ledger's own `.6f` rendering of the leaf's float dollars denotes (row 49). One helper in `pr_cost.py`, `_usd_to_micros(dollars)`, computes it as `int(Decimal(f"{dollars:.6f}").scaleb(6))`. M5's check uses the same helper on the row's `<class>_usd` scalars.
- **Labels.** Variant and class labels have one runtime home, `pricing._PRICING_VARIANTS` and `pricing._TOKEN_CLASSES`, which the producer check reads (M5). A hand-written test literal pinned `==` to each is the rename tripwire. The parser checks labels for key shape only (M3).
- **"Models used"** is the sorted top-level keys, so it covers priced models only. A nonzero `unpriced_turns` signals excluded models (row 10).
- **Formatting.** The formatter gains a JSON-column branch: `None` renders as `""`, and anything else goes through the canonical encoder. Export passthrough reuses this branch with no edit to `pr_cost_export.py` (row 7).

Prescribed comments:
- On the cell column: "Empty cell: not recorded. `{}`: recorded, no priced turns."
- On the codec's key rule and leaf keys: "Persisted contract: a cell outside this key rule, these leaf keys, or this depth makes every checkout with this codec refuse the ledger. Never reshape model_breakdown; a new cell shape is a new appended column under a new frozen header. New variant and class labels need neither, because they are checked for key shape only."

A JSON cell is heavier than a scalar column, so these lighter options were checked:
- (a) Fixed per-family columns fail row 12.
- (b) Fixed per-model columns turn every model release into a migration of this plan's shape.
- (c) A long-format sidecar file. It needs no cross-file atomicity, because a missing entry already means "not recorded". Its real costs are a join key `(host, repo, pr_number, machine, captured_at)` and a second 0600 file at rest. The in-file column follows the `host` precedent instead. M6's write-time policy keeps the shared failure domain from costing a scalar row.
- (d) A hand-rolled compact grammar reimplements what stdlib `json` provides.

**M3: Cell codec, structure-only and value-preserving.** `anchors: row9, row21, row41, G3`

`_decode_model_breakdown_cell(cell, line_no)` in `pr_cost_ledger.py`:
- `""` decodes to `None`.
- Any other cell goes through `json.loads` with an `object_pairs_hook` that rejects a duplicate key at any level, since last-wins would drop a value silently.
- `ValueError` (which covers `JSONDecodeError`) and `RecursionError` both map to `_PrCostLedgerParseError`.
- The top level is an object and may be empty. Each model value and each variant value is a non-empty object. Each class value is a leaf.
- Every key at every level must pass `re.fullmatch` against one shared rule, `[a-z0-9][a-z0-9._-]{0,63}`. The 64-character cap is a sanity bound, not a vendor limit. The longest table key today is 25 characters (`claude-haiku-4-5-20251001`).
- A leaf has exactly the keys `tokens` and `usd_micros`. Each value is a non-bool `int` and at least 0, which rejects floats, NaN, and Infinity.
- The codec runs no membership check (model, variant, or class), no class-completeness check, and no cross-column check. Those run in the producer (M5).
- This strictness is a persisted contract. A new leaf field, a key outside the rule, or another nesting level makes every checkout carrying this codec refuse the whole ledger. M1's header table can only append a column, so such a change ships as a new appended column under a new frozen header, and `model_breakdown`'s shape never changes. New variant and class labels need no new column, because the codec checks them for key shape only.
- Every message reads `line N: malformed model_breakdown (<rule>)`, with the rule taken from a fixed literal set. No message embeds cell text (`pr_cost_ledger.py:115-117`).

One canonicalization policy applies, consistent with row 21:
- Parse preserves values and accepts whitespace and key-order variants. The formatter re-encodes canonically on the next write. A reformatted cell therefore never blocks the scalar rows.
- A cell the codec cannot decode fails loud on read mode, `--record`, and export, like any other malformed cell. Blanking that cell, keeping its tab, is always valid and is the documented recovery (M8).

Lighter options checked:
- (a) Membership against live `pricing` tuples. Renaming a label would make every ledger holding it unreadable after a pull, and adding one would make older checkouts refuse new rows.
- (b) Membership against a frozen registry. This fixes renames, but every later addition still makes older checkouts refuse new rows with no header change to signal it. Parse-time membership also protects nothing this tool interprets: read mode prints no cell field, and export passes the cell through.
- (c) A canonical-bytes rule. It rejects value-preserving reformatting, which bricks the scalar rows and contradicts row 21.
- (d) An opaque string. It gives export consumers no documented shape and lets arbitrary text reach the export.

**M4: Pricing variant.** `anchors: row11, row13, row14, row38, G2`
- `pricing.py` gains `_PRICING_VARIANTS = ("standard", "fast", "us_geo", "fast_us_geo")` and `_pricing_variant(usage) -> str`. The function uses the same exact `usage.get("speed") == "fast"` and `usage.get("inference_geo") == "us"` comparisons as `pricing.py:549-552`, with no case-folding or stripping.
- `_price_turn` is untouched, and only `cost.py` calls `_pricing_variant` (decision 4). This adds a sixth copy of the condition (row 14). The exact-`==` consistency test (Verification 3) keeps that copy from diverging from `_price_turn` for the two multiplier conditions that exist today. It cannot see a new condition added to `_price_turn`: those turns would be priced with the multiplier and labelled `standard`, and every sum would still agree. A prescribed comment on the multiplier constants (`pricing.py:40-44`), outside `_price_turn`, names `_pricing_variant` for whoever adds one.

Prescribed comments:
- On `_PRICING_VARIANTS` and on `_TOKEN_CLASSES`: "Labels persist in pr-cost ledger model_breakdown cells; never rename or remove one. Older checkouts read an added label, because the ledger parser checks labels for key shape only."
- On the multiplier constants (`pricing.py:40-44`): "A new multiplier condition in _price_turn must also become a _pricing_variant outcome and a _PRICING_VARIANTS label, or pr-cost ledger cells file its turns under the wrong variant."
- On `_MODEL_BASE_INPUT_RATES`: "Keys persist verbatim in pr-cost ledger rows and exports; add vendor-published model IDs only."

Lighter options checked:
- (a) A combiner-side recorded-minus-repriced residual cannot separate a rate change from a multiplier, so rows with fast or US-geo turns would never be re-priced exactly.
- (b) A per-row count of multiplied turns would label those rows but could not re-price them.

**M5: Accumulation, row construction, and the producer check.** `anchors: row10, row12, row35, row38, row49, row56`
- `cost._new_pr_cost_agg` gains `"by_model": {}`.
- `cost._compute_pr_cost_branch_totals` adds each priced turn's tokens and float dollars at `[model][_pricing_variant(usage)][class]`. It does this right after `_token_counts` (`cost.py:165-168`), in the same iteration that feeds the scalars. `workstream-cost` shares this function and ignores `by_model` (row 56).
- `pr_cost._new_pr_cost_row` writes `model_breakdown` from `pr_cost._build_model_breakdown_cell(by_model)`, which converts each leaf once with `_usd_to_micros`. That function is the seam the degrade tests patch. The zero-activity default yields `{}`.
- `pr_cost._check_model_breakdown_cell(cell, row)` raises `_ModelBreakdownCheckError(rule)` on the first failure among these five checks. `rule` comes from one module tuple holding the closed set `shape`, `model-membership`, `label-membership`, `tokens`, `dollars`, and the exception's text is the rule alone, never a key or value. A `None` cell skips all five checks (nothing recorded, nothing to check), so a disabled producer cannot crash a run. The decoder's own raises use `from None`, as the rest of the module does, so no cell text reaches `__context__`:
  1. **Shape.** The canonical encoding decodes through `pr_cost_ledger._decode_model_breakdown_cell` to the same value, so the tool never writes a cell it would refuse to read. The check catches any `_PrCostLedgerParseError` from the decoder and raises `_ModelBreakdownCheckError("shape")` `from None`. Every decoder rejection maps to this one rule, so no rule text crosses the module boundary, and the decoder's message, with its placeholder line number, never surfaces.
  2. **Model membership.** Every model key is in `pricing._MODEL_BASE_INPUT_RATES`.
  3. **Label membership.** Every variant key is in `pricing._PRICING_VARIANTS`. Each (model, variant)'s class keys equal `pricing._TOKEN_CLASSES`. The check reads the live tuples; there is no second copy.
  4. **Tokens.** For each class, the summed leaf `tokens` equal `<class>_tokens` exactly.
  5. **Dollars.** For each class, the summed leaf `usd_micros` differ from `_usd_to_micros(<class>_usd)` by no more than a tolerance set by N, the number of (model, variant) groups:
     - **N = 0.** The tolerance is 0, so `{}` requires every class scalar to be zero.
     - **N = 1.** The tolerance is 0. The leaf and the scalar accumulate the same floats in the same order, so they agree exactly.
     - **N ≥ 2.** The tolerance is `(N + 1) // 2`. That is the most the per-leaf rounding (at most 0.5 each) plus the scalar's own rounding can produce. Prescribed comment at the tolerance: "N leaves and the scalar each round by at most half a micro-dollar, so their integer gap is at most (N + 1) // 2."
- **Call site.** The `--record` loop calls the check on `new_row["model_breakdown"]` immediately after `_new_pr_cost_row` (`pr_cost.py:723-729`) and before `_append_pr_cost_ledger_row` (`:731`), so it checks the exact dict the writer formats.
- **Construction defects abort.** `_usd_to_micros` and the cell build can raise only on a non-finite float, which only an absurd token count produces, and such a row already fails today's write (`pr_cost_ledger.py:158-159`). Any exception other than `_ModelBreakdownCheckError`, there or in the accumulation, aborts the run before the write with the ledger unchanged, as any defect in `_compute_pr_cost_branch_totals` or `_new_pr_cost_row` does today. The accumulation runs over the whole corpus before the branch loop (`cost.py:107-110`), so it cannot be contained per PR. A defect there also aborts `workstream-cost` (row 56). The accumulation, literal, and sweep tests catch such defects before merge.
- `opus_dollars` stays stored and unchanged in derivation, because legacy rows need it. A unit test pins it to the opus-family leaf sum within `pytest.approx(rel=1e-12)`, because the two sums add the same floats in different orders.

**M6: Write path, failure policy, and notices.** `anchors: row5, row6, row36, row40, row42, row47, row45, row50, row51, row52, row55, G3`
- No migrator, backup copy, or standalone tool runs. Read mode and export never write.
- **Value postcondition (decision 2, scoped by the engineer; row 36).**
  - `_write_pr_cost_ledger_file` first reads and parses the existing ledger, if there is one, which applies M1's defaults by name. The read runs before `mkstemp`. `FileNotFoundError` means no prior file. Any other `OSError` propagates unchanged, as the caller's own read at `pr_cost.py:674` already does.
  - A prior-file parse failure raises `_PrCostLedgerParseError("refusing to write the ledger (ledger unchanged): the ledger on disk changed during this run and no longer parses: <inner>")`. The caller parsed the same bytes under the same lock, so only an edit outside the lock reaches it, and its `line N` is the operator's own file.
  - The writer takes the prior header from the text it already read. `_parse_pr_cost_ledger_file_text` keeps its signature and return type.
  - After the existing read-back byte check and re-parse, it refuses to publish unless the rewritten file's first N parsed rows equal those prior rows.
  - The comparison is typed. Float columns compare as `round(x, 6)` on both sides, the precision the ledger declares (row 49). Every other column compares with `==`, `model_breakdown` as its decoded value. So `1.5`, a reformatted JSON cell, a hand-edited `0.1234567`, and `-1e-7` all pass, and the write rounds them as it does today (row 21).
  - The check runs on every write, the upgrading write included.
  - A refusal raises `_PrCostLedgerParseError` (row 52) with one of two exact messages, which name a data-row ordinal and a column, never a value. `<k>` is 1-based over data rows, and `<col>` is the first differing column in current column order:
    - `refusing to write the ledger (ledger unchanged): the rewrite would change prior data row <k>, column <col> -- a claude-config defect, or the ledger was edited during this run; rerun, and if it repeats on a current claude-config, report it`
    - `refusing to write the ledger (ledger unchanged): the rewrite would drop prior data rows from <k> on -- a claude-config defect, or the ledger was edited during this run; rerun, and if it repeats on a current claude-config, report it`
  - Coverage: it catches a dropped, reordered, shifted, or value-changed row from the formatter, the writer, or the re-parse. It cannot catch a parser-side misread (M1's reorder or default fill), which yields the same wrong value on both sides; Verification's hand-written literal rows cover that.
- **Upgrade notice.**
  - `_write_pr_cost_ledger_file` returns `True` when it replaced a file whose header line was not the current one, and `False` otherwise, creation included. It prints nothing.
  - The `--record` loop prints the notice to stderr after its own bookkeeping for that row (`pr_cost.py:742-744`), once per upgraded ledger: `pr-cost: upgraded the ledger to the current header -- older claude-config checkouts refuse this file until they are updated, and there is no supported downgrade; see docs/pr-cost.md in the claude-config repo`.
  - Under `--all-accounts`, "the ledger" reads "account-N's ledger", using the loop's existing `account-{ordinal}` label (`pr_cost.py:583`, `:702`).
  - A refused write returns nothing, so it prints no notice. A stderr failure while printing cannot interrupt a publish, because the file is already replaced and the row recorded.
  - Creation prints nothing by design: no older checkout has read that file, and the CHANGELOG entry covers the lockout.
- **Cost.** A populated cell adds about 0.3 KB per (model, variant) group (row 55). Each write parses the whole ledger three times under the per-branch lock: the caller's read, the writer's prior read, and the staged re-parse. Round-3 stand-in benchmarks at 5,000 rows put that at about 0.5-1.5 s for typical cells and about 6-8 s with a ceiling-size cell on every row, against the 30 s a concurrent `--record` waits for the lock (row 55).
- **Mode.** The writer preserves the file's mode, neither widening nor narrowing it, matching the existing tested intent (row 42).
- **Failure policy:**
  - **The producer check fails (M5).**
    - The `--record` loop catches `_ModelBreakdownCheckError`, sets the row's `model_breakdown` to `None`, and records the row. It does this every time, including a `--force` re-capture over a row with a populated cell: the empty-cell row becomes the latest for that key, and the populated row stays in the ledger history (row 50).
    - After the row's write succeeds (beside `pr_cost.py:744`), it prints `pr-cost:   PR #N: per-model breakdown failed its <rule> check (<cause>) -- recorded the row without it; see docs/pr-cost.md` and moves on to the next branch. Each rule's `<cause>` is a fixed literal beside the rule tuple: `malformed transcript data or a claude-config defect` for `shape`, the one rule transcript data can reach (a negative token count fails the decoder's non-negative leaf rule), and `a claude-config defect` for the other four.
    - After the run, and after the `--all-accounts` summary line when present (`pr_cost.py:755-759`, stdout), it prints to stderr `pr-cost: <n> row(s) recorded without a per-model breakdown (<list>); those rows are valid -- once the cause is fixed, re-capture each with --record --force --pr N and this run's account flags, only while every session of that PR is still inside cleanupPeriodDays (see docs/pr-cost.md)`. `<list>` is `PR #<a>, PR #<b>`, each entry prefixed `account-K ` under `--all-accounts`. A run that stops early after a degraded row skips this line, so its per-PR lines are then the only record; M8 says so.
    - It then exits 1. An empty cell reads the same as a row recorded before the upgrade, so, unlike a degraded `status`, which names itself in the row, the missing breakdown is invisible downstream, and transcript retention makes it permanent. The exit code is the only signal a scheduled caller can detect (row 47). The path fires only on a defect or a malformed transcript, and a scheduled rerun without `--pr` skips the captured PR and exits 0, so the alarm fires once per PR.
    - This changes what exit 1 means: today it always means an early stop or a refusal (row 51). M8 documents both meanings. Exit 0 with the same count line would match the `status` column's exit behavior but leave no machine-visible signal, so it was rejected (row 50).
    - This path already prints the PR number (`pr_cost.py:632`, `:744`), and no line carries cell text.
  - **The write is refused.**
    - This covers a read-back mismatch, a re-parse failure of the staged rewrite, a failed value postcondition, and a prior-file parse failure.
    - Today's behavior is unchanged: the ledger stays unchanged, any temp file is unlinked, the lock is released, and the run exits 1 immediately (`pr_cost.py:737-739`). Each of these failures signals a writer defect, a disk fault, or an edit outside the lock, which later writes in the run would likely hit too.
    - The staged-rewrite failure is re-raised as `refusing to write the ledger (ledger unchanged): the staged rewrite failed validation: <inner>`, so nobody mistakes its `line N` for a line in their own file.
  - **The existing ledger fails to parse,** including a malformed `model_breakdown` cell.
    - Today's behavior is unchanged on every path. Read mode, `--record`, and export exit 1 and write nothing (`pr_cost.py:455-457`, `:603-605`, `:677-679`; `pr_cost_export.py:212-214`).
    - For a cell-level error, the documented recovery is to blank the cell.

Heavier options checked:
- A standalone migrator adds a write path outside the consent gate.
- D4 adds a second sensitive file at rest that has no lifecycle.
- The text-prefix check D5 first weighed would block a value-preserving re-render, a failure the typed check does not have.

One lighter option was also checked: aborting on a producer-check failure, which is today's behavior. It trades every remaining branch's scalar row for a breakdown defect (row 40), so the plan degrades instead (row 50). Skipping the PR only when a forced re-capture would supersede a populated cell was also weighed and rejected (row 50).

**M7: Export.** `anchors: row7, row8, row46, G4`
- `model_breakdown` passes through un-tokenized via M2's formatter branch, and `pr_cost_export.py` gets no edit, because export columns derive from ledger columns (row 7). Two reasons support passing it through:
  - A combiner needs a key that stays stable across exports in order to re-price.
  - The key string carries nothing beyond the vendor catalogue. The table admits vendor-published IDs only, and M5 asserts membership. The table is also committed to this public repo, and a PR's published `--summary` Cost block already names each priced model that PR used (`pricing.py:71-74`), so the export adds no new publication path for a table key.
  Model keys are checked for shape only at read and export, so the exporting checkout does not vouch for them.
- The column stays DO-NOT-PUBLISH because it rides an export row, which carries the account dimension, and because its variant labels record fast-mode and data-residency configuration (G4). The non-neutral association of a PR with a model is why a per-model split of an aggregate is its own figure (`docs/pr-cost.md:163`).
- The triage test classifies the column in a new `model_bearing_row_level` set. That set is documentation only. The literal-ID passthrough test pins the pass-through decision, so a later tokenizing change fails it; it is not a disclosure-safety test. The safety controls are M5's membership check, the table comment (M4), and the export's DO-NOT-PUBLISH marker.
- `legacy_header_accounts` keeps counting pre-host-header ledgers only, and M8 documents that (row 46).

**M8: Docs and changelog.** `anchors: row18, row19, row20, row26, row33, row44, row45, row46, row50, row51, row53`

In `docs/pr-cost.md`:
- **Schema table.** Add a `model_breakdown` row stating:
  - the shape, and that `usd_micros` is integer micro-dollars as the `.6f` columns render them;
  - that an empty cell means not recorded (the row predates the column, or its breakdown failed the write-time check), and `{}` means recorded with no priced turns;
  - that "models used" means the sorted top-level keys, priced models only, and that a nonzero `unpriced_turns` signals excluded models whose identity is never recorded;
  - that the sum invariants hold at write time: tokens exactly, dollars within the stated rounding bound;
  - that labels are never renamed or removed but a later version may add one, so readers must tolerate unknown variant and class labels, while a new leaf field, key shape, or nesting level would arrive as a new appended column under a new header, never as a reshaped `model_breakdown`, because this version's parser refuses it;
  - that model keys are checked for shape only at read and export, so the exporting checkout does not vouch for them;
  - that per-model sums cover recorded rows only;
  - that the last cell may be empty, so readers must not whitespace-strip lines;
  - that `usd_micros` is kept alongside tokens for the write-time cross-check and for rate stamps with no recoverable rate history.
- **Row-parser paragraph (`:34`).** Rewrite it to cover:
  - the three recognized headers and the per-column defaults;
  - the upgrade, which has no supported downgrade, quoting the stderr notice;
  - older checkouts' exact refusal string (`missing or mismatched pr-cost ledger header row`), with the remedy: update every checkout (`git pull` for a stow clone; a rebase or merge for a worktree on an older base); the ledger is intact; do not delete it. State Rollback's canonical recovery statement once here: an upgraded ledger cannot be read by older checkouts, there is no supported downgrade, and a checkout that cannot record loses each uncaptured PR once its transcripts age out. If a current checkout's recording is blocked by a defect, turn `pr_cost_recording` off until a fix lands;
  - the width-error suffix for a row one cell short under the current header (M1), and its fix: restore the trailing tab. Every row without a breakdown ends in a tab, so an editor that trims trailing whitespace breaks all of them at once, and the parser reports them one line at a time; no bulk repair is shipped;
  - the cell parse rule, and blanking a cell, keeping its tab, as the recovery for a cell-level parse error.

  End the paragraph with "a later column follows the same shape: freeze the outgoing header and document the new column's default".
- **`docs/transcript-analysis.md` (`:1090`).** Add one sentence to the `pr-cost` `--record` bullet: it can exit 1 after a completed run, per `docs/pr-cost.md` § "Row status".
- **Row status (`:44-52`).** Add that `--record` exits 1 after finishing every branch when any row was recorded without its breakdown, quoting M6's per-PR line and count line. A row with a degraded `status` still exits 0, as today. The ledger and every recorded row stay valid, and a scheduled rerun without `--pr` skips those PRs and exits 0. So exit 1 means either an early stop or refusal, or a completed run with a missing breakdown. A run that stops early after such a row prints no count line, so its per-PR lines are the only record. The cause is a claude-config defect or, for the `shape` rule only, possibly malformed transcript data; if the same PR fails again on an updated claude-config, the transcript is the likely cause and re-capturing will not add the breakdown. The follow-up is the re-capture in the re-record contract, with its retention caveat.
- **Data (`:60`).** Amend "Never hand-edit this file" to name its two documented exceptions: blanking a `model_breakdown` cell that fails to parse, keeping its tab, and restoring a trailing tab an editor stripped. Each changes no cell's value. State that either edit is made only while no `--record` runs, and is confirmed by the next run parsing the file.
- **Re-record contract (`:82`).** Replace "byte-identical" with "every prior row's existing values are left unchanged; a row predating a column gains it as an empty cell when the file is next rewritten". Add the canonical re-capture caveat: `--record --force --pr N`, with the run's account flags, appends a row that carries the breakdown under the current `rate_stamp`, recomputes every scalar from the transcripts that survive, and becomes the PR's current row; under `--all-accounts` it does so in every account whose corpus touched the branch. Run it only while every session of that PR is still inside `cleanupPeriodDays`, because a PR whose early sessions have aged out is understated. Add one more: a forced re-capture whose breakdown fails the check appends an empty-cell row that supersedes a populated one; the prior row remains in the ledger history, and the fix is to re-force, within the caveat above, after the cause is fixed.
- **Columns (`:140-147`).** Qualify `:147` ("Every other column is byte-identical to the source ledger's own cell"): `model_breakdown` exports value-identical in canonical encoding, so a hand-reformatted source cell does not export byte-identical. Add M7's reasoning. The column is not tokenized because the rate table is committed to this public repo, and a PR's published `--summary` Cost block already names each priced model that PR used. Model keys are checked for shape only, so the exporting checkout does not vouch for them. The column is DO-NOT-PUBLISH because it rides an export row carrying the account dimension, and because of its variant labels. Point to "the 'The disclosed fields are not neutral' paragraph in `docs/transcript-analysis.md`'s `cost` section", the form `README.md:369` uses, not the `§` citation form (row 44).
- **Fingerprints (`:161`).** Extend the fingerprint sentence to cover the per-model mix and the variant mix. `fast` and `us_geo` record fast-mode and data-residency configuration facts.
- **Publication exception (`:163`).** Add that a per-model or per-variant split of an aggregate is its own figure and is asked about separately. The reason is that associating a set of PRs with a model or variant is non-neutral.
- **Legacy-header backfill (`:175`).** Add that `legacy_header_accounts` counts pre-host-header ledgers only.
- **Combining procedure (`:189`).** Add a clause that `model_breakdown` is never a required column: an export lacking it means every row is not recorded.
- **Stale pointers (`:9`, `:140`).** Fix them.
- **Headings.** Rename none, because citations resolve against them.

In `CHANGELOG.md`, add one entry under `[Unreleased]` → `### Changed`:
- **Body.** The new column. The upgrade on the first `--record` that writes a row, which has no supported downgrade, and its notice. This applies only with `pr_cost_recording` on, and `cost` dollars are unchanged. `--record` can now exit 1 after finishing every branch, when a row was recorded without its breakdown; the end-of-run line says so.
- **`**Migration:**` sub-bullets:**
  - Old checkouts print `missing or mismatched pr-cost ledger header row` on read mode, `--record`, and `pr-cost-export`.
  - Update every checkout that shares this ledger, including the checkout each cron job runs from, before the next `--record`. Do not delete or recreate the ledger.
  - Roll forward; do not revert. Older checkouts cannot read an upgraded ledger and there is no supported downgrade: update them, and the ledger stays intact. A checkout that cannot record loses each uncaptured PR once its transcripts age out. If a current checkout's recording is blocked by a defect, turn `pr_cost_recording` off until a fix lands.
  - `--record --force --pr N`, with the run's account flags, re-captures a PR, and the new row gains the breakdown. It recomputes the whole PR from surviving transcripts and becomes its current row, so use it only while every session of that PR is still inside `cleanupPeriodDays`. It is also the follow-up for a row recorded without its breakdown, once the cause is fixed.

## Critical files

Code (Dispatch 1):
- `claude/.claude/scripts/transcript_analysis/pr_cost_ledger.py`. Changes:
  - M1;
  - the M2 formatter branch;
  - the M3 codec;
  - M6's writer changes: the prior-file read and parse, the value postcondition and its two refusal strings, the staged-rewrite re-raise wording, and the `True`-on-upgrade return value.

  It adds only the stdlib imports `json` and `re`, no package module (row 27). Reuse `_format_pr_cost_ledger_row`'s `columns=` parameter and the existing re-parse in `_write_pr_cost_ledger_file`.
- `claude/.claude/scripts/transcript_analysis/pricing.py`: M4 and its four prescribed comments, one of them on the multiplier constants at `:40-44`. `_price_turn` stays untouched.
- `claude/.claude/scripts/transcript_analysis/cost.py`: M5 accumulation. Reuse `pricing._token_counts` and `pricing._pricing_variant`.
- `claude/.claude/scripts/transcript_analysis/pr_cost.py`. Changes:
  - `_usd_to_micros`;
  - `_new_pr_cost_row` writing the cell;
  - `_build_model_breakdown_cell`, the seam the degrade tests patch (M5);
  - `_ModelBreakdownCheckError`, its closed rule tuple with each rule's cause literal, and `_check_model_breakdown_cell`;
  - the `--record` loop's check call, degrade-and-continue path, upgrade notice, end-of-run count line, and exit (M6).

Tests (Dispatch 1):
- `claude/.claude/scripts/tests/_pr_cost_helpers.py`:
  - Add one ordered tuple of hand-written frozen header lines, pre-host then pre-model, as the single test-side source. Tests derive each width from its literal (`len(line.split("\t"))`), never from the live tuple or a bare 39 or 40, so the next column addition appends one entry.
  - `_sample_pr_cost_row` defaults `model_breakdown=None`, and its docstring's type list is updated.
  - `_legacy_row_line` keeps its name (decision 3) but formats through `columns=` built from the pre-host literal (row 15).
  - Add `_pre_model_row_line()`.
  - Add `_sample_model_breakdown(model="claude-sonnet-5")`, consistent with the sample row's scalars and written as a literal, not computed through `_usd_to_micros`.
  - Add one hand-written literal pre-host row line and one pre-model row line, each with distinct cell values.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_ledger.py`: header, codec, formatting, writer, and `--record` upgrade tests (Verification 3). Edit the golden at `:363-378` by hand to add the trailing empty cell. Fix `test_mismatched_header_raises` by dropping a middle column. Strengthen the upgrade test at `:274-303` and parametrize it over both legacy headers.
- `claude/.claude/scripts/tests/test_transcript_pr_cost.py`: accumulation, the producer check, the `opus_dollars` pin, the seeded sweep, the degrade policy, and the deny paths.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`: the `_pricing_variant` unit tests and the decision-4 consistency test, next to the multiplier pins at `:2265-2305`.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_export.py`: the triage set, the literal-ID passthrough, and export equivalence across a rewrite.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_export_accounts.py`: one new mixed-schema export test. Lines `:461` and `:474` stay untouched (decision 3).
- Existing `_write_pr_cost_ledger_file` test callers that rewrite an existing path must pass a superset of its rows, or the postcondition fails them. staff-sdet's round-2 scan of the 40-50 callers found them append-only, and `select-tests.py` confirms. New tests must not patch `Path.read_text` globally against an existing ledger, as `test_readback_mismatch_raises_without_publishing` does, because that also corrupts the writer's prior-file read.

Docs (Dispatch 2):
- `docs/pr-cost.md`: M8, including the `:60` amendment and the exit-1 sentence.
- `CHANGELOG.md`: M8.
- `docs/transcript-analysis-architecture.md`: edit only if Dispatch 1 added a package-module import to `pr_cost_ledger.py`. Otherwise `:271` stays true (row 27).
- `docs/transcript-analysis.md`: one sentence on the `pr-cost` `--record` bullet (`:1090`) saying it can exit 1 after a completed run, citing `docs/pr-cost.md` § "Row status" in the `` `target` § "Heading" `` form that `test_skills.py` checks. M8 lists this edit.

Plan: `.claude/plans/pr-cost-ledger-schema.md`.

Deliberately not edited:
- `pr_cost_export.py` (decision 3; row 7).
- `test_transcript_cli_bootstrap.py` (row 17).
- `test_transcript_pr_cost_gh.py` and `test_transcript_ledger_common.py`, which the `None` default covers.
- `test_transcript_workstream_cost.py`, which must pass unedited (row 56).
- `transcript-analysis.py` and `cache_rebuild_rules.py` (row 14).
- `claude/.claude/hooks/config-keys.psv` (row 48).

**Dispatch split.** Two `code-writer` dispatches, run in sequence.
1. **Dispatch 1: every code and test file above.** They don't split into non-overlapping sets. Between a format half and a producer half, `--record` would KeyError in the formatter on a row without `model_breakdown`, and the upgrade test spans both halves. Hand it M1's and M6's exact strings. Verify with `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root.
2. **Dispatch 2: the docs files above.** It runs after Dispatch 1 because it quotes the final constant names and M1's and M6's exact strings, which the session checks against Dispatch 1's code and hands it. It checks whether `pr_cost_ledger.py`'s package imports changed, and it owns the architecture-doc sentence if they did. Verify with the same command.

## Verification

1. From the worktree root, run `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. The path has four `..` segments because of row 28. All tests must pass.
2. Run `../../../../.venv/bin/ruff check` on every changed `.py` file, then `/code-review` on the diff.
3. These tests must exist and pass:
   - **Headers** (`test_transcript_pr_cost_ledger.py`):
     - The production pre-host and pre-model header lines equal the entries of the helper's ordered frozen-header tuple. The current header equals the tuple's last entry plus `\tmodel_breakdown`. Each failure message says "frozen; never re-derive from the live tuple", and the current-header message adds that the next column addition appends the outgoing header to that tuple.
     - Every current column missing from a recognized header has a default.
     - The literal pre-host and pre-model rows parse at parser level. By-name assertions check their distinct values, `host == "github.com"` for the pre-host row, and `model_breakdown is None`.
     - Width is checked per header, parametrized over the frozen tuple and the current header, with each width derived from its literal. A row one cell wider fails under each frozen header, and a row one cell short fails under the current header with M1's trailing-tab suffix. Each message names that header's width.
     - A header equal to the current one plus an extra column raises with "missing or mismatched" and the newer-version hint.
   - **Codec rejects.** Each case below raises `_PrCostLedgerParseError`. Its message names `line 2` and `model_breakdown` and omits a distinctive marker planted in the bad cell:
     - non-JSON, including a whitespace-only cell;
     - a duplicate key at each level;
     - a top-level `null`, list, number, or string;
     - a non-object below the top level;
     - an empty object at the model or variant level;
     - an empty-string key, an uppercase key, or a non-ASCII key (written `\u`-escaped);
     - a 65-character key (a 64-character key is accepted);
     - a missing leaf key or an extra leaf key;
     - a float, bool, negative, `NaN`, or `Infinity` leaf value.

     The `ValueError` and `RecursionError` mappings use real inputs, not a patched `json.loads`: `'{"a":' * 100000` raises `RecursionError`, and a leaf whose `tokens` is a 5000-digit literal raises `ValueError` (row 41). That test sets `sys.set_int_max_str_digits(4300)` and restores the prior value afterward, so a contributor's `PYTHONINTMAXSTRDIGITS` cannot change its outcome. The restore runs in a `finally` or fixture finalizer. Each codec-rejects case also asserts the raised exception's `__cause__` and `__context__` are `None` or marker-free.
   - **Codec accepts:**
     - `""` as `None`, and `{}` as `{}`;
     - a populated canonical cell;
     - a whitespace or unsorted-key variant, which decodes to the same value and re-formats canonically;
     - an unknown but well-formed variant label and class label;
     - a group missing one class;
     - a synthetic retired model key, `claude-test-retired`.

     Every `_MODEL_BASE_INPUT_RATES` key satisfies the key rule (parametrized). A hand-written literal of `_PRICING_VARIANTS` and one of `_TOKEN_CLASSES` each equal the live tuple, order included; the failure message says labels persist in ledger rows, so rename or remove none and add a new one to the literal. A hand-written golden ledger, with its own header line written inline rather than built from production constants, parses; its populated cell includes an unknown label and a retired synthetic key.
   - **Formatting:**
     - `test_default_columns_rendering_is_unchanged` stays a hand-written literal and gains the trailing empty cell.
     - A second hand-written golden pins one populated cell's canonical bytes at cell level, not as a whole row. Every other test compares cells via `json.loads`.
     - The two-argument `_parse_pr_cost_ledger_row_cells` round-trips a populated row.
     - A pre-model line formats as its original cells plus one empty cell.
     - `csv.reader(delimiter="\t")` yields the same cells as `split("\t")` for a populated row and for an empty-last-cell row.
   - **Writer:**
     - The value postcondition refuses in two cases: a monkeypatched encoder that drops a value, and a call that drops or reorders a prior row. Each raises `_PrCostLedgerParseError` with M6's exact string. In both cases the ledger's bytes and mtime are unchanged and no `.pr-cost-ledger-*.tmp` sibling remains.
     - A hand-edited `1.5`, a reformatted JSON cell, a seven-decimal `0.1234567`, and `-1e-7` all pass. They re-render as `1.500000`, the canonical cell, `0.123457`, and `-0.000000`.
     - One test drives the six-decimal comparison through the writer: a prior ledger whose float cells are hand-edited to `-1e-7`, `0.0078125`, `2.5e-06`, `5e-07`, `1.5e-06`, and `1e300` is rewritten without refusal, and each rewritten cell equals `f"{x:.6f}"`.
     - Calling the writer on a malformed existing file raises M6's "changed during this run" wording and leaves the bytes unchanged.
     - An `OSError` other than `FileNotFoundError` from the writer's prior-file read, patched for that one path only, propagates unchanged and leaves no `.pr-cost-ledger-*.tmp` sibling.
     - A structurally invalid cell handed to the writer raises with the staged-rewrite wording, and the existing ledger stays byte-unchanged. With no existing ledger, the path still does not exist afterward.
     - The writer returns `True` on a pre-model and on a pre-host upgrade, and `False` on a current-header write and on creation.
     - A `--record` run that records two branches into a pre-model ledger prints the notice exactly once. An `--all-accounts` run over two upgraded ledgers prints it once per ledger, each naming its `account-N`. A refused write and the four deny-path runs print none. Assertions match a fixed fragment, not the whole string. These runs, the degrade-policy runs, and the refusal runs also plant a distinctive marker in the ledger's directory name and in a prior row's string cell, and assert it appears nowhere in captured stderr.
     - Mode is preserved across the upgrade for a 0600 file and a 0644 file.
   - **`--record` upgrade** (integration, parametrized over pre-host and pre-model):
     - Seed at least three hand-written legacy rows. Between them they cover:
       - a same-key supersede chain;
       - a non-`github.com` host (pre-model only);
       - a `degraded_network` status;
       - a non-hex legacy `machine`;
       - an empty `supersedes`;
       - all-false bools;
       - zero floats;
       - non-default scalars.
     - After a real `_pr_cost_report` `--record`, assert the full written data lines. Each old line is its original cells, with `github.com` prepended for pre-host, plus one empty cell, in original order. Then comes the new row, whose cell passes `_check_model_breakdown_cell`.
   - **Producer** (`test_transcript_pr_cost.py`):
     - **Exact `usd_micros` literals,** at the unit layer (`_compute_pr_cost_branch_totals`, then `_new_pr_cost_row`), one branch per fixture. A precondition asserts `_model_rates("claude-opus-5-5")["cache_read"] == 0.2` and `_model_rates("claude-opus-5")["cache_read"] == 0.5`, with a message saying the literals assume those rates. The first three use `claude-opus-5-5` cache reads at $0.20/MTok. Compute each literal by hand, not via `_price_turn`:
       - 3 tokens gives 1, which is round, not floor;
       - 2 tokens gives 0, which is round, not ceil;
       - ten turns of 2 tokens give 4, which is per-leaf rounding, not per-turn;
       - `claude-opus-5` with 5 cache-read tokens renders `0.000003`, so `usd_micros` is 3 where builtin `round(d * 1e6)` gives 2, which pins the `.6f` semantics. For this N = 1 row, the scalar cell the production formatter renders, read as a `Decimal` times 10^6, equals the leaf `usd_micros`.
     - **Accumulation.** Assert the whole cell against a hand-written literal for a fixture of two priced models across all four variants, plus a sidechain turn on the second model and a zero-token priced turn, whose zero-valued group passes the check. A synthetic `claude-test-unpriced` turn is absent from the cell and its tokens land in `unpriced_tokens`; after a write, that string appears nowhere in the ledger bytes or captured stderr. An unpriced-only branch yields `{}` and passes the check.
     - **Check boundary pairs, in both signs.** Hand-built pairs: for N = 1 / 2 / 3, the check accepts at 0 / 1 / 2 and rejects at 1 / 2 / 3. Token sums require exact equality. Real-token fixtures at N = 2, `claude-opus-5-5` standard plus `us_geo`: 2 cache-read tokens each gives leaves 0 + 0 against a scalar of 1, and 3 each gives leaves 1 + 1 against 1; both pass. An N = 3 pass at a gap of 2 uses three hand-built leaves of `0.0078125` (scalar `0.0234375`) through `_new_pr_cost_row` and the check.
     - **Check rejects,** each from a fixture that violates exactly one rule and each asserting that specific rule, since the check reports only the first failure:
       - `{}` with a nonzero dollar scalar, and `{}` with a nonzero token scalar;
       - the model key `claude-test-unpriced`, which is not in the table;
       - an unknown variant label;
       - a group missing a class;
       - a negative leaf `tokens`, which the decoder rejects: the rule is `shape`, and no `_PrCostLedgerParseError` escapes;
       - at N = 3, one class's token sum off by one while its dollars sit inside tolerance: the rule is `tokens`, so a token check that borrowed the dollar tolerance fails.
     - **No echo.** A lowercase marker that passes the key rule, planted in a model key, a variant key, and a class key in turn, makes the real check fire, and the marker is absent from the exception text.
     - **`opus_dollars` pin.** On a fixture with `claude-opus-5-5`, `claude-opus-5`, and `claude-fable-5`, `opus_dollars` matches the opus-family sum of the accumulator's float leaf dollars, not the cell's `usd_micros`, within `pytest.approx(rel=1e-12)`, and the Fable leaves are excluded.
     - **Seeded sweep.** Many small seeded fixtures at N = 2 to 4 run through `_compute_pr_cost_branch_totals`, `_new_pr_cost_row`, `_check_model_breakdown_cell`, and `_write_pr_cost_ledger_file`. The generator keeps its own expected token totals per (model, variant, class) from the turns it generates, and each fixture asserts the check passes and every leaf's `tokens` equals that independent total.
     - **Degrade policy** (`--record` runs):
       - The check, monkeypatched to fail for one of two branches: both rows are written and the failing row's cell is empty; stderr carries M6's per-PR line, naming the rule and the PR with no cell text, and M6's count line; the run raises `SystemExit(1)` after both branches; the ledger parses.
       - The real check, fed by `_build_model_breakdown_cell` monkeypatched to return the planted marker as a model key: the row degrades with the `model-membership` rule and the marker is absent from stderr.
       - A real decoder rejection, from a transcript turn with a negative `cache_read_input_tokens`: the row degrades instead of aborting the run, and its per-PR line carries the `shape` rule's transcript-data cause.
       - `_build_model_breakdown_cell` monkeypatched to raise `RuntimeError` on the first of two branches: the run aborts, the second branch is not recorded, and the seeded ledger's bytes are unchanged (M5).
       - `--all-accounts` over two accounts where the first degrades: the second account still records, the summary line prints, the count line is the last stderr line and names `account-1`, and the run raises `SystemExit(1)`.
       - A `--force` re-capture whose check fails over a row with a populated cell: an empty-cell row is appended with `supersedes` set, and the populated row is still in the file.
       - After a degrade, `--record --force --pr N` with the check passing appends a populated-cell row whose `supersedes` is the degraded row's `captured_at`, and exits 0.
       - After a degrade, an unforced `--record` without `--pr` skips the PR, prints no count line, and exits 0.
       - A degrade on the first of three branches, then a refused write on the second (`_write_pr_cost_ledger_file` patched to raise `_PrCostLedgerParseError` on its second call): the run raises `SystemExit(1)`, stderr carries the first branch's per-PR line and no count line, the third branch is not recorded, and the ledger holds the first branch's row.
     - **Count line and cause table.** One unit test pins the closed rule tuple to exactly the five rules, each mapped to its cause literal, with only `shape` carrying the transcript-data cause. A run with two degraded PRs in one account reads `2 row(s)` with `PR #<a>, PR #<b>` in processing order and no `account-` prefix. An `--all-accounts` run with a degrade in both accounts reads `account-1 PR #<a>, account-2 PR #<b>`.
     - **Per-PR line follows the write.** A branch whose check fails (patched) and whose write is also refused (`_write_pr_cost_ledger_file` raising `_PrCostLedgerParseError` for that same branch): the run raises `SystemExit(1)`, stderr carries the refusal text, no `per-model breakdown failed` fragment and no count line, and the ledger bytes are unchanged.
     - **Disabled producer.** `_build_model_breakdown_cell` patched to return `None` on a current-header ledger that already holds populated rows: the row is recorded with an empty cell, prior populated rows are unchanged, the ledger parses, no degrade line prints, and the run exits 0.
     - **Output scope.** Read mode and export console output over a ledger holding a populated cell with a distinctive, key-rule-valid model key never contain that key (the export file itself does). The marker assertions in the notice, degrade-policy and refusal runs span stdout plus stderr, not stderr alone.
     - **Prescribed hand edits.** A current-header ledger with (a) a corrupt populated cell and (b) a row whose trailing tab is stripped: read mode, `--record` and export each exit non-zero with `line N` and `model_breakdown` (or the width text), no cell text, and the bytes unchanged. After the prescribed edit, all three succeed and `--record` writes a row.
     - **Deny paths.** Six runs leave a pre-model file's bytes unchanged and no `.tmp` sibling: an unconsented `--record`, a consented `--record` that only skips (already captured), read mode, export, a `--record` refused by the git-tracked-path check, and an `--all-accounts` account that is not opted in. The upgrade run asserts its new row equals the hand-computed cell literal, exits 0, and prints no per-PR or count line.
     - **Unrecognized header.** Read mode, `--record`, and export exit non-zero, and the file's bytes and mtime are unchanged.
   - **Pricing variant** (`test_transcript_analysis.py`):
     - `_pricing_variant` returns the right label for all four combinations and for these near-misses:
       - `speed` of `"standard"`, `None`, `"FAST"`, or absent;
       - `inference_geo` of `"global"`, `"US"`, `""`, or absent.
     - **The consistency test (decision 4).** It is parametrized over all of those usages and uses a fractional-cent token mix. For every class, `_price_turn(model, usage)` dollars `==` the standard-usage dollars times the multipliers the returned label implies. The multipliers apply in `_price_turn`'s order: `_FAST_MODE_RATE_MULTIPLIER`, then `_INFERENCE_GEO_US_RATE_MULTIPLIER`. It covers only the two multiplier conditions that exist today (M4).
   - **Export:**
     - The triage test passes with `model_bearing_row_level`.
     - A source cell keyed by a literal synthetic model ID exports byte-identical.
     - In the mixed-schema export (pre-host, pre-model, and current accounts), `_parse_pr_cost_export_provenance_line(...)["legacy_header_accounts"] == "1"`, so the pre-model account counts 0. The current account's cell equals its source, and the older accounts' cells are empty.
     - Exporting before and after a no-new-row rewrite (`_write_pr_cost_ledger_file(path, parse(old_text))`) yields identical data rows and the same `corpus=` digest.
   - **Unchanged existing tests:**
     - `test_transcript_cli_bootstrap.py`'s subprocess export passes unedited and now exercises the pre-model header (row 17).
     - `test_transcript_workstream_cost.py` passes unedited (row 56).
     - The multiplier pins at `test_transcript_analysis.py:2265-2305` pass unedited. They use `pytest.approx`, so no bit-identity is claimed from them. Item 4 is the evidence that `_price_turn` is unchanged.
4. **Unchanged code.** `git diff origin/main` shows:
   - no change inside `_price_turn`'s body;
   - no change to `pr_cost_ledger.py`'s `from transcript_analysis import` line;
   - no diff in `pr_cost_export.py`.
5. **Docs.**
   - No `docs/pr-cost.md` heading is renamed, and the citation tests run through `select-tests.py`'s `docs/` mapping.
   - A manual grep (not a pytest assertion) confirms that "byte-identical" no longer appears in "The re-record contract" paragraph.
   - No new `§` citation targets the run-in paragraph (row 44).
6. **PR body.** It links the issue as `Refs #1239`, never a closing keyword, because the issue stays open for the combiner. It carries:
   - a "Departures from #1239" section that:
     - quotes verbatim the Acceptance bullet beginning "Mixed-schema fixtures pool pre- and post-migration rows together", re-read with `gh issue view 1239` when the body is written;
     - lists the departures: one `model_breakdown` cell instead of separate columns; "models used" derived from the cell's keys, covering priced models only, with a nonzero `unpriced_turns` flagging excluded ones; integer micro-dollars; the pricing-variant level; and reliance on the in-window `--record --force --pr N` re-capture, which the issue's Out of scope calls an irreversible append that understates aged-out PRs, so the plan advises it only while every session of the PR survives;
     - names the three places a legacy row differs from "legacy rows never gain the new columns": the upgraded ledger gives it an empty cell; a new-code export carries that empty cell; an old-checkout export lacks the column entirely, so the combiner treats the column as optional;
     - states the intent that is met: legacy rows get no per-model values and are never assigned a derived or estimated split;
     - states the lockout: new code reads old ledgers, old code refuses an upgraded one, and there is no supported downgrade (Rollback's canonical recovery statement);
     - carries this replacement Acceptance wording, ready to paste as an issue comment: "Mixed-schema fixtures pool rows from pre-change exports (no `model_breakdown` column), exports from this version, where legacy rows carry an empty `model_breakdown`, and populated rows. Rows without a recorded breakdown pool on their scalar columns only and are never assigned a derived or estimated per-model split. "Models used" covers priced models only; a nonzero `unpriced_turns` flags excluded ones. How per-model figures are reported, and whether any is published, is left to the combiner's plan and `docs/pr-cost.md`'s per-figure publication rule."
   - one unconditional line saying that after this merges, roll forward and never revert, and that a full revert removes the frozen tuple's comment and the CHANGELOG entry, so this line is the surviving warning;
   - the exit-1 change for scheduled `--record` callers.
7. **No real data.** No step reads a real `pr-cost-ledger.tsv` or a real export inside the session. Every check uses synthetic fixtures (`docs/pr-cost.md:177`).

## Out of scope

- **Later plans named in the Ask (row 1).** The pricing-history module, the era direction-word definition in `docs/pr-cost.md`, recapture retirement, and the combining subcommand.
- **Backfilling per-model values into existing rows,** other than the existing in-window `--force --pr N` recapture an operator chooses to run (G1; rows 25, 45).
- **A standalone migrator, downgrade tool or command, or backup copy** (D4; row 36). No downgrade command is documented or shipped. If an operator need appears, file an issue.
- **Collapsing the other four multiplier-condition copies onto `_pricing_variant`** (row 14). They predate this change, and decision 4 keeps `_price_turn` untouched. The session proposes a follow-up issue to the engineer instead of leaving it as a note.
- **Export-side flagging or withholding of model keys the exporting checkout doesn't recognize.**
  - M5's write-time membership check and the table rule already close the cooperative-writer path.
  - An exporter on an older or newer checkout legitimately sees keys outside its own table, so a count would flag correct data.
  - A new provenance field would change the field set pinned at `test_transcript_pr_cost_export_accounts.py:506-509`.
  - Retired-ID handling belongs to the pricing-history plan.
- **Reading an undecodable cell as "not recorded".** Both sides of M6's value check would then parse to `None`, so the next `--record` would erase the cell unchecked. The cell fails loud instead, and blanking it is the documented recovery (M3).
- **Parse-time label membership** (M3 option b).
- **Extending the `pr_cost_recording` description at `claude/.claude/hooks/config-keys.psv:96`** (row 48).
- **Rewording "Comparing rows across rate stamps."** The re-pricing procedure belongs to the pricing-history and combining plans.
- **An export provenance count of rows lacking `model_breakdown`.** A combiner can count empty cells itself.
- **A shipped command that checks whether the latest row carries a breakdown, and a bulk repair for trailing tabs an editor stripped.** Each would be one more operator shell command to test and keep in step with the header width. M6's stderr lines already report a missing breakdown, and the width error names each stripped line.
- **Changes to `--force`, `supersedes`, latest-row selection, or `_PRICING_FETCH_DATE`.**
- **The `_LEGACY_` to `_PRE_HOST_` rename** (decision 3) **and any edit to `pr_cost_export.py`** (row 7; decision 3 covers only the rename).
- **Any edit to `_price_turn`** (decision 4). The pointer for a future multiplier condition sits on the multiplier constants instead (M4).
- **A distinct exit code for a run that recorded a row without its breakdown.** Exit 1 plus the count line is the signal; a new code would add a documented contract for a path that fires only on a defect (M6).
- **Containing an untyped cell-construction defect per PR.** It aborts the run like any other defect in that code path (M5).
- **Editing GitHub issue #1239's text.** It is not a repository file. The PR body carries the disclosure (Verification 6), and the session can propose an issue comment with revised Acceptance wording to the engineer separately.
- **`README.md:522`'s "exactly three levels deep" claim, which a slashed branch name breaks.** Observed while planning; unrelated.
- **`docs/transcript-analysis.md:671`.** It is cited, not edited. The file's only edit is one sentence on the `pr-cost` `--record` bullet (Critical files).
- **Preserved records** (`docs/case-studies/*`, `docs/reports/*`, merged plans).

## Decisions

Resolved by the engineer:
- **Decisions 1-4** (rows 35-38): "Your recommendations make sense to me". D5 resolves through decision 2 (row 36).
- **Postcondition scope** (row 36): "(c) Every write, floats at 6 decimals (Recommended)". Listed for information; it is not open.
- **D1, append the column to the existing ledger** (row 29), **D2, one JSON cell** (row 30), and **D3, the pricing-variant level** (row 31): "Confirm D1, D2, D3 (Recommended)". D1 upgrades the ledger in place on the next consented `--record`, as the `host` column did. The alternative was a long-format sidecar file. It would leave the 39-column ledger untouched, so no older checkout is locked out. It costs a join key, a second sensitive file at rest that needs its own consent gate, lock, mode, and git-tracked-path handling, and an edit to `pr_cost_export.py`. The lockout reaches only an older checkout reading the same ledger (G3), which fails loud without writing and recovers by updating.
- **Write-time failure policy** (row 50): "Degrade always, exit 1 (Recommended)". A failing breakdown check records the row with an empty cell every time, including a `--force` re-capture over a populated row, and `--record` then exits 1 after finishing every branch. Earlier the engineer questioned a skip carve-out: "Skip that PR? That doesn’t seem right. Can you have fable check?"

- **No downgrade command:** "Accept the drop (Recommended)", after the engineer's "Ask fable". Fable's read-only check recommended dropping the command, and round 4's staff-backend-engineer review concurred. plan-architect preferred a copy-less temp-and-rename version. Rollback is roll-forward only, per Rollback's canonical recovery statement; the only hand edits are the two cell-level ones in M8.

Delegated by the engineer, outcome not their own words:
- **D4, no backup copy** (row 32) stands literally, since no tool or documented command makes a copy. It rests on decision 2's text "No backup copy (D4 stands)".
- **Remaining round-3 concerns.** The engineer answered "Ask the architect"; this round's edits apply the architect's list, minus the downgrade-command edits.

Dispatch 1 waits for the engineer to see this final plan, because rollback is forward-only once any consumer upgrades.

For the engineer at plan presentation:
- Round 4 of `/plan-review` returned Approve with concerns from all five reviewers. Its changes are applied; whether to run a fifth round or commit now.
- D1's sidecar alternative above says it needs "its own consent gate, lock, mode, and git-tracked-path handling". staff-data-engineer's round-4 read of `pr_cost.py:464-554, :595` finds a sidecar written in the same `--record` branch would share the gate, lock and git-tracked refusal; its real extra costs are 0600 creation, the join key, a second file at rest, and the export edit (M2 option c). D1 was confirmed on the earlier wording; reconfirm it on the shorter list if you want.
- A re-capture caveat an operator can check: the plan says to re-capture only while every session of the PR is inside `cleanupPeriodDays`, but nothing the tool prints shows that. staff-product-engineer suggests a proxy such as "the branch's first commit is within `cleanupPeriodDays`", and aligning the existing "Recapture later" degraded-status bullets. Not applied; your call.
- Whether to file the follow-up issue for the four remaining multiplier-condition copies (Out of scope).
