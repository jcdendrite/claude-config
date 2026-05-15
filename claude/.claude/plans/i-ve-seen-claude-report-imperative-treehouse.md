# Fix stale CI-status reports in `ready-for-review`

## Context

Claude has reported CI as green during the `ready-for-review` gate when CI
was actually pending. Root cause is step ordering in
`claude/.claude/skills/ready-for-review/SKILL.md`:

- **Step 5 ("CI status")** runs `gh pr checks <n>`, which queries GitHub and
  therefore only sees the **last-pushed** commit.
- **Step 6 ("Final hygiene recheck")** runs afterward and is where fix
  commits from steps 2/3 actually get pushed.

So when steps 2/3 produce a fix commit, step 5 reports CI for the pre-fix
HEAD, step 6 pushes the fix commit, and the Completion summary echoes step
5's now-stale result. Observed: step 5 saw `11ef241` green, step 6 pushed
`e1acc31`, gate reported "CI green" while `e1acc31` was still pending.

The check runs at the wrong time. The fix is to run it once, at the right
time — after the branch is in its final pushed state — not to check early
and then warn that the early result might be stale.

## Change

Prose-only edit to `claude/.claude/skills/ready-for-review/SKILL.md`:
**relocate the CI-status check to run after the final push.** No script or
hook changes; the `HOOK_TEST_FIXTURE` blocks (steps 0 and 7) move with their
content but are not modified.

New step order (sequential renumber — only the CI step relocates):

| New | Was  | Step                          |
|-----|------|-------------------------------|
| 1   | 1    | Preconditions                 |
| 2   | 2    | Verification                  |
| 3   | 3    | Code review                   |
| 4   | 4    | Sync PR description           |
| 5   | 5.5  | Create PR if missing          |
| 6   | 6    | Final hygiene recheck (pushes)|
| 7   | 5    | **CI status**                 |
| 8   | 7    | Record completion + deactivate|

Because step 7 now runs after every push, `gh pr checks` always reflects the
pushed HEAD — no "stale result" concept needed.

Relocated step 7 body (the only content change is the framing line and one
added outcome bullet for the post-push registration race):

> ## 7. CI status (warn only)
>
> The branch is now in its final pushed state. Run `gh pr checks <n>`.
>
> - All green → continue.
> - Still running → note the in-flight checks; user decides whether to wait.
> - No checks reported (`gh pr checks` exits non-zero) → CI has not
>   registered a run for the pushed commit yet. Report as pending.
> - Red → surface failing check names with a one-line summary of each.
>   Do not auto-halt — sometimes the human reviewer wants to see the
>   failure themselves — but make the failure explicit before handoff.

The fourth bullet is required: immediately after step 6's push, CI for the
new commit may not be registered yet and `gh pr checks` exits non-zero — that
must read as pending, never as green. It uses the same `→` style as the
three existing bullets in this step.

Reference fix-ups from the renumber:

- Step 1: "step 5.5 will open one" → "step 5 will open one".
- Step 6: "PR #N (created in step 5.5 if newly opened)" → "step 5".
- Step 8: "halt-on-fail step (1, 2, 3, 6)" — step 6's number is unchanged,
  so this reference stays correct.
- Old step 5's "skip if no PR" caveat is dropped: step 5 (create PR) now
  guarantees a PR exists before step 7 runs.

## Files

- `claude/.claude/skills/ready-for-review/SKILL.md` — relocate + renumber
  steps 5–7; reference fix-ups in steps 1 and 6.

## Verification

- Per repo CLAUDE.md ("When editing a skill, run the skill on its own
  diff"): invoke `/skill-review` on the staged diff; confirm no findings.
- Read steps 1–8 in sequence and confirm: every push completes before the
  CI check; no step-number reference is dangling.
- Confirm the two `HOOK_TEST_FIXTURE` fenced blocks are byte-identical to
  before (the hook-alignment test re-reads them from this file).
- Run the test suite: `pytest claude/.claude/` and
  `ruff check claude/.claude/`.
