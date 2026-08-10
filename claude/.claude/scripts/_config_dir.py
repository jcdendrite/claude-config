"""Shared Claude Code config-directory resolution for scripts/ tooling."""
import os
import sys
from pathlib import Path

# Matches bash's [:space:] class (the set cleanup-merged-branches.sh's
# read_configured_roots trims against) -- not str.strip(), which also eats
# NBSP/U+3000.
_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"


def config_dir() -> Path:
    """Return the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set (must be absolute), else ~/.claude."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"CLAUDE_CONFIG_DIR must be an absolute path, got: {override!r}")
        return path
    return Path.home() / ".claude"


def declared_transcript_roots() -> list[Path]:
    """Return the config dirs listed in ~/.claude/transcript-config-dirs (or
    TRANSCRIPT_CONFIG_DIRS_FILE, a test seam mirroring
    cleanup-merged-branches.sh's CLEANUP_MERGED_BRANCHES_ROOTS_FILE),
    deduped by resolved real path.

    Returns config dirs, matching the file's own contents -- NOT projects
    dirs; a caller needing projects dirs derives `d / "projects"` itself.
    config_dir() itself is never included here -- the caller adds it.

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
    lacks a projects/ subdirectory, or raises OSError while being checked (a
    permissions failure, not just a missing path) is skipped with a warning
    naming it by index only, never by path -- the path identifies an
    engagement. The roots file itself being absent or unreadable is a
    silent single-root no-op, matching a stale line's fail-open behavior: a
    config-file problem must never break every invocation.
    """
    roots_file = Path(os.environ.get("TRANSCRIPT_CONFIG_DIRS_FILE") or (Path.home() / ".claude" / "transcript-config-dirs"))
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
                valid = candidate.is_dir() and (candidate / "projects").is_dir()
            except OSError:
                valid = False
        if not valid:
            print(f"declared_transcript_roots: declared root {index} unreadable", file=sys.stderr)
            continue
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        roots.append(candidate)
    return roots
