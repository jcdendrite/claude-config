# Triage: issue 472 error-mode findings

## Context

**Goal:** resolve the seven error-mode findings in issue 472 into per-finding
verdicts backed by independent evidence from this machine, then land the two
fixes that are cheap and settled and file tracked issues for the rest.

Issue 472 was filed from a single run of `/error-mode-analysis` on a different
machine, over a sample the report itself flags as thin — the requested six-week
window contained only seven days of records, so every "Recurring" call in it is
within-week recurrence, not confirmed multi-week recurrence. Triaging those
findings against a second, independent corpus is what makes them actionable:
three of the seven changed materially once measured here, and one of the
proposed fixes turns out to be ordered wrong. Intended outcome is a merged PR
carrying the triage record plus the settled fixes, and a set of issues carrying
the rest with their evidence attached.

**Evidence base.** Corroboration ran against this machine's transcript corpus —
42 project directories, 725 sessions with main-thread activity in the six-week
window 2026-06-12 → 2026-07-24 — using `transcript-analysis.py` plus direct
JSONL parsing, since the tool's own output was partly what was under test. This
is a corroboration pass, not a fresh `/error-mode-analysis` report: the goal was
to test seven specific pre-existing claims, and a fresh bucketing run would have
produced a new sample rather than a second read on the existing one.

## Approach

### Assumption ledger

**Root problem:** seven findings were promoted from a one-week, single-machine
sample; acting on all seven as written would encode one wrong diagnosis, one
misordered fix, and two single-occurrence observations into globally-stowed
config that ships to every user of this repo.

| # | Assumption | Tag |
|---|---|---|
| 1 | Only `review-trace` and `judgment-pair` derive session branch from the first main-thread record; the other 15 subcommands resolve per-record. | `[verified: transcript-analysis.py:839-855, 1027-1036; full 17-subcommand enumeration]` |
| 2 | `review-trace` also derives session *model* from the first record; no other subcommand does. | `[verified: transcript-analysis.py:840-846]` |
| 3 | Mid-session branch transitions are common, not an edge case. | `[verified: 168/725 sessions carry >1 gitBranch; 134 start on main/master and move to a feature branch; worst case hides 771 of 989 records]` |
| 4 | The reported date-range mismatch is **not** a date-parsing bug. | `[verified: buckets day-level output matched true min/max timestamps exactly across the 3 largest project dirs; no mtime/filename-derived dates anywhere in the tool]` |
| 5 | The real mechanism is cross-project branch-name collision under an unscoped default `--projects` glob. | `[verified: _projects_glob defaults to "*"; buckets keys on the literal gitBranch string with no project scoping; branch "main" pools 26 projects / 543 files spanning 2026-06-19 → 2026-07-24]` |
| 6 | `error-mode-analysis` Step 3 prescribes three `gh api` commands that `require-respond-pr.sh` denies — the skill's own documented procedure is self-blocking. | `[verified: error-mode-analysis/SKILL.md:33-37 vs require-respond-pr.sh:71-79]` |
| 7 | `gh api graphql` matches none of the gate's three regexes, **same-repo included**, for both read queries and comment-posting mutations. | `[verified: the regex chain at require-respond-pr.sh:71-79 falls through to `exit 0` at line 78 before the cross-repo check at line 81 is ever reached, so repo targeting is irrelevant to the miss; confirmed by a dry run of the hook against synthetic same-repo read and `addComment` payloads — both allowed, while a REST `pulls/1/comments` payload denied]` — records the pre-Edit-4 state. Edit 4 gates the comment-write mutations (inline, multi-line, and file-sourced bodies) and leaves reads allowed by design. Not total closure of the GraphQL write surface: mutations that alter comment visibility or thread state rather than authoring text stay allowed on purpose. The `-R`-shaped-substring spoof was reachable through this arm — a mutation body carrying `-R other/repo` released the write `[verified: hook run against that payload allowed it; the same mutation with an ordinary body denied]` — and Edit 5 closes it by confining the bypass to reads |
| 8 | A single GraphQL query retrieves all three comment kinds; `gh pr view --json` recovers only two, having no field for inline diff comments. | `[verified: counts matched paginated REST ground truth 4=4, 2=2, 38=38 on a public external PR; the CLI's own field-validation error output lists every supported --json field and none covers inline comments]` |
| 9 | The worktree hook denies frequently and has never let a main-tree write land. | `[verified: 517 denials / 235 sessions / per-session max 26; 2 bypass candidates examined — one false match, one correct recovery into a linked worktree]` |
| 10 | The sandbox cwd reset is high-volume and self-correcting. | `[verified: 1603 occurrences / 173 sessions; verbatim text "Shell cwd was reset to <path>"; max-count session re-prefixed `cd X && cmd` on every call after the first notice]` |
| 11 | Whether agents adapt *because they read the notice* or because `cd X && cmd` is already habitual is not determinable from transcripts. | `[unverified]` |
| 12 | Finding 5 (tracker rewrote link text after resolving a hand-written internal ID) has one confirmed occurrence and no second data point. | `[unverified]` — not corroborable here; tracker usage differs between machines |
| 13 | Finding 6 is a prose-quality failure with no mechanical transcript signature. | `[unverified]` — accepted as unmeasurable, not as unreal |
| 14 | The two-artifact collapse should be gated behind a pilot rather than landing now. | `[engineer-verified]` |
| 15 | `deny-private-project-refs.sh` has **no generic pattern** for person names, absolute paths embedding project names, or internal URLs/hostnames — those are reachable only as exact-literal whole-word entries a user hand-adds to `~/.claude/private-projects.md`. | `[verified: hook header line 28 names tool names, absolute paths, and structural fingerprints as undetected; grep confirms no person-name or hostname pattern anywhere in the file; the blocklist scan is `grep -iw -F` against user-supplied literals]` |
| 16 | Extracting the two scans into text-in/matches-out functions is a moderate refactor, and no standalone scan capability exists in the repo today. | `[verified: both scans are ~15 lines of inline shell reading a single $SCAN_TARGET global; repo-wide search for an existing scan CLI found only the hook and its docs]` |
| 17 | `gh api graphql --paginate` drives exactly one cursor, so it cannot page a three-connection query. | `[verified: gh 2.93.0 --help specifies a single `$endCursor` variable and one collection's pageInfo. Mechanism corrected at review time — a live run against PR 470 exited 0 with no error, paged `reviews` cleanly, and returned an empty `reviewThreads` node list from page 2 on. The symptom is data-dependent: what is invariant is that one shared cursor cannot track three independent connection positions, and that the failure can be exit-0-silent. An earlier draft of this row asserted a deterministic re-return/error split that did not reproduce]` |
| 18 | `error-mode-analysis` Step 1 contradicts itself: its prose sets the default scope to "the current project" while the command it demonstrates passes no `--projects` and therefore globs every project on the machine. | `[verified: error-mode-analysis/SKILL.md:13 shows a bare `buckets` invocation; SKILL.md:18 sets the current-project default; transcript-analysis.py:63 `_projects_glob` defaults to `"*"`]` |
| 19 | `buckets`' "Date range" column is descriptive of whatever the glob matched, not a filter, and `buckets` accepts no date flags at all — so Step 1's instruction to "identify the … date range under analysis" cannot be satisfied by the tool it names. | `[verified: transcript-analysis.py:424-468 computes the column as min/max over every matching record; `--since`/`--until` are absent from buckets' argparse block at 3038-3041, and SKILL.md:16 itself scopes those flags to `review-trace`]` |
| 20 | `gh issue comment` reached the same comment-posting endpoint as `gh pr comment` while matching neither the arm chain nor the write-signal list. | `[verified: against the pre-fix hook, `gh issue comment 5 --body hi` allowed while `gh pr comment 5 --body hi` denied; after the fix both deny, and `gh issue list` / `gh issue view 5` still allow]` — found by `/code-review`, not present in 472 |
| 21 | A lowercase `-X delete` executed as a real DELETE while reading to the gate as a non-write, because the mutating-method pattern matched only uppercase. | `[verified: against the pre-fix hook, `gh api repos/other/repo/issues/comments/12345 -X delete` allowed while the same command with `-X DELETE` denied]` |
| 22 | This repo has **no** per-worktree project directory under `~/.claude/projects/`; only the main-tree slug exists, because sessions start in the main tree even when their edits land in a worktree. | `[verified: of 36 project directories, exactly one matches this repo and it is the main-tree slug; the sole worktree-shaped directory on the machine belongs to a different repo]` — this reverses the premise of an earlier draft of Edit 3b, which assumed each linked worktree gets its own directory |
| 23 | A REST URL wrapped with a backslash line-continuation slipped every arm even after flattening, because flattening substituted a space where the shell removes the pair entirely. | `[verified: `gh api repos/foo/bar/pulls/1/\<newline>comments` allowed against the one-step flattening and denies against the two-step form; the argument-boundary and mid-path shapes both deny now, and two unrelated commands on separate lines still allow]` |
| 24 | The read-side decoy tradeoff is real and remains accepted: a *bare* `other/repo` token in a quoted body does not release a current-repo read, but a *full* `repos/OWNER/REPO/pulls/N/comments` path embedded in one does. | `[verified: both shapes run against the shipped hook — bare token denies, full path allows]` — this is the "both extractions scan raw command text" caveat the hook's own comment already names; the confinement means the most it can release is a read, so it is not closed here |

### Per-finding verdicts

| # | 472's claim | Verdict here | Disposition |
|---|---|---|---|
| 1 | Branch attribution from first record, tool-wide | **Confirmed, narrower** — 2 of 17 subcommands, not tool-wide | Issue 1 |
| — | Printed date range mismatched raw timestamps | **Not reproduced; misdiagnosed** — the tool is correct; Step 1's own guidance produces the misreading | **Fix here (Edit 3)** |
| 2 | Worktree hook fires repeatedly; low priority | **Confirmed, larger** — 517 denials vs the 4-8 reported | Issue 4 |
| 3 | Comment-fetch gate blocks read-only analysis | **Confirmed** — and the skill's own Step 3 is self-blocking | **Fix here (Edit 2)** |
| 4 | Sandboxed `cd` silently resets | **Confirmed, far larger** — 173 sessions vs the 3 reported | **Dropped** — see below |
| 5 | Hand-built cross-reference tag overwritten by ID resolution | **Uncorroborated, plausible** | Issue 5 |
| 6 | No review station covers quantitative/causal claims in narrative prose | **Accepted** — structurally sound, not mechanically measurable | **Fix here (Edit 1)** |
| 7 | Fresh-authoring the public artifact loses evidence density | **Accepted** | Issue 3, pilot-gated |
| new | `gh api graphql` bypasses the respond-pr gate entirely, same-repo included | **New — not in 472** | **Fix here (Edit 4)**; residual design question to Issue 2 |
| new | The cross-repo bypass releases writes on repo-shaped text found anywhere in the command, including a comment body | **New — not in 472**; undoes Edit 4, and pre-dates the branch for the REST arms | **Fix here (Edit 5)** |
| new | `gh issue comment` (and its `--edit-last`/`--delete-last` forms) was ungated entirely, on the same endpoint `gh pr comment` uses | **New — not in 472**, found by `/code-review`; pre-dates the branch | **Fix here (Edit 4)** |
| new | A lowercase `-X delete` executed as a write while the gate read it as a non-write | **New — not in 472**, found by `/code-review`; pre-dates the branch | **Fix here (Edit 4)** |
| new | A REST URL wrapped with a backslash continuation slipped every arm, the gap flattening was added to close | **New — not in 472**, found by `/code-review`; introduced by this branch's own flattening step | **Fix here (Edit 4)** |

### The calls worth arguing

**The date-range finding is a usage defect in this skill, not a bug in the
tool.** 472 flags "a separate date-range bug in the tool itself" (explicitly as
a flag, not an investigated diagnosis). There is no date bug: `buckets`
day-level output matched true per-record min/max exactly across the three
largest project directories, and no date anywhere in the tool derives from file
mtime, filename, or directory name. The tool reports exactly what it was asked
for.

What asked it wrongly is Step 1 of this skill. Its prose sets the default scope
to "the current project" (SKILL.md:18), while the command it demonstrates
directly above passes no `--projects` and therefore globs every project on the
machine (SKILL.md:13, ledger row 18). Under that glob `main` pools 26 unrelated
projects into a single row spanning five weeks — and the reported symptom was on
the default branch, the most collided name there is. Step 1 then compounds it by
naming "identify the … date range under analysis" as its job, which invites
reading the Date range column as the analysis window; that column is descriptive
of whatever the glob matched, and `buckets` has no date flags with which to bound
anything (ledger row 19).

So the whole reported symptom is reproducible from the skill's own instructions
with the code untouched, which makes the skill edit the lighter fix and the
correct one. Two heavier alternatives were set aside: adding `--since`/`--until`
to `buckets` (does not help — even a correctly dated, correctly scoped `main` row
still spans every session on that branch, so it is still not the delivery's
window), and adding a project column or glob echo to the output (a real
readability improvement, but it makes an ambiguity visible rather than removing
the instruction that walks into it — demoted to an optional note on Issue 1).

**472's proposed fix for Finding 3 is right but out of order.** Candidate fix #4
in the issue is to document the read-only escape hatch in the gate's own denial
text. Two distinct things sit behind the one fact that `gh api graphql` matches
none of the gate's regexes:

- A read-only GraphQL *query* is legitimate. It satisfies exactly what the gate
  exists to force — all three comment kinds, no truncation — and writes nothing.
- The same command surface also carries comment-*posting* mutations
  (`addComment`, `addPullRequestReview`), confirmed allowed same-repo by a hook
  dry run (ledger row 7). Those bypass the `[Claude Code]` attribution prefix,
  which exists to disclose AI authorship to **external viewers of a public PR** —
  so the harm is loss of authorship disclosure to third parties, not internal
  bookkeeping.

So the denial text should name the read-only form, as 472 proposes — but only
after the mutation path is gated. Do it in the other order and the denial
message becomes a signpost to an ungated write path.

That ordering argument was initially read as a reason to defer the hook
entirely. It is not: it constrains only the *denial-text* change, because
closing the mutation gap requires no denial-text edit at all. Edit 4 therefore
gates the mutations here, and the denial-text change stays with Issue 2, now
unblocked rather than blocked.

Severity calibration, so the issue is not over-read: this gate governs the
agent's own discipline using the repo owner's own token. No credentials, no
cross-tenant boundary, no data exfiltration. The gap pre-exists this PR and is
derivable by any reader of the public hook source — low severity, genuinely
real, worth fixing in the right order.

The deeper point belongs in Issue 2 rather than here: the gate blocks *fetches*
to force comprehensive reading, but the integrity concern it actually protects
is a *mutation* concern. Gating reads to enforce a write-side invariant is why a
read-only analysis procedure ends up denied by a gate never aimed at it.

**Why Finding 6 lands in CLAUDE.md while Findings 4 and 5 do not, given all
three are thin.** By volume the ranking is backwards: Finding 4 is the
best-measured of the three (173/725 sessions) and is dropped, while Finding 6 is
`[unverified]` (ledger row 13) and gets the most expensive placement in the repo.
Volume is the wrong axis. Findings 4 and 5 propose *workarounds for specific
environment behavior* — a sandbox `cd` reset, one tracker's ID-resolution — and a
workaround is only worth its permanent context cost if the behavior recurs and
generalizes. Both fail that: row 11 records that we cannot tell whether the `cd`
notice or a pre-existing habit produces the observed self-correction, and the
`cd` line is simply wrong for non-sandboxed stow users; row 12's tracker
behavior is one tracker on one machine. Finding 6 proposes a *general
discipline* — re-derive a number at the moment you write it — whose validity
does not depend on how often it was violated in one seven-day sample. It is also
unplaceable anywhere cheaper: it fires on any prose about tickets, PRs, or
handoffs rather than inside one skill's flow, and nothing mechanical can detect a
number that was recalled instead of re-derived, so neither a skill body nor a
hook can carry it. That combination — generalizable, cross-cutting,
non-enforceable — is what earns the CLAUDE.md slot, not the evidence count.

**Two findings were dropped from the fix set on evidentiary grounds**, both
after review pushed back on promoting thin data into globally-stowed prose:

- **Finding 4 (the `cd` reset note).** Ledger row 10 shows sessions self-correct
  on first sight of the notice, and row 11 records that we cannot tell whether
  the notice or a pre-existing `cd X && cmd` habit produces that. The benefit is
  therefore unverified while the cost — one line loaded in every session on every
  stow machine, including non-sandboxed users for whom it is simply wrong — is
  certain and permanent. Volume (173/725 sessions) measures how often the notice
  fires, not how often a rule would help beyond the self-correction already
  observed. Dropped; the observation lives in this triage record.
- **Finding 5 (tracker cross-references).** Folding it into Edit 1 was tidy —
  same recall-versus-re-derive failure shape, same review blind spot — but it
  would encode an N=1, single-machine, `[unverified]` observation about one
  tracker's ID-resolution behavior as a behavioral rule for every stow user.
  472 itself rates it Low-medium, self-caught, never reached a human. Routed to
  Issue 5 instead, to revisit on a second occurrence or cross-tracker
  confirmation.

Alternatives set aside for Edit 1's placement: a `sync-pr-description` checklist
entry (fires only for PR bodies; the reported miss was in a ticket description);
a dedicated pre-presentation checklist skill (nothing would reliably trigger
it). For Edit 2: a bypass marker letting `error-mode-analysis` through the gate
the way `/respond-pr` does — a heavier privileged-execution primitive than a
skill-body edit, and it would leave the read/write conflation untouched.

## Critical files

For instruction files the prose *is* the change, so each edit is drafted here
verbatim. **Both target files write each bullet as one unwrapped physical
line** — the blockquotes below are single lines regardless of how they render
here, and must be applied that way or the line-count accounting is wrong.

**Edit 1 — `claude/.claude/CLAUDE.md`, Finding 6.** Two changes in the
"Ground every choice" block under Engineering Judgment.

First, the lead sentence currently reads *"**Ground every choice.** Five
categories of decision require a primary-source citation before implementation,
not after."* Change to *"Six categories of decision require a primary-source
citation before implementation or publication, not after."* — "Five" goes stale
the moment a sixth bullet lands, and "before implementation" does not describe
prose written about already-finished work.

Shipping a stale count inside the rule that argues for re-deriving numbers
rather than stating them from recall would be this plan committing Finding 6's
own error. `claude/.claude/hooks/tests/test_doc_counts.py` exists to lock
claimed-versus-actual counts and has no entry for this sentence; register one in
the same change.

Second, append as a sixth bullet:

> - **Quantitative or causal claims in ticket, PR, and handoff prose** — re-derive each number and each cause-and-effect claim from the code, config, or query that produces it, at the moment you write it. A number verified in one artifact is not thereby verified in another. Prose is never part of a staged diff, so no review station catches a wrong one.

**Edit 2 — `claude/.claude/skills/error-mode-analysis/SKILL.md`, Finding 3.**
Replace lines 31-37 entirely. Line 31's tail currently reads *"Fetch all three
comment types with `--paginate` — skipping any one, or omitting `--paginate`,
silently truncates the signal"*, which contradicts the replacement; it cannot be
kept. The closer at line 39 ("Correlate each comment against…") stays untouched
and remains accurate.

> A distinct second source, not a subset of the transcript. Human PR reviewers comment on the PR itself; those exchanges never appear in the session transcript unless the AI was asked to read them. Fetch all three comment kinds in one read-only GraphQL round trip rather than three separate paginated REST calls:
>
> ```bash
> gh api graphql -f owner=OWNER -f repo=REPO -F pr=NUMBER -f query='
> query($owner:String!, $repo:String!, $pr:Int!) {
>   repository(owner:$owner, name:$repo) { pullRequest(number:$pr) {
>     comments(first:100)      { totalCount pageInfo{endCursor} nodes{ author{login} body } }
>     reviews(first:100)       { totalCount pageInfo{endCursor} nodes{ author{login} state body } }
>     reviewThreads(first:100) { totalCount pageInfo{endCursor} nodes{ comments(first:100){
>                                  totalCount nodes{ author{login} path body } } } }
>   } }
> }'
> ```
>
> Compare each `totalCount` against its returned node count, including the nested `comments` inside each review thread. Where they differ — say `reviewThreads.totalCount` is 38 but 20 nodes came back — re-run with `after:"<that connection's pageInfo.endCursor>"` and merge, repeating until the counts match. Do not reach for `--paginate`: it drives one cursor across the whole query, so with three connections the shared cursor diverges from at least one connection's own position — an observed run exited 0 while returning an empty node list for `reviewThreads` from the second page on. The symptom is data-dependent; a clean exit is not evidence the fetch was complete. Truncating one connection undercounts human-caught findings exactly the way fetching only inline comments does.
>
> This query reads. Posting any reply goes through `/respond-pr`, never this surface.

Two lines precede the fenced block and are part of the edit. A one-line note
that `-F pr=` is required — `-f` sends a string and the `Int!` variable rejects
it, confirmed live, and a reader who normalizes the asymmetric flags to all-`-f`
breaks the query. And a `<!-- HOOK_TEST_FIXTURE: fetch-pr-comments -->` marker
immediately above the fence, which is what lets the new gate test read this
command from the skill instead of carrying a second copy; the repo's
`extract_skill_command` helper resolves the block by that id.

Three deliberate choices in that text. The `pageInfo{endCursor}` fields are
required for the truncation instruction to be executable at all — without them
there is no cursor value to put after `after:`. The `--paginate` warning is a
grounded directive rather than a caveat, because ledger row 17 shows it fails
*silently* on two of three connections before erroring on the third. And the
closing line is a directive about this step, not a restatement of what
`require-respond-pr.sh` gates — that rationale lives in the hook's own header
and would go stale when Issue 2 changes the gate's coverage.

**Edit 3a — `claude/.claude/skills/transcript-analysis/SKILL.md`, Caveats.**
The mechanics belong to the toolkit skill, not its caller: `--projects`
defaulting to `*`, `buckets` grouping by bare branch name, and the Date range
column being descriptive are all properties of the tool. `error-mode-analysis`
Step 2 already sets the precedent — *"Invoke `transcript-narrative` and
`transcript-analysis` by name — do not restate their procedures here"* — so
inlining them in Step 1 would break that file's own rule. Append one bullet to
the existing Caveats list (~70 lines against a 200-line cap):

> - `--projects` defaults to `*` — every project on the machine. `buckets` groups by bare branch name with no project column, so an unscoped run silently pools every repo sharing a branch name (`main` is the usual casualty) into one row, and its Date range column describes whatever the glob matched rather than a bounded window — `buckets` takes no `--since`/`--until`. Scope `--projects` to the directory name under `~/.claude/projects/`, which is the session's *startup* cwd (not the current shell cwd) with `/` and `.` both replaced by `-` — derive it with `git rev-parse --path-format=absolute --git-common-dir` rather than `pwd` (see `error-mode-analysis`'s Step 1 for the full derivation, which is stable across worktrees); use `review-trace --since/--until` for a bounded window.

**Edit 3b — `claude/.claude/skills/error-mode-analysis/SKILL.md`, Step 1.**
Pointers only. Replace the bare invocation at line 13 with a scoped,
mechanically derivable one, and append one clause to line 18. Leave line 16's
flag inventory alone — it is already accurate.

> ```bash
> python3 ~/.claude/scripts/transcript-analysis.py buckets \
>   --projects="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)" | tr '/.' '-')*"
> ```
>
> `--git-common-dir` resolves to the main repo's `.git` regardless of which worktree the session started in, so the derived prefix is stable across worktrees; the trailing `*` then also picks up per-worktree project dirs and sessions started in a repo subdirectory — deriving from `pwd` instead breaks as soon as the session's *startup* cwd (which is what names the project dir; see `transcript-analysis`'s Caveats) is a linked worktree, since the current shell cwd at query time need not match it. The glob is prefix-based, so a sibling directory whose name happens to share the prefix would also match — for `buckets`, a report-scoping tradeoff, not a data-exposure one. Scope `--projects` to the current repo this way — an unscoped run pools every project on the machine.

Line 18 append:

> Take the analysis window from `review-trace --since/--until`, not `buckets`' Date range column (see `transcript-analysis`'s Caveats).

Two details in that command, each of which broke a draft that omitted it. The
`--projects=` equals form is required: every absolute Unix path begins with `/`,
which the transform turns into a leading `-`, and argparse reads a
space-separated value starting with `-` as another option flag and exits with
`expected one argument`. And the source is `git rev-parse --git-common-dir`,
not `pwd` and not `git rev-parse --show-toplevel`. The project directory is
keyed to the session's *startup* cwd; `pwd` reports the shell's *current* cwd,
and the two diverge routinely. `--show-toplevel` fails the same way, since in a
linked worktree the toplevel is the worktree. `--git-common-dir` alone resolves
to the main repo's `.git` from every worktree, so the prefix is stable
[verified: from both the main tree and this branch's linked worktree it yields
the same string, which matches the one project directory that exists for this
repo].

An earlier draft of this section argued the opposite — `pwd` over
`--show-toplevel`, on the theory that a repo using worktree-per-branch gives
each linked worktree its own project directory, so a repo-root-derived slug
would name a directory that does not exist. That is backwards for this repo.
`~/.claude/projects/` holds **only** the main-tree slug here; no worktree-derived
directory exists for this repo at all, because sessions start in the main tree
even when their edits land in a worktree. So it was the `pwd` form that derived
a nonexistent directory and returned zero rows, in exactly the layout this
repo's own worktree enforcement produces. The Verification section's "Done"
claim below did not catch this because it was run from the main tree, where
`pwd` coincidentally equals the repo root — the one place the bug cannot show.

The same correction reverses the wildcard call. An earlier draft required an
exact match, reasoning that `~/.claude/projects/` holds sibling entries whose
names are prefixes of one another, so a wildcard would re-create the
cross-project pooling this edit removes. But an exact match cannot cover a
session started in a linked worktree or a repo subdirectory, both of which get
their own directory when they do occur. The trailing `*` covers them; the
residual prefix-collision risk it reintroduces is bounded — for `buckets` it
over-includes rows in a report, which is visible in the output, rather than
silently returning nothing, which is not.

The line-18 clause is the load-bearing half. Scoping `--projects` removes the
cross-project pooling, but a correctly scoped `main` row still spans every
session ever run on that branch in that project — the column is never the
analysis window, and Step 1 must stop implying it is.

**Edit 4 — `claude/.claude/hooks/require-respond-pr.sh`, the GraphQL write gap.**
Add a fourth arm to the command-matching chain, matching comment write verbs on
`gh api graphql` only:

> ```
> PATTERN_GRAPHQL_MUTATION='gh[[:space:]]+api[[:space:]]+graphql[^|&;]*(add|update|delete|submit)[A-Za-z]*(Comment|Review)'
> ...
> elif [[ "$COMMAND_FLAT" =~ $PATTERN_GRAPHQL_MUTATION ]]; then
> ```
>
> Each pattern is bound to a name once and shared by the arm chain and the
> write-signal list, which put different questions to the same command. POSIX
> `[[:space:]]`, never GNU's `\s`: a class BSD grep does not honour makes an arm
> silently miss, and a missed arm falls through to allow. Bash `[[ =~ ]]` rather
> than `printf | grep` because this chain runs on every Bash tool call and each
> arm was two forks; the extractions below the chain keep `sed`, since they run
> only after an arm has matched and `sed`'s greedy last-match semantics differ
> from the leftmost match `[[ =~ ]]` gives.

Match on the mutation name, not on the string `mutation`: GraphQL's operation
keyword is optional in shorthand, so a body can mutate without containing it.
Shape is verb, any middle segment, object. The middle segment is load-bearing,
not decoration — `updateIssueComment` and `addDiscussionComment` put a word
between verb and object, so a pattern requiring them adjacent misses both. A
first draft did exactly that and the deny tests caught it.

`submit` sits alongside add/update/delete because `submitPullRequestReview`
publishes a pending review body — the GraphQL twin of the `gh pr review` that
arm 3 already gates, and the one form no verb-only pattern catches.

Two more evasions have to close with it, or the arm is decorative:

- **Multi-line commands.** `grep` matches within a line and `.` never crosses a
  newline, so any wrapped command slips every arm. Flatten `$COMMAND` into
  `$COMMAND_FLAT` once, before the chain. This is not a GraphQL fix — the same
  hole applied to a wrapped REST URL, so it belongs to all four arms. GraphQL
  merely made it likely rather than theoretical, since mutation bodies are
  conventionally written multi-line.
- **File-sourced query bodies.** `-f/-F query=@file` and `--input file` are
  documented `gh api` usage and carry the mutation text outside the command
  line entirely. A fifth arm denies them: a body the gate cannot read is one it
  cannot clear. The cost is a false deny on a file-sourced *read*, accepted
  because the two are indistinguishable from the command alone.

The deny pattern is deliberately loose about the middle segment, so the tests
pin the other boundary too: `minimizeComment`, `unminimizeComment`,
`resolveReviewThread`, `unresolveReviewThread`, and `addReaction` must stay
allowed. None authors comment text, so none needs the attribution prefix.

Deliberately asymmetric with the REST arms, which gate reads too. That read
gating exists to force a complete three-kind fetch; a single GraphQL query
already returns all three in one round trip, so it cannot produce the partial
fetch the REST rule prevents. Gating the query would re-break Edit 2.

Three further gaps closed here, all found by `/code-review` after the arm above
was drafted, and all the same bug shape one surface over:

- **`gh issue comment` was ungated entirely** — not by the arm chain and not by
  the write-signal list. It posts through `POST /repos/{o}/{r}/issues/{n}/comments`,
  the identical endpoint `gh pr comment` reaches and the one the hook's own
  retained comment already names, and its `--edit-last`/`--delete-last` forms
  rewrite and remove already-posted bodies `[verified: before the fix,
  `gh issue comment 5 --body hi` was allowed while `gh pr comment 5 --body hi`
  denied; after, both deny, and `gh issue list`/`gh issue view` still allow]`.
  It gets its own arm and its own write-signal entry.
- **Lowercase `-X delete` evaded the mutating-method check**, which matched only
  the uppercase spelling. `gh` normalizes the method before sending, so the
  lowercase form is a real executing DELETE that read to the gate as a GET
  `[verified: `-X delete` allowed, `-X DELETE` denied, on the same URL]`. The
  method check moves out of the pattern loop and folds case on its own, because
  the other patterns must stay case-significant — `repos/` path segments and
  GraphQL mutation names must not match in the wrong case.
- **A REST URL wrapped mid-path still slipped.** Flattening replaced every
  newline with a space, but a backslash-newline is a shell line continuation:
  the shell removes both characters and joins with nothing, so
  `pulls/1/\<newline>comments` executes as `pulls/1/comments` while the gate saw
  `pulls/1/\ comments` and missed. Flattening is now two steps — strip
  backslash-newline, then convert bare newlines to spaces — so the gate reasons
  about the command the shell will actually run.

The write-path denial text is new (Edit 5 adds a separate write denial; the read
path's text is untouched and stays with Issue 2). Its first draft pointed the
reader at `/respond-pr` for *any* denied write, including a write to another
repo — which `/respond-pr` cannot service, since it scopes to the current
branch's PR. That is a denial with no working remediation for the one case this
diff newly blocks unconditionally. The shipped text splits the two: current
branch's PR → run `/respond-pr`; any other repo or unrelated PR → stop and ask
the user.

**Edit 5 — the same file, confining the cross-repo bypass to reads.** Edit 4's
arm is undone by the bypass beneath it unless this lands with it, so the two
ship together.

An earlier draft of this plan asserted that a mutation "addresses its target by
node ID, so `COMMAND_REPO` stays empty and the command denies rather than
bypassing." That was reasoned, not run, and it is false. `COMMAND_REPO` comes
from scanning the whole command for a repo-shaped token, and a GraphQL
mutation's *comment body* is part of that command — so a body containing
`-R other/repo`, or a path shaped like `repos/other/repo/pulls/3/comments`,
populates it and releases the write. Verified by running the hook against both
payloads: each was allowed, while the same mutation with an ordinary body
denied.

The same substring evidence undoes the REST arms, and there it predates this
branch: a call that reads a cross-repo URL on one line and writes this repo's
PR on the next is released by the read's repo reference. Confirmed at the
merge-base, so not Edit 4's regression — but Edit 4's bug shape one arm over,
and one change closes both.

The fix is not tighter extraction. It is that **the bypass releases reads
only**. Its documented purpose is research on an external repo, and research is
a read; there is no cross-repo write it needs to permit, because the
attribution the gate protects is owed to readers of any public PR rather than
only this repo's. Confining it that way is also what makes substring evidence
safe to decide on: a decoy that can release only a read costs nothing, since
the read was never the thing being protected.

Write detection is asked independently of the arm chain, not folded into it.
The chain is `if/elif` and stops at its first hit, so a call that reads one
endpoint and writes another settles on the read arm — a first attempt set the
flag inside the arms and let exactly that decoy through, which the regression
tests caught. Writes are: `gh pr comment|review`, `gh issue comment`, a GraphQL
comment mutation, an explicit mutating `-X`/`--method` in any case, any field
flag — `gh api` issues POST whenever one is present, so the absence of `-X` does
not imply a read — and a request body sourced from a file.

That last signal is written against `gh api` generally, not `gh api graphql`.
Scoping it to graphql is the same narrow-scoping mistake the ledger's own
sibling-audit rule warns about: `gh api repos/{o}/{r}/issues/N/comments --input
body.json` carries no field flag and no `-X`, so it reads as a fieldless GET
while `gh` actually POSTs it. Verified allowed against the hook before this
widened `[verified: the command plus a trailing `# repos/other/repo/...` shell
comment was allowed; the comment is enough of a decoy because the extraction's
leading `.*` is greedy and so returns the *last* repo token in the command,
not the one being written to]`.

Two consequences worth stating. Cross-repo writes now deny where they were
allowed; five existing tests asserted the old behavior and flip to deny. And
`repos/{owner}/{repo}/...`, gh's own documented substitution for the current
repo, was reading as a cross-repo token and releasing same-repo access — the
placeholder is now rejected as repo evidence.

One more fail-open closes here. `COMMAND_FLAT` was built by `printf | tr`; a
subshell pipeline that fails to exec leaves it empty, every arm misses, and the
gate allows. Bash parameter expansion (`${COMMAND//$'\n'/ }`) cannot fail and
drops two forks from a hook that fires on every Bash call.

**Create — the triage record:**
- `.claude/plans/error-mode-findings-triage.md` (this file), shipping on the
  branch as the durable evidence trail behind the fixes and the issues.

**Also modified — `CHANGELOG.md`.** Repo convention for a change of this shape,
and load-bearing rather than ceremonial here: `claude/.claude/**` reaches stow
users on `git pull` with no re-install step, so the command shapes this branch
newly denies start denying on their machines without any action on their part.
The `[Unreleased]` entry names each newly-denied shape and what to do instead.

**Reuse, do not rebuild:**
- `transcript-analysis.py` subcommands for all corroboration; no new script.
- `deny-private-project-refs.sh`'s existing scan logic is the basis for Issue 3's
  hardened scrub (ledger rows 15-16) — extract `scan_tracker_ids` /
  `scan_blocklist`, do not write a new scanner.

**Explicitly not modified:** `transcript-analysis.py` (Issue 1).
`require-respond-pr.sh` is modified by Edits 4 and 5 — the matching chain and
the cross-repo bypass's read/write confinement. Its denial text for the read
path is untouched (Issue 2); Edit 5 adds a separate write-path denial rather
than rewording that one.

## Issues to file

Each carries its corroboration numbers inline, so the next session does not
re-derive them.

1. **`review-trace` and `judgment-pair` misattribute sessions to the first
   record's branch** — two sites, plus `review-trace`'s model attribution. Cite
   168/725 sessions and the 771-of-989 worst case, and note that `review-trace`
   is the subcommand `error-mode-analysis` Step 2 calls most load-bearing, so the
   bug degrades the analysis that found it. Add as an optional secondary item,
   clearly marked not-a-bug: `buckets` groups by bare branch name and echoes no
   glob, so a pooled multi-project row is indistinguishable from a single-project
   one. Edits 3a/3b fix the instruction that walks into this; a project column or
   a glob echo in the header would make it visible at the output too. Add a
   second: exact-match `--projects` scoping under-covers a repo using
   worktree-per-branch, since each linked worktree gets its own project
   directory. That is real but platform-specific to one worktree convention, so
   it cannot go in a globally-stowed skill body — it needs a tool-side answer.
2. **The respond-pr gate conflates reads with writes.** Edits 4 and 5 closed
   the concrete write gaps, so what remains here is the design question and
   472's candidate fix #4. The gate blocks *fetches* to force comprehensive
   reading, but the integrity concern it protects is a *mutation* concern —
   gating reads to enforce a write-side invariant is why a read-only analysis
   procedure ended up denied by a gate never aimed at it. Now that the write
   path is gated, documenting the read-only form in the denial text is
   unblocked and should ship here. Reassess whether the REST read arms still
   earn their false-positive cost, given a single GraphQL query satisfies the
   completeness requirement they exist to force.

   Edit 5 sharpens rather than answers this: the hook now *has* a read/write
   distinction, but only where the bypass consumes it. Narrow the framing before
   filing — the "second classification" complaint is now true only of the two
   REST arms. The three command arms (`gh pr comment|review`, `gh issue comment`,
   the GraphQL mutation) are literally the same named pattern in both the arm
   chain and the write-signal list, so for those write-ness *is* a property the
   arm carries. Only a REST URL genuinely needs an independent signal, because
   the URL alone cannot say which verb it will be issued with. If the
   reassessment above concludes the read arms should narrow, that is where the
   two collapse into one classification — do not pre-build it here.

   What remains is a drift risk rather than a live bypass: nothing structurally
   stops a future arm from being added to the chain and forgotten in the
   write-signal list, which is exactly how `gh issue comment` came to be gated
   in one and not the other. Carry a second acceptance criterion: **a test
   asserting every write-capable `PATTERN_*` also appears in
   `gated_write_patterns`.**

   Two integrity concerns are deliberately still out of scope. Mutations that
   suppress a comment without authoring text — `resolveReviewThread`,
   `minimizeComment` — can hide a human reviewer's feedback, which is an
   integrity concern distinct from attribution and not one this gate is shaped
   for. And the marker bypass proves that `/respond-pr`'s activation step ran,
   not that the attribution prefix was applied; no code path forces the prefix
   onto the eventual write. Both are design ceilings, not regressions.

   Carry one concrete acceptance criterion for the second: **a test asserting
   that a comment posted through `/respond-pr` carries the `[Claude Code]`
   prefix.** The whole gate exists to route writes into that skill, and nothing
   currently verifies the guarantee the routing is for — so the invariant is
   untested, which for a security control is indistinguishable from absent.
   It sits in Issue 2 rather than here because asserting it means exercising
   `/respond-pr`'s own write path, a different file than this branch touches.
3. **Collapse `error-mode-analysis` to one full-fidelity artifact, pilot-gated.**
   Extract the two scans from `deny-private-project-refs.sh` into a standalone
   scan mode, pilot against past reports, then remove the fresh-authoring step.
   Carry ledger row 15's narrow framing: person names, absolute paths, and
   hostnames have no generic pattern and are reachable only as exact-literal
   blocklist entries — the pilot must not be read as covering them. Carry 472's
   own counter-consideration too: roughly a third of that report's lines carried
   an identifier before scrubbing, several mid-quote, which is the missed-span
   risk the two-artifact design exists to prevent.
4. **Worktree-enforcement denial volume** — 517 denials / 235 sessions / max 26,
   zero main-tree writes landed. The hook is correct; the open question is
   whether a session-start reminder earns its context cost. File with the
   numbers and no prescribed fix.
5. **Tracker cross-references built from a recalled internal ID** — one confirmed
   occurrence on one machine, one tracker, `[unverified]` here (ledger row 12).
   File with that caveat explicit; revisit for a global rule only on a second
   occurrence or cross-tracker confirmation.
6. **`\s` in hook regexes is a GNU grep extension, not POSIX ERE** — **38
   occurrences across 7 hooks** `[verified: for each `claude/.claude/hooks/*.sh`,
   comment lines stripped, then `grep -o '\s'` counted;
   `require-ready-for-review.sh` 14, `require-stow-reminder.sh` 8,
   `deny-escaped-backticks-in-pr-body.sh` 4, and 3 each in
   `check-claude-md-length.sh`, `check-skill-length.sh`,
   `guard-settings-session-keys.sh`, `require-code-review.sh`]`. On a BSD grep
   (macOS default) `\s` is not guaranteed to mean `[[:space:]]`, and a gate arm
   that silently fails to match falls through to allow. CI runs `ubuntu-24.04`
   only, so no workflow exercises this; the first macOS stow user is the
   detector. Surfaced by `staff-platform-engineer` during Edit 5's review and
   **not verified on a BSD grep** — no BSD host was available, so file it as a
   portability risk to confirm, not a reproduced failure.

   `require-respond-pr.sh` is **no longer among them** and is excluded from the
   count above: `/code-review` flagged that this diff was authoring *new* arms
   in the non-portable idiom inside a control whose header claims a closed fail
   posture, so the whole file was converted to `[[:space:]]` rather than only
   its new lines. That leaves the file internally consistent, which is why the
   original "converting one file of eight leaves the rest inconsistent"
   rationale no longer applies to it. The remaining seven stay deferred on the
   narrower and still-valid ground that sweeping unrelated hooks is scope creep
   in this branch, not because a partial conversion would be worse than none.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` — the contributor venv lives at the
  main worktree root only, so the repo-root form does not resolve from this
  worktree.
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
  unchanged by this diff; run as a regression check.
- **New tests — gate/skill coupling, both directions.** In
  `claude/.claude/hooks/tests/test_require_respond_pr.py`: one case feeding the
  exact GraphQL command from Edit 2 through `require-respond-pr.sh` and
  asserting `allow`, and a parametrized case asserting `deny` across the
  **11** comment write verbs Edit 4 gates `[verified: counted from the
  parametrize block on `test_graphql_comment_mutations_denied` — 6 `add*`,
  2 `update*`, 2 `delete*`, 1 `submit*`]`. Keep them as a pair — the allow assertion
  alone passes equally against a hook with no GraphQL arm at all, so the deny
  half is what proves the arm exists. Fill the command's OWNER/REPO/NUMBER
  placeholders before the hook sees it: every deny regex requires a numeric PR
  number, so an unsubstituted command falls through regardless of its URL and
  would sit green against the REST regression the test exists to catch.
- **New tests — Edit 5's bypass confinement.** In the same file: the five
  cross-repo *write* cases move from the allow parametrization to a deny one,
  and the allow list keeps only the three cross-repo reads. Four decoy cases
  assert `deny` — a cross-repo read plus a same-repo write across two lines,
  the same shape with a `-X PATCH`, and two GraphQL mutations whose comment
  body carries `-R other/repo` and a `repos/.../comments` path. One case
  asserts `repos/{owner}/{repo}/...` denies. A fifth decoy covers the REST
  `--input` form. Two boundary cases join the deny list for signals the older
  parametrization never exercised: long-form `--method POST`, and `--field=`
  rather than `-F`.

  **The ablation behind the decoy cases proves a narrower thing than an earlier
  draft of this section claimed, and the claim is corrected here.** That draft
  said the five decoys had checked "discriminating power" for Edit 5's bypass
  confinement, on the evidence that disabling the `GATED_WRITE` deny flips them
  all to allow. The ablation result is real, but it establishes only that
  `GATED_WRITE` exists and fires — which `test_cross_repo_writes_denied` already
  covers with no decoy at all. It does not establish that the bypass is confined
  to reads.

  It cannot, and the reason is structural: `GATED_WRITE` denies and exits
  strictly before `COMMAND_REPO` is ever extracted, so **no command can exercise
  both** the write check and the cross-repo bypass. The two are mutually
  exclusive by control flow. A test named for "a decoy repo token does not
  release a write" is therefore not constructible against this design — every
  candidate case denies at the write check without the decoy mattering. The
  invariant that does hold, and the one those cases are re-scoped to pin, is
  that **write detection runs before and takes precedence over the cross-repo
  bypass**.

  The `[{}]` placeholder-neutralization mechanism the old name gestured at is
  reachable only on *reads*, and gets its own non-vacuous cases: a read carrying
  a decoy repo token in a quoted body, and partial placeholders
  (`repos/{owner}/actual-repo/...`, `repos/real-owner/{repo}/...`) alongside the
  existing fully-templated case.
- **New tests — the shapes `/code-review` found ungated.** Also in
  `test_require_respond_pr.py`, each pinning a gap that was live before this
  branch: `gh issue comment` write forms (`--body`, `--edit-last`,
  `--delete-last`, `-R other/repo`) deny, paired with `gh issue list|view|create`
  allowing so the arm is shown not to be over-broad; `-X delete`,
  `--method patch`, and `-X DeLeTe` deny, pinning the case fold; a single REST
  URL wrapped across a backslash continuation denies in all three placements
  (argument boundary, mid-path early, mid-path late), paired with two unrelated
  commands on separate lines still allowing, so flattening is shown to close the
  gap without over-fusing; and a cross-repo read denies in a repo with no
  `origin` remote, naming that unconditional deny as deliberate fail-closed
  behavior rather than an accident.
- **New entry — `test_doc_counts.py`** registering the "Six categories"
  sentence against the actual bullet count in the "Ground every choice" list.
- Length gates: the two `check-*-length.sh` hooks enforce the 200-line cap on the
  real files at `git commit` time; `test_check_claude_md_length.py` and
  `test_check_skill_length.py` unit-test the hook mechanism against synthetic
  fixtures and do not read these files. Post-edit both stay well clear, counted
  with `wc -l` against the applied edits rather than estimated — CLAUDE.md
  103 → 104, `error-mode-analysis` 121 → 140 (Edits 2 and 3b, plus the Step 1
  and Step 3 rewrites `/code-review` required), `transcript-analysis` 70 → 71
  (Edit 3a). All three stay well clear of the 200-line cap.
- **Edit 3 regression check.** Run the scoped `buckets` command from the revised
  Step 1 verbatim and confirm the `main` row's Date range narrows to this
  project's actual span — the unscoped run pools 26 projects across
  2026-06-19 → 2026-07-24, so the delta is the check. **Run it from a linked
  worktree, not only from the main tree.** The main tree is the one cwd where a
  `pwd`-derived slug and a repo-root-derived one coincide, so a main-tree-only
  run cannot distinguish a correct derivation from the broken one — that is
  precisely how the earlier `pwd` form passed this check while returning zero
  rows from a worktree. **Done:** the shipped `--git-common-dir` form yields the
  same prefix from the main tree and from this branch's linked worktree, and
  that prefix matches the single project directory that exists for this repo;
  the superseded `pwd` form yields a directory that does not exist.
- One-time manual check: run Edit 2's GraphQL query against a real PR in this
  repo, confirm it is not denied and returns all three comment kinds. The unit
  test above is what makes this repeatable; the live run confirms the query
  itself is correct against real data.
- `/skill-review` on the SKILL.md diff (hook-enforced) and
  `/ai-instruction-and-memory-files` on the CLAUDE.md diff, per
  `.claude/rules/review-pipeline-dispatch.md`. No agent file or plugin directory
  is touched, so no other per-file-type dispatch applies.

Review surface: three instruction files, one hook, and two test additions.
Risk concentrates in two places. The CLAUDE.md edit is globally stowed and
affects every session on every machine that pulls this repo. The hook edit is
an enforcement gate — a regex that over-matches would deny legitimate reads
(including Edit 2's own query), and one that under-matches leaves the write
path open; the allow/deny test pair is what bounds both directions.

## Out of scope

- `transcript-analysis.py` and `require-respond-pr.sh` changes — Issues 1 and 2.
- Person-name, filesystem-path, or hostname detection in the redaction hook —
  Issue 3's pilot.
- A global rule for either the sandbox `cd` reset or tracker cross-references —
  both dropped above on evidentiary grounds; Finding 5 continues as Issue 5.
- Re-running a full `/error-mode-analysis` on this machine. This pass tested
  seven existing claims against a second corpus; a fresh report would be a new
  sample, not a second read, and Step 3 is self-blocking until Edit 2 lands.
