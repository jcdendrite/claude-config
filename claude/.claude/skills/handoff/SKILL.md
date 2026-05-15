---
name: handoff
description: Write a cross-session handoff file at /tmp/<descriptive-slug>-handoff.md capturing goal, status, next step, files modified, active markers, open questions, and resume command.
disable-model-invocation: true
---

Write a cross-session handoff file at `/tmp/<descriptive-slug>-handoff.md` using the structure below.

## §1 Goal
One sentence: what was being attempted.

## §2 Status
done / in-flight / blocked.

## §3 Next concrete step
The exact command, file edit, or question to resume on. No vague "continue the work."

## §4 Files modified this session
Header line: working directory + current git branch. Then list paths edited this session and their state (staged / unstaged / committed). Include the most recent uncommitted work.

## §5 Active gates / markers
List active markers under `~/.claude/*-markers/` and `~/.claude/.*-active.d/` — filenames, which skill wrote each, and (for completion markers) the staged-diff hash the marker covers.

## §6 Open questions / decisions deferred
Open AskUserQuestion exchanges, pending decisions the user still owes a call on, and recent failed commands + root causes the resuming session needs to know.

## §7 Resume command
`Read /tmp/<slug>-handoff.md and continue.` When §3's next step is execution to be delegated to sub-agents, add a line telling the resuming agent to call `ExitPlanMode` first if it opens in plan mode — sub-agents spawned from a plan-mode session honor the plan-mode system-reminder and decline to execute.

## You may drop

- Successful tool output already acted on
- Exploratory dead-ends that didn't inform the final approach
- Verbatim file contents already on disk (paths suffice)

## Slug naming

The slug names the task, not the date. Examples: `respond-pr-skill-edge-case-handoff.md`, `claude-md-redaction-handoff.md`. Never use `<task>-handoff.md` literally.

Target under 200 lines. Reference files by path; do not inline contents.

## Pre-write checklist

Before writing the file, verify:
- Every section §1–§7 above is populated
- No placeholder text ("TBD", "TODO", "fill in later") in any section
- §2 Status is consistent with §3 Next concrete step and §6 Open questions
- You are not claiming "done" for any step whose verification is still pending
- §7 Resume command names the exact file you are about to write
- Markers in §5 use the globs `~/.claude/*-markers/` and `~/.claude/.*-active.d/` — not a hardcoded subdir list
