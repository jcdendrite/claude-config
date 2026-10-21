"""Contract tests for helpers.installed_locale_where: the locale it returns and its fail-or-skip policy when none matches."""
from __future__ import annotations

# module object for monkeypatch.setattr: installed_locale_where reads _installed_utf8_locale_names from it at call time
import helpers
import pytest
from helpers import installed_locale_where

_ALWAYS_HOLDS = "true"
_NEVER_HOLDS = "false"


def test_installed_locale_where_returns_the_first_installed_locale_whose_condition_holds(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(helpers, "_installed_utf8_locale_names", lambda: ["first.UTF-8", "second.UTF-8"])

    assert installed_locale_where(_ALWAYS_HOLDS, "x", absent_message="unused") == "first.UTF-8"


def test_installed_locale_where_passes_the_probe_argument_to_the_condition_as_dollar_one(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(helpers, "_installed_utf8_locale_names", lambda: ["only.UTF-8"])

    assert installed_locale_where('[ "$1" = "wanted" ]', "wanted", absent_message="unused") == "only.UTF-8"


def test_installed_locale_where_sets_lc_all_per_candidate_and_skips_a_candidate_whose_condition_fails(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(helpers, "_installed_utf8_locale_names", lambda: ["plain.UTF-8", "wanted.UTF-8"])

    assert installed_locale_where('[ "$LC_ALL" = "wanted.UTF-8" ]', "x", absent_message="unused") == "wanted.UTF-8"


def test_installed_locale_where_fails_under_ci_when_no_installed_locale_matches(monkeypatch) -> None:
    monkeypatch.setattr(helpers, "_installed_utf8_locale_names", list)
    monkeypatch.setenv("CI", "true")

    with pytest.raises(pytest.fail.Exception, match="no locale for the probe; CI must provide one"):
        installed_locale_where(_ALWAYS_HOLDS, "x", absent_message="no locale for the probe")


def test_installed_locale_where_fails_under_ci_when_installed_locales_all_fail_the_condition(monkeypatch) -> None:
    monkeypatch.setattr(helpers, "_installed_utf8_locale_names", lambda: ["only.UTF-8"])
    monkeypatch.setenv("CI", "true")

    with pytest.raises(pytest.fail.Exception, match="CI must provide one"):
        installed_locale_where(_NEVER_HOLDS, "x", absent_message="no locale for the probe")


def test_installed_locale_where_skips_outside_ci_when_no_installed_locale_matches(monkeypatch) -> None:
    monkeypatch.setattr(helpers, "_installed_utf8_locale_names", list)
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(pytest.skip.Exception, match="no locale for the probe"):
        installed_locale_where(_ALWAYS_HOLDS, "x", absent_message="no locale for the probe")
