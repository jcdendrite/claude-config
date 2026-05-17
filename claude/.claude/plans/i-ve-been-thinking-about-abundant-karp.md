# Plan: Generalize the dispatch principle to codebase discovery

## Context

Auto mode on a Max plan locks the **parent session** to Opus (`claude-auto`
defaults `--model opus` because Sonnet auto mode is Team/Enterprise/API-only).
The parent stays Opus; the way to keep it economical is to push token-heavy
work off the parent and onto Sonnet/Haiku subagents.

An audit of the current setup found it is already well-architected for
dispatch in most places — `check-runner` handles suite output (mandatory,
`CLAUDE.md` "Heavy command output"), reviewer personas run in isolated
context, `/ready-for-review` and `/plan-it` are clean dispatchers.

One genuine gap remains: **general codebase discovery done by the parent
outside a skill has no dispatch guidance.** When the Opus parent runs broad
`grep`/`glob` sweeps or locate-style reads just to *find* something, the raw
output inhales into Opus context — but the parent only needed the *answer*.
`CLAUDE.md:29` already names this exact failure mode ("parent context inhales
the output") but scopes it to check *commands*; the identical cost for a broad
`Read`/`Grep` is never named. This plan generalizes that one principle.

Adding new agent definitions was explicitly rejected: every agent
`description` is permanently loaded into the parent (Opus) context for
routing, so adding agents to save money costs Opus tokens. The fix uses the
harness-builtin `Explore` and `general-purpose` subagent types — no new agent
files.

## Change

One new H3 subsection in `claude/.claude/CLAUDE.md`, inserted **after line 29**
(end of the "Heavy command output" section) and **before line 31**
(`## Code Review`) — a sibling of `### Heavy command output` under
`## Working Style`:

```markdown
### Codebase discovery

When you need to *locate* something — where a symbol is defined, which files
reference an identifier, broad `grep`/`glob` sweeps, exploratory reads mapping
an unfamiliar area — dispatch it to a subagent (`subagent_type: Explore` for
locate-style search; `general-purpose` when the exploration must read whole
files, as `/plan-it` and `/plan-review` do) rather than running it inline.
Discovery output inhales into the parent context exactly like a check suite
does, and an auto-mode parent on Opus pays that in the most expensive tokens —
for output it only needed an *answer* from. A single targeted lookup — one
`grep` for a known symbol, one `Read` of a known path — stays inline; dispatch
when the search is broad or spans more than ~3 queries.

This does not apply to *comprehension* reads: when you need a file's content in
your own reasoning — to write or modify it, review it, or design against it —
read it directly. The split is locate-and-report (delegable) vs.
read-and-reason (not).
```

**Why this placement and shape:**
- Sits next to `### Heavy command output`, the principle it generalizes —
  same section, same "inhales into the parent context" rationale.
- The comprehension carve-out is load-bearing: without it the instruction
  would contradict `design-decisions.md` decision 8 (parent reads project-layer
  skill files directly because it needs the content in its reasoning) and
  `/code-review`'s `SKILL.md:47` (parent reads every changed file fully — that
  read *is* the review, correctly on Opus).
- The `Explore` vs `general-purpose` split mirrors existing repo convention:
  `/plan-review`'s `SKILL.md:34` already says use `general-purpose` (not
  `Explore`) when exploration must read whole files.
- The ~3-query threshold mirrors the harness's own session guidance.

## File modified

- `claude/.claude/CLAUDE.md` — insert the subsection above between lines 29
  and 31. No other files change.

## What this deliberately does NOT do

- **No Model Routing table change.** Line 52's "Sonnet (default): all code
  reading" describes a *tier*, not a per-session guarantee; it is correct for
  the `opusplan` default. Caveating it for the auto-mode exception would load
  the common-case table with edge-case conditionals — the compounding-caveat
  anti-pattern (`CLAUDE.md:18`). The new instruction carries the auto-mode
  context where it belongs: next to the actionable rule.
- **No new agent files.** Uses harness-builtin subagent types only.
- **No skill changes.** `/code-review`, `/respond-pr`, etc. read files inline
  for judgment work that is correctly on the parent — not in scope.

## Verification

This is a pure-prose `CLAUDE.md` edit; it touches no hooks or skills, so the
`pytest` / `ruff` suite is unaffected and not part of verification.

1. **Consistency read** — re-read the new subsection in context against
   `### Heavy command output` (lines 23-29) and `## Model Routing` (lines
   50-54); confirm no contradiction and consistent terminology.
2. **Run `/ai-instruction-and-memory-files`** on the diff — the targeted
   review skill for `CLAUDE.md` edits.
3. **Run `/code-review`** before presenting, per the repo workflow.
4. **Stow-distribution check** — `claude/.claude/CLAUDE.md` is stowed to
   `~/.claude/CLAUDE.md` for every clone of this repo. Confirm the instruction
   reads cleanly stack-agnostically: the "auto-mode parent on Opus" clause is
   framed as the sharpest case, not a universal claim (Team/Enterprise/API
   users may run `ANTHROPIC_MODEL=sonnet claude-auto`).
