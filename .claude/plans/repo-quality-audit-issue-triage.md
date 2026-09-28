# Repo quality audit — issue triage

## Context

Triage `docs/reports/2026-08-10-repo-quality-audit/findings.md` into separate
GitHub issues in the `jcdendrite/claude-config` repo, then retire
the monolithic umbrella issue [#619](https://github.com/jcdendrite/claude-config/issues/619)
("Triage and fix findings from 8/10/26 repo quality audit"), which today has
no body content beyond a pointer to that doc — no per-finding breakdown, no
tracking of which findings already landed.

This task was handed off from a prior session via a continuity file, as a
fresh task separate from that session's actual work (which was unrelated and
already merged). While exploring the findings doc, this session found that a
newer report — `docs/reports/2026-08-22-discovery-audit/findings.md` — ran its
own baseline reconciliation of the 2026-08-10 report's 8 findings and is more
current than that report's own 2026-08-18 Status note. Several findings the
original Status note still calls open (findings 2 and 3, and finding-7
sub-items 7c/7e/7h) are actually Fixed; that corrected picture drives this
plan's scope.

Intended outcome: #619 closed with its body rewritten to link out to
newly-filed issues covering every genuinely-still-open item, plus a corrected
`## Status` section in the 2026-08-10 report reflecting the 2026-08-22
reconciliation.

A 2026-09-27 re-check against current `origin/main` (65+ commits since the
2026-08-22 reconciliation's baseline) found a new epic, #1113 ("Review
quality: reviewer read methodology and oversized code files"), filed
2026-09-26 with children #1114–#1119. Two of those children already track
work this plan's original nine-issue draft would otherwise have filed as new
issues: #1116 tracks finding 4 (backlog phases 4a and 4b, the
`transcript-analysis.py` split), and #1117 tracks finding 7b (backlog phase 3,
the `_lib.sh` reorganization). This revision drops those three sub-units from
the filing list and cross-references the pre-existing issues instead.

## Approach

File **six phase-shaped GitHub issues** covering nine of the eleven sub-units
the 2026-08-22 reconciliation still records as open — the other two
(finding 4 and finding 7b) are already tracked by pre-existing issues #1116
and #1117, children of epic #1113 — edit the 2026-08-10 report's `## Status`
section into the durable repo-local index of what is closed and what is
tracked where (eight tracking issues total), then rewrite the #619 body as a
pointer list and close it as "not planned". No finding is fixed in this plan;
the deliverable is the tracking surface plus one permitted doc edit.

**Why phase-shaped rather than per-finding.** An issue's job is to track one
unit of work to closure, and the closing artifact here is a PR.
`docs/reports/2026-08-10-repo-quality-audit/findings.md:317` states "Each
phase is an independent PR," so the backlog's phases already are the
mergeable units, and each carries ordering rationale and hard constraints
(phase 3's "must not create a new file"; phase 4a's node-ID set-equality
invariant) that belong in exactly one issue body. Per-finding issues fail on
three counts: finding 7 would become one issue spanning four PRs across three
subsystems, findings 1/2/3 and sub-units 7c/7e/7h would get issues for work
already done, and finding 8 is explicitly "not defects." So the spine is
phases.

**Why the spine is not applied purely.** Phases and findings do not align
1:1, and two places break the mapping:

- **Phase 6 gets no issue.** Findings 2 (CI never runs `plugins/` tests) and 3
  (`persist-credentials: false`) are both Fixed —
  `.github/workflows/tests.yml:160,166,170` and `:52-55` per the 2026-08-22
  reconciliation. Phase 6's work landed incidentally in CI restructuring with
  no dedicated PR, which is why the 2026-08-10 report's own Status note still
  calls it open.
- **Phase 5 splits four ways.** It bundles findings 5, 6, and three finding-7
  sub-units across `README.md`, `docs/hooks.md`, `test_doc_counts.py`, a
  stowed `SKILL.md`, an agent file, and root `CLAUDE.md`. That is not one
  reviewable PR, and the seams are real rather than aesthetic:
  `.claude/rules/review-pipeline-dispatch.md:16-26` makes a `SKILL.md` edit
  drag a hook-enforced `/skill-review` and an agent-file edit drag
  `/agent-review`, so bundling a skill-body rewrite with README copy edits
  forces one behavioral-equivalence review over unrelated changes. Splitting
  preserves the "independent PR" property rather than violating it — the
  report says phases are independent, not indivisible.
- **7d moves out of phase 5.** The duplicated `--config-dir` argparse blocks
  live inside `transcript-analysis.py` — phase 4b's file, now 15 identical
  blocks up from 4. Filing it with the docs work would put a
  `transcript-analysis.py` edit in a docs PR. It becomes its own issue, but no
  longer sequenced before phase 4b: phase 4b is now #1116, outside this plan,
  and `transcript-analysis-decomposition.md:139,155` keeps `build_parser()` in
  the shim until the final `cli.py` phase, so no `transcript_analysis/` module
  registers argparse args yet for it to collide with. Cross-reference #1116
  in the body instead of sequencing against it, and land this issue early —
  the block count keeps growing (4 → 14 → 15) the longer it waits.

Three issues the original nine-issue draft would have filed — phase 3
(finding 7b), phase 4a and phase 4b (both finding 4) — are dropped
from the filing list. Epic #1113, filed 2026-09-26 with children #1114–#1119,
already tracks them more comprehensively than a fresh issue would: #1117 is
phase 3 verbatim, citing the same 2026-08-10 audit line and correcting the
audit's own stale objections to splitting `_lib.sh` into a sibling file
(`_config.sh` shipped as a hook-side sibling since the audit, answering 3 of
its 4 stated objections); #1116 is phases 4a and 4b together, with phase 4a
explicitly superseded into 4b's co-move design — the node-ID set-equality
invariant phase 4a would have carried is preserved inside #1116's and
#1118's acceptance criteria instead of a standalone split-first issue. Filing
new issues for either would create direct duplicates.

**The six issues** (recommended filing order; each body states "Covers:
2026-08-10 repo quality audit, finding N (backlog phase P), baseline
`eb5eae2`" and cites the report section rather than restating the finding):

| # | Draft title | Covers | Notes for the body |
|---|---|---|---|
| 1 | `Phase 2b: give _lib_repo_root a directory argument and route the 15 hook show-toplevel sites through it` | finding 7a | `_lib_repo_root()` (`_lib.sh:432-437`) takes no argument today and resolves ambient cwd; 14 of the 15 hook call sites already resolve against the payload's `.cwd` (`git -C "$CWD"` / `cd "$CWD" &&`), only `deny-private-project-refs.sh:370` uses ambient cwd. Give the helper an optional directory argument — keep the no-arg form byte-identical, since `marker.sh:139` and `pr-diff-against-base.sh:116,148` depend on it for REPO_HASH agreement, and mirror the same shape inside `_lib.sh:2522`. Routing through a bare no-arg helper would reintroduce 2026-08-22 S3's bug shape (fixed by #704). Separately, 4 of the 15 sites are uncapped (`advance-past-commit-stall.sh:184`, `require-stow-reminder.sh:113`, `deny-private-project-refs.sh:370`, `require-worktree-for-file-writes.sh:131`; `require-ready-for-review.sh:360` is already capped) — routing those 4 closes 2026-08-22's S2 for those lines only, say so without expanding this issue into S2's scope. Not attached to #1113; no ordering vs #1117. Implementer confirms `git -C dir` vs `cd dir &&` toplevel byte-identity for symlinked paths before landing. Cross-reference `.claude/plans/discovery-audit-remediation-plan.md`'s Phase 1b |
| 2 | `Dedupe transcript-analysis.py's 15 subcommand-level --config-dir argparse blocks` | finding 7d | 15 `action="append", dest="extra_config_dirs"` blocks (`transcript-analysis.py:11786`–`:12656`); the top-level `--config-dir` at `:11720` replaces rather than appends and stays separate. Reject folding into `_add_project_scope_args()` — 34 call sites vs 15, would add `--config-dir` to subcommands that don't want it, change the CLI surface, and flip `main()`'s `hasattr(parsed, "extra_config_dirs")` branch at `:12848` that cost-counting relies on. Prescribe a sibling helper with a help-text parameter instead, since help text differs at `:12389` and `:12464`. No sequencing vs #1116 — `transcript-analysis-decomposition.md:139,155` keeps `build_parser()` in the shim until the final `cli.py` phase, and no `transcript_analysis/` module registers argparse args yet — but keep a cross-reference to #1116. Land early: the block count keeps growing (4 → 14 → 15) |
| 3 | `Fix README agent-types claim and docs/hooks.md plugin-hook scope drift; pin the agent-type claim in test_doc_counts.py` | findings 5a, 5b, 5c residual | Keep 5b in scope: `docs/hooks.md:3` calls the threat-model tier table "the one exception" to main-dir scope, but `:115` (also `:100`, `:226`, `:303`) carries a full Gate-hooks bullet for plugin-resident `require-skill-review.sh`, outside that stated exception. Narrow 5c to `consume-migration-token.sh` only — the other two undocumented hooks are now documented at `docs/hooks.md:82-83`. Name the open sub-decision rather than settle it: whether the tier table's widening extends to the Gate-hooks/Utility-hooks sections too, and where `consume-migration-token.sh` goes given only two gate hooks got tier rows |
| 4 | `sql-query-conventions: attribute EXPLAIN plan vocabulary per backend` | finding 6 | Match the per-backend table pattern the same file already uses for IN-lists (`:65-71`). Re-verify the EXPLAIN vocabulary's current line range at filing time. Hook-enforced `/skill-review` |
| 5 | `Defer root CLAUDE.md's do-not-commit restatement to the global rule; fix staff-backend-engineer.md section ordering` | findings 7g, 7f | Only the do-not-commit restatement defers to the global `CLAUDE.md` bullet — root `CLAUDE.md`'s Secrets section also carries a repo-specific incident-response rule (tell the owner in-session, never rotate or rewrite history yourself) that has no global equivalent and stays as-is. Two small edits; `/agent-review` applies to the agent-file ordering fix |
| 6 | `ai-instruction-and-memory-files: unify heading scheme and relieve cap pressure` | finding 7i | Separate from issue 4 because the fix may be a restructure, not a renumber — the file sits within a few lines of its 200-line cap; re-measure at filing time |

Tracked by pre-existing issues, not filed here: #1116 (finding 4, phases
4a+4b; phase 4a superseded into 4b's co-move design) and #1117 (finding 7b,
phase 3), both children of epic #1113. #1118 tracks a different set of
oversized test files and is not a mapping target for this plan.

Phase prefixes stay on the one new issue that maps 1:1 onto a named backlog
phase (issue 1, phase 2b) and are dropped on the rest, since phases 3, 4a,
and 4b are now tracked by #1117 and #1116 rather than by issues from this
plan, and "phase 5" never named a single PR after the split.

**Coverage invariant** (checkable, see Verification): 27 reconciled sub-units
= 11 Fixed + 5 Cleared (no issue) + 11 open, and the 11 open rows map onto
exactly 8 tracking issues — the six new issues above plus #1116 and #1117 —
each row exactly one issue; finding 4 maps to #1116 alone.

**`#619`'s new body** is pointers, not status: one line stating it is
superseded and why (an empty umbrella over a partly-landed audit), the report
path, four grouped lists — landed (phases 1a/#698, 2a/#623, phase 6 landed
incidentally with no dedicated PR, 7c/7e/7h fixed, and finding 4's
guard-message pin), tracked by pre-existing issues (#1116, #1117, both
children of #1113), newly filed (the six `#N` references, one line each
naming what it covers), and reviewed-no-action (finding 8's 8a-8e) — and a
final line pointing at the report's `## Status` section as the authority for
per-sub-unit status. GitHub renders `#N` references with live title and
state, so the body never restates status it would then have to maintain.

**Assumption ledger**

Root: #619 is an empty umbrella over an audit that is now 11 sub-units fixed,
5 cleared, and 11 open, so no reader can tell what remains; the fix is one
issue per remaining unit of work plus one durable repo-local index, after
which #619 is retired.

Givens:
- **G1 — The 2026-08-10 report's findings and backlog table are frozen; only
  a dated `## Status` section may be edited.** `docs/reports/README.md:8-14`
  sets this as repo-wide documentation policy; changing it is a decision
  outside this plan.
- **G2 — The 2026-08-22 reconciliation's per-sub-unit statuses are
  authoritative.** Re-deriving 27 statuses from live code is a second audit,
  not a step in this plan.
- **G3 — GitHub's close-reason vocabulary is whatever `gh` offers.**
  Vendor-imposed; the plan picks from it rather than modelling supersession
  some other way.

| # | Assumption | Tag | Anchors |
|---|---|---|---|
| 1 | Phase-shaped issues are the right granularity because phases are the mergeable unit and carry the per-phase constraints | `[verified: findings.md:317 and the backlog table at :319-328]` | root |
| 2 | Phase 6 needs no issue — findings 2 and 3 are both Fixed | `[verified: 2026-08-22 findings.md:62-63, citing tests.yml:160,166,170 and :52-55]` | row 1 |
| 3 | Phase 5 decomposes four ways along review-pipeline and file-surface seams | `[verified: .claude/rules/review-pipeline-dispatch.md:16-26]` | row 1 |
| 4 | 7d leaves phase 5 as its own issue but is no longer sequenced before phase 4b, since that work is now tracked by #1116 outside this plan; land early instead, since the block count keeps growing | `[verified: transcript-analysis.py:11786–:12656, 15 blocks; transcript-analysis-decomposition.md:139,155 — build_parser() stays in the shim until the final cli.py phase]` | row 1 |
| 5 | The report's `## Status` section is edited; nothing else in that file changes | `[verified: docs/reports/README.md:8-14]` | root |
| 6 | The Status section points at the newer reconciliation table rather than restating its 27 rows | `[verified: 2026-08-22 findings.md:52-86]` | row 5 |
| 7 | Status is the single authoritative statement of what is open; the #619 body holds pointers only | `[verified: CLAUDE.md § Engineering Judgment single-source-of-truth rule]` | row 5 |
| 8 | Both reports cite `claude/.claude/skills/...` for findings 6 and 7i, but both skills now live under `claude-skills/skills/` — issue bodies must re-resolve every path at filing time rather than copying baseline citations forward as current | `[verified: Glob this session — claude-skills/skills/sql-query-conventions/SKILL.md and claude-skills/skills/ai-instruction-and-memory-files/SKILL.md]` | G2 |
| 9 | No existing issue duplicates any of the six, as of this session's searches — but GitHub state drifts, so the duplicate search is re-run immediately before filing, not trusted from plan time | `[verified: this session's per-issue gh issue list --search, zero relevant hits beyond #1116/#1117/#1118, already excluded above]` | root |
| 10 | No milestone, project board, or sub-issue hierarchy. Two lighter primitives already cover what ordering remains: plain `#N` cross-references (issue 1 → `discovery-audit-remediation-plan.md`'s Phase 1b; issue 2 → #1116, soft, non-sequencing), and the report's Status section, which gives a repo-local index that survives GitHub entirely. A board would add a surface nobody else in this repo maintains | — | row 1 |
| 11 | Issue bodies cite the report section and baseline SHA rather than restating finding text, so a later correction has one home | `[verified: findings.md:4-6 pins every citation to eb5eae2]` | root |
| 12 | The 2026-08-22 S2/D3 continuation of finding 1's bug shape is not folded into this issue set | `[verified: 2026-08-22 findings.md:86 — "not a reopening of the (genuinely Fixed) original finding"]` | root |
| 13 | #619 gets its body rewritten and is then closed, rather than left open as a tracker | `[engineer-verified]` | root |
| 14 | The confirmation gate is satisfied: the engineer selected "Yes, as listed (Recommended)" — file the six issues, then rewrite and close #619 as "not planned", after revising this plan and running `/plan-review` | `[engineer-confirmed]` | root |
| 15 | `gh issue close --reason "not planned"` is the closest available label for a split-into-successors close — the work is planned, just elsewhere, so `completed` would be false. "duplicate" implies one target, and #1113 covers only 2 of the 11 open rows | `[verified: gh issue close --help this session — accepted values are completed, not planned, or duplicate]` | G3 |
| 16 | Findings 7b (phase 3) and 4 (phases 4a+4b) are tracked by pre-existing issues #1117 and #1116, both children of epic #1113 filed 2026-09-26, rather than by new issues from this plan | `[verified: #1117's and #1116's bodies, both citing the same 2026-08-10 audit phases by line number; #1118 is a distinct, non-overlapping tracking target]` | root |
| 17 | `_lib_repo_root` must gain an optional directory argument rather than being called as-is, keeping the no-arg form byte-identical | `[verified: marker.sh:139 and pr-diff-against-base.sh:116,148 depend on the no-arg form for REPO_HASH agreement; same shape inside _lib.sh:2522]` | row 1 |
| 18 | Issue 2 (7d) prescribes a new sibling helper for the `--config-dir`/`extra_config_dirs` pair rather than folding into `_add_project_scope_args()`, since that helper has 34 call sites vs 15, would widen the CLI surface, and would flip `main()`'s `hasattr(parsed, "extra_config_dirs")` branch that cost-counting relies on | `[verified: transcript-analysis.py:11696 (_add_project_scope_args, 34 call sites), :11720 (top-level --config-dir stays separate), :12848 (hasattr branch), :12389 and :12464 (differing help text)]` | row 1 |

**Dispatch split.** One phase, so one `code-writer` dispatch — for the
`findings.md` Status edit only, and only after the issues exist, since the
edit names their numbers. The `gh` operations stay in the parent session:
they are not code-writing, and they sit behind a confirmation gate a
subagent cannot hold. Sequence: `/plan-review` on this revision (the
engineer's confirmation in row 14 is conditioned on it) → file the six
issues → capture their numbers → rewrite and close #619 as "not planned" →
dispatch the Status edit → `/code-review` → commit → `/ready-for-review` →
PR. #619 is retired before the doc edit so the Status bullet can state its
final state as fact.

No open decision is left to the user; the confirmation gate in row 14 is
satisfied.

## Critical files

- `docs/reports/2026-08-10-repo-quality-audit/findings.md` — **the `##
  Status` section only** (currently lines 17-46). Add a new dated bullet
  group recording: findings 2 and 3 now Fixed (superseding this section's own
  "no other finding had been actioned as of `293ccf3`" claim); 7c, 7e, and 7h
  Fixed; finding 4's guard-message residual now Fixed
  (`test_transcript_analysis.py:335` pins the literal); backlog phase 6
  landed incidentally with no dedicated PR; the eleven still-open sub-units
  and which of the eight tracking issues (the six new plus #1116 and #1117)
  tracks each, noting that phase 4a is folded into #1116's co-move design
  rather than tracked standalone; and that #619 is retired in favour of
  those issues. Add a second, separately dated bullet for this session's own
  re-check, pinned to the `origin/main` SHA in use at execution time (not
  `6291b343`) so it reads as newer than the 2026-08-22 table rather than
  merging into it, recording the deltas the re-check surfaced (the
  `_lib_repo_root` helper existing but unrouted; epic #1113's filing; the
  sub-unit-to-issue remap above). Point at
  `docs/reports/2026-08-22-discovery-audit/findings.md` § "Baseline
  reconciliation — prior report's 8 findings (27 sub-units) at `6291b343`"
  as the authority for per-sub-unit detail rather than restating the table.
  Keep each citation on one line per `.claude/rules/citation-grammar.md`. Do
  **not** edit the Backlog table's phase-6 row, any finding body, or the
  superseded Publication-constraint block — G1 forbids it, and the phase-6
  news goes in Status instead.

  Reuse: the existing Status section's bullet style and its established
  habit of naming the commit plus PR (`484defb` (#623), `13ea55a` (#698));
  the 2026-08-22 reconciliation table as the status source, cited not
  copied; `docs/reports/README.md` for what the permitted edit may contain.

No other repository file changes. The rest of the deliverable is GitHub
state: six new issues, plus the #619 rewritten body and close.

## Verification

There is no pytest or ruff assertion that proves this work correct — the
deliverable is tracker state plus one doc edit. Two layers:

**Hard precondition.** Present the full six-issue list (titles plus the
finding/phase each covers) and the proposed #619 body outline to the
engineer, and get explicit confirmation before the first `gh issue create`.
Re-run the duplicate search (`gh issue list --search <topic> --state all`
per issue) immediately before filing, since the Step 3 result is
point-in-time. Confirm `gh issue close --reason` accepts `"not planned"` via
`gh issue close --help` before running it (ledger row 15).

**Freshness re-check, immediately before drafting each issue body.** The
2026-08-22 reconciliation is pinned to commit `6291b343`; both reports'
own headers show high commit velocity (89 commits / 258 files changed in
just the 12 days between the two report baselines), so by execution time it
may be stale. Re-run each finding's own citation command against current
`main` and use the fresh figure, not the reconciliation's, in the issue
body:
- 7a: `grep -l "show-toplevel" claude/.claude/hooks/*.sh | grep -v '/_lib\.sh$' | wc -l`
  (excludes `_lib.sh`, which also matches; note its `_lib.sh:2522` site separately)
- 7d: count of `--config-dir` `action="append", dest="extra_config_dirs"`
  blocks in `transcript-analysis.py`
- 5a, 5b, 5c, 6, 7f, 7g, 7i: re-read the cited `file:line` directly rather
  than trusting either report's quoted text

Also re-confirm, immediately before filing, that #1116 and #1117 are still
open and still cover finding 4 and finding 7b respectively — the mapping
this revision records is a point-in-time read of both issues' current
bodies, not a permanent fact about them.

If a re-check shows an item already fixed, drop it from the filing list and
move it to the "already landed" bucket in both the #619 body and
`findings.md`'s Status update instead of filing an issue for it. If a
re-check instead shows an item already covered by a pre-existing issue,
move it to the "tracked by pre-existing issues" bucket instead.

**Manual checklist, run after filing:**

1. Six issues exist and are open; each title matches the confirmed list.
   #1116 and #1117 remain open and are cross-referenced, not re-filed.
2. Coverage arithmetic holds: re-derive the still-open row count from the
   2026-08-22 reconciliation table's Status column at filing time (11 rows
   as of this reconciliation: 4, 5a, 5b, 5c, 6, 7a, 7b, 7d, 7f, 7g, 7i) and
   confirm each maps to exactly one of the eight tracking issues — the six
   new plus #1116 and #1117 — with finding 4 mapping to #1116 alone and
   finding 7b mapping to #1117 alone. No row marked `Fixed` or `Cleared` maps
   to any issue.
3. Every issue body names its finding, its backlog phase (or "no phase —
   split out of phase 5"), and the `eb5eae2` baseline, and restates no
   finding text.
4. Every file path in every issue body resolves on `main` today, not merely
   at `eb5eae2` (ledger row 8 — the two skill paths both moved).
5. The two soft cross-references are present, neither a hard sequencing
   dependency: issue 2 (7d) cross-references #1116, and issue 1 (7a)
   cross-references `discovery-audit-remediation-plan.md`'s Phase 1b.
6. The phase hard constraints this plan would otherwise have carried
   directly are checked against #1116's and #1117's own bodies instead,
   since those phases are tracked there rather than filed here: #1117
   carries the no-new-file constraint, and #1116 carries the node-ID
   set-equality invariant and the entry-point external contract (the latter
   explicit at `transcript-analysis-decomposition.md:43-44`). Confirm both
   bodies still state them before treating #619 as fully covered.
7. The #619 body lists all four groups — landed, tracked by pre-existing
   issues, newly filed (six `#N` references), reviewed-no-action — and #619
   is closed with reason "not planned".
8. The `## Status` edit is confined to that section — `git diff` shows no
   hunk outside it.

**Repo test command for the doc edit**, scoped per this repo's CLAUDE.md
(agents run the selector, not the full suite):

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

`test_doc_counts.py` is the guard that matters if the selector picks it up,
since the Status edit adds numeric claims. Run `/code-review` before the
commit and `/ready-for-review` before pushing, per CLAUDE.md.

## Out of scope

- **The 2026-08-22 discovery audit's own 30 findings, beyond what
  `.claude/plans/discovery-audit-remediation-plan.md` (#742) already
  covers.** That plan, not this one, is the 2026-08-22 audit's triage; issue
  1 (7a) cross-references its Phase 1b for the S2 overlap rather than
  duplicating it. This plan's scope stays the 2026-08-10 report; any
  2026-08-22 finding outside #742's plan remains untracked and out of scope
  here. It contains higher-value work than several items filed here, notably
  S2/D3's 13+ newly-identified unguarded call sites. **Recommended
  follow-up:** extend #742's plan, or triage the remainder, for whatever it
  doesn't already cover. Completing this plan must not be read as "the
  2026-08-22 audit backlog is now fully tracked."
- **Fixing any finding.** This plan files issues and edits one Status
  section; it lands no remediation.
- **Any edit to the 2026-08-10 report outside `## Status`** — including
  striking the now-complete phase 6 from the Backlog table, correcting stale
  `file:line` citations inside findings, or updating finding 4's line counts
  in its body. The report is a frozen record (G1); corrections go in Status.
- **Back-filling tracking issues for landed phases 1a and 2a.** PRs #698 and
  #623 are merged with no `closingIssuesReferences`; the #619 body notes them
  as landed and nothing more.
- **Relitigating finding 8.** 8a-8e were reconfirmed Cleared at `6291b343`;
  the 8a note about promoting the "supersede, never delete" line to
  `docs/design-decisions.md` is a recorded non-defect, not an issue to file.
- **Linking or referencing issues #973, #472, #879, #845, #844, #869, #993,
  #625, or #489** — all individually ruled out as unrelated in Step 3.
- **Creating new GitHub labels, milestones, or a project board.** Reuse an
  existing label if one fits; otherwise file the issues bare.
