---
name: brief
description: Produce a cold-start task briefing at /tmp/<slug>-task.md for a fresh session to pick up known, well-scoped work (abandoned PR, surfaced follow-up, ticket whose scope is settled). Distinct from /handoff, which captures mid-flight session state.
disable-model-invocation: true
---

Write a cold-start task briefing at `/tmp/<descriptive-slug>-task.md` using the structure below. A fresh session — with no memory of the originating conversation — must be able to read the file and execute the work.

## When to use this vs `/handoff`

- **`/handoff`** captures mid-flight session state: "I'm partway through, here's exactly where, here's how to resume." The reader is a future continuation of the same task.
- **`/brief`** captures a well-scoped piece of work the current session is *not* going to do: an abandoned PR nobody is driving, a settled-scope ticket, a follow-up surfaced mid-session that doesn't belong in the current plan. The reader is a brand-new session picking it up cold.

If you are still mid-task and running low on context, use `/handoff`. If the work is known, abandoned, and someone else (or a future you) should pick it up cold, use `/brief`.

## §1 Goal
Single sentence: what shipping this work means. Frame in terms of the user-visible or system-visible outcome, not the implementation path.

## §2 What it does
The diff scope, ticket body, or change description. Why the work exists — the motivating problem or constraint. Enough that a fresh reader understands the request without the originating conversation.

## §3 Anchors
Concrete pointers the reader will need: branch name, worktree path, PR URL, ticket URL, key file paths, relevant commit SHAs. List the artifacts; do not summarize their contents.

## §4 Current state
Objective facts about where the work stands right now: branch drift vs. the default branch, CI status, review state, mergeability, outstanding test failures, environment quirks. Facts only — no narrative about how it got there.

## §5 Decisions to make
What must be confirmed BEFORE deep review or implementation continues. Open questions the picking-up session needs to resolve, not the prior session's preferences smuggled in as constraints.

## §6 Steps to ship
The concrete ordered sequence to land the work. Each step is a command, file edit, or decision the reader can execute. No vague "continue from where it was left."

## §7 Out of scope
Adjacent work that must NOT be bundled into this change. Name the temptation explicitly — refactors in the same area, parallel cleanups, "while we're here" additions — so the reader knows what to leave alone.

## Cold-start readability rules

The briefing is read by a session with zero context from the originating conversation. To survive that:

- **No PR-defined terminology.** Do not reference labels ("Defense A", "Pattern 2") that only made sense in the source conversation. If a label is meaningful, define it inline.
- **No "the prior session did…" framing.** The reader does not know what the prior session did and does not need to. State the current state, not its history.
- **No implicit context.** If a constraint depends on something said earlier, write the constraint out.
- **Names over pronouns.** A reader scanning the file should not have to chase antecedents.

Self-test: if the source conversation evaporated tomorrow, would this file still be executable? If not, rewrite.

## Slug naming

The slug names the work, not the date. Examples: `pr-242-flag-status-task.md`, `oauth-token-refresh-task.md`, `legacy-job-cleanup-task.md`. Never use `<task>-task.md` literally.

Target under 200 lines. Reference files by path; do not inline contents.

## Pre-write checklist

Before writing the file, verify:
- Every section §1–§7 above is populated
- No placeholder text ("TBD", "TODO", "fill in later") in any section
- §4 Current state is objective — facts a fresh session could verify, not editorial
- §5 Decisions to make lists open questions, not foregone conclusions framed as questions
- §6 Steps to ship name concrete commands or file edits, not vague verbs
- §7 Out of scope names at least one specific temptation to leave alone
- File contains no references to the originating conversation, prior sessions, or unstated context

## Worked example

A minimal briefing for an abandoned PR whose scope is settled:

```
# Sync user_status enum across worker and API

## §1 Goal
Ship PR #482 so worker and API agree on the user_status enum, unblocking
the rollout of the suspended-account state.

## §2 What it does
Adds `suspended` to the user_status enum in the API schema and in the
worker's local copy. Updates the two consumers that pattern-match on the
enum. Linear ticket TICKET-1893 has the full requirement.

## §3 Anchors
- Branch: add-suspended-user-status
- Worktree: ~/MyCode/<repo>/.claude/worktrees/add-suspended-user-status
- PR: https://github.com/<org>/<repo>/pull/482
- Ticket: https://linear.app/<org>/issue/TICKET-1893
- Migration file: db/migrations/20260418_add_suspended_status.sql

## §4 Current state
- 7 commits ahead of main, no drift (rebased 3 days ago).
- CI: lint passes, integration suite fails on test_user_lifecycle.
- Review: one approval, one change-requested on the worker patch.
- Mergeable: false (failing check + unresolved review).

## §5 Decisions to make
- Reviewer asked whether `suspended` should be terminal or reversible.
  Product has not weighed in. Block until answered.
- Migration is non-reversible as written. Confirm rollback strategy.

## §6 Steps to ship
1. Resolve the reversible/terminal question with product (see §5).
2. Update the migration with the chosen semantics.
3. Fix test_user_lifecycle to cover the new state.
4. Push, request re-review on the worker patch.
5. Merge once both reviewers re-approve.

## §7 Out of scope
- Renaming the other user_status values.
- Adding new admin UI for the suspended state — separate ticket.
- Refactoring the enum into a lookup table.
```

The example is generic. Adapt section content to the actual work; do not preserve example phrasing.
