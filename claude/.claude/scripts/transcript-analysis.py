#!/usr/bin/env python3
"""transcript-analysis.py — Claude Code transcript analysis toolkit.
Every subcommand is a local read except the few that call gh and the few that
write a file; docs/transcript-analysis.md lists both sets.
"""

from transcript_analysis.cli import build_parser, main

# Test files read both names from a loaded copy of this script, and tests/_move_fidelity.py reads build_parser.
__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    main()
