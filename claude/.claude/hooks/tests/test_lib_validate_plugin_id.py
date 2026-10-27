"""Unit tests for _lib.sh's _lib_validate_plugin_id."""
from __future__ import annotations

import os
import shlex
import time

import pytest
from helpers import (
    _sourced_value,
)

from ._lib_sh_helpers import (
    _run_lib_call,
)
from .test_config_lib import _locale_that_widens_ascii_ranges

# ---------------------------------------------------------------------------
# _lib_validate_plugin_id / _lib_missing_plugin_ids -- direct unit coverage
# independent of both callers (bootstrap-project-plugins.sh's tests and
# reconcile-project-plugins.sh's tests in
# claude/.claude/scripts/tests/test_reconcile_project_plugins.py), so neither
# caller can silently diverge from the other on this shared seam.
# ---------------------------------------------------------------------------

# A bounded-cost check runs within this multiple of a control run.
# The unbounded `##*@` removal on a 96 KiB id costs orders of magnitude more.
_PLUGIN_ID_COST_RATIO_LIMIT = 10


class TestLibValidatePluginId:
    @pytest.mark.parametrize(
        "plugin_id",
        ["foo@bar", "a@b", "foo.bar@my-marketplace", "foo_bar@marketplace_1"],
    )
    def test_accepts_well_formed_id(self, plugin_id: str) -> None:
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 0, repr(result)

    def test_accepts_marketplace_name_containing_npm_as_a_substring(self) -> None:
        """The `npm` exclusion is an exact match on the marketplace half, not
        a substring match -- a marketplace merely named similarly must still
        pass."""
        result = _run_lib_call("_lib_validate_plugin_id foo@npmxyz", env=dict(os.environ))
        assert result.returncode == 0, repr(result)

    @pytest.mark.parametrize(
        "plugin_id",
        ["-foo@bar", ".foo@bar", "_foo@bar"],
    )
    def test_rejects_leading_punctuation_before_the_marketplace_separator(
        self, plugin_id: str
    ) -> None:
        """Both halves must start with an alphanumeric character --
        `[A-Za-z0-9._-]+` alone would admit a leading `-`, `.`, or `_`, which
        would defeat the anti-flag-injection purpose of validating at all."""
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 1, repr(result)

    @pytest.mark.parametrize(
        "plugin_id",
        ["foo@-bar", "foo@.bar", "foo@_bar"],
    )
    def test_rejects_leading_punctuation_after_the_marketplace_separator(
        self, plugin_id: str
    ) -> None:
        """Mirrors test_rejects_leading_punctuation_before_the_marketplace_
        separator above -- the marketplace half is anchored on the same
        alphanumeric-first requirement as the name half, not just the name
        half."""
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 1, repr(result)

    def test_rejects_reserved_npm_pseudo_marketplace(self) -> None:
        """The CLI treats `<pkg>@npm` as a real install form; `npm` would
        otherwise pass the regex and be blocked only by happening not to
        appear in the registered-marketplace list."""
        result = _run_lib_call("_lib_validate_plugin_id foo@npm", env=dict(os.environ))
        assert result.returncode == 1, repr(result)

    def test_rejects_missing_marketplace_half(self) -> None:
        result = _run_lib_call("_lib_validate_plugin_id foo", env=dict(os.environ))
        assert result.returncode == 1, repr(result)

    @pytest.mark.parametrize(
        "plugin_id",
        [
            "a b@c",
            "a@b@c",
            "",
            "a;x@b",
            "a$x@b",
            "a`x@b",
            "a/x@b",
            "plugin-a\x1b@market-a",
            "plugin-a@market-a\nplugin-b@market-a",
        ],
        ids=[
            "space",
            "second-at-sign",
            "empty",
            "semicolon",
            "dollar-sign",
            "backtick",
            "slash",
            "escape-character",
            "two-valid-ids-joined-by-a-newline",
        ],
    )
    def test_rejects_id_outside_the_name_at_marketplace_alphabet(self, plugin_id: str) -> None:
        """Shell metacharacters, whitespace, control bytes, and a second `@` fall outside
        the `name@marketplace` shape, so the validator rejects each one.
        `TestPluginIdRevalidation` in `test_reconcile_project_plugins.py` pins that the reconciler skips an invalid id."""
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 1, repr(result)

    def test_accepts_id_of_exactly_the_max_length_with_a_long_marketplace_half(self) -> None:
        """The length bound is inclusive, so the longest accepted id is
        `a@` plus a marketplace half that fills the rest."""
        max_length = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))
        plugin_id = "a@" + "b" * (max_length - len("a@"))
        assert len(plugin_id) == max_length

        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )

        assert result.returncode == 0, repr(result)

    def test_rejects_id_one_byte_over_the_max_length_with_a_long_marketplace_half(self) -> None:
        """Guards the cost bound: bash's `##*@` removal is super-linear in the
        marketplace half, so a valid-shaped id past the bound must be refused
        before any pattern work."""
        max_length = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))
        plugin_id = "a@" + "b" * (max_length - len("a@") + 1)
        assert len(plugin_id) == max_length + 1

        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )

        assert result.returncode == 1, repr(result)

    @pytest.mark.timing
    def test_rejects_valid_shaped_id_of_96_kib_marketplace_half_as_fast_as_a_max_length_id(self) -> None:
        """The length check runs before the super-linear `##*@` removal, so a
        96 KiB id costs about what a maximum-length one does. Timing is a ratio
        to a control run on the same machine, never an absolute bound."""
        max_length = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))
        control_id = "a@" + "b" * (max_length - len("a@"))
        control_seconds = max(self._validate_run(control_id)[1] for _ in range(3))

        hostile_runs = [self._validate_run("a@" + "b" * (96 * 1024)) for _ in range(3)]

        assert {returncode for returncode, _ in hostile_runs} == {1}, hostile_runs
        # The fastest of three: a regression is slow on every run, while a one-off scheduler stall is not.
        hostile_seconds = min(seconds for _, seconds in hostile_runs)
        assert hostile_seconds < control_seconds * _PLUGIN_ID_COST_RATIO_LIMIT, (hostile_seconds, control_seconds)

    @staticmethod
    def _validate_run(plugin_id: str) -> tuple[int, float]:
        started = time.monotonic()
        result = _run_lib_call(f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ))
        return result.returncode, time.monotonic() - started

    def test_rejects_non_ascii_letter_even_under_a_locale_that_widens_bracket_ranges(self) -> None:
        """The alphabet is ASCII-only only because the check pins LC_ALL=C;
        under such a locale an unpinned `[A-Za-z]` admits accented letters."""
        env = {**os.environ, "LC_ALL": _locale_that_widens_ascii_ranges()}
        accented_plugin_id = "caf\u00e9@bar"

        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(accented_plugin_id)}", env=env
        )

        assert result.returncode == 1, repr(result)
