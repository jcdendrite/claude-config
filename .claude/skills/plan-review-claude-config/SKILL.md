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

- Ask first whether the finding is a per-vector gap or a genuinely-lax failure. A per-vector gap needs a shape a cooperative agent would never emit, against a gate whose tier line omits `untrusted-input`. A genuinely-lax failure is the gate's rule failing on a shape a cooperative agent does emit. That section's `cooperative` bullet and its mis-parse paragraph draw the line. A genuinely-lax failure stays under the base rules unless it is already in that section's closed existing-debt set.
- A regression, as that section defines it, stays an enforcement-invariant finding under the fix-or-ask rule at every tier, including a shape the plan newly admits. No recording step changes that.
- A finding against a gate whose tier line lists `untrusted-input` stays under the base rules.
- Any other finding that section waives or routes is not an enforcement-invariant finding under the fix-or-ask rule. It still appears in the output and does not block the verdict. When the plan names no step recording it, the verdict's change list adds one step that records every such shape this review surfaced in one line of the gate's header Known-gaps section, creating the section when the header has none.
- For a gate, S1's bypass-vector enumeration and S2's defense in depth cover every regression and otherwise only the shapes its tier treats as defects.
- A finding that section does not explicitly waive or route stays under the base rules.
