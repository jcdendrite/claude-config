# Contributing to claude-config

`claude-config` is a public repo. Every commit, skill body, commit message, and PR description ships to anyone with the URL. The contribution workflow is enforced by hooks — steps cannot be skipped.

## Contribution workflow

The skills form a sequential pipeline enforced by hooks — steps cannot be skipped. See the [workflow diagram in the README](./README.md#workflow) for the full pipeline and which hook gates each transition.

## Worktrees

Non-read-only git operations (`commit`, `push`, `rebase`, etc.) must run inside a linked git worktree, not the main working tree. Create one before starting work on a feature or fix:

```bash
git worktree add .claude/worktrees/my-feature -b my-feature
cd .claude/worktrees/my-feature
# work happens here
```

See [Worktree enforcement in the README](./README.md#worktree-enforcement) for the reasoning.

## Optional nudges

A few advisory hooks are opt-in and dormant until a contributor explicitly arms them. `nudge-error-mode-analysis.sh` watches for accumulated in-session friction (hook denials, failed test runs, corrections) and suggests running `/error-mode-analysis` once a backtested threshold is crossed:

```bash
touch ~/.claude/.error-mode-nudge-enabled
```

See [`docs/error-mode-nudge.md`](./docs/error-mode-nudge.md) for what it does, how to disable it again, and known limitations.

## AI agents don't merge their own PRs

If an AI agent opens a PR, it does not also merge it. CI passing is necessary but not sufficient — wait for the maintainer's explicit "merge it." Open-ended instructions like "handle" or "do the swap" cover writing the change and opening the PR, not landing it.

## Redaction

This repo is public. Do not commit anything that identifies a private project, organization, engagement, or codebase. See [Private-project redaction in the README](./README.md#private-project-redaction) and the "Redact private-project-identifying content" rule in [`CLAUDE.md`](./CLAUDE.md).

`deny-private-project-refs.sh` enforces the mechanical categories (tracker IDs, blocklist entries) automatically. Structural fingerprints rely on contributor discipline.

## Tests and lint

See [Tests](./README.md#tests) for the contributor `.venv` setup, the pinned `pytest` / `ruff` commands, and cross-worktree invocation. Run both before opening a PR — CI runs them on every PR and main push, and both must pass before the PR can be reviewed.

## Skill evals

`evals/run_skill_evals.py` measures each skill's `trigger-cases.json` against
its declared TRIGGER / DO NOT TRIGGER conditions, via one of three per-skill
methods (`runtime`, `description-fidelity`, or `behavioral-dispatch`). It runs
`claude -p` locally using your Claude subscription auth — no API key, no CI
wiring, no per-token charge on Max plan.

```bash
# Run the 4 pilot skills (runtime, description-fidelity, and behavioral-dispatch):
python evals/run_skill_evals.py --skill code-review --skill test-conventions --skill test-evaluation --skill subagent-delegation

# Run a single skill after editing its TRIGGER block:
python evals/run_skill_evals.py --skill <name>
```

See `evals/README.md` for the full usage guide, output format, and how to
add `trigger-cases.json` files for additional skills.

## Skill and agent edits

When editing a `SKILL.md`, invoke the skill on its own diff before committing. A skill edit can violate the rules it enforces — the skill is the reviewer of its own changes. Run `/skill-review` via the Skill tool and check the diff against its output before staging the file. (`/skill-review` is provided by the `skill-management@claude-config` plugin, auto-enabled after `./install.sh` — see [docs/skills.md — Project-scoped plugins](./docs/skills.md#project-scoped-plugins).)

The same rule applies to agent files (`claude/.claude/agents/*.md`, `plugins/*/agents/*.md`): run `/agent-review` via the Skill tool and check the diff against its output before staging. `/agent-review` is a stowed skill (no plugin install needed) and is dispatcher-invoked by `/code-review`; unlike `/skill-review` it is not hook-enforced (see [README — Workflow](./README.md#workflow) for the asymmetry rationale).

## Contact

Bug reports and feature requests: [GitHub Issues](https://github.com/jcdendrite/claude-config/issues).  
Security disclosures: see [SECURITY.md](./SECURITY.md).
