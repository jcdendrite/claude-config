# Plan: Delegate debug-investigation reads to stop context-limit handoffs

## Context

**Goal: stop the read-heavy debugging investigation from overflowing the parent
context and forcing mid-task `context-limit` handoffs — by delegating the
*investigation* (read-only), while the parent keeps the edit and the judgment.**

Since `check-runner` was retired (2026-06-23, PR #401 / GH-352), the user has
felt friction in real work sessions. Transcript analysis of the named session
("continue billing silent writes handoff" — a private project's implementation
chain of 3 sessions on 2026-06-28, each link broken by a `context-limit`
handoff) plus the post-retirement corpus pinpoints the cause:

- **The context sink is read-heavy *investigation* during debugging, not check
  output.** In the implementation session: file reads + greps to root-cause a
  failure ≈ **244K chars (~61K tokens)** — the dominant consumer by ~10×.
  Check/test run output ≈ **~6K tokens** (negligible; the harness truncates at
  30 KB and the agent was already `grep`/`tail`-trimming). Test *authoring*
  edits ≈ **2.3K chars** (negligible). The session auto-compacted **3×** and
  handed off **before it could commit or ship**.
- **A follow-on session re-paged large spilled files**: a 106 KB `/code-review`
  diff read **5×**, a 68 KB verify output read **6×** — an independent,
  avoidable sink.

This confirms the user's instinct that check-runner was the wrong approach (it
targeted check-*output* volume, which was never the sink), and resolves the
three options the user floated:

| Option floated | Verdict |
|---|---|
| Revive check-run delegation | **No** — inline checks are cheap (harness-truncated). |
| Delegate debugging | **Yes — but only the read-only investigation.** |
| Instruct parent to write tests to a file | **No** — test-authoring is ~2.3K chars, a non-problem. |

The intended outcome: a real implementation+debug+review cycle fits in one
context far more often, because the 61K-token investigation read-load is
returned to the parent as a ~2K-token diagnosis instead of accumulating.

## Approach

Refine the existing **`subagent-delegation`** skill
(`claude/.claude/skills/subagent-delegation/SKILL.md`) — its canonical home —
with two surgical changes. No new agent, no new hook, no new skill.

### Change A — Carve out the debug-investigation loop as a delegation trigger

The skill currently pushes debugging *inline*: its "stays inline" list reads
*"Output you must reason over line by line (a failure you are debugging, a diff
you must design against)."* That conflates two distinct things:

- **Stays inline (correct):** the failure output itself, and the fix you design
  against it — parent-grade, line-by-line reasoning.
- **Should delegate (the actual sink):** the read-heavy probe to *understand*
  the failure — "find how existing tests handle X", "locate the convention",
  "map the analogous pattern". This is locate-and-report work that returns a
  compact answer, identical in shape to the discovery work the skill already
  delegates.

The edit will:
1. **Tighten the stays-inline line** so it scopes to the failure output / the
   diff you design against — *not* the investigation around it.
2. **Add a short debug-investigation paragraph**: when root-causing a check/test
   failure requires a read-heavy probe (multiple reads/greps to find why it
   fails, the relevant convention, the analogous pattern), dispatch that probe
   as an *objective* — "diagnose why X fails; report root cause + minimal
   evidence + proposed fix" — to `general-purpose` (`model: sonnet`) or
   `Explore`. The parent reasons over the **returned diagnosis**, designs the
   fix, and applies the edit + re-runs the check **inline**. This preserves the
   line-by-line reasoning (now over the compact diagnosis, not 40 raw files) and
   keeps editing agency in the parent.
3. Reference the `root-cause-analysis` skill for the diagnosis discipline the
   dispatched objective should follow (establish the full symptom first).

**Why read-only, not a debug-and-fix agent (lighter-alternatives).** A
write-capable autonomous debugger is the heavier primitive and re-introduces the
exact model-agency failure class that retired check-runner — see
`docs/case-studies/check-runner.md` Incident 1 ("modified production source
while fixing a test it was verifying") and the retirement sample ("modified the
migration file instead of just running checks"), which "remained live" through
six hardening rounds. Lighter primitives that solve the problem without it:
- **Read-only `general-purpose`/`Explore` investigation** (chosen) — returns a
  diagnosis; zero write surface; reuses existing agents.
- **`root-cause-analysis` skill inline** — already exists for the diagnosis
  discipline; the gap is only *where* the read-load runs, which delegation
  fixes.
A debug-and-fix agent would need `Write`/`Edit` and longer autonomy — a *bigger*
blast radius than check-runner ever had — and is the "compounding defensive
layers / over-powered primitive" tell the global CLAUDE.md flags.

### Change B — Re-paging discipline for persisted large output

Generalize the skill's existing >30 KB-spill paragraph (currently: "`grep` the
persisted file for the runner's own summary lines … do not re-run the suite") to
cover **any** persisted large output (suite spill, a redirected command's
output, a captured `/code-review` diff), and add the missing rule: **do not
re-`Read` the whole persisted file on later turns — each whole-file read
re-bills; `grep`/`sed` the slice you need.** This directly addresses the
106 KB-diff-read-5× / 68 KB-output-read-6× pattern.

### Deliberately unchanged

- **`claude/.claude/CLAUDE.md`** stays as-is. Its "Default-consider delegation"
  bullet already names "beginning a Read-heavy probe" and defers detail to the
  skill — single-source-of-truth keeps the detail in the skill only.
- **`docs/case-studies/check-runner.md`** is a preserved historical record
  (CLAUDE.md scope Axis 3) — not edited.

## Critical files

- **`claude/.claude/skills/subagent-delegation/SKILL.md`** — the only
  substantive edit (Changes A + B). Reuse, do not reinvent:
  - The existing **"Codebase discovery → `Explore` / `general-purpose`"** section
    (lines ~95–120) already encodes locate-and-report delegation; Change A's
    debug-investigation paragraph is a named special case of it, written to
    cross-reference rather than restate.
  - The existing **>30 KB-spill paragraph** (lines ~89–93) is extended in place
    for Change B, not duplicated.
  - Keep additions tight (the skill argues for brevity/progressive disclosure);
    aim near net-neutral by tightening the stays-inline line as you add the
    carve-out.
- **Frontmatter `description`** — verify the TRIGGER/DO-NOT-TRIGGER lines don't
  *exclude* the new case: the current DO-NOT-TRIGGER "output requiring
  line-by-line reasoning" must be re-scoped to "the specific failure output /
  diff you reason over" so it no longer swallows the delegable investigation.
  Treat any description edit as high-blast (loaded every session) and minimal.
- **Sibling-site reconciliation (required, do before finalizing the edit).**
  Change A re-scopes the "a failure you are debugging → stays inline"
  carve-out. Before committing, `git grep` for other sites that restate that
  guidance and reconcile them to the refined rule (single-source-of-truth — a
  stale restatement would now contradict). Search at least: the
  `root-cause-analysis`, `test-evaluation`, and `code-review` skills,
  `claude/.claude/CLAUDE.md`, and the rest of `claude/.claude/skills/**`. Probe
  terms: `debugging`, `inline`, `line by line`, `failure you are`,
  `reason over`. Expected outcome: detail stays only in `subagent-delegation`;
  any sibling either defers to it or is left untouched if it addresses a
  genuinely different concern (record which, in the PR description).
- **`docs/design-decisions.md` — INCLUDE (decided).** Add a short entry
  recording the decision (investigation-delegation over a debug-and-fix agent),
  grounded in the measurement, mirroring the §10 check-runner entry — it is the
  durable home for "why the lighter primitive," the same role §10 plays for
  check-runner. Must be redacted (no project names; cite the pattern
  generically: "a 3-session chain that compacted 3× and handed off at
  context-limit before commit; investigation reads dominated check output
  ~10:1"). Keep it to a short paragraph; no incident narrative (there is none —
  this is a measurement-driven decision, not a postmortem).

## Verification

1. **Self-review the diff against the skill's own rules** (contributor rule):
   re-read the skill body with the diff in mind — does the addition respect the
   brevity/DRY the skill enforces?
2. **`/skill-review`** on the SKILL.md diff — hook-enforced
   (`require-skill-review.sh` blocks commit until the behavioral-equivalence
   marker is written). Confirm the description change preserves routing behavior.
3. **`/code-review`** on the staged diff (dispatches `/skill-review` for
   SKILL.md automatically).
4. **Test suite + lint** from a worktree: `../../../.venv/bin/pytest
   claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/`. No
   behavioral hook/script change is expected, so this is a regression guard.
5. **Behavioral spot-check (manual):** re-read the edited skill as if mid-debug —
   confirm it now routes the "understand why this fails" probe to a subagent
   while keeping the failure output, fix design, and edit inline. No automated
   `claude -p` harness (per the CI-eval-harness-tradeoffs decision); manual
   smoke test only.
6. No new enforcing test: the delegation heuristic is a judgment cue, not a
   hook-enforceable convention, so the "add test enforcement for new
   conventions" rule does not apply here — note this in the PR.

## Out of scope

- **The behavioral-dispatch / `run_skill_evals.py` friction** (claude-config
  meta-work: "why can't you run the behavioral eval" — needs API budget +
  `--dangerously-skip-permissions` + explicit authorization). That is the eval
  *instrument's* design, not the debug loop; separate effort.
- **Reviving any form of check-*run* delegation** — settled by the retirement.
- **A standalone debugging skill or agent** — rejected above; would duplicate
  `subagent-delegation`'s domain and/or re-introduce check-runner's failure class.
