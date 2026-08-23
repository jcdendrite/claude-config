---
name: plan-it
description: >
  Produces an implementation plan and hands off to /plan-review.
  TRIGGER when: asked for a plan or implementation strategy
  for work spanning multiple files or domains. DO NOT TRIGGER when:
  single-file tweaks, "just implement it" requests, or when a plan
  already exists (use /plan-review instead).
user-invocable: true
argument-hint: "[optional topic or ticket id]"
---

# Plan-it

## Step 1 — Branch + plan file

`.claude/plans/` holds plans that gate a change to this repository — the plan file is provenance for that change, not the deliverable itself. Work that turns out to make no repository change (an audit, a status assessment) still runs this skill end to end, `/plan-review` included; scaffold the plan file here as normal, and Step 7 decides whether it gets committed.

**If plan mode is active:** write the plan to the harness-provided plan path (named in the plan-mode system-reminder). Skip branch creation here — plan mode would block it. Resume the branch-management flow only after `ExitPlanMode` is approved: derive the slug, run `branch-management`, and move the plan file to `.claude/plans/<topic-slug>.md` on the new branch.

**If that write fails** (e.g. a machine-level policy denies the harness-provided path): this is the one case where creating a branch happens before `ExitPlanMode` is approved — not a general license to branch during plan mode, only a recovery once the designated path write has already failed. In a git repository, derive the slug now, create its worktree (`branch-management`'s worktree steps), and write the plan to the repo-relative `.claude/plans/<topic-slug>.md` there instead — a path outside whatever denied the harness-provided one. The branch/worktree already exists by the time `ExitPlanMode` is approved, so skip the post-approval `branch-management` call above; nothing needs to move. Outside a git repository there is no repo-relative path to fall back to — present the full plan as chat text instead of a file and skip `ExitPlanMode`, asking the user to approve conversationally.

**Otherwise:** if on the default branch, invoke the `branch-management` skill to pick a slug and start from a fresh default tip. If already on a feature branch, keep it and derive the slug from the branch name — if the branch name contains `/` (e.g. `GH-42/add-auth`), use only the portion after the last `/`. Plan path is `.claude/plans/<topic-slug>.md` on the implementation branch (per `branch-management`'s "plan files go on the implementation branch" rule).

If `.claude/plans/<topic-slug>.md` already exists, open it for revision in place rather than scaffolding a new file.

## Step 2 — Discovery

Restate the problem, why now, and the intended outcome in one short paragraph. If any of the three is unclear, ask the user before moving on. This becomes the lead of the plan's **Context** section, with the first sentence stating the goal.

## Step 2.5 — Load project-specific layer

If a project-specific layer exists for this skill, load it now. Glob for `.claude/skills/plan-it-*/SKILL.md` from the repo root (resolved via `git rev-parse --show-toplevel`); if exactly one matches, read it with the Read tool — the layer's rules apply to the steps below. If multiple match, list them and stop — that's a config error in the project, not something you can resolve. If none match, proceed without a layer.

## Step 3 — Codebase exploration

Find similar features, the target subsystem, and integration points. Spawn `general-purpose` subagents in parallel when scope warrants — judge fan-out from surface area, do not default to a fixed count. Pass an explicit `model: sonnet` per `CLAUDE.md`'s Model Routing rule. Read the files each subagent flags before designing. Do not use `Explore` here; its read-excerpt window is wrong for design-context analysis.

**Pattern claims require a grep, not a single example.** Before calling a shape "canonical," grep and cite call-site counts (e.g., "12 of 13 modules use form X; exception at `path/to/file:NN`") rather than a single example.

**If the task is a debugging or root-cause investigation** (fixing a reported bug or incident rather than building a new feature), consult `root-cause-analysis` before exploring — establish the full symptom and verify your tools fully ingested their input before forming any hypothesis.

## Step 4 — Clarifying questions

List every underspecified decision (edge cases, error handling, scope boundaries, backward compatibility) and ask the user. Do not proceed until answered or the user delegates the call to you.

## Step 5 — Architecture design

Choose the approach. Always include brief rationale — what alternatives were weighed and why they were set aside. For trivial choices one sentence suffices; no separate alternatives section is needed. Consult `code-review`, `test-conventions`, `verify-sources`, and `ai-instruction-and-memory-files` if their domains are implicated.

**External-pattern grounding.** When invoking an external-doc pattern, quote the literal source lines — a paraphrase or bare pattern name risks crystallizing a wrong interpretation.

**Name the dispatch split.** Implementation of an approved plan is
delegated to `code-writer` per phase by default (`subagent-delegation`);
the plan decides how a phase divides further, not the session that
implements it. Split a phase into more than one dispatch only when its
steps partition into non-overlapping file sets that are each specifiable
without restating the other's context — then name each dispatch's files
and verification command in **Critical files**. Sequence them whenever
one dispatch's output is the next one's input (a signature and its
callers, a schema and its consumers); parallelize only for genuinely
disjoint file sets, and note that parallel dispatches share the parent's
feature worktree — `CLAUDE.md`'s Agent Briefing bars `isolation:
worktree` for PR-bound work, and overlapping edits in one tree clobber
silently rather than conflict. Do not split when the same shared-state
background would have to be restated in every dispatch prompt: each
agent re-reads the same files in its own context and can resolve the
same open question differently, and no agent's self-review sees the
other's.

**Assumption ledger.** The Approach section carries a structured ledger — recording what was checked and what wasn't, so a later revision can be diffed against it instead of silently drifting from a fact the session already established:
- **One root problem/threat line** stating what the plan solves, followed by the **givens** it accepts — conditions the design treats as fixed that lie beyond its own reach. Each carries a one-sentence reason: another party owns it, a vendor or protocol imposes it, or dissolving the design's dependence on it needs a decision outside this plan. "The engineer decided it" is not such a reason — tag that `[engineer-verified]` on its own row. A condition the plan *could* change but deliberately won't is not a given — record it in **Out of scope** with its reason. A given with no qualifying reason is an untested premise, and `plan-review` Step 4 fires on it.
- **Per mechanism:** a one-line justification anchored to `anchors: root` or `anchors: row<N>`, so completeness is a real parse, not another judgment call. This is where the over-powered-primitive check lives: if a mechanism is heavier, more privileged, or wider-scope than the task requires — a heavier abstraction, a more privileged execution context, a more complex coordination pattern, a more invasive integration — enumerate at least two lighter primitives from the source documentation/system and justify in one sentence why each fails, anchored to the row it replaces; fewer than two found means re-read the source before continuing.
- **Every material assumption gets its own row, tagged:**
  - `[verified: <source>]` — checked against code/docs this session, source citable — and prose describing a restriction is not evidence about behavior, so when the claim is what a tool or path can reach, run it and cite the result.
  - `[unverified]` — asserted, load-bearing, not checked; anything downstream inherits the flag.
  - `[engineer-verified]` — sourced from a direct utterance this session, never from a file the human wrote (that is `[verified: <file>]`, which carries no override protection). Never silently revise or override it from your own investigation — a contradiction pauses and asks instead.

See `plan-it/REFERENCES.md` for a worked example and the full grammar rationale.

**Question the ticket's prescribed approach.** Acceptance criteria often prescribe *how* to implement, not only the outcome. Treat a prescribed approach as a hypothesis, not settled design: when planning it triggers the wrong-foundation tell — compounding patches accreting on one mechanism to force the prescribed approach to work — re-derive the correct design ignoring both the current code and the AC, then surface that re-scope to the user rather than planning around a wrong premise.

Write the plan with these sections:

1. **Context** — problem, why now, intended outcome (lead with a one-sentence goal)
2. **Approach** — chosen design with rationale; note alternatives considered and why they were set aside (inline in this section, not a separate block). Lead with the concluded design in one or two plain-language sentences before the assumption ledger — the ledger is supporting detail for diffing against a later revision, not the reader's entry point.
3. **Critical files** — paths to create/modify, with **reuse opportunities** (existing functions/utilities to call rather than reimplement). When the work changes no repository file — an audit, a status assessment — write `None` plus what the deliverable is instead; that's a real result Step 7 acts on, not a gap to fill with speculative paths.
4. **Verification** — how to test end-to-end
5. **Out of scope** — only if scope creep was observed

Effort sections optional; if present, describe review surface (file count, domain spread, risk concentration), never hours or days.

## Step 6 — Hand off to /plan-review

Invoke `/plan-review` against the written plan file. Address any findings before presenting the plan to the user.

**If plan mode is active:** after `/plan-review` is clean and findings are addressed, call `ExitPlanMode` to request approval. The harness shows the plan file's contents in the approval UI; do not also ask conversationally.

**Draft-PR handoff for design-doc or cross-team contract changes.** If the plan introduces a new design document (e.g., a new file under `docs/design/`) or defines a cross-team data contract (a schema shape, enum, or API surface that downstream teams or analytical pipelines depend on), after `ExitPlanMode` is approved and the plan + doc are committed on the implementation branch, push the branch and open a **draft** PR before starting implementation. Async comments on the rendered diff are easier to thread than prose in a plan file, and downstream reviewers may need lead time. Skip this for plans that are implementation-only (no new design doc, no cross-team contract).

## Step 7 — Commit or unwind the plan, then choose where implementation runs

**If the Critical files section names at least one file:** commit the
reviewed plan to the implementation branch before implementation
begins — it makes an approved plan durable before a phase that
rewrites the working tree, and `handoff` §5 already requires it.

**If it names none:** the plan is the deliverable rather than
provenance for a change, so it isn't committed — `mv` the plan file
out of `.claude/plans/`, remove the branch and worktree created for
it, and route the narrative through the project's own tracker or
documentation tool (see that project's `CLAUDE.md`, or ask the
engineer if undocumented) — the review still counts, it just ships as
findings rather than a commit. Stop here — the choice below is about
where implementation runs, and there is none.

Then choose the session. **Continue in this one by default.** A fresh session is not free: it re-pays for context this session already holds, and that rebuild dominates its first several turns, so handing off early costs more than it saves. Run `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/nudge-handoff-near-context-cap.sh" --check` and act on its JSON (`docs/handoff-nudge.md` carries the contract):

- `"status":"ok"` — hand off when `over_threshold` is `true`, or when `already_fired` is `true`. Report `estimate` and `threshold`. Say so when `nudge_disabled` is `true`: the measurement is still valid, but no nudge will arrive on its own. When `"model_recognized":false`, report `model` and `context_window` as well and treat the result as a soft number — the window fell back to the 1M default, so the threshold may not match the running model and those two fields are what let the engineer judge how far off it is.
- `"status":"cannot-resolve"` or `"status":"schema-drift"` — say the estimate is unavailable, name the `reason`, and fall back to judgment: session length, how much of the task remains, whether the plan boundary is a natural seam.

These are a floor, not the only signal: hand off regardless when the engineer asked, when the session is ending anyway, or when a `handoff` §2 reason applies on its own terms. Do not quote the raw `session_id` into prose that may reach a commit, PR body, or plan file.

Delegating implementation to `code-writer` is a separate axis, not a tiebreaker: a subagent starts from a fresh context either way, so it neither argues for handing off nor for staying. Whichever session implements, dispatch `code-writer` per phase by default — the plan already fixed scope and approach, so `subagent-delegation`'s decision-made test is satisfied by construction. See `subagent-delegation`'s "Implementation work → `code-writer`" section for the two carve-outs.
