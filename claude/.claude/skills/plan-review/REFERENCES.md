# References — plan-review

Not loaded at skill runtime. Consult when editing the skill to verify a design decision still holds or to evaluate whether routing content should be inlined back into SKILL.md.

## Tripwire → CLAUDE.md principle mapping

The items below operationalize named principles from CLAUDE.md. This table
lives here (edit-time reference) rather than in SKILL.md (runtime-loaded body)
because it is design provenance for a skill editor, not an instruction that
changes review behavior.

Keep one row per Step 4 tripwire. A tripwire missing from this table reads
as "has no canonical principle behind it," which is a different claim from
"nobody has filled in the row yet."

| SKILL.md Step 4 tripwire | Canonical CLAUDE.md principle |
|---|---|
| Over-powered primitive | §Engineering Judgment — Default-suspect over-powered primitives |
| Compounding layers | §Working Style — Compounding defensive layers are a wrong-foundation tell |
| Self-referential findings | §Working Style — same bullet as Compounding layers, which covers a design that "starts citing its own prior findings" |
| Misordered observe-then-mutate steps | None — skill-local. CLAUDE.md has no ordering/self-inflicted-staleness principle to operationalize; this tripwire generalizes a single observed planning failure (see the surfacing incident below) rather than a stated global rule. |
| Overcorrection that negates a named allowance | §Working Style — Scope discipline, Axis 2 (the in-file opportunistic-refactoring license is the allowance most often negated) |

## Load-on-demand routing architecture

**Why ROUTING.md exists:** The 200-line skill ceiling is a hard constraint enforced by `check-skill-length.sh`. The Reviewer roles table, Reconciliation logic, and Item ownership table together account for ~83 lines — extracting them to a load-on-demand file via a Read directive was the primary lever for getting SKILL.md under the ceiling without dropping behavioral content.

**The pattern:** SKILL.md's Reviewer routing section contains a single unconditional directive: `Read ~/.claude/skills/plan-review/ROUTING.md before any spawn decision.` All spawn criteria — including the always-spawn rules for ciso-reviewer and staff-product-engineer — live exclusively in ROUTING.md. The model reads ROUTING.md on demand before making any spawn decision.

## Reconciliation discriminator

The escalation-only discriminator in ROUTING.md's Reconciliation section —
what convergence does and does not decorrelate, and why prescribed
co-ownership disclaims independence without disqualifying escalation — is
grounded in `docs/design-decisions.md` §3's `### Sources` block. See that
block for the citations rather than restating them here.

## Finding-enumeration requirement

Output format's positive enumeration requirement ("Every finding a spawned
reviewer returns must appear in the rendered output") is plan-review's
counterpart to `code-review/SKILL.md`'s Finding disposition step ("walk
*every* reviewer-spawned finding and tag it ADDRESS or DEFER"). The two are
not equivalent: plan-review has no ADDRESS/DEFER disposition station, so its
requirement stops at enumeration rather than tagging. Deliberately not
mirrored as a full disposition mechanism — the gap this closes is a finding
going unlisted, not a missing taxonomy, and a taxonomy would be a heavier
mechanism than that gap requires. Reconciliation's dedup rule (ROUTING.md)
already covers *how* two attributed findings merge into one entry; this
requirement only guarantees the merged or standalone entry is present at all.

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

The fifth tripwire (overcorrection that negates a named allowance) has a different provenance from the first four: no single plan surfaced it. It came from a judgment-activation pass closing the gap where CLAUDE.md's principles were loaded in every session but did not fire at the moment a judgment-class error was being made — so the rule is anchored to observable plan text (a blanket rule whose wording contradicts an allowance CLAUDE.md names) rather than to a remembered incident.

Keep a paragraph here per tripwire. When Step 4 gains a rule and this section does not, the omission reads as "that rule has no recorded origin" — and the count drift is invisible until someone compares the two files line by line.

## Hook enforcement — 2026-05-05

Two hooks mechanically enforce ROUTING.md consultation during plan-review:

- **`log-routing-read.sh`** (PostToolUse on Read): writes `~/.claude/.plan-review-routing-read.d/$SESSION_ID` when ROUTING.md is read during an active plan-review session. Observation-only; always exits 0.
- **`require-routing-read.sh`** (PreToolUse on Agent): denies sub-agent spawning if no fresh (<60 min) routing-read marker exists for the session. Only fires when `~/.claude/.plan-review-active.d/$SESSION_ID` is present.

The output format also requires listing spawned agents with their checklist item IDs (from ROUTING.md's Item ownership table). Item-to-agent mappings only exist in ROUTING.md, so correct attribution requires consulting the table — making the rationale a de-facto smoke test on every plan-review run.

## Enforcement-invariant fix-or-ask rule — surfacing incident (GH-428)

Added after a 2026-07-02 review-pipeline assessment (corpus: 264 engineer-authored inline review comments since 2026-03-31, plus a 36-plan sweep) found a 2-for-2 pattern: when a plan-time review detected a weakening of an enforcement invariant and disposed of it as a "disclosed tradeoff," the engineer rejected or reverted it on contact with the implementation.

1. PR #413's approved plan contained a web-UI draft→ready push-gate bypass, labeled "Known hole," disposed as disclose-in-PR-body. Shipped; rejected at human review; reverted same day; GH-415 then re-derived the identical invariant analysis from scratch.
2. A plan that removed a check-runner read guard disclosed the resulting drift risk and deferred it to a "separate foundational plan" that was never filed; the misbehavior persisted until the subsystem was retired wholesale (#401 / GH-352).

Mechanism: author-as-judge. The session that designed the mechanism grades its own found invariant-break as an acceptable tradeoff, and plan approval does not function as informed consent — in the #413 case the hole sat at ~line 113 of a 152-line plan; the engineer's genuine reaction surfaced only on reading the implemented hook.

Falsifiable: the rule earns its keep if plan-time-detected invariant holes stop appearing in post-merge reverts. See GH-428.
