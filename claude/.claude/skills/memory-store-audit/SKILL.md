---
name: memory-store-audit
description: >
  Migrate-verify-delete workflow for auditing the machine's Claude Code
  auto-memory stores once they outgrow their per-session load budget.
  TRIGGER when: nudged by nudge-memory-store-audit.sh's additionalContext
  advisory, or asked to audit, prune, or clean up the Claude Code
  auto-memory store. DO NOT TRIGGER when: writing a new memory file or
  deciding which surface a rule belongs in — that is
  ai-instruction-and-memory-files' pre-write routing, not this skill's
  periodic audit.
---

## Step 1 — Inventory the store

Read-only. Scan the same path shape `nudge-memory-store-audit.sh` scans:
`<config-dir>/projects/*/memory/` (`<config-dir>` means `$CLAUDE_CONFIG_DIR`
when set, else `~/.claude`). Report each store's file count and byte total.

Project directory names surfaced by this step are local machine context
only. Never let one reach a PR body, commit message, or plan file — see the
repo's own `CLAUDE.md`, "Redact private-project-identifying content."

## Step 2 — Dispatch the classifier

Dispatch `memory-store-classifier` (Agent tool, explicit `model: opus` at
the dispatch site — CLAUDE.md requires the explicit pass even on a pinned
agent) with the file paths from Step 1's inventory, not their contents; it
reads each file itself. Wait for its returned table:

| Memory file | Candidate destination | Where that destination already covers it | Verdict |
|---|---|---|---|

Verdict is one of four: migrate / delete on contact / keep / file as issue.
The criteria are `ai-instruction-and-memory-files` §5's routing table and
anti-duplication heuristic, applied uniformly across all four memory types
with no type-tag carve-out — the classifier's own body carries the full
criteria and the four verdicts' definitions; do not re-derive them here.
Present the returned table to the engineer before proceeding.

## Step 3 — Land the durable artifact before any deletion

For every `migrate` verdict, land and commit the repo-side edit (CLAUDE.md,
AGENTS.md, a `.claude/rules/*.md` file, or the relevant `SKILL.md`) before
touching the memory file. For every `file as issue` verdict, hold it for
Step 4 — its durable artifact is the filed issue, not a commit. Memory files
are gitignored and unrecoverable once removed, so the lesson must survive an
abandoned PR either way.

## Step 4 — File approved issues

For each `file as issue` verdict, file it with:

```
gh api repos/{owner}/{repo}/issues -f title=… -f body=…
```

run with cwd inside the `claude-config` checkout. `gh api` is used for
consistency with `deny-private-project-refs.sh`'s other gated dispatch
surfaces, not because `gh issue create` is unsafe — both are
redaction-gated identically. This repo is the only filing target; a
harness or private-project gap downgrades that row to *keep* plus a report
line naming it for the engineer to file by hand.

One `AskUserQuestion` per issue, immediately before its `gh api` call,
showing the exact title, the exact body, and the target repo verbatim. No
batching and no approve-all. On a failed `gh api` call, report it to the
engineer naming which item failed and stop the loop — no automatic retry:
GitHub's Issues API has no idempotency key, so retrying a call that failed
after the server already created the issue risks a duplicate.

## Step 5 — Compression-diff audit before any deletion

Before deleting or shortening any file, fill `ai-instruction-and-memory-files`
§2's compression-diff table for it — cited by pointer, not restated here.
Any `N` in that table restores the dropped content instead of proceeding
with the deletion.

## Step 6 — Quarantine approved deletions

For each `delete on contact` verdict, one `AskUserQuestion` per file,
immediately before its move, naming the file, its byte size, its verdict,
and the location the classifier cited as already covering it. No batching
and no approve-all. On approval, move the file to
`<config-dir>/.memory-audit-quarantine/<original-filename>` — overwriting a
same-named file already there from a prior audit — rather than removing it;
a skipped or careless approval then costs a quarantined file, not an
unrecoverable one. On a "no" answer, leave the file untouched and continue
to the next item.

Pruning the file's `MEMORY.md` index line is a separate act with a
different gate: neither the quarantine move nor the topic-file case is
hook-gated (`require-memory-skill.sh` gates `Edit`/`Write`/`MultiEdit`
only), but the index-line edit is an `Edit` on `MEMORY.md` and still needs
an active `ai-instruction-and-memory-files` bypass marker. Two operational
constraints:

- Activate the marker as a standalone Bash call with nothing else in it:
  ```
  ~/.claude/scripts/marker.sh activate memory-skill
  ```
- Address memory paths in the `~/.claude/…` form, not any stow-folded
  repo-physical form a checkout might resolve to.

Deactivate the marker when the index edits for this audit are done:
```
~/.claude/scripts/marker.sh deactivate memory-skill
```

## Step 7 — Report

Summarize what moved (destination file per migration), what was filed (issue
title and number per filing), what was quarantined (and where), and what
earned its keep (kept files and why, per §5's heuristic). No marker write
beyond Step 6's own deactivate, and no handshake back to
`nudge-memory-store-audit.sh` — the nudge's own re-arm band is what re-fires
on the next genuine growth (see `docs/memory-audit-nudge.md`).

**What holds these gates.** Every `AskUserQuestion` pause in Steps 4 and 6
is an instruction to this skill's own session, not a hook or a marker —
nothing blocks a session that skips it. Quarantine, not the pause itself, is
what bounds a skipped pause's blast radius on deletion; no equivalent bound
exists for a skipped issue-filing pause, so Step 4's pause is the only
safety net on that path.

## Closing note

This skill owns the workflow, not the classification criteria:
`ai-instruction-and-memory-files` remains the single source of truth for
§5's routing table and heuristic, and the only path to the write-gate
marker. For the repo-side migrations Step 3 lands, run `/code-review`
before committing, same as any other change.
