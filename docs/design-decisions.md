# Design Decisions

Non-obvious choices and the reasoning behind them. For longer-form writeups with primary-source citations, see [`case-studies.md`](case-studies.md).

## 1. Hook-enforced gates over advisory instructions

A CLAUDE.md rule is advisory and can be reasoned around ("this change is too trivial"); a PreToolUse hook enforces mechanically at the tool-call boundary and cannot be talked out of firing.

## 2. Content-addressed review markers

Five kinds exist: `code-review`, `plan-review`, `ready-for-review`, `skill-review`, and `cumulative-review` (§44) — a fifth kind that hashes the cumulative PR-vs-base diff so `ready-for-review` step 3 can reuse a prior clean pass instead of re-running it on a byte-identical rebase.

A marker's *content* — not its filename, and not its mere existence — is what authorizes a gate to open. The sha256 is taken from the staged diff at the time `/code-review` runs; the hook recomputes the sha256 at commit time and compares. If even one line has been re-staged since the review ran, the sha256 doesn't match and the gate fires again — no manual invalidation needed, no timer to expire, no way to accidentally commit a diff that wasn't reviewed.

That content-addressing is what makes the filename a pure implementation detail. The marker lives at `<config-dir>/code-review-markers/<repo-hash>.<session-id>` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`), where the session-id component exists so two parallel Claude Code sessions in the same worktree don't overwrite each other's markers — a write-side concern. Reading it back as an *authorization* predicate was a mistake worth naming: it narrowed the gate to "this session reviewed this state" when the property the gate wants is "this state has been reviewed," so a session resumed under a new id was denied a review it had genuinely completed. The gate now matches on content across every session suffix under the repo-hash. The repo-hash stays part of the read, because an identical diff in an unrelated repository was reviewed against different code.

The same reasoning generalizes past `/code-review`: `/plan-review` hashes the active plan set, `/ready-for-review` stores the gated HEAD sha, `/skill-review` hashes the SKILL.md-scoped diff. For the read semantics each gate applies, and for the separate question of *who* may write a marker at all, see [`hooks.md` — Marker keying and gate-release authority](hooks.md#marker-keying-and-gate-release-authority).

## 3. Specialist reviewer roster (8 personas)

A generalist code review misses domain-specific failure modes: a backend engineer reviewing a data migration won't naturally think in terms of CDC impact or lock-budget windows; a frontend engineer reviewing a schema change won't think in terms of ELT-readiness. Eight stack-specific agents (CISO, backend, frontend, data-engineer, analytics-engineer, platform, product, SDET) each bring distinct review heuristics grounded in their domain's canonical failure modes.

1. **What the fan-out decorrelates.** Each reviewer reads the diff fresh and sees no other reviewer's findings — reasoning contamination, one reviewer's framing anchoring the next, is genuinely broken.
2. **What it does not.** All eight run on a shared base model, so a pattern over-represented as a smell in training data draws convergent flags regardless of whether it is wrong in this code. Convergence therefore opens the reconciliation test each dispatcher's Reconciliation section applies — it does not settle the question by itself; the test lives there, not restated here.
3. **Some convergence is prescribed, not emergent.** The Item-ownership tables deliberately assign one checklist item to a primary owner plus co-owners — `ciso-reviewer` alone co-owns ten code-review items across nine rows (`code-review/SKILL.md:328, 347, 351, 356, 357, 358, 360, 361, 365`; row 347 covers two items). Two reviewers landing on one `file:line` is often the routing contract working as designed, not independent corroboration.
4. **The residual this roster cannot fix.** Reconciliation runs only over findings that *exist*. A blind spot shared by all eight — a failure mode none of their heuristics surface — is untouched by any test applied after they return. Reviewer silence is not evidence of absence.

This residual is recorded here rather than at the point a clean review is reported (`code-review/SKILL.md`'s "If no issues are found, say: 'No issues found'"): a caveat firing on every clean review would cost more than it returns, and a reader asking "how much should I trust this roster?" is already reading at this altitude.

**Why the model-diversity mitigation is rejected.** Routing `ciso-reviewer` to a different model family was considered and rejected. Claude Code's subagent `model:` field accepts only `sonnet`, `opus`, `haiku`, `fable`, a full Anthropic model ID, or `inherit` ([Create custom subagents](https://code.claude.com/docs/en/sub-agents), frontmatter table) — cross-vendor decorrelation has no expression at this layer, so only the Opus/Sonnet pairing is even reachable. That pairing is not grounded either: Kim, Garg et al. (ICML 2025) report "larger and more accurate models have highly correlated errors, even with distinct architectures and providers," and Goel et al. (ICML 2025) report "model mistakes are becoming more similar with increasing capabilities, pointing to risks from correlated failures" — Opus and Sonnet are same provider, same family, same generation, the weakest case either paper considers. The only lineage difference Anthropic publishes between the two is training-data cutoff ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)), which is knowledge recency, not corpus independence. Neither paper measures cross-tier decorrelation benefit, so the rejection is recorded as *not established*, not as *near-zero* — the option stays re-openable if Anthropic publishes lineage data. Goel et al. also supplies a reason to prefer a structural test over unaided judgment at reconciliation: "LLM-as-a-judge scores favor models similar to the judge," and the orchestrator adjudicating reviewer findings runs the same model family as the reviewers it judges.

### Sources

- Anthropic, *Create custom subagents* — https://code.claude.com/docs/en/sub-agents — first-party documentation; `model:` frontmatter field values.
- Anthropic, *Models overview* — https://platform.claude.com/docs/en/about-claude/models/overview — first-party documentation; training-data cutoff comparison table.
- Kim, Garg et al., *Correlated Errors in Large Language Models* (ICML 2025), arXiv:2506.07962 — peer-reviewed; larger and more accurate models show highly correlated errors across distinct architectures and providers.
- Goel et al., *Great Models Think Alike and this Undermines AI Oversight* (ICML 2025), arXiv:2502.04313 — peer-reviewed; error similarity rises with model capability, and LLM-as-a-judge scoring favors models similar to the judge.

## 4. No shared skill partials

When two skills need the same rule, the text is duplicated — not factored into a `_shared/` include or referenced via `@path` import. This keeps each skill independently readable: you can open any `SKILL.md` and understand it without tracing imports. It also avoids cross-skill coupling: updating a shared partial changes behavior across all skills that include it, including skills you weren't thinking about when you made the change. Duplication is the right tradeoff here — if you find yourself wanting a shared partial, that's a signal to reconsider whether the two skills should be merged, not a signal to add an include mechanism.

`plan-review/ROUTING.md` is not a violation of this decision. It is a co-located, single-skill auxiliary file that `plan-review` reads at runtime via the Read tool — it is not shared across skills and creates no cross-skill coupling. The 200-line cap in `check-skill-length.sh` marks the point past which skill behavior degrades for Claude; the correct first response to approaching it is to shorten the skill. For `plan-review`, the routing table was load-bearing and could not be cut. Extracting to a co-located auxiliary does not reduce context cost — Claude still reads it via the Read tool, so indirection is added on top of the original content load. `require-routing-read.sh` (blocks subagent spawn until ROUTING.md is read) and `log-routing-read.sh` (records the read per session) compensate mechanically. Treat this as a last-resort exception requiring that level of hook enforcement, not a pattern to reach for when any skill approaches the cap.

## 5. Stow distribution over plugin marketplace

GNU Stow installs the config as symlinks from `claude/.claude/` into `~/.claude/`. A `git pull` updates the repo, and the symlinks already point into it — the installed state is always at HEAD with no reinstall step. New skills, hooks, and agents appear in `~/.claude/` the moment the pull lands. The tradeoff: this requires `stow`, a Unix-like system, and a manual `./install.sh` re-run when a new top-level child is added to `claude/.claude/` (Stow links each immediate child individually, so a brand-new subdirectory only appears in `~/.claude/` after re-linking). Windows is not supported.

## 6. Three-tier redaction system

Tier 1 is always on and requires no setup: a regex blocks `[A-Z]{2,}-\d+` tokens not on the OSS allowlist, catching accidentally committed JIRA/Linear/GitHub tracker references before they land in a public commit or PR. Tier 2 is opt-in: the user drops `<config-dir>/private-projects.md` with client codenames; the hook does a case-insensitive whole-word literal match against every non-comment line. The opt-in design is deliberate — forcing everyone to maintain a blocklist they don't need creates friction for the majority to gain safety for the minority who work on sensitive projects. Tier 3 relies on reviewer discipline: structural fingerprints (domain vocabulary, architecture patterns that identify a client) can't be caught mechanically without false-positive rates that would make the hook unusable; that surface stays a human judgment call in the review step.

## 7. Worktree-required as a per-project sentinel

Worktree enforcement is activated per-repo by committing a `.claude/worktree-required` file. It is not a global setting because not every repo needs isolation: a small personal script with one developer has no concurrent-session race condition to guard against, and requiring worktrees there just adds friction. A multi-session feature branch with parallel Claude Code instances does need the guard. The committed sentinel means the enforcement decision lives in source control alongside the code it protects — the same `git pull` that brings the sentinel to a new machine activates enforcement there too, without any per-machine configuration.

As usage scaled to many-repo engagements (25+ repos across projects), per-repo activation became friction: every new clone needed a manual marker drop, and a forgotten drop left a silent race window. The machine-level sentinel (`<config-dir>/worktree-required`, checked as a union with the legacy `~/.claude/worktree-required` so a sentinel armed before `CLAUDE_CONFIG_DIR` adoption still activates) addresses this without changing the shipped default: setting it once activates enforcement on every repo on that machine. The per-repo opt-out (`.claude/worktree-optout`) exempts individual repos from the machine default only — it cannot defeat a committed repo sentinel, preserving the team-enforcement guarantee. Downstream stow users who never set the machine sentinel are unaffected.

## 8. Project-layer composition via prose-pointer + glob

`/plan-it`, `/code-review`, `/plan-review`, `/pr-description`, and `/test-conventions` each check for a project-specific layer at skill start, using an explicit prose pointer that globs for `.claude/skills/<parent>-*/SKILL.md` from the repo root and reads a single match via the Read tool. Reading the file directly is the right primitive: the parent incorporates the layer's content (checklist items, rules, conventions) into its own reasoning pass — it needs the content, not a separate invocation context. Layer frontmatter is unconstrained by this mechanism — the layer can carry `disable-model-invocation: true` to stay out of the always-loaded listing budget without affecting the parent's ability to read it. The glob convention generalizes across projects without editing the public skill body on each onboarding; hardcoding project names in the base skill was rejected because it would require public-repo edits to add each new project, and config-file indirection was rejected because it adds no value over the established naming convention. The tradeoff: the consuming repo must follow the `<parent>-<project>/SKILL.md` naming convention exactly — a typo produces a zero-match silent skip rather than a load error.

## 9. Reviewer persona roster operations

Three operations on the persona roster, with different decision criteria. **Bias against spawn.** Persona count grows linearly; co-ownership cross-references grow combinatorially.

**Extend** — add review angles to an existing persona. Cheap. Default move. Use when the new angles align with the persona's existing mental model and the persona has room (file under context budget, ownership lines uncluttered). Example: adding NoSQL document-shape and partition-key design to `staff-backend-engineer` — backend already thinks in access patterns and query shape, so partition keys are adjacent.

**Split** — carve a slice out of an existing persona into a new one. Use when the persona has two genuinely distinct mental models crammed in, or the file is exceeding context budget, or co-ownership lines are tangled. Cost: ownership lines redrawn, persona files reshaped, dispatcher wiring updated, co-ownership clauses across other personas may shift. Example: carving warehouse modeling out of `staff-data-engineer` into `staff-analytics-engineer` — OLTP migration-safety reasoning and dimensional modeling are different muscle memory.

**Spawn from scratch** — create a persona for a domain none of the existing ones cover. Use when the gap is chronic (diffs in this category consistently go un-reviewed), the new domain has its own canonical body of failure modes, AND extending an existing persona would dilute that persona's mental model. Cost: full new persona file, dispatcher entry, co-ownership lines woven into adjacent personas — higher than split because there's no pre-existing scope to inherit.

Decision tree: (1) Can an existing persona absorb this without diluting? → extend. (2) Is this a slice of a persona with two distinct mental models? → split. (3) Is this a chronic gap with its own canonical failure-mode body? → spawn. Splits and spawns must come with explicit ownership-line updates in adjacent personas.

The right criterion for adding a persona is **distinct review heuristics an AI reviewer can act on from a diff** — not industry-headcount mimicry. Some roles are deliberately absent: DBRE distinctive value is live-system observability unavailable from a diff, with static heuristics already covered by `staff-data-engineer` and `staff-platform-engineer`; data platform engineer is absorbed into those same two; data steward/governance is a non-engineering function whose policy stays human-owned. If a project's review pattern consistently surfaces a gap and the AI can act on diff-visible signals, the right response is to spawn with explicit ownership-line updates in adjacent personas.

**Not every reviewer is a stack specialist.** `skill-fidelity-reviewer` is a reviewer spawned from scratch for a gap none of the stack personas cover: a session can invoke a skill by name, load its procedure, then deliver work that skips it — and the same session's `/code-review` and `/plan-review` passes do not catch the deviation, because the reasoning that waved off the skill still sits in the reviewing context. The fix is an observer that never shares that context: it is handed the list of skills the branch invoked (extracted from the transcript, not from the session's account of itself), reads each skill body fresh, and compares it to the delivered diff. Two properties keep it distinct from the specialist roster. It is spawned by `/ready-for-review` — a once-per-branch gate — not by the per-diff dispatchers, so it carries no Item-ownership rows and is excluded from the specialist-roster count (`REVIEWER_AGENTS` in `test_agent_roster.py`). But it does write `findings_path` output, so it still carries the file-based-output canary, enforced via the separate `CANARY_AGENTS` superset. It also omits `Bash`: its task is closed-form (read skill bodies, compare to a diff), so it needs no shell — the omission is task-shape, not a data boundary, since read-scope minimization is enforced upstream in the transcript extractor, not by withholding a tool.

**Cost and effectiveness are re-measurable, not assumed.** The invocation-list lookup this agent's dispatch depends on (`transcript-analysis.py skill-invocation`) runs as a script the *parent* executes via `Bash`, not as agent reasoning — none of the transcript data it scans is billed as model tokens, only its already-reduced output is. That, combined with the closed-form task shape above, keeps `skill-fidelity-reviewer` among the cheapest reviewers this repo dispatches by per-invocation token cost; `transcript-analysis.py cost --this-repo` and `subagent-mix --this-repo` reproduce the current comparison against `staff-*` and `ciso-reviewer`. It also surfaces genuine silent-abbreviation findings at a non-trivial rate rather than routinely returning clean, so skill-skipping is not a rare failure mode worth deprioritizing. Re-run both commands before revisiting whether to keep this agent — don't assume either figure has held.

**`comment-discipline-reviewer` is a second spawn-from-scratch, on the same "not every reviewer is a stack specialist" reasoning as `skill-fidelity-reviewer` above, for a different chronic gap.** The gap: CLAUDE.md's comment-verbosity rule reached the authoring session and was applied only at the site a human named, leaving the rest of the diff unswept — observed three times across two repos and two Claude accounts (see `.claude/plans/comment-verbosity-gate.md`'s Context section for the incident ledger). Extending an existing `staff-*` persona was set aside: comment discipline is cross-cutting across every domain those personas already split by (backend, frontend, data, infra), so folding it into one would either check it inconsistently across the others or get dropped by all of them — the same reasoning `docs/design-decisions.md §3` already applies to keep cross-cutting concerns out of a single domain persona. The chronic-gap and canonical-failure-mode criteria are satisfied the same way `skill-fidelity-reviewer`'s were: the failure mode (partial sweep that fixes the two most obvious violations and stops) is specific enough to give a fresh-context reviewer a concrete, diff-visible target, and the fix depends on the reviewing context never having shared the authoring session's satisficing — the same "uncontaminated observer" property, applied to a different contamination source. It is spawned by `/code-review`'s Ripple-effect-triage Change-type table rather than a domain match, carries no Item-ownership row of its own domain checklist (it owns checklist item 12a instead, cited from Step 1.5's inline tripwire), and is excluded from the specialist-roster count for the same structural reason `skill-fidelity-reviewer` is — it is in `CANARY_AGENTS` for the file-based-output canary but not in `REVIEWER_AGENTS`. It also omits `Bash`, for the same closed-form-task reason: it reads a diff and a fixed rule set, no shell needed.

## 10. check-runner agent: charter scoping over command-pattern hardening

`check-runner` is a Haiku subagent that runs a project's check suite (test, lint, typecheck, build) and returns a structured per-command verdict. It runs arbitrary project commands, so it needs constraints — but the constraints are *charter scoping*: what the agent is for and what inputs it receives, not regex denials on the command strings it is handed. Stacking command-string regexes is the compounding-defensive-layers tell the global `CLAUDE.md` flags as a wrong-foundation signal — each layer exists only to close a gap the previous layer left. Every operational incident check-runner has had traced to a charter or input-scoping gap, not a missing command filter; the [check-runner case study](case-studies/check-runner.md) is the empirical record. The design those incidents produced:

**Checks only — no setup or state mutation.** check-runner runs checks and refuses setup or state-mutating commands (database reset, migration apply, container lifecycle, DB seed, dependency installs), reporting each as `NOT RUN — out of charter` in its verdict. A setup command is directory- and shared-state-sensitive in ways a check is not; admitting one lets the agent corrupt a shared database or install packages while "verifying."

**No write access; reviewer-style prose.** `tools: Bash` only — no `Write`. Spool output flows through a Bash redirect to `${TMPDIR:-/tmp}/`, the only legitimate write. Prose instructs the agent that it does not modify project files. An agent that can edit files and is staring at a failing check will try to fix it — a check-runner "fixing" the thing it was asked to verify is the failure to foreclose.

**Turn cap.** `maxTurns: 20` — roughly 2× a typical three-command dispatch — hard-stops runaway iteration. The charter is narrow and deterministic enough that a cap cannot truncate legitimate work; contrast `code-writer` (§11), whose open-ended charter takes no cap.

**cwd anchoring.** A subagent session has no guaranteed working directory. The dispatch must pass an absolute path; check-runner `cd`s to it as its first standalone Bash call and runs every check from there, returning FAIL rather than guessing if no path is given. A check run from the wrong directory verifies the wrong tree.

**One self-contained call per command.** Each command runs in a single Bash call that emits the spool path *before* the command runs, redirects output to the spool, captures the exit code, and emits a bounded tail — never a separate glob or `ls` to locate the spool afterward. Emitting the path first lets a timeout-killed call still reference its partial spool; prohibiting glob recovery stops a stale spool from a prior session being read in place of this run's output.

**Silent on success; counts are the parent's job.** The tail is emitted only on a non-zero exit. A passing command's verdict is its exit code — the agent never sees sub-suite summary lines on a green run, so it cannot misreport a count it never read. When the parent needs a test count or per-type breakdown it `grep`s the full spool itself and quotes the runner's own summary lines verbatim. Removing the count-bearing input from the agent's context is structural; a prose prohibition against repeating salient input competes with the act of summarizing and loses.

**Why not a hook or `isolation: worktree`.** Agent-frontmatter `hooks:` do not fire for Agent-spawned subagents, and a `settings.json` hook fires for every session — including the parent that legitimately runs setup commands — so neither fences check-runner specifically. `isolation: worktree` would give a guaranteed cwd but verifies the wrong thing: an isolated agent runs in a bare checkout at a committed ref, without the parent's uncommitted changes or prepared environment, and a worktree boundary does not isolate a shared database. The charter-scoping rules above hold without either. `check-runner-bash-guard.sh` is kept as a reference implementation of the git-write guard for any future invocation path that does support agent-scoped hooks.

**Retired 2026-06-23.** Transcript-corpus measurement (649 sessions, 784 dispatches, 953 inline check runs) showed neither justification holds: no inline check run in a month exceeded the 30 KB harness truncation, and the verdict-ergonomics benefit was realized in only ~67% of dispatches — with the dispatch-id contract (PR #351) showing no improvement. The agent continued misbehaving post-hardening. The parent now runs checks inline. The empirical record and retirement rationale are in `docs/case-studies/check-runner.md`.

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

**Narrowed 2026-08-21.** For the subcase of implementing a plan that has
already cleared `/plan-review`, "left unmade" no longer holds:
`subagent-delegation`'s decision-made test now states delegation as the
default for that case (scope and approach are already fixed by the
plan), with `plan-it` Step 7 and `handoff` §3 pointing to it at the two
points a session decides where implementation runs. The general case —
any code-writing the parent might do inline, plan or no plan — stays
advisory for the reason above: the routing rule still cannot be
hook-enforced, since a hard deny still has no way to tell approved-plan
implementation apart from any other legitimate inline edit (fixing a
diff, editing the plan file itself, docs, config). See
`.claude/plans/handoff-code-writer-delegation.md` for the transcript
measurement that grounded this narrowing (102 plan-review-boundary
sessions, 90d, this repo: only 33% of handoff §3 sections named
`code-writer`, 35% of sessions were inline-only with zero delegation
attempt).

**Narrowed further 2026-09-01.** The fix that follows `code-review`,
`ready-for-review`, or `respond-pr` feedback is also delegated by
default now: `subagent-delegation`'s "Implementation work →
`code-writer`" section dispatches one `code-writer` per review round,
carrying the round's ADDRESS rows verbatim. This supersedes the prior
paragraph's "fixing a diff" example of a legitimate inline edit — a
reviewer-dispositioned fix is no longer one of the cases a hard deny
couldn't distinguish from plan implementation, since it is itself
delegated-by-construction. The general case — inline code-writing
outside an approved plan or a review disposition — stays advisory for
the same reason above: the routing rule still cannot be hook-enforced.
The 90-day inline-vs-dispatch measurement in
`.claude/plans/handoff-code-writer-delegation.md` predates both
narrowings for all but a handful of its days, so it cannot serve as a
remeasurement of either; a windowed remeasurement keyed to each
narrowing's own merge date is needed instead.

The name `code-writer` is job-shaped — an action-noun (like `code-review`)
describing the work the agent does — not a persona job title.
Anthropic's subagent documentation treats the agent `name` as a pure
identifier; behavior comes from the system prompt.

## 12. Reviewer file-based output via `findings_path`

All eight reviewer agents write structured Markdown findings to `agent-reviews/<agent-name>-<epoch>-<slug>.md` when the dispatch prompt includes `findings_path:`, and return only a pointer line inline (~650 B median vs ~4,500–7,800 B for full inline findings, an ~86–92% inline context reduction per reviewer). The parent reads the file after the reviewer returns, starting with the `## Recommendations` section. Each reviewer carries the `Write` tool and a `### File-based output` section in its `## Output format`; the section activates only when `findings_path` is present, so reviewers dispatched without it continue to return findings inline. The dispatching skill runs `~/.claude/scripts/findings-path-suffix.sh` once per round, which appends `agent-reviews/` to the repo's ignore list before the first spawn. The append is duplicate-tolerant rather than strictly idempotent under concurrent invocations. `deny-reviewer-tree-mutation.sh` confirms with `git check-ignore` at write time that the directory is actually ignored in the repo being written to. A denied write falls back to each reviewer's documented inline output automatically, so the mechanism holds even in a repo with no committed `.gitignore` entry for it. The directory itself is created on first write by the reviewer's `Write` call — no `mkdir` step is needed in the dispatcher. The plan-review gate (`require-plan-review.sh`) exempts writes to `<repo>/agent-reviews/*` with an exact prefix match, so reviewer writes are not blocked when a plan is in flight — without the exemption, a reviewer dispatched during code-review would fall back to full-inline output, defeating the context savings the mechanism exists to provide. The findings files also remain readable in `agent-reviews/` for the duration of the worktree, giving human reviewers direct access to each specialist's full analysis without re-running the review.

`/plan-review` (`plan-review/ROUTING.md`) and `/ready-for-review` are a second and third dispatcher wiring `findings_path`, reusing the same path template, spawn-time exemption, and inline-fallback unchanged. The on-disk findings this produces are also what let a `plan-architect` `MODE=consult` dispatch cite paths instead of transcribing their contents into the dispatch prompt, per the Model & Effort Routing Opus bullet's instruction to name files for a consult to read rather than transcribing them. The findings_path-agent contract is enforced only at authoring time, not at dispatch time, and that gap now applies across all three dispatchers rather than one.

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

Because `Read`-based consumption never registers as a `Skill` invocation, a usage audit (e.g. `/doctor`) undercounts real consultation for every skill wired this way — `test-conventions`, `sql-query-conventions`, and `error-handling` (via `staff-backend-engineer`'s pointer above) alike. A low or zero invocation count for any of these three reflects this wiring mechanism, not disuse; whether a given skill's count lands at exactly zero additionally depends on whether it also has a broader `Skill`-invoke pathway (e.g. `code-review`'s dispatcher pointers) firing independently of the Read path.

Both skills are moved to `skillOverrides: name-only` following the `error-handling` precedent. The TRIGGER blocks and `user-invocable: false` frontmatter are kept: the test suite's `_specialist_skills()` discovery relies on `user-invocable: false` to determine which skills require TRIGGER discipline, and graceful degradation on older clients (pre-v2.1.129) means the description-based path is still available as a fallback.

The same principle was extended to `agent-review` — a dispatcher-reached reviewer skill that carries TRIGGER blocks but is always invoked by name from `/code-review` (SKILL.md:241), never by description auto-trigger. Moving it to `skillOverrides: name-only` freed its description from the always-loaded listing budget. The TRIGGER blocks are kept for graceful degradation on pre-v2.1.129 clients.

`skill-review` was a candidate for the same treatment, but it is plugin-scoped (`plugins/skill-management/`), and **plugin skills are categorically exempt from `skillOverrides`** — neither a bare key nor a qualified `plugin:skill` key takes effect (see [Override skill visibility from settings](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings)). Instead, `skill-review`'s description is minimized: the TRIGGER/DO-NOT-TRIGGER blocks are stripped — they were always-loaded permanent cost with zero routing value, since the skill is always dispatched by name from `/code-review` and the `require-skill-review` hook, never by description auto-trigger. `user-invocable: false` is kept. The asymmetry between `agent-review` (user-scope, name-only) and `skill-review` (plugin-scope, description minimized) reflects the user-scope vs plugin-scope difference.

## 16. Finding disposition calibrated by review surface, not coding time (2026-06-14)

§14 established that effort anchors on review surface, not implementation time, and scoped the rule to plan effort sections. The same miscalibration surfaced at the code-review finding-disposition step: the orchestrator deferred reviewer findings cheap to ADDRESS — small, in already-touched code, covered by tests already running — on effort/size/non-blocking grounds. A transcript audit found the pushback recurring across 10+ sessions in two repos; in several the "pre-existing/independent" label was wrong because the PR's own change touched or activated the finding.

The fix wires §14's principle into disposition: the code-review skill's Finding-disposition section now states disposition calibrates on complexity, risk, and testing area — not implementation effort — reinforces the opportunistic-refactoring license for tech debt in already-touched, already-tested code, hardens "Orthogonal scope" against the touch/activates-it mislabel, and adds "small/quick/cosmetic/non-blocking/advisory" to the invalid-DEFER list.

### Sources

- `claude/.claude/skills/code-review/SKILL.md` — Finding disposition (ADDRESS/DEFER) machinery
- §14 — the parent principle this extends

## 17. `loop` and `simplify` flipped from `off` to `name-only` (2026-06-17)

Before Claude Code v2.1.129, `skillOverrides: "off"` was the only way to exclude a bundled skill's description from the listing budget. Two bundled skills — `/loop` and `/simplify` — were set to `"off"` to avoid budget pressure while keeping them out of the always-loaded listing. Neither is central to this repo's review-pipeline workflow, but both have occasional on-demand utility: `/loop` for recurring-interval task automation, `/simplify` for ad-hoc code simplification when `/code-review`'s specialist routing is heavier than the task warrants.

`name-only` (shipped in v2.1.129) achieves the original goal at zero additional cost: the description stays out of budget, the skill remains slash-invocable and model-invokable by exact name, and no `"off"` entry silently blocks access. Both entries are moved from the `"off"` group to the `"name-only"` group in `settings.json`.

`skillOverrides` does not apply to plugin skills — plugin visibility is managed via the `/plugin` command and `enabledPlugins` in `settings.json`. This is why the two disabled official plugins (`claude-md-management`, `claude-code-setup`) remain in `enabledPlugins: false` rather than being handled via `skillOverrides`. Source: [Claude Code settings — skillOverrides](https://code.claude.com/docs/en/settings).

## 18. Debug-investigation delegation: read-only probe over debug-and-fix agent

When root-causing a check or test failure requires a read-heavy investigation — locating the relevant convention, finding how existing tests handle a pattern, mapping an analogous code shape — the investigation probe is dispatched as a read-only objective to `general-purpose` (model: sonnet) or `Explore`; the parent retains the edit and the judgment. Transcript-corpus measurement showed investigation reads dominated check output roughly 10:1 in sessions that compacted mid-task; a multi-session context-limit chain showed approximately 61K tokens in file reads vs ~6K tokens of check output — making the investigation the dominant context cost, not the failure artifact itself. A write-capable debug-and-fix agent was rejected as the heavier primitive: it re-introduces the model-agency failure class documented in check-runner Incident 1 and the retirement record (see `docs/case-studies/check-runner.md`) — an agent that can edit files while staring at a failing check will attempt to fix it, re-introducing the verification failure the separation was designed to foreclose. The stays-inline rule is unchanged for the failure artifact itself (the output or diff the parent reasons over line by line); only the upstream read-heavy investigation is delegable.

## 19. Continuity-file permission hardening: one choke point over a per-directory recipe

`/handoff` and `/brief` write durable files under `<config-dir>/handoffs/` and `<config-dir>/briefs/` that can sit unresumed for days. Under the common `umask 0002` default, `$HOME` is `755` and `~/.claude` is `775` unless narrowed — Claude Code itself only narrows a handful of paths (`.credentials.json`, `projects/`, `sessions/`, `ide/`, `daemon/`), leaving `file-history/`, `plans/`, `shell-snapshots/`, and any other subdirectory at the umask default, world-readable to every other local account. The two skills previously each ran their own `mkdir`/`chmod`/`touch`/`chmod` recipe to compensate directory by directory.

`chmod 700 ~/.claude` blocks traversal into every subdirectory at once — one choke point at install time replaces a recipe that would otherwise need repeating in every skill that writes under `~/.claude`, including ones not yet written. `install.sh` runs it once, guarded so a missing directory (a run before `stow` has created it) doesn't abort the install. `~/.claude.json` gets its own guarded `chmod 600` because it sits outside `~/.claude/` at `$HOME` level — narrowing the directory doesn't touch it — and it indexes every project directory the user has ever opened.

That `chmod 600` is a one-time repair, not a control that needs reapplying. Current Claude Code releases create `~/.claude.json` at `0600` and preserve its mode across their own rewrites: the file is rewritten via temp-file-plus-rename (the inode changes on every write) but an explicitly-set `0600` survives it. Files sitting at `664` were created by older releases; once narrowed they stay narrowed. This is why no write-triggered or periodic re-narrowing mechanism is warranted here.

`chmod` dereferences symlinks, so the `~/.claude` step is skipped with a warning when that path is a symlink. `stow` tree-folds a target directory that does not already exist into a single symlink pointing back into the repo checkout, and chmod-ing through it would narrow the checkout rather than a private directory. `install.sh` `mkdir -p`s `~/.claude` before invoking `stow` so the fold never happens going forward — matching what it already does for `~/.local/bin` — and the symlink guard covers machines folded by an earlier install.

The `handoff`/`brief` skill bodies therefore only `mkdir -p` their target directory — they chmod nothing, and do not pre-create the file they are about to write with the `Write` tool. Both skills stay correct standing alone (`mkdir -p` is idempotent and harmless without the install-time hardening), but the owner-only guarantee for their output depends on `install.sh` having run — a stow user who skips it inherits the umask-derived, group/world-traversable default instead.

The accepted cost is that protection is now single-layer. Files under `~/.claude` keep their umask-derived modes, so anything that later loosens the directory — a manual `chmod -R` across `$HOME`, a backup-restore that recreates the tree, a filesystem interop layer that drops POSIX bits — exposes every file inside at once, with no per-file mode as a backstop. The per-directory recipe it replaces provided that backstop for exactly two of the roughly thirty directories under `~/.claude`, which is what made it the worse trade.

## 20. Hashline edit format declined (2026-08-08)

Evaluated replacing Claude Code's built-in `Edit`/`Write` with Stencil's "hashline" content-hashed-line edit format and declined it — the empirical record, the measurement subcommand, and the redaction-defect disposition are in the [hashline edit format case study](case-studies/hashline-edit-format.md).

## 21. Cost-lever register consolidated across six prior plans (2026-08-10)

Six plans each accumulated their own rejected-alternatives section for cost-reduction levers; a seventh re-measured ground the first six had already closed. [`cost-levers-considered.md`](cost-levers-considered.md) consolidates every lever investigated so far — lever, verdict, measured reason, source plan — into one page.

A numbered section in this file was considered and rejected for the register itself: the ~30 rows involved would dominate a file whose sections are each one self-contained decision. Amending an already-merged plan file in place was also rejected — this repo's CLAUDE.md (Axis 3) treats merged plans as read-only records, and editing one wouldn't un-scatter the other five.

## 22. Main-thread/subagent cost association published in docs, not the delegation skill (2026-08-10)

Re-deriving the main-thread-vs-subagent split from `transcript-analysis.py` (default config dir, last 14 days, 2026-08-10) gives main 71.4% / subagent 28.6% of dollars (`cost --since 14d`, `## Cost by thread`) and main 28.9% / sidechain 71.1% of tool-result bytes (`subagents --since 14d`, summed per-branch `main`/`sidechain` byte columns). The two shares move in opposite directions: the main thread carries a minority of raw tool-result bytes but a majority of the dollar cost. This is an observed association across an uncontrolled mix of session shapes, not a causal per-byte price — sidechain prefixes are shorter for reasons beyond byte placement, and the tool computes no joined per-byte cost model (see [`cost-levers-considered.md`](cost-levers-considered.md)'s "dollar-per-byte allocation model" entry).

This reverses PR #593's closure of the "delegation-discipline pilot," which was closed as unmeasurable on ISO-week time-series noise: ~25 days of history against a >3x noise floor, with a September 1 repricing inside any window powered enough to detect a trend. That reasoning ruled out a recurring measurement program, not a single cross-sectional ratio — a time series needs enough history to clear a noise floor, a cross-sectional snapshot needs only one well-scoped run, so this entry doesn't revive the pilot itself. The number lives here rather than in `subagent-delegation/SKILL.md` because a dated, drifting ratio has no staleness gate to keep it honest in an auto-triggering skill body — the same principle applied to keeping measured figures out of `CLAUDE.md` proper. The skill's qualitative delegation argument is unchanged by this entry.

## 23. Sentinel promotion criterion: machine -> machine-promptable (2026-08-11)

`install.sh`'s `SENTINEL_INVENTORY` array classifies each machine-level opt-in sentinel as either `machine` (report-only — mentioned in docs, never offered interactively) or `machine-promptable` (offered as a `[y/N]` prompt on every `./install.sh` run). Two rows, `.error-mode-nudge-enabled` and `.cost-ledger-enabled`, were reclassified from `machine` to `machine-promptable` after auditing all six `machine`-scope rows against an explicit criterion, both legs of which must hold:

1. **State is plain boolean file-presence, not a content-based value.** A sentinel whose meaning depends on file *content* (e.g. a mode string) has no natural mapping onto a `[y/N]` prompt and stays report-only (account scope) instead.
2. **Enabling the file opts INTO a new, off-by-default capability, not OUT of an already-on-by-default one.** Asking a contributor to disable a default they haven't yet experienced at install time is premature — a kill-switch sentinel (e.g. `.handoff-nudge-disabled`) stays report-only until the contributor has lived with the default long enough to want to suppress it.

The other four `machine`-scope rows audited (`.handoff-nudge-disabled`, `.consume-durable-continuity-disabled`, `.commit-stall-block-disabled`, `.session-title-disabled`) are all kill switches — each fails leg 2 — and stay report-only.

This criterion is structural only: it tests the sentinel's state shape and opt-in direction, not the security weight of the capability it gates. `worktree-required` and `autonomous-shipping-required` — two of the three pre-existing `machine-promptable` rows, both security/governance controls — happen to also satisfy both legs, but that is incidental, not evidence the criterion accounts for security impact. A future sentinel that is itself a security control (auth/authz gating, privilege grant) satisfying both legs still needs a separate security-impact discussion before promotion; this criterion alone is necessary but not sufficient for that case.

## 24. Effort-tier routing: two-way clamp accepted, `xhigh` reserved for uniformly-hard work (2026-08-14)

`effort:` frontmatter overrides the invoking session's effort level in both directions, not only as a floor: a session run at `max` gets a pinned `ciso-reviewer` dispatch clamped down to `xhigh`, the same mechanism that raises a `low`-effort session's dispatch up to `xhigh`. This is accepted deliberately for every pinned agent in the roster — consistent effort per agent matters more than deferring to whatever effort the calling session happens to run at.

`xhigh`, not `max`, is the ceiling for the reviewer agents. Claude Code's general effort guidance flags `max` as prone to diminishing returns and directs testing before adopting it broadly, rather than banning it outright — `xhigh` is the deliberate starting point this repo hasn't yet measured past, not a permanent prohibition.

`code-writer` is pinned to `high`, not `xhigh`. Two Anthropic sources describe "coding" differently at each tier: the Sonnet 5 prompting guide's `high` bullet says "complex reasoning, coding, and agentic tasks" (an unqualified list item), while the general effort-levels table's `high` row says "difficult coding problems" (an explicit qualifier), and both describe `xhigh` as reserved for "the hardest coding and agentic tasks." `code-writer` is dispatched across this repo's full implementation range — "feature code, bug fixes, refactors, migrations, schema, scripts" — not exclusively hard problems, so a single static pin applied to every dispatch should match the tier meant for that range, not the tier meant for its hardest subset. This differs from the 8 reviewer agents: their job is a uniformly exhaustive single pass with no downstream redo, matching `xhigh`'s "repeated tool calling and detailed search" description across their whole workload, not just a hard subset of it. `code-writer` also has a downstream backstop the reviewers don't: an independent `/code-review` pass runs on every diff it produces regardless of its own self-review.

### Sources

- Anthropic, *Create custom subagents* — https://code.claude.com/docs/en/sub-agents — first-party documentation; `effort` frontmatter field, "Overrides the session effort level."
- Anthropic, *Model configuration* — https://code.claude.com/docs/en/model-config — first-party documentation; "Adjust effort level" section, "Choose an effort level" table, `max` guidance ("Test before adopting broadly").
- Anthropic, *Effort* — https://platform.claude.com/docs/en/build-with-claude/effort — first-party documentation; general "Effort levels" table (`high`: "difficult coding problems"; `xhigh`: "the hardest coding and agentic tasks"; general "When to adjust the effort parameter" section, `xhigh` bullet listing Claude Sonnet 5 as a supported model).
- Anthropic, *Prompting Claude Sonnet 5* — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 — first-party documentation; "Calibrating effort and thinking depth" section (`high`: "complex reasoning, coding, and agentic tasks"; `xhigh`: "the hardest coding and agentic use cases") and "Interactive coding products" section (recommends `xhigh` or `high` for autonomous, single-turn coding agents).

## 25. Widening `skill-fidelity-reviewer`'s scope over a new spawn-dispatch hook (2026-08-14)

`code-review`'s Ripple effect triage spawn-dispatch step is self-reported twice over: the orchestrating session judges which Change-type rows a diff matches, then narrates in its own "Spawn decisions:" text whether it dispatched the required specialist for each — and `skill-fidelity-reviewer`, the observer built for exactly this "invoke a skill, then quietly skip it" failure shape (§9), categorically excluded `code-review` from its own audit scope. One incident surfaced the hole (see `.claude/plans/ready-for-review-self-review-gate.md`'s Context section — a 6-account transcript scan found exactly this one confirmed instance, a floor on detectable literal-phrase reuse, not a ceiling on the failure class): a session skipped a matched row and reviewed it itself, citing rationales the skill's own "Invalid skip rationales" list already named as invalid, and nothing downstream caught it.

A mechanical hook — tailing the session's transcript for the "Spawn decisions:" block and substring-matching the known-invalid rationale phrases — was considered and rejected. It trains its own evasion: the incident was visible only because the model confessed using the skill's own banned vocabulary, so a matcher keyed to those literals selects for paraphrase on the next occurrence, converting a self-reported failure into a silent one while creating a false coverage claim. It is also structurally blind to the more likely failure shape (a row omitted from the enumeration entirely leaves no banned phrase to match), and no clean tool-call boundary exists for it — the incident happened during `/ready-for-review` step 3's cumulative-diff pass, which deliberately never writes a `code-review` completion marker (§2's content-addressed-marker model has no artifact to gate there).

§16 fixed a structurally similar orchestrator-drift incident (invalid DEFER rationales) with prose alone, and it held — but that precedent doesn't transfer here. DEFER findings get persisted to a `## Deferred review findings` PR-body block a human reviewer actually reads; a bad DEFER is visible to someone. Spawn decisions have no such artifact — they live only in the orchestrator's own transcript. Persisting them to the PR body, mirroring DEFER, was considered and declined: it's still self-report, merely published, so it doesn't change who is trusted, and it adds body noise to every PR.

The fix instead extends `skill-fidelity-reviewer` per §9's "extend" arm: it already reads a skill's body fresh from disk and independently judges a diff against it for every other skill in scope. Widening that method to `code-review`'s Change-type table specifically — narrowly, only for the spawn-dispatch question, only for a completed pass, only when fed a `transcript-analysis.py review-trace` timeline (subagent_type/timestamp/branch metadata, never assistant prose) — closes the gap without adding narration-parsing machinery or a new reviewer persona. The mechanizable subset of the Change-type table is already hook-enforced elsewhere (`require-skill-review.sh` gates `git commit` on `SKILL.md` paths); the residual is inherently semantic judgment ("what the change does for an operator or consumer," not file-type-derivable — `code-review/SKILL.md:233`), which is exactly the shape `skill-fidelity-reviewer`'s independent-read method was already built to handle.

## 26. Duplicated-evidence detection sited at plan review, not code review (2026-08-15)

`plan-review` Step 4 gained a seventh foundation tripwire, "Evidence restated across mechanisms": it fires when two or more mechanisms in a plan write the same measurement, citation, or investigation result into different files in full, rather than one holding it and the others pointing at it. It deliberately excludes two look-alikes. Restated rule text is not evidence — §4 already sanctions duplicating instructional prose across skills that must each stand alone, and this tripwire only ever fires on evidence, never on a rule. A compressed summary that points at a named holding site is not a duplicate either — the summary carries no independent claim a reader could trust over the source; requiring every pointer to disappear entirely would make cross-referencing impossible in normal prose.

**Why no code-stage counterpart.** §9 records that `comment-discipline-reviewer` is deliberately closed-form — "reads a diff and a fixed rule set, no shell needed" — which grounds its `effort: medium` pin and its omission of `Bash`. No wording for a sixth review angle covering this defect shape could separate a genuine duplicate from a legitimate compressed pointer-summary without either false-positiving on deliberate cross-referencing (the `## Reconciliation` block, duplicated on purpose across `code-review/SKILL.md:269-284` and `plan-review/ROUTING.md:53-68`, with its own pytest) or requiring the open-ended load-path investigation that §9's closed-form design excludes (commit `962779fb` restates `178/178` at three sites and `92/95` at two, each already pointing at the case study it creates as the home — exactly the compressed-summary pattern a duplicate-detecting wording would need to distinguish from a real duplicate, which needs the same document-altitude judgment the closed-form design was built to avoid). `comment-discipline-reviewer` is left unmodified — charter, angle list, frontmatter, and Output format all unchanged.

**Not a `/code-review` checklist item.** §13 already considered and rejected adding a numbered checklist item for single-source-of-truth, on the grounds that it would be "a second copy of an always-loaded rule on a surface that can drift from it — the exact failure the rule names." A Step 4 tripwire firing on observable plan text is not that: it has no independent prose restating the CLAUDE.md rule, only a firing condition and a `Required:` clause in the same shape as the six tripwires already in that list.

**Evidence ratio.** An error-mode analysis of this repo's 2026-08-02 → 2026-08-14 session corpus found 6 instances of the agent defaulting to a heavier, multi-site fix after being redirected toward a lighter, single-source one. Only one, PR #631, produced a shipped diff and is confirmed to match this specific evidence-duplication shape — a prior plan's design had four separate files (a docs page, an instruction file, an agent-scoping file, and a skill checklist item) each independently restate the same plan-mode model-override claim and its measurement, with no site designated as the home, while a fifth site in the same plan cross-referenced the correction instead of restating it — the author's own non-duplicating counter-example, in the same file as the four duplicates. The other 5 were conversational corrections with nothing left to inspect; they establish that the broader family of defect recurs, not that this specific tripwire would have fired on them. Cite this as 1 of 6, not as 6 confirmed instances.

**Revisit trigger.** The check is a model judgment — no hook can distinguish a restated measurement from a coincidentally equal number, recognize the same result stated differently, identify which site an author intends as the home, or separate a compressed summary from a duplicate — so false positives have no automatic detector. If two false positives are reported, narrow the tripwire's wording or drop it.

### Sources

- PR #631 review comment: "Wrong solution overall. You added the plan mode caveat all over the place. This is compounding engineering."
- `.claude/plans/plan-mode-model-routing.md` at commit `e119b47f` — the known-positive case (four sites restating the same claim vs. a fifth site's own non-duplicating cross-reference).
- Commit `962779fb` — the held-out negative showing a genuine compressed-summary pattern a code-stage angle could not reliably distinguish from a duplicate.
- §9 — `comment-discipline-reviewer`'s closed-form justification for `effort: medium` and no `Bash`.
- §13 — the prior rejection of a `/code-review` checklist item for single-source-of-truth.
- §4 — the no-shared-partials policy this tripwire is built to coexist with.

## 27. Fixture-setup caching declined after measuring the real ratio (2026-08-15)

The pytest-suite parallelization work (`.github/workflows/tests.yml`'s two-pass `pytest-xdist` split, merged as PR #634) had a conditional follow-on: cache the function-scoped git-repo fixtures in `claude/.claude/hooks/tests/conftest.py` (build once per shape, copy per test) if their setup cost was a meaningful share of suite wall time. The landing bar was set as a concrete measured ratio rather than a subjective "still feels slow" call — fixture setup must reach ≥25% of total wall time, measured on the actual CI runner rather than approximated locally, since local timing under `-n auto` scales with the local machine's core count and a typical dev machine (16 cores, in this case) diverges sharply from the GitHub-hosted runner's 4.

A throwaway PR (#661, closed without merging) added `--durations=0` to the CI-only parallel test invocation for one run. Result, from the real 4-vCPU runner ([run 31916577615](https://github.com/jcdendrite/claude-config/actions/runs/31916577615/job/95089240040)): 34.8s of summed `setup`-phase time against 120.91s total wall time (5417 passed, 46 skipped, 1 xfailed). Read two ways — 34.8s divided across the runner's 4 workers as a share of wall time (~7%), or summed `setup` as a share of total test-execution work (`setup` + `call` + `teardown` = 340.28s, ~10%) — both land well under the 25% threshold and agree with each other, unlike an earlier local-machine measurement (16 workers, contended by concurrent unrelated sessions) that produced contradictory 23%/29% readings depending on which ratio was used. The caching work does not clear its own gate and was not pursued.

## 28. Startup context bloat: `disableArtifact`/`disableWorkflows` over `permissions.deny` (2026-08-21)

A brand-new session's baseline context was measured directly (`wc -c`/`wc -w` against the literal tool-definition and `CLAUDE.md` text shown in a session's own system prompt) rather than estimated. The two largest single contributors are the built-in `Artifact` and `Workflow` tools' full-schema descriptions — 9,432 and 19,371 characters respectively (roughly 2,400 and 4,800 tokens at a 4-chars/token estimate) — loaded eagerly into every session's system prompt. That's larger than the other 12 eagerly-loaded built-in tools combined (15,157 characters), and far larger than the ~78 deferred tools (MCP tools plus several built-ins: `CronCreate`, `WebFetch`, `SendMessage`, etc.), whose names plus the harness's own MCP-instructions text together cost only 4,497 characters — confirming deferral is the cheap path already used for the large majority of tools, while `Artifact` and `Workflow` are the outliers.

Both tools have a documented, purpose-built disable setting: `disableArtifact` / `CLAUDE_CODE_DISABLE_ARTIFACT` and `disableWorkflows` / `CLAUDE_CODE_DISABLE_WORKFLOWS` ([Claude Code settings](https://code.claude.com/docs/en/settings), cross-checked against the published [settings JSON Schema](https://www.schemastore.org/claude-code-settings.json)). `permissions.deny` was considered and rejected as the mechanism: it is documented to gate *calling* a tool, not to remove the tool's schema from the system prompt, so it doesn't address token cost the way a setting specifically documented as disabling "the Artifact tool" does. Neither setting's documentation explicitly states whether it strips the schema from context or only blocks invocation — that distinction is unverified pending an actual `/context` before/after comparison, not assumed.

`~/.claude/settings.local.json` is not a real Claude Code scope: the documented settings-file precedence ([Claude Code settings](https://code.claude.com/docs/en/settings)) lists exactly `Managed`, `CLI args`, `Local` (`.claude/settings.local.json`, resolved to the current repository's root, not the user's home directory), `Project` (`.claude/settings.json`, also repository-scoped), and `User` (`~/.claude/settings.json`, global — the same file this repo's stow install symlinks to `claude/.claude/settings.json`). Environment variables are used instead of any settings.json because the preference must hold across every repo on the machine, and the only globally-scoped file (`User`) is, for a stow install, this repo's own shared, tracked config: `CLAUDE_CODE_DISABLE_ARTIFACT=1` and `CLAUDE_CODE_DISABLE_WORKFLOWS=1`, exported from a personal shell profile outside this repo, are read by Claude Code at every launch regardless of which repository the session starts in ([Claude Code environment variables](https://code.claude.com/docs/en/env-vars)), reaching every repo without writing a personal preference into the config every stow consumer inherits. README.md documents the setting for other stow users.

No equivalent lever was found for the available-agent-types listing (11,207 characters, roughly 2,800 tokens): a raw-text search of the settings JSON Schema for `agentOverride`, `agentListingBudget`, and `agentDescriptionBudget` returned zero matches, unlike skills' `skillOverrides`. That gap is recorded here, not solved — there is nothing to configure yet.

Every settings claim above was verified directly against primary sources rather than taken from a subagent summary; Claude Code has no `--tools` CLI flag (commands reference, cited below).

### Sources

- [Claude Code settings reference](https://code.claude.com/docs/en/settings) — `disableArtifact` entry, fetched and quoted directly.
- [Claude Code settings JSON Schema](https://www.schemastore.org/claude-code-settings.json) — raw-text search confirming `disableArtifact`, `disableWorkflows`, `CLAUDE_CODE_DISABLE_ARTIFACT`, `CLAUDE_CODE_DISABLE_WORKFLOWS`, and the absence of any `agentOverride`/`agentListingBudget`/`agentDescriptionBudget` key.
- [Claude Code commands reference](https://code.claude.com/docs/en/commands) — confirms `/context [all]` and the absence of a `--tools` CLI flag.
- [Claude Code environment variables](https://code.claude.com/docs/en/env-vars) — confirms shell-exported environment variables are read at every `claude` launch regardless of the starting repository, and that a settings-file `env` block only wins over the shell when both set the same variable.
- `.claude/plans/startup-context-bloat.md` — full measured breakdown and assumption ledger.

## 29. Worktree-lock self-recognition keyed on session_id, not PID (2026-08-22)

`_lib_worktree_collision_guard` (`claude/.claude/hooks/_lib.sh`) recognizes "this is my own lock" by comparing the acquiring process's PID against the PID stored in the worktree's `locked` file. `claude --continue`/`--resume` keeps a session's `session_id` stable but assigns the CLI process a new PID, so a resumed session's own pre-resume lock no longer PID-matches — self-recognition fails, and the guard falls through to a `kill -0` liveness check on the now-stale PID, which either reports the lock dead or, if the OS has since reissued that exact PID to an unrelated process, falsely reports the worktree already in use by a live session.

The fix keys self-recognition on `session_id` instead: it's assigned by the harness and stays stable across `--continue`/`--resume` (only the PID changes), so storing it in the lock file's reason string at acquisition time (`claude-code pid <N> session <ID>`) and comparing it at every later check makes self-recognition survive a resume entirely. `kill -0` PID liveness is unchanged and still runs, but now only for a lock carrying no matching session_id — i.e. a genuinely foreign or old-format lock, the scenario it was always meant to cover. An old-format lock (predating this fix, or one whose write was truncated mid-flight by the guard's own 5s timeout) falls back to today's PID-only comparison rather than being treated as immediately foreign — the same degrade-not-auto-evict posture the guard already applies to every other malformed-lock case.

Rewriting the lock's stored PID from `capture-session-id.sh` on every `SessionStart` (which already rewrites two other PID-keyed lookups it owns for the same `--continue` reason) was considered and rejected: it would need a new mechanism to discover which worktree(s) a session's *prior* PID might hold a lock in, and `capture-session-id.sh` has no reason to know about worktree locks — a `_lib.sh`/collision-guard concern. Session-id keying needs no new discovery mechanism and dissolves the bug at its source.

This is the same shape §2 already fixed for the code-review marker gate: narrowing a gate's self-recognition to a transient identifier (there, a marker's session-id-suffixed filename; here, a lock's stored PID) instead of the property the gate actually wants ("this state has been reviewed" / "this session holds this lock") produces a false negative whenever that identifier changes but the underlying state hasn't. §2's fix widened matching to ignore the volatile identifier entirely (content-addressing, matched across every session suffix); this fix instead swaps the volatile identifier (PID) for the harness's own stable one (session_id), since PID liveness still needs a real PID to `kill -0` against for the genuinely-foreign case and has no broad-match substitute.

### Sources

- `.claude/plans/worktree-lock-pid-resume-mismatch.md` — full plan, per-mechanism ledger, and verified givens.
- Anthropic, *Hooks reference* — https://code.claude.com/docs/en/hooks — confirms `session_id` stays the same across `--continue`/`--resume`, and that `SessionStart`'s `source` matcher includes a `resume` value precisely for this event.

## 30. `/plan-it` Step 5 dispatches a pinned-Opus `plan-architect` agent instead of anchoring the whole session (2026-08-22)

A whole-session `--model opus` anchor escalates every inheriting subagent dispatch and every low-judgment turn, not only Step 5's design synthesis. `/plan-it` Step 5 now dispatches a new repo-owned agent, `claude/.claude/agents/plan-architect.md`, isolating the escalation to that one call: an explicit `model: "opus"` per-dispatch parameter, on every run regardless of the session's own model.

The agent also pins `model: opus` in its own frontmatter, in addition to the per-dispatch parameter — belt-and-suspenders, not redundant. `docs/auto-mode.md` documents the per-invocation parameter as a *request* competing with resolution step 4 (parent inheritance), not a guaranteed override. The specific resolution direction this design depends on (Sonnet parent → Opus child) is published as the same algorithm as the downgrade direction, but not separately measured the way that direction is. A silently-Sonnet `plan-architect` dispatch would fail quietly — a plausible, well-formatted, lower-quality plan that could still clear `/plan-review` and land as a durable artifact — and closing that asymmetry is why the pin exists.

`plan-architect` is deliberately a custom agent file rather than the harness built-in `Plan`. `Plan` (with `Explore`) is one of exactly two subagent types that skip the automatic CLAUDE.md/git-status startup load every other subagent gets ([Claude Code sub-agents](https://code.claude.com/docs/en/sub-agents), "Subagent Startup Context Loading"). `Plan`'s tool set and read-only boundary are also undocumented in this repo — a "mandate," not a registry `test_agent_roster.py` can assert against — where a custom agent's `tools: Read, Grep, Glob` frontmatter is pinned mechanically by the test suite.

`plan-architect` holds no `Write`, `Edit`, `Bash`, or `Skill`: it returns finished plan prose for the dispatching session to insert verbatim into the plan file, rather than writing `.claude/plans/` itself. `require-plan-review.sh` exempts a `Write`/`Edit`/`MultiEdit` targeting a `.claude/plans/` file the same way it does `agent-reviews/`, so `plan-architect`'s no-`Write` choice stands on its own grounds independent of that gate.

Rejected alternatives:
- No `model:` frontmatter field at all — fails `test_agent_roster.py`'s `test_required_fields_present`, which requires a non-empty `model:` on every agent file.
- `model: inherit` explicitly — satisfies that test and states the fallback rather than leaving it unstated, but still relies solely on the per-dispatch parameter. Kept as the fallback design if a future measurement shows the frontmatter pin isn't honored from a Sonnet parent.
- `CLAUDE_CODE_SUBAGENT_MODEL=opus` — resolution step 1, overrides every subagent including the `staff-*` reviewers' Sonnet pin; the blunt global hammer `docs/auto-mode.md` already names to avoid.
- Keeping the status quo of documenting `--model opus` as the only path to an Opus plan — fails on the whole-session escalation this design exists to remove.
- Dispatching `general-purpose` with `model: opus` instead of a dedicated agent — carries `Write`/`Bash`/`Skill` unconditionally, none of which a design-authoring dispatch should hold, and has no durable charter of its own: every instruction `plan-architect.md`'s body carries once would have to be restated in the Step 5 prompt on every dispatch.

Two residual risks are accepted rather than closed.

First, the frontmatter pin is unconditional once it lands. The harness makes every named agent dispatchable from any session, so nothing technical enforces `plan-architect`'s single-caller premise. An off-path by-name dispatch costs one unbudgeted Opus-tier call, bounded rather than a cascade, because `plan-architect` carries no `Agent` tool and so cannot itself spawn further dispatches. The caller constraint is stated in the agent's own `description` plus this entry, with the spend-share measurement below as a detective backstop rather than a preventive control. `plan-architect` serves two independently frequency-bounded call sites, each with its own constraint stated in the agent's own `description` — see §37 for the full reasoning.

Second, the dispatching session inserts `plan-architect`'s returned text verbatim, with no rewrite or summarization on the way to disk. This is the mechanism the whole design exists to enable — rewriting Opus's output on the way to disk would reintroduce the Sonnet-quality loss the dispatch removes — but it also means a committed plan file can carry an entire section authored by a different agent, with no editorial pass by the session that reviews and commits it.

**Revisit triggers.** Run one `/plan-it` outside plan mode and read the Step 5 dispatch's resolved model off the transcript — the upgrade-direction resolution this design depends on (Sonnet parent → Opus child) has not been separately measured the way the downgrade direction has. Measure Opus's share of spend via `transcript-analysis.py cost --since 14d` shortly after this lands and again ~30 days later, and flag growth in either reading — not against the 16.0% figure in `.claude/plans/pin-explore-to-sonnet.md` (2026-08-09), which predates the plan-mode-forces-Opus fix (PRs #647/#654, merged 2026-08-15) and so overstates the clean baseline this change should be measured against. Re-run the frontmatter-pin-vs-per-dispatch-parameter falsification test periodically, not only once at implementation time, to decide whether the `model: inherit` fallback should replace the pin.

## 31. Artifact/Workflow disabled by default, with a per-session opt-back-in (2026-08-25)

§28 measured `Artifact` and `Workflow` as the two largest eagerly-loaded tool schemas in every session's system prompt and deliberately chose not to set `disableArtifact`/`disableWorkflows` repo-wide, reasoning that publishing Artifacts and running Workflows are legitimate for many stow consumers and the shared config shouldn't impose the choice on their behalf; it shipped a personal-shell-profile environment-variable path instead. That env var reaches nobody who merely installs this repo — it has to be exported from a shell profile the stow package does not touch. This entry reverses that default: `claude/.claude/settings.json` now sets both keys to `true`. The reasoning that changed is the shape of the cost/benefit pair, not the facts §28 recorded. The schema cost is unconditional, paid by every session regardless of whether that session ever calls either tool. The benefit of having either tool loaded is occasional. The only mechanism previously available to reclaim the cost reached nobody by default. Pairing the flip with a one-command per-session opt-back-in (`claude-workflow`, `claude-artifact`) removes the downside §28 was avoiding: a consumer who wants either tool for a session is one command away from it, rather than permanently paying for a schema most sessions never use.

The opt-back-in uses CLI-scope `--settings`, not an environment variable and not `enableArtifact`. An environment variable was rejected because `CLAUDE_CODE_DISABLE_ARTIFACT`/`CLAUDE_CODE_DISABLE_WORKFLOWS` are independent disable triggers OR-combined with the settings key, not overrides of it — no environment variable can re-enable a settings-file `true`, so an env-var path cannot serve as the opt-back-in the new default needs. `enableArtifact` was rejected because it is scope-restricted to User-or-managed, and for a stow install the User-scope file *is* this repo's own shared, tracked `settings.json` — so it cannot express a personal, per-session override the way a CLI-scope flag can. CLI scope is the only scope that outranks the User-scope default from outside a specific repository, per the documented settings-file precedence.

Two alternatives were already closed by §28 and are not re-argued here:

- `permissions.deny: ["Artifact"]` — gates *calling* the tool rather than removing its schema from the system prompt, so it doesn't address the token cost this decision is aimed at.
- A settings-file `env` block setting the disable environment variables — strictly worse than the settings key directly, since an env-var trigger cannot be cancelled from CLI scope either.

The pre-committed go/no-go measurement that gated this change: a plain session's `/context` Tools figure measured 23.8k tokens; a session started with `claude --settings '{"disableArtifact": true, "disableWorkflows": true}'` measured 7.8k tokens — a 16k-token drop, well clear of the plan's 5,000-token floor. This also closes the question §28 left explicitly open — whether disabling actually strips the schema from the system prompt or only blocks invocation — in favor of the former: the Tools figure dropped by an amount consistent with schema removal, not merely invocation-blocking.

**Residual risk, not closed.** The opt-back-in mechanism rests on an undocumented, unversioned property of the Claude Code CLI: that an explicit `false` at CLI scope overrides a `true` at User scope, rather than only key *presence* mattering. Neither this repo nor the vendor's published settings reference states that precedence rule for value collisions explicitly — it was confirmed by direct experiment (see Sources), not read from documentation. No automated regression test can exercise the real CLI's settings-precedence resolution, since the test suite has no interactive `claude` session to launch. If a consumer reports `claude-workflow`/`claude-artifact` silently no-op'ing — the tool still absent from `/context` after running the wrapper — the first thing a future maintainer should reach for is `.claude/plans/disable-artifact-workflow-default.md`'s Verification step 2, the `CLAUDE_CONFIG_DIR`-isolated re-run recipe this decision's own precedence claim was originally verified against.

A second, distinct property is equally unverified against the real CLI. `claude-enable-tool.sh`'s refusal logic stops scanning at a literal `--`. This assumes the real `claude` binary treats `--` as an end-of-options marker, so a caller-supplied `--settings` placed after it cannot land a second, competing `--settings` flag. No automated test exercises this against the real binary, for the same no-interactive-session reason as the precedence property above. To re-verify: launch `claude --settings '{"disableWorkflows": false}' -- --settings '{"disableWorkflows": true}'` and confirm via `/context` that Workflow stays enabled — if the second `--settings` wins instead, the assumption is false. Re-run this recipe and Verification step 2 after any `claude` CLI version bump, not only once at implementation time.

### Sources

- `docs/design-decisions.md` §28 — the prior decision this entry reverses, including the schema-size figures and the citations to the Claude Code settings reference and settings JSON Schema.
- `.claude/plans/disable-artifact-workflow-default.md` — full assumption ledger, the go/no-go measurement, and Verification step 2's falsification test.

## 32. Worktree-lock fast path: reads stop reacquiring the lock (2026-08-28)

The fast path calls `_lib_worktree_collision_guard` only when the lock is already present, so a read never reaches the guard's "unlocked" diagnosis. An absent lock falls through to full parsing, where a read is allowed unconditionally and a write re-runs the guard. Acquisition is a write-only side effect on both paths.

The guard's contention tiebreak is first-write-wins: the first *write* — a git write via this hook, or a file write via `require-worktree-for-file-writes.sh`'s identical guard call — is what claims the worktree. This is the correct direction for a guard whose stated purpose is preventing two sessions from writing into one worktree. A read-only session was never the invariant it needed to protect.

Two tradeoffs are accepted rather than closed.

- The fast path's exclusion list (`cd`, `-C`, `(`, backtick) doesn't cover `||`/`&`. A `||`/`&`-chained git write such as `git fetch || git commit -m x` denies when the lock is absent, via the slow path's `||`/`&` write-cwd-ambiguity check. That check cannot distinguish a relocation-risky chain from a bare read-then-write chain with no `cd`. The deny names its own remedy and the window self-heals on the first lock acquisition from any source. Relaxing that deny for a no-`cd` chain is deferred to a separate change, since it loosens a security-relevant gate in the permissive direction and deserves its own review.
- The fast path's python3-free exit now requires an already-held lock, narrower than before:
  - A never-locked worktree's first git operation, read or write, falls through to full parsing.
  - A foreign-locked worktree's python3-less write denies citing python3 rather than the true foreign-lock holder, because the fast path checks only the guard's exit code, not its reason. `test_python3_absent_against_foreign_lock_gives_misleading_reason` pins this case.

### Sources

- `.claude/plans/worktree-lock-conditional-reacquire.md` — full assumption ledger, the over-powered-primitive check (a bash-side read/write pre-filter and a non-acquiring "peek" guard mode were both rejected), and the behavioral test matrix.

## 33. `skill-fidelity-reviewer`'s low cited-path edit rate is a citation-genre mismatch, not a reviewer-value signal (2026-08-30)

The corrected `reviewer-yield` measurement (GH-762, PR #764) puts `skill-fidelity-reviewer`'s zero-finding-bucket cited-path edit rate well below every peer reviewer's. §9 established this agent's charter and its own findings-rate re-measurement instruction; it contains no discussion of the cited-path column, so citing it for why that rate is expected is a misattribution. This entry, not §9, is the record's home for the cited-path reasoning below.

**Mechanism 1: the join key is lexical and hashed, so even the one branch shape where the citation is the work surface still cannot register.** `skill-fidelity-reviewer` resolves each skill by reading `~/.claude/skills/<name>/SKILL.md` (its Name resolution step) — a config-dir path. A branch in this repo that edits that same skill edits it through the stow source, `claude/.claude/skills/<name>/SKILL.md`. `_normalize_cited_path` is deliberately lexical — no `Path.resolve()`, `os.path.realpath`, or `stat` — and hashes the normalized string to a sha256 prefix. Two spellings of the same file produce two different keys and never join, so a branch that genuinely edits the cited skill in response to a finding still would not register as an edited cited path.

**Mechanism 2: on a clean pass the scanned output names specifications, not the branch's work surface.** Citations are drawn from the last assistant text plus every `Write` blob. With `findings_path` set, the inline return is a one-line pointer, and the substance lives in the findings file. On a clean pass, that file's content is a dismissal list naming skills and skill bodies — the specs the agent read, not the diff it was handed. A branch is not normally editing the skill it invoked, which is what keeps the numerator small.

Dispatch timing then inflates the denominator without touching that numerator. `/ready-for-review` spawns this agent once per branch at the last gate before handoff. The ship path at that point still produces real edits, but they are edits fixing other reviewers' findings, not this reviewer's own citations. `Active`, defined as "the session recorded any code edit at all," is a null control, not a path-specific one, so it is easily satisfied while the cited spec paths stay untouched.

Together this means the cross-reviewer `Rate` comparison is not like-for-like. A `staff-*` reviewer carries `Bash`, re-fetches the diff inside its own context, and cites the diff's own files — the files the session edits next. `skill-fidelity-reviewer` carries `Read, Grep, Glob, Write`, no `Bash`, and cites the specifications it checked the diff against. Ranking the two against each other measures citation genre, not reviewer value, so §25's scope-widening decision and §9's charter and re-measurement instruction stand unchanged: no routing, trigger-prose, or dispatch-condition change follows from this rate.

**Falsifier.** Cited paths are held only as sha256 digests and never surface as raw paths, so mechanism 2 cannot be checked against the tool's own output. It can be checked against the agent's own findings files under `agent-reviews/` in existing worktrees: if clean-pass findings files routinely name the branch's own diff paths rather than the skills and specs read, mechanism 2 is wrong and the low rate becomes a real "the session ignored what it was told" signal. The same follows if the agent's Output format later gains a required enumeration of files checked. Either observation reopens this entry.

### Sources

- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` — `_normalize_cited_path` (lexical, hashed join key) and `_reviewer_yield_cited_keys` (citation candidates drawn from the last assistant text and every `Write` blob).
- `claude/.claude/agents/skill-fidelity-reviewer.md` — Name resolution (config-dir path reads); Output format (findings-file substance on a clean pass).
- `claude/.claude/skills/ready-for-review/SKILL.md` — the once-per-branch dispatch step.
- `docs/transcript-analysis.md`'s `reviewer-yield` section — the `Cited`/`Active`/`Edited`/`Rate` column definitions and the digest-only redaction note.
- GH-762 / PR #764 — the reviewer-yield measurement fix this observation post-dates.

## 34. Reviewer responsibility bounded to the diff under review, uniformly, with default-branch and cumulative-pass guards (2026-08-29)

The redesign applies one uniform clause to every Change-type row: a spawn's exhaustive-enumeration duty is bounded to the diff already handed to it, but a defect outside that boundary the change causes, activates, or newly reaches stays in scope for the spawn's flagging duty. No per-row exemption list is needed to protect `ciso-reviewer`, `staff-sdet`, or any other row's cross-change reasoning.

The boundary computes no new ref: it is simply the diff a spawn is already being handed (`git diff --cached` for the commit-gate pass, the same basis `require-code-review.sh` hashes), restated as file paths and line ranges for reviewers without `Bash`.

`ready-for-review`'s cumulative PR-vs-base pass gets zero narrowing, enforced by a positive precondition rather than an opt-out flag: narrowing applies only when the diff under review is the currently-staged diff. Every context failing that precondition — the cumulative pass, a presentation-path review, an ad-hoc review — enumerates the full diff automatically, with no exclusion list to maintain. `ready-for-review/SKILL.md` carries its own mirrored applicability statement rather than relying solely on `code-review`'s precondition, because a session mid-way through several re-review rounds is exactly the case most likely to misclassify the cumulative pass as "just another round."

The precondition additionally requires `HEAD` not be the repository's default branch. A direct commit to the default branch is followed by no `ready-for-review` cumulative pass at all, so the guard forces full, unnarrowed enumeration of that one commit. Worktree enforcement makes this rare in this repo specifically, but `claude/` installs to every stow consumer and not every consumer opts into worktree enforcement. Cross-commit protection generally comes from the causal-reach clause, applied uniformly to every row, not from this guard specifically.

Responsibility-narrowing saves fix-loop churn, not reviewer reads — every non-prose reviewer still opens whole files for context, unchanged. Only the comment/prose row is additionally match-narrowed (it does not spawn at all when the boundary carries no comment/durable-doc prose), because it alone is closed-form with no cross-file reach; that is the one genuine token-read saving this design delivers.

**Named residual, not fixed here.** Two `/code-review` invocations against the same staged state with no commit between them see an identical boundary under this design — it does not distinguish "already cleared this round" from "never reviewed" within a round, because both hand the same diff. `SKILL.md`'s existing requirement to pass prior findings plus what's been applied on re-review is the standing mitigation.

### Sources

- `.claude/plans/scope-code-review-delta-rounds.md` — full assumption ledger, mechanism list, and out-of-scope residuals.

## 35. Skill evals run locally only; a CI eval harness stays declined (2026-08-29)

`evals/run_skill_evals.py` measures a skill's declared behavior by launching `claude -p` under the operator's own Claude Code subscription auth and reports a per-case pass rate a human reads. Wiring the same harness into GitHub Actions was evaluated and declined. A CI runner *can* authenticate — `claude -p` accepts an `ANTHROPIC_API_KEY` — so the deciding ground is cost, not reachability:

- Every sample is a full headless session.
- Cost scales as K samples × cases per skill.
- `disposition-fidelity` adds about four `claude -p` calls per sample on top of that.
- All of it bills per token, off-subscription.

Two secondary grounds stand independently:

- Triggering is probabilistic, so a single-sample binary pass/fail is flaky.
- A public-repo workflow would need `--dangerously-skip-permissions` over PR-authored content.

`evals/README.md`'s "Why local only — never CI" section holds the full statement and is the site to update when one ground changes. The same posture covers `evals/measure_subagent_model_resolution.py`. The substitute for CI coverage is a manual pre-merge run against the skill the change touches, with the pass rate recorded in the PR description.

## 36. Auto-clearing a dead-PID worktree lock via a release-free claim file (2026-08-28)

`_lib_worktree_collision_guard` (`claude/.claude/hooks/_lib.sh`) denied a write against a worktree whose lock it had just conclusively proven dead via `kill -0`, requiring a human to run `git worktree unlock <path>` and retry manually. This mechanism accounted for 48 identical deny-then-manual-unlock-then-retry cycles in a two-week window (GH-754), and a background subagent dispatch hitting the same lock had no way to clear it at all, since approving the manual unlock needs a human the dispatch does not have (GH-747).

The fix makes eviction of a proven-dead lock a **once-only right, claimed by an exclusive create and never released**: before removing a lock it has proven dead, a caller must win an `O_EXCL` create of a per-lock-identity claim file in the worktree's own admin directory (`<wt-git-dir>/claude-evicted-lock-<dead-pid>-<dead-session-id or nosession>`). Winning the claim reads the raw lock file and unlinks it in the same subprocess, only if its content still matches the holder it proved dead — closing the window a separate reread-then-delete call pair would leave open between confirming the lock's content and removing it. No other *evictor* holding the same claim can land a write in between. The residual window this narrows to, rather than closes, is a manual `git worktree unlock` plus a third party's fresh acquisition landing within that one subprocess's own read-then-unlink instructions, which could still in principle race it. Closing that fully would need an OS-level primitive (`flock`/`lockf`), rejected below for cross-platform reasons. The winner then re-acquires the lock through the guard's existing atomic acquisition path (`_lib_worktree_acquire_lock`). The guard returns 0 on a successful reclaim, so the Edit/Write/git call that triggered the hook proceeds in the same `PreToolUse` invocation — no manual `git worktree unlock`, no retry. Every losing claimant denies with the existing message, reworded since the guard now attempts a clearance rather than only diagnosing one: `this worktree is locked by pid %s, which is no longer running, and could not be cleared automatically — clear it with \`git worktree unlock %s\` and retry`.

A successful reclaim is less observable than an ordinary first-time acquisition on a virgin worktree. An ordinary acquisition emits an `additionalContext` note (via `_lib_emit_allow_with_context`) explaining that the write just re-acquired the lock; a reclaim never does. That note's callers gate it on `_lib_worktree_lock_absent`, which tests only whether the lock file was absent before the guard's own write. That check is never true for a reclaim, since a lock file being present is the reclaim's own precondition. The claim file is therefore the only after-the-fact detection surface a reclaim gets. This is why the Revisit triggers below already recommend periodically grepping for orphaned claim files: it is the same surface, not a new recommendation.

**Supersedes** `.claude/plans/worktree-collision-guard.md:86-108`, which rejected in-hook eviction on exactly this design shape: "git's `worktree lock` is exclusive-create but `worktree unlock` is not compare-and-swap, there is no race-safe way to auto-evict inline without adding a second coordination layer on top of the first." That reasoning is still correct, and this design doesn't argue with it — it adds the second coordination layer the prior analysis said was needed, but the layer is **exclusive-create-only, with no unlock half at all**. The prior rejection's own live-reproduced counterexample was a race in the *unlock* half of an evict-then-relock sequence (one session unlocks-and-relocks while a second session's stale `unlock` call lands afterward and strips the first session's fresh lock); a claim that is created once and never removed has no unlock half to race. A conventional acquire/release mutex would reintroduce the same problem one level up, since a mutex that leaks (the hook killed mid-critical-section) needs staleness handling, and stale-mutex reaping is itself an unconditional unlink racing a second reaper — never releasing is what makes the claim race-safe where releasing safely could not be made race-safe.

Cross-reference: §29 fixed a different bug in the same function (self-recognition keyed on `session_id` instead of PID, so a resumed session's own lock survives `claude --continue`). This entry builds on that format directly — the claim filename's `<dead-session-id or nosession>` component is the same session_id §29 introduced, falling back to a `nosession` placeholder for a pre-§29, old-format lock that carries no session_id field.

Rejected alternatives, from lightest to heaviest:
- No coordination at all (unlink-then-relock with no claim) — the exact shape §29's plan document already live-reproduced as unsafe.
- The existing `O_EXCL` create on the lock file alone, with no separate claim — exclusive for *creating* the lock, but eviction needs an *unlink* first, and unlink is unconditional; the unlink-to-create window stays unprotected.
- The existing post-write verification re-read alone — two evictors can each verify their own write at a different instant and both return 0, which is why the guard's `TestCollisionGuardRereadRace` tests assert a fail-closed outcome rather than treating that re-read as a mutual-exclusion primitive.
- `git worktree unlock` plus a new ownership token in the reason string — the token has to be checked and then acted on in two separate steps, so it's the same non-atomic sequence with an extra field, and `unlock` still has no ownership check, so anyone can strip the token-bearing lock regardless.
- A long-lived daemon or watcher reaping dead locks out of band — a far more privileged, invasive execution context (a persistent process, its own lifecycle, its own crash story) for a problem one file create solves, and it would still race a live session acquiring a lock it's mid-reap.
- OS-level `flock`/`lockf` — neither CLI is present on both platforms this hook runs on (macOS ships `lockf` but not `flock`; CI's `ubuntu-24.04` ships `flock` but not `lockf`), and reaching `flock(2)` from bash would need a `python3` spawn, adding a dependency this guard doesn't otherwise carry.
- **A runtime kill-switch** (a `.claude/worktree-optout`-style file gating auto-eviction specifically) — rejected as unnecessary ceremony. Reverting the `_lib.sh` diff cleanly restores deny-only behavior with no migration step: any claim files already created by the time of a revert become inert, unused files (bounded by worktree lifetime, since they live in the worktree's own admin directory and are removed with it). A code revert is an adequate rollback path, so a parallel runtime toggle would be a second lever doing the same job the first already does.

### Sources

- `.claude/plans/auto-clear-dead-worktree-locks.md` — full plan, assumption ledger, and mechanism justifications.

**Revisit triggers.** Re-count locked-but-dead worktrees on a developer machine after ~30 days and compare against the pre-fix baseline (70 worktrees locked, 61 of 66 unique locking PIDs dead, at the time this plan was authored) — a count that hasn't dropped means auto-eviction isn't firing in practice, not that the problem went away. Separately, check for orphaned claim files (`find <repo>/.git/worktrees/*/claude-evicted-lock-* ` across active worktrees) left behind by an interrupted eviction — each one permanently disables auto-eviction for that one lock identity in that one worktree, and a growing count over time would indicate the harness is killing hooks mid-reclaim more often than the design's bounded-worst-case reasoning assumed.

## 37. `plan-architect` widened to a second, ad hoc consult mode instead of a new agent (2026-08-30)

Widen `plan-architect` in place rather than add a second agent for ad hoc mid-session Opus architectural consults, or merely document the existing `general-purpose` + `model: opus` path. A subagent's `description` loads into every session's base context with no flag, plugin-scoping, or lazy-discovery mechanism to avoid it — the only documented mitigation is keeping the description short and pushing detail into the body, which loads only on dispatch (Anthropic, *Sub-agents*, "Subagent Startup Context Loading"). A second agent would add a second such description, unconditionally, forever; widening `plan-architect` adds zero new base-context cost, since its frontmatter is a restructured description of comparable size, not an additional one.

`general-purpose` + `model: opus` is rejected on the same grounds §30 already rejected it for Step 5: it carries `Write`/`Bash`/`Skill` unconditionally, none of which a design-consult dispatch should hold, and has no durable charter of its own — every instruction `plan-architect.md`'s body carries once would have to be restated in the dispatch prompt on every call. `plan-architect` already has the right shape for this — read-only, `model: opus`, `effort: xhigh`, no `Agent` tool — it was hard-scoped to one caller and one output grammar, not to a narrower privilege set.

§30's cost-isolation rationale survives as: no whole-session Opus anchor, no cascade (no `Agent` tool), and a frequency bound on every call site. What is genuinely weakened: Step 5's bound is structural (once per plan run, with a plan run as its denominator); the consult bound is behavioral (a human's asking rate, which has no ceiling). That weakening is accepted because the counterfactual is not "no Opus spend" — these dispatches already happen via `general-purpose` + `model: opus`, so the expected marginal Opus spend from this change is ~zero, and routing them to `plan-architect` instead is a privilege reduction and a token saving. This yields a falsifiable prediction: post-launch Opus spend share should be flat or lower, and growth attributable to `plan-architect` runs specifically (read off `subagent-mix`'s per-`agentType` `Runs` and `Actual$`, since an aggregate share alone can't attribute growth to this agent) falsifies the "these dispatches already happen" premise.

Mode selection is an explicit `MODE=plan-sections` / `MODE=consult` literal on the dispatch prompt's first line, not inferred from prompt shape. Inferring from evidence-carrying vs. bare-path shape fails because Step 5's revision re-dispatch (a plan file path only) is a third shape that would collide with a bare consult, and because the harmful misfire direction is real: freeform consult prose landing verbatim in a committed plan file. The literal costs one line per caller and removes both failure modes. A dispatch carrying no `MODE=` line defaults to consult — the fail-safe direction, since an unmarked dispatch then produces conversational prose and no durable artifact, where defaulting to plan-sections would instead make every forgotten-marker consult read a 142-line skill file and answer in plan grammar it was never asked for. The residual case — a Step 5 dispatch whose marker is dropped in a reword — is already caught by Step 5's existing "a return that ignores the required grammar → re-dispatch from scratch" rule (`plan-it/SKILL.md`).

The explicit-user-ask gate that scopes the consult call site is a documented-convention constraint on the dispatching session's judgment, at the same enforcement tier as Step 5's existing single-caller premise — not mechanically detectable, and deliberately not made so. A subagent cannot observe the conversation that produced its own dispatch: it receives the dispatch prompt plus its startup load and nothing else, so it cannot itself verify a user asked for it. Any in-body self-check could only ask the prompt to assert the ask happened, and a prompt that asserts it is exactly what a wrongly-motivated caller would write — a compounding defensive layer closing nothing. The preventive control is the statement at the decision point (the agent's own `description` plus the CLAUDE.md routing bullet); the detective control is the spend-share measurement above.

### Sources

- `docs/design-decisions.md` §30 — the prior decision this entry extends, including the rejection reasoning for `general-purpose` + `model: opus` and the residual risks this entry's forward-pointer sentence updates.
- `.claude/plans/plan-architect-scope.md` — full assumption ledger, per-mechanism reasoning, and the Verification steps that measure the spend-share prediction.
- Anthropic, *Sub-agents* — https://code.claude.com/docs/en/sub-agents — confirms a subagent's `description` loads into every session's base context with no lazy-discovery mechanism.
## 38. Universal prose rules promoted from the personal output-preferences layer into the global CLAUDE.md (2026-08-31)

Response-shape, concision, and sentence-craft rules are not personal taste, but they lived only in `<config-dir>/output-preferences.md`. That file is uncommitted and per-user, and it reaches a session only through an optional `Read` instruction inside `claude/.claude/CLAUDE.md`. `claude/.claude/CLAUDE.md` itself stows to every consumer's user-scope `CLAUDE.md` and loads before the first turn, for the session and for every subagent except `Explore`/`Plan`. Placing non-taste rules on the weaker of the two surfaces meant a subagent, or a fresh consumer who never created the personal file, drafted without them, and `tighten-prose` absorbed the cost afterward by rewriting prose that had already been produced instead of shaping it up front.

Four already-decided rules moved into the new `## Prose and Output Format` section as bullets: lead with the answer or action taken and skip closing restatement; let shape follow content (prose for a single concept, a list for parallel items, headers only past ~15 lines, matching code-block language tags, no width-fragile tables in terminal output); cut sentences that add no information while never dropping a fact to shorten one; and the fold of the old `## Output Preferences` pointer as the section's closing bullet. A fifth already-decided rule, the don't-know clause, folded into `## Working Style`'s existing "Be precise" bullet instead of becoming a new bullet in the new section, since that bullet already calibrates what gets asserted against what is actually known. Tone and emoji avoidance stayed in the personal file: both are taste rather than correctness, and a consumer who wants different tone should get it by editing their own file rather than by contradicting a rule every other consumer also loads.

A mining pass over `tighten-prose` §4 promoted four more rules on the reasoning that a rule worth applying reactively to every drafted PR body is worth stating before the draft exists: one idea per sentence, one term per concept, active voice, and plain verbs over inflated ones and noun stacks. §2's overriding constraint — never drop or flatten a fact, number, decision, hedge, or conditional to shorten a sentence — was promoted in the same bullet as the concision rule, because brevity guidance stated without it teaches shortening by dropping qualifiers, which is a correctness regression rather than a style improvement.

Two `tighten-prose` rules were declined rather than promoted. The ~20–25-word sentence target depends on §3's semantic carve-outs (hedges, quantifiers, negation, conditionals) sitting next to it for safe application, and those carve-outs are too long for a 200-line always-loaded file; a bare numeric target pushes toward splitting exactly the sentences that must not be split. §3's whole-sentence-class carve-out (deploy and coordination steps, security-invariant claims, reviewer action items) fires only inside `pr-description`'s flow and defers to that skill's own coordination-step-preservation section, so it belongs in the skill body it already lives in, not in CLAUDE.md.

`comment-discipline-reviewer` gained no new rule to enforce: all six of its review angles were already stated in `claude/.claude/CLAUDE.md`'s Code Comments section before this change. Per §9, that reviewer is a fresh-context sweep against a rule the authoring session already had loaded, not a backstop for a missing rule, so the gap it closes is authoring-session satisficing on a rule already in context — a gap no CLAUDE.md line can close. This change should be expected to move `tighten-prose`'s rewrite volume and not that reviewer's finding volume.

The new section's "one idea per sentence" restates the core of §Code Comments' "Split multi-fact comments" bullet, and the overlap is deliberate rather than an oversight. It stands under the Engineering Judgment section's "a small duplicated value that beats a bad abstraction" exception, not the "instructional prose that must stand alone" exception — both sections live in the same always-loaded file behind the identical load path, so no consumer ever sees one without the other, which is what the stand-alone exception is meant to protect against. Four files at six sites (`claude/.claude/skills/code-review/SKILL.md`, `claude/.claude/agents/code-writer.md`, `claude/.claude/agents/comment-discipline-reviewer.md`, `claude/.claude/skills/plan-it/SKILL.md`) cite the Code Comments section by name as a self-contained rule set, so trimming it to defer upward would ripple into those sites — including a `SKILL.md` edit that would pull hook-enforced `/skill-review` into a prose-only change — for a saving of one repeated clause. The comment-scoped bullet also carries a remedy the general rule does not: an explicit list when the facts are genuinely parallel.

### Sources

- `claude/.claude/skills/tighten-prose/SKILL.md` §2 and §4 — the mined rule list and the preserve-every-fact constraint.
- `claude/.claude/agents/comment-discipline-reviewer.md` — the six review angles, all already covered by existing CLAUDE.md rules.
- `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md`'s length-targets section — the 200-line cap and the per-line behavior test each promoted line was drafted against.
- `claude/.claude/skills/plan-it/SKILL.md` Step 5 — subagent CLAUDE.md loading, and the `Explore`/`Plan` exception.

## 39. `claudeMdExcludes` suppresses the nested-discovery duplicate of `claude/.claude/CLAUDE.md` (2026-08-31)

Nested-CLAUDE.md discovery walks the physical filesystem tree rather than deduplicating against files already loaded through a symlink. A session working in this repo therefore loaded the global-instructions file twice: once at user scope through the `~/.claude/CLAUDE.md` stow symlink, and again as a fresh system-reminder block the first time anything under `claude/.claude/**` was read.

The exclusion is added to this repo's own project-scope `.claude/settings.json` rather than to any per-machine `settings.local.json`, because the duplicate only occurs for a session whose working directory is inside this repo — every contributor hits it, so the fix belongs where every contributor's `git pull` picks it up.

The stow-source `claude/.claude/settings.json` deliberately carries no matching entry. That file installs to every consumer's user-scope `~/.claude/settings.json`, so an entry there would apply to every project on every consumer's machine — wider than the condition it addresses, which is specific to sessions working inside this repo.

Renaming or relocating `claude/.claude/CLAUDE.md` to dodge the duplicate was rejected: Stow links each immediate child of `claude/.claude/` individually (§5), so moving the file breaks the 1:1 install mapping every consumer runs.

Whether `claudeMdExcludes` also matches a symlink's target path is undocumented for CLAUDE.md files (only for `.claude/rules/`), so this pattern's correctness was unverified until a fresh session confirmed it empirically. That fresh session confirmed both that the user-scope load survives and that the pattern does not over-suppress. See the plan's assumption ledger (`.claude/plans/exclude-nested-claude-md-duplicate.md`) for the full ledger row.

### Sources

- [Claude Code memory docs](https://code.claude.com/docs/en/memory), "Exclude specific CLAUDE.md files" — `claudeMdExcludes` setting, path-matching semantics, and the either-path symlink rule for `.claude/rules/` files.
- `.claude/plans/exclude-nested-claude-md-duplicate.md` — full assumption ledger and verification steps.

## 40. Attribution stays in skill prose and a hook gate rather than the native `attribution` settings key (2026-09-01)

The `attribution` settings object has three properties: `commit`, `pr`,
and `sessionUrl`. Together they reach two surfaces: commit messages and
pull request *bodies*. Neither this repo's `.claude/settings.json` nor
the stow-source `claude/.claude/settings.json` sets it. None of the
attribution prose in `pr-description/SKILL.md` or `respond-pr/SKILL.md`
can be deleted in its favor, for three independent reasons below.

**The largest surface is out of the setting's reach entirely.**
`respond-pr/SKILL.md`'s Attribution section requires every PR or issue
comment reply to open with `**[Claude Code]**` and close with the
disclosure trailer. `require-respond-pr.sh` denies `gh pr comment`,
`gh issue comment`, `gh pr review`, the REST `comments`/`reviews`
endpoints, and the equivalent GraphQL mutations at the tool-call
boundary unless the write is routed through that skill. `attribution`
has no property covering review comments, so no candidate key exists to
replace any of it.

The prefix also does a second job no attribution string could. Replies
post through the user's own GitHub token and appear under the user's
account, so a `user.login` check cannot distinguish a reply Claude wrote
from a comment the user wrote. The prefix is what `respond-pr` checks
before a PATCH edit to avoid overwriting the user's own text
irrecoverably, so deleting it would remove a data-loss guard, not only a
disclosure line.

**On the one surface both cover, the required shapes differ.**
`pr-description/SKILL.md` places the trailer at both the first line and
the last line of the body. `attribution.pr` is a single appended string,
so it could supply the bottom copy at most, leaving the skill to
prescribe the top one regardless.

**The setting sits at the advisory tier §1 distinguishes from a hook.**
Its effect is mediated by an instruction the model receives and must
then choose to follow, not by post-processing applied to the commit or
body after the fact, so it is exposed to the same reasoned-around
failure that motivated hook enforcement everywhere else here. Two
upstream reports show that tier failing. anthropics/claude-code #65657
reports the system-prompt `Co-Authored-By` trailer taking precedence so
the `attribution.commit` value is never applied. It is closed as not
planned. #77830 reports the `Claude-Session:` trailer being injected
through the Bash tool description and ignoring `attribution`. The legacy
footer *is* correctly suppressed by the same setting — only the newer
trailer ignores it. #77830 is closed, labeled a bug, and marked
reproduced.

The two reports disagree about which half fails. Both were read as
rendered issue pages rather than full comment threads, so the
discrepancy is recorded here rather than resolved. Either report alone
is enough to rule out trading mechanically-gated prose for the setting.

Commit trailers are a separate question from PR bodies. No hook and no
`CLAUDE.md` line prescribes commit-trailer text anywhere in this repo,
so setting `attribution.commit` now would invent a convention rather
than codify an existing one. #65657 above already documents that exact
mechanism as unreliable for the commit case. Setting it in the
stow-source `claude/.claude/settings.json` instead would compound the
problem by imposing that invented choice on every consumer's every
repository — wider than any condition this repo has, the same scoping
reasoning §39 applies to `claudeMdExcludes`.

`sessionUrl` was evaluated separately as the one property that would
have changed behavior, by suppressing the `Claude-Session:` deep link
this public repo's commits and PR bodies otherwise carry. It is left at
its default of `true`. The link resolves against the owner's own
account. Claude Code sessions are private by default regardless of the
surface that started them. They become visible to anyone else only when
their owner explicitly shares that session. The schema documents the
trailer as appended when running from a web or Remote Control session.
Whether it also appears on commits authored from a plain CLI session is
not established. #77830 does not name the surface it reproduced on.

**Revisit** when `attribution` gains a property covering PR or issue
review comments, or when both reports above close with a shipped fix.
Neither condition alone is enough: a fix with no review-comment property
still leaves `require-respond-pr.sh` and `respond-pr`'s prefix doing
work nothing else does.

### Sources

- [Claude Code settings reference](https://code.claude.com/docs/en/settings), Attribution settings section — the `attribution` object, its three properties, and the two surfaces it covers.
- [SchemaStore `claude-code-settings.json`](https://www.schemastore.org/claude-code-settings.json) — machine-readable property list confirming no review-comment property exists, and `sessionUrl`'s documented web/Remote-Control scoping.
- [anthropics/claude-code #65657](https://github.com/anthropics/claude-code/issues/65657) — report that the system-prompt trailer overrides `attribution.commit`; closed as not planned.
- [anthropics/claude-code #77830](https://github.com/anthropics/claude-code/issues/77830) — report that the `Claude-Session:` trailer is injected via the Bash tool description and ignores `attribution`; closed, labeled a bug and reproduced.
- [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web), "Share sessions" — sessions are private by default and become visible to others only on an explicit share.
- `claude/.claude/skills/respond-pr/SKILL.md` — the Attribution section's prefix-and-trailer requirement, and the prefix's pre-PATCH self-authorship check.
- `claude/.claude/skills/pr-description/SKILL.md` — the top-and-bottom trailer placement rule.
- `claude/.claude/hooks/require-respond-pr.sh` — the tool-call-boundary gate routing every comment write through the skill that applies the prefix.

## 41. `ScheduleWakeup` misapplied outside `/loop`: documented, not guarded (2026-09-01)

**Superseded by §49 (2026-09-04):** its mechanism-exhaustion claim never reached the settings layer, where a bare-tool-name `permissions.deny` removes the tool from context entirely instead of rejecting a call after the fact. The three mechanisms rejected below remain correctly rejected.

This repo will not add a hook, advisory nudge, or `CLAUDE.md` line to catch a recurring pattern: the assistant reflexively calls the built-in `ScheduleWakeup` tool as a fallback heartbeat after dispatching a subagent or backgrounding a Bash command, outside the `/loop` dynamic mode the tool is scoped to. `ScheduleWakeup`'s own description (Claude Code 2.1.258) states the scope and the prohibition directly: "Schedule when to resume work in /loop dynamic mode... Do NOT schedule a short-interval wakeup to poll for background work you started — when harness-tracked work finishes, you are re-invoked automatically, so polling is wasted." The misuse violates a constraint already present, verbatim, in the tool's own always-loaded description — this is not a documentation gap this repo can close.

A review of this repo owner's own session history found the pattern recurring across many different session types — planning, code review, plain chat, an eval harness — never inside an actual `/loop` session. Two failure sub-modes were observed, both self-corrected without user intervention in every case:

- A call omitting the required `prompt` field is rejected instantly by the harness's own schema validation.
- A well-formed call actually schedules a wakeup, which later fires and re-invokes the session before the assistant recognizes the mismatch and cancels it.

Counts and durations are not reproduced here: the corpus mixes this owner's private-project and public-repo transcripts, so an aggregate figure would inherit the private half. See the upstream reports below for the full evidence.

`ScheduleWakeup`'s own description doesn't name the latent risk directly, but an adjacent one is real: the `Agent` tool's description warns "Never fabricate or predict a pending agent's results" for exactly the moment a stray `ScheduleWakeup` wakeup could land — mid-pending-dispatch. None of the observed instances actually fabricated or predicted a result. Every one cancelled cleanly and resumed. The clean outcome is what was observed here, not a property the pattern guarantees.

Three repo-side mechanisms were considered and rejected:

- **A `PreToolUse` gate denying `ScheduleWakeup` outside `/loop`.** No reliable predicate exists to gate on. `/loop` is harness-native, with no `SKILL.md` for it anywhere in this repo or on the local filesystem. It also has no marker analogous to the one `require-routing-read.sh:33-60` uses for the repo-owned `/plan-review` skill. The two candidate substitutes both fail:
  - A `ScheduleWakeup` prompt-shape check catches only the sentinel-literal case, not an arbitrary real `/loop` prompt.
  - An inferred read of the transcript's own command-invocation marker fails two ways:
    - It answers the wrong predicate — "did this session ever run /loop" rather than whether the current call is legitimate.
    - Even that wrong predicate relies on an undocumented format.

  Either substitute inverts `require-routing-read.sh`'s fail-open safety property into a fail-closed gate keyed on absence of evidence — guarding the one workflow that runs unattended and can't report its own breakage.
- **A `PostToolUse` advisory nudge** (the shape `consume-durable-continuity-file-on-read.sh` uses). It would re-deliver a constraint already sitting in the model's context at the moment of the misfire. It would also fire on every turn of a genuine `/loop` session — noise aimed at the one population the tool exists for.
- **A `CLAUDE.md` line restating `ScheduleWakeup`'s own scope.** Rejected as a repo-side copy of harness-owned text this repo can't keep in sync. It would also cost budget that `check-claude-md-length.sh` polices.

The correct alternative already lives in this repo. `handoff/SKILL.md`'s collect-in-flight-dispatches step tells a session to end its turn and let the harness's automatic notification arrive rather than poll. `.claude/plans/handoff-hard-block.md` separately rejected `ScheduleWakeup`-based active polling on the same "polling reimplements what the notification path already delivers passively" reasoning. That skill loads only when `/handoff` runs, though — it was not in context in any of the observed misfires, which happened in other skills entirely. The constraint that *was* in context every time is `ScheduleWakeup`'s own description; no repo-owned surface reaches all of them.

**Revisit** if any of:

- `ScheduleWakeup` itself starts rejecting calls outside `/loop`, or a `PreToolUse` input field exposes the harness's own computed active-mode state, making inference unnecessary.
- An instance surfaces where the misfire doesn't self-correct cleanly — a fabricated or predicted result, or one needing user intervention.
- A materially higher well-formed-call rate appears on large-context sessions, where a full context re-read per misfire becomes a real `docs/cost-ledger.md` line item.

### Sources

- [anthropics/claude-code #80350](https://github.com/anthropics/claude-code/issues/80350) — "Agent invokes ScheduleWakeup prematurely instead of waiting for subagent notifications," open, matches this pattern directly.
- [anthropics/claude-code #88260](https://github.com/anthropics/claude-code/issues/88260) and [#88205](https://github.com/anthropics/claude-code/issues/88205) — the missing-`prompt`-field validation-error sub-mode.
- `claude/.claude/hooks/require-routing-read.sh:33-60` — the marker-as-predicate mechanism and its fail-open default, and why it doesn't transfer to a harness-owned skill.
- `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh` — the advisory-nudge shape considered and declined here.
- `claude/.claude/skills/handoff/SKILL.md` and `.claude/plans/handoff-hard-block.md` — this repo's own prior rejection of `ScheduleWakeup`-based polling, and the correct alternative's load-scope limits.
- `.claude/plans/harness-context-mismatched-tool-dispatch.md` — full assumption ledger, per-mechanism reasoning, and the corpus-evidence sourcing this entry summarizes.

## 42. `/code-review`'s Fix-route step routes a mechanism-inventing fix through `plan-architect`, dispatched rather than hooked (2026-09-02)

Step 1's implementation-fitness gate evaluates the diff as written; nothing in `/code-review` evaluated the fix a finding's own disposition was about to cause. An ADDRESS finding whose proposed fix introduces a mechanism heavier, more privileged, or wider-scope than the surface already under review — a new hook, a new agent, a new coordination primitive — could be authored inline or dispatched straight to `code-writer` like any other fix, bypassing every design gate the surrounding work went through to get written in the first place. `code-review/SKILL.md`'s `## Finding disposition` step now closes that gap: such a fix is a design question, not a fix, and is routed to `plan-architect` — through `plan-it` Step 5's existing revision re-dispatch when the branch has a plan file, or a direct `MODE=consult` dispatch when it doesn't — before anyone writes it.

**A dispatch, not a hook.** A `PreToolUse` gate on `Write`/`Edit` was rejected on three independent grounds. The tool payload carries a path and content, never a semantic label for "this is a fix I designed mid-review" — there is no predicate at the decision point. A Write-time gate would also fire after the design choice is already made, the wrong moment to catch it. The same need would recur here: distinguishing a legitimately-planned new mechanism from an unplanned one needs its own cross-turn marker, the identical compounding-layers shape §41 already declined for a structurally different case (an unenforceable `ScheduleWakeup` misuse). Both cases add a defensive layer whose only job is to close a gap the layer below it created.

**The plan-file route is hook-backed in practice, even though the rule itself is advisory.** The new `code-review` paragraph is prose the model must choose to follow, the same enforcement tier as any other skill instruction. But its compliance act — inserting `plan-architect`'s returned sections into the plan file — is *editing the plan file*, and `require-plan-review.sh` re-arms on any modified plan file and denies every subsequent non-plan `Write`/`Edit` until a marker covering the new plan state exists. The failure this closes escaped detection precisely because the plan file was never touched: the design shipped as prose, across several autonomous handoffs, with no plan-review gate ever re-arming. Routing the fix through the plan file converts an advisory rule into a hook-backed one on the branch where the failure actually occurred, without writing a hook. The no-plan-file consult route carries no equivalent backstop — it stays advisory, same as the rest of `code-review`'s disposition step.

**§37's ad hoc consult gate is unchanged; this is a second, skill-prescribed call site.** `plan-architect.md`'s description and `claude/.claude/CLAUDE.md`'s Model & Effort Routing bullet both bar a session from dispatching `MODE=consult` on its own initiative — that constraint governs a session's own judgment that a decision looks architecturally significant, and it stays exactly as strict. `/code-review`'s Fix-route step is not that: it is a skill prescribing the dispatch, which CLAUDE.md's Agent Briefing section already covers under "a prescribed dispatch is an authorized dispatch." Both surfaces got a one-clause pointer to that rule rather than a restatement, because the failure direction of a misread is a session declining the dispatch and free-handing the design instead — the exact failure this entry closes.

**The rejected alternative: consult `plan-architect` unconditionally on every ADDRESS finding, no gate.** This repo logged 319 `/code-review` invocations across 120 distinct branches in the 2026-08-01+ window, against 41 `plan-architect` runs over the same 30-day window (both published in `docs/case-studies/opus-frontload-review-rounds.md`). An unconditional route would put a ceiling of roughly 9x current `plan-architect` volume. No corpus instrument today reports ADDRESS-findings-per-round, so that multiplier's floor cannot be narrowed further. The alternative was set aside on three further grounds:

- It does not remove the contestable judgment, only relocates it to arguing the rule's scope — the same failure this file's existing invalid-rationale lists already document happening elsewhere in this skill.
- It does not shrink the design — the consult-vs-revision routing choice and the dispatch's own question both survive, and a third de facto genre inside `MODE=consult` would re-open what §37's explicit mode literal exists to close.
- It is inconsistent with *Ripple effect triage* one section earlier in the same file, which resolved the identical dispatch-cost-vs-under-dispatch tension as gate-plus-mandatory-visible-rationale, not spawn-everything.

**Revisit** if either of these:

- A later `review-trace` pass shows the gate firing at or near zero while fixes that add a new hook, agent, or coordination primitive keep landing — evidence the trigger's two limbs are being reasoned around rather than the gate simply not firing often.
- `plan-architect`'s monthly run count attributable to this call site drifts unboundedly instead of tracking the observed ADDRESS-finding rate for mechanism-proposing fixes — checkable via `subagent-mix --this-repo`'s per-`agentType` `Runs`, mirroring §37's own spend-focused falsifiable prediction for its call site.

This is a third frequency-bound call site alongside §30's and §37's, extended rather than restated: bounded by how often a review finding proposes a new mechanism, not by a session's own asking rate (§37) or by once-per-plan-run (§30).

### Sources

- `.claude/plans/repo-scoped-issue-triage.md` (PR #807) — the originating incident: an ADDRESS finding's fix invented a new agent and hook in prose across several handoffs before being caught.
- That plan's assumption row 19 — the hardest sub-decision behind this entry's closed list of invalid skip rationales.
- `docs/design-decisions.md` §30 and §37 — the frequency-bound reasoning this entry extends with a third call site, without editing either entry.
- `docs/design-decisions.md` §41 — the compounding-layers precedent for declining a marker-based hook here.
- `docs/case-studies/opus-frontload-review-rounds.md:35,112` — the 319/41 repo-scoped volume figures cited above.
- `claude/.claude/hooks/require-plan-review.sh` — the re-arm-on-plan-file-modification behavior that hook-backs the plan-file route.

## 43. UI and notification preference keys ship as shared defaults in the stow-source settings file (2026-09-02)

Stow installs `claude/.claude/settings.json` as every consumer's user-scope
`~/.claude/settings.json`. Claude Code's settings precedence has five
levels: managed policy, command line, project-local
(`.claude/settings.local.json`), shared project (`.claude/settings.json`),
and user (`~/.claude/settings.json`). None of the five is a user-scope
*local* file.
`~/.claude/settings.local.json`, where a stow user's own
`claude/.claude/settings.local.json` lands, therefore corresponds to no
documented scope, and a direct test confirmed `tui`, `theme`, and
`agentPushNotifEnabled` are not honored there. While the user settings file
is a symlink into this repo, a personal preference and a shipped default are
the same bytes; overriding one means editing the tracked file, the tradeoff
every key in that file already carries.

All three are committed here. `tui: "fullscreen"` has the functional case —
the alternate-screen renderer offers three things the classic renderer
doesn't:

- No redraw flicker.
- Flat memory across long conversations.
- Mouse support (click-to-expand tool output, click-and-drag selection,
  copy-to-clipboard on mouse release).

`theme: "dark"` and `agentPushNotifEnabled: true` have no equivalent
justification; they are defaults, not capabilities.

**The session-keys guard is a drift gate, not a presence ban.**
`guard-settings-session-keys.sh` compares the staged file against `main` key
by key, so a guarded key holding a committed value keeps its protection: a
later `/config` theme change or `/tui classic` still denies the commit that
would ship it. `model: "sonnet"` already sits in this file under that
arrangement. The gate has no agent-side proceed path by design, so the
commit that first introduces a guarded key's value is run by the engineer
directly rather than by a Claude Code session, and the guarded key list stays
untouched. Dropping `theme` and `tui` from that list to unblock one commit
was rejected. Claude Code writes both keys itself. The file is also
reachable through the user-scope symlink, the accidental-commit path the
keys were added to catch in the first place.

**Two entries this file cannot host.** `enabledPlugins` has no per-account
expression under this layout — every Claude Code account on a machine
resolves `settings.json` through the same symlink, so a plugin enabled for
one account is enabled for all. A toggle wanted for a single account, and an
`extraKnownMarketplaces` entry naming one machine's absolute checkout path,
were both dropped rather than committed; the second would be wrong for any
other consumer, whose checkout is elsewhere. A real per-account settings file
belongs in the machine's own provisioning, outside this repo.

**Open discrepancy, not resolved here.** The settings reference lists
`enabledPlugins` and `agentPushNotifEnabled` with scope "Any file", defined
there as effective in all four settings locations including Local.
`.claude/rules/settings-json-conventions.md` states the opposite for
`enabledPlugins`. Both cannot be right, and nothing in this repo tests the
question.

### Sources

- [Claude Code settings](https://code.claude.com/docs/en/settings) — the five-level precedence model and each level's file path.
- [Claude Code settings reference](https://code.claude.com/docs/en/settings-reference) — per-key scope column, including the "Any file" scope cited above.
- [Fullscreen mode](https://code.claude.com/docs/en/fullscreen) — alternate-screen renderer behavior, memory, and mouse support.
- `claude/.claude/hooks/guard-settings-session-keys.sh` and [`docs/hooks.md`](hooks.md) — the guarded key set and the staged-vs-`main` comparison.

## 44. `ready-for-review`'s cumulative-diff review cache: a fifth content-addressed marker kind (2026-09-02)

`ready-for-review` step 3 ran a mandatory, fully-unnarrowed `/code-review` pass over the entire PR-vs-base diff on every push to a branch with an open PR, including a push that only rebases or merge-syncs onto a moved default branch with zero conflicts and zero content change. The gating marker (`ready-for-review-markers`) is keyed on the exact HEAD SHA, not diff content, so a conflict-free rebase always re-arms it and forces a full specialist-reviewer re-run from scratch.

A `cumulative-review` marker kind closes this: its value is the sha256 of `pr-diff-against-base.sh`'s output. It is written by `marker.sh write cumulative-review` at the end of a clean step-3 pass and read back through `marker.sh status`'s existing completion-marker report, so a rebase that leaves the cumulative diff byte-identical reuses the prior clean review instead of re-running it.

The preimage is the diff bytes alone, deliberately excluding the merge-base SHA and the base-branch ref — folding either in would make the cache miss in precisely its motivating case, since a rebase onto a moved default branch always changes the merge-base.

**Named residual, not fixed here** (§34's "Named residual, not fixed here" is the precedent shape for recording this kind of gap rather than engineering it away). A byte-identical diff rebased onto a moved default branch is not strictly the same review object: a reviewer's ripple and causal-reach judgment can depend on code outside the diff, and a clean rebase surfaces textual conflicts only, not semantic ones. This residual is not closed by folding the base ref into the hash — that would defeat the cache's own motivating case, as above — so it is compensated instead: step 2's verification runs against the rebased tree on every pass and is never cached, and CI runs on every push. The cache skips reviewer judgment over bytes nobody changed, not verification of the tree.

**A second named residual: the write is a zero-evidence self-attestation.** `write cumulative-review`'s only precondition is that the diff hashes to something; nothing binds the write to proof that a review actually ran. This is not a new privilege boundary: every existing marker kind is already self-attested, with no hook correlating a write to completed review work. It does, however, convert step 3 from prose-"unskippable" into a silently skippable step. The accepted mitigation is the same one already in place for the base-move residual above: step 2's verification and CI still run unconditionally on every pass, cache hit or not. §50 closes the narrower defect this residual was later found to also cover — a write that stamped a diff it never reviewed at all — without closing this residual itself, which stays open at parity with the other four marker kinds.

### Sources

- `.claude/plans/rfr-cumulative-diff-cache.md` — full assumption ledger, mechanism list, and out-of-scope residuals.

## 45. Round-3-triggered `plan-architect MODE=consult` gate, with a condition-shaped CLAUDE.md carve-out (2026-09-02)

A PR entering a third `/code-review` round is a non-converging review loop,
not a quality gradient: mean `commit_count` across a 213-PR sample moves
2.33 → 2.54 → 2.86 through rounds 0–2, then jumps to 6.31 at round 3+
(`docs/case-studies/opus-frontload-review-rounds.md`'s "Follow-up
diagnostic" section). Firing after round 1 would catch roughly half of all
PRs to address a 14% tail, so the trigger point is entry to round 3
specifically, and — per this repo's own convention that memory and skill
instructions cannot fulfill an automatic-trigger request — it is
hook-enforced rather than left to `code-review/SKILL.md` prose.

The gate is a `PreToolUse` hook, `require-architect-consult.sh`, paired
with a `PostToolUse` recorder, `log-reviewer-round.sh`, both registered on
the `Agent|Task` matcher and self-filtering internally to a
reviewer-persona spawn. Splitting gate from recorder, rather than writing
state on the gate's own allow path, preserves a distinction the gate alone
cannot: the recorder writes the architect-consult latch only on a
`plan-architect` dispatch that actually returns, so a consult aborted
mid-flight leaves the gate armed rather than silently satisfied.

The round counter is a per-branch state file of distinct `(HEAD sha,
staged-diff sha256)` pairs, capped at two entries, keyed at
`<config-dir>/.reviewer-round-state.d/<repo-hash>.<branch-hash>`. The pair,
not either half alone, is the unit that survives a whole parallel reviewer
fan-out (five reviewers dispatched against one state collapse to one
entry) while still telling a committed fix (HEAD moves, staged diff
empties) apart from a staged one. Four lighter counters were rejected:

- Branch commit count — correlated with round count but not equal to it, so
  a 3-commit branch at round 1 would fire on a converging PR.
- The existing `review-narrative-ledger` — session-keyed, disable-able,
  swept at 30 days, and already known not to reliably keep pace with
  `/code-review` runs.
- Counting `agent-reviews/` findings files — opt-in via the
  `findings_path:` convention, so the count tracks reviewer output opt-in,
  not rounds.
- Appending round rows to `code-review-markers/` — that file's content is
  itself the commit gate's authorization, and this repo's CLAUDE.md forbids
  hand-writing marker state outright.

The release condition is a content-free, presence-only latch at
`<config-dir>/.architect-consult-latch.d/<repo-hash>.<branch-hash>`,
deliberately unlike every completion marker elsewhere in this repo, whose
content is a hash of the reviewed state so the gate re-arms the moment
that state changes. Re-arming on state change is exactly wrong here:
rounds 3+ exist because the diff keeps changing, so a content-addressed
latch would re-deny on the first post-consult fix. The gate fires once per
branch and does not re-arm at round 5, 7, or 9 — a re-arming latch would
be a second defensive layer closing a gap the first created, the
compounding-layers shape this repo's CLAUDE.md already names as a
wrong-foundation tell. A branch still active 30 days after its one consult
can fire again once, because both state directories sweep on the same
30-day interval as every other self-sweeping state directory in this repo.

The gate ships on by default for every stow consumer, cleared only by a
presence-only disable sentinel, `<config-dir>/.round-consult-gate-disabled`
— the `handoff-nudge` precedent (on-by-default with a disable escape)
rather than the opt-in precedent `track-permission-prompts` /
`nudge-error-mode-analysis` use. The gate interrupts a cost-and-quality
problem general to every consumer's review pipeline, not a personal
preference or a scan with a false-positive cost, so defaulting it on is
closer to `handoff-nudge`'s shape than either opt-in nudge's.

`claude/.claude/CLAUDE.md`'s Model & Effort Routing Opus bullet's flat
"never dispatch it this way on your own initiative" prohibition needed a
carve-out for this gate's own prescribed dispatch, or the gate's deny
message would prescribe an action CLAUDE.md itself forbids. The carve-out
added to that clause names a precondition — a hook deny, or the
dispatching session's own recognition that the branch is entering a third
`/code-review` round — not an event.
An event-shaped carve-out ("unless a hook deny prescribes it") would
reward the worse outcome: a session that self-triggers early and correctly
would be technically in violation, while one that blundered into the deny
would be compliant, and disabling the gate would silently revoke the
option along with the enforcement, since no deny would ever exist to
prescribe it. Conditioning on the precondition itself keeps the standing
option alive independent of whether the mechanical gate is on.

That standing option is not a second enforcement layer for the same
trigger, and composes with the gate and recorder at no code cost: the
recorder writes the latch on any `plan-architect` dispatch whose prompt's
first line is not `MODE=plan-sections`, whether or not a deny preceded it.
A session that recognizes round 3 on its own and dispatches the consult
proactively writes the latch through the same door the gate's own
prescribed dispatch would use, and the fan-out that follows is never
denied. The two signals — the dispatching session's own visibility into
real review rounds, and the hook's admitted proxy count — disagree
harmlessly in both directions: over-counting costs one extra Opus dispatch
on a converging PR, and under-counting or inaction still gets caught by
the hook itself, denying at its own precise, mechanical count.

The latch's actual meaning is "an architect consult ran on this branch
recently," not "the gate's prescribed consult ran" — an explicit-user-ask
consult at round 1 (§37's original case) writes the same latch and spends
the branch's one firing, after which rounds 3, 4, 5 get nothing further
from either path. That is intended coverage under the latch's stated
meaning, not a hole to patch: patching it would require content in the
latch, which would reintroduce the re-denial-on-first-fix failure this
design exists to avoid.

§37's text is untouched. Its claim was narrower than this entry's trigger:
that a subagent cannot verify why it was dispatched, so the
explicit-user-ask gate is a documented-convention constraint on the
dispatching session's judgment rather than a mechanically detectable one.
That claim survives intact — this gate mechanizes a different
precondition, round count, observable only through tool-call payloads the
hook can see, and leaves the user-ask question exactly where §37 left it.

### Sources

- `docs/case-studies/opus-frontload-review-rounds.md` lines 158–269 ("Follow-up diagnostic") — the round-bucket commit-count table and the round-3 trigger-point recommendation.
- `docs/cost-levers-considered.md`'s 4th register row — prior grounding for the cost lever this gate closes.
- `docs/design-decisions.md` §37 — the ad hoc `plan-architect MODE=consult` mode and the explicit-user-ask gate this entry leaves untouched.
- `.claude/plans/round3-review-consult-trigger.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.
- `claude/.claude/hooks/require-architect-consult.sh`, `claude/.claude/hooks/log-reviewer-round.sh` — the gate and recorder.
- `claude/.claude/hooks/_lib.sh` — `_lib_reviewer_round_state_key`/`_value`, `_lib_is_reviewer_persona`, `_lib_round_consult_gate_disabled`, `_lib_append_line_locked`.

## 46. Rule-file review stays a section inside `ai-instruction-and-memory-files/SKILL.md` rather than its own skill (2026-09-03)

`skill-review` and `agent-review` are each their own skill file, split per artifact type. `.claude/rules/*.md` review instead lives as §4 inside `ai-instruction-and-memory-files/SKILL.md`, split per instruction-prose domain — a different organizing axis, and the placement question recurred across review rounds.

The tradeoff is artifact-mechanics residue. `skill-review` and `agent-review` each carry a substantial body of artifact-specific machinery with no CLAUDE.md analog: `skill-review`'s name/directory matching, description-as-always-loaded-budget, `disable-model-invocation`, `allowed-tools`, and trigger-eval harness; `agent-review`'s restrictive-`tools`-vs-additive-`allowed-tools` distinction and the `maxTurns`-as-string silent-ignore trap. A rule file's equivalent — the `paths:` glob contract — lives instead in `rule-authoring-conventions.md`, itself a rule file, because it's needed at authoring time and not only at review time. That extraction leaves the review-time residue at two checklist items: glob-set/body-applicability match, and stowed-rule portability.

A fourth stowed skill for two items would cost a permanent slot in the aggregate skill-listing description budget every stow consumer's session pays (`TestTotalListingBudgetUnderSonnet::test_total_within_listing_budget`), plus a fourth verbatim copy of the behavior test, compression-diff audit, and duplication rules that `docs/skills.md` forbids factoring into a shared partial. `skill-review` itself avoids a comparable cost via project-scoped plugin packaging (it lives under `plugins/skill-management/`, keeping it out of the always-loaded catalog for downstream stow consumers) — a route not open to a rules reviewer, since rule files stow globally and every consumer authors them.

Four of the five checks `ai-instruction-and-memory-files/SKILL.md` §4 lists carry over from §1-3 unchanged (behavior test, compression-diff audit, duplicate-vs-reference, anti-duplication, Step 1 item 2 placement); only the 200-line CLAUDE.md cap and auto-memory mechanics drop out. Running the same exercise against `skill-review` inverts the list — its distinctive checks are all about a natural-language trigger evaluated by a model, which a rule file does not have. Rule files sit on the CLAUDE.md/AGENTS.md/memory side of the line (human-authored instruction prose injected into context, dominant failure mode is context cost and adherence), not the SKILL.md/agent side (dispatchable procedures, dominant failure mode is trigger matching).

**What would flip this.** Extract when the rule-file-specific checklist reaches three or four items, or when `ai-instruction-and-memory-files/SKILL.md`'s own growth pushes it toward being unwieldy as an operational checklist — not before.

## 47. A second `claudeMdExcludes` entry suppresses the nested-discovery duplicate of each stow-source rule file in a linked worktree (2026-09-03)

In a linked worktree the user-scope symlink target (`~/.claude/rules`, pointing at the main checkout) and the nested project path (`<worktree>/claude/.claude/rules/`) are different absolute paths, so each matching stow-source rule file loads twice. In the main checkout those two paths are identical, so only one copy loads there.

The main-checkout copy wins, so a worktree session editing a stow-source rule file won't see its own edit take effect there.

The `.claude/worktrees/` segment in the new pattern is what keeps it off the user-scope copy's link target. A pattern without that segment suppresses both copies, because a `claudeMdExcludes` pattern matching a rules file's link target excludes it the same as matching its own path.

The tail is `/**`, the shape the Claude Code documentation uses for a rules directory. Observation confirmed it matches a direct child. It also covers a rule file later placed in a subdirectory of `claude/.claude/rules/`, a case no rule file occupies today and which was therefore not observed directly.

The entry reaches only a worktree created under `.claude/worktrees/`. A linked worktree created elsewhere still loads both copies.

The observable symptom, if this entry ever stops matching, is a worktree session carrying each matching stow-source rule twice. Nothing reports that at runtime. A future contributor who notices a larger-than-expected context in a worktree session should re-verify the entry rather than assume it is inert. Reverting the entry restores the double-load and has no other effect.

See §39 for the sibling `CLAUDE.md` exclusion and for why the stow-source `claude/.claude/settings.json` carries no `claudeMdExcludes` entry.

### Sources

- [Claude Code memory docs](https://code.claude.com/docs/en/memory), "Exclude specific CLAUDE.md files" — the either-path symlink rule and absolute-path matching.
- [Claude Code large-codebases docs](https://code.claude.com/docs/en/large-codebases.md) — the rules-file reach of `claudeMdExcludes`.
- `.claude/plans/dedupe-nested-rules-dir.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.

## 48. `review-trace` gets a fifth event kind so a prescribed `plan-architect` consult stops being invisible to `skill-fidelity-reviewer` (2026-09-04)

A `plan-architect MODE=consult` dispatch prescribed by §37, §42, or §45 produced no artifact either `review-trace` or `skill-fidelity-reviewer` could see. The gap surfaced on a live branch: confirming a prescribed consult had actually run required hand-tracing the raw session transcript, because neither tool surfaced it — and the reviewer's silence was the worse half, since it reported nothing rather than reporting the question as one it could not answer. `_review_trace_session_events`'s existing `elif block_name in ("Agent", "Task")` arm now also classifies a non-reviewer `plan-architect` dispatch whose prompt's first line is not the literal `MODE=plan-sections` (including an absent or empty first line — fail-safe toward "consult") as a new `architect-consult` event kind, and `skill-fidelity-reviewer` gets a mandatory-emission step over it.

**Re-reading §25's "never assistant prose" bar as structural, not literal.** §25 barred the timeline from carrying the deviating session's rationale, keyed on the evidence surface being tool-call metadata rather than free text. Testing a dispatch prompt's first line against a fixed protocol literal is not that: `MODE=consult` is a two-valued mode selector `plan-architect.md` itself requires the caller to emit, not model-chosen rationale, and the design keeps the property structural by construction — the prompt string is never stored on the event dict, only the classification result plus the branch/model/timestamp metadata every other event kind already carries. A test pins this at both layers: the event dict's key set, and `review-trace`'s printed output.

**The verdict-set ceiling this event caps `skill-fidelity-reviewer` at.** The new check can assert only two things: consult observed (`[DISCLOSED]`, with timestamps, never claiming which prescription site it satisfied) or no consult observed (folded into the existing *Dismissed as undecidable* protocol, reasoned "absence of a row is not evidence of absence"). It can never legitimately emit `[SILENT-SKIP]`, for three independent reasons:

- `review-trace` never loads subagent records, so a consult dispatched from inside a subagent is structurally invisible.
- §37's explicit-user-ask gate and §42's `Fix route:` narration are both already-established non-evidence (the latter is exactly the assistant-prose class §25 rejected).
- §45's own deny-latch correlation, while technically decidable, was rejected as a second detective layer duplicating what the `PreToolUse` gate already enforces preventively — the compounding-defensive-layers shape this repo's CLAUDE.md names as a wrong-foundation tell.

The event also signals dispatch *initiation*, not completion — it fires on an `Agent`/`Task` tool_use block's presence alone, with no dependence on a `tool_result` arriving, unlike `log-reviewer-round.sh`'s `PostToolUse` latch — so an aborted consult yields a `[DISCLOSED]` row while §45's gate stays armed. Every surface stating the event's meaning says "initiated," not "completed" or "ran."

**Cross-runtime duplication, accepted under a named exception.** The classification is identical to `log-reviewer-round.sh:_maybe_write_consult_latch`'s existing logic for §45's latch, re-expressed in Python rather than read from a shared file — accepted under CLAUDE.md's small-duplicated-value exception to single-source-of-truth, guarded by a behavioral-parity test rather than a source-literal one: a byte-equality assertion across the three literal sites would pass even with the Python comparison inverted (`!=` for `==`), so the guard instead runs one parametrized `(first_line, expect_consult)` table against each runtime's own test harness — bash via `TestLogReviewerRoundConsultLatch`, Python via `TestReviewTrace`. `plan-architect.md` itself gets only a substring tripwire (`"## MODE=plan-sections" in body`), since it is LLM-consumed prose with no runtime to assert behavior against.

**Deliberately out of scope.** Adding `plan-architect` to `_REVIEWER_EXACT_NAMES` — that frozenset also gates `reviewer-yield`'s verdict tables and `subagent-mix`'s per-`agentType` rows, and can't distinguish `MODE=plan-sections` from `MODE=consult`, so every `/plan-it` Step 5 dispatch would register; guarded by a negative-membership test rather than a blanket closed-set pin. Reading `include_subagents=True` into `review-trace` to close the subagent-invisibility gap — it would change scan cost and output volume for every consumer and reopen the §25 blindness question with real sidechain prose in scope, a separate decision with its own plan. Any change to `require-architect-consult.sh`, `log-reviewer-round.sh`'s behavior, or the latch's content-free shape — §45 chose that shape deliberately, and this design never reads the latch.

### Sources

- `.claude/plans/review-trace-consult-classification.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.
- `docs/design-decisions.md` §25, §37, §42, §45 — the blindness property, the ad hoc consult mode, the Fix-route dispatch, and the round-3 gate/recorder this entry reads but does not edit.
- `claude/.claude/scripts/transcript-analysis.py` — `_is_architect_consult_dispatch`, the `elif block_name in ("Agent", "Task")` arm in `_review_trace_session_events`, and `cmd_review_trace`'s `architect-consults=<N>` header/printer arm.
- `claude/.claude/hooks/log-reviewer-round.sh:118-135` (`_maybe_write_consult_latch`) — the bash classification the Python re-expresses.
- `claude/.claude/agents/plan-architect.md:28-31,33,42` — the `MODE=plan-sections`/`MODE=consult` protocol literal and its two occurrences.
- `claude/.claude/agents/skill-fidelity-reviewer.md` — the architect-consult check, its Output-format record, and the corrected Input-contract clauses.

## 49. `ScheduleWakeup` denied by bare tool name in `permissions.deny`, reversing §41 (2026-09-04)

Ship `"ScheduleWakeup"` as a second bare tool-name entry in `claude/.claude/settings.json`'s `permissions.deny`, alongside `EnterPlanMode`. A bare tool name removes the tool from context entirely rather than rejecting a call after the fact: "A bare tool name like `Bash` removes the tool from Claude's context entirely, so Claude never sees it" (`code.claude.com/docs/en/permissions`). §41 evaluated a `PreToolUse` gate, a `PostToolUse` advisory nudge, and a `CLAUDE.md` line, and rejected all three — correctly, on their own terms. What its survey never reached was the settings layer, where this primitive already existed and needs no `/loop`-awareness to work: it prevents the call from being formed at all, rather than reasoning about which calls are legitimate.

**Which of §41's three Revisit conditions are met.** Of the three conditions §41 named for reopening itself, only the third is met. `ScheduleWakeup` still accepts out-of-`/loop` calls, and no `PreToolUse` field exposes harness-computed active-mode state. Upstream issues #80350, #88260, and #88205 are all still open, and no changelog entry through Claude Code 2.1.258 addresses this. The first condition is therefore not met. No instance surfaced of a misfire failing to self-correct cleanly, either: every observed case cancelled or disclosed honestly with no fabricated or predicted result, so the second condition is not met. The third condition, a materially higher well-formed-call rate at large context sizes, is met: the well-formed non-`/loop` call turned out to be the majority sub-mode rather than the minority §41 assumed, concentrated at context sizes where a wasted re-invocation is a real cost. The supporting counts and context medians are not reproduced here — the corpus mixes private-project and public transcripts, and any count, ratio, median, or duration would inherit the private half's composition.

**The mechanism-exhaustion lesson.** §41 was correct about every mechanism it evaluated and wrong about the set being complete. A mechanism-exhaustion claim needs a sweep of every configuration layer that could plausibly reach the problem — settings keys, CLI flags, hooks, prose — not only the layer the problem first surfaced in.

**Why nothing addresses the `noop` misconception directly.** The failure is driven by the model reading `noop` as a "check back later, do nothing" mode the tool does not have; errored calls carried a uniform `{delaySeconds, noop, reason}` shape with `prompt` and `stop` never present. No second mechanism — no `CLAUDE.md` line, no skill clause, no advisory nudge — addresses this directly: once the tool is absent from context, the misconception has no expression surface, since the model cannot construct a call to a tool it cannot see. A mechanism that closed a gap the deny already closes would be the same compounding-defensive-layer shape §41 and §42 both name as a wrong-foundation tell.

**Blast radius.** The entry ships in the stow-source settings file, so it reaches every stow consumer, not only this repo. Self-paced `/loop` (no fixed interval) is degraded, but not silently. Across four manual test sessions run against a live `permissions.deny` before this change shipped, `/loop`'s dynamic mode detected the missing tool via `ToolSearch` on its first turn rather than attempting a call that could fail. Those four sessions split into two outcomes:

- Three of four disclosed the gap honestly and fell back to `Monitor`-only (event-driven, no periodic heartbeat) with no fabricated workaround.
- One of four substituted `CronCreate` as a fallback heartbeat (roughly 20-minute cadence) without asking first — a real observed instance of the substitute-mechanism risk this entry's Revisit list names below, not merely a hypothetical one.

Fixed-interval `/loop <interval> <prompt>` is unaffected, confirmed directly: a `CronCreate` job scheduled and fired successfully under the deny. Deny rules across settings scopes union rather than override, so the opt-out is editing the tracked file. Three override attempts were each tried in turn against a live user-scope deny, and `ScheduleWakeup` stayed absent in all three:

- A project-scope `permissions.allow` entry.
- A project-local `permissions.allow` entry.
- Plain omission.

These three attempts cover the project-scope and project-local settings-file layers only. The command-line flag layer, which outranks both in Claude Code's five-level precedence order, was not tried. Other stow consumers' `/loop` usage is not observable from this corpus; the blast-radius argument rests on the deny being reversible in one line plus this entry's documented migration note, not on a claim about other consumers' behavior.

**Revisit** if any of:

- The model substitutes a worse wait mechanism for the removed tool *in production* — repeated `ListAgents`/`TaskOutput` polling, a `Bash sleep`, or a recurring `CronCreate` call.
- A genuine `/loop` need arises in this repo's own pipeline — none exists today; `ci-watch.sh`, the strongest candidate, is deliberately built to avoid polling via `Bash run_in_background`.
- A stow consumer reports a silently-truncated self-paced `/loop`.
- `ScheduleWakeup` gains its own out-of-`/loop` validation upstream, which moots this entry.
- Claude Code changes bare-tool-name-deny semantics or cross-scope permission-union precedence in a future release. Either would silently invalidate this entry's claims while `test_schedulewakeup_stays_denied_in_settings` keeps passing, since that test pins only the declared config value, not the harness behavior behind it.

**Accepted residual risk.** Three things are accepted rather than closed:

- **The `CronCreate` substitution channel is unguarded.** The pre-implementation gate observed the model substituting `CronCreate` as a fallback heartbeat in 1 of 4 runs, without asking first. That is the exact autonomous-reinvocation risk this deny exists to close, now sitting on an adjacent tool. `CronCreate` carries none of: a guardrail, an ask-tier check, a cap. The first Revisit condition above already covers a production recurrence of this pattern.
- **No committed artifact backs the manual pre-implementation gate.** Its results are prose only, recorded in this plan and in this entry. No transcript, log, or fixture is committed alongside. This is the same limitation the `EnterPlanMode` sibling accepts, not a new gap unique to this entry. See `.claude/plans/plan-mode-workflow-discipline.md`'s Accepted residual risk section.
- **No Revisit condition previously covered the underlying mechanism drifting upstream.** The first four Revisit conditions above all track the *model's* behavior. None tracked Claude Code itself changing bare-tool-name-deny semantics or cross-scope union precedence — a change that would silently invalidate this entry's claims while the declared-config test kept passing. The fifth Revisit condition above closes that gap.

**Reversal is not friction-free.** Removing this line re-triggers `ask-review-permissions.sh`'s `ask` decision and a `review-permissions` re-review, exactly like adding it did. "One line to revert" describes the diff size, not the process cost.

### Sources

- [Claude Code permissions docs](https://code.claude.com/docs/en/permissions) — bare-tool-name deny semantics.
- `docs/design-decisions.md` §41 — the decision this entry reverses.
- `docs/design-decisions.md` §17 — the `skillOverrides.loop` decision this entry leaves standing.
- `docs/design-decisions.md` §43 — the settings-scope union precedent this entry's opt-out argument relies on.
- `claude/.claude/settings.json` — the `permissions.deny` entry itself.
- `.claude/plans/prevent-non-loop-schedulewakeup-calls.md` — full assumption ledger, per-mechanism reasoning, and the live-verification test-session results this entry summarizes.

## 50. `write cumulative-review` stops recomputing its own artifact: the marker's value is the recorded review subject, not a write-time diff (2026-09-04)

The defect, stated structurally: `marker.sh write cumulative-review` and `marker.sh status` both called the identical helper, `_lib_cumulative_diff_hash`, so the value the write stored was, by construction, whatever the reader would later recompute. A mistimed write — one issued after a fix commit whose only review was a narrow staged-diff `/code-review` — was therefore structurally incapable of producing a marker that failed to match; it stamped whatever diff existed at the moment it ran, regardless of what was reviewed.

The fix moves the write's value source, not its shape. `pr-diff-against-base.sh` gains a `--record` flag, added to `ready-for-review` step 3's existing diff command, that captures the diff at step-3 entry into `<config-dir>/cumulative-review-subject-markers/<repo-hash>.<session-id>`. `write cumulative-review` reads and consumes that recorded subject instead of calling `pr-diff-against-base.sh` itself — present and non-empty, hash it; absent or empty, refuse and write nothing. `status` is unchanged: it still recomputes the diff live via `_lib_cumulative_diff_hash`. A write issued after a fix commit now stamps the pre-fix subject, `status` recomputes the post-fix diff, the two disagree, and the marker reads `historical` — the mistimed write becomes self-defeating instead of harmful.

A corpus sweep across every root in `~/.claude/transcript-config-dirs` found seven executed `write cumulative-review` invocations across six sessions and five branches, all failing the documented step-3 standard, with zero correct executions on record. The mechanism dissolves all seven retrospectively. Five had a fix commit land between a real cumulative pass and the write, so the recorded subject is stale and the marker now reads `historical`. One ran no `pr-diff-against-base.sh` at all in the session, so no subject exists and the write now refuses. One ran only `git diff --stat` with no review in place of the pass, landing in one of the same two outcomes depending on whether an earlier record stood. This is a retrospective result, not a forward guarantee — `--record` sits inside step 3's own routine diff command, so a session that re-enters step 3 after a fix commit and proceeds straight to the write reaches record-then-immediately-write with no review in between through the ordinary flow, not only through deliberate hand-forging.

**What this does not establish: that a review happened.** Record-then-write with no review in between still produces a live marker, and no in-session mechanism distinguishes it from a genuine pass. That is the same self-attestation exposure §44's second residual already names for every marker kind, and parity with the other four is the accepted bar. The control is `ready-for-review/SKILL.md`'s `SCOPE_RULE:ready-for-review-cumulative-unnarrowed` block, not new machinery. It now states the write-authority fact directly: the cache marker is written only from a clean pass of step 3's own cumulative `/code-review`, never from a fix commit's staged-diff pass.

Two disclosures. First, a pre-existing unearned marker outside the roots this session's corpus sweep covered stays `live` forever: only the write arm changed, `status` is untouched, and an unswept unearned marker still matches whatever the reader recomputes. Second, this session evicted the six markers its sweep confirmed unearned, mapped to five branches, as part of landing this change — recorded here as history, not as an implementation step this design depends on.

### Sources

- `.claude/plans/cumulative-review-marker-proof.md` — full assumption ledger, mechanism list, and out-of-scope residuals.

## 51. The no-op-dispatch guard is a CLAUDE.md rule, not a hook (2026-09-03)

A session that spawns a subagent whose only instruction is to wait, occupy the turn, or report back immediately pays a full agent's context for an empty return. The prohibition first shipped as prose in `subagent-delegation/SKILL.md` Step 1 on 2026-09-02. It recurred on 2026-09-03 in a session that never loaded the skill body — only the one-line trigger description from the available-skills listing. That is the whole failure: a skill-body rule reaches a session only when the skill is in context, and this impulse arrives at moments that do not look like delegation decisions. Two occurrences are confirmed, both in this repo's own history:

- `memory-content-migration`, 2026-08-30
- `discovery-audit-remediation-plan`, 2026-09-03

That is a floor from keyword search and one direct admission, not an exhaustive count: transcripts do not record which skill bodies were in a session's context, so this repo's own history cannot be counted as exhaustive. The rule now also sits in `claude/.claude/CLAUDE.md`'s Agent Briefing section, which every session loads on every turn.

**§1 prefers a hook, and no hook is available here.** A `PreToolUse` hook on the `Agent` tool receives `tool_name`, `tool_input` (`subagent_type`, `prompt`, `description`, `model`), `session_id`, `cwd`, and `transcript_path`. None of those carries a non-content signal for "this dispatch does no work." The one non-content fact reachable — that another `Agent` dispatch is already in flight, via transcript parsing — is not the defect: `plan-it` Step 3 encourages parallel fan-out, so gating on it would deny legitimate dispatches. A hook would therefore have to match on prompt text. Three hooks match the `Agent` tool today, and none supplies that predicate:

- `require-routing-read.sh` (`PreToolUse`) gates on a file-read marker.
- `require-architect-consult.sh` (`PreToolUse`) reads `tool_input.subagent_type` only to scope itself to reviewer-persona dispatches, then gates on a round-count state unrelated to no-op detection.
- `log-reviewer-round.sh` (`PostToolUse`, never denies) reads both `tool_input.subagent_type` and `tool_input.prompt` but only to record round state for the same gate.

Reusing any of the three would still mean adding new prompt-content matching for no-op intent specifically — the same cost as a fresh hook. §1's preference holds where a mechanical predicate exists. Here there is none, so the real choice is between two advisory surfaces, and the always-loaded one wins.

**Partial duplication, accepted under a named exception.** CLAUDE.md carries the prohibition, the shapes it covers, and what to do instead. `subagent-delegation` keeps the return-value framing and the delegation-cost reasoning. Both surfaces must stand alone, because a session that loaded the skill and a session that did not are different readers — and the second reader is why this entry exists. That is CLAUDE.md §Engineering Judgment's "instructional prose that must stand alone" exception, not an unexamined copy. The overlapping sentences are worded identically on both surfaces so that `git grep` exposes drift.

**No mechanical enforcer.** The trigger is dispatch intent, not a file-content fact. A test asserting the bullet's literal string would be tautological and would break on any rewording.

**Revisit** if the behavior recurs after this rule ships. The next lever is a `PreToolUse` hook on `Agent`, accepting prompt-content matching and its false-positive cost. At that point two advisory surfaces will have failed, which is itself the evidence that no advisory surface reaches this impulse.

### Sources

- `claude/.claude/CLAUDE.md` §Agent Briefing — the rule's text.
- `claude/.claude/skills/subagent-delegation/SKILL.md` Step 1 — the same rule with its delegation-cost reasoning.
- `claude/.claude/hooks/require-routing-read.sh` — an `Agent`-matching `PreToolUse` hook gating on a file-read marker rather than on tool-input content.
- `claude/.claude/hooks/require-architect-consult.sh` — an `Agent`/`Task`-matching `PreToolUse` hook reading `tool_input.subagent_type` to scope itself, then gating on round-count state.
- `claude/.claude/hooks/log-reviewer-round.sh` — an `Agent`/`Task`-matching `PostToolUse` hook reading both `tool_input.subagent_type` and `tool_input.prompt` to record round state, never denies.
- `.claude/plans/guard-placeholder-wait-forks.md` — the first mechanism choice, including the ledger rows that rejected both a hook and CLAUDE.md at that time.
- `.claude/plans/guard-no-op-dispatch-rule.md` — this change's assumption ledger.

## 52. Reviewer-agent dispatches are scoped to input provenance, not persona, and carry no `isolation: "worktree"` carve-out (2026-09-04)

A session dispatched several reviewer subagents with `isolation: "worktree"` plus a `findings_path` write. Every findings-file write was denied by worktree enforcement, and each agent fell back to inline output. The dispatching session was not misbehaving: repo prose in several places, including the deny message a blocked session reads, licensed the combination.

The corrected rule (`claude/.claude/CLAUDE.md`'s "Agent Briefing" section) is scoped to **input provenance**, not to "reviewer" as a persona: `isolation: "worktree"` is passed only when an agent's input is already committed and its output is disposable, and never when the agent's input is the dispatching session's working tree or its output has to land there. A persona-scoped rule ("never combine `isolation` with `findings_path`") would still license the failure mode's silent half: an isolated reviewer dispatched *without* `findings_path` reads a committed-ref checkout that never contained the changes under review, is denied nothing, and can return a clean verdict on work it never read. That false-clean outcome is why the rule is scoped to provenance rather than to the `findings_path` collision alone. It is deliberately not restated in the CLAUDE.md bullet itself: the always-loaded rule already prohibits the dispatch outright, so spelling out the consequence there would add motivation rather than a new instruction, at a recurring context cost every stow consumer's session pays.

The rule carries no exception clause. The exception is exactly the judgment call that failed on every dispatch that faced it; a carve-out would reinstate it.

The fix is prose plus one structural test — `test_no_agent_declares_worktree_isolation`, asserting no agent file under `claude/.claude/agents/` or `plugins/*/agents/*.md` declares `isolation:` in frontmatter — not a hook gate. A `PreToolUse` gate on `Agent(isolation:worktree)` is deferred, not rejected outright: whether `tool_input.isolation` appears in a hook's raw stdin JSON is unverified, and a gate that could only match on the permission-rule `if` and read `subagent_type` would need a persona allowlist — the same judgment call the corrected rule removes. `permissions.deny` is not a substitute: a permission rule is scoped to one parameter, so it cannot be narrowed by subagent type, and a blanket deny would over-block the two dispatch shapes the corrected rule still permits (parallel exploration of already-committed code, throwaway spikes). Layering a runtime gate on prose that currently commands the mistake is also the compounding-defensive-layers tell — fix the foundation first, then measure whether the corrected prose holds.

Two related defects are filed as separate issues rather than fixed here. First, the lock-reason parser that backs worktree-collision detection does not recognize the harness's own ephemeral-worktree lock-reason shape, so it denies with a liveness-blind message for any legitimately-isolated agent that writes. Fixing that does not make the isolated-reviewer combination work: a relative `findings_path` still resolves inside the ephemeral worktree rather than the parent's tree. Second, the `PreToolUse` gate above, blocked on the stdin-JSON question.

### Sources

- `.claude/plans/reviewer-agent-worktree-isolation-prose.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.

## 53. Full-suite pytest drift traced to two stale pre-`select-tests.py` plans; no handoff-validation mechanism added (2026-09-04)

A transcript-corpus audit of this repository's own project directories, described in full in the plan, confirmed the full-suite-run pattern was recurring, not a one-off, after the rule shipped (`7200d727`/`bf215df9`, 2026-08-25).

A systemic fix was drafted and rejected. The design added a `check-handoff.py` soft check plus a `handoff/SKILL.md` §3 clause requiring a handoff's named verification command be re-derived from the project's current documentation rather than copied from its plan. `/plan-review` found a foundation-level defect: CLAUDE.md's second full-suite exception ("a plan's Verification step... genuinely calls for a whole-repo claim") is a condition on intent, not a machine-readable predicate. Any mechanism checking "does this command match the default" would therefore silently override a legitimate whole-repo Verification claim on some future plan — trading the observed defect for a different one. This forecloses the whole family of "just check the command against the default" fixes, not only the one drafted here.

The two stale plan files are not fixed by this decision. That correction was handed directly to the two branches' own sessions rather than made from this unrelated branch, since editing another branch's plan file re-arms `require-plan-review.sh` there until re-reviewed. The full ledger, evidence, and rejected design live in `.claude/plans/select-tests-handoff-drift.md`, kept on this branch as the durable record rather than restated here.

**Revisit** if any of:

- A future inherited full-suite run traces to a plan file that postdates `select-tests.py` (2026-08-25) — that would mean the drift is live and recurring rather than two aging plans working through their own backlog, and would reopen the systemic-fix question.
- Either of the two branches above merges without its Verification section corrected — the stale command becomes a merged, harder-to-notice instruction rather than a live one two sessions were told about directly.
- `review-pipeline-orchestrator-subagent.md`'s proposed (not yet built) Bash-mutation-restriction hook ships with its command allowlist still omitting `select-tests.py` — that would convert this from a stale instruction into an enforced one.

### Sources

- `.claude/plans/select-tests-handoff-drift.md` — full assumption ledger, per-session provenance classification, and the rejected mechanism's design and `/plan-review` rejection.
- `CLAUDE.md`'s Commands section — the `select-tests.py` rule and its two named exceptions.
- `.claude/plans/prevent-runaway-subagent-cost.md` and `.claude/plans/review-pipeline-orchestrator-subagent.md` (each on its own branch) — the two stale Verification sections this entry traces the drift to.
- `.claude/plans/select-tests-fallback-audit.md` (merged, GH-765) — the prior related audit that fixed five other propagation surfaces without flagging `handoff/SKILL.md`'s silence as one of them.

## 54. `guard-settings-session-keys.sh`'s default-branch diff accepts up to 4 extra `_lib_capped` git calls on its staged path (2026-09-04)

`guard-settings-session-keys.sh` diffs staged `claude/.claude/settings.json` against the repo's resolved default branch, so an unresolvable default branch must deny rather than silently comparing nothing. This is the one fail-closed exception to the hook's otherwise fail-open posture. Resolving that branch via the shared `_lib_resolve_default_branch` (`_lib.sh`) adds up to 4 more `_lib_capped` git calls on the settings.json-staged path:
- one for the symbolic-ref probe
- up to three more for the `main`/`master`/`develop` candidate loop

Each call is capped at `_lib_capped`'s default 5s, so the theoretical worst case — every call independently timing out — is ~45s. That is well past the hooks' <100ms/fire budget. On a machine lacking both `timeout(1)` and `gtimeout(1)`, each of these calls instead runs uncapped and can hang indefinitely rather than for a bounded 5s. This diff also roughly doubles the sequential-call count on the settings-staged path (4 pre-existing calls to up to 8), so that uncapped-timeout exposure compounds against more calls, not just a longer one.

This is an accepted tradeoff scoped to the narrow settings.json-staged path, not a general latency regression. The hook's earlier staged-file check exits before reaching this diff on every commit that doesn't touch `claude/.claude/settings.json`, which is what keeps the cost scoped to that one path. The new `.cwd`-extraction `jq` call this diff also adds, however, runs unconditionally on every gated `git commit`, not only the settings.json-staged path. `require-ready-for-review.sh`'s default-branch bypass check pays a comparable call-count shape. It resolves the default branch through its own separately-duplicated logic, not this shared helper.
