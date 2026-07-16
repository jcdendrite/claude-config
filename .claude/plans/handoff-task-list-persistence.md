# Persist the task list across a /handoff resume

## Context

**Goal:** make a session resumed from a `/handoff` file rebuild its task list automatically, instead of starting empty and re-deriving remaining work from prose.

Why now: a fresh session continuing from a handoff observed "Task list is empty in this fresh session (expected — it doesn't persist across sessions). I'll recreate the remaining tasks from the handoff." That re-derivation is lossy and manual. The handoff file is already the durable state carrier — it just never captures the live task list.

Root cause (confirmed by exploration):
- Neither `handoff` nor `brief` serializes the harness task list (`TaskCreate`/`TaskList` state). Handoff `§2.5` only asks for free-text prose about "tasks started but unfinished" — not a faithful list.
- `resume-context.sh` launches a **brand-new** `claude` session (`--append-system-prompt-file <file>`). A new session gets a new session id and therefore a fresh, empty task list.
- The harness does persist todos to disk under `~/.claude/.../todos/<session_id>`, but keyed by session id — a resumed session is a new id and never inherits the prior file.

Intended outcome: `/handoff` writes the current task list into the handoff file; the resuming session (which loads that file into its system prompt) recreates the pending/in-progress items as tasks before executing `§3`.

**Decisions locked with the user:** scope is **handoff only** (brief's `§6 Steps to ship` already functions as its task list; no change there); restore behavior is **auto-rebuild** (resuming session recreates pending/in-progress items; completed items listed for context only).

## Approach

Add a new decimal-numbered section **`§2.6 Task list`** to `handoff/SKILL.md`, placed immediately after `§2.5 Incomplete prerequisites` (the status neighborhood). Decimal numbering mirrors the existing `§2.5`/`§3.5` inserts and avoids renumbering `§3`–`§7`.

The section instructs the writing agent to:
- Read the live task list (via the task tool, not from memory) and serialize each item with its status — `completed` / `in_progress` / `pending` — preserving order.
- Carry a one-line **resume directive** aimed at the reader: *before executing `§3`, recreate the `pending` and `in_progress` items below as tasks via the task tool, preserving order; completed items are listed for context only — do not re-add them.* Recreating an `in_progress` item may take two calls (`TaskCreate` then `TaskUpdate`) if the tool cannot set that status at creation — phrase the directive so the implementer does not assume one-call status fidelity.
- Write `None.` when the session used no task list (mirrors `§2.5`'s "If none" convention).

The resume directive is a **reversible, auto-executable** in-session action — a peer to `§3`'s safe steps, not the artifact preamble's re-confirm-before-executing list (which is scoped to irreversible/shared-state actions; task creation is neither). Word the `§2.6` directive so a cautious resuming agent rebuilds the list without gating it behind engineer re-confirmation.

The resume directive lives **inside the section text**, so it ships to the resuming session through both resume paths with no script change: the fresh-process path loads the file via `--append-system-prompt-file`, and the same-session path surfaces it in the `Read` output that `consume-durable-continuity-file-on-read.sh` acts on. `resume-context.sh`'s generic launch prompt is left untouched (it is shared with `brief`).

**De-duplication with `§2.5`:** `§2.5`'s context-limit clause currently says "note what was mid-flight: tasks started but unfinished, open tool calls, pending verifications." The "tasks started but unfinished" fragment now overlaps `§2.6`. Trim `§2.5` to point task-list state at `§2.6` while keeping "open tool calls, pending verifications" — one authoritative home per the single-source-of-truth rule.

**Why prose, not a heavier mechanism (alternatives considered and rejected):**
- **Bridge the harness `todos/<session_id>` file** — copy the outgoing session's todo JSON to the new session's path. Rejected: the new session id is not known until the new session starts, and the file path/format is undocumented harness internal state that can change without notice. Fragile, and against "default-suspect over-powered primitives."
- **`PostToolUse` hook on `Task*` tools** auto-snapshotting the list to disk. Rejected: it only helps the capture side, and the **restore** side is impossible via a hook — nothing but the agent can call `TaskCreate` in a fresh session. It also duplicates persistence the handoff file already provides (single-source-of-truth violation). This is why "should this be a hook?" resolves to *no* here: the trigger (`/handoff`) is already skill-driven, and restore is inherently agent-driven.

The prose serialization is the lightest primitive that closes the loop with documented tools only, inside the existing handoff design.

## Critical files

- **`claude/.claude/skills/handoff/SKILL.md`** (the only behavioral change):
  - Insert `## §2.6 Task list` after `§2.5` (currently line 53–57) with the serialization instruction, the resume directive, and the `None.` fallback.
  - Trim the `§2.5` context-limit clause (line 55) to remove the task-list overlap, deferring to `§2.6`.
  - Update the frontmatter `description` (line 3) to add "task list" to the captured-sections list — **verify it stays under the description cap** (`test_description_within_harness_cap`).
  - Add one **Pre-write checklist** item (near lines 119–120): `§2.6` is populated — a faithful task-list serialization with per-item status, or `None.` — and carries the resume directive.
  - *Reuse:* follow the exact shape of the existing `§2.5` block and its two checklist lines; do not invent new phrasing patterns.
- **`claude/.claude/skills/tests/test_skills.py`** (enforcement, per the "add test enforcement for new conventions" rule): add a test asserting `handoff/SKILL.md` contains the `§2.6 Task list` section and the resume directive substring. Model it on the existing `test_handoff_prewrite_checklist_crosschecks_section3` (line 466) / `test_handoff_runs_sync_pr_description` (line 448) substring assertions.
- **Docs check (read-only unless drift found):** `docs/skills.md` and `README.md` describe the handoff mechanism at a summary altitude; grep for any enumerated section list that would now be stale. Update only if an explicit section enumeration exists — do not add a task-list mention to a one-line role summary.

## Verification

- `../../../.venv/bin/pytest claude/.claude/skills/tests/test_skills.py` — new `§2.6` test passes; existing handoff assertions (`§3.5` cross-check, `sync-pr-description` pointer, description cap, write-target/recipe) still pass.
- `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` — full suite/lint green (no regression in `resume-context` / consume-hook tests, which are untouched).
- **Skill self-review:** invoke `/skill-review` on the `handoff/SKILL.md` diff (per the repo rule "when editing a skill, run the skill on its own diff") — confirm the added section earns its tokens and the trimmed `§2.5` removed the duplication cleanly.
- **End-to-end manual smoke:** in a scratch session, create a few tasks (mix of completed/pending), run `/handoff`, confirm `§2.6` serializes them with correct statuses; then `resume-context <file>` into a fresh session and confirm it recreates the pending/in-progress items and skips completed ones before touching `§3`.

## Out of scope

- **`brief/SKILL.md`** — no change; `§6 Steps to ship` already serves as its task list (user-confirmed scope).
- **Harness `todos/<session_id>` bridging** and any `Task*` `PostToolUse` capture hook — rejected above; not to be added "while we're here."
- **`resume-context.sh` / consume-on-read hook** — the resume directive travels in the file body; no script or launch-prompt change.
