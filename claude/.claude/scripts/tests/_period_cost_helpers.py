"""Helpers shared by the period_cost test files."""
import errno

import pytest

_RESOLVE_ERRORS = [
    pytest.param(OSError(errno.ELOOP, "too many levels of symbolic links"), id="os_error"),
    pytest.param(RuntimeError("loop"), id="runtime_error"),
]
