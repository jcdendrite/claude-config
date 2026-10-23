"""Tests for transcript_analysis/turn_shape.py: the mutating-git classifier, the call-count and streak buckets,
per-session turn extraction and streaks, and the turn-shape, turn-shape-samples, and turn-shape-holdout-samples
commands end to end.
"""
import pytest
from transcript_analysis import corpus, pricing, render, scope, turn_shape

from .conftest import _asst, _bash_use, _priced, _table_cols, _write_jsonl


def _turn_shape_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
    })()


def _turn_shape_samples_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    sample: int = 100,
    seed: int | None = 42,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "sample": sample,
        "seed": seed,
    })()


def _turn_shape_holdout_samples_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    sample: int = 30,
    seed: int = 0,
    offset: int = 0,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "sample": sample,
        "seed": seed,
        "offset": offset,
    })()


class TestBashCommandIsMutatingGit:
    """Direct unit tests for _bash_command_is_mutating_git — the delegation-rule
    streak's mutating-git exclusion classifier."""

    @pytest.mark.parametrize("subcommand", sorted(turn_shape._TURN_SHAPE_MUTATING_GIT_SUBCOMMANDS))
    def test_every_enumerated_mutating_subcommand_classifies_as_mutating(self, subcommand):
        assert turn_shape._bash_command_is_mutating_git(f"git {subcommand} extra-arg") is True

    @pytest.mark.parametrize(("command", "expected"), [
        ("git status", False),
        ("git log", False),
        ("ls -la", False),
        # Existing normalization (sudo-strip, env-prefix-strip, flag-value-drop)
        # must keep classifying these as mutating once the segment-split refactor lands.
        ("sudo git commit -m x", True),
        ("FOO=bar git commit -m x", True),
        ("git -C /some/path commit -m x", True),
    ])
    def test_read_only_and_normalization_edge_cases(self, command, expected):
        assert turn_shape._bash_command_is_mutating_git(command) is expected

    def test_mutating_git_after_shell_and_operator_still_classifies_as_mutating(self):
        """"cd worktree && git commit -m wip" — the mutating git call isn't at
        shlex.split's index 0/1 of the whole command, so a first-segment-only
        check would miss it."""
        assert turn_shape._bash_command_is_mutating_git("cd worktree && git commit -m wip") is True

    def test_read_only_git_before_shell_and_operator_does_not_classify_as_mutating(self):
        assert turn_shape._bash_command_is_mutating_git("git status && echo done") is False

    def test_mutating_git_only_in_later_chained_segment_still_classifies_as_mutating(self):
        """A segment where only a later chained command is mutating still trips
        the classifier — every segment is checked, not just the first."""
        assert turn_shape._bash_command_is_mutating_git("echo hi && git commit -m x") is True

    def test_env_prefix_on_a_later_chained_segment_still_classifies_as_mutating(self):
        """"cd dir && FOO=bar git commit" — the env-prefix strip must apply
        per segment, not only once at the start of the whole command, or a
        chained segment's own env-var assignment hides its mutating git call."""
        assert turn_shape._bash_command_is_mutating_git("cd dir && FOO=bar git commit -m x") is True


class TestTurnShapeBuckets:
    """Boundary tests for the call-count and streak-length bucket ladders."""

    @pytest.mark.parametrize(("call_count", "expected_bucket"), [
        (3, "2-3"), (4, "4-7"), (7, "4-7"), (8, "8+"),
    ])
    def test_call_count_bucket_boundaries(self, call_count, expected_bucket):
        assert turn_shape._turn_shape_call_count_bucket(call_count) == expected_bucket

    @pytest.mark.parametrize(("streak_len", "expected_bucket"), [
        (2, "2"), (3, "3-5"), (5, "3-5"), (6, "6-10"), (10, "6-10"), (11, "11+"),
    ])
    def test_streak_bucket_boundaries(self, streak_len, expected_bucket):
        assert turn_shape._turn_shape_streak_bucket(streak_len) == expected_bucket


class TestTurnShapeSessionTurns:
    """Direct unit tests for _turn_shape_session_turns — the per-turn population
    builder shared by cmd_turn_shape and cmd_turn_shape_samples."""

    def test_same_request_id_records_merge_into_one_turn_with_summed_call_count(self):
        """Three tool_use blocks split across three same-requestId records merge into
        one turn with call_count=3 — the fixture uses separate records sharing one
        requestId, not three blocks in a single record, so the dedup merge path is
        actually exercised."""
        records = [
            _priced("claude-sonnet-5", input=10, output=5, request_id="req-1",
                     content=[_bash_use("b1", "ls")]),
            _priced("claude-sonnet-5", input=10, output=5, request_id="req-1",
                     content=[_bash_use("b2", "pwd")]),
            _priced("claude-sonnet-5", input=10, output=5, request_id="req-1",
                     content=[_bash_use("b3", "whoami")]),
        ]
        turns = turn_shape._turn_shape_session_turns(records, None, "sess")
        assert len(turns) == 1
        assert turns[0]["call_count"] == 3

    def test_dollar_field_matches_price_turn_for_the_turns_own_usage(self):
        """A turn's own "dollars" field equals _price_turn's own price for that
        turn's exact usage."""
        rec = _priced("claude-sonnet-5", input=1000, output=500, cache_read=200,
                        content=[_bash_use("b1", "ls")])
        expected_dollars_by_class, _, _ = pricing._price_turn("claude-sonnet-5", rec["message"]["usage"])
        turns = turn_shape._turn_shape_session_turns([rec], None, "sess")
        assert turns[0]["dollars"] == sum(expected_dollars_by_class.values())

    def test_zero_tool_call_turn_has_call_count_zero(self):
        """A turn with no tool_use blocks (a text-only reply) has call_count 0, which
        _turn_shape_call_count_bucket maps to bucket "0", not "1"."""
        rec = _priced("claude-sonnet-5", input=10, output=5,
                        content=[{"type": "text", "text": "just a reply"}])
        turns = turn_shape._turn_shape_session_turns([rec], None, "sess")
        assert turns[0]["call_count"] == 0
        assert turn_shape._turn_shape_call_count_bucket(turns[0]["call_count"]) == "0"

    def test_sidechain_turns_excluded_entirely(self):
        """A session made entirely of isSidechain turns yields no entries at all,
        regardless of how many tool_use blocks it carries or how they'd otherwise
        bucket."""
        rec = _asst("claude-sonnet-5", sidechain=True, content=[_bash_use("b1", "git status")],
                     request_id="req-1")
        rec["message"]["usage"] = {
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }
        turns = turn_shape._turn_shape_session_turns([rec], None, "sess")
        assert turns == []

    def test_null_git_branch_normalizes_the_same_as_empty_string(self):
        """A record whose gitBranch is JSON null must normalize to the same "branch"
        value as one whose gitBranch is "" — regression test for the
        rec.get("gitBranch", "") vs rec.get("gitBranch") or "" divergence, which
        only shows up when the key is present with an explicit null."""
        rec1 = _priced("claude-sonnet-5", input=10, output=5, branch="",
                         content=[_bash_use("b1", "ls")], request_id="r1")
        rec2 = _priced("claude-sonnet-5", input=10, output=5, branch="",
                         content=[_bash_use("b2", "pwd")], request_id="r2")
        rec2["gitBranch"] = None
        turns = turn_shape._turn_shape_session_turns([rec1, rec2], None, "sess")
        assert turns[0]["branch"] == turns[1]["branch"] == ""
        streaks = turn_shape._turn_shape_streaks(turns, require_bash=False)
        assert [len(s) for s in streaks] == [2]

    def test_since_ts_excludes_turns_before_cutoff_and_turns_with_no_timestamp(self):
        """since_ts drops a turn timestamped before the cutoff and a turn with no
        "timestamp" key at all (_parse_ts returns None for a missing timestamp,
        which must not compare as in-scope)."""
        before_cutoff = _priced("claude-sonnet-5", ts="2026-01-01T00:00:00.000Z", input=10, output=5,
                                  content=[_bash_use("b1", "ls")], request_id="r1")
        after_cutoff = _priced("claude-sonnet-5", ts="2026-06-01T00:00:00.000Z", input=10, output=5,
                                 content=[_bash_use("b2", "pwd")], request_id="r2")
        no_timestamp = _priced("claude-sonnet-5", ts="", input=10, output=5,
                                 content=[_bash_use("b3", "whoami")], request_id="r3")
        cutoff = corpus._parse_ts("2026-03-01T00:00:00.000Z")
        turns = turn_shape._turn_shape_session_turns([before_cutoff, after_cutoff, no_timestamp], cutoff, "sess")
        assert [t["command"] for t in turns] == ["pwd"]


class TestTurnShapeStreaks:
    """Direct unit tests for _turn_shape_streaks — the batching- and
    delegation-rule streak builder."""

    def test_gitbranch_change_ends_the_current_streak(self):
        """Two single-call turns on branch A followed by two on branch B produce two
        separate length-2 streaks, not one length-4 streak."""
        records = [
            _priced("claude-sonnet-5", input=10, output=5, branch="main",
                     content=[_bash_use("b1", "git status")], request_id="r1"),
            _priced("claude-sonnet-5", input=10, output=5, branch="main",
                     content=[_bash_use("b2", "git status")], request_id="r2"),
            _priced("claude-sonnet-5", input=10, output=5, branch="feature",
                     content=[_bash_use("b3", "git status")], request_id="r3"),
            _priced("claude-sonnet-5", input=10, output=5, branch="feature",
                     content=[_bash_use("b4", "git status")], request_id="r4"),
        ]
        turns = turn_shape._turn_shape_session_turns(records, None, "sess")
        streaks = turn_shape._turn_shape_streaks(turns, require_bash=False)
        assert [len(s) for s in streaks] == [2, 2]

    def test_mutating_git_breaks_delegation_streak_but_not_batching_streak(self):
        """git commit is in the mutating-git set, so it does not extend the
        delegation streak, but still extends the batching streak; git status
        extends both."""
        records = [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git status")], request_id="r1"),
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b2", "git commit -m msg")], request_id="r2"),
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b3", "git status")], request_id="r3"),
        ]
        turns = turn_shape._turn_shape_session_turns(records, None, "sess")
        batching_streaks = turn_shape._turn_shape_streaks(turns, require_bash=False)
        delegation_streaks = turn_shape._turn_shape_streaks(turns, require_bash=True)
        # Batching streak: all three turns qualify regardless of Bash subcommand.
        assert [len(s) for s in batching_streaks] == [3]
        # Delegation streak: git commit breaks it into two length-1 streaks.
        assert [len(s) for s in delegation_streaks] == [1, 1]


class TestCmdTurnShape:
    """Thin integration checks that cmd_turn_shape wires the pure functions above
    into its printed tables and unpriced-turn caveat correctly — branch and
    boundary coverage lives in TestTurnShapeSessionTurns/TestTurnShapeStreaks/
    TestTurnShapeBuckets above, not here."""

    def test_prints_call_count_and_streak_tables_from_the_same_corpus(self, fake_projects, capsys):
        """A 3-call turn lands in the "2-3" call-count bucket with its priced dollar
        total, and the same corpus's two single-call turns form one length-2
        batching streak — confirms cmd_turn_shape's table rendering reads the same
        population/aggregation the pure functions above compute. r3 is a mutating
        git command so the batching and delegation streak tables diverge
        numerically (batching counts both single-call turns; delegation excludes
        r3 and sees only r4) — a fixture where both tables end up identical would
        pass even if cmd_turn_shape swapped which population feeds which table."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5, request_id="req-1",
                     content=[_bash_use("b1", "ls"), _bash_use("b2", "pwd")]),
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b3", "git commit -m x")], request_id="r3"),
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b4", "git log")], request_id="r4"),
        ])
        expected_dollars_by_class, _, _ = pricing._price_turn(
            "claude-sonnet-5",
            {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0},
        )
        expected_turn_dollars = sum(expected_dollars_by_class.values())
        turn_shape.cmd_turn_shape(_turn_shape_args())
        out = capsys.readouterr().out
        call_cols = _table_cols(
            out, header_contains="Bucket", row_contains="2-3", row_startswith=True, occurrence=1,
        )
        assert call_cols["Turns"] == "1"
        assert call_cols["$"] == render._fmt_usd(expected_turn_dollars)
        batching_cols = _table_cols(
            out, header_contains="Bucket", row_contains="2", row_startswith=True, occurrence=2,
        )
        assert batching_cols["Streaks"] == "1"
        assert batching_cols["$"] == render._fmt_usd(expected_turn_dollars * 2)
        # Delegation table (occurrence=3): r3's mutating git commit excludes it,
        # so only r4 qualifies — a length-1 streak, not length-2. "1 " (with a
        # trailing space) disambiguates the "1" bucket row from "11+", which
        # also starts with the character "1".
        delegation_len1_cols = _table_cols(
            out, header_contains="Bucket", row_contains="1 ", row_startswith=True, occurrence=3,
        )
        assert delegation_len1_cols["Streaks"] == "1"
        assert delegation_len1_cols["$"] == render._fmt_usd(expected_turn_dollars)
        delegation_len2_cols = _table_cols(
            out, header_contains="Bucket", row_contains="2", row_startswith=True, occurrence=3,
        )
        assert delegation_len2_cols["Streaks"] == "0"

    def test_prints_unpriced_turn_caveat_beneath_the_tables(self, fake_projects, capsys):
        """A turn priced under an unrecognized model contributes $0.00 silently to
        every bucket unless surfaced via the "(N unpriced turns / M tokens
        excluded from priced spend)" caveat every sibling dollar-weighted
        subcommand prints — claude-opus-4-7 is deliberately unpriced (see
        conftest's _priced_opus docstring)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-4-7", input=100, output=400, cache_read=0,
                     content=[_bash_use("b1", "ls")], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape(_turn_shape_args())
        out = capsys.readouterr().out
        assert "1 unpriced turns / 500 tokens excluded from priced spend" in out


class TestTurnShapeSamples:
    def test_banner_present_and_no_file_written(self, fake_projects, tmp_path, monkeypatch, capsys):
        """--samples output is stamped with the DO NOT PUBLISH banner and
        writes no file into the working directory or the config dir, verified by a
        before/after diff of tmp_path, which holds both."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git status")], request_id="r1"),
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b2", "git log")], request_id="r2"),
        ])
        monkeypatch.chdir(tmp_path)
        before = set(tmp_path.rglob("*"))
        turn_shape.cmd_turn_shape_samples(_turn_shape_samples_args())
        out = capsys.readouterr().out
        after = set(tmp_path.rglob("*"))
        assert out.startswith(scope._DO_NOT_PUBLISH_BANNER)
        assert before == after

    def test_no_exception_on_empty_flagged_population(self, fake_projects, capsys):
        """A corpus with no streak of length >= 2 emits only the banner, with no
        candidates and no exception."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5, content=[
                {"type": "tool_use", "id": "e1", "name": "Edit", "input": {}},
                {"type": "tool_use", "id": "e2", "name": "Edit", "input": {}},
            ], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_samples(_turn_shape_samples_args())
        out = capsys.readouterr().out
        assert out.strip() == scope._DO_NOT_PUBLISH_BANNER

    def test_candidate_body_renders_rule_length_dollars_session_and_turn_detail(self, fake_projects, capsys):
        """The rendered candidate body — the exact text a calibration rater reads —
        carries the rule name, streak length, dollar total, session id, and one
        "N. tool_name: command" line per turn, for a hand-computed 2-turn streak.
        Both turns invoke mutating git, so only a "batching" candidate forms (the
        delegation streak excludes mutating git entirely), keeping the assertion
        unambiguous."""
        rec1 = _priced("claude-sonnet-5", input=10, output=5,
                         content=[_bash_use("b1", "git commit -m a")], request_id="r1")
        rec2 = _priced("claude-sonnet-5", input=10, output=5,
                         content=[_bash_use("b2", "git commit -m b")], request_id="r2")
        _write_jsonl(fake_projects / "sess.jsonl", [rec1, rec2])
        expected_dollars_by_class, _, _ = pricing._price_turn("claude-sonnet-5", rec1["message"]["usage"])
        expected_total = render._fmt_usd(sum(expected_dollars_by_class.values()) * 2)
        turn_shape.cmd_turn_shape_samples(_turn_shape_samples_args())
        out = capsys.readouterr().out
        assert f"--- batching streak, length=2, {expected_total}, session=sess ---" in out
        assert "1. Bash: git commit -m a" in out
        assert "2. Bash: git commit -m b" in out
        assert "delegation streak" not in out


class TestTurnShapeHoldoutSamples:
    """Unit tests for cmd_turn_shape_holdout_samples — the unflagged (length-1
    streak) complement of TestTurnShapeSamples above."""

    def test_banner_present_and_no_file_written(self, fake_projects, tmp_path, monkeypatch, capsys):
        """--samples output is stamped with the DO NOT PUBLISH banner and
        writes no file into the working directory or the config dir, verified by a
        before/after diff of tmp_path, which holds both."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m a")], request_id="r1"),
        ])
        monkeypatch.chdir(tmp_path)
        before = set(tmp_path.rglob("*"))
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args())
        out = capsys.readouterr().out
        after = set(tmp_path.rglob("*"))
        assert out.startswith(scope._DO_NOT_PUBLISH_BANNER)
        assert before == after

    def test_no_exception_on_empty_holdout_population(self, fake_projects, capsys):
        """A corpus with no streak of length == 1 (only a length-2 call turn) emits
        only the banner, with no candidates and no exception."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5, content=[
                {"type": "tool_use", "id": "e1", "name": "Edit", "input": {}},
                {"type": "tool_use", "id": "e2", "name": "Edit", "input": {}},
            ], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args())
        out = capsys.readouterr().out
        assert out.strip() == scope._DO_NOT_PUBLISH_BANNER

    def test_candidate_body_renders_rule_length_dollars_session_and_turn_detail(self, fake_projects, capsys):
        """The rendered candidate body carries the rule name, streak length,
        dollar total, session id, and one "N. tool_name: command" line, for a
        hand-computed length-1 streak. The turn invokes mutating git, so only a
        "batching" candidate forms (mutating git never qualifies for the
        delegation streak), keeping the assertion unambiguous."""
        rec = _priced("claude-sonnet-5", input=10, output=5,
                        content=[_bash_use("b1", "git commit -m a")], request_id="r1")
        _write_jsonl(fake_projects / "sess.jsonl", [rec])
        expected_dollars_by_class, _, _ = pricing._price_turn("claude-sonnet-5", rec["message"]["usage"])
        expected_total = render._fmt_usd(sum(expected_dollars_by_class.values()))
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args())
        out = capsys.readouterr().out
        assert f"--- batching streak, length=1, {expected_total}, session=sess ---" in out
        assert "1. Bash: git commit -m a" in out
        assert "delegation streak" not in out

    def test_length_2_streak_excluded_from_holdout_population(self, fake_projects, capsys):
        """A length-2 streak is the exact complement of the holdout population
        (length == 1, not a range) — it must not appear here even though it
        would appear in turn-shape-samples's flagged population."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m a")], request_id="r1"),
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b2", "git commit -m b")], request_id="r2"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args())
        out = capsys.readouterr().out
        assert out.strip() == scope._DO_NOT_PUBLISH_BANNER

    def test_offset_pages_the_shuffled_population_without_overlap(self, fake_projects, capsys):
        """Same seed, offset=0/sample=1 and offset=1/sample=1 yield two distinct
        sessions, and offset=0/sample=2 yields both — --offset pages a
        deterministic shuffle rather than re-shuffling per invocation."""
        _write_jsonl(fake_projects / "s1.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m a")], request_id="r1"),
        ])
        _write_jsonl(fake_projects / "s2.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m b")], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=0, sample=1))
        first_page = capsys.readouterr().out
        assert first_page.count("--- batching streak") == 1
        assert ("session=s1" in first_page) != ("session=s2" in first_page)

        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=1, sample=1))
        second_page = capsys.readouterr().out
        assert second_page.count("--- batching streak") == 1
        assert ("session=s1" in second_page) != ("session=s2" in second_page)
        assert ("session=s1" in first_page) != ("session=s1" in second_page)

        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=0, sample=2))
        both_page = capsys.readouterr().out
        assert both_page.count("--- batching streak") == 2
        assert "session=s1" in both_page
        assert "session=s2" in both_page

    def test_offset_past_end_of_nonempty_population_emits_banner_and_diagnostic(self, fake_projects, capsys):
        """offset=5 against 2 candidates emits the banner and a stderr diagnostic
        distinguishing "paged past the end" from a genuinely empty population,
        with no exception."""
        _write_jsonl(fake_projects / "s1.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m a")], request_id="r1"),
        ])
        _write_jsonl(fake_projects / "s2.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m b")], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=5, sample=30))
        out, err = capsys.readouterr()
        assert out.strip() == scope._DO_NOT_PUBLISH_BANNER
        assert "--offset=5 is past the end of the 2 unflagged candidates" in err

    def test_offset_equals_total_candidates_triggers_past_end_diagnostic(self, fake_projects, capsys):
        """offset == total_candidates exactly (not just past it) must still
        trigger the past-end diagnostic — the boundary the `>=` check exists
        to get right, since a rater paging with a fixed --sample stride lands
        here naturally on the page after the last full one."""
        _write_jsonl(fake_projects / "s1.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m a")], request_id="r1"),
        ])
        _write_jsonl(fake_projects / "s2.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m b")], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=2, sample=30))
        out, err = capsys.readouterr()
        assert out.strip() == scope._DO_NOT_PUBLISH_BANNER
        assert "--offset=2 is past the end of the 2 unflagged candidates" in err

    def test_offset_partial_final_page_returns_exactly_the_remainder(self, fake_projects, capsys):
        """3 candidates, offset=2/sample=2 returns exactly 1 candidate — the
        boundary case the offset-pagination logic exists to get right."""
        for i in range(3):
            _write_jsonl(fake_projects / f"s{i}.jsonl", [
                _priced("claude-sonnet-5", input=10, output=5,
                         content=[_bash_use("b1", f"git commit -m {i}")], request_id="r1"),
            ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=2, sample=2))
        out = capsys.readouterr().out
        assert out.count("--- batching streak") == 1

    def test_negative_offset_exits_2_with_stderr_message(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=-1))
        assert exc_info.value.code == 2
        assert "--offset must not be negative" in capsys.readouterr().err

    def test_negative_sample_exits_2_with_stderr_message(self, fake_projects, capsys):
        with pytest.raises(SystemExit) as exc_info:
            turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(sample=-1))
        assert exc_info.value.code == 2
        assert "--sample must not be negative" in capsys.readouterr().err

    def test_sample_zero_returns_no_candidates_without_past_end_diagnostic(self, fake_projects, capsys):
        """--sample 0 against a non-empty, non-past-end offset must not trip the
        "offset past the end" diagnostic — that message means the offset itself
        exceeds the population, not that the caller asked for a zero-length page."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git commit -m a")], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args(offset=0, sample=0))
        out, err = capsys.readouterr()
        assert out.strip() == scope._DO_NOT_PUBLISH_BANNER
        assert "is past the end" not in err
        assert "(offset=0, window=0 of 1 unflagged candidates)" in err

    def test_isolated_non_mutating_bash_turn_renders_as_both_batching_and_delegation(
        self, fake_projects, capsys
    ):
        """A single isolated, non-mutating-git Bash turn qualifies as a length-1
        streak under both require_bash=False and require_bash=True, so it
        renders as two candidates — one "batching", one "delegation" — for the
        same session/turn. A rater who sees this pair must recognize it as one
        repeated run, not two independent violations toward the sizing tally."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=10, output=5,
                     content=[_bash_use("b1", "git log")], request_id="r1"),
        ])
        turn_shape.cmd_turn_shape_holdout_samples(_turn_shape_holdout_samples_args())
        out = capsys.readouterr().out
        assert "--- batching streak, length=1" in out
        assert "--- delegation streak, length=1" in out
        assert out.count("session=sess") == 2
