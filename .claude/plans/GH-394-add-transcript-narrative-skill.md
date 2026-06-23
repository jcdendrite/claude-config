# Plan: add `transcript-narrative` skill (issue #394)

## Context

The repo has `transcript-analysis` — a name-only reference card over the deterministic
`transcript-analysis.py` CLI that emits structured metrics. What it lacks is a documented
workflow for the qualitative, LLM-driven task that sits on top of that CLI: turning session
transcripts into a **narrative case study / annotated timeline** (verbatim prompts + phase
buckets + quantitative appendix + extracted lessons). Issue #394 asks for a new invoke-only
sibling skill, `transcript-narrative`, that encodes this synthesis workflow. Keeping it a
sibling (rather than a `transcript-analysis` subcommand) preserves `transcript-analysis` as a
tight metrics reference and avoids the repo's first argument-dispatched multi-mode skill. The
outcome: a reader/agent doing a retrospective lands on the right skill, and the narrative
workflow is captured once instead of re-improvised per investigation.

## Approach

Add one new skill plus the wiring its invocation mode requires. Four files change.

**1. New skill body — `claude/.claude/skills/transcript-narrative/SKILL.md`**

Frontmatter (invoke-only, mirrors `transcript-analysis`):
```yaml
---
name: transcript-narrative
description: Produce a narrative case study / annotated timeline from Claude Code session transcripts — verbatim prompts, phase buckets, quantitative metrics, extracted lessons. For raw quantitative metrics use transcript-analysis.
---
```
- **No `TRIGGER when:` clause** → invoke-only (the test treats a no-TRIGGER /
  no-`disable-model-invocation` skill as requiring a `name-only` override).
- **No `disable-model-invocation: true`** — mutually exclusive with `name-only` per
  `test_name_only_skill_does_not_carry_disable_flag`.
- `user-invocable: true` is the default for repo skills; the issue lists it, so include it
  explicitly to match intent. (It is not load-bearing for any test, but harmless and
  documents intent.)

Body encodes the six-step workflow from the issue, platform-agnostic, no private-project
identifiers, under the 200-line cap (`check-skill-length.sh` counts the whole file):

1. **Scope the analysis** — identify branches, session date-range, repos. Use
   `transcript-analysis.py buckets` to enumerate branches and per-branch models.
2. **Extract verbatim user turns** — describe the approach in prose (read session JSONL,
   filter to `user`-role turns, **exclude `isSidechain` subagent turns**). Capture BOTH
   openers AND mid-session steering turns (later redirects are often the pivotal ones). Per
   the issue's shorten-first guidance, **describe the extraction, do not vendor a script.**
3. **Bucket into phases / annotated timeline** — group by branch and investigative phase;
   per phase record dates, session count, active minutes, the artifact (PR/branch), and the
   1–3 most consequential verbatim prompts.
4. **Cross-reference quantitative metrics** — name the relevant `transcript-analysis`
   subcommands as the quantitative appendix: `fail-seq` (convergence vs thrashing),
   `duration` (active vs idle), `subagents` (subagent vs main split), `pr-link` (branch→PR),
   `review-trace` (review/denial/reviewer timeline).
5. **Redaction — written accurately, NOT as the issue's literal text.** Quoted prompts are
   extracted directly from JSONL, so the CLI's `--redact` flag (which exists only on
   `audit-routing`, and only remaps project-dir names) does **not** touch them. The skill
   says: manually scan every quoted prompt for PII/credentials before any shared/public
   surface; `--redact` is available on `audit-routing` to anonymize project names in the
   quantitative appendix. (See "Deviation from issue" below.)
6. **Extract lessons ranked by prompt-arc visibility** — tie each lesson to the verbatim
   evidence that surfaced it.

Plus a one-line **cross-reference back to `transcript-analysis`** in the body (the
description already points forward; the body line satisfies the reciprocal requirement
clearly and at zero description-budget cost).

**2. Reciprocal cross-reference — `claude/.claude/skills/transcript-analysis/SKILL.md`**

Add one line in the body (not the description — description budget) pointing to
`transcript-narrative` for the qualitative synthesis workflow. One-line, minimal edit.
This restages a second SKILL.md, so `/skill-review` runs on it too (cheap, one-line).

**3. skillOverrides — `claude/.claude/settings.json`**

Add `"transcript-narrative": "name-only",` to the `skillOverrides` block, grouped with the
other name-only entries (place it adjacent to `transcript-analysis` for readability).

**4. Documentation — `docs/skills.md` (REQUIRED, not in the issue's AC)**

`test_skill_overrides_documented_in_docs_skills_md` asserts every non-`on` override has a
`` | `/<name>` | `` row in `docs/skills.md`. Without this the suite fails. Changes:
- Add a `` | `/transcript-narrative` | … | `` row to the "Skills available by name" table.
- Add a `/transcript-narrative` bullet to the prose list above it (mirrors the existing
  per-skill bullets, each noting "Model-invocable by exact name; description excluded …").
- Update the now-stale counts in the curated workflow table's surrounding prose. Verified
  exact sites (re-grep `Eight skills` / `four workflow` at implementation time to confirm
  line numbers haven't drifted):
  - **`docs/skills.md:27`** — `"Unlike the four workflow-utility name-only skills (brief,
    handoff, read-docx-comments, transcript-analysis), …"` → bump "four" → "five" **and** add
    `transcript-narrative` to the parenthetical list.
  - **`docs/skills.md:33`** — `"Eight skills in this repo use skillOverrides: name-only"` →
    "Nine"; and `"The four workflow utilities carry no TRIGGER blocks"` → "five".
  - `loop` and `simplify` live in a **separate** table (lines 69–70) and are excluded from
    the "Eight"/curated count — do not touch their rows or recount them into this number.

### Deviation from issue (Step 5 `--redact`)

The issue's Step 5 says to "run `transcript-analysis.py` with `--redact`" to redact quoted
prompts. Verified against the CLI source: `--redact` is a `store_true` flag on the
`audit-routing` subcommand only, and it remaps project-directory names to anonymized labels —
it does not exist on `buckets`/`fail-seq`/`duration`/`subagents`/`pr-link`/`review-trace`,
and it never sees the verbatim prompts (those come from direct JSONL extraction in Step 2).
Writing the skill to claim `--redact` redacts quoted prompts would be inaccurate. The skill
will instead require a manual PII/secret scan of quoted prompts and scope `--redact` to the
`audit-routing` appendix. Flagging because it departs from the issue's literal wording.

### Alternatives considered

- **Make it a `transcript-analysis` subcommand / mode** — rejected for the issue's stated
  reason: different shape (open-ended synthesis vs deterministic metrics), different reader,
  and it would introduce the repo's first multi-mode dispatched skill. Sibling is lighter.
- **Vendor a prompt-extractor helper script under the skill dir** — rejected per the issue's
  shorten-first guidance and `docs/skills.md` "shorten first rather than extract auxiliary
  files." Describe the JSONL-filtering approach in prose; revisit only if the logic proves
  worth keeping.
- **Put the reciprocal cross-ref in `transcript-analysis`'s description** — rejected; the
  body line is zero description-budget cost and reads more naturally.

## Critical files

| File | Change |
|---|---|
| `claude/.claude/skills/transcript-narrative/SKILL.md` | **create** — frontmatter + 6-step body, <200 lines |
| `claude/.claude/skills/transcript-analysis/SKILL.md` | one-line reciprocal cross-reference in body |
| `claude/.claude/settings.json` | add `"transcript-narrative": "name-only"` to `skillOverrides` |
| `docs/skills.md` | add table row + prose bullet; bump "Eight→Nine" / "four→five" counts |

**Reuse / mirror (don't reinvent):** copy the frontmatter shape and tone from
`transcript-analysis/SKILL.md`; mirror the `skillOverrides` entry format and the
`docs/skills.md` bullet + table-row format already used for the four workflow utilities.
Subcommand names referenced in the body are taken verbatim from `transcript-analysis.py`
(`buckets`, `fail-seq`, `duration`, `subagents`, `pr-link`, `review-trace`, `audit-routing`).

## Verification

- `.venv/bin/pytest claude/.claude/` — full suite. Specifically exercises:
  `TestNameOnlySkillContracts` (skill file exists, no disable flag),
  `test_skill_overrides_documented_in_docs_skills_md` (docs row present),
  `TestModelInvokableSkillTriggerContracts` (confirms transcript-narrative is NOT treated as
  model-invokable, i.e. the name-only override took effect).
- `.venv/bin/ruff check claude/.claude/` — lint (no Python added, but run for parity).
- `/skill-review` on both staged SKILL.md files — clean (behavioral-equivalence marker;
  hook-enforced on commit). Watch the brevity rule against the new body.
- `/code-review` on the full diff.
- Manual: `git commit` dry-run path — confirm `check-skill-length.sh` passes (new file
  <200 lines) and `require-skill-review.sh` is satisfied.
- Manual smoke: from a session, `/transcript-narrative` resolves and loads; confirm the
  forward/back cross-references read correctly.

## Out of scope

- No change to `transcript-analysis.py` (the CLI). The skill documents existing subcommands.
- No new vendored extractor script (see Alternatives).
- No broader rewrite of `transcript-analysis/SKILL.md` beyond the one cross-reference line.
