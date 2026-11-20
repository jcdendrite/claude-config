# Stop hook word boundaries

## Context

Goal: make the Stop hook's permission-question predicate match whole words, so wording like "commitment", "committee" and "should include" stops firing and "emergency" and "unblocked" stop silencing a real stall.

Ask: "oh wow yeah let's do #1 . I think that's the most important one" (the engineer, choosing the word-boundary fix for the Stop hook predicate, as its own PR, with a plan).

The Stop hook `claude/.claude/hooks/advance-past-commit-stall.sh` is on by default for every stow consumer since the autonomous-shipping change merged. Its three regexes (`VERB_RE`, `OBJECT_RE`, `EXCLUDE_RE`, about lines 153-162) are bare substrings run under `nocasematch`, so in-word matches fire or silence wrongly. `docs/commit-stall-block.md` § "Known limitations" discloses these gaps as an enumerative list. The repo rule is POSIX ERE only (`[[:space:]]`; no `\b` or `\s`).

Intended outcome: whole-word boundaries on the verb and object halves and a leading boundary on the exclusion list, the corpus rows for the in-word gaps flipped into regression pins, and the Known-limitations list shrunk to what a word boundary cannot fix (wrong-sense matches such as "push back").

## Approach

Bound every match with the explicit POSIX classes `(^|[^[:alnum:]_])` and `([^[:alnum:]_]|$)`: both sides of each permission verb, both sides of each object and its inflections, and only the start of each exclusion phrase. One verb alternation feeds both the fire pattern and the `phrasing-drift` check, and the verb-to-object joiner supplies the verb's trailing boundary and the object's leading boundary, so a single space serves as both.

The hook's regex block (`advance-past-commit-stall.sh:159-162`) becomes:

```bash
WORD_START='(^|[^[:alnum:]_])'
WORD_END='([^[:alnum:]_]|$)'
PERMISSION_VERBS='(want me to|would you like me to|let me know if|shall I|should I|do you want me to)'
SHIPPING_OBJECTS='(commit(s|ted|ting)?|push(es|ed|ing)?|open (a|the) PRs?|create a PR)'
# The join's first and last characters are the verb's trailing and the object's leading word boundary, so one space serves as both.
VERB_OBJECT_JOIN='[^[:alnum:]_.?!]([^.?!]*[^[:alnum:]_.?!])?'
FIRE_RE="${WORD_START}${PERMISSION_VERBS}${VERB_OBJECT_JOIN}${SHIPPING_OBJECTS}${WORD_END}"
VERB_RE="${WORD_START}${PERMISSION_VERBS}${WORD_END}"
# Each exclusion phrase is bounded at its start only, so a form that extends it (merged, errors) still excludes.
EXCLUDE_RE="${WORD_START}(merge|--force|force-push|reset --hard|close the PR|delete the branch|failing|failed|error|blocked|anyway)"
```

The `phrasing-drift` branch at `:174` keeps its text and now tests the bounded `VERB_RE` (M2). The comment at `:164` says "These regexes" instead of "Both regexes", because three are matched.

The `force push` and `-f` spellings stay a disclosed gap (hook header `:52`, `docs/commit-stall-block.md:76`), per Out of scope.

What changes on the corpus (M4):
- **Stops firing:** "commitment", "committee", "open a project", "create a profile page" and "should include", plus the new rows "shall include", "recommit", "reopen the PR" and the underscore identifier `commit_hook`.
- **Starts firing:** "emergency" (through "merge") and "unblocked" (through "blocked").
- **Unchanged:** "push back" and "push notification" still fire, and "error handling" still excludes, because a word boundary cannot tell a word's sense.
- **Still fires through an inflection:** "committing", "committed", "commits", "pushing", "pushed", "pushes", "open the PRs".
- **Still excludes through a suffix:** "merged", "errors".

Alternatives weighed:
- **`\b` or `\<`/`\>`.** Rejected: CLAUDE.md's "Hook regexes: POSIX ERE only" bars them, and `test_hook_alignment.py:1283` flags only `\s`, so nothing mechanical would catch the slip (row 11).
- **`[[:<:]]` and `[[:>:]]`.** Rejected: a BSD bracket extension, not POSIX ERE, so the same rule bars it. Whether glibc accepts it was not checked.
- **`grep -w`, or a bash loop that splits the sentence into words.** Rejected: `grep -w` adds a fork on every Stop, and the no-fire path is pinned at one `jq` spawn (row 10). A word loop must still re-match the multi-word verbs and objects.
- **A trailing boundary group on the verb and a leading boundary group on the object, around today's `[^.?!]*` joiner.** Rejected: one space cannot satisfy both groups, so "Want me to commit" stops firing (row 6).
- **Boundaries around the unchanged object list.** Rejected: "committing" and "pushing" stop firing (row 7).
- **A trailing boundary on the exclusion phrases too.** Rejected: "merged", "errors" and "force-pushed" stop excluding, which newly fires on questions the list routes back (row 8).
- **`PRs?` on `create a PR` as well.** Rejected: "create a PRs" is not English, so the branch would be untestable.
- **Re-running the regex experiment on `/bin/bash` 3.2.** Rejected: bash below 5.2 never reaches these regexes (row 9). The axis that matters is the regex library, which Verification covers with the local macOS run and the CI glibc run.
- **Extracting the predicate into `_lib.sh` and unit-testing it by sourcing.** Rejected: one hook uses it, a new hook-side `_*-lib.sh` fails CI, and the refactor is beyond the Ask (row 20, Out of scope).

### Assumption ledger

**Root:** The fire predicate's three regexes match bare substrings under `nocasematch`, so a verb, object or exclusion phrase inside a longer word wrongly fires or silences the Stop hook for every stow consumer.

**Givens:**
- G1. Hook regexes are POSIX ERE only. Reason: CLAUDE.md's repo rule, which this plan does not revise.
- G2. `[[ =~ ]]` hands the pattern to the platform's regex library: macOS libc for local runs, and glibc on CI's `ubuntu-24.04` runner (`.github/workflows/tests.yml:28`). Reason: bash and the OS vendor impose it.

**Mechanisms:**
- **M1. Whole-word fire pattern.** Replace `:159-161` with the block above, through `VERB_RE`. `WORD_START` and `WORD_END` follow the one hook precedent (row 5). The joiner holds at least one character where `[^.?!]*` allowed zero. The object alternation takes the stem form.

  anchors: root, row1, row4, row5, row6, row7, row9, row10, row11, row15
- **M2. Bounded `phrasing-drift` check.** `VERB_RE` is rebuilt from `PERMISSION_VERBS` with both boundaries, so the near-miss branch at `:174` and the fire pattern share one alternation and the near-miss line needs no edit. A new test pins the log line, which no test reads today.

  anchors: row2, row3
- **M3. Exclusion phrases bounded at their start only.** `EXCLUDE_RE` gets `WORD_START` before the alternation and no `WORD_END` after it. It gains no phrase, so the target-word choice and the settled force-push gap both stand.

  anchors: row8, row12, row19, row21
- **M4. Corpus and drift test.** Flip and add the rows in Critical files. Each new exclusion row gets a fire twin, per the corpus's existing twin rule (`test_advance_past_commit_stall.py:222-223`).

  anchors: row3, row4, row7, row8, row19
- **M5. Docs and CHANGELOG.** Reword the predicate sentence at `docs/commit-stall-block.md:18`, and replace the substring bullet at `:77-83` with a whole-word bullet. Add one `### Changed` entry under `## [Unreleased]`.

  anchors: row13, row14, row18, row19

1. `VERB_RE`, `OBJECT_RE` and `EXCLUDE_RE` are bare alternations, and `FIRE_RE` joins verb and object with `[^.?!]*`. All three run under `nocasematch` against the final sentence. [verified: `advance-past-commit-stall.sh:159-162`, `:167-183`]
2. The near-miss branch tests `VERB_RE`, which shares its verb alternation with the fire pattern. A bounded fire pattern with an unbounded near-miss would log `phrasing-drift` for "should include". [verified: `advance-past-commit-stall.sh:172-179`]
3. No test reads a `phrasing-drift` log line. [verified: Grep of `claude/.claude/hooks/tests/` for `phrasing-drift`, no match]
4. The corpus pins today's substring behavior with seven known-gap fire rows and three known-gap exclusion rows. It also marks two inflection rows and three PR-only rows as must-keep. [verified: `test_advance_past_commit_stall.py:234-259`]
5. One hook writes a word boundary as `(^|[^[:alnum:]_])…([^[:alnum:]_]|$)`. [verified: Grep of `claude/.claude/hooks/*.sh` for `[^[:alnum:]_]`, one call site, `require-worktree-for-git-writes.sh:265`]
6. `^` in bash's `=~` matches only at the start of the string, so a separate trailing boundary group on the verb and a separate leading boundary group on the object need two non-word characters between them. [unverified: reasoning from POSIX regcomp semantics without REG_NEWLINE; Verification step 2 exercises only the chosen joiner]
7. Boundaries around today's object list stop "Want me to start committing this?" and "Shall I start pushing this?" from firing. The stem form keeps both firing, and it also fires "Should I push back…". The stem form does not fire "should include", "commitment", "open a project", "create a profile page" or "Should I recommit?". [unverified: the dispatcher's scratch experiment on macOS bash 5.3.20 and `/bin/bash` 3.2.57, relayed and not re-run; Verification steps 1-2 re-check it]
8. Today's substring match excludes "merged", "merges", "errors" and "force-pushed", and a trailing boundary would stop that. No corpus row pins a suffixed exclusion form. [verified: hand trace of `advance-past-commit-stall.sh:162`; `test_advance_past_commit_stall.py:212-221` use base forms only]
9. Bash below 5.2 never evaluates these regexes. `_config.sh` refuses to load below the floor, `_lib.sh` sources it, and the hook exits 0 when `_lib.sh` fails to source. [verified: `_config.sh:40`, `_lib.sh:25-27`, `advance-past-commit-stall.sh:57-59`, `README.md:110`]
10. The no-fire path is pinned at one `jq` spawn and no `git`. [verified: `test_advance_past_commit_stall.py:1233-1243`]
11. CLAUDE.md requires POSIX ERE in hook regexes, and `test_hook_alignment.py` mechanically flags only `\s`. [verified: CLAUDE.md "Hook regexes: POSIX ERE only"; `test_hook_alignment.py:1283-1287`]
12. The merged autonomous-shipping plan kept `EXCLUDE_RE` unchanged for target words (`main`, `deploy` and similar) under the engineer's choice recorded there. This plan adds no target word, so that choice stands. [verified: `.claude/plans/autonomous-shipping-default.md:45`, `:227`]
13. Outside `.claude/plans/`, only the hook, its tests and `docs/commit-stall-block.md` restate the permission-verb wording, and `docs/hooks.md:244` restates none. [verified: case-insensitive Grep for `want me to`, whose other matches are unrelated eval and test files; `docs/hooks.md:244` per the exploration relay]
14. A CHANGELOG entry is a bold headline under `## [Unreleased]` / `### Changed` with sub-bullets, and a hook change ends with "The change is live on `git pull`." [verified: `CHANGELOG.md:5-17`, `:89-93`]
15. This PR is the word-boundary fix alone. [engineer-verified: "oh wow yeah let's do #1 . I think that's the most important one"]
16. Items 2, 4 and 5 go in a later PR. The mapping of those numbers to the tidy items is the dispatcher's relay. [engineer-verified: "and then yes I agree 2,4, and 5 could go in a PR after that"]
17. Item 3 is dropped. The mapping of "#3" to the `helpers.py` exit-status follow-up is the dispatcher's relay. [engineer-verified: "I think #3 is gold plating and should be dropped."]
18. The doc labels its 76 ms no-fire figure a one-off measurement, not a baseline for later changes. [verified: `docs/commit-stall-block.md:85`]
19. A wrongly silenced stall leaves the agent's own question for the engineer, so silence is the safe failure direction. A wrong fire force-continues once per prompt, with only the reason's carve-out to route it back. [verified: `docs/commit-stall-block.md:80`, `:83`]
20. The repo rule permits a single-hook extraction only inside the existing `_lib.sh`, because a new hook-side `_*-lib.sh` fails CI until that guard is widened. This plan declines it for scope and because `_lib.sh` loads in every hook. [verified: `.claude/rules/bash-unit-test-seams.md`]
21. Review-ledger row `468a72a48fe3` is SETTLED on the finding "Stop hook exclusion regex misses force push and -f spellings". The engineer decided it in an earlier session, keeping the documented gap with "Keep all four, ship (Recommended)". [verified: the review-narrative-ledger row with id `468a72a48fe3`]
22. Asked whether the `force push` and whole-token `-f` exclusions should ship in this PR, the engineer handed the call to the architect and named a scope worry. [engineer-verified: "Ask the architect. i'm worried this is scope creep / over-egineering"]
23. The architect's call is to leave both spellings out of this PR. Row 21 already settled the gap, and neither spelling is an in-word match, so row 15's word-boundary scope does not need them. The engineer's words do not state this outcome. [unverified: the architect's recommendation, relayed by the dispatching session]
24. A missed force-push spelling fires. The reason's carve-out defers to CLAUDE.md, whose Shipping bullet says the ask "does not extend to force-pushing". A characterization test pins both spellings as firing with the carve-out, and this plan leaves that test unchanged. [verified: `claude/.claude/CLAUDE.md:177`, `advance-past-commit-stall.sh:248`, `test_advance_past_commit_stall.py:1196-1225`]
25. Review-ledger DEFER `ce201a3f5174` deferred "no test pins the negative half of the rev-list failure arm (unborn HEAD on a non-default branch stays silent; exit 0 with empty stdout)" under the orthogonal-scope criterion. Its rationale names "follow-up test in the next PR touching this file". This plan edits no line of `_commit_stall_work_pending`. [verified: the review-narrative-ledger row with id `ce201a3f5174`; `advance-past-commit-stall.sh:220-230`]
26. `docs/commit-stall-block.md:20` counts "any failed upstream comparison" as pending, but when `rev-list` fails the hook counts the work as pending only if HEAD also resolves. [verified: `docs/commit-stall-block.md:20`, `advance-past-commit-stall.sh:225-229`]

## Critical files

- `claude/.claude/hooks/advance-past-commit-stall.sh`
  - `:159-162`: the regex block above (M1-M3).
  - `:164`: "Both regexes" becomes "These regexes".
  - `:52` and `:174` stay as written.
  - Reuse: the boundary classes from `require-worktree-for-git-writes.sh:265`.
- `claude/.claude/hooks/tests/test_advance_past_commit_stall.py`
  - M4: replace `:234-259`, from the "Known gap: OBJECT_RE has no word boundaries" comment through the last row, with the block below.
  - M2: after `:817-828`, add `test_phrasing_drift_logs_a_whole_word_verb_without_an_object`, parametrized over `(message, expected_drift)`:
    - `("Want me to profile it?", True)`
    - `("Want me to raise the commitment question with the team?", True)`
    - `("The fix should include tests.", False)`, which is red on the current hook because the unbounded `VERB_RE` matches inside "should include"
    - `("Want me to commit this?", False)`, the control that a fired sentence logs no drift
    - `("Let me know if", True)`, which ends on the verb and pins the `$` branch of `WORD_END` in `VERB_RE`

    Each case runs `_fire` on `dirty_repo` with `session_id="s"`, `prompt_id="p1"`, then asserts that `"phrasing-drift session=s prompt=p1\n" in log_text` equals `expected_drift`.

    `log_text` is `isolated_home / ".claude" / ".commit-stall-block.log"`, read as empty when the file is absent (the `:827-828` pattern).
  - `test_known_gap_question_targeting_an_action_outside_the_grant_still_fires_with_the_carve_out` (`:1196-1225`) stays, so its two force-push cases keep pinning the gap (row 24).

```python
    # Whole-word matching: a verb, object or exclusion phrase inside a longer word does not count.
    ("Want me to raise the commitment question with the team?", False),  # commit inside commitment
    ("Let me know if the committee wants a different layout.", False),  # commit inside committee
    ("Want me to open a project?", False),  # open a PR inside open a project
    ("Want me to create a profile page?", False),  # create a PR inside create a profile
    ("The fix should include a commit.", False),  # should I inside should include
    ("The release shall include a push to staging.", False),  # shall I inside shall include
    ("Want me to rename the commit_hook helper?", False),  # an underscore joins a word, so commit inside commit_hook does not count
    ("Want me to push the emergency fix?", True),  # merge inside emergency
    ("Want me to commit this now that CI is unblocked?", True),  # blocked inside unblocked
    # Accepted miss: a prefixed object has no word boundary before it, so the agent's question stands.
    ("Should I recommit this?", False),
    ("Want me to reopen the PR?", False),
    # Inflections the object half accepts.
    ("Want me to start committing this?", True),
    ("Shall I start pushing this?", True),
    ("Want me to get this committed?", True),
    ("Want me to squash these commits?", True),
    ("Want me to get this pushed?", True),
    ("Want me to batch these pushes?", True),
    ("Want me to open the PRs?", True),
    ("Want me to run `git push`?", True),  # a backtick is a word boundary
    ("Should I open a PR", True),  # no terminal punctuation: the end of the string closes the word
    # Real stalls whose only object is a PR alternative.
    ("Should I open a PR?", True),
    ("Want me to open the PR?", True),
    ("Want me to create a PR?", True),
    # An exclusion phrase is bounded only at its start, so a suffixed form still excludes. Each row has a fire twin.
    ("Want me to push this once it is merged?", False),
    ("Want me to push this once it is reviewed?", True),
    ("Want me to commit this despite the errors?", False),
    ("Want me to commit this despite the warnings?", True),
    ("Anyway, want me to commit this?", False),  # the sentence starts with an exclusion phrase; the existing "Want me to commit this?" row is its twin
    # Known gap: a whole word in an unrelated sense still fires or excludes (commit-stall-block.md).
    ("Should I push back on the spec change?", True),
    ("Should I add push notification support next?", True),
    ("Want me to commit the error handling changes?", False),
```

- `docs/commit-stall-block.md`
  - `:18`, three edits:
    - "case-insensitive phrasing match" becomes a case-insensitive whole-word match.
    - After "paired with commit/push/open a PR", add that inflections such as "committing" and "pushed" count.
    - The exclusion clause becomes "a word that starts with an exclusion phrase".
  - `:77-83`: replace the substring bullet with one bullet, **The predicate matches whole words, so a word in an unrelated sense still counts.**, and these sub-bullets:
    - "push back" and "push notification" fire ("Should I push back on the spec change?").
    - "Want me to commit the error handling changes?" is silenced through "error".
    - An exclusion phrase is bounded only at its start, so a longer word that begins with one still excludes ("merged", "errors"), and a word that merely contains one no longer does ("emergency", "unblocked", "parse_error").
    - A prefixed object does not match, so "Should I recommit this?" and "Want me to reopen the PR?" are not blocked. They may log `phrasing-drift`, because the verb half still matches.
    - An underscore joins a word, so `commit_hook` does not count and an underscore-wrapped question (`_Want me to commit this?_`) is not blocked.
    - Word boundaries follow the process locale, so a letter outside ASCII counts as part of a word under a UTF-8 locale.
    - The existing over-exclusion sentence (`:83`) and carve-out sentence (`:80`), kept.
  - `:20`, `:28`, `:62`, `:66`, `:76` and `:85` stay. Line 20 is Out of scope, and line 76 keeps disclosing the force-push spellings.
- `CHANGELOG.md`: add a top entry under `## [Unreleased]` / `### Changed`, headlined **The commit-stall Stop hook (`advance-past-commit-stall.sh`) now matches permission verbs, objects and exclusion phrases as whole words.**, with these sub-bullets:
  - The "Stops firing" and "Starts firing" sets from Approach, two examples each.
  - Inflections still count.
  - A prefixed object (such as "recommit") no longer fires.
  - A whole word in an unrelated sense still counts, with a pointer to [`docs/commit-stall-block.md`](docs/commit-stall-block.md) § "Known limitations".
  - "The change is live on `git pull`."

Reuse: `_fire`, `stop_input`, the `dirty_repo` fixture, and the log-read pattern at `test_advance_past_commit_stall.py:827-828`.

Dispatch split: one `code-writer` dispatch for all four files, in Verification's order: the test file first, then the hook, then the doc and CHANGELOG. Splitting off the docs would restate the flipped-row set in both prompts, which is the shared-state case `plan-it/SKILL.md` Step 5 says not to split.

Projected diff: about 135 lines (hook about 16, test file about 92, doc about 20, CHANGELOG about 7).

## Verification

1. **Red before the hook edit.** After editing only the test file, run `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_advance_past_commit_stall.py`. Exactly these 13 cases should fail, and every other case should pass:
   - The corpus rows "commitment", "committee", "open a project", "create a profile page", "should include", "shall include", "commit_hook", "emergency", "unblocked", "Should I recommit this?" and "Want me to reopen the PR?".
   - The `phrasing-drift` cases for "commitment" and "The fix should include tests.".

   Any other failure, or a listed case passing, means an expectation or a premise (row 7) is wrong. Then stop and report rather than shaping the hook to fit.
2. **Green after the hook edit.** After the hook edit, the same command passes under the local bash 5.2 or newer, which exercises the macOS regex library (G2).
3. **Scoped suite.** `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, and its selection includes `test_advance_past_commit_stall.py` and `test_autonomous_shipping_grant_boundary.py`. If either is missing, report it as a rule-table bug per CLAUDE.md rather than widening the run by hand.
4. **glibc.** The pushed branch's CI `tests` job on `ubuntu-24.04` passes, which runs the same corpus against glibc's regex library (G2). A local pass alone does not cover it.
5. **Lint.** `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` and `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/test_advance_past_commit_stall.py` are both clean.
6. **Text checks.**
   - A Grep of the hook for `\b` finds nothing.
   - A Grep of `docs/commit-stall-block.md` and the hook for `bare substrings` finds nothing.

## Out of scope

- The tidy follow-ups (sync-skill item 1, the `.gitignore` entry, the design-record banner, the printed-append sweep) go in a later PR (row 16).
- The `helpers.py` exit-status follow-up is dropped (row 17).
- The `force push` and whole-token `-f` exclusions: leave both spellings out.
  - Review-ledger row `468a72a48fe3` settled the gap as disclosed (row 21).
  - The engineer handed this PR's call to the architect over a scope worry (row 22).
  - The architect's call is to leave both out (row 23).
  - A missed spelling fires, with the carve-out deferring to CLAUDE.md's force-push limit (row 24).
  - The hook header `:52`, `docs/commit-stall-block.md:76` and the characterization test keep disclosing it.
- The unborn-HEAD negative-arm test and the `docs/commit-stall-block.md:20` correction: leave both out, still deferred together under review-ledger DEFER `ce201a3f5174` (rows 25, 26).
  - This PR edits no line of the work-pending arm.
  - Row 15 scopes this PR to the word-boundary fix.
  - The row's rationale routes the test to "the next PR touching this file", and this PR touches both the hook the row cites and its test file, so the deferral rests on the two points above.
- A near-cap corpus row and a glibc timing of the regex step. Both measured linear on macOS libc, with the `EXCLUDE_RE` start boundary adding at most about 2 ms at the 2048-character cap, and no wall-clock assertion belongs in the suite.
- A corpus row with an object beside a non-ASCII boundary character. It passes locally in every locale tried, and the CI glibc run covers the rest of the matrix.
- Telling a word's sense apart ("push back", "push notification", "error handling", "pre-commit"). A boundary cannot do it, and it needs the question-target reading `docs/commit-stall-block.md:75` already discloses.
- Other exclusion spellings, such as "merging", "closing the PR" and "deleting the branch". Neither the substring nor the whole-word form matches them, and widening the list's spellings is a separate change. M5's doc bullet discloses "merging".
- Target words such as `main` and `deploy` in `EXCLUDE_RE`, which the engineer kept out in the merged plan (row 12).
- Extracting the predicate into `_lib.sh` to unit-test it by sourcing (row 20). The corpus stays an end-to-end matrix.
- The last-sentence pattern removal's cost, and re-measuring the 76 ms no-fire figure, which the doc already labels a one-off (row 18).
