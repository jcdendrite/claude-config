# Reinstate a narrow owner-permission exception for wider-than-one-account measurements

## Context

Reinstate a narrow, owner-approval-gated exception to
`docs/private-project-redaction.md`'s "A wider corpus goes to the
owner, never into a public artifact" bar, so a tool whose own design
structurally refuses publishing a wider-than-one-account figure by
default can still have that figure published when the owner explicitly
authorizes it.

PR #1011 (commit `e4857828`) collapsed a 382-line pooled-measurement
carve-out down to a strict single-account bar with no exception:
"Publish nothing computed from such a read... in any artifact... full
stop." That collapse itself is correct and stays as-is — the old
carve-out's "What it permits" scope lists, three standing bars,
permitted-split conditions, and time-series ban apparatus are not being
reintroduced. But the collapse went one step further than intended: it
removed even a narrow, explicit, owner-approved escape hatch for a tool
that already asks the owner before publishing anything.

The engineer's own words this session: "I want to keep it as an
internal reporting only tool by default, with the liberty to give
permission to publish." Read as: wider-than-one-account output from
such a tool stays internal and unpublished by default, but the owner
retains the ability to explicitly authorize publishing one specific
figure, case by case, cited in the artifact that publishes it.

The concrete motivating case is `review-round-cost --pooled`
(implemented on the separate, not-yet-merged `review-round-cost-pooled`
branch, PR #1009). Its `_POOLED_PUBLICATION_POINTER` text already
implements exactly this shape today: propose the figure, the exact
command, and the destination artifact to the owner; cite the owner's
approval in that artifact; nothing in the tool itself checks that for
you. That branch currently cites the now-deleted "Publishing a pooled
tooling measurement" heading — retargeting that citation to whatever
heading this plan creates is explicitly out of scope here; it happens
in a separate follow-up once this PR merges and PR #1009 rebases onto
the new `main` tip.

This ships as its own dedicated PR, not folded into PR #1009 — engineer-confirmed this session, matching the precedent PR #1011 set of
policy-only PRs getting dedicated `/plan-it` → `/plan-review` treatment.

## Approach

Add one new `###` subsection to `docs/private-project-redaction.md`, immediately after "A wider corpus goes to the owner, never into a public artifact" and before "Own-history counts were never inside this class." It states that the owner can release the wider-corpus bar for **one specific figure at a time**, names the three things a proposal must carry (the figure, the exact command, the destination artifact), requires the authorization to be cited beside the published figure, and closes with a three-item list of what an authorization does **not** release. Two one-clause edits follow it: the repo-root `CLAUDE.md` line that currently asserts "never published" is made to defer instead, and the `/code-review` project-layer checklist's wider-corpus sub-bullet gains the authorization citation as a second `unless`. No Python, no hook, no test, no CHANGELOG entry.

### Resolving design question 1 — scope of the exception

**Neither a named-tool carve-out nor a structural-tool-shape test. Key the exception on the act of authorization: the owner authorizes one figure, from one command, into one artifact.** This lands at the same practical destination the session leaned toward: general wording, no `review-round-cost --pooled` by name, and a future tool qualifying with no further doc PR. It replaces the proposed hinge, though, which does not survive contact with the repo.

The proposed structural test was "a command that refuses a narrower scope by construction, requires explicit owner sign-off to go wider, and cites that sign-off in the publishing artifact." `cost --share-only` satisfies the first clause literally: it refuses `--projects` (non-default glob), `--by-project`, `--no-redact`, `--summary`, and `--top`, all at the CLI boundary with exit 2, and prints its own "report any figure derived from it to the owner and publish none" banner (`claude/.claude/scripts/transcript_analysis/cost.py:566-600`, `:961-968`). So a shape-keyed exception would make `--share-only` output eligible for publication — contradicting `docs/transcript-analysis.md:620` ("Nothing it prints is publishable under the bar") and the standing P1 at `.claude/skills/code-review-claude-config/SKILL.md:18-20` ("`--share-only` output in any artifact, regardless of dimensionality"). That is not a wording risk to tighten; it is the test admitting the one output the repo twice says is never publishable.

A shape key is also the wrong kind of control. What makes the single-account bar trustworthy is that a reader can re-run a command that *cannot* be pointed wider — the refusal is the evidence. A wider read has no such refusal available by definition, so nothing about the producing tool's design does safety work here. Dressing the exception in tool-shape language invites a reviewer to credit the tool for a guarantee only the owner's judgment supplies. Keying on the act keeps the control where it actually sits.

The named-tool alternative was set aside for the reason the session gave — a doc PR per tool, and a flag name aging into a policy doc — plus one more: a tool name in this doc is a category error, since every other rule here is about corpora and figures.

### Resolving design question 2 — durability of approval

**Require the authorization to be cited in the publishing artifact.** Three reasons, and one counter-precedent worth naming rather than ignoring.

First, an uncited permission is indistinguishable from no permission. The `/code-review` checklist flags "a figure computed over a corpus wider than this repository on one account" as P1 on sight; a reviewer reading the diff has no way to tell an authorized figure from a violation unless the artifact says so. Without a trace the exception is a hole, not a gate.

Second, it matches the shape already implemented — `_POOLED_PUBLICATION_POINTER` already instructs "cite the owner's approval in that artifact." Recording the shipped shape beats inventing a second one the tool would then contradict.

Third, the neighboring "This repository, one account" subsection already requires citing the scoping command beside the figure. Same register, one artifact-level trace per published figure.

The counter-precedent: "New figures against the grandfathered set" ends "an uncited in-session answer settles it." That check is different in kind. Its subject figure is already *inside* the bar (own-history, or this-repo/one-account) and the ask is only about composition, so the usual outcome is "publish as planned" and the artifact looks compliant on its face either way. Here the figure is outside the bar by default, so the citation is the only thing that makes it readable as compliant at all. Different evidentiary need, not an inconsistency.

The citation names the authorization and the command; it names nothing about the corpus. An over-detailed citation would leak the provenance the bar exists to keep out.

### Prescribed prose

New subsection, inserted between current lines 167 and 169 of `docs/private-project-redaction.md`. Wrap to the file's existing ~68-column width. The implementer may tighten wording; the four-item exclusion list and the one-figure/one-artifact sentence are invariants, not stylistic choices.

```markdown
### The owner can authorize one figure, case by case

The bar above holds by default: a wider read stays with the owner
and publishes nothing. The owner can release it for one specific
figure. Propose the figure, the exact command that produced it, and
the artifact it would appear in. Publish only after an explicit,
in-session yes, and cite that authorization beside the figure,
naming what was proposed and the timestamp of the yes. A bare claim
that approval occurred, with nothing to check it against, is not a
citation. A recalled yes from another transcript or artifact does
not count — only a live answer in the current session does, the
same standard "New figures against the grandfathered set" below
sets. The command is cited so a reader can re-run the measurement.
Unlike the single-account case above, authorization guarantees
nothing about scope by itself; it is the whole control. That is why
it is granted per figure and left as a citable trace. The mechanical
backstop is this repository's own human-only merge gate: the owner
reviews every PR before it merges and can catch a citation for a yes
that was never given.

An authorization covers the figure, the command, and the artifact it
named. A second figure, the same figure in a second artifact, or a
re-run over a grown corpus is a fresh ask. Cite each authorized
figure on its own — one citation spanning several figures does not
establish that each was individually proposed and approved. Before
citing an authorized figure, check whether it composes with an
already-published rate or count — from the grandfathered set below,
or from an earlier authorization under this section — to reconstruct
a calendar-time series or narrow a boundary. If so, name that
composition in the proposal. "New figures against the grandfathered
set" below is the mechanical half of that check; it does not cover
composition against a prior authorization on its own.

An authorization releases the corpus-scope bar and nothing else. Four
things stay barred alongside it:

- A figure carrying a per-project, per-account, or per-engagement
  dimension. The repo-root `CLAUDE.md` bars that absolutely. It is a
  dimension question rather than a scope question, so this section
  cannot relax it.
- A count of accounts or declared config-dir roots — see "Account
  cardinality" below.
- `--share-only` output. It keeps a wider read's absolutes out of the
  agent's context; it is not a publication instrument.
- A figure with its own calendar-time axis (per-week, per-month, or a
  two-point before/after split) drawn from a wider corpus — barred
  even as a single authorized figure, since no split mechanism
  exists here to sanction one.
```

Also broaden "New figures against the grandfathered set"'s opening
scope sentence so its own text actually covers the figure type the
subsection above points at it for. Currently: "A new figure —
own-history or drawn from this repository, one account — can still
newly disclose something private..." Change the em-dash clause to "A
new figure — own-history, drawn from this repository on one account,
or an owner-authorized wider-corpus figure —". No other change to that
subsection.

Same file, one phrase, in the next subsection: "Two classes fall outside **the bar above**" becomes "Two classes fall outside **the wider-corpus bar**." Inserting a section between that sentence and its referent leaves a reader resolving "above" against the nearest preceding heading; naming the bar removes the question. This is descriptive prose about how the rule works, not a preserved record, so it is inside the in-file scope license.

`CLAUDE.md` lines 164-167 — extend the citation's claimed coverage and replace the conclusion with a deferral:

```
account — `docs/private-project-redaction.md` § "Publishing a tooling measurement" states the scope bar, the commands that enforce it, the
two exemptions, and the limits on the owner's case-by-case
authorization for a wider figure. Anything wider goes to the owner
privately; it publishes only with that authorization cited, and only
for the one figure it covers. If in doubt, don't.
```

The citation now names the authorization exception's limits, not only
"the two exemptions" — a ciso-reviewer finding from plan review. An
agent reasoning from CLAUDE.md alone, holding a genuine yes for one
figure, had no textual signal that the account-cardinality and
`--share-only` bars sit outside the exception's reach. Neither bar
appeared in CLAUDE.md, and the old citation didn't claim to point at
them either.

"the two exemptions" stays at two. The doc's own vocabulary distinguishes the words: "Two classes fall **outside** the bar above rather than being **exceptions** to it." The new subsection is an exception by construction — the bar applies and the owner releases it once — so it is not a third exemption and must not be counted as one.

`.claude/skills/code-review-claude-config/SKILL.md` — extend the dimension sub-bullet's `unless` clause, extend its "cannot refuse a wider corpus" sibling the same way, and add a new standalone bullet for the calendar-time-axis bar:

```
- a figure computed over a corpus wider than this repository on one
  account, unless that section's own-history-count exemption covers it
  or the artifact cites the owner's timestamped authorization for that exact
  figure
- `--share-only` output in any artifact, regardless of dimensionality —
  it exists only to keep a wider corpus's raw absolutes out of the
  agent's own context, never to publish from
- a figure citing no command, or citing one that cannot refuse a wider
  corpus, unless the artifact cites the owner's timestamped authorization for
  that exact figure
- a figure with its own calendar-time axis drawn from a corpus wider
  than this repository on one account — barred regardless of
  authorization
```

The dimension clause reads as exhaustive today, so a reviewer trusting the checklist would P1 a validly authorized figure — the same gap applies to the "cannot refuse a wider corpus" sibling bullet, since an authorized figure's command can never itself refuse a wider corpus by construction. Both escapes are diff-visible, which is what the checklist wants. Both `unless` clauses require a *timestamped* authorization, not a bare claim, matching the doc's own citation-content requirement. The new calendar-time-axis bullet is unconditional, mirroring `--share-only`'s and account-cardinality's own absolute bars, since the doc's exclusion list bars it regardless of authorization. The `--share-only`, account-cardinality, and auto-publishing-scope sub-bullets otherwise stay byte-identical — the new subsection's exclusion list is written so those three keep agreeing with the doc by construction rather than by luck.

### Assumption ledger

**Root problem:** an agent holding the owner's explicit permission to publish one wider-than-one-account figure has no rule that permits it, because the collapse in PR #1011 left the wider-corpus bar absolute in both the doc and the always-loaded `CLAUDE.md`.

**Givens:**

- **G1.** Publication discipline here is tier-3 reviewer judgment with no mechanical enforcement. The doc states why: "A measurement's safety depends on which corpus produced it, not what string it contains, and a hook can't see that" (`docs/private-project-redaction.md:93-96`). Dissolving this needs a provenance mechanism that does not exist.
- **G2.** `_POOLED_PUBLICATION_POINTER` lives on the unmerged `review-round-cost-pooled` branch, which another PR owns. Its citation cannot be retargeted from here.
- **G3.** The repo-root `CLAUDE.md`'s per-project/per-account/per-engagement dimension bar is absolute and is the parent instruction this doc serves. A subordinate doc cannot relax it.

**Rows:**

1. The doc's own vocabulary separates "exemption" (falls outside the bar) from "exception" (inside it, released case by case), so `CLAUDE.md`'s "the two exemptions" stays at two. `[verified: docs/private-project-redaction.md:169-173]`
2. A structural-tool-shape eligibility test qualifies `cost --share-only`: it refuses `--projects`/`--by-project`/`--no-redact`/`--summary`/`--top` at the CLI boundary with exit 2 and prints its own do-not-publish banner. `[verified: claude/.claude/scripts/transcript_analysis/cost.py:566-600, :961-968]`
3. Two standing sites state that `--share-only` output publishes nothing, and both must stay true after this change. `[verified: docs/transcript-analysis.md:620; .claude/skills/code-review-claude-config/SKILL.md:18-20]`
4. `docs/transcript-analysis.md:620` cites "A wider corpus goes to the owner, never into a public artifact" by exact heading text for the claim that a calendar-anchored wider-corpus series is barred outright, and a test pins that citation. `[verified: claude-skills/skills/tests/test_skills.py:3162-3178]`
5. This doc's intra-file cross-references use a bare quoted heading name, not the `` § "…" `` citation grammar, so the new prose adds no citation-extraction surface. `[verified: docs/private-project-redaction.md:188-190, :211, :222]`
6. Inserting a subsection before `### Remediation` cannot disturb the case-study registration test, which slices the file from `### Remediation` to end. `[verified: claude-skills/skills/tests/test_skills.py:3214-3215]`
7. No `cost.py` citation site needs a change — all three are `--share-only`-specific or its docstring, and the exception explicitly does not reach `--share-only`. `[verified: cost.py:385-398, :578-586, :961-968]`
8. `.claude/skills/*/SKILL.md` is inside `test_skills.py`'s scanned skill corpus, so any citation added there is already covered by the general resolution test; editing the file also triggers hook-enforced `/skill-review`. `[verified: claude/.claude/scripts/select-tests.py:198-201; .claude/rules/review-pipeline-dispatch.md]`
9. This section's changes do not get CHANGELOG entries — PR #1011 rewrote 382 lines of it and logged none. `[verified: grep of CHANGELOG.md for single-account/carve-out/pooled/redaction — zero matches]`
10. The owner wants wider-than-one-account output internal and unpublished by default, with the standing liberty to authorize publication case by case. `[engineer-verified]`
11. This ships as its own PR rather than folded into PR #1009. `[engineer-verified]`
12. The proposal's three-part shape (figure, exact command, destination artifact) plus the in-artifact citation is what `_POOLED_PUBLICATION_POINTER` already instructs. `[verified: constant quoted verbatim in this session's dispatch; the constant itself was not re-read here — it is on the unmerged branch, per G2]`
13. The checklist sub-bullet's single `unless` reads as exhaustive, so an authorized figure would be flagged P1 against the amended doc. `[verified: .claude/skills/code-review-claude-config/SKILL.md:16-17]`
14. A `CLAUDE.md` trim is claimed by other sessions, making this file a merge-conflict surface. `[unverified — from an auto-memory index line, not checked against any branch this session]`
15. The deleted carve-out's "No time series" bar named two distinct risk shapes, and its own wording treats accumulation across successive authorized asks — not a single figure that is itself a calendar-time series — as the primary case ("a second one published later forms a barred two-point series"). The original three-item exclusion list carried forward neither explicitly; `docs/transcript-analysis.md:620` already independently asserts a calendar-anchored wider-corpus series is "barred outright." A first fix pass (calendar-time-axis exclusion item) closed only the single-command half; a second re-review pass found the accumulation half still open, since neither the "fresh ask" sentence nor the original composition-naming clause reached composition against a prior owner-authorized figure — the composition-naming clause now covers that too. `[verified: git show e4857828 -- docs/private-project-redaction.md; docs/transcript-analysis.md:620; found by ciso-reviewer review passes this session — a cumulative-diff pass, then a plan-re-review pass for the accumulation gap]`
16. The deleted carve-out's "Approval gate" required a durable, independently-checkable citation and explicitly excluded a bare narrative claim, offering two session-ID-free anchor options: "session identifier plus turn index **or timestamp**." This repo's own `ready-for-review/SKILL.md` separately instructs never quoting a raw `session_id` into PR-reaching prose, ruling out the first option — but not the second. A first fix pass used a bare date, which a ciso-reviewer re-review flagged as too coarse for a repo with a high-frequency, multi-session-per-day usage pattern (a date alone doesn't identify which session carried the yes); a timestamp (date and time-of-day) is the closer match to the deleted gate's own durability bar and carries no `session_id` exposure. `[verified: git show e4857828 -- docs/private-project-redaction.md; claude-skills/skills/ready-for-review/SKILL.md's "Do not quote the raw session_id into prose" line; found by the same two ciso-reviewer passes as row 15]`
17. The calendar-time-axis exclusion item's "time-bucketed breakdown" wording leaves an edge case ambiguous — whether a two-point before/after split counts — but the plan's own Out-of-scope section already states no permitted-split mechanism is being reinstated, so a two-point split has no sanctioned path regardless of how the bullet's wording is read; the clarification is stated inline to spare a reader from reconstructing that connection. `[verified: found by the second ciso-reviewer plan-re-review pass (row 15's second pass), Low severity, over-restrictive direction]`

**Mechanisms:**

- **M1 — a new `###` subsection rather than inline prose.** `anchors: row4`. Inline prose would put the escape hatch inside the exact subsection `docs/transcript-analysis.md:620` cites for "barred outright," weakening a test-pinned citation's target; a sibling section leaves that subsection byte-identical. It also gives the out-of-scope pointer retarget a stable heading to aim at. Lighter primitives weighed: **(a) one sentence appended to the existing subsection** — fails on the citation-weakening above, and because an exception with no stated limits is precisely the hole; the limits are what need the words. **(b) No doc change, leaving the exception implicit in "ask the owner"** — fails because the `/code-review` checklist P1s the figure and there would be no doc text to override it, and the tool's pointer would have no heading to cite. **(c) A `docs/design-decisions/` entry instead of amending the canonical doc** — fails the single-source-of-truth rule: a live publication rule recorded outside its canonical section is a second copy that drifts.
- **M2 — eligibility keyed on the act of authorization, not tool shape or tool name.** `anchors: row2, row3`. The shape key admits `--share-only`; the name key needs a doc PR per tool and puts a flag name into a corpus-and-figures policy.
- **M3 — authorization cited in the publishing artifact.** `anchors: G1, row13`. With no mechanical enforcement available, the artifact-level citation is the only thing a reviewer can check, and the only thing that distinguishes an authorized figure from a violation on its face.
- **M4 — a closed three-item list of what an authorization does not release.** `anchors: G3, row3`. Without it the exception silently becomes a route around `CLAUDE.md`'s absolute dimension bar and around two standing `--share-only`/cardinality bars. Naming them is also what keeps the checklist's other sub-bullets correct unedited.
- **M5 — `CLAUDE.md`'s "never published" clause made to defer.** `anchors: root`. The always-loaded surface is the first thing every agent reads; leaving it asserting "never published" makes the exception unreachable in practice, so the feature would not ship. The clause also currently restates a conclusion the doc owns, which is the drift the single-source-of-truth rule names. Lighter alternative — leave it and rely on the doc — fails for the unreachability above.
- **M6 — one clause added to one checklist sub-bullet.** `anchors: row13`. The minimum that stops the enforcement surface from contradicting the rule it enforces; anything larger buys a bigger `/skill-review` surface (row 8) for no gain.
- **M7 — no new test.** `anchors: G1, row5, row6`. The convention being established is unverifiable by machine for the reason G1 states, and the mechanical surfaces this diff touches are already covered: the doc's intra-file references add no citation construct (row 5), the Remediation slice test is untouched (row 6), and the SKILL.md's existing citation is unchanged. A presence-pin on the new heading would not have survived the kind of deliberate deletion PR #1011 performed, so it buys nothing.
- **M8 — a fourth, unconditional exclusion-list item for a wider-corpus figure's own calendar-time axis, plus its two-point-split clarification.** `anchors: row15, row17`. Closes the single-command half of the deleted "No time series" bar — a command authorized once that returns a time-bucketed series in one shot — and states inline that a two-point before/after split has no sanctioned path either, so a reader doesn't have to reconstruct that connection from the separate Out-of-scope note. Lighter alternative weighed: folding this into the dimension bullet's existing `unless` clause as a carve-out — fails because the other three exclusion-list items are already unconditional bars, not conditional carve-outs on an `unless`, so a fifth condition bolted onto an `unless` clause would read as weaker than the peer items it sits beside.
- **M9 — citation content extended to require a timestamp, not a session/turn identifier.** `anchors: row16`. The deleted gate's own shape (session ID plus turn index) cannot be reinstated verbatim without violating this repo's separate, already-established rule against quoting a raw `session_id` into PR-reaching prose — but the deleted gate's own text offered a second, session-ID-free option ("or timestamp"), which a bare date under-reaches on a repo with routine multi-session-per-day usage. A timestamp is the closer match to the deleted gate's durability bar with no session-ID exposure.
- **M10 — composition-naming duty extended to prior owner-authorized wider-corpus figures, not only the Remediation grandfathered list.** `anchors: row15`. Closes the accumulation half of the deleted "No time series" bar — two or more individually-scalar, separately-authorized figures that, once both are published, reconstruct a series the deleted text calls out as barred even for a plain count. This is a plan-text change to the composition check's stated scope (G1: no mechanical enforcement is feasible for this class of judgment), not a new mechanism.

### Dispatch split

**One `code-writer` dispatch.** The three edits do not partition into independently specifiable file sets — the `CLAUDE.md` clause and the checklist clause both have to agree with the new subsection's exact wording, so splitting would force the same prose into two prompts and let two agents settle it differently.

## Critical files

- **`docs/private-project-redaction.md`** (modify) — insert the new `### The owner can authorize one figure, case by case` subsection between the end of "A wider corpus goes to the owner, never into a public artifact" (current line 167) and `### Own-history counts were never inside this class` (current line 169). In that next subsection, change "the bar above" to "the wider-corpus bar" in the sentence "Two classes fall outside the bar above rather than being exceptions to it." Also broaden "New figures against the grandfathered set"'s opening em-dash clause per the Approach section's prescribed text, so that section's own scope sentence covers an owner-authorized wider-corpus figure, not only own-history/this-repo-one-account figures. Reuse the file's existing conventions rather than inventing: bare quoted heading names for intra-file references (as at lines 189, 211, 222), a bullet list for a set of parallel conditions (as in "This repository, one account"), ~68-column wrap. Do not rename either existing heading. Do not add a `` § "…" `` construct anywhere in the new prose.
- **`CLAUDE.md`** (modify, lines 164-167) — extend the citation's claimed coverage and replace "Anything wider goes to the owner privately and is never published." with the deferring clause given above; leave "the two exemptions" wording itself untouched (still two, per the doc's exemption/exception vocabulary distinction). Merge-conflict surface per row 14: keep the edit to this clause and do not reflow neighbouring paragraphs. Editing this file requires an `ai-instruction-and-memory-files` pass before commit.
- **`.claude/skills/code-review-claude-config/SKILL.md`** (modify) — extend the dimension sub-bullet's and the "cannot refuse a wider corpus" sub-bullet's `unless` clauses, and add one new unconditional sub-bullet for the calendar-time-axis bar, as given. This is a `SKILL.md`, so `/skill-review` is **hook-enforced**: `require-skill-review.sh` blocks `git commit` until the behavioral-equivalence marker is written (`.claude/rules/review-pipeline-dispatch.md`). Leave the remaining sub-bullets and the existing `` § "Publishing a tooling measurement" `` citation byte-identical, so `test_tooling_measurement_code_review_skill_citation_is_still_present` stays green.

No test file changes, no `claude/.claude/scripts/**` changes, no `CHANGELOG.md` entry (row 9).

## Verification

1. **Test suite:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree root. This is the sanctioned command per this repo's `CLAUDE.md`; do not substitute a hand-widened `pytest` invocation. The changed paths map through `DOCS_DIR`, `ROOT_CLAUDE_MD`, and `ROOT_SKILLS_DIR`, so let the script decide the domain set rather than pre-judging it.
2. **Citation resolution must stay green** in `claude-skills/skills/tests/test_skills.py` — these are the tests this diff could break, and each would fail on a heading rename rather than on the insertion:
   - `test_tooling_measurement_citation_resolves_to_real_heading` (parametrized over `CLAUDE.md`, `docs/cost-levers-considered.md`, `docs/transcript-analysis.md`, and the two `docs/design-decisions/schedulewakeup-*.md` files)
   - `test_cost_trend_share_only_citation_resolves_to_real_heading`
   - `test_tooling_measurement_code_review_skill_citation_is_still_present`
   - `test_skill_citations_resolve_to_real_headings` and `test_every_citation_shaped_construct_is_extracted`
   - `test_case_study_pooled_figure_markers_are_registered_or_new`
3. **Heading-stability check:** `git diff -- docs/private-project-redaction.md` must show the two existing `###` headings as context lines, never as changed lines. A rename there breaks citations in six files.
4. **`/skill-review`** on the `code-review-claude-config/SKILL.md` diff — hook-enforced, blocks the commit until its marker is written. Per `.claude/rules/skill-and-agent-self-review.md`, re-run fresh allow/deny fixture pairs against the amended sub-bullets: a must-flag fixture (a wider-corpus figure with no timestamped authorization citation) and a must-allow fixture (the same figure with one), plus a must-flag fixture for a calendar-time-axis figure even with a timestamped authorization citation present (the new bullet is unconditional).
5. **`ai-instruction-and-memory-files`** on the `CLAUDE.md` diff.
6. **`/code-review`** before commit. Its project layer is the very file this diff amends — the reviewer must read the amended checklist, not a cached copy of it.
7. No lint run is implicated: no Python and no shell files change.

## Out of scope

- **PR #1009 / the `review-round-cost-pooled` branch and worktree.** Retargeting `_POOLED_PUBLICATION_POINTER`'s citation to the new heading is a separate follow-up after this merges and #1009 rebases. Forward note for whoever does it: `review_rounds.py` is a `.py` file, so it sits outside both `test_skills.py`'s scanned skill corpus and `test_tooling_measurement_citation_resolves_to_real_heading`'s `docs/*.md` parametrize list — that retarget needs its own citation-resolution test or an added parametrize entry, or the new citation ships unpinned.
- **Reinstating any machinery PR #1011 deleted** — the "What it permits" scope lists, three standing bars, and permitted-split conditions. The deleted "no time series" bar is the one exception: its two component risks (repeated authorized asks accumulating into a series, and a single authorized figure that is itself a calendar-time series) are both closed here, but by two independent one-line rules — the "a re-run over a grown corpus is a fresh ask" sentence and the new calendar-time-axis exclusion-list item — not by the deleted apparatus's own machinery.
- **Renaming either existing heading** in `docs/private-project-redaction.md`.
- **Amending every downstream site that summarizes the bar.** The rule applied: amend a site only where the overstatement would make the exception unreachable (`CLAUDE.md`, always loaded) or would false-positive an enforcement surface (the `/code-review` checklist). Deliberately left unchanged, each because it states a correct default for its own subject rather than the general rule: `claude-skills/skills/transcript-analysis/SKILL.md:98`, `docs/transcript-analysis.md:620`, `cost.py:392`/`:583`/`:967`, `docs/cost-levers-considered.md:533`, and the two `docs/design-decisions/schedulewakeup-*.md` withholding sites. Growing an "unless the owner says yes" clause on each is exactly the prose accretion PR #1011 cleared.
- **Any relaxation of `--share-only`, account cardinality, the per-project/per-account/per-engagement dimension bar, or the calendar-time-axis bar.** The new subsection's exclusion list exists to foreclose all four.
- **Mechanical enforcement of the citation requirement.** Barred by G1, not deferred — the merge gate named in the new subsection's prose is a human read, not a mechanical check.
- **A registry of previously-authorized figures analogous to Remediation's grandfathered list.** ciso-reviewer flagged that authorized figures accumulate with no enumerable set to check future composition against, the way Remediation's closed list lets "New figures against the grandfathered set" run mechanically. Building that registry is real scope, not a one-line fix — left as a follow-up rather than folded into this plan silently.
