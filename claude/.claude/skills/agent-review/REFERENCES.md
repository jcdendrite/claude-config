# agent-review — References

## Canonical Anthropic docs

**Sub-agents reference:** `https://code.claude.com/docs/en/sub-agents`

Verbatim quotes for the frontmatter contract rules in §1 and §7:

- **Required fields:** *"Only `name` and `description` are required."*
- **`name`:** *"Unique identifier using lowercase letters and hyphens. Hooks receive this value as `agent_type`. The filename does not have to match."*
- **`name` uniqueness:** *"Keep `name` values unique across the whole tree: if two files within one scope declare the same name, Claude Code keeps one and discards the other without warning."*
- **`tools` semantics (restrictive):** *"Tools the subagent can use. Inherits all tools if omitted."* and *"By default, subagents inherit all tools from the main conversation, including MCP tools. To restrict tools, use either the `tools` field (allowlist) or the `disallowedTools` field (denylist)."*
- **`disallowedTools`:** *"Tools to deny, removed from inherited or specified list."* and *"If both are set, `disallowedTools` is applied first, then `tools` is resolved against the remaining pool."*
- **`model`:** *"Model to use: `sonnet`, `opus`, `haiku`, a full model ID (for example, `claude-opus-4-7`), or `inherit`. Defaults to `inherit`."*
- **`maxTurns`:** *"Maximum number of agentic turns before the subagent stops."*
- **`permissionMode`:** *"`default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, or `plan`. Ignored for plugin subagents."*
- **`mcpServers`:** *"MCP servers available to this subagent. … Ignored for plugin subagents."*
- **`hooks`:** *"Lifecycle hooks scoped to this subagent. Ignored for plugin subagents."*
- **`skills`:** *"Skills to preload into the subagent's context at startup. The full skill content is injected, not just the description."*
- **`memory`:** *"Persistent memory scope: `user`, `project`, or `local`. Enables cross-session learning."*
- **`isolation`:** *"Set to `worktree` to run the subagent in a temporary git worktree, giving it an isolated copy of the repository."*
- **`color`:** *"Display color for the subagent in the task list and transcript. Accepts `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, or `cyan`."*
- **`background`:** *"Set to `true` to always run this subagent as a background task."*
- **`effort`:** *"Effort level when this subagent is active. … Options: `low`, `medium`, `high`, `xhigh`, `max`; available levels depend on the model."*
- **`initialPrompt`:** *"Auto-submitted as the first user turn when this agent runs as the main session agent (via `--agent` or the `agent` setting)."*

**Plugin-subagent ignored fields (security):** *"For security reasons, plugin subagents do not support the `hooks`, `mcpServers`, or `permissionMode` frontmatter fields. These fields are ignored when loading agents from a plugin."*

## Why a peer skill and not a merged `/skill-review`

`skill-review` and `agent-review` have a deliberate lane split: skill files use the harness's always-loaded description budget and route the dispatcher, while agent files are lazy-loaded — body content is paid only when the harness dispatches the agent. The frontmatter contracts also diverge in load-bearing ways:

- Skill `allowed-tools` is **additive** (omitting it inherits nothing; listing tools opens them up).
- Agent `tools` is **restrictive** (omitting it inherits everything; listing tools narrows the pool).

A single merged skill body would need to grow §1 across two divergent contracts indefinitely, and the opposite defaults are the kind of footgun that wants a dedicated checklist item. PR #291 attempted the merged form and surfaced this divergence as the structural blocker.

## Why a peer skill and not a `/code-review` specialist

`/code-review`'s `staff-*` specialists review general code through domain lenses (backend, frontend, data, etc.). Agent-file quality is criteria-specific — frontmatter contract, executor-style carve-out, voice/length — closer in shape to SKILL.md review than to general-code review. A peer skill mirroring `/skill-review`'s shape is structurally obvious to a future reader and matches the criteria-specificity.

## Why not adopt Anthropic's `agent-creator`

Anthropic's official `plugin-dev` plugin (at `anthropics/claude-plugins-official/plugins/plugin-dev/agents/`) ships separate `skill-reviewer.md`, `agent-creator.md`, and `plugin-validator.md` — the per-type split here matches that precedent. `agent-creator` is a creator (interactive generation), not a reviewer; it does not carry this repo's conventions (redaction, behavior test, behavioral-equivalence audit on compression diffs, no-shared-partials, platform-genericness, executor-style carve-out). Anthropic does not ship an `agent-reviewer` at all. Same rationale as PR #161's `skill-creator` removal.

## Why this skill is not gated

Captured inline in SKILL.md's closing section ("Why this skill is not gated"). Short summary: agent bodies are lazy-loaded and lower-blast-radius than skill descriptions, so dispatcher-level invocation through `/code-review` is sufficient; no pre-commit gate. Bundling under the `skill-management` plugin's hook would couple two independent consumer contracts — plugin consumers who installed for SKILL.md enforcement would inherit an agent-review gate they did not opt into.
