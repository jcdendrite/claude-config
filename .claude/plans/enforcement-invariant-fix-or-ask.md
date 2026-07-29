# Plan — Enforcement-invariant findings are fix-or-ask (GH-428)

## Context

**Goal:** teach the review pipeline that a finding which *weakens an enforcement invariant* (a gate/hook/marker guarantee) has exactly two dispositions — **fix it** or **stop and ask** — and that "disclose in the PR body and proceed" is not a valid disposition for this finding class. GH-428 names `plan-review` and `ready-for-review`; review surfaced that `/code-review`'s DEFER path is the actual machinery, so the rule lands in all three (see Approach).

**Why now:** GH-428 documents a 2-for-2 pattern from the 2026-07-02 review-pipeline assessment. When a plan-time review detected a weakening of an enforcement invariant and disposed of it as a "disclosed tradeoff," the engineer rejected/reverted it on contact with the implementation:
1. PR #413 shipped a web-UI draft→ready push-gate bypass labeled "Known hole," disposed as disclose-in-PR-body; rejected at human review and reverted same day (GH-415 re-derived the identical invariant analysis from scratch).
2. A plan removing a check-runner read guard disclosed the resulting drift risk and deferred it to a "separate foundational plan" that was never filed; the misbehavior persisted until the subsystem was retired wholesale (#401 / GH-352).

**Mechanism:** author-as-judge. The session that designed the mechanism grades its own found invariant-break as an acceptable tradeoff, and plan approval does not function as informed consent — in #413 the hole sat at ~line 113 of a 152-line plan; the engineer's genuine reaction surfaced only on reading the implemented hook.

**Intended outcome:** plan-time-detected invariant holes stop appearing in post-merge reverts. Cost is one `AskUserQuestion` per occurrence, which the 2/2 revert rate already justifies. This is a **disposition rule, not a new review layer** — it constrains what disposition the reviewer may output, not what the reviewer looks for.

## Approach

Add the same disposition rule to **three** skills, phrased **generically** (these skill bodies are stowed to every user — no hardcoded claude-config hook/marker names in the runtime body), and record the concrete claude-config provenance once in `plan-review/REFERENCES.md` (edit-time reference, not loaded at runtime — the established home for this skill's "surfacing incident" records).

Plan-review found that the "disclose in the PR body and proceed" *machinery* is not in `ready-for-review` — it is `/code-review`'s codified **DEFER disposition**, which persists deferred findings to the PR description (`code-review/SKILL.md:283–316`) and which `ready-for-review` step 5 splices into the PR body (`DEFERRED_FINDINGS`). `/code-review` also runs standalone. So closing the hole requires constraining the DEFER path at its source in `code-review`, not only mirroring prose into `ready-for-review`. User approved this scope expansion beyond GH-428's two named skills.

**Rule text (generic), adapted to each surface:**
> **Enforcement-invariant findings are fix-or-ask.** When a finding is that the design opens a path around an enforcement invariant — a gate, hook, permission check, required-approval, or marker guarantee that some mechanism currently makes unbypassable — it has exactly two dispositions: **fix** (the design closes the hole) or **ask** (surface a blocking one-line decision point to the user, e.g. "this design lets a UI flip bypass the full gate — accept?"). "Disclose in the PR body / description and proceed" is not a valid disposition for this finding class: approval of a plan or PR does not function as informed consent for an invariant-break buried in the body. This is deliberately narrow — PR-body disclosure stays valid for every *other* tradeoff (it is the legitimate use of `/code-review`'s DEFER path); only the enforcement-invariant finding class is excluded.

### Placement

- **`plan-review/SKILL.md` — Output format section (near the verdict).** The rule constrains the reviewer's *disposition/verdict*, and a qualifying finding can be surfaced by the checklist, Step 4, or a spawned specialist (e.g. `ciso-reviewer` flagged exactly this class in the #413 lineage). Anchoring it to the verdict catches it regardless of which stage surfaced it. It adds a hard constraint to the existing `Approve` / `Approve with changes` / `Request changes` verdict line: for this finding class, "Approve with changes: disclose in PR body" is unavailable — it is Request changes (fix) or a blocking stop-and-ask.
  - *Rejected: a fifth Step 4 foundation tripwire.* Step 4 tripwires "fire on observable plan text" and produce a fixed `Foundation concern / Lighter alternative` output that halts specialist spawning. An invariant-break is often surfaced *by* a specialist and isn't a foundation-shape problem, so the tripwire slot is the wrong mechanism and altitude.

- **`code-review/SKILL.md` — Invalid DEFER rationales list (`~lines 291–299`).** Add one closed-list entry: a finding that weakens an enforcement invariant is never DEFER-eligible — it is ADDRESS (fix) or stop-and-ask, never persisted to the `## Deferred review findings` block. This is the home that actually closes the machinery: it covers standalone `/code-review` *and* the DEFER block `ready-for-review` splices, both of which bypass any prose that lives only in `ready-for-review`. Matches the terse one-line shape of the existing invalid-DEFER entries.

- **`ready-for-review/SKILL.md` — Step 3 (Code review, halt on findings).** ready-for-review runs `/code-review` at step 3; the rule reinforces at the human-handoff gate that a `/code-review` finding weakening an enforcement invariant is fix-or-ask and must not reach the spliced `DEFERRED_FINDINGS` / `## Deferred review findings` block (step 5) or otherwise land in the PR body. Names that mechanism explicitly rather than a generic "PR body." With the `code-review` entry in place this is belt-and-suspenders, but it keeps the gate's own prose self-consistent and the issue explicitly asked for a `ready-for-review` mirror.

- **`plan-review/REFERENCES.md` — new provenance section.** Mirrors the existing "Foundation-tripwire rules — surfacing incident" record: the 2/2 pattern, the author-as-judge mechanism, and the falsifiability criterion. This is the single canonical home for the *why*; the `code-review` and `ready-for-review` rule lines carry no local provenance (the commit message + this record suffice — creating a REFERENCES.md record in each skill for one shared rationale would duplicate the *why*).

### Lighter alternatives considered

The chosen change is already the lightest primitive — prose rules in the two skills that own the disposition, plus an edit-time provenance note. No hook, no marker, no new agent. Two lighter-still options weighed and set aside:
- *Memory/CLAUDE.md rule only.* A global CLAUDE.md line can't bind the disposition at the point the two review skills produce a verdict; the skills are where the disposition is chosen, so the rule belongs in their bodies (per the repo's "route automatic/recurring review behavior into the owning skill" convention).
- *plan-review only, skip ready-for-review.* The 2/2 evidence spans both a plan-time hole (#413) and an implementation-time one; ready-for-review is the last gate before human handoff and is where the disclose-in-body temptation is strongest. Mirroring both is what the issue asks and what the evidence supports.

## Critical files

- `claude/.claude/skills/plan-review/SKILL.md` — add the disposition rule to the **Output format** section, immediately before the verdict line (currently ~line 224). Ceiling 500 (currently 240) — ample room.
- `claude/.claude/skills/code-review/SKILL.md` — add one entry to the **Invalid DEFER rationales** closed list (~lines 291–299): enforcement-invariant weakening is never DEFER-eligible. Ceiling 500 (currently 388) — fits.
- `claude/.claude/skills/ready-for-review/SKILL.md` — add the mirrored rule to **Step 3** (currently ~lines 63–83), naming the `DEFERRED_FINDINGS` / `## Deferred review findings` splice (step 5, ~lines 95–102) it must not reach. Ceiling 200 (currently 163) — the small addition fits.
- `claude/.claude/skills/plan-review/REFERENCES.md` — append a provenance section (the 2/2 pattern, mechanism, falsifiability). Not runtime-loaded; repo-specific citations (PR #413, #401/GH-352, GH-415) are consistent with the existing incident records here.
- **Relocate this plan file** into the repo at `claude-config`'s `.claude/plans/<slug>.md` on the implementation branch and include it in the PR (per plan-it Step 1 / plan-review B17). It currently lives in `~/.claude/plans/` (harness plan-mode scratch), which is not in the repo.

**Reuse / consistency:** match the existing REFERENCES.md "surfacing incident" section shape, code-review's terse invalid-DEFER bullet voice, and the SKILL.md verdict/step voice — terse, imperative, no PR-defined terminology in the runtime bodies.

## Verification

- **Skill-length gate:** rely on `check-skill-length.sh` at commit — confirm no SKILL.md exceeds its ceiling (all three are 500/500/200 with wide margins).
- **`/skill-review` on the diff** (required + hook-enforced for SKILL.md edits): run against all three edited skills; confirm the added prose passes the skill's own brevity/duplication checks. The cross-skill duplication is intentional (repo rule: no shared partials — duplicate skill rules deliberately), so skill-review should not flag it as a DRY defect; if it does, annotate the intent.
- **Hook-alignment tests unaffected:** the edits touch no `HOOK_TEST_FIXTURE` fenced block, so `test_require_plan_review.py` / `test_require_ready_for_review.py` / `test_require_code_review.py` remain green. Run `../../../.venv/bin/pytest claude/.claude/hooks/tests/` from the worktree to confirm.
- **Behavioral smoke (manual):** (a) feed plan-review a synthetic plan containing a disclosed gate-bypass ("known hole, disclose in PR body") and confirm the review now forces fix-or-ask rather than Approve-with-changes; (b) feed `/code-review` a diff whose finding weakens an enforcement invariant and confirm it tags ADDRESS/stop-and-ask, not DEFER→`## Deferred review findings`.
- **`/code-review`** on the full diff before handoff.

## Out of scope

- **Mechanical enforcement of the disposition.** "The reviewer chose fix-or-ask" is a judgment output, not a hook-checkable artifact — consistent with the existing Step 4 tripwires, which are also non-mechanical. A drift-guard / parity test across the three copies was considered and **declined** (user-confirmed): the copies deliberately differ per surface (verdict / DEFER-list / handoff), the actual convention isn't grep-able, and a text-presence test is false comfort; the repo does not parity-test its other intentionally-duplicated skill rules.
- **Broadening the finding class** beyond enforcement invariants (e.g. to all "disclosed tradeoffs"). GH-428 is scoped to gate/hook/marker guarantees; keep it there — and PR-body disclosure remains the correct disposition for other tradeoffs.
