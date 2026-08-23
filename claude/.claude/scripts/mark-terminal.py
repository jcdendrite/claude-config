#!/usr/bin/env python3
"""mark-terminal.py — resolve a PID's controlling terminal and title it, so a
blocked or stuck Claude Code session (a stuck lock, a hung command) can be
spotted among many open terminal windows by PID alone.

Resolves the PID's TTY via `ps -o tty= -p <pid>` and writes an OSC 0
title-set escape sequence directly to the resolved /dev/ttysNNN device. The
title is auto-derived from that PID's entry in the Claude Code session
registry (<config-dir>/sessions/<pid>.json) when one exists and is live
(not a stale entry describing a since-recycled pid); an explicit --title
always wins. --list enumerates every currently-live registry entry with its
PID/TTY/cwd, for the "many windows open, which one is it" case.

macOS/BSD only: the no-tty sentinel ("??") and /dev/ttysNNN device naming
this relies on are Darwin-shaped. This repo's stow package also installs on
Linux and WSL2 (README.md), where both are shaped differently and
unsupported here — this script exits loudly on any other platform rather
than misbehaving silently.
"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import unicodedata
from datetime import UTC, datetime, timezone
from pathlib import Path

from _config_dir import config_dir, declared_roots_matching

DEFAULT_EMOJI = "📍"

# ps -o lstart= has whole-second resolution and the registry's own procStart
# capture can round independently, so exact equality is too strict. Mirrors
# post-crash-sessions.py's own tolerance for the same comparison.
_PROC_START_TOLERANCE_SECONDS = 2.0

# ps is a short-lived local command querying local process state; this is a
# hang-detection backstop, not a measured worst case. Wider than
# post-crash-sessions.py's own 5.0s value for the same call: this module's
# test suite spawns ps via a real PATH-injected subprocess (not the pure
# dependency injection post-crash-sessions.py's tests use), so it needs
# enough margin to tolerate fork/exec scheduling delay under heavy host
# contention, not just genuine hangs.
_SUBPROCESS_TIMEOUT_SECONDS = 10.0

_LSTART_FORMAT = "%a %b %d %H:%M:%S %Y"


def _positive_pid(raw: str) -> int:
    if not raw.isdigit() or int(raw) <= 0:
        raise argparse.ArgumentTypeError(f"pid must be a positive integer, got {raw!r}")
    return int(raw)


def resolve_config_dirs(extra_config_dirs: list[str] | None, *, tool_name: str) -> list[Path]:
    """Resolve the ordered list of config dirs to scan, mirroring
    post-crash-sessions.py's --config-dir precedence: an explicit
    --config-dir (repeatable) overrides scanning every declared root
    entirely; absent that flag, the active config dir is scanned first,
    followed by every root declared in ~/.claude/transcript-config-dirs,
    deduped by resolved path. Raises ValueError for an invalid --config-dir
    or an invalid CLAUDE_CONFIG_DIR (from config_dir() itself).
    """
    default_dir = config_dir()
    config_dirs = [default_dir]
    seen_resolved = {default_dir.resolve()}
    if extra_config_dirs:
        for raw in extra_config_dirs:
            candidate = Path(raw)
            if not candidate.is_dir():
                raise ValueError(f"--config-dir {raw!r} is not a directory")
            if not ((candidate / "sessions").is_dir() or (candidate / "projects").is_dir()):
                raise ValueError(
                    f"--config-dir {raw!r} rejected: no sessions/ or projects/ subdirectory found"
                )
            resolved = candidate.resolve()
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            config_dirs.append(candidate)
    else:
        for declared_dir in declared_roots_matching(
            lambda candidate: (candidate / "sessions").is_dir() or (candidate / "projects").is_dir(),
            warn_prefix=tool_name,
        ):
            resolved = declared_dir.resolve()
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            config_dirs.append(declared_dir)
    return config_dirs


_TTY_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")


def resolve_tty(pid: int, *, run=subprocess.run) -> str:
    """Return the bare tty name (e.g. "ttys015") for a live pid's
    controlling terminal. Raises ValueError for a nonexistent pid (ps exits
    nonzero), a pid with no controlling terminal (stripped output "??"), or
    a tty name containing anything other than letters/digits — ps is
    resolved by bare name via PATH, and an unvalidated value would otherwise
    be joined onto /dev and opened for write, letting a `/` or `..` in a
    compromised ps's output escape /dev entirely.

    BSD ps space-pads the tty= column to fixed width (e.g. "??      ") —
    .strip() before any comparison or path construction, or the no-tty
    sentinel and device path both silently fail to match.
    """
    try:
        result = run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"ps not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"ps timed out after {_SUBPROCESS_TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        raise ValueError(f"no such process: {pid}")
    tty = result.stdout.strip()
    if not tty or tty == "??":
        raise ValueError(f"pid {pid} has no controlling terminal")
    if not _TTY_NAME_RE.fullmatch(tty):
        raise ValueError(f"unexpected tty name for pid {pid}: {tty!r}")
    return tty


def _sanitize_for_terminal(value) -> str | None:
    """Strip control/escape bytes before a value can ever reach rendered
    output (an OSC title write or --list's printed table): C0 controls, DEL,
    the C1 range (U+0080-U+009F, the 8-bit encoding of the same escape
    introducers — CSI, OSC, ST — that C0/ESC-stripping targets), and every
    Unicode Format-category (Cf) character — bidi overrides (U+202E) and
    zero-width characters (U+200B) can otherwise still reach rendered output
    and produce a visually misleading title. This control's charter is
    escape/control-injection prevention, not full invisible-character
    moderation: a zero-width-but-non-Cf codepoint (e.g. a variation
    selector) passes through unstripped, since it carries no
    escape-sequence risk. Duplicated from
    post-crash-sessions.py rather than imported — a small, self-contained
    piece of logic with no existing shared home; extracting one would mean
    also touching post-crash-sessions.py for no behavior change of its own.
    A non-string value (schema drift, or a hostile field of the wrong JSON
    type) degrades to None rather than raising."""
    if not isinstance(value, str):
        return None
    return "".join(
        ch for ch in value
        if ord(ch) >= 0x20
        and ch != "\x7f"
        and not (0x80 <= ord(ch) <= 0x9f)
        and unicodedata.category(ch) != "Cf"
    )


def _ps_lstart(pid: int, *, run=subprocess.run) -> str | None:
    """Query ps for pid's process start time, pinned to UTC/C locale so the
    result is directly comparable to the registry's own procStart string
    with no timezone or locale conversion. Empty stdout means dead."""
    env = dict(os.environ)
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    try:
        result = run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, env=env, timeout=_SUBPROCESS_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _same_process(
    stored_proc_start,
    live_lstart: str | None,
    *,
    tolerance_seconds: float = _PROC_START_TOLERANCE_SECONDS,
    tz: timezone = UTC,
) -> bool | None:
    """True/False if both sides parse (within tolerance_seconds), else None
    when either side is missing, non-string, or unparseable — a pid that is
    alive but whose sameness can't be confirmed is not evidence either way."""
    def _parse(raw) -> datetime | None:
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return datetime.strptime(raw.strip(), _LSTART_FORMAT).replace(tzinfo=tz)
        except ValueError:
            return None

    stored = _parse(stored_proc_start)
    live = _parse(live_lstart)
    if stored is None or live is None:
        return None
    return abs((stored - live).total_seconds()) <= tolerance_seconds


def _read_registry_entry(config_dir: Path, pid: int, *, ps_lstart=_ps_lstart) -> str | None:
    """Read <config_dir>/sessions/<pid>.json and return its sanitized cwd,
    or None for "no usable entry": a missing file, malformed JSON, a
    non-dict payload, a stale procStart mismatch (this pid was recycled
    since the entry was written — this also covers the same pid appearing
    under two config dirs, since at most one dir's procStart can match the
    live process), or a live/non-stale entry whose cwd is missing or the
    wrong JSON type.
    Never raises — mirrors post-crash-sessions.py's own handling of this
    same undocumented, schema-driftable format.
    """
    path = config_dir / "sessions" / f"{pid}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    live_lstart = ps_lstart(pid)
    if _same_process(data.get("procStart"), live_lstart) is not True:
        return None
    return _sanitize_for_terminal(data.get("cwd"))


def build_title(pid: int, config_dirs: list[Path], *, emoji: str, explicit_title: str | None) -> str:
    """An explicit --title wins outright; otherwise the first resolved
    config dir with a live, non-stale registry entry that has a cwd wins,
    formatted as "{emoji} {basename(cwd)} ({pid})"; falls back to a bare
    "PID {pid}" when no config dir has one."""
    if explicit_title is not None:
        return _sanitize_for_terminal(explicit_title)
    sanitized_emoji = _sanitize_for_terminal(emoji) or ""
    for cdir in config_dirs:
        cwd = _read_registry_entry(cdir, pid)
        if cwd:
            basename = os.path.basename(cwd.rstrip("/")) or cwd
            return f"{sanitized_emoji} {basename} ({pid})".strip()
    return f"PID {pid}"


def write_title(device_path: Path, title: str) -> None:
    """Write an OSC 0 title-set escape sequence directly to device_path.
    Checks os.access() first for a clear error instead of a raw
    PermissionError traceback, and still wraps the write itself in
    try/except as a backstop for the check-then-write race. Authorization
    for this write is delegated entirely to the OS's tty permission bits on
    device_path — there's no in-tool check that the caller owns the pid
    whose terminal this is."""
    if not os.access(device_path, os.W_OK):
        raise ValueError(f"cannot write to {device_path}: permission denied")
    sequence = f"\033]0;{title}\007".encode()
    try:
        with open(device_path, "wb") as fh:
            fh.write(sequence)
    except PermissionError as exc:
        raise ValueError(f"cannot write to {device_path}: {exc}") from exc


def _run_list(config_dirs: list[Path]) -> int:
    # No cross-config-dir dedup: a pid genuinely live and matching procStart
    # under two config dirs (unlike build_title, which picks the first match)
    # renders as one row per dir.
    rows: list[tuple[int, str, str]] = []
    for cdir in config_dirs:
        sessions_dir = cdir / "sessions"
        if not sessions_dir.is_dir():
            continue
        try:
            candidates = sorted(sessions_dir.iterdir())
        except OSError:
            continue
        for path in candidates:
            if path.suffix != ".json" or not path.stem.isdigit():
                continue
            pid = int(path.stem)
            cwd = _read_registry_entry(cdir, pid)
            if cwd is None:
                continue
            try:
                tty = resolve_tty(pid)
            except ValueError:
                continue
            rows.append((pid, tty, cwd))

    if not rows:
        print("mark-terminal: no live sessions found")
        return 0

    width_pid = max(len("PID"), *(len(str(pid)) for pid, _, _ in rows))
    width_tty = max(len("TTY"), *(len(tty) for _, tty, _ in rows))
    print(f"{'PID':<{width_pid}}  {'TTY':<{width_tty}}  CWD")
    for pid, tty, cwd in rows:
        print(f"{pid:<{width_pid}}  {tty:<{width_tty}}  {cwd}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a PID's controlling terminal and title it, so a blocked session can be "
            "found among many open terminal windows by PID alone."
        ),
    )
    parser.add_argument("pid", type=_positive_pid, nargs="?", help="PID whose terminal to title")
    parser.add_argument(
        "--title", metavar="TEXT",
        help="Explicit title text; overrides the session-registry-derived title",
    )
    parser.add_argument(
        "--emoji", metavar="EMOJI", default=DEFAULT_EMOJI,
        help=f"Emoji prefix for an auto-derived title (default: {DEFAULT_EMOJI})",
    )
    parser.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help=(
            "Additional Claude Code config directory to scan (repeatable). The default resolved "
            "config dir is always scanned first. Absent this flag, every root declared in "
            "~/.claude/transcript-config-dirs is scanned too; passing this flag explicitly "
            "overrides that default and scans only the directories named here."
        ),
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List every currently-live Claude Code session with its PID/TTY/cwd, instead of titling one",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if platform.system() != "Darwin":
        print(
            "mark-terminal: macOS/BSD only — this repo's stow package also installs on Linux and "
            "WSL2, where ps output and /dev/ttysNNN device naming are shaped differently and "
            "unsupported here",
            file=sys.stderr,
        )
        return 2

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.list and args.pid is None:
        parser.error("pid is required unless --list is given")

    try:
        config_dirs = resolve_config_dirs(args.extra_config_dirs, tool_name="mark-terminal")
    except ValueError as exc:
        print(f"mark-terminal: {exc}", file=sys.stderr)
        return 2

    if args.list:
        return _run_list(config_dirs)

    try:
        tty = resolve_tty(args.pid)
    except ValueError as exc:
        print(f"mark-terminal: {exc}", file=sys.stderr)
        return 1

    title = build_title(args.pid, config_dirs, emoji=args.emoji, explicit_title=args.title)
    device_path = Path("/dev") / tty
    try:
        write_title(device_path, title)
    except ValueError as exc:
        print(f"mark-terminal: {exc}", file=sys.stderr)
        return 1

    print(f"Titled {device_path} (pid {args.pid}): {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
