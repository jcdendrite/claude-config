# Replace the pooled-tooling-measurement carve-out with a this-repo/single-account publication bar

## Context

Replace `docs/private-project-redaction.md`'s pooled-tooling-measurement carve-out with a single
publication-scope bar: a measurement published in a public artifact of this repository is
computed over this repository's own corpus on one `CLAUDE_CONFIG_DIR` account, and anything wider
is reported to the owner privately and never published. The carve-out grew from 78 to 382 lines
across ten amendments in five days, each closing a compositional gap the last reviewer found — the
shape `CLAUDE.md` itself names as a wrong-foundation tell ("Compounding defensive layers are a
wrong-foundation tell"), not a policy converging. This session's fifth `/code-review` round on a
rework of `docs/case-studies/handoff-hard-block-position.md` found a Critical-severity live
composition-reconstruction risk and a rate-denominator violation; a `plan-architect` consult
recommended against loosening the denominator, proposed yet another new reporting form, and found
the carve-out's own precedent study already carrying the identical violation in a clearer form. An
outside Fable consult concluded the threat the machinery defends against — a reader estimating the
engineer's own Claude Code bill — does not justify it, and the engineer endorsed the simpler bar
this session.

The intended outcome: the carve-out section is rewritten to the simple bar with its live citations
and tests retargeted in the same change; the in-flight Phase 2 rework built for the old model is
discarded from the index before any editing begins; the predecessor plan's factually wrong G4 row
is corrected on the record; and every already-published figure — this study's and the precedent's
alike — stays exactly as published, under a grandfather rule stated once in the doc rather than as
a per-file note.

**Engineer confirmations this session** (all `[engineer-verified]`, carried forward into the ledger
below):

- Scope is the doc, its citing sites, and its tests. `transcript-analysis.py`'s `--share-only`
  mode code stays — unused by new case studies under the new bar, but not broken and not removed.
- `docs/case-studies/handoff-threshold-impact.md` is left fully untouched — no edit at all, not
  even a grandfather note. It predates the bar; rewriting published figures re-raises the
  retraction question `CLAUDE.md` already resolves against.
- `cdff9e60`'s rate-denominator restriction is moot for a this-repo/single-account figure, which
  has no private pooled total hiding behind it to reconstruct. The commit is not reverted; the new
  bar simply does not carry or restate the restriction.
- The private-reporting path for cross-account and machine-wide questions is documented practice
  only — no new script, no new skill.

## Approach

Delete the carve-out and replace it with one scope bar plus two named exemptions, in a section
roughly a quarter its current length. A figure computed over this repository's own corpus on one
account carries no private-engagement record, so nothing about it is withheld — total, rate,
median, share, pool size, calendar series, before/after split at any pivot are all publishable, and
the closed lists, three standing bars, split conditions, and approval gate that governed them are
deleted rather than amended. A figure computed over anything wider is not published in any form; it
is measured with the same tools at their wider scope and reported to the owner off-channel. The two
classes that were never inside the carve-out — own-history counts, and account cardinality's
absolute bar — survive as their own subsections, because each governs figures the new bar does not
reach.

Every heading in the section is renamed or deleted, and all seven live citation sites are
retargeted in the same change. Preserving the old anchors to avoid citation churn was the obvious
lighter path and it fails: `### Three standing bars` names three bars this plan deletes, and its
one citing site cites it specifically *for* the deleted "No time series" bar. The three prose sites
carrying the H2 citation all end "…and its approval gate, unexercised here," so they need an edit
regardless of the rename — preserving the H2 saves no file, only a token per file.

A `/plan-review` round (`ciso-reviewer`) and a `plan-architect` consult on an alternate design —
never disclosing pooling scope or account cardinality, so pooled figures could keep publishing
freely with none of this bar's machinery — both ran before this plan was finalized. The alternate
design was rejected: concealing scope doesn't stop the arithmetic once an exact this-repo figure
ships automatically on every merged PR (`pr-cost-section.sh`), and it would require rewriting
`CLAUDE.md`'s own composition-is-publication reading, not just this carve-out. Two real gaps in
this plan's own first draft survived that consult and are fixed here: `pr-cost-section.sh`'s
automated output needed a standing pre-clearance rather than silent reliance on "already covered"
(rows 11, 21), and the one surviving composition rule needed to reach every new figure, not only
own-history ones, with a named target list rather than an unbounded search (rows 12, 22, 23).

### Assumption ledger

**Root problem.** A reviewer-discipline carve-out permitting mixed-corpus figures has accreted ten
amendments in five days without converging, because the class of figure it exists to permit — a
pooled measurement over a corpus containing private-engagement records — cannot be made safe by
composition rules; the repository publishes a large empirical corpus anyway, so the bar has to move
rather than tighten.

**Givens** (fixed beyond this plan's reach):

- **G1.** Remediation is forward-looking only and never a retraction — git history retains every
  published figure regardless of what the tip says, and the same source bars an agent-run rewrite.
  `[verified: docs/private-project-redaction.md:464-470]`
- **G2.** This tier is reviewer discipline with no hook behind it; a figure's safety depends on
  which corpus produced it, which a `PreToolUse` hook cannot see. This plan adds no mechanical
  enforcement and none is available to it. `[verified: docs/private-project-redaction.md:93-95]`
- **G3.** `docs/case-studies/**` and `docs/reports/**` sit outside `_all_doc_paths()` as preserved
  records, so no test asserts anything about a case study's contents. The only test surface a
  case-study edit can break is nothing at all — which is why the Verification section's citation
  sweep checks `docs/case-studies/` by grep rather than relying on a test.
  `[verified: claude-skills/skills/tests/test_skills.py:4566-4579]`
- **G4.** `.claude/skills/*/SKILL.md` *is* inside `_all_skill_md_files()`, alongside
  `claude-skills/skills/*/SKILL.md` and `plugins/*/skills/*/SKILL.md`. Both SKILL.md citations are
  therefore already covered by the general citation-resolution test; the dedicated test at
  `:3129-3143` asserts a different thing (that the citation is still *present*, not deleted).
  `[verified: claude-skills/skills/tests/test_skills.py:2419-2441]`
- **G5.** `cost --this-repo --summary`'s scope refusal is shipped behavior this plan does not
  change — the engineer's scope answer puts `transcript_analysis` scope code out of reach.
  `--summary` requires `--this-repo`, refuses every other scope flag, resolves to the active config
  dir alone, and exits 2 if more than one root is ever in scope. `[verified:
  docs/transcript-analysis.md:619, read this session; corroborated by Step 3's reads of
  transcript_analysis/scope.py:527-528 and cost.py:534-565]`

**Assumption rows:**

1. The carve-out is replaced with a this-repo/single-account publication bar; cross-account and
   machine-wide questions are measured and reported to the owner privately, never published.
   `[engineer-verified]`
2. `docs/case-studies/handoff-threshold-impact.md` is left fully untouched — no figure edit, no
   grandfather note. `[engineer-verified]`
3. `cdff9e60`'s rate-denominator restriction is moot for a this-repo/single-account figure and is
   not restated in the new bar. The commit is not reverted. `[engineer-verified]`
4. The private-reporting pattern is documented practice only — no new script or skill.
   `[engineer-verified]`
5. Scope is doc + citing sites + tests. `--share-only`'s mode code is not removed or reworked.
   `[engineer-verified]`
6. **Every heading in the section is renamed or deleted rather than preserved as a citation
   anchor.** `### Three standing bars` cannot survive honestly once its three bars are deleted, and
   its single citing site cites it *for* the deleted "No time series" bar, so that citation must be
   retargeted on content grounds whatever the heading says. Anchors: root. `[verified:
   docs/transcript-analysis.md:620; claude-skills/skills/tests/test_skills.py:2989-3005]`
7. **The rename adds a token per file, not a file.** Three of the five parametrized citing sites
   end their citation with "…and its approval gate, unexercised here" — a gate this plan deletes —
   so all three take a prose edit regardless. Anchors: row 6. `[verified:
   docs/cost-levers-considered.md:533; docs/design-decisions/schedulewakeup-misapplied-documented.md:14;
   docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md:7]`
8. **Those three sentences are not preserved records.** Each states, in the present tense, which
   rule currently governs a withheld figure. Under `CLAUDE.md` Axis 3's own decision test they
   describe how the system currently behaves rather than recording something that happened, so
   editing them is maintenance, not record revision. Anchors: row 7.
9. **Four live consumers Step 3 did not name.**
   `claude-skills/skills/transcript-analysis/SKILL.md:98`, and
   `claude/.claude/scripts/transcript_analysis/cost.py` at `:390-392` (docstring), `:580-582`
   (stderr refusal), `:962-966` (stdout banner). `[verified: repo-wide grep for each site, direct
   read of each cited line range]`
10. **Editing those three `cost.py` strings is inside row 5, not an exception to it.** "Leave the
    mode code as-is" reads as "do not remove or rework the mode"; reading it as "no edit at all"
    leaves a shipped banner citing a deleted heading and directing the reader to an approval gate
    that no longer exists. No flag, exit code, refusal, or render path changes, and the substrings
    `test_transcript_cost.py` asserts (`"cost: --share-only refuses --by-project"` and siblings)
    are preserved verbatim. Anchors: rows 5, 9. `[verified:
    claude/.claude/scripts/tests/test_transcript_cost.py:2742-2778 assert only the refusal
    prefixes; grep for "private-project-redaction" over claude/.claude/scripts/tests/ returns only
    test_install_dev.py:544,575, which match the filename alone]`
11. **The Approval gate has no remaining subject for author-composed figures and is deleted for
    those.** Under the new bar a this-repo/single-account figure needs no per-instance approval, and
    a wider figure is barred outright, which is strictly stronger than a gate. `pr-cost-section.sh`'s
    automated output is a distinct case: no per-instance approval could ever reach it, since
    `/code-review`'s P1 pass runs before `/ready-for-review` composes the PR body that embeds it
    (`ready-for-review/SKILL.md` step 3 vs. step 5-6). Its scope-refusal contract is cleared once,
    as a standing determination in "This repository, one account," rather than per publication — see
    row 21. Anchors: root, row 21. `[verified: docs/private-project-redaction.md:166-169,362-462;
    ready-for-review/SKILL.md step ordering, confirmed by a `plan-architect` consult this session]`
12. **One composition rule survives, broadened to cover every new figure and given a named
    target list.** This plan deliberately leaves grandfathered pooled figures published (row 2), so
    the case where a *new* figure can newly disclose something private — subtracting it from an
    already-published pooled figure down to its non-this-repo remainder — still exists, and is not
    limited to own-history figures: a `ciso-reviewer` pass this session found the plan's first draft
    textually scoped this check to `### Own-history counts` alone, missing that a this-repo/
    single-account figure (the bar's own worked case) is exactly as composable. The rule now lives
    in its own subsection, "New figures against the grandfathered set," and points at Remediation's
    named list rather than an unbounded "search everything" instruction. Anchors: rows 2, 11, 22.
    `[verified: docs/private-project-redaction.md:310-331, the five bullets this collapses;
    ciso-reviewer finding this session, confirmed against ready-for-review/SKILL.md step ordering
    and pr-cost-section.sh:43,51]`
13. **The staged rework is discarded from the index before any editing begins.** Three files sit
    staged (`M ` in the index); any `git commit` — including the plan-file commit — would otherwise
    carry them. `[verified: git status at session start]`
14. **`handoff-hard-block-position.md` needs no edit under this plan.** At HEAD (last touched by
    commit `0f3336ac`, PR #977), this file contains no reference to `private-project-redaction`,
    "permitted split," "Three standing bars," "approval gate," or any citation this plan retargets
    — the only such citations live in this session's own staged Phase-2-compliance rework, which
    Phase 0 discards before any editing begins. Anchors: rows 2, 6. `[verified: git show
    HEAD:docs/case-studies/handoff-hard-block-position.md | grep -n 'private-project-redaction'
    returns nothing; broader grep for "permitted split", "Three standing bars", "approval gate",
    "calendar axis", "calendar-dated pivot", "pool-volume" also returns nothing against the same
    HEAD blob]`
15. `docs/case-studies.md` needs no edit after the discard — it cites no heading in this doc.
    `[verified: git show HEAD:docs/case-studies.md | grep -n 'private-project-redaction\|pooled'
    returns nothing]`
16. `docs/cost-levers-considered.md` needs exactly one edit after the discard, at `:533`. Its
    grandfathered figure rows at `:311-315`, `:535-544`, and `:552-575` — including the
    machine-wide pooled review-round share — are untouched. `[verified: git show
    HEAD:docs/cost-levers-considered.md | sed -n '530,535p' confirms the exact targeted sentence
    is present at HEAD: "...is governed by `docs/private-project-redaction.md` §
    \"Publishing a pooled tooling measurement\" and its approval gate, unexercised here."]`
17. **The predecessor plan's G4 was wrong on both halves, and the correction is load-bearing
    here.** It claimed `pr-cost --record` "resolves a single account by default
    `[verified: transcript-analysis.py:8510]`"; that line sits inside `_resolve_pinned_gh_repo`,
    unrelated `gh repo view` identity-matching code. Correct behavior: `cmd_pr_cost` calls
    `_resolve_cost_roots(args, "pr-cost")`, which skips the `cost`-only single-root short-circuit
    and unions every declared root; `cmd_pr_cost` then exits 2 rather than write or report a
    blended total when more than one resolves, and `--all-accounts` opts into a per-account loop
    instead of blending. The new bar states this correctly as a *refusal*, which is what makes it a
    publication instrument. `[verified: Step 3 exploration — transcript-analysis.py:8865,8886-8901;
    transcript_analysis/scope.py:527-528,530-555]`
18. **The bar rests on a refusal, not on flag hygiene.** A command that merely *can* be scoped
    narrowly is not a publication instrument; a command that exits non-zero on a wider corpus is
    one a reader can re-run and check. Anchors: rows 17, G5.
19. **The exemption line is the scope's content, not the quantity's type.** A machine-wide
    `transcript-analysis.py` count of tool calls, sessions, or dispatches is now barred — its scope
    is every account's transcripts, which contain private-engagement records. A machine-wide PR,
    branch, review-finding, hook-denial, or log-line count stays outside the bar, because its scope
    is a named set of public and personal repositories containing none. This is what the existing
    "Own-history counts" section already says; the new bar changes only that the carve-out no
    longer reopens the first case. `[verified: docs/private-project-redaction.md:282-305]`
20. **Line numbers for the three affected files are verified against HEAD, not the working tree.**
    `docs/case-studies.md`, `docs/cost-levers-considered.md:533`, and
    `handoff-hard-block-position.md` (row 14) were each checked directly against HEAD rather than
    the staged working-tree diff, so no residual line-number risk remains for any of the three.
    Anchors: rows 14, 15, 16.
21. **`pr-cost-section.sh`'s two automated blocks are each scope-fixed by construction, and the
    whole script is opt-in.** It is gated by the `<config-dir>/pr-cost-disclosure` sentinel (exact
    content `"dollars"`, off by default — a fork contributor publishes nothing until they enable
    it). Its dollar block calls `cost --this-repo --branches <branch> --summary` (G5's refusal
    applies). Its counts block calls `cost-counts --this-repo --branches <branch>`, which requires
    `--this-repo`, refuses any `--projects` scope, and always resolves to
    `[config_dir() / "projects"]` — a single hardcoded root with no flag that can widen it. Neither
    call can ever produce a wider-than-this-repo figure, which is what makes a standing
    determination sufficient in place of per-instance approval (row 11). Anchors: row 11.
    `[verified: claude/.claude/scripts/pr-cost-section.sh:1-51;
    claude/.claude/scripts/transcript-analysis.py:3001-3035]`
22. **Remediation's named list is the same set Out-of-scope already enumerated, now made
    canonical in the doc itself.** `docs/case-studies/handoff-threshold-impact.md`,
    `docs/cost-levers-considered.md`'s machine-wide pooled review-round share and machine-wide
    rows, `docs/case-studies.md`'s index blurbs, and eight case studies under
    `docs/case-studies/` (`effort-estimation-review-surface.md`, `hashline-edit-format.md`,
    `targeted-read-discipline.md`, `delegate-instrument-authoring.md`,
    `plan-mode-model-resolution.md`, `opus-frontload-review-rounds.md`,
    `markdown-context-ingestion.md`, `review-vs-babysitting.md`) — all confirmed to exist at these
    paths this session. Anchors: row 12. `[verified: find docs -iname '<name>.md' for all eight;
    Out-of-scope's own pre-existing enumeration]`
23. **The engineer declined to audit whether currently-shipping per-PR counts already compose
    against the grandfathered machine-wide pooled counts.** Asked directly this session, the
    engineer's answer: no further action — the grandfathered figures predate this bar and their
    relevance decays as the corpus ages, so this is resolved once (recorded in "Remediation") rather
    than re-derived per PR. `[engineer-verified]`

### Mechanisms

**M1 — replace the section outright rather than amend it an eleventh time.** `anchors: root, row1,
row11`.

Over-powered-primitive check. The heavier primitive here is *retaining machinery*, and two
lighter-looking alternatives were weighed and rejected: (i) amend the existing section again,
adding a this-repo/single-account condition on top of the closed lists, three bars, split
conditions, and approval gate — rejected, it is precisely the compounding-defensive-layers pattern
`CLAUDE.md` names as a wrong-foundation tell, and every condition left standing governs a figure
class the new bar no longer permits, so the amendment would be dead text on arrival; (ii) keep the
carve-out as a documented fallback route beneath a new "preferred path" subsection — rejected, two
live publication routes with different bars is a single-source-of-truth violation, and
`code-review-claude-config`'s P1 item, which reads this doc, would have no way to tell which route
a given figure answers to.

**M2 — rename every surviving heading and retarget all seven live citations in the same phase.**
`anchors: row6, row7, row9`.

Over-powered-primitive check on renaming rather than preserving anchors. Two lighter primitives,
both rejected: (i) preserve `## Publishing a pooled tooling measurement` and `### Three standing
bars` byte-for-byte so no citation and no test moves — rejected, "Three standing bars" would
resolve a live citation onto content that deleted all three bars, and
`docs/transcript-analysis.md:620` cites it *for* the deleted "No time series" bar, so that one site
needs a content edit no anchor preservation avoids; (ii) preserve only the H2 and rename the
sub-headings — rejected, "pooled" is the exact word the new bar negates, and per row 7 the three
files carrying that citation are already being edited, so preserving it saves no file.

**M3 — keep the two classes that were never inside the carve-out, collapsed.** `anchors: row12,
row19`. "Own-history counts were never inside this class" keeps its heading verbatim and loses its
last two bullets to row 12's two-sentence composition rule. Account cardinality's absolute bar
moves out of the deleted "Account and machine scope" into its own subsection, since
`docs/transcript-analysis.md:510`'s root-count elision convention depends on it and it is
independent of the carve-out.

**M4 — discard, then edit, in that order.** `anchors: row13, row14`. The index reset is a
parent-session git operation that must complete and be verified before the plan file is committed
or any dispatch starts, because a staged file is carried by any commit regardless of which files
the dispatch touches.

### Phases

0. **Discard the predecessor's staged Phase 2 work.** Parent session, before committing the plan
   file and before any dispatch.
1. **Rewrite the doc section, and retarget its prose consumers and tests.**
2. **Retarget the tool-surface consumers** (two SKILL.md files, `cost.py`,
   `docs/transcript-analysis.md`).
3. **Dropped — see row 14.** `docs/case-studies/handoff-hard-block-position.md` needs no edit;
   renumber nothing else.

**Dispatch split.** Phases 1–2 go to **one** `code-writer` dispatch, not two. They partition
cleanly by file, but every edit in Phase 2 is keyed to the exact heading names and bar text Phase 1
authors — restating that shared text in two prompts is the don't-split signal `plan-it` names, and
two agents resolving "what is the new heading called" independently is exactly the drift the single
dispatch avoids. Phase 0 stays in the parent because it is a git index operation with no authoring
in it.

## Critical files

### Phase 0 — parent session, before anything else

```
git checkout HEAD -- docs/case-studies.md docs/case-studies/handoff-hard-block-position.md docs/cost-levers-considered.md
```

Updates index and worktree together. Confirm with `git status --short` that no `M ` entry remains
for any of the three. Nothing is committed in this phase and no revert commit is created — the
staged content was never committed, so there is nothing in history to undo.

**`cec0904a` needs no revert either.** It touched only `docs/private-project-redaction.md`'s
carve-out section and `code-review-claude-config/SKILL.md`'s P1 item — both of which Phase 1 and
Phase 2 overwrite directly. A fresh commit superseding that text is the whole disposition; git
history is not rewritten.

### Phase 1

**`docs/private-project-redaction.md`** *(modify)* — replace `:91-470` entire (the `##
Publishing a pooled tooling measurement` H2 through the end of `### Remediation`, stopping before
`## Why the blocklist can't be armed by default`) with:

```markdown
## Publishing a tooling measurement

This is the tier-3 companion to the two mechanical tiers above. It is
reviewer discipline, not a hook. A measurement's safety depends on
which corpus produced it, not what string it contains, and a hook
can't see that. The repo-root
[`CLAUDE.md`](../CLAUDE.md) "Also redact structural fingerprints and
provenance" rule states the bar this section gates.

### This repository, one account

A measurement of this repo's own tooling in use is publishable when it
is computed over this repository's own corpus on a single
`CLAUDE_CONFIG_DIR` account. That corpus holds no private-engagement
record, so a figure drawn from it carries no engagement's fingerprint
to redact.

Scope it with a command that *refuses* a wider corpus, and cite that
command beside the figure. Avoiding a wider corpus by choosing flags
carefully is not the same thing: the refusal is what a reader can
re-run and check.

- `transcript-analysis.py cost --this-repo --summary` is the worked
  case. `--summary` requires `--this-repo`, refuses every other scope
  flag, resolves to the active config dir alone, and exits 2 if more
  than one root is ever in scope.
- `pr-cost --record` refuses differently, to the same effect. It
  unions every declared root, then exits 2 rather than write or report
  a blended total when more than one resolves. `--all-accounts` opts
  into a per-account loop, whose output publishes nothing — see the
  next section.
- A subcommand with no scope refusal of its own is not a publication
  instrument. Route it through the next section instead.

`pr-cost-section.sh` is a standing pre-cleared instance of this bar,
not a per-publication judgment call. Gated by the opt-in
`<config-dir>/pr-cost-disclosure` sentinel (off by default — a fork
contributor publishes nothing until they enable it), it embeds two
scope-fixed-by-construction blocks in every merged PR's body: a dollar
total from `cost --this-repo --branches <branch> --summary`, and
review-round/subagent-spawn counts from `cost-counts --this-repo
--branches <branch>`, which always resolves to a single hardcoded root
regardless of any flag. Neither call can be pointed at a wider corpus,
so there is nothing for a reviewer to approve per PR — the review
question is answered once, here, rather than re-litigated on every
merge. A diff that widens either call's scope, or weakens a refusal a
publication instrument depends on, is a P1 finding.

Within that scope nothing further is withheld. A total, a rate, a
median, a share, a pool size, a per-day series, a before/after split
at any pivot — all publishable. This repository's own commit and PR
history is already public, so neither a calendar axis nor a volume
count over it discloses anything the repository does not already
disclose, and the dollars are the owner's own spend on public work.

Doubt about whether a given repository or account genuinely carries no
private-engagement record goes to the owner. Doubt is never a reason
to publish anyway.

### A wider corpus goes to the owner, never into a public artifact

Machine-wide, multi-account, and cross-machine questions are worth
asking. They are not worth publishing. Measure them with the same
tools at their wider scope — `cost` without `--summary` (add
`--share-only` to keep raw absolutes out of the agent's context),
`pr-cost --all-accounts`, or any subcommand at its default machine-wide
scope. Report the figures to the owner in session or through another
non-public channel.

Publish nothing computed from such a read: no total, no rate, no
share, no count, no bounded range, in any artifact — commit message,
PR body, issue, decision record, case study, or illustrative example.
A public artifact may record that the read happened and what it
decided: which lever was adopted or declined, and which way the
reading pointed. It names no figure from it. The withholding sites in
`docs/cost-levers-considered.md` and `docs/design-decisions/` are the
worked shape.

### Own-history counts were never inside this class

The test is the scope's content, not the account or machine count, and
not the quantity's type. Two classes fall outside the bar above rather
than being exceptions to it:

- A count whose scope holds no private-engagement record anywhere —
  this repo's own history, or the owner's other personal, non-client
  repositories. Examples: branch, PR, review-finding, hook-denial, and
  log-line counts. This holds however many accounts or machines the
  scope unions, for a count with no per-account or per-machine
  decomposition: unioning more roots discloses more of the same thing,
  not a new one.
- It does not extend to a `transcript-analysis.py` Cost, Duration, or
  activity measurement — tool calls, sessions, dispatches, dollars, or
  wall-clock — at a scope wider than the bar above. Those read
  whatever private work the roots in scope contain, which is what the
  bar keeps out of a published figure.

This exemption covers what a figure is, not what it combines with —
see "New figures against the grandfathered set" below for the
composition check every new figure clears, own-history or not.

### Account cardinality

How many accounts or declared config-dir roots exist is never
published as a digit or a bounded range, at any pooling breadth. An
account can correspond to a single private engagement, so its
cardinality is the per-account dimension the repo-root `CLAUDE.md`
bars absolutely. `docs/transcript-analysis.md`'s sample outputs elide
the root count for this reason.

How many machines exist may be stated as a digit. A machine is the
operator's own hardware and partitions no engagement. This repository
states its own machine count in ordinary prose. Doubt about whether an
operator's own machine boundary correlates with an engagement boundary
— as it could for a fork contributor running client-dedicated hardware
— goes to that operator, the same as every other content-purity
judgment call in this section.

### New figures against the grandfathered set

Figures published before this bar stay published (see "Remediation"
below). A new figure — own-history or drawn from this repository, one
account — can still newly disclose something private if it lets a
reader subtract it from one of those down to its non-this-repo
remainder. Before publishing, check the new figure against
Remediation's named list. If it could narrow one of those figures'
residual, tell the owner what the combination would newly disclose and
ask in session; an uncited in-session answer settles it.

This check runs once per new figure, forward from here. It does not
require re-auditing the grandfathered set against this repository's
own accumulating totals — the owner has weighed that once (see
"Remediation") and it is not re-litigated per publication.

### Remediation

Content published before this bar took effect stays as published. The
grandfathered set, computed over a machine-wide, multi-account corpus
under the now-closed carve-out:

- `docs/case-studies/handoff-threshold-impact.md`, entirely
- `docs/cost-levers-considered.md`'s machine-wide pooled review-round
  share and machine-wide rows
- `docs/case-studies.md`'s index blurbs
- `docs/case-studies/effort-estimation-review-surface.md`,
  `hashline-edit-format.md`, `targeted-read-discipline.md`,
  `delegate-instrument-authoring.md`, `plan-mode-model-resolution.md`,
  `opus-frontload-review-rounds.md`, `markdown-context-ingestion.md`,
  and `review-vs-babysitting.md`

The bar above governs what ships next, not what already shipped:
rewriting a published figure is not a retraction once a public repo's
history can be cloned, forked, or cached — it only adds a second
version.

Whether this repository's own now-accumulating single-account totals
(`pr-cost-section.sh`'s per-PR figures) already compose against this
grandfathered set closely enough to matter is the owner's call, made
once here: no further action. The grandfathered figures predate this
bar and their relevance decays as the corpus ages; this is not
re-audited per PR.

A wrongly-scoped figure discovered already published is the owner's
call, not the agent's. Stop and report what was published and where —
do not rewrite history yourself, even if told to.
```

**`CLAUDE.md`** *(modify)* — `:161-166`, replace from `One narrow carve-out` through `Work that
section before publishing under it.` with:

```
A measurement of this repo's own tooling in use is publishable only
when it is computed over this repository's own corpus on a single
account — `docs/private-project-redaction.md` § "Publishing a tooling measurement" states the scope bar, the commands that enforce it, and
the two exemptions. Anything wider goes to the owner privately and is
never published.
```

Keep `If in doubt, don't.` as the closing sentence and `:168-169`'s "Content derived only from this
repo's own history…" paragraph unchanged. The citation must stay on one unbroken line past the wrap
width, per `.claude/rules/citation-grammar.md`.

**`docs/cost-levers-considered.md`** *(modify, one edit)* — `:533` only. Replace `is governed by
`docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" and its approval
gate, unexercised here` with `is barred by `docs/private-project-redaction.md` § "Publishing a
tooling measurement"`. Every figure and every other sentence in the file is untouched (row 16).

**`docs/design-decisions/schedulewakeup-misapplied-documented.md`** *(modify, one edit)* — `:14`,
same substitution.

**`docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md`** *(modify, one edit)* —
`:7`, same substitution.

**`claude-skills/skills/tests/test_skills.py`** *(modify, three tests)*:
- `:2959-2986` — rename to `test_tooling_measurement_citation_resolves_to_real_heading`, change
  the expected heading to `"Publishing a tooling measurement"`, update the docstring's quoted
  heading. Keep all five parametrized paths.
- `:2989-3005` — change the expected heading from `"Three standing bars"` to whatever
  `docs/transcript-analysis.md:620` cites after Phase 2 (`"A wider corpus goes to the owner, never
  into a public artifact"`). Keep the docstring's second paragraph — it records why the test
  exists — editing only the clause naming the old target section.
- `:3129-3143` — rename to match, update the pinned heading string to `"Publishing a tooling
  measurement"`.

**Reuse:** `_assert_citation_resolves_to_heading` already does the work in all three; only the
constants and names change. No new helper.

### Phase 2

**`.claude/skills/code-review-claude-config/SKILL.md`** *(modify)* — replace `:9-38` with:

```markdown
P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material. See CLAUDE.md's "Also redact structural fingerprints and
provenance" rule and `docs/private-project-redaction.md` § "Publishing a tooling measurement" for the publication bar. A figure
decomposed by project, account, or engagement is a P1 finding on sight.
So is any of:

- a figure computed over a corpus wider than this repository on one
  account, unless that section's own-history-count exemption covers it
- a figure citing no command, or citing one that cannot refuse a wider
  corpus
- a count of how many accounts or declared config-dir roots exist
- a new figure that lets a reader subtract a previously-published
  pooled figure down to its non-this-repo remainder
- a diff that widens the scope of an auto-publishing script (for
  example, adding a flag to `pr-cost-section.sh` or loosening
  `cost-counts`'s hardcoded single root), or weakens a scope refusal a
  publication instrument depends on

Give a rounded or generalized figure more scrutiny, not less. The six
always-on structural detectors already catch raw pastes, so what reaches
this item is disproportionately content already generalized enough to
clear them.
```

Keep the `§` citation on one unbroken line, as `:12` already does.

**`claude-skills/skills/transcript-analysis/SKILL.md`** *(modify)* — `:98`, replace with:

```markdown
- `cost`'s default redaction makes labels safe, not figures. Under a multi-root scope its totals, rates, and medians are pooled absolutes across accounts, which `docs/private-project-redaction.md` § "Publishing a tooling measurement" keeps out of every public artifact — report them to the owner instead. `--summary` is the one mode scoped for publication; `--share-only` keeps raw absolutes out of the agent's context on a wider read that stays private.
```

**`claude/.claude/scripts/transcript_analysis/cost.py`** *(modify, three strings, no behavior
change)*:
- `:390-392` docstring — retarget the citation and drop "pooled-measurement carve-out"; state the
  durable fact only: no `$`, `Tokens`, or grand-total column, by construction.
- `:580-582` stderr — preserve the asserted prefix `"cost: --share-only refuses --by-project"`
  verbatim, and replace the trailing rationale with one naming the absolute bar: a per-project
  figure is barred outright by the repo-root `CLAUDE.md` rule, retargeting the `§` citation.
- `:962-966` stdout banner — replace `for the approval gate before publishing any figure derived
  from this output` with text stating the operative rule: this output covers a corpus wider than
  one repository on one account, so report it to the owner and publish no figure derived from it.
  Retarget the `§` citation.

**`docs/transcript-analysis.md`** *(modify, prose only)* — `:620` and `:624`. At `:620`, restate
`--share-only`'s purpose as keeping raw pooled absolutes out of the agent's context on a private
wider read, note that nothing it prints is publishable under the bar, and retarget the
`cost-trend` sentence's citation to `§ "A wider corpus goes to the owner, never into a public
artifact"` with its reason updated (a wider corpus is not published at all, share-only or not). At
`:624`, reword the `--by-project` refusal's rationale the same way `cost.py:580-582` is reworded.
Leave every behavioral description — the four tables, the refusal list, the suppression list,
`--summary`'s whole entry at `:619` — byte-unchanged.

### Phase 3 dropped

`docs/case-studies/handoff-hard-block-position.md` needs no edit under this plan. Its HEAD content
(last touched by PR #977 / commit `0f3336ac`) carries no citation to any heading this plan
retargets — the only such citations exist in this session's staged Phase-2-compliance rework, which
Phase 0 discards before any editing begins. `docs/case-studies.md` and
`docs/case-studies/handoff-threshold-impact.md` also need no edit (rows 15, 2).

## Verification

**Test command.** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's
documented scoped command. Expect it to select `claude-skills/skills/tests/test_skills.py` (both
SKILL.md edits plus the three citation tests) and `claude/.claude/scripts/tests/test_transcript_cost.py`
(the `cost.py` string edits). Per `CLAUDE.md`'s Commands section, do not widen to the full suite by
hand; CI runs it on every push.

**Hook-enforced gates.** Two `SKILL.md` files are staged, so `require-skill-review.sh` blocks `git
commit` until its behavioral-equivalence marker is written — and
`.claude/rules/skill-and-agent-self-review.md` requires a fresh allow/deny fixture pair for the
rewritten P1 checklist item specifically, since its match surface changes completely. The
`CLAUDE.md` edit puts `ai-instruction-and-memory-files` in the path (dispatcher-invoked, not
hook-enforced). `/code-review` runs before the commit; `deny-private-project-refs.sh` fires on
commit and covers both the diff and this plan file.

**Citation sweep** — the primary structural check, since the rename is what can break silently:

- `git grep -n 'Publishing a pooled tooling measurement' -- ':!.claude/plans'` must return nothing.
- `git grep -nE 'Three standing bars|The permitted split|Scope . two closed lists|Account and
  machine scope|What it permits' -- ':!.claude/plans'` must return nothing.
- `git grep -n 'approval gate' -- ':!.claude/plans'` must return nothing.
- `git grep -n 'private-project-redaction' -- docs/case-studies/` must return nothing.

`.claude/plans/**` is excluded from all four: preserved records under Axis 3, outside both
`_all_doc_paths()` and every citation-extraction corpus, so a heading name surviving there is inert
rather than a dangling citation.

**Index cleanliness** — before the plan-file commit, `git status --short` shows no staged entry for
`docs/case-studies.md`, `docs/case-studies/handoff-hard-block-position.md`, or
`docs/cost-levers-considered.md` carrying the discarded rework. After the plan lands, `git diff
--stat HEAD -- docs/case-studies/` must show no changes at all.

**Behavior-preservation check on `cost.py`** — `git diff HEAD -- claude/.claude/scripts/transcript_analysis/cost.py`
touches only string literals inside a docstring and two `print()` calls. No flag, exit code,
refusal, early return, or render path changes, and every substring `test_transcript_cost.py`
asserts survives verbatim.

**Cross-file consistency** — after Phase 2, no surviving prose anywhere in the repo describes a
closed list, a standing bar, a permitted split, a pivot budget, a per-side pool-size bar, or an
approval gate as a live rule.

## Out of scope

- **`docs/case-studies/handoff-threshold-impact.md`, entirely.** Row 2. This includes its
  machine-wide Tier 0/Tier 3 tables, its cap-sensitivity table, its crossing-share pair, and the
  cross-account PR count over private-project repos at `:75` that the predecessor plan listed as an
  unresolved gap class. That count is the one item inside the grandfather rule that would be barred
  outright going forward rather than merely non-compliant in form; it stays published, and
  re-opening it is the owner's call under `§ "Remediation"`, not this plan's.
- **Every other already-published figure.** Named once, canonically, in `§ "Remediation"` (row 22)
  rather than re-enumerated here. The new bar is a genuine tightening for future publication —
  several of these studies could not be published again under it — and the grandfather rule is what
  keeps that from becoming a repo-wide rewrite. That is a deliberate trade, not an oversight.
- **Auditing existing figures for composition risk against currently-accumulating single-account
  totals.** Row 23 — the engineer's call, resolved once in `§ "Remediation"`, not re-litigated per
  PR.
- **Reverting `cec0904a` or `cdff9e60`.** Neither. `cec0904a`'s text is directly overwritten by
  Phases 1–2; `cdff9e60` stays per row 3.
- **Removing or reworking `--share-only`.** Row 5. Three citation/rule strings change (row 10); the
  mode, its four tables, its five refusals, and its suppression list do not.
- **Any new script or skill for private reporting.** Row 4 — documented practice only.
- **Mechanical enforcement.** G2. The only enforcement added is the rewritten
  `code-review-claude-config` P1 item, which is reviewer discipline with a checklist behind it, not
  a hook.
- **A per-file grandfather note on any case study.** `§ "Remediation"` is the single canonical home
  for that rule; a per-file note duplicates it and would drift.
- **`.claude/plans/**` restatement sites.** Preserved records under Axis 3 and outside every live
  citation corpus. The predecessor plan at `.claude/plans/case-study-split-redaction.md` is
  superseded in place by this one at the tip; its committed form in history — including its wrong
  G4 citation, corrected on the record at row 17 — is not rewritten.
- **`docs/case-studies/handoff-hard-block-position.md` edits.** Row 14 — needs none under this
  plan; its HEAD content carries no carve-out citations to retarget.
