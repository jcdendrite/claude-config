# check-runner: stop the agent volunteering invented sub-suite test counts

## Context

During a `/ready-for-review` run in a downstream project (2026-05-15), the
`check-runner` subagent ran `npm run verify` and reported "Unit tests: PASS
(81 tests passed)" and "Integration tests: PASS (7 tests passed)". Both
numbers were wrong: `npm run verify` is a single command from the agent's
view but internally runs ~7 sub-suites, and the actual totals were ~1396
Vitest + 293 Deno-integration + 81 Deno-unit + 7 Vitest-integration + hook
suites. The agent (a **haiku** model) free-form-summarized a 1.1 MB spool
file with no defined extraction procedure and grabbed two salient-looking
numbers, mislabeling them. The wrong counts were propagated into a PR body
before discovery.

Root cause: the agent spec's return contract never asks for test counts —
it asks for per-command exit code, pass/fail, a failure excerpt, and the
spool path. The agent volunteered a per-suite breakdown that was never part
of its contract, and the haiku model had no grounding procedure for it.

Fix: tighten the spec so the agent returns **only** what its contract
defines — one verdict entry per enumerated command, no per-sub-suite
decomposition, no test counts. If the parent needs counts it reads the
spool file. (This is "Option A" from the investigation brief
`~/.claude/research/check-runner-test-count-misreport.md`; the brief
rejects a grounded count-extraction procedure as fragile across runner
formats and compounding complexity — consistent with the repo's
foundational-scoping-over-hardening philosophy, `docs/design-decisions.md`
§10 and the global CLAUDE.md "Compounding defensive layers" rule.)

## Collision / landing strategy

PR #239 (`worktree-check-runner-charter-and-cwd`, OPEN, mergeable/clean) is
an in-flight check-runner change that edits the **same** `check-runner.md`
and adds `docs/design-decisions.md` §11. To avoid a merge conflict on a
shared file, **fold this fix into PR #239** rather than opening a competing
branch. Implement on the `worktree-check-runner-charter-and-cwd` branch;
both changes are 2026-05-15 check-runner spec tightenings and belong in one
PR. Update PR #239's title/body to cover the added scope.

## Changes

### 1. `claude/.claude/agents/check-runner.md` — add umbrella-command discipline

Base the edit on the **PR #239 version** of the file (it already has the
"Scope: checks only" and "Working directory" paragraphs).

Insert a new bold paragraph immediately **after** the line
`Do not interpret failures or recommend fixes — that is the parent's job.`:

> **Umbrella-command discipline.** A single enumerated command (e.g.
> `npm run verify`) may internally run several sub-suites, each printing
> its own summary block. Return exactly one verdict entry for that command
> — its name, its exit code, its overall pass/fail — never a per-sub-suite
> breakdown. Do not report, total, or characterize test counts; do not
> name or decompose the individual sub-suites. If the parent needs that
> detail it reads the spool file. This does not change the failure-excerpt
> rule above: on a failed command you still quote the smallest excerpt
> verbatim — a verbatim quote is not a synthesized count.

Rationale for placement: the rule is a natural extension of the existing
"Do not interpret failures" line — both say the agent reports, the parent
interprets.

### 2. `docs/design-decisions.md` — append §12

PR #239 adds §11; append §12 after it. Match the §10 format (dated title,
problem paragraph, resolution paragraph):

`## 12. check-runner verdict over-reporting: invented sub-suite counts (2026-05-15)`

Record:
- **Incident**: on a `/ready-for-review` run for PR #224, check-runner
  reported per-suite test counts ("81 unit", "7 integration") that were
  wrong — `npm run verify` runs ~7 sub-suites; actual totals were far
  larger. The wrong numbers reached the PR body before discovery.
- **Root cause**: the return contract never asks for counts; the haiku
  model volunteered a breakdown by free-form-summarizing a 1.1 MB spool
  file and picked salient numbers with no grounding in which runner
  emitted each line.
- **Resolution**: prose-only — an umbrella-command-discipline paragraph
  constraining the agent to one verdict entry per enumerated command, no
  counts, no sub-suite decomposition.
- **Why not a grounded count-extraction procedure**: fragile across runner
  formats (vitest, deno, hook scripts, pytest all differ); an umbrella
  command emits N summary blocks so "the" count needs a per-project lookup
  table; the result is still a haiku model extracting structured data from
  free-form output. Compounding complexity — consistent with §10's
  foundational-scoping-over-hardening stance.

## Deliberately out of scope

- **No CLAUDE.md "Heavy command output" companion note.** Once the agent
  stops volunteering counts, the verdict carries no counts for the parent
  to echo — the source vector is closed. A parent-side note would guard
  only the weaker, separate vector of the parent reading the spool and
  miscounting itself; the existing "the parent reads the file for more
  detail" line already covers that, and CLAUDE.md is loaded every session
  (anti-bloat). Noted here so the omission is visible as a decision.
- The shared-DB / wrong-directory concerns are PR #239's scope, not this
  fix.

### 3. Commit this plan file into the repo

The repo tracks plan files under `claude/.claude/plans/` (5 plans already
committed there; PR #239 commits its own plan,
`check-runner-might-need-more-scalable-zebra.md`). `~/.claude/plans/` is a
separate non-repo directory. To keep PR #239 symmetric — both folded
changes documented the same way — copy this plan to
`claude/.claude/plans/read-claude-research-check-runner-test-c-composed-graham.md`
and include it in the PR.

## Files

- `claude/.claude/agents/check-runner.md` — modify (one new paragraph)
- `docs/design-decisions.md` — modify (append §12)
- `claude/.claude/plans/read-claude-research-check-runner-test-c-composed-graham.md`
  — add (copy of this plan)

All edits land on the `worktree-check-runner-charter-and-cwd` branch
(PR #239).

## Verification

- Run `pytest claude/.claude/` and `ruff check claude/.claude/` via the
  `check-runner` agent — these changes are prose only (agent `.md`, docs),
  no hook scripts, so the suite should be unaffected; confirm green.
- Manual review: re-read the edited `check-runner.md` body and confirm a
  dispatched `npm run verify` would yield a single verdict entry
  (`npm run verify` — exit code — PASS/FAIL) with no per-suite numbers,
  and that the failure-excerpt rule is still unambiguous on a failed run.
- Manual review: confirm §12 matches the §10/§11 format and date.
- Update PR #239's title and body to reflect the added scope before
  handing the PR back for review.
