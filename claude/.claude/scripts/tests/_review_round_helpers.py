"""Fixture builders shared by the tests of transcript_analysis subcommands that read review rounds."""
from .conftest import _bash_use

_SONNET = "claude-sonnet-5"

_MARKER_WRITE_COMMAND = "marker.sh write code-review"


def _marker_write_use(tool_id: str) -> dict:
    return _bash_use(tool_id, _MARKER_WRITE_COMMAND)
