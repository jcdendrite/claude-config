# Plan: Add `/root-cause-analysis` skill and wire `plan-it` to consult it

Tracking: [jcdendrite/claude-config#395](https://github.com/jcdendrite/claude-config/issues/395)

## Context

**Goal:** ship a user-invocable, invoke-only `/root-cause-analysis` playbook skill and make `plan-it` load it during exploration whenever a planning task is a debugging/RCA investigation.

The skill encodes lessons from a real multi-week AI-assisted investigation where two plausible, data-backed, genuinely-correct fixes shipped against the *wrong* problem before a one-line root cause was found — because complete symptom information existed all along but was never fully ingested (the decisive evidence sat on page 2 of a ticket a reader silently truncated to page 1). The playbook's job is to force symptom-establishment and tool-ingestion verification *before* a hypothesis forms, so future RCA sessions don't repeat that failure mode. Wiring it into `plan-it` makes the discipline automatic for debugging tasks rather than relying on the user to remember to invoke it.

## Approach

Three files created/modified plus two bookkeeping files the test suite requires.

### 1. New skill — `claude/.claude/skills/root-cause-analysis/SKILL.md`

Invoke-only / name-only skill. Frontmatter is **`name` + `description` only** — the exact shape of `claude/.claude/skills/brief/SKILL.md:1-4`. No `TRIGGER when:` clause, no `disable-model-invocation`, no `user-invocable` field (its absence is correct; `user-invocable: false` would wrongly mark it model-only and re-impose TRIGGER discipline per `test_skills.py`).

```
---
name: root-cause-analysis
description: A playbook for debugging and root-cause investigation: establish the full symptom, verify tool ingestion, reproduce and capture the asymmetry, pull data for the specifically affected entities, then confirm the fix addresses the reported incident before shipping.
---
```

Body encodes the issue's 10 principles, platform-agnostic, zero private identifiers, framed as the ordered flow the issue suggests: (a) establish full symptom → (b) verify tool ingestion → (c) reproduce / capture asymmetry → (d) pull affected-entity data → (e) form hypothesis → (f) confirm the fix addresses THIS incident before shipping — with a persistent "open questions / what we still don't know" list maintained throughout. The remaining principles (read every field including unfamiliar status values; treat screenshots/error states as engineering data; name when spinning in circles and stop; distinguish "a real bug in the right area" from "the reported bug") attach to the flow stage they serve. **Under the 200-line body cap** (`check-skill-length.sh`); this content fits comfortably in ~60–90 lines.

### 2. `plan-it` consult — Step 3, not Step 5

Per the placement decision (confirmed with the user): add the consult to **Step 3 — Codebase exploration**, not the Step 5 sibling-consult sentence the issue defaulted to. Rationale: the playbook's core principles (full symptom, tool ingestion, reproduce-before-hypothesizing) are *investigation* activities that must run before a hypothesis forms — Step 3 territory. By Step 5 ("Choose the approach") exploration is done and a wrong hypothesis may already be locked in. This is the issue's sanctioned "move it earlier" path; **the PR description must justify the Step 3 placement** (the issue requires this).

Add as a new bolded sub-rule in Step 3 (matching the existing "Pattern claims require a grep" sub-rule shape at `plan-it/SKILL.md:35`), one-to-two sentences, e.g.:

> **If the task is a debugging or root-cause investigation** (fixing a reported bug or incident rather than building a new feature), consult `root-cause-analysis` before exploring — establish the full symptom and verify your tools fully ingested their input before forming any hypothesis.

Keep `plan-it` within its length budget — this is an addition of a few lines, no new section.

### 3. Required bookkeeping (test-enforced)

- **`claude/.claude/settings.json`** — add `"root-cause-analysis": "name-only"` among the existing name-only entries in `skillOverrides` (lines 42-51). Required: `test_skills.py` asserts every invoke-only skill (no `TRIGGER when:`, no `disable-model-invocation`) is listed `name-only`. Do **not** also set `disable-model-invocation: true` — mutually exclusive with `name-only` and a test failure.
- **`docs/skills.md`** — add a `| `/root-cause-analysis` | … |` row to the name-only table (lines 35-44) and reconcile the prose at line 33: bump "Eight skills" → "Nine", **and** fix the category breakdown in the same sentence. Today it reads *"The four workflow utilities carry no TRIGGER blocks…; the three knowledge-domain skills and one dispatcher-reached reviewer skill carry TRIGGER blocks."* `root-cause-analysis` is a fifth no-TRIGGER name-only repo skill but fits none of those three categories — it's a playbook — so the breakdown must be widened (e.g. "five skills carry no TRIGGER blocks: four workflow utilities and one debugging playbook"), not just the count. `test_skill_overrides_documented_in_docs_skills_md` requires a doc row of the exact form `| `/<name>` |` for every non-`on` skillOverride entry; the prose count/taxonomy is not test-enforced but ships to all stow users, so it must stay accurate.

**Note on local settings-file drift:** `git status` shows uncommitted edits to both `.claude/settings.json` and `claude/.claude/settings.json` at session start. Before editing, verify these are not per-session model/effort overrides that must be restored from main first (per the "don't commit session settings" rule). The skillOverrides edit must be the only change staged from `claude/.claude/settings.json`.

## Critical files

| File | Change | Reuse / reference |
|---|---|---|
| `claude/.claude/skills/root-cause-analysis/SKILL.md` | **create** | Frontmatter shape from `skills/brief/SKILL.md:1-4` |
| `claude/.claude/skills/plan-it/SKILL.md` | edit Step 3 (~line 35) | Match sub-rule voice of existing "Pattern claims require a grep" rule |
| `claude/.claude/settings.json` | add 1 `skillOverrides` entry (~line 51) | Mirror existing `"name-only"` entries |
| `docs/skills.md` | add 1 table row (~line 44) + bump count (line 33) | Mirror existing rows |

## Verification

1. `/skill-review` on the new `root-cause-analysis/SKILL.md` **and** on the `plan-it` edit — must be clean (also hook-enforced via `require-skill-review.sh` before commit).
2. `/code-review` (auto-dispatches `/skill-review` for SKILL.md changes).
3. From a worktree: `../../../.venv/bin/pytest claude/.claude/` — confirm `TestNameOnlySkillContracts` (skill file exists, no disable flag) and `test_skill_overrides_documented_in_docs_skills_md` pass.
4. `../../../.venv/bin/ruff check claude/.claude/` — lint clean.
5. Manual check: `git grep -n root-cause-analysis` shows the entry in settings.json, docs/skills.md, plan-it, and the new skill — and nowhere else unexpected.
6. Confirm the skill body contains zero private-project identifiers (the `deny-private-project-refs` hook also fires on commit).

## Out of scope

- No `TRIGGER when:` clause — the skill must not auto-trigger globally (acceptance criterion). Invocation is via `plan-it`'s Step 3 consult or explicit `/root-cause-analysis`.
- No project-layer (`plan-it-*`) or plugin packaging — this is a global stowed skill.
