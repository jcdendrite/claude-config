# Plan: add a "Claude Code config" domain to plan-review

## Context

`plan-review` has no domain that routes a plan proposing new CLAUDE.md,
AGENTS.md, SKILL.md, agent-file, or hook content to the audit skill that
owns that file type's checklist — so a plan drafting that content gets no
placement/altitude/duplication/length/behavior-test scrutiny before the
user is asked to approve it. `code-review`, its sibling gate, has exactly
this domain (`## Domain: Claude Code config`, `code-review/SKILL.md:167-177`)
dispatching to `ai-instruction-and-memory-files`, `skill-review`,
`agent-review`, and `claude-hook-review` — but `plan-review/SKILL.md`'s
Step 2 domain list (`:36-42`) stops at Security, and neither its body nor
`ROUTING.md` mentions any of those four skills.

This surfaced concretely this session: a `plan-it`-drafted CLAUDE.md bullet
ran long and mixed the instruction with its own lineage ("this is the
complement of rule X"). It passed `/plan-review` clean — Step 4's
design-fitness gate correctly judged the *design* as proportionate, but
nothing in `plan-review` ran the instruction-file-specific audit
(`ai-instruction-and-memory-files`'s behavior test and sibling-density
checks) against the actual drafted text. The verbosity was only caught by
the user directly at `ExitPlanMode`. The user was right that the fix
belongs in the repo's own instructions/skills, not in personal memory —
this is a gap any future plan touching this surface will hit again.

**Intended outcome:** `plan-review` gains a "Claude Code config" domain,
structurally mirroring `code-review`'s, so a plan proposing instruction-file,
skill, agent, or hook content gets the matching audit skill invoked against
the plan's own drafted text — before the plan is presented for approval, not
deferred to `/code-review` after implementation when the plan has already
been signed off.

## Approach

Add one new Step 2 domain bullet and one new `## Domain: Claude Code config`
section to `claude/.claude/skills/plan-review/SKILL.md`, both closely mirroring
`code-review/SKILL.md`'s existing domain of the same name — same skill
routing, same per-file-type split, adapted only in framing (audit the
*plan's drafted text*, not a diff) and in one added sentence naming why the
timing matters (this plan's own motivating incident, phrased generically —
"before the plan is presented," not "an agent got this wrong").

**No `ROUTING.md` changes.** `code-review`'s equivalent domain is deliberately
absent from its Item-ownership / spawn table — these are Skill-tool
`invoke`s (skills), not `staff-*` agent spawns, so they don't participate in
the spawn-per-question routing machinery `ROUTING.md` governs. Mirroring that
omission keeps the two skills' domain shape identical rather than
introducing a routing-table entry code-review's sibling section doesn't have
either.

**`plugin-semver` intentionally excluded.** `code-review`'s Claude Code config
domain also dispatches `plugin-semver` for version-bump discipline, but that
check is inherently diff-based (compares the plugin's `version` field against
the branch's merge-base) — a plan proposing plugin content has no diff yet to
compare, so there's nothing for `plugin-semver` to check pre-implementation.
Leaving it out avoids fabricating a check with no input to run against.

Alternative considered: fold this into `plan-review`'s existing Base
checklist as a new numbered item (B18) rather than a Domain section.
Rejected — every other file-type-specific audit skill (`skill-review`,
`agent-review`, `ai-instruction-and-memory-files`, `claude-hook-review`) is
dispatched via a Domain section in `code-review`, not a Base checklist item;
matching that shape in `plan-review` keeps the two skills' structure
parallel and makes future edits to either easier to keep in sync by
inspection.

## Critical files

- `claude/.claude/skills/plan-review/SKILL.md` — **modify.**
  - Step 2 (`:36-42`): add one domain bullet after **Security**:
    `- **Claude Code config**: New or modified CLAUDE.md, AGENTS.md, SKILL.md, agent files, hooks, memory files (~/.claude/projects/*/memory/), or permissions.allow rules proposed by the plan`
  - After the existing `## Domain: Security` section (`:183-197`), add:
    ```
    ## Domain: Claude Code config

    Apply when the plan proposes new or modified content for `.claude/skills/**/SKILL.md`, `claude/.claude/agents/*.md` or `plugins/*/agents/*.md`, `CLAUDE.md`/`AGENTS.md`/`~/.claude/projects/*/memory/`, hooks (`claude/.claude/hooks/*.sh`, `settings.json` hook entries), or `permissions.allow` rules.

    For SKILL.md content, invoke `skill-review` against the plan's drafted text. For agent-file content, invoke `agent-review`. Each owns frontmatter contract, trigger design, voice, length, behavior test, and cross-reference vs duplication for its file type.

    For CLAUDE.md, AGENTS.md, or memory-file content, invoke `ai-instruction-and-memory-files` against the plan's drafted text — it owns placement (which surface), altitude, duplication, length cap, and the behavior test. Running it here, on the plan's proposed text, is the point: a placement or verbosity defect caught at `/code-review` has already been signed off on by the user at plan approval.

    For hook content, invoke `claude-hook-review`. For `permissions.allow` rules, invoke `/review-permissions`.
    ```
  - Exclusions list (`:199-205`): no change needed — it already scopes to
    "Domain checklist items for domains the plan doesn't touch," which
    covers the new domain automatically.

No other file needs to change. `ROUTING.md` is explicitly not touched (see
Approach); `code-review/SKILL.md` is the mirrored source and is not itself
modified.

## Verification

1. **`/skill-review`** (mandatory, hook-enforced for any `SKILL.md` diff) —
   verify frontmatter is unchanged, the new section reads standalone (no
   forward reference to this plan or this session), and the addition doesn't
   push the file past its length budget.
2. **Side-by-side diff against `code-review/SKILL.md`'s Claude Code config
   section** — confirm the routing (which skill owns which file type) is
   identical; any divergence should be deliberate and named (as the
   `plugin-semver` exclusion is above), not accidental drift.
3. **Test suite** — `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree.
   Confirmed no existing test asserts `plan-review`'s domain-list content
   (`grep` of `claude/.claude/hooks/tests/test_require_plan_review.py`
   found no domain-related assertions) — this is a prose-only skill-body
   change with no test-breaking surface.
4. **Manual dry run** — the next time a plan proposes CLAUDE.md/SKILL.md/
   agent/hook content, confirm `/plan-review` actually invokes the matching
   audit skill and that its findings surface before the plan is presented,
   not only later at `/code-review`.

## Out of scope

- Extending this same domain pattern to any other skill besides
  `plan-review` — `code-review` already has it; no third gate needs it.
- Adding `plugin-semver` to the new domain (see Approach — no diff exists
  yet at plan-review time for it to check).
