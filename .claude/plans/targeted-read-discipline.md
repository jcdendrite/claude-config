# Targeted-read discipline: measure the baseline, then decide

## Context

**Goal.** Decide, from this repo's own measured transcript corpus rather than from
an inherited conclusion, whether a "locate before you read" instruction belongs in
this repo's global guidance — and land the measurement instrument that makes the
decision re-derivable later.

**Problem.** An evaluation held outside this repo concluded that the one
uncontested token saving available on the read path is replacing whole-file
`Read` calls with `offset`/`limit` reads around a located region, and recommended
adding a short instruction to global guidance. That evaluation flagged two of its
own numbers as unreliable — it used assistant *output* tokens as the denominator
for what is a prompt-token effect — and its background assumed whole-file reads
dominate. Neither claim had been checked against this repo's corpus.

**Why now.** The claim is cheap to check and the instrument is reusable: this repo
already grounds guidance decisions in `transcript-analysis.py` measurements
(`docs/case-studies/hashline-edit-format.md`, `review-vs-babysitting.md`,
`worktree-enforcement.md`, `check-runner.md`), and no existing subcommand inspects
`Read` inputs or result sizes at all.

**Outcome.** A measured baseline, a committed `read-scope` subcommand that
re-derives it, one line of new global guidance carrying only what the platform's
own `Read` tool description does not already say, and a case study recording the
decision with a numeric revisit trigger.

## Approach

### What the scratch measurement established

Measured this session across four config-dir profiles. **These figures are
provisional.** They
came from a scratch script whose growth denominator does not yet carry the
corrections in "Computing the denominator" below; the committed subcommand must
re-derive all of them, and Verification gate 4 governs what may be published. The
final, published figures live in the case study, derived from `read-scope`.

Two notes on the citations below. Every `:NNNN` line reference was taken against
commit `4956e39`; the branch was later synced to `74a0f22`, which restructured
multi-account scanning and shifted every offset in `transcript-analysis.py`. The
named functions are all still present and the design is unchanged — only the
numbers are stale, so read them as "find this function," not "go to this line."
That sync also brought `declared_transcript_roots()`, which `_resolve_cost_roots`
now consults, so `read-scope` picks up every declared account with no
`--config-dir` flags once `~/.claude/transcript-config-dirs` is populated.

| quantity | provisional value |
|---|---|
| `Read` calls | 21,839 |
| carrying `offset` or `limit` | 9,947 (**45.5%**) |
| whole-file reads (neither) | 11,412 (52.3%) — but **77.8%** of Read-result tokens |
| Read-result est. tokens | 49.1M |
| prompt-token growth | 297.6M (**recompute — see below**) |
| Read results as share of growth | **16.5%** (inherits that recompute) |
| whole-file reads ≥2,000 est. tok | 6,037 calls / 33.6M tok (87.9% of whole-file tokens) |
| gross ceiling if all became median targeted reads | 29.9M = **10.06% of growth** |
| repeat whole-file reads of same path in a session | 847 calls / 1.47M tok = **0.49% of growth** |
| whole-file-read tokens inside subagents | **73.1%** |

Three conclusions follow, and each one *removes* work the source evaluation
proposed:

1. **The "whole-file reads dominate" premise is half wrong.** 45.5% of calls are
   already targeted — 3.5× the 12.8% instruction-adoption reference point the
   source evaluation set as the bar an instruction would have to clear. The case
   survives only on token mass (77.8%), not on call count.
2. **The "re-read narrowly after an edit" clause is not worth writing.** Repeat
   whole-file reads are 0.49% of growth, and ~38.5% of those target
   `.output`/`.log`-shaped paths — live polling of a growing file, which is
   correct behavior, not waste.
3. **The correct denominator is prompt-token growth, not output tokens.** Read
   results are prompt tokens. Against output tokens the same numerator reads
   29.7%, which overstates it; against total `cache_creation` it reads 2.4%,
   which understates it because that field is inflated ~6.8× by repeated
   re-caching of unchanged content and measures cache churn rather than content
   volume.

### Computing the denominator

Prompt-token growth is the plan's headline denominator, and a naive
sum-of-positive-deltas over a session's merged record list is wrong in six
independent ways. Each has existing precedent in this file; the subcommand must
implement each explicitly:

1. **Partition by source file before differencing — the merged list cannot do
   it.** `_read_session_file` (`:370-409`) appends *all* subagent-file records
   after *all* main-thread records and concatenates separate subagent files in
   filename-sort order — not chronological, not interleaved. Differencing the
   flattened list produces spurious deltas at the main→subagent boundary and
   between unrelated subagent files, comparing context values from entirely
   different windows. `isSidechain` recovers main-vs-subagent but is a boolean —
   it cannot separate two subagent files from each other, and the merge discards
   the file boundary that can. Real subagent records do carry `agentId` and
   `sessionId` (sampled directly from the live corpus), so an in-list partition is
   *possible* — but its coverage across the full rolling corpus is unverified, and
   a record missing the field would silently collapse into a neighbouring
   sequence. The file boundary is correct by construction and cannot degrade that
   way, so the growth chain partitions on it. See M6 for the mechanism.
   `_session_peak_context` (`:4160-4163`) takes `main_thread_turns` explicitly for
   the same class of reason.
2. **Partition on `sessionId` within a file as well.** This codebase has already
   hit records from a foreign `sessionId` interleaved into one physical file —
   `_attribute_model_to_prompt` (`:691-708`) exists for it, pinned by
   `test_attribute_model_ignores_cross_session_interleaved_assistant`
   (`test_transcript_analysis.py:8565-8575`). One such record differenced against
   its neighbours produces exactly the spurious delta item 1 fixes at the file
   boundary. File partitioning alone does not cover this.
3. **Reset the chain at each compaction boundary.** A `{"type": "system",
   "subtype": "compact_boundary"}` record (detected at `:5507`) collapses the
   context; the following turn carries a large negative delta, and regrowth from
   the smaller baseline then re-counts content already counted once. Reset the
   chain at each boundary and attribute no growth across it.
4. **The first turn of each sequence contributes no growth.** It has no
   predecessor. Counting its absolute context as growth would add every session's
   system-prompt baseline to the denominator.
5. **Skip records with absent or malformed `usage`.** Every per-turn consumer in
   this file guards this (`:3853-3855`, `:4617-4618`, `:4923-4924`); a growth
   chain that treats a missing `usage` as a zero-context turn manufactures a full
   context snapshot as one turn's growth on the following record.
6. **`--since` filters completed deltas, not records.** Every existing per-turn
   `since_ts` filter in this file drops records inline via `continue`
   (`:3857-3861`, `:4620-4623`, `:4927-4930`). Applied to a delta chain that would
   either lose the first real jump or, worse, treat a raw absolute context
   snapshot as one turn's growth. Build the full chain over the unfiltered
   sequence — including items 3's resets — then filter the completed deltas by the
   owning turn's timestamp.

**Cross-check denominator.** The headline ratio divides an estimated numerator
(Read-result `chars // 4`) by a measured denominator (real `usage` fields) — mixed
units, recorded as R10. The report therefore also prints a fully self-consistent
secondary ratio: Read-result tokens as a share of *total tool-result tokens across
all tools*, both sides `chars // 4`. If the two ratios tell different stories, that
is a finding, and neither number is published until it is understood.

### Chosen design

**Three deliverables, one of them deliberately small.**

**(a) A `read-scope` subcommand in `transcript-analysis.py`.** Single pass, matching
`edit-format`'s shape: per-account breakdown, explicit unpaired/error counters, a
`--since Nd` window so a later before/after comparison is one flag rather than a
new script. Chosen over an ad hoc script because the question is recurring by
construction — the case study's revisit trigger has nothing to re-derive it with
otherwise. This repo has a documented criterion for exactly this choice
(`docs/case-studies/effort-estimation-review-surface.md:18`: recurring and
re-derivable → subcommand; one-off rarity finding → ad hoc), and adherence
tracking is the recurring case.

**(b) One line in `claude/.claude/CLAUDE.md`,** under Working Style, adjacent to
the existing `Default-consider delegation` bullet (`:31`). It carries only the
content the platform's own `Read` tool description omits. That description reads,
verbatim: *"When you already know which part of the file you need, only read that
part. This can be important for larger files."* — a rule conditional on already
knowing the range. The new line addresses the case where you do not:

> - **Locate before a whole-file read.** Once you've decided to read a file,
>   decide *how much* of it. When you don't know which part you need, a single
>   `Grep` inside that file hands back the matching line numbers — then `Read`
>   that range plus a margin. When you don't know how big it is, `wc -l` answers
>   that in one cheap call. Read whole when you need the file's shape rather than
>   a region in it, or when you already know it's short.

Four properties this wording is chosen for, each fixing a defect a review round
found in an earlier draft:

- **It opens by scoping to a file already chosen.** This is what keeps it from
  colliding with `subagent-delegation/SKILL.md:112-115` (*"Dispatch any read whose
  purpose is to explore, map, or locate — even a single command"*). That mandate
  governs codebase discovery — which file, where a symbol lives across the repo.
  A `Grep` bounded to a file you have already decided to open is part of the
  comprehension read the same skill tells you to do inline (`:120-123`), not a
  discovery sweep. Scoping it this way resolves the tension without naming the
  skill in `CLAUDE.md`.
- **It names only `Grep` for locating.** `Glob` returns paths, not line numbers —
  an earlier draft claimed otherwise.
- **`wc -l` answers a question, rather than gating the carve-out.** Making the
  carve-out "files small enough that locating costs more than reading" was
  circular: you cannot know a file is small without paying the call the condition
  exempts you from. Here `wc -l` is what you run when you *don't* know the size,
  and the carve-out rests on an ex ante signal instead.
- **It does not reference the built-in tool description**, which a human reader of
  `CLAUDE.md` cannot inspect, and its carve-out is bounded by two stated
  conditions rather than by a self-certified *"genuinely must reason over it end
  to end"* that a reader could invoke by assertion for nearly any read.

**(c) A case study** at `docs/case-studies/targeted-read-discipline.md`, linked
from `docs/case-studies.md`, in `hashline-edit-format.md`'s house style, plus a
`CHANGELOG.md` entry under `## [Unreleased] → ### Changed`. The changelog entry is
not optional bookkeeping: `claude/.claude/**` goes live for every stow consumer on
`git pull` with no re-install, so a global-guidance change with no changelog line
reaches them silently. This repo has precedent for exactly this category of entry.

**Declined: a `PostToolUse` `Read` hook.** Considered as the adherence counter and
rejected on two independent grounds. It is redundant — the transcript already
records `offset`/`limit` on every call, which is exactly how the 45.5% figure was
derived, so the subcommand *is* the counter and the hook adds no measurement. And
it is self-defeating — advisory context injected on all 21,839 `Read` calls spends
context to save context. Recorded here because the source evaluation named it as
the fallback if adherence proved low; measured adherence is not low.

**Declined: placing the rule in `subagent-delegation/SKILL.md`.** That skill's
frontmatter DO-NOT-TRIGGER list names *"comprehension read feeding your own
writing/review/design"* — the exact moment a locate-first rule needs to fire. A
rule placed behind a trigger that excludes its own use case does not fire. It is
also at 172 of its 200-line cap.

### Assumption ledger

**Root problem.** Whole-file `Read` calls carry 77.8% of this corpus's Read-result
tokens and roughly a sixth of all prompt-token growth, and no committed instrument
measures whether that share moves.

**Givens** — conditions beyond this design's reach:

- **G1.** The built-in `Read` tool description already carries the conditional
  form of this rule. *Reason:* Anthropic owns the tool description; this repo
  cannot edit it. This is why deliverable (b) is scoped to the complement.
- **G2.** The transcript corpus is mutable, rolling, and grows during the session
  that measures it. Every figure is point-in-time. *Reason:* Claude Code owns
  transcript retention; no pinned corpus is available.

Two further conditions this design accepts — the nested-workflow-transcript
undercount and the absence of a control group — are inside this repo's reach and
are therefore **declined scope, not givens**; see Out of scope for each one's
reason. They appear below as R12 and R13 because they bound the numbers, not
because they are beyond changing.

**Mechanisms:**

- **M1 — `read-scope` subcommand.** *anchors: root.* The root problem is a share
  that must be watched over time, which requires a committed instrument.
  *Over-powered check* — three lighter primitives, all failing:
  (i) the scratch script already written — not committed, so the revisit trigger
  in deliverable (c) would be unenforceable;
  (ii) reuse of an existing subcommand — none inspects `Read` inputs or result
  sizes anywhere in the file (verified: the only `offset`/`limit` occurrences are
  `_truncate_prompt_text`'s display limit and `friction-count`'s byte checkpoint);
  (iii) a documented `jq` one-liner — the six-part delta-chain correction above
  is not expressible as a one-liner a future reader would run correctly.
- **M2 — `TestReadScope` + `TestScanReadScopeSession`.** *anchors: M1.*
  `edit-format`'s convention is that only classification *behavior* is reproducible
  across a mutable corpus (`hashline-edit-format.md:29`); synthetic fixtures are
  what make that true. Two classes, not one, because
  `test_transcript_analysis.py:6948-6956` documents why the split exists:
  classification and arithmetic invariants assert on returned dicts, CLI and
  format concerns assert on printed output.
- **M3 — extract `_context_at_turn(usage)` from `_price_turn` (`:4121-4125`).**
  *anchors: R3.* The growth denominator needs the same per-turn context sum
  `_price_turn` already computes; extracting it is strictly lighter than
  duplicating the formula in a second place where the two could drift.
  Behavior-preserving, pinned by test. *Known cost:* `_price_turn` still needs
  `input_t`, `cache_read_t`, `eph_1h`, `eph_5m` individually for its `dollars`
  dict (`:4131-4137`), so it calls `_cache_write_split` a second time. Pure and
  idempotent, so no behavior change — but the plan states it rather than implying
  a single computation.
- **M6 — add `_read_session_file_partitioned(jsonl, include_subagents) ->
  list[list[dict]]`, and make `_read_session_file` a thin flattening wrapper over
  it.** *anchors: denominator item 1.* The growth chain needs the per-file
  boundary the merge discards; classification still wants the flat list. Returning
  the partition and flattening it in the existing function serves both from one
  read, with no duplicated parse loop. *Over-powered check* — two lighter
  primitives, both failing: (i) partition in-list on `agentId`/`sessionId` — the
  fields exist on live records but their coverage across the rolling corpus is
  unverified, and an absent field degrades silently into a wrong number rather
  than an error; (ii) let `read-scope` re-glob `subagents/*.jsonl` itself
  (mirroring `:397`) and read every file a second time — correct, but duplicates
  the parse loop of a shared helper and doubles I/O over every scanned file.
  *Blast radius:* all 23 subcommands read through the wrapper. Behaviour is
  identical by construction (flatten ∘ partition = the current concatenation
  order), pinned by a test asserting the wrapper's output is byte-equal to
  today's on a fixture with a main file and two subagent files.
- **M4 — one `CLAUDE.md` line.** *anchors: root.* Prose is the lightest available
  primitive; the two heavier ones (hook, skill) are declined above with reasons.
- **M5 — case study + index entry + changelog entry.** *anchors: root.* The
  decision and its revisit trigger need a durable home, and the stow-consumer
  surface needs a disclosure line.

**Assumptions:**

| # | assumption | tag |
|---|---|---|
| R1 | 45.5% of `Read` calls already carry `offset`/`limit`; whole-file reads hold 77.8% of Read-result tokens | `[verified: scratch measurement]` — call-side only; unaffected by the denominator corrections |
| R2 | The output-token denominator (29.7%) is wrong because Read results are prompt tokens | `[verified: both denominators computed side by side]` |
| R3 | Prompt-token growth = per-file-and-`sessionId`-partitioned, compaction-reset, first-turn-excluded sum of positive deltas of `_context_at_turn` | `[verified: implemented, fixture-pinned, and A/B'd against the uncorrected definition in one corpus pass — see Verification 4]`. Final: 301,756,171, with Read results at 15.9% of it |
| R4 | No existing subcommand inspects `Read` `offset`/`limit` or result size | `[verified: source, whole-file grep]` |
| R5 | `read-scope` must join the top-level `--config-dir` refusal tuple at `:6871`, and that tuple is the only such site | `[verified: source, confirmed independently by two reviewers]` |
| R6 | Instruction form = one `CLAUDE.md` line, locate-first only; no post-edit re-read clause | `[engineer-verified]` |
| R7 | Committed subcommand, not ad hoc script | `[engineer-verified]` |
| R8 | The `subagent-delegation:94-96` overlap is adjudicated at `/code-review` against the real diff | `[engineer-verified]` |
| R9 | **The ceiling is gross, not net.** It assumes every whole-file read ≥2,000 tok could become a 612-token targeted read with no correctness loss, and it does not subtract the `Grep`/`wc -l` calls the instruction asks for. Verification 5b measures that cost | `[unverified]` |
| R10 | **Read-result token figures are `chars // 4` estimates against a real-usage denominator** — a mixed-units ratio. Verification 5a fits the ratio; the self-consistent cross-check ratio bounds it | `[unverified]` |
| R11 | 73.1% of whole-file-read tokens are inside subagents, where a read is discarded on return rather than re-billed for the session's remainder — so realized saving is below the growth-denominated share | `[verified: measurement]`; effect on the ceiling `[unverified]` |
| R12 | Every figure undercounts nested workflow-agent transcripts: `_read_session_file` (`:370`) merges direct `subagents/*.jsonl` children but not `subagents/workflows/wf_*/agent-*.jsonl`. Magnitude unmeasured | `[verified: source; hashline-edit-format.md:30 records the same limit]` |
| R13 | No before/after figure can be read causally. A staggered rollout across the four profiles would give a real control and is reachable, but is declined — see Out of scope | `[verified: each profile carries its own config dir]` |
| R14 | **The corpus is one engineer's, on one machine; the instruction ships to every stow consumer.** Whether a ~46% baseline and a whole-file-heavy tail generalize to a consumer with a different task mix — heavier unfamiliar-codebase exploration, where whole reads are more often warranted — is not established and cannot be from this data. The measurement now carries direct evidence against assuming it does: across this one engineer's four account profiles the targeted-read share spans **34.3% to 48.2%**, a 14-point spread driven by nothing but which work each account does | `[verified: per-account breakdown]` for the spread; `[unverified]` for cross-consumer generalization |
| R15 | Non-text `Read` results are encoded as `content: [{"type": "image", "source": …}]`; text results are a plain `str`. `chars // 4` over an image block is meaningless, so image results are excluded from the size histogram and counted separately | `[verified: corpus probe, 2,466 files — 11,907 `str` results vs 12 `image` results]` |
| R16 | **57 `Read` calls carry `__unparsedToolInput` as their only input key, and 1 carries no keys at all** — no `file_path`, no `offset`/`limit`. Filing them under whole-file (the naive `is not None` outcome) would inflate the whole-file cohort with calls whose scope is unknowable. They get their own `unparsed_input` counter, excluded from both cohorts | `[verified: corpus probe]` |

R9, R10, R11 and R14 all push the same direction: the honest headline is a bounded,
gross ceiling with stated sensitivity, never a point estimate.

## Critical files

**Modify — `claude/.claude/scripts/transcript-analysis.py`**

New, mirroring `edit-format`'s layout:

- `_READ_SCOPE_CHARS_PER_TOKEN = 4` — deliberately a second pin alongside
  `_EDIT_FORMAT_CHARS_PER_TOKEN` (`:5051`) rather than a shared constant, so
  recalibrating one report's published figures cannot silently move the other's.
  This is the named "small duplicated value beats a bad abstraction" exception.
- `_new_read_scope_stats()` / `_merge_read_scope_stats()` — mirror `:5067` / `:5083`.
- `_scan_read_scope_session(records, since_ts)` — one pass, mirroring
  `_scan_edit_format_session` (`:5137`). Counts:
  - Total `Read` calls; `offset`, `limit`, either, both, `pages`.
    **Classify on `is not None`, never on truthiness** — `offset=0` is a valid
    first-line read and is falsy in Python; a truthiness check would silently
    file it as a whole-file read.
  - Result est. tokens and size histogram, split by cohort (targeted /
    whole-file) × scope (main-thread / subagent).
  - `unpaired`, `error_result`, `non_text_result`, and `unparsed_input` counters,
    each printed explicitly and each excluded from the size histogram (R15, R16).
    Whether unpaired results contribute tokens is decided one way — excluded —
    and tested. `unparsed_input` is excluded from *both* cohorts, not filed under
    whole-file: a call whose input did not parse has unknowable scope, and the
    naive `is not None` check would silently call it a whole-file read.
  - **Cohort percentages divide by the full `Read` call census, never by
    `targeted + whole_file`.** Otherwise the two printed shares sum to 100% while
    the 58 excluded calls vanish from the arithmetic. This mirrors `edit-format`,
    which fixes its rates against `edit_n` (`:5308-5314`) and prints
    `unclassified` as a visible sibling line (`:5327`) rather than netting it out
    of the denominator. Each exclusion counter prints on its own line with a
    parenthetical definition, in that same style.
  - Repeat-whole-file-read count and token sum per session, plus the
    `.output`/`.log`-suffix sub-count that explains it. **Pure aggregates: counts
    and token sums only. No path, filename, path fragment, or session identifier
    is retained past the scan or printed.** These exist because the case study
    cites them as the reason a clause was dropped, and Verification gate 4
    requires every published figure to be re-derivable here.
  - Per-sequence prompt-token growth, per "Computing the denominator" above.
    This part consumes `_read_session_file_partitioned`'s per-file lists (M6), not
    the flat merge — the flat list is still what the Read-call classification
    above runs over, since `isSidechain` is all that bucketing needs.
- `_read_scope_report(args, roots)` / `_print_read_scope_report(stats, per_account)`
  — mirror `:5225` / `:5285`. Two invariants stated in the docstring, not left
  implicit in the word "mirror":
  - **The report is unconditionally redact-invariant.** `--no-redact` unlocks
    nothing; it exists for CLI parity and to carry the shared multi-root refusal.
    This differs from `cost` / `context-distribution`, where `--no-redact` is a
    real unlock that prints paths (`:4547`) and session IDs (`:4827`) — a
    precedent a later feature could otherwise slide into.
  - **The `--no-redact`-under-multi-root refusal is re-enforced inside
    `_read_scope_report` itself**, not only at `_resolve_cost_roots`, mirroring
    `_edit_format_report:5252-5258` and its stated reason: every direct caller,
    including this module's own tests, bypasses the CLI boundary.
- `cmd_read_scope(args)` — mirror `:5215`.

**Reuse, do not reimplement:** `_resolve_cost_roots` (`:4227`),
`_resolve_project_scope` (`:2323`), `_parse_since_nd_arg` (`:337`),
`_cache_write_split` (`:4088`), `_add_project_scope_args`, `_root_index_for_path`
(`:4336`), and the `compact_boundary` shape already pinned at `:5505-5507`.
`_read_session_file` (`:370`) is reused for classification but **not** for the
growth chain — see M6.

**Edit, minimally:**

- Extract `_context_at_turn(usage) -> int` from `_price_turn` (`:4121-4125`); have
  `_price_turn` call it (M3).
- Add `_read_session_file_partitioned` and reduce `_read_session_file` to a
  flattening wrapper over it (M6). Behaviour-preserving for all 23 subcommands;
  pinned by an equality test against the current concatenation order.
- Register the subparser next to `edit-format` (`:6740`): `_add_project_scope_args`,
  repeatable `--config-dir`, `--no-redact`, `--since Nd`, `set_defaults(func=...)`.
- **Add `"read-scope"` to the refusal tuple at `:6871`** — the structural-sibling
  check: three arms already live there and the fourth belongs with them (R5).

**Modify — `claude/.claude/scripts/tests/test_transcript_analysis.py`**

Two classes, following `TestEditFormat` (`:6649`) / `TestScanEditFormatSession`
(`:6948`) and reusing the fixture builders at `:6566-6646` plus a new
`_read_tool_use(id, *, file_path, offset=None, limit=None)`.

`TestScanReadScopeSession` (direct-dict assertions — classification and arithmetic):
- `offset=0, limit=N` lands in the targeted cohort, not whole-file. This is the
  adversarial-fixture bar `test_notfound_cause_not_misled_by_indentation_alone`
  (`:6763`) set after a classifier defect produced a measured 12× over-attribution.
- offset-only and limit-only each land in targeted — an implementation checking
  one key would misclassify the other with no test failing.
- A `Read` inside a subagent-merged file lands in the subagent scope bucket, not
  main-thread. The published "73.1% inside subagents" figure depends on it.
- A non-`Read` tool carrying `offset`/`limit` is not counted.
- Error results, unpaired results, image results, `pages` reads, and
  `__unparsedToolInput` calls each land in their own counter and none reaches the
  size histogram. The `__unparsedToolInput` fixture asserts **three** things, not
  one: both cohort counters are 0, `unparsed_input == 1`, and the total `Read`
  call census still counts the record. Asserting only "lands in neither cohort"
  is satisfied equally by a correct classifier and by one that drops the record
  entirely — and dropping it undercounts the census that every published
  percentage divides by. Filing it under whole-file is the defect R16 names; a
  silent drop is its twin.
- Cohort percentages are asserted against the full call census on a fixture that
  mixes classified and excluded calls, so a denominator computed as
  `targeted + whole_file` fails.
- Growth arithmetic: single-turn sequence yields zero; a compaction boundary
  resets the chain; two subagent files do not produce a cross-file delta; a
  record carrying a foreign `sessionId` mid-file does not produce a delta against
  its neighbours; a turn with absent `usage` is skipped rather than read as
  zero-context; a shrinking context yields no negative contribution; `--since`
  filters completed deltas rather than records.
  **Fixture note:** the existing subagent builders (`_asst`,
  `_write_subagent_jsonl`, `:30-36`, `:166-182`) do not populate `sessionId` or
  `agentId`. The foreign-`sessionId` and per-file-partition fixtures must set them
  explicitly — a test written against the current builders would pass without
  exercising either partition.
- `_read_session_file_partitioned` (M6): flattening its output equals today's
  `_read_session_file` output exactly, on a fixture with a main file and two
  subagent files.

`TestReadScope` (report-level, regex on printed output — CLI and format):
- Per-account rows use `account-N` labels, mirroring `:6881-6919`.
- `--no-redact` refused under multi-root at the CLI, **and** refused by
  `_read_scope_report` when called directly, mirroring
  `test_no_redact_refused_by_edit_format_report_itself_even_when_called_directly`
  (`:6930`).
- **No `file_path` substring from any fixture appears anywhere in the printed
  report** — the path invariant asserted, not merely commented.
- One test pinning `_price_turn`'s output unchanged after M3.

**Modify — `claude/.claude/CLAUDE.md`** (114 lines; gate at 200): one bullet under
Working Style, after `:31`.

**Create — `docs/case-studies/targeted-read-discipline.md`**; **modify —
`docs/case-studies.md`** (one index bullet matching the existing shape); **modify —
`CHANGELOG.md`** (one entry under `## [Unreleased] → ### Changed`).

## Verification

0. **Done before the classifier was written.** Sampled 2,466 real transcript files
   for `Read` `tool_use` / `tool_result` shapes. Results are recorded as R15 and
   R16; R16 (`__unparsedToolInput`) was not anticipated by this plan and exists
   only because the probe ran first.
1. `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py`
   — full file, not just the new classes, because M3 touches `_price_turn` (shared
   with `cost`, `context-distribution`, `cost-trend`, `audit-routing`) and M6
   touches `_read_session_file`, which every one of the 23 subcommands reads
   through.
2. `../../../.venv/bin/ruff check claude/.claude/`.
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
4. **Reconcile against the scratch measurement.** Run `read-scope` over the same
   four profiles; every number in the case study is re-derived from its output,
   none copied from the scratch run.

   **This gate's original wording was wrong and is corrected here.** It predicted
   the growth figures *should not* reproduce, and treated agreement as evidence
   the six corrections had not been implemented. A same-corpus A/B of the two
   definitions, run in one pass, shows otherwise:

   | growth definition | total | Δ |
   |---|---|---|
   | flat merged, no corrections | 301,774,970 | — |
   | + per-source-file partition | 301,274,877 | **−0.17%** |
   | + per-`sessionId` | 301,274,877 | −0.17% |
   | + compaction reset | 301,274,877 | −0.17% |
   | + skip absent `usage` | 301,274,877 | −0.17% |

   The corrections are right and their behaviour is pinned by fixtures, but on
   this corpus their aggregate effect is 0.17%, essentially all from file
   partitioning. Two of them are exactly inert here, and one is inert *by
   construction*: summing only positive deltas already discards the drop a
   compaction causes, so resetting the chain cannot change the sum. It stays in
   for corpora that do contain the pattern, but the case study must not present
   it as having fixed a number.

   **Reconciling the two implementations required accounting for drift, and
   nearly produced a false alarm.** Ordered by when each ran: `read-scope`
   corrected = 301,248,855; the A/B's corrected = 301,274,877; the A/B's
   uncorrected = 301,774,970; a later `read-scope` corrected = 301,756,171. Read
   out of order, that last figure sits next to the *uncorrected* total and looks
   like the corrections were never applied. They were — the two corrected
   implementations agree to 26,022 tokens (0.009%) when measured minutes apart,
   and the +481k gap to the later run is drift. The corpus grew roughly half a
   million tokens of prompt-token growth over the few minutes between runs,
   because the session doing the measuring is itself writing large transcripts
   into it. That is G2 made concrete, and it sets the floor on how precisely any
   figure here can be stated: two runs of the *same* command minutes apart differ
   by more than the entire six-part correction does.

   The reconciliation that did earn its keep was the repeat-whole-file-read
   metric, where the first implementation scoped repeats across a transcript *and
   its subagents* and returned 4,429 reads / ~15.96M tokens against the scratch
   run's 847 / ~1.47M. A parent and its subagent are separate context windows, so
   a subagent re-reading its parent's file is not a redundant read. Rescoped to
   the same partition the growth chain uses, the subcommand returns 848 /
   ~1.48M — two independent implementations agreeing.
5. **Bound the two `[unverified]` rows before publishing any percentage.**
   - **5a (R10).** Fit chars-per-token from the corpus over turns whose only new
     content is a single tool result, and print it as one diagnostic line. The
     filter predicate is itself a classifier and gets fixtures: a turn with a
     result plus inline assistant text, a turn with two results, and a turn whose
     sole result is a non-`Read` tool must all be excluded from the sample. Report
     the headline at the 4.0 convention with the fitted ratio as the sensitivity
     band, and check it against the self-consistent cross-check ratio.
   - **5b (R9).** Measure the locate step's own cost — `Grep` / `Glob` / `wc -l`
     result tokens in the turns preceding targeted reads — and net it against the
     gross ceiling in Honest limits. Until that lands, every quotation of the
     ceiling says *gross*.
6. Confirm `transcript-analysis.py --config-dir X read-scope` exits 2 with the
   existing message.
7. Confirm `claude/.claude/CLAUDE.md` is under 200 lines at commit time.
8. `/code-review` before commit — which also adjudicates R8, the
   `subagent-delegation:94-96` overlap, against the actual diff.

**Numeric revisit trigger** for the case study, all re-derivable from `read-scope`:

- Targeted-read share falls below **40%** of `Read` calls, sustained over two
  weeks — the source evaluation's own threshold, now measurable against a
  baseline of 45.5% rather than assumed.
- Read results exceed **20%** of prompt-token growth, on the corrected
  denominator that ships with this change.
- Whole-file reads ≥2,000 est. tokens exceed **90%** of whole-file-read tokens
  (today 87.9%) — the mass concentrating further into the addressable tail.

## Out of scope

- Any re-adjudication of the edit-path mechanism the source evaluation covers, and
  any of its code, hooks, or grammar. This branch touches the read path only.
- Existing `PreToolUse` `Edit`/`Write` hook registrations — untouched.
- Widening `_read_session_file`'s subagent glob to reach nested workflow-agent
  transcripts (R12). Inside this repo's reach and deliberately declined: the helper
  is shared by all 23 subcommands, so widening it would silently change every
  already-published figure in this repo's other case studies. Separate change, and
  it should re-publish those figures when it lands.
- A staggered rollout of the instruction across account profiles to create a
  control group (R13). Reachable — each profile resolves its own config dir — but
  declined: it would fragment the stowed config `install.sh` keeps uniform, and
  withhold guidance from real work to serve a measurement. The case study states
  the limit instead.
- Retro-fitting Verification 5a's fitted chars-per-token ratio into
  `edit-format`'s published figures. Those figures are a dated record of a
  measurement taken on 2026-08-08, which the repo's scope rules treat as
  preserved content rather than a description of current behavior — correcting
  them is a re-measurement, not an edit. If 5a shows 4.0 is materially off, the
  case study says so and names `edit-format` as also affected, so the next
  re-measurement there is informed rather than unaware.
- Unrelated `transcript-analysis.py` maintenance or refactoring. M3 is the sole
  edit to shared code and exists only because the growth denominator requires it.
