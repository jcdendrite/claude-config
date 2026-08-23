# Identify a terminal window by PID and title it

## Context

Add a script that, given a process ID, finds the terminal window that
process is attached to and rewrites that terminal's tab/window title, so a
blocked or stuck session can be spotted at a glance among many open
windows. This matters because a blocked Claude Code session (a stuck lock,
a hung command) often leaves the engineer with only its PID — no window
title, no distinguishing text — and manually correlating that PID to a
terminal today means hand-typing `ps` and a raw escape-sequence command
each time. A prior Haiku-authored technique brief in a session scratchpad
verified the underlying mechanism live (`ps` → TTY → OSC title escape
sequence) but left it as ad hoc one-liners; this plan turns it into a
tested, installed script following this repo's existing scripts/
conventions.

## Approach

Add `mark-terminal.py` to `claude/.claude/scripts/` (installed as the
`mark-terminal` command via a bash wrapper in `claude/.local/bin/`,
matching every existing script in that directory). It resolves a PID's
controlling terminal via `ps -o tty= -p <pid>`, builds a title
automatically from that PID's entry in the Claude Code session registry
(`<config-dir>/sessions/<pid>.json`) when one exists, and writes an OSC
0 title-set escape sequence directly to the resolved `/dev/ttysNNN`
device. A `--list` mode enumerates every currently-live registry entry
with its PID/TTY/cwd, for the "many windows open, which one is it"
case. This is Python (not bash, unlike Haiku's brief) because reading
and safely rendering JSON registry data is what this repo's existing
session-debugging scripts (`post-crash-sessions.py`, `token-analyzer.py`)
already do, and this script reuses their `_config_dir.py` primitives and
multi-account config-dir precedence directly.

### Assumption ledger

- **Root problem:** a blocked local process's PID is, on its own, not
  enough to find which terminal window it lives in; the correlation
  requires the process table's TTY column plus a way to write to that
  device.
- **Givens:**
  - The terminal emulator in use understands OSC 0 (xterm title-set) —
    a property of third-party terminal software (Terminal.app, iTerm2,
    Ghostty) this plan doesn't touch, not something editing this script
    could change. Haiku's brief already exercised this exact sequence
    against a live window and confirmed the title changed.
    `[verified: identify-and-rename-terminal-task.md §4, "PID 95540 was
    successfully marked"]`

  Darwin-only support and no-tmux are *not* givens — both are things
  this script's own code could extend to cover, just deliberately
  isn't; they're recorded once, under Out of scope, with their reasons
  (not duplicated here).

- **Mechanisms:**
  - **PID → TTY via `ps -o tty= -p <pid>`, then write straight to
    `/dev/<tty>`** `anchors: root`. Lighter alternatives considered and
    set aside: (1) AppleScript automation (`tell application "Terminal"
    to set custom title of...`) — rejected: app-specific (no path for
    Ghostty, the terminal this machine actually runs — confirmed via a
    live `ps aux` read this session), and triggers a macOS Automation
    permission prompt the raw device write never needs; (2) a
    long-running background "title watcher" daemon reacting to lock
    events — rejected: a persistent privileged process is a heavier
    coordination primitive than a one-shot, on-demand CLI invocation
    warrants for this task.
  - **Auto-derive the title from the session registry
    (`<config-dir>/sessions/<pid>.json`)** `anchors: root`. Chosen over
    requiring the caller to type a label (this repo's engineer selected
    this explicitly) because the motivating case is exactly the one
    where the caller has *nothing but the PID* to go on — deriving the
    project name from the registry's `cwd` needs no extra input.
    `[engineer-verified]`
  - **Reuse `_config_dir.py`'s `config_dir()` /
    `declared_roots_matching()` for multi-account scanning**
    `anchors: root`. This machine runs several isolated Claude Code
    config profiles (per this session's own project instructions), and
    `_config_dir.py` is this repo's already-established, single shared
    home for that resolution — `post-crash-sessions.py` and
    `transcript-analysis.py` both import it rather than reimplementing.
    `[verified: claude/.claude/scripts/post-crash-sessions.py:51]`
  - **Duplicate (not import) `post-crash-sessions.py`'s
    `_sanitize_for_terminal` control-char stripping and its
    `--config-dir`-vs-declared-roots merge loop** `anchors: root`. Both
    are small, self-contained pieces of logic with no existing shared
    home beyond the primitives already reused above; extracting a new
    cross-script abstraction for them would mean also touching
    `post-crash-sessions.py` to consume it, which is outside this
    ticket's file boundary and risks an unrelated regression in a
    stable, already-tested script for no behavior change of its own.
    This is the named CLAUDE.md DRY exception — a small duplicated value
    beats a bad abstraction built only to remove it.
    `[verified: claude/.claude/scripts/post-crash-sessions.py:369-381,
    1230-1258]`
  - **Verify a registry entry's `procStart` against the live process's
    actual start time before trusting it** `anchors: root`. A pid is
    recycled by the OS eventually; without this check a
    `sessions/<pid>.json` file outliving its process silently attaches
    an unrelated live process's window to the wrong project title —
    the same failure class `post-crash-sessions.py` names explicitly as
    a reason it anchors classifications against boot time
    (`post-crash-sessions.py:19-24`). A lighter, self-contained
    per-entry timestamp comparison is used here instead of that
    script's full boot-time-anchored classification system, since this
    tool only needs "is this specific entry still describing this
    specific live process," not a general crash/recoverability
    taxonomy — pulling in the heavier machinery would be over-scoped
    for that narrower question. `[engineer-verified: raised by
    staff-sdet and staff-platform-engineer's independent plan-review
    passes]`
  - **Guard on `platform.system() == "Darwin"` at startup** `anchors:
    root`. This repo's stow package installs on Linux and WSL2 too
    (`README.md:9,91`), where the BSD-specific `??` no-tty sentinel and
    `/dev/ttysNNN` device naming this design relies on don't hold; an
    explicit early exit fails loudly instead of misbehaving silently on
    those platforms, matching this repo's existing fail-loud convention.
    `[verified: README.md:9,91, contradicting an earlier draft of this
    plan's Out-of-scope section]`

## Critical files

- `claude/.claude/scripts/mark-terminal.py` (new) — CLI entry point.
  - `resolve_config_dirs(extra_config_dirs, *, tool_name)` — mirrors
    `post-crash-sessions.py:1230-1258`'s precedence (explicit
    `--config-dir` overrides the declared-roots default; otherwise scan
    the active profile plus every root in
    `~/.claude/transcript-config-dirs`), built on `_config_dir.py`'s
    `config_dir()` and `declared_roots_matching()` — **reuse, don't
    reimplement** those two.
  - `resolve_tty(pid)` — validates `pid` is a positive integer before
    ever shelling out (a non-positive or non-numeric pid errors at
    argparse, not here); runs `ps -o tty= -p <pid>`, and **`.strip()`s
    stdout before any comparison or path construction** — BSD `ps`
    space-pads the `tty=` column to fixed width (confirmed live on this
    machine: `ps -o tty= -p 1` returns `"??      "`, not `"??"`), so an
    unstripped compare would silently never match the no-tty sentinel
    and an unstripped device path would 404 with a misleading raw
    `FileNotFoundError` instead of the intended clean error. Raises a
    caller-facing error for a nonexistent pid (nonzero exit), a pid
    with no controlling terminal (stripped output `"??"`), or a
    missing/hung `ps` (`FileNotFoundError`/`subprocess.TimeoutExpired`,
    a 10s backstop). The returned tty name is also validated against a
    bare-alphanumeric allowlist before any caller joins it onto `/dev` —
    `ps` is resolved by bare name via `PATH`, so an unvalidated value
    would let a compromised `ps` traverse (`../../../tmp/pwned`) outside
    `/dev` entirely.
  - `_read_registry_entry(config_dir, pid)` — reads
    `<config_dir>/sessions/<pid>.json` if present; degrades to "no
    entry" (never raises) on `JSONDecodeError`/`OSError`/
    `UnicodeDecodeError` or a non-dict payload, mirroring
    `post-crash-sessions.py`'s own handling of this same undocumented,
    schema-driftable format (`post-crash-sessions.py:466-478`). Every
    string field read from the parsed JSON — `cwd` above all — is
    passed through `_sanitize_for_terminal` immediately at this parse
    point, not deferred to each caller, so both `--list`'s printed table
    and `build_title`'s OSC-bound title are covered by construction
    (mirrors `post-crash-sessions.py`'s own load-time sanitization at
    lines 479, 485). A non-string `cwd` (schema drift, or a hostile
    field of the wrong JSON type) degrades to `None` via the same
    sanitizer, matching `post-crash-sessions.py:379-381`.
  - **Stale-registry-entry check.** A `sessions/<pid>.json` file
    outlives its process; if the OS later recycles that pid, the file
    describes a different, unrelated live process. `_read_registry_entry`
    treats an entry as stale (equivalent to "no entry") whenever the
    registry's `procStart` field doesn't match the live process's actual
    start time (`ps -o lstart= -p <pid>`, parsed and compared with a
    tolerance of a few seconds for clock/format granularity). This one
    check also resolves the "same pid present under two config dirs"
    case from `build_title`/`--list`: at most one config dir's entry can
    have a `procStart` matching the live process, so the others are
    excluded as stale by the same test — no separate precedence rule is
    needed.
  - `build_title(pid, config_dirs, *, emoji, explicit_title)` — an
    explicit `--title` wins outright; otherwise calls
    `_read_registry_entry` against each resolved config dir in order and
    formats `"{emoji} {basename(cwd)} ({pid})"` for the first non-stale
    hit with a `cwd`, falling back to a bare `"PID {pid}"` when no
    config dir has a live, non-stale entry.
  - `_sanitize_for_terminal(value)` — duplicated from
    `post-crash-sessions.py:369-381` (see rationale above). Every title
    component reaching the escape sequence or `--list`'s printed table
    passes through this: registry `cwd` (via `_read_registry_entry`
    above), an explicit `--title`, and a caller-supplied `--emoji` —
    `--emoji` is just as much free-form input as `--title`, and nothing
    in this design distinguishes them. Beyond C0 controls, DEL, and the
    C1 range, also strips every Unicode Format-category (`Cf`)
    character — bidi overrides (`U+202E`) and zero-width characters
    (`U+200B`) fall outside those control ranges but can still render a
    misleading title from an untrusted registry `cwd`. This control's
    charter is escape/control-injection prevention, not full
    invisible-character moderation: a zero-width-but-non-`Cf` codepoint
    (e.g. a variation selector) passes through unstripped, since it
    carries no escape-sequence risk.
  - `write_title(device_path, title)` — writes `\033]0;{title}\007` to
    the resolved device; checks `os.access(device, os.W_OK)` first for a
    clear error instead of a raw `PermissionError` traceback, and still
    wraps the write itself in `try/except PermissionError` as a backstop
    for the check-then-write race. Authorization for this write is
    delegated entirely to the OS's tty-permission bits on `device_path`
    — there is no in-tool check that the caller owns the pid whose
    terminal this is.
  - `--list` mode — scans `<config_dir>/sessions/*.json` across the
    resolved config dirs, keeps only entries whose pid is currently
    alive (`ps -p <pid>`) *and* pass the stale-registry-entry check
    above, resolves each survivor's TTY, and prints a pid/tty/cwd table
    built from the already-sanitized fields `_read_registry_entry`
    returned.
  - **Platform guard.** `mark-terminal.py` checks `platform.system() ==
    "Darwin"` at startup and exits with a clear "macOS/BSD only" error
    otherwise, rather than attempting BSD-shaped `ps`/`/dev/ttysNNN`
    logic unconditionally — this repo's stow package also installs on
    Linux and WSL2 (`README.md:9,91`), where the no-tty sentinel and
    live-tty device paths are shaped differently and unverified here.
- `claude/.local/bin/mark-terminal` (new) — bash wrapper, byte-for-byte
  the same shape as `claude/.local/bin/post-crash-sessions`:
  `exec "$HOME/.claude/scripts/mark-terminal.py" "$@"`. Picked up
  automatically by `scripts/list-shell-files.sh`'s shebang-sniffing scan
  (`.github/workflows/tests.yml`'s `SHELL_REGEX` already matches
  `^claude/\.local/bin/` on the path alone) — no CI registration needed.
- `claude/.claude/scripts/tests/test_mark_terminal.py` (new) — see
  Verification.
- `docs/scripts.md` (edit) — new entry alongside the existing
  `post-crash-sessions.py` entry, following that entry's own
  "complements ... " cross-reference convention: post-crash-sessions.py
  answers "which sessions are recoverable," this answers "which open
  window is this live PID."

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_mark_terminal.py`
  from the worktree. Covers, without a real interactive terminal (none
  is available in this sandboxed session or in CI):
  - `_sanitize_for_terminal` strips ESC/BEL/DEL and other C0 controls,
    the C1 control range, and Unicode Format-category (`Cf`) characters
    (bidi overrides, zero-width characters).
  - `build_title`: registry hit → formatted title; registry miss →
    bare `PID {pid}` fallback; explicit `--title` overrides both;
    malformed JSON, a non-dict payload, and a non-string `cwd` (int,
    list, null) each degrade to a miss rather than raising; a stale
    registry entry (`procStart` not matching the stubbed live process's
    start time) is treated as a miss; the same pid present under two
    config dirs resolves to whichever one's `procStart` matches the
    live process, not first-match-wins.
  - `resolve_tty`: a stubbed `ps` (PATH-injected, matching
    `test_claude_auto.py`'s stub pattern) reproducing real `ps`'s
    space-padding — `"ttys015  \n"`, `"??      \n"` — plus a nonzero
    exit (nonexistent pid), each producing the right stripped result or
    error; a non-positive or non-numeric pid argument is rejected before
    `ps` is ever invoked, with a clear error rather than a raw
    stack trace; a tty name containing path separators (a compromised
    `ps` on `PATH`) is rejected before it reaches `Path("/dev") / tty`;
    a missing `ps` binary and a hung `ps` (via a pure fake `run=`
    raising `subprocess.TimeoutExpired`, no real subprocess spawned)
    each produce the intended clear error; `_ps_lstart`'s `TZ=UTC`/
    `LC_ALL=C` env-forcing is asserted directly against the exact `env`
    dict passed to `run(...)`, independent of what a PATH-stubbed `ps`
    does with it.
  - `main()`'s CLI dispatch: `--list` routes to `_run_list`; a
    `resolve_tty`/`write_title` failure exits 1 with the underlying
    error text; an invalid `--config-dir` exits 2; a missing `pid`
    without `--list` is rejected by argparse; the non-Darwin platform
    guard exits 2 before touching anything else.
  - The actual device write, exercised end-to-end against a real
    pseudo-terminal from Python's stdlib `pty.openpty()` — asserts the
    literal OSC byte sequence lands on the master fd for a legitimate
    title, **and**, per input source (registry `cwd`, `--title`,
    `--emoji`), for a title containing an embedded `\x1b]0;...\x07` —
    asserting the resulting master-fd byte stream contains exactly one
    well-formed OSC sequence with the hostile content neutralized, not
    two. A device path that fails the `os.access(..., os.W_OK)` check
    (e.g. a `pty` slave `chmod`'d unwritable) asserts the intended clear
    error, not a raw `PermissionError`; a separate test exercises the
    `except PermissionError` backstop independently, by forcing
    `os.access()` to report writable while the underlying `open()`
    still raises.
  - `--list`: a temp config-dir tree with fake `sessions/<pid>.json`
    files, a stubbed `ps` reporting which pids are alive, asserts dead
    pids are excluded, a stale (pid-recycled) entry is excluded even
    though its pid is alive, TTYs resolve for genuinely-live entries,
    and a `cwd` containing control characters prints sanitized in the
    table — not just in the OSC write path. Also covers mixed
    digit-width column alignment across two pids, the same live pid
    rendering as two rows when present under two config dirs (no
    cross-dir dedup, unlike `build_title`'s first-match-wins), and an
    unreadable `sessions/` dir being excluded rather than raised.
  - `resolve_config_dirs`: mirrors `post-crash-sessions.py`'s existing
    `--config-dir`/declared-roots test coverage in
    `test_post_crash_sessions.py:1841-1997` (explicit overrides
    default, dedup by resolved path, rejection of an invalid
    `--config-dir`) — confirmed a realistic template to mirror.
- `../../../.venv/bin/ruff check claude/.claude/scripts/mark-terminal.py`.
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  — confirms the new wrapper is discovered and passes.
- Manual smoke test (cannot run inside this sandboxed session — no
  controlling terminal is attached to it): after `./install.sh` re-stows
  the new `claude/.local/bin/mark-terminal` wrapper from the **main
  checkout**, run `mark-terminal <pid>` from one terminal window against
  a `claude` session's PID running in a different window, and confirm
  that window's tab title changes.

## Out of scope

- tmux/screen pane targeting — a multiplexer pane has no
  `/dev/ttysNNN` of its own (panes share the host terminal's single
  pty); reaching a specific pane needs a different mechanism (tmux's
  own `select-pane -T`) this plan deliberately doesn't add, since the
  motivating case is Terminal.app/iTerm2/Ghostty windows, not
  multiplexer panes.
- Cross-platform support (Linux `pts/N`, Windows) — this repo's stow
  package also installs on Linux and WSL2 (`README.md:9,91`), not just
  macOS, so this script guards itself with an explicit `platform.system()
  == "Darwin"` startup check (see Critical files) rather than silently
  misbehaving there; adding real Linux/WSL2 support is buildable later
  if a stow user needs it, not a reason to widen this script now.
- Restoring or auto-reverting a title later (no timeout, no undo).
- Automating window *focus* or bringing a window forward.
- Inspecting lock files or otherwise auto-detecting *which* PID is
  stuck — the PID is assumed to already be in hand.
