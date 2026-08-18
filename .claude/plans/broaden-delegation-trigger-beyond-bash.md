# Broaden the delegation trigger beyond Bash

## Context

**Goal:** give the `subagent-delegation` rule a retrospective measurement of
read-heavy multi-site sweeps — the non-Bash shape its own prose already
covers — so that broadening the rule's in-session trigger becomes a
calibrated decision rather than an assertion.

`subagent-delegation/SKILL.md` states its principle in tool-agnostic terms:
an output test and a judgment test, applied to an *objective* rather than a
command. `claude/.claude/CLAUDE.md`'s "Default-consider delegation" bullet
names a "Read-heavy probe" explicitly. But the skill's one mechanically
enforced trigger is narrow — "when you notice you are about to run the
*second or third* `Bash` command toward the same question" — and Step 1's
header scopes to `Bash` as well.

Why now: the measurement side of the delegation rule is being built out.
`tool-call-compliance-enforcement.md` shipped `turn-shape` (Phase 0 as
PR #688, Phase 1 as PR #693) to measure the Bash-only trigger, and
pre-registered calibration floors for it. A broadened trigger changes what
population "the delegation rule" means, so it needs its own detector and its
own pre-registered floors before anything gates on it — not a silent
inheritance of the existing ones.

Intended outcome: a `read-sweep-shape` / `read-sweep-samples` subcommand pair
measuring read-sweep prevalence and dispatchability, verified against the
real corpus, with floors pre-registered for a later rating pass. The
SKILL.md trigger change is gated on that rating pass clearing the floors.

## Approach

Ship a **new subcommand pair** in `transcript-analysis.py` that reuses the
read-fanout and read-then-edit primitives `audit-routing-shape` already has,
but over its own population: turns whose tool calls are all `Read`/`Grep`/
`Glob`, across every model, with `Bash`-only turns excluded so the existing
delegation rule is not double-counted. A turn is flagged when it touches two
or more distinct sites and is *not* followed by an edit within three turns.

The central discovery driving this design: the brief's premise that a read
sweep "trips neither the skill's hard trigger nor `turn-shape`'s
retrospective measurement" is only half correct. `audit-routing-shape`
already measures this shape closely — it just does so for a different
population and a different question.

### Assumption ledger

**Root problem.** The delegation rule's enforced trigger and its
retrospective measurement both scope to `Bash`, while the rule's own stated
principle is tool-agnostic — so read-heavy multi-site sweeps are neither
triggered on in-session nor measured retrospectively, and no evidence exists
either way about how often they occur or how often flagging one would be
wrong.

**Givens** (fixed, beyond this design's reach):

| # | Given | Why it is a given |
|---|---|---|
| G1 | The transcript JSONL schema — `tool_use` blocks, their `name`, and their `input` field names — is vendor-defined | Vendor-imposed; the record shape is Anthropic's |
| G2 | No artifact records data-dependency between tool calls | Vendor-imposed; nothing encodes "this Read was part of one investigation", so any detector works on shape, never on intent. Inherited from `tool-call-compliance-enforcement.md` G2 |
| G3 | Retrospective measurement is bounded by the ~30-day transcript retention window | Vendor-imposed; `docs/cost-ledger.md` documents the same constraint |

**Mechanisms.**

| # | Mechanism | Justification |
|---|---|---|
| M1 | A new `read-sweep-shape` / `read-sweep-samples` subcommand pair rather than a third `turn-shape` streak or an in-place re-scope of `audit-routing-shape` | anchors: root. Over-powered-primitive check — three lighter primitives were considered and each fails: **(a) re-scope `audit-routing-shape` with flags** (lightest — the D1/D2/D3 machinery already exists) fails because its population includes `Bash` (`_CODE_READ_TOOLS`, line 3610), so relaxing its filters double-counts the shipped delegation rule, and because it would fuse the routing question ("should this have been Sonnet") with the delegation question ("should this have been dispatched") in one output; **(b) a third `turn-shape` streak population** fails because `_turn_shape_streaks` qualifies only `call_count == 1` turns (line 7909), so a single turn carrying eight `Read` blocks is structurally invisible — and the batching rule actively pushes sweeps into exactly that shape; **(c) a SKILL.md prose change alone** fails because it would write an uncalibrated rule into a skill that installs for every stow consumer |
| M2 | Site count as the within-turn breadth signal, mirroring D1's bucket ladder | anchors: row A6. Reuses `_count_read_file_paths`'s counting shape (line 7241) generalized past `Read`-only |
| M3 | A three-turn read-then-edit lookahead, mirroring D3 | anchors: row A7. The skill's own "stays inline" carve-out for `Edit`/`Write` sequences is what this operationalizes; building a fresh over-flagging guard when D3 already encodes one would be reinvention |
| M4 | Floors re-registered, not inherited | anchors: row A9 |

**Assumptions.**

| # | Assumption | Tag |
|---|---|---|
| A1 | `_turn_shape_streaks` qualifies only turns with `call_count == 1`, so no turn-shape streak population can see a batched multi-`Read` turn | **[verified: `claude/.claude/scripts/transcript-analysis.py:7909-7942`]** |
| A2 | `audit-routing-shape` already implements per-turn distinct-`Read`-path fan-out (D1), consecutive code-read streaks (D2), and a read-then-edit lookahead (D3), with `_D1_DISPATCHABLE_BUCKETS` starting at two files | **[verified: `transcript-analysis.py:7241-7258`, `:7291-7292`, `:7460-7492`]** |
| A3 | `audit-routing-shape` and `audit-routing-samples` are filtered to Opus turns only | **[verified: `transcript-analysis.py:7369`, `:7613` — `if _fam(model) != "opus": continue`]** |
| A4 | `_CODE_READ_TOOLS` includes `Bash`, so `audit-routing`'s code-read class is not a non-Bash population | **[verified: `transcript-analysis.py:3610`]** |
| A5 | The delegation question is already sampled for Opus turns — `cmd_audit_routing_samples`'s docstring reads "Designed for manual curation of which turns should/should not have been delegated" | **[verified: `transcript-analysis.py:7552-7562`]** |
| A6 | Build as a new subcommand pair reusing D1/D3 primitives | **[engineer-verified]** — chosen this session over re-scoping `audit-routing-shape` and over a third `turn-shape` streak |
| A7 | This population's dominant false-positive risk is legitimate inline reading, not batched-unrelated-calls as in the Bash rule's A13 | **[unverified]** — the expected FP shapes are named below, before calibration, but their relative frequency is exactly what the rating pass measures |
| A8 | Judgment spans are deliberately **not** excluded from this population | **[verified: `plan-it/SKILL.md` Step 3 — "Spawn `general-purpose` subagents in parallel when scope warrants"]**. `audit-routing-shape` excludes judgment spans because judgment work should not route to Sonnet. The delegation rule runs the other way: the judgment skills instruct exploration *be* delegated, so a judgment span is where the rule most applies, not least. **Disclosure cost of this choice:** judgment spans are disproportionately wide reads over whatever repo the session was in, so including them raises the share of sampled entries bearing private-project paths — handled by the Verification 7 handling rule, not by narrowing the population |
| A9 | Floors re-registered at per-rule precision ≥ 0.70 and inter-rater Cohen's κ ≥ 0.61; recall reported, not gating | **[engineer-verified]** — deliberately the same numbers as M8 of the parent plan, chosen so the two rules stay comparable and so a fuzzy detector that cannot reach 0.70 fails rather than has its bar lowered. Per that plan's own discipline, these are the engineer's to change **before** the rating pass, not after |
| A10 | This ships as a separate plan, not a Phase 3 of `tool-call-compliance-enforcement.md` | **[engineer-verified]** — that plan's row A9 scopes it to two rules "provisional", and its Phase 2 gating should not shift while its own calibration is pending |
| A11 | The SKILL.md trigger broadening is gated on this plan's rating pass clearing the floors | **[engineer-verified]** |
| A12 | `Bash`-only turns are excluded from this population | **[verified: `transcript-analysis.py:7909-7942`]** — those turns are the shipped delegation rule's population; including them would double-count and would make the two rules' precision figures non-independent |
| A13 | The brief's cited line anchors are stale (it cites 8761, 8876, 8601 for symbols now at 7909, 8024, 7749) | **[verified: `grep -n` against the current file]** — implementation should re-locate by symbol name, not by the brief's line numbers |
| A14 | `Grep`/`Glob`'s `path` is routinely relative while `Read`'s `file_path` is absolute, so raw string comparison would double-count one file | **[verified: `transcript-analysis.py:2850-2905`]** — `_normalize_cited_path`'s own docstring exists for this hazard class ("would make the join key depend on where each analyst's clone lives"), and `:2978` shows the `rec.get("cwd")` source it needs. The frequency of the mismatch in *this* corpus is **[unverified]** and is what Verification 6a measures |
| A15 | `read-sweep-samples` retains full, unredacted paths rather than truncating or relativizing them | **[engineer-verified — recorded as a deliberate choice, not inherited]**. Every flagged entry carries at least two real paths by construction of the predicate, making this output denser in path content than `turn-shape-samples`, whose `Bash` command text is only sometimes path-bearing. Full paths are retained because a rater cannot judge dispatchability without seeing what was actually read; the mitigation is the handling rule in Verification 7, not redaction |
| A16 | This plan adds ~400 lines to `transcript-analysis.py` while `.claude/plans/transcript-analysis-decomposition.md` is actively extracting that file into `transcript_analysis/`, with `audit-routing` — whose D1/D3 internals this plan reuses — queued as a future extraction phase | **[verified: `.claude/plans/transcript-analysis-decomposition.md:150-152`]**. Resolution below under "Relationship to the decomposition effort" |
| A17 | Test helpers `_read_use`, `_grep_use`, `_glob_use` already exist — in `test_transcript_analysis.py:9888-9898`, not `conftest.py` — and the existing `_grep_use(tool_id)` hardcodes `path: "."` with no parameter | **[verified: `grep -rn "def _read_use\|def _grep_use\|def _glob_use" claude/.claude/scripts/tests/`]**. Adding a differently-signatured `_grep_use` to `conftest.py` would be shadowed by the local definition throughout the region where the new tests belong |
| A18 | `read-sweep-samples` emits prior user prose as well as paths, and the commit-time redaction backstop only partly covers it | **[verified: `docs/private-project-redaction.md`]**. `deny-private-project-refs`'s always-on detectors catch home-rooted paths and UUID-shaped session IDs, but project and org *names* — the likeliest leak in user prose — are caught only when `~/.claude/private-projects.md` is populated, which is empty for a fresh contributor. Anyone running this against a mixed corpus should populate that blocklist first; the printed handling rule is the primary control, the hook is the backstop, and neither is sufficient alone |

### Relationship to the decomposition effort

`.claude/plans/transcript-analysis-decomposition.md` is actively extracting
`transcript-analysis.py` into a `transcript_analysis/` package, one command
group per PR, largest-first, with `audit-routing` among the queued groups
(`:150-152`). This plan reuses audit-routing's D1/D3 internals and adds a new
command group to the monolith, so the two efforts intersect (A16).

**Resolution: build in the monolith, and declare `read-sweep` its own
decomposition group.** That plan's Phase 1 explicitly keeps every `cmd_*` in
the monolith until its group's own phase runs, so building there is the
in-contract choice today, and it matches how the `turn-shape` family — this
plan's nearest sibling — was added. Building directly into the package
instead would front-run an ordering that plan set deliberately, and would
require importing back into the shim for the audit-routing helpers this
detector reuses, which is the circular-import shape its Phase 1 exists to
avoid.

What this plan owes the decomposition effort is a declaration rather than a
different destination: `read-sweep` is a distinct group, to be moved after
`audit-routing`'s phase lands, because it depends on helpers that phase
relocates. Whoever moves `audit-routing` must re-point this detector's
references to `_count_read_file_paths`, `_CODE_WRITE_TOOLS`,
`_ORCHESTRATION_TOOLS`, and `_normalize_cited_path` — the same
shared-helper-promotion step that effort's cost-family phase already
performed. Recording the dependency here is what makes that discoverable;
leaving it unstated is the actual defect.

### Detector definition

**Population.** Assistant turns, non-sidechain, carrying `usage`, after
`_dedup_turns_by_request_id`. A turn is a **read-sweep candidate** when it
has at least one `tool_use` block and every block's name is in
`{Read, Grep, Glob}`. No model filter; no judgment-span filter (A8);
`Bash`-bearing turns excluded (A12).

**Site count.** The size of the set of normalized path strings the turn
touches:

- a `Read` block contributes its `file_path`;
- a `Grep`/`Glob` block contributes its `path` argument when one is given;
- a `Grep`/`Glob` block with a `glob` but no `path` contributes its `glob`
  string — a glob-scoped search is narrower than an unscoped one, and two
  identical globs in one turn are one site rather than two;
- a `Grep`/`Glob` block with neither contributes a per-call unique sentinel —
  an unscoped search returns a set of sites by construction.

**Normalization.** Each contributed string is passed through
`_normalize_cited_path(candidate, cwd)` — the existing lexical normalizer —
with `cwd` taken from the record's own `cwd` field, as
`transcript-analysis.py:2978` already does. Without this, the same file scores
twice: `Read`'s `file_path` is absolute while `Grep`/`Glob`'s `path` is
routinely relative to the session cwd (A14). That helper returns `None` for a
candidate it discards, including one with no directory separator such as a
bare `.`; on `None`, fall back to the raw string so the call still contributes
exactly one site rather than vanishing.

Counting as a *set* is what makes `CLAUDE.md`'s own "Locate before a
whole-file read" instruction — grep inside one file, then read that range —
score 1 rather than 2, so following that rule does not trip this detector.

**A named limit.** A `Grep` scoped to a *directory* followed by a `Read` of a
file inside it scores 2, because a directory string and a file string are
different sites lexically. Collapsing that would require resolving directory
containment at analysis time, which the lexical-only normalizer deliberately
avoids. The shape is recorded as an expected false positive below rather than
special-cased.

**Flag predicate.** A candidate turn is flagged when site count ≥ 2 **and**
the read-then-edit lookahead does not return `inline-edit`.

**Lookahead.** For each candidate, scan forward up to three non-user turns:
an `_CODE_WRITE_TOOLS` turn yields `inline-edit`; an `_ORCHESTRATION_TOOLS`
turn yields `dispatched`; otherwise `neither`. Only `inline-edit` exculpates
— narrower than `_D3_DISPATCHABLE_CASES`, which treats `dispatched` as
dispatchable because it is answering the routing question, not asking
whether the rule was followed.

Path identity between the read and the subsequent edit is deliberately
**not** required. Requiring it would tighten exculpation and raise the flag
rate; the looser form mirrors D3 and is the lighter primitive. Revisit only
if calibration shows `inline-edit` is exculpating turns raters call
violations.

**Streaks.** A secondary distribution over maximal runs of consecutive
candidate turns, mirroring D2's bucket ladder, and resetting on a mid-session
`gitBranch` change as `_turn_shape_streaks` does.

### Expected false-positive shapes (named before calibration, per A13 of the parent plan)

1. **Comprehension read feeding the parent's own writing.** Reading two or
   three files to design against them, then writing prose rather than an
   edit — the skill's explicit carve-out, and invisible to the lookahead
   because no `Edit` follows.
2. **The read output is the deliverable.** The user asked what several files
   say; the raw content legitimately belongs in the parent's context.
3. **Scope still forming.** A read sequence where the parent is deciding
   *what* to change; the eventual edit may land more than three turns later
   and be missed by the lookahead window.
4. **Reviewing a diff across files.** Reading each changed file line by line
   is the artifact itself, not the investigation preceding it.
5. **Locate-then-read across a directory.** A `Grep` scoped to a directory
   followed by a `Read` of one file inside it — the named limit above. This is
   `CLAUDE.md`'s own "Locate before a whole-file read" idiom in its more
   common form, and unlike the single-file form the set semantics do not
   collapse it.
6. **Sentinel inflation from repeated unscoped searches.** Two unscoped
   `Grep`s in one turn score 2 by construction of the sentinel rule, even
   when they are one coherent search — grepping an old name and a new name
   during a rename, for instance. This one is guaranteed by the detector's
   own definition rather than merely likely.

Shapes 1, 3, and 5 are expected to dominate, which is the substantive
difference from the Bash rule, whose A13 names correctly-batched unrelated
commands as its dominant FP. If precision misses 0.70, these are the first
candidates for a sharpened predicate — shape 6 in particular has a cheap fix
available (treat all unscoped searches in one turn as a single site) that is
deliberately not applied pre-calibration, since it would also mask genuine
multi-question sweeps.

## Critical files

**Modify:**

- `claude/.claude/scripts/transcript-analysis.py` — add `_read_sweep_*`
  helpers, `cmd_read_sweep_shape`, `cmd_read_sweep_samples`, and argparse
  wiring. Place the new block adjacent to the `turn-shape` family.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — unit tests for
  the site-count set semantics (especially the grep-then-read-same-file case),
  the lookahead classifier, bucket boundaries, and branch-change streak
  reset; plus `fake_projects` integration tests asserting printed columns.
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py` — two
  subprocess smoke tests per subcommand, matching the pattern the two
  preceding subcommand-adding commits used: a `--help` exit-zero test
  asserting a distinguishing flag, and a seeded end-to-end run.
- No `conftest.py` change. `_read_use` / `_grep_use` / `_glob_use` already
  exist at `test_transcript_analysis.py:9888-9898` and are widened in place
  per step 1 (A17).
- `docs/transcript-analysis.md` — two new `## ` sections following the house
  template (Purpose / Flags / Sample output / When to reach for it),
  inserted after the `turn-shape` family.
- `claude/.claude/skills/transcript-analysis/SKILL.md` — two rows in the
  "Which subcommand to use" table. Table rows only; no prose or frontmatter
  change, so the skill's length and trigger contract are untouched.

**Reuse rather than reimplement:**

- `_dedup_turns_by_request_id`, `_price_turn`, `_parse_since_nd_arg`,
  `_resolve_scan_roots`, `_resolve_project_scope`, `_add_project_scope_args`
  — the whole scope/pricing preamble is shared by every subcommand.
- `_CODE_WRITE_TOOLS` (line 3609) and `_ORCHESTRATION_TOOLS` (line 3608) for
  the lookahead classifier — do not redefine these sets.
- `_count_read_file_paths` (line 7241) as the shape to generalize for site
  counting.
- `_DO_NOT_PUBLISH_BANNER` and `_print_resolved_scope(..., file=sys.stderr)`
  — the samples subcommand emits real paths and session IDs, so it must
  stamp the banner and keep the scope line off stdout, exactly as
  `turn-shape-samples` does.
- `_table_cols`, `_priced`, `_asst`, `_write_jsonl`, `fake_projects` from
  `conftest.py` for tests.

## Implementation steps (Phase 1)

Each step is one target and one change. Re-locate every symbol by name — the
brief's line anchors are stale (A13), and the numbers cited in this plan will
drift as the file changes.

1. **`test_transcript_analysis.py` — widen the existing `_grep_use` and
   `_glob_use` in place; leave `_read_use` alone.** They already exist at
   `:9888-9898` (A17). Give `_grep_use`/`_glob_use` optional `path` and
   `glob` parameters defaulting to today's hardcoded values, so every
   existing `audit-routing-shape` call site keeps its current behavior.
   Do **not** add these to `conftest.py`: the local definitions would shadow
   the imported ones throughout the region where the new tests belong, and
   the new tests need a variable `path` that today's `_grep_use(tool_id)`
   cannot express.

2. **`transcript-analysis.py` — add `_read_sweep_site_count(content, cwd) -> int`.**
   Returns the size of the normalized path-string set defined under "Site
   count" above, calling `_normalize_cited_path(candidate, cwd)` per
   candidate and falling back to the raw string when it returns `None`.
   Place it beside `_count_read_file_paths`, whose counting shape it
   generalizes past `Read`-only. Do not modify `_count_read_file_paths`
   itself — `audit-routing-shape`'s D1 is its only caller and depends on its
   current `Read`-only semantics.

3. **`transcript-analysis.py` — add `_read_sweep_is_candidate(content) -> bool`.**
   True when the turn has ≥1 `tool_use` block and every block name is in a
   new `_READ_SWEEP_TOOLS = frozenset({"Read", "Grep", "Glob"})`. Define that
   set rather than reusing `_CODE_READ_TOOLS`, which includes `Bash` (A4/A12).

4. **`transcript-analysis.py` — add `_read_sweep_lookahead(session_turns, idx) -> str`.**
   Returns `"inline-edit"` / `"dispatched"` / `"neither"` per the Lookahead
   rule, reusing `_CODE_WRITE_TOOLS` and `_ORCHESTRATION_TOOLS` rather than
   redefining either set.

5. **`transcript-analysis.py` — add `_read_sweep_session_turns(records, since_ts, session_id)`.**
   Build the per-turn record list: dedup via `_dedup_turns_by_request_id`,
   drop sidechain / non-assistant / usage-less / pre-`since_ts` records, and
   carry `branch`, `dollars` (via `_price_turn`), `is_candidate`,
   `site_count`, and enough class information for the lookahead. Source `cwd`
   from `rec.get("cwd") or ""` for step 2's normalizer. Model the function on
   `_turn_shape_session_turns` but do not modify it — and carry over its
   `rec.get("gitBranch") or ""` branch normalization, not
   `rec.get("gitBranch", "")`; that distinction was a real regression there,
   visible only on an explicit `null`, and step 10 pins it.

6. **`transcript-analysis.py` — add bucket helpers and constants.**
   `_READ_SWEEP_SITE_BUCKETS` and `_READ_SWEEP_STREAK_BUCKETS` mirroring
   `_D1_BUCKETS` / `_D2_BUCKETS`, plus `_read_sweep_site_bucket` and
   `_read_sweep_streak_bucket`.

7. **`transcript-analysis.py` — add `cmd_read_sweep_shape`.** Print three
   dollar-weighted tables: site-count distribution, streak-length
   distribution, and lookahead-case distribution, plus a flagged-share
   summary line. **Denominator, stated so it is not invented at
   implementation time:** flagged share is flagged-candidate dollars ÷ *all
   candidate* dollars, not ÷ all turns and not ÷ flagged turns. Print both
   the dollar share and the turn-count share on that line, since a
   dollar-weighted share alone hides a long tail of cheap violations. Plain
   text, matching `cmd_turn_shape`'s manual column formatting; emit the same
   unpriced-turn caveat line when any turn is unpriced.

8. **`transcript-analysis.py` — add `cmd_read_sweep_samples`.** Emit flagged
   turns as plain text with `_DO_NOT_PUBLISH_BANNER` on stdout and stderr,
   the scope line via `_print_resolved_scope(..., file=sys.stderr)`, and
   `--sample` / `--seed` handling copied from `cmd_turn_shape_samples`.
   Per sampled turn emit the tool-call lines, the verbatim prior user
   message, and the first line of the model's own response — the context
   Verification 7's rater note requires, and the same context
   `cmd_audit_routing_samples` already emits for its rating task. This widens
   the disclosure surface beyond paths to user prose (A18), which is why the
   handling rule below is printed rather than assumed.
   Write no file. Include in the banner region a one-line handling rule:
   output from a real-corpus run is never pasted into a commit, PR body,
   review comment, or plan file — only counts and label sets leave the
   terminal. The rule belongs in the tool's own output because that is the
   only place the person about to paste is looking.

9. **`transcript-analysis.py` — wire both subparsers.** `_add_project_scope_args`
   plus `--since` on both; `--sample` (default 30) and `--seed` on the
   samples parser only.

10. **`test_transcript_analysis.py` — add the unit tests.** Each named, not
    bundled, so none is silently skipped:

    *Site count:* grep-scoped-to-one-file plus read-of-that-file → 1
    (Verification 4); the same pair where the grep `path` is relative and the
    read `file_path` absolute → still 1, which is the test that actually
    pins A14's normalization; grep-scoped-to-a-directory plus read-inside-it
    → 2 (the named limit, asserted as intended behavior); two unscoped greps
    → 2 (FP shape 6, asserted so a later fix is a visible change); a
    glob-scoped grep repeated twice → 1; a `Read` block with no `file_path`
    key → contributes 0; the same file read twice at different offsets → 1;
    eight distinct `Read` blocks in one turn → 8 and flagged (Verification 5).

    *Lookahead:* edit at exactly window position 3 → `inline-edit`; edit at
    position 4 → `neither`; a user turn between read and edit does not
    consume the window; an `Agent` call → `dispatched`; the candidate is the
    session's last turn → `neither` (and therefore flagged) — the
    end-of-session case audit-routing's own D3 fixtures never had to cover.

    *Session turns:* an explicit `null` `gitBranch` normalizes the same as an
    empty string, mirroring `test_null_git_branch_normalizes_the_same_as_empty_string`;
    a branch change ends a streak.

    *Buckets:* boundary values at each ladder edge, matching
    `TestTurnShapeBuckets`.

    Plus `fake_projects` integration tests asserting printed columns via
    `_table_cols`, including the flagged-share line's two denominators.

11. **`test_transcript_cli_bootstrap.py` — add four subprocess tests**: a
    `--help` exit-zero test per subcommand asserting a distinguishing flag,
    and a seeded end-to-end run per subcommand against a written session
    fixture. The samples run additionally asserts the banner is present and
    that no file appeared under `tmp_path` (a before/after `rglob("*")` diff,
    as `TestTurnShapeSamples` does) — turning step 8's "write no file" from an
    assertion into a checked invariant.

12. **`docs/transcript-analysis.md` — add two `## ` sections** after the
    `turn-shape` family, following the house template (Purpose / Flags /
    Sample output / When to reach for it). Sample output must use synthetic
    paths, never corpus content.

13. **`transcript-analysis/SKILL.md` — add five rows** to the "Which
    subcommand to use" routing table: this plan's two, plus the three
    `turn-shape` family rows missing today (`turn-shape`,
    `turn-shape-samples`, `turn-shape-holdout-samples`). Adding two rows to a
    table while leaving three known-missing siblings out is the structural-
    siblings case `CLAUDE.md` names; the three are verified missing by grep,
    need no design decision, and cost one line each. Record them under
    "Incidental edits" in the PR description.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` — full suite green.
2. `../../../.venv/bin/ruff check claude/.claude/` — clean.
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
   no shell files change, but the repo's stated verification set runs whole.
4. Unit assertion, stated as an explicit test: a turn containing a `Grep`
   scoped to one file plus a `Read` of that same file scores site count 1 and
   is not flagged.
5. Unit assertion: a single turn carrying eight distinct `Read` blocks is
   flagged — the case A1 shows no `turn-shape` streak can see.
6. **Corpus run** (the analogue of the parent plan's Verification 9): run
   `read-sweep-shape --this-repo --since 35d`, confirm a non-zero session
   count, and confirm an immediate re-run reproduces identical numbers.
   "Immediate" is load-bearing: `--since Nd` is computed against `time.time()`
   and the corpus rolls off at ~30 days, so two runs separated by a
   meaningful wall-clock gap legitimately differ — they can gain newly
   written turns and lose aged-out ones. Reproducibility here means
   determinism within one sitting, not stability across days. Report the
   distribution, not raw sample content.

6a. **Normalization spot-check against real data.** A14 is verified as a
   hazard class but unverified as to frequency *in this corpus*. Before
   trusting the site-count semantics, count how many candidate turns contain
   both a `Grep`/`Glob` `path` and a `Read` `file_path` that normalize to the
   same string but differ as raw strings. Report the count. A zero here would
   mean the fixture-only proof in step 10 is the only evidence normalization
   does anything, which is worth knowing before calibration rather than after.
7. **Rating pass** (the analogue of Verification 10, a later human-in-the-loop
   phase, not part of the first PR): sample flagged turns via
   `read-sweep-samples`, classify each as genuine violation or legitimate
   inline work, and report precision against the A9 floors.

   **Sampling sizing, pre-registered here** — the parent plan pre-registers
   its holdout sizing rule for the same reason it pre-registers its floors,
   and deferring the rule to measurement time is the thing that discipline
   exists to prevent. The flagged sample is sized from the flagged
   population. The unflagged holdout is sized by a fixed minimum-misses
   target: **sample until 30 true violations are observed among unflagged
   candidate turns, or until 1,000 unflagged candidate turns have been
   labeled, whichever comes first.** The resulting N is computed at
   measurement time; the rule is fixed now.

   **Rater note.** Shapes 1 and 2 are not distinguishable from a true
   violation using tool-call lines alone — both turn on *why* the read
   happened, which the `turn-shape-samples` output format does not carry.
   `read-sweep-samples` therefore emits, per sampled turn, the verbatim
   prior user message and the first line of the model's own response
   alongside the tool calls, as `cmd_audit_routing_samples` already does for
   its own rating task. Without that context the taxonomy is unusable and κ
   will fail on ambiguity rather than on genuine disagreement. Raters treat a
   read whose *output is the deliverable* (the user asked what the files say)
   as a legitimate true-negative, not a violation.

   **Handling.** Classification stays ephemeral: raters run the identical
   seeded command and exchange only label sets — turn identifier plus
   judgment — never raw output. Nothing sampled is pasted into a commit, PR
   body, review comment, or plan file.

## Phasing

- **Phase 1 (this PR):** detector, tests, docs, and Verification 1-6.
- **Phase 2:** `read-sweep-holdout-samples` for the recall half.
- **Phase 3:** the rating pass against the A9 floors.
- **Phase 4, gated on Phase 3 clearing the floors:** broaden
  `subagent-delegation/SKILL.md`'s operational trigger and Step 1 header past
  `Bash`, and align `claude/.claude/CLAUDE.md`'s delegation bullet.

## Out of scope

- Modifying `_turn_shape_streaks` or `cmd_turn_shape_samples` in place.
- Re-scoping `audit-routing-shape`'s Opus or judgment-span filters.
- The existing two rules' calibration, and PR #699's merge.
- Any `PostToolBatch` hook — this plan produces measurement only. A nudge for
  this rule would be a separate decision after Phase 3.
- Extracting `read-sweep` into the `transcript_analysis/` package. Deliberate,
  with reasoning under "Relationship to the decomposition effort" — it is a
  condition this plan could change and declines to, not a given.
