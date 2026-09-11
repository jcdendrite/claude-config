# Redact account/root-count figures in opus-session-cost-ab-test.md on main

## Context

`.claude/plans/opus-session-cost-ab-test.md`, already merged to `main` via
PR #912, states the exact cardinality of the machine's Claude-Code-account /
`transcript-analysis.py`-root corpus at roughly two dozen sites — describing
the corpus's account/root count directly in prose, and using a bounded
ordinal-labeling scheme for individual accounts. Root `CLAUDE.md` states,
without qualification: "Never publish a figure carrying a per-project,
per-account, or per-engagement dimension — this is absolute, not a factor to
weigh, no borderline case." An account/root count is the literal cardinality
of the dimension that rule forbids. The only mechanism that could license
such a figure is `docs/private-project-redaction.md`'s pooled-measurement
carve-out. That carve-out's scope is closed to "this repo's own tooling in
use — Claude Code tool calls, sessions, and agent dispatches. Nothing
else," and does not cover account/root counts.

This was surfaced by a CISO review of a sibling branch
(`opus-ab-test-cross-machine-rerun`), which found the same pattern at more
sites there (the inherited-from-`main` sites plus new ones from that
branch's own amendment). The engineer decided, in-session, to have that
sibling branch's own session fix its own unpushed copy, and separately
authorized this PR: a follow-up PR against `main` that generalizes the
already-published sites to vague phrasing carrying no per-account dimension.
This does not retract the exposure already in `main`'s git history — per
`docs/private-project-redaction.md`'s Remediation section, only a history
rewrite would do that, and that stays the owner's to run personally, never
an agent's, even if told to. The engineer did not authorize a history
rewrite in this conversation; this plan is scoped to a forward-looking
wording fix only.

A wider check this session (see Approach's assumption ledger) found the
true site count in the target file is roughly double the original
estimate. The same defect recurs in at least eleven other committed files
repo-wide. Two of those carry a worse disclosure than a bare count. The
engineer decided to keep this PR scoped to the one file only, and to track
the wider sweep as internal next-phase follow-up rather than a public
GitHub issue (see Out of scope).

Intended outcome: a `main`-targeted PR that rewords the target file's sites
to vague phrasing, reviewed by `/plan-review` before implementation and
`/code-review` before commit.

## Approach

Rewrite each account/root-cardinality phrase in
`.claude/plans/opus-session-cost-ab-test.md` into the unnumbered register the
same file already uses everywhere else — "the declared-roots union," "every
declared root," "every account on this machine" — so the prose keeps its
full design meaning while carrying no per-account cardinality. The rewrite
is per-site and semantic, never a global token substitution: several sites
use the number to carry a distinct load (a scope that *cannot be narrowed*,
a dedup that needs *every* root's set at once), and at least three numeric
matches in the file have nothing to do with accounts and must not change.

Two corrections to the initial scope estimate, both from greps run this
session:

**The site inventory is larger than ~13.** An initial, narrower search
pattern (matching keyword variants of the account/root count, plus the
ordinal scheme's exact digit bound) matches 13 lines. A wider pattern
generalizing those keywords, plus a bare digit-class check on the ordinal
scheme (`account-[0-9]`, case-insensitive) instead of the narrower
pattern's exact bound, matches 22 lines — 21 of which are real, about 25
phrase-level occurrences. Eight lines carry the dimension in phrasings the
narrow pattern never anticipated (`:61`, `:71`, `:96`, `:139`, `:141`,
`:172`, `:206`, `:237`) — number-word variants naming the cardinality
directly rather than via the narrower pattern's flagged keywords. The exit
criterion is therefore a full-file read-through plus the wide grep, not a
grep against the pattern that already undercounted once.

**This plan file must not republish the figures it exists to remove.** A
plan under `.claude/plans/` ships in the same PR as the implementation and
is subject to the same redaction rules as any other public-repo content
(root `CLAUDE.md`). The Context section above describes the defect class
without restating the values, and the same constraint binds the commit
message and PR body. Worth distinguishing precisely: the diff's own `-`
lines unavoidably show the removed text, and that text is already in
history at `a83beb5d` — the Remediation section is right that only a
rewrite retracts it. The plan Context and PR prose are different, because
they are *new* standalone republication, and they are avoidable.

**Why this phrasing direction rather than the `2663500e` share pattern.**
That commit replaced a raw session count with a share of a denominator the
reader could already see. No analogous denominator exists for an account
count, and a range still states the cardinality within a bound, which the
rule forbids without qualification regardless of how wide that bound is.
The chosen
register is not invented for this PR: the target file already writes
"per-root loop, not the union" (`:35`), "once per declared root" (`:37`),
"looped once per declared root" (`:176`, `:177`), and "every account
referenced by ordinal" (`:220`). The fix makes the numbered minority match
the unnumbered majority in the same file, which is also why no design claim
is lost — every downstream argument in the file turns on "more than one
root, looped per root," never on the specific value.

**Alternatives set aside.** Deleting the offending sentences: destroys
design rationale (why `cost --branches` needs cross-root dedup) for no
redaction gain, and a committed plan is a record of a decision under
`CLAUDE.md` Axis 3 — this PR is authorized to edit the disclosing figure,
not to rewrite the record. Keeping the bounded ordinal range because
ordinals are the redaction mechanism: the scheme stays, only its upper
bound leaks, so `account-N` preserves the mechanism exactly. A history
rewrite: excluded by the engineer and by
`docs/private-project-redaction.md`'s Remediation section.

### Assumption ledger

**Root:** an already-public plan file states the exact per-account/per-root
cardinality of this machine's Claude Code corpus at roughly two dozen sites,
which `CLAUDE.md:158` forbids without qualification and which
`docs/private-project-redaction.md:108-112`'s pooled-measurement carve-out
does not license; this change removes the figure going forward without
claiming to retract it.

**Givens:**

- The exposure already in `main`'s history is not reachable by this change
  — only a history rewrite retracts it, and that is the owner's to run
  personally (`docs/private-project-redaction.md:177-181`). Another party
  owns it.
- No mechanical gate catches this class, so nothing will block a regression
  here or in a future plan file. Widening `deny-private-project-refs.sh` is
  a hook-design decision outside this plan.
- The declared-roots set is managed by `setup-claude-accounts.sh` in a
  separate private repository, so the underlying fact will keep recurring in
  future machine-wide analysis plans. Another repo owns it.

**Rows:**

1. The engineer authorized a forward-looking wording fix to this one file
   and did not authorize a history rewrite — `[engineer-verified]`.
2. The dimension appears on 21 lines of the target file at roughly 25
   phrase occurrences: `:39`, `:43`, `:49`, `:61`, `:71`, `:96`, `:122`,
   `:138`, `:139`, `:141`, `:162`, `:164`, `:170`, `:172`, `:181`, `:186`,
   `:204`, `:206`, `:232`, `:237`, `:241` —
   `[verified: wide grep + line reads this session]`.
3. The initial, narrower search pattern undercounts by eight lines —
   `[verified: both greps run this session, 13 lines vs 22]`.
4. Three numeric matches are false positives that must survive unchanged —
   `[verified: read this session]`:
   - "those five" (`:39`, `:138`) — the five subcommands (`cost`,
     `subagents`, `subagent-mix`, `plan-boundary`, `pr-cost`).
   - "six parts below" (`:212`) — the six Verification items.
5. No arithmetic or downstream claim in the file depends on the literal
   value — `[verified: every matching line read this session; the file's
   non-matching prose was not read end to end, which is why Verification
   item 2 requires the read-through]`.
6. The file already uses the target unnumbered register at `:35`, `:37`,
   `:176`, `:177`, `:220` — `[verified: read this session]`.
7. The bounded ordinal range at `:164` is a pseudonymization scheme whose
   mechanism is sound; only the range's upper bound discloses the count,
   and `:220` already refers to the same scheme without a bound —
   `[verified: read this session]`.
8. This plan's own Context names the defect class without restating the
   values — `CLAUDE.md`'s redaction rule applies to plan files too
   (`[verified: read this session]`).
9. At least eleven other committed files on `main` carry the same
   account-cardinality figure, including two reader-facing
   `docs/case-studies/` entries and a source comment —
   `[verified: repo-wide grep this session; the run was head-limited at 40
   matches and may be incomplete]`.
10. No hook detects this class — evidenced directly by row 9: a dozen files
    carrying the figure passed `deny-private-project-refs.sh` at commit
    time, and `docs/private-project-redaction.md:42-52` scopes the
    always-on tier to six structural detectors that do not include numeric
    cardinality — `[verified: repo-wide grep + docs read this session]`.
11. `select-tests.py` maps no domain to a markdown-only diff under
    `.claude/plans/` — `[unverified]`; the target file's own Verification
    section (`:212`) asserts this precedent, but that is prose about the
    tool, not the tool's behavior, so Verification item 5 resolves it by
    running it.
12. The engineer decided to keep this PR scoped to the target file only,
    and to track the wider repo-wide sweep (row 9) as an internal
    next-phase item rather than a public GitHub issue — `[engineer-verified]`.

**Mechanisms:**

- Per-site semantic rewrite, no `sed` or global substitution —
  `anchors: row2, row4, row5`. A token substitution would flatten a
  cannot-be-narrowed scope claim into a merely-more-than-one claim, and
  would corrupt the three false positives.
- Replacement vocabulary drawn from the file's own existing unnumbered
  phrasing — `anchors: row6`. No new register to review, and internal
  consistency improves rather than degrades.
- Keep the `account-N` ordinal scheme, drop only its upper bound —
  `anchors: row7`.
- Rewrite this plan's Context and constrain the commit message and PR body
  to the same rule — `anchors: row8, root`.
- Exit criterion is wide grep plus full read-through —
  `anchors: row3, row5`.
- Single `code-writer` dispatch, no split — `anchors: row2`. One file, one
  contiguous concern; the split test needs non-overlapping file sets, and
  there is only one set.
- Scope stays single-file; the repo-wide sweep is deferred, tracked only
  in this plan's Out of scope section, no external issue —
  `anchors: row12`.

**Over-powered-primitive check.** The tempting heavier mechanism is a new
always-on detector in `deny-private-project-refs.sh` matching number-words
near "account"/"root", so this cannot recur. Two lighter primitives exist
and are preferred. First, the reviewer-discipline tier already enumerated
in this repo's `CLAUDE.md` — adding account/root cardinality to that prose
list costs one line and no regex surface. Second, the review path that
actually caught this: a CISO review dispatched by `/code-review`, which
reads for meaning rather than pattern. The decisive argument against the
detector is inside the target file itself: `:212`'s Verification-item
count is a number-word phrase with nothing to do with accounts, which a
cardinality detector would have to either misfire on, or be narrowed until
it also fails to catch a genuine account-count phrasing worded to route
around the same keywords. A detector is not adopted here.

## Critical files

**`.claude/plans/opus-session-cost-ab-test.md`** — the only repository file
this change edits. Rewrite the cardinality at 21 lines. Group them by the
semantic each rewrite must preserve; a reviewer checks the semantic, not the
wording:

1. **Scope-that-cannot-be-narrowed** — `:39`, `:138` (same claim in ledger
   row 2), `:181`, `:43` — each states the corpus's full multi-root scope
   as a fixed, non-narrowable quantity. The replacement must keep "the
   full declared-roots union, not narrowable," not degrade to "multi-root."
2. **Every-root-at-once, which the dedup requires** — `:43`, `:170`, `:204`,
   `:206`, `:186`. The cross-root dedup is only sound because *all* roots'
   branch-name sets are compared; "every declared root" preserves that,
   "several roots" does not.
3. **Cost-of-a-second-pass** — `:49`, `:96`. The argument is that a rescan
   multiplies by the number of roots; "across every declared root" carries
   it without the multiplier's value.
4. **Stowed-config uniformity across accounts** — `:61`, `:141`. The claim
   is that all accounts share one checkout; "every account on this machine"
   is exact.
5. **Corpus-composition and commingling disclosures** — `:71`, `:122`,
   `:162`, `:164`, `:172`, `:186`, `:232`, `:237`, `:241`. These carry a
   per-*engagement* count of client accounts, which is the same defect one
   step worse; `:186` carries this alongside its group-2 dedup-scope
   phrase, as two distinct phrases on one line. Replace with an unnumbered
   form such as "multiple private-project accounts."
6. **The ordinal scheme** — `:164` only. The bounded ordinal range becomes
   the unbounded `account-N` form; `:220` already shows the unnumbered form
   of the same rule.
7. **The target file's own ledger rows** — `:122` (a Given), `:138`, `:139`,
   `:141`. `:139` currently states the exact root count the declared-roots
   file configures; after the rewrite it asserts only that the file
   declares more than one root, and its `[verified]` tag still holds. The
   row is not vacuous — multi-root scope is what every downstream claim
   actually consumes. `:122`'s Given keeps its qualifying reason ("a
   separate repository manages it") unchanged.

**Must not change:** `:39` and `:138`'s "those five" (subcommands), `:212`'s
"six parts below" (Verification items), and `:61`'s "repo-level" (a
substring false positive on `repo-[A-Z]`).

**`.claude/plans/redact-account-root-count.md`** — this plan's own Context,
rewritten to name the defect class without restating the values (done — see
Context above).

**Reuse.** The direction is the one commit `2663500e` established on the
sibling branch: replace a corpus-derived figure rather than delete its
sentence. Its *mechanism* — substituting a share of a visible denominator —
does not transfer, for the reason in Approach. Do not cite it as precedent
for a share construction here.

**Dispatch split.** One `code-writer` dispatch covering both files. No
split: a single file set, and the two files' edits share the same rule, so
splitting would force the same background into both prompts.

## Verification

No test suite exercises this change; every item below is a human or
reviewer check.

1. **Wide re-grep.** After the rewrite, grep the target file
   case-insensitively for the account/root-cardinality number-words and
   ordinal-digit patterns identified during this session's scoping pass
   (re-derive the concrete keywords from the target file's pre-rewrite
   content rather than committing them to this plan — see Approach's
   no-republication rule). Expect surviving matches only at the sites
   already named as false positives, each justified in the PR body:
   `:39`/`:138` ("those five," the five subcommands), `:212` ("six parts
   below," the six Verification items), `:61` ("repo-level"), and `:164`'s
   `repo-A`/`repo-B` pseudonym labels (an unrelated hit on the same
   ordinal-scheme check). Any survivor outside that list is a missed site.
2. **Full read-through.** Read the target file end to end for number-words
   carrying the dimension that no pattern anticipated. This item is not
   redundant with item 1 — the initial, narrower search pattern already
   missed eight lines, which is the evidence that a grep alone is not a
   sufficient exit criterion.
3. **Same check on the rest of the diff.** Run item 1's pattern against
   `.claude/plans/redact-account-root-count.md`, and against the commit
   message and PR body text before either is published.
4. **Semantic-preservation spot check.** At `:39`/`:138` confirm the text
   still says the scope cannot be narrowed, not merely that it spans
   several roots. At `:43`/`:204`/`:206` confirm the dedup still reads as
   comparing *every* root's set. At `:164` confirm the ordinal scheme
   survives and only its bound is gone. At `:139` confirm the ledger row
   still asserts something load-bearing.
5. **`select-tests.py`.** Run
   `.venv/bin/python3 claude/.claude/scripts/select-tests.py` and report
   what it selected. A markdown-only diff under `.claude/plans/` is
   expected to select little or nothing, which is the correct result — per
   this repo's `CLAUDE.md`, a path it cannot map is a bug in its rule
   table, never a licence to widen the run by hand. Resolves ledger row 11.
6. **Gates.** `/plan-review` on this plan before implementation
   (hook-enforced). `/code-review` before the commit — it carries no
   file-type exception, so a markdown-only diff is in scope.
7. **PR-body claim accuracy.** Run `/pr-description`'s claim-verification
   step against one specific sentence: the body must not state or imply
   that this figure is no longer published in this repository. Ledger row 9
   says it still is, in at least eleven other files, and the history at
   `a83beb5d` retains this file's own prior text. A body claiming a
   completed remediation would be a false quantitative claim under
   `CLAUDE.md` § Ground every choice.

## Out of scope

- This branch's own copy is not part of this plan's Critical files — a
  different session on `opus-ab-test-cross-machine-rerun` owns that fix.
- Any git history rewrite of `main` or any other branch's commits — the
  engineer's call, never an agent's, per
  `docs/private-project-redaction.md`'s Remediation section.
- Amending `docs/private-project-redaction.md`'s pooled-measurement carve-out
  to ever cover account/root counts — a separate policy-change decision the
  engineer has not asked for here.
- **The same figure in at least eleven other committed files on `main`.** A
  repo-wide grep this session found the identical account-cardinality
  figure in `docs/transcript-analysis.md` (`:974`, and a long match at
  `:287`), `docs/case-studies/delegate-instrument-authoring.md:50`,
  `docs/case-studies/handoff-threshold-impact.md:128`,
  `claude/.claude/scripts/transcript_analysis/reviewer_yield.py:521`, and
  the plan files `opus-frontload-review-rounds.md` (`:81`, `:91`),
  `handoff-threshold-impact-analysis.md` (`:33`, `:107`, `:199`, `:533`,
  `:780`), `cost-attribution-integrity.md` (`:181`, `:182`),
  `background-slow-bash-calls.md:77`, `cost-top-multiroot-redact-assertion.md:9`,
  `plan-architect-scope.md:115`, and `transcript-scope-header-zero-match.md:39`.
  That grep was head-limited at 40 matches, so the inventory may be
  incomplete. Two of those sites carry more than a bare cardinality:
  `cost-attribution-integrity.md:181-182` publishes a per-account
  filesystem inventory and a per-account distributional fact, and
  `background-slow-bash-calls.md:77` publishes a raw session-file count
  from the mixed corpus — the class
  commit `2663500e` treated as a violation. **Decision:** the engineer
  chose to keep this PR scoped to the target file only. The wider sweep is
  deferred as a next-phase item, noted here for whoever picks it up next —
  no GitHub issue or other external tracking artifact was opened for it,
  per the engineer's explicit instruction.
- Adding a mechanical detector for account/root cardinality to
  `deny-private-project-refs.sh` — rejected on its merits in Approach, not
  merely deferred; a detector able to catch a genuine account-count
  phrasing would misfire on `:212`'s unrelated "six parts below" in this
  very file.
- Any change to `~/.claude/transcript-config-dirs` or the account set
  itself — managed by a separate repository, per the target file's own
  Given at `:122`.
