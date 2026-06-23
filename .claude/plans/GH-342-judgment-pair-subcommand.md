# Plan: `judgment-pair` subcommand for transcript-analysis.py (GH-342)

## Context

**Goal:** add a `judgment-pair` subcommand to `transcript-analysis.py` that mines
transcripts for (review-skill output, user response) pairs, collapsing a
multi-hour manual JSONL read into a seconds-long query.

The existing `review-trace` subcommand finds *where* review-skill invocations
happen (timestamps, line numbers, counts) but never emits the review text or the
user's reply. Surfacing those pairs is the manual process that was done by hand
across ~150 JSONL files for a presentation on critical reading when working with
AI. This subcommand automates the extraction; **judging** which pairs represent
real human-catches-AI-error cases stays manual and out of scope.

Two corrections to the issue's implementation notes, confirmed by reading the
script and the transcript corpus:

1. The issue says to "reuse" `_is_fresh_user_prompt` and references an existing
   `user-input` subcommand. **Neither exists.** `_is_fresh_user_prompt` will be
   written fresh. `_content_text` and the `review-trace` skill-detection pattern
   *do* exist and are reused.
2. The issue defines REVIEW OUTPUT as the *first* assistant turn after the skill
   result. For multi-turn reviews (the common `code-review` / `ready-for-review`
   case) that turn is the "starting review…" preamble + agent spawns, not the
   findings the user reacts to. **Per user decision, REVIEW OUTPUT is instead the
   last assistant turn with non-empty text before the user's reply** — the
   synthesis the correction actually responds to. (Requiring non-empty text folds
   in the single-turn-review fallback: when only one text turn exists, last == first.)

## Approach

Add one subcommand `cmd_judgment_pair` plus one module-level helper
`_is_fresh_user_prompt`, mirroring `cmd_review_trace`'s structure (it is the
nearest sibling: same date-window semantics, same skill-detection, same
per-session branch filter).

**Per session** (records from `iter_sessions`, main thread only):

1. Resolve session branch from the first main-thread record carrying
   `gitBranch`; skip the session if a `--branches` filter excludes it (same as
   `cmd_review_trace`).
2. Walk records by index. A *matching skill invocation* is a main-thread
   assistant `Skill` tool_use whose `input.skill` is in the `--skills` set
   (default `REVIEW_SKILLS`). Record its skill name, 1-based line number, and
   timestamp.
3. Apply the `--since` / `--until` day-window to the **invocation timestamp**,
   reusing `cmd_review_trace`'s exact boundary math (`since_ts`;
   `until_epoch = day_start + 86400`; records with no parseable ts excluded when
   a boundary is active).
4. **REVIEW OUTPUT** = the last main-thread assistant turn with non-empty
   `_content_text` in the window `(invocation, window_end)`, where `window_end`
   is the index of the next *fresh user prompt* **or** the next matching skill
   invocation, whichever comes first. Bounding by the next matching invocation
   gives back-to-back invocations distinct outputs; the skill body's own
   tool_result is a tool-result-bearing user record, so `_is_fresh_user_prompt`
   skips it automatically and no explicit tool_use_id match is needed.
5. **USER RESPONSE** = the next fresh user prompt's text. If none exists before
   end of session, emit `(no user response — end of session)`.
6. Truncate REVIEW OUTPUT to `--truncate-chars` (default 1000); leave the user
   response untruncated (it is the key signal and usually short).

`_is_fresh_user_prompt(rec)` returns True iff: `type == "user"`, not
`isSidechain`, not `isMeta`, not `isCompactSummary`, content is **not**
tool-result-bearing, and `_content_text` is non-empty after strip. (Grounded in
a corpus scan: `userType` is uniformly `"external"` so it is not a
discriminator; every `isMeta` record carries injected text, so `isMeta` must be
filtered explicitly rather than inferred from emptiness.)

**Output** (matches the issue's block format), written to `--out` if given else
stdout:

```
### <proj-label> · <session-id-prefix> · <YYYY-MM-DD>
Skill: <name>  (line <N>)

--- REVIEW OUTPUT (truncated to <N> chars) ---
<review text>

--- USER RESPONSE ---
<user text>
---
```

- `proj-label` via existing `_derive_proj_label(jsonl)`.
- `session-id-prefix` = `jsonl.stem[:8]`.
- date via `_fmt_date(_parse_ts(invocation_ts))`.

**Argparse** (`judgment-pair` subparser, mirroring `review-trace`): `--projects`
(default `*`), `--branches`, `--since`/`--until` (`type=_iso_date`), `--skills`
(default `",".join(REVIEW_SKILLS)`, split on comma into a set — free-form, no
`choices` restriction so any skill is targetable), `--truncate-chars`
(`type=int`, default 1000), `--out` (default None → stdout).

*Alternatives considered:* anchoring REVIEW OUTPUT on the matching
`tool_use_id`'s tool_result (issue's literal "after the skill result") was set
aside — for Option A the window already starts after the invocation and
`_is_fresh_user_prompt` skips the body result, so the extra lookup adds a
failure mode (missing/`is_error` result on harnesses without Skill support, per
the `skill-pair` note) for no benefit. Bounding the window by *only* the next
user prompt (ignoring the next invocation) was set aside because back-to-back
routed invocations would then share one synthesis; bounding by both keeps
attribution clean.

## Critical files

- **`claude/.claude/scripts/transcript-analysis.py`** — add module-level
  `_is_fresh_user_prompt` (near `_content_text`), `cmd_judgment_pair` (after
  `cmd_review_trace`), and the `judgment-pair` subparser in `main()`.
  **Reuse:** `_content_text`, `_derive_proj_label`, `_parse_ts`, `_iso_date`,
  `_fmt_date`, `_projects_glob`, `_branch_filter`, `iter_sessions`,
  `REVIEW_SKILLS`, and `cmd_review_trace`'s date-window block.
  **Also fix the module docstring** (line 2): it currently claims
  `No writes; pr-link is the only subcommand that touches the network`. The new
  `--out` flag writes a file, so the "No writes" claim becomes false — amend to
  note that `judgment-pair --out` writes a report file (still no network).
- **`docs/transcript-analysis.md`** — add a `## judgment-pair` reference section
  matching the existing per-subcommand sections (purpose, flags, a **synthetic**
  example output block — do not paste real review text or private project
  content). Placed alongside `## review-trace`.
- **`claude/.claude/skills/transcript-analysis/SKILL.md`** — add one row to the
  question→subcommand table (e.g. "Where did a human push back on an
  AI review's output? | `judgment-pair`") and, if warranted, one note line. This
  is the skill's discovery surface; without it the subcommand is unroutable.
  **Editing SKILL.md triggers hook-enforced `/skill-review`** — budget that
  pipeline step (see Notes).
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** — add
  `TestJudgmentPair` + a `_judgment_pair_args(...)` helper, mirroring
  `_review_trace_args`. **Reuse** fixtures `fake_projects`, `_write_jsonl`,
  `_asst`, `_user_msg`, `_skill_use`, `_tool_result`.

Test cases:
- Single-turn review: invocation → assistant findings → user reply yields one
  pair with the findings + reply.
- Multi-turn review: preamble turn + intermediate tool turns + final synthesis →
  pair captures the **last** text turn (synthesis), not the preamble.
- `--skills` override and default; skill outside the set excluded.
- Sidechain skill invocation excluded.
- tool_result-bearing and `isMeta` user records are **not** taken as the user
  response (skipped to the next genuine prompt).
- `--since` / `--until` boundary filtering on the invocation ts.
- No user response before end of session → graceful sentinel text.
- `--truncate-chars` truncates the review output.
- `--branches` filter.
- `--out PATH` writes the blocks to the file.
- Unit tests for `_is_fresh_user_prompt` (each exclusion arm + the True case).

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k JudgmentPair` (from a worktree; `.venv` lives at main worktree root).
- `../../../.venv/bin/ruff check claude/.claude/scripts/transcript-analysis.py`.
- Read-only smoke run against the live corpus and eyeball a few blocks:
  `python3 claude/.claude/scripts/transcript-analysis.py judgment-pair --skills code-review --since 2026-05-01 --truncate-chars 800`.

## Out of scope

- Classifying which pairs are real judgment failures (stays manual, per issue).
- Redaction of output: this is a local read-only analysis tool; its output is
  not committed. No `--redact` flag (unlike `audit-routing`). Tests use only
  synthetic fixtures.

## Notes for execution

- Implementation-only (script + tests); no new design doc or cross-team
  contract → no draft-PR-before-implementation step.
- This file is under `claude/`, which is stowed to every user who clones the
  repo; the change is additive and read-only at runtime (new subcommand, no
  behavior change to existing commands), so blast radius is low.
- Branch slug for after ExitPlanMode approval: `GH-342-judgment-pair-subcommand`.
- Pipeline before PR: `/code-review` then `/ready-for-review`. The SKILL.md edit
  (transcript-analysis routing table) makes **`/skill-review` required and
  hook-enforced** (`require-skill-review.sh` blocks commit until its marker is
  written); `/code-review` dispatches it automatically. No agent files touched,
  so no agent-review.
