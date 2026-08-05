#!/usr/bin/env python3
"""post-crash-sessions.py — enumerate Claude Code sessions orphaned by an
unclean shutdown and print a resume command for each one that is recoverable.

Read-only, always: writes no file, creates no directory, emits only to
stdout/stderr. Three evidence sources are cross-referenced per session id:

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

Freshness caveat: after a reboot, process ids restart low, so a freshly
launched session can overwrite a crashed session's registry entry at the same
pid — run this before starting new Claude Code sessions post-reboot. Every
row's classification is anchored against boot time specifically so it stays
correct regardless of whether Claude Code itself sweeps stale entries at
startup (unverified either way; the boot-time anchor makes it moot).

Both the registry and the lock file are undocumented first-party formats,
observed on one machine at one CLI version — every field beyond the required
core (sessionId + pid) is read with .get() and a default, matching this
repo's existing posture for undocumented Claude Code state (see
statusline-command.sh's account-info block).

Env overrides:
  POST_CRASH_SESSIONS_FIND_ROOT   root for the scheduled_tasks.lock filesystem
                                   sweep instead of $HOME. Tests only — never
                                   sweeps the real $HOME otherwise.
"""
import argparse
import json
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

from _config_dir import config_dir

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

# Empirically measured at ~18.9s for a full $HOME sweep on one Darwin
# machine; this bounds a hang on a slower disk or a large home tree, it does
# not guarantee the sweep completes within it on every machine.
_FIND_SWEEP_TIMEOUT_SECONDS = 25.0

# sysctl/ps are short-lived local commands querying local process state; this
# is a hang-detection backstop, not a measured value.
_SUBPROCESS_TIMEOUT_SECONDS = 5.0

# Heuristic, not a vendor-specified value: covers the write latency of a
# session's last turn landing on disk shortly before a crash. Only used to
# decide whether a transcript with no registry or lock entry at all is worth
# surfacing as corroborating-only evidence.
_NEAR_BOOT_TRANSCRIPT_WINDOW_SECONDS = 600.0

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
CLASS_CLEAN_EXIT = "clean-exit"
CLASS_UNKNOWN = "unknown"


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


@dataclass
class LockEntry:
    session_id: str
    pid: int
    proc_start: str | None
    acquired_at: float | None
    mtime: float | None
    path: Path


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


def _parse_lstart(raw: str | None, tz: timezone) -> datetime | None:
    """Pure parse of one ps -o lstart=-format string. tz is a parameter, never
    read from the process clock, so this never depends on the ambient
    environment — only on what the caller passes in."""
    if not raw:
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


def _entry_liveness(pid: int, proc_start: str | None, *, ps_lstart, ps_usable: bool) -> str:
    """Return 'live', 'dead', or 'indeterminate' for one registry/lock entry."""
    if not ps_usable:
        return "indeterminate"
    live_lstart = ps_lstart(pid)
    if live_lstart is None:
        return "dead"
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
                pid_mismatch=pid_mismatch,
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
                has_main=True, subagent_count=0, path=jsonl,
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
                    has_main=False, subagent_count=0, path=sub_jsonl,
                )
                transcripts[parent_session_id] = info
            info.subagent_count += 1
            info.last_activity = _max_optional_float(info.last_activity, sub_mtime)

    return transcripts, cwds


def _near_boot_transcript_only_ids(
    transcripts: dict[str, TranscriptInfo],
    known_session_ids: set[str],
    boot_time: float | None,
    *,
    window_seconds: float = _NEAR_BOOT_TRANSCRIPT_WINDOW_SECONDS,
) -> list[str]:
    """Session ids with a transcript but no registry or lock entry at all,
    whose last activity sits just before boot — corroborating-only evidence
    that a crash may have happened without leaving any other trace."""
    if boot_time is None:
        return []
    return [
        sid for sid, info in transcripts.items()
        if sid not in known_session_ids
        and info.has_main
        and boot_time - window_seconds <= (info.last_activity or 0.0) <= boot_time
    ]


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
) -> SessionRow:
    entry_count = len(registry_entries) + len(lock_entries)
    has_main_transcript = transcript is not None and transcript.has_main
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
            entry_count, _cwd_missing(cwd),
        )

    liveness: dict[tuple[str, int], str] = {}
    for e in registry_entries:
        liveness[("registry", e.pid)] = _entry_liveness(e.pid, e.proc_start, ps_lstart=ps_lstart, ps_usable=ps_usable)
    for e in lock_entries:
        liveness[("lock", e.pid)] = _entry_liveness(e.pid, e.proc_start, ps_lstart=ps_lstart, ps_usable=ps_usable)

    if "live" in liveness.values():
        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_CLEAN_EXIT, cwd, branch, last_activity,
            "a live process matches a tracked pid; not crash evidence.",
            entry_count, False,
        )

    if registry_entries:
        if boot_time is None:
            cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                "boot time could not be determined on this platform, so registry entries cannot be dated against it.",
                entry_count, _cwd_missing(cwd),
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
                    entry_count, _cwd_missing(cwd),
                )
            return SessionRow(
                session_id, CLASS_CRASHED_NO_TRANSCRIPT, cwd, branch, last_activity,
                f"{boot_note} No main transcript was found for this session.{subagent_note}",
                entry_count, _cwd_missing(cwd),
            )

        if dead_after_boot:
            newest = max(dead_after_boot, key=lambda e: e.mtime)
            cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                f"registry entry written {_fmt_ts(newest.mtime)}, after boot ({_fmt_ts(boot_time)}) — "
                "not evidence of surviving a crash.",
                entry_count, _cwd_missing(cwd),
            )

        if mtime_unknown:
            cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
            return SessionRow(
                session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
                "registry entry's file modification time could not be read, so this session cannot be dated "
                "against boot time.",
                entry_count, _cwd_missing(cwd),
            )

        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
            "registry procStart could not be parsed; this session's pid liveness could not be confirmed.",
            entry_count, _cwd_missing(cwd),
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
                    entry_count, _cwd_missing(cwd),
                )
            return SessionRow(
                session_id, CLASS_CRASHED_NO_TRANSCRIPT, cwd, branch, last_activity,
                f"scheduled-task lock's pid is dead; no transcript was found for this session.{subagent_note}",
                entry_count, _cwd_missing(cwd),
            )
        cwd, branch, last_activity = _best_effort_location(registry_entries, lock_entries, transcript)
        return SessionRow(
            session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
            "scheduled-task lock's procStart could not be parsed; liveness could not be confirmed.",
            entry_count, _cwd_missing(cwd),
        )

    # No registry, no lock entry — reached only for a near-boot transcript-only session.
    cwd = transcript.cwd if transcript is not None else None
    branch = transcript.git_branch if transcript is not None else None
    last_activity = transcript.last_activity if transcript is not None else None
    return SessionRow(
        session_id, CLASS_UNKNOWN, cwd, branch, last_activity,
        "only a transcript exists, with no registry or lock entry; its last activity sits near the last "
        "boot, but this alone does not prove a crash.",
        entry_count, _cwd_missing(cwd),
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

    registry_by_session: dict[str, list[RegistryEntry]] = {}
    for e in registry_entries:
        registry_by_session.setdefault(e.session_id, []).append(e)
    lock_by_session: dict[str, list[LockEntry]] = {}
    for e in lock_entries:
        lock_by_session.setdefault(e.session_id, []).append(e)

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

    known_session_ids = set(registry_by_session) | set(lock_by_session)
    near_boot_only = _near_boot_transcript_only_ids(transcripts, known_session_ids, boot_time)
    all_session_ids = known_session_ids | set(near_boot_only)

    rows = [
        _classify_session(
            sid, registry_by_session.get(sid, []), lock_by_session.get(sid, []),
            transcripts.get(sid), boot_time=boot_time, ps_lstart=resolved_ps_lstart, ps_usable=ps_usable,
        )
        for sid in sorted(all_session_ids)
    ]

    legacy_dead: list[Path] = []
    if ps_usable:
        for path in legacy_bare_pid_paths:
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
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _assign_ordinal(value: str, ordinal_map: dict[str, str], prefix: str) -> str:
    if value not in ordinal_map:
        ordinal_map[value] = f"{prefix}-{len(ordinal_map) + 1}"
    return ordinal_map[value]


def render_report(report: Report, *, redact: bool) -> str:
    lines: list[str] = ["# Post-crash session recovery report", ""]

    if redact:
        lines.append(f"Config directories scanned: {len(report.config_dirs)}")
    else:
        lines.append(f"Config directories scanned: {', '.join(str(d) for d in report.config_dirs)}")
    lines.append(
        "Freshness: a dead pid in the registry is only crash evidence until that pid is reused — "
        "run this before starting new Claude Code sessions after a reboot, since a fresh session can "
        "silently overwrite a crashed one's entry at the same pid."
    )
    lines.append(
        f"Last boot: {_fmt_ts(report.boot_time)}" if report.boot_time is not None
        else "Last boot: could not be determined on this platform — registry entries default to unknown."
    )
    if not report.any_sessions_dir_found:
        lines.append("NOTE: no sessions/ directory found in any scanned config dir — source A (the session registry) is empty.")
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

    def sid_of(value: str) -> str:
        return _assign_ordinal(value, session_map, "session") if redact else value

    def cwd_of(value: str | None) -> str | None:
        if value is None:
            return None
        return _assign_ordinal(value, cwd_map, "project") if redact else value

    resumable = sorted(
        (r for r in report.rows if r.classification == CLASS_RESUMABLE),
        key=lambda r: r.last_activity or 0.0, reverse=True,
    )
    lines.append(f"## Resumable ({len(resumable)})")
    lines.append("")
    if not resumable:
        lines.append("None found.")
    for row in resumable:
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
        meta = [f"last activity {_fmt_ts(row.last_activity)}"]
        if not redact and row.git_branch:
            meta.append(f"branch {row.git_branch}")
        meta.append(f"{row.entry_count} underlying entr{'y' if row.entry_count == 1 else 'ies'}")
        lines.append("  " + ", ".join(meta))
        lines.append(f"  {row.detail}")
    lines.append("")

    other_groups = (
        (CLASS_CRASHED_NO_TRANSCRIPT, "Crashed, no transcript"),
        (CLASS_CLEAN_EXIT, "Not crash evidence (clean exit or still running)"),
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
            lines.append(f"session {sid_display}{location}: {row.detail}")
        lines.append("")

    if report.legacy_bare_pid_dead:
        lines.append(f"## Legacy bare-pid lookup files with a dead pid ({len(report.legacy_bare_pid_dead)})")
        lines.append("")
        lines.append(
            "These are capture-session-id.sh's session_id<->pid lookup files, not the session registry "
            "above — active infrastructure the require-* hook gates depend on. Only dead-pid files are "
            "listed here; do not delete a live one."
        )
        for path in report.legacy_bare_pid_dead:
            lines.append(f"  {path.name if redact else path}")
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
            "config dir is always scanned first. Each supplied directory must contain a sessions/ "
            "or projects/ subdirectory, or it is rejected."
        ),
    )
    parser.add_argument(
        "--redact", action="store_true",
        help=(
            "Map project directories and session ids to ordinals (project-1, session-1, ...) and "
            "drop git branch names, for pasting into a public issue or channel."
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
    for raw in args.extra_config_dirs or []:
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

    find_root = Path(os.environ.get(_FIND_ROOT_ENV_VAR, str(Path.home())))

    report = build_report(config_dirs=config_dirs, find_root=find_root)
    print(render_report(report, redact=args.redact))
    return 0


if __name__ == "__main__":
    sys.exit(main())
