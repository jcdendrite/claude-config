# Plan: `error-mode-analysis` skill — templatize the multi-session delivery analysis methodology

## Context

**Goal: create a new `error-mode-analysis` skill that turns the ad-hoc "signal-bucketed error-mode report" methodology into a repeatable, single-file workflow.** GH issue #414 documents a post-hoc analysis run once, by hand, on a delivered body of multi-session AI-assisted work: it bucketed every observed failure by *which layer of the review pipeline caught it* (to answer "where is claude-config self-correcting vs. blind, and where must a control be added?"), correlated transcript signals with PR review comments, and produced two documents behind a de-identification boundary. The methodology worked but was built from scratch with no template. Why now: a template pays for itself on the second use, and the discipline (bucket taxonomy, two-artifact model, structural-fingerprint scrubbing) is easy to lose. Intended outcome: an `error-mode-analysis` skill, sibling to `transcript-narrative`/`transcript-analysis`, that an analyst invokes by name to run the same analysis end-to-end.

The unit of analysis is "one level up" from a single transcript — a *delivered body of work* (many sessions / PRs), not one session. **Name rationale (grounded in primary sources):** "retro"/"retrospective" was rejected because the Scrum Guide and Agile Manifesto scope it to a *recurring, mid-project, whole-team ceremony*, explicitly contrasted with the one-off post-completion analysis this skill performs; "postmortem" (the accurate standard term) collides with this repo's `CLAUDE.md` usage of "postmortem" for preserved incident records. `error-mode-analysis` follows the descriptive-method naming of the `transcript-*` siblings and names the issue's load-bearing organizing principle (detection-layer error-mode bucketing).

## Approach

A **new `name-only` skill that composes the two existing transcript skills** rather than an enhancement to either. Rationale: `transcript-narrative` (phase-arc synthesis) and `transcript-analysis` (raw CLI metrics) each have one clean job; the new methodology adds three things neither does — (1) a second data source (PR review comments, fetched separately and correlated), (2) a different organizing principle (bucket by *detection layer*, not investigative phase), and (3) a two-artifact de-identification boundary. Folding these into `transcript-narrative` would change its output shape and strain the 200-line cap. Instead `error-mode-analysis` *invokes* both existing skills as steps (issue steps 2–3) and owns only the three net-new layers — keeping each existing skill single-purpose and staying DRY (it references their procedures, does not restate them).

`name-only` in `skillOverrides` matches both siblings: invoked by name/slash at analysis time, no always-loaded listing-budget cost, no auto-trigger noise, and no TRIGGER-block discipline required.

The SKILL.md body maps the issue's four deliverables onto a step sequence:

1. **Scope the delivery** — identify branches / PRs / sessions / date-range under analysis. Reuse `transcript-analysis.py buckets`.
2. **Collect transcript signals** — invoke `transcript-narrative` (arc + ranked lessons) and `transcript-analysis` (metrics: `fail-seq`, `review-trace`, `duration`, `subagents`, `pr-link`) *by name*; do not restate their procedures.
3. **Collect PR review comments** — the distinct second source. `gh api repos/<owner>/<repo>/pulls/<n>/comments` (plus review threads); these do **not** appear in transcripts. Correlate each against the error-mode list so Bucket 3 is complete.
4. **Assign each error mode to a detection-layer bucket** — the four-bucket taxonomy table (cross-session process / bot / human-unique / pipeline-working) with detection-stratum and remediation-priority columns, plus decision rules for the ambiguous case ("a bot caught it, but should a reviewer agent have?"). Buckets described *generically* (reviewer agents / post-commit bots / human PR reviewers) — no hardcoded `staff-*` agent names — so the body stays portable across stow users.
5. **Split into two artifacts + pre-transport boundary checklist** — Artifact A (the private, project-identifying report, stays private) vs Artifact B (de-identified lessons, may cross into a public repo). The checklist is a compact, self-contained scrub list (tracker IDs → `PROJ-`/`TICKET-` placeholders; project/org names; internal tool/path refs; **structural fingerprints** — verbatim policy shapes, rare column-naming, unusual error-code namespaces). Two correctness constraints on how the body describes enforcement (from CISO review of the boundary):
   - **Scope the mechanical-gate claim to its firing condition.** The stowed `deny-private-project-refs` hook short-circuits unless `git remote get-url origin` contains `claude-config` (`deny-private-project-refs.sh:277`). So it catches identifier tiers **only when the commit destination is the claude-config repo itself**; for Artifact B carried into any *other* public repo the hook is a silent no-op and the manual checklist is the sole defense. The skill body must state this at the point it references the hook — never claim the hook catches identifier tiers unconditionally (the overstated backstop is the primary leak vector: an analyst trusts a gate that won't fire for their destination). The **structural-fingerprint scan is always manual** in every destination (the hook cannot catch it).
   - **Make scrub-complete + diff a hard transport precondition, not a reminder.** The body states as a precondition: the checklist is completed and Artifact B is diffed/reviewed *before* it enters any repo. Label which destination gets the hook as a genuine second layer (claude-config) vs. which is single-layer manual-only (everywhere else). No new mechanical script — the rejected "pre-transport script" stays rejected; this closes the gap in prose.

   The compact inline restatement of the scrub categories is a deliberate, named DRY exception (CLAUDE.md: "instructional prose that must let each file stand alone may be duplicated") — a stow user without the claude-config repo checked out cannot follow a path reference to `docs/private-project-redaction.md`.
6. **Artifact B skeleton (inline)** — a fenced fill-in template: (1) lessons organized by bucket, (2) candidate claude-config fixes, (3) a GH issue draft for methodology follow-ups (section headers and per-lesson shape: failure-mode / mechanism / candidate-fix). The body must instruct that Artifact B is **authored fresh from the bucket taxonomy, not redacted down from a copy of Artifact A** — derive-clean minimizes leakage better than redact-down (a from-scratch lessons doc never contains the private span; a redaction pass that misses a span leaks it).

**Length discipline:** the SKILL.md is capped at 200 lines (`check-skill-length.sh`; only `code-review`/`plan-review` get the 500 carve-out). The bucket table and Artifact B skeleton are the two heavy blocks — keep prose terse; steps 2 reference siblings by name rather than restating.

### Lighter alternatives considered

- **Enhance `transcript-narrative` with a mode flag** — rejected: adds a second output shape + second data source + redaction boundary to a single-purpose skill, bloating it toward the cap. A composing sibling keeps responsibilities separated.
- **Plain doc under `docs/` instead of a skill** — rejected: the siblings are skills, the analyst invokes this by name mid-analysis, and it orchestrates two other skills; a passive doc can't be invoked or compose.
- **A mechanical pre-transport script/hook for Artifact B** — rejected as an over-powered primitive: the stowed `deny-private-project-refs` hook already gates identifier tiers on commit, and structural fingerprints are un-mechanizable (per `docs/design-decisions.md`). A manual checklist is the lighter primitive that fits.

## Critical files

**Create:**
- `claude/.claude/skills/error-mode-analysis/SKILL.md` — the skill (≤200 lines). Flat `name-only`-style frontmatter description (no TRIGGER blocks), closing cross-reference to `transcript-narrative`/`transcript-analysis` mirroring their reciprocal closers.

**Modify:**
- `claude/.claude/settings.json` — add `"error-mode-analysis": "name-only"` to `skillOverrides` (project-shared; belongs in committed `settings.json`, not `.local`).
- `docs/skills.md` — three edits required for tests + accuracy:
  - Add a `- **`/error-mode-analysis`** — …` bullet under **## Skills (slash commands)**.
  - Add a `| `/error-mode-analysis` | … |` row to the **Skills available by name** table (**required** — `test_skill_overrides_documented_in_docs_skills_md` fails without it).
  - Update the name-only counts/lists: "Eleven skills" → "Twelve"; the six→seven workflow-utility list (line ~29) and the "Seven skills carry no TRIGGER blocks" → "Eight" prose (line ~35).

**Reuse (call, do not reimplement):**
- `~/.claude/scripts/transcript-analysis.py` subcommands: `buckets`, `fail-seq`, `review-trace`, `duration`, `subagents`, `pr-link`, `audit-routing --redact`.
- `transcript-narrative` and `transcript-analysis` skills — invoked by name in step 2.
- `deny-private-project-refs.sh` (stowed to `~/.claude/hooks/`) — the mechanical identifier gate the checklist points to.

**No changes needed:** `test_skills.py` auto-discovers name-only skills from `skillOverrides` (no hardcoded list to append); no `evals/trigger-cases.json` (name-only skills don't auto-trigger on description).

## Verification

- `.venv/bin/pytest claude/.claude/` — especially `test_skills.py`: `TestNameOnlySkillContracts` (SKILL.md exists, no `disable-model-invocation` flag), `test_builtin_name_only_allowlist_matches_settings` (error-mode-analysis has a repo SKILL.md so must not be treated as bundled), and `test_skill_overrides_documented_in_docs_skills_md` (the docs row).
- `.venv/bin/ruff check claude/.claude/` — no Python added, expected clean.
- `wc -l claude/.claude/skills/error-mode-analysis/SKILL.md` — confirm ≤200 (matches `check-skill-length.sh`).
- Manual smoke test: invoke `/error-mode-analysis` by name; confirm it walks all six steps, invokes the two sibling skills by name, fetches PR comments as a distinct step, and emits the Artifact B skeleton. (No automated `claude -p` harness — per repo convention, manual smoke only.)
- **Boundary-correctness read** of the finished SKILL.md: confirm the hook-coverage claim is scoped to `origin` = claude-config (not stated unconditionally), that scrub-complete + diff is written as a hard transport precondition, and that Artifact B is instructed to be authored fresh (not redacted down from Artifact A) — the three CISO findings folded into step 5–6.
- `/skill-review` on the new SKILL.md and `/code-review` on the full diff (settings.json + docs) before handoff, per the repo's skill-edit rule.

## Out of scope

- Automating the analysis itself (the transcript skills already do the heavy lifting; issue out-of-scope).
- A mechanical pre-transport scrub script/hook (deferred to the existing commit hook + manual checklist).
- New reviewer-agent content (separate issues, driven by specific Bucket 2/3 findings; issue out-of-scope).
- Changes to Artifact A's structure (varies per delivery; issue out-of-scope).
