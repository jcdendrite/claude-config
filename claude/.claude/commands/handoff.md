Write a cross-session handoff file that lets a fresh Claude session resume the current task without asking clarifying questions.

## What to produce

Write `/tmp/<descriptive-slug>-handoff.md` where the slug names the task, not the date. Examples: `respond-pr-skill-edge-case-handoff.md`, `claude-md-redaction-handoff.md`. Never use `<task>-handoff.md` literally.

Target under 200 lines. Reference files by path; do not inline their contents.

## Sections

1. **Goal** — one sentence: what was being attempted.
2. **Status** — done / in-flight / blocked.
3. **Next concrete step** — the exact command, file edit, or question to resume on. No vague "continue the work."
4. **Files modified this session** — run `git status` and `git diff --stat`; list paths and their state (staged / unstaged / committed).
5. **Active gates / markers** — run:
   ```
   ls ~/.claude/review-markers/ ~/.claude/skill-review-markers/ ~/.claude/plan-review-markers/ ~/.claude/.*-active.d/ 2>/dev/null
   ```
   For each marker file: which skill wrote it, and (for completion markers) which staged-diff hash it covers.
6. **Open questions / decisions deferred** — anything the user still owes a call on.
7. **Resume command** — `Read /tmp/<slug>-handoff.md and continue.`

## Pre-write checklist

Before writing the file, verify:
- No placeholder text ("TBD", "TODO", "fill in later") in any section
- Status is consistent with Next concrete step and Open questions
- You are not claiming "done" for any step whose verification is still pending
- The resume command names the exact file you are about to write
