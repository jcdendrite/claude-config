"""Unit tests for _lib.sh's _lib_skill_basename, _lib_args_lead_word_is, the agent-roster functions and _lib_list_contains."""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import installed_locale_where

from ._lib_sh_helpers import _LIB_SH

# --- _lib_skill_basename ----------------------------------------------------
#
# Hooks that match a Skill call by name call this helper, so none carries its
# own copy of the qualified-name split.


@pytest.mark.parametrize(
    ("skill_name", "expected_basename"),
    [
        pytest.param("code-review", "code-review", id="bare_name"),
        pytest.param("plugin-x:code-review", "code-review", id="plugin_qualified"),
        pytest.param(
            ".claude/worktrees/some-branch/claude:code-review",
            "code-review",
            id="directory_qualified",
        ),
        pytest.param("/code-review", "code-review", id="leading_slash"),
        pytest.param("plugin:", "", id="trailing_colon_empty_name"),
        pytest.param("code-review/", "", id="trailing_slash_empty_name"),
        pytest.param("plugin:dir/", "", id="plugin_qualified_trailing_slash_empty_name"),
        pytest.param("", "", id="empty_string"),
    ],
)
def test_skill_basename_prints_final_name_component(skill_name: str, expected_basename: str) -> None:
    argv = [
        "bash", "-c", f'. {_LIB_SH}; _lib_skill_basename "$@"', "bash", skill_name,
    ]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected_basename + "\n"


def test_skill_basename_with_no_argument_prints_empty_line() -> None:
    argv = ["bash", "-c", f". {_LIB_SH}; _lib_skill_basename"]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "\n"


def test_skill_basename_with_no_argument_under_set_u_prints_empty_line() -> None:
    """`${1:-}` keeps the helper callable with $1 unset under a caller's `set -u`."""
    argv = ["bash", "-c", f". {_LIB_SH}; set -u; _lib_skill_basename"]
    # LC_ALL=C keeps bash's error text in English so the stderr assertion is meaningful.
    env = {**os.environ, "LC_ALL": "C"}
    result = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "\n"
    assert "unbound variable" not in result.stderr


# --- _lib_args_lead_word_is -------------------------------------------------
#
# The review-orchestrator routing gate calls this helper to read the
# `unrouted` lead word of a Skill call's args.


def _args_lead_word_is(args: str, word: str, *, env_overrides: dict | None = None) -> int:
    """Return the exit code of `_lib_args_lead_word_is args word`."""
    argv = ["bash", "-c", f'. {_LIB_SH}; _lib_args_lead_word_is "$@"', "bash", args, word]
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(argv, capture_output=True, text=True, check=False, env=env).returncode


@pytest.mark.parametrize(
    "args",
    [
        pytest.param("unrouted", id="word_alone"),
        pytest.param("unrouted /abs/x.diff", id="word_then_space"),
        pytest.param("unrouted\t/abs/x.diff", id="word_then_tab"),
        pytest.param("unrouted\n/abs/x.diff", id="word_then_newline"),
        pytest.param("unrouted\r/abs/x.diff", id="word_then_carriage_return"),
        pytest.param("unrouted ", id="word_then_trailing_space"),
        pytest.param("  unrouted", id="leading_spaces"),
        pytest.param("\tunrouted", id="leading_tab"),
        pytest.param("\nunrouted", id="leading_newline"),
        pytest.param("\r\n unrouted /abs/x.diff", id="leading_mixed_whitespace"),
    ],
)
def test_args_lead_word_is_matches_the_word_alone_or_before_whitespace(args: str) -> None:
    assert _args_lead_word_is(args, "unrouted") == 0


@pytest.mark.parametrize(
    "args",
    [
        pytest.param("unroutedly", id="word_boundary_extension"),
        pytest.param("unrouted:", id="colon_boundary"),
        pytest.param("unrouted,", id="comma_boundary"),
        pytest.param("Unrouted", id="capitalized"),
        pytest.param("UNROUTED /abs/x.diff", id="uppercase"),
        pytest.param("please run unrouted", id="prose_before_word"),
        pytest.param("please run unrouted /abs/x.diff", id="prose_before_word_and_path"),
        pytest.param('"unrouted"', id="double_quoted"),
        pytest.param("'unrouted'", id="single_quoted"),
        pytest.param("`unrouted`", id="backtick_wrapped"),
        pytest.param("", id="empty_args"),
        pytest.param("   ", id="only_spaces"),
        pytest.param("\t\n\r", id="only_other_whitespace"),
    ],
)
def test_args_lead_word_is_rejects_everything_but_the_exact_lead_word(args: str) -> None:
    assert _args_lead_word_is(args, "unrouted") == 1


def test_args_lead_word_is_rejects_an_empty_word() -> None:
    assert _args_lead_word_is("", "") == 1


def _locale_where_ideographic_space_is_space() -> str:
    """An installed UTF-8 locale under which an unpinned `[[:space:]]` matches
    U+3000, the exposure `local LC_ALL=C` removes. `C.UTF-8` is not guaranteed
    to match, so a test that hardcodes it cannot tell the pinned helper from an
    unpinned one."""
    return installed_locale_where(
        '[[ "$1" == [[:space:]] ]]', "\u3000", absent_message="no installed locale makes [[:space:]] match U+3000"
    )


@pytest.mark.parametrize(
    "separator",
    [
        # Discriminates only on a libc that counts U+00A0 as space; glibc does not.
        pytest.param("\u00a0", id="no_break_space"),
        pytest.param("\u2003", id="em_space"),
        pytest.param("\u3000", id="ideographic_space"),
    ],
)
@pytest.mark.parametrize(
    "build_args",
    [
        pytest.param(lambda separator: f"{separator}unrouted", id="before_word"),
        pytest.param(lambda separator: f"unrouted{separator}/abs/x.diff", id="after_word"),
    ],
)
def test_args_lead_word_is_ignores_non_ascii_whitespace_in_a_utf8_locale(separator: str, build_args) -> None:
    locale = _locale_where_ideographic_space_is_space()
    args = build_args(separator)
    assert _args_lead_word_is(args, "unrouted", env_overrides={"LC_ALL": locale}) == 1


def test_args_lead_word_is_leaves_the_caller_locale_unchanged() -> None:
    """The helper's LC_ALL=C is function-local, so it must not leak into the
    caller's own shell."""
    locale = _locale_where_ideographic_space_is_space()
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; export LC_ALL={locale}; '
            '_lib_args_lead_word_is x unrouted; printf %s "$LC_ALL"; [[ "$1" == [[:space:]] ]]',
            "bash",
            "\u3000",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout == locale
    assert result.returncode == 0, "U+3000 no longer matches [[:space:]] in the caller after the helper ran"


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("_lib_args_lead_word_is", id="no_arguments"),
        pytest.param('_lib_args_lead_word_is "unrouted"', id="args_but_no_word"),
    ],
)
def test_args_lead_word_is_with_missing_arguments_under_set_u_returns_one(call: str) -> None:
    """`${n:-}` keeps the helper callable with positionals unset under a
    caller's `set -u`, returning 1 rather than aborting on an unbound variable."""
    argv = ["bash", "-c", f'. {_LIB_SH}; set -u; {call}; echo "status=$?"']
    # LC_ALL=C keeps bash's error text in English so the stderr assertion is meaningful.
    env = {**os.environ, "LC_ALL": "C"}
    result = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "status=1"
    assert "unbound variable" not in result.stderr


# --- _lib_is_no_gate_release_agent ---------------------------------------


def _is_no_gate_release_agent(agent_type: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_is_no_gate_release_agent "$1"', "bash", agent_type],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _no_gate_release_agents() -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_no_gate_release_agents"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_no_gate_release_set_covers_every_review_only_agent() -> None:
    """The set is a superset of the review-only roster, by derivation not by copy."""
    review_only = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_review_only_agents"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert review_only, "review-only roster must not be empty"
    assert set(review_only) <= set(_no_gate_release_agents())


def test_no_gate_release_set_includes_code_writer() -> None:
    """code-writer is an implementer, so it is absent from the review-only roster —
    but it is exactly the identity that can release a gate it could not have
    earned, so the no-release set must name it separately."""
    assert "code-writer" in _no_gate_release_agents()


def _plan_writer_agents() -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_plan_writer_agents"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _is_plan_writer_agent(agent_type: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_is_plan_writer_agent "$1"', "bash", agent_type],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def test_no_gate_release_set_covers_every_plan_writer_agent() -> None:
    """The set is a superset of the plan-writer registry, by derivation not by copy."""
    plan_writers = _plan_writer_agents()
    assert plan_writers, "plan-writer registry must not be empty"
    assert set(plan_writers) <= set(_no_gate_release_agents())


def test_plan_writer_predicate_accepts_every_registry_member() -> None:
    for agent_type in _plan_writer_agents():
        assert _is_plan_writer_agent(agent_type), agent_type


@pytest.mark.parametrize(
    "agent_type",
    ["", "plan-architect-x", "PLAN-ARCHITECT", "plan_architect", "code-writer", "general-purpose"],
)
def test_plan_writer_predicate_rejects_non_members(agent_type: str) -> None:
    """Exact match only; an empty agent_type is the main session and must not match."""
    assert not _is_plan_writer_agent(agent_type)


@pytest.mark.parametrize(
    "agent_type",
    ["code-writer", "plan-architect", "staff-sdet", "ciso-reviewer", "Explore", "Plan"],
)
def test_no_gate_release_agent_matches_roster_members(agent_type: str) -> None:
    assert _is_no_gate_release_agent(agent_type)


@pytest.mark.parametrize(
    "agent_type",
    ["general-purpose", "claude", "", "code-write", "code-writer-x", "CODE-WRITER"],
)
def test_no_gate_release_agent_rejects_non_members(agent_type: str) -> None:
    """Exact match only.

    Empty agent_type is the main session and must pass. general-purpose and
    claude carry the full tool set and can genuinely run a review skill, so
    they keep the documented delegation escape hatch. The near-miss strings
    pin that the predicate is not doing prefix or case-insensitive matching.
    """
    assert not _is_no_gate_release_agent(agent_type)


def _reviewer_persona_agents() -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_reviewer_persona_agents"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_reviewer_persona_set_is_review_only_roster_minus_harness_builtins() -> None:
    """The set is the review-only roster minus Explore and Plan, by derivation not by copy.

    Catches the shell-side `Explore | Plan) continue` exclusion drifting from
    this literal. A new harness built-in added to the review-only roster with
    the exclusion left untouched passes, since the name lands on both sides.
    """
    review_only = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_review_only_agents"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert review_only, "review-only roster must not be empty"
    assert set(_reviewer_persona_agents()) == set(review_only) - {"Explore", "Plan"}


def _is_reviewer_persona(agent_type: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_is_reviewer_persona "$1"', "bash", agent_type],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def test_is_reviewer_persona_accepts_every_member_of_the_derived_roster() -> None:
    roster = _reviewer_persona_agents()
    assert roster, "reviewer-persona roster must not be empty"
    rejected = [agent_type for agent_type in roster if not _is_reviewer_persona(agent_type)]
    assert not rejected, f"roster members rejected by _lib_is_reviewer_persona: {rejected}"


@pytest.mark.parametrize(
    "agent_type",
    [
        "Explore",
        "Plan",
        "code-writer",
        "plan-architect",
        "general-purpose",
        "",
        "staff-sdet-x",
        "staff-sde",
        "STAFF-SDET",
        "ciso-reviewer comment-discipline-reviewer",
    ],
)
def test_is_reviewer_persona_rejects_agents_that_are_not_reviewer_personas_and_absent_type(
    agent_type: str,
) -> None:
    """Every agent outside the reviewer-persona array is rejected.

    Explore and Plan are review-only roster members but harness built-ins.
    code-writer is an implementer. plan-architect is a design consultant that a
    caller branches on separately. general-purpose is a harness built-in outside
    the review-only roster. The empty case is a dispatch payload with no
    subagent_type. The staff-sdet variants pin that the predicate is not doing
    prefix or case-insensitive matching. The space-joined pair of two real
    roster members pins that a value is not accepted as a substring or word
    list of the roster.
    """
    assert not _is_reviewer_persona(agent_type)


# --- _lib_list_contains ----------------------------------------------------
#
# Shared by the roster-membership predicates (_lib_is_review_only_agent,
# _lib_is_no_gate_release_agent, _lib_is_reviewer_persona,
# _lib_is_plan_writer_agent); the helper uses _lib_words_start_with's
# flatten-as-positional-args idiom.


def _list_contains(value: str, items: tuple[str, ...]) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_list_contains "$@"', "bash", value, *items],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class TestListContains:
    def test_empty_list_does_not_crash_under_set_dash_u(self) -> None:
        """Zero ITEM args (an empty array flattened via "${arr[@]}") must
        reach the "$@" loop safely rather than aborting on an unbound
        variable."""
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'set -uo pipefail; . {_LIB_SH}; _lib_list_contains "$@"',
                "bash",
                "anything",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "unbound variable" not in result.stderr

    def test_single_item_list_matches(self) -> None:
        assert _list_contains("staff-sdet", ("staff-sdet",))

    def test_single_item_list_rejects_non_match(self) -> None:
        assert not _list_contains("staff-sdet", ("ciso-reviewer",))

    def test_value_matching_more_than_one_item_still_found(self) -> None:
        """No dedup required -- VALUE need only equal one ITEM among several
        equal ones for the scan to report a match."""
        assert _list_contains("staff-sdet", ("staff-sdet", "staff-sdet", "ciso-reviewer"))

    def test_value_matching_later_position_still_found(self) -> None:
        """Pins the loop-continuation branch directly: a match past the
        first ITEM must still be found, not just one at index 0."""
        assert _list_contains("c", ("a", "b", "c"))

    def test_empty_string_value_matches_empty_string_item(self) -> None:
        assert _list_contains("", ("a", "", "b"))

    def test_empty_string_value_does_not_match_list_without_it(self) -> None:
        assert not _list_contains("", ("a", "b"))

    def test_glob_metacharacter_item_does_not_spuriously_match(self) -> None:
        """The call sites rely on [ "$a" = "$b" ] string equality, not
        a case glob match -- an ITEM shaped like a glob pattern must match
        only that literal string, never a value it would otherwise
        glob-match."""
        assert not _list_contains("code-writer", ("code*",))

    def test_glob_metacharacter_item_matches_its_own_literal_value(self) -> None:
        assert _list_contains("code*", ("code*",))
