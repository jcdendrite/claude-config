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
as the outer bound on its lifecycle. A 30-day whole-file sweep runs inside
that bound as well, added during code review so task-descriptive slugs are
not retained indefinitely on a machine whose `/tmp` survives for months —
see the Assumption ledger's first Given.

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
`resume-context.sh` — into a per-uid append-only TSV under the same tmp
root as the destinations themselves, sharded one file per UTC day, and
ship a small reader script that resolves a slug substring to a still-live
destination path. The hook needs no new code: it already delegates its
move to `resume-context.sh --consume-only`, so one insertion point covers
both consuming paths.

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
`${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}/resume-context-index-$EUID/consumed.<UTC YYYY-MM-DD>.tsv`,
one row per consume, in the day-file for the UTC date the consume happened on:

```
<utc-iso8601>\t<destination>\t<source>
```

Source last, because it is the only field whose bytes come from an
argument rather than from `mktemp`/`date` — a tab inside it cannot displace
`<destination>` from field 2. The directory name uses a hyphen
(`resume-context-index-…`), so it does not match `resume-context.*`, the
glob the not-found branch already tells humans to `ls -t`
(`resume-context.sh:183`). Splitting by day is what lets retention (below)
delete a whole stale file instead of rewriting a live one — see the No
lock subsection.

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
   reasoning as a comment or docstring at `_lib_resume_context_index_dir`
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
# in _lib.sh — one home for the index directory, called by writer and reader
_lib_resume_context_index_dir() {
  local dir
  dir="$(_lib_resume_context_tmpdir_root)/resume-context-index-$EUID"
  # EEXIST here is the expected steady state from the second call onward;
  # left unguarded by design — safe under `set -e` only because this
  # function always runs inside a command substitution (`$(...)`), never
  # called directly. A future direct call would need its own `|| true`.
  ( umask 077; mkdir -- "$dir" ) 2>/dev/null
  [ ! -L "$dir" ] && [ -d "$dir" ] && [ -O "$dir" ] || return 1
  chmod 700 -- "$dir" 2>/dev/null
  printf '%s\n' "$dir"
}
```

`local dir; dir=...` is deliberately two statements, not `local dir=$(...)` —
the combined form is SC2155 (declare-and-assign masks the substitution's own
exit status) and this repo's pinned `shellcheck` flags it; `_lib.sh` has no
existing instance of that shape.

The helper prints the directory, not a file. Rows live in **day-files** named
`consumed.<UTC YYYY-MM-DD>.tsv` inside it: the writer composes today's name,
the reader globs `consumed.*.tsv` in name order (which is chronological,
since the names are fixed-width ASCII digits), and retention deletes whole
files by mtime. That naming contract is stated once, in this helper's doc
comment, and is enforced across the writer/reader boundary by the end-to-end
test (Verification item 12) rather than by a third helper — a lister helper
would still leave the reader owning a glob, so it would move the duplication
rather than remove it.

```bash
# in resume-context.sh, after a successful mv, before chmod 600 "$DEST"
record_consumed_destination() {                 # invoked as `... || true`
  local src="$1" dest="$2" dir stamp row row_bytes day_file
  case "$src" in *$'\n'*) return 0 ;; esac      # a newline would forge a row
  dir=$(_lib_resume_context_index_dir) || return 0
  stamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ') || return 0
  # One file per UTC day, so retention below is a whole-file mtime sweep
  # rather than a rewrite of a file other processes are appending to.
  # ${stamp%%T*} is the date half of the stamp already computed above, so
  # this costs no second `date` fork.
  day_file="$dir/consumed.${stamp%%T*}.tsv"
  [ -L "$day_file" ] && return 0
  [ -e "$day_file" ] && chmod 600 -- "$day_file" 2>/dev/null
  row="$stamp"$'\t'"$dest"$'\t'"$src"
  # Byte count, not ${#row}: bash counts characters under a multi-byte
  # locale, so a non-ASCII $src can pass a character cap while the bytes
  # actually written exceed it. `wc -c` counts exactly what the printf
  # below writes, trailing newline included.
  # bash's printf builtin chunks output into multiple write(2) calls past
  # roughly 4096 bytes (verified via strace), and only a single write(2)
  # call is atomic under O_APPEND. 2048 leaves headroom for
  # cross-platform/shell-version variance.
  row_bytes=$(printf '%s\n' "$row" | wc -c | tr -d '[:space:]') || return 0
  [ "$row_bytes" -gt 2048 ] && return 0
  ( umask 077; printf '%s\n' "$row" >> "$day_file" ) 2>/dev/null
  chmod 600 -- "$day_file" 2>/dev/null
  # 30-day retention, the same sweep every other self-sweeping state
  # directory in this repo uses. Unlinking a whole file is not a
  # read-modify-write, so concurrent sweeps cannot lose a row. Runs after
  # the append so a `find` failure never costs the row, and tolerates a
  # racing sweep that already unlinked the same file.
  find "$dir" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true
  return 0
}
```

The `tr -d '[:space:]'` is not cosmetic: BSD/macOS `wc` pads its output with
leading spaces, and `log-reviewer-round.sh:104` already strips the same
padding off a `wc` result for the same reason.

Any guard failure skips the index write entirely and never falls back to a
looser path; the consume itself proceeds regardless, because the index is
best-effort convenience and the move is the contract.

**No lock, because retention never rewrites a file another process is
appending to.** Two properties have to hold at once: concurrent appends must
not corrupt or lose rows, and the index must not retain task-descriptive
slugs indefinitely. Both are met without any coordination primitive, because
the unit of retention is a whole file rather than a row inside one.

*Appends.* Every destination is a fresh `mktemp` name, so
`_lib_append_line_locked`'s dedup half (`grep -qFx`, `_lib.sh:2411`) can never
fire; only its mutual-exclusion half would be used, and its own documented
worst case after five failed acquisitions is an unlocked append anyway
(`_lib.sh:2382-2386`). Against that it brings a hazard specific to this
caller: it registers a bare `trap … EXIT` whose lock file holds `$$`, and
`resume-context.sh` `exec`s — an EXIT trap does not run across `exec`, and the
leaked lock's PID is then the live `claude` process, so the helper's `kill -0`
eviction never fires and the lock survives the whole interactive session.
Stripping the trap does not rescue it: release would have to be hand-written
on all six early-return paths of a function invoked as `|| true`, which is how
locks leak. `handoff-record-conversion.sh:22` is the in-repo precedent for the
lighter primitive (a plain `>>` for one best-effort line), and it appends to
`$CONFIG_DIR`, which can be network-backed; ours cannot, so a single
short-line `printf` under `O_APPEND` is strictly safer here than at the
precedent's own call site. The row-length cap is what keeps that write inside
one `write(2)`, and it is measured in **bytes**, not `${#row}`'s
locale-dependent character count — see the ledger's byte-cap row.

*Retention.* Rows go into one file per UTC day, and stale day-files are
deleted whole by `find "$dir" -maxdepth 1 -type f -mtime +30 -delete`. This is
this repo's canonical retention idiom, already at five state directories
(`nudge-worktree-anchor.sh:167`, `nudge-handoff-near-context-cap.sh:555`,
`nudge-error-mode-analysis.sh:151,176`, `advance-past-commit-stall.sh:207`,
`log-reviewer-round.sh:114,133`), with `review-ledger.sh:87` sweeping its own
per-session ledger files the same way. Deleting a directory entry is not a
read-modify-write, so N concurrent sweeps cannot lose a row: the losers'
`unlink` calls simply fail with ENOENT, which `2>/dev/null || true` absorbs.
The only file a live append targets is *today's*, which by construction is not
31 days old, so the sweep and the hot path never touch the same file at all.

*What this replaces.* A row-level prune requires reading the shared index,
filtering it, and swapping the result in — a read-modify-write whose losers
discard every row appended since their own read. Gating that rewrite on
observed staleness narrows when it runs without changing what it does; two
processes that both observe the same stale row still both rewrite, and the
last `mv` wins. Splitting by day removes the rewrite instead of scheduling it.
It also removes `_lib_resume_context_retention_cutoff` and its GNU/BSD
`date -d`/`date -r` fork, the `awk` staleness gate, the `awk` filter pass, the
`mktemp`+`mv` swap, and the orphaned-`*.prune.*` temp files that swap could
leave behind — the fix is a net deletion, not a new mechanism.

*Retention bound and its residuals.* A row's maximum lifetime is a little over
31 days: `find -mtime +30` fires once a file's age exceeds 31 truncated
24-hour periods, and a row may sit up to a day earlier in its file's span than
that file's final mtime. A uid that stops consuming entirely leaves its last
day-file until OS tmp-cleanup reaps the directory — the same bound the
plan's `/tmp`-lifecycle given already accepts, and the same hole a row-level
prune had, since it too only ran on append. A second sweep call from the
reader would close it, and is deliberately not added: it would be a sixth
hand-duplicated copy of a sweep line this repo's own discovery audit already
flags as duplicated (`docs/reports/2026-08-22-discovery-audit/findings.md:262`,
SC2), for a residual that OS tmp-cleanup already bounds.

*One new failure mode the reader must handle.* Iterating a set of files means
a concurrent consume's sweep can unlink a day-file between the reader's glob
and its `open`. The reader runs under `set -euo pipefail`, where a failed
redirect aborts the script, so each per-file loop ends `done < "$f" ||
continue`. Loop-body state (`MATCHED`, `PRINTED`, `LAST_PRINTED_DEST`)
survives, because a redirect — unlike a pipe — introduces no subshell.

**The reader.** `find-consumed-continuity-file.sh <slug-substring>` sets
`shopt -s nullglob` before iterating — without it, a glob that matches
zero day-files leaves `"$dir"/consumed.*.tsv` unexpanded, and the loop
body's own `done < "$f"` then fails to open a literal, unescaped pattern
string, printing bash's own redirection error to stderr on top of the
intended "no index found" diagnostic (`staff-backend-engineer`,
reproduced under bash 5.2.21). With `nullglob` set, a zero-match glob
disappears from the argument list entirely and the loop body never runs.
It calls the same `_lib.sh` helper for the index directory, iterates
`"$dir"/consumed.*.tsv` in glob order (chronological, since day-file names
are fixed-width ASCII dates), skips any entry that is not a regular
non-symlink file, and walks each file's rows with
`while IFS=$'\t' read -r stamp dest src`, substring-matching `$src` only —
not the whole line, so a numeric slug can't match a timestamp, and no
regex or `--` handling is needed. It prints only rows whose destination
still exists, is a regular file, is not a symlink, and is owned by
`$EUID`. That filter is required for truthfulness, not hygiene: age-based
tmp cleanup sweeps individual destination files on its own schedule,
independent of the index's own 30-day day-file sweep, so a row naming an
already-cleaned-up destination is the normal steady state. It is also the
integrity control on a surface whose output a human or agent then feeds to
`claude --append-system-prompt-file` — an unowned or symlinked destination
is never printed. The append-order contract is unchanged: within a
day-file, write order; across day-files, date order. An empty glob is the
"no index found" diagnosis, which the current code derives from the
absence of a single `consumed.tsv`.

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

- The index lives in `/tmp`, not a durable directory, so OS tmp-cleanup owns
  the outer bound of its lifecycle `[engineer-verified]`. A 30-day
  whole-file sweep runs inside that bound as well, accepted during code
  review in response to `ciso-reviewer`'s unbounded-plaintext-retention
  finding, so task-descriptive slugs are not retained indefinitely on a
  machine whose `/tmp` survives for months.
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
| 1 | The hook performs no move of its own — it shells out to `resume-context.sh --consume-only` and only formats the returned path, so one insertion point in `resume-context.sh` covers both consuming paths | `[verified: consume-durable-continuity-file-on-read.sh:118-134; resume-context.sh:269-292 (line numbers refreshed this round after the prior fix-round commit shifted them ~60 lines — staff-platform-engineer)]` |
| 2 | `mktemp` → `mv` → `chmod 600` at `resume-context.sh:269-290` is shared by launch mode and `--consume-only`; the mode split happens only afterward at line 291 | `[verified: resume-context.sh:269-292]` |
| 3 | Appending before the `chmod` converts the hook's fourth known gap from "neither output channel reports it" into "the index still names it" — the `chmod` failure path exits at line 289 before any later statement would run | `[verified: resume-context.sh:287-290; consume-durable-continuity-file-on-read.sh:77-81]` |
| 4 | `--consume-only`'s stdout contract is exactly one line, the destination; the index append writes to a file and emits nothing on stdout, so the contract is undisturbed | `[verified: resume-context.sh:50-52, 291-292]` |
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
| 19 | Age-based tmp cleanup sweeps individual destinations on its own schedule, so a row naming a destination that no longer exists is the steady state rather than an edge case | `[unverified]` — cleaner policy varies by distro; the design does not depend on it, because the reader existence-checks every row either way. Under day-files a row's own file ages out on a bounded 30-day mtime sweep, so this row is about destination liveness only, not index growth |
| 20 | One shared per-uid index across multiple `CLAUDE_CONFIG_DIR` accounts is correct, not a leak: rows carry absolute source paths that name their config dir, and the destinations already share one tmp root today | `[verified: resume-context.sh:161 — the root is uid-scoped at most, never config-dir-scoped]` |
| 21 | `find <dir> -maxdepth 1 -type f -mtime +30 -delete` is this repo's canonical retention sweep for a self-managed state directory, at five existing sites, and `docs/design-decisions.md:890-892` names the 30-day interval as the repo-wide convention | `[verified: nudge-worktree-anchor.sh:167; nudge-handoff-near-context-cap.sh:555; nudge-error-mode-analysis.sh:151,176; advance-past-commit-stall.sh:207; log-reviewer-round.sh:114,133; review-ledger.sh:87 (glob-filtered variant); docs/design-decisions.md:890-892]` |
| 22 | A staleness-gated `mktemp`+`awk`+`mv` rewrite of a shared append-only index is a lost-update race, not a narrow single-row window: every process observing staleness performs the same whole-file replace, and the last `mv` discards every row appended since its own read — including its own just-completed append | `[verified: staff-backend-engineer-1788597494 review — 12 concurrent --consume-only runs against a pre-seeded 31-day-stale row landed 9/11/6/6/8 of 12 rows across 5 runs; the 2-process case lost a row in 3/30 trials; a control run with no stale row landed 12/12 three times]` |
| 23 | The row-level prune exists only because the index was one file — `docs/scripts.md:152` states that premise outright ("the index file's single-file mtime refreshes on every append and so can't drive a whole-file age sweep"). One file per UTC day dissolves it, since each day-file's mtime stops advancing once its day ends | `[verified: docs/scripts.md:152; resume-context.sh:144-164]` |
| 24 | Under day-files a row's maximum lifetime is a little over 31 days — `find -mtime +30` truncates age to whole 24-hour periods, and a row can sit up to a day earlier in its file's span than that file's last append | `[unverified]` — the exact `find` day-truncation boundary was not run this session; the design needs only "bounded near 30 days," and all five existing sweep sites carry identical semantics |
| 25 | `${#row}` counts characters under a multi-byte locale, so a non-ASCII `$src` can pass a 2048-character cap while writing 4286 bytes — past the ~4096-byte single-`write(2)` threshold the cap exists to enforce. `_lib.sh:369`'s `LC_ALL=C sort` does not transfer as a fix: that is an assignment prefix on an *external* command, where the value reaches a child process's environment, whereas `${#row}` is expanded by the parent shell itself | `[verified: staff-backend-engineer-1788597494 (1400 CJK chars → char_len 1486, byte_len 4286) and staff-platform-engineer-1788597494 (héllo: 5 chars, 6 bytes); _lib.sh:369 for the cited precedent's shape]` |
| 26 | Whether a `LC_ALL=C` prefix on the `[ ... ]` builtin would fix the count at all is unresolved: POSIX simple-command evaluation expands a command's words before performing its prefix assignments, which would leave `${#row}` evaluated under the ambient locale | `[unverified]` — not executed this session, and the chosen `wc -c` form does not depend on resolving it |
| 27 | `flock(1)` has no shell call site anywhere in this repo — the only `flock` references are Python `fcntl.flock` in `transcript-analysis.py` — so adopting it would be a new external-tool dependency needing its own portability justification | `[verified: repo-wide grep for flock]`; that macOS ships no `flock(1)` in its base system is `[unverified]` and load-bearing only for rejecting an alternative |

### Mechanism justification

- **Append inside `resume-context.sh`, not the hook** (`anchors: root`,
  `row1`). Lighter primitives considered: (a) have the hook write the row
  from `$DEST` — rejected, it covers only the Read path and leaves the
  launch path unrecorded, and the hook has no access to the resolved
  source after the legacy-location fallback; (b) a separate PostToolUse
  hook watching for `resume-context.*` creation — rejected, a new hook is a
  wider-scope mechanism for something the moving script already knows for
  free, and it would fire after the fact with no source path at all.
- **Plain `>>` in a `umask` subshell rather than `_lib_append_line_locked`,
  and no lock of any kind** (`anchors: row7`, `row8`). This is the lighter
  primitive; the heavier one is the shared helper, and rows 7-8 record why it
  is heavier *and* worse here. The "helper minus its EXIT trap" variant fails
  for the same reason at one remove: release would have to be hand-written on
  every early-return path of a function invoked as `|| true`, and a leaked
  noclobber lock holding a live `exec`'d PID is exactly row 7's hazard.
- **Retention by whole-file mtime sweep over per-day index files, rather than
  by rewriting rows inside one shared file** (`anchors: row21`, `row22`,
  `row23`). This is the lightest available mechanism, so the
  over-powered-primitive check runs in the rejecting direction — four heavier
  candidates, each rejected: (a) a subshell-scoped `flock` around the prune's
  critical section — rejected on correctness before portability, since it
  serializes prune-against-prune but not prune-against-plain-append, leaving
  the originally documented single-writer loss intact unless every append also
  takes the lock, which reopens row 7; row 27 adds that it would also be this
  repo's first shell `flock` dependency; (b) an out-of-band, single-instance
  sweep triggered from `find-consumed-continuity-file.sh`'s reads — rejected,
  the reader may never run on a machine that only ever consumes, so retention
  would be unbounded in exactly the case `ciso-reviewer` raised, and a
  reader-side rewrite still races concurrent appends; (c) an optimistic
  size-or-mtime recheck of the index between snapshot and `mv` — rejected as
  the compounding-defensive-layer shape this repo's CLAUDE.md names as a
  wrong-foundation tell: it narrows the window the previous layer created
  without closing it, since the recheck is itself a check-then-act; (d) a
  size cap with truncation instead of age-based retention — rejected, it does
  not bound *age*, which is what the retention finding was about, and
  truncation is a read-modify-write again.
- **Row-length cap measured with `wc -c` rather than `${#row}`**
  (`anchors: row25`, `row26`). Lighter primitive considered: an `LC_ALL=C`
  assignment prefix on the existing `[ ... ]` test, as both reviewers
  suggested — rejected because the cited `_lib.sh:369` precedent is a prefix
  on an external command and does not transfer to a parent-shell expansion,
  and because whether the prefix affects `${#row}` at all is row 26's open
  question. A `( LC_ALL=C; [ … ] )` subshell would work only if bash
  re-invokes `setlocale(3)` on assignment to an unexported `LC_ALL`, which is
  a subtler claim across bash 3.2 and 5.x than one `wc` fork per consume is
  worth avoiding.
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
  `_lib_resume_context_index_dir` (creates, guards, chmods, prints the
  directory; returns 1 on an untrusted path; its doc comment is the one home
  for the `consumed.<UTC YYYY-MM-DD>.tsv` day-file naming contract), and
  `_lib_print_recovery_hint` (moved from `resume-context.sh:105-108`).
  **Delete `_lib_resume_context_retention_cutoff` entirely** (this function
  was added post-plan-review, during the code-review fix round this
  revision is undoing) — the row-level cutoff comparison it exists for is
  gone, and with it the GNU/BSD `date -d`/`date -r` fork.
- `claude/.claude/scripts/resume-context.sh` — source `_lib.sh`; the
  `TMPDIR_ROOT` assignment uses `_lib_resume_context_tmpdir_root`; add
  `record_consumed_destination` per the Approach block and call it after the
  `mv` and before the `chmod` on `$DEST`; add one line to the not-found
  branch naming the lookup command with `${SRC##*/}`; `print_recovery_hint`
  delegates to the `_lib.sh` version; header "Destination visibility" block
  gains the index as a third channel and names the 30-day day-file sweep.
- `claude/.claude/scripts/find-consumed-continuity-file.sh` — `shopt -s
  nullglob` before the day-file loop, so a zero-match glob (no index yet,
  or nothing survived retention) drops out of the argument list instead of
  being iterated as a literal unexpanded pattern string, which would
  otherwise print a raw bash redirection error on top of the intended "no
  index found" diagnosis (`staff-backend-engineer`, reproduced this
  round); call `_lib_resume_context_index_dir`; iterate
  `"$dir"/consumed.*.tsv` in glob order; skip any entry failing `[ -f "$f"
  ] && [ ! -L "$f" ]`; end each per-file read loop with `done < "$f" ||
  continue` so a day-file unlinked by a concurrent sweep between glob and
  open cannot abort the script under `set -e`; the "no index found"
  diagnosis becomes "the glob matched nothing." Header contract block
  updated for the day-file shape.
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
  isolation. Retarget every existing index assertion to today's day-file;
  **replace** `test_row_older_than_30_days_is_pruned_on_next_append` and
  `test_row_within_30_days_survives_next_append` with their day-file sweep
  equivalents (Verification item 15); add Verification items 14, 16, and 17.
- `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py` —
  one end-to-end test that a hook-triggered consume also lands a row,
  exercising the delegation rather than re-testing the append; the
  hardcoded `consumed.tsv` path becomes today's day-file.
- `claude/.claude/hooks/tests/test_lib.py` — unit coverage for the three
  new helpers, the directory-mode repair-pass case (Verification item 11),
  and a docstring recording the different-owner reasoning (see mechanism
  item 1's "Different-owner pre-creation" note); retarget the index-path
  tests from `consumed.tsv` to the directory contract; **delete**
  `test_lib_resume_context_retention_cutoff_is_30_days_before_now` along
  with the helper it covers.
- `claude/.claude/skills/tests/test_skills.py` — content assertion that
  `handoff/SKILL.md` and `brief/SKILL.md` both reference
  `find-consumed-continuity-file` after the edit (Verification item 13).
- `claude/.claude/hooks/tests/test_shellcheck.py` — add
  `claude/.local/bin/find-consumed-continuity-file` to
  `KNOWN_EXTENSIONLESS_SHELL_FILES` (lines 40–53).
- `claude/.claude/scripts/tests/test_find_consumed_continuity_file.py` — its
  module docstring and `_index_path` helper hardcode `consumed.tsv`;
  retarget to the directory plus day-file names, and add cross-day ordering
  and per-file symlink coverage.
- `docs/scripts.md` — extend the `resume-context.sh` entry with the index
  write and its guard, and replace the "each append also prunes rows older
  than 30 days" sentence (wherever it landed after the fix-round commit) —
  this sentence must be replaced, not edited, since it states the false
  premise this revision dissolves (ledger row 23: "the index file's
  single-file mtime refreshes on every append and so can't drive a
  whole-file age sweep"). Replacement states that rows land in one file per
  UTC day and that whole day-files older than 30 days are swept by mtime,
  the same sweep this repo's other self-managed state directories use. Add
  a `find-consumed-continuity-file.sh` entry with the same
  bullet-plus-fenced-usage shape the neighbouring entries use, including
  the day-file glob and the cross-file ordering guarantee.
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
- `nudge-worktree-anchor.sh:167` — the exact sweep line, `|| true`
  included, `-type f` included. `advance-past-commit-stall.sh:207` carries
  the identical form; copy it rather than varying the flags.
  `nudge-error-mode-analysis.sh:151,176` sweeps without `-type f` — a
  narrower precedent, not an identical one (`staff-platform-engineer`,
  verified this round) — so it is not the one to copy.
- `log-reviewer-round.sh:104` — the `wc … | tr -d` idiom for stripping BSD
  `wc`'s output padding before an arithmetic comparison.

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
6. Ten sequential consumes on one UTC day produce ten well-formed rows in
   one day-file and no interleaved line. This proves absence of corruption
   *without* concurrency, not the no-lock design's actual claim — see items
   10 and 14.
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
10. **(`ciso-reviewer` + `staff-sdet`)** Two or more real
    `resume-context.sh --consume-only` subprocesses launched concurrently
    against the same `RESUME_CONTEXT_TMPDIR`-scoped index; assert every
    resulting line parses as a well-formed 3-field row with none truncated
    or merged. At least one fixture `$src` must contain non-ASCII path
    components — `staff-platform-engineer` flagged that this test's
    all-ASCII fixtures are why the character-vs-byte cap defect went
    unexercised.
11. **(`staff-sdet`)** A pre-existing index *directory* left at `0755`
    (owned, non-symlink) is tightened to `0700` on the next call to
    `_lib_resume_context_index_dir` — mirrors item 4's file-level repair
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
14. **(`staff-backend-engineer` + `staff-platform-engineer`, BLOCKER
    regression)** Pre-seed a day-file named for a past date and backdate its
    mtime 31+ days, then launch N ≥ 8 concurrent `--consume-only`
    subprocesses. Assert **exactly N rows**, each a well-formed, distinct
    3-field row (not merely a count match — a count-only check cannot tell
    N good rows from N-1 good rows plus one corrupted line), land in
    today's day-file; assert the stale day-file is gone. **What this
    guards, precisely (`staff-sdet`, reworded this round):** under the
    day-file design there is no shared mutable file for a live append and
    a concurrent sweep to race on — the sweep only ever unlinks an already
    day-boundary-separated file, never the one an append targets — so this
    test cannot "reproduce" the originally-found lost-update race in the
    literal sense; it instead regression-guards the specific invariant a
    future edit could reintroduce the race by breaking (today's file
    staying untouched by a concurrent sweep of a different file). The
    original defect for context: the staleness-gated rewrite this revision
    removes landed 9/11/6/6/8 of 12 rows across five runs of this exact
    scenario shape. Run it repeatedly (the 2-process case only failed in
    10% of trials), or at N ≥ 8 where the old design failed on essentially
    every run.
15. A day-file with mtime backdated 31 days is deleted by the next append;
    one backdated 29 days survives it; today's file is never deleted while
    a stale sibling is. Mirrors the existing sweep coverage at
    `test_nudge_worktree_anchor.py:328-358` and
    `test_advance_past_commit_stall.py:741-777`.
16. A `$src` whose character count is under 2048 but whose serialized row
    exceeds 2048 **bytes** produces no row, and the consume still succeeds
    with its normal stdout. Build the path from nested directories of ~78
    multi-byte characters each — a single component must stay under the
    255-**byte** `NAME_MAX`, so roughly nine such components reach ~2100
    bytes. A multi-byte `$src` comfortably under the byte cap still appends
    exactly one row.
17. Two day-files from different dates are both read by
    `find-consumed-continuity-file.sh`, oldest-file rows first, and the
    recovery hint names the newest live destination across both. A
    day-file replaced by a symlink is skipped without aborting the reader,
    and the remaining day-file's rows still print.
18. An index directory containing no `consumed.*.tsv` at all yields the
    "no index found" diagnosis and exit 1 — the day-file replacement for
    the current "`consumed.tsv` is absent" branch. Assert stderr's exact
    content, not just the exit code: without `shopt -s nullglob` this case
    additionally prints a raw bash redirection error
    (`staff-backend-engineer`, reproduced this round), and an assertion
    that only checks exit status would pass with that noise still present.

Item 11's directory-mode repair-pass coverage and item 12's writer/reader
end-to-end contract test both still apply unchanged; item 12 now also
guards the day-file naming contract, since a drift in either side's
`consumed.` prefix or `.tsv` suffix fails it. Item 5's existing
`test_symlinked_index_file_skips_append_but_consume_still_succeeds` also
still applies, retargeted from the index file to today's day-file
(`[ -L "$day_file" ] && return 0` in the writer) — name this explicitly
here (`staff-sdet`) so the retarget pass doesn't silently drop it while
renaming `index_file` references to `day_file` across the test file.

Manual check before commit, since the skill-length gate is a local
`git commit` hook and not in CI:
`wc -l claude/.claude/skills/handoff/SKILL.md` must stay ≤ 200.

## Out of scope

- **Any retention mechanism beyond the 30-day whole-file sweep.** No size
  cap, no row-count cap, no compaction, no rotation by count. The sweep
  bounds age, which is what the retention finding was about; a size cap
  would reintroduce a read-modify-write on a file other processes append to,
  which is the defect this revision removes. Unbounded intra-day row count
  is an accepted residual, not an overlooked one (`ciso-reviewer`): a
  single UTC day's worth of consumes on one uid is bounded by human/agent
  activity, not by an adversary, and each row is already length-capped —
  this is a disk-fill/availability question, not the confidentiality
  question the 30-day age bound exists to close.
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
- **A migration path from the flat `consumed.tsv` this branch's earlier
  commits wrote.** The index has never shipped past this branch and lives in
  `/tmp`; a leftover flat file on a contributor's machine is cruft the
  unfiltered `-type f` sweep collects on its own once it ages out, or that a
  reboot clears. The reader's `consumed.*.tsv` glob does not match
  `consumed.tsv`, so a leftover is invisible rather than misread.
- **Extracting the duplicated 30-day sweep line into a shared `_lib.sh`
  helper.** Real and already recorded as SC2 in
  `docs/reports/2026-08-22-discovery-audit/findings.md:262`, but it changes
  five existing call sites and an off-by-one at the boundary would propagate
  to all of them. Copy the existing line here; raise the extraction
  separately.
- **A second sweep call from `find-consumed-continuity-file.sh`.** It would
  close the "uid stops consuming, last day-file lingers until OS tmp-cleanup"
  residual, at the cost of a sixth hand-duplicated sweep line. The outer
  `/tmp`-lifecycle bound already covers that case.
- **Isolating the index per `CLAUDE_CONFIG_DIR` account rather than per
  uid.** Row 20 already accepts that one shared per-uid index across
  multiple accounts is not a confidentiality-at-rest leak. Left as an
  accepted residual, distinct from that: on a machine running several
  `CLAUDE_CONFIG_DIR`-scoped accounts under one uid, any process running
  as that uid can now substring-search *every* account's consumed slugs
  in one place, which is more convenient than the pre-index status quo of
  grepping destination files by content (`ciso-reviewer`). Out of scope
  because the threat model this plan targets is a different-uid
  adversary on a shared box, not a same-uid one; account-level isolation
  would need its own stated goal and design.

**One judgment call worth an explicit override if the engineer disagrees:**
the reader's stdout is raw TSV rows rather than a single bare destination
path. Rows keep it usable when several handoffs share a slug prefix and
let `cut -f2` recover the machine form; a bare-path contract would be
terser for an agent but forces an arbitrary tie-break when more than one
row matches. Recommendation: keep rows, as specified above.
