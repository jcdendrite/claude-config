---
name: sync-pr-description
description: >
  Verify and sync the current branch's open PR description against branch
  state — flag per-commit narratives, stale content claims, TBD markers,
  and files missing from the body; preserve coordination steps; apply the
  fix with gh pr edit. Dispatched from /ready-for-review step 4 and the
  /handoff pre-write checklist; also run standalone.
---

# Sync PR description

Precondition: resolve the PR with `gh pr view --json number,body,title`.
If the branch has no open PR, report "no PR to sync" and stop.

The PR description is for the reviewer, not for posterity. Compare
the body against branch state:

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
  CI placeholders (CI status is `/ready-for-review`'s job —
  `gh pr checks`). Keep items requiring reviewer judgment: deploy
  coordination, security-invariant catalog approval, architectural
  sign-off.
- **Content-claim verification.** Read each file the body describes at
  its final state (clean tree = HEAD) and confirm its claims about that
  content (deployment order, feature names, step numbers) still match —
  a removed guard or deleted structure must be gone from the body too.
- `TBD` / `pending` / "to be updated" markers still in the body.
- Files in the diff absent from the body.

Propose an updated body and apply with `gh pr edit <n> --body`. Keep
the project's template structure intact — refresh content inside
existing sections, don't restructure.

**Coordination-step preservation.** Before applying `gh pr edit <n> --body`, enumerate every action item the existing body carries — coordination steps, pre-deploy commands, manual external-system setup, sync workflows. For each, give it an explicit disposition: survive into the new body, answer-and-strip (Claude resolved it — see the "Reviewer-action items Claude can answer itself" bullet above), or strip-as-stale (no longer applies — see the `TBD` / `pending` markers bullet above). Deliberate removal is fine; silent loss during a wholesale restructure is the failure mode.

**Backtick hygiene.** When constructing the new body via
`gh pr edit <n> --body "$(cat <<'EOF' … EOF )"`, write backticks
literally inside the heredoc. Do NOT write `\``. The single-quoted
delimiter (`'EOF'`) tells bash to preserve every character verbatim —
no expansion, no escape processing — so `\`` survives into the body and
breaks GitHub markdown code-span rendering. Backslash-escape backticks
only inside double-quoted strings or unquoted (`<<EOF`) heredocs where
they would otherwise trigger command substitution.
