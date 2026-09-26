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
replacement is a later phase, out of scope here). `docs/cost-levers-considered.md`
states its other dollar-and-count figures as ratio-only phrasing, consistent
with this plan's own no-absolute-figure design choice (see row 5 and the Out
of scope section for the current state of each). Intended outcome: a
`--pooled` flag that computes and prints
cross-account pooled Cost-share and round-count figures compliant with
`docs/private-project-redaction.md`'s "Publishing a pooled tooling
measurement" section, replacing the ad hoc hand-summing.

## Approach

Add `--pooled` to `review-round-cost` as a **dedicated render path that emits percentages and nothing else** — no dollar amount, no raw count, no per-account or per-branch split — computed by summing `compute_review_round_costs`'s existing return value across its `(root_idx, branch)` keys, with a branch-cluster percentile bootstrap giving every share a 95% CI. The flag refuses every scope-narrowing flag at two layers (CLI boundary and inside the render function), suppresses the `DO NOT PUBLISH` banner that the per-round table needs, and prints an in-band pointer to the redaction doc's human approval gate. `compute_review_round_costs` itself is not touched.

The load-bearing choice is that the pooled block emits **no absolute figure of any kind**; row 5 carries that argument and its citations.

This revision closes the silent-exclusion bug class at every level of the traversal `--pooled` reads: scan root, project directory, and session transcript. The listing and read that already happen now record each unreadable directory or transcript in an opt-in counter, and the pooled block refuses when that counter is non-empty (rows 20-27). A missing path, or one that is not the kind of object its level expects, stays an empty scope rather than a gap, as it was before this revision (rows 32, 33). Because the root-level listing now matches `--projects` against one directory name, the CLI rejects any value `Path.glob` would read as another shape, rather than let it silently select nothing (row 36). The pooled stderr filter also fails closed: an unrecognized diagnostic is withheld behind one fixed notice rather than printed (row 29).

Alternatives set aside: (a) a `--summary`-style block that also prints median `$` per round — rejected, because a permitted per-round rate composes with that already-published round count into the raw pooled total `docs/private-project-redaction.md:132-133` bars outright; (b) mirroring `cost --summary`'s single-root requirement — rejected, because the figure this exists to produce is machine-wide across every declared account (`docs/cost-levers-considered.md:552-554`), so a single-account requirement would make the feature unable to compute the thing it was asked for; (c) sharing a refusal-policy helper with `cost --summary` — rejected, because the two policies differ in the root-count direction (summary refuses multi-root, pooled requires it), so one helper would be an abstraction over two different rules. This revision's own set-aside alternatives sit in rows 21, 26, 29, 30, 32, 33, and 36.

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
- **G6.** `Path.glob` returns no matches, rather than raising, when a directory it walks cannot be listed, and when the path it is called on is not a directory. On CPython 3.12, `_WildcardSelector._select_from` catches every `OSError` from `scandir`, and `_Selector.select_from` returns nothing when `is_dir()` is false. This is standard-library behavior the design cannot change. `[verified: /usr/lib/python3.12/pathlib.py:163-170, 199-206, 1083-1096 — the standard library of this repo's .venv interpreter, CPython 3.12.3 per its pyvenv.cfg; CI pins python-version '3.12' at .github/workflows/tests.yml:138]` Other interpreter versions are `[unverified]`. Verification's mutation check 1 re-confirms it at runtime on the interpreter under test.

**Rows.**

1. A "review round" is analogous to "agent dispatch" for `docs/private-project-redaction.md:115-116`'s closed countable list, making round-count a permitted countable unit — consistent with the "Own-history counts" section, which explicitly excludes a `transcript-analysis.py` measurement of tool calls, sessions, dispatches, dollars, or duration from its own-history exemption, keeping "review round" inside the mixed-corpus machinery this plan builds rather than sliding it into that exemption. `[engineer-verified; verified: docs/private-project-redaction.md:115-116, 265-270]`
2. Round-count is reportable only as a share or a median, never as a raw total — stricter than the doc's own counts bullet (`:122-124`), because a raw round count composes with a permitted per-round rate. `[engineer-verified]`
3. A branch is declined as a countable or reportable unit entirely (≈1:1 with a delivered PR/task, the reasoning that already bars a Duration share). Consequence the implementer must not miss: the existing footer's `Mean rounds per branch` line has **no pooled counterpart** and must not be carried over in any form, mean or median. `[engineer-verified]`
4. Every pooled share the block emits carries a bootstrap confidence interval as its sample-size disclosure — not a raw count. `[engineer-verified]`
   - CI width alone does not let a reader recover the exact branch count. Width is jointly determined by branch count *and* by the variance/skew of per-branch dollar shares, so two pools of equal size but different composition produce different widths. Inverting width back to count needs information (the variance) the reader doesn't have — unlike a raw count, which a reader recovers exactly.
   - A residual coarse trend remains: across repeated citations, a narrower interval over time implies the pool grew. Two existing controls bound it, so this design adds none:
     - The "No time series" standing bar (`docs/private-project-redaction.md:176-184`) means no compliant publication ever shows two dated citations side by side in one artifact.
     - The approval gate requires a proposal to name "any prior publication of the same or a composing statistic" (`:384-388`), which puts a second citation of the same share in front of the human approver before it ships. The approver, not a mechanical rule, is the right layer to judge whether two dated CI citations together cross a line.

   `[verified: docs/private-project-redaction.md:176-184, 384-388]`
5. **Mechanism — the pooled block prints percentages only.** `anchors: root`. This is the design's load-bearing choice.
   - Within the block: with no dollar figure and no raw count anywhere in it, "Composition is publication" (`:199-217`, including its Account-P worked rejection) is satisfied structurally rather than per-figure.
   - Against a rate/count pair published elsewhere: the block supplies no rate, so it can never be the rate half of a rate × count product that reconstructs a raw pooled total — including against a raw round or branch count published alongside a rate. This is also why no `$`-per-round rate appears even though `:144-145` would permit one as a rate per dispatch (alternative (a) above).
   - Residual: a share combined with an absolute figure published elsewhere can still yield a new absolute. The block's no-dollar/no-count property doesn't reach a figure it never touches, so the in-band pointer's explicit composition caveat (Design detail, Output shape) closes that residual instead.
   - The closure is structural rather than tied to any one doc's current content. The raw round count this rule was first checked against (`docs/cost-levers-considered.md:549` at plan-review time) was later redacted by a peer PR during this branch's sync and is not reproduced here.
   - It also makes the enforcing test a single grammar assertion rather than a per-figure judgment call.

   `[verified: docs/private-project-redaction.md:122-132, 144-145, 199-217; docs/cost-levers-considered.md — current lines 542/565]`
6. **Mechanism — two-layer refusal, mirroring `cost --summary`.** `anchors: root`. Layer 1 in `cmd_review_round_cost` before any corpus scan; layer 2 re-derived inside the pooled render function, because every direct caller of that function — this module's own tests included — bypasses the CLI boundary. This is the shape `cost.py:533-564` already uses, and its layer-2 comment states exactly that rationale. `[verified: cost.py:533-564; scope.py:556-575]`
7. **Mechanism — `--pooled` also refuses the top-level `--config-dir`.** `anchors: row6`. Not in the flag list as briefed, but it is the most dangerous omission: `resolve_scan_roots` returns *that directory alone* when `--config-dir` is set, silently collapsing a "pooled" figure to one named account. `[verified: scope.py:353-355]`
8. **Mechanism — `--pooled` requires more than one resolved root, machine-wide or `--this-repo` alike.** `anchors: root`. A single-root pool *is* a per-account figure, and the carve-out does not reach it:
   - "What it permits" requires a corpus that actually mixes private and public sources (`:100-109`). A pool of exactly one account/machine has nothing to mix: every share it reports is exactly that one account's own proportion, with no other account's data diluting it.
   - "Account and machine scope"'s share exception (`:304-310`) states a share "may span accounts or machines". That language describes crossing a boundary between two or more, not a floor of one.
   - A share confined to a pool of one is functionally identical to the per-account rate that section's opening sentence already confines to a single account by default.
   - This holds under `--this-repo` too. "Own-history counts were never inside this class" states explicitly that a `transcript-analysis.py` measurement of tool calls, sessions, dispatches, dollars, or duration — exactly what `--pooled` computes (row 1) — does not get the own-history exemption and "stays inside [Account-and-machine-scope] machinery regardless of `--this-repo` scoping." Row 1 cites the same sentence for the same conclusion.
   - `pr-cost-section.sh`'s worked precedent (`:166-169`) doesn't carry a `--this-repo` exemption either. It publishes a `$`/PR rate under the ordinary single-account default every reporting mode gets, not an own-history-exempt figure.

   `[verified: docs/private-project-redaction.md:100-109, 166-169, 265-270, 304-310]`
9. **Mechanism — `--pooled` refuses `--this-repo` outright.** `anchors: row8`. This is a product-scope decision, not a policy requirement — the redaction doc permits a `--this-repo` pooled share under the same governance as machine-wide (row 8), it just isn't implemented. Three reasons to not implement it now:
   - (a) No consumer — this plan's stated purpose is replacing the machine-wide figure at `docs/cost-levers-considered.md:565,569-572`, which was never `--this-repo`-scoped (`:552-554`).
   - (b) On this engineer's own machine, `--this-repo` never resolves to more than one account in practice — only a small minority of this engineer's declared accounts have ever touched this repo, confirmed by listing each declared root's `projects/` directory for a claude-config entry — so a `--this-repo` pool would rarely if ever satisfy row 8's floor anyway.
   - (c) `--this-repo` unions across every declared root by default (`scope.py`'s `_resolve_project_scope` docstring), the same width as machine-wide, so a compliant `--this-repo` variant would need the identical floor, pointer, and approval gate as machine-wide for zero exemption benefit — refusing it removes an always-untested code path from a publication surface instead of building parity machinery nothing consumes.

   Refuse loudly (exit 2), not a silent fallback to machine-wide scope — a scripted invocation must not believe it scoped to this repo when it didn't. `[engineer-confirmed this session]`
10. **Mechanism — `--pooled` suppresses the `DO NOT PUBLISH` banner.** `anchors: root`. The banner exists because the per-round table carries real branch names and dated dollars; the pooled block emits neither, and printing "DO NOT PUBLISH" above the one output mode built to be published would defeat the feature. `[verified: review_rounds.py:478-480, 550-562; scope.py:509-511]`
11. **Mechanism — branch-cluster percentile bootstrap, B = 2,000, fixed seed.** `anchors: row4`. Every statistic is a ratio of two branch-level sums, so the branch is the cluster; resampling rounds would leave the denominator undefined. Two lighter primitives were checked and fail: a **normal/Wilson analytic interval** assumes an unclustered binomial proportion and would understate the interval badly on a dollar ratio dominated by a few expensive branches; a **jackknife** is cheaper still but is known to be unreliable for ratio and non-smooth statistics, which is precisely this statistic's shape. Reusing an existing repo implementation is not an option (G5, and no resampling code exists anywhere in the repo — the `2,000-resample` citations at `docs/cost-levers-considered.md:205,368` are a different statistic, direction-of-effect on session-level rows). `[verified: grep for bootstrap/resample/percentile( across claude/.claude/scripts/ and claude-skills/ returns no statistical implementation]`
12. **Mechanism — the pooled policy constants stay local to `review_rounds.py`.** `anchors: root`. Two heavier placements rejected: hoisting them beside `scope._DO_NOT_PUBLISH_BANNER` is speculative generality at one consumer (that banner earned its place in `scope.py` at three call sites — `cost.py:621-622`, `review_rounds.py:479-480`, `transcript-analysis.py`'s `cmd_subagent_mix`); and a shared `scope`-level refusal helper serving both `--summary` and `--pooled` would abstract over two policies that disagree on root count. Promotion trigger, to be stated in a one-line comment beside the constants: when a second subcommand grows a pooled mode, move the doc pointer and approval pointer to `scope.py`. `[verified: scope.py:509-511 and its three call sites]`
13. The pooled cost denominator is `branch_totals` summed over **only those branch keys with at least one in-scope round** — identical to what the existing per-root footer accumulates, so the pooled share means the same thing the current `Non-round dollars` line's complement means and is comparable to the already-published 30.3%. `[verified: review_rounds.py:565-575, 590-594]`
14. No list-price caveat is duplicated from `cost.py`. The pooled block states a different, share-specific fact in one clause ("shares of list-price compute, not of billed spend") — `cost._LIST_PRICE_CAVEAT`'s "this is not an invoice" sentence is about absolute dollars, which this block never prints. `[verified: cost.py:25-37; G3]`
15. `docs/private-project-redaction.md:106-109` ("its output to the agent is the rounded pooled figure only — never per-session or per-project raw content") is inaccurate about what non-pooled `review-round-cost` already prints. Noted, deliberately not fixed here — separately trackable. `[verified: docs/private-project-redaction.md:100-109 against review_rounds.py:550-562]`
16. The pool is per-invocation, therefore per-machine. Reporting each machine's pooled share separately, or as a range across machines, is already established practice in this repo's own published doc, so this design's own output is honest independent of whether a separate cross-machine merge mechanism exists elsewhere. `[verified: docs/cost-levers-considered.md:541-543 publishes per-machine figures]` (See also the engineer-confirmed note above: the engineer confirmed a cross-machine merge mechanism is under design on another branch, unidentified as of this session, and confirmed this design should not block on or coordinate with it — G4's per-invocation constraint means `--pooled`'s own output is correct regardless of what that mechanism eventually does.)
17. **Mechanism — data-quality is reported as share-of-rounds-affected, not as counts.** `anchors: row5`. `unpriced_turns` has no denominator in the current return value, and adding one would change `compute_review_round_costs`'s per-round dict shape; "share of pooled rounds containing at least one unpriced turn / dangling dispatch" needs no new field and is a permitted count share under row 2. `[verified: review_rounds.py:305-310 return contract]`
18. **Mechanism — the resolved-scope header suppresses its root-count clause under `--pooled`.** `anchors: root`.
    - `scope.print_resolved_scope` → `_resolved_scope_header` → `_root_count_desc` states the number of resolved scan roots unconditionally, including at one root, by design.
    - That count tracks the number of declared accounts reachable from the machine — a literal per-account dimension not on `docs/private-project-redaction.md:115-116`'s closed countable list.
    - Non-pooled output was never reachable for publication, because it ships under the `DO NOT PUBLISH` banner (row 10). `--pooled` is the first mode where this pre-existing header field becomes citable, so it needs its own fix rather than inheriting the shared function's behavior unchanged.

    Why `_resolved_scope_header` itself stays unmodified: it is a shared function with other call sites (e.g. judgment-pair's `--out` file) that still need its undercount-prevention property.

    What `--pooled` builds instead: its own header line, reusing `scope_label` — always the literal `*` now that `--projects` and `--this-repo` (row 9) are both refused (`scope.py:448`), never `this repo (N project dirs)` — with the fixed word `pooled` in place of the root-count clause. The header is therefore a fixed string, not merely digit-free.

    `[verified: scope.py:429-451, 454-495]`
19. **The "one boundary-crossing exception, ever" rule does not constrain this design.** `anchors: root`. That rule caps exactly one scarce mechanism — the whole-period before/after split — at one total use across everything ever published under the carve-out. A dimensionless share is not that mechanism: it is a standing, always-available reporting mode enumerated in `docs/private-project-redaction.md:111-169`'s closed lists, not a one-time-consumable exception to a default the way the split is. `--pooled`'s output is entirely shares (rows 2, 5, 17) and never proposes a split, so it never spends the one-time allowance and never needs to check whether it has already been spent. `[verified: docs/private-project-redaction.md:185-198]`
20. The pooled caption's full-coverage claim ("Pooled across every scan root in scope") fails silently whenever a directory or transcript under a resolved root is unreadable. Each level fails differently today:
    - Scan root: covered only by `_pooled_scope_refusal`'s `os.access` clause, a probe separate from the listing that reads.
    - Project directory: `project_dir.glob("*.jsonl")` in `_iter_glob_scoped_sessions` and its sibling `_iter_scoped_sessions` drops an unreadable directory with no error (G6).
    - Session transcript: `read_session_file` returns `[]` for an unreadable file and for a readable empty one alike, and both iterators skip `[]` identically.

    `[verified: review_rounds.py:563-572; scope.py:293, 325-326; corpus.py:104-106, 135]`
21. **Mechanism — the listing that reads is the listing that records.** `anchors: row20`. `_list_dir_recording_gaps` replaces the bare `glob()` calls at `scope.py:293`, `:325`, and `:326` with `sorted(directory.iterdir())` inside `try/except`. A missing path or a non-directory returns `[]` silently (row 32). Any other `OSError` returns `[]` and records one gap at the caller's level. Alternatives checked:
    - Extend the `os.access` probe to every project directory and transcript before scanning. Rejected: a probe walk separate from the read is the layered-probe shape that let each prior fix stop one level short, and a permission change between probe and read makes the two disagree.
    - `os.walk(onerror=...)`. Rejected: it walks subagent directories `--pooled`'s iteration never reads, and the glob filter, sort, and cross-root dedup would all have to be re-applied on top of it.

    `[verified: scope.py:292-296, 325-329]`
22. **Mechanism — one shared inner generator for both multi-root iterators.** `anchors: row21`. `_iter_project_dir_sessions(project_dirs, include_subagents, scan_gaps)` replaces the identical four-line loop at `scope.py:292-296` and `:325-329`. Sharing it gives `_iter_scoped_sessions` the same fix as `_iter_glob_scoped_sessions` (CLAUDE.md, "Audit structural siblings"), even though `--pooled` refuses `--this-repo` and never reaches it. `[verified: scope.py:292-296, 325-329]`
23. **Mechanism — the single-root glob branch raises instead of recording.** `anchors: row22`. `_resolve_project_scope` raises `ValueError` when a caller passes `scan_gaps` and the glob branch has one root (`scope.py:449-450`). A supplied-but-ignored counter would otherwise read as a clean scan. `corpus.iter_sessions` is not given the capability:
    - Its one flat glob over `{projects_glob}/*.jsonl` has no per-directory listing to record against.
    - Its flat sort across full paths is a documented ordering guarantee (`corpus.py:145-152`) that a per-directory listing would have to re-derive.
    - `--pooled` never reaches it: the root-count refusal fires before scope resolution.

    `[verified: corpus.py:138-159; scope.py:449-450; review_rounds.py:918-931]`
24. **Mechanism — the session-transcript level reuses corpus's existing unreadable-versus-empty distinction.** `anchors: row20`. The shared generator calls `corpus._read_session_file_partitioned`. It returns `[]` for an unreadable main file and `[[], ...]` for a readable empty one, so no extra `open` is needed. The generator flattens the groups itself — a one-line comprehension duplicated from `read_session_file`, under CLAUDE.md's small-duplicated-value exception. `corpus.py`'s behavior is unchanged. Only `read_session_file`'s docstring caller list changes (`corpus.py:123-126`). `--pooled` iterates with `include_subagents=False`, so this read touches only the main transcript. `[verified: corpus.py:85-135; review_rounds.py:931; scope.py:372; transcript-analysis.py:45 already imports _read_session_file_partitioned]`
25. **Mechanism — the counter is an opt-in keyword holding level tags only.** `anchors: row21`.
    - `scan_gaps: collections.Counter[str] | None = None` is added to both iterators and `_resolve_project_scope`. Only the `--pooled` path in `review_rounds.py` passes one; every other `_resolve_project_scope` caller keeps the `None` default. `[verified: grep for "_resolve_project_scope(" under claude/.claude/scripts]`
    - Keys are three module constants in `scope.py` naming the level. A key never holds a path or an account ordinal, so the counter is not itself a per-account dimension.
    - With `scan_gaps=None`, every listing `OSError` is skipped silently, matching the old `glob` path on CPython 3.12. That path swallowed every `OSError` from `scandir` and returned nothing for a non-directory (G6). The new helper returns `[]` for every `OSError` and records only when given a counter. Other interpreter versions are `[unverified]`. `[verified: /usr/lib/python3.12/pathlib.py:163-170, 199-206]`
    - Root-level project selection moves from `root.glob(projects_glob)` to the listing plus `fnmatch.fnmatchcase(name, projects_glob)`. The two select the same entries only when `Path.glob` compiles the value as one wildcard component. It then matches each entry name with `fnmatch.translate`, case-sensitively on POSIX, which is `fnmatchcase`'s own rule. Row 36 rejects every other value at the CLI. `[verified: /usr/lib/python3.12/pathlib.py:57-58, 81-108, 190-219 (CPython 3.12.3)]`
26. **Mechanism — `--pooled` refuses on any recorded gap, after the scan and before the first print.** `anchors: row20`.
    - Refusing, not disclosing, matches the precedent the root-level `os.access` clause set. A digit-free notice printed beside the block (the ciso finding's alternative) was set aside: a printed figure stays citable whether or not its reader saw a stderr line.
    - `scan_gaps` fills only as `compute_review_round_costs` consumes the lazy session iterator. The earliest refusal point is therefore `_render_pooled_block`'s existing refusal call, which already runs before that function's first print. Nothing on the `--pooled` path prints to stdout before it, since the banner and the resolved-scope header are both suppressed.
    - `_render_pooled_block` takes `scan_gaps` as a required keyword-only parameter, so a direct caller cannot skip the clause by omission — the same reasoning as its existing `roots or []`. `_pooled_scope_refusal`'s own `scan_gaps=None` defers the clause, as `roots=None` defers the root-count clause. Only `cmd_review_round_cost`'s two pre-scan calls rely on that deferral.
    - Tradeoff: a misconfigured machine now pays a full scan before the refusal, a cost on the fail-closed side. A transcript deleted between listing and open is skipped, not refused (row 33).
    - The message is digit-free and names no path. It asks the user to check each account's `projects/` directory for an unreadable directory or `.jsonl`, with `find <projects-dir> ! -readable` labeled as a GNU find example. A `-maxdepth 2` form would put a digit in the message and break every refusal test's digit-free assertion. `-readable` is GNU-only and README.md lists macOS as supported, hence the prose statement alongside it.

    `[verified: review_rounds.py:702-726, 891-958; README.md:94]`
27. **Mechanism — the root-level `os.access` clause is deleted.** `anchors: row26`. The traversal's root-level listing records the same condition, so keeping both re-creates the layered probe this revision exists to end. Four existing tests change premise: the two CLI-level unreadable-root tests keep their assertions but no longer refuse "before any scan"; the direct-call test and the missing-active-profile test lose the clause they targeted and are rewritten (Critical files). `[verified: review_rounds.py:563-572; test_transcript_review_rounds.py:1837-1912]`
28. The dispatch-transcript level of the same bug class stays open. `compute_review_round_costs` reads subagent transcripts through `_price_dispatch` and `corpus._index_subagent_dispatches`, not through scope's iterators. An unreadable dispatch transcript, `meta.json`, or `subagents/` directory counts as a dangling dispatch, the same bucket as a routine lookup miss.
    - Inside a round window, the "dangling dispatch" share discloses it.
    - Outside every round window, its dollars leave `branch_totals` with no disclosure, which inflates the inside-round share.

    Closing it needs `compute_review_round_costs` to report dangling dispatches outside rounds, a return-shape change G2 places outside this plan. See Out of scope. `[verified: corpus.py:41-62; review_rounds.py:222-230, 326, 370-376]`
29. **Mechanism — the `--pooled` stderr filter fails closed.** `anchors: root`. Any stderr line matching no known pattern is replaced by one fixed, digit-free notice, printed once per wrapped call. Today's fail-open default already leaks: `pricing.py`'s NOTICE (`:432-437`, a raw `requestId` plus a record count) and WARNING (`:376-383`, a raw `requestId`) are both reachable under `--pooled` through `dedup_turns_by_request_id` inside the filtered compute call, and neither matches a known pattern. Alternatives checked:
    - A regression test that scans module source for new `print(..., file=sys.stderr)` sites (the ciso finding's required control). Rejected: it asserts on source text, not behavior, which `code-review`'s checklist item 9g bars. Its module list would also already be wrong: the declared-root diagnostic originates in `_config_dir.py`, outside the three modules the finding names.
    - A third known pattern for pricing's two lines. Rejected: it fixes today's instance and keeps the fail-open default for the next one.

    Tradeoff: under `--pooled`, an operator loses in-band diagnostics; the notice routes them to a non-pooled rerun. `[verified: pricing.py:222, 266-268, 280, 297, 376-383, 432-437; review_rounds.py:229, 322, 789-790, 804-841; claude-skills/skills/code-review/SKILL.md:118]`
30. **Mechanism — markdown heading extraction gets one home in `claude/.claude/tests/helpers.py`.** `anchors: row5`. The pointer-citation test guards the in-band pointer row 5 relies on, and it hand-rolls a heading scanner without `test_skills.py`'s normalization (backtick/emphasis stripping, whitespace collapse). Moving `_normalize_heading`/`_heading_texts` to `helpers.py` as public names gives both tests one implementation. Alternatives checked:
    - Import them from `test_skills.py` directly. Rejected: `claude-skills/skills/tests/` has no `__init__.py` and isn't on `pyproject.toml`'s `pythonpath`, so the import would depend on pytest's collection order.
    - Copy the normalization into the pooled test. Rejected: two homes for one rule is the finding itself.

    `helpers.py` is already on `pythonpath` and already imported by `test_skills.py`. `heading_texts` doesn't skip fenced code blocks, unlike the hand-rolled scanner. A stale pointer could false-pass only if its cited text appeared as a `#` line inside a fence in the redaction doc — accepted. `[verified: pyproject.toml:18; test_skills.py:54, 3252-3279; test_transcript_review_rounds.py:1638-1669]`
31. `_bootstrap_share_intervals` drops a resample's share when that draw's denominator is zero, so a key can get its CI from fewer than `_BOOTSTRAP_RESAMPLES` values. No test reaches a pool where only some draws hit that. The staff-sdet finding's suggested fixture (zero `agent_dollars`) wouldn't either: `spend_reviewer_only`'s denominator is `branch_dollars`, not `agent_dollars`. The new test gives two of four branches zero `branch_dollars` and zero `round_dollars`, and asserts on the resulting interval's bounds rather than the internal resample count. `[verified: review_rounds.py:603-608, 648-674]`
32. **Mechanism — a missing path and a non-directory are both an empty scope, not a gap.** `anchors: row21`. `_list_dir_recording_gaps` catches `NotADirectoryError` alongside `FileNotFoundError`. Both return `[]` and record nothing.
    - Without this branch, a scan root that exists as a regular file becomes a permanent root-level gap. `iterdir()` is `os.listdir` with no `except` (`pathlib.py:1052-1059`), so that root raises there. The pre-revision `root.glob(projects_glob)` returned nothing for it, because `_Selector.select_from` checks `is_dir()` before it lists (`pathlib.py:163-170`). The refusal's "restore read access" advice would then misdirect, since nothing is unreadable.
    - The deleted `os.access` clause had the same scope: it fired only when `os.path.isdir(root)` held (`review_rounds.py:563-565`). Row 27's replacement now matches it.
    - `resolve_scan_roots` does not filter non-directory roots (`scope.py:353-366`), so a declared account whose `projects` path is a regular file reaches this listing. That account contributes no branch. With fewer than two contributing accounts left, `_render_pooled_block` prints no share at all (`review_rounds.py:685-686, 761-768`).
    - The review finding that raised this cited a stray regular file (e.g. `.DS_Store`) directly under a scan root. That entry never reaches a project-dir listing. `_dedup_new_project_dirs` skips every candidate whose `is_dir()` is false (`scope.py:207-209`), and both multi-root iterators pass candidates through it before listing. The new branch is a second guard there. It does real work at `_iter_glob_scoped_sessions`'s root listing, which has no `is_dir()` guard, unlike `_iter_scoped_sessions` (`scope.py:280, 318-325`).
    - Alternatives checked:
      - An `is_dir()` guard before `_iter_glob_scoped_sessions`'s root listing, mirroring `scope.py:280`. Rejected: it is a probe separate from the listing, the shape row 21 rejects.
      - Keep recording the gap and reword the refusal. Rejected: no transcript is excluded, so there is nothing to refuse.
    - `os.listdir` on a non-directory failing with `ENOTDIR`, which Python raises as `NotADirectoryError`, is `[unverified]` this session. `TestScanGapCounter` item 9 and mutation check 6 confirm it at runtime.

    `[verified: /usr/lib/python3.12/pathlib.py:163-170, 1052-1059 (CPython 3.12.3); scope.py:207-209, 280, 318-325, 353-366; review_rounds.py:563-565, 685-686, 761-768]`
33. **Mechanism — the session-transcript level applies row 32's rule through `_failed_transcript_read_is_gap`.** `anchors: row32`. A `*.jsonl` whose read returns `[]` records `_SCAN_GAP_SESSION_FILE` only while it is still a regular file, or when stat'ing it fails. A directory named `*.jsonl`, a dangling `*.jsonl` symlink, and a transcript deleted between listing and open are all skipped, as a missing directory is.
    - This is row 32's bug shape one level down (CLAUDE.md, "Audit structural siblings"). The pre-revision `project_dir.glob("*.jsonl")` yielded every name match, directories and dangling symlinks included. A one-segment pattern's selector sets `dironly` false and never checks `is_dir()` (`pathlib.py:155-161, 199-219`). Each such entry failed its `open`, `read_session_file` returned `[]`, and the loop skipped it silently (`corpus.py:65-82, 104-106`). Recording every `[]` would turn each into a permanent refusal.
    - `Path.is_file()` returns `False` for `ENOENT`, `ENOTDIR`, `EBADF`, and `ELOOP`, and re-raises any other `OSError` (`pathlib.py:44, 51-53, 888-900`). The helper counts a re-raised `OSError` as a gap. That covers `EACCES` inside a project directory that is readable but not searchable, where listing succeeds and every `open` fails.
    - It runs only after a read has failed, and only when `scan_gaps` is not `None`. The happy path gains no `stat`, and non-pooled callers are untouched. It cannot make a readable transcript refuse, so it is not row 21's rejected pre-read probe.
    - Alternatives checked:
      - `is_file()` on every listed entry before reading. Rejected: one `stat` per transcript on the happy path, and a pre-read probe is row 21's rejected shape.
      - Have `_parse_jsonl_records` report which `OSError` it caught. Rejected: row 24 keeps `corpus.py`'s behavior unchanged, and that function's `None` contract also serves the subagent-file read (`corpus.py:112-115`).
    - It supersedes row 26's transient-deletion tradeoff: a transcript deleted between listing and open is skipped, not refused.

    `[verified: /usr/lib/python3.12/pathlib.py:44-53, 155-161, 199-219, 888-900; corpus.py:65-82, 104-117]`
34. Row 29's fail-closed filter removed the check that caught a recognized diagnostic slipping past its own pattern. `test_pooled_run_prints_no_root_count_diagnostic_to_stderr` caught a `scanning root` line that stopped matching only through its digit-free assertion. That worked while the `else:` branch printed the raw, digit-bearing line. After row 29 the same line becomes the digit-free withheld notice, and the test passes. Each of the two end-to-end stderr tests therefore gains one assertion that `_POOLED_STDERR_WITHHELD_NOTICE` is absent, rather than a new test being added. The declared-root test's `count == 1` already catches a full slip of its own pattern. Its new assertion catches a recognized line that also reaches the `else:` branch. `[verified: review_rounds.py:788-794, 831-840; test_transcript_review_rounds.py:2254-2294]`
35. The withheld-diagnostics notice sends an operator to a non-pooled rerun (row 29). That rerun surfaces the diagnostic only while the non-pooled path calls `compute_review_round_costs` outside the filter (`review_rounds.py:944`). No test pinned that, as the ciso FYI noted. Decision: add the test now rather than defer it. It reuses `_pooled_two_root_fixture`, needs no chmod, and guards the only route an operator has to a withheld diagnostic. `[verified: review_rounds.py:844-850, 940-952]`
36. **Mechanism — `--projects` accepts only a value `Path.glob` reads as one directory-name pattern.** `anchors: row25`. Row 25's root-level `fnmatchcase` matches a single entry name, so a `--projects` value `Path.glob` read any other way would now silently select something else. That reaches every subcommand whose scan resolves more than one root — any subcommand on a machine that declares a second account (`scope.py:353-366`) — not only `--pooled`, which refuses a non-default `--projects` anyway. On CPython 3.12, `Path.glob` departs from `fnmatchcase` in four shapes:
    - A `/` splits the value across directory levels (`pathlib.py:404, 1093-1095`). `fnmatchcase` never matches it, since no entry name contains `/`.
    - A whole-component `**` selects every directory at every depth, subagent directories included (`:85-93, 222-229`). `fnmatchcase` reads it as `*`.
    - A `**` inside a component raises `ValueError` (`:98-99`). `fnmatchcase` accepts it.
    - `.` parses to no component, so `_make_selector` raises `IndexError` (`:82, 404`). `..` selects the root's parent (`:96-97, 179-187`). `fnmatchcase` matches neither, since `iterdir()` lists neither.

    The new argparse `type=` function `scope._single_level_projects_glob` rejects all four with exit 2. It is wired into both `--projects` definitions that reach `_iter_glob_scoped_sessions`: `_add_project_scope_args`'s, and `skill-invocation`'s own, whose command calls `_iter_glob_scoped_sessions` directly (`transcript-analysis.py:2339-2344`).
    - It rejects on a single-root machine too, where `corpus.iter_sessions` still passes the value to `Path.glob`. Whether a command is accepted should not depend on how many accounts the machine declares.
    - Every value it admits also selects the same project directories in `_scan_root_transcripts`, which keeps `root.glob(projects_glob)` (`scope.py:650`), as in the session iterator `cost` runs beside it.
    - `user-input`'s own `--projects` stays unvalidated. It reads only `scope.PROJECTS_DIR` through `corpus.iter_sessions`, which this revision leaves unchanged (`transcript-analysis.py:541, 572`).
    - Every production caller of `_iter_glob_scoped_sessions` gets `projects_glob` from a parsed `--projects`. A direct Python caller bypasses the check, so the function's docstring states the precondition.
    - Alternatives checked:
      - Document and test the single-level narrowing as accepted. Rejected: a value that silently selects nothing is this revision's own bug class. The docs already call the flag a project-directory glob, and nothing enforced it.
      - Validate inside `_resolve_project_scope`. Rejected: it runs mid-subcommand, and `cmd_skill_invocation` bypasses it, so it would need a second copy there. An argparse `type=` rejects before any subcommand code runs, and `_iso_date` (`transcript-analysis.py:240-246`) is this file's precedent for one.
      - Keep `root.glob(projects_glob)` for selection beside a separate listing that records the root-level gap. Rejected: a listing separate from the one that reads is row 21's rejected shape.
    - No test literal and no documented invocation uses any of the four shapes, so no existing invocation changes. `[verified: grep of --projects and projects= literals under claude/.claude/scripts/tests; grep for --projects values containing "/" or "**" across the repo outside .claude/plans]`
    - Whether other interpreter versions depart in the same four shapes is `[unverified]`. It bears only on this one-time change: after it, root-level selection is `fnmatchcase` on every version.

    `[verified: /usr/lib/python3.12/pathlib.py:81-102, 179-187, 222-229, 404, 1083-1096 (CPython 3.12.3); transcript-analysis.py:240-246, 541, 572, 2337-2346, 12584-12596, 12636, 12819-12824; scope.py:353-366, 448-451, 650, 703-704]`

### Design detail

**CLI wiring** (`transcript-analysis.py`, in `p_review_round_cost`):

```
--pooled  action="store_true"
help: "Print only a cross-account pooled block of shares (no dollar amounts, no
       raw counts, no per-branch rows). Refuses every scope-narrowing flag; see
       docs/private-project-redaction.md."
```

**Refusal policy** — one function, `_pooled_scope_refusal(args, roots=None, scan_gaps=None) -> str | None`, evaluated in order, returning the first applicable message. `roots=None` skips the root-count clause. `scan_gaps=None` skips the scan-gap clause. `cmd_review_round_cost` calls it twice (once before `resolve_scan_roots`, once after, with `roots`); both calls precede the scan. `_render_pooled_block` calls it a third time with `roots or []` and its required `scan_gaps`. That third call is the defense-in-depth layer for direct callers and the only point the scan-gap clause can fire. Every message names the flag or condition and its reason, and ends with the doc pointer:

| Refused | Reason to state |
|---|---|
| `--branches` | names branches; a branch-scoped figure is a per-deliverable figure (row 3) |
| `--projects` other than the default `*` | a named glob is a per-project dimension |
| `--skill` | narrows the numerator against an un-narrowed denominator (already documented at `docs/transcript-analysis.md:1066`) and degenerates the per-skill lines |
| `--since` / `--until` | whole period only, never a time series (`docs/private-project-redaction.md:176-184`) |
| top-level `--config-dir` | collapses the pool to one named account (row 7) |
| `--this-repo` | not implemented as a pooled scope — a product decision, not a policy bar (row 9) |
| one resolved root | a single-account figure is a per-account figure (row 8) |
| a resolved scan root, or a directory or transcript under one, that cannot be read | that part of the corpus would silently drop out of the pool (rows 20, 26); evaluated only after the scan |

Exit code 2 on all, matching `cost --summary`. The single-root message must be actionable and must name the declared-roots file via `scope.TRANSCRIPT_CONFIG_DIRS_LABEL`, not a hardcoded path. The `--this-repo` row is evaluated in the flag block (`roots=None` layer), not the root-count clause, so it fires before the root-count check would — a run with `--this-repo` on a single-root machine must get the flag-not-supported message, not the "declare another account" message, since the latter wouldn't fix anything for a refused flag.

**Ordering invariant.** Both refusal calls inside `cmd_review_round_cost` must run, and exit, before any print side effect on the `--pooled` path — including the `and not pooled` change to the `DO NOT PUBLISH` banner-suppression branch at `:478-480`. The two edits share one function body, so this is stated here as an explicit implementation and test requirement rather than left to fall out of edit order: no header, no banner-suppression, and no pooled block may print before the second refusal call (the one with `roots` in hand) has returned `None`. The scan-gap clause is the one refusal that cannot run before the scan. It fires at `_render_pooled_block`'s refusal call, which still precedes that function's first print, so a refused run prints nothing to stdout.

**Aggregation** (confirming the briefed estimate): yes, ~10 lines. Build one `per_branch` list, one entry per branch key present in the in-scope rounds, each holding `(round_dollars, agent_dollars, branch_dollars, per_skill_round_counts, per_skill_round_dollars, rounds_with_dangling, rounds_with_unpriced, round_count)`; `branch_dollars` comes from `branch_totals.get(branch_key, 0.0)`. Point estimates are elementwise sums over that list. The `root_idx` half of the key is simply never read — pooling *is* dropping it.

**Bootstrap.** Module constants `_BOOTSTRAP_RESAMPLES = 2000`, `_BOOTSTRAP_SEED = 0`, `_CI_LEVEL = 0.95`. Use a local `random.Random(_BOOTSTRAP_SEED)` instance, never the module-global RNG. Per resample: draw `len(per_branch)` entries with replacement (`rand.choices`), accumulate the same vector, recompute all shares from that one draw so every reported CI comes from a mutually consistent set of resamples. Percentile bounds by index on the sorted per-statistic resample distribution: `lo_idx = round(0.025 * (B - 1))`, `hi_idx = round(0.975 * (B - 1))`.

Comments the implementer must include (durable one-liners, no plan/PR narration):
- On `_BOOTSTRAP_RESAMPLES`: names the technique (percentile bootstrap, resampled over branches) and grounds the count in this repo's own prior use at `docs/cost-levers-considered.md:205`. Do not invent a textbook page or section number.
- On `_BOOTSTRAP_SEED`: fixed so a published figure is reproducible by whoever checks it; the value itself is arbitrary.
- On the resampling unit: the branch is the cluster because every reported statistic is a ratio of two branch-level sums.

Degenerate cases: fewer than two branches in scope, fewer than two contributing roots (`review_rounds.py:761-767`), or a zero denominator, print `(95% CI not computed — too few branches in scope)` / `(95% CI not computed — no priced branch spend)`. Neither wording may contain a digit (see the enforcing test below).

**Output shape.** Header: `--pooled` does not call `scope.print_resolved_scope` (row 18) — it prints its own header line via the new `_pooled_resolved_scope_header`, built from `scope_label`, which is always the literal `*` under the refusal table, with a fixed `pooled` word in place of `_root_count_desc`'s root count. Then the pointer block, then the caption, then the figure lines. **Every figure below is illustrative filler, not derived from any run — chosen as visibly round numbers specifically so this plan file cannot be read as citing a real, unapproved figure:**

```text
REVIEW ROUND COST SOURCES (*; pooled)

POOLED — publishable only under docs/private-project-redaction.md
§ "The owner can authorize one figure, case by case". Propose the figure, this exact
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

**Traversal gap recording (`scope.py`, rows 21-25, 32, 33, 36).**

```python
_SCAN_GAP_ROOT = "root"
_SCAN_GAP_PROJECT_DIR = "project-dir"
_SCAN_GAP_SESSION_FILE = "session-file"

def _list_dir_recording_gaps(
    directory: Path, scan_gaps: Counter[str] | None, level: str,
) -> list[Path]: ...

def _failed_transcript_read_is_gap(jsonl: Path) -> bool: ...

def _iter_project_dir_sessions(
    project_dirs: Iterable[Path], include_subagents: bool, scan_gaps: Counter[str] | None,
) -> Iterator[tuple[Path, list[dict]]]: ...

def _iter_scoped_sessions(slugs, include_subagents, roots=None, *, scan_gaps=None): ...
def _iter_glob_scoped_sessions(roots, projects_glob, include_subagents, *, scan_gaps=None): ...
def _resolve_project_scope(args, subcommand, include_subagents=False, roots=None, *, scan_gaps=None): ...

def _single_level_projects_glob(value: str) -> str: ...
```

- `_list_dir_recording_gaps`: `sorted(directory.iterdir())`. `FileNotFoundError` or `NotADirectoryError` → `[]`, nothing recorded. Any other `OSError` → `[]`, plus `scan_gaps[level] += 1` when `scan_gaps` is not `None`. It never prints.
- `_failed_transcript_read_is_gap`: returns `jsonl.is_file()`. An `OSError` raised by `is_file()` → `True`.
- `_iter_project_dir_sessions`: for each project dir, list with `_SCAN_GAP_PROJECT_DIR` and keep entries where `fnmatch.fnmatchcase(entry.name, "*.jsonl")`. Read each with `corpus._read_session_file_partitioned`. On `[]`, `scan_gaps[_SCAN_GAP_SESSION_FILE] += 1` when `scan_gaps` is not `None` and `_failed_transcript_read_is_gap(jsonl)` is true, then skip either way. Otherwise flatten the groups in order and yield when non-empty — the existing `if records:` rule.
- `_iter_glob_scoped_sessions`: list the root with `_SCAN_GAP_ROOT`, filter by `fnmatch.fnmatchcase(entry.name, projects_glob)`, pass through `_dedup_new_project_dirs` as today, then the shared generator. That filter selects what `root.glob(projects_glob)` did for every value `_single_level_projects_glob` admits (rows 25, 36). The "scanning root" print stays unchanged; `--pooled`'s stderr filter drops it.
- `_iter_scoped_sessions`: its existing root-level `try/except OSError` and stderr diagnostic stay. The `except` also records `_SCAN_GAP_ROOT` when `scan_gaps` is not `None`. The inner loop becomes the shared generator.
- `_resolve_project_scope`: threads `scan_gaps` into both iterators. The single-root glob branch raises `ValueError` when `scan_gaps is not None`.
- `_single_level_projects_glob`: an argparse `type=` for `--projects`, beside `_projects_glob` (`:703-704`). A value containing `/` or `**`, or equal to `.` or `..`, raises `argparse.ArgumentTypeError("must match one project-directory name: no '/' or '**', and not '.' or '..'")`. Any other value, `""` included, is returned unchanged. `transcript-analysis.py` wires it into `_add_project_scope_args`'s `--projects` and `skill-invocation`'s. `user-input`'s own `--projects` stays unwired (row 36).

**Refusal and stderr wiring (`review_rounds.py`, rows 26, 27, 29).**

- `cmd_review_round_cost`: `scan_gaps = Counter() if pooled else None`, passed to `scope._resolve_project_scope` and to `_render_pooled_block`.
- `_render_pooled_block(args, roots, scope_label, rounds, branch_totals, *, scan_gaps: Counter[str])`, with `scan_gaps` required and passed to its refusal call.
- `_pooled_scope_refusal`: the `os.access` clause is deleted. After the root-count clause: `if scan_gaps: return _POOLED_SCAN_GAP_REFUSAL + _POOLED_REFUSAL_DOC_POINTER`.
- `_POOLED_SCAN_GAP_REFUSAL`, a module-level f-string like `_DECLARED_ROOT_SKIPPED_NOTICE`: "review-round-cost --pooled refuses a partial scan: a resolved scan root, or a directory or transcript under one, exists but could not be read, so part of the corpus would silently drop out of the pooled figure. Check each account's projects/ directory for a directory or .jsonl transcript you cannot read (with GNU find: `find <projects-dir> ! -readable`), then restore read access, or remove that account from {scope.TRANSCRIPT_CONFIG_DIRS_LABEL} if it is a declared entry you no longer need."
  - The message's diagnosis and its "restore read access" advice hold because a gap is recorded only when an existing directory or regular file fails to read (rows 32, 33). A missing path, a stray non-directory, and a non-regular `*.jsonl` never reach it. A non-permission `OSError` on an existing path (e.g. `EIO`) still does. There, the advice and the `find` hint don't apply. Accepted: the refusal itself is still correct, and only the hint misdirects.
- `_POOLED_STDERR_WITHHELD_NOTICE`: "review-round-cost --pooled: one or more diagnostics were withheld; rerun without --pooled to read them before citing any figure."
- `_pooled_filtered_stderr_call`'s `else:` branch prints `_POOLED_STDERR_WITHHELD_NOTICE` once per call, deduped through the existing `printed_notices` set, instead of the raw line.

Durable comments and docstrings to write (one fact per sentence):
- `_list_dir_recording_gaps`: "Lists with iterdir, not glob: Path.glob returns no matches for an unreadable directory instead of raising."
- Its `FileNotFoundError`/`NotADirectoryError` branch: "A missing path or a non-directory is an empty scope, not a gap."
- `_failed_transcript_read_is_gap`'s docstring: "A transcript that failed to read is a gap only while it is still a regular file. A missing path or a non-regular file is an empty scope, as a missing directory is. A failing stat counts as a gap."
- `_resolve_project_scope`'s docstring: "`scan_gaps`, when given, records one level tag per unreadable directory or transcript the returned iterator skips. The single-root glob branch cannot record gaps, so it raises ValueError rather than ignore the counter."
- `_single_level_projects_glob`'s docstring: "argparse type for --projects. The multi-root scan matches the value against one directory name with fnmatch. A value containing '/' or '**', or equal to '.' or '..', would silently match nothing there or match something else, so it is rejected."
- `_iter_glob_scoped_sessions`'s docstring, one added sentence: "projects_glob must name one directory level, since each root entry's name is matched against it with fnmatch; the CLI enforces this through _single_level_projects_glob."
- `_pooled_scope_refusal`'s docstring: "roots=None defers the root-count check. scan_gaps=None defers the scan-gap check. Only cmd_review_round_cost's own calls may rely on either deferral, since both precede the scan. Every other caller must pass a resolved list and a counter."
- `_render_pooled_block`'s docstring: "scan_gaps fills only as the session iterator is consumed, so this function's refusal call is the only point the scan-gap clause can fire."
- The pattern-table comment at `review_rounds.py:804-807`: "Known diagnostic shapes and their replacements. Any stderr line matching none of them is withheld behind _POOLED_STDERR_WITHHELD_NOTICE."

## Critical files

### This revision

Two `code-writer` dispatches, run in sequence, never in parallel: Dispatch 1, then Dispatch 2. They overlap on three files — `review_rounds.py`, `test_transcript_review_rounds.py`, and `docs/transcript-analysis.md` — but in disjoint functions, tests, and paragraphs. Sequencing keeps either from clobbering the other, and neither prompt needs the other's context. Dispatch 1 goes first because Dispatch 2's scan-gap refusal item 6 asserts on `_POOLED_STDERR_WITHHELD_NOTICE`, which Dispatch 1 introduces. Dispatch 2's line numbers predate Dispatch 1's edits; locate each target by its symbol. Each dispatch ends with the Verification section's `select-tests.py` and `ruff` commands. The mutation checks run once, after Dispatch 2.

#### Dispatch 1 — fail-closed stderr filter, heading-helper move, bootstrap partial-zero test

Batched, not coupled. Its three pieces share no design context: the fail-closed stderr filter and its tests (rows 29, 34, 35), the heading-helper move (row 30), and the partial-zero-denominator bootstrap test (row 31). They share only `test_transcript_review_rounds.py`, down to its import line. None needs another to pass, so splitting further would put three agents on that one file for no gain. Review each piece against its own row.

**Modify — `claude/.claude/scripts/transcript_analysis/review_rounds.py`**
- New constant `_POOLED_STDERR_WITHHELD_NOTICE` (Design detail).
- Stderr filter (`:788-841`): fail-closed `else:` branch. Rewrite the comment at `:804-807` and the pass-through sentence in `_pooled_filtered_stderr_call`'s docstring (`:822-823`).

**Modify — `claude/.claude/scripts/tests/test_transcript_review_rounds.py`**
- Imports: add `pricing` to the `transcript_analysis` import (`:11`), and `REPO_ROOT`, `heading_texts`, `normalize_heading` from `helpers`.
- Stderr filter:
  1. Rename `test_pooled_stderr_filter_passes_through_a_genuine_diagnostic` (`:2296`) to `test_pooled_stderr_filter_withholds_an_unrecognized_diagnostic` and invert it. The injected line is absent from `err`, `_POOLED_STDERR_WITHHELD_NOTICE` appears exactly once, and "scanning root" is still dropped. Rewrite the docstring to match; this supersedes the stale-name finding.
  2. Rename `test_pooled_stderr_filter_reemits_buffered_lines_when_wrapped_call_raises` (`:2319`) to `test_pooled_stderr_filter_withholds_buffered_lines_when_wrapped_call_raises`. The `RuntimeError` still propagates, the raw line is absent, and the notice is present.
  3. New: `monkeypatch.setattr(pricing, "_non_contiguous_merge_notices_logged", set())`, then run `pricing._log_non_contiguous_merge_decision("<placeholder-request-id>", 2, merged=True)` through `review_rounds._pooled_filtered_stderr_call`. Assert the placeholder id is absent from `err` and the notice is present. This exercises a real production print reachable under `--pooled` today (row 29), not a source scan.
  4. `test_pooled_run_prints_no_root_count_diagnostic_to_stderr` (`:2254-2268`) and `test_pooled_run_with_unreadable_declared_root_entry_prints_no_digit_to_stderr` (`:2270-2294`): add `assert review_rounds._POOLED_STDERR_WITHHELD_NOTICE not in err` to each (row 34). Keep every existing assertion.
  5. New: `test_withheld_diagnostic_reaches_stderr_on_a_non_pooled_rerun` (row 35).
     - Capture the real `review_rounds.compute_review_round_costs`, then monkeypatch it with a wrapper that prints one placeholder diagnostic to stderr and delegates.
     - On `_pooled_two_root_fixture`, the `--pooled` run's `err` lacks the placeholder and contains `_POOLED_STDERR_WITHHELD_NOTICE` exactly once.
     - The non-pooled run's `err` contains the placeholder and lacks the notice.
     - One monkeypatch reaches both paths, because each looks the function up as a module global at call time (`review_rounds.py:850, 944`).
- `test_pooled_publication_and_refusal_pointers_cite_a_real_heading` (`:1638-1669`): replace the hand-rolled scanner with `heading_texts(...)` over the doc and `normalize_heading(...)` on the cited text. Use `REPO_ROOT` instead of `Path(__file__).resolve().parents[4]`. Keep only the docstring's statement of why the test exists; drop its justification for hand-rolling.
- New `TestBootstrapShareIntervals` test (row 31). Build four branches: `_asymmetric_two_branch_pooled_totals()` (50% and 60% spend shares) plus two branches with zero `branch_dollars`, zero `round_dollars`, zero per-skill dollars, and nonzero `round_count`. An all-zero-denominator draw then has probability 1/16, well above the 2.5% lower tail. For `spend_inside`, assert `lo is not None` and `50.0 <= lo <= point <= hi <= 60.0`. Assert that `gap_unpriced`, whose denominator is `round_count`, still gets a non-`None` CI.
- Docstring splits (one fact per sentence):
  - `test_resample_percentile_at_the_half_index_rounding_boundary` (`:1361-1371`): give "only `hi` is asserted" and its float-error reason their own sentences, separate from "production B varies per stat because a zero-denominator draw is dropped before indexing". Drop the `--` aside.
  - `test_no_branch_name_leak` (`:1605-1611`): "Asserts presence in the disclosed render and absence from the pooled render separately." Then: "Matches the existing disclosed/redacted pairing convention (see `TestCmdReviewRoundCost.test_branch_label_raw_under_this_repo_and_redacted_otherwise_multi_root`)."
  - `test_pooled_run_with_unreadable_declared_root_entry_prints_no_digit_to_stderr` (`:2273-2281`): split into two sentences at "Still-poolable case:".

**Modify — `claude/.claude/tests/helpers.py`**
- Receive `_HEADING_LINE_RE`, `_HEADING_STRIP_CHARS_RE`, `_normalize_heading`, and `_heading_texts` from `test_skills.py:3252-3279`. Rename the two functions to public `normalize_heading` and `heading_texts`. Behavior is unchanged, including no fenced-code skipping.

**Modify — `claude-skills/skills/tests/test_skills.py`**
- Delete the moved definitions (`:3252-3279`). Add `heading_texts` and `normalize_heading` to the existing `from helpers import ...` line (`:54`). Rename every call site (`:3357, :3401, :3406, :3419, :3821, :4924, :4927, :4939, :4942`) and the comment at `:3908`. `test_normalize_heading` (`:3817`) stays here and now tests the helper.

**Modify — `docs/transcript-analysis.md`** (`## review-round-cost`, Pooled mode paragraph, `:1148-1159`)
- Add one sentence to the paragraph at `:1159`: under `--pooled`, any stderr diagnostic the command doesn't recognize is withheld behind one fixed notice; rerun without `--pooled` to read it.

**Modify — `docs/private-project-redaction.md`**
- Replace the six-sentence paragraph at `:193-201`, under § "The owner can authorize one figure, case by case", with two sentences. First: "`transcript-analysis.py review-round-cost --pooled` is a worked instrument for this section." Second: "`docs/transcript-analysis.md` § "review-round-cost" is the canonical home for its refusal list, contributing-account floor, and output grammar." Keep the citation on one line (`.claude/rules/citation-grammar.md`). Cite that section heading, not "Pooled mode", which is a bold lead-in, not a heading.
- Do not touch the "What it permits" passage (row 15).

#### Dispatch 2 — scan-gap traversal and `--projects` validation

Do not split: the counter contract in `scope.py`, its consumer in `review_rounds.py`, and the end-to-end chmod tests that pin both are one body of shared context — a CLI-level scan-gap test can only be debugged against the traversal it exercises. The `--projects` validator belongs here because it exists only for the traversal's new root-level `fnmatch` match (row 36).

**Modify — `claude/.claude/scripts/transcript_analysis/scope.py`**
- Add the three level constants, `_list_dir_recording_gaps`, `_failed_transcript_read_is_gap`, and `_iter_project_dir_sessions` (Design detail, Traversal gap recording).
- `_iter_scoped_sessions` (`:240-296`): keyword-only `scan_gaps=None`. Record `_SCAN_GAP_ROOT` in the existing `except OSError` (`:284-291`), keeping its stderr line. Replace the inner loop (`:292-296`) with the shared generator.
- `_iter_glob_scoped_sessions` (`:299-329`): keyword-only `scan_gaps=None`. Replace `sorted(root.glob(projects_glob))` (`:325`) with the listing helper plus `fnmatch.fnmatchcase`, which selects what the old `glob` did for every value `_single_level_projects_glob` admits (rows 25, 36). Replace the inner loop (`:325-329`) with the shared generator. Add the docstring sentence from Design detail.
- `_resolve_project_scope` (`:369-451`): keyword-only `scan_gaps=None`, threaded into both iterators. `ValueError` in the single-root glob branch (`:449-450`) when `scan_gaps is not None`. Add the docstring sentence from Design detail.
- Add `_single_level_projects_glob` beside `_projects_glob` (`:703-704`), with its docstring from Design detail (row 36).
- Imports: add `fnmatch`, `collections.Counter`, and `_read_session_file_partitioned` to the corpus import (`:31`). Drop `read_session_file` from that import if nothing else uses it (ruff F401 will say).
- Unchanged: `_scan_root_transcripts` and its docstring caveat (`:616-634`) — cost's per-root path, not a `--pooled` path.
- Reuse: `_dedup_new_project_dirs` (`:193-214`); `corpus._read_session_file_partitioned` (`corpus.py:85-117`).

**Modify — `claude/.claude/scripts/transcript-analysis.py`**
- Add `_single_level_projects_glob` to the `transcript_analysis.scope` import (`:156-171`).
- Pass `type=_single_level_projects_glob` to `--projects` in `_add_project_scope_args` (`:12592`) and in `skill-invocation`'s scope group (`:12820-12824`). Leave `user-input`'s own `--projects` (`:12636`) unchanged (row 36).

**Modify — `claude/.claude/scripts/transcript_analysis/corpus.py`** — docstring only.
- `read_session_file`'s docstring (`:123-126`) names `iter_sessions` and `_iter_scoped_sessions` as its callers. After this revision `_iter_scoped_sessions` no longer calls it, and it already had callers the paragraph never named: `analyze-context.py:132`, `token-analyzer.py:77`, and `transcript-analysis.py:12293`, beside `iter_sessions` (`corpus.py:157`). Replace the paragraph with two sentences that name no caller list, so the next caller cannot make it stale: "The shared merged-records read, so callers cannot drift in how they parse records or merge subagent files. Inside scope.py, `_iter_project_dir_sessions` calls `_read_session_file_partitioned` directly instead, to tell an unreadable main file from a readable empty one, and repeats only the flatten below." No behavior change.

**Modify — `claude/.claude/scripts/transcript_analysis/review_rounds.py`**
- `_pooled_scope_refusal` (`:516-573`): add the `scan_gaps=None` parameter. Delete the `os.access` clause (`:563-572`). Add the scan-gap clause after the root-count clause. Rewrite the docstring's deferral sentence per Design detail. Drop `import os` if it becomes unused.
- New constant `_POOLED_SCAN_GAP_REFUSAL` (Design detail).
- `_render_pooled_block` (`:702-785`): required keyword-only `scan_gaps: Counter[str]`, passed to its refusal call at `:721`. Add the docstring sentence.
- `cmd_review_round_cost` (`:853-958`): build the counter under `--pooled`. Pass it at `:931` and `:957`.

**Modify — `claude/.claude/scripts/tests/test_transcript_analysis.py`**
- New class `TestScanGapCounter` directly after `TestIterScopedSessionsUnreadableRoot` (`:20649-20678`). Each chmod test carries `@pytest.mark.skipif(os.geteuid() == 0, ...)` and restores permissions in `finally`, matching `:20658-20671`. Assertions reference `_mod.scope._SCAN_GAP_*`, never raw strings. Fixture names within one directory stay distinct under `str.casefold()`, per `.claude/skills/test-conventions-claude-config/SKILL.md`; the literal names in items 3 and 12 satisfy it.
  1. `_iter_glob_scoped_sessions` over two roots, one unreadable → counter is `{_SCAN_GAP_ROOT: 1}`, and the readable root's sessions are still yielded.
  2. A readable root holding one readable and one unreadable project dir → `{_SCAN_GAP_PROJECT_DIR: 1}`, and the readable project's sessions are still yielded.
  3. An unreadable `sealed.jsonl` → `{_SCAN_GAP_SESSION_FILE: 1}`. A readable empty `empty.jsonl` beside it records nothing — the distinction row 24 relies on.
  4. A root that doesn't exist → the counter stays empty.
  5. `_iter_scoped_sessions` with an unreadable slug-matched project dir → `{_SCAN_GAP_PROJECT_DIR: 1}` — the structural sibling gets the fix (row 22).
  6. `_resolve_project_scope(..., roots=[one_root], scan_gaps=Counter())` → `ValueError`.
  7. Item 2's fixture with `scan_gaps` omitted → no exception, and the same sessions item 2 yields. Non-pooled callers are unchanged.
  8. Across items 1-3, `set(counter) <= {the three constants}`: no path, no ordinal.
  9. `_iter_glob_scoped_sessions` over two roots, one of them a regular file rather than a directory → the counter stays empty, and the directory root's sessions are still yielded. This is the test that catches a missing `NotADirectoryError` branch (row 32). No chmod.
  10. A regular file named `.DS_Store` directly under a readable root, matched by the default `*` → the counter stays empty, and the root's sessions are still yielded. This test only pins existing behaviour. `_dedup_new_project_dirs`'s `is_dir()` skip filters the entry before any project-dir listing, so it passes with or without row 32's branch. No chmod.
  11. Inside a readable project dir holding one readable `.jsonl`, add a directory named `stray.jsonl` and a dangling `dangling.jsonl` symlink → the counter stays empty, and the readable transcript is still yielded. Paired with item 3, this pins row 33's classification in both directions. No chmod.
  12. Several gap levels in one run, with two gaps at each increment site: `_iter_glob_scoped_sessions` over two roots, with `roots[0]` chmod'd `000`. `roots[1]` holds two project dirs chmod'd `000` (`proj-sealed-a`, `proj-sealed-b`), plus one readable project dir (`proj-open`) holding `sealed-a.jsonl` and `sealed-b.jsonl` chmod'd `000` beside a readable `open.jsonl`. Expect `counter == Counter({_SCAN_GAP_ROOT: 1, _SCAN_GAP_PROJECT_DIR: 2, _SCAN_GAP_SESSION_FILE: 2})` exactly, and only `open.jsonl` yielded. Exact equality catches a level recorded under another level's key. The two counts of two distinguish `+= 1` from `= 1` at both increment sites: `_list_dir_recording_gaps` records the project-dir gaps, and `_iter_project_dir_sessions` records the session-file gaps (mutation check 9).
  13. `roots[1]` holds a symlink to a readable project dir under `roots[0]` → the counter stays empty, and each of that project's sessions is yielded exactly once. A candidate `_dedup_new_project_dirs` drops is skipped, not recorded as a gap. This test only pins existing behaviour. `_dedup_new_project_dirs` drops the aliased candidate before any scan-gap code runs, so no scan-gap mutation changes its result. No chmod.
  14. `_iter_scoped_sessions` over two roots, one of them chmod'd `000`, called with `scan_gaps=Counter()` and a literal slug list → `{_SCAN_GAP_ROOT: 1}`, and the readable root's slug-matched sessions are still yielded. Its root-level `except OSError` is the one increment site outside `_list_dir_recording_gaps` and `_iter_project_dir_sessions`, and no other item reaches it.
- New class `TestSingleLevelProjectsGlob` directly after `TestScanGapCounter` (row 36). No chmod and no fixture directory.
  1. `_mod.scope._single_level_projects_glob` returns each accepted value unchanged, parametrized: `"*"`, `"-home-user-repo*"`, `"feat-?"`, `"[ab]*"`, and `""`.
  2. It raises `argparse.ArgumentTypeError` for each rejected value, parametrized: `"a/b"`, `"a/"`, `"/a"`, `"**"`, `"a**"`, `"."`, `".."`.
  3. `_mod.build_parser().parse_args([...])` with `--projects a/b` exits with `SystemExit` code 2 on `buckets`, which reaches the `_add_project_scope_args` wiring, and on `skill-invocation`, which has its own `--projects`. The captured `err` names `--projects`.
- Existing tests must pass unchanged, including every multi-root `--projects` glob test (fnmatch parity, rows 25 and 36) and `TestIterScopedSessionsUnreadableRoot`.

**Modify — `claude/.claude/scripts/tests/test_transcript_review_rounds.py`**
- Every surviving direct `_render_pooled_block(...)` call (`:1803, :1814, :1823, :1834, :2002, :2005, :2083, :2204`) passes `scan_gaps=Counter()`. `Counter` and `scope` are already imported (`:7, :11`).
- Scan-gap refusal, in `TestCmdReviewRoundCostPooled` (chmod tests skip under `euid == 0`):
  1. New: `_pooled_two_root_fixture` plus a second project dir under `roots[1]` holding a round, chmod'd `000`. That account still contributes through its readable project, so the refusal can only come from the scan-gap clause. Assert `SystemExit` code 2, `out == ""`, `review_rounds._POOLED_SCAN_GAP_REFUSAL` in `err`, no digit in `err`, and the unreadable directory's path not in `err`.
  2. New: the same fixture, with one of `roots[1]`'s two session `.jsonl` files chmod'd `000` instead → the same assertions.
  3. `test_refuses_unreadable_scan_root_via_cmd_review_round_cost` and `test_refuses_unreadable_active_profile_scan_root_via_cmd_review_round_cost` (`:1837-1877`): keep their assertions. Rewrite both docstrings: the refusal now comes from the traversal's root-level record after the scan, not from a pre-scan probe.
  4. Replace `test_render_pooled_block_called_directly_refuses_unreadable_scan_root` (`:1879-1891`) with a direct call passing two fabricated roots and `scan_gaps=Counter({scope._SCAN_GAP_PROJECT_DIR: 1})` → exit 2. No chmod needed.
  5. `test_missing_active_profile_projects_dir_is_not_refused_as_unreadable` (`:1893-1912`): run `cmd_review_round_cost` with `--pooled` end to end on the same fixture and assert it renders without `SystemExit`. Its current `_pooled_scope_refusal` assertion targets a clause that no longer exists.
  6. New: `test_pooled_clean_scan_with_harmless_entries_does_not_refuse`, the explicit test that a clean scan does not trip the scan-gap clause.
     - Run `cmd_review_round_cost` with `--pooled` on `_pooled_two_root_fixture` and keep `out`.
     - Then add a regular file named `.DS_Store` directly under `roots[1]`. Inside `roots[1]`'s project dir, add a readable empty `.jsonl` and a directory named `stray.jsonl`.
     - Run again.
     - Assert that neither run raises `SystemExit`, and that the second run's `out` equals the first's byte for byte.
     - Assert that neither run's `err` contains `_POOLED_SCAN_GAP_REFUSAL` or `_POOLED_STDERR_WITHHELD_NOTICE`.
     - No chmod, so no `skipif`.

**Modify — `docs/transcript-analysis.md`**
- § "Scoping to this repo: `--this-repo`", first paragraph (`:22`): append "On every subcommand that accepts `--this-repo`, `--projects` must match one project-directory name: a value containing `/` or `**`, or equal to `.` or `..`, exits 2." (row 36).
- `## review-round-cost`, Pooled mode paragraph: replace the bullet at `:1157` with: "a resolved scan root, or a directory or transcript under one, that exists but cannot be read -- that part of the corpus would silently drop out of the pool; checked only after the full scan, and the refusal names neither the path nor a count".

#### After both dispatches — PR #1009 body (not a repository file)

After implementation, the session runs `/pr-description` for four items. The first three come from reviewer findings in an earlier round; this plan did not read the body itself.
- DEFER row 2: drop the claim that the test's docstring calls it "a regression-guard pin, not a discriminator". No such text exists in `test_transcript_review_rounds.py` `[verified: grep for "regression-guard", "not a discriminator", "pins pre-existing" returns zero matches]`. The deferral itself stands; restate its actual reason.
- DEFER row 3: re-evaluate for removal (Out of scope); don't edit its text in place.
- The Notes-for-the-reviewer claim that nothing reachable on the `--pooled` path needed changing is superseded by this revision.
- State row 36's `--projects` validation as a user-visible change: a value containing `/` or `**`, or equal to `.` or `..`, now exits 2 on every subcommand that accepts `--this-repo`.

### Original feature (implemented in earlier commits on this branch; kept for reference)

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

**Modify — `docs/private-project-redaction.md`** — superseded by this revision's entry above.

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
```

- `select-tests.py` decides the scope and widens on its own when a diff warrants it. This revision edits `claude/.claude/tests/helpers.py`, the shared test-helper module (`test_skills.py:54` imports it), so expect a wider selection than earlier revisions. Neither hand-widen nor hand-narrow it.
- The run's `-ra` summary (`pyproject.toml`'s `addopts`) lists every skipped test. Confirm none of the new chmod-based tests reports as skipped. A run as root would skip them and verify nothing about rows 21-27.
- Run each mutation check below once locally, after Dispatch 2 has landed, and revert it before commit. Together they prove the new tests catch the bugs they target. Each check must fail on an assertion, or on pytest's `DID NOT RAISE`, or on an unexpected `SystemExit(2)` where stated. It must never fail on an uncaught exception from the code under test.
  1. Put `project_dir.glob("*.jsonl")` back in `_iter_project_dir_sessions` in place of its project-dir listing.
     - Expected: `TestScanGapCounter` items 2, 5, and 12 fail with an `AssertionError` on the counter, because the `_SCAN_GAP_PROJECT_DIR` entry is missing. Scan-gap refusal item 1 fails with `DID NOT RAISE` for `SystemExit`.
     - That failure shape re-confirms G6 on the interpreter under test.
     - If either test instead fails with an uncaught `PermissionError`, G6 is false on that interpreter. Stop and report it to the engineer rather than count the check as passed.
  2. Make `_bootstrap_share_intervals` append `0.0` for a `None` draw → the partial-zero-denominator test fails.
  3. Restore pass-through in `_pooled_filtered_stderr_call`'s `else:` branch → the pricing-notice test fails.
  4. Pass `_SCAN_GAP_ROOT` instead of `_SCAN_GAP_PROJECT_DIR` to `_iter_project_dir_sessions`'s project-dir listing → `TestScanGapCounter` items 2, 5, and 12 fail with an `AssertionError` on the counter's keys. This proves the per-level assertions tell levels apart. It does not affect the refusal, which reads only the counter's truthiness.
  5. Reverse `_pooled_scope_refusal`'s `if scan_gaps:` to `if not scan_gaps:` → scan-gap refusal items 1-4 fail with `DID NOT RAISE`. Scan-gap refusal items 5 and 6 fail with an unexpected `SystemExit(2)`, like every other test that renders a `--pooled` block.
  6. Drop `NotADirectoryError` from `_list_dir_recording_gaps`'s silent branch → `TestScanGapCounter` item 9 fails with an `AssertionError`, because the counter holds `{_SCAN_GAP_ROOT: 1}` instead of being empty. Item 10 still passes, as row 32 predicts.
  7. Make `_failed_transcript_read_is_gap` return `True` unconditionally → `TestScanGapCounter` item 11 fails with an `AssertionError`, and scan-gap refusal item 6 fails with an unexpected `SystemExit(2)`.
  8. Change `_SCANNING_ROOT_DIAGNOSTIC_RE` so it no longer matches `scanning root N/M...` → `test_pooled_run_prints_no_root_count_diagnostic_to_stderr` fails on its new withheld-notice assertion, and so does scan-gap refusal item 6. Its digit-free assertion alone no longer catches this (row 34).
  9. Change `scan_gaps[level] += 1` to `scan_gaps[level] = 1` in `_list_dir_recording_gaps` → `TestScanGapCounter` item 12 fails with an `AssertionError` on exact counter equality, because `_SCAN_GAP_PROJECT_DIR` reads 1 instead of 2. Revert it, then make the same change to `_iter_project_dir_sessions`'s `_SCAN_GAP_SESSION_FILE` increment → item 12 fails the same way on `_SCAN_GAP_SESSION_FILE`. Item 12 is the only fixture with two gaps at one level, so it alone tells accumulation from assignment. No refusal test notices either change, since the refusal reads only the counter's truthiness.
  10. Remove `type=_single_level_projects_glob` from `_add_project_scope_args`'s `--projects` → `TestSingleLevelProjectsGlob` item 3's `buckets` case fails with `DID NOT RAISE` for `SystemExit`. Restore it, then delete the validator's `/` check → item 2's `"a/b"`, `"a/"`, and `"/a"` cases fail with `DID NOT RAISE` for `ArgumentTypeError`.

Then, once green, run the command against the real corpus at both scopes to confirm the block renders and the refusals fire:

```bash
python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled
python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled --this-repo   # expect exit 2
python3 claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled --since 2026-08-01   # expect exit 2
```

Record wall-clock time for the first invocation. The bootstrap is `2,000 × <branches in scope> × ~13` float accumulations on top of the corpus scan; if it materially lengthens the run, note the finding, without its number, as a follow-up rather than changing the design.

Then bound what a refusal now costs. The deleted `os.access` clause refused an unreadable root before any scan. The scan-gap clause refuses only after `compute_review_round_costs` has consumed every readable root's sessions (row 26), so a refused run now pays a full scan of the readable corpus. Bound that from the happy-path run instead of reproducing a refusal: never chmod a real config directory to test this. Profile the first invocation once:

```bash
python3 -m cProfile -s cumtime claude/.claude/scripts/transcript-analysis.py review-round-cost --pooled 2>/dev/null | grep -E 'function calls|\(_bootstrap_share_intervals\)'
```

The run's total minus `_bootstrap_share_intervals`'s cumulative time upper-bounds a refused run on this machine. `_render_pooled_block`'s refusal call precedes the bootstrap and every print (`review_rounds.py:721-726`), and an unreadable root adds no sessions. The `grep` keeps the pooled block's figures out of the output. If the bound is most of the run, record that as the known cost of refusing after the scan; do not restore a pre-scan probe (row 21 rejects it).

Every timing above is measured over the machine-wide, multi-account corpus. Report it to the engineer in session only, never in the PR body, a commit, an issue, or this plan (`docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact").

If the first invocation now exits 2 with the scan-gap refusal, the new traversal check found a real unreadable path on this machine. Locate it with the refusal's own hint and report it to the engineer in session. Never paste that path into the PR, the commit, or this plan: it can name a private project. If it prints the withheld-diagnostics notice, rerun without `--pooled` to read the diagnostics, and report them in session only.

**Do not paste any real `--pooled` output** into the PR body, the commit message, this plan file, or a doc in this PR. The approval gate at `docs/private-project-redaction.md:333-411` governs every figure the command prints, and this plan ships in the same public PR as the implementation. Verification claims in the PR body state that the command ran and what shape the output had, never the figures themselves.

## Out of scope

- **Replacing the figures at `docs/cost-levers-considered.md:565` and `:569-572`** with `--pooled` output. That is the later phase named in this plan's own Context, and it needs an owner-approved figure per the approval gate before anything can land.
- **Fixing `docs/private-project-redaction.md:100-109`.** Its claim that a producing command's "output to the agent is the rounded pooled figure only" does not describe non-pooled `review-round-cost`, which prints per-round rows. Real, separately trackable, and a policy-doc edit that wants the owner's own review rather than a rider on a feature PR.
- **`docs/cost-levers-considered.md:526/542`** already states a ratio only; `--pooled` output must not edit that entry regardless.
- **A cross-machine merge mechanism** — a `--pooled-merge` mode, or a sufficient-statistics payload one machine writes for another to combine. Both machines' shares can be reported separately or as a range, which this repo's own published doc already does (`docs/cost-levers-considered.md:541-543`), and a naive merge payload built from `--pooled`'s own output would risk becoming a raw-pooled-total artifact — exactly the shape `docs/private-project-redaction.md:132-133` bars, if not designed carefully. The engineer confirmed a separate, unidentified branch is already building such a mechanism; two named candidates (`claude-config/pr-cost-forensics`, `claude-config/cross-account-aggregate-caveat`) were checked and confirmed to not be it (see Approach). This plan does not coordinate with that other effort — G4 means `--pooled`'s own output is correct on its own terms regardless of what a separate merge mechanism does with it.
- **Any programmatic or marker-based approval gate.** No such mechanism exists in the redaction doc; the in-band pointer directs a reader to the human process and says so.
- **Changing `compute_review_round_costs`** — detection semantics, filters, or return shape. `--pooled` is a pure consumer.
- **Extracting the existing per-branch renderer** into a symmetric `_render_per_branch`.
- **Hoisting the pooled policy constants to `scope.py`, or adding a pooled mode to any other subcommand.** The promotion trigger is recorded in a comment for whoever adds the second consumer.
- **Moving `cost._LIST_PRICE_CAVEAT` to `pricing.py`.** It would be the correct single-source home and would resolve the duplication question generally, but the pooled block needs a different, share-specific clause rather than that constant, so the move buys nothing here and would touch `cost.py` plus two assertions in `test_transcript_cost.py:2965,2978`.
- **The dispatch-transcript level of the silent-exclusion class (row 28).** Left as a named residual: closing it changes `compute_review_round_costs`'s return shape, which G2 places outside this plan. It is a candidate follow-up issue.
- **A source-scanning regression test for new stderr call sites (row 29).** The fail-closed filter makes it unnecessary, and `code-review`'s checklist item 9g bars the shape.
- **Gap recording in `corpus.iter_sessions`, the single-root path (row 23).** `_resolve_project_scope` raises instead of accepting a counter it would ignore.
- **`_scan_root_transcripts`'s nested-subdirectory caveat (`scope.py:629-632`).** That is cost's own per-root diagnostic, not a `--pooled` path; it and its docstring stay as they are.
- **Disclosing a scan gap instead of refusing (row 26).**
- **Unreadable subagent files for other subcommands.** `_read_session_file_partitioned` still skips an unreadable subagent file silently for `include_subagents=True` callers. `--pooled` never reads subagent files through the iterator.
- **Editing PR #1009's DEFER row 3** (`_iter_glob_scoped_sessions` has no `OSError` guard). This revision closes exactly the function it names. Re-evaluate the row for removal after implementation, via `/pr-description`, rather than editing its text in place.
- **A CI-level guard against the chmod-based tests skipping when the runner's effective user ID is root.** Every such test here carries `@pytest.mark.skipif(os.geteuid() == 0, ...)`, as the five existing sites already do (`test_transcript_review_rounds.py:1837, 1859, 1879`; `test_transcript_analysis.py:17476, 20658`). Verification's manual `-ra` check is this PR's mitigation. A durable guard is a candidate follow-up issue.
- **A scan root that is readable but not searchable (`r` without `x`).** Listing it succeeds, but `_dedup_new_project_dirs`'s `is_dir()` on each entry raises `PermissionError`. `Path.is_dir()` re-raises every `OSError` outside `ENOENT`, `ENOTDIR`, `EBADF`, and `ELOOP` (`pathlib.py:44, 872-880`). The pre-revision `glob` path raised the same way. The result is a loud crash, not a silent exclusion or a false refusal, so it stays outside this revision's bug class. A project directory in the same state is covered: row 33 counts its transcripts as gaps.
- **Validating `user-input`'s own `--projects` (row 36).** It reads only `scope.PROJECTS_DIR` through `corpus.iter_sessions`, whose `Path.glob` matching this revision leaves unchanged, so no value it accepts changes meaning.
- **A FIFO or other special file named `*.jsonl`.** `open()` on a FIFO blocks until a writer appears, so a scan that reaches one hangs. This predates the revision: the old `project_dir.glob("*.jsonl")` yielded the same entry, and `read_session_file` opened it the same way. `_failed_transcript_read_is_gap` never runs, because the read never returns. A hang is loud, not a silent exclusion, so it falls outside this revision's bug class and stays an accepted residual.
