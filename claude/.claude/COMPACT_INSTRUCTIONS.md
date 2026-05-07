# State digest format

Read this file when summarizing the conversation for `/compact`
(manual or auto). The same shape is used by `/handoff` for
cross-session resume files — a context-reset agent reads the same
digest regardless of how the context was reset.

Produce a summary with these sections, in order:

## §1 Goal
One sentence: what was being attempted.

## §2 Status
done / in-flight / blocked.

## §3 Next concrete step
The exact command, file edit, or question to resume on. No vague
"continue the work."

## §4 Files modified this session
Header line: working directory + current git branch.
Then list paths edited this session and their state (staged /
unstaged / committed). Include the most recent uncommitted work.

## §5 Active gates / markers
List active markers under `~/.claude/*-markers/` and
`~/.claude/.*-active.d/` — filenames, which skill wrote each, and
(for completion markers) the staged-diff hash the marker covers.

## §6 Open questions / decisions deferred
Open AskUserQuestion exchanges, pending decisions the user still
owes a call on, and recent failed commands + root causes the
resuming session needs to know.

## You may drop

- Successful tool output already acted on
- Exploratory dead-ends that didn't inform the final approach
- Verbatim file contents already on disk (paths suffice)
