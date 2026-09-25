"""Unit tests for _lib.sh's _lib_passes_path_char_allowlist, the pure-bash
byte-class gate announce-resume-command.sh runs on FILE_PATH and a resolved
worktree root before interpolating either into its emitted resume-context
command.

These source _lib.sh directly and call the function through /bin/bash -- no
hook invocation, no JSON payload -- mirroring test_lib_mask_shell_quotes.py.
The hook's own test file keeps one subprocess case per branch; the byte-class
matrix lives here instead.
"""
from __future__ import annotations

import os
import string
import subprocess

import pytest
from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"

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


def _run_sweep(values: list[bytes], locale: str) -> bytes:
    """Runs _lib_passes_path_char_allowlist once per value in one bash spawn
    under LC_ALL=<locale> and returns the raw '1'/'0' verdict bytes, one per
    input value, in order.

    Pins /bin/bash directly rather than a bare `bash` resolved from PATH
    (test_lib.py's _run_lib_call), since a Homebrew bash 5 on macOS would
    hide a failure specific to the shipped /bin/bash 3.2. Values are passed
    as argv bytes, not interpolated into the script text, so an invalid-
    UTF-8 byte needs no filename-encoding workaround."""
    env = dict(os.environ)
    env["LC_ALL"] = locale
    result = subprocess.run(
        [b"/bin/bash", b"-c", _SWEEP_SCRIPT, b"_", str(LIB_SH).encode(), *values],
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _en_us_utf8_exhibits_locale_sensitive_bracket_matching() -> bool:
    """True only when this runner's en_US.UTF-8 makes bracket ranges locale-sensitive;
    if it collapses to C, the en_US params cannot guard the LC_ALL=C pin.
    set-session-title-from-branch.sh's header comment is the canonical description
    of the locale-sensitive bracket-range hazard."""
    probe = subprocess.run(
        [b"/bin/bash", b"-c", b"[[ \xc3\xa9 == [a-z] ]]"],
        capture_output=True,
        env={**os.environ, "LC_ALL": "en_US.UTF-8"},
        check=False,
    )
    # Exit 1 means the match failed: hazard absent. Any other non-zero exit
    # is a broken probe, not a skippable locale.
    assert probe.returncode in (0, 1), (probe.returncode, probe.stderr)
    return probe.returncode == 0


@pytest.fixture(scope="module")
def utf8_locale_is_functional() -> bool:
    return _en_us_utf8_exhibits_locale_sensitive_bracket_matching()


def _bin_bash_version() -> str:
    result = subprocess.run(
        [b"/bin/bash", b"-c", b"printf %s \"$BASH_VERSION\""],
        capture_output=True,
        check=False,
    )
    return result.stdout.decode(errors="replace")


def _skip_if_utf8_locale_not_functional(locale: str, utf8_locale_is_functional: bool) -> None:
    if locale != "C" and not utf8_locale_is_functional:
        pytest.skip(
            f"Under /bin/bash {_bin_bash_version()}, LC_ALL=en_US.UTF-8 did not make `[a-z]` match "
            "an accented letter, so the LC_ALL=C pin is invisible here."
        )


class TestLibPassesPathCharAllowlist:
    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_single_byte_sweep_accepts_exactly_the_allowlisted_class(
        self, locale, utf8_locale_is_functional
    ):
        """Sweeps every single byte from 0x01 to 0xFF; NUL cannot go in argv.
        Exactly the 68 bytes in [A-Za-z0-9._/@+-] are accepted.
        The sweep runs under both the C locale and a caller-exported UTF-8 locale.
        It catches a typo in the class, including the boundary bytes `:`, `[`, backtick and `{`.
        Single high bytes are invalid UTF-8 and are never collated.
        The sweep therefore cannot detect removal of the helper's LC_ALL=C pin."""
        _skip_if_utf8_locale_not_functional(locale, utf8_locale_is_functional)
        values = [bytes([b]) for b in range(1, 256)]
        verdicts = _run_sweep(values, locale)
        assert len(verdicts) == len(values)
        accepted = {v[0] for v, bit in zip(values, verdicts, strict=True) if bit == ord("1")}
        assert accepted == _EXPECTED_ALLOWED_BYTES

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    @pytest.mark.parametrize("value", _DENY_WHOLE_VALUES)
    def test_whole_value_deny_cases(self, value, locale, utf8_locale_is_functional):
        """Only the non-ascii-e-acute case detects removal of the LC_ALL=C pin.
        It does so only on bash < 5 (macOS /bin/bash 3.2).
        A green Linux CI run is therefore not proof the pin exists."""
        _skip_if_utf8_locale_not_functional(locale, utf8_locale_is_functional)
        assert _run_sweep([value], locale) == b"0"

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    @pytest.mark.parametrize("value", _ALLOW_WHOLE_VALUES)
    def test_whole_value_allow_cases(self, value, locale, utf8_locale_is_functional):
        _skip_if_utf8_locale_not_functional(locale, utf8_locale_is_functional)
        assert _run_sweep([value], locale) == b"1"

    def test_caller_locale_is_unaffected_by_the_helpers_own_pin(self):
        """The function's LC_ALL=C runs in a subshell body, so it must not
        leak into the caller's own shell."""
        result = subprocess.run(
            [
                "/bin/bash",
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
                "/bin/bash",
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
