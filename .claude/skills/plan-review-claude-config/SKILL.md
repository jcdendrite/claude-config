---
name: plan-review-claude-config
description: Project-specific layer for /plan-review, loaded only when reviewing plans in the claude-config repo itself.
disable-model-invocation: true
---

## User surface (Step 4, question 1)

`claude/` is stowed into `$HOME` — changes ship to every user who clones and
stows this repo, not only to the session owner. When reviewing a plan for
claude-config, evaluate with that audience in mind. Files under `claude/` are
not personal config; they are distributed to all stow users on `git pull`.
Weight finding severity accordingly. Wide distribution does not by itself
raise the threat model — a local CLI run by many people is still not
externally reachable. The redaction obligation is unchanged either way; see
root `CLAUDE.md`'s "Plans in this repo affect all stow users" bullet for what
a plan file itself may and may not contain.
