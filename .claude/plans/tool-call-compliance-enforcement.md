# Tool-call batching and delegation compliance: measure, then nudge

## Context

**Goal:** give two standing tool-call-economy rules a feedback mechanism — a
retrospective measurement of how often sessions violate them, and an in-session
advisory that fires while a session can still change course.

Two rules govern how tool calls are issued. The harness instructs that
independent calls go in one message: "If you intend to call multiple tools and
there are no dependencies between the calls, make all of the independent calls
in the same block, otherwise you MUST wait for previous calls to finish first to
determine the dependent values." Separately, `subagent-delegation` sets an
operational trigger: "when you notice you are about to run the *second or third*
`Bash` command toward the same question, stop — dispatch the question instead of
continuing inline" (`claude/.claude/skills/subagent-delegation/SKILL.md:40-42`).

Neither rule has any checking mechanism. `/code-review` and `/plan-review` are
hook-gated at a tool-call boundary with a content-addressed marker; these two
depend entirely on the model noticing mid-session. There is no retrospective
measurement either — no subcommand counts how many tool calls a turn carried.

**Why now.** A peer session capturing a live `PostToolBatch` payload found it
exposes batch composition natively, making an in-session detector cheap for the
first time (A1 — reported, not yet confirmed here). And the
`transcript-analysis.py` decomposition landed (PR #681, `98a3615`), so Phase 1
can be written against a stable package structure.

**Intended outcome.** A re-derivable corpus-wide turn-shape measurement,
per-rule precision and recall figures, and a default-on advisory whose
thresholds trace to those figures against bars fixed *before* the measurement
runs.

## Approach

**Concluded design.** Three pieces. **Phase 0** lands the fix to
`dedup_turns_by_request_id`, because that bug manufactures the exact signal
Phase 1 measures (A3). **Phase 1** adds a `turn-shape` subcommand measuring, per
turn, how many tool calls it carried and how long runs of consecutive
single-call and Bash-only turns get, weighted by dollars; a calibration pass
then samples flagged runs and reports precision and recall per rule against
pre-registered floors (M8). **Phase 2** adds a `PostToolBatch` hook carrying two
detectors, injecting an advisory when either trips.

**The two detectors and how each attributes a rule** (M6):

- **Detector A — cross-batch.** Counts consecutive batches of size 1. It is
  **tool-aware**: it records each single-call batch's `tool_name`. A homogeneous
  `Bash` streak is attributed to the **delegation** rule (this is that rule's
  paradigm case — sequential Bash calls toward one question, split across
  turns); any other streak is attributed to the **batching** rule.
- **Detector B — within-batch.** Counts repeated `Bash` calls inside one
  resolved batch, attributed to the **delegation** rule. Needed because that
  rule's trigger fits entirely inside a well-formed batch: three greps issued
  together satisfy the batching rule while violating the delegation rule, and a
  size-1-streak counter is structurally blind to it.

Detector A must be tool-aware rather than tool-agnostic; otherwise a run of
single-`Bash` batches — the delegation rule's most common shape — is either
mislabelled a batching hit or, if an intervening multi-call batch breaks the
streak, missed entirely.

**Alternatives set aside.**

*A blocking gate.* Rejected on this repo's own precedent, three times over:
`design-decisions.md` §11 states a routing rule "cannot be hook-enforced: there
is no tool-call boundary for 'the parent is about to write code'... so unlike
the review gates (§1) it stays advisory"; §25 rejected a transcript-matching
hook for a near-identical gap because it "trains its own evasion" and is
"structurally blind" to omission; and `binding-context-cap`'s `Stop`-hook block
failed `/plan-review` twice as "satisfiable by any one-sentence reply." A gate
would also fire on a proxy rather than the rule (G2).

*A post-hoc reviewer agent.* §25's accepted answer for behavioral-rule
compliance, set aside on payoff profile: `skill-fidelity-reviewer` catches a
lapse while it is still correctable; a tool-call-economy lapse is already paid
for by the time a PR exists.

*A transcript-scanning nudge.* Strictly heavier for the same signal — checkpoint
state, stale-checkpoint detection, and a timeout backstop to deliver what the
payload already carries. Not retained as a fallback; see A1.

*Extending `cmd_audit_routing_shape`* (the lighter primitive, so it carries the
burden of justification). It already has streak bucketing, output-token
weighting, and the scope layer (`transcript-analysis.py:7506-7762`). Passed over
because its docstring pins it to Opus code-read turns outside judgment spans and
it carries a judgment-span state machine "intentionally duplicated from
cmd_audit_routing — tests cross-validate the two copies to guard against drift"
(`:7511`). Turn-shape compliance is a different population — all turns, all
models — so threading a second population through adds coupling to a structure
the codebase already flags as drift-prone. *A throwaway analysis script* (the
other lighter option) was rejected because the motivating audit's scratch
scripts did not survive their session, which is exactly why A2 is not
re-derivable.

### Assumption ledger

**Root problem.** Two standing rules that materially drive per-turn cost have no
feedback mechanism of any kind — nothing measures adherence retrospectively and
nothing raises salience in-session — so the violation rate is unmeasured and
each violation goes unnoticed at the moment it is made.

**Givens** (fixed, beyond this design's reach):

| # | Given | Why it is a given |
|---|---|---|
| G1 | The harness defines when `PostToolBatch` fires and what constitutes a batch | Vendor-imposed; the event contract is Anthropic's |
| G2 | No artifact records data-dependency between tool calls | Vendor-imposed; nothing encodes "call B needed call A's result", so any detector works on shape, never the rule |
| G3 | Retrospective measurement is bounded by the ~30-day transcript retention window | Vendor-imposed; `docs/cost-ledger.md` documents the same constraint |

**Mechanisms:**

| # | Mechanism | Justification | Anchor |
|---|---|---|---|
| M1 | Phase 2 reads batch composition from the `PostToolBatch` payload, not the transcript | Lighter primitives enumerated: (a) a `Stop` hook — fires only when the agent stops, never observing the autonomous stretch where serial calls accumulate; (b) a transcript scan with byte-offset checkpointing — strictly heavier, needing checkpoint state, stale-checkpoint detection and a timeout backstop for a signal the payload already carries | anchors: root |
| M2 | Advisory only; no exit-2 block, no escalation ladder | The block primitive exists, but both detectors fire on shape proxies (G2) and this repo rejected blocking on behavioral compliance three times (A4) | anchors: root, G2 |
| M3 | New `turn-shape` subcommand | Two lighter alternatives rejected — see "Alternatives set aside" | anchors: root |
| M4 | Phase 1 reuses `dedup_turns_by_request_id`, `_price_turn`, `iter_sessions`, `resolve_scan_roots` | Canonical implementations; the prior audit established that reimplementing them yields divergent numbers | anchors: root |
| M5 | Phase 1 excludes mutating-git runs from the delegation signal — see the command set below | Delegating consequential mutating steps is worse practice, not missed efficiency, so the signal must target read-only diagnostic chains | anchors: row A2 |
| M6 | Two detectors, Detector A tool-aware (see Approach) | A tool-agnostic single-call-streak counter mislabels or misses the delegation rule's paradigm cross-batch case | anchors: root |
| M7 | The hook's jq extraction reads only `.tool_calls \| length` and each entry's `tool_name`; no code path — including fail-open and error paths — writes `$INPUT`, `tool_input`, or `tool_response` to stdout, stderr, state, or a log; the header states this and the script sets no shell trace | Those fields carry raw command text and credentials, and `redact-credential-values.sh` is not a backstop — it extracts `.tool_response` only, on `PostToolUse` | anchors: row A10 |
| M8 | Calibration floors are **pre-registered here, before Verification 10 runs**: per-rule precision ≥ 0.70 to ship that rule's detector; inter-rater Cohen's κ ≥ 0.61 for the classification to count at all; recall reported but not gating | Setting the bar after seeing the split is researcher degrees of freedom, which would contradict this plan's own claim that thresholds trace to figures rather than judgment. κ ≥ 0.61 is Landis & Koch's "substantial agreement" boundary. Precision 0.70 is this plan's own engineering judgment, not a cited standard: an advisory wrong a third of the time still carries a majority-correct signal, while one near 0.50 is noise. Recall does not gate because low recall makes the nudge less useful, not harmful. **These values are the engineer's to change before calibration runs, not after.** **If κ < 0.61**, the classification is void: re-rate once with a sharpened rubric; if it fails again, Phase 2 returns to planning rather than shipping on an unreliable label set | anchors: root, row A9 |

**M5's excluded command set** — enumerated because no such classifier exists in
the codebase and the exclusion is otherwise untestable:
`commit`, `push`, `merge`, `rebase`, `cherry-pick`, `reset`, `revert`, `stash`,
`tag`, `checkout`, `switch`, `restore`, `add`, `rm`, `mv`, `branch`, `clean`,
`remote`, `fetch`, `reflog`, `symbolic-ref`, `fsck`, and `worktree`.

Each is excluded **wholesale**, with no read-only carve-out for `stash list`,
`git tag -l`, or `worktree list`. This matches the posture of
`deny-reviewer-tree-mutation.sh`, whose `REVIEWER_GIT_WRITE_CAPABLE` list is
subtracted wholesale and whose own comment records that it deliberately
over-denies the bare/list forms as an accepted false positive. The cost is
recall — a genuine `git tag -l` investigation chain goes unflagged — and that
direction is the safe one for a signal whose false positives reach every stow
user.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| A1 | `PostToolBatch` payloads carry a `tool_calls` array with per-entry `tool_name`, `tool_input`, `tool_use_id`, `tool_response` | **[unverified]** — a peer session's single live capture (main-thread, 2-call batch). Verification 11 must confirm. **On failure, Phase 2 returns to planning** — no fallback is claimed, because a transcript scanner would first have to redefine what a "batch" is, which is a design question |
| A2 | The motivating audit found ~48.3% of $80.85 across 441 turns in three sampled windows attributable to violating turns | **[unverified]** — not re-derivable: the producing scripts were session-scoped scratch files, and the figures predate the dedup fix. Motivating, not a baseline |
| A3 | `dedup_turns_by_request_id` merges only *contiguous* same-`requestId` runs, so a `tool_result` written mid-run splits one billed turn into two | **[verified: `transcript_analysis/pricing.py:196-212`]** — `continues_run` requires an unbroken run; the non-assistant `else` arm resets `run_key = None`. **Not neutral noise:** it manufactures the "consecutive single-call turn" shape Detector A's offline analogue flags, biasing toward over-flagging. Hence Phase 0 |
| A4 | This repo rejected hook-blocking for behavioral compliance three times | **[verified: `docs/design-decisions.md` §11, §25; `docs/cost-levers-considered.md` binding-context-cap row]** |
| A5 | Adding a subcommand is unchanged by the decomposition: `build_parser()` (`transcript-analysis.py:9299`), `main()` (`:10070`), and all `cmd_*` remain in that file | **[verified at `98a3615`]** |
| A6 | A `settings.json` hook event key holds an array of matcher groups, so multiple scripts coexist under one event | **[verified: `claude/.claude/settings.json` — `UserPromptSubmit` 1 group / 4 scripts; `PreToolUse` 5 groups / 33 scripts]** |
| A7 | `PostToolBatch` is unregistered today | **[verified: `claude/.claude/settings.json`]**. Two collision sites with the `nudge-posttooluse-migration` branch: the `settings.json` key, and a payload builder in `claude/.claude/tests/helpers.py`. Both mechanical. Distinct from Phase 0's dependency on `dedup-noncontiguous-request-id-runs` — two different branches, two different relationships |
| A8 | A recurring *time-series* delegation-adherence measurement was closed as unmeasurable; a *cross-sectional* one was later accepted | **[verified: `docs/cost-levers-considered.md` delegation-discipline-pilot row; `docs/design-decisions.md` §22]**. Phase 1 is cross-sectional and must not be framed as reviving the pilot |
| A9 | Both rules targeted; default-on with kill-switch; calibration before threshold-setting; Phase 2 independent of `nudge-posttooluse-migration` | **[engineer-verified]**. Both-rules scope is **provisional**: M8's floors decide per rule whether that detector ships live |
| A10a | `tool_input` for a Bash call is the command string and `--samples` emits raw command text and paths, so both carry credential- and private-project-shaped content | **[verified: the tool schema itself — a Bash `tool_input.command` *is* the command string]** |
| A10b | No existing hook backstops that content | **[verified: `redact-credential-values.sh` extracts `.tool_response` only, on `PostToolUse`; `deny-private-project-refs.sh` catches tracker IDs and six structural shapes, not arbitrary command text]** |
| A11 | Three independently-answerable unknowns, each gating Phase 2 on its own: (a) does `PostToolBatch` fire inside subagents; (b) does it carry a top-level `session_id`; (c) does it carry an `agent_type` equivalent | **[unverified]** — (b) failing means the per-session counter has no key and Phase 2 returns to planning; (a) or (c) failing means the subagent gate cannot be built and Phase 2 ships main-thread-only or returns to planning. Without a gate, an `Explore` agent's legitimate sequential search calls inflate the parent's counter. `nudge-error-mode-analysis.sh:91-94` needed exactly this gate |
| A12 | Phase 1's per-turn tool-call count and Phase 2's `tool_calls` length measure the same underlying event | **[unverified]** — if the harness's batch boundary does not map 1:1 onto "all `tool_use` blocks in one assistant API turn," Phase 1's calibrated thresholds do not transfer. Verification 11 |
| A13 | Detector B cannot distinguish N `Bash` calls toward one question from N *unrelated* `Bash` calls correctly batched together — `pytest`, `ruff`, and `shellcheck` issued in one batch is rule-1-compliant behavior that trips Detector B | **[verified: by construction — the detector reads `tool_name` only, per M7]**. Its false-positive rate is therefore highest exactly where Bash usage is richest, which is what makes M8's per-rule split load-bearing rather than a formality |

**Residual risk carried deliberately** (accepted, not oversights):

- **Evasion.** The detectors fire on shape, so padding a batch with a trivial
  call silences the nudge. §25's "trains its own evasion" critique targets proxy
  detection generally, not blocking specifically, so it applies here and is not
  dissolved by M2's advisory posture. The nudge's only lever is context-visible
  text, so evasion pressure is soft rather than scored — but soft is not absent,
  since an advisory that reappears in the model's own context is itself a
  stimulus to stop triggering. Accepted; the falsifying signal is a corpus that
  trends compliant without a matching drop in serial-call cost.
- **Single-population calibration.** Floors are evaluated against the declared
  transcript roots — in practice one machine's corpus — then shipped default-on
  to every stow consumer, whose tool-call mix may differ. No canary or staged
  rollout. Accepted per A9.
- **Nuisance rate.** Including the batching rule widens the flagged population to
  violations the audit called cheap and low-stakes. A9 plus M8 make this
  contingent on measured precision.
- **Mixed-streak under-attribution.** A streak broken by one non-`Bash` call —
  `Bash`, `Read`, `Bash` as three single-call batches — reads as mixed and is
  attributed wholly to the batching rule, even though it contains the delegation
  rule's literal trigger (that rule counts Bash calls toward one question and
  says nothing about an intervening `Read` resetting the count). The advisory
  will name the wrong rule in this case. This follows from G2 — shape cannot
  recover intent — and is accepted rather than fixed, but it depresses Detector
  A's delegation recall specifically and should be visible to raters during
  calibration.

## Critical files

**Phase 0 — prerequisite (gates Verification 9–10 only)**

- Land `dedup-noncontiguous-request-id-runs` (plan-only at `38283ba`; no
  implementation, local-only, no remote-tracking ref) before Phase 1's **corpus
  run and calibration**. Verification 1–8 use synthetic fixtures and are **not**
  gated — subcommand build and unit-test work proceeds in parallel.
- Its fix flips the existing pin at
  `claude/.claude/scripts/tests/test_transcript_analysis.py:7336-7346`
  (`test_user_record_between_same_request_id_records_prevents_merge`), which
  today asserts the buggy behavior as unqualified design. **Phase 1 must not add
  a second pin** — that would double the update surface with no way to discover
  both sites. That test's docstring gains the tracking-branch name so `grep`
  surfaces every coordinated site.
- **Stall contingency:** if the fix has not landed by the time the subcommand is
  otherwise unit-tested, this plan's author lands it directly rather than
  waiting. Calibration does not run against the buggy dedup — A3's bias is in
  the same direction as the signal being measured, so the resulting floors would
  be unsound rather than merely imprecise.

**Phase 1 — retrospective measurement**

- `claude/.claude/scripts/transcript-analysis.py` — add `cmd_turn_shape` plus its
  `build_parser()` block, following the registration shape at `:9358-9363`.
  **Reuse:** `dedup_turns_by_request_id` (aliased at `:65`), `_price_turn`,
  `iter_sessions`, `_resolve_scan_roots`, `_resolve_project_scope`,
  `_add_project_scope_args`, `_parse_since_nd_arg`, `render.py` table helpers,
  and `_DO_NOT_PUBLISH_BANNER`. Do not reimplement parsing, pricing, or scope
  resolution (M4). No existing consumer enumerates the subcommand list —
  `test_transcript_cli_bootstrap.py` smoke-tests `--help` plus one
  representative subcommand — so no consumer update is required.
- `--samples`: **stdout only**, stamped with `_DO_NOT_PUBLISH_BANNER`, never
  written into the repo working tree (A10a/A10b).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — new tests.
  **Reuse:** `conftest.py`'s `_asst`, `_bash_use`, `_tool_result`, `_user_msg`;
  `_priced` is module-level in this file at `:5068`.
- `docs/transcript-analysis.md` — new `##` section.

**Phase 2 — in-session advisory**

- `claude/.claude/hooks/nudge-serial-tool-calls.sh` — new.
  `# hook-class: informational` on line 2; header states the fail-open posture
  and M7's no-payload-echo rule.
  **Strict mode:** `set -uo pipefail` (never `-e`), following
  `nudge-transcript-toolkit.sh:56`, the sibling of the same class and comparable
  fire frequency. Note the family is not uniform: `nudge-error-mode-analysis.sh`
  omits strict mode entirely, and its comment at `:49-51` gives the reason —
  "strict mode could cause unexpected early exits from the `|| true` guards."
  That risk is real, so every fallible operation here carries a `|| true` guard.
  **Also follows:** the `AGENT_TYPE` subagent gate (`:91-94`), which exits before
  any state mutation; `# shellcheck disable=SC2016` on `_lib_capped_for`-wrapped
  `jq -n --arg`.
  **Reuse:** `_lib.sh` helpers and `_lib_capped_for`; the per-session state
  directory; the `hookSpecificOutput.additionalContext` shape
  (`nudge-handoff-near-context-cap.sh:442-447`); the
  `$CONFIG_DIR/.<name>-nudge-disabled` kill-switch (`:275,371`).
- **State shape:** two counters and **two independent fired-flags**, one per
  rule. Suppression is **per-rule, not global** — a batching-rule fire must not
  consume the session's only delegation-rule advisory, which would re-create the
  blindness M6 exists to remove.
- **Sweep:** the sibling's 30-day sweep runs `find … -mtime +30 -delete` on every
  qualifying call (`nudge-error-mode-analysis.sh:151`), written for a
  once-per-turn host. That is not viable per-fire here. This hook sweeps on
  `SessionStart` instead, where the cost is paid once per session. Nothing
  accumulates *within* a session — one session writes one state entry for its own
  `session_id`, however many times it fires.
- **Performance budget: <100ms per fire**, per `claude-hook-review` §7, which
  states the budget without a percentile; this plan operationalizes it as **p95**.
  If exceeded, the fallback is a single `jq` invocation with no *additional*
  subprocess and no per-fire `find`. (`jq` itself is a subprocess via
  `_lib_capped_for`; the sibling `nudge-transcript-toolkit.sh` makes seven such
  calls — `:65, 71, 101, 104, 111, 113, 129` — so one is well within precedent.)
- Advisory copy: names which rule tripped, and does not assert a violation as
  fact — G2, A13, and the calibration step all establish that flagged runs
  include legitimate sequential work.
- `claude/.claude/settings.json` — **two** registrations for this one script:
  a new `PostToolBatch` key (the detectors), and a `SessionStart` entry (the
  sweep). `command` uses the stable `~/.claude/hooks/...` prefix this repo's
  stowed settings already use. The `SessionStart` entry is registered
  **unrestricted, not `matcher: "startup"`**: of the four existing `SessionStart`
  registrations, two scope to `startup` only and one to `startup|clear|compact`,
  all of which skip the `resume` source — that would leave state unswept for
  exactly the long-lived, frequently-resumed sessions the sweep exists to bound.
  The fourth, `capture-session-id.sh`, is registered unrestricted and is the
  precedent followed here. The script branches on `hook_event_name`.
- `claude/.claude/tests/helpers.py` — add a `PostToolBatch` payload builder,
  **encoding the schema Verification 11 confirms** so the live capture survives
  its session (A2's failure mode).
- `claude/.claude/hooks/tests/test_nudge_serial_tool_calls.py` — model on
  `test_nudge_error_mode_analysis.py`.
- `docs/hooks.md` — new entry.

**Recording the outcome**

- `docs/cost-levers-considered.md` — a row carrying **only** aggregate figures,
  never `--samples` text (A10a/A10b); its relationship to the closed
  delegation-discipline pilot (A8); and a **revisit trigger** naming what would
  invalidate the numbers. Figures are grounded by citing the deterministic
  sampling command and its scope, **not** by pasting example content.

## Verification

**Phase 0.** The fix lands; the existing pin at
`test_transcript_analysis.py:7336-7346` is updated in the same commit; and a
**positive** assertion is present that a `tool_result`-interrupted same-`requestId`
run now merges. A differential check reports corpus turn-shape numbers before and
after, showing the expected reduction in flagged single-call streaks.

**Phase 1** (1–8 ungated; 9–10 gated on Phase 0)

1. A turn with N `tool_use` blocks counts as one turn with N calls. **The fixture
   must use N separate records sharing one `requestId`** — N blocks in a single
   `_asst` call bypasses `_merge_assistant_run` entirely and would pass even if
   the merge path were broken.
2. Hand-computed dollar assertion against `_price_turn`.
3. Zero-tool-call turns bucket correctly.
4. `isSidechain` turns are **excluded** from the measurement (matching
   `cmd_audit_routing_shape`'s thread-scoping), tested.
5. Streak behavior across a mid-session `gitBranch` change: **resets**, tested.
6. M5's excluded command set, tested against the enumerated list.
7. `--samples` output shape; banner present; stdout only.
8. Real-subprocess wiring in `test_transcript_cli_bootstrap.py`.
9. Corpus run: non-zero session count, repeatable to the same numbers.

**Calibration (gates which detectors ship; floors pre-registered in M8)**

10. Sample flagged runs; classify each as genuine violation or legitimate
    sequential work. Report **precision per rule** and **recall** from a
    manually-labeled holdout of *unflagged* runs. Compare against M8's floors.

    **Sampling.** The flagged sample is sized from the flagged population. The
    unflagged holdout is sized by a **fixed minimum-misses target: sample until
    30 true violations are observed among unflagged runs, or until 1,000
    unflagged runs have been labeled, whichever comes first** — reusing the
    flagged formula would risk observing too few misses to estimate recall at
    all, since violations are rare in that population. Pre-registering the
    *method* here, rather than leaving "its own sizing rule" to be invented at
    measurement time, is the same discipline M8 applies to the floors; the
    resulting N is computed later, the rule is not.

    **Rater note.** Detector B fires on any repeated `Bash` in one batch, so
    this repo's own verification stanza — `pytest`, `ruff check`, and
    `shellcheck` issued together — trips it while being textbook rule-1
    compliant (A13). Raters must recognize this pattern as a legitimate
    true-negative rather than labeling it a violation; it is expected to be a
    material share of Detector B's flagged population.

    **Process discipline.** Classification is ephemeral — raters view
    `--samples` output directly; no shared file carrying raw content is
    committed, handed off, or pasted into a PR body. This does not obstruct
    Cohen's κ: both raters reach the *same runs* by independently running the
    identical deterministic `--samples` command against the identical corpus,
    and what they exchange to compute agreement is a **label set** (run
    identifier plus judgment), which carries no command text, paths, or
    credentials and so is outside the raw-content ban.

**Phase 2**

11. **Gating smoke test.** Across a single-call batch, a multi-call batch, an
    error-containing batch, **and a subagent-originated batch**: confirm
    `tool_calls` is present and populated (A1); a top-level `session_id` exists
    (A11b); whether an `agent_type` equivalent exists (A11a/c); and that a
    multi-`tool_use`-block turn corresponds to exactly one firing with a
    matching-length array (A12). The confirmed schema is written into the
    `helpers.py` builder. Phase 2 does not proceed until these pass.
12. Hook tests: below threshold → silent exit 0; **each detector at its own
    threshold → one advisory naming its own rule**; **both detectors tripping on
    one batch** → defined, tested behavior; a mixed-tool batch and a
    below-threshold same-tool batch → no Detector B fire (false-positive
    control); Detector A attributing a homogeneous `Bash` streak to delegation
    and a mixed streak to batching; **an intervening multi-call batch resets
    Detector A's streak counter** — the miss mode the Approach section names;
    **per-rule suppression** — a batching fire does not suppress a later
    delegation fire; kill-switch; subagent gate; plan-mode gate;
    `CLAUDE_CONFIG_DIR` honored; unwritable state dir; traversal-safe
    `session_id`; **jq-absent and required-tool-absent → silent-allow** (exit 0,
    empty stdout).
13. Malformed-payload cases named separately: missing `tool_calls` key; empty
    `tool_calls` array; missing `session_id`.
14. A real round-trip across two invocations in one session, proving **both**
    counters and **both** fired-flags persist and increment independently.
15. **Two fixture variants carrying the same credential-shaped string in
    `tool_input`** — one well-formed and crossing a firing threshold (so the
    advisory-output assertion inspects real emitted text, not an empty string),
    one malformed (exercising the fail-open path). Each asserts the string
    appears in neither stdout, stderr, state files, nor any log (M7). Run
    against both detectors' code paths. A static check confirms the script never
    enables shell tracing.
16. The hook emits no blocking decision under any input (M2).
17. Per-fire latency against the <100ms p95 budget — a pass/fail gate. Measured
    over ≥100 iterations against a state directory pre-populated with **≥200
    session entries**, not an empty one.
18. **The `SessionStart` sweep**: fires on both a fresh start and a `resume`;
    evicts state older than 30 days; preserves recent and currently-live state;
    the script branches correctly on `hook_event_name` so a `SessionStart`
    invocation runs no detector logic and a `PostToolBatch` invocation runs no
    sweep; the swept path is traversal-safe on a hostile `session_id`.

**Whole change:** `../../../.venv/bin/pytest claude/.claude/`,
`../../../.venv/bin/ruff check claude/.claude/`, and
`scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.

## Out of scope

- **The escalation ladder and the `PostToolBatch` migration of the handoff
  nudge** — `nudge-posttooluse-migration` owns both. This plan shares no
  interface with it; collision sites are named in A7. Not a given: nothing here
  depends on it landing.
- **`cost --top` multi-root `AssertionError`** — separately briefed, unrelated.
- **Re-deriving the audit's original figures (A2).** Phase 1 produces a fresh
  number; retro-fitting is not worth the retention race (G3).
- **Changing the wording of either rule.** Unprimed subagents applied both
  consistently, so comprehension is not the failing step, and wording changes
  would confound the calibration.
- **Adding a column to `docs/cost-ledger.md`.** Per A8 this is cross-sectional;
  a recurring column would revive the closed time-series pilot.
- **A field feedback channel for miscalibration.** The revert path is a
  follow-up commit adjusting a threshold or the default, consumed on the next
  `git pull`; there is no push mechanism and no aggregation of consumers' logs.
