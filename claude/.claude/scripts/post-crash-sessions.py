#!/usr/bin/env python3
"""post-crash-sessions.py — enumerate Claude Code sessions orphaned by an
unclean shutdown and print a resume command for each one that is recoverable.

Read-only, always: writes no file, creates no directory, emits only to
stdout/stderr. Five evidence sources are cross-referenced per session id:

  A. The session registry (<config-dir>/sessions/<pid>.json) — Claude Code's
     own first-party, undocumented record of interactive sessions.
  B. Scheduled-task locks (<project>/.claude/scheduled_tasks.lock) — a dead
     pid here means that scheduled run died abnormally. Discovered as the
     union of two methods (exact cwd values harvested from the transcript
     corpus, and a bounded `find` sweep of $HOME) because each misses what
     the other catches.
  C. The transcript corpus (<config-dir>/projects/*/*.jsonl and
     */*/subagents/*.jsonl) — resolves whether a session id has any
     transcript at all, and supplies its cwd/gitBranch/last-activity.
  D. capture-session-id.sh's <config-dir>/sessions/<pid> bare lookup files —
     a session_id/pid mapping written at every SessionStart/SubagentStart
     and never swept on a clean exit. Admitted as evidence only when the
     file's own mtime sits within the crash-evidence window
     (--crash-window-hours), since an unwindowed dead pid here is the
     routine tail every session ever run leaves behind. This is the source
     that survives a non-reboot crash once Claude Code has already pruned
     source A.
  E. record-session-end.sh's <config-dir>/session-end-records/<pid> — a
     per-pid record written when Claude Code's SessionEnd hook fires,
     meaning that process shut down gracefully. No crash window is applied
     at read time. Instead, the match rule that consults it does its own
     mtime ordering against the dead entry it's explaining.

A dead pid's weight as crash evidence tracks whether its source deletes the
entry on a clean exit:

  - A and B are self-pruning first-party sources, so any surviving dead
    entry there is anomalous on its own.
  - D is never swept, so only its recency is meaningful, never its mere
    existence.
  - E is exculpatory, not incriminating. A record can only move a row out of
    possible-crash into confirmed-clean-exit, never the reverse. Its absence
    proves nothing, since a hard kill also leaves no record.
  - Boot time narrows classification where it's informative: a dead A/B
    entry that predates the last boot is definitively unclean, since the
    reboot explains the death.
  - A dead entry postdating boot, or a source-C/D session with no reboot in
    between it and now, is not thereby ruled non-actionable — both surface
    as "possible crash" rather than "unknown", since an application-level
    crash that never rebooted Linux looks exactly like an
    otherwise-unexplained death on a machine that stayed up.

Run this before starting new Claude Code sessions post-reboot regardless:
process ids restart low after a reboot, so a freshly launched session can
overwrite a crashed session's registry entry at the same pid.

Both the registry and the lock file are undocumented first-party formats,
observed on one machine at one CLI version — every field beyond the required
core (sessionId + pid) is read with .get() and a default, matching this
repo's existing posture for undocumented Claude Code state (see
statusline-command.sh's account-info block). The registry's procStart field
is platform-variant too: a `ps -o lstart=`-format date string on Darwin, an
all-digits /proc/<pid>/stat tick count on Linux — parsed by the stored
value's own shape, not by platform.

Env overrides:
  POST_CRASH_SESSIONS_FIND_ROOT   root for the scheduled_tasks.lock filesystem
                                   sweep instead of $HOME. Tests only — never
                                   sweeps the real $HOME otherwise.
"""
import argparse
import json
import math
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

from _config_dir import TRANSCRIPT_CONFIG_DIRS_LABEL, config_dir, declared_roots_file_state, declared_roots_matching

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The first cwd-bearing record in a real transcript sits at index 2-4 (line 1
# is always {mode, sessionId, type}); 12 is a generous bound past that with a
# safety margin, not a measured worst case. Records past this bound are never
# read: exhaustion means the session's cwd is unknown, not license to keep
# scanning for it.
_MAX_TRANSCRIPT_RECORDS = 12

# ps -o lstart= has whole-second resolution and the registry's own procStart
# capture can round independently, so exact equality is too strict.
_PROC_START_TOLERANCE_SECONDS = 2.0

# proc(5) field 22 (starttime), 1-indexed; minus 3 to land at index 19 of the
# post-comm field list: pid and comm are consumed by the rpartition(")")
# split (2 fields), and the remainder is 0-indexed (1 more).
_PROC_STAT_STARTTIME_INDEX = 19

# Empirically measured at ~18.9s for a full $HOME sweep on one Darwin
# machine; this bounds a hang on a slower disk or a large home tree, it does
# not guarantee the sweep completes within it on every machine.
_FIND_SWEEP_TIMEOUT_SECONDS = 25.0

# sysctl/ps are short-lived local commands querying local process state; this
# is a hang-detection backstop, not a measured value.
_SUBPROCESS_TIMEOUT_SECONDS = 5.0

# Sized to "was this session plausibly still open when the crash happened,"
# not write latency: an empirical sample of 11 crash-orphaned transcripts had
# gaps between last activity and boot ranging 29min-2h45m, so 4h covers that
# range with margin. Governs how far back any of this tool's crash evidence
# reaches: a boot-anchored transcript with no other corroboration, a
# now-anchored transcript with no reboot in between, and how recent a
# never-swept Source D lookup file's mtime must be to count as evidence
# rather than routine tail.
_CRASH_EVIDENCE_WINDOW_SECONDS = 14400.0

# Sized to one screenful on a typical terminal. Unlike token-analyzer.py's
# and analyze-context.py's uncapped-by-note [:N] truncations, this list
# drives a destructive rm command, so a truncation note is required rather
# than silent.
_LEGACY_DEAD_LIST_CAP = 20

# CLI versions this registry/lock schema has actually been read against.
# A version outside this set doesn't change how anything is parsed (every
# field beyond the required core is already read defensively) — it only
# earns a one-line "not validated" banner in the report.
_VALIDATED_REGISTRY_VERSIONS = frozenset({"2.1.221"})

# acquiredAt's epoch-millisecond unit is inferred, never confirmed by any
# schema — range-checked against year 2000..2100 in milliseconds before
# being trusted as a timestamp at all.
_MS_EPOCH_MIN = 946684800000
_MS_EPOCH_MAX = 4102444800000

# Matches this toolkit's SUBAGENT_SUBDIR (transcript-analysis.py) by value,
# not by import — this script is deliberately standalone (see docs/scripts.md).
_SUBAGENT_SUBDIR = "subagents"

_LSTART_FORMAT = "%a %b %d %H:%M:%S %Y"

_FIND_ROOT_ENV_VAR = "POST_CRASH_SESSIONS_FIND_ROOT"

CLASS_RESUMABLE = "resumable"
CLASS_CRASHED_NO_TRANSCRIPT = "crashed-no-transcript"
CLASS_LIVE_PROCESS = "live-process"
CLASS_UNKNOWN = "unknown"
CLASS_POSSIBLE_CRASH = "possible-crash"
CLASS_CONFIRMED_CLEAN_EXIT = "confirmed-clean-exit"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RegistryEntry:
    session_id: str
    pid: int
    proc_start: str | None
    cwd: str | None
    status: str | None
    started_at: float | None
    updated_at: float | None
    version: str | None
    mtime: float | None
    path: Path
    pid_mismatch: bool
    # The config dir this entry's sessions/ file was read from -- None only
    # when a test constructs an entry directly without one.
    config_dir: Path | None = None


@dataclass
class LockEntry:
    session_id: str
    pid: int
    proc_start: str | None
    acquired_at: float | None
    mtime: float | None
    path: Path


@dataclass
class LookupEntry:
    """capture-session-id.sh's <config-dir>/sessions/<pid> lookup file --
    never swept on a clean exit, so admission is mtime-windowed at read time
    rather than trusted unconditionally like the registry and lock sources."""
    session_id: str
    pid: int
    proc_start: str | None
    mtime: float | None
    path: Path
    # Derived from path's own <config_dir>/sessions/<pid> location -- never
    # None in practice, since every caller passes sessions/-rooted paths.
    config_dir: Path | None = None


@dataclass
class SessionEndRecord:
    """record-session-end.sh's <config-dir>/session-end-records/<pid> record
    -- written once, at SessionEnd, by the process that pid names. Purely
    exculpatory evidence: _graceful_end_record can only use it to move a dead
    entry out of possible-crash, never the reverse."""
    session_id: str
    pid: int
    reason: str | None
    mtime: float
    path: Path
    # The config dir this record was read from -- raw, unresolved, like every
    # other dataclass's config_dir field; resolved only at comparison time.
    config_dir: Path


@dataclass
class TranscriptInfo:
    session_id: str
    cwd: str | None
    git_branch: str | None
    first_seen_ts: float | None
    last_activity: float | None
    has_main: bool
    subagent_count: int
    path: Path
    # The config dir this transcript was found under -- None only when a
    # test constructs an entry directly without one.
    config_dir: Path | None = None


@dataclass
class SessionRow:
    session_id: str
    classification: str
    cwd: str | None
    git_branch: str | None
    last_activity: float | None
    detail: str
    entry_count: int
    cwd_missing: bool
    # The config dir this session's evidence was found under -- None when the
    # only evidence is a scheduled-task lock, which carries no account of its own.
    config_dir: Path | None = None


@dataclass
class Report:
    rows: list[SessionRow]
    boot_time: float | None
    ps_usable: bool
    unparsed_registry: int
    unparsed_lock: int
    legacy_bare_pid_dead: list[Path]
    find_timed_out: bool
    find_elapsed_seconds: float
    version_drift: list[str]
    pid_mismatches: list[Path]
    config_dirs: list[Path]
    any_sessions_dir_found: bool
    any_session_end_dir_found: bool


# ---------------------------------------------------------------------------
# Portability seams: boot time
# ---------------------------------------------------------------------------

def _boot_time_darwin(*, run=subprocess.run) -> float | None:
    """kern.boottime is Darwin/BSD-only: 'kern.boottime: { sec = N, usec = M } ...'."""
    try:
        result = run(
            ["sysctl", "kern.boottime"], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
    return float(match.group(1)) if match else None


def _boot_time_linux(proc_stat_path: Path = Path("/proc/stat")) -> float | None:
    """Linux has no kern.boottime; the equivalent is the 'btime <epoch>' line in /proc/stat."""
    try:
        with open(proc_stat_path) as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _boot_time(*, system: str | None = None, darwin_fn=_boot_time_darwin, linux_fn=_boot_time_linux) -> float | None:
    system = platform.system() if system is None else system
    if system == "Darwin":
        return darwin_fn()
    if system == "Linux":
        return linux_fn()
    return None


# ---------------------------------------------------------------------------
# Portability seams: process liveness
# ---------------------------------------------------------------------------

def _ps_lstart(pid: int, *, run=subprocess.run) -> str | None:
    """Query ps for pid's process start time, pinned to UTC/C locale so the
    result is directly comparable to the registry's own UTC-formatted
    procStart string with no timezone conversion — pinning both sides to the
    same locale turns the PID-reuse guard into a plain string/tolerance
    compare instead of parsing an ambient-locale month name.

    Empty stdout means dead, never the return code: macOS and Linux disagree
    on ps's exit status for an invalid/absent pid.
    """
    env = dict(os.environ)
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    try:
        result = run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout.strip()
    return output or None


def _ps_lstart_batch(pids: list[int], *, run=subprocess.run) -> dict[int, str]:
    """Query ps once for every pid in pids, pinned to UTC/C locale like
    _ps_lstart. One call replaces one call per pid — build_report uses this
    to avoid spawning a subprocess per registry/lock/legacy entry in a loop.
    A pid ps has nothing to report for (dead, or the whole batch call
    failing) is simply absent from the returned dict, same as _ps_lstart's
    None. -p accepts a comma-separated pid list per POSIX; not chunked,
    since a personal machine's session count never approaches ps's
    command-line-length ceiling.
    """
    if not pids:
        return {}
    env = dict(os.environ)
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    try:
        result = run(
            ["ps", "-p", ",".join(str(p) for p in pids), "-o", "pid=,lstart="],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    starts: dict[int, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        starts[int(parts[0])] = parts[1].strip()
    return starts


def _ps_usable(*, ps_lstart=_ps_lstart, self_pid: int | None = None) -> bool:
    """Self-test: query our own pid (guaranteed alive) and confirm ps
    understands -o lstart=. A minimal ps (e.g. busybox) can silently return
    nothing for an unsupported output specifier rather than erroring, which
    would otherwise be indistinguishable from every pid being dead."""
    pid = os.getpid() if self_pid is None else self_pid
    return ps_lstart(pid) is not None


def _proc_starttime_ticks(pid: int, *, proc_root: Path = Path("/proc")) -> int | None:
    """Linux-only: field 22 (starttime) of /proc/<pid>/stat, a count of
    clock ticks since boot — proc(5): 'the time the process started after
    system boot ... expressed in clock ticks'. comm (field 2) is
    parenthesized and may itself contain spaces and closing parens, so the
    split happens after the last ")" in the line, never the first. Missing
    or unreadable file, or a line shorter than expected, yields None
    (indeterminate) rather than raising."""
    try:
        text = (proc_root / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = text.rpartition(")")[2].split()
    try:
        return int(fields[_PROC_STAT_STARTTIME_INDEX])
    except (IndexError, ValueError):
        return None


def _parse_lstart(raw: str | None, tz: timezone) -> datetime | None:
    """Pure parse of one ps -o lstart=-format string. tz is a parameter, never
    read from the process clock, so this never depends on the ambient
    environment — only on what the caller passes in."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), _LSTART_FORMAT).replace(tzinfo=tz)
    except ValueError:
        return None


def _same_process(
    stored_proc_start: str | None,
    live_lstart: str | None,
    *,
    tolerance_seconds: float = _PROC_START_TOLERANCE_SECONDS,
    tz: timezone = UTC,
) -> bool | None:
    """True/False if both sides parse (within tolerance_seconds), else None
    when either side is missing or unparseable — a pid that is alive but
    whose sameness can't be confirmed is not evidence either way."""
    stored = _parse_lstart(stored_proc_start, tz)
    live = _parse_lstart(live_lstart, tz)
    if stored is None or live is None:
        return None
    return abs((stored - live).total_seconds()) <= tolerance_seconds


def _proc_start_is_numeric(proc_start: str | None) -> bool:
    """True when the stored procStart is a Linux /proc/<pid>/stat field-22
    tick count (all-digits) rather than a Darwin/BSD `ps -o lstart=` date
    string — dispatches on the stored value's own shape, not on
    platform.system(), so this stays correct if a future CLI version
    changes format again on either platform."""
    return isinstance(proc_start, str) and proc_start.isdigit()


def _proc_start_comparable(mtime: float | None, boot_time: float | None) -> bool:
    """A numeric procStart's ticks are an offset from *a* boot, so they are
    only meaningfully comparable to a live process's current ticks when both
    were captured under the same boot — true whenever the entry's own file
    mtime postdates the last boot. Unknown boot_time or mtime is not
    comparable, matching _safe_mtime's None-means-unknown contract."""
    return boot_time is not None and mtime is not None and mtime >= boot_time


def _same_process_by_proc_starttime(
    stored_proc_start: str | None, pid: int, *, proc_starttime_ticks,
) -> bool | None:
    """Exact-integer-equality compare of a Linux /proc/<pid>/stat field-22
    tick count against the live pid's own current value — no tolerance,
    since both sides are the same integer clock with no unit conversion or
    independent rounding to reconcile."""
    if not _proc_start_is_numeric(stored_proc_start):
        return None
    live_ticks = proc_starttime_ticks(pid)
    if live_ticks is None:
        return None
    return int(stored_proc_start) == live_ticks


def _entry_liveness(
    pid: int,
    proc_start: str | None,
    *,
    ps_lstart,
    ps_usable: bool,
    proc_starttime_ticks=_proc_starttime_ticks,
    proc_start_comparable: bool = False,
) -> str:
    """Return 'live', 'dead', or 'indeterminate' for one registry/lock/lookup
    entry. A numeric (Linux ticks) proc_start dispatches to the ticks
    comparison only when proc_start_comparable is True; otherwise it's
    indeterminate rather than compared against a possibly-stale pre-boot
    value. A non-numeric proc_start takes the lstart-string comparison
    path."""
    if not ps_usable:
        return "indeterminate"
    live_lstart = ps_lstart(pid)
    if live_lstart is None:
        return "dead"
    if _proc_start_is_numeric(proc_start):
        same = (
            _same_process_by_proc_starttime(proc_start, pid, proc_starttime_ticks=proc_starttime_ticks)
            if proc_start_comparable else None
        )
    else:
        same = _same_process(proc_start, live_lstart)
    if same is True:
        return "live"
    if same is False:
        # A different process now holds this pid; our tracked process is gone.
        return "dead"
    return "indeterminate"


# ---------------------------------------------------------------------------
# Field-allowlist helpers
# ---------------------------------------------------------------------------

def _ms_to_seconds(value) -> float | None:
    """Range-check an assumed epoch-millisecond value before trusting it —
    the unit is inferred, never confirmed by any schema."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not (_MS_EPOCH_MIN <= value <= _MS_EPOCH_MAX):
        return None
    return value / 1000.0


def _coerce_pid(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _sanitize_for_terminal(value) -> str | None:
    """Strip control/escape bytes before a value can ever reach rendered
    output. cwd, gitBranch, and sessionId are read from local state, but
    gitBranch and cwd both trace back to strings someone else could choose
    (a branch name, a directory name) — an unstripped ESC byte would let a
    crafted one inject terminal escape sequences (OSC title-set, clipboard
    writes, output hiding) at render time, the same reasoning
    statusline-command.sh's account-info block already applies to
    ~/.claude.json fields. A non-string value (schema drift, or a hostile
    field of the wrong JSON type) degrades to None rather than raising."""
    if not isinstance(value, str):
        return None
    return "".join(ch for ch in value if ord(ch) >= 0x20 and ch != "\x7f")


def _safe_mtime(path: Path) -> float | None:
    """path.stat() can fail on a real, narrow TOCTOU window — a prior read of
    path succeeded, then it became unreadable before stat(). None means
    unknown, never a substitute timestamp: a stat failure silently coerced to
    epoch 0 would sort as older than everything and, if ever compared against
    boot_time, would misclassify an unknown-age entry as pre-boot evidence."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _max_optional_float(a: float | None, b: float | None) -> float | None:
    """max() that treats None as unknown rather than negative infinity — an
    unknown value never wins, but two unknowns stay unknown instead of
    collapsing to a value that would render as a bogus epoch timestamp."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _parse_iso_ts(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_age(seconds: float) -> str:
    """Single floored unit — '45m old', '3h old', '12d old' — never fractional."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m old"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h old"
    days = hours // 24
    return f"{days}d old"


# ---------------------------------------------------------------------------
# Source A — session registry
# ---------------------------------------------------------------------------

def _read_registry(config_dirs: list[Path]) -> tuple[list[RegistryEntry], list[Path], int, bool]:
    """Read <config_dir>/sessions/<pid>.json across every supplied config dir.

    Returns (entries, legacy_bare_pid_paths, unparsed_count, any_sessions_dir_found).
    Legacy bare-pid files (no .json suffix, written by capture-session-id.sh)
    are collected separately — they are a different, still-active mechanism,
    not part of this registry.
    """
    entries: list[RegistryEntry] = []
    legacy_paths: list[Path] = []
    unparsed = 0
    any_sessions_dir_found = False
    for cdir in config_dirs:
        sessions_dir = cdir / "sessions"
        if not sessions_dir.is_dir():
            continue
        any_sessions_dir_found = True
        try:
            candidates = sorted(sessions_dir.iterdir())
        except OSError:
            continue
        for path in candidates:
            if not path.is_file():
                continue
            if path.suffix != ".json":
                if path.name.isdigit():
                    legacy_paths.append(path)
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                unparsed += 1
                continue
            if not isinstance(data, dict):
                unparsed += 1
                continue
            session_id = data.get("sessionId")
            pid = _coerce_pid(data.get("pid"))
            if not isinstance(session_id, str) or not session_id or pid is None:
                unparsed += 1
                continue
            session_id = _sanitize_for_terminal(session_id)
            filename_stem = path.stem
            pid_mismatch = filename_stem.isdigit() and int(filename_stem) != pid
            entries.append(RegistryEntry(
                session_id=session_id, pid=pid,
                proc_start=data.get("procStart"),
                cwd=_sanitize_for_terminal(data.get("cwd")), status=data.get("status"),
                started_at=_ms_to_seconds(data.get("startedAt")),
                updated_at=_ms_to_seconds(data.get("updatedAt")),
                version=_sanitize_for_terminal(data.get("version")), mtime=_safe_mtime(path), path=path,
                pid_mismatch=pid_mismatch, config_dir=cdir,
            ))
    return entries, legacy_paths, unparsed, any_sessions_dir_found


# ---------------------------------------------------------------------------
# Source B — scheduled-task locks
# ---------------------------------------------------------------------------

def _read_lock(path: Path) -> LockEntry | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    session_id = data.get("sessionId")
    pid = _coerce_pid(data.get("pid"))
    if not isinstance(session_id, str) or not session_id or pid is None:
        return None
    session_id = _sanitize_for_terminal(session_id)
    return LockEntry(
        session_id=session_id, pid=pid, proc_start=data.get("procStart"),
        acquired_at=_ms_to_seconds(data.get("acquiredAt")), mtime=_safe_mtime(path), path=path,
    )


def _cwd_harvest_lock_paths(cwds: set[str]) -> list[Path]:
    """Reaches worktree locks nested below the find sweep's depth limit —
    exact, since every cwd came from a record that actually wrote there."""
    found = []
    for raw_cwd in cwds:
        candidate = Path(raw_cwd) / ".claude" / "scheduled_tasks.lock"
        if candidate.is_file():
            found.append(candidate)
    return found


def _find_scheduled_task_locks(
    find_root: Path, *, timeout_seconds: float = _FIND_SWEEP_TIMEOUT_SECONDS, run=subprocess.run
) -> tuple[list[Path], bool, float]:
    """Bounded `find` sweep for scheduled_tasks.lock under find_root.

    -xdev stays within one filesystem: cloud-storage and network mounts under
    $HOME can force file hydration or block indefinitely. No -L: a symlinked
    lock is not this sweep's concern. Returns (paths, timed_out, elapsed).
    """
    root = find_root.resolve()
    started = time.monotonic()
    try:
        result = run(
            ["find", str(root), "-xdev", "-maxdepth", "6", "-type", "f", "-name", "scheduled_tasks.lock"],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return [], True, time.monotonic() - started
    except OSError:
        return [], True, time.monotonic() - started
    elapsed = time.monotonic() - started
    paths = [Path(line) for line in result.stdout.splitlines() if line]
    return paths, False, elapsed


# ---------------------------------------------------------------------------
# Source C — transcript corpus
# ---------------------------------------------------------------------------

def _read_transcript_head(jsonl: Path, max_records: int) -> tuple[bool, str | None, str | None, str | None]:
    """Read up to max_records records looking for the first cwd-bearing one.

    Extracts only cwd, gitBranch, and timestamp — sessionId is taken from the
    filename stem (this toolkit's existing convention: transcript-analysis.py
    treats jsonl.stem as the canonical session id throughout). message,
    content, and toolUseResult are never bound to a variable.

    Returns (any_record_parsed, cwd, git_branch, timestamp).
    """
    try:
        if jsonl.stat().st_size == 0:
            return False, None, None, None
    except OSError:
        return False, None, None, None
    any_parsed = False
    cwd: str | None = None
    git_branch: str | None = None
    timestamp: str | None = None
    try:
        with open(jsonl) as fh:
            for _ in range(max_records):
                raw = fh.readline()
                if not raw:
                    break
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                any_parsed = True
                if isinstance(rec, dict) and "cwd" in rec:
                    cwd = _sanitize_for_terminal(rec.get("cwd"))
                    git_branch = _sanitize_for_terminal(rec.get("gitBranch"))
                    timestamp = rec.get("timestamp")
                    break
    except OSError:
        return any_parsed, cwd, git_branch, timestamp
    return any_parsed, cwd, git_branch, timestamp


def _scan_transcripts(
    config_dirs: list[Path], *, max_records: int = _MAX_TRANSCRIPT_RECORDS
) -> tuple[dict[str, TranscriptInfo], set[str]]:
    """One bounded pass over the transcript corpus across every config dir.

    Returns (transcripts keyed by session id, every cwd value discovered —
    including from subagent files, which is what lets the cwd-harvest half
    of source B reach a nested worktree lock the depth-limited find misses).
    """
    transcripts: dict[str, TranscriptInfo] = {}
    cwds: set[str] = set()

    for cdir in config_dirs:
        projects_dir = cdir / "projects"
        if not projects_dir.is_dir():
            continue

        for jsonl in sorted(projects_dir.glob("*/*.jsonl")):
            has_record, cwd, git_branch, ts_raw = _read_transcript_head(jsonl, max_records)
            if cwd:
                cwds.add(cwd)
            if not has_record:
                continue
            # jsonl.stem is a filename, not a validated field — POSIX permits raw
            # ESC/BEL bytes there, so it is sanitized like every other untrusted
            # session id before being used as this session's key and identity.
            session_id = _sanitize_for_terminal(jsonl.stem)
            transcripts[session_id] = TranscriptInfo(
                session_id=session_id, cwd=cwd, git_branch=git_branch,
                first_seen_ts=_parse_iso_ts(ts_raw), last_activity=_safe_mtime(jsonl),
                has_main=True, subagent_count=0, path=jsonl, config_dir=cdir,
            )

        for sub_jsonl in sorted(projects_dir.glob(f"*/*/{_SUBAGENT_SUBDIR}/*.jsonl")):
            has_record, cwd, _git_branch, _ts_raw = _read_transcript_head(sub_jsonl, max_records)
            if cwd:
                cwds.add(cwd)
            if not has_record:
                continue
            # Sanitized the same way as the main-loop session id above, so a
            # session with both a main transcript and subagent transcripts
            # keys to the same dict entry instead of splitting into two.
            parent_session_id = _sanitize_for_terminal(sub_jsonl.parents[1].name)
            sub_mtime = _safe_mtime(sub_jsonl)
            info = transcripts.get(parent_session_id)
            if info is None:
                info = TranscriptInfo(
                    session_id=parent_session_id, cwd=None, git_branch=None,
                    first_seen_ts=None, last_activity=None,
                    has_main=False, subagent_count=0, path=sub_jsonl, config_dir=cdir,
                )
                transcripts[parent_session_id] = info
            info.subagent_count += 1
            info.last_activity = _max_optional_float(info.last_activity, sub_mtime)

    return transcripts, cwds


# ---------------------------------------------------------------------------
# Source D — capture-session-id.sh lookup files (never swept on clean exit)
# ---------------------------------------------------------------------------

def _read_lookup_entries(
    paths: list[Path], *, now: float | None, window_seconds: float = _CRASH_EVIDENCE_WINDOW_SECONDS,
) -> list[LookupEntry]:
    """Parse capture-session-id.sh's <config-dir>/sessions/<pid> lookup
    files: line 1 is the session id, line 2 (when present) is a
    `ps -o lstart=`-format start time. This corpus is never swept on a
    clean exit (capture-session-id.sh's own documented posture), so a file
    is admitted as evidence only when its mtime sits within window_seconds
    of now — every older file is the routine tail every session ever run
    leaves behind, not crash evidence. now=None (build_report's fail-closed
    default) admits nothing.
    """
    if now is None:
        return []
    entries: list[LookupEntry] = []
    for path in paths:
        pid = _coerce_pid(path.name)
        if pid is None:
            continue
        mtime = _safe_mtime(path)
        if mtime is None or now - mtime > window_seconds:
            continue
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        if not lines:
            continue
        session_id = _sanitize_for_terminal(lines[0])
        if not session_id:
            continue
        # A file with fewer than two lines is not an error: proc_start=None
        # only makes a *live* pid's liveness indeterminate; a dead pid is
        # still dead regardless (_entry_liveness checks ps before proc_start).
        proc_start = lines[1] if len(lines) > 1 else None
        entries.append(LookupEntry(
            session_id=session_id, pid=pid, proc_start=proc_start, mtime=mtime,
            path=path, config_dir=path.parent.parent,
        ))
    return entries


# ---------------------------------------------------------------------------
# Source E — record-session-end.sh SessionEnd records
# ---------------------------------------------------------------------------

def _read_session_end_records(
    config_dirs: list[Path],
) -> tuple[dict[tuple[Path, int], SessionEndRecord], bool]:
    """Read <config_dir>/session-end-records/<pid> across every supplied
    config dir.

    Returns (records keyed by (resolved config dir, pid), any_dir_found).
    Keying the dict by the *resolved* config dir means a lookup keyed the
    same way already satisfies half of _graceful_end_record's match rule
    (condition 1: both sides' config dir resolve and are equal). No crash
    window is applied on read -- staleness is entirely the match rule's
    concern, and a window here would only re-suppress valid exculpatory
    evidence for a session that ended long ago.
    """
    records: dict[tuple[Path, int], SessionEndRecord] = {}
    any_dir_found = False
    for cdir in config_dirs:
        records_dir = cdir / "session-end-records"
        if not records_dir.is_dir():
            continue
        any_dir_found = True
        try:
            candidates = sorted(records_dir.iterdir())
        except OSError:
            continue
        for path in candidates:
            pid = _coerce_pid(path.name)
            if pid is None:
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            session_id = data.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                continue
            mtime = _safe_mtime(path)
            if mtime is None:
                continue
            key = (cdir.resolve(), pid)
            existing = records.get(key)
            if existing is not None and existing.mtime >= mtime:
                continue
            records[key] = SessionEndRecord(
                session_id=_sanitize_for_terminal(session_id), pid=pid,
                reason=_sanitize_for_terminal(data.get("reason")), mtime=mtime, path=path, config_dir=cdir,
            )
    return records, any_dir_found


def _graceful_end_record(
    entry: RegistryEntry | LookupEntry, records: dict[tuple[Path, int], SessionEndRecord],
) -> SessionEndRecord | None:
    """The match rule: a record explains a dead entry iff all three hold:
    (1) both sides' config dir resolve and are equal, (2) pids are equal,
    (3) the record's mtime is not older than the entry's. Condition 1 is
    satisfied by looking the record up under entry's own resolved config
    dir, since records is keyed the same way. Condition 3 is >=, not >:
    an exact mtime tie counts as a match, and is what makes pid reuse
    safe without a stored process-identity field -- every session writes
    its start-side evidence at the same pid it later writes its end
    record to, so a process reusing pid P necessarily rewrites P's entry
    after the previous occupant's record.
    """
    if entry.config_dir is None or entry.mtime is None:
        return None
    record = records.get((entry.config_dir.resolve(), entry.pid))
    if record is None or record.mtime < entry.mtime:
        return None
    return record


def _graceful_end_coverage(
    entries: list[RegistryEntry] | list[LookupEntry], records: dict[tuple[Path, int], SessionEndRecord],
) -> tuple[int, int, list[SessionEndRecord]]:
    """Coverage of a dead-entry list by graceful-exit records: how many of
    entries have a matching SessionEnd record, out of how many total, plus
    the matched records themselves (used to cite the newest one's reason and
    time when coverage is full). A record is exculpatory only -- this can
    move a row out of possible-crash, never promote a row that had no other
    evidence for it."""
    matched = [record for e in entries if (record := _graceful_end_record(e, records)) is not None]
    return len(matched), len(entries), matched


def _partial_coverage_note(covered: int, total: int) -> str:
    return (
        f" {covered} of {total} tracked process instances for this session recorded a graceful "
        "SessionEnd; at least one did not."
    )


def _confirmed_clean_exit_detail(
    newest_record: SessionEndRecord, *, has_main_transcript: bool, subagent_note: str,
) -> str:
    reason_note = f"reason {newest_record.reason}" if newest_record.reason else "no reason recorded"
    clean_exit_note = (
        "a graceful SessionEnd was recorded for every tracked process instance for this session "
        f"(newest: {reason_note}, {_fmt_ts(newest_record.mtime)})."
    )
    if has_main_transcript:
        return f"{clean_exit_note} A transcript exists for this session."
    return f"{clean_exit_note} No main transcript was found for this session.{subagent_note}"


def _recent_transcript_only_ids(
    transcripts: dict[str, TranscriptInfo],
    known_session_ids: set[str],
    boot_time: float | None,
    *,
    window_seconds: float = _CRASH_EVIDENCE_WINDOW_SECONDS,
    now: float | None = None,
) -> list[str]:
    """Session ids with a transcript but no registry, lock, or lookup entry
    at all, admitted on either of two disjuncts: last activity sits just
    before boot (a reboot may have killed it), or last activity sits
    recently regardless of boot (a same-day, non-reboot crash is never
    separated from now by a reboot at all) — corroborating-only evidence
    that a crash may have happened without leaving any other trace."""
    ids = []
    for sid, info in transcripts.items():
        if sid in known_session_ids or not info.has_main:
            continue
        last_activity = info.last_activity if info.last_activity is not None else 0.0
        boot_anchored = boot_time is not None and boot_time - window_seconds <= last_activity <= boot_time
        now_anchored = now is not None and last_activity >= now - window_seconds
        if boot_anchored or now_anchored:
            ids.append(sid)
    return ids


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _cwd_missing(cwd: str | None) -> bool:
    if not cwd:
        return False
    return not Path(cwd).is_dir()


def _best_effort_location(
    registry_entries: list[RegistryEntry], lock_entries: list[LockEntry], transcript: TranscriptInfo | None
) -> tuple[str | None, str | None, float | None]:
    if transcript is not None and transcript.has_main:
        return transcript.cwd, transcript.git_branch, transcript.last_activity
    if registry_entries:
        newest = max(registry_entries, key=lambda e: e.mtime or 0.0)
        return newest.cwd, None, (newest.updated_at or newest.mtime)
    if lock_entries:
        newest = max(lock_entries, key=lambda e: e.mtime or 0.0)
        return None, None, (newest.acquired_at or newest.mtime)
    if transcript is not None:
        return None, None, transcript.last_activity
    return None, None, None


def _classify_session(
    session_id: str,
    registry_entries: list[RegistryEntry],
    lock_entries: list[LockEntry],
    transcript: TranscriptInfo | None,
    *,
    boot_time: float | None,
    ps_lstart,
    ps_usable: bool,
    proc_starttime_ticks=_proc_starttime_ticks,
    near_boot_window_seconds: float = _CRASH_EVIDENCE_WINDOW_SECONDS,
    lookup_entries: tuple[LookupEntry, ...] = (),
    now: float | None = None,
    session_end_records: dict[tuple[Path, int], SessionEndRecord] | None = None,
) -> SessionRow:
    entry_count = len(registry_entries) + len(lock_entries) + len(lookup_entries)
    has_main_transcript = transcript is not None and transcript.has_main
    session_end_records = session_end_records or {}
    # A scheduled-task lock's path lives under the session's own cwd, not
    # under any declared config dir, so it carries no account attribution;
    # prefer the transcript's config dir, falling back to the registry's,
    # falling back to the lookup file's own <config_dir>/sessions/ location.
    row_config_dir = (
        transcript.config_dir if transcript is not None
        else (registry_entries[0].config_dir if registry_entries
              else (lookup_entries[0].config_dir if lookup_entries else None))
    )
    subagent_note = ""
    if transcript is not None and transcript.subagent_count and not has_main_transcript:
        plural = "s" if transcript.subagent_count != 1 else ""
        subagent_note = (
            f" ({transcript.subagent_count} subagent transcript file{plural} exist under this "
            "session's directory, but the main thread never wrote one.)"
        )

    if not ps_usable:
        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
            "ps did not return usable output on this system; liveness could not be confirmed for any entry.",
            entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
        )

    liveness: dict[tuple[str, int], str] = {}
    for source_name, entries in (("registry", registry_entries), ("lock", lock_entries), ("lookup", lookup_entries)):
        for e in entries:
            liveness[(source_name, e.pid)] = _entry_liveness(
                e.pid, e.proc_start, ps_lstart=ps_lstart, ps_usable=ps_usable,
                proc_starttime_ticks=proc_starttime_ticks,
                proc_start_comparable=_proc_start_comparable(e.mtime, boot_time),
            )

    if "live" in liveness.values():
        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_LIVE_PROCESS, cwd, branch, last_activity,
            "a live process matches a tracked pid; not crash evidence.",
            entry_count, False, config_dir=row_config_dir,
        )

    if registry_entries:
        if boot_time is None:
            cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                "boot time could not be determined on this platform, so registry entries cannot be dated against it.",
                entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
            )

        dead_before_boot = [
            e for e in registry_entries
            if liveness[("registry", e.pid)] == "dead" and e.mtime is not None and e.mtime < boot_time
        ]
        dead_after_boot = [
            e for e in registry_entries
            if liveness[("registry", e.pid)] == "dead" and e.mtime is not None and e.mtime >= boot_time
        ]
        mtime_unknown = [
            e for e in registry_entries
            if liveness[("registry", e.pid)] == "dead" and e.mtime is None
        ]

        if dead_before_boot:
            newest = max(dead_before_boot, key=lambda e: e.mtime)
            cwd = (transcript.cwd if has_main_transcript else None) or newest.cwd
            branch = transcript.git_branch if has_main_transcript else None
            last_activity = transcript.last_activity if transcript is not None else (newest.updated_at or newest.mtime)
            boot_note = f"registry entry written {_fmt_ts(newest.mtime)} (boot was {_fmt_ts(boot_time)}), before boot."
            if has_main_transcript:
                return SessionRow(
                    session_id, CLASS_RESUMABLE, cwd, branch, last_activity,
                    f"{boot_note} A transcript exists for this session.",
                    entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                )
            return SessionRow(
                session_id, CLASS_CRASHED_NO_TRANSCRIPT, cwd, branch, last_activity,
                f"{boot_note} No main transcript was found for this session.{subagent_note}",
                entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
            )

        if dead_after_boot:
            newest = max(dead_after_boot, key=lambda e: e.mtime)
            boot_note = (
                f"registry entry written {_fmt_ts(newest.mtime)}, after boot ({_fmt_ts(boot_time)}) — "
                "the process's death is unexplained by a reboot, which is what an unclean application "
                "crash looks like, but a deliberate clean exit looks identical."
            )
            covered, total, matched_records = _graceful_end_coverage(dead_after_boot, session_end_records)
            coverage_note = _partial_coverage_note(covered, total) if 0 < covered < total else ""
            if has_main_transcript:
                cwd = transcript.cwd or newest.cwd
                branch = transcript.git_branch
                last_activity = transcript.last_activity
                if covered == total:
                    # matched_records is non-empty here: this branch only runs inside
                    # `if dead_after_boot:`, so total >= 1 and covered == total >= 1.
                    newest_record = max(matched_records, key=lambda r: r.mtime)
                    detail = _confirmed_clean_exit_detail(
                        newest_record, has_main_transcript=True, subagent_note=subagent_note,
                    )
                    return SessionRow(
                        session_id, CLASS_CONFIRMED_CLEAN_EXIT, cwd, branch, last_activity,
                        detail, entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                    )
                return SessionRow(
                    session_id, CLASS_POSSIBLE_CRASH, cwd, branch, last_activity,
                    f"{boot_note} A transcript exists for this session.{coverage_note}",
                    entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                )
            cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
            if covered == total:
                # matched_records is non-empty here: this branch only runs inside
                # `if dead_after_boot:`, so total >= 1 and covered == total >= 1.
                newest_record = max(matched_records, key=lambda r: r.mtime)
                detail = _confirmed_clean_exit_detail(
                    newest_record, has_main_transcript=False, subagent_note=subagent_note,
                )
                return SessionRow(
                    session_id, CLASS_CONFIRMED_CLEAN_EXIT, cwd, branch, last_activity,
                    detail, entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                )
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                f"{boot_note} No main transcript was found for this session.{subagent_note}{coverage_note}",
                entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
            )

        if mtime_unknown:
            cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                "registry entry's file modification time could not be read, so this session cannot be dated "
                "against boot time.",
                entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
            )

        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
            "registry procStart could not be parsed; this session's pid liveness could not be confirmed.",
            entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
        )

    if lookup_entries:
        dead_lookups = [e for e in lookup_entries if liveness[("lookup", e.pid)] == "dead"]
        indeterminate_lookups = [e for e in lookup_entries if liveness[("lookup", e.pid)] == "indeterminate"]
        if dead_lookups and not indeterminate_lookups:
            cwd = transcript.cwd if has_main_transcript else None
            branch = transcript.git_branch if has_main_transcript else None
            last_activity = (
                transcript.last_activity if transcript is not None
                else max((e.mtime or 0.0) for e in dead_lookups)
            )
            # This source is never swept on a clean exit, so a dead pid alone
            # is the routine end state of every session that ever ran, including
            # cleanly-exited ones -- it bounds *when* the session ended, not *how*,
            # so it never promotes to Resumable the way a self-pruning source does.
            covered, total, matched_records = _graceful_end_coverage(dead_lookups, session_end_records)
            coverage_note = _partial_coverage_note(covered, total) if 0 < covered < total else ""
            if covered == total:
                # matched_records is non-empty here: this branch only runs inside
                # `if dead_lookups and not indeterminate_lookups:`, so total >= 1
                # and covered == total >= 1.
                newest_record = max(matched_records, key=lambda r: r.mtime)
                detail = _confirmed_clean_exit_detail(
                    newest_record, has_main_transcript=has_main_transcript, subagent_note=subagent_note,
                )
                return SessionRow(
                    session_id, CLASS_CONFIRMED_CLEAN_EXIT, cwd, branch, last_activity,
                    detail, entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                )
            if has_main_transcript:
                return SessionRow(
                    session_id, CLASS_POSSIBLE_CRASH, cwd, branch, last_activity,
                    "a capture-session-id.sh lookup file's pid is dead and its mtime sits within the "
                    f"crash-evidence window; a transcript exists for this session.{coverage_note}",
                    entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                )
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                f"a capture-session-id.sh lookup file's pid is dead and its mtime sits within the "
                f"crash-evidence window, but no main transcript was found for this session.{subagent_note}{coverage_note}",
                entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
            )
        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
            "a capture-session-id.sh lookup file's liveness could not be confirmed for this session.",
            entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
        )

    if lock_entries:
        dead_locks = [e for e in lock_entries if liveness[("lock", e.pid)] == "dead"]
        indeterminate_locks = [e for e in lock_entries if liveness[("lock", e.pid)] == "indeterminate"]
        if dead_locks and not indeterminate_locks:
            cwd = transcript.cwd if has_main_transcript else None
            branch = transcript.git_branch if has_main_transcript else None
            last_activity = (
                transcript.last_activity if transcript is not None
                else max((e.mtime or 0.0) for e in dead_locks)
            )
            if has_main_transcript:
                return SessionRow(
                    session_id, CLASS_RESUMABLE, cwd, branch, last_activity,
                    "scheduled-task lock's pid is dead; a transcript exists for this session.",
                    entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
                )
            return SessionRow(
                session_id, CLASS_CRASHED_NO_TRANSCRIPT, cwd, branch, last_activity,
                f"scheduled-task lock's pid is dead; no transcript was found for this session.{subagent_note}",
                entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
            )
        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
            "scheduled-task lock's procStart could not be parsed; liveness could not be confirmed.",
            entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
        )

    # No registry, lock, or lookup entry -- reached only for a session admitted solely
    # via _recent_transcript_only_ids's boot-anchored or now-anchored disjunct.
    cwd = transcript.cwd if transcript is not None else None
    branch = transcript.git_branch if transcript is not None else None
    last_activity = transcript.last_activity if transcript is not None else None
    activity_for_anchor = last_activity if last_activity is not None else 0.0
    boot_anchored = (
        boot_time is not None
        and boot_time - near_boot_window_seconds <= activity_for_anchor <= boot_time
    )
    now_anchored = now is not None and activity_for_anchor >= now - near_boot_window_seconds
    if boot_anchored:
        anchor_note = f"its last activity sits within {near_boot_window_seconds / 3600:g}h before the last boot"
    elif now_anchored:
        # No reboot separates last activity from now -- the non-reboot-crash
        # shape this anchor exists to catch.
        anchor_note = (
            f"its last activity sits within {near_boot_window_seconds / 3600:g}h of now, "
            "with no reboot in between to explain the gap"
        )
    else:
        # Reached with neither disjunct matching: only possible when this
        # function is called directly rather than through build_report's own
        # admission gate (_recent_transcript_only_ids).
        anchor_note = "its last activity could not be dated against either the last boot or now"
    return SessionRow(
        session_id, CLASS_POSSIBLE_CRASH, cwd, branch, last_activity,
        f"only a transcript exists, with no registry, lock, or lookup-file entry; {anchor_note}, but "
        "with no other corroboration this cannot confirm the session was still open at crash time.",
        entry_count, _cwd_missing(cwd), config_dir=row_config_dir,
    )


# ---------------------------------------------------------------------------
# Report construction
# ---------------------------------------------------------------------------

def build_report(
    *,
    config_dirs: list[Path],
    find_root: Path,
    ps_lstart=_ps_lstart,
    boot_time_fn=_boot_time,
    proc_starttime_ticks_fn=_proc_starttime_ticks,
    near_boot_window_seconds: float = _CRASH_EVIDENCE_WINDOW_SECONDS,
    now: float | None = None,
) -> Report:
    boot_time = boot_time_fn()

    registry_entries, legacy_bare_pid_paths, unparsed_registry, any_sessions_dir_found = _read_registry(config_dirs)
    transcripts, harvested_cwds = _scan_transcripts(config_dirs)

    lock_candidate_paths: set[Path] = set(_cwd_harvest_lock_paths(harvested_cwds))
    find_paths, find_timed_out, find_elapsed = _find_scheduled_task_locks(find_root)
    lock_candidate_paths |= set(find_paths)

    lock_entries: list[LockEntry] = []
    unparsed_lock = 0
    for path in sorted(lock_candidate_paths):
        entry = _read_lock(path)
        if entry is None:
            unparsed_lock += 1
        else:
            lock_entries.append(entry)

    lookup_entries = _read_lookup_entries(
        legacy_bare_pid_paths, now=now, window_seconds=near_boot_window_seconds,
    )
    session_end_records, any_session_end_dir_found = _read_session_end_records(config_dirs)

    registry_by_session: dict[str, list[RegistryEntry]] = {}
    for e in registry_entries:
        registry_by_session.setdefault(e.session_id, []).append(e)
    lock_by_session: dict[str, list[LockEntry]] = {}
    for e in lock_entries:
        lock_by_session.setdefault(e.session_id, []).append(e)
    lookup_by_session: dict[str, list[LookupEntry]] = {}
    for e in lookup_entries:
        lookup_by_session.setdefault(e.session_id, []).append(e)

    # One batched `ps` call for every pid this run needs a liveness answer
    # for — registry, lock, and legacy bare-pid entries, plus our own pid for
    # the usability self-test — rather than a subprocess spawn per entry.
    # Only applies to the real _ps_lstart: an injected test double is a plain
    # function call, not I/O, so there is nothing to batch.
    resolved_ps_lstart = ps_lstart
    if ps_lstart is _ps_lstart:
        all_pids = {os.getpid(), *(e.pid for e in registry_entries), *(e.pid for e in lock_entries)}
        all_pids.update(
            pid for pid in (_coerce_pid(p.name) for p in legacy_bare_pid_paths) if pid is not None
        )
        resolved_ps_lstart = _ps_lstart_batch(sorted(all_pids)).get

    ps_usable = _ps_usable(ps_lstart=resolved_ps_lstart)

    # Source D's ids join known_session_ids too, so a session with only a
    # lookup entry still reads as "known" rather than re-surfacing through
    # the transcript-only fallback below with weaker (no-pid) evidence.
    known_session_ids = set(registry_by_session) | set(lock_by_session) | set(lookup_by_session)
    recent_only = _recent_transcript_only_ids(
        transcripts, known_session_ids, boot_time, window_seconds=near_boot_window_seconds, now=now,
    )
    all_session_ids = known_session_ids | set(recent_only)

    rows = [
        _classify_session(
            sid, registry_by_session.get(sid, []), lock_by_session.get(sid, []),
            transcripts.get(sid), boot_time=boot_time, ps_lstart=resolved_ps_lstart, ps_usable=ps_usable,
            proc_starttime_ticks=proc_starttime_ticks_fn,
            near_boot_window_seconds=near_boot_window_seconds,
            lookup_entries=tuple(lookup_by_session.get(sid, [])), now=now,
            session_end_records=session_end_records,
        )
        for sid in sorted(all_session_ids)
    ]

    # A dead-pid legacy file admitted as Source D evidence (in-window) must
    # never also appear in the deletion list -- deleting it would destroy the
    # very record just cited as evidence.
    lookup_paths_in_window = {e.path for e in lookup_entries}
    legacy_dead: list[Path] = []
    if ps_usable:
        for path in legacy_bare_pid_paths:
            if path in lookup_paths_in_window:
                continue
            pid = _coerce_pid(path.name)
            if pid is not None and resolved_ps_lstart(pid) is None:
                legacy_dead.append(path)

    versions_seen = {e.version for e in registry_entries if e.version}
    version_drift = sorted(versions_seen - _VALIDATED_REGISTRY_VERSIONS)
    pid_mismatches = [e.path for e in registry_entries if e.pid_mismatch]

    return Report(
        rows=rows, boot_time=boot_time, ps_usable=ps_usable,
        unparsed_registry=unparsed_registry, unparsed_lock=unparsed_lock,
        legacy_bare_pid_dead=legacy_dead,
        find_timed_out=find_timed_out, find_elapsed_seconds=find_elapsed,
        version_drift=version_drift, pid_mismatches=pid_mismatches,
        config_dirs=config_dirs, any_sessions_dir_found=any_sessions_dir_found,
        any_session_end_dir_found=any_session_end_dir_found,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _assign_ordinal(value: str, ordinal_map: dict[str, str], prefix: str) -> str:
    if value not in ordinal_map:
        ordinal_map[value] = f"{prefix}-{len(ordinal_map) + 1}"
    return ordinal_map[value]


def _account_ordinal_map(config_dirs: list[Path]) -> dict[str, str]:
    """Stable account-N label per resolved config dir, sorted by resolved
    path (not scan order) so the same physical account reads as the same
    account-N regardless of which profile produced the report — mirrors
    transcript-analysis.py's _redaction_ordinals convention."""
    ordinal_map: dict[str, str] = {}
    for cdir in sorted(config_dirs, key=lambda d: d.resolve()):
        _assign_ordinal(str(cdir.resolve()), ordinal_map, "account")
    return ordinal_map


def _config_dirs_scanned_note(*, config_dirs_explicit: bool, root_count: int) -> str:
    """Qualify the "Config directories scanned" line so its count alone never
    has to stand in for provenance: an explicit --config-dir override, an
    absent roots file, and a roots file that declares nothing usable all
    otherwise render identically at one root. Never names the roots file's
    resolved path, only its display label.

    Beyond one root the raw-paths/count already shows the roots file
    contributed, so no note is needed there; and an explicit override is
    noted regardless of root_count, since the roots file plays no part in
    that run either way.
    """
    if config_dirs_explicit:
        return f" (--config-dir passed explicitly; {TRANSCRIPT_CONFIG_DIRS_LABEL} not consulted)"
    if root_count != 1:
        return ""
    state = declared_roots_file_state()
    if state == "absent":
        return f" (no {TRANSCRIPT_CONFIG_DIRS_LABEL} declared)"
    if state == "unreadable":
        return f" ({TRANSCRIPT_CONFIG_DIRS_LABEL} present but unreadable)"
    return f" ({TRANSCRIPT_CONFIG_DIRS_LABEL} declared; contributed no additional directories)"


def render_report(report: Report, *, redact: bool, config_dirs_explicit: bool = False, now: float | None = None) -> str:
    lines: list[str] = ["# Post-crash session recovery report", ""]

    # Beyond a single dir, raw paths print only when the operator typed
    # --config-dir themselves -- the declared-roots-file default must never
    # disclose paths they didn't type this run.
    show_raw_config_dirs = not redact and (config_dirs_explicit or len(report.config_dirs) == 1)
    scanned_dirs_note = _config_dirs_scanned_note(
        config_dirs_explicit=config_dirs_explicit, root_count=len(report.config_dirs),
    )
    if show_raw_config_dirs:
        lines.append(f"Config directories scanned: {', '.join(str(d) for d in report.config_dirs)}{scanned_dirs_note}")
    else:
        lines.append(f"Config directories scanned: {len(report.config_dirs)}{scanned_dirs_note}")
    lines.append(
        "Freshness: a dead pid in the registry or a scheduled-task lock is crash evidence only until "
        "that pid is reused, and a lookup file's dead pid is evidence only within the crash-evidence "
        "window — run this before starting new Claude Code sessions after a reboot, since a fresh "
        "session can silently overwrite a crashed one's registry entry at the same pid."
    )
    lines.append(
        f"Last boot: {_fmt_ts(report.boot_time)}" if report.boot_time is not None
        else "Last boot: could not be determined on this platform — registry entries default to unknown."
    )
    if not report.any_sessions_dir_found:
        lines.append("NOTE: no sessions/ directory found in any scanned config dir — source A (the session registry) is empty.")
    if not report.any_session_end_dir_found:
        lines.append(
            "NOTE: no session-end-records/ directory found in any scanned config dir — source E (SessionEnd "
            "records) is empty; either record-session-end.sh isn't installed yet, or no session has cleanly "
            "exited since it was."
        )
    if not report.ps_usable:
        lines.append(
            "WARNING: `ps -o lstart=` returned no usable output on this system — no pid liveness could "
            "be confirmed, so every session below is classified unknown rather than risking a false crash report."
        )
    if report.find_timed_out:
        lines.append(
            f"WARNING: the scheduled_tasks.lock filesystem sweep timed out after {report.find_elapsed_seconds:.1f}s "
            "— lock evidence outside the transcript corpus's own working directories may be missing from this run."
        )
    else:
        lines.append(f"scheduled_tasks.lock filesystem sweep completed in {report.find_elapsed_seconds:.1f}s.")
    if report.version_drift:
        lines.append(
            f"NOTE: registry format not validated for CLI version(s): {', '.join(report.version_drift)} "
            "— fields are read defensively but may be incomplete."
        )
    if report.unparsed_registry or report.unparsed_lock:
        lines.append(
            f"NOTE: {report.unparsed_registry} registry entr{'y' if report.unparsed_registry == 1 else 'ies'} and "
            f"{report.unparsed_lock} lock file(s) could not be parsed and are excluded above."
        )
    if report.pid_mismatches:
        lines.append(
            f"NOTE: {len(report.pid_mismatches)} registry file(s) had a filename pid disagreeing with their own "
            "pid field; the field was preferred."
        )
    lines.append("")

    cwd_map: dict[str, str] = {}
    session_map: dict[str, str] = {}
    # Only computed above one config dir: at a single root every row is the
    # same, only-declared account, so tagging it would add noise, not signal.
    account_labels = _account_ordinal_map(report.config_dirs) if len(report.config_dirs) > 1 else {}

    def sid_of(value: str) -> str:
        return _assign_ordinal(value, session_map, "session") if redact else value

    def cwd_of(value: str | None) -> str | None:
        if value is None:
            return None
        return _assign_ordinal(value, cwd_map, "project") if redact else value

    def account_of(config_dir: Path | None) -> str | None:
        if config_dir is None:
            return None
        return account_labels.get(str(config_dir.resolve()))

    def render_resume_section(class_key: str, title: str) -> None:
        """Shared layout for Resumable and Possible-crash: both are rows with a
        real resume command, so they share the resume-command line, cwd-missing
        warning, meta line, and detail line."""
        rows = sorted(
            (r for r in report.rows if r.classification == class_key),
            key=lambda r: r.last_activity or 0.0, reverse=True,
        )
        lines.append(f"## {title} ({len(rows)})")
        lines.append("")
        if not rows:
            lines.append("None found.")
        for row in rows:
            cwd_display = cwd_of(row.cwd)
            sid_display = sid_of(row.session_id)
            # shlex.quote: this line is meant to be copy-pasted straight into a shell, and cwd/session_id
            # both trace back to locally-readable but not fully trusted strings (a git branch name, a
            # directory name) — quoting turns even a crafted value into an inert argument, not a second command.
            if cwd_display:
                lines.append(f"cd {shlex.quote(cwd_display)} && claude --resume {shlex.quote(sid_display)}")
                if row.cwd_missing:
                    lines.append("  WARNING: this directory no longer exists on disk — resuming will fail.")
            else:
                lines.append(
                    f"claude --resume {shlex.quote(sid_display)}  # cwd unknown — resuming from this directory may fail"
                )
            meta = []
            account_label = account_of(row.config_dir)
            if account_label:
                meta.append(account_label)
            meta.append(f"last activity {_fmt_ts(row.last_activity)}")
            if now is not None and row.last_activity is not None and row.last_activity <= now:
                meta.append(_fmt_age(now - row.last_activity))
            if not redact and row.git_branch:
                meta.append(f"branch {row.git_branch}")
            meta.append(f"{row.entry_count} underlying entr{'y' if row.entry_count == 1 else 'ies'}")
            lines.append("  " + ", ".join(meta))
            lines.append(f"  {row.detail}")
        lines.append("")

    render_resume_section(CLASS_RESUMABLE, "Resumable")
    render_resume_section(CLASS_POSSIBLE_CRASH, "Possible crash — process gone, clean exit not ruled out")

    other_groups = (
        (CLASS_CRASHED_NO_TRANSCRIPT, "Crashed, no transcript"),
        (CLASS_CONFIRMED_CLEAN_EXIT, "Confirmed clean exit (SessionEnd recorded)"),
        (CLASS_LIVE_PROCESS, "Still running (a live process matches a tracked pid)"),
        (CLASS_UNKNOWN, "Unknown"),
    )
    for class_key, label in other_groups:
        group_rows = sorted(
            (r for r in report.rows if r.classification == class_key),
            key=lambda r: r.last_activity or 0.0, reverse=True,
        )
        lines.append(f"## {label} ({len(group_rows)})")
        lines.append("")
        if not group_rows:
            lines.append("None found.")
        for row in group_rows:
            sid_display = sid_of(row.session_id)
            cwd_display = cwd_of(row.cwd)
            location = f" ({cwd_display})" if cwd_display else ""
            account_label = account_of(row.config_dir)
            account_tag = f" [{account_label}]" if account_label else ""
            lines.append(f"session {sid_display}{location}{account_tag}: {row.detail}")
        lines.append("")

    if report.legacy_bare_pid_dead:
        total_legacy_dead = len(report.legacy_bare_pid_dead)
        # Oldest first: the oldest files are the safest cleanup candidates and
        # this gives a stable, incrementally-progressing queue across re-runs.
        sorted_legacy_dead = sorted(report.legacy_bare_pid_dead, key=lambda p: _safe_mtime(p) or 0.0)
        shown_legacy_dead = sorted_legacy_dead[:_LEGACY_DEAD_LIST_CAP]
        lines.append(f"## Legacy bare-pid lookup files with a dead pid ({total_legacy_dead})")
        lines.append("")
        lines.append(
            "These are capture-session-id.sh's session_id<->pid lookup files, not the session registry "
            "above — active infrastructure the require-* hook gates depend on. Only dead-pid files are "
            "listed here; do not delete a live one."
        )
        for path in shown_legacy_dead:
            lines.append(f"  {path.name if not show_raw_config_dirs else path}")
        if total_legacy_dead > _LEGACY_DEAD_LIST_CAP:
            lines.append(
                f"  (showing {_LEGACY_DEAD_LIST_CAP} of {total_legacy_dead} oldest — re-run after deleting "
                "these to see the rest.)"
            )
        if show_raw_config_dirs:
            lines.append("  rm -- " + " ".join(shlex.quote(str(p)) for p in shown_legacy_dead))
        elif not redact:
            lines.append(
                "  (rm command omitted: these paths span declared accounts not passed via an explicit"
                " --config-dir — re-run with --config-dir <dir> to get a runnable command for one account,"
                " or --redact to suppress this note.)"
            )
        lines.append("")

    lines.append(
        "Richer resume context may exist in ~/.claude/handoffs/ or ~/.claude/briefs/ for any of these "
        "directories — check there before reconstructing context from scratch."
    )
    if redact:
        lines.append(
            "Redacted for sharing, but still not guaranteed publish-safe — verify before pasting into a "
            "public issue or channel."
        )
    else:
        lines.append(
            "This report is NOT publish-safe: it contains real paths, session ids, and branch names. "
            "Re-run with --redact before pasting it anywhere public."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _declared_config_dirs() -> list[Path]:
    """Declared roots from ~/.claude/transcript-config-dirs (or
    TRANSCRIPT_CONFIG_DIRS_FILE), validated against this script's own looser
    sessions/-or-projects/ check (main()'s --config-dir validation, below)
    instead of declared_transcript_roots()'s projects/-only requirement --
    the latter would silently drop a sessions-only root, the crashed-fresh-
    account case this tool exists for.
    """
    return declared_roots_matching(
        lambda candidate: (candidate / "sessions").is_dir() or (candidate / "projects").is_dir(),
        warn_prefix="post-crash-sessions",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Enumerate Claude Code sessions orphaned by an unclean shutdown and print a resume "
            "command for each one that is recoverable. Read-only: writes nothing, ever."
        ),
    )
    parser.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved "
            "config dir is always scanned first. Absent this flag, every root declared in "
            "~/.claude/transcript-config-dirs is scanned too; passing this flag explicitly "
            "overrides that default and scans only the directories named here. Each supplied "
            "directory must contain a sessions/ or projects/ subdirectory, or it is rejected."
        ),
    )
    parser.add_argument(
        "--redact", action="store_true",
        help=(
            "Map project directories and session ids to ordinals (project-1, session-1, ...) and "
            "drop git branch names, for pasting into a public issue or channel."
        ),
    )
    parser.add_argument(
        "--crash-window-hours", "--near-boot-hours", type=float, metavar="HOURS", default=None,
        dest="crash_window_hours",
        help=(
            f"How far back this tool's crash evidence reaches: how long before the last boot a "
            f"boot-anchored transcript's last activity can sit, how recently a now-anchored "
            f"transcript or a never-swept sessions/<pid> lookup file can have last run, and still "
            f"surface under 'Possible crash' (default {_CRASH_EVIDENCE_WINDOW_SECONDS / 3600:g}h). "
            "Widen this to recover sessions from an older crash, e.g. --crash-window-hours 72 for "
            "three days. --near-boot-hours is accepted as an alias."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        default_config_dir = config_dir()
    except ValueError as exc:
        print(f"post-crash-sessions: {exc}", file=sys.stderr)
        return 2

    config_dirs = [default_config_dir]
    seen_resolved = {config_dirs[0].resolve()}
    if args.extra_config_dirs:
        for raw in args.extra_config_dirs:
            candidate = Path(raw)
            if not candidate.is_dir():
                print(f"post-crash-sessions: --config-dir {raw!r} is not a directory", file=sys.stderr)
                return 2
            if not ((candidate / "sessions").is_dir() or (candidate / "projects").is_dir()):
                print(
                    f"post-crash-sessions: --config-dir {raw!r} rejected: no sessions/ or projects/ subdirectory found",
                    file=sys.stderr,
                )
                return 2
            resolved = candidate.resolve()
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            config_dirs.append(candidate)
    else:
        # No explicit --config-dir: default to every declared root too, mirroring
        # transcript-analysis.py's _resolve_scan_roots precedence -- an explicit
        # --config-dir overrides the declared-roots default entirely.
        for declared_dir in _declared_config_dirs():
            resolved = declared_dir.resolve()
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            config_dirs.append(declared_dir)

    find_root = Path(os.environ.get(_FIND_ROOT_ENV_VAR, str(Path.home())))

    near_boot_window_seconds = _CRASH_EVIDENCE_WINDOW_SECONDS
    if args.crash_window_hours is not None:
        # math.isfinite rejects nan/inf: a bare `<= 0` check lets `nan` through (NaN
        # comparisons are always False), silently disabling near-boot detection.
        if not math.isfinite(args.crash_window_hours) or args.crash_window_hours <= 0:
            print(
                f"post-crash-sessions: --crash-window-hours (--near-boot-hours) must be positive, "
                f"got {args.crash_window_hours!r}",
                file=sys.stderr,
            )
            return 2
        near_boot_window_seconds = args.crash_window_hours * 3600

    # Captured once so build_report's now-anchored admission window and
    # render_report's age annotation never disagree about "now".
    now = time.time()
    report = build_report(
        config_dirs=config_dirs, find_root=find_root, near_boot_window_seconds=near_boot_window_seconds, now=now,
    )
    print(render_report(
        report, redact=args.redact, config_dirs_explicit=bool(args.extra_config_dirs), now=now,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
