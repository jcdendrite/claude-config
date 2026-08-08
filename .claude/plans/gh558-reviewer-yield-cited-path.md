# GH-558 (Part B): cited-path edit overlap for `reviewer-yield`, with a null control

## Context

**Goal:** make `reviewer-yield` report whether a reviewer dispatch *changed
anything* — did the session subsequently edit a file the reviewer cited — rather
than only whether the reviewer returned a verdict, and close GH-558.

This is Part B of the already-approved, already-merged plan at
`.claude/plans/gh557-denial-friction-census.md`. Part A (GH-557) shipped as
`bf3ea41` via PR #587; Part B's stated prerequisite ("branches from Part A's
merge commit") is satisfied. Part A touched `:13`, `:809`, `:874-1186`, `:4819`;
Part B touches `:337`, `:2406`, `:2455-2557` — **zero hunk overlap**, so Part B
is independently revertible.

The merged plan specifies Part B's design; this plan does not re-derive it. It
exists to discharge the decisions the merged plan left open, and to record the
implementation facts that constrain the design — several of which invert what the
merged plan assumed.

**Why now:** GH-558 is the last open child of the GH-554 workflow-efficiency
audit.

## Approach

### Decisions discharged

**1. The `Cited`-extraction coverage floor is 65%, hard.** Merged-plan assumption
12 required a numeric floor fixed *before* the spike. The anchor is the merged
plan's own measurement: 136 of a 257-dispatch sample carry a `Write` tool_use
with string content, so a structured-only extractor lands near that share and a
floor at or below it tests nothing about the inline contract. No negotiation band
sits under 65% — a floor with a "come back and discuss" range beneath it has an
effective value at the bottom of that range, set after the measurement, which is
the tautology assumption 12 exists to close.

That 136/257 figure is an **upper bound** on structured coverage, not an
estimate of it: it counts any `Write` with string content, including scratch and
plan files that are not the findings file. So the inline contribution 65%
actually demands is *greater* than the naive `(65−52.9)/47.1` arithmetic
suggests, and this plan does not quote a specific inline-share number, because
the denominator that would make one meaningful is unmeasured.

The abandon action is unchanged: below 65%, ship the verdict-bucket comparison
without the cited-path overlap and leave GH-558 open. **What the abandon branch
actually delivers, stated plainly:** the `--since` fold, a docs edit, and a
GH-558 comment carrying the spike's per-bucket numbers. Table 2 stripped of
`Cited`/`Active`/`Edited`/`Rate` is table 1's data re-arranged, so it is not
shipped at all on that branch. That is a tooling-only diff, and **on the abandon
branch no PR is opened** — the spike result is posted to GH-558 and the branch is
discarded. Opening a PR that closes nothing would recreate the exact "tooling
only, no visible result" gap GH-554 filed against #569.

**2. The rate's denominator is conditional on citation *and* on continued
editing.** Two conditions, both reported:

- `Cited` — dispatches yielding ≥1 extracted citation.
- `Active` — of those, dispatches after which the session recorded a code edit
  (see "The edit index," below, for exactly which edits count).
- `Rate` = `Edited ÷ Active`. Since `Edited` is a subset of `Active` by
  construction (both drawn from the same edit index — see "The edit index"),
  `Rate` cannot exceed 100%; this identity is load-bearing and stated once here
  rather than left to be inferred.

`Cited` alone would conflate parser failure with "the reviewer cited nothing,"
and since zero-finding reviewers structurally cite less, that alone manufactures
a spread. `Active` additionally targets the dispatch-position confound the merged
plan could only disclose: zero-finding dispatches cluster at end-of-work where
the session has stopped editing regardless of what was cited, and requiring a
post-dispatch edit removes exactly those — **provided the edit index itself
does not admit edits that aren't evidence of fix work.** It does not, by
construction; see below.

**3. Inter-arm coverage gap is an exit condition.** If per-bucket `Cited`
coverage differs between the findings-found and zero-finding arms by **more than
20 percentage points**, the spread is not reportable and GH-558 stays open on
that basis — comparing two arms measured by instruments of different accuracy is
not a comparison. Fixed here, before the spike, for the same reason the 65% floor
is.

**4. GH-558 closes on partial coverage, stated as an upper bound.** At `Active`≥10
per bucket — `Active` is N=10's actual referent, not raw dispatch count — a rough
read against GH-558's own 30-day *dispatch* volumes suggests perhaps 3 of 9
reviewer agent types (`staff-sdet`, `ciso-reviewer`, `staff-platform-engineer`)
clear the floor in the zero-finding arm; the other 6 print `insufficient`. This
figure is explicitly an **upper bound derived from dispatch counts, not from
measured `Active`** — `Active ≤ Cited ≤ Dispatches`, so the true clearing count
can only be equal or lower. The PR body states the measured count at write time,
not this estimate, and if it differs materially from "3 of 9" says so rather than
quoting this plan's number as the result.

### Implementation shape

**Two-phase loop, not streaming.** `cmd_reviewer_yield` (`:2501-2534`) currently
classifies each dispatch the moment it sees the tool_use. "Edited *after* the
dispatch returned" needs records later in the same list, so the loop becomes:
phase 1 builds the edit index and the `tool_result` timestamp map; phase 2 joins.
Two constraints an implementer will otherwise get wrong:

- **Phase 1 must not inherit the loop's `if rec.get("type") != "assistant":
  continue` filter.** The `tool_result` timestamps the ordering rule depends on
  live on **`user`-type** records (verified against live corpus records: a
  top-level ISO-8601 `timestamp` plus `toolUseResult`).
- **The edit index and the tool_result map are filtered to the same `--since`
  window as the dispatch query**, not left unfiltered. The join gate is `edit_ts
  > dispatch's tool_result_ts`, so an out-of-window dispatch is never evaluated
  and a pre-window edit can't match an in-window dispatch regardless — but the
  **subagent-read scope** (below) still needs the window applied explicitly, or
  it reads transcripts the query has no reason to touch.

`iter_sessions` is typed `Iterator[tuple[Path, list[dict]]]` (`:412-416`) — parent
records are already a materialized list, so phase 1's parent-side pass is a
second traversal of already-decoded data. **No new file I/O on the parent side.**

**The edit index — scope, cost gate, and the self-write exclusion.**

A parent-main-thread-only index misses the dominant fix path: this repo's
CLAUDE.md mandates routing implementation work to `code-writer`, and edits made
there are invisible to a parent-only scan. So the index also reads subagent
transcripts for dispatches occurring after an **in-window** reviewer dispatch
returned — bounded by `--since`, using `_index_subagent_dispatches` (`:2373`),
which already maps `toolUseId → path` for every dispatch with a `meta.json`, so
no directory walk is added.

**This is genuinely new I/O and the plan sets a hard, pre-committed cost gate for
it**, in the same style as the 65% floor and the 20-point gap: measured on the
live corpus, subagent transcript bytes are a large fraction of parent-transcript
bytes for a reviewed session (roughly comparable order of magnitude, re-measured
at implementation time against the actual `--since`-scoped read set, not the
unscoped figure this sentence would otherwise quote as fact).

**The gate isolates the new cost rather than judging total wall clock**, so a
failure identifies its own cause. Verification 7(a) runs the corpus command
twice: once with subagent-edit reads disabled (parent-only, matching the
pre-Part-B code path) and once with them enabled. **The disabled run is a
sanity check, not an exact match, against the inherited 13.5s baseline: that
figure is all-time and machine-wide, while this run is `--since 30d`, so the two
measure different corpora and are not expected to be numerically identical.**
State the disabled-run figure explicitly and flag it (do not silently proceed)
if it exceeds 2× the inherited baseline — a gap that large means the corpora are
too different for the delta comparison below to mean what it claims, and the
gate needs re-deriving against a same-window baseline before it can be trusted.
Otherwise, take the delta between the disabled and enabled runs. **If that delta
exceeds the inherited 13.5s baseline itself (i.e., subagent reads alone cost
more than the entire original scan), the fallback is to exclude subagent
edits from the index and report the exclusion as a named confound in the PR
body.** If the delta is small but the *enabled* run is still materially over
budget, that is evidence the new extraction pass (not I/O) is the cost — the
action is to profile the extraction path, not to re-apply the I/O fallback,
since that would treat a CPU problem as if it were the I/O problem the gate was
built to catch; this is named as an escalation back to the engineer rather than
an automatic action, because it means the gate's premise (I/O is the new cost)
was wrong for this corpus. This revises merged-plan assumption 9, which claimed
zero new I/O; Part B is no longer strictly zero-new-I/O, and the actual cost is
measured and gated as Verification item 7(a) requires (not item 4, which is
unrelated — extractor fixtures).

**The 13.5s figure itself is inherited, not re-derived here**: it is the merged
plan's own measurement (assumption 9 there: 354 parent sessions / 1,603 subagent
transcripts / 474 MB, machine-wide, single run — see ledger row K). This plan
does not re-measure it independently; Verification 7(a)'s comparison runs are
against that inherited number, and 7(a) states the run count and cache state
(warm, same machine) for its own two runs so the delta is reproducible rather
than a single noisy sample.

**The index must exclude reviewer-subagent writes entirely, not pattern-match
their targets.** Reviewer subagents write their own findings via `Write` tool_use
to `findings_path` (measured on the live corpus, unscoped and unwindowed —
re-measure at PR-write time per row K: `staff-sdet`, `ciso-reviewer`, and
`staff-platform-engineer` dispatches each show hundreds of such writes). In a
review fan-out, a zero-finding dispatch is routinely followed by *sibling*
reviewers writing their own findings files — satisfying "the session recorded an
edit" with zero fix work, reintroducing the exact end-of-work clustering `Active`
exists to remove.

Matching writes against an extracted `findings_path` string was considered and
rejected: no field on the dispatch tool_use, `meta.json`, or subagent transcript
carries that value directly — it exists only inside the parent's free-text
`prompt`, so recovering it would mean regexing prose to police path equality,
duplicating the exact fragility the extractor's own free-text problem already
has, and a repo-relative or `~`-prefixed prompt value would fail a raw-string
match against an absolute `Write` target regardless.

The simpler, correct rule: **any `Write` tool_use inside a subagent transcript
whose dispatch `subagent_type` is in the reviewer set (`_REVIEWER_PREFIX` /
`_REVIEWER_EXACT` / `_REVIEWER_YIELD_EXTRA_EXACT`, the same set
`cmd_reviewer_yield`'s own dispatch loop already classifies against) is excluded
from the edit index outright — not only writes matching a specific path.** This
needs no path comparison and no provenance question. It is sound because these
agents' documented contract is review-only: 8 of the 9 reviewer agents grant
`Read, Grep, Glob, Bash, Write`; `skill-fidelity-reviewer` grants the same minus
`Bash` — every reviewer's tool grant includes `Write` and excludes `Edit`, and
every reviewer agent's stated behavior (verified against all nine
`claude/.claude/agents/*.md` files) writes findings and nothing else. If a
reviewer agent ever writes something other than its findings
file, that write is excluded too — accepted, since reviewers are not tooled for
code edits as part of normal operation, and the alternative (trying to
distinguish a reviewer's rare non-findings write from its findings write) adds
exactly the fragility this rule exists to avoid.

**Single-pass scan, split by responsibility.** `_last_assistant_text` (`:2406`,
one call site at `:2525`, zero test references) is replaced by one scan returning
`(last_assistant_text, write_content_blobs)`. Only the `Write` blobs need the same
pass; inline citations come from the last assistant text the caller already holds.
A pure `_extract_cited_paths(text) -> set[str]` then runs over both, which is what
makes the path cases unit-testable. **Error contract:** the current
`""`-on-`OSError` behavior extends to "read error ≠ zero citations" — a read
failure is excluded from the `Cited` denominator, never entered as a legitimate
zero, **and the exclusion count is printed**, mirroring the existing
`meta_read_errors` line (`:2542`, `:2557`), so the 65% gate is judged against a
denominator the reader can see rather than one silently inflated by unreadable
transcripts.

**Normalization is lexical only — zero filesystem access.** No `Path.resolve()`,
no `os.path.realpath`, no `stat`. Verified empirically: `Path('/tmp/x').resolve()`
returns `/private/tmp/x` on macOS, so `resolve()` normalizes *away* from the
canonical form this plan picks, and it does so by traversing symlinks — `/tmp` is
itself a symlink. It would also chase stow symlinks, making the join key depend on
where each analyst's clone lives, and it adds a syscall per candidate path over
~375 MB of prose. An `OSError` from that traversal embeds the offending path in
its message, and `main()` (`:5468`) has no top-level handler, so it would print.

Ordered steps — **`~` expansion moved before relative-path resolution**, since a
`~`-prefixed candidate is neither absolute nor genuinely relative and the
original step order left `~/.claude/plans/x.md` unexpanded (`expanduser` is a
no-op on a non-leading `~`, and by the time the original step 4 ran, the path had
already been joined to `cwd` as if relative):

1. Strip a trailing `:line` or `:line:col` suffix.
2. Reject candidates with no directory separator. A bare `SKILL.md` or
   `settings.json` is ordinary reviewer prose; resolving it against `cwd`
   manufactures an in-repo path, and the false positives concentrate in the
   prose-heavy findings-found arm — biasing the spread, not merely adding noise.
   The spike counts discarded bare filenames per bucket.
3. Expand `~` (lexically, via `os.path.expanduser`). A candidate that resolved to
   an absolute path here skips step 4 entirely — it is not relative. A candidate
   still beginning with `~` after this step (the `~otheruser/…` form, which
   `expanduser` only resolves via a directory-service lookup) is discarded and
   counted alongside step 2's discarded bare filenames — left live, it would be
   joined to `cwd` as a relative path at step 4, manufacturing an in-repo false
   positive, the same failure class step 2 exists to prevent. Note this is the
   one place `expanduser` can touch the filesystem (`pwd.getpwnam` for the
   `~user` form); discarding rather than resolving that form keeps "zero
   filesystem access" true for every candidate this normalizer actually resolves.
4. Resolve `..` and relative segments against the **unstripped** `cwd`, for
   candidates that remain relative after step 3. This must precede worktree
   stripping: `../../../.venv/bin/pytest` — the idiom this repo's own CLAUDE.md
   prescribes — means three levels above the *worktree*, and resolving it
   against a stripped `cwd` silently changes which directory it names.
5. Collapse a leading `/private/tmp` to `/tmp`. macOS-only aliasing; an inert
   no-op on Linux, where that prefix cannot appear in a transcript. Carries a
   one-line comment naming the reason.
6. Strip `.claude/worktrees/<branch>/` **to fixpoint**, not once, so a nested
   worktree (an `isolation: worktree` agent under a worktree-anchored parent)
   doesn't leave a dangling second segment. **`<branch>` is assumed
   single-segment** — verified against this repo's own `branch-management`
   convention: every branch currently checked out under `.claude/worktrees/` in
   this repo is a single hyphenated segment with no `/`. A slash-containing
   worktree path (a hand-created branch violating that convention) is not
   losslessly decidable from the path alone with zero filesystem access — a
   fixed rule like "first component that re-enters the repo tree" breaks on a
   branch named `docs` versus a branch named `docs/x`, which produce identical
   path prefixes with different correct splits. The normalizer takes the first
   segment only in that case — a documented bias toward under-stripping, not a
   crash — and this is named as a known limitation rather than solved generally.

**The join key is a digest of the normalized path, not the path itself.**
`hashlib.sha256(normalized.encode()).hexdigest()[:16]` — deterministic across
runs and processes, unlike Python's built-in `hash()`, which is
`PYTHONHASHSEED`-salted and would make `test_redact_flag_is_true_no_op`
(`:1005`, byte-identical output across two runs) flaky. Table 2's row order does
not derive from key iteration in any case — it is sorted by agent type name
(reviewer-major, bucket-minor; see "Output," below) — but the digest choice
removes the hazard regardless of how ordering is implemented. Every downstream
use of the key is equality-only — set membership for the cited set, membership
test against the edit index — so a digest preserves every correctness property
needed. Path semantics are needed only *inside* normalization, which completes
before the key is stored; hashing makes "no path can print" a structural
property of the keyset, not an invariant maintained by review across every future
edit to a function holding thousands of real absolute paths. This does not close
the pre-existing `--projects` scope-header leak (see ledger row O and
Verification 6) — that leak is a different channel, never a join key — nor does
it replace the requirement that normalization stay lexical-only; both remain
independently necessary. It costs crash-time debuggability; the spike is
throwaway code and keeps raw paths for coverage scoring.

**Excluded from the cited set:** the dispatch's own `findings_path` value, and
plan files under either `~/.claude/plans/` or an in-repo `.claude/plans/` (a
`/plan-review` dispatch cites the plan the parent then edits — a guaranteed
self-match). Both predicates are named explicitly, not left as "plan files."
This is a separate exclusion from the edit-index reviewer-write exclusion above —
one governs what counts as *cited*, the other what counts as *edited*, and they
use different mechanisms (a path-normalized set membership check here, a
`subagent_type` classification there) because they answer different questions.

**Extraction pattern.** A single bounded, length-capped character class.
**Nested quantifiers are forbidden** — the natural shape `(?:[\w.-]+/)+[\w.-]+`
has a quantifier inside a quantified group and backtracks catastrophically on a
long non-matching slash run, which reviewer prose produces routinely via code
fences and tree output. `_DENIAL_HOOK_NAME_RE` (`:894`) is safe because it is one
flat bounded class; that is the property to copy, not merely the length cap. The
regression test for this (Verification 3) pins a concrete wall-clock ceiling
against a concrete input size, not an unbounded "must be fast" assertion —
otherwise it is unfalsifiable and, on a shared CI runner, a flake source rather
than a guard.

**Edit-tool coverage.** Reuse `_CODE_WRITE_TOOLS` (`:2894`). `MultiEdit` carries a
single `file_path` (plus an `edits` list) and is covered; `NotebookEdit` carries
`notebook_path` instead, so read `file_path` with a `notebook_path` fallback.

**Output.** Table 1 stays byte-identical. Table 2 is new:

```
AgentType  Bucket  Dispatches  Cited  Active  Edited  Rate
```

- **Ordering: reviewer-major, bucket-minor**, so an agent type's two comparison
  arms are adjacent — the within-agent-type spread is the reportable signal, and
  bucket-major ordering would separate the arms by the number of agent types.
  Sorted by agent-type name, not dict-iteration order:
  `test_redact_flag_is_true_no_op` (`:1005`) asserts byte-identical output across
  two runs, so any nondeterminism is a flake.
- **Units, pinned:** `Dispatches`, `Cited`, `Active`, `Edited` are integer counts,
  so `Rate = Edited ÷ Active` is recoverable from the row. `Rate` renders through
  an explicit format spec, `f"{rate:>6.1%}"` — verified empirically to always
  produce a single whitespace-safe token (`'  0.0%'`, `' 50.0%'`, `'100.0%'`;
  leading padding is stripped by `str.split()`, never interior).
- `insufficient` in `Rate` when `Active` < 10; `excluded` in `Cited`/`Active`/
  `Edited`/`Rate` on the `unclassified` row. Both single tokens.
- **`N=10`'s referent is `Active`**, the rate's own denominator — not bucket
  dispatches or `Cited`. Any other reading gates a proportion on a count that
  isn't its denominator, and Decision 4's closure arithmetic depends on getting
  this right.
- **Zero-`Active` renders a sentinel, not a `ZeroDivisionError`.**
- **`_table_cols` (`test_transcript_analysis.py:51-95`) needs three changes, not
  one.** (1) An `occurrence` parameter scopes the *search* to a table section —
  the line range from the Nth header match to the next blank line or header,
  **skipping one immediately-following dashed-rule line if present, rather than
  treating it as a terminator**. The real renderer prints the header then
  `"-" * len(header)` on the very next line (`transcript-analysis.py:2548-2549`)
  — table 1's own rule line — so a boundary that stops at the first dashed rule
  would end the section one line after the header, before any data row, and
  every `occurrence=1` call site would fail with zero rows matched. Table 2's
  own renderer follows the same header/rule/rows shape; pin that explicitly so
  the boundary rule's correctness doesn't depend on an unstated assumption
  about a table this plan is introducing. Table 1's trailing
  `(N meta.json files failed to parse, excluded)` line, when present, is
  preceded by a blank line (`:2557`), so the blank-line terminator already
  handles it once the rule line is skipped rather than treated as a stop.
  This fixes the header-uniqueness assert (`:83`) but not row-uniqueness
  (`:89`): table 2 has **two** rows per agent type (one per bucket) under
  reviewer-major/bucket-minor ordering, plus table 1's one row for the same
  agent type, so a bare `row_contains="staff-sdet"` matches three lines even
  once scoped to one table's section. (2) `row_contains` therefore also
  changes from `str` to `str | Sequence[str]` (all members must appear on the
  matched line) — a caller cannot express "match this agent type AND this
  bucket" as a single substring without hardcoding inter-column padding, which
  is the exact width-coupling the helper's docstring says it exists to avoid.
  Table-2 callers pass `row_contains=("staff-sdet", "findings-found")`.
  (3) `occurrence` defaults to `None` (no section scoping, current whole-output
  search behavior) — the file has roughly 90 other `_table_cols` call sites
  outside `TestReviewerYield`, and any other default either breaks them or
  silently changes what they match. Loosening the uniqueness assert itself is
  explicitly rejected — it would reintroduce the GH-363 silent-wrong-column
  class the assert exists to prevent. The 10 existing `_table_cols` call sites
  in `TestReviewerYield` are updated mechanically to pass `occurrence=1` for
  table 1, unaffected in behavior since a single-string `row_contains` still
  works under the widened type.

A single-table "column group" (the merged plan's literal wording) was weighed:
it dissolves the header collision and puts both rates adjacent, but has no place
for the merged plan's required per-bucket `unclassified` row. Two tables, with
the helper fixed rather than worked around, keeps both properties.

**In-file cleanup:** fold `cmd_reviewer_yield`'s inline `--since` parsing
(`:2483-2490`) into `_parse_since_nd_arg` (`:337`) — **call-site only, that
function's body unchanged**, or the revert stops being Part-B-local since other
subcommands share it.

### Lighter primitives considered and rejected

The extractor is the heaviest mechanism — free-text parsing over arbitrary prose.
Two structured alternatives fail:

1. **Join on the reviewer's own `Read`/`Grep` `file_path` values.** Zero parsing.
   Fails because *read* is not *cited*: a reviewer reads the whole diff surface,
   so overlap sits at ceiling in both arms. The verdict-bucket split cannot
   rescue a signal saturated on both sides.
2. **Parse only the `findings_path` Write body.** Deterministic and cheap. Fails
   the 65% floor by construction, its ceiling being the structured share.

## Implementation sequence

1. Fix the floor (**done: 65%**), the inter-arm gap (**done: 20 points**), and
   the subagent-read cost gate (**done: delta between with/without subagent
   reads must not exceed the inherited 13.5s baseline**) — no code.
2. Spike: candidate extraction scored over the corpus, corpus-wide **and**
   per-bucket, plus discarded-bare-filename counts. Compare to 65% and to the
   20-point inter-arm gap. Record in this file, redacted per Verification 1.
   Below either gate → abandon action, post to GH-558, no PR. Spike code
   discarded.
3. `_normalize_cited_path` + `_extract_cited_paths` as pure functions, with their
   unit tests. No command wiring yet.
4. Single-pass transcript scan replacing `_last_assistant_text`.
5. Two-phase loop: edit index (parent + `--since`-scoped subagent reads, with the
   subagent_type-based reviewer-write exclusion) and `tool_result` timestamp map.
6. Table 2 rendering; `_table_cols` `occurrence` + table-section scoping +
   composite row anchors, and the 10 call-site updates.
7. Docs, SKILL.md row, full suite, corpus run, measure the subagent-read cost
   gate.
8. `/code-review`, `/ready-for-review`, PR with `Closes #558`.

## Critical files

- `claude/.claude/scripts/transcript-analysis.py` — `cmd_reviewer_yield`
  (`:2455-2557`), `_last_assistant_text` (`:2406`), reviewer constants
  (`:2347-2370`), argparse (`:5121-5141`). New: normalizer, extractor, edit index
  (with the subagent_type-based reviewer-write exclusion), table-2 renderer,
  printed read-error count. **`cmd_reviewer_yield`'s docstring `--redact` paragraph
  (`:2476-2478`) must be corrected** — it currently justifies the no-op by
  "output is aggregate-only," which after this change sits above a function
  holding a large in-memory keyset. Under the digest key the claim becomes true
  by construction for that keyset specifically (not for the pre-existing
  `--projects` scope-header line, a separate, unfixed channel — see ledger row
  O); state that distinction so the next contributor adding a diagnostic print
  does not read the stale justification as covering everything.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — new unit classes
  for the normalizer and extractor; extend `TestReviewerYield` (`:817-1135`,
  15 tests). Helper changes: `_edit_use` (`:5485`, 4 call sites, all in
  `TestAuditRoutingSamples`) gains a `path=` parameter **and moves to the shared
  helper block at `:117-158`**, since it becomes cross-class. `_user_msg`
  (`:136`, 63 call sites) gains a keyword-only `ts` following `_asst`'s
  `if ts:` idiom. **`_tool_result` (`:144`) does not** — it returns a
  `tool_result` *block*, and the timestamp lives on the enclosing record. A
  `Write`-with-`input.content` builder is genuinely new (the one case the
  "parameter additions, not parallel builders" rule does not cover) and matches
  the inline shape already at `:3404`. A count-parameterized dispatch factory for
  the N=10 fixture is sanctioned by `test-conventions` §6.
- `docs/transcript-analysis.md` — `## reviewer-yield` (`:178-199`). Table-2 sample
  block plus a column-legend paragraph after the fence, per the `subagent-mix`
  (`:172`) / `skill-pair` (`:249`) precedent. **The legend states `Rate = Edited
  ÷ Active` explicitly, what `insufficient` and `excluded` mean, and — new —
  that `Active`/`Edited` count edits made by any subagent dispatched after the
  reviewer returned (in the same `--since` window), not only edits the reviewing
  session made itself in its own main thread.** This repo mandates `code-writer`
  delegation for implementation work, so most counted edits are delegated ones;
  a reader unaware of that would misread the column.
- `claude/.claude/skills/transcript-analysis/SKILL.md` — row at `:24`, currently
  `| Are reviewer dispatches producing real findings or mostly zero-finding
  passes? | `reviewer-yield --since 30d --redact` |`. Becomes: `| Are reviewer
  dispatches producing real findings, and do sessions then edit what was cited? |
  `reviewer-yield --since 30d --redact` |`. File is 79 lines; this is the only
  edit.

**Reuse:** `_index_subagent_dispatches` (`:2373`), `_parse_ts` (`:328`),
`_content_text` (`:90`), `_parse_since_nd_arg` (`:337`), `_REVIEWER_VERDICT_*`
(`:2368-2370`), `_CODE_WRITE_TOOLS` (`:2894`), `_DENIAL_HOOK_NAME_RE` (`:894`).

## Assumption ledger

**Root problem:** `reviewer-yield` measures whether a reviewer *spoke*, not
whether anything *changed*; raw cited-path overlap approaches ceiling in both
arms, so it needs a null control to carry signal.

**Givens** (fixed, outside this plan's reach):

| Given | Reason |
|---|---|
| Transcript record schema — `cwd`, `timestamp`, `toolUseId`, tool_use/tool_result shapes | Written by Claude Code; no artifact in this repo changes what the harness records. |
| macOS `/tmp` ↔ `/private/tmp` aliasing; normalizable **lexically**, not dissolvable | OS-level. The qualifier is load-bearing: "normalizable" without it invites `resolve()`, which is the bug. |

Reviewers emitting findings as prose is **not** a given — `claude/.claude/agents/*.md`
are in this repo. The merged plan declined to change that contract; inherited, not
reopened. See **Out of scope**.

**Mechanisms:**

| Mechanism | Justification | Anchors |
|---|---|---|
| Single-pass scan returning `(text, write_blobs)`, extraction split into a pure function | Avoids a second read of the same transcript; the split is what makes path cases unit-testable | row C |
| Lexical-only normalization, digest join key | `resolve()` normalizes the wrong way, adds syscalls, and leaks paths via `OSError`; equality is all the join needs | rows A, L |
| Subagent edits included in the edit index, `--since`-scoped, reviewer writes excluded by `subagent_type`, delta-based cost gate | Excluding subagent edits undercounts `Edited` arm-correlated; excluding reviewer writes prevents sibling reviewers' findings-file writes from re-inflating `Active`; the delta gate isolates the new cost instead of judging an unattributed total | rows H, M, P |
| Two tables + `_table_cols` `occurrence` + table-section scoping | A test helper's assertion must not name a user-facing column, and must not silently mismatch rows once a table has more than one row per entity | row D |
| Verdict-bucket split, N≥10 on `Active` | Inherited from merged plan (assumptions 6, 18); referent pinned to `Active` this round | root |

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| A | `cwd` is present on both parent and subagent transcript records. Subagent records carry the worktree path. Parent records carry whatever directory that session ran in — frequently a worktree, not the main checkout | `[verified: live corpus records]` |
| B | The script never reads a record's `cwd`; repo identity comes only from project-dir slugs (`:1780`, `:1791`) | `[verified: grep, no hits]` |
| C | `_last_assistant_text` has exactly one call site (`:2525`), zero test references | `[verified: grep across script + tests]` |
| D | `_table_cols` asserts exactly one header match (`:83`) and one row match (`:89`). `TestReviewerYield` spans `:817-1135` and holds 15 tests, of which 10 call `_table_cols` | `[verified: re-confirmed this round]` |
| E | `_parse_since_nd_arg` (`:337`) is semantically and error-text identical to `:2483-2490` | `[verified: side-by-side read]` |
| F | `_CODE_WRITE_TOOLS` (`:2894`) includes `NotebookEdit`; `MultiEdit` carries one `file_path` | `[verified: constant read]` for membership; `[from tool schema]` for `notebook_path` — a constant read cannot establish a tool's input shape, and the corpus contains 0 `NotebookEdit` and 0 `MultiEdit` records |
| G | The `Cited` floor is 65%, fixed before the spike | `[engineer-verified]` |
| H | The rate's denominator is `Active` (cited **and** followed by a qualifying edit); both `Cited` and `Active` print | `[engineer-verified]` |
| I | Extraction coverage will be lower in the zero-finding arm | `[unverified]` — not silently load-bearing: decision 3 gates on the measured inter-arm gap at 20 points. |
| J | `_user_msg` has 63 call sites; `_tool_result` 19; `_edit_use` 4, all in `TestAuditRoutingSamples`; no helper builds a `Write` with `input.content`, though an inline one exists at `:3404` | `[verified]` |
| K | Merged assumptions 6, 7, 9, 11, 15, 16, 17, 18 hold, **scoped to this repo's own project dirs unless stated otherwise**. **9 is revised by this plan** (subagent-edit reads are new I/O; the 13.5s baseline 9 established — 354 sessions / 1,603 subagent transcripts / 474 MB, machine-wide, single run — is inherited unchanged and the new cost is gated on the delta against it, not re-measured itself). **11 governs how corpus figures are described** — all-time local history, not GH-554's or GH-558's window, re-derived at PR-write time | `[verified: merged plan ledger]` |
| L | `Path.resolve()` maps `/tmp/x` → `/private/tmp/x` on macOS and traverses symlinks | `[verified: run on this machine]` |
| M | Subagent vs. parent-main-thread `Edit`/`Write`/`MultiEdit` tool_use counts are large and comparable in order of magnitude; the specific ratio is corpus- and scope-dependent (all-projects vs. this-repo-only measurements diverged materially between review rounds) | `[verified: re-measured this round; the earlier draft's specific figure did not reproduce and is not repeated here — re-derive fresh at PR-write time per row K]` |
| N | `.github/workflows/tests.yml:26-35` states "pytest alone consumes 72-92s"; this is a `timeout-minutes: 5` justification measured on `ubuntu-24.04` in CI, not a pytest budget and not a local-machine figure; no threshold for "materially" exists anywhere else in the repo | `[verified: grep across `.github/`, `docs/`, CLAUDE.md — one hit]` |
| O | `_resolve_project_scope` returns the `--projects` glob verbatim as `scope_label` (`:2079`) and `_print_resolved_scope` prints it (`:2097`) — pre-existing, shared by every subcommand, not created or widened by this plan | `[verified]` |
| P | Reviewer subagents write their own `findings_path` file via `Write`; counts are large enough (hundreds per agent type in an unscoped, all-projects measurement — not yet re-measured scoped to this repo or to a window) that including any reviewer's writes in the edit index would materially inflate `Active` | `[verified: live-corpus measurement this round; scope and window match row M's caveat — re-derive scoped and windowed at PR-write time, not quoted as a shipped figure]` |

## Verification

Run from the linked worktree; commands carry the `../../../.venv/bin/` prefix —
`requirements-dev.txt` pins `shellcheck-py` and a bare `shellcheck` resolves to a
different binary than CI uses. `pytest claude/.claude/`, `ruff check
claude/.claude/`, `scripts/list-shell-files.sh | xargs -0 shellcheck`. Failures
are reproduced against a merge-base worktree before being treated as in scope.

**Sequencing gate.** The spike runs *before* the extractor is designed; its code
is discarded and does not become the shipped extractor.

1. **Spike** — score candidate extraction over the corpus. Report corpus-wide
   coverage, **per-bucket** coverage, and discarded-bare-filename counts. Gates:
   corpus-wide ≥ 65%, inter-arm gap ≤ 20 points. Either failing fires the abandon
   action (post to GH-558, no PR). Record the result in this plan file, following
   this repo's full redaction standard (`docs/private-project-redaction.md`, not
   only the "aggregate figures, no project labels" subset) — aggregate numbers
   only, no paths, no project identifiers, no session ids.
2. **Corpus run** — `reviewer-yield --since 30d`. Re-check `Cited` against the
   floor; **if the shipped extractor lands below 65% where the spike cleared it,
   that is a defect in the extractor, not a re-run of the abandon decision** —
   fix it before shipping. Then, as a **named ticket exit condition, not a CI
   criterion**: confirm the arms separate per agent type, printing each cell's
   `Cited` and `Active`. If they do not separate at cells with adequate `Active`,
   or no agent type clears `Active`=10 in both buckets, GH-558 stays open — in the
   latter case with the stated reason "dispatch volume, not yield, is the limiting
   factor." Report the **measured** count of agent types clearing the floor
   against Decision 4's "~3 of 9" upper-bound estimate.
3. **Unit layer** (the load-bearing tests). `_normalize_cited_path` as a
   table-driven `(input, cwd) -> expected_key` suite, and `_extract_cited_paths`
   as `(text) -> expected_path_set`. These are pure string functions; testing them
   through a rendered CLI table reports "row match not unique" instead of "step 6
   ran before step 3." Cases: `~` expansion (including `~/.claude/plans/…`,
   pinning the reordered step 3-before-4); `/private/tmp` collapse; `file:line`
   and `file:line:col`; relative-vs-absolute; worktree-rooted vs repo-relative;
   absolute citation vs worktree-rooted edit from a *different branch of the same
   repo*; two repos sharing a relative suffix (must **not** match); nested
   worktree (fixpoint stripping); a slash-containing branch slug (pins the
   documented first-segment bias, not a crash); `..` escaping a worktree
   (resolved against unstripped `cwd`, before worktree stripping); bare filename
   (rejected); an unexpandable `~otheruser/…` form (discarded, not resolved via
   a filesystem lookup); no resolvable repo. Fixtures derive `$HOME` via
   `monkeypatch.setenv` — **no literal home-rooted path**, the very defect this
   plan defers in Out of scope. Plus a pathological-input timing case: a 100 KB
   adversarial input (a long, non-matching slash-heavy line, e.g. repeated
   `a/a/a/a/…` with no terminating token) must complete extraction in **under 1
   second, enforced by a hard timeout (e.g. `pytest-timeout` or a signal-based
   deadline on the test process), not a post-hoc `time.perf_counter()` delta**.
   Catastrophic backtracking on 100 KB does not return slowly — it hangs — so a
   measured-after-the-fact assertion can never fail on the exact failure mode it
   exists to catch; only a hard timeout that kills the test process produces a
   red result. The 1-second ceiling is order-of-magnitude separated from both
   the normal case (microseconds, per `_DENIAL_HOOK_NAME_RE`'s existing
   bounded-class idiom) and the catastrophic-backtracking failure mode it guards
   against (seconds to minutes, and here: unbounded) — chosen for separation,
   not tightness, so CI noise on a shared runner decides nothing.

   **An unreadable transcript is not a unit-layer case** — `_normalize_cited_path`
   and `_extract_cited_paths` are pure string functions and take no file, so an
   `OSError` path cannot be exercised here. It belongs in item 5's command-level
   fixtures, below.
4. **Corpus-shaped extractor fixtures** — 15–20 real reviewer-output strings
   sampled *during* the spike, **stratified across the spike's own per-bucket
   coverage distribution, including strings the spike extracted zero paths
   from**, each with its expected extracted-path set, asserted against
   `_extract_cited_paths` directly, plus one assertion of the aggregate
   extracted share over the sample. Stratification and the aggregate-share
   assertion are both required — per-string assertions alone would let an
   extractor rewrite keep the easy 15–20 green while corpus coverage collapsed
   below 65%, which is the exact gap this item exists to close. **Redaction at
   authoring time**, before landing in the repo, per this repo's full standard
   (`docs/private-project-redaction.md` — not only the "no `$HOME`/project
   slugs/session ids" subset item 1 restates): `$HOME`, project slugs, and
   session ids are replaced with the same sentinel scheme Verification 6 defines
   (`/SENTINEL-ROOT/SENTINEL-PROJ/…`), preserving separator count and
   `~`/worktree/`file:line` shape so the fixture still exercises the same
   grammar production output has — eliding instead of substituting would test a
   different grammar than the extractor actually faces. This is real reviewer
   prose, so it is also the one fixture set plausibly carrying the standard's
   always-on structural detectors (private-range IPv4, SSH key paths, long
   hex/UUID identifiers, internal-TLD hostnames), not only the three-element
   substitution above. `deny-private-project-refs` is a commit-time `Bash`
   PreToolUse gate with no standalone "scan this string" entrypoint — it has no
   separate invocation step to run here. It is satisfied in practice by the
   commit that lands these fixtures: the gate scans the staged diff, so authoring
   the fixtures to pass it is the actual requirement, verified when `git commit`
   runs, not a pre-commit action this item performs on its own. `$HOME` in these
   fixtures is monkeypatched, matching item 3's rule.
5. **Command-level fixtures** (only what the unit layer cannot show). Bucket
   routing; the `Cited`/`Active` denominator arithmetic — a zero-extraction
   dispatch stays in `Dispatches` and is absent from `Cited`, and a cited-but-no-
   subsequent-edit dispatch stays in `Cited` and is absent from `Active`, each
   asserted on the specific counts; `Active`=10 vs 9 boundary asserting a
   **specific rate value** (fix `Edited` at a known count, e.g. `Edited`=10 at
   `Active`=10 → `100.0%`), not merely `!= "insufficient"`; zero-`Active`
   sentinel; the `unclassified` row asserted by name; table-1 byte-identity;
   determinism across two runs. Ordering cases: edit preceding the dispatch's
   return (must not count), edit at exactly the `tool_result` timestamp (pins
   strict `>` vs `>=`), unparseable timestamp, dispatch with no paired
   `tool_result`, an unreadable subagent transcript following a reviewer dispatch
   (excluded from `Cited`, counted in the printed read-error line, distinguishable
   in assertion from the zero-extraction case — this is the command-level case
   the unit layer cannot express, since it needs a real unreadable file). Tool
   coverage: parent edit via `Write` and `MultiEdit`, not only `Edit`;
   `NotebookEdit` via `notebook_path`. Plus a `code-writer` subagent edit
   following a reviewer dispatch (must count), a *sibling* reviewer's own
   findings-file write following a zero-finding dispatch (must **not** count,
   pinning the subagent_type-based reviewer-write exclusion — note this test
   becomes unreachable if the cost gate's fallback strips subagent reads
   entirely; state in the test which branch it covers), the findings file of the
   dispatch itself (must not), a plan-file self-match under both `~/.claude/plans/` and in-repo
   `.claude/plans/` (must not), a multi-path citation with one path edited
   (dispatch counts once), and both contracts on one dispatch (deduped).
   **New table-2 tests anchor on `AgentType` with an explicit `occurrence`, plus
   a composite row match including the bucket name** — a bare `row_contains` on
   the agent type now matches three lines across both tables and both buckets.

   Dropped from the merged plan's list: "`findings_path` set but no `Write`
   emitted → lands in `unclassified`." That conflates two axes — `unclassified` is
   a *verdict* bucket assigned by `_classify_reviewer_verdict` from the last
   assistant text, so such a dispatch is `findings-found` with zero extracted
   citations. Covered instead by the zero-extraction denominator case above.
6. **Redaction, automated.** A fixture whose cited paths and `cwd` values are
   distinctive sentinels (`/SENTINEL-ROOT/SENTINEL-PROJ/x.py`), asserting the
   sentinel appears in neither stdout nor stderr. This replaces a manual eyeball
   check, which protects only the run it was performed on. **Note the scope
   header is a real, pre-existing exception:** `_resolve_project_scope` returns
   the `--projects` glob verbatim and `_print_resolved_scope` prints it (ledger
   row O), so a user-supplied glob containing a project slug already reaches
   stdout today on every subcommand, and the hyphenated slug form evades
   `deny-private-project-refs`'s home-rooted-path detector. This plan does
   **not** fix that — fixing it would touch a function shared by every
   subcommand, breaking the single-commit atomic-revert property this plan
   otherwise has, and the leak requires an operator to type a project-identifying
   glob themselves rather than being triggered by any content this plan
   generates. The assertion here is scoped to table bodies with the header
   exception named, rather than claiming a property the code lacks. **Filed as
   a follow-up issue in the same post-approval step as the redaction issue in
   Out of scope**, not yet assigned an issue number at plan-authoring time — the
   PR body records the number once filed.
7. **Wall clock, two separate obligations plus the new cost gate.**
   (a) Corpus run recorded against the inherited 13.5s baseline (merged plan
   assumption 9; not re-derived by this plan — see ledger row K) as **PR-body
   evidence only** — CI has no `~/.claude/projects` and can never re-derive it.
   **This is also where the subagent-read cost gate is measured and checked**,
   not Verification 4, which is unrelated (extractor fixtures): run the corpus
   command with subagent-edit reads disabled, then enabled, both warm-cache on
   the same machine, and state the run count. Gate on the **delta** between the
   two, not total wall clock — if the delta exceeds 13.5s, apply the fallback
   (exclude subagent edits, name the confound in the PR body); if the delta is
   small but the enabled run is still materially over budget, that implicates
   the extraction pass rather than I/O and is escalated to the engineer rather
   than resolved by the same fallback. (b) If measured pytest suite time
   falls outside the `72-92s` band stated at `.github/workflows/tests.yml:26`,
   update that band — measured from **the branch's own CI run**, not a local
   macOS measurement, since the band is a `ubuntu-24.04`-runner figure and a
   local number would misstate it. If the band moves, also re-check the
   aggregate worst-case sentence and the `timeout-minutes: 5` justification at
   `tests.yml:31-35`, which the band feeds into.
8. **Rollback.** No persisted state — no cache, checkpoint, marker, or migration;
   the only writes in the script are a `--out` path in a different subcommand
   (`:1774`) and an `mkstemp` (`:4942`). Part B ships as a **single commit** so
   the script, docs, and SKILL.md revert atomically. `claude/` is stowed as
   symlinks, so a revert reaches consumers on their next `git pull` — no other
   consumer action.

**Pipeline:** `/code-review` before commit; `/ready-for-review` before the PR;
`Closes #558`. The PR body carries the spike result, the floor and gap gates it
was judged against, per-bucket coverage, the measured closure count against the
~3-of-9 estimate, the measured subagent-edit I/O cost against its gate, and the
scope-header follow-up issue number.

## Out of scope

- **Changing the reviewer agents' output contract** to structured findings —
  would dissolve extraction entirely, but rewrites nine agent definitions and
  changes what every stow consumer's reviewers return. Declined and filed by the
  merged plan.
- **Printing unmatched paths** as a debug affordance — would make `--redact`
  silently non-no-op.
- **Finding-level join** (finding → diff hunk), GH-558's literal ask. Free-text
  finding-splitting is the fragile parse that misfired in #569.
- **The `--projects` scope-header leak** (ledger row O). Pre-existing, affects
  every subcommand, evades the redaction hook's slug detector, and is neither
  created nor widened by this plan — the fix-or-ask test for enforcement-gap
  findings resolves to "ask/defer," not "block," when the change under review
  doesn't touch the gap. Filed as a follow-up issue in the same post-approval
  step as the item below; Verification 6 names the exception rather than
  asserting around it.
- **Session-file boundary.** Edits after a resumed or compacted session land in a
  new `*.jsonl` that a per-session-file edit index misses. Same directional bias
  as the subagent gap, unmeasured; named here rather than left silent.
- **Case sensitivity.** The lexical key is case-sensitive; macOS APFS defaults to
  case-insensitive, so two citations differing only in case are one file but two
  keys. Symmetric across both arms, so it cannot fake a spread.
- **The `/home/jared/…` fixture paths** in
  `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` — home-rooted
  path with a real username in a public repo, pre-existing on `main`, unrelated to
  GH-558. Follow-up issue filed separately `[engineer-verified]`.
