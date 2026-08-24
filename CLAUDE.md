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

See README.md's Tests section for ShellCheck flag sourcing,
`pytest-xdist` debugging flags, and the worktree-relative `.venv`
paths.

## Working in this repo

**Repo layout:** `claude/` is the stow package — `claude/.claude/` maps 1:1 to `~/.claude/`. Skills, hooks, and reviewer agents live under `claude/.claude/skills/`, `claude/.claude/hooks/`, and `claude/.claude/agents/` respectively.

**Two CLAUDE.md files, plus path-scoped rules:** see README.md's Docs
section, "Two `CLAUDE.md` files, plus path-scoped rules" bullet, for
the split between this file, `claude/.claude/CLAUDE.md`, and
`.claude/rules/` — see `.claude/rules/` file names and README.md's
Configuration files section for what each rule covers.

Worktree enforcement is active — see README.md's Worktree enforcement
section for the hook, the opt-in mechanism, and why. `git worktree add
.claude/worktrees/<branch> -b <branch>` (or an agent with `isolation:
worktree`) satisfies it.

`claude/` is stowed into `$HOME`. Changes under `claude/.claude/**` go live on
`git pull` — no re-install needed.

**Footgun: never recommend `>>` writes through stow-symlinked files pointing
at a git-tracked target.**
Never `>>`-append to files under `~/.claude/` — they're symlinks to this
repo's tracked files, so appends silently stage to the public repo; edit the
committed file via PR instead. Exception: verify a file is actually
gitignored (e.g. `.handoff-nudge.log`) before treating it as safe to
append to — untracked runtime state isn't staged, so appending there
never leaks to the public repo.

**Terminology:** Use "project" / "private project", not "client", in
`claude-config` prose. The redaction hook is `deny-private-project-refs`.

**Hook defense-in-depth:** Hooks must filter their own input by tool
name and matcher; do not rely solely on settings.json `if` conditions.

**Should this be a hook?** When the user asks for automated/recurring
behavior ("from now on when X…", "whenever X…", "each time X…",
"before/after X…"), configure a hook in
`.claude/settings.json` — memory and skill instructions cannot fulfill an
automatic-trigger request. Route to `claude-hook-review` for hook design and
review.

**Plugin skills use `plugin:skill` names — never path-prefixed.** This repo's stow source (`claude/.claude/skills/**`) holds the same skill names that stow installs at personal scope (`~/.claude/skills/**`). Because the names clash, Claude Code renders the stow-source copies as directory-qualified duplicates in the available-skills listing — e.g. `.claude/worktrees/<branch>/claude:code-review` — and instructs the model to prefer that form. That qualification applies only to project skills. The three marketplace plugins registered in `enabledPlugins` (`skill-management`, `claude-hook-review`, `plugin-semver`) are never directory-scoped: invoke them by the fully-qualified `plugin:skill` name (`skill-management:skill-review`, `claude-hook-review:claude-hook-review`, `plugin-semver:plugin-semver`) with no directory or worktree path prepended.

**Project-scoped plugins:** skills that apply to one or a few private projects — not broadly to all sessions — live under `plugins/<name>/` as marketplace plugins, not in `claude/.claude/skills/`. The repo exposes itself as a marketplace via `.claude-plugin/marketplace.json`. Add `.claude-plugin/plugin.json` and `skills/<name>/SKILL.md` inside `plugins/<name>/`. Install at project scope from the consuming repo: `claude plugin install <name>@claude-config --scope project`.

**Plans in this repo affect all stow users.** A plan touching anything under `claude/` is not personal-machine tooling — `claude/` installs to every contributor who runs `./install.sh`. When authoring or reviewing such a plan (`/plan-it`, `/plan-review`), frame the user surface and threat model as "every stow consumer," not the session owner alone. This also governs what a plan file itself may contain: a plan committed under `.claude/plans/` ships in the same PR as the implementation, so cited evidence (command output, file listings) is subject to the same redaction rules as any other public-repo content — see "Redact private-project-identifying content" below. Illustrate with placeholder paths and names, not the contributor's own.

## Review pipeline

See README.md's Workflow section for the hook-enforced `plan-it` →
`plan-review` → code → `code-review` pipeline order, and
`.claude/rules/review-pipeline-dispatch.md` for per-file-type dispatch
details (loaded automatically for SKILL.md, agent, and
plugin-directory files).

## AI agents: don't merge your own PRs

`block-gh-pr-merge.sh` blocks `gh pr merge` at the tool-call boundary
(see `docs/hooks.md`'s entry for that hook). Open-ended verbs like
"handle" or "do the swap" cover writing the change and opening the PR,
not landing it.

## Redact private-project-identifying content

Never commit anything that identifies a specific private project,
engagement, or codebase. Three enforcement tiers apply:

**Always caught by hook:** tracker IDs matching `[A-Z]{2,}-\d+` not on
the OSS allowlist (`CVE-`, `RFC-`, `GH-`, and similar), plus six
always-on structural detectors — see `docs/private-project-redaction.md`
for the full detector list and non-matching illustrative shapes. For
tracker-ID-shaped placeholders in examples, use `PROJ-<digits>` or
`TICKET-<digits>` — both pass the allowlist.

**Caught by hook when `~/.claude/private-projects.md` is populated:**
project/org names (including the owner's own private projects),
codenames, internal URLs/project domains on a TLD other than the
always-on list above, non-home-rooted filesystem paths embedding
project names, env var names encoding a project, and person names
other than the repo owner's commit-author identity. Default: if in
doubt, strip it.

**Reviewer discipline only — hook doesn't catch these:** internal
tool/product names not generally known in open source; commit SHAs or
PR numbers from private repos; structural fingerprints and
private-corpus provenance (see below).

### Also redact structural fingerprints and provenance

Identifiers aren't the only leak. Structural shapes can identify a
project even without names — a verbatim RLS policy, a rare
column-naming pattern, an unusual error-code namespace. Generalize
examples that would reveal the project via shape alone.

Provenance leaks the same way. If the only reason you know a fact is
exposure to private engagement material, publishing it carries that
engagement's fingerprint — whatever the datatype, and whether you
quoted it, computed it, or recalled it. The test is where the
knowledge came from, not what shape it takes; a figure drawn from a
corpus mixing private and public sources inherits the private half.
Content derived only from this repo's own history, from public
sources, or from synthetic fixtures is not in this class.

### Secrets, tokens, credentials

Not a redaction concern — a do-not-commit-ever concern. API keys,
OAuth tokens, service-role keys, `.env` contents, database URLs with
credentials, private-key material. If one ever lands, ask the owner to
rotate it *then* rewrite history.

### Enforcement

`deny-private-project-refs.sh` is the mechanical enforcement. See
README.md's Private-project redaction section for the trigger list,
and `docs/private-project-redaction.md` for blocklist setup, opt-in
instructions, and match semantics.
