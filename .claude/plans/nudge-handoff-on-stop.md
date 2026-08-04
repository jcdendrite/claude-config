# Fire the handoff nudge on Stop, not just UserPromptSubmit

## Context

`nudge-handoff-near-context-cap.sh` warns Claude to suggest `/handoff` once a
session's carried context crosses 60% of the resolved model's context
window, but it only checks on `UserPromptSubmit` — so a session that crosses
the threshold on its own final assistant turn, with no further user prompt,
gets no warning at all. An ad-hoc query over this machine's 307 main-session
transcripts (`~/.claude/projects/*/*.jsonl`, script left at
`/private/tmp/claude-501/-Users-jared-MyCode-claude-config/138b05bb-6f1f-4354-aef9-a70fe273da92/scratchpad/nudge_trigger_gap.py`,
reusing the hook's own model-window table from commit `d9bd16e`) found 18 of
299 usage-bearing sessions crossed the threshold at some point; of those, 2
(11.1%) crossed on their final turn with no subsequent prompt — sessions the
current design structurally cannot reach. The other 16 (88.9%) had a later
prompt and so had a chance to fire, possibly delayed by one turn. This is a
small sample (n=18 crossers); the plan treats it as directional evidence of
a real gap, not a precise rate. The intended outcome is closing that
specific gap: the same nudge, checked at one more point in the turn cycle,
with no change to its threshold, marker, or message logic.

## Approach

**Register the same script on `Stop` in addition to `UserPromptSubmit`,
rather than writing a new sibling hook file.**

Two ways to close the gap were on the table:

1. **Extend the existing script to also register on `Stop`.**
2. **Write a new `nudge-handoff-on-stop.sh` sibling file**, matching the
   repo's general one-file-per-concern nudge pattern (`nudge-worktree-anchor.sh`,
   `nudge-error-mode-analysis.sh`, `nudge-handoff-near-context-cap.sh` are
   three separate files despite similar shape).

Chose (1). The repo's own precedent cuts the other way for *this specific*
case: `git grep`-counting hook commands against `hooks` keys in
`claude/.claude/settings.json` shows 1 of 39 distinct hook commands
(`capture-session-id.sh`) is already registered under two events
(`SessionStart` and `SubagentStart`) because its logic is event-agnostic —
the same action, done identically regardless of which event triggered it.
The three-separate-files precedent applies when the nudges are *different
concerns* that happen to share a shape (worktree anchoring vs. error-mode
suggestion vs. context-cap warning) — not when it's the *same* concern
checked at a second point in the lifecycle. Verified against the official
hooks reference (code.claude.com/docs/en/hooks, fetched this session) that
`Stop`'s input carries the same `session_id`, `transcript_path`,
`agent_type` (subagent-only), and `permission_mode` fields `UserPromptSubmit`
already reads, and that `Stop` supports `hookSpecificOutput.additionalContext`
identically — so the existing field-extraction, subagent gate, plan-mode
gate, threshold math, and one-shot marker all carry over unchanged. A new
file would duplicate all of that logic for a one-field difference (which
event name to stamp on the output).

The one-shot marker is already keyed by `SESSION_ID` alone, not
`SESSION_ID`+event — so a session nudged via `UserPromptSubmit` earlier in
its life won't double-fire when it later hits `Stop`, and vice versa, with
no marker-scheme change needed.

**Scope boundary — the two other `UserPromptSubmit`-only advisory nudges
(`nudge-worktree-anchor.sh`, `nudge-error-mode-analysis.sh`) are explicitly
NOT touched by this plan** (confirmed with the engineer). They share this
hook's shape (one-shot marker, advisory `additionalContext`,
`UserPromptSubmit`-only) but not its failure mode: their harm requires a
*next* action to redirect (working in the wrong tree, an unanalyzed error) —
a session ending on its final turn loses nothing by missing them, since no
further action happens either way. The handoff nudge's harm is specifically
"the session may end with no warning that it ran long," which is exactly the
final-turn/no-next-prompt case. This asymmetry means the audit-siblings
principle doesn't mechanically apply here; noted rather than silently
skipped.

**Message text: unchanged, reused verbatim for both trigger points.** The
existing copy ("If the current task is not close to done, suggest running
/handoff... If the task is nearly complete, ignore this and finish.") reads
correctly whether injected before the user's next prompt or after Claude's
own turn just finished — no Stop-specific wording needed.

**Log line: add `event=<UserPromptSubmit|Stop>`.** The existing log format
(`nudged session=%s est=%s model=%s window=%s`) doesn't currently record
which event fired it. Adding one field lets a future query (like the one
that grounded this plan) directly measure how often each trigger point
catches a crossing, instead of re-deriving it from transcript timing.

### Assumption ledger

Root: the current `UserPromptSubmit`-only trigger structurally cannot warn
a session that crosses the context threshold on its final turn with no
subsequent prompt.

| # | Assumption | Tag |
|---|---|---|
| root | UserPromptSubmit-only trigger misses final-turn crossings with no next prompt | `[verified: ad-hoc query over 307 transcripts, script path above — 2/18 crossers (11.1%) were final-turn-only misses]` |
| 1 | `Stop` supports `hookSpecificOutput.additionalContext` as a non-blocking mechanism, same posture as `UserPromptSubmit` — it does NOT force an extra generation cycle the way `decision:"block"` does | `[verified: code.claude.com/docs/en/hooks, fetched this session, verbatim — "Stop hooks can block Claude from finishing the turn, forcing continuation of the agentic loop. They can also inject context for Claude to act on without blocking... The context is added to the conversation and Claude processes it on the next model request."]` |
| 1b | Registering a second `Stop` hook alongside the existing `advance-past-commit-stall.sh` (a blocking `turn-gate` hook) doesn't prevent either from running — both execute and their outputs compose, rather than one short-circuiting the other | `[verified: code.claude.com/docs/en/hooks, fetched this session — "All matching hooks run in parallel, and identical handlers are deduplicated automatically", corroborated by this repo's own PreToolUse precedent: 19 distinct hooks already run under one `Bash` matcher in claude/.claude/settings.json]` |
| 2 | `Stop`'s input JSON includes `session_id`, `transcript_path`, `agent_type` (subagent-only), `permission_mode` — the same fields the hook already reads | `[verified: code.claude.com/docs/en/hooks, fetched this session — Stop receives the documented "common input fields" plus Stop-specific `last_assistant_message`]` |
| 3 | Registering the same script under a second event is an existing, accepted pattern in this repo | `[verified: claude/.claude/settings.json — capture-session-id.sh is the only one of 39 distinct hook commands registered under two events (SessionStart, SubagentStart); no other multi-event registrations exist]` |
| 4 | The one-shot marker (keyed by `SESSION_ID` only) correctly prevents a double-fire across the two events in one session with no scheme change | `[verified: claude/.claude/hooks/nudge-handoff-near-context-cap.sh:124 — FIRED_MARKER="${MARKER_DIR}/${SESSION_ID}", no event component]` |
| 5 | The output's `hookSpecificOutput.hookEventName` must match the actual firing event (`"Stop"` vs `"UserPromptSubmit"`), not a hardcoded value | `[verified: code.claude.com/docs/en/hooks examples — every event's example payload stamps its own event name in hookEventName]` — the hook currently hardcodes `"UserPromptSubmit"` (nudge-handoff-near-context-cap.sh:138); this must become dynamic, read from the input's `hook_event_name` field |
| 5b | Every existing test's `_base_payload()` helper omits `hook_event_name` entirely, so once the hook reads it dynamically, all pre-existing test cases exercise the empty-string/missing case, not a real event name — the hook must have defined, deliberate behavior for that case, not an accident of whatever `case`/`if` order gets written | `[verified: claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py — `_base_payload()` builds only `session_id` and `transcript_path`, no `hook_event_name` key, in every current test]` |
| 6 | The two other UserPromptSubmit-only nudges are out of scope for this plan | `[engineer-confirmed]` |
| 7 | The 11.1% final-turn-miss figure is directional, not a precise population rate, given n=18 crossers | `[verified: script output above — small-sample caveat stated explicitly, not silently generalized]` |
| 8 | `# hook-class: informational` (line 2 of the hook) stays unchanged — adding a `Stop` registration does not make this a `# hook-class: turn-gate` hook, since the script never emits `decision:"block"` on either event; `turn-gate` is reserved for Stop hooks that can block the turn from ending, which this one deliberately never does | `[verified: plugins/claude-hook-review/skills/claude-hook-review/SKILL.md §4 — "turn-gate ... fires on Stop and may block the turn from ending ... not a Stop hook mislabeled 'gate'"; claude/.claude/hooks/tests/test_hook_alignment.py has no rule tying Stop-registration to hook-class, classification is purely about blocking capability]` |

Lighter-primitive check for the "second file" alternative (over-powered
relative to reusing the existing script): considered (a) a wholly separate
`nudge-handoff-on-stop.sh` duplicating the field-read/threshold/marker logic,
and (b) a shared sourced helper file for the duplicated logic. Rejected (a)
per row 3/precedent above — duplicating ~140 lines of identical logic for a
one-field difference is the heavier choice, not the lighter one. Rejected
(b) as unnecessary: there is only one script's logic to reuse, so extending
it directly is already the lightest option; a shared-helper split only pays
off with three or more callers, and this repo's CLAUDE.md rule against
shared partials across skills doesn't apply to hook scripts, but the general
principle (don't build an abstraction for one caller) still does.

## Critical files

- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — the only
  behavioral change. `# hook-class: informational` on line 2 stays as-is
  (see ledger row 8 — this hook never emits `decision:"block"` on either
  event, so it does not become `turn-gate`). Read `hook_event_name` from
  input alongside the existing four fields (`session_id`, `agent_type`,
  `permission_mode`, `transcript_path`) in the same `jq` pass; default an
  empty/missing value to `"UserPromptSubmit"` (matching the pre-existing
  behavior every current test implicitly relies on, per ledger row 5b —
  this is a deliberate fallback, not a silent accident of read order) and
  stamp the resolved value into the output's `hookSpecificOutput.hookEventName`
  instead of the hardcoded `"UserPromptSubmit"` string; add `event=%s` to
  both the `nudged` and `schema-drift` log lines. No change to the
  threshold table, marker scheme, subagent gate, or plan-mode gate — all
  reused as-is.
- `claude/.claude/settings.json` — add a `Stop` entry registering
  `~/.claude/hooks/nudge-handoff-near-context-cap.sh`, matcher-less
  (Stop has no matcher support, matching `advance-past-commit-stall.sh`'s
  existing registration). Reuse opportunity: mirror the exact
  `capture-session-id.sh` multi-event registration shape already present
  under `SessionStart`/`SubagentStart`. Coexists with
  `advance-past-commit-stall.sh`'s own `Stop` registration without
  conflict — see ledger row 1b.
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` —
  extend `_base_payload()` to accept a `hook_event_name` parameter
  (default `"UserPromptSubmit"`, matching every existing call site so
  current tests keep exercising the same case they always have), then
  `pytest.mark.parametrize` the existing fire/no-fire/marker/schema-drift
  cases across `hook_event_name=Stop` in addition — this file already
  uses `pytest.mark.parametrize` for the model→window table (line 408),
  so parametrize here rather than duplicating test bodies. Add a new
  assertion that the output's `hookEventName` matches whichever event the
  test invoked the hook with, that the log line's `event=` field matches
  too, and one case covering `hook_event_name` omitted entirely (asserts
  the `"UserPromptSubmit"` fallback from ledger row 5b).
- `docs/handoff-nudge.md` — update the "fires on UserPromptSubmit" framing
  to describe both trigger points and why (the final-turn gap), plus
  document the new `event=` log field.
- `README.md:166` — the hooks table's `nudge-handoff-near-context-cap.sh`
  row currently reads `— (UserPromptSubmit, advisory)`; update to reflect
  both events.

## Verification

- Run `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py -v`
  from this worktree (per this repo's three-levels-deep `.venv` convention)
  — extended tests must pass for both event names.
- Run `../../../.venv/bin/shellcheck` (via `scripts/list-shell-files.sh`) or
  target the single file directly against the modified hook.
- Manually simulate a `Stop` invocation: pipe a synthetic input JSON
  (`{"session_id":"...", "hook_event_name":"Stop", "transcript_path":"...",
  "permission_mode":"default"}`, `agent_type` omitted) with a fixture
  transcript whose last assistant usage block exceeds threshold, into the
  hook directly, and confirm the emitted JSON's `hookSpecificOutput.hookEventName`
  is `"Stop"` and the log line records `event=Stop`.
- Re-run the ad-hoc `nudge_trigger_gap.py`-style query is not required for
  merge (it measured historical transcripts, not this change's behavior),
  but is available if the engineer wants a before/after sanity check later
  via a fresh `handoff-ratio`-style pass once enough Stop-triggered fires
  have logged in practice.

## Out of scope

- `nudge-worktree-anchor.sh` and `nudge-error-mode-analysis.sh` — see the
  Approach section's scope-boundary note; explicitly declined for this plan,
  not silently skipped.
- Any change to the 60%-of-window threshold value or the model→window
  table — unrelated to this gap; last touched and sourced in GH-556/PR #561.
- Adding `SubagentStop` — the hook's existing `AGENT_TYPE`-non-empty gate
  already exits early for subagent contexts, and `Stop`'s `agent_type` field
  is only populated inside a subagent per the docs, so a bare `Stop`
  registration on the main hook already can't double-fire inside a subagent
  invocation; a separate `SubagentStop` registration would have no observable
  effect given the existing gate and isn't needed to close the gap this plan
  targets.
