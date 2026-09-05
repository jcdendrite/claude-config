# Worktree-isolation Bash-tool guard

The Claude Code harness enforces worktree isolation on the Bash tool with a static
pre-execution check: inside a worktree-anchored session it refuses certain command
*shapes* before running them, on the stated grounds that the command is "too complex to
verify that it stays inside the worktree." This is a harness-native check — no script or
hook in this repo implements or can intercept it — and it is not scoped to this repo; it
applies to any worktree-anchored Claude Code session. See `design-decisions.md` §7 for why this repo opts into worktree isolation at all, a
different question from what the harness's guard does once isolated.

This doc records what a bisection of the guard found, why this repo's fix is
"one script call per multi-step recipe" rather than prose-documented multi-statement
Bash choreography, and the current uncertainty about whether the guard's refusals are
reproducible on demand.

## Trigger taxonomy

Each trigger below was independently reproduced 2+ times at the time the bisection ran,
as a literal, separate Bash-tool call from a worktree-anchored session:

| Trigger | Shape | Result |
|---|---|---|
| A | `$(...)` assigned to a variable, used in a later statement in the same call | Refused |
| B | Any reference to `$CLAUDE_CONFIG_DIR`, bare or `${VAR:-default}`, in an otherwise-trivial single statement | Refused |
| C | An unquoted variable-built path argument (e.g. unquoted `$HOME`/`~` concatenated with `$PPID`) | Refused; the identical path double-quoted succeeded |
| D | `$PPID` used as a whole/standalone argument, regardless of quoting (`echo $PPID`, `head -n1 "$PPID"`) | Refused; `$PPID` embedded as a suffix inside a longer double-quoted string did not trigger this |
| E | A bare `$(git ...)` substitution, even with no assignment and no later statement | Refused |

Triggers A and B are the ones with prior, now-stale documentation in this repo's skills
(see the sweep below); C, D, and E were newly isolated during this investigation and had
no prior mention anywhere in this repo.

## Changelog cross-check

An earlier investigation attributed a fix for Triggers A/B to Claude Code 2.1.224, citing
the Anthropic changelog. A verbatim re-fetch of the primary source does not support that
attribution:

- 2.1.224 (the cited version) has no worktree/isolation/Bash-guard changelog entry at all.
- The quoted fix text — "Fixed worktree-isolated sessions and their subagents being able
  to run destructive git commands against the main checkout; isolation now applies to
  file edits and Bash in every session type" — is real, but belongs to **2.1.222**, two
  versions earlier, and describes an isolation-*bypass* fix (subagents evading isolation,
  addressed by widening the guard's scope), not a false-positive fix that would narrow
  which commands the guard refuses.
- Worktree/git-isolation changelog entries appear at 2.1.210, 2.1.216, 2.1.222, 2.1.229,
  and 2.1.232, each closing one specific bypass or false-positive shape. 2.1.238 itself
  ships "Fixed worktree-isolation Bash refusals telling you to remove a redirect when the
  command had none" — a narrow false-positive fix for one specific spurious-message case,
  unrelated to Triggers A–E.

No version, including 2.1.238 (the version installed when this investigation ran), has
ever been observed to fix Triggers A or B. This is not a regression of a previously-fixed
bug — the prior investigation's "no longer reproduces" observation was real, but its
causal attribution to 2.1.224 was wrong.

## Site sweep

A repo-wide sweep for the Trigger-A/B shape (a `$(...)`-assigned variable used later in
the same Bash-tool call, or a bare `$CLAUDE_CONFIG_DIR` reference) found it at eight
SKILL.md sites, one CLAUDE.md site, and one further site that turned out to be a
false positive:

| File | Shape found |
|---|---|
| `handoff/SKILL.md` | Trigger A + Trigger B |
| `ready-for-review/SKILL.md` (code-review diff site) | Trigger A |
| `ready-for-review/SKILL.md` (skill-fidelity site) | Trigger A + Trigger B |
| `pr-description/SKILL.md` (Cost gate) | Trigger A + Trigger B |
| `pr-description/SKILL.md` (Cost fetch) | Trigger A |
| `git-feature-branch-sync/SKILL.md` | Trigger A |
| `respond-pr/SKILL.md` | Trigger A |
| `plan-review/SKILL.md` (plan-mode-path recipe) | Trigger A + Trigger B |
| `claude/.claude/CLAUDE.md` (Shipping section) | Trigger B |
| `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` | Not affected — see below |

The tenth site is a fenced hook-script skeleton example: documentation of what a hook
script's own file content should look like, never typed into an agent's Bash tool call.
It carries an explicit `HOOK_SCRIPT_CONTENT_EXAMPLE` marker comment for this reason, and
`test_skills.py`'s regression scan (below) excludes any fenced block preceded by that
marker or the pre-existing `HOOK_TEST_FIXTURE` marker — a structural, not stylistic,
opt-in: a shebang line alone is not treated as exclusion, since a defensively-shebang'd
real recipe would silently evade a shebang-keyed heuristic.

## The fix: script-first, not prose-split

Every affected site now invokes one dedicated script under `~/.claude/scripts/` — a
single, literal Bash-tool call with no `$(...)`, no assignment, and no
`$CLAUDE_CONFIG_DIR` reference at the call site — in place of the multi-statement
recipe. This is a better fix than documenting a multi-call, fallback-branching
choreography in prose at each site, on four independent grounds:

1. **It matches the refusal message's own framing.** The guard's stated concern is
   *static verifiability*. A fixed literal path with literal arguments — nothing to
   substitute — is about as statically verifiable as a command gets.
2. **It closes the `$CLAUDE_CONFIG_DIR` gap a prose fix can't.** `$CLAUDE_CONFIG_DIR`
   can't appear in Bash-tool command text under Trigger B, but a script reads it from
   its own process environment internally — never as literal text in the invocation —
   so the gap disappears rather than degrading to a `$HOME/.claude`-only fallback.
3. **This repo already validates the pattern.** `marker.sh` invocations are hardcoded to
   the literal `~/.claude/scripts/marker.sh` path repo-wide, and were never affected by
   this bug shape for exactly that reason. Every script this fix adds follows the same
   convention.
4. **It's more DRY.** A prose fallback dance restated at every site can silently drift
   out of sync between copies; a script is one tested, shellchecked artifact.

Every git-tracked file under `claude/.claude/` resolves into one shared stow checkout
regardless of the active account, so a hardcoded `~/.claude/scripts/<name>` or
`~/.claude/hooks/<name>` names the same file under every `$CLAUDE_CONFIG_DIR` — there is
no non-default-account gap for a script or hook call site to inherit.

For an ad-hoc orchestrator Bash call no script pre-covers, the fallback convention is:
one double-quoted statement, no nested `$(...)`, no `$CLAUDE_CONFIG_DIR` reference — the
same discipline a script gives you automatically, applied by hand.

## Current status: does not reproduce on demand

Re-running all five triggers, plus the exact original pre-fix `handoff` compound recipe,
live, from a worktree-anchored session at the same Claude Code version (2.1.238) as the
bisection above, found **zero refusals across seven independent shapes** — a full
reversal from the bisection results. No config, mode, or environment difference between
the two test passes was identified.

This does not disprove the bisection — each trigger's evidence was independently
reproduced 2+ times when the bisection ran. It means the guard's refusals are not a
stable, on-demand-reproducible static check as of this writing. Readers should not expect
to trigger any row of the taxonomy table deterministically. The most likely explanation
is that the guard's behavior is not a pure function of command text alone — it may be
session-, timing-, or otherwise context-dependent in a way this investigation could not
isolate — but that is not confirmed, only the leading guess.

The script-first fix in this repo does not depend on the guard reliably reproducing:
grounds 1–4 above hold regardless of whether the guard currently refuses anything, and a
script the guard never has to parse for complexity closes the question either way.

**2026-09-03:** from a linked worktree, the guard refused a compound `grep … "$(git
rev-parse --git-path info/exclude)"` call with "names git in a form too complex to
verify" — a single dated observation, not a repeatable check, against the zero-refusals
finding above. It landed on the exact shape the `findings-path-suffix.sh` migration
removes from every skill body that carried it.

## How to re-verify

To check whether the guard is currently refusing any of the triggers above, from a
worktree-anchored session, run each shape as a literal, separate Bash-tool call (not
batched, not scripted around) and observe whether it's accepted or refused. A single
non-refusal is not strong evidence either way, given the non-reproduction finding above —
repeat each shape at least twice before drawing a conclusion. If a refusal reproduces,
it validates continuing the script-first convention (nothing to change); if a new trigger
shape is found that these scripts don't yet cover, that's the signal to add one.

## Reporting to Anthropic

A harness bug report for this behavior is out of scope for this repo's own fix work.
This doc's non-reproduction finding above is relevant context for characterizing
reliability in such a report, but authoring or filing it is not this doc's job.
