# Sleep-poll sub-split of the "waiting on own Bash call" cache-rebuild cause

## Context

`_attribute_idle_gap_cause` (added in PR #913, commit `cdaaf23`) classifies
subagent idle-gap cache rebuilds into four causes. Follow-up investigation
this session, across this machine and a peer session's machine, found
"waiting on own Bash call" is a plurality-to-majority cause on both, and
hand-rolled sleep-poll loops (`until ... kill -0 $PID ...; do sleep N;
done`, or `sleep N; <check>`) are the dominant sub-pattern within it —
not the full-suite-pytest pattern the original merged plan's Approach
section had guessed. Per `docs/design-decisions.md` §49's own precedent,
the exact counts and ratios are withheld here: the sampled corpus mixes
private-project and public transcripts, so any such figure would inherit
the private half's composition. A `plan-architect MODE=consult` dispatch
this session separately established that no generically-buildable lever
exists for this cause — backgrounding a slow Bash call converts a blocking
wait into an equally long idle wait absent independent work to interleave
(`docs/cost-levers-considered.md:244-245`), and `Monitor` is event-driven
with no periodic heartbeat to refresh the prompt-cache TTL during the wait
either (`docs/design-decisions.md:1069`).

The intended outcome is two artifacts: (1) the cache-rebuild report gains a
precise sleep-poll-vs-other sub-split within the existing "waiting on own
Bash call" row, so this finding moves from a hand sample to a rerunnable
instrument any reader can point at their own corpus; (2) a dated follow-up
entry in `docs/cost-levers-considered.md` records the consult's dead-end
finding against `subagent-idle-gap-cache-rebuild-split.md`'s unmeasured
follow-up (`docs/cost-levers-considered.md:483`, "cutting the stall... is
the first thing to re-examine"), so a future reader doesn't re-open that
follow-up without first reading why it was already closed. No behavior
change ships from this plan — both deliverables are measurement and
documentation only. Every figure this plan itself quotes, and every figure
either deliverable publishes, comes from a `--this-repo`-scoped run, which
is content derived only from this repo's own history and so outside the
private-provenance class per this repo's own redaction rule.

## Approach

`_attribute_idle_gap_cause` gains a third return element — a wait-shape label for the winning Bash marker's own recorded `command` string — and a new pure `_classify_bash_wait_shape` sorts that string into one of three labels (`sleep-poll wait`, `other Bash wait`, `no command recorded`). The report prints those three as their own zero-seeded block immediately after the existing four-row attribution table, summing exactly to its `waiting on own Bash call` row, and the consult's dead-end finding lands as a dated follow-up appended under the `subagent-idle-gap-cache-rebuild-split.md` section it corrects.

**1. Thread the shape out of the classifier; do not re-scan the window.**
Only `_attribute_idle_gap_cause` knows which marker won — "last marker wins" is its own forward scan (`transcript-analysis.py:5966-5984`), and the sub-split is a property of that winning marker, not of the window. So the winning Bash block's command text has to leave that function. Change `bash_tool_use_ids` (a `set` of ids, `:5958-5962`) to a `dict[str, str | None]` mapping each id to `input.command` when it is a string; dict membership is key membership, so the `tool_use_id in ...` test at `:5974` keeps its exact current behavior and the two self-scoping tests (`test_transcript_analysis.py:8004`, `:8016`) pass untouched. Assign the winning command in the same tuple assignment that already sets cause and timestamp (`:5984`), so a later meta-leg win resets the command to `None` by construction rather than by a separate guard. Return `tuple[str, float | None, str | None]`, where the third element is `_classify_bash_wait_shape(...)`'s label when the cause is `_ATTR_OWN_BASH` and `None` otherwise — mirroring the `attribution is None` idiom the aggregation loop already keys on (`:6396`).

Return the *label*, not the raw command. The label is one of three module constants, so no corpus command text ever reaches the candidate dict, a return value, or anything printable — this report's output is published (`docs/transcript-analysis.md:781-885` pastes it verbatim), and a command string carries paths, project names, and tracker IDs.

Two lighter primitives were checked and set aside. Re-deriving the command at the call site with a second pass over the same window keeps the 2-tuple contract, but puts the last-marker-wins precedence rule in a second place that the 15 existing precedence tests do not cover — the two copies drift silently. Wrapping one shared `_winning_idle_gap_marker` helper in two accessors also keeps the contract, but leaves two entry points each discarding two-thirds of one scan's result and forces the call site to restate the own-Bash guard it already computes. A third positional element costs one added name at each of the 15 existing unpack sites (`test_transcript_analysis.py:7935`-`:8126`) and rewrites no assertion, keeps one function and one scan, and the return contract already has a single documented home in the docstring's `Returns` paragraph.

**2. Match `sleep <number>` in shell command position, nothing wider.**
One module-level compiled regex beside the existing marker constants:

```python
_SLEEP_POLL_COMMAND_RE = re.compile(r"(?:\A|[;&|\n]|\b(?:do|then|else))[ \t]*sleep[ \t]+[0-9]")
```

`re` is already imported (`transcript-analysis.py:18`). Both hand-sampled shapes reduce to the same invariant: a `sleep` command word in command position with a numeric argument — shape 1 reaches it after `; do`, shape 2 at the start of the string. The separator and reserved-word set is grounded in the POSIX Shell Command Language's own list of what can precede a simple command, not in the two sampled separators; truncating it to `\A` and `do` alone would be *less* grounded, since the set would then trace to a sample of two rather than to a grammar. `\bdo` cannot fire inside `sudo` or `docker` (no word boundary before `d` in the first; `[ \t]*sleep` fails in the second).

`_classify_bash_wait_shape(command: str | None) -> str` is three branches: `_BASH_WAIT_NO_COMMAND` when `command` is not a string, `_BASH_WAIT_SLEEP_POLL` on a regex match, `_BASH_WAIT_OTHER` otherwise. It is a pure string function, so its whole test surface is literal command strings — no record dicts, no fixture transcript.

Two known limits ship as disclosed report prose rather than as more matching machinery. The match is textual with no shell parsing, so a quoted or heredoc-embedded `sleep` counts. Requiring a leading digit means `sleep $INTERVAL` falls to `other Bash wait`. Both are named in the block's prose with their direction: the digit requirement biases toward under-counting sleep-poll waits, never over-counting.

**3. Three zero-seeded rows in their own block; the four-row loop is untouched.**
A new `_OWN_BASH_WAIT_SHAPES: tuple[str, ...]` iterated by its own print loop after the four-row table, with the same five-column layout and the same `<32 >9 >12 >12 >12` widths. Four parallel accumulators seeded exactly like `_CACHE_REBUILD_ATTRIBUTIONS`'s (`:6178-6181`), populated in the same `for cand in idle_gap_candidates` loop under an `if cand["bash_shape"] is not None` guard.

Folding the shapes into `_CACHE_REBUILD_ATTRIBUTIONS` as three extra rows was rejected: it would break the printed table's stated "the rows below sum exactly to that row" invariant (`:6461-6462`) and silently change what two existing tests assert (`test_transcript_analysis.py:8687`, `:8825`). A single scalar disclosure line — the lighter alternative — was rejected because it cannot carry the `5m-1h $` band or reconcile column-for-column against the parent row, and every other figure in this report is a zero-seeded table row that reconciles against its parent.

`no command recorded` is a third row rather than a fold-into-`other` plus a non-zero-only parenthetical. Three uniform rows keep the sum invariant exact and trivially testable, avoid conflating "looked, not a sleep-poll" with "could not look", and need no conditional print path — the report's own zero-seeding convention (`:6159-6161`, `:6166-6170`) exists precisely so an empty bucket renders visibly instead of vanishing.

Rows stay un-indented with labels that prefix-collide with nothing else printed. That keeps `_extract_cache_rebuild_attribution_row` (`test_transcript_analysis.py:7860-7873`) usable verbatim for the new block — its `line.startswith(row_label)` match would fail on an indented row, and `_table_cols` excludes indented lines outright (`test_row_startswith_excludes_indented_lines`, `:327`). No new extractor is needed.

**4. The register entry is a dated follow-up under the section it corrects, and publishes no machine-wide figure.**
Append after `docs/cost-levers-considered.md:483`, opening with the established `**2026-09-07 follow-up, <clause>.**` lead-in (precedent at `:32`, `:104`, `:115`, `:201`, `:356`, `:421`). The entry's whole job is to correct the premise of follow-up (1) in that section's own closing paragraph; a reader arriving at `:483` has to meet the correction there, not via a cross-reference hop. A new `## From` section would have to restate follow-up (1) in order to correct it, and the register's own precedent for correcting a prior entry is an in-section dated follow-up, not a replacement section. The file has no table of contents, so nothing else needs updating. Follow-up (2), per-agent-type attribution, is left untouched — it was declined on its own separate grounds.

The entry cross-references rather than restates: `run_in_background` converting a blocking wait into an equally long idle wait is the `background-slow-bash-calls.md` section's second row (`:245`), and `Monitor` being event-driven with no periodic heartbeat is `docs/design-decisions.md` §49.

It states the sub-shape finding qualitatively and withholds the machine-wide counts, naming the reason. `docs/design-decisions.md` §49 already set that exact split — it published its directional finding and withheld its counts because "the corpus mixes private-project and public transcripts, and any count, ratio, median, or duration would inherit the private half's composition." Any figure the entry does quote comes from a `--this-repo`-scoped run, which is content derived only from this repo's own history and so outside the private-provenance class by CLAUDE.md's own carve-out. The instrument change is what makes withholding costless: the figure is now one command away on any reader's own corpus, which is the same argument `:481` already makes for this section's prior deliverable.

### Assumption ledger

**Root problem.** The `waiting on own Bash call` row is the plurality-to-majority subagent idle-gap cause and is unsplit, so the sleep-poll sub-pattern's dollar share rests on a hand sample rather than on the instrument, while the merged register entry still names "cutting the stall" as the first thing to re-examine behind a lever the consult found does not exist.

**Givens** (fixed beyond this plan's reach):

- **G1 — Claude Code owns the `tool_use` block shape and the marker literals; a harness release can change either without notice.** [verified: `transcript-analysis.py:5887-5889`] Vendor owns the emitting side.
- **G2 — A transcript carries no per-call execution-time field, so a recorded command's elapsed window cannot be separated from permission-prompt wait or operator idle.** [verified: `docs/cost-levers-considered.md:244`] Vendor owns the schema.
- **G3 — No mechanism reaches this stall: backgrounding converts a blocking wait into an equally long idle wait absent independent work to interleave, and `Monitor` has no periodic heartbeat to refresh the cache TTL.** [verified: `docs/cost-levers-considered.md:245`, `docs/design-decisions.md` §49] Both are vendor-side capabilities; dissolving the dependence needs a harness feature, not a plan.

**Assumptions:**

1. Live Bash `tool_use` blocks carry a string `input.command`, so the classifier has something to match. [verified: `.claude/plans/idle-gap-sleep-poll-attribution.md:9-11` — the Context's two sampled command shapes could only have been read from live command text; `transcript-analysis.py:5959` reads the same block dict that holds it]
2. The three shape rows sum exactly to the `waiting on own Bash call` row, because the shape is set only when the cause is `_ATTR_OWN_BASH` and the classifier returns exactly one of the three labels for every such candidate. [verified: `transcript-analysis.py:6395-6402` accumulation pattern]
3. Only `--this-repo`-scoped figures are publishable from this report; machine-wide figures inherit the private half of a mixed corpus. [verified: repo `CLAUDE.md` § "Also redact structural fingerprints and provenance"; `docs/design-decisions.md` §49's own withheld-counts paragraph]
4. A high sleep-poll share implies no lever, so this plan ships no behavior change. [verified: `docs/cost-levers-considered.md:245`; `docs/design-decisions.md` §49]
5. The `--this-repo` corpus's own-Bash row is small — 8 rebuilds in the sample block's last pasted run — so the published sub-split may be too small to be directional, and the entry must not present it as one. [verified: `docs/transcript-analysis.md:851`]
6. `_extract_cache_rebuild_attribution_row` parses the new block unchanged, given identical column widths and labels that prefix-collide with no other printed row. [verified: `test_transcript_analysis.py:7860-7873`]
7. `select-tests.py` selects `claude/.claude/hooks/tests`, `claude/.claude/scripts/tests`, and `claude-skills/skills/tests` for this diff — script change via `DOMAIN_RULES` (`select-tests.py:352`), docs changes via the `DOCS_DIR` blanket (`:467`), plan file selecting nothing (`:357`). [verified: `select-tests.py:350-359`, `:467`]
8. A selected domain directory is no longer dropped when a contained file is also selected, so a scoped run's pass count can be taken at face value. [verified: `resolve_target_paths` implements containment dedup at `select-tests.py:604-650` and runs unconditionally before the pytest invocation at `:695`]
9. The textual `sleep` match's false-positive rate against quoted or heredoc-embedded `sleep` in the live corpus is unmeasured, as is whether `no command recorded` is ever non-zero there. [unverified]

**Mechanisms:**

- Three-element return from `_attribute_idle_gap_cause`, replacing the current 2-tuple — the winning marker is known only inside that scan, and the two lighter alternatives above each duplicate the precedence rule or split it across two entry points. `anchors: row1`
- `_classify_bash_wait_shape` plus `_SLEEP_POLL_COMMAND_RE` as a pure string classifier separate from the scan — isolates the one part whose accuracy is unmeasured behind a test surface that needs no record dicts. `anchors: row9`
- `_OWN_BASH_WAIT_SHAPES` and its own three-row print block — a separate uniform loop preserves both the four-row table's sum invariant and its existing tests' literal meaning. `anchors: row2, row6`
- Dated follow-up appended under the existing register section, cross-referencing the two rejection sources rather than restating them. `anchors: row3, row4`
- One `--this-repo` run feeding both the doc's sample block and the entry's figures — one scope, one run, internally consistent output. `anchors: row3, row5`

## Critical files

Two sequenced `code-writer` dispatches. Phase 2's input is Phase 1's shipped instrument, so they cannot run in parallel; their file sets are disjoint.

**Phase 1 — instrument and tests.**

`claude/.claude/scripts/transcript-analysis.py` (modify):
- `:5875-5889` — add `_BASH_WAIT_SLEEP_POLL`, `_BASH_WAIT_OTHER`, `_BASH_WAIT_NO_COMMAND`, `_OWN_BASH_WAIT_SHAPES`, and `_SLEEP_POLL_COMMAND_RE`, following the existing print-order-tuple comment convention at `:5880-5885`.
- new `_classify_bash_wait_shape` immediately before `_attribute_idle_gap_cause`.
- `:5928-5990` — `bash_tool_use_ids` set becomes an id-to-command dict; the winning command joins the `last_cause, last_marker_ts` tuple assignment at `:5984`; all three return statements (`:5987`, `:5989`, `:5990`) gain the shape element; the docstring's `Returns` paragraph documents "shape label when the cause is own-Bash, `None` otherwise."
- `:6325-6343` — unpack the third element; add `"bash_shape"` to the candidate dict beside `"attribution"`.
- `:6178-6181` — four parallel accumulators keyed on `_OWN_BASH_WAIT_SHAPES`.
- `:6395-6402` — accumulate under `if cand["bash_shape"] is not None`.
- after `:6491` — the new `## Own-Bash wait shape [unverified]` block. Its prose must carry: that the three rows partition the `waiting on own Bash call` row above and sum exactly to it; that the label comes from the winning Bash `tool_use`'s own recorded command; that `sleep-poll wait` means a `sleep <number>` in shell command position; that the match is textual with no shell parsing, so a quoted `sleep` counts and `sleep $VAR` does not, biasing toward under-counting; that only the gap-closing call's own command is classified, so a repeated `sleep N; check` loop is classified once per gap it closed; that a high sleep-poll share points at no lever (cross-referencing `docs/cost-levers-considered.md`); and the closing `[unverified]` tag the sibling block already carries at `:6481`.

Reuse: `_pct_of` with `statistics.median` for the median-coverage cell (`:6486`), the `dict.fromkeys` zero-seeding idiom (`:6178-6180`), the `[unverified]` header-and-closing-line convention (`:6460`, `:6481`).

`claude/.claude/scripts/tests/test_transcript_analysis.py` (modify):
- `TestAttributeIdleGapCause`, 15 unpack sites between `:7935` and `:8126` — each gains a third name; no assertion changes.
- new tests in that class: a two-parallel-Bash window where only the later call is a sleep-poll, pinning that the shape follows the *winning* marker (extends the existing `test_two_bash_pairs_poll_loop_last_marker_wins_the_later_tool_result` at `:8068`); a coordinator-marker win returning `None` shape; a Bash block with no `input` returning `no command recorded`.
- a new small class for `_classify_bash_wait_shape`, one literal command string per case: both hand-sampled shapes, `&&` separator, `sleep $VAR`, `--sleep-interval 5`, a quoted `sleep 5` inside `echo` (documenting the accepted false positive), `None`.
- new report-level tests: a sleep-poll fixture populating the sleep-poll row; the three rows rendering zero-seeded when the corpus has no subagent rebuilds; the three rows summing exactly to the own-Bash row on Rebuilds, Excess $, and 5m-1h $ — modeled on `test_main_origin_rebuild_enters_no_attribution_row_and_totals_reconcile_with_subagent_row` (`:8639-8693`).

Reuse: `_bash_use`, `_tool_result`, `_user_msg`, `_asst` (`conftest.py:194-228`); `_tool_result_record` and `_meta_marker_record` (`:7905-7920`); `_write_subagent_jsonl`/`fake_projects`/`capsys` and `_extract_cache_rebuild_attribution_row` unchanged (`:7860-7873`).

**Phase 2 — one live run, then both docs edits.**

Run `.venv/bin/python3 claude/.claude/scripts/transcript-analysis.py cache-rebuild --this-repo` once (~33s at this corpus size per `docs/transcript-analysis.md:920`); every figure in both files below comes from that single run.

`docs/transcript-analysis.md` (modify): re-paste the whole `--this-repo` sample block (`:782-885`) from that run rather than splicing the new rows into stale numbers — a block half from one run and half from another is incoherent, and PR #913 set the re-paste precedent when it added the attribution table at `:826-854`. Add one prose paragraph after `:912`, in the same per-block style as the `Subagent idle-gap cause attribution` paragraph at `:900-912`.

`docs/cost-levers-considered.md` (modify): append the `**2026-09-07 follow-up, ...**` paragraph after `:483`.

## Verification

Scoped test run, per this repo's CLAUDE.md Commands block:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/
```

Expect `select-tests.py` to report `domain-selected` with `claude/.claude/hooks/tests`, `claude/.claude/scripts/tests`, and `claude-skills/skills/tests`. A `full-suite` result on this diff means a path went unmatched — that is a `select-tests.py` rule-table bug to report, not a reason to widen by hand. No shell files change, so ShellCheck is not in scope.

Behaviors the new tests must pin:

1. Both hand-sampled command shapes classify as `sleep-poll wait`; `sleep $VAR` and `--sleep-interval 5` do not; a `sleep` inside a quoted `echo` does (the accepted false positive, asserted so a later widening is a deliberate change).
2. A non-string or absent `command` classifies as `no command recorded`.
3. The shape follows the winning marker: with two parallel Bash calls in one window where only the later is a sleep-poll, the sleep-poll row is the one populated; reversing them flips it.
4. A coordinator-marker or background-task win carries no shape, and a main-origin rebuild carries neither attribution nor shape.
5. The three shape rows sum exactly to the `waiting on own Bash call` row on Rebuilds, Excess $, and 5m-1h $.
6. A corpus with no subagent idle-gap rebuilds renders all three shape rows zero-seeded with `n/a` median coverage, alongside the four attribution rows, without `statistics.median` raising on an empty list.
7. The four existing attribution rows' counts and dollars are unchanged by the addition.

Live-corpus check on the Phase 2 run: the printed shape rows sum to the printed `waiting on own Bash call` row — the same invariant as item 5, checked once against real data rather than a fixture.

Publication check on the diff before commit: no command string, session ID, project label, or per-session row appears in either docs file, and every quoted figure traces to the single `--this-repo` run named in the entry. This applies to the plan file itself, which ships in the same PR.

## Out of scope

- Implementing any behavior change based on the sleep-poll finding — there
  is none; this plan is measurement plus a documented dead end only.
- The sub-5-minute-check-in-cadence candidate the consult separately
  surfaced. That is an ungated, unmeasured idea going into a GitHub issue,
  not this plan.
- Widening `_SLEEP_POLL_COMMAND_RE` past a `sleep <number>` in command position — no shell parsing, no variable-argument match, no per-tool poll idioms (`gh run watch`, `kubectl wait`). Each would be an ungrounded guess at a shape no sample supports, and the report prose discloses the resulting under-count instead.
- Splitting `sleep-poll wait` further into loop-versus-single-sleep. Both hand-sampled shapes point at the same absent lever, so the split would carry no decision value.
- A machine-wide `cache-rebuild` run, or publishing the cross-machine hand-sample percentages. Any such figure inherits the private half of a mixed corpus; the entry states the direction and leaves the counts to a reader's own rerun.
- Attributing the main-origin idle-gap row. The existing block excludes main origin deliberately (`transcript-analysis.py:6476-6478`), and nothing here changes that.
- Follow-up (2) in the corrected register section, per-agent-type attribution — already declined on the dispersion result's own grounds.
- The pre-existing quirk that a Bash `tool_use` block with no `id` puts `None` into the id lookup, where a `tool_result` with no `tool_use_id` could match it. The set-to-dict change preserves the behavior exactly; fixing it is a separate concern with its own test.
