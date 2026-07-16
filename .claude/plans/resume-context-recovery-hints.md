# Plan: Make consumed continuity-file destinations visible + recoverable

## Context

When `resume-context` (or the PostToolUse consume hook) moves a `/handoff` or
`/brief` continuity file out of `~/.claude/handoffs/` (or `briefs/`), it lands
at a **deliberately non-descriptive** temp path (`/tmp/resume-context.XXXXXX` —
the random name exists so the slug can't leak on a shared `/tmp`). Today that
move is **invisible**: launch mode `exec`s `claude` without printing the
destination, and the consume hook swallows all output (`>/dev/null 2>&1`). So
when a file is consumed — deliberately, or *accidentally* by a peek-Read that
trips the hook — and the human later runs `resume-context <original-path>`, they
get a bare `source file not found` with no way to locate the moved copy.

**Intended outcome:** every consumption announces where the file went, and a
later failed `resume-context` on the original path points the human at where
consumed files live so they can recover.

**Design note (no persistent index).** An earlier draft recorded a durable
`src → dest` breadcrumb log. We dropped it: the moved files all live in `/tmp`
under a single predictable prefix, so "where did it go" is already answerable
with `ls -t /tmp/resume-context.*` — a persistent index adds append/rewrite
machinery only to save that `ls`. Worse, any such index has to share `/tmp`'s
lifetime to stay honest (a copy in `~/.claude` would outlive the `/tmp` files it
points to and hand back stale, reboot-wiped pointers). Once the index must live
in `/tmp` to avoid lying, it's redundant with the `/tmp` files themselves. So:
announce at move-time, and make the not-found message point at the `/tmp` prefix.
No new state.

Decisions confirmed with the user: announce on **both** consumption paths
(including the silent hook), and keep the fix minimal — no index file.

## Approach

Two files change. `resume-context.sh` owns the move for both paths.

### 1. `claude/.claude/scripts/resume-context.sh`

- **`print_recovery_hint <dest>` helper** (new; keeps the reload string in one
  place per the extract-a-named-what rule). Emits to **stderr**:
  ```
  resume-context.sh: reload with: claude --append-system-prompt-file <dest>
  ```
- **Launch mode** (after the move, before `exec claude`): print the destination
  + call `print_recovery_hint "$DEST"`, both to **stderr**:
  ```
  resume-context.sh: moved <src> -> <dest>
  resume-context.sh: reload with: claude --append-system-prompt-file <dest>
  ```
  This is **best-effort UX**, not the recovery guarantee — whether it survives
  `exec` into claude's alt-screen TUI depends on the terminal (`smcup`/`rmcup`)
  and is lost under `-p`/piped invocations. That's fine: in launch mode a
  successful `exec` means you're already resuming; the print only matters if you
  later look back, and the not-found branch below is the dependable path. Frame
  it as best-effort in the header comment so a reader doesn't over-trust it.
- **Consume-only mode** (unchanged move; after it): print **only** `<dest>` to
  **stdout** (single line) so the hook can capture it; exit 0. No other stdout in
  this mode (no `set -x`, no stray output) — the hook relies on `$(...)`
  returning exactly the dest.
- **Not-found branch** (`[ ! -f "$SRC" ]`): replace the bare one-liner with a
  hint. The `TMPDIR_ROOT="${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}"` assignment
  currently sits in the launch-only section (below the not-found check); **hoist
  it above the not-found check** so a single definition feeds both the hint and
  the move (DRY — one expression, not two copies). The hint then names the
  **actual** directory in use (still `exit 1` — the requested file genuinely
  isn't there):
  ```
  resume-context.sh: source file not found: <src>
  resume-context.sh: it may already have been consumed — moved copies are at
  resume-context.sh:   <TMPDIR_ROOT>/resume-context.* (newest first: ls -t)
  resume-context.sh: those are cleared on reboot; if none remain, it's unrecoverable.
  ```
  Purely a text hint — no directory listing performed by the script, no state
  consulted. Coherent across the reboot boundary: post-reboot the glob is empty,
  which correctly tells the human nothing survives.

### 2. `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh`

- Capture the `--consume-only` call's **stdout** (the dest) while keeping stderr
  swallowed, the `timeout` wrapper, kill-switch, defense-in-depth filters, and
  the fail-open contract intact:
  `DEST=$(timeout ... "$RESUME_SCRIPT" --consume-only "$FILE_PATH" 2>/dev/null) || DEST=""`.
  (The hook runs `set -uo pipefail`, no `-e`; `$(...)` strips the trailing
  newline, so `DEST` is exactly the path.)
- If `DEST` is non-empty, emit a user-visible `systemMessage` (verified against
  the hooks reference: `systemMessage` is a supported top-level PostToolUse field
  shown to the user as a warning; plain PostToolUse stdout is **not** user-
  visible, so JSON is required). Constraints:
  - Build with `jq -n --arg dest "$DEST" '{systemMessage: ...}'` — `--arg` is
    string-typed and escaped, so a dest with quotes/newlines can't break the JSON.
  - Write jq's output **directly to the hook's own stdout with no downstream
    pipe** (a pipe would let `pipefail` surface a consumer failure), then `exit 0`.
  - Empty `DEST`, missing `jq`, or any failure → emit nothing, `exit 0`.
- Message text names the destination and how to reload, e.g.:
  `Continuity file moved to <DEST> to keep ~/.claude/handoffs tidy. Reload with: claude --append-system-prompt-file <DEST>`
- The hook stays fail-open and never blocks the Read; it is simply no longer
  *silent* on success. Update the header comment to say so.

### Why not the alternatives

- **Persistent `src → dest` index** (earlier draft, in `~/.claude`): outlives the
  `/tmp` files it points to → stale post-reboot pointers; and the append/umask/
  lookup machinery + harness leak-risk isn't worth saving one `ls`. Rejected.
- **Index in `/tmp`** (shares the files' lifetime, so no stale pointers): honest,
  but now redundant with the `/tmp` files themselves — still log machinery to
  avoid an `ls`. Rejected as overengineered.
- **Print at move-time only, nothing on the not-found path**: leaves the exact
  case in the report (a later `resume-context` on a gone path) still bare.

## Critical files

- `claude/.claude/scripts/resume-context.sh` — `print_recovery_hint` helper,
  launch-mode dest print, consume-only stdout dest, enriched not-found hint,
  header docs.
- `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh` — capture
  dest, emit `systemMessage` via `jq --arg` direct to stdout, header docs.
- `claude/.claude/scripts/tests/test_resume_context.py` — new assertions.
- `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py`
  — new assertions.

No new `$HOME` writes are introduced (the dropped index was the only one), so no
test-harness isolation change is needed — the existing `RESUME_CONTEXT_TMPDIR` /
`isolated_home` seams already keep tests off the real filesystem.

## New tests

**`test_resume_context.py`:**
- Launch mode: stderr contains `moved ... -> <dest>` and the reload line naming
  the dest (existing happy-path move/launch assertions stay).
- Consume-only: **stdout is exactly the dest path** (assert no extra/trailing
  content) — guards the hook's capture contract.
- Not-found: stderr names the `<TMPDIR_ROOT>/resume-context.*` location and the
  reboot caveat; `exit 1`. Assert it uses the overridden `RESUME_CONTEXT_TMPDIR`
  value, not a hardcoded `/tmp`.

**`test_consume_durable_continuity_file_on_read.py`:**
- On consume: hook stdout is valid JSON (jq-parseable) carrying `systemMessage`
  with the dest.
- Fail-open: already-gone source / script failure → no `systemMessage`, `exit 0`.
- Kill-switch still fully suppresses (no move, no message).

## Verification

```bash
# from the repo root
.venv/bin/pytest claude/.claude/scripts/tests/test_resume_context.py \
                 claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py -q
.venv/bin/ruff check claude/.claude/
# Manual smoke (env seams only — never the real claude/tmp/home):
tmp=$(mktemp -d)
printf 'hi\n' > "$tmp/foo-handoff.md"
RESUME_CONTEXT_LAUNCHER=true RESUME_CONTEXT_TMPDIR="$tmp" \
  claude/.claude/scripts/resume-context.sh "$tmp/foo-handoff.md"   # stderr: moved ... -> <dest>
RESUME_CONTEXT_LAUNCHER=true RESUME_CONTEXT_TMPDIR="$tmp" \
  claude/.claude/scripts/resume-context.sh "$tmp/foo-handoff.md"   # not-found -> points at $tmp/resume-context.*
ls -t "$tmp"/resume-context.*                                       # the moved copy is discoverable here
```
No SKILL.md or agent files touched, so `/skill-review` / `/agent-review` are not
needed. Pre-commit `/code-review` dispatches `claude-hook-review` (hook change)
and `staff-*` reviewers as applicable against the written code.

## Out of scope

- Any persistent record / index of moves (see Design note and Why-not).
- Auto-listing or auto-recovering `/tmp` files from the script (the not-found
  hint tells the human where + `ls -t`; the script performs no listing).
- Changing the destination naming to be descriptive (the random name is a
  deliberate slug-leak defense — preserved).
- `handoff`/`brief` SKILL.md §7 prose: the recovery hint is self-documenting via
  script output; these skills argue for brevity, so no note is added. Called out
  so the omission is a decision, not an oversight.
