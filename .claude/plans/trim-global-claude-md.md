# Trim and reorganize both CLAUDE.md files

> **Revision 2.** This plan passed three `/plan-review` rounds under its
> original framing, then six decisions superseded it (recorded in git
> history at commit `ce415922`). This revision resolves all six against the
> current (post-rebase, post-PR1-partial-merge) state of the repo. Summary
> of resolution, for anyone diffing against the prior version:
>
> 1. **PR `close-hook-coverage-gaps` dependency.** Read via its committed
>    per-duty deletion-license table
>    (`.claude/plans/close-hook-coverage-gaps.md:172-187`) rather than
>    restated here. That PR's Phases A–C (Python floor, marker-write-bypass
>    closure, path-prefixed-manager-invocation closure) are committed on its
>    branch but **not yet merged**; Phases D (manifest parsers) and E (docs)
>    are not yet started. This plan's Phase A step 3 below reads the
>    *post-Phase-ABC* hook state directly off that branch (verified this
>    session) rather than the pre-fix state the original plan cited, and
>    notes where Phase D's completion (pending) will further narrow one
>    residual.
> 2. **`code-change-discipline.md` rule dropped.** No glob safely defines
>    "code" without missing languages or over-matching prose/config, and the
>    firing event ("about to author code") isn't a file-read the way
>    `**/*.sql` or `**/*.sh` are. The five judgment bullets that rule would
>    have held (**Audit structural siblings**, **A locally-valid patch can
>    signal a wrong foundation**, **Extract functions...**, **Use
>    descriptive variable and function names**, **Ground every choice**
>    categories 1–5) now **stay in `claude/.claude/CLAUDE.md` unconditionally
>    — no relocation.** This removes that mechanism from the Mechanisms
>    table and from Phase B, and moots Phase C's citation-repair steps
>    entirely (nothing moved, so no citation needs repointing).
> 3. **Mechanism labels** — already descriptive in the plan body (verified:
>    no `M1`–`M5` shorthand exists outside this header). No body change
>    needed.
> 4. **Rationale relocation.** Checked every surviving compression in this
>    revision against the repo's existing docs before proposing a new file.
>    Every one already has a documented home: `docs/hooks.md` (marker and
>    plan-review mechanics), `docs/auto-mode.md` (plan-mode Opus-escalation
>    measurement), `docs/private-project-redaction.md` (structural
>    detectors). Creating `docs/claude-md-references.md` for this pass would
>    launch it with no unique content — the "bad abstraction built only to
>    remove it" pattern this repo's own Engineering Judgment section warns
>    against. **Deviation from the literal supersession instruction,
>    flagged for reviewer/engineer override:** this plan points each
>    compressed bullet at its existing doc home instead. If a future pass
>    finds genuinely orphaned rationale, `docs/claude-md-references.md` gets
>    created then, modeled on `docs/rules-references.md`'s structure
>    (verified this session: title, one `##` per source, one citation-plus-
>    quote-plus-drift-note bullet per claim).
> 5. **Vendor's own test applied** to every surviving line in Phase A/D
>    below: *"Would removing this cause Claude to make mistakes?"* This is
>    also how several of the original plan's proposed cuts were found
>    already moot — see the note at the top of Phase A.
> 6. **"Minimal does not necessarily mean short" citation** — already absent
>    from Alternatives (verified: not present outside this header). No body
>    change needed.

## Context

Cut the per-session cost of the two always-loaded instruction files —
`claude/.claude/CLAUDE.md` (stowed to `~/.claude/CLAUDE.md`, loaded in every
session in every repo on the machine) and the repo-root `CLAUDE.md` (loaded
in every `claude-config` session) — by relocating content to surfaces that
load only when the rule can actually fire, without losing any enforcement
guarantee.

Why now: `/context` reported the global file at ~8.8k tokens when this plan
was first authored. The growth is organic, not a defect — re-derive with
`git log --follow --oneline -- claude/.claude/CLAUDE.md`, which shows
incremental commits since the file's creation with no anomalous size jump.

Current baseline, re-derived this session (`wc -lc`, post-rebase onto
`origin/main`): the global file is **141 lines / 27,332 chars**; the root
file is **182 lines / 13,061 chars**. Both grew since the plan's original
baseline (140/26,647 and 172/12,491) via unrelated intervening commits — one
of which already applied several of this plan's originally-proposed cuts
(see Phase A's opening note).

Intended outcome, stated as the metric that survives review (see Approach →
"What this actually saves"): a smaller unconditional cut than originally
projected, because (a) item 2 above removes the largest relocation
(~5 judgment bullets no longer move), and (b) several Phase A cuts this
session found already applied by intervening commits. No rule loses
coverage.

## Approach

Classify every rule by **what event fires it**, then move each to the
cheapest surface whose load trigger matches that event. Rules whose firing
event is not a file read stay always-loaded regardless of size. Rules
already guaranteed by a hook keep the clause that tells the model the shape
of the denial and lose the clause that restates the prohibition — but only
after a per-hook check that the hook is always-armed and not self-documented
as incomplete (the **armed-and-complete check**, below).

### What this actually saves

`docs/cost-levers-considered.md:194-215` measured that this repo's dominant
spend driver is idle-gap cache rebuilds triggered by TTL lapse during
concurrent-session switching (92.9%), not by prompt content. Rebuild
*frequency* is unrelated to byte count; only rebuild *magnitude* scales with
it. So the saving is stated by session shape, not as one percentage, and as
a range rather than a single figure since Phase A's exact cut list is
finalized against real byte counts below rather than proportional estimates:

| Session shape | Global file saving | Root file saving |
|---|---|---|
| Opens no matching glob (planning, prose, git-only, analysis) | ~1,200 chars (unconditional) + ~2,400 chars (relocated, deferred not avoided) | n/a outside this repo |
| Code-editing session | ~1,200 chars only | ~2,400 chars (unconditional) + ~1,900 chars (relocated) |

These figures are grounded in Step-by-step byte counts taken directly from
the current file content (Phase A/D below cite `wc -c` per cut); Step 20
reconciles the final total.

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
- Post-compaction, `paths:`-scoped rules are not re-injected until Claude
  next reads a matching file. `[verified: code.claude.com/docs/en/memory]`

**Out of scope, not givens:** stripping rationale from any rule that stays;
the `## Commands` bash block; the machine-level `CLAUDE.local.md`; any
change to `permissions.allow`/`deny` or to hook scripts.

**Mechanisms (revised — one fewer than the original plan; see supersession
item 2):**

| # | Mechanism | Justification | Anchors |
|---|---|---|---|
| Skill-rule extension | Extend `.claude/rules/skill-and-agent-self-review.md` | Its globs already match `**/SKILL.md` | `.claude/rules/skill-and-agent-self-review.md` |
| Settings-json rule | New project-scope rule `.claude/rules/settings-json-conventions.md` | settings.json conventions fire only when editing settings.json | new file |
| PR-description move | Move one "Ground every choice" category into `pr-description/SKILL.md` | That category fires when authoring a PR body | `claude/.claude/skills/pr-description/SKILL.md:152-156` |
| Denial-shape trim | Trim-to-denial-shape for hook-backed prose, gated by the armed-and-complete check below | The hook guarantees the outcome; only denial-shape survives | per-bullet, Phase A/D |

**Over-powered-primitive check on the settings-json rule:** it creates a
file for ~1,075 chars (current size of the sibling
`skill-and-agent-self-review.md`, as a size reference) while this plan
leaves a two-bullet, ~610-char pair in place otherwise. The stated test is
**whether an existing rule file already globs the trigger path**, not byte
count: `.claude/rules/review-pipeline-dispatch.md:1-6` globs SKILL.md/agent/
plugin paths only (verified this session — no settings.json entry), so
neither settings.json nor hook files have a host. Lighter primitives
rejected: (1) extend `review-pipeline-dispatch.md` — would require widening
its globs to `**/settings.json`, coupling two unrelated triggers in one
file; (2) leave in CLAUDE.md — the content fires only on settings.json
edits, so it is pure load in every other session.

**Assumption rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | `paths:` rules load only when Claude reads a matching file | `[verified: code.claude.com/docs/en/memory — "Path-scoped rules trigger when Claude reads files matching the pattern, not on every tool use."]` |
| 2 | User-scope `~/.claude/rules/` is supported; it is a directory symlink to the main checkout, so new files need no re-stow but are not live until merge | `[verified: docs; readlink ~/.claude/rules; require-stow-reminder.sh]` |
| 3 | SKILL.md bodies load only on invocation | `[verified: code.claude.com/docs/en/skills]` |
| 4 | `/code-review`, `/plan-review`, `/ready-for-review` are hook-gated on the same trigger the prose covers, and `require-code-review.sh`/`require-plan-review.sh` document no gaps (pass armed-and-complete cleanly); `require-ready-for-review.sh` documents bypasses affecting which push shapes trigger review, not code-fetch reachability | `[verified: docs/hooks.md entries for require-code-review.sh, require-plan-review.sh, require-ready-for-review.sh; require-ready-for-review.sh:35-58 self-documents --dry-run and default-branch-push bypasses]` |
| 5 | `EnterPlanMode` is hard-denied for every stow user; the deny does not cover writing `permissions.defaultMode: "plan"` | `[verified: claude/.claude/settings.json:65]` |
| 6 | `test_doc_counts.py`'s `_count_ground_every_choice_categories` counts nested bullets under the "Ground every choice." anchor **within `claude/.claude/CLAUDE.md` only**, and compares against the numeral word parsed from that same file's lead-in sentence via the same-file `Occurrence` pattern — both sides of the check live in one file, so reducing to five categories and rewording "Six" → "Five" needs no test-code change | `[verified: test_doc_counts.py:143-182,255-307, read directly this session]` |
| 7 | No existing `.claude/rules/` file globs `settings.json` | `[verified: read both files' paths: this session]` |
| 8 | `~/.claude/docs` does not exist for stow users, so a bare `docs/*.md` citation inside `claude/.claude/CLAUDE.md` is ambiguous — but `claude/.claude/rules/github-actions-workflows.md:12` already establishes the fix: qualify as "`docs/X.md` **in the claude-config repo**," not delete | `[verified: read github-actions-workflows.md:1-13 this session; ls ~/.claude/docs → absent]` |
| 9 | `docs/hooks.md` documents the marker content-hash mechanism (line 66) and `require-plan-review.sh`'s `ExitPlanMode` gate mechanics (line 7) already, in more detail than the CLAUDE.md bullets being compressed | `[verified: grep + read docs/hooks.md this session]` |
| 10 | `docs/private-project-redaction.md` documents the six structural detectors and their non-matching illustrative shapes already (§"The six structural detectors", line 29) | `[verified: prior-session read, confirmed present this session via subagent]` |
| 11 | Trim depth = Moderate; repo-root CLAUDE.md is in scope | `[engineer-verified]` |
| 12 | Spend is rebuild-dominated, so the saving is stated by session shape and no dollar figure is claimed | `[verified: docs/cost-levers-considered.md:194-215]` |
| 13 | `enforce-marker-script-shape.sh`'s known-gaps header, read directly off the `close-hook-coverage-gaps` branch (Phase B committed there), narrows the marker-write bypass to: `>|` clobber-override, `python3 -c`/heredoc writes, `$(...)`-computed target paths, shell-function/variable indirection, `cp`/`mv`/`install -t DIR` forms, and symlink-shape evasion beyond a scan budget — the direct `>`/`>>`/`tee`/`cp`/`mv`/`install`/`dd of=`/`sed -i` bypass this plan's prior revision cited is now closed | `[verified: read enforce-marker-script-shape.sh header directly on close-hook-coverage-gaps branch, this session]` |
| 14 | `deny-network-installs.sh`'s known-gaps header, read directly off the same branch (Phase C committed there), no longer lists path-prefixed manager invocation among its 8 residuals (pip -e VCS-URL, bare npx/bunx/uvx/pipx, unrecognized value-taking flag, text-mention, curl/wget co-occurrence, timeout-flag-preceding-duration, heredoc/here-string glued to install, quoted-arg-becomes-redirect-shaped) | `[verified: read deny-network-installs.sh header directly on close-hook-coverage-gaps branch, this session]` |
| 15 | `ask-new-dependency-disclosure.sh` still fires only on `basename == "package.json"` on the `close-hook-coverage-gaps` branch — Phase D (manifest-parser extension) is not yet implemented there | `[verified: read ask-new-dependency-disclosure.sh directly on that branch, this session; cross-checked against that PR's own task list]` |
| 16 | `claude/.claude/private-projects.md` is gitignored/untracked and `install.sh:611-618` only prints a TIP, so the blocklist tier is unarmed for every fresh stow user | `[verified: .gitignore:49; git ls-files; install.sh:611-618]` |
| 17 | `deny-pii-in-commits.sh:3-7` gates `git commit` on staged-diff credential *shapes* (GitHub token, AWS key ID, PEM), always-armed, but is not a general secret scanner | `[verified: read directly]` |
| 18 | The redaction heading "Redact private-project-identifying content" is cited by 11 `emit_deny` sites in `deny-private-project-refs.sh` (lines 477, 481, 511, 515, 541, 545, 564, 568, 590, 657, 709) | `[verified: grep -n, this session]` |

### The armed-and-complete check (gates every denial-shape trim)

Before trimming any hook-backed bullet, verify the hook is **(a)
unconditional/always-armed** and **(b) not self-documented as incomplete**
for the exact scenario the prose covers — re-verified against the *current*
hook header, not a stale snapshot. Four duties fail this check and are
therefore **not trimmed at all** (the flat imperative stays verbatim):

- **Marker forging** — fails (b). Row 13: even post-PR1-Phase-B, several
  bypass shapes remain open (`python3 -c`, heredoc, `$(...)`-computed paths,
  shell-function indirection, and others). The flat imperative "Never write
  `~/.claude/*-markers/*` by hand" and "A general 'ship it' instruction is
  not authorization to forge a marker" stay verbatim; only the content-hash
  *mechanism* sentence is compressible (already documented at
  `docs/hooks.md:66`).
- **Package naming** — fails (b). Row 15: only `package.json` is covered on
  the dependency PR's branch today; that branch's own Phase D (pending, not
  yet implemented) will extend coverage to other manifests before it merges,
  but even then `deny-network-installs.sh` (the gate layer, distinct from
  the disclosure/ask layer) keeps its own 8 residuals regardless. The
  naming-duty imperative stays intact for all manifests either way.
- **Installing software autonomously** — fails (b). Row 14: 8 residuals
  remain documented in `deny-network-installs.sh`'s own header (bare
  `npx`/`bunx`/`uvx`/`pipx`, `pip install -e <VCS-URL>`, and six others). The
  flat prohibition stays; only the `!`-escape alternative and the
  already-declared-dependency carve-out were ever candidates for
  compression, and both are kept as denial-shape (see Phase A step 4).
- **Redaction, "caught when populated" tier** — fails (a). Row 16: unarmed
  by default for every fresh stow user (`install.sh` only prints a TIP).
  Category list and the "if in doubt, strip it" default stay in root
  CLAUDE.md verbatim.

Two duties are trimmed only partially:

- **Secret-file reads** (root CLAUDE.md Safety section) — re-audited this
  session against the current text: it is already minimal (no restated
  prohibition to cut beyond what each clause uniquely states — the
  Read-tool prohibition, the `!`-escape non-exemption, the ask-for-a-
  separate-terminal instruction, and the credential-path Bash-denial
  asymmetry are each load-bearing and distinct). **No further cut proposed**
  — see Phase A's opening note on why several originally-planned cuts are
  now moot.
- **Pre-Handoff Review** — the current text is already a bare pointer ("run
  `/ready-for-review`. If the review finds issues, fix them first, then
  push or hand off.") with no explanatory hook-mechanism sentence to trim.
  **No change proposed.**

### The trigger audit

Every relocation must answer: **what event fires this rule?** If the answer
is not "Claude reads a file matching a glob," a `paths:` rule cannot carry
it. Rules that **fail** and stay always-loaded (unchanged from the original
plan, still valid):

| Rule | Firing event | Why a `paths:` rule fails |
|---|---|---|
| "Locate before a whole-file read" | About to read a file | Must be loaded *before* the read that would trigger it |
| "Prove your change caused a failing check" | A check fails | No file read guaranteed; a fresh "run the tests" session never triggers it |
| Scope discipline, Axes 1–4 | Any edit, including prose | Axis 3 governs docs and changelogs; a code glob never fires there |
| Root: "Plugin skills use `plugin:skill` names" | Invoking a skill by name | Reads no file under `plugins/` |
| Root: "Plans affect all stow users" | Writing a new plan file | The plan file is created, not read |
| Root: "Project-scoped plugins" | Deciding where to create a skill | Placement decision precedes the file existing |
| **All five judgment bullets item 2 would have moved** (Audit structural siblings, locally-valid patch, extract functions, descriptive names, Ground-every-choice 1–5) | Authoring any code, in any language, in any file | No glob safely defines "code" without missing a language or over-matching non-code files (supersession item 2) |

### Alternatives set aside

**A code-globbed rule for the five judgment bullets above:** rejected —
superseded item 2's finding. No glob can define "code-authoring intent"
without either missing a language extension or catching non-code files
(config, prose) where the same judgment still applies. Per vendor guidance,
content that's only sometimes relevant belongs in a skill, not a `paths:`
rule (`code.claude.com/docs/en/memory`) — but no existing skill fires on
"about to author code" the way `/code-review` fires on "about to present
it," so there is no skill-shaped home either. These bullets stay in
`claude/.claude/CLAUDE.md`.

**A new `docs/claude-md-references.md` for cut rationale:** rejected for
this pass — supersession item 4's literal instruction, overridden by
evidence gathered this session (see the Revision-2 header, item 4). Every
rationale this plan actually cuts already has an existing documented home.

**Aggressive tier** (defer Agent Briefing and Model & Effort Routing):
rejected — both fire on subagent dispatch, which reads no file.

**Rationale-stripping:** rejected. Rationale generalizes a rule to cases the
literal text misses; pointing to the existing doc preserves it, stripping
destroys it.

**An adherence eval harness before trimming:** rejected as out of scope. The
unconditional cuts rest on single-source-of-truth
(`docs/design-decisions.md:158` §13), not a measured adherence curve; and a
task-resolution metric cannot observe the failure mode that matters (a rule
silently not firing).

## Implementation steps

Land as **separate commits within one PR, never separately merged PRs** — a
user pulling between two merges would see rules deleted before their new
home exists — a coverage gap for anyone who pulls mid-series. **This PR
cannot be opened until `close-hook-coverage-gaps` merges** (supersession
item 1) — Phase A step 3 below depends on its post-merge hook state.

### Phase A — global file (`claude/.claude/CLAUDE.md`)

This session re-audited every cut the original plan proposed against the
*current* file content and the vendor's own test ("would removing this
cause Claude to make mistakes?"). Several are now moot — the Code Review
section, the Pre-Handoff Review section, the secrets/binary line, and the
secret-file-reads line are all already minimal, with no explanatory clause
left to compress (an unrelated intervening commit during the 29-commit
rebase gap appears to have already tightened them). Only four cuts remain
grounded and proposed:

1. **Plan Review section** (currently one bullet: "...run `/plan-review`
   before presenting the plan to the user — including when calling
   `ExitPlanMode`... The `require-plan-review.sh` hook backs this
   mechanically: it denies `ExitPlanMode` while an un-reviewed plan file
   exists. If the review finds issues, address them first, then present the
   final version."). Delete the middle sentence (124 chars) describing the
   hook mechanism — already documented at `docs/hooks.md:7`. Replace with a
   4-word parenthetical: "(hook-enforced; mechanics: `docs/hooks.md` in the
   claude-config repo)". Net: ~-90 chars. Rationale: row 4/9, armed-and-
   complete passes cleanly for this hook.

2. **`sudo` bullet** (line 98, "Never run sudo commands directly." — 36
   chars). Delete entirely. `settings.json:35-36` denies both `Bash(sudo *)`
   and `Bash(sudo)` unconditionally — verified this session, still present.
   No mechanism explanation exists in the current bullet to preserve.

3. **Plan-mode bullet** ("Do not enter harness plan mode on your own
   initiative..." — 664 chars). Delete the "why" (Opus-escalation cost,
   already at `docs/auto-mode.md`'s plan-mode subsection) and the "how to
   plan instead" (`plan-it` Step 1's "Otherwise" branch, already documented
   in that skill's own body). **Keep verbatim** the governs-only-self-
   initiated-entry clause (256 chars) — the `EnterPlanMode` deny does not
   cover writing `permissions.defaultMode: "plan"` (row 5), so this is the
   one piece of guidance with no other enforcement backstop. Append: "(why
   and how-to-plan-instead: `docs/auto-mode.md`'s plan-mode subsection and
   `plan-it`'s Step 1, both in the claude-config repo)" — replace, don't
   silently drop. Net: ~-350 chars.

4. **Marker bullet** (line 112, 1,060 chars). Delete only the content-hash
   mechanism sentence (303 chars: "Gates match on a marker's **content** —
   a hash of the exact state that was reviewed..."), already documented at
   `docs/hooks.md:66` in more precise and complete form (covers the
   session-id write-key distinction and the repo-hash read-scope this
   bullet doesn't). **Keep verbatim**: the flat imperative, the "ship it"
   clause, the denial-names-the-skill clause, and the `code-writer`
   cannot-self-resolve clause — none of these are restated elsewhere.
   Append: "(mechanism: `docs/hooks.md` in the claude-config repo)". Net:
   ~-280 chars.

5. **Model & Effort Routing citations** (unrelated to the above; a genuine
   correctness fix, not a compression). Three citations, all within the
   `## Model & Effort Routing` section (current lines 82–94), are currently
   written in repo-relative form: `claude/.claude/agents/Explore.md` (line
   88), `claude/.claude/hooks/tests/test_agent_roster.py` (line 94), and
   `claude/.claude/skills/review-permissions/SKILL.md` (Safety section,
   line 116 — same fix, different section). None resolve for a stow user
   reading their own installed `~/.claude/CLAUDE.md` — `~/.claude/agents/`,
   `~/.claude/hooks/tests/`, and `~/.claude/skills/review-permissions/` all
   exist as stowed paths (verified `ls` this session), so rewrite all three
   to their `~/.claude/...` stowed forms. Separately, reword (not delete)
   the five `docs/design-decisions.md`/`docs/auto-mode.md` parentheticals at
   lines 85, 89, 91, 92, and 93 to the same "in the claude-config repo"
   qualifier established at `claude/.claude/rules/github-actions-workflows.md:12`
   — this preserves the pointer's usefulness while being honest that it's a
   repo-only resource, an improvement on the original plan's "just delete"
   approach. Net: character-neutral to slightly positive (rewording adds
   ~24 chars × 5 ≈ +120 chars) — this step is about correctness, not
   savings.

**Phase A total: ~-1,200 chars unconditional**, all four cuts backed by an
existing doc citation, none creating a new file.

### Phase B — global file, PR-description relocation

6. `claude/.claude/skills/pr-description/SKILL.md` (currently 199 lines,
   at this skill's own 200-line advisory length ceiling — cap this addition
   at 1-2 sentences folded into the existing bullet list, not a new
   standalone item) — add "Ground every choice" category 6
   (quantitative/causal claims in ticket, PR, and handoff prose) adjacent to
   the existing claim-verification step at `:152-156` (verified present
   this session). State it without a category number — no cross-file count
   is being maintained (`test_doc_counts.py`'s check is single-file-scoped
   per row 6; nothing there counts categories across files). Write it in
   this skill's own second-person "Flag and fix" bullet idiom — do not
   paste CLAUDE.md's generic-imperative phrasing verbatim. **Non-overlap
   with existing bullets, spelled out** (skill-review flagged this as
   under-justified): "Content-claim verification" (:152-155) checks whether
   claims about file/deployment content are *stale at HEAD* — a staleness
   check. "External-state claims" (:156) re-verifies ticket/external claims
   at their source — a re-verification check. The "Reviewer-action items
   Claude can answer itself" bullet routes test counts *out* of the PR body
   entirely (omit, don't cite) — the opposite resolution from category 6's
   (ground and cite in place). None of the three requires that a
   *surviving* number or causal claim name the code/config/query it came
   from — that is the actual gap category 6 closes.
7. `claude/.claude/CLAUDE.md` — delete category 6's bullet (502 chars) from
   the "Ground every choice" list; change the lead-in from "Six categories"
   to "Five categories." Categories 1–5 stay verbatim, unconditionally
   (supersession item 2 — no `code-change-discipline.md` relocation).

**Phase B total: ~-500 chars, only in sessions that never open a PR-body
context; re-enters context on the first `pr-description` invocation
(deferral, not avoidance, per the Approach section's framing).**

### Phase C — citation repair

**None required.** The original plan's Phase C existed solely to repoint
citations at a new `code-change-discipline.md` rule file. Supersession item
2 cancels that relocation, so `code-review/REFERENCES.md:19-22,61`,
`plan-review/REFERENCES.md:23`, and `code-writer.md:56-61,91` all still
correctly point at "§Engineering Judgment" content that isn't moving
(verified: all four citations confirmed accurate against current line
numbers this session — no drift, no repointing needed).

### Phase D — root file (`CLAUDE.md`)

8. Extend `.claude/rules/skill-and-agent-self-review.md` with, from root
   `CLAUDE.md`: "No shared partials across skills" (line 88), "`REFERENCES.md`
   is the edit-time co-located reference" (line 90), "Global skill bodies
   stay platform-agnostic" (line 94), and the "abstract first" worked
   example (lines 168-175, the `### When a skill is surfaced...` heading and
   its two ✅/❌ bullets) — moved verbatim, no rationale lost (straight
   relocation). **Add an internal `##` heading separating this new content
   from the file's existing self-review-discipline material** (e.g. "Skill
   and rule authoring conventions" vs. the existing self-review-workflow
   content) — the file's name describes only the workflow half, and
   `ai-instruction-and-memory-files` flagged the topic mix; a heading
   resolves it without a rename, since both topics already share the
   identical trigger glob (no behavioral difference either way). Delete
   the moved content from root `CLAUDE.md`. Existing `paths:`
   (`claude/.claude/skills/**/SKILL.md`, `.claude/skills/**/SKILL.md`,
   `plugins/**/skills/**/SKILL.md`, `claude/.claude/agents/*.md`,
   `plugins/*/agents/*.md` — verified this session) already match; no glob
   change needed.
9. Create `.claude/rules/settings-json-conventions.md` (`paths:`
   `**/settings.json`, `**/settings.local.json`) holding, from root
   `CLAUDE.md`: "Plugin config: `enabledPlugins`" (lines 75-76) and
   "Disabling a plugin: `false` vs. removing" (lines 78-84) — moved
   verbatim. Delete from root `CLAUDE.md`.
10. Root `CLAUDE.md` — reduce to one-line pointers (dedup, not rationale
    loss — each target already fully documents the content): the
    ShellCheck-flags and pytest-xdist sentences (lines 17-22, currently
    duplicated verbatim at `README.md`, now ~L490-495 — shifted from the
    original plan's L483-490 citation, re-verify exact lines at
    implementation time since a `.venv`-worktree-path note was inserted
    nearby); "Two CLAUDE.md files" (line 28) and "Path-scoped rules" (lines
    30-37) (`README.md:62,239-240` — confirmed accurate this session); the
    worktree-enforcement mechanics (lines 39-46) (**`README.md`'s actual
    Worktree-enforcement section moved to `README.md:252-314`** — the
    original plan's `README.md:486` citation is now stale/wrong, confirmed
    this session); the "Review pipeline" section (lines 98-106).
    **`ai-instruction-and-memory-files` flagged a duplication risk here:**
    the current "Path-scoped rules" sentence names its two rule files
    inline ("skill/agent self-review discipline and per-file-type
    review-pipeline dispatch") and would undercount once
    `settings-json-conventions.md` exists (step 9). Write its one-line
    replacement as a bare pointer with **no enumerated file list** — e.g.
    "see `.claude/rules/` file names and README.md's rules list for what
    each covers" — so it cannot go stale as rule files are added or
    renamed; the enumeration then lives in exactly one place (README.md,
    kept in sync by step 14 below).
11. Root `CLAUDE.md` — "AI agents: don't merge your own PRs" (lines
    108-114): compress the
    boilerplate ("CI passing is necessary but not sufficient — wait for the
    user's explicit 'merge it'...") to a pointer, since `block-gh-pr-merge.sh`
    already enforces and names this rule at the tool-call boundary
    (`docs/hooks.md`'s entry for that hook, verified this session). **Keep
    verbatim** the open-ended-verbs clause ("Open-ended verbs like 'handle'
    or 'do the swap' cover writing the change and opening the PR, not
    landing it.") — confirmed this session that `block-gh-pr-merge.sh`'s own
    behavior/deny-message does not restate this distinction anywhere.
12. Root `CLAUDE.md` redaction section — trim §"Always caught by hook"
    (lines 121-130) and §"Enforcement" (lines 177-182) toward
    `docs/private-project-redaction.md` (which already
    documents the six structural detectors and the enforcement trigger list
    in full — row 10), keeping inline: the tracker-ID regex, the OSS
    allowlist examples, and the `PROJ-<digits>`/`TICKET-<digits>` allowlist
    shapes (needed inline since the model must self-check tracker IDs
    without a doc round-trip). **Do not trim** §"Caught by hook when
    populated" (unarmed by default, row 16 — nothing backs it), §"Reviewer
    discipline only", §"Also redact structural fingerprints and
    provenance" (including the provenance paragraph — added by an
    intervening commit during the rebase gap, has no hook or doc backing
    it, same "no backstop" reasoning as the rest of this subsection), or
    §"Secrets, tokens, credentials". **The heading "Redact
    private-project-identifying content" must survive verbatim** — 11
    `emit_deny` sites in `deny-private-project-refs.sh` cite it by exact
    string (row 18).

**Phase D total: ~-2,400 chars unconditional (items 8, 9, 11 partial, 12
partial) + ~-1,900 chars relocated (items 8, 9 — reappears on first matching
SKILL.md/agent or settings.json read in this repo).**

### Phase E — tests, docs, accounting

13. `claude/.claude/hooks/tests/test_doc_counts.py` — **no code change**.
    Per assumption row 6, `_count_ground_every_choice_categories` counts
    nested bullets and parses the lead-in numeral **both within
    `claude/.claude/CLAUDE.md`**; after Phase B step 7 (five bullets, "Five
    categories" lead-in, both in the same file), the existing dynamic
    numeral-vs-bullet-count check holds unchanged. Verify this by running
    the test after Phase B lands, not by editing the test file.
14. `README.md` — update the global-CLAUDE.md-contents description and the
    rules-mechanism description (near current `:62`, `:239-240`) to mention
    `settings-json-conventions.md` alongside the existing rule files.
15. `docs/cost-levers-considered.md` — add a section for this lever with the
    verdict and the session-shape-split reason, per that file's convention
    (existing section runs `:194-215`).
16. Reconcile: run `wc -c` on both files after implementation and correct
    any estimate above that diverges materially from actual.

## Verification

1. **Compression-diff audit** (required by `ai-instruction-and-memory-files`):
   removed text | surviving reference | behavior-preserving Y/N, for every
   cut. Any N restores the instruction. A Y cites the specific doc now
   carrying the rationale (`docs/hooks.md`, `docs/auto-mode.md`,
   `docs/private-project-redaction.md`, or the skill/rule file the content
   moved to verbatim).
2. **Trigger audit re-check** — per relocated rule (Phase D items 8, 9),
   state the firing event and the glob covering it.
3. **Armed-and-complete re-check** — for each denial-shape trim (Phase A
   items 1, 4; Phase D item 11), re-read the cited hook's current header
   and confirm it still passes (a) and (b) as of implementation time, since
   `close-hook-coverage-gaps` may have advanced further by then.
4. `../../../.venv/bin/pytest claude/.claude/` — must pass, including
   `test_doc_counts.py` with no source change (step 13).
5. `../../../.venv/bin/ruff check claude/.claude/`.
6. **Stale cross-reference grep**: `git grep -n "Ground every choice"`,
   `"Engineering Judgment"`, `"Axis 2"`, `"No shared partials"`,
   `"REFERENCES.md"`, `"plugin:skill"`,
   `"Redact private-project-identifying content"`,
   `"AI agents: don't merge your own PRs"`. Every hit outside moved content
   must still resolve; `.claude/plans/*.md` hits are Axis-3 preserved
   records and are left alone.
7. **Live-load check — post-merge only.** Both `~/.claude/CLAUDE.md` (a
   per-file symlink) and `~/.claude/rules` (a directory symlink) resolve to
   the **main** checkout, so nothing on this branch is live pre-merge.
   After merge and `git pull`: open a file matching each new/extended
   rule's globs in a fresh session and confirm the rule loads; confirm a
   session opening no matching file does not.
8. **Byte accounting** — actual before/after `wc -c` vs. this plan's
   estimates (step 16 above).

## Out of scope

- The machine-level `CLAUDE.local.md` (home directory) — versioned in a
  separate private repo, not this one.
- Stripping rationale from any rule that stays.
- An adherence eval harness.
- Restructuring unrelated content in the extended skills and rules.
- A shared-partial mechanism; duplicate deliberately instead.
- Any change to `permissions.allow`/`deny` or to hook scripts — this plan
  relies on existing enforcement and adds none.
- The `## Commands` bash block in root CLAUDE.md.
- Computing a dollar saving (see "What this actually saves").
- Creating `docs/claude-md-references.md` this pass (see Alternatives).
