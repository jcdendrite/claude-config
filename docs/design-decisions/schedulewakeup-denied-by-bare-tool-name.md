# `ScheduleWakeup` denied by bare tool name in `permissions.deny`, reversing [§41](schedulewakeup-misapplied-documented.md)

*2026-09-04. Formerly `docs/design-decisions.md` §49.*

Ship `"ScheduleWakeup"` as a second bare tool-name entry in `claude/.claude/settings.json`'s `permissions.deny`, alongside `EnterPlanMode`. A bare tool name removes the tool from context entirely rather than rejecting a call after the fact: "A bare tool name like `Bash` removes the tool from Claude's context entirely, so Claude never sees it" (`code.claude.com/docs/en/permissions`). [§41](schedulewakeup-misapplied-documented.md) evaluated a `PreToolUse` gate, a `PostToolUse` advisory nudge, and a `CLAUDE.md` line, and rejected all three — correctly, on their own terms. What its survey never reached was the settings layer, where this primitive already existed and needs no `/loop`-awareness to work: it prevents the call from being formed at all, rather than reasoning about which calls are legitimate.

**Which of [§41](schedulewakeup-misapplied-documented.md)'s three Revisit conditions are met.** Of the three conditions [§41](schedulewakeup-misapplied-documented.md) named for reopening itself, only the third is met. `ScheduleWakeup` still accepts out-of-`/loop` calls, and no `PreToolUse` field exposes harness-computed active-mode state. Upstream issues #80350, #88260, and #88205 are all still open, and no changelog entry through Claude Code 2.1.258 addresses this. The first condition is therefore not met. No instance surfaced of a misfire failing to self-correct cleanly, either: every observed case cancelled or disclosed honestly with no fabricated or predicted result, so the second condition is not met. The third condition, a materially higher well-formed-call rate at large context sizes, is met: the well-formed non-`/loop` call turned out to be the majority sub-mode rather than the minority [§41](schedulewakeup-misapplied-documented.md) assumed, concentrated at context sizes where a wasted re-invocation is a real cost. The supporting counts and context medians are not reproduced here — the corpus mixes private-project and public transcripts, and any count, ratio, median, or duration would inherit the private half's composition.

**The mechanism-exhaustion lesson.** [§41](schedulewakeup-misapplied-documented.md) was correct about every mechanism it evaluated and wrong about the set being complete. A mechanism-exhaustion claim needs a sweep of every configuration layer that could plausibly reach the problem — settings keys, CLI flags, hooks, prose — not only the layer the problem first surfaced in.

**Why nothing addresses the `noop` misconception directly.** The failure is driven by the model reading `noop` as a "check back later, do nothing" mode the tool does not have; errored calls carried a uniform `{delaySeconds, noop, reason}` shape with `prompt` and `stop` never present. No second mechanism — no `CLAUDE.md` line, no skill clause, no advisory nudge — addresses this directly: once the tool is absent from context, the misconception has no expression surface, since the model cannot construct a call to a tool it cannot see. A mechanism that closed a gap the deny already closes would be the same compounding-defensive-layer shape [§41](schedulewakeup-misapplied-documented.md) and [§42](code-review-fix-route-plan-architect.md) both name as a wrong-foundation tell.

**Blast radius.** The entry ships in the stow-source settings file, so it reaches every stow consumer, not only this repo. Self-paced `/loop` (no fixed interval) is degraded, but not silently. Across four manual test sessions run against a live `permissions.deny` before this change shipped, `/loop`'s dynamic mode detected the missing tool via `ToolSearch` on its first turn rather than attempting a call that could fail. Those four sessions split into two outcomes:

- Three of four disclosed the gap honestly and fell back to `Monitor`-only (event-driven, no periodic heartbeat) with no fabricated workaround.
- One of four substituted `CronCreate` as a fallback heartbeat (roughly 20-minute cadence) without asking first — a real observed instance of the substitute-mechanism risk this entry's Revisit list names below, not merely a hypothetical one.

Fixed-interval `/loop <interval> <prompt>` is unaffected, confirmed directly: a `CronCreate` job scheduled and fired successfully under the deny. Deny rules across settings scopes union rather than override, so the opt-out is editing the tracked file. Three override attempts were each tried in turn against a live user-scope deny, and `ScheduleWakeup` stayed absent in all three:

- A project-scope `permissions.allow` entry.
- A project-local `permissions.allow` entry.
- Plain omission.

These three attempts cover the project-scope and project-local settings-file layers only. The command-line flag layer, which outranks both in Claude Code's five-level precedence order, was not tried. Other stow consumers' `/loop` usage is not observable from this corpus; the blast-radius argument rests on the deny being reversible in one line plus this entry's documented migration note, not on a claim about other consumers' behavior.

**Revisit** if any of:

- The model substitutes a worse wait mechanism for the removed tool *in production* — repeated `ListAgents`/`TaskOutput` polling, a `Bash sleep`, or a recurring `CronCreate` call.
- A genuine `/loop` need arises in this repo's own pipeline — none exists today; `ci-watch.sh`, the strongest candidate, is deliberately built to avoid polling via `Bash run_in_background`.
- A stow consumer reports a silently-truncated self-paced `/loop`.
- `ScheduleWakeup` gains its own out-of-`/loop` validation upstream, which moots this entry.
- Claude Code changes bare-tool-name-deny semantics or cross-scope permission-union precedence in a future release. Either would silently invalidate this entry's claims while `test_schedulewakeup_stays_denied_in_settings` keeps passing, since that test pins only the declared config value, not the harness behavior behind it.

**Accepted residual risk.** Three things are accepted rather than closed:

- **The `CronCreate` substitution channel is unguarded.** The pre-implementation gate observed the model substituting `CronCreate` as a fallback heartbeat in 1 of 4 runs, without asking first. That is the exact autonomous-reinvocation risk this deny exists to close, now sitting on an adjacent tool. `CronCreate` carries none of: a guardrail, an ask-tier check, a cap. The first Revisit condition above already covers a production recurrence of this pattern.
- **No committed artifact backs the manual pre-implementation gate.** Its results are prose only, recorded in this plan and in this entry. No transcript, log, or fixture is committed alongside. This is the same limitation the `EnterPlanMode` sibling accepts, not a new gap unique to this entry. See `.claude/plans/plan-mode-workflow-discipline.md`'s Accepted residual risk section.
- **No Revisit condition previously covered the underlying mechanism drifting upstream.** The first four Revisit conditions above all track the *model's* behavior. None tracked Claude Code itself changing bare-tool-name-deny semantics or cross-scope union precedence — a change that would silently invalidate this entry's claims while the declared-config test kept passing. The fifth Revisit condition above closes that gap.

**Reversal is not friction-free.** Removing this line re-triggers `ask-review-permissions.sh`'s `ask` decision and a `review-permissions` re-review, exactly like adding it did. "One line to revert" describes the diff size, not the process cost.

## Sources

- [Claude Code permissions docs](https://code.claude.com/docs/en/permissions) — bare-tool-name deny semantics.
- `docs/design-decisions.md` [§41](schedulewakeup-misapplied-documented.md) — the decision this entry reverses.
- `docs/design-decisions.md` [§17](loop-simplify-name-only.md) — the `skillOverrides.loop` decision this entry leaves standing.
- `docs/design-decisions.md` [§43](ui-notification-defaults-in-stow-source.md) — the settings-scope union precedent this entry's opt-out argument relies on.
- `claude/.claude/settings.json` — the `permissions.deny` entry itself.
- `.claude/plans/prevent-non-loop-schedulewakeup-calls.md` — full assumption ledger, per-mechanism reasoning, and the live-verification test-session results this entry summarizes.
