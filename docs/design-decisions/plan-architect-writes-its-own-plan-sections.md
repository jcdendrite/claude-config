# `plan-architect` writes its own plan sections instead of returning them for the session to re-emit

*2026-10-05.*

`plan-architect` holds `Edit` (and still no `Write`, `Bash`, or `Skill`) and writes its Approach, Critical files, Verification, and Out of scope sections straight into the plan file during a `MODE=plan-sections` dispatch. The Sonnet session no longer re-emits Opus's finished prose through its own `Edit` or `Write` call. That transcription step cost a re-emission of text the agent had already produced, and it could drift from what the agent wrote. `MODE=consult` never edits, and a plan-sections return of the sections as text stays as the fallback.

[§30](plan-it-step5-pinned-opus-dispatch.md) made the agent read-only at creation on least-privilege grounds and never weighed granting `Edit`. [§37](plan-architect-consult-mode.md) widened the agent to a second mode without weighing it either. This entry weighs it and grants only `Edit`, confined by a hook.

## Why `Edit` and not `Write`

A whole-file `Write` reproduces the plan's Context, `Ask:` line included, so a slip would rewrite the text `/plan-review` measures the plan against. `Edit` changes only the span its `old_string` names, which lets the agent's reach match its region, from `## Approach` to the end of the file.

## Why the gate checks the path and `/plan-it` checks Context

`deny-plan-writer-off-plan-writes.sh` allows a plan-writer's `Edit` only to an existing file directly under the repository's `.claude/plans/`, and denies every other `Edit` and every `Write` or `MultiEdit`. A rule that also denied an `Edit` starting above `## Approach` would need byte-offset matching over `old_string` in bash and a test arm per branch. Detection is enough: `/plan-it` Step 5 (`claude-skills/skills/plan-it/SKILL.md`) hashes the Context and stops on a change, before `/plan-review` runs against altered Context. The gate's header lists its known gaps, and this entry cites that list rather than repeating it.

## Why a dedicated gate

An arm inside `deny-reviewer-tree-mutation.sh` would save one hook process per call, but it would put a second invariant into a gate named for review-only agents. An arm in any already-linked, already-registered hook would also close the windows listed under Accepted residuals. Per a CLI check on 2026-05-14 that this decision did not re-run, agent-frontmatter `hooks:` do not fire for dispatched subagents. A path specifier in `tools:` is not documented for subagents, and a `permissions.deny` `Edit(...)` rule cannot key on agent identity. A new gate in the `Edit|Write|MultiEdit` group, keyed on `.agent_type`, is the lightest mechanism that confines one agent.

## Why one agent

Consults prescribed by `/code-review` (its fix-plan pilot only when `architect_fix_plan_pilot` is on) and `/ready-for-review` can pass a plan path, as can an engineer-asked consult. A consult-mode `Edit` there is possible, and at the `/code-review` and `/ready-for-review` sites the plan is already committed, so it re-arms the plan-review gate and the session's next non-plan write is denied. `/review-pr`'s consult passes no plan path but reads untrusted PR content. The gate limits such an `Edit` to repository plan files, and git recovers a committed one. A read-only consult agent would remove that residual, but it would add a second agent description to every session's base context, which is the cost [§37](plan-architect-consult-mode.md) rejected. The consult charter forbids `Edit` and a `test_skills.py` test pins that sentence.

## Tier

The tier and the grounds for each component are in the gate's header and its row in `docs/hooks.md` § "Per-hook classification".

## Roster enrollment

`_LIB_PLAN_WRITER_AGENTS` in `_lib.sh` is the registry, and `_LIB_NO_GATE_RELEASE_AGENTS` is derived from it plus the review-only roster and `code-writer`. Membership in the registry therefore implies marker-write denial by construction. `plan-architect` holds no `Skill` and no `Task`, which is the roster's tool-absence ground. `test_edit_without_bash_agents_are_write_scope_confined` confines every agent that declares `Edit` or `MultiEdit` without `Bash` to the review-only roster or the plan-writer registry.

## Fixed headings and the splice

Every plan now ends with the same four headings, in order: `## Approach`, `## Critical files`, `## Verification`, `## Out of scope`. The agent owns everything from `## Approach` to the end of the file. The fixed headings turn the fallback into a parse: a successful return carries none, so a summary cannot overwrite edits that landed. Step 5 owns the splice conditions and the handling of plans committed before this change.

## Notes

- `plan-it/SKILL.md` Step 5 holds `(pending: plan-architect)`, the scaffold bytes, and the four heading literals. `plan-architect.md` cites Step 5 for the placeholder and names only the headings its edit rules need. A `test_skills.py` test pins the literals in Step 5.
- The hash command is one statement with none of the worktree guard's trigger shapes, and `sha256sum` is an `install.sh` prerequisite. Step 5 holds the command and its stop conditions.

## Accepted residuals

- **Consult mode is limited by its charter only.**
  - A consult-mode `Edit` can reach Context and the `Ask:` line.
  - A consult the engineer asks for mid-planning raises no signal, because the plan is already active and the plan-review gate does not re-arm.
  - The Context hash covers only plan-sections dispatches.
  - A `/review-pr` consult reads untrusted PR content under the same charter-only limit on `Edit`. Which repository's plan files it can reach depends on the session's working directory at that step, which is unverified. An `Edit` to an untracked plan there raises no signal.
- **A plan path on prompt line 1 classifies as a consult.** A session that puts the plan path there instead of on line 2 makes the dispatch classify as a consult, which writes the per-branch consult latch.
- **Windows in which the gate does not run.** The gate's header lists them under Known gaps, with what a plan-writer's `Edit` can reach in each.
- **A dedicated gate file can lock out every caller.** On Claude Code 2.1.295 or later, a gate file that `./install.sh` has not linked blocks every `Edit`, `Write` and `MultiEdit` call, because its registration sets `onFailure: "block"`. `docs/hooks.md` § "Gate deadlock recovery" gives the fix.
- **A plan-mode dispatch falls back to returned sections.** Plan mode's default plan path sits outside the repository's `.claude/plans/`, so the charter has the agent return its sections instead of editing. The gate denies any `Edit` it attempts there, and such a deny counts under `plan-writer-scope`.
- **One more hook process per `Edit`/`Write`/`MultiEdit` call in every session.** For any caller other than a plan-writer the hook bootstraps and exits early. It runs in parallel with the group's other hooks, so it adds CPU time rather than wall-clock time. `.claude/plans/hook-bootstrap-cost.md` records the measurement this rests on.
- **The live `.agent_type` wiring rests on vendor documentation** (`agent-review/REFERENCES.md`) and on the sibling gate's production use.
- **The gate's known gaps are accepted as its header lists them.** Inside the allowed set, an untracked plan file has no git recovery, so the gate does not protect the uncommitted tree in general.

## Revisit triggers

- A gate deny on a repository plan file that `/plan-it` relays.
- A plan-review deny right after a `MODE=consult` dispatch in `/code-review` or `/ready-for-review`.
- A Context-hash stop.
- The gate file found missing from `~/.claude/hooks/` on any install whose `settings.json` registers it.

## Sources

- [§30](plan-it-step5-pinned-opus-dispatch.md) and [§37](plan-architect-consult-mode.md) — the read-only design and the consult widening this entry partly supersedes.
- `claude/.claude/hooks/deny-plan-writer-off-plan-writes.sh` — the gate and its known-gaps list.
- `.claude/plans/plan-architect-edit-grant.md` — full assumption ledger and per-mechanism reasoning.
