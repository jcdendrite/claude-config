# References — code-review

Reference material that informed this skill. Not loaded during skill execution — consult when editing the skill to verify a rule still holds or to add new guidance.

## F-04: Mid-skill auto-trigger empirical test (2026-05-04)

**Question:** Does description-based skill auto-trigger fire from inside a running skill?

**Test setup:** Invoked `/code-review` on a diff touching three specialist-skill surfaces simultaneously:
- `claude/.claude/hooks/_f04_probe.sh` — surface for `claude-hook-review`
- `claude/.claude/skills/_f04_probe/SKILL.md` — surface for `skill-review`
- `claude/.claude/settings.json` adding a `permissions.allow` rule — surface for `review-permissions`

**Observation method:** Watched for system-reminders announcing skill body loads before code-review executed. System-reminders appear at message-submission time (harness auto-trigger); explicit Skill tool calls appear during execution (prose-pointer invocation). The two mechanisms are temporally distinguishable.

**Result:** No system-reminders for `claude-hook-review`, `skill-review`, or `review-permissions` appeared. Only `code-review` itself loaded. Auto-trigger did **not** fire from inside a running skill.

**Policy decision:** The prose pointers at `code-review/SKILL.md` ("see the `skill-review` skill" and "see the `claude-hook-review` skill") are load-bearing. Without them, specialist skills are silently skipped when code-review runs on their surfaces. Keep the pointers. F-14 item 1 (standardize the voice of those pointers) is the next step.

## F-05: Project-layer composition via prose-pointer + glob (2026-05-05)

**Trigger:** A review shipped a 7-site DRY violation despite multiple `/code-review` passes. Investigation found two gaps: (1) no Hygiene item for in-house logic duplication, (2) a project-specific code-review layer (`user-invocable: false` + description-trigger) had never fired — F-04 explains why.

**Decision:** Wire project layers via explicit prose pointer (Step 0.5 in code-review, Step 2.5 in plan-review) using a glob `git rev-parse --show-toplevel`/`.claude/skills/<parent>-*/SKILL.md`. Single match → invoke via Skill tool. Multiple matches → stop (config error in the project, not something the review skill resolves). Zero matches → proceed without a layer.

**Rationale:** The glob generalizes across projects without editing the public skill on each onboarding. Hardcoding project names was rejected (no generalization). Config-file indirection was rejected (no value over the established naming convention). Prose pointer is the F-04-validated mechanism: Skill-tool invocation works mid-skill; description-based auto-trigger does not.

**Hygiene item added:** Item 9 (Repeated in-house logic that should be extracted) was added to the Hygiene section of `code-review/SKILL.md`. The item includes a DAMP carve-out for test files — repeated arrange/assert blocks are intentional in tests. Items 9–34 renumbered to 10–35 throughout the body and ownership table.
