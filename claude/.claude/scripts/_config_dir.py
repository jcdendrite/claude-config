"""Shared Claude Code config-directory resolution for scripts/ tooling."""
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

# Matches bash's [:space:] class (the set cleanup-merged-branches.sh's
# read_configured_roots trims against) -- not str.strip(), which also eats
# NBSP/U+3000.
_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"

# The display literal for messages naming *where to declare* a root (e.g. an
# install.sh TIP, a scan-result sentence) -- unlike declared_roots_file()
# below, this intentionally does NOT follow the TRANSCRIPT_CONFIG_DIRS_FILE
# test seam, since an operator-facing message must always name the real path.
TRANSCRIPT_CONFIG_DIRS_LABEL = "~/.claude/transcript-config-dirs"

RootsFileState = Literal["absent", "unreadable", "present"]


def config_dir() -> Path:
    """Return the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set (must be absolute), else ~/.claude."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"CLAUDE_CONFIG_DIR must be an absolute path, got: {override!r}")
        return path
    return Path.home() / ".claude"


def declared_roots_file() -> Path:
    """Return the path declared_roots_matching() reads: TRANSCRIPT_CONFIG_DIRS_FILE
    if set (a test seam mirroring cleanup-merged-branches.sh's
    CLEANUP_MERGED_BRANCHES_ROOTS_FILE), else ~/.claude/transcript-config-dirs.
    Unlike TRANSCRIPT_CONFIG_DIRS_LABEL, this DOES follow the seam.
    """
    return Path(os.environ.get("TRANSCRIPT_CONFIG_DIRS_FILE") or (Path.home() / ".claude" / "transcript-config-dirs"))


def declared_roots_file_state() -> RootsFileState:
    """Return whether the roots file is absent, unreadable (exists but raised
    OSError on read, e.g. permissions), or present (read successfully --
    regardless of whether its content is empty, comments-only, or every entry
    later fails validation; those all count as "present" here, distinguished
    only by declared_roots_matching()'s own per-index stderr warnings).

    Uses its own read_text() call rather than Path.exists() or sharing
    declared_roots_matching()'s parse: .exists() re-raises OSError on some
    Python versions but swallows it on others, and returns True for a
    present-but-unreadable file -- masking exactly the state an operator must
    fix. Not Path.is_file() either, for the same masking reason.
    """
    try:
        declared_roots_file().read_text(errors="replace")
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    return "present"


def declared_roots_matching(is_valid: Callable[[Path], bool], *, warn_prefix: str) -> list[Path]:
    """Parse ~/.claude/transcript-config-dirs (or TRANSCRIPT_CONFIG_DIRS_FILE,
    a test seam mirroring cleanup-merged-branches.sh's
    CLEANUP_MERGED_BRANCHES_ROOTS_FILE), returning declared config dirs
    (NOT projects dirs -- a caller needing those derives `d / "projects"`
    itself) deduped by resolved real path. config_dir() itself is never
    included here -- the caller adds it.

    `is_valid` is called on each candidate only after it has already passed
    the absolute-path and `is_dir()` checks below, and decides what "valid"
    means beyond that for the caller -- e.g. requiring a `projects/`
    subdirectory, or a looser `sessions/`-or-`projects/` check.
    `warn_prefix` names the caller in each skipped-line warning.

    Ports cleanup-merged-branches.sh's read_configured_roots() (:138-153):
    blank lines and leading-'#' comments are skipped (never a mid-line
    split, which would truncate a legitimate path containing '#'), CRLF/LF
    line endings are both handled by str.splitlines(), padding is trimmed
    against the explicit ASCII whitespace set above, and a leading '~' or
    '~/' is expanded via a literal $HOME prefix substitution -- never
    Path.expanduser(), which also resolves ~otheruser via the passwd
    database.

    A declared path that is not absolute (after tilde expansion -- a bare
    relative line would otherwise resolve against the process's CWD at
    invocation time, making the same line silently present or absent
    depending on which directory the tool is run from), is not a directory,
    fails `is_valid`, or raises OSError while being checked (a permissions
    failure, not just a missing path) is skipped with a warning naming it by
    index only, never by path -- the path identifies an engagement. The
    roots file itself being absent or unreadable is a silent single-root
    no-op, matching a stale line's fail-open behavior: a config-file problem
    must never break every invocation.
    """
    roots_file = declared_roots_file()
    try:
        raw_text = roots_file.read_text(errors="replace")
    except OSError:
        return []

    home = str(Path.home())
    roots: list[Path] = []
    seen_resolved: set[Path] = set()
    for index, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip(_ASCII_WHITESPACE)
        if not line or line.startswith("#"):
            continue
        if line == "~":
            line = home
        elif line.startswith("~/"):
            line = home + line[1:]
        candidate = Path(line)
        if not candidate.is_absolute():
            # Matches config_dir()'s own absolute-path requirement above -- see
            # the docstring for why a relative line can't be trusted as-is.
            valid = False
        else:
            try:
                valid = candidate.is_dir() and is_valid(candidate)
            except OSError:
                valid = False
        if not valid:
            print(f"{warn_prefix}: declared root {index} unreadable", file=sys.stderr)
            continue
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        roots.append(candidate)
    return roots


def declared_transcript_roots() -> list[Path]:
    """Return the config dirs listed in ~/.claude/transcript-config-dirs (or
    TRANSCRIPT_CONFIG_DIRS_FILE) that contain a `projects/` subdirectory.
    See declared_roots_matching() for the parsing contract this builds on.
    """
    return declared_roots_matching(
        lambda candidate: (candidate / "projects").is_dir(),
        warn_prefix="declared_transcript_roots",
    )
