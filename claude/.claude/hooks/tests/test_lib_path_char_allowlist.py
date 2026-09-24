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

# Independently derived from the allowlist's documented character set
# (bash 3.2's `case` glob under _lib.sh is the thing under test, so this must
# not be copied from that glob's own literal text).
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
    """Whether this runner's en_US.UTF-8 locale actually enables locale-
    sensitive bracket-range collation -- the hazard documented in
    set-session-title-from-branch.sh's locale-sensitive-range comment -- rather than silently
    collapsing to C behavior because the locale isn't installed. Without
    this check, an en_US.UTF-8-parametrized case run on a runner where that
    locale collapses to C would pass regardless of whether the helper's own
    LC_ALL=C pin still works, giving no failure signal for a real
    regression in the property this file guards."""
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


# Removing the helper's LC_ALL=C pin is detectable only by the
# `non-ascii-e-acute` whole-value deny case on bash < 5 (macOS /bin/bash 3.2).
# A green Linux CI run (bash 5, where the en_US.UTF-8 cases skip) is not proof
# the pin exists.
def _skip_if_utf8_locale_not_functional(locale: str, utf8_locale_is_functional: bool) -> None:
    if locale != "C" and not utf8_locale_is_functional:
        pytest.skip(
            "runner's en_US.UTF-8 locale does not exhibit locale-sensitive "
            "bracket matching -- not a meaningful regression guard here"
        )


class TestLibPassesPathCharAllowlist:
    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_single_byte_sweep_accepts_exactly_the_allowlisted_class(
        self, locale, utf8_locale_is_functional
    ):
        """Exhaustive 0x01-0xFF single-byte sweep (NUL can't go in argv):
        exactly the 68 bytes in [A-Za-z0-9._/@+-] are accepted, under both
        the C locale and a caller-exported UTF-8 locale -- catches a typo in
        the class, including the boundary bytes `:`, `[`, backtick and `{`.
        Single high bytes are invalid UTF-8 and are never collated, so this
        sweep cannot detect removal of the helper's LC_ALL=C pin."""
        _skip_if_utf8_locale_not_functional(locale, utf8_locale_is_functional)
        values = [bytes([b]) for b in range(1, 256)]
        verdicts = _run_sweep(values, locale)
        assert len(verdicts) == len(values)
        accepted = {v[0] for v, bit in zip(values, verdicts, strict=True) if bit == ord("1")}
        assert accepted == _EXPECTED_ALLOWED_BYTES

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    @pytest.mark.parametrize("value", _DENY_WHOLE_VALUES)
    def test_whole_value_deny_cases(self, value, locale, utf8_locale_is_functional):
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
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "status=1"
