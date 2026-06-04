# Add two reusable rules to the planning and research skills

## Context

**Goal:** give `plan-it` and `verify-primary-sources` one durable rule each so future planning and research sessions apply two judgment habits by default — question a ticket's *prescribed implementation approach*, and triangulate durable decisions across multiple first-tier sources.

**Why now:** these two habits recur as ad-hoc corrections rather than defaults. Encoding them in the skill bodies (which load only when their workflow fires) is the right home — not always-loaded CLAUDE.md and not user-scoped auto-memory. Source brief: `/tmp/claude-config-planning-and-research-skill-rules-task.md`.

**Intended outcome:** two scoped skill-body edits in `claude-config`, shipped as one PR, each passing `skill-review`.

**Scope decision (resolved with the user):** the brief's pre-approved wording is **tightened for DRY** rather than inserted verbatim, because both draft rules overlap guidance that already exists (CLAUDE.md's compounding-layers tell; the reputable-sources auto-memory; `deep-research`'s harness role). The optional `plan-review` lens (brief step 3) is **dropped** — Step 4 already covers it. A separate concern the user raised — making "research"/"web research" reliably engage primary-source discipline — is **deferred to its own brief** (see Out of scope); a transcript analysis confirmed it is a triggering/hook question, not a body-content question this PR can solve.

## Approach

Two targeted prose insertions into existing skill bodies. Edit the **repo source** under `claude/.claude/skills/` only — never the stowed mirror at `~/.claude/skills/` (it updates on `git pull`).

### Rule 1 — `plan-it` Step 5 (Architecture design)

Insert after the existing **Lighter-alternatives subsection** paragraph (so its back-reference resolves within the same step), before "Write the plan with these sections":

> **Question the ticket's prescribed approach.** Acceptance criteria often prescribe *how* to implement, not only the outcome. Treat a prescribed approach as a hypothesis, not settled design: when planning it triggers the wrong-foundation tell — compounding patches accreting on one mechanism to force the prescribed approach to work — re-derive the correct design ignoring both the current code and the AC, then surface that re-scope to the user rather than planning around a wrong premise.

*Rationale / what was trimmed vs. the brief draft:* the brief's draft restated the "compounding patches" detection signal (already in CLAUDE.md's "Compounding defensive layers are a wrong-foundation tell" and plan-review Step 4's "Compounding layers" tripwire) and re-listed "consult reviewers and primary sources" (Step 5 already says "Consult `code-review`, `test-conventions`, and `verify-primary-sources`"). The tightened form keeps only the genuinely-new angle: **the ticket's prescribed HOW is itself a hypothesis, and the response is to surface a re-scope to the user** — naming the existing tell by its one-phrase symptom rather than restating it or cross-referencing the adjacent **Lighter-alternatives subsection** (which is about heavier-than-needed *mechanisms*, a different signal).

### Rule 2 — `verify-primary-sources` (## The rule)

Insert as a new bolded paragraph after numbered item 4, before the closing "If a primary source cannot be located…" paragraph:

> **Triangulate durable decisions across multiple first-tier sources.** Depth on a single source (items 1–4) is not sufficient for a durable guideline or architectural standard — one authoritative source can still be incomplete or idiosyncratic. Cite two or more independent first-tier origins (vendor docs, specs, standards bodies), not one aggregator restating the others however well-staffed. Record a verbatim quote + URL per source so a reader can re-check what each claim is scoped to.

*Rationale / what was trimmed vs. the brief draft:* the brief draft's "cite the origin, not a later aggregator" largely restates this skill's existing thesis (items 1–2: secondary sources are leads, fetch the primary source). Its tail — "delegate bulky fetching to research subagents that return a citations dossier" — pushes this lightweight read-the-origin skill into `deep-research`'s fan-out-harness role (over-scope). The tightened form keeps only the **additive axis: breadth/triangulation across ≥2 independent origins for durable decisions**, scoped explicitly to guidelines/standards, plus a light per-source citation-capture discipline. Tier/credibility wording is kept self-contained in the skill body (not cited from the user-scoped memory, per the no-memory-refs-in-durable-docs rule).

### Sequencing & review

1. Create a PR-bound worktree (worktree enforcement is active): `git worktree add .claude/worktrees/<slug> -b <slug>` from the main tree, then work there. Slug per `branch-creation` (no tracker ID → topic-slug alone), e.g. `plan-research-skill-rules`.
2. Apply both edits to the repo-source SKILL.md files.
3. Run `/skill-review` against each edited SKILL.md and address findings. This is **hook-enforced** for SKILL.md commits (`require-skill-review.sh` blocks `git commit` until the behavioral-equivalence marker is written) — and per repo policy, re-read each skill body against its own diff (a brevity-arguing skill must not gain bloat).
4. `/code-review` before presenting (it auto-dispatches `/skill-review` for SKILL.md changes).
5. Open one PR per `claude-config` conventions. Do not merge (AI-opened PRs await the user's explicit "merge it").

## Critical files

- `claude/.claude/skills/plan-it/SKILL.md` — Step 5, insert Rule 1 after the Lighter-alternatives paragraph (currently `:47`).
- `claude/.claude/skills/verify-primary-sources/SKILL.md` — `## The rule`, insert Rule 2 after item 4 (currently `:55–58`), before the closing paragraph (`:60`).

**Reuse / no new mechanism:** both edits are prose into existing sections — no new files, no `_shared/` partial (cross-skill sharing is prohibited here; the two rules are intentionally independent). Each rule leans on already-in-context guidance (CLAUDE.md compounding-layers tell; the skill's own items 1–4) by reference rather than restatement.

**No collateral files:** `docs/skills.md` carries a one-line *role* summary for each skill (e.g. "read the primary documentation directly…"); a body-rule addition does not change either role, so per the match-doc-granularity rule no `docs/skills.md` edit is required. Neither skill has a `REFERENCES.md` entry to update (the rules cite no specific external source). The plan file itself ships on the implementation branch and is committed in the same PR as the edits (B17).

## Verification

- **Tests (primary guard):** `../../../.venv/bin/pytest claude/.claude/` (from the worktree) — confirm no hook-alignment/skill tests regress. No fenced HOOK_TEST_FIXTURE blocks are touched, and only skill *bodies* change (descriptions are untouched, so the always-loaded description budget is unaffected); the full run also covers any skill body-length test.
- **Lint (no-regression check only):** `../../../.venv/bin/ruff check claude/.claude/` — `ruff` lints Python, not markdown, so it does not validate this prose change; run it to confirm nothing else regressed.
- **`skill-review` markers:** confirm a behavioral-equivalence marker is written for each edited SKILL.md (the commit hook gates on it).
- **Manual read-through:** re-read each inserted paragraph in place — its in-file references ("items 1–4" in verify-primary-sources) must resolve, and neither insertion may contain PR-defined terminology or "used to be X" framing (durable-doc rule).

## Out of scope

- **Workstream B — make "research"/"web research" reliably engage primary-source discipline.** Transcript analysis (4,804 sessions) showed `verify-primary-sources` fires ~5 times ever and never from "research" phrasing — even an explicit "check primary sources first" didn't trigger it — because it is `user-invocable: false` and its description never lexically matches "research" (whereas the external `deep-research` skill leads with that word). Per CLAUDE.md's "whenever X → hook" doctrine, this is a deterministic-hook design question (routed via `claude-hook-review`), with false-positive and token-cost tradeoffs and a `deep-research`-relationship to untangle. Deferred to its own brief; offer to write it after this plan is approved.
- **`plan-review` matching lens** (brief step 3) — dropped as redundant with Step 4's "Over-powered primitive" tripwire and "Question implementation choices, not feature scope" line.
- **The `error-handling` propagation-axis work** (`/tmp/error-handling-skill-propagation-axis-task.md`) — independent brief; not folded in.
