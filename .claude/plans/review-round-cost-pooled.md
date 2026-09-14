# review-round-cost: `--pooled` render mode

## Context

Add a `--pooled` render mode to the `review-round-cost` subcommand
(`claude/.claude/scripts/transcript_analysis/review_rounds.py`) so that
future cost-share write-ups in `docs/cost-levers-considered.md` can cite
tool output directly instead of hand-summed arithmetic across
branches/accounts. Why now: the prior phase (PR #927, merged, commit
`7606de3c`) shipped the first cross-machine `review-round-cost` run and
produced a hand-summed, imprecise 10-15% figure in
`docs/cost-levers-considered.md` (line ~565) that this mechanism is
meant to eventually let the repo replace with real tool output (that
replacement is a later phase, out of scope here). A peer PR, merged
into `main` during this branch's later sync onto it, has since redacted
that same entry's own raw branch/round count and a sibling before/after
dollar figure elsewhere in the same doc, replacing both with
ratio-only phrasing — consistent with this plan's own no-absolute-figure
design choice, and neither redacted figure is reproduced here (see row 5
and the Out of scope section for the current state of each). Intended
outcome: a `--pooled` flag that computes and prints
cross-account pooled Cost-share and round-count figures compliant with
`docs/private-project-redaction.md`'s "Publishing a pooled tooling
measurement" section, replacing the ad hoc hand-summing.

## Approach

Add `--pooled` to `review-round-cost` as a **dedicated render path that emits percentages and nothing else** — no dollar amount, no raw count, no per-account or per-branch split — computed by summing `compute_review_round_costs`'s existing return value across its `(root_idx, branch)` keys, with a branch-cluster percentile bootstrap giving every share a 95% CI. The flag refuses every scope-narrowing flag at two layers (CLI boundary and inside the render function), suppresses the `DO NOT PUBLISH` banner that the per-round table needs, and prints an in-band pointer to the redaction doc's human approval gate. `compute_review_round_costs` itself is not touched.

The design's load-bearing choice is that the pooled block emits **no absolute figure of any kind**. That single rule discharges the redaction doc's composition bar mechanically rather than case-by-case: with no dollar rate and no raw count in the block, nothing it prints can be multiplied by anything already published — including a raw round or branch count published alongside a rate — to reconstruct a raw pooled total. (The raw round count this rule was checked against at plan-review time, at `docs/cost-levers-considered.md:549`, was itself redacted by a peer PR during this branch's later sync and is not reproduced here; see the Context section's note.) It also makes the enforcing test a single grammar assertion rather than a per-figure judgment call.

Alternatives set aside: (a) a `--summary`-style block that also prints median `$` per round — rejected, because a permitted per-round rate composes with that already-published round count into the raw pooled total `docs/private-project-redaction.md:132-133` bars outright; (b) mirroring `cost --summary`'s single-root requirement — rejected, because the figure this exists to produce is machine-wide across every declared account (`docs/cost-levers-considered.md:552-554`), so a single-account requirement would make the feature unable to compute the thing it was asked for; (c) sharing a refusal-policy helper with `cost --summary` — rejected, because the two policies differ in the root-count direction (summary refuses multi-root, pooled requires it), so one helper would be an abstraction over two different rules.

### Cross-machine reporting (engineer-confirmed this session)

A single `--pooled` invocation can only pool the scan roots reachable
from the machine it runs on (see given G4 below); it deliberately emits
no raw dollar weights, so a correct hand-combination of two machines'
shares into one true pooled figure isn't mathematically possible from
this output alone. This design's own answer is: report each machine's
share separately (with its own CI, or as a range across machines) —
consistent with existing practice already in
`docs/cost-levers-considered.md:541-543`.

Two coordination checks were run this session, both by direct message
to the named peer session. `claude-config/pr-cost-forensics` replied
that it is not building a cross-machine merge mechanism — its actual
scope is a redaction fix on an unrelated case study, `--this-repo`-scoped
throughout. `claude-config/cross-account-aggregate-caveat` replied that
it is also not — its scope is `docs/private-project-redaction.md`'s
pooling rules and a couple of skill-review/hook fixes. `[verified:
cross-session replies from both peer sessions, this session]` The
engineer separately confirmed that some other, unidentified branch *is*
building a cross-machine merge mechanism, and confirmed this design
should not block on or coordinate with it: G4's per-invocation
constraint means `--pooled`'s own per-machine output is correct
regardless of what that mechanism eventually does with it, so no
coordination is required for this plan to proceed. `[engineer-confirmed
this session]`

### Assumption ledger

**Root problem.** Every published figure from `review-round-cost` today is hand-summed from output that prints one dated, dollar-valued row per round per branch (`review_rounds.py:550-562`), so the command is not itself an aggregation boundary in the sense `docs/private-project-redaction.md:106-109` requires, and any figure taken from it inherits the private half of the corpus's composition.

**Givens** (fixed beyond this design's reach):

- **G1.** The redaction doc's two closed lists and its human approval gate are the owner's policy, and the gate is purely a human sign-off with no programmatic component anywhere in the doc — this plan may satisfy the policy but cannot widen or automate it. `[verified: docs/private-project-redaction.md:111-169, 333-354]`
- **G2.** `compute_review_round_costs`'s round-window detection semantics and its `{"rounds", "branch_totals"}` return shape are fixed by the existing contract and its tests; `--pooled` is a consumer of that output. Changing detection is a different plan. `[verified: review_rounds.py:259-392; docs/transcript-analysis.md:1068-1074]`
- **G3.** `review_rounds.py` must not import `cost.py` — a documented module invariant, so `cost._LIST_PRICE_CAVEAT` / `_LIST_PRICE_CAVEAT_ALERT` are unavailable here and dissolving that boundary is a separate architectural decision. `[verified: review_rounds.py:14-18 module docstring; import line at :29]`
- **G4.** One invocation pools only the scan roots reachable from the machine it runs on (`scope.resolve_scan_roots` = `PROJECTS_DIR` + declared roots). Cross-*machine* pooling is outside any single command's reach. `[verified: scope.py:332-366]`
- **G5.** The transcript toolkit runs on the stdlib alone — no numpy/scipy/pandas import exists anywhere under `claude/.claude/scripts/`, so the bootstrap is hand-written. `[verified: grep for numpy|scipy|pandas under claude/.claude/scripts/ returns nothing; only `random` and `statistics` appear, at transcript-analysis.py:17,22]`

**Rows.**

1. A "review round" is analogous to "agent dispatch" for `docs/private-project-redaction.md:115-116`'s closed countable list, making round-count a permitted countable unit — consistent with the "Own-history counts" section, which explicitly excludes a `transcript-analysis.py` measurement of tool calls, sessions, dispatches, dollars, or duration from its own-history exemption, keeping "review round" inside the mixed-corpus machinery this plan builds rather than sliding it into that exemption. `[engineer-verified; verified: docs/private-project-redaction.md:115-116, 265-270]`
2. Round-count is reportable only as a share or a median, never as a raw total — stricter than the doc's own counts bullet (`:122-124`), because a raw round count composes with a permitted per-round rate. `[engineer-verified]`
3. A branch is declined as a countable or reportable unit entirely (≈1:1 with a delivered PR/task, the reasoning that already bars a Duration share). Consequence the implementer must not miss: the existing footer's `Mean rounds per branch` line has **no pooled counterpart** and must not be carried over in any form, mean or median. `[engineer-verified]`
4. Every pooled share the block emits carries a bootstrap confidence interval as its sample-size disclosure — not a raw count. `[engineer-verified]` CI width alone does not let a reader recover the exact branch count: width is jointly determined by branch count *and* by the variance/skew of per-branch dollar shares, so two pools of equal size but different composition produce different widths, and inverting width back to count needs information (the variance) the reader doesn't have — unlike a raw count, which a reader recovers exactly. The residual coarse trend a reader could still notice across repeated citations (narrower interval over time ⇒ pool grew) is bounded by two controls this design doesn't need to add: the "No time series" standing bar (`docs/private-project-redaction.md:176-184`) means no compliant publication ever shows two dated citations side by side in one artifact, and the approval gate's own requirement that a proposal name "any prior publication of the same or a composing statistic" (`:384-388`) puts a second citation of the same share in front of the human approver before it ships — the approver, not a mechanical rule, is the right layer to judge whether two dated CI citations together cross a line. `[verified: docs/private-project-redaction.md:176-184, 384-388]`
5. **Mechanism — the pooled block prints percentages only.** `anchors: root`. Justification: with no dollar figure and no raw count anywhere in the block, "Composition is publication" (`:199-217`, including its Account-P worked rejection) is satisfied structurally rather than per-figure *within the block itself*. It does not, on its own, close composition against a rate/count pair published *elsewhere* in the same doc — at plan-review time this cited a live example (`docs/cost-levers-considered.md:526,549`'s mean-$/PR rate and its sibling branch count, neither reproduced here since both are now redacted); a peer PR redacted both to ratio-only phrasing during this branch's later sync, so that specific pair no longer exists, but the general risk is why this design closes composition structurally rather than relying on any one doc's current content. That residual is closed instead by the in-band pointer's explicit composition caveat (Design detail, Output shape) rather than by this row's structural argument, since the block's own no-dollar/no-count property doesn't reach a figure it never touches. This is also why no `$`-per-round rate appears even though `:144-145` would permit one as a rate per dispatch. `[verified: docs/private-project-redaction.md:122-132, 144-145, 199-217; docs/cost-levers-considered.md — cited pair now redacted, current lines 542/565]`
6. **Mechanism — two-layer refusal, mirroring `cost --summary`.** `anchors: root`. Layer 1 in `cmd_review_round_cost` before any corpus scan; layer 2 re-derived inside the pooled render function, because every direct caller of that function — this module's own tests included — bypasses the CLI boundary. This is the shape `cost.py:533-564` already uses, and its layer-2 comment states exactly that rationale. `[verified: cost.py:533-564; scope.py:556-575]`
7. **Mechanism — `--pooled` also refuses the top-level `--config-dir`.** `anchors: row6`. Not in the flag list as briefed, but it is the most dangerous omission: `resolve_scan_roots` returns *that directory alone* when `--config-dir` is set, silently collapsing a "pooled" figure to one named account. `[verified: scope.py:353-355]`
8. **Mechanism — `--pooled` requires more than one resolved root, machine-wide or `--this-repo` alike.** `anchors: root`. A single-root pool *is* a per-account figure and the carve-out does not reach it: "What it permits" requires a corpus that actually mixes private and public sources (`:100-109`), and a pool of exactly one account/machine has nothing to mix — every share it reports is exactly that one account's own proportion, with no other account's data diluting it. "Account and machine scope"'s share exception (`:304-310`) states a share "may span accounts or machines" — language that describes crossing a boundary between two or more, not a floor of one — and a share confined to a pool of one is functionally identical to the per-account rate that section's opening sentence already confines to a single account by default. This holds under `--this-repo` too: "Own-history counts were never inside this class" states explicitly that a `transcript-analysis.py` measurement of tool calls, sessions, dispatches, dollars, or duration — exactly what `--pooled` computes (row 1) — does not get the own-history exemption and "stays inside [Account-and-machine-scope] machinery regardless of `--this-repo` scoping." Row 1 already cites this same sentence for the same conclusion; this row and row 1 now agree rather than being in tension. `pr-cost-section.sh`'s worked precedent (`:166-169`) doesn't carry a `--this-repo` exemption either — it publishes a `$`/PR rate under the ordinary single-account default every reporting mode gets, not an own-history-exempt figure. `[verified: docs/private-project-redaction.md:100-109, 166-169, 265-270, 304-310]`
9. **Mechanism — `--pooled` refuses `--this-repo` outright.** `anchors: row8`. This is a product-scope decision, not a policy requirement — the redaction doc permits a `--this-repo` pooled share under the same governance as machine-wide (row 8), it just isn't implemented. Three reasons to not implement it now: (a) no consumer — this plan's stated purpose is replacing the machine-wide figure at `docs/cost-levers-considered.md:565,569-572`, which was never `--this-repo`-scoped (`:552-554`); (b) on this engineer's own machine, `--this-repo` never resolves to more than one account in practice — only a small minority of this engineer's declared accounts have ever touched this repo, confirmed by listing each declared root's `projects/` directory for a claude-config entry — so a `--this-repo` pool would rarely if ever satisfy row 8's floor anyway; (c) `--this-repo` unions across every declared root by default (`scope.py`'s `_resolve_project_scope` docstring), the same width as machine-wide, so a compliant `--this-repo` variant would need the identical floor, pointer, and approval gate as machine-wide for zero exemption benefit — refusing it removes an always-untested code path from a publication surface instead of building parity machinery nothing consumes. Refuse loudly (exit 2), not a silent fallback to machine-wide scope — a scripted invocation must not believe it scoped to this repo when it didn't. `[engineer-confirmed this session]`
10. **Mechanism — `--pooled` suppresses the `DO NOT PUBLISH` banner.** `anchors: root`. The banner exists because the per-round table carries real branch names and dated dollars; the pooled block emits neither, and printing "DO NOT PUBLISH" above the one output mode built to be published would defeat the feature. `[verified: review_rounds.py:478-480, 550-562; scope.py:509-511]`
11. **Mechanism — branch-cluster percentile bootstrap, B = 2,000, fixed seed.** `anchors: row4`. Every statistic is a ratio of two branch-level sums, so the branch is the cluster; resampling rounds would leave the denominator undefined. Two lighter primitives were checked and fail: a **normal/Wilson analytic interval** assumes an unclustered binomial proportion and would understate the interval badly on a dollar ratio dominated by a few expensive branches; a **jackknife** is cheaper still but is known to be unreliable for ratio and non-smooth statistics, which is precisely this statistic's shape. Reusing an existing repo implementation is not an option (G5, and no resampling code exists anywhere in the repo — the `2,000-resample` citations at `docs/cost-levers-considered.md:205,368` are a different statistic, direction-of-effect on session-level rows). `[verified: grep for bootstrap/resample/percentile( across claude/.claude/scripts/ and claude-skills/ returns no statistical implementation]`
12. **Mechanism — the pooled policy constants stay local to `review_rounds.py`.** `anchors: root`. Two heavier placements rejected: hoisting them beside `scope._DO_NOT_PUBLISH_BANNER` is speculative generality at one consumer (that banner earned its place in `scope.py` at three call sites — `cost.py:621-622`, `review_rounds.py:479-480`, `transcript-analysis.py`'s `cmd_subagent_mix`); and a shared `scope`-level refusal helper serving both `--summary` and `--pooled` would abstract over two policies that disagree on root count. Promotion trigger, to be stated in a one-line comment beside the constants: when a second subcommand grows a pooled mode, move the doc pointer and approval pointer to `scope.py`. `[verified: scope.py:509-511 and its three call sites]`
13. The pooled cost denominator is `branch_totals` summed over **only those branch keys with at least one in-scope round** — identical to what the existing per-root footer accumulates, so the pooled share means the same thing the current `Non-round dollars` line's complement means and is comparable to the already-published 30.3%. `[verified: review_rounds.py:565-575, 590-594]`
14. No list-price caveat is duplicated from `cost.py`. The pooled block states a different, share-specific fact in one clause ("shares of list-price compute, not of billed spend") — `cost._LIST_PRICE_CAVEAT`'s "this is not an invoice" sentence is about absolute dollars, which this block never prints. `[verified: cost.py:25-37; G3]`
15. `docs/private-project-redaction.md:106-109` ("its output to the agent is the rounded pooled figure only — never per-session or per-project raw content") is inaccurate about what non-pooled `review-round-cost` already prints. Noted, deliberately not fixed here — separately trackable. `[verified: docs/private-project-redaction.md:100-109 against review_rounds.py:550-562]`
16. The pool is per-invocation, therefore per-machine. Reporting each machine's pooled share separately, or as a range across machines, is already established practice in this repo's own published doc, so this design's own output is honest independent of whether a separate cross-machine merge mechanism exists elsewhere. `[verified: docs/cost-levers-considered.md:541-543 publishes per-machine figures]` (See also the engineer-confirmed note above: the engineer confirmed a cross-machine merge mechanism is under design on another branch, unidentified as of this session, and confirmed this design should not block on or coordinate with it — G4's per-invocation constraint means `--pooled`'s own output is correct regardless of what that mechanism eventually does.)
17. **Mechanism — data-quality is reported as share-of-rounds-affected, not as counts.** `anchors: row5`. `unpriced_turns` has no denominator in the current return value, and adding one would change `compute_review_round_costs`'s per-round dict shape; "share of pooled rounds containing at least one unpriced turn / dangling dispatch" needs no new field and is a permitted count share under row 2. `[verified: review_rounds.py:305-310 return contract]`
18. **Mechanism — the resolved-scope header suppresses its root-count clause under `--pooled`.** `anchors: root`. `scope.print_resolved_scope` → `_resolved_scope_header` → `_root_count_desc` states the number of resolved scan roots unconditionally, including at one root, by design — that count tracks the number of declared accounts reachable from the machine, a literal per-account dimension not on `docs/private-project-redaction.md:115-116`'s closed countable list. Non-pooled output was never reachable for publication because it ships under the `DO NOT PUBLISH` banner (row 10); `--pooled` is the first mode where this pre-existing header field becomes citable, so it needs its own fix rather than inheriting the shared function's behavior unchanged. `_resolved_scope_header` itself is not modified — it is a shared function with other call sites (e.g. judgment-pair's `--out` file) that still need the undercount-prevention property row 18 exists to state is correct *there*. Instead, `--pooled` builds its own header line, reusing `scope_label` — which is always the literal `*` now that `--projects` and `--this-repo` (row 9) are both refused (`scope.py:448`), never `this repo (N project dirs)` — and replacing the root-count clause with the fixed word `pooled`. The header is therefore a fixed string, not merely digit-free. `[verified: scope.py:429-451, 454-495]`
19. **The "one boundary-crossing exception, ever" rule does not constrain this design.** `anchors: root`. That rule caps exactly one scarce mechanism — the whole-period before/after split — at one total use across everything ever published under the carve-out. (An earlier round of this plan also named a "Count-bin" order-of-magnitude label as a second such mechanism; the engineer confirmed this session that no such mechanism exists — it was removed from the redaction doc as overengineered, and every reference to it in this plan, including the dedicated test assertion below, is struck.) A dimensionless share is not that mechanism: it is a standing, always-available reporting mode enumerated in `docs/private-project-redaction.md:111-169`'s closed lists, not a one-time-consumable exception to a default the way the split is. `--pooled`'s output is entirely shares (rows 2, 5, 17) and never proposes a split, so it never spends the one-time allowance and never needs to check whether it has already been spent. `[verified: docs/private-project-redaction.md:185-198]`

### Design detail

**CLI wiring** (`transcript-analysis.py`, in `p_review_round_cost`):

```
--pooled  action="store_true"
help: "Print only a cross-account pooled block of shares (no dollar amounts, no
       raw counts, no per-branch rows). Refuses every scope-narrowing flag; see
       docs/private-project-redaction.md."
```

**Refusal policy** — one function, `_pooled_scope_refusal(args, roots=None) -> str | None`, evaluated in order, returning the first applicable message; `roots=None` skips the last clause. `cmd_review_round_cost` calls it twice (once before `resolve_scan_roots`, once after, with `roots`); `_render_pooled_block` calls it a third time as the defense-in-depth layer for direct callers. Every message names the flag and its reason, and ends with the doc pointer:

| Refused | Reason to state |
|---|---|
| `--branches` | names branches; a branch-scoped figure is a per-deliverable figure (row 3) |
| `--projects` other than the default `*` | a named glob is a per-project dimension |
| `--skill` | narrows the numerator against an un-narrowed denominator (already documented at `docs/transcript-analysis.md:1066`) and degenerates the per-skill lines |
| `--since` / `--until` | whole period only, never a time series (`docs/private-project-redaction.md:176-184`) |
| top-level `--config-dir` | collapses the pool to one named account (row 7) |
| `--this-repo` | not implemented as a pooled scope — a product decision, not a policy bar (row 9) |
| one resolved root | a single-account figure is a per-account figure (row 8) |

Exit code 2 on all, matching `cost --summary`. The single-root message must be actionable and must name the declared-roots file via `scope.TRANSCRIPT_CONFIG_DIRS_LABEL`, not a hardcoded path. The `--this-repo` row is evaluated in the flag block (`roots=None` layer), not the root-count clause, so it fires before the root-count check would — a run with `--this-repo` on a single-root machine must get the flag-not-supported message, not the "declare another account" message, since the latter wouldn't fix anything for a refused flag.

**Ordering invariant.** Both refusal calls inside `cmd_review_round_cost` must run, and exit, before any print side effect on the `--pooled` path — including the `and not pooled` change to the `DO NOT PUBLISH` banner-suppression branch at `:478-480`. The two edits share one function body, so this is stated here as an explicit implementation and test requirement rather than left to fall out of edit order: no header, no banner-suppression, and no pooled block may print before the second refusal call (the one with `roots` in hand) has returned `None`.

**Aggregation** (confirming the briefed estimate): yes, ~10 lines. Build one `per_branch` list, one entry per branch key present in the in-scope rounds, each holding `(round_dollars, agent_dollars, branch_dollars, per_skill_round_counts, per_skill_round_dollars, rounds_with_dangling, rounds_with_unpriced, round_count)`; `branch_dollars` comes from `branch_totals.get(branch_key, 0.0)`. Point estimates are elementwise sums over that list. The `root_idx` half of the key is simply never read — pooling *is* dropping it.

**Bootstrap.** Module constants `_BOOTSTRAP_RESAMPLES = 2000`, `_BOOTSTRAP_SEED = 0`, `_CI_LEVEL = 0.95`. Use a local `random.Random(_BOOTSTRAP_SEED)` instance, never the module-global RNG. Per resample: draw `len(per_branch)` entries with replacement (`rand.choices`), accumulate the same vector, recompute all shares from that one draw so every reported CI comes from a mutually consistent set of resamples. Percentile bounds by index on the sorted per-statistic resample distribution: `lo_idx = round(0.025 * (B - 1))`, `hi_idx = round(0.975 * (B - 1))`.

Comments the implementer must include (durable one-liners, no plan/PR narration):
- On `_BOOTSTRAP_RESAMPLES`: names the technique (percentile bootstrap, resampled over branches) and grounds the count in this repo's own prior use at `docs/cost-levers-considered.md:205`. Do not invent a textbook page or section number.
- On `_BOOTSTRAP_SEED`: fixed so a published figure is reproducible by whoever checks it; the value itself is arbitrary.
- On the resampling unit: the branch is the cluster because every reported statistic is a ratio of two branch-level sums.

Degenerate cases: fewer than two branches in scope, or a zero denominator, print `(95% CI not computed — too few branches in scope)` / `(95% CI not computed — no priced branch spend)`. Neither wording may contain a digit (see the enforcing test below).

**Output shape.** Header: `--pooled` does not call `scope.print_resolved_scope` (row 18) — it prints its own header line via the new `_pooled_resolved_scope_header`, built from `scope_label`, which is always the literal `*` under the refusal table, with a fixed `pooled` word in place of `_root_count_desc`'s root count. Then the pointer block, then the caption, then the figure lines. **Every figure below is illustrative filler, not derived from any run — chosen as visibly round numbers specifically so this plan file cannot be read as citing a real, unapproved figure:**

```
REVIEW ROUND COST SOURCES (*; pooled)

POOLED — publishable only under docs/private-project-redaction.md
§ "Publishing a pooled tooling measurement". Propose the figure, this exact
command, and the destination artifact to the owner, then cite the owner's
approval in that artifact. Nothing here checks that for you. Before citing
this alongside any rate or count already published elsewhere (e.g. a $/PR
rate or a branch count), name that composition in the proposal — these
shares were not designed to be composed with a figure outside this block.

Pooled across every scan root in scope, machine-wide, whole period. Every
figure below is a share of list-price compute, never of billed spend. No
dollar amount, no raw count, and no per-account, per-project, or per-branch
split is emitted. Each interval is a 2,000-resample percentile bootstrap
resampled over branches, so it reflects branch-to-branch variation, treating
the branches in scope as a sample of ongoing work.

  Share of branch spend
    inside round windows          40.0% (95% CI 35.0-45.0%)
    outside every round window    60.0% (95% CI 55.0-65.0%)
    reviewer dispatches only      22.0% (95% CI 17.0-27.0%)
  Round-window spend by skill
    code-review                   50.0% (95% CI 45.0-55.0%)
    plan-review                   30.0% (95% CI 25.0-35.0%)
    ready-for-review              20.0% (95% CI 15.0-25.0%)
  Rounds by skill
    code-review                   55.0% (95% CI 50.0-60.0%)
    plan-review                   25.0% (95% CI 20.0-30.0%)
    ready-for-review              20.0% (95% CI 15.0-25.0%)
  Rounds affected by a data-quality gap
    dangling dispatch              5.0% (95% CI 0.0-10.0%)
    unpriced turn                  0.0% (95% CI 0.0-0.0%)
```

There is one pointer/caption variant, not two — `--this-repo` is refused (row 9), so the pointer block and caption above are unconditional.

Every figure is formatted by one helper, `_fmt_share_with_ci(point, lo, hi) -> str`, so the grammar has one home and the enforcing test has one thing to pin. Percentages use `.1f`, matching `render._pct_of`.

**Structure.** `_render_pooled_block` is a new module-level function; `cmd_review_round_cost` gains an early return into it after `compute_review_round_costs`, before any per-branch printing. The existing per-branch renderer is deliberately *not* extracted into a symmetric `_render_per_branch` — that is a ~180-line refactor of well-tested code with no bearing on this feature.

## Critical files

One `code-writer` dispatch. Do not split: the refusal policy, the output grammar, and the tests that pin it are one body of shared context, and a second agent re-reading the redaction doc could resolve the same policy question differently.

**Modify — `claude/.claude/scripts/transcript-analysis.py`**
- `p_review_round_cost` (`:12559-12576`): add the `--pooled` argument after `--skill`.
- Reuse: nothing else; `_add_project_scope_args` (`:11777-11790`) is unchanged.

**Modify — `claude/.claude/scripts/transcript_analysis/review_rounds.py`**
- New module constants: `_POOLED_PUBLICATION_POINTER`, `_POOLED_CAPTION`, `_BOOTSTRAP_RESAMPLES`, `_BOOTSTRAP_SEED`, `_CI_LEVEL`, plus the promotion-trigger comment (row 12).
- New functions: `_pooled_scope_refusal`, `_pooled_branch_aggregates`, `_bootstrap_share_intervals`, `_resample_percentile`, `_fmt_share_with_ci`, `_pooled_resolved_scope_header` (row 18 — builds the header line locally instead of calling `scope.print_resolved_scope`, reusing `scope_label` but replacing the root-count clause with the fixed word `pooled`), `_render_pooled_block`.
- `cmd_review_round_cost` (`:436-614`): layer-1 refusal before `resolve_scan_roots` (includes the `--this-repo` check, row 9); second refusal call once `roots` is known; `and not pooled` on the `DO NOT PUBLISH` guard at `:478-480`; **guard the existing unconditional `scope.print_resolved_scope("review-round-cost", scope_label, roots)` call at `:483` with `if not pooled:`** — it executes three lines before `compute_review_round_costs` (`:486`), strictly before the early-return point below, so leaving it unguarded prints the original root-count-bearing header for `--pooled` too, a second time, ahead of the sanitized `_pooled_resolved_scope_header` line (row 18's fix would otherwise not close what it claims to); early return into `_render_pooled_block` (which itself calls `_pooled_resolved_scope_header`) after `compute_review_round_costs`. Extend the docstring with the pooled contract.
- `compute_review_round_costs` (`:259-392`): **unchanged**, return shape included.
- Reuse: `render._pct_of` / `render._pct_value` for point estimates (`render.py:39-48`), `scope.print_resolved_scope`, `scope.TRANSCRIPT_CONFIG_DIRS_LABEL`, `REVIEW_SKILLS` for stable per-skill ordering, stdlib `random.Random`. Do **not** import `cost.py` (G3); do not add a third-party dependency (G5).

**Modify — `claude/.claude/scripts/tests/test_transcript_review_rounds.py`**
- Add `pooled: bool = False` and `config_dir: str | None = None` to `_review_round_cost_args` (`:28-44`).
- New `TestBootstrapShareIntervals` class, calling the pure math functions directly with 3-5 synthetic `per_branch` tuples — no `_write_jsonl`, no fixture corpus, no CLI. This is the fast layer for the feature's actual arithmetic risk; the corpus/CLI-level tests below are scoped to what only that layer can prove (wiring, refusal enforcement, redaction, banner suppression), not re-proving the math:
  1. `_pooled_branch_aggregates` sums a hand-built `per_branch`-shaped list correctly across all seven accumulated fields.
  2. `_resample_percentile` on a known sorted sample returns the documented `round(0.025 * (B-1))` / `round(0.975 * (B-1))` indices, checked against a small fixed `B`.
  3. `_bootstrap_share_intervals` with `_BOOTSTRAP_SEED = 0` and a fixed synthetic `per_branch` list is deterministic across two direct calls in the same process (the in-process half of the determinism property; the cross-process half is item 9 below) **and** its point estimate matches a hand-computed ratio-of-sums on the same asymmetric-`branch_dollars` fixture required by the `TestCmdReviewRoundCostPooled` point-estimate item — determinism alone would pass identically on a function broken in a deterministic way (e.g. one that always returns `0.0`), so the numeric assertion is required here, not only at the CLI layer, to localize a ratio-computation regression to this function directly.
  4. `_fmt_share_with_ci` renders both the numeric form and both degenerate-wording forms (too few branches; zero denominator) with no digit in the degenerate parenthetical.
- New `TestCmdReviewRoundCostPooled` class:
  1. **Grammar (the convention-enforcing test).** On a two-root fixture with rounds under both roots: assert `"$" not in block`; then locate the figure lines by slicing the block on the literal `_POOLED_CAPTION` constant (not a line-offset scan or an indent match) and, for every line after that slice, strip the label and assert the remainder either contains no digit at all or matches `^\d{1,3}\.\d% \(95% CI (\d{1,3}\.\d-\d{1,3}\.\d%|not computed — [a-z ]+)\)$`. Slicing on the constant means a compliant digit in the caption paragraph above it (e.g. "2,000-resample") can never affect this test either direction. This is the single assertion that enforces "shares only, never a raw dollar total, never an un-declined raw count, never a rate beside its own denominator." Additionally: assert the figure-line label set equals a fixed tuple compared for equality (not "any line not otherwise forbidden") and assert no label matches `\b(before|after|pivot)\b` — that phrase contains no digit and would otherwise sail through the digit-based check untouched, and nothing else in this test's grammar would catch a future before/after split sneaking into `_render_pooled_block`. This turns row 19's "never proposes a split" from a design-time claim into a regression-checked one.
  2. **No `Mean rounds per branch` and no `Totals:` line** appear in pooled output (row 3's consequence, asserted by absence).
  3. **Banner suppression:** two roots + `--pooled` → `scope._DO_NOT_PUBLISH_BANNER` absent; same fixture without `--pooled` → present.
  4. **No branch-name leak:** a fixture branch named distinctively does not appear in pooled output, on the machine-wide two-root fixture (presence and absence asserted separately, matching `:945-976`'s existing convention).
  5. **Exact header string:** assert the pooled header line equals the literal `REVIEW ROUND COST SOURCES (*; pooled)` exactly, on the two-root machine-wide fixture (row 18) — an exact-string assertion, not merely digit-free, since `scope_label` can no longer vary once `--projects` and `--this-repo` are both refused. Also assert **absence**, across the *entire* printed block (not only the header line), of any substring matching `_root_count_desc`'s own patterns (`\d+ roots?\b`) — this is the regression check for the pre-existing, unguarded `scope.print_resolved_scope` call at `:483`: a test that only checks the sanitized line is *present* would still pass if the original root-count-bearing header also printed, unsanitized, ahead of it.
  6. **Cross-root pooling:** rounds under two roots produce one block with no `account-` label, and a share that reflects both roots' dollars rather than either root alone.
  7. **Refusals, one test per row of the table above**, each asserting `SystemExit` code 2 and a message naming the flag — including `--this-repo` (row 9), asserted on both a single-root and a two-root fixture so the flag-refusal message fires regardless of what the root-count check alone would have said. The "one resolved root" row has no flag to name, so its own test is distinct in shape, not covered by the "names the flag" template above: on a single-root fixture with no `--this-repo` and no other narrowing flag, assert `SystemExit` code 2 and that the message contains `scope.TRANSCRIPT_CONFIG_DIRS_LABEL`'s actual value (imported and compared, never a hardcoded string duplicate) — distinguishing this row's message, by content, from the `--this-repo` row's message on that same single-root fixture. A test suite must not satisfy "one test per row" by reusing the `--this-repo`-on-single-root case as evidence for both rows; the plain single-root path is the only path that ever reaches the root-count clause at all, given row 9's refusal fires first, and needs its own exercise.
  8. **Defense-in-depth:** call `_render_pooled_block` directly with an args namespace carrying `--branches`, bypassing the CLI layer → still exits 2.
  9. **Determinism, cross-process:** run the CLI as two separate `subprocess.run([sys.executable, ...])` invocations over a fixture with **at least four branches** (the two-root fixture reused elsewhere in this file is two *roots*, not necessarily four *branches* — confirm or extend it) and diff stdout byte-for-byte. Branch count matters here: with only two branches there are only two possible `set`-iteration orderings, and two independently hash-seeded subprocesses (no `PYTHONHASHSEED` is pinned anywhere in this repo's test config) have a non-trivial chance of coincidentally agreeing even with the exact bug present — a four-branch fixture widens the ordering space enough to make that false-negative unlikely. An in-process two-call comparison cannot substitute for this: `PYTHONHASHSEED` is fixed once per interpreter process, so two calls inside one pytest process share a hash seed even if a `set` iteration elsewhere in the pooled path would silently vary it across real, separate `python3` runs. As the implementation's own invariant (stated here because no other test can check it): no ordering anywhere in the `--pooled` path may pass through a `set` — only dict/list insertion order or an explicit sort, matching how `REVIEW_SKILLS`'s tuple order is already reused for skill-line ordering.
  10. **Point-estimate correctness:** a hand-computable two-branch fixture where the round-window share is known exactly. The two branches must carry **different** `branch_dollars` totals — with equal weights, "share of sums" (the correct aggregation) and "mean of per-branch shares" (a plausible regression) produce the same number, so an equal-weight fixture cannot distinguish correct code from that regression. Assert the point estimate and that the CI brackets it.
  11. **Degenerate:** one branch in scope → `not computed` wording, no crash, no digit in the parenthetical; zero priced branch dollars → no `ZeroDivisionError`; and a branch with priced non-round spend but zero in-scope rounds → pooled shares computed as if that branch didn't exist (row 13's denominator-exclusion rule — the one shape none of the other tests exercises, and the one a regression that pooled over every `branch_totals` key instead of only rounds' own branch keys would silently pass undetected).
  12. **Ordering invariant, no print before refusal:** on the "one resolved root" refusal fixture (item 7's single-root, non-`--this-repo` case — the furthest-executing refusal check in the function body, and therefore the one most exposed to a reordering regression), assert `capsys.readouterr().out` contains none of the pooled header, banner-suppression, or pointer-block strings when `SystemExit(2)` fires. Every other refusal test in this class checks the exit and the message; none inspects the printed side effect, so a future reordering of the `if not pooled:` guard on the pre-existing `scope.print_resolved_scope` call relative to the second refusal call (the ordering the Design detail's "Ordering invariant" paragraph and row 18's fix both depend on) could print a partial, unsanitized header ahead of the eventual exit(2) with every other stated test still passing.
  13. **Refusal-skip unit check:** call `_pooled_scope_refusal(args, roots=None)` directly (not through the CLI) on an args namespace carrying no narrowing flag, confirming the root-count clause's documented skip when `roots` is `None` returns `None` without raising — this is the layer-1, pre-resolution call path, and no other test in this class exercises it directly.
- Reuse: `_two_declared_roots` (`:67-80`), `_skill_block`, `_slash_user`, `_session_iter`, and the `conftest` helpers `_write_jsonl` / `_priced` / `_user_msg` / `_write_subagent_dispatch`.

**Modify — `docs/transcript-analysis.md`**
- `review-round-cost` section (`:1058-1102`): add `--pooled` to the Flags list, a short **Pooled mode** subsection stating the refusal list with its reasons, the caption, the bootstrap's resampling unit and resample count, and a sample output block. The sample must be synthetic, matching the existing section's convention.

**Modify — `docs/private-project-redaction.md`**
- One sentence only, in the worked-case paragraph at `:166-169`, naming `transcript-analysis.py review-round-cost --pooled` as the second worked aggregation boundary alongside `pr-cost-section.sh`. This file is the canonical home for which commands are aggregation boundaries, so omitting it means the next agent cannot find the mechanism from the policy. **Do not touch `:100-109`** (row 15).

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
```

`select-tests.py` scopes to the diff and is the command this repo documents for agents; it widens on its own when a diff warrants it. This diff's file set resolves to `{SCRIPTS_TESTS_DIR, HOOKS_TESTS_DIR, SKILLS_TESTS_DIR}` with no under-collection — `resolve_target_paths`'s containment filter (`select-tests.py:635-674`) already drops a file target subsumed by a directory target also in the selection, confirmed by an existing regression test for this exact shape.

Then, once green, run the command against the real corpus at both scopes to confirm the block renders and the refusals fire:

```bash
python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled
python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled --this-repo   # expect exit 2
python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled --since 2026-08-01   # expect exit 2
```

Record wall-clock time for the first invocation. The bootstrap is `2,000 × <branches in scope> × ~13` float accumulations on top of the corpus scan; if it materially lengthens the run, note the measurement as a follow-up rather than changing the design.

**Do not paste any real `--pooled` output** into the PR body, the commit message, this plan file, or a doc in this PR. The approval gate at `docs/private-project-redaction.md:333-411` governs every figure the command prints, and this plan ships in the same public PR as the implementation. Verification claims in the PR body state that the command ran and what shape the output had, never the figures themselves.

## Out of scope

- **Replacing the figures at `docs/cost-levers-considered.md:565` and `:569-572`** with `--pooled` output. That is the later phase named in this plan's own Context, and it needs an owner-approved figure per the approval gate before anything can land.
- **Fixing `docs/private-project-redaction.md:100-109`.** Its claim that a producing command's "output to the agent is the rounded pooled figure only" does not describe non-pooled `review-round-cost`, which prints per-round rows. Real, separately trackable, and a policy-doc edit that wants the owner's own review rather than a rider on a feature PR.
- **The rate-beside-its-denominator shape at `docs/cost-levers-considered.md:526`** (a mean-$/PR rate stated beside its own raw before/after dollar figures and sample sizes — not reproduced here) this bullet originally flagged has since been fixed by a peer PR, confirmed this session: the entry now states the same percentage change with no raw dollar figures or sample sizes, at current line `:542`. Nothing left to do here — recorded so this plan is not read as reproducing the now-superseded shape, and so `--pooled`'s own output must not edit that entry regardless.
- **A cross-machine merge mechanism** — a `--pooled-merge` mode, or a sufficient-statistics payload one machine writes for another to combine. Both machines' shares can be reported separately or as a range, which this repo's own published doc already does (`docs/cost-levers-considered.md:541-543`), and a naive merge payload built from `--pooled`'s own output would risk becoming a raw-pooled-total artifact — exactly the shape `docs/private-project-redaction.md:132-133` bars, if not designed carefully. The engineer confirmed a separate, unidentified branch is already building such a mechanism; two named candidates (`claude-config/pr-cost-forensics`, `claude-config/cross-account-aggregate-caveat`) were checked and confirmed to not be it (see Approach). This plan does not coordinate with that other effort — G4 means `--pooled`'s own output is correct on its own terms regardless of what a separate merge mechanism does with it.
- **Any programmatic or marker-based approval gate.** No such mechanism exists in the redaction doc; the in-band pointer directs a reader to the human process and says so.
- **Changing `compute_review_round_costs`** — detection semantics, filters, or return shape. `--pooled` is a pure consumer.
- **Extracting the existing per-branch renderer** into a symmetric `_render_per_branch`.
- **Hoisting the pooled policy constants to `scope.py`, or adding a pooled mode to any other subcommand.** The promotion trigger is recorded in a comment for whoever adds the second consumer.
- **Moving `cost._LIST_PRICE_CAVEAT` to `pricing.py`.** It would be the correct single-source home and would resolve the duplication question generally, but the pooled block needs a different, share-specific clause rather than that constant, so the move buys nothing here and would touch `cost.py` plus two assertions in `test_transcript_cost.py:2965,2978`.
