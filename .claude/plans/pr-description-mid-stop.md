# pr-description mid-run stops

## Context

Agents occasionally stop mid-way through a `/ready-for-review` (or `/handoff`) run right after the `pr-description` skill finishes, instead of continuing to the orchestrator's next step — the user's own gut-check put this at roughly 5% of runs. Why now: it's a recurring, if rare, friction point that erodes trust in the gate's autonomy, and it was never root-caused. Intended outcome: a targeted fix (or a documented decision not to fix, if unwarranted) that closes the specific gap found, without overreach into unrelated skill-transition risk.

A transcript investigation (via `transcript-analysis`) found `pr-description` is almost always invoked as an intermediate step — nested inside `ready-for-review` step 5, or `handoff`'s pre-write checklist — rather than run standalone. A small number of those nested invocations, rare relative to the total, showed the reported pattern: `pr-description` completed cleanly (a well-formed `BODY_FILE: <path>` line, not truncated or garbled — a copy/terminal artifact made it look garbled when the user pasted it into their report), but the assistant's turn ended there instead of continuing into the orchestrator's next step. The user had to prompt again ("why did you stop?"), and the agent's own diagnosis was accurate: it had finished one step of a longer sequence and should have continued straight into the next one. In every other mid-orchestration case observed, the turn continued on its own. Root cause: `pr-description`'s own success format is a deliberately terminal-looking single deliverable line (documented in its own SKILL.md as "extractable rather than transcribed"), and neither `ready-for-review` nor `handoff` ever tells the model explicitly that reaching that line is not the caller's own stopping point.

The user was asked how aggressively to enforce continuation, given the failure is real but rare (consistent with the user's own gut-check of roughly 5%): hook-enforced (a new `Stop` hook, mirroring this repo's existing `advance-past-commit-stall.sh` precedent), prose-only, or both. **The user chose prose-only** — no new hook, no marker, no change to `pr-description`'s own deliverable format.

## Approach

Add one sentence to each of `pr-description`'s two orchestrating callers stating that the skill's return is not the caller's stopping point, and pin both sentences with a regression test alongside the two existing invoke-pointer tests. Nothing in `pr-description` itself changes: its `BODY_FILE: <path>` line is a contract read by every consumer including the standalone invocation, where ending the turn is the correct behavior — the obligation to continue belongs to whoever owns the sequence, not to the callee, which cannot know who called it.

Both edits are written **in place on existing lines**, adding no new lines. This is forced, not stylistic: `claude/.claude/skills/ready-for-review/SKILL.md` sits at exactly 200 lines against `check-skill-length.sh`'s 200-line default cap, and that gate denies `git commit` whenever a staged SKILL.md both exceeds its cap and grew versus HEAD. A net-zero-line edit passes; a one-line addition trips both halves of the condition at once.

**Root problem:** `pr-description` is invoked inline via the Skill tool as an intermediate step, ends on a self-contained deliverable that reads as terminal, and neither caller's body says the caller's own work continues past that point — so the orchestrating turn can end there, leaving the PR uncreated or the handoff file unwritten.

**Givens:**

- **G1 — `pr-description`'s deliverable formats are fixed.** Author mode's single-line `BODY_FILE: <path>` is parsed by `ready-for-review` step 6 and documented as extractable-rather-than-transcribed; sync mode applies via `gh pr edit --body-file`. Both are the contract every consumer reads, including the standalone run where terminating there is correct — changing them is a different decision, made against a different set of consumers, than this one.
- **G2 — `check-skill-length.sh` owns the line budget.** It caps `ready-for-review/SKILL.md` and `handoff/SKILL.md` at the 200-line default and denies the commit on growth past cap. Raising a per-skill cap edits a committed repo hook this plan does not own and would need its own justification comment in `limit_for()`.
- **G3 — turn-ending is probabilistic.** Prose shifts the model's balance toward continuing; it cannot guarantee it. This is a property of the runtime, beyond any prose fix's reach, and it is the accepted residual of the prose-only choice.

**Rows:**

1. `[engineer-verified]` Prose-only: no Stop hook, no marker, no new mechanism of any kind. — `anchors: root`
2. `[verified: claude/.claude/skills/ready-for-review/SKILL.md, step 5 and step 6 bodies]` Step 5 names both `pr-description` endings and step 6 consumes step 5's reported path, but no text in either step says the turn continues; the file's only sequencing instruction is the top-of-file "Run steps in order." — `anchors: root`
3. `[verified: claude/.claude/skills/handoff/SKILL.md:152-174]` The pre-write checklist carries the identical shape. Its trigger requires an open PR, so `pr-description` there always resolves to sync mode and ends on an applied `gh pr edit`, never on a `BODY_FILE:` line — the handoff sentence must therefore not name `BODY_FILE:`. — `anchors: root`
4. `[verified: claude/.claude/skills/pr-description/SKILL.md:22,181-187]` The deliverable format is documented as a contract for all consumers and the standalone mode is explicitly supported, so the mechanism lives in the callers. — `anchors: G1`
5. `[verified: claude/.claude/hooks/check-skill-length.sh:71-90; wc -l and awk 'END{print NR}' run against both files this session]` `ready-for-review/SKILL.md` is exactly 200 lines — at its cap, not over it, and nothing about it is currently in violation: the gate's condition is `new > limit AND new > old`, and `200 > 200` is false. `handoff/SKILL.md` is 198. The consequence is unchanged: a one-line addition to `ready-for-review` makes `new=201`, satisfying both halves, so that edit must be net-zero-line. `handoff` has two lines of headroom but takes the same in-line form so both callers read identically. Re-check both counts immediately before staging — the gate compares the staged file against HEAD, so any other commit on this branch touching either file moves the baseline. — `anchors: G2`
6. `[verified: claude/.claude/skills/ready-for-review/SKILL.md, step 6 body]` Appending to the end of an existing hard-wrapped line is consistent with this file, which already carries a ~1,100-character single-line paragraph in step 6. Rewrapping step 5's whole paragraph to preserve an even column width was set aside: it produces an 8-line diff for one added sentence and forces the reviewer to diff-read reflowed text to find it. — `anchors: G2`
7. `[verified: claude/.claude/skills/tests/test_skills.py:416,474-480]` `TestConventionSkillWiring` already pins both invoke pointers with `self._skill_body(...)`; the continuation clauses get sibling tests in that same class. Prose is the entire mechanism here, so an unpinned sentence is one copy-edit from silent removal — and this repo runs a `tighten-prose` skill that shortens prose by design. — `anchors: row1`
8. `[verified: read of all four other sites]` Sibling audit found no other arm carrying this bug shape. The discriminator is a sub-skill invoked inline whose own spec ends it on a self-contained deliverable, with caller work remaining afterward:
   - `ready-for-review` step 1 (`/git-feature-branch-sync`) and step 3 (`/code-review`) already carry explicit bridges ("re-run step 2 against the synced tree", "return to step 2 and re-run fast checks"), and `code-review`'s Output format (SKILL.md:219-242) ends on a findings list or "No issues found" — no extractable single-line deliverable.
   - `ready-for-review` step 4 dispatches an agent via Task, not the Skill tool, and already names the post-return action ("`Read` the findings file after it returns").
   - `respond-pr/SKILL.md:125` re-runs `/pr-description` from a terminal Guidelines bullet, not a numbered step whose successor consumes its output.
   - `claude/.claude/CLAUDE.md:175` already states the continuation itself ("before moving on, because nothing re-reads the body for you").

   Do not add continuation prose to any of these — stacking a bridge at every step boundary is the compounding-defensive-layers smell, and it would breach G2's budget. — `anchors: root`
9. `[unverified]` The handoff arm has no observed failure in the investigated corpus; its fix is applied on shape match, per CLAUDE.md's sibling-audit rule ("scope follows the bug, not where it surfaced"), not on measurement. — `anchors: root`
10. `[verified: CHANGELOG.md:10 precedent]` Do **not** publish the observed failure rate in the CHANGELOG, PR body, or this plan file unless it is re-derived from a query explicitly scoped to this repo's own sessions (`--projects '*claude-config*'`, the form CHANGELOG.md:10 uses for its published figure). `transcript-analysis` scans the union of declared roots across accounts by default, so a figure from an unscoped run inherits whatever private-engagement sessions the corpus contained — CLAUDE.md's provenance rule, whose default is to strip. State the failure mode qualitatively instead. Likewise, do not cite the incident's worktree name, session id, or transcript path anywhere in the committed artifacts. — `anchors: root`
11. **Over-powered-primitive check:** the mechanism is one declarative sentence per caller, plus one assertion per sentence. No lighter primitive exists short of doing nothing, and the two heavier ones — a Stop hook and a marker handshake — were weighed and declined at Step 4. The check is satisfied by the mechanism being the floor, not by enumeration. — `anchors: row1`

## Critical files

Single `code-writer` dispatch. The four files are interdependent — the tests pin the exact strings written into the two skill bodies — so splitting them would require restating the same prose in two prompts.

1. **`claude/.claude/skills/ready-for-review/SKILL.md`** — modify line 114 in place, appending one sentence to the existing line (confirmed via direct read this session — verify with a fresh grep before editing in case an earlier commit on this branch already moved it). No line added. Resulting line 114:

   ```
   body to a temp file and ends its report with a `BODY_FILE: <path>` line. Neither ending is this gate's stopping point — continue to the next step in the same turn.
   ```

   "Neither ending" refers to the two endings the preceding sentence names, covering sync mode (`gh pr edit`) and author mode (`BODY_FILE:`) in one clause. "The next step" rather than "step 6" is deliberate: in sync mode step 6 is skipped and the next step is 7. "This gate" is the file's own established self-reference (§0 activation, step 6, step 8).

2. **`claude/.claude/skills/handoff/SKILL.md`** — modify line 170 in place (confirmed via direct read this session). No line added. Resulting line 170:

   ```
   - If this session pushed commits to a branch with an open PR and `/ready-for-review` did not run this session, run the `pr-description` skill before writing this file. Its report ends that skill, not this checklist — finish the remaining items and write the handoff file in the same turn
   ```

   No trailing period, matching the surrounding bullets in that list (several of which already carry internal sentences). Deliberately does not name `BODY_FILE:` — per ledger row 3, that mode is unreachable from this trigger.

3. **`claude/.claude/skills/tests/test_skills.py`** — add two tests to the existing `TestConventionSkillWiring` class (line 416), immediately after `test_handoff_runs_pr_description` (line 480). **Reuse** that class's `self._skill_body(...)` helper; do not add a module-level reader. Normalize whitespace inline in each assertion — `" ".join(self._skill_body("ready-for-review").split())` — so a future rewrap of either paragraph cannot break the pin, following `TestCodeWriterSelfReviewScope`'s `_body()` precedent at line 483. Assert on the fragments `continue to the next step in the same turn` and `write the handoff file in the same turn`. One-line docstring each, naming what the loss of the clause would cause.

4. **`CHANGELOG.md`** — one bullet under `## [Unreleased]` → `### Changed`. State the failure mode qualitatively per ledger row 10, name both callers, and say `pr-description`'s deliverable format is unchanged and no hook was added. Close with "Live on `git pull` with no re-install," matching the convention used by other `claude/.claude/**` entries.

5. **`.claude/plans/pr-description-mid-stop.md`** — this plan, committed per `plan-it` Step 7 (Critical files names files, so it is committed as provenance).

No update to `docs/skills.md:43` — that table row is a one-line role summary, and this is an implementation caveat that belongs in the skill bodies.

## Verification

The diff includes a `.py` file, so `/ready-for-review` step 2's no-executable-code scope exception does **not** apply — do not claim it.

1. **Line-budget guard, before staging.** Run the gate's own counting method, once per file — `awk 'END{print NR}'` against `claude/.claude/skills/ready-for-review/SKILL.md`, then against `claude/.claude/skills/handoff/SKILL.md` (two separate calls; `awk` given both files at once prints their combined total). Expected: 200 and 198, unchanged by the edit. A result of 201 for `ready-for-review` means a line was added and `check-skill-length.sh` will deny the commit. `wc -l` is an accurate proxy for both files as they stand today, since each ends with a trailing newline — but the two commands diverge by one if a trailing newline is ever dropped, and the gate reads `awk 'END{print NR}'`, so prefer `awk` when the count sits on the cap boundary as `ready-for-review`'s does.
2. **Tests:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented agent-facing command, which maps the touched skill and test paths to the suites that read them. Do not widen to the full suite by hand.
3. **Lint:** `.venv/bin/ruff check claude/.claude/` — a Python file changed. No `shellcheck` run; no shell file is touched.
4. **Reviews:** `/code-review` on the staged diff, which per `.claude/rules/review-pipeline-dispatch.md` also invokes `/skill-review` — hook-enforced by `require-skill-review.sh`, which blocks `git commit` until the behavioral-equivalence marker is written. This is the substantive verification for a skill-body change; the test run above only proves the pinned strings are present.
5. **Self-check on the review itself:** `/skill-review` runs against a diff to the skills that define the review pipeline. Per `.claude/rules/skill-and-agent-self-review.md`, confirm the two added sentences do not themselves violate the prose rules they sit among — one fact per sentence, no PR-defined terminology, readable by a contributor who never saw this PR.

## Out of scope

- **Any hook, marker, or `Stop`-hook mechanism.** Settled at Step 4; prose-only.
- **Changing `pr-description`'s deliverable format**, in either mode. Load-bearing for `ready-for-review` step 6's parse and for the standalone invocation (G1).
- **Trimming `ready-for-review/SKILL.md` below its cap.** The file sits exactly at 200 lines, so it passes the gate today but has zero headroom: the next contributor who needs to add a line there is blocked until someone first removes one. That is a real constraint on future work, but relieving it means cutting prose from a gate skill for reasons unrelated to this ticket — raise it to the reviewer as a follow-up rather than bundling it here.
- **Continuation prose at the other four `pr-description` invocation sites.** Audited in ledger row 8; the mechanism does not match at any of them.
- **A `docs/design-decisions.md` section recording why a Stop hook was declined.** The rejected alternative belongs in the commit message and PR body; a new numbered design-decisions section is heavier than a one-sentence-per-file change warrants.
- **Generalizing to skill-transition risk at large.** The fix closes the one shape the investigation established. A general "a sub-skill returning is never the caller's stopping point" rule in `claude/.claude/CLAUDE.md` would be a separate change with a much wider blast radius across all stow consumers, and it is not supported by evidence at any site other than the two fixed here.
