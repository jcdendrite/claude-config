# Skill reference

Full descriptions for skills, slash commands, and project-scoped plugins in this repo. For the pipeline overview and which hook gates each transition, see the [README](../README.md#workflow).

## Skills (slash commands)

- **`/plan-it`** — produce an implementation plan in `.claude/plans/<topic-slug>.md` through discovery, codebase exploration, clarifying questions, and architecture design; hands off to `/plan-review`.
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/ready-for-review`** — pre-handoff gate: verifies tests/lint/typecheck, runs `/code-review` against the cumulative PR diff (all commits vs default branch), and syncs the PR description; required before `git push` on a branch with an open PR.
- **`/review-permissions`** — security audit of `permissions.allow` rules against a structured checklist.
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/subagent-delegation`** — when to dispatch work to a subagent rather than running it inline: the two-test gate (output test, judgment test), which subagent fits which case (`check-runner` for check suites, `Explore` / `general-purpose` for codebase discovery, `code-writer` for implementation work), and what stays inline (Edit/Write, single targeted reads, content you must reason over line by line). Auto-triggers on the relevant dispatch decisions; the rationale (parent context is re-read every turn) lives in the skill body, not the description.
- **`/branch-creation`** — naming conventions (`<TICKET-ID>/<topic-slug>` for ticketed projects, `<topic-slug>` alone otherwise), anti-patterns to reject (tracker `<user>/` defaults), and branching from a fresh default-branch tip.
- **`/git-feature-branch-sync`** — decision framework for keeping a feature branch current with the default branch: when to rebase-and-force-push vs merge-in, and how to force-push safely (`--force-with-lease` vs `--force-if-includes`).
- **`/git-state-safety`** — safely inspecting other branches when the working tree is in a fragile state (mid-merge, mid-rebase, mid-cherry-pick), avoiding the silently-corrupted-index failure mode where a diagnostic `git checkout <ref> -- <path>` overwrites a partially-resolved merge, and recovering from bad merges that were already committed.
- **`/test-conventions`** — see [Skills available by name](#skills-available-by-name-no-description-budget-cost).
- **`/test-evaluation`** — audit guidance for evaluating existing test suites.
- **`/config-environments`** — designing configuration that differs across environments (dev, staging, production): env var naming, credential isolation, secrets provisioning, and the anti-patterns that reintroduce tight coupling.
- **`/sql-query-conventions`** — see [Skills available by name](#skills-available-by-name-no-description-budget-cost).
- **`/ai-instruction-and-memory-files`** — how AI coding agents load instruction files (CLAUDE.md, AGENTS.md, Cursor rules, Lovable knowledge) and Claude Code auto-memory: precedence, duplication rules, length targets, import patterns.
- **`/verify-primary-sources`** — when web research informs a code or design decision, read the primary documentation directly rather than trusting agent summaries or secondary sources.
- **`/handoff`** — write a structured cross-session handoff file at `/tmp/<slug>-handoff.md` capturing goal, status, next step, modified files, active markers, open questions, and the resume incantation. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only` — see [Skills available by name](#skills-available-by-name-no-description-budget-cost).
- **`/brief`** — write a cold-start task briefing at `/tmp/<slug>-task.md` for a fresh session to pick up known, well-scoped work (abandoned PR, surfaced follow-up, settled-scope ticket) — covers goal, scope, anchors, current state, decisions to make, steps to ship, out of scope. Distinct from `/handoff`, which captures mid-flight session state; `/brief` is for work the current session is *not* going to do. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/transcript-analysis`** — reference guidance for the `transcript-analysis.py` toolkit: which subcommand answers which analysis question, how to read `fail-seq` convergence-vs-thrashing output, and the measurement caveats. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/error-handling`** — eight-principle error-handling standard: single code namespace, RFC 9457–derived envelope, developer-only message fields, and call-site anti-patterns. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.

Unlike the four workflow-utility name-only skills (brief, handoff, read-docx-comments, transcript-analysis), error-handling, test-conventions, and sql-query-conventions are knowledge-domain skills kept name-only because their trigger surfaces are too broad to scope reliably — reached by name from the review skills and by `Read` from reviewer agents.

Each skill lives in `claude/.claude/skills/<skill-name>/SKILL.md`. A skill directory may also contain co-located auxiliary files — see architecture notes below for the two distinct roles they play. Skills that primarily apply to this repo's own workflow (editing SKILL.md files, authoring hooks) live as project-scoped plugins instead — see [Project-scoped plugins](#project-scoped-plugins) below.

## Skills available by name (no description budget cost)

Seven repo skills use `skillOverrides: name-only` — the model can invoke them when referenced by name in conversation, but their descriptions are excluded from the always-loaded listing budget. These skills are also slash-invocable directly. Requires Claude Code **v2.1.129+**; on older Claude Code versions (pre-v2.1.129) the override is silently ignored and these skills fall back to `on` (description loaded). The four workflow utilities carry no TRIGGER blocks so auto-trigger is not a concern; the three knowledge-domain skills carry TRIGGER blocks and may fire via description match on older versions.

| Skill | Role |
|---|---|
| `/brief` | Cold-start task briefing for a fresh session to pick up well-scoped work |
| `/handoff` | Cross-session handoff file capturing mid-flight session state |
| `/read-docx-comments` | Extract comments from `.docx` files (Google Docs / Word feedback) |
| `/transcript-analysis` | Reference guide for the `transcript-analysis.py` toolkit |
| `/error-handling` | Canonical error-handling standard: code namespace, RFC 9457–derived envelope, developer-only message fields, call-site anti-patterns |
| `/test-conventions` | Test authoring conventions: pyramid shape, fixture design, naming, regression-test intent; reached by name from code-review and by Read from reviewer agents |
| `/sql-query-conventions` | Read-path SQL conventions: explicit limits, N+1 avoidance, explicit column selection; reached by name from code-review and by Read from reviewer agents |

The `skillOverrides` setting controls skill visibility from settings rather than frontmatter. The four values (Claude Code v2.1.129+):

| Override value | Listed to model | Model can invoke | Description in budget | In `/` menu |
|---|---|---|---|---|
| `on` (default) | name + description | yes (auto-triggers) | yes | yes |
| `name-only` | name only | yes, by name | no | yes |
| `user-invocable-only` | hidden | no | no | yes |
| `off` | hidden | no | no | no |

Source: [Claude Code settings — skillOverrides](https://code.claude.com/docs/en/settings) · [Override skill visibility from settings](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings).

## Bundled skills disabled by default

Claude Code ships a set of bundled skills alongside its custom-skill support. Twelve bundled skills are disabled in this repo's `settings.json` via `skillOverrides: "off"`. The reason in each case is either redundancy with a more capable repo-specific skill or low utility relative to the description-budget cost. All skill descriptions contribute to the `skillListingBudgetFraction` context allocation; `/doctor` reports a warning when the budget overflows and descriptions are dropped. The disabled skills freed budget for the always-relevant `user-invocable: false` skills that auto-trigger during the engineering workflow.

| Bundled skill | Why disabled |
|---|---|
| `/claude-api` | Only relevant when building Claude API / Anthropic SDK apps. Out of scope for this repo's tooling work. |
| `/fewer-permission-prompts` | One-time setup utility; rarely fires in established sessions. |
| `/init` | One-time setup; CLAUDE.md is already established, and `/init` advice may conflict with repo conventions. |
| `/keybindings-help` | One-time setup utility; rarely fires in established sessions. |
| `/loop` | Recurring-interval task automation. Not part of this repo's skill-authoring / review-pipeline workflow. |
| `/review` | "Review a PR" — superseded by `/code-review` (specialist reviewer routing) and `/ultrareview`. |
| `/run` | Launches and drives "this project's app" — claude-config is dotfiles, no app to drive. Out of scope. |
| `/schedule` | Cron-scheduled remote agents (routines). Not part of this repo's skill-authoring / review-pipeline workflow. |
| `/security-review` | Superseded by `/code-review` specialist routing (ciso-reviewer agent fires automatically). |
| `/simplify` | Overlaps with `/code-review`, which spins up domain specialists and produces a structured checklist. |
| `/update-config` | Bundled generic settings.json editor. Redundant with `/review-permissions` (permissions.allow), `/claude-hook-review` (hooks), `/skill-review` (skill bodies), and `/agent-review` (agent bodies); remaining env/model/theme edits are trivial direct file changes. |
| `/verify` | Manual-verification skill that drives the app to confirm a change. Same scope mismatch as `/run` — claude-config skills and hooks are verified via `pytest claude/.claude/`. |

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

## Skill evals

`evals/run_skill_evals.py` is a local harness that measures each skill's
`trigger-cases.json` against its declared TRIGGER / DO NOT TRIGGER conditions —
either by observing live auto-dispatch (`runtime`) or by asking a model to
classify which skill a query should match (`description-fidelity`). See
`evals/README.md` for usage and the two-method model.

## Skill architecture notes

- **Co-located files come in two roles, neither auto-loaded.** `REFERENCES.md` is an edit-time reference (canonical URLs, key quotes, framework notes that informed the skill's rules) — read by humans and agents when updating the skill, not at runtime. A runtime auxiliary file (e.g., `plan-review/ROUTING.md`) is read by the skill itself via the Read tool at runtime. The default 200-line cap (`check-skill-length.sh`) sits below Anthropic's documented 500-line ceiling — claude-config defaults lower because cumulative skill surface drives session token cost, and anecdotal reports suggest comprehension degrades as a single skill body grows. Shorten first, do not extract: an auxiliary adds Read-tool indirection without reducing context cost. The hook carves out the full 500-line ceiling for `/code-review` and `/plan-review` — their item-ownership and routing tables are genuinely load-bearing and resist trimming; that exception is reserved for skills in the same class, not as a routine response to hitting the cap. `plan-review/ROUTING.md` is a separate last-resort exception (content could not be cut; `require-routing-read.sh` and `log-routing-read.sh` compensate for the indirection). Both file roles belong to one skill and are not shared across skills.
- **Frontmatter has no inclusion fields.** There are no `includes:`, `import:`, or `extends:` frontmatter keys — skills do not support partial inclusion.
- **`@path` import syntax is for `CLAUDE.md` only.** The `@path/to/file` import pattern that works in `CLAUDE.md` files is not supported in `SKILL.md`.
- **Duplicate rule text across skills intentionally.** When two skills need the same rule, copy it into both — do not extract it into a `_shared/` partial or similar abstraction. Duplication is deliberate: it keeps each skill independently readable and avoids brittle cross-skill coupling. If you find yourself wanting a shared partial, that is a signal to reconsider whether the skills should be merged, not a signal to add an include mechanism.
- **Bare-name `Skill()` calls resolve against plugin-namespaced skills.** When a skill's identifier in the available-skills listing is `plugin-name:skill-name`, invoking `Skill(skill="skill-name")` still resolves — the harness accepts the bare name. Prose pointers in calling skills (e.g., "invoke the `skill-name` skill") do not need updating when a skill moves to plugin form.
- **When to gate a review skill with a pre-commit hook.** Gate skills whose target files carry always-loaded context budget on every session or route dispatcher decisions — getting them wrong is high-stakes, so `/skill-review` is enforced by `require-skill-review.sh`. Do not gate skills whose target files are lazy-loaded — body cost is paid only when the harness dispatches them, so a mistake degrades a specific dispatch path rather than the global surface. `/agent-review` falls in this lazy-loaded class; dispatcher-level invocation from `/code-review` is sufficient. Bundling an ungated reviewer under a gated plugin's hook (e.g., `/agent-review` under `skill-management`'s) would couple two independent consumer contracts — plugin consumers who installed for one enforcement would inherit the other they did not opt into.

## Project-scoped plugins

Three skills that primarily apply to this repo's own workflow — editing `SKILL.md` files, authoring hook scripts, and managing plugin versioning — live as project-scoped plugins in `plugins/` rather than stowed user-scope skills. This keeps them out of the always-loaded skill catalog for downstream projects that stow claude-config but rarely touch these surfaces.

| Plugin | What it provides | When to install |
|---|---|---|
| `skill-management@claude-config` | Commit-time structural validator (catches frontmatter that would silently truncate from the harness's skill listing or fail strict-YAML parsing), plus behavioral-equivalence audit via `/skill-review` | Repos that author their own `SKILL.md` files |
| `claude-hook-review@claude-config` | Review playbook for `.claude/hooks/*.sh` scripts and `settings.json` hook entries | Repos that author their own hook scripts |
| `plugin-semver@claude-config` | Semver and version-field discipline for plugin manifests | Repos that author Claude Code plugins for a marketplace |

All three plugins are enabled automatically in claude-config sessions via `enabledPlugins` in `.claude/settings.json`. For this to work, the claude-config marketplace must be registered on the machine:

```bash
claude plugin marketplace add ~/MyCode/claude-config   # adjust to your actual checkout path
```

Then Claude Code will resolve the plugins from the project settings. To install any plugin in a downstream project:

```bash
claude plugin install skill-management@claude-config --scope project
claude plugin install claude-hook-review@claude-config --scope project
claude plugin install plugin-semver@claude-config --scope project
```

**Version field convention:** a plugin's `version` is declared in its `.claude-plugin/plugin.json` only — never in a `marketplace.json` entry. Claude Code resolves `plugin.json` first and silently masks any marketplace value, so adding `version` to a marketplace entry only creates drift.

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

`/plan-it`, `/plan-review`, `/code-review`, and `/test-conventions` load a project-specific layer if one exists in the consuming repo — so a project can extend the base skill without forking the public skill body.

- **Location:** `.claude/skills/plan-it-<project>/SKILL.md`, `.claude/skills/code-review-<project>/SKILL.md`, `.claude/skills/plan-review-<project>/SKILL.md`, or `.claude/skills/test-conventions-<project>/SKILL.md`, placed in the consuming repo. The `<project>` token is freeform; only the prefix (`plan-it-`, `code-review-`, `plan-review-`, or `test-conventions-`) is load-bearing.
- **Frontmatter:** any shape works — the parent skill globs for the file and reads it directly via the Read tool. Recommended: `user-invocable: false` to hide from the `/` menu, and `disable-model-invocation: true` to keep the layer's description out of the always-loaded skill-listing budget. Both flags are safe because the parent reads the file, not invokes it.
- **Behavior:** glob runs from the repo root (`git rev-parse --show-toplevel`). Single match → parent reads the file and incorporates its content (checklist merge for `/code-review` and `/plan-review`, convention application for `/test-conventions`, rule application for `/plan-it`). Multiple matches → the skill stops — that's a config error in the consuming project, not something the skill resolves. Zero matches → proceeds without a layer.
