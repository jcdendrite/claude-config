---
name: ready-for-review
description: >
  Pre-handoff gate: runs verification, code review, syncs or opens a
  PR. TRIGGER when: handing off to a human reviewer — wrapping up a
  branch, "ship it" intent, before a multi-persona review (CISO +
  staff-* engineers) or /ultrareview, or on any push to a branch with
  an open PR, mid-iteration included.
  DO NOT TRIGGER when: no push or gh pr ready is attempted, or on the
  default branch.
argument-hint: "[optional PR context]"
---

# Ready-for-review gate

Run steps in order. Halt on failures unless the step is marked **warn
only**. After fixes produced by step 3 or step 4, re-run
step 2 — do not re-run either on its own output.

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

- **Session is anchored in the branch's worktree.** Under worktree enforcement, confirm the working directory is this branch's linked worktree and not the main checkout — an unanchored session runs verification, writes review markers, and dispatches subagents against the main checkout on the default branch, so every check below passes against the wrong tree (and the next bullet fails with a misleading reason). Re-enter the worktree per `branch-management/SKILL.md` § "Anchor the session in the worktree", then restart this step.
- Current branch is not the default branch (`main` / `master` / `develop`).
- Working tree is clean: no unstaged or uncommitted changes.
- If a PR exists for the branch, capture its number and base:
  `gh pr view --json number,baseRefName`
- If no PR exists, step 5 authors the body and step 6 opens the PR from it,
  after verification and review.
- **Branch is in sync with `origin/<base>`.** Run the canonical detection recipe (see `git-feature-branch-sync/SKILL.md` § "Detecting divergence"). If behind > 0, invoke `/git-feature-branch-sync`, then re-run step 2 against the synced tree; step 9's completion marker must record the post-resync HEAD SHA so it matches what the push-gate hook checks.

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

## 4. Skill-procedural-fidelity review (halt on findings)

Check that skills this branch invoked were executed, not silently abbreviated —
run by an independent observer so a rationalization in the working session can't
wave the deviation through. List what the branch invoked:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
python3 ~/.claude/scripts/transcript-analysis.py skill-invocation \
  --branches "$BRANCH" --include-subagents
```

Omit `--projects` — its repo-scoped default is the minimization control keeping
other projects' skill names out of the output; passing one here is a bug.

- Empty list → state no skills were invoked on this branch and continue (an
  affirmative no-op, not a silent skip).
- Otherwise spawn `skill-fidelity-reviewer` **synchronously** with: the list; the
  **text** of step 3's cumulative diff (`git diff $(git merge-base
  origin/$BASE_REF HEAD)...HEAD` — text, not the range, since the agent has no
  `Bash`); the plan path if one exists; and `findings_path:
  agent-reviews/skill-fidelity-reviewer-<epoch>-<slug>.md` (same convention as
  `/code-review`). `Read` the findings file after it returns.

Name the pipeline's own skills **out of scope** in the prompt (the agent body
also excludes them) — `code-review`, `plan-review`, `ready-for-review`,
`skill-review`, `agent-review`, plus this run's still-executing invocations —
else the reviewer audits the gate running it.

**Halt on a silent-abbreviation finding.** The escape hatch is stating the
deviation with a rationale — a low bar. Fix findings in a new commit (normal
staged-diff `/code-review` + marker gate), then return to step 2; don't re-run
this step on its own output.

## 5. PR description (unconditional; warn + fix)

Invoke the `pr-description` skill via the Skill tool. It owns body content in
both directions and runs the same checks either way; the standard lives there,
not here — don't restate it.
Never skipped — markdown, skill, and config diffs benefit from the same pass.
Pass it this run's `$ARGUMENTS`, plus the `## Deferred review findings` block
if step 3's `/code-review` returned one (≥1 DEFER, no open PR). With a PR open
it applies the fix itself via `gh pr edit --body-file`; with none, it writes the
body to a temp file and ends its report with a `BODY_FILE: <path>` line.

## 6. Create PR if missing (skip if PR already exists)

Skip if PR found in step 1. Halt if no remote tracking — "Branch is not pushed. Push with `git push -u origin <branch>` then re-run." TICKET-ID: split branch on `/`; if first segment matches `^[A-Za-z]+-[0-9]+$`, use as title prefix; else omit. Title: `<TICKET-ID>: <slug-hyphens-as-spaces>` ≤70 chars.

The body is step 5's file; this step composes none of its own. Substitute step 5's reported path and the title derived above as **literal text** in one Bash call — write out the real path, not a `$VAR` holding it. `gh pr create --body-file` is scanned by a redaction gate that resolves the flag's argument statically; a shell variable is opaque to that scan, so it fails closed and refuses the call. Guard, then create: `[ -f "<path>" ] && [ -n "$(tr -d '[:space:]' < "<path>")" ] || { echo "step 5 produced no body — halting"; exit 1; }` and `gh pr create --title "<title>" --body-file <path>`. `-f` catches a missing path; `-s` alone would pass the whitespace-only file a truncated write leaves. Halting matters because an empty-bodied PR is unrepairable: step 5 takes its *sync* path once a PR exists, and sync checks a body against branch state rather than authoring one. Capture the PR number for step 7.

## 7. Final hygiene recheck (halt on fail)

Steps 3–6 may have produced new commits or body writes. Reconfirm:

- Working tree is clean.
- All commits are pushed. If `git status` shows the branch ahead of
  `origin/<branch>` because steps 2/3/4 produced fix commits, push them
  now — those commits are inside the approved scope of this gate and
  the user does not need to re-authorize the push. After pushing,
  re-verify the branch is no longer ahead.
- The PR body landed, whether step 5 edited it or step 6 created the PR from it — re-fetch with `gh pr view` and confirm.
- Branch is not behind the base branch — if steps 3–6 produced new commits, re-run the divergence detection recipe (`git-feature-branch-sync/SKILL.md` § "Detecting divergence") before handing off.

## 8. CI status (warn only)

Run `gh pr checks <n>`:
- All green → continue.
- Still running → note the in-flight checks; user decides whether to wait.
- No checks reported (`gh pr checks` exits non-zero) → not yet registered; treat as pending.
- Red → surface failing check names with a one-line summary of each.
  Do not auto-halt — sometimes the human reviewer wants to see the
  failure themselves — but make the failure explicit before handoff.

## 9. Record gate completion + deactivate session

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

- Any halt-on-fail step (1, 2, 3, 4, 7) produced findings that weren't
  fixed in this session.
- The user asked you to present findings without finishing the gate.
- You are not in a git repository.
- The branch has no PR and no remote tracking (nothing to gate).

## Completion

Summarize for the user, then (and only then) signal that the branch is ready for human review:

- Verification: commands run and their results.
- Code review: findings fixed, or "none."
- PR description: authored for a new PR, or updated / "already in sync."
- CI: status per check.
- Branch: clean, pushed, PR #N ready for review.
