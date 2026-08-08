# Port the `user-input` subcommand into transcript-analysis.py

## Context

Recover a finished, unmerged feature that currently exists only as a
reachable-but-unstable git stash commit — a `user-input` subcommand for
`claude/.claude/scripts/transcript-analysis.py` that answers "what prompts
did I write, and where did I redirect Claude?" by scanning transcripts for
typed user prompts and classifying each as INITIAL (first prompt in a
session), FOLLOWUP (a later prompt with no frustration-phrase match), or
EXPLICIT_CORRECTION (a later prompt matching a `STRUGGLE_PHRASES` entry).
This needs to land in `main` before a `git stash clear` destroys it — the
work is stranded, not designed here.

This plan covers **Feature A only**. A second, unrelated stashed feature
(excluding a generated block from the CLAUDE.md length hook) was also under
consideration; the engineer resolved that one this session: the mechanism
was originally designed for a different repository and abandoned there
after that repo's plan-review found it lets an agent dodge the length cap
by stuffing prose into the generated block instead of writing concise
prose. It will not be ported here, in this repo, or anywhere. No branch,
worktree, or code exists for it as part of this plan.

## Approach

**Root problem:** `cmd_user_input` and its six helper functions exist only
in stash commit `89d9953` (base `36311cb`, ~180 commits behind current
`main` at `bf3ea41`), whose direct-patch-apply fails on both hunks because
`transcript-analysis.py` has grown from ~1700 to 5492 lines since that
base. The feature must be hand-ported, reconciled against helpers that
didn't exist at the stash's base.

**Givens:**
- G1: The three-way classification scheme (INITIAL / FOLLOWUP /
  EXPLICIT_CORRECTION) and the six-function decomposition are accepted as
  designed; this plan ports them, it does not redesign them. Reason: this
  is recovery of prior, feature-complete work, not a new-feature design
  exercise — redesigning the classification scheme is out of scope for a
  recovery task. `[unverified — inherited from the stash's own design,
  not re-litigated with the engineer this session]`
- G2: The flag surface (`--projects --branches --since --until --out
  --corrections-only --truncate-chars --redact`) is accepted as-is.
  `[verified: claude/.claude/scripts/transcript-analysis.py, every
  sibling subcommand registered via sub.add_parser (grep count: 21) uses
  the same --projects/--branches/--since/--until/--out naming and
  argparse.type=_iso_date convention for date flags]`

**Per-mechanism justification** (anchors noted):

1. Insert the six functions immediately after `cmd_struggle` and before
   `cmd_duration` (anchors: root). `cmd_struggle` currently ends at line
   627 in `claude/.claude/scripts/transcript-analysis.py` — same relative
   position as the stash's own insertion point (`@@ -295,6 +295` in the
   stash diff, immediately after `cmd_struggle` at that base too).
   Matches this file's existing convention of grouping a subcommand's
   private helpers directly above its own `cmd_*` entry point.
2. Register the `user-input` subparser directly after the `p_struggle`
   block (currently ends ~line 5094, immediately before `p_duration`)
   (anchors: G2) — mirrors both the function-ordering convention above and
   every neighboring subparser's flag style.
3. Build the redact map via the existing `_build_redact_map()` helper
   (`claude/.claude/scripts/transcript-analysis.py:3005`) instead of
   porting the stash patch's own inline label-collection loop (anchors:
   root). `_build_redact_map` postdates the stash's base commit and is
   the single source every other `--redact` caller in the file now uses
   (2 call sites, `_mod.py:3116` and `:3689`) — porting the stash's
   13-line inline loop verbatim would reintroduce logic the file has
   since centralized, and would silently diverge from it (e.g. it
   wouldn't participate in the multi-root `_RedactMapKey` shape the
   current helper supports).
4. Document the `--redact` scope gap at both surfaces a user actually
   reads (anchors: G1 — this documents the ported behavior, doesn't
   change it): every existing `--redact` implementation in this file
   anonymizes project labels and session IDs only, never free-text
   message content (grep of `_redact_proj_label`/`_redact_session_id`
   call sites confirms no content-scrubbing path exists anywhere in the
   file today). `cmd_user_input` prints raw prompt text via
   `_truncate_prompt_text` regardless of `--redact`, consistent with that
   existing convention, not a regression from it — but the file already
   carries this exact caveat shape once (`review-trace` — "not
   publish-safe under the default machine-wide scope") for an analogous
   case, so `user-input` gets the same explicit warning. Two surfaces,
   per `ciso-reviewer`'s plan-review finding (S2): the SKILL.md caveat
   (consulted by an agent invoking the skill) is necessary but doesn't
   reach a human running the CLI directly — the stash's own
   `--redact` argparse help string, `"Anonymize project names for public
   reporting."`, overstates what the flag does at exactly that
   direct-invocation surface. Both need the fix, not just the doc.
5. Reuse `_content_text`, `_parse_ts`, `_iso_date`, `_fmt_date`, `_fam`,
   `_derive_proj_label`, `_redact_proj_label`, `_projects_glob`,
   `_branch_filter`, `iter_sessions`, `PROJECTS_DIR`, and
   `STRUGGLE_PHRASES` as-is — all confirmed present and unchanged in
   shape at current `HEAD`.

**Deviation from the brief's step list:** the brief's `§6` steps (matching
what actually shipped in the stash) include no test changes — `git show
--stat 89d9903` touches only the script, its settings.json copy, and the
skill doc. But every other `cmd_*` subcommand in this file has a dedicated
test class in `claude/.claude/scripts/tests/test_transcript_analysis.py`
(`TestCmdStruggle`, `TestBuckets`, `TestReviewTrace`, `TestAuditRouting`,
… 30+ classes, one per subcommand/helper group). Landing `user-input`
without a `TestCmdUserInput` class would be the first exception to that
convention in the file and would very likely draw a `staff-sdet`
test-coverage finding at `/code-review`. This plan adds test coverage as
an explicit step the brief's literal list omitted.

**Alternatives considered:** porting via `git apply` with manual
conflict-hunk resolution was rejected — both hunks conflict outright (not
a fuzzy-offset case `git apply -3` could reconcile) because the anchor
context on each side has fully diverged; a clean transplant-and-reconcile
of the function bodies is more reliable than coaxing a 3-way merge across
180 commits of drift.

## Critical files

- `claude/.claude/scripts/transcript-analysis.py` (5492 lines) — add
  `_is_fresh_user_prompt`, `_is_unrecognized_user_list_record`,
  `_classify_prompt`, `_attribute_model_to_prompt`,
  `_truncate_prompt_text`, `cmd_user_input` after `cmd_struggle` (~line
  627); register `p_user_input` after the `p_struggle` block (~line
  5094). **Reuse, don't reimplement:** `_build_redact_map()` (§Approach
  item 3) in place of the stash's inline redact-map loop; `_content_text`,
  `_parse_ts`, `_iso_date`, `_fmt_date`, `_fam`, `_derive_proj_label`,
  `_redact_proj_label`, `_projects_glob`, `_branch_filter`,
  `iter_sessions`, `PROJECTS_DIR`, `STRUGGLE_PHRASES` — all unchanged
  from the stash's usage and present at current `HEAD`. **Deviation from
  the stash's own text** (§Approach item 4 / `ciso-reviewer` finding):
  the `--redact` argument's `help=` string must not carry over the
  stash's `"Anonymize project names for public reporting."` verbatim —
  reword to name the actual scope, e.g. `"Anonymize project labels and
  session IDs for public reporting (prompt text is not redacted —
  review before sharing)."`
- `claude/.claude/skills/transcript-analysis/SKILL.md` (78 lines) —
  three additions, wording reproducible via `git diff 89d9953^ 89d9953 --
  claude/.claude/skills/transcript-analysis/SKILL.md` (no external file
  needed):
  1. One row in the "Which subcommand to use" table:
     `| What prompts did I write, and where did I redirect Claude? | user-input |`
  2. A new "## Reading user-input output" section, verbatim from the
     stash delta:
     ```
     ## Reading user-input output

     ```
     Total sessions: 12   Fresh prompts: 47   Explicit corrections: 6 (12.8%)
     ```

     Three classifications appear in the output:

     - **INITIAL** — the first typed prompt in a session. Sets the direction of the conversation.
     - **FOLLOWUP** — a subsequent prompt with no frustration-phrase match. Quiet redirects: refining scope, asking follow-up questions, or continuing the thread without explicit frustration.
     - **EXPLICIT_CORRECTION** — a subsequent prompt containing a phrase from the `struggle` phrase list (e.g., "try again", "not that"). The matched phrase is noted in parentheses.

     Every non-INITIAL prompt counts as a course correction under the "frustration phrases + all follow-up prompts" definition. The FOLLOWUP vs EXPLICIT_CORRECTION split lets you distinguish polite redirects from explicitly frustrated ones.

     Use `--corrections-only` to strip initial prompts when you only want the steering moments. Use `--since`/`--until` to focus on a date window (e.g., a sprint or a specific project phase).
     ```
  3. New in the "## Caveats" section (§Approach item 4 — not in the
     stash delta, added by this plan):
     `- `user-input` prints raw prompt text verbatim regardless of
     `--redact` — that flag anonymizes project labels and session IDs
     only, matching every other `--redact` implementation in this file
     (none scrub message content). Review output before pasting it
     anywhere public.`
  4. Update the frontmatter `description` (line 3) to mention the new
     per-session prompt narrative — per `skill-review`'s plan-review
     finding, the description is the always-loaded auto-trigger surface,
     and the existing sentence's "correction-signal frequency" phrase
     covers `struggle`'s aggregate count, not `user-input`'s per-session
     classified narrative; a session asking "what did I type, where did
     I redirect" won't auto-trigger this skill without it. Insert before
     the closing "or a corpus-wide census" clause: `, a per-session
     narrative of typed prompts classified as initial/followup/explicit
     correction,`.
- `claude/.claude/skills/error-mode-analysis/SKILL.md` (139 lines) —
  **explicit user-requested addition, not required for `user-input` to
  function; scoped in by direct request, not silent scope creep.** Step
  2 ("Collect transcript signals") names `review-trace` as "the most
  load-bearing subcommand for this step" but doesn't yet point at any
  subcommand for locating session-level course-correction moments —
  today that work happens by manually re-reading full transcripts (see
  the verbatim-quote narrative in tracked issue #472's Finding 6/7,
  produced by hand). Add one sentence after that `review-trace` line:
  `user-input --corrections-only surfaces every FOLLOWUP/
  EXPLICIT_CORRECTION moment in a session with the prompt text itself —
  use it to populate Step 4's Human-unique and Cross-session process
  buckets with verbatim evidence instead of re-reading full transcripts
  by hand.` (Bucket names per `skill-review`'s plan-review finding:
  match Step 4's table labels exactly — "Cross-session process," not
  "Cross-session" — so a future bucket rename doesn't silently break
  the pointer.) One sentence, pointer-only (per this skill's own "invoke by
  name, don't restate their procedures" rule for `transcript-analysis`)
  — no procedure duplication, stays under the file's own voice and
  length norms.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` (8655
  lines) — new `TestCmdUserInput` class following the existing
  `fake_projects` / `_write_jsonl` / `_user_msg` / `_asst` /
  `type("A", (), {...})()` fixture pattern used by `TestCmdStruggle`
  (`:6090`). Base coverage: INITIAL vs FOLLOWUP vs EXPLICIT_CORRECTION
  classification (including a struggle-phrase match and a control phrase
  that shouldn't match, mirroring `TestCmdStruggle`'s own
  hallucinat/stale-cache pair), `--corrections-only` filtering,
  `--since`/`--until` date bounds, `--redact` (assert project labels are
  remapped, assert prompt text is *not* altered by `--redact` — this
  documents item 4's caveat as a test, not just prose),
  `--truncate-chars` (including `0` = no truncation), and the
  unrecognized-shape stderr counter.

  **Additional cases required per `staff-sdet`'s plan-review pass**
  (each names a real untested branch in the ported function bodies, not
  a hypothetical):
  1. `_is_fresh_user_prompt` exclusion guards — one fixture record each
     for `isMeta=True`, `isSidechain=True`, and a record carrying
     `toolUseResult`/`sourceToolUseID`/`sourceToolAssistantUUID`;
     assert each is excluded from `total_fresh_prompts`.
  2. Empty-string content on the plain-string path — assert whichever
     behavior the code actually exhibits (the string path has no
     `.strip()` guard the list-of-blocks path has); pin it, don't guess.
  3. `_attribute_model_to_prompt`: no matching assistant record found →
     `model_fam == "unknown"`; and a record from a *different*
     `sessionId` interleaved between a prompt and its reply in the same
     file → the interleaved record must not be used for attribution.
  4. `session_entries` sort order: ≥2 sessions with distinct `first_ts`
     sort ascending; a session whose first prompt has an unparseable/
     missing timestamp (`first_ts is None`) — assert its actual placement
     (the sort key `(e["first_ts"] or 0.0)` places it at the front,
     ahead of every real-dated session; confirm this is the intended
     behavior or flag it as a bug during the port, don't silently keep
     it untested).
  5. First prompt in a session containing a `STRUGGLE_PHRASES` match
     still classifies `INITIAL` with empty `matched_phrase` (the
     `is_first_in_session` override takes precedence over phrase
     matching).
  6. `--branches` filtering excludes a non-matching branch's prompts.
  7. `--redact`'s `claude-config` self-exception: a fixture with a
     project literally labeled `claude-config` plus at least one other
     label — assert `claude-config` passes through unredacted and the
     other is remapped to `private-project-N`.
  8. `--out` write failure (mock `Path.write_text` to raise `OSError`,
     or target an unwritable path) — assert exit code 1 and the stderr
     message shape.

## Verification

1. Manual smoke test from the worktree:
   `../../../.venv/bin/python claude/.claude/scripts/transcript-analysis.py user-input --since 7d`
2. `../../../.venv/bin/pytest claude/.claude/`
3. `../../../.venv/bin/ruff check claude/.claude/`
4. `/code-review` — fix anything it finds (expect `staff-sdet` to check
   the new test class; the SKILL.md addition may draw a doc-scope check
   too).
5. Commit, `/ready-for-review`, then open the PR — permitted without
   asking per this session's confirmed
   `~/.claude/autonomous-shipping-required` sentinel. PR body includes
   `Closes #589` (github.com/jcdendrite/claude-config/issues/589,
   filed for this plan).

## Out of scope

- Feature B (CLAUDE.md length hook generated-block exclusion) — dropped
  per the engineer's decision this session (see Context). Not ported,
  not planned further here.
- Refactoring any of the other 21 `cmd_*` subcommands while adding the
  22nd.
- `stash@{2}` `3cb43e3` (~470 lines of unrelated WIP) — needs its own
  triage, not folded in here.
- Dropping any stash entry (including the Feature A duplicate `stash@{4}`
  `7ad509f`) — pending engineer authorization, not part of this PR.
- Cleanup of the stale `claude-md-length-generated-block-exclusion`
  branch/worktree — now that Feature B is dropped entirely, that
  branch/worktree has no remaining purpose, but deleting it needs the
  engineer's explicit authorization (not requested this session) and
  doesn't block this PR.
