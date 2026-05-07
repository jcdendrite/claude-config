Write a cross-session handoff file at `/tmp/<descriptive-slug>-handoff.md`.

The file uses the structured shape defined in
`~/.claude/COMPACT_INSTRUCTIONS.md` (§1–§6). Apply the same
per-section guidance — the difference between a `/handoff` file and a
`/compact` summary is the carrier (file vs. inline summary), not the
content shape.

After §6, append:

## §7 Resume command
`Read /tmp/<slug>-handoff.md and continue.`

## Slug naming

The slug names the task, not the date. Examples:
`respond-pr-skill-edge-case-handoff.md`,
`claude-md-redaction-handoff.md`. Never use `<task>-handoff.md`
literally.

Target under 200 lines. Reference files by path; do not inline
contents.

## Pre-write checklist

Before writing the file, verify:
- Every section §1–§6 from `~/.claude/COMPACT_INSTRUCTIONS.md` is populated
- No placeholder text ("TBD", "TODO", "fill in later") in any section
- §2 Status is consistent with §3 Next concrete step and §6 Open questions
- You are not claiming "done" for any step whose verification is still pending
- §7 Resume command names the exact file you are about to write
- Markers in §5 use the globs `~/.claude/*-markers/` and `~/.claude/.*-active.d/` — not a hardcoded subdir list
