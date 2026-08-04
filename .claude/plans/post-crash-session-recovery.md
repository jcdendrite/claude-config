# Post-crash session recovery

## Context

**Goal: ship a script that, after a machine crash or reboot, reports every Claude Code session that was live at crash time and gives the engineer a runnable command to resume it — replacing the ad-hoc investigation that currently has to be reconstructed by hand each time.**

The engineer's machine has crashed several times in recent months, each time orphaning
Claude Code sessions. Recovering them today means improvising a search across the
transcript corpus, and that improvisation has already failed twice in one incident: a
cwd-based directory search missed work done through dispatched subagents, and a session
that crashed before persisting its first turn left no transcript to find at all. The only
surviving proof of that second crash was a stale scheduled-task lock file.

A smaller gap surfaced from the same incident: `docs/transcript-analysis.md` maintains an
explicit list of scoping blind spots for the toolkit, and the subagent-transcript-location
behavior is a fourth one that is not documented. Both ship in this PR.

**Audience.** `claude/` is a stow package — this installs into `$HOME` for every user who
clones and stows this repo, on `git pull`. It is not personal tooling, and Linux stow users
are in scope.

## Step 0 — Resolve the load-bearing unknown before writing code

One inference underpins the primary evidence source, and it is currently unverified:
**does a clean exit remove a session's registry entry, or does Claude Code sweep stale
entries at next startup?** All observed entries had live PIDs, which is equally consistent
with both. It matters because the second reading breaks the tool at exactly its moment of
use: the natural post-reboot flow is *reboot → open terminal → launch Claude Code → ask
what happened*, and launching is the sweep trigger. The engineer would get a false
all-clear.

Two experiments settle it, in this order:

1. Run `claude -p 'hi'`, let it exit cleanly, and check whether its `<pid>.json` survives.
2. Kill a session with `SIGKILL`, confirm the entry survives, then launch a fresh `claude`
   and re-check whether the dead entry was swept.

**Regardless of the outcome, the classifier does not depend on it.** Boot time is already
being computed for source C; applying it to source A makes the inference non-load-bearing:

- A dead-PID entry whose **file mtime predates last boot** is a pre-crash session under
  *both* readings.
- An entry written **after** boot is not crash evidence and must be labelled as such.

This also handles a failure the experiments do not: after a reboot PIDs restart low, so a
relaunched session writing `4711.json` **overwrites** a crashed session's entry. Source A
degrades the longer the user waits, independently of the sweep question. The report header
must state the freshness constraint, and every row must show its entry mtime against boot
time.

If experiment 2 shows entries are swept, add a "run this before relaunching Claude Code"
line to the report header and the README — `--help` is unreachable at the moment of need.

## Approach

### Root problem

After an unclean shutdown there is no single place that answers "what was running, and what
can I get back?" — the evidence is scattered across a session registry, per-project lock
files, and the transcript corpus, each covering a different slice with different failure
modes.

### The primitive this design is built on

The brief proposed process-liveness checks plus a boot-time heuristic plus a machine-wide
`find`. Re-reading the actual on-disk state found a lighter first-party primitive the brief
did not know about: **Claude Code maintains its own session registry at
`<config-dir>/sessions/<pid>.json`**. Two lighter alternatives were weighed and rejected:

- **Boot-time transcript-mtime heuristic alone** (`kern.boottime` vs each transcript's
  mtime): infers liveness from a file timestamp rather than reading it. It cannot
  distinguish a session that exited cleanly seconds before the crash from one that died
  mid-turn, and says nothing about a session that never wrote a transcript. Retained as a
  *corroborating* signal (source C), not the foundation.
- **`scheduled_tasks.lock` files alone** (the brief's centrepiece): a scheduled-task-only
  mechanism with no coverage of interactive sessions, storing only the most recent
  acquisition per project — it can prove "at least one crash" but never enumerate them.
  Retained as source B, demoted from primary.

### Three evidence sources

**A — Session registry** (`<config-dir>/sessions/*.json`). Primary signal for interactive
sessions. Classified against boot time per Step 0.

Legacy bare-`<pid>` files also live in that directory — written by this repo's
`capture-session-id.sh`, deleted by `cleanup-session-id.sh` on `SessionEnd`. **These are
active infrastructure, not cruft:** `marker.sh`'s `_walk_session` reads them, and every
`require-*` gate depends on that resolution. Report only the dead-PID ones, in a section
whose header states plainly that live entries must not be deleted.

**B — Scheduled-task locks** (`<project>/.claude/scheduled_tasks.lock`). A dead `pid` means
that scheduled run died abnormally. Discovery is the **union** of two methods, because each
misses what the other catches: harvesting exact `cwd` values from the transcript corpus
(fast, exact, reaches worktree locks nested below a depth-limited find), and a bounded
`find` over `$HOME` (catches projects that have a lock but no transcript — the exact case
that produced the original incident's only proof of a crash).

There is deliberately **no `--fast` flag.** Skipping the `find` half reintroduces precisely
the blind spot the tool exists to close, and ~19s is free during post-reboot triage.

**C — Transcript corpus** (`<config-dir>/projects/*/*.jsonl` and `*/*/subagents/*.jsonl`).
Resolves whether a `sessionId` has any transcript at all (resumable vs.
crashed-before-first-turn), supplies `cwd`/`gitBranch`/last-activity, and surfaces subagent
work whose `cwd` differs from its containing project directory.

### Reading the corpus: field allowlist, not line position

An earlier draft of this plan asserted the first line carries `cwd`. **That is false, and
the correction is load-bearing:** across a 200-file sample, exactly 1 line-1 record carried
`cwd` (line 1 is `{mode, sessionId, type}`); the first `cwd`-bearing record sits at index
2–4, and **198 of those 200 records also carry `message`** — the verbatim first user prompt,
the highest-density private content on the machine.

So the read is specified **by field, never by line position**:

- Parse records until one carries `cwd`, bounded at **12 records**; exhaustion yields
  `unknown`, not license to keep reading.
- Extract exactly `cwd`, `gitBranch`, `sessionId`, `timestamp`. Discard the parsed record
  immediately. Never bind `message`, `content`, or `toolUseResult` to a variable.
- Apply the same allowlist to the registry: `sessionId`, `pid`, `procStart`, `cwd`,
  `status`, `startedAt`, `updatedAt`. Unknown keys are ignored **by construction**, so a
  future Claude Code release adding a field cannot widen output. This matters concretely —
  the registry carries a free-text `name` (a session title derived from content) that no
  `.items()`-style render may ever reach.

### Schema-drift contract

Both the registry and the lock format are **undocumented first-party contracts**, observed
on one machine at one CLI version. Every stow user runs a different version. Follow the
repo's existing posture for undocumented Claude Code fields (`statusline-command.sh`, the
comment block at ~line 79: degrade to empty, never error):

- Minimal required core is `sessionId` + `pid`; everything else is `.get()` with a default.
- Entries failing the core go to a **counted "unparsed" bucket** — never silently dropped.
- Capture each entry's `version`; when it differs from the validated set, print a one-line
  "registry format not validated for CLI x.y.z" banner.
- `acquiredAt`'s epoch-millisecond unit is **inferred**; range-check rather than trust it.

### Classification and precedence

Four states: `resumable`, `crashed-no-transcript`, `clean-exit`, `unknown`. Because
`unknown` is otherwise a catch-all that makes any under-specified case conformant, the
precedence rules are fixed here:

| Evidence | Class |
|---|---|
| Any source reports a **live** PID (procStart-matched) | never crashed — `clean-exit` or live |
| Registry dead-PID, entry mtime **before** boot, transcript exists | `resumable` |
| Registry dead-PID, entry mtime **before** boot, no transcript | `crashed-no-transcript` |
| Registry dead-PID, entry mtime **after** boot | `unknown` — not crash evidence, say so |
| Lock dead-PID, no registry entry, transcript exists | `resumable` |
| Lock dead-PID, no registry entry, no transcript | `crashed-no-transcript` |
| Sources disagree, or `procStart` unparseable, or `ps` unusable | `unknown` |
| Transcript mtime just before boot, no registry/lock entry | `unknown` — corroborating only |

Two collapse rules: resume reuses `sessionId`, so the registry can hold an old dead entry
and a live one for the same session — **collapse by `sessionId`, alive wins**, and show the
underlying entry count. And every `unknown` row must print **which sources disagreed and
what each asserted**; otherwise the tool hands back the manual investigation it replaces.

### The report is the deliverable

Classification alone stops one step short of the goal. Each resumable row prints, sorted
most-recent-activity first: the copy-pasteable `cd <cwd> && claude --resume <id>`, the
`gitBranch`, and last-activity time. A row whose `cwd` no longer exists must say so —
resuming from a deleted directory fails. One footer line notes that `~/.claude/handoffs/`
and `~/.claude/briefs/` may hold a richer resume for these directories (a session can have
written a handoff hours before crashing).

### Portability — this ships to Linux users too

Every primitive below was measured on Darwin only. Each needs a module-level seam with a
per-platform branch and a unit test faking the other platform's source:

- **Boot time.** `kern.boottime` is Darwin/BSD-only. Linux equivalent is the `btime <epoch>`
  line in `/proc/stat`. CI runs Linux only, so a Darwin-only implementation fails there and
  a Linux-only one is never exercised locally.
- **`ps -p <pid> -o lstart=`.** Pin `TZ=UTC LC_ALL=C` on the call. This is not cosmetic:
  `lstart` renders in ambient timezone *and* ambient `LC_TIME`, so a user with a non-English
  locale gets a month name `strptime` cannot parse. Pinning makes both sides UTC in the C
  locale and reduces the PID-reuse guard to a whitespace-stripped compare — **deleting the
  timezone-conversion logic rather than testing it**. Also: treat **empty stdout** as dead,
  never the return code (macOS and Linux differ on rc for a malformed PID); validate the pid
  as an integer before shelling; strip trailing whitespace; and if `ps` is unusable
  (busybox has no `-o lstart`), classify `unknown`, never `crashed` — a false crash report
  sends the engineer hunting nothing. Compare with a **±2s tolerance**, not equality.
- **File mtime.** Use `os.stat(p).st_mtime`. BSD `stat -f %m` and GNU `stat -c %Y` are
  mutually fatal; `stat(1)` is not used at all.
- **The `find` sweep.** `find <realpath of $HOME> -xdev -maxdepth 6 -type f -name
  scheduled_tasks.lock`, stderr discarded, wrapped in a `subprocess.run(timeout=...)` whose
  named constant cites the 18.9s measurement as its basis. `-xdev` matters: cloud-storage
  and network mounts under `$HOME` can force file hydration or block indefinitely. No `-L`.
  On timeout, print which coverage was lost and continue — never abort. Print the sweep's
  elapsed time so a slow machine is visible rather than mysterious.

### Config-dir resolution reuses the existing canonical resolver

`claude/.claude/scripts/_config_dir.py::config_dir()` already resolves `$CLAUDE_CONFIG_DIR`
with a `~/.claude` fallback and is imported by `transcript-analysis.py`,
`token-analyzer.py`, and `analyze-context.py`. It supplies element zero of the
`--config-dir` list. Do not spell "where is the config dir" a second time.

A supplied `--config-dir` must contain a `sessions/` or `projects/` child or be rejected by
name. Every parse failure reports **path + reason only** — never the offending bytes, never
a raw traceback.

### Error handling

One rule: **no single bad input aborts the run.** Exit 0 whenever a report is produced,
even partial; nonzero is reserved for usage errors and an unreadable `--config-dir`. Named
paths: sessions dir missing → empty source A with an explicit note; `EACCES` or malformed
JSON → the counted unparsed bucket (a *live* session mid-write yields partial JSON, so this
is transient and never crash evidence); `pid` absent or non-integer → `unknown`; filename
`<pid>.json` disagreeing with the `pid` field → prefer the field and report the mismatch;
zero-byte or unparseable transcript → `crashed-no-transcript`, not `resumable`.

### Output safety

The report prints real paths, session UUIDs, and branch names — raw output trips three of
this repo's always-on structural detectors. Default stays **unredacted**, because a
recovery report reading `private-project-1` is useless at the moment of need. But a
`--help` warning is not a sufficient control, since the realistic publish path
(`gh issue create`) is documented in `docs/private-project-redaction.md` § Known gaps as
scanned by none of the three scans. So: ship `--redact`, which maps project dirs and
session UUIDs to ordinals and drops `gitBranch` while preserving classification counts and
per-row status. Document it in `--help` and `docs/scripts.md`, and print a one-line
not-publish-safe footer in the report itself.

**Stated invariant:** the tool reads only. It writes no file, creates no directory, and
emits only to stdout/stderr.

### Shape: standalone script, not a `transcript-analysis.py` subcommand

Every subcommand in that toolkit takes `_add_project_scope_args`, and its `--this-repo` /
`--projects` semantics are *antithetical* to crash recovery — scoping the search to one repo
is precisely the failure mode this tool exists to prevent. A subcommand would either offer
scope flags that invite the error, or pointedly reject the flags every sibling accepts. The
reuse on offer is thin; `config_dir()` is imported, and the rest is a JSONL line read.

### Assumption ledger

| # | Assumption | Tag |
|---|---|---|
| 1 | Registry entries carry `sessionId`/`pid`/`procStart`/`cwd`/`status`/`startedAt`/`updatedAt`/`kind`/`entrypoint`/`version` | `[verified: read all present entries at CLI 2.1.221]` — **but the key set is NOT identical across entries** (`name`, `nameSource`, `peerProtocol`, `statusUpdatedAt` appear variably), which is why the allowlist above is by-field. anchors: root |
| 2 | A dead `pid` in the registry means the session did not exit cleanly | `[unverified]` — Step 0 resolves it empirically; the boot-time comparison makes the classifier correct under **either** outcome. anchors: row 1 |
| 3 | Subagent transcripts live at `<parent project dir>/<parent session id>/subagents/agent-<id>.jsonl`, keyed by the **parent's** cwd | `[verified: 1383 subagent transcript files; 964 have a first-record cwd whose slug ≠ the containing project dir; 904 of those cwds have no project directory at all]` — anchors: root |
| 4 | Those 964 split as 936 descendant-of-parent (worktree/subdirectory), 21 genuinely cross-directory, 7 parent-is-worktree-of-subagent-repo | `[verified: classified across the full corpus]` — anchors: row 3 |
| 5 | The bounded `find` over `$HOME` completes in ~18.9s and finds all 5 locks present | `[verified: timed run on one Darwin machine]` — a basis for the timeout constant, **not a bound** for other users. anchors: source B |
| 6 | cwd-harvest alone is **incomplete** — misses a real lock whose project has no transcript, and finds 2 worktree locks the depth-4 find misses | `[verified: both methods tested against the 5 known locks]` — anchors: source B, justifies the union and the removal of `--fast` |
| 7 | Lock fields are `sessionId`/`pid`/`procStart`/`acquiredAt` | `[verified: identical 4-field shape across all 5 locks]` — but this is the same undocumented-contract class as row 1; `acquiredAt`'s unit is inferred. anchors: source B |
| 8 | Reads under `~/.config/**` are blocked by **enterprise managed settings**, not any hook in this repo, and that layer gates tool calls only — a script run in the engineer's own terminal is unaffected | `[verified: no hook in this repo matches that path; the managed-settings file states the manual-override property explicitly]` — anchors: `--config-dir` |
| 9 | Additional account profiles come from a repeatable `--config-dir`, not auto-discovery | `[engineer-verified]` — keeps claude-config free of any dependency on the account-provisioning repo's layout |
| 10 | Tool and doc bullet ship in one PR | `[engineer-verified]` |
| 11 | Transcript line 1 does **not** carry `cwd`; the first `cwd`-bearing record is at index 2–4 and almost always carries the user's first prompt | `[verified: 1 of 200 sampled files had cwd on line 1; 198 of 200 first-cwd records also carried `message`]` — corrects a false claim in the prior draft; anchors the field-allowlist design |
| 12 | `test_no_bash4_constructs.py` sweeps `claude/.claude/**/*.sh` only | `[verified: `_CLAUDE_DIR.rglob("*.sh")`, `_CLAUDE_DIR = <repo>/claude/.claude`]` — the extensionless wrapper at `claude/.local/bin/` is **not** covered by it; shellcheck via `scripts/list-shell-files.sh` is what reaches it. anchors: Verification |
| 13 | `_config_dir.py::config_dir()` is the canonical config-dir resolver | `[verified: imported by transcript-analysis.py:23, token-analyzer.py:9, analyze-context.py:26]` — anchors: config-dir design |

### The documentation change

Add a fourth bullet to `docs/transcript-analysis.md` § "What `--this-repo` does not cover,
and the documented fallback", precise about a distinction the source investigation blurred:

- `--this-repo` with `--include-subagents` **does** read subagent files whose parent session
  ran in this repo — not a broken path for the common case.
- The genuine `--this-repo` gap is the inverse: a parent session anchored in *another* repo
  dispatching a subagent whose cwd is in *this* repo. That work sits under the other repo's
  project directory and no directory-name method reaches it.
- The wider gap, and the one that caused the incident, is discovery by directory name
  generally — most of these subagent cwds have no project directory at all, so a name match
  has nothing to find.
- Fallback, matching the section's existing style: content-grep across `*/subagents/*.jsonl`,
  or traverse those files and read each one's own `cwd` field — never a directory-name
  approach.
- One clause: `--include-subagents` is off by default on every subcommand offering it, so
  even a correctly-scoped run under-reports subagent work unless it is passed.
- One pointer line: for post-crash enumeration, see `post-crash-sessions`.

## Critical files

**Create**
- `claude/.claude/scripts/post-crash-sessions.py` — the tool. Stdlib-only.
- `claude/.local/bin/post-crash-sessions` — two-line `exec` wrapper matching the 8 existing ones.
- `claude/.claude/scripts/tests/test_post_crash_sessions.py` — tests.

**Modify**
- `docs/transcript-analysis.md` — the fourth bullet plus the `--include-subagents` clause.
- `docs/scripts.md` — one entry in the leading analysis group after `transcript-analysis.py`,
  cross-linked to `resume-context.sh`; states `--redact` and the interpreter floor.
- `README.md` — one bullet adjacent to `resume-context`, so the tool is findable at a
  months-apart moment of need without recalling its name.
- `claude/.claude/hooks/cleanup-session-id.sh` — **one header comment line** recording that
  `<pid>.json` is Claude Code's own registry and is deliberately not swept. This PR creates
  that invariant (the recovery tool now depends on those files surviving) and nothing else
  records it; a future contributor tidying the directory would silently blind the tool.

**Reuse rather than reimplement**
- `claude/.claude/scripts/_config_dir.py::config_dir()` — the config-dir resolver.
- `claude/.claude/scripts/tests/conftest.py::_dead_pid()` — a guaranteed-dead PID.
- `claude/.claude/scripts/transcript-analysis.py` — `_path_to_project_slug` (~1206) is the
  canonical slug formula; `_read_session_file`'s docstring (~344) documents the
  `<session_id>/subagents/` layout.
- `claude/.claude/scripts/resume-context.sh` — conventions template: `set -euo pipefail`,
  rationale in a header block, `<SCRIPT>_<THING>` env seams, `printf 'name: msg\n' >&2`.
- `claude/.claude/scripts/statusline-command.sh` (~79–97) — the repo's prior art for
  reading an undocumented Claude Code field defensively.

**Testability seams — design these in before writing the traversal; retrofitting is the expensive rewrite**
- `_ps_lstart(pid) -> str | None`, stubbed the way the repo already stubs subprocess.
- The `procStart` comparison as a pure function taking the timezone as a **parameter**,
  never reading the process clock.
- Boot-time lookup behind one seam with per-platform branches.
- Scan functions take `config_dirs: list[Path]` and `find_root: Path` as **arguments**;
  module constants exist only as argparse defaults. Note the repo's existing corpus fixture
  pattern binds `PROJECTS_DIR` at import, so parameterization — not monkeypatched constants
  — is what makes the multi-`--config-dir` design testable.

## Verification

1. `.venv/bin/pytest claude/.claude/scripts/tests/test_post_crash_sessions.py`, covering:
   - Every classification state including each `unknown` trigger — non-JSON registry file,
     entry missing `pid`/`procStart`, truncated first records, a lock `sessionId` with
     neither registry entry nor transcript, sub-second `procStart` rounding at the match
     boundary.
   - **PID-reuse guard** via the `_ps_lstart` stub: live PID with mismatched `procStart` →
     not the same process; 1-second skew → same process (±2s tolerance).
   - **Hostile locale/timezone**: inject a non-UTC `TZ` and a non-English `LC_TIME` and
     assert classification is unchanged. A test that only passes under a UTC runner is a
     false-signal test — CI runs UTC.
   - **`ps` unusable** → `unknown`, never `crashed`.
   - **Union discovery** with `find_root` injected at `tmp_path`: a find-only lock and a
     cwd-harvest-only lock both appear in the union, and each half alone misses one. The
     real `$HOME` sweep must never run in the suite.
   - **Both boot-time branches**, each with the other platform's source faked.
   - **Schema drift**: renamed field, missing field, extra field → degrade, not crash.
   - **No prompt leakage**: no substring of any fixture transcript's `message` appears in
     rendered output.
   - **`--redact`** output matches none of the structural detector regexes in `_lib.sh`.
   - **Writes nothing**: `tmp_path` unchanged after a run.
2. `.venv/bin/pytest claude/.claude/` — full suite. Note the wrapper is **not** covered by
   `test_no_bash4_constructs.py` (ledger row 12); shellcheck in step 3 is its only gate.
3. `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
4. **End-to-end over a fixture corpus**, not live machine state: drive `main()` against a
   tmp config dir holding a dead-PID registry entry, a dead-PID lock, and a matching
   transcript, asserting the full rendered report including the resume command string. A
   live run is retained only as a no-traceback smoke check with **no numeric expectations** —
   "5 locks, all PIDs alive" is stale after the next reboot and never exercises the crashed
   branch.
5. **Negative controls:** a config dir with no `sessions/` directory at all; and a config dir
   containing a same-named-but-foreign JSON file (the drift case, not the absence case).
   Both produce a clean report, not a traceback.
6. Re-read the rendered `docs/transcript-analysis.md` bullet against ledger rows 3–4; it must
   not restate a figure that isn't re-derivable from the corpus at the moment it is written.

**Review surface.** One new Python script plus its tests, three doc files, and a one-line
hook comment. Risk concentrates in process liveness, boot-time comparison, and the two
undocumented on-disk contracts.

## Out of scope

- Modifying `resume-context.sh`, `/handoff`, or `/brief` — those recover deliberately
  authored continuity files; this tool detects an unannounced crash.
- Loosening the managed-settings or hook rules gating `~/.config/**`.
- Reading or depending on the account-provisioning repo's `accounts.tsv`.
- Changing any existing `transcript-analysis.py` subcommand's behavior.
- **The bare-PID leak.** `claude -p` one-shot runs never fire `SessionEnd`, so
  `sessions/<pid>` files accumulate. Real, but separable from this tool and from the
  one-line invariant comment above.
- **Widening `test_no_bash4_constructs.py` to `claude/.local/bin/`** (ledger row 12). A
  pre-existing coverage gap this plan surfaces but does not own.
- **No `requires-python` floor anywhere in the repo.** The wrapper execs
  `#!/usr/bin/env python3` and `install.sh` only checks that `python3` exists, so a stow
  user on an older interpreter gets a `SyntaxError` rather than a diagnostic. Pre-existing;
  this plan states the targeted floor in `docs/scripts.md` rather than fixing it.
