"""The one render point for text that reaches the terminal or a run record's `missing_detail`.

Child-derived and agent-derived text can hold terminal control sequences or newlines that imitate a report
line, so it passes through here before it is stored or printed.
"""
from __future__ import annotations

_KEPT_CONTROL_CHARACTERS = frozenset("\n\t")


def safe_text(text: str, *, keep_line_breaks: bool = False) -> str:
    """Replaces every non-printable character with its backslash escape, leaving printable text unchanged.
    `keep_line_breaks` leaves newline and tab in place, for a message the runner composed itself across lines."""
    return "".join(
        character if character.isprintable() or (keep_line_breaks and character in _KEPT_CONTROL_CHARACTERS)
        else character.encode("unicode_escape").decode("ascii")
        for character in text
    )


class SafeStream:
    """A text stream whose every write goes through `safe_text`, so no line the runner prints can carry an escape
    sequence, whichever site composed it. Every other attribute is the wrapped stream's."""

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        self._stream.write(safe_text(text, keep_line_breaks=True))
        return len(text)

    def __getattr__(self, name: str):
        return getattr(self._stream, name)
