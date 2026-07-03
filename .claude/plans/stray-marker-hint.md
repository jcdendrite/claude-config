# Evaluate: is #427 obsoleted by #435? (No) + minimal discoverability fix

## Context

**Question asked:** Does PR #435 make issue #427 obsolete?

**Verdict: No — the hypothesis is incorrect.** #427 is a still-open concern
that #435 does not touch. The two are mirror-image guards on different file
paths and different mechanisms; neither subsumes the other.

**But the originally-proposed fix for #427 is not worth doing** (red-hat
conclusion below). Instead of changing the enforcement *logic*, this plan makes
one small *discoverability* improvement to the deny message, which is where the
real cost in #427's originating transcript actually landed.

### Why #435 does not obsolete #427

| | Issue #427 | PR #435 (merged) |
|---|---|---|
| **File path** | `<repo>/.claude/worktree-required` (repo-root **sentinel** the hook *reads*) | `claude/.claude/worktree-required` + `.error-mode-nudge-enabled` (stow-**source** copies → `~/.claude/`) |
| **Direction** | Read/activation: an *untracked* marker should not silently activate enforcement | Write/distribution: the marker should not be *committed* and shipped to stow users |
| **Mechanism** | Hook marker test is existence-based (`[ -f ]`, `_lib.sh:208`) | `.gitignore` entries |
| **Code touched** | none by #435 | `.gitignore` only |

Two independent reasons #435 cannot close #427:
1. **Different path, not even matched.** #435's `.gitignore` entry is
   slash-anchored to `claude/.claude/worktree-required` (the stow-source copy →
   *machine* marker at `_lib.sh:215`). #427 concerns the *repo* sentinel at
   `_lib.sh:208`. The gitignore pattern does not match that path.
2. **Different mechanism.** `.gitignore` does not stop a file from existing on
   disk, and the hook keys on existence. Gitignoring could never change the
   hook's behavior.

Both hooks route through the single `_lib_worktree_enforcement_active`
(`require-worktree-for-git-writes.sh:139`, `require-worktree-for-file-writes.sh:97`),
which still uses pure `[ -f ]` at `_lib.sh:208` on current `main`.

### Red-hat: why the logic fix (existence → tracked) is NOT worth doing

- **Untracked usually means "about to be tracked."** A human who runs
  `touch .claude/worktree-required` is opting in; enforcement activating
  immediately, before they commit, is desirable, not a bug.
- **The failure mode is fail-*safe*.** A stray marker makes the repo
  *over*-enforce (pushes you into a worktree you didn't need) — annoying, not
  destructive. It is self-diagnosing (`git status` shows it untracked) and
  self-healing (`rm` it or commit it). The dangerous direction (a stray file
  *disabling* enforcement) is the opt-out, which #427 is not about.
- **The fix cost is real.** Swapping `[ -f ]` for `git ls-files --error-unmatch`
  forks a git subprocess on *every* file-write and git command the hook
  inspects — a hot-path tax to convert a rare, fail-safe, self-healing annoyance
  into a rarer one. That is the "add machinery to a guard for a fail-safe edge
  case" anti-pattern.
- **No contract drift.** README/CLAUDE.md say the sentinel is "committed" to
  describe the normal setup (commit it so it travels via `git pull`), not to
  assert the hook checks tracked-ness. Nothing to reconcile.

The one genuine signal in #427's transcript was **discoverability**: the
engineer was confused about *why* enforcement was on and had to investigate
before realizing the marker was untracked. That is a message problem, not a
logic problem — and it is all this plan fixes.

## Approach

Leave `_lib.sh:208` and all enforcement logic **unchanged**. Add a conditional
note to the two primary "you're on the main tree and enforcement is active"
deny messages, surfacing a stray untracked marker only when one actually exists.

1. **New shared helper in `_lib.sh`:** `_lib_stray_marker_hint REPO_ROOT` —
   returns a short hint string iff `<repo>/.claude/worktree-required` exists in
   the working tree **and** is not tracked in the index
   (`git ls-files --error-unmatch` fails); returns empty otherwise. Placed in
   `_lib.sh` (not a single hook) because both hooks consume it — single source
   of truth. Mirrors the existing conditional-note helper shape already in
   `require-worktree-for-git-writes.sh` (`cwd_anchor_note_if_chained`,
   `git_C_note_if_present`).

2. **Interpolate at the two primary deny sites** using the same
   `$(helper …)` idiom those files already use:
   - `require-worktree-for-git-writes.sh:230` (git subcommand not on read-only
     allowlist, main tree, enforcement active)
   - `require-worktree-for-file-writes.sh:115` (main-tree file write blocked)

Hint text (only shown when the marker is genuinely untracked):
> Note: `.claude/worktree-required` is present but **untracked** — an accidental
> stray copy activates enforcement exactly like a committed one. Commit it if
> intentional, or remove it if it was created by accident.

**Why this shape:** the helper runs only at *deny* time — after the hook has
already decided to block and already forked git several times — so it adds
**zero** cost to the allow path (every normal file write). Because it fires only
when a marker is actually untracked, it stays silent for the committed-marker
and machine-marker cases (no noise, no misleading text). This is the
discoverability win from #427 without the logic change the red-hat rejected.

### Scope note — only the two primary deny sites
The other `emit_deny` calls (`git-writes.sh:148,225`, `file-writes.sh:106`) are
parse/git-state *error* paths, a different failure mode where the stray-marker
hint is not the likely explanation. Keep the note on the two "enforcement is
active and blocking you on the main tree" messages where the "why is this even
on?" confusion actually occurs.

## Critical files
- **`claude/.claude/hooks/_lib.sh`** — add `_lib_stray_marker_hint` (~5 lines)
  near the existing helpers. **Reuse:** copy the conditional-note pattern from
  `require-worktree-for-git-writes.sh` rather than inventing a new shape.
- **`claude/.claude/hooks/require-worktree-for-git-writes.sh`** (line 230) —
  append `$(_lib_stray_marker_hint "$REPO_ROOT")` to the deny string. `REPO_ROOT`
  is in scope; the file already uses `$(…)` note helpers here.
- **`claude/.claude/hooks/require-worktree-for-file-writes.sh`** (line 115) —
  same interpolation; `REPO_ROOT` set at line 93, `_lib.sh` already sourced
  (it calls `_lib_worktree_enforcement_active` at line 97).
- **`claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`** and
  **`test_require_worktree_for_file_writes.py`** — add cases: (a) **untracked**
  marker → still **denied** (logic unchanged) **and** deny message contains the
  stray-marker hint; (b) **committed** marker (`opted_in_repo`, `conftest.py:43-55`)
  → denied **and** hint absent. **Reuse:** clone the `opted_in_repo` fixture but
  drop the commit step for a `stray_marker_repo` fixture in `conftest.py`.

## Verification
- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py`
  — new cases fail before the change (no hint emitted), pass after; all existing
  enforcement tests stay green (logic unchanged).
- `../../../.venv/bin/ruff check claude/.claude/` — clean.
- Manual: scratch git repo, `: > .claude/worktree-required` (untracked), attempt
  a main-tree git write → **denied** and the message includes the stray-marker
  hint; commit the marker → **denied**, hint gone.

## Out of scope
- **The logic change (existence → tracked-ness).** Considered and rejected —
  fail-safe, self-healing edge case not worth a per-hook git subprocess. If
  desired, close #427 noting the deny-message hint as the resolution.
- `.claude/worktree-optout` tracked-ness, the machine marker (`_lib.sh:215`),
  and anything in #435's already-merged `.gitignore` domain.
