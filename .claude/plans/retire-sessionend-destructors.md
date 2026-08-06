# Retire the SessionEnd destructor hooks in favour of writer self-sweeps

## Context

**Goal:** delete the `SessionEnd` cleanup hooks whose work belongs in the hook
that *writes* each marker, matching the pattern the repo already uses in 3 of its
4 existing sweep sites — and close two marker directories that nothing sweeps today.

The prompt was an `/exit` hang sometimes reporting cancelled hooks. Investigating
that surfaced a design problem worth fixing on its own merits: the four
`SessionEnd` hooks perform garbage collection at the most budget-constrained
moment in the session lifecycle — Claude Code gives **all `SessionEnd` hooks a
single shared 1.5s budget**, not 1.5s each ([hooks
reference](https://code.claude.com/docs/en/hooks)) — while **two of the four
marker directories they nominally protect are swept by nothing at all**.

**Honest scoping of the causal claim:** measured this session, the four hooks
cost ~220ms combined (4 bash spawns 83ms + 4 `jq` 50ms + 3 `_lib.sh` sources
~75ms + 1 `ps` 13ms), warm-cache and sequential. The same docs state hooks for an
event "run in parallel," so wall-clock is closer to max-of-one (~60–80ms) than to
the 220ms sum. **That is a modest share of a 1500ms budget, so this change is not
predicted to be the cure for the `/exit` hang.** It is justified by the unswept
directories and the removed complexity. The residual hang may be
[anthropics/claude-code#41577](https://github.com/anthropics/claude-code/issues/41577),
where configured `SessionEnd` timeouts are reportedly ignored outright — an
upstream bug this repo cannot fix.

## Approach

Move each directory's cleanup into the hook that writes into it, then delete the
destructor. This is not a new pattern — it is the repo's existing majority
pattern, extended to full coverage.

**Population check (grep for `mtime +30 -delete`, 4 sweep sites total):**

| Sweep site | Placement |
|---|---|
| `nudge-handoff-near-context-cap.sh:173` | writer self-sweep |
| `nudge-error-mode-analysis.sh:142` | writer self-sweep |
| `nudge-error-mode-analysis.sh:167` | writer self-sweep |
| `cleanup-commit-stall-marker.sh:37` | destructor (the outlier) |

3 of 4 are writer self-sweeps, each placed immediately after `mkdir -p` and
before the marker write. Two carry the comment *"Evict stale markers from
one-shot runs that skipped SessionEnd cleanup"* — the repo has **already**
concluded `SessionEnd` is unreliable and compensated in the writers. This change
finishes that migration rather than starting a new one.

**Current GC coverage, by directory:**

| Directory | Writer sweep? | Destructor? | Disposition |
|---|---|---|---|
| `.handoff-nudge-fired.d` | yes | yes (redundant) | delete destructor |
| `.commit-stall-block.d` | no | yes | move sweep to writer, delete destructor |
| `.worktree-anchor-nudge.d` | **no** | no — *leaks* | **add** writer sweep, delete destructor |
| `~/.claude/sessions` | no — co-owned, must not sweep | content-match, no sweep | delete destructor, add nothing |

### `~/.claude/sessions` is co-owned and must not be swept

This directory is shared with Claude Code itself, which writes `<pid>.json`
sidecar files alongside this repo's bare `<pid>` lookup files.
`test_cleanup_session_id.py::test_cleanup_preserves_pid_json_sidecar` exists
specifically to assert the hook "deletes a single exact path … and never globs
the directory." **A `find`-based sweep here would delete Claude Code's own
files** — so the writer-self-sweep pattern does not transfer to this one
directory, and no sweep is proposed for it.

`cleanup-session-id.sh` is therefore deleted with no replacement sweep. The
resulting leak is accepted as self-limiting: bare `<pid>` files are ~40 bytes,
bounded by PID space, and `capture-session-id.sh` overwrites on PID reuse.
`SessionEnd` ends up with zero registered hooks.

### Security: `cleanup-session-id.sh`'s deletion has a side effect beyond disk cleanup

**Previously-settled, now reopened twice** (ledger rows 6, 11, 12) — CISO review
found this destructor's actual function is not byte reclamation but
invalidating the `session_id`→PID binding in `~/.claude/sessions/<pid>` before
that PID can be reused. `marker.sh:42`'s `_walk_session` resolves the *current*
session's id by reading that file for the Bash tool's ancestor PID; a stale
entry there resolves the *wrong* session's id, which then keys review-gate
bypass markers (`.plan-review-active.d/<id>`, `.ready-for-review-active.d/<id>`,
etc.).

**Not a hypothetical the deletion introduces from scratch — a real,
currently-observable gap the destructor only partially covers.** Measured live
on this machine, with the destructor still running today: 62 stale entries in
`~/.claude/sessions/`, 18 of which are PIDs currently reused by live processes.
The repo's own test suite already demonstrates the resulting primitive
(`test_marker_script.py:523-541`, `test_activate_ignores_stale_session_entry`):
seed `sessions/<pid>` with a foreign session id, and `marker.sh activate`
writes a bypass marker under that foreign id, stamped with a live PID. Chained
through `--resume` (which preserves session id), this reaches a session
inheriting another session's review-gate bypass. Full path in CISO review's S1
finding.

**First attempt (rejected on its own re-review):** age-bound the active-bypass
markers themselves, reusing `require-routing-read.sh:56`'s `find -mmin -60`
pattern. A follow-up CISO pass caught that this doesn't work as intended:
`capture-session-id.sh:75-81` rewrites every `.*-active.d/<own-session-id>`
entry's content (and therefore mtime) on *every* `SessionStart` — which fires
on `--resume` with no matcher — precisely the moment the exploit chain above
depends on. The bound would reset its own clock at the point of attack, mostly
defeating it. Chasing this with a second patch (embed an activation timestamp
in the marker's own content instead of relying on mtime) would have been a
*third* layer bounding the same underlying conflation — the compounding-layers
pattern this repo's own CLAUDE.md names as a wrong-foundation tell. Stepped
back instead of patching again.

**What shipped instead — fix the actual conflation, not its consequences.**
The root cause is that `sessions/<pid>` keys an entry by **PID number**, but a
PID number is not a stable identity — it gets reused. Nothing needs to *bound*
the resulting staleness if the staleness is detectable at the point of read.
Every process has a start time; a reused PID is trivially a *different*
process with a *different* start time. `capture-session-id.sh` now records
that start time alongside the session id, and `marker.sh:_walk_session`
verifies it matches the live process's *current* start time before trusting
the entry — this is the same technique operating systems use internally to
distinguish PID-reuse generations (Linux's `/proc/<pid>/stat` start-time field
serves exactly this role for pidfd-less callers).

This closes the resolution ambiguity itself rather than bounding its
consequence: a reused-PID entry self-invalidates the moment anything tries to
read it, with no clock, no window, and — because it removes the reason
`cleanup-session-id.sh` mattered in the first place — **no compensating
control needed at all**. The original plan (delete all four `SessionEnd`
hooks, accept only the disk-space "leak") is now correct as originally stated,
not merely accepted as a tradeoff. Net effect versus where this review started:
less code changes, not more, and the security property is real instead of
time-boxed. Mechanism and file list under "Fix the `sessions/` resolution
ambiguity" below.

**Residual, explicitly accepted:** a same-second PID-reuse coincidence, where a
dying and a new process both start within the same wall-clock second at
second-resolution (`ps -o lstart=`) *and* the OS assigns the exact same PID
number to the new one, would still collide. Astronomically less likely than
today's unbounded window; not chased further.

### Why not the lighter alternatives

- **Add `timeout` to the four `SessionEnd` entries** (config-only, lightest).
  Rejected: leaves `.worktree-anchor-nudge.d` leaking, keeps four process spawns
  at teardown, and GH#41577 reports configured `SessionEnd` timeouts being
  ignored, so it may buy nothing.
- **Consolidate four destructors into one** (no writer changes). Rejected: still
  parks GC on the 1.5s budget and still leaves `.worktree-anchor-nudge.d` unswept.

### Complexity this dissolves

`cleanup-session-id.sh` carries a content-match guard (delete only if the file
still holds the *ending* session's id) that exists solely to survive `/clear`,
where `SessionEnd`(old) and `SessionStart`(new) race over the same
`sessions/<pid>` path. Delete the destructor and the race has no second party —
`capture-session-id.sh` simply overwrites. This is a defensive layer that exists
only because of the layer beneath it.

### Assumption ledger

Root problem: *GC for per-session marker files is placed in destructors on a
1.5s shared budget, yet one target directory is swept by nothing.*

| # | Assumption / mechanism | Anchor | Tag |
|---|---|---|---|
| 1 | `SessionEnd` hooks share one 1.5s budget, raised only by a per-hook `timeout` (max 60s) | root | `[verified: code.claude.com/docs/en/hooks]` |
| 2 | Hooks for one event run in parallel, so wall-clock ≈ max, not sum | root | `[verified: code.claude.com/docs/en/hooks]` |
| 3 | The four hooks cost ~220ms warm/sequential | root | `[verified: measured this session]` |
| 4 | Writer self-sweep is the repo's majority pattern (3 of 4 sites) | root | `[verified: grep mtime +30 -delete]` |
| 5 | `.worktree-anchor-nudge.d` has no sweeper anywhere | root | `[verified: grep across hooks/ + scripts/]` |
| 6 | All four destructors are GC-only in what they store; **amended by CISO review** — `cleanup-session-id.sh`'s *removal* has a security-relevant side effect its stored-state audit didn't measure: it flips `marker.sh`'s session-id resolution from fail-closed-on-absence to fail-open-on-staleness (see security section below) | root | `[verified: read all four for stored-state; CISO review corrected the removal-side-effect claim]` |
| 7 | Gate-bypass markers (`.plan-review-active.d` etc.) are **not** touched by these four — they are `marker.sh clear-stale`'s job | row 6 | `[verified: grep; session-marker-dashboard.sh:59-61]` |
| 8 | `~/.claude/sessions` is co-owned with Claude Code (`<pid>.json` sidecars); no sweep may be added there | row 4 | `[verified: test_cleanup_session_id.py:108-122]` |
| 9 | Deleting the four `SessionEnd` registrations breaks no test — nothing parses the `SessionEnd` array | root | `[verified: subagent survey of tests/]` |
| 10 | Removing these hooks measurably shortens the observed `/exit` hang | — | `[unverified]` — rows 2–3 argue against; GH#41577 suggests upstream cause |
| 11 | Audit finding (row 6) contradicted the engineer's prior that "some of those hooks are essential"; engineer reviewed and approved deleting all four | root | `[engineer-verified]` — **re-closed, this time on solid ground**: the `sessions/` fix (row 13) closes the resolution ambiguity itself, so all four delete cleanly with no compensating control required. |
| 12 | Accepting the `sessions/` leak in exchange for deleting its destructor | row 8 | `[engineer-verified]` — **re-closed**: with row 13's fix, the leak is disk-space only again (bytes, self-limiting by PID space) — the security dimension CISO raised is resolved at the source, not bounded around. |
| 13 | Fix: record each PID's process start time in `sessions/<pid>` alongside the session id; `_walk_session` verifies it matches the live process's current start time before trusting the entry — a reused PID's stale entry self-invalidates at read time | root | `[engineer-verified]` — chosen after the age-bound approach (see security section) was found broken by its own interaction with `capture-session-id.sh`'s existing PID-refresh loop. Design re-reviewed by CISO ("Approve with concerns") and SDET ("Request changes"); every blocker from both (locale/TZ pinning, named invariant, 14-site test blast radius, writer-side test update) is incorporated below, not deferred. |
| 14 | `ps -o lstart=` is stable and comparable within a single environment | row 13 | `[verified: two reads of the same live PID, same ambient TZ/locale, returned identical output — this does NOT cover cross-environment drift; see row 15, which found and closed that gap]` |
| 15 | Writer (`sh`-shim SessionStart hook) and reader (user-profile Bash tool shell) can run under different ambient `TZ`/`LC_TIME`, making unpinned `lstart` diverge and every entry mismatch forever | row 13 | `[verified: CISO review measured 3 distinct renderings of the same instant under 3 ambient locale/TZ settings]` |
| 16 | Explicitly prefixing `TZ=UTC LC_ALL=C` at both `ps -o lstart=` call sites neutralizes row 15 regardless of ambient drift | row 15 | `[verified: measured directly — simulated writer env (TZ=America/New_York, LC_ALL=fr_FR.UTF-8) and reader env (TZ=UTC, LC_ALL=C) both produced byte-identical pinned output]` |
| 17 | Test-suite blast radius of the two-line format change is 14 call sites across 10 files, not the single helper the first draft named | row 13 | `[verified: CISO and SDET review independently enumerated the same site set]` |

## Critical files

**Add a self-sweep — use `cleanup-commit-stall-marker.sh:36-37`'s guarded shape,
NOT `nudge-handoff-near-context-cap.sh:171-173`'s bare one:**

```bash
if [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ]; then
  find "$STATE_DIR" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true
fi
```

Platform-engineer and SDET review independently flagged that the bare reference
shape drops two guards the destructor has today (`-type f`, `[ -d ] && [ ! -L ]`).
Verified directly against `cleanup-commit-stall-marker.sh` and the test that pins
the symlink guard (`test_sweep_skipped_when_state_dir_is_a_symlink`) — confirmed
real. The platform-engineer additionally claimed the bare shape can `rmdir` the
state directory itself once emptied (directory mtime old enough, `-delete`
implying `-depth`), which would silently break `advance-past-commit-stall.sh`'s
turn-gate on the very turn it should fire. I attempted to reproduce this directly
and could not — but the sandbox's `find` resolves to `bfs` (a non-standard
reimplementation), not the GNU find this repo's CI runs (`ubuntu-24.04`) or the
real BSD find installed on a contributor's Mac, so that non-reproduction proves
nothing either way. The guarded shape above closes the entire hazard class
regardless of which `find` implementation is right, at zero cost — adopt it
without resolving the dispute.

- `claude/.claude/hooks/advance-past-commit-stall.sh` — insert after the existing
  `mkdir -p "$STATE_DIR"` at line 198. *Relocates* the sweep currently at
  `cleanup-commit-stall-marker.sh:37`; net behaviour preserved (with the guards
  above, unlike the bare shape).
- `claude/.claude/hooks/nudge-worktree-anchor.sh` — insert after `mkdir -p "$STATE_DIR"`
  at line 154. This runs only on a *transition into* the drifted state (the
  content-match dedup at line 145 gates it), not on every prompt — say so in the
  hook's own comment rather than leaving it implied. **New coverage — fixes a
  current leak.**

Each writer hook's own header comment must gain a line explaining the sweep's
presence, matching `nudge-error-mode-analysis.sh:165-166`'s precedent ("Gated on
fire… because markers are themselves only ever written here") — today neither
hook's header mentions cleanup at all; that knowledge lives solely in the
destructor being deleted.

**Delete** (hook + its dedicated test file):

| Hook | Test file | Tests removed |
|---|---|---|
| `cleanup-handoff-nudge-marker.sh` | `tests/test_cleanup_handoff_nudge_marker.py` | 8 |
| `cleanup-worktree-anchor-nudge-marker.sh` | `tests/test_cleanup_worktree_anchor_nudge_marker.py` | 7 |
| `cleanup-commit-stall-marker.sh` | `tests/test_cleanup_commit_stall_marker.py` | 12 |
| `cleanup-session-id.sh` | `tests/test_cleanup_session_id.py` | 7 |

All four go, so the `SessionEnd` array empties and the key is removed outright,
now unconditionally — the `sessions/` fix above resolves the security question
that previously made `cleanup-session-id.sh` contingent. One coverage gap this
specific deletion still opens on its own, independent of the security fix
(deleting the test file removes the closest thing to a warning system for it):

- **Co-ownership guard anchor** (SDET finding): `test_cleanup_session_id.py`'s
  `test_cleanup_preserves_pid_json_sidecar` is ledger row 8's cited verification
  anchor ("`~/.claude/sessions` is co-owned with Claude Code; no sweep may be
  added there"). Deleting the file removes that anchor with nothing replacing
  it. Add a test on `capture-session-id.sh` (the writer — the place a future
  contributor would naturally add a sweep) asserting a `<pid>.json` sidecar in
  `~/.claude/sessions` survives whatever `capture-session-id.sh` does.

(The foreign-id resolution invariant CISO's S3 finding wanted is now covered
by the new test under "Fix the `sessions/` resolution ambiguity" above, as a
closed invariant rather than a documented gap.)

Of the four sweep-behaviour tests in `test_cleanup_commit_stall_marker.py`, SDET
review found only one ports as-is; **port three, drop one**, into
`test_advance_past_commit_stall.py`, and mirror the same three for
`nudge-worktree-anchor.sh`:

- `test_sweep_deletes_entries_older_than_30_days` — ports as-is (failure is loud
  either way).
- `test_sweep_preserves_entries_within_30_days` — ports, but **must add an
  assertion that the fire actually happened** (e.g. a `{"decision":"block"}`
  payload for the stall hook, a non-empty context for the anchor hook) alongside
  the survival assertion. Without it, the test stays green if a fixture drift
  stops the hook from firing at all — the sweep line never runs, and the test
  can't tell.
- `test_sweep_skipped_when_state_dir_is_a_symlink` — ports, but **re-point the
  assertion at the symlink itself**, not at files through it. Verified (both
  reviewers, independently): `find <symlink> -maxdepth 1 -delete` does not
  follow a symlinked starting point regardless of the `[ ! -L ]` guard — POSIX
  `-P` default behavior, universal across `find` implementations. What the guard
  actually prevents is deletion of the *symlink itself* once its own mtime ages
  past 30 days; the test must assert that, or it passes against a guard-free
  implementation and proves nothing.
- `test_sweep_confined_to_state_dir_when_absent` — **do not port.** Its stated
  invariant ("the sweep must not create the dir if absent") cannot hold at
  either new site: both writer hooks unconditionally `mkdir -p "$STATE_DIR"`
  immediately before the sweep runs, so the directory always exists by the time
  the sweep executes. The invariant is inverted by the relocation, not merely
  untestable. (The existing test also turned out to assert nothing beyond
  `returncode == 0` relative to its own docstring — drop it rather than port a
  vacuous check.)

**Fix the `sessions/` resolution ambiguity:**

**Named invariant** (both CISO and SDET review required this be stated
explicitly, not left implicit in code): *a `sessions/<pid>` entry with no
recorded start time — every entry that predates this change, and any entry a
future edit fails to write correctly — is untrusted and must fail closed,
falling through to the next ancestor exactly as if the file were absent.* This
is the line an implementer must not "fix" by loosening the check to make old
seed-format tests pass; the required test-suite rework below exists precisely
so that pressure doesn't arise.

**Locale/TZ hazard, found and closed in the same round.** A first draft of
this fix used `ps -o lstart=` unpinned. CISO review measured `lstart`'s
rendering as sensitive to the calling shell's ambient `TZ`/`LC_TIME` — and the
writer (`capture-session-id.sh`, spawned via a `sh` shim at `SessionStart`)
and the reader (`marker.sh`, run under a Bash tool shell initialized from the
user's own profile) are two different environments that can set these
differently. Unpinned, any such divergence makes *every* entry mismatch
forever, which — because this sits upstream of every `require-*.sh` gate via
`_walk_session` — would make every review gate unpassable for that user,
misattributed to "capture-session-id.sh SessionStart hook did not run"
(`marker.sh:49`'s existing error text). **Verified the fix**: explicitly
prefixing `TZ=UTC LC_ALL=C` at both `ps` call sites produces identical output
regardless of ambient drift — confirmed by simulating a writer environment
(`TZ=America/New_York`, `LC_ALL=fr_FR.UTF-8`) and a reader environment
(`TZ=UTC`, `LC_ALL=C`) and getting byte-identical `lstart` strings from both,
because the explicit prefix overrides whatever the calling shell inherited.
Every `ps -o lstart=` call below carries this prefix; it is load-bearing, not
cosmetic.

- `claude/.claude/hooks/capture-session-id.sh` — after resolving `CLAUDE_PID`
  (existing code at line 55), resolve its start time and fail the same way an
  unresolvable PID already does if `ps` can't provide one:
  ```bash
  CLAUDE_PID_START=$(TZ=UTC LC_ALL=C ps -o lstart= -p "$CLAUDE_PID" 2>/dev/null)
  if [ -z "$CLAUDE_PID_START" ]; then
    echo "[capture-session-id] could not resolve start time for claude PID $CLAUDE_PID; respond-pr skill will fail at Step 0" >&2
    exit 0
  fi
  ```
  Change the write at line 66 from `printf '%s\n' "$SESSION_ID"` to
  `printf '%s\n%s\n' "$SESSION_ID" "$CLAUDE_PID_START"` — two lines, session id
  then start time.
- `claude/.claude/scripts/marker.sh` — in `_walk_session` (line 31), replace
  the bare `cat` read with a two-line read plus a start-time comparison before
  trusting the entry. Read via `[ -r ]`, not `[ -f ]`, so an existing-but-
  unreadable file is treated as absent up front rather than reaching the
  redirect and leaking a "Permission denied" to stderr (CISO finding 3):
  ```bash
  local sid pid recorded_start current_start
  sid=""
  if [ -r "$HOME/.claude/sessions/$pid" ]; then
    {
      IFS= read -r sid
      IFS= read -r recorded_start
    } < "$HOME/.claude/sessions/$pid" 2>/dev/null
    if [ -n "$sid" ] && [ -n "$recorded_start" ]; then
      current_start=$(TZ=UTC LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null)
      [ "$current_start" = "$recorded_start" ] || sid=""
    else
      sid=""
    fi
  fi
  ```
  (`recorded_start`/`current_start` declared `local`, per this repo's shell
  conventions — CISO caught the omission.) A mismatch, an old-format single
  line with no `recorded_start`, or an empty file all clear `sid`, so the
  existing `if [ -n "$sid" ]` below falls through to the next ancestor exactly
  as if the file were absent — no other line in the function changes.
- **Consumer impact** (B2) — every caller of `_resolve_session_id` /
  `_resolve_claude_pid` (all four `marker.sh` subcommand arms: `write`,
  `activate`, `deactivate`, plus the `respond-pr` skill's Step 0 lookup) goes
  through `_walk_session`, so all inherit the fix with no per-caller change.

**Test-suite rework — larger than "update one helper."** Both CISO and SDET
review independently found the same defect in this plan's first draft: it
claimed `_seed_session` was one shared helper at `test_marker_script.py:517`.
It is not. Verified count: **four** independent class-local `_seed_session`
copies in that one file (lines 83, 149, 331, 517) plus a direct inline seed
(~680), and **ten more** call sites across nine other files that seed
`sessions/<pid>` with bare content and then drive `marker.sh` through it —
`test_marker_worktree_keying.py:56`, `test_require_plan_review.py:581,620,656,758`,
`test_require_memory_skill.py:179,204`, `test_require_skill_review.py:311,351,389`,
`test_require_ready_for_review.py:708,749,832`, `test_require_respond_pr.py:689,920`,
`test_require_code_review.py:218`, `test_require_routing_read.py:88`. Left as
bare content, every one of these becomes an "old-format leftover" under the
named invariant above and its test goes from asserting success to asserting
exit 2 — most will fail loudly, but `TestMarkerScriptSessionIdValidation`
(`test_marker_script.py:83`, 14 parametrized tests of the path-traversal
chokepoint) would **pass for the wrong reason**: resolution now fails on the
missing start-time line before `_lib_valid_session_id_component` is ever
reached, so the security-control tests stay green while no longer testing
that control — the single most dangerous shape of test rot for this diff to
introduce.

**Required:** extract one shared seeding helper into `claude/.claude/hooks/tests/conftest.py`
that writes `session_id\n<TZ=UTC LC_ALL=C ps -o lstart= for the given pid>\n`,
and re-point all fourteen call sites above at it — a net reduction in
duplicated seed logic across the four in-file copies, not just a mechanical
find/replace. `ps -o lstart=` carries trailing whitespace before the newline
(verified on this machine); the helper must capture it byte-for-byte via
`$(...)`, not `.strip()` it, or every seeded entry mismatches its own writer.

**New tests required**, none of which existed in the first draft's one-test plan:
- Mismatched start time (the original ask) — seed `sessions/<live-pid>` with a
  session id and a start time that does not match that PID's actual `lstart`;
  assert `activate` fails to resolve rather than writing a bypass marker under
  it. This is the test that pins CISO's S3 finding closed.
- Old-format single-line leftover (no second line) — assert it resolves as
  absent, not a crash and not a false match. Highest-probability real input
  this change will see: every entry on disk at merge time is exactly this
  shape.
- Empty file / empty first line — same treatment, same assertion.
- Writer/reader round-trip — run `capture-session-id.sh` for real, then
  `marker.sh activate`, assert it resolves successfully. Pins the two-script
  format as a tested contract instead of two independent implementations that
  happen to agree today.
- `capture-session-id.sh`'s new `CLAUDE_PID_START` resolution-failure branch —
  currently untested.
- Differing writer/reader locale — reproduce the CISO-found hazard directly:
  run the writer under one `TZ`/`LC_ALL` and the reader under another, assert
  resolution still succeeds (guards the pin itself from silently regressing).

- `claude/.claude/hooks/tests/test_capture_session_id.py:39` — asserts the
  written file's content equals the bare session id via `.strip()`; update for
  the two-line format (CISO/SDET both caught this; the plan's original writer-side
  survey never named it).

**Update:**

- `claude/.claude/settings.json` — remove the `SessionEnd` entries (lines 158–192)
  and the now-empty `SessionEnd` key itself, rather than leaving `[]`.
- `tests/test_lib.py` (~line 623) — its docstring hardcodes "Ten hooks currently
  qualify"; SDET review re-ran the count and found it already stale today (**11**,
  not ten) even before this change, dropping to **8** post-deletion. Fix the
  docstring to the correct post-deletion count, not a re-derivation of the
  currently-wrong one. The assertion itself is unaffected either way.
- `capture-session-id.sh`'s header comment, which cross-references the
  destructor's `/clear` ordering contract.

**Doc sites that go stale (surveyed).** Note `test_hook_alignment.py::test_hook_documented_in_hooks_md`
checks hook→doc only; **there is no reverse test**, so stale doc entries for
deleted hooks fail silently and must be removed by hand:

- `README.md:163,165,167,170` — four rows in the hook table.
- `docs/hooks.md:31,32,34,37,39` — the canonical descriptions. `:32` is the
  densest block (PID walk, `/clear` content-match race, "would grow … without
  bound"); `:31`'s "the create and delete form a lifecycle pair" sentence must be
  rewritten. `docs/hooks.md:125` describes `sessions/` staleness semantics that
  loosen once nothing deletes the lookup file on exit — reword, don't delete.
- `docs/handoff-nudge.md:12,66` — `:66` is a Known-limitations bullet built
  entirely on the `SessionEnd`-vs-`claude -p` asymmetry; the writer self-sweep
  largely dissolves it, so rewrite rather than rename.
- `docs/commit-stall-block.md:71` — attributes the 30-day sweep to the destructor;
  re-point at `advance-past-commit-stall.sh`.

**Precedent for the commit message (not new doc text):** `docs/error-mode-nudge.md:36`
already documents this exact model for the error-mode nudge, and a prior plan
(`.claude/plans/durable-handoff-location.md:223`) records rejecting SessionEnd GC
because "`SessionEnd` doesn't even fire reliably for `claude -p`."

## Verification

1. `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/`
   from the main worktree root (`../../../.venv/bin/…` from a linked worktree).
2. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
3. Ported sweep tests must pass in their new homes — specifically that a
   backdated (`touch -t`, 60 days) marker is evicted, a fresh one survives (with
   the fire-assertion added), and an aged symlink itself is skipped (assertion
   re-pointed at the symlink, not at files reached through it).
4. Manual: drive each of the two writer hooks through its fire path with a
   synthetic stdin payload; confirm the backdated dummy is gone and the current
   session's marker is not. This step is valid from the implementation worktree
   (invokes the worktree's own copies by path).
4a. `sessions/` resolution fix — the six new tests named above, plus: run the
    **full** test suite (not just `test_marker_script.py` — all fourteen
    re-pointed call sites, across all ten files) to confirm the shared
    `conftest.py` seeding helper didn't silently change behavior any test
    depends on, with particular attention to `TestMarkerScriptSessionIdValidation`
    (`test_marker_script.py:83`) actually exercising `_lib_valid_session_id_component`
    again, not short-circuiting on a missing start-time line.
5. Behavioural — **run only from the main tree after `git pull`, not from the
   implementation worktree.** `~/.claude/hooks` is a folded *directory* symlink
   into the main worktree (platform-engineer review, confirmed inode-identical);
   a session run from `.claude/worktrees/<branch>/` still executes the live
   (old) hooks and old `settings.json` regardless of what the worktree contains,
   so this step would silently confirm nothing. After merge: start a session,
   `/exit`, confirm no cancelled-hook message, and that `.worktree-anchor-nudge.d`
   no longer accumulates across session cycles. Expect two harmless transients,
   both self-resolving on next session start, not regressions:
   - A session that read the old `settings.json` before the pull may report a
     missing-hook error on its own `/exit` (merge-window `SessionEnd` gap).
   - **Every** `sessions/<pid>` entry on disk — 62 measured on this machine,
     including any in-flight session's own entry — is old-format at the moment
     of merge (CISO finding). Under the named invariant, old-format reads as
     untrusted, so a session already running across the pull loses
     `marker.sh` self-resolution (its own `/respond-pr` Step 0, `activate`,
     etc. return exit 2) until its next `SessionStart`/`--resume`, which
     rewrites its own entry in the new format. Restarting, not just
     continuing, clears it.

**Rollback:** clean. Because `~/.claude/hooks` is a folded *directory* symlink
into the main worktree (not per-file symlinks), a `git revert` on the merge
commit restores every deleted hook and the `settings.json` entries with no
re-run of `./install.sh` — confirmed by platform-engineer review. Residual
marker-directory entries are GC-only regardless of rollback state.

## Out of scope

- **The startup hang.** Unrelated to `SessionEnd`; `SessionStart` command hooks
  get ~600s, not 1.5s. Next diagnostic is the user running `claude --bare`
  (skips hooks, MCP, plugins, prefetches, keychain reads) versus `--safe-mode`.
- **`set-session-title-from-branch.sh`'s git calls.** Separate question; its git
  plumbing is load-bearing (main-worktree basename, default-branch skip), not
  vestigial, and it is local-only so not implicated in either hang.
- **`.commit-stall-block.d` is missing from `.gitignore`** (siblings at
  `.gitignore:97,107,110` are present). Pre-existing gap, noted not fixed.
- Implementation must run inside a linked worktree per this repo's worktree
  enforcement.
