# Contributor Instructions

This repository is **public** — every commit, skill body, commit message,
and PR description ships to anyone with the URL. The guardrails below
govern any contribution (human or agent).

## Redact client-identifying content

Never commit anything that ties a skill, rule, or example back to a
specific client, engagement, or private codebase. That includes:

- **Client / company names** (including the repo owner's own clients).
- **Project codenames** unique to a client codebase.
- **Issue or ticket IDs from client trackers** (e.g., `DAY-484`,
  `ACME-123`, anything matching `[A-Z]{2,}-\d+` that is not a
  well-known open-source reference).
- **Internal URLs or hostnames** (private repo paths, internal
  services, Slack channel names, client-specific domains).
- **Person names** other than the repo owner's own commit-author
  identity.

**Check all three surfaces before committing:**

1. File content being committed.
2. Commit message body — the most common leak site, because it's where
   motivation-context gets narrated.
3. PR title and description.

## When a skill is surfaced by real-world work, abstract first

Skills are often motivated by a concrete incident. The insight belongs
in this repo; the incident specifics do not.

- ✅ "Surfaced during a production incident where a mid-merge index was
  silently corrupted by a diagnostic `git checkout`."
- ❌ "Surfaced during the Acme DAY-484 review..."

If a draft commit or skill body contains a client reference, fix it
**before** committing — do not let history ship with the reference even
if the skill body is clean. Rewriting unpushed history on a personal
branch is the right tool here (see `git-feature-branch-sync`).

## AI agents

The same rule applies when an AI agent is drafting. If the agent
proposes a commit message, PR description, or skill body that includes
a client reference — or an agent's research or reasoning hands the
agent a reference from a private memory or conversation transcript —
strip it before committing. The fact that the reference came from the
agent, not a human, is not a defense.

## Scope and residue

This rule applies to all new contributions going forward. The repo's
existing history may contain residue from before the rule was formalized;
remediating that requires rewriting `main`, which is a deliberate
decision and not part of this CLAUDE.md.
