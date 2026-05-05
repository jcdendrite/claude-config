# References — plan-review

Not loaded at skill runtime. Consult when editing the skill to verify a design decision still holds or to evaluate whether routing content should be inlined back into SKILL.md.

## Load-on-demand routing architecture

**Why ROUTING.md exists:** The 200-line skill ceiling is a hard constraint enforced by `check-skill-length.sh`. The Reviewer roles table, Reconciliation logic, and Item ownership table together account for ~83 lines — extracting them to a load-on-demand file via a Read directive was the primary lever for getting SKILL.md under the ceiling without dropping behavioral content.

**The pattern:** SKILL.md's Routing section keeps the two non-optional spawn rules inline (ciso-reviewer for auth/infra plans, staff-product-engineer for user-facing changes) and delegates everything else — spawn criteria, the domain-to-agent table, reconciliation logic, and item ownership assignments — to ROUTING.md. The model reads ROUTING.md on demand before making specific spawn decisions.

## Smoke test — 2026-05-05

Post-extraction smoke test to verify ROUTING.md is reachable and substantively consulted, not just linked-and-ignored.

**Setup:** Subagent followed the new SKILL.md (245 lines, worktree version) on a synthetic 3-domain plan (backend API endpoints, frontend React component, database migration with RLS). Explicitly read ROUTING.md when the Routing section directed it to.

**Result:** 7 distinct agents spawned (ciso-reviewer, staff-backend-engineer, staff-frontend-engineer, staff-product-engineer, staff-data-engineer, staff-analytics-engineer, staff-sdet). ROUTING.md informed 4 decisions that the inline Routing section alone could not have:

1. `staff-data-engineer` spawned as D4 primary owner (RLS enforceability) — D4 ownership only appears in ROUTING.md's Item ownership table, not in the inline section.
2. `staff-analytics-engineer` spawned via the B2 co-owner row (warehouse-consumer fitness for a new table) — also only in ROUTING.md.
3. Reconciliation logic collapsed a duplicate `WITH CHECK` RLS finding from two agents into one attributed finding — reconciliation prose is only in ROUTING.md.
4. "Spawn per question, not per file-path domain" shaped agent prompt framing — only in ROUTING.md.

**What would invalidate this:** A future test where agents skip the Read directive and make spawn decisions using only the inline Routing section — which would lose items 1–4 above. If that is observed, the inline section needs to replicate the critical spawn criteria rather than relying on the Read directive.

**What this does not prove:** That the architecture is correct in general. It is evidence the extraction didn't break agent spawning on one realistic test case. Re-run this test when SKILL.md's Routing section is edited or when ROUTING.md content is modified.
