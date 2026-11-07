# Redirect /ready-for-review step 3's diff stdout

## Context

**Goal:** stop `/ready-for-review` step 3 from printing the whole cumulative PR-vs-base diff into the session, by discarding the script's stdout and keeping the diff available only through the file path on stderr.

Ask: "let's do the redirect. I think even if we do the reviewer orchestration change, the redirect will still be helpful." Scope is the redirect only; moving the cumulative review loop into `review-orchestrator` is not part of this change. The engineer chose the label "Fixes GH-1296 (Recommended)" for whether the PR closes GH-1296.

Why now: PR #1389 added the `DIFF_EMPTY: no changes against base` stderr line, which step 3 now halts on. That met the one constraint GH-1296 states for any fix, an empty-diff signal that survives a stdout redirect. Step 3 still runs `~/.claude/scripts/pr-diff-against-base.sh --record --diff-file` with stdout visible, so the diff bytes still land in the session on every pass and stay in context. The two sibling call sites (`code-review/SKILL.md` for `--staged --diff-file`, and step 4's cache-hit call in `ready-for-review/SKILL.md`) already append `> /dev/null`.

Intended outcome: step 3's invocation discards stdout, nothing in step 3 depends on the diff text being visible in the session, a test holds the redirect in place, and the docs and changelog describe the new behavior.

## Approach

Step 3's command discards stdout, so the diff reaches the session only through the `DIFF_FILE:` path, and one pin test per redirected call site holds that in place. Each command pin requires the redirect to end the command line, so an appended `2>&1` or `&>`, which would discard the stderr signals step 3 acts on, fails it. The script, `code-review/SKILL.md` and every doc stay as they are. "Critical files" lists the per-file edits.

Alternatives set aside:

- **A flag on `pr-diff-against-base.sh` that suppresses stdout, or making `--diff-file` imply it.** Either adds script interface and tests for what a shell redirect already does at the caller. Either also breaks the "stdout is unchanged by any flag below" contract in `docs/scripts.md:168`, which `test_pr_diff_against_base.py:1522` asserts for `--record --diff-file`. The two sibling calls already use the redirect.
- **Running step 3's command in a subagent.** The cumulative `/code-review` pass must stay in this session, which is why it passes `unrouted` (`ready-for-review/SKILL.md:91`). The session needs only the path, which the redirect leaves on stderr.
- **Prose only, no test.** No current test fails if the redirect is dropped (row 16), and the Context's intended outcome asks for a test that holds it.
- **One scan test asserting that every skill-body call of the script carries `> /dev/null`.** Only three call sites exist (row 19), and each already has its own literal-pin test. A scan would misfire on a later prose mention of the script's name. Per-site literals are lighter.

**Root problem.** `/ready-for-review` step 3 runs `pr-diff-against-base.sh --record --diff-file` with stdout visible, so every cumulative pass prints the whole PR-vs-base diff into the session, where it stays in context. The same bytes already sit on disk at the `DIFF_FILE:` path the step hands onward, and the two sibling calls already discard stdout.

**Givens** (fixed conditions this design accepts, each beyond its own reach):

- **G1 — The Bash tool returns a command's stderr to the session when its stdout goes to `/dev/null`.** The Claude Code harness imposes this behavior. `[verified: ran ~/.claude/scripts/pr-diff-against-base.sh --diff-file > /dev/null from this worktree before implementation; the tool result showed a stderr "gh pr view failed; defaulting base to main" line and a "DIFF_FILE: <path>" line, and no diff text]`
- **G2 — `ready-for-review/SKILL.md` is at its 200-line cap.** `check-skill-length.sh` denies a commit that grows a SKILL.md past its limit, this file has no override, and raising its cap needs a decision outside this plan. `[verified: Grep line count 200; claude/.claude/hooks/check-skill-length.sh:6-9,139-140]`

**Mechanisms and material assumptions.**

| # | Mechanism / assumption | Evidence | anchors |
|---|---|---|---|
| 1 | The change is the step-3 redirect. Moving the cumulative review loop into `review-orchestrator` is a separate change. | `[engineer-verified: "let's do the redirect. I think even if we do the reviewer orchestration change, the redirect will still be helpful."]` | anchors: root |
| 2 | The PR body carries `Fixes GH-1296`. The option description shown with that label was the session's proposal and grounds nothing here. | `[engineer-verified: "Fixes GH-1296 (Recommended)"]` | anchors: root |
| 3 | GH-1296's one stated constraint on any fix is an empty-diff signal that survives a stdout redirect. | `[unverified: issue text not read; the plan's Context states it]` | anchors: root |
| 4 | That signal survives. The script prints `DIFF_EMPTY: no changes against base` on stderr, and step 3's halt keys on that stderr line. | `[verified: claude/.claude/scripts/pr-diff-against-base.sh:167-169; claude-skills/skills/ready-for-review/SKILL.md:86]` | anchors: row3 |
| 5 | Mechanism: append ` > /dev/null` to the command at `ready-for-review/SKILL.md:83`. This matches the shape of the two sibling calls. | `[verified: claude-skills/skills/ready-for-review/SKILL.md:106; claude-skills/skills/code-review/SKILL.md:295]` | anchors: root |
| 6 | Discarding stdout changes neither artifact. `--record` writes the subject from the captured `$DIFF_TEXT`, and `--diff-file` writes the diff file from the same variable, both after the stdout `printf`. A write to `/dev/null` does not fail, so the `set -e` path is unchanged. | `[verified: pr-diff-against-base.sh:39,155,163,205,236]` | anchors: row5 |
| 7 | `marker.sh write cumulative-review` reads the recorded subject file, never step 3's stdout. | `[verified: claude/.claude/scripts/marker.sh:669-691]` | anchors: row5 |
| 8 | `marker.sh status` recomputes the hash by running the script in its own process and hashing that run's stdout, so the session's redirect never reaches it. | `[verified: claude/.claude/hooks/_lib.sh:1262; marker.sh:15]` | anchors: row5 |
| 9 | Every signal step 3 acts on arrives on stderr: `DIFF_EMPTY:`, `DIFF_FILE:`, and every failure line. Step 4's reuse branch takes the path from step 3's `DIFF_FILE:` line, not the diff text. | `[verified: pr-diff-against-base.sh:64-156,168,208-254,240; ready-for-review/SKILL.md:106]` | anchors: row5 |
| 10 | No hook, runtime script, or transcript analysis keys on step 3's command text. Two gates match on command shape, not on the script name (rows 30 and 31). | `[verified: Grep "pr-diff" over claude/.claude/hooks/*.sh hits only comments, at _config.sh:1062 and _lib.sh:597,827,1262,3292; Grep "pr-diff-against-base" over *.py hits only test files]` | anchors: row5 |
| 11 | With stdout gone, the inline `/code-review` pass has no copy of the diff in context. `code-review` Step 0 names "context, git diff, or the conversation" as sources of what changed. A session that reaches for `git diff` gets porcelain output, which diff config can make differ from the plumbing diff the script records. | `[verified: claude-skills/skills/code-review/SKILL.md:11; pr-diff-against-base.sh:145-155]` | anchors: row5 |
| 12 | Mechanism: insert the one sentence given under Critical files into `ready-for-review/SKILL.md:91`, directly after "(`unrouted` keeps this pass in this session).". `/review-pr` step 5 already runs `/code-review` in-session over a path-only diff file, but that precedent rests on the skill's instruction text alone. The evidence that the shape works is Verification step 6's observation of a `Read` of the `DIFF_FILE:` path in this change's own cumulative pass. | `[verified: claude-skills/skills/review-pr/SKILL.md:86; the observation itself is pending until Verification step 6]` | anchors: row11 |
| 13 | The inserted sentence must not contain the word `unrouted`. The span from "Run `/code-review` against that diff" to "(`unrouted` keeps this pass in this session)" must stay intact. | `[verified: claude-skills/skills/tests/test_skills.py:9158-9185]` | anchors: row12 |
| 14 | Both edits stay inside existing lines, because of G2. Both also sit outside the verbatim-pinned `CACHE_RULE` block (lines 76-78) and `SCOPE_RULE` block (lines 87-89). | `[verified: ready-for-review/SKILL.md:76-78,87-89; test_skills.py:4675,4884,4968,5004 key pins on both anchor names]` | anchors: row12 |
| 15 | Line 91's no-`DIFF_FILE:` halt stays as written. It already halts before `/code-review`. Its stated reason (the prose reviewer carries no `Bash`) is still true. The row-12 sentence now precedes it and supplies the stronger reason. | `[verified: ready-for-review/SKILL.md:91]` | anchors: row12 |
| 16 | No current test fails if step 3's redirect, or `/code-review`'s, is dropped. The step-3 literals `pr-diff-against-base.sh --record` (:5229) and `pr-diff-against-base.sh --record --diff-file` (:5272) still match with the redirect appended. The code-review note test's literals (:5428, :5442-5447) omit the redirect. Only step 4's call is pinned with it (:5263). | `[verified: test_skills.py:5229,5263,5272,5428,5442-5447]` | anchors: root |
| 17 | Mechanism: a new test pins the redirected step-3 command. It is a dedicated test rather than a longer literal at :5272, because :5272 sits in step 4's handoff test and its failure message names step 4's dependency on `--diff-file`, not the redirect. | `[verified: test_skills.py:5242-5292]` | anchors: row16 |
| 18 | Mechanism: add a separate no-further-redirect check of code-review's redirected literal to that test, outside the tuple at :5442-5447. A plain substring in the tuple would pass on an appended `2>&1`. The check stays out of `staged_diff_literal` (:5428), because that literal's exactly-once count (:5437) guards against the instruction being duplicated, and a longer literal would let an un-redirected copy slip past that count. | `[verified: code-review/SKILL.md:295 carries the exact text; test_skills.py:5428-5451]` | anchors: row16 |
| 19 | The three calls in rows 5 and 18 are the only skill-body invocations of the script. | `[verified: Grep "pr-diff-against-base\.sh" over claude-skills/skills/*/SKILL.md, claude/.claude/agents/*.md, plugins/**/*.md, claude/.claude/rules/*.md and claude/.claude/CLAUDE.md: code-review/SKILL.md:295, ready-for-review/SKILL.md:83,106]` | anchors: row18 |
| 20 | No hooks test pins step 3's command or line 91. The hooks tests read this SKILL.md only for named command spans and the gate-completion order. `select-tests.py` also maps this SKILL.md to `SCRIPTS_TESTS_DIR`, so `test_pr_diff_against_base.py`'s `DIFF_EMPTY:` token read runs under Verification step 1. | `[verified: Grep over claude/.claude/hooks/tests/test_require_ready_for_review.py:34-2703 and test_hook_alignment.py:3341; select-tests.py:703-711,861]` | anchors: row14 |
| 21 | No doc describes step 3's stdout as reaching the session, so no doc changes. `docs/scripts.md:168`'s "hands the printed `DIFF_FILE:` path onward" stays accurate, since that line prints on stderr. Its "stdout is unchanged by any flag below" describes the script, which does not change. | `[verified: docs/scripts.md:168-179]` | anchors: root |
| 22 | The other doc hits describe the script's own capture, the subject recording, base resolution, or dated follow-ups. None describes step 3's stdout as current behavior. The hits are `docs/design-decisions/cumulative-review-marker-not-recomputed.md:9`, `cumulative-review-marker-keyed-on-normalized-diff.md:21`, `ready-for-review-cumulative-diff-cache.md:9`, `ready-for-review-stacked-pr-base.md`, `docs/hooks.md:347,353`, `docs/cost-levers-considered.md:180-214` and `docs/case-studies/claude-code-sandbox-write-boundary.md:152,436`. | `[verified: those lines]` | anchors: row21 |
| 23 | Merged plans that name `--record --diff-file` (`rfr-reentry-convergence.md`, `reviewer-diff-input.md`, `reviewer-diff-handoff-by-path.md`) are read-only records. | `[verified: Grep "--record --diff-file"; docs/cost-levers-considered.md:15-16; CLAUDE.md § Scope discipline, Axis 3]` | anchors: row21 |
| 24 | `test_marker_script.py`'s `_record_subject` docstring ("step 3's own diff command with the recording flag") stays. The helper mirrors step 3's subject recording, which the redirect does not change, and it already omits `--diff-file`. | `[verified: claude/.claude/hooks/tests/test_marker_script.py:202-217]` | anchors: row6 |
| 25 | Mechanism: one `CHANGELOG.md` bullet under `## [Unreleased]`, `### Changed`, placed directly after the `DIFF_EMPTY:` entry and its sub-bullet, which record the groundwork for this change. | `[verified: CHANGELOG.md:5,7,113-114]` | anchors: root |
| 26 | `ready-for-review/SKILL.md` changes, so `/skill-review` is hook-required before the commit. No file under `plugins/` changes, so no version bump fires. | `[verified: .claude/rules/review-pipeline-dispatch.md]` | anchors: root |
| 27 | Bash stdout of a large diff reaches the session cut short, so the stdout copy was also partial on a large PR. No mechanism depends on this row. | `[unverified: stated by review-pr/SKILL.md:86 and docs/cost-levers-considered.md:192 ("30,000-byte truncation threshold"), not observed this session]` | anchors: root |
| 28 | The pin on `code-review/SKILL.md`'s own redirect (rows 16 and 18) stays in this change, though that file is not edited. | `[engineer-verified: "Keep the pin"]` | anchors: row18 |
| 29 | The line-91 sentence (row 12) stays in this change, though the Ask names only the redirect. | `[engineer-verified: "Keep the sentence"]` | anchors: row12 |
| 30 | The marker-script-shape scan in `enforce-marker-script-shape.sh` runs its redirect scan on any command containing `.claude`, takes the `/dev/null` target, finds no `.claude` or ledger-directory substring in it, and skips it. The two sibling calls already pass the identical `> /dev/null` shape through this gate. | `[verified: enforce-marker-script-shape.sh:653-690,838; the G1 run above passed the shape through this gate]` | anchors: row10 |
| 31 | The harness-native worktree guard has no trigger the command meets, since it has no `$(...)`, `$CLAUDE_CONFIG_DIR`, `$PPID` or variable-built path. The two sibling calls already pass the identical `> /dev/null` shape through this gate. | `[verified: docs/worktree-bash-guard.md; the G1 run above passed the shape through this gate]` | anchors: row10 |

## Critical files

One `code-writer` dispatch covers every file below. The files share one small context, and the test literal depends on the SKILL.md text.

- `claude-skills/skills/ready-for-review/SKILL.md`. Two in-line edits, and no line added (G2):
  - Line 83: the command becomes `~/.claude/scripts/pr-diff-against-base.sh --record --diff-file > /dev/null`.
  - Line 91: directly after "(`unrouted` keeps this pass in this session).", insert the sentence: "Read the diff only from the file at that path: the `> /dev/null` in the command above keeps the diff's bytes out of this session's context."
- `claude-skills/skills/tests/test_skills.py`:
  - New `test_ready_for_review_step3_discards_the_diff_stdout`, directly after `test_ready_for_review_step3_never_produces_a_staged_diff`. Reuse `_skill_file`, `_section_between` and `_READY_FOR_REVIEW_STEP3_HEADING`, as the neighboring step-3 test does. It asserts two things on step 3's section text:
    - `~/.claude/scripts/pr-diff-against-base.sh --record --diff-file > /dev/null` is present and not followed by a further redirect. Match it with `re.search(re.escape(<command>) + r"(?!\s*(?:[0-9]?>|&>))", <text>)`, so an appended `2>&1` or `2>/dev/null` fails. Those would discard the `DIFF_EMPTY:` and `DIFF_FILE:` stderr signals that step 3 acts on.
    - The fragment `Read the diff only from the file at that path` is present, which holds the line-91 sentence in place (rows 12 and 29). The fragment contains no `unrouted`.
    - The docstring names GH-1296 and states the why: step 3 hands the diff on by its `DIFF_FILE:` path, so printing it to stdout only adds its bytes to the session's context.
  - In `test_code_review_staged_diff_instruction_lives_in_its_own_note_only`, add a check that `~/.claude/scripts/pr-diff-against-base.sh --staged --diff-file > /dev/null` is present in the note, with the same no-further-redirect lookahead. Leave `staged_diff_literal` and the literal tuple at :5442-5447 as they are (row 18). Extend the docstring at :5408-5414 to say the test also pins the redirect, so a later reader does not remove the check as redundant.
- `CHANGELOG.md`. Insert after line 114: "- **`/ready-for-review` step 3 now discards `pr-diff-against-base.sh`'s stdout, so the cumulative diff no longer prints into the session.** The session reads the diff from the file the `DIFF_FILE:` stderr line names. The script is unchanged, and step 3 still halts on the `DIFF_EMPTY:` line."
- `.claude/plans/redirect-step3-diff-stdout.md`, this plan, which ships with the change.

Checked and left unchanged (rows 6-10 and 20-24): `claude/.claude/scripts/pr-diff-against-base.sh`, `claude/.claude/scripts/marker.sh`, `claude-skills/skills/code-review/SKILL.md`, `docs/scripts.md`, `docs/hooks.md`, the four `docs/design-decisions/` files in row 22, `docs/cost-levers-considered.md`, `docs/case-studies/claude-code-sandbox-write-boundary.md`, `claude/.claude/scripts/tests/test_pr_diff_against_base.py`, `claude/.claude/hooks/tests/test_marker_script.py`, and the merged plans in row 23.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes.
2. `scripts/venv-run.sh ruff check claude-skills/skills/tests/test_skills.py` is clean.
3. Each new pin fails when what it holds is gone. Run `scripts/venv-run.sh pytest claude-skills/skills/tests/test_skills.py -k step3_discards_the_diff_stdout` and see it fail for each of these edits, restoring the text after each:
   - Delete ` > /dev/null` from `ready-for-review/SKILL.md:83`.
   - Append ` 2>&1` to the end of that command.
   - Delete the line-91 sentence.

   Then repeat with the redirect in `code-review/SKILL.md:295` deleted, and again with ` 2>&1` appended, under `-k staged_diff_instruction_lives_in_its_own_note_only`. Before staging, `git status --short` shows `code-review/SKILL.md` unmodified.
4. `grep -c '' claude-skills/skills/ready-for-review/SKILL.md` prints `200` (G2).
5. G1 is already observed (the givens list): it ran before implementation, with `--diff-file` only, so it recorded no cumulative-review subject.
6. During this change's own `/ready-for-review` run, the step-3 `/code-review` pass issues a `Read` of the `DIFF_FILE:` path before it writes its disposition record. Check the tool log for that `Read` and record the result in the PR body, since this is the evidence for row 12.
7. `/code-review` on the staged change runs `/skill-review` for the SKILL.md edit (row 26), then `/ready-for-review` runs before handoff.

Rollback: revert the `ready-for-review/SKILL.md`, `test_skills.py` and `CHANGELOG.md` edits in one commit. No persisted state needs migration, because rows 6-8 make the subject and diff-file artifacts byte-identical with or without the redirect. The symptom that calls for the revert is step 3 halting with no `DIFF_FILE:` line.

## Out of scope

- Moving the cumulative review loop into `review-orchestrator`, per the Ask (row 1).
- Any change to `pr-diff-against-base.sh` or its tests, per the first alternative under Approach.
- Teaching `code-review/SKILL.md` Step 0 that a path after `unrouted` names the diff under review. Both in-session callers that pass only a path, `/review-pr` step 5 and step 3 after this change, say so in their own text. Changing `code-review`'s argument contract is a separate change.
- A `docs/cost-levers-considered.md` entry. The register keeps "the verdict plus the measured reason" (`docs/cost-levers-considered.md:9-10`), and this change measures nothing.
- Rewording the dated records and merged plans that mention step 3's command (rows 22-23).
