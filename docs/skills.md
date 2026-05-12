# Skill reference

Full descriptions for every skill (slash command) in `claude/.claude/skills/`. For the pipeline overview and which hook gates each transition, see the [README](../README.md#workflow).

## Skills (slash commands)

- **`/plan-it`** — produce an implementation plan in `.claude/plans/<topic-slug>.md` through discovery, codebase exploration, clarifying questions, and architecture design; hands off to `/plan-review`.
- **`/plan-review`** — review implementation plans before presenting, with domain-specific reviewer roles.
- **`/code-review`** — principal engineer code review checklist with ripple-effect triage and domain-specific audits (backend, frontend, security, infrastructure, data).
- **`/skill-review`** — behavioral-equivalence audit for `SKILL.md` changes; required before `git commit` when staged changes include a SKILL.md. Produces an explicit table verifying every removed or shortened line's behavior is preserved.
- **`/ready-for-review`** — pre-handoff gate: verifies tests/lint/typecheck, runs `/code-review` against the cumulative PR diff (all commits vs default branch), and syncs the PR description; required before `git push` on a branch with an open PR.
- **`/review-permissions`** — security audit of `permissions.allow` rules with a 21-item checklist.
- **`/respond-pr`** — fetch and address PR review comments, with `[Claude Code]` attribution on all replies.
- **`/claude-hook-review`** — review playbook for `claude/.claude/hooks/*.sh` and `settings.json` hook entries: event/matcher selection, defense-in-depth filtering within the script body, exit-code contracts.
- **`/branch-creation`** — naming conventions (`<TICKET-ID>/<topic-slug>` for ticketed projects, `<topic-slug>` alone otherwise), anti-patterns to reject (tracker `<user>/` defaults), and branching from a fresh default-branch tip.
- **`/git-feature-branch-sync`** — decision framework for keeping a feature branch current with the default branch: when to rebase-and-force-push vs merge-in, and how to force-push safely (`--force-with-lease` vs `--force-if-includes`).
- **`/git-state-safety`** — safely inspecting other branches when the working tree is in a fragile state (mid-merge, mid-rebase, mid-cherry-pick), avoiding the silently-corrupted-index failure mode where a diagnostic `git checkout <ref> -- <path>` overwrites a partially-resolved merge, and recovering from bad merges that were already committed.
- **`/test-conventions`**, **`/test-evaluation`** — test authoring and audit guidance.
- **`/config-environments`** — designing configuration that differs across environments (dev, staging, production): env var naming, credential isolation, secrets provisioning, and the anti-patterns that reintroduce tight coupling.
- **`/sql-query-conventions`** — read-path conventions for SQL and PostgREST/Supabase queries: pagination, limits, N+1 avoidance, batch-size ceilings, explicit column selection.
- **`/ai-instruction-and-memory-files`** — how AI coding agents load instruction files (CLAUDE.md, AGENTS.md, Cursor rules, Lovable knowledge) and Claude Code auto-memory: precedence, duplication rules, length targets, import patterns.
- **`/verify-primary-sources`** — when web research informs a code or design decision, read the primary documentation directly rather than trusting agent summaries or secondary sources.
- **`/read-docx-comments`** — extract comments from `.docx` files with anchored text context.
- **`/handoff`** — write a structured cross-session handoff file at `/tmp/<slug>-handoff.md` capturing goal, status, next step, modified files, active markers, open questions, and the resume incantation. User-invoked only — implemented as a slash command at `claude/.claude/commands/handoff.md`, not a skill, to keep it out of the always-loaded skills catalog.

Each skill lives in `claude/.claude/skills/<skill-name>/SKILL.md`. A skill directory may also contain co-located auxiliary files — see architecture notes below for the two distinct roles they play.

## Skill architecture notes

- **Co-located files come in two roles, neither auto-loaded.** `REFERENCES.md` is an edit-time reference (canonical URLs, key quotes, framework notes that informed the skill's rules) — read by humans and agents when updating the skill, not at runtime. A runtime auxiliary file (e.g., `plan-review/ROUTING.md`) is read by the skill itself via the Read tool at runtime; this pattern is used when SKILL.md content would exceed `check-skill-length.sh`'s 200-line cap. Both types belong to one skill and are not shared across skills.
- **Frontmatter has no inclusion fields.** There are no `includes:`, `import:`, or `extends:` frontmatter keys — skills do not support partial inclusion.
- **`@path` import syntax is for `CLAUDE.md` only.** The `@path/to/file` import pattern that works in `CLAUDE.md` files is not supported in `SKILL.md`.
- **Duplicate rule text across skills intentionally.** When two skills need the same rule, copy it into both — do not extract it into a `_shared/` partial or similar abstraction. Duplication is deliberate: it keeps each skill independently readable and avoids brittle cross-skill coupling. If you find yourself wanting a shared partial, that is a signal to reconsider whether the skills should be merged, not a signal to add an include mechanism.

## Project-specific layers

`/plan-review` and `/code-review` auto-load a project-specific layer if one exists in the consuming repo — so a project can extend the base checklist without forking the public skill body.

- **Location:** `.claude/skills/code-review-<project>/SKILL.md` or `.claude/skills/plan-review-<project>/SKILL.md`, placed in the consuming repo. The `<project>` token is freeform; only the prefix (`code-review-` or `plan-review-`) is load-bearing.
- **Frontmatter:** match the shape of any skill in `claude/.claude/skills/` (`name`, `description`, `user-invocable`). The parent invokes the layer via the Skill tool — not via description-based auto-trigger, which doesn't fire from inside a running skill (design rationale in [`docs/design-decisions.md`](design-decisions.md)).
- **Behavior:** glob runs from the repo root (`git rev-parse --show-toplevel`). Single match → invoked and merged into the base checklist. Multiple matches → review stops — that's a config error in the consuming project, not a review item the skill resolves. Zero matches → proceeds without a layer.
