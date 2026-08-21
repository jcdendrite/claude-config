# plan-review verdict states the plan file's full pathname

## Context

When `plan-review` finishes and delivers its verdict — the point at which
the human reviewer is asked to approve, revise, or reject the plan — the
verdict text names no file path, so a reviewer working from chat alone has
to hunt for which file under `.claude/plans/` the verdict is about. The user
asked for this directly: state the plan file's full pathname at verdict
time so it's trivial to find. This is a same-session doc-only fix to one
skill file.

## Approach

Add the plan file's full absolute path to `plan-review`'s Output format
section, in the same sentence that states the verdict — so the path
appears at the exact moment the human is asked to approve. No other file
needs the same information — `plan-it` presents `plan-review`'s output
verbatim rather than re-deriving a verdict of its own, and `ExitPlanMode`'s
approval path (harness plan mode) already shows the plan file's contents in
its own UI per `plan-it` Step 6, so it doesn't need a redundant path line.

**Alternatives considered:** Duplicating the same instruction into `plan-it`
Step 6 was rejected — `plan-it` doesn't compose its own verdict text, it
forwards `plan-review`'s, so the single edit covers both entry points and
avoids a second copy that could drift (CLAUDE.md's single-source-of-truth
rule).

### Assumption ledger

- **Root:** the plan-review verdict, as currently written, gives the human
  reviewer no file path to act on.
- **Given:** `plan-review` already resolves *which* plan file it's
  reviewing before Step 1 completes (existing Step 1 logic) — this plan
  only adds "make that path absolute and repeat it in the output," not new
  file-discovery logic. [verified: claude/.claude/skills/plan-review/SKILL.md Step 1]
- Row 1 — `plan-it`'s hand-off to a human outside harness plan mode relies
  entirely on `plan-review`'s rendered output, with no separate
  presentation text of its own. [verified: claude/.claude/skills/plan-it/SKILL.md Step 6]
- Row 2 — Harness plan mode's `ExitPlanMode` approval UI already renders
  the plan file's contents, so it doesn't need the path restated.
  [verified: claude/.claude/skills/plan-it/SKILL.md Step 6, "the harness shows the plan file's contents in the approval UI"]

## Critical files

- `claude/.claude/skills/plan-review/SKILL.md`
  - Output format section, closing verdict sentence — the only edit;
    `skill-review` flagged a companion sentence originally drafted for
    Step 1 as redundant with this one and it was dropped (see Approach):

    Before:
    ```
    End with a verdict: **Approve**, **Approve with changes** (list what), or **Request changes** (list blockers).
    ```
    After:
    ```
    End with the plan file's full absolute path, then a verdict: **Approve**, **Approve with changes** (list what), or **Request changes** (list blockers).
    ```

No other file changes — `REFERENCES.md`/`ROUTING.md` in the same skill
directory contain no verdict-format or path-related text to reconcile
(checked via grep, no matches).

## Verification

Re-read the edited `SKILL.md` end to end: the instruction is unambiguous
about *which* path (absolute, not repo-relative) and *where* it must
appear (immediately with the verdict, not buried earlier in the output).
No automated test covers skill prose; `skill-review` (run per this repo's
skill-self-review rule) is the check that the wording change doesn't
regress voice, length, or duplication.

## Out of scope

None observed.
