# Global Instructions

## Engineering Judgment

- Before proposing changes, understand the intent of the existing code or configuration.
- When making recommendations, evaluate them not just against generic best practices but also against this project's actual stack, tooling, and constraints.
- Avoid duplicating managed values across files where they can drift out of sync, but use judgment — sometimes a simple hardcoded value is better than an over-engineered abstraction.
- Before taking any action that is destructive, irreversible, or has blast radius beyond the immediate change (data loss, breaking API changes, infrastructure modifications), flag the risk and confirm the approach.
- When uncertain about a CLI flag, tool behavior, or API detail, verify rather than guessing.
- **Default-suspect over-powered primitives.** When a design adopts a more powerful, invasive, or wider-scope mechanism than the task requires — a heavier abstraction, a more privileged execution context, a more complex coordination pattern, a more invasive integration — identify the lighter primitive in the source documentation/system that could solve the problem before adopting the heavier one. Re-read the source with the specific question "what mechanisms exist that do NOT require this heavier choice?" — first-pass reads miss the answer routinely.
- **Audit structural siblings before scoping a fix narrowly.** When a fix lands in one arm of a multi-arm structure (case statement, switch, parallel subcommands, sibling functions sharing a shape), check the other arms for the same bug shape before finalizing scope. If the fix is identical, apply to every affected site; abstract when two or more share it. Scope is set by the bug, not by where the symptom surfaced.
- **Prove your change caused a failing check before treating it as in-scope.** When a check fails, reproduce it on the pre-change baseline — a throwaway worktree at the merge-base (`git worktree add <tmp> $(git merge-base HEAD origin/main)`) — before assuming the failure is yours. Failing there too means pre-existing drift, not this task's work: sync the branch if the base already fixed it, but never hand-reimplement a merged fix or edit an unrelated file. Passing at the baseline but failing on your branch means your change caused it — in scope even if you never touched the failing file, since a change breaks dependents through their imports.

## Working Style

- Walk through your proposed approach and explain tradeoffs before writing code. When presenting options, evaluate them — state which you'd recommend and why, rather than listing choices without a judgment.
- Be precise. Do not overstate severity, conflate distinct issues, or hand-wave. State the realistic impact and verify claims against actual code — not against what the code or a sensible design should do.
- Always prefer minimal, targeted changes. Do not refactor entire files or expand scope beyond what was asked. If you see an opportunity for a broader improvement, mention it separately — do not bundle it in.
- **Compounding defensive layers are a wrong-foundation tell.** When a design accumulates stacked defenses on a single mechanism — each new layer closing a gap that the prior layer's existence created — or starts citing its own prior findings, step back and ask whether a foundational change would dissolve them. Do not keep adding hardening. The right primitive usually has a simple shape; compounding complexity is a signal to question the foundation, not to defend it more carefully.
- Before assuming anything about the environment, stack, or project conventions, check first. Read the actual config files rather than guessing defaults.
- Use descriptive variable and function names. No generic names.
- When a session crosses ~60% context usage (visible via statusline `.context_window.used_percentage`) AND the current task is incomplete, proactively suggest the user run `/handoff`. Do not run it yourself — it's a user-invoked slash command. If the user agrees, the command writes `/tmp/<slug>-handoff.md` and provides the resume incantation. Run `analyze-context` if unsure about the threshold. (See README's "Threshold reference" section for the why-60%.)

### Heavy command output

Use the `Agent` tool with `subagent_type: check-runner` to run the checks (full test suites, lint, typecheck, build) — not `Bash` in the parent directly. **Enumerate the exact command strings in the dispatch prompt** (e.g. "Run these commands: `pytest claude/.claude/`, `ruff check claude/.claude/`") — not "run the checks" or "run the suite". The subagent writes full output to `${TMPDIR:-/tmp}/<command-slug>-<epoch-ms>.txt` and returns a structured verdict plus the file paths; the parent reads the file for more detail rather than re-running. This applies to suite-level runs; commands scoped to a single test file or single test name during interactive debugging can stay inline.

**Reporting test counts.** check-runner's verdict carries no test counts and no per-sub-suite breakdown — on a passing run it surfaces nothing but exit codes. To tell the user how many tests passed, or a per-type breakdown, do not quote a number from the verdict or state one from memory — `grep` the spool file for the runner's own summary lines (e.g. `grep -E '(Test Files|Tests|passed|failed)' <spool>`) and quote those. A `grep` over the full spool recovers every sub-suite's verbatim totals in a few dozen lines — context-cheap, unlike reading the whole spool back.

**The dispatch prompt must include the absolute working directory** (e.g. `Working directory: /absolute/path/to/worktree`). check-runner has no guaranteed cwd; directory-sensitive commands run from the wrong tree produce misleading failures. Do not enumerate setup or state-mutating commands in the list — db resets, migrations, container start/stop, seed scripts, package installs — perform setup yourself before dispatching.

If the subagent reports a check was `BLOCKED`, do NOT fall back to running it directly in the parent — that defeats the dispatch (parent context inhales the output) and silently bypasses the gate that stopped it. Branch on the `block_type` the verdict carries:

- `SETTINGS_DENIAL` — a missing or declined permission rule. Before recommending an allow-rule, confirm an existing rule does not already cover the command (don't propose a dead rule). Surface the exact rule needed — exact-match, not a glob (per the Safety section), e.g. `Bash(npm run verify)` — and wait for the user to pre-approve it in the appropriate scope's `permissions.allow` or run the command themselves.
- `HOOK_BLOCK` — a PreToolUse hook blocked the call; this is not a settings gap. Diagnose from the hook's verbatim stderr in the verdict; do not add an allow-rule.
- `UNKNOWN_BLOCK` — surface the verbatim message and ask the user; an interactive-prompt decline can land here. Do not guess a remediation.

### Codebase discovery

When you need to *locate* something — where a symbol is defined, which files reference an identifier, broad `grep`/`glob` sweeps, exploratory reads mapping an unfamiliar area — dispatch it to a subagent (`subagent_type: Explore` for locate-style search; `general-purpose` when the exploration must read whole files, as `/plan-it` and `/plan-review` do) rather than running it inline. Discovery output inhales into the parent context exactly like a check suite does, and an auto-mode parent on Opus pays that in the most expensive tokens — for output it only needed an *answer* from. A single targeted lookup — one `grep` for a known symbol, one `Read` of a known path — stays inline; dispatch when the search is broad or spans more than ~3 queries.

This does not apply to *comprehension* reads: when you need a file's content in your own reasoning — to write or modify it, review it, or design against it — read it directly. The split is locate-and-report (delegable) vs. read-and-reason (not).

## Code Review

- After writing or modifying code, run `/code-review` before presenting the code to the user. If the review finds issues, fix them first, then present the final version.

## Plan Review

- After writing or modifying an implementation plan, run `/plan-review` before presenting the plan to the user. If the review finds issues, address them first, then present the final version.

## Pre-Handoff Review

- Before pushing a branch with an open PR or handing off to a human reviewer, run `/ready-for-review`. If the review finds issues, fix them first, then push or hand off.

## Agent Briefing

- When spawning sub-agents with `isolation: "worktree"`, do NOT include an explicit `Working directory: /path/to/repo` line. The harness sets the agent's CWD to the isolated worktree automatically; naming the main repo path causes the agent to use `git -C <main-path>` operations that bypass isolation and mutate the main working tree directly.
- Before delegating execution to a sub-agent from a session in plan mode, call `ExitPlanMode` in the parent first. A spawned sub-agent receives the plan-mode system-reminder and a typical agent honors it — declining to execute and returning a plan file even when the prompt says "execute, do not plan." This is the agent obeying an instruction, not a hard harness block, so the symptom is a polite refusal, not a tool error. Exit plan mode in the parent before delegating execution work.
- In a repo with worktree enforcement opt-in (`.claude/worktree-required` committed), Edit and Write must also target the worktree path — the hook blocks main-tree file writes, but resolving paths to `.claude/worktrees/<branch>/...` up front avoids the round-trip denial.
- `isolation: "worktree"` is an **ephemeral-isolation** primitive, not a feature-branch primitive. The harness creates the worktree on a harness-generated branch name (`worktree-agent-<hash>`), so the `branch-creation` skill never runs. Use it only for work that will NOT become a named PR branch — parallel exploration, reviewer/check-runner agents, throwaway spikes. For PR-bound implementation work, create the worktree yourself first: pick a slug per the `branch-creation` skill, run `git worktree add .claude/worktrees/<slug> -b <slug>` (allowed on the main tree even under worktree enforcement), then dispatch the agent **without** `isolation: "worktree"`, naming that worktree path as its working directory.

## Model Routing

- **Opus:** judgment-heavy reasoning, plan-mode planning, and parent-dispatcher orchestration.
- **Sonnet (default):** all code reading, code writing, and specialist reviewer agents. Enforced via `model: sonnet` frontmatter in each agent file.
- **Haiku:** narrow, deterministic skills only. Never for code authoring or judgment.

## Safety

- Never run sudo commands directly.
- Never commit secrets, credentials, API keys, or large binary assets to repositories.
- Never use the Read tool on files likely to contain secrets (`.env`, `.claude.json`, `credentials.json`, similar). Reading pulls the secret into the conversation context. When you need to inspect such a file, give the user a shell command (`cat`, `grep`, `jq`) to run via `!` instead.
- Apply the **principle of least privilege** when recommending or provisioning credentials, roles, or grants: default to the narrowest scope the operation actually needs, not the broadest one available. Account-wide secrets, root tokens, and admin scopes are never the default.
- Never write `~/.claude/*-markers/*` by hand. Each review skill writes its own marker directory (`/code-review` → `review-markers/`, `/plan-review` → `plan-review-markers/`, etc.) when a review passes, and pre-commit hooks gate on their presence. If a commit is blocked, run the review skill the hook names; if the skill is harness-blocked, spawn a subagent that can run it. A general "ship it" instruction is not authorization to forge a marker.
- If a skill's active-bypass gate refuses to release after the skill has finished, run `~/.claude/scripts/marker.sh clear-stale` to evict orphaned active markers from dead sessions.
- Don't add globs (`Bash(pytest *)`, `Bash(npm run *)`) to `permissions.allow`. Globs widen the surface to flag injection, command chaining, and shell-expansion attacks — see `claude/.claude/skills/review-permissions/SKILL.md` checklist items 1–9. Use exact-match rules (`Bash(pytest)`, `Bash(npm run verify)`) instead.
- `.claude/settings.json` vs `.claude/settings.local.json` scoping: project-shared rules (permissions, hooks, skillOverrides that every engineer on the project needs) go in committed `.claude/settings.json`. Personal-machine-only rules (per-machine tooling, individual preferences) go in gitignored `.claude/settings.local.json`. Before adding a rule, ask: would another engineer on this project need this? If yes → `settings.json`. If no → `settings.local.json`.

## Code Comments and Durable Documentation

Code comments and durable in-repo documentation (REFERENCES.md, doc files, README sections) must be readable by a future contributor who has not read the PR description, commit message, or planning document. In particular:

- **No PR-defined terminology** (e.g., "Defense A", "Action 6", "Pattern C"). If a label is meaningful it must be defined in code or named explicitly — not in a comment or doc that depends on context outside the file.
- **No "used to be X" / "was Y before"** framing. The rationale-vs-prior-version belongs in the commit message or PR body.
- **Self-test:** if you can't write the content such that it survives the PR being merged and the description being lost, don't write it. Move the rationale to the commit message instead.

## Output Preferences

If `~/.claude/output-preferences.md` exists, read it at session start and apply those preferences for response tone and formatting. Cap at 50 lines.
