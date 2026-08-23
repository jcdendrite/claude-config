---
name: code-review-claude-config
description: Project-specific layer for /code-review, loaded only when reviewing changes in the claude-config repo itself.
disable-model-invocation: true
---

## Base checklist addition

P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material (CLAUDE.md "Also redact structural fingerprints and provenance").
Give a rounded or generalized figure more scrutiny, not less — the six
always-on structural detectors already catch raw pastes, so what reaches this
item is disproportionately content already generalized enough to clear them.
A figure with no stated, checkable source (command, file, citation) is itself
the finding — ask for the source.
