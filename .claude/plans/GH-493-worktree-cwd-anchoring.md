# GH-493 — Anchor worktree sessions so cwd cannot silently revert to the main checkout

## Context

**Goal: make a session working on a feature branch stay anchored in its
worktree, so commands, subagents, and review markers cannot silently
execute against the main checkout on the default branch.**

Worktree discipline is enforced on two tool verbs — git writes
(`require-worktree-for-git-writes.sh`) and file writes
(`require-worktree-for-file-writes.sh`). Command *execution* is
ungoverned, and the harness's cwd behavior actively pulls a
worktree-anchored session back toward the main checkout. GH-493 sampled
11 incidents across five repositories in roughly one week: four
misleading-green verification runs, one mutation of the main working
tree, one review marker recorded against the wrong repository, four
wrong-tree subagent reads, and one poisoned session-start header.

Why now: the marker-integrity case undermines the gate system every
other review guarantee in this repo rests on. `CLAUDE.md` states gates
"match on a marker's **content** — a hash of the exact state that was
reviewed." Under drift they match on a hash of a *different tree's*
state.

Intended outcome: drift becomes impossible-by-default rather than
detected-after-the-fact, marker identity stops depending on ambient
shell state, and the residual failure mode ("never anchored at all") is
reported.

## Approach

GH-493 proposes four fixes that all treat drift as inevitable and layer
detection or enforcement on top of it. Investigation found a harness
primitive the issue does not consider — `EnterWorktree{path}`, which
re-anchors the session into an existing worktree — that removes the
precondition instead. Its tool documentation states:

> Pass `path` instead of `name` to switch the session into a worktree
> that already exists (e.g., one you just created with `git worktree
> add`).

and

> Switches the session's working directory to the new worktree

That reframes the issue's proposals rather than replacing them: fix 2
(marker keying) stands on its own, fix 3 (prose) gets shorter, fix 1
moves to a cheaper event, and fix 4 is deferred.

### Established harness behavior

Claude Code 2.1.220. The cwd behavior is two-regime, and the second
regime is the defect generator:

| Condition | `cd` target | Result |
| --- | --- | --- |
| Un-anchored | outside project root (`/tmp`) | reverts to **project root = main checkout**, notice printed |
| Un-anchored | inside project root via stow symlink (`~/.claude/hooks`) | **kept, no notice** — lands in main checkout on the default branch |
| Anchored via `EnterWorktree` | either of the above | reverts to **the worktree**, notice printed |

The stow route is the silent one because `~/.claude` is a symlink into
`<repo>/claude/.claude/`, so it resolves *inside* the project root and
the harness keeps it.

Two further behaviors, both load-bearing below: a subagent's Bash
starting cwd is the parent's cwd at dispatch time (the `Working
directory:` line in an agent prompt does not set it), and a hook
payload's `.cwd` field reports the shell's current cwd rather than a
pinned project root.

### Chosen design

1. **Anchor at branch creation.** `branch-creation` gains an
   `EnterWorktree{path}` step after `git worktree add`. This is the
   root fix: it moves the revert target to the worktree, so both
   excursion routes self-correct.
2. **Make marker identity independent of ambient cwd.** `marker.sh`
   fails closed when its resolved root is the main tree under active
   worktree enforcement, and derives every value via `git -C
   "$REPO_ROOT"`. The marker-reading hooks that use bare ambient git
   calls thread one resolved root through **every** git call, not only
   the root-resolution line.
3. **Detect "never anchored".** A `UserPromptSubmit` hook reports when
   worktree enforcement is active, cwd is the main tree, and a linked
   worktree exists.

### Assumption ledger

```
Root: a session working on a feature branch can silently execute
commands, dispatch subagents, and write review markers against the main
checkout on the default branch, because the harness's cwd revert target
is the session's project root rather than the worktree it is working in.

Row 1 [mechanism]: EnterWorktree{path} re-anchor at branch creation —
anchors: root — moves the revert target to the worktree so both
excursion routes self-correct. Lighter alternatives rejected: (a) prose
telling every command to carry its own absolute anchor — that rule
already exists at subagent-delegation/SKILL.md:78-87 and did not prevent
the sampled incidents, because it sits in a skill about *whether to
delegate* that is not loaded when a suite runs; (b) a SessionStart hook
that re-anchors — SessionStart cannot change the session's cwd, only
emit context.

Row 2 [assumption]: un-anchored, `cd` outside the project root reverts
to the project root and prints "Shell cwd was reset to <project-root>"
[verified: session reproduction — `cd /tmp && pwd` from a worktree shell
printed /tmp plus the reset notice naming the main checkout; the next
call's `git branch --show-current` returned the default branch] —
anchors: row1

Row 3 [assumption]: un-anchored, `cd ~/.claude/hooks` is kept with no
notice and leaves the session in the main checkout on the default branch
[verified: session reproduction — no notice emitted; follow-up `pwd`
returned <repo>/claude/.claude/hooks and `git branch --show-current`
returned the default branch] — anchors: row1

Row 4 [assumption]: after EnterWorktree{path}, both excursion routes
revert to the worktree and print the notice [verified: session
reproduction — the same stow excursion that was silent in row 3 printed
"Shell cwd was reset to <worktree>"] — anchors: row1

Row 5 [assumption]: a subagent's Bash starting cwd is the parent's cwd
at dispatch time; the "Working directory:" prose line does not set it
[verified: two dispatches — from a drifted parent, an agent whose prompt
named the worktree returned the parent's main-checkout path on the
default branch as its first `pwd`; from an anchored parent, an agent
given no working-directory line returned the worktree] — anchors: row1

Row 6 [mechanism]: correct the CLAUDE.md Agent Briefing bullet
prescribing "naming that worktree path as its working directory" —
anchors: row5 — that instruction is inert, so PR-bound delegated work
silently inherits whatever the parent drifted to. The corrected bullet
states the *why* only and defers the *how* to branch-creation, matching
the deferral already in that bullet ("pick a slug per the
`branch-creation` skill").

Row 7 [mechanism]: marker.sh fails closed when its resolved root is the
main tree under active enforcement, and derives all values via `git -C
"$REPO_ROOT"` — anchors: root — marker.sh receives no hook payload, so
it cannot import a trusted cwd. Lighter alternatives rejected: (a) take
the repo root as an argument — supplied by the same drifted session, so
it inherits the drift; (b) key markers on `--git-common-dir` so worktree
and main tree share one key — that makes a wrong-tree write silently
consistent instead of blocked, and would let a main-tree review satisfy
a worktree gate.

Row 7a [mechanism]: marker.sh's refusal additionally requires a linked
worktree to exist on disk — anchors: row7 — the wrong-tree failure needs
a second tree: a main-tree marker is only wrong when the reviewed work
lives elsewhere, so with no worktree the marker correctly describes the
only tree there is. `require-worktree-for-file-writes.sh` denies without
this conjunct, and that precedent does not transfer: it gates a *tool
call* (PreToolUse on Edit/Write), so it can prevent the state from
arising, whereas marker.sh observes ambient git state it did not create.
[verified: require-worktree-for-file-writes.sh:118-120 and
require-worktree-for-git-writes.sh:286-291 both fire only on Claude Code
tool calls; neither intercepts a hand-staged edit in a terminal or
editor, a CI checkout, or content staged before the repo opted in] —
so an unconditional refusal would wedge those legitimate cases while
buying no protection, there being no second tree to confuse. Supersedes
an earlier draft of this row that justified the unconditional refusal by
asserting main-tree reviewable state was unreachable; that assertion was
false.

Row 8 [assumption]: every git call in marker.sh is bare — lines 75, 91
(twice), 145, 157, 187 [verified: grep of
claude/.claude/scripts/marker.sh] — anchors: row7

Row 9 [mechanism]: thread one resolved root through every git call in
require-code-review.sh and require-skill-review.sh, and adopt the
payload-`.cwd`-with-`$PWD`-fallback idiom for root resolution in those
two plus require-plan-review.sh — anchors: root — one cwd source shared
by marker writer and readers.

Row 10 [assumption]: that idiom is already canonical — 3 hooks use it
(require-worktree-for-git-writes.sh:99-100,
require-ready-for-review.sh:67-68, require-stow-reminder.sh:71-72)
against 3 marker hooks on bare ambient (require-code-review.sh:62,
require-plan-review.sh:77, require-skill-review.sh:66)
[verified: grep] — anchors: row9

Row 11 [assumption]: a hook payload's `.cwd` reports the shell's current
cwd rather than a pinned project root [verified: session probe — with
the session un-anchored so that shell cwd and project root differed, the
identical gated command `git add <nonexistent-path>` was ALLOWED by
require-worktree-for-git-writes.sh while the shell sat in the worktree
and DENIED while it sat in the main checkout; that hook reads `.cwd`,
and cwd was the only variable] — anchors: row9

Row 12 [assumption]: converting root resolution alone would be a
regression, because require-code-review.sh computes its marker hash from
bare `git diff --cached` (lines 70, 85) and require-skill-review.sh from
bare git calls at lines 74, 81, 122, 150, 185 — root from payload plus
hash from ambient is a new inconsistency, worse than today's
consistently-wrong pair. require-plan-review.sh is exempt: it delegates
to `_lib_active_plan_hash`, which already threads `-C "$repo_root"`
(_lib.sh:254, 260, 268) [verified: file read] — anchors: row9

Row 13 [mechanism]: UserPromptSubmit detector for main-tree cwd while a
linked worktree exists under active enforcement — anchors: root —
backstop for "never anchored", which row 1 cannot self-enforce. Lighter
alternatives rejected: (a) no detector, relying on row 1 alone — row 1
is a prose instruction, and the sampled incidents are precisely the case
where a prose anchoring rule was not followed; (b) fold the check into
the existing SessionStart check-branch-divergence.sh — it fires once at
startup, before the worktree for this task exists, so it cannot observe
the condition it needs to report.

Row 14 [assumption]: UserPromptSubmit rather than PostToolUse|Bash is a
cadence judgment, not a capability one — PostToolUse does support
hookSpecificOutput.additionalContext per the official hooks
documentation. The reason to prefer UserPromptSubmit is that once row 1
lands, mid-turn drift self-corrects, leaving only the turn-start "never
anchored" condition; a per-Bash-call hook would pay a process spawn on
every call to watch a condition that changes at most once per turn
[verified: https://code.claude.com/docs/en/hooks] — anchors: row13

Row 15 [assumption]: anchor state is NOT monotonic — a session can enter
a worktree mid-session and later drift back — so the one-shot
per-session dedup used by nudge-handoff-near-context-cap.sh and
nudge-error-mode-analysis.sh would wrongly suppress a genuine second
occurrence. The detector must re-arm on state change instead
[verified: read of both nudge hooks] — anchors: row13

Row 16 [assumption]: EnterWorktree is session-scoped, so a resumed
session must re-enter [verified: a session resumed from a handoff whose
prior session had entered this branch's worktree started in the main
checkout on the default branch — `pwd` returned the repo root and
`git branch --show-current` returned the default branch, before any
`EnterWorktree` call] — anchors: row1

Row 17 [assumption]: GH-493 proposal 4 (PreToolUse gate on
test/lint/build/package-manager runners) is deferred [engineer-verified]
— anchors: root

Row 18 [assumption]: the read-only git allowlist gap (`git branch -D`,
`git worktree remove` on the main tree) goes to a separate issue —
different invariant, ref state rather than working-tree files
[engineer-verified] — anchors: root
```

### Evidence note on the sampled incident

GH-493's "review marker written against the wrong repository" row does
not name which gate class it hit. The confirmed-vulnerable path is the
code-review class, where writer and reader share one ambient cwd and
therefore drift together into a self-consistent wrong-tree pass. The
`ready-for-review` class already reads payload `.cwd` on the reader side
only, so under drift it mismatches its own writer and most likely fails
*closed*. Do not cite the sampled incident as evidence for the
ready-for-review path.

## Critical files

### Anchor discipline (prose)

- `claude/.claude/skills/branch-creation/SKILL.md` — after the
  `git worktree add` guidance, add the `EnterWorktree{path}` step and
  state why (the revert target follows the anchor). The file already
  names `EnterWorktree` in passing at line 62; extend that rather than
  introducing the concept twice. This is the canonical home for the
  *how*; other surfaces point here.
- `claude/.claude/CLAUDE.md` — Agent Briefing, final bullet. Replace the
  inert "naming that worktree path as its working directory" clause with
  the reason (a dispatched child inherits the parent's cwd at dispatch,
  so the parent must be anchored first) and defer mechanics to
  `branch-creation`. Leave the three preceding bullets untouched.
- `claude/.claude/skills/ready-for-review/SKILL.md` — add **one dense
  single-line bullet** to §1 *Preconditions (halt on fail)* (line 32),
  not §2. Anchoring is a state precondition, and §1 already halts on
  fail. **Hard constraint:** `check-skill-length.sh` denies `git commit`
  when a skill file exceeds 200 lines *and* grew; this file is at 194,
  so the edit must land at ≤200 lines total. Match the one-line density
  of the existing sync-check bullet at line 40.
- `claude/.claude/skills/handoff/SKILL.md` — §4's header line (line 74)
  prescribes "working directory + current git branch" with no derivation
  recipe. Specify deriving it from the worktree and instruct the resumed
  session to re-enter (row 16, now verified).

Durable prose must not carry this work's investigative framing — no
"verified this session", no Claude Code version numbers, no "used to
be X". That rationale belongs in the commit message and PR body.

### Marker identity (code)

- `claude/.claude/scripts/marker.sh` — `_resolve_repo_root` (lines
  70-81) gains a fail-closed check; lines 91, 145, 157, 187 take
  `-C "$REPO_ROOT"`. The denial must name the recovery action (re-enter
  the worktree at the named path and retry), because this fires late —
  after the reviewed artifact already exists — and a dead-end denial is
  what pushes a session toward disabling enforcement outright. The file
  uses `set -u` without `-e`, so the check must `exit` explicitly.
  Condition: enforcement active, **plus** main tree, **plus** at least one
  linked worktree present on disk. That third conjunct is deliberate and
  departs from `require-worktree-for-file-writes.sh`, which denies without
  it — see row 7a for why the precedent does not transfer.
- `claude/.claude/hooks/require-code-review.sh` — payload-`.cwd` idiom at
  line 62 **and** `-C "$REPO_ROOT"` on the bare git calls at lines 70
  and 85. Converting only line 62 is a regression (row 12).
- `plugins/skill-management/hooks/require-skill-review.sh` — same
  treatment at line 66 plus the bare calls at lines 74, 81, 122, 150,
  185.
- `claude/.claude/hooks/require-plan-review.sh` — line 77 only;
  downstream hashing already threads the root (row 12).
- `plugins/skill-management/.claude-plugin/plugin.json` — **version bump
  required.** Editing a file under a plugin directory triggers
  `require-plugin-version-bump.sh`, which blocks `git commit` until the
  version is strictly raised. Run `/plugin-semver`.

**Merge ordering:** `marker.sh` must land with or before the reader
changes. Readers converted first, against an un-converted writer, can
disagree by two different mechanisms. Shipping all of them in one commit
satisfies this — `git pull` is atomic per commit, so no consumer observes
a half-converted state.

**Activation cadence is not uniform, though, and one reader lags.**
`marker.sh` and the two stowed readers go live on `git pull`;
`require-skill-review.sh` ships inside the `skill-management` plugin and
does not activate until that plugin is updated. So a consumer can run a
converted writer against an un-converted skill-review reader. This is
safe rather than merely tolerable, and for a reason worth stating: the
writer-side change dominates. A drifted session cannot produce a
wrong-tree marker for *any* skill once the shared `marker.sh` refuses to
write one, regardless of which reader version is installed. Non-drifted
sessions are unaffected either way, since ambient cwd and payload `.cwd`
agree. The plugin conversion is therefore defense-in-depth that lands on
its own schedule, not a half of a pair that must land together.

### Detector (new)

- `claude/.claude/hooks/nudge-worktree-anchor.sh` (new) — named for the
  existing `nudge-*` family (two UserPromptSubmit advisories); this repo
  has no `warn-*` hooks. Required properties: **exit 0 on every path**
  (a non-zero exit on `UserPromptSubmit` risks disrupting prompt
  submission), fail-open and silent on any error (missing `jq`, non-repo
  cwd, detached HEAD, bare repo), and every git call bounded by
  `timeout` — this fires on the per-turn hot path, unlike the
  SessionStart template it borrows from, so an unbounded hang would
  stall every prompt for the session's lifetime. Emits
  `hookSpecificOutput.additionalContext` only when enforcement is
  active, cwd resolves to the main tree, and a linked worktree exists.
  Re-arms on state change rather than firing once per session (row 15).
- `claude/.claude/settings.json` — register as a third `UserPromptSubmit`
  entry (no matcher key, matching the existing two).

Do not copy `require-worktree-for-git-writes.sh`'s unresolvable-git-state
branch: that hook denies, which is correct for a gate and wrong for an
advisory.

### Reuse opportunities

- `_lib_worktree_enforcement_active` (`claude/.claude/hooks/_lib.sh`
  :422-436) — the three-marker opt-in check, for both `marker.sh`
  validation and the new hook. Do not re-derive. Its gating is what
  confines blast radius: a repo that never opted in sees no change.
- The `--absolute-git-dir` vs `--git-common-dir` comparison at
  `require-worktree-for-git-writes.sh:129-140` — the existing
  linked-worktree test. Reuse verbatim; do not invent a path-prefix
  check against `.claude/worktrees/`.
- `_marker_lib_repo_hash` (`_lib.sh:106-108`) — repo-hash derivation.
- `check-branch-divergence.sh` — template for a quiet-on-success
  advisory hook and its `jq`-built payload (line 121). Note it uses
  `set -uo pipefail` **without `-e`** deliberately, relying on explicit
  emptiness checks after unguarded git calls; copy that reasoning, not
  just the syntax. Decide once whether the new hook emits via `_lib_jq`
  (timeout-guarded) or a bare `jq -n ... || true` as the template does.
- `claude/.claude/tests/helpers.py` — `run_hook`, `run_hook_advisory`,
  `bash_input`. `bash_input` emits no `.cwd` key; adding one is a
  net-new helper change, not a copy of an existing pattern — the only
  precedent (`test_require_ready_for_review.py:606-630`) hand-rolls the
  payload dict.
- `opted_in_with_worktree` fixture (`hooks/tests/conftest.py`) — already
  builds a real linked worktree via `git worktree add`.

## Verification

Run from the worktree (the contributor `.venv` lives at the main
worktree root only, three levels up):

```bash
../../../.venv/bin/pytest claude/.claude/
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

**Existing-suite expectation.** No payload constructed by
`test_require_code_review.py`, `test_require_plan_review.py`, or
`test_require_skill_review.py` sets a `.cwd` key, and every `run_hook`
call passes the target repo as subprocess cwd — so the `$PWD` fallback
resolves to exactly today's value and these suites should stay green.
Re-run them explicitly as the regression gate rather than inferring
this; if any goes red, the conversion changed more than root resolution.

New coverage:

1. **marker.sh fail-closed states** — five cases, not two: main tree with
   a worktree present (deny); invoked from the linked worktree (allow,
   and `REPO_HASH` matches the worktree path's hash); **opted-in repo
   with no worktree at all** (allow — this is the case that would
   otherwise wedge every solo main-tree repo, per row 7a); enforcement
   inactive (no-op regardless of tree); invoked from a *subdirectory* of
   each tree. Also assert `clear-stale` is unaffected, so a later
   refactor cannot route it through `_resolve_repo_root`. Add the
   transition case too — same repo, same main-tree cwd, allowed before a
   worktree exists and denied after `git worktree add` — since that is
   the boundary the third conjunct draws.
2. **marker.sh single-invocation consistency** — one `marker.sh`
   subprocess with `cwd=<worktree>`; assert every derived value
   (`REPO_HASH`, recorded HEAD, staged-diff hash) reflects worktree
   state and none leaks main-tree state. This is what catches a missed
   `-C` conversion at one line while others are fixed.
3. **Payload `.cwd` preference** — for each converted hook, run with
   subprocess cwd in an unrelated temp dir and payload `.cwd` at the
   fixture repo; assert the hook keys off the payload. Scope note: this
   pins the *hook's* contract given a payload. It does not re-verify
   row 11 — that was settled empirically against the live harness, and
   no hand-constructed payload could distinguish the hypotheses.
4. **Reader/writer agreement** — in the non-drifted case, assert payload
   `.cwd` and marker.sh's resolved root produce the same `REPO_HASH`, so
   a future change to `.cwd` semantics fails a test rather than silently
   restoring the wrong-tree pass.
5. **Detector** — silent when cwd is a linked worktree; silent when
   enforcement is inactive; silent when no linked worktree exists;
   silent when a recorded worktree path no longer exists on disk;
   emits `additionalContext` only in the main-tree-plus-live-worktree
   case; exits 0 in all of them; re-fires after an anchored→unanchored
   transition.
6. **Skill-body fixtures** — `ready-for-review/SKILL.md` carries
   `HOOK_TEST_FIXTURE` anchors at lines 25, 164 and 171 and the edit
   targets line 32; extraction matches on content, not line number, so
   the shift is safe. Run the hook-alignment suite to confirm rather
   than assume.

Manual, against the live harness (the reproduction that motivated this):
from a session anchored via `EnterWorktree`, run `cd ~/.claude/hooks`
and confirm the reset notice fires and the follow-up `pwd` returns the
worktree, and confirm a dispatched subagent's first `pwd` returns the
worktree. (Row 16's resume behavior is no longer open — see its ledger
entry.)

Review pipeline: SKILL.md edits require `/skill-review`
(hook-enforced); `claude/.claude/CLAUDE.md` requires
`/ai-instruction-and-memory-files`; the new hook and `settings.json`
entry require `/claude-hook-review`; the plugin file requires
`/plugin-semver` (hook-enforced). `/code-review` dispatches these.

## Out of scope

- **GH-493 proposal 4** — the `PreToolUse|Bash` gate denying
  test/lint/build/package-manager runs outside a worktree. It needs an
  open-ended runner allowlist, and the root fix removes its
  precondition. Revisit if incidents recur after this lands.
- **The read-only git allowlist gap** — `_lib_readonly_git_subcmds`
  keys on subcommand name, so `git branch -D` and `git worktree remove`
  run ungated on the main tree. Different invariant (ref state, not
  working-tree files). File as its own issue referencing GH-493.
- **Machine-level enforcement ergonomics** — a user who sets
  `~/.claude/worktree-required` as a personal default inherits
  fail-closed `marker.sh` in every repo they clone until they add
  `.claude/worktree-optout`. This mirrors the existing cost of the two
  worktree hooks rather than adding a new one; noted so a reader does
  not mistake it for a net-new restriction.
- **`isolation: "worktree"` auto-anchoring** — the CLAUDE.md bullet
  asserting the harness sets a subagent's cwd automatically for isolated
  agents was not re-verified; it describes a different code path from
  the bullet this plan corrects. Left untouched.
- **Other bare `git rev-parse --show-toplevel` sites** —
  `check-claude-md-length.sh:52`, `check-skill-length.sh:48`,
  `deny-private-project-refs.sh:265`,
  `require-plugin-version-bump.sh:74`, `require-npm-version-bump.sh:110`
  resolve a repo root for path resolution rather than for marker
  keying. Same shape, different blast radius; noted, not changed.
