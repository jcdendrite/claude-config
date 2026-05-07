# Compaction Instructions

Read this file when summarizing the conversation for `/compact` (manual or auto). Preserve:

- Active markers in `~/.claude/*-markers/` and `~/.claude/.*-active.d/` — include filenames and which skill wrote them
- Working directory and current git branch
- The current task focus and most recent uncommitted work
- File paths edited this session and their state (staged / unstaged / committed)
- Open AskUserQuestion exchanges and pending decisions
- Recent failed commands and their root causes

You may drop:

- Successful tool output already acted on
- Exploratory dead-ends that didn't inform the final approach
- Verbatim file contents already on disk (paths suffice)
