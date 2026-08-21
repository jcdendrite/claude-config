# Review-pipeline orchestrator subagent

## Context

**Goal:** stop `/code-review`, `/plan-review`, and `/ready-for-review` from
running inline in the main session, so their reviewer findings and
fix/re-review churn land in a disposable subagent's context instead of the
long-lived one — and stop the main session from writing code inline
post-handoff instead of delegating to `code-writer`.

Both failure modes were observed in the same real session (the user's own
transcript, quoted in this plan's originating request): the main session
misdescribed its own review pipeline's context-isolation behavior, then,
once corrected, confirmed that a reviewer's *file* still gets `Read` back
into the invoking session's context whenever it reports findings — so even
the existing `findings_path` mechanism only reduces the cost of a clean
review, not a dirty one. Separately, the same class of session tends to
write implementation code directly after a plan is approved rather than
dispatching `code-writer`, which `docs/design-decisions.md` §11 already
names as a known, deliberately-unenforced gap ("Routing is substitute-only
and advisory... it does not change how often the parent delegates versus
writes inline").

Why now: this repo's own review infrastructure (`findings_path`,
content-addressed markers, the `general-purpose` gate-release escape hatch)
already has almost everything this needs — the gap is that nothing
dedicates or enforces it, so sessions fall back to running the skills
inline under context pressure exactly the way `docs/design-decisions.md` §1
predicts advisory rules do.

Intended outcome: a new dedicated `review-orchestrator` subagent runs these
three skills to completion — including the fix→re-verify loop, which it
performs by nested-dispatching `code-writer` rather than editing directly
— and a new hook makes dispatching it the only path, instead of an
optional convention. The main session's context receives only a
synthesized summary, never raw findings or fix-loop churn.

## Approach

A new `review-orchestrator` agent invokes the **existing, unmodified**
`code-review`/`plan-review`/`ready-for-review` `SKILL.md` files via the
`Skill` tool and follows their instructions to completion inside its own
disposable context; a new hook denies the top-level session from invoking
those three skills directly, forcing the dispatch. The orchestrator gets no
`Edit`/`Write` tool, and — because `Bash` alone can mutate the tree just as
well as `Edit` can (`echo > file`, `sed -i`, `git commit`) — a second new
hook restricts its `Bash` calls to a narrow allowlist (read-only git
subcommands, the handful of named helper scripts, and verification/test
commands), so any step in a skill's instructions that calls for changing
repository content is genuinely, not just nominally, satisfied by a nested
`code-writer` dispatch instead. A new small checkpoint script gives the
orchestrator crash-resilience: if it's killed mid-run, the parent confirms
it's actually terminated, then re-dispatches a fresh instance with the same
run id, which resumes from the last recorded step instead of restarting.

Three decisions were escalated to the user before this design was fixed,
because each genuinely changed the plan's shape: (1) hook-enforced
delegation vs. advisory-only — **hook-enforced**, since this repo's own §1
precedent is that advisory rules get silently skipped under context
pressure, which is the exact failure mode reported, and (unlike
`code-writer`'s Edit/Write boundary, which §11 says can't be
hook-enforced) a `Skill`-tool call naming one of these three skills *is* a
distinguishable tool-call boundary; (2) reuse the existing `general-purpose`
escape hatch vs. build a dedicated agent now — **dedicated agent**, trading
the smaller footprint of reuse for a stable identity with its own
model/effort pin and hook-enforced tool scoping, mirroring why `code-writer`
itself was built instead of leaving code-writing on ad hoc `general-purpose`
dispatch (§11); (3) hand-rolled checkpoint file vs. a Workflow script for
native `resumeFromRunId` resumability — **hand-rolled**, because Workflow
scripts have no `Skill`-tool hook, so a Workflow-based version would have to
reimplement all three skills' dispatch/reconciliation/disposition logic as
JavaScript running in parallel with the `SKILL.md` prose, which is the
single-source-of-truth violation CLAUDE.md's first Engineering Judgment
bullet warns against, and nothing would mechanically catch the two
drifting apart over time.

**What the hook changes, and what it doesn't.** The gate's actual
invariant — commit/PR blocked until a content-addressed marker matches
current reviewed state — is unchanged by this plan; no new verification is
added that a marker reflects genuine review completion. That trust
currently rests on whichever session runs the skill doing so honestly; this
plan relocates where that trust is placed (from the top-level session to
`review-orchestrator`, or to `general-purpose` via the residual gap below)
without strengthening it. Read "hook-enforced" here as ergonomics — the
skill is harder to skip — not as a new cryptographic or verification
guarantee on the marker itself.

**Accepted residual: prompt injection from reviewed content.** Content
`review-orchestrator` reads *during* a review (a malicious comment in the
diff it's reviewing) is a threat class this repo already accepts for the
top-level session running these skills today. What changes is blast
radius: `review-orchestrator` can nest-dispatch `code-writer` (and only
`code-writer` or a reviewer persona — row 7b closes the path to an
unrestricted `general-purpose`/`claude` dispatch) and release the commit
gate with no human turn in between, surfacing only a synthesized summary —
so a successful injection now reaches "diff written by `code-writer`,
under its own self-review pass, + gate released," not just "diff written."
No new control beyond rows 7a/7b is proposed given this repo's stated
local-tooling, cooperative-agent threat model (`docs/design-decisions.md`'s
own scoping); named here so it isn't rediscovered as a surprise later.

### Assumption ledger

```
Root: The main/top-level session running /code-review, /plan-review, or
/ready-for-review inline pulls reviewer findings and fix/re-review churn
into its own long-lived context, which then goes stale after subsequent
edits; separately, sessions post-handoff tend to write code inline rather
than delegate to code-writer. Both are heavy work landing in a persistent
context instead of a disposable one.

Given 1: Claude Code's harness provides no built-in crash/resume for a
killed subagent — only a completed subagent can be resumed, conversationally,
via SendMessage [verified: code.claude.com/docs/en/sub-agents, fetched this
session — "The documentation provides no mechanism for resuming a subagent
that crashed or was interrupted mid-task. Resume functionality only applies
to subagents that completed their work."] — reason: vendor/harness-controlled,
outside this repo's reach.

Given 2: A dispatched subagent's tool grants come from its agents/*.md
frontmatter (or the built-in type's own registry); this repo cannot grant a
tool the harness itself doesn't support for that agent shape — reason:
vendor/harness-controlled.

Given 3: Custom agent frontmatter's `model:` field accepts only
sonnet/opus/haiku/fable/a full model ID/inherit — no finer-grained routing
exists [verified: docs/design-decisions.md §3, citing Anthropic's *Create
custom subagents* docs] — reason: vendor-imposed.

Row 1 [mechanism]: new `review-orchestrator` agent (tools: Skill, Agent,
Read, Grep, Glob, Bash — no Edit, no Write; model: opus; effort: high) —
anchors: root. It invokes the Skill tool for the named review skill and lets
the existing SKILL.md content execute verbatim inside its own disposable
context — zero duplication of any skill's dispatch, reconciliation, or
disposition logic.
  Lighter primitives rejected:
  (a) Stronger CLAUDE.md prose only, no new agent — rejected: this is the
      status quo's own advisory mechanism, and it already failed in the
      reported transcript; docs/design-decisions.md §1 names exactly this
      failure mode ("The model decides advisory rules don't apply... it
      happens reliably, not occasionally").
  (b) Dispatch the existing `general-purpose` escape hatch ad hoc instead of
      a new agent — genuinely lighter, and the initial recommendation;
      engineer chose the dedicated agent instead for the same reason
      `code-writer` itself was built rather than left on ad hoc
      `general-purpose` dispatch (§11): a stable identity gets a model/effort
      pin and hook-enforced tool scoping that ad hoc dispatch prose doesn't
      reliably produce. [engineer-verified]
  (c) A Workflow script instead of a subagent — rejected: no Skill-tool hook
      inside Workflow scripts, so it would force reimplementing three
      skills' logic as JS in parallel with the SKILL.md prose.
      [engineer-verified]

Row 2 [mechanism]: new hook `require-review-orchestrator-dispatch.sh`
(PreToolUse on Skill-tool calls naming code-review/plan-review/
ready-for-review, firing only when `agent_type` is absent from the payload
— the true top-level session) — anchors: root. A nested Skill(code-review)
call made by review-orchestrator itself (agent_type=review-orchestrator) is
unaffected, and so is any other subagent's ad hoc use of the existing
general-purpose escape hatch — this hook narrows only the top-level
session's own direct path.
  Lighter primitives rejected:
  (a) Advisory CLAUDE.md guidance only, no hook — rejected for the same
      reason as row 1(a).
  (b) A .claude/rules/ path-scoped rule — rejected: mirrors
      subagent-dispatch-authorization's own row 1(c) rejection — the
      decision point (about to invoke a Skill) isn't reliably tied to any
      single file-glob match. [verified:
      .claude/plans/subagent-dispatch-authorization.md:90-92]

Row 3 [mechanism]: new checkpoint script `orchestrator-checkpoint.sh`
(append/read subcommands; JSONL keyed by
`<repo-hash>.<orchestrator_run_id>.jsonl` under
`<config-dir>/orchestrator-checkpoints/`; `orchestrator_run_id` is minted
and remembered by the dispatching parent, not derived from a
harness-assigned session id; entries bounded to step id, status, and
marker-hash only — never raw findings or diff text, see row 3b) — anchors:
root, row 1.
  Lighter primitives rejected:
  (a) Reuse review-ledger.sh's existing session-id-keyed store directly —
      rejected: [unverified] whether `_resolve_session_id`'s capture
      mechanism (a SessionStart hook) fires the same way for a dispatched
      subagent as for a top-level session; and even if it does, a fresh
      review-orchestrator re-dispatched after a crash gets a new session
      identity, so the old key isn't independently discoverable without the
      parent separately remembering it — at which point a parent-minted
      stable id is simpler than plumbing a harness-assigned one.
  (b) No checkpoint at all, restart from scratch on crash — rejected:
      wastes already-completed reviewer dispatches and fixes; this was the
      specific gap flagged mid-session. [engineer-verified]

Row 3a [mechanism]: `orchestrator_run_id` is minted by the parent as
`<skill>-<branch-slug>-<epoch>-<4 bytes from /dev/urandom, hex>` — anchors:
row 3. The random suffix exists so two concurrent runs against the same
skill and branch (a real scenario: re-running `/code-review` on the same
branch twice in one sitting) don't collide on one checkpoint key; epoch
alone isn't collision-resistant at 1-second granularity.

Row 3b [mechanism]: checkpoint entries never carry review findings text,
diff content, or file paths beyond what's needed to name a step — anchors:
row 3. Without this bound, the checkpoint file becomes a second durable
copy of exactly the content this plan exists to keep ephemeral, just
relocated from the main session's context to `<config-dir>/orchestrator-
checkpoints/`, outside repo scope, with no redaction discipline of its own.
  Lighter primitives rejected:
  (a) Let the checkpoint carry whatever content makes resume easiest to
      implement — rejected: reintroduces the exact durability problem this
      plan is built to avoid, just under a different directory.

Row 3c [mechanism]: the noclobber-lock + PID-liveness-eviction + single
EXIT-trap primitive `review-ledger.sh` already implements
(`_append_ledger_line_locked`) is extracted into `_lib.sh` as a shared
helper, and both `review-ledger.sh` and the new `orchestrator-checkpoint.sh`
call it — anchors: row 3. This reverses an earlier draft of this plan,
which proposed duplicating the ~25-line locking algorithm instead, citing
CLAUDE.md's "a small duplicated value can beat a bad abstraction" exception.
That citation doesn't fit what's actually being copied: a
correctness-sensitive concurrency algorithm, not a value, and one this repo
would then hold in three near-identical copies (`_lib.sh`'s own
`_lib_worktree_collision_guard`, `review-ledger.sh`'s, and the new script's)
— exactly the drift CLAUDE.md's single-source-of-truth principle warns
against, made worse by both callers already loading `_lib.sh`. `_lib.sh` is
this repo's existing home for cross-script shared bash helpers (distinct
from the barred "shared skill partials" pattern, which is about SKILL.md
content, not shell library functions), so this is the correct DRY
placement, not a new abstraction built to house one caller.
  Lighter primitives rejected:
  (a) Duplicate the locking helper into the new script, as originally
      drafted — rejected per the paragraph above.
  (b) Leave `review-ledger.sh` untouched and only newly-write
      `orchestrator-checkpoint.sh` with its own copy — rejected: still
      leaves the correctness-sensitive algorithm duplicated twice
      (`_lib_worktree_collision_guard` and the new copy), and this task
      already has a live reason to touch `review-ledger.sh` (extracting a
      function it already contains), unlike the "separately-owned
      mechanism I have no other reason to touch" framing the original
      draft relied on.

Row 4 [assumption]: a custom agent granted `Agent` in its `tools:`
frontmatter can actually call the Agent tool to nest-dispatch further
subagents, reliably rather than once [unverified — this session's own
research turned up exactly one data point: a dispatched general-purpose-
shaped subagent successfully called Agent; no repo documentation or
Anthropic doc confirms this for a custom agent type specifically, and no
existing agent in this repo is granted Agent today] — anchors: row 1. This
is the single highest-risk unverified assumption in this plan — Verification
step 1 gives it an explicit protocol and pass/fail bar rather than a single
anecdotal try.

Row 5 [assumption]: review-orchestrator, running on Opus, is authorized to
dispatch reviewer/code-writer subagents despite the harness's "don't call
Agent unless the user requested it" system-prompt line, because its own
agent description prescribes those dispatches [verified:
claude/.claude/CLAUDE.md's Agent Briefing bullet 1, merged via PR #486 —
"When a skill body, a CLAUDE.md rule, or an agent description you are
following prescribes a subagent dispatch, the user put that instruction in
play... the prescription is the request. Dispatch normally."] — anchors:
root.

Row 6 [assumption]: review-orchestrator must run in the parent's own
worktree, not an isolated one, or its marker write won't be recognized by
the gate it's meant to release [verified: docs/design-decisions.md §2's
content-addressed-marker mechanism is keyed per repo-hash, and this repo's
worktree-scoped marker keying means a marker written from a different
worktree path doesn't match] — anchors: root.

Row 7 [assumption]: omitting review-orchestrator from
`_LIB_NO_GATE_RELEASE_AGENTS` is sufficient for it to write markers — no
`_lib.sh` logic change needed for gate-release itself [verified:
claude/.claude/hooks/_lib.sh:1473-1476 — "general-purpose and claude carry
the full tool set and can genuinely run a review skill, so they are
deliberately absent — that is the documented delegation escape hatch."] —
anchors: root. This is deliberately decoupled from row 7a below: whether an
agent may release a gate and whether its `Bash` calls are mutation-
restricted are two different properties that happened to be conflated in
`_LIB_REVIEW_ONLY_AGENTS` for every agent that needed both restricted
together — review-orchestrator needs the second without the first, which is
exactly why it's a new, separate array rather than an addition to the
existing one.

Row 7a [mechanism]: new `_LIB_BASH_MUTATION_RESTRICTED_AGENTS` array
(containing `review-orchestrator`), consumed by a new hook that restricts
its `Bash` tool calls to: strict read-only git subcommands (reusing
`_lib_strict_readonly_git_subcmds`), exact-path invocations of `marker.sh`,
`review-ledger.sh`, and `orchestrator-checkpoint.sh`, and exactly the
verification commands root `CLAUDE.md`'s own Commands section already
names for this repo (`.venv/bin/pytest claude/.claude/`,
`.venv/bin/ruff check claude/.claude/`,
`scripts/list-shell-files.sh | xargs -0 shellcheck`, and their
worktree-relative `../../../.venv/bin/...` forms) — a closed enumeration,
not an open "whatever the skill names" bucket — denying output redirection,
`sed -i`, `git commit`, `git add`, `rm`, `mv`, or `cp` into a tracked path —
anchors: row 1. Without this, `review-orchestrator`'s `Bash` grant lets it
mutate the tree directly (`echo > file`, `git commit`) with zero `Edit`/
`Write` tool call, which would make the "fixes route through code-writer"
claim in Row 1 false as designed rather than true by construction.
  Lighter primitives rejected:
  (a) Rely on `review-orchestrator`'s own system-prompt instructions never
      to use `Bash` for mutation, no hook — rejected: this is exactly the
      advisory-vs-hook-enforced distinction row 1(a)/row 2(a) already
      reject for the analogous cases; an instruction a model can talk
      itself out of isn't a guarantee.
  (b) Add `review-orchestrator` to the existing `_LIB_REVIEW_ONLY_AGENTS`
      array instead of a new one — rejected: that array also drives
      `_LIB_NO_GATE_RELEASE_AGENTS` (row 7), so adding it there would strip
      the marker-write capability the whole design depends on. This is
      exactly the coupling row 7 names and row 7a exists to avoid.
  (c) An open-ended allowlist bucket for "whatever verification command the
      running skill happens to name" — rejected on re-review: no existing
      hook payload exposes "which skill is running" for a PreToolUse hook
      to key on, and an open bucket has no fixture coverage by
      construction. Pinned to the closed, already-canonical command list
      above instead.

Row 7b [mechanism]: `review-orchestrator`'s `Bash` restriction (row 7a)
only covers its *direct* tool calls — it is also granted `Agent` (row 1),
and without a matching restriction on dispatch *targets* it could
nest-dispatch an unrestricted `general-purpose` or `claude` to mutate the
tree and release the gate on its behalf, reopening row 7a's gap one hop
away with zero `Bash` call from `review-orchestrator` itself. New hook
`require-review-orchestrator-agent-target.sh`: PreToolUse on `Agent`
tool calls where the caller's `agent_type` is `review-orchestrator`, denying
any dispatch whose requested subagent type is not a member of
`_LIB_REVIEW_ONLY_AGENTS ∪ {code-writer}` — anchors: row 1, row 7a. Reuses
`_LIB_REVIEW_ONLY_AGENTS` as the allowlist rather than inventing a third
roster: every member is already established as mutation-restricted and
non-gate-releasing, which is exactly the property a legitimate nested
dispatch target needs, and the array already carries this repo's
closed-enumeration discipline ("new entries are added deliberately... not
accreted via etc./like").
  Lighter primitives rejected:
  (a) Prose-only instruction in `review-orchestrator.md` naming
      `code-writer` as the only legitimate nested-dispatch target, no hook
      — rejected for the same advisory-vs-hook-enforced reason as row
      7a(a); this is the exact gap the re-review surfaced by naming a
      concrete bypass path, not a hypothetical one.
  (b) A closed allowlist of exactly `{code-writer}`, nothing else —
      rejected: `review-orchestrator` also needs to nest-dispatch the
      reviewer personas themselves (the Change-type/routing-table spawns
      that code-review/plan-review's own instructions call for), so the
      allowlist has to cover them too; `_LIB_REVIEW_ONLY_AGENTS` already
      enumerates exactly that set.

Row 8 [assumption]: plan-review's own design never has the acting session
edit the plan file itself — required changes always return to the plan's
author — so review-orchestrator running /plan-review needs no
fix-application capability at all; only code-review and ready-for-review do
[verified: claude/.claude/skills/plan-review/SKILL.md:270 — "Do not write
[the marker] on Request changes — write it only after the plan author
revises the plan and a clean re-review completes."] — anchors: root.

Row 9 [assumption]: before re-dispatching a fresh review-orchestrator
instance against a checkpoint left by a presumed-dead one, the parent
confirms genuine termination via the harness's task-status tooling (the
`Monitor`/`TaskStop`/`TaskOutput` family) rather than inferring death from
silence — anchors: root, row 3. Subagents aren't OS-addressable processes a
parent can `kill -0`; two live orchestrator instances independently reading
the same "last completed step" and both dispatching a fix or a reviewer run
would duplicate work in a way the file-locking in row 3c doesn't prevent
(locking protects concurrent *writes* from corrupting the file, not two
readers from acting on the same read). [unverified — this plan names the
mechanism to check before redispatch; confirming its exact call shape for a
task presumed dead, as opposed to one merely slow, is deferred to
implementation]
```

**On Row 4 and staged delivery — two PRs, not one.** Given how much of
this plan's value depends on nested dispatch actually working, this ships
as two PRs with a stated gate between them, not one PR with an internal
note:

- **PR 1** — `review-orchestrator.md`, `orchestrator-checkpoint.sh`, the
  `_lib.sh` extraction (row 3c), the new Bash-mutation-restriction hook
  (row 7a), and their tests. Purely additive: nothing in the existing
  pipeline changes, and the orchestrator is only ever reached by an
  explicit, manual `Agent` dispatch. Independently mergeable and useful on
  its own.
- **Gate.** Open PR 2 only once each of the three skills has been manually
  dispatched through `review-orchestrator` at least twice, on real work in
  this repo, with no nested-dispatch failure and no checkpoint-resume
  anomaly (Verification steps 3–4). A count-based, run-your-own-usage gate
  — not a calendar-time one.
- **PR 2** — `require-review-orchestrator-dispatch.sh`, the CLAUDE.md
  rewiring, and the `docs/` entries. If Row 4 turns out false or unreliable
  during PR 1's dogfooding, PR 2 simply never opens, and every stow user's
  session keeps working exactly as it does today — no one is ever
  hard-blocked by an unproven mechanism.

**Rollback.** Both PRs are cleanly revertible (new files, and additive
edits to `settings.json`/`CLAUDE.md`); this repo's stow distribution means
a revert commit propagates to every contributor's `~/.claude` the same way
the forward change did, via a plain `git pull`. For the interim window
before a revert lands, the existing `general-purpose` escape hatch (see the
residual gap below) is the **sanctioned** bypass if
`require-review-orchestrator-dispatch.sh` misfires for a session that needs
to run one of these three skills immediately — this promotes it from an
unclosed loophole to a documented rollback path, matching this repo's
existing precedent of a general-purpose-shaped escape hatch for other
marker-write hooks. No automated detection exists for post-merge,
below-dogfooding-rate unreliability in Row 4 (e.g., a 1-in-20 failure rate
the manual gate above wouldn't surface); this is an accepted gap, matching
the `spawnDepth` telemetry gap already named in Out of Scope, not a promise
this plan makes and fails to keep.

**Known residual gap: the hook can't force review-orchestrator specifically.**
It fires only on `agent_type` absent, so a session could still comply with
its letter by dispatching the existing `general-purpose` escape hatch
instead of `review-orchestrator` — closing the main-session-context problem
(FM1) but silently missing `code-writer` substitution and checkpointing.
Blocking every `agent_type` except `review-orchestrator` isn't a fix: it
would break the documented `general-purpose` escape hatch this repo already
relies on elsewhere (CLAUDE.md Safety: "if [a marker-write] hook is
harness-blocked, delegate it to a general-purpose subagent"). This is an
accepted limitation, not a hole to patch — the hook closes the worst case,
not every path around the preferred one. (See also: Rollback, above, which
depends on this same gap staying open.)

**No changes to any skill's content.** Because the orchestrator calls the
`Skill` tool and follows what loads, `code-review/SKILL.md`,
`plan-review/SKILL.md`, `plan-review/ROUTING.md`, and
`ready-for-review/SKILL.md` need zero edits — every new behavior (fix
substitution, halt substitution, checkpointing) lives in the new agent
file. Where a skill's instructions say "fix it" or "halt on findings," the
orchestrator's own body carries one generic substitution rule: if it lacks
the tool a step calls for (`Edit`/`Write`), it dispatches `code-writer`
with a narrowly-scoped description of that one change, re-verifies, and
resumes the skill's flow rather than returning control to its caller —
except for a finding the skill's own instructions give no deterministic
disposition path for (a genuine DEFER/dispute), which it surfaces in its
summary instead of guessing. **This substitution behavior has no automated
regression coverage** — it's prose interpreted by an agent's own reasoning,
not a script; Verification steps 4–5 (manual dogfooding, one-time smoke
test) are the only checks, and neither re-runs on a future edit to
`review-orchestrator.md`'s body. Named here as a permanent accepted gap,
matching the transparency of the residual-gap note above, not left
implicit.

**Checkpoint resume-decision interpretation is similarly untested.** The
checkpoint *script's* read/append contract is unit-tested (Critical Files,
below). Whether `review-orchestrator` correctly *interprets* a resumed
checkpoint — skipping a step already marked done rather than re-running it
— is agent behavior, not script behavior, and Verification step 3's manual
kill-and-resume check is the only coverage it gets, once, by hand.

## Critical files

**PR 1**

| File | Change |
|---|---|
| `claude/.claude/agents/review-orchestrator.md` | **New.** tools: Skill, Agent, Read, Grep, Glob, Bash. model: opus. effort: high (spans trivial "no findings" runs to multi-round reconciliation; its highest-stakes action — code fixes — is backstopped by `code-writer`'s own self-review pass, matching CLAUDE.md's `high` criterion). Body: accepts `skill`, `target`, and `orchestrator_run_id` in its dispatch prompt; reads any existing checkpoint for that run id and resumes from the last recorded step; invokes the named skill via the Skill tool; substitutes a `code-writer` dispatch for any Edit/Write step or "halt on findings" step per the Approach section's rule; appends a checkpoint entry (step id, status, marker-hash only — row 3b) after each meaningful step; returns only a synthesized summary (verdict/marker status, fixed/deferred/disputed counts, anything needing human judgment) — never raw findings. Must run without `isolation: worktree` (Row 6). |
| `claude/.claude/hooks/_lib.sh` | Extract `review-ledger.sh`'s noclobber-lock + PID-liveness-eviction + single-EXIT-trap append primitive into a shared function (row 3c); add `_LIB_BASH_MUTATION_RESTRICTED_AGENTS = (review-orchestrator)` and its accessor (row 7a), documented as decoupled from `_LIB_REVIEW_ONLY_AGENTS`/`_LIB_NO_GATE_RELEASE_AGENTS` (row 7). |
| `claude/.claude/scripts/review-ledger.sh` | Switch `_append_ledger_line_locked` to call the extracted `_lib.sh` helper (row 3c) — behavior-preserving, existing tests must stay green. |
| `claude/.claude/scripts/orchestrator-checkpoint.sh` | **New.** `append`/`read` subcommands; JSONL at `<config-dir>/orchestrator-checkpoints/<repo-hash>.<orchestrator_run_id>.jsonl` (row 3a for the id shape); calls the same shared `_lib.sh` locking helper `review-ledger.sh` now uses. |
| `claude/.claude/hooks/require-review-orchestrator-bash.sh` | **New.** Restricts `Bash` tool calls for `_LIB_BASH_MUTATION_RESTRICTED_AGENTS` members to the closed allowlist in row 7a; denies everything else with a message naming `code-writer` dispatch as the alternative. |
| `claude/.claude/hooks/require-review-orchestrator-agent-target.sh` | **New** (row 7b). Restricts `Agent` tool calls made by `review-orchestrator` to subagent types in `_LIB_REVIEW_ONLY_AGENTS ∪ {code-writer}`; denies a dispatch targeting `general-purpose`, `claude`, or any other type, naming the closed allowlist in the denial message. |
| `claude/.claude/hooks/tests/test_orchestrator_checkpoint_script.py` | **New.** Cases: append/read round-trip; concurrent appends under the shared lock; a truncated/corrupt JSONL line from a kill mid-write; a stale checkpoint from an abandoned run interacting with the sweep; no checkpoint file yet for a brand-new `orchestrator_run_id`; a duplicate entry for the same step (retry semantics); repo-hash scoping across two worktrees of the same repo doesn't cross-read. |
| `claude/.claude/hooks/tests/test_require_review_orchestrator_bash.py` | **New.** Cases: allowed read-only git subcommand passes; allowed helper-script invocation passes; each of the closed verification-command forms passes; a redirect/`sed -i`/`git commit`/`rm` is denied with `code-writer` guidance; the restriction does not fire for any agent type other than `review-orchestrator`; malformed/missing payload fields (command, agent_type) are handled without crashing, mirroring the equivalent case in the PR2 hook's test file. |
| `claude/.claude/hooks/tests/test_require_review_orchestrator_agent_target.py` | **New** (row 7b). Cases: dispatch to `code-writer` allowed; dispatch to a `_LIB_REVIEW_ONLY_AGENTS` member (e.g. `ciso-reviewer`) allowed; dispatch to `general-purpose` denied; dispatch to `claude` denied; the restriction does not fire for any caller other than `review-orchestrator`. |
| `claude/.claude/hooks/tests/test_agent_roster.py` | Add `review-orchestrator`'s expected model/effort entries; assert its `tools:` frontmatter excludes both `Edit` and `Write`; assert it is present in `_LIB_BASH_MUTATION_RESTRICTED_AGENTS`; assert it is absent from both `_LIB_NO_GATE_RELEASE_AGENTS` (row 7) and `_LIB_REVIEW_ONLY_AGENTS` (row 7), each with a comment explaining why — so a future "fix" can't silently re-couple the two properties. |
| `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` | Add `review-orchestrator` to the existing `GATE_RELEASE_ALLOWED_AGENTS` test fixture (currently `["general-purpose", "claude"]`), giving it the same functional allow-path test coverage those two identities already get — a roster-membership assertion in `test_agent_roster.py` alone doesn't exercise the hook's actual allow branch. |
| `claude/.claude/settings.json` | Register `require-review-orchestrator-bash.sh` and `require-review-orchestrator-agent-target.sh`, following `require-skill-review.sh`'s existing PreToolUse registration shape — without this, neither hook is live and rows 7a/7b are enforced in name only. |
| `docs/hooks.md` | Document `require-review-orchestrator-bash.sh` and `require-review-orchestrator-agent-target.sh`, mirroring existing entries' style. |
| `docs/design-decisions.md` | New §29 entry recording this decision, its rejected alternatives, and the row 7/7a/7b coupling fix (condensed from the assumption ledger above). |
| `.claude/plans/review-pipeline-orchestrator-subagent.md` | This plan, committed to the branch. |

**PR 2** (opened only after the dogfooding gate above)

| File | Change |
|---|---|
| `claude/.claude/hooks/require-review-orchestrator-dispatch.sh` | **New.** PreToolUse on `Skill` calls naming code-review/plan-review/ready-for-review with `agent_type` absent; denies with the exact dispatch shape (agent name, no isolation, required prompt fields) to run instead. |
| `claude/.claude/hooks/tests/test_require_review_orchestrator_dispatch.py` | **New**, mirroring `test_require_skill_review.py`'s shape. Cases: deny when `agent_type` absent and skill name matches one of the three; allow when `agent_type=review-orchestrator`; allow when `agent_type=general-purpose` (the preserved escape hatch — a regression here silently breaks the documented rollback path above, so this case is load-bearing, not incidental); allow for an unrelated skill name (e.g. `skill-review`) even with `agent_type` absent; malformed/missing `agent_type` field handled as absent, not as a crash. |
| `claude/.claude/settings.json` | Register `require-review-orchestrator-dispatch.sh`, following `require-skill-review.sh`'s existing PreToolUse registration shape. This repo's stow distribution means a plain `git pull` is sufficient for every contributor to pick this up — no `install.sh` re-run, since `settings.json` is a plain symlink with no `--adopt` merging step. A session already running when the pull happens won't see the new hook until its next start (hook config is read at session start), which is expected, not a bug. |
| `claude/.claude/CLAUDE.md` | Rewrite the "Code Review," "Plan Review," and "Pre-Handoff Review" bullets to describe dispatching `review-orchestrator` instead of running the skill inline. Add one bullet to Model & Effort Routing naming `review-orchestrator`, mirroring the existing `code-writer` bullet's shape. File is 141 lines against a 200-line cap — budget the additions accordingly. |
| `docs/hooks.md` | Document `require-review-orchestrator-dispatch.sh`, alongside the two PR1 hooks already documented there. |

## Verification

**Before PR 1**

1. **Feasibility spike (Row 4) — explicit protocol, before writing anything
   else.** From a minimal throwaway custom agent definition with `Agent` in
   its `tools:` frontmatter, run at least 3 sequential nested dispatches:
   at least one to a reviewer-shaped prompt, at least one to a
   `code-writer`-shaped prompt. **Pass:** every dispatch returns usable
   output, none fails after the first succeeds, no context/tool-budget
   failure appears on repeat. **Fail:** any dispatch after the first fails,
   or output is silently truncated/malformed. On fail, do not proceed with
   the rest of this plan — fall back to Row 1(b) (dispatch the existing
   `general-purpose` escape hatch ad hoc) and revise this plan accordingly.

**PR 1**

2. `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree
   (covers the `_lib.sh` extraction and `review-ledger.sh`'s existing tests
   staying green), plus `scripts/list-shell-files.sh | xargs -0 shellcheck`
   for the new shell scripts.
3. **Checkpoint script correctness (automated).** Covered by
   `test_orchestrator_checkpoint_script.py`'s enumerated cases above — this
   tests the script's mechanical read/append/resume-marker contract only,
   not whether `review-orchestrator` correctly interprets a resumed
   checkpoint.
4. **Checkpoint resume-decision correctness (manual, one-time, no
   regression coverage — see the note in Approach).** Kill a
   `review-orchestrator` dispatch mid-run against a real diff; re-dispatch
   with the same `orchestrator_run_id`. Concrete assertions: the checkpoint
   file has exactly one entry per completed step (no duplicates from the
   resumed run re-doing a step), no reviewer already dispatched is
   dispatched again, no fix already verified is re-applied.
5. **Manual dogfooding round** (the two-PR gate above): dispatch
   `review-orchestrator` by hand for each of the three skills against real
   work in this repo, at least twice each, before opening PR 2.
6. `agent-review` on `review-orchestrator.md` (code-review's own Change-type
   table already routes agent files there — no skill edit needed for this).
7. `claude-hook-review` on both new hooks.
8. `/code-review` on PR 1's full staged diff; `/plan-review` on this plan.

**PR 2**

9. **Behavioral smoke test.** From a fresh top-level session, attempt
   `/code-review` directly and confirm the hook denies it with actionable
   guidance; confirm a `general-purpose` dispatch is *not* denied (the
   sanctioned rollback path); then dispatch `review-orchestrator` and
   confirm it completes and the main session's context shows only the
   summary.
10. `/code-review` on PR 2's full staged diff.

## Out of scope

- **Extending the enforcing hook to `/skill-review`, `/ai-instruction-and-
  memory-files`, or other review-adjacent skills.** The user's request
  scoped this to code-review/plan-review/ready-for-review; those stay
  reachable via the existing `general-purpose` escape hatch.
- **Cross-model reviewer decorrelation.** Unrelated to this change; already
  addressed and rejected in `docs/design-decisions.md` §3.
- **A `spawnDepth > 1` telemetry/observability pass, and any automated
  detection of below-dogfooding-rate Row 4 unreliability post-merge.** This
  repo's transcript tooling has never recorded a dispatch deeper than one
  level; adding analysis for the new two-level shape this plan introduces,
  or alerting on intermittent nested-dispatch failure, is worth doing once
  real usage exists, not speculatively here. Named explicitly in Approach
  (Rollback) as an accepted gap, not a promise this plan makes.
- **Exact call shape for confirming a presumed-dead orchestrator is
  genuinely terminated before redispatch (row 9).** The mechanism family
  (`Monitor`/`TaskStop`/`TaskOutput`) is named; the precise sequence is
  deferred to implementation, since it depends on details of in-flight
  harness behavior this plan hasn't independently verified.
