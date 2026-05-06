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
