"""Contract tests for helpers.LARGE_MULTILINE_TAIL and helpers.MASK_SAFE_MULTILINE_TAIL."""
from __future__ import annotations

from helpers import LARGE_MULTILINE_TAIL, MASK_SAFE_MULTILINE_TAIL


def test_large_multiline_tail_is_multi_line_and_exceeds_100_kib() -> None:
    """A shorter or single-line tail makes the SIGPIPE regression tests pass vacuously."""
    assert LARGE_MULTILINE_TAIL.count("\n") > 1
    assert len(LARGE_MULTILINE_TAIL) > 100 * 1024


def test_mask_safe_multiline_tail_is_multi_line_above_pipe_capacity_and_below_150_kib() -> None:
    """Above 64 KiB keeps the tail past the pipe capacity; the upper bound keeps `_lib_mask_shell_quotes` inside its 5 s cap."""
    assert MASK_SAFE_MULTILINE_TAIL.count("\n") > 1
    assert 64 * 1024 < len(MASK_SAFE_MULTILINE_TAIL) < 150 * 1024
