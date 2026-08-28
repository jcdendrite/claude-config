# Handoff-nudge threshold/block impact analysis

## Context

The user wants to know, across all PRs and all Claude Code accounts on this
machine, whether pushing the handoff-block escalation (PR #691, tightened in
PR #708) and lowering the handoff-nudge absolute-token cap from 360,000 to
150,000 (PR #727) actually reduced dollar/token cost and whether it preserved
or improved review quality — using review-round counts and reviewer-findings
counts as the quality proxy. This is being asked now because both changes have
been live long enough to form an initial read, but the mechanism itself has
kept moving (five distinct threshold/block revisions between 2026-05-26 and
2026-08-23), so no single clean before/after comparison has ever been run
against the current settled state. The intended outcome is a precise,
evidence-grounded verdict — not a redesign of the mechanism — reporting actual
$/token deltas and quality-proxy deltas between a defined "before" era and the
current "after" era, with sample sizes and confidence stated honestly rather
than glossed over.

A first draft of this plan used "share of spend above the nudge's own fire
threshold" as the primary cost metric. Engineer review during plan-review
surfaced that this metric is structurally biased toward showing an
improvement regardless of whether the intervention actually saved money —
see "Metric hierarchy" below for why, and the revised design that replaces
it.

## Approach

Run a fixed instrument battery across all six declared accounts, reporting
outcomes in four tiers rather than one primary/one secondary metric, and
publish the result as a case study. **Tier 0** (`spend-over-threshold`) is a
manipulation check, not a cost result. **Tier 1** — total cost to reach a
shipped unit of work, reported in dollars, measured across all six accounts'
own repos via a new per-account capture step (Phase 2/"Phase C" below) — is
the primary outcome. **Tier 2** (review rounds, findings ratio, hook-denial
friction) is a quality guardrail under non-inferiority framing. **Tier 3** is
a new handoff-overhead mediator instrument: it cannot prove causation, but
without it a Tier 1 improvement and a Tier 0 improvement can't be told apart
from "the mechanism truncated sessions and pushed cost into overhead the
other tiers don't see." Two CLI changes ship with this plan: `reviewer-yield`
gains an absolute `--until DATE` (Tier 2's differencing instrument), and a
new `workstream-cost` subcommand (Tier 3, and Tier 1's cross-account reach)
is added.

**Root problem.** Two threshold/block changes (PR #691 → #708 hard-block
escalation, PR #727 cap 360,000→150,000) shipped on cost-and-quality
reasoning that was never re-measured after the fact; `docs/handoff-nudge.md`'s
Known-limitations bullet records that gap in the repo itself. This plan
produces the measurement that closes it, at the precision the data actually
supports.

### Metric hierarchy: why share-of-spend-above-threshold cannot be primary

Write the mechanism as an inequality: `net saving = (above-threshold spend
avoided) − (handoff overhead incurred)`. `spend-over-threshold` measures only
the first term, and it measures it as a **share** — every dollar of handoff
overhead (a continuation session re-orienting itself after a truncation) is,
by construction, spend that occurs *below* the fire threshold, so it lands in
that share's own denominator. The metric therefore improves as the harm it's
supposed to catch grows, not merely fails to detect it: an intervention whose
entire mechanism is "truncate sessions at threshold T" will show an improved
`spend-over-threshold` share whether or not it saved a single dollar. This
was raised directly by the engineer during plan-review and confirmed by a
dedicated design consultation (recorded here since it is the load-bearing
rationale for this section, not restated at each site that depends on it —
see "Evidence restated across mechanisms" discipline).

Consequently `spend-over-threshold` is retyped from "primary cost metric" to
a **Tier 0 mechanism-engagement gate**: it establishes that the intervention
fired at all (if above-threshold spend and block/nudge counts didn't move
between eras, the eras are indistinguishable and nothing downstream is
interpretable), but it never appears in the report as a cost result on its
own, and no cost headline may cite it without Tier 1's dollar figure in the
same sentence.

### Givens

| # | Given | Why it is a given |
|---|---|---|
| G1 | Per-model prices are vendor-set and hardcoded with a fetch date, so ledger rows carrying different `rate_stamp` values are not directly comparable in dollars. | Anthropic sets prices. `[verified: transcript_analysis/pricing.py:16 — _PRICING_FETCH_DATE = date(2026, 8, 2); docs/pr-cost.md:76 — "Never compare their usd columns directly ... must re-derive dollars from the retained per-class token counts under one rate table"]` |
| G2 | Transcripts Claude Code has already deleted are unrecoverable, so every session-level instrument's before-era window is floored by whatever survives on disk at run time — not by the era definition. | Deleted files are gone. `[verified: docs/pr-cost.md:3; docs/cost-ledger.md:6]` Raising `cleanupPeriodDays` recovers none of it and is separately declined (Out of scope). |
| G3 | `pr-cost` resolves one target repo per run (`gh` identity is pinned once, from the invoking cwd's own `git remote get-url origin`, before any `--all-accounts` looping starts) and refuses (exit 2) on a mismatch. Under `--all-accounts` that identity resolves **once for the whole run**, widening which *accounts'* local corpora get scanned for activity on that one repo — not which repo gets scanned. | pr-cost's own identity check owns this. `[verified: docs/pr-cost.md:90, :82; transcript-analysis.py:7614 pins gh_repo via _resolve_pinned_gh_repo(..., ordinal=redact_ordinals[roots[0].resolve()]) before the per-root loop]` This is why Phase 2 runs one `pr-cost` invocation **per target repo, from a local checkout of that repo**, rather than treating cross-repo reach as unreachable — and why `--all-accounts` is passed on every one of those invocations: it is what lets a single per-repo run credit each contributing account's own corpus into that account's own ledger, and (per A12) it is also what keeps the run from being refused outright. |
| G4 | The two eras differ in every other change merged between them, not only the handoff threshold; no held-out arm exists in the corpus. | The repo's merge cadence is not this plan's to hold still, and constructing a controlled arm is a separate decision. This bounds the study to *observational before/after*: it can falsify a large claimed saving and bound the observed movement; it cannot attribute causality. |
| G5 | `reviewer-yield`'s second table (cited-path edit overlap) cannot be date-windowed even once `until_ts` is exposed. | Its compute layer deliberately leaves the paired tool-result and edit indexes `since_ts`-only. `[verified: transcript_analysis/reviewer_yield.py:432-437 docstring, :452-456]` Only table 1's counts are era-valid. |
| G6 | No signal — session id, timestamp, or content — connects an outgoing session to the continuation session a handoff produces, and this cannot be retrofitted to cover the before era even by adding new logging now. | `.handoff-nudge.log`'s `handoff` line records only the outgoing session's own id (`handoff-record-conversion.sh:22`, `handoff/SKILL.md:174-179`), written before the continuation session exists; `resume-context.sh` launches the continuation as a fresh harness-assigned session with no back-reference. `handoff/SKILL.md:25` additionally forbids embedding the outgoing session's raw id in handoff-file prose. `[verified: dedicated codebase investigation, 2026-08-28 — grepped every cross-session field candidate (resume, handoff, continuation, parent_session, session_chain) against transcript-analysis.py and transcript_analysis/*.py; none exists]` Even a new logging field added today only instruments *future* sessions; the before era's transcripts are already fixed. This is why Tier 3 measures session/branch **shape** (below), not a literal handoff count — see "Tier 3" under Mechanisms. |

### Assumption rows

| # | Assumption | Tag |
|---|---|---|
| A1 | Eras are: **before** = advisory-only regime through 2026-08-16; **excluded** = 2026-08-17 through 2026-08-22; **after** = 2026-08-23 onward. | `[engineer-verified]` |
| A2 | Run now and caveat low-n buckets as directional rather than withholding a result. | `[engineer-verified]` |
| A3 | Enable `pr-cost` fresh in the three accounts that have never recorded it, and record across all six. | `[engineer-verified]` |
| A4 | Rounds = `code-review`/`ready-for-review`/`plan-review` invocations per PR branch; findings = `reviewer-yield` Found-verdict count; hook-denial friction is a secondary signal. | `[engineer-verified]` |
| A5 | `compute_reviewer_yield_data` already accepts an exclusive `until_ts`; only `cmd_reviewer_yield` fails to expose it, passing `since_ts` alone. | `[verified: transcript_analysis/reviewer_yield.py:422-426, :462-469, :578, :585]` |
| A6 | `review-trace` accepts inclusive absolute `--since`/`--until`, a per-event `--branches` filter, `--skill NAME` (one value, from a six-name set), and `--deny-summary`. | `[verified: transcript-analysis.py:10458-10486 subparser; :1751-1757 inclusive-day conversion; :1728-1734 per-event branch attribution; :964-966 REVIEW_TRACE_SKILLS]` |
| A7 | `spend-over-threshold` accepts an absolute `--since DATE`, buckets whole sessions by first timestamp into ISO weeks, and prints per-week session count, above-threshold `$`, total `$`, and share — with "above" defined by `_hook_effective_fire_threshold`, i.e. the **current** min(40% of window, 150,000) applied uniformly to every era. This is Tier 0's instrument; see "Metric hierarchy" above for why its output is a gate, not a result. | `[verified: transcript-analysis.py:10939-10950, :7850-7932, :9288-9297]` |
| A8 | `cost-trend` prints a per-ISO-week table of `$`, ≥200k-context dollar share, and Opus dollar share, bucketed by **turn** timestamp; it has no date flags and covers whatever the corpus holds. | `[verified: transcript_analysis/cost.py:682-732, :788-797]` |
| A9 | 2026-08-16 is a Sunday (last day of ISO 2026-W33); 2026-08-23 is a Sunday (last day of W34); 2026-08-24 is a Monday (first day of W35). Consequence: week-granularity instruments cannot split the after era's first day out of the mixed transition week. | `[unverified]` — hand-derived from the calendar. Re-derive mechanically before relying on it: `python3 -c "import datetime;print([datetime.date(2026,8,d).isocalendar() for d in (16,23,24)])"` |
| A10 | `pr-cost` refuses to capture a PR until `now − mergedAt ≥ --asof-window-days` (default 3.0, `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT`). Held uniform across every account and every repo Phase C touches — a per-account override would flatter whichever account merges fastest. | `[verified: transcript-analysis.py:6610, :7407-7413, :7745-7751]` |
| A11 | `<config-dir>/.handoff-nudge.log` records `nudged session=… est=… model=… window=… event=… [action=block]`, `schema-drift session=… event=…`, and `handoff session=…` lines, with **no timestamp field on any line kind** — session id is the only join key, and (per G6) it only resolves to *that* session's own branch, never to a continuation's. | `[verified: nudge-handoff-near-context-cap.sh:616-617 (block), :635-636 (nudged), :560 (schema-drift); handoff-record-conversion.sh:22; transcript-analysis.py:9503-9559 _parse_nudge_log_entries, whose per-kind field docstring at :9512-9517 lists no branch or timestamp field for any kind]` |
| A12 | Every subcommand in the battery resolves its scan roots as the active account's `projects/` unioned with every root declared in `~/.claude/transcript-config-dirs`, deduped by real path, and `--this-repo` composes across that union. That declared-roots file is read from `$HOME/.claude/` regardless of which `CLAUDE_CONFIG_DIR` is active, so entering a container's `direnv` context does **not** narrow the union. `pr-cost` builds the same union but then **refuses (exit 2) whenever more than one root resolves unless `--all-accounts` is passed — in read mode as well as under `--record`** — and under `--all-accounts` it iterates roots one ledger at a time instead of aggregating them. Consequence: on this machine every `pr-cost` invocation in this plan, including each of Phase C's per-repo preview runs, carries `--all-accounts`. | `[verified: transcript_analysis/scope.py:295-317, :365-368, :489-536 (union built from config_dir() + declared_transcript_roots() at :531); _config_dir.py:33-39 (declared_roots_file() resolves against Path.home(), not config_dir()); transcript-analysis.py:7521-7535 (the >1-root refusal, whose own message covers "or a read that could conflate two accounts' branch/repo data"), :7592 (per-root loop), :7609 (per-root ledger path)]` |
| A13 | The repo already encodes a minimum-denominator convention: `reviewer-yield` prints `insufficient` instead of a rate below `_REVIEWER_YIELD_ACTIVE_FLOOR = 10`. This is the precedent the report's small-n reporting floor adopts rather than inventing a new one. | `[verified: transcript_analysis/reviewer_yield.py:48, :634-638]` |
| A14 | A PR worked on from two accounts yields one partial row per account (each scans only its own corpus), so the cross-account union must **sum** corpus-derived columns per PR and take gh-sourced columns from any single row. The union key is `(host, repo, pr_number)`, never `pr_number` alone — once Phase C targets more than one repo, the same PR number recurs across unrelated repos as a matter of course, and a bare `pr_number` join would silently add two different repos' PRs together. | `[unverified]` — confirm empirically **after Phase 2's backfill completes**, by checking whether any `(host, repo, pr_number)` triple appears in more than one account's ledger; a check run before backfill is a false negative, not a validated answer, since Phase 2 is exactly the mutation that could introduce the duplicates. Grounding leads: `docs/pr-cost.md:82-84`; the ledger's own latest-row key is `(host, repo, pr_number, machine)` `[verified: transcript-analysis.py:7483, :7742-7744]`. |
| A15 | The after-era **claude-config** merged-PR population is **28** as of 2026-08-28 — not the single-digit count an earlier draft of this plan assumed. It therefore clears A13's floor of 10, so median/IQR and percentage reporting are admissible for that bucket rather than raw-values-only. Two corrections ride on that: the *captured* n is 28 minus whatever A10's 3-day as-of window holds back at capture time, so Tier 1 reports the captured denominator it actually has, measured at capture, not the merged count; and the cross-account bucket Phase C adds remains a separate, larger population whose size Phase 0 measures rather than assumes. | `[verified: gh pr list --repo <owner>/claude-config --state merged --search "merged:>=2026-08-23" --limit 200 --json number, run 2026-08-28 → 28]` — re-derive the figure from this same command at the moment the report states it (CLAUDE.md, "Ground every choice"). |
| A16 | `docs/case-studies/` is the repo's declared home for "longer-form writeups ... with primary-source citations ... the empirical record", and `docs/cost-levers-considered.md` keeps "the verdict plus the measured reason, not the full investigation." | `[verified: docs/case-studies.md:3; docs/cost-levers-considered.md:1-17]` |
| A17 | The committed artifact carries **both** scopes: `--this-repo`-scoped figures **and** machine-wide aggregate totals and percentages spanning all six accounts (dollars, tokens, session counts, merged-PR counts, denial and block counts, and — per Phase C — cross-account Tier 1/Tier 3 figures). It carries no figure that could identify a specific account or private project — no per-account row, column, or ordered value list; no repo name, branch name, or per-account directory name other than `claude-config`'s own; no session ids. Aggregation across all six accounts, not repo-scoping, is the publication boundary. A machine-wide cell is only publishable when **at least 2** of the 6 accounts contributed to it — a single-contributor cell is arithmetically identical to a per-account disclosure and must be held back, not relabelled and published. This account-diversity gate is orthogonal to A13's n<10 volume floor; both must pass independently. This is the **sole** privacy control over Phase C's real per-account repo data — see A18. Phase 4 verifies this gate with a machine-readable per-cell contributor-count listing (an uncommitted, throwaway audit artifact — not a new committed script, per Out of scope's reusable-script exclusion) diffed against the drafted case study before staging, not by visual inspection alone. | `[engineer-verified]` |
| A18 | A per-account consent field in the operator's private, uncommitted multi-account credential registry governs whether dollar figures may appear **inside that account's own PR descriptions** — a narrower publication surface than this study's cross-account aggregate. It does not gate inclusion in this study: A17's ≥2-contributor aggregation floor is the sole and sufficient privacy control for what this plan publishes, since no per-account figure is ever published regardless of that field's value. | `[engineer-verified 2026-08-28]` |
| A19 | Each non-personal account maps to **one or more** operator-local container directories, and a container holds **zero or more** git checkouts — a container may be empty, hold a single repo, or hold a bulk-cloned org mirror, and its checkout count is not a proxy for how many of those repos were ever worked in from Claude Code. Each container carries a `direnv` `.envrc` that exports, for that container and every directory beneath it, a `GH_TOKEN`, a `GH_HOST` (the container's own declared host, **defaulting to `github.com` only when the container declares none** — an enterprise host is a first-class configured value, not an exception), and a `CLAUDE_CONFIG_DIR`. `git` needs no wrapper (its credential routing is directory-based), so every step of Phase C's repo-discovery runs unwrapped; only the `gh`-touching steps run under `cd <checkout> && direnv exec . <command>`, reusing exactly the pattern `CLAUDE.local.md` already documents. No new credential is provisioned for any of this. Container names, checkout paths, org names, hostnames, and per-account checkout counts are operator-local and are never written into this plan or the case study (A17) — a private forge hostname is itself an org identifier. | `[verified: direct filesystem and gh census, 2026-08-28 — per-account container count, per-container checkout count, and per-container host and auth status enumerated; direct inspection of each container's own .envrc-generation mechanism, confirming GH_HOST defaults to github.com only when a container declares no host]` |
| A20 | Reusing `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT` (3 days) as the "candidate abandoned branch" threshold — a branch with zero PR match (merged or closed-unmerged) whose most recent local activity is older than this window — is a defensible reuse of an existing, already-justified constant rather than a newly invented number; it is deliberately not doubled or otherwise adjusted; results are reported as raw last-activity-age values (Statistical framing), not as a hard classification. | `[engineer-verified — reuse rather than a new tunable, per "Ground every choice"]` |
| A21 | "Startup burn" is defined as a continuation session's own first 5 main-thread turns' summed dollars (`main_thread_turns[:5]`, from `_extract_rearm_session_turns`'s existing per-turn tuples). 5 is not a new invented cutoff: it is the repo's own first ramp-curve turn-index bucket boundary, already used to mean "a session's early turns" elsewhere in this same file. | `[verified: transcript-analysis.py:9343-9382 _extract_rearm_session_turns return shape (main_thread_turns: list[tuple[context_at_turn, output_tokens, actual_dollars]]); _RAMP_CURVE_TURN_INDEX_BUCKETS at :9253-9264 has 0-5 as its first bucket]` |
| A22 | **Every repo with Claude Code session activity is in Phase C's scope**, and the target set is derived from the transcript corpus itself — which repos sessions actually ran in — rather than from a filesystem crawl of every checkout under a container. A crawl is the wrong-foundation mechanism here: it enumerates repos that were never worked in from Claude Code, and the corpus already encodes the answer. | `[engineer-verified 2026-08-28]` |
| A23 | Every transcript record carries a `cwd` field, and Claude Code names each project directory by sanitizing that same cwd — replacing every `/` **and** every `.` with `-`. That mapping is one-way and lossy, so a project directory name **cannot** be reversed into a checkout path; the `cwd` field inside the records is the only faithful source. Because all sessions in one project directory share the cwd that directory was named from, reading the first `cwd`-bearing record of one transcript per project directory recovers the full set of session working directories, and `slug(cwd) == dir.name` is a free per-directory correctness check on that recovery. Resolving a working directory to a repo identity — main checkout via `git worktree list --porcelain` (whose first `worktree ` line is the main worktree), then `git remote get-url origin` parsed as host + owner/name — needs no credentials at all. | `[verified: transcript_analysis/scope.py:37-45 (the one-way slug, with the .claude/worktrees/<branch> example); transcript_analysis/reviewer_yield.py:140-143 (the first-cwd-bearing-record read pattern already in production) and :339-343 ("Claude Code populates cwd on essentially every record"); transcript-analysis.py:6915-6937 and :6634-6639 (the host/owner/repo parse pr-cost itself uses, which accepts any host and rejects a non-standard port rather than misrouting); scope.py:75-95 (this codebase's existing git worktree list --porcelain parse); git-worktree(1) 'list' subcommand documentation ("The main worktree is listed first, followed by each of the linked worktrees") for the first-line-is-main ordering claim itself]` |
| A24 | **`pr-cost`'s own read mode is the reachability check** — no separate probe and no new code is needed. Before anything else it resolves the invoking checkout's origin host and owner/name, then runs a **host-scoped** `gh auth status --hostname <that host>`, then pins gh's effective repo identity against the local remote (exit 2 on disagreement), then bulk-discovers merged PRs — and in read mode it writes nothing. A **bare** `gh auth status` is not a substitute: it is an aggregate check across every host gh holds credentials for and fails wide on any one of them, which is precisely how the prior draft's probe produced a false negative for an enterprise-hosted container that is in fact fully authenticated. Every gh call `pr-cost` makes is host-qualified (`HOST/OWNER/REPO`), so an enterprise-hosted repo is queried on its own host rather than silently on `api.github.com`; and a `GH_HOST` that disagrees with the checkout's own remote produces a named, non-retried host-mismatch abort rather than a silent misquery. | `[verified: transcript-analysis.py:7056-7071 (_gh_auth_preflight_ok, whose docstring states the bare-status failure mode directly), :7577-7580 (preflight runs before the record/read branch), :7074-7126 (identity pin, exit 2), :7129-7138 (host-qualified --repo), :7141-7163 (bulk discovery), :7622-7627 (read mode returns before the sentinel/ledger-write branch), :7015-7026 (host-mismatch classified, never retried, with an operator-actionable message)]` |
| A25 | `pr-cost` matches a merged PR to local spend **by branch name** (`headRefName` against branch totals computed from the whole in-scope corpus). With one target repo per account corpus that was safe; across many repos in one corpus it is not — a generic branch name recurring in a second repo would fold that repo's dollars into this repo's PR row. Phase C's per-repo runs therefore pass **`--this-repo`**, which scopes the corpus to the invoking checkout's own worktree project directories, matched as exact directory names. The known cost is that a session started in a *subdirectory* of the checkout is silently excluded; Phase 0 sizes that by running each repo's preview both ways once and reporting the difference in uncaptured-PR counts. The claude-config arm deliberately keeps its existing unscoped basis: its already-captured before-era rows were recorded that way (confirmed this session — `docs/pr-cost.md`'s examples and this plan's own pre-rework Phase 2 text never pass `--this-repo`), and switching mid-study would split one component across two measurement bases — a worse confound than the collision risk in a corpus this arm has always had. The asymmetry is stated in the report; it corrupts no comparison, since Tier 1's two components are reported under separate scope labels and no ratio crosses them (A17, Publication scope). | `[verified: transcript-analysis.py:7600 (branch totals from the resolved session iterator), :7464-7489 and :7769-7779 (headRefName matching and the "activity attributed to N other branches" diagnostic); transcript_analysis/scope.py:48-63 (exact-name slug matching, and the documented repo-subdirectory exclusion), :65-68 (fail-closed when cwd is not a worktree of this repo); --this-repo confirmed as an existing flag at transcript-analysis.py:10193-10198 (_add_project_scope_args); historical no-\`--this-repo\`-usage confirmed this session against docs/pr-cost.md and this plan's own pre-rework Phase 2 text, 2026-08-28]` |

### Mechanisms

**Tier 0 — mechanism-engagement gate (never a headline).**
`spend-over-threshold`, run twice (`--since 2026-08-23` for the after era;
an unfiltered run's per-week rows summed for weeks ≤ 2026-W33 for the before
era), plus `action=block`/`handoff` line counts per era from
`.handoff-nudge.log`. Report **both** the share and the absolute
above-threshold dollars, since the share's own denominator contains handoff
overhead (Metric hierarchy, above) and is therefore endogenous to the thing
being measured. Every place this appears in the report carries the sentence:
*"Expected to improve by construction; carries no cost verdict on its own."*
If zero hard blocks are attributable to after-era sessions, the report says
so and reframes the after era as cap-only. `anchors: root`, `anchors: G4`,
`anchors: A11`

**Tier 1 — primary outcome: total cost per shipped unit of work, dollars
primary, tokens decomposed by class.** Two components, reported together
under separate scope labels:

- *`claude-config`-only component (`pr-cost` ledger as before).* Per-class
  **token** counts per PR as the mechanism detail (rate-invariant), dollars
  after confirming a single `rate_stamp` across compared rows. The after-era
  merged population is 28 (A15), which clears A13's floor of 10, so report
  median and IQR alongside the mean — and state the *captured* denominator
  actually achieved, which A10's 3-day as-of window holds below the merged
  count at any given capture moment. Keep listing the per-PR values
  individually anyway: at n in the tens the distribution's shape is still
  the honest presentation, and the value list is what makes the median
  auditable. `anchors: G1`, `anchors: A15`
- *Cross-account component ("Phase C").* The same `pr-cost --record`
  mechanism, run **once per target repo, from a local checkout of that
  repo, under `direnv exec`** (A19) — with `--all-accounts` (A12: without
  it the run is refused, and with it each contributing account's own corpus
  is credited to that account's own ledger) and with `--this-repo` (A25).
  Not `--all-accounts` against a single repo, since G3 means
  `--all-accounts` widens which accounts' corpora get scanned for *one*
  repo, not which repo gets targeted. **The target repo set is derived from
  the corpus, not from the filesystem** (A22): each session's own recorded
  working directory, resolved to a checkout and then to a
  host + owner/name repo identity, deduped across accounts (A23). Every run
  uses the container's existing `gh` credentials and `CLAUDE_CONFIG_DIR`, so
  no new credential is provisioned, and reachability is confirmed by the
  read-mode preview itself rather than by a separate probe (A24). Output is
  folded into the same machine-wide aggregate A17 already governs; no
  per-account, per-repo, per-container, or per-host row is ever printed into
  the case study. `anchors: A22`, `anchors: A24`

Report dollars as the decision variable; tokens decomposed by class (input /
output / cache-create-5m / cache-create-1h / cache-read) as the mechanism
detail, since handoff overhead concentrates in cache-creation and re-read
input while a long uninterrupted session pays cache-read on a large prefix
every turn — tokens and dollars can diverge in *either* direction here, so
"tokens-first" is the wrong default for a cost question.

**Tier 2 — quality guardrail (non-inferiority framing, not a win
condition).**
- **Rounds:** one `review-trace` scan per era, `--this-repo`, counted not
  read — `--since`/`--until` bound each era exactly; redirect to a temp file,
  count per (branch, skill) pair in a second pass. `anchors: A6`
- **Denial friction:** `review-trace --deny-summary`, date-bounded per era,
  grouped counts only. `anchors: A6`
- **Findings:** `reviewer-yield` run three times — `--until 2026-08-16`,
  `--until 2026-08-22`, unbounded — differenced, all three with
  `--this-repo` (the merged-PR denominator is claude-config-only by G3, so an
  unscoped numerator over it is a cross-scope ratio, not a directional
  approximation — **do not** report a per-PR findings figure regardless of
  scope). `anchors: A5`, `anchors: G5`, `anchors: G3`
- **Framing:** a quality *loss* between eras is a stop condition for any
  cost claim (interpreting a cost saving that came with a quality
  regression as a win is exactly the kind of proxy-divergence this plan
  exists to catch); a quality *gain* is not itself a win condition for the
  mechanism, since Tier 2 is a guardrail on Tier 1, not a second outcome to
  optimize.

**Tier 3 — handoff-overhead mediator (new instrument, `workstream-cost`
subcommand).** Explains the *sign* of Tier 1 — the term neither Tier 0 nor
Tier 1 alone can see. G6 rules out a literal handoff count, so this measures
session/branch **shape** instead, explicitly labelled as an approximation of
handoff overhead, not a causal handoff count (a branch with 3 sessions may be
3 genuine handoffs or 3 days of unrelated normal work):

- *Sessions-per-branch and continuation startup-burn — pure transcript data,
  no `gh`, no Phase C, runs corpus-wide across all six accounts for free.*
  Group every session by attributed branch (reusing `_attributed_branch`/
  `_session_branch_index`, `transcript_analysis/cost.py:26-48`, `:51-78` —
  already resolves a `worktree-agent-*` label back to its real branch, so
  ephemeral-isolation dispatches roll up correctly) and order each branch's
  sessions by first-turn timestamp. Report, per era: mean/median sessions
  per branch, and the summed "startup burn" (A21) across every non-first
  session on a branch, as a fraction of that branch's total dollars.
- *Abandoned-branch rate — needs Phase C's `gh` reach for the repos it
  covers.* A branch with local activity and no PR match (merged **or**
  closed-unmerged — `_gh_discover_merged_prs`, `transcript-analysis.py:7168`,
  needs a sibling `--state closed` discovery call to tell "still open" apart
  from "never had a PR," since today's `--state merged` query can't
  distinguish them) whose last activity is older than A20's threshold is a
  *candidate* abandoned branch. Report raw last-activity-age values per
  Statistical framing below, not a hard classification.

`anchors: G6`, `anchors: A20`, `anchors: A21`

**The two code changes, the lighter primitives rejected for each, and the third change deliberately not made.**

1. `reviewer-yield` gains `--until DATE` (inclusive, mirroring
   `review-trace`'s semantics), wired to the `until_ts` parameter
   `compute_reviewer_yield_data` already accepts. `--since` keeps its
   existing `Nd` contract untouched — three runs plus differencing cover all
   three buckets without it. Lighter alternatives weighed and set aside: (1)
   **relative `--since Nd` differencing with fractional days** — the window
   is defined by the invocation instant (`scope.py:674`,
   `time.time() − days*86400`), so a published figure isn't reproducible;
   (2) **`cost-ledger --record`'s per-week windowing** — `--record` appends
   only the current ISO week, so it cannot produce a historical era row at
   all `[verified: transcript-analysis.py:6354-6373 read mode lists week
   *labels* only; :10853-10856]`; (3) **post-filtering `reviewer-yield`
   output** — impossible, pre-aggregated with no per-dispatch timestamps;
   (4) **a throwaway local script calling `compute_reviewer_yield_data`
   directly** — lighter, but the case study's figures must be re-derivable
   by any stow consumer via a committed subcommand. Heavier alternative also
   rejected: adding `--branches` to `reviewer-yield`.
2. A new `workstream-cost` subcommand, default mode pure-transcript
   (no `gh`, corpus-wide), `--check-pr-status` mode additionally calling
   `gh` (repo-pinned like `pr-cost`, run once per repo including via Phase
   C's `direnv` wrapper). Lighter alternatives weighed and set aside: (1)
   **extending `pr-cost` itself** — rejected because `pr-cost`'s per-branch
   loop is unconditionally `--record`-or-read against ledger rows keyed by
   merged PR; sessions-per-branch and startup-burn apply to *every* branch
   with corpus activity, merged or not, and forcing that through `pr-cost`'s
   PR-keyed row shape would mean synthesizing placeholder ledger rows for
   unmerged branches — a heavier distortion of an existing durable-write
   subcommand than a new read-only one; (2) **computing Tier 3 by hand from
   `cost-trend`/`pr-cost` output already in the battery** — rejected because
   neither exposes ordered per-branch session lists or per-turn dollar
   prefixes, so "by hand" would mean re-deriving `_extract_rearm_session_turns`
   output outside the codebase, which is exactly the "post-filtering
   pre-aggregated output" trap `reviewer-yield`'s own rejected alternative
   (3) above already names. Heavier alternative rejected: a new persistent
   ledger for workstream data — this is read-only analysis, mirroring
   `pr-cost`'s own default (unflagged) read mode; no new durable write is
   introduced beyond what Phase C's `pr-cost --record` calls already are.
3. **No third CLI change for repo discovery or reachability — the existing
   primitives cover both.** Reachability needs no code: read-mode `pr-cost`
   already performs the host-scoped auth preflight, the identity pin, and
   the merged-PR discovery, and writes nothing (A24). Discovery needs no
   code either: the corpus's own `cwd` field plus plain `git` answer it
   (A23), as a throwaway operator-local step inside Phase C — the same
   shape A17 already established for Phase 4's uncommitted per-cell
   contributor-count listing, and the same exclusion Out of scope already
   applies to a battery script. The heavier alternative — a committed
   subcommand that prints the distinct checkout paths per root — is
   rejected on more than one-time-ness: its *default output* would be a
   list of home-rooted checkout paths, the one datum every other command in
   this package deliberately redacts (`pr-cost` has no `--no-redact` escape
   hatch at all, `transcript-analysis.py:7517`), and it ships to every stow
   consumer. Lighter alternatives weighed and set aside: (1) **`cost
   --by-project`** — it already aggregates per (account root, project
   family) and collapses a repo's worktrees into one row, but its key is the
   *slug-derived* label, and the slug maps both `/` and `.` to `-`, so it
   cannot be reversed into a path to `cd` into `[verified:
   transcript_analysis/redaction.py:20-22, :31-44;
   transcript_analysis/scope.py:37-45]`; and the only mode that prints real
   labels, `--no-redact`, is refused whenever more than one root is in
   scope, which is the default here `[verified:
   transcript_analysis/scope.py:554-559]`; (2) **`post-crash-sessions.py`'s
   registry `cwd` field** — real and already multi-root, but it covers only
   registry-tracked sessions rather than the retained corpus, so it
   under-covers exactly the historical window this study needs; (3) **a
   filesystem crawl for `.git` under each container** — the over-powered
   primitive this design exists to avoid: it enumerates checkouts that were
   never worked in from Claude Code, and spends live `gh` calls proving it
   (A22); (4) **`pr-cost --all-accounts` alone, per container** — it cannot
   enumerate targets, because it pins exactly one repo per run from the
   invoking cwd (G3).

**Extract before adding the fourth/second copy.** Two independent
extractions, both "pure extraction, no behavior change":

- The six-line inclusive-day conversion is duplicated at **four** production
  sites, not three — `transcript-analysis.py:519-526` (`cmd_user_input`),
  `:1751-1757` (`cmd_review_trace`), `:1856-1863` (`cmd_judgment_pair`), and
  `:2285-2292` (`cmd_subagent_mix`, reading `since_date`/`until_date` attrs
  into differently-named locals — same pattern, different attribute names)
  — plus a test helper at `tests/test_transcript_analysis.py:2229-2234`.
  Adding a fifth for `reviewer-yield --until` is the locally-valid patch
  CLAUDE.md's "abstract into a shared helper once two or more share it" rule
  fires on. Extract `_parse_absolute_window_args(args, subcommand,
  since_attr="since", until_attr="until") -> (since_ts, until_epoch)` into
  `transcript_analysis/scope.py` beside its exact sibling
  `_parse_since_nd_arg` (`scope.py:661`) — `scope.py` already imports from
  `transcript_analysis.corpus` at line 32, so pulling `_parse_ts` in adds no
  cycle — and route all four existing sites plus the new one through it,
  passing `since_attr="since_date", until_attr="until_date"` at
  `cmd_subagent_mix`'s call site and the defaults everywhere else.
- `_compute_pr_cost_branch_totals` (`transcript-analysis.py:7355-7404`)
  already implements "group every session by branch and sum" — exactly
  `workstream-cost`'s own foundation — but lives in the CLI script rather
  than the `transcript_analysis` package, where its two dependencies
  (`_attributed_branch`, `_session_branch_index`) already live
  (`cost.py:26-48`, `:51-78`). Move it into `cost.py` alongside them, extend
  it there with a per-branch ordered session-timestamp list and a
  last-activity timestamp (neither tracked today — `_new_pr_cost_agg`,
  `:7343-7352`, has no timestamp field), and have both `pr-cost` and the new
  `workstream-cost` subcommand import the one extended function. This is the
  same "abstract into a shared helper once two or more share it" rule, not a
  new principle — the second consumer is what triggers the move, not
  `workstream-cost`'s existence on its own.

**Phase C closes the reach gap, and derives its own targets from the
corpus.** An earlier draft of this plan stated the per-PR arm was
permanently limited to `claude-config`'s own PR history because reaching
other accounts' repos "needs credentials this session doesn't hold." That
was true only for the `gh`-enrichment layer, resolved once per invocation
(G3) — not for reaching the repos at all. A later draft over-corrected in
the other direction, assuming one repo per account and probing reachability
with a bare `gh auth status`; both halves of that were wrong. An account
maps to one or more containers, a container to zero or more checkouts
(A19), and a bare `gh auth status` reports a *false negative* for a
container whose credential is valid only on its own enterprise host (A24) —
so the prior draft would have excluded a fully usable account on the
strength of a mis-posed probe.

The corrected design: **derive, confirm, then write.** The target set comes
from the transcript corpus — the checkouts sessions actually ran in,
resolved to repo identities (A22, A23) — not from a filesystem crawl, and
not from an assumed one-repo-per-account. Reachability is confirmed per
repo by the read-mode `pr-cost` preview, which performs the host-scoped
auth check against **that repo's own origin host** and writes nothing
(A24). Only a repo whose preview lists at least one uncaptured merged PR
proceeds to `--record`; a repo with no such PR needs no write and consumes
no further `gh` budget. Exclusions are recorded **per repo and by category**
— not per account, and never by name — because "this account is
unreachable" is not a category this machine's shape actually produces: an
account can contribute from one container while a second container of the
same account is out of `pr-cost`'s scope entirely, for reasons no
credential work would change.

**Publication scope.** Every figure `pr-cost --record` produces per account
under Phase C is subject to the same discipline A17 already established for
the session-level arm — sum first, publish second, no per-account or
per-repo row ever staged. Every committed figure names its own scope in
situ (`claude-config`-only vs. machine-wide), and no ratio divides a figure
of one scope by a figure of the other — where a denominator is
`claude-config`-only by G3 (anything per-merged-PR, including the findings
ratio), its numerator is produced with `--this-repo` as well (A12); everywhere
else, including every Tier 1 and Tier 3 cross-account figure, both sides are
machine-wide.

**Statistical framing.** Report raw counts everywhere (PRs, sessions,
dispatches, denials per bucket) alongside every ratio, and the *observed*
date window per bucket rather than the nominal one. Adopt A13's floor: any
bucket whose denominator is under 10 is reported as raw values plus a point
delta labelled **directional, not decisive** — no percentage headline, no
confidence interval, no p-value, no bootstrap (the repo's bootstrap
precedent in `opus-plan-boundary-handoff.md` ran 2,000 session-level
resamples on a large arm; at single-digit n it manufactures precision).
Every ratio prints its numerator and denominator. Add, as named limitations
rather than footnotes: (1) the Tier 0 arithmetic-bias statement from "Metric
hierarchy" above; (2) a **pre-registered decision rule**, written before the
Phase 3 numbers are read: if after-era median dollars-per-shipped-unit —
Tier 1's claude-config-only component, the only one with a defined
median/IQR (A15) — exceeds the before-era IQR's upper bound while Tier 0
improves, the report states the result as overhead dominance and names the
#727 cap as a candidate for reversal, rather than reporting the Tier 0
improvement as the headline; (3) a **threshold-sensitivity check**: recompute Tier 0 at
100,000 / 150,000 / 250,000 / 360,000 and disclose any sign flip; (4) **no
significance testing** — at the n this corpus produces, report descriptives
and individual points, and pre-commit to "inconclusive" as an acceptable,
stated outcome rather than a headline built on an underpowered comparison.
Close with a **numeric revisit trigger** — a stated after-era PR count at
which the battery is re-run — matching the convention the case-study index
already uses; this is a trigger for a *follow-up*, not a statement that the
current run is merely an interim look, since the after-era population is neither single-digit (A15 measured the
claude-config-only bucket at 28) nor limited to one repo (Phase C's derived
target set), so the revisit trigger is a threshold for a follow-up, not an
admission that this run is merely an interim look.

**Confounds the report must name explicitly, in this order:** (1) G4
co-intervention, the dominant threat; (2) G2's retention floor, measured not
assumed; (3) A10's as-of window, held uniform across every account and every
repo Phase C touches, so the after arm isn't flattered by early capture in
whichever account merges fastest; (4) within-era heterogeneity — the before
era spans four threshold regimes (flat 120,000 → 60% → 40% → 40%∧360,000),
so it is a *no-hard-block* baseline, not a *360,000-cap* baseline; a
secondary sensitivity arm restricted to 2026-08-08..2026-08-16 gives the true
immediately-prior comparison; (5) A9's one-day granularity mismatch; (6)
volume — era totals are not comparable, only per-PR / per-session /
per-priced-turn normalizations are; (7) G6 — Tier 3's session/branch-shape
proxies approximate handoff overhead, they do not count actual handoffs, and
this is a permanent measurement-design limit, not a gap this plan's own code
change could close.

### Phases and dispatch split

- **Phase 0 — Preconditions census (read-only).** Measure, don't assume:
  resolved root count and each root's oldest surviving transcript (the
  before-era floor); the existing ledgers' `machine` label, `rate_stamp`
  value set, and pre-existing row count per account (aggregate only — a
  pre-existing row captured under different flags is what would put one
  component on two measurement bases, A25); whether `PR_COST_LEDGER_PATH`
  or `GH_REPO` is set (both would break Phase 2); which accounts hold
  `.pr-cost-enabled`, and the resolved config-dir path for each that
  doesn't. Then, **the Phase C target derivation and preview** (no
  `--record`, nothing written anywhere):
  1. *Derive.* Across every resolved root, for each project directory, read
     the first `cwd`-bearing record of one top-level transcript (A23) and
     collect the distinct working directories, checking `slug(cwd) ==
     directory name` per directory and counting mismatches rather than
     guessing at them. **Unbounded in time** — the study's lower bound is
     the corpus's own retention floor (G2), so the whole retained corpus
     *is* the study window and no new date threshold is introduced. Resolve
     each working directory to its main checkout and then to a
     host + owner/name identity with plain `git` (no credentials, no
     `direnv`), and dedupe across accounts on the case-folded identity, so
     one repo is one target no matter how many accounts' sessions touched
     it. One Bash statement, output redirected to a temp path outside the
     repo, per Phase 3's own convention; the file is throwaway, never
     committed, never pasted, and deleted after Phase 2.
  2. *Categorize the non-targets, by count only.* A working directory that
     no longer resolves to a git checkout; a checkout with no `origin`
     remote configured, or whose origin host is not a GitHub host `gh` can
     query at all (out of `pr-cost`'s scope by construction — `pr-cost` is
     `gh`-only; this is not an auth gap and no credential work would close
     it — a checkout with no `origin` remote counts under this same
     category, not a separate fifth one); and two categories completed only
     after step 3 runs — a repo whose preview shows no uncaptured merged
     PR, and a repo whose preview aborts on auth or host mismatch. Four
     categories, four counts, no names, no per-account decomposition; every
     derived working directory lands in exactly one.
  3. *Preview each target.* `cd <that repo's own checkout> && direnv exec .
     python3 …/transcript-analysis.py pr-cost --all-accounts --this-repo
     --machine-label <the label already in the ledger>` (A12 for
     `--all-accounts`, A25 for `--this-repo`, A24 for what the run itself
     verifies). Record, per repo, the uncaptured-PR count, then return to
     step 2 and complete its last two categories using this step's results.
     Run each repo's preview **once more without `--this-repo`** and record
     the difference in uncaptured-PR counts — that difference is A25's
     scoping cost, reported as a named limitation rather than assumed to be
     zero. Never record the checkout path, the hostname, the org, or `gh
     auth status`'s own login/org output anywhere in this repo.

  Also measure: per bucket (before/excluded/after), how many of the 6
  accounts hold at least one contributing session or ledger row, flagging
  any bucket at 1 contributing account (A17) or fewer than 10 (A13); and
  total session and file count across the root union, as a duration signal
  for Phase 3's battery. Output: the exact before-era start date every later
  command uses; the target repo list (temp file, operator-local); and the
  real after-era PR counts for both the claude-config-only population
  (A15's 28, re-derived at read time) and the cross-account population.
  **Not this phase:** A14's cross-account duplicate check — Phase 2's
  backfill is the mutation that could introduce the duplicate, so that
  check runs after Phase 2.
- **Phase 1 — CLI changes, two sequential `code-writer` dispatches (`model:
  sonnet`), both must land before Phase 3 consumes them.** 1a and 1b both
  edit `transcript-analysis.py` (1a rewires four call sites plus the
  `reviewer-yield` subparser; 1b removes the relocated function body, adds
  the `--state closed` discovery call, and adds the `workstream-cost`
  subparser) — their edit ranges don't overlap today, but "disjoint files"
  would overstate that; **1a's dispatch runs to completion and its commit
  lands before 1b's dispatch starts**, so 1b's code-writer sees 1a's
  already-edited file rather than two independent dispatches racing to edit
  the same file. 1a: `reviewer-yield --until` (extraction + flag + tests +
  doc). 1b: the `workstream-cost` subcommand (extraction/relocation of
  `_compute_pr_cost_branch_totals` into `cost.py` + the new subcommand +
  `--state closed` discovery + tests + doc).
- **Phase 2 — pr-cost enablement and capture, including Phase C. Not a
  `code-writer` dispatch for this entire phase — durable writes plus live
  `gh` calls, across real credentials, stay in the operator-visible session
  throughout. Starts only after Phase 1b's own behavior-preservation
  verification has passed** — Phase 2's `pr-cost --record` call site depends on Phase 1b's
  relocated `_compute_pr_cost_branch_totals`, so running Phase 2 against a
  mid-refactor `cost.py` risks corrupting real ledger state across up to 6
  accounts before Phase 1b's own verification would have caught the
  regression. (Phase 0's previews are unaffected by that ordering: they run
  before any refactor starts, and read mode writes nothing.) Create
  `.pr-cost-enabled` as a **regular file per account, never a symlink**
  (`docs/pr-cost.md:84`), at each config-dir path Phase 0 resolved.

  **claude-config arm (unchanged basis, A25).** Run `pr-cost
  --all-accounts` in **read mode first** (the preview) against
  `claude-config`, then `--record --all-accounts --machine-label <the label
  already in the ledger>` after the operator has seen that listing. **A
  per-account ledger-parse failure aborts the whole `--all-accounts` loop
  via `sys.exit(1)`** even though rows already recorded before the failing
  account persist: treat a nonzero exit code as the retry signal, not the
  printed `recorded X of N` summary line, which counts only rows newly
  written in that invocation and legitimately reads below 6 on a
  fully-recovered ledger. The completeness signal is the read-mode
  preview's uncaptured-PR listing going empty. A `degraded_*` row is
  resolved here (recapture with `--force --pr N`, or excluded and the
  exclusion recorded in Phase 4's raw counts), not deferred to
  Verification.

  **Phase C, additionally — derive, confirm, then write, once per target
  repo.** Re-run Phase 0's derivation step immediately before the sweep and
  diff it against Phase 0's list: a repo whose first session landed between
  the two runs is a target too, and a stale list would silently drop it.
  Also re-preview, not only re-derive, every repo Phase 0 excluded under
  category 3 ("no uncaptured merged PR") — that exclusion is PR-status-
  dependent and can go stale in the window between Phase 0's census and
  this sweep (nothing pauses account activity in that window; Phase 3's
  pause covers only the battery itself), unlike the other three categories,
  which are identity-dependent and stable. A repo whose first mergeable PR
  merges in that window is a target too, and the identity-only diff above
  would not catch it. Then, for each target repo, in this order:
  1. *Confirm identity, host-scoped.* From that repo's own checkout,
     `direnv exec .` and print the resolved `CLAUDE_CONFIG_DIR` (via
     `printenv CLAUDE_CONFIG_DIR`), the checkout's own `git remote get-url
     origin` host and owner/name, and `gh auth status --hostname <that
     host>` — **the host from the repo's own remote, never a bare `gh auth
     status`**, which is an aggregate check across every host gh holds
     credentials for and returns a false negative for an
     enterprise-hosted account that is in fact authenticated (A24). Assert
     the printed `CLAUDE_CONFIG_DIR` matches the account Phase 0 expected
     with a scripted equality check (`[ "$(direnv exec . printenv
     CLAUDE_CONFIG_DIR)" = "<expected config-dir>" ] || exit 1`) **before**
     anything is written — not an eyeball-only comparison, since this step
     repeats across up to six accounts in one sitting and operator fatigue
     is a realistic failure mode at that volume.
  2. *Preview.* The same read-mode invocation Phase 0 used
     (`--all-accounts --this-repo`, no `--record`). This mirrors the main
     loop's own preview-before-write pattern rather than deferring the
     identity check to an end-of-phase audit, and it is deliberately
     re-run here even though Phase 0 already previewed: Phase 0's run was a
     census, this one is the immediately-before-write confirmation.
  3. *Record.* Only for a repo whose preview listed at least one uncaptured
     merged PR, and only after step 1's confirmation: the same invocation
     plus `--record --machine-label <label>`. A repo with an empty preview
     needs no write; count it, don't run it.

  The sweep is **resumable and idempotent**: each repo's run is independent,
  the ledger is append-only, and an already-captured `(host, repo,
  pr_number, machine)` row is skipped rather than re-appended absent
  `--force`, so a rate-limit or network abort mid-sweep is retried from the
  same list without special handling. Rate-limit exhaustion is the expected
  transient at this volume — each repo's run makes at least two `gh` calls
  plus one per uncaptured PR, with `gh pr list` bounded at a fixed
  `--limit` — and it is retried once and counted **separately** from the
  Phase-0-excluded categories, using the same nonzero-exit-is-retry-signal
  discipline, never silently folded into "excluded." A repo excluded at
  Phase 0 is skipped, counted by category, and never named.

  Two exceptions the derivation can surface, both counted and stated rather
  than silently absorbed: a repo identity with **more than one local
  checkout** that has session activity (`--this-repo` scopes to one
  checkout's worktrees, so the second checkout's sessions would be missed —
  run that repo once without `--this-repo`, accepting corpus-wide branch
  matching for it alone, and record the exception in the report's
  limitations); and a checkout whose origin host disagrees with its
  container's ambient `GH_HOST`, which aborts with a named host-mismatch
  classification rather than misquerying another host (A24).

  If a per-repo identity confirmation is ever caught mismatched **after** a
  `--record` call already ran, the remediation is: back up the wrong
  account's `pr-cost-ledger.tsv` first (`cp` to a sibling path), locate the
  misattributed row by its full `(host, repo, pr_number, machine)` key —
  not `host`/`repo` alone, which is not unique once a repo has more than
  one captured PR, the common case rather than the edge case given A15's
  28-PR claude-config population — delete that line by hand (the ledger is
  append-only and has no `--unrecord` operation), assert the row count
  dropped by exactly one and every remaining row for that `host`/`repo`
  pair is unaffected, and check every other row that container's session
  wrote before the mismatch was caught (a wrong `direnv` resolution is the
  root cause, and it does not affect only the one row that surfaced it)
  before re-running `--record` from the correct checkout. **After the
  backfill completes, check A14 empirically on the `(host, repo,
  pr_number)` key.**
- **Phase 3 — Instrument battery.** Deterministic command list, all runs
  back-to-back in one sitting so every instrument sees the same corpus.
  **Pause other Claude Code activity on all 6 accounts for the battery's
  duration** — estimate the actual pause window from Phase 0's session/file
  count signal before starting, rather than leaving it open-ended. Corpus
  growth mid-battery breaks the corpus-stability check in Verification,
  which re-runs and compares only **one** instrument at the end — a partial
  check that catches gross violations, not every instrument's own drift,
  so treat a pass as "no gross violation detected," not a full guarantee
  that no corpus growth occurred anywhere. Each command is a single Bash statement; where output
  must be counted rather than read, redirect once to a temp path outside
  the repo and count in a second call. Before trusting the `reviewer-yield`
  differencing, count dispatch records with a missing or unparseable
  timestamp (an untimestamped dispatch is silently counted in the unbounded
  run and excluded from every bounded run — `reviewer_yield.py:462-469`
  only filters when a bound is actually passed). Run `workstream-cost`'s
  default (pure-transcript) mode once, corpus-wide, covering all six
  accounts with no `gh` calls. Run `workstream-cost --check-pr-status` once
  per repo Phase C reached (claude-config directly; each other account via
  the same `direnv exec` wrapper Phase 2 used). Capture the raw pass/fail
  number for each of Verification's internal-consistency checks into the
  same temp path as the counted output — Phase 4 cites them. No new script
  under `~/.claude/scripts/` — that is the over-powered primitive for a
  one-time battery.
- **Phase 4 — Author the artifacts.** Kept in the session that holds the
  measurement output; not split across dispatches, since every dispatch
  would need the same measurement context restated and could reach a
  different verdict on the same numbers.

## Critical files

**Phase 1a dispatch — one `code-writer` (`model: sonnet`), runs and lands
first; 1b's dispatch starts only after this one's commit lands:**

- `claude/.claude/scripts/transcript_analysis/scope.py` — add
  `_parse_absolute_window_args(args, subcommand) -> tuple[float | None, float
  | None]` beside `_parse_since_nd_arg` (line 661). **Reuse:**
  `corpus._parse_ts` (already reachable — `scope.py:32` imports from
  `transcript_analysis.corpus`); replicate `cmd_review_trace`'s exact
  inclusive-day semantics and its explanatory comment
  (`transcript-analysis.py:1751-1752`).
- `claude/.claude/scripts/transcript-analysis.py` — replace the four
  duplicated conversions at `:519-526`, `:1751-1757`, `:1856-1863`, and
  `:2285-2292` with calls to the new helper (the last passing
  `since_attr="since_date", until_attr="until_date"`); add `--until` to the
  `reviewer-yield` subparser at `:10367-10387`, using `type=_iso_date` and
  the same help wording shape as `:10468`, with one added sentence stating
  that the flag bounds dispatch detection only and leaves the
  cited-path-overlap table unwindowed (G5).
- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` — in
  `cmd_reviewer_yield` (`:529`), resolve the until bound through the new
  helper and pass `until_ts=` to the existing `compute_reviewer_yield_data`
  call at `:585`. Extend **table 1's** heading (`:592`) to a label naming
  both bounds. **Table 2's** heading (`:614`) stays since-only (G5) and
  gets its own short caveat appended beneath it.
- `claude/.claude/scripts/tests/test_transcript_reviewer_yield.py` — four
  functional `--until` tests beside `test_since_filter_excludes_out_of_window_dispatch`
  (`:310`): a boundary test with reviewer dispatches on both sides of the
  `--until` date (sub-second boundary precision, matching the existing
  corpus fixture style); an additivity test over timestamped-only fixtures
  (`count(--until B) + count(rest) == count(unbounded)`), plus a **second,
  separate** assertion using a missing/unparseable-timestamp fixture,
  checking the known delta rather than equality (`compute_reviewer_yield_data`
  only filters when a bound is passed, `:462-469`, so that fixture is
  counted unbounded and dropped from both bounded runs); a combined
  `--since 35d --until DATE` test asserting the result is the AND of both
  bounds (nothing in argparse blocks passing both, and
  `compute_reviewer_yield_data` applies both bounds independently); a
  regression test on the actual filtered table-1 output; an empty-corpus
  case; a case confirming table 2's row count and heading are unaffected by
  `--until`.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — a regression
  assertion that `--since 35d` still parses as a relative window; re-point
  the local helper at `:2229-2234` to the extracted function; a regression
  assertion that `cmd_subagent_mix`'s `--since-date`/`--until-date` window
  still parses correctly through the parameterized helper. No
  `reviewer-yield`-specific test content moves here.
- `docs/transcript-analysis.md` — document `--until` in the `reviewer-yield`
  section, including the table-2 caveat.

Verification command for this dispatch: `.venv/bin/python3
claude/.claude/scripts/select-tests.py` and `.venv/bin/ruff check
claude/.claude/`.

**Phase 1b dispatch — one `code-writer` (`model: sonnet`), starts only
after Phase 1a's dispatch has landed:**

- `claude/.claude/scripts/transcript_analysis/cost.py` — move
  `_compute_pr_cost_branch_totals` (currently `transcript-analysis.py:7355-7404`)
  here, beside its dependencies `_attributed_branch` (`:51-78`) and
  `_session_branch_index` (`:26-48`). Extend `_new_pr_cost_agg`'s dict shape
  (currently `transcript-analysis.py:7343-7352`) with an ordered
  `session_starts: list[tuple[float, str]]` (timestamp, session id) and a
  `last_activity_ts: float` field, appended to alongside the existing
  `sessions` set population at `:7389` — **note that `iter_sessions` yields
  in file-path sort order, not chronological order** (`corpus.py:88-107`),
  so `session_starts` must be sorted by its timestamp element (ascending)
  before "first"/"non-first" is determined; do this once, in
  `compute_workstream_dollars`, rather than relying on append order. Add a
  new `compute_workstream_dollars(session_iter, until_first_n_turns=5) ->
  dict[branch, dict]` that, per branch, sorts `session_starts` by
  timestamp and sums each non-first (by that sorted order, never file-path
  encounter order) session's first `until_first_n_turns` main-thread
  turns' dollars (`_extract_rearm_session_turns`'s `main_thread_turns`,
  `transcript-analysis.py:9343-9382` — summing fewer than
  `until_first_n_turns` turns when a session has fewer, never padding or
  scaling) as `"startup_burn_dollars"`, alongside the branch's total
  dollars already computed. **Reuse, no duplicate logic:** `pr-cost`'s own
  call site (`transcript-analysis.py:7627`) is updated to import the
  relocated function from `cost.py` rather than calling a module-local
  one.
- `claude/.claude/scripts/transcript-analysis.py` — remove the relocated
  function body; re-import it into this module's own top-level namespace
  (`from transcript_analysis.cost import _compute_pr_cost_branch_totals  #
  noqa: F401`, mirroring `_attributed_branch`'s existing re-import at
  `:45`) so the test suite's existing `_mod._compute_pr_cost_branch_totals`
  access paths keep resolving without their own edits; add a `--state closed` sibling
  discovery call beside `_gh_discover_merged_prs` (`:7168-7190`) — same `gh`
  call shape, different `--state` value — returning a set of branches with a
  closed-but-unmerged PR, so the new subcommand's abandoned-branch check can
  distinguish "closed unmerged" from "no PR match at all" rather than
  conflating them; add the `workstream-cost` subparser (default mode: no
  `gh`, corpus-wide via A12's existing root-union; `--check-pr-status`
  flag: also calls both discovery functions, repo-pinned like `pr-cost`
  via the same `_resolve_pinned_gh_repo` call `pr-cost` already uses);
  `cmd_workstream_cost` prints, per era-bucketed run: sessions-per-branch
  (mean/median), summed startup-burn dollars as a fraction of branch-total
  dollars, and (only under `--check-pr-status`) each zero-PR-match branch's
  last-activity age.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — tests for the
  relocated `_compute_pr_cost_branch_totals` (moved, not rewritten — same
  assertions, new import path) plus `--state closed` discovery.
- `claude/.claude/scripts/tests/test_transcript_workstream_cost.py`
  **(new)** — fixtures covering: a branch with 1 session (startup-burn
  contributes 0, since there's no non-first session); a branch with 3
  sessions in reverse file-path order relative to their timestamps (proves
  "first" is determined by sorted timestamp, not iteration order — only
  the two chronologically-later sessions contribute startup-burn); a
  non-first session with only 2 main-thread turns (startup-burn sums
  exactly those 2 turns' dollars, not scaled or padded to 5); a
  `worktree-agent-*` labelled session correctly rolling up to its real
  branch; `--check-pr-status`'s three-way branch classification (merged /
  closed-unmerged / no match at all) against a fixture exercising all
  three; an empty-corpus case; a direct assertion that
  `startup_burn_dollars <= total_dollars` for every branch in a
  multi-session fixture (the Tier 3 sanity invariant Verification also
  spot-checks live in Phase 3 — this makes it a merge gate, not only a
  runtime spot-check).
- `docs/transcript-analysis.md` — document `workstream-cost`, both modes,
  and A21's startup-burn definition (5 turns, citing the ramp-curve bucket
  precedent so a future reader isn't left wondering why 5).

Verification command for this dispatch: `.venv/bin/python3
claude/.claude/scripts/select-tests.py` and `.venv/bin/ruff check
claude/.claude/`.

**Phase 4 — authored in-session from the measurement output, not
dispatched:**

- `docs/case-studies/handoff-threshold-impact.md` **(new)** — the full
  empirical record: methodology, the four-tier structure and why Tier 0 is
  a gate not a result, per-era instrument tables, raw counts, every
  confound above, and the numeric revisit trigger. Tables split into two
  scope-labelled groups per A17: **machine-wide aggregate** (Tier 0, Tier 1
  cross-account, Tier 3, denial counts, block counts, session counts) and
  **`claude-config`-only** (Tier 1's claude-config component, Tier 2's
  rounds and findings ratio). Name the scope in each table's own heading.
  **Reuse:** follow `docs/case-studies/review-vs-babysitting.md`'s
  structure.
- `docs/case-studies.md` — one index bullet, matching the existing
  "grounded in *N* …" phrasing convention at lines 7-14.
- `docs/cost-levers-considered.md` — a new `## From
  \`handoff-threshold-impact-analysis.md\`` section, one row per question
  answered, headline figure plus scope label, deferring to the case study
  for the investigation.
- `docs/handoff-nudge.md` — in the Known-limitations bullet at line 118,
  replace **only** the trailing clause "The 1.25x–4x figures are from that
  earlier 360000-introducing transition, not re-measured for the
  360000→150000 drop … but by an unmeasured amount" with the measured
  result — scope named inline — plus a link. The preceding sentences record
  what an earlier measurement found and are a historical record under
  CLAUDE.md Axis 3 — leave them byte-identical.

**Phase C repo discovery and reachability — no dispatch, no new repository
file.** Both are covered by primitives that already exist (A23, A24); the
rejected third CLI change and the lighter alternatives weighed against it
are recorded under "the third change deliberately not made," above. Phase
1a and Phase 1b are unaffected by this arm.

**Files deliberately not touched:** `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`,
`handoff-record-conversion.sh`, `resume-context.sh`, and every constant in
them. This plan measures; it does not retune, and (per G6) it does not add
handoff→continuation logging — even if added now, that would only
instrument future sessions, not this study's before era.

**Local state written outside the repo (not repository files, listed so the
blast radius is explicit):** `<config-dir>/.pr-cost-enabled` in three
accounts; `<config-dir>/pr-cost-ledger.tsv` appends in up to six accounts —
one row per (target repo, merged PR, contributing account) under Phase C,
so the row count scales with the derived target set rather than with the
account count; no new file type is introduced. One throwaway operator-local
file outside the repo holds Phase C's derived target list and the
per-instrument counted output (Phase 3's own temp-path convention); it is
never committed, never pasted, and deleted after Phase 2.

## Verification

**Phase 1, mechanical (both dispatches).**
- `.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes;
  `.venv/bin/ruff check claude/.claude/` clean; `scripts/list-shell-files.sh
  | xargs -0 .venv/bin/shellcheck` unaffected (no shell touched).
- Phase 1a: the extraction is behavior-preserving — pre-existing tests
  covering `review-trace`, `user-input`, `judgment-pair`, and `subagent-mix`
  date bounds pass unchanged; `test_transcript_analysis.py`'s only edits are
  re-pointing the helper and the two new regression assertions (`--since
  35d`, and `subagent-mix`'s differently-named date-window flags).
  `reviewer-yield --until 2026-08-16` and `--since 35d` both parse; `--until`
  rejects a malformed date via `_iso_date`.
- Phase 1b: `_compute_pr_cost_branch_totals`'s relocation is behavior-preserving
  — its existing test assertions pass unchanged under the new import path.
  Read-mode output is byte-identical before and after the move: the
  `code-writer` dispatch captures `pr-cost` read-mode output **as its first
  action, before any edit**, and diffs it against the same read-mode
  invocation after the move. `--record` output is **not** diffed
  stdout-to-stdout against the same real ledger (a second `--record` run
  against an already-appended ledger legitimately prints a different
  summary even with zero behavior change) — instead, run `--record` once
  before the move and once after against two copied throwaway ledger
  files, and diff the two runs' **appended row fields** against each
  other, not raw stdout. `workstream-cost`'s default mode makes zero `gh`
  calls (assert via a fixture with no network mock configured — a `gh`
  call attempt fails loudly rather than silently succeeding).

**Phase 2, before interpreting anything.**
- The read-mode preview's uncaptured-PR list is inspected first; the
  subsequent `--record` run must produce exactly those rows and no others.
- Re-run read mode afterward and assert: every compared row carries the
  same `rate_stamp`; no compared row's `status` is other than `ok`; the
  latest row per `(host, repo, pr_number, machine)` is the one used.
- Confirm the sentinel in each newly-enabled account is a regular file, not
  a symlink: `test -f <path> && ! test -L <path>` against each of the 3
  config-dir paths Phase 0 resolved.
- **Phase C specifically:** for each repo reached, confirm the row(s)
  written carry that repo's own `host`/`repo` identity (not
  claude-config's) and that `direnv exec`'s resolved `CLAUDE_CONFIG_DIR`
  matched the account Phase 0 expected — a `direnv` misconfiguration
  silently recording under the wrong account's ledger is the failure mode
  this check exists to catch. This is a second, redundant backstop after
  the per-repo pre-flight identity confirmation, not the only control.
- **Phase C target-set completeness:** the derivation re-run at the top of
  Phase 2 is diffed against Phase 0's list, and the diff is recorded (empty,
  or the added repos named only by count). Every derived working directory
  is accounted for in exactly one bucket — targeted, or one of the four
  non-target categories — and the buckets sum to the derived total. A
  working directory in no bucket is a hole in the derivation, not a repo
  with nothing to say.
- **Reachability probe shape:** every auth probe run in Phase 0 or Phase 2
  passed `--hostname` with the host taken from that repo's own origin
  remote. A bare `gh auth status` anywhere in the transcript of this study
  is a defect, not a data point (A24).
- The A14 cross-account duplicate check (run after backfill) is recorded on
  the `(host, repo, pr_number)` key: either no triple repeats across
  accounts, or the sum-corpus/take-gh-from-one-row union rule was applied
  and is stated in the report.

**Phase 3, internal consistency — each of these is a real failure mode, not
a formality.**
- **Monotonicity:** the three `reviewer-yield` runs must satisfy
  `n(--until 2026-08-16) ≤ n(--until 2026-08-22) ≤ n(unbounded)`. A
  violation means the differencing premise is wrong; stop.
- **Corpus stability:** re-run one instrument at the end of the battery and
  compare with its first run. A changed figure invalidates cross-instrument
  arithmetic and the battery is redone in one window.
- **Boundary-spanning sessions:** confirm no session's turns straddle an era
  boundary. If any does, the report states which convention `cost-trend`
  (turn-bucketed) and `spend-over-threshold` (session-bucketed) each used.
- **Treatment applied:** at least one `action=block` line attributable to
  an after-era session, or the report reframes the after era as cap-only.
- **Falsification:** run the same battery over the *excluded*
  2026-08-17..22 window. A genuine mechanism effect should place the
  transition between the two eras.
- **Sensitivity arm:** re-run the before-era instruments restricted to
  2026-08-08..2026-08-16. If the verdict flips against the full before era,
  the reported effect is a threshold-regime artifact.
- **Untimestamped-dispatch check:** the count of missing/unparseable-timestamp
  dispatch records is stated in the report; if nonzero, the additivity
  check above is caveated rather than treated as clean.
- **Tier 3 sanity check:** every branch `workstream-cost` reports must have
  ≥1 session (a branch with 0 sessions is a bug in the branch-attribution
  join, not a real workstream) and a startup-burn dollar figure ≤ that
  branch's total dollar figure (a value exceeding the total means the
  non-first-session filter double-counted the first session).

**Phase 4, publish safety.**
- Every machine-wide-scoped command is consumed as a count or a summed
  total; no `review-trace` timeline, `_print_resolved_scope` header, ledger
  line, or `workstream-cost --check-pr-status` per-branch row is pasted
  anywhere.
- **Aggregation is the boundary, so audit the draft for per-account
  decomposition, not only for names (A17).** Flag and collapse: per-account
  rows or columns; any ordered list of six values; any k-of-6 subset
  figure; min/max/range or "the heaviest account"; per-root retention-floor
  dates; per-account `.handoff-nudge.log` block or nudge counts;
  **per-account-repo Phase C rows or directory names.**
- **Also flag and collapse, in the same audit:** any per-account repo
  count or container count; any hostname other than `github.com`; any
  origin URL or owner/name; any statement of the form "one account holds N
  repos"; and any per-container decomposition of the four non-target
  categories. A private forge hostname and a per-account repo count are each
  sufficient on their own to identify an engagement, independent of names.
  Machine-wide totals over the derived target set are publishable under
  A17's ≥2-contributor gate; per-account slices of them are not.
- Extend the pre-staging grep list to include `.ghe.`, `dev.azure`, `git@`,
  `https://` followed by any host other than `github.com` — this general
  non-`github.com`-host pattern is the mechanical backstop of record; the
  named substrings are illustrative examples, not an exhaustive list, since
  a custom private hostname matches neither — and `--hostname`.
- **Per-bucket contributor-count check (A17):** for every machine-wide
  aggregate cell, confirm at least 2 of the 6 accounts contributed to it —
  including Phase C's Tier 1/Tier 3 cells and the sensitivity arm's
  2026-08-08..2026-08-16 sub-window, neither of which inherits its parent
  era's count automatically. Generate this as a throwaway, uncommitted
  per-cell contributor-count listing (never pasted into the case study)
  and diff every drafted table cell against it before staging — a
  visual-only audit is not sufficient given this gate has no mechanical
  (grep-based) backstop, unlike the name/path redaction checks below.
- Every committed figure names its scope where it appears, and no ratio
  mixes scopes.
- Grep the drafted case study for home-rooted paths, session ids,
  `account-N`/`branch-N` labels, any per-account directory name from Phase
  C, any `--machine-label` value used during capture, and any repo or
  branch name other than `claude-config`'s own, before staging. `deny-private-project-refs.sh` is the mechanical backstop, not
  the primary control.
- `/plan-review` on this plan, then `/code-review` on each Phase 1 diff and
  again on the Phase 4 docs — the docs carry quantitative claims, which
  CLAUDE.md requires be re-derived from their producing command at the
  moment of writing, with that command named alongside each figure.

## Out of scope

- **Retuning any hook value** (`HANDOFF_NUDGE_ABS_CAP`,
  `HANDOFF_NUDGE_BLOCK_AFTER`, `HANDOFF_NUDGE_REARM_SPACING`). This plan
  measures. A retune is a separate plan that cites this measurement as its
  evidence.
- **Adding `--branches` to `reviewer-yield`.** It needs per-record branch
  attribution the module doesn't carry, and the era-level ratio answers the
  question.
- **Adding handoff→continuation session-id logging** (e.g., to
  `handoff-record-conversion.sh`/`resume-context.sh`). Per G6 this would
  only instrument future sessions and cannot retroactively cover the
  before era this study needs — a separate decision with its own tradeoffs
  (see `handoff/SKILL.md:25`'s existing reasons not to persist raw session
  ids in handoff-adjacent artifacts), not something this plan's own
  before/after design can use even if built.
- **Raising `cleanupPeriodDays`.** A real lever, already evaluated and
  declined in `token-cost-per-pr-study.md`'s own Out of scope.
- **`cost-ledger --record` backfill.** Structurally impossible.
- **`reviewer-yield`'s cited-path edit-overlap table.** G5: not
  date-windowable.
- **A controlled experiment arm** (alternating the cap week-over-week, or a
  held-out account). It would answer G4's confound properly; it is a much
  larger change to how the engineer works and needs its own decision.
- **Re-measuring the earlier 360,000-introducing transition's session-share
  figures** (the 1.25x–4x range at `docs/handoff-nudge.md:118`). Those stay
  a historical record; only the "not re-measured" clause after them is in
  scope.
- **A reusable script for the battery.** One-time analysis; a committed
  script under `~/.claude/scripts/` would be the heavier primitive.
- **Extending Phase C beyond `pr-cost --record`** (e.g., running the full
  Tier 2 quality battery inside each account's own repo). Tier 2's
  denominator is claude-config-only by design (G3, Findings mechanism); Phase
  C exists to widen Tier 1/Tier 3 reach, not to relitigate that scoping
  decision.
- **Repos with no Claude Code session activity.** Phase C's target set is
  derived from the corpus (A22); a checkout that was never worked in from
  Claude Code has no dollars to attribute and contributes nothing but `gh`
  calls. This is a scope decision, not a reachability limit.
- **Non-GitHub forges.** `pr-cost` is `gh`-only end to end — its discovery,
  enrichment, and identity pin are all `gh` calls (A24). A checkout whose
  origin is on another forge is outside this instrument's reach by
  construction; extending `pr-cost` to a second forge API is a separate
  change with its own credential and rate-limit design.
