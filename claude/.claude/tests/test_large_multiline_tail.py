"""Contract test for helpers.LARGE_MULTILINE_TAIL."""
from __future__ import annotations

from helpers import LARGE_MULTILINE_TAIL


def test_large_multiline_tail_is_multi_line_and_exceeds_100_kib() -> None:
    """A shorter or single-line tail makes the SIGPIPE regression tests pass vacuously."""
    assert LARGE_MULTILINE_TAIL.count("\n") > 1
    assert len(LARGE_MULTILINE_TAIL) > 100 * 1024
