# Efficiency-audit follow-ups: price-weight audit-routing, reviewer yield, denial grouping, spend trend

## Context

Goal: give a real answer to "is claude-config spend inefficient, and what's not
working" — is Opus over-invoked, are reviewer-agent dispatches worth their cost,
and is spend actually climbing — by closing the concrete follow-ups #554's
waste-screen audit already filed (F1/#555, F3/#557, F4/#558), building on the
`cost` subcommand that shipped in #552.

Why now: a fresh `cost --since 30d` run (posted to #554 this session) shows
total spend up 18.6% over the audit's original snapshot ($8,047.76 →
$9,547.35) with composition essentially unchanged — the "is it climbing"
feeling is real at the aggregate level, but nothing in the toolkit yet
attributes *why*, and `audit-routing` — the tool a reader would reach for —
still headlines an output-token-only figure representing under 12% of spend.

Intended outcome: `audit-routing`'s headline is price-weighted; a new
`reviewer-yield` subcommand reports whether reviewer-agent dispatches produce
real findings or mostly zero-finding passes, by agent type; `review-trace`
gains a lightweight denial-grouping view to make the F3 manual census
tractable; and a new `cost-trend` subcommand gives a standing week-over-week
spend view so composition or volume drift is visible before it compounds.

## Approach

**Root problem:** the efficiency toolkit's headline metric is denominated in
the wrong unit (tokens, not dollars), and its two largest *discretionary*
cost/friction centers — reviewer-agent fan-out and review-gate bookkeeping
denials — have no yield instrumentation at all, so "is this worth what it
costs" is currently unanswerable from the tool.

All four pieces live in the same file, `claude/.claude/scripts/transcript-analysis.py`
(3,832 lines, 18 subcommands today), and each reuses an existing helper rather
than introducing a new corpus-walk or pricing mechanism:

| # | Mechanism | Justification | anchors |
|---|---|---|---|
| 1 | Wire `_price_turn` (line 2386) into `cmd_audit_routing`'s existing per-turn loop | The dollar-per-token-class primitive already exists and is already used by `cost`; `audit-routing` already visits every Opus turn with `model`+`usage` in scope (lines 2199, 2212) — no new pricing logic, no new corpus walk | root |
| 2 | Extend `review-trace` with a `--deny-summary` grouping mode over its existing denial-detection path (`hook_denial_key`, line 791) | `review-trace` already classifies every denial and already resolves corpus scope; grouping is a new presentation over data already collected, not new detection | row1 |
| 3 | New `reviewer-yield` subcommand joining a main-thread Agent/Task dispatch to its subagent's own final-message verdict, via the `toolUseId` field in `subagents/agent-<id>.meta.json` (verified on-disk this session) | No dispatch→outcome join exists today (confirmed: `subagent-mix` only counts dispatch events, `_read_session_file` already merges sidechain records but nothing correlates a specific dispatch to its specific sidechain file's content) — the `meta.json` sidecar is the grounded join key | row1 |
| 4 | New `cost-trend` subcommand reusing `_resolve_project_scope(..., include_subagents=True)` (same corpus walk as `cost`), `_price_turn` (same pricing), and `cmd_handoff_ratio`'s ISO-week bucketing (line ~2571) | All three ingredients already exist and are each already used by a different subcommand; a new subcommand composes them rather than duplicating any | row1 |

**Alternatives considered and set aside:**
- A standalone dollar-weighted subcommand instead of modifying `audit-routing` in place — rejected: would duplicate `audit-routing`'s existing per-turn Opus-class classification (`_classify_opus_turn`, the judgment-span state machine), which is the expensive/subtle part of that function. Modifying in place is the single-source-of-truth choice.
- Correlating F4's yield to the orchestrator's own `ReportFindings` tool call, as originally proposed — rejected after measurement: corpus-wide, `ReportFindings` is called by the orchestrating session (14 hits / 300 main-thread files sampled) essentially never by the subagent itself (2 hits / 600 subagent files sampled, ~0.1%). It is not a per-dispatch signal.
- A stricter, literal regex on each reviewer agent's documented `**No X concerns**` / `Wrote findings to <path>. Found <N> issues.` contract — measured at only 51% coverage on real corpus data (654 reviewer-type dispatches) due to markdown-formatting and phrasing variance, not a data gap. A loosened, format-tolerant version of the same regex (case-insensitive, optional bold, singular/plural, non-adjacent sentences) was measured at **77.4% coverage** (506/654) on the same sample and is what this plan implements, with the uncovered remainder reported as an explicit `unclassified` bucket rather than silently dropped — the same "best-effort, not exact" posture `_HOOK_DENIAL_SIGNATURE` (line 770) already documents for hook-denial detection.
- Full dispatch→finding→subsequent-diff-change correlation (F4's issue as originally suggested) — descoped for this plan; that requires temporally correlating a subagent's sidechain records to *main-thread* `Edit`/`Write` events after the dispatch returns, a materially bigger join than dispatch→verdict. Left as a candidate follow-up, not started here.
- Automatic "avoidable vs correctly-caught" classification for F3's denial census — rejected: that's a safety judgment call the issue itself frames as requiring a human walk-through ("someone classify each denial"); code can group and count, not judge. `--deny-summary` produces the grouped counts the census needs, nothing more.
- A day-bucketed (vs. week-bucketed) trend — rejected: only ~22 days of local transcript history exist today (oldest transcript file: 2026-07-13), so day buckets would be mostly noise; week buckets give ~3 usable data points now and stay meaningful as history accumulates. `cmd_handoff_ratio` already establishes the week-bucketing convention (`f"{iso.year}-W{iso.week:02d}"`) — reused verbatim rather than inventing a second date-bucketing idiom in the same file.

**Assumption ledger:**
- `_price_turn` and `_model_rates`'s pricing table (`_MODEL_BASE_INPUT_RATES`, line 2332) is current and carries its own staleness mechanism (`_MODEL_RATE_EXPIRES`, line 2343) — `[verified: transcript-analysis.py:2316-2343, fetched 2026-08-02]`. No new pricing logic is introduced by this plan; `audit-routing` and `cost-trend` both inherit the existing staleness banner behavior for free.
- `subagents/agent-<id>.meta.json`'s `toolUseId` field matches the dispatching `Agent`/`Task` tool_use block's `id` — `[verified: read a live meta.json on this machine this session, confirmed key `toolUseId` present and formatted as `toolu_...`, matching the Anthropic API's tool_use id shape]`.
- Reviewer-dispatch verdict-signal coverage is 77.4% (506/654) with the loosened regex, measured against this machine's live corpus — `[verified: ad hoc measurement this session, see Approach alternatives above; not yet codified as a test fixture — Verification section adds one]`.
- Only ~22 days of local transcript history exist, so `cost-trend`'s first real run will show ~3 partial weeks, not a long trend — `[verified: `find` over `~/.claude/projects` this session, oldest main-session file dated 2026-07-13]`. This is a data-retention fact the plan does not attempt to change.
- No sibling price file exists outside `transcript-analysis.py`; all four pieces stay single-file — `[verified: general-purpose exploration agent this session, confirmed via grep]`.

## Critical files

- **`claude/.claude/scripts/transcript-analysis.py`**
  - `cmd_audit_routing` (line 2129): add a parallel `"dollars"` accumulator to `corpus_totals`/`session_class_tokens` alongside the existing `"out"`/`"cr"` keys, populated via `_price_turn(model, usage)` at the same point `out_tokens`/`cr_tokens` are read (line 2223-2224). Replace or supplement the token-only "Sonnet-tier estimate" print (lines 2308-2311) with a dollar figure and percentage-of-priced-spend; keep the existing token line as a secondary diagnostic (do not delete — F1/#555 says "point at cost's output, or retire the headline in favor of a cost-based one," and keeping both preserves backward-compatible diagnostic value at negligible cost). This is genuinely new wiring, not reuse-as-is: `cmd_audit_routing` has no unpriced-turn accounting today, unlike `_cost_report` (line 2473) — add an `unpriced_turns`/`unpriced_tokens` counter mirroring `_cost_report`'s convention, incremented whenever `_price_turn` returns `None` for `dollars_by_class`, and print it alongside the dollar headline so a corpus with unpriced (e.g. stale/unknown) model turns doesn't silently under-report.
  - `cmd_review_trace` (line 822) + `p_review_trace` parser (line 3634): add a `--deny-summary` flag. When set, group already-detected denial events (via `hook_denial_key`, line 791) two ways: (a) by originating hook/gate — extracted from the denial message text using the codebase's own existing hook/gate names as literal category labels (no new terminology invented, per CLAUDE.md's "no PR-defined terminology" rule); (b) by the paired tool_use's attempted command shape (`git commit` / `git checkout` / `git push` / other), joined back via the same `tool_use_id` the denial event already carries. A denial matched by `_HOOK_DENIAL_SIGNATURE` (documented best-effort, line 764-772) but naming no known hook goes into an explicit `unmatched` bucket in the by-hook grouping — never silently dropped, since a silent drop would make `--deny-summary`'s total undercount `--deny-only`'s raw count with no visible signal. Output is a grouped count table, not a classification — the human judgment call from #557 stays manual.
  - New `cmd_reviewer_yield` + `p_reviewer_yield = sub.add_parser("reviewer-yield", ...)` (near `cmd_subagent_mix`/`p_mix`, line ~1613/3552, since it's the closest sibling in shape): corpus-wide walk via `_resolve_project_scope(args, "reviewer-yield", include_subagents=True)`; for each main-thread `Agent`/`Task` tool_use with `subagent_type` in the reviewer set (`_REVIEWER_PREFIX`/`_REVIEWER_EXACT`, lines 757-758, plus `skill-fidelity-reviewer` per #558's own table), resolve its dispatch's `subagents/agent-<id>.meta.json` by matching `toolUseId`, read the paired `.jsonl`'s last assistant text block, and classify via the loosened regex (found-N-issues / no-concerns-verdict / unclassified). The join trusts the **dispatch's own** `subagent_type` for classification, not any type field inside `meta.json` — `meta.json` is used only to resolve `toolUseId` → its paired `.jsonl` path. A `.jsonl` with no assistant text blocks at all (truncated/empty subagent transcript — distinct from a missing `meta.json`, which excludes the dispatch entirely) classifies as `unclassified`, same as any other non-matching verdict text. Aggregate per agent type: dispatch count, findings-found count, zero-finding-verdict count, unclassified count, total findings. **Redaction:** add a `--redact` flag wired the same way as `cmd_audit_routing`/`cmd_cost` (`_build_redact_map()`, `_redact_proj_label`/`_redact_session_id` on any project-label or session-id field the output surfaces). The aggregate-only output schema above names no per-session or per-project field and must stay that way — do not add a debug/example-session pointer or raw verdict-text excerpt to the `unclassified` bucket's output; raw subagent verdict text can carry real file paths, table names, or business logic far more identifying than a bare project-label token, and the schema's aggregate-only shape is what keeps this subcommand out of that risk class by design.
  - New `cmd_cost_trend` + `p_cost_trend = sub.add_parser("cost-trend", ...)` (near `cmd_cost`/`p_cost`, line ~2425/3713): corpus-wide walk via `_resolve_project_scope(..., include_subagents=True)` (same as `cost`), price each turn via `_price_turn`, bucket by ISO week using `cmd_handoff_ratio`'s pattern (line ~2571), and print total $ per week plus context-share % and Opus-share % per week (mirroring the metrics #554 already tracks, so this becomes the standing instrument #555 asked for). The most recent bucket is very likely a partial week given real-world invocation timing — label it explicitly (e.g. a `(partial)` suffix on the current ISO week's row) rather than presenting it as a complete week's total, since the plan's own rationale for week- over day-bucketing (see Approach) is that the corpus is barely 3 weeks deep and a silently-partial trailing bucket would misread as a real week-over-week drop. This subcommand's output schema (week / $ / context-share % / Opus-share %) names no per-session or per-project field, so no `--redact` flag is needed — same aggregate-only reasoning as `handoff-ratio`, which has none today.

- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** (5,736 lines)
  - `_opus()` (line 1759-1773) hardcodes an unpriced model (`claude-opus-4-7`, not in `_MODEL_BASE_INPUT_RATES`) — every existing `TestAuditRouting` test built on it exercises only the new unpriced branch. Add a `model=` parameter (default unchanged, so existing tests are untouched) or a sibling `_priced_opus()` helper defaulting to a priced ID (`claude-opus-5`), so the new dollar-headline logic isn't exercised by only one test.
  - `TestAuditRouting` (line 1871): add a case analogous to `test_sonnet_tier_estimate_printed` (line 1997) asserting the new dollar figure via hand-computed pricing, following `test_price_turn_hand_computed_dollar_total`'s (in `TestCost`, line 2092) verification style — using the priced fixture above. Add a second case mixing a priced and an unpriced Opus turn in the same corpus, asserting the dollar accumulator and the `unpriced_turns` counter each account for their own turn without double-counting or dropping the other. Name a structured extractor for the new `$`-formatted headline line (analogous to `_extract_corpus_class_tokens`, line 1856) rather than ad hoc per-test regex, per this file's existing parse-don't-regex convention.
  - `TestAuditRoutingShape` (line 2701) / `TestAuditRoutingSamples` (line 3037): re-check `test_cross_validation_with_audit_routing` in each for coupling to the current unweighted headline text — update if the assertion parses that exact line.
  - `TestReviewTrace` (line 1322): add `--deny-summary` grouping tests, including a case mixing multiple hook names, multiple git-command shapes, and a denial matched by `_HOOK_DENIAL_SIGNATURE` but naming no known hook (must land in the `unmatched` bucket, not silently drop).
  - New test fixture helper (no existing equivalent — `_write_subagent_jsonl`, line 25, writes only the `.jsonl`, never a paired `.meta.json`): write both files for a synthetic dispatch, with `toolUseId` set to match the dispatching tool_use's `id`.
  - New `TestReviewerYield`, using the helper above: one case per loosened-regex axis, not folded into a single "found-N-issues" case — bold vs. unbold verdict phrasing, singular "issue" vs. plural "issues", and case-insensitivity — since each axis is a specific relaxation from the 51%-coverage strict version (see Approach) and a future edit could silently re-tighten one without any of the three broad-shape fixtures catching it. Plus: a non-reviewer `subagent_type` (must be excluded from aggregation entirely), a dispatch with no matching `meta.json` (must not crash, classifies as excluded/unclassified per the design above), a `.jsonl` with zero assistant text blocks (distinct failure mode from the missing-`meta.json` case — must not crash, classifies as `unclassified`), and a default (`--no-redact`) run asserting no project-label or session-id field appears in output — proving the schema's aggregate-only claim rather than leaving it unverified.
  - New `TestCostTrend`: multi-week fixture data verifying week-bucket boundaries and reuse of `_price_turn`'s per-model pricing, plus an explicit case for a partial trailing week (fixture data ending mid-ISO-week) asserting the `(partial)` label appears on that row and not on complete weeks.

- **`docs/transcript-analysis.md`**: update the `audit-routing` section (around line 372-406) to document the dollar-weighted headline; add sections for `reviewer-yield` and `cost-trend` following the file's existing per-subcommand doc pattern (Purpose / example output / "When to reach for it").
- **`claude/.claude/skills/transcript-analysis/SKILL.md`**: add two rows to the existing `| Question | Subcommand |` routing table (verified shape, line 12-25), immediately after the `cost` row (line 22) to keep the cost-family questions grouped:
  `| Are reviewer dispatches producing real findings or mostly zero-finding passes? | \`reviewer-yield --since 30d --redact\` |`
  `| Is spend climbing week over week? | \`cost-trend\` |`

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py` — full suite, run from this worktree.
- `../../../.venv/bin/ruff check claude/.claude/scripts/transcript-analysis.py`.
- End-to-end against the real local corpus (read-only, no `--no-redact` output leaves this session):
  - `python3 claude/.claude/scripts/transcript-analysis.py audit-routing --since 30d --redact` — eyeball that the new dollar headline is present and the token line is unchanged from today's output.
  - `python3 claude/.claude/scripts/transcript-analysis.py review-trace --deny-only --deny-summary --since <30d-ago>` — eyeball grouped counts against the shape of #557's manually-produced table.
  - `python3 claude/.claude/scripts/transcript-analysis.py reviewer-yield --since 30d --redact` — eyeball per-agent-type findings/zero-finding/unclassified counts; confirm the unclassified share is in the ~20-25% range measured this session, not dramatically higher (a regression signal if it is).
  - `python3 claude/.claude/scripts/transcript-analysis.py cost-trend` — eyeball week-bucket boundaries against the known corpus date range (2026-07-13 to present), and confirm the current week is labeled `(partial)`. No `--redact` flag: this subcommand's output is aggregate-only by design (see Critical files).

## Out of scope

- Full dispatch→finding→subsequent-diff-change correlation (F4's issue as originally written) — the verdict-only join implemented here answers "did this dispatch produce findings," not "did those findings result in a code change." Candidate follow-up, not started.
- Automatic denial classification ("avoidable" vs "correctly caught") for F3/#557 — `--deny-summary` produces grouped counts only; the human judgment walk stays manual, per the issue's own framing.
- Improving verdict-signal coverage above 77.4% (e.g., investigating each remaining `unclassified` case individually) — shipped as a documented best-effort signal, consistent with `_HOOK_DENIAL_SIGNATURE`'s existing precedent in this file.
- Day-level (rather than week-level) spend-trend granularity — revisit once more than a few months of local transcript history exists.
