# Bound the code-review fix loop: ADDRESS rows record their site, and a repeat noise verdict stops for the engineer

## Context

Goal: make the existing "third finding at a site two earlier fixes rewrote" stop able to fire, and restore a human stop when the round-cap consult returns "foundation is fine, the loop is the noise" a second time on one branch.

Ask: "Agreed about #1 and #2." and "yes let's do #1 and #2 together as one PR and do a plan-it session." (engineer's words, as recorded in the handoff that started this session)

Why now: on a long `/code-review` loop for a merged branch (GH-1445, about 26 rounds), every ADDRESS ledger row carried `"source":"n/a"`. The site stop in `code-review/SKILL.md` § "Finding disposition" (the `code-review-contradiction-route` region) matches a finding against a site by the ledger's `--source` field or the fix commit's diff hunk. ADDRESS rows record no site, so the stop never fired. The noise verdicts from the round-cap consult also turned into blanket DEFERs, which the `code-review-round-cap-consult-verdict` region says they must not be. The retired `/ready-for-review` Cap had a verdict-independent human stop that nothing replaced.

Change 1: require `--source` on ADDRESS rows in the same range form DEFER and SETTLED use. Enforce it in `review-ledger.sh append` with a test. Say in the contradiction route that the "two earlier rounds' fixes already rewrote" check is a Grep of the branch's ledger or history for that path, not recall.

Change 2: inside the `code-review-round-cap-consult-verdict` region, make a second "foundation is fine, the loop is the noise" verdict on one branch a blocking stop-and-ask. The engineer chooses among shipping with the live DEFER and SETTLED rows, re-planning, or continuing review. Update the region's pin. Add a dated amendment to `docs/design-decisions/round-cap-consult-rearm.md`.

Do not add: a hard round cap that auto-stops or auto-ships, a change to the consult interval, a "consult before round 3" instruction, or a hook that computes site overlap.

Handoff line numbers (`code-review/SKILL.md` 327, 385, 446) were re-checked this session and match.

The amendment's anchor differs from the handoff's line 29 of `round-cap-consult-rearm.md`: the interval's revisit conditions sit at lines 28-30.

The Cap's human stop is described in `ready-for-review-fix-loop-convergence.md` at lines 82, 89 and 98-99.

`code-review/SKILL.md` is 536 lines against a 550-line cap.

## Approach

Every ADDRESS ledger row now carries a `--source`, a reopen's included. `review-ledger.sh append` normalizes it as it normalizes a DEFER or SETTLED source, but never hashes it. The contradiction route then counts a site's earlier rewrites with a Grep of the branch's ledger rows, keyed on the path. The ledger also gains a `CONSULT` disposition. Whenever the round-cap gate fires, the session logs the consult's verdict as a `CONSULT` row whose `--rationale` is `re-plan`, `noise` or `one-defect`. In a round with a finding, a noise verdict becomes a blocking stop-and-ask once the ledger holds two or more noise rows, its own included, and the engineer ships, re-plans or continues.

A `CONSULT` row is a durable verdict record. `docs/design-decisions/round-cap-consult-rearm.md` lines 90-94 and 133 say the design keeps none. This plan reverses that part of the decision, because a second-trip rule has no other way to see the earlier verdict across a compaction or handoff (rows 18, 20). The engineer chose the ledger row as that record (row 40).

The whole change is one `code-writer` dispatch and one commit. The script, its usage text, `code-review/SKILL.md`, `docs/scripts.md` and two decision records all state one contract, so any split leaves a reviewed state in which one of them states the old rule (M9).

**Change 1: ADDRESS rows record their site.**

- `_review-ledger-lib.sh:189-200`: the `--source` requirement covers ADDRESS as well as DEFER and SETTLED. The non-DEFER branch prints "--source <repo-relative path>[:<start>[-<end>]] is required for --disposition <disposition>", so the ADDRESS rejection names `--disposition ADDRESS` and the SETTLED message is unchanged (row 55).
- `review-ledger.sh:499-509`: normalization runs for ADDRESS, DEFER and SETTLED, named explicitly so a CONSULT row's `n/a` source is never read as a path. The location-parts split, the carry-forward range check and the `site_hash` stay DEFER- and SETTLED-only. The comment above the block becomes two sentences: every finding's source is normalized to a repo-relative path, and only a DEFER or SETTLED range-form source gets a site hash, because only a decision is matched by a carry.
- `review-ledger.sh:62-69` (usage): "ADDRESS, DEFER and SETTLED require --source as ...". The hashing sentence is scoped to "A DEFER or SETTLED range", and a new sentence follows it: "An ADDRESS source is never hashed, so its file need not exist."
- The reopen command. A reopen is an ADDRESS row whose fix rewrites its decision's site, so it carries that decision's `--source` and counts toward the two-rewrites stop like any ADDRESS row (row 49). Its four spellings (row 48) become:
  - `_review-ledger-lib.sh:590`: "ADDRESS --ref %s --relog with the decision's --source reopens that decision and retires its carries from the PR block too".
  - `_review-ledger-lib.sh:757`: "reopen %s by logging ADDRESS --ref %s --relog with the decision's --source, which retires that decision and its carries from the PR block."
  - `code-review/SKILL.md:391`, inside the contradiction-route region: "a reopen is logged `ADDRESS --ref <id> --relog` with the decision's `--source`, which retires the decision and its carries from the PR block."
  - `docs/scripts.md:171`: "To reopen a decision, log `ADDRESS --ref <id> --relog` with the decision's `--source`, taking the id from the Id column of `review-ledger.sh render` or of the PR block."
- `code-review/SKILL.md:446`: the template's `[--source '<file:line>']` loses its brackets. The two source sentences become: "ADDRESS, DEFER and SETTLED require `--source`: cite the file the finding concerns (the nearest in-repo file when it names none). A DEFER or SETTLED finding with no in-repo file takes the blocking stop, and an ADDRESS one cites the file its fix edits. An ADDRESS or DEFER `--source` ranges the whole block, as for a keep, except that an ADDRESS whose fix creates a file names that path alone."
- `code-review/SKILL.md:385`, after "unless it carries as below.": "Count those rewrites from the ledger, never from recall: write the branch's rows with `~/.claude/scripts/review-ledger.sh show --out agent-reviews/review-history-<suffix>.md`, Grep that file for the finding's path, and count the earlier rounds holding an ADDRESS row on that path whose fix edited the site. The path is the key, because a row's range gives its own round's lines and later fixes shift them. `unchanged` writes no file and counts zero, and a non-zero exit makes the match uncertain."

**Change 2: the ledger records each round-cap verdict, and a second noise verdict stops for the engineer.**

The ledger side:

- `review-ledger.sh:442-456`: a `CONSULT` arm beside CLEAN's, which exits 2 when `_review_ledger_validate_consult` rejects. The `*)` message at `:453` and the synopsis at `:47` add CONSULT.
- `_review-ledger-lib.sh` gains the verdict check, modeled on the DEFER criteria (row 51):
  - `_REVIEW_LEDGER_CONSULT_VERDICTS=(re-plan noise one-defect)` sits beside `_REVIEW_LEDGER_DEFER_CRITERIA` (`:14-20`), under a one-line comment naming the round-cap verdicts in `code-review/SKILL.md`.
  - `_review_ledger_validate_consult FINDING RATIONALE SOURCE` rejects a present finding, a source other than `n/a`, and a rationale outside the array, each by flag name. The rationale message ends "Pass one of: re-plan, noise, one-defect", built from the array as `_review_ledger_defer_hint` (`:50-54`) builds its list.
- `_review-ledger-lib.sh`: CONSULT joins CLEAN in three rejections, and each of their messages names the disposition it got:
  - the `--decided-by` arm (`:112`), beside ADDRESS and CLEAN;
  - the `--ref` rejection (`:173-176`);
  - the no-lanes arm of `_review_ledger_validate_lanes` (`:219-229`).
- `_review-ledger-lib.sh` wording:
  - the unknown-disposition message (`:129`) reads "--disposition must be ADDRESS, DEFER, SETTLED, CLEAN, or CONSULT", as `:453` does;
  - the lane hint (`:72`) reads "CLEAN and CONSULT take neither --reviewer nor --relog".
- `_review-ledger-lib.sh:897`: the trend's input drops CONSULT rows, so a round holding only its verdict is absent from the series instead of reading `clean` (row 35).
- Usage text:
  - `:57-59` gains "CONSULT records the round-cap consult's verdict in --rationale, one of: re-plan, noise, one-defect. --finding, --source, --reviewer and --relog must be omitted."
  - `:85` reads "CLEAN and CONSULT take no --ref."
  - `:148` reads "one for each round that has a row other than a CONSULT row".
  - `:107-108` takes the lane rule below.
- The lane rule, stated at `review-ledger.sh:107-108`, `code-review/SKILL.md:446` and `docs/scripts.md:152` (row 41), becomes "CLEAN and CONSULT take no lanes; ADDRESS, DEFER and SETTLED take at least one lane (`--reviewer`) or `--relog`, never both." The usage text keeps its existing unbackticked form.
- `transcript_analysis/author_outcome.py`: `_round_blocks` (`:441-460`) skips a CONSULT row, compared through a new `_DISPOSITION_CONSULT` beside `:82-83`. The filter drops the row from `keyed_entries` before the `groupby`, so an ADDRESS, CONSULT, ADDRESS run in one round stays one block. Its docstring says a CONSULT row records a verdict, not a review outcome, so it belongs to no block. A round that logged only its verdict then maps as it does today and is never classed PASS (row 36).
- No `schema_version` bump (row 38).

The prose side. The first two edits sit inside the `code-review-round-cap-consult-verdict` region:

- `:330`, replacing its last sentence: "When the gate fired this round, report the verdict as an `architect-consult: <verdict>` tag on the *Spawn decisions:* line, where `<verdict>` is `re-plan`, `noise` or `one-defect`. Once the round acts on a verdict, and before retrying the denied spawn, log it with `~/.claude/scripts/review-ledger.sh append code-review --disposition CONSULT --rationale '<verdict>' --round <N>`, where `<N>` is the round number Step 0.1 tracks, the last `/code-review` round's at a `/ready-for-review` step-4 denial. An append that still fails after one retry is a blocking stop relaying the script's reason."
- `:327`, after "not a new criterion.": "When this round has a finding and the ledger holds two or more `noise` `CONSULT` rows in any rounds, this verdict's own included, the verdict is also a blocking stop-and-ask. Grep a fresh `show --out` copy, written and read as the contradiction route below does, for `"disposition":"CONSULT","rationale":"noise"`, and treat an uncertain count as two. Ask once this round's findings are tagged, before any is logged or fixed, and let the engineer pick one of:" Three nested items follow it:
  - "*Ship with the live DEFER and SETTLED rows.* Nothing is fixed. Give the contradiction route's notice, apply the enforcement-invariant rule to each finding first, then log each finding as that route logs a human keep."
  - "*Re-plan*, as the first verdict does."
  - "*Continue review*, dispositioning this round as this verdict does."
- `:446`, outside the region: the lane rule above replaces "CLEAN takes no lanes; every other disposition takes at least one lane (`--reviewer`) or `--relog`, never both."

The `:330` command omits `--authoring-agent` and `--authoring-effort`, which the script leaves optional (`review-ledger.sh:119-120`), because the row records a verdict, not a review of an authored diff.

The stop counts rows and never compares round numbers. A round that continues after its verdict holds its CONSULT row before its disposition rows. A compaction in that span makes Step 0.1 file the rest of the round one number higher (row 45), so a test for "a noise row from an earlier round" would read this verdict's own row as an earlier one. That renumbering is accepted. A compaction inside today's disposition batch already renumbers the same way, and the rewrite count still sees the round's ADDRESS rows as one round. Other properties of the count:

- A same-session repeat of one verdict at one round number is one row, by the append's dedup key (row 46). That repeat is not expected (row 47), so the stop does not depend on seeing it.
- A third and later noise verdict also stops. Nothing resets the count on a clean round (row 23).
- The stop sees only verdicts logged to the branch's own ledger, so it can come late (rows 31, 32). It comes early only when `show` fails.
- A failed verdict append stops at once, because a lost row would let the next noise verdict pass unasked (row 54).

Two readers that test only whether a session has rows now see a CONSULT-only session as having one. `_ledger_possibly_swept` no longer reads it as possibly swept, and the compliance log records `ledger=present` (rows 35, 50). Both are accepted, because the row exists.

A revert needs no ledger cleanup. On a branch that logged a CONSULT row, the reverted trend would read a verdict-only round as `clean` and the reverted `author_outcome` would class it PASS, until the ledger sweep, whose floor is 30 days, removes that ledger (row 57). The PR body carries this sentence.

**Settled questions.**

1. *Range form for ADDRESS.* ADDRESS uses the DEFER and SETTLED grammar with no hash, so working-tree drift since the review cannot reject the row. Its `--source` cites one of:
   - the lines the finding cites in the reviewed content;
   - the file the fix edits, when the finding names no in-repo file, so an ADDRESS row never takes a stop (row 9);
   - the created path alone, when the fix creates a file;
   - the decision's `--source`, on a reopen (row 49).

   Check: rows 5-7.
2. *Path-only or range, and `site_hash`.* The script still accepts path-only, as it does for DEFER and SETTLED. The prose asks for the block's range. ADDRESS gets no `site_hash`, because nothing would read it (row 6). The two-rewrites count keys on the path, because a range gives its own round's lines.
3. *SKILL.md lines.* The file has 536 lines (row 27). The edits at `:327`, `:330`, `:385`, `:391` and `:446` stay inside their existing lines, for +0. The three nested option items add 3, so 536 + 3 = 539, 11 under the 550 cap. `:324` is not edited, because the stop adds no `agent-reviews/` file. Nothing is deleted and no list is flattened.
4. *Tests and callers.* Listed per file in Critical files, from rows 11-17, 34, 36, 39, 41, 42, 46, 48 and 50-52.
5. *Dispatch split.* One `code-writer` dispatch and one commit (M9).
6. *Review gates.* Listed in Verification.
7. *Verdict record.* A `CONSULT` disposition, shaped and justified in M5. Check: rows 19, 35-40.
8. *Noise count.* Rows in any rounds, this verdict's own included, never a round-number comparison (M5). Check: rows 44-47.

**Docs.**

- `round-cap-consult-rearm.md`, a new paragraph directly after line 5's note: "**Partially superseded (2026-11-11):** a second "foundation is fine, the review loop is the noise" verdict on one branch, in a round with a finding, is now a blocking stop-and-ask, where the engineer ships with the live DEFER and SETTLED rows, re-plans or continues review (`code-review/SKILL.md` § "Round-cap architect consult"). To see an earlier verdict across a compaction or handoff, the session logs every gate-fired verdict as a `CONSULT` row in the branch's review ledger (`review-ledger.sh --help`). That row is the durable verdict record that "No separate human ladder" says this design does not keep, so that bullet and the Out-of-scope line on recording verdicts durably no longer hold. Verdicts also no longer live only in transcripts, as "What would change the interval" and "What a run loses" say, although no tool counts the rows yet. The `--trend` series skips a `CONSULT` row, so "What a consult receives" still holds. The gate, its interval and its counter are unchanged."
- `ready-for-review-fix-loop-convergence.md`, a new first note directly under the line-3 provenance line: "**Superseded in part (2026-11-11):** an ADDRESS ledger row now also requires `--source`, and the two-rewrites site stop counts earlier fixes from those rows, not from session context. "The contradiction route" and the site-stop paragraph under "How often the new stops interrupt a run" therefore no longer rest on session context, and `--source` no longer "stays optional for every other disposition"."
- `docs/scripts.md`, in the `review-ledger.sh` entry:
  - `:156`: the hash and the symlink-out rejection are scoped to a range-form DEFER or SETTLED source. The control-character rejection is stated for every source.
  - `:152` takes the lane rule, and `:153` reads "It is `[]` on CLEAN and CONSULT and on a copy of a row that holds no lanes".
  - `:171` takes the reopen wording in Change 1.
  - A new bullet after **Flags.** (`:148`): "**Consult verdicts.** A `CONSULT` row records a round-cap consult's verdict for `/code-review`'s second-noise-verdict stop. It is not a decision, so `render` never publishes it, `--trend` skips it and the dashboard does not count it. Its round counts toward `show`'s `max_round`."
- `docs/transcript-analysis.md:1628`, after "belongs to no block.": "A `CONSULT` row, which records a round-cap consult's verdict rather than a review outcome, belongs to no block either."
- `CHANGELOG.md`, under `[Unreleased]` → `### Changed`, following the lane entry's shape (`CHANGELOG.md:45-50`):
  - **`/code-review` ADDRESS ledger rows now record their site, and a second "foundation is fine, the loop is the noise" round-cap verdict on one branch stops for the engineer.**
  - `review-ledger.sh append` now rejects an ADDRESS row without `--source`. The source takes the DEFER and SETTLED grammar and is stored repo-relative. It gets no `site_hash`, and its file need not exist. A reopen, `ADDRESS --ref <id> --relog`, passes its decision's `--source`.
  - The contradiction route's two-rewrites stop counts earlier fixes from those rows. ADDRESS rows logged before this change carry `n/a` and count toward no site.
  - `review-ledger.sh append` accepts a new `CONSULT` disposition, whose `--rationale` is the round-cap consult's verdict: `re-plan`, `noise` or `one-defect`. `/code-review` logs one whenever the gate fires. A `CONSULT` row never renders, the `--trend` series skips it, and `transcript-analysis.py author-outcome` leaves it out of its round blocks.
  - In a round with a finding, a noise verdict is a blocking stop-and-ask once the branch's ledger holds two noise rows, its own included: ship with the live DEFER and SETTLED rows, re-plan, or continue review.
  - The script change is live on `git pull`, and the noise stop engages only after the pull and a session restart. A session that loaded `/code-review` before the pull logs no `CONSULT` row, so the stop never sees its verdicts, and its ADDRESS appends are rejected until it adds `--source`. A branch in flight counts from its first verdict logged after the restart.
  - Any caller of `review-ledger.sh append` outside the stowed `/code-review` skill must now pass `--source` on an ADDRESS row.

### Assumption ledger

**Root:** ADDRESS ledger rows record no site, because `--source` defaults to `n/a` on them. So the contradiction route's "two earlier rounds' fixes already rewrote" stop has nothing durable to match and falls back on recall. No rule sends a repeated round-cap noise verdict to the engineer either, so a grinding loop reaches the human only when a consult's own judgment sends it there.

**Givens:**

- **G1.** The round-cap gate's trigger, interval and counter stay as `round-cap-consult-rearm.md` § "The interval and the reset" sets them. Reason: changing them is that decision's call, and no instrument evaluates its revisit conditions (`round-cap-consult-rearm.md:28-30`). `[verified: round-cap-consult-rearm.md:21-31]`
- **G2.** No hook can see a consult's verdict: the gate keeps a presence-only latch and a state count. Reason: the hook design belongs to the rearm decision, and making a hook read verdicts would reopen it. `[verified: round-cap-consult-rearm.md:94]`
- **G3.** The session running `/code-review` is cooperative, so its ledger rows and `agent-reviews/` records are self-attested, as the disposition record already is. Reason: the repo's hook threat model sets that tier (`CLAUDE.md` "Hook threat model"). `[verified: ready-for-review-fix-loop-convergence.md:44-46]`

**Rows:**

1. `[engineer-verified: "yes let's do #1 and #2 together as one PR and do a plan-it session."]` Both changes ship in one PR, planned here. The quote covers bundling and planning, not any detail below.
2. `[engineer-verified: "Agreed about #1 and #2."]` The engineer agreed to the two changes the handoff numbered #1 and #2.
3. `[unverified]` The Context's Change 1 and Change 2 paragraphs, its "Do not add" list and its out-of-scope items are the prior session's relay of an earlier architect report, not engineer words. Where this plan departs from them, the departure has its own row (rows 12, 22, 40).
4. `[verified: review-ledger.sh:360, :442-456; _review-ledger-lib.sh:189-200]` `--source` defaults to `n/a`, and today only DEFER and SETTLED reject a missing one.
5. `[verified: review-ledger.sh:499-509, :523-528; _review-ledger-lib.sh:371-401]` Today only DEFER and SETTLED sources are normalized. A range-form one is hashed from the working tree, which rejects a missing file and a range past the end of the file.
6. `[verified: _review-ledger-lib.sh:618-655, :713-733; grep of site_hash under claude/.claude/scripts]` `site_hash` is read only by the `--ref` and carry checks, which accept only a live DEFER or SETTLED decision. An ADDRESS row's hash would have no reader, and its fix rewrites the hashed text anyway.
7. `[verified: _review-ledger-lib.sh:257-325]` Normalization checks the `<path>[:<start>[-<end>]]` grammar and makes the path repo-relative without opening the file. It rejects a control character, a `..`, `.` or empty segment, and an absolute path outside the repository.
8. `[verified: code-review/SKILL.md:446]` The prose already asks a DEFER source to range the whole block, and sends a DEFER or SETTLED finding with no in-repo file to a blocking stop.
9. `[unverified]` Every `/code-review` ADDRESS fix edits or creates a repository file, so "the file its fix edits" always exists. A reopen's fix edits its decision's file (row 49). A fix that edits no repository file, such as a PR-body-only fix, is not expected from `/code-review`, and such a row would fall back to the nearest in-repo file.
10. `[verified: announce-branch-review-history.sh:3-5, :31-33, :50; _review-ledger-lib.sh:957, :996; code-review/SKILL.md:313]` The hook writes `agent-reviews/review-history-<slug>.md` only when a `plan-architect` dispatch starts, so it can be stale or absent at disposition time. `show --out` accepts a `review-history-<suffix>.md` target. Each round's `findings-path-suffix.sh` run has already made `agent-reviews/` ignored.
11. `[verified: claude-skills/skills/tests/test_skills.py:7077-7192]` The contradiction-route region is pinned whole by exact equality, so the `:385` edit needs `_PINNED_CONTRADICTION_ROUTE_CLAUSE` updated.
12. `[verified: test_skills.py:2617-2625, :7419-7434, :7794-7803]` The round-cap region has no whole-text pin. Its tests check the anchor pair, that the first bullet is the re-plan verdict, and the exclusion phrase. So the Context's "Update the region's pin" is carried out as a new clause test in that style. The exclusion phrase stays, because the stop adds no `agent-reviews/` file.
13. `[verified: test_skills.py:8058-8062]` A prose control pins the template substring `[--source '<file:line>']`.
14. `[verified: grep of "review-ledger\.sh append" and "append", "code-review" across the repo]` `code-review/SKILL.md` is the only production caller. Real-script ADDRESS appends with no `--source` sit at these sites:
    - `test_review_ledger_script.py`, through `_append_args`'s default (`:181-231`);
    - `test_author_outcome.py:1195-1201` and `:1229-1231`;
    - `test_require_code_review.py:890-895`;
    - `test_announce_branch_review_history.py:114-119`.

    `test_session_marker_dashboard.py` and the other `test_require_code_review.py` ledger rows (`:825-960`, apart from `:890-895`) are written as JSON directly and are unaffected.
15. `[verified: test_review_ledger_script.py:164-178, :2484, :2990-3002, :3006, :3017, :3099-3118, :3902, :1481-1489]` Six tests put U+0001 in an ADDRESS source, and normalization rejects a control character in a path:
    - four (`:2484`, `:3006`, `:3017`, `:3902`) size a row to the byte bound through `_fields_for_row_of_bytes`;
    - two (`:2995`, `:3109`) fill every free-text field with control characters to show that the byte bound, not the character caps, rejects the row.

    A printable 200-character source plus the capped finding and rationale cannot reach 4,095 bytes. All six therefore move the control-character mass to an engineer SETTLED's quote, as `:1481` already does, and a separate test covers an ADDRESS source holding a control character.
16. `[verified: claude/.claude/scripts/tests/test_review_ledger_lib.py:82-119, :136-202; _review-ledger-lib.sh:189]` `_validate` defaults `source` to `n/a`. The two ADDRESS accept cases at `:103-104` need a source. The ADDRESS reject cases still fail on their own flag first, because the source check runs last.
17. `[verified: test_review_ledger_script.py:700-718]` The v2-row dedup test appends `_append_args()`. Its fixture row's `source` must equal the new ADDRESS default, so the test still isolates `session_id`.
18. `[verified: grep of "architect-consult:" across the repo]` The verdict tag exists only on the round report's Spawn decisions line (`code-review/SKILL.md:330`) and in transcripts (`round-cap-consult-rearm.md:103`). No file or ledger row records it.
19. `[verified: review-ledger.sh:442-456; _review-ledger-lib.sh:111-131, :173-176, :219-229; test_review_ledger_lib.py:231; test_review_ledger_script.py:655]` The script's disposition case and the lib's flag check each reject a disposition they do not name, and two tests pin that message. CLEAN's rules sit in three places:
    - the script's case rejects a finding, a rationale and a source;
    - the flag check rejects `--decided-by` and `--ref`;
    - the lane check rejects `--reviewer` and `--relog`.

    The lane check tests for CLEAN alone, so any other disposition needs a lane unless it is named there.
20. `[verified: round-cap-consult-rearm.md:90-94, :133]` The rearm decision holds three things. A second-trip rule needs the previous verdict. Carrying it across a handoff needs a durable verdict record. The design keeps no such record.
21. `[verified: round-cap-consult-rearm.md:84; test_skills.py:7058-7063]` A staged review that a verdict ended writes no `code-review` marker. The completion marker counts a DEFER or a SETTLED keep as resolved.
22. `[engineer-verified: "Accept all three (Recommended)"]` The quote covers these three choices as the question stated them:
    - the stop fires only when the round has a finding, because with none, ship and continue are the same and the consult has just called the foundation fine;
    - *ship* logs each finding as an engineer keep, so the PR ships with them as live SETTLED rows;
    - nothing resets the count on a clean round.
23. `[verified: ready-for-review-fix-loop-convergence.md:82, :105; round-cap-consult-rearm.md:89]` The retired Cap's human stop reset on a clean pass. The rearm decision rejected a clean-pass reset, because alternating dirty and clean passes escaped through it.
24. `[verified: round3-consult-verdict-routing.md:26-30; code-review/SKILL.md:417]` A blanket "stop reviewing" verdict was rejected as a DEFER criterion granted by a subagent's prose. An invariant-class finding is never DEFER-eligible, and the human's keep of one carries `--enforcement-invariant`. *Ship*'s keeps are the engineer's own decisions, taken after the invariant rule runs, so neither bar is crossed.
25. `[verified: ready-for-review/SKILL.md:19-24]` `/ready-for-review` retries a denied spawn unless the verdict is re-plan. A stop placed at disposition time leaves that sentence true.
26. `[verified: code-review/SKILL.md:433, :391]` Precedent for this stop's notice and logging: the disposition challenge gives "the contradiction route's notice" and logs a human-decided row "as the contradiction route logs a human keep". The contradiction route applies the enforcement-invariant rule to a finding "first".
27. `[verified: check-skill-length.sh:130-134; code-review/SKILL.md read to its last line, 536]` The file's cap is 550 lines.
28. `[verified: .claude/rules/design-decisions.md "Supersession"; ready-for-review-fix-loop-convergence.md:5, :7, :58, :98-99; round-cap-consult-rearm.md:5]` An overturned decision gets a dated note under its provenance line. Change 1 overturns `ready-for-review-fix-loop-convergence.md`'s "session context carries the earlier rounds' fixes" (`:58`, `:98`) and "the field stays optional for every other disposition" (`:99`).
29. `[verified: docs/scripts.md:155-156]` `:155` already says only a range-form DEFER or SETTLED row stores a `site_hash`. `:156` says "A range-form source gets a `site_hash`", which Change 1 makes false for ADDRESS.
30. `[verified: CHANGELOG.md:45-50]` Precedent: the lane requirement's entry told sessions to restart and told outside callers to pass the new flag.
31. `[verified: review-ledger.sh:55-56; round-cap-consult-rearm.md:137]` The ledger is branch-scoped, and per-session on a detached HEAD or the default branch. A branch checked out at two worktree paths keys two ledgers. In either case the stop sees only the verdicts logged to its own ledger.
32. `[unverified]` Legacy ADDRESS rows carry `n/a`, and no verdict given before the upgrade has a CONSULT row. On a branch in flight at upgrade, the rewrite count and the noise count therefore start at the first round logged after the upgrade.
33. `[verified: CLAUDE.md "Working in this repo"]` `claude/` and `claude-skills/` are stowed from the main checkout and go live on `git pull`. This branch's own review rounds therefore run the old script and skill.
34. `[verified: claude/.claude/hooks/tests/test_enforce_marker_script_shape.py:2416, :2559-2562, :2689-2693]` That file's ADDRESS commands lack `--rationale` and `--reviewer`, which `append` already rejects. Those tests check the hook's decision, so none depends on a successful append.
35. `[verified: _review-ledger-lib.sh:477-478, :812, :891-899; session-marker-dashboard.sh:137-146; require-code-review.sh:168-197; grep of "disposition" across claude/.claude/hooks/*.sh; grep of the ledger directory name under evals/]` Readers by disposition:
    - `render` publishes only live DEFER and SETTLED decisions, and a `--ref` resolves only to one.
    - The trend counts ADDRESS, DEFER and SETTLED rows but lists every round that holds any row, so a round holding only a CONSULT row would read `clean`.
    - The dashboard counts the three by name and prints nothing when none exists.
    - No gate matches on a disposition. The append gate keys on the op word and `--engineer-quote`, and the compliance log on any row of the session.
    - Nothing under `evals/` reads the ledger.
36. `[verified: transcript_analysis/author_outcome.py:441-460, :501-511, :574-579]` `author_outcome` groups a session's round-keyed rows into blocks and maps them by rank onto round-opens. A round whose block holds no ADDRESS row is PASS, so a CONSULT row would make a re-plan round, which reviewed nothing, a PASS. Kept out of the blocks, that round has no block, as today, so a session whose other rounds have blocks reads as a round-number mismatch, also as today.
37. `[verified: code-review/SKILL.md:25, :326, :446-448]` After a compaction, Step 0.1 takes the round number from one past `show`'s `max_round`. A round the re-plan verdict ends reaches no disposition and is not clean, so today it logs no row and a compacted session can reuse its number. A CONSULT row at `--round <N>` advances `max_round` past it.
38. `[verified: review-ledger.sh:38-40; docs/scripts.md:153]` `schema_version` is unread and marks a row shape, and schema 5 added the `reviewers` key. A CONSULT row has the same keys as a CLEAN row, so the version stays 5.
39. `[verified: review-ledger.sh:572-573, :759; _review-ledger-lib.sh:454]` `append` builds `disposition` immediately before `rationale`. `show` adds `source_repo_hash` last and prints each row with `tojson`, which keeps key order. A noise CONSULT row therefore prints `"disposition":"CONSULT","rationale":"noise"` as one run of text.
40. `[engineer-verified: "Ledger CONSULT row (Recommended)"]` The engineer chose to record round-cap verdicts as a CONSULT ledger row, over a gitignored per-branch file. The quote is the option's label only. The row's shape and every detail of it are this plan's.
41. `[verified: review-ledger.sh:107-108; code-review/SKILL.md:446; docs/scripts.md:152; _review-ledger-lib.sh:72; test_review_ledger_script.py:1690, :2438; test_review_ledger_lib.py:628, :744; test_skills.py:8082]` The usage text, `code-review/SKILL.md` and `docs/scripts.md` each state "CLEAN takes no lanes; every other disposition takes at least one lane", and the lane hint says "CLEAN takes neither --reviewer nor --relog". Five test strings pin those phrases.
42. `[verified: test_review_ledger_lib.py:93-94; test_review_ledger_script.py:1238-1245]` The lib test's `_CONSULT` fixture and the script test's `_consult_args` helper each build a `SETTLED --decided-by plan-architect` row, the contradiction route's consult keep. The CONSULT-disposition cases and helpers take names built on "round-cap verdict" instead.
43. `[verified: docs/transcript-analysis.md:1628, :1634]` The runbook defines blocks, and says a PASS round's matching rows are "necessarily all `DEFER`/`SETTLED`/`CLEAN`". That stays true once a CONSULT row belongs to no block.
44. `[verified: ready-for-review/SKILL.md:19-20; round-cap-consult-rearm.md:64-65]` A `/ready-for-review` step-4 spawn routes a denial through the round-cap section outside any `/code-review` round. The gate denies it only when step 3 was a cache hit and no recorded state matches.
45. `[verified: code-review/SKILL.md:25, :446; review-ledger.sh:771-776]` After a compaction, Step 0.1 resumes at one past `show`'s `max_round`, the largest `round` over every row. Disposition rows are logged in one batch once dispositions are final. Today only a compaction inside that batch files the rest of a round one number higher. A CONSULT row logged before the spawn retry widens that window to the rest of any round that continues after its verdict.
46. `[verified: review-ledger.sh:115-118, :645-646]` The append's dedup key is every field but `id`, `schema_version` and `event_time`. A CONSULT row varies only in `round`, `rationale` and `session_id`, so one session's repeat of one verdict at one round is an exit-0 no-op, and another session's lands as a second row.
47. `[unverified]` Two gate firings at one round number in one session are not expected. Each consult restarts the gate's three-state count (`round-cap-consult-rearm.md:9`), and `/ready-for-review` never consults again without a new reviewed state (`ready-for-review/SKILL.md:21`). How step-4 spawns count toward the gate's state counter was not traced.
48. `[verified: _review-ledger-lib.sh:590, :757; code-review/SKILL.md:391; docs/scripts.md:171; test_skills.py:7160; test_review_ledger_lib.py:981, :1690, :1752; test_review_ledger_script.py:2344, :2352-2354]` The reopen command `ADDRESS --ref <id> --relog` is spelled at four non-test sites. Each spelling already leaves out `--finding`, `--rationale` and `--round`, so it names only what makes a row a reopen. Its pins sit at three depths:
    - `test_skills.py:7160` holds it inside the pinned contradiction-route clause;
    - `test_review_ledger_lib.py:981` asserts the `:590` phrase through "reopens that decision";
    - `test_review_ledger_lib.py:1690`, `:1752` and `test_review_ledger_script.py:2344` assert it only up to `--relog`.

    The reopen test at `test_review_ledger_script.py:2352-2354` appends through `_append_args`.
49. `[verified: docs/transcript-analysis.md:1648; code-review/SKILL.md:391, :446; _review-ledger-lib.sh:851-852]` A reopen is an ADDRESS row because the engineer now requires the fix, and it records no raise of its own. Its fix therefore rewrites the decision's site. The digest's tables print each decision's Source beside the Id the reopen takes.
50. `[verified: transcript_analysis/author_outcome.py:546, :799]` `_ledger_possibly_swept` reads the unfiltered row list, so a session holding only CONSULT rows no longer reads as possibly swept. Row 35 records that the compliance log marks any row of the session `ledger=present`.
51. `[verified: _review-ledger-lib.sh:14-20, :50-54, :75-81; claude/.claude/scripts/tests/test_review_ledger_lib.py:1-7, :57-74]` The DEFER criteria live in the lib as an array, a predicate and a hint that lists them. The lib tests source the lib in one `bash -c` per case, with no repository, session or ledger, and the file's docstring names it the home of the input matrices.
52. `[verified: test_review_ledger_script.py:1525-1592, :1601-1617]` `TestReviewLedgerEnumParity` reads `code-review/SKILL.md`'s DEFER criteria, drives the script with each one, and compares the script's "one of:" rejection listing and the usage text's listing with the SKILL.md set. The file already reads `code-review/SKILL.md`.
53. `[verified: review-ledger.sh:134-144]` `show --out` deletes its target first. It prints `changed: <path>` on success, prints `unchanged` and writes no file when there is no ledger or no row, and exits 1 or 2 on a failure.
54. `[verified: code-review/SKILL.md:450-451]` A per-finding append that cannot resolve the ledger is retried once, then reported. A DEFER or SETTLED append that still fails is a blocking stop relaying the script's reason.
55. `[verified: _review-ledger-lib.sh:189-200]` The source check picks its message with `if DEFER ... else`, so adding ADDRESS to its `case` alone would print the SETTLED message for an ADDRESS row.
56. `[verified: code-review/SKILL.md:79, :446; ready-for-review-fix-loop-convergence.md:99; claude/.claude/agents/code-writer.md:23]` Every round's base checklist flags unchanged prose that still states a changed name's old contract. `code-review/SKILL.md:446` and the convergence record's `:99` state the old source rule. `code-writer` never stages or commits, so the session commits whatever the dispatch leaves.
57. `[unverified]` The ledger sweep removes a branch's ledger once it is older than `_LEDGER_SWEEP_FLOOR_DAYS`. Only the value was read: 30 days (`hooks/_lib.sh:5534`, `author_outcome.py:131`).
58. `[verified: ready-for-review/SKILL.md:107-111]` Step 4's findings come from `skill-fidelity-reviewer` and are fixed in a new commit through the staged-diff `/code-review`, so step 4 logs no disposition rows of its own.
59. `[engineer-verified: "Proceed anyway"]` The engineer answered the plan-review size question, asked because the `Projected diff:` line states about 470 changed lines against a 400-line threshold. The quote covers shipping at that size as one PR and nothing else.

**Mechanisms:**

**M1. `_review_ledger_validate_flags` requires `--source` on ADDRESS, a reopen's included.** `anchors: root, row4, row48, row49, row55`. The existing prose-only expectation is what left every ADDRESS row at `n/a`, so the rule moves into the script, where DEFER and SETTLED already have it. A reopen passes its decision's `--source` in every spelling of the command (row 48). Two alternatives for the reopen were set aside:
- *Copy the source from the `--ref` decision, as `--relog` copies lanes.* The flag check runs before the `--ref` lookup (`review-ledger.sh:488-515`), so the copy needs an exception in that check and a write after the lookup. The value it would copy sits beside the id the session already takes from the digest (row 49).
- *Exempt a `--relog` ADDRESS from `--source`.* Its fix rewrites the decision's site, so the rewrite count would miss it (row 49).

**M2. `append` normalizes an ADDRESS source but computes no `site_hash`.** `anchors: row5, row6, row7, row15`. Three alternatives were set aside:
- Hash ADDRESS sources too. Nothing reads the hash (row 6), the fix makes it stale, and it rejects a fix that creates a file.
- Store the source raw, with presence as the only check. That is lighter and keeps the six filler tests unchanged. But it leaves the grammar unchecked and the path spelling free (row 7), and the "same range form" change and M4's Grep rest on both.
- Require range form in the script. It rejects a fix that creates a file, which has no lines to cite.

**M3. `:446` prose: an ADDRESS row cites its block, or the file its fix edits or creates.** `anchors: row8, row9, row13`. Reusing DEFER's "nearest in-repo file" and "whole block" rules keeps one citation rule for every finding row. DEFER's blocking stop for a finding with no in-repo file does not carry over, because an ADDRESS row always has a fix and so a file.

**M4. The contradiction route counts rewrites from a fresh `show --out` copy.** `anchors: root, row10, row11, row53`. Two lighter reads fail:
- The hook's `review-history-<slug>.md` can be stale or absent at disposition time (row 10).
- Bare `show` prints every row into the session's context, which grows with the branch.

The `:385` sentence states the read's outcomes once (row 53). `unchanged` counts zero, and a non-zero exit is an uncertain match, which the route's stop list already sends to the engineer. The `:327` stop reads its copy by the same rule and treats an uncertain count as two.

**M5. A `CONSULT` ledger disposition records each gate-fired verdict, and the stop counts its noise rows.** `anchors: root, row18, row19, row20, row31, row35, row36, row37, row38, row39, row40, row44, row45, row46, row47, row51, row58`. The engineer chose a ledger row (row 40). Its smallest form is a new disposition value whose verdict word sits in `--rationale`, checked against the three words by a lib function, as the DEFER criteria are (row 51). It reuses CLEAN's bars on a finding, a source, `--ref`, `--decided-by` and lanes, and it adds no key, so `schema_version` stays (row 38). Alternatives set aside:
- *A `verdict` field.* It adds a key and so a schema bump, for a value the closed `--rationale` check already carries.
- *CLEAN with a rationale.* A verdict round with findings is not clean, and the trend and `author_outcome` would read a verdict-only round as clean.
- *A SETTLED `--decided-by plan-architect` row.* It is a decision, so `render` would publish it in the PR body and a later `--ref` could retire it.
- *Session recall or the Spawn decisions tag.* Neither survives a compaction or handoff (row 18), and Change 1 already rejects recall for the sibling site check.

The readers need two filters, not more. `render`, the dashboard and the gates already ignore a disposition they do not name (row 35). The trend would read a verdict-only round as `clean`, and `author_outcome` would class it PASS (row 36). The row also advances `show`'s `max_round` over a re-plan round, which today logs nothing, so Step 0.1 no longer reuses that round's number after a compaction (row 37). The `:327` Grep relies on `show` printing `disposition` and `rationale` side by side (row 39), and a script test pins that. A step-4 denial in `/ready-for-review` has no open round, so its row takes the last round's number, and the stop reads step 4's findings as that round's (row 44).

The stop counts the `noise` rows in any rounds, this verdict's own included. Two alternatives were set aside:
- *A round-number test*, "a noise row from an earlier round". After a compaction inside a round that continues past its verdict, Step 0.1 resumes one number higher, and the test reads this verdict's own row as an earlier one (row 45).
- *Logging the verdict with the round's disposition rows*, which keeps Step 0.1's numbering. A `/ready-for-review` step-4 verdict has no disposition rows to join (rows 44, 58).

**M6. The stop fires at disposition time and offers three routes.** `anchors: row21, row22, row23, row24, row25, row26, row54`. The verdict itself is logged when it arrives, before the denied spawn's retry, so a round the re-plan verdict ends still records it. A stop at verdict time, before the spawn retry, was set aside. *Ship* could not finish there: a review that did not run writes no marker (row 21), so the commit or push could not go ahead. It would also contradict `/ready-for-review`'s retry sentence (row 25). Disposition time costs this round's spawns when the engineer picks re-plan, and it puts the round's findings in front of the engineer. *Ship* reuses the existing human-keep logging, notice and invariant ordering (row 26), so it adds no new log shape. A verdict append that still fails after one retry is a blocking stop, as a DEFER or SETTLED append that still fails is (row 54), because the count needs every verdict's row.

**M7. Test changes.** `anchors: row11, row12, row13, row14, row15, row16, row17, row34, row36, row39, row41, row42, row46, row48, row51, row52`. A script subprocess test covers each new branch once. The input matrices go to the lib test, per `claude/.claude/rules/shell-script-conventions.md`'s extraction bullet, which is why the verdict check moves into the lib. Those lib cases still start one `bash -c` each, but with no repository, session or ledger, which is that file's stated layer for matrices (row 51). Three further choices:
- A parity test copies `TestReviewLedgerEnumParity` (row 52), so a verdict word renamed in `code-review/SKILL.md`, the script or the usage text fails, as does a Grep literal `show` no longer prints.
- The round-cap clause test names each load-bearing clause as a substring and asserts the option order and *Ship*'s invariant-then-log order by index. A whole-region pin was set aside: the region has none today (row 12), and a whole pin holds these clauses only implicitly.
- Every new behavior test is written and run red before any production edit, and the intended pre-change passes are named in Verification 1.

**M8. Docs.** `anchors: row20, row28, row29, row30, row32, row41, row43, row48`. Each overturned decision gets a dated note under its provenance line. `docs/scripts.md` gets the source scoping fix, the lane rule, the reopen wording and a CONSULT bullet. `docs/transcript-analysis.md` gets one block sentence, and `CHANGELOG.md` gets one breaking-change entry.

**M9. One `code-writer` dispatch and one commit.** `anchors: row56`. `plan-it/SKILL.md` § "Step 5 — Architecture design" splits a phase only into non-overlapping file sets. Two lighter shapes were set aside:
- *Two commits, script first.* That commit's staged review flags `code-review/SKILL.md:446` and the convergence record's `:99` by construction (row 56). No closed-list DEFER criterion covers a fix in a later commit of the same PR, so the loop would ADDRESS it in the first commit and undo the split.
- *Two sequenced dispatches into one commit.* The verdict-word parity test reads `code-review/SKILL.md` text and drives the script, so it belongs to both file sets.

One dispatch reads the contract once, and its self-review sees the whole change.

## Critical files

**One `code-writer` dispatch: the ledger script, its readers and callers, the skill prose, and the records (M9).** Its brief says to write every new and changed test first, then run them against the unchanged production files, as Verification 1 states, before any production edit.

- `claude/.claude/scripts/_review-ledger-lib.sh`:
  - the ADDRESS arm of the `--source` check (`:189-200`), reusing `_review_ledger_reject`, with the non-DEFER message naming its disposition;
  - `_REVIEW_LEDGER_CONSULT_VERDICTS` and `_review_ledger_validate_consult`, modeled on `_REVIEW_LEDGER_DEFER_CRITERIA`, `_review_ledger_is_criterion` and `_review_ledger_defer_hint` (`:14-20`, `:50-54`, `:75-81`);
  - CONSULT in the `--decided-by` arm (`:112`), the unknown-disposition message (`:129`), the `--ref` rejection (`:173-176`), the no-lanes arm (`:219-229`) and the lane hint (`:72`);
  - the reopen advice at `:590` and `:757`;
  - the trend input filter (`:897`).
- `claude/.claude/scripts/review-ledger.sh`:
  - normalization for ADDRESS, DEFER and SETTLED, with the hash kept DEFER- and SETTLED-only (`:499-509`), reusing `_review_ledger_normalize_location` as is;
  - the CONSULT arm calling `_review_ledger_validate_consult`, and the `*)` message (`:442-456`);
  - the usage text at `:47`, `:57-59`, `:62-69`, `:85`, `:107-108` and `:148`.
- `claude/.claude/scripts/transcript_analysis/author_outcome.py`: `_DISPOSITION_CONSULT` and the `_round_blocks` filter and docstring.
- `claude/.claude/scripts/tests/test_review_ledger_lib.py`:
  - The ADDRESS accept cases at `:103-104` gain a source. New accept cases cover an ADDRESS path-only source and a range. New reject cases cover an ADDRESS with no source and with an empty one, and the no-source case asserts `--disposition ADDRESS`.
  - `_review_ledger_validate_flags`: a plain CONSULT row is accepted, and a CONSULT row with `--decided-by` or `--ref` is rejected by flag name. The ids say "round-cap verdict row", never reusing the `_CONSULT` fixture name (row 42).
  - `_review_ledger_validate_consult`: each of `re-plan`, `noise` and `one-defect` is accepted. `Noise`, `noise ` with a trailing space, `replan`, `one_defect` and an empty rationale are each rejected, with stderr naming all three words. A present finding and a source other than `n/a` are each rejected by flag name.
  - `_review_ledger_validate_lanes` accepts a CONSULT row with no lane and rejects one with `--reviewer` or with `--relog`, each message naming CONSULT.
  - `TestTrend` (`:2716`): a round holding only a CONSULT row is absent from the series, and a CONSULT row beside an ADDRESS row leaves that round at 1.
  - `TestRenderBlock` (`:1812`): a ledger holding only CONSULT rows renders nothing. `TestCheckRef` (`:934`): a `--ref` naming a CONSULT row is rejected as "not a DEFER or SETTLED decision", as `:1091-1097` does for ADDRESS.
  - The unknown-disposition message at `:231`, the lane-hint strings at `:628` and `:744`, and the reopen advice at `:981` take the new wording.
- `claude/.claude/hooks/tests/test_review_ledger_script.py`:
  - `_append_args` passes `foo.py:12` when the disposition is ADDRESS and no source is given. Omitted-source behavior for DEFER and SETTLED does not change.
  - `test_append_defaults_source_to_n_a` (`:338-342`) is replaced in `TestReviewLedgerAppendHappyPath` by three tests, each writing no row on a rejection:
    - an ADDRESS append without `--source` exits 2, and stderr names `--source` and `--disposition ADDRESS`;
    - `./absent.py:3` is stored as `absent.py:3` with an empty `site_hash`, although the file does not exist;
    - an ADDRESS source holding a control character exits 2 with the normalization's "control character" message, not the byte-limit one.
  - The six filler sites move their control characters to an engineer SETTLED's quote (row 15). `:2484`, `:3006`, `:3017` and `:3902` pass `_fields_for_row_of_bytes` the SETTLED overrides `:1481` passes. `:2995` and `:3109` use `_engineer_args` with the control characters in finding, rationale and quote, and still assert "4095-byte limit". `_fields_for_row_of_bytes`'s docstring (`:164-169`) drops the ADDRESS-source mode.
  - The `:708-712` fixture row's `source` becomes the new default.
  - A new helper, `_round_cap_verdict_args`, builds a CONSULT append. Its name stays apart from `_consult_args` (`:1238`, row 42).
  - A CONSULT happy-path test appends `--disposition CONSULT --rationale noise --round 2`. The stored row has an empty finding, `n/a` source, empty `reviewers` and empty `site_hash`. `show` prints a line holding `"disposition":"CONSULT","rationale":"noise"`, and its header reads `max_round=2`. The docstring names the round-cap stop in `code-review/SKILL.md` as the reader of that text.
  - One CONSULT rejection test through the script: a word outside the three exits 2 and writes no row. The word matrix is in the lib test.
  - Two dedup tests (row 46). One session's identical CONSULT append at one round exits 0 and leaves one row. The same append from a second seeded session adds a second row.
  - A `TestRoundCapVerdictSkillParity` class beside `TestReviewLedgerEnumParity` (row 52) extracts from the round-cap region the backticked words of the "where `<verdict>` is" sentence and the backticked `"disposition":"CONSULT","rationale":"noise"` literal. It asserts four things:
    - each extracted word is accepted by the script;
    - the script's "Pass one of:" listing equals the extracted set;
    - the usage text's "one of:" listing for CONSULT equals it too;
    - the literal is a substring of the `show --out` line for an appended noise row.
  - The unknown-disposition message at `:655` and the lane strings at `:1690` and `:2438` take the new wording.
- `claude/.claude/scripts/tests/test_author_outcome.py`:
  - `--source` at `:1195-1201` and `:1229-1231`.
  - `TestRoundBlocks` (`:966`): CONSULT rows in rounds 1 and 2 beside an ADDRESS row in round 1 yield one block, holding only the ADDRESS row. An ADDRESS, CONSULT, ADDRESS run in one round yields one block of the two ADDRESS rows, mirroring `:997-1003`.
  - `TestRoundNumberMismatch` (`:630`): two round-opens over an ADDRESS round and a CONSULT-only round return `_MISMATCH_FEWER_BLOCKS`, as a round that logged nothing does.
  - `TestReviewLedgerSubprocessIntegration` (`:1160`): a real CONSULT append, read back through `_read_ledger_row_entries_for_session`, yields no block.
- `claude/.claude/hooks/tests/test_require_code_review.py`: `--source` at `:890-895`.
- `claude/.claude/hooks/tests/test_announce_branch_review_history.py`: `--source` at `:114-119`.

- `claude-skills/skills/code-review/SKILL.md`: the edits at `:327` (plus three nested lines), `:330`, `:385`, `:391` and `:446`, as worded in Approach.
- `claude-skills/skills/tests/test_skills.py`:
  - `_PINNED_CONTRADICTION_ROUTE_CLAUSE` (`:7087-7162`) gains the `:385` sentences and the `:391` reopen wording (`:7159-7161`).
  - The `:8060` prose control becomes `--round <N> --source '<file:line>' [--decided-by`, which pins the flag outside brackets.
  - The `:8082` prose control becomes "CLEAN and CONSULT take no lanes".
  - A new test beside `:7794` reads the region through `_normalized_anchor_text`. It asserts each of these as a substring:
    - "When this round has a finding" (row 22);
    - "two or more `noise` `CONSULT` rows in any rounds, this verdict's own included";
    - "the verdict is also a blocking stop-and-ask";
    - the Grep literal `"disposition":"CONSULT","rationale":"noise"`;
    - "treat an uncertain count as two";
    - "Ask once this round's findings are tagged, before any is logged or fixed";
    - "before retrying the denied spawn, log it with", followed by the `--disposition CONSULT --rationale '<verdict>' --round <N>` command;
    - "An append that still fails after one retry is a blocking stop";
    - "where `<verdict>` is `re-plan`, `noise` or `one-defect`".

    It also asserts by index that the three option labels appear in order, and that *Ship*'s "apply the enforcement-invariant rule to each finding first" precedes "then log each finding as that route logs a human keep".
- `docs/scripts.md`: the `:152`, `:153`, `:156` and `:171` edits and the **Consult verdicts.** bullet.
- `docs/transcript-analysis.md`: the `:1628` sentence.
- `docs/design-decisions/round-cap-consult-rearm.md`: the dated note.
- `docs/design-decisions/ready-for-review-fix-loop-convergence.md`: the dated note.
- `CHANGELOG.md`: the `[Unreleased]` entry.
- Verification: Verification 1 to 6, then `/code-review` and `/skill-review` on the one staged diff.

Projected diff: about 470 lines (additions plus deletions). Most are in `test_review_ledger_script.py` (about 150), `test_review_ledger_lib.py` (about 80), `test_author_outcome.py` (about 55), `test_skills.py` (about 50), `_review-ledger-lib.sh` (about 45) and `review-ledger.sh` (about 28).

## Verification

1. Red first, inside the dispatch. With every new and changed test written and no production file edited (`review-ledger.sh`, `_review-ledger-lib.sh`, `author_outcome.py`, `code-review/SKILL.md`), run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Every new behavior test and every moved pin fails there. These are the intended pre-change passes, kept as controls:
   - the six byte-size tests moved to an engineer SETTLED's quote (row 15);
   - the trend case where a CONSULT row beside an ADDRESS row leaves that round at 1;
   - the `render` and `--ref` cases for a CONSULT row, which pin documented non-reader behavior;
   - tests whose only change is `_append_args`'s new ADDRESS default.

   A new test outside that list that passes before the change is reworked until it fails there.
2. After the production edits, `select-tests.py` passes. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` are clean.
3. `wc -l claude-skills/skills/code-review/SKILL.md` prints 539, which `check-skill-length.sh` holds to 550 at commit.
4. Every line `git grep -n "SETTLED require " -- claude/ claude-skills/ docs/` prints also names ADDRESS (the trailing space skips "SETTLED requires --decided-by"), so no current-behavior statement of the source rule is left at two dispositions. Dated notes and `CHANGELOG.md` history are records and are exempt.
5. `git grep -n -e "CLEAN takes" -e "every other disposition" -- claude/ claude-skills/ docs/scripts.md` prints nothing, so no lane or `--ref` rule leaves out CONSULT.
6. Every reopen spelling names `--source` (row 48):
   - `git grep -n -e '--relog reopens' -e '--relog, which' -e '--relog`, which' -e '--relog`, taking' -- claude/.claude/scripts/_review-ledger-lib.sh claude-skills/skills/code-review/SKILL.md docs/scripts.md` prints nothing;
   - `git grep -n -F -e 'relog with the decision' -e 'relog` with the decision' -- claude/.claude/scripts/_review-ledger-lib.sh claude-skills/skills/code-review/SKILL.md docs/scripts.md` prints the four sites: `_review-ledger-lib.sh:590` and `:757`, `code-review/SKILL.md:391` and `docs/scripts.md:171`.
7. This branch's own review rounds run the stowed main-checkout script and skill (row 33), so they do not exercise the new rules. The tests are the evidence for them.
8. Review gates:
   - `/plan-review` on this plan before any code edit.
   - `/code-review` on the one staged commit, which also needs `/skill-review` for `code-review/SKILL.md`, as `require-skill-review.sh` enforces.
   - `/ready-for-review` before the push.
   - No hook, agent, rule, plugin or `config-keys.psv` file changes, so `claude-hook-review`, `/agent-review`, `ai-instruction-and-memory-files` and `plugin-semver` do not apply.

## Out of scope

- The prior session's proposals, which are the session's and not engineer-verified:
  - replacing the hand-maintained per-hook list at `docs/hooks.md` lines 481-490;
  - a duplicated-fact angle for `comment-discipline-reviewer`;
  - a liveness fix at `require-routing-read.sh` line 63.
- The Context's "Do not add" list stands as relayed (row 3).
- Using the CONSULT rows anywhere but the stop. Teaching `plan-architect`'s Foundation questions to use them, or `transcript-analysis.py` to count them for the interval's first revisit condition, is possible once they exist, but the stop needs neither.
- A whole-text pin for the round-cap region. No such pin exists (row 12), and the clause test names each load-bearing clause (M7).
- Making Step 0.1's recovery skip a mid-round CONSULT row. The stop reads no round number, and a compaction inside a disposition batch already renumbers the same way (row 45).
- A row field that tells one session's repeat of one verdict at one round from the first. The repeat is not expected (rows 46, 47).
- Backfilling `--source` on legacy `n/a` ADDRESS rows, or CONSULT rows for verdicts given before the change. The ledger is append-only and is never written by hand (`code-review/SKILL.md:450`).
- An existence check on an ADDRESS source's file. It would reject a fix that creates a file.
- `ready-for-review/SKILL.md` and `plan-architect.md`. `/ready-for-review`'s retry sentence stays true (row 25), its step-4 consult already routes through the round-cap section (row 44), and the stop reads the ledger itself, not the consult.
- Rejecting `--authoring-agent` or `--authoring-effort` on a CONSULT row. The skill omits them, and both stay optional for every disposition.
