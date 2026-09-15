# Repo quality audit — issue triage

## Context

Triage `docs/reports/2026-08-10-repo-quality-audit/findings.md` into separate,
per-finding GitHub issues in the `jcdendrite/claude-config` repo, then retire
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

## Approach

File **nine phase-shaped GitHub issues** covering the eleven sub-units the
2026-08-22 reconciliation still records as open, edit the 2026-08-10 report's
`## Status` section into the durable repo-local index of what is closed and
what is tracked where, then rewrite the #619 body as a pointer list and
close it. No finding is fixed in this plan; the deliverable is the tracking surface
plus one permitted doc edit.

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
  live inside `transcript-analysis.py` — phase 4b's file, now 14 identical
  blocks up from 4. Filing it with the docs work would put a
  `transcript-analysis.py` edit in a docs PR and collide with 4b. It becomes
  its own issue, sequenced before 4b so the dedup happens once rather than
  being copied into new modules.

**The nine issues** (recommended filing order; each body states "Covers:
2026-08-10 repo quality audit, finding N (backlog phase P), baseline
`eb5eae2`" and cites the report section rather than restating the finding):

| # | Draft title | Covers | Notes for the body |
|---|---|---|---|
| 1 | `Phase 2b: extract _lib_repo_root and route all show-toplevel hook call sites through it` | finding 7a | 14 files now, not the report's 12. Overlaps `check-claude-md-length.sh`/`check-skill-length.sh` sites that the 2026-08-22 S2 also flags — note the overlap, do not expand into S2 |
| 2 | `Phase 3: reorganize _lib.sh in place — delimited sections and a header index, no new file` | finding 7b | Must carry the report's four-independent-checks constraint against splitting into a sibling file; now 1,497 lines, no delimiters |
| 3 | `Dedupe transcript-analysis.py's 14 repeated --config-dir argparse blocks` | finding 7d | Route through the existing `_add_project_scope_args()`. Land before issue 5 |
| 4 | `Phase 4a: split test_transcript_analysis.py by subcommand group` | finding 4 | Carries the verification invariant: collected node IDs set-equal before/after via `pytest --collect-only -q`, diffed as a set |
| 5 | `Phase 4b: complete the transcript-analysis.py package split behind a thin entry point` | finding 4 | Reframe from "start the split" to "finish it": the `transcript_analysis/` package already holds 3,235 lines across 7 modules while the entry point grew to 11,069 lines / 33 `cmd_*`. Keeps the hard external-contract constraint (the literal filename is invoked by a hook, five skills, an agent, and the test shim). Also picks up finding 4's residual — the guard message hard-coding `transcript-analysis.py` with no test pinning it. Depends on 4 and 3 |
| 6 | `Fix README.md and docs/hooks.md drift; pin the agent-type claim in test_doc_counts.py` | findings 5a, 5b, 5c | Names the open sub-decision rather than settling it: documenting the three plugin hooks means either widening `docs/hooks.md`'s stated scope (and `test_hook_alignment.py`'s coverage set) or giving plugin hooks their own surface |
| 7 | `sql-query-conventions: attribute EXPLAIN plan vocabulary per backend` | finding 6 | Match the per-backend table pattern the same file already uses for IN-lists. Hook-enforced `/skill-review` |
| 8 | `Defer root CLAUDE.md's secrets rule; fix staff-backend-engineer.md section ordering` | findings 7g, 7f | Two small edits; `/agent-review` applies to the agent file |
| 9 | `ai-instruction-and-memory-files: unify heading scheme and relieve cap pressure` | finding 7i | Separate from issue 7 because the fix may be a restructure, not a renumber — the file sits at 193 of a 200-line cap |

Phase prefixes stay on the four issues that map 1:1 onto a named backlog
phase and are dropped on the rest, since "phase 5" no longer names a single
PR after the split.

**Coverage invariant** (checkable, see Verification): 27 reconciled sub-units
= 11 Fixed + 5 Cleared (no issue) + 11 open, and the 11 open map onto exactly
these 9 issues, with finding 4 alone spanning two.

**`#619`'s new body** is pointers, not status: one line stating it is
superseded and why (an empty umbrella over a partly-landed audit), the report
path, three grouped lists — already landed (phases 1a/#698, 2a/#623, and
phase 6 landed incidentally with no dedicated PR), newly filed and open (the
nine `#N` references, one line each naming what it covers), and
reviewed-no-action (finding 8's 8a-8e, plus 7c/7e/7h fixed) — and a final
line pointing at the report's `## Status` section as the authority for
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
| 4 | 7d leaves phase 5 and is sequenced before 4b, because it edits 4b's file | `[verified: 2026-08-22 findings.md:72 — 14 blocks in transcript-analysis.py]` | row 1 |
| 5 | The report's `## Status` section is edited; nothing else in that file changes | `[verified: docs/reports/README.md:8-14]` | root |
| 6 | The Status section points at the newer reconciliation table rather than restating its 27 rows | `[verified: 2026-08-22 findings.md:52-86]` | row 5 |
| 7 | Status is the single authoritative statement of what is open; the #619 body holds pointers only | `[verified: CLAUDE.md § Engineering Judgment single-source-of-truth rule]` | row 5 |
| 8 | Both reports cite `claude/.claude/skills/...` for findings 6 and 7i, but both skills now live under `claude-skills/skills/` — issue bodies must re-resolve every path at filing time rather than copying baseline citations forward as current | `[verified: Glob this session — claude-skills/skills/sql-query-conventions/SKILL.md and claude-skills/skills/ai-instruction-and-memory-files/SKILL.md]` | G2 |
| 9 | No existing issue duplicates any of the nine, as of the Step 3 searches — but GitHub state drifts, so the duplicate search is re-run immediately before filing, not trusted from plan time | `[verified: Step 3 gh issue list --search per topic, zero relevant hits]` | root |
| 10 | No milestone, project board, or sub-issue hierarchy. Two lighter primitives already cover the phase graph: plain `#N` cross-references in issue bodies express the 4a→4b and 3→4b ordering, and the report's Status section gives a repo-local index that survives GitHub entirely. A board would add a surface nobody else in this repo maintains | — | row 1 |
| 11 | Issue bodies cite the report section and baseline SHA rather than restating finding text, so a later correction has one home | `[verified: findings.md:4-6 pins every citation to eb5eae2]` | root |
| 12 | The 2026-08-22 S2/D3 continuation of finding 1's bug shape is not folded into this issue set | `[verified: 2026-08-22 findings.md:86 — "not a reopening of the (genuinely Fixed) original finding"]` | root |
| 13 | #619 gets its body rewritten and is then closed, rather than left open as a tracker | `[engineer-verified]` | root |
| 14 | No `gh issue create` or `gh issue close` runs until the engineer confirms the full proposed list | `[engineer-verified]` | root |
| 15 | `gh issue close --reason "not planned"` is the closest available label for a split-into-successors close — the work is planned, just elsewhere, so `completed` would be false. The accepted values are not confirmed this session | `[unverified]` | G3 |

**Dispatch split.** One phase, so one `code-writer` dispatch — for the
`findings.md` Status edit only, and only after the issues exist, since the
edit names their numbers. The `gh` operations stay in the parent session:
they are not code-writing, and they sit behind a confirmation gate a
subagent cannot hold. Sequence: confirm the list → file the nine issues →
capture their numbers → rewrite and close #619 → dispatch the Status edit →
`/code-review` → commit → `/ready-for-review` → PR. #619 is retired before
the doc edit so the Status bullet can state its final state as fact.

No open decision is left to the user beyond the confirmation gate in row 14.

## Critical files

- `docs/reports/2026-08-10-repo-quality-audit/findings.md` — **the `##
  Status` section only** (currently lines 17-46). Add a new dated bullet
  group recording: findings 2 and 3 now Fixed (superseding this section's own
  "no other finding had been actioned as of `293ccf3`" claim); 7c and 7h
  Fixed; finding 4 Partially fixed with the package extraction underway;
  backlog phase 6 landed incidentally with no dedicated PR; the eleven
  still-open sub-units and which new issue tracks each; and that #619 is
  retired in favour of those issues. Point at
  `docs/reports/2026-08-22-discovery-audit/findings.md` § "Baseline
  reconciliation — prior report's 8 findings (27 sub-units) at `6291b343`"
  as the authority for per-sub-unit detail rather than restating the table.
  Keep the citation on one line per `.claude/rules/citation-grammar.md`. Do
  **not** edit the Backlog table's phase-6 row, any finding body, or the
  superseded Publication-constraint block — G1 forbids it, and the phase-6
  news goes in Status instead.

  Reuse: the existing Status section's bullet style and its established
  habit of naming the commit plus PR (`484defb` (#623), `13ea55a` (#698));
  the 2026-08-22 reconciliation table as the status source, cited not
  copied; `docs/reports/README.md` for what the permitted edit may contain.

No other repository file changes. The rest of the deliverable is GitHub
state: nine new issues, plus the #619 rewritten body and close.

## Verification

There is no pytest or ruff assertion that proves this work correct — the
deliverable is tracker state plus one doc edit. Two layers:

**Hard precondition.** Present the full nine-issue list (titles plus the
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
- 7a: `grep -rl "show-toplevel" claude/.claude/hooks/*.sh | wc -l`
- 7b: `wc -l claude/.claude/hooks/_lib.sh`
- 7d: count of `--config-dir` `add_argument` blocks in `transcript-analysis.py`
- 4: line count and `cmd_*` count in `transcript-analysis.py`, plus line
  count and module count under `claude/.claude/scripts/transcript_analysis/`
- 5a, 5b, 5c, 6, 7f, 7g, 7i: re-read the cited `file:line` directly rather
  than trusting either report's quoted text

If a re-check shows an item already fixed, drop it from the filing list and
move it to the "already landed" bucket in both the #619 body and
`findings.md`'s Status update instead of filing an issue for it.

**Manual checklist, run after filing:**

1. Nine issues exist and are open; each title matches the confirmed list.
2. Coverage arithmetic holds: every 2026-08-22 reconciliation row marked
   `Status unchanged`, `Status unchanged (worse)`, or `Partially fixed` — 4,
   5a, 5b, 5c, 6, 7a, 7b, 7d, 7f, 7g, 7i, eleven rows — maps to exactly one
   issue, except finding 4 which maps to issues 4 and 5. No row marked
   `Fixed` or `Cleared` maps to any issue.
3. Every issue body names its finding, its backlog phase (or "no phase —
   split out of phase 5"), and the `eb5eae2` baseline, and restates no
   finding text.
4. Every file path in every issue body resolves on `main` today, not merely
   at `eb5eae2` (ledger row 8 — the two skill paths both moved).
5. The two dependency notes are present: issue 5 depends on issues 3 and 4.
6. Issues 2, 4, and 5 each carry their phase's hard constraint (no new file;
   node-ID set-equality; the entry-point external contract).
7. The #619 body lists all nine `#N` references plus the landed/no-action
   groups, and #619 is closed.
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

- **The 2026-08-22 discovery audit's own 30 findings, and its own triage.**
  That report has no umbrella issue and no per-finding issues at all —
  confirmed by search in Step 3. It is strictly larger than this backlog and
  contains higher-value work than several items filed here, notably S2/D3's
  13+ newly-identified unguarded call sites. **Recommended follow-up:** run
  the same triage pass against it. Completing this plan must not be read as
  "the audit backlog is now tracked."
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
