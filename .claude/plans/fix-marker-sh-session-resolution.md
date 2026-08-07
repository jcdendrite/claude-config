# Fix capture-session-id.sh: key the session lookup file to claude's own PID

## Context

**Goal:** make `capture-session-id.sh` write its session lookup file under the
`claude` process's own PID instead of claude's parent, so `marker.sh` resolves
session identity reliably and the review gates (`/code-review`,
`/plan-review`, `/ready-for-review`, `/respond-pr`, and the `.*-active.d`
bypass mechanism) stop silently misbehaving.

An investigation was opened because `marker.sh` aborted with
`SESSION_ID empty — capture-session-id.sh SessionStart hook did not run`,
deadlocking a live session's gates. Two hypotheses were offered up front and
**both are wrong**: it is not a one-time live-migration artifact from PR #571,
and it is not a `CLAUDE_CONFIG_DIR` resolution mismatch (the variable is set
to the same path as the default, and writer and reader both resolve it
through `_lib_config_dir`). The defect is a permanently wrong PID, present on
every session.

`capture-session-id.sh:67` derives the Claude PID as
`ps -o ppid= -p "$PPID"` — the hook's *grandparent* — on the premise stated in
its header (lines 19–22) that "this hook is invoked through a transient `sh`
shim, so its `$PPID` is that shim, not claude." **That premise is false in
Claude Code 2.1.x:** the hook's `$PPID` *is* the `claude` process. The extra
hop lands the lookup file on **claude's parent login shell**.

Measured across every live `claude` process on the development machine:
**zero** have a lookup file at their own PID; nearly all have one at their
parent shell's PID. For one session, `claude` had PID 98601 with start time
`02:42:08` while its parent `bash` had PID 98124 with start time `02:42:02`;
`sessions/98124` held that session's id paired with `02:42:02` — the
**shell's** start time. `sessions/98601` did not exist.

Four consequences, all confirmed live:

1. **Bypass markers store the wrong process.** `marker.sh:279–299` writes the
   resolved PID into `.*-active.d/<sid>`, and `_lib.sh:736` liveness-checks it
   with `kill -0`. Verified by activating a marker during this planning
   session: it recorded the login shell, not claude. Of 6 existing bypass
   entries, 3 name live login shells — those gates are bypassed indefinitely,
   because the shell outlives claude and `kill -0` keeps reporting it alive.
2. **PR #571's staleness guard protects the wrong PID.** #571 added a
   recorded-vs-live `ps -o lstart=` comparison so a reused PID
   self-invalidates. It compares the *shell's* start time, so it is inert
   against the claude-PID reuse it was built for.
3. **Cross-session clobbering.** One long-lived shell hosting sequential
   sessions rewrites a single file. One observed entry records a start time
   from the previous day (the shell's) with an mtime from the next
   (a later session's write).
4. **The reported failure.** The walk in `_walk_session` starts at the Bash
   tool and climbs. Because the file sits *above* claude, resolution depends
   on ancestors beyond claude still being alive and unrecycled. When that
   chain breaks — parent shell exited and claude reparented — the walk
   reaches PID 1 and aborts. `sessions/1` exists on disk, which is this
   off-by-one landing on `launchd`.

**Intended outcome:** the lookup file sits at claude's own PID, where the
reader's walk always terminates.

## Approach

**Fix the writer; change nothing in `marker.sh`.**

Derive the PID as **`$PPID`, with `$CLAUDE_PID` accepted only when it
validates** — validate-then-select, never select-then-validate:

```bash
claude_pid=$PPID
if [ -n "${CLAUDE_PID:-}" ] && [[ $CLAUDE_PID =~ ^[0-9]+$ ]] && _is_parent_or_grandparent "$CLAUDE_PID"; then
  claude_pid=$CLAUDE_PID
fi
```

The ordering is load-bearing. `${CLAUDE_PID:-$PPID}` substitutes before any
check runs, so a whitespace-only value would be selected and could then only
be *rejected* — and on this hook's fail-open contract a rejection writes no
lookup file at all, reproducing the exact deadlock this plan fixes. Validating
first means an unusable `CLAUDE_PID` falls back to `$PPID` instead of
aborting. The output variable is lowercase so the env value stays readable for
the diagnostic.

**Acceptance is bounded to one hop, not to "any ancestor."** The login shell,
`login`, and the terminal emulator are all ancestors, and all outlive the
session — accepting one would reproduce the present bug deliberately, giving a
bypass marker whose `kill -0` never fails. `$CLAUDE_PID` is accepted only when
it equals `$PPID` (the observed case) or is `$PPID`'s immediate parent (the
shim case the original code was written for). Everything above that is
rejected. In the observed topology the first arm is a string comparison with
**zero `ps` forks**, so the net cost is one *fewer* `ps` call per invocation
than today — which matters because this hook fires on every subagent start,
not once per session.

**Why this alone is sufficient**, and why the reader needs no change: the Bash
tool that runs `marker.sh` is a descendant of `claude`. Once the file is keyed
to claude's own PID, the walk finds it in one or two hops and **stops there**.
It never needs the login shell, `login`, the terminal emulator, or `launchd`.
Consequence 4 exists *only* because the file sits above claude; moving it down
removes the dependency on that upper chain entirely. Consequences 1–3 close
for the same reason — the resolved PID becomes claude's, which dies with the
session, so `kill -0` and #571's start-time guard both begin measuring the
process they were written for.

**Alternatives set aside.**

- *Read `$CLAUDE_CODE_SESSION_ID` in `marker.sh` (env-first).* Initially
  chosen, then withdrawn on review evidence. Two costs: (a)
  `test_enforce_marker_script_shape.py:433` documents that the
  `FOO=bar marker.sh activate …` env-prefix form is **deliberately ungated**
  by the shape hook, so env-first would make
  `CLAUDE_CODE_SESSION_ID=<any-id> marker.sh activate plan-review` write a
  bypass marker under an arbitrary identity through a form the suite blesses;
  (b) `SessionStart` fires on `/clear` with a new session id in the *same
  process*, and the gates read `session_id` from the hook stdin payload — if
  the exported variable does not update in-process, `marker.sh` would write
  under a stale id while the gate looks up the new one, a divergence no
  fallback can cover. The lookup file has neither problem: it carries the
  payload id by construction, and the hook re-fires on `/clear`.
- *Resolve claude by process name (`first ancestor whose comm is 'claude'`).*
  Rejected as unportable. BSD/macOS `comm` is argv[0] verbatim (full paths and
  leading `-` observed); GNU/Linux `comm` is the exec basename truncated to 15
  characters, which for this install would read `claude.exe`, not `claude`.
  A stow user on Linux or WSL would get a silent no-match and fail open.
- *Read Claude Code's own `sessions/<pid>.json` registry.* It carries pid,
  sessionId and procStart, and looks like a clean replacement — two reviewers
  independently proposed it. But it covers only a **subset** of live claude
  processes (measured twice, partial both times), so it cannot be the sole
  source. Usable later as a cross-check, not as the mechanism.

### Assumption ledger

**Root problem:** the session lookup file is keyed one process hop above
`claude`, so the resolved PID is wrong on every session and resolution fails
outright whenever the chain above claude is unreachable.

| # | Assumption | Tag |
|---|---|---|
| 1 | `capture-session-id.sh:67` uses `ps -o ppid= -p "$PPID"`; header lines 19–22 state the `sh`-shim premise | `[verified: file read]` |
| 2 | Hook `$PPID` **is** the claude process, so the file lands on claude's parent | `[verified: a lookup file holding a live session's id paired with the parent shell's own lstart; re-measured twice during planning — on both passes, every live claude PID lacked a file at its own PID]` |
| 3 | The reader's walk starts at the Bash tool and climbs through claude, so a file at claude's PID is always reachable | `[verified: marker.sh:47-68 + measured ancestor chain]` |
| 4 | Bypass markers currently store a login-shell PID and 3 of 6 live entries are affected | `[verified: activated a marker this session and inspected all .*-active.d entries]` |
| 5 | `CLAUDE_PID` is exported by the claude binary (string literal in `bin/claude.exe`) and equals the live claude PID | `[verified: env read + binary strings + match against the registry]` |
| 6 | `comm`-based matching is unportable (macOS argv[0] vs Linux 15-char exec basename `claude.exe`) | `[verified: reviewer probes on this host + field semantics]` |
| 7 | The registry `sessions/<pid>.json` does not cover every live session — a strict subset, count varies by moment | `[verified: direct count, taken twice, both times a partial subset]` |
| 8 | The env-prefix invocation form is deliberately ungated by the shape hook | `[verified: test_enforce_marker_script_shape.py:433 and its docstring]` |
| 9 | `capture-session-id.sh` is registered on **both** `SessionStart` and `SubagentStart`, both matcher-less | `[verified: settings.json parse]` |
| 10 | The `SubagentStart` payload carries the **parent** session's id, so its rewrite is content-identical and benign | `[verified: the lookup file was rewritten during this session's subagent fan-out with unchanged content — but measured against the CURRENT derivation, so it does not transfer to the new code; no unit test can settle it, only Verification step 4]` |
| 11 | `~/.claude/hooks` and `~/.claude/scripts` are directory symlinks into the **main checkout**, so live verification from a worktree tests unchanged code | `[verified: readlink]` |
| 12 | Writer-only, no env-first in `marker.sh` | `[engineer-verified]` |
| 13 | Evict mis-keyed bypass markers; cover `SubagentStart`; update the stale docs; fix the escaping test | `[engineer-verified]` |
| 14 | `$CLAUDE_PID` is present in *hook* env (not just Bash-tool env). Hooks are claude subprocesses so it should be, but this was not measured; `$PPID` covers the case either way | `[unverified — the fallback makes it non-load-bearing]` |

**Mechanism justifications.**

- *Key the file to `$PPID`* (`anchors: root`) — the lightest primitive that
  names claude's PID, and kernel-supplied rather than caller-supplied. Two
  heavier alternatives were weighed and rejected above: name-matching via
  `comm` (`anchors: row6`) and reading the first-party registry
  (`anchors: row7`).
- *Accept `$CLAUDE_PID` only when validated, and only within one hop*
  (`anchors: row5`, `row14`) — the value becomes a path component at line 89;
  `ps` guarantees numeric, an environment variable does not. Two lighter
  primitives were considered for the bound and rejected: (a) accept any
  ancestor — the login shell and terminal emulator qualify and outlive the
  session, which is the present bug; (b) numeric check alone — permits naming
  an unrelated live process. One hop admits the shim case and nothing above
  it. A literal hop cap (not `while true`) plus the existing empty/`0`/`1`
  guards (`marker.sh:48`) bound the walk, since this hook runs on **every
  subagent start** (`anchors: row9`).
- *No change to `marker.sh`* (`anchors: row3`) — the reader is already
  correct; it was reading a file placed in the wrong location.

### Changes

**`claude/.claude/hooks/capture-session-id.sh`** — the only behavior change.

- Replace the line 67 derivation as described. Keep every existing
  fail-open-with-`exit 0`-and-stderr path; a `SessionStart` hook that fails
  closed blocks session startup. Do not introduce `set -e`.
- Rewrite the header's "Deriving claude_pid" block (lines 19–22) to state the
  current contract, name `SubagentStart` alongside `SessionStart`, and record
  the known gap (what happens when validation rejects both candidates). No
  "used to be" framing — the comment must survive the PR description being
  lost.
- Leave the `active.d` PID-rewrite loop (lines 94–105) and the `jq` payload
  parse unchanged.

**One-time eviction, run after the fixed hook is live** (otherwise an
in-flight session re-activating a marker writes a shell PID again). Two sets,
both fail-safe — deleting either only re-arms a gate or forces a loud abort:

1. **`.*-active.d` bypass entries.** 3 of 6 currently name live login shells,
   so those gates are bypassed indefinitely. No code change is needed beyond
   this: once the writer is fixed, every new marker carries claude's real PID,
   which dies with the session, so `kill -0` self-heals from then on — modulo
   PID reuse, which these entries do not guard against (they store a bare PID
   with no start time, unlike `_walk_session`).
2. **The five two-line `sessions/` entries keyed to live login shells.** This
   narrows the note-only decision recorded for `sessions/` cleanup, on
   evidence found in review: these five are not inert disk clutter. Their
   recorded `lstart` still matches the live shell, so `_walk_session:57-58`
   accepts them. `capture-session-id.sh` fails open on eight distinct paths;
   on any of them claude's own entry is absent, the walk keeps climbing, and
   it finds a live shell's entry holding a **previous session's id** — a
   silent wrong-identity marker write where the design intends a loud abort.
   The 61 legacy single-line files stay (already rejected by the
   `-n recorded_start` check) and `sessions/1` stays (unreachable via the
   `!= "1"` loop guard).

Also delete the stray `sessions/`-entry artifact a reviewer agent wrote into
the live config directory during this plan's review.

**Documentation, now inaccurate.**

- `docs/hooks.md:32` — states the lookup is found "via the bash tool's
  `$PPID`" and, in the same entry, both "Overwritten on the next session
  start under the same PID" and "written once at `SessionStart` and never
  rewritten." Those two clauses already contradict each other, and the second
  is disproved by the `SubagentStart` registration. Rewrite the entry.
- Five skill bodies assert the wrong diagnosis on failure —
  `code-review/SKILL.md:390`, `plan-review/SKILL.md:22`,
  `ready-for-review/SKILL.md:31`, `respond-pr/SKILL.md:16`,
  `ai-instruction-and-memory-files/SKILL.md:19` all say "the
  `capture-session-id.sh` SessionStart hook didn't run." Broaden to name
  resolution failure rather than a specific cause. `/code-review` requires
  `skill-review` for SKILL.md edits — run it on these five.

**Reuse, not reimplementation:** `_lib_valid_session_id_component`,
`_lib_config_dir`, and `marker.sh:48`'s existing chain-guard shape are all
called or mirrored rather than duplicated.

### Critical files

| Path | Change |
|---|---|
| `claude/.claude/hooks/capture-session-id.sh` | PID derivation + validation + header |
| `claude/.claude/hooks/tests/test_capture_session_id.py` | pin the keyed PID; control `CLAUDE_PID` |
| `docs/hooks.md` | rewrite the `capture-session-id.sh` entry |
| `claude/.claude/skills/{code-review,plan-review,ready-for-review,respond-pr,ai-instruction-and-memory-files}/SKILL.md` | broaden the failure diagnosis |

`claude/.claude/scripts/marker.sh` is deliberately **not** in this table.

## Verification

**Fix the test that let this escape — this is the core of the change's
value.** `test_capture_session_id.py:50` currently asserts only
`files[0].name.isdigit() and int(files[0].name) > 0`, with the comment "We
don't pin the exact value (depends on test runner topology)." That deliberate
looseness is why the off-by-one shipped. `helpers.run_hook` invokes the hook
via `subprocess.run([str(hook)])` with no shell, so the hook's `$PPID` **is**
the pytest process: assert `files[0].name == str(os.getpid())`. That fails red
against today's code (which writes `os.getppid()`) and green after. No wrapper
process, no `ps` stubbing, no name matching.

**Test isolation.** These tests run the scripts as real subprocesses
inheriting `os.environ`, and a real Claude Code session exports `CLAUDE_PID` —
which would silently satisfy the new derivation and mask the `$PPID` path. Add
an autouse fixture clearing it in **`claude/.claude/hooks/tests/conftest.py`
only**; that directory already contains every test that runs the two scripts,
including `test_marker_worktree_keying.py`. Nothing under
`claude/.claude/skills/tests/` or `claude/.claude/scripts/tests/` invokes
`marker.sh` (verified by grep), so adding conftests there would create dead
fixtures. Use `monkeypatch.delenv("CLAUDE_PID", raising=False)` — the variable
is absent on CI runners and `delenv` raises `KeyError` otherwise, which would
go red on `ubuntu-24.04` while passing on every developer machine. This
matches the existing `isolated_home` precedent at `hooks/tests/conftest.py:54`.

**The pinned-PID assertion alone would pass for the wrong reason.** With
`CLAUDE_PID` cleared, `$PPID` and the accepted value are identical, so
`test_valid_input_writes_lookup_file` goes green even if the `CLAUDE_PID` read
were deleted outright. Add a case where the two candidates *differ*: invoke
the hook through a shim (`subprocess.run(["sh", "-c", "exec <hook>"])`) so
`$PPID` is the shim, set `CLAUDE_PID=str(os.getpid())`, and assert the file
lands at `os.getpid()` and not at the shim's PID. That is what ties the green
to the mechanism.

**Do not treat `test_marker_script.py`'s writer/reader round-trip as
regression coverage.** It is structurally incapable of catching a hop-count
error: the writer is off by one and `_walk_session` walks *every* ancestor, so
it passes both before and after. Fix the writer-side assertion instead.

Additional cases: `CLAUDE_PID` non-numeric or whitespace-only must fall back
to `$PPID` and still write a file (not become a path component, and not
abort); `CLAUDE_PID` set to a live process outside the one-hop bound must be
rejected (`subprocess.Popen(["sleep","30"])` gives a live non-ancestor child);
the happy path must emit **no** stderr, per the convention
`test_capture_session_id.py:137` already establishes.

Two comments in that file describe the derivation being removed and must be
updated with it: lines 47–49 ("resolved via `ps -o ppid=`… We don't pin the
exact value") and the docstring at lines 194–196 ("the earlier `-o ppid=`
`CLAUDE_PID` resolution still succeeds").

No unit test can settle the `SubagentStart` process topology — whether the
subagent-start hook is spawned by the same `claude` process, which is what
decides that `$PPID` still names claude. The hook has no `hook_event_name`
branch, so a payload-level test would be tautological. Verification step 4 is
the only thing that covers it.

1. `.venv/bin/pytest claude/.claude/` — full suite.
2. `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`. The form
   above is shellcheck-clean as written — no SC2155 (it is an assignment, not
   a command substitution) — but quote PIDs passed to `ps` to avoid SC2086.
   `[[ =~ ]]` is in-bounds under the file's `#!/bin/bash` shebang; leave the
   regex unquoted for bash 3.2 compatibility.
3. **Live checks must target the worktree explicitly.** `~/.claude/hooks` and
   `~/.claude/scripts` are directory symlinks into the *main checkout*, so
   invoking `~/.claude/scripts/marker.sh` from a worktree tests unchanged code
   and passes vacuously. Run the worktree path directly.
4. Post-merge only: start one fresh session and confirm
   `<config-dir>/sessions/<claude's own PID>` exists — not the parent shell's
   — with claude's own `lstart` on line 2. Then spawn one subagent and confirm
   the `SubagentStart` invocation rewrites the same file with unchanged
   content.
5. Post-merge: confirm a session whose parent shell has exited now resolves,
   and that a newly written `.*-active.d` entry names the claude PID.

**Rollback:** `git revert` is clean in both directions. Files written at
claude's own PID are still found by the old walk (it climbs through claude and
the recorded `lstart` matches), and post-fix `active.d` entries hold real
claude PIDs that `kill -0` handles correctly.

## Out of scope

- **Legacy `sessions/` entries** — the 61 single-line files and `sessions/1`.
  Engineer selected note-only, and review confirmed both sets are already
  unreachable: single-line entries fail the `-n recorded_start` check, and
  `sessions/1` is excluded by the walk's `!= "1"` guard. The five *two-line*
  entries keyed to live shells are **not** in this bucket — they are reachable,
  and Changes evicts them. Note in the PR body that `post-crash-sessions.py`
  surfaces the *dead* subset as `legacy_bare_pid_dead`; re-keying makes that
  report more accurate, since a long-lived login-shell PID currently reads as
  alive and is under-reported.
- **`marker.sh` changes of any kind**, including env-first resolution. See
  Approach.
- **Namespace collision risk.** `sessions/` is also written by Claude Code
  itself (`<pid>.json`); the bare-PID files share that directory. Worth one
  line in the PR body, not a change here.
- **`post-crash-session-recovery.md`** — separate in-flight plan; flag its
  pre-#571 staleness to its owner rather than editing it here.
- **The two uncommitted `settings.json` modifications** in the main checkout.
