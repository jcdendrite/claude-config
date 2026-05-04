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
