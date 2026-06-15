# Design Decisions

Non-obvious choices and the reasoning behind them. For longer-form writeups with primary-source citations, see [`case-studies.md`](case-studies.md).

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

`/plan-it`, `/code-review`, `/plan-review`, and `/test-conventions` each check for a project-specific layer at skill start, using an explicit prose pointer that globs for `.claude/skills/<parent>-*/SKILL.md` from the repo root and reads a single match via the Read tool. Reading the file directly is the right primitive: the parent incorporates the layer's content (checklist items, rules, conventions) into its own reasoning pass — it needs the content, not a separate invocation context. Layer frontmatter is unconstrained by this mechanism — the layer can carry `disable-model-invocation: true` to stay out of the always-loaded listing budget without affecting the parent's ability to read it. The glob convention generalizes across projects without editing the public skill body on each onboarding; hardcoding project names in the base skill was rejected because it would require public-repo edits to add each new project, and config-file indirection was rejected because it adds no value over the established naming convention. The tradeoff: the consuming repo must follow the `<parent>-<project>/SKILL.md` naming convention exactly — a typo produces a zero-match silent skip rather than a load error.

## 9. Reviewer persona roster operations

Three operations on the persona roster, with different decision criteria. **Bias against spawn.** Persona count grows linearly; co-ownership cross-references grow combinatorially.

**Extend** — add review angles to an existing persona. Cheap. Default move. Use when the new angles align with the persona's existing mental model and the persona has room (file under context budget, ownership lines uncluttered). Example: adding NoSQL document-shape and partition-key design to `staff-backend-engineer` — backend already thinks in access patterns and query shape, so partition keys are adjacent.

**Split** — carve a slice out of an existing persona into a new one. Use when the persona has two genuinely distinct mental models crammed in, or the file is exceeding context budget, or co-ownership lines are tangled. Cost: ownership lines redrawn, persona files reshaped, dispatcher wiring updated, co-ownership clauses across other personas may shift. Example: carving warehouse modeling out of `staff-data-engineer` into `staff-analytics-engineer` — OLTP migration-safety reasoning and dimensional modeling are different muscle memory.

**Spawn from scratch** — create a persona for a domain none of the existing ones cover. Use when the gap is chronic (diffs in this category consistently go un-reviewed), the new domain has its own canonical body of failure modes, AND extending an existing persona would dilute that persona's mental model. Cost: full new persona file, dispatcher entry, co-ownership lines woven into adjacent personas — higher than split because there's no pre-existing scope to inherit.

Decision tree: (1) Can an existing persona absorb this without diluting? → extend. (2) Is this a slice of a persona with two distinct mental models? → split. (3) Is this a chronic gap with its own canonical failure-mode body? → spawn. Splits and spawns must come with explicit ownership-line updates in adjacent personas.

The right criterion for adding a persona is **distinct review heuristics an AI reviewer can act on from a diff** — not industry-headcount mimicry. Some roles are deliberately absent: DBRE distinctive value is live-system observability unavailable from a diff, with static heuristics already covered by `staff-data-engineer` and `staff-platform-engineer`; data platform engineer is absorbed into those same two; data steward/governance is a non-engineering function whose policy stays human-owned. If a project's review pattern consistently surfaces a gap and the AI can act on diff-visible signals, the right response is to spawn with explicit ownership-line updates in adjacent personas.

## 10. check-runner agent: charter scoping over command-pattern hardening

`check-runner` is a Haiku subagent that runs a project's check suite (test, lint, typecheck, build) and returns a structured per-command verdict. It runs arbitrary project commands, so it needs constraints — but the constraints are *charter scoping*: what the agent is for and what inputs it receives, not regex denials on the command strings it is handed. Stacking command-string regexes is the compounding-defensive-layers tell the global `CLAUDE.md` flags as a wrong-foundation signal — each layer exists only to close a gap the previous layer left. Every operational incident check-runner has had traced to a charter or input-scoping gap, not a missing command filter; the [check-runner case study](case-studies/check-runner.md) is the empirical record. The design those incidents produced:

**Checks only — no setup or state mutation.** check-runner runs checks and refuses setup or state-mutating commands (database reset, migration apply, container lifecycle, DB seed, dependency installs), reporting each as `NOT RUN — out of charter` in its verdict. A setup command is directory- and shared-state-sensitive in ways a check is not; admitting one lets the agent corrupt a shared database or install packages while "verifying."

**No write access; reviewer-style prose.** `tools: Bash` only — no `Write`. Spool output flows through a Bash redirect to `${TMPDIR:-/tmp}/`, the only legitimate write. Prose instructs the agent that it does not modify project files. An agent that can edit files and is staring at a failing check will try to fix it — a check-runner "fixing" the thing it was asked to verify is the failure to foreclose.

**Turn cap.** `maxTurns: 20` — roughly 2× a typical three-command dispatch — hard-stops runaway iteration. The charter is narrow and deterministic enough that a cap cannot truncate legitimate work; contrast `code-writer` (§11), whose open-ended charter takes no cap.

**cwd anchoring.** A subagent session has no guaranteed working directory. The dispatch must pass an absolute path; check-runner `cd`s to it as its first standalone Bash call and runs every check from there, returning FAIL rather than guessing if no path is given. A check run from the wrong directory verifies the wrong tree.

**One self-contained call per command.** Each command runs in a single Bash call that emits the spool path *before* the command runs, redirects output to the spool, captures the exit code, and emits a bounded tail — never a separate glob or `ls` to locate the spool afterward. Emitting the path first lets a timeout-killed call still reference its partial spool; prohibiting glob recovery stops a stale spool from a prior session being read in place of this run's output.

**Silent on success; counts are the parent's job.** The tail is emitted only on a non-zero exit. A passing command's verdict is its exit code — the agent never sees sub-suite summary lines on a green run, so it cannot misreport a count it never read. When the parent needs a test count or per-type breakdown it `grep`s the full spool itself and quotes the runner's own summary lines verbatim. Removing the count-bearing input from the agent's context is structural; a prose prohibition against repeating salient input competes with the act of summarizing and loses.

**Why not a hook or `isolation: worktree`.** Agent-frontmatter `hooks:` do not fire for Agent-spawned subagents, and a `settings.json` hook fires for every session — including the parent that legitimately runs setup commands — so neither fences check-runner specifically. `isolation: worktree` would give a guaranteed cwd but verifies the wrong thing: an isolated agent runs in a bare checkout at a committed ref, without the parent's uncommitted changes or prepared environment, and a worktree boundary does not isolate a shared database. The charter-scoping rules above hold without either. `check-runner-bash-guard.sh` is kept as a reference implementation of the git-write guard for any future invocation path that does support agent-scoped hooks.

**Justified on verdict quality, not context cost.** check-runner is not a context-cost optimization. The Claude Code harness already truncates Bash tool output at 30 KB, so an inline heavy run costs the parent ~2 KB of context, not the full suite output. What check-runner adds is a *curated* verdict — per-command pass/fail plus the failure tail, the part a truncation preview omits — produced on Haiku. It earns its place as a signal-quality and verdict-ergonomics tool.

## 11. `code-writer` agent and in-agent self-review

Code-writing delegated to a subagent ran on the built-in `general-purpose`
agent. That agent commits review-finding-class defects — N+1 query shapes,
missing idempotency on retryable writes, unhandled error and empty states —
that the parent's `/code-review` pipeline then catches. Each catch costs a
parent → review → re-dispatch round-trip. `general-purpose` also has no `model:`
of its own and inherits the parent — the footgun the Model Routing section of
the global `CLAUDE.md` already carries a standing workaround for.

Framed as feedforward *guides* versus feedback *sensors*: the parent-side
`/code-review` is a sensor that fires only after the subagent has returned, so
every defect it finds is a round-trip. The `code-writer` agent moves a sensor
earlier — into the writing agent's own context. After writing the change,
`code-writer` re-reads its own diff and verifies it against the review angles
the `staff-*` reviewers enumerate, then fixes what it finds before returning.
Reviewing a finished diff is a sharper, more focused task than writing it; the
defect is caught inside the agent rather than surfacing as a parent turn.

**The self-review reads the `staff-*` agent files live; it does not copy their
checklists.** With no copy there is nothing to drift: when a reviewer agent
gains a review angle, `code-writer` inherits it on its next run. This extends
§8's principle — reading the file directly, so its content enters the agent's
own reasoning pass — and avoids the duplication §4 warns against. `code-writer`
therefore depends on the `staff-*` and `ciso-reviewer` files being co-present in
`~/.claude/agents/`; both they and `code-writer` ship from this repo via stow,
so the dependency holds for every user by construction.

**No `maxTurns`.** `check-runner`'s cap (§10) fences a deterministic, narrow
charter against runaway iteration. Code implementation is open-ended; a low cap
truncates legitimate work. Scope creep is bounded instead by charter prose —
implement only the dispatch spec, touch no unrelated files, fix only defects in
the agent's own diff, run no state-mutating setup commands (the §10 lesson).

**Routing is substitute-only and advisory.** The `CLAUDE.md` rule sends
*delegated* code-writing to `code-writer` instead of `general-purpose`; it does
not change how often the parent delegates versus writes inline — that is a
separate, broader decision left unmade. A routing rule cannot be hook-enforced:
there is no tool-call boundary for "the parent is about to write code"
(`Edit` / `Write` fire identically for code, config, and docs), so unlike the
review gates (§1) it stays advisory.

The name `code-writer` is job-shaped — an action-noun parallel to `check-runner`
and to the `code-review` skill's umbrella term — not a persona job title.
Anthropic's subagent documentation treats the agent `name` as a pure
identifier; behavior comes from the system prompt.

## 12. Reviewer file-based output via `findings_path`

All eight reviewer agents write structured Markdown findings to `agent-reviews/<agent-name>-<epoch>-<slug>.md` when the dispatch prompt includes `findings_path:`, and return only a pointer line inline (~650 B median vs ~4,500–7,800 B for full inline findings, an ~86–92% inline context reduction per reviewer). The parent reads the file after the reviewer returns, starting with the `## Recommendations` section. Each reviewer carries the `Write` tool and a `### File-based output` section in its `## Output format`; the section activates only when `findings_path` is present, so reviewers dispatched without it continue to return findings inline. The `agent-reviews/` directory is gitignored and created on first write by the reviewer's `Write` call — no `mkdir` step is needed in the dispatcher. The plan-review gate (`require-plan-review.sh`) exempts writes to `<repo>/agent-reviews/*` with an exact prefix match, so reviewer writes are not blocked when a plan is in flight — without the exemption, a reviewer dispatched during code-review would fall back to full-inline output, defeating the context savings the mechanism exists to provide. The findings files also remain readable in `agent-reviews/` for the duration of the worktree, giving human reviewers direct access to each specialist's full analysis without re-running the review.

### Sources

- Anthropic, *Create custom subagents* — https://code.claude.com/docs/en/sub-agents — first-party documentation.
- Martin Fowler, *Harness engineering for coding agent users* — https://martinfowler.com/articles/harness-engineering.html — industry-practitioner article; source of the guides (feedforward) versus sensors (feedback) framing.
- Ngassom, Moradi Dakhel, Tambon, Khomh, *Chain of Targeted Verification Questions to Improve the Reliability of Code Generated by LLMs* — https://arxiv.org/abs/2405.13932 — arXiv preprint (2024, not peer-reviewed); reports that targeted post-generation verification questions reduce targeted errors in LLM-generated code by 21–62%.

## 13. Single source of truth elevated to a canonical CLAUDE.md rule (2026-05-22)

Duplicated content — the same rule, value, or explanation copied across files — recurred as a PR-review finding. The global `CLAUDE.md` carried only a partial form ("avoid duplicating managed values across files where they can drift out of sync"), scoped to values and readable as code-only. The Engineering Judgment bullet was rewritten as a general single-source-of-truth rule.

SSOT and DRY are the same principle: DRY's canonical definition *is* the single-source-of-truth statement, and DRY is explicitly about *knowledge* — specifications and documentation included — not duplicated code text. That is why the rule covers prose and docs, where the recurring findings landed. A `/code-review` checklist item was considered and rejected: it would be a second copy of an always-loaded rule on a surface that can drift from it — the exact failure the rule names. `/code-review`'s existing item 9 (repeated logic) already operationalizes the principle for code.

The exceptions are deliberate, not loopholes: test code is DAMP rather than DRY (readability earns some repetition); load-bearing instructional prose duplicated across files that must each stand alone (this repo's skills — see §4); and a small duplicated value over a bad abstraction built only to remove it.

### Sources

- Dave Thomas & Andy Hunt, *The Pragmatic Programmer* (20th Anniversary Edition, Addison-Wesley, 2020), "The Evils of Duplication" — Tip 15, DRY: "Every piece of knowledge must have a single, unambiguous, authoritative representation within a system." The chapter frames duplication as duplicated *knowledge* across specifications, code, and tests — not duplicated code text — which is why the rule extends to prose and docs. Verified against the publisher's chapter extract (media.pragprog.com).
- DAMP ("Descriptive And Meaningful Phrases") — the readability-over-deduplication counterpart for test code; a mid-2010s community term with no single canonical author. Already used uncited in `code-review`'s SKILL.md item 9.

## 14. Effort estimated by review surface, not implementation time (2026-05-29)

For a coding agent, implementation time is near-zero — a 500-line change and a one-line fix both run in minutes. The real costs that gate a change are reviewer time (file count, domain complexity, risk concentration) and testing surface area. Anchoring effort estimates on implementation time miscalibrates triage and treats low-implementation-cost changes as low-risk even when they touch shared surfaces.

The rule is encoded in two planning skills: `plan-it` instructs that effort sections, if present, describe review surface (file count, domain spread, risk concentration) and never hours or days; `plan-review` enforces it at checklist item B15, flagging any effort section citing hours/days and rewriting it in review-surface terms. The rule is deliberately scoped to plan documents — where effort estimates are formally consequential — rather than all prose: a general always-loaded instruction would be a heavier mechanism than the documented occurrence warrants (the [effort estimation case study](case-studies/effort-estimation-review-surface.md) puts the rate at roughly 6–8 instances across 50K assistant text blocks, with the dominant corpus signal being the agent flagging the pattern as a defect, not using it).

### Sources

- `claude/.claude/skills/plan-it/SKILL.md` — the governing rule (Step 5, effort section guidance)
- `claude/.claude/skills/plan-review/SKILL.md` — enforcement (checklist item B15)
- `claude/.claude/skills/plan-it/REFERENCES.md` — cross-template research confirming no canonical PR planning template uses hour/day estimates at single-PR scope

## 15. Convention skills wired by explicit pointer, not description-based auto-trigger

`test-conventions` and `sql-query-conventions` carry `user-invocable: false` and TRIGGER blocks, but across thousands of transcripts neither skill fired via description-based auto-trigger in practice. The trigger surface for each is too broad — any SELECT query, any test file — to scope reliably in a description, and description matching fires (or fails to fire) based on session context the author cannot observe.

The repair is explicit pointer wiring: every consumer that should consult the skill is told to `Read` the `~/.claude/skills/<skill>/SKILL.md` path directly (the `Read` tool expands `~`). This is the same pattern `staff-backend-engineer` uses for `error-handling` (§11's "reading the file directly, so its content enters the agent's own reasoning pass"). Consumers wired:

- **`code-writer`** (write-time): reads `test-conventions` when writing test code; reads `sql-query-conventions` when writing a read-path SELECT query.
- **`staff-sdet`** (reviewer): reads `test-conventions` before citing a §N section, which also runs the skill's Step 0 project-layer glob.
- **`staff-backend-engineer`** (reviewer): reads `sql-query-conventions` when evaluating pagination and read-path query design.
- **`code-review`** (dispatcher): inline pointer to invoke `test-conventions` on test-code changes and `sql-query-conventions` on performance-sensitive paths.

Both skills are moved to `skillOverrides: name-only` following the `error-handling` precedent (§11). The TRIGGER blocks and `user-invocable: false` frontmatter are kept: the test suite's `_specialist_skills()` discovery relies on `user-invocable: false` to determine which skills require TRIGGER discipline, and graceful degradation on older clients (pre-v2.1.129) means the description-based path is still available as a fallback.

## 16. Finding disposition calibrated by review surface, not coding time (2026-06-14)

§14 established that effort anchors on review surface, not implementation time, and scoped the rule to plan effort sections. The same miscalibration surfaced at the code-review finding-disposition step: the orchestrator deferred reviewer findings cheap to ADDRESS — small, in already-touched code, covered by tests already running — on effort/size/non-blocking grounds. A transcript audit found the pushback recurring across 10+ sessions in two repos; in several the "pre-existing/independent" label was wrong because the PR's own change touched or activated the finding.

The fix wires §14's principle into disposition: the code-review skill's Finding-disposition section now states disposition calibrates on complexity, risk, and testing area — not implementation effort — reinforces the opportunistic-refactoring license for tech debt in already-touched, already-tested code, hardens "Orthogonal scope" against the touch/activates-it mislabel, and adds "small/cosmetic/non-blocking/advisory" to the invalid-DEFER list. A one-line CLAUDE.md pointer carries the guard to ad-hoc reviewer dispositions outside `/code-review`. This adds the always-loaded pointer the effort-estimation case study deliberately declined — justified here because the disposition miscalibration recurs far more often (10+ flagged sessions vs the 6–8 conversational effort estimates that study measured) and ships as a merged-PR omission, not a stray sentence.

### Sources

- `claude/.claude/skills/code-review/SKILL.md` — Finding disposition (ADDRESS/DEFER) machinery
- `claude/.claude/CLAUDE.md` §Code Review — the always-loaded pointer
- §14 — the parent principle this extends
