"""Shared Claude Code config-directory resolution for scripts/ tooling."""
import os
from pathlib import Path


def config_dir() -> Path:
    """Return the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set (must be absolute), else ~/.claude."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"CLAUDE_CONFIG_DIR must be an absolute path, got: {override!r}")
        return path
    return Path.home() / ".claude"
