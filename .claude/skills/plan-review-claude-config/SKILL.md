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

## Gate threat-model tiers (Domain: Security; Output format)

Before dispositioning a finding against a `hook-class: gate` hook, read `docs/hooks.md` § "Threat-model tiers". That section decides whether the finding is a defect in the gate and, when it is not, where the gap is recorded. Non-gate hooks and shared library code get no disposition change from this section.

- A regression, as that section defines it, stays under the base rules at every tier, including a shape the plan newly admits. No recording step changes that.
- Any other finding that section waives or routes is not an enforcement-invariant finding under the fix-or-ask rule. It still appears in the output. It blocks the verdict only while the plan names no step making the recording that section requires. When the plan names one, the verdict is Approve with changes and lists that step; when it names none, the verdict names the step required.
- Subject to the first bullet, S1's bypass-vector enumeration and S2's defense in depth, for a gate, cover only the shapes its tier treats as defects.
- A finding that section does not explicitly waive or route stays under the base rules.
