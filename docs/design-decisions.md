# Design Decisions

Nine non-obvious choices and the reasoning behind them.

## 1. Hook-enforced gates over advisory instructions

A CLAUDE.md instruction is advisory: the model reads it, weighs it against context, and can decide that "this change is too trivial to need a review." A PreToolUse hook is a denial: the commit doesn't happen until the condition is met, regardless of what the model thinks about the change's complexity. The model decides advisory rules don't apply on simple changes — it happens reliably, not occasionally. A hook can't be talked out of it; it either finds the marker or it doesn't. The enforcement lives at the tool-call boundary where the model has no agency to override it.

## 2. Per-session marker keyed on diff sha256

The review marker filename is `~/.claude/review-markers/<repo-hash>.<session-id>`. The session-id component prevents two parallel Claude Code sessions running in the same worktree from overwriting each other's markers — each session's marker is its own file, so session A reviewing a diff doesn't clear session B's gate. The sha256 is taken from the staged diff at the time `/code-review` runs; the hook recomputes the sha256 at commit time and compares. If even one line has been re-staged since the review ran, the sha256 doesn't match and the gate fires again — no manual invalidation needed, no timer to expire, no way to accidentally commit a diff that wasn't reviewed.

## 3. Specialist reviewer roster (8 personas)

A generalist code review misses domain-specific failure modes: a backend engineer reviewing a data migration won't naturally think in terms of CDC impact or lock-budget windows; a frontend engineer reviewing a schema change won't think in terms of ELT-readiness. Eight stack-specific agents (CISO, backend, frontend, data-engineer, analytics-engineer, platform, product, SDET) each bring distinct review heuristics grounded in their domain's canonical failure modes. When multiple specialists independently flag the same surface in a review round, that convergence is signal: the surface is the wrong abstraction, not a collection of independent fixable issues. The "convergence as design tell" principle redirects effort from patching each finding to replacing the surface.

## 4. No shared skill partials

When two skills need the same rule, the text is duplicated — not factored into a `_shared/` include or referenced via `@path` import. This keeps each skill independently readable: you can open any `SKILL.md` and understand it without tracing imports. It also avoids cross-skill coupling: updating a shared partial changes behavior across all skills that include it, including skills you weren't thinking about when you made the change. Duplication is the right tradeoff here — if you find yourself wanting a shared partial, that's a signal to reconsider whether the two skills should be merged, not a signal to add an include mechanism.

`plan-review/ROUTING.md` is not a violation of this decision. It is a co-located, single-skill auxiliary file that `plan-review` reads at runtime via the Read tool — it is not shared across skills and creates no cross-skill coupling. The 200-line cap in `check-skill-length.sh` marks the point past which skill behavior degrades for Claude; the correct first response to approaching it is to shorten the skill. For `plan-review`, the routing table was load-bearing and could not be cut. Extracting to a co-located auxiliary does not reduce context cost — Claude still reads it via the Read tool, so indirection is added on top of the original content load. `require-routing-read.sh` (blocks subagent spawn until ROUTING.md is read) and `log-routing-read.sh` (records the read per session) compensate mechanically. Treat this as a last-resort exception requiring that level of hook enforcement, not a pattern to reach for when any skill approaches the cap.

## 5. Stow distribution over plugin marketplace

GNU Stow installs the config as symlinks from `claude/.claude/` into `~/.claude/`. A `git pull` updates the repo, and the symlinks already point into it — the installed state is always at HEAD with no reinstall step. New skills, hooks, and agents appear in `~/.claude/` the moment the pull lands. The tradeoff: this requires `stow`, a Unix-like system, and a manual `./install.sh` re-run when a new top-level child is added to `claude/.claude/` (Stow links each immediate child individually, so a brand-new subdirectory only appears in `~/.claude/` after re-linking). Windows is not supported.

## 6. Three-tier redaction system

Tier 1 is always on and requires no setup: a regex blocks `[A-Z]{2,}-\d+` tokens not on the OSS allowlist, catching accidentally committed JIRA/Linear/GitHub tracker references before they land in a public commit or PR. Tier 2 is opt-in: the user drops `~/.claude/private-projects.md` with client codenames; the hook does a case-insensitive whole-word literal match against every non-comment line. The opt-in design is deliberate — forcing everyone to maintain a blocklist they don't need creates friction for the majority to gain safety for the minority who work on sensitive projects. Tier 3 relies on reviewer discipline: structural fingerprints (domain vocabulary, architecture patterns that identify a client) can't be caught mechanically without false-positive rates that would make the hook unusable; that surface stays a human judgment call in the review step.

## 7. Worktree-required as a per-project sentinel

Worktree enforcement is activated per-repo by committing a `.claude/worktree-required` file. It is not a global setting because not every repo needs isolation: a small personal script with one developer has no concurrent-session race condition to guard against, and requiring worktrees there just adds friction. A multi-session feature branch with parallel Claude Code instances does need the guard. The committed sentinel means the enforcement decision lives in source control alongside the code it protects — the same `git pull` that brings the sentinel to a new machine activates enforcement there too, without any per-machine configuration.

## 8. Project-layer composition via prose-pointer + glob

`/plan-it`, `/code-review`, and `/plan-review` each check for a project-specific layer at skill start, using an explicit prose pointer that globs for `.claude/skills/<parent>-*/SKILL.md` from the repo root and invokes a single match via the Skill tool. The mechanism is a prose pointer rather than a description-based auto-trigger because an empirical test confirmed that auto-trigger does not fire from inside a running skill — a project-specific layer depending on description-based triggering would silently skip on every review. The glob convention generalizes across projects without editing the public skill body on each onboarding; hardcoding project names in the base skill was rejected because it would require public-repo edits to add each new project, and config-file indirection was rejected because it adds no value over the established naming convention. The tradeoff: the consuming repo must follow the `<parent>-<project>/SKILL.md` naming convention exactly — a typo produces a zero-match silent skip rather than a load error.

## 9. Reviewer persona roster operations

Three operations on the persona roster, with different decision criteria. **Bias against spawn.** Persona count grows linearly; co-ownership cross-references grow combinatorially.

**Extend** — add review angles to an existing persona. Cheap. Default move. Use when the new angles align with the persona's existing mental model and the persona has room (file under context budget, ownership lines uncluttered). Example: adding NoSQL document-shape and partition-key design to `staff-backend-engineer` — backend already thinks in access patterns and query shape, so partition keys are adjacent.

**Split** — carve a slice out of an existing persona into a new one. Use when the persona has two genuinely distinct mental models crammed in, or the file is exceeding context budget, or co-ownership lines are tangled. Cost: ownership lines redrawn, persona files reshaped, dispatcher wiring updated, co-ownership clauses across other personas may shift. Example: carving warehouse modeling out of `staff-data-engineer` into `staff-analytics-engineer` — OLTP migration-safety reasoning and dimensional modeling are different muscle memory.

**Spawn from scratch** — create a persona for a domain none of the existing ones cover. Use when the gap is chronic (diffs in this category consistently go un-reviewed), the new domain has its own canonical body of failure modes, AND extending an existing persona would dilute that persona's mental model. Cost: full new persona file, dispatcher entry, co-ownership lines woven into adjacent personas — higher than split because there's no pre-existing scope to inherit.

Decision tree: (1) Can an existing persona absorb this without diluting? → extend. (2) Is this a slice of a persona with two distinct mental models? → split. (3) Is this a chronic gap with its own canonical failure-mode body? → spawn. Splits and spawns must come with explicit ownership-line updates in adjacent personas.

The right criterion for adding a persona is **distinct review heuristics an AI reviewer can act on from a diff** — not industry-headcount mimicry. Some roles are deliberately absent: DBRE distinctive value is live-system observability unavailable from a diff, with static heuristics already covered by `staff-data-engineer` and `staff-platform-engineer`; data platform engineer is absorbed into those same two; data steward/governance is a non-engineering function whose policy stays human-owned. If a project's review pattern consistently surfaces a gap and the AI can act on diff-visible signals, the right response is to spawn with explicit ownership-line updates in adjacent personas.
