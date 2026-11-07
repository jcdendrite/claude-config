"""Contract tests for helpers._nonempty_shell_lines, the shared reader behind the roster helpers."""
from __future__ import annotations

import pytest
from helpers import _nonempty_shell_lines


def test_nonzero_exit_raises_with_the_stderr_text() -> None:
    with pytest.raises(AssertionError, match="reading probe-roster exited 3: probe-failure"):
        _nonempty_shell_lines("echo probe-failure >&2; exit 3", "probe-roster")


@pytest.mark.parametrize(
    "shell_body",
    [
        pytest.param("true", id="no-bytes"),
        pytest.param("printf '\\n'", id="only-a-newline"),
    ],
)
def test_empty_output_raises_naming_what_was_read(shell_body: str) -> None:
    with pytest.raises(AssertionError, match="reading probe-roster printed nothing"):
        _nonempty_shell_lines(shell_body, "probe-roster")


def test_blank_lines_are_dropped_from_the_returned_lines() -> None:
    assert _nonempty_shell_lines("printf 'alpha\\n\\nbeta\\n'", "probe-roster") == ["alpha", "beta"]
