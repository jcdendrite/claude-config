"""Unit tests for _lib.sh's _lib_passes_path_char_allowlist, the pure-bash
byte-class gate its callers run on a path before putting it in their output:
- announce-resume-command.sh, on FILE_PATH and a resolved worktree root,
  before interpolating either into its emitted resume-context command.
- announce-approved-plan-path.sh, on each plan path it relays.

These source _lib.sh directly and call the function through bash -- no
hook invocation, no JSON payload -- mirroring test_lib_mask_shell_quotes.py.
Each caller's own test file keeps subprocess cases for its emission behavior;
the byte-class matrix lives here instead.
"""
from __future__ import annotations

import os
import shutil
import string
import subprocess

import pytest
from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"
BASH = shutil.which("bash")
assert BASH is not None, "bash is not on PATH"

# Derived independently from the documented character set.
# It must not copy the literal text of the `case` glob in `_lib.sh`, because that glob is the code under test.
_EXPECTED_ALLOWED_BYTES = frozenset((string.ascii_letters + string.digits + "._/@+-").encode("ascii"))

_REQUIRE_FUNCTION_DEFINED = "declare -F _lib_passes_path_char_allowlist >/dev/null || exit 91; "

_SWEEP_SCRIPT = (
    b'. "$1" || exit 90; shift; '
    + _REQUIRE_FUNCTION_DEFINED.encode()
    + b'for v in "$@"; do '
    b'if _lib_passes_path_char_allowlist "$v"; then printf 1; else printf 0; fi; done'
)

_DENY_WHOLE_VALUES = [
    pytest.param(b"", id="empty"),
    pytest.param(b"linked\n\nSENTINEL-INJECT\n\nwt", id="embedded-newlines"),
    pytest.param(b"a-handoff.md\n", id="trailing-newline"),
    pytest.param(b"my notes-handoff.md", id="embedded-space"),
    pytest.param("café-handoff.md".encode(), id="non-ascii-e-acute"),
    pytest.param(b"esc\x1b[31m-handoff.md", id="escape-byte"),
    pytest.param(b"abc\x9bdef", id="invalid-utf8-byte"),
]

_ALLOW_WHOLE_VALUES = [
    pytest.param(b"team@x+y-handoff.md", id="at-and-plus-file-path"),
    pytest.param(b"/fake/wt@team/a+b", id="at-and-plus-worktree-root"),
]


def _run_sweep(values: list[bytes]) -> bytes:
    """Runs _lib_passes_path_char_allowlist once per value in one bash spawn
    under LC_ALL=C and returns the raw '1'/'0' verdict bytes, one per input
    value, in order. Values are passed as argv bytes, not interpolated into
    the script text, so an invalid-UTF-8 byte needs no filename-encoding
    workaround."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    result = subprocess.run(
        [os.fsencode(BASH), b"-c", _SWEEP_SCRIPT, b"_", str(LIB_SH).encode(), *values],
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestLibPassesPathCharAllowlist:
    def test_single_byte_sweep_accepts_exactly_the_allowlisted_class(self):
        """Sweeps every single byte from 0x01 to 0xFF; NUL cannot go in argv.
        Exactly the 68 bytes in [A-Za-z0-9._/@+-] are accepted.
        The sweep runs under the C locale.
        It catches a typo in the class, including the boundary bytes `:`, `[`, backtick and `{`.
        Single high bytes are invalid UTF-8 and are never collated.
        The sweep therefore cannot detect removal of the helper's LC_ALL=C pin."""
        values = [bytes([b]) for b in range(1, 256)]
        verdicts = _run_sweep(values)
        assert len(verdicts) == len(values)
        accepted = {v[0] for v, bit in zip(values, verdicts, strict=True) if bit == ord("1")}
        assert accepted == _EXPECTED_ALLOWED_BYTES

    @pytest.mark.parametrize("value", _DENY_WHOLE_VALUES)
    def test_whole_value_deny_cases(self, value):
        assert _run_sweep([value]) == b"0"

    @pytest.mark.parametrize("value", _ALLOW_WHOLE_VALUES)
    def test_whole_value_allow_cases(self, value):
        assert _run_sweep([value]) == b"1"

    def test_caller_locale_is_unaffected_by_the_helpers_own_pin(self):
        """The function's LC_ALL=C runs in a subshell body, so it must not
        leak into the caller's own shell."""
        result = subprocess.run(
            [
                BASH,
                "-c",
                f'. "{LIB_SH}"; {_REQUIRE_FUNCTION_DEFINED}LC_ALL=en_US.UTF-8; '
                '_lib_passes_path_char_allowlist x; printf %s "$LC_ALL"',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "en_US.UTF-8"

    def test_no_arg_call_under_set_u_returns_one_without_aborting(self):
        """`${1-}` keeps the function callable with $1 unset under a
        caller's `set -u`, denying (exit 1) rather than aborting on an
        unbound variable."""
        result = subprocess.run(
            [
                BASH,
                "-c",
                f'set -u; . "{LIB_SH}"; {_REQUIRE_FUNCTION_DEFINED}_lib_passes_path_char_allowlist; echo "status=$?"',
            ],
            capture_output=True,
            text=True,
            check=False,
            # LC_ALL=C keeps bash's error text in English so the stderr assertion is meaningful.
            env={**os.environ, "LC_ALL": "C"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "status=1"
        assert "unbound variable" not in result.stderr
