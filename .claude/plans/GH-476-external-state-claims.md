# GH-476 — External-state claims in the PR body

## Context

**Goal: give `/pr-description` a check for claims the PR body makes about
state outside the repo, and give `/respond-pr` the obligation to update what
already referenced a follow-up ticket once that ticket is filed.**

Issue #476 reports a PR body asserting "a ticket to plan the CI hookup is
pending rather than filed yet" while, two comments later in the same review,
the ticket was filed. The body was never corrected. The issue's evidence
includes a human running `grep -n "pending\|..."` against the body by hand —
a post-hoc check run precisely because no pipeline step caught it.

**What each edit does, stated precisely** (an earlier draft of this plan
overstated Edit 1; corrected after review):

- The reported instance contained the literal word "pending", which the
  existing marker bullet (`pr-description/SKILL.md:118`) already
  pattern-matches. Edit 1 would **not** have caught it that the marker check
  missed — it would have been caught had the skill re-run at all. **Edit 2 is
  what addresses the reported instance's trigger.**
- Edit 1 covers the class the issue also names but the marker check cannot
  see: a claim that goes stale while containing no marker word ("will be
  filed", "not yet confirmed").

The issue predates two merged changes and its file/line target is stale: the
skill was renamed `sync-pr-description` → `pr-description` (#478), and its
"Flag and fix" list is now `claude/.claude/skills/pr-description/SKILL.md:101-119`.
Neither check added since closes the gap:

- **Reader-coherence pass** (`:75-94`) is scoped to judging the body against
  itself — `:77-78` says so outright. "A ticket is pending" is internally
  coherent prose.
- **Content-claim verification** (`:114-117`) re-reads *files in the repo* at
  HEAD. It has no analog for state living outside the repo.

## Approach

Two additive edits. Neither relocates or rewrites existing rule text, so
neither carries a behavioral-equivalence risk.

### Edit 1 — `pr-description`: an external-state check

One bullet in "Flag and fix", placed after Content-claim verification
(`:117`) and before the marker bullet (`:118`).

Mechanism: **re-check the claim at its own source.** This is a deliberate
departure from the issue's prescribed "consult the branch's session history",
rejected because a ticket filed by the human outside any Claude session is
invisible to a transcript but plainly visible in the tracker, and because
`pr-description` carries no transcript dependency anywhere else. Confirmed
with the user.

```
- **External-state claims.** **Content-claim verification** covers files in
  this repo; this covers state outside it — most often a follow-up ticket
  said to be pending, or promised as `will create`. Re-check each at its own
  source, then rewrite the claim to its current truth value; carry an
  identifier across only where the body already names that tracker. Whether
  CI is *wired up* is such a claim; whether CI is *passing* is not — that is
  stripped under **Reviewer-action items Claude can answer itself**.
```

Four review findings are already resolved in that text:

- **No false justification.** The earlier draft claimed the marker check
  "cannot see it" while offering "pending" as its own example. Dropped.
- **No duplication of `:159`.** "External system unconfigured" and
  "coordination step outstanding" were dropped — `:159` Coordination-step
  preservation already enumerates those and supplies their `strip-as-stale`
  disposition. What is genuinely new is the follow-up-ticket case and the
  re-check-at-source verb.
- **Identifier bounded** (`ciso-reviewer` S5). "Rewrite it to what is true
  now" left the model free to write an internal tracker URL into a **public**
  OSS PR body. `deny-private-project-refs.sh:283` scopes itself out of any
  repo whose origin lacks `claude-config`, so nothing mechanically catches
  that. The clause binds the rewrite to the claim's truth value, not the
  source's identity.
- **CI distinction stated.** The issue's own ticket was to *wire a package
  into gating CI* — configuration, not run status. `:110-113` correctly
  dispositions run status as strip; without the boundary named, an
  implementer reads the two rules as contradictory.

Named cross-references (`**Content-claim verification**`, `**Reviewer-action
items…**`) replace the earlier positional "the bullet above / below", which
would silently become false if the list were reordered.

**No frontmatter change.** An earlier draft widened the description at `:8`.
Dropped: `claude/.claude/settings.json:49` sets `pr-description` to
`name-only`, so `_model_invokable_skills()` excludes it — the description is
outside the model's listing budget entirely and cannot affect triggering. The
edit would have been read by no model, while adding a third site to keep in
sync whenever the Checks list changes.

### Edit 2 — `respond-pr`: a filed ticket updates what referenced it

One bullet appended to the **Guidelines** list (`:96-111`), beside the
existing stale-SHA rule at `:111` that governs the same shape — a claim in an
already-posted reply that went stale during the session.

```
- **A filed follow-up updates what already referenced it.** When you file a
  ticket a reply promised as `will create`, correct every place that promise
  was already published: post a correction reply for earlier replies, as with
  a stale SHA, and refresh the PR body by re-running `/pr-description`, which
  owns that surface. Nothing re-reads those artifacts for you.
```

**Nothing is moved or deleted.** An earlier draft relocated the "must be
created before the skill session ends" clause out of the `DEFERRED` field
line (`:41`) into a sentence governing both `DEFERRED` and `OUT-OF-SCOPE`.
Abandoned on three findings:

- `skill-review` Item 11: that is a **behavior change, not a relocation** —
  `OUT-OF-SCOPE` never carried a creation deadline — and the replacement
  downgraded an imperative ("the ticket **must be created**") to a
  declarative headline.
- `staff-product-engineer`: `:39` permits `where-tracked` = "ticket **or
  backlog**", so a hard session-end deadline makes a previously-valid
  `OUT-OF-SCOPE` disposition unsatisfiable.
- The asymmetry is pre-existing and outside this ticket (scope Axis 1).

**Remedy chosen: correction reply, not in-place edit.** `:101` and `:111`
prescribe different remedies for a defective prior reply. Matching `:111` —
the nearer sibling and the same failure shape — keeps this bullet clear of
`:101`'s prefix-verified PATCH path, which `:101` itself documents as
irrecoverable when mistargeted.

**Why prose, and which edit is load-bearing.** `staff-product-engineer`
raised the foundation question: the incident happened while `:41` already
required the ticket be filed before session end. That obligation was
*satisfied* — the ticket was filed; what had no rule was the downstream
artifact, which Edit 2 adds. But the reviewer's underlying point stands:
prose has no runtime gate. Edit 1 is therefore the durable half — it runs on
every `/pr-description` invocation, and the push gate forces one on any push
to a branch with an open PR. Edit 2 is a cheap point-of-change assist, not
the enforcement mechanism.

### Residual gap, stated rather than closed

`pr-description` reads the PR **body** only. A stale promise published in a
**review reply** is never re-examined by any recurring check — Edit 2's
correction-reply obligation is the only thing covering it, and it is prose.
An earlier draft of this plan described the residual hole as "a reply round
that files a ticket and makes no code change"; that was too narrow.

### Assumption ledger

```
Root: the PR body can carry a claim about state outside the repo that was
true when written and has since been overtaken, and no check re-evaluates it.

Row 1 [mechanism]: new "External-state claims" bullet in pr-description's
Flag and fix list — anchors: root — the list has an in-repo content-claim
check and a literal-placeholder check; a claim about external state carrying
no marker word matches neither.
Row 2 [mechanism]: verify at the claim's own source, not via session history
— anchors: row1 — the tracker is already authoritative on whether a ticket
exists and also sees tickets filed outside any Claude session, which a
transcript cannot. Session-history introspection is heavier and weaker.
Row 3 [mechanism]: one additive Guidelines bullet in respond-pr — anchors:
root — respond-pr manufactures the promise, so the obligation to sweep it
belongs there. Two lighter alternatives rejected: doing nothing (leaves
published replies uncovered), and a cross-skill auto-dispatch of
pr-description from respond-pr (heavier; a pointer suffices).
Row 4 [assumption]: target file is claude/.claude/skills/pr-description/
SKILL.md, "Flag and fix" at :101-119, not the issue's stated lines 22–39
[verified: read of file; CHANGELOG.md:35, commit fd1a2bf] — anchors: root
Row 5 [assumption]: the coherence pass cannot catch this class — it judges
the body only against itself [verified: SKILL.md:77-78] — anchors: row1
Row 6 [assumption]: Content-claim verification is scoped to in-repo files
[verified: SKILL.md:114-117] — anchors: row1
Row 7 [assumption]: the reported instance contained literal "pending" and so
was already in the marker bullet's reach; Edit 1 does not close it, Edit 2
does [verified: issue #476 body; SKILL.md:118] — anchors: root
Row 8 [assumption]: CI run status is dispositioned strip-not-verify, but CI
*wiring* is not covered there [verified: SKILL.md:110-113 vs issue #476's
"wire <package> into gating CI"] — anchors: row1
Row 9 [assumption]: :159 already enumerates coordination steps and external-
system setup with a strip-as-stale disposition, so the bullet must not
restate them [verified: SKILL.md:159] — anchors: row1
Row 10 [assumption]: respond-pr:39 permits "ticket or backlog" for
OUT-OF-SCOPE, so extending a filing deadline to it would break a valid
disposition [verified: respond-pr/SKILL.md:39] — anchors: row3
Row 11 [assumption]: :101 and :111 prescribe conflicting remedies for a
defective prior reply; :111 is the nearer sibling and avoids :101's
irrecoverable PATCH path [verified: respond-pr/SKILL.md:101-111] — anchors:
row3
Row 12 [assumption]: pr-description is name-only in settings.json:49 and is
excluded by _model_invokable_skills(), so its description is outside the
listing budget and test_description_within_harness_cap never evaluates it
[verified: settings.json:49; test_skills.py _model_invokable_skills docstring
and body] — anchors: row1
Row 13 [assumption]: no test pins "must be created before the skill session
ends" [verified: grep across skills/tests and hooks/tests — zero hits].
Now moot: the revised plan relocates nothing. — anchors: row3
Row 14 [assumption]: skill-management is absent from committed enabledPlugins
and require-skill-review.sh ships inside that plugin, so /skill-review is
neither invocable nor hook-enforced in this checkout [verified: git show
HEAD:claude/.claude/settings.json; find for require-skill-review.sh] —
anchors: verification
Row 15 [engineer-verified]: the source-verification mechanism (over the
issue's session-history prescription) and the two-site scope (over
pr-description alone) were chosen by the user in this session.
```

## Critical files

| File | Change |
|---|---|
| `claude/.claude/skills/pr-description/SKILL.md` | One bullet inserted after `:117`. Frontmatter untouched. |
| `claude/.claude/skills/respond-pr/SKILL.md` | One bullet appended to Guidelines, after `:111`. Nothing removed. |
| `claude/.claude/skills/tests/test_skills.py` | Two test classes (below) |
| `CHANGELOG.md` | One `### Added` bullet under `[Unreleased]`, naming both new obligations |

**Reuse:** the module-level `_skill_file(name)` helper (`test_skills.py:567`),
and the `TestPrDescriptionTwoModeDispatch` shape (`:551-575`) — a class
docstring naming the regression the assertions catch.

House style in this file requires **per-assertion mutation evidence** in the
docstring (`:526-542`: "confirmed by mutation testing that the other four
assertions stay green even if this sentence is dropped"). State that result
for each assertion; drop any that cannot fail alone.

- `TestPrDescriptionExternalStateCheck` — assert the **mechanism** phrase
  ("at its own source") and the **identifier-bounding** clause. A heading-only
  assertion is dropped as subsumed: no plausible degradation keeps the
  mechanism phrase while dropping the heading. The bounding clause needs its
  own assertion — an unasserted clause is the first thing a later brevity
  edit drops (`ciso-reviewer`).
- `TestRespondPrPromiseRedemption` — assert the sweep obligation and the
  `/pr-description` pointer. The three-assertion structure an earlier draft
  needed is moot: nothing is relocated, so there is no asymmetry fix or
  deletion to pin.
- **Placement assertion** for Edit 1: slice the Flag-and-fix list and assert
  the external-state bullet falls after Content-claim verification and before
  the marker bullet. Substring checks are order-blind, and adjacency is what
  makes the pair readable. Precedent: `test_skills.py:498-516`.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/skills/` — new classes pass;
   `TestPrDescriptionTwoModeDispatch` and the wiring tests (`:458`, `:463`)
   stay green. Do **not** cite `test_description_within_harness_cap` — it does
   not parametrize this skill (Row 12).
2. `../../../.venv/bin/ruff check claude/.claude/` — clean.
3. `/skill-review` on both SKILL.md diffs — required by
   `.claude/rules/review-pipeline-dispatch.md`. **Not available as a skill in
   this checkout** (Row 14): read the body from
   `plugins/skill-management/skills/skill-review/SKILL.md` and apply it, or
   enable the plugin first. Do not record it as hook-enforced.
4. `/code-review`, then `/ready-for-review` before handoff.
5. **Positive-fixture check for Edit 1** (replaces an earlier step that did
   not work). An earlier draft claimed `SKILL.md:92-94` requires an
   affirmative report, so a skipped check would be visible. It does not —
   `:85-94` scopes that requirement to the reader-coherence pass; the
   Flag-and-fix list has none, so a skipped check is indistinguishable from a
   clean one. Instead: run `/pr-description` against a scratch body carrying a
   known-stale `will create` claim for a ticket that exists, and confirm the
   run names that claim. This branch's own PR body is a negative fixture and
   proves nothing.

## Out of scope

- The `OUT-OF-SCOPE` / `DEFERRED` deadline asymmetry in `respond-pr` — real,
  pre-existing, and entangled with `:39`'s "ticket or backlog" allowance.
- The `:101` vs `:111` conflicting-remedy overlap — Edit 2 sidesteps it by
  matching `:111`; reconciling the two is its own change.
- `skill-management` / `claude-hook-review` / `plugin-semver` missing from
  committed `enabledPlugins` while repo-root `CLAUDE.md` states all three are
  registered (Row 14). Raise to the user; do not fix here.
- `docs/skills.md:11` — its check list is already non-exhaustive.
