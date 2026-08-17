---
name: code-review-claude-config
description: Project-specific layer for /code-review, loaded only when reviewing changes in the claude-config repo itself.
disable-model-invocation: true
---

## Base checklist addition

P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material. See CLAUDE.md "Also redact structural fingerprints and provenance"
for the rule and its exemptions. Treat a rounded or generalized figure with
more scrutiny, not less: the six always-on structural detectors already catch
raw pastes, so what reaches this item is disproportionately content already
generalized enough to clear them. An exemption can only be applied to a figure
whose origin is stated, so an empirical figure naming no checkable source — a
command, a file, a citation — is itself the finding: ask for the source rather
than guessing at it.
