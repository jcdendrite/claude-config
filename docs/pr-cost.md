# Per-PR cost ledger

A local, append-only record of this repo's own per-PR AI-tooling dollar cost, joined against GitHub PR size, rework, and review-surface data — one row per captured merged PR per machine, appended by `transcript-analysis.py pr-cost --record`. Capture before the transcript retention window (`cleanupPeriodDays`, default 30d) expires — once a branch's transcripts age out, that PR's spend is unrecoverable, and the ledger itself has no backup or cross-machine replication of its own.

Unlike the weekly `cost-ledger` (`docs/cost-ledger.md`), whose rows are aggregate-only, every row here carries a branch name and a repo identifier — see "Redaction: `head_branch` is opaque, `repo` is raw at rest" below before pointing this tool at any repo other than `claude-config`.

## Schema

Columns, in ledger order (`_PR_COST_LEDGER_COLUMNS` in `transcript-analysis.py`):

| Column | What it holds |
|---|---|
| `repo`, `pr_number`, `machine` | The row's key. `repo` is a case-folded `owner/name`, stored raw (never scrubbed at rest — see "Redaction: `head_branch` is opaque, `repo` is raw at rest") since it must stay stable and comparable across runs; PR numbers are unique only per-repo, so `repo` is part of the key from the first row. |
| `head_branch` | The joined branch, stored in its already-scrubbed form. |
| `merged_at` | The PR's `mergedAt` from `gh`. |
| `rate_stamp` | The pricing table's fetch date (`_PRICING_FETCH_DATE`) in effect when the row was computed — rows under different rate stamps are not directly comparable; see "Comparing rows across rate stamps" below. |
| `captured_at` | When this row was written. |
| `join_confidence` | `high` / `medium` / `low` — see "Join confidence" below. |
| `supersedes` | Empty, or the `captured_at` of the prior row this one corrects. |
| `status` | `ok` / `degraded_rate_limit` / `degraded_network` — see "Row status" below. |
| `cache_read_usd`, `cache_write_5m_usd`, `cache_write_1h_usd`, `output_usd`, `input_usd` | Dollars by token class. |
| `cache_read_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`, `output_tokens`, `input_tokens` | Token counts by class — the retained figures "Comparing rows across rate stamps" re-derives dollars from. |
| `unpriced_turns`, `unpriced_tokens` | Turns whose model ID wasn't recognized by the price table, and their token count. An unrecognized model is excluded from pricing, not priced at $0 — a nonzero value here means the dollar columns understate this row's true cost. |
| `turn_count`, `session_count` | Priced-turn count and distinct session count attributed to this branch. |
| `opus_dollars`, `opus_dollar_share_pct` | Opus-family spend, in dollars and as a share of this row's total. |
| `sum_context_at_turn`, `mean_context_at_turn` | The additive sum and its derived mean — kept alongside each other so a cross-PR rollup can be computed as a true average, not an average of per-row averages. |
| `additions`, `deletions`, `changed_files` | From `gh pr view`'s size fields. |
| `commit_count`, `review_comment_count` | Pre-squash commit count and review-comment count from `gh pr view`. |
| `distinct_top_level_dirs`, `distinct_file_extensions` | Mechanical review-surface proxies over the PR's changed-file list. |
| `tests_changed` | Whether any changed file matches the built-in test-file heuristic (ecosystem-generic: a `tests/` path segment, a `test_`/`_test.py` Python name, or a `.test.`/`.spec.` JS/TS suffix). |
| `plan_file_added` | Whether exactly one changed file matches `--plan-file-glob` (default `.claude/plans/*.md`, claude-config-specific). |
| `risk_surface_flag` | Whether any changed file matches a `--risk-surface-glob` (repeatable; the built-in defaults — `claude/.claude/hooks/**`, `claude/.claude/settings*.json`, `.github/workflows/**`, `install*.sh`, `claude/.claude/rules/**` — are claude-config-specific and inert against any other repo's tree until overridden). |

The row parser (`_parse_pr_cost_ledger_row_cells`) is strict on column count (fails rather than shifting cells), so adding a column later is a migration — existing rows must be rewritten or the new column needs a documented backward-compatible default.

### Join confidence

`join_confidence` grades how the branch-to-PR join was made, not whether it happened:

- `high` — `gh`'s own `headRefName` matched directly, and at least one independent cross-check corroborated it (the PR's added plan-file slug equals the branch name, or at least one pre-squash commit SHA still resolves to a local git object).
- `medium` — a direct `headRefName` match with no corroboration.
- `low` — the branch name matched more than one merged PR (branch-name reuse); resolved by highest commit-SHA overlap, ties broken by most recent `mergedAt`, and a remaining tie leaves the row unresolved (no row written).

### Row status

`status` is a fixed enum, deliberately carrying no embedded `gh` diagnostic text — any error detail goes to stderr, never into a ledger cell:

- `ok` — enrichment (`gh pr view`) succeeded.
- `degraded_rate_limit` — the per-PR enrichment call exhausted its retry budget on a rate-limit response; the row's `additions`/`deletions`/`changed_files`/`commit_count`/`review_comment_count` and mechanical proxies are absent or zero-valued. Recapture later with `--force --pr N`.
- `degraded_network` — the same, for a non-rate-limit transient failure (including an auth-shaped failure surfacing mid-run, once no other row is at risk). Same recapture path.

A degraded row's dollar/token figures are still trustworthy (those come from the local corpus pass, not `gh`); only the `gh`-sourced columns are incomplete.

## Data

Ledger data lives outside this repo, at `$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv` by default (`~/.claude/pr-cost-ledger.tsv` when `CLAUDE_CONFIG_DIR` is unset) — a local, per-account file that `--record` creates on first use and never enters this repo's git tree. Set `PR_COST_LEDGER_PATH` to an absolute path to record somewhere else instead; a relative value is rejected. Unlike the public, git-committed weekly cost ledger, a freshly created pr-cost ledger file is given restrictive `0600` permissions, since its rows carry branch names and a repo identifier the weekly ledger's rows don't.

`--record` additionally requires the opt-in sentinel `~/.claude/.pr-cost-enabled` (prompted by `install.sh`, alongside `.cost-ledger-enabled`) — a write-taking subcommand shipped to every stow user stays consent-gated.

Never hand-edit this file: with no checksum/hash-chain layer over prior rows, an out-of-band edit (typo fix, row deletion, manual dollar edit) leaves no detectable trace — append only through the tool.

### Default (read) output

With no `--record`, `pr-cost` prints every row currently in the ledger file, followed by a listing of merged PRs that have local-corpus activity but no captured row yet — the gap between "recorded" and "still recoverable." The gap listing is restricted to branches with an unambiguous direct `headRefName` match (a branch matching zero or more than one merged PR needs the manual audit `join_confidence: low` rows point at, not this quick check) and makes no `gh` calls beyond the one bulk discovery call read mode already needs.

### `--record`'s capture

`--record` requires `--machine-label` (an opaque token matching `^[a-z0-9]{1,8}$`, rejected case-insensitively against this machine's hostname — publishing a hostname risks deanonymizing the corpus). With no `--pr`, it walks every branch with local corpus activity; `--pr N` targets exactly one PR and turns several would-be skips into hard failures (an unmatched or too-recent PR aborts instead of being silently skipped).

**The as-of window.** A branch keeps accruing local transcript activity for a while after its PR merges, so capturing immediately after merge understates the PR's true cost. `--asof-window-days` (default `3`, per `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT`) is the close-out window a PR must clear before it's eligible for capture. This default is a **provisional placeholder**, not a validated figure: the real close-out window is meant to be set as a measured percentile of (last priced turn − `mergedAt`) across the surviving corpus, and the default may change once that measurement lands.

**The re-record contract.** An unforced re-record of an already-captured `(repo, pr_number, machine)` refuses and names `--force`. `--force` requires `--pr` (a correction targets exactly one PR) and does not overwrite: it appends a new row carrying the same key, a fresh `captured_at`, and a `supersedes` reference to the prior row's own `captured_at`. Every prior row is left byte-identical. Readers take the latest row per key (`_latest_pr_cost_row`, by `captured_at`). This is deliberate — more than one correction per PR is plausible, and since this ledger is the sole surviving record once transcripts age out, a single-slot overwrite would lose prior corrections permanently.

### Comparing rows across rate stamps

Two rows with different `rate_stamp` values were priced under different vendor rate tables. **Never compare their `usd` columns directly** — a change in `usd` between them can be a real cost difference, a pricing change, or both, and the columns alone can't distinguish which. A cross-rate-stamp comparison must re-derive dollars from the retained per-class token counts under **one** rate table instead.

## Refusals

**Multi-root (exit 2 without `--all-accounts`).** `pr-cost` refuses whenever more than one scan root resolves (e.g. more than one Claude account declared in `~/.claude/transcript-config-dirs`) — unlike a pure read command, this subcommand durably writes, and even its read mode could otherwise conflate two accounts' branch/repo data into one listing. Drop `--config-dir` to scope to a single profile, or pass `--all-accounts` to scan every declared account in one run instead.

**`--all-accounts`.** Lifts the multi-root refusal for both read mode and `--record`, looping the full report (local corpus scan, ledger read/print, and — under `--record` — ledger write) once per resolved account. `gh` auth and repo-identity resolution, and the merged-PR discovery call, happen once for the whole run rather than once per account — they are account-independent (never scoped by `CLAUDE_CONFIG_DIR`).

Each account's own `~/.claude/.pr-cost-enabled` sentinel still individually gates whether *that account's* row is durably written: an account with no sentinel is skipped, not aborted, with a per-account stderr notice, and the run ends with a summary line stating how many of the declared accounts actually recorded a row. Symlinking one account's sentinel to another's is not a shortcut — the existence check follows the symlink, so a symlinked sentinel silently opts both accounts in together, defeating the per-account gate. Create each account's sentinel as its own regular file.

`PR_COST_LEDGER_PATH` forces one shared absolute ledger path; combined with `--all-accounts` across more than one resolved root, this would silently commingle every account's rows into a single file, defeating the per-account separation the sentinel gate above depends on — refused outright (exit 2) instead. Drop `--all-accounts`, or unset `PR_COST_LEDGER_PATH` and let each account default to its own `$CLAUDE_CONFIG_DIR/pr-cost-ledger.tsv`.

**Residual cross-account correlation risk under `--all-accounts`.** `pr_number`, `machine`, and both timestamp columns (`merged_at`, `captured_at`) print raw, unredacted, in the read-mode listing — only `repo`/`head_branch` are redacted (see "Redaction" below). At single-account scope this was never a cross-account signal; under `--all-accounts`, two (or more) accounts' rows now print within one continuous invocation, so these columns become a real correlation surface between accounts. This is documented here, not newly redacted: the fields are genuine operator-facing data, and the risk is specific to genuinely multi-tenant declared accounts, not a single operator's own machine.

**`gh` identity mismatch (exit 2).** Before any `gh` discovery call, `pr-cost` resolves `gh`'s own effective target repo (`gh repo view --json nameWithOwner`) and compares it, case-folded, against this repo's own `git remote get-url origin` identity. A mismatch refuses rather than silently recording rows against the wrong repo — check `GH_REPO`, `gh repo set-default`, or an ambient cwd mismatch (the run may simply not be happening from this repo's own working tree). The confirmed identity is then pinned via `--repo` on every subsequent `gh` call this run makes, so ambient `gh` state can't drift the target mid-run.

## Redaction: `head_branch` is opaque, `repo` is raw at rest

`head_branch`:
- Never reaches the ledger file or a terminal in its original form — `_new_pr_cost_row` writes it through `_assign_root_scoped_redact_label` before it's placed in the row, and every print path (the per-PR progress line, the read-mode listing, the ledger preview, every refusal message above) does the same before display.
- The substitution is full-value and opaque (`account-<K>/branch-<N>`), not a scan for known-sensitive shapes — a branch name that happens to encode a client name in plain English (`feature/acme-onboarding`) has no gap to fall through, because the original string is never retained anywhere this subcommand writes or prints. There is deliberately no `--no-redact` escape hatch.
- This is a different mechanism from `deny-private-project-refs.sh`'s git-commit-time tracker-ID and blocklist scan — that hook covers the publish boundary for this repo's own source, not pr-cost's runtime output, and pr-cost never reads its `<config-dir>/private-projects.md` blocklist.

`repo` is not protected the same way at rest:
- `_new_pr_cost_row` stores `pinned_repo` directly in the ledger row with no substitution, so every captured PR's owner/name pair sits in the ledger file in the clear, permanently, once written — an unmitigated gap.
- Terminal output is the one place `repo` *is* covered: the read-mode listing computes a redacted label via the same `_assign_root_scoped_redact_label` call `head_branch` uses before printing, so a `repo` value doesn't reach your terminal in the clear even though it reaches the file that way.
- Keeping the ledger file itself outside this repo's git tree does not close this — that only stops it from being published in a commit, not from sitting in the clear in a local file.

## Residual replication paths the git-tree check doesn't close

`--record` refuses (exit 2) when the resolved ledger path sits inside a git working tree, so the default location is never accidentally committed. That check walks up from the ledger path to the nearest existing ancestor and asks git directly whether it's tracked — it cannot see two git-invisible ways the same branch/repo data could still end up shared or duplicated outside this machine:

- **A cloud-sync folder** (Dropbox, iCloud, OneDrive, or similar) syncing `$CLAUDE_CONFIG_DIR` or `PR_COST_LEDGER_PATH`'s directory. The ledger's branch names and repo identifier — more sensitive than the weekly ledger's aggregate-only rows — would replicate to every device and account the sync folder reaches.
- **A bare-repo dotfile manager** (yadm-style, tracking `$HOME` via `--git-dir`/`--work-tree` flags rather than an in-tree `.git`). The git-tree check looks for a conventional in-tree `.git`; a bare-repo dotfile manager's tracking is invisible to it, so the ledger could be silently version-controlled and pushed to a remote without the check ever firing.

Avoid both if the ledger's contents should stay off a shared destination — this is independent of, and in addition to, `repo`'s own raw-at-rest exposure above.
