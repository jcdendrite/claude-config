# check-runner: narrow charter to checks-only + require cwd anchoring

## Context

A live `check-runner` dispatch failed in a way that cost ~18 min of agent
runtime plus a long parent diagnostic. The parent dispatched check-runner
with the prose "Run supabase db reset + npm run verify in worktree".
check-runner ran `supabase db reset` from the wrong directory — it applied
a *different* worktree's migration set to the shared local Supabase DB.
The migration under test was never applied, so `npm run verify` failed
with a PostgREST schema-cache miss ("Could not find the function … in the
schema cache"). That error reads like a genuine test failure, so the
parent burned a long diagnostic session tracing it back to a
wrong-directory `db reset`.

Two root causes:

1. **`supabase db reset` is environment setup, not a check.** It is
   directory-sensitive (applies migrations from `<cwd>/supabase/migrations/`)
   and mutates shared state (one local Supabase DB is shared across all
   worktrees and concurrent sessions). check-runner's charter is read-only
   verification — test, lint, typecheck, build — but its body never
   excluded state-mutating / setup commands, so the agent ran it.
2. **check-runner has no guaranteed working directory.** It runs in its
   own subagent session; nothing pins its cwd to the target worktree. The
   dispatch passed the worktree only as vague prose ("in worktree"), with
   no absolute path. A directory-sensitive command had no anchor.

The existing `check-runner-bash-guard.sh` hook did not catch this: it is
verified-unwireable (agent-frontmatter `hooks:` do not fire for
Agent-spawned subagents — see `docs/design-decisions.md` §10) and only
covers `git`. A settings.json hook would fire globally, including for the
parent that legitimately runs `supabase db reset`. Per the repo's
established philosophy (§10/§61: foundational scoping over compounding
pattern-regex hardening), the fix is **prose guardrails, no hook**.

## Changes

### 1. `claude/.claude/agents/check-runner.md` — charter narrowing + cwd anchoring

Add two paragraphs to the agent body.

**Scope: checks only, not environment setup.** State that check-runner
runs read-only verification commands (test suites, lint, typecheck,
build) and must NOT run environment/fixture setup or state-mutating
commands — e.g. `supabase db reset`, any `* db reset`, applying or
generating migrations, `docker` container lifecycle, DB seeding,
dependency installs. If the enumerated command list contains such a
command, do not run it. Include a one-line why: these commands mutate
shared state and are directory-sensitive, so running them from a
subagent with no guaranteed cwd misapplies state and produces misleading
check failures.

**Refusal must fit the existing verdict schema.** check-runner already
returns a structured per-command verdict (lines 21–27) with PASS/FAIL
and a precedent non-PASS/FAIL state — TIMEOUT (line 15). Model both new
refusal outcomes the same way rather than as a free-form message, so the
parent can still parse the verdict: an out-of-charter command gets a
per-command status of `NOT RUN — out of charter` (named, with the reason)
and a missing-working-directory dispatch yields an overall verdict that
requests the absolute path. The overall PASS/FAIL line and output-file
paths stay in the same format.

**Run from the parent-supplied working directory.** The dispatch prompt
includes an absolute working directory. As the FIRST Bash call, `cd` to
that exact path as a standalone command (not chained with `&&`). Run
every enumerated command from that anchored cwd; do not prefix
individual commands with `cd … &&` (fragile under parallel Bash calls; a
check run from the wrong directory can silently pass/fail against the
wrong code). If the dispatch prompt has no absolute working directory,
return a verdict requesting one rather than guessing.

### 2. `claude/.claude/CLAUDE.md` — "Heavy command output" section

The section currently tells the parent to enumerate exact command
strings. Add to it:

- The dispatch prompt must include the **absolute working directory**
  the commands run from.
- Do **not** enumerate environment- or fixture-setup commands
  (`supabase db reset`, migration apply, `docker` start/stop, dependency
  install, DB seed) in the check-runner command list — check-runner runs
  checks only. Perform setup yourself, in the correct worktree, before
  dispatching.

Note for the implementer: check-runner is dispatched as a plain `Agent`
(not `isolation: worktree`), so passing an absolute working directory in
its prompt does **not** conflict with the existing "Agent Briefing" rule
in the same `CLAUDE.md` — that rule forbids a `Working directory:` line
only for `isolation: worktree` agents, whose cwd the harness sets
automatically. check-runner has no such automatic anchor, which is the
whole reason it needs an explicit one.

### 3. `docs/design-decisions.md` — new §11

Add `## 11. check-runner scope narrowed to checks-only after a
wrong-directory db reset (2026-05-15)`. Record: the incident and its
misleading-failure cost; the two root causes (setup-command-in-charter,
no-cwd-anchor); the prose-only resolution; and why a hook was rejected
(unwireable per-agent, global if via settings.json — consistent with
§10/§61's foundational-scoping-over-hardening stance).

## Files

- `claude/.claude/agents/check-runner.md` — modify (body prose)
- `claude/.claude/CLAUDE.md` — modify ("Heavy command output" section)
- `docs/design-decisions.md` — modify (append §11)
- `claude/.claude/plans/check-runner-might-need-more-scalable-zebra.md` —
  this plan file. The repo tracks `claude/.claude/plans/` (other plan
  files are committed there), so include this plan in the implementation
  PR rather than orphaning it.

## Out of scope / noted separately

- `README.md:194` says check-runner is "guarded by a `hooks.PreToolUse`
  script that denies git write operations", which reads as if the hook
  is wired (§10 clarifies it is not). A one-word tightening could be
  bundled, but it is a separate doc-accuracy nit — flagging, not
  including, to keep this change minimal.
- The shared-DB race itself (concurrent sessions resetting one local
  DB) is a property of the consuming project's environment, not
  claude-config. This plan only prevents check-runner from being the
  agent that triggers it; it does not solve cross-session DB contention.

## Verification

- Run `pytest claude/.claude/` and `ruff check claude/.claude/` — these
  changes touch only prose (agent `.md`, `CLAUDE.md`, docs), no hook
  scripts, so the suite should be unaffected; confirm green.
- Manual review: re-read the edited `check-runner.md` body and confirm
  (a) a dispatch list containing `supabase db reset` would be refused
  with a named-command verdict, and (b) the cwd-anchoring instruction is
  unambiguous about the standalone-`cd`-first protocol.
- Manual review: confirm the `CLAUDE.md` "Heavy command output" edit
  and the agent body agree on the same contract (parent passes absolute
  cwd; setup commands stay out of the dispatch list) with no drift.
