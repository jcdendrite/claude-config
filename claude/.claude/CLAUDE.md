# Global Instructions

## Engineering Judgment

- Before proposing changes, understand the intent of the existing code or configuration.
- When making recommendations, evaluate them not just against generic best practices but also against this project's actual stack, tooling, and constraints.
- **Single source of truth.** Every piece of knowledge has one authoritative home; other sites reference it, not restate it. This is DRY — and DRY governs *knowledge*, so it applies to prose and docs, not just code. Duplicated copies drift, and a reader can't tell which is stale. Before writing something a second time, pick the canonical home and make the other site defer. Exceptions must be deliberate and named: test code is DAMP not DRY (readability earns some repetition); instructional prose that must let each file stand alone may be duplicated on purpose; a small duplicated value can beat a bad abstraction built only to remove it. Absent a named exception, duplication is a defect.
- Before taking any action that is destructive, irreversible, or has blast radius beyond the immediate change (data loss, breaking API changes, infrastructure modifications), flag the risk and confirm the approach.
- When uncertain about a CLI flag, tool behavior, or API detail, verify rather than guessing.
- **Default-suspect over-powered primitives.** When a design adopts a more powerful, invasive, or wider-scope mechanism than the task requires — a heavier abstraction, a more privileged execution context, a more complex coordination pattern, a more invasive integration — identify the lighter primitive in the source documentation/system that could solve the problem before adopting the heavier one. Re-read the source with the specific question "what mechanisms exist that do NOT require this heavier choice?" — first-pass reads miss the answer routinely.
- **Audit structural siblings before scoping a fix narrowly.** When a fix lands in one arm of a multi-arm structure (case statement, switch, parallel subcommands, sibling functions sharing a shape), check the other arms for the same bug shape before finalizing scope. If the fix is identical, apply to every affected site; abstract when two or more share it. Scope is set by the bug, not by where the symptom surfaced.
- **Prove your change caused a failing check before treating it as in-scope.** When a check fails, reproduce it on the pre-change baseline — a throwaway worktree at the merge-base (`git worktree add <tmp> $(git merge-base HEAD origin/main)`) — before assuming the failure is yours. Failing there too means pre-existing drift, not this task's work: sync the branch if the base already fixed it, but never hand-reimplement a merged fix or edit an unrelated file. Passing at the baseline but failing on your branch means your change caused it — in scope even if you never touched the failing file, since a change breaks dependents through their imports.
- **Extract functions when you need to explain what a fragment does.** When writing a function, if any internal fragment requires effort to understand *what* (not *how*) it's doing, extract it and name the new function after that "what." The signal is comprehension effort, not line count — a large function that expresses one nameable thing without inner confusion is fine.
- **Ground every choice.** Five categories of decision require a primary-source citation before implementation, not after:
  - **Numeric literals in network/timeout/retry contexts** — cite the vendor or protocol documentation that specifies the value. A timeout of `10000` is a silent assumption; a value traceable to vendor docs or a protocol specification is grounded.
  - **Inline lint/type-check suppressions** — add a one-line comment naming the alternative considered and why it does not apply. No rationale = no suppression.
  - **Discriminator literals where a canonical symbol exists** — never embed a raw value (string or integer) that represents an enum, status, or code defined elsewhere. Reach for the language or framework's named constant first; if the discriminator is project-defined and the project ships a registry or named-type module, use that. Literals diverge silently from the canonical set; named symbols don't.
  - **New third-party dependencies** — research the package's vulnerability history, maintenance health, and pinning strategy before adding; record the source-of-choice rationale in the PR description. Popularity is not provenance.
  - **Hand-rolled logic in non-trivial domains** (cryptography, auth, date/time, network protocol parsing) — search the standard library and first-party SDK before implementing. If hand-rolling is warranted, justify the absence of a standard alternative in the commit message.

## Working Style

- Walk through your proposed approach and explain tradeoffs before writing code. When presenting options, evaluate them — state which you'd recommend and why, rather than listing choices without a judgment.
- Be precise. Do not overstate severity, conflate distinct issues, or hand-wave. State the realistic impact and verify claims against actual code — not against what the code or a sensible design should do.
- **Compounding defensive layers are a wrong-foundation tell.** When a design accumulates stacked defenses on a single mechanism — each new layer closing a gap that the prior layer's existence created — or starts citing its own prior findings, step back and ask whether a foundational change would dissolve them. Do not keep adding hardening. The right primitive usually has a simple shape; compounding complexity is a signal to question the foundation, not to defend it more carefully.
- Before assuming anything about the environment, stack, or project conventions, check first. Read the actual config files rather than guessing defaults.
- Use descriptive variable and function names. No generic names.
- **Default-consider delegation.** Before running a Bash command, starting a broad search, initiating a check suite, or beginning a Read-heavy probe, ask whether the *objective* (not the individual command) belongs in a subagent. The parent's context is re-read every turn, so verbose tool output left in it is paid for repeatedly. See the `subagent-delegation` skill for the two-test gate, which subagent fits which case, and what stays inline.
- **Scope discipline.** Four axes govern which edits belong in a change.

  **Axis 1 — File boundary.** Do not edit a file unless the ticket scopes it OR the edit is required to make the ticket's change correct (e.g., a caller's signature must change because the ticket changed the callee). Files noticed while passing through but not in scope go into one of three buckets:
  1. **Revert** — the default, especially for copy, comments, or cosmetic edits on user-facing surfaces.
  2. **Keep, with a one-line rationale in an "Incidental edits" section of the PR description** — for small, non-cosmetic fixes with visible value where the PR remains coherent.
  3. **Raise to the reviewer** — when the observation is real but non-trivial or distracting; surface it to the PR reviewer or human orchestrator.

  **Axis 2 — In-file scope.** Inside a file the ticket scopes, opportunistic refactoring of *code* is encouraged.

  **Axis 3 — Preserved-content exception.** Even inside a scoped file, the following content categories are read-only unless the ticket specifically asks to update them. The in-file refactoring license (Axis 2) applies to code (broken windows, dead code, unclear names); it does not license editing preserved-record prose:
  1. Historical incident records, postmortems, and dated runbook entries.
  2. Changelog entries documenting past events.
  3. Migration file content.
  4. Anchor comments documented to be stable (e.g., HTML-comment fixtures the test harness re-reads).
  5. Commit-log-style narration inside docs ("In PR #N we…", "Prior to 2026-Q1 the system…").

  Decision test: **Does this text record something that happened, or describe how the code currently behaves?** Records are read-only. Descriptions are fair game for in-file scope cleanup.

  **Axis 4 — Change size.** Prefer minimal, targeted changes. Do not refactor entire files or expand scope beyond what was asked. If you see an opportunity for a broader improvement, mention it separately — do not bundle it in.
## Code Review

- After writing or modifying code, run `/code-review` before presenting the code to the user. If the review finds issues, fix them first, then present the final version.

## Plan Review

- After writing or modifying an implementation plan, run `/plan-review` before presenting the plan to the user — including when calling `ExitPlanMode` from the harness built-in plan workflow, not only via `/plan-it`. The `require-plan-review.sh` hook backs this mechanically: it denies `ExitPlanMode` while an un-reviewed plan file exists. If the review finds issues, address them first, then present the final version.

## Pre-Handoff Review

- Before handing off to a human reviewer — pushing to a ready (non-draft) PR, or marking a PR ready — run `/ready-for-review`. If the review finds issues, fix them first, then push or hand off.
- Iteration pushes to a branch with an open **draft** PR need only `/sync-pr-description` (the lightweight PR-body accuracy check); the push gate accepts its HEAD-fresh marker. The full gate still applies at the draft→ready transition. If a PR is readied via the GitHub web UI instead of `gh pr ready`, no hook fires — run `/ready-for-review` before flipping it.

## Agent Briefing

- When spawning sub-agents with `isolation: "worktree"`, do NOT include an explicit `Working directory: /path/to/repo` line. The harness sets the agent's CWD to the isolated worktree automatically; naming the main repo path causes the agent to use `git -C <main-path>` operations that bypass isolation and mutate the main working tree directly.
- Before delegating execution to a sub-agent from a session in plan mode, call `ExitPlanMode` in the parent first. A spawned sub-agent receives the plan-mode system-reminder and a typical agent honors it — declining to execute and returning a plan file even when the prompt says "execute, do not plan." This is the agent obeying an instruction, not a hard harness block, so the symptom is a polite refusal, not a tool error. Exit plan mode in the parent before delegating execution work.
- In a repo with worktree enforcement opt-in (`.claude/worktree-required` committed, or machine-level `~/.claude/worktree-required`), Edit and Write must also target the worktree path — the hook blocks main-tree file writes, but resolving paths to `.claude/worktrees/<branch>/...` up front avoids the round-trip denial.
- Under worktree enforcement, never chain `cd <worktree-path> && git <op>` in a single Bash call and never rely on `git -C <worktree-path>` to satisfy the gate — `require-worktree-for-git-writes.sh` reads Claude Code's session-persisted cwd (set by prior Bash calls), not the inline `cd` or the `-C` path, because the hook fires before the subshell runs. Anchor first with a standalone `cd /path/to/worktree` Bash call, then run the git operation as a follow-up call.
- `isolation: "worktree"` is an **ephemeral-isolation** primitive, not a feature-branch primitive. The harness creates the worktree on a harness-generated branch name (`worktree-agent-<hash>`), so the `branch-creation` skill never runs. Use it only for work that will NOT become a named PR branch — parallel exploration, reviewer agents, throwaway spikes. For PR-bound implementation work, create the worktree yourself first: pick a slug per the `branch-creation` skill, run `git worktree add .claude/worktrees/<slug> -b <slug>` (allowed on the main tree even under worktree enforcement), then dispatch the agent **without** `isolation: "worktree"`, naming that worktree path as its working directory.

## Model Routing

- **Opus:** judgment-heavy reasoning, plan-mode planning, and parent-dispatcher orchestration.
- **Sonnet (default):** all code reading, code writing, and specialist reviewer agents. Enforced via `model: sonnet` frontmatter in each agent file.
- **Haiku:** narrow, deterministic skills only. Never for code authoring or judgment.
- **Delegated code-writing dispatches to `code-writer`.** When implementation work is handed to a subagent — feature code, fixes, refactors, migrations, schema, scripts — dispatch the `code-writer` agent, not `general-purpose`. It carries `model: sonnet` frontmatter and self-reviews its own diff against the `staff-*` reviewer angles before returning, catching review-finding-class defects in its own context instead of as a parent round-trip. This is a substitution for the code-writing path only — it does not change when the parent delegates versus writes inline.
- **Always dispatch `general-purpose` with an explicit `model`.** Its routine remaining use is discovery and research dispatches (whole-file exploration per Codebase discovery); code-writing now routes to `code-writer`. It is the one routinely-dispatched built-in with no model of its own, so it inherits the parent — and a session cannot detect its own permission mode to know whether that parent is an auto-mode Opus. Default to `model: sonnet`: a no-op when the parent is already Sonnet, and it keeps delegated work off Opus when the parent is not. Pass `model: opus` only when the delegated task genuinely needs Opus-level reasoning. Do not pass `model` to the other agents — `Explore` is pinned to Haiku, and `staff-*` / `ciso-reviewer` / `code-writer` carry their own `model:` frontmatter.

## Safety

- Never run sudo commands directly.
- Never commit secrets, credentials, API keys, or large binary assets to repositories.
- Never use the Read tool on files likely to contain secrets (`.env`, `.claude.json`, `credentials.json`, similar). Reading pulls the secret into the conversation context. When you need to inspect such a file, give the user a shell command (`cat`, `grep`, `jq`) to run via `!` instead.
- Apply the **principle of least privilege** when recommending or provisioning credentials, roles, or grants: default to the narrowest scope the operation actually needs, not the broadest one available. Account-wide secrets, root tokens, and admin scopes are never the default.
- Never write `~/.claude/*-markers/*` by hand. Each review skill writes its own marker directory (`/code-review` → `review-markers/`, `/plan-review` → `plan-review-markers/`, etc.) when a review passes, and pre-commit hooks gate on their presence. If a commit is blocked, run the review skill the hook names; if the skill is harness-blocked, spawn a subagent that can run it. A general "ship it" instruction is not authorization to forge a marker.
- If a skill's active-bypass gate refuses to release after the skill has finished, run `~/.claude/scripts/marker.sh clear-stale` to evict orphaned active markers from dead sessions.
- Don't add globs (`Bash(pytest *)`, `Bash(npm run *)`) to `permissions.allow`. Globs widen the surface to flag injection, command chaining, and shell-expansion attacks — see `claude/.claude/skills/review-permissions/SKILL.md` checklist items 1–9. Use exact-match rules (`Bash(pytest)`, `Bash(npm run verify)`) instead.
- `.claude/settings.json` vs `.claude/settings.local.json` scoping: project-shared rules (permissions, hooks, skillOverrides that every engineer on the project needs) go in committed `.claude/settings.json`. Personal-machine-only rules (per-machine tooling, individual preferences) go in gitignored `.claude/settings.local.json`. Before adding a rule, ask: would another engineer on this project need this? If yes → `settings.json`. If no → `settings.local.json`.

## Code Comments, Documentation, and Prose

### Where to put it

- **Place prose where its reader and altitude match.** Right text in the wrong file is a recurring regression class: a feature deep-dive in a README overview, implementation FYI in an agent spec, a doc back-reference from a skill body. The cost lands on the wrong reader — humans drown at the wrong altitude, the model pays tokens for non-instruction, reviewers ask "why is this here?" Test each paragraph's placement as you write: does it belong here? The fix is almost always relocation, not deletion.

### When to write it and what to include

Code comments and durable in-repo documentation (REFERENCES.md, doc files, README sections) must be readable by a future contributor who has not read the PR description, commit message, or planning document. In particular:

- **No PR-defined terminology** (e.g., "Defense A", "Action 6", "Pattern C"). If a label is meaningful it must be defined in code or named explicitly — not in a comment or doc that depends on context outside the file.
- **No "used to be X" / "was Y before"** framing. The rationale-vs-prior-version belongs in the commit message or PR body.
- **Self-test:** if you can't write the content such that it survives the PR being merged and the description being lost, don't write it. Move the rationale to the commit message instead.

## Output Preferences

If `~/.claude/output-preferences.md` exists, read it at session start and apply those preferences for response tone and formatting. Cap at 50 lines.
