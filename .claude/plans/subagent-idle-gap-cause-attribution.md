# Subagent idle-gap cause attribution

## Context

Attribute each subagent-origin "idle 5m-1h" cache-rebuild (the band `cache-rebuild --this-repo` already measures, added in PR #909 / `.claude/plans/subagent-idle-gap-cache-rebuild-split.md`) to a specific cause, so the choice between cutting the cause versus paying to survive it (a `cacheTtl` switch) is made on evidence instead of the `[unverified]` guess that plan's row 13 left behind.

Why now: that plan's own Out of scope section named "attributing subagent idle gaps to a cause" as unmeasured and explicitly deferred. A hand-sampled pre-check (15 subagent-origin idle-5m-1h gaps, read directly from the underlying transcript files) found the row-13 Bash-stall hypothesis explains only about half of them:

- **8/15**: a `Bash` `tool_use` fires at the gap start and its `tool_result` lands at the gap end, spanning nearly the whole window (pytest runs — including at least one full-suite run rather than `select-tests.py` — and a Bash poll loop waiting on a backgrounded process).
- **7/15**: not explained by any tool call. The gap ends instead with either (a) a `"[SYSTEM NOTIFICATION - NOT USER INPUT]"` background-task-monitor record (4 instances), or (b) a `"The coordinator sent a message while you were working"` record (3 instances) — the subagent is idle waiting on the harness's own background-task delivery or on the dispatching parent's next message, not on anything it's running itself.

Intended outcome: a `cache-rebuild` report extension that classifies each subagent-origin idle-5m-1h/idle->1h gap into one of these three causes (or unattributed), so a follow-up decision about which lever to pull — cutting Bash stalls (already mandated via `select-tests.py`), or something else for the other two — is made against a measured split instead of a sample of 15.

**Scope default (not escalated to the user):** subagent-origin gaps only, matching the origin split PR #909 already established. Main-origin idle gaps are out of scope — the Approach section of PR #909 notes `experimental.cacheTtl` cannot reach main-conversation traffic, so a main-origin attribution would have no actionable lever to point at, unlike the subagent case.

## Approach

Extend the existing `cache-rebuild` report with one new print block that sub-classifies its already-collected subagent-origin idle-gap candidates by the last marker record found inside each gap window: a `tool_result` for that same turn's own `Bash` call, a background-task notification, a coordinator message, or no marker at all. The classification is a pure function over a bounded slice of records the scan already holds in memory; the scan loop itself stays assistant-only, gaining only an index it must track alongside the `prev_ts` it already tracks.

**What each cause's lever would be** — the reason a three-way split is worth taking, and the asymmetry the measurement exists to resolve:

- **`waiting on own Bash call`**:
  - Lever: already repo-owned. Root `CLAUDE.md`'s Commands section mandates `select-tests.py` rather than the full suite for agents.
  - Observed gap: the hand-sampled pre-check (Context section) found at least one full-suite run inside a subagent — an enforcement gap in a rule that already exists, not a call for new machinery.
  - Consequence: cutting the stall removes the gap rather than paying to survive it, so on this share the lever strictly dominates a `cacheTtl` switch.
  - Separate, weaker claim: a poll-loop subset of this share is independently useful evidence. `docs/design-decisions.md:1043` names "a `Bash sleep`" as the first of §49's Revisit conditions for the `ScheduleWakeup` deny, so a measured poll-loop share is production evidence against a decision that currently rests on none.
- **`waiting on background task`**:
  - Lever: none exists today, and the repo's own recommended pattern produces this shape. `docs/design-decisions.md:1044` records `ci-watch.sh` as "deliberately built to avoid polling via `Bash run_in_background`" — event-driven delivery is the correct pattern, and its cost is precisely a subagent sitting idle until the `[SYSTEM NOTIFICATION - NOT USER INPUT]` record arrives.
  - Candidate lever: a dispatch-authoring rule ("don't background work you immediately block on"). Whether that rule is worth designing depends on this share — the measurement decides that, it does not presuppose the rule is available.
- **`waiting on coordinator message`**:
  - Lever: none identified, and none is expected to exist. The subagent's idle time between a coordinator's messages is harness scheduling; no `claude-config` file reaches it.
  - Response surface: `experimental.cacheTtl` is the entire lever for this cause — pay to survive the gap rather than cut it.
  - Decision-relevant asymmetry: if this cause dominates the subagent idle-gap dollars, the switch-delta table already in this report is the whole decision. If it is small, cutting causes 1 and 2 is the better lever and the switch stays unattractive.
- **`unattributed`** — reading rule, stated so a later reader does not misread the row: a large share here means the marker taxonomy is incomplete, not that the gaps are causeless. The follow-up is another marker sweep, not a lever choice.

**Why extend `cache-rebuild` rather than add a subcommand.** The unit of analysis is literally `idle_gap_candidates` (`transcript-analysis.py:6225-6232`) — the same corpus scan, the same `--since`/`--threshold`/`--config-dir`/redaction contract, the same per-origin chain state, the same priced excess. The report already carries three orthogonal breakdowns of this same candidate list (concurrency split, per-account, origin); a fourth is the established structure, not a new one.

Two lighter alternatives were considered and set aside:

- **A new subcommand.** It would re-implement all of the following to produce a strictly-derived breakdown of a number the existing report already prints: `_resolve_project_scope`, the partitioned read, `_dedup_turns_by_request_id`, the per-origin chain reset, tail gating, and `_cache_rebuild_excess_dollars`. It would also force a reader comparing "cut the cause" against "pay via `cacheTtl`" to run two scans and reconcile them by hand.
- **An opt-in flag gating the new block.** Set aside because the block costs nothing on the common path — it runs only over idle-gap tail candidates (51 of 33,549 calls scanned in `docs/transcript-analysis.md`'s own sample run) — and every other breakdown in this report except the multi-root per-account table prints unconditionally.

**Does this require the scan loop to process `type == "user"` records? No — and that is the load-bearing design call.** The marker records are user-type, so they must be *read*; but they do not have to be read *by the loop*. `group_records` is already fully materialized at `transcript-analysis.py:6131`, so at the moment a candidate is appended the window `group_records[prev_index + 1 : idx]` is a plain slice of a list in scope. Attribute there, via a standalone pure function. The loop's entire diff is: `enumerate(group_records)`, a `prev_index` field advanced in the same conditional that already advances `prev_ts`, and one call inside the existing `if cause in _CACHE_REBUILD_IDLE_GAP_CAUSES` branch. Three reasons this beats adding a user-record branch to the loop:

1. **Cost.** Marker matching runs once per idle-gap tail candidate, not once per user record in the corpus. An inline branch would run a prefix comparison over every user record in every scanned session to serve a few dozen candidates.
2. **State isolation.** An inline branch needs its own per-origin "markers seen since this origin's last assistant record" accumulator whose reset must stay in lockstep with `chain_state`. That is exactly the coupled-state hazard the per-origin keying comment at `transcript-analysis.py:6145-6153` exists to warn about, and it would put new mutable state inside the classification machine rather than beside it. The window slice needs no state at all.
3. **Testability.** `_attribute_idle_gap_cause(prior_turn, window, *, gap_start_ts, gap_seconds) -> tuple[str, float | None]` is a pure function, so it can get direct unit tests — hand-built `prior_turn`/`window` dicts, no `_cache_rebuild_report` fixture, no `fake_projects`, no `capsys` — independent of a full report run. `_classify_cache_rebuild_cause` sets no such precedent to mirror: a grep of the test file finds zero direct call sites for that function, which is exercised exclusively through full-fixture report runs today. This new function should not repeat that gap.

Two further alternatives were weighed and set aside. A deferred second pass per group (collect `(candidate, start, end)` during the loop, classify after it) is functionally identical but adds a per-group list and a second loop for no gain, since the slice is available at append time. Reusing `_build_tool_result_ts_map` (`transcript_analysis/reviewer_yield.py:389-416`) is the wrong granularity: it builds a whole-session `tool_use_id -> timestamp` map with `--since` filtering baked in, which would run per group regardless of whether that group has any candidates, and it discards the tool *name*, which the Bash leg needs.

**Marker taxonomy and precedence.** Four labels, parallel in construction so the table reads without a legend: `waiting on own Bash call`, `waiting on background task`, `waiting on coordinator message`, `unattributed`. Each leg is self-scoping rather than relying on a separate origin filter over window records — the Bash leg matches on a `tool_use_id` the prior turn itself emitted (ids are unique, so a main-thread `tool_result` can never match a sidechain turn's id), and the two meta legs require both `isMeta` and `isSidechain` on the marker record. Precedence is **last marker wins**, scanning the window forward: the question the report answers is what released the subagent, and the last marker before the gap-end call is by construction the one nearest that release. Last-marker-wins also needs no threshold constant, unlike a coverage-ratio precedence rule, whose share cutoff would be an ungrounded numeric literal.

**Covered-share column.** Last-marker-wins alone cannot distinguish "the marker occupied the gap" from "the marker appeared somewhere in the gap," and this plan exists to replace an unverified guess — an attribution with no quality measure would replace it with another. Each attributed candidate therefore carries `covered_share = (marker_ts - gap_start_ts) / gap_seconds`, and the table prints the per-row median. A near-100% median means the marker sits at the gap end and the attribution is tight; a low median means the marker landed early and most of the gap is still unexplained. No clamping of out-of-range shares: a median is robust to a stray clock-skew value, which is why no defensive branch is warranted. `gap_seconds >= 300` by construction for every candidate, so there is no division-by-zero case.

**Band-restricted excess column.** The attribution table pools both `idle 5m-1h` and `idle >1h` (matching the origin row it decomposes, so the rows reconcile exactly), but only the 5m-1h band is what a `cacheTtl` switch rescues — `X` in the switch-delta table already excludes `>1h` for that reason. A fifth column carrying each cause's `idle 5m-1h`-only excess connects the split to the lever without doubling the table into one per band. Five columns matches the existing switch-delta table's own width class.

**Placement.** Immediately after `## Idle-gap rebuilds by origin` (`transcript-analysis.py:6331-6334`) and before the switch-delta block — it decomposes the row above it and feeds the decision below it.

### Assumption ledger

**Root problem.** PR #909 measured *that* subagent-origin idle 5m-1h rebuilds happen and priced what a `cacheTtl` switch would do about them, but left row 13's Bash-stall hypothesis `[unverified]`. Without a cause split, the choice between cutting the cause and paying to survive it is a guess.

**Givens:**

- **G1.** The two marker literals and the `isMeta`/`isSidechain` record shape are emitted by Claude Code itself — a repo-wide grep for both strings and their near-variants returns zero hits in any `.py`/`.sh`/`.md` file here. A harness release can change them without notice. *(Another party owns the surface.)*
- **G2.** The 300s/3600s band boundaries and the 5m/1h cache tiers are vendor-set, and the Out of scope section of PR #909 already forbids touching `_classify_cache_rebuild_cause` or those constants. *(Vendor-imposed, plus a merged prior decision.)*
- **G3.** `experimental.cacheTtl` is settable only in subagent frontmatter and cannot reach main-conversation traffic. *(Harness surface; established in the Approach section of PR #909 and restated at `transcript-analysis.py:6344-6347`.)*
- **G4.** A transcript records the markers the harness delivered into a subagent, never a statement of why the subagent was idle. Every attribution here is inference from marker presence and position. *(Changing this needs upstream telemetry, outside this plan.)*

**Mechanisms:**

1. Extend `_cache_rebuild_report` with a fourth breakdown of the existing `idle_gap_candidates` list rather than adding a subcommand — the candidate set, scan, scope contract, and pricing are already exactly what the question needs. (anchors: root)
2. Classify at candidate-append time over `group_records[prev_index + 1 : idx]`, leaving the record loop assistant-only, so no new per-record state joins the `chain_state` machine and no cost is paid on non-candidate turns. (anchors: row 1)
3. Advance `prev_index` in the same conditional that advances `prev_ts` (`transcript-analysis.py:6234`), so the window's start endpoint is always the record whose timestamp is `gap_start_ts`. (anchors: row 2)
4. Self-scope each leg — `tool_use_id` membership for Bash, `isMeta` + `isSidechain` for the two meta markers — instead of filtering window records by origin, which would depend on `isSidechain` being present on ordinary `tool_result` user records, a fact not established. (anchors: row 2)
5. Last-marker-wins precedence, no coverage threshold, because the causal claim is about what ended the gap and a share cutoff would be an ungrounded literal under CLAUDE.md's Ground-every-choice rule. (anchors: G4)
6. Median covered-share column as the attribution-quality disclosure, matching the existing house pattern of printing a coverage figure rather than assuming a bound (`transcript-analysis.py:6398-6403`). (anchors: row 5)
7. Band-restricted `5m-1h $` column so the split connects to the only lever it can feed. (anchors: G3, row 1)
8. Subagent-origin candidates only; main-origin candidates carry a `None` attribution and enter no row. (anchors: G3)
9. Define both harness literals as named module constants with a comment stating this repo does not emit them — no canonical symbol exists to reference (G1), so a named constant here *is* the canonical home. (anchors: G1)
10. No drift-detection layer beyond the `unattributed` row: a changed literal moves share into `unattributed` rather than into a wrong cause, so the failure is self-disclosing. A second mechanism guarding a gap the row already discloses is the compounding-defensive-layers tell. (anchors: G1)
11. Header and caveat sentence both carry `[unverified]`, following the only existing precedent for disclosing an inference in this report's printed output (`transcript-analysis.py:6309-6312`). (anchors: G4)

**Assumptions:**

1. Both marker types carry `type == "user"`, `isMeta == True`, `isSidechain == True`, and a plain-string `message.content` beginning with a fixed literal. `[verified: live `.jsonl` transcripts, 3+ independent samples per type, from the hand-sampled pre-check described in the Context section]`
2. Those literals stay stable across harness releases. `[unverified]` — mitigated by mechanism 10, not closed.
3. A merged multi-block assistant turn retains its `tool_use` blocks: `_merge_assistant_run`'s `message.content` "is the concatenation of every record's own content blocks, in original order." `[verified: transcript_analysis/pricing.py:284-289]`
4. User records survive dedup in position — "Non-assistant records always pass through unchanged in their own position." `[verified: transcript_analysis/pricing.py:235-236]`
5. Whenever `cause in _CACHE_REBUILD_IDLE_GAP_CAUSES`, both `prev_ts` and `prev_index` are set (a `None` `prev_ts` yields `gap_seconds is None`, hence `_CAUSE_TS_ANOMALY`), so the window slice needs no `None` guard. `[verified: transcript-analysis.py:5892-5897, 6170-6171]`
6. `statistics` is already imported in this module. `[verified: transcript-analysis.py:6293]`
7. `_extract_cache_rebuild_row` returns only 1- or 2-cell rows and raises otherwise, so a 5-column row needs a purpose-built sibling extractor. `[verified: test_transcript_analysis.py:7843-7857]`
8. Existing row extractions match on line prefix and take the first match, so new row labels must not prefix-collide with `main`/`subagent`/`unexplained`/etc., no prose line in the new block may begin with a row label, and the block must sit after the origin table so existing first-match lookups still resolve to the row they resolve to today. `[verified: test_transcript_analysis.py:7850-7857; transcript-analysis.py:6331-6354]`
9. No test reads `docs/transcript-analysis.md` by path, so the docs edit selects no tests on its own and the `.py` edits carry selection. `[verified: test_select_tests.py:781-785]`
10. `select-tests.py` may under-collect when a domain directory and a file inside it are both selected (GH-882); this diff has that shape (`SCRIPTS_TESTS_DIR` plus the `test_transcript_analysis*.py` cross-domain glob at `select-tests.py:55`). `[unverified]` — Verification names the check and the fallback.
11. The Context section's hand-sampled pre-check's 8/4/3 split over 15 hand-sampled gaps is the prior this measurement tests, not an input to it; no code or doc prose may assume it holds at corpus scale. `[engineer-verified]`
12. Root `CLAUDE.md`'s Commands section mandates `select-tests.py` over the full suite for agents. `[verified: CLAUDE.md, Commands section]` The observed full-suite run inside a subagent comes from that same hand-sampled pre-check. `[engineer-verified]`
13. `docs/design-decisions.md` §49 names "a `Bash sleep`" among its Revisit conditions and records `ci-watch.sh` as deliberately avoiding polling via `Bash run_in_background`. `[verified: docs/design-decisions.md:1043-1044]`

`plan-it` Step 7 archives a plan only when its Critical files section names no file; a plan that names files is committed and stays. `.claude/plans/subagent-idle-gap-cache-rebuild-split.md` names five files, so its presence on `main` is expected, not an oversight, and there is nothing to do about it.

## Critical files

Single `code-writer` dispatch. The three files are not partitionable: the docs sample block is generated by the printed block, and the tests assert against that same output, so any split would restate the whole design in each prompt.

**Modify — `claude/.claude/scripts/transcript-analysis.py`**
- Near `:5858-5864`: add `_ATTR_OWN_BASH` / `_ATTR_BACKGROUND_TASK` / `_ATTR_COORDINATOR` / `_ATTR_UNATTRIBUTED`, the tuple `_CACHE_REBUILD_ATTRIBUTIONS` (print order), and the two harness marker-prefix constants. One-line comment on the marker constants: these strings are emitted by Claude Code, not by anything in this repository.
- New module-level `_attribute_idle_gap_cause(prior_turn, window, *, gap_start_ts, gap_seconds) -> tuple[str, float | None]`, placed beside `_classify_cache_rebuild_cause` (`:5878`). Docstring states the last-marker-wins rule and that the Bash leg is scoped by `tool_use_id` membership rather than by an origin filter.
- `:6062-6105`: seed `attribution_rebuilds`, `attribution_excess`, `attribution_band_excess` from `dict.fromkeys(_CACHE_REBUILD_ATTRIBUTIONS, ...)` and `attribution_shares: dict[str, list[float]]` — the zero-seeded-dict pattern the origin dicts at `:6081-6082` establish, for the same zero-state-row reason.
- `:6130-6131`: `group_records` is already materialized; `:6155` becomes `for idx, rec in enumerate(group_records)`.
- `:6151-6153`: add `"prev_index": None` to each origin's `chain_state` entry.
- `:6225-6232`: add `"cause": cause`, `"attribution"`, and `"covered_share"` keys; compute the latter two only when `origin == "subagent"`, else `None`.
- `:6234`: pair the `prev_index` advance with the existing `prev_ts` advance, with a one-line comment stating the invariant (`prev_index` names the record `prev_ts` came from).
- `:6256-6279`: in the existing consumption loop, accumulate the three attribution dicts and append `covered_share` when non-`None`.
- After `:6334`: the new print block. `[unverified]` in both the header and the closing caveat sentence, per the `:6309-6312` precedent; state in the header prose that the rows sum to the `subagent` row of the table above.

**Modify — `claude/.claude/scripts/tests/test_transcript_analysis.py`**
- New `_extract_cache_rebuild_attribution_row(out, label)` beside `_extract_cache_rebuild_row` (`:7843`), documented as the 5-cell sibling that helper cannot parse — the same "neither existing extractor fits" precedent `_extract_cache_rebuild_dispersion` (`:7860`) sets.
- New `TestCacheRebuildIdleGapAttribution` class modeled on `TestCacheRebuildOriginSplit` (`:8155-8228`): one fixture transcript per case, assert the row.
- Direct unit tests for `_attribute_idle_gap_cause`, calling it directly with hand-built `prior_turn`/`window` dicts (no `_cache_rebuild_report` fixture, no `fake_projects`, no `capsys`) — this is a new pattern for the file, not a mirror of an existing one (see the Approach section's Testability reason).

**Modify — `docs/transcript-analysis.md`** (`## cache-rebuild`, `:770-880`)
- Regenerate the whole sample-output block (`:782-855`) from **one** fresh `--this-repo` run rather than splicing the new block into the existing numbers — a spliced block would not reconcile against the surrounding figures, which is exactly the inconsistency this report's own coverage-disclosure style exists to prevent.
- Add one bolded-topic-sentence paragraph after `:868`'s origin-split paragraph, in the same order as the sample block, covering: the four labels, last-marker-wins, what the covered-share column does and does not establish, why the `5m-1h $` column exists, why main origin is excluded, and how to read a large `unattributed` share.
- Re-check `:857`'s "genuinely zero in this corpus" claim and `:874`'s rule-of-thumb figures against the regenerated run.

**Reuse (call, do not reimplement):** `_parse_ts` for marker timestamps; `statistics.median` (already imported, `:6293`); `_pct_of` for the covered-share format; the `dict.fromkeys(<tuple>, 0)` seed-then-iterate table pattern from `_CACHE_REBUILD_ORIGINS` (`:6081-6082`, `:6331-6334`); conftest's `_priced(..., content=[...])`, `_bash_use`, `_tool_result`, `_user_msg`, `_write_subagent_jsonl`, `fake_projects`. `_build_tool_result_ts_map` is deliberately **not** reused — see Approach. Conftest needs no change: build meta records via `_user_msg` and set `isMeta`/`isSidechain` post-hoc, matching `TestCacheRebuildOriginSplit`'s own `rec["isSidechain"] = True` idiom (`:8185-8186`); wrap that in a module-local helper in `test_transcript_analysis.py` if more than two tests need it.

## Verification

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped command. **Read the printed target list** and confirm it includes `claude/.claude/scripts/tests/`; this diff has the domain-dir-plus-contained-file shape GH-882 describes (`SCRIPTS_TESTS_DIR` alongside the `test_transcript_analysis*.py` cross-domain glob at `select-tests.py:55`). If that directory is missing from the list, re-run naming it explicitly and file the selection gap as a `select-tests.py` rule-table bug — widening by hand is the fallback, not the fix.
2. `.venv/bin/ruff check claude/.claude/`
3. New tests, each asserting the named row of the new table:
   - Bash tool_result at gap end → `waiting on own Bash call`, high covered share.
   - `[SYSTEM NOTIFICATION - NOT USER INPUT]` meta record → `waiting on background task`.
   - `The coordinator sent a message while you were working:` meta record → `waiting on coordinator message`.
   - Empty window → `unattributed`, covered share renders `n/a`.
   - Both orderings of a Bash result and a coordinator marker in one window → last marker wins, each direction.
   - A `tool_result` whose `tool_use_id` the prior turn never emitted, **in a fixture that also interleaves a second origin's own Bash tool_use/tool_result in the same `group_records` list** (mirror `TestCacheRebuildOriginSplit::test_inline_sidechain_record_in_main_file_counts_as_subagent_origin_and_own_gap_chain`'s main/inline-sidechain/main shape) → the subagent's window attributes only to its own Bash call, never the other origin's, proving self-scoping is true `tool_use_id` membership rather than "any Bash tool_result in the window."
   - A non-Bash `tool_use` (`_edit_use`) whose result lands in the window → `unattributed`.
   - Prefix text present but `isMeta` absent → not attributed.
   - `isMeta: True, isSidechain: False` (a main-thread system notification) → not attributed to a subagent's window; and the reverse, `isSidechain: True, isMeta: False` → not attributed either. Both flags are required; test each held individually false.
   - Two Bash `tool_use`/`tool_result` pairs in one window (poll-loop shape, per the Context section's hand-sample) → last-marker-wins picks the *later* tool_result's timestamp, not the first.
   - A marker timestamp outside `[gap_start_ts, gap_end_ts]` (clock skew) → the row still renders a finite covered-share/median without crashing or silently clamping, per the Approach section's no-clamping design.
   - A fixture with one attribution bucket populated (e.g. `waiting on own Bash call`) and at least one other left empty (e.g. `waiting on background task`) in the same run → the empty bucket's row renders its zero/`n/a` sentinel without `statistics.median` raising on an empty list. (Distinct from the all-zero corpus case below, which never reaches a populated branch.)
   - A main-origin idle-gap rebuild in the same fixture → enters no attribution row; the origin table is unchanged.
   - **Reconciliation:** attribution `Rebuilds` and `Excess $` sum exactly to the origin table's `subagent` row.
   - An `idle >1h` subagent rebuild → counts in `Rebuilds`/`Excess $`, contributes `0.00` to `5m-1h $`.
   - A corpus with no subagent idle-gap rebuilds → all four rows print zero-seeded.
   - Direct unit tests for `_attribute_idle_gap_cause` — calling the function directly with hand-built `prior_turn`/`window` dicts, no fixture transcript or `_cache_rebuild_report` run — covering the same precedence, self-scoping, and clock-skew cases above.
4. Live run: `--this-repo`. Confirm the rows reconcile against the printed `subagent` origin row, then regenerate `docs/transcript-analysis.md`'s sample block from that same run. Every figure in the surrounding prose is re-derived from that run at the moment it is written, per CLAUDE.md's quantitative-claims rule. The block stays aggregate-only, as it already is — no project names, no session IDs.

## Out of scope

- **Main-origin idle-gap attribution.** No lever exists to point it at (G3), which is the reason PR #909 scoped its own origin work the way it did.
- **Any change to `_classify_cache_rebuild_cause` or the 300s/3600s constants** — inherited verbatim from the Out of scope section of PR #909 (G2). The new labels are a sub-classification of two existing causes, never a new member of `_CACHE_REBUILD_CAUSES`.
- **Pulling any lever.** Setting `experimental.cacheTtl`, editing agent specs, adding a dispatch-authoring rule about backgrounded work, or changing any `run_in_background` call site. This plan produces the measurement the decision needs; the decision is its own change.
- **Filing a §49 Revisit under its first condition.** A measured poll-loop share is evidence for it, but reversing or amending a shipped `permissions.deny` is a separate decision with its own review-permissions gate (`docs/design-decisions.md:1055`).
- **Claiming why a subagent was idle.** The transcript carries a marker, not a reason (G4). The report attributes to a marker and discloses that limit in its own printed caveat; no prose in this change may upgrade that to a causal statement.
- **Attributing the non-idle causes** (`unexplained`, `model switch`, `session start`). They are not gap-driven, so they have no window to inspect.
- **Broadening the marker taxonomy speculatively.** Only the two literals actually observed in live transcripts get constants. A fourth marker type earns a row when a corpus run shows `unattributed` large enough to warrant another sweep — not before.
