# Read the pr-cost ledger's review_rounds column in round reports

## Context

Goal: let `review-cost-by-size` and the other round reports read the `review_rounds` column of the pr-cost ledger, so a PR's round series survives after its transcripts age out of `cleanupPeriodDays`.

Ask: GitHub issue #1421 ("Read the pr-cost ledger's review_rounds column in review-cost-by-size and other round reports"), narrowed by the engineer's selections in this session:
- Scope: selected the label "All candidates" (review-cost-by-size, cost-counts, pr-cost read mode, review-round-cost).
- Source policy: "I thought the whole point of the ledger was to use it. I'm concerned about transcripts being mixed and matched with the ledger. It would strike me as a bug with the ledger if we needed to fall back on transcripts." Then selected the label "Ledger only (Recommended)" for review-cost-by-size.
- Size and post-merge rounds: selected the label "Architect's defaults (Recommended)". The session's own description of that option, not the engineer's words: size comes from the same ledger row as the rounds, degraded rows are excluded and counted, and rounds flagged `after_merge` are excluded and counted.
- Other reports: selected the label "Add a ledger view to each (Recommended)". The session's own description: cost-counts and pr-cost read mode print a per-PR round count from the ledger, review-round-cost gets a ledger mode with per-PR rounds and no branch names or timestamps, and each report's transcript mode is unchanged.

Constraints from the issue: in default mode read only the active account's ledger and only rows for this checkout's `host` and `repo`; `--all-accounts` stays a private view; exclude `join_confidence: low` rows.

Answers at `/plan-review` Step 4, each a label the engineer selected:
- `rule`/`rate_stamp` policy: selected "Re-price at today's rates (Recommended)". The session's own description, from a `plan-architect` consult: keep only cells whose `rule` equals `REVIEW_ROUND_RULE` and count the rest, and re-price every kept round's stored token splits at the current rate table.
- Publication: selected "Accept: not publishable (Recommended)". The session's own description: no ledger view is a publication instrument, `docs/private-project-redaction.md` is rewritten to say so, and a capture-scope column is a separate issue.
- cost-counts view: selected "Drop it (Recommended)". This narrows the earlier "Add a ledger view to each (Recommended)" selection: the cost-counts view is not built and goes under Out of scope.
- Diff size: selected "Proceed anyway" when asked "This plan projects about 2650 changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?"

Answers after two review rounds, each the engineer's typed words:
- Direction: "Yeah, do the minimal reader and then file an issue for the writer fix". This supersedes the scope above: one minimal reader, one PR, and the review-round-cost view and `cost-counts` view are not built.
- Publication: "I want to do the simpler option, even if that means making more stuff publishable." The engineer also said of the earlier "Accept: not publishable" selection: "I just selected the accept not publishable because I didn't want to fight".
- Earlier typed concern: "I thought it was straightforward just to build the reading for the ledger".

Evidence from Step 3 exploration (a subagent report, not a conclusion): the reader surface is `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`, `review_rounds.py`, `pr_cost.py`, `pr_cost_ledger.py`, `pr_cost_export.py`, `pricing.py` and `subagent_mix.py`; the docs are `docs/pr-cost.md` and `docs/transcript-analysis.md`; the tests are under `claude/.claude/scripts/tests/` (`test_transcript_review_cost_by_size.py`, `test_transcript_pr_cost_ledger.py`, `_pr_cost_helpers.py`).

## Approach

`review-cost-by-size` reads the pr-cost ledger instead of transcripts, through one small reader, `ledger_rounds.read_ledger_prs`, and makes no `gh` call. The reader keeps each PR's one current row for this checkout's origin host and repo, and excludes and counts the PRs it cannot trust. The report re-prices each remaining round's stored token splits at this checkout's rate table and prints one notice that its figures are scope-unverified. Every other report's behavior is unchanged, and the work ships as one PR (rows 47, 48, 53).

### Open decisions

Both are recorded, not settled. The plan takes the smaller choice for each until the engineer answers.
- Row 48: the engineer's "minimal reader" does not name the `CR/PR/RR` column, which row 35 selected. The plan cuts it.
- Row 50: the report stays outside the publication instruments, and a figure from it publishes through the owner's per-figure authorization. Today's `review-cost-by-size --this-repo` is a listed publication instrument (row 19), so this PR makes less publishable than today, not more, against the engineer's "making more stuff publishable". The writer fix, issue #1426, is the route back.

### Design

**Reader: `claude/.claude/scripts/transcript_analysis/ledger_rounds.py` (new).**

`read_ledger_prs(account_roots, host, repo, *, subcommand)` reads each account's ledger and returns `select_ledger_prs` of the parsed rows. Its steps:
1. When `account_roots` holds more than one root and `PR_COST_LEDGER_PATH` is non-empty, print `<subcommand>: PR_COST_LEDGER_PATH is refused when more than one root resolves -- unset it (each account then reads its own ledger) or drop --all-accounts` to stderr and exit 2. A single-root run honors the variable, as `pr-cost` does, and an empty value counts as unset (rows 20, 33).
2. For each root, in `scope._redaction_ordinals` order and mapped back to its declared root as `pr_cost_export.py:165-180` does, resolve `pr_cost_ledger._pr_cost_ledger_path(config_dir_override=root.parent)`. Read it with one `read_text()` call and no `exists()` probe. A `FileNotFoundError` contributes no rows. Every other failure exits 1 with a fixed message that echoes no path, value or cell:
   - a relative override prints `<subcommand>: account-<K>: PR_COST_LEDGER_PATH must be an absolute path`, as `pr_cost.py:784-792` does;
   - any other `OSError`, and a `UnicodeDecodeError`, print `<subcommand>: account-<K>: ledger file could not be read`;
   - a `pr_cost_ledger._PrCostLedgerParseError` whose message starts `line N:` prints `<subcommand>: account-<K>: <error> -- see docs/pr-cost.md § "Data"`. Any other parse error (a header failure: a zero-length file, an unrecognized first line, or a ledger from a newer checkout) prints the parser's text with no pointer. The parser's messages carry a rule and, for a data row, a line number, never a cell.

`select_ledger_prs(rows_by_account, host, repo)` is pure, so the policy tests need no ledger file. Its steps:
1. Collapse each account's rows on their own with `_collapse_pr_cost_rows_to_current`: D1 calls it as `pr_cost_export._collapse_pr_cost_rows_to_current` (row 15), and D3 moves it to `pr_cost_ledger` and repoints the call (row 57). Keep the current rows whose `host` and `repo` equal the arguments (row 13).
2. Group those rows by `pr_number`, across accounts and machines. Set aside each row whose `review_rounds` cell is empty, so a cell-less row never makes a PR multi-row (row 16).
3. Each PR takes the first matching exclusion, counted by reason:
   - no row left with a cell (row 40);
   - more than one row left with a cell (row 16, G2);
   - `join_confidence` equal to `pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW` (row 8);
   - a cell `rule` other than `review_rounds.REVIEW_ROUND_RULE` (row 9);
   - a round, in any skill's list and not flagged `after_merge: 1`, whose `main_tokens` or `agent_tokens` `pricing._reprice_token_splits` returns None for (row 53).

   A degraded `status` excludes nothing, because a row's size and rounds do not come from the enrichment call it reports on (rows 14, 54).
4. For a kept PR, remove each round whose `after_merge` is 1, counted per skill (row 5). A round with no `after_merge` key stays. The writer omits the key only when the round's opening timestamp or the PR's `merged_at` does not parse (`pr_cost.py:339-341`), and the plan chooses to count such a round rather than drop one it cannot place.
5. Each remaining round's dollars are its `main_tokens` and `agent_tokens` re-priced by `pricing._reprice_token_splits` and summed (row 53).

Both return a `LedgerRead` with these fields:
- `prs`: one `LedgerPr(number, lines, rounds_by_skill, usd_by_skill)` per kept PR, in PR-number order. `lines` is the row's `additions + deletions`. `rounds_by_skill` holds the cell's per-skill lists as the parser decoded them, less the removed rounds, so unknown round and skill keys pass through (G1). `usd_by_skill` holds step 5's dollars per skill, in `rounds_by_skill` order.
- `pr_count`: PRs with a current row for this repository.
- `excluded`: a count per reason. The module's `EXCLUSION_REASONS` tuple holds each reason's printed label, in step 3's order.
- `after_merge_rounds`: the removed rounds, counted per skill.

The module docstring opens, verbatim: "Reads the pr-cost ledger's review_rounds cells for one repository." It then carries the import-by-module sentence the package's other modules carry.

**Re-pricing: `claude/.claude/scripts/transcript_analysis/pricing.py`.**

`_reprice_token_splits(token_splits) -> float | None` prices one `{model: {variant: {class: int}}}` object at the current rate table (row 53). For each group, `_usage_for_token_group(variant, counts_by_class)` builds a synthetic usage dict, and the helper sums the dollars the unchanged `_price_turn` returns on it (row 12):
- classes map to `input_tokens`, `output_tokens`, `cache_read_input_tokens` and a nested `cache_creation` holding `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`, the fields `_token_counts` reads (`pricing.py:639-655`);
- variants map to `speed: "fast"` and `inference_geo: "us"`, the inverse of `_pricing_variant` (`pricing.py:625-636`).

It returns None for a model `_price_turn` does not price, and for a variant or class label outside `_PRICING_VARIANTS` or `_TOKEN_CLASSES`. An empty object prices at 0.0, and a class missing from a group prices as zero, as `_price_turn` reads a missing usage field.

**review-cost-by-size: `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`.**
- Unchanged:
  - `--this-repo` is required, and `--projects` and a top-level `--config-dir` are refused (row 23).
  - `--all-accounts` prints `_PRIVATE_VIEW_BANNER` on both streams, before any failure.
  - The roots: `[scope.config_dir() / "projects"]` by default and `scope.resolve_scan_roots(args)` under `--all-accounts`, with today's `ValueError` handling (row 42). No `pr_cost_recording` gate applies, as in `pr-cost` read mode (row 17).
  - `_PrRecord`, every aggregate and the per-PR rows.
- Host and repo come from `gh_cli._git_remote_origin_host_and_owner_repo(subcommand="review-cost-by-size")` (row 13). The auth preflight, the repo pin, merged-PR discovery, `_scan_code_review_rounds`, `_join_rounds_to_prs`, `_JoinCounts`, the `gh pr list` limit note and the `review_rounds` import are removed.
- Each kept PR with a non-empty `code-review` list becomes a `_PrRecord`. Its `round_dollars` are the reader's `usd_by_skill["code-review"]`, in list order (row 53).
- Output, in order:
  - The header `REVIEW COST BY SIZE SOURCES (pr-cost ledger rows for this checkout's origin; <source>)`, printed after the read. `<source>` is today's `every declared account` or `active account only`. A non-empty `PR_COST_LEDGER_PATH` at one root replaces it, in both modes, with the fixed `PR_COST_LEDGER_PATH file, account scope unverified` (row 55). `pr_cost_ledger.pr_cost_ledger_path_overridden()` holds the rule that a non-empty value counts as set (row 57), and the multi-root refusal uses it too. The header never prints the variable's value or a path.
  - `_SCOPE_UNVERIFIED_NOTICE`, verbatim: `Scope unverified -- a pr-cost ledger row does not record which project directories its --record run read, so a PR's rounds can include another repository's session on a same-named branch; see docs/transcript-analysis.md § "review-cost-by-size".`
  - When no PR is reported and `ledger_read.pr_count` is 0, `_NO_PR_RECORDED_MESSAGE` (row 58), verbatim: `No PR reported. The ledger read above has no row for this repository. Run pr-cost --record to capture merged PRs past the as-of window; it needs the pr_cost_recording config key (see docs/pr-cost.md § "Data").`
  - When no PR is reported and `ledger_read.pr_count` is above 0, `_NO_PR_REPORTED_MESSAGE`, verbatim: `No PR reported. This report reads only rows that pr-cost --record captured, which needs the pr_cost_recording config key, and a row recorded before the review_rounds column needs pr-cost --record --force --pr N to refill it. That re-capture is safe only while every session of that PR is inside cleanupPeriodDays; outside it, it permanently understates the PR's row and the PR stays excluded. The ledger diagnostics below count the PRs left out; see docs/transcript-analysis.md § "review-cost-by-size".` (row 3).
  - The aggregates, which print `n/a` over no records (row 52), then "Ledger diagnostics" in place of "Join diagnostics", then the per-PR rows.
- "Ledger diagnostics" prints these lines, zeros included:
  - `PRs with a current row for this repository`;
  - `PRs reported`;
  - one `PRs excluded, <reason>` line per `EXCLUSION_REASONS` label;
  - `PRs with no code-review round before merge`, for a kept PR whose `code-review` list is absent, empty, or held only after-merge rounds;
  - `Code-review rounds removed as opened at or after merge`;
  - today's unpriced-turns, dangling-dispatches and conditional `MALFORMED USAGE` lines, over the reported PRs' code-review rounds before merge. Each sums the cell's `unpriced_turns`, `dangling` and `invalid_usage_records` fields. [verified: `pr_cost.py:333-335`] Other skills' rounds, after-merge rounds and excluded PRs' rounds add nothing.
- Removed lines: `Merged PRs listed`, `PRs joined`, `Unmatched branches`, `Ambiguous branches (excluded)` and `Unreadable sources skipped`.
- Help text:
  - The subparser help ends "Requires --this-repo and a pr-cost ledger; makes no gh call." in place of "Requires --this-repo and gh."
  - The `--all-accounts` help (`review_cost_by_size.py:350-352`) reads: "Read every declared account's pr-cost ledger instead of the active account's alone. A PR with more than one row that carries a review_rounds cell is excluded and counted, not summed." The private-view sentence after it stays.
  - The `--this-repo` help reads: "Select the pr-cost ledger rows recorded for this checkout's origin repository (see docs/transcript-analysis.md)." It makes no worktree claim. `scope._add_project_scope_args` takes it through a new keyword, `this_repo_help: str | None = None`. A caller that omits the keyword keeps today's string, so no other subcommand changes (row 43).
- The module docstring says the command reads the pr-cost ledger's `review_rounds` cells, in place of "joined to each PR by exact head-branch name". Its import sentence names `gh_cli`, `ledger_rounds`, `render` and `scope`.

**Publication.** Neither the plan nor the docs claim the report's figures are publishable. `docs/private-project-redaction.md` § "This repository, one account" admits a figure only from a command that refuses a wider corpus. The reader cannot refuse a row that its `--record` run captured over every project directory (rows 18, 19, G5). The docs say so, and route a figure to the owner's per-figure authorization, the route that already exists. Row 50 holds the open decision.

### Alternatives set aside

- A transcript fallback: the engineer chose ledger only (row 2).
- Keeping the `gh repo view` pin and live `gh pr list` sizes: a row's host and repo already equal the origin identity the pin confirmed at capture (row 13), its size is in the row (row 14), and a live call would mix sources.
- Summing a PR's cell-carrying rows across machines or accounts: no field tells a second machine from a regenerated `machine-id`, and summing the latter doubles the rounds (row 16, G2). The PR stays excluded and counted.
- For `rate_stamp`, two other rules:
  - Summing as-priced micro-dollars across stamps mixes rate tables, which `docs/pr-cost.md:304` bars.
  - Excluding every row under another stamp drops each earlier-stamped row, aged-out PRs included, at every rate-table refresh, which undoes the root.
- A second pricer that multiplies the splits by `_model_rates` directly: it would duplicate `_price_turn`'s fast-mode and US-geo conditions, which `pricing.py:45-46` ties to `_pricing_variant`, so a later multiplier would have two places to change.
- Per-rule aggregates for `rule`: they double every figure's code path for a case that cannot occur while `REVIEW_ROUND_RULE` is 1.
- Calling `_collapse_pr_cost_rows_to_current` where it is, in `pr_cost_export.py`: set aside by row 57, because the reader would import from a command module.
- The `pr_cost_recording` gate under `--all-accounts`: the key gates writes and the export file (row 17), and `pr-cost` read mode already lists every account's ledger without it.
- Refusing `PR_COST_LEDGER_PATH` in every mode: the engineer chose to honor it in a single-root run (row 33).
- A no-rows message per cause, counts of other repositories' rows and of unflagged rounds, and a `Rates:` line: row 48 cuts the diagnostics the exclusions do not need. Row 58 keeps only two messages, chosen by `pr_count`, and the exclusion counts say why a report with recorded rows is empty.

### Shipping

- One PR (row 48), written by the two sequenced dispatches in Critical files in the current feature worktree. This plan file ships in it.
- The writer-fix issue is #1426, and the PR body links it (row 47). Before the PR body links it, the main session reads the issue body and confirms it states, in its own words, the four requirements in row 50 about a row's recorded scope and the branch names a row may join. If the issue lacks one, the main session edits the issue first. The confirmation is against row 50 as finally resolved, so an engineer change to row 50 (Open decisions) is made in the issue too. The plan does not close that decision.
- The PR body names the publication trade. Today's `review-cost-by-size --this-repo` reads only this repository's worktrees on the active account, and it is a listed publication instrument (rows 19, 43). The ledger-only report gives that up for retention plus the scope-unverified notice, and it is empty until `pr-cost --record` has run. This PR therefore makes less publishable than today, not more, and #1426 is the route back (row 50). The body also says the report no longer excludes a PR whose head branch is `main`, `master`, `develop`, a detached `HEAD` or a `worktree-agent-*` name. Such a PR carries every round the capture saw on that name, including other repositories' sessions (row 56).
- The body cites #1421 as `Part of #1421`, with no closing keyword. #1421's title also asks for "other round reports", and this PR leaves every other round report unchanged, so closing it would record that work as done. `/pr-description` runs without the ticket ID, because it adds `Fixes <TICKET-ID>` only when one is passed in (`claude-skills/skills/pr-description/SKILL.md:80`).
- The branch meets the commit-time diff-size gate, `require-diff-size-prompt.sh`, a per-branch latch separate from row 31's plan-time answer (`docs/hooks.md:177`). The first commit that brings the branch to 400 or more changed lines is denied until the engineer answers `Proceed anyway` and the session runs the ack. The main session commits, because a `code-writer` agent running the ack is denied (`docs/hooks.md:183`).

### Assumption ledger

**Root:** A PR's review-round series lives in its transcripts only until `cleanupPeriodDays` deletes them. `pr-cost --record` already preserves the series in each row's `review_rounds` cell, but no report reads that cell (`docs/pr-cost.md:69`). So `review-cost-by-size` and the other round reports lose every PR whose transcripts aged out.

**Givens:**
- G1. The `review_rounds` cell's shape is a persisted contract. A round gains keys only as integer fields, and any other change is a new column under a new frozen header. Rows already on every stow consumer's disk carry the shape, so this plan reads it as it is. [verified: `pr_cost_ledger.py:238-245`; `docs/pr-cost.md:99`]
- G2. A cell's rounds carry no timestamps, and each skill's list is ordered only within that skill, so rounds from two rows or two skills cannot be interleaved. The same contract as G1 owns this. [verified: `docs/pr-cost.md:93,96`; `pr_cost.py:345-354`]
- G3. A row stores `head_branch` as an opaque per-run label, so no reader can join a row to a branch name. The same contract owns this. [verified: `pr_cost.py:563`; `docs/pr-cost.md:376-378,437`]
- G4. Claude Code deletes transcripts older than `cleanupPeriodDays`. The vendor owns this. [verified: `docs/pr-cost.md:82,207`]
- G5. A row records nothing about the project scope its `--record` run read. Adding that is a new frozen header, which the writer-fix issue covers (row 47). [verified: `pr_cost_ledger.py:26-60`]

**Rows.**
1. [engineer-verified: "All candidates"] The scope covers review-cost-by-size, cost-counts, pr-cost read mode and review-round-cost, the four names the Context relays with that label. The engineer's later selection in row 30 narrows it: cost-counts gains no ledger view. Row 47 supersedes it: only review-cost-by-size gains a ledger read.
2. [engineer-verified: "Ledger only (Recommended)"] review-cost-by-size reads the ledger alone. The engineer's reason: [engineer-verified: "It would strike me as a bug with the ledger if we needed to fall back on transcripts."]
3. [unverified] The session's description of row 2's option, not the engineer's words: remove the transcript path, and print a message naming `pr_cost_recording` and `pr-cost --record` when the ledger has no usable rows.
4. [engineer-verified: "Architect's defaults (Recommended)"] The engineer accepted the defaults for size and post-merge rounds. Row 54 removes the degraded-row exclusion that row 5's description held.
5. [unverified] The session's description of row 4's option: size comes from the same ledger row as the rounds, degraded rows are excluded and counted, and rounds flagged `after_merge` are excluded and counted. Row 54 removes its degraded-row exclusion.
6. [engineer-verified: "Add a ledger view to each (Recommended)"] Each other report gains a ledger view. The engineer's later selection in row 30 narrows it: cost-counts gains none. Row 47 supersedes the rest, as row 48 reads it.
7. [unverified] The session's description of row 6's option: cost-counts and pr-cost read mode print a per-PR round count from the ledger, review-round-cost gets a ledger mode with per-PR rounds and no branch names or timestamps, and each transcript mode is unchanged. Row 30 drops its cost-counts part, and row 48 drops the rest.
8. [unverified] The issue's constraints, as the Context relays them: default mode reads only the active account's ledger and only rows for this checkout's `host` and `repo`, `--all-accounts` stays a private view, and `join_confidence: low` rows are excluded. The issue itself was not read.
9. [engineer-verified: "Re-price at today's rates (Recommended)"] `rule` policy: keep cells whose `rule` equals `REVIEW_ROUND_RULE`, and exclude and count the rest. The tag covers only the engineer's choice of that label for the `rule`/`rate_stamp` question (Context). This policy detail is the design's, from the option description a prior `plan-architect` consult wrote. `docs/pr-cost.md:100` says to compare rounds across rows only at one `rule` value. The cost: after a future `REVIEW_ROUND_RULE` bump, every older row drops out until a `--force` re-capture, which works only inside `cleanupPeriodDays`.
10. [engineer-verified: "Re-price at today's rates (Recommended)"] `rate_stamp` policy: re-price every kept round's token splits at the current rate table. As in row 9, the tag covers only the choice of label, and the policy detail is the design's. `docs/pr-cost.md:100` says the splits "re-price under any other rate table". Row 48 cut it, and row 53 restores it.
11. A round's token splits cover exactly the turns its stored micro-dollars price, so re-pricing under the capture's own rate table reproduces them up to rounding. [verified: `review_rounds.py:357-363,483-493`; `pr_cost.py:329-338`]
12. No helper prices a `{model: {variant: {class: int}}}` object today, and `_price_turn` applies the fast-mode and US-geo multipliers itself. The writer files each priced turn's `_token_counts` under its `_pricing_variant` label, so a synthetic usage dict built from one group re-prices it through `_price_turn` unchanged. [verified: `pricing.py:216-228,580-655`; `review_rounds.py:289-296`]
13. A row's `host` and `repo` equal this checkout's origin host and owner/name, case-folded. `--record` pins gh's repo only after refusing any mismatch with origin, then writes the host and the pinned repo. A reader can therefore filter on `_git_remote_origin_host_and_owner_repo` alone, with no `gh` call. [verified: `gh_cli.py:200-231,424-433`; `pr_cost.py:559-560,750,760`]
14. A row's `additions`, `deletions` and `changed_files` come from the bulk `gh pr list` discovery, not from the per-PR enrichment whose failure sets a degraded `status`. Only `commit_count`, `review_comment_count` and the mechanical proxies come from enrichment, and `status` does not cover the `review_rounds` cell. `docs/pr-cost.md:224` says a degraded row's additions and deletions are absent or zero-valued, which the writer contradicts. Cell-less rows are set aside before any exclusion, so only a row from a checkout that writes the `review_rounds` column reaches the check. An older local checkout of this repository, from before that column existed, already read size from the bulk call, as the current writer does, so a degraded row's size is readable and row 54 removes the exclusion. [verified: `gh_cli.py:483`; `pr_cost.py:553-556,582-584`; `docs/pr-cost.md:229`; an older checkout's `transcript-analysis.py:8763,9007`, which has no `_build_review_rounds_cell`]
15. `pr_cost_export._collapse_pr_cost_rows_to_current` is the one current-row collapse. It groups by `(host, repo, pr_number, machine)` and takes `_latest_pr_cost_row` per group, and its only direct tests are `TestCollapsePrCostRowsToCurrent` in `test_transcript_pr_cost_export.py:436-498`. `pr_cost_export.py` imports no module that imports the reader, so the reader can call it where it is with no import cycle. [verified: `pr_cost_export.py:16-18,43-60`; `pr_cost_ledger.py:532-556`; grep of `_collapse_pr_cost_rows_to_current` under `claude/.claude/scripts/`] Row 57 supersedes this: the function moves to `pr_cost_ledger.py`, so the citations above describe the tree before D3.
16. One account's ledger can hold two `machine` values for one PR. A legacy `--machine-label` row and a generated-identity row are two captures of one corpus, never summed, while two physical machines' rows are summed. A legacy-label row carries no `review_rounds` cell, because `pr-cost --record --machine-label` is refused, and its changelog entry sits below the column's. Two cell-carrying rows for one PR are therefore a second physical machine, a second account, or a regenerated `machine-id`, and no field tells them apart. [verified: `docs/pr-cost.md:254-255,259,433,442`; `pr_cost.py:738`; `CHANGELOG.md:59,451`]
17. `pr-cost` read mode under `--all-accounts` reads and lists every account's ledger without checking `pr_cost_recording`. The key gates writes and `pr-cost-export` only. [verified: `pr_cost.py:794-806`; `pr_cost_export.py:186-237`; `docs/pr-cost.md:366,393`]
18. `pr-cost --record` defaults to `--projects "*"`, and its review-round pass reads that same scope, keying rounds by branch name across every project directory in it. A row recorded without `--this-repo` can therefore hold rounds from another repository's same-named branch. [verified: `scope.py:509-517`; `pr_cost.py:491-498`]
19. `docs/private-project-redaction.md` lists `review-cost-by-size --this-repo` as a publication instrument because it "reads the active config directory alone", and requires a command that refuses a wider corpus: "Avoiding a wider corpus by choosing flags carefully is not the same thing: the refusal is what a reader can re-run and check." `docs/pr-cost.md:447-449` lets a `review_rounds`-derived figure publish only when it is computed over this repository's own corpus on one account, or under the owner's per-figure authorization. [verified: `docs/private-project-redaction.md:105-108,124-129`; `docs/pr-cost.md:447-449`]
20. `PR_COST_LEDGER_PATH` replaces every account's ledger path with one absolute file, and an empty value counts as unset. `pr-cost` refuses it only under `--all-accounts` across more than one root, and `pr-cost-export` only when more than one root resolves. Both honor it at one root and print a fixed message, with no value, for a relative one. [verified: `pr_cost_ledger.py:136-147`; `pr_cost.py:709-719,784-792`; `pr_cost_export.py:240-251,375-386`]
21. `pr-cost-section.sh` calls `cost-counts --this-repo --branches <branch>`, and two skills name only that branch form. A ledger row exists only after the merge, the as-of window and a `--record` run. [verified: `pr-cost-section.sh:45-46`; `claude-skills/skills/transcript-analysis/SKILL.md:53`; `claude-skills/skills/review-loop-cost-audit/SKILL.md:173`; `docs/pr-cost.md:261`]
22. [retired] It served only review-round-cost's ledger view, which row 48 cuts.
23. scope.py's top-level `--config-dir` refusal list holds `cost-counts` and `review-cost-by-size` but not `review-round-cost`, whose `--pooled` refusal checks `args.config_dir` itself. [verified: `scope.py:702-708`; `review_rounds.py:794-798`]
24. Test placement: a new module's test file ships with it, and a test file over the 1,000-line limit splits at a named seam. These files already exceed it: `test_transcript_review_rounds.py` (6,346 lines), `test_transcript_pr_cost.py` (3,557), `test_transcript_pr_cost_ledger.py` (1,854), `test_transcript_review_cost_by_size.py` (1,107) and `test_transcript_pr_cost_gh.py` (1,005). No check enforces the limit yet. D1 adds about three lines to the last (Critical files). [verified: `.claude/rules/transcript-analysis-tests.md`; `docs/design-decisions/code-file-line-limit.md:19,50-57`; line counts by Grep, and `wc -l` for `test_transcript_pr_cost_gh.py`] Row 57 accepts two further cases: `test_transcript_pr_cost_ledger.py` grows by about 60 with the moved collapse tests, and `test_transcript_review_cost_by_size.py` stays over the limit after D2 and D3. Neither splits, because no check enforces the limit yet and a seam file would add a `TEST_FILES` entry and an architecture-doc paragraph edit for no new subject.
25. `docs/transcript-analysis-architecture.md` must carry a `### <module>.py` heading for every package module, and the review-cost-by-size docs-sample test compares the doc's sample labels with a real run, through the per-PR header line. [verified: `test_transcript_analysis_architecture_doc.py:35-49`; `test_transcript_review_cost_by_size.py:120-135,1097-1107`]
26. `CHANGELOG.md` has one `[Unreleased]` section, and its line 59 entry says no report reads `review_rounds` yet. A changelog entry is a preserved record. [verified: `CHANGELOG.md:5,59`; `CLAUDE.md` § "Scope discipline", Axis 3]
27. select-tests maps every new file here to the scripts test domain, with no rule-table change. [verified: `select-tests.py:623`]
28. [engineer-verified: "Accept: not publishable (Recommended)"] For the publication question, the engineer accepted that no ledger view is a publication instrument. Row 49 supersedes it.
29. [unverified] The session's description of row 28's option, not the engineer's words: `docs/private-project-redaction.md` is rewritten to say so, and a capture-scope column is a separate issue. Superseded with row 28, except that the capture-scope column is the writer-fix issue (row 47).
30. [engineer-verified: "Drop it (Recommended)"] The cost-counts ledger view is not built.
31. [engineer-verified: "Proceed anyway"] This plan keeps its scope rather than being cut to a `Projected diff:` below 400 changed lines. The engineer selected the label when asked "This plan projects about 2650 changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?" The tag does not settle whether the work ships as one PR or two; row 32 did, and row 48 now does.
32. [engineer-verified: "2 PRs"] The work ships as two PRs. The engineer typed it in reply to the session's question whether it ships as one PR or two. The split boundary is this plan's design: D1, D2 and D3 in the first PR, following the engineer's row 35 selection, and D4 in the second. The engineer's words do not name the boundary. Row 47 supersedes it, as row 48 reads it: one PR.
33. [engineer-verified: "Honor it in single-root runs (Recommended)"] Ledger views honor `PR_COST_LEDGER_PATH` in a single-root run. The detail is this plan's design: more than one root means `account_roots` holds more than one entry, an empty value counts as unset, and a relative value exits 1 as it does in `pr-cost`.
34. [unverified] The session's description of row 33's option, not the engineer's words: match `pr-cost`, which refuses the variable only under `--all-accounts` across more than one root. It replaces the unconditional refusal in the Approach and in M7, and removes M7's publication anchor.
35. [engineer-verified: "Move it to PR 1 (Recommended)"] The per-skill round-count column ships in the first PR. The detail is this plan's design: it is dispatch D3, after D2, with its own read-mode test seam, under the header `CR/PR/RR`. Row 47 supersedes it, as row 48 reads it: the column is not built. Row 48 raises this for the engineer's confirmation.
36. [unverified] The session's description of row 35's option, not the engineer's words: the column and its read-mode test seam move from the old D3 into the first PR, so PR 1 is D1, D2 and the column, and PR 2 holds only the review-round-cost view. Superseded with row 35.
37. The suite's autouse isolation fixture pins `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE` only, and no test deletes `PR_COST_LEDGER_PATH`. A reader that honors the variable reads a workstation's exported ledger in any test that does not set it. [verified: `conftest.py:1614-1640`; Grep of `delenv("PR_COST_LEDGER_PATH"` under `claude/.claude/scripts/tests/`, no match]
38. `test_transcript_package_module_prefixes.py` fails for a loader-free test file that imports `transcript_analysis` and is missing from `TEST_FILES`. It also holds each `PRODUCTION_MODULES` entry to by-module imports, and `review_cost_by_size.py` is already one. [verified: `test_transcript_package_module_prefixes.py:1-14,27-59`]
39. [retired] It served only review-round-cost's ledger view, which row 48 cuts.
40. A row recorded before the `review_rounds` column has an empty cell. An unforced `--record` skips an already-captured PR, and only `--record --force --pr N` inside `cleanupPeriodDays` refills one. A row captured after some sessions aged out undercounts, and no field marks it. [verified: `docs/pr-cost.md:71-82`; `CHANGELOG.md:64,71`]
41. Among the round reports, only `review-round-cost --pooled` prints a stale-rate line, and review-cost-by-size prints none. [verified: `review_rounds.py:638-643,1022-1026,1159`; `review_cost_by_size.py:276-329`]
42. `config_dir()` raises a `ValueError` whose text carries `CLAUDE_CONFIG_DIR`'s value when that value is relative, and review-cost-by-size prints that text today. [verified: `_config_dir.py:33-40`; `review_cost_by_size.py:354-358`]
43. `scope._add_project_scope_args` registers one shared `--this-repo` help, "Scope to this repo's own worktrees only", which is false for this command once it reads ledger rows. The helper gains an optional keyword, `this_repo_help: str | None = None`, that defaults to that string, so no other caller changes. `review_cost_by_size.py` passes its own string at its call site. The literal "own worktrees only" appears in `scope.py:520` (the shared help), in `skill_invocation.py:268` (a `--projects` help of its own, a different string), in `docs/transcript-analysis.md:451` (the `--projects` entry of another command) and in this plan. No test file contains it, so no test pins the shared string. `docs/transcript-analysis.md:48` relies on every subcommand but four taking both `--projects` and `--this-repo`, which the keyword leaves true. [verified: `scope.py:509-521`; `docs/transcript-analysis.md:48,451`; `review_cost_by_size.py:395`; Grep of "own worktrees only" over the worktree]
44. [retired] It served only read mode's `CR/PR/RR` column, which row 48 cuts.
45. These lines outside the command section describe a behavior the change alters, each named with the lines that state it:
    - `gh` use: `docs/transcript-analysis.md:13`;
    - the scan-gap behavior: `docs/transcript-analysis.md:34-35`;
    - the worktree-identity claim for every accepting subcommand: `docs/transcript-analysis.md:48`;
    - the corpus scope and the header: `docs/transcript-analysis.md:82,91`;
    - round-detection reuse: `docs/transcript-analysis.md:740-741`;
    - no report reading the cell: `docs/pr-cost.md:69`;
    - a degraded row's size: `docs/pr-cost.md:224,227`;
    - the first branch for publishing a `review_rounds` figure: `docs/pr-cost.md:447-449`, which the report's rows, being one repository's rows from one account, would otherwise meet.

    `gh_cli.py:1` stays true, because review-cost-by-size still reads `gh_cli._git_remote_origin_host_and_owner_repo`. Read mode's and review-round-cost's lines stay true, because neither command changes. [verified: the lines above; `gh_cli.py:1-3`]
46. [engineer-verified: "Proceed to re-review (Recommended)"] The engineer chose to keep this scope and go to a second review round when the `Projected diff:` rose to about 3,750 lines, asked after the first review round's revision. The tag covers that choice only; the two answers on `PR_COST_LEDGER_PATH` ("Honor it in single-root runs (Recommended)") and the `Rounds` column ("Move it to PR 1 (Recommended)") are recorded where the design applies them. Row 47 supersedes the scope it kept.
47. [engineer-verified: "Yeah, do the minimal reader and then file an issue for the writer fix"] The work is a minimal ledger reader. The writer-side fix is filed as its own issue and not built here.
48. [unverified] The session's reading of row 47, from its dispatch and not the engineer's words: the minimal reader is review-cost-by-size's ledger rewrite in one PR. It cuts review-round-cost's ledger view, read mode's `CR/PR/RR` column, re-pricing, the second PR, and every diagnostic line the exclusions do not need. Row 47's words name none of these. Rows 6, 10, 32 and 35 hold engineer selections this reading supersedes. The Context's gloss of row 47 names one PR and the review-round-cost view, but not re-pricing or the column, so the plan asked the engineer to confirm rows 10 and 35. Row 53 answers row 10 and restores re-pricing. Row 35 stays open (Open decisions).
49. [engineer-verified: "I want to do the simpler option, even if that means making more stuff publishable."] The engineer prefers the simpler design and accepts that it can widen what publishes. Of row 28's selection the engineer said: [engineer-verified: "I just selected the accept not publishable because I didn't want to fight"].
50. [unverified] The plan reads row 49 as met by the route that already exists: a figure from the report publishes under the owner's per-figure authorization, `docs/private-project-redaction.md` § "The owner can authorize one figure, case by case". The report does not become a publication instrument. Making it one needs either the writer fix's capture-scope column, with a reader that refuses every row whose own recorded scope is not this repository, or a change to the publication rule in `CLAUDE.md` § "Redact private-project-identifying content" and `docs/private-project-redaction.md` § "Publishing a tooling measurement", which this plan does not edit. An empty or absent scope counts as not this repository, because a row written before the column exists can never be scoped afterwards. The scope-unverified notice and any later publishability therefore depend on a row's own recorded scope, never on the column existing. The recorded scope is the corpus the capture pass actually read, enforced by a capture-time refusal of a pass that reads a wider one, and never a value the caller supplies, because the ledger has no integrity layer to catch a self-asserted value (`docs/pr-cost.md:239`). Row 50 states four requirements for #1426: the empty-scope rule, the keying on a row's own recorded scope, this capture-time enforcement, and the branch-name guard. The guard: the writer must not join a default-branch candidate (`main`, `master`, `develop`), a detached `HEAD` or a `worktree-agent-*` name to a PR, or must record a row-level fact the reader can refuse on. The reader cannot reapply it, because `head_branch` is an opaque per-run label (G3, row 56). This is open for the engineer (Open decisions).
51. [retired] Superseded by row 53: re-pricing replaces the `rate_stamp` exclusion.
52. `_compute_aggregates` and `_print_aggregates` accept an empty record list and print `n/a` over zero PRs, so an empty report needs no branch beyond its message. [verified: `review_cost_by_size.py:227-254,276-297`; `test_transcript_review_cost_by_size.py:289,366`]
53. [engineer-verified: "Re-price at today's rates (Recommended)"] Re-pricing is restored. The engineer selected the label again when asked whether to put re-pricing back after row 48's cut. It supersedes row 48's cut of it and row 51's `rate_stamp` exclusion. The design is this plan's, from a `plan-architect` consult, not the engineer's words: `pricing._reprice_token_splits` builds a synthetic usage dict per group and sums what the unchanged `_price_turn` returns, and the reader excludes and counts a PR holding a round it cannot price.
54. [engineer-verified: "Remove it (Recommended)"] The degraded-`status` exclusion is removed. The detail is this plan's design, on row 14's basis: a degraded row is read like an `ok` row, `docs/pr-cost.md:224,227` are corrected in this PR, and a test reads a degraded row's size.
55. `_pr_cost_ledger_path` returns a non-empty `PR_COST_LEDGER_PATH` as the active ledger, and `pr-cost` reads and `--record` writes the one path it returns. [verified: `pr_cost_ledger.py:136-147`; `pr_cost.py:785,798,1124`] Under an exported non-empty variable, every account that runs `--record` writes that one file, so a single-root read of it can hold more than the active account's captures. The header then prints the fixed `PR_COST_LEDGER_PATH file, account scope unverified` in the account-scope slot, in default mode and under `--all-accounts`, because `active account only` would claim a scope no code verifies. [unverified: no code ties the override file to one account]
56. The old report refused to join a branch named `main`, `master`, `develop`, a detached `HEAD` or a `worktree-agent-*` name to a PR, even when a merged PR carried that head name. The ledger writer applies no such guard. It keys rounds by every branch name the pass saw, matches each name to a merged PR by `headRefName` alone, and fills the cell from that name's rounds, so a PR whose head branch is such a name carries every round the capture saw on it, including other repositories' sessions. The row holds no fact the reader can refuse on, and this PR drops the exclusion. [verified: `review_cost_by_size.py:120`; `scope.py:931,996-999`; `docs/transcript-analysis.md:1466`; `pr_cost.py:962,969,1084`; Grep of `_DEFAULT_BRANCH_CANDIDATES` and `_names_no_pr_branch` in `pr_cost.py`, no match; Grep of the default-branch and worktree-agent guards in `cost._compute_pr_cost_branch_totals`, no match]
57. [engineer-verified: "All four"] The engineer selected "All four" when asked which of four scope additions from the first code-review round on this branch to make. The four are this plan's description, not the engineer's words: (a) the duplicated test builders that mean the same thing move into `tests/_pr_cost_helpers.py`, and builders that differ in meaning keep distinct names, with the stored-versus-re-priced difference and the absent-`code-review`-key `_cell` case staying pinned; (b) the two `_add_project_scope_args` keyword tests move into `tests/test_transcript_scope.py`; (c) `pr_cost_ledger.py` owns `pr_cost_ledger_path_overridden()`, which holds the rule that a non-empty `PR_COST_LEDGER_PATH` counts as set, and `_pr_cost_ledger_path`, `pr_cost.py`, `pr_cost_export.py` and `ledger_rounds.py` all call it, so no module repeats the read. The tests keep the literal variable name, which is the only pin on the documented name, and message text is not built from the predicate; (d) `_collapse_pr_cost_rows_to_current` moves unchanged (same signature and `(row, correction_count)` return) from `pr_cost_export.py` into `pr_cost_ledger.py`, with `TestCollapsePrCostRowsToCurrent` moving to `test_transcript_pr_cost_ledger.py`. That the move supersedes row 15 and the Out of scope line is this plan's inference. It answers the first code-review round's backend findings R3 and R4, and the engineer's rows 47 and 49 are read as widened by this later selection.
58. [engineer-verified: "Branch the message (Recommended)"] The engineer selected the label when asked whether the empty message should tell a first-time operator to run `pr-cost --record`. It supersedes row 48's single-message choice. The two message texts and the `pr_count` branch are this plan's design, not the engineer's words. `pr_count` is the key because it is `len(rows_by_pr)` (`ledger_rounds.py:139`), counted before the no-cell exclusion, so a row recorded before the `review_rounds` column keeps `pr_count` above 0 and the `--force --pr N` text. Keying on `len(prs)` or on the sum of `excluded` would send that cohort to the first-run text and drop their remedy. [verified: `ledger_rounds.py:116-139`] The engineer's earlier "Keep the current text" covered only the "PR stays excluded" wording and stays in force for the `pr_count` above 0 message. The same round's second selection, [engineer-verified: "Add them (Recommended)"], adds to the CHANGELOG Migration bullets the `PRs reported` line, the `Scope unverified` notice, the empty message, and the new exit 1 for a relative `PR_COST_LEDGER_PATH`.
59. [engineer-verified: "Add both files (Recommended)"] The engineer selected the label when asked whether to add `select-tests.py` and `tests/test_select_tests.py` to the plan or drop the `docs/pr-cost.md` read. The cause is this plan's reading, [verified: the `code-writer`'s run of `select-tests.py` failed `test_every_resolved_path_selects_a_target_covering_its_reading_test` after the heading-cite test began reading `docs/pr-cost.md`, and passed with the row added]. The size is about 10 lines.

**Mechanisms.**
- M1. One reader module, an IO step over a pure selection step. anchors: root, row8, row13, row15, row16. Lighter options set aside: (a) parsing and filtering the ledger inside `review_cost_by_size.py`, which ties the ledger's read policy to one report and leaves its tests behind that report's command; (b) reading a `pr-cost-export` file, a redacted cross-account artifact at an operator-chosen path, not a per-repository source.
- M2. Call `_collapse_pr_cost_rows_to_current` once per account: in `pr_cost_export` in D1, in `pr_cost_ledger` after D3 (row 57). anchors: row15, row16.
- M3. Set cell-less rows aside, then apply first-match PR exclusions, each counted. A degraded `status` excludes nothing. anchors: row8, row9, row14, row16, row40, row53, row54, G2.
- M4. Remove and count `after_merge: 1` rounds, and keep a round with no flag. anchors: row5, G1.
- M5. Re-price each round's token splits at this checkout's rate table through one `pricing.py` helper over the unchanged `_price_turn`. anchors: row10, row11, row12, row53. Lighter options set aside: (a) the stored micro-dollars alone, which mix rate tables across stamps; (b) excluding rows under another stamp, which drops aged-out PRs at every rate-table refresh, against the root.
- M6. Filter on origin host and repo with no `gh` call. anchors: row8, row13.
- M7. Honor `PR_COST_LEDGER_PATH` at one root and refuse it across more than one. Under the override the header prints the fixed `PR_COST_LEDGER_PATH file, account scope unverified` label in place of the account scope. anchors: row20, row33, row55.
- M8. `--all-accounts` reads every declared account with no opt-in gate. anchors: row8, row17.
- M9. One scope-unverified notice under the header, and docs that claim no publishability and route a figure to the owner's per-figure authorization. The docs and the PR body also say the old default-branch and non-PR-branch exclusion is gone. anchors: row18, row19, row49, row50, row56, G5.
- M10. Cut: read mode's `CR/PR/RR` column (Out of scope). anchors: row48.
- M11. Cut: review-round-cost's ledger view (Out of scope). anchors: row48.
- M12. The new module's tests in their own file, and review-cost-by-size's ledger tests in place of its transcript tests in its own file, each registered as row 38 requires. anchors: row24, row25, row38.
- M13. The docs, the architecture doc and a new changelog entry, with `CHANGELOG.md:59` left as recorded. anchors: row19, row25, row26, row45, row50.
- M14. Two no-PR messages chosen by `pr_count`, and the exclusion counts, so an empty report says why. anchors: row3, row40, row52, row58.
- M15. The autouse isolation fixture's body moves into a plain helper that also deletes `PR_COST_LEDGER_PATH`. A helper test fails when that line or either existing pin is removed, and a wiring test fails when the autouse fixture stops applying the helper. anchors: row33, row37.
- M16. A `this_repo_help` keyword on `scope._add_project_scope_args`, defaulting to today's string, and a ledger-row help string passed from this command's subparser. Lighter option set aside: leaving the shared help, which would print a worktree claim this command no longer makes. anchors: row43, row49.
- M17. Today's handling of an unresolvable config directory stays (Out of scope). anchors: row42.

## Critical files

Two sequenced `code-writer` dispatches in the current feature worktree, because D2 consumes D1's `LedgerRead`. Each brief says to write each new function's failing test first.

**D1: the reader.**
- `claude/.claude/scripts/transcript_analysis/pricing.py`: `_reprice_token_splits` and `_usage_for_token_group` (row 53).
- `claude/.claude/scripts/tests/test_transcript_pricing.py`: their tests. The file holds 670 lines, so it stays under the 1,000-line limit (row 24).
- `claude/.claude/scripts/transcript_analysis/ledger_rounds.py` (new): both functions, `LedgerRead`, `LedgerPr` and `EXCLUSION_REASONS`.
- `claude/.claude/scripts/tests/test_transcript_ledger_rounds.py` (new): the reader tests.
- `claude/.claude/scripts/tests/conftest.py`: the body of `_isolate_transcript_corpus_lookups` moves into a plain helper, `_pin_transcript_corpus_lookups(tmp_path, monkeypatch)`, which the autouse fixture calls, the shape `_guard_shell_exec` uses with `install_shell_exec_guard`. The helper also runs `monkeypatch.delenv("PR_COST_LEDGER_PATH", raising=False)`, and its docstring names the third variable in one sentence (row 37).
- `claude/.claude/scripts/tests/test_transcript_shared_fixtures.py`: two tests.
  - The helper test sets `PR_COST_LEDGER_PATH` to a sentinel on its own `monkeypatch` and calls the helper with that `monkeypatch` and `tmp_path`. It asserts the variable is gone from `os.environ`, and `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE` are set to the helper's two pinned values. It fails when the `delenv` line or either `setenv` line is removed.
  - The wiring test, modelled on `test_shell_exec_guard_wired.py`, sits in a class whose class-scoped fixture sets `PR_COST_LEDGER_PATH` through `pytest.MonkeyPatch.context()`, and its body asserts the variable is gone from `os.environ`. It needs no nested pytest run. It fails when `_isolate_transcript_corpus_lookups` stops calling the helper, loses `autouse=True`, or inlines a body without the `delenv`.
  - The wiring test relies on pytest running a higher-scoped fixture before a function-scoped autouse one, so the variable is set before the autouse fixture runs. [verified: a throwaway probe under pinned `pytest==8.*` (`requirements-dev.txt`), where a class-scoped fixture setting a variable and a function-scoped autouse fixture deleting it left the variable absent in the test body, and left it set once the autouse fixture no longer deleted it]
- `claude/.claude/scripts/tests/test_transcript_pr_cost_gh.py`: the writer-side check that a degraded row keeps the bulk call's size, which row 14 and row 54 rely on. It extends `test_perpetual_gh_pr_view_rate_limit_failure_marks_row_degraded_and_still_records` (`:38-69`), the first of the file's three degraded-row tests, none of which asserts a written size. The bulk-call PR gets `additions` 37 and `deletions` 5 in place of 1 and 1. The per-PR enrichment call fails, so the row's `status` is degraded, and one added assertion checks the written row carries `additions` 37 and `deletions` 5. The writer is the subject, so the check sits in the writer's file and not the reader's. The file holds 1,005 lines and the change adds about three (row 24). [verified: `test_transcript_pr_cost_gh.py:38-69`; `wc -l`; Grep of `gh_pr_view_failure_stderr` and of `["additions"]` assertions under `claude/.claude/scripts/tests/`]
- `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`: `ledger_rounds.py` joins `PRODUCTION_MODULES`, and the new test file joins `TEST_FILES` (row 38).
- `claude/.claude/scripts/transcript_analysis/__init__.py`: `ledger_rounds` joins the docstring's command-group module list.
- `docs/transcript-analysis-architecture.md`: a `### ledger_rounds.py` section naming the reader's by-module reads of `pr_cost_ledger`, `pricing`, `review_rounds` and `scope`, and `ledger_rounds.py` added to the importers of `review_rounds.py` (`:26-31`).

Reuse in D1:
- Production: `pr_cost_ledger._parse_pr_cost_ledger_file_text`, `_pr_cost_ledger_path(config_dir_override=...)`, `_PrCostLedgerParseError` and `_PR_COST_JOIN_CONFIDENCE_LOW`; `pr_cost_export._collapse_pr_cost_rows_to_current` (D3 moves it to `pr_cost_ledger`); `scope._redaction_ordinals`; `pricing._price_turn`, `_PRICING_VARIANTS` and `_TOKEN_CLASSES`; `review_rounds.REVIEW_ROUND_RULE`.
- The pricing tests build source splits with `pricing._pricing_variant` and `_token_counts`, the functions the writer files a turn's tokens with.
- Tests write fixture ledgers through `pr_cost_ledger._write_pr_cost_ledger_file`, so no hand-rolled writer can drift from production. They build rows with `_pr_cost_helpers.py`'s `_sample_pr_cost_row`, `_sample_review_rounds`, `_sample_review_round`, `_cell_with`, `_round_dict`, `_PRE_REVIEW_ROUNDS_HEADER_LINE`, `_pre_review_rounds_row_line` and `_LEDGER_DIR_MARKER`, and with `conftest._two_declared_roots`, so D1 leaves `_pr_cost_helpers.py` unchanged. D3 adds to it (row 57).

**D2: review-cost-by-size, after D1.**
- `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`.
- `claude/.claude/scripts/transcript_analysis/scope.py`: `_add_project_scope_args` gains the keyword `this_repo_help: str | None = None`. The `--this-repo` argument uses it when given and today's string otherwise (rows 43, M16). `review_cost_by_size.py` passes its string at the call site (`:395`). The helper's docstring (`scope.py:512-513`) is reworded to say `_resolve_project_scope` routes `--this-repo` through `_repo_scoped_project_slugs()` and that a caller giving the flag another meaning passes `this_repo_help` to describe it, so the docstring no longer claims that routing for every caller.
- `claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py`:
  - keeps `TestComputeAggregates`, the aggregate tests in `TestPrintedReport`, the banner heading test, and the docs-sample label test, the last over a ledger fixture;
  - keeps the two command tests that pin retained behavior: `test_all_accounts_over_a_single_root_still_marks_both_streams_do_not_publish` and `test_default_mode_exits_2_under_its_own_name_when_the_config_dir_is_unresolvable` (M17);
  - keeps the helpers the new tests reuse: `_PROJECT_SLUG`, `_write_code_review_session` and `_printed_pr_numbers`;
  - deletes `TestJoinRoundsToPrs`, `TestScanCodeReviewRounds`, the join-diagnostics tests and `TestCmdReviewCostBySize`'s other transcript and `gh` tests;
  - adds the ledger-fixture command and diagnostics tests, the `--all-accounts` unresolvable-config-directory test, and the success-run positive-control, exclusion and withholding assertions (Verification step 4), which replace the deleted success-path withholding tests.

  The file stays over the limit, and row 24 accepts that for a file no check yet enforces. D3's commit message records the measured count.
- `docs/transcript-analysis.md`, each stale line outside the command section (row 45):
  - `:13`: drop the command from the `gh` list.
  - `:34-35`: drop it from both scan-gap lists.
  - `:48`: the worktree-identity sentence names every accepting subcommand except `review-cost-by-size`, which selects the ledger rows recorded for this checkout's origin repository.
  - `:82` and `:91`: the ledger scope and the new header, with `:90` also naming the override label `PR_COST_LEDGER_PATH file, account scope unverified` as a third scope the header can print.
  - `:740-741`: drop it from both lists, whose `pr-cost` `review_rounds` column entry already covers what it reads.
- `docs/transcript-analysis.md` § "review-cost-by-size" (`:1441-1544`):
  - purpose and requirements: reads the ledger, and needs no `gh`;
  - exit codes:
    - 2 for a refused flag, an unresolvable config directory, or a non-empty `PR_COST_LEDGER_PATH` across more than one root;
    - 1 for the origin remote, a relative `PR_COST_LEDGER_PATH`, an unreadable or non-text ledger, or a bad row in a read ledger, with the recovery at `docs/pr-cost.md:239`;
  - the `--this-repo` entry (`:1457`): it selects this checkout's origin rows, as the new help says, and makes no worktree claim; and the `--all-accounts` entry (`:1458`): the help text's wording;
  - a "Which rows it reads" paragraph in place of "Join": the filter, the cell-less set-aside, each exclusion with its cause and remedy, a degraded row read like any other, the after-merge removal and the round with no flag, and a non-empty `PR_COST_LEDGER_PATH` replacing every account's ledger path;
  - what a record measures: each round's stored token splits, re-priced at this checkout's rate table;
  - diagnostics: each line's cause;
  - the header: under a non-empty `PR_COST_LEDGER_PATH`, both modes print `PR_COST_LEDGER_PATH file, account scope unverified` in the account-scope slot, with no path or value (row 55);
  - fixed messages: the rule that an error echoes no path, value or cell covers the ledger reader's errors only (Out of scope);
  - known limits:
    - the **Known limits.** list in `docs/transcript-analysis.md` § "review-cost-by-size" is the text of record for the limits. The list below is its outline, and the shipped list carries further limits;
    - `:1487-1489` and `:1491` stay;
    - `:1490`'s fork-PR join becomes the capture's own branch join;
    - `:1492-1495` give way to the ledger's limits: recorded PRs only, each after the as-of window and a `--record` run (row 21); the retention undercount at capture (row 40); rounds from another repository's same-named branch (row 18); and one machine's capture per ledger. Each account's ledger lives in its own config directory (`pr_cost_ledger.py:147`), so a PR worked on two machines shows only the rounds this machine captured, an undercount no field marks. If two machines write one ledger file, such a PR is excluded as multi-row (row 16). Whether any setup shares a file that way is unverified, so the doc states it only as that condition;
    - a PR whose head branch is `main`, `master`, `develop`, a detached `HEAD` or a `worktree-agent-*` name carries every round the capture saw on that name, including other repositories' sessions, and the report no longer excludes it (row 56);
  - publication: the report reads rows that do not record their capture scope, so it is not a publication instrument, and a figure from it publishes only under `docs/private-project-redaction.md` § "The owner can authorize one figure, case by case". The `--all-accounts` private view stays, and `:1502`'s `scanning root` sentence goes;
  - sample output: synthetic, with the "illustrative filler" caption kept;
  - "When to reach for it": limited to PRs captured since the `review_rounds` column.
- `docs/private-project-redaction.md:124-129`: the review-cost-by-size bullet says it reads pr-cost ledger rows, which do not record the project scope their rounds were read from, so it refuses no wider corpus and is not a publication instrument. It routes a figure to § "The owner can authorize one figure, case by case".
- `docs/pr-cost.md`:
  - `:69`: "Recorded only" becomes a "Readers" bullet naming review-cost-by-size. Read mode still does not print the cell.
  - `:224`: a degraded row's `commit_count`, `review_comment_count` and mechanical proxies are zero-valued, while its `additions`, `deletions` and `changed_files` come from the bulk discovery call and stay accurate (row 14). `:227`'s "`gh`-sourced columns" becomes "enrichment-sourced columns".
  - `:447-449`: the sentence "No export figure meets this, because an export carries other repositories' rows." stays. Beside it, a clause says a figure read from any ledger row that records no capture scope, review-cost-by-size's included, does not meet the first branch, because that row's rounds can include another repository's session on a same-named branch (row 18).
- `docs/transcript-analysis-architecture.md`: the `review_cost_by_size.py` section (`:305-314`), the `gh_cli.py` reader list (`:415,426-427`), and `review_rounds.py`'s importer lists (`:30,287`), which drop review-cost-by-size.
- `CHANGELOG.md`: a new `[Unreleased]` entry with no figures, whose **Migration:** bullets say:
  - `gh` is no longer required, and transcripts are no longer read;
  - `CHANGELOG.md` `[Unreleased]` is the text of record for the entry, one Migration bullet per surface. The list here is its outline, and the shipped entry also carries the bullets for the honored `PR_COST_LEDGER_PATH`, the ledger-failure exit 1 and the output wording. D3 folds a relative value's exit 1 into the ledger-failure bullet, and the `PRs reported` line, the `Scope unverified` notice and the `No PR reported.` messages into the output-wording bullet (row 58);
  - the report shows recorded PRs only, empty until `pr-cost --record` runs, and a row recorded before the `review_rounds` column stays excluded. `--record --force --pr N` refills it only while every session of that PR is inside `cleanupPeriodDays`. Outside that window the re-capture succeeds and permanently understates the PR's row, at the cost `docs/pr-cost.md` § "The re-record contract" states;
  - post-merge rounds no longer count, so figures differ from earlier runs;
  - under `--all-accounts`, a PR two accounts recorded is excluded and counted, where the merged scan summed its rounds;
  - the command is no longer a publication instrument;
  - the report no longer excludes a PR whose head branch is `main`, `master`, `develop`, a detached `HEAD` or a `worktree-agent-*` name, so such a PR carries every round the capture saw on that name, including other repositories' sessions (row 56).

**D3: first code-review round's fixes, after D2.** Ledger row 57.
- `claude/.claude/scripts/tests/_pr_cost_helpers.py`: the builders that `test_transcript_ledger_rounds.py` and `test_transcript_review_cost_by_size.py` define alike. Each shared builder keeps one signature, and the brief names it. The shared set is named by the semantics each consumer needs: an explicit omit-the-key cell form, a round builder that keeps the stored-not-equal-to-re-priced property and the main-versus-agent asymmetry, and per-file wrappers that supply each file's host, repo and machine sentinels. `test_transcript_pricing.py:677`'s copy of the priced-model constant moves too. Builders that differ in meaning keep distinct names: `_cell(None)` means one default round in the first file and no `code-review` key in the second, and `_ledger_round` fixes stored micro-dollars that differ from the re-price. Both files import the shared ones.
- `claude/.claude/scripts/tests/test_transcript_pr_cost.py`, `test_transcript_pr_cost_export.py`: one added test per gate that sets `PR_COST_LEDGER_PATH` to the empty string and expects the gate to treat it as unset.
- `claude/.claude/scripts/tests/test_transcript_scope.py`: the two `_add_project_scope_args` keyword tests, moved from `test_transcript_review_cost_by_size.py`.
- `claude/.claude/scripts/transcript_analysis/pr_cost_ledger.py`: `pr_cost_ledger_path_overridden()`, used by `_pr_cost_ledger_path` at `:141`, and `_collapse_pr_cost_rows_to_current`, which moves in unchanged.
- `claude/.claude/scripts/transcript_analysis/pr_cost.py`, `pr_cost_export.py`: call the predicate at `pr_cost.py:709` and `pr_cost_export.py:375`, and `pr_cost_export.py:278` calls the collapse in `pr_cost_ledger`. `pr_cost_export.py` keeps no alias.
- `claude/.claude/scripts/transcript_analysis/ledger_rounds.py`: calls the predicate and the collapse in `pr_cost_ledger`, drops its `pr_cost_export` import, and drops `pr_cost_export` from its module docstring's import-by-module sentence.
- `claude/.claude/scripts/tests/test_transcript_pr_cost_export.py`, `test_transcript_pr_cost_ledger.py`: `TestCollapsePrCostRowsToCurrent` moves to the second with only the module qualifier changed, and `test_transcript_pr_cost_export.py` gains one integration test: through `cmd_pr_cost_export`, two same-day rows seconds apart with the newer appended first export the newer row's values. That file holds 1,854 lines already, so the move adds about 60, a growth row 24 accepts.
- `docs/transcript-analysis-architecture.md`: the `### ledger_rounds.py` section drops `pr_cost_export`, the `pr_cost_export.py` section (`:475-478`) drops its collapse import, and the `### pr_cost_ledger.py` section (`:444-449`) names the collapse and the predicate.
- `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`, `claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py`: row 58. A `_NO_PR_RECORDED_MESSAGE` constant and the `ledger_read.pr_count` branch where the empty report prints, and the empty-report tests re-pointed per Verification step 4. The heading-cite test resolves each message's own cited document. Cite the `--all-accounts` help by symbol, since the added constant shifts its line.
- `claude/.claude/scripts/select-tests.py`, `claude/.claude/scripts/tests/test_select_tests.py`: a `PR_COST_DOC_MD` constant with its `SCRIPTS_TESTS_DIR` row, and its entry in the doc list that `test_every_resolved_path_selects_a_target_covering_its_reading_test` walks. The heading-cite test now reads `docs/pr-cost.md`, and that test fails unless `select-tests.py` maps the doc to the tests that read it (row 59).
- `CHANGELOG.md`: the Migration additions row 58 names, folded into the sibling bullets.
- D3's verification: Verification step 6.

Each dispatch's verification command: Verification steps 1 and 2, with that dispatch's tests from step 3 or step 4.

Projected diff: about 2,350 lines

- D1: about 640, of which about 90 are the pricing helper and its tests, about 40 the isolation helper and its two tests, about 30 the two exclusion-order tests and about 3 the writer-side size assertion.
- D2: about 1,450, of which about 800 are deleted transcript-path code and tests, about 240 the success-run positive-control, exclusion, withholding, diagnostics-sum and empty-report assertions plus the `--all-accounts` config-directory test, and about 20 the `--this-repo` help keyword, its call site and its assertion.
- D3: about 250 moved or edited lines, about 50 of them row 58's constant, branch, test edits and CHANGELOG additions.

An estimate from the files' current sizes, not a measurement.

## Verification

1. After each dispatch, from the worktree root: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It selects the scripts tests for every code and test file here (row 27). The doc and changelog files add no scripts tests.
2. After each dispatch: `scripts/venv-run.sh ruff check claude/.claude/`.
3. D1's tests show, for `pricing`, in `test_transcript_pricing.py`:
   - For every `_PRICING_VARIANTS` label, `_pricing_variant` of the usage `_usage_for_token_group` builds for it returns that label.
   - For a source usage per variant, with unequal class counts and a nested `cache_creation`, the splits `{model: {_pricing_variant(usage): _token_counts(usage)}}` re-price to the sum of `_price_turn`'s dollars on the source usage, within float tolerance.
   - `_reprice_token_splits` returns None for an unpriced model, an unknown variant label and an unknown class label, and 0.0 for an empty object.

   And, through `select_ledger_prs` unless named otherwise:
   - Filter: a row for another host with this repo, and a row for another repo on this host, are never returned and stay out of `pr_count`. A superseded row is ignored. A newer degraded correction over an older `ok` row is the row read.
   - Each exclusion reason, alone, excludes its PR and moves only its own count. A PR meeting both the `join_confidence` and the `rule` reasons counts once, under `join_confidence`. Two further pairs are tested in `test_transcript_ledger_rounds.py`, each asserting that no other count moves:
     - A PR with two cell-carrying rows from two machines, one of them with `join_confidence` low, counts once under multi-row.
     - A row with `rule` equal to `review_rounds.REVIEW_ROUND_RULE + 1`, carrying a round not flagged `after_merge` on a model the rate table does not price, counts once under `rule`.
   - Boundaries, each derived from its constant and never a literal:
     - a row under each `pr_cost_ledger._PR_COST_STATUS_VALUES` entry other than `_PR_COST_STATUS_OK` is kept, with `lines` equal to its `additions + deletions`;
     - `join_confidence: medium` is kept;
     - a `rule` of `review_rounds.REVIEW_ROUND_RULE + 1` is excluded;
     - a `rate_stamp` one day before `pricing._PRICING_FETCH_DATE` is kept and re-priced.
   - Re-pricing: each kept round's `usd_by_skill` entry equals its two `_reprice_token_splits` results summed, in list order. A round on a model the rate table does not price excludes its PR under its own reason, and the same round flagged `after_merge: 1` does not. The unpriceable round sits in a `plan-review` list on a PR whose code-review rounds all price, and the PR is excluded.
   - A PR with one cell-less row and one cell-carrying row is kept.
   - `after_merge: 1` rounds are removed and counted per skill, a `plan-review` round under its own skill, and the rest keep their order. A round with no `after_merge` key is kept. A round with an added integer key reads, and an unknown skill key passes through.
   - Cross-account: a PR with the same host, repo, number and machine in two accounts' rows is excluded once as multi-row, not merged by one collapse.
   - Through `read_ledger_prs`:
     - two roots read both ledgers, the second not opted in;
     - a missing ledger and a header-only ledger each yield zero rows;
     - a ledger under the pre-`review_rounds` header lands under the no-cell reason, not a parse error;
     - a parse error and a zero-length ledger each exit 1 with the parse-error message;
     - an unreadable ledger (`chmod 000`), under the suite's existing root skip, and a ledger holding invalid UTF-8 each exit 1 with `could not be read`;
     - `PR_COST_LEDGER_PATH`: one root reads the override file, two roots exit 2, an empty value counts as unset, and a relative sentinel value exits 1 with `account-1`;
     - each error test asserts the exact message, and the absence of `_LEDGER_DIR_MARKER`, the sentinel and `str(tmp_path)` from both streams.

   And, for the writer, in `test_transcript_pr_cost_gh.py` (Critical files, D1): a `--record` run whose bulk-call PR has `additions` 37 and `deletions` 5 and whose per-PR enrichment call fails writes a degraded row carrying both values.

   And, for the isolation helper, in `test_transcript_shared_fixtures.py` (row 37):
   - with `PR_COST_LEDGER_PATH` set to a sentinel, calling the helper leaves it out of `os.environ` and pins `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE`;
   - the wiring test passes with the autouse fixture calling the helper, and fails with the call removed from `_isolate_transcript_corpus_lookups`.
4. D2's tests show:
   - Default mode reads only the active account's ledger (its isolation seed is under the success runs below), and the header ends `active account only`. `--all-accounts` reads a second declared account's, not opted in, and prints the banner first on both streams, also when the reader then fails.
   - The recorded `subprocess.run` argv list equals exactly `[["git", "remote", "get-url", "origin"]]` in every run that reaches the origin lookup, so a `gh` call fails the comparison even though `_fake_pr_cost_subprocess_run` answers one with canned success. The list comes from that fake's `call_log` argument, which the command tests pass through `_run_cmd(..., call_log=...)`. The existing tests compare it to `[]` for the refusals. A failing origin lookup exits 1 under `review-cost-by-size:`. [verified: `_pr_cost_helpers.py:288-300,395`; `test_transcript_review_cost_by_size.py:98,718`; `gh_cli.py:214`]
   - A single-root `--all-accounts` run still prints the banner first on both streams (kept: `test_all_accounts_over_a_single_root_still_marks_both_streams_do_not_publish`).
   - An unresolvable config directory exits 2 in both modes. Default mode exits under `review-cost-by-size:` (kept: `test_default_mode_exits_2_under_its_own_name_when_the_config_dir_is_unresolvable`, M17). `--all-accounts` exits under `_projects_dir:`, the prefix `scope.py:80` prints, after the banner on both streams, and one test is added for that arm. That test first runs `monkeypatch.delitem(vars(scope), "PROJECTS_DIR", raising=False)`, because `_projects_dir` calls `config_dir()` only when the global is absent. An earlier `monkeypatch.setattr` on that name reads the old value through the module `__getattr__`, which stores it as a real module global, so a test that merely assumes the global is unresolved passes alone and fails after such a test. [verified: `review_cost_by_size.py:348-358`; `scope.py:45-82`; `conftest.py:1306,1526`]
   - Ledger only: a code-review transcript session seeded where the retired scan read one, under an account root in a `_PROJECT_SLUG` directory through `_write_code_review_session`, on a branch equal to a ledger PR's `head_branch`. Its round count and dollars differ visibly from the ledger's, and the output carries the ledger's figures only.
   - Arithmetic, over a fixture with unequal additions and deletions and unequal main and agent token splits: `lines == additions + deletions`; `round_dollars` are the reader's re-priced code-review dollars, in list order, over stored micro-dollars that differ from the splits' price; only code-review rounds feed the series; an after-merge round in mid-list adds nothing and leaves the others in order.
   - A kept PR with only other skills' rounds, one with only after-merge code-review rounds, and one with no `code-review` key each count under `PRs with no code-review round before merge`.
   - "Ledger diagnostics" prints its exact line set, zeros included, with each exclusion count and the after-merge count flowing from fixture to line.
   - Diagnostics sums, through the command, with no new pure seam. The fixture gives nonzero, distinct `unpriced_turns`, `dangling` and `invalid_usage_records` values to four rounds and zero to every other round:
     - a reported code-review round;
     - an after-merge code-review round;
     - a kept PR's `plan-review` round;
     - an excluded PR's round.

     The assertions:
     - The three printed sums equal the reported code-review round's values alone.
     - That round's `invalid_usage_records` is at least 1,000, so the line prints the thousands separator.
     - One more run sets that round's `invalid_usage_records` to 0, keeps the other three nonzero, and asserts the `MALFORMED USAGE` line is absent.
     - The fixture also holds an after-merge `plan-review` round, and the printed after-merge line counts only code-review rounds.
   - The per-PR rows print after every aggregate, and a 1,480-line PR's row prints the thousands separator.
   - Each empty-report case exits 0 and prints the message `pr_count` selects (row 58):
     - `_NO_PR_RECORDED_MESSAGE` for a missing ledger, a header-only ledger, and a ledger holding only other repositories' rows, with zero counts;
     - `_NO_PR_REPORTED_MESSAGE` for a ledger whose rows are all excluded, with a nonzero count on its exclusion lines;
     - `_NO_PR_REPORTED_MESSAGE` for a ledger whose kept PRs have no code-review round before merge, with a nonzero `PRs with no code-review round before merge`.
   - The heading-cite test resolves each message's own cited document: `docs/transcript-analysis.md` for `_NO_PR_REPORTED_MESSAGE`, and `docs/pr-cost.md` for `_NO_PR_RECORDED_MESSAGE`.

     The missing-ledger, header-only and all-excluded runs also seed the transcript session of the "Ledger only" bullet. They assert `PRs reported` is 0 and that the recorded `call_log` equals exactly `[["git", "remote", "get-url", "origin"]]`. A transcript fallback or a `gh` call that only an empty result would trigger therefore fails.
   - A zero-length ledger exits 1 with the parse-error message, because the parser finds no header (`pr_cost_ledger.py:485-493`).
   - The stdout line after the header is `_SCOPE_UNVERIFIED_NOTICE`, on the populated path and the missing-ledger path. The notice cites a real heading in `docs/transcript-analysis.md`, as `test_private_view_banner_cites_a_real_heading` checks the banner's.
   - The refusals hold: no `--this-repo`, any `--projects`, and `PR_COST_LEDGER_PATH` under `--all-accounts` across two roots.
   - The default run, the `--all-accounts` run and a one-root `PR_COST_LEDGER_PATH` run are success runs. Each asserts positive controls and component-wise withholding in the same run:
     - Positive control: every seeded kept PR's number prints in the per-PR rows, and `PRs reported` counts them. Each seeded kept PR carries a priceable `code-review` round.
       - The `--all-accounts` run seeds one PR only in the second account's ledger.
       - The override run seeds one PR only in the override file and another only in the default account's ledger. It asserts the first prints, the second is absent, and the header ends `PR_COST_LEDGER_PATH file, account scope unverified`, in default mode and under `--all-accounts` at one root (row 55).
     - Default-mode isolation: the default run also declares a second account through `conftest._two_declared_roots` (`conftest.py:1292`), whose ledger holds a kept, priceable PR. The run asserts that PR's number is absent from the per-PR rows, and that `PRs with a current row for this repository` and `PRs reported` count only the active account's PRs. No banner prints on either stream. The second account's directory name joins the withheld components.
     - Exclusions: the default run also seeds one PR for each exclusion reason, including the multi-row PR with two machines. Each excluded row carries its own seeded `machine` and `head_branch`. The unpriced-model PR's round carries a distinctive model-ID sentinel. The run asserts each `PRs excluded, <reason>` count and that no excluded row's component prints.
     - Cell-borne keys: one kept PR's cell carries an unknown skill key, and one of that PR's rounds carries an unknown integer round key. The reader passes both through (G1), and no report line prints either.
     - Withholding: on both streams, each component is asserted absent alone, never only as a composite string. The components are the head-branch token, the ESC byte, the host, the owner, the repo name, each seeded `machine` label, `str(tmp_path)`, each account directory name, the override value, the override file's basename, `_LEDGER_DIR_MARKER` (`_pr_cost_helpers.py:166`), which the seeded ledger's directory name carries, and, in the default run, the model-ID sentinel, the unknown skill key and the unknown round key.
   - The missing-ledger, header-only and all-excluded runs assert the same withholding, the all-excluded run with its excluded rows' components, and need no positive control.
   - The seeds are distinctive sentinels that cannot collide with report text, and are constrained so each run stays a success run:
     - the host, owner, repo name, `machine` label and `head_branch` token are each a different sentinel;
     - `head_branch` is `_HOSTILE_HEAD_BRANCH`'s ESC sequence (`test_transcript_review_cost_by_size.py:39`) plus a distinctive token, with no tab, newline, carriage return or other `str.splitlines` boundary character, because `_format_pr_cost_ledger_row` refuses a tab, newline or carriage return (`pr_cost_ledger.py:526-527`);
     - `machine` is a distinctive label of at most 8 lowercase alphanumerics, which `ledger_common._MACHINE_LABEL_RE` (`^[a-z0-9]{1,8}\Z`, `ledger_common.py:27`) accepts, because a malformed one makes the ledger a parse error (`pr_cost_ledger.py:389-390`);
     - the model-ID sentinel, the unknown skill key and the unknown round key are each a different sentinel that matches the key grammar `[a-z0-9][a-z0-9._-]{0,63}`, and the model sentinel names a model the rate table does not price. The grammar governs model, variant and class names, skill keys and the names of a round's additive fields, and an additive field's value must be an integer. A sentinel outside it makes the ledger a parse error, and the run stops being a success run. The rounds under the unknown skill key use a priceable model, because a round the reader cannot price excludes its PR. [verified: `pr_cost_ledger.py:153,177-187,284-294,317-318,334`]
   - The docs-sample label test passes over a ledger fixture.
   - The `--this-repo` help of this subparser contains no "worktree" (row 43).
5. A synthetic smoke after D2, per `docs/transcript-analysis.md` § "Testing against a synthetic corpus", so no real ledger enters the session. Point `CLAUDE_CONFIG_DIR` and `TRANSCRIPT_CONFIG_DIRS_FILE` at an empty throwaway directory and roots file, and unset `PR_COST_LEDGER_PATH`. `python3 claude/.claude/scripts/transcript-analysis.py review-cost-by-size --this-repo` then prints the header, the notice, `_NO_PR_RECORDED_MESSAGE` and zero-count diagnostics, and exits 0.

6. D3's tests show:
   - `pr_cost_ledger_path_overridden()` is true for a non-empty value and false for unset and for the empty string. The pr-cost and export gates each treat an empty value as unset (one test each), and the existing tests that set the literal `PR_COST_LEDGER_PATH` stay literal, including `conftest.py`'s `delenv` and its helper test. Together they fail for a gate that kept its own read.
   - The six `TestCollapsePrCostRowsToCurrent` tests are collected from `test_transcript_pr_cost_ledger.py` and none from `test_transcript_pr_cost_export.py`, by pre- and post-move counts of both files. `TestLatestPrCostRowTieBreakLastWins` is unmodified.
   - The export integration test above fails when dates are truncated before the collapse.
   - `_scope_header` has a four-row unit table over its two booleans: default, `--all-accounts`, and each with the override. A command-level case sets `PR_COST_LEDGER_PATH` to the empty string and expects the non-override label. The success runs keep one override wiring assertion each.
   - Collected-test counts for `test_transcript_ledger_rounds.py` and `test_transcript_review_cost_by_size.py` match before and after the builders move.
   - The pre- and post-D3 line counts of the two review-cost-by-size and ledger test files are recorded in the commit message.

## Out of scope

- The writer fix: a capture-scope column, a repo-scoped `--record` default and the branch-name guard (row 56). The column is a new frozen header (G5). The fix is filed as issue #1426 (row 47, Shipping). Row 50 states what a reader and the writer then do with a row's recorded scope and the branch names a row may join, and Shipping names who confirms the issue carries it.
- review-round-cost's ledger view. Row 48 cuts it, and it would inherit the same capture-scope gap until the writer fix lands.
- Read mode's `CR/PR/RR` column. Row 48 cuts it, and `pr-cost-export` and a direct parse still reach the raw cell (`docs/pr-cost.md:69`).
- A cost-counts ledger view: the engineer dropped it (row 30).
- review-cost-by-size printing `config_dir()`'s `ValueError` text, which can carry `CLAUDE_CONFIG_DIR`'s value (row 42). Echoing that text is the package's shared convention (`scope.py:80`, `scope.py:755`, `read_cost.py:392`, `review_cost_by_size.py:357`; `period_cost.py:1833` is the one fixed-message exception). The `--all-accounts` arm reaches `scope.py:80` through `resolve_scan_roots`, so a fix local to this command would leave that arm echoing.
- `CHANGELOG.md:59`'s "no report reads it yet", a preserved record (row 26).
- Splitting the other over-limit test files (row 24).
- `pr-cost-section.sh` and every transcript mode, whose behavior stays unchanged (D3 edits `pr_cost.py` and `pr_cost_export.py` without changing what they do).
