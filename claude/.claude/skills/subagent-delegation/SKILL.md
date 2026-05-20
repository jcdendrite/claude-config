---
name: subagent-delegation
description: >
  When to dispatch work to a subagent vs run inline.
  TRIGGER when: running a full check suite or full-project
  verification; starting a broad codebase search; running 2nd/3rd
  Bash command toward same question; delegating implementation work;
  reporting test counts from check-runner. DO NOT TRIGGER when: a
  single targeted lookup; a comprehension read feeding your own
  writing/review/design; Edit/Write sequences; or output you must
  reason over line by line.
---

# Subagent delegation

The parent session's context is the expensive resource — it is re-read
on every turn, for the rest of the session, so verbose tool output left
there is paid for again and again. Treat a subagent as a function call:
the parent keeps the *return value*, not the callee's stack.

## Step 1 — The two-test gate

Before running a `Bash` command — or starting a sequence of them —
toward a question, apply two tests:

- **Output test:** will my reasoning consume this command's *output*, or
  only a *conclusion drawn from it*? Conclusion-only ⇒ the output is
  scratch.
- **Judgment test:** does choosing this command, and the next one after
  seeing its result, need parent-grade judgment? If a cheaper model
  could run the loop, it should.

Both pointing to delegate ⇒ dispatch the **objective** — not the
individual command — to a subagent: "find out X; report findings." The
subagent runs the whole probe loop in its own context; the parent gets
back the findings.

**Operational trigger:** when you notice you are about to run the
*second or third* `Bash` command toward the same question, stop —
dispatch the question instead of continuing inline.

**Stays inline — do not over-delegate:**

- A single targeted lookup; one `grep` for a known symbol; one `Read`
  of a known path.
- A comprehension read whose content feeds your own writing, review, or
  design.
- `Edit`/`Write` sequences (judgment-dense, not scratch).
- Output you must reason over line by line (a failure you are
  debugging, a diff you must design against).

**No permission cost.** A subagent runs under the parent's permission
mode. Under auto mode, its read-only diagnostics are evaluated by the
same classifier that clears the parent's — delegating read-heavy work
adds no permission prompts and needs no `permissions.allow` entries.

## Step 2 — Pick the right subagent

### Heavy command output → `check-runner`

Use the `Agent` tool with `subagent_type: check-runner` to run the
checks (full test suites, lint, typecheck, build) — not `Bash` in the
parent directly.

- **Enumerate the exact command strings in the dispatch prompt** (e.g.
  "Run these commands: `pytest claude/.claude/`, `ruff check
  claude/.claude/`") — not "run the checks" or "run the suite".
- **The dispatch prompt must include the absolute working directory**
  (e.g. `Working directory: /absolute/path/to/worktree`). check-runner
  has no guaranteed cwd; directory-sensitive commands run from the
  wrong tree produce misleading failures.
- **Do not enumerate setup or state-mutating commands** in the list —
  db resets, migrations, container start/stop, seed scripts, package
  installs — perform setup yourself before dispatching.
- Commands scoped to a single test file or single test name during
  interactive debugging can stay inline.

The subagent writes full output to
`${TMPDIR:-/tmp}/<command-slug>-<epoch-ms>.txt` and returns a structured
verdict plus the file paths; the parent reads the file for more detail
rather than re-running.

**Reporting test counts.** check-runner's verdict carries no test
counts and no per-sub-suite breakdown — on a passing run it surfaces
nothing but exit codes. To tell the user how many tests passed, or a
per-type breakdown, do not quote a number from the verdict or state one
from memory — `grep` the spool file for the runner's own summary lines
(e.g. `grep -E '(Test Files|Tests|passed|failed)' <spool>`) and quote
those. A `grep` over the full spool recovers every sub-suite's verbatim
totals in a few dozen lines — context-cheap, unlike reading the whole
spool back.

**A lock your session holds never blocks a subagent you dispatch.** A
subagent (Task/Agent tool) inherits the parent's `session_id`: to any
hook or single-instance-resource guard keyed on session identity, a
dispatched `check-runner` is the *same* session, not a competing one.
Holding such a lock is therefore never a reason to run a check suite
inline — dispatch `check-runner` as normal; its same-session calls
clear the guard. Only a genuinely separate concurrent `claude` process
counts as a different session. Do not pre-emptively skip the dispatch
on a guess that the subagent will be blocked — if one ever genuinely
is, it surfaces as the `HOOK_BLOCK` verdict handled just below.

**BLOCKED check handling.** If the subagent reports a check was
`BLOCKED`, do NOT fall back to running it directly in the parent — that
defeats the dispatch (parent context inhales the output) and silently
bypasses the gate that stopped it. Branch on the `block_type` the
verdict carries:

- `SETTINGS_DENIAL` — a missing or declined permission rule. Before
  recommending an allow-rule, confirm an existing rule does not already
  cover the command (don't propose a dead rule). Surface the exact rule
  needed — exact-match, not a glob, e.g. `Bash(npm run verify)` — and
  wait for the user to pre-approve it in the appropriate scope's
  `permissions.allow` or run the command themselves.
- `HOOK_BLOCK` — a PreToolUse hook blocked the call; this is not a
  settings gap. Diagnose from the hook's verbatim stderr in the
  verdict; do not add an allow-rule.
- `UNKNOWN_BLOCK` — surface the verbatim message and ask the user; an
  interactive-prompt decline can land here. Do not guess a remediation.

### Codebase discovery → `Explore` or `general-purpose`

When you need to *locate* something — where a symbol is defined, which
files reference an identifier, broad `grep`/`glob` sweeps, exploratory
reads mapping an unfamiliar area — dispatch it to a subagent rather
than running it inline:

- `subagent_type: Explore` for locate-style search (reads excerpts, not
  whole files).
- `general-purpose` when the exploration must read whole files (as
  `/plan-it` and `/plan-review` do). Always pass an explicit `model`
  on `general-purpose` — it has no model of its own and inherits the
  parent's, which under auto mode may be Opus.

Discovery output inhales into the parent context exactly like a check
suite does, and an auto-mode parent on Opus pays that in the most
expensive tokens — for output it only needed an *answer* from. A single
targeted lookup — one `grep` for a known symbol, one `Read` of a known
path — stays inline; dispatch when the search is broad or spans more
than ~3 queries.

This does not apply to *comprehension* reads: when you need a file's
content in your own reasoning — to write or modify it, review it, or
design against it — read it directly. The split is locate-and-report
(delegable) vs. read-and-reason (not).

### Implementation work → `code-writer`

When delegating code-writing — feature code, fixes, refactors,
migrations, schema, scripts — dispatch the `code-writer` subagent,
not `general-purpose`. It carries `model: sonnet` and self-reviews its
own diff against staff-engineer reviewer angles before returning,
catching review-finding-class defects in its own context instead of as
a parent round-trip.

### Everything else → `general-purpose`

The two-test gate above covers every other case: multi-step
diagnostics, log correlation, verbose `git diff` / state-survey bursts.
Dispatch the objective — not the commands — to `general-purpose` with
an explicit `model: sonnet` (a no-op when the parent is already
Sonnet; keeps the dispatched work off Opus when the parent is not).
