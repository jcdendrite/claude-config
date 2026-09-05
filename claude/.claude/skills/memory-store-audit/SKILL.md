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

## Step 2 — Classify each file inline

Switch to Opus first (`/model opus`) — nothing dispatches this step to a
pinned model, so reaching Opus-level reasoning here is on you.

Read each file from Step 1's inventory yourself, one at a time, with `Read`.
Step 1's inventory can span several projects: each file's verdict,
destination cell, and any proposed title or body text must draw solely on
that file, never on another file read earlier or later in Step 2's pass,
even when two files share a project, a theme, or a phrase.
Apply `ai-instruction-and-memory-files` §5's routing table and
anti-duplication heuristic fresh per file — re-read §5 for every
classification rather than relying on an earlier read in this session —
uniformly across all four memory types with no type-tag carve-out; also
consult CLAUDE.md, AGENTS.md, `.claude/rules/*.md`, and the repo's
`docs/*.md` as needed to answer §5's question: does this generalize into a
rule already covered elsewhere?

Produce one table, one row per file:

| Memory file | Candidate destination | Where that destination already covers it | Verdict |
|---|---|---|---|

Verdict is one of four:

- **migrate** — a genuine standing rule, merely living in the wrong place.
  The destination cell names the repo file (and section, where one
  exists) it belongs in.
- **delete on contact** — §5's anti-duplication heuristic fires: the
  destination cell names the CLAUDE.md/AGENTS.md/hook that already covers
  it.
- **keep** — earns its keep per §5 (user preference, feedback calibration
  with its *why*, time-sensitive project context, or an external-system
  pointer) and does not generalize into a rule any contributor should
  follow.
- **file as issue** — narrower than *migrate*, this verdict has five
  rules:
  - **Definition:** the memory records a workaround for, or a repeated
    correction of, behavior this repo's own tooling could enforce
    mechanically (a hook, a skill step, an agent frontmatter change), so
    the durable fix is a change to the tooling rather than one more line
    of prose telling a reader to remember.
  - **Scope:** use it only when no documentation change would close the
    gap, not merely when documenting it is inconvenient.
  - **Destination cell:** holds the proposed issue title. The
    covering-location cell states plainly that nothing covers it — that
    absence is the finding.
  - **Exclusion:** a candidate whose underlying gap is only reachable
    via, or evidenced by, private-project-specific content must not get
    this verdict. Downgrade it to *keep*, and note in the table that it
    needs manual filing by the engineer, because Step 4 files this
    verdict's proposed title and body to a **public** GitHub issue
    verbatim.
  - **Redaction:** before adding any *file as issue* row, generalize or
    strip private-project-identifying detail from the proposed title per
    this repo's own CLAUDE.md "Redact private-project-identifying
    content" rules.

Every verdict's "where that destination already covers it" cell must cite
a specific file and section a human can open and read in under a minute —
that citation is what Steps 4 and 6's per-item `AskUserQuestion` approval
checks before it acts. A verdict you cannot ground in an openable citation
is not ready to add to the table; keep looking or downgrade toward *keep*.

If a file's classification is genuinely ambiguous under §5's criteria,
say so in the table rather than guessing at a verdict, and surface that
ambiguity to the engineer instead of resolving it yourself.

Present the completed table to the engineer before proceeding.

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

`gh api` is deliberate: `deny-private-project-refs.sh` scans mutating `gh
api` calls but has no `gh issue` branch at all, so `gh issue create` would
file the same content unscanned. See that hook's own "Known gaps" section.

Before the first `gh api` call, verify cwd is actually inside the
`claude-config` checkout rather than assuming it from prose alone — this
skill's nudge fires in every session on the machine, so an ordinary
forgotten `cd` both files the issue against the wrong repo and silently
bypasses the redaction gate, which short-circuits outside a
`claude-config` remote:

```bash
git rev-parse --show-toplevel && git config --get remote.origin.url
```

Confirm the printed remote contains `claude-config` before proceeding; if
it doesn't, `cd` into the checkout first.

This repo is the only filing target; a harness or private-project gap
downgrades that row to *keep* plus a report line naming it for the
engineer to file by hand.

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
and the location Step 2 cited as already covering it. No batching
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

**What holds these gates.** No hook or marker enforces Steps 4 and 6's
`AskUserQuestion` pauses. Quarantine bounds a skipped deletion pause;
Step 4's pause is the only safety net on the filing path.

## Closing note

This skill owns the workflow, not the classification criteria:
`ai-instruction-and-memory-files` remains the single source of truth for
§5's routing table and heuristic, and the only path to the write-gate
marker. For the repo-side migrations Step 3 lands, run `/code-review`
before committing, same as any other change.
