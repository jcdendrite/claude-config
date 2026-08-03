# Root-cause analysis: why comment verbosity survived PR #522

## Context

**Goal:** explain why code-comment verbosity in this repo's `.sh`/`.py` diffs is undiminished after PR #522 (`f93170a`, merged 2026-08-01T00:57Z), and hand the engineer a diagnosis — not a fix — to decide from.

#522 was aimed at exactly this symptom: a "One line, not a paragraph" bullet in `claude/.claude/CLAUDE.md` §Code Comments, Documentation, and Prose, plus a **Non-durable comment** tripwire in `code-review/SKILL.md` Step 1.5 and `code-writer.md`. Engineer-confirmed surface: `#` comment blocks in `.sh`/`.py`, **not** PR bodies, commit messages, or the Markdown the diff adds. Engineer-chosen scope this session: **diagnosis only, no fix.**

## Approach

### Root cause

**The dominant source of comment volume in this repo's `.sh` files is mandated by a checklist, not tolerated by one. `claude-hook-review` requires each hook header to document purpose, scope, dispatch surfaces, known gaps, fail posture, `# hook-class:`, the `settings.json` `if`-pattern pairing, and what the hook deliberately does NOT gate — and sets no length bound on any of it. #522 added a generic length rule to CLAUDE.md; a generic rule cannot bind against a specific checklist that requires the content. The specific mandate wins, correctly.**

The mandate, verbatim from `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md`:

| Line | Required header content |
|---|---|
| `:116` | `# hook-class:` declared on the second line — "every hook must" |
| `:114` | "State the chosen posture in the script header. Reviewers shouldn't have to re-derive it" |
| `:125` | "document the pairing explicitly in the script header" (`if`-pattern vs internal regex) |
| `:150` | "Header documents purpose, scope, dispatch surfaces, known gaps, and fail posture" |
| `:156` | "header documents what the hook deliberately does NOT gate" |
| `:159` | "Header lists known gaps the hook does not close" |

`grep -n -i "length\|concise\|verbos"` on that file returns no length constraint of any kind. Its only numeric budget (`:138`) caps hook *runtime* at 100ms.

Map that against the Stage D artifact below and essentially every line is a mandated category — `hook-class`, purpose, dispatch, fail posture, kill switches (scope), and an 11-line "Known gaps" enumeration that `:159` and `:156` between them require twice. A reviewer applying `claude-hook-review` to that header correctly passes it. A reviewer applying CLAUDE.md's "one line, not a paragraph" to the same header would have to strike content another skill mandates. **#522 could not have worked on hook files.**

### Secondary contributors

**(a) The length rule reached the authoring instruction but neither review pass.** #522 did put a length predicate on an enforcement surface — `code-writer.md:55` now says "write any comment as a **one-line** durable fact." But `code-writer.md:81` (self-review step 6) and `code-review/SKILL.md:54` (Step 1.5) both enumerate only the three provenance triggers — *narrates PR/incident history*, *references "this diff"*, *re-litigates a rejected alternative at length*. So length steers at write time and is unchecked at both review points. Corrected from an earlier draft of this plan, which claimed length never reached any enforcement surface; that was wrong.

**(b) Bullet-to-trigger coverage is partial, not absent.**

| CLAUDE.md §Code Comments bullet (`:101-104`) | Trigger on the review surfaces |
|---|---|
| 1. No PR-defined terminology | **none** ← `Part 3's gate repair` |
| 2. No "used to be X" framing | partial — "narrates PR/incident history" |
| 3. Self-test: survives PR-description loss | partial — via the tripwire's governing clause "rather than stating a durable fact about the code," not an enumerated trigger ← `see the plan` slipped it anyway |
| 4. One line, not a paragraph | authoring baseline only; **no trigger at either review pass** |

**(c) Six `code-review` items push toward more comment text, one penalizes removal.** 9c (cite vendor docs for numeric literals), 9d (rationale comment per suppression), 9e (dependency provenance), 14a (config-change intent), `:299` ("mark the boundary with an in-code comment"), `:305` (deferred findings get a "durable home (header comment…)"). Item 12 supplies the only mechanically-checkable comment trigger in the file — and it points at *deletion*: "Check `git diff` for deleted comment lines in changed hunks." `:187` then bars flagging "comment style" in unchanged code.

**(d) Item 12's counterweight no longer exists.** It reads *"The 'default to no comments' rule applies to adding; it does not authorize bulk removal."* `git grep` across tracked files returns only item 12 itself — the rule is gone. Item 12 (added 2026-05-13, PR #211) was written against Claude Code's then-current system-prompt default. The harness now instructs the opposite: **"Write code that reads like the surrounding code: match its comment density, naming, and idiom."** The anti-removal half survived; the anti-addition half it balanced was deleted and replaced with a mimicry instruction — pointed at a corpus (`claude/.claude/hooks/*.sh`, 36 files, **pooled 52% comment lines, per-file 28–69%**, normalization `^\s*#` over total lines; existing contiguous blocks of 142/126/95/84/77) whose density is itself a product of the mandate above.

### Stage D — the artifact

`claude/.claude/hooks/advance-past-commit-stall.sh:2-39`, shipped by PR #536, merged 2026-08-02 — a day after the fix. A 38-line header. Two lines violate CLAUDE.md verbatim:

```
#   fires once, forcing one wasted retry — accepted tradeoff, see the plan.
# - --dry-run/default-branch bypass residuals inherited from Part 3's gate
#   repair are orthogonal to this hook (it does not gate a git operation).
```

`see the plan` points at a document that does not survive the merge (bullet 3); `Part 3's gate repair` is PR-defined terminology undefined in-repo (bullet 1). **The other 36 lines are not narration** — they are mandated categories, and `:26` correctly cites `docs/commit-stall-block.md`, which exists. So the artifact splits cleanly: a *provenance* defect of 2 lines that the tripwires should have caught, and a *length* property of 38 lines that no rule can call a defect while `claude-hook-review` requires the content.

### Measurement — what it does and does not show

Boundary `f93170a`; 17 merged PRs per side; added lines only.

| Metric | BEFORE | AFTER |
|---|---|---|
| Pooled comment ratio | 18.2% | 11.1% |
| Median comments per 100 added code lines (PRs ≥50 code lines) | 7.8 (n=7) | 12.5 (n=12) |
| Max block length | 65 | 38 |

**No trend is claimable from this.** The pooled drop reverses when three outlier PRs are removed (6.3% vs 8.0%), and one of those (#506) was a mechanical 24-file header sweep. The median rise rests on n=7 vs n=12 — a 1.7× retention asymmetry that means the two populations differ in size distribution, not only in date. And the AFTER window is ~1.2 days of commit activity dominated by the GH-526 hook push: **the AFTER sample is disproportionately hook files, the exact class carrying the mandate.** That confound alone predicts a density rise with or without #522.

The defensible claim is the negative one: **nothing here shows a reduction.** The marker counts for the violations #522 names are single-digit on both sides (e.g. references-to-a-plan/PR/ticket: 1 before, 7 after, out of 922 and 486 added comment lines) — too small to carry weight in either direction, and reported here only so the issue does not imply otherwise.

### Assumption ledger

```
Root: comment volume in .sh is mandated by claude-hook-review's six required
  header categories, which carry no length bound. A generic CLAUDE.md length
  rule cannot bind against a specific checklist requiring the content.

Row 1 [finding, verified]: claude-hook-review mandates hook-class, posture,
  if-pattern pairing, purpose/scope/dispatch/known-gaps/fail-posture, and
  deliberate-non-coverage in the header; no length rule exists in that file
  [verified: grep for length/concise/verbos over
  plugins/claude-hook-review/skills/claude-hook-review/SKILL.md this session;
  lines 114,116,125,138,150,156,159 read directly].
Row 2 [finding, verified]: code-review/SKILL.md:176 DOES route
  claude/.claude/hooks/*.sh to claude-hook-review. An earlier draft of this
  plan claimed shell had no length owner; the owner exists, the length rule
  does not [verified: read :176 this session]. Non-hook .sh (scripts/) has no
  skill owner; .py has neither skill nor rule.
Row 2b [finding, verified]: claude/.claude/rules/shell-script-conventions.md
  is a path-scoped rule (paths: **/*.sh, **/*.bash) that auto-loads on every
  shell file, hooks included — 52 lines, and it says nothing about comments,
  headers, or length [verified: read frontmatter and grepped for
  comment/length/header/concise, zero hits]. It is the lightest existing
  surface a shell comment-length convention could occupy.
Row 3 [finding, verified]: length reached code-writer.md:55 (authoring
  baseline) but NOT code-writer.md:81 nor code-review/SKILL.md:54, which both
  enumerate three provenance triggers only [verified: read all three lines].
Row 4 [finding, verified]: Stage D artifact is 38 lines, postdates f93170a,
  and 2 of 38 lines violate CLAUDE.md bullets 1 and 3; the rest map to
  mandated categories and its cited docs/commit-stall-block.md exists
  [verified: read :2-39 and ls'd the doc].
Row 5 [finding, verified]: item 12 cites a "default to no comments" rule
  absent from the repo; the harness replaced it with "match its comment
  density" [verified: git grep returns only SKILL.md:106].
Row 6 [limitation]: the before/after sample supports "no reduction shown"
  ONLY. It cannot support "verbosity rose": n=7/12 after filtering, ~1.2
  days of AFTER activity, and a work-mix confound (AFTER is hook-heavy —
  the mandated class). Point estimates move against the fix, within noise.
Row 7 [verified-with-limit]: "the checklist was never reached" is ruled out
  in its strong form — 9 of 10 prior sessions whose transcripts touch
  advance-past-commit-stall.sh contain the literal string "Non-durable
  comment" [verified: grep -c over
  ~/.claude/projects/-Users-jared-MyCode-claude-config/*.jsonl]. LIMIT:
  loading is not executing, and #536's authoring session ran in a worktree
  with no transcript directory. A third branch remains open and is NOT ruled
  out: the reviewer applied Step 1.5 and correctly cleared the block, since
  36 of its 38 lines are mandated content. Under that branch, interventions
  1-2 are the wrong fix.
Row 7b [finding, verified]: do NOT infer review coverage from the commit
  gate. require-code-review.sh:95 calls
  _lib_chains_marker_write_before_commit, so `marker.sh write code-review &&
  git commit` satisfies it at PreToolUse with no pre-existing on-disk marker;
  the hook also fires only on the Bash tool (:55), and its regex (:64) misses
  `git -C <path> commit` [verified: read the hook and the _lib.sh function
  body this session]. The gate proves a marker write occurred in the command
  string, not that a review executed.
Row 7c [limitation]: the BEFORE sample size does not reproduce cleanly —
  the measurement used 17 PRs/side; an independent recount of first-parent
  squash merges from 2026-07-28 to f93170a returned 20. The boundary must be
  pinned as a SHA with a stated inclusion rule before anyone re-runs this.
  Does not change Row 6's conclusion, which is already "no reduction shown."
Row 8 [assumption, engineer-verified]: symptom is .sh/.py comment blocks;
  deliverable is diagnosis only, no fix.
```

### Candidate interventions — listed, not chosen

Every one edits `claude/**`, which installs to **every contributor running `./install.sh`** — blast radius is all stow consumers, not this engineer. Noted per item.

1. **Add a length bound to `claude-hook-review`** — the mandate lives there, so the bound belongs there ("known gaps as a bulleted list, one line each; link `docs/` for the design record rather than inlining it"). Directly addresses the root cause and reaches hook `.sh` through the route that already exists. Blast radius: hook reviews in every stow consumer's repo. Counter: it constrains a checklist whose categories are individually justified.
2. **Give the review passes a length trigger** — Step 1.5 and `code-writer.md:81` have none. Counter: #522's ledger rejected a numeric threshold for lack of a groundable source, and that stands. A relative trigger ("a comment block longer than the code it documents") needs no external constant. Blast radius: `/code-review` behavior in every repo every stow user reviews — and it fires on every language, not just `.sh`, so the intended scope must be stated. **Under Row 7's third branch this fixes nothing.**
3. **Add a comment-length convention to `claude/.claude/rules/shell-script-conventions.md`** — a path-scoped rule with `paths: ["**/*.sh", "**/*.bash"]` that auto-loads whenever a shell file is open. This is the lightest primitive available and the one that reaches the file class with no owner at all (`scripts/**`, `.py`): a one-file prose edit, no new skill, no new route, no new hook. Blast radius: every stow consumer editing shell, in any repo. Counter: a rule that loads is still advisory — it shares #522's failure mode of steering without checking.
4. **Repair item 12's dangling citation** — a one-sentence rewrite in a stowed skill, independent of the root-cause decision. Should be filed separately so it does not close with the parent issue.
5. **A commit-time hook** — the repo's own precedent for length enforcement is mechanical (`check-claude-md-length.sh`, `check-skill-length.sh`), and repo-root `CLAUDE.md:59` says a recurring automatic behavior is a hook. Costed honestly: both existing hooks are growth *ratchets* on total file lines (`check-skill-length.sh:76` denies only when `new > limit && new > old`), neither measures comment lines, and neither path regex touches `.sh`/`.py`. So this is a new predicate, not a copied one — and #522 rejected a numeric block threshold as ungroundable. Named here because "pair with a mechanical check" is otherwise an empty phrase.

**The enumeration itself may be the wrong foundation.** Step 1.5 shipped with three tripwires, #522 made it four, intervention 2 would make it five — each closing a gap the previous list's incompleteness created. That is CLAUDE.md §Working Style's compounding-layers tell. A tripwire list must carry one trigger per CLAUDE.md bullet forever and drifts every time a bullet is added, which is exactly how #522 failed. Worth answering before adding a fifth: should Step 1.5 enumerate at all, or point at §Code Comments and require the reviewer to walk its bullets?

## Critical files

Read-only for this scope; this is the evidence base.

- `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` — `:114`, `:116`, `:125`, `:138`, `:150`, `:156`, `:159`. The mandate.
- `claude/.claude/skills/code-review/SKILL.md` — `:54` tripwire; `:176` hook route; `:106` item 12; `:187` exclusions; `:88-98`, `:116`, `:299`, `:305` comment-adding items.
- `claude/.claude/CLAUDE.md` — `:101-104`, the four bullets.
- `claude/.claude/agents/code-writer.md` — `:55` (has the length predicate), `:81` (does not).
- `claude/.claude/rules/shell-script-conventions.md` — path-scoped, auto-loads on all shell; currently silent on comments.
- `claude/.claude/hooks/advance-past-commit-stall.sh:2-39` — the artifact.
- `claude/.claude/hooks/require-code-review.sh:55,64,95` + `_lib.sh:542` — why the commit gate does not prove review coverage.
- `git show f93170a:.claude/plans/trim-comment-verbosity.md` — #522's plan. Its Row 5 deferred the general-verbosity question "pending stronger evidence… if this recurs."

**Deliverable:** one GitHub issue. Lead with the root cause and the mandate table; put the before/after measurement in a collapsed block framed as a premise check, not a finding, with Row 6's limitation adjacent to the table rather than in a footnote. Carry Row 7's open third branch into the issue body as a named open question — the headline is unproven without it. File intervention 3 as its own issue. Public repo: all quotes are in-repo, `GH-526` is on the OSS allowlist, no redaction concerns.

## Verification

1. `grep -n -iE "length|concise|verbos" plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` returns no length constraint; `:150`/`:159` read as mandates.
2. `sed -n '176p' claude/.claude/skills/code-review/SKILL.md` confirms the hooks→`claude-hook-review` route.
3. `sed -n '55p;81p' claude/.claude/agents/code-writer.md` — `:55` contains "one-line durable fact," `:81` does not.
4. `sed -n '28,40p' claude/.claude/hooks/advance-past-commit-stall.sh` shows both violations; `docs/commit-stall-block.md` exists.
5. `git grep -n "default to no comments" -- claude/ plugins/ docs/` returns item 12 and nothing else.
6. Density re-derives as pooled 52% / per-file 28–69% across 36 hook files under `^\s*#` over total lines; `head`-truncating a sorted list yields a wrong low end.
7. `grep -n -iE "comment|length|header" claude/.claude/rules/shell-script-conventions.md` returns nothing — the rule is silent on this today.
8. Re-running the measurement must pin the BEFORE boundary as a SHA with a stated inclusion rule (Row 7c), and must reproduce all three normalizations plus the work-mix split; the pooled ratio alone shows a 7-point improvement that is not there.
9. **Row 7's third branch is unsettled.** Before any fix is chosen, establish whether Step 1.5 fired and cleared the block as mandated content. Interventions 1 and 2 assume different answers — and per Row 7b the commit gate cannot be used as the evidence.

## Out of scope

- Any edit to `claude/**` or `plugins/**` — engineer chose diagnosis-first.
- PR bodies, commit messages, diff-Markdown. #522's Row 5 deferral of PR-description verbosity stays open, untouched.
- Reverting #522. Its provenance tripwire is not wrong; it is incomplete, and it was never the binding constraint on hook files.

---

## Fix implementation (issue #544, lands on PR #546)

**This section supersedes the diagnosis-only scope above** — the engineer requested pulling #544's fix into this PR in the follow-up session that produced this section.

### Context

**Goal:** implement issue #544's root-cause fix so hook-header verbosity has a real constraint to fail against, without introducing an ungroundable numeric length cap.

Settled this session (evidence, not assumption): `gh pr view 536` shows the PR that shipped the artifact file ran a cumulative `/code-review` pass explicitly including `claude-hook-review` against the full branch diff, producing 4 fixed + 3 deferred findings, none about header length. This rules out issue #544's Row 7 "never reached" branch — the checklist fired and correctly passed the header as mandated content, because the checklist carries no bound for that content to fail against. That is the gap this fix closes.

**Engineer constraint (this session):** no numeric length threshold. #522's own plan already rejected one as ungroundable — no vendor or style-guide source caps comment block height — and the engineer independently flagged strict length bounds as an anti-pattern, consistent with a prior `/verify-sources` finding that no official source recommends restricting code on length alone. Both interventions below are structural (one fact per bullet, elaboration moves to `docs/`) or relative (a block longer than the code it documents) — neither asserts an arbitrary line count.

### Approach

Two complementary, independent edits — chosen over the diagnosis's other three candidates:

**1. `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` — bound the mandate at its source.** The checklist (§9) requires headers to document purpose, scope, dispatch, known gaps, and fail posture with no shape constraint on any of them — `advance-past-commit-stall.sh`'s "Known gaps" alone runs 14 of the header's 38 lines across 5 enumerated items. Add one checklist bullet, placed after the existing `:159` "Header lists known gaps" bullet:

> Each fact within a header category (each known gap, each scope exclusion, etc.) is one sentence — a category with multiple facts stays a multi-line bulleted list, one sentence per bullet (wrapping a long sentence across physical lines for width is fine; splicing in a second fact or its rationale is not); elaboration beyond the fact goes in docs/, cited by path, not inlined.

"Sentence," not "line": `deny-private-project-refs.sh:9-17`'s own Dispatch field — the checklist's own cited model at `:125` — wraps a single logical clause across 7 physical lines for readability, which this rule must not flag. The design is grounded in an existing rule already in force globally in this repo (CLAUDE.md §Code Comments) — not a new invented constant — but the shipped bullet text does not name CLAUDE.md: `claude-hook-review` is an independently-installable marketplace plugin (its own description: "Install at project scope in repos that author `.claude/hooks/*.sh` scripts"), so it cannot assume every installing repo's CLAUDE.md carries an equivalent rule the way `claude/.claude/rules/shell-script-conventions.md` can (same stow package as the global CLAUDE.md it cites, so the two are always co-installed). Caught during `/code-review`'s `skill-review` sub-pass; fixed by dropping the CLAUDE.md reference and stating the rule self-contained. Rejected: a numeric line cap per the constraint above; rewriting the six mandated categories down to fewer categories (each is independently justified per the diagnosis, and narrowing categories is a different, larger change than bounding their shape).

**2. `claude/.claude/rules/shell-script-conventions.md` — cover the file classes with no owner today.** This path-scoped rule (`**/*.sh`, `**/*.bash`) auto-loads on every shell file and currently says nothing about comments. Add one bullet, following the file's existing bullet format (bolded lead claim, then explanation):

> **Match CLAUDE.md's comment-length convention in every `#` block.** State each non-obvious fact as one sentence, not a multi-sentence rationale — move elaboration or design rationale to `docs/`, cited by path, rather than inlining it in the comment.

The diagnosis found bullet 4 ("one line, not a paragraph") has no trigger at either review pass today — this closes that gap. This is the lightest available primitive — a one-file prose addition, no new skill/route/hook — and the only one of the five candidates that reaches non-hook `scripts/**`. It remains advisory (steers without a check), same limitation #522 had; accepted because the mandate-vs-generic-rule conflict that made #522 fail on hook files specifically is what edit 1 closes, and non-hook shell scripts have no competing mandate to conflict with.

**Not implementing (from the diagnosis's candidate list):**
- **Intervention 2** (relative length trigger on `code-review` Step 1.5 / `code-writer.md` step 6) — weakened by the Row 7 finding: the generic Step 1.5 tripwire isn't what reviewed the artifact file, `claude-hook-review` was, so a Step 1.5 trigger would not have caught this case. Also flagged in the diagnosis as a 5th tripwire in a compounding-layers pattern (3 → #522's 4 → this would be 5) — the diagnosis's own recommendation was to question the enumeration before extending it again.
- **Intervention 5** (commit-time hook) — diagnosis costs this as a new predicate, not a copy of existing precedent (`check-claude-md-length.sh`/`check-skill-length.sh` are line-count ratchets, not comment-density checks, and neither's path regex touches `.sh`/`.py`). Mechanical enforcement of a non-numeric, per-category shape rule doesn't have a clean predicate shape; out of scope for this PR.
- **Intervention 4** (item 12's dangling citation) — filed separately as issue #545 specifically so it would not ride on #544; stays out of scope here per that issue's own stated intent.
- **`.py` and non-hook `scripts/**` beyond edit 2's reach** — edit 2 only covers `**/*.sh`/`**/*.bash`. `.py` remains genuinely unowned after this fix; not addressed here since none of #544's five candidates proposed a `.py`-specific mechanism. Worth a future issue if `.py` verbosity recurs, not this PR's scope.

#### Assumption ledger

```
Root: claude-hook-review mandates hook-header content with no shape bound;
  a generic CLAUDE.md length rule cannot bind against a specific checklist
  requiring the content it forbids. [established in the diagnosis section
  above, re-affirmed here]

Row 1 [finding, verified]: PR #536's body documents a cumulative /code-review
  pass that included claude-hook-review against the full branch diff (4 fixed
  + 3 deferred findings, none about header length) [verified: `gh pr view 536
  --json body` read this session]. Settles Row 7: the checklist fired and
  correctly passed the header as mandated content.
Row 2 [engineer-verified]: no numeric length threshold — engineer flagged
  strict length bounds as an anti-pattern this session, consistent with a
  prior /verify-sources finding (no official source recommends restricting
  code on length alone) and with #522's own ungroundable-threshold rejection
  already on record in this plan's diagnosis section.
Row 3 [finding, verified]: claude-hook-review/SKILL.md:150-159 (the review
  checklist) has no comment-length or shape guidance of any kind
  [verified: read the file this session; matches the diagnosis's original
  grep-for-length-concise-verbos finding].
Row 4 [finding, verified]: advance-past-commit-stall.sh's "Known gaps"
  section (lines 26-39) is 14 of the header's 38 lines, the single largest
  category [verified: read the file this session].
Row 5 [finding, verified]: shell-script-conventions.md is silent on comments
  entirely and auto-loads on every .sh/.bash file including hooks
  [verified: read the file this session; matches the diagnosis's finding].
Row 6 [finding, verified]: claude-hook-review is a marketplace plugin
  (plugins/claude-hook-review/.claude-plugin/plugin.json present) at version
  2.2.0 [verified: read plugin.json this session] — plugin-semver requires a
  version bump for this change; minor (2.2.0 -> 2.3.0), matching PR #536's
  precedent for a backward-compatible checklist addition.
```

### Critical files

- `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` — add one bullet to the §9 review checklist, after the existing `:159` "Header lists known gaps" bullet, using the exact text quoted in Approach item 1 above.
- `plugins/claude-hook-review/.claude-plugin/plugin.json` — bump `version` `2.2.0` → `2.3.0` (minor: backward-compatible checklist addition, per `plugin-semver`).
- `claude/.claude/rules/shell-script-conventions.md` — add one bullet using the exact text quoted in Approach item 2 above, following the file's existing bullet format (no vendor citation needed since this cites an in-repo rule, not an external standard).

Reuse: no new mechanism in either file. Edit 2 points at CLAUDE.md's already-existing "one line, not a paragraph" bullet by name, safe because `shell-script-conventions.md` ships in the same stow package as the CLAUDE.md it cites. Edit 1 is grounded in the same CLAUDE.md rule but does not name it in the shipped text — `claude-hook-review` is an independently-installable plugin with no such co-installation guarantee, so its bullet states the rule self-contained (see Approach item 1's platform-genericness note).

### Verification

1. `grep -n -iE "length|concise|docs/" plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` shows the new bullet.
2. `jq -r .version plugins/claude-hook-review/.claude-plugin/plugin.json` reads `2.3.0`.
3. `grep -n -iE "comment|length" claude/.claude/rules/shell-script-conventions.md` shows the new bullet (currently returns nothing, per the diagnosis's Verification step 7).
4. `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/` stay clean — this change touches no executable code, only skill/rule prose.
5. `/skill-review` (hook-enforced on the `SKILL.md` edit) and `plugin-semver` (hook-enforced on the plugin-directory edit) both pass before commit, per `.claude/rules/review-pipeline-dispatch.md`.
6. `/code-review` on the staged diff before commit, then `/ready-for-review` before push (PR #546 already exists, so its step 6 create-PR is a no-op).

### Out of scope (fix phase)

- Interventions 2, 4, 5 from the diagnosis — see rationale above.
- `.py` / non-hook `scripts/**` comment-length ownership beyond what edit 2 already covers.
- Any change to `advance-past-commit-stall.sh` itself — its header is correct under the *current* mandate; this fix changes the mandate going forward, it does not retroactively edit the artifact that surfaced the gap.
