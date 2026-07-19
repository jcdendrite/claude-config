---
name: plan-review-claude-config
description: Project-specific layer for /plan-review, loaded only when reviewing plans in the claude-config repo itself.
disable-model-invocation: true
---

## User surface (Step 2)

`claude/` is stowed into `$HOME` — changes ship to every user who clones and
stows this repo, not only to the session owner. When reviewing a plan for
claude-config, evaluate with that audience in mind. Files under `claude/` are
not personal config; they are distributed to all stow users on `git pull`.
Weight finding severity accordingly.
