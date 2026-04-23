---
name: ready-for-review
description: >
  Pre-handoff gate run before a human reviewer looks at an open PR:
  verifies tests/lint/typecheck, runs /code-review, syncs PR description
  against branch state, checks CI, and confirms tree hygiene.
  TRIGGER when: the user signals work is ready for their review ("ready
  for review", "ready for human review", "ready for your review", "ship
  it"); OR Claude is about to emit a handoff line such as "Ready for
  your review.", "PR #N is ready for your review", "PR #N is ready for
  merge", "the branch is ready for handoff", or "take a look when you're
  back" — run this skill first and do not emit the handoff line until
  it passes; OR before invoking /ultrareview or inviting external
  reviewers on a PR.
  DO NOT TRIGGER when: work is still being iterated, only a diff review
  or a single verification step was requested, on the default branch,
  or when a project-specific pre-merge-style skill already wraps these
  checks (let that skill delegate here instead).
argument-hint: "[optional scope note]"
---

# Ready-for-review gate

Run steps in order. Halt on failures unless the step is marked **warn
only**. After fixes produced by step 3, re-run step 2 — do not re-run
step 3 on its own output.

## 1. Preconditions (halt on fail)

- Current branch is not the default branch (`main` / `master` / `develop`).
- Working tree is clean: no unstaged or uncommitted changes.
- If a PR exists for the branch, capture its number and base:
  `gh pr view --json number,baseRefName`
- If no PR exists, note this — steps 4 and 5 will be skipped and a
  reminder surfaced at the end.

## 2. Verification (halt on fail)

Auto-detect verification commands from repo files — do not invent one.

- `package.json` → run whichever of `scripts.test`, `scripts.lint`,
  `scripts.typecheck` / `scripts.tsc` exist.
- `pyproject.toml` / `pytest.ini` / `setup.cfg` with pytest config →
  `pytest`; add `ruff check .` and/or `mypy` if configured.
- `go.mod` → `go test ./...` and `go vet ./...`.
- `Cargo.toml` → `cargo test`, `cargo clippy -- -D warnings`,
  `cargo fmt --check`.
- `Makefile` with `test` / `lint` / `check` targets → prefer those
  over raw commands.

If the repo's CLAUDE.md has an explicit Testing or Verification
section, that list wins over auto-detection.

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

Run `/code-review` over the full branch diff. Unskippable — markdown,
skill, and config diffs benefit from the same pass. Fix all findings,
then return to step 2 and re-run fast checks on the fixes. Do not
re-run `/code-review` on its own output (loop risk).

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

## Completion

Summarize for the user, then (and only then) signal that the branch
is ready for human review:

- Verification: commands run and their results.
- Code review: findings fixed, or "none."
- PR description: sections updated, or "already in sync" / "no PR."
- CI: status per check, or "no PR."
- Branch: clean, pushed, PR #N ready for review (or "push + open PR
  via project skill, then hand off").
