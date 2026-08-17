# Trim and reorganize both CLAUDE.md files

> **Status: superseded in part. Revise before implementing.** This plan
> passed three `/plan-review` rounds, then six decisions changed it. Do not
> implement it as written.
>
> 1. **Depends on PR `close-hook-coverage-gaps`, which must merge first.**
>    That PR closes three hook gaps and carries a per-duty table naming
>    exactly which prose each fix licenses deleting. Read that table rather
>    than this plan's trim list; this plan predates it and over-credits what
>    the hooks cover.
> 2. **The `code-change-discipline.md` rule is dropped.** Its globs cannot
>    define "code" without silently missing languages, and engineering
>    judgment is not file-type-bound the way the existing `**/*.sh` and
>    `**/*.sql` rules are. Official guidance is that sometimes-relevant
>    content belongs in a skill, not a `paths:` rule
>    (`code.claude.com/docs/en/memory`). Every relocation this plan routed
>    there needs a new home or stays put.
> 3. **Mechanism labels are now descriptive** (code-globbed rule,
>    skill-rule extension, settings-json rule, PR-description move,
>    denial-shape trim); the former `M1`–`M5` shorthand is gone.
> 4. **Rationale relocates rather than compresses away.** Every surviving
>    judgment bullet keeps its imperative and any non-obvious constraint in
>    `CLAUDE.md`; the explanation moves to a new
>    `docs/claude-md-references.md`, following the structure of the existing
>    `docs/rules-references.md` ("Not loaded at runtime — read when editing
>    a rule"). Nothing behavioral leaves `CLAUDE.md`.
> 5. **Apply the vendor's own test to every surviving line**: *"Would
>    removing this cause Claude to make mistakes?"* plus the ✅/❌ table at
>    `code.claude.com/docs/en/best-practices`, section "Write an effective
>    CLAUDE.md".
> 6. **Remove the "minimal does not necessarily mean short" citation** from
>    Alternatives. It is attributed to Anthropic but is not on the
>    best-practices page, which says the opposite for `CLAUDE.md` ("keep it
>    short", "ruthlessly prune"). It was carried from a secondary source and
>    never verified — the defect this plan's own Ground-every-choice rule
>    exists to prevent.
>
> Unchanged and still valid: the trigger audit, the armed-and-complete
> check, the redaction tier being undeletable, the four orphaned citations
> in Phase C, and the `test_doc_counts.py` logic change.

## Context

Cut the per-session cost of the two always-loaded instruction files —
`claude/.claude/CLAUDE.md` (stowed to `~/.claude/CLAUDE.md`, loaded in every
session in every repo on the machine) and the repo-root `CLAUDE.md` (loaded
in every `claude-config` session) — by relocating content to surfaces that
load only when the rule can actually fire, without losing any enforcement
guarantee.

Why now: `/context` reported the global file at ~8.8k tokens. The growth is
organic, not a defect — re-derive with
`git log --follow --oneline -- claude/.claude/CLAUDE.md`, which shows
incremental commits since the file's creation with no anomalous size jump,
and no duplication, truncation, or stale-cache cause.
`[unverified in this document — the command above is the re-derivation path;
the original finding came from a prior session that left no repo artifact]`

Intended outcome, stated as the metric that survives review (see Approach →
"What this actually saves"): the **unconditional** cuts remove ~1,730 chars
from the global file and ~2,960 from the root file in *every* session. The
**relocations** remove a further ~4,260 (global) and ~3,112 (root) only in
sessions that never open a matching file; in a code-editing session those
bytes re-enter context on the first matching read, so the saving there is a
one-to-two-turn deferral, not an avoidance. No rule loses coverage.

## Approach

Classify every rule by **what event fires it**, then move each to the
cheapest surface whose load trigger matches that event. Rules whose firing
event is not a file read stay always-loaded regardless of size. Rules
already guaranteed by a hook keep the clause that tells the model the shape
of the denial and lose the clause that restates the prohibition — but only
after a per-hook check that the hook is always-armed and not self-documented
as incomplete.

### What this actually saves

`docs/cost-levers-considered.md:198-199` measured that this repo's dominant
spend driver is idle-gap cache rebuilds triggered by TTL lapse during
concurrent-session switching (92.9%), not by prompt content. Rebuild
*frequency* is unrelated to byte count; only rebuild *magnitude* scales with
it. So the saving must be stated by session shape, not as one percentage:

| Session shape | Global file saving | Root file saving |
|---|---|---|
| Opens no matching glob (planning, prose, git-only, analysis) | ~5,990 chars | n/a outside this repo |
| Code-editing session | ~1,730 chars, plus a 1–2 turn deferral of ~4,260 | ~2,960 chars, plus deferral of ~3,112 |

The unconditional column excludes the install prohibition (row 19), which
fails the armed-and-complete check and therefore stays. Every figure here is
an estimate under row 14 and is reconciled at Step 25.

`claude-config`'s own sessions are almost always code-editing — the worst
case for the relocation half. The unconditional cuts are the reliable win;
the relocations pay off on other repos and in non-code sessions.

This plan does not claim a dollar figure. Deriving one requires CLAUDE.md's
share of a typical rebuild's rewritten context, which is not measured here;
`docs/cost-levers-considered.md`'s rebuild scan thresholds at ≥100k tokens,
against which this cut is a small uncharacterized slice.

### Assumption ledger

**Root problem:** two instruction files load unconditionally into every
session, but a majority of their rules can only fire under conditions far
narrower than "a session started."

**Givens:**

- The Claude Code context-loading model is vendor-defined; this plan can
  only choose among surfaces the vendor provides.
  `[verified: code.claude.com/docs/en/memory]`
- Anthropic's tokenizer is not available as a local library, so no local
  tool can reproduce `/context`'s accounting. (Supporting evidence, not the
  given itself: the repo venv holds only pytest/ruff/shellcheck.)
  `[verified: code.claude.com/docs/en/memory; pip list on .venv]`
- Post-compaction, `paths:`-scoped rules are not re-injected until Claude
  next reads a matching file. `[verified: code.claude.com/docs/en/memory]`

**Out of scope, not givens:** stripping rationale prose; the `## Commands`
bash block; the machine-level `CLAUDE.local.md`.

**Mechanisms:**

| # | Mechanism | Justification | Anchors |
|---|---|---|---|
| Code-globbed rule | New user-scope rule `claude/.claude/rules/code-change-discipline.md` | Code-authoring judgment fires when a code file is open | `anchors: root` |
| Skill-rule extension | Extend `.claude/rules/skill-and-agent-self-review.md` | Its globs already match `**/SKILL.md` | `anchors: root` |
| Settings-json rule | New project-scope rule `.claude/rules/settings-json-conventions.md` | settings.json conventions fire only when editing settings.json | `anchors: row 8` |
| PR-description move | Move one "Ground every choice" category into `pr-description/SKILL.md` | That category fires when authoring a PR body | `anchors: row 3` |
| Denial-shape trim | Trim-to-denial-shape for hook-backed prose, gated by the armed-and-complete check below | The hook guarantees the outcome; only denial-shape survives | `anchors: row 5` |

**Over-powered-primitive check on the code-globbed rule:** two lighter primitives rejected —
(1) `@path` import: loads at launch, saves zero context
`[verified: code.claude.com/docs/en/memory]`; (2) skill body behind a
CLAUDE.md pointer: converts a guaranteed load into a "model remembers"
load `[unverified — asserted from the task brief, which is not a repo
artifact and cannot be cited]`.

**Over-powered-primitive check on the settings-json rule:** It creates a file for ~574 chars
while this plan leaves a 140-char bullet in place. The stated test is
**whether an existing rule file already globs the trigger path**, not byte
count: `.claude/rules/review-pipeline-dispatch.md:1-6` globs SKILL.md/agent/
plugin paths, so neither settings.json nor hook files have a host. Its
content is 4× the overhead of its own frontmatter and heading; the 140-char
hook bullet is not, so it stays. Lighter primitives rejected: (1) extend
`review-pipeline-dispatch.md` — would require widening its globs to
`**/settings.json`, coupling two unrelated triggers in one file;
(2) leave in CLAUDE.md — the content fires only on settings.json edits, so
it is pure load in every other session.

**Assumption rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | `paths:` rules load only when Claude reads a matching file | `[verified: code.claude.com/docs/en/memory — "Path-scoped rules trigger when Claude reads files matching the pattern, not on every tool use."]` |
| 2 | User-scope `~/.claude/rules/` is supported; it is a directory symlink to the main checkout, so new files need no re-stow but are not live until merge | `[verified: docs; readlink ~/.claude/rules; require-stow-reminder.sh:104-133]` |
| 3 | SKILL.md bodies load only on invocation | `[verified: code.claude.com/docs/en/skills]` |
| 4 | The three review-section prose blocks duplicate skill `description:` text already in session context | `[verified: direct observation of this session's available-skills listing]` |
| 5 | `/code-review`, `/plan-review`, `/ready-for-review` are hook-gated on the same trigger the prose covers | `[verified: require-code-review.sh:64,100-122; require-plan-review.sh:73-76,104-131; require-ready-for-review.sh:94-117,204-224]` |
| 6 | `EnterPlanMode` is hard-denied for every stow user; the deny does not cover writing `permissions.defaultMode: "plan"` | `[verified: claude/.claude/settings.json:65]` |
| 7 | `test_doc_counts.py:143-182` requires exactly one "Ground every choice" anchor and compares the numeral to the nested-bullet count **within one file** | `[verified: read directly]` |
| 8 | No existing `.claude/rules/` file globs `settings.json` | `[verified: read both files' paths:]` |
| 9 | `code-review/SKILL.md:292` cites "§Working Style Axis 2" by name — Scope discipline stays put, so it is unaffected | `[verified: read directly]` |
| 10 | `~/.claude/docs` does not exist (global CLAUDE.md's `docs/*.md` citations dangle for stow users); `~/.claude/agents/`, `~/.claude/hooks/tests`, `~/.claude/skills/` do exist | `[verified: ls at each path]` |
| 11 | The agent roster stows everywhere, so Model & Effort Routing is genuinely global — only its citations were parochial | `[verified: row 10]` |
| 12 | Trim depth = Moderate; repo-root CLAUDE.md is in scope | `[engineer-verified]` |
| 13 | Spend is rebuild-dominated, so the saving is stated by session shape and no dollar figure is claimed | `[verified: docs/cost-levers-considered.md:194-214,198-199]` |
| 14 | All char figures are proportional estimates from a section-by-section scan, not tokenizer output; Step 25 reconciles them against actual `wc -c` | `[unverified — estimates; downstream percentages inherit the flag]` |
| 15 | `enforce-marker-script-shape.sh:17-38` self-documents that a Bash redirect never mentioning `marker.sh` bypasses its write-authority check | `[verified: read directly]` |
| 16 | `ask-new-dependency-disclosure.sh:36` fires only on `package.json`; other manifests are uncovered | `[verified: read directly]` |
| 17 | `claude/.claude/private-projects.md` is gitignored/untracked and `install.sh:612-620` only prints a TIP, so the blocklist tier is unarmed for every fresh stow user | `[verified: .gitignore:49; git ls-files; install.sh:612-620]` |
| 18 | `deny-pii-in-commits.sh` gates `git commit` on staged-diff credential *shapes* (GitHub token, AWS key ID, PEM), always-armed, but is not a general secret scanner | `[verified: deny-pii-in-commits.sh:8]` |
| 19 | `deny-network-installs.sh` self-documents six accepted gaps, including path-prefixed manager invocation and bare `npx`/`pipx` | `[verified: deny-network-installs.sh:8-22]` |
| 20 | `require-ready-for-review.sh` self-documents `--dry-run` and default-branch-push bypasses | `[verified: require-ready-for-review.sh:46-59]` |
| 21 | The redaction heading is cited by 11 `emit_deny` sites in `deny-private-project-refs.sh`, not nine | `[verified: grep -c, this session]` |

### The armed-and-complete check (gates every denial-shape trim)

Before trimming any hook-backed bullet, verify the hook is **(a)
unconditional/always-armed** and **(b) not self-documented as incomplete**
for the exact scenario the prose covers. Four bullets failed this check and
are therefore **not trimmed**:

- **Marker forging** — fails (b). Row 15: the hook admits a redirect bypass.
  The flat imperative "Never write `~/.claude/*-markers/*` by hand" and "A
  general 'ship it' instruction is not authorization to forge a marker" stay
  verbatim.
- **Package naming** — fails (b). Row 16: only `package.json` is covered.
  The naming-duty imperative stays intact for all manifests.
- **Installing software autonomously** — fails (b). Row 19: the hook
  self-documents six gaps, including a path-prefixed manager invocation
  (`/opt/homebrew/bin/npm install x`), which is a realistic shape on a
  Homebrew machine. The flat prohibition stays; only the `!`-escape
  alternative and the already-declared-dependency carve-out were ever
  candidates for compression, and both are kept as denial-shape.
- **Redaction, "caught when populated" tier** — fails (a). Row 17: unarmed
  by default for every fresh stow user. Category list and the "if in doubt,
  strip it" default stay in root CLAUDE.md.

Two bullets are trimmed only partially:

- **Secret-file reads** keeps both halves of the branch verbatim. Note the
  asymmetry precisely: `deny-credential-bash-reads.sh:71,79` *does* state
  the non-exposing→`!`-escape half, so only the
  exposing→separate-terminal half is Read-channel-only
  (`deny-credential-file-reads.sh:78`). Keeping both is the conservative
  call, but do not restate the rationale as "no Bash-channel hook states
  it" — that is false for half the branch.
- **Pre-Handoff Review** trims against a hook with its own documented gaps
  (row 20: `--dry-run` and default-branch-push bypasses). Those gaps change
  *which push shapes* trigger review, not whether arbitrary code can be
  fetched, so the one-line pointer is adequate — but the reduction must
  keep the skill name so the model can still reach it unprompted.

### The trigger audit

Every relocation must answer: **what event fires this rule?** If the answer
is not "Claude reads a file matching a glob," a `paths:` rule cannot carry
it. Rules that **failed** and stay always-loaded:

| Rule | Firing event | Why a `paths:` rule fails |
|---|---|---|
| "Locate before a whole-file read" | About to read a file | Must be loaded *before* the read that would trigger it |
| "Prove your change caused a failing check" | A check fails | No file read guaranteed; a fresh "run the tests" session never triggers it |
| Scope discipline, Axes 1–4 | Any edit, including prose | Axis 3 governs docs and changelogs; a code glob never fires there. Also the target of a live citation (row 9) |
| Root: "Plugin skills use `plugin:skill` names" | Invoking a skill by name | Reads no file under `plugins/` |
| Root: "Plans affect all stow users" | Writing a new plan file | The plan file is created, not read |
| Root: "Project-scoped plugins" | Deciding where to create a skill | Placement decision precedes the file existing |

### Alternatives set aside

**Aggressive tier** (defer Agent Briefing and Model & Effort Routing):
rejected — both fire on subagent dispatch, which reads no file.

**Rationale-stripping:** rejected. Rationale generalizes a rule to cases the
literal text misses; relocation preserves it, stripping destroys it.

**An adherence eval harness before trimming:** rejected as out of scope. The
unconditional cuts rest on single-source-of-truth
(`docs/design-decisions.md:158` §13), not a measured adherence curve; and a
task-resolution metric cannot observe the failure mode that matters (a rule
silently not firing).

**Keeping all six "Ground every choice" categories together in CLAUDE.md:**
rejected — five are code-triggered and the sixth is not, so keeping them
together means either paying for five always or mis-triggering the sixth.
The split costs the aggregate-count ground truth (Step 9).

## Implementation steps

Land as **separate commits within one PR, never separately merged PRs** — a
user pulling between two merges would see rules deleted before their new
home exists — a coverage gap for anyone who pulls mid-series.

### Phase A — global file, unconditional cuts

1. `claude/.claude/CLAUDE.md` — Code Review section (L52-54): delete the
   commit-triggered sentence (hook-backed, row 5); keep the clause
   distinguishing terminal-act-is-commit from terminal-act-is-presentation,
   which no hook covers (no commit fires on a proposal).
2. Same file — Plan Review (L56-58) and Pre-Handoff Review (L60-62): reduce
   each to one line naming the skill and the gate. Rationale: row 5.
   `require-code-review.sh` and `require-plan-review.sh` document no gaps and
   pass the armed-and-complete check cleanly; `require-ready-for-review.sh`
   has documented bypasses (row 20) that affect which push shapes trigger
   review, not code-fetch reachability — the one-liner must still name the
   skill so the model can reach it unprompted.
3. Same file — plan-mode bullet (L69-78): delete all but the
   `permissions.defaultMode: "plan"` clause, which the `EnterPlanMode` deny
   does not cover (row 6).
4. Same file — `sudo` (L98): delete; `settings.json:35-36` denies it.
5. Same file — installs (L99): **keep the flat prohibition** — it fails the
   armed-and-complete check on row 19 (six self-documented hook gaps,
   including path-prefixed invocation). Keep the `!`-escape alternative and
   the already-declared-dependency carve-out as denial-shape. Compress only
   redundant phrasing within the sentence, if anything. **Do not touch
   L100-108** (naming duty — fails the same check, row 16).
6. Same file — secrets/binary (L109): keep the large-binary-asset half (no
   hook); trim the secrets half to a pointer, noting row 18's limit
   (three credential shapes, not a general scanner).
7. Same file — secret reads (L110): keep both halves of the exposing vs.
   non-exposing branch and the do-not-route-around-the-denial clause
   verbatim; delete the prohibition restatement. Rationale per the
   armed-and-complete section: only the exposing→separate-terminal half is
   Read-channel-only; `deny-credential-bash-reads.sh:71,79` already states
   the non-exposing→`!`-escape half.
8. Same file — marker bullet (L112): keep the flat imperative, the "ship
   it" clause, the denial-names-the-skill clause, and the `code-writer`
   cannot-self-resolve clause; delete only the content-hash mechanism
   explanation, which `docs/hooks.md` carries.

### Phase B — global file, relocations

9. Create `claude/.claude/rules/code-change-discipline.md`. Frontmatter:
   bare `paths:` key only, matching the four existing rule files. Globs:
   `**/*.py`, `**/*.ts`, `**/*.tsx`, `**/*.js`, `**/*.jsx`, `**/*.go`,
   `**/*.rs`, `**/*.rb`, `**/*.java`, `**/*.kt`, `**/*.swift`, `**/*.c`,
   `**/*.h`, `**/*.cpp`, `**/*.cs`, `**/*.php`, `**/package.json`,
   `**/requirements*.txt`, `**/go.mod`, `**/Cargo.toml`, `**/Gemfile`.
   **Globs stay generic — never claude-config-relative** (this installs to
   every stow user). Move in, verbatim: "Audit structural siblings",
   "A locally-valid patch can signal a wrong foundation", "Extract functions
   when you need to explain what a fragment does", "Use descriptive variable
   and function names", and "Ground every choice" categories 1–5.
   **Change the lead-in numeral from "Six categories" to "Five categories."**
10. `claude/.claude/skills/pr-description/SKILL.md` — add "Ground every
    choice" category 6 (quantitative/causal claims in ticket, PR, and
    handoff prose) adjacent to the existing claim-verification step at
    `:152-156`, which verifies post-hoc what this states as an authoring
    duty. State it without a category number — no cross-file count.
11. Delete the relocated bullets from `claude/.claude/CLAUDE.md`.
12. Same file — Model & Effort Routing: rewrite three citations to their
    stowed forms (`~/.claude/agents/Explore.md`,
    `~/.claude/hooks/tests/test_agent_roster.py`,
    `~/.claude/skills/review-permissions/SKILL.md`) and delete the two
    `docs/auto-mode.md` / `docs/design-decisions.md` parentheticals, which
    resolve for no stow user (row 10).

### Phase C — citation repair (required: four live citations point at content this plan moves)

13. `claude/.claude/skills/code-review/REFERENCES.md:19-22` — repoint four
    "§Engineering Judgment — Ground every choice (…)" rows to
    `rules/code-change-discipline.md`. Leave `:18` (Single source of truth)
    alone — that content stays.
14. Same file `:61` — repoint "§Engineering Judgment 'Audit structural
    siblings'" to the new rule file.
15. `claude/.claude/skills/plan-review/REFERENCES.md:23` — repoint "A
    locally-valid patch can signal a wrong foundation". Leave `:18` and
    `:24` alone — those targets stay.
16. `claude/.claude/agents/code-writer.md:56-61` — the generic
    "§Engineering Judgment" reference at `:56` and `:91` still resolves
    (the section survives), but the restated Ground-every-choice list at
    `:56-61` must name the new rule file instead.

### Phase D — root file

17. `CLAUDE.md` — extend `.claude/rules/skill-and-agent-self-review.md` with
    "No shared partials across skills", "`REFERENCES.md` is the edit-time
    co-located reference", "Global skill bodies stay platform-agnostic", and
    the "abstract first" worked example; delete them from root CLAUDE.md.
    Existing `paths:` already match — no glob change.
18. Create `.claude/rules/settings-json-conventions.md` (`paths:`
    `**/settings.json`, `**/settings.local.json`) holding "Plugin config:
    `enabledPlugins`" and "Disabling a plugin: `false` vs. removing"; delete
    from root CLAUDE.md.
19. Root CLAUDE.md — reduce to one-line pointers: the ShellCheck-flags and
    pytest-xdist sentences (verbatim at `README.md:483-490`), "Two CLAUDE.md
    files" and "Path-scoped rules" (`README.md:62,239-240`), the
    worktree-enforcement mechanics (`README.md:486` + both worktree hooks'
    deny messages), the "Review pipeline" section, and "AI agents: don't
    merge your own PRs" — **keeping the open-ended-verbs clause**, which
    `block-gh-pr-merge.sh:68` does not restate.
20. Root CLAUDE.md redaction — trim §"Always caught by hook" and
    §"Enforcement" toward `docs/private-project-redaction.md`, keeping the
    `PROJ-<digits>` allowlist shapes. **Do not trim §"Caught by hook when
    populated"** (row 17 — no hook arms this tier) or the three subsections with no hook
    and no doc behind them. **The heading "Redact private-project-identifying
    content" must survive verbatim** — 11 `emit_deny` sites in
    `deny-private-project-refs.sh` cite it by exact string (row 21).

### Phase E — tests, docs, accounting

21. `claude/.claude/hooks/tests/test_doc_counts.py` — this is a **logic
    change, not a path-constant edit** — the split spans two files, so a path-constant edit alone leaves the count wrong. Repoint
    `_GLOBAL_CLAUDE_MD` (`:144`) to the new rule file; update the label and
    description strings (`:295,:304`) to name it; and rewrite the docstring
    (`:148-168`), whose "compares one part of CLAUDE.md against another part
    of the same file" premise now describes the rule file. The registry fact
    becomes the rule file's local count of five; the aggregate six-category
    claim is dropped, because no single-path function can see the category
    that moved to `pr-description/SKILL.md`. Rename the constant from
    `_GLOBAL_CLAUDE_MD` to `_CODE_CHANGE_DISCIPLINE_RULE` — post-repoint the
    old name states something false about its own target. Leave
    `_GROUND_EVERY_CHOICE_BULLET` (`:143`) alone: the bullet text moves
    verbatim, so the anchor-count and terminator-scan invariants
    (`:169-182`) hold unchanged.
22. Add a rule-frontmatter unit test. Parse each `claude/.claude/rules/*.md`
    and `.claude/rules/*.md` front matter into a dict and assert `paths:` is
    present, non-empty, and a list. **Reuse `parse_frontmatter()`**
    (`validate_skill_structure.py:54-68`, already cross-imported by
    `test_agent_roster.py:297-326`) — a true parsed-object assertion, not a
    substring check for `"paths:"` in the raw text. PyYAML is already a
    declared dev dependency, so no new package is involved.

    For the user-scope files (`claude/.claude/rules/*.md`, which install to
    every stow user), additionally assert each glob is repo-agnostic, with
    this concrete predicate: **the glob's first path segment must be a
    wildcard token (`*` or `**`), not a literal directory name.** That
    admits every existing global glob (`**/*.sql`, `**/*.sh`) and rejects
    the repo-relative shape used legitimately by the project-scope files
    (`claude/.claude/skills/**/SKILL.md`). Apply the assertion only to the
    user-scope directory; project-scope globs are expected to be literal.
23. `README.md` — update `:238` (global CLAUDE.md contents), `:62` and
    `:239-240` (rules mechanism) to list both new rule files; add a note
    that `paths:`-scoped rules can stop applying mid-session after
    compaction until the next matching-file read (per `staff-product-engineer.md`s finding code B14; prior art for
    this class of note at `README.md:69,453`).
24. `docs/cost-levers-considered.md` — add a section for this lever with the
    verdict and the session-shape-split reason, per that file's convention.
25. Reconcile: run `wc -c` on both files and correct every estimate in this
    plan that diverges materially (row 14).

## Verification

1. **Compression-diff audit** (required by `ai-instruction-and-memory-files`):
   removed text | surviving line | behavior-preserving Y/N, for every cut.
   Any N restores the instruction. A Y cites the specific surviving line.
2. **Trigger audit re-check** — per relocated rule, state the firing event
   and the glob covering it.
3. **Armed-and-complete re-check** — for each denial-shape trim, restate that the hook
   is always-armed and not self-documented as incomplete.
4. `../../../.venv/bin/pytest claude/.claude/` — must pass.
5. `../../../.venv/bin/ruff check claude/.claude/`.
6. **Stale cross-reference grep**, including the two hardcoded heading
   strings below:
   `git grep -n "Ground every choice"`, `"Engineering Judgment"`,
   `"Axis 2"`, `"No shared partials"`, `"REFERENCES.md"`, `"plugin:skill"`,
   `"Redact private-project-identifying content"`,
   `"AI agents: don't merge your own PRs"`. Every hit outside moved content
   must still resolve; `.claude/plans/*.md` hits are Axis-3 preserved
   records and are left alone.
7. **Live-load check — post-merge only.** Both `~/.claude/CLAUDE.md` (per-file
   symlink) and `~/.claude/rules` (directory symlink) resolve to the **main**
   checkout, so nothing on this branch is live pre-merge. After merge and
   `git pull`: open a file matching each new rule's globs in a fresh session
   and confirm the rule loads; confirm a session opening no matching file
   does not. A pre-merge substitute is available if wanted — point a scratch
   `CLAUDE_CONFIG_DIR` at the worktree — but it is not required.
8. **Byte accounting** — actual before/after `wc -c` vs. this plan's
   estimates (Step 25).

## Out of scope

- the machine-level `CLAUDE.local.md` (home directory) — versioned in a separate private repo, not this one.
- Stripping rationale from any rule that stays.
- An adherence eval harness.
- Restructuring unrelated content in the extended skills and rules.
- A shared-partial mechanism; duplicate deliberately instead.
- Any change to `permissions.allow`/`deny` or to hook scripts — this plan
  relies on existing enforcement and adds none.
- The `## Commands` bash block in root CLAUDE.md.
- Computing a dollar saving (see "What this actually saves").
