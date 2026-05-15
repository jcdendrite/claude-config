# References — plan-review

Not loaded at skill runtime. Consult when editing the skill to verify a design decision still holds or to evaluate whether routing content should be inlined back into SKILL.md.

## Load-on-demand routing architecture

**Why ROUTING.md exists:** The 200-line skill ceiling is a hard constraint enforced by `check-skill-length.sh`. The Reviewer roles table, Reconciliation logic, and Item ownership table together account for ~83 lines — extracting them to a load-on-demand file via a Read directive was the primary lever for getting SKILL.md under the ceiling without dropping behavioral content.

**The pattern:** SKILL.md's Reviewer routing section contains a single unconditional directive: `Read ~/.claude/skills/plan-review/ROUTING.md before any spawn decision.` All spawn criteria — including the always-spawn rules for ciso-reviewer and staff-product-engineer — live exclusively in ROUTING.md. The model reads ROUTING.md on demand before making any spawn decision.

## Smoke test — 2026-05-05

Post-extraction smoke test to verify ROUTING.md is reachable and substantively consulted, not just linked-and-ignored.

**Setup:** Subagent followed the new SKILL.md (245 lines, worktree version) on a synthetic 3-domain plan (backend API endpoints, frontend React component, database migration with RLS). Explicitly read ROUTING.md when the Routing section directed it to.

**Result:** 7 distinct agents spawned (ciso-reviewer, staff-backend-engineer, staff-frontend-engineer, staff-product-engineer, staff-data-engineer, staff-analytics-engineer, staff-sdet). ROUTING.md informed 4 decisions that the inline Routing section alone could not have:

1. `staff-data-engineer` spawned as D4 primary owner (RLS enforceability) — D4 ownership only appears in ROUTING.md's Item ownership table, not in the inline section.
2. `staff-analytics-engineer` spawned via the B2 co-owner row (warehouse-consumer fitness for a new table) — also only in ROUTING.md.
3. Reconciliation logic collapsed a duplicate `WITH CHECK` RLS finding from two agents into one attributed finding — reconciliation prose is only in ROUTING.md.
4. "Spawn per question, not per file-path domain" shaped agent prompt framing — only in ROUTING.md.

**What would invalidate this:** A future test where the model skips the Read directive entirely and makes spawn decisions without reading ROUTING.md — all always-spawn rules (including ciso-reviewer and staff-product-engineer) now live exclusively in ROUTING.md, so a missed Read means no principled routing at all. If that is observed, consider moving ROUTING.md content back inline.

**What this does not prove:** That the architecture is correct in general. It is evidence the extraction didn't break agent spawning on one realistic test case. Re-run this test when SKILL.md's Routing section is edited or when ROUTING.md content is modified.

## Foundation-tripwire rules — surfacing incident

Step 4's foundation tripwires (over-powered primitive, compounding layers, self-referential finding) were added after a session built elaborate scaffolding (PreToolUse validator + Write hook with `lstat` symlink-race checks + mktemp anchoring + cross-platform fixtures + staggered two-PR rollout) to harden a `bypassPermissions: true` foundation that was itself unnecessary — the source doc had a lower-privilege pre-approval primitive sitting in plain sight. A specialist `ciso-reviewer` pass surfaced three Critical findings, all real, all only present *because* the bypass design existed.

The lesson the rules encode: gap-finding on a wrong foundation elaborates the wrong foundation. The tripwires anchor Step 4 to observable surface features (layer counts, self-references, heavier-than-needed mechanism) so they fire even when the AI's internal reasoning is coherent. See also `claude/.claude/CLAUDE.md` Engineering Judgment and Working Style for the global-level statement of the same heuristics.

The fourth tripwire (misordered observe-then-mutate steps) was added after a planning session repeatedly proposed annotating a CI-status check as possibly-stale rather than relocating it after the push that invalidated its result — an ordering bug papered over with a compensating caveat.

## Hook enforcement — 2026-05-05

Two hooks mechanically enforce ROUTING.md consultation during plan-review:

- **`log-routing-read.sh`** (PostToolUse on Read): writes `~/.claude/.plan-review-routing-read.d/$SESSION_ID` when ROUTING.md is read during an active plan-review session. Observation-only; always exits 0.
- **`require-routing-read.sh`** (PreToolUse on Agent): denies sub-agent spawning if no fresh (<60 min) routing-read marker exists for the session. Only fires when `~/.claude/.plan-review-active.d/$SESSION_ID` is present.

The output format also requires listing spawned agents with their checklist item IDs (from ROUTING.md's Item ownership table). Item-to-agent mappings only exist in ROUTING.md, so correct attribution requires consulting the table — making the rationale a de-facto smoke test on every plan-review run.
