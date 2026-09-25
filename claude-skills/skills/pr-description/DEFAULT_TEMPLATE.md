# Default PR description template

The section skeleton `SKILL.md` falls back to when the repo ships no `.github/PULL_REQUEST_TEMPLATE.md`. Use the `##` headings below, in this order. The prose under each heading is instruction to you, never text to copy into the body.

`## Screenshots`, `## Context for the reviewer`, and `## Alternatives considered` are conditional. Omit a conditional heading and its body entirely when the change gives it nothing real to say — never keep the heading with a placeholder or an invented entry.

## Summary

`SKILL.md` § "What the body must carry" owns this section's content: follow its **What and why** bullet.

## Screenshots

Before/after images or a short clip of a surface a reader can look at. Omit for a change that renders nothing.

## Context for the reviewer

What a reviewer with no prior exposure to this area needs and the diff does not show: the constraint that forced the shape, the surrounding subsystem, the behavior being replaced.

When the caller's own `$ARGUMENTS` account is background rather than what and why, it belongs here. `SKILL.md` § "What the body must carry" covers folding it in.

## Alternatives considered

Each approach actually weighed during the work, with its one-line reason for being set aside. Never reconstruct one after the fact.

## Test plan

`SKILL.md` § "What the body must carry" owns this section's content: follow its **A `## Test plan` of results, not a checklist** bullet.
