# Fix handoff skill's conversion-signal recipe for worktree-isolated sessions

## Context

The `handoff` skill's "After writing: record the conversion signal" step is
refused by the Bash tool whenever it runs from a worktree-isolated session —
which, in this repo, is every session, since worktree discipline is active
(`.claude/worktree-required`). A prior session hit this, misdiagnosed it as
a repo-contamination risk from the stow-symlinked log target, and skipped
the step rather than working around it. The fix restructures the recipe
into a shape the Bash tool accepts unconditionally, and tightens the
CLAUDE.md rule that contributed to the misdiagnosis.

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
| `head -n1 "$CONFIG_DIR/sessions/$PPID" 2>/dev/null` alone (single statement, no assignment) | succeeded |
| `printf 'text\n' >> "$HOME/.claude/.handoff-nudge.log"` alone (single statement, no `$(...)`) | succeeded |
| `test -n "x" && printf 'text\n' >> file` (single statement, conditional, no `$(...)`) | succeeded |

The trigger is a `$(...)` command substitution assigned to a variable,
combined with a later statement in the *same* Bash tool call — not the
conditional, not the redirect alone, not the stow-symlinked target. A
single command using `$(...)` with no further statement, or a multi-statement
command with no such assignment, both pass.

I also confirmed the prior session's stated risk (repo contamination) was
never real for this specific target: `claude/.claude/.handoff-nudge.log` is
listed in `.gitignore:114`, so an append through the symlink cannot enter
`git status` — the CLAUDE.md footgun rule the prior session cited governs
git-tracked stow-symlink targets, not this one.

### Fix

Split the recipe into two separate, single-statement Bash fences: read the
session id (no assignment, no later statement in the same call), then —
only if that printed something — append a literal-inlined line (no `$(...)`
in that call at all). Neither half matches the refused shape; both were
verified directly this session (table above).

**Alternatives considered and rejected:**
- Drop the `[ -n "$SESSION_ID" ]` guard but keep one fence — still combines
  the assignment with a later statement, which the bisection above shows is
  the actual trigger, independent of the conditional. Doesn't fix it.
- Nest the append inside the substitution itself (e.g. pipe through `xargs`
  into `printf ... >>`) — a more exotic single-call shape with no
  session-time way to confirm it avoids the guard, trading a demonstrated
  fix for an unverified guess.

### Assumption ledger

**Root problem:** the handoff skill's conversion-signal recipe is refused by
the Bash tool's worktree-isolation guard when a session is anchored in a
worktree, because the recipe combines a `$(...)`-assigned variable with a
later statement in one call.

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
1. Split `handoff/SKILL.md`'s recipe into two single-statement fences.
   anchors: root. [verified: session reproduction table above — the exact
   original recipe fails; each half, run standalone, succeeds]
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
   false-positives.
3. Tighten the stow-symlink footgun rule in repo-root `CLAUDE.md` to scope
   its "silently stages changes to the public repo" warning to git-tracked
   targets. anchors: root. [verified: `.gitignore:114` lists
   `claude/.claude/.handoff-nudge.log` — a gitignored path structurally
   cannot appear in `git status`, so the rule's actual risk cannot occur for
   this target] [engineer-verified: user opted to include this scope
   expansion in the plan's clarifying questions]

**Other assumptions:**
- A1. No other skill currently contains this bug shape. [verified: a
  `general-purpose` subagent this session swept every fenced bash/sh block
  under `claude/.claude/skills/`, `claude/.claude/CLAUDE.md`,
  `claude/.claude/rules/`, and `docs/` for the three-part shape (command
  substitution + file redirect + multi-statement/conditional); the only
  match beyond handoff's known recipe is a human-run maintenance recipe in
  `docs/permission-prompt-tracking.md:39-48` that an agent session never
  executes as part of a skill flow — out of scope, noted below]
- A2. The two-step split reliably avoids the refusal against future harness
  versions. [unverified] — inferred from one session's empirical testing
  against the current harness build; if the underlying guard heuristic
  changes, this could regress silently since no test in this repo can
  invoke the harness's own Bash-tool classifier. The regression test (row 2)
  guards against the *shape* reappearing, not against the harness redefining
  what shape it refuses.

## Critical files

- `claude/.claude/skills/handoff/SKILL.md` — replace the single compound
  recipe in "After writing: record the conversion signal" (current lines
  145-160) with two single-statement fences: one to read the session id,
  one to append a literal-inlined line. Drafted replacement (reviewed via
  `skill-management:skill-review` against this exact text this session —
  clean, no findings):

  ````
  ## After writing: record the conversion signal

  Once the handoff file is written and verified, append one line recording this session's id to
  `nudge-handoff-near-context-cap.sh`'s own log — pairing it with that hook's `nudged` lines lets a
  future report count how often a nudge fire is followed by a handoff in the same session, without
  joining to transcript content. Run this as two separate, single-purpose Bash calls, not one
  combined script: a worktree-isolated session's Bash tool refuses a command that both assigns a
  `$(...)` result to a variable and acts on it in the same call.

  Read the session id:

  ```bash
  head -n1 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions/$PPID" 2>/dev/null
  ```

  If that printed a session id, append it as a literal (substitute the printed value for
  `<session-id>` — do not capture it into a shell variable first):

  ```bash
  printf 'handoff session=<session-id>\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.handoff-nudge.log"
  ```

  Best-effort: `sessions/$PPID` is the session-id lookup file `capture-session-id.sh` writes at
  session start; if the first command printed nothing, skip the second — this is a conversion
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
- Re-run both halves of the new recipe as literal, separate Bash tool calls
  from within this worktree-isolated session to confirm neither is refused
  — already done this session as part of root-cause verification (see the
  reproduction table above); re-run once more against the final fenced-block
  wording in the committed `SKILL.md` to catch any transcription drift
  between the plan's prose and the actual file.
- `/skill-review` on the `handoff/SKILL.md` diff (this repo's
  `skill-and-agent-self-review` rule requires it before staging any skill
  edit) and `/code-review` before commit, which routes to `/skill-review`
  automatically for the SKILL.md change and to
  `ai-instruction-and-memory-files` for the CLAUDE.md change.

## Out of scope

- Fixing or documenting the Claude Code harness's own Bash-tool
  worktree-isolation guard — it is harness-owned, not this repo's code, and
  the workaround (recipe shape) is the only lever this repo has.
- `docs/permission-prompt-tracking.md`'s human-run log-trimming recipe,
  which shares the refused shape but is documentation for a human operator
  running it manually, not a step any agent session executes as part of a
  skill flow.
