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

- **No open PR → author mode.** Draft a body per "What the body must carry" below, run every check against the draft, write it to a file, and report that path. Do not create the PR — the caller owns PR lifecycle. Run standalone, that reported path *is* the deliverable; never no-op because there is no PR to edit.
- **Open PR → sync mode.** Fetch the body, run every check against it, and
  apply the result with `gh pr edit <n> --body-file <path>`.

## Load project-specific layer

If a project-specific layer exists for this skill, load it now. Glob for
`.claude/skills/pr-description-*/SKILL.md` from the repo root (resolved via
`git rev-parse --show-toplevel`); if exactly one matches, read it with the
Read tool and merge its check items into the Checks section below. If
multiple match, list them and stop — that's a config error in the project,
not something you can resolve. If none match, proceed without a layer.

## What the body must carry

- **What and why.** What change is being made — summarized so the reviewer
  gets its shape without reading the whole diff — and why: the context you
  had as the author, and decisions that are not visible in the source.
- **A first line of prose that stands alone.** The first line of prose, read after the attribution trailer below, is what a reader skimming a list of PRs sees; it has to carry the change by itself.
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
- **The attribution trailer**, at both the top and the bottom of the body — the first line (blank line, then the rest of the body) and the last line: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## Cost section

Machine-managed, delimited by `<!-- pr-cost:start -->` / `<!-- pr-cost:end -->` — regenerated fresh every sync, never reinserted verbatim (contrast `## Deferred review findings` below).

Gate: resolve the config dir as `$CLAUDE_CONFIG_DIR` when it is set **and absolute**, else `$HOME/.claude` — a relative `$CLAUDE_CONFIG_DIR` is invalid, not a cwd-relative path, and disables the section. Read `<config-dir>/pr-cost-disclosure`, trim leading and trailing whitespace only, lowercase via `tr '[:upper:]' '[:lower:]'`. Exactly `dollars` → regenerate the block. Anything else — absent, unreadable, empty, interior whitespace, a second line, any other value — delete the block if one exists. An unreadable sentinel counts as absent (delete the block, don't leave it alone) — no third, indeterminate outcome. Resolve exactly one path ($CLAUDE_CONFIG_DIR xor $HOME/.claude) — checking both would leak one account's opt-in into another, since the sentinel is per-account.

```bash
~/.claude/scripts/pr-cost-section.sh
```

Exit 0: enabled and the branch resolved cleanly — stdout is the cost report; embed it **verbatim** under
`## Cost`, followed by the exact command `~/.claude/scripts/pr-cost-section.sh` as "the exact command
that produced it" for reproducibility — never recompose, round, or re-narrate the figures. Exit 1:
disabled, unreadable, or malformed `<config-dir>/pr-cost-disclosure` — delete the block if one exists,
no stdout. Exit 2: enabled but the branch is the literal `HEAD` (detached) — omit the section and say
why, no stdout. Exit 3: branch resolved but the downstream cost report itself failed — omit `## Cost`
and note in the body that the report failed to generate, unlike exit 1's silent deletion. The sentinel
check (`<config-dir>/pr-cost-disclosure`, trimmed and lowercased, exactly `dollars`) is per Claude
account, not per repo: cost is an organizational fact, and each account is its own billing entity.
Resolves that one config-dir path only — never unions it with `$HOME/.claude`, or one account's opt-in
would activate disclosure under another; call the script once, it performs the sentinel check internally.
**One deliberate narrowing:** a sentinel consisting of a blank line followed by `dollars` reads as
two lines and is judged disabled, where a whitespace-collapsing read would have judged it enabled
— in the direction this gate already prefers (under-disclosing over guessing). Session/turn counts
and per-model-ID dollars are not neutral — they signal engagement scale and model mix. That is the
intended read under an account that opted in; it is not a property of the output format, and an
account enabling this for one engagement should not assume the fields are harmless in another.

## Prose tightening pass

Gate: resolve `config_dir` exactly as the Cost section's gate above; skip the pass if `$config_dir/pr-description-tighten-prose-optout` exists (any content, or none), else dispatch `tighten-prose` by name against the drafted body file, leaving the `## Cost` / `## Deferred review findings` blocks and the attribution trailer untouched (its own carve-out rule already protects code spans, headings, identifiers, and file paths). Run it after `$ARGUMENTS` is folded in and before `## Checks`, so `## Checks` validates the final tightened bytes, not pre-rewrite text.

## Checks

Run every check below in both modes — against the draft in author mode,
against the fetched body in sync mode.

**Machine-managed blocks come out first.** A `## Deferred review findings`
section delimited by `<!-- code-review:deferred:start -->` /
`<!-- code-review:deferred:end -->` is located mechanically by later
`/code-review` runs and must survive byte-identical, delimiters included.
Lift the delimited span out before the coherence pass and reinsert it
verbatim afterward — left in place it is precisely the "what is this?" span
that pass is told to flag. A `## Cost` section (`<!-- pr-cost:start -->` /
`<!-- pr-cost:end -->`, "Cost section" above) gets the same lift-out
treatment but not the same reinsert rule, stated here rather than left to
proximity: it regenerates fresh every sync, never reinserted verbatim.

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
- **External-state claims.** **Content-claim verification** covers files in this repo; this covers state outside it — most often a follow-up ticket said to be pending, or promised as `will create`. Re-check each claim at its own source, then rewrite it to its current truth value; carry an identifier across only where the body already names that tracker — never invent a fresh reference for a claim the body didn't already make. Whether CI is *wired up* is such a claim; whether CI is *passing* is not — that is stripped under **Reviewer-action items Claude can answer itself**.
- `TBD` / `pending` / "to be updated" markers still in the body.
- Files in the diff absent from the body.
- **Missing or duplicated attribution trailer.** Older bodies may carry the trailer only at the bottom — add it as the first line too. Never end up with more than one copy at either position.

## Delivering the body

The body reaches its consumer as a **file**, never as a shell argument.
Create it with `mktemp "${TMPDIR:-/tmp}/pr-body.XXXXXX"` — the random suffix
keeps concurrent runs in sibling worktrees from colliding, and the prefix
makes the file identifiable to whoever meets it later — then populate it with
the **`Write` tool**.

Author the body with the Write tool, not a shell heredoc — an unquoted
`<<EOF` parses embedded backticks/`$(...)` as command substitution exactly
like `--body "$(cat <<EOF)"` does, so use `<<'EOF'` if a heredoc is
unavoidable.

Write backticks literally — a backslash-escaped backtick survives into the
body unconsumed and breaks GitHub's code-span rendering, whether authored via
Write tool or heredoc.

Then, by mode:

- **Author mode:** end your report with the path on a line of its own, in
  the form `BODY_FILE: <path>`. The caller builds its `gh pr create` argument
  by reading that line out of an otherwise long report; a fixed single-line
  form makes the path extractable rather than transcribed.
- **Sync mode:** apply with `gh pr edit <n> --body-file <path>`.

Write the path as literal text in both commands, not a `$VAR` — the
redaction gate resolves `--body-file` statically, so a variable is opaque to
it and the call fails closed.

When a body already exists, keep the project's template structure intact —
refresh content inside existing sections, don't restructure. Exception: a
section the template's own instructions say to delete once a condition holds
is meant to be removed, not emptied or annotated — give any action item
inside it a disposition first (see "Coordination-step preservation" below).

**Coordination-step preservation.** Before applying an edit to an existing body, enumerate every action item that body carries — coordination steps, pre-deploy commands, manual external-system setup, sync workflows. Give each action item a disposition: survive (name its new section if the old one is deleted), answer-and-strip (Claude resolved it — see the "Reviewer-action items Claude can answer itself" bullet above), or strip-as-stale (no longer applies — see the `TBD` / `pending` markers bullet above). Deliberate removal is fine; a survivor left with no home is not.
