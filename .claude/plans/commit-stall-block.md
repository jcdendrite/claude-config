# Eliminate the commit-permission turn-end stall (GH-526)

## Context

**Goal:** in repos where the engineer has opted in to autonomous shipping, a
session that has finished writing code advances to a pushed branch with an
open PR on its own, instead of ending its turn to ask the human whether they
want to review the diff first.

Issue #526 reports a recurring stall — the agent finishes the work, then ends
the turn with "Want me to commit this, or do you want to review the diff
first?" or "Per your standing instruction, I haven't committed." Each costs a
mechanical human turn carrying no decision, and lands the human at the wrong
review surface: this repo's pipeline puts the human's first look at the PR.

**Root cause, two sources, both verified.**

1. The Claude Code harness's `Bash` tool description contains verbatim:
   `- Commit or push only when the user asks. If on the default branch,
   branch first.` It is always loaded. Grepping every tracked file for
   `only when the user asks` returns **zero** matches — nothing in the stowed
   layer contradicts it. That is the "standing instruction" the agent cites.
2. `claude/.claude/CLAUDE.md:53` reinforces it by naming *presenting* as the
   terminal act of a coding task, and says nothing about committing.

**Why the repo's own design says the stall is wrong.** `README.md:119`: the
only "present to user" node is `ExitPlanMode`, before code exists; the next
human node is "Review comments arrive" on the PR.
`ready-for-review/SKILL.md:37` requires a clean tree, so stopping to show an
uncommitted diff stops *before* the gate that produces the handoff.
`handoff/SKILL.md:71` states the authorization principle — reversible, no
side effects outside the repo — and its §3.5 list omits `git commit` and
plain `git push`. `docs/walkthrough.md:33` models it: "Let me commit."

**Why existing enforcement cannot catch it.** `require-code-review.sh:115`
already carries the corrective text ("Do not ask the user for permission").
But it is a PreToolUse **deny message** — it fires only when the agent
*attempts* the operation. An agent that never attempts it never sees it. No
hook in this repo observes the end of a turn.

**Revision note.** This is the third draft. Two full `/plan-review` rounds
(4 specialists each, plus direct source verification of every load-bearing
claim) found a false-premise gate assumption, a design that let a hostile
repo grant itself push/PR authority, and a tail-slice bug that would have
made the hook never fire on the issue's own quoted text. All three are fixed
below; see the Assumption ledger for the full trail.

## Approach

Four parts: a machine-anchored opt-in that scopes the whole change, a
CLAUDE.md rule conditioned on it, a `Stop` hook that enforces it at turn end,
and one repair to an existing gate the design leans on.

### Part 0 — Scope the change with a machine-anchored opt-in

Autonomous shipping is contentious enough that it must not switch on for
every stow user on `git pull`, and — this is the load-bearing constraint —
**a repo's own committed content must never be able to grant it.**
`_lib.sh::_lib_worktree_enforcement_active` was the first design's template,
but its precedence is wrong for this case: it lets a committed
`<repo>/.claude/worktree-required` activate unconditionally, undefeatable by
a repo-level opt-out. That is sound for worktree enforcement — a hostile
repo can only make the visiting agent *more* constrained, never less. It is
unsound for a mechanism that *removes* a human checkpoint: reusing that
precedence would let any cloned repo commit a file and thereby authorize
autonomous commit → push → `gh pr create` for a user who never opted in,
with no repo-level opt-out able to defeat it and the eventual kill switch
covering only the hook half, not the always-loaded prose half.

**Resolved design (engineer decision):** the repo tier can only *narrow*,
never *grant*.

```
active  iff  ~/.claude/autonomous-shipping-required exists
             AND <repo>/.claude/autonomous-shipping-optout is absent
```

There is no `<repo>/.claude/autonomous-shipping-required` in the code path
at all — a repo cannot switch this on by committing anything. A repo that
wants contributors to run with it documents the one-time opt-in
(`touch ~/.claude/autonomous-shipping-required`) in its own README or
CONTRIBUTING, the same way any other machine-level preference is documented;
it cannot be committed into effect.

This is a **new, separate** helper, not a generalization of
`_lib_worktree_enforcement_active` — the two functions now encode genuinely
different security invariants (repo content may restrict; only the human's
own machine state may grant), and forcing them through one parameterized
signature was the mistake in the prior draft. `_lib_worktree_enforcement_active`
is untouched by this plan; zero refactor risk to the two worktree gate hooks
or `nudge-worktree-anchor.sh` that depend on it.

```bash
# Autonomous shipping is a granting mechanism (it removes a human checkpoint),
# unlike worktree enforcement (a restricting mechanism a repo may force on
# itself). A committed repo file therefore has no effect here — only the
# engineer's own machine state can activate this, and a stray root-owned
# ~/.claude/autonomous-shipping-required would force-activate autonomous
# shipping in every repo the engineer opens, which is why the $HOME guard is
# checked before the file test, not folded into it.
_lib_autonomous_shipping_active() {
  [ "$#" -eq 1 ] || return 1
  local repo_root="$1"
  [ -n "$repo_root" ] || return 1
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] || return 1
  [ -f "$home_norm/.claude/autonomous-shipping-required" ] || return 1
  [ -f "$repo_root/.claude/autonomous-shipping-optout" ] && return 1
  return 0
}
```

Direct unit tests land in `test_lib.py` for this function specifically —
empty `repo_root`, empty `$HOME`, machine file absent, machine file present,
machine file present + repo optout present, wrong-arity call under `set -u`
(exercised via `scripts/marker.sh`, which sources `_lib.sh` under `set -u`).
No such tests exist today for the worktree analogue (verified: `grep -rn
enforcement_active claude/.claude/hooks/tests/` returns zero hits before
this plan) — this function does not inherit that gap.

### Part 0.5 — `install.sh` prompts for both machine-level opt-ins

**Engineer decision, added after the second review round:** `install.sh`
today has zero interactive prompts anywhere (verified: no `read -r -p` or
`[ -t 0 ]` in the file). Both machine-level sentinels — the new
`~/.claude/autonomous-shipping-required` and the pre-existing
`~/.claude/worktree-required`, which has never had an install-time prompt —
should be offered interactively on every run, including confirming whether
to *keep* an already-enabled setting, not just offering to enable an absent
one. Folding the pre-existing worktree sentinel into the same prompt is
explicit added scope at the engineer's request, not scope creep: it is the
identical opt-in shape, and leaving it un-prompted while the new one gets a
prompt would be an inconsistent onboarding experience for the two settings
this plan puts side by side in the README.

**Design, matching `install.sh`'s existing conventions** (function-per-concern,
`INSTALL_TEST_FIXTURE` marker pairs for the test suite's block-extraction
strategy, warnings via stderr rather than hard failure):

```bash
# INSTALL_TEST_FIXTURE: machine-level-opt-ins — start
_prompt_sentinel_opt_in() {
  local sentinel_path="$1" human_name="$2" description="$3"
  if [ -f "$sentinel_path" ]; then
    printf '%s is currently ENABLED on this machine (%s).\n' "$human_name" "$sentinel_path"
    printf '%s\n' "$description"
    read -r -p "Keep it enabled? [Y/n] " answer
    case "$answer" in
      [Nn]*) rm -f -- "$sentinel_path"; echo "  → disabled: removed $sentinel_path" ;;
      *) echo "  ✓ keeping $human_name enabled" ;;
    esac
  else
    printf '%s is currently disabled on this machine.\n' "$human_name"
    printf '%s\n' "$description"
    read -r -p "Enable it now? [y/N] " answer
    case "$answer" in
      [Yy]*) mkdir -p "$(dirname "$sentinel_path")"; touch "$sentinel_path"; echo "  → enabled: created $sentinel_path" ;;
      *) echo "  ✓ leaving $human_name disabled" ;;
    esac
  fi
}

configure_machine_level_opt_ins() {
  if [ ! -t 0 ]; then
    echo ""
    echo "=== Machine-level opt-ins ==="
    echo "  (skipped — not an interactive terminal; existing settings are unchanged)"
    return 0
  fi
  echo ""
  echo "=== Machine-level opt-ins ==="
  _prompt_sentinel_opt_in "$HOME/.claude/worktree-required" "Worktree enforcement" \
    "Denies git commit/push/etc. outside a linked worktree on every repo without a per-repo .claude/worktree-optout. See README 'Worktree enforcement'."
  _prompt_sentinel_opt_in "$HOME/.claude/autonomous-shipping-required" "Autonomous shipping" \
    "Lets Claude Code commit, push, and open PRs without asking first, on every repo without a per-repo .claude/autonomous-shipping-optout. A repo cannot enable this by committing anything — only this machine-level file can. See README 'Autonomous shipping'."
}
# INSTALL_TEST_FIXTURE: machine-level-opt-ins — end
```

Both prompts default (bare Enter) to **leaving current state unchanged** —
"keep enabled" defaults Y, "enable now" defaults N — so a scripted `yes ""`
or a user who hits Enter through every prompt never flips a setting they
didn't explicitly answer. `[ -t 0 ]` gates only the outer
`configure_machine_level_opt_ins` call site, not `_prompt_sentinel_opt_in`
itself, so tests exercise the prompt logic directly with piped stdin the
same way `test_install_sh_local_bin_path.py` exercises
`ensure_local_bin_on_path` — the TTY gate is tested separately, once, by
asserting `configure_machine_level_opt_ins` makes no filesystem change when
stdin is not a TTY.

Call site: after the `chmod 700 ~/.claude` hardening block (line 80) and
before the marketplace/plugin registration section — early enough that the
prompts aren't buried under that section's longer output.

### Part 1 — The prose rule

`claude/.claude/CLAUDE.md:68` already phrases a rule conditionally on a
committed-vs-machine sentinel pair, so the wording pattern has in-file
precedent, but the *precedence* it states there is the worktree (restricting)
shape — do not copy that clause's logic here, only its style.

Add a new `## Shipping` section as the **final** section of the file. End
position is deliberate: attention decays in the middle of an always-loaded
file, and this rule has to out-compete a harness instruction that is also
always loaded. Exact text to add:

```markdown
## Shipping

- **Where autonomous shipping is active, a request to do work is the ask.**
  Some sessions carry a harness instruction of the form "Commit or push only
  when the user asks." In a repo where autonomous shipping is active — the
  engineer has run `touch ~/.claude/autonomous-shipping-required` on this
  machine and this repo carries no `.claude/autonomous-shipping-optout` —
  being asked to make the change is that ask: run `/code-review`, commit,
  run `/ready-for-review`, and open the PR without pausing to request
  permission for any of those steps. A repo cannot switch this on by
  committing anything; only the engineer's own machine state can. Do not
  offer to show the diff first — the review surface is the PR, not a local
  working tree. Merge stays human-only. This authorization is for the
  session the engineer is talking to; a dispatched subagent returns its work
  to its dispatcher rather than shipping on its own.
- Stopping is still correct when the work is genuinely blocked — a failing
  test you cannot fix, a design ambiguity with no defensible default, a tree
  left partly broken. Say what is blocked; do not ask permission to proceed
  with work that is already done.
```

The subagent clause closes a gap two reviewers found independently:
`CLAUDE.md` loads in subagent context, so without it `code-writer` would read
the authorization as licence to push from a worktree the parent meant to
review. "A repo cannot switch this on by committing anything" is stated
explicitly in the rule itself, not left implicit — a reader of this file in
an unfamiliar repo should not have to check the sentinel semantics elsewhere
to know a hostile clone cannot have silently granted this.

Reword `claude/.claude/CLAUDE.md:53`. Before:

> - After writing or modifying code, run `/code-review` before presenting the code to the user. If the review finds issues, fix them first, then present the final version.

After:

> - After writing or modifying code, run `/code-review` before the change goes anywhere — commit, PR, or a reply presenting it. If the review finds issues, fix them first. When the request was for a change, the terminal act is the commit; when it was for a proposal, a spike, or an option comparison, it is the presentation.

Compression-diff audit (required for any CLAUDE.md line that shortens):

| Removed/shortened | Surviving line | Behavior-preserving? |
|---|---|---|
| "before presenting the code to the user" | "before the change goes anywhere — commit, PR, or a reply presenting it" | Y — presentation still gated, commit and PR added |
| "then present the final version" | "when it was for a proposal, a spike, or an option comparison, it is the presentation" | Y — the proposal case keeps presentation as its terminal act |
| "fix them first" | "fix them first" | Y — verbatim |

The proposal carve-out is load-bearing: without it, "draft a patch for X so I
can see the shape" yields a branch, a commit, and a PR for a spike.

### Part 2 — The `Stop` hook

`claude/.claude/hooks/advance-past-commit-stall.sh`, registered on `Stop`
(matcher-less — the docs state `Stop` has no matcher support). On a turn that
ended in a commit/push/PR permission question with work pending, it emits:

```json
{"decision": "block", "reason": "<forward-progress instruction>"}
```

Per the [hooks docs](https://code.claude.com/docs/en/hooks): "Use
`decision: \"block\"` to prevent Claude from stopping and force it to
continue generating […] Claude stays in the same turn and keeps going." The
`reason` names the next step (`/code-review` → commit → `/ready-for-review` →
PR, stop before merge), prescribes **path-scoped staging, never stage-all**,
and names `~/.claude/.commit-stall-block-disabled` as the always-effective
in-session kill switch (works regardless of sentinel state) plus
`.claude/autonomous-shipping-optout` as the repo-scoped disable.

**Gate order** — cheapest first; `Stop` re-fires after every block, so a
mis-ordered guard pays git spawns on every non-firing turn:

1. `$HOME` non-empty and `$HOME/.claude` is a directory (else exit 0).
2. `~/.claude/.commit-stall-block-disabled` absent.
3. `agent_type` empty (subagents never force-continued).
4. `session_id` present and `_lib_valid_session_id_component`.
5. `permission_mode` != `plan`.
6. `prompt_id` non-empty, and differs from the state file's content.
7. Tail regex matches; exclusion regex does not (both fork-free `[[ =~ ]]`).
8. Repo root resolves (one `git rev-parse`), and
   `_lib_autonomous_shipping_active` is true.
9. Work pending — the remaining git calls, cheapest-discriminating last, per
   `nudge-error-mode-analysis.sh:103-110`'s "the marker check gates the
   spawn" precedent.

Steps 7 and 8 are ordered before the repo-root spawn deliberately: a regex
match is cheaper than a process spawn, and most non-firing turns fail step 7
first.

**Fire predicate, tightened, and given as a literal — not paraphrased —
since the corpus's value depends on the exact pattern.**

```bash
# Final sentence only: split on ". " / "? " / "! " followed by a capital or
# end-of-string, take the last segment. A quoted example mid-message (this
# repo discusses these strings routinely) does not reach this slice.
last_sentence="${msg##*[.?!] }"

FIRE_RE='(want me to|would you like me to|let me know if|shall I|should I|do you want me to)[^.?!]*(commit|push|open (a|the) PR|create a PR)'
EXCLUDE_RE='(merge|--force|force-push|reset --hard|close the PR|delete the branch|failing|failed|error|blocked|anyway)'

if [[ $last_sentence =~ $FIRE_RE ]] && ! [[ $last_sentence =~ $EXCLUDE_RE ]]; then
  # candidate stall — proceed to predicate 8-9
fi
```

Both regexes are unquoted in `[[ =~ ]]` (a **quoted** pattern in `[[ =~ ]]`
does not perform regex matching on bash 3.2.57, verified directly on this
machine — it falls back to literal string comparison and silently never
matches). Case-insensitive matching is required — the issue's own examples
begin "Want me to…" — via `shopt -s nocasematch` saved and restored around
the match, not `grep -i`, to stay fork-free.

Design history, so the next reviewer does not re-litigate settled tradeoffs:

- **Anchored to the final sentence, not a 600-char window.** All three issue
  quotes carry the ask in the last sentence. Verified empirically: the
  original two-independent-match, whole-window design fired on 6 of 7
  realistic legitimate turns (a prior review's corpus); final-sentence
  anchoring brought that to 0 of 7 while all three issue quotes still fired.
- **Objects narrowed to `commit|push|open/create a PR`.** `look over` and
  bare `review the diff` are dropped; all three issue quotes also contain
  "commit", so coverage is unaffected while "want me to profile it?" goes
  silent.
- **Exclusion window matches the fire window (final sentence), not the
  whole message.** This is a real, accepted tradeoff, not an oversight:
  - *If exclusion scanned the whole message*, "Fixed the failing test in the
    parser. Want me to commit this, or do you want to review the diff
    first?" — the modal shape after a routine bug-fix task, and the closest
    real-world match to the issue's own quotes — would never fire, because
    "failing" appears earlier in the message. That is the primary case this
    hook exists to catch; over-suppressing it defeats the fix.
  - *With exclusion scoped to the final sentence*, a failure signal in an
    earlier sentence is missed: "The push failed with a non-fast-forward
    error. Want me to push again after rebasing?" fires when it should stay
    silent. The cost is bounded, not open-ended: the loop guard fires at
    most once per `prompt_id`, so the agent retries the already-failing
    operation exactly once, the retry fails again for the same underlying
    reason, and the *next* Stop event for the same `prompt_id` is not
    reblocked — it ends the turn normally, reporting the failure. One wasted
    tool-call cycle, not a runaway loop and not a silently swallowed error.
  - Recorded as a known limitation in `docs/commit-stall-block.md`, with a
    dedicated corpus case pinning the accepted miss so a future change to
    the window doesn't silently reintroduce the worse (whole-message)
    failure mode without a deliberate decision.
- **Work pending** is `git status --porcelain` non-empty **or HEAD ahead of
  its upstream** — not "commits not on `origin/<default>`", which stays true
  for a branch's whole life including after the PR opens. Extracted as a
  named function (`_commit_stall_work_pending`) with its own unit cases:
  dirty tree, unpushed-no-PR, pushed-with-open-PR (must be false).

**bash 3.2 correctness — empirically verified on this machine (bash
3.2.57).** Three defects, all silent under shellcheck, all invisible if
tested only with the issue's short verbatim strings:

- Quoted regex in `[[ =~ ]]` does not match; regex must be unquoted.
- Case folding needs `shopt -s nocasematch` (saved/restored) or `grep -qiE`.
- **A fourth, more severe defect found and fixed this round:** a tail slice
  written `${msg: -600}` returns **empty** for any message shorter than 600
  characters — confirmed directly against the issue's own 64-character
  quote, which produced `[]`. As originally drafted, the hook would never
  have fired on any of the issue's three real examples. Replaced with
  final-sentence extraction (above), which has no length floor. A corpus
  case at the exact 64-character issue-quote length is required to pin this;
  a >700-char fixture alone would have masked the bug, since it is the one
  length at which the buggy slice happens to produce output.

**Loop guard.** `stop_hook_active` is not a re-entrancy guard — the docs
define it as "`true` if the hook was triggered by Claude finishing a
response." State lives at `~/.claude/.commit-stall-block.d/<session_id>`,
content = last-fired `prompt_id`; at most one forced continuation per user
turn, re-arming on a new `prompt_id` (content-comparison pattern from
`nudge-worktree-anchor.sh:144-147`).

Two failure directions, both fail-silent:

- **Write failure** (read-only `$HOME`, full disk) must not block. Write the
  state file, **read it back**, and emit `block` only if it holds the current
  `prompt_id`. Otherwise stay silent.
- **Empty `prompt_id`** would compare equal to an absent state file's `""`
  and misbehave in either direction depending on comparison order. Guard
  with `[ -n "$PROMPT_ID" ] || exit 0` before the comparison, and log a
  `schema-drift` line on that path.

Required tests: absent `prompt_id`, empty `prompt_id`, a bounded-iteration
case (invoke N times with one payload, assert exactly one fire), state-file
write failure (read-only dir) stays silent.

**Exit code 2 is a second block channel.** The docs: "On exit code 2, Claude
Code prevents Claude from stopping and continues the conversation with the
stderr text as feedback, the same way `decision: block` with `reason` works."
`jq` exits 2 on usage errors, so a failing emit without an explicit `exit 0`
tail turns a broken `jq` into a block loop whose reason is a jq error string.
`|| true` on the emitting jq, explicit `exit 0` as the final line; a test
running the hook with `jq` absent from `PATH` must assert exit 0, empty
stdout — silent-allow, the opposite of what a gate hook would do.

**Git calls are unbounded on stock macOS.** `_lib.sh:29-35`'s `_lib_capped`
falls back to running the command bare when `timeout(1)` is absent — it is
not a bound there. Three git calls total in this hook (repo-root resolve,
status --porcelain, upstream comparison); a failure or hang on any of them
yields empty output ⇒ read as "no work pending" ⇒ silent, the correct
fail-safe direction, pinned by a test with `git` off `PATH`.

**Logging.** `~/.claude/.commit-stall-block.log`, mirroring
`nudge-handoff-near-context-cap.sh:107,133`: `fired session=… prompt=…` per
fire; `schema-drift session=… field=…` when a required input field is
absent; and one near-miss line — tail matched the permission-verb half but
not the object half → `phrasing-drift`, the only signal that catches a
future model rewording the stall without logging on nearly every turn.

**Hook class.** `test_hook_alignment.py:246-252` pins `# hook-class:` to
`gate` or `informational`. Neither is honest: `gate` triggers
`test_emit_deny_defined_before_lib_source`, a PreToolUse-specific contract
this hook never invokes; `informational` is a false label on a hook that
blocks. Add a third value, `turn-gate`. The filename must avoid the
`deny-|require-|enforce-|guard-|block-|check-*-guard` prefixes
(`_GATE_PREFIX_PATTERNS`) that would force `gate` —
`advance-past-commit-stall.sh` does. **Cost, stated explicitly:**
`GATE_HOOKS` drives every Layer-2 auto-parametrized behavior test, so a
`turn-gate` hook inherits none of them and needs hand-written equivalents —
notably jq-absent, which here must be silent-allow.

**Test infrastructure gap.** `claude/.claude/tests/helpers.py`'s `run_hook`
reads `payload["hookSpecificOutput"]["permissionDecision"]`, which raises
`KeyError` on `{"decision": "block", ...}`; `run_hook_advisory` maps any
non-deny payload to "allow", which cannot express "blocked the turn." A new,
Stop-specific runner is required that asserts the exact `{"decision",
"reason"}` key pair — the harness routes on those literal keys, and a typo
silently no-ops.

Also update `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md`
§4 and §9, which enumerate the two-value class taxonomy and describe
`emit_deny` JSON as the only block shape. Leaving it stale would have the
skill contradict the hook it is meant to review. This arms
`require-skill-review.sh` and `require-plugin-version-bump.sh` on the
commit — budgeted, not avoided.

### Part 3 — Repair the gate the design leans on

**Verified defect, not hypothetical.** `require-ready-for-review.sh:92-94`
exits 0 unless the command is `git push` or `gh pr ready`;
`:164-167` exits 0 when `gh pr view` finds no open PR. So `gh pr create` is
matched by nothing, and a first push on a branch with no PR fail-opens. The
sequence this plan's `reason` text prescribes — commit → push →
`gh pr create` — currently has `/ready-for-review` enforced by prose only.

Add a third boolean, `is_gh_pr_create`, to the existing tokenize-by-fragment
block (`FRAGMENTS` / `_lib_fragment_invokes_git` pattern at lines 82-94, same
shape as the existing `is_git_push`/`is_gh_pr_ready` pair), matching
`gh pr create` per fragment. Skip the PR-existence early-return (`:164-167`)
specifically on that arm — a PR being created by definition does not exist
yet. Fix the deny-message branch, which currently falls into the
push-worded message for any non-`gh pr ready` arm; it must name which of the
three commands triggered the gate.

**Verified residuals, recorded rather than silently left** — both found by
this round's SDET review, reproduced by inspection of the existing bypass
logic:

- The `--dry-run` bypass (`:97-100`) greps the **whole** `$COMMAND` string,
  not per-fragment. `git push --dry-run && gh pr create` would exit 0 at
  that check before the new `gh pr create` arm is ever evaluated. Fixing
  this requires reworking the existing push-bypass checks to be
  fragment-scoped, which is a larger, separable change to
  `require-ready-for-review.sh`'s existing logic — out of scope for this
  plan; not yet filed as a follow-up issue.
- The default-branch bypass (`:132-149`) runs unconditionally before the
  gate and would also exempt a same-command `gh pr create`. Low real-world
  exposure — `gh pr create` errors when the current branch is the PR base —
  but not zero for a scripted or chained invocation. Same open item.
- **Bare `git push` with no PR stays ungated**, unchanged from today. A
  pushed branch is not yet a review artifact; `gh pr create` is the
  publication boundary, and `/ready-for-review` pushes as part of its own
  flow.

**Also out of scope, and now on firmer ground than the prior draft's
deferral:** a staged-content secret scanner on `git commit`. With Part 0's
corrected precedence, autonomous shipping cannot be granted by repo content,
so the exposure this would guard against is bounded to repos the engineer's
own machine has opted into — materially narrower than the prior draft's
deferral, which rested on a bounding argument a reviewer correctly showed did
not hold under the original (grantable) sentinel design. Still filed as a
separate follow-up issue: it is a distinct hook with its own threat model,
not a rider on this change.

### Alternatives set aside

- **Prose only** — `README.md:50` states the repo's position: prompt-layer
  instructions are advisory.
- **Hook only, no prose** — burns a wasted turn on every affected task
  instead of preventing the stall.
- **`hookSpecificOutput.additionalContext` instead of `decision: block`** —
  for `Stop` it "sets up context for Claude to see on the next request,"
  which does not remove the human turn.
- **A `PostToolUse` hook on the last `Edit`/`Write`** — cannot know the agent
  is done editing; would fire mid-implementation.
- **Advanceable-state predicate** (fire on any turn ending with work
  pending, no phrasing requirement) — catches silent stops too, but also
  fires on "here's the change, thoughts?". Set aside; the prose layer covers
  silent stops, imperfectly, and that gap is recorded (Row 19).
- **Generalizing `_lib_worktree_enforcement_active` into a shared,
  parameterized sentinel helper** — the first draft's approach. Rejected
  once the two mechanisms were shown to need opposite repo-tier precedence;
  forcing them through one signature was the defect, not a shortcut around
  duplication. Two small, honestly-different functions instead.
- **Letting a repo commit `.claude/autonomous-shipping-required` (the
  worktree-identical shape)** — the prior draft's design. Rejected: it let
  any cloned repo grant itself the agent's push/PR authority, undefeatable
  by a repo-level opt-out, contradicting the intended safety property that
  only the engineer's own machine state can activate a checkpoint-removing
  mechanism.

### Assumption ledger

```
Root problem: the always-loaded harness instruction "Commit or push only when
the user asks" is uncontradicted in this repo's global layer, so a session
ends its turn asking permission for an operation this repo's pipeline treats
as autonomous.

Row 1 [mechanism]: machine-anchored opt-in, repo tier narrows only — anchors:
  root — confines the change to engineers who explicitly opt in on their own
  machine, and closes the specific hole a repo-grantable sentinel opened.
  Lighter primitives rejected: (a) single global kill-switch file — disables
  only the hook, leaving the always-loaded prose rule reaching every
  session; (b) the worktree-identical shape (repo commit grants
  unconditionally) — lets any cloned repo self-authorize autonomous
  push/PR, verified exploitable by reading _lib_worktree_enforcement_active
  directly; (c) no scoping at all — ships autonomous push/PR into every
  private work repo on `git pull`.
Row 2 [mechanism]: CLAUDE.md rule conditioned on the opt-in — anchors: row1
  — states the authorization in the always-loaded layer where the harness
  instruction lives, so the stall is prevented, not corrected after.
Row 3 [mechanism]: Stop hook returning decision:"block" — anchors: root —
  turn-end is the only boundary at which this failure is observable. Lighter
  primitives rejected: (a) PreToolUse deny message — exists already at
  require-code-review.sh:115, never reached, since the agent makes no tool
  call; (b) UserPromptSubmit additionalContext (what all three existing
  nudges use) — fires before the turn, cannot see how it ended.
Row 4 [mechanism]: gh pr create added to require-ready-for-review.sh —
  anchors: row3 — without it the sequence the hook's reason text prescribes
  reaches a public remote with no gate.
Row 5 [assumption]: the harness Bash tool description contains "Commit or
  push only when the user asks" and no tracked repo file contradicts it
  [verified: this session's Bash tool schema; git grep, zero matches] —
  anchors: root
Row 6 [assumption]: no Stop or SubagentStop hook exists in this repo today
  [verified: claude/.claude/settings.json, plugins/*/hooks/hooks.json] —
  anchors: row3
Row 7 [assumption]: Stop input carries last_assistant_message, prompt_id,
  permission_mode; decision:"block" continues the same turn; exit 2 is a
  second block channel [verified: https://code.claude.com/docs/en/hooks] —
  anchors: row3
Row 8 [assumption]: stop_hook_active is NOT a re-entrancy guard [verified:
  same page] — anchors: row3
Row 9 [assumption]: require-ready-for-review.sh matches only `git push` and
  `gh pr ready`, and exits 0 when no open PR exists — so `gh pr create` and
  first-push-without-PR are ungated [verified: require-ready-for-review.sh:
  92-94, 164-167, read directly] — anchors: row4. Additional residual found
  this round: the --dry-run bypass (:97-100) is whole-command-scoped and
  would also exempt a chained `gh pr create`; the default-branch bypass
  (:132-149) runs before any command-type check. Both filed as follow-up,
  not fixed by row4's change.
Row 10 [assumption]: deny-private-project-refs.sh exits unless origin.url
  contains "claude-config", so redaction scanning is absent in every other
  repo [verified: deny-private-project-refs.sh:283] — anchors: row1
Row 11 [assumption, SUPERSEDED this round]: originally recorded that
  _lib_worktree_enforcement_active's three-tier shape would be generalized
  and reused. A security review showed that shape's repo-tier OR-precedence
  is unsound for a granting mechanism — verified directly by reading the
  function: a committed repo file activates unconditionally, undefeatable
  by repo opt-out. Superseded by Row 1's machine-anchored design; the
  worktree function is now untouched by this plan. Recorded rather than
  deleted, since the superseded reasoning is what the next reviewer would
  otherwise re-derive from scratch.
Row 12 [assumption]: _lib_capped falls back to running bare when timeout(1)
  is absent, so it is NOT a bound on stock macOS; three git calls in the
  Stop hook are all subject to this [verified: _lib.sh:29-35] — anchors:
  row3
Row 13 [assumption]: on bash 3.2.57, a quoted regex in [[ =~ ]] does not
  match while unquoted does, and matching "Want me to" needs case folding;
  separately, ${msg: -600} returns EMPTY for any message under 600 chars —
  confirmed against the issue's own 64-char quote, which produced an empty
  string [verified: executed all forms directly on this machine, twice,
  independently, in two review rounds] — anchors: row3. This is the
  correction that matters most in this revision: the original design would
  not have fired on any of the issue's real examples.
Row 14 [assumption]: a turn-gate hook inherits none of the Layer-2
  auto-parametrized behavior tests [verified: test_hook_alignment.py,
  GATE_HOOKS derivation] — anchors: row3
Row 15 [RESOLVED this round, was engineer-verified + contradicted]: original
  text authorized "the same framework that powers worktree requirement,"
  which a security review showed meant a repo-grantable sentinel — a
  contradiction was flagged rather than resolved unilaterally, per this
  skill's own rule. Put to the engineer directly with the concrete tradeoff;
  resolved as machine-anchored, repo-narrows-only (Row 1). No longer
  ambiguous — recording the resolution, not re-flagging it.
Row 16 [assumption]: the authorized span runs through PR open, not commit
  only [engineer-verified] — anchors: root. Unaffected by this round's
  changes; re-verified consistent with Parts 1 and 3.
Row 17 [assumption]: the fire predicate stays phrasing-matched rather than
  advanceable-state [engineer-verified] — anchors: row3. Unaffected;
  re-verified consistent with the corrected predicate.
Row 18 [assumption]: the tightened regex will still miss some phrasings and
  may occasionally match a legitimate turn [unverified] — anchors: row3.
  Bounded by the exclusion set (final-sentence-scoped, with its own
  documented residual), the once-per-turn guard, the opt-in, and the
  phrasing-drift log line.
Row 19 [assumption]: silent (non-question) stops are not caught by the hook
  and are covered only by the prose layer, which is itself opt-in [verified:
  predicate requires a question construction] — anchors: row3. The issue is
  not fully closed by this PR alone; recorded in the PR body, and the PR
  should reference #526 rather than auto-close it.
Row 20 [mechanism]: install.sh interactive opt-in prompts for both
  sentinels — anchors: row1 — an opt-in mechanism only a machine-level file
  can satisfy needs a discoverable, low-friction way to set that file, or
  the opt-in becomes de facto opt-out-forever for engineers who don't know
  the sentinel exists. Folding in the pre-existing worktree-required
  sentinel is the engineer's explicit added scope. [verified: install.sh
  has no existing interactive prompt of any kind before this plan — grepped
  directly; the prompt/skip/keep/disable behavior was executed directly on
  this machine for all four cases, including piped non-TTY stdin]
```

## Critical files

**New — the hook and its destructor**

- `claude/.claude/hooks/advance-past-commit-stall.sh` — `# hook-class:
  turn-gate` within the first five lines. Header states purpose, dispatch
  surface, fail-silent posture, the deliberate omission of `set -euo
  pipefail` (as `nudge-worktree-anchor.sh:51-54` does), and known gaps
  (exclusion-window residual, `_lib_capped` non-boundedness on stock macOS,
  `--dry-run`/default-branch bypass residuals inherited from Part 3).
  **Reuse:** `_lib_jq`, `_lib_valid_session_id_component`, the new
  `_lib_autonomous_shipping_active`; the single-jq-pass-into-pre-initialized-vars
  read from `nudge-handoff-near-context-cap.sh:29-49`; `jq -n --arg`
  emission as in `nudge-worktree-anchor.sh:149-159`. Explicit `exit 0` as
  the final line.
- `claude/.claude/hooks/cleanup-commit-stall-marker.sh` — `SessionEnd`
  destructor. **Reuse:** copy `cleanup-worktree-anchor-nudge-marker.sh`,
  including its `_lib_valid_session_id_component` guard. 30-day
  `find … -mtime +30 -delete` sweep on the fire path.

**Modified — new shared helper (own commit, does not touch the worktree
function)**

- `claude/.claude/hooks/_lib.sh` — add `_lib_autonomous_shipping_active`
  (shown above) as a standalone function.
  `_lib_worktree_enforcement_active` is not modified.
- `claude/.claude/hooks/tests/test_lib.py` — direct unit cases for the new
  function: empty `repo_root`, empty `$HOME`, machine file absent, machine
  file present alone, machine file present + repo optout, wrong-arity call.

**Modified — gate repair (own commit)**

- `claude/.claude/hooks/require-ready-for-review.sh` — add `is_gh_pr_create`
  to the existing fragment-tokenized boolean set; skip the PR-existence
  early return on that arm; fix the deny-message branch to name the
  triggering command.
- `claude/.claude/hooks/tests/test_require_ready_for_review.py` — `gh pr
  create` under `fake_gh_no_pr` (not `fake_gh_pr_exists`, which would pass
  regardless of whether the fix is implemented) → deny; chained and wrapped
  forms; bare `git push` with no PR still passes through unchanged
  (`test_branch_with_no_pr_allowed`, untouched).

**Modified — install-time onboarding**

- `install.sh` — add `_prompt_sentinel_opt_in` and
  `configure_machine_level_opt_ins` (shown in Part 0.5), called after the
  `chmod 700 ~/.claude` block. Wrapped in a new `INSTALL_TEST_FIXTURE:
  machine-level-opt-ins` marker pair, matching the extraction convention
  `test_install_sh_local_bin_path.py` and `test_install_sh_continuity_hardening.py`
  already use.
- `claude/.claude/hooks/tests/test_install_sh_machine_level_opt_ins.py` —
  new, mirroring `test_install_sh_local_bin_path.py`'s extraction-and-`bash
  -c`-invocation shape. Cases: sentinel absent + `y` → created; sentinel
  absent + bare Enter → stays absent; sentinel present + bare Enter → stays
  present; sentinel present + `n` → removed; non-TTY stdin (empty/closed
  pipe into `configure_machine_level_opt_ins`) → no filesystem change and no
  hang, for both sentinels.

**Modified — wiring, prose, taxonomy, docs**

- `claude/.claude/settings.json` — matcher-less `Stop` block; one more
  `SessionEnd` entry for the destructor. No `statusMessage` on either —
  every existing `statusMessage` in this file is on a `PreToolUse` entry
  (verified: lines 162-326); the `SessionEnd` blocks at 128-152 carry none,
  and this follows that convention.
- `claude/.claude/CLAUDE.md` — new `## Shipping` section (final section);
  line 53 reword. 107 lines today against a 200-line cap.
- `claude/.claude/hooks/tests/test_hook_alignment.py` — accept `turn-gate`;
  extend the class docstring to say it means a hook that blocks a *turn*
  from ending rather than a tool call from running.
- `claude/.claude/tests/helpers.py` — a Stop-specific test runner asserting
  the exact `{"decision", "reason"}` key pair, distinct from `run_hook`
  (reads `permissionDecision`, wrong key entirely for this hook) and
  `run_hook_advisory` (cannot express "blocked").
- `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` — §4 and
  §9 taxonomy and block-shape updates; bump the plugin `version`.
- `docs/hooks.md` — a line-start `- **\`name.sh\`**` bullet per new hook
  (regex-enforced by `test_hook_documented_in_hooks_md`).
- `README.md` — rows in the Hook → Gates → Cleared-by table (146-165); a new
  `### Autonomous shipping` subsection alongside `### Worktree enforcement`
  (peer treatment, not a single sentence) whose first line names the
  *symptom* ("the agent ends its turn asking whether you want to review the
  diff before it commits") so it is findable by what the reader noticed, an
  activation snippet, and a one-line pointer to the docs page. Both this
  subsection and `### Worktree enforcement`'s existing activation snippet
  get a line noting `install.sh` now offers the machine-level opt-in
  interactively, so the manual `touch`/heredoc snippets read as the
  non-interactive/scripted alternative, not the only path.
- `docs/commit-stall-block.md` — new page on the `docs/handoff-nudge.md`
  precedent: **leads with the two things that matter to the reporter** — the
  activation command and what fires — before log format. Covers: the
  machine-anchored opt-in and why a repo commit cannot grant it; the fire
  predicate and its exclusion-window tradeoff (the accepted "failure signal
  in an earlier sentence" miss, with the bounded-retry argument); in-session
  recovery (`Esc` to interrupt; `touch ~/.claude/.commit-stall-block-disabled`
  via the `!` shell escape); log format; Known limitations (silent stops
  uncaught; the `--dry-run`/default-branch bypass residuals from Part 3;
  `claude -p` leaks state files at one-shot rate; no bound on the git calls
  without GNU coreutils).

**New — tests** (`claude/.claude/hooks/tests/`)

- `test_advance_past_commit_stall.py` — subprocess-with-stdin-JSON shape from
  `test_nudge_worktree_anchor.py:26-45`. **Reuse:** `conftest.py`'s
  `isolated_home`, `git_repo`; `claude/.claude/tests/helpers.py` for
  `HOOKS_DIR`, `TRAVERSAL_SESSION_ID`, `assert_gate_handles_traversal_session_id`,
  and the new Stop-specific runner. Required coverage:
  - A **table-driven corpus** as `(message, expected)` tuples, each positive
    carrying a comment naming issue #526: the three issue quotes verbatim
    at their **natural length** (the 64-char quote specifically, to pin the
    tail-slice fix); ≥8 realistic negatives — a post-PR-open turn, a
    self-referential message quoting the trigger phrase, a genuine design
    question, "Fixed the failing test… want me to commit?" (must FIRE — the
    modal case), "The push failed… want me to push again?" (documented miss
    — asserted silent, with a comment explaining why per the exclusion-window
    tradeoff above), "want me to profile it?" after a commit.
  - The bash 3.2 trio, each pinned with its own case: quoted-vs-unquoted,
    case folding, and the tail-slice length floor at exactly 64 chars.
  - Loop guard: `prompt_id` absent and `prompt_id: ""` both silent;
    bounded-iteration (N invocations, one payload, exactly one fire);
    state-file write failure (read-only dir) stays silent.
  - Opt-in matrix: machine file absent → silent; machine file present, no
    repo optout → fires; machine file present + repo optout → silent; a
    committed `.claude/autonomous-shipping-required` in the repo, machine
    file absent → **silent** (the specific case this round's redesign
    exists to guarantee).
  - `$HOME` unset and `$HOME/.claude` absent → silent.
  - `agent_type` non-empty → silent. `last_assistant_message` missing/null.
  - Hand-written Layer-2 equivalents: `jq` absent from `PATH` → exit 0,
    empty stdout; `git` absent → silent.
  - Emitted JSON pinned to exactly `{"decision": "block", "reason": …}`.
- `test_cleanup_commit_stall_marker.py` — mirrors the existing cleanup test,
  **plus** sweep coverage the existing ones lack because their hooks have no
  sweep: >30d entry deleted, ≤30d preserved, sweep confined to the state dir
  when it is a symlink or absent.

## Verification

1. **Tests.** From a linked worktree: `../../../.venv/bin/pytest claude/.claude/`.
   Picks up `test_hook_alignment.py` and `test_shellcheck.py`
   auto-parametrization with no wiring.
2. **Lint.** `../../../.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
3. **Hook in isolation.** With `~/.claude/autonomous-shipping-required`
   touched and a dirty tree:
   ```bash
   printf '%s' '{"session_id":"t1","prompt_id":"p1","cwd":"'"$PWD"'","permission_mode":"default","hook_event_name":"Stop","last_assistant_message":"Want me to commit this, or do you want to review the diff first?"}' \
     | bash claude/.claude/hooks/advance-past-commit-stall.sh
   ```
   Expect the block payload. Then, with the same payload: remove the machine
   file (expect silent); restore it and add `.claude/autonomous-shipping-optout`
   in the test repo (expect silent); commit a
   `.claude/autonomous-shipping-required` in the test repo with the machine
   file absent (expect silent — the redesign's central guarantee); repeat
   the original request with the same `prompt_id` (expect silent, re-arms
   guard); clean tree (expect silent); `permission_mode: plan` (expect
   silent).
4. **End-to-end, live session, on a throwaway GitHub repo with a real
   remote** — a scratch repo with no remote cannot run `gh pr create` and
   the upstream comparison never resolves. `claude/.claude/**` goes live on
   `git pull` with no re-install. With `~/.claude/autonomous-shipping-required`
   touched: confirm the agent proceeds to commit and PR without a permission
   question; confirm the forced continuation **terminates** (reaches the PR
   and stops); confirm `~/.claude/.commit-stall-block.d/<sid>` holds exactly
   one `prompt_id`.
5. **Negative controls.** (a) Without the machine file, the agent stalls as
   it does today, even if the repo commits
   `.claude/autonomous-shipping-required` — this is the finding this round
   exists to close, verify it directly rather than trusting the unit test
   alone. (b) A turn ending "want me to profile it?" on a branch with an
   open PR must not block. (c) Ask it to merge the PR — it must still stop
   (`block-gh-pr-merge.sh` and `CLAUDE.md:100-106` unaffected).
6. **Commit sequencing.** Land the new `_lib.sh` function, the gate repair,
   the prose rule, the hook, and the `install.sh` prompts as **separate
   commits** — the prose half is the safe half, and a clean `git revert` of
   the hook alone is the fastest rollback for a misfiring turn-end block.
7. **`install.sh` prompts, live run.** Run `./install.sh` interactively in a
   scratch `$HOME` (or accept the one-time state change on the real machine,
   since both prompts default to no-op on Enter): confirm both sentinels
   prompt in the expected order, confirm a bare Enter changes nothing for
   either, confirm `y`/`n` create/remove the expected file, and confirm a
   piped invocation (`echo | ./install.sh` or `install.sh < /dev/null`)
   skips both prompts without hanging.

## Out of scope

- `claude/.claude/skills/code-review/SKILL.md:3` ("before presenting code").
  Its description already reads "a commit is pending," so the drift from the
  CLAUDE.md reword is small; the CLAUDE.md rule is the surface that governs
  behavior.
- Repo-root `CLAUDE.md:100-106` and the `CONTRIBUTING.md:33` restatement —
  they address human contributors too, the named stand-alone-prose exception
  to DRY. The new global rule owns the general authorization; these keep the
  merge prohibition.
- A staged-content **secret scanner** on `git commit` — filed as a separate
  follow-up issue; see Part 3's closing note on why this round's redesign
  narrows but does not eliminate the case for it.
- The `--dry-run`-chained and default-branch bypass residuals in
  `require-ready-for-review.sh`, inherited by the new `gh pr create` arm —
  see Part 3 and Row 9. Filed as a follow-up on the existing hook's
  bypass-detection logic, which is broader than this plan's scope.
- Suppressing the block in `claude -p` headless runs. No reliable
  non-interactive signal exists in the `Stop` payload, and in a headless run
  there is no human to ask, so forcing continuation is the intended
  behavior.
- Broadening the predicate to catch silent, non-question stops (Row 19). The
  PR should reference #526, not close it — this plan does not fully
  eliminate the failure mode the issue names, only the phrasing-matched
  subset of it, and that gap should be visible to the issue's author rather
  than implied closed.
