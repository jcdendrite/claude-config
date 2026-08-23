# Surface `.claude/rules/` as a design option in plan-it

## Context

`plan-it` never leads agents to propose a `.claude/rules/` path-scoped rule
as a design mechanism — only skills — whether run inside claude-config or in
a repo that merely has claude-config's skills stowed. The user wants to know
why and how to fix it. Two research passes over this session confirmed the
mechanism: `plan-it/SKILL.md` and `plan-review/SKILL.md` never mention
`.claude/rules/` at all, and the repo's one existing "which mechanism"
precedent (CLAUDE.md's "Should this be a hook?" bullet) itself omits rules
from its own comparison set — so there is no artifact in the chain an
authoring or reviewing agent consults that ever surfaces rules as a peer of
skill/CLAUDE.md/memory. The fix is to add that missing option where the
decision already lives (`ai-instruction-and-memory-files`, whose frontmatter
already claims to own it) and point both `plan-it` and `plan-review` at it.

**Step 4 (clarifying questions) note:** the one open judgment call — single-
file patch to `plan-it` only vs. this three-file fix — was not put back to
the user as a question. The user's own phrasing ("how can we get agents to
consider using rule in designs") asks for the *how*, delegating that design
call per Step 4's own escape valve; this is a stated abbreviation, not a
silent one.

## Approach

Extend the one skill whose job is already "decide which surface an
instruction belongs on" (`ai-instruction-and-memory-files`) to actually
offer `.claude/rules/` as a candidate, then have `plan-it`'s authoring step
and `plan-review`'s review step both route to it — mirroring how `plan-it`
already defers to `code-review`/`test-conventions`/`verify-sources` instead
of inlining their logic, and how `plan-review` already routes CLAUDE.md/
memory content to this same skill.

**External-pattern grounding** (in-repo primary source, verified this
session): `.claude/rules/` is Claude Code's own native mechanism, already
documented in this repo —

> "Contributor-workflow instructions that only apply to specific file types
> live in [`.claude/rules/`](.claude/rules/) instead, Claude Code's native
> path-scoped rules directory — loaded automatically only when a matching
> file is opened, rather than every session." — `README.md:62`

> "**Path-scoped rules:** `.claude/rules/` holds contributor-workflow
> instructions that only need to load when a specific file type is open,
> instead of every session... They load automatically via `paths`
> frontmatter matching." — repo-root `CLAUDE.md` ("Working in this repo")

No repo artifact anywhere contrasts this against "skill" (explicit
invocation via the Skill tool) or "CLAUDE.md/memory" (always/every-session)
in one place — confirmed by grep for `"rule vs skill"`, `"skill vs rule"`,
`"instead of a rule"`, `"when to use a rule"` (zero hits) across the whole
repo. Two historical, already-merged plans (`subagent-dispatch-
authorization.md`, `comment-verbosity-root-cause.md`) did reason correctly
about rule-vs-skill-vs-hook as part of the over-powered-primitive ledger
check — proof the check *can* surface rules when an author happens to think
of them — but that reasoning was never distilled back into `plan-it` or
`plan-review`'s own instructions, so it isn't systematically triggered.

### Assumption ledger

- **Root:** Agents running `plan-it` (in claude-config or any repo where its
  skills are stowed) never propose `.claude/rules/` as a design mechanism,
  because no artifact in the routing chain they consult — `plan-it`,
  `plan-review`, or the dedicated placement-decision skill
  (`ai-instruction-and-memory-files`) — names it as a candidate, even though
  the last of those already claims that exact responsibility in its own
  frontmatter.
- **Given:** Claude Code's harness auto-loads `.claude/rules/*.md` via
  `paths` frontmatter matching, independent of any skill's own logic — this
  plan cannot change *how* the harness triggers a rule, only where the prose
  that points an agent at the mechanism lives. [harness/platform boundary]

- **Row1 — Add `.claude/rules/*.md` as a candidate destination inside
  `ai-instruction-and-memory-files`'s three existing routing artifacts**
  (Step 1 checklist, "Quick decision flow" table, "Where does a given rule
  belong?" table). anchors: root. This skill is the sole existing routing
  authority for "which surface should an instruction live on" — the fix is
  additive (one more option in an existing decision), not a new mechanism.
  `[verified: claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md]`
  — read this session; its Step 1, §4 table, and §5 table each enumerate
  {CLAUDE.md/AGENTS.md, that skill's own SKILL.md, auto-memory} and never
  `.claude/rules/`, despite the frontmatter description already promising
  "deciding which surface a rule belongs in."
- **Row2 — Add one sentence to `plan-it` Step 5** pointing plan authors at
  `ai-instruction-and-memory-files` whenever the mechanism choice is which
  surface a new instruction/behavior should live on. anchors: row1. Closes
  the reported symptom at the exact step (Architecture design) where the
  blind spot was observed, without duplicating Row1's routing logic.
  `[verified: claude/.claude/skills/plan-it/SKILL.md:47]` — current text is
  "Consult `code-review`, `test-conventions`, and `verify-sources` if their
  domains are implicated," with no rules mention anywhere in the file
  (confirmed by full-file review this session).
- **Row3 — Extend `plan-review`'s "Domain: Claude Code config" trigger
  globs and routing sentence** to include rule files
  (`.claude/rules/*.md`, `claude/.claude/rules/*.md`), routed to the same
  `ai-instruction-and-memory-files` call already used for CLAUDE.md/memory
  content. anchors: row1. The review-side counterpart to Row2 — without it,
  a plan that *does* propose a new rule file never gets its placement
  checked, the same gap that exists today would just move one level down.
  `[verified: claude/.claude/skills/plan-review/SKILL.md:216-220]` — the
  trigger sentence lists `.claude/skills/**/SKILL.md`, agent files,
  `CLAUDE.md`/`AGENTS.md`/memory, hooks, and `permissions.allow`, but never
  `.claude/rules/*.md`.

### Alternatives considered and set aside

1. **Add a "Should this be a rule?" bullet to repo-root `CLAUDE.md`**,
   mirroring "Should this be a hook?" (`CLAUDE.md:67-73`) — rejected: that
   file is claude-config's own contributor-workflow doc, not stowed to
   `~/.claude/CLAUDE.md`, so it would never reach "other repos that leverage
   claude-config" — the user's stated symptom — and it would duplicate
   `ai-instruction-and-memory-files`'s routing logic in a second place.
2. **Create a new dedicated routing skill** — rejected: `ai-instruction-
   and-memory-files` already exists, is already dispatched by name (not by
   autonomous trigger-matching), and its frontmatter already claims this
   exact responsibility; a second skill would be a competing routing
   authority.
3. **Add a hook** that injects rules-awareness when `plan-it`/`plan-review`
   run — rejected: CLAUDE.md's own "Should this be a hook?" criteria don't
   match here (no recurring automated action to perform on a tool event);
   this is a missing option in existing prose, not an automation gap.
4. **Duplicate the full rule-vs-CLAUDE.md-vs-skill-vs-memory table directly
   into `plan-it`'s own Step 5 body** — rejected: `plan-it` already defers
   domain-specific decisions to specialist skills (`code-review`,
   `test-conventions`, `verify-sources`) rather than inlining their logic;
   inlining `ai-instruction-and-memory-files`'s table here would violate
   CLAUDE.md's single-source-of-truth instruction and drift over time.

**Explicitly declined, not just deferred:** enriching `ai-instruction-and-
memory-files`'s frontmatter `description` to name `.claude/rules/` more
prominently for autonomous-trigger discovery. Row2's explicit named
invocation from `plan-it` doesn't depend on trigger-matching, so this isn't
required for the fix to work, and frontmatter-description changes carry
broader trigger-matching implications outside this plan's scope (Axis 4).

## Critical files

- `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` — Row1.
  Insert a new check between existing Step 1 items #1 and #2: "Does this
  rule apply automatically whenever a specific file type or path pattern is
  open, regardless of which skill or workflow is running? If yes → a
  path-scoped rule (`.claude/rules/*.md`, or the stowed `claude/.claude/
  rules/*.md` for a rule that should apply in every repo) — auto-loads via
  `paths` frontmatter matching, with no explicit invocation and no
  every-session CLAUDE.md cost." Renumber the old #2/#3 to #3/#4 and update
  "If step 1 or 2 produces a destination" → "If step 1, 2, or 3...". Add one
  row to the "Quick decision flow" table (§4, placed after the existing
  skill-specific row) and one row to the "Where does a given rule belong?"
  table (§5, placed after the existing skill-specific row), each stating
  the same file-type-scoped → `.claude/rules/*.md` mapping. Do not touch
  the Step 0 `HOOK_TEST_FIXTURE` block — a test suite re-reads it verbatim.
  **Reuse:** existing table/list structure and voice; no new section.
- `claude/.claude/skills/plan-it/SKILL.md` — Row2. In Step 5, change
  "Consult `code-review`, `test-conventions`, and `verify-sources` if their
  domains are implicated." to also name `ai-instruction-and-memory-files`
  for surface-choice decisions, deferring to its routing table rather than
  restating it — one sentence, no new section.
- `claude/.claude/skills/plan-review/SKILL.md` — Row3. In the "Domain:
  Claude Code config" section: add `.claude/rules/*.md` and
  `claude/.claude/rules/*.md` to the trigger-glob sentence, and extend "For
  CLAUDE.md, AGENTS.md, or memory-file content, invoke `ai-instruction-and-
  memory-files`..." to also cover rule-file content, routed to the same
  skill call.

## Verification

Documentation-only change (no code, no runtime behavior) — verify by:
1. Re-read all three edited sections end-to-end for internal consistency
   (Step 1 numbering, table alignment, sentence flow) after editing.
2. `../../../.venv/bin/pytest ../../../claude/.claude/` from this worktree —
   confirms the `HOOK_TEST_FIXTURE` block in `ai-instruction-and-memory-
   files/SKILL.md` (untouched) still matches what `require-memory-skill.sh`
   expects, and that nothing else in the hook/skill test suite regresses.
3. `/code-review` before commit — since all three edited files are
   `SKILL.md`, `.claude/rules/review-pipeline-dispatch.md` requires
   dispatching `skill-review` on the diff; let that run and address any
   findings (length cap, voice, duplication).
4. After drafting the Row1 text, run `wc -l claude/.claude/skills/ai-
   instruction-and-memory-files/SKILL.md` — it starts at 193/200 lines
   against the default `check-skill-length.sh` gate (no per-skill override,
   unlike `plan-review`'s 500-line cap), so confirm the drafted addition
   still lands under 200 before committing.

## Out of scope

- Repo-root `CLAUDE.md`'s "Should this be a hook?" bullet — left as-is (see
  Alternative 1 above); it's a distinct, already-working precedent for a
  different mechanism class (automation) and isn't stowed to other repos.
- Any change to how `.claude/rules/*.md` itself loads or is authored — this
  plan only changes where agents are told the option exists.
