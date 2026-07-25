---
name: pr-description
description: >
  Author or sync the current branch's PR description. No open PR — draft the
  body to the standard here, check it, and report a file path for the caller
  to create the PR from. Open PR — fetch the body and verify it against
  branch state. Both modes read the body end to end for self-consistency,
  then flag per-commit narratives, stale content claims, TBD markers, and
  files missing from the body, and preserve coordination steps. Dispatched
  from /ready-for-review step 5 and the /handoff pre-write checklist; also
  run standalone.
---

# PR description

The PR description is for the reviewer, not for posterity.

Resolve the PR with `gh pr view --json number,body,title`, then take one of
two modes. Both run the same checks; the difference is where the body comes
from and where it goes.

- **No open PR → author mode.** Draft a body per "What the body must carry"
  below, run every check against the draft, write it to a file, and report
  that path. Do not create the PR — the caller owns PR lifecycle. Run
  standalone, that reported path *is* the deliverable; never no-op because
  there is no PR to edit.
- **Open PR → sync mode.** Fetch the body, run every check against it, and
  apply the result with `gh pr edit <n> --body-file <path>`.

## What the body must carry

- **What and why.** What change is being made — summarized so the reviewer
  gets its shape without reading the whole diff — and why: the context you
  had as the author, and decisions that are not visible in the source.
- **A first line that stands alone.** A reader skimming a list of PRs sees
  that line and nothing else; it has to carry the change by itself.
- **No list of commit subjects.** A bulleted `git log` of the branch is
  chronology, not a summary: it re-narrates how the work arrived instead of
  what it does, and it is the per-commit narrative the checks below exist to
  strip. Organize by the surface a reviewer maps to instead.
- **Section structure from the repo's template.** If the repo has
  `.github/PULL_REQUEST_TEMPLATE.md`, read it and use its headings — neither
  `gh pr create --body-file` nor `--body` applies the template, so it is
  honored only by reading it here. Absent one, use `## Summary` and
  `## Test plan`.
- **A `## Test plan` of results, not a checklist.** Verification has already
  run by the time this fires, so state what ran and what it produced, in past
  tense. If verification was skipped under a documented scope exception, say
  that the exception applied and that no executable-code verification ran.
  Never `- [ ]` items, never a placeholder prompt, never an empty section —
  and never fabricated results. A future-tense checklist for work already
  done is the heading-negates-its-own-body defect the coherence pass below
  flags.
- **The caller's context, folded in.** Text passed as `$ARGUMENTS` is the
  caller's own account of the change. Work it into the What/Why prose under
  the body's own headings — do not drop it, do not silently paraphrase it,
  and do not leave it sitting as an unlabeled block between sections. A bare
  block is exactly the span the coherence pass is told to flag.
- **The attribution trailer** as the last line of the body:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## Checks

Run every check below in both modes — against the draft in author mode,
against the fetched body in sync mode.

**Machine-managed blocks come out first.** A `## Deferred review findings`
section delimited by `<!-- code-review:deferred:start -->` /
`<!-- code-review:deferred:end -->` is located mechanically by later
`/code-review` runs and must survive byte-identical, delimiters included.
Lift the delimited span out before the coherence pass and reinsert it
verbatim afterward — left in place it is precisely the "what is this?" span
that pass is told to flag.

**Reader-coherence pass.** Before the pattern checks below, read the
body end to end as the reviewer will and answer: **does this document
make sense on its own?** Judge the body against itself — comparison
against branch state and file content comes after. Those checks are
pattern matches; none of them surfaces "this section contradicts
itself" or "this text should not be here at all."

Markers, illustrative rather than exhaustive:

- A heading whose own body negates it — a "why I skipped X" section
  whose text says X was not skipped.
- Two sections that contradict each other — a "no breaking changes"
  claim up top against a breaking change in the deploy notes.
- Leftover template instruction text: placeholder prompts the
  template's own directions said to remove once a condition holds.
- Any span a reader arriving cold would stop on and ask "what is this?"

If nothing fires after a careful read, say so — naming the sections
you read end to end. A bare negative cannot distinguish a coherent
body from a skipped pass.

Then compare the body against branch state:

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

## Delivering the body

The body reaches its consumer as a **file**, never as a shell argument.
Create it with `mktemp "${TMPDIR:-/tmp}/pr-body.XXXXXX"` — the random suffix
keeps concurrent runs in sibling worktrees from colliding, and the prefix
makes the file identifiable to whoever meets it later — then populate it with
the **`Write` tool**.

Do not build the body through a shell. Backticks and `$(...)` in authored
markdown are command substitutions the moment the shell parses them, and a
heredoc redirect (`cat > "$f" <<EOF`) is parsed exactly as `--body "$(cat
<<EOF)"` is — writing to a file does not by itself escape that. If a heredoc
is unavoidable, the single-quoted `<<'EOF'` delimiter is mandatory.

Write backticks literally either way. **Never write `` \` ``** to "escape"
one: nothing consumes that backslash, so it survives into the body and breaks
GitHub's code-span rendering. This holds for a `Write`-tool-authored file as
much as for a heredoc — the hazard is in the delivered bytes, not the shell.

Then, by mode:

- **Author mode:** end your report with the path on a line of its own, in
  the form `BODY_FILE: <path>`. The caller builds its `gh pr create` argument
  by reading that line out of an otherwise long report; a fixed single-line
  form makes the path extractable rather than transcribed.
- **Sync mode:** apply with `gh pr edit <n> --body-file <path>`.

Write the path out as literal text in both commands — not a `$VAR` holding
it. A redaction gate scans the file named by `--body-file` and resolves that
argument statically, so a shell variable is opaque to it and the call fails
closed.

When a body already exists, keep the project's template structure intact —
refresh content inside existing sections, don't restructure. Exception: a
section the template's own instructions say to delete once a condition holds
is meant to be removed, not emptied or annotated — give any action item
inside it a disposition first (see "Coordination-step preservation" below).

**Coordination-step preservation.** Before applying an edit to an existing body, enumerate every action item that body carries — coordination steps, pre-deploy commands, manual external-system setup, sync workflows. For each, give it an explicit disposition: survive into the new body — and when its own section is being deleted under the exception above, name the remaining section it moves to, since a survivor with no home is the silent loss in disguise — answer-and-strip (Claude resolved it — see the "Reviewer-action items Claude can answer itself" bullet above), or strip-as-stale (no longer applies — see the `TBD` / `pending` markers bullet above). Deliberate removal is fine; silent loss during a wholesale restructure is the failure mode.
