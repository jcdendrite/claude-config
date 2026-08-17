# Fix `cost` crashing under multi-root scope: redact-map/session-iterator desync

## Context

Fix `transcript-analysis.py cost` so a multi-root, `--redact` (default) run
no longer crashes with `AssertionError: cost: redact map has no entry for a
project label under root N ... — the redact map's roots are out of sync with
the session iterator's roots`. This surfaced incidentally while ranking
sessions by cost (`cost --top`) against a real 6-root corpus. It currently
blocks any multi-root `cost` report against a corpus that happens to contain
a project directory whose only priced content is subagent-dispatched — and
`--no-redact` is not a workaround, since `cost` already refuses `--no-redact`
outright once more than one root is in scope. Reproduction during
investigation confirmed the underlying defect is not actually
multi-root-specific — the same corpus shape crashes at single-root scope
too — but single root at least has `--no-redact` as an escape hatch, which
is why multi-root is where this became a hard blocker. The intended outcome
is: the same corpus shape reports cleanly at both scopes, every project —
including a subagent-only one — gets a stable placeholder, and regression
tests pin the exact shape that used to crash.

## Approach

Root cause, confirmed both by reading the two enumeration paths side by side
and by live reproduction (below): `_build_redact_map`'s label enumeration
(`_sorted_distinct_proj_labels`,
`claude/.claude/scripts/transcript_analysis/redaction.py:73`) scans each root
via `iter_sessions(root, "*")` with `include_subagents` left at its default
(`False`, `transcript_analysis/corpus.py:88-91`) — a project directory whose
main transcript has zero parseable JSON records is skipped entirely
(`iter_sessions` only yields when `records:` is truthy,
`transcript_analysis/corpus.py:106-108`). `_cost_report`'s own per-session
scan (`transcript-analysis.py:4169`) instead calls
`_resolve_project_scope(args, "cost", include_subagents=True, roots=roots)`,
which merges a session's `<session_id>/subagents/*.jsonl` records into its
own (`read_session_file(jsonl, include_subagents=True)`,
`transcript_analysis/corpus.py:70`, reached via `_iter_glob_scoped_sessions`
at multi-root, `transcript_analysis/scope.py:261`, or directly via
`iter_sessions` at single root), so a project whose main file is empty but
whose subagent transcript has real priced content still yields a non-empty,
non-zero-total session. That session then looks itself up in the redact map
(`_redact_proj_label`, defined at `transcript_analysis/redaction.py:55`,
called at `transcript-analysis.py:4369`) and misses, because
`_build_redact_map` never registered its label — triggering the assertion at
`transcript-analysis.py:4380-4384`. Because the mismatched `include_subagents`
axis is inside `_sorted_distinct_proj_labels` itself (shared by both the
single- and multi-root branches of `_build_redact_map`), the defect and the
fix both apply at either scope; multi-root is only where it becomes a hard
blocker, since `--no-redact` is refused there but still usable as a
single-root workaround.

(Citations below reflect `transcript_analysis/` as a package —
`claude/.claude/scripts/transcript-analysis.py` was decomposed into
`transcript-analysis.py` plus a `transcript_analysis/{corpus,pricing,redaction,render,scope}.py`
package on `main` after this plan's original citations were written against
the pre-decomposition monolith; the mechanism and fix are unchanged, only
file/line locations moved.)

**Live reproduction** (two-root fixture: `root-a` with one normal project,
`root-b` with a project whose main `.jsonl` is 0 bytes and whose
`<session>/subagents/*.jsonl` carries one real priced turn):

```
$ python3 transcript-analysis.py cost --config-dir <root-b> --top 15   # (root-a as the active profile)
scanning root 1/2...
scanning root 2/2...
cost: account-1: scanned 1 transcripts, 0 skipped (unreadable)
cost: account-2: scanned 1 transcripts, 0 skipped (unreadable)
Corpus fingerprint: 244210e48437  (private-project labels are not comparable across a different fingerprint)
COST SOURCES (*; 2 roots)
Traceback (most recent call last):
  ...
AssertionError: cost: redact map has no entry for a project label under root 2 (label hash f028896907d4) — the redact map's roots are out of sync with the session iterator's roots
```

Pointing the same `root-b` at the *default* (single-root, no `--config-dir`)
scan path reproduces the identical assertion, worded for the single-root
case: `"...under the single scan root (label hash f028896907d4)..."` —
confirming the desync is in the shared enumeration, not anything
multi-root-specific.

This is exactly the desync the codebase's own test suite already documents
but works around rather than fixes: `test_transcript_analysis.py:8044-8053`'s
docstring for `test_worktree_agent_record_unresolvable_with_no_main_thread_branch_in_session`
explicitly avoids a wholly-empty main file specifically because it "would
instead exercise a pre-existing, unrelated desync between
`_build_redact_map`'s own (non-subagent-merged) scan basis and cost's
subagent-merged session iterator" — an unambiguous, in-repo confirmation of
this exact bug, independent of the fresh reproduction above.

**Fix**: make `_sorted_distinct_proj_labels` scan with
`include_subagents=True`, closing the enumeration gap at its source rather
than softening the assertion. `_build_redact_map` is shared by `cost` and
`audit-routing` (pinned by
`test_transcript_analysis.py:5905-5930`'s `test_shared_redact_map_binds_same_project_across_cost_and_audit_routing`),
so the fix applies once, upstream of both callers, and every other
`_build_redact_map` caller (`cmd_user_input`, `audit-routing`,
`cache-rebuild`) either doesn't merge subagents in its own session scan
(so gains a redact-map entry it will simply never look up — harmless) or
only uses the map for a fingerprint hash (`cache-rebuild`, which becomes
consistently subagent-inclusive along with everything else). No other
caller of `_build_redact_map`/`_redact_proj_label` asserts on a map miss
the way `_cost_report` does (grepped every call site;
`cmd_user_input` at `transcript-analysis.py:634`/`cmd_audit_routing` at
`transcript-analysis.py:3887` print `_REDACT_MAP_MISS_TOKEN`'s literal
string on a miss instead, which is unreachable for them since their own
scan basis already agrees with the pre-fix map), so this fix's blast radius
is exactly the one function that needs it.

**Alternatives considered:**
- *Parameterize `_build_redact_map`/`_sorted_distinct_proj_labels` so `cost`
  passes `include_subagents=True` and `audit-routing` keeps `False`.*
  Rejected: would make `cost`'s and `audit-routing`'s maps disagree on
  ordinal numbering for any corpus containing a subagent-only project,
  breaking the already-tested shared-map invariant above.
- *Downgrade the `_cost_report` assertion to skip/warn instead of raise.*
  Rejected per the brief's explicit instruction and the assertion's own
  documented rationale (`transcript-analysis.py:4374-4379`): a project
  silently dropped from a `--redact` report is a quieter version of the
  exact failure mode the assertion exists to prevent, not a fix for it.
- *Flip `iter_sessions`' own default to `include_subagents=True`.* Rejected:
  over-broad — a dozen other subcommands (`buckets`, `fail-seq`, `struggle`,
  `duration`, `review-trace`, `judgment-pair`, `pr-link`, `commit-gate`, …)
  deliberately call `iter_sessions`/`_resolve_project_scope` without
  `include_subagents=True` today; changing the shared default would alter
  their behavior for a bug scoped to one function.

### Assumption ledger

**Root problem:** `_cost_report` raises when a `--redact` run's
subagent-merged session scan finds a project the non-subagent-merged
redact-map enumeration never registered. No givens apply — the desync, the
assertion, and every enumeration path involved are all in-repo code this
plan can (and, for the assertion, deliberately chooses not to) change; there
is no vendor, protocol, or other-party constraint outside this plan's own
reach.

**Mechanism:**
- Change `_sorted_distinct_proj_labels`'s `iter_sessions(root, "*")` call to
  `iter_sessions(root, "*", include_subagents=True)`. `anchors: root`
  `[verified: transcript_analysis/corpus.py:88-91,106-108;
  transcript_analysis/scope.py:261; transcript-analysis.py:4169; live
  reproduction above]`
- Do not parameterize per-caller instead. `anchors: row1` `[verified:
  test_transcript_analysis.py:5905-5930]`
- Do not soften the assertion — within this plan's own reach to change, but
  deliberately declined (see Out of scope). `anchors: root` `[verified:
  brief §5; transcript-analysis.py:4374-4379]`
- `iter_sessions` is imported into `transcript-analysis.py` from
  `transcript_analysis.corpus` (`transcript-analysis.py:38`), not a function
  local to the file — corrects this plan's own earlier anchor note (written
  pre-decomposition), which had claimed it was local. Does not change the
  fix: `_sorted_distinct_proj_labels`, the one call site being edited, lives
  in the same module (`transcript_analysis/redaction.py`) that imports
  `iter_sessions` (`transcript_analysis/redaction.py:17`). `anchors: root`
  `[verified: transcript-analysis.py:38; transcript_analysis/redaction.py:17]`
- Update the now-stale docstring on
  `test_worktree_agent_record_unresolvable_with_no_main_thread_branch_in_session`
  (`test_transcript_analysis.py:8044-8053`), which currently documents this
  exact desync as an existing, unfixed condition — false after this fix.
  `anchors: row1` `[verified: test_transcript_analysis.py:8044-8053]`

## Critical files

- `claude/.claude/scripts/transcript_analysis/redaction.py`
  - `_sorted_distinct_proj_labels` (line 73): the fix — add
    `include_subagents=True` to its `iter_sessions` call, and extend its
    docstring by one sentence explaining why (a project whose only priced
    content is subagent-dispatched still needs a stable placeholder, since
    `_cost_report`'s own scan is subagent-inclusive).
  - No other production code needs to change — `_build_redact_map`,
    `_cost_report`, and every other caller are correct once the enumeration
    they share is correct.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - `TestCostMultiRootRedaction` (line 6534), alongside the existing
    `test_redact_map_miss_raises_instead_of_printing_unmapped_row`
    (line 6588, monkeypatch-based): add two new tests reproducing the real
    (non-monkeypatched) desync —
    1. Single-root: one project whose main `.jsonl` is empty
       (`_write_jsonl(path, [])`) with a real, priced subagent transcript
       (`_write_subagent_jsonl`); asserts
       `_mod._cost_report(_cost_args(), date(...), roots=[root])` no longer
       raises and the session row carries a real `private-project-N` label,
       never `_mod._REDACT_MAP_MISS_TOKEN`. Also asserts the raw
       (undelegated) directory-derived project label string is absent from
       captured stdout — pinning the actual security property `--redact`
       exists to guarantee, not just that some placeholder was chosen
       (ciso-reviewer S5).
    2. Multi-root: the same subagent-only project alongside a second,
       normal-project root, matching the brief's original reported shape;
       asserts no raise and a real `account-N/private-project-N` label.
    Reuse `_write_cost_root`, `_write_subagent_jsonl`, `_priced`,
    `_cost_args`, `_mod._redaction_ordinals` (for computing the expected
    ordinal) — all already used by neighboring tests in this class and
    `TestBuildRedactMapDirectUnit` (line 16852).
  - `TestBuildRedactMapDirectUnit` (line 16852): add a direct unit test on
    `_sorted_distinct_proj_labels`/`_build_redact_map`'s return value (the
    class's own existing convention, one level below the `_cost_report`
    end-to-end tests above), covering two edge cases in the function's
    changed contract that no end-to-end test exercises (staff-sdet's
    Foundation-concern finding):
    - A project whose subagent transcript has only *unpriced* turns (no
      priced usage) is still newly included in the label census post-fix,
      which can shift a different, alphabetically-later priced project's
      `private-project-N` ordinal even though the phantom project itself is
      never looked up (`_cost_report`'s `if session_total:` gate already
      skips zero-total sessions, so this case was never reachable by the
      crash pre-fix — it's a silent ordinal-renumbering risk, not a second
      crash site). Assert the sibling project's ordinal is the intended
      value.
    - Two subagent-only projects in the same corpus: assert both get
      distinct labels in the correct sorted order.
  - Update `test_worktree_agent_record_unresolvable_with_no_main_thread_branch_in_session`'s
    docstring (line 8044-8053) to drop the now-incorrect "pre-existing,
    unrelated desync" caveat.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` from the worktree — full
   suite, including the two new `_cost_report`-level regression tests, the
   new `TestBuildRedactMapDirectUnit` ordinal-edge-case tests, and the
   existing `test_shared_redact_map_binds_same_project_across_cost_and_audit_routing`
   test that pins the shared-map ordinal contract this fix must not
   disturb.
2. `../../../.venv/bin/ruff check claude/.claude/`.
3. Re-run the live reproduction fixture from the Approach section above
   (both the `--config-dir` two-root form and the single-root form) against
   the fixed code and confirm both report cleanly instead of raising.
4. `/code-review`, then `/ready-for-review` to open the PR (no merge — see
   engineer-authorization note below).

## Out of scope

- Softening or removing the `_cost_report` redact-map-miss assertion
  (`transcript-analysis.py:4380-4384`) — within this plan's own reach to
  change, but deliberately declined: it would trade a loud, correct failure
  for a silent, incorrect one (a project quietly dropped from a `--redact`
  report), the opposite of what the brief asks for. See "Alternatives
  considered" above.
- The unrelated `_dedup_turns_by_request_id` non-contiguous-run gap (a
  separate brief) — not touched here.
- `--summary` mode's own root resolution — it already refuses multi-root
  scope and never reaches `_build_redact_map`, so it cannot hit this bug.
- Widening investigation into `--this-repo`/`--projects` scoping's
  interaction with redaction — this bug reproduces under default scope with
  only `--config-dir` added, and nothing in the root-cause trace implicates
  those flags.
- A regression test on `audit-routing`'s own `_redact_proj_label` call
  (`transcript-analysis.py:3887`, unasserted on miss unlike `_cost_report`'s)
  — `audit-routing`'s own session scan stays `include_subagents=False`
  post-fix, so it isn't newly at risk from this change (ciso-reviewer S4).
  Worth a follow-up only if `audit-routing` ever gains its own
  subagent-inclusive scan.
- Merging the resulting PR — pending engineer authorization once opened and
  reviewed, per the brief's continuity-file header.
