# Durable index for consumed continuity files

## Context

When a `/handoff` or `/brief` continuity file is consumed — relocated from
`~/.claude/handoffs/` or `~/.claude/briefs/` to a random
`/tmp/resume-context.<hash>` path, by either
`consume-durable-continuity-file-on-read.sh` (the PostToolUse:Read hook) or
`resume-context.sh`'s own launch-mode move — the destination is reported
only ephemerally: stderr, plus `hookSpecificOutput.additionalContext` for
the hook path. Both are visible only in the consuming session's own
transcript. `resume-context.sh`'s own "not found" branch already tells a
human *where to look* (`ls -t $TMPDIR_ROOT/resume-context.*`) but not
*which* file, because there is no durable slug-to-destination record. A
different session — not the one that did the consuming — currently has no
way to find the destination without grepping every `/tmp/resume-context.*`
file by content.

This is distinct from the gap GH-474 closed: that fix taught the
*authoring* session not to re-`Read` its own handoff, via `handoff/SKILL.md`'s
"Verify… never Read" section. It does nothing for a *different* session's
`Read` consuming a file, which is the gap this plan closes.

The fix must record the relocation in a durable-enough index so a lookup
can resolve it later, while keeping the index itself in `/tmp` (not
`~/.claude/handoffs/` or any other durable/backed-up location)
`[engineer-verified]` — specifically so it inherits normal OS tmp-cleanup
lifecycle and never needs a separate purge mechanism.

**Scope:** both `~/.claude/handoffs/*-handoff.md` and
`~/.claude/briefs/*-task.md` `[engineer-verified]` — both flow through the
same two consuming code paths, so the same mechanism closes the same gap
for each.

**Lookup surface:** a small new wrapper script, not a documented grep
recipe `[engineer-verified]` — discoverable, testable, matches this repo's
script-first convention for multi-step Bash recipes, and gives Verification
a concrete test target.

## Approach

Record the relocation from the one place that actually performs it —
`resume-context.sh` — into a single per-uid append-only TSV under the same
tmp root as the destinations themselves, and ship a small reader script
that resolves a slug substring to a still-live destination path. The hook
needs no new code: it already delegates its move to
`resume-context.sh --consume-only`, so one insertion point covers both
consuming paths.

**The single-writer-site finding.** `consume-durable-continuity-file-on-read.sh`
performs no move of its own — lines 118–122 shell out to
`"$RESUME_SCRIPT" --consume-only "$FILE_PATH"` and do nothing with the file
but format the returned path into two messages. `resume-context.sh:208–223`
(`mktemp` → `mv` → `chmod 600`) is the only relocation in either path,
shared by launch mode and `--consume-only` alike. The index append
therefore lands once, in `resume-context.sh`, immediately after a
successful `mv` and **before** the `chmod 600`. That ordering closes a
documented gap: the hook's fourth known gap
(`consume-durable-continuity-file-on-read.sh:77–81`) records that a `chmod`
failure exits `resume-context.sh` non-zero with the file already moved and
empty stdout, so "neither output channel reports it." Appending before the
`chmod` means the move is recorded at the moment it becomes true, and that
gap's premise no longer holds.

**Index location and record.**
`${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}/resume-context-index-$EUID/consumed.tsv`,
one row per consume:

```
<utc-iso8601>\t<destination>\t<source>
```

Source last, because it is the only field whose bytes come from an
argument rather than from `mktemp`/`date` — a tab inside it cannot displace
`<destination>` from field 2. The directory name uses a hyphen
(`resume-context-index-…`), so it does not match `resume-context.*`, the
glob the not-found branch already tells humans to `ls -t`
(`resume-context.sh:183`).

**The 0600-from-creation mechanism, concretely.**
`track-permission-prompts.sh:71–82` is this repo's existing, documented
pattern for exactly this problem, with its three-layer rationale already
written out: refuse a symlinked target, `umask 077` to close the
creation-time window, `chmod 600` before the append to tighten a
pre-existing looser file, `chmod 600` after as backstop. Two adaptations
are required and neither is optional:

1. *The parent directory carries the primary control, not the file.*
   `track-permission-prompts.sh` can dismiss symlink-planting as "requires
   local write access to `$CONFIG_DIR` already" because `$CONFIG_DIR` is
   `chmod 700` from `install.sh`. `/tmp` is not —
   `resume-context.sh:73–83` states the 1777 premise itself. The index has
   a *predictable* name (unlike every `mktemp` destination), so any local
   user can pre-create it. Restore the `$CONFIG_DIR` property by creating a
   per-uid directory at mode 0700 in a single `mkdir(2)`, then refusing to
   proceed unless the path we ended up with is a real, non-symlink
   directory we own. `/tmp`'s sticky bit prevents another user from
   renaming or removing a directory we own, so after that check every
   file-level guard inside it is defense-in-depth rather than the control.
   **Acknowledged tradeoff (`staff-platform-engineer` review):** if another
   local user (or a stray leftover) already owns a directory or symlink at
   this exact predictable path, `mkdir` and the ownership check correctly
   refuse it — but `/tmp`'s own sticky bit then also blocks the legitimate
   uid from ever reclaiming that name, so the index feature is disabled
   permanently and silently for that uid on that machine (the consume
   itself is unaffected; only cross-session lookup degrades). This is a
   proportionate failure mode given the plan's best-effort framing and the
   deliberate absence of a kill-switch (see Out of scope) — recorded here
   so it isn't rediscovered as a surprise later.
   **Different-owner pre-creation (`ciso-reviewer` review):** the case
   where the squatting directory is owned by a genuinely different OS user
   cannot be reproduced in single-uid CI. It is closed by POSIX semantics
   rather than an executable test: `mkdir(2)` is atomic, so a successful
   call always yields a directory owned by the calling EUID with no
   attacker-controlled window, and a failed call (attacker got there first)
   is caught by `[ -O "$dir" ]` evaluating false, since an attacker cannot
   forge EUID ownership without privilege they don't have. Record this
   reasoning as a comment or docstring at `_lib_resume_context_index_file`
   in `claude/.claude/hooks/tests/test_lib.py` rather than an executable
   regression test, so the residual is documented and tracked rather than
   invisible to a future edit that weakens the guard (e.g. swapping
   `[ -O ]` for `[ -w ]`).
2. *`umask` must be set in a subshell, never process-wide.*
   `track-permission-prompts.sh` can set `umask 077` globally because it
   exits immediately. `resume-context.sh:243` `exec`s into an interactive
   `claude`, which would inherit a process-wide umask and write every
   subsequent file in that session at 0600. Both the `mkdir` and the append
   go inside their own `( umask 077; … )`.

```bash
# in _lib.sh — one home for the path formula, called by writer and reader
_lib_resume_context_index_file() {
  local dir
  dir="$(_lib_resume_context_tmpdir_root)/resume-context-index-$EUID"
  # EEXIST here is the expected steady state from the second call onward;
  # left unguarded by design — safe under `set -e` only because this
  # function always runs inside a command substitution (`$(...)`), never
  # called directly. A future direct call would need its own `|| true`.
  ( umask 077; mkdir -- "$dir" ) 2>/dev/null
  [ ! -L "$dir" ] && [ -d "$dir" ] && [ -O "$dir" ] || return 1
  chmod 700 -- "$dir" 2>/dev/null
  printf '%s\n' "$dir/consumed.tsv"
}
```

`local dir; dir=...` is deliberately two statements, not `local dir=$(...)` —
the combined form is SC2155 (declare-and-assign masks the substitution's own
exit status) and this repo's pinned `shellcheck` flags it; `_lib.sh` has no
existing instance of that shape.

```bash

# in resume-context.sh, after a successful mv, before chmod 600 "$DEST"
record_consumed_destination() {                 # invoked as `... || true`
  local src="$1" dest="$2" index stamp
  case "$src" in *$'\n'*) return 0 ;; esac      # a newline would forge a row
  index=$(_lib_resume_context_index_file) || return 0
  [ -L "$index" ] && return 0
  [ -e "$index" ] && chmod 600 -- "$index" 2>/dev/null
  stamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ') || return 0
  ( umask 077; printf '%s\t%s\t%s\n' "$stamp" "$dest" "$src" >> "$index" ) 2>/dev/null
  chmod 600 -- "$index" 2>/dev/null
  return 0
}
```

Any guard failure skips the index write entirely and never falls back to a
looser path; the consume itself proceeds regardless, because the index is
best-effort convenience and the move is the contract.

**No lock.** Every destination is a fresh `mktemp` name, so
`_lib_append_line_locked`'s dedup half (`grep -qFx`, `_lib.sh:2364`) can
never fire; only its mutual-exclusion half would be used, and its own
documented worst case after five failed acquisitions is an unlocked append
anyway (`_lib.sh:2335–2338`). Against that it brings a hazard specific to
this caller: it registers a bare `trap … EXIT` whose lock file holds `$$`,
and `resume-context.sh` `exec`s — an EXIT trap does not run across `exec`,
and the leaked lock's PID is then the live `claude` process, so the
helper's `kill -0` eviction never fires and the lock survives the whole
interactive session. `handoff-record-conversion.sh:22` is the in-repo
precedent for the lighter primitive (a plain `>>` for one best-effort
line), and it appends to `$CONFIG_DIR`, which can be network-backed; ours
cannot, so a single short-line `printf` under `O_APPEND` is strictly safer
here than at the precedent's own call site.
**Test scope (`ciso-reviewer` + `staff-sdet` convergent finding):** this
argument establishes why a lock is unnecessary; it does not by itself
demonstrate that unlocked `O_APPEND` survives genuine concurrent writers.
Verification below adds a real multi-subprocess concurrency test rather
than relying on sequential repetition to imply it — see Verification
item 6's revision.

**The reader.** `find-consumed-continuity-file.sh <slug-substring>` calls
the same `_lib.sh` helper for the path, then walks rows with
`while IFS=$'\t' read -r stamp dest src` and substring-matches `$src`
only — not the whole line, so a numeric slug can't match a timestamp, and
no regex or `--` handling is needed. It prints only rows whose destination
still exists, is a regular file, is not a symlink, and is owned by `$EUID`.
That filter is required for truthfulness, not hygiene: age-based tmp
cleanup sweeps individual destination files while the index's own mtime
keeps refreshing on each append, so an index outliving its oldest rows is
the normal steady state. It is also the integrity control on a surface
whose output a human or agent then feeds to
`claude --append-system-prompt-file` — an unowned or symlinked destination
is never printed.

**Loop closure at the point of confusion.** `resume-context.sh`'s
not-found branch (lines 180–186) is where a human lands when the file is
already gone; it gains one line naming the lookup command with the slug
pre-filled from `${SRC##*/}` (pure parameter expansion, no fork; the full
source path is already printed on line 181, so this leaks nothing new).
`print_recovery_hint` (lines 105–108) moves to `_lib_print_recovery_hint`
so the reader can print the reload command without duplicating the
string — the existing comment states keeping it in one place is the point.

### Assumption ledger

**Root problem:** a continuity file's `/tmp` destination is reported only
into the consuming session's own transcript, so a different session cannot
find it without grepping every `resume-context.*` file by content.

**Givens:**

- The index lives in `/tmp`, not a durable directory, so OS tmp-cleanup
  owns its lifecycle and no purge mechanism is written
  `[engineer-verified]`.
- Destination filenames stay non-descriptive; the slug must not become
  visible via `ls` on a shared box (`resume-context.sh:73–83` — the
  constraint this design must not reopen).
- Scope covers handoffs and briefs together `[engineer-verified]`.
- The lookup surface is a script, not a documented grep recipe
  `[engineer-verified]`.
- `~/.claude/handoffs/` and `~/.claude/briefs/` are 0700 only if
  `install.sh`'s one-time hardening ran; this plan does not change that
  and does not depend on it.

| # | Assumption | Tag |
|---|---|---|
| 1 | The hook performs no move of its own — it shells out to `resume-context.sh --consume-only` and only formats the returned path, so one insertion point in `resume-context.sh` covers both consuming paths | `[verified: consume-durable-continuity-file-on-read.sh:118-134; resume-context.sh:208-228]` |
| 2 | `mktemp` → `mv` → `chmod 600` at `resume-context.sh:208-223` is shared by launch mode and `--consume-only`; the mode split happens only afterward at line 225 | `[verified: resume-context.sh:208-231]` |
| 3 | Appending before the `chmod` converts the hook's fourth known gap from "neither output channel reports it" into "the index still names it" — the `chmod` failure path exits at line 222 before any later statement would run | `[verified: resume-context.sh:220-223; consume-durable-continuity-file-on-read.sh:77-81]` |
| 4 | `--consume-only`'s stdout contract is exactly one line, the destination; the index append writes to a file and emits nothing on stdout, so the contract is undisturbed | `[verified: resume-context.sh:50-52, 225-228]` |
| 5 | `track-permission-prompts.sh` already establishes the umask-plus-idempotent-chmod append pattern in this repo, with its layering rationale written out | `[verified: track-permission-prompts.sh:71-82]` |
| 6 | A process-wide `umask 077` in `resume-context.sh` would be inherited by the exec'd interactive session; `track-permission-prompts.sh` avoids this only because it exits immediately | `[verified: resume-context.sh:243 — terminal exec "$LAUNCHER" …]` |
| 7 | `_lib_append_line_locked` registers a bare `trap … EXIT` holding `$$`, and eviction is by `kill -0` — so across `exec` the trap never runs and the leaked lock's PID stays live for the session | `[verified: _lib.sh:2318-2372]` |
| 8 | Its dedup half is inert here (every destination is a fresh `mktemp` name) and its documented lock-exhaustion fallback is an unlocked append | `[verified: _lib.sh:2335-2338, 2364-2371; resume-context.sh:208]` |
| 9 | Sourcing `_lib.sh` from a `scripts/*.sh` script under `set -euo pipefail` is the established convention, unguarded | `[verified: handoff-record-conversion.sh:6-9; review-ledger.sh:7-10; both use . "$(dirname "$0")/../hooks/_lib.sh"]` |
| 10 | `${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}` appears in exactly one code site today (`resume-context.sh:161`); a second independent copy in the reader would silently split the index from the destinations on any future rename | `[verified: repo-wide grep for RESUME_CONTEXT_TMPDIR — one code site, the rest are tests and committed plan files]` |
| 11 | `resume-context-index-<uid>` does not match the `resume-context.*` glob the not-found branch already publishes | `[verified: resume-context.sh:183 — literal dot after the prefix]` |
| 12 | `handoff/SKILL.md` is 198 lines against a 200-line cap; `brief/SKILL.md` is 190. The handoff edit has **two lines** of headroom and must replace the existing sentence rather than add to it | `[verified: line counts this session; check-skill-length.sh:64-74 — neither file is in the 500/210 override list]` |
| 13 | Both skills carry the identical closing sentence "it does not name where the file went," which this change falsifies | `[verified: handoff/SKILL.md:55-59; brief/SKILL.md:39-43]` |
| 14 | A new `claude/.local/bin/` shim must be added to `KNOWN_EXTENSIONLESS_SHELL_FILES` or the shellcheck-discovery test fails | `[verified: test_shellcheck.py:40-53 — nine existing shims enumerated, including claude/.local/bin/resume-context]` |
| 15 | `claude/.local/bin/` matches no `DOMAIN_RULES` predicate, so `select-tests.py` returns `unmatched-path` and widens to the full suite for this diff | `[verified: select-tests.py:342-351, 499-514; no rule references .local/bin]` |
| 16 | A new `scripts/*.sh` is already routed to `SCRIPTS_TESTS_DIR`, to `HOOKS_TESTS_DIR` for shellcheck, and to `SKILLS_TESTS_DIR` for the executable-bit check — no rule-table entry needed for the script itself | `[verified: select-tests.py:344, 384-392; test_skills.py:2133-2141]` |
| 17 | `/tmp` is world-writable-and-sticky (1777) on the target platforms, so the predictable index name is plantable but a directory we own cannot be replaced by another user | `[verified: resume-context.sh:75-76 states the 1777 premise; the sticky-bit consequence is standard POSIX, not executed this session]` |
| 18 | Bash's `[ -O path ]` (owned by effective uid) and `mkdir` under `umask 077` yielding exactly 0700 behave as designed | `[unverified]` — documented bash/POSIX behavior; the new `_lib.sh` tests assert both empirically |
| 19 | Age-based tmp cleanup sweeps individual destinations while the index's mtime keeps refreshing, so stale rows are the steady state rather than an edge case | `[unverified]` — cleaner policy varies by distro; the design does not depend on it, because the reader existence-checks every row either way |
| 20 | One shared per-uid index across multiple `CLAUDE_CONFIG_DIR` accounts is correct, not a leak: rows carry absolute source paths that name their config dir, and the destinations already share one tmp root today | `[verified: resume-context.sh:161 — the root is uid-scoped at most, never config-dir-scoped]` |

### Mechanism justification

- **Append inside `resume-context.sh`, not the hook** (`anchors: root`,
  `row1`). Lighter primitives considered: (a) have the hook write the row
  from `$DEST` — rejected, it covers only the Read path and leaves the
  launch path unrecorded, and the hook has no access to the resolved
  source after the legacy-location fallback; (b) a separate PostToolUse
  hook watching for `resume-context.*` creation — rejected, a new hook is a
  wider-scope mechanism for something the moving script already knows for
  free, and it would fire after the fact with no source path at all.
- **Plain `>>` in a `umask` subshell rather than
  `_lib_append_line_locked`** (`anchors: row7`, `row8`). This is the
  lighter primitive; the heavier one is the shared helper, and rows 7–8
  record why it is heavier *and* worse here.
- **Per-uid 0700 parent directory rather than file-level guards alone**
  (`anchors: row17`). Lighter primitives considered: (a)
  `track-permission-prompts.sh`'s symlink-check-plus-chmod with no parent
  directory — rejected, that pattern's own soundness argument rests on
  `$CONFIG_DIR` being 0700, which `/tmp` is not; (b) a `mktemp`-generated
  index name — rejected outright, an unpredictable name is exactly what
  makes the index unfindable by another session, which is the root
  problem.
- **`_lib.sh` helper for the path formula rather than a duplicated
  expression** (`anchors: row10`). A drift here fails silently and
  asymmetrically: the writer and reader would target different roots and
  the lookup would return nothing with no error.
- **Source path as the last TSV field and a newline guard on it**
  (`anchors: root`). Field order removes the tab-corruption case
  structurally; only the newline case needs a check, and it fails closed
  by skipping the row.
- **Reader filters on destination liveness and ownership**
  (`anchors: row19`). One filter serves two purposes: truthfulness under
  tmp cleanup, and refusing to print a path that would become an
  `--append-system-prompt-file` argument.

### Dispatch shape

One `code-writer` dispatch, not split. The record format, the `_lib.sh`
guard helper, and the reader's row-parsing contract are shared context
every part would otherwise have to restate. Order within the dispatch:
`_lib.sh` helpers → `resume-context.sh` writer → reader script and shim →
tests → docs and skills.

Two implementation notes the diff must carry, because they read as
oversights otherwise. `record_consumed_destination` is invoked as
`record_consumed_destination "$SRC" "$DEST" || true`, which disables
`set -e` for the whole function body — that is the intent (best-effort,
never abort a completed move), and this repo's shell-script-conventions
rule flags the `||` suppression, so state the intent in one line at the
call site. And the `chmod 700` on the index directory runs only after the
`! -L` / `-d` / `-O` triple has passed, since `chmod` dereferences.

## Critical files

**Create**

- `claude/.claude/scripts/find-consumed-continuity-file.sh` — the lookup
  script, mode 0755. Sources `_lib.sh` per the
  `handoff-record-conversion.sh` pattern. Contract: stdout is zero or more
  `<stamp>\t<dest>\t<src>` rows in append order (newest last), filtered to
  live destinations; stderr carries the reload hint for the newest match
  and the diagnostic on failure; exit 0 when at least one row printed, 1
  otherwise, with stderr distinguishing "no index" from "no row matched
  that substring" from "matched N rows, every destination has been cleaned
  up — unrecoverable." No argument prints every live row.
- `claude/.local/bin/find-consumed-continuity-file` — two-line shim, mode
  0755, exactly the shape of `claude/.local/bin/resume-context`
  (`exec "$HOME/.claude/scripts/find-consumed-continuity-file.sh" "$@"`).
- `claude/.claude/scripts/tests/test_find_consumed_continuity_file.py`.

**Modify**

- `claude/.claude/hooks/_lib.sh` — add `_lib_resume_context_tmpdir_root`,
  `_lib_resume_context_index_file` (creates, guards, chmods, prints;
  returns 1 on an untrusted path), and `_lib_print_recovery_hint` (moved
  from `resume-context.sh:105-108`).
- `claude/.claude/scripts/resume-context.sh` — source `_lib.sh`; line 161
  becomes `TMPDIR_ROOT=$(_lib_resume_context_tmpdir_root)`; add
  `record_consumed_destination` and call it after the `mv` at line 213 and
  before the `chmod` at line 220; add one line to the not-found branch
  (180–186) naming the lookup command with `${SRC##*/}`;
  `print_recovery_hint` delegates to the `_lib.sh` version; header
  "Destination visibility" block gains the index as a third channel.
- `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh` —
  comment only, no code. Rewrite the fourth known-gap bullet (lines
  77–81): the chmod-failure case is now recorded in the index even though
  both output channels stay silent.
- `claude/.claude/scripts/tests/test_resume_context.py` — new
  index-append coverage; extend the existing not-found-hint assertions
  (the class around lines 85–95) for the added line; the concurrent-writer
  test (Verification item 10, launching multiple real `--consume-only`
  subprocesses) and the writer/reader end-to-end contract test
  (Verification item 12) both belong here, since both drive the real
  `resume-context.sh` entry point rather than unit-testing `_lib.sh` in
  isolation.
- `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py` —
  one end-to-end test that a hook-triggered consume also lands a row,
  exercising the delegation rather than re-testing the append.
- `claude/.claude/hooks/tests/test_lib.py` — unit coverage for the three
  new helpers, the directory-mode repair-pass case (Verification item 11),
  and a docstring recording the different-owner reasoning (see mechanism
  item 1's "Different-owner pre-creation" note).
- `claude/.claude/skills/tests/test_skills.py` — content assertion that
  `handoff/SKILL.md` and `brief/SKILL.md` both reference
  `find-consumed-continuity-file` after the edit (Verification item 13).
- `claude/.claude/hooks/tests/test_shellcheck.py` — add
  `claude/.local/bin/find-consumed-continuity-file` to
  `KNOWN_EXTENSIONLESS_SHELL_FILES` (lines 40–53).
- `docs/scripts.md` — extend the `resume-context.sh` entry (line 150) with
  the index write and its guard; add a `find-consumed-continuity-file.sh`
  entry with the same bullet-plus-fenced-usage shape the neighbouring
  entries use.
- `docs/hooks.md:64` — one clause on the consume-hook entry: the
  destination is now also recorded in a cross-session index, with the
  pointer to `docs/scripts.md` it already carries.
- `claude/.claude/skills/handoff/SKILL.md:55-59` — **net ≤ +2 lines**
  (198/200). Replace the clause "it does not name where the file went" —
  keeping the sentence's existing lead-in — with:
  `recover the destination with
  \`~/.claude/scripts/find-consumed-continuity-file.sh <slug>\`.`
  Do not append a new sentence; edit the existing one in place so the net
  line delta stays within budget. Verify with `wc -l` before committing —
  the length gate is a local `git commit` hook, not CI, so a miss here is
  not caught until commit time.
- `claude/.claude/skills/brief/SKILL.md:39-43` — same replacement text
  (`<slug>` here refers to the brief's own task slug), 10 lines of
  headroom.
- `README.md:74` — one clause on the "Self-consuming continuity files"
  bullet. Line 481 stays untouched; it documents the resume command,
  which does not change.

**Reuse rather than reimplement**

- `track-permission-prompts.sh:71-82` — the umask/chmod append layering,
  adapted per the two changes above.
- `handoff-record-conversion.sh` — the whole shape of a best-effort
  single-line append from a `scripts/` script, including the `_lib.sh`
  source line and its `# shellcheck source=../hooks/_lib.sh` directive.
- `claude/.local/bin/resume-context` — the shim template.
- `_lib_print_recovery_hint` — the one home for the reload string, as
  `resume-context.sh:99-104` already intends.

## Verification

`.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the
documented scoped command. It will report `unmatched-path` and widen to
`claude/.claude/ plugins/` on its own, because `claude/.local/bin/` matches
no rule (assumption-ledger row 15). That is CLAUDE.md's first legitimate
full-suite case, not a hand-widening; do not add a rule-table entry to
avoid it.

Then `.venv/bin/ruff check claude/.claude/` and
`scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.

New tests must cover, at minimum:

1. A `--consume-only` run appends exactly one row whose field 2 equals the
   path on stdout and whose field 3 is the source, with
   `RESUME_CONTEXT_TMPDIR` pointed at `tmp_path` (the existing harness
   already isolates this).
2. A launch-mode run appends the same row and still `exec`s the recorder
   stub.
3. The index file's mode is `0600` and its parent directory's is `0700`,
   asserted with `stat().st_mode & 0o777` immediately after the first
   run — first creation, not after a repair pass.
4. A pre-existing index file left at `0644` is tightened to `0600` by the
   next append.
5. The index directory pre-created as a symlink causes the append to be
   skipped, and the consume still succeeds with its normal stdout.
6. Ten sequential consumes produce ten well-formed rows and no interleaved
   line. This proves absence of corruption *without* concurrency, not the
   no-lock design's actual claim — see item 10.
7. The hook path (via
   `test_consume_durable_continuity_file_on_read.py`) lands a row for a
   Read-triggered consume.
8. The reader returns the destination for a slug substring; returns
   nothing and exits 1 when the destination has been deleted, with stderr
   naming the consumed-but-cleaned-up case distinctly; matches against the
   source field only, so a substring occurring in a timestamp does not
   match; skips a row whose destination is a symlink.
9. `resume-context.sh`'s not-found branch names the lookup command with
   the requested file's basename, and still names `$RESUME_CONTEXT_TMPDIR`
   (the existing assertion must keep passing).
10. **(`ciso-reviewer` + `staff-sdet`)** Two (or more) real
    `resume-context.sh --consume-only` subprocesses launched concurrently
    against the same `RESUME_CONTEXT_TMPDIR`-scoped index; assert every
    resulting line parses as a well-formed 3-field row with none
    truncated or merged — the actual test of the no-lock design's central
    claim, which item 6 does not provide.
11. **(`staff-sdet`)** A pre-existing index *directory* left at `0755`
    (owned, non-symlink) is tightened to `0700` on the next call to
    `_lib_resume_context_index_file` — mirrors item 4's file-level repair
    pass, which the directory's own unconditional `chmod 700` needs
    symmetric coverage for.
12. **(`staff-sdet`)** An end-to-end test invoking the real
    `resume-context.sh --consume-only` and the real
    `find-consumed-continuity-file.sh` as two separate subprocesses under
    one shared `RESUME_CONTEXT_TMPDIR` override, asserting the reader
    finds the writer's row — the contract test at the writer/reader
    boundary that would fail if a future edit ever reintroduces two
    independent copies of the tmpdir-root formula (ledger row 10).
13. **(`staff-sdet`)** A content assertion (substring/`grep`, not a
    behavioral test) in `claude/.claude/skills/tests/test_skills.py` or a
    sibling, that both `handoff/SKILL.md` and `brief/SKILL.md` reference
    `find-consumed-continuity-file` after the edit — the only guard
    currently named for those two files is the line-count ceiling, which
    says nothing about whether the prose itself was updated correctly.

Manual check before commit, since the skill-length gate is a local
`git commit` hook and not in CI:
`wc -l claude/.claude/skills/handoff/SKILL.md` must stay ≤ 200.

## Out of scope

- **Any purge, rotation, or size cap on the index.** The engineer's
  constraint places it in `/tmp` precisely so OS tmp-cleanup owns the
  lifecycle; the reader's per-row existence check makes stale rows
  harmless in the meantime.
- **A dedicated kill-switch for the index write.**
  `.consume-durable-continuity-disabled` already suppresses the
  Read-triggered path end to end, and the launch path is explicitly
  invoked by a human who is asking for the file to be moved. Adding a
  second sentinel is machinery this change does not need.
- **A `claude/.local/bin/` rule in `select-tests.py`'s `DOMAIN_RULES`.**
  Real, but it changes selection for all nine existing shims, and getting
  it wrong under-selects — the failure direction the whole table is built
  to avoid. Unrelated to this change's correctness; raise it separately.
- **Recording anything beyond timestamp, destination, and source**
  (session id, branch, cwd). Each additional field is a slug-adjacent
  identifier in a shared-`/tmp` file and needs its own justification
  against the row-17 threat model.
- **Making destination filenames descriptive**, which would dissolve the
  need for an index but reopen the `ls`-visibility leak
  `resume-context.sh:73-83` documents.
- **Surfacing the index through the hook's `additionalContext`.** That
  channel already names the destination to the session that consumed it;
  the index exists for the *other* session, which has no tool result to
  attach to.
- **Migrating other `>>`-append sites in the repo onto the new directory
  guard.** Only this one targets a world-writable root.

**One judgment call worth an explicit override if the engineer disagrees:**
the reader's stdout is raw TSV rows rather than a single bare destination
path. Rows keep it usable when several handoffs share a slug prefix and
let `cut -f2` recover the machine form; a bare-path contract would be
terser for an agent but forces an arbitrary tie-break when more than one
row matches. Recommendation: keep rows, as specified above.
