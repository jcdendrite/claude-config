# References — claude-hook-review

Reference material that informed this skill. Not loaded during skill execution — consult when editing the skill to verify a rule still holds or to add new guidance.

## Anthropic hooks documentation

**URLs (verified live 2026-05-06):**

- Hooks reference: `https://docs.claude.com/en/docs/claude-code/hooks` (redirects to `https://code.claude.com/docs/en/hooks`)
- Hooks guide: `https://docs.claude.com/en/docs/claude-code/hooks-guide` (redirects to `https://code.claude.com/docs/en/hooks-guide`)

**Verbatim quotes used in Section 2 (path resolution):**

From the hooks reference, "Reference scripts by path" section:

> "Handlers run in the current directory with Claude Code's environment."

> "`$CLAUDE_PROJECT_DIR`: the project root. Wrap in quotes to handle paths with spaces."

> "`${CLAUDE_PLUGIN_ROOT}`: the plugin's installation directory, for scripts bundled with a plugin. Changes on each plugin update."

From the hooks-guide troubleshooting section:

> "If you see 'command not found', use absolute paths or `$CLAUDE_PROJECT_DIR` to reference scripts."

**PreToolUse event contract (grounds Section 4, fail-closed posture):**

From the hooks reference, PreToolUse section (`https://docs.claude.com/en/docs/claude-code/hooks#pretooluse`): `.tool_name` and `.tool_input` are always present on a real hook event. That is what makes a jq failure on either field evidence of malformed or spoofed input rather than a legitimate variation, and therefore what licenses the deny rather than a fall-through.

## Motivating failure (Sections 7 and 10)

A hook PR that shelled out to an external daemon command passed `/code-review` without `staff-platform-engineer` ever being consulted. A follow-up manual review found a Medium-severity unbounded-latency bug: the external command could hang indefinitely with no timeout guard, blocking every matching tool invocation until the OS fired a default timeout.

The miss was structural: `code-review` delegates hook review to `claude-hook-review`, and neither had a step that escalated to `staff-platform-engineer` for operational-footprint review. Item 36 in `code-review`'s item-ownership table names `staff-platform-engineer` as primary owner of "Hook correctness," but the ownership was declared and never actuated by either the primary or the delegate skill.

Section 10 closes the gap at the delegate so the escalation fires on both entry paths (`code-review` → `claude-hook-review`, and `claude-hook-review` triggered directly). The Section 7 timeout line closes the deterministic catch — both are needed because Section 7 catches the specific external-command pattern statically while Section 10 provides the holistic operational judgment a static checklist cannot replicate.
