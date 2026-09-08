# Narrow the provenance-redaction rule to permit pooled behavioral aggregates

## Context

Narrow `claude-config`'s root `CLAUDE.md` provenance-redaction rule so a
pooled, undecomposed behavioral aggregate about this repo's own tooling
(Claude Code tool calls, sessions, agents) can be published, while making
the per-project/per-account/per-engagement prohibition absolute rather than
a factor to weigh.

The current sentence (root `CLAUDE.md`, "Also redact structural
fingerprints and provenance" section) says "a figure drawn from a corpus
mixing private and public sources inherits the private half," with no
carve-out for a pooled aggregate that carries no per-project decomposition.
That sentence is broader than the section's own stated purpose (stopping
content that identifies a specific private project, engagement, or
codebase). Three existing sites in this repo have already applied the
broader rule by withholding legitimate, publishable pooled figures as bare
directional claims with no supporting numbers, making them unverifiable:

- `docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md:7`
- `docs/design-decisions/schedulewakeup-misapplied-documented.md:14`
- `docs/cost-levers-considered.md:501`

This PR does not publish any actual measurement figure — it only changes
what the rule permits. A separate, currently unimplemented sibling branch
(`standardize-background-wait-mechanism`, one plan-only commit today) holds
the actual measurement write-up that will consume this rule change once
merged.

## Approach

Rewrite the provenance paragraph in root `CLAUDE.md` so it carries one
explicit carve-out — a pooled measurement of this repo's own tooling in
use, with no per-project, per-account, or per-engagement dimension, is
publishable — plus an absolute prohibition on decomposing that figure along
any of those three axes. Then update the one downstream site that
operationalizes this rule as a review check,
`.claude/skills/code-review-claude-config/SKILL.md`, so it defers to the
narrowed rule instead of contradicting it. No hook changes, no new test, no
new file.

**Two findings from exploration that reshape the brief.**

First, the brief scopes the edit to the "corpus mixing private and public
sources" sentence. That sentence is not the only thing blocking the pooled
aggregate. The paragraph's *first* sentence — "If the only reason you know
a fact is exposure to private engagement material, publishing it carries
that engagement's fingerprint — whatever the datatype, and whether you
quoted it, computed it, or recalled it" (`CLAUDE.md:152-155`) —
independently prohibits it, since a figure computed from the mixed
transcript corpus is known only through that exposure and the clause names
"computed it" explicitly. A carve-out that only replaces the corpus-mixing
clause leaves the paragraph still prohibiting the target content. The
carve-out has to be positioned to govern the whole paragraph.

Second, `.claude/skills/code-review-claude-config/SKILL.md:9-11` currently
reads "Flag any measurement, example, log excerpt, or command output the
diff adds whose only known source is private engagement material." That is
the project layer `/code-review` loads on every commit in this repo. Left
as written, the very first commit publishing a pooled figure draws a P1
finding, and the reviewer has no basis in its own checklist for the
carve-out. The rule change is inert without this edit, which puts it inside
Axis 1's "required to make the ticket's change correct," not scope creep.

**Chosen scope for the carve-out.** The carve-out is scoped to measurements
of this repo's own tooling in use (Claude Code tool calls, sessions,
agents), not written as a bare decomposition test with no subject-matter
scope. Reason: the paragraph's first sentence (finding one, above) is
broader than the corpus-mixing clause — it covers any fact learned only
through private-engagement exposure, not only tooling stats. A bare
decomposition test with no subject-matter scope would therefore also
legalize publishing pooled facts *about the engagements themselves* (e.g.,
"engagements typically run N weeks") as long as they're not broken down per
account — a channel nothing else in the rule would catch, since that's not
a structural-fingerprint shape either. Scoping the carve-out to tooling
measurements matches the task's own stated motivation and closes that
channel. The sibling structural-fingerprints paragraph (`CLAUDE.md:147-150`,
untouched) already catches project-shape claims through a separate
channel, which is why the decomposition test can carry the rest of the
load *within* that scope.

**Recommended replacement text** for `CLAUDE.md:152-159` (the paragraph
under `### Also redact structural fingerprints and provenance`):

```
Provenance leaks the same way. If the only reason you know a fact is
exposure to private engagement material, publishing it carries that
engagement's fingerprint — whatever the datatype, and whether you
quoted it, computed it, or recalled it. The test is where the
knowledge came from, not what shape it takes.

One carve-out, for measurements of this repo's own tooling in use
(Claude Code tool calls, sessions, agents): a figure pooled across a
corpus that mixes private and public sources is publishable when it
carries no per-project, per-account, or per-engagement dimension.
Cite the command or script that produced it. Decomposing that same
figure by project, account, or engagement is prohibited outright — in
any artifact and any form, a commit message, PR body, decision entry,
or illustrative example included. Not a factor to weigh, not a
borderline case to raise.

Content derived only from this repo's own history, from public
sources, or from synthetic fixtures is not in this class.
```

The final sentence is preserved verbatim per the fixed decision. Splitting
the old semicolon-joined compound into separate sentences also brings the
paragraph into line with root `CLAUDE.md`'s own "Split multi-fact comments"
rule.

**Recommended edit** to `.claude/skills/code-review-claude-config/SKILL.md`,
replacing only the first sentence of P1 and adding one:

```
P1. **Private-corpus provenance** — Flag any measurement, example, log excerpt,
or command output the diff adds whose only known source is private engagement
material, against CLAUDE.md's "Also redact structural fingerprints and
provenance" rule and the one carve-out it names. A figure decomposed by
project, account, or engagement is a P1 finding on sight.
```

The remaining four lines of P1 (rounded-figure scrutiny, no-stated-source
finding) stay verbatim. This defers to `CLAUDE.md` rather than restating
the carve-out, per single-source-of-truth.

**Alternatives set aside.** Adding a numeric or percentage detector to
`deny-private-project-refs.sh` — ruled out by the brief, and correctly: it
would false-positive against public-only percentages already published in
`docs/cost-levers-considered.md`. Adding a pytest pinning the carve-out's
wording, or pinning `CLAUDE.md` and the code-review layer in sync — a
substring assertion over prose is brittle and becomes its own drift source;
for this convention the enforcing mechanism *is* the `/code-review` P1
item, which is the answer to "write the enforcing test in the same PR" for
a rule no test can evaluate. A "concentration disclosure" requirement —
excluded by fixed decision.

### Assumption ledger

**Root:** root `CLAUDE.md`'s provenance rule is broader than its section's
stated purpose (stopping content that identifies a specific private
project, engagement, or codebase), so it suppresses pooled tooling
measurements that identify nothing; narrow it without opening any
per-project channel.

**Givens:**

- **G1.** The `test_legacy_numbers_form_contiguous_range` conflict in
  `claude/.claude/hooks/tests/test_design_decision_files.py` blocks adding
  a post-split decision entry here. Its fix lives on an unlanded sibling
  branch — another change owns it. `[verified:
  standardize-background-wait-mechanism branch, commit 85eac0b7 fixes the
  test_legacy_numbers_form_contiguous_range conflict]` — the engineer's own
  decision, made this session, is the
  separate choice of how to sequence around G1 (ship the rule change now,
  add the decision entry once the sibling branch's fix lands), not the
  underlying fact G1 states.
- **G2.** The three withholding sites are dated records under Axis 3's
  preserved-content exception; amending them needs a task that scopes
  them. `[engineer-verified]`
- **G3.** `deny-private-project-refs.sh` has no numeric/ratio detector, so
  nothing mechanical enforces this paragraph in either direction; the rule
  is reviewer discipline by construction. `[verified: CLAUDE.md:131-135
  lists provenance under "Reviewer discipline only — hook doesn't catch
  these"; README.md:433 states the same tier split]`

**Rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | The paragraph to edit is `CLAUDE.md:152-159`, under the heading at `:145`; the parent section's purpose statement is at `:112-113`. | `[verified: CLAUDE.md read in full this session]` |
| 2 | The paragraph's first sentence independently prohibits the pooled aggregate, so the carve-out must govern the whole paragraph rather than replace one clause. | `[verified: CLAUDE.md:152-155 — "whatever the datatype, and whether you quoted it, computed it, or recalled it"]` |
| 3 | `docs/private-project-redaction.md` does not restate the provenance sentence anywhere; no edit needed there. | `[verified: two greps over the file — provenance/corpus/inherits/derived-only returned zero hits; measurement/figure/aggregate/number/percent/ratio/statistic returned only :12 (hook scan target), :63 (SSH-path detector), :156-161 (blocklist worked example), :218 (hook latency measurement)]` |
| 4 | `.claude/skills/code-review-claude-config/SKILL.md:9-11` operationalizes this rule and would flag the now-permitted figure; it is the only site that does. | `[verified: file read in full; repo-wide grep for provenance/corpus/fingerprint surfaced no other operational restatement]` |
| 5 | `README.md:433` paraphrases the tier but needs no edit — it summarizes the reviewer-discipline tier's role, and `:435` explicitly routes definitional authority to root `CLAUDE.md` ("defines *what* to keep out"). | `[verified: README.md:427-435]` |
| 6 | Editing `.claude/skills/**/SKILL.md` is **not** hook-gated by `require-skill-review.sh` — its pathspecs are `claude-skills/skills/**/SKILL.md` and `plugins/*/skills/**/SKILL.md` only. `/skill-review` is still the documented convention. | `[verified: plugins/skill-management/hooks/require-skill-review.sh:108, :186, :221; convention at .claude/rules/skill-and-agent-self-review.md]` |
| 7 | `select-tests.py` maps root `CLAUDE.md` to the hooks test dir (via a repo-wide `rglob("*.md")` content scan, no by-path reader) and `.claude/skills` to the skills test dir. Expect both domains, not an empty selection. | `[verified: claude/.claude/scripts/select-tests.py:167-171 (ROOT_CLAUDE_MD), :181-184 (ROOT_SKILLS_DIR)]` |
| 8 | Root `CLAUDE.md` is 174 lines pre-edit; the recommended text adds roughly 12. Whether a 200-line budget binds this file (as distinct from `claude/.claude/CLAUDE.md`) is not established here — README.md:450 names the budget only in the global-CLAUDE.md context. | `[unverified]` — resolved by the `/ai-instruction-and-memory-files` run in Verification |
| 9 | No test asserts the content of this `CLAUDE.md` section, so the prose edit breaks nothing mechanically. | `[unverified]` — the `select-tests.py` run in Verification is what settles it |

**Mechanisms:**

- **Prose edit to `CLAUDE.md:152-159`** — `anchors: root`. The rule is
  reviewer discipline with no mechanical enforcer (G3), so editing the rule
  text is the entire available lever; there is no lighter primitive than
  changing the sentence that states the rule.
- **Prose edit to the `/code-review` project layer** — `anchors: row4`.
  Lighter alternatives considered and rejected: leaving it unchanged
  (fails — the layer contradicts the narrowed rule and blocks the content
  on first use); adding the carve-out text to the layer instead of a
  deferral pointer (fails — duplicates the rule across two files, which
  drifts, against single-source-of-truth). The chosen deferral is the
  lightest form that resolves the contradiction.
- **No hook detector, no new test, no new file** — `anchors: root`. The
  over-powered options here are a numeric detector in
  `deny-private-project-refs.sh` and a wording-pinning pytest; both are
  rejected above with reasons, and G3 records that the surrounding rule was
  already reviewer-discipline-only before this change.

## Critical files

Exactly two files change. `git diff --name-only` naming any third file is a
defect.

- **`CLAUDE.md`** (repo root) — replace the paragraph at `:152-159` with
  the recommended text above. Do not touch the heading at `:145`, the
  structural-fingerprints paragraph at `:147-150`, the tier list at
  `:112-143`, the pointer at `:135`, or the `### Secrets, tokens,
  credentials` and `### Enforcement` subsections at `:161-173`. Re-locate
  the paragraph by its opening words "Provenance leaks the same way" rather
  than by line number.
- **`.claude/skills/code-review-claude-config/SKILL.md`** — replace P1's
  first sentence and add the decomposition-on-sight sentence, per the text
  above. Lines 12-16 (rounded-figure scrutiny, no-stated-source finding)
  stay byte-identical. Frontmatter unchanged.

Both edits are one `code-writer` dispatch: the two files are small, the
second's wording depends on the first's, and splitting would force
restating the same rule-design context in both prompts.

**Reuse:** the SKILL.md edit reuses the existing citation form already in
the file — `CLAUDE.md "Also redact structural fingerprints and
provenance"` — rather than introducing a new pointer style.

## Verification

1. `git diff --name-only` — must list exactly `CLAUDE.md` and
   `.claude/skills/code-review-claude-config/SKILL.md`. This is the check
   for the fixed decision that the three dated records stay untouched.
2. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's
   documented scoped command. Per assumption-ledger row 7 this should
   select the hooks and skills test dirs, not an empty set: root
   `CLAUDE.md` is picked up by `test_nudge_transcript_toolkit.py`'s
   repo-wide `rglob("*.md")` content scan, and `.claude/skills` by
   `test_skills.py`'s `_all_skill_md_files()`. Read the actual selection
   from the output rather than assuming this prediction holds; a genuinely
   empty selection means the rule table missed a path, which is a bug to
   report, not a reason to widen by hand.
3. `.venv/bin/ruff check claude/.claude/ claude-skills/` — the documented
   Python lint. No Python changes here, so it is a no-op regression guard.
4. `/ai-instruction-and-memory-files` against the edited `CLAUDE.md` text.
   This is the pass that settles row 8 (length budget) and applies the
   per-line behavior test to the new sentences. Invariants that must
   survive any tightening it proposes: the carve-out reaches the whole
   paragraph, not one clause; the prohibition names all three axes
   (project, account, engagement); the prohibition reads as absolute
   rather than as a factor; the final sentence stays verbatim.
5. `/skill-review` on the `.claude/skills/code-review-claude-config/SKILL.md`
   diff, per `.claude/rules/skill-and-agent-self-review.md`. Row 6
   establishes this is not hook-gated for this path, so it will not block
   the commit — run it regardless, and expect the behavioral-equivalence
   table to cover the replaced P1 sentence.
6. `/code-review`. Note that this diff edits the project layer
   `/code-review` itself loads, so run it after both edits are in the
   working tree — the review then exercises the updated P1 wording against
   a real diff.

## Amendment: length reduction (PR #928 review)

The originally drafted carve-out paragraph added 26 lines to a
`CLAUDE.md` that sits at the hook-enforced 200-line cap with zero
headroom, against row 8's (above) budgeted "roughly 12" — resolving
row 8 from `[unverified]`. The full procedure (scope list, two
pre-publication checks, citation requirement, remediation path) is
reachable only by a session that has already decided to publish a
figure, so it does not need to live in the always-loaded file; the
failure mode of a session not knowing the procedure is over-redaction,
which fails closed. `CLAUDE.md` now keeps only the absolute
prohibition plus a conditional pointer; the rest moved to a new
`docs/private-project-redaction.md` § "Publishing a pooled tooling
measurement" section, with `.claude/skills/code-review-claude-config/SKILL.md`'s
P1 citation redirected to it. This supersedes Critical Files' "exactly
two files change" and Out of scope's "no edit" for
`docs/private-project-redaction.md` — a three-file diff is correct for
this PR.

## Out of scope

- **Publishing any measurement figure.** This PR changes what the rule
  permits and publishes nothing. The write-up that consumes it lives on
  the `standardize-background-wait-mechanism` sibling branch; do not pull
  it forward, and add nothing to `docs/cost-levers-considered.md`.
- **Any per-project, per-account, or per-engagement breakdown, anywhere.**
  Not in the plan file, the commit message, the PR body, a code comment, or
  an illustrative example. The prohibition binds this PR's own artifacts,
  not only future ones.
- **A `docs/design-decisions/` entry.** Deferred per G1 until the sibling
  branch's `test_design_decision_files.py` fix lands on `origin/main`. The
  commit message carries the record instead: that this narrows a rule
  applied three times, naming
  `docs/design-decisions/schedulewakeup-denied-by-bare-tool-name.md:7`,
  `docs/design-decisions/schedulewakeup-misapplied-documented.md:14`, and
  `docs/cost-levers-considered.md:501`. Also cite
  `.claude/plans/private-corpus-provenance-redaction.md` (commit `ddeb74b`,
  PR #687) in the commit message as the mechanism's original design record.
  It authored both the CLAUDE.md paragraph and the
  `code-review-claude-config` P1 checklist item this PR edits. Note in the
  commit message that its two-arm verification experiment (`:95`) concluded
  "inherits the private half" was necessary for a "mixed aggregate" fixture
  case — a conclusion this narrowing changes for the undecomposed case.
  This file is a preserved historical record (Axis 3) and is not edited
  here, distinct from the three sites above, which *applied* the rule to
  withhold a figure rather than designed it.
- **Amending those three sites.** Preserved records per G2.
- **Any numeric, percentage, or ratio detector in
  `deny-private-project-refs.sh`,** or any other hook change. It would
  false-positive against public-only percentages already published in this
  repo.
- **Any other part of the redaction section.** The tracker-ID tier, the
  blocklist tier, the remaining reviewer-discipline bullets, the
  structural-fingerprints paragraph, and the secrets subsection are
  untouched. In particular the structural-fingerprints paragraph must stay
  as-is: the decomposition-only test relies on it to cover the
  project-shape channel.
- **`docs/private-project-redaction.md`** — no edit. Row 3 verified it
  carries no copy of the provenance sentence, so there is nothing to
  convert into a cross-reference.
- **`README.md:427-435`** — no edit. Row 5: it is a one-line tier summary
  that already defers to root `CLAUDE.md` for the definition.
- **`claude-skills/skills/error-mode-analysis/SKILL.md:99,103`** — no edit.
  It restates the structural-fingerprints paragraph and the hook's tier
  coverage, neither of which this PR changes.
- **A test pinning the new rule's wording or the two files' agreement.**
  Rejected above; the enforcing mechanism for this convention is the
  `/code-review` project-layer P1 item.
