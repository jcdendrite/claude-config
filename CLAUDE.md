# Contributor Instructions

This repository is **public** — every commit, skill body, commit message,
and PR description ships to anyone with the URL. The guardrails below
govern any contribution (human or agent).

## Commands

```bash
./install.sh                            # first-time setup (stow + plugin registration)
pip install 'ruff==0.6.*'              # one-time linter install
pytest claude/.claude/hooks/tests/     # hook test suite
ruff check claude/.claude/hooks/tests/ # lint
```

## Working in this repo

**Repo layout:** `claude/` is the stow package — `claude/.claude/` maps 1:1 to `~/.claude/`. Skills, hooks, and reviewer agents live under `claude/.claude/skills/`, `claude/.claude/hooks/`, and `claude/.claude/agents/` respectively.

**Two CLAUDE.md files:** This file (repo root) governs contributor workflow. `claude/.claude/CLAUDE.md` contains the global engineering instructions (judgment heuristics, working style, safety rules) — it is stowed to `~/.claude/CLAUDE.md` and applies to all Claude Code sessions on this machine.

Worktree enforcement is active. `.claude/worktree-required` is committed, so
non-read-only git operations must run inside a linked worktree
(`git worktree add .claude/worktrees/<branch> -b <branch>`) or an agent with
`isolation: worktree`. See README "Worktree enforcement" for why.

`claude/` is stowed into `$HOME`. Changes under `claude/.claude/**` go live on
`git pull` — no re-install needed.

**Footgun: never recommend `>>` writes through stow-symlinked files.**
Files under `~/.claude/` (e.g. `~/.claude/CLAUDE.md`) are symlinks to
tracked files in this repo — appending via `>>` writes through the
symlink and silently stages changes to the public repo. Edit the
committed file directly via PR.

**Terminology:** Use "project" / "private project", not "client", in
claude-config prose. The redaction hook is `deny-private-project-refs`.

**Hook defense-in-depth:** Hooks must filter their own input by tool
name and matcher; do not rely solely on settings.json `if` conditions.

**Plugin config:** `enabledPlugins` only takes effect in
`settings.json`, not `settings.local.json`.

## Plans in this repo affect all stow users

`claude/` is stowed into `$HOME` — changes ship to every user who clones and stows this repo, not only to the session owner. When reviewing a plan for claude-config, evaluate with that audience in mind. Files under `claude/` are not personal config; they are distributed to all stow users on `git pull`. Surface this when declaring the user surface in Step 2 of plan-review, and weight finding severity accordingly.

## AI agents: don't merge your own PRs

In this repo, an AI agent that opens a PR does not also merge it.
CI passing is necessary but not sufficient — wait for the user's
explicit "merge it" before running `gh pr merge`. Open-ended verbs
like "handle" or "do the swap" cover writing the change and opening
the PR, not landing it.

## When editing a skill, run the skill on its own diff

A skill's body states the rules it enforces; an edit can violate
those rules unless you re-read the body with the diff in mind. Before
committing a skill change, invoke the skill via the `Skill` tool and
check the diff against its output — e.g. an edit adding prose to a
skill that argues for brevity is the kind of thing the skill would
flag against itself.

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
and `gh pr edit`. See README "Private-project redaction" for blocklist
setup, opt-in instructions, and test location.
