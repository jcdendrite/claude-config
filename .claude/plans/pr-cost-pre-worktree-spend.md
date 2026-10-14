# Plan: attribute pre-worktree spend in PR Cost sections

## Context

Goal: decide and implement an attribution rule so a PR's Cost section accounts for same-session spend recorded under `gitBranch=main` before the session entered the feature worktree.

Ask: engineer invoked `/plan-it` on the handoff file `pr-cost-pre-worktree-spend-handoff.md`, whose §3 problem statement (agent-written) reads "PR Cost sections omit spend that occurs on `main` before a session enters its feature worktree; decide the attribution rule." Widened by the engineer: "I think all branches consumers otherwise we have divergence among figures." On the rule choice the engineer said: "I'm really not sure. I think this is a complicated decision. What does the architect think". Selected label for backfill: "No backfill (Recommended)". On the consumer set the engineer first said: "I'm leaning toward the six cost-family consumers, but get an adversarial fable review on that"; after that review they selected the label "All per-record consumers (Recommended)". For multi-PR sessions they selected "First leading run only (Recommended)". They then asked: "and have the architect review both answers again". On `worktree-agent-*` resolution outside the cost family they said: "I think we need to fix this all in this PR. Get Fable's take please." On cross-repo sidechain records they said: "why would we include a different repo's sidechain?! That's insane. There must be a foundational bug if that's a possibility." After a Fable consult on the attribution foundation, they selected the label "Adopt Fable's design (Recommended)". Asked whether a subagent this session dispatched that worked in another repo should count toward the session's PR, they said: "So you're worried about a main session working in one repo and dispatching a subagent working in another repo and that subagent's work getting counted as part of the main agent work? I mean I don't think that actually is a valid scenario with my harness, but I guess in that case it still makes sense to count the subagent's work toward the main agent's PR. Ask the architect for its opinion as well."

Problem: `pr-cost-section.sh` runs `cost` and `cost-counts` with `--branches "$branch"`; `cost` keeps a record only when its attributed `gitBranch` is in the set. A session that plans on `main` (including an Opus architect subagent) and then enters its worktree has that prefix dropped. Planning precedes the branch name, so the prefix is structurally unavoidable.

Evidence gathered this session (local reads only; figures are withheld because none was scoped with a refusing instrument, per `docs/private-project-redaction.md` § "This repository, one account"):
- A local read pointed toward subagent records usually carrying `gitBranch`, though some records can lack one (Verification 1(c)). When planning comes before the branch exists, the Opus architect's records sit under the default branch.
- Some local sessions start on the default branch and then enter one feature branch. A local read pointed toward the leading default-branch stretch carrying spend in those sessions, with Opus subagents as one source.
- No existing mechanism attributes pre-branch spend. Only `worktree-agent-*` sidechain records are carried forward, and only in the cost family.
- `cost` attributes every other sidechain record by its own `gitBranch`. Round attribution and `review-trace` give every sidechain record the dispatching main thread's branch instead.
- `docs/transcript-analysis.md:647` documents `--branches` as "Per-record, not per-session".

## Approach

**Design.** Every per-record `--branches` consumer attributes a record through one helper, `scope._attributed_branch(rec, ctx)`, which applies two steps.
- **Timeline step.** A main-thread record keeps its own tag. A sidechain record never uses its own tag. It takes the branch the session's main thread was on at that record's timestamp: the last main-thread entry at or before it, falling forward to the first entry. `cost` already does this for `worktree-agent-*` records; now every sidechain record follows it.
- **Pre-branch step.** Within one session, a default-branch entry (`main`, `master`, `develop`) before the main thread first moves to another real branch counts toward that first branch. Only the session's first such run is remapped.
- **Positional consumers.** Round attribution and `review-trace` keep their carry-forward, which already gives sidechain records the main thread's branch. They pass the carried value through `scope._attribute_carried_branch`, which applies only the pre-branch step.
- Whole-session filters and sequence boundaries keep the literal tag.
- An AST test fails on any unclassified `"gitBranch"` constant.

**Why this rule (the engineer delegated the choice, row 16).**
- **A (whole session) is rejected.** It breaks the documented per-record contract (`docs/transcript-analysis.md:647`) and misprices multi-branch sessions in both directions.
- **C (a separate disclosed line) is rejected.** Deduplicating the prefix across PRs needs this same pre-pass, and the ledger would need a new column to agree with the section.
- **B+C is rejected.** It adds a disclosed figure to every PR body. That changes what the pre-cleared block prints (row 50), and the engineer chose no new sentence (row 38). The split stays available locally, because M10 prints moved turns and dollars in the full report. The trailer command does not give the split: it re-emits the same `--summary` block (row 46).
- **D (process fix) is rejected.** Plan mode blocks branch creation (G2).

How B settles the five tensions:
1. **Multi-PR sessions.** The first leading run goes to the first real branch only (row 19). Each record still lands in exactly one aggregate, so the all-branch total does not change (row 13). Later default-branch stretches stay on that branch.
2. **Ledger vs section.** Both resolve through `scope._attributed_branch` over the same deduped records (row 2), so they agree by construction.
3. **Per-record contract.** Attribution stays per record and uses session context, as `worktree-agent-*` attribution already does. `:647` stays true and gains a pointer.
4. **Disclosure.** The `--summary` block's printed shape, its `Scope:` caption, and both calls' single-root `--this-repo` scope stay unchanged (row 38). The rule is documented in `docs/transcript-analysis.md` and CHANGELOG. The standing pre-clearance (`docs/private-project-redaction.md:125-141`) is not reopened. Its scope and refusals are unchanged, and the block's printed shape is unchanged (row 38), so neither of its reopening triggers fires. The doc does not define "private-engagement record". The plan relies on a consult's reading of it (row 64), and on the engineer's decision (row 63; the owner role is assumed, row 64), not on a definition in the doc. The plan reads the premise as unchanged (row 64). Both steps move only same-session records. A sidechain record's attributed branch is always one its own session's main thread recorded, so a sidechain record's own tag never decides its branch. A dispatched subagent's spend counts toward its dispatcher whatever its `cwd` (row 58). The caption's "this repository only" names the sessions read, and that selection is unchanged.
5. **Pure Q&A and separate-session planning.** A session that never leaves the default branch stays on it. Q&A that comes before branch work in the same session is folded into that branch (row 14). Planning done in a separate session that stays on the default branch is not moved. That is the same structural cause as G2, and it is a documented limit.

**Sidechain records follow the dispatching main thread (the engineer's questions; rows 37, 51, 52, 58).** The exposure was an attribution inconsistency, not a corpus-scoping bug.
- `--this-repo` selects sessions by the parent session's project directory, and subagent transcripts live under it (row 41). That scoping is deliberate. A subagent's spend is the dispatching session's real spend.
- `cost` trusted a non-`worktree-agent-*` sidechain record's own tag (rows 1, 2), while round attribution and `review-trace` already ignored it (row 40).
- A sidechain tag is not a repo identity (row 32). Literal matching therefore admitted a foreign-tagged record whenever its tag equalled the filter. The pre-branch step would also have turned the shared `main` tag into the PR's branch.

The timeline step removes the inconsistency at its source. It excludes the label, not the spend:
- **Label.** A sidechain record can be labeled only with a branch its own session's main thread recorded. A sidechain record's own foreign tag never becomes a label, and a shared tag name no longer admits a sidechain record into a `--branches` total.
- **Spend.** A dispatched subagent's spend counts toward the dispatching session's branch whatever its own tag or `cwd`. That includes a subagent this repo's session dispatched into another repo. The engineer chose this for attribution (row 58) and for inclusion in the PR Cost section (row 63). The case has occurred (row 62), and Verification 1(f) sizes it locally before the build.
- No consumer reads `cwd`, and no eligibility gate exists.
- **`subagents` gate.** Under `--this-repo`, every branch `subagents` prints was recorded by a main-thread record. Its attestation gate stays and fails closed: a branch no main-thread `assistant` or `user` record carried prints under the opaque `account-<K>/branch-<N>` label (row 61).

Behavior changes beyond the pre-branch rule:
- **Divergent sidechain tags.** A non-isolated sidechain record whose own tag differs from the main thread's branch at its timestamp now follows the main thread. The expected common case is a subagent still running after the session switches branch, including an architect that is still running when the session enters X; it now lands wholly on X. Verification 1(c) measures how often this happens and whether such a subagent keeps its dispatch-time tag (row 60).
- **Cross-repo subagents.** A subagent this session dispatched into another repo counts toward the dispatching branch (row 58). Before, it counted toward whatever branch its own tag named.
- **Untagged sidechain records.** A sidechain record with no `gitBranch` of its own now takes the main thread's branch instead of falling into the unbranched total.
- **Sessions with no main-thread branch.** In a session with no branch-bearing main-thread record, sidechain records resolve to `None` (`?`, unbranched). Before, they used their own tags.
- **Timestamp-less main threads.** When none of the session's branch-bearing main-thread records has a parseable timestamp, a sidechain record takes the first main-thread branch in file order (row 53).
- **Totals.** Unfiltered grand totals do not change. `pr-cost`'s all-branch total plus `unbranched_totals` does not change, because the change only moves records between aggregates.

Two-model residuals, documented in the rule block:
- Rounds price a whole dispatch at its spawn record's branch, while per-record consumers split a subagent that straddles a main-thread switch. Both put the whole dispatch on X in a prefix session.
- `review-trace` renders `?` where per-record consumers fall forward.
- Inside a review round's window, the round's key governs every record (pass 2 of `_session_record_branches`). This affects `review-round-cost` dollars only, never the PR section.

**Consumer set (decision record).**
- Round 1: the architect recommended the six cost-family consumers.
- The engineer said "I think all branches consumers otherwise we have divergence among figures. What does the architect think?" (row 17), then selected "All per-record consumers (Recommended)" (row 18).
- On `worktree-agent-*` resolution outside the cost family, the engineer said "I think we need to fix this all in this PR. Get Fable's take please." (row 22).

The settled set:
- **Every per-record `--branches` consumer adopts the full composed attribution:** `cost`, `review-round-cost`, `cost-counts`, `subagent-mix`, `buckets`, `fail-seq`, `struggle`, `user-input`, `duration`, `judgment-pair`, `skill-invocation`, `skill-pair`, `pr-link`, `subagents`.
  - The timeline step reaches only the consumers that read sidechain records: the cost family, `subagents`, `skill-invocation --include-subagents`, `skill-pair`, and inline sidechain records in `buckets` and `duration`.
  - The others exclude sidechain records, so only the pre-branch step reaches them (row 55).
- **Shared-pass and derived adopters:**
  - `pr-cost` and `workstream-cost`, through the shared branch-totals pass.
  - `handoff-signal-response`'s startup-burn benchmark, through `cost._compute_workstream_dollars` (row 43).
  - Review-bench mining, through `review_rounds._session_record_branches` (row 44). Its prefix rounds join X, the group a `/plan-review` held after branch creation already joins. That function keeps its two-argument signature.
- **Positional consumers:** `review-trace` and round attribution apply `_attribute_carried_branch` to their carry-forward (rows 4, 24, 40).
- **Exempt:**
  - `commit-gate` and `rearm-backtest` select whole sessions (row 25).
  - `turn-shape` streak breaks and `fail-seq`'s pending-pair reset are points where the checkout physically changed (row 26).

**Adversarial reviews.**

*First review (six-consumer set):*
- F1 accepted; it drove the all-consumer set.
- F2 accepted and widened: `HEAD` and `worktree-agent-*` entries are skipped as successor.
- F3 accepted as docs; first-only holds.
- F4 partly accepted: `docs/pr-cost.md` and `:1129` are in; the SKILL.md wording edits are out.
- F5 accepted.
- F6 accepted (M10).
- F7 accepted (M12).
- F8 accepted, with tests now listed.
- F9 accepted as a limit.
- The `handoff_nudge` test edit was rejected.

*Fable review (worktree-agent scope):*
- Accepted:
  - one composed entry point, applied resolve-then-remap;
  - `review-trace` keeps its carry-forward and `?`;
  - delete `cost._attributed_branch` with no wrapper;
  - `skill-invocation` and `review-trace` feed published and fidelity figures;
  - gap 1 (resolved-entry timestamp);
  - gap 2 (skip `worktree-agent-*` and `HEAD` as successor);
  - gap 4 (`duration`).
- Gap 4's `fail-seq` part was resolved differently: the reset stays raw.
- Gap 3 (cross-repo fold) is superseded by the timeline step (rows 37, 51, 58).

*`/plan-review` round 1:*
- staff-backend-engineer:
  - B1.1, B1.2, B1.4 and B4 accepted: local census with stop conditions (Verification 1).
  - B1.3 accepted: `None` handling per site, plus a branch-less `user-input` test.
  - B2.1 accepted (`handoff-signal-response`).
  - B2.2 accepted: two-argument signature kept, plus an evals test.
  - B2.3 accepted as an effects clause.
  - B2.4 accepted as a stated limit: derived consumers are pinned by behavioral tests.
  - B3 accepted: CHANGELOG and the rule block move into Dispatch 1.
  - B11.1-3 accepted: per-checkout cutover, no ledger column.
  - K1.1 accepted (sample rewrite).
  - K1.2 accepted.
  - K1.4 superseded by the timeline step.
  - K2 accepted as FYI with no change.
  - The vacuous conservation check was accepted and replaced.
- staff-sdet:
  - Accepted: every-constant AST collector, innermost-def attribution with `<module>`, one key format, per-function counts, synthetic-snippet tests.
  - The redundant non-vacuity assertion is dropped.
  - Accepted: explicit increasing timestamps, tie and near-miss cases, the three named `review-trace` bases.
  - Accepted: `--since`/`--until` tests, the cross-consumer agreement test, the evals test.
  - Accepted: the census, A/B and M10 fixes; the two-case `fail-seq` test; the A/B recipe and owner.
  - The discriminating attestation test was replaced in round 1. Round 2 restores a deny-path test (row 61).
  - "No existing fixture changes" now holds for the pre-branch step only (row 47). The timeline step's flips are named (row 54).
- staff-product-engineer:
  - The false trailer premise is corrected.
  - The constant-sentence recommendation is not adopted (row 38). Moved dollars in M10 are adopted.
  - The ledger items are accepted, except a merge-date `captured_at` cutoff for every row. Round 2 adopts its sound one-sided form.
  - Row 21 is confirmed by the engineer.
  - Accepted: `handoff-signal-response` with non-comparability notes, `docs/case-studies/*` left untouched, same-session wording plus the separate-session limit and a local residual check.
  - The cross-repo finding is resolved by the timeline step and row 58.
  - The `_lib.sh` pin is accepted (M14). The `buckets` Date range is accepted.
  - The fidelity-reviewer run is accepted as this PR's own `/ready-for-review`.
- ciso-reviewer:
  - F2 accepted: the Context evidence is figure-free.
  - F3 superseded by the timeline step; the residuals are listed in the rule block; the pre-clearance statement is in tension 4.
  - F1 accepted: index-purity test and comment, multi-root output-level tests.
  - The `:202` doc fix is accepted.
  - The OUTPUT INVARIANT stays untouched.

*Fable foundation consult (after `/plan-review` round 1; the engineer adopted it, row 51):*
- Accepted:
  - every sidechain record resolves against the main-thread timeline inside `scope._attributed_branch`, and its own tag is never read;
  - the `cwd` gate and its context field are dropped;
  - the `worktree-agent-*`/`HEAD` check applies only to main-thread entries, when choosing the successor;
  - `_pre_branch_moved` compares the timeline branch, not the raw sidechain tag;
  - the post-boundary sidechain test flips;
  - the `subagents` test is reworded;
  - `None` for sessions with no branch-bearing main-thread record is documented.
- Refined: an empty timestamped index falls back to the first main-thread branch in file order. This matches Fable's stated `None` condition (rows 53, 54).
- Rejected: "the `:1447` caveat shrinks". That caveat compares rounds with spawns, and both are main-thread only. It gains only "both apply the block".
- Rejected alternatives: corpus-seam drop, corpus-seam repo tag or per-record repo identity, and the `cwd` gate (lighter-primitive check).

*`/plan-review` round 2 (all accepted unless noted):*
- staff-backend-engineer:
  - B1.1 / B4: row 59, Verification 1(e) and the fixture-flag note.
  - B1.3: row 7 is no longer cited as a frequency bound; G1 and row 7 are reconciled through row 60 and Verification 1(c).
  - B2: the `user-input` counts effect is added.
  - B3: the Dispatch 1 wording at `:47`/`:669` is scoped to the rule block's adopter list.
  - K1.1: `:47`'s last sentence is rewritten in both dispatches.
  - Attestation: the claim is reworded to "recorded", and the gate fails closed (row 61).
  - Lookup: `bisect` in `_timeline_entry`.
  - 1(c) includes empty tags.
  - M10 is settled by into-filter semantics.
  - The row-37 flag is answered by row 58.
- staff-sdet:
  - Deny-path `subagents` test.
  - New `review-trace` test functions through `cmd_review_trace`.
  - Passthrough without `--`, with a per-dispatch file check.
  - Tests-first order with named failing sets.
  - M10 format constant, semantics, cases and one-cent tolerance.
  - Agreement adapters.
  - Full AST keys with `scope.py` pinned at 1/1/1.
  - Timestamped `subagents` rewrites; the collision test is deleted.
  - `ruff` on the changed `evals/` file only.
  - Unconditional worktree removal.
  - Mixed-candidate case, conservation oracle, stale-text list.
- staff-product-engineer:
  - CHANGELOG bullet 3 and the retitled `docs/pr-cost.md` subsection name both steps and both directions.
  - The census and residual move before Dispatch 1, with an engineer go and stop conditions.
  - Row-37 wording, census item 1(f), and the dispatched-subagent clause.
  - `docs/pr-cost.md:255,257,310`, the one-sided rule, and only the pre-cutover row re-captured.
  - The candidate-only timeline alternative is added to the lighter-primitive check.
  - Row 38: no change, because the caption describes session scope, which is unchanged.
  - `transcript-analysis/SKILL.md:86`: left as written (Out of scope).
- ciso-reviewer:
  - F1 (reword, row 59, census 1(e)).
  - F2 (label vs spend wording, mirrored).
  - F3 (absence assertions on a nonzero-moved fixture, with line-shape equality).
  - F4 (strings stay local; verdicts are direction only).
  - The zero-range Context rewording is adopted.

**Assumption ledger**

Root: PR Cost sections, `pr-cost` ledger rows and every other per-branch figure leave out same-session records that Claude Code tagged with the default branch before the session entered the PR's branch. `cost` also takes a sidechain record's own tag literally, while the positional consumers use the dispatching main thread's branch, and the per-record consumers outside the cost family take `worktree-agent-*` tags literally.

Givens:
- G1: Claude Code stamps `gitBranch` and `cwd` on each record from the checkout at write time, and transcripts carry no default-branch identity. The vendor owns the record format.
- G2: Plan mode blocks branch creation, so plan-mode planning runs on whatever branch the checkout is on. The harness imposes this, and `claude-skills/skills/plan-it/SKILL.md:19` only conforms to it.

Rows:
1. [verified: claude/.claude/scripts/transcript_analysis/cost.py:39-91] `_session_branch_index` / `_attributed_branch` remap only `worktree-agent-*` records and return every other record's own tag. They fall forward to the first entry when a record has no timestamp or predates the index.
2. [verified: cost.py:140-154, cost.py:849-851, pr_cost.py:532,821] `cost --branches` and `pr-cost` both resolve every record, sidechain included, through `_attributed_branch`, from records deduped the same way.
3. [verified: cost.py:212,272-275] `workstream-cost` reuses `_compute_pr_cost_branch_totals` and resolves startup-burn turns through `_attributed_branch`.
   - [verified: grep of cost_ledger.py] `cost-ledger` has no branch attribution.
4. [verified: review_rounds.py:14-18,140-170,389-427; docs/transcript-analysis-architecture.md:154-159] Round attribution is a positional main-thread carry-forward shared by `review-round-cost` and `cost-counts`, and the module never imports `cost.py`.
5. [verified: subagent_mix.py:179-184,347-354] `cmd_subagent_mix` and `cost-counts`' spawn counts match a main-thread record's literal `gitBranch`.
6. [verified: pr-cost-section.sh:38,46] The PR Cost section is `cost --summary` plus `cost-counts`, both run with `--branches "$branch"`.
7. [unverified] A local read pointed toward a non-isolated subagent's records carrying the main thread's branch at dispatch time. That does not bound frequency. Verification 1(c) measures that.
8. [verified: claude/.claude/hooks/_lib.sh:1273-1304; docs/hooks.md:194] `main`, `master` and `develop` are this repo's fallback default-branch candidates.
9. [verified: claude/.claude/scripts/tests/test_default_branch_resolution_is_shared.py:1-6] Reading `refs/remotes/origin/HEAD` is confined to `_lib.sh`.
10. [verified: claude/.claude/scripts/transcript_analysis/author_outcome.py:88-91] Precedent: Python copies a small `_lib.sh` literal, with a comment naming its source.
11. [verified: docs/transcript-analysis.md:669] Worktree-agent attribution already folds spend into a branch's `--branches` total with no caption disclosure.
12. [verified: claude-skills/skills/transcript-analysis/SKILL.md:24] That skill quotes the `Scope:` caption's form.
13. [verified: cost.py:153-154] Each record lands in exactly one aggregate, so a remap leaves the all-branch total unchanged.
14. [unverified] A session's leading default-branch run is setup for the branch it then enters. Partial support: a local read pointed toward Opus subagent work in the prefix. Verification 1(g) and 5 test this locally.
15. [unverified] Main-thread file order and timestamp order differ by at most sub-second jitter. Verification 1(a) checks this, with a stop condition.
16. [engineer-verified: "I'm really not sure. I think this is a complicated decision. What does the architect think"] The engineer delegated the attribution-rule choice.
17. [engineer-verified: "I think all branches consumers otherwise we have divergence among figures. What does the architect think?"] The engineer leaned toward every `--branches` consumer.
18. [engineer-verified: "All per-record consumers (Recommended)"] Every per-record `--branches` consumer adopts the rule.
19. [engineer-verified: "First leading run only (Recommended)"] Only a session's first leading default-branch run is remapped.
20. [engineer-verified: "No backfill (Recommended)"] Existing PR bodies are not regenerated.
21. [engineer-verified: "Yes, no backfill also includes existing pr cost ledger rows."] Existing `pr-cost` ledger rows are not re-recorded.
22. [engineer-verified: "I think we need to fix this all in this PR. Get Fable's take please."] `worktree-agent-*` resolution outside the cost family lands in this PR.
23. [verified: transcript-analysis.py:355-356, 416-421, 490-495, 666-669, 850-852, 934-936, 1134-1142, 1300-1307, 1403-1405; subagents.py:128-131,141-144] The non-cost per-record consumers match the literal `gitBranch`. `subagents`, `skill-pair` and `skill-invocation --include-subagents` read sidechain records.
24. [verified: review_trace.py:233-268,296; docs/transcript-analysis.md:465] `review-trace` carries the last main-thread branch to every event and never reads a sidechain record's own tag. With no preceding main-thread branch, an event renders `?`.
25. [verified: transcript-analysis.py:1526-1534; handoff_nudge.py:401-420] `commit-gate` and `rearm-backtest` include or exclude whole sessions.
26. [verified: transcript-analysis.py:3421-3464, 416-420] `turn-shape` reads `gitBranch` only to end a streak. `fail-seq` clears unpaired test runs on a raw branch change.
27. [verified: skill-fidelity-report.sh:16,22; transcript-analysis.py:1095-1102] `skill-invocation --branches` and `review-trace --branches` feed `/ready-for-review`'s fidelity check. `skill-invocation` output is quoted into public PR descriptions.
28. [verified: subagents.py:97-101,116-130,140-143,171-177] Under `--this-repo`, `subagents` prints a branch raw only when a main-thread `assistant` or `user` record carried that (root, branch) pair, and checks membership against the printed key.
29. [verified: tests/test_transcript_analysis_cost_import_direction.py:28-31; repo grep for `_attributed_branch`/`_session_branch_index`] Moving both helpers breaks no importer, and the shim re-exports at `transcript-analysis.py:94,103` have no reader.
30. [verified: pr-cost-section.sh:27] `HEAD` names no PR branch.
    - [unverified] Claude Code records a detached checkout as `gitBranch: "HEAD"`. Verification 1 checks this.
31. [unverified] A main-thread `worktree-agent-*` record occurs only in a session started inside an agent worktree.
32. [verified: docs/transcript-analysis.md:47,202; subagents.py:44-48,97-100] A sidechain record's own `gitBranch` can name a different repo than its parent session's, or disagree with its `cwd`'s checkout.
33. [verified: corpus.py:142-152] Split subagent files are read only under `include_subagents`.
    - [unverified] Legacy inline `isSidechain` records can carry `worktree-agent-*` tags. This is moot under universal adoption.
34. [engineer-verified: "Keep (Recommended)"] The M10 full-report audit line stays in this PR.
35. [engineer-verified: "Keep (Recommended)"] The structural AST test stays in this PR.
36. [engineer-verified: "Keep (Recommended)"] The `docs/pr-cost.md` comparability paragraph stays in this PR.
37. [engineer-verified: "why would we include a different repo's sidechain?! That's insane. There must be a foundational bug if that's a possibility."] The engineer rejects folding a different repo's sidechain records into a branch's figures, and suspects a foundational cause.
38. [engineer-verified: "No sentence (Recommended)"] The `--summary` block gains no new sentence, and its printed shape stays unchanged.
39. [unverified] A local read pointed toward sessions in this repo's project directories with no record `cwd` outside this repo's tree, and no sidechain record with a `worktree-agent-*` tag. This is a direction from a vendor-owned format (G1), not a guarantee, and row 62 records a counter-example for `cwd`. Verification 1(f) and 1(h) test the `cwd` clause. No verification item tests the `worktree-agent-*` clause, which is moot under universal adoption (row 33).
40. [verified: review_rounds.py:158-165; review_trace.py:262-268,296] The positional pair assigns the carried main-thread branch to every record, sidechain included.
41. [verified: scope.py:82-114; docs/transcript-analysis.md:47; grep of transcript_analysis/ for `cwd`] `--this-repo` selects sessions by the parent's project directory, and subagent transcripts live under the parent's project directory. No `--branches` consumer reads a record's `cwd`; only `reviewer_yield.py` reads it, for path normalization.
42. [verified: claude/.claude/CLAUDE.md, Agent Briefing's `isolation: "worktree"` bullet] A `worktree-agent-*` branch is the harness's checkout of the dispatching session's own repository at a committed ref.
43. [verified: handoff_signal_response.py:387-402,600-608; docs/transcript-analysis.md:1598] `handoff-signal-response`'s benchmark and `exceeds_startup_burn_benchmark` come from `cost._compute_workstream_dollars`.
44. [verified: evals/review_bench/mine_review_rounds.py:172-187,426-439,642-659] Review-bench mining keys each round on `_session_record_branches(records, windows)[open_idx]`, groups rounds by it, and resolves each group's touched paths from its merge-base with `origin/main`.
    - [unverified] A `main` group touches no paths unless local `main` is ahead of `origin/main`. If so, today's `main`-grouped rounds yield no candidates, and no prior adjudication is keyed to them.
45. [verified: CHANGELOG.md:5; docs/pr-cost.md:16-17,35,166; claude-skills/skills/pr-description/SKILL.md:85; repo CLAUDE.md "Changes under `claude/.claude/**` go live on `git pull`"] Facts the docs instruction must reflect:
    - CHANGELOG has no release section;
    - no ledger column records the attribution rule;
    - cutover is per checkout, at pull time;
    - an open PR's Cost section regenerates on every sync;
    - a forced re-capture is sound only inside `cleanupPeriodDays`.
46. [verified: pr-cost-section.sh:64-70] The reproducibility trailer names `pr-cost-section.sh`, which re-emits the same `--summary` block. No command in a PR body prints the prefix split.
47. [verified: tests/conftest.py:621-646,789-798; cost.py:56-60; staff-sdet scan of the named test files] `_asst` stamps no timestamp by default and `_priced` stamps one constant timestamp. The pre-branch step's `<` is strict, so the pre-branch step remaps no existing fixture.
48. [verified: select-tests.py:78-88,548,709-713] The new test files are selected through the `claude/.claude/scripts/tests` directory target. A `scope.py` or `review_rounds.py` change also selects the review-bench tests.
49. [verified: claude/.claude/hooks/_lib.sh:1290-1297,4304] `_lib.sh` lists `main master develop` in `_lib_default_branch_or_guess`'s loop, and again in `_LIB_REVIEW_LEDGER_DEFAULT_BRANCH_NAMES`.
50. [verified: docs/private-project-redaction.md:100-141] A published figure needs a refusing instrument cited beside it. The `pr-cost-section.sh` pre-clearance holds until the block's printed shape or either call's scope changes.
51. [engineer-verified: "Adopt Fable's design (Recommended)"] Every sidechain record resolves against the dispatching main thread's timeline inside `scope._attributed_branch`, with no `cwd` gate.
52. [verified: scope.py:82-114; docs/transcript-analysis.md:47; cost.py:64-91,849-851; review_rounds.py:158-165; review_trace.py:262-268] The exposure was an attribution inconsistency, not a corpus-scoping bug.
    - Scoping by parent session is deliberate, and it prices real session spend.
    - `cost` trusted sidechain tags, while the positional consumers never read them.
53. [unverified] Real main-thread records always carry a parseable timestamp, so the file-order fallback never fires on real data. Verification 1(d) checks this, with a stop condition.
54. [verified: reads of test_transcript_workstream_cost.py:152-173, test_transcript_analysis.py:6036-6047, test_transcript_subagents.py:45-67,69-82,135-155,555-660; conftest.py:621-640; confirmed by the round-2 staff-sdet trace] These existing tests change under the timeline step:
    - `test_non_first_session_that_is_subagent_only_contributes_zero_startup_burn` (empty main file, so `None`); it fails at Dispatch 1;
    - `test_branch_filter_composes_with_include_subagents` (an `other`-tagged sidechain record now follows `wanted`); it fails at Dispatch 2;
    - `test_this_repo_discloses_attested_main_thread_branch_but_not_sidechain_only_branch` and `test_this_repo_cross_account_attestation_independence` (a branch seen only on sidechain records no longer prints); both fail at Dispatch 2;
    - `test_this_repo_branch_attestation_collision_residual_folds_unattested_sidechain_into_disclosed_row` (docstring premise only; it passes unchanged).

    The other timestamp-less sidechain fixtures I read resolve through the file-order fallback to the tag they already carry.
55. [verified: subagent_mix.py:180,350; transcript-analysis.py:421,494,535,918,1405; cost.py:207] Spawn counts, `fail-seq` counting, `struggle`, `user-input`, `judgment-pair`, `pr-link` and startup burn exclude sidechain records, so only the pre-branch step affects them.
56. [engineer-verified: "Keep (Recommended)"] The test pinning `_DEFAULT_BRANCH_CANDIDATES` to `_lib.sh`'s list stays in this PR (answer to the `/plan-review` round-2 scope check).
57. [engineer-verified: "Keep (Recommended)"] `handoff-signal-response` and review-bench mining stay in the plan as adopters through shared helpers, with their tests and docs note (answer to the round-2 scope check).
58. [engineer-verified: "So you're worried about a main session working in one repo and dispatching a subagent working in another repo and that subagent's work getting counted as part of the main agent work? I mean I don't think that actually is a valid scenario with my harness, but I guess in that case it still makes sense to count the subagent's work toward the main agent's PR. Ask the architect for its opinion as well."] A subagent this repo's session dispatched counts toward the dispatching session's branch even when it worked in another repo. The engineer doubts the case occurs with their harness.
59. [unverified] Every record in a split subagent file carries `isSidechain: true`, user records included. `corpus.read_session_file`'s docstring (`corpus.py:163-165`) documents the flag on assistant records only. A round-2 reviewer's local check pointed toward no exception. Verification 1(e) checks this, with a stop condition.
60. [verified: claude/.claude/CLAUDE.md, Working Style, "Dispatching cannot clear a denial your child inherits" bullet] A subagent starts in its dispatcher's working directory.
    - [unverified] A subagent's working directory, and therefore its own tag (G1), stays at that starting checkout when the dispatcher later enters a worktree. That reconciles G1 with row 7: a subagent still running after the switch keeps its dispatch-time tag. Verification 1(c)'s switch split checks it.
61. [verified: cost.py:49-59; subagents.py:116-130,140-143] The timeline index admits a branch-bearing main-thread record of any type, while `subagents` adds to its attestation set only from main-thread `assistant` and `user` records. A branch recorded only by another main-thread record type can be a timeline branch, and `subagents` then prints it under the opaque label.
62. [verified: .claude/plans/cross-repo-subagent-cost-attribution.md:112-126; docs/transcript-analysis.md:47] A prior investigation observed a subagent whose `cwd` was a different, already-existing repo than its dispatching session's, and whose own `gitBranch` did not name its `cwd`'s branch. `:47`'s last sentence gives that as the reason an all-zero `--branches` result is inconclusive.
    - [unverified] A subagent can read or write another repo by absolute path without its recorded `cwd` changing, so Verification 1(f) is a lower bound on the cross-repo cohort.
63. [engineer-verified: "It's intentional to have pr cost include all work that contributed to that PR, including main and subagent."; also "Realistically, the subagent is only going to be looking at another repo that is part of of THE SAME ORGANIZATION."; also "pr cost disclosure IS published in private repositories - it IS. That's intentional, if the pr cost disclosure config key is true."] Asked whether the PR Cost section may include cross-repo subagent spend after a CISO finding on the pre-cleared block, the engineer said the above. Decision: the PR Cost section includes main-thread and subagent work that contributed to the PR, including a subagent that worked in another repo. The engineer expects any such repo to belong to the same organization, and the rule and docs do not condition on that.
64. [unverified] The reasoning offered for row 63, which is a Fable consult's reading and not the engineer's words:
    - The pre-clearance's "private-engagement record" (`docs/private-project-redaction.md:104-106`) means a record of a session anchored in a private repo, which `--this-repo` excludes by identity (`scope.py:93-113`).
    - The disclosure key is account-scoped by design (`docs/hooks.md:215`).
    - A subagent this repo's session dispatched is that session's own record, like a main-thread read of another repo's checkout, which the corpus already holds.
    - `docs/private-project-redaction.md:148`'s "spend on public work" and the `Scope:` caption's "this repository only" are left unedited, as are the opt-in texts at `docs/hooks.md:215-217` and `docs/transcript-analysis.md:671` (row 65).
    - The engineer's role as repo owner, to whom `docs/private-project-redaction.md:150-152` routes doubt, is assumed from the engineer making row 63's statement.
65. [engineer-verified: "Leave unedited (Recommended)"] Asked whether the PR leaves the `Scope:` caption, the redaction doc and the opt-in text unedited or adds a pointer to the cross-repo-subagent inclusion, the engineer chose to leave them unedited.

Mechanisms:
- M1. Move `_session_branch_index` and `_WORKTREE_AGENT_BRANCH_PREFIX` into `scope.py` with bodies unchanged. Add the comment `# Skipping sidechain records is a disclosure invariant: every branch this index yields was recorded by the session's main thread.` — anchors: row4, row22, row28, row29, row59
- M2. Add `_DEFAULT_BRANCH_CANDIDATES`, `_DETACHED_HEAD_BRANCH`, `_names_no_pr_branch`, `_PreBranchRun`, `_pre_branch_run` and `_attribute_pre_branch` to `scope.py`. — anchors: root, row8, row10, row19, row30, row31
- M3. Add `_SessionBranchContext`, `_session_branch_context`, `_timeline_entry`, `_attributed_branch`, `_attribute_carried_branch` and `_pre_branch_moved`. A sidechain record's branch comes only from its session's main-thread timeline, found by `bisect`. Delete `cost._attributed_branch` and both shim re-exports, with no wrapper. — anchors: row1, row2, row3, row7, row29, row32, row37, row40, row51, row52, row53, row58, row59, row60
- M4. In `_session_record_branches`, pass 1 runs each carried value through `scope._attribute_carried_branch`. The function derives the context from `records` internally and keeps its signature. Pass 2 is unchanged. — anchors: row4, row6, row44
- M5. Both literal reads in `subagent_mix` call `scope._attributed_branch`. — anchors: row5, row6, row55
- M6. Use a fixed candidate set, not git resolution. — anchors: row9
- M7. Neither the `--summary` block nor its caption changes. One rule block in `docs/transcript-analysis.md` holds the rule and the only adopter list, and other docs point to it. `:47` and the rule block state that a dispatched subagent's spend counts toward its dispatcher whatever its `cwd`. CHANGELOG and `docs/pr-cost.md` state both steps, both directions, the per-checkout cutover, the one-sided classification rule and the re-capture limits. No ledger column is added. — anchors: row11, row12, row21, row27, row38, row45, row46, row58, row62, row63
- M8. Every non-cost per-record consumer calls `scope._attributed_branch`. The `subagents` attestation set stays raw and fails closed to the opaque label; a deny-path test pins it. — anchors: row17, row18, row22, row23, row28, row61
- M9. `review-trace` keeps its carry-forward and `?`, and passes the carried value through `scope._attribute_carried_branch`. — anchors: row24, row40
- M10. The full `cost` report under `--branches` prints one line, from a named format constant, with the turns and dollars that the pre-branch step moved into the filter set from outside it. It never prints under `--summary` or `--share-only`. — anchors: row11, row38
- M11. These keep the raw tag: `commit-gate`, `rearm-backtest`, `turn-shape` streak breaks, `fail-seq`'s pending reset and its session pre-filter. — anchors: row25, row26
- M12. An AST test classifies every `"gitBranch"` string constant in `transcript_analysis/*.py` and the shim. — anchors: row17, row18, row35
- M13. Behavioral tests pin the derived adopters, which the AST test cannot see: `handoff-signal-response` and review-bench mining. — anchors: row43, row44
- M14. A test pins `_DEFAULT_BRANCH_CANDIDATES` to `_lib_default_branch_or_guess`'s loop. — anchors: row10, row49
- M15. The row-54 tests are rewritten in place, and the collision residual test is deleted, as Critical files specifies. Any other failing or changed existing assertion stops the dispatch. — anchors: row47, row54

Lighter-primitive check. M3, M8 and M12 add a composed per-session helper, adopt it at about 18 call sites, and add an AST test:
- **Widen `pr-cost-section.sh` to `--branches "$branch,main"`.** This counts every session's `main` spend into every PR. Fails root.
- **Process fix.** Fails G2.
- **Change only cost's helper.** The rounds and spawns tables would diverge. Fails row4, row6, row18.
- **Separate disclosed line (C).** Fails row2, row38.
- **Resolve the default branch from git per run.** Fails row9.
- **Inline remap per call site.** Restates the rule about 18 times. Fails row17.
- **Pre-branch step only where no sidechains are read.** Creates two attribution APIs. Fails row22, row23.
- **Switch the positional pair to `scope._attributed_branch`.** Changes their branch-less handling and `?`. Their carry-forward already ignores sidechain tags. Fails row4, row24.
- **Behavioral tests only.** They don't catch a future literal read. Fails row17 over time.

For the foundation (row 51), the alternatives from the Fable consult and round 2:
- **Corpus-seam drop of sidechain records whose `cwd` lies outside the repo.**
  - It changes unfiltered totals for every consumer.
  - It needs a repo identity that only `--this-repo` has.
  - It discards spend the session really incurred.

  Fails row41, row52, row58.
- **Corpus-seam repo tag or per-record repo identity.**
  - It resolves historical `cwd` paths through the filesystem or git inside the corpus layer, and removed worktrees or moved checkouts break that.
  - It rests on unverified harness `cwd` semantics for subagents.

  Fails row41, row52, row62.
- **A `cwd` gate on the pre-branch remap (the previous design).** It closes only the path the remap opened, leaves literal matches on shared names, and adds a `cwd` set to the context. Fails row37, row51.
- **Timeline step only for sidechain records tagged with a default-branch candidate.** It keeps published-figure shifts to the reported gap. But it keeps literal matches on shared non-candidate names, and `cost` keeps diverging from the positional consumers. Fails row37, row40, row51.
- **Fall back to the sidechain record's own tag when the timeline is empty.** It reads the tag the design stops trusting. Fails row51.
- **Leave a timestamp-less main thread to `None`.** It forces rewrites of fixtures that model the ordinary case, for no effect on real data. Fails row53, row54.

No new module is added: `scope.py` already holds `_branch_filter`, and every adopter already imports `scope`.

**Rule specification (M2, M3, M10):**
- `_names_no_pr_branch(branch)` is true for `"HEAD"` and for any branch starting with `_WORKTREE_AGENT_BRANCH_PREFIX`.
- `_pre_branch_run(branch_index)` skips entries whose branch passes `_names_no_pr_branch`.
  - It returns `None` when no entries remain or the first remaining entry's branch is not a candidate.
  - Otherwise it returns `_PreBranchRun(successor_branch, boundary_ts)` from the first remaining non-candidate entry, or `None` if there is none.
- `_attribute_pre_branch(branch, ts, run)` returns `run.successor_branch` only when all of these hold: `run` is not `None`, `branch` is a candidate (exact string equality), `ts` is not `None`, and `ts < run.boundary_ts`. Otherwise it returns `branch` unchanged. `""` and `None` are never remapped.
- `_session_branch_context(records)` returns `_SessionBranchContext(branch_index, index_timestamps, pre_branch_run, untimed_main_branch)`. It is built from the session's records before any `--since`/`--until` filter.
  - `index_timestamps` is the list of `branch_index` timestamps, in index order.
  - `untimed_main_branch` is the first non-empty `gitBranch` on a main-thread record, in file order, or `None`. It is consulted only when `branch_index` is empty.
- `_timeline_entry(rec, ctx)` returns `(branch, ts)`:
  - **Main-thread record:** `(raw or None, rec_ts)`. A main-thread `worktree-agent-*` tag stays raw (row 31).
  - **Sidechain record, non-empty index:** position `bisect.bisect_right(ctx.index_timestamps, rec_ts) - 1`, or position 0 when that is negative or `rec_ts` is `None`. It returns that entry's `(entry_branch, entry_ts)`. This is the last entry with `entry_ts <= rec_ts`, the same entry the old linear scan chose, including among tied timestamps.
  - **Sidechain record, empty index:** `(ctx.untimed_main_branch, None)`.
  - A sidechain record's own `gitBranch` is never read.
- `_attributed_branch(rec, ctx)` returns `_attribute_pre_branch(*_timeline_entry(rec, ctx), ctx.pre_branch_run)`.
- `_attribute_carried_branch(carried, rec, ctx)` returns `_attribute_pre_branch(carried, rec_ts, ctx.pre_branch_run)`. The record's own timestamp is used, so a positional record with no timestamp is never remapped.
- `_pre_branch_moved(rec, ctx)` is true when `_timeline_entry(rec, ctx)[0]` differs from `_attributed_branch(rec, ctx)`. It counts only moves made by the pre-branch step, never a sidechain record whose own tag merely differs from its timeline branch.
- M10 counts an assistant turn with a usage block when all of these hold:
  - it passes the `--since`/`--until` window;
  - `_pre_branch_moved` is true;
  - its attributed branch is in the filter set, and its timeline branch is not.

  Unpriced turns count toward turns but not dollars. A filter that holds both a default branch and its successor therefore prints nothing for that session.
  - The line is `cost._PRE_BRANCH_MOVED_LINE`, a `str.format` template with `{turns}` and `{dollars}` fields. Dollars render the way the report renders its other dollar figures.
  - It sits after the `--share-only` return (`cost.py:1037`), inside the `not summary_mode` diagnostic block (`:1062`), and prints nothing at zero.

## Critical files

There are two sequential `code-writer` dispatches, two commits, and one PR. Dispatch 2 consumes Dispatch 1's `scope.py` names, so they cannot run in parallel. Each commit is coherent on its own: the commit that changes published figures also carries the rule block and the CHANGELOG entry, and Dispatch 1's doc wording is scoped to the rule block's adopter list. Dispatch 1 starts only after the engineer's go on Verification 1. Both dispatches verify with Verification steps 2–3.

Order of work in each dispatch:
1. **Write the dispatch's new tests, then confirm each fails against current code for the intended reason.** Helper unit tests fail on the missing `scope` names. Wiring and end-to-end tests fail on the literal-tag result, not on an import error. A "stays" case (for example `ts == boundary_ts`) may pass already, but its sibling "moves" case must fail.
2. Make the production change.
3. **Run Verification 2.** The failing existing tests must equal that dispatch's named row-54 set exactly:
   - Dispatch 1: `test_non_first_session_that_is_subagent_only_contributes_zero_startup_burn`.
   - Dispatch 2: `test_branch_filter_composes_with_include_subagents`, `test_this_repo_discloses_attested_main_thread_branch_but_not_sidechain_only_branch` and `test_this_repo_cross_account_attestation_independence`.

   If any other existing test fails, stop and raise it rather than updating it.
4. Rewrite the named tests as listed under the dispatch, and make the listed text-only and deletion edits.

Every new fixture that needs a timeline uses explicit, strictly increasing timestamps. A subagent-file record built with `_priced` or `_user_msg` does not carry `isSidechain`, so every new sidechain fixture sets `rec["isSidechain"] = True` explicitly. New test files use relative `.conftest` imports and `from helpers import REPO_ROOT`.

**Dispatch 1: attribution core, cost family, rounds, spawns, derived adopters, rule docs.**

Production:
- `claude/.claude/scripts/transcript_analysis/scope.py`: add M1, M2 and M3 per the rule specification, next to `_branch_filter` (`:898`).
  - `_DEFAULT_BRANCH_CANDIDATES` carries `# Mirrors _lib_default_branch_or_guess's candidate list in claude/.claude/hooks/_lib.sh.`
  - Import `bisect` and `NamedTuple`, and reuse the imported `_parse_ts`.
- `claude/.claude/scripts/transcript_analysis/cost.py`:
  - Delete the moved or replaced names.
  - Build `scope._session_branch_context` at `:142`, `:272` and `:820` (still only under `--branches` at `:820`), and call `scope._attributed_branch` at `:153`, `:212` and `:850`.
  - `_first_main_thread_turns_dollars_by_branch` takes the context.
  - Add `_PRE_BRANCH_MOVED_LINE` and the M10 line.
  - Update the docstrings and comments at `:109-136`, `:187-203`, `:513`, `:560` and `:818-819`. The `:116-121` `None` description becomes "no branch-bearing main-thread record in the session, or a main-thread record with no `gitBranch`".
- `claude/.claude/scripts/transcript_analysis/review_rounds.py`: apply M4 at `:140-170`, and update the module docstring (`:14-18`) and the function docstring.
- `claude/.claude/scripts/transcript_analysis/subagent_mix.py`: call `scope._attributed_branch(rec, ctx) or ""` at `:182` and `:352`, building the context once per session.
  - Keep the call `scope.`-qualified, which `test_transcript_package_module_prefixes.py` requires.
  - Update the docstrings at `:52-64` and `:336-343`. No `--branches` consumer reads a subagent's own tag any more.
- `claude/.claude/scripts/transcript-analysis.py`: in the import block only, drop `:94` and `:103`, and change "eleven" to "nine" at `:88`.
- `evals/review_bench/mine_review_rounds.py`: unchanged (M4 keeps its call valid).

Tests:
- New `claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py`, helper unit cases:
  - **Pre-branch step:**
    - main→X; main→X→main (the trailing `main` stays); main→X→Y; main→develop→X (both candidate entries fold into X); a session starting on a feature branch; a main-only session; `master` and `develop` prefixes;
    - `HEAD` before or inside the run; a main-thread `worktree-agent-*` entry, never the successor and kept raw;
    - a main-thread `main` record with no timestamp stays; a record at `ts == boundary_ts` stays;
    - near-miss names `main-x`, `develop/foo`, `Main` and `mainline` are not candidates;
    - a same-timestamp tie between the first two main-thread entries, pinning the branch-name tie-break of `index.sort()`.
  - **Timeline step:**
    - a prefix sidechain record tagged `main` resolves to X;
    - a post-boundary sidechain record tagged `main` (an architect still running after entry) resolves to X;
    - a sidechain record tagged `other` while the main thread is on X resolves to X;
    - on main→X→Y, a sidechain record tagged X and timestamped after the Y entry resolves to Y;
    - a sidechain record timestamped exactly at two tied main-thread entries resolves to the later-sorted one, as the old linear scan did;
    - a sidechain record with no `gitBranch` resolves to the main thread's branch;
    - a sidechain record with no timestamp falls forward to the first entry, and to X when that entry is in the prefix;
    - `worktree-agent-*` records behave as before, in and after the prefix;
    - no branch-bearing main-thread record gives `None`;
    - branch-bearing main-thread records that all lack timestamps give the first main-thread branch in file order.
  - **Index purity:** a sidechain record with a non-candidate foreign tag, timestamped between main-thread `main` and X, is neither an index entry nor the successor.
  - `_attribute_carried_branch` for timestamped and timestamp-less records.
  - `_pre_branch_moved`: true for a remapped main-thread record and a remapped prefix sidechain record; false for a post-boundary sidechain record tagged `main` that resolves to X.
  - The M14 pin: parse the `for candidate in … ; do` line inside `_lib_default_branch_or_guess` and compare it to `_DEFAULT_BRANCH_CANDIDATES`.
- `claude/.claude/scripts/tests/test_transcript_cost.py`:
  - **`--branches X` end to end:**
    - main-thread and sidechain prefix records count toward X;
    - a post-boundary sidechain record tagged `main` counts toward X, and a sidechain record tagged `other` during X counts toward X and not `other`;
    - a main-thread record with no `gitBranch` stays excluded;
    - a non-`worktree-agent-*` sidechain record in a session with an empty main file is counted unfiltered and excluded from every `--branches` total.
  - **Cross-repo subagent spend (row 63), `cost --this-repo --branches X --summary`.** Call `_mod._cost_report(_cost_args(summary=True, this_repo=True, branches="X"), ...)` with `getcwd` monkeypatched to `/repo/main`, as `test_summary_emits_nothing_identifying_and_excludes_cross_repo_spend` does.
    - Main thread: `main` then `X`, explicit strictly increasing timestamps.
    - One split subagent file holds one sidechain record, with `rec["isSidechain"] = True` and `rec["cwd"] = "/elsewhere/other-repo"` set explicitly, a foreign non-candidate `gitBranch`, a timestamp inside X's stretch and a distinctive priced amount.
    - **Allow path.** The extracted grand total equals main-thread dollars plus the sidechain dollars. The same fixture with the sidechain `cwd` inside `/repo/main` gives an equal total, so a gate in either direction, or one that treats a missing `cwd` as inside the repo, fails.
    - **Deny path.** A second session under a different project directory, on `X`, with a sidechain record whose `cwd` is `/repo/main`, adds nothing to the total.
  - **M10.** Build every expected line from `cost._PRE_BRANCH_MOVED_LINE`:
    - The line appears only in the full report, and only when nonzero.
    - It counts a main-thread prefix turn and a sidechain prefix turn. An unpriced moved turn counts in turns but not dollars.
    - Under `--since` starting inside the prefix, it counts only in-window moved turns.
    - No line prints under `--branches main` or `--branches main,X`.
    - **Absence under `--summary` and `--share-only`.** Run both on a fixture where the full report prints a nonzero line. Then replace every digit run with a placeholder, and require the `--summary` output's lines to equal those of the same fixture with the prefix removed.
  - **`--since`:**
    - On main→X→main→Y with the window starting between X and the second `main`, the second `main`'s turns stay on `main`. A context built from window-filtered records would wrongly remap them to Y.
    - With the window starting inside the prefix, only in-window prefix turns count, and they land on X.
  - Text only: update comments and docstrings that name the moved helpers at `:2044` and `:3526-3529`.
- `claude/.claude/scripts/tests/test_transcript_pr_cost.py`:
  - **Conservation test.** On a fixture with a prefix and divergent sidechain tags, the all-branch total plus `unbranched_totals` equals the fixture's per-record dollars summed independently of `pr-cost` (each `_priced` record's known cost).
  - Add an agreement test: `cost --branches X --summary` dollars equal `branch_totals["X"]`.
  - Add a case: a sidechain record in an empty-main session lands in `unbranched_totals`.
  - Keep `:74-94`. Text only: update `:46` and `:100`.
- `claude/.claude/scripts/tests/test_transcript_workstream_cost.py`:
  - A non-first prefix session's startup burn includes its prefix turns, and the session adds nothing to `main`'s session count.
  - Row-54 rewrite of `test_non_first_session_that_is_subagent_only_contributes_zero_startup_burn`: replace sess-2's empty main file with one main-thread `_user_msg` on `feature-k`, timestamped before the subagent record, and update its docstring premise. All three assertions stay. Add a sibling case where sess-2's main file is empty: `feature-k` reports one session and $1.00.
  - Text only: update `:132`.
- `claude/.claude/scripts/tests/test_transcript_handoff_signal_response.py`: `_startup_burn_benchmark(cost._compute_workstream_dollars(...))` over a continuation session that starts on `main` and then enters X equals the hand-computed value.
- `claude/.claude/scripts/tests/test_transcript_review_rounds.py`:
  - A prefix round is keyed to X in both `compute_review_round_costs` and `compute_review_round_counts`, and its window stays X past the boundary.
  - An inline prefix sidechain record outside any window, carrying `main`, is remapped to X by its own timestamp.
  - Fix the stale docstring at `:483`.
- `claude/.claude/scripts/tests/test_transcript_cost_counts.py`: a prefix round and a prefix spawn count under `--branches X`.
- `claude/.claude/scripts/tests/test_transcript_subagent_mix.py`: a prefix dispatch's row lands under X.
- `evals/test_review_bench_mining.py`: on a session opened on `main` (round) and then X, `_collect_round_entries` gives that round `branch == X`. Use the file's `_assistant`/`_user` builders with explicit `ts`.

Docs:
- `docs/transcript-analysis.md`:
  - Rewrite `:669` as **Subagent attribution.** It says that in every subcommand the next block lists, every sidechain record, isolated or not, takes the dispatching main thread's branch at its timestamp. It covers:
    - the fall-forward;
    - the file-order fallback;
    - `?` for a session with no branch-bearing main-thread record;
    - same-session scope;
    - a subagent's own branch name never becomes a label, and its spend counts toward its dispatching session's branch whatever its own tag or `cwd`.

    Update `:647`'s pointer to the new name.
  - Rewrite `:47`'s last sentence. The fallback recovers scope, not branch attribution.
    - Every subcommand the rule block lists attributes a subagent record to its dispatching session's main-thread branch.
    - A subagent that worked in this repo from another repo's session therefore counts under that session's branch, so an all-zero result for this repo's branch is expected there, and it is still not proof of zero spend.
    - A subagent this repo's session dispatched into another repo counts toward the dispatching branch here.
    - Subcommands outside the list still filter on a subagent record's own `gitBranch`, which can disagree with its `cwd`'s checkout, so an all-zero result from them is inconclusive.
  - Add a bold-led **Pre-branch default-branch attribution.** block right after it, in the same form. It covers:
    - the rule, candidates, skipped entries, boundary, single successor, first leading run, and conservation;
    - the adopter list, as the only list, holding the Dispatch 1 adopters plus `pr-cost`, `workstream-cost`, the `handoff-signal-response` benchmark and review-bench mining;
    - which adopters the subagent step reaches;
    - the positional consumer (rounds) and the raw-tag exceptions;
    - M10's line, including that it counts only turns moved into the filter set from outside it;
    - **effects:**
      - startup burn now includes a continuation's cold-start turns, so `workstream-cost` burn share, sessions per branch and the benchmark move;
      - `review-round-cost --pooled`'s branch units move;
      - a subagent still running after a branch switch now follows the switch;
      - a subagent record with no tag of its own now takes the main thread's branch;
    - **limits**, each as a bullet:
      - a default branch outside the candidates gets no remap;
      - unrelated prefix work is folded in;
      - later default-branch stretches stay;
      - main-thread records with no timestamp stay;
      - planning done in a separate session that stays on the default branch is not moved;
      - a session with no branch-bearing main-thread record attributes its subagent records to `?`;
      - a subagent this session dispatched counts toward the dispatching branch whatever repo it worked in; the `Scope:` caption's "this repository only" names the sessions read, not each subagent's working directory;
      - inside a review round's window the round's key governs every record;
      - rounds price a whole dispatch at its spawn record's branch, while per-record consumers split a subagent that straddles a switch;
      - `review-trace` renders `?` where per-record consumers fall forward;
      - a session that moves its working directory into another repo can name a foreign branch as the pre-branch successor, so the leading run goes to that name and this PR's branch loses it, and a foreign-repo default-branch stretch followed by a branch here is credited to this PR.
  - Add a pointer at `:647`.
  - Add a whole-session/literal-tag note at `:390`.
  - Add adoption pointers at `:235`, `:1146` and `:1441`.
  - At `:1129-1132` (workstream-cost) and `:1598` (handoff-signal-response), add that these figures follow both blocks, and that runs predating them are not comparable with runs that apply them. Do not edit `docs/case-studies/*`.
  - Amend `:1447`: both models apply the block, and they still differ only on a round opened on a record with no `gitBranch`.
  - Keep `## review-round-cost`'s `**Small-pool residual` text, which `test_transcript_review_rounds.py` reads.
- `docs/transcript-analysis-architecture.md`: add the helpers to the `scope.py` section (`:66-72`), and rewrite `:154-159` to name `scope._attribute_carried_branch`.
- `docs/pr-cost.md`:
  - Point `:24` at the blocks.
  - Add a `### Pre-branch and subagent attribution` subsection beside "Comparing rows across rate stamps". It says:
    - **What a row follows.** Once the capturing checkout has pulled this change, a row follows both blocks in `docs/transcript-analysis.md`: same-session pre-branch spend counts toward the branch, and every subagent record counts toward the branch its dispatching main thread was on at that record's time, whatever repo the subagent worked in.
    - **A figure can rise or fall.**
      - The pre-branch step raises the figure of the branch a session enters and lowers a `--branches main` figure. Its shift is expected in `plan_file_added` rows and in `opus_dollars`/`opus_dollar_share_pct`.
      - The subagent step moves three cohorts:
        - a subagent record whose own tag differs from its dispatcher's branch, for example one still running after a switch, moves to the dispatcher's branch;
        - an untagged subagent record moves from unbranched to the dispatcher's branch;
        - a session with no branch-bearing main-thread record moves its subagent spend from the subagents' own tags to unbranched.
    - **Cutover.** It is per checkout at pull time, and no column records which rule a row used.
      - A row whose `captured_at` precedes this change's merge into the default branch is old-rule on every machine, unless its capturing checkout was on this PR's branch.
      - A later row is old-rule on any machine that had not yet pulled.
    - A pre-cutover row is neither a lower nor an upper bound on its new-rule figure.
    - **Comparing dollars across the cutover.** First re-capture the pre-cutover row with `--record --force --pr N` inside `cleanupPeriodDays`; the post-cutover row needs no re-capture. Outside that window, exclude the row from cross-cutover dollar comparisons.
    - An open PR's Cost section changes at its first sync after the pull.
  - At `:255` and `:310`, add that a pooled or combined total spanning this cutover carries the same comparability label as a rate-stamp span, pointing at the new subsection.
  - At `:257`, add that for an attribution-rule change such as this one, the side of the change is set by the source ledger's `captured_at` against the capturing checkout's pull date, not by `merged_at`.
- `CHANGELOG.md` `[Unreleased]` → Changed:
  - `--branches` and per-branch rows, filtered or not, now credit a session's leading default-branch records to the first branch that same session enters. This applies to the cost family, round attribution, spawn counts and the startup-burn benchmark, and the rule block in `docs/transcript-analysis.md` holds the full list. Planning in a separate session is not moved.
  - The cost family now attributes every subagent record to the dispatching main thread's branch at that record's time, never its own `gitBranch`. A subagent still running after a branch switch follows the switch. A dispatched subagent's spend counts toward the dispatching branch whatever repo it worked in, and a subagent's own branch name never appears as a label. A session with no branch-bearing main-thread record leaves its subagent records unbranched.
  - PR Cost sections and new `pr-cost --record` rows follow both changes, so a figure can rise or fall. Cutover is per checkout at pull time, and an open PR's Cost figure changes at its next sync. Existing rows are not re-recorded. See `docs/pr-cost.md` § "Pre-branch and subagent attribution" for which rows and columns shift.

**Dispatch 2: remaining per-record consumers, structural test, agreement test.**

Production:
- `claude/.claude/scripts/transcript-analysis.py`: call `scope._attributed_branch(rec, ctx) or ""` (bare-imported through the existing `transcript_analysis.scope` block) at these sites, so `None` takes each site's existing no-`gitBranch` path:
  - `cmd_buckets` `:356`
  - `cmd_struggle` `:491`
  - `cmd_user_input` `:667`
  - `cmd_duration` `:851`
  - `cmd_judgment_pair` `:934`
  - `cmd_skill_invocation` `:1141`
  - `cmd_skill_pair` `:1305`
  - `cmd_pr_link` `:1404`
  - `cmd_fail_seq`, for its filter and `branch_runs` key only (`:410` and the `:416-420` reset stay raw)

  Build the context once per session, before any per-record window filter. Update the docstrings at `:891-894` and `:1088`. Leave `cmd_skill_invocation`'s OUTPUT INVARIANT (`:1095-1107`) and its session-iterator selection untouched.
- `claude/.claude/scripts/transcript_analysis/subagents.py`: at `:128-131` and `:141-144`, the row key and filter use `scope._attributed_branch(rec, ctx) or ""`. The attestation set keeps adding the raw branch from main-thread `assistant` and `user` records only. Rewrite the docstring (`:40-58`) and the `:97-100` comment. A sidechain record's turns print under its dispatching main-thread branch, so every printed branch was recorded by a main-thread record. The gate stays and fails closed to the opaque label for a branch no main-thread `assistant` or `user` record carried.
- `claude/.claude/scripts/transcript_analysis/review_trace.py`: at `:296`, `evt_branch = scope._attribute_carried_branch(last_branch, rec, ctx) or "?"`, with the context built once per `_review_trace_session_events` call before the date filter. Update the docstrings at `:233-239` and `:641-647`.

Tests:
- `claude/.claude/scripts/tests/test_transcript_analysis.py`: one prefix case per shim adopter, plus:
  - `duration` Span includes the prefix.
  - `buckets`: X's Date range starts at the prefix, and the `main` row's Sess and turn counts drop.
  - `user-input`, unfiltered, with a branch-less prompt: its label matches today's (no `None`).
  - `user-input --branches X` on a prefix session counts the prefix's first prompt as X's initial prompt.
  - `fail-seq`:
    1. A prefix test-run `tool_use` with its `tool_result` also in the prefix counts under X.
    2. A `tool_use` on the last `main` record with its `tool_result` on the first X record is dropped by the raw reset.
  - `skill-invocation --include-subagents`:
    - a prefix sidechain invocation, a post-boundary sidechain invocation tagged `main`, and a `worktree-agent-*` invocation count under X;
    - on X→Y, a sidechain invocation timestamped in the Y stretch counts under Y whatever its own tag.
  - `skill-pair` counts a `worktree-agent-*` follower and a foreign-tagged sidechain follower under X.
  - Row-54 rewrite of `test_branch_filter_composes_with_include_subagents`:
    - main-thread records on `wanted` then `other`, with explicit timestamps;
    - the `code-review` sidechain record is timestamped in the `wanted` stretch and the `plan-review` one in the `other` stretch;
    - the assertions stay.
- `claude/.claude/scripts/tests/test_transcript_subagents.py`, using multi-root `--this-repo` fixtures whose main-thread records carry explicit increasing timestamps (the index path):
  - A prefix sidechain record tagged `main` and a post-boundary sidechain record tagged `main` print under X's disclosed label.
  - A sidechain record with a foreign non-candidate tag prints under its dispatcher's disclosed label, and the foreign tag never appears.
  - No `worktree-agent-*` string appears.
  - No opaque `account-<K>/branch-<N>` label appears when every branch the timeline resolves to is carried by a main-thread `assistant` or `user` record.
  - **Deny path.** A branch recorded only by a main-thread record of another type (for example one built with `_hook_deny`), followed by a sidechain record that resolves to it, prints under an opaque label, and the raw name never appears.
  - A session whose sidechain records have no branch-bearing main-thread record contributes no row.
  - Row-54 rewrites, each with timestamped main-thread records:
    - `test_this_repo_discloses_attested_main_thread_branch_but_not_sidechain_only_branch`: assert alpha's sidechain line holds the Sonnet turn, `beta` is absent, and no `account-\d+/branch-\d+` label appears. Rename it to say the sidechain record follows its dispatcher.
    - `test_this_repo_cross_account_attestation_independence`: assert account B's sidechain turn prints under B's own `account-<K>/other` row, account A's `main` row has no sidechain line, and no opaque label appears.
  - Delete `test_this_repo_branch_attestation_collision_residual_folds_unattested_sidechain_into_disclosed_row`. The foreign-tag test above covers its remaining contract.
- `claude/.claude/scripts/tests/test_transcript_review_trace.py`: add new test functions that copy the record shapes of the fixtures at `:480`, `:1007` and `:1052`, each with an event added on a prefix record. The three existing tests stay untouched. Check:
  - prefix events print `branch=X`, including `architect-consult` and a prefix sidechain event;
  - `?` still renders when no main-thread record precedes an event;
  - `--until` set before the boundary still prints `branch=X`;
  - the header follows, and `--branches X` keeps the prefix events. Both run through `cmd_review_trace` with `fake_projects`.
- `claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution.py`: add an agreement class. Its one shared fixture (explicit timestamps; main→X) holds:
  - a prefix `/plan-review` round and an architect dispatch whose subagent file holds a prefix turn, a post-boundary turn tagged `main`, and a turn tagged `other`;
  - one main-thread prefix record placed after the boundary in file order but carrying an earlier timestamp.

  Every record carries a distinct signature, a power-of-two output-token count, so any token or dollar total decodes to a record set.
  - Each consumer gets an adapter that returns the set of record ids it put under X, restricted to the records that consumer can observe. The consumers are the in-process entry points of `cost --branches X`, `pr-cost` branch totals, `workstream-cost`, the `cost-counts` rounds and spawns tables, `review-round-cost`, `subagent-mix`, `buckets` and `review-trace`.
  - Assert that every record observed by two or more consumers falls on the same side of X in each of them.
- New `claude/.claude/scripts/tests/test_transcript_analysis_branch_attribution_structure.py`:
  - `_collect_gitbranch_reads(source, module_key) -> Counter[str]` visits the AST with an explicit class/def stack. It records every `ast.Constant` equal to `"gitBranch"`, except docstring statements, against `<path relative to claude/.claude/scripts>:<qualname>`.
  - Attribution goes to the innermost enclosing def. Lambdas and comprehensions count toward their enclosing def. A class-body read counts toward the class's qualname. A module-level read counts toward `<module>`.
  - An expected table maps each key to a group, an exact count and a one-line reason. The collected Counter must equal it exactly. Keys and counts:
    - helper group: `transcript_analysis/scope.py:_session_branch_index` 1, `transcript_analysis/scope.py:_session_branch_context` 1, `transcript_analysis/scope.py:_timeline_entry` 1;
    - `_POSITIONAL_CARRY_FORWARD`: `transcript_analysis/review_rounds.py:_session_record_branches` 1 and `transcript_analysis/review_trace.py:_review_trace_session_events` 1; each must call `_attribute_carried_branch`;
    - `_RAW_READ_BESIDE_ATTRIBUTION`: `transcript-analysis.py:cmd_fail_seq` 2 and `transcript_analysis/subagents.py:cmd_subagents` 2; each must call `_attributed_branch`;
    - `_RAW_BY_DESIGN`: `transcript-analysis.py:cmd_commit_gate` 1, `transcript_analysis/handoff_nudge.py:_session_matches_rearm_scope` 1 and `transcript-analysis.py:_turn_shape_session_turns` 1.
  - The "must call" check matches both `ast.Name` and `ast.Attribute`.
  - Synthetic-snippet tests feed source text to the collector and assert the qualnames it returns. Cases:
    - nested def, class method, lambda;
    - `rec["gitBranch"]`, a module constant `KEY = "gitBranch"` (reported as `<module>`), `"gitBranch" in rec`;
    - a docstring mention and a help string containing the word are not counted.
  - The module docstring states that the test sees literal reads only. Derived consumers are pinned behaviorally.

Docs:
- `docs/transcript-analysis.md`:
  - Extend the rule block's adopter list (the D2 subcommands, plus `review-trace` as a positional consumer) and its effects:
    - `duration` Span/Active include the prefix;
    - `buckets`' Date range and `main`-row counts;
    - `user-input` labels, and its initial, follow-up and correction counts under X, because the prefix's first prompt becomes X's initial prompt;
    - `skill-invocation --include-subagents` and `skill-pair` count subagent invocations under the dispatching branch.
  - Drop `:47`'s clause about subcommands outside the list, because none remain.
  - Add adoption pointers at `:85`, `:119`, `:145`, `:170`, `:195`, `:319`, `:360`, `:416`, `:457` and `:561`.
  - Rewrite `:202`.
    - The printed branch is the attributed branch.
    - A subagent's turns print under its dispatching main-thread branch, so every printed branch was recorded by a main-thread record.
    - Under `--this-repo` a branch still prints raw only when a main-thread `assistant` or `user` record carried that (root, branch) pair. Otherwise it stays opaque.
  - Point `:465` at the block, keeping `?`.
  - Rewrite the sample at `:471-484`. Its first two events and the header become `my-feature`. The prose explains that the session opened on `main` and that the block credits its opening events to `my-feature`. `models=` still shows per-event model attribution.
  - At `:568`, change "own `gitBranch`" to "attributed branch".
- `CHANGELOG.md`: extend the first Dispatch 1 bullet to "every subcommand the rule block lists". Add these bullets:
  - Per-record consumers outside the cost family now attribute every subagent record, `worktree-agent-*` included, to the dispatching main thread's branch.
  - `/ready-for-review`'s fidelity report (`skill-fidelity-report.sh`) now sees plan-mode skill invocations and architect consults made earlier in the same session on the default branch.

Review surface:
- 7 production files (the shim is touched once per dispatch, in different regions).
- 13 test files, 2 of them new; the agreement class extends a Dispatch 1 file.
- 3 docs files and CHANGELOG.

Risk concentrates in two places:
- `scope._attributed_branch` / `_timeline_entry`, which every adopter shares;
- `review_rounds._session_record_branches`, which round-cost, round-counts and review-bench mining share.

## Verification

The orchestrating session owns step 1 before Dispatch 1, and steps 4–6 after both dispatches and before the PR opens. Each `code-writer` dispatch owns steps 2–3.

**Stays local.** No figure, branch label, prompt text, skill name or event row from steps 1, 4, 5 or 6 enters the plan, a commit or the PR body. Report them to the engineer in chat. Any ledger verdict written back (rows 7, 14, 15, 30, 39, 53, 59, 60, 62, and the 1(h) outcome on row 39) states direction only, with no count or share.

1. **Pre-build census.** The orchestrating session runs it from the worktree before Dispatch 1, while production code still equals the merge-base.
   - Run `subagents --this-repo` for the distinct `gitBranch` values per thread and the sidechain tag distribution. Stop and raise if a detached checkout appears as anything but `HEAD` (row 30).
   - **Scratch script.** It lives outside the repo tree, is never committed, and prints counts and dollars only. It covers every `--this-repo` session:
     - It reads with `corpus._read_session_file_partitioned` for file provenance.
     - It dedups with `pricing.dedup_turns_by_request_id`.
     - It prices with `pricing._price_turn` (`pricing.py:520`).
     - It reimplements the rule specification.

     Items:
     - (a) main-thread timestamp inversions larger than one second in file order, in sessions that start on a candidate and later enter another branch;
     - (b) prefix span and gap to the boundary, for the same sessions;
     - (c) sidechain records whose own tag, empty included as its own split, differs from their timeline branch before the pre-branch step. Split them three ways: inside a leading run; outside one, with the main thread changing branch during that subagent file; and the remainder. Report counts and dollars. The switch split also shows whether a subagent running past a switch kept its dispatch-time tag (row 60);
     - (d) sessions whose sidechain records resolve through the file-order fallback, and sessions whose sidechain records resolve to `None`, with dollars;
     - (e) records in `*/subagents/*.jsonl` that lack `isSidechain: true`, by record type (row 59);
     - (f) sidechain records whose `cwd` is neither the main checkout's root nor under it (which covers `.claude/worktrees/*`), with dollars. This is a lower bound on the cross-repo cohort (rows 58, 62);
     - (g) `plan-it`, `plan-review` and `architect-consult` events that `review-trace --this-repo --branches main` prints, classified as inside a same-session leading run (they move), separate-session planning (they stay) or other (they stay), with dollars;
     - (h) leading runs whose successor entry (the main-thread record `_pre_branch_run` picks) has a `cwd` outside the main checkout's root and `.claude/worktrees/*`, and leading runs whose own records carry a `cwd` outside that tree (a foreign-repo `main` stretch followed by a branch here is folded into this PR). Report counts and the leading run's dollars for each class. This is the main-thread analogue of (f), computed from corpus records only (row 39).
   - **Stop conditions.** Stop and raise on any of: (a); a file-order fallback in (d); nonzero `None`-cohort dollars in (d); a nonzero (c) remainder; any (e); any (f); any (h). A hit on (f) or (h) asks the engineer whether row 63 still holds at that size, and a hit on (h) reopens the rejected `cwd` gate for the pre-branch step only, whichever class hit.
   - Report every item to the engineer, with (g) as the upper bound on what stays on `main`. Dispatch 1 starts only after the engineer's explicit go.
2. From the worktree root, run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
   - Its target list must include `claude/.claude/scripts/tests` (the directory target that collects the new files, `select-tests.py:548`).
   - It must also include the review-bench test glob, because `scope.py` and `review_rounds.py` are review-bench dependencies (`:78-88`, `:711`).
   - To confirm collection, re-run as `select-tests.py --collect-only -q`, with no `--`, since select-tests forwards its arguments to pytest verbatim (`:975-980`, `:1102`). At Dispatch 1, `test_transcript_analysis_branch_attribution.py` must appear. At Dispatch 2, both it and `test_transcript_analysis_branch_attribution_structure.py` must appear.
   - The failing set must match the dispatch's named set (Critical files, step 3).
   - Don't widen the run by hand.
3. Run `../../../.venv/bin/ruff check claude/.claude/ claude-skills/ evals/test_review_bench_mining.py`. CI does not lint `evals/` (`.github/workflows/tests.yml:170`), so only the changed file is checked. No shell file changes.
4. Set up the A/B comparison.
   - Run `git merge-base HEAD origin/main`.
   - In a separate call, run `git worktree add --detach ../pr-cost-merge-base <sha>`, using the literal SHA (no `$(...)`).
   - Run each command below from the feature worktree root, once as `./claude/.claude/scripts/transcript-analysis.py …` and once as `../pr-cost-merge-base/claude/.claude/scripts/transcript-analysis.py …`. The shim imports its sibling package, so the full checkout is needed.
   - If any hook or harness denial occurs in steps 4–6, report it verbatim and skip the remaining A/B steps.
   - Run `git worktree remove ../pr-cost-merge-base` on every exit path from steps 4–6, including a stop or a denial.
5. Local A/B, both copies:
   - **Turn conservation:** total turns summed across `buckets --this-repo` rows are identical.
   - **Dollar conservation:** compare `cost --this-repo --branches <S>` grand totals, where S is every branch label either copy's `workstream-cost --this-repo` prints.
     - If step 1(d) reported no `None` session and step 1(c) reported no untagged subagent record, the totals must be identical. Otherwise report the difference beside those counts.
     - If the labels print redacted, say so and rely on the unit conservation test.
   - **M10:** pick a branch X with a prefix session and zero step-1(c) records.
     - The worktree copy's full report for `cost --this-repo --branches X` prints the M10 line.
     - X's dollars exceed the merge-base figure by that line's dollars, within one cent of rounding.
     - On `cost --this-repo --branches main`, the worktree copy's dollars fall and no M10 line prints.
     - If no such X exists, report the mismatch with the (c) count.
   - **Agreement:** on the worktree copy, `workstream-cost`'s total for X equals `cost --this-repo --branches X` dollars.
   - **`worktree-agent-*` rows:** `subagents --this-repo` shows no `worktree-agent-*` row from a sidechain record on the worktree copy. A main-thread one is row 31's case; report it.
   - **Startup burn:** compare `workstream-cost --this-repo`'s burn share and `handoff-signal-response`'s benchmark across the two copies.
   - **Residual and row 14:**
     - On the worktree copy, confirm that the `plan-it`, `plan-review` and consult events left in `review-trace --this-repo --branches main` match step 1(g)'s "stay" classes.
     - For every prefix X, read the prompts that `user-input --this-repo --branches X` adds on the worktree copy, to judge whether the prefix is setup for X.
6. Fidelity path, both copies, on the same X: run `skill-invocation --branches X --include-subagents` and `review-trace --this-repo --branches X` (`skill-fidelity-report.sh:16,22`).
   - The worktree copy shows the plan-mode `plan-it` invocation and the `architect-consult` row that the merge-base copy omits.
   - `?` still appears for any event with no preceding main-thread record.
   - This PR's own `/ready-for-review` then runs the fidelity reviewer on the changed input.

## Out of scope

- **Switching `review-trace` or round attribution to `scope._attributed_branch`.** Their carry-forward already ignores sidechain tags. Switching would change their handling of branch-less records and `?` (rows 4, 24, 40).
- **Attributing a subagent by its dispatch-time branch.** That would link each sidechain record to its spawn for every consumer. The record-time timeline is the rule `cost` already applied to `worktree-agent-*` records. The resulting difference from rounds is a documented residual.
- **A same-repo check on main-thread records.** A main-thread tag is the session's own checkout (G1). The timeline step already eliminates literal `--branches` matches on shared names for sidechain records. G1 does not establish that a session's checkout stays in this repo. If a session started here moves its working directory into another repo, the pre-branch successor can carry that repo's branch name, and a foreign `main`, `master` or `develop` extends the leading run by name. The leading run then goes to the foreign branch name, so this PR's branch loses it and `--this-repo` reports print a branch with no checkout here. The reverse variant also holds: a foreign-repo default-branch stretch followed by a branch here is credited to this PR. Among main-thread records, only leading-run records change under this PR, because records after the move already count under the foreign tag. This is a documented limit in the rule block, and Verification 1(h) sizes it locally.
- **A `cwd` gate as a disclosure control.** It cannot see a main-thread read of another repo, which the corpus already holds. Row 63 covers main-thread and subagent work that contributed to the PR.
- **Excluding a dispatched subagent's spend by its `cwd`.** The engineer chose to count it (row 58). A recorded `cwd` is also a leaky repo signal (row 62).
- **Making the `subagents` attestation set match the timeline index's record types.** The narrower set fails closed, and a deny-path test pins it (row 61).
- **Attributing whole-session filters and sequence boundaries.** Rows 25, 26.
- **Default-branch stretches after the session has left the default branch.** They could be wrap-up or setup.
- **Planning done in a separate session that stays on the default branch.** It has no same-session successor. This is a documented limit, and Verification 1(g) sizes it.
- **Repos whose default branch is not `main`, `master` or `develop`.**
- **A `--summary` sentence, a `Scope:` caption change, or a separate disclosed dollar line.** Rows 11, 12, 38.
- **A ledger column that records the attribution rule.** It would force another header migration right after `model_breakdown` (`docs/pr-cost.md:35`). Per-checkout cutover and the one-sided classification rule are documented instead (row 45).
- **Regenerating existing PR bodies and re-recording existing ledger rows.** Rows 20, 21. `--record --force --pr N` stays available for a manual re-capture.
- **A time-gap bound on the prefix.** No grounded value exists.
- **Editing `docs/case-studies/*`.** These are preserved records. The instrument sections carry the non-comparability note instead.
- **`_lib.sh`'s second candidate copy (`:4304`).** Pre-existing duplication inside `_lib.sh`.
- **`claude-skills/skills/transcript-analysis/SKILL.md:86`'s "`gitBranch` string only" wording.** The attributed value is always a `gitBranch` string that a main-thread record of the same session carried, so "by `gitBranch` string only, never by project dir or root" stays true. The line's point, same-name pooling across accounts, is unchanged. An edit would need a `/skill-review` pass and would change nothing a reader acts on.
- **`claude/.claude/agents/skill-fidelity-reviewer.md` and `post-crash-sessions.py`.** Each stays true as written, or is not a `--branches` consumer.