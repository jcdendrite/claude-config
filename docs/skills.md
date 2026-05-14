# Skill reference

Full descriptions for skills, slash commands, and project-scoped plugins in this repo. For the pipeline overview and which hook gates each transition, see the [README](../README.md#workflow).

## Skills (slash commands)

- **`/plan-it`** — produce an implementation plan in `.claude/plans/<topic-slug>.md` through discovery, codebase exploration, clarifying questions, and architecture design; hands off to `/plan-review`.
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/ready-for-review`** — pre-handoff gate: verifies tests/lint/typecheck, runs `/code-review` against the cumulative PR diff (all commits vs default branch), and syncs the PR description; required before `git push` on a branch with an open PR.
- **`/review-permissions`** — security audit of `permissions.allow` rules with a 21-item checklist.
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/branch-creation`** — naming conventions (`<TICKET-ID>/<topic-slug>` for ticketed projects, `<topic-slug>` alone otherwise), anti-patterns to reject (tracker `<user>/` defaults), and branching from a fresh default-branch tip.
- **`/git-feature-branch-sync`** — decision framework for keeping a feature branch current with the default branch: when to rebase-and-force-push vs merge-in, and how to force-push safely (`--force-with-lease` vs `--force-if-includes`).
- **`/git-state-safety`** — safely inspecting other branches when the working tree is in a fragile state (mid-merge, mid-rebase, mid-cherry-pick), avoiding the silently-corrupted-index failure mode where a diagnostic `git checkout <ref> -- <path>` overwrites a partially-resolved merge, and recovering from bad merges that were already committed.
- **`/test-conventions`**, **`/test-evaluation`** — test authoring and audit guidance.
- **`/config-environments`** — designing configuration that differs across environments (dev, staging, production): env var naming, credential isolation, secrets provisioning, and the anti-patterns that reintroduce tight coupling.
- **`/sql-query-conventions`** — read-path conventions for SQL and PostgREST/Supabase queries: pagination, limits, N+1 avoidance, batch-size ceilings, explicit column selection.
- **`/ai-instruction-and-memory-files`** — how AI coding agents load instruction files (CLAUDE.md, AGENTS.md, Cursor rules, Lovable knowledge) and Claude Code auto-memory: precedence, duplication rules, length targets, import patterns.
- **`/verify-primary-sources`** — when web research informs a code or design decision, read the primary documentation directly rather than trusting agent summaries or secondary sources.
- **`/handoff`** — write a structured cross-session handoff file at `/tmp/<slug>-handoff.md` capturing goal, status, next step, modified files, active markers, open questions, and the resume incantation. User-invoked only — implemented as a slash command at `claude/.claude/commands/handoff.md`, not a skill, to keep it out of the always-loaded skills catalog.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context. User-invoked only — implemented as a slash command at `claude/.claude/commands/read-docx-comments.md`. Run irregularly enough that always-loaded skill description budget is not justified.

Each skill lives in `claude/.claude/skills/<skill-name>/SKILL.md`. A skill directory may also contain co-located auxiliary files — see architecture notes below for the two distinct roles they play. Skills that primarily apply to this repo's own workflow (editing SKILL.md files, authoring hooks) live as project-scoped plugins instead — see [Project-scoped plugins](#project-scoped-plugins) below.

## Bundled skills disabled by default

Claude Code ships a set of bundled skills alongside its custom-skill support. Seven bundled skills are disabled in this repo's `settings.json` via `skillOverrides: "off"`. The reason in each case is either redundancy with a more capable repo-specific skill or low utility relative to the description-budget cost. All skill descriptions contribute to the `skillListingBudgetFraction` context allocation; `/doctor` reports a warning when the budget overflows and descriptions are dropped. The disabled skills freed budget for the always-relevant `user-invocable: false` skills that auto-trigger during the engineering workflow.

| Bundled skill | Why disabled |
|---|---|
| `/claude-api` | Only relevant when building Claude API / Anthropic SDK apps. Out of scope for this repo's tooling work. |
| `/fewer-permission-prompts` | One-time setup utility; rarely fires in established sessions. |
| `/init` | One-time setup; CLAUDE.md is already established, and `/init` advice may conflict with repo conventions. |
| `/keybindings-help` | One-time setup utility; rarely fires in established sessions. |
| `/review` | "Review a PR" — superseded by `/code-review` (specialist reviewer routing) and `/ultrareview`. |
| `/security-review` | Superseded by `/code-review` specialist routing (ciso-reviewer agent fires automatically). |
| `/simplify` | Overlaps with `/code-review`, which spins up domain specialists and produces a structured checklist. |

### Re-enable for your session

Via `/skills` UI: open `/skills`, highlight the skill, press `Space` to cycle to `"on"`, then `Enter`. This writes to `~/.claude/settings.local.json` (gitignored; persists across sessions).

Persistent per-user: add to `~/.claude/settings.local.json`:

```json
{
  "skillOverrides": {
    "claude-api": "on"
  }
}
```

`settings.local.json` overrides `settings.json` at the same scope; the repo's `"off"` entry does not win. Remove the entry (or set to `"on"`) to restore. Reference: [Claude Code skills — Override skill visibility from settings](https://code.claude.com/docs/en/skills.md).

## Skill architecture notes

- **Co-located files come in two roles, neither auto-loaded.** `REFERENCES.md` is an edit-time reference (canonical URLs, key quotes, framework notes that informed the skill's rules) — read by humans and agents when updating the skill, not at runtime. A runtime auxiliary file (e.g., `plan-review/ROUTING.md`) is read by the skill itself via the Read tool at runtime. The 200-line cap (`check-skill-length.sh`) marks the degradation point — shorten first, do not extract: an auxiliary adds Read-tool indirection without reducing context cost. `plan-review/ROUTING.md` is a last-resort exception (content could not be cut; `require-routing-read.sh` and `log-routing-read.sh` compensate for the indirection). Both types belong to one skill and are not shared across skills.
- **Frontmatter has no inclusion fields.** There are no `includes:`, `import:`, or `extends:` frontmatter keys — skills do not support partial inclusion.
- **`@path` import syntax is for `CLAUDE.md` only.** The `@path/to/file` import pattern that works in `CLAUDE.md` files is not supported in `SKILL.md`.
- **Duplicate rule text across skills intentionally.** When two skills need the same rule, copy it into both — do not extract it into a `_shared/` partial or similar abstraction. Duplication is deliberate: it keeps each skill independently readable and avoids brittle cross-skill coupling. If you find yourself wanting a shared partial, that is a signal to reconsider whether the skills should be merged, not a signal to add an include mechanism.

## Project-scoped plugins

Two skills that primarily apply to this repo's own workflow — editing `SKILL.md` files and authoring hook scripts — live as project-scoped plugins in `plugins/` rather than stowed user-scope skills. This keeps them out of the always-loaded skill catalog for downstream projects that stow claude-config but rarely touch these surfaces.

| Plugin | What it provides | When to install |
|---|---|---|
| `skill-review@claude-config` | Behavioral-equivalence audit for `SKILL.md` changes; gates `git commit` when staged changes include a `SKILL.md` | Repos that author their own `SKILL.md` files |
| `claude-hook-review@claude-config` | Review playbook for `.claude/hooks/*.sh` scripts and `settings.json` hook entries | Repos that author their own hook scripts |

Both plugins are enabled automatically in claude-config sessions via `enabledPlugins` in `.claude/settings.json`. For this to work, the claude-config marketplace must be registered on the machine:

```bash
claude plugin marketplace add ~/MyCode/claude-config   # adjust to your actual checkout path
```

Then Claude Code will resolve the plugins from the project settings. To install either plugin in a downstream project:

```bash
claude plugin install skill-review@claude-config --scope project
claude plugin install claude-hook-review@claude-config --scope project
```

## Tuning the skill-listing budget for your project

Claude Code allocates 1% of the context window for skill descriptions by default (`skillListingBudgetFraction: 0.01`). Run `/doctor` to see current usage; a warning appears when descriptions are dropped.

After this repo's plugin restructure, stowed skills from claude-config use less budget than before. If a downstream project still sees truncation — because it has many of its own project-specific skills — raise the cap locally in `~/.claude/settings.local.json` (create if absent; gitignored, per-user):

```json
{
  "skillListingBudgetFraction": 0.02
}
```

`settings.local.json` overrides `settings.json` at the same scope, so this raise applies only to the user who adds it without forking the stowed config. Reference: [Claude Code settings — skillListingBudgetFraction](https://code.claude.com/docs/en/settings).

## Project-specific layers

`/plan-review` and `/code-review` auto-load a project-specific layer if one exists in the consuming repo — so a project can extend the base checklist without forking the public skill body.

- **Location:** `.claude/skills/code-review-<project>/SKILL.md` or `.claude/skills/plan-review-<project>/SKILL.md`, placed in the consuming repo. The `<project>` token is freeform; only the prefix (`code-review-` or `plan-review-`) is load-bearing.
- **Frontmatter:** match the shape of any skill in `claude/.claude/skills/` (`name`, `description`, `user-invocable`). The parent invokes the layer via the Skill tool — not via description-based auto-trigger, which doesn't fire from inside a running skill (design rationale in [`docs/design-decisions.md`](design-decisions.md)). Set `disable-model-invocation: true` so the layer's description is excluded from the always-loaded skill-listing budget; Skill-tool invocation from the parent still works. (Suppression is only observable from a fresh CLI session — subagents inherit the parent session's listing.)
- **Behavior:** glob runs from the repo root (`git rev-parse --show-toplevel`). Single match → invoked and merged into the base checklist. Multiple matches → review stops — that's a config error in the consuming project, not a review item the skill resolves. Zero matches → proceeds without a layer.
