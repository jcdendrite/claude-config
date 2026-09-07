# Handoff-nudge deep-tail lever: scope a REARM_SPACING re-test and price a two-tier nudge

## Context

Scope an investigation into whether the handoff-nudge cost lever needs
retuning to address a growing deep-context tail, and decide whether either
of two named-but-unpriced candidate levers merits a full measurement or
build-out.

`docs/case-studies/handoff-hard-block-position.md` found that raising
`HANDOFF_NUDGE_BLOCK_AT` from 230,000 to 470,000 was a clean cost win (mean
$/PR fell 47.5%), but the share of session dollars spent past the unchanged
150,000-token advisory threshold rose from 57.6% to 77.3% — a deep-context
tail is growing underneath the collapsed hard-block rate. That study's own
revisit trigger stays in place rather than relaxing. Two candidate levers
are named but never priced: retuning `HANDOFF_NUDGE_REARM_SPACING` (currently
80,000) under the now-deeper `BLOCK_AT=470,000` regime, and building a
two-tier (informational-then-hard) nudge named in `docs/handoff-nudge.md`'s
Known Limitations as a way to reduce dismissal-as-noise risk but explicitly
"not implemented." The intended outcome is a decision, for each lever,
between running a full measurement/build and declining — following this
repo's own convention (`docs/cost-levers-considered.md`) of fixing a
go/no-go rule before measuring, not after.

A third candidate emerged during scoping: the nudge's own phrasing has
never been examined as a lever, and the adherence rate that would tell it
apart from the two-tier lever turns out to be retrospectively measurable
today. See Approach.

**Settled, not reopened by this plan:** moving `HANDOFF_NUDGE_ABS_CAP`
(150,000) itself. `docs/case-studies/handoff-nudge-cap-recalibration.md`
grounded it as the cheapest trustworthy bucket's upper edge;
`docs/case-studies/handoff-threshold-impact.md` tested tightening it and
found no clean cost win; and `docs/cost-levers-considered.md`'s own
re-bucketing (both this machine and a macOS peer session) found the
monotonic cost-vs-depth curve still rising with no plateau through
300,000–400,000 tokens/PR.

## Exploration evidence (for plan-architect)

Gathered by a `general-purpose` subagent (`model: sonnet`) reading
`claude/.claude/scripts/transcript-analysis.py` and
`claude/.claude/hooks/nudge-handoff-near-context-cap.sh` directly. Absolute
paths:
- `claude/.claude/scripts/transcript-analysis.py`
- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`
- `docs/handoff-nudge.md`
- `docs/cost-levers-considered.md`
- `docs/case-studies/handoff-hard-block-position.md`
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`

**A. `rearm-backtest` (lever 1 candidate instrument):**
- `cmd_rearm_backtest` (transcript-analysis.py:10531) wraps `_rearm_backtest_report` (10543). It scopes sessions (`--this-repo`/`--projects`, `--since`, `--branches`), extracts each session's real turn sequence and prices it (`_extract_rearm_session_turns`, 10139), builds a ramp curve for counterfactual post-crossing repricing (`_ramp_curve_from_corpus`, 10227), computes real operator-response lag by joining `.handoff-nudge.log` (`_operator_response_lag_from_log`, 10363 — excludes `action=block` lines at 10397 since a forced overshoot isn't voluntary compliance), then simulates each candidate spacing under both a perfect-compliance and a lagged-compliance model (`_simulate_rearm_spacing`, 10413), reporting `$` delta and mean-context (`C_bar`) delta per spacing versus a no-rearm baseline.
- **`HANDOFF_NUDGE_BLOCK_AT` never appears anywhere in `transcript-analysis.py`** (verified by grep, zero hits). The simulation has no concept of a hard stop — it keeps re-arming indefinitely regardless of how deep a session runs. The only `BLOCK_AT`-adjacent logic is the `action=block` exclusion at line 10397, which is a filter on the log join, not a modeled cutoff, and needs no update for a different `BLOCK_AT` value.
- **Conclusion: `rearm-backtest --this-repo` can be re-run today with no code changes**, and doing so against the current corpus automatically reflects the deeper tail — the "corpus whose deep tail ended at 230,000" was a fact about available data at the time of the original run, not a parameter in the code.
- The historical scoring (quoted from `_rearm_backtest_report`, 10674-10694): for each spacing/compliance pair, `delta = total - baseline_total` (dollars) and `delta_c_bar` (mean-context). 80,000 was chosen over the dollar/context-tied 40,000 because it gives "roughly 2.6x the sample's median 30,624-token operator-response lag as separation between nudges" — a dismissal-risk argument applied on top of the tie, not itself computed by the script.
- Required flags (parser, 11797-11838): `--this-repo`/`--projects` (mutually exclusive), `--config-dir` (repeatable), `--since Nd`, `--branches`, `--no-redact`, `--spacings N1,N2,...` (default `40000,80000,120000`). No `--block-at` flag exists.

**B. Hook escalation structure (for scoping a two-tier nudge, lever 2):**
- Advisory-vs-block branch point: `nudge-handoff-near-context-cap.sh:632-633`, a 4-conjunct `if` (estimate ≥ `BLOCK_AT`; not the session's first-ever crossing; event is `PostToolBatch`; no live `/handoff` bypass marker). Block branch: 634-650 (logs `action=block`, `exit 2`, no JSON). Advisory branch: 652-667 (JSON `additionalContext` payload, no `exit`).
- **No existing severity/enum field to reuse.** Today's log format has exactly one advisory severity; the only signal distinguishing block from advisory is the presence/absence of `action=block` — a binary check, not an enumerated field. Adding an "informational" tier below advisory needs its own threshold constant (mirroring `resolve_block_at`, 175-180), its own re-arm/marker state (today's single `FIRED_MARKER`/`LAST_FIRED_AT` at 545-558 cannot distinguish "last fired at info tier" from "last fired at advisory tier"), a new `action=` value or field, and changes to the escalation `if` (632-650) and advisory-fire block (652-667).
- **Consumers of the binary distinction that would need updating, not just tolerate a new value:** `_operator_response_lag_from_log` (transcript-analysis.py:10397) explicitly checks the literal string `"block"` — a third tier is not automatically excluded from the lag measurement unless this check is revisited. `_parse_nudge_log_entries` (10299-10360) already treats `action` as optional-if-present (10344-10345), so it would parse a new value without a code change, but every reader must still decide what to do with it. `spend-over-threshold` and `rearm-backtest`'s simulation never read `action` at all — unaffected. Test files with literal-string assertions on `action=block`: `test_transcript_analysis.py` (~18126-18353) and `test_nudge_handoff_near_context_cap.py` (multiple sites, ~1348-2550).

**C. Measurement primitives for pricing lever 2's dismissal-risk benefit:**
- `_operator_response_lag_from_log` (10363) already computes per-nudge operator-response lag — the instrument behind the existing "median 30,624-token" figure.
- The hook already records and logs a per-session ignored-re-arm counter (`ignored=` on every `nudged` line), and `_parse_nudge_log_entries` already parses it into `entry["ignored"]` (10346-10348) — **but no existing subcommand aggregates or reports a distribution over it.** Parsed and available, currently unused downstream.
- **No existing instrument can retrospectively price a two-tier nudge's dismissal-risk effect**, because today's log has exactly one advisory severity — there is nothing to distinguish, after the fact, which historical fires would have been tier-1 (informational) vs. tier-2 (today's advisory) under a hypothetical two-tier scheme. Pricing this lever requires shipping the hook change first and measuring forward, not a retrospective backtest like lever 1.
- `_rearm_backtest_report`'s single-threshold, single-spacing simulation model (`_simulate_rearm_spacing`) has no notion of a second, earlier threshold to insert — extending it to a two-tier model would be new simulation logic, not a parameter change.

**D. Conversion signal, root resolution, phrasing history, and cross-machine mechanics (added after the first `/plan-review` pass):**

- **The `handoff` log line exists, is parsed, and is read by nothing.** `claude/.claude/scripts/handoff-record-conversion.sh:22` appends `printf 'handoff session=%s\n' "$session_id"` to `"$config_dir/.handoff-nudge.log"` — the same file the hook writes `nudged` and `schema-drift` lines to. `claude-skills/skills/handoff/SKILL.md:179-189` invokes it after the handoff file is written and verified, and `:183` names the intended consumer: "pairs with that hook's `nudged` lines for a future nudge→handoff conversion report." `transcript-analysis.py:10070` lists `handoff` in `_NUDGE_LOG_LINE_KINDS` and `:10356-10359` parses it. A corpus grep for readers of that kind outside `_parse_nudge_log_entries` returns zero hits — the same untapped shape as the `ignored=` field in part C.
- **`rearm-backtest` is multi-root by default, and its log join is not.** `cmd_rearm_backtest` resolves roots via `_resolve_cost_roots` (`:10539`), which unconditionally unions `config_dir()`, every `declared_transcript_roots()` entry, and any `--config-dir` extras (`transcript_analysis/scope.py:526-535`); its only narrowing branch is gated on `subcommand == "cost" and args.summary` (`:523-524`). `scope.py:363-367` states the consequence outright: "a populated `~/.claude/transcript-config-dirs` makes `--this-repo` multi-root by default with no `--config-dir` flag at all." But `_rearm_backtest_report` joins at the single path `config_dir() / ".handoff-nudge.log"` (`:10627`) while building `session_traces` across every root (`:10619`). Six roots are declared on this machine; **4 of the 6 carry a `.handoff-nudge.log`.**
- **No flag reaches single-root scope, and both attempts fail silently.** The subcommand-level `--config-dir` is additive and deduped, so pinning the default root is a no-op (`scope.py:526-548`). The top-level `--config-dir` reassigns `scope.PROJECTS_DIR` (`main():12049`), which `_resolve_cost_roots` never reads; it also arms `_resolve_project_scope`'s fail-closed exit (`scope.py:396-405`), changing error behavior without changing scope. `rearm-backtest` is absent from `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR` (`scope.py:480-485`) despite meeting that tuple's stated membership rule, so `main()`'s guard against the two same-named flags diverging does not cover it.
- **The exclusion count is not a measure of the multi-root defect.** `_operator_response_lag_from_log` (`:10394-10409`) excludes entries whose session is absent from `session_traces`; more roots make that set larger, so multi-root can only reduce exclusions. The real defect is the asymmetry: five roots' sessions feed spend and traces while their nudges sit in unread logs.
- **Missing per-root logs are already safe.** `_read_bounded_log_lines` returns `[]` for an absent or unreadable file (`:8740-8741`, `:8747-8748`), so the root-aware join needs no guard for the 2 roots with no log. Its `_NUDGE_LOG_MAX_READ` tail-truncation (`:8743-8745`) now applies per root rather than to one combined read.
- **`account-N` labels are stable by construction.** `_redaction_ordinals` (`scope.py:179-199`) sorts roots by resolved path and assigns ordinals from that order, explicitly so "a position-based ordinal would renumber every other declared root depending on which profile produced the report." Stable across runs on one machine with an unchanged root set; not comparable across machines.
- **Multi-account aggregates are established practice in this repo.** `docs/handoff-nudge.md:26` publishes "426 fired sessions across 6 accounts"; `docs/case-studies/handoff-threshold-impact.md:128` publishes retention and window facts "across all six accounts"; `docs/case-studies/cold-cache-attribution.md:268` treats the multi-account corpus as its default; `docs/case-studies/hashline-edit-format.md:93-104` publishes a per-account breakdown under `account-N` labels, "never the raw config-dir path or account name"; `docs/cost-levers-considered.md:37-39` names account types. `cost --summary`'s narrowing (`scope.py:513-524`) is an attribution guard for a *labeled* per-account figure, not a bar on pooled aggregates.
- **`ignored=` is optional on older lines.** `_parse_nudge_log_entries`' docstring (`:10311`) states `ignored` and `skills` are "present on log lines written by hook versions that record per-fire telemetry; absent on older lines"; `:10346-10348` only sets the key when present.
- **Instrument age does not truncate the window.** `handoff-record-conversion.sh` first landed 2026-08-22, before the 2026-08-25 retention floor G2 establishes.
- **The report's existing tests tolerate an appended section.** `TestRearmBacktestReport` (`claude/.claude/scripts/tests/test_transcript_analysis.py:18428-18540`) reads the spacing table through `_table_cols(out, header_contains="Spacing", …)` and otherwise asserts substrings.
- **`docs/handoff-nudge.md`'s log-format table is wrong in two ways.** Its "Log location" section (`:95-104`) opens "The hook appends one line per significant event… Two line types appear," then tables only `nudged` and `schema-drift`. The `handoff` line is undocumented, and it is not written by the hook. No test parses this table: `test_doc_counts.py`'s only cases for this file (`:445-489`) pin numeric literals elsewhere.
- **Phrasing has changed repeatedly, but never in register.** `git log -p` over ~10 revisions of the advisory `additionalContext` and block stderr text shows the threshold framing moving from percent-of-window to absolute tokens, and a cost-comparison clause added then simplified. Every variant stayed advisory and addressed to the agent as a relay request. Current text at `nudge-handoff-near-context-cap.sh:660`: "If the current task is not close to done, suggest running /handoff to the user… If the task is nearly complete, ignore this and finish."
- **Cross-machine mechanics.** `config_dir()` (`claude/.claude/scripts/_config_dir.py:22-30`) resolves from `$CLAUDE_CONFIG_DIR` or `$HOME`, never from the script's own path, so a branch checkout run on a second machine reads that machine's own roots and logs — no merge, no waiting for this branch to land.

## Prior art on forward-looking pilots (for plan-architect's go/no-go framing)

`docs/cost-levers-considered.md` has repeatedly declined forward-looking,
build-first-measure-after pilots for uncertain benefit at achievable
sample sizes — e.g. under `opus-frontload-review-rounds.md`: "A forward-
looking controlled pilot flipping a `model:` pin for a treatment arm |
Declined, not deferred | ... the outcome variable's variance puts a
sub-one-round effect out of reach at achievable n." Lever 2 (two-tier
nudge) is structurally the same shape: no retrospective price is possible,
so any measurement requires shipping first.

## Approach

Fix the log join to be root-aware, build the nudge→handoff conversion report on top of it, run the corrected instrument once per machine, and record four verdicts in `docs/cost-levers-considered.md`. Every decision rule below is frozen in this file before any command runs.

Three things reshaped this plan after its first `/plan-review` pass. The engineer works across two machines, so a single-machine corpus is half the available evidence. The nudge's *phrasing* was never examined as a lever. And the adherence rate that would tell levers 2 and 3 apart turns out to be retrospectively measurable from a log line this repo already writes and already parses but has never read.

A fourth thing surfaced during that rework and changes the plan's spine: **the measurement this plan was going to run was reading the wrong population.** `_resolve_cost_roots` unions the default config dir with every entry in `~/.claude/transcript-config-dirs` (`transcript_analysis/scope.py:526-535`), so `rearm-backtest --this-repo` is multi-root by default with no flag given — six roots on this machine. But `_rearm_backtest_report` joins its nudge log at a single hardcoded path, `config_dir() / ".handoff-nudge.log"` (`:10627`). Sessions from all six roots feed `sessions_data`, `session_traces`, and the dollar simulation, while only one root's nudges are ever joinable. The median operator-response lag — the sole input to the realistic-compliance arm and to Gate C — is therefore measured on one account and applied to six. The same defect would hit the conversion instrument, whose numerator would come from one log while its denominator spanned six roots, biasing the conversion rate downward by construction.

**So the fix comes before the measurement, and it widens rather than narrows.** Multi-root scope is kept: pooled, pseudonymous multi-account aggregates are this repo's established practice for exactly this kind of operational telemetry (`docs/handoff-nudge.md:26` publishes "426 fired sessions across 6 accounts"; `docs/case-studies/hashline-edit-format.md:93-104` publishes a per-account breakdown under `account-N` labels; `docs/case-studies/cold-cache-attribution.md:268` treats the multi-account corpus as its default). What changes is the join: read each resolved root's own `.handoff-nudge.log` and join within-root. That fixes the bias, raises the lag sample and the conversion denominator instead of shrinking them, and serves both instruments in one function.

**One run per machine, not two.** Because the join fix must land before any number is trustworthy, there is no longer a free pre-code round. One command per machine, after the fix, feeds all five gates from one corpus on one date — which is strictly better pre-registration than two rounds whose corpora drift apart between them.

**Ordering is the load-bearing part.** The rules below are frozen before the commands run. If the numbers come back and a threshold looks inconvenient, the answer is the verdict the rule produces, not an edited rule — this repo's register records pre-registered gates and their outcomes precisely so that latitude is closed (`docs/cost-levers-considered.md:474`, `:489`). This applies identically to Gates D and E and to the cross-machine replication rule.

**One term, held throughout: "conversion."** The adherence rate the engineer asked for is the nudge→handoff conversion this repo already named and deferred — `claude-skills/skills/handoff/SKILL.md:183` says the conversion-signal append "pairs with that hook's `nudged` lines for a future nudge→handoff conversion report." This plan builds that report. "Adherence" and "conversion" are the same quantity; the code, the docs, and the register use "conversion."

### The runs — two machines, one round

Two runs total, both after the join fix and the conversion section land on this branch.

```
python3 <branch-worktree>/claude/.claude/scripts/transcript-analysis.py rearm-backtest \
  --this-repo --since <N>d --spacings 40000,80000,120000,160000
```

- **No `--config-dir`, no `--no-redact`, no `--projects`.** Multi-root is the intended scope, and `rearm-backtest` already refuses `--no-redact` once more than one root is in scope (`transcript-analysis.py:10561-10567`) — the configuration the instrument was built to make publishable.
- **Invoked from a checkout of this branch, not the installed copy.** `claude/.claude/**` reaches a machine only on `git pull`, and the second machine will not have pulled. `config_dir()` resolves from `$CLAUDE_CONFIG_DIR` or `$HOME`, never from the script's own location (`claude/.claude/scripts/_config_dir.py:22-30`), so a branch checkout still reads that machine's own live logs and roots.
- **Pre-register the cutoff *date*, not `N`.** `--since Nd` is a rolling `time.time() - days*86400` cutoff on each session's first timestamp (`transcript_analysis/scope.py:660-677`), so each machine computes its own `N` for its own run date. The frozen cutoff is **on or after 2026-08-31 00:00** — the after era's first full day, since PR #769 merged 2026-08-30 17:55:36 -0700 and `handoff-hard-block-position.md` excluded 2026-08-30 as a straddling day (`docs/case-studies/handoff-hard-block-position.md:5`, `:136`). Record the `N` used and the generated date for each run; the report self-documents both as `last <N>d, generated <date>` (`transcript-analysis.py:10643-10644`).
- **Record each run's resolved-scope header verbatim**, including its root count — `_print_resolved_scope` (`:10577`) renders it via `_root_count_desc` (`scope.py:416-438`). The root count is disclosure, not a gate: a run resolving a different number of roots than its sibling is a fact to state, not a reason to discard.
- **Why `--since` and not all time.** The simulation models no hard stop, so the `BLOCK_AT` regime enters through the session population: before-era sessions were truncated near 230,000 and carry artificially short tails. Including them dilutes the deep-tail signal this re-test exists to isolate. The original run was unscoped (`.claude/plans/rearm-hook-band-spacing.md:25`, 127 sessions) — the right comparator to name, the wrong population to re-decide on.
- **`160000` added to the candidate set** so the report carries `DeltaUSD`/`DeltaCbar` for the spacing that would restore a 2x separation margin at the upper end of a plausible lag increase — informational only: Gate B's condition 4 and Gate C's tie-break both foreclose adopting anything above 120,000 regardless. It is a flag value, not a code change.
- **An optional unscoped run is context only.** If run, disclosed alongside; never substituted into any gate. Pre-registering which run decides is what stops two populations from becoming two chances at a preferred answer.

**One discarded run, disclosed not pasted.** A peer session on the macOS machine already ran the pre-correction invocation and returned a report resolving 6 roots across 118 project dirs. It is unusable for any gate — its lag median was computed from one root's log while its sessions spanned six, which is the defect this plan fixes. Record that it happened and why it was discarded; paste none of its figures, since a number produced by a known-biased join would otherwise sit in the plan file looking like evidence.

### Replication rule — applies to Gates A/B/C

**Gate A is evaluated per machine.** Each run is separately admissible. This is mechanical, not stylistic: a median lag cannot be pooled from two printed medians.

**Gates B and C decide on the Linux run.** That machine's corpus grounds every prior tuning of this constant (`rearm-hook-band-spacing.md`, `handoff-hard-block-position.md`). The second machine's run is a **replication check, not a second vote.**

1. **Both admissible, verdicts agree** — adopt the agreed verdict. "Agree" means both keep 80,000, or both adopt the same spacing S.
2. **Both admissible, verdicts disagree** — verdict is **"Inconclusive — replication disagreed,"** the default stays 80,000, both tables recorded. Two corpora disagreeing about an effect whose materiality floor is $50 is exactly the noise Gate B's floor exists to reject, and this constant ships to every stow consumer.
3. **Only the Linux run admissible** — proceed, naming the missing machine. A Linux-only run **may confirm "keep 80,000"** but **may not by itself change the shipped constant**; if its gates recommend a change, the verdict is **"Change recommended, held pending replication,"** recorded with the recommended S. The asymmetry is deliberate and costs nothing in the expected branch: keeping the default is free, shipping a change to every stow consumer on one unreplicated corpus is not. It also removes any incentive to time a run so the second machine misses it.

### Gate A — admissibility, per machine

Read off each report's own header (`transcript-analysis.py:10645-10652`). A run decides nothing unless both hold: **sessions in scope ≥ 30** and **operator-response-lag sample ≥ 10 joined `nudged` lines**. The session floor is set by analogy to the n=23 "trustworthy sample" bar this hook's own cap grounding used (`docs/handoff-nudge.md:16`); the lag floor exists because the median lag is the sole input to the realistic-compliance arm and to Gate C. Both are engineering judgment, not a power calculation, and are stated as such. The root-aware join makes both floors easier to clear than before, which is a reason to trust a pass, not a reason to lower them. **If both machines fail, the verdict is "Inconclusive — corpus too thin under the after-era window," the default stays 80,000, and no widening is attempted** (see Out of scope).

### Gate B — dollar-driven change

Adopt a spacing S ≠ 80,000 only if, under the **realistic** compliance arm, all four hold:

1. S's `DeltaUSD` beats 80,000's by more than **$50** — the materiality floor this register already applied twice (`docs/cost-levers-considered.md:474`, citing `:388`);
2. S's `DeltaCbar` is no worse than 80,000's;
3. S ≥ 2 × the newly measured median lag;
4. S ≤ 120,000 — the ceiling Gate C's tie-break establishes below, applied here too: any spacing above 120,000 silently converts a 200k-window model's re-arm to one-shot, a risk no dollar saving outweighs regardless of which gate recommends the change.

The original run's realistic-arm spread between 40,000 and 80,000 was $2.05 (−261.81 vs −259.76) on 127 sessions and was called indistinguishable (`.claude/plans/rearm-hook-band-spacing.md:37-39,47-51`); the $50 floor sits far above that noise, deliberately.

### Gate C — dismissal-risk-driven change, evaluated independently of Gate B

80,000 was chosen over the tied 40,000 solely for separation: 80,000 / 30,624 ≈ 2.6x the then-median operator-response lag (`docs/handoff-nudge.md:20`). A deeper tail plausibly lengthens that lag. **If the new median lag exceeds 40,000, the incumbent no longer clears 2x its own grounding margin.** In that case adopt the smallest candidate S with S ≥ 2 × new median lag, provided S's realistic `DeltaUSD` is worse than 80,000's by no more than $50 (the same floor, applied in the paying-for-margin direction) and its `DeltaCbar` is no worse.

**Gate C tie-break, pre-settled.** A 200k-window model fires at 80,000 (`docs/handoff-nudge.md:13`), so any spacing above 120,000 pushes its re-arm past the window entirely and silently converts the nudge to one-shot for that whole model class — the structural sibling of the already-documented "hard block is unreachable on a 200k-window model" limitation (`:135`). **If restoring the 2x margin requires S > 120,000, do not adopt it**: keep 80,000 and record the margin erosion as a documented limitation instead. A silent behavior change across a model class, inherited by every stow consumer, is strictly larger than the risk it would fix.

**Expected outcome for Gates B/C, named before the runs:** no change. The regime shift altered session depth, not the ramp curve's shape. The one way this expectation could now be wrong in a new direction: the corrected multi-root lag median may differ materially from the single-root figure the incumbent was grounded on, which is precisely what Gate C tests.

### The conversion instrument — what it measures and why it is built here

`handoff-record-conversion.sh:22` appends `handoff session=<id>` to the *same* `.handoff-nudge.log` the hook writes its `nudged` lines to, and the handoff skill invokes it immediately after the handoff file is written and verified (`claude-skills/skills/handoff/SKILL.md:179-189`). That is a direct signal that a handoff actually happened, not an inferred proxy. `_parse_nudge_log_entries` has parsed it into `{"kind": "handoff", "session": <id>}` since the function existed (`transcript-analysis.py:10070`, `:10356-10359`) — and **no subcommand reads a `kind == "handoff"` entry anywhere.** Parsed, available, never used.

The log carries no timestamps, but it is append-only and one session's own lines land in real chronological order, so **ordering is recoverable even though elapsed time is not.** That is enough for every question here.

**Where it goes: a new section inside `_rearm_backtest_report`, not a new subcommand.** Two lighter alternatives were weighed. An `awk` one-liner over the logs is lighter in machinery but cannot scope to `--this-repo`/`--since`, so its denominator would be every session across every project — a different population from the one Gates A/B/C decide on — and it would print raw session ids to a terminal. A standalone `nudge-conversion` subcommand would duplicate `_add_project_scope_args`, root resolution, `_resolve_project_scope`, and the corpus scan that yields the in-scope session-id set, all of which `_rearm_backtest_report` already performs at `:10576-10627`, and would let the two reports drift onto different corpora — fatal for a pre-registered gate whose point is that one command, one corpus, and one date produce every reading. It would also need its own copy of the root-aware join. The accepted cost is that `rearm-backtest`'s output now carries two reports; it is contained by a distinct section heading and a table sharing no column header with the spacing table.

**The classification, frozen before any run.** For each session id appearing on at least one `nudged` line **and** having a surviving in-scope transcript (the same `session_traces` population the lag join uses), read that session's own log lines in file order within its own root's log:

| Bucket | Rule |
|---|---|
| **Voluntary** | A `handoff` line exists, and no `action=block` line precedes it |
| **Forced** | A `handoff` line exists, and at least one `action=block` line precedes it |
| **Blocked, no handoff** | At least one `action=block` line, no `handoff` line |
| **No compliance observed** | At least one `nudged` line, no `action=block`, no `handoff` line |

Four buckets, not three. Splitting "blocked, no handoff" out of forced compliance matters: a session that reached the hard block and never handed off is not compliance of any kind, and folding it into "forced" would read as evidence the block works when it is evidence of the opposite. The `action=block` precedence test mirrors `_operator_response_lag_from_log`'s existing exclusion (`:10397`) — a forced overshoot is not voluntary behavior.

**"No compliance observed" is not failure.** The advisory copy explicitly invites it: "If the task is nearly complete, ignore this and finish" (`nudge-handoff-near-context-cap.sh:660`). Gate E treats it as neutral.

**Re-arms tolerated at compliance.** For each **voluntary** session, the `ignored=` value on the last `nudged` line preceding its `handoff` line, reported as a distribution. `ignored=` is absent on lines written by older hook versions (`transcript-analysis.py:10311`), so those sessions are reported in a separate **"no `ignored=` field"** count and never defaulted to 0 — defaulting would bias the distribution toward "complied immediately."

**Output is counts plus derived rates, never session ids.** Printing the raw integers — voluntary, forced, blocked-no-handoff, no-compliance-observed, the fired-session denominator, the out-of-scope-dropped count, and the no-`ignored=`-field count — is what makes the two machines' rates poolable without merging corpora: a rate is a ratio of counts, so numerators and denominators sum across machines. Medians do not, which is why Gate E treats the `ignored=` median per machine.

### Gate D — conversion admissibility, per machine

1. **Fired sessions in scope ≥ 10** — the same order as Gate A's lag floor, and the same engineering judgment.
2. **Join validity: at least one `handoff` line whose session id matches an in-scope `nudged` session id.** The hook takes its session id from the hook-event JSON payload; `handoff-record-conversion.sh` resolves its own by PID walk (`_lib_resolve_claude_pid`). If those paths disagree systematically, every session reads as non-compliant — indistinguishable from a broken join. **Zero matches on both machines means the verdict is "Instrument unvalidated — join key unconfirmed," Lever 3 is inconclusive rather than declined, and the session-id-resolution mismatch is recorded as the follow-up.** The report prints this count for exactly this reason.
3. **Per-root log sizes, printed by the report itself and labeled `account-N`, never a raw path.** `_read_bounded_log_lines` tail-truncates at `_NUDGE_LOG_MAX_READ` (`:8734-8748`), so an oversized log silently drops its oldest lines. Truncation mostly degrades gracefully — a session losing its `nudged` lines simply leaves the denominator — but a session whose `action=block` line was truncated while its `handoff` line survived misclassifies as voluntary. The cap now applies **per root**, which is materially safer than one combined read. This size check is not a separate manual command: `_rearm_backtest_report` already resolves and iterates every root for the join fix above, so it prints each root's log byte size alongside the resolved-scope header, labeled by the same `_redaction_ordinals`-based `account-N` scheme every other per-account figure in this codebase uses (`docs/case-studies/hashline-edit-format.md:93-104`'s precedent) — never the resolved path. A manual `wc -c` loop over `declared_transcript_roots()` was considered and rejected: it would sit outside the report's own aggregate-only, `--no-redact`-refused code path, and its most natural implementation pastes each account's raw config-dir directory name — a per-account identifier that should not appear in a public commit — directly into a file this plan commits to the public repo. **Any root over the cap: the run still counts, the forced/voluntary split is reported as a lower bound on forced, and the size is disclosed by label.**
4. **That machine's Gate A conditions hold**, so the conversion population and the lag population are the same corpus.

### Gate E — lever 3 (nudge phrasing) go/no-go

Three metrics on admissible runs. Rates pool across machines by summing the printed counts, with each machine's own counts disclosed. The `ignored=` median is per machine.

- **Conversion rate** = (voluntary + forced) / fired sessions.
- **Block-reach rate** = sessions with any `action=block` line / fired sessions.
- **Median `ignored=` at voluntary compliance**, per admissible machine.

**Decline a phrasing change** if all three hold: pooled conversion ≥ **0.60**, pooled block-reach ≤ **0.20**, and median `ignored=` at compliance ≤ **2** on every admissible machine.

**Recommend a forward pilot of stronger copy — recommend, not build** — if any one fails.

The three thresholds are engineering judgment frozen in advance, the same standing as Gate A's floors, and are stated as such rather than dressed as a power calculation. Block-reach is the one with independent grounding: `handoff-hard-block-position.md` measured the hard-block rate collapsing after `BLOCK_AT` rose to 470,000, so a block-reach rate above 0.20 under that regime would mean sessions are absorbing the advisory copy all the way to 470,000 tokens — the strongest available evidence that the current wording does not move behavior. **Known ambiguity, recorded rather than hidden:** a blocked session with no `handoff` line may have ended outright or used the kill-switch instead of ignoring the block; `docs/handoff-nudge.md:34-38` lists three routes out of a block and only one writes a `handoff` line. Block-*reach* is unambiguous; the bucket split beneath it is not, and is reported as such.

### Lever 3 — nudge phrasing: explored here, not shipped here

Structurally the same shape as lever 2 — no retrospective A/B price exists — with one decisive difference: lever 3 now has a free premise check.

**No natural experiment exists.** The advisory `additionalContext` text and the block stderr text have both been rewritten several times: the threshold framing moved from percent-of-window to absolute tokens, and a cost-comparison clause was added and later simplified. Every variant stayed in the same register — advisory, addressed to the agent, asking it to *suggest* running `/handoff` to the user. No all-caps, imperative, or act-unilaterally variant has ever shipped, so there is no historical contrast to price.

**The engineer's example is a behavioral fork, not a tone change.** Today's copy is a *suggestion the agent relays to the human*: "If the current task is not close to done, suggest running /handoff to the user" (`nudge-handoff-near-context-cap.sh:660`). The proposed wording — "You HAVE to run handoff now… IMMEDIATELY AFTER before doing ANYTHING else, run handoff" — is a *command directed at the agent to act on its own*. Shipping it as literally written would have an agent end the session and write a handoff file without asking. That is a distinct UX and interruption cost, independent of the token cost this plan is scoped to, and it lands on every stow consumer. Two readings exist and they are not interchangeable:

- **(a) Advisory, addressed to the human, more forceful** — the copy stays a relay, the hedging softens, the agent still asks first.
- **(b) Directive, addressed to the agent, acts autonomously** — the agent runs `/handoff` unilaterally at the threshold.

**This plan does not pick between them.** Picking is the pilot's job, and the pilot is only recommended if Gate E says so. If Gate E recommends a pilot, the register row names this fork as the pilot's own unsettled design question and the engineer settles it then — the choice between (a) and (b) is a product decision about interrupting a human, not a measurement this corpus can make.

**Verdict path:** land the instrument → run both machines → Gate D admissibility → Gate E renders decline-for-now or recommend-a-forward-pilot → record the verdict, the three measured numbers, and the reasoning in the register. Same pattern as levers 1 and 2. No copy ships in this plan.

### Lever 2 — two-tier nudge: decline, and record why

Declined, not deferred. Three reasons, in order of weight:

1. **No retrospective price exists and none can be built cheaply.** Today's log carries one advisory severity, so nothing distinguishes which historical fires would have been tier 1 versus tier 2; `_simulate_rearm_spacing` has no second threshold to insert. Any measurement requires shipping first — structurally identical to the forward-looking pilot this register already declined at `docs/cost-levers-considered.md:338` ("the outcome variable's variance puts a sub-one-round effect out of reach at achievable n").
2. **The benefit is a human-behavior effect no instrument here observes.** The named risk is engineers tuning the nudge out as session-share rose 1.25x–4x (`docs/handoff-nudge.md:132`). Nothing in the transcript corpus measures "did the operator start ignoring it," so the lever has no falsifiable success criterion even after shipping.
3. **The heavy build isn't the only design, and that matters for the record.** A new threshold constant below the advisory point, new tier-aware marker state, a new `action=` value, and a revisit of `_operator_response_lag_from_log`'s literal `"block"` check is one reading. A lighter one already has its precondition in the hook: line 632's `[ -n "$LAST_FIRED_AT" ]` conjunct already distinguishes a session's first-ever crossing from a re-arm, and the advisory payload is a single hard-coded string at `nudge-handoff-near-context-cap.sh:657-661`. Softer copy on first contact, current copy on re-arms is a two-branch string selection over an already-computed condition — no new constant, no new state, no new log field, no consumer updates. **Recording this in the register is the point**: a future revisit should not default to the heavy design. The decline stands either way, because reason 2 is independent of build cost.

**Its premise check now ships as part of the conversion instrument, and that does not reopen the decline.** The `ignored=` distribution named as a follow-up in this plan's first draft is built here — not for lever 2, but because Gate E needs it. It measures lever 2's *premise*: if operators rarely let re-arms pass, the dismissal-as-noise risk lever 2 addresses is small. The promotion from "follow-up" to "built" is a change of reason, not of verdict — reason 2 is untouched by any premise number, so the distribution can strengthen the decline or move lever 2 to "reconsider," never dissolve it.

**Pre-registered reopening condition, frozen now:** lever 2 is reopened for a fresh scoping only if Gate E fails **and** a forward pilot of stronger copy is actually run **and** that pilot fails to move the conversion rate. Copy is the lighter primitive; a structural two-tier change is the heavier one, and the heavier one is not scoped until the lighter one has been tried and failed.

### Assumption ledger

**Root:** the handoff nudge's re-arm spacing was tuned against a corpus whose sessions were truncated near 230,000 tokens, and the deep tail under `BLOCK_AT=470,000` may have moved the operator-response lag that spacing was grounded on — while two further named levers (a two-tier nudge, and the nudge's own phrasing) have never been priced, the adherence rate that would tell them apart has never been read despite being logged and parsed, and the instrument that would answer all of this joins its log against one account while scanning six.

**Givens** (fixed beyond this plan's reach):

- **G1.** `HANDOFF_NUDGE_ABS_CAP` (150,000) and `HANDOFF_NUDGE_BLOCK_AT` (470,000) are fixed inputs. Dissolving either needs a decision outside this plan: both were closed by prior studies whose revisit triggers explicitly stay in place (`docs/handoff-nudge.md:32`; Context's settled list).
- **G2.** Transcript retention on this machine is not under this plan's control. The oldest surviving transcript measured 2026-08-25 as of the 2026-09-06 study, and its cause was left unresolved as an open operational question (`docs/case-studies/handoff-hard-block-position.md:35`, `:151`). This bounds every retrospective window available here.
- **G3.** Model context-window sizes are vendor-owned, so the 200k arm's 80,000 fire point is not a value this plan can move (`docs/handoff-nudge.md:13`).
- **G4.** The second corpus exists only on the engineer's macOS machine, and only a session on that machine can run anything against it — this plan holds no path to that filesystem. Its reply is asynchronous, so the replication rule fixes the handling for both outcomes in advance rather than waiting.
- **G5.** The declared-roots set is machine-owned state managed by an external setup script, not by this repo. Which accounts exist, and which of them have ever fired a nudge, is an input this plan reads and discloses, never something it changes for a measurement.

**Mechanisms:**

- `rearm-backtest --this-repo --since <N>d` as the sole instrument — `anchors: root`. The lightest primitive that answers the question: it already exists and reports both decision axes. Two heavier alternatives rejected: extending the simulator with a modeled hard stop (unnecessary — the regime enters through the population, not the model) and building a fresh measurement script (duplicates a shipped, tested subcommand).
- A root-aware log join replacing the single hardcoded read — `anchors: row 4b`. Two lighter alternatives rejected. Narrowing the corpus to one root instead would leave the simulation's own population smaller right where Gate A needs ≥30 sessions, and no flag delivers it anyway: the subcommand's `--config-dir` is additive and deduped (`scope.py:526-548`), and the top-level one is never read by `_resolve_cost_roots`. Emptying `~/.claude/transcript-config-dirs` for the duration would mutate shared machine state that other tooling reads, to obtain a worse corpus. The join fix is the smaller change and the better population.
- A new report section inside `_rearm_backtest_report`, not a new subcommand — `anchors: row 16`. Two lighter alternatives rejected: an `awk` one-liner (cannot scope, wrong denominator, prints session ids) and a standalone subcommand (re-implements scope resolution, the corpus scan at `:10576-10627`, and the root-aware join, and lets the two readings drift onto different corpora).
- Ordering-only classification from an untimestamped log — `anchors: row 17`. The lighter primitive by construction: ordering answers every Gate D/E question. Adding a timestamp field is a shipped hook change producing data only going forward, useless for a retrospective read.
- Register entry as the only always-touched narrative artifact — `anchors: root`. Lighter than a `docs/case-studies/` file, matching the closest-sized precedent (`docs/cost-levers-considered.md:472-491`).

| # | Assumption | Status |
|---|---|---|
| 1 | `HANDOFF_NUDGE_BLOCK_AT` appears nowhere in `transcript-analysis.py`; the simulation models no hard stop, so the spacing simulation needs no change for the new regime. | `[verified: exploration part A, grep with zero hits; the `action=block` exclusion at :10397 is a log-join filter, not a modeled cutoff]` |
| 2 | The after era begins 2026-08-31; 2026-08-30 straddles both regimes and must not be included. | `[verified: docs/case-studies/handoff-hard-block-position.md:5, :136]` |
| 3 | `--since Nd` filters on each session's **first** timestamp with a rolling epoch cutoff, so a session begun 2026-08-30 and continued into 08-31 is excluded. | `[verified: transcript_analysis/scope.py:660-677; transcript-analysis.py:11818-11819]` |
| 4 | `--this-repo` resolves scope from **live** `git worktree list --porcelain`, matched as exact project-dir slugs, failing closed. Sessions from already-removed worktrees are silently absent, so the original 127-session population is not reproducible. | `[verified: transcript_analysis/scope.py:47-99]` |
| 4a | **`--this-repo` is multi-root by default.** A populated `~/.claude/transcript-config-dirs` makes `rearm-backtest` scan every declared root with no `--config-dir` given; this machine declares 6. Omitting the flag is not single-root — it is the opposite. | `[verified: transcript_analysis/scope.py:363-367, :526-535; the discarded macOS run's own header, "6 roots"]` |
| 4b | **The report joins its nudge log at one hardcoded path while scanning every root**, so the median lag is measured on one account and applied to six. This is the defect the plan fixes, and it biases the realistic-compliance arm and Gate C. | `[verified: transcript-analysis.py:10627 vs. the multi-root `session_traces` built at :10619]` |
| 4c | **No flag combination scopes `rearm-backtest` to one root today.** Its subcommand `--config-dir` is additive and deduped, so pinning the default root is a no-op; the top-level `--config-dir` reassigns `scope.PROJECTS_DIR`, which `_resolve_cost_roots` never reads. Both fail silently. Recorded so a future session does not retry the flag. | `[verified: scope.py:488-548, transcript-analysis.py:10539, main():12030-12049; `rearm-backtest` absent from `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR` at scope.py:480-485]` |
| 4d | The exclusion count in the lag header is **not** a measure of the multi-root defect — it counts log lines whose session falls outside `--this-repo`/`--since`, and multi-root can only reduce it. The defect is the asymmetry in 4b. | `[verified: transcript-analysis.py:10394-10409]` |
| 5 | How many sessions and joined log lines survive into the scoped window on either machine. | `[unverified]` — resolved by each run's own header; Gate A exists precisely because this is unknown at authoring time. |
| 6 | Worktree-retention practice on either machine is not encoded in the repo, so surviving-session count is an output of each run, not a premise. | `[unverified]` |
| 7 | The incumbent 80,000 clears 2.6x the then-measured 30,624-token median lag; 2x separation corresponds to a median-lag ceiling of exactly 40,000. | `[verified: docs/handoff-nudge.md:20; .claude/plans/rearm-hook-band-spacing.md:31,47-60; 80,000/30,624 = 2.61]` |
| 8 | An unresolved lag-figure discrepancy (30,624 vs 52,184) was left open for "whoever next touches that figure." The re-run recomputes the same-lineage figure via `_operator_response_lag_from_log`; if the discrepancy reflects instrument bias rather than method, Gate C inherits it. | `[verified: .claude/plans/handoff-nudge-cap-recalibration.md:375-378]` for its existence; `[unverified]` for its cause. |
| 9 | A pooled, pseudonymous multi-account aggregate is publishable in this public repo: no per-account time series appears anywhere in this plan, `account-N` labels never carry a raw config-dir path or account name, and `--no-redact` is refused under multi-root. This is established practice here, not a new allowance. | `[verified: docs/handoff-nudge.md:26; docs/case-studies/handoff-threshold-impact.md:128; docs/case-studies/hashline-edit-format.md:93-104; docs/case-studies/cold-cache-attribution.md:268; transcript-analysis.py:10561-10567, :11827-11830]` |
| 9a | `cost --summary`'s single-root narrowing does **not** generalize to this report's pooled outputs (the spacing table, the conversion section): it guards a *labeled* per-account figure from contamination inside a PR authored under one account, not a pooled unattributed aggregate. The report's per-root log-size disclosure (Gate D condition 3) is the one genuinely labeled-per-account figure this plan produces — its precedent is `hashline-edit-format.md`'s `account-N` breakdown, not the pooled spacing/conversion sections, and it is labeled accordingly rather than pooled. | `[verified: transcript_analysis/scope.py:513-524; docs/case-studies/hashline-edit-format.md:93-104 for the labeled-figure precedent]` |
| 9b | `account-N` ordinals are assigned by sorted resolved path, not scan order, so the label is stable across this plan's separate runs **on one machine with an unchanged declared-roots set**. Two caveats to disclose: adding or removing a declared root renumbers, and the mapping is per-machine — one machine's `account-3` is not the other's. | `[verified: transcript_analysis/scope.py:179-199]` |
| 10 | No two-tier fire can be identified retrospectively in today's log, and `_simulate_rearm_spacing` has no second threshold to insert. | `[verified: exploration part C]` |
| 11 | The hook already computes "is this the session's first-ever crossing" as a reusable condition, and the advisory payload is one hard-coded string. | `[verified: nudge-handoff-near-context-cap.sh:632, 638-640, 657-661]` |
| 12 | `ignored=` is logged on every `nudged` line and parsed into `entry["ignored"]`, and no subcommand aggregates it. | `[verified: exploration part C; transcript-analysis.py:10346-10348]` |
| 13 | A spacing above 120,000 makes the re-arm unreachable on a 200k-window model (80,000 + S > 200,000). | `[verified: docs/handoff-nudge.md:13; sibling limitation at :135]` |
| 14 | `select-tests.py` drops any target covered by another before invoking pytest, so the GH-882 directory-plus-contained-file under-collection no longer applies in this tree. | `[verified: claude/.claude/scripts/select-tests.py:616-655]` |
| 15 | `handoff-record-conversion.sh` appends `handoff session=<id>` to the same log the hook writes, invoked immediately after the handoff file is written and verified — a direct compliance signal, not an inferred proxy. | `[verified: claude/.claude/scripts/handoff-record-conversion.sh:22; claude-skills/skills/handoff/SKILL.md:179-189]` |
| 16 | `_parse_nudge_log_entries` already parses that line into `{"kind": "handoff", "session": <id>}`, and no subcommand anywhere reads that kind. | `[verified: transcript-analysis.py:10070, 10356-10359; corpus grep for readers outside the parser, zero hits]` |
| 17 | A `handoff` line carries no timestamp and no field beyond `session`, so only ordering within one session's own lines is recoverable — not elapsed time, not token depth at compliance. Ordering holds because `_parse_nudge_log_entries` appends in file-read order. | `[verified: handoff-record-conversion.sh:22; transcript-analysis.py:10316-10359]` |
| 18 | The `nudged`↔`handoff` join key is the session id, but the writers resolve it differently: the hook reads it from its hook-event JSON payload, `handoff-record-conversion.sh` resolves it by PID walk. | `[unverified]` that the two always agree — Gate D condition 2 exists to catch a systematic mismatch, which would otherwise present as universal non-compliance. |
| 19 | `ignored=` is absent on lines from older hook versions, so the re-arms-tolerated distribution must carry a separate "no `ignored=` field" count rather than defaulting those to 0. | `[verified: transcript-analysis.py:10311, 10346-10348]` |
| 20 | `handoff-record-conversion.sh` shipped 2026-08-22, before the oldest surviving transcript (2026-08-25 per G2), so the conversion denominator is not truncated by instrument age. | `[verified: git log for that script, first commit 2026-08-22; G2's floor at docs/case-studies/handoff-hard-block-position.md:35]` |
| 21 | `_read_bounded_log_lines` tail-truncates at `_NUDGE_LOG_MAX_READ`, so an oversized log drops its oldest lines and a session whose `action=block` line was truncated but whose `handoff` line survived misclassifies as voluntary. Under the root-aware join the cap applies per root, which is materially safer than one combined read. | `[verified: transcript-analysis.py:8734-8748]` for the mechanism; `[unverified]` whether any root's log exceeds it — Gate D condition 3 records the sizes. |
| 21a | **A missing per-root log needs no guard**: `_read_bounded_log_lines` returns `[]` for an absent or unreadable file. | `[verified: transcript-analysis.py:8740-8741, 8747-8748]` |
| 21b | **4 of the 6 declared roots on this machine carry a `.handoff-nudge.log`** — the default root plus three others. The root-aware join therefore widens the log corpus roughly fourfold, not sixfold, and a root with no log is indistinguishable from a root where the nudge never fired. Sessions from log-less roots still feed the spend simulation; they simply never enter the fired-session denominator, which is correct. | `[verified: filesystem check of every path in ~/.claude/transcript-config-dirs]` |
| 22 | `docs/handoff-nudge.md`'s "Log location" section states "Two line types appear" and documents only `nudged` and `schema-drift`; the `handoff` line is undocumented, and the lead sentence attributes every line to the hook, which is false for that third type. | `[verified: docs/handoff-nudge.md:95-104]` |
| 23 | No test parses that table structurally — `test_doc_counts.py`'s only `docs/handoff-nudge.md` cases pin numeric literals elsewhere — so adding a third row and correcting the lead sentence breaks nothing. | `[verified: claude/.claude/hooks/tests/test_doc_counts.py:445-489; grep of the hooks test tree for a Log-location parse, zero hits]` |
| 24 | `rearm-backtest`'s end-to-end tests read the spacing table via `_table_cols(out, header_contains="Spacing", …)` plus substring assertions, so an appended section with its own heading and no second `Spacing` column header leaves them passing. | `[verified: claude/.claude/scripts/tests/test_transcript_analysis.py:18428-18540]` |
| 25 | `config_dir()` resolves from `$CLAUDE_CONFIG_DIR` or `$HOME`, never from the script's own location, so a branch checkout run on either machine reads that machine's own live roots and logs. | `[verified: claude/.claude/scripts/_config_dir.py:22-30]` |
| 26 | Each machine is a stow consumer, so `claude/.claude/**` changes reach it only on `git pull` — the second machine's run needs a checkout of this branch, which row 25 makes sufficient. | `[verified: CLAUDE.md, "Changes under `claude/.claude/**` go live on `git pull`"]` |
| 27 | Whether the second machine's corrected run returns before this plan concludes. | `[unverified]` — the replication rule's case 3 fixes the handling in advance so nothing blocks. |
| 28 | Every historical variant of the advisory and block message text stayed advisory and addressed to the agent as a relay request; the threshold framing moved from percent-of-window to absolute tokens and a cost-comparison clause was added then simplified, but no imperative or all-caps variant ever shipped — so no retrospective A/B for a phrasing change exists. | `[verified: git log -p across ~10 revisions of the hook's advisory and block message text]` |
| 29 | Today's copy asks the agent to relay a suggestion to the human; the engineer's example directs the agent to act on its own. Different behaviors, not two tones of one behavior. | `[verified: nudge-handoff-near-context-cap.sh:660]` for the current copy; `[engineer-verified]` for the proposed phrasing and its intent. |
| 30 | "No compliance observed" is a legitimate outcome the nudge's own copy invites, so Gate E treats it as neutral rather than as failure. | `[verified: nudge-handoff-near-context-cap.sh:660, "If the task is nearly complete, ignore this and finish."]` |
| 31 | A session reaching `action=block` with no later `handoff` line is ambiguous — it may have ended outright or used the kill-switch. Block-*reach* is unambiguous; the bucket split beneath it is not. | `[verified: docs/handoff-nudge.md:34-38, three routes out of a block, one of which writes a `handoff` line]` |
| 32 | Gate D's and Gate E's numeric thresholds (10 fired sessions, conversion 0.60, block-reach 0.20, median `ignored=` 2) are engineering judgment frozen before the runs, the same standing as Gate A's floors. Block-reach alone has independent grounding via the post-#769 hard-block-rate collapse. | `[verified: docs/case-studies/handoff-hard-block-position.md]` for the collapse; the three thresholds are `[assumed]`, deliberately and on the record. |

## Critical files

**Always touched** (every branch — the join fix and the conversion instrument ship regardless of any gate outcome):

- `claude/.claude/scripts/transcript-analysis.py` — three changes in `_rearm_backtest_report` and its helpers:
  1. **Root-aware log join.** Replace the single `_parse_nudge_log_entries(config_dir() / ".handoff-nudge.log")` read at `:10627` with one read per resolved root, joining within-root. The roots list is already in hand — `cmd_rearm_backtest` resolves it at `:10539` and threads it in as `scan_roots` (`:10558`). Each root's log sits at `<root>.parent / ".handoff-nudge.log"`, since `scan_roots` entries are `<config-dir>/projects`. **Reuse, do not guard:** `_read_bounded_log_lines` already returns `[]` for an absent file (`:8740-8741`), which 2 of this machine's 6 roots need (row 21b). Print each root's log byte size alongside the resolved-scope header, labeled `account-N` via `_redaction_ordinals` (`transcript_analysis/scope.py:179-199`) — reuse, not new machinery: the ordinal function and the `account-N/<kind>-N` label format already exist in `transcript_analysis/redaction.py`. This is Gate D condition 3's own data; folding it into this same read means it inherits the report's aggregate-only, `--no-redact`-refused guarantee instead of living as an unscripted side-channel that could paste a raw account directory name into the plan file.
  2. **Conversion helper.** Add `_nudge_conversion_from_log(session_traces, log_entries_by_root)`, a pure function. **Reuse:** mirror `_operator_response_lag_from_log`'s two-value return shape — results plus an explicit dropped count (`:10382-10390`) — rather than inventing a new one, and mirror its `action == "block"` literal check (`:10397`) for the precedence test.
  3. **Report section.** Print it after the spacing table under `## Nudge→handoff conversion`. Its table must not use `Spacing` as a column header (row 24). Print raw counts alongside every rate — that is what makes the two machines' figures poolable (row 9).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — a new `TestNudgeConversionFromLog` class for the unit-level cases (1-10) plus extensions to `TestRearmBacktestReport` for the report-level cases (11-15), per Verification's layering. The fifteen cases are named there; they are the pre-registered classification made executable, so they are not optional.
- `docs/transcript-analysis.md` — under the existing `## rearm-backtest` heading (`:1064`): document the conversion section (four buckets, the "no compliance observed is legitimate" caveat, the block-reach-vs-bucket-split ambiguity, the missing-`ignored=` count, the per-root bounded-read caveat) as a sibling of the existing `perfect`/`realistic` bullets at `:1070-1071`, and correct the `realistic` bullet itself, which currently describes the lag as coming from "`<config-dir>/.handoff-nudge.log`'s own `nudged` lines" — true before this change, wrong after it.
- `docs/handoff-nudge.md` — the "Log location" section at `:95-104`. Two edits, both required for the new report to be documented against a true description: change "Two line types appear" to three and add the `handoff session=<id>` row, and fix the lead sentence, which attributes every line to the hook (row 22). Scoped work, not incidental — the report reads a line type the canonical log-format doc denies exists. No test parses this table (row 23).
- `.claude/plans/handoff-nudge-deep-tail-lever.md` — paste both runs' verbatim output (resolved-scope header including root count, sessions in scope, lag sample line, spacing table, conversion section) below this Approach, in the shape `.claude/plans/rearm-hook-band-spacing.md:28-42` used. Tag each `[verified: rearm-backtest --this-repo --since <N>d, <machine>, run <date>]`. Paste each machine's per-root log sizes directly from the report's own printed `account-N`-labeled output (Gate D condition 3 — no separate command, no raw path), and one line disclosing the discarded pre-correction macOS run with no figures from it. **Disclose that `account-N` labels are per-machine and not comparable across the two pasted runs** (row 9b) — two tables side by side otherwise invite a false cross-machine account identification. This disclosure is a manual authoring step with no automated backstop, accepted as low-risk: the underlying `account-N` assignment is correct by construction (row 9b), and the worst case of a missing sentence is reader confusion, not a data leak.
- `docs/cost-levers-considered.md` — one new dated section appended after the `:493` entry, headed `## From \`handoff-nudge-deep-tail-lever.md\` — "..." (YYYY-MM-DD)`, using the register's `| Lever | Verdict | Measured reason |` table with four rows: the `REARM_SPACING` re-test (verdict per Gates A/B/C plus the replication rule, naming which case applied); the two-tier nudge (`Declined, not deferred`, carrying the lighter-design note and the pre-registered reopening condition); nudge phrasing (verdict per Gate E, naming the advisory-to-human vs. directive-to-agent fork if a pilot is recommended); and the nudge→handoff conversion report (`Built, not deferred` — the reversal of the earlier follow-up framing, on `:489`'s precedent that an instrument needed to reach a verdict is built). Keep verdict plus measured reason and point at the plan slug — the register indexes plans, it does not restate them (`:3-17`).

**Touched only if the replication rule adopts a new spacing:**

- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — the default at line 162 (`REARM_SPACING=80000`) inside the `resolve_rearm_spacing` malformed-value guard at 157-163. **Reuse, do not restructure**: the guard's `?????????*` arm caps the override at 8 digits and already accommodates any candidate here.
- `docs/handoff-nudge.md` — the "Why this spacing" paragraph (line 20: replace the backtest figures, the median-lag figure, and the separation multiple) and the Known-limitations re-arm bullet (line 131, `default 80000`). **Hazard: line 13's `| 200k | 80000 | 80000 |` cells are the per-model *threshold*, not the spacing — a find/replace on `80000` corrupts them.** If the adopted value exceeds 120,000, add a Known-limitations bullet on 200k-window re-arm unreachability, as a sibling of the existing `:135` bullet.
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` — the file that actually pins the default (72 occurrences of the constant/name).
- `claude/.claude/hooks/tests/test_doc_counts.py` — **re-run, expect no edit.** Its `docs/handoff-nudge.md` cases pin thresholds and percentages, not the spacing (`:445-489`). Confirm rather than assume.
- **Only if the adopted spacing exceeds 120,000:** `transcript-analysis.py:11835` (`--spacings` default `40000,80000,120000`) and its doc line `docs/transcript-analysis.md:1079`, so the instrument's candidate set still brackets the shipped default for the next re-run.

**Dispatch split: two `code-writer` dispatches, strictly sequential — never parallel.**

- **Dispatch 1 (unconditional): the root-aware join plus the conversion instrument.** Files: `transcript-analysis.py`, `test_transcript_analysis.py`, `docs/transcript-analysis.md`, `docs/handoff-nudge.md`'s Log-location section. Verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py` plus `.venv/bin/ruff check claude/.claude/ claude-skills/`. **This dispatch gates both runs** — no gate is evaluable before it lands, since every pre-fix number was measured on the wrong population.
- **Dispatch 2 (conditional): the spacing constant.** Files: `nudge-handoff-near-context-cap.sh`, `docs/handoff-nudge.md`'s "Why this spacing" and Known-limitations text, `test_nudge_handoff_near_context_cap.py`. Verification adds `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
- **Why sequential:** both dispatches edit `docs/handoff-nudge.md`, and parallel dispatches share the parent's feature worktree, where overlapping edits clobber silently rather than conflict. Dispatch 2 is also gated on run results dispatch 1 must precede. Sequence: dispatch 1 → both runs → Gates A–E → dispatch 2 if adopted.
- The register entry and the plan-file paste stay inline in the session — they carry the run outputs, which no subagent holds.

## Verification

Run from the worktree root, using its own `.venv` (README.md, Tests section):

```
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
```

`select-tests.py` derives scope from the diff itself; do not hand-widen. Expected selections, so a mismatch is visible rather than silent:

- **Instrument branch** (`transcript-analysis.py`, its test file, two `docs/` files, the plan file, the register): the scripts domain's own `claude/.claude/scripts/tests` via the domain table; `TICKET_REFERENCE_DISCIPLINE_TEST_PATH` via `_is_py_source_under_claude_or_plugins` (`select-tests.py:476`); `SELECT_TESTS_TEST_PATH` via `_is_test_source_change` (`:477`); and `claude/.claude/hooks/tests` plus `claude-skills/skills/tests` via `_is_under(p, DOCS_DIR)` (`:467`). The plan file selects nothing — no test reads `.claude/plans/` by path or subprocess (`:128-129`). Containment resolution collapses any glob into its enclosing directory (`:616-655`).
- **Spacing branch** additionally selects the hooks domain's own tests, `claude/.claude/scripts/tests` via `_is_hooks_dir_shell_script_change` (`:462`), and the `test_transcript_analysis*.py` glob via `_is_hooks_or_skills_change` (`:450`).

Spacing branch only, since it edits a shell script:

```
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

**Fifteen test cases pin the pre-registered classification, the join fix, and the derived-rate arithmetic Gates D/E read.** The classification is frozen before the runs, and a test suite is the only thing that keeps it frozen through implementation. **Cases 1-10 are pure unit tests against `_nudge_conversion_from_log` directly** (plain `session_traces`/`log_entries` dicts, no filesystem or report-rendering plumbing) — mirroring `TestOperatorResponseLagFromLog`'s existing unit-level convention for its sibling function (`test_transcript_analysis.py:~18280-18345`), avoiding an inverted-pyramid cost for logic a direct unit test covers in milliseconds. **Cases 11-15 require the full `_rearm_backtest_report` + `fake_projects` + `_table_cols` stack**, since redaction, root scanning, and the printed rate arithmetic don't exist inside the pure helper.

1. `nudged` then `handoff` for one session → **voluntary**.
2. `nudged`, then `nudged … action=block`, then `handoff` → **forced** (block precedes).
3. **Block-after-handoff ordering, the mirror of case 2.** `nudged` → `handoff` → a later `nudged … action=block` for the same session → still **voluntary**. The classification's "precedes" language is load-bearing; an implementation checking mere existence of a block line rather than its position passes case 2 identically but misclassifies this realistic continuation (hand off, keep working the same session, later hit the block) as forced.
4. `nudged … action=block` with no `handoff` → **blocked, no handoff**, asserted as its own bucket, not folded into forced.
5. `nudged` only → **no compliance observed**.
6. A `handoff` line whose session has no in-scope trace → counted in the dropped figure, not silently discarded. Mirrors `test_excluded_operator_lag_count_is_reported` (`test_transcript_analysis.py:18463`).
7. **The mirror orphan shape: a `nudged`-only session with no in-scope trace.** Must be excluded from every bucket and reflected only in the dropped count — not silently counted as "no compliance observed," which would inflate that bucket and understate the denominator's own trace-scoping.
8. A `nudged` line carrying no `ignored=` field, followed by `handoff` → counted in the "no `ignored=` field" bucket, never as 0.
9. **Last-`ignored=`-before-`handoff` selection.** ≥2 `nudged` lines with distinct `ignored=` values preceding one `handoff` → the classification reads the value from the line *immediately preceding* `handoff`, not the first, min, max, or a sum/average. This selection-rule bug would not surface by hand-checking the report, since Gate E's median-≤2 threshold is insensitive in direction to many plausible wrong selections at small sample sizes.
10. **Well-formed, non-overlapping session ids from the two writers** — `nudged session=hookid-A` and `handoff session=pidwalk-B`, both present and non-empty, never coincident — asserting join-validity is 0 and the report states it. This is the actual risk Gate D condition 2 exists to catch (the hook resolves its session id from the hook-event payload; `handoff-record-conversion.sh` resolves its own by PID walk), distinct from a malformed/missing-field input, which may still be covered by a separate assertion but does not itself exercise a systematic ID-scheme mismatch.
11. **Redaction:** no session id from any log line appears anywhere in the printed output.
12. **Root-aware join, the regression this plan exists to fix:** two roots each with their own log, a session in each. Both sessions' nudges must join. A test fixture with the second root's log ignored — today's behavior — must fail this case.
13. **A root with no log file** contributes zero entries and does not raise, exercising `_read_bounded_log_lines`' absent-file path against the real 4-of-6 shape on this machine (row 21b).
14. **Per-root log sizes print with `account-N` labels, never a raw config-dir path.** Exercises the Gate D condition 3 fix directly: the resolved-scope header's size line uses the same `_redaction_ordinals`-based labeling as every other per-account figure, and no root's filesystem path appears anywhere in the output.
15. **Gate D/E derived-rate arithmetic, plus the bucket-exhaustiveness invariant.** One fixture with ≥2 sessions per bucket (voluntary, forced, blocked-no-handoff, no-compliance-observed, one dropped, one no-`ignored=`-field) — asserting the printed fired-session count, conversion rate, and block-reach rate each equal the hand-computed ratio over the fixture's known bucket counts, **and** that the four bucket counts alone sum to the printed fired-session denominator, with the dropped count excluded from it. The four buckets plus the dropped count exhaust the log's own session population; the printed "fired" figure is deliberately narrower, since a dropped session was never classifiable into a bucket. Without this case, Gate D/E would be evaluated at run time against unregression-tested arithmetic: each of cases 1-10 exercises one bucket assignment in near-single-session isolation, and none constructs the mixed corpus where the rate arithmetic itself could diverge from the sum of individually-correct assignments.

Add one regression expectation when running the four existing `_table_cols(out, header_contains="Spacing", …)` assertions: the spacing table is unchanged in shape by the appended sections (row 24).

**The measurement's own verification** is that it is re-runnable, not a fixture: each recorded command plus its `--since` value and generated date reproduces its report, and a future re-test costs one command per machine rather than a new plan. Record, for each of the two runs: the machine, the command, `N`, the generated date, the resolved-scope header including root count, sessions in scope, the lag sample size and median, the conversion counts, and the per-root log sizes. Gates A and D are unevaluable after the fact without them.

## Out of scope

- **`HANDOFF_NUDGE_ABS_CAP` (150,000) and `HANDOFF_NUDGE_BLOCK_AT` (470,000).** Settled by prior studies whose revisit triggers explicitly stay in place; see Context and G1. None of the scope additions reopens either.
- **Shipping any change to the nudge's copy.** Lever 3 is explored, measured against Gate E, and given a verdict. No wording change ships here. If a pilot is recommended, its own plan settles the advisory-to-human vs. directive-to-agent fork with the engineer first.
- **Building the two-tier nudge, in either the heavy or the light design.** Declined with reasons and a pre-registered reopening condition recorded in the register; that record is this plan's deliverable on lever 2.
- **Extending `_simulate_rearm_spacing` to model a second threshold.** New simulation logic in service of a declined lever.
- **Adding timestamps, a machine identifier, or any other field to `.handoff-nudge.log`.** Both are shipped hook changes producing data only going forward — useless to the retrospective read this plan makes, and neither is needed: ordering answers every Gate D/E question (row 17), and two side-by-side reports answer the cross-machine question with no code.
- **Adding a single-root flag to `rearm-backtest`, or fixing its absence from `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`.** Row 4c records the flag's real behavior so a future session does not retry it, but the root-aware join makes single-root scope unnecessary here, and re-litigating the two-flag design across the 13 subcommands that share `_resolve_cost_roots` is a separate change with its own blast radius.
- **Changing `~/.claude/transcript-config-dirs`, on either machine.** G5 — shared machine state other tooling reads. A measurement never edits its own corpus definition.
- **Investigating why 2 of 6 declared roots have no `.handoff-nudge.log`.** Recorded and disclosed (row 21b); whether those accounts never fired a nudge or never ran the hook is an operational question this plan does not need answered, since either way their sessions correctly stay out of the fired-session denominator.
- **Widening scope to rescue a failed Gate A or Gate D.** No `--projects '*'`, no extra `--config-dir` roots beyond the declared set. Machine-wide scope defeats the identity-based minimization control `--this-repo` exists to enforce (`transcript_analysis/scope.py:47-68`), and replaces this repo's own session shape — the population every prior grounding of this constant used — with a different one. An inconclusive result is recorded as inconclusive, in the register's own verdict vocabulary.
- **Measuring conversion for sessions outside the resolved scope.** The conversion denominator is the same `session_traces` population the lag join uses, so both readings describe one corpus. Out-of-scope `nudged` sessions are counted and disclosed, never classified.
- **Reconciling the two session-id resolution paths** (row 18) if Gate D condition 2 shows a mismatch. Record it as the follow-up and mark lever 3 inconclusive; fixing hook-payload-vs-PID-walk agreement is a hook change with its own blast radius across every stow consumer.
- **Editing `claude-skills/skills/handoff/SKILL.md`.** Its `:183` line says the conversion append pairs with the hook's `nudged` lines "for a future nudge→handoff conversion report" — only the word "future" ages, and the sentence's operative content stays true. Editing any `SKILL.md` pulls a hook-enforced `/skill-review` round onto a plan whose deliverable is a measurement, which is not a trade worth making for one adjective.
- **Resolving the 30,624-vs-52,184 lag discrepancy.** The runs recompute the same-lineage figure through `_operator_response_lag_from_log`, so this plan's gates are internally consistent; establishing the other figure's provenance is a separate investigation, and row 8 carries the inherited risk rather than hiding it.
- **A new `docs/case-studies/` file.** The deliverable is four verdicts, one instrument fix, and two recorded runs — register-sized, matching the `:472-491` precedent that also built its own instrument. A case study is the format for a before/after cost comparison, which this plan does not run.
