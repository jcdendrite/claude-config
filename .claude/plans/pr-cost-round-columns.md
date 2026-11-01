# Round columns in the pr-cost ledger

## Context

Goal: record each PR's code-review, plan-review and ready-for-review rounds, with per-round cost and tokens, in the `pr-cost` ledger at capture time, so the data survives the 30-day transcript retention window.

Ask: GH-1364 piece 2, "Round columns in the `pr-cost` ledger. Record code-review round count and per-round cost at capture time, so the data outlives the 30-day transcript retention window." (issue #1364, Plan item 2). Engineer words this session, in order:
- "move to phase 2 with plan-it"
- "I think add per round tokens as well. What does the analytics engineer think" — widens the Ask to per-round tokens.
- Shape: "Header + rule stamp (Recommended)".
- Force guard: "Add rounds-only guard (Recommended)".
- Scope: "Record only (Recommended)".
- Review skills: "why shouldn’t it do the other review skills as well" — widens the Ask from code-review to every review skill.
- Malformed usage in the round pass: "Add skip-and-count guard (Recommended)".
- Export (answering a reviewer's proposal to omit the column): "I would say why not export it’s easier to do now then changing the schema later".
- Backfill: "I would keep back full entirely out of scope".
- Reporting the malformed-usage exclusion in `review-round-cost` and `review-cost-by-size`: "Print a footer line (Recommended)".
- Bounding the cell: "Writer check + decoder backstop (Recommended)".

Why now: the transcript scan behind `review-cost-by-size` (merged, piece 1) cannot see sessions older than `cleanupPeriodDays`, so each day without a captured copy loses round history for good. Piece 4's diff-size threshold needs that history.

Intended outcome: `transcript-analysis.py pr-cost --record` writes one appended JSON column holding the ordered per-round data. An empty cell means "not captured". Reading that column back from `review-cost-by-size` is a separate, later decision.

Decisions from the design consults (analytics engineer, data engineer, architect review), each the session's relay of that reviewer's recommendation unless it carries an engineer quote:
- One trailing JSON column, `review_rounds`, not scalar columns: scalars cannot hold the ordered series the within-PR trend needs, and the ledger's int and float parsers cannot hold an empty cell.
- All three review skills, keyed by skill inside the cell. Capture costs the same for all of them, and widening after transcripts expire is impossible.
- No in-cell schema version. The frozen TSV header versions structure. Readers tolerate added leaf fields. An integer `rule` stamp, which never blocks parsing, versions round detection and attribution.
- Leaf fields: main and agent micro-dollars, `agents`, `unpriced_turns`, `dangling`, `after_merge`, and per-round tokens per thread in the `model_breakdown` model, variant, class nesting, plus `unpriced_tokens`. No timestamps.
- Empty cell means not captured. All-skills-empty-lists means captured with zero rounds.
- The `--force` re-record gets a guard that protects captured round data.
- A failed round pass writes the row with an empty cell, prints the exception class name only, and exits 1 at the end of the run.

Public repo: the plan and docs name no figure from issue #1364's wider-corpus analysis. Only bucket bounds and the direction "rising with PR size" may appear anywhere.

## Approach

**Design.** `pr-cost --record` appends one trailing JSON column, `review_rounds`. Its cell holds an integer `rule` stamp and, keyed by every review skill, that PR's rounds in round order: each round carries main-thread and subagent micro-dollars, a per-model token split for each thread, and completeness counts. The frozen TSV header versions the cell's structure, the `rule` stamp versions round detection and attribution, and an empty cell always means "not captured".

The rounds come from a second per-account transcript pass through `review_rounds.compute_review_round_costs`, which prices only sessions that open a round. It runs at most once per account, never under the ledger lock, and only when a row may be written. A failure of that pass, or of the cell's build or write-time check, costs only this cell. A `--force` re-capture that would replace captured rounds with none, fewer or less complete ones is refused. The export carries the column, emitting only vocabulary the exporting checkout knows. `review-round-cost` and `review-cost-by-size` print a `MALFORMED USAGE` line for the turns the round pass now excludes.

**Scope check.** Each inclusion beyond the column itself, and what puts it here:
- **Engineer-chosen.**
  - Per-round tokens (row 2), every review skill (row 36), and the `--force` guard (row 4).
  - The malformed-usage skip-and-count in the round pass (row 37), and the footer lines that report it (row 38).
  - The export column (row 39), and the two-layer cell bound (row 41).
- **Forced by a choice above, or by a check that fails without it.**
  - `_usage_is_invalid` moves to `session_pricing.py`. `review_rounds.py` may not import `cost.py`, and a second copy would give one predicate two homes (rows 13, 47).
  - `select-tests.py` lists `session_pricing.py` as a review_bench dependency, because a ground-truth test fails on the new import otherwise (row 42).
  - The export vocabulary filter. The export decision carries un-tokenized keys that the tolerant decoder admits from any writer (row 40).
  - The `rule` golden test. The engineer's schema-drift concern (row 3) needs a tripwire for a forgotten bump.
  - Export triage, docs and the CHANGELOG entries. The triage test fails without the first (row 10). The others are the only channels for the older-checkout refusal, the exit-1 recovery and the figure change (G2).
- **This plan's judgment.** `after_merge` (row 43), the round pass's skip of sessions that open no round (row 51), and the export's dropped-keys stderr line (row 40).

The malformed-usage guard is not framed as forced. It covers the one field the scalar pass already skips and counts (row 13). A record outside the transcript format, in a session that opens a round, still empties the account's `review_rounds` cells for that run (row 45).

**Who this reaches.**
- **Consumers with `pr_cost_recording` on.**
  - The next `--record` that writes a row rewrites the ledger under the new header and prints the existing upgrade notice. Older checkouts then refuse that ledger on read mode, `--record`, and export (G2).
  - A `--record` run that writes at least one row prints a start line and scans the transcript corpus a second time, pricing only sessions that open a round (row 26).
  - `--record` can exit 1 after a completed run for a row recorded without its rounds. A `--force` re-capture can now be refused, and that refusal exits 1 under `--all-accounts` too.
  - The end-of-run count line's stem changes from `row(s) recorded without a per-model breakdown` to `row(s) recorded with failed cells`.
  - New at-rest data: per-PR review-round series with dollars, tokens, priced model IDs and variant labels, in the same file, created 0600.
- **Export consumers.** A trailing `review_rounds` column, a `review_rounds_dropped_keys` key in the provenance line, and one stderr line when that count is nonzero.
- **Every `review-round-cost` and `review-cost-by-size` user, recording on or off.** A turn whose usage fails the malformed-usage check is now excluded from round and branch dollars instead of raising or pricing negative, and a nonzero in-window count prints one `MALFORMED USAGE` line. Every other figure is unchanged to display precision. Both commands read round dicts by key, so the new keys are additive (row 15). The shared dispatch tally adds nested turns in a different float order (M1), which can move a dollar figure at its last binary digit.
- **`cost`, `period-cost`, and `workstream-cost` users.** No change: `_usage_is_invalid` moves verbatim.

**Schema versioning, adversarial pass.** The engineer asked whether the cell needs a schema version, since a JSON cell is schema-on-read (row 3). Each way such a column drifts, and what stops it:
- **Incompatible structure change.** The required keys at every level are fixed for the column's life. Any change to them ships as a new appended column under a new frozen header, and every checkout that predates that header refuses the file loudly (G2). The header line is the structural version. It is checked on every read, and no reader can ignore it.
- **Additive field.** A round may gain a key-shaped name holding an integer. Older readers accept it. The decoder returns the whole object and the write postcondition compares decoded values (row 9), so an older checkout's rewrite preserves the field. Presence means a writer that knew the field recorded it; absence means unknown, never zero.
- **Same shape, new meaning.** `rule` is bumped by any change to which records open a round, where a window ends, which branch a round goes to, or which turns and dispatches it prices. Readers compare rounds across rows only at one `rule` value. A golden test pins detection output for each round shape in an append-only map keyed by rule, so a behavior change a shape exercises fails until a new rule's entry lands and the constant is bumped (M1). It is a tripwire, not mechanical enforcement: rewriting an entry in place still passes. A change no shape exercises relies on the constant's comment.
- **Pricing change.** The row's `rate_stamp` and the per-round token splits cover it, not `rule`. Per-round dollars are as-priced under that stamp, and the tokens re-price under any other.
- **Skill-set change.** A skill absent from `rounds` was not tracked by that writer, while a present empty list is a tracked zero. Adding a skill changes which records open a round, so it bumps `rule`.
- **Downgrade.** An older-rule checkout re-capturing over a newer-rule row is refused (M5).
- **Malformed or oversized cell.** It refuses the whole ledger with a fixed rule text naming the column, never cell text, and blanking the cell is the recovery, as for `model_breakdown`. The decoder's length backstop sits far above the write bound, so raising the write bound, up to the lowest read bound any released checkout carries, needs no header change (M3).

An in-cell `v` adds nothing to this. A `v` that refuses unknown values turns every row a newer writer produced into a whole-ledger refusal, whose documented recovery blanks valid data. A `v` that accepts unknown values gives a reader no rule it can act on beyond what the header, the required-key contract and `rule` already give.

**Alternatives set aside.**
- **Scalar columns** (a count and a total per skill). They cannot hold the ordered per-round series, every new skill or field needs a new header, and the int and float parsers have no empty "not captured" value.
- **Delimited lists in scalar cells.** A one-off mini-format reimplementing what stdlib `json` provides.
- **A round-grain sidecar file.** Two files cannot be swapped atomically, and the sidecar needs a second lock, its own header lifecycle, 0600 handling, and an export path.
- **Raising `cleanupPeriodDays`.** It keeps conversation content longer for every stow consumer to avoid a capture step.
- **A strict round, as `model_breakdown` has.** Every additive per-round field would become a new column plus a header bump that locks out every older checkout. The tolerant round admits integer additions only, so the parse stays structural.
- **Per-class `usd_micros` in the round.** Tokens plus `rate_stamp` re-price a round, and the two thread totals carry the as-priced figure.
- **Timestamps in the round.** The export truncates dates to remove a timing handle, and per-round times would restore it. `after_merge` keeps the one timing fact a later reader needs.
- **A re-pricing write-time check** (re-priced tokens must equal the dollars). Re-pricing from a variant label needs another copy of the multiplier conditions that `pricing.py:45-46` already warns about. It would guard against tokens and dollars taken from different turns, which accumulating both from the same `_price_turn` result rules out (row 34). The check keeps size, shape and membership only, and unit tests pin the accumulation.
- **One transcript pass for both scalars and rounds.** The scalar pass merges subagent records, which `compute_review_round_costs` must not see (row 11). Filtering them back out re-derives what `include_subagents=False` already does.
- **An eager round pass.** Every scheduled `--record` would pay a second scan even when nothing is uncaptured.
- **Carrying the prior cell forward on `--force`.** It would put micro-dollars priced under the prior `rate_stamp` into a row stamped with the current one.
- **Skipping the row when the round pass fails**, so a scheduled rerun retries it. A deterministic failure, such as one record outside the transcript format, would then block every scalar row in the account until that session ages out, losing the data the ledger exists for.
- **Treating a non-string timestamp as absent in `corpus._parse_iso_datetime`.** Its raise is every reader's documented contract, pinned by `test_transcript_session_pricing.py:1232-1249`, and changing it is the decode-policy decision `corpus.py:45-46` defers (row 45).
- **Bounding the round pass to in-window dispatches inside a session that opens a round.** A skipped dispatch also skips its `visited` entry, so a colliding `toolUseId` would price into a round that `review-round-cost` prices at zero, and the cell would stop matching piece 1's figures (`review_rounds.py:265-268`). Skipping a whole session that opens no round is exact, because `visited` and `dispatch_index` never cross sessions, so M1 adopts that instead (row 51).
- **Scanning under the ledger lock.** A scan can outlast the lock timeout, after which a concurrent `--record` exits 1 (row 49). M4 takes the existing already-captured skip before the lock instead, so the scan's memo is filled whenever the locked path reads it.
- **A write bound at a spreadsheet's per-cell character limit.** It would degrade the heaviest PRs' cells, the cohort the threshold analysis needs, permanently once their transcripts expire. Export readers already need a TSV parser (`docs/pr-cost.md:263`).
- **A round-count bound instead of a length bound.** A count stops bounding a round's length once additive fields arrive, and length is what the parse and the export carry.
- **A pooled `--show-withheld` share for malformed usage.** It would change the publication pointer's instructions to the owner (`review_rounds.py:534-538`) and the `--show-withheld` contract, a publication-governance surface. The pooled coverage list documents the exclusion instead (M7).
- **Blanking the whole exported cell on unknown vocabulary.** One additive field from a newer writer would blank every row that writer recorded. Dropping only the unknown keys keeps the rest.
- **A subprocess `--record` smoke test.** `test_transcript_cli_bootstrap.py` has no `gh` fake on `PATH` (`:647`). The new imports are module-level (`transcript-analysis.py:97`), so `test_transcript_analysis_pr_cost_help_exits_zero` already loads `pr_cost`, `review_rounds` and `session_pricing` in a fresh interpreter.
- **A containment boundary around the `--force` guard.** It reads only cells the decoder and the write-time check already validated, so its truth-table tests leave nothing for a second layer to catch.

**Rollback (forward-only).** Before any consumer's first write by post-change code, `git revert` is clean. After it, reverted code refuses the upgraded ledger on every path (G2), so a defect fix keeps the header and codec.
- **Emergency lever.** A `_build_review_rounds_cell` that returns `None` records each new row with an empty cell, prints no degrade line, and exits 0. While it is in place, the guard refuses `--force` over a captured cell (`this checkout does not record review rounds`), so the captured series stays the current row.
- **No round-pass opt-out and no downgrade recipe** (row 46).
  - Containment already limits a pass defect to the cell. What remains is run time and exit 1, and the existing `pr_cost_recording` off switch covers a defect that writes wrong data (`docs/pr-cost.md:111`).
  - A per-pass key would add a `config-keys.psv` row, a hook-reviewed surface, for one optional pass.
  - A downgrade recipe is a hand edit that drops a column, which `docs/pr-cost.md:145` bars outside its two named edits.

**Root problem.** A PR's review-round history (round count, order, per-round cost and tokens) exists only in transcripts that expire (G1), and the ledger has no column that retains it, so `review-cost-by-size` and any later threshold analysis lose every round older than the retention window.

**Threat.** The change can fail five ways, each reaching every stow consumer whose `--record` or export runs after a pull:
- It silently drops, shifts or misreads an existing row, the sole surviving record once transcripts age out.
- A defect in the new pass or cell costs a PR its scalar row, or costs the run its remaining branches.
- A `--force` re-capture silently replaces captured rounds with fewer or less complete ones, after the transcripts behind them have aged out.
- Rows recorded under different round semantics mix with no discriminator, so a later reader trends incomparable series.
- A per-round series reaches a reader it should not: the export carries keys the exporting checkout cannot vouch for, or a figure derived from the column is published as an exempt own-history count.

**Givens:**
- **G1.** Round data for a PR can be captured only while its sessions survive `cleanupPeriodDays` (default 30 days). Reason: Claude Code's transcript retention is vendor-defaulted and operator-owned (`docs/pr-cost.md:3`).
- **G2.** Checkouts already on disk recognize only their own headers and refuse a newer one, naming the update remedy. Reason: deployed code is out of this plan's reach, and each operator decides when to pull (`pr_cost_ledger.py:309-322`, `:346-348`).
- **G3.** Round-opening shapes, `toolUseId` joins and usage fields are written by Claude Code. Reason: the transcript format is vendor-owned (`review_rounds.py:126-160` reads it).
- **G4.** Export rows are row-level DO-NOT-PUBLISH data. Reason: this is the owner's redaction policy (`CLAUDE.md` "Redact private-project-identifying content"; `docs/pr-cost.md:290`).

**Assumption ledger.**

| # | Assumption | Tag |
|---|---|---|
| 1 | Record only: no report reads `review_rounds`, and `review-cost-by-size` does not read the ledger. The cell's only readers are the export, through its filter and triage test, and the `--force` guard, which decodes the prior row's cell. | `[engineer-verified: "Record only (Recommended)"]` for no report reading it; the guard's read rests on row 4 and the export's on row 39 |
| 2 | Per-round tokens are in scope. | `[engineer-verified: "I think add per round tokens as well. What does the analytics engineer think"]` |
| 3 | Version the column by its frozen header plus an integer rule stamp, after an adversarial review of schema versioning. | `[engineer-verified: "Header + rule stamp (Recommended)"]` for the shape; `[engineer-verified: "don't we need to keep track of schema version? We are effectively using NoSQL for this column so we need to be careful. Have the architect do an adversarial review"]` for the request; the hazard list and the no-`v` reasoning are `[unverified]`, this plan's judgment |
| 4 | A `--force` guard that protects round data only. | `[engineer-verified: "Add rounds-only guard (Recommended)"]` for a rounds-only guard; its conditions in M5 are `[unverified]`, this plan's proposal |
| 5 | The live tuple has 41 columns and ends in `invalid_usage_records`. The frozen headers are pre-`invalid_usage_records` (40, ending in `model_breakdown`), pre-`model_breakdown` (39) and pre-`host` (38). The relayed figure of 40 is stale. After this change the current header has 42 and the new frozen header 41. | `[verified: pr_cost_ledger.py:26-55, counted]` |
| 6 | A row parses under its own file's header, then each missing column fills from `_PR_COST_LEDGER_COLUMN_DEFAULTS` by name. The newer-version hint fires only when line 1 is the current header plus a tab and non-blank text, so a new column must be appended last. | `[verified: pr_cost_ledger.py:66-85, :290-306, :309-322]` |
| 7 | Today the one-cell-short hint is "append a tab" under the pre-`invalid_usage_records` header, "restore the column's correct value" under the current header, and absent otherwise. A parametrized test pins all four headers. | `[verified: pr_cost_ledger.py:294-301; test_transcript_pr_cost_ledger.py:466-498]` |
| 8 | JSON handling is hard-wired to `model_breakdown` at the parse and format sites, and `_PR_COST_JSON_COLUMNS` has no other reader. `_encode_model_breakdown_cell` has one production caller outside the formatter and one test monkeypatch. | `[verified: pr_cost_ledger.py:99, :286, :377-378; pr_cost.py:271; test_transcript_pr_cost_ledger.py:938; repo grep]` |
| 9 | Every write rewrites the whole file under the current header, refuses unless prior rows survive value-for-value with JSON cells compared as decoded values, and returns `True` when it upgraded. Each write parses the ledger three times under the per-branch lock. | `[verified: pr_cost_ledger.py:434-552; pr_cost.py:776]` |
| 10 | Export columns derive from the ledger tuple through the shared formatter, and the triage test fails on any untriaged column. | `[verified: pr_cost_export.py:27-31, :231; test_transcript_pr_cost_export.py:184-210]` |
| 11 | `compute_review_round_costs` requires an iterator without merged subagent records and prices dispatches itself. Its round dicts hold `branch_key` (`(None, raw branch)` under `resolved_roots=None`), `skill`, `ts`, `main_dollars`, `agent_dollars`, `agents`, `unpriced_turns`, `dangling` and `sort_key`. | `[verified: review_rounds.py:330-469]` |
| 12 | Neither pricing site in `review_rounds.py` checks usage validity, and both discard `_price_turn`'s unpriced-token count. `_price_turn` applies `int()` to each token field, so a non-numeric string raises `ValueError` and a negative count prices negative dollars. | `[verified: review_rounds.py:303-310, :419-430; pricing.py:578-606]` |
| 13 | `cost._usage_is_invalid` wraps `session_pricing._validate_usage_token_fields`. It is called at four `cost.py` sites and at `period_cost.py:539`, and named in six further `cost.py` docstrings and comments. `review_rounds.py` never imports `cost.py`. `session_pricing.py` imports only `corpus` and `pricing`, so `review_rounds.py` can import it without a cycle. | `[verified: cost.py:44-52, :121, :190, :850, :1173; period_cost.py:539; review_rounds.py:15; session_pricing.py:17, :128-139]` |
| 14 | `_price_dispatch` has two callers, both in `review_rounds.py`, and no test calls it. | `[verified: repo grep for _price_dispatch]` |
| 15 | The production round-dict readers, `cmd_review_round_cost` and `review_cost_by_size._join_rounds_to_prs`, read by key. No review-rounds test compares a round dict's whole key set. | `[verified: review_rounds.py:1341-1403; review_cost_by_size.py:96-143; grep of test_transcript_review_rounds.py for set and literal-dict comparisons]` |
| 16 | The scalar pass uses `include_subagents=True` and keys by raw attributed branch. The loop's `branch` is that raw name, or the PR's `headRefName` under `--pr`. A single-account `--pr N` with no local activity records an all-zero aggregate, and `--all-accounts --pr N` skips such an account. | `[verified: pr_cost.py:529-533, :712-720, :802-823]` |
| 17 | An existing test pins exactly one `_resolve_project_scope` call per `--record` run. | `[verified: test_transcript_pr_cost.py:730-766]` |
| 18 | The `model_breakdown` degrade path catches its check error, nulls the cell, records the row, prints a per-PR line after the write and a count line at the end, then exits 1. The tests' line stems live in `_pr_cost_helpers.py`. | `[verified: pr_cost.py:832-896; _pr_cost_helpers.py:132-135]` |
| 19 | The already-captured check runs under the per-branch lock against a fresh read. `existing_rows` is read under the account lock and replaced after every write, so a pre-lock lookup against it is a safe hint. Every write refuses unless prior rows survive (row 9), so a row the hint finds is also in the locked read unless the file was hand-edited, which `docs/pr-cost.md:145` bars. | `[verified: pr_cost.py:696-711, :772-794, :852; docs/pr-cost.md:145]` |
| 20 | `review-round-cost --pooled` already catches `Exception` and prints only the class name, because the message may embed a filesystem path. | `[verified: review_rounds.py:1314-1323]` |
| 21 | ruff selects `E`, `F`, `B`, `I`, `UP` and `SIM`, none of which flags `except Exception`, so no suppression comment is needed. | `[verified: pyproject.toml:6]` |
| 22 | A turn is priced only when its `message.model` is exactly a `_MODEL_BASE_INPUT_RATES` key, and every key must match `_MODEL_BREAKDOWN_KEY_RE`. So token-split model keys are table keys, and an unpriced model's ID is never recorded. | `[verified: pricing.py:59-61, :155-165]` |
| 23 | `docs/pr-cost.md:62` says `model_breakdown` is not the current header's trailing cell, which the append makes false. `:40` names `invalid_usage_records` as the current trailing column. `:263` says the export's last column is `model_breakdown`, which is already stale, since it is `invalid_usage_records`. | `[verified: docs/pr-cost.md:40, :62, :263; pr_cost_export.py:27-30]` |
| 24 | Layer 2 of "Combining exports" sums dollar and token columns across matched rows. `docs/pr-cost.md:197` lists every transcript-derived column as following both the pre-branch and subagent blocks. Round attribution applies only the pre-branch block, to the carried main-thread branch, and a dispatch prices into its round by `toolUseId`, so the subagent block does not apply. | `[verified: docs/pr-cost.md:197, :345; review_rounds.py:15-21, :236-249, :283-291; docs/transcript-analysis.md:718-719]` |
| 25 | `test_transcript_cli_bootstrap.py` seeds a literal pre-`model_breakdown` header that still parses after this change. `test_transcript_pr_cost_gh.py`, `test_transcript_analysis_branch_attribution.py`, `test_transcript_ledger_common.py` and `test_transcript_gh_cli.py` need no edit. `test_transcript_review_cost_by_size.py` builds `_JoinCounts` at four sites and hand-builds round dicts in `_round_entry`, and `test_transcript_pr_cost_export_accounts.py` pins the provenance key set, so both need edits. | `[verified: test_transcript_cli_bootstrap.py:255-276; test_transcript_review_cost_by_size.py:80-89, :409, :459, :488, :543; test_transcript_pr_cost_export_accounts.py:546-549]`; the no-edit sentence rests on staff-sdet's read of those four files |
| 26 | The round pass costs about one more scan of the account's main-thread corpus, plus re-reading each dispatched subagent file in sessions that open a round, on every `--record` run that writes a row, each `--force --pr N` included. Docs describe it qualitatively. | `[unverified]` |
| 27 | A representative round's encoded length is measured by a test over a synthetic round, not hand-counted, and the write bound admits at least 1000 such rounds. The floor of 1000 is this plan's judgment of "far above an organic PR", not a measurement. | `[unverified]` |
| 28 | How often malformed usage occurs in real corpora. | `[unverified]`; the guard costs one check per usage-bearing turn either way |
| 29 | `CHANGELOG.md` records consumer-visible `pr-cost` schema changes under `[Unreleased]`, in `### Changed`, with bold leads and `**Migration:**` sub-bullets. | `[verified: CHANGELOG.md:5-9, :186-201]` |
| 30 | `docs/transcript-analysis-architecture.md` lists `review_rounds.py`'s importers and imports, and `pr_cost.py`'s imports. | `[verified: docs/transcript-analysis-architecture.md:25-30, :279-281, :441-443]` |
| 31 | `_resolve_project_scope` defaults `include_subagents` to `False`. The round pass passes it explicitly anyway. | `[verified: scope.py:524-531]` |
| 32 | `docs/private-project-redaction.md` keeps no per-column list, so it needs no edit. `docs/pr-cost.md`'s redaction and fingerprint paragraphs are the home. | `[verified: grep of docs/private-project-redaction.md]` |
| 33 | Every record inside a round's window is attributed to the opening branch, while the row attributes each record on its own, so a branch's round dollars can exceed its row total. No round-versus-row bound holds. | `[verified: review_rounds.py:217-250; cost.py:118]` |
| 34 | Accumulating tokens beside dollars from the same `_price_turn` result means a turn reaches both or neither. `pricing.py:45-46` already warns that each multiplier condition must be mirrored in `_pricing_variant`. | `[verified: pricing.py:45-46, :602-605; review_rounds.py:419-430]`; that this makes a re-pricing check redundant is `[unverified]`, this plan's judgment |
| 35 | `_sample_pr_cost_row` asserts it covers every ledger column, and the hand-written frozen-header literals are the test-side source for older headers. | `[verified: _pr_cost_helpers.py:12-38, :338-363]` |
| 36 | The cell covers every review skill, not only code-review. | `[engineer-verified: "why shouldn’t it do the other review skills as well"]` |
| 37 | The malformed-usage skip-and-count guard in the round pass is in scope. | `[engineer-verified: "Add skip-and-count guard (Recommended)"]` |
| 38 | `review-round-cost` and `review-cost-by-size` each print one footer line when the summed `invalid_usage_records` is nonzero, and the behavior change gets its own `### Fixed` CHANGELOG entry. | `[engineer-verified: "Print a footer line (Recommended)"]` for the footer; the line wording and the `### Fixed` entry follow `cost.py:1043-1046` and `CHANGELOG.md:569-572`, `[verified]` as precedents |
| 39 | The export carries `review_rounds` in this change. Export columns derive from the ledger tuple and consumers compare columns by name, so a later addition would have been additive; the cost accepted is that a shipped export file cannot be recalled. | `[engineer-verified: "I would say why not export it’s easier to do now then changing the schema later"]` for the decision; `[verified: pr_cost_export.py:27-30; docs/pr-cost.md:296]` for the facts |
| 40 | The export emits only vocabulary the exporting checkout knows, checked per key position, drops everything else, counts one per removed key in the provenance line, and prints one fixed stderr line when that count is nonzero. | `[unverified]`: the session's proposal answering ciso-reviewer R2, not an engineer decision; the stderr line is this plan's judgment |
| 41 | The cell is bounded twice: a write-time rule that degrades only the cell and can be loosened later, and a decoder backstop far above it. | `[engineer-verified: "Writer check + decoder backstop (Recommended)"]` for the two layers; the length measure, the values and their grounding are `[unverified]` (row 27) |
| 42 | `test_select_tests.py` ground-truths `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` against the import closure walked from `evals/review_bench`, which already reaches `review_rounds.py`. `session_pricing.py` is not in the set, and it imports only modules already in it. | `[verified: select-tests.py:88-98; test_select_tests.py:805-812; review_rounds.py:37-38; session_pricing.py:17]` |
| 43 | `after_merge` stays in the cell. With timestamps excluded, it is the only way any later reader can separate post-merge rounds once transcripts expire. Piece 1 counts them toward the PR (`docs/transcript-analysis.md:1428`), so a ledger reader must be able to match or exclude them. Context's design consults list it. | `[unverified]`: this plan's judgment; no engineer quote covers `after_merge` |
| 44 | Backfilling rows captured before this change stays out of scope, with no new flag and no candidate-listing docs. | `[engineer-verified: "I would keep back full entirely out of scope"]` |
| 45 | `corpus._parse_iso_datetime` raises `AttributeError` on a truthy non-string timestamp, a pinned contract. In each session it prices, the round pass parses every record's timestamp through `scope._attribute_carried_branch` and reads the session's `*.meta.json` files, where a non-object file raises out of `corpus._index_subagent_dispatches`. The scalar pass parses a timestamp only for a usage-bearing assistant record, and builds its branch index only from branch-tagged main-thread records. | `[verified: corpus.py:42-46, :70-79, :215-225; scope.py:978-991, :1062, :1086; review_rounds.py:240-245, :396; cost.py:107-128; test_transcript_session_pricing.py:1232-1249]` |
| 46 | No runtime opt-out for the round pass, and no downgrade recipe. | `[unverified]`: the session's call; the engineer was not asked |
| 47 | Moving `_usage_is_invalid` to `session_pricing.py`, and the resulting exclusion in `review-round-cost` and `review-cost-by-size`, follow from row 37 but are not in its quote. | `[unverified]` |
| 48 | A row recorded during a failed pass counts as captured, so a scheduled rerun without `--pr` skips it, and only `--record --force --pr N` refills it. | `[verified: pr_cost.py:783-794; docs/pr-cost.md:87]` |
| 49 | `--record` resolves the as-of window before the per-branch lock, and a waiter on that lock exits 1 after `ledger_common._COST_LEDGER_LOCK_TIMEOUT_S` (30 s). | `[verified: pr_cost.py:759-773; pr_cost_ledger.py:558-578; ledger_common.py:36]` |
| 50 | Under `--all-accounts`, every existing skip continues the run without changing its exit code, and an account that records no row counts once as skipped in the summary line. | `[verified: pr_cost.py:764-768, :786-808, :874-887]` |
| 51 | `compute_review_round_costs` builds `dispatch_index` and `visited` per session and creates a round only for a window detected in that session, so skipping a session with no window leaves every returned round unchanged. The skip also skips that session's branch attribution and `*.meta.json` reads. `review-cost-by-size` reads only `rounds` too. | `[verified: review_rounds.py:391-454, :217-250; review_cost_by_size.py:90-93]` |
| 52 | The account read and the per-branch write take one lock file, `<ledger>.lock`. | `[verified: pr_cost.py:697-699, :771-773]` |
| 53 | `review-round-cost` scans every root `scope.resolve_scan_roots` returns and re-raises any exception outside `--pooled`. An explicit top-level `--config-dir` narrows the roots to one. | `[verified: review_rounds.py:1260-1263, :1314-1319]`; the `--config-dir` narrowing rests on `scope._resolve_project_scope`'s docstring (`scope.py:577-582`), not a run |
| 54 | `git diff --merge-base A` compares the working tree with the merge base of A and `HEAD`. git-diff(1) reads: "git diff --merge-base A is equivalent to git diff $(git merge-base A HEAD)". | `[unverified]`: quoted from git's documentation, not run here |
| 55 | `docs/pr-cost.md`'s "Write-time check failures" heading is cited by `CHANGELOG.md:192` and `docs/transcript-analysis.md:1173`, so keeping its text keeps both citations resolving at any heading level. | `[verified: repo grep]` |
| 56 | The scripts test tree is a package whose shared builders live in 11 `_*_helpers.py` modules imported relatively, and `select-tests.py` maps every path under `claude/.claude/scripts/` to that whole test directory, so a new helper module needs no rule-table edit. | `[verified: claude/.claude/scripts/tests/__init__.py; test_transcript_pr_cost_ledger.py:23; select-tests.py:611; glob of claude/.claude/scripts/tests/_*.py]` |

**Mechanisms.**

**M1: Round-pass additions and the report footers.** `anchors: row2, row11, row12, row13, row14, row15, row26, row28, row34, row37, row38, row42, row45, row47, row51, G3`
- `review_rounds.py` gains `REVIEW_ROUND_RULE: int = 1` beside `REVIEW_SKILLS`. Prescribed comment: "Stamped into pr-cost ledger review_rounds cells; bump it with any change to which records open a round, where a window ends, which branch a round goes to, or which turns and dispatches it prices."
- `_price_dispatch(tool_use_id, dispatch_index, visited, tally) -> None` accumulates into a caller-supplied `_DispatchTally` dataclass with fields `dollars`, `unpriced_turns`, `unpriced_tokens`, `invalid_usage_records`, `dangling`, `resolved` and `tokens`, and its recursion passes the same tally. The main loop makes one tally per top-level spawn. It binds `tokens` to the round's `agent_tokens` when the spawn is inside a window, and to a throwaway dict otherwise, and adds `tally.dollars` to `branch_totals` as today. A shared tally adds nested turns one at a time rather than as subtotals, so a nested dispatch's dollars can differ at the last binary digit.
- Each round dict gains four keys:
  - `main_tokens` and `agent_tokens`: `{model: {variant: {class: int}}}`, priced turns only, all five classes per group;
  - `unpriced_tokens`: `_price_turn`'s third value for each unpriced turn;
  - `invalid_usage_records`: the count below.
- One helper adds a priced turn's tokens with `pricing._token_counts` and `pricing._pricing_variant`. Both priced sites call it immediately after adding the turn's dollars.
- At both sites, a usage-bearing assistant record whose usage fails `session_pricing._usage_is_invalid` adds 1 to `invalid_usage_records`: the round's when inside a window, the tally's inside a dispatch. It is excluded from dollars, tokens, unpriced counts and `branch_totals`, and its spawn blocks are still followed.
- **Sessions with no round.** `compute_review_round_costs` gains a keyword `rounds_only: bool = False`. When it is set, a session whose `detect_round_windows` result is empty is skipped before `_session_record_branches` and `corpus._index_subagent_dispatches`, and the return carries no `branch_totals` key, so a caller cannot read partial totals. Every returned round is unchanged (row 51). Only M4's scan passes it. The docstring states both effects. A session-filtering wrapper in `pr_cost.py`, the lighter option, would detect windows twice per session, the first time before the function's own dedup.
- **The guard's reach.** It covers usage only. A record outside the transcript format still raises, as every reader's contract says: a truthy non-string timestamp, a non-object `*.meta.json`, or a pathological integer literal (row 45). The round pass reads more of those than the scalar pass in each session that opens a round, so one such record there empties that account's `review_rounds` cells for the run through M4's containment. M7 documents it.
- `_usage_is_invalid` moves verbatim from `cost.py` to `session_pricing.py`. `cost.py`'s four calls and `period_cost.py:539` call it there, and `cost.py`'s six mentions name its new home. It stays underscore-prefixed, so `session_pricing`'s stability promise does not cover it.
- `compute_review_round_costs`'s docstring lists the new keys and the malformed-usage exclusion. The module docstring's import sentence adds `session_pricing`.
- `select-tests.py` adds `claude/.claude/scripts/transcript_analysis/session_pricing.py` to `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES`, in sorted position (row 42).
- **`review-round-cost` footer.** `per_root_totals` gains `total_invalid_usage_records`, summed over each block's rounds. `_print_footer` prints it after the unpriced-turns line, only when nonzero: `{prefix}MALFORMED USAGE — {n:,} usage-bearing turns inside round windows had non-numeric or negative token fields and were excluded from this report's dollars`. `--pooled` prints no such line (see "Alternatives set aside").
- **`review-cost-by-size` diagnostics.** `_JoinCounts` gains a required `invalid_usage_records` field, summed over joined rounds as `unpriced_turns` is. `_print_join_diagnostics` prints it after the dangling-dispatches line, only when nonzero: `  MALFORMED USAGE — {n:,} usage-bearing turns inside joined rounds had non-numeric or negative token fields and were excluded from this report's dollars`.
- Both lines follow `cost`'s banner wording (`cost.py:1043-1046`). They count inside round windows only, as the neighboring unpriced and dangling lines do.
- **Golden test** in `test_transcript_review_rounds.py`:
  - One param per `_ROUND_SHAPE_CORPUS_BUILDERS` entry, each in its own fresh `fake_projects`, as the agreement guard at `:1184` does. One composed corpus is not possible: every builder writes `sess-1.jsonl`, and `_write_jsonl` truncates.
  - Three builders join the list, so the agreement guard covers them too:
    - `_corpus_prefix_default_branch`, lifted from `TestPreBranchRoundAttribution._write_prefix_round_session`, which then calls it;
    - `_corpus_ready_for_review`, a `Skill`-shape `ready-for-review` round closed by a user prompt;
    - `_corpus_token_and_variant_mix`, with distinct nonzero counts in every token class, a `speed="fast"` turn, an unpriced-model turn, a malformed-usage turn, two priced models across main thread and dispatch, and a duplicate-`requestId` pair inside the dispatch.
  - Before the compare, assert each shape's round count and skill sequence against a hand-written per-shape table, so a regenerated literal cannot pass on a collapsed corpus.
  - Project each round, in `sort_key` order, to its rate-independent fields: `skill`, branch, `agents`, `dangling`, `unpriced_turns`, `unpriced_tokens`, `invalid_usage_records`, and `main_tokens` and `agent_tokens` restricted to nonzero classes. Dollars stay out, so a rate-table change never trips it.
  - Hold the expected projections in `_GOLDEN_ROUND_PROJECTIONS_BY_RULE: dict[int, dict[str, list]]`, keyed by rule and then by param id. Assert that `max(_GOLDEN_ROUND_PROJECTIONS_BY_RULE)` equals `review_rounds.REVIEW_ROUND_RULE`, then that the current rule's entry for the param equals the projection. Prescribed comment: "Append-only: never change a recorded projection; a behavior change records new projections under a new rule and bumps REVIEW_ROUND_RULE."
  - The map is a tripwire, not enforcement. An in-place rewrite of an entry still passes, so the comment and review remain the guard.

Two lighter tally shapes were checked:
- Keeping the 4-tuple and adding an accumulator keyword gives one walk two accumulation channels.
- A 6-tuple return grows every recursive unpack.

**M2: Ledger column and frozen header.** `anchors: row5, row6, row7, row35, G2`
- Freeze today's tuple as `_PR_COST_LEDGER_PRE_REVIEW_ROUNDS_COLUMNS`, written as `(*_PR_COST_LEDGER_PRE_INVALID_USAGE_COLUMNS, "invalid_usage_records")`. Define `_PR_COST_LEDGER_COLUMNS` as that tuple plus `"review_rounds"`, last.
- Register the frozen tuple in `_PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE`, and add `"review_rounds": ""` to `_PR_COST_LEDGER_COLUMN_DEFAULTS`.
- Prescribed comments:
  - On the frozen tuple: "Exact column order of pre-review_rounds ledgers; never edit, and never revert to code that lacks it."
  - On the new column: "Empty cell: not recorded. Every skill an empty list: recorded, no rounds."
- `_current_cells_from_file_row` derives the one-cell-short hint from the header's last column instead of naming headers:
  - "append a tab" when that column is a JSON column;
  - "restore the column's correct value" when that column's default is non-empty;
  - nothing otherwise. A column with no `_PR_COST_LEDGER_COLUMN_DEFAULTS` entry, such as `risk_surface_flag`, counts as an empty default.

  This keeps every existing header's message (row 7). The current header gets the append-tab hint, the new frozen header gets the restore hint, and the next column needs no edit here.
- A lighter option, inserting the column anywhere but last, breaks the newer-version hint (row 6).

**M3: The cell and its codec.** `anchors: root, row3, row8, row9, row22, row27, row33, row41, row43, G4`
- **Shape.** `{"rule": int, "rounds": {skill: [round, ...]}}`, with the same canonical encoding as `model_breakdown`. The writer emits every `review_rounds.REVIEW_SKILLS` member as a key, each list in `sort_key` order.
- **Meaning.**
  - An empty cell means not captured: the row predates the column, the pass failed, the cell failed its build or write-time check, the branch had no local transcript activity, or the checkout records no rounds (the Rollback lever).
  - Every list empty means captured with zero rounds.
  - A skill key absent from `rounds` was not tracked by that writer.
  - A literal `null` cell is malformed.
- **Round fields.**
  - `main_usd_micros` and `agent_usd_micros`: the round's `main_dollars` and `agent_dollars` through `pr_cost._usd_to_micros`, as-priced under the row's `rate_stamp`.
  - `main_tokens` and `agent_tokens`: raw token counts per priced model, pricing variant and class, with `model_breakdown`'s labels.
  - `agents`, `dangling`, `unpriced_turns`, `unpriced_tokens` and `invalid_usage_records`: the round dict's counts across both threads.
  - `after_merge` (row 43): `1` when the round's opening timestamp is at or after the PR's `mergedAt`, else `0`. The key is absent when either timestamp does not parse.
  - No timestamp, stored count or total: the count is the list length and the total is a sum.
- **One home for field names.** `pr_cost_ledger.py` holds `_REVIEW_ROUND_COUNT_FIELDS`, `_REVIEW_ROUND_TOKEN_FIELDS` and `_REVIEW_ROUND_FLAG_FIELDS` (`after_merge`). The codec, the builder's key-set test and the export filter (M6) all read them.
- **Codec.** `_decode_review_rounds_cell(cell, line_no)` in `pr_cost_ledger.py` checks structure only and adds no package import.
  - `""` decodes to `None`. A cell longer than `_REVIEW_ROUNDS_CELL_READ_MAX_CHARS` is refused before parsing. Any other cell goes through the same `json.loads` call with `_reject_duplicate_json_keys`, and the same `ValueError` and `RecursionError` mapping, as `model_breakdown`, shared rather than copied.
  - The top level is an object with exactly the keys `rule` and `rounds`. `rule` is a non-bool integer from 0 to `2**63 - 1`. `rounds` is an object whose keys match `_MODEL_BREAKDOWN_KEY_RE` and whose values are lists of round objects.
  - A round holds every count and token field. Each count is a non-bool integer from 0 to `_MODEL_BREAKDOWN_LEAF_VALUE_MAX`. `after_merge`, when present, is 0 or 1.
  - Each token object nests model, variant and class keys that match the key rule. Model and variant values are non-empty objects, and class values are integers in the count range. A token object may be empty.
  - Any other round key must match the key rule and hold an integer in that range. The decoder returns the whole object, unknown keys included.
  - No membership, skill-set or `rule`-value check runs here, so a renamed or added label, skill or rule never makes a checkout refuse the ledger.
  - Every message reads `line N: malformed review_rounds (<rule>)`, with no cell text. `<rule>` comes from `_REVIEW_ROUNDS_DECODE_RULES`, a closed tuple of literals in code, one per defect class: invalid JSON, duplicate key, deep nesting, oversized cell, top-level shape, `rule` value, key shape, `rounds` shape, round shape, missing field, integer range, `after_merge` value, and token-object shape.
- **Bounds** (row 41). Both live in `pr_cost_ledger.py`, and a module-level check raises if the write bound exceeds the read bound.
  - The write bound measures the canonical encoding's length. The encoding is ASCII, so characters equal bytes.
  - The read bound measures the raw cell's characters, before parsing. For a cell the tool wrote, the two lengths are equal. A hand-reformatted cell can differ.
  - `_REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS`: the smallest power of two at or above 1000 times a representative round's measured encoded length (row 27). M4's `size` rule enforces it. Prescribed comment: "Admits at least 1000 representative rounds; it may rise only up to the lowest value _REVIEW_ROUNDS_CELL_READ_MAX_CHARS has shipped with."
  - `_REVIEW_ROUNDS_CELL_READ_MAX_CHARS`: 16 times the write bound's first value, written as its own literal and pinned by a test. Prescribed comment: "Persisted contract: never lower it, because a checkout refuses any cell longer than the value it shipped with."
  - Every write parses the whole ledger three times under the lock (row 9). The docs state the linear growth, both bounds by name and ungrouped decimal value, what each measures, and that a cell near the read bound is slow and memory-heavy to parse.
- **Routing.**
  - `_PR_COST_JSON_COLUMNS` becomes a column-to-decoder map used at both the parse site and the format site, so each error names its column.
  - `_encode_model_breakdown_cell` becomes `_encode_json_cell`. `pr_cost.py:271` and the monkeypatch at `test_transcript_pr_cost_ledger.py:938` follow the rename.
  - `_decode_model_breakdown_cell` keeps its name and signature, because its tests call it directly.
- Prescribed comment on the round contract: "Persisted contract: a round may gain a key only as a key-shaped name holding an integer from 0 to 2**63 - 1; any other shape change is a new appended column under a new frozen header."
- The lighter options checked, scalar columns and delimited lists, fail the ordered series, and a strict round turns each additive field into a lockout (see "Alternatives set aside").

**M4: Capture in the `--record` loop.** `anchors: row11, row16, row17, row18, row19, row20, row21, row26, row33, row41, row45, row48, row49, row51, row52, row53, G1`
- **The scan.** `pr_cost.py` imports `review_rounds` by module. `_scan_review_rounds_by_branch(args, root)` calls `scope._resolve_project_scope(args, "pr-cost", include_subagents=False, roots=[root])`, then `review_rounds.compute_review_round_costs(session_iter, resolved_roots=None, rounds_only=True)` with no filters. It groups the rounds by raw branch name (`branch_key[1]`) and prints no scope line. When it starts, it prints `pr-cost: scanning transcripts for review rounds...` to stderr, with `account-K's ` before `transcripts` under `--all-accounts`.
- **At most once per account, never under the lock.** A per-account lazy getter runs the scan the first time it is called and memoizes the result or the failure.
  - After the as-of check and before the per-branch lock, the loop takes the existing already-captured skip when `--force` is off and `_latest_pr_cost_row(existing_rows, ...)` finds a row (row 19). It keeps today's message and today's single-account `--pr` exit 1, and one helper prints that message at both sites.
  - Otherwise the loop calls the getter when the branch has local activity (`branch in branch_totals`). Under the lock, the loop reads only the memo, which is therefore filled whenever the branch has local activity.
  - The locked already-captured check stays, for a row a concurrent `--record` appended after the pre-lock read.
  - The pre-lock skip can be wrong only after an out-of-band row deletion, which `docs/pr-cost.md:145` bars. It then skips a PR that the next run records.
- **Pass failure.** The getter's one call site catches `Exception` (row 21), following the `--pooled` precedent (row 20), and prints once: `pr-cost: the review-round pass failed (<ExceptionClass>) -- any row this run records<for-account> carries no review rounds; see docs/pr-cost.md`. `<for-account>` is empty, or ` for account-K` under `--all-accounts`.
  - Only the class name prints, never the message.
  - Catching narrower types would let an unanticipated defect in this optional pass cost every remaining branch its scalar row, the outcome the degrade policy prevents. Exit 1 keeps the defect visible.
- **Recovery after a failed pass** (row 48). Those rows count as captured, so a scheduled rerun skips them. Only `--record --force --pr N` refills each one, once the cause is fixed and only inside `cleanupPeriodDays`. The count line lists them, and M7 states that a caller ignoring exit 1 loses their rounds. `review-round-cost`, run with the failed run's scope flags minus `--all-accounts`, re-raises the exception with its traceback, the diagnostic M7 points to (row 53). It prices every session, not only those that open a round, so it can also fail on a record the pass skipped.
- **Selection.** Under the lock, the branch's rounds are its memoized list, possibly empty, or an absence: `pass-failed`, or `no-local-activity` when `branch not in branch_totals` (the all-zero fallback).
- **One containment boundary.** `_review_rounds_cell_outcome(rounds, absence, merged_at)` returns `(cell, absence)`, with `absence` from the closed set `pass-failed`, `no-local-activity`, `not-built`, or a check rule.
  - With an absence already given, it builds nothing.
  - Otherwise it calls `_build_review_rounds_cell(rounds, merged_at)`, the seam the tests patch, then `_check_review_rounds_cell(cell)`, inside one `try` that catches `Exception`, so `SystemExit` passes through.
  - A `_ReviewRoundsCheckError` gives its rule, any other exception gives the rule `build`, and neither prints exception text. A builder returning `None` gives `not-built`.
  - `_new_pr_cost_row` takes the cell through a required `review_rounds_cell` keyword with no default, so a missed wiring fails loudly.
- **Write-time check.** `_check_review_rounds_cell(cell)` raises `_ReviewRoundsCheckError(rule)` on the first failing rule. `_REVIEW_ROUNDS_CHECK_CAUSES` maps each rule, in check order, to the cause its per-PR line prints:
  - `size`: the canonical encoding is no longer than `pr_cost_ledger._REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS`. Cause: `more rounds than the cell admits`.
  - `shape`: encoding then decoding through `pr_cost_ledger` returns the same value. Cause: `malformed transcript data or a claude-config defect`.
  - `model-membership` and `label-membership`: token model keys are `_MODEL_BASE_INPUT_RATES` keys, variant keys are `_PRICING_VARIANTS` members, and each group's class keys equal `_TOKEN_CLASSES`. Cause: `a claude-config defect`. They become one helper shared with `_check_model_breakdown_cell`.
  - `build`: the containment's rule for a builder exception. Cause: `a claude-config defect`.

  There is no skill-membership rule. The builder iterates `REVIEW_SKILLS` itself, which a unit test pins, and the decoder accepts any skill set, so the rule could only turn a readable cell into a nulled one. No round-versus-row bound is checked (row 33). A `None` cell skips every check.
- **Degrade.** The loop nulls a failing cell and records the row. After the write it prints `pr-cost:   PR #<a>: review rounds <reason> -- recorded the row without them; see docs/pr-cost.md`. `<reason>` is `were not captured (the review-round pass failed)` or `failed their <rule> check (<cause>)`. `no-local-activity` and `not-built` print nothing and are not counted.
- **Count line.** The existing count line generalizes to every degraded cell: `pr-cost: <n> row(s) recorded with failed cells (<list>); those rows are valid -- once the cause is fixed, re-capture each with --record --force --pr N and this run's account flags, only while every session of that PR is still inside cleanupPeriodDays (see docs/pr-cost.md)`.
  - A pure `_failed_cells_count_entry(pr_number, failed_columns, account_ordinal)` builds each `<list>` entry: `PR #<a> (<cells>)`, where `<cells>` is `model_breakdown`, `review_rounds`, or `model_breakdown and review_rounds`, prefixed `account-K ` under `--all-accounts`.
  - The run then exits 1, as today.
- **Partial data.** A round with nonzero `unpriced_turns`, `dangling` or `invalid_usage_records` is recorded as is, and the run exits 0.
- `_pr_cost_report`'s docstring covers the new pass, the degrade and the guard.
- The lighter options checked, an eager pass, one shared pass and a bounded pass, are in "Alternatives set aside".

**M5: The `--force` guard.** `anchors: row4, row19, row50`
- **Placement.** Inside the per-branch lock, after both write-time checks and before `_append_pr_cost_ledger_row`, when `--force` is set and a prior row exists.
- **Rule.** A pure `_review_rounds_force_refusal(prior_cell, new_cell, new_absence) -> str | None` returns the refusal reason or `None`. P is the prior row's (`already`'s) `review_rounds` cell, and N is the new row's. The first matching case decides:
  - P not captured (`None`): allow. The guard protects captured rounds, and such a row holds none.
  - N empty: refuse with the reason its absence gives: `the review-round pass failed`, `the new cell failed its <rule> check`, `this branch has no local transcript activity`, or `this checkout does not record review rounds`. A P whose lists are all empty is captured data, a recorded zero, so it refuses here too.
  - N's `rule` lower than P's: refuse, `this checkout's round rule is older than the captured row's`.
  - Equal `rule` values, where some skill in P has a longer list or a higher summed `agents` than in N, a missing key counting as empty: refuse, `the re-capture is less complete under the same round rule`.
  - Otherwise allow, any higher N `rule` included.
- **Why those two fields.** Under one rule, a skill's list gets shorter only when a session transcript is gone, and its summed `agents` falls only when a dispatch transcript is. A later session that adds rounds raises both, so it never triggers a refusal.
- **Message.** `pr-cost:   PR #<a> for machine=<m>: refusing --force -- the new row would lose captured review rounds (<reason>); see docs/pr-cost.md`. M7 gives each reason's next step. A `--projects` or `--this-repo` scope narrower than the original capture's can also produce `no local transcript activity` or `less complete`, so those two next steps name it.
- **Exit.** A single-account `--pr` run exits 1 at once with the ledger unchanged. Under `--all-accounts`, that account skips the PR and counts as skipped in the summary, as other skips do (row 50). The run then exits 1 after its summary and any count line, because a correction the operator named was refused.
- **No override.** The guard refuses the whole append, so it also blocks a `--force` meant to heal a degraded `status` (`docs/pr-cost.md:130-131`) or to re-capture across the attribution cutover (`:217`). Each refusal means either the re-capture cannot be complete here or this checkout cannot record the series, and M7 gives each reason a way forward without a hand edit.
- **Residual.** A higher-rule re-capture over partly aged transcripts passes, leaving the prior series only in the superseded row. It prints no notice, and M7 documents it. The guard covers `review_rounds` only, not the scalar columns or `model_breakdown`.

**M6: Export.** `anchors: row10, row22, row39, row40, G4`
- **Passthrough** (row 39). `review_rounds` exports through the formatter's JSON branch, after the filter below.
- **Vocabulary filter** (row 40). `_review_rounds_cell_for_export(cell) -> tuple[dict | None, int]` in `pr_cost_export.py` takes the decoded cell and returns the filtered cell and its drop count:
  - The top level passes through unchanged, because the decoder already fixes it to exactly `rule` and `rounds`.
  - Each key position is checked against its own vocabulary only: skill keys against `review_rounds.REVIEW_SKILLS`, round keys against M3's three field tuples, model keys against `pricing._MODEL_BASE_INPUT_RATES`, variant keys against `pricing._PRICING_VARIANTS`, and class keys against `pricing._TOKEN_CLASSES`. A name known at one position is unknown at every other.
  - Every other key is removed. The count adds one per key removed at the highest level that removes it, and nothing inside a removed key's value, so it is bounded by key occurrences and says nothing about a dropped subtree's size.
  - A model or variant object the filter leaves empty is removed uncounted, because the keys that emptied it were counted, so the exported cell still decodes. A token object it leaves empty stays `{}`, which the decoder accepts.
- The per-account loop sums the counts. The provenance line gains `review_rounds_dropped_keys=<n>` after `legacy_machine_value_rows`.
- When the sum is nonzero, after the existing `wrote` line, the export prints to stderr: `pr-cost-export: dropped <n> review_rounds key(s) this checkout does not know -- some rows lack fields, skills or models a newer checkout recorded, so their token splits can under-sum their dollars; re-export from an updated checkout before summing rounds (see docs/pr-cost.md)`. It carries no key text.
- `pr_cost_export.py` imports `pricing` and `review_rounds` by module. Neither imports `pr_cost_export`, so no cycle forms.
- The triage test adds `review_rounds` to the `model_bearing_row_level` set, and the set's comment names both columns.
- It stays DO-NOT-PUBLISH, because it rides an export row that carries the account dimension.
- The filter covers `review_rounds` only. `model_breakdown` stays unfiltered and shape-checked, as `docs/pr-cost.md:60` documents, so the two columns differ on purpose.

**M7: Docs and changelog.** `anchors: row13, row23, row24, row29, row30, row32, row33, row38, row39, row40, row41, row44, row45, row48, row53, row55`

No docs or CHANGELOG text presents `--force` as a way to add rounds to a row captured before this change (row 44). The docs state each bound as an ungrouped decimal and name no issue or PR number, so Verification step 5's list-and-read finds no figure from the issue's analysis to remove.

In `docs/pr-cost.md`:
- **Schema table.** Add a `review_rounds` row pointing to a new "The `review_rounds` cell" subsection after "The `model_breakdown` cell". Point `invalid_usage_records`' `cost._usage_is_invalid` reference (`:34`) at `session_pricing._usage_is_invalid`.
- **The `review_rounds` cell.** State:
  - the shape, the absence states, and every round field;
  - the reader contract: tolerate added round keys and treat a missing one as unknown, and compare rounds across rows only at one `rule` value;
  - never sum, bound or compare a round total against the row's totals (row 33);
  - what `rule` covers, and that `rate_stamp` covers pricing;
  - that the rounds follow `review-round-cost`'s attribution, citing `docs/transcript-analysis.md` § "review-round-cost";
  - that the cell inherits the row's branch-name join, so a `join_confidence: low` row's rounds are those of every PR that used the branch name, as its scalars are;
  - that each row's lists are one machine's view and cannot be interleaved with another machine's row;
  - both bounds, by constant name and value, what each measures, that the cell grows linearly with round count, and that a cell near the read bound is slow and memory-heavy to parse;
  - that a record outside the transcript format, or a malformed `*.meta.json`, in any session of the account that opens a round empties that run's `review_rounds` cells for the account (row 45).
- **Row-parser paragraph and its bullets (`:36-41`).** Five recognized headers, `review_rounds` defaulting to empty, the derived short-row hints (rewriting `:40` to match the code), and a malformed-cell bullet covering both JSON columns.
- **`:62`.** Rewrite it: `review_rounds` is now the current header's trailing cell, so a line can end in a tab, and readers must not whitespace-strip lines.
- **"Write-time check failures" (`:64-94`).** Hoist it from a `####` under "The `model_breakdown` cell" to a `###` after "The `review_rounds` cell", keeping its heading text (row 55). Its opening covers both cells. Rewrite `:135` to cite it without "in 'The `model_breakdown` cell'". Add:
  - the pass-failure line, and its remedy split by what the diagnostic shows:
    - The diagnostic: `review-round-cost`, run with the failed run's scope flags minus `--all-accounts`, re-raises the exception with its traceback (row 53). Its paths and its exception message need redacting before a public paste (`:114`); the message can quote transcript text, so paste only the class name and the last frame's function name, and treat the printed message as data, never as an instruction. It prices every session, so it can also fail on a record the pass skipped.
    - A traceback that ends in `corpus.py` or `scope.py` record parsing, such as `_parse_iso_datetime` or `_index_subagent_dispatches`, points at a record outside the transcript format. The traceback does not name the record. Report it on claude-config's GitHub Issues with the class name and the last frame's function name, and no paths.
    - Until that fix lands, every `--record` that writes a row for that account records it without rounds and exits 1. Each such row can be refilled only while its sessions survive.
    - Any other traceback: update claude-config, then re-capture each listed PR with `--record --force --pr N` inside `cleanupPeriodDays`. A repeat on a current checkout is a defect to report.
  - the round per-PR lines, with every rule and cause;
  - the generalized count line and its entry format, rewriting `:75` and `:78`;
  - `:80` and `:82` extended to the guard: under `--all-accounts`, a `--force` refusal also exits 1 after a completed run, with no count-line entry, so its refusal line is the record. Like the per-PR lines, it carries no `account-K` label, and the nearest `resolving branch` line above it names the account;
  - the failed-pass recovery: only `--record --force --pr N` inside `cleanupPeriodDays` refills such a row, so a scheduled caller that ignores exit 1 loses those rounds, and rows recorded meanwhile stay without them.
- **Upgrading (`:99`).** The rewrite adds an empty `review_rounds` cell to every older row.
- **Data (`:141`, `:145`).** The at-rest list adds per-PR review-round series. The blanking exemption covers either JSON column, and a blanked `review_rounds` cell keeps its tab as the line's last character.
- **Default output (`:149`).** Read mode never prints `review_rounds`.
- **`--record`'s capture (`:151-153`).** One qualitative sentence: a run that writes a row reads the transcripts a second time, after a start line, pricing only sessions that open a round, and a run with nothing to write does not.
- **Re-record contract (`:167-169`).**
  - Rewrite `:167`'s "The tool does not check this": the tool checks it for `review_rounds` only, through the guard, and not for the scalar columns or `model_breakdown`.
  - Make `:169` the one home for the per-PR cost of any `--force` re-capture: a full scalar scan plus the round scan, a re-stamped `rate_stamp`, a move to the current `rule`, and 1 more in the export's `correction_count`. It also states that `--force` refills a `review_rounds` cell that a failed pass or a failed check left empty, within the `cleanupPeriodDays` caveat. Its existing `model_breakdown` sentences stay.
  - The guard's rule, its residual with no console notice, and its `--all-accounts` exit 1. It has no override, so it also blocks a status-heal or cutover re-capture that would lose rounds.
  - Each reason's next step, none of them a hand edit (`:145`):
    - the pass failed: follow the split remedy under "Write-time check failures";
    - the cell failed a check: the captured row stays current, and a repeat on an updated claude-config is a defect to report;
    - no local activity: either the sessions aged out or ran on another machine, so a re-capture here would understate the scalars too and the captured row should stay, or this run's `--projects` or `--this-repo` scope is narrower than the original capture's, so repeat the original scope flags;
    - the checkout does not record rounds: update to one that does;
    - an older round rule: update this checkout;
    - less complete: either some transcripts aged out since the captured row, so keep it, or the scope is narrower than the original capture's, so repeat its flags.
- **Attribution (`:197`).** Add a bullet after the list. `review_rounds` postdates both blocks, so no cell is old-rule. A round's branch follows only the pre-branch block, applied to the carried main-thread branch (`docs/transcript-analysis.md:718-719`). A dispatch prices into its round by `toolUseId`, so the subagent block does not apply. `rule` marks later attribution changes.
- **Redaction (`:255`).** `review_rounds` is stored in the clear too.
- **Export parsing and columns (`:263`, `:272-274`).**
  - The last column is `review_rounds`, which fixes the stale name.
  - It exports in canonical encoding after the vocabulary filter, so the un-tokenized claim covers only what the exporting checkout knows, and a model gone from that checkout's rate table drops with a count. This scoping applies to the `review_rounds` column only: `model_breakdown` keys still export shape-checked only.
  - Name the `review_rounds_dropped_keys` provenance key and its stderr line. A nonzero count means some rows lack fields, skills or models a newer checkout recorded, so their token splits can under-sum their as-priced dollars.
  - One sentence says the two JSON columns differ: `review_rounds` is vocabulary-filtered, and `model_breakdown` is shape-checked only (`:60`).
  - It stays DO-NOT-PUBLISH.
- **`correction_count` (`:280`).** Four causes, adding a cell refill: a re-capture that fills a cell an earlier capture left empty.
- **Fingerprint (`:288`).** The per-round series, its per-round variant labels and its `after_merge` flags add per-round resolution to the per-PR fingerprint and to the configuration facts the variants record.
- **Publication boundary (`:290`).** A figure derived from `review_rounds` publishes only under the aggregate exception, or when it is computed over "this repository's own corpus on a single account", quoting CLAUDE.md, per `docs/private-project-redaction.md` § "Publishing a tooling measurement". An export carries other repositories' rows, so no figure computed from one meets that scope: round counts, per-round dollars or tokens, after-merge shares.
- **Combining (`:310-318`, `:345`).**
  - `review_rounds` is never a required column (`:316`).
  - A new validation bullet: leave an input out of round sums when its provenance line's `review_rounds_dropped_keys` is nonzero, or re-export it from an updated checkout first.
  - In Layer 2, per-skill round counts and the as-priced `main_usd_micros` and `agent_usd_micros` totals add across matched rows only when those rows share one `rule` value, subject also to Layer 1's keep-latest rule and the rate-stamp rule. A pool spanning two `rule` values is summed per value or left out.
  - Only the ordered series needs a single row, because rounds carry no timestamps to interleave, and `after_merge` stays per row.

In `docs/transcript-analysis.md`:
- The `pr-cost` `--record` bullet (`:1173`) adds the second pass, the exit 1 for a row recorded without its rounds, and the `--force` refusal, and points to `docs/pr-cost.md`'s re-record contract for the re-capture's cost and retention limit.
- `review-round-cost`:
  - the round detection caveats (`:1236-1243`) add the malformed-usage exclusion and state its predicate: a turn whose usage is not an object, or whose token fields are not non-negative integers;
  - the reconciliation paragraph (`:1245`) adds the `MALFORMED USAGE` line, counted inside round windows only;
  - "Coverage the scan-gap refusal does not give" (`:1329-1339`) adds that pooled shares exclude such turns, with no share of their own.
- `review-cost-by-size`'s diagnostics (`:1439-1443`) add the `MALFORMED USAGE` line.

In `docs/transcript-analysis-architecture.md`:
- `review_rounds.py`'s importers add `pr_cost.py` and `pr_cost_export.py` (`:26-30`).
- Its imports add `session_pricing` (`:279`).
- `pr_cost.py`'s imports add `review_rounds` (`:441`).
- `pr_cost_export.py`'s imports add `pricing` and `review_rounds` (`:451`).

In `CHANGELOG.md`, under `[Unreleased]`:
- **`### Changed`, one entry.**
  - Body: the column and what it holds, the upgrade notice and the absence of a downgrade, the second pass and its start line, the new exit-1 paths and `--force` refusals, and the export column with its filter, provenance key and stderr line.
  - Body: every pre-existing row now ends in a tab, so do not whitespace-strip ledger or export lines, and read export columns by header name.
  - `**Migration:**` sub-bullets:
    - Older checkouts print `missing or mismatched pr-cost ledger header row`.
    - Update every checkout that shares the ledger, cron checkouts included, before the next `--record`.
    - Roll forward; do not revert.
    - The count line's stem changes from `row(s) recorded without a per-model breakdown` to `row(s) recorded with failed cells`, and each entry names its cells; update any wrapper that matches it.
    - A row the count line lists with `review_rounds` carries an empty cell. `--record --force --pr N` refills it only while every session of that PR is inside `cleanupPeriodDays`, at the cost `docs/pr-cost.md`'s re-record contract states.
- **`### Fixed`, one entry.** `review-round-cost` and `review-cost-by-size` exclude a turn whose usage is not an object or whose token fields are not non-negative integers. Before, such a turn raised, priced negative, or had a float, boolean or numeric-string count coerced to an integer. Both commands print a `MALFORMED USAGE` line when any turn was excluded. A re-run over the same transcripts can give different figures than one made before this fix.

## Critical files

Three sequential `code-writer` dispatches, all in this feature worktree. Their file sets do not overlap. Dispatch 2 reads Dispatch 1's round-dict keys, `REVIEW_ROUND_RULE`, `rounds_only`, `session_pricing._usage_is_invalid` and the shared corpus helpers, and Dispatch 3 documents what both land, so each starts only after the previous one's verification passes. The docs form their own dispatch because they describe every message and constant the first two land, each stated verbatim in this plan, except the two bound values, which Dispatch 3 reads from `pr_cost_ledger.py`.

No dispatch prompt quotes or links issue #1364's analysis. The Ask's quote is the only issue text any dispatch needs, and Verification step 5 is the session's own.

**Dispatch 1: round pass and report footers (M1).**
- `claude/.claude/scripts/transcript_analysis/review_rounds.py`: `REVIEW_ROUND_RULE`, `_DispatchTally`, the token helper, the four new round-dict keys, the malformed-usage guard at both pricing sites, `rounds_only`, the footer line, and the docstrings. Reuse `pricing._token_counts`, `pricing._pricing_variant`, and `_price_turn`'s third return value.
- `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`: `_JoinCounts.invalid_usage_records` and its diagnostics line.
- `claude/.claude/scripts/transcript_analysis/session_pricing.py`: receives `_usage_is_invalid` verbatim, beside `_validate_usage_token_fields`.
- `claude/.claude/scripts/transcript_analysis/cost.py`: drops the definition; four call sites and six mentions point at `session_pricing._usage_is_invalid`.
- `claude/.claude/scripts/transcript_analysis/period_cost.py`: the `:539` call site, plus the `session_pricing` import.
- `claude/.claude/scripts/select-tests.py`: one `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` entry.
- `claude/.claude/scripts/tests/test_transcript_review_rounds.py`: the M1 tests, the three new builders, the golden test, the `rounds_only` test and the footer tests. Reuse `_ROUND_SHAPE_CORPUS_BUILDERS`, the agreement guard's per-param fixture (`:1184`), and `_priced`. Move the free-text corpus builders (`:94-176`) and `TestOrchestratorDispatchFreeTextNeverSurfaces._write_corpus` (`:1450-1465`) to the new helper module below, and import them from it.
- `claude/.claude/scripts/tests/_review_round_corpus_helpers.py` (new, row 56): those builders moved verbatim, with `_write_corpus` as the module function `_write_orchestrator_free_text_corpus`. It gains one keyword, off by default so the existing tests' corpus is unchanged, that adds a `/slash` round with sentinel args and an unpriced turn on a marker-named model. It imports from `.conftest` relatively, and its docstring is one line naming its users.
- `claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py`:
  - `_round_entry` (`:80-89`) gains an `invalid_usage_records` keyword, default 0, plus `main_tokens`, `agent_tokens` and `unpriced_tokens` at fixed empty values, so the join's new sum does not raise `KeyError` at its call sites.
  - The `:431-437` and `:469-478` fixtures give `invalid_usage_records` distinct primes, as `unpriced_turns` and `dangling` already have.
  - The new `_JoinCounts` field at `:409`, `:459`, `:488` and `:543`, the diagnostics-line tests, and the stub-fidelity test.
- Verification: steps 1 and 2, and step 3's Dispatch 1 list.

**Dispatch 2: ledger, capture and export (M2-M6).**
- `claude/.claude/scripts/transcript_analysis/pr_cost_ledger.py`: M2, and M3's codec, field tuples and bounds. Reuse `_reject_duplicate_json_keys`, `_MODEL_BREAKDOWN_KEY_RE`, `_MODEL_BREAKDOWN_LEAF_VALUE_MAX`, the decode error mapping, and the existing write postcondition, which already compares decoded JSON values.
- `claude/.claude/scripts/transcript_analysis/pr_cost.py`: M4 and M5. Reuse `_usd_to_micros`, `pr_cost_ledger._latest_pr_cost_row`, `corpus._parse_ts`, the `model_breakdown` degrade path's structure, `_check_model_breakdown_cell`'s membership checks as the shared helper, and `review_rounds.py:1314-1323`'s class-name-only print.
- `claude/.claude/scripts/transcript_analysis/pr_cost_export.py`: M6.
- `claude/.claude/scripts/tests/_pr_cost_helpers.py`:
  - Append `_PRE_REVIEW_ROUNDS_HEADER_LINE` (the pre-`invalid_usage_records` line plus `\tinvalid_usage_records`) to `_FROZEN_PR_COST_HEADER_LINES`, with a hand-written row line whose `invalid_usage_records` is nonzero, its parsed row, and a `_pre_review_rounds_row_line` renderer.
  - Add one table, derived from `_FROZEN_PR_COST_HEADER_LINES` zipped with `strict=True` against each header's renderer, parsed row and expected short-row hint, for every per-header test to iterate.
  - Add `"review_rounds": None` to `_sample_pr_cost_row` and to every parsed literal.
  - Add `_sample_review_rounds()` and a canonical-cell literal beside `_sample_model_breakdown`.
  - Move `_COUNT_LINE_STEM` to the generalized count line, and add stems for the start line, the pass-failure line, the round per-PR line and the guard refusal.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_ledger.py`:
  - The frozen-header, upgrade-literal, short-row-hint, codec and rename changes.
  - Replace the eight hand-copied header lists (`:317-326`, `:418-427`, `:434-452`, `:466-478`, `:1085-1092`, `:1128-1136`, `:1277-1284`, `:1301-1308`) with the derived table, and pass the hint as a param instead of the `if`/`elif` at `:492-498`.
  - `_current_ledger_text_with_cell` takes the column, so the codec matrix shares the `model_breakdown` suite's primitive cases.
  - Fix the tests this change breaks: `test_a_change_in_any_single_column_is_refused_naming_that_column` (`:1186-1194`) needs a populated prior `review_rounds`, and the trailing literals at `:320-324`, `:381`, `:439`, `:443`, `:448`, `:736`, `:768` and `:1457` gain the new cell.
- `claude/.claude/scripts/tests/test_transcript_pr_cost.py`:
  - The capture, containment, degrade, laziness, guard, hand-edit and leak tests.
  - Replace `test_resolves_project_scope_exactly_once_across_multi_pr_run` (`:730-766`) with the laziness matrix.
  - Rebuild the editor-stripped-trailing-tab case (`:2071-2110`) on `review_rounds`, now the trailing column.
  - Move `_count_line` (`:1647-1652`) and its call sites (`:1683`, `:1778`, `:1906`, `:1926`) to the entry format.
  - Pass `review_rounds_cell=None` at the two direct `_new_pr_cost_row` callers (`:493`, `:1094`).
  - Fix the `cost._usage_is_invalid` mention at `:1711`.
  - Import the free-text sentinel corpus from `_review_round_corpus_helpers.py` rather than rebuild it.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_export.py`: the triage, passthrough, filter and stderr-line tests.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_export_accounts.py`: the provenance key set at `:546-549`, and the multi-account `review_rounds_dropped_keys` sum cases from Verification 3's export list.
- Verification: steps 1 and 2, and step 3's Dispatch 2 list.

**Dispatch 3: docs and changelog (M7).**
- `docs/pr-cost.md`, `docs/transcript-analysis.md`, `docs/transcript-analysis-architecture.md`, and `CHANGELOG.md`.
- Verification: steps 1 and 4. The session then runs step 5.

No other file is edited. `pricing.py`, `corpus.py`, `scope.py`, `transcript-analysis.py`, `test_transcript_cli_bootstrap.py`, `test_transcript_pr_cost_gh.py`, `test_transcript_analysis_branch_attribution.py`, `test_transcript_ledger_common.py`, `test_transcript_gh_cli.py` and `test_transcript_session_pricing.py` stay unchanged.

## Verification

1. **Scoped suite.** After each dispatch, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` from the worktree root. It takes a long time, so run it in the background and wait for its exit. Pass means exit 0. Dispatch 1's run includes `test_select_tests.py`, which passes unmodified once `session_pricing.py` is in the frozenset.
2. **Lint.** `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` exits 0.
3. **Tests that must exist and pass.** Each new test that pins changed behavior is shown failing before its production edit, and the dispatch's return reports those runs. A test that passes before the change only as the pair of a refusal or degrade test needs no red run.

   Dispatch 1, `test_transcript_review_rounds.py`:
   - **Tokens.** A window whose main thread runs one priced model, one turn `speed: "fast"`, dispatches onto a second model, and that dispatch spawns a nested one on a third model or variant, all with distinct nonzero per-class counts, yields literal `main_tokens` and `agent_tokens` with every class per group, the nested level included.
   - **Variant sweep.** `speed`, `inference_geo` and both file under `fast`, `us_geo` and `fast_us_geo`, mirroring `TestPrCostBranchTotalsByModel` (`test_transcript_pr_cost.py:1114`).
   - **Outside a window.** A dispatch spawned outside every window, carrying one valid turn and one malformed-usage turn, adds only the valid turn's dollars to `branch_totals`, and its tokens and `invalid_usage_records` to no round. A main-thread malformed-usage turn outside every window appears in no round and is excluded from `branch_totals`.
   - **Unpriced.** An unpriced main-thread turn and an unpriced dispatch turn add their totals to `unpriced_tokens`.
   - **Malformed usage.** A non-numeric main-thread usage field and a negative dispatch usage field raise nothing, add to `invalid_usage_records`, and are excluded from dollars, tokens and `branch_totals`. The malformed record's own spawn block is still priced.
   - **Golden.** Per M1, with the agreement guard green over the three new builders. Acceptance evidence in the return: each of these mutations turns at least one param red, run one at a time:
     - changing `detect_round_windows`' `window_end`;
     - dropping the dispatch-level dedup (`review_rounds.py:294`);
     - dropping the main-thread dedup (`:392`);
     - dropping the `_attribute_carried_branch` call.
   - **`rounds_only`.** Over every `_ROUND_SHAPE_CORPUS_BUILDERS` param plus a session that opens no round but dispatches a subagent, `rounds_only=True` returns rounds equal to the default call's, and its result has no `branch_totals` key. A spy on `corpus._index_subagent_dispatches` sees only sessions that open a round. A session that opens no round and holds a `{"type": "user", "timestamp": 12345}` record raises `AttributeError` under the default call and not under `rounds_only=True`.
   - **Footer.** One malformed in-window turn prints the `MALFORMED USAGE` line with count 1, under multiple roots in that root's block only. A run with none prints no such line, and `--pooled` prints none.
   - **Moved builders.** `TestOrchestratorDispatchFreeTextNeverSurfaces` passes unchanged over the helper module's corpus, with the new keyword off.

   Dispatch 1, `test_transcript_review_cost_by_size.py`:
   - `_JoinCounts.invalid_usage_records` sums over joined rounds only, pinned by the prime fixtures, and the diagnostics line prints exactly when it is nonzero.
   - **Stub fidelity.** `_round_entry`'s key set equals the key set of a real round from `compute_review_round_costs` over a minimal one-round corpus.

   Dispatch 2, ledger (`test_transcript_pr_cost_ledger.py`):
   - The pre-`review_rounds` frozen line equals the production tuple, and the current header equals the newest frozen line plus `\treview_rounds`.
   - Every per-header test iterates the derived table, so the new header gets the postcondition refusals, the returns-`True`-on-upgrade check and mode preservation.
   - A hand-written row under every frozen header parses with `review_rounds` `None`, and survives the upgrading write as its original cells plus the defaulted ones, ending in a tab.
   - The short-row hint for every header matches M2, including a current-header row whose trailing tab was stripped and a header whose last column has no default entry.
   - **Codec accepts** `_sample_review_rounds()`'s canonical cell, `""`, `{"rule":1,"rounds":{}}`, an empty token object, an unknown integer round key that survives a rewrite and the postcondition, and a raw cell exactly at the read bound, with the bound patched small.
   - **Codec refuses, position by defect**, from one parametrized table. Each case asserts the exact `line N: malformed review_rounds (<rule>)` text against a hand-written rule literal, that a sentinel key stays out of the message, and that `__cause__` and `__context__` are `None` (as the `model_breakdown` suite pins at `test_transcript_pr_cost_ledger.py:600`, `:614` and `:629`):
     - integer positions (`rule`, one count field, one token leaf, an unknown round key, `after_merge`): one representative each of non-integer or bool, out of range, and a 5000-digit literal, plus a 2 for `after_merge`;
     - key positions (skill, unknown round key, model, variant, class): one each of empty, uppercase and 65 characters;
     - the full primitive set, shared with the `model_breakdown` suite, once at one integer position and once at one key position: float, bool, negative, above `2**63 - 1`, NaN, Infinity, non-ASCII, leading dot, leading hyphen and trailing newline, with hyphen and underscore after the first character accepted;
     - structure: a missing or extra top-level key, `rounds` not an object, a skill value not a list, a round not an object, a missing required field, an empty model or variant object, a token object that is a list, a model value that is a number, a variant value that is a string, a duplicate key, deep nesting, invalid JSON, `null`, and a raw cell one character over the patched read bound.
   - **Rule reachability.** The set of expected rule literals across the refusal table equals `_REVIEW_ROUNDS_DECODE_RULES`.
   - **Bound pair.** With nothing patched, a valid cell longer than `_REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS` decodes, and `_check_review_rounds_cell` refuses it with `size`. `_REVIEW_ROUNDS_CELL_READ_MAX_CHARS` equals a hand-written literal, pinned as the frozen header lines are.

   Dispatch 2, capture (`test_transcript_pr_cost.py`):
   - **Literal cell.** Under `_assume_hand_computed_dollar_rates`, a fixture with two code-review rounds, one plan-review round, one ready-for-review round, and a dispatched subagent file with known tokens records a literal cell with `rule` 1. The dispatch's `agent_usd_micros` and `agent_tokens` count once. The spy asserts the scalar pass's call carries `include_subagents=True`, the round pass's carries `include_subagents=False` and `roots=[root]`, and `compute_review_round_costs` receives `rounds_only=True`.
   - **Builder key set.** `_build_review_rounds_cell`'s round keys equal M3's three field tuples, its `rounds` keys equal `REVIEW_SKILLS`, and zero rounds give every list empty.
   - **`after_merge`.** With explicit timestamps, since default `_priced` timestamps fall after `_merged_pr`'s `mergedAt`: 1 after, 1 at equality, 0 before, and absent for an unparseable opening timestamp or `merged_at`.
   - **No activity.** A single-account `--pr` with no local activity, and the renamed-branch zero-activity row, record an empty cell, exit 0, and print no count line.
   - **Partial data.** Nonzero `unpriced_turns`, `dangling` and `invalid_usage_records` record as is, with exit 0 and no degrade line.
   - **Laziness matrix**, counting `_resolve_project_scope` calls. No round scan runs for every PR captured, an as-of skip, a branch with no local activity, or an `--all-accounts --pr` account skip. One round scan runs for an unforced uncaptured PR, for `--force` over a captured row, and for two uncaptured PRs. Each scanned account prints one start line.
   - **Scan outside the lock.** In the matrix's scan cases, the scan spy opens `<ledger>.lock`, the one lock file both the account read and the per-branch write take (row 52), on its own file descriptor and tries a non-blocking exclusive `flock`. It asserts the lock was free on every call. Acceptance evidence in the return: moving the getter call inside the per-branch lock turns it red.
   - **Pre-lock skip.** Without `--force`, a PR the pre-lock read finds captured prints today's already-captured line, runs no scan, and exits 1 under single-account `--pr`. A row a concurrent `--record` appends between the pre-lock read and the locked read gives the same line from the locked check.
   - **Pass failure.** A scan raising an exception whose message carries a sentinel prints the failure line once, with the class name and not the sentinel. Over two uncaptured PRs it scans once, records both rows with `model_breakdown` populated and `review_rounds` empty, lists both in the count line, and exits 1. Under `--all-accounts`, only the failing account's rows lose the cell.
   - **Real trigger.** With no patching, a session that opens a code-review round on a priced branch and also holds a `{"type": "user", "timestamp": 12345}` record, with no `gitBranch` and no usage, records the scalar row, leaves `review_rounds` empty, prints `AttributeError` once and not the message, and exits 1.
   - **`BaseException` passes through.** A scan raising `KeyboardInterrupt`, and a builder raising `SystemExit`, each propagate out of `--record` and write no row for that PR.
   - **Check rules.** `_check_review_rounds_cell` unit tests give each of `size`, `shape` and `model-membership`, and each of `label-membership`'s two disjuncts (a variant key outside `_PRICING_VARIANTS`, a class-key set unequal to `_TOKEN_CLASSES`), a minimal violating cell pinned against a hand-written cause map, plus one planted-key case per disjunct whose key stays out of the exception text. They also pass an empty token object and a `None` cell. `size` runs with the write bound patched small, and the existing `_check_model_breakdown_cell` suite stays green over the shared helper.
   - **Containment.** Through the patched `_build_review_rounds_cell`, a cell failing each rule and a builder raising an exception with a sentinel message (`build`) each null the cell, print the per-PR line with the right rule and cause and no sentinel, record the remaining branches, and exit 1. A planted model key never reaches stderr.
   - **Rollback lever.** `_build_review_rounds_cell` returning `None` leaves populated prior rows untouched, records an empty cell, prints no per-PR or count line, and exits 0.
   - **Count line.** `_failed_cells_count_entry` unit tests cover each cell set with and without an account prefix. One integration run with both cells degraded gives one entry naming both.
   - **Guard truth table** on `_review_rounds_force_refusal`. P `None` allows for every N, an absent N included. Each N absence gives its literal reason over a populated P and over an all-empty-lists P. An all-empty-lists P with an all-empty N at the same or a higher rule allows, as does a P holding a skill key with an empty list that N lacks. A lower N rule refuses. At the same rule, a shorter list, a skill P holds non-empty that N lacks, one skill longer while another is shorter, and a lower summed `agents` each refuse. At the same rule, equal or longer lists with equal or higher `agents`, more `dangling` alone, and a skill only N holds each allow, and any higher N rule allows, all-empty lists included.
   - **Guard integration**, one per outcome: a refusal exits 1 with the ledger bytes unchanged, an `--all-accounts` refusal counts as skipped and exits 1 after the summary, and an allowed forced re-capture appends.
   - **Hand edits.** `TestPrCostPrescribedHandEdits` gains a `review_rounds` case. Read, `--record` and export refuse a corrupt cell with a message naming the column and no cell text. Blanking the cell is accepted. Blanking it and then stripping the trailing tab is refused with the append-a-tab hint, and restoring the tab is accepted.
   - **Leak tripwire**, one test parametrized over four run outcomes: success, pass failure, a degraded cell, and a guard refusal. The corpus is `_write_orchestrator_free_text_corpus` with the new keyword on (orchestrator prompt and description, `Skill` args, `/slash` command args, and a marker-named unpriced model), plus a sentinel branch name and a non-review skill name.
     - It first asserts the outcome happened: a captured round in the cell for success and for the refusal's prior row, and the outcome's line for the other two, so it cannot pass vacuously.
     - It then finds no sentinel in the ledger bytes, the export bytes, or the stdout and stderr of record and export.
     - On success, the marker model's tokens appear only in `unpriced_tokens`.
     - Read mode and export console output never contain the cell's model keys.
   - **Cross-account success path.** Two accounts with the same raw branch name and different rounds each record only their own.
   - **Upgrade notice.** A pre-`review_rounds` ledger prints it once.
   - **Bounds.** A test measures a representative synthetic round's encoded length (main thread on one model in `standard` and `fast`, a dispatch on a second model, every class at a nine-digit count, every count field nonzero, `after_merge` present) and asserts the write bound admits at least 1000 of them.
   - **Fresh interpreter.** `test_transcript_analysis_pr_cost_help_exits_zero` stays green, loading the new import chain (see "Alternatives set aside").

   Dispatch 2, export (`test_transcript_pr_cost_export.py`, `test_transcript_pr_cost_export_accounts.py`):
   - Triage covers `review_rounds`.
   - A known-vocabulary cell exports value-identical in canonical encoding, with a count of 0 and no stderr line.
   - Hand-counted filter cases, each exporting a cell that still decodes, with `review_rounds_dropped_keys` equal to the literal count:
     - an unknown skill holding two rounds: 1;
     - an unknown round key on each of two rounds: 2;
     - an unknown model holding two variants of five classes each: 1;
     - a known model whose only variant is unknown: 1, and the emptied model is removed;
     - a known model and variant whose only classes are two unknown ones: 2, and the emptied variant and model are removed;
     - a rate-table model ID at the skill position, and a skill name at the model position: 1 each.
   - **Sentinels.** A key-shaped sentinel at each of the five key positions is absent from the export bytes and from the export's stdout and stderr, and the stderr line prints the count with no key text.
   - **Multi-account sum** (`test_transcript_pr_cost_export_accounts.py`): two accounts whose cells drop different nonzero counts give `review_rounds_dropped_keys` equal to their sum, and the stderr line prints that same sum with no key text. A second case where only the first account drops and the second drops zero fails an assign-instead-of-add or last-account-wins loop.
   - The provenance key-set pin includes `review_rounds_dropped_keys`.
4. **Reference checks.**
   - `grep -rn "cost\._usage_is_invalid" claude/ docs/` prints nothing.
   - `grep -n "def _usage_is_invalid" claude/.claude/scripts/transcript_analysis/*.py` prints only the `session_pricing.py` line.
5. **Redaction.** The session runs this after Dispatch 3's verification and again before every push of this branch, from the worktree root, writing nothing inside the repository.
   - **Setup.** Run `git fetch origin main`. Before the implementation commit, also run `git add --intent-to-add claude/.claude/scripts/tests/_review_round_corpus_helpers.py`, so the one new file the change creates shows in the diff. `git status --porcelain --untracked-files=all` must then list no `??` path, because an untracked file is invisible to the diff. `git diff --merge-base origin/main` compares the working tree, committed or not, with the branch's merge base (row 54).
   - **List.** Run `git diff --merge-base origin/main -U0 | grep -E '^\+[^+]' | grep -oE '[0-9]+(\.[0-9]+)?' | sort -u`. The output must be non-empty. Empty output means the diff or the pipeline failed and the check has not run.
   - **Read.** The session reads the list once against `gh issue view 1364 --json body`. Each number must be a code constant, a header width, a line or row citation, a test fixture value, or a date. A number that restates a figure from that issue's analysis, other than a bucket bound or the direction "rising with PR size", is removed or reworded. The list shows bare tokens, so it cannot tell a small integer that restates an analysis figure from a row citation. For the public prose, also read each digit-bearing added line with its sentence: `git diff --merge-base origin/main -U0 -- docs CHANGELOG.md .claude/plans | grep -E '^\+[^+].*[0-9]'`. Run both commands from the worktree root. Empty output from the second means the check has not run. Before every push, `git log origin/main..HEAD --format=%B` gets the same read. Before `gh pr create`, before every PR-body edit and before posting any PR comment, the draft text gets the same read, because a read after sending finds a figure already public.
   - The cumulative review's `ciso-reviewer` is the second reader of the same numbers. No pipeline decides this: whether a number derives from the barred analysis is a judgment, and a script that tried to decide it failed open or tripped on fixture dates in review.

## Out of scope

- **Any reader of `review_rounds` beyond the export and the guard:** `review-cost-by-size` reading the ledger, a combiner implementation, and read-mode output. The engineer scoped this change to recording (row 1). The PR body's follow-up list names that reader as the next piece, edited in place there rather than filed as a new issue.
- **Backfilling rows captured before this change**, with no new flag, no candidate-listing docs, and no docs or CHANGELOG text presenting `--force` as a backfill (row 44). The guard still allows a forced re-capture over a row without rounds (M5), because it protects captured rounds only.
- **Passing `rounds_only=True` from `review-cost-by-size`**, which reads only rounds too (row 51). It would change a merged command's failure surface, so it is its own change.
- **A console notice for a higher-rule re-capture with fewer rounds.** M5's residual is documented instead.
- **A pooled `--show-withheld` share for malformed usage.** See "Alternatives set aside".
- **Skip-and-count for records outside the transcript format**, such as a non-string timestamp or a non-object `*.meta.json`. That is the decode-policy decision `corpus.py:45-46` defers (row 45).
- **Containing `_build_model_breakdown_cell`'s `OverflowError`.** `docs/pr-cost.md:92` documents it as accepted at token counts far beyond any real PR, where the scalar row is already unusable.
- **Extending the `--force` guard to the scalar columns or `model_breakdown`, adding an override, or making it a full retention check.** The engineer chose a rounds-only guard (row 4). Its same-rule completeness case is the extent of its retention sensing.
- **Extending the export vocabulary filter to `model_breakdown`.** Its keys stay shape-checked only, as `docs/pr-cost.md:60` documents. The PR body's follow-up list names this sibling extension, edited in place there rather than filed as a new issue.
- **A runtime opt-out for the round pass, and a downgrade recipe** (row 46).
- **Mechanically enforcing `rule` bumps for attribution code no golden shape exercises.** The constant's comment carries that residual.
- **Raising `cleanupPeriodDays`.** See "Alternatives set aside".
- **Moving `model_breakdown` to the tolerant contract, or renaming `_MODEL_BREAKDOWN_KEY_RE` and `_MODEL_BREAKDOWN_LEAF_VALUE_MAX` to column-neutral names.** The rename would add a `pricing.py:60` comment edit for no behavior change.
