# Orchestrator resume hardening and wait-without-polling guidance

## Context

Fix two gaps that let a review-pipeline dispatch churn indefinitely instead
of resuming or converging, surfaced by a peer session's incident transcript
(pr-review-skill-fe, agent-af6089427ae139f49.jsonl — the same incident
root-caused independently on `prevent-runaway-subagent-cost`, confirmed by
the engineer as one incident viewed two ways).

That transcript showed a parent session using 2,151 manual "sleep N, then
check again" Bash turns to wait on a dispatched agent instead of a
blocking/notifying primitive — cache reads grew to ~390K tokens by the end
— and, separately, a killed child reviewer's next message treating the
kill as grounds for a full restart rather than a resumable interruption.
Investigation this session found the first is a genuine, repo-wide
documentation gap (`subagent-delegation` never says how to wait on a
dispatch), and the second — while not actually exercised by
`review-orchestrator.md`'s own protocol in that transcript (it ran under an
ad hoc `general-purpose` agent instead) — exposed a real latent defect in
`review-orchestrator.md`'s own checkpoint format: two redispatch attempts of
the same interrupted step append byte-identical checkpoint lines, which the
checkpoint store's own dedup logic collapses into one entry, making retry
count unrepresentable and an unbounded-redispatch loop structurally
possible with no record of it happening.

Why now: PR #714 (branch `review-pipeline-orchestrator-subagent`) is the
release vehicle for `review-orchestrator.md` itself and is still open for
review — the engineer named this fix a hard prerequisite for that release.

Intended outcome: (1) `subagent-delegation` gains an explicit
wait-without-polling section any session's own dispatches can follow; (2)
`review-orchestrator.md`'s checkpoint format and resume protocol make retry
count representable and cap automatic redispatch of any one step, so an
interrupted step gets bounded retries before halting and surfacing to a
human instead of continuing indefinitely.

**Coordination note:** a peer plan, `prevent-runaway-subagent-cost.md`
(committed on branch `prevent-runaway-subagent-cost`, not yet implemented),
addresses the same incident from a different angle — capping how a
compound multi-step gate loop gets delegated as one opaque dispatch (a
dispatch-*composition* fix), plus an idempotency short-circuit and a
turn-count nudge. Its own scope explicitly disclaims stall/liveness
detection as a known gap. Coordinated with that session directly: it will
build its dispatch-composition addition to `subagent-delegation/SKILL.md`
on top of this plan's wait-mechanics addition to the same file, once this
lands, rather than both sessions editing it independently.

## Approach

Two independent mechanisms, on two disjoint file sets, landing on PR #714.

### Assumption ledger

**Root problem:** a session waiting on a dispatched agent has no documented
non-polling wait pattern to follow, and `review-orchestrator.md`'s own
checkpoint format cannot distinguish a step's first `started` entry from
its third, so nothing bounds how many times an interrupted step gets
automatically redispatched.

**Givens:**

| # | Given | Reason |
|---|---|---|
| G1 | The harness delivers a `<task-notification>` automatically when a same-session dispatch completes, including after the dispatching turn has already ended. | `[verified: claude/.claude/skills/handoff/SKILL.md]` — handoff's own "Before writing: collect in-flight background dispatches" section states and a repo test (`test_collect_step_forbids_polling_and_taskoutput`, `claude/.claude/skills/tests/test_skills.py`) pins this as the mechanism a session already relies on in one narrow case. |
| G2 | `orchestrator-checkpoint.sh`'s append path dedupes an exactly-identical line file-wide before writing. | `[verified: claude/.claude/scripts/orchestrator-checkpoint.sh lines 160-173]` — the script builds `{step, status, marker_hash}` via `jq -nc` and passes it to `_lib_append_line_locked`, documented at the subcommand's own `--help` text ("No-ops (exit 0) if the identical line already exists"). Two `--step reviewer:X --status started` calls with the same (unset) `marker_hash` produce byte-identical lines. |
| G3 | This repo has no existing numeric convention for a workflow-step retry cap — the only `_RETRIES` constants in the codebase (`_CHECKPOINT_LOCK_RETRIES`, `_LEDGER_LOCK_RETRIES`, both 5) govern file-lock contention, a different domain (a cheap, fast-retrying local lock vs. an expensive multi-turn reviewer dispatch). | `[verified: grep for _RETRIES/MAX_RETR across claude/.claude/scripts and hooks, this session]` |

**Mechanisms:**

- **M1 — Wait-without-polling guidance in `subagent-delegation`.** `anchors: root`. Add a new section documenting the non-polling wait pattern G1 already establishes as the harness's actual behavior, generalized from handoff's narrow instance to any dispatch: after dispatching, do not write a `sleep N`-then-recheck Bash loop; end the turn (or continue other work) and let the `<task-notification>` arrive, checking `ListAgents` once if inline confirmation is needed, never in a loop. Covers the same-session case directly; a cross-session peer wanting proactive notice uses `SendMessage`'s `notify_when_idle` instead.
  - Lighter primitives considered: (a) a hook that denies a `sleep`-shaped Bash call outright — rejected, `sleep` has legitimate uses outside dispatch-waiting (a documented backoff, a fixture's timing test) and a hook can't distinguish those from a polling loop by command text alone; (b) folding this into `handoff/SKILL.md`'s existing clause instead of a new section — rejected, that clause is scoped to handoff's own pre-write step and this repo's skill-authoring convention (`CLAUDE.md`: "duplicate it into both skill files intentionally — do not extract it into a `_shared/` directory") means a cross-skill reference there wouldn't generalize the guidance to every other dispatch site; a session waiting on a dispatch outside a handoff draft has no reason to open `handoff/SKILL.md`.
- **M2 — Retry-count-representable checkpoint format + bounded redispatch in `review-orchestrator.md`.** `anchors: root`. Add an optional `--attempt <n>` field to `orchestrator-checkpoint.sh append`, included in the JSON line so a second `started` entry for the same step with a different attempt number is no longer byte-identical to the first and survives the dedup (G2) instead of collapsing into it. `review-orchestrator.md`'s Resume protocol counts existing `started` entries for a step during its checkpoint read (something it already scans per-step) to compute the next attempt number and to decide whether the retry cap has been reached. On reaching the cap, the orchestrator does not redispatch again — it reports the step under "Anything needing a human's judgment" in its Return format instead.
  - **Retry cap: 3 total attempts (2 automatic retries).** `[verified: AWS Step Functions Retry field documentation, https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html — "MaxAttempts (Optional) — A positive integer that represents the maximum number of retry attempts (3 by default)."]` Of the conventions researched (Temporal's `RetryPolicy.MaximumAttempts` defaults to unlimited, bounded instead by non-retryable error types and workflow timeouts we are not adopting here; Kubernetes Job `spec.backoffLimit` defaults to 6, but governs cheap, fast-restarting pods, not an expensive multi-turn reviewer dispatch; Airflow's `default_task_retries` defaults to 0 with no documented production-value convention), AWS Step Functions' `Retry` field is the closest domain match: a bounded number of retries on one step of an orchestrated workflow before falling through to an explicit failure/human path, the same shape as a `review-orchestrator` step. 3 total attempts gives one step enough slack to survive a transient crash or machine-contention stall (the incident's own contributing factor) without permitting indefinite churn.
  - Lighter primitives considered: (a) a session-wide retry counter instead of per-step — rejected, a run with many steps would hit a shared cap from unrelated transient blips on different steps, which doesn't target the actual failure (one step looping); (b) re-using `marker_hash` to smuggle an attempt count instead of adding a new field — rejected, that field is documented and used elsewhere as literally a marker hash; overloading its meaning breaks that contract for any future reader relying on it being a hash.

## Critical files

- `claude/.claude/skills/subagent-delegation/SKILL.md` — new closing section, heading exactly `## Step 3 — Wait for a dispatch without polling` (after the existing "Everything else → `general-purpose`" subsection), documenting the wait-without-polling pattern (M1): end the turn and let the harness's automatic `<task-notification>` delivery arrive; check `ListAgents` once if inline confirmation is needed, never in a loop; for a cross-session peer, use `SendMessage`'s `notify_when_idle` instead. Keep it to ~10-12 lines — the file is at 187 of its 200-line soft target. Also add a new frontmatter `TRIGGER when:` clause ("about to wait on a dispatched subagent, or about to write a Bash `sleep`-then-recheck loop") and a matching `DO NOT TRIGGER when:` clause ("a `sleep` unrelated to waiting on a dispatch — a documented backoff, a fixture's timing test") — without the trigger clause, a session about to busy-poll has no description-level cue to load this skill at all.
- `claude/.claude/scripts/orchestrator-checkpoint.sh` — add optional `--attempt <n>` to the `append` subcommand: numeric-only validation (reuse the existing reject-over-cap-rather-than-truncate discipline the file already applies to `--step`/`--status`/`--marker-hash`), included in the `jq -nc` line construction, defaulting to `"1"` when omitted so existing callers/tests are unaffected. Update the subcommand's own `--help` usage text to document it.
- `claude/.claude/agents/review-orchestrator.md` — extend the "Resume protocol" section: when scanning checkpoint entries per step, count `started` entries for that step to determine the next `--attempt` value and check it against the cap. Extend "Checkpointing" to pass `--attempt <n>` on every `reviewer:<name>` `started` append. Extend "Return format"'s "Anything needing a human's judgment" bullet to explicitly enumerate a step that hit its retry cap (name the step, the attempt count, and that automatic redispatch stopped) as one of the cases it covers — the bullet already covers "a halt the skill's own instructions call for" but should name this concretely rather than leave a reader to infer it applies. Add a short **inline** instruction (2-3 lines, not a bare pointer) near "## Running the skill" — do not poll with a `sleep`-then-recheck loop while waiting on a nested `Agent`/`Skill` dispatch; let the `<task-notification>` arrive or check `ListAgents` once — rather than only referencing `subagent-delegation`'s new section by name: `review-orchestrator` has no mechanism to auto-load another skill's body at dispatch time (it invokes only `code-review`/`plan-review`/`ready-for-review` via `Skill`), so a bare cross-reference here could silently go unread.
- `claude/.claude/hooks/tests/test_orchestrator_checkpoint_script.py` — new tests: `--attempt` accepts a numeric value and rejects non-numeric/over-cap input, paired with an exact-boundary-allowed case (mirroring `TestOrchestratorCheckpointFieldCapsAllowAtExactBoundary`'s existing pattern for `--step`/`--status`/`--marker-hash`/run id, guarding against an accidental `-ge`/`-gt` flip on the new field); two `started` appends for the same step with different `--attempt` values both survive (not deduped); omitting `--attempt` defaults to `"1"` and existing dedup behavior for truly-identical lines (including identical `--attempt`) is unchanged.
- `claude/.claude/skills/tests/test_skills.py` (or a new focused test module under `claude/.claude/hooks/tests/`) — pin the new `subagent-delegation` section's presence and the new `review-orchestrator.md` retry-cap language, mirroring the existing `test_collect_step_forbids_polling_and_taskoutput` pattern.

**Reuse opportunities:** `_lib_append_line_locked`'s existing dedup mechanism needs no change — the new field naturally defeats dedup for genuine retries while preserving it for accidental duplicate appends (G2's protection is kept, not removed). `review-orchestrator.md`'s Resume protocol already scans checkpoint entries per-step before deciding done-vs-retry; counting `started` entries is an extension of a scan it already performs, not new scan logic.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` green after all changes.
- New `orchestrator-checkpoint.sh` unit tests (above) pass, including the differing-`--attempt`-values-both-survive case — a regression here would silently re-break the exact bug this plan closes (the identical-attempt-still-dedupes case guards a separate invariant, G2's accidental-duplicate suppression, not this one).
- `/agent-review` and `/skill-review` on the `review-orchestrator.md` and `subagent-delegation/SKILL.md` diffs respectively (both are required dispatches inside `/code-review` for these file types per `.claude/rules/review-pipeline-dispatch.md`).
- Manual dry run: append `--step reviewer:test --status started` three times with `--attempt 1`, `2`, `3` to a scratch `orchestrator_run_id`; confirm `orchestrator-checkpoint.sh read` returns all three distinct lines (not deduped to one).

## Out of scope

- `prevent-runaway-subagent-cost.md`'s M1 (dispatch composition), M2 (idempotency marker check), and M3 (turn-count nudge hook) — owned by that plan, sequenced to land on top of this one's `subagent-delegation` addition.
- True stall/liveness detection for a dispatched subagent (distinguishing "hung due to machine contention" from "still legitimately working") — `prevent-runaway-subagent-cost.md`'s own G4 already names this as a harness-level gap neither plan closes.
- Hardening `review-orchestrator.md` against being killed mid-dispatch by an external actor bypassing its own dispatcher (e.g., a grandparent session calling `TaskStop` directly on a nested reviewer dispatch) — the crash/redispatch protocol this plan hardens assumes the standard path (the orchestrator's own dispatcher redispatches it with the same `orchestrator_run_id`); a bypass scenario is unconfirmed by any incident and not designed against here.
- Script-layer unit coverage for the attempt-counting/cap-comparison arithmetic itself. That logic lives in `review-orchestrator.md` prose (LLM-reasoned over `orchestrator-checkpoint.sh read` output), consistent with how the file already makes its equivalent done-vs-redo decision; the only planned test is a source-scan pinning that the cap language is present in the file, not that the arithmetic is ever exercised. Deliberate: extracting it into a testable script subcommand is a heavier change than this plan's scope (retry-count representability + wait guidance), not a defect this plan is closing.
- A TOCTOU race between the Resume protocol's checkpoint read (count existing `started` entries) and its subsequent append (write the computed `--attempt`): `_lib_append_line_locked`'s lock serializes the append itself but not the read-decide-append sequence, so two concurrent redispatches of the same step could compute and append the same attempt number, colliding into dedup again. Bounded to concurrent redispatch of the same step under the same `orchestrator_run_id`, a narrower window than the single-threaded case this plan closes; not designed against here.
