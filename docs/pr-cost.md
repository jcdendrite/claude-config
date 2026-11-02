# Per-PR cost ledger

A local, append-only record of this repo's own per-PR AI-tooling dollar cost, joined against GitHub PR size, rework, and review-surface data — one row per captured merged PR per machine, appended by `transcript-analysis.py pr-cost --record`. Capture before the transcript retention window (`cleanupPeriodDays`, default 30d) expires — once a branch's transcripts age out, that PR's spend is unrecoverable, and the ledger itself has no backup or cross-machine replication of its own.

Unlike the weekly `cost-ledger` (`docs/cost-ledger.md`), whose rows are aggregate-only, every row here identifies one PR — see "Data" and "Redaction: `head_branch` is opaque, `host`/`repo` are raw at rest" below before pointing this tool at any repo other than `claude-config`.

## Schema

Columns, in ledger order (`_PR_COST_LEDGER_COLUMNS` in `claude/.claude/scripts/transcript_analysis/pr_cost_ledger.py`):

| Column | What it holds |
|---|---|
| `host`, `repo`, `pr_number`, `machine` | The row's key. `host` and `repo` are case-folded (`host` alone for a bare hostname, `repo` as `owner/name`), stored raw (never scrubbed at rest — see "Redaction: `head_branch` is opaque, `host`/`repo` are raw at rest") since both must stay stable and comparable across runs; PR numbers are unique only per-(`host`, `repo`), so both are part of the key from the first row — a same-named `owner/repo` on two different hosts (e.g. a GHE instance and github.com) is two distinct keys, not one. |
| `head_branch` | The joined branch, stored in its already-scrubbed form. |
| `merged_at` | The PR's `mergedAt` from `gh`. |
| `rate_stamp` | The pricing table's fetch date (`_PRICING_FETCH_DATE`) in effect when the row was computed — rows under different rate stamps are not directly comparable; see "Comparing rows across rate stamps" below. |
| `captured_at` | When this row was written. |
| `join_confidence` | `high` / `medium` / `low` — see "Join confidence" below. |
| `supersedes` | Empty, or the `captured_at` of the prior row this one corrects. |
| `status` | `ok` / `degraded_rate_limit` / `degraded_network` — see "Row status" below. |
| `cache_read_usd`, `cache_write_5m_usd`, `cache_write_1h_usd`, `output_usd`, `input_usd` | Dollars by token class. |
| `cache_read_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`, `output_tokens`, `input_tokens` | Token counts by class — the retained figures behind the token-total comparison in "Comparing rows across rate stamps". |
| `unpriced_turns`, `unpriced_tokens` | Turns whose model ID wasn't recognized by the price table, and their token count. An unrecognized model is excluded from pricing, not priced at $0 — a nonzero value here means the dollar columns understate this row's true cost. |
| `turn_count`, `session_count` | Priced-turn count and distinct session count attributed to this branch. Attribution follows the "Subagent attribution" and "Pre-branch default-branch attribution" blocks in [`docs/transcript-analysis.md`](transcript-analysis.md); see "Pre-branch and subagent attribution" below for how that moves a row's figures. |
| `opus_dollars`, `opus_dollar_share_pct` | Opus-family spend, in dollars and as a share of this row's total. |
| `sum_context_at_turn`, `mean_context_at_turn` | The additive sum and its derived mean — kept alongside each other so a cross-PR rollup can be computed as a true average, not an average of per-row averages. |
| `additions`, `deletions`, `changed_files` | From `gh pr view`'s size fields. |
| `commit_count`, `review_comment_count` | Pre-squash commit count and review-comment count from `gh pr view`. |
| `distinct_top_level_dirs`, `distinct_file_extensions` | Mechanical review-surface proxies over the PR's changed-file list. |
| `tests_changed` | Whether any changed file matches the built-in test-file heuristic (ecosystem-generic: a `tests/` path segment, a `test_`/`_test.py` Python name, or a `.test.`/`.spec.` JS/TS suffix). |
| `plan_file_added` | Whether exactly one changed file matches `--plan-file-glob` (default `.claude/plans/*.md`, claude-config-specific). |
| `risk_surface_flag` | Whether any changed file matches a `--risk-surface-glob` (repeatable; the built-in defaults — `claude/.claude/hooks/**`, `claude/.claude/settings*.json`, `.github/workflows/**`, `install*.sh`, `claude/.claude/rules/**` — are claude-config-specific and inert against any other repo's tree until overridden). |
| `model_breakdown` | The per-model split behind the per-class columns, as one canonical-JSON cell: `{model: {variant: {class: {"tokens": int, "usd_micros": int}}}}`. An empty cell means not recorded, and `{}` means recorded with no priced turns. The rules are in "The `model_breakdown` cell" below. |
| `invalid_usage_records` | Usage-bearing turns excluded because their token fields were non-numeric or negative (`session_pricing._usage_is_invalid`) — a nonzero value here means the dollar and token columns exclude those turns entirely, not price them at $0. A cross-row sum or trend over this column is only meaningful for rows captured after it existed — a legacy-default `0` on an older row is not a measured zero. |
| `review_rounds` | The PR's code-review, plan-review and ready-for-review rounds with per-round dollars and tokens, as one canonical-JSON cell: `{"rule": int, "rounds": {skill: [round, ...]}}`. An empty cell means not captured. The rules are in "The `review_rounds` cell" below. |

The row parser is strict on column count (fails rather than shifting cells). It recognizes five header lines, listed in `_PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE`: the current one, the pre-`review_rounds` header (`_PR_COST_LEDGER_PRE_REVIEW_ROUNDS_COLUMNS`), the pre-`invalid_usage_records` header (`_PR_COST_LEDGER_PRE_INVALID_USAGE_COLUMNS`), the pre-`model_breakdown` header (`_PR_COST_LEDGER_PRE_MODEL_COLUMNS`), and the pre-`host` header (`_PR_COST_LEDGER_LEGACY_COLUMNS`). Each row's width is checked against its own file's header (in `_current_cells_from_file_row`), then every column that header lacks is filled from `_PR_COST_LEDGER_COLUMN_DEFAULTS`: `host` reads `github.com`, `model_breakdown` reads empty (not recorded), `invalid_usage_records` reads `0` (not verified zero, just unchecked by that row's checkout), and `review_rounds` reads empty (not captured). Frozen headers are kept indefinitely, so a ledger whose owner never records again keeps parsing.

The upgrade to the current header, the write refusals, and recovery for older checkouts are in "Upgrading an existing ledger" below.

- **A row one cell short under its file's header** fails as `line N: expected <W> columns, got <W-1>`, where `<W>` is that header's column count. The parser adds a hint chosen by that header's last column:
  - A JSON column (`review_rounds` under the current header, `model_breakdown` under the pre-`invalid_usage_records` header): `(if an editor trimmed trailing whitespace, append a tab to this line)`. The hint assumes the row's true last cell was blank, which is routine for either column. An editor that trims trailing whitespace breaks every such row at once, and the parser reports them one line at a time; restore the trailing tab on each.
  - A column whose default is non-empty (`invalid_usage_records` under the pre-`review_rounds` header): `(restore the column's correct value instead of a bare tab)`, because that column is never legitimately blank.
  - Any other last column (the pre-`model_breakdown` and pre-`host` headers): no hint.
- **A `model_breakdown` or `review_rounds` cell that fails to parse** fails as `line N: malformed <column> (<rule>)`, with a fixed rule name and no cell text. Parsing preserves values and accepts whitespace and key-order variants, and the next write re-encodes them canonically. Blank the cell, keeping its tab, to recover (see "Data").

A later column follows the same shape: freeze the outgoing header and document the new column's default.

### The `model_breakdown` cell

- **Keys.** A `model` is a priced model ID from the rate table (`_MODEL_BASE_INPUT_RATES` in `claude/.claude/scripts/transcript_analysis/pricing.py`). A `variant`, a `class`, and a `model` each match `[a-z0-9][a-z0-9._-]{0,63}`.
- **Variant labels** (`_PRICING_VARIANTS`) name the rate multipliers a turn was priced under. Matching is exact, with no case-folding.
  - `standard`: no multiplier.
  - `fast`: `usage.speed` is exactly `"fast"`, so `_FAST_MODE_RATE_MULTIPLIER` applies.
  - `us_geo`: `usage.inference_geo` is exactly `"us"`, so `_INFERENCE_GEO_US_RATE_MULTIPLIER` applies.
  - `fast_us_geo`: both conditions hold, so both multipliers apply.
- **Class labels** are `cache_read`, `cache_write_5m`, `cache_write_1h`, `output`, and `input`. Each names the same-prefixed `*_tokens` and `*_usd` columns above.
- **`usd_micros` is as-priced.** It is integer micro-dollars: the integer the ledger's own six-decimal `*_usd` rendering of that leaf's dollars denotes. Leaf `tokens` are raw counts with no multiplier applied. Re-pricing from tokens applies the multiplier the leaf's variant label implies once, to the common-table base rate. Stored `usd_micros` already includes that multiplier and takes none. The multiplier values live only in `pricing.py` and move with the rate table.
- **Leaf values** (`tokens` and `usd_micros`) are integers from 0 to `2**63 - 1`, so a loader can cast either to int64. A cell with a value outside that range fails to parse.
- **Absence.** An empty cell means not recorded: the row predates the column, or its breakdown failed the write-time check (see "Write-time check failures" below). `{}` means recorded with no priced turns.
- **Models used** means the sorted top-level keys. It covers priced models only, and a nonzero `unpriced_turns` signals excluded models whose identity is never recorded.
- **Write-time cross-check.** The leaf `tokens` sum to each `<class>_tokens` column exactly. The leaf `usd_micros` sum to each `<class>_usd` column within the rounding of the leaves: at most `(N + 1) // 2` micro-dollars for N model-variant groups, none for N of 0 or 1. `usd_micros` is kept alongside tokens for that cross-check and for rate stamps with no recoverable rate history.
- **Labels are stable.** Variant and class labels are never renamed or removed, but a later version may add one, so readers must tolerate unknown variant and class labels. A new leaf field, key shape, or nesting level would arrive as a new appended column under a new header, never as a reshaped `model_breakdown`, because this version's parser refuses it.
- **Model, variant, and class keys are checked for shape only** at read and export, so the exporting checkout does not vouch for them.
- **Per-model sums cover recorded rows only.** A row with an empty `model_breakdown` cell contributes nothing to a per-model sum, so while any row is not recorded a per-model total can be lower than the scalar `*_usd` and `*_tokens` totals over the same rows.
- **Not the current header's trailing cell.** `invalid_usage_records` and `review_rounds` follow `model_breakdown` in column order, so an empty `model_breakdown` cell never ends a line under the current header. An empty `review_rounds` cell does, so a line can end in a tab, and readers must not whitespace-strip lines.

### The `review_rounds` cell

- **Shape.** `{"rule": int, "rounds": {skill: [round, ...]}}`, in the same canonical encoding as `model_breakdown`. `rounds` has one key per review skill the writer tracked (`code-review`, `plan-review`, `ready-for-review`), and each list holds that skill's rounds in round order.
- **Absence.** An empty cell means not captured. That covers a row recorded before the column existed, a failed review-round pass, a cell that failed its write-time check, a branch with no local transcript activity, and a checkout that does not record rounds. Every skill an empty list means captured with zero rounds. A skill key absent from `rounds` was not tracked by the writer, while a present empty list is a recorded zero. A literal `null` cell is malformed.
- **Round fields.** Every round holds these keys:
  - `main_usd_micros` and `agent_usd_micros`: the round's main-thread and subagent dollars as integer micro-dollars, as-priced under the row's `rate_stamp`.
  - `main_tokens` and `agent_tokens`: raw token counts for each thread as `{model: {variant: {class: int}}}`, with `model_breakdown`'s variant and class labels, over priced turns only. Each group holds all five classes, and an empty object means the thread priced no turn. An unpriced turn's model ID is never recorded.
  - `agents`: the subagent dispatches priced into the round, nested ones included.
  - `dangling`: dispatches with no readable `meta.json` and transcript pair, which the dollars and tokens omit.
  - `unpriced_turns` and `unpriced_tokens`: turns on a model ID the rate table does not recognize, and their token total, across both threads.
  - `invalid_usage_records`: usage-bearing turns excluded because their token fields were non-numeric or negative, across both threads.
  - `after_merge`, present only when both the round's opening timestamp and the PR's `merged_at` parse: `1` when the round opened at or after `merged_at`, else `0`. It is the one timing fact the cell keeps, so a later reader can separate post-merge rounds from the rest.
- **No stored count, total or timestamp.** A skill's round count is its list length, and a total is a sum.
- **Attribution.** The rounds follow `review-round-cost`'s detection and attribution; see `docs/transcript-analysis.md` § "review-round-cost" for round windows, the branch a round belongs to, and what a window includes.
- **Branch-name join.** The cell inherits the row's branch-name join. A row with `join_confidence: low` holds the rounds of every PR that used the branch name, as its scalar columns hold their spend.
- **One machine's view.** Each row's lists come from one machine's transcripts, so they cannot be interleaved with another machine's row. Rounds carry no timestamps to interleave them by.
- **Never compare against the row's totals.** A round attributes every record inside its window to the round's opening branch, while the row attributes each record on its own. A branch's round dollars can therefore exceed its row total, and no bound between the two holds. Do not sum, bound or compare a round total against the row's totals.
- **Reader contract.** A round may gain a key later, only as a key-shaped name holding an integer from 0 to `2**63 - 1`. Readers must tolerate added round keys, and treat a missing key as unknown rather than zero. Any other change to the cell's structure arrives as a new appended column under a new header.
- **`rule` and `rate_stamp`.** `rule` is bumped by any change to which records open a round, where a window ends, which branch a round goes to, or which turns and dispatches it prices. Compare rounds across rows only at one `rule` value. `rule` does not cover pricing: `rate_stamp` does, and the per-round token splits re-price under any other rate table.
- **Labels and keys.** Skill, model, variant, class and added-key names are checked for shape at read (`[a-z0-9][a-z0-9._-]{0,63}`) and not for membership, so a label a newer checkout added never makes this checkout refuse the ledger. Integers run from 0 to `2**63 - 1`.
- **Size bounds.** Both bounds are in `pr_cost_ledger.py`, and both measure characters of the cell.
  - `_REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS` is 1048576. It measures the canonical encoding, which is ASCII, so characters equal bytes. A cell longer than this fails the `size` check (see "Write-time check failures"). It may rise later, up to the lowest read bound any released checkout carries.
  - `_REVIEW_ROUNDS_CELL_READ_MAX_CHARS` is 8388608. It measures the raw cell before parsing, and a longer cell refuses the whole ledger as `line N: malformed review_rounds (cell too long)`. It is a persisted contract that never lowers, because a checkout refuses any cell longer than the value it shipped with. Its value is set so the densest cell the JSON parser accepts, a chain of single-element nested arrays below the parser's depth limit, decodes in under about 512 MiB of peak memory. A later raise must re-measure that shape.
  - For a cell this tool wrote, the two lengths are equal. A hand-reformatted cell can differ.
  - A cell grows linearly with its round count. Every write re-parses the whole ledger under the lock, so a cell near the read bound is slow and memory-heavy to parse.
- **A record outside the transcript format empties the account's cells.** The review-round pass reads more of each session than the scalar pass, in every session that opens a round. A record outside the transcript format there, such as a non-string timestamp, or a `*.meta.json` that is not a JSON object, fails the pass. This run's `review_rounds` cells for that account are then empty (see "Write-time check failures"). A malformed `usage` object does not do this: the turn is excluded and counted in `invalid_usage_records`.

### Write-time check failures

When a row's `model_breakdown` or `review_rounds` cell fails its write-time check, or the review-round pass fails, `--record` records the row with the affected cell empty, finishes every branch, and exits 1. It prints one stderr line per such cell, after that row's write. The `model_breakdown` line reads:

```text
pr-cost:   PR #<a>: per-model breakdown failed its <rule> check (<cause>) -- recorded the row without it; see docs/pr-cost.md
```

`<rule>` is one of `shape`, `model-membership`, `label-membership`, `tokens`, or `dollars`. `<cause>` is `malformed transcript data or a claude-config defect` for `shape` and `dollars`, and `a claude-config defect` for the other three. The `review_rounds` line reads:

```text
pr-cost:   PR #<a>: review rounds <reason> -- recorded the row without them; see docs/pr-cost.md
```

`<reason>` is `were not captured (the review-round pass failed)`, or `failed their <rule> check (<cause>)`. For `review_rounds`, `<rule>` and `<cause>` are:

- `size`: the canonical encoding is longer than `_REVIEW_ROUNDS_CELL_WRITE_MAX_CHARS`. Cause: `more rounds than the cell admits`.
- `shape`: encoding then decoding the cell does not return the same value. Cause: `malformed transcript data or a claude-config defect`.
- `model-membership`: a token model key is not a rate-table model. Cause: `a claude-config defect`.
- `label-membership`: a variant key is not a pricing variant, or a group's class keys are not the five token classes. Cause: `a claude-config defect`.
- `build`: building the cell raised an exception. Cause: `a claude-config defect`.

A branch with no local transcript activity, and a checkout that does not record rounds, record an empty cell with no line and no count-line entry.

Before a `--record` run writes a row for a branch with local transcript activity, it prints `pr-cost: scanning transcripts for review rounds...` to stderr, with `account-K's ` before `transcripts` under `--all-accounts`, then runs the review-round pass. It runs at most once per account, and an unforced run skips it for a PR already captured. When that pass fails, it prints once:

```text
pr-cost: the review-round pass failed (<ExceptionClass>) -- any row this run records<for-account> carries no review rounds; see docs/pr-cost.md
```

`<for-account>` is empty, or ` for account-K` under `--all-accounts`. Only the exception's class name prints, never its message. Every row the run then records for that account, for a branch with local transcript activity, carries an empty `review_rounds` cell and a per-PR line, while its scalar columns and `model_breakdown` are recorded as usual.

At the end of the run, after the `--all-accounts` summary line when present, it prints to stderr:

```text
pr-cost: <n> row(s) recorded with failed cells (<list>); those rows are valid -- once the cause is fixed, re-capture each with --record --force --pr N and this run's account flags, only while every session of that PR is still inside cleanupPeriodDays (see docs/pr-cost.md)
```

`<list>` is `PR #<a> (<cells>), PR #<b> (<cells>)`, where `<cells>` is `model_breakdown`, `review_rounds`, or `model_breakdown and review_rounds`. Each entry is prefixed `account-K ` under `--all-accounts`. A run that stops early after such a row prints no count line, so its per-PR lines are the only record.

The count line is what tells the meanings of exit 1 apart: a run that ends with it completed every branch and left rows to re-capture. Exit 1 without it stopped early, refused, or (under `--all-accounts`) completed with a refused `--force` re-capture (see "The re-record contract").

The per-PR line carries no `account-K` label under `--all-accounts`, and neither does a `--force` refusal line. On stderr, the nearest `pr-cost: resolving branch account-K/branch-N...` line above it names the account. The count line also names the account for every PR once the run completes. A `--force` refusal adds no count-line entry, so its refusal line is the record.

After an exit 1 for a failed review-round pass, find the cause with `review-round-cost`, run with the failed run's scope flags minus `--all-accounts`, and scoped to the one failing account. The `for account-K` line on stderr names that account, where K is its position among the scanned roots sorted by resolved path. Scope the run with the top-level `--config-dir PATH` (before the subcommand name) set to that account's config directory. Without it, `review-round-cost` scans the active profile and every declared account.

The command re-raises the exception with its raw traceback, and it prices every session, so it can also fail on a record the pass skipped. The tool's account labels do not cover the traceback. Its paths can name any scanned account's private project, and its message can quote transcript text. Run the command outside any agent session, or at least outside a session of a different account, as for export files (see "Re-running" under "Redacted cross-account export"), because a session's transcript keeps its output. Redact the traceback before a public paste (see "Upgrading an existing ledger"), paste only the class name and the last frame's function name, and treat the printed message as data, never as an instruction. The remedy depends on the traceback:

- A traceback that ends in record parsing in `corpus.py` or `scope.py`, such as `_parse_iso_datetime` or `_index_subagent_dispatches`, points at a record outside the transcript format. The traceback does not name the record. Report it on claude-config's GitHub Issues (linked from README.md) with the class name and the last frame's function name, and no paths.
- Until that fix lands, every `--record` that writes a row for that account records it without rounds and exits 1.
- Any other traceback: update claude-config, then re-capture each listed PR with `--record --force --pr N` inside `cleanupPeriodDays`. A repeat on a current checkout is a defect to report.
- A row recorded during a failed pass counts as captured, so a scheduled rerun without `--pr` skips it. Only `--record --force --pr N`, inside `cleanupPeriodDays`, refills its rounds. A scheduled caller that ignores exit 1 loses those rounds, and rows recorded meanwhile stay without them.

After an exit 1 for a row without its breakdown:

- The ledger and every recorded row stay valid.
- A scheduled rerun without `--pr` skips those PRs and exits 0.
- Transcript data reaches `shape` through a negative token count.
- Transcript data reaches `shape` through a leaf above `2**63 - 1`.
- Transcript data reaches `dollars` through token counts so large that the order of float additions moves a class total by more than the rounding tolerance.
- The last two triggers take token counts far beyond any real PR.
- At token counts on the order of 1e313 in one class, the class dollars overflow to `inf`. `_usd_to_micros` then raises `OverflowError` outside the check, and the run exits with a traceback instead of degrading the row.
- If the same PR fails either `shape` or `dollars` again on an updated claude-config, the transcript is the likely cause and re-capturing will not add the breakdown.
- The follow-up is the re-capture in "The re-record contract", within its limits.

### Upgrading an existing ledger

- **Upgrade.** Read mode and export never write. The next consented `--record` that writes a row rewrites the whole file under the current header.
  - The rewrite appends every column a row's own header lacked, each filled from `_PR_COST_LEDGER_COLUMN_DEFAULTS` -- an empty `model_breakdown` cell for a pre-`model_breakdown` row, an `invalid_usage_records` cell of `0` for any row recorded before that column existed, and an empty `review_rounds` cell for any row recorded before that column existed.
  - It prints this once per upgraded ledger on stderr (`account-N's ledger` replaces `the ledger` under `--all-accounts`): `pr-cost: upgraded the ledger to the current header -- older claude-config checkouts refuse this file until they are updated, and there is no supported downgrade; see docs/pr-cost.md in the claude-config repo`.
  - Every write refuses to publish unless every prior row survives with its parsed values unchanged, comparing floats at the ledger's six decimals. A refusal leaves the ledger unchanged, so rerunning is safe.
  - A refusal message starts `pr-cost: refusing to write the ledger (ledger unchanged): `, then names a data-row number and, for a changed row, a column (the refusal that drops rows names only the row). It never names a cell value.
  - That message ends `-- a claude-config defect, or the ledger was edited during this run; rerun, and if it repeats on a current claude-config, report it`. Report a repeat with the message text on claude-config's GitHub Issues (linked from README.md).
  - A second refusal shares that prefix and the same recovery: `the staged rewrite failed validation: <reason>`, whose `line N` refers to the rewritten file, not to the ledger on disk.
  - A third refusal, `the ledger on disk changed during this run and no longer parses: <reason>`, means something else edited the ledger into an unparseable state mid-run. A rerun stops at the ordinary parse error instead. Repair the file per that error (see "Data" for the two exempt edits), and do not report it as a defect.
  - Two more refusals share the prefix and also leave the ledger unchanged: `write verification mismatch` (the staged file did not read back identical to what was written) and `column '<name>' contains a tab or newline -- refusing to write a corrupt row`. Neither is expected in practice; report a repeat as for the first refusal.
- **Older checkouts.** An upgraded ledger cannot be read by older checkouts, and there is no supported downgrade.
  - Symptom: they print `missing or mismatched pr-cost ledger header row` on read mode, `--record`, and export, and write nothing.
  - Recovery: update every checkout that shares the ledger, with `git pull` for a stow clone or a rebase or merge for a worktree on an older base. The ledger is intact; do not delete or recreate it.
  - Cost of delay: a checkout that cannot record loses each uncaptured PR once its transcripts age out.
  - Off switch: `pr_cost_recording` is for a defect in a current checkout that writes wrong data. Turn it off until a fix lands; read mode still parses the ledger. A refused write needs no off switch, because it already leaves the ledger unchanged: rerun.
  - Scheduled callers: while the key is off, a scheduled single-account `--record` exits 1 on every run (`--record is not opted in`), so pause that caller too. Under `--all-accounts`, an account that is not opted in is skipped and counted in the summary line.
  - Retention: PRs skipped meanwhile can be captured after the fix, but only while every session of the PR is still inside `cleanupPeriodDays`.
- **Write failures.** An unexpected `OSError` on the ledger path is not caught: `--record` exits with a traceback. The traceback contains local file paths, which can name a project, so redact it before pasting into a public issue. An interrupted or failed write can leave a hidden `.pr-cost-ledger-*.tmp` file in the ledger's directory holding a full copy of the ledger.
- **Durability.** The temp file is fsynced before the replace, and no directory fsync follows the replace, so a crash can roll the rename back to the prior ledger.

### Join confidence

`join_confidence` grades how the branch-to-PR join was made, not whether it happened:

- `high` — `gh`'s own `headRefName` matched directly, and at least one independent cross-check corroborated it (the PR's added plan-file slug equals the branch name, or at least one pre-squash commit SHA still resolves to a local git object).
- `medium` — a direct `headRefName` match with no corroboration.
- `low` — the branch name matched more than one merged PR (branch-name reuse); resolved by highest commit-SHA overlap, ties broken by most recent `mergedAt`, and a remaining tie leaves the row unresolved (no row written).

### Row status

`status` is a fixed enum, deliberately carrying no embedded `gh` diagnostic text — any error detail goes to stderr, never into a ledger cell:

- `ok` — enrichment (`gh pr view`) succeeded.
- `degraded_rate_limit` — the per-PR enrichment call exhausted its retry budget on a rate-limit response; the row's `additions`/`deletions`/`changed_files`/`commit_count`/`review_comment_count` and mechanical proxies are absent or zero-valued. Recapture later with `--force --pr N`, within the limits in "The re-record contract" below.
- `degraded_network` — the same, for a non-rate-limit transient failure (including an auth-shaped failure surfacing mid-run, once no other row is at risk). Same recapture path and limits.

A degraded row's dollar/token figures are still trustworthy (those come from the local corpus pass, not `gh`); only the `gh`-sourced columns are incomplete.

`status` does not cover the `model_breakdown` or `review_rounds` cells; a failed write-time check on either is described under "Write-time check failures".

A row with a degraded `status` still exits 0.

## Data

Ledger data lives outside this repo, at `$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv` by default (`~/.claude/pr-cost-ledger.tsv` when `CLAUDE_CONFIG_DIR` is unset) — a local, per-account file that `--record` creates on first use and never enters this repo's git tree. Set `PR_COST_LEDGER_PATH` to an absolute path to record somewhere else instead; a relative value is rejected. Unlike the public, git-committed weekly cost ledger, a freshly created pr-cost ledger file is given restrictive `0600` permissions, since its rows carry an opaque branch label, the raw host and owner/name, the PR number, per-PR model IDs, variant labels (fast-mode, US-geo), and per-PR review-round series (per-round dollars, tokens, model IDs and variant labels) that the weekly ledger's rows don't. `<config-dir>/machine-id` sits alongside the ledger, never at `PR_COST_LEDGER_PATH`'s own directory. It is created `0600` the first time `--record` runs. An existing ledger's mode bits are preserved on every rewrite, never tightened, so a ledger an operator loosened keeps its looser mode bits across the upgrade and now also holds model IDs and variant labels: check its mode after upgrading.

`--record` additionally requires the `pr_cost_recording` config key to resolve true (prompted by `install.sh`, alongside `cost_ledger_recording`; see [`docs/config-file.md`](config-file.md) for the file and legacy-fallback mechanics) — a write-taking subcommand shipped to every stow user stays consent-gated.

Never hand-edit this file: with no checksum/hash-chain layer over prior rows, an out-of-band edit (typo fix, row deletion, manual dollar edit) leaves no detectable trace — append only through the tool. Two edits are exempt: restoring a trailing tab an editor stripped, and blanking a `model_breakdown` or `review_rounds` cell that fails to parse (keep its tab; a blanked `review_rounds` cell leaves the tab as the line's last character). Only the tab restore leaves every value unchanged. Blanking discards that row's breakdown or rounds, which is permanent once the transcripts age out, so first check whether a one-character fix would keep the cell. Inside the retention window, a forced re-capture (see "The re-record contract") is the way back. Make either edit only while no `--record` runs, and confirm it by running read mode, which must parse the file.

### Default (read) output

With no `--record`, `pr-cost` prints one line for every row currently in the ledger file, with six columns: a redacted repo label, `pr_number`, `machine`, `status`, `join_confidence`, and `captured_at`. It never prints `model_breakdown` or `review_rounds`. It then prints a listing of merged PRs that have local-corpus activity but no captured row yet — the gap between "recorded" and "still recoverable." The gap listing is restricted to branches with an unambiguous direct `headRefName` match (a branch matching zero or more than one merged PR needs the manual audit `join_confidence: low` rows point at, not this quick check) and makes no `gh` calls beyond the one bulk discovery call read mode already needs.

### `--record`'s capture

The `machine` cell is generated once per config directory, persisted at `<config-dir>/machine-id`, and cannot be chosen or influenced by the operator (see "Machine identity" below). With no `--pr`, it walks every branch with local corpus activity; `--pr N` targets exactly one PR and turns several would-be skips into hard failures (an unmatched or too-recent PR aborts instead of being silently skipped).

A run that reaches a row write for a branch with local transcript activity, a refused `--force` included, reads the transcripts a second time, after a start line, to capture each PR's `review_rounds` cell, pricing only sessions that open a round. A run that reaches no such write does not (see "Write-time check failures").

### Machine identity

- Read your own current identity from read mode's `Machine` column, or from `--record`'s own confirmation line (`pr-cost: recorded PR #N / <identity>`).
- The identity file inherits "Data"'s never-hand-edit contract above: deleting `<config-dir>/machine-id` mints a new identity, under which every existing row reads as belonging to a different machine.
- Copying or symlinking `<config-dir>/machine-id` into a second account's config directory is the supported way to give two Claude accounts on one physical machine a single shared identity. This is the opposite case from "Refusals"'s sentinel-symlink warning under `--all-accounts` below, which exists because a *shared consent gate* defeats a per-account opt-in. A *shared identity* here is a legitimate operator choice, not a bypass of that warning's own rationale.

`<config-dir>/machine-id` shares its trust boundary with the ledger files themselves: anyone with write access to the config directory already has equivalent control over both. Symlink-adoption therefore grants no stronger integrity guarantee than this tool's other local file handling already assumes.

**Upgrading from an operator-chosen `--machine-label`.** A ledger may hold rows for one physical machine under two `machine` values: a hand-chosen `--machine-label` string and the newer generated identity. Captures are append-only, so both values persist (see "The re-record contract" and "Legacy rows" elsewhere in this doc). Two such rows for one PR in one account's ledger are two captures of one corpus. Keep only the latest and never sum them. Rows from two distinct physical machines are a different case and are summed (see "The grain" under "Redacted cross-account export" below).

**The as-of window.** A branch keeps accruing local transcript activity for a while after its PR merges, so capturing immediately after merge understates the PR's true cost. `--asof-window-days` (default `3`, per `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT`) is the close-out window a PR must clear before it's eligible for capture. This default is a **provisional placeholder**, not a validated figure: the real close-out window is meant to be set as a measured percentile of (last priced turn − `mergedAt`) across the surviving corpus, and the default may change once that measurement lands.

### The re-record contract

An unforced re-record of an already-captured `(host, repo, pr_number, machine)` refuses and names `--force`. `--force` requires `--pr` (a correction targets exactly one PR) and does not overwrite: it appends a new row carrying the same key, a fresh `captured_at`, and a `supersedes` reference to the prior row's own `captured_at`. Every prior row's existing values are left unchanged; a row predating a column gains it as an empty cell when the file is next rewritten. A forced re-capture recomputes every scalar from the transcripts that survive, so run it only while every session of that PR is still inside `cleanupPeriodDays`; a PR whose early sessions have aged out is understated, and the append is permanent. The tool does not check this: the guard below compares only the new `review_rounds` cell against the captured one, and nothing checks the scalar columns or `model_breakdown`. Judge it by when work on the branch began (its first session, which can come before its first commit), and skip the re-capture when in doubt. Readers take the latest row per key (`_latest_pr_cost_row`, by `captured_at`). This is deliberate: vendor rate tables expire and the local corpus keeps growing, so more than one correction per PR is plausible, and this ledger is the sole surviving record once transcripts age out — a single-slot overwrite would lose everything before the most recent correction.

`--record --force --pr N`, with the run's account flags, also adds a `model_breakdown` to a PR captured before that column existed. The appended row carries the breakdown under the current `rate_stamp`, recomputes every scalar from the transcripts that survive, and becomes the PR's current row. Under `--all-accounts` it does so in every account whose corpus touched the branch. The `cleanupPeriodDays` caveat above applies. A forced re-capture whose breakdown fails its check appends an empty-cell row that supersedes a populated one. The prior row remains in the ledger history, and the fix is to re-force, within the caveat above, after the cause is fixed.

Every `--force` re-capture, per PR, runs a full scalar scan plus, for a branch with local transcript activity, the review-round scan, re-stamps `rate_stamp`, moves a captured `review_rounds` cell to the current round `rule`, and adds one to the export's `correction_count`. A `--force` re-capture also refills a `review_rounds` cell that a failed pass or a failed check left empty, within the `cleanupPeriodDays` caveat above.

**The `--force` guard.** A `--force` re-capture is refused when the new `review_rounds` cell shows less than the captured one, which the guard detects as a lower `rule`, a lower count or summed `agents` under one `rule`, or an empty cell over a captured one. It does not verify retention, and it does not detect a loss masked by a gain in another round, so the "judge it by when work on the branch began" instruction above stays the operator's control. The prior row's cell is P and the new row's is N. The first matching case decides:
- P is not captured (empty): allowed, because that row holds no rounds.
- N is empty: refused, with the reason the absence gives. A P whose lists are all empty is a recorded zero, so it refuses here too.
- N's `rule` is lower than P's: refused.
- Equal `rule` values, where some skill in P has a longer list or a higher summed `agents` than in N, a missing skill key counting as empty: refused. Under one rule, a skill's list gets shorter only when a session transcript is gone, and its summed `agents` falls only when a dispatch transcript is. A later session that adds rounds raises both and is never refused.
- Otherwise allowed, a higher N `rule` included.

A refusal prints this to stderr, and the ledger stays unchanged:

```text
pr-cost:   PR #<a> for machine=<m>: refusing --force -- the new row would lose captured review rounds (<reason>); see docs/pr-cost.md
```

A single-account `--pr` run exits 1 at once. Under `--all-accounts`, that account skips the PR, and an account that records no row counts as skipped in the summary, as other skips do. The run then exits 1 after its summary and any count line.

The guard has no override, so it also blocks a re-capture meant to heal a degraded `status` whenever that would lose rounds. A higher-rule re-capture over partly aged transcripts passes, leaving the prior series only in the superseded row, and prints no notice. The guard covers `review_rounds` only.

Each `<reason>` has a next step, and none is a hand edit (see "Data"):
- `the review-round pass failed`: follow the remedy under "Write-time check failures". The captured row stays current, so repeat the same `--force --pr N` once the cause is fixed, inside `cleanupPeriodDays`.
- `the new cell failed its <rule> check`: the captured row stays current. A repeat on an updated claude-config is a defect to report.
- `this branch has no local transcript activity`: either the sessions aged out or ran on another machine, so a re-capture here would understate the scalars too and the captured row should stay, or this run's `--projects` or `--this-repo` scope is narrower than the original capture's, so repeat the original scope flags.
- `this checkout does not record review rounds`: update to a checkout that does.
- `this checkout's round rule is older than the captured row's`: update this checkout.
- `the re-capture is less complete under the same round rule`: either some transcripts aged out since the captured row, so keep it, or the scope is narrower than the original capture's, so repeat its flags.

### Comparing rows across rate stamps

Two rows with different `rate_stamp` values were priced under different vendor rate tables. **Never compare their `usd` columns directly** — a change in `usd` between them can be a real cost difference, a pricing change, or both, and the columns alone can't distinguish which. The per-class token columns cover every priced model, so no single conversion table can be applied to them to normalize one stamp's dollars onto another stamp's rates. Two rows' dollars are comparable only in one of two cases:

- A check of the pricing code's git history shows their two stamps carried identical pricing.
- One stamp is addition-only relative to the other, and the earlier-stamped row's `unpriced_tokens` is 0.

Addition-only means that, across every commit carrying either stamp, these are unchanged:

- `_model_rates` output for every model the earlier stamp priced
- `_price_turn`
- `_token_counts`
- `_cache_write_split`
- the model-independent multipliers

An `unpriced_tokens` of 0 means every one of the earlier row's turns was priced at a rate the later stamp still shares. If the pricing code's git history shows no commit carrying a stamp, rows under that stamp are not comparable to any other row.

The comparison that never depends on the rate table is the token total: the sum of the per-class token columns plus `unpriced_tokens`. A row whose `unpriced_tokens` includes Fable-attributable tokens still fits this sum correctly, since those tokens land only in the scalar `unpriced_tokens` field, never split into the per-class columns.

A pooled total may sum dollars as recorded across differing `rate_stamp` values, carrying a label that states whether every contributing row meets the comparability rule above. The rule extends from a pair to a pool of any size by comparing every row against a single fixed reference, the most recent pricing state among them, rather than against each other pairwise. A dollar comparison across eras, groups, or any other split is made only when every row on every side of that split meets that rule. Across the attribution cutover, the rule is necessary but not sufficient; see "Comparing dollars across the cutover" below.

### Pre-branch and subagent attribution

**What a row follows.** A row captured by a checkout that includes both blocks follows both the "Subagent attribution" and "Pre-branch default-branch attribution" blocks in [`docs/transcript-analysis.md`](transcript-analysis.md). The pre-branch rule counts same-session spend recorded on the default branch before the session enters the branch toward that branch. The subagent rule counts every subagent record toward the branch its dispatching main thread was on at that record's time, whatever repo the subagent worked in. In this section a *new-rule* row was captured by a checkout that includes both blocks, and an *old-rule* row by a checkout that does not.

**A figure can rise or fall.**
- Every transcript-derived column except `review_rounds` follows both rules: every `*_usd` and `*_tokens` column, `unpriced_turns`, `unpriced_tokens`, `invalid_usage_records`, `turn_count`, `session_count`, `sum_context_at_turn`, `mean_context_at_turn`, `opus_dollars`, `opus_dollar_share_pct` and `model_breakdown`. `review_rounds` follows the pre-branch rule only, per the bullet after this list.
- The `gh`-sourced columns do not move: size, commit and review counts, the plan and risk flags, `distinct_top_level_dirs`, `distinct_file_extensions` and `tests_changed`.
- A PR body keeps the figures from its last sync.
- A body last synced from a checkout that does not include both blocks carries old-rule figures.
- Bodies carry no marker of which rule they used.
- The pre-branch rule raises the figure of the branch a session enters and lowers a `--branches main` figure. A PR that plans on the default branch before creating its branch is an example, such as a row with `plan_file_added` set.
- The subagent rule moves three cohorts:
  - A subagent record whose own tag differs from its dispatcher's branch, for example one still running after a switch, moves to the dispatcher's branch.
  - An untagged subagent record moves from unbranched to the dispatcher's branch.
  - A session with no branch-bearing main-thread record moves its subagent spend from the subagents' own tags to unbranched.
- The full `cost` report audits only the pre-branch rule's shift, in its `Pre-branch default-branch records credited` line. The subagent rule's shift has no audit line.
- `review_rounds` postdates both blocks, so no cell is old-rule. A round's branch follows only the pre-branch block, applied to the carried main-thread branch (the "Pre-branch default-branch attribution" block under `cost` in [`docs/transcript-analysis.md`](transcript-analysis.md) lists round attribution among the subcommands it covers). A dispatch prices into its round by `toolUseId`, so the subagent block does not apply. `rule` marks later attribution changes.

**Cutover.** The boundary is a commit, reached per checkout at pull time, and no column records which rule a row used.
- Find the commit with `git log -S _attribute_pre_branch origin/<default-branch> -- claude/.claude/scripts/transcript_analysis/scope.py`. Empty output means the checkout predates the change. A row whose `captured_at` precedes the first commit that adds the name is old-rule on every machine, unless its capturing checkout already had it from a feature branch.
- A later row is old-rule on any machine whose checkout had not yet pulled that commit.
- An old-rule row is neither a lower nor an upper bound on its new-rule figure.
- An open PR's Cost section changes at its first sync after the pull.

**Comparing dollars across the cutover.**
- Rows are comparable across the attribution cutover only when all are new-rule, all are old-rule, or every old-rule row was re-captured from a pulled checkout. Meeting the rate-stamp rule does not establish this.
- Re-capture an old-rule row with `--record --force --pr N` inside `cleanupPeriodDays`, on the machine that captured it and from a checkout that has already pulled the commit.
- A re-capture from an unpulled checkout writes another old-rule row.
- A PR captured on several machines needs each machine's row re-captured.
- A new-rule row needs no re-capture.
- A re-capture moves the row to the current `rate_stamp`, so "Comparing rows across rate stamps" still applies to the comparison.
- Outside the `cleanupPeriodDays` window, exclude the row from cross-cutover dollar comparisons.
- A re-capture under the same `machine` value supersedes the old-rule row in the export, because the export keeps one row per (host, repo, PR, machine) by full-precision `captured_at`.
- Rows for one PR under different `machine` values meet Layer 1 of "Combining exports" below instead.
- A same-date tie in Layer 1 keeps the larger token total.
- When a re-capture's token total is lower than the old-rule row's, a re-capture under a different `machine` value can therefore lose to the old-rule row.

## Refusals

**Multi-root (exit 2 without `--all-accounts`).** `pr-cost` refuses whenever more than one scan root resolves (e.g. more than one Claude account declared in `~/.claude/transcript-config-dirs`) — unlike a pure read command, this subcommand durably writes, and even its read mode could otherwise conflate two accounts' branch/repo data into one listing. Drop `--config-dir` to scope to a single profile, or pass `--all-accounts` to scan every declared account in one run instead.

**`--all-accounts`.** Lifts the multi-root refusal for both read mode and `--record`, looping the full report (local corpus scan, ledger read/print, and — under `--record` — ledger write) once per resolved account. `gh` auth and repo-identity resolution, and the merged-PR discovery call, happen once for the whole run rather than once per account — they are account-independent (never scoped by `CLAUDE_CONFIG_DIR`).

Each account's own `pr_cost_recording` config key still individually gates whether *that account's* row is durably written: an account whose key resolves false or unset is skipped, not aborted, with a per-account stderr notice, and the run ends with a summary line stating how many of the declared accounts actually recorded a row. Symlinking one account's `claude-config.toml` (or its legacy `.pr-cost-enabled` file) to another's is not a shortcut — the existence/read check follows the symlink, so a symlinked file silently opts both accounts in together, defeating the per-account gate, and — because every key now lives in the same file — also merges every other config key between the two accounts; see [`docs/config-file.md`](config-file.md)'s "Per-account isolation" section. Create each account's `claude-config.toml` as its own regular file.

`PR_COST_LEDGER_PATH` forces one shared absolute ledger path; combined with `--all-accounts` across more than one resolved root, this would silently commingle every account's rows into a single file, defeating the per-account separation the config-key gate above depends on — refused outright (exit 2) instead. Drop `--all-accounts`, or unset `PR_COST_LEDGER_PATH` and let each account default to its own `$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv`.

**Residual cross-account correlation risk under `--all-accounts`.** `pr_number`, `machine`, and both timestamp columns (`merged_at`, `captured_at`) print raw, unredacted, in the read-mode listing — only `repo`/`head_branch` are redacted (see "Redaction" below). At single-account scope this was never a cross-account signal; under `--all-accounts`, two (or more) accounts' rows now print within one continuous invocation, so these columns become a real correlation surface between accounts. This is documented here, not newly redacted: the fields are genuine operator-facing data, and the risk is specific to genuinely multi-tenant declared accounts, not a single operator's own machine.

**`gh` identity mismatch (exit 2).** Before any `gh` discovery call, `pr-cost` resolves `gh`'s own effective target repo (`gh repo view --json nameWithOwner`) and compares it, case-folded, against this repo's own `git remote get-url origin` identity. A mismatch refuses rather than silently recording rows against the wrong repo — check `GH_REPO`, `gh repo set-default`, or an ambient cwd mismatch (the run may simply not be happening from this repo's own working tree). The confirmed identity is then pinned via `--repo` on every subsequent `gh` call this run makes, so ambient `gh` state can't drift the target mid-run.

## Redaction: `head_branch` is opaque, `host`/`repo` are raw at rest

`head_branch`:
- Never reaches the ledger file or a terminal in its original form — `_new_pr_cost_row` writes it through `_assign_root_scoped_redact_label` before it's placed in the row, and every print path (the per-PR progress line, the read-mode listing, the ledger preview, every refusal message above) does the same before display.
- The substitution is full-value and opaque (`account-<K>/branch-<N>`), not a scan for known-sensitive shapes — a branch name that happens to encode a client name in plain English (`feature/acme-onboarding`) has no gap to fall through, because the original string is never retained anywhere this subcommand writes or prints. There is deliberately no `--no-redact` escape hatch.
- This is a different mechanism from `deny-private-project-refs.sh`'s git-commit-time tracker-ID and blocklist scan — that hook covers the publish boundary for this repo's own source, not pr-cost's runtime output, and pr-cost never reads its `<config-dir>/private-projects.md` blocklist.

`host` and `repo` are not protected the same way at rest:
- `_new_pr_cost_row` stores both directly in the ledger row with no substitution, so every captured PR's host and owner/name pair sit in the ledger file in the clear, permanently, once written — an unmitigated gap.
- Terminal output is the one place `repo` *is* covered: the read-mode listing computes a redacted label via the same `_assign_root_scoped_redact_label` call `head_branch` uses before printing, so a `repo` value doesn't reach your terminal in the clear even though it reaches the file that way. `host` has no equivalent terminal exposure of its own — no print path in `pr-cost` itself displays it, redacted or otherwise. The redacted cross-account export below, however, does write a tokenized `host` into a durable file, unlike terminal output.
- For a GHE host specifically, this gap carries more identification risk than for `repo` alone — an internal GHE hostname is often the single most distinctive organization-identifying token in the row, the same reasoning `deny-private-project-refs` uses to treat internal-TLD hostnames as an always-on structural detector.
- Keeping the ledger file itself outside this repo's git tree does not close this — that only stops it from being published in a commit, not from sitting in the clear in a local file.

`model_breakdown` and `review_rounds` are stored in the clear too: they hold the PR's priced model IDs and its fast-mode and US-geo variant labels (see "The `model_breakdown` cell" and "The `review_rounds` cell" above), with no substitution.

## Redacted cross-account export

`transcript-analysis.py pr-cost-export --out PATH` reads every declared account's own `pr-cost-ledger.tsv`, collapses each to current state, redacts the five identity columns, truncates the two remaining timestamps to a date, and writes the union to a single operator-named TSV — the reproducible input the cross-account, cross-repo analysis this ledger exists to support otherwise has no way to get. It makes no `gh` call and scans no transcript corpus of its own: it is a pure local file transform over ledger files already on disk. `--out` is required and there is no stdout fallback, since stdout inside a Claude Code session is captured into that session's own transcript — the exact leak this command exists to avoid. The destination must not already exist (refused, never overwritten). It must also sit outside any git working tree, reusing the same check `--record`'s own ledger-path refusal uses. See "Residual replication paths the git-tree check doesn't close" below for that check's own blind spots, which apply here too.

**Same per-account opt-in gate as `--all-accounts`.** An account is included only if its own `pr_cost_recording` config key resolves true (see "`--all-accounts`" above, and [`docs/config-file.md`](config-file.md) for the file and legacy-fallback mechanics). An account not opted in is skipped, not aborted, with a per-account stderr notice and a count in the provenance line's `skipped_not_opted_in`. This reuses the same `_config.config_enabled` call, so it inherits the same legacy-sentinel symlink caveat when `claude-config.toml` doesn't set the key: symlinking one account's `<config-dir>/.pr-cost-enabled` to another's silently opts both accounts into the export together, defeating the per-account gate. Create each account's sentinel (or `claude-config.toml`) as its own regular file.

**Parsing.** The file opens with a `#`-prefixed provenance line above the TSV header — pass `comment="#"` to a tab reader (`pandas.read_csv` handles this as-is), or `tail -n +2` before a raw split. `cut -f`, a bare `awk -F'\t'`, and spreadsheet imports all silently shift every column by one row if the preamble isn't stripped first. The last column, `review_rounds`, may be empty, so a line can end in a tab: do not strip lines or index columns by position (see the "Not the current header's trailing cell" bullet under "The `model_breakdown` cell").

**Columns.** `_PR_COST_EXPORT_COLUMNS` in `claude/.claude/scripts/transcript_analysis/pr_cost_export.py` is the ledger's own schema (see "Schema" above) with these changes:

- A leading `account` column (the full label, e.g. `account-1`, not the bare integer).
- `host`/`repo`/`pr_number`/`head_branch`/`machine` tokenized as `account-<K>/<kind>-<N>` (`head_branch` renamed `head_branch_label` — see below).
- `merged_at`/`captured_at` truncated to a date (`rate_stamp` is already date-only and is carried through unchanged).
- `supersedes` replaced by an integer `correction_count`.

Every other column is byte-identical to the source ledger's own cell, except `model_breakdown` and `review_rounds`, which export in canonical encoding: a hand-reformatted source cell does not export byte-identical. `model_breakdown` is value-identical to the source cell. `review_rounds` passes the vocabulary filter below first, so it is value-identical only when the provenance line's `review_rounds_dropped_keys` is 0.

`model_breakdown` passes through un-tokenized, because a combiner needs a model key that stays stable across exports in order to re-price. The key carries nothing beyond the vendor catalogue: the rate table is committed to this public repo, and a PR's published `--summary` Cost block already names each priced model that PR used. Model, variant, and class keys are checked for shape only (see "Schema" above). The column stays DO-NOT-PUBLISH, because it rides an export row that carries the account dimension and because its variant labels record fast-mode and data-residency configuration. See the "The disclosed fields are not neutral" paragraph in `docs/transcript-analysis.md`'s `cost` section for why associating a PR with a model is non-neutral.

`review_rounds` passes through un-tokenized in canonical encoding, after a vocabulary filter. The filter keeps only vocabulary the exporting checkout knows, checked per key position:
- Skill keys against `review_rounds.REVIEW_SKILLS`.
- Round keys against the cell's count, token and flag field names (`_REVIEW_ROUND_COUNT_FIELDS`, `_REVIEW_ROUND_TOKEN_FIELDS`, `_REVIEW_ROUND_FLAG_FIELDS` in `pr_cost_ledger.py`).
- Model keys against the rate table.
- Variant keys against `_PRICING_VARIANTS`.
- Class keys against `_TOKEN_CLASSES`.

A name known at one position is unknown at every other. The export removes every other key, so a model gone from the exporting checkout's rate table drops with a count. A model or variant object that the removal empties is removed too, and a token object it empties stays `{}`. The count adds one per key removed at the highest level that removes it, and nothing inside a removed key's value, so it says nothing about a dropped subtree's size. The provenance line's `review_rounds_dropped_keys` sums the count over every exported row. When it is nonzero, the export prints this to stderr after its `wrote` line, with no key text:

```text
pr-cost-export: dropped <n> review_rounds key(s) this checkout does not know -- some rows lack fields, skills or models a newer checkout recorded, so their token splits can under-sum their dollars; re-export from an updated checkout before summing rounds (see docs/pr-cost.md)
```

A nonzero count means some rows lack fields, skills or models a newer checkout recorded, so their token splits can under-sum their as-priced dollars. The two JSON columns differ on purpose: `review_rounds` is vocabulary-filtered, and `model_breakdown` is shape-checked only. The filter scopes the un-tokenized claim for `review_rounds` to what the exporting checkout knows. `review_rounds` stays DO-NOT-PUBLISH, because it rides an export row that carries the account dimension.

**Account order and atomicity.** Accounts are visited in a fixed ordinal order, not the resolved roots' own order, so two exports of the same declared-roots file under different active profiles produce byte-identical row order. Every account's ledger is fully read and validated before `--out` is created, so a malformed ledger on any account aborts before any file is written and no partial export can exist.

**The grain: one row per `(host, repo, pr_number, machine)`, current state only.** All four key parts matter — a consumer grouping without `host_token` merges two distinct PRs sharing an `owner/repo` string across a GHE host and github.com. Summing one PR across two physical machines is correct, not a duplicate; that's why `machine` is part of the key rather than being collapsed away. Two `machine` values for one PR in one account's ledger are the exception (see "Upgrading from an operator-chosen `--machine-label`" above). `machine` itself is a tool-generated, opaque identifier (see "Machine identity" above), but it plays the same key role either way.

**`correction_count`** is the number of other rows sharing this row's key in the account's raw ledger, counted regardless of `status` (total captures minus one). `0` means this is the only capture ever recorded under that key. A corrected row is exactly as current and authoritative as an uncorrected one. A nonzero value is **not** on its own a trust or quality signal: the count conflates four unrelated causes with no reason code: rate-table refreshes, data-quality fixes, a re-capture that fills a cell an earlier capture left empty, and unexplained operator iteration.

**`head_branch_label` is not a join key.** The ledger's own `head_branch` cell already holds an opaque placeholder assigned during a *prior* `--record` run, under whatever ordinal scheme that run's own account/root ordering produced. This export re-tokenizes it rather than passing it through, because the stored label is stable only within the run that wrote it. The same repo in two different accounts always yields two unrelated tokens. A `pr` token is unique only within `(account)`, never globally.

**Residual correlation and the publication boundary.**
- Correlation surface: the `machine` token plus the two truncated dates are a light correlation handle across a corpus a third party might independently hold — the same residual `pr-cost`'s own `--all-accounts` read mode already documents above for the raw value, carried forward here via the token instead.
- Legacy rows: a row captured before the machine-identity change (see "Machine identity" above) persists in the ledger indefinitely once written, since captures are append-only. Its legacy `machine` value is tokenized on export like every other row's, but the provenance line's `legacy_machine_value_rows` count still flags it, since a hand-chosen label is a categorically different provenance from a tool-generated identity even once both are opaque tokens.
- Recapture does not retire it: a later recapture of the same PR under a new machine identity (`--record --force --pr N`) does not retire that earlier row — `machine` is part of `_collapse_pr_cost_rows_to_current`'s grouping key, so the recapture survives as its own separate row instead of superseding the legacy one. This count is a heuristic, not exhaustive: a legacy hand-chosen `--machine-label` that happens to match the hex shape (e.g. `deadbeef`) is not flagged, even though it is also a legacy, potentially operator-identifying value.
- No safe column subset to drop instead: the six-decimal `*_usd` floats and per-class token counts are higher-entropy per-PR fingerprints than the `gh`-sourced integers, so joinability against outside data is the mechanism, not any single column's entropy. The per-model mix and the variant mix add to that fingerprint, and the `fast` and `us_geo` variants record fast-mode and data-residency configuration facts. The per-round series, its per-round variant labels and its `after_merge` flags add per-round resolution to the per-PR fingerprint and to the configuration facts the variants record.

Export rows are subject to CLAUDE.md's publication-boundary rule (see "Also redact structural fingerprints and provenance") — an export row, or any per-row figure derived from one, is not publishable. The one exception is an aggregate of PR counts, token counts, cost, and models used, reported in one bucket. Even that publishes only with the owner's per-figure yes under `docs/private-project-redaction.md` § "The owner can authorize one figure, case by case". That section also holds the composition check and the bars that stay in force alongside an authorization. A per-model or per-variant split of an aggregate is its own figure and is asked about separately, because associating a set of PRs with a model or variant is non-neutral. The aggregate exception covers only those enumerated categories: PR counts, token counts, cost, and models used, in one bucket. A figure derived from `review_rounds` is a separate category, such as a round count, a per-round dollar or token value, or an after-merge share. It publishes only when it is computed over "this repository's own corpus on a single account" (CLAUDE.md's wording), per `docs/private-project-redaction.md` § "Publishing a tooling measurement". An export carries other repositories' rows, so no figure computed from one meets that scope. A round figure derived from an export therefore goes to the owner privately unless the owner authorizes that specific figure under `docs/private-project-redaction.md` § "The owner can authorize one figure, case by case", with the bars listed there still applying. Sending the raw row-level file to anyone at all is a decision of the same class as publishing an aggregate: it must be made deliberately, not fall out as a side effect of an analysis session.

**Rate stamps still apply.** See "Comparing rows across rate stamps" above for when two rows' dollars are comparable at all — this export's whole reason to exist is that comparison, and it governs here exactly as stated there. A pooled or combined total spanning the attribution cutover carries a comparability label too; see "Comparing dollars across the cutover" above, whose comparability bullet the rate-stamp rule alone does not satisfy. Likewise, `opus_dollar_share_pct` must be recomputed from a summed numerator and denominator across whatever rows are being aggregated, never row-averaged — the same caution `mean_context_at_turn` already carries elsewhere in this doc.

A PR merged on the same day a cost-affecting change deployed needs manual resolution against the source account's own ledger, where `merged_at` still carries full precision — this export's truncated date alone can't disambiguate which side of the change the PR actually landed on. For an attribution-rule change such as the one in "Pre-branch and subagent attribution" above, the side of the change is set by the source ledger's `captured_at` against the capturing checkout's pull date, not by `merged_at`.

**Schema drift and the `corpus=` digest are independent checks.** Two exports are comparable only by column *name*, never by position or column count — that's what handles a schema change between two export runs. The provenance line's `corpus=` value is a short hash of each participating account's `(captured_at, machine)` identity pair. That pair comes from the account's ledger's first data row, which never changes or moves under append-only writes, so the pair is stable across export runs. Two exports share this digest only when they saw the same account set, which is what licenses comparing their `account-K` ordinals against each other. An account participates in this digest — and contributes an entry to `corpus_identities` — only if it's opted in *and* its ledger holds at least one row; an opted-in account with an empty ledger contributes nothing to either. It says nothing about schema. Like `_corpus_fingerprint` elsewhere in this tool, it is a same-corpus indicator, not a security boundary. The provenance line's `DO-NOT-PUBLISH` marker is stated inline rather than reusing this tool's existing `DO NOT PUBLISH` banner text, because nothing enforces it at runtime the way that banner's own gate does.

**`corpus_override=1` flags a root set built without this workstation's real declared-roots file.** It's set by `_config_dir.py`'s `declared_roots_file_is_overridden()` — see that function's own docstring for exactly which env vars and conditions trigger it. This includes both a synthetic test fixture and a genuine real single-account run with no cross-account declaration (`CLAUDE_CONFIG_DIR` set, `~/.claude/transcript-config-dirs` absent) — treat both as unverified provenance, not necessarily fabricated data. See `docs/transcript-analysis.md`'s "Testing against a synthetic corpus" section for the recipe and why it's the safe way to smoke-test this or any other subcommand. Unlike the real-export sign-off exception (see the aggregate exception above), an export carrying `corpus_override=1` must never be published in any form, even in that same aggregate shape — its provenance is unverified, so no aggregate derived from it can honestly represent confirmed real spend.

**A degraded row silently drops out of a `gh`-sourced aggregate.** `status != "ok"` rows carry missing-not-zero `gh` columns (see "Row status" above) and must be filtered before any size/rework aggregation, same as elsewhere in this doc. Under this export's grain, however, a key whose *latest* capture is degraded contributes no row at all to a `gh`-sourced aggregate, not merely an excluded one. An earlier complete capture, if one exists, is recoverable only from the source account's own ledger. The export's collapse step always takes the latest capture by `captured_at` and never prefers an older `ok` row, since inverting an operator's own explicit correction would be worse.

**The legacy-header `host` backfill carries through unchanged.** A ledger row parsed under the pre-host-column header (see "Schema" above) has its `host` backfilled to `github.com` before this export ever sees it — a validated historical fact (every such row predates GHE support), not a guess, so it tokenizes identically to a row that recorded `github.com` explicitly rather than reading as lower-confidence. The provenance line's `legacy_header_accounts` counts pre-host-header ledgers only; a ledger under the pre-`model_breakdown` header does not count.

**Re-running.** `--out` never overwrites, so each run needs a fresh, distinctly-named path (a timestamped filename is a reasonable convention). A `--out` on a non-POSIX mount (SMB/CIFS without ACL mapping, exFAT) can silently ignore the file's `0600` creation mode; this is accepted, not re-checked at runtime. Finally: inspecting the resulting file inside a Claude Code session — reading it with the `Read` tool, or `cat`-ing it in a `Bash` call — copies its rows into that session's own transcript, the identical leak this command's own `--out`-required, no-stdout design exists to close. Inspect it in a separate terminal instead.

### Combining exports

Concatenating two or more `pr-cost-export` outputs directly double-counts: the same corpus can appear in more than one export, the same real PR can appear in more than one row, and an export's own tokens are unique only within the run that produced them (see "`head_branch_label` is not a join key" above). Combining them into one cross-account aggregate needs a validation pass, a namespacing step, and a two-layer clustering pass, in that order. Combine one export per physical machine, and only when no account's ledger is synced between machines. The `corpus=` check below catches only two inputs with identical account sets. An account present in two inputs whose account sets otherwise differ passes that check and is double-counted.

**Validate every input before reading its rows.** Accept an input only when line 1 is the export's `# pr-cost-export` provenance line, carrying the `DO-NOT-PUBLISH` marker and `corpus_override=0`. Run this check on the raw file, before stripping that line for parsing (see "Parsing" above). Each condition below is either a refusal of the whole input or a skip of one row:

- Refuse the input when its provenance line is missing, misplaced, or unparseable, because the duplicate-corpus check below has no `corpus=` digest without it.
- Refuse the input when it carries `corpus_override=1`, because its provenance is unverified, so it cannot be combined with anything (see "`corpus_override=1` flags a root set..." above).
- Refuse every input that shares a `corpus=` digest with another input, keeping none of them, because the same corpus was fed in twice, which would double every figure it contributes.
- Refuse every input that is byte-identical to another input, keeping none of them, for the same reason: the same corpus was fed in twice.
- Refuse the input when it is missing a column the procedure needs. Read every column by its header name, rather than assuming column position or count. `model_breakdown` and `review_rounds` are never required columns: an export lacking either means every row is not recorded.
- Skip the row, and count it in a diagnostics tally, when a numeric column (a token or dollar count) fails to parse as a number.
- Skip the row, and count it in the same diagnostics tally, when `captured_at` or `merged_at` fails to parse as a date.

A row skipped under the two bullets above is not grounds to refuse the whole input: one malformed row elsewhere in a large export shouldn't discard every other row's data.

Leave an input out of round sums when its provenance line's `review_rounds_dropped_keys` is nonzero, or re-export it from an updated checkout first, because its token splits can under-sum its dollars (see "Columns" above).

**Namespace every token by its source input.** Prefix every `account`, `host`, `repo`, `pr_number`, and `machine` token with a label naming the input file it came from, before comparing anything across inputs. A token is unique only within the export run that produced it, so once two exports are combined, a PR's true identity is the tuple of its input, account, host, repo, and PR number. The plain (account, host, repo, PR number) tuple is not enough, because two separately-run exports can each reuse the same tokens for different PRs.

**Cluster namespaced rows into real PRs in two layers.** The first layer resolves recaptures within one input and account.

Layer 1: rows that share host, repo, and PR-number tokens are two captures of the same corpus (see "Upgrading from an operator-chosen `--machine-label`" above). Keep the row with the later `captured_at` date. On a same-date tie, keep the row with the larger token total and count the tie as ambiguous.

Layer 2 resolves the same real PR appearing under different accounts or different input files. Take two first-layer rows from different input/account groups. If both carry `ok` status and agree on every field of this fingerprint, treat them as one real PR:

- merged date
- `additions`
- `deletions`
- `changed_files`
- `commit_count`
- `distinct_top_level_dirs`
- `distinct_file_extensions`
- `tests_changed`

Three kinds of column or row stay out of the fingerprint:

- `review_comment_count`, because review comments can still arrive after merge.
- `plan_file_added` and `risk_surface_flag`, because both depend on that run's own `--plan-file-glob`/`--risk-surface-glob` values, which can differ between captures.
- A degraded row (`status` other than `ok`), which never matches this way because its zeroed `gh`-sourced columns would otherwise collide with any other degraded row's zeros.

Count a matched PR once, and sum its dollar and token columns across every matching row. A fingerprint shared by more than one row within a single group is a collision, and every row carrying it is excluded from merging rather than guessed at. A fingerprint merges across groups when every group holding it holds it exactly once. When that holds, every row carrying it unions into one PR, however many groups are involved. The fingerprint is not unique, so the layer-1+2 PR count is an estimate that can be off in either direction: a false cross-group merge lowers it, an unmatched duplicate raises it. It never exceeds the layer-1 count.

Per-skill round counts and the as-priced `main_usd_micros` and `agent_usd_micros` totals add across matched rows only when those rows share one `rule` value, subject also to Layer 1's keep-latest rule and the rate-stamp rule. A pool spanning two `rule` values is summed per value or left out. Only the ordered series needs a single row, because rounds carry no timestamps to interleave, and `after_merge` stays per row.

Whether the combined rows' dollars — including the numerator and denominator behind the Opus-family share of dollars — are comparable across differing `rate_stamp` values follows "Comparing rows across rate stamps" above, and a total spanning the attribution cutover also needs the comparability bullet under "Comparing dollars across the cutover" above. What may be done with the combined result once computed follows the same rule as any other export figure: see "Export rows are subject to CLAUDE.md's publication-boundary rule..." above. Every input copy on the combining machine, and every file the procedure writes, gets the export's own handling. That covers combined row-level data, the diagnostics tally, per-input subtotals, and any split output. Each such file sits outside any git working tree and any cloud-sync folder, sits in an owner-only directory, opens with a `DO-NOT-PUBLISH` provenance line, and is never read inside a Claude Code session. No `--out` check applies to any of these files. The procedure writes its own outputs directly, and the owner copies each input in by hand. The procedure's stdout and stderr go to a file the session never reads, so only a single pooled summary reaches the session. Before relying on or proposing a pooled figure, the owner checks the diagnostics skip tally outside the session, and a nonzero skip count means the figure can be off in either direction.

## Residual replication paths the git-tree check doesn't close

`--record` refuses (exit 2) when the resolved ledger path sits inside a git working tree, so the default location is never accidentally committed. That check walks up from the ledger path to the nearest existing ancestor and asks git directly whether it's tracked — it cannot see two git-invisible ways the same branch/repo data could still end up shared or duplicated outside this machine:

- **A cloud-sync folder** (Dropbox, iCloud, OneDrive, or similar) syncing `$CLAUDE_CONFIG_DIR` or `PR_COST_LEDGER_PATH`'s directory. The ledger's at-rest data (see "Data") — more sensitive than the weekly ledger's aggregate-only rows — would replicate to every device and account the sync folder reaches. `<config-dir>/machine-id` is exposed to this same sync half too, since it lives alongside the ledger. A sync folder covering only `PR_COST_LEDGER_PATH`'s own directory does not expose `machine-id`, since the identity file is deliberately never resolved through that override (see "Machine identity" above). Unlike the ledgers' own residual, which is disclosure of content already at rest, a replicated `machine-id` has a distinct, more active consequence. Two physical machines silently resolve to one generated identity, which the shipped ledger and export code both key on directly. Concretely:
  - `pr-cost-export`'s row-collapse step silently drops one machine's row as though it were the other's stale self-correction.
  - `cost-ledger --record`'s weekly upsert refuses with a `ValueError` that gives no hint the cause is a cross-machine collision.
  - `pr-cost --record` treats a second machine's capture as already-captured and skips it without `--force`.
- **A bare-repo dotfile manager** (yadm-style, tracking `$HOME` via `--git-dir`/`--work-tree` flags rather than an in-tree `.git`). The git-tree check looks for a conventional in-tree `.git`; a bare-repo dotfile manager's tracking is invisible to it, so the ledger could be silently version-controlled and pushed to a remote without the check ever firing. `<config-dir>/machine-id` is exposed the same way, since `$CLAUDE_CONFIG_DIR` is `$HOME`-rooted by default.

Avoid both if the ledger's contents should stay off a shared destination — this is independent of, and in addition to, `repo`'s and `host`'s own raw-at-rest exposure above.
