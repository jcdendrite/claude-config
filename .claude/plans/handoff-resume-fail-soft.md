# Make the handoff task-list resume directive fail-soft

## Context

**Problem.** A resumed session stalled on the handoff mechanism's task-list
resume step and produced reasoning that read as confusion — the first time this
symptom has been observed. Diagnosis: the mechanism instructs the session to
recreate its task list "via the task tool," but that tool is not present in the
kind of session that resumes a handoff, and the directive gives no graceful
fallback.

**Diagnosis (verified via primary sources + empirical tests).**

1. **Task creation was not removed — it was renamed.** Official docs
   (`code.claude.com/docs/en/agent-sdk/todo-tracking`): *"as of … Claude Code
   v2.1.142, sessions use the structured Task tools `TaskCreate`, `TaskUpdate`,
   `TaskGet`, and `TaskList` instead of `TodoWrite`"* — and they are the default.
   The creator's 2026-01-22 announcement frames it as *"upgrading Todos … to
   Tasks … collaborate … across multiple sessions or subagents."* So the skill's
   `Task*` naming is current and the capability exists upstream.

2. **The Task tools are gated on an interactive TTY.** Two first-party
   anthropics/claude-code issues: **#20463** — *"Tasks tools not available in
   headless mode. `claude -p` does not know the Task Tools"* — and **#23874** —
   *"Task tools (TaskCreate/TaskUpdate/TaskList/TaskGet) disabled in VSCode
   extension due to isTTY check."* The tools are compiled into the binary but
   withheld from headless / non-TTY contexts.

3. **The handoff mechanism's sessions are exactly those non-TTY contexts.**
   `resume-context.sh` launches the resumed session programmatically (via
   `exec "$LAUNCHER" …`), and orchestration/harness sessions are likewise
   non-interactive. Empirically confirmed absent across three sessions on this
   machine (plan / auto / manual permission modes; Opus 4.8 and Sonnet 5) — with
   `claude --version` = 2.1.216, `CLAUDE_CODE_ENABLE_TASKS` unset, no settings
   `disabledTools`, and the `claude-auto` launcher adding only
   `--model opus --permission-mode auto`. Nothing local disables the tools; the
   gate is upstream and contextual.

4. **Open thread (not blocking the fix).** A live *interactive* session in a
   normal terminal also reported no `Task*` tools, which pure TTY-gating would
   not predict — so there may be additional gating on this build or the current
   model family. Pursuing that is about getting the tools *back* (see Out of
   scope); it does not change the fix, because handoff-resume sessions are
   non-TTY and will lack the tools regardless.

**Compounding causes that turned "tool absent" into visible confusion:**
- **Generic reference** — the directive says "the task tool," never a concrete
  name, so the session's search surfaced Todoist (a literal task app) as a
  candidate to rule out, and it reached for `ToolSearch` (which indexes only
  deferred tools and cannot see a first-class builtin anyway). The dead-ended
  hunt is what read as confusion.
- **No fail-soft fallback + mandatory framing** — "recreate … before taking any
  other action" and "do not reconstruct from memory" frame the step as critical
  and first. With the tool unavailable and no escape hatch, the session could
  neither perform nor cleanly skip it, so it narrated a dead end and, on
  pushback, defended the conclusion.

**Intended outcome.** A resumed session that lacks a task-list tool — the normal
case for handoff resumes — degrades gracefully: it tracks the serialized items
inline and continues, instead of stalling. Sessions that *do* expose a task-list
tool still mirror the items into it.

## Approach

Flip the directive's emphasis and remove the hard gate, at both sites that carry
it. The §2.6 serialization stays authoritative — it is a human/agent-readable
checklist that is valuable regardless of tooling. We change only how the
resuming session is told to *consume* it.

1. **Inline tracking is the primary, expected path.** Handoff-resume sessions
   are non-TTY and typically expose no task-list tool, so instruct the session
   to track the `pending` / `in_progress` items inline from §2.6 and proceed.
2. **Tool use is opportunistic, not required.** "If your session exposes a
   task-list tool (e.g. `TaskCreate`/`TaskUpdate`), also mirror the items into
   it." Name the current builtin as an example, not the sole identifier, so the
   text survives a future rename. *Retained even though the primary resume path
   (`resume-context.sh`) launches a non-TTY, toolless session: the §2.6 directive
   travels inside the handoff file and can also be resumed manually in an
   interactive TTY session, where the tool is present.*
3. **Drop the blocker framing.** Remove "before taking any other action" / "before
   executing §3"; tracking the list is a normal early step whose absence never
   blocks §3.

The capture side has the same defect: the §2.6 *capture* instruction (authoring)
currently says "read the live task list via the task tool," but the authoring
session is itself typically non-TTY and toolless, so that is already
unfollowable. The fix makes capture read from the tool *if present*, else from
the inline-tracked items — symmetric with resume.

Deliberately *not* added: a "the tool is a first-class builtin, don't trust an
empty ToolSearch" clause. The tool is genuinely absent in these contexts, so that
guidance would re-invite the same dead-end hunt rather than prevent it.

Edit sites (line numbers verified against current `HEAD`):
- **`handoff/SKILL.md` §2.6 capture instruction (line 49)** — read from the
  task-list tool if present, else from inline-tracked items (still not from
  memory).
- **`handoff/SKILL.md` §2.6 resume directive (line 51)** — inline-primary /
  tool-opportunistic / no-blocker. (Header §2.6 is line 47; the pre-write
  checklist at line 117 references the directive *by existence*, not content —
  leave it unchanged.)
- **`resume-context.sh` launch prompt (line 167)** — rewrite to match.
- **Pinned tests** — update the verbatim assertions: `test_resume_context.py`
  **L116** (launch prompt), `test_skills.py` **L494** (capture instruction) and
  **L508** (resume directive). Grep the changed phrases across both test files
  first to catch any assertion not listed here; confirm the unchanged pre-write
  checklist assertion stays green.

### Proposed wording (skill-review finalizes at implementation)

- **Launch prompt (`resume-context.sh` L167):** *"Continue from the handoff/brief
  file loaded into your system prompt. If it contains a task-list resume
  directive, track its pending and in-progress items from the file (not from
  memory) as you resume — using your session's task-list tool if one is
  available, otherwise inline. A missing task-list tool is not a blocker."*
- **§2.6 capture instruction (L49):** replace "Read the live task list via the
  task tool (not from memory)" with *"Read the current task list — from your
  session's task-list tool if it exposes one, otherwise from the inline items you
  have been tracking (not reconstructed from memory)"*; the rest of the sentence
  (ordinal / status / blocking edges / `activeForm` / example) is unchanged.
- **§2.6 resume directive (L51):** *"**Resume directive:** §2.6 is the
  authoritative source of remaining task state on resume — do not reconstruct the
  task list from the plan file or from memory. As you resume, track the `pending`
  and `in_progress` items below, preserving order and their `blockedBy`/`blocks`
  relationships (map the serialized ordinals to the items in that position);
  completed items are listed for context only — do not re-add them. If your
  session exposes a task-list tool (e.g. `TaskCreate`/`TaskUpdate`), mirror the
  items into it — an `in_progress` item may take two calls (create, then set
  status) if creation can't set it directly. If it does not — common for resumed
  sessions — track them inline. Tracking these items is a safe, reversible action,
  not gated by the re-confirm-before-executing rule; a missing task-list tool is
  not a blocker."*

## Critical files

- `claude/.claude/skills/handoff/SKILL.md` — §2.6, lines 49 (capture) and 51
  (resume directive).
- `claude/.claude/scripts/resume-context.sh` — launch-prompt string, line 167.
- `claude/.claude/scripts/tests/test_resume_context.py` — launch-prompt
  assertion, L116.
- `claude/.claude/skills/tests/test_skills.py` —
  `TestHandoffTaskListPersistence` assertions, L494 (capture) and L508 (resume
  directive).

No new code; prose edits plus test updates. Reuse the existing §2.6 serialization
and the existing conditional launch-prompt shape.

## Verification

- `.venv/bin/pytest claude/.claude/scripts/tests/test_resume_context.py claude/.claude/skills/tests/test_skills.py`
- `.venv/bin/ruff check claude/.claude/`
- `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` (resume-context.sh is shell)
- Manual resume smoke test: run `resume-context.sh` with `RESUME_CONTEXT_LAUNCHER`
  pointed at a recorder stub (existing test pattern) and confirm the new
  launch-prompt string; read the rendered §2.6 directive and confirm the
  inline-primary / no-blocker wording.
- Run `skill-review` on `handoff/SKILL.md` (stowed public skill) and `/code-review`.

## Out of scope

- **`brief/SKILL.md`** — carries no task-list directive (§6 is its task list);
  unchanged. The launch prompt's `if it contains a task-list resume directive`
  guard already no-ops for briefs.
- **Getting the Task tools back on this build** (the open thread above:
  TTY-gating and possible model/build gating). If resolved, resumed sessions
  launched from a real TTY could use the tool — but the fail-soft fix stands
  regardless, so this is a parallel investigation, not a dependency.
- **Deleting the recreation machinery.** The §2.6 serialization is kept — it is
  the resumable record of remaining work. We de-emphasize the recreate-via-tool
  branch (now opportunistic), not the serialization itself. A stow user on an
  interactive-TTY build still benefits from the tool branch.
