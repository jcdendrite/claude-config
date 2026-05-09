# Contributing to claude-config

`claude-config` is a public repo. Every commit, skill body, commit message, and PR description ships to anyone with the URL. The contribution workflow is enforced by hooks — steps cannot be skipped.

## Contribution workflow

The skills form a sequential pipeline:

1. **`/plan-it`** — produce an implementation plan before touching any code.
2. **`/plan-review`** — required before writing code when a plan file exists in `.claude/plans/`.
3. **Write code** — after plan-review passes. Hook blocks edits until review marker exists.
4. **`/code-review`** — required before `git commit`. Hook blocks commit until review marker exists.
5. **`git commit`** — only after code-review passes.
6. **`/ready-for-review`** — required before `git push` on a branch with an open PR. Verifies tests, lint, typecheck, and cumulative diff.
7. **`git push` / PR open** — only after ready-for-review passes.
8. **`/respond-pr`** — required before reading or posting PR comments via `gh api`.

The [workflow diagram in the README](./README.md#workflow) shows the Mermaid pipeline and which hook gates each transition.

## Worktrees

Non-read-only git operations (`commit`, `push`, `rebase`, etc.) must run inside a linked git worktree, not the main working tree. Create one before starting work on a feature or fix:

```bash
git worktree add .claude/worktrees/my-feature -b my-feature
cd .claude/worktrees/my-feature
# work happens here
```

See [Worktree enforcement in the README](./README.md#worktree-enforcement) for the reasoning.

## AI agents don't merge their own PRs

If an AI agent opens a PR, it does not also merge it. CI passing is necessary but not sufficient — wait for the maintainer's explicit "merge it." Open-ended instructions like "handle" or "do the swap" cover writing the change and opening the PR, not landing it.

## Redaction

This repo is public. Do not commit anything that identifies a private project, organization, engagement, or codebase. See [Private-project redaction in the README](./README.md#private-project-redaction) and the "Redact private-project-identifying content" rule in [`CLAUDE.md`](./CLAUDE.md).

`deny-private-project-refs.sh` enforces the mechanical categories (tracker IDs, blocklist entries) automatically. Structural fingerprints rely on contributor discipline.

## Tests and lint

```bash
pytest claude/.claude/   # hook and skill tests
ruff check claude/.claude/  # lint
```

CI runs both on every PR and main push. Both must pass before the PR can be reviewed.

## Skill edits

When editing a `SKILL.md`, invoke the skill on its own diff before committing. A skill edit can violate the rules it enforces — the skill is the reviewer of its own changes. Run `/skill-review` via the Skill tool and check the diff against its output before staging the file.

## Contact

Bug reports and feature requests: [GitHub Issues](https://github.com/jcdendrite/claude-config/issues).  
Security disclosures: see [SECURITY.md](./SECURITY.md).
