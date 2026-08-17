# Private-corpus provenance: reframe the tier-3 redaction gap

## Context

Extend `CLAUDE.md`'s tier-3 redaction rule to name **provenance** — content
drawn from a private-engagement corpus — as a leak class, and enforce it with
a `/code-review` project layer scoped to this repo. The gap is real: a
duration pair read out of a private-engagement corpus reached a public
document and survived two authoring passes before a reviewer caught it. The
existing tier-3 text names only *structural* shapes ("a verbatim RLS policy, a
rare column-naming pattern") and instructs the author to "generalize examples
that would reveal the project via shape alone" — a rounded measurement has no
distinctive shape, so nothing in the rule addressed it.

PR #682 addressed the same gap by defining a **numeric** class (duration,
percentage, dollar figure, count) with a four-clause exemption list, writing
it as a new `CLAUDE.md` subsection and a new item in the *globally stowed*
`/code-review` base checklist. The engineer rejected that shape this session.
Rather than revise it in place, this plan closes #682 and re-derives the rule
from provenance, carrying forward the one part of #682 that survives review:
its measurement of what a mechanical detector would cost.

## Approach

Retitle `CLAUDE.md`'s existing `### Also redact structural fingerprints`
subsection to `### Also redact structural fingerprints and provenance` and
append a short paragraph: if the only reason you know a fact is exposure to
private engagement material, publishing it carries that engagement's
fingerprint whatever the datatype and however you came by it. Extend the
tier-3 bullet list to name the class, sync the two README lines that restate
tier 3, and put the active review-time check in a new
`.claude/skills/code-review-claude-config/SKILL.md` — a repo-local project
layer that `/code-review` Step 0.5 already globs for, not the global base
skill. No new `###` heading is added anywhere.

The reframe is the substance. #682 asked reviewers "is this population large
or diverse enough that no single source dominates it?" — a judgment call about
a figure's statistical character, which #682's own data shows it could not
apply consistently (ledger row 10). This plan asks "is the only reason I know
this fact exposure to private engagement material?" — a question about the
author's own knowledge rather than about the figure. That question is
answerable where the first is not, and it generalizes past numbers: a log
excerpt, a stack trace, a directory listing, and a row count all leak the same
way and for the same reason, so the rule needs no shape taxonomy and no
exemption list.

Two honest limits on that claim, both surfaced by review and recorded as
ledger rows 13–14 rather than argued away. First, the question is only
answerable by an author who thinks to ask it — the motivating incident is
precisely a case where one did not, so this control addresses "asked and
answered wrongly," not "never asked." Second, the CLAUDE.md rule and the
project-layer checklist item are **not two independent controls**: both invoke
the same reasoning process on the same question, usually in the same session.
What the second surface adds is guaranteed execution — `require-code-review.sh`
gates the commit on a marker keyed to the staged diff's hash — not a second
opinion. The design is worth having on that basis and should be described that
way in the PR, not as defense in depth.

**Assumption ledger**

- **Root problem:** content whose only identifying property is *where it came
  from* clears every mechanical gate and is not named by tier-3's
  shape-based text, so neither the author nor the reviewer has a rule to
  check against.
- **Given:** Claude Code's skill-loading contract — that `/code-review`
  resolves `.claude/skills/code-review-*/SKILL.md` from the repo root at
  runtime, and that a `disable-model-invocation: true` skill stays out of the
  always-loaded description listing. This is the harness platform the
  enforcement mechanism runs on top of; no change to this repo can alter what
  it provides.

| # | Assumption | Tag |
|---|---|---|
| 1 | `claude/.claude/CLAUDE.md` — the *stowed global* instructions — contains no redaction, fingerprint, or private-project content; the redaction rule exists only in claude-config's repo-root `CLAUDE.md`. #682's base-skill item therefore ends in a cross-reference to a section absent from every other stow user's config. | [verified: `grep -n "edact\|fingerprint\|private-project" claude/.claude/CLAUDE.md` returned zero matches this session] |
| 2 | `claude/.claude/skills/code-review/SKILL.md` carries no claude-config-specific or redaction content today — #682's item would be the first. | [verified: `grep -n "claude-config\|private-project\|redact\|stow\|public repo"` over that file returned one hit, item 9b at line 86, which uses "prefix length for redaction" as a generic example of a semantic bound and is not repo-specific] |
| 3 | The project-layer mechanism is live and has a working single-instance precedent to model on. | [verified: `claude/.claude/skills/code-review/SKILL.md:25` globs `.claude/skills/code-review-*/SKILL.md` from the repo root; `.claude/skills/plan-review-claude-config/SKILL.md` exists at 13 lines with frontmatter `name` / `description` / `disable-model-invocation: true`] |
| 4 | Adding `.claude/skills/code-review-claude-config/SKILL.md` leaves exactly one file matching Step 0.5's glob, so its "multiple match → list them and stop" error branch is not triggered. | [verified: `ls -d .claude/skills/*/` returns only `plan-review-claude-config/` this session] |
| 5 | No doc or test enumerates existing project-layer instances, so adding one requires no index update. | [verified: `docs/skills.md:172` documents the filename-prefix pattern only and names no instances; grep for `plan-review-claude-config` across `README.md` and `docs/*.md` returned no hits; no file under `claude/.claude/hooks/tests/` references it] |
| 6 | `docs/private-project-redaction.md` needs no edit — it is scoped to the hook's three mechanical scans, and its "Known gaps" section covers a hook-dispatch gap (`gh issue create` / `gh issue comment` are never scanned), not reviewer-discipline classes. | [verified: section headings and the full "Known gaps" body read this session] |
| 7 | `README.md` restates tier 3 in two places and will drift from `CLAUDE.md` unless both are updated. | [verified: `README.md:74` (feature-list line) and `README.md:413` (numbered Private-project-redaction list, item 3), both located by `grep -n` this session. An earlier revision of this row cited 71 and 411 — those were hunk-start line numbers read off #682's diff headers, not the changed lines, and the tag was corrected once a drift check surfaced the mismatch] |
| 8 | Provenance is invisible to any text scanner. Whether a figure came from a private corpus is not recoverable from the committed bytes, so no regex — however tuned, however language-aware — can decide this class. | [verified: categorical. The discriminator is a fact about the authoring process, not a property of the text; this is what disqualifies a hook, independent of any measured hit rate] |
| 9 | #682's measured candidate-detector rates corroborate rejecting a hook: a percentage pattern hit 150 times with 65 (~43.3%) hand-classified risky, a dollar pattern hit 22 times with 4 risky and a further 5 that were shell/SQL positional parameters rather than money, and a time pattern hit 61 times with 5 (~8.2%) risky. This plan does **not** rest on these — row 8 disqualifies a hook categorically. | [verified: `origin/numeric-fingerprint-redaction-check:.claude/plans/numeric-fingerprint-redaction-check.md` ledger rows 1–3, read this session. Inherited measurement over `docs/` and `claude/.claude/skills/` at that branch's tree — NOT re-run against the current tree, so the counts are indicative, not current] |
| 10 | The numeric class could not be applied consistently even by its own author over a hand-classified population: #682's ledger row 5a reclassified two of the hits it had named as risky back to safe on a closer read ("precision and per-instance framing alone over-classified them"), and row 5b records that "count" — one of the four shapes the rule names — was never measured at all. | [verified: same plan file, rows 5a and 5b, read this session] |
| 11 | `ciso-reviewer` would not fire on the commits that introduce this class. Its TRIGGER list is auth / secrets / tokens / access-control / privileged-function / input-validation shaped; a prose edit under `docs/` adding a measurement matches none of it. | [verified: `ciso-reviewer`'s description in this session's agent roster, read directly] |
| 12 | The engineer chose the vehicle (close #682, fresh branch, salvage the measurement work) and the enforcement surface (CLAUDE.md clause **plus** a project-layer check, not the clause alone). | [engineer-verified: both answers given this session] |
| 13 | **Residual risk, accepted.** The rule is answerable only by an author who thinks to ask it. The motivating incident is a case where the author did not recognize or recall the provenance across two authoring passes, so this control covers "asked and answered wrongly," not "never asked." No wording change closes this; only a different reasoning path or data source would. | [verified: the incident as described in #682's plan Context section, read this session — it records the figure surviving two passes before a third-pass reviewer caught it] |
| 14 | **The two surfaces are one control invoked twice, not layered defenses.** Both the CLAUDE.md rule and the project-layer item apply the same judgment to the same question, typically in the same session. The gain is guaranteed execution, not independent verification: `require-code-review.sh` gates `git commit` on a marker keyed to a hash of the staged diff, so the second pass cannot be silently skipped. | [verified: `ciso-reviewer` pass this session confirmed the marker gate's shape; the non-independence follows from both surfaces routing to the same reasoning process] |
| 15 | `CLAUDE.md` is under a mechanical 200-line cap enforced by `check-claude-md-length.sh`, and repo-root `CLAUDE.md` was 172 lines before this change and is 182 after, leaving 18 lines of headroom. #682's 13-line block would have consumed roughly half the remaining headroom for a single rule. | [verified: `check-claude-md-length.sh:15,69` (default limit 200); `wc -l CLAUDE.md` → 172 pre-edit and 182 post-edit, both run this session. The post-edit figure is 182 rather than the ~179 first estimated because the tier-3 bullet rewrapped] |
| 16 | The six always-on structural detectors already catch the *unlaundered* subset of this class — a raw paste from private material often still carries a home-rooted path or internal hostname. The instances that reach tier 3 are therefore disproportionately the *laundered* ones, already generalized enough to clear the hook. Rounding and generalization are what a near-miss leak looks like after partial redaction, so they warrant more reviewer scrutiny, not less. | [verified: `ciso-reviewer` pass this session; the detector list is in `CLAUDE.md`'s tier-1 paragraph, read this session] |
| 17 | **The unstated-source clause does not create an over-flagging problem, because `/code-review` is diff-scoped.** This repo's docs do carry many unsourced empirical figures — measured at commit `d848750`, `docs/transcript-analysis.md` has 60 percentage figures and `docs/cost-levers-considered.md` 46, and sampled lines in the latter (`44% of turns are subagent turns`, `Opus measured at 15.7% of spend`, `71.2% of tool-result bytes`) state no source inline. None of them enter a diff unless a change edits those lines, and base checklist item 14 routes anything noticed in unchanged content to a separate informational section rather than a finding. `P1` therefore fires only on figures a change *adds*, where requesting a source costs one citation. | [verified: both counts re-derived at `d848750` and again at `7804120` via `grep -oE "[0-9]+(\.[0-9]+)?%" <file> \| wc -l` — identical at both, since neither file was touched between them. Anchored to a fixed commit rather than to "the merge base", which moves every time the default branch advances; `grep -c` is the wrong instrument here and disagrees, counting matching lines rather than matches. The sampled lines were confirmed present, this session; `code-review/SKILL.md:116` item 14 read this session. Two corrections this row went through, both worth keeping visible: a reviewer's offered counts (61 and 22) were wrong — 22 is #682's dollar-pattern corpus total from row 9, not a per-file count — and the `cost-levers-considered.md` count was 42 at the original base `98a3615`, changing to 46 when #678 landed mid-review. Counts are anchored to a commit here precisely because they move; the row's argument does not depend on the exact figure] |
| 18 | **Accepted limit: a stated source is taken on attestation, not verified.** Once an author supplies a source, `P1` has no instruction to check that the figure actually derives from it. This is a distinct failure from row 13's (which is about failing to *recognize* provenance); this one is failing to *verify* an asserted provenance. Recorded rather than closed: requiring in-repo grep-verifiability would wrongly reject legitimate vendor-documentation and public-source citations. | [verified: `ciso-reviewer` re-review this session raised it and recommended naming it rather than changing the rule] |
| 19 | `P1` needs no wording change to make a finding blocking. `/code-review`'s "Finding disposition" section defaults every finding to ADDRESS and explicitly invalidates "non-blocking" and "advisory" as DEFER rationales, and `require-code-review.sh` writes its marker only when no unresolved critical findings remain. A project layer restating blocking semantics would duplicate that contract, which the base skill's "Project-layer scope" note forbids overriding. | [verified: `code-review/SKILL.md:286,304,306` and the Project-layer scope note at :204, all read this session] |
| 20 | **`.claude/rules/skill-and-agent-self-review.md`'s `paths` globs did not cover repo-root `.claude/skills/**/SKILL.md`.** All four entries required a leading `claude/` (the stow package) or `plugins/`, so neither this plan's new project layer nor the pre-existing `plan-review-claude-config` layer loaded that rule when opened — meaning a project-layer SKILL.md could be edited without the repo's own skill-self-review discipline in context. Adding the repo-root glob is required for this change to be covered by existing discipline, not an opportunistic edit. | [verified: the file's four `paths` entries read this session; no test under `claude/.claude/hooks/tests/` pins the glob list, and `docs/rules-references.md` / `README.md:239` describe the mechanism generically without enumerating globs] |
| 21 | **A wording change to `P1` silently invalidates its discrimination evidence, and this is demonstrated rather than hypothetical.** `P1` was reworded three times during review; the second change (compressing it to defer to CLAUDE.md) removed the exemption list that had been locally scoping the unstated-source clause, and a re-run showed `P1` then over-flagged a benign local measurement it had previously passed. The re-verification requirement therefore lives in `.claude/rules/skill-and-agent-self-review.md`, which loads when a SKILL.md is opened — the editor's moment — rather than inside `P1`, whose reader is a reviewer applying it. | [verified: the three-arm fixture runs this session, before and after the scope fix] |
| 22 | **The CLAUDE.md cross-reference is load-bearing, not decorative.** Run against a fixture holding a recalled figure, a mixed private+public aggregate, an unsourced figure, and a repo-sourced figure: the arm reading `P1` **plus** the referenced section decided all four correctly and named the reference as necessary for two — CLAUDE.md's "whether you quoted it, computed it, or recalled it" defeats an author's "I did not copy this" defense, and "inherits the private half" resolves the mixed aggregate, which `P1`'s "only known source is private" wording cannot. The arm reading `P1` alone was UNSURE on both of those and, before the scope fix, wrongly flagged the repo-sourced figure. After the fix it passes that case and matches on all four, with the mixed-aggregate case still resolved only by the reference — as designed. | [verified: two-arm experiment plus a post-fix re-run this session; fixtures held in the scratchpad, never committed] |

**Mechanism justification**

- **Retitle-and-extend the existing `### Also redact structural fingerprints` subsection** (a three-sentence paragraph, not a new heading) — anchors: root. Lighter primitives weighed: *(a)* no doc change, relying on the existing text to cover provenance — fails because that text is explicitly about shape ("Generalize examples that would reveal the project via shape alone"), and a rounded measurement has no distinctive shape; *(b)* a new sibling subsection, #682's choice — fails on altitude and DRY: the tier-3 bucket and the "identifiers aren't the only leak" framing already exist, so a second heading restates the frame to introduce one idea. #682's version also spent three of its twelve lines narrating why no hook was built, which is commit-message content in a durable doc.
- **Repo-local project layer at `.claude/skills/code-review-claude-config/SKILL.md`** — anchors: row 1, row 2, row 3, row 12. Lighter primitives weighed: *(a)* the global base checklist, #682's choice — fails per rows 1–2, shipping repo-specific content plus a dangling cross-reference to every stow user; *(b)* a `ciso-reviewer` instruction — fails per row 11, wrong trigger shape, so it buys no coverage on the commits that matter. The genuinely lighter option — the CLAUDE.md clause with no active check at all — was named to the engineer and declined (row 12), so the layer is an engineer-directed choice rather than an unexamined default.
- **No hook** — anchors: root, row 8, row 9. This is the *lighter* primitive being declined in the other direction: a hook is the heavier, always-on, no-judgment mechanism, and row 8 rules it out categorically rather than on tuning.
- **Closing #682 rather than revising it** — anchors: row 12. Engineer-directed. Its measurement survives as ledger row 9 here; nothing else from its diff is carried forward.

## Critical files

- **`CLAUDE.md`** — three edits inside the existing "Redact private-project-identifying content" section. **No new `###` heading:** the existing subsection is retitled and extended instead, so the tier-3 bucket keeps one scannable home.

  1. Tier-3 bullet, replacing the trailing `structural fingerprints (see below)`:

     > structural fingerprints and private-corpus provenance (see below)

  2. Retitle the subsection heading — required, not cosmetic. The appended text's own point is that the test is *not* about shape, so leaving it under a heading promising "structural fingerprints" hides a non-structural class behind a structural label, and `CLAUDE.md` headings are scan targets:

     > `### Also redact structural fingerprints` → `### Also redact structural fingerprints and provenance`

  3. Appended after the subsection's existing closing sentence:

     > Provenance leaks the same way. If the only reason you know a fact is
     > exposure to private engagement material, publishing it carries that
     > engagement's fingerprint — whatever the datatype, and whether you quoted
     > it, computed it, or recalled it. The test is where the knowledge came
     > from, not what shape it takes; a figure drawn from a corpus mixing
     > private and public sources inherits the private half. Content derived
     > only from this repo's own history, from public sources, or from
     > synthetic fixtures is not in this class.

  The "quoted it, computed it, or recalled it" clause is load-bearing, not padding: it closes the coverage gap review found in an earlier draft, where "read out of a private corpus" described literal transcription and an author could honestly answer *no* for a figure they derived from private inputs, reconstructed from memory, or aggregated over a mixed corpus (ledger row 13's neighbours). No PR-number or commit-log narration, no why-no-hook rationale (that belongs in the commit message), and the boundary is stated positively rather than as a carve-out list.

- **`.claude/skills/code-review-claude-config/SKILL.md`** *(new)* — repo-local project layer. **Reuse:** frontmatter fields and section shape from `.claude/skills/plan-review-claude-config/SKILL.md`. Drafted in full:

  > ```
  > ---
  > name: code-review-claude-config
  > description: Project-specific layer for /code-review, loaded only when reviewing changes in the claude-config repo itself.
  > disable-model-invocation: true
  > ---
  >
  > ## Base checklist addition
  >
  > P1. **Private-corpus provenance** — Flag any measurement, example, log
  > excerpt, or command output the diff adds whose only known source is private
  > engagement material. See CLAUDE.md "Also redact structural fingerprints and
  > provenance" for the rule and its exemptions. Treat a rounded or generalized
  > figure with more scrutiny, not less: the six always-on structural detectors
  > already catch raw pastes, so what reaches this item is disproportionately
  > content already generalized enough to clear them. An exemption can only be
  > applied to a figure whose origin is stated, so an empirical figure naming no
  > checkable source — a command, a file, a citation — is itself the finding:
  > ask for the source rather than guessing at it.
  > ```

  **`P1` defers the base rule rather than restating it.** An earlier draft restated CLAUDE.md's provenance rule inline (datatype independence, "quoted, computed, or recalled", the mixed-corpus case, the exemptions). That is knowledge duplication against this repo's own single-source-of-truth rule — the same defect this plan faults #682 for — so `P1` now carries only what is specific to review time and points at CLAUDE.md for the rule. Deferring is safe because repo-root `CLAUDE.md` is always loaded in this repo, so the reference resolves to content already in context rather than a file the reviewer must fetch. Ledger row 22 records the experiment confirming the reference is load-bearing rather than decorative.

  **The unstated-source clause came out of verification step 5.** The first fixture pair separated an attributed private-corpus figure from an attributed repo-history figure cleanly — but both *stated* their provenance, which is the easy case. With no stated source, the provenance question is answerable only by the author, leaving a reviewer to guess: the same undecidability this plan faults #682 for. `CLAUDE.md`'s "Ground every choice" rule does not close it — its quantitative-claims bullet is scoped to "ticket, PR, and handoff prose," not to `docs/` content. The clause is anchored on *checkability* ("an exemption can only be applied to a figure whose origin is stated") rather than left unscoped, for the reason in row 22. [verified: that bullet's scope read in `CLAUDE.md` this session]

  **Item numbered `P1`, not `13a`.** Review suggested `13a` on the base checklist's own lettered-suffix convention. Declined: a project layer that claims an identifier in the base skill's namespace collides silently if the stowed base skill later adds a real `13a` — and the base skill is a separately-evolving artifact shipped to every stow user, so that is not hypothetical. A `P`-prefixed namespace has no collision surface and marks the item's origin at the citation site. Base `/code-review`'s output format requires findings cite an item "by number and name"; `P1` satisfies that.

  Do **not** duplicate `plan-review-claude-config`'s stow-audience paragraph here — that is plan-review's lane, and `/code-review`'s Change-type table already routes skill and agent files.

- **`README.md`** — sync the two lines that restate tier 3 (row 7):

  1. Line 74, feature-list summary: `reviewer discipline for structural fingerprints` → `reviewer discipline for structural fingerprints and private-corpus provenance`.
  2. Line 413, numbered item 3, replacing the sentence:

     > 3. **Reviewer discipline** — what the hook can't catch: structural fingerprints (a verbatim RLS policy, a rare column-naming pattern) and private-corpus provenance (a fact known only through exposure to private engagement material, whether quoted, computed, or recalled) are a review responsibility, not a mechanical one.

  Both README lines are summaries that must stay consistent with the retitled `CLAUDE.md` subsection, which is the single source of truth for the rule; neither restates the rule's full text.

- **`.claude/rules/skill-and-agent-self-review.md`** — two edits, both driven by review findings (rows 20–21):
  1. Add `".claude/skills/**/SKILL.md"` to `paths`. The existing four globs all require a leading `claude/` or `plugins/`, so repo-root project layers — this plan's new one and the pre-existing `plan-review-claude-config` — were outside the rule's coverage. Required for this change to fall under the repo's own skill-self-review discipline.
  2. Add one line requiring that a wording change to a checklist item whose behavior was verified against fixtures be re-verified against fresh fixtures, because the prior evidence does not survive the edit. This is the forcing function `ciso-reviewer` asked for. It goes here rather than in `P1` on altitude grounds: this rule file loads when a SKILL.md is *opened for editing*, which is the moment the instruction applies, whereas `P1`'s reader is a reviewer applying the item to a diff and would pay for editor-facing prose on every review.

- **`.claude/plans/private-corpus-provenance-redaction.md`** — this file, committed to the branch per `branch-management`.

- **Not edited:** `claude/.claude/skills/code-review/SKILL.md` (row 2 — the global base skill stays clean), `claude/.claude/CLAUDE.md` (row 1), `docs/private-project-redaction.md` (row 6), `docs/skills.md` (row 5).

- **Outward action, after the PR is open:** `gh pr close 682` with a comment naming the reframe and linking the replacement PR, so the measurement work stays discoverable from the closed PR.

## Verification

1. **Glob resolves to one layer** — `ls .claude/skills/code-review-*/SKILL.md` returns exactly one path, confirming row 4 still holds after the addition and that Step 0.5's multiple-match error branch stays untriggered.
2. **The layer does not ship to stow users** — confirm the new file's path is under repo-root `.claude/`, not under the `claude/` stow package, and that `~/.claude/skills/` gains nothing. This is the defect being corrected; assert it rather than assume it.
3. **Frontmatter parity** — the new layer's frontmatter carries the same three fields as `plan-review-claude-config/SKILL.md`, including `disable-model-invocation: true` so its description stays out of the always-loaded skill listing.
4. **End-to-end, and self-applying** — run `/code-review` on this branch's own diff. Step 0.5 must report loading `code-review-claude-config`. This proves the layer *loads and executes*; it does not prove the item *discriminates*, which is step 5's job.
5. **Allow/deny fixtures — the item's actual detection behavior.** Invocation is not detection, and this repo's own base-checklist item 13 holds that an untested control is indistinguishable from an absent one. Apply `P1` to three fabricated scratch diffs, all held in the scratchpad and never committed — verifying a private-content rule must not itself introduce private-shaped content into a public repo:
   - **Attributed pair** — a rounded measurement with no structural fingerprint, attributed in the diff's own prose to a private engagement's corpus, against a figure of identical shape attributed to this repo's own git history. Must flag the first, pass the second.
   - **Unattributed set** — a vendor-documented constant, a value sourced to a repo config file, and a bare measurement naming no source. Must pass the first two and flag the third.
   - **Hard cases** — a figure computed from recalled private material (testing that an author's "I did not copy this" is not an exemption), a mixed private-plus-public aggregate, an explicitly recalled figure, and a repo-sourced measurement naming its command. Run this one **twice**: once with `P1` plus the CLAUDE.md section it references, once with `P1` alone. The two arms establish whether the cross-reference is load-bearing, and the `P1`-alone arm is what catches an over-flagging regression when the item is compressed (row 22).

   If any arm's verdicts diverge from the rule's intent, the wording is wrong and goes back to drafting before the PR opens.
6. **CLAUDE.md stays under the length gate** — `wc -l CLAUDE.md` after the edit must be ≤200 per `check-claude-md-length.sh` (row 15: 172 before, 182 after).
7. **Skill and instruction-file review** — `skill-management:skill-review` against the new `SKILL.md`; `ai-instruction-and-memory-files` against the `CLAUDE.md` diff. Both are required by this repo's per-file-type dispatch rules, not optional.
8. **Cross-reference resolves after the retitle** — the drafted `SKILL.md` quotes the heading `"Also redact structural fingerprints and provenance"` verbatim. Grep `CLAUDE.md` for that exact string after the retitle lands; a prose heading is the only available anchor, so the reference breaks silently if the two drift.
9. **Test suite** — `../../../.venv/bin/pytest claude/.claude/` from the worktree. No executable code changes here, so any failure should be triaged as pre-existing; per `CLAUDE.md`, prove it on a merge-base worktree before treating it as in scope rather than assuming.
10. **Redaction hook passes its own subject matter** — the commit and PR body describe private-corpus redaction and must themselves clear `deny-private-project-refs.sh`. Use placeholder shapes in every example.
11. **`gh pr view 682 --json state`** reports `CLOSED` after the close step.

## Out of scope

- **The numeric-shape taxonomy and its exemption list.** Deliberately dropped, not deferred — rows 8 and 10 are the reasons.
- **A mechanical detector for this class.** Ruled out categorically by row 8; no seventh structural detector is proposed and none should be revisited on the strength of a better regex.
- **The three-tier model and the six always-on structural detectors.** Inside this repo's reach — `deny-private-project-refs.sh` is our own artifact and could be restructured. Declined deliberately: this plan closes a tier-3 gap, and the tiers themselves are working as designed for the classes they do cover. Nothing here is evidence against the model.
- **The blocklist's fail-open behavior when `~/.claude/private-projects.md` is absent.** Also inside reach. Declined because it is a tier-2 mechanical concern and this plan is scoped to tier 3; the two do not interact. Flagged rather than dropped — it is a real property of the current design worth a separate decision.
- **The `gh issue create` / `gh issue comment` hook-dispatch gap** (row 6). Real, documented in `docs/private-project-redaction.md`'s "Known gaps", and separate work.
- **Re-running #682's detector measurement against the current tree.** Row 9 is tagged as inherited precisely so this is not silently assumed current; nothing in the design depends on the counts.
- **Removing the `numeric-fingerprint-redaction-check` worktree.** Local machine state, and this repo carries many retained locked worktrees.
- **Commits made outside a Claude Code session.** A direct edit through the GitHub web UI, or a raw `git commit` outside the harness, bypasses `require-code-review.sh`'s marker gate and therefore `P1` entirely, leaving only the tier-1/2 pattern hook — which row 8 says cannot see this class. A pre-existing property of the whole hook architecture, not introduced here, and not closable from inside this plan.
- **A preventive control at the account-isolation boundary.** Review noted this design is purely detective: it catches at the review checkpoint rather than reducing the odds that private-corpus content enters a `claude-config` session's context at all. Per-account `CLAUDE_CONFIG_DIR` separation already provides part of that upstream, but it cannot stop content crossing via the author. A genuinely independent layer would need a different reasoning path or data source (row 14); designing one is separate work.
- **Enforcing the fixture re-verification requirement.** Nothing records that a re-run actually happened before a `P1` wording change was staged: `.claude/rules/skill-and-agent-self-review.md` is a prose reminder loaded at edit time, not a checked precondition like `require-code-review.sh`'s hash-keyed commit gate. This is a property of the whole path-scoped rules mechanism — the file's pre-existing `/skill-review` requirement is equally unenforced — not something this change introduces, and a hook-backed gate on a judgment-based prose control is the compounding-defensive-layer shape `CLAUDE.md` warns against. Named here rather than closed.
- **A correction to `skill-management`'s architecture doc.** Its description of project-layer loading as Glob + Skill-tool does not match `/code-review` Step 0.5's actual Glob + Read mechanism. Real, but it lives in a marketplace plugin outside this repo's tree.
