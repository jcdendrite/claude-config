---
name: ready-for-review
description: >
  Pre-handoff gate: runs verification, code review, syncs or opens a
  PR. TRIGGER when: handing off to a human reviewer — wrapping up a
  branch with an open PR, "ship it" intent, or before spawning a
  multi-persona review (CISO + staff-* engineers) or /ultrareview.
  DO NOT TRIGGER when: during active iteration, on diff-only
  requests, or on the default branch.
argument-hint: "[optional PR context]"
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
- If no PR exists, step 5 will open one after verification and review.
- **Branch is in sync with `origin/<base>`.** Run the canonical detection recipe (see `git-feature-branch-sync/SKILL.md` § "Detecting divergence"). If behind > 0, invoke `/git-feature-branch-sync`, then re-run step 2 against the synced tree; step 8's completion marker must record the post-resync HEAD SHA so it matches what the push-gate hook checks.

## 2. Verification (halt on fail)

If the repo's CLAUDE.md has a Testing or Verification section, use those
commands. Otherwise inspect the config (`package.json`, `pyproject.toml`,
`go.mod`, `Cargo.toml`, `Makefile`, CI workflows) to identify the project's
test, lint, and typecheck commands. Do not invent — skip undefined steps.

**Run the checks inline** — per `subagent-delegation/SKILL.md` § "Heavy command output — run inline".

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
  CI placeholders (step 7 covers those). Keep items requiring
  reviewer judgment: deploy coordination, security-invariant catalog
  approval, architectural sign-off.
- **Content-claim verification.** Read each file the body describes at
  its final state (clean tree = HEAD) and confirm its claims about that
  content (deployment order, feature names, step numbers) still match —
  a removed guard or deleted structure must be gone from the body too.
- `TBD` / `pending` / "to be updated" markers still in the body.
- Files in the diff absent from the body.

Propose an updated body and apply with `gh pr edit <n> --body`. Keep
the project's template structure intact — refresh content inside
existing sections, don't restructure.

**Coordination-step preservation.** Before applying `gh pr edit <n> --body`, enumerate every action item the existing body carries — coordination steps, pre-deploy commands, manual external-system setup, sync workflows. For each, give it an explicit disposition: survive into the new body, answer-and-strip (Claude resolved it — see the "Reviewer-action items Claude can answer itself" bullet above), or strip-as-stale (no longer applies — see the "Stale prose" bullet above). Deliberate removal is fine; silent loss during a wholesale restructure is the failure mode.

**Backtick hygiene.** When constructing the new body via
`gh pr edit <n> --body "$(cat <<'EOF' … EOF )"`, write backticks
literally inside the heredoc. Do NOT write `\``. The single-quoted
delimiter (`'EOF'`) tells bash to preserve every character verbatim —
no expansion, no escape processing — so `\`` survives into the body and
breaks GitHub markdown code-span rendering. Backslash-escape backticks
only inside double-quoted strings or unquoted (`<<EOF`) heredocs where
they would otherwise trigger command substitution.

## 5. Create PR if missing (skip if PR already exists)

Skip if PR found in step 1. Halt if no remote tracking — "Branch is not pushed. Push with `git push -u origin <branch>` then re-run." TICKET-ID: split branch on `/`; if first segment matches `^[A-Za-z]+-[0-9]+$`, use as title prefix; else omit. Title: `<TICKET-ID>: <slug-hyphens-as-spaces>` ≤70 chars.
Body (omit `$ARGUMENTS` if empty; use single-quoted `<<'EOF'` heredoc; capture PR number for step 6). If `/code-review` returned a `## Deferred review findings` block at review time (≥1 DEFER, no open PR), assign it to `DEFERRED_FINDINGS` and it will be spliced in above; otherwise set `DEFERRED_FINDINGS` to empty string:
```
## Summary
$(git log $(git merge-base origin/$(gh pr view --json baseRefName --jq .baseRefName 2>/dev/null || echo main) HEAD)..HEAD --format="- %s")
$ARGUMENTS
## Test plan
- [ ] (fill in manual verification steps)
$DEFERRED_FINDINGS
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
## 6. Final hygiene recheck (halt on fail)

Steps 3 and 4 may have produced new commits or body edits. Reconfirm:

- Working tree is clean.
- All commits are pushed. If `git status` shows the branch ahead of
  `origin/<branch>` because steps 2/3 produced fix commits, push them
  now — those commits are inside the approved scope of this gate and
  the user does not need to re-authorize the push. After pushing,
  re-verify the branch is no longer ahead.
- PR body edit (if any) landed — re-fetch with `gh pr view` and confirm.
- Branch is not behind the base branch — if steps 3–5 produced new commits, re-run the divergence detection recipe (`git-feature-branch-sync/SKILL.md` § "Detecting divergence") before handing off.

## 7. CI status (warn only)

Run `gh pr checks <n>`:
- All green → continue.
- Still running → note the in-flight checks; user decides whether to wait.
- No checks reported (`gh pr checks` exits non-zero) → not yet registered; treat as pending.
- Red → surface failing check names with a one-line summary of each.
  Do not auto-halt — sometimes the human reviewer wants to see the
  failure themselves — but make the failure explicit before handoff.

## 8. Record gate completion + deactivate session

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

Removes only this session's file. If the skill errors before reaching this step, the gate will evict the orphan automatically once the session's process ends — the hook checks PID liveness on each gate hit.

**Do NOT write the completion marker if:**

- Any halt-on-fail step (1, 2, 3, 6) produced findings that weren't
  fixed in this session.
- The user asked you to present findings without finishing the gate.
- You are not in a git repository.
- The branch has no PR and no remote tracking (nothing to gate).

## Completion

Summarize for the user, then (and only then) signal that the branch is ready for human review:

- Verification: commands run and their results.
- Code review: findings fixed, or "none."
- PR description: sections updated, or "already in sync" / "no PR."
- CI: status per check.
- Branch: clean, pushed, PR #N ready for review.
