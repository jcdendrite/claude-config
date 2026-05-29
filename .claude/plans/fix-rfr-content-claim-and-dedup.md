# Plan: content-claim verification + check-runner dedup in /ready-for-review

## Context

**Goal:** two coordinated single-source-of-truth fixes to
`claude/.claude/skills/ready-for-review/SKILL.md`:

- **(A — the reported bug)** make step 4 catch PR-description claims that no longer match
  the files they describe (wrong deployment order, superseded feature names, references
  to deleted internals) before push.
- **(B — duplication surfaced while fixing A)** stop step 2 from restating the
  check-runner dispatch protocol that `subagent-delegation` already owns; defer to it the
  same way step 1 already defers divergence detection to `git-feature-branch-sync`.

**Why now (A):** three such inaccuracies survived a real PR even after an agent ran
`/ready-for-review` and updated the description. Root cause: step 4 compares the body
against `git log --oneline` and `git diff --name-only` — commit *messages* and file
*names* — but never the files' final *content*. A file can be in the diff yet carry a
stale description, because a later commit in the same branch can supersede an earlier one;
`git log` can't reveal that. Step 4 already lists "Stale prose left behind by code
changes" as a flag-and-fix item but prescribes **no mechanism**. This supplies it.

**Why now (B):** while auditing for trimmable prose, step 2's line 48 turned out to
restate, almost verbatim, the parent-side check-runner dispatch protocol that
`subagent-delegation` § "Heavy command output → check-runner" is the canonical home for
(see the duplication table in Approach). Three copies of one protocol drift; the bug in
(A) is itself a drift-between-copies failure, so fixing a sibling instance in the same
file is thematically coherent.

**Intended outcome:** step 4's descriptive "stale prose" bullet becomes an *actionable*
content-claim-verification bullet; step 2 defers the dispatch mechanics to
`subagent-delegation`; the file stays under its 200-line cap with no hook-policy change.

## Hard constraint — line cap (governs the edit shape)

`ready-for-review/SKILL.md` is **exactly 200 lines** (`awk 'END{print NR}'` = 200).
`check-skill-length.sh` applies the **200-line default** here (only `code-review` and
`plan-review` are on the 500-line list). The hook denies when `new > 200 AND new > old`,
so at the cap **any net line increase blocks the commit**. The edit must be **net ≤ 0**.

**The hook counts newlines, not characters.** This is the load-bearing subtlety:
- Shortening a long *single* physical line (48 is 1097 chars on one line; 50 is 220 on
  one line) frees **zero** newlines.
- Only deleting/merging whole wrapped lines, or replacing a multi-line block with fewer
  lines, changes the count.
- Conversely, a *replacement* written wrapped at ~60 cols where the original was one long
  line **adds** lines. The step-2 reference (B) MUST be authored as a single long line —
  matching the existing one-line references at line 39/153 — or it inflates the count.

## Trim audit (thorough pass — recorded for the reviewer)

- **True non-load-bearing content:** step 2's line 48 duplication of the dispatch protocol
  (fix B). Genuinely deletable. But as a single physical line it frees no newline by
  itself — the budget gain comes from dropping the *now-redundant* line 50 (inline
  exception, covered by the referenced section).
- **Condensable load-bearing prose (found, but not used):** the backtick-hygiene
  paragraph (120–127, 8 wrapped lines) states a ~5-line rule with verbose restatement and
  could be condensed without losing instruction. The final plan does **not** condense it —
  the budget closes via the 4-line bullet + Part B — so this stays as a noted-but-untaken
  option, avoiding churn to load-bearing prose.
- **Marginal:** example lists at 43–46 and 52–57 (~1 line each) — useful concrete
  guidance, not recommended for trimming.
- **Load-bearing, untouched:** preconditions, cumulative-diff recipe, marker/loop guards,
  PR-template heredoc, the HOOK_TEST_FIXTURE blocks (test-read — must not touch), and the
  coordination-step-preservation paragraph.

## Approach

Single file edited: `ready-for-review/SKILL.md`. `subagent-delegation/SKILL.md` and
`check-runner.md` are **referenced, not modified**.

### Part A — step 4 content-claim verification

1. **Replace the "Stale prose left behind by code changes" bullet** (lines 107–109) with
   a **Content-claim verification** bullet. **Locked final wording — 4 lines at the
   file's ~60-col wrap (keep ≤4; the budget math depends on it):**
   > - **Content-claim verification.** Read each file the body describes at
   >   its final state (clean tree = HEAD) and confirm its claims about that
   >   content (deployment order, feature names, step numbers) still match —
   >   a removed guard or deleted structure must be gone from the body too.

   Confirmed **general + examples** wording: three examples in a parenthetical, not an
   exhaustive list, so reviewers aren't anchored to only those patterns. No explanatory
   "why `git log` misses it" mechanics in the prose — "at its final state" carries the
   instruction; the rationale stays out (per reviewer note). The bullet *absorbs* the old
   bullet's cases (removed guard, abandoned approach, deleted file) — those are content
   claims that no longer match — so nothing is lost. The "clean tree = HEAD" assumption is
   backed by step 1's precondition and rechecked at step 6.

2. **Fold the "files listed in the body no longer in the diff" half** of the current
   final bullet (lines 111–112) into the verification bullet (a named-but-absent file is
   a content-claim mismatch). **Keep the reverse half** — "files in the diff absent from
   the body" — as its own one-line bullet, since that is an *omission*, not a stale claim.

**No backtick-hygiene condensation.** Earlier drafts condensed the backtick-hygiene
paragraph (120–127) to fund a 6-line bullet. With the bullet now 4 lines and Part B
freeing a line, the budget closes without it — so that paragraph is left untouched. This
removes the only load-bearing-prose churn the plan had carried.

### Part B — step 2 check-runner dedup

4. **Replace step 2's line 48** (the dispatch-protocol restatement) with a **single-line
   reference**, mirroring the line-39 style:
   > **Run the checks via `check-runner`, not inline Bash** — dispatch per
   > `subagent-delegation/SKILL.md` § "Heavy command output → check-runner": enumerate
   > the exact command strings identified above, pass `dispatch-id: <uuid>` and the
   > worktree's absolute path, and read the returned spool rather than re-running.

   Authored as one physical line (like line 39). The protocol details (slug rule,
   spool-path format, verdict fields, cache rationale) live in the referenced section and
   in `check-runner.md` — not re-stated here.

5. **Drop line 50** (the inline-exception paragraph): the referenced section already
   covers "single test file / test name stays inline" (subagent-delegation line 78), so
   it becomes redundant once step 2 points there. Frees one line.

   Step-2-specific content stays untouched: which commands to derive (43–46), scope-skip
   exceptions (52–57), pre-existing-failure handling (59–61), test-to-fit ban (63).

### Net-line arithmetic (gate is unforgiving — file is at exactly 200)

| Change | Lines |
|---|---|
| A1: stale-prose bullet 3 → content-claim bullet 4 | **+1** |
| A2: files-listed 2 → keep reverse half 1 | **−1** |
| B4: line 48 (1) → one-line reference (1) | **0** |
| B5: drop line 50 | **−1** |
| **Total** | **−1 → 199 lines** |

One line of headroom. The bullet (A1) must stay ≤4 wrapped lines and the B4 reference must
be a single physical line — both are displayed multi-line above but constrained as noted.
Confirm with `awk 'END{print NR}'` before commit; if it reads 200–201, the bullet or
reference wrapped wider than planned — re-tighten, do not touch backtick-hygiene. No hook
change.

### Why this shape (alternatives weighed)

- *New standalone first bullet for A* (the brief's literal proposal): adds net lines the
  cap forbids and stacks a parallel check beside the old descriptive one (compounding
  layers) instead of fixing the bullet that already nominally covered this.
- *Duplicate the dispatch protocol intentionally in step 2 (repo's "skills independently
  readable" stance)*: rejected for **this** content — the protocol is a complex recipe
  with a clear canonical owner (`subagent-delegation`), and the file **already** defers
  its other complex recipe (divergence detection) to `git-feature-branch-sync` by
  reference. A pointer here is the established same-file pattern, not a `_shared/`
  extraction. Short standalone rules still get duplicated; multi-element recipes with an
  owner get referenced.
- *Raise this skill's cap to 500*: changes hook policy to fit prose — out of scope and
  unnecessary now that B frees a line.

## Critical files

- `claude/.claude/skills/ready-for-review/SKILL.md` — **the only file modified.** Step 4
  flag-and-fix list (95–112) for Part A; step 2 (lines 48, 50) for Part B. The
  backtick-hygiene paragraph (120–127) and the HOOK_TEST_FIXTURE blocks in steps 0 and 8
  are **left untouched** (the test re-reads the fixtures verbatim).

**Reference targets (read, not edited):**
- `subagent-delegation/SKILL.md` § "Heavy command output → check-runner" (lines 61–89) —
  canonical parent-side dispatch protocol; the B4 reference resolves here.
- `check-runner.md` (lines 23–47) — the agent's own spool/verdict contract.

**Mirror:** the existing one-line cross-skill reference style at lines 39/153; the
flag-and-fix bullet style (category + parenthetical example + what-to-do).

## Verification

- `awk 'END{print NR}' claude/.claude/skills/ready-for-review/SKILL.md` ≤ 200 (target 198).
- `.venv/bin/pytest claude/.claude/` green — confirms no test broke (hook-alignment suite
  reads only the fixture blocks, untouched; verified during exploration).
- `.venv/bin/ruff check claude/.claude/` clean (no Python touched; sanity only).
- Reference resolves: `grep -n "Heavy command output" subagent-delegation/SKILL.md`
  returns the cited heading (verified now: line 61), and step 2 no longer restates the
  slug/spool/dispatch-id mechanics.
- Run `/skill-review` on the diff (hook-enforced for SKILL.md commits) and `/code-review`;
  address findings. Re-read both edited regions against the skill's brevity ethos.
- Manual read-through: step 2 reads coherently with the protocol deferred; the
  content-claim bullet reads naturally in the flag-and-fix list; the retained "files in
  the diff absent from the body" line stands alone.

## Out of scope

- The `/handoff` "preserve existing content" language — correct in general; the failure
  was applying it without the step-4 content check.
- A test for the new bullet — the suite covers hook behavior and structural fixtures, not
  skill-prose correctness (confirmed).
- Anything in the downstream project whose PR surfaced this — already fixed there.
- Raising the skill length cap / editing `check-skill-length.sh`.
- Editing `check-runner.md` or `subagent-delegation.md` — both are correct as-is and are
  the canonical homes; this change only makes ready-for-review defer to them.
- Any minor spool-path overlap between `subagent-delegation` and `check-runner.md` — they
  serve different audiences (parent dispatch vs agent self-contract); acceptable.

## Ship steps

Branch in `claude-config` (worktree per enforcement) → edit Part A + Part B → re-run
`/skill-review` + `/code-review` → `awk` line-count + pytest + ruff → commit → push →
open PR (describe both parts; note Part B as a related dedup, not scope creep).
**Pending engineer authorization:** merging the PR (do not self-merge — repo rule: an AI
agent that opens a PR does not merge it).
