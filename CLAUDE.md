# Contributor Instructions

This repository is **public** — every commit, skill body, commit message,
and PR description ships to anyone with the URL. The guardrails below
govern any contribution (human or agent).

## Commands

```bash
./install.sh                                                 # first-time setup (stow + plugin registration)
./install-dev.sh                                             # contributor venv setup from requirements-dev.txt (one-time, run from repo root)
.venv/bin/pytest claude/.claude/                             # test suite (hooks + skills)
.venv/bin/ruff check claude/.claude/                         # lint (Python)
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck  # lint (shell, all tracked scripts)
```

ShellCheck flags live in the repo-root `.shellcheckrc`, not on the command
line — CI, this command, and any editor integration all read them from there.

## Working in this repo

**Repo layout:** `claude/` is the stow package — `claude/.claude/` maps 1:1 to `~/.claude/`. Skills, hooks, and reviewer agents live under `claude/.claude/skills/`, `claude/.claude/hooks/`, and `claude/.claude/agents/` respectively.

**Two CLAUDE.md files:** This file (repo root) governs contributor workflow. `claude/.claude/CLAUDE.md` contains the global engineering instructions (judgment heuristics, working style, safety rules) — it is stowed to `~/.claude/CLAUDE.md` and applies to all Claude Code sessions on this machine.

**Path-scoped rules:** `.claude/rules/` holds contributor-workflow instructions
that only need to load when a specific file type is open, instead of every
session — skill/agent self-review discipline and per-file-type review-pipeline
dispatch. They load automatically via `paths` frontmatter matching; see the
file names for what each one covers. `claude/.claude/rules/` is the stowed
sibling (installs to `~/.claude/rules/`) — since it applies in every repo a
stow user opens, not just this one, its `paths` globs must stay generic
(`**/*.sql`, not a claude-config-specific path) rather than repo-relative.

Worktree enforcement is active. `.claude/worktree-required` is committed, so
non-read-only git operations must run inside a linked worktree
(`git worktree add .claude/worktrees/<branch> -b <branch>`) or an agent with
`isolation: worktree`. See README "Worktree enforcement" for why. The
contributor `.venv` lives at the main worktree root only — it is gitignored,
so linked worktrees never inherit it. That path is exactly three levels deep,
so run tests and lint from a worktree via `../../../.venv/bin/pytest` and
`../../../.venv/bin/ruff`.

`claude/` is stowed into `$HOME`. Changes under `claude/.claude/**` go live on
`git pull` — no re-install needed.

**Footgun: never recommend `>>` writes through stow-symlinked files.**
Files under `~/.claude/` (e.g. `~/.claude/CLAUDE.md`) are symlinks to
tracked files in this repo — appending via `>>` writes through the
symlink and silently stages changes to the public repo. Edit the
committed file directly via PR.

**Terminology:** Use "project" / "private project", not "client", in
`claude-config` prose. The redaction hook is `deny-private-project-refs`.

**Hook defense-in-depth:** Hooks must filter their own input by tool
name and matcher; do not rely solely on settings.json `if` conditions.

**Should this be a hook?** When the user asks for an automated or
recurring behavior — "from now on when X…", "each time X…", "whenever
X…", "before/after X…" — the answer is a hook configured in
`.claude/settings.json`, not a memory or a skill instruction. The
harness executes hooks; nothing in memory or a CLAUDE.md prose rule can
fulfill an automatic-trigger request. Route to the `claude-hook-review`
skill for hook design and review.

**Plugin config:** `enabledPlugins` only takes effect in
`settings.json`, not `settings.local.json`.

**Disabling a plugin: `false` vs. removing the entry.** In this repo's
committed `claude/.claude/settings.json`, `enabledPlugins[name]: false`
is reserved for plugins kept as quick-flip handles for occasional
re-enable. Plugins with no foreseeable re-enable use case are removed
from the map entirely. Don't propose `false` reflexively as the
disable-pattern — it smuggles in a re-enable affordance the user may
not want to extend. Mirror existing entries when in doubt.

**Plugin skills use `plugin:skill` names — never path-prefixed.** This repo's stow source (`claude/.claude/skills/**`) holds the same skill names that stow installs at personal scope (`~/.claude/skills/**`). Because the names clash, Claude Code renders the stow-source copies as directory-qualified duplicates in the available-skills listing — e.g. `.claude/worktrees/<branch>/claude:code-review` — and instructs the model to prefer that form. That qualification applies only to project skills. The three marketplace plugins registered in `enabledPlugins` (`skill-management`, `claude-hook-review`, `plugin-semver`) are never directory-scoped: invoke them by the fully-qualified `plugin:skill` name (`skill-management:skill-review`, `claude-hook-review:claude-hook-review`, `plugin-semver:plugin-semver`) with no directory or worktree path prepended.

**No shared partials across skills — but co-located auxiliary files are distinct.** `SKILL.md` frontmatter has no `includes:`, `import:`, or `extends:` fields; the `@path` import syntax works in `CLAUDE.md` only, not in `SKILL.md`. When two skills need the same rule text, duplicate it into both skill files intentionally — do not extract it into a `_shared/` directory or similar abstraction. Duplication keeps each skill independently readable and prevents brittle cross-skill coupling. A skill directory may contain co-located auxiliary files (`REFERENCES.md` for edit-time reference; a runtime auxiliary like `plan-review/ROUTING.md` — an exception for load-bearing content that cannot be shortened, not a routine response to hitting the length cap) — these belong to one skill and are not cross-skill sharing. See [`docs/skills.md` — Skill architecture notes](docs/skills.md#skill-architecture-notes) for the full breakdown.

**`REFERENCES.md` is the edit-time co-located reference for a skill.** A skill directory may contain a `REFERENCES.md` alongside `SKILL.md` — use it for canonical URLs, key quotes, and framework notes that informed the skill's rules. `REFERENCES.md` is not loaded at skill runtime; read it manually (via Read or Bash) when editing a skill to verify a rule still holds or to add new guidance. Do not embed this reference material directly in `SKILL.md`. Some skills also contain a runtime auxiliary file in the same directory (e.g., `plan-review/ROUTING.md`); see [`docs/skills.md` — Skill architecture notes](docs/skills.md#skill-architecture-notes) for the two-pattern distinction.

**Project-scoped plugins:** skills that apply to one or a few private projects — not broadly to all sessions — live under `plugins/<name>/` as marketplace plugins, not in `claude/.claude/skills/`. The repo exposes itself as a marketplace via `.claude-plugin/marketplace.json`. Add `.claude-plugin/plugin.json` and `skills/<name>/SKILL.md` inside `plugins/<name>/`. Install at project scope from the consuming repo: `claude plugin install <name>@claude-config --scope project`.

**Global skill bodies stay platform-agnostic.** Skills under `claude/.claude/skills/` are stowed to every user who clones this repo — their bodies must read cleanly regardless of stack. Encode the generic concept; do not hardcode engine/platform-specific tokens (`pg_cron`, `net.http_post`, vendor API names). Stack-specific examples and checks belong in a project-layer skill (`<skill>-<project>/SKILL.md`) that the base skill loads via its project-layer glob — e.g. `/code-review`'s Step 0.5 globs `.claude/skills/code-review-*/SKILL.md`.

**Plans in this repo affect all stow users.** A plan touching anything under `claude/` is not personal-machine tooling — `claude/` installs to every contributor who runs `./install.sh`. When authoring or reviewing such a plan (`/plan-it`, `/plan-review`), frame the user surface and threat model as "every stow consumer," not the session owner alone. This also governs what a plan file itself may contain: a plan committed under `.claude/plans/` ships in the same PR as the implementation, so cited evidence (command output, file listings) is subject to the same redaction rules as any other public-repo content — see "Redact private-project-identifying content" below. Illustrate with placeholder paths and names, not the contributor's own.

## Review pipeline

`/plan-it` is the prescribed entry for plan creation. `/plan-review` and
`/code-review` are mandatory pipeline steps before PR handoff — both are
hook-enforced (see README "Workflow" for the full skill invocation order and
which hook gates each transition). Per-file-type dispatch details (what
`/code-review` additionally requires for SKILL.md, agent, and plugin-directory
files) live in `.claude/rules/review-pipeline-dispatch.md`, loaded
automatically whenever one of those files is opened.

## AI agents: don't merge your own PRs

In this repo, an AI agent that opens a PR does not also merge it.
CI passing is necessary but not sufficient — wait for the user's
explicit "merge it" before running `gh pr merge`. Open-ended verbs
like "handle" or "do the swap" cover writing the change and opening
the PR, not landing it.

## Redact private-project-identifying content

Never commit anything that identifies a specific private project,
engagement, or codebase. Three enforcement tiers apply:

**Always caught by hook:** tracker IDs matching `[A-Z]{2,}-\d+` not on
the OSS allowlist (`CVE-`, `RFC-`, `GH-`, and similar). For
tracker-ID-shaped placeholders in examples, use `PROJ-<digits>` or
`TICKET-<digits>` — both pass the allowlist.

**Caught by hook when `~/.claude/private-projects.md` is populated:**
project/org names (including the owner's own private projects),
codenames, internal URLs/hostnames/Slack channels/project domains,
filesystem paths embedding project names, env var names encoding a
project, and person names other than the repo owner's commit-author
identity. Default: if in doubt, strip it.

**Reviewer discipline only — hook doesn't catch these:** internal
tool/product names not generally known in open source; commit SHAs or
PR numbers from private repos; structural fingerprints (see below).

### Also redact structural fingerprints

Identifiers aren't the only leak. Structural shapes can identify a
project even without names — a verbatim RLS policy, a rare
column-naming pattern, an unusual error-code namespace. Generalize
examples that would reveal the project via shape alone.

### Secrets, tokens, credentials

Not a redaction concern — a do-not-commit-ever concern. API keys,
OAuth tokens, service-role keys, `.env` contents, database URLs with
credentials, private-key material. If one ever lands, ask the owner to
rotate it *then* rewrite history.

### When a skill is surfaced by real-world work, abstract first

Keep the failure mode and the fix; drop the trigger's identity.

- ✅ "Surfaced during a production incident where a mid-merge index was
  silently corrupted by a diagnostic `git checkout`."
- ❌ "Surfaced during the ExampleCo PROJ-123 review, where the
  mid-merge index was silently corrupted..."

### Enforcement

`deny-private-project-refs.sh` fires on `git commit`, `gh pr create`,
`gh pr edit`, and mutating `gh api` calls. See
`docs/private-project-redaction.md` for blocklist setup, opt-in
instructions, and match semantics.
