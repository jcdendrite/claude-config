# Discovery audit — methodology

## Context

**Goal:** Produce `findings.md` in this directory — a formally severity-rated, security-inclusive discovery audit of this repo, applying an external audit-methodology playbook's S/I/C/SC/D finding taxonomy and severity rubric (restated in repo-appropriate terms, not quoted verbatim — see "Category taxonomy" and "Severity rubric" in `findings.md`), landed in this repo's own `docs/reports/` convention.

`docs/reports/2026-08-10-repo-quality-audit/` already carries a proven, six-domain-auditor self-audit methodology, but that audit's four specialist reviewers checked the *plan* for correctness, not a dedicated severity-rated finding set — it never produced a formal S/I/C/SC/D-tagged, severity-rubric-scored security pass. This audit closes that gap, treating the 2026-08-10 report as known baseline context rather than re-deriving its findings from scratch.

This audit's driving session ran from a sibling private checkout on the same machine, which required its own worktree carve-out here (see this repo's README "Worktree enforcement" section for why non-read-only git operations require a linked worktree). Fuller planning context — the assumption ledger and alternatives considered — lives in the originating plan, outside this repo.

## Methodology

### Phase A — domain-auditor fan-out (8 dispatches)

One subagent per domain surface, mirroring the 2026-08-10 report's proven six-domain split, extended to cover surfaces that have grown since its baseline (89 commits, 258 changed files since `eb5eae2`). Each auditor was required to tag every candidate finding with an S/I/C/SC/D category and a severity, using the rubric restated in repo-appropriate terms (this is a dev-tooling repo, not a SaaS product with paying users) rather than the source playbook's literal SaaS-oriented wording.

The eight domains and their auditor focus match `findings.md`'s Feature & Codebase Coverage Map exactly (hooks, scripts, skills, agents, instruction surface, docs/repo-meta, tests/CI, evals/plugins-meta) — see that table for the per-domain focus and the resulting finding list; it is not repeated here.

Plan files (`.claude/plans/`, 206 committed files) did not get their own dispatch — per the originating plan's explicit exception to the source playbook's literal "read every function/handler" method, 206 short-lived, already-merged implementation-plan files are a different kind of surface than live product code. They were sampled by filename cluster and cross-reference instead; see `findings.md`'s "What was not verified" section.

**Alternatives considered:** running the source playbook's literal category-first order (one full-repo pass for S, then I, then C, ...) was rejected — at 571 tracked files / ~196.5K lines, five full passes over the same population is prohibitively expensive, and the 2026-08-10 audit already proved a domain-first split works at this scale. Escalating to the `Workflow` tool for deterministic multi-agent orchestration was considered and rejected: the user had not opted into multi-agent orchestration for this task, and plain parallel `Agent` dispatch matches the prior audit's proven shape without adding heavier orchestration machinery.

### Phase A' — baseline reconciliation (1 dispatch)

A dedicated subagent re-verified each of the 2026-08-10 report's 8 findings (27 reconciled sub-units) against the current baseline, using the source playbook's re-audit status vocabulary (Fixed / Partially fixed / Superseded / Cleared / Status unchanged / Not re-verified). Full detail is folded into `findings.md`'s "Baseline reconciliation" section (see also "Out of scope" below).

### Consolidation (between Phase A and Phase B)

The 8 domain-auditor reports plus the baseline reconciliation were merged into one candidate-findings list, deduplicated, and assigned sequential per-category IDs (this is the first audit to apply the S/I/C/SC/D taxonomy to this repo, so numbering starts fresh at S1/I1/C1/SC1/D1 rather than continuing the 2026-08-10 report's plain 1–8 scheme).

### Phase B — specialist adversarial verification (4 dispatches)

The specialist reviewer agents shipped in `claude/.claude/agents/` — `ciso-reviewer`, `staff-platform-engineer`, `staff-sdet`, `staff-backend-engineer` — each independently verified an assigned slice of the candidate list against live code at the pinned commit, mirroring the 2026-08-10 audit's four-specialist pattern (security, platform, testing, backend) exactly.

| Specialist | Assigned findings |
|---|---|
| `ciso-reviewer` | All S findings (S1-S30), plus D2/D3/D8/D9/D10/D11 (test-coverage companions to specific S findings) |
| `staff-platform-engineer` | All I findings (I1-I5), all SC findings (SC1-SC8), D1, D12, D13, C26 |
| `staff-sdet` | All D findings (D1-D16) in full, plus a sanity-check of any S/C finding whose risk claim hinges on "no test covers this" |
| `staff-backend-engineer` | All C findings (C1-C26), plus a correctness-angle cross-check of S4-S7 and S15-S17 |

Each specialist confirmed the cited `file:line` exists and matches the described behavior, judged whether the assigned severity was justified against the restated rubric, and flagged anything overstated, understated, already mitigated, wrong, or missing context. Full rigor was required for every High-severity claim; file/line existence plus a plausibility check was the floor for Medium/Low/Very Low/N/A claims, matching the quality-review methodology's Pass-2 prioritization convention (see Phase D below).

**Outcome:** of the findings verified, 3 severities were adjusted downward (S8, S11: High→Medium; D1: High→Medium), 2 candidate findings were determined to overstate their claim and were corrected in place (D3's test-coverage scope; S1's affected-command scope), 1 candidate finding (I3) was determined not to be a real gap and is retained in `findings.md` as N/A with the specialists' reasoning rather than silently dropped, and six further citation/count corrections were folded in (SC1, SC6, S30, D7, C14, C18). No finding turned out to be mitigated by a control the domain auditors had missed.

### Phase C — deliverable assembly

`findings.md` and this file were written incorporating every specialist correction. `docs/reports/README.md` was updated with a new row.

### Phase D — quality review

A two-pass quality-review sweep (Pass 1: fresh-read flow/tone; Pass 2: correctness/citation, run twice) ran against the finished `findings.md` before it was treated as done — see the Verification section below for what each pass covered and found.

## Reuse

- `ciso-reviewer`, `staff-platform-engineer`, `staff-sdet`, `staff-backend-engineer` agents for specialist verification — no bespoke specialist prompts needed.
- `docs/reports/2026-08-10-repo-quality-audit/findings.md` and `2026-05-20-code-review-trend-audit/plan.md` as format precedent for this report and this file, respectively.
- The source audit-methodology playbook's finding ID scheme and severity rubric, reused directly with restated (not rewritten) category definitions.
- A companion two-pass quality-review structure, reused as-is for the verification gate.

## Verification

- Every "N of M" style claim in `findings.md` (file counts, test coverage ratios, commit counts) was re-derived live at the pinned commit at write time — not quoted from a subagent's report or the originating plan's own assumption ledger, which itself turned out to disagree with live counts on at least the agent population (13 assumed vs. 12 actual) and the hooks/tests split.
- The quality-review methodology's Pass 1 (fresh-read flow/tone) ran once and Pass 2 (correctness/citation sweep) ran twice independently against the finished `findings.md` and this file. Pass 1 surfaced structural and consistency issues (a Coverage Map that omitted several finding IDs and mismatched its own per-finding domain tags, cross-reference mismatches between findings, inconsistent correction-labeling and terminology) — all folded back into both files. Pass 2's first sweep found several claims that no longer held under direct re-verification (a baseline-reconciliation row describing a defect backwards, a test-count claim off by roughly 6.5×, an internally-impossible plugin-skill count, an overstated "nowhere in the repo" absence claim) plus a handful of citation drifts (a line number, a "lines below" distance, a "both"/"all" count mismatch) — all corrected in place before the second sweep ran.
- `docs/reports/2026-08-10-repo-quality-audit/findings.md` was confirmed untouched — its one permitted edit (the dated `## Status` section) is out of scope for this work.
- `docs/reports/README.md`'s new row matches this report's actual title and path.

## Out of scope

- Fixing any finding — this audit produces findings only, consistent with the source playbook's discipline that the deliverable is the output.
- Filing GitHub issues or otherwise linking findings to a tracker.
- Re-litigating the 2026-08-10 report's 8 findings' severity or validity — only their *current status* was re-verified, not whether they should exist.
- Auditing any uncommitted working-tree state at plan time — scope is git-tracked files at the pinned commit only.
