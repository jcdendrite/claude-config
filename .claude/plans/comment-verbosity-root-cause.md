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
