## Context

Cache-read tokens, overwhelmingly on Sonnet, dominate this harness's usage cost, and the existing cost tooling (`transcript-analysis.py`, `token-analyzer.py`, `transcript_analysis/cost.py`) does not yet answer where that spend concentrates: how much is the main session versus subagents, which subagent *types* (code-writer, staff-\*-engineer, Explore, general-purpose, etc.) account for it, and how spend and round-count break down per PR across the `plan-review` / `code-review` / `ready-for-review` review-loop iterations. The goal is a re-runnable, committed CLI extension to the existing tooling — not an ad hoc script — that reports both breakdowns, plus a verified answer to whether a well-respected OSS project already does this and should be adopted instead of extending the hand-rolled tool further.

Discovery established that the first breakdown already exists: the `cost` subcommand (`transcript_analysis/cost.py`) already splits main-session vs. subagent dollars per thread, and `subagent-mix` (`transcript-analysis.py`) already groups subagent dollars by `subagent_type`. The remaining gap — round-level cost attribution per review skill per PR, plus total round count — is what this plan builds. OSS research (`ccusage`, `cc-analyzer`, `claude-session-analyzer`, `ccost`, `token-dashboard`, `claude-usage`, plus Anthropic's own OTel export) found no candidate that solves round/PR attribution, since it requires knowing which skill invocations belong to the same PR and which invocation-index they are — this repo's own orchestration semantics. No OSS adoption; extend the existing `transcript_analysis` package instead.

Two decisions were made explicit by the engineer before design: (1) **invocation-count rounds** — every dispatch of a review skill is its own round, with no dedup by diff-state, because token cost is incurred regardless of whether the round produced findings; (2) `ready-for-review` counts toward a PR's total-rounds figure uniformly with `plan-review`/`code-review`, with a per-skill-type sub-breakdown also available.

## Approach

Add one new read-only subcommand, `review-round-cost`, implemented in a new package module `transcript_analysis/review_rounds.py`. It walks each session's main transcript, opens a **round** at every `plan-review` / `code-review` / `ready-for-review` invocation (both the `Skill` tool_use path and the `/slash` `<command-name>` path), closes it at the next round-open or next fresh user prompt, and prices every main-thread turn plus every subagent transcript dispatched inside that window. Rows are keyed by attributed git branch — this repo's one-branch-per-PR unit, the same key `pr-cost` and `workstream-cost` already compute on — with per-branch totals, per-skill sub-breakdowns, and a round-vs-non-round reconciliation line that makes any attribution gap visible instead of silent. No `gh` call, no network, no new dependency.

Three shaping decisions, with the alternatives set aside:

- **New subcommand, not an extension of `review-trace` or `pr-cost`.** `review-trace` is per-session-timeline and prices nothing; bolting a per-branch cost aggregation onto it would give one command two output shapes. `pr-cost`'s ledger is strictly one row per PR with a column-count-strict parser (`docs/pr-cost.md`, "adding a column later is a migration") — per-(PR, skill, round) rows are a different cardinality that cannot fit that schema without a migration.
- **No embedded `gh` branch→PR join.** Two lighter primitives already deliver PR numbers: `pr-link` (`--branches B1,B2,...`, existing subcommand, "Map branches to GitHub PRs") composes directly with this command's `--this-repo` raw branch output; and `pr-cost`'s read-mode listing already prints its own captured branch↔PR pairs. Embedding a join would mean replicating auth preflight, repo-identity pinning and its mismatch refusal, rate-limit backoff, and the multi-root write refusal — a large privileged surface for one display column — and every one of those helpers lives in the *shim*, which the package may not import back (`docs/transcript-analysis-architecture.md`: "the shim imports it, never the reverse").
- **Span boundary comes from `cmd_judgment_pair`, not `workstream-cost`.** `_compute_workstream_dollars`/`_first_main_thread_turns_dollars_by_branch` (`cost.py:176-281`) is a *positional prefix window* — each non-first session's first 5 main-thread turns — not a skill-scoped span, so it is not reusable here. `cmd_judgment_pair` (`transcript-analysis.py:2166-2193`) already implements exactly the needed rule: scan forward from the invocation to the minimum of the next fresh user prompt (`_is_fresh_user_prompt`, `:199`) and the next matching invocation. That rule is re-expressed in `review_rounds.py` over both invocation paths.

### Assumption ledger

**root:** No tool answers "what did each review loop on this branch cost, and how many invocations did it take" — `reviewer-yield` has no dollar column and no per-PR/per-round axis, `review-trace` numbers and prices nothing, and `pr-cost` collapses a whole branch to one figure.

**Givens** (fixed beyond this design's reach):

- **G1 — `_lib.sh`'s reviewer-round latch is untouchable.** It is a live enforcement gate (`log-reviewer-round.sh` writes, `require-architect-consult.sh` reads); changing its key, cap, or semantics changes escalation behavior for every stow consumer. Owned by the hook layer, not by analytics.
- **G2 — transcript retention.** A branch's rounds are unrecoverable once its transcripts age out (`cleanupPeriodDays`, default 30d). Vendor-imposed; nothing here can widen it.
- **G3 — meta.json is harness-written external input.** `toolUseId`/`model` field shapes are Claude Code's contract, not this repo's, so a dispatch with no readable sidecar is excluded and counted, never guessed at.
- **G4 — the shim→package import direction is one-directional.** `docs/transcript-analysis-architecture.md:14-18`; every gh-join helper sits on the shim side of that line. Dissolving it is the `cli.py`/`pr_join.py` migration phase, a decision outside this plan.

**Rows:**

1. Every invocation of the three skills is its own round; no diff-state dedup, and `ready-for-review` counts uniformly toward the per-branch total while still carrying its own sub-breakdown. `[engineer-verified]` — anchors: root
2. `REVIEW_SKILLS` (`transcript-analysis.py:988`) is exactly `("code-review", "plan-review", "ready-for-review")` — the round-skill set already exists as a named constant and needs no new literal. `[verified: transcript-analysis.py:988]` — anchors: row1
3. A review skill is invoked by two disjoint transcript shapes: a `Skill` tool_use with `input.skill`, and a user record carrying `<command-name>/name</command-name>`, "the /slash invocation path, which injects the skill body directly **without** a Skill tool_use." Detecting only the first — which is all `review-trace` does — would miss every `/code-review` the operator typed, the path `CLAUDE.md` itself prescribes. `[verified: transcript-analysis.py:2280-2289, 2368-2372; fixtures at tests/test_transcript_analysis.py:12585-12599]` — anchors: row1
4. Because the two paths are disjoint by construction (row 3), no cross-path dedup is built; a single fixture asserting a slash-only round counts exactly once is the guard instead of defensive code. `[verified: transcript-analysis.py:2287-2289]` — anchors: row3
5. `review-trace`'s own membership test uses the **raw** `input.skill` string (`transcript-analysis.py:1664-1665`), so a directory-qualified spelling (`.claude/worktrees/b/claude:code-review`) silently fails to match. Round detection therefore applies `_normalize_skill_name` and then matches the segment after the last `:`. `[verified: transcript-analysis.py:1664-1665, 2253-2277]` — anchors: row2
6. Widening the match past the `dir:`/`plugin:` qualifier is safe here and not in `_normalize_skill_name`: this is a closed three-name membership test, not a display label, and no registered plugin (`skill-management`, `claude-hook-review`, `plugin-semver`) exposes a skill with one of those three names. `[verified: CLAUDE.md § "Marketplace plugin skills use `plugin:skill` names"; transcript-analysis.py:2272-2275]` — anchors: row5
7. Rounds are detected on the **main thread only**. A review skill run inside a dispatched subagent is already priced as that dispatch's cost; counting it as its own round would double-count its dollars. The cost of a review delegated to a subagent *outside* any round window lands in the branch's non-round remainder, visible on the reconciliation line rather than silently dropped. `[verified: transcript-analysis.py:2294-2302 — the two-consumer split that made subagent skill invocations opt-in]` — anchors: row1
8. A round's cost window is `records[open_idx : window_end)` — inclusive of the opening record, so the assistant turn that fired the `Skill` block is charged to the round. A `/slash` opening record is a user record with no `usage`, so inclusion is a no-op there. `[verified: transcript-analysis.py:2166-2197; pricing skips records without `message.usage`]` — anchors: row3
9. Subagent attribution joins on `toolUseId`, not on a timestamp window: for each `Agent`/`Task` tool_use inside the round window, look the id up in the session's meta.json index and price that transcript, then recurse into that transcript's own `Agent`/`Task` ids with a visited set. Timestamp-window attribution was rejected because a long-running dispatch opened before the round would be misattributed; recursion is required because `review-trace` documents reviewer spawns *from inside* another subagent. `[verified: reviewer_yield.py:53-98; transcript-analysis.py:1957-1963; docs/transcript-analysis.md:426,431]` — anchors: row8
10. Dispatches inside a round window with no readable meta.json/jsonl pair are counted and printed as a `dangling` figure, mirroring `subagent-mix`'s own Dangling column, rather than dropped or estimated. `[verified: reviewer_yield.py:76-98 meta_read_errors; transcript-analysis.py:2474-2479]` — anchors: G3
11. Double-counting is structurally impossible because scope is resolved with `include_subagents=False` and each subagent `.jsonl` is read exactly once, by path, from the meta index. `pricing.dedup_turns_by_request_id` runs per file before pricing, matching `_compute_pr_cost_branch_totals`'s dedup-then-price sequence so these dollars are derived the same way `cost`'s and `pr-cost`'s are. `[verified: cost.py:133-173; pricing.py docstring ordering constraint]` — anchors: row9
12. A round's branch is the opening record's own `gitBranch`, carried forward from the last non-empty main-thread value when absent. `cost._attributed_branch` is deliberately **not** used: it exists to resolve `worktree-agent-*` sidechain records, and a main-thread round-opening record is never one. This keeps `review_rounds.py` free of any `cost.py` import, so no command-group→command-group coupling is introduced. `[verified: cost.py:65-92; transcript-analysis.py:1588-1594 carry-forward convention]` — anchors: G4
13. Round ordering is by opening timestamp across every session on the branch, not iteration order — `iter_sessions` yields file-path sort order, the exact trap `_compute_workstream_dollars` re-sorts around. Sort key `(ts or +inf, session path, record index)` keeps an unparseable-timestamp round in the report, ordered last, deterministically. `[verified: corpus.py:93-105; cost.py:228-232]` — anchors: row1
14. `_index_subagent_dispatches` moves from `reviewer_yield.py` to `corpus.py`, name unchanged. It is pure split-transcript-layout logic — `corpus.py` already owns `SUBAGENT_SUBDIR` — and already has two consumers reaching across a module boundary for it, which the architecture doc records as an exception. The move closes that exception rather than adding a third reach. Cross-module reads of a `corpus` private are the established convention (`cost.py` reads `corpus._parse_ts`). `[verified: docs/transcript-analysis-architecture.md:86-91; corpus.py:12; cost.py:57; call sites at reviewer_yield.py:559 and transcript-analysis.py:2621 only — no test references it]` — anchors: row9
15. `REVIEW_SKILLS` moves from the shim into `review_rounds.py` as a public name the shim back-imports for `cmd_judgment_pair`'s default, adding a second documented entry to the architecture doc's one-directional exception list. Duplicating the tuple instead would violate single-source-of-truth. `[verified: transcript-analysis.py:988, 2118, 2673; docs/transcript-analysis-architecture.md:16-18 exception pattern]` — anchors: row2
16. Output redaction follows `subagent-mix`'s documented contract exactly: `_root_scoped_display_label(disclose=...)` prints a raw branch under `--this-repo` and an opaque `account-<K>/branch-<N>` otherwise, with the `DO NOT PUBLISH` banner above one root. This is what makes the report usable without inventing a `--no-redact` escape hatch. `[verified: redaction.py:233-246; docs/transcript-analysis.md:204-207; scope.py:471-473]` — anchors: row12
17. Unpriced turns are carried per round as counts, never as `$0` — an unrecognized model is excluded from pricing, not priced at zero, and a nonzero figure means the round's dollars understate its true cost. `[verified: cost.py:161-164; docs/pr-cost.md schema row for `unpriced_turns`]` — anchors: row11
18. `docs/transcript-analysis.md` content is not pinned by any test (`test_select_tests.py:781-785` asserts no test reads it by path), so its update is reviewer-enforced. `docs/transcript-analysis-architecture.md` **is** pinned — a new package module without a `### review_rounds.py` heading fails CI. `[verified: tests/test_transcript_analysis_architecture_doc.py:35-49; tests/test_select_tests.py:781-785]` — anchors: root

**Mechanism justifications:**

- New subcommand in a new package module — anchors: root. The two lighter in-place options both fail on a named limitation: `review-trace` prices nothing and is per-session, `pr-cost`'s ledger parser is column-count-strict at one row per PR.
- `cmd_judgment_pair`'s window rule rather than a new segmentation — anchors: row8. Reuses an existing, tested boundary definition; `workstream-cost`'s positional prefix window cannot express "one invocation of skill X."
- `toolUseId` join with recursive descent rather than timestamp-window attribution — anchors: row9. Exactness matters for a cost figure, and the meta.json sidecar is the only per-dispatch boundary the flattened `include_subagents` merge destroys.
- Composition with `pr-link` rather than an embedded `gh` join — anchors: G4. Two lighter primitives (`pr-link --branches`, `pr-cost` read mode) already deliver PR numbers with zero new privileged surface.
- Per-branch round/non-round reconciliation line rather than a cross-command check against `pr-cost` — anchors: row11. Computed from the same single pass, so it is internally consistent by construction instead of comparing two independently-scoped runs.

### Output shape

```
REVIEW ROUND COST SOURCES (this repo; 1 root)

account-1/some-feature-branch
  rounds=6  (code-review=3  plan-review=1  ready-for-review=2)
  round $ 12.40 of 31.75 branch $ (39.1%)
   #  skill              n  date        main $   agent $  agents   total $
   1  plan-review        1  2026-08-02    0.41      1.88       4      2.29
   2  code-review        1  2026-08-03    0.55      2.10       5      2.65
   ...

Totals: 4 branches, 19 rounds (code-review=11  plan-review=4  ready-for-review=4)
Mean rounds per branch: 4.75
Mean $ per round -- code-review 2.41  plan-review 1.90  ready-for-review 1.12
Non-round dollars: 61.2% of branch dollars fell outside every round window
Dangling dispatches inside round windows: 2 (no readable meta.json/jsonl pair)
Unpriced turns inside round windows: 0
```

`#` is the branch-wide round ordinal (Step 4 answer 2's grand total); `n` is that skill's own ordinal (the sub-breakdown).

### Flags

`--projects GLOB` / `--this-repo` (via `_add_project_scope_args`), `--branches B1,B2,...`, `--since` / `--until` (filtering on the round's opening timestamp, matching `judgment-pair`'s convention), `--skill {code-review,plan-review,ready-for-review}`. Roots resolve via `scope.resolve_scan_roots` — no subcommand-level `--config-dir`, matching `workstream-cost` and `reviewer-yield`.

## Critical files

One `code-writer` dispatch, not two. The two phases below both edit `transcript-analysis.py` and `docs/transcript-analysis-architecture.md`, so splitting them would put overlapping edits in one shared worktree and force the same package-boundary background into both prompts.

**Phase 1 — mechanical move (do first; Phase 2 imports its result):**

- `claude/.claude/scripts/transcript_analysis/corpus.py` — receives `_index_subagent_dispatches` verbatim from `reviewer_yield.py:53-98`, docstring intact. Sits alongside `SUBAGENT_SUBDIR` (`:12`), whose convention it reads.
- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` — delete the moved function; call site at `:559` becomes `corpus._index_subagent_dispatches(jsonl)` (module-attribute access, per the file's own top-of-file discipline).
- `claude/.claude/scripts/transcript-analysis.py` — move `_index_subagent_dispatches` out of the `from transcript_analysis.reviewer_yield import (...)` block (`:116-139`) into the `corpus` import; update the explanatory comment at `:119` and the call-site map line at `:124`. Call site at `:2621` (`cmd_subagent_mix`) is unchanged in text.
- `docs/transcript-analysis-architecture.md` — add the function to the `### corpus.py` section (`:22-27`); remove it from `### reviewer_yield.py`'s cross-boundary sentence (`:89-91`) and from the two-module exception note at `:16-18`.

**Phase 2 — the feature:**

- `claude/.claude/scripts/transcript_analysis/review_rounds.py` **(new)** — `REVIEW_SKILLS`, the round-open predicate for both invocation paths, `compute_review_round_costs(session_iter)`, and `cmd_review_round_cost`. Imports `corpus`, `pricing`, `redaction`, `render`, `scope` by module only — the same import set `reviewer_yield.py` uses, plus `redaction` for `_root_scoped_display_label`. **No `cost` import** (ledger row 12).
  - *Reuse, do not reimplement:* `corpus._index_subagent_dispatches` (post-move), `corpus._parse_jsonl_records`, `corpus.SUBAGENT_SUBDIR`, `corpus._parse_ts`; `pricing.dedup_turns_by_request_id`, `pricing._price_turn`, `pricing._token_counts` (`pricing.py:554`), `pricing._TOKEN_CLASSES`; `scope.resolve_scan_roots`, `scope._resolve_project_scope`, `scope.print_resolved_scope`, `scope._parse_absolute_window_args`, `scope._redaction_ordinals`, `scope._DO_NOT_PUBLISH_BANNER` (printed at the same stdout+stderr call sites `cost.py:493-495`/`transcript-analysis.py:2545-2546` use whenever output is undisclosed/multi-root — ciso-reviewer round-1 finding: this symbol was missing from the reuse enumeration despite ledger row 16's commitment to the full `subagent-mix` contract, and a code-writer following this list mechanically would ship the redacted labels without the banner); `redaction._root_scoped_display_label`; `render._fmt_usd`, `render._pct_of`, `render._content_text`, `render._sanitize_table_cell`.
  - *Re-express, not import (shim-resident, package may not import back):* `_is_fresh_user_prompt` (`transcript-analysis.py:199-223`) and `_normalize_skill_name` (`:2253-2277`). Both are short and pure; each new copy carries a one-line comment naming the shim function it mirrors, following `cost.py:40-49`'s own precedent for restating a shim convention in the package.
  - *Invariant to carry forward explicitly (ciso-reviewer round-1 finding):* `cmd_skill_invocation` (`transcript-analysis.py:2311-2323`) carries an "OUTPUT INVARIANT" comment that only `input["skill"]` is ever extracted from a `Skill` tool_use block, never `input["args"]` (which can carry absolute local paths). `review_rounds.py`'s round-open detection matches on the same field; restate that invariant as its own comment rather than leaving it implicit, and cover it with the test named in Verification below.
- `claude/.claude/scripts/tests/test_transcript_review_rounds.py` **(new)** — fixture-driven, modeled on `test_transcript_workstream_cost.py`'s structure (module load via `spec_from_file_location`, `fake_projects`, `_write_jsonl`, `_priced`) and on `test_pr_cost_section.py` for the CLI-surface assertions. Uses `conftest._write_subagent_dispatch` (`conftest.py:154-171`) — it writes both the `.jsonl` and the `meta.json` sidecar the dispatch join needs, unlike `_write_subagent_jsonl`.
- `claude/.claude/scripts/transcript-analysis.py` — delete `REVIEW_SKILLS` (`:988`); back-import it from `transcript_analysis.review_rounds` alongside `cmd_review_round_cost`; add the `p_review_round_cost = sub.add_parser(...)` block with `_add_project_scope_args`, `--branches`, `--since`/`--until`, `--skill`, and `set_defaults(func=cmd_review_round_cost)`, placed adjacent to `p_workstream_cost` (`:11603-11619`).
- `docs/transcript-analysis.md` — new `## review-round-cost` section following the file's template (**Purpose** / **Flags** / **Sample output** / **When to reach for it**), placed after `## workstream-cost` (`:928-944`). Add the subcommand to the redaction enumeration paragraph at `:47`, stating that it follows `subagent-mix`'s contract. Document these caveats explicitly: main-thread-only round detection and what that misses (row 7); the window closing early on a mid-review user interjection; a round truncated by session end; the non-round remainder line's meaning; the `pr-link --branches` composition for PR numbers; and — ciso-reviewer round-1 finding — that a per-branch, per-round, dated dollar figure is a stronger de-anonymization/correlation key than any prior redacted subcommand's aggregate-only output (`subagent-mix`'s `CR`/`PR`/`RR` columns disclose per-skill *counts* per branch today, but never a dated $ series), extending the same acknowledgment `redaction.py:216-224`'s docstring already makes for exact-cent dollar columns to this new, more-correlatable case.
- `docs/transcript-analysis-architecture.md` — `### review_rounds.py` section (CI-enforced, ledger row 18) and a second entry in the one-directional-exception note for the `REVIEW_SKILLS` back-import.

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
```

`select-tests.py` maps every path under `claude/.claude/scripts/` (including `transcript_analysis/`) to `claude/.claude/scripts/tests/`, and maps `docs/transcript-analysis-architecture.md` to the same directory. This diff also changes a test file *inside* that selected directory — the shape of the known under-collection defect (GH-882, [[project_select_tests_under_collection_bug]]) where a domain directory and a contained file are both selected. Confirm `test_transcript_review_rounds.py` appears in `select-tests.py`'s output; if it does not, run the directory explicitly rather than treating the gap as a pass:

```bash
.venv/bin/pytest claude/.claude/scripts/tests/
```

Test cases the new suite must cover:

1. Three `code-review` invocations on one branch produce three rounds — including when two of them sit between the same pair of user prompts with no intervening diff change (the invocation-count contract, ledger row 1).
2. A `/slash`-only round (`<command-name>/code-review</command-name>` user record, no `Skill` block) is detected and counted exactly once (rows 3, 4).
3. A directory-qualified `Skill` name (`.claude/worktrees/b/claude:code-review`) is detected; an unrelated skill (`handoff`, `plan-it`) is not (rows 5, 6).
4. A round's dollars include the opening assistant turn, every main-thread turn to the window end, and every subagent transcript dispatched inside the window — asserted against exact `_priced` amounts (row 8).
5. A fresh user prompt closes a round; a tool-result user record and an `isMeta` record do not.
6. A nested dispatch (`Agent` tool_use inside a subagent's own transcript, its meta.json in the same `subagents/` dir) is priced into the round (row 9).
7. A dispatch inside a round window whose `.meta.json` is absent or unparseable increments `dangling` and contributes no dollars (rows 10, G3).
8. No subagent turn is double-counted: a branch's round dollars plus non-round dollars equal its total priced dollars (row 11).
9. Round ordinals follow timestamp order across sessions written in reverse file-path order — the `test_transcript_workstream_cost.py:61-85` trap, re-asserted here (row 13).
10. Per-skill sub-breakdown and grand total agree; `ready-for-review` rounds appear in both (Step 4 answer 2).
11. A branch label prints raw under `--this-repo` and, otherwise, prints as `account-<K>/branch-<N>` **with the raw branch name absent from that same output** — presence-of-label and absence-of-raw-name are separate assertions (staff-sdet / ciso-reviewer round-1 finding: redaction bugs typically show up as the raw value leaking alongside the label, not replacing it) — with `DO NOT PUBLISH` above one root (row 16).
12. An unrecognized model's turn inside a round increments `unpriced_turns` and adds no dollars (row 17).
13. **(staff-sdet round-1 finding)** A Skill-shape round immediately followed by a `/slash`-shape round (or the reverse), with zero fresh-user-prompt records between them, produces exactly two correctly-bounded rounds with correctly apportioned dollars — asserted in both adjacency orders. `_is_fresh_user_prompt` happens to treat a `/slash` command's own user record as a fresh prompt, which closes a preceding Skill-shape round as a side effect of window-close bound (a) rather than because bound (b) was extended to recognize the `/slash` shape — this coincidence between two independently-maintained predicates needs its own assertion so a future edit to either doesn't silently break the transition.
14. **(staff-sdet round-1 finding)** A round left open at session end (no closing fresh user prompt, no next invocation, EOF inside the window) is priced through the last record and correctly reflected in the round/non-round reconciliation line (row 11) — not dropped, mis-priced, or thrown on.
15. **(staff-sdet round-1 finding)** A subagent's meta.json `toolUseId` collides with an id already in the recursive-descent visited set (a duplicate-`toolUseId` shape drawn from the same corrupted/retried-dispatch failure class as row 10's `dangling` handling): assert the walk terminates and the colliding dispatch's dollars are counted exactly once — not merely that the command doesn't throw.
16. **(staff-sdet round-1 finding, FYI)** A fixture corpus where one of the three `REVIEW_SKILLS` has zero invocations anywhere: the report renders without error and that skill's mean-$-per-round is a defined "no data" state, not a computed zero or an unhandled division-by-zero.
17. **(ciso-reviewer round-1 finding)** A `Skill` tool_use block whose `input.args` contains a path-like string never surfaces that string anywhere in `review-round-cost` output — regression coverage for the `input.skill`-only extraction invariant carried forward from `cmd_skill_invocation` (see Critical Files).

Manual smoke run against the live corpus, checking the reconciliation line is coherent and the round counts match a `review-trace --this-repo` reading of the same branch:

```bash
.venv/bin/python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --this-repo
```

## Out of scope

- **`claude/.claude/hooks/_lib.sh`, `log-reviewer-round.sh`, `require-architect-consult.sh`.** Read-only for this change. The `(HEAD, staged-diff)` latch answers a different question — finding-bearing rounds capped at 2, for consult escalation — and this feature is an analytics consumer of transcripts, not a change to that gate (G1).
- **A `--pr-numbers` / `gh` join.** Deferred with a named blocker: every join helper (`_gh_discover_merged_prs`, `_direct_headref_matches`, `_resolve_branch_pr`, `_resolve_pinned_gh_repo`, `_gh_call_with_backoff`) is shim-resident, and the package may not import back across that boundary (G4). Reaching PR numbers today is `pr-link --branches` against this command's `--this-repo` output. Extracting those helpers into a `transcript_analysis/pr_join.py` is a migration phase in its own right.
- **Any change to `pr-cost`'s ledger schema or `_compute_pr_cost_branch_totals`.** Adding a column is a migration (`docs/pr-cost.md`, "The row parser … is strict on column count"), and a per-round cardinality does not fit a one-row-per-PR ledger.
- **Migrating `cmd_review_trace`, `cmd_subagent_mix`, or `cmd_cost_ledger` into the package.** Their migration phases are independent of this feature.
- **Adding a dollar column to `reviewer-yield`.** A real gap, but a different question (corpus-wide dispatch→verdict yield, not per-branch round attribution) and a separate change.
- **Adopting `ccusage`, `cc-analyzer`, or Anthropic's OTel export.** No new third-party dependency; none of the three carries a review-round or PR-attribution concept, and the subagent-cost-typing slice is already solved locally by `subagent-mix` and `cost.py`.
- **Fixing the pre-existing inconsistency in `docs/transcript-analysis.md:47`** — that paragraph lists `subagents` as having "no redaction of any kind," which contradicts `:162-163`'s description of its multi-root branch redaction. Not this change's file to correct; raising it to the PR reviewer instead.
