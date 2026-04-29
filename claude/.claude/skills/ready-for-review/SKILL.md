---
name: ready-for-review
description: >
  Pre-handoff gate run before a human reviewer looks at an open PR:
  verifies tests/lint/typecheck, runs /code-review against the
  cumulative PR-vs-default-branch diff, syncs PR description against
  branch state, checks CI, and confirms tree hygiene.
  TRIGGER when: (a) the user signals work is reviewer-ready, OR Claude
  is about to summarize what landed and implicitly hand back — fire on
  the *intent* (work-is-done-for-now-and-the-user-is-the-next-actor),
  not on specific phrases. Examples that should fire: "ready for
  review", "ship it", "PR is up", "take a look", "your turn", "PR is
  now full-scope X", "implementation pushed and reviewable"; (b) Claude
  has just pushed (or is about to push) commits to a branch that
  already has an open PR and is wrapping up rather than continuing to
  iterate; (c) before spawning a multi-persona ripple review (CISO +
  multiple staff-* engineers) via the Agent tool on a PR-stage diff
  outside an active /ready-for-review or /pre-merge flow, or before
  invoking /ultrareview.
  DO NOT TRIGGER when: work is still being iterated (more commits
  planned before handoff); only a diff review or single verification
  step was requested; on the default branch; or when a project-specific
  pre-merge-style skill already wraps these checks (let that skill
  delegate here instead).
argument-hint: "[optional scope note]"
---

# Ready-for-review gate

Run steps in order. Halt on failures unless the step is marked **warn
only**. After fixes produced by step 3, re-run step 2 — do not re-run
step 3 on its own output.

## 0. Activate gate session

Write the active-session marker so this skill's own iteration pushes
(step 3 fix → push → loop back to step 2) are not self-blocked by
the `require-ready-for-review.sh` hook:

<!-- HOOK_TEST_FIXTURE: activate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/ready-for-review/SKILL.md) to verify it matches require-ready-for-review.sh's active-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID") && [ -n "$SESSION_ID" ] && mkdir -p "$HOME/.claude/.ready-for-review-active.d" && touch "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
```

If the chain fails (empty `SESSION_ID`, etc.), the
`capture-session-id.sh` SessionStart hook didn't run — abort and
report; do not proceed without the marker, since the gate would
block iteration pushes.

## 1. Preconditions (halt on fail)

- Current branch is not the default branch (`main` / `master` / `develop`).
- Working tree is clean: no unstaged or uncommitted changes.
- If a PR exists for the branch, capture its number and base:
  `gh pr view --json number,baseRefName`
- If no PR exists, note this — steps 4 and 5 will be skipped and a
  reminder surfaced at the end.

## 2. Verification (halt on fail)

If the repo's CLAUDE.md has an explicit Testing or Verification
section, run exactly what it specifies.

Otherwise, inspect the repo's config (`package.json` scripts,
`pyproject.toml`, `go.mod`, `Cargo.toml`, `Makefile`, CI workflow
files, etc.) and run the test, lint, and typecheck commands the
project actually defines. Do not invent or guess commands — if the
project doesn't define a lint step, don't add one.

**Scope exceptions — skip step 2 entirely:** skip when the diff
contains no executable code — only markdown, plans, or non-executable
config. Examples: skill bodies under `.claude/skills/**`, plans under
`.claude/plans/**`, agent configs under `.claude/agents/**`, top-level
`*.md`, `docs/**`. If the diff touches scripts, hooks, tests, or
application source — even alongside docs — run step 2.

**Pre-existing failures on the default branch.** If a verification
step fails with issues unrelated to this branch's diff, do not bundle
the fix. Confirm it's unrelated (`git log -- <file>` and
`git diff origin/<default> -- <file>`), then either wait for the
existing owner or open a separate branch + PR for the fix. Rebase this
branch once the default branch is green again.

**Test-to-fit is forbidden.** If a test fails because of this branch's
change, fix the code — not the test — unless the product requirement
genuinely changed.

## 3. Code review (halt on findings)

Run `/code-review` against the **cumulative** PR-vs-default-branch
diff — not staged changes, not per-commit deltas:

```bash
BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null || echo main)
git diff $(git merge-base origin/$BASE_REF HEAD)...HEAD
```

The squash-merge artifact reviewers ultimately see is this diff.
Cumulative review surfaces cross-commit findings — defense-in-depth
gaps spanning two commits, deprecations exposed only when a new test
exercises an unmodified call site, idempotency-key shape mismatches
across modules — that per-commit review misses. Per-commit
`/code-review` during iteration remains valuable for fast feedback;
treat its findings as inputs here, not substitutes.

Because the reviewed diff is not the staged diff, do NOT write the
review-completion marker (per `/code-review`'s own rule). If findings
are produced, fix them in a new commit; that commit goes through the
normal staged-diff `/code-review` + marker gate, then return to
step 2 and re-run fast checks. Do not re-run `/code-review` on its
own output (loop risk).

Unskippable — markdown, skill, and config diffs benefit from the
same pass.

## 4. Sync PR description (warn + fix; skip if no PR)

Compare the PR body against branch state:

- `gh pr view <n> --json body,title`
- `git log <base>..HEAD --oneline`
- `git diff <base>..HEAD --name-only`

Flag and fix:

- `TBD` / `pending` / "to be updated" markers still in the body.
- Commits on the branch not reflected in the Summary section.
- Files listed in the body that are no longer in the diff, or files
  in the diff absent from the body.
- Stale line counts, screenshots of removed UI, references to approaches
  that were abandoned during iteration.

Propose an updated body and apply with `gh pr edit <n> --body`. Keep
the project's template structure intact — refresh content inside
existing sections, don't restructure.

## 5. CI status (warn only; skip if no PR)

Run `gh pr checks <n>`.

- All green → continue.
- Still running → note the in-flight checks; user decides whether to
  wait.
- Red → surface failing check names with a one-line summary of each.
  Do not auto-halt — sometimes the human reviewer wants to see the
  failure themselves — but make the failure explicit before handoff.

## 6. Final hygiene recheck (halt on fail)

Steps 3 and 4 may have produced new commits or body edits. Reconfirm:

- Working tree is clean.
- All commits are pushed: `git status` shows the branch up to date
  with `origin/<branch>`, not ahead.
- PR body edit (if any) landed — re-fetch with `gh pr view` and confirm.

If the branch has no PR and no remote tracking, surface this: the
human can't review what isn't pushed. A project-specific pre-merge or
PR-creation skill should handle the actual open; this skill does not
create PRs.

## 7. Record gate completion + deactivate session

If every halt-on-fail step above passed, record the completed gate
and remove the active-session marker:

<!-- HOOK_TEST_FIXTURE: record-completion — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/ready-for-review/SKILL.md) to verify it matches require-ready-for-review.sh's completion-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID") && [ -n "$SESSION_ID" ] && mkdir -p "$HOME/.claude/ready-for-review-markers" && REPO_HASH=$(git rev-parse --show-toplevel | tr -d '\n' | sha256sum | awk '{print $1}') && git rev-parse HEAD > "$HOME/.claude/ready-for-review-markers/$REPO_HASH.$SESSION_ID"
```

Then remove the active-session marker:

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/ready-for-review/SKILL.md) to verify it matches require-ready-for-review.sh's active-marker cleanup. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
SESSION_ID=$(cat "$HOME/.claude/sessions/$PPID" 2>/dev/null) && [ -n "$SESSION_ID" ] && rm -f "$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
```

Removes only this session's file. If the skill errors out before
reaching this step, don't manually clean up — the hook's 60-minute
staleness cutoff handles the orphan automatically.

**Do NOT write the completion marker if:**

- Any halt-on-fail step (1, 2, 3, 6) produced findings that weren't
  fixed in this session.
- The user asked you to present findings without finishing the gate.
- You are not in a git repository.
- The branch has no PR and no remote tracking (nothing to gate).

## Completion

Summarize for the user, then (and only then) signal that the branch
is ready for human review:

- Verification: commands run and their results.
- Code review: findings fixed, or "none."
- PR description: sections updated, or "already in sync" / "no PR."
- CI: status per check, or "no PR."
- Branch: clean, pushed, PR #N ready for review (or "push + open PR
  via project skill, then hand off").
