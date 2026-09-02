---
name: subagent-delegation
description: >
  Dispatch to a subagent vs inline. TRIGGER when: full
  check suite or full-project verification; broad codebase search;
  first exploratory read; 2nd/3rd Bash toward same question; delegating
  implementation; delegating a multi-step gate/review loop.
  DO NOT TRIGGER when: single-artifact targeted lookup (one file or
  value, not a multi-site sweep); comprehension read feeding your own
  writing/review/design; Edit/Write sequences where scope or content
  is still forming; the specific failure output or diff you reason over
  line by line.
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
  scratch — the same locate-and-report vs. read-and-reason split named
  under Codebase discovery below.
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

- A lookup whose result is a single artifact the parent consumes
  directly — one `Read` of a known path, or a `grep` that resolves to
  one value or one yes/no answer (e.g. confirming a symbol is defined
  in exactly one place). The test is result shape, not whether the
  target is known: multi-site lookups (known or unknown target)
  dispatch; single-value lookups stay inline.
- A comprehension read whose content feeds your own writing, review, or
  design.
- `Edit`/`Write` sequences where you are still deciding approach, scope,
  or the substantive content — the judgment is the parent's and the edit
  stays inline. See the **Read-then-edit: decision-made test** in the
  `code-writer` section below for the narrow exception.
- The failure output or diff you reason over line by line — the artifact itself, not the investigation that precedes it.

**No permission cost.** A subagent inherits the parent's permission
mode, so under auto mode its read-only diagnostics clear the same
classifier as the parent's — no extra prompts, no `permissions.allow`
entries needed.

## Step 2 — Pick the right subagent

### Heavy command output — run inline

Run check commands (test suites, lint, typecheck, build) directly via the
parent's Bash tool. Do not delegate them to a subagent.

Bash output truncates at 30 KB — well above typical check-suite size —
so an inline heavy run costs the parent only the ~2 KB failure tail,
not the full suite output, with no follow-up read needed.

- **Enumerate check commands and run them one at a time** (test, then lint,
  then typecheck) or as a single chained command when they share a working
  directory.
- **Set an explicit working directory** before running — always run from an
  absolute path you've anchored with `cd` or `Bash(cd ... && ...)`.
- **Setup and state-mutating commands run inline too** (db resets, migrations,
  container start/stop, seed scripts, package installs). There is no charter
  boundary that requires splitting setup from checks.
- Commands scoped to a single test file or single test name during interactive
  debugging also stay inline.

- On overflow, the harness persists full output to a `tool-results/`
  file and returns only a ~2 KB preview (the *first* 2 KB, usually the
  startup banner) — grep/sed the persisted file for what you need.
- Never re-run the command or `Read` the persisted file whole; each
  full re-read re-bills the entire file size.

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

Dispatch any exploratory read immediately, even a single command — a
"~3-query" rule of thumb is only a trailing indicator, since by the
third command multiple turns of output have already landed in context.

This does not apply to *comprehension* reads: when you need a file's
content in your own reasoning — to write or modify it, review it, or
design against it — read it directly. The split is locate-and-report
(delegable) vs. read-and-reason (not).

### Debug-investigation probe → `general-purpose` or `Explore`

When root-causing a check or test failure requires a read-heavy probe —
finding how existing tests handle a pattern, locating the relevant
convention, mapping an analogous code shape — dispatch that probe as an
objective to `Explore` or `general-purpose`, both with an explicit
`model: sonnet` per `CLAUDE.md`'s Model Routing rule — pass it even on
`Explore`, whose `Explore.md` pin is a request, not a guarantee. Use
the same Explore/general-purpose split as Codebase discovery above.

> "Diagnose why [test/check] fails; report root cause + minimal evidence
> + proposed fix."

The parent reasons over the returned diagnosis, designs the fix, and
applies the edit and re-runs the check inline. The investigation read
load stays in the subagent's context, not the parent's.

See `root-cause-analysis` for the diagnosis discipline (establish the
full symptom before forming a hypothesis).

### Implementation work → `code-writer`

When delegating code-writing — feature code, fixes, refactors,
migrations, schema, scripts — dispatch the `code-writer` subagent,
not `general-purpose`. It carries `model: sonnet` and self-reviews its
own diff against staff-engineer reviewer angles before returning,
catching review-finding-class defects in its own context instead of as
a parent round-trip.

**Read-then-edit: decision-made test.** A read-then-edit sequence
routes to `code-writer` only when both conditions hold: (1) the change
is already decided before you read — you read only to *locate* a known
target and apply a fixed change, not to determine scope or design a fix
from what the file reveals; (2) reaching that target costs non-trivial
context that will sit in the parent for the rest of the session. If you
are still deciding *what* to change as you read — scope, approach, or
the substantive content — the edit stays inline. Discovery reads
(mapping an unfamiliar area before deciding) route to
`Explore`/`general-purpose`, not `code-writer`.

**Implementation of an approved plan is delegated by default.** A plan that cleared `/plan-review` already fixed
scope and approach, so condition (1) of the decision-made test above holds by construction — dispatch
`code-writer` per phase, naming the plan path, the phase's steps, and its verification command. Two things stay
with the parent: a step the plan deliberately left open for implementation-time discovery, and review of what a
dispatch returns. That review means running the phase's verification command inline, reading the returned diff
line by line, and applying only a correction the parent's own read of the diff decided — never one a reviewer's
disposition decided instead. Root-causing a failure the returned diff does not explain is not parent work:
dispatch it as a **Debug-investigation probe** (above) and apply the fix the returned diagnosis specifies.

**The fix that follows `code-review`, `ready-for-review`, or `respond-pr` feedback is also delegated by
default.** A reviewer's disposition already names the finding, `file:line`, and a concrete suggested fix, so
condition (1) holds by construction. Condition (2) commonly does not hold — locating one named finding is
usually a single ranged `Read`. That doesn't gate this case, though: the fix is itself review-bearing work,
since an inline fix would skip the re-review `ready-for-review` step 3 runs on a dispatched one. The edit load
still accumulates in the parent either way. A review round is the ADDRESS/DEFER disposition output of one
`/code-review` or `/ready-for-review` invocation, not a span across multiple re-review loop iterations. Dispatch
one `code-writer` per round, `model: sonnet`, carrying: every ADDRESS row verbatim (finding, `file:line`,
suggested fix); the diff scope; and the verification command. Never dispatch a DEFER row. The parent keeps the
commit, the `/code-review` re-run, and the marker. Two further carve-outs, neither size-based:

- Not code (a `## Deferred review findings` block, a `respond-pr` reply, a plan-file edit) — stays inline.
- Still being re-decided — fails condition (1), stays inline.

A finding surviving a second dispatch stops being delegated — it is now a design question, not a fix.

### Everything else → `general-purpose`

The two-test gate above covers every other case: multi-step diagnostics, log correlation, verbose `git diff` /
state-survey bursts. Dispatch the objective — not the commands — to `general-purpose` with an explicit `model:
sonnet` (a no-op when the parent is already Sonnet; keeps the dispatched work off Opus when the parent is not).

### Exception: gate/review loops stay orchestrator-driven

A "review → commit → push → ready-for-review, repeat until clean" sequence — or any open-ended, multi-step,
loop-until-converged gate sequence — never goes to one subagent as an internal loop, no matter how well it
otherwise fits "everything else" above. Drive it from the orchestrating session itself, or split it into
separate bounded per-step dispatches the orchestrator sequences between, so an interruption's blast radius stays
scoped to one step and each step's result stays visible in the orchestrator's own conversation.

This does not conflict with per-phase `code-writer` dispatch above: dispatching one plan phase's bounded
implementation work is not a gate loop, even across several phases dispatched in sequence — each phase dispatch
returns to the parent, which reviews it before the next one goes out.
