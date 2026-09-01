# Exclude forwarded `<task-notification>` text from correction/frustration phrase matching

## Context

`transcript-analysis.py`'s correction/frustration phrase matchers (`STRUGGLE_PHRASES`) score a "user" turn as a correction/struggle signal whenever a phrase like "incorrect," "hallucinat," or "try again" appears in that turn's text — but a harness-injected `<task-notification>` record (forwarded when a background task or subagent finishes) is also a `type: "user"` record with plain-string content, so its `<summary>` text (written by the subagent describing its own findings, not by the human) is scored identically to genuine human input. On this repo's corpus, this produced an 82% false-positive rate on `EXPLICIT_CORRECTION` classifications (GH-752, source: `error-mode-analysis` run 2026-08-27, finding A15). The fix must exclude `<task-notification>...</task-notification>` envelope text from phrase matching at every site that scores a turn against `STRUGGLE_PHRASES`, so future `error-mode-analysis`/`transcript-analysis` runs on subagent-orchestration-heavy repos stop inheriting this bias.

## Evidence gathered this session

**Three structural-sibling match sites, all matching the same `STRUGGLE_PHRASES` list against raw turn text with no envelope exclusion** (`claude/.claude/scripts/transcript-analysis.py`):
- `cmd_struggle` (~line 396-399): `text = _content_text(msg.get("content", "")).lower(); if any(phrase in text for phrase in STRUGGLE_PHRASES): ...` — feeds the `struggle` subcommand's per-branch/per-model table.
- `_classify_prompt` (~line 464-476), called from `cmd_user_input` (~line 581-582): classifies each fresh prompt as INITIAL/FOLLOWUP/EXPLICIT_CORRECTION; this is the exact path the issue's 56-flagged/~10-genuine measurement came from (`user-input --corrections-only`).
- `_friction_struggle_turn_events` (~line 8964-8980): counts struggle turns for `cmd_friction_count`'s composite friction score (`_friction_signals`, ~line 8983).

**`<task-notification>` record shape, verified against a live transcript** (a session JSONL file under this machine's Claude Code projects directory): `type: "user"`, `isMeta` absent/falsy, `isSidechain: False`, `promptId` present, `message.content` a **plain string** (not a list-of-blocks) whose full text is the `<task-notification>...</task-notification>` block, e.g.:
```
<task-notification>
<task-id>b336kkt8h</task-id>
<tool-use-id>toolu_01RxTALjoiL9gg3XVs33N2iu</tool-use-id>
<output-file>/private/tmp/claude-501/.../tasks/b336kkt8h.output</output-file>
<status>completed</status>
<summary>Background command "../../../.venv/bin/pytest claude/.claude/ -q 2&gt;&amp;1 | tail -30" completed (exit code 0)</summary>
</task-notification>
```
Because content is a plain string, this record passes every "fresh user prompt" discriminator in the file unchanged (`_is_fresh_user_prompt`, `_is_fresh_user_prompt_for_narrative`, and `cmd_struggle`'s inline `rtype in ("user", "human")` check) — none of them distinguish a harness-forwarded notification from a real keystroke. A `<summary>` field can contain STRUGGLE_PHRASES-matching text (e.g., a subagent reporting "still failing" or "incorrect" in its own findings).

**No existing handling.** `grep -rn "task-notification"` across `claude/.claude/scripts/transcript-analysis.py` and `claude/.claude/scripts/transcript_analysis/*.py` returns nothing — this is a net-new exclusion, not a regression of prior logic.

**Corpus check for other envelope shapes.** Scanning `<tag>`-shaped strings across ~60 sampled user-type records in this repo's own transcripts found only `task-notification` as a subagent/background-summary-forwarding envelope tag (full tag list included things like `bash-stdout`, `local-command-stdout`, `system-reminder` — none of which forward a subagent's own prose the way `task-notification`'s `<summary>` does). No evidence was found for a second envelope shape needing the same treatment.

**`_content_text`** lives in `claude/.claude/scripts/transcript_analysis/render.py` (a dependency-free text-formatting helper module, already imported piecemeal into the main script's namespace) — the natural home for a new sibling helper, since all three match sites already import from this module.

**Test conventions** (`claude/.claude/scripts/tests/test_transcript_analysis.py`): `TestCmdStruggle` (~line 10044), `TestCmdUserInput` (~line 10167), and the friction-count test class (struggle-turn test ~line 12496) each build fake session JSONL via `_user_msg`/`_ui_user` + `_write_jsonl` (helpers in `conftest.py`) and assert on captured stdout / returned JSON. `conftest.py`'s `_user_msg(content, *, branch=..., ts=...)` builds a bare `{"type": "user", ...}` record — a task-notification-shaped record for these tests is just `_user_msg("<task-notification>...</task-notification>")` (plain-string content, matching the verified real shape).

**Docs**: `docs/transcript-analysis.md`'s `## struggle` section (~line 105) documents the `struggle` subcommand's phrase-matching behavior for users; `user-input` and `friction-count` have no dedicated doc sections in that file (undocumented there already, out of scope to add). `CHANGELOG.md`'s `[Unreleased]` section is the repo's convention for recording behavior-changing fixes to `claude/` tooling.

## Approach

Add one shared string helper, `_strip_task_notifications`, to `transcript_analysis/render.py` that removes `<task-notification>…</task-notification>` spans from a turn's text, and call it at exactly the three places that score text against `STRUGGLE_PHRASES` — `cmd_struggle`, `_classify_prompt`, and `_friction_struggle_turn_events`. Nothing else changes: the text `cmd_user_input` prints inside each `~~~text` block is still the unmodified record, because the strip happens inside `_classify_prompt` on a by-value copy rather than on the string the renderer holds.

**Shape of the change.**

```python
# transcript_analysis/render.py, beside the existing _CONTROL_CHAR_RE / _sanitize_table_cell pair
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)


def _strip_task_notifications(text: str) -> str:
    """Remove `<task-notification>...</task-notification>` spans from a user turn's text.

    The harness delivers a finished background task or subagent as a plain
    `type: "user"` record whose body is that envelope, so its `<summary>` is
    the subagent's own prose about its own findings, not human input. An
    unterminated envelope is left in place rather than swallowing the rest of
    the turn. Each span is replaced by a single space, matching
    `_content_text`'s own block separator, so removing one cannot weld two
    surrounding words into a phrase that was never written.
    """
    return _TASK_NOTIFICATION_RE.sub(" ", text)
```

Call sites, one line each:

- `cmd_struggle` (~:397) — `text = _strip_task_notifications(_content_text(msg.get("content", ""))).lower()`
- `_classify_prompt` (~:472) — `lowered = _strip_task_notifications(text).lower()`
- `_friction_struggle_turn_events` (~:8977) — same expression as `cmd_struggle`

Plus the import into the shim's existing `from transcript_analysis.render import (…)` block (`transcript-analysis.py:95-108`), sorted after `_sanitize_table_cell` since ruff's `I` rule is enabled.

**What this fixes and what it deliberately leaves standing.** After the change, a `<task-notification>` record no longer contributes to `cmd_struggle`'s per-branch table, no longer increments `struggle_turns` in the friction composite, and is no longer labeled `EXPLICIT_CORRECTION` — so it drops out of `correction_count`, out of `phrase_hits`, and out of the "Top struggle phrases" summary line, which is what GH-752's 82% figure measures. It does *not* drop out of `user-input --corrections-only`'s prompt listing: that filter drops only `INITIAL` (`cmd_user_input:704`), so a notification record reappears one line down as a `FOLLOWUP` entry, still counted in `total_fresh_prompts`. Suppressing it there means changing the fresh-prompt discriminator, which shifts INITIAL/FOLLOWUP/total counts and is recorded in **Out of scope**, not smuggled into this fix.

### Assumption ledger

**Root problem.** Three sibling sites match `STRUGGLE_PHRASES` against the raw text of any `type: "user"` record, and a harness-forwarded `<task-notification>` record is one — so a subagent's own prose about its own findings is scored as a human correction.

**Givens** (fixed conditions this design does not reach):

- **G1 — The transcript record schema is the harness's.** Claude Code decides that a finished background task or subagent arrives as a `type: "user"` record with plain-string content and no distinguishing top-level key; this repo can only read what it emits, so exclusion has to be inferred from the text itself.

(Not recalibrating `FRICTION_THRESHOLD` is a condition this plan is fully able to change — it's a local constant in `claude/.claude/hooks/nudge-error-mode-analysis.sh` — so it is recorded once, in **Out of scope**, rather than duplicated here as a "given.")

**Assumption rows:**

1. `<task-notification>` records carry plain-string `message.content`, so `_content_text` returns the envelope verbatim rather than `""`. `[verified: live transcript record quoted above]`
2. `<task-notification>` is the only attested envelope tag that forwards a subagent's own prose. `[verified: corpus scan above — but the sample was ~60 user-type records from one project's transcripts, so this is weak evidence of absence, not proof; a second envelope shape surfacing later needs its own row in the same regex]`
3. The envelope is not guaranteed to be the whole turn, so the strip must be positional-agnostic (strip wherever it appears, not assume it's the entire string). `[unverified — every sampled real example was a standalone envelope with no surrounding text; a mixed turn is not proven impossible, and `re.sub` handles it at zero extra cost regardless]`
4. Envelope content spans multiple lines, so `re.DOTALL` is required and a non-`DOTALL` pattern would silently strip nothing. `[verified: live record quoted above]`
5. `cmd_struggle`, `_classify_prompt`, and `_friction_struggle_turn_events` are the complete set of `STRUGGLE_PHRASES` match sites. `[verified: grep -n "STRUGGLE_PHRASES" claude/.claude/scripts/transcript-analysis.py returns exactly the definition plus these three match expressions]`
6. Tool-result user records already contribute no text at the two flat sites, so this change cannot perturb them. `[verified: render.py:27-32 — _content_text extracts only type == "text" blocks, and a tool_result block yields ""]`
7. `render.py` is the documented home for this helper. `[verified: docs/transcript-analysis-architecture.md's "### render.py" section — "Small display-formatting helpers with no state of their own"]`
8. The helper is reachable and testable through the shim's namespace the way `_content_text` already is. `[verified: transcript-analysis.py:95-108 imports render.py helpers by name into its own namespace; tests reference `_mod.<name>` for names imported this way]`
9. Stripping cannot leak into displayed prompt text. `[verified: cmd_user_input:581 stores the unstripped text in the prompt dict, :716 renders that same string, and _classify_prompt receives a stripped copy by value and returns only (classification, matched_phrase) — never the text itself]`
10. Only the lowercase tag spelling is attested, so the regex stays case-sensitive. `[engineer-verified — declined re.IGNORECASE deliberately: it would strip a literal uppercase-tagged string a human typed, trading a hypothetical miss for a real over-strip]`
11. Ruff enforces import ordering (`I`) and unused-import detection (`F401`), so the new import's position and use are mechanically checked. `[verified: pyproject.toml's ruff config]`
12. `select-tests.py` falls open to the full suite when any changed path matches no domain rule, which `CHANGELOG.md` and this plan file both do. `[verified: select-tests.py's unmatched-path fallback comment and DOMAIN_RULES definition]`
13. GH-752's own cited numbers (56 flagged, ~10 genuine, 82% false-positive) are not re-derived this session. `[unverified — Verification step 5 re-derives before/after counts directly rather than copying the issue's figures into CHANGELOG.md]`
14. Friction checkpoints persist running totals, not re-derivable per-record counts, so a session already in progress keeps whatever it accumulated pre-fix. `[verified: claude/.claude/hooks/nudge-error-mode-analysis.sh's checkpoint-file logic accumulates deltas into a stored total, keyed by session id, evicted after 30 days]`

**Mechanisms:**

- **M1 — One compiled regex plus one shared helper in `render.py`.** `anchors: root, row2, row3, row4, row5, row7`. It is the lightest mechanism that covers all three sibling sites from one definition; this repo's structural-siblings convention requires abstracting once two or more arms share the fix. Lighter primitives weighed and rejected: (a) *an index-based `str.find`/slice loop with no `re` at all* — lighter in dependency terms but heavier in code, needing hand-rolled multi-occurrence and unterminated-opener bookkeeping where `re.sub` gives both for free, and `re` is already imported at `render.py:8` with an established compiled-constant precedent (`_CONTROL_CHAR_RE`); (b) *inlining the same `re.sub` at each of the three sites and sharing nothing* — three copies of one pattern that drift independently, which is exactly the defect the structural-siblings rule names. Heavier primitives rejected: (c) *an XML/HTML parser* — transcript text is prose, not a document; user prompts routinely contain unbalanced `<`/`>` and fenced code, so a parser would either raise or silently restructure the whole turn to remove one fixed literal tag; (d) *a generalized envelope-tag registry* — `system-reminder`, `bash-stdout`, and `local-command-stdout` were also seen in the corpus scan and none forwards a subagent's own prose, so registry-based stripping would change unrelated behavior with no evidence of a defect behind it (`anchors: row2`).
- **M2 — Strip inside `_classify_prompt` rather than at `cmd_user_input`'s text-extraction line.** `anchors: row9`. Stripping at the extraction point would change the string the `~~~text` block renders; stripping inside the classifier keeps display and scoring on separate copies, which is the settled constraint.
- **M3 — Replace each span with a single space, not the empty string.** `anchors: root, row3`. Empty-string replacement can weld the characters on either side into a phrase nobody wrote (e.g. "...try " + "again..." → a "try again" match); a single space cannot, and it matches `_content_text`'s own `" ".join` block separator.
- **M4 — Non-greedy match requiring the closing tag.** `anchors: row3, row4`. A greedy pattern would swallow everything between the first opener and the last closer across a mixed turn; requiring the closer means a truncated, unterminated envelope degrades to "left in place" (a false positive that already exists) rather than to "rest of the turn deleted" (a new false negative).
- **M5 — One-clause widening of `render.py`'s module docstring and its `### render.py` section in the architecture doc.** `anchors: row7`. Both currently say "display-formatting helpers," which already somewhat mis-describes `_content_text` and would mis-describe this helper too; the doc is the only thing telling a future contributor where leaf text logic belongs, so leaving it stale would make the helper read as misplaced.

## Critical files

**One `code-writer` dispatch.** The change does not partition into independently specifiable file sets — the tests need the helper's final name, and the `CHANGELOG.md` entry needs the numbers Verification step 5 produces — so splitting would force the same shared background into two prompts. Dispatch once, with `.venv/bin/python3 claude/.claude/scripts/select-tests.py` as the verification command.

**Modify:**

- `claude/.claude/scripts/transcript_analysis/render.py` — add `_TASK_NOTIFICATION_RE` and `_strip_task_notifications`; widen the module docstring's opening clause to cover text normalization, not only display formatting. *Reuse:* place the constant-then-function pair beside `_CONTROL_CHAR_RE` / `_sanitize_table_cell`, which is the module's existing shape for exactly this; `re` is already imported, so no new import.
- `claude/.claude/scripts/transcript-analysis.py` — add `_strip_task_notifications` to the `from transcript_analysis.render import (…)` block (:95-108), positioned after `_sanitize_table_cell`; three one-line call-site edits at `cmd_struggle` (~:397), `_classify_prompt` (~:472), `_friction_struggle_turn_events` (~:8977). *Reuse:* `_content_text` stays the extraction step at both flat sites — the strip composes onto it, it does not replace it. No signature changes; `STRUGGLE_PHRASES` (:153) is untouched. Add one sentence to `_classify_prompt`'s docstring noting that phrase-matching runs on notification-stripped text — a reader of this function alone otherwise has no way to know why a `STRUGGLE_PHRASES` entry inside a forwarded envelope doesn't register.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — four test groups (staff-sdet-reviewed edge-case list below; each case is a *required* addition, not illustrative):
  - `TestHelpers` (~:339) — unit tests on `_mod._strip_task_notifications`:
    - multi-line envelope removed; two envelopes in one string both removed; envelope-free string returned unchanged.
    - envelope with surrounding real text leaves that text intact, built specifically at the word-adjacent boundary from the Approach section's own example (`"...try "` immediately before the envelope, `"again..."` immediately after) — named for the regression it pins (e.g. `test_strip_does_not_weld_words_across_envelope_boundary`), so a future edit back to `.sub("", text)` (empty-string substitution) fails this test rather than passing silently.
    - unterminated opener (no closing tag) left in place.
    - **nested/self-quoting case:** a `<summary>` field that itself contains the literal string `<task-notification>` (plausible in this repo, which analyzes its own transcript format) — pin the accepted degraded behavior (non-greedy match stops at the first `</task-notification>`, leaving a dangling closing tag in the remainder) rather than leaving it unverified.
    - **case-sensitivity:** an upper-cased `<TASK-NOTIFICATION>...</TASK-NOTIFICATION>` string is returned unchanged — pins ledger row 10's deliberate `re.IGNORECASE`-declined choice against a future "fix" that silently reintroduces the over-strip risk that row rejected.
  - `TestCmdStruggle` (~:10044) — a `_user_msg("<task-notification>...<summary>...still failing...</summary>...</task-notification>", branch="feat")` after an `_asst(...)` produces no row (assert `"feat" not in out`, matching `test_stale_control_phrase_does_not_inflate_count`'s shape); a companion **mixed-turn** case where a struggle phrase sits *outside* the envelope in the same turn still counts 1 via `_table_cols`.
  - `TestCmdUserInput` (~:10167) — a notification prompt after a real initial prompt is not `EXPLICIT_CORRECTION`, *and* its full envelope text still appears inside the rendered `~~~text` block (pins the display-preservation constraint). Add the same **mixed-turn** case as `TestCmdStruggle` (real correction phrase + envelope in one turn, still classifies `EXPLICIT_CORRECTION` on the outside phrase) — each of the three structural-sibling sites gets its own copy of this scenario rather than asserting it once and assuming it generalizes, per this repo's own structural-siblings convention. Keep the envelope-bearing fixture's `<summary>` text under the 500-char `--truncate-chars` default so the display-preservation assertion isn't silently defeated by truncation.
  - The `friction-count` struggle test group (~:12496) — `signals["struggle_turns"] == 0` for a notification-only transcript, plus the same **mixed-turn** case (real phrase outside the envelope still counts 1) and an **unterminated-envelope-with-embedded-phrase** case (a struggle phrase inside an envelope missing its closing tag still counts 1, per M4's stated tradeoff) — at least one of the three per-site groups must cover this case end-to-end, not only the isolated `TestHelpers` unit test.
  - *Reuse:* `_write_jsonl` / `_user_msg` / `_asst` (`conftest.py`), `_ui_user`, `_user_input_args`, `_friction_count_args`, `_table_cols`. A task-notification fixture is just `_user_msg("<task-notification>...</task-notification>")` — plain-string content, matching the verified real shape (row 1).
- `docs/transcript-analysis.md` — one sentence after the "Each cell is the count of signal phrases…" line in `## struggle`, stating that text inside a `<task-notification>` envelope is excluded before matching because it is the harness's forwarded summary of a finished background task or subagent, not user input.
- `docs/transcript-analysis-architecture.md` — one clause in the `### render.py` section, per M5.
- `CHANGELOG.md` — one bullet at the top of `[Unreleased]` → `### Changed` (there is no `### Fixed` heading in this file, and the dominant fact here is a behavior change across three subcommands' output plus the `/error-mode-analysis` nudge's trigger rate). Shape: name the three subcommands, state that forwarded `<task-notification>` text is excluded from correction/frustration phrase matching, cite the before/after "Explicit corrections" counts *measured in Verification step 5* — not GH-752's figures, which row 13 flags as un-re-derived — and close with the two honest residuals: notification records still appear in `user-input --corrections-only` as `FOLLOWUP` entries, and a session's already-accumulated friction checkpoint keeps its pre-fix totals until it self-evicts. No migration line; nothing downstream depends on the removed count.

**Create:** none.

## Verification

Run from the worktree root (`.venv` paths are worktree-relative per README.md's Tests section).

1. **Record the pre-fix corpus numbers first, before editing anything.** Scope to this repo's own project directories so no private-project transcript contributes to a figure that ships in a public `CHANGELOG.md`, and filter to the summary line so prompt bodies never reach the terminal:
   - `.venv/bin/python3 claude/.claude/scripts/transcript-analysis.py user-input --projects '*claude-config*' --corrections-only | grep -E 'Fresh prompts|Followups|Explicit corrections|Top struggle phrases'`
   - `.venv/bin/python3 claude/.claude/scripts/transcript-analysis.py struggle --this-repo`

   (`user-input` has no `--this-repo` flag — its only scope control is `--projects GLOB`.)
2. **Test suite:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Expect a full-suite run, not a narrowed one — `CHANGELOG.md` and the plan file are unmatched paths, which the script's selection logic fails open on by design (row 12). That is the sanctioned command per this repo's `CLAUDE.md`; do not substitute `pytest claude/.claude/`.
3. **Lint:** `.venv/bin/ruff check claude/.claude/` — `I` catches a misplaced import in the `render` block and `F401` catches it if a call site is missed and the import goes unused.
4. **Iterate loop while developing:** `.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k "task_notification or Struggle or UserInput or Friction"`. Every pre-existing friction-count cross-path/checkpoint test must pass unmodified — they pin that the full-scan and `--checkpoint` paths agree, which is the regression a same-shaped-but-divergent second fix would trip.
5. **Re-derive the post-fix numbers** with the identical step-1 commands, and write *those* two figures into the `CHANGELOG.md` bullet. The delta in "Explicit corrections" is the fix's measured effect; "Fresh prompts" and "Followups" must be unchanged, which is the check that the fix stayed inside classification and did not touch prompt counting.
6. **Guard against over-stripping.** After the change, `user-input --projects '*claude-config*' --corrections-only` must still report a non-zero "Explicit corrections" count and a non-empty "Top struggle phrases" line. A drop to zero means the regex is eating genuine text, not just envelopes.
7. **Confirm display preservation by hand.** In the post-fix `user-input` output, locate one `FOLLOWUP` entry that is a task notification and confirm its `~~~text` block still contains the complete `<task-notification>...</task-notification>` body. The automated test in `TestCmdUserInput` covers this, but the manual check on real data is what proves the fixture matches production shape.

## Out of scope

- **Redefining what counts as a fresh user prompt.** `_is_fresh_user_prompt_for_narrative` and `_is_fresh_user_prompt` both admit `<task-notification>` records, and excluding them there would be the broader fix. It is not this one. The consequence, stated plainly: notification records keep incrementing `total_fresh_prompts` and `followup_count`, and keep appearing in `user-input --corrections-only` output as `FOLLOWUP` entries, because that filter drops only `INITIAL`. The blast radius is why — `_is_fresh_user_prompt` also drives other prompt-window and compaction-boundary logic elsewhere in the file, so a discriminator change needs its own plan and its own before/after counts.
- **Recalibrating `FRICTION_THRESHOLD`.** The threshold is 12, grounded on a p99 ≈ 11.47 composite measured on a corpus whose `struggle_turns` were inflated by exactly the bug this plan fixes. Lowering struggle counts shifts that distribution down, so the `/error-mode-analysis` nudge will fire on strictly fewer sessions than its calibration intended. Worth a follow-up ticket with a fresh per-signal distribution; not something to guess at inside this change.
- **Migrating in-flight friction checkpoints.** The checkpoint file stores running totals rather than re-derivable per-record counts, so a session already underway keeps whatever it accumulated under the old rule and scores only newly-appended bytes under the new one. Files self-evict at 30 days. Shipping an invalidation sweep would cost more than the drift is worth.
- **Handling other envelope tags.** `system-reminder`, `bash-stdout`, and `local-command-stdout` appeared in the same corpus scan; none forwards a subagent's own prose the way `<task-notification>`'s `<summary>` does, and there is no measured defect behind them. Note the row-2 caveat: the scan sampled ~60 records from one project's transcripts, so a second envelope shape surfacing later is a plausible follow-up, not a closed question.
- **Consolidating the three `STRUGGLE_PHRASES` scan loops into one matcher.** Weighed as an alternative shape — it would make the exclusion structurally impossible to forget at a future fourth site. Set aside because the two flat sites want a boolean (`any(...)`) and `_classify_prompt` wants the first matching phrase plus an `is_initial` early return, so unifying them widens the diff across behaviorally distinct shapes without fixing the reported bug. If a fourth site ever appears, revisit then.
- **Documenting `user-input` and `friction-count` in `docs/transcript-analysis.md`.** Neither has a section there today; `## struggle` gets the one-line addition because it is the only touched subcommand with an existing doc surface describing phrase matching. Writing two new subcommand sections is its own change.
- **Case-insensitive envelope matching.** Declined per row 10 — it trades a hypothetical miss for a real over-strip of user-typed text.
