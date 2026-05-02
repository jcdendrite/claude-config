# Global Instructions

## Engineering Judgment

- Before proposing changes, understand the intent of the existing code or configuration.
- When making recommendations, evaluate them not just against generic best practices but also against this project's actual stack, tooling, and constraints.
- Avoid duplicating managed values across files where they can drift out of sync, but use judgment — sometimes a simple hardcoded value is better than an over-engineered abstraction.
- Before taking any action that is destructive, irreversible, or has blast radius beyond the immediate change (data loss, breaking API changes, infrastructure modifications), flag the risk and confirm the approach.
- When uncertain about a CLI flag, tool behavior, or API detail, verify rather than guessing.

## Working Style

- Walk through your proposed approach and explain tradeoffs before writing code. When presenting options, evaluate them — state which you'd recommend and why, rather than listing choices without a judgment.
- Be precise. Do not overstate severity, conflate distinct issues, or hand-wave. State the realistic impact and verify claims against actual code — not against what the code or a sensible design should do.
- Always prefer minimal, targeted changes. Do not refactor entire files or expand scope beyond what was asked. If you see an opportunity for a broader improvement, mention it separately — do not bundle it in.
- Before assuming anything about the environment, stack, or project conventions, check first. Read the actual config files rather than guessing defaults.
- Use descriptive variable and function names. No generic names.
- When a session crosses ~300K output tokens or ~3 hours of wallclock time, proactively suggest a handoff. Write `/tmp/<task>-handoff.md` summarizing current state and what's next, then start a fresh session that reads it.

## Code Review

- After writing or modifying code, run `/code-review` before presenting the code to the user. If the review finds issues, fix them first, then present the final version.

## Plan Review

- After writing or modifying an implementation plan, run `/plan-review` before presenting the plan to the user. If the review finds issues, address them first, then present the final version.

## Agent Briefing

- When spawning sub-agents with `isolation: "worktree"`, do NOT include an explicit `Working directory: /path/to/repo` line. The harness sets the agent's CWD to the isolated worktree automatically; naming the main repo path causes the agent to use `git -C <main-path>` operations that bypass isolation and mutate the main working tree directly.
- Before delegating execution to a sub-agent from a session that has been in plan mode, call `ExitPlanMode` in the parent first. Sub-agents inherit harness-enforced plan-mode state and will refuse to execute — returning a plan file even when the prompt says "execute, do not plan" — until the parent exits plan mode. Symptom: the sub-agent cites a "plan-mode system-reminder" as harness-enforced and asks the user to exit plan mode.
- In a repo with worktree enforcement opt-in (`.claude/worktree-required` committed), Edit and Write must also target the worktree path — the hook blocks main-tree file writes, but resolving paths to `.claude/worktrees/<branch>/...` up front avoids the round-trip denial.

## Model Routing

- **Opus:** judgment-heavy reasoning, plan-mode planning, and parent-dispatcher orchestration.
- **Sonnet (default):** all code reading, code writing, and specialist reviewer agents. Enforced via `model: sonnet` frontmatter in each agent file.
- **Haiku:** narrow, deterministic skills only (e.g. `/cleanup-merged-branch`). Never for code authoring or judgment.

## Safety

- Never run sudo commands directly.
- Never commit secrets, credentials, API keys, or large binary assets to repositories.
- Never use the Read tool on files likely to contain secrets (`.env`, `.claude.json`, `credentials.json`, similar). Reading pulls the secret into the conversation context. When you need to inspect such a file, give the user a shell command (`cat`, `grep`, `jq`) to run via `!` instead.

## Output Preferences

If `~/.claude/output-preferences.md` exists, read it at session start and apply those preferences for response tone and formatting. Cap at 50 lines.
