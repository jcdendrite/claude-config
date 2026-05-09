---
name: ready-for-review
description: >
  Pre-handoff gate for open PRs: verifies tests/lint/typecheck, runs
  /code-review on the full PR diff, syncs PR description, and checks CI.
  TRIGGER when handing off to a human reviewer — wrapping up work on a
  branch with an open PR, "ship it" intent, or before spawning a
  multi-persona review (CISO + staff-* engineers) or /ultrareview.
  DO NOT TRIGGER during active iteration, on diff-only requests,
  on the default branch, or when a project-specific pre-merge skill
  already wraps these checks.
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
~/.claude/scripts/marker.sh activate ready-for-review
```

If the chain fails (empty `SESSION_ID`), the `capture-session-id.sh` SessionStart hook didn't run — abort and report; the gate will block iteration pushes without this marker.

## 1. Preconditions (halt on fail)

- Current branch is not the default branch (`main` / `master` / `develop`).
- Working tree is clean: no unstaged or uncommitted changes.
- If a PR exists for the branch, capture its number and base:
  `gh pr view --json number,baseRefName`
- If no PR exists, note this — steps 4 and 5 will be skipped and a
  reminder surfaced at the end.

## 2. Verification (halt on fail)

If the repo's CLAUDE.md has a Testing or Verification section, use those
commands. Otherwise inspect the config (`package.json`, `pyproject.toml`,
`go.mod`, `Cargo.toml`, `Makefile`, CI workflows) to identify the project's
test, lint, and typecheck commands. Do not invent — skip undefined steps.

**Run the commands via the `Agent` tool with `subagent_type: check-runner`** — not inline Bash. Suite-level output displaces parent working state and invalidates the prompt cache. **Enumerate the exact command strings in the dispatch prompt** (e.g. "Run these commands: `pytest claude/.claude/`, `ruff check claude/.claude/`") — not "run the checks" or "run the suite". The subagent writes each command's full output to `${TMPDIR:-/tmp}/<slug>-<epoch-ms>.txt` (slug = command lowercased with non-alphanumeric runs collapsed to `-`, e.g. `npm test` → `npm-test`; epoch-ms via `date +%s%3N`), then returns: per-command name/exit code/pass-fail, smallest failure excerpt (last ~50 lines or runner summary), overall PASS or FAIL, and the output file paths. If the parent needs more detail, Read the file — do not re-run.

**Inline exception.** Run a command directly only when scoped to a single test file or test name — e.g. `npx vitest run src/components/X.test.tsx` or `pytest -k test_y`. Suite-level runs always go through the subagent.

**Scope exceptions — skip step 2 entirely:** skip when the diff
contains no executable code — only markdown, plans, or non-executable
config. Examples: skill bodies under `.claude/skills/**`, plans under
`.claude/plans/**`, agent configs under `.claude/agents/**`, top-level
`*.md`, `docs/**`. If the diff touches scripts, hooks, tests, or
application source — even alongside docs — run step 2.

**Pre-existing failures:** if a step fails on code unrelated to this diff, confirm it's
unrelated (`git log -- <file>`, `git diff origin/<base> -- <file>`), then either wait for
the existing owner or open a separate branch. Rebase once the default branch is green.

**Test-to-fit is forbidden:** fix the code, not the test — unless the product requirement genuinely changed.

## 3. Code review (halt on findings)

Run `/code-review` against the **cumulative** PR-vs-default-branch
diff — not staged changes, not per-commit deltas:

```bash
BASE_REF=$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null || echo main)
git diff $(git merge-base origin/$BASE_REF HEAD)...HEAD
```

The squash-merge artifact reviewers see is this diff; cumulative review surfaces cross-commit findings that per-commit review misses. Per-commit `/code-review` during iteration remains valuable — treat its findings as inputs here, not substitutes.

Because the reviewed diff is not the staged diff, do NOT write the
review-completion marker (per `/code-review`'s own rule). If findings
are produced, fix them in a new commit; that commit goes through the
normal staged-diff `/code-review` + marker gate, then return to
step 2 and re-run fast checks. Do not re-run `/code-review` on its
own output (loop risk).

Unskippable — markdown, skill, and config diffs benefit from the same pass.

## 4. Sync PR description (warn + fix; skip if no PR)

The PR description is for the reviewer, not for posterity. Compare
the body against branch state:

- `gh pr view <n> --json body,title`
- `git log <base>..HEAD --oneline`
- `git diff <base>..HEAD --name-only`

Flag and fix:

- **Per-commit narratives** ("Commit X did Y, Commit Z did W").
  Reorganize "What shipped" by surface the reviewer maps to (schema /
  handler / tests / invariants / migration-deploy notes); `git log`
  already has the chronology.
- **Reviewer-action items Claude can answer itself.** Strip claims
  you can verify ("all migrations match precedent" — confirm and
  remove), test counts (those belong in the commit message), and
  CI placeholders (step 5 covers those). Keep items requiring
  reviewer judgment: deploy coordination, security-invariant catalog
  approval, architectural sign-off.
- Stale prose left behind by code changes — a removed guard, an
  abandoned approach, a deleted file must also disappear from the
  migration-ordering note, test plan, and "remaining concerns" list.
- `TBD` / `pending` / "to be updated" markers still in the body.
- Files listed in the body that are no longer in the diff, or files
  in the diff absent from the body.

Propose an updated body and apply with `gh pr edit <n> --body`. Keep
the project's template structure intact — refresh content inside
existing sections, don't restructure.

**Backtick hygiene.** When constructing the new body via
`gh pr edit <n> --body "$(cat <<'EOF' … EOF )"`, write backticks
literally inside the heredoc. Do NOT write `\``. The single-quoted
delimiter (`'EOF'`) tells bash to preserve every character verbatim —
no expansion, no escape processing — so `\`` survives into the body and
breaks GitHub markdown code-span rendering. Backslash-escape backticks
only inside double-quoted strings or unquoted (`<<EOF`) heredocs where
they would otherwise trigger command substitution.

## 5. CI status (warn only; skip if no PR)

Run `gh pr checks <n>`.

- All green → continue.
- Still running → note the in-flight checks; user decides whether to wait.
- Red → surface failing check names with a one-line summary of each.
  Do not auto-halt — sometimes the human reviewer wants to see the
  failure themselves — but make the failure explicit before handoff.

## 6. Final hygiene recheck (halt on fail)

Steps 3 and 4 may have produced new commits or body edits. Reconfirm:

- Working tree is clean.
- All commits are pushed: `git status` shows the branch up to date
  with `origin/<branch>`, not ahead.
- PR body edit (if any) landed — re-fetch with `gh pr view` and confirm.

If the branch has no PR and no remote tracking, surface this — a project-specific pre-merge skill should open the PR; this skill does not.

## 7. Record gate completion + deactivate session

If every halt-on-fail step above passed, record the completed gate
and remove the active-session marker:

<!-- HOOK_TEST_FIXTURE: record-completion — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/ready-for-review/SKILL.md) to verify it matches require-ready-for-review.sh's completion-marker layout. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh write ready-for-review
```

Then remove the active-session marker:

<!-- HOOK_TEST_FIXTURE: deactivate-gate — the hook-alignment test suite reads this exact fenced block from this file (claude/.claude/skills/ready-for-review/SKILL.md) to verify it matches require-ready-for-review.sh's active-marker cleanup. Do not duplicate the recipe elsewhere; the test re-reads it from here. -->
```
~/.claude/scripts/marker.sh deactivate ready-for-review
```

Removes only this session's file; if the skill errors before this step, do not manually clean up — the hook's 60-minute staleness cutoff handles orphans.

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
