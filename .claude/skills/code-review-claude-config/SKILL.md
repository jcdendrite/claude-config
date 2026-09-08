---
name: code-review-claude-config
description: Project-specific layer for /code-review, loaded only when reviewing changes in the claude-config repo itself.
disable-model-invocation: true
---

## Base checklist addition

P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material, against CLAUDE.md's "Also redact structural fingerprints and
provenance" rule and `docs/private-project-redaction.md` § "Publishing a
pooled tooling measurement", which states the carve-out's conditions. A
figure decomposed by project, account, or engagement is a P1 finding on
sight.
Give a rounded or generalized figure more scrutiny, not less — the six
always-on structural detectors already catch raw pastes, so what reaches this
item is disproportionately content already generalized enough to clear them.
A figure with no stated, checkable source (command, file, citation) is itself
the finding — ask for the source.
