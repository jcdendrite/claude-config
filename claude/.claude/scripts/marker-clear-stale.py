#!/usr/bin/env python3
"""Sweeps orphaned session markers under $CONFIG_DIR/.*-active.d/, called by
marker.sh's `clear-stale` arm. One process for the whole sweep, not one per
entry: a per-file bash loop would pay a fresh python3 spawn (O_NOFOLLOW read)
per file. O_NOFOLLOW reads stay -- a symlink planted at one of these
predictable <active-dir>/<session-id>[.suffix] paths must never be followed,
the same hardening _lib.sh's _lib_write_no_follow and marker.sh's own
_read_marker_no_follow apply per-file elsewhere.

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
import sys
import time

# review-pr's own artifacts, never a bare PID: reaped once the PID recorded
# inside the sibling .provenance file (same directory, same session id) is
# confirmed dead, never on the entry's own mtime -- cwd activity between
# writing an artifact and posting it can idle past the 60-minute window
# below while a review is still in flight. All four now key on the same PID
# field rather than a per-suffix rule.
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
    dead. PermissionError (EPERM) means the process exists but is owned by
    another user, so it reports alive rather than being misread as an
    eviction candidate."""
    if not re.match(r"^[0-9]+$", pid_text or ""):
        return False
    try:
        os.kill(int(pid_text), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def sweep(config_dir: str, dry_run: bool) -> tuple[int, int, list[str]]:
    """Returns (evicted, kept, dry-run-only display lines)."""
    evicted = 0
    kept = 0
    lines: list[str] = []

    for active_dir in sorted(glob.glob(os.path.join(config_dir, ".*-active.d"))):
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
                # _lib_review_pr_provenance_field does: a provenance file
                # whose first line isn't exactly the literal "schema=1" is
                # an unrecognized (e.g. pre-migration positional) format, so
                # liveness can't be determined from it -- keep rather than
                # evict, since defaulting to eviction here would delete a
                # live session's artifacts under a format this reader
                # doesn't understand. An empty file is the load-bearing case:
                # the provenance writer truncates before it writes, so a
                # concurrent sweep can observe an empty file for a live
                # review. No age bound applies, so a writer that crashed
                # mid-write pins its sibling artifacts until they are
                # removed by hand.
                unrecognized_provenance_format = False
                if provenance_content is not None:
                    provenance_lines = provenance_content.decode("utf-8", "replace").splitlines()
                    if provenance_lines[:1] != ["schema=1"]:
                        unrecognized_provenance_format = True
                    else:
                        # Keyed on "pid=", not a positional line index: the
                        # provenance file is _lib_write_review_pr_provenance's
                        # key=value schema (a `schema=1` header line, then one
                        # KEY=VALUE line per field), additive by design -- a
                        # later phase can add a field without shifting this
                        # read's line position.
                        for line in provenance_lines[1:]:
                            if line.startswith("pid=") and line[len("pid="):].strip():
                                owner_pid = line[len("pid="):].strip()
                                break
                owner_alive = owner_pid is not None and pid_alive(owner_pid)
                if unrecognized_provenance_format:
                    kept += 1
                    if dry_run:
                        lines.append(f"  keep: {dir_name}/{entry_name} (unrecognized provenance format, keeping conservatively)")
                elif owner_alive:
                    kept += 1
                    if dry_run:
                        lines.append(f"  keep: {dir_name}/{entry_name} (owning PID {owner_pid} alive)")
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
