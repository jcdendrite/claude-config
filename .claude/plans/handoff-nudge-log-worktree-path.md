# Fix handoff skill's conversion-signal recipe for worktree-isolated sessions

## Context

The `handoff` skill's "After writing: record the conversion signal" step is
refused by the Bash tool whenever it runs from a worktree-isolated session —
which, in this repo, is every session, since worktree discipline is active
(`.claude/worktree-required`). A prior session hit this, misdiagnosed it as
a repo-contamination risk from the stow-symlinked log target, and skipped
the step rather than working around it. The fix restructures the recipe so
the Bash tool accepts it in the common case (fully solving the reported
failure), with a documented, best-effort fallback for the rest, and tightens
the CLAUDE.md rule that contributed to the misdiagnosis.

## Approach

### Root cause (re-derived this session, not assumed from the report)

The prior session's own narrative attributed the failure to the worktree
*file-write* gate refusing a write that lands outside the worktree via a
stow symlink. That is not what happened. `require-worktree-for-file-writes.sh`
only matches `Edit`/`Write`/`MultiEdit` tool calls and explicitly exempts
every path under `$HOME/.claude/` (`claude/.claude/hooks/require-worktree-for-file-writes.sh:59-93`)
— a Bash `>>` never reaches it. `require-worktree-for-git-writes.sh` only
evaluates commands containing a `git` word (`claude/.claude/hooks/require-worktree-for-git-writes.sh:110-112`)
— the recipe has none. Grepping every hook script in this repo for the
actual refusal text ("too complex to verify", "isolated in the worktree")
returns zero matches. The refusal is a harness-native Bash-tool guard,
external to this repo's own hooks, that fires on *any* command — not just
git — when the session is anchored in a worktree.

I reproduced the refusal directly this session (worktree
`handoff-nudge-log-worktree-path`) by running the skill's exact recipe
verbatim, then bisected it by re-running variants as single Bash tool calls:

| Command shape | Result |
|---|---|
| `CONFIG_DIR=...`; `SESSION_ID=$(head -n1 ...)`; `[ -n "$SESSION_ID" ] && printf ... >> "$CONFIG_DIR/..."` (the skill's current recipe, verbatim) | refused |
| `VAR=$(echo hi)` then a second statement using `$VAR`, redirect target literal (not a variable) | refused |
| `head -n1 "$HOME/.claude/sessions/$PPID" 2>/dev/null` alone (single statement, no assignment) | succeeded |
| `printf 'text\n' >> "$HOME/.claude/.handoff-nudge.log"` alone (single statement, no `$(...)`) | succeeded |
| `test -n "x" && printf 'text\n' >> file` (single statement, conditional, no `$(...)`) | succeeded |
| `MY_TEST_VAR="probe"; echo "$MY_TEST_VAR"` (multi-statement, local literal assignment, no `$(...)`) | succeeded |
| `echo "$CLAUDE_CONFIG_DIR"` alone — bare reference, no assignment, no `$(...)`, no redirect | **refused** |
| `head -n1 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions/$PPID" 2>/dev/null` alone | **refused** |
| `printf 'x\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge.log"` alone | **refused** |

Two independent triggers, not one:

- **Trigger A** — a `$(...)` command substitution assigned to a variable,
  combined with a later statement in the same call. A local, literal
  (non-`$(...)`) assignment followed by a later statement is fine (row 6).
- **Trigger B** — any reference to `$CLAUDE_CONFIG_DIR` specifically,
  regardless of how simple the rest of the command is — even a bare,
  read-only `echo`. `$HOME` and `$PPID` are unaffected (rows 3-4, 6); only
  `$CLAUDE_CONFIG_DIR` triggers this. I found Trigger B only after the plan
  below had already been through one `/plan-review` pass and one commit —
  the originally-approved fix used `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` in
  both replacement commands, which Trigger B refuses outright. This
  revision corrects that (see Fix, below) before implementation starts.

I also confirmed the prior session's stated risk (repo contamination) was
never real for this specific target: `claude/.claude/.handoff-nudge.log` is
listed in `.gitignore:114`, so an append through the symlink cannot enter
`git status` — the CLAUDE.md footgun rule the prior session cited governs
git-tracked stow-symlink targets, not this one.

### Fix

Split the recipe into two separate, single-statement Bash fences (fixes
Trigger A: read the session id with no assignment and no later statement in
the same call, then — only if that printed something — append a
literal-inlined line with no `$(...)` in that call at all). For Trigger B,
try the `$CLAUDE_CONFIG_DIR`-aware form of each command first; if the Bash
tool refuses it citing worktree isolation, retry the same step with the
plain `$HOME/.claude` fallback in place of `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`.
This fully fixes the reported failure for the common case (`CLAUDE_CONFIG_DIR`
unset, which includes this session's own environment and is the default for
most stow users) and accepts one narrower residual gap: a session with
`CLAUDE_CONFIG_DIR` set to a non-default location, running inside a
worktree-isolated repo, still can't record the signal to its custom log
location — best-effort telemetry degrading to a no-op in a narrow
intersection, not a regression from today's fully-broken state.

**Alternatives considered and rejected:**
- Drop the `[ -n "$SESSION_ID" ]` guard but keep one fence — still combines
  the assignment with a later statement, which the bisection above shows is
  Trigger A, independent of the conditional. Doesn't fix Trigger A, and
  doesn't touch Trigger B at all.
- Nest the append inside the substitution itself (e.g. pipe through `xargs`
  into `printf ... >>`) — a more exotic single-call shape with no
  session-time way to confirm it avoids Trigger A, trading a demonstrated
  fix for an unverified guess; also still references `$CLAUDE_CONFIG_DIR`
  directly, so Trigger B would still refuse it.
- Hardcode `$HOME/.claude` everywhere and drop `$CLAUDE_CONFIG_DIR` support
  from this recipe entirely — simpler, and sidesteps Trigger B completely,
  but silently misdirects the signal for any `CLAUDE_CONFIG_DIR` user
  *outside* a worktree-isolated repo too (the common case where Trigger B
  isn't even active and the `$CLAUDE_CONFIG_DIR`-aware form would have
  worked fine). `nudge-handoff-near-context-cap.sh` itself resolves its log
  path via `_lib_config_dir()` (honoring `$CLAUDE_CONFIG_DIR`), so a
  hardcoded fallback would send this recipe's `handoff session=` line to a
  different file than the `nudged` lines it exists to pair with for any such
  user — breaking the join this section's own stated purpose depends on,
  not just narrowing an edge case.
- Move the whole recipe into a new dedicated script (mirroring how
  `marker.sh`'s `_walk_session` resolves the Bash-tool's `$PPID`-depth
  difference and keeps all `$CLAUDE_CONFIG_DIR` handling inside the script
  file, invisible to the harness guard) — this would close the residual gap
  completely with no fallback branching. Rejected as heavier than this
  best-effort, non-gating, three-line step warrants: it either duplicates
  `marker.sh`'s process-ancestor-walk logic in a new file (a second copy of
  logic this repo already has once, drifting under its own maintenance) or
  couples a telemetry-log script to `marker.sh`'s review-gate-marker
  internals, which isn't its documented purpose. The fallback-prose fix
  above resolves the actually-reported failure at a fraction of the
  maintenance surface; revisit with a dedicated script only if the residual
  `CLAUDE_CONFIG_DIR`-plus-worktree-isolation gap turns out to matter in
  practice.

### Assumption ledger

**Root problem:** the handoff skill's conversion-signal recipe is refused by
the Bash tool's worktree-isolation guard when a session is anchored in a
worktree, for either of two independent reasons: the recipe combines a
`$(...)`-assigned variable with a later statement in one call (Trigger A),
or it references `$CLAUDE_CONFIG_DIR` at all, however simply (Trigger B).

**Givens:**
- G1. The refusal originates in the Claude Code harness's own Bash-tool
  guard, not in any script this repo owns. Given because: this repo cannot
  patch harness source; the only lever available is the recipe's shape.
  [verified: grepped `claude/.claude/hooks/*.sh` and `*.py` for the exact
  refusal text — zero matches; both repo-owned worktree hooks are scoped to
  Edit/Write/MultiEdit or to commands containing a `git` word, neither of
  which this recipe is]
- G2. The guard is not scoped to this repo — it fires for any session
  anchored via worktree isolation, regardless of which repo's hooks are
  active. Given because: `handoff` is a globally-stowed skill used across
  every repo the user works in, so a repo-local workaround (e.g. relaxing
  this repo's own worktree enforcement) wouldn't fix the skill anywhere else
  worktree isolation is used. [engineer-verified: user confirmed this
  session that the fix should be a general skill-shape fix, not scoped to
  this repo, via the plan's clarifying questions]

**Per mechanism:**
1. Split `handoff/SKILL.md`'s recipe into two single-statement fences, each
   with a `$CLAUDE_CONFIG_DIR`-aware primary form and a plain-`$HOME/.claude`
   fallback. anchors: root. [verified: session reproduction table above —
   the exact original recipe fails on both Trigger A and Trigger B; each
   fallback form, run standalone, succeeds]
2. Add a cross-skill regression test in `test_skills.py` flagging any
   SKILL.md fenced bash block that assigns a variable via `$(...)` and uses
   it in a later statement in the same fence. anchors: row1.
   [engineer-verified: user opted for this over a prose-only fix in the
   plan's clarifying questions] A full shell-AST parser mirroring
   `parse-git-command.py`'s tokenizer was considered and rejected as
   disproportionate — that tokenizer exists to safely judge git write
   *effective cwd* across cd/-C chains, a materially harder problem than
   flagging one narrow assignment-then-use shape; a line-based regex
   heuristic is sufficient here and cheap to special-case if it ever
   false-positives. **Scoped to Trigger A only, deliberately** — a test
   flagging any `$CLAUDE_CONFIG_DIR` reference in a skill fence would
   false-positive on this very fix's own primary form (and on every other
   skill's legitimate, correct use of `$CLAUDE_CONFIG_DIR` outside a
   worktree-isolated session, which is the common case); Trigger B has no
   syntactic shape to flag that doesn't also flag correct code.
3. Tighten the stow-symlink footgun rule in repo-root `CLAUDE.md` to scope
   its "silently stages changes to the public repo" warning to git-tracked
   targets. anchors: root. [verified: `.gitignore:114` lists
   `claude/.claude/.handoff-nudge.log` — a gitignored path structurally
   cannot appear in `git status`, so the rule's actual risk cannot occur for
   this target] [engineer-verified: user opted to include this scope
   expansion in the plan's clarifying questions]

**Other assumptions:**
- A1. No other skill currently contains Trigger A's bug shape.
  [verified: a `general-purpose` subagent this session swept every fenced
  bash/sh block under `claude/.claude/skills/`, `claude/.claude/CLAUDE.md`,
  `claude/.claude/rules/`, and `docs/` for the three-part shape (command
  substitution + file redirect + multi-statement/conditional); the only
  match beyond handoff's known recipe is a human-run maintenance recipe in
  `docs/permission-prompt-tracking.md:39-48` that an agent session never
  executes as part of a skill flow — out of scope, noted below]. Separately,
  no other skill's `SKILL.md` body references `$CLAUDE_CONFIG_DIR` or
  `sessions/$PPID` at all (Trigger B's shape): [verified: `grep -rn
  "sessions/\$PPID\|CLAUDE_CONFIG_DIR" claude/.claude/skills/*/SKILL.md`
  matches only `handoff/SKILL.md:153` — `respond-pr`, the other skill that
  needs this session's id, resolves it via `~/.claude/scripts/marker.sh
  activate respond-pr` instead of inline Bash, so it never exposes either
  token to the harness guard in the first place].
- A2. The two-step split with `$CLAUDE_CONFIG_DIR`-aware-then-`$HOME`-fallback
  forms reliably avoids the refusal against future harness versions.
  [unverified] — inferred from one session's empirical testing against the
  current harness build; if the underlying guard heuristic changes, this
  could regress silently since no test in this repo can invoke the harness's
  own Bash-tool classifier. The regression test (row 2) guards against
  Trigger A's *shape* reappearing, not against the harness redefining
  what shape it refuses, and does not (and, per row 2's note, should not)
  guard against Trigger B at all.
- A3. The residual gap (a `CLAUDE_CONFIG_DIR`-customized session running
  inside a worktree-isolated repo still can't record the signal) is
  acceptable to ship rather than close. [assumed — flagged to the user
  alongside this revision, not yet explicitly confirmed] — this is a
  narrower intersection than "worktree-isolated" alone (today's
  fully-broken condition), degrades to the pre-existing silent-skip
  behavior rather than a new failure mode, and the step is documented as
  best-effort telemetry, not a gate. If the user wants the gap closed
  instead, the dedicated-script alternative in the Fix section's rejected
  list is the fallback design.

## Critical files

- `claude/.claude/skills/handoff/SKILL.md` — replace the single compound
  recipe in "After writing: record the conversion signal" (current lines
  145-160) with two single-statement steps, each with a `$CLAUDE_CONFIG_DIR`-
  aware primary form and a plain-`$HOME/.claude` fallback for when the
  harness's worktree-isolation guard refuses the primary form. Drafted
  replacement (**revised** after this plan's first `/plan-review` pass and
  commit — see Approach's Trigger B finding; the version below, not the
  originally-committed one, is what implementation should build; re-review
  via `skill-management:skill-review` still pending as of this revision,
  see Verification):

  ````
  ## After writing: record the conversion signal

  Once the handoff file is written and verified, append one line recording this session's id to
  `nudge-handoff-near-context-cap.sh`'s own log — pairing it with that hook's `nudged` lines lets a
  future report count how often a nudge fire is followed by a handoff in the same session, without
  joining to transcript content. Run each step below as its own single-purpose Bash call, not a
  combined script: a worktree-isolated session's Bash tool refuses any command that assigns a
  `$(...)` result to a variable and acts on it in the same call, and separately refuses any command
  that references `$CLAUDE_CONFIG_DIR` at all — even a bare, read-only reference.

  Read the session id:

  ```bash
  head -n1 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions/$PPID" 2>/dev/null
  ```

  If the Bash tool refuses that command citing worktree isolation, retry with the plain fallback
  (only the default `$HOME/.claude` location is reachable from a worktree-isolated session — accept
  the narrower gap for a `CLAUDE_CONFIG_DIR`-customized setup):

  ```bash
  head -n1 "$HOME/.claude/sessions/$PPID" 2>/dev/null
  ```

  If either form printed a session id, append it as a literal (substitute the printed value for
  `<session-id>` — do not capture it into a shell variable first), using whichever config-dir form
  the read step above actually succeeded with:

  ```bash
  printf 'handoff session=<session-id>\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge.log"
  ```
  or, if the fallback read was needed:
  ```bash
  printf 'handoff session=<session-id>\n' >> "$HOME/.claude/.handoff-nudge.log"
  ```

  Best-effort: `sessions/$PPID` is the session-id lookup file `capture-session-id.sh` writes at
  session start; if neither read attempt printed anything, skip the append — this is a conversion
  metric, not a gate.
  ````

- `CLAUDE.md` (repo root) — tighten the "Footgun: never recommend `>>`
  writes through stow-symlinked files" rule (lines 47-51) to scope the
  "silently stages changes to the public repo" warning to git-tracked
  targets. Drafted replacement (reviewed via `ai-instruction-and-memory-files`
  against this exact text this session — clean, no findings):

  ```
  **Footgun: never recommend `>>` writes through stow-symlinked files pointing
  at a git-tracked target.**
  Files under `~/.claude/` (e.g. `~/.claude/CLAUDE.md`) are symlinks to
  tracked files in this repo — appending via `>>` writes through the
  symlink and silently stages changes to the public repo. Edit the
  committed file directly via PR instead. This does not cover gitignored
  runtime state under `claude/.claude/` (e.g. `.handoff-nudge.log`) — an
  append there never reaches `git status`, so check `.gitignore` before
  assuming this rule blocks a specific write.
  ```

- `claude/.claude/skills/tests/test_skills.py` — add a module-level test
  (near `_all_skill_md_paths()` at line 1261, following the style of
  `test_disposition_rule_anchors_present()` immediately below it) that
  extracts every fenced bash/sh block from every SKILL.md (stowed +
  plugins) and flags any block assigning a variable via `$(...)` that is
  followed by another non-comment statement in the same block.
  **Reuse:** `_all_skill_md_paths()` already enumerates every SKILL.md
  (stowed and plugin) — call it directly rather than re-deriving the skill
  list.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` from the worktree — the new
  regression test must pass against the fixed `handoff/SKILL.md`, and (as a
  one-time local sanity check before committing, not a committed step) must
  fail if the old compound recipe is pasted back in.
- `../../../.venv/bin/ruff check claude/.claude/` — lint, unaffected by this
  change but part of the repo's standard check.
- Re-run all four command forms (primary + fallback, for both the read and
  the append step) as literal, separate Bash tool calls from within this
  worktree-isolated session to confirm each behaves as the reproduction
  table predicts — the primary (`$CLAUDE_CONFIG_DIR`-aware) forms already
  confirmed refused, the fallback (`$HOME/.claude`-only) forms already
  confirmed to succeed, both this session (see the reproduction table
  above); re-run once more against the final fenced-block wording in the
  committed `SKILL.md` to catch any transcription drift between the plan's
  prose and the actual file.
- `/skill-review` on the `handoff/SKILL.md` diff (this repo's
  `skill-and-agent-self-review` rule requires it before staging any skill
  edit) and `/code-review` before commit, which routes to `/skill-review`
  automatically for the SKILL.md change and to
  `ai-instruction-and-memory-files` for the CLAUDE.md change. **Note:** the
  `skill-review` pass already run against this section's drafted text
  (recorded in Critical files) covered the pre-revision, `$(...)`-fallback-
  free version — it must run again against the fallback-form text above
  before implementation is treated as reviewed.

## Out of scope

- Fixing or documenting the Claude Code harness's own Bash-tool
  worktree-isolation guard — it is harness-owned, not this repo's code, and
  the workaround (recipe shape) is the only lever this repo has.
- `docs/permission-prompt-tracking.md`'s human-run log-trimming recipe,
  which shares the refused shape but is documentation for a human operator
  running it manually, not a step any agent session executes as part of a
  skill flow.

## Postscript: the recipe-restructuring fix in this plan was reverted

Before finalizing implementation, re-testing the exact original recipe (and
the simplest Trigger A/B cases) in the implementing session found the
refusal no longer reproduces. The Claude Code changelog confirms why:
2.1.224 (shipped after this plan's bisection, before the PR opened) fixed
"worktree-isolated sessions and their subagents being able to run
destructive git commands against the main checkout; isolation now applies
to file edits and Bash in every session type" — the buggy, overly-broad
check this plan's Trigger A/B bisection actually hit. The shipped diff
reverts the recipe restructuring in `handoff/SKILL.md`,
`ready-for-review/SKILL.md`, and `pr-description/SKILL.md` back to this
plan's pre-fix originals, replacing it with a one-sentence historical note
in each. The `CLAUDE.md` footgun-rule scoping (mechanism 3) is independent
of the Bash-tool refusal and still shipped as designed. The bisection
table and root-cause analysis above remain an accurate record of what was
investigated and found at the time — only the fix that followed from it
didn't hold up once the platform bug it targeted was found already fixed.
