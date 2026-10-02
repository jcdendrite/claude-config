#!/usr/bin/env python3
"""Sweeps orphaned session markers under $CONFIG_DIR/.*-active.d/, called by
marker.sh's `clear-stale` arm. One process for the whole sweep, not one per
entry: a per-file bash loop would pay a fresh python3 spawn per O_NOFOLLOW
read. Reads use O_NOFOLLOW so a symlink planted at one of these predictable
<active-dir>/<session-id>[.suffix] paths is never followed, the same hardening
_lib.sh's _lib_write_no_follow and _lib_cat_no_follow apply per-file elsewhere.

Usage: marker-clear-stale.py CONFIG_DIR DRY_RUN

DRY_RUN is "1" or "0". Prints one line per evicted entry in both modes, and
one line per kept entry in dry-run mode only, followed by a summary line,
and exits 0 unconditionally -- a stale marker left behind on an unexpected
error is a slow leak, not a correctness failure, so this never aborts the
caller.
"""
from __future__ import annotations

import contextlib
import glob
import os
import re
import subprocess
import sys
import time

# review-pr's own artifacts, never a bare PID. Each is reaped only when both hold:
# - The PID recorded inside the sibling .provenance file (same directory, same
#   session id) is dead.
# - No sessions/<pid> file with a live, start-time-matching PID names the owning
#   session id, because a resumed session runs under a new PID while its
#   provenance keeps the old one.
# The entry's own mtime never decides: cwd activity between writing an artifact
# and posting it can idle past the 60-minute window below while a review is
# still in flight. All four suffixes use the same two checks rather than a
# per-suffix rule.
REVIEW_PR_SUFFIXES = (".body", ".provenance", ".diff", ".context.json")


def read_no_follow(path: str) -> bytes | None:
    """Read path's full content, or None if it's absent, unreadable, or a
    symlink (O_NOFOLLOW refuses to open through one)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read()


def pid_alive(pid_text: str | None) -> bool:
    """True only for a well-formed positive-integer PID whose process
    currently exists. ProcessLookupError (ESRCH) means the PID is genuinely
    dead, and so is a PID too large for the C pid type (OverflowError) or past
    Python's int-conversion digit limit (ValueError), which no process can
    hold. PermissionError (EPERM) means the process exists but
    is owned by another user, so it reports alive rather than being misread
    as an eviction candidate."""
    if not re.match(r"^[1-9][0-9]*$", pid_text or ""):
        return False
    try:
        os.kill(int(pid_text), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return False
    return True


def start_time_matches(pid_text: str, recorded_start: str) -> bool:
    """True when pid's current `ps -o lstart=` equals recorded_start, the same
    exact comparison, pinned TZ/locale, and 5-second cap as _lib.sh's
    _lib_resolve_claude_pid. A mismatch means the PID was reused since
    capture-session-id.sh wrote the entry. A ps that cannot run or times out
    reads as a match, so an unverifiable entry keeps its artifacts."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", pid_text],
            env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.stdout.rstrip("\n") == recorded_start


def live_session_ids(sessions_dir: str) -> set[str]:
    """Session ids named by capture-session-id.sh's sessions/<pid> files whose
    <pid> is alive and whose recorded start time still matches that process.
    Line 1 of each file is the session id, line 2 the process start time."""
    session_ids: set[str] = set()
    try:
        file_names = os.listdir(sessions_dir)
    except OSError:
        return session_ids
    for file_name in file_names:
        if not pid_alive(file_name):
            continue
        content = read_no_follow(os.path.join(sessions_dir, file_name))
        if content is None:
            continue
        session_id, _, recorded_start = content.decode("utf-8", "replace").partition("\n")
        recorded_start = recorded_start.partition("\n")[0]
        if session_id.strip() and recorded_start and start_time_matches(file_name, recorded_start):
            session_ids.add(session_id.strip())
    return session_ids


def sweep(config_dir: str, dry_run: bool) -> tuple[int, int, list[str]]:
    """Returns (evicted, kept, dry-run-only display lines)."""
    evicted = 0
    kept = 0
    lines: list[str] = []
    live_sessions = live_session_ids(os.path.join(config_dir, "sessions"))

    for active_dir in sorted(glob.glob(os.path.join(glob.escape(config_dir), ".*-active.d"))):
        if not os.path.isdir(active_dir):
            continue
        dir_name = os.path.basename(active_dir)
        for entry_name in sorted(os.listdir(active_dir)):
            # A bare shell glob (e.g. "$active_dir"/*) never expands to a
            # dotfile; os.listdir carries no such exclusion, so a stray
            # dotfile (e.g. .DS_Store) needs an explicit skip here.
            if entry_name.startswith("."):
                continue
            entry = os.path.join(active_dir, entry_name)
            if not os.path.isfile(entry):
                continue
            # .planmode-path holds a declared plan-mode file path, never a
            # PID, so the numeric-PID test below would always misread it as
            # a dead marker. Permanently exempt, unlike the review-pr
            # suffixes below.
            if entry_name.endswith(".planmode-path"):
                continue

            review_pr_suffix = (
                next((s for s in REVIEW_PR_SUFFIXES if entry_name.endswith(s)), None)
                if dir_name == ".review-pr-active.d"
                else None
            )
            if review_pr_suffix is not None:
                owner_session_id = entry_name[: -len(review_pr_suffix)]
                provenance_content = read_no_follow(os.path.join(active_dir, owner_session_id + ".provenance"))
                owner_pid = None
                # Fails closed the same way _lib.sh's
                # _lib_review_pr_provenance_field does:
                # - A provenance file whose first line isn't exactly the
                #   literal "schema=1" is an unrecognized format, so liveness
                #   can't be determined from it.
                # - An unrecognized format is kept, not evicted, because
                #   eviction would delete a live session's artifacts.
                # - An empty file is the load-bearing case: the provenance
                #   writer truncates before it writes, so a concurrent sweep
                #   can observe an empty file for a live review.
                # - No age bound applies, so a writer that crashed mid-write
                #   pins its sibling artifacts until they are removed by hand.
                unrecognized_provenance_format = False
                if provenance_content is not None:
                    provenance_lines = provenance_content.decode("utf-8", "replace").splitlines()
                    if provenance_lines[:1] != ["schema=1"]:
                        unrecognized_provenance_format = True
                    else:
                        # Keyed on "pid=", not a positional line index: the
                        # provenance file is _lib_write_review_pr_provenance's
                        # key=value schema (a `schema=1` header line, then one
                        # KEY=VALUE line per field), so an added field cannot
                        # shift this read.
                        for line in provenance_lines[1:]:
                            if line.startswith("pid=") and line[len("pid="):].strip():
                                owner_pid = line[len("pid="):].strip()
                                break
                owner_alive = owner_pid is not None and pid_alive(owner_pid)
                if unrecognized_provenance_format:
                    kept += 1
                    if dry_run:
                        lines.append(f"  keep: {dir_name}/{entry_name} (unrecognized provenance format, keeping conservatively)")
                elif owner_alive or owner_session_id in live_sessions:
                    kept += 1
                    if dry_run:
                        keep_reason = (
                            f"owning PID {owner_pid} alive" if owner_alive else f"owning session {owner_session_id} alive"
                        )
                        lines.append(f"  keep: {dir_name}/{entry_name} ({keep_reason})")
                else:
                    evicted += 1
                    display_pid = owner_pid or "empty"
                    if dry_run:
                        lines.append(f"  evict (dry-run): {dir_name}/{entry_name} (owning PID {display_pid} dead)")
                    else:
                        with contextlib.suppress(OSError):
                            os.remove(entry)
                        lines.append(f"  evict: {dir_name}/{entry_name} (owning PID {display_pid} dead)")
                continue

            # Same two-part staleness definition _lib_active_bypass_marker_live
            # uses: PID alive AND mtime within the 60-minute idle window.
            stored_content = read_no_follow(entry)
            stored_pid = stored_content.decode("utf-8", "replace").strip() if stored_content is not None else ""
            alive = pid_alive(stored_pid)
            fresh = False
            if alive:
                try:
                    fresh = (time.time() - os.stat(entry).st_mtime) < 3600
                except OSError:
                    fresh = False
            if alive and fresh:
                kept += 1
                if dry_run:
                    lines.append(f"  keep: {dir_name}/{entry_name} (PID {stored_pid} alive)")
            else:
                evicted += 1
                reason = f"idle timeout, PID {stored_pid} alive" if alive else f"PID {stored_pid or 'empty'} dead"
                if dry_run:
                    lines.append(f"  evict (dry-run): {dir_name}/{entry_name} ({reason})")
                else:
                    with contextlib.suppress(OSError):
                        os.remove(entry)
                    lines.append(f"  evict: {dir_name}/{entry_name} ({reason})")

    return evicted, kept, lines


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("marker-clear-stale.py: usage: marker-clear-stale.py CONFIG_DIR DRY_RUN", file=sys.stderr)
        return 2
    _, config_dir, dry_run_arg = argv
    dry_run = dry_run_arg == "1"

    evicted, kept, lines = sweep(config_dir, dry_run)

    for line in lines:
        print(line)
    verb = "would evict" if dry_run else "evicted"
    kept_verb = "keep" if dry_run else "kept"
    print(f"clear-stale: {verb} {evicted} orphan(s), {kept_verb} {kept} active")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
