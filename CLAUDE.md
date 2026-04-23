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

## Redact client-identifying content

Never commit anything that ties a skill, rule, or example back to a
specific client, engagement, or private codebase. Categories:

- **Client / company names** (including the repo owner's own clients).
- **Project codenames** unique to a client codebase.
- **Internal tool or product names** (bespoke CLIs, in-house services)
  beyond those generally known in open source.
- **Issue or ticket IDs from client trackers** — anything matching
  `[A-Z]{2,}-\d+` that is *not* on this allowlist of standard
  open-source references: `CVE-`, `RFC-`, `PEP-`, `ISO-`, `GH-`,
  `BUG-` / bugzilla-style, and clearly-public project prefixes.
  Default: if in doubt, strip it.
- **Internal URLs, hostnames, Slack channels, client domains**.
- **Absolute filesystem paths** that embed client names
  (`~/Code/acme-platform/...`, `/home/foo/WorkForClient/...`).
- **Environment variable names** that encode a client (`ACME_API_URL`,
  `PROD_FOOCO_DB_URL`).
- **Commit SHAs or PR numbers from private repos** — pastes like
  `see abc1234 in the main repo` are useless publicly and correlatable.
- **Person names** other than the repo owner's own commit-author
  identity.

### Also redact structural fingerprints

Identifiers aren't the only leak. Structural shapes can identify a
client even without names — a verbatim RLS policy copied from a
client codebase, a rare column-naming pattern, an unusual error-code
namespace. When an example would reveal the client via shape alone,
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

If a draft commit, skill body, or PR description contains a client
reference, fix it **before** committing — do not let history ship with
the reference even if the skill body is clean. Rewriting unpushed
history on a personal branch is the right tool here (see
`git-feature-branch-sync`).

## AI agents

The same rule applies when an AI agent is drafting. If the agent
proposes a commit message, skill body, or PR description that includes
a client reference — or an agent's research / memory hands it a
reference — strip it before committing. The fact that the reference
came from the agent, not a human, is not a defense.

## Enforcement

The `deny-client-refs.sh` PreToolUse hook (wired in
`claude/.claude/settings.json`) blocks `git commit` when the staged
diff or commit message contains a tracker-ID token outside the OSS
allowlist. Tests live in `claude/.claude/hooks/tests/test_hooks.py`.

The hook catches the mechanical category (tracker IDs). Other
categories above — client names, internal tool names, structural
fingerprints — still require review discipline. Extend the hook's
pattern list if a category becomes repeatable enough to automate.
