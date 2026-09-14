# `ScheduleWakeup` misapplied outside `/loop`: documented, not guarded

*2026-09-01. Formerly `docs/design-decisions.md` §41.*

**Superseded by [§49](schedulewakeup-denied-by-bare-tool-name.md) (2026-09-04):** its mechanism-exhaustion claim never reached the settings layer, where a bare-tool-name `permissions.deny` removes the tool from context entirely instead of rejecting a call after the fact. The three mechanisms rejected below remain correctly rejected.

This repo will not add a hook, advisory nudge, or `CLAUDE.md` line to catch a recurring pattern: the assistant reflexively calls the built-in `ScheduleWakeup` tool as a fallback heartbeat after dispatching a subagent or backgrounding a Bash command, outside the `/loop` dynamic mode the tool is scoped to. `ScheduleWakeup`'s own description (Claude Code 2.1.258) states the scope and the prohibition directly: "Schedule when to resume work in /loop dynamic mode... Do NOT schedule a short-interval wakeup to poll for background work you started — when harness-tracked work finishes, you are re-invoked automatically, so polling is wasted." The misuse violates a constraint already present, verbatim, in the tool's own always-loaded description — this is not a documentation gap this repo can close.

A review of this repo owner's own session history found the pattern recurring across many different session types — planning, code review, plain chat, an eval harness — never inside an actual `/loop` session. Two failure sub-modes were observed, both self-corrected without user intervention in every case:

- A call omitting the required `prompt` field is rejected instantly by the harness's own schema validation.
- A well-formed call actually schedules a wakeup, which later fires and re-invokes the session before the assistant recognizes the mismatch and cancels it.

Counts and durations are not reproduced here: the corpus mixes this owner's private-project and public-repo transcripts, so publishing a figure from it is governed by `docs/private-project-redaction.md` § "Publishing a pooled tooling measurement" and its approval gate, unexercised here. See the upstream reports below for the full evidence.

`ScheduleWakeup`'s own description doesn't name the latent risk directly, but an adjacent one is real: the `Agent` tool's description warns "Never fabricate or predict a pending agent's results" for exactly the moment a stray `ScheduleWakeup` wakeup could land — mid-pending-dispatch. None of the observed instances actually fabricated or predicted a result. Every one cancelled cleanly and resumed. The clean outcome is what was observed here, not a property the pattern guarantees.

Three repo-side mechanisms were considered and rejected:

- **A `PreToolUse` gate denying `ScheduleWakeup` outside `/loop`.** No reliable predicate exists to gate on. `/loop` is harness-native, with no `SKILL.md` for it anywhere in this repo or on the local filesystem. It also has no marker analogous to the one `require-routing-read.sh:33-60` uses for the repo-owned `/plan-review` skill. The two candidate substitutes both fail:
  - A `ScheduleWakeup` prompt-shape check catches only the sentinel-literal case, not an arbitrary real `/loop` prompt.
  - An inferred read of the transcript's own command-invocation marker fails two ways:
    - It answers the wrong predicate — "did this session ever run /loop" rather than whether the current call is legitimate.
    - Even that wrong predicate relies on an undocumented format.

  Either substitute inverts `require-routing-read.sh`'s fail-open safety property into a fail-closed gate keyed on absence of evidence — guarding the one workflow that runs unattended and can't report its own breakage.
- **A `PostToolUse` advisory nudge** (the shape `consume-durable-continuity-file-on-read.sh` uses). It would re-deliver a constraint already sitting in the model's context at the moment of the misfire. It would also fire on every turn of a genuine `/loop` session — noise aimed at the one population the tool exists for.
- **A `CLAUDE.md` line restating `ScheduleWakeup`'s own scope.** Rejected as a repo-side copy of harness-owned text this repo can't keep in sync. It would also cost budget that `check-claude-md-length.sh` polices.

The correct alternative already lives in this repo. `handoff/SKILL.md`'s collect-in-flight-dispatches step tells a session to end its turn and let the harness's automatic notification arrive rather than poll. `.claude/plans/handoff-hard-block.md` separately rejected `ScheduleWakeup`-based active polling on the same "polling reimplements what the notification path already delivers passively" reasoning. That skill loads only when `/handoff` runs, though — it was not in context in any of the observed misfires, which happened in other skills entirely. The constraint that *was* in context every time is `ScheduleWakeup`'s own description; no repo-owned surface reaches all of them.

**Revisit** if any of:

- `ScheduleWakeup` itself starts rejecting calls outside `/loop`, or a `PreToolUse` input field exposes the harness's own computed active-mode state, making inference unnecessary.
- An instance surfaces where the misfire doesn't self-correct cleanly — a fabricated or predicted result, or one needing user intervention.
- A materially higher well-formed-call rate appears on large-context sessions, where a full context re-read per misfire becomes a real `docs/cost-ledger.md` line item.

## Sources

- [anthropics/claude-code #80350](https://github.com/anthropics/claude-code/issues/80350) — "Agent invokes ScheduleWakeup prematurely instead of waiting for subagent notifications," open, matches this pattern directly.
- [anthropics/claude-code #88260](https://github.com/anthropics/claude-code/issues/88260) and [#88205](https://github.com/anthropics/claude-code/issues/88205) — the missing-`prompt`-field validation-error sub-mode.
- `claude/.claude/hooks/require-routing-read.sh:33-60` — the marker-as-predicate mechanism and its fail-open default, and why it doesn't transfer to a harness-owned skill.
- `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh` — the advisory-nudge shape considered and declined here.
- `claude-skills/skills/handoff/SKILL.md` and `.claude/plans/handoff-hard-block.md` — this repo's own prior rejection of `ScheduleWakeup`-based polling, and the correct alternative's load-scope limits.
- `.claude/plans/harness-context-mismatched-tool-dispatch.md` — full assumption ledger, per-mechanism reasoning, and the corpus-evidence sourcing this entry summarizes.
