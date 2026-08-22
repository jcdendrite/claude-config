# Skill reference

Full descriptions for skills, slash commands, and project-scoped plugins in this repo. For the pipeline overview and which hook gates each transition, see the [README](../README.md#workflow).

## Skills (slash commands)

- **`/plan-it`** — produce an implementation plan in `.claude/plans/<topic-slug>.md` through discovery, codebase exploration, clarifying questions, and architecture design; hands off to `/plan-review`.
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/ready-for-review`** — pre-handoff gate: verifies tests/lint/typecheck, runs `/code-review` against the cumulative PR diff (all commits vs default branch), and authors or syncs the PR description (via `/pr-description`); required before `git push` on a branch with an open PR.
- **`/pr-description`** — own a PR body end to end. With no open PR it drafts one to an authoring standard and reports a file path for `/ready-for-review` to create the PR from; with a PR open it verifies the existing body against branch state. Both modes run the same reader-coherence pass, content-claim verification, and coordination-step preservation, and deliver the body as a file rather than a shell argument. Dispatched from `/ready-for-review` step 5 and the `/handoff` pre-write checklist; also runs standalone. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/review-permissions`** — security audit of `permissions.allow` rules against a structured checklist.
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/subagent-delegation`** — when to dispatch work to a subagent rather than running it inline: the two-test gate (output test, judgment test), which subagent fits which case (`Explore` / `general-purpose` for codebase discovery, `code-writer` for implementation work), and what stays inline (check suites, Edit/Write, single targeted reads, content you must reason over line by line). Auto-triggers on the relevant dispatch decisions; the rationale (parent context is re-read every turn) lives in the skill body, not the description.
- **`/branch-management`** — naming conventions (`<TICKET-ID>/<topic-slug>` for ticketed projects, `<topic-slug>` alone otherwise), anti-patterns to reject (tracker `<user>/` defaults), branching from a fresh default-branch tip, and anchoring the session in the branch's worktree.
- **`/git-feature-branch-sync`** — decision framework for keeping a feature branch current with the default branch: when to rebase-and-force-push vs merge-in, and how to force-push safely (`--force-with-lease` vs `--force-if-includes`).
- **`/git-state-safety`** — safely inspecting other branches when the working tree is in a fragile state (mid-merge, mid-rebase, mid-cherry-pick), avoiding the silently-corrupted-index failure mode where a diagnostic `git checkout <ref> -- <path>` overwrites a partially-resolved merge, and recovering from bad merges that were already committed.
- **`/test-evaluation`** — audit guidance for evaluating existing test suites.
- **`/config-environments`** — designing configuration that differs across environments (dev, staging, production): env var naming, credential isolation, secrets provisioning, and the anti-patterns that reintroduce tight coupling.
- **`/ai-instruction-and-memory-files`** — how AI coding agents load instruction files (CLAUDE.md, AGENTS.md, Cursor rules, Lovable knowledge) and Claude Code auto-memory: precedence, duplication rules, length targets, import patterns.
- **`/verify-sources`** — when researching a library, API, or architecture/design decision, or when acting on a documentation claim from a subagent, blog post, or other secondary source, confirm it at the official docs or spec directly.
- **`/handoff`** — write a structured cross-session handoff file at `<config-dir>/handoffs/<slug>-handoff.md` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) capturing goal, status, task list, next step, modified files, active markers, open questions, and the resume incantation. Resumed via `resume-context <path>`, which moves the file to a temp path and launches a new session with it loaded. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only` — see [Skills available by name](#skills-available-by-name-no-description-budget-cost).
- **`/brief`** — write a cold-start task briefing at `<config-dir>/briefs/<slug>-task.md` for a fresh session to pick up known, well-scoped work (abandoned PR, surfaced follow-up, settled-scope ticket) — covers goal, scope, anchors, current state, decisions to make, steps to ship, out of scope. Distinct from `/handoff`, which captures mid-flight session state; `/brief` is for work the current session is *not* going to do. Resumed the same way, via `resume-context <path>`. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/transcript-analysis`** — reference guidance for the `transcript-analysis.py` toolkit: which subcommand answers which analysis question, how to read `fail-seq` convergence-vs-thrashing output, and the measurement caveats. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/transcript-narrative`** — workflow for producing a narrative case study / annotated timeline from Claude Code session transcripts: verbatim prompts bucketed into phases, quantitative appendix from `transcript-analysis`, and extracted lessons. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/error-mode-analysis`** — signal-bucketed error-mode report for a delivered body of multi-session AI-assisted work: composes `transcript-narrative` and `transcript-analysis`, adds PR review comments as a second data source, buckets each failure by which pipeline layer caught it, and splits output into a private, project-identifying report and a de-identified public lessons doc. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/error-handling`** — eight-principle error-handling standard: single code namespace, RFC 9457–derived envelope, developer-only message fields, and call-site anti-patterns. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.
- **`/tighten-prose`** — rewrite prose for clarity and concision (short sentences, active voice, one idea per sentence, consistent terminology) without dropping or softening any fact, number, decision, or hedge; carve-outs exclude conditionals, hedges, and coordination/security/reviewer-action sentences from rewriting entirely. Operates on a drafted PR body, handoff note, or literal input text — not on code comments, doc files, or skill/agent bodies, which `comment-discipline-reviewer` flags rather than rewrites. Dispatched by `/pr-description`'s prose-tightening pass; also invocable standalone. Model-invocable by exact name; description excluded from the listing budget via `skillOverrides: name-only`.

Unlike the eight workflow-utility name-only skills (brief, handoff, read-docx-comments, pr-description, transcript-analysis, transcript-narrative, error-mode-analysis, tighten-prose), error-handling, test-conventions, and sql-query-conventions are knowledge-domain skills kept name-only because their trigger surfaces are too broad to scope reliably — reached by name from the review skills and by `Read` from reviewer agents. `root-cause-analysis` is a debugging playbook: invoke-only, no TRIGGER blocks, consulted by name during investigation planning. `agent-review` forms a fourth category: a dispatcher-reached reviewer skill that carries TRIGGER blocks (graceful-degradation insurance on pre-v2.1.129 clients) but is always reached by name from `/code-review`, never by description auto-trigger. `skill-review` is plugin-scoped (`skill-management`) and therefore exempt from `skillOverrides` — see note below the table.

Each skill lives in `claude/.claude/skills/<skill-name>/SKILL.md`. A skill directory may also contain co-located auxiliary files — see architecture notes below for the two distinct roles they play. Skills that primarily apply to this repo's own workflow (editing SKILL.md files, authoring hooks) live as project-scoped plugins instead — see [Project-scoped plugins](#project-scoped-plugins) below.

## Skills available by name (no description budget cost)

Thirteen skills in this repo use `skillOverrides: name-only` — the model can invoke them when referenced by name in conversation, but their descriptions are excluded from the always-loaded listing budget. These skills are also slash-invocable directly. Requires Claude Code **v2.1.129+**; on older Claude Code versions (pre-v2.1.129) the override is silently ignored and these skills fall back to `on` (description loaded). Nine skills carry no TRIGGER blocks: eight workflow utilities and one debugging playbook; the three knowledge-domain skills and one dispatcher-reached reviewer skill carry TRIGGER blocks and may fire via description match on older versions.

| Skill | Role |
|---|---|
| `/brief` | Cold-start task briefing for a fresh session to pick up well-scoped work |
| `/handoff` | Cross-session handoff file capturing mid-flight session state |
| `/pr-description` | PR-body authoring and accuracy sync; dispatched from `/ready-for-review` and the `/handoff` pre-write checklist |
| `/read-docx-comments` | Extract comments from `.docx` files (Google Docs / Word feedback) |
| `/transcript-analysis` | Reference guide for the `transcript-analysis.py` toolkit |
| `/transcript-narrative` | Narrative case study / annotated timeline from session transcripts: verbatim prompts, phase buckets, quantitative appendix, extracted lessons |
| `/error-mode-analysis` | Signal-bucketed error-mode report for a delivered body of work: composes the two transcript skills, adds PR review comments, buckets by detection layer, splits into private/de-identified artifacts |
| `/tighten-prose` | Rewrite drafted prose for clarity and concision without dropping or softening any fact; dispatched by `/pr-description`'s prose-tightening pass. Distinct from `comment-discipline-reviewer`, which flags durable in-repo prose rather than rewriting it |
| `/error-handling` | Canonical error-handling standard: code namespace, RFC 9457–derived envelope, developer-only message fields, call-site anti-patterns |
| `/test-conventions` | Test authoring conventions: pyramid shape, fixture design, naming, regression-test intent; reached by name from code-review and by Read from reviewer agents |
| `/sql-query-conventions` | Read-path SQL conventions: explicit limits, N+1 avoidance, explicit column selection; reached by name from code-review and by Read from reviewer agents |
| `/agent-review` | Reviewer audit for agent files (frontmatter, triggers, voice, length); dispatched by `/code-review`, never description-auto-triggered |
| `/root-cause-analysis` | Debugging and root-cause investigation playbook: symptom-first, tool-ingestion verification, asymmetry capture, entity-level data pull, incident confirmation before shipping |

**Note on `/skill-review`:** `skill-review` lives in the `skill-management` plugin (`plugins/skill-management/`). Plugin skills are categorically exempt from `skillOverrides` — neither a bare key nor a qualified `plugin:skill` key takes effect (see [Override skill visibility from settings](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings)). Instead, its description is minimized (TRIGGER/DO-NOT-TRIGGER blocks stripped — always-loaded permanent cost with zero routing value since the skill is dispatched by name from `/code-review` and the `require-skill-review` hook, never by description auto-trigger) and `user-invocable: false` is kept.

The `skillOverrides` setting controls skill visibility from settings rather than frontmatter. The four values (Claude Code v2.1.129+):

| Override value | Listed to model | Model can invoke | Description in budget | In `/` menu |
|---|---|---|---|---|
| `on` (default) | name + description | yes (auto-triggers) | yes | yes |
| `name-only` | name only | yes, by name | no | yes |
| `user-invocable-only` | hidden | no | no | yes |
| `off` | hidden | no | no | no |

Source: [Claude Code settings — skillOverrides](https://code.claude.com/docs/en/settings) · [Override skill visibility from settings](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings).

Note: `skillOverrides` does not apply to plugin skills — plugin visibility is managed via the `/plugin` command and `enabledPlugins` in settings.json.

## Bundled skills disabled by default

Claude Code ships a set of bundled skills alongside its custom-skill support. Ten bundled skills are disabled in this repo's `settings.json` via `skillOverrides: "off"`, and two (`/loop`, `/simplify`) are set to `name-only` — invokable by name with no description-budget cost. The reason in each case is either redundancy with a more capable repo-specific skill or low utility relative to the description-budget cost. All skill descriptions contribute to the `skillListingBudgetFraction` context allocation; `/doctor` reports a warning when the budget overflows and descriptions are dropped. The disabled skills freed budget for the always-relevant `user-invocable: false` skills that auto-trigger during the engineering workflow.

Two bundled skills are name-only instead of fully disabled — they are available via `/loop` and `/simplify` (or by name from conversation), with descriptions excluded from the listing budget.

| Bundled skill | Why name-only (kept invokable) |
|---|---|
| `/loop` | Recurring-interval task automation. Occasionally useful on demand; not part of this repo's review-pipeline workflow, so the description stays out of budget. |
| `/simplify` | Code simplification. Occasionally useful on demand; `/code-review` covers routine simplification via specialist routing, so the description stays out of budget. |

| Bundled skill | Why disabled |
|---|---|
| `/claude-api` | Only relevant when building Claude API / Anthropic SDK apps. Out of scope for this repo's tooling work. |
| `/fewer-permission-prompts` | One-time setup utility; rarely fires in established sessions. |
| `/init` | One-time setup; CLAUDE.md is already established, and `/init` advice may conflict with repo conventions. |
| `/keybindings-help` | One-time setup utility; rarely fires in established sessions. |
| `/review` | "Review a PR" — superseded by `/code-review` (specialist reviewer routing) and `/ultrareview`. |
| `/run` | Launches and drives "this project's app" — claude-config is dotfiles, no app to drive. Out of scope. |
| `/schedule` | Cron-scheduled remote agents (routines). Not part of this repo's skill-authoring / review-pipeline workflow. |
| `/security-review` | Superseded by `/code-review` specialist routing (ciso-reviewer agent fires automatically). |
| `/update-config` | Bundled generic settings.json editor. Redundant with `/review-permissions` (permissions.allow), `/claude-hook-review` (hooks), `/skill-review` (skill bodies), and `/agent-review` (agent bodies); remaining env/model/theme edits are trivial direct file changes. |
| `/verify` | Manual-verification skill that drives the app to confirm a change. Same scope mismatch as `/run` — claude-config skills and hooks are verified via `pytest claude/.claude/`. |

### Re-enable for your session

Via `/skills` UI: open `/skills`, highlight the skill, press `Space` to cycle to `"on"`, then `Enter`. This writes to that repository's own `.claude/settings.local.json` (gitignored; persists across sessions in that repo).

Persistent per-project: add to the repository's own `.claude/settings.local.json`:

```json
{
  "skillOverrides": {
    "claude-api": "on"
  }
}
```

`.claude/settings.local.json` is per-repository, not per-user — there is no untracked settings file at the user's home config directory, only `~/.claude/settings.json` (tracked, shared by every stow consumer). A re-enable added this way applies to sessions in this one repository. It overrides `settings.json` at the same scope, so the repo's `"off"` entry does not win. Remove the entry (or set to `"on"`) to restore. Reference: [Claude Code skills — Override skill visibility from settings](https://code.claude.com/docs/en/skills.md).

## Skill evals

`evals/run_skill_evals.py` is a local harness that measures each skill's
`trigger-cases.json` against its declared TRIGGER / DO NOT TRIGGER conditions —
either by observing live auto-dispatch (`runtime`) or by asking a model to
classify which skill a query should match (`description-fidelity`). See
`evals/README.md` for usage and the two-method model.

## Skill architecture notes

- **Co-located files come in two roles, neither auto-loaded.** `REFERENCES.md` is an edit-time reference (canonical URLs, key quotes, framework notes that informed the skill's rules) — read by humans and agents when updating the skill, not at runtime. A runtime auxiliary file (e.g., `plan-review/ROUTING.md`) is read by the skill itself via the Read tool at runtime. The default 200-line cap (`check-skill-length.sh`) sits below Anthropic's documented 500-line ceiling — claude-config defaults lower because cumulative skill surface drives session token cost, and anecdotal reports suggest comprehension degrades as a single skill body grows. Shorten first, do not extract: an auxiliary adds Read-tool indirection without reducing context cost. The hook carves out the full 500-line ceiling for `/code-review` and `/plan-review` — their item-ownership and routing tables are genuinely load-bearing and resist trimming; that exception is reserved for skills in the same class, not as a routine response to hitting the cap. `plan-review/ROUTING.md` is a separate last-resort exception (content could not be cut; `require-routing-read.sh` and `log-routing-read.sh` compensate for the indirection). Both file roles belong to one skill and are not shared across skills.
- **Two carve-outs to "URLs live in `REFERENCES.md`."** The split is enforced by `test_skill_bodies_carry_no_citation_urls` in `claude/.claude/skills/tests/test_skills.py`, which asserts that no `SKILL.md` body holds an `http(s)://` outside a code region. (1) *A URL inside a fenced code block or an inline code span is not a citation.* It is functional or illustrative — an XML namespace URI a parser needs, an attack payload in a security example, a placeholder in a template — and it stays in the body, because moving it would break the thing it is part of. The test strips code regions before scanning, so this needs no allowlist. (2) *A bare authority name may stay in the body where the claim is contestable.* Attribution does two jobs: provenance (which source grounds this) and pushback-resistance (what the model stands on when a user disputes the claim mid-session). `REFERENCES.md` serves the first but is not loaded at the moment of the second. So drop the URL and the verbatim quote, and keep a tag like "Per semver.org 2.0.0" where a user might reasonably argue the other way. Where the rule is uncontestable, or ships with its own project-level override, use bare prose.
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

Two more plugins also live in `plugins/` but are not part of this repo's own authoring workflow — they provide skills for downstream repos rather than for contributors to claude-config itself: `lovable-cloud@claude-config` for downstream Lovable Cloud project repos, and `npm-semver@claude-config` for downstream repos that publish an npm package (semver and version-field discipline for the package's own `package.json`, plus a commit-time hook and a reminder to propagate a bump to consumers).

Each plugin is installed per-project via `claude plugin install`. For this to work, the claude-config marketplace must be registered on the machine:

```bash
claude plugin marketplace add ~/MyCode/claude-config   # adjust to your actual checkout path
```

Then Claude Code will resolve the plugins from the marketplace. To install any plugin in a downstream project:

```bash
claude plugin install skill-management@claude-config --scope project
claude plugin install claude-hook-review@claude-config --scope project
claude plugin install plugin-semver@claude-config --scope project
```

**Version field convention:** a plugin's `version` is declared in its `.claude-plugin/plugin.json` only — never in a `marketplace.json` entry. Claude Code resolves `plugin.json` first and silently masks any marketplace value, so adding `version` to a marketplace entry only creates drift.

## Tuning the skill-listing budget for your project

Claude Code allocates 1% of the context window for skill descriptions by default (`skillListingBudgetFraction: 0.01`). Run `/doctor` to see current usage; a warning appears when descriptions are dropped.

After this repo's plugin restructure, stowed skills from claude-config use less budget than before. If a downstream project still sees truncation — because it has many of its own project-specific skills — raise the cap in that project's own `.claude/settings.local.json` at the repository root (create if absent; gitignored automatically, per-project):

```json
{
  "skillListingBudgetFraction": 0.02
}
```

`.claude/settings.local.json` overrides `settings.json` at the same (repository) scope, so this raise applies only to that one project without forking the stowed config — there is no untracked settings file at the user's home config directory to raise it globally in. Reference: [Claude Code settings — skillListingBudgetFraction](https://code.claude.com/docs/en/settings).

Tools have an analogous per-tool lever — `disableArtifact`/`disableWorkflows` — for the two largest eagerly-loaded built-in tool schemas, though the right place to set them is different: see `design-decisions.md` §28.

## Project-specific layers

`/plan-it`, `/plan-review`, `/code-review`, `/pr-description`, and `/test-conventions` load a project-specific layer if one exists in the consuming repo — so a project can extend the base skill without forking the public skill body.

- **Location:** `.claude/skills/plan-it-<project>/SKILL.md`, `.claude/skills/code-review-<project>/SKILL.md`, `.claude/skills/plan-review-<project>/SKILL.md`, `.claude/skills/pr-description-<project>/SKILL.md`, or `.claude/skills/test-conventions-<project>/SKILL.md`, placed in the consuming repo. The `<project>` token is freeform; only the prefix (`plan-it-`, `code-review-`, `plan-review-`, `pr-description-`, or `test-conventions-`) is load-bearing.
- **Frontmatter:** any shape works — the parent skill globs for the file and reads it directly via the Read tool. Recommended: `user-invocable: false` to hide from the `/` menu, and `disable-model-invocation: true` to keep the layer's description out of the always-loaded skill-listing budget. Both flags are safe because the parent reads the file, not invokes it.
- **Behavior:** glob runs from the repo root (`git rev-parse --show-toplevel`). Single match → parent reads the file and incorporates its content (checklist merge for `/code-review` and `/plan-review`, check-item merge for `/pr-description`, convention application for `/test-conventions`, rule application for `/plan-it`). Multiple matches → the skill stops — that's a config error in the consuming project, not something the skill resolves. Zero matches → proceeds without a layer.
