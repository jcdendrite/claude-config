# Plan-mode workflow discipline

## Context

Keep agent-initiated planning out of harness plan mode, which is the only
condition under which subagent `model:` routing stops being honored.

PR #631 (merged as `a179811`) measured the escalation and established its
cause. Across 500 plan-mode dispatches, 489 resolved to Opus, including all 70
that carried an explicit `model: sonnet` param. A falsification test then ruled
out the obvious confound — that the repo's `opusplan` default makes plan mode
and an Opus-anchored parent nearly synonymous — by isolating 178 non-plan-mode
dispatches from Opus-anchored parents: 178/178 resolved to Sonnet, matching the
pin. A mirror counter-example closed the other direction: a plan-mode dispatch
whose parent was running Sonnet still resolved to Opus. The override is gated
on `permissionMode`, not on the parent's model in either direction
(`docs/case-studies/plan-mode-model-resolution.md`).

That conclusion sets this plan's whole shape. Because the escalation is keyed
to plan mode itself, no model-routing change can fix it — the only lever is
not being in plan mode. The case study names exactly that as one of two real
levers: "keeping agent-initiated planning out of harness plan mode entirely."

Documenting the leak did not close it. Every agent-initiated `EnterPlanMode`
call still escalates all downstream discovery fan-out, and nothing in the repo
argues against making that call — `git grep -i "plan mode"` returns hits that
are all either historical plan-file records or procedural handling of plan mode
*once already active*. There is no rule about whether to enter it.

Intended outcome: the model cannot enter plan mode and is told what to do
instead, so the repo's prescribed planning path runs where `model:` pins are
honored. A human's own entry stays untouched throughout.

## Approach

Three changes in one PR:

- **Lever A** — an advisory bullet in `claude/.claude/CLAUDE.md` telling the
  agent not to enter plan mode itself, and what to do instead.
- **Lever B** — `"EnterPlanMode"` in `permissions.deny`, removing the tool so
  the model cannot enter plan mode even if the prose is ignored.
- **Lever C** — flipping the session default `model` from `opusplan` to
  `sonnet`, so the config stops advertising a planning tier it no longer
  delivers.

**A and B are the fix; C is a consequence of it.** A states the rule and B
enforces it, and between them the agent's planning runs outside plan mode
where `model:` pins hold — the 178/178 result is direct evidence that they do.
C fixes nothing about the escalation and is not claimed to: no model setting
can, because the override is keyed to `permissionMode`.

What C fixes instead is a config that stops being true once A and B land.
`opusplan` means Opus *in plan mode*, Sonnet during execution. With plan mode
closed to the agent, its Opus half is reachable only through the one path the
repo now tells agents not to take — so the prescribed workflow (`plan-it`
outside plan mode) runs planning on Sonnet while the default advertises Opus
planning. Flipping to `sonnet` makes the config honest about what is already
happening, and moves the Opus decision into the open as an explicit choice.

**The escalation path is a real workflow, not a consolation.** Start the
session with `--model opus` and run `plan-it`: the parent's planning turns get
Opus, and because `plan-it` no longer enters plan mode, the discovery fan-out
resolves to Sonnet as pinned. This is the exact configuration the falsification
test established for Opus-anchored, non-plan-mode parents: 178/178 dispatches
resolved to Sonnet, honoring the pin. That corpus was reviewer-agent
dispatches rather than `plan-it`'s own fan-out, so this rests on the mechanism
generalizing across agent types — which is how the case study frames it — not
on a measurement of this exact workflow. On that basis it is better than what
`opusplan` plus plan mode produced, where the fan-out was the part on Opus.

Two things this plan does **not** claim about C, because neither survives the
evidence. It is not a cost win on the common path: in a default session that
never enters plan mode, `opusplan` and `sonnet` are the same thing, so the flip
changes nothing there. And it is not a quality regression: under `opusplan`
with A and B in place, the prescribed path was *already* running planning on
Sonnet. The flip changes what the config says, not what the common path does.
Its only behavioral delta is that a human who enters plan mode by hand gets
Sonnet parent turns rather than Opus — subagents in that session still escalate
either way.

Two prior entries bear on C, and neither is contradicted here.
`docs/cost-levers-considered.md:79` is this exact flip, dropped after four
revisions on operational grounds — the settings-guard collision, the doc
sweep, the escalation path — all of which this plan re-solves rather than
disputes. `:27` is a broader "reduce Opus usage" lever closed on the grounds
that Opus's 15.7% spend share was already acceptable; it is cited here only
to note that the case for C is not a cost case and so does not reopen it. The
appended follow-up must distinguish the two rather than implying either
verdict was wrong.

### Lever C — flip the session default

`claude/.claude/settings.json`'s `"model": "opusplan"` becomes `"sonnet"`.

This change cannot be committed from inside a Claude Code session:
`guard-settings-session-keys.sh` guards `model` (among six machine-local keys)
and denies any `git commit` staging a changed value. It is a PreToolUse hook
bound to Claude Code's own Bash calls, so a human committing from a terminal
never invokes it — that is the whole of the "bypass," not a special case.

**The engineer's commit is a one-way gate.** The hook diffs the *entire
staged file* against `main`, not the commit's own delta. Once `model: sonnet`
is on the branch, `settings.json` differs from `main` on a guarded key
permanently until merge — so **every** later Claude-authored commit that
stages that file is denied, even one whose own diff never mentions `model`.
A post-review tweak to the deny rule, a rebase-conflict resolution, a
docs-driven touch: all blocked.

Sequencing therefore reads: land every other change first as Claude-authored
commits, resolve **all** settings.json feedback — including anything
`/plan-review` and `/code-review` raise — and only then hand the one-line
`model` edit to the engineer. After that point, any further settings.json fix
goes through the terminal too. The deny-rule edit (Lever B) touches the same
file but an unguarded key, so it commits cleanly while `model` still reads
`opusplan`.

### Lever B — `permissions.deny: ["EnterPlanMode"]`

A bare tool name in `permissions.deny` removes the tool from the session's
context entirely, so the model never sees it. This would be the repo's first
bare tool-name deny entry — all 30 existing entries use the parenthesized
`Tool(specifier)` form.

Lighter primitives weighed and rejected:

- **Advisory prose alone (Lever A).** No config change and no global tool
  removal, but instruction-layer only. #631's own 0/70 result is the evidence
  against relying on stated routing intent to bind harness behavior.
- **A `PreToolUse` hook matching `EnterPlanMode`.** Better in one respect — a
  denial carries a reason string, where tool removal is silent. Rejected as
  the *heavier* option: it costs a script, a pytest module, a settings.json
  registration, and a `docs/hooks.md` entry against the deny rule's one JSON
  line, and the teaching it adds duplicates Lever A's prose at a second site.
- **`--disallowedTools EnterPlanMode` in the `claude-auto` wrapper.** Lightest
  of the three, but only reaches sessions started through that wrapper; the
  plain `claude` invocation — the common path — stays unguarded.

The deny rule's silence is the one real cost, and Lever A is what pays it:
prose the model reads every session names the alternative that a vanished tool
cannot.

**Lever B removes a guardrail, not only a routing inefficiency.** Plan mode is
the one read-only checkpoint the agent can impose on itself at the harness
level rather than by convention. Per
[the permission-modes doc](https://code.claude.com/docs/en/permission-modes):
"Plan mode tells Claude to research and propose changes without making them.
Claude reads files, runs shell commands to explore, and writes a plan, but
does not edit your source." So the guarantee being given up is *no source
edits* — not "no side effects," since shell commands still run. The substitute
Lever A points at, `require-plan-review.sh`, gates Edit/Write/ExitPlanMode
only once an uncommitted plan file already exists; it does not stop an agent
skipping plan-file creation and going straight to code, and it does not cover
Bash at all. This plan accepts that trade — the lost guarantee is narrower
than it first appears, and the escalation it prevents is measured — but it is
a trade, and it lands hardest on unattended sessions where no human is present
to enter plan mode by hand.

### Lever A — advisory bullet in `claude/.claude/CLAUDE.md`

Placed in **Agent Briefing**, adjacent to the existing bullet on calling
`ExitPlanMode` before delegating execution — the two are siblings, and a
reader looking for "what do I do about plan mode" finds both together. Model
Routing was the alternative placement and was set aside: its bullets are all
"use model X for work of type Y", whereas this is a workflow-choice rule that
happens to have a routing consequence.

The bullet names the harm by reference to `docs/auto-mode.md`'s plan-mode
section rather than restating the measurement — #631 made that section the
single source of truth and this plan does not reopen it. Drafted text:

> - **Do not enter harness plan mode on your own initiative.** Entering it
>   escalates downstream subagent dispatches to Opus, overriding the Model
>   Routing rules below (see `docs/auto-mode.md`'s plan-mode subsection for
>   the measurement). Plan on a feature branch instead — `plan-it` Step 1's
>   "Otherwise" branch derives a slug, creates the worktree, and writes the
>   plan file with no plan mode involved. This governs only entry you
>   initiate yourself; a human's `Shift+Tab`, `/plan` prefix, or
>   `defaultMode` setting is untouched, as is planning this way when the user
>   asks you to.

The rate is left unquantified in the bullet because the measurement varies by
dispatch type (97–99.7%) rather than being a flat absolute; `docs/auto-mode.md`
carries the numbers and this bullet points at them.

Model Routing's Opus bullet changes in the same commit, from:

> - **Opus:** judgment-heavy reasoning, plan-mode planning, and
>   parent-dispatcher orchestration.

to:

> - **Opus:** judgment-heavy reasoning and parent-dispatcher orchestration.

Two reasons, either sufficient. Lever C removes Opus from the default
session's plan-mode turns, so the bullet would no longer describe the shipped
config. And Levers A and B route agent planning out of plan mode entirely, so
"plan-mode planning" stops naming a path the agent takes at all — the Opus
decision now lives in an explicit `--model opus` session (Row 2a), which the
`Sonnet (default)` bullet's escalation guidance already covers.

### Assumption ledger

```
Root: harness plan mode resolves every subagent dispatch to Opus regardless
of `model:` frontmatter or an explicit dispatch param, so any agent-initiated
plan-mode entry silently escalates all downstream discovery fan-out.

Givens:
- The plan-mode model override is platform behavior gated on
  `permissionMode` — beyond reach: Anthropic owns the harness dispatch path,
  and no repo-side setting reaches it.
- Shift+Tab plan-mode entry is a UI toggle, not a tool invocation — beyond
  reach: the harness owns the keybinding; permissions and hooks act only on
  tool calls.

Row 1 [mechanism]: keep agent-initiated planning out of plan mode, via Lever A
(rule) and Lever B (enforcement) — anchors: root — the override is keyed to
`permissionMode`, so not entering plan mode is the only lever that reaches it.
Row 2 [assumption]: the override is gated on `permissionMode`, not on the
parent's model, so no model setting can fix the escalation [verified:
docs/case-studies/plan-mode-model-resolution.md — 178/178 non-plan-mode
dispatches from Opus-anchored parents resolved to Sonnet, and a mirror
counter-example had a Sonnet parent's plan-mode dispatch resolve to Opus] —
anchors: row1 — the natural competing hypothesis, that subagents inherit the
parent's model and a `sonnet` default would therefore dissolve the
escalation, is falsified by this row and cannot be used to justify Lever C.
Row 2a [assumption]: outside plan mode, an Opus-anchored parent's dispatches
honor their `model: sonnet` pin, so `--model opus` plus `plan-it` yields Opus
planning turns with Sonnet fan-out [verified: the mechanism the 178/178 result
establishes — that corpus was `staff-*`/`ciso-reviewer` dispatches from
Opus-anchored non-plan-mode parents, not `plan-it`'s own discovery fan-out, so
this row rests on the mechanism generalizing across agent types as the case
study frames it, not on a dispatch-for-dispatch match] — anchors: row1 — the
escalation path is grounded, but its exact configuration is untested.
Row 2b [mechanism]: flip `model` from `opusplan` to `sonnet` — anchors: row1 —
once row1 closes plan mode to the agent, `opusplan`'s Opus half is reachable
only through the path the agent is told not to take, so the default advertises
a planning tier the prescribed workflow never receives. This is a coherence
fix, not a fix for root.
Row 3 [assumption]: `guard-settings-session-keys.sh` denies any Claude-
authored commit staging a changed `model` key [verified:
claude/.claude/hooks/guard-settings-session-keys.sh `GUARDED_KEYS_JSON`;
tests/test_guard_settings_session_keys.py::test_model_change_denies_commit]
— anchors: row2b
Row 4 [assumption]: five files carry a live "opusplan is the default"
assertion in editable prose — README.md, claude-auto.sh, settings.json,
docs/auto-mode.md (including `:199`, inside the plan-mode section), and
docs/scripts.md [verified: `git grep -n opusplan -- README.md docs/ claude/`.
Four further hits are Axis-3 preserved records left untouched:
docs/case-studies/hashline-edit-format.md, docs/cost-levers-considered.md, and
docs/case-studies/plan-mode-model-resolution.md with its index entry in
docs/case-studies.md]
— anchors: row2b — supersedes the "11 sites across 5 files" figure at
docs/cost-levers-considered.md:79, which counted files touched during an
earlier PR's editing process rather than a stable point-in-time total.
Row 5 [assumption]: the committed value reaches existing users on `git pull`
with no re-install, because install.sh stows settings.json as a symlink
[verified: install.sh's `stow -t "$HOME" claude` invocation; CLAUDE.md
"Changes under `claude/.claude/**` go live on `git pull`"] — anchors: row2b
Row 6 [mechanism]: bare-name `"EnterPlanMode"` in `permissions.deny` —
anchors: root — blocks autonomous plan-mode entry for the sessions row1
cannot reach, namely those started with an explicit `--model opus`.
Row 7 [assumption]: a bare tool name in `permissions.deny` removes that tool
from the session's tool list [verified: measured this session — denying
`WebSearch` dropped the init-event tool count 88→87; denying `EnterWorktree`
removed it while `ExitWorktree` survived, showing removal is per-tool, not
per-family] — anchors: row6
Row 8 [assumption]: the same removal applies to `EnterPlanMode` specifically,
leaving `ExitPlanMode` and every human entry path intact [verified:
interactive session against a full copy of the stowed settings.json —
`EnterPlanMode` absent with no loadable schema; `ExitPlanMode` still present
as a deferred tool; `Shift+Tab` and the `/plan` prefix both still entered plan
mode] — anchors: row7 — headless `-p` never exposes plan-mode tools even under
`--permission-mode plan`, so this could only be settled interactively.
Row 8a [assumption]: interactive sessions honor project-scope bare-name deny,
not only headless ones [verified: a `WebSearch` control, denied in the same
full copy of the stowed `settings.json` used for Row 8's check, was
behaviorally unavailable in the interactive session while the deferred
`WebFetch` remained] — anchors: row7 — rules out the failure mode
where the mechanism measured headlessly does not apply to real sessions.
Row 9 [assumption]: no hook, test, or skill depends on `EnterPlanMode` being
present [verified: `git grep -n EnterPlanMode -- claude/ docs/ README.md`
returns nothing; require-plan-review.sh gates `ExitPlanMode`] — anchors: row6
Row 10 [mechanism]: advisory bullet in CLAUDE.md's Agent Briefing — anchors:
root — a bare-name deny removes the tool silently, so without prose the agent
has no signal about what to do instead.
Row 11 [assumption]: no existing prose in either checkout discourages agent-
initiated plan-mode entry [verified: `git grep -n -i "plan mode"`, 51 hits,
every one procedural or historical] — anchors: row10
Row 12 [assumption]: the escalation path for Opus-during-planning users is an
explicit `--model opus` session running `plan-it`, not a new wrapper script
[engineer-verified] — anchors: row2b
Row 13 [assumption]: all three levers ship as one PR [engineer-verified] —
anchors: root — the work this depends on merged as `a179811`, so this branch
sits directly on main.
Row 14 [assumption]: plan mode's harness-level guarantee is "does not edit
your source", not "no side effects" — shell commands still run
[verified: code.claude.com/docs/en/permission-modes, quoted in Lever B] —
anchors: row6 — bounds how much guardrail Lever B actually gives up.
Row 15 [assumption]: a human has three plan-mode entry paths — `Shift+Tab`,
the `/plan` prefix, and `defaultMode: "plan"` — none routed through the
`EnterPlanMode` tool [verified: code.claude.com/docs/en/permission-modes] —
anchors: row6 — this is the premise that makes Lever B narrower than
"prohibit plan mode entirely".
Row 16 [assumption]: after the engineer's `model` commit lands, any later
Claude-authored commit staging settings.json is denied, because the hook
diffs the whole staged file against `main` rather than the commit's own delta
[verified: claude/.claude/hooks/guard-settings-session-keys.sh] — anchors:
row3 — forces the sequencing and the two-action rollback.
```

## Critical files

**Claude-authored commits (land first):**

| File | Change |
|---|---|
| `claude/.claude/CLAUDE.md` | Agent Briefing — new bullet (Lever A), placed after the existing `ExitPlanMode`-before-delegating bullet. **Also** Model Routing's Opus bullet at `:74`, which currently reads "**Opus:** judgment-heavy reasoning, plan-mode planning, and parent-dispatcher orchestration" — drop "plan-mode planning" per the Lever A section's two reasons |
| `claude/.claude/settings.json` | `permissions.deny` — append `"EnterPlanMode"` (Lever B). Do **not** touch `model` in this commit |
| `docs/auto-mode.md` | Add a row to the "Hard-floor deny rules" table for the new entry; update the opusplan-default prose at `:23`, `:24`, `:42`, `:51`, `:141`, and at `:199`, where "this repo's `opusplan` default makes plan mode and an Opus-anchored parent nearly synonymous" describes the confound at measurement time and must be scoped to then rather than left present-tense |
| `docs/case-studies/plan-mode-model-resolution.md` | Its closing line names "keeping agent-initiated planning out of harness plan mode entirely" as one of two levers, "both … follow-up decisions, not made here". This PR makes that decision — append a dated follow-up recording the outcome, per Axis 3 rather than editing the record |
| `README.md` | `:240` ("Configured with **opusplan** as the default model") and `:418` (the `claude-auto` bullet's two `opusplan` references) |
| `claude/.claude/scripts/claude-auto.sh` | `:4,6` — comments asserting the repo default is `opusplan`. Update the rationale; the wrapper still earns its place for `--model` passthrough |
| `docs/scripts.md` | `:65` — same stale rationale as the wrapper's comments |
| `CHANGELOG.md` | New `### Changed` bullet at the top of `## [Unreleased]`: bolded lead, prose rationale, **Migration:** callout leading with the recommended planning workflow — start the session with `--model opus` and run `/plan-it`, which gets Opus planning turns with Sonnet fan-out (Row 2a). Must state that the flip does not change what a default session does, so readers do not go looking for a behavior change that is not there |
| `claude/.claude/skills/review-permissions/SKILL.md` | Widen the frontmatter TRIGGER to cover `permissions.deny` bare tool-name entries, and add one checklist item: does removing this tool silently break a documented workflow, and is the removal paired with prose telling the agent what to do instead? Lever B ships the repo's first bare tool-name deny entry, and this skill is the only thing that would review the next one |
| `claude/.claude/hooks/tests/test_hook_alignment.py` | Add a config-value pin: `json.loads` the real `settings.json` and assert `"EnterPlanMode" in settings["permissions"]["deny"]`. Docstring must scope the claim honestly — it proves the rule is declared, not that the harness honors it |
| `docs/cost-levers-considered.md` | **Append** a dated follow-up note (Axis 3 — the `:79` and `:27` rows are preserved records, edit neither). It must record that the falsification test *removed* the flip's original cost rationale and supplied a coherence rationale instead, so the next person to propose it as a cost lever finds the refutation rather than repeating the reasoning. Also resolves the 2026-08-11 note's "reopening the flip is a separate decision, not made here", and corrects the "11 sites across 5 files" figure against Row 4's count |

**Engineer-authored commit (lands last):**

| File | Change |
|---|---|
| `claude/.claude/settings.json` | `:67` — `"model": "opusplan"` → `"sonnet"`. Blocked from the harness by `guard-settings-session-keys.sh`; commit directly from a terminal |

**Reuse opportunities**

- `python3 claude/.claude/scripts/transcript-analysis.py subagent-mix` already
  emits the per-agentType observed/requested/declared model-mix table that
  produced #631's numbers — reuse it for post-flip verification rather than
  writing a new instrument.
- `docs/auto-mode.md`'s existing "Hard-floor deny rules" table is the
  established home for documenting a `permissions.deny` entry; add a row, do
  not open a new section.
- `docs/cost-levers-considered.md`'s `| Lever | Verdict | Measured reason |`
  three-column format and its existing dated-follow-up convention are both
  already established in-file; match them.

## Pre-implementation gate — run, passed

This gate must run before Lever B is implemented, since Row 8 decides whether
Lever B ships at all; running it during verification instead would mean
implementing a lever the check might delete. It is also the procedure to
repeat for the periodic re-verification the residual-risk section commits to.

Setup: append `"EnterPlanMode"` to `permissions.deny` in a **full copy of the
stowed `~/.claude/settings.json`** — not a minimal file — placed as
`.claude/settings.json` in a scratch project, then start an interactive
`claude` session there. The full copy matters because the real file carries
30+ other deny entries plus a hook and `skillOverrides` tree that a minimal
repro cannot rule out interacting with, and because
`review-permissions/REFERENCES.md` records a prior case where project- and
user-scope settings did not behave identically for permissions.

**Add a control tool to the same deny list** — a second, unrelated tool known
to be removable. Without it a negative result is unattributable: it could mean
the deny worked, or that the settings file was never loaded.

**Ask for tool *calls*, never for a tool inventory.** Self-reported tool lists
are unreliable — a model can claim access to a tool that has been removed.
Forcing an actual call makes the result observable: the call either happens or
it does not.

Four checks:

1. Instruct the session to *use* the control tool. It must be unable to.
   (Failing this means the deny is not reaching interactive sessions at all,
   which invalidates everything below.)
2. Instruct the session to *call* `EnterPlanMode`. It must be unable to.
3. `Shift+Tab` and the `/plan` prefix must both still enter plan mode — the
   human's entry paths, which the permission-modes doc names as distinct from
   the tool.
4. `ExitPlanMode` must remain available, so `require-plan-review.sh`'s gate is
   unaffected.

**Result: all four passed** — see Row 8 and Row 8a for the verified specifics.
Lever B ships as planned.

Should a later re-run fail any check, drop Lever B and ship Levers A and C
alone, recording the negative in `docs/cost-levers-considered.md`.

## Verification

1. `.venv/bin/pytest claude/.claude/` — full suite. Nothing currently
   references `EnterPlanMode` (Row 9), so no test should change behavior;
   `test_guard_settings_session_keys.py` must still pass with `model`
   untouched in the Claude-authored commits.
2. `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — the
   `claude-auto.sh` edit is comment-only but still lints.
3. `git grep -n opusplan -- README.md docs/ claude/` after the sweep returns
   hits only in preserved records (`docs/case-studies/hashline-edit-format.md`,
   `docs/cost-levers-considered.md`) and in prose explaining what `opusplan`
   *means* — no remaining assertion that it is the current default.
4. **Post-merge effectiveness check.** Accumulate a fresh corpus and run
   `transcript-analysis.py subagent-mix`. The measure is **plan-mode dispatch
   volume from agent-initiated sessions**, which A and B should drive toward
   zero — *not* the plan-mode Opus resolution rate, which will stay near 100%
   because the override is `permissionMode`-gated (Row 2) and nothing in this
   PR changes it. Reading the rate as the success metric would misjudge a
   working fix as a failed one. Segment out sessions where a human entered
   plan mode by hand; those are out of this change's reach by design. Lands
   after merge; record as a dated follow-up in
   `docs/cost-levers-considered.md` rather than blocking the PR.
5. Commit order check: `git log --oneline` shows the `model` flip as the final
   commit, authored by the engineer, with every doc and prose change already
   landed beneath it.

**Rollback.** Reverting the `model` flip alone leaves the four prose sites
Row 4 names — README.md, claude-auto.sh, docs/auto-mode.md, docs/scripts.md —
plus the CHANGELOG entry asserting `sonnet` against a shipped `opusplan`, and
no test compares
prose to config. So a revert is two actions, not one: revert the flip *and*
either revert the doc sweep or land a follow-up restoring the `opusplan`
prose. Both of those touch `settings.json` or its documentation after the
guarded key already differs from `main`, so both go through the terminal —
same one-way gate described in Lever C.

**Accepted residual risk.** Nothing automated catches harness-semantics drift:
if a future Claude Code version stops honoring bare-name deny, the entry stays
in `settings.json`, the pin test still passes, and Lever B silently stops
working. The pin test catches only the entry being *removed or reshaped*
(e.g. into `"EnterPlanMode(*)"`, a weaker rule). Driving a real interactive
session from pytest would need pty automation — disproportionate for one
boolean. Recorded as a residual, with periodic manual re-verification noted in
the `docs/cost-levers-considered.md` follow-up rather than implied closed.

## Out of scope

- **Staging or gating the settings.json rollout.** `install.sh` stows
  `claude/.claude/settings.json` as a symlink, so the flipped default reaches
  every stow consumer the moment they `git pull` — no re-install, no opt-in
  window. That is inside this repo's reach (`install.sh` is its own artifact),
  so it is a deliberate non-change rather than a fixed condition: the
  immediate-propagation contract is what makes every other `claude/` change
  work, and carving out one key would fork it. The CHANGELOG's **Migration:**
  callout is the mitigation instead.
- **A `SessionStart` notice announcing the model-default change.** The repo
  already runs `SessionStart` hooks (`session-marker-dashboard.sh`,
  `check-branch-divergence.sh`), so a one-time version-gated notice pointing
  at `--model opus` is buildable. Declined: a hook, its tests, and a
  fired/not-fired state file are disproportionate to a one-line default flip.
  The CHANGELOG **Migration:** callout is the accepted, weaker substitute —
  weaker because the repo has no guarantee anyone reads the CHANGELOG before
  their next session, which makes this the change's thinnest point of user
  communication.
- **Relaxing `guard-settings-session-keys.sh` so the `model` flip is
  Claude-committable.** The hook could be changed; it deliberately will not
  be. Guarding machine-local key drift is the point, and carving out an
  exception for the one key most worth guarding inverts it.
- **Removing or restructuring the `claude-auto` wrapper.** With `sonnet` as
  the default, the wrapper's model-mismatch rationale weakens — `sonnet` *is*
  a valid auto-mode session model. Its `--model` passthrough still earns it,
  and its comments are updated in scope; whether the script survives long-term
  is a separate call.
- **`plan-it` Step 1 restructuring.** The "Otherwise" branch already does what
  Lever A points at; no skill edit is needed. Reworking Step 1's ordering was
  floated in #631's review thread and stays there.
- **`CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1`.** Already checked and
  rejected in #631 (`docs/auto-mode.md`) — it removes the built-in `Explore`
  and `Plan` subagents entirely, moving that work onto the parent's own turns.
  A cost regression, not a fix.
