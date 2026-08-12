# Plan-mode workflow discipline

## Context

Stop harness plan mode from silently escalating every subagent dispatch to
Opus, by removing the escalation's cause and blocking the agent-initiated
entry path that reaches it.

PR #631 measured the leak and documented it: across 500 plan-mode dispatches,
489 resolved to Opus, including **all 70 that carried an explicit
`model: sonnet` param — 0/70 honored**. `Explore`'s repo-owned pin fared no
better (92/95 plan-mode dispatches on Opus, versus 0/32 outside). That PR
corrected the false claims in five files and gave the measurement a canonical
home at `docs/auto-mode.md`'s "Subagent delegation under plan mode".

Documenting a leak does not close it. Every agent-initiated `EnterPlanMode`
call still escalates all downstream discovery fan-out, and nothing in the repo
argues against making that call — `git grep -i "plan mode"` returns 51 hits,
every one either a historical plan-file record or procedural handling of plan
mode *once already active*. There is no rule about whether to enter it.

Intended outcome: the default session no longer has an Opus parent for
plan-mode subagents to inherit; the model cannot autonomously enter plan mode;
and the agent is told what to do instead. A human's Shift+Tab stays untouched
throughout.

## Approach

Three changes in one PR, stacked on PR #631 (which is unmerged, and whose
`docs/auto-mode.md` section is what the new prose cites):

- **Lever A** — an advisory bullet in `claude/.claude/CLAUDE.md` telling the
  agent not to enter plan mode itself, and what to do instead.
- **Lever B** — `"EnterPlanMode"` in `permissions.deny`, removing the tool so
  the model cannot enter plan mode even if the prose is ignored.
- **Lever C** — flipping the session default `model` from `opusplan` to
  `sonnet`.

They are described below in dependency order — C, then B, then A — because
each later one covers what the previous cannot.

**The `opusplan` flip is the foundation, not a third peer.** PR #631's own
conclusion names it: "The only real levers are revisiting the `opusplan`
session default … or accepting the cost as intrinsic"
(`docs/auto-mode.md:245`). The mechanism follows from Anthropic's sub-agents
doc — the built-in `Plan` subagent's "Model: inherits from the main
conversation" — combined with what `opusplan` means: Opus *during plan mode*,
Sonnet during execution. Plan mode makes the parent Opus; inheriting subagents
copy it. Pin the session to `sonnet` and there is no Opus parent to inherit.

That reframing is why the other two levers are still worth shipping rather
than dropped as redundant: the flip fixes the default path only. A session
started with an explicit `--model opus` still has an Opus parent, and entering
plan mode there escalates every dispatch exactly as before. Levers A and B
cover that residual.

**Two costs this plan accepts, stated plainly rather than left implicit.**

*Lever C downgrades plan authoring itself, not only the fan-out.* `opusplan`
puts Opus on the parent's own plan-mode turns — that is the feature, not a
side effect. Subagent inheritance cannot be scoped independently of the
parent's model, so removing the inherited Opus necessarily removes the
authoring Opus with it. This plan accepts that: the measured waste is
concentrated in fan-out that was never asked for, and a user who wants Opus
reasoning for a hard planning problem can still start the session with
`--model opus`. But it is a real quality reduction on the default path, not a
pure cost win.

*This reopens a verdict that was closed on different evidence.*
`docs/cost-levers-considered.md:27` judged Opus's *total spend share* (15.7%)
acceptable and left `opusplan` in place. That is a different question from
whether part of that share was spent against explicit routing intent, which is
what the plan-mode measurement shows. The appended follow-up note must state
that distinction, so the record reads as a reopening with cause rather than a
silent override.

### Lever C — flip the session default (root fix)

`claude/.claude/settings.json`'s `"model": "opusplan"` becomes `"sonnet"`.

Escalation path for Opus-during-planning users is an explicit `--model opus`
session, documented in the CHANGELOG's **Migration:** callout.

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

The mechanism claim is deliberately deferred to Model Routing's Sonnet bullet
(`claude/.claude/CLAUDE.md:75`), which already states that neither the pin nor
the param is honored during plan mode and cites the same source. The rate is
left unquantified here because the measurement is 97–99.7% depending on
dispatch type, not a flat absolute — only the explicit-param subset is a
literal 0/70.

Model Routing's first bullet changes in the same commit, from:

> - **Opus:** judgment-heavy reasoning, plan-mode planning, and
>   parent-dispatcher orchestration.

to:

> - **Opus:** judgment-heavy reasoning and parent-dispatcher orchestration.

Lever C makes plan-mode planning run on Sonnet by default, so listing it as
an Opus use would contradict the shipped default.

### Assumption ledger

```
Root: harness plan mode resolves every subagent dispatch to Opus regardless
of `model:` frontmatter or an explicit dispatch param, so any agent-initiated
plan-mode entry silently escalates all downstream discovery fan-out.

Givens:
- Plan-mode subagent model inheritance is platform behavior — beyond reach:
  Anthropic owns the harness dispatch path, and no per-repo override for the
  built-in `Plan` subagent is documented.
- Shift+Tab plan-mode entry is a UI toggle, not a tool invocation — beyond
  reach: the harness owns the keybinding; permissions and hooks act only on
  tool calls.

Row 1 [mechanism]: flip `model` from `opusplan` to `sonnet` — anchors: root —
removes the Opus parent that plan mode's inheriting subagents copy; #631's
own analysis leaves this as the only open lever.
Row 2 [assumption]: pinning the session to `sonnet` makes plan-mode subagent
dispatches resolve to Sonnet [unverified] — anchors: row1 — inferred from
#631's measurement plus the sub-agents doc's "Model: inherits from the main
conversation"; not measured post-flip.
Row 3 [assumption]: `guard-settings-session-keys.sh` denies any Claude-
authored commit staging a changed `model` key [verified:
claude/.claude/hooks/guard-settings-session-keys.sh `GUARDED_KEYS_JSON`;
tests/test_guard_settings_session_keys.py::test_model_change_denies_commit]
— anchors: row1
Row 4 [assumption]: exactly five files carry a live "opusplan is the default"
assertion — README.md, claude-auto.sh, settings.json, docs/auto-mode.md,
docs/scripts.md [verified: `git grep -n opusplan -- README.md docs/ claude/`,
this session, excluding the two preserved records at
docs/case-studies/hashline-edit-format.md and docs/cost-levers-considered.md]
— anchors: row1 — supersedes the "11 sites across 5 files" figure at
docs/cost-levers-considered.md:79, which counted files touched during an
earlier PR's editing process rather than a stable point-in-time total.
Row 5 [assumption]: the committed value reaches existing users on `git pull`
with no re-install, because install.sh stows settings.json as a symlink
[verified: install.sh's `stow -t "$HOME" claude` invocation; CLAUDE.md
"Changes under `claude/.claude/**` go live on `git pull`"] — anchors: row1
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
explicit `--model opus` session, not a new wrapper script
[engineer-verified] — anchors: row1
Row 13 [assumption]: all three levers ship as one PR stacked on #631
[engineer-verified] — anchors: root
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
| `claude/.claude/CLAUDE.md` | Agent Briefing — new bullet (Lever A), placed after the existing `ExitPlanMode`-before-delegating bullet. **Also** Model Routing's first bullet, which currently reads "**Opus:** judgment-heavy reasoning, plan-mode planning, and parent-dispatcher orchestration" — Lever C makes plan-mode planning run on Sonnet by default, so leaving "plan-mode planning" in the Opus list would contradict the shipped default |
| `claude/.claude/settings.json` | `permissions.deny` — append `"EnterPlanMode"` (Lever B). Do **not** touch `model` in this commit |
| `docs/auto-mode.md` | Add a row to the "Hard-floor deny rules" table for the new entry; update `:23-24` and `:42`/`:51`/`:141` opusplan-default prose; rewrite `:245`, whose "the only real levers are revisiting the `opusplan` session default … or accepting the cost" sentence this PR resolves |
| `README.md` | `:238` ("Configured with **opusplan** as the default model") and `:416` (the `claude-auto` bullet's two `opusplan` references) |
| `claude/.claude/scripts/claude-auto.sh` | `:4,6` — comments asserting the repo default is `opusplan`. Update the rationale; the wrapper still earns its place for `--model` passthrough |
| `docs/scripts.md` | `:65` — same stale rationale as the wrapper's comments |
| `CHANGELOG.md` | New `### Changed` bullet at the top of `## [Unreleased]`: bolded lead, prose rationale, **Migration:** callout naming `--model opus` — as the path for judgment-sensitive planning, not only for cost preference. Several of this repo's rules (scope discipline, destructive-action flagging) are prose-only with no hook backstop, so their reliability tracks model capability |
| `claude/.claude/skills/review-permissions/SKILL.md` | Widen the frontmatter TRIGGER to cover `permissions.deny` bare tool-name entries, and add one checklist item: does removing this tool silently break a documented workflow, and is the removal paired with prose telling the agent what to do instead? Lever B ships the repo's first bare tool-name deny entry, and this skill is the only thing that would review the next one |
| `claude/.claude/hooks/tests/test_hook_alignment.py` | Add a config-value pin: `json.loads` the real `settings.json` and assert `"EnterPlanMode" in settings["permissions"]["deny"]`. Docstring must scope the claim honestly — it proves the rule is declared, not that the harness honors it |
| `docs/cost-levers-considered.md` | **Append** a dated follow-up note (Axis 3 — the `:79` and `:27` rows are preserved records, edit neither). It resolves the existing 2026-08-11 note's "reopening the flip is a separate decision, not made here", and corrects the "11 sites across 5 files" figure against Row 4's fresh count |

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
4. **Post-flip leak measurement (resolves Row 2).** After the engineer's
   commit is live, accumulate a fresh corpus of plan-mode sessions and run
   `transcript-analysis.py subagent-mix`. **Filter the corpus to
   default-model sessions before computing the rate** — the escalation path
   this plan documents means some post-flip sessions will be deliberate
   `--model opus` starts, and leaving them in either masks a real residual
   leak or manufactures a fake one. Row 2 holds if the plan-mode Opus
   resolution rate for default-model sessions falls from ~98% toward the ~0%
   observed outside plan mode. This lands after the PR merges; record the
   result as a dated follow-up in `docs/cost-levers-considered.md` rather
   than blocking the PR on it.
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
