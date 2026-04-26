# Contributor Instructions

This repository is **public** — every commit, skill body, commit message,
and PR description ships to anyone with the URL. The guardrails below
govern any contribution (human or agent).

## Working in this repo

Worktree enforcement is active. `.claude/worktree-required` is committed, so
non-read-only git operations must run inside a linked worktree
(`git worktree add .claude/worktrees/<branch> -b <branch>`) or an agent with
`isolation: worktree`. See README "Worktree enforcement" for why.

`claude/` is stowed into `$HOME`. Changes under `claude/.claude/**` go live on
`git pull` — no re-install needed.

**Footgun: never recommend `>>` writes through stow-symlinked files.**
Files under `~/.claude/` (like `~/.claude/CLAUDE.md`) are symlinks to
tracked files in this repo. Telling a user to
`echo "..." >> ~/.claude/CLAUDE.md` writes through the symlink and
silently stages changes to the public repo. To add Claude-context for
a feature, edit the committed file directly via PR. For per-user
private context, use a non-stowed location like
`~/.claude/private-projects.md` (opt-in, not part of the stow tree).

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
  Default: if in doubt, strip it.
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

## Check all three surfaces before committing

1. **File content** being committed.
2. **Commit message body** — the most common leak site, because it's
   where motivation-context gets narrated.
3. **PR title and description**.

## When a skill is surfaced by real-world work, abstract first

Skills are often motivated by a concrete incident. The insight belongs
in this repo; the incident specifics do not.

**Rule:** keep the failure mode and the fix; drop the trigger's identity.

- ✅ "Surfaced during a production incident where a mid-merge index was
  silently corrupted by a diagnostic `git checkout`."
- ❌ "Surfaced during the ExampleCo WIDGET-123 review, where the mid-merge
  index was silently corrupted..."

If a draft commit, skill body, or PR description contains a private-
project reference, fix it **before** committing — do not let history
ship with the reference even if the skill body is clean. Rewriting
unpushed history on a personal branch is the right tool here (see
`git-feature-branch-sync`).

## When editing a skill, run the skill on its own diff

A skill's body states the rules it enforces; an edit can violate
those rules unless you re-read the body with the diff in mind.
Before committing a skill change, load the skill into context and
check the diff against its sections.

The common failure mode: an edit adds prose to a skill that argues
against prose-heavy rules (or a long body to a skill that targets
brevity, etc.) — exactly the kind of thing the skill would flag if
applied to its own diff.

## AI agents

The same rule applies when an AI agent is drafting. If the agent
proposes a commit message, skill body, or PR description that includes
a private-project reference — or an agent's research / memory hands it
a reference — strip it before committing. The fact that the reference
came from the agent, not a human, is not a defense.

## Enforcement

The `deny-private-project-refs.sh` PreToolUse hook (wired in
`claude/.claude/settings.json`) blocks `git commit`, `gh pr create`,
and `gh pr edit` when the staged diff, commit message, or PR
title/body/body-source-file contains either:

1. A tracker-ID token (`[A-Z]{2,}-\d+`) outside the OSS allowlist, or
2. A case-insensitive whole-word match against an entry in the
   user's opt-in `~/.claude/private-projects.md` blocklist.

The blocklist file is user-local and never committed. See README
"Private-project redaction" for opt-in instructions. Tests live in
`claude/.claude/hooks/tests/test_hooks.py`.

The hook catches two mechanical categories (tracker IDs always; named
projects when the user opts in). Other categories above — internal
tool names, structural fingerprints — still require review
discipline. Extend the hook's pattern list if a category becomes
repeatable enough to automate.
