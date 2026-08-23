# Silent branch-mismatch drops in cost reporting: surface them, and stop one from corrupting the permanent PR-cost ledger

## Context

PR #735's merged `## Cost` section reported $4.70 / 12.9M tokens for a
12+-subagent discovery-audit session, an implausibly low figure for that
workload — root-caused this session (see Findings below) to
`transcript-analysis.py cost --branches` silently dropping the driving
session's entire transcript because its own `gitBranch` not matching the filter after a mid-investigation
branch rename (see Findings §2), with no output indicating anything was
excluded. `pr-cost-section.sh` calls exactly this path
(`cost --this-repo --branches "$branch" --summary`,
`claude/.claude/scripts/pr-cost-section.sh:30`) for every PR's Cost section,
so the same silent gap recurs on any PR whose driving session's own recorded
branch differs from the PR's final head branch — a branch rename mid-work,
or a multi-branch session, not only the ephemeral-isolation case
`_attributed_branch` was built for.

Plan-review surfaced a structural sibling with the same root cause but a
worse consequence: `_compute_pr_cost_branch_totals`
(`transcript-analysis.py:7354-7403`, used by `pr-cost --record`) hits the
identical branch-mismatch, but instead of under-reporting a stdout table it
silently writes a **zero-valued row into the permanent, committed
`docs/pr-cost.md` ledger** — a worse blast radius (a corrupted durable
record, not an ephemeral report gap) that feeds downstream cost-trend
analysis. Per the engineer's explicit direction, this plan now fixes both
in two independent phases.

This plan adds visibility so a future occurrence surfaces in the tool's own
output instead of requiring an hour of manual `~/.claude/projects/` grepping
to root-cause, as this one did — and stops the ledger sibling from writing
a silently-wrong permanent record.

## Findings (root cause, confirmed this session)

1. The $4.70 total is arithmetically exact against `pricing.py`'s published
   Sonnet-5 rates for the token mix actually in that session's own
   transcript — not a pricing-math bug. 97% of the 12.9M tokens were
   `cache_read` at its documented 90% discount, which alone explains why a
   token-heavy, cache-reusing session prices cheaply.
2. The real gap: `_attributed_branch`
   (`claude/.claude/scripts/transcript_analysis/cost.py:51-78`) only
   carry-forward-resolves a record whose own `gitBranch` starts with the
   harness's `worktree-agent-` ephemeral-isolation prefix
   (`_WORKTREE_AGENT_BRANCH_PREFIX`, `cost.py:23`); every other record's
   `gitBranch` — including a real, non-ephemeral branch name — passes
   through unchanged. The driving session's own `gitBranch` was
   `claude-config-audit`, a real branch since renamed/consolidated into
   `discovery-audit` (PR #735's actual head branch) before the PR was
   opened; `--branches discovery-audit` correctly and mechanically excludes
   every one of that session's records, and `_cost_report`
   (`cost.py:403-406`) emits nothing indicating the exclusion happened.
3. This is a real design boundary, not a defect in `_attributed_branch`
   itself: real branch renames and multi-branch sessions are outside the
   ephemeral-isolation case it exists to handle. The gap is the silence at
   the call site, not the resolution logic.
4. Structural sibling: `_compute_pr_cost_branch_totals` reuses the same
   `_attributed_branch`/
   `_session_branch_index` pair (imported from `cost.py`, confirmed at
   `transcript-analysis.py:40-53` — Phase 2 below touches no `cost.py`
   code). Its caller looks up `branch_totals.get(branch)` for the PR's
   *current* head branch (`transcript-analysis.py:7795`); when that
   branch's activity was actually recorded under a since-renamed name, the
   lookup misses and falls through to `_new_pr_cost_agg()` — a zero-valued
   aggregate — which is then written into `docs/pr-cost.md` as if the
   account genuinely had no local activity for that PR.

## Approach

**Phase 1 — visibility in `cost --branches`.** Add a turn-count diagnostic
to `_cost_report`'s `--branches` exclusion path: tally excluded turns by
branch, gated on `branch_filter is not None`; print an aggregate-only line
under `--summary` and a full per-branch table otherwise, both routed
through the same `redact` flag every other identifying value in this file
already respects.

**Phase 2 — stop the silent zero-write in the PR-cost ledger.** When
`_compute_pr_cost_branch_totals`'s branch lookup misses but the scan saw
other branch activity at all (i.e., this account is not simply idle for
this PR), print a redaction-safe stderr warning before writing the
(unavoidably still zero-valued, for this run) row — visibility, not a
behavior change, matching `pr-cost`'s own "no raw branch name is ever
printed, no `--no-redact` escape hatch" policy (`transcript-analysis.py:
7541-7543`).

### Assumption ledger

```
Root: --branches (cost.py) and the branch_totals lookup (pr-cost --record)
both silently drop/misattribute records whose resolved branch doesn't
match what's being looked for, with no signal in either tool's own output
— a PR's Cost section can under-report by a whole session, and a PR's
permanent pr-cost.md ledger row can be recorded as zero-cost when it isn't.

Givens: --summary's fixed refusal of --by-project/--no-redact/--config-dir
(cost.py:191-201) and its public-PR-body destination (pr-cost-section.sh)
are pre-existing constraints this plan does not touch — beyond reach: they
gate an existing, reviewed redaction boundary; changing them is a
larger-scope decision than a diagnostic addition. pr-cost's own
zero-escape-hatch redaction policy for branch/repo values
(transcript-analysis.py:7541-7543) is likewise pre-existing and unchanged
by this plan — beyond reach for the same reason.

Row 1 [mechanism]: Phase 1 per-branch excluded-turn-count tally — anchors:
root — turn counts (not re-priced dollars) are the lightest primitive that
answers "which branch, how many turns were dropped" without re-running
_price_turn on discarded records for a figure the diagnostic isn't trying
to produce.
Row 2 [assumption]: auto-repricing every excluded turn to a dollar figure
was considered and set aside — anchors: row1 — it doubles the pricing work
done per excluded record for a number the diagnostic doesn't need; an
engineer who sees "N turns on branch X were excluded" already has what's
needed to rerun with the right --branches value and get real pricing.
Row 3 [assumption]: cost.py:171's own docstring already documents
--branches as filtering on _attributed_branch, not literal gitBranch
[verified: cost.py:171] — anchors: root — Phase 1's diagnostic is net-new
visibility, not a correction to that documented behavior.
Row 4 [mechanism]: the new diagnostic threads the existing `redact` flag
through both branch-name and session-identifier display, exactly like
every other identifying value in _cost_report (proj_label at cost.py:
445-460, session_id at cost.py:608) — anchors: root — one fix, not two ad
hoc patches, since both bypasses trace to the same gap: the diagnostic
must never be the first place in this function to print an identifying
value unconditionally. Under `redact=True` (the default, and
unconditionally true
under --summary since --no-redact can't coexist with it), distinct
excluded branch names print as deterministic sequential labels
("branch-1", "branch-2", ... assigned in sorted order, mirroring
project_repr_label's own deterministic-not-iteration-order-dependent
convention at cost.py:474) instead of raw names; raw names print only
under explicit --no-redact. excluded_session_ids is consumed only via
len() in both modes, in both markdown and plain rendering — never
iterated to print an individual id, matching _redact_session_id's
treatment of every other session-id display path in this file
(redaction.py:192-194).
Row 5 [assumption]: a raw excluded branch name is not safe to print even
under non-summary/local-CLI mode by default — anchors: row4 —
[verified: deny-private-project-refs.sh's blocklist matches only
tracker-ID-shaped strings or pre-registered codenames in
~/.claude/private-projects.md] deny-private-project-refs.sh (the
commit-time redaction hook) does NOT backstop an arbitrary, unregistered
codename-shaped branch name, so the diagnostic's own redaction split is
the sole control against that leak class, not one layer among several;
--summary's aggregate line is unconditionally safe (no branch names at
all), and non-summary's table is safe only because Row 4 gates it on
`redact` too.
Row 6 [assumption]: the "?" sentinel for a record whose attributed branch
resolves to None is an established display convention in this codebase,
not a new one this plan invents [verified:
claude/.claude/scripts/transcript-analysis.py:1732 docstring — "An event
whose branch or model cannot be resolved renders '?'"] — anchors: root.
Row 7 [assumption]: an aggregate count of turns excluded because their
branch attribution missed the corpus's own `--branches` filter is more
precisely described as counting distinct transcript files, not
"sessions" — anchors: row1 — a 12+-subagent investigation (the exact
shape that motivated this plan) spans that many separate jsonl
files/session_ids for one perceived investigation; the printed label says
"transcript files," not "sessions," to avoid implying a 1:1 mapping to
what an engineer perceives as one investigation.
Row 8 [mechanism]: Phase 2's fix is "warn, still write the row" — never
"skip the row" — anchors: root — the existing --all-accounts arm's skip
(transcript-analysis.py:7787) is safe there specifically because another
account can supply the real value; the single-account default arm has "no
other account to fall back to" (transcript-analysis.py:7785-7786,
pre-existing comment) — a genuinely branch-idle account for this PR is a
real, legitimate zero-cost case that must still record a row, so
distinguishing "genuinely idle" from "renamed/mismatched" reliably enough
to safely skip is a heuristic this plan does not attempt; visibility
without a behavior change is the correctly-scoped fix here, and it mirrors
Phase 1's own "diagnostic, not behavior change" shape.
Row 9 [assumption]: Phase 2's warning is safe to print with zero raw
branch names — anchors: row8 — pr-cost's own docstring already states this
policy applies to the whole subcommand ("no raw branch name or repo value
is ever printed... deliberately no --no-redact escape hatch",
transcript-analysis.py:7541-7543) — Phase 2's warning follows an
already-stricter existing rule, not a new one this plan invents.
```

## Critical files

**Phase 1** (`cost.py` + its own tests — dispatch independently of Phase
2; disjoint file set, confirmed no shared edit target):

- `claude/.claude/scripts/transcript_analysis/cost.py` — add
  `_print_branch_exclusion_diagnostic(excluded_turns_by_branch: dict[str,
  int], excluded_transcript_ids: set[str], *, redact: bool, markdown:
  bool = False) -> None` next to `_print_thread_table` (same dual-mode
  shape; see ledger Row 4 for the redaction behavior it must implement).
  Prints nothing when `excluded_turns_by_branch` is empty (no exclusions
  occurred) — locks a deliberate choice rather than leaving it implicit.
  In `_cost_report`: initialize `excluded_turns_by_branch:
  dict[str, int] = defaultdict(int)` and `excluded_transcript_ids:
  set[str] = set()` before the session loop (mirroring `unpriced_tokens`'s
  scope, cost.py:327); at the `attributed_branch not in branch_filter`
  branch (cost.py:405-406), before `continue`, increment
  `excluded_turns_by_branch[attributed_branch or "?"]` and add
  `session_id` to `excluded_transcript_ids`. Call the new print function
  once, gated on `branch_filter is not None`, at the same call site as
  `_print_thread_table` (cost.py:561, before the `if summary_mode: return`
  at 563-564) so it appears in both `--summary` and full-report output.
  Reuse: `defaultdict` (already imported), the `"?"` sentinel convention,
  `_print_thread_table`'s markdown/plain shape, a sequential-label helper
  matching `project_repr_label`'s deterministic-ordering convention for
  the redacted branch-name case.
- `claude/.claude/scripts/tests/test_transcript_cost.py` — extend
  `TestCostBranchFilter` (containing
  `test_branch_filter_is_per_record_not_per_session`, ~line 1732) with:
  (a) a per-record mixed-branch case reusing that test's own fixture shape
  (one session, records on two branches) asserting the excluded count is
  1 transcript / 1 turn, not 2 — guards against a session-level (rather
  than record-level) tally bug; (b) a zero-exclusions case
  (`branches="main"` where every record is already on `main`) pinning that
  nothing is printed; (c) a null-`gitBranch` case extending
  `test_null_git_branch_record_counted_unfiltered_excluded_under_branch_filter`
  asserting a "?" row with turn count 1 appears when `--branches main` is
  active; (d) a `--summary`-mode case with **two** distinct excluded
  branch names (e.g. `"feature-a"` and `"feature-b"`, both named
  explicitly in the assertion) asserting the aggregate line's counts and
  asserting neither literal branch name nor any redacted-sequential label
  collision appears in the summary output — comment the assertion: this is
  the sole redaction control against the PR-body leak path, since
  `pr-cost-section.sh` embeds this output verbatim into a public PR;
  (e) a non-summary,
  `redact=True` (default) case asserting branch names render as sequential
  `branch-N` labels, not raw names; (f) a non-summary, `--no-redact` case
  asserting the real branch name appears; (g) in both (e) and (f), assert
  no raw transcript/session id is printed anywhere in the diagnostic's
  output (only a count).
- `claude/.claude/scripts/tests/test_pr_cost_section.py` — **no new
  fixture.** Every fixture in this file replaces `transcript-analysis.py`
  wholesale with a hardcoded stub
  that never calls the real `cost.py`, so there is no "script's actual
  `--summary` output" to assert Phase 1's diagnostic against here — that
  would be coverage theater. `test_transcript_cost.py`'s cases above
  already cover the real logic at the correct layer, and this file's
  existing `test_prints_cost_report_verbatim_and_exit_zero` already covers
  the wrapper's pass-through behavior, which is genuinely all this file's
  job is.

**Phase 2** (`transcript-analysis.py`'s pr-cost section + its own tests —
dispatch independently of Phase 1; touches no `cost.py` code, confirmed at
Findings §4):

- `claude/.claude/scripts/transcript-analysis.py` — in the single-account
  write path (around `agg = branch_totals.get(branch) or
  _new_pr_cost_agg()`, line 7795), before that line: if `branch not in
  branch_totals and branch_totals` (the account's scan saw *some* local
  activity, just not attributed to this exact branch key — the
  discriminator that separates "renamed/mismatched" from "genuinely
  branch-idle for this PR"), print a stderr warning naming no raw branch
  values (per ledger Row 9) — e.g. "pr-cost: PR #{number}'s branch has no
  matching local corpus activity, but this account's scan attributed
  activity to N other branch(es) — this row may under-report if the
  branch was renamed; investigate locally with `cost --branches
  <branch>`." Still writes the row exactly as today (ledger Row 8) — this
  is visibility, not a behavior change.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — add a case
  around `cmd_pr_cost`'s `--record` path: a corpus with activity on branch
  `"old-name"` and a PR whose resolved head branch is `"new-name"`;
  assert the warning fires on stderr, the ledger row is still written
  (unchanged existing zero-valued-fallback behavior), and no raw branch
  name (`"old-name"` or `"new-name"`) appears in the warning text.

## Verification

```
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_cost.py claude/.claude/scripts/tests/test_pr_cost_section.py claude/.claude/scripts/tests/test_transcript_analysis.py
../../../.venv/bin/ruff check claude/.claude/scripts/transcript_analysis/cost.py claude/.claude/scripts/transcript-analysis.py
```
`test_transcript_analysis.py` is included because it contains a direct
`_cost_report(..., branches=...)` call (~line 15823) exercising the exact
path Phase 1 changes, and is where Phase 2's new test lives. Manually confirm Phase 1 against this
investigation's own finding: a fixture with one session on the target
branch and one on a different real branch name (not a `worktree-agent-*`
name) reproduces PR #735's exact shape and the diagnostic surfaces it.

## Out of scope

- Auto-repricing excluded turns to a dollar figure (see ledger row 2).
- Changing `_attributed_branch`'s resolution logic itself — both phases
  add visibility into what it already excludes/misattributes, not a
  change to how it resolves.
- Improving the `--all-accounts` arm's existing skip message
  (`transcript-analysis.py:7787`) to distinguish "genuinely no activity"
  from "activity present under other branch names" — same underlying
  ambiguity as Phase 2, but that arm already has a real fallback (another
  account) and a working skip; a message-wording improvement there is a
  smaller, separable follow-up, not part of this fix.
- A `docs/transcript-analysis.md` or `pr-description` SKILL.md note about
  this failure class — raised to the engineer at plan presentation as a
  candidate follow-up, not committed here without confirmation.
