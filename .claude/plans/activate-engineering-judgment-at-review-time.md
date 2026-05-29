# Activate Engineering Judgment at review time (not just declare it)

## Context

**Goal: close the gap where CLAUDE.md §Engineering Judgment is loaded into every session but does not *fire* at the moments judgment-class errors are made — by adding observable, review-time tripwires to the review skills and a directed self-review reference to the code-writer agent, while keeping CLAUDE.md the single canonical home for the principles themselves.**

Two recent multi-session PRs (one backend/Postgres project, one spanning a backend + native-client pair) produced egregious errors requiring near-full rewrites under heavy line-by-line correction. Transcript analysis (`transcript-analysis.py` + direct read, abstracted to strip project identity) established the root cause:

- **The pipeline ran. The gates fired. The defects shipped anyway.** Both `/plan-review` and `/code-review` executed — multiple rounds, specialists spawned, `/ready-for-review` run. This is **not** a coverage gap.
- **The rules were present but did not activate on the specific case.** A scope-discipline rule landed 2026-05-25; the same out-of-scope-edit pattern reappeared on a PR opened *after* that date. `code-review`'s "evaluate against THIS project's stack" checklist ran yet did not flag a speculative claim about which CI env vars existed. The user had to *name* the principles (Fowler opportunistic-refactoring, primary sources, "this is an overcorrection") before they were applied — reactively.
- **No skill references §Engineering Judgment by name at runtime.** The only link lives in `plan-review/REFERENCES.md` (load-on-demand, edit-time). The skills silently re-derived a *subset* (over-powered primitive, compounding layers, DRY) as unlinked standalone items — the DRY drift CLAUDE.md itself warns against.

### Design principle settled with the user: different-time, different-content

The "CLAUDE.md canonical vs. skills canonical" framing is a false binary — the surfaces act at different times and hold complementary content:

- **Principle** (e.g. "default-suspect over-powered primitives") → **CLAUDE.md**. One home, always-on, shapes code at **authoring time**, including the large fraction of work that never invokes a review skill.
- **Operationalized tripwire** (the observable test: "does *this diff* assert external state it can't see?") → **review skill body**, inline at **review time**. A tripwire is the detection *procedure* for a principle, not a restatement of it — different knowledge, different home, not a DRY violation. This is already how `plan-review` Step 4 works.

Why not relocate the authoritative text into the skills: ad-hoc work that never runs a skill would lose the judgment lens entirely, and skills fire *after* authoring. Why not just point CLAUDE.md→skills by name and stop: being-in-CLAUDE.md is exactly what already failed; "loaded" ≠ "activated."

### Why no enforcement hook, and no roster-wide agent edits

- **Spawned `staff-*`/`ciso-reviewer` agents already inherit `~/.claude/CLAUDE.md` automatically** (official docs, sub-agents.md "What loads at startup"; only built-in `Explore`/`Plan` skip it). A by-name pointer in those bodies would be loaded-but-not-activated — the same weakness. Their activation channel is the **directed spawn prompt** ("verify X"), which already carries the tripwire once it's in the skill body. No `staff-*` edits.
- **`code-writer` is the exception** — it's the *authoring* path (Engineering Judgment's native habitat), gets a *broad* prompt with no directed tripwire channel, and self-reviews its own diff. Its body is the only place to raise judgment salience, and its self-review step is a real decision moment. Per the user's preference, this is a **reference to the section by name, not a restatement** of its contents.
- **A suppression-rationale hook was considered and rejected** (see Out of scope). It would cover only one of the four deterministic-cluster members at high cross-language-regex complexity — the over-powered primitive the plan's own rules warn against. Review-time checklist items are the lighter primitive; a hook becomes the justified escalation only if they later under-fire.

This repo is stow-distributed: `claude/` ships to every user who clones it. Per `plan-review` Step 2, the user surface is **all stow users**.

## Approach

Four coordinated, independently-mergeable changes. **CLAUDE.md is intentionally not modified** — the principles are complete; the defect is activation.

### 1. `code-review` — Judgment-activation step + deterministic checklist items + back-links

- **New "Step 1.5 — Judgment-activation pass"** (~8 lines, after the Step 1 implementation-fitness gate), referencing CLAUDE.md §Engineering Judgment / §Working Style **by name**, with tripwires that fire on **diff surface** (the property that makes Step 4 of plan-review work):
  - **Unverified external-state claim** — diff or commit message asserts state the author cannot see (which env vars/secrets exist, CI/config contents, whether a migration was applied, git blame/authorship, that a referenced file exists on `main`) without tool output or an explicit "unverified" flag. Default to flag.
  - **Out-of-scope file edits** — files changed the stated task did not require, especially copy/comment/cosmetic edits on unrelated files (operationalizes §Working Style Scope discipline Axis 1). Distinct from item 14 (don't *fix* unchanged code): this is having *edited* what was out of scope.
  - **Preserved-record edits** — edits to already-applied migrations, changelog/incident records, anchor fixtures (operationalizes Axis 3).
- **Deterministic-cluster checklist items** (Hygiene section, numbered **9c / 9d / 9e** after the existing 9a/9b): 9c ungrounded numeric literal in network/timeout/retry context; 9d lint/type suppression added without a rationale comment; 9e new third-party dependency added without provenance research. (Discriminator literals are already covered by item 9a.) Each is a one-line item that becomes a directed spawn instruction when the orchestrator passes it to a specialist — a new activation point, not a re-paid authoring-time rule.
- **Item ownership rows** (required, else the new items are orphaned): add 9c/9d/9e rows to the `## Item ownership` table (≈ lines 297–330). 9c → `staff-backend-engineer` / `staff-platform-engineer` (network/timeout context); 9d → judgment (any reviewer), mirroring 9a/9b; 9e → `staff-backend-engineer` (runtime deps), co-owner `staff-platform-engineer` (build/CI deps) — mirroring the existing item-31 ownership.
- **Back-links**: append `(Operationalizes CLAUDE.md §Engineering Judgment: …)` to items 9 (→ Single source of truth) and 9a (→ Ground every choice → discriminator literals), resolving the silent duplication at runtime rather than only in REFERENCES.md.

### 2. `plan-review` — Overcorrection tripwire + B5 extension + back-links

- **New Step 4 tripwire — Overcorrection**: plan introduces a blanket rule or revert that contradicts an allowance named elsewhere in CLAUDE.md (e.g. "revert all out-of-scope edits" colliding with the Axis-2 in-file opportunistic-refactoring license). A fix that trades a defect for its opposite is the finding; require the narrower rule. **Phrase it to key on observable plan text** — "a blanket rule/revert that contradicts a named allowance" — so it fits the Step 4 subsection's stated nature (tripwires fire on observable text, not judgment calls); it must cite the colliding allowance by name rather than ask the reviewer to infer one. If it cannot be reduced to an observable-text test in implementation, place it instead as a Base-checklist Clarity item, not in the Step 4 foundation set.
- **Extend B5 (Evidence and verification)**: include external-state claims the author cannot see — assert only with tool output or an explicit "unverified" flag.
- **Back-links** on the two existing Step 4 tripwires: "Over-powered primitive" → §Engineering Judgment *Default-suspect over-powered primitives*; "Compounding layers" → §Working Style *Compounding defensive layers*.

### 3. `code-writer` agent — judgment cue at both authoring and self-review (reference, not repetition)

Two short lines, naming CLAUDE.md §Engineering Judgment and §Working Style **by name** (not restating them — the agent already inherits both in context). They do different jobs at different times, so both belong:

- **Authoring cue (shape-time)** — in the Charter (lines 12–29) or as a closing line of the Implementation baseline (lines 31–55): let §Engineering Judgment and §Working Style steer choices *as the code is written*. Most of §Engineering Judgment is authoring-time judgment (understand intent first, ground every choice, default-suspect over-powered primitives, extract functions) — it must shape the first draft, not surface only at review, or the defect is already written and needs rework (the full-rewrite failure mode this whole change targets). Note the Charter's first bullet (line 14) already operationalizes §Working Style Scope discipline at authoring time; this adds the explicit §Engineering Judgment cue alongside it.
- **Self-review check (check-time)** — in the Self-review pass (lines 57–80), appended to step 4 (line 71) or as a new step after step 5 (line 79): re-check the diff against the two sections as a discrete, verifiable moment — catches what the authoring cue missed. (Not the Charter region for *this* line — the self-review section is the checkable-moment home.)

These are complementary, not redundant: the authoring cue prevents the defect; the self-review check catches the residue. Naming only these two sections (not "all of CLAUDE.md") gives them salience above the rest of the inherited context.

### 4. Struggle-lexicon fix + first test

`transcript-analysis.py` `STRUGGLE_PHRASES` matched **zero** real correction phrases, so correction-density analysis under-counts. Add conservative, low-false-positive entries for observed signals: `hallucinat`, `are you saying`, `you should be able to`, `that doesn't exist`, `that doesn't match`. Deliberately **exclude** bare `stale` (legitimate technical term; high false-positive risk) with an inline comment. `cmd_struggle` currently has **no test** and reads JSONL transcripts bucketed by model family — a test cannot assert against the bare `STRUGGLE_PHRASES` list. Add the first `cmd_struggle` test **mirroring the existing `cmd_commit`/`cmd_review` fixture pattern** in `test_transcript_analysis.py` (synthetic transcript file → invoke the command), with one user turn containing a new phrase (asserted to register) and a control turn containing "stale cache" (asserted not to trip).

## Critical files

**Modify:**
- `claude/.claude/skills/code-review/SKILL.md` — Step 1.5 + 3 Hygiene items + back-links on 9/9a (§1). Already 42KB; additions must stay tight and pass `/skill-review` length/voice checks.
- `claude/.claude/skills/plan-review/SKILL.md` — Step 4 Overcorrection tripwire + B5 extension + back-links (§2).
- `claude/.claude/agents/code-writer.md` — one-line self-review reference (§3).
- `claude/.claude/scripts/transcript-analysis.py` — extend `STRUGGLE_PHRASES` (around lines 26–47) (§4).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — add first `cmd_struggle` test (§4).

**Reuse (do not reimplement):**
- `plan-review/SKILL.md` Step 4 — the observable-tripwire phrasing to mirror in code-review Step 1.5.
- The existing `cmd_struggle` matching semantics (case-insensitive substring, lowercased once) — new phrases must be lowercase, no regex.

**Intentionally untouched:** `claude/.claude/CLAUDE.md` (§Engineering Judgment stays canonical and unchanged); all `staff-*`/`ciso-reviewer` agents (inherit CLAUDE.md already; activation rides the spawn prompt).

## Verification

- `.venv/bin/pytest claude/.claude/` (run from a worktree via `../../../.venv/bin/pytest`) — new `cmd_struggle` test passes; existing suite green. `.venv/bin/ruff check claude/.claude/`.
- `python3 ~/.claude/scripts/transcript-analysis.py struggle --branches <a heavy-feedback branch>` now registers previously-missed corrections; confirm "stale cache"-type benign text does **not** trip.
- Manual read-through: each new skill tripwire fires on the abstracted failure modes from the transcripts (out-of-scope edit; unverified env-var claim; overcorrection-in-fix), and does not duplicate an existing item.
- Confirm no SKILL.md length-budget test (`claude/.claude/skills/tests/test_skills.py`) trips on the additions to the 42KB code-review/SKILL.md — check headroom before finalizing the Step 1.5 + checklist wording.
- Process gates (mandatory, hook-enforced): `/skill-review` on each SKILL.md diff (re-read each body against its own brevity/voice rules with the diff in mind); `/agent-review` on the code-writer diff; `/code-review` on the full diff. CLAUDE.md is not edited, so `/ai-instruction-and-memory-files` is not triggered. `/plan-review` runs now (this plan), before implementation.

## Out of scope

- **Suppression-rationale enforcement hook** — file as a follow-up issue; revisit only if the §1 checklist items demonstrably under-fire. Covers one of four cluster members at disproportionate complexity.
- **Broader agent relevant-subset-pointer question** — whether `staff-*` agent bodies should point at the CLAUDE.md subset relevant to each reviewer (vs. inheriting all of it). A salience question worth its own look; deferred per user. File as a follow-up issue.
- **Editing CLAUDE.md §Engineering Judgment** — evidence says the principles are complete; the fix is activation.
- **Consuming-repo project-layer skills / CI lint rules** for stack-specific deterministic checks — live in the consuming repos, not claude-config.
