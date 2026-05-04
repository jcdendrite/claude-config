# Design Decisions

Seven non-obvious choices and the reasoning behind them.

## 1. Hook-enforced gates over advisory instructions

A CLAUDE.md instruction is advisory: the model reads it, weighs it against context, and can decide that "this change is too trivial to need a review." A PreToolUse hook is a denial: the commit doesn't happen until the condition is met, regardless of what the model thinks about the change's complexity. The model decides advisory rules don't apply on simple changes — it happens reliably, not occasionally. A hook can't be talked out of it; it either finds the marker or it doesn't. The enforcement lives at the tool-call boundary where the model has no agency to override it.

## 2. Per-session marker keyed on diff sha256

The review marker filename is `~/.claude/review-markers/<repo-hash>.<session-id>`. The session-id component prevents two parallel Claude Code sessions running in the same worktree from overwriting each other's markers — each session's marker is its own file, so session A reviewing a diff doesn't clear session B's gate. The sha256 is taken from the staged diff at the time `/code-review` runs; the hook recomputes the sha256 at commit time and compares. If even one line has been re-staged since the review ran, the sha256 doesn't match and the gate fires again — no manual invalidation needed, no timer to expire, no way to accidentally commit a diff that wasn't reviewed.

## 3. Specialist reviewer roster (8 personas)

A generalist code review misses domain-specific failure modes: a backend engineer reviewing a data migration won't naturally think in terms of CDC impact or lock-budget windows; a frontend engineer reviewing a schema change won't think in terms of ELT-readiness. Eight stack-specific agents (CISO, backend, frontend, data-engineer, analytics-engineer, platform, product, SDET) each bring distinct review heuristics grounded in their domain's canonical failure modes. When multiple specialists independently flag the same surface in a review round, that convergence is signal: the surface is the wrong abstraction, not a collection of independent fixable issues. The "convergence as design tell" principle redirects effort from patching each finding to replacing the surface.

## 4. No shared skill partials

When two skills need the same rule, the text is duplicated — not factored into a `_shared/` include or referenced via `@path` import. This keeps each skill independently readable: you can open any `SKILL.md` and understand it without tracing imports. It also avoids cross-skill coupling: updating a shared partial changes behavior across all skills that include it, including skills you weren't thinking about when you made the change. Duplication is the right tradeoff here — if you find yourself wanting a shared partial, that's a signal to reconsider whether the two skills should be merged, not a signal to add an include mechanism.

## 5. Stow distribution over plugin marketplace

GNU Stow installs the config as symlinks from `claude/.claude/` into `~/.claude/`. A `git pull` updates the repo, and the symlinks already point into it — the installed state is always at HEAD with no reinstall step. New skills, hooks, and agents appear in `~/.claude/` the moment the pull lands. The tradeoff: this requires `stow`, a Unix-like system, and a manual `./install.sh` re-run when a new top-level child is added to `claude/.claude/` (Stow links each immediate child individually, so a brand-new subdirectory only appears in `~/.claude/` after re-linking). Windows is not supported.

## 6. Three-tier redaction system

Tier 1 is always on and requires no setup: a regex blocks `[A-Z]{2,}-\d+` tokens not on the OSS allowlist, catching accidentally committed JIRA/Linear/GitHub tracker references before they land in a public commit or PR. Tier 2 is opt-in: the user drops `~/.claude/private-projects.md` with client codenames; the hook does a case-insensitive whole-word literal match against every non-comment line. The opt-in design is deliberate — forcing everyone to maintain a blocklist they don't need creates friction for the majority to gain safety for the minority who work on sensitive projects. Tier 3 relies on reviewer discipline: structural fingerprints (domain vocabulary, architecture patterns that identify a client) can't be caught mechanically without false-positive rates that would make the hook unusable; that surface stays a human judgment call in the review step.

## 7. Worktree-required as a per-project sentinel

Worktree enforcement is activated per-repo by committing a `.claude/worktree-required` file. It is not a global setting because not every repo needs isolation: a small personal script with one developer has no concurrent-session race condition to guard against, and requiring worktrees there just adds friction. A multi-session feature branch with parallel Claude Code instances does need the guard. The committed sentinel means the enforcement decision lives in source control alongside the code it protects — the same `git pull` that brings the sentinel to a new machine activates enforcement there too, without any per-machine configuration.
