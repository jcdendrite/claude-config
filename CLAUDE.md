# Contributor Instructions

This repository is **public** — every commit, skill body, commit message,
and PR description ships to anyone with the URL. The guardrails below
govern any contribution (human or agent).

## Commands

```bash
./install.sh                        # first-time setup (stow + plugin registration)
pytest claude/.claude/hooks/tests/  # hook test suite
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

## AI agents: don't merge your own PRs

In this repo, an AI agent that opens a PR does not also merge it.
CI passing is necessary but not sufficient — wait for the user's
explicit "merge it" before running `gh pr merge`. Open-ended verbs
like "handle" or "do the swap" cover writing the change and opening
the PR, not landing it.

## When editing a skill, run the skill on its own diff

A skill's body states the rules it enforces; an edit can violate
those rules unless you re-read the body with the diff in mind. Before
committing a skill change, load the skill into context and check the
diff against its sections — e.g. an edit adding prose to a skill that
argues for brevity is the kind of thing the skill would flag against
itself.

## Redact private-project-identifying content

Never commit anything that ties a skill, rule, or example back to a
specific private project, engagement, or private codebase. Categories:

- **Project or organization names** (including the repo owner's own
  private projects).
- **Project codenames** unique to a private codebase.
- **Internal tool or product names** (bespoke CLIs, in-house services)
  beyond those generally known in open source.
- **Issue or ticket IDs from private trackers** — anything matching
  `[A-Z]{2,}-\d+` that is *not* on this allowlist of standard
  open-source references: `CVE-`, `RFC-`, `PEP-`, `ISO-`, `GH-`,
  `BUG-` / bugzilla-style, and clearly-public project prefixes.
  Default: if in doubt, strip it. When an example or commit message
  needs a tracker-ID-shaped placeholder, use `PROJ-<digits>` or
  `TICKET-<digits>` — both prefixes are reserved as placeholders
  and pass the allowlist with any digit suffix.
- **Internal URLs, hostnames, Slack channels, project domains**.
- **Absolute filesystem paths** that embed project names
  (`~/Code/acme-platform/...`, `/home/foo/WorkForProject/...`).
- **Environment variable names** that encode a project (`ACME_API_URL`,
  `PROD_FOOCO_DB_URL`).
- **Commit SHAs or PR numbers from private repos** — pastes like
  `see abc1234 in the main repo` are useless publicly and correlatable.
- **Person names** other than the repo owner's own commit-author
  identity.

### Also redact structural fingerprints

Identifiers aren't the only leak. Structural shapes can identify a
project even without names — a verbatim RLS policy copied from a
private codebase, a rare column-naming pattern, an unusual error-code
namespace. When an example would reveal the project via shape alone,
generalize the example.

### Secrets, tokens, credentials

Not a redaction concern — a do-not-commit-ever concern. API keys,
OAuth tokens, service-role keys, `.env` contents, database URLs with
credentials, private-key material. The repo has no legitimate use for
any of these. If one ever lands, rotate it *then* rewrite history.

### When a skill is surfaced by real-world work, abstract first

Skills are often motivated by a concrete incident. The insight belongs
in this repo; the incident specifics do not.

**Rule:** keep the failure mode and the fix; drop the trigger's identity.

- ✅ "Surfaced during a production incident where a mid-merge index was
  silently corrupted by a diagnostic `git checkout`."
- ❌ "Surfaced during the ExampleCo WIDGET-123 review, where the mid-merge
  index was silently corrupted..."

### Enforcement

The mechanical categories above — tracker IDs always, named projects
when the user opts in via `~/.claude/private-projects.md` — are
enforced by the `deny-private-project-refs.sh` PreToolUse hook on
`git commit`, `gh pr create`, and `gh pr edit`. Other categories
(internal tool names, structural fingerprints) still require review
discipline. See README "Private-project redaction" for full hook
behavior, opt-in instructions, and test location.
