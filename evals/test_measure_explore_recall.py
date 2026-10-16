"""Unit tests for evals/measure_explore_recall.py.

Fixture-based, mirroring test_measure_subagent_model_resolution.py's style
(`_write_subagent_jsonl`-shaped helpers) and never launching a live `claude`
subprocess — every test that would otherwise hit the launcher injects a fake
one instead. Lives beside the harness rather than in claude/.claude/tests/
for the same reason test_measure_subagent_model_resolution.py does: that
directory is stowed to every consumer of this repo, and this harness imports
a module that is never stowed.

pyproject.toml adds 'evals' to the pytest pythonpath so `import
measure_explore_recall` resolves correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import measure_explore_recall as m
import measure_subagent_model_resolution as msmr
import pytest
from test_review_bench_mining import _commit, _git, _init_repo, _write

# --- Fixture-data helpers ------------------------------------------------------


def _stratum_a_case(index: int, n_sites: int) -> dict:
    return {
        "id": f"a{index}", "stratum": "A", "question": f"question {index}?",
        "grep_pattern": f"pattern{index}", "grep_mode": "literal",
        "key": [f"path/to/file_{index}_{site}.py" for site in range(n_sites)],
    }


def _stratum_b_case(index: int, n_sites: int, *, second_pass_confirmed: bool = True) -> dict:
    case: dict = {
        "id": f"b{index}", "stratum": "B", "question": f"question {index}?",
        "key": [f"path/to/file_{index}.py:symbol_{site}" for site in range(n_sites)],
        "accepted_near_misses": [],
    }
    if second_pass_confirmed:
        case["second_pass_confirmed"] = True
    return case


def _valid_fixture_data() -> dict:
    """Exactly clears the fixture floors: 8 Stratum A cases (5 with >=5
    sites), 5 Stratum B cases (3 with >=5 sites)."""
    stratum_a = [_stratum_a_case(i, 5 if i < 5 else 1) for i in range(8)]
    stratum_b = [_stratum_b_case(i, 5 if i < 3 else 1) for i in range(5)]
    return {"pinned_commit": "abc123def456", "cases": stratum_a + stratum_b}


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _text_record(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _tool_use_record(name: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]}}


def _sample_score(recall: float) -> m.SampleScore:
    return m.SampleScore(
        recall=recall, precision=1.0, matched=frozenset(), false_positives=frozenset(),
        near_miss=frozenset(), format_failure=False,
    )


def _wide_site_key(prefix: str, n_sites: int = 20) -> frozenset[str]:
    """20 sites by default. Three such cases pool to n=60, which clears the
    Wilson lower bound's n>=35 floor at zero false positives. That keeps a
    coverage-isolation test's verdict from being confounded by an
    independently insufficient pooled n."""
    return frozenset(f"{prefix}_site_{i}.py:f" for i in range(n_sites))


def _model_aggregate(**overrides: object) -> m.ModelAggregate:
    defaults: dict = {
        "arm": "x", "per_case_recall_mean": {}, "per_case_recall_min": {}, "per_case_recall_max": {},
        "stratum_recall_mean": {}, "pooled_false_positives": 0, "pooled_returned_items": 0,
        "precision_point_estimate": None, "precision_wilson_lower_bound": None, "format_failures": 0,
        "discards": 0, "timeouts": 0, "observed_model_ids": frozenset(),
    }
    defaults.update(overrides)
    return m.ModelAggregate(**defaults)


def _scripted_launcher(families: list[str | None], *, cost: float = 1.0):
    """A fake launcher returning one scripted observed_model_family per
    call, in order, regardless of which arm was requested — for tests that
    exercise the arm-mismatch discard/retry path itself, or that only ever
    launch a single fixed arm. Never a real subprocess."""
    calls: dict[str, int] = {"count": 0}

    def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
        family = families[calls["count"]]
        calls["count"] += 1
        return m.SampleAttempt(
            observed_model_family=family, observed_model_ids=frozenset(), cost_usd=cost,
            score=m.score_sample(("x.py",), key=case.key),
        )

    return launcher, calls


def _matching_family_launcher(*, cost: float = 0.0):
    """A fake launcher that always reports the arm's own expected model
    family, so no sample ever mismatches -- for tests that exercise
    run_batch's happy path across both arms without the discard/retry path
    muddying the call count. Never a real subprocess."""
    calls: dict[str, int] = {"count": 0}

    def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
        calls["count"] += 1
        return m.SampleAttempt(
            observed_model_family=m.ARM_EXPECTED_FAMILY[arm], observed_model_ids=frozenset(), cost_usd=cost,
            score=m.score_sample(("x.py",), key=case.key),
        )

    return launcher, calls


# --- Fixture schema ------------------------------------------------------------


class TestParseFixtureCases:
    def test_stratum_b_case_without_second_pass_confirmed_is_rejected(self) -> None:
        raw = [{"id": "b1", "stratum": "B", "question": "q", "key": ["a.py:f"]}]
        with pytest.raises(m.FixtureSchemaError, match="second_pass_confirmed"):
            m.parse_fixture_cases(raw)

    def test_stratum_b_case_with_second_pass_confirmed_false_is_rejected(self) -> None:
        raw = [{"id": "b1", "stratum": "B", "question": "q", "key": ["a.py:f"], "second_pass_confirmed": False}]
        with pytest.raises(m.FixtureSchemaError, match="second_pass_confirmed"):
            m.parse_fixture_cases(raw)

    def test_stratum_a_case_round_trips_grep_fields(self) -> None:
        raw = [{"id": "a1", "stratum": "A", "question": "q", "grep_pattern": "foo", "grep_mode": "literal", "key": ["a.py"]}]
        cases = m.parse_fixture_cases(raw)
        assert cases[0].grep_pattern == "foo"
        assert cases[0].grep_mode == "literal"
        assert cases[0].key == frozenset({"a.py"})

    def test_unknown_stratum_rejected(self) -> None:
        raw = [{"id": "x1", "stratum": "C", "question": "q", "key": ["a.py"]}]
        with pytest.raises(m.FixtureSchemaError, match="unknown stratum"):
            m.parse_fixture_cases(raw)

    def test_case_missing_key_rejected(self) -> None:
        raw = [{"id": "a1", "stratum": "A", "question": "q", "grep_pattern": "x", "grep_mode": "literal"}]
        with pytest.raises(m.FixtureSchemaError, match="'key'"):
            m.parse_fixture_cases(raw)

    def test_non_list_cases_rejected(self) -> None:
        with pytest.raises(m.FixtureSchemaError, match="must be a JSON array"):
            m.parse_fixture_cases({"id": "a1"})

    def test_non_dict_case_entry_rejected(self) -> None:
        with pytest.raises(m.FixtureSchemaError, match="must be a JSON object"):
            m.parse_fixture_cases(["not-a-dict"])

    def test_case_missing_id_rejected(self) -> None:
        raw = [{"stratum": "A", "question": "q", "grep_pattern": "x", "grep_mode": "literal", "key": ["a.py"]}]
        with pytest.raises(m.FixtureSchemaError, match="'id'"):
            m.parse_fixture_cases(raw)

    def test_case_missing_question_rejected(self) -> None:
        raw = [{"id": "a1", "stratum": "A", "grep_pattern": "x", "grep_mode": "literal", "key": ["a.py"]}]
        with pytest.raises(m.FixtureSchemaError, match="'question'"):
            m.parse_fixture_cases(raw)

    def test_stratum_a_case_missing_grep_pattern_rejected(self) -> None:
        raw = [{"id": "a1", "stratum": "A", "question": "q", "grep_mode": "literal", "key": ["a.py"]}]
        with pytest.raises(m.FixtureSchemaError, match="'grep_pattern'"):
            m.parse_fixture_cases(raw)

    def test_stratum_a_case_missing_grep_mode_rejected(self) -> None:
        raw = [{"id": "a1", "stratum": "A", "question": "q", "grep_pattern": "x", "key": ["a.py"]}]
        with pytest.raises(m.FixtureSchemaError, match="'grep_mode'"):
            m.parse_fixture_cases(raw)


class TestLoadFixture:
    def test_valid_fixture_loads_successfully(self, tmp_path: Path) -> None:
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(_valid_fixture_data()))

        pinned_commit, cases = m.load_fixture(path)

        assert pinned_commit == "abc123def456"
        assert len(cases) == 13

    def test_missing_top_level_key_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        del data["pinned_commit"]
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="pinned_commit"):
            m.load_fixture(path)

    def test_pinned_commit_shorter_than_the_abbreviation_floor_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        data["pinned_commit"] = "abc123"  # 6 hex characters, below the 7-character floor
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="pinned_commit"):
            m.load_fixture(path)

    def test_pinned_commit_with_non_hex_characters_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        data["pinned_commit"] = "not-a-hex-sha"
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="pinned_commit"):
            m.load_fixture(path)

    def test_stratum_b_case_missing_second_pass_confirmed_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        for case in data["cases"]:
            if case["stratum"] == "B":
                del case["second_pass_confirmed"]
                break
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="second_pass_confirmed"):
            m.load_fixture(path)

    def test_too_few_stratum_a_cases_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        data["cases"] = [c for c in data["cases"] if c["stratum"] != "A"] + [_stratum_a_case(0, 5)]
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="Stratum A"):
            m.load_fixture(path)

    def test_too_few_wide_stratum_a_cases_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        # Eight Stratum A cases, but only four clear the >=5-site floor.
        data["cases"] = (
            [c for c in data["cases"] if c["stratum"] != "A"]
            + [_stratum_a_case(i, 5 if i < 4 else 1) for i in range(8)]
        )
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="Stratum A"):
            m.load_fixture(path)

    def test_too_few_stratum_b_cases_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        data["cases"] = [c for c in data["cases"] if c["stratum"] != "B"] + [_stratum_b_case(0, 5)]
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="Stratum B"):
            m.load_fixture(path)

    def test_too_few_wide_stratum_b_cases_rejected(self, tmp_path: Path) -> None:
        data = _valid_fixture_data()
        data["cases"] = [c for c in data["cases"] if c["stratum"] != "B"] + [_stratum_b_case(i, 1) for i in range(5)]
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(data))

        with pytest.raises(m.FixtureSchemaError, match="Stratum B"):
            m.load_fixture(path)


# --- --repo-dir validation ------------------------------------------------------


class TestValidateRepoDir:
    def test_nonexistent_path_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(m.RepoDirValidationError, match="does not exist"):
            m.validate_repo_dir(tmp_path / "missing", "deadbeef")

    def test_a_clean_checkout_at_the_pinned_commit_is_accepted(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "x = 1\n")
        commit = _commit(repo, "initial")

        m.validate_repo_dir(repo, commit)  # must not raise

    def test_a_checkout_whose_head_starts_with_the_abbreviated_pin_is_accepted(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "x = 1\n")
        commit = _commit(repo, "initial")

        m.validate_repo_dir(repo, commit[:12])  # must not raise

    def test_a_checkout_at_a_different_commit_is_rejected(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "x = 1\n")
        _commit(repo, "initial")

        with pytest.raises(m.RepoDirValidationError, match="does not start with the fixture's pinned_commit"):
            m.validate_repo_dir(repo, "0" * 40)

    def test_a_dirty_tree_at_the_pinned_commit_is_rejected(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "x = 1\n")
        commit = _commit(repo, "initial")
        _write(repo, "a.py", "x = 2\n")

        with pytest.raises(m.RepoDirValidationError, match="uncommitted changes"):
            m.validate_repo_dir(repo, commit)

    def test_a_path_that_is_not_a_git_repository_is_rejected(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        with pytest.raises(m.RepoDirValidationError, match="not a git repository"):
            m.validate_repo_dir(not_a_repo, "deadbeef")


# --- Sidecar final-answer reader ----------------------------------------------


class TestScanSubagentFinalAnswerText:
    def test_returns_none_when_file_has_no_assistant_record(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [{"type": "user", "message": {}}])

        assert m._scan_subagent_final_answer_text(path) is None

    def test_concatenates_text_blocks_and_ignores_tool_use(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Hello "},
                {"type": "tool_use", "name": "Read", "input": {}},
                {"type": "text", "text": "world"},
            ]}},
        ])

        assert m._scan_subagent_final_answer_text(path) == "Hello world"

    def test_only_the_last_assistant_record_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [_text_record("first"), _text_record("second")])

        assert m._scan_subagent_final_answer_text(path) == "second"

    def test_last_record_with_only_tool_use_yields_empty_text_not_earlier_text(self, tmp_path: Path) -> None:
        """The last assistant record decides -- its own lack of a text block
        must not fall back to an earlier turn's text."""
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [_text_record("first"), _tool_use_record("Read")])

        assert m._scan_subagent_final_answer_text(path) == ""

    def test_skips_attachment_and_thinking_records_in_a_real_transcript_shape(self, tmp_path: Path) -> None:
        """Reproduces a real subagent sidecar's own record shapes: a leading
        `attachment`-type record (no `message` at all), a `thinking` content
        block alongside `tool_use`, and a `tool_result` record from the
        `user` role -- none of these are the `assistant` text turn the
        reader is after, and must not be mistaken for it."""
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [
            {"type": "user", "message": {"role": "user", "content": "do the search"}},
            {"type": "attachment", "attachment": {"path": "/some/file"}},
            {"type": "assistant", "message": {
                "role": "assistant", "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "thinking", "thinking": "let me check permissions"}],
            }},
            {"type": "assistant", "message": {
                "role": "assistant", "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "tool_use", "name": "Grep", "input": {}}],
            }},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "denied"}]}},
            {"type": "assistant", "message": {
                "role": "assistant", "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": 'denied\n```json\n[]\n```'}],
            }},
        ])

        assert m._scan_subagent_final_answer_text(path) == 'denied\n```json\n[]\n```'

    def test_string_content_is_read_through_to_read_final_answer_like_block_lists(self, tmp_path: Path) -> None:
        """message.content can be a bare string rather than a list of typed
        blocks -- already a handled shape elsewhere in this harness's own
        transcript tooling (transcript-analysis.py, transcript_analysis/
        render.py). Must read through read_final_answer the same as the
        list-of-blocks shape."""
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [
            {"type": "assistant", "message": {"content": 'Done.\n```json\n["a.py", "b.py"]\n```'}},
        ])

        assert m._scan_subagent_final_answer_text(path) == 'Done.\n```json\n["a.py", "b.py"]\n```'
        result = m.read_final_answer(path)
        assert result.format_failure is False
        assert result.items == ("a.py", "b.py")


class TestLastFencedJsonBlock:
    def test_no_block_returns_none(self) -> None:
        assert m._last_fenced_json_block("just some prose, no block here") is None

    def test_extracts_block_with_prose_after_it(self) -> None:
        text = 'Here is my answer:\n```json\n["a.py", "b.py"]\n```\nLet me know if you need more.'
        assert m._last_fenced_json_block(text) == '["a.py", "b.py"]'

    def test_last_of_multiple_blocks_wins(self) -> None:
        text = '```json\n["first.py"]\n```\nSome more thinking.\n```json\n["second.py"]\n```'
        assert m._last_fenced_json_block(text) == '["second.py"]'


class TestParseAnswerArray:
    def test_valid_array_of_strings(self) -> None:
        assert m.parse_answer_array('["a.py", "b.py"]') == ["a.py", "b.py"]

    def test_non_array_json_rejected(self) -> None:
        assert m.parse_answer_array('{"a": 1}') is None

    def test_array_with_non_string_items_rejected(self) -> None:
        assert m.parse_answer_array('["a.py", 1]') is None

    def test_invalid_json_rejected(self) -> None:
        assert m.parse_answer_array("not json") is None


class TestReadFinalAnswer:
    def test_no_block_is_a_format_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [_text_record("no block of any kind here")])

        result = m.read_final_answer(path)

        assert result.format_failure is True
        assert result.items is None

    def test_prose_after_the_block_still_parses(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [_text_record('Done.\n```json\n["a.py", "b.py"]\n```\nLet me know if that helps.')])

        result = m.read_final_answer(path)

        assert result.format_failure is False
        assert result.items == ("a.py", "b.py")

    def test_block_valid_json_but_not_array_of_strings_is_a_format_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [_text_record('```json\n{"a": "b.py"}\n```')])

        result = m.read_final_answer(path)

        assert result.format_failure is True
        assert result.items is None

    def test_well_formed_array_is_normalized_and_deduped(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [_text_record('```json\n["./a.py", "a.py", "b//c.py"]\n```')])

        result = m.read_final_answer(path)

        assert result.format_failure is False
        assert result.items == ("a.py", "b/c.py")

    def test_no_assistant_record_at_all_is_a_format_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write_jsonl(path, [{"type": "user", "message": {}}])

        result = m.read_final_answer(path)

        assert result.format_failure is True
        assert result.items is None


class TestNormalizeEntry:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("./foo/bar.py", "foo/bar.py"),
        ("foo//bar.py", "foo/bar.py"),
        ("  foo/bar.py  ", "foo/bar.py"),
        ("foo/bar.py", "foo/bar.py"),
    ])
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert m.normalize_entry(raw) == expected

    def test_strips_a_leading_absolute_repo_dir_prefix(self) -> None:
        assert m.normalize_entry("/repo/foo/bar.py", repo_dir=Path("/repo")) == "foo/bar.py"

    def test_leaves_a_relative_path_unchanged_when_repo_dir_given(self) -> None:
        assert m.normalize_entry("foo/bar.py", repo_dir=Path("/repo")) == "foo/bar.py"

    def test_does_not_strip_a_path_under_a_differently_named_sibling_directory(self) -> None:
        """repo_dir="/repo" must not match "/repository/..." -- the stripped
        prefix is always slash-terminated so a same-prefix sibling directory
        name doesn't get mistaken for repo_dir itself."""
        assert m.normalize_entry("/repository/foo.py", repo_dir=Path("/repo")) == "/repository/foo.py"


class TestNormalizeAndDedup:
    def test_dedups_equivalent_entries_after_normalization(self) -> None:
        assert m.normalize_and_dedup(["./foo.py", "foo.py", "bar.py"]) == ("foo.py", "bar.py")

    def test_preserves_first_seen_order(self) -> None:
        assert m.normalize_and_dedup(["b.py", "a.py", "b.py"]) == ("b.py", "a.py")

    def test_threads_repo_dir_into_each_entrys_normalization(self) -> None:
        """An absolute-path answer and a relative-path answer for the same
        site must dedup to one entry once repo_dir strips the prefix."""
        result = m.normalize_and_dedup(["/repo/foo.py", "foo.py"], repo_dir=Path("/repo"))
        assert result == ("foo.py",)

    def test_stratum_b_alias_and_canonical_label_collapse_to_one_entry(self) -> None:
        """path:main and path:top-level-main name the same site -- the
        alias canonicalization must run before dedup collapses them."""
        result = m.normalize_and_dedup(["foo.py:main", "foo.py:top-level-main"], stratum=m.STRATUM_B)
        assert result == ("foo.py:top-level-main",)

    def test_stratum_a_entries_are_not_passed_through_symbol_canonicalization(self) -> None:
        """A Stratum A path containing a literal ':' (not a symbol
        separator) must not be touched when stratum isn't B."""
        assert m.normalize_and_dedup(["foo.py:main"]) == ("foo.py:main",)


class TestCanonicalizeStratumBSymbol:
    @pytest.mark.parametrize("alias", sorted(m.STRATUM_B_TOP_LEVEL_SYMBOL_ALIASES))
    def test_each_known_alias_canonicalizes_to_top_level_main(self, alias: str) -> None:
        assert m.canonicalize_stratum_b_symbol(f"foo.py:{alias}") == "foo.py:top-level-main"

    def test_alias_matching_is_case_insensitive(self) -> None:
        assert m.canonicalize_stratum_b_symbol("foo.py:MAIN") == "foo.py:top-level-main"

    def test_strips_surrounding_backticks_before_matching(self) -> None:
        assert m.canonicalize_stratum_b_symbol("foo.py:`main`") == "foo.py:top-level-main"

    def test_strips_a_trailing_call_style_parens_before_matching(self) -> None:
        assert m.canonicalize_stratum_b_symbol("foo.py:main()") == "foo.py:top-level-main"

    def test_backtick_stripping_also_applies_to_a_non_alias_symbol(self) -> None:
        assert m.canonicalize_stratum_b_symbol("foo.py:`_lib_marker_value_present`") == "foo.py:_lib_marker_value_present"

    def test_a_numeric_symbol_is_left_as_is(self) -> None:
        """A mis-reported line number has no convention to rescue it onto --
        it is deliberately left to score as a false positive."""
        assert m.canonicalize_stratum_b_symbol("foo.py:42") == "foo.py:42"

    def test_a_bare_path_with_no_colon_is_left_as_is(self) -> None:
        assert m.canonicalize_stratum_b_symbol("foo.py") == "foo.py"

    def test_an_existing_non_alias_function_name_is_untouched(self) -> None:
        assert m.canonicalize_stratum_b_symbol("foo.py:_lib_marker_value_present") == "foo.py:_lib_marker_value_present"


# --- Matching and per-sample scoring ------------------------------------------


class TestScoreSample:
    def test_exact_match_full_recall_and_precision(self) -> None:
        score = m.score_sample(("a.py", "b.py"), key=frozenset({"a.py", "b.py"}))
        assert score.recall == 1.0
        assert score.precision == 1.0
        assert score.format_failure is False

    def test_partial_recall(self) -> None:
        score = m.score_sample(("a.py",), key=frozenset({"a.py", "b.py"}))
        assert score.recall == 0.5

    def test_false_positive_lowers_precision(self) -> None:
        score = m.score_sample(("a.py", "x.py"), key=frozenset({"a.py", "b.py"}))
        assert score.recall == 0.5
        assert score.precision == 0.5
        assert score.false_positives == frozenset({"x.py"})

    def test_near_miss_is_excluded_from_precision_entirely(self) -> None:
        """A near-miss item is neither a hit nor a false positive -- it must
        drop out of the precision denominator, not merely out of the
        numerator."""
        score = m.score_sample(
            ("a.py", "near.py"), key=frozenset({"a.py", "b.py"}), accepted_near_misses=frozenset({"near.py"}),
        )
        assert score.recall == 0.5
        assert score.precision == 1.0
        assert score.near_miss == frozenset({"near.py"})

    def test_near_miss_alone_leaves_precision_undefined(self) -> None:
        score = m.score_sample(("near.py",), key=frozenset({"a.py"}), accepted_near_misses=frozenset({"near.py"}))
        assert score.matched == frozenset()
        assert score.false_positives == frozenset()
        assert score.precision is None

    def test_empty_returned_array_undefined_precision_zero_recall(self) -> None:
        score = m.score_sample((), key=frozenset({"a.py"}))
        assert score.recall == 0.0
        assert score.precision is None

    def test_format_failure_scores_zero_recall_and_undefined_precision(self) -> None:
        score = m.score_sample(None, key=frozenset({"a.py"}), format_failure=True)
        assert score.recall == 0.0
        assert score.precision is None
        assert score.format_failure is True


# --- Aggregation ---------------------------------------------------------------


class TestPerCaseRecallStats:
    def test_mean_min_max_over_samples(self) -> None:
        scores = [_sample_score(0.2), _sample_score(0.8)]
        mean, lo, hi = m.per_case_recall_stats(scores)
        assert mean == pytest.approx(0.5)
        assert lo == 0.2
        assert hi == 0.8


class TestMacroAverage:
    def test_weights_cases_equally(self) -> None:
        assert m.macro_average([1.0, 0.0]) == 0.5


class TestPooledFalsePositivesAndN:
    def test_sums_matched_and_false_positives_across_scores(self) -> None:
        scores = [
            m.score_sample(("a.py", "x.py"), key=frozenset({"a.py"})),
            m.score_sample(("a.py",), key=frozenset({"a.py"})),
        ]
        x, n = m.pooled_false_positives_and_n(scores)
        assert x == 1
        assert n == 3

    def test_format_failures_contribute_nothing(self) -> None:
        scores = [m.score_sample(None, key=frozenset({"a.py"}), format_failure=True)]
        assert m.pooled_false_positives_and_n(scores) == (0, 0)


class TestPooledPrecisionPointEstimate:
    def test_computes_one_minus_false_positive_rate(self) -> None:
        assert m.pooled_precision_point_estimate(1, 4) == 0.75

    def test_zero_n_is_undefined(self) -> None:
        assert m.pooled_precision_point_estimate(0, 0) is None


class TestAggregateModelResults:
    def test_discarded_outcomes_excluded_from_every_stat(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        good_score = m.score_sample(("a.py",), key=case.key)
        bad_attempt = m.SampleAttempt(
            observed_model_family=msmr.MODEL_FAMILY_SONNET, observed_model_ids=frozenset({"claude-sonnet-5"}),
            cost_usd=1.0, score=good_score,
        )
        discarded_outcome = m.SampleOutcome("c1", msmr.DISPATCH_EXPLORE_HAIKU, 0, bad_attempt, discarded=True)
        good_attempt = m.SampleAttempt(
            observed_model_family=msmr.MODEL_FAMILY_HAIKU, observed_model_ids=frozenset({"claude-haiku-4-5"}),
            cost_usd=1.0, score=good_score,
        )
        kept_outcome = m.SampleOutcome("c1", msmr.DISPATCH_EXPLORE_HAIKU, 1, good_attempt, discarded=False)

        agg = m.aggregate_model_results((case,), msmr.DISPATCH_EXPLORE_HAIKU, (discarded_outcome, kept_outcome))

        assert agg.discards == 1
        assert agg.per_case_recall_mean == {"c1": 1.0}
        assert agg.observed_model_ids == frozenset({"claude-haiku-4-5"})

    def test_format_failures_tallied_separately_and_folded_into_recall_mean(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        fail_score = m.score_sample(None, key=case.key, format_failure=True)
        good_score = m.score_sample(("a.py",), key=case.key)
        outcomes = (
            m.SampleOutcome(
                "c1", msmr.DISPATCH_EXPLORE_HAIKU, 0,
                m.SampleAttempt(msmr.MODEL_FAMILY_HAIKU, frozenset(), 1.0, fail_score), discarded=False,
            ),
            m.SampleOutcome(
                "c1", msmr.DISPATCH_EXPLORE_HAIKU, 1,
                m.SampleAttempt(msmr.MODEL_FAMILY_HAIKU, frozenset(), 1.0, good_score), discarded=False,
            ),
        )

        agg = m.aggregate_model_results((case,), msmr.DISPATCH_EXPLORE_HAIKU, outcomes)

        assert agg.format_failures == 1
        assert agg.per_case_recall_mean["c1"] == pytest.approx(0.5)


class TestComputeGateInputs:
    def test_stratum_gap_is_absolute_and_per_case_gap_is_signed(self) -> None:
        haiku = _model_aggregate(stratum_recall_mean={"A": 0.9, "B": 0.5}, per_case_recall_mean={"c1": 0.9})
        sonnet = _model_aggregate(stratum_recall_mean={"A": 0.5, "B": 0.9}, per_case_recall_mean={"c1": 0.5})
        cases = (m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"})),)

        stratum_gaps, min_gap = m.compute_gate_inputs(cases, haiku, sonnet)

        assert stratum_gaps == {"A": pytest.approx(0.4), "B": pytest.approx(0.4)}
        assert min_gap == pytest.approx(0.4)

    def test_haiku_outperforming_sonnet_yields_a_positive_gap_not_a_cliff(self) -> None:
        """Term 2 only guards against Haiku underperforming -- the caller
        must pass the signed gap, not its absolute value, or a case where
        Haiku is far better would wrongly look like a cliff. With only one
        common case, the min() reduction has nothing else to pick from, so
        the lone case's own positive gap passes through unchanged."""
        haiku = _model_aggregate(stratum_recall_mean={}, per_case_recall_mean={"c1": 0.9})
        sonnet = _model_aggregate(stratum_recall_mean={}, per_case_recall_mean={"c1": 0.2})
        cases = (m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"})),)

        _stratum_gaps, min_gap = m.compute_gate_inputs(cases, haiku, sonnet)

        assert min_gap == pytest.approx(0.7)

    def test_min_reduction_surfaces_haikus_worst_case_and_fails_term_two(self) -> None:
        """Regression for the 2026-10-15 K=5 run's shape: case b2's gap is
        +0.033 (Haiku slightly ahead) and case b3's is -0.20 (Haiku's
        cliff). The reduction must surface b3's -0.20 rather than b2's
        +0.033, or the cliff would hide inside a passing mean, and that
        -0.20 worst gap must fail term 2."""
        haiku = _model_aggregate(per_case_recall_mean={"b2": 1.0, "b3": 0.8})
        sonnet = _model_aggregate(per_case_recall_mean={"b2": 0.967, "b3": 1.0})
        cases = (
            m.RecallCase(id="b2", stratum=m.STRATUM_B, question="q", key=frozenset({"a.py:f"})),
            m.RecallCase(id="b3", stratum=m.STRATUM_B, question="q", key=frozenset({"a.py:f"})),
        )

        stratum_gaps, min_gap = m.compute_gate_inputs(cases, haiku, sonnet)

        assert min_gap == pytest.approx(-0.20, abs=1e-3)
        verdict = m.evaluate_gate(
            stratum_recall_gaps=stratum_gaps, min_per_case_recall_gap=min_gap,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.FAIL

    def test_a_worst_gap_mirrored_at_minus_ten_points_passes_term_two(self) -> None:
        """Mirrors the regression above with b3's gap narrowed to -0.10,
        inside the 15-point floor, so term 2 passes."""
        haiku = _model_aggregate(per_case_recall_mean={"b2": 1.0, "b3": 0.9})
        sonnet = _model_aggregate(per_case_recall_mean={"b2": 0.967, "b3": 1.0})
        cases = (
            m.RecallCase(id="b2", stratum=m.STRATUM_B, question="q", key=frozenset({"a.py:f"})),
            m.RecallCase(id="b3", stratum=m.STRATUM_B, question="q", key=frozenset({"a.py:f"})),
        )

        stratum_gaps, min_gap = m.compute_gate_inputs(cases, haiku, sonnet)

        assert min_gap == pytest.approx(-0.10, abs=1e-3)
        verdict = m.evaluate_gate(
            stratum_recall_gaps=stratum_gaps, min_per_case_recall_gap=min_gap,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.PASS


class TestGateDataCoverageIsComplete:
    def test_true_when_every_stratum_and_case_has_data_for_both_models(self) -> None:
        cases = (
            m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"})),
            m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=frozenset({"b.py:f"})),
        )
        haiku = _model_aggregate(
            stratum_recall_mean={m.STRATUM_A: 1.0, m.STRATUM_B: 1.0}, per_case_recall_mean={"a1": 1.0, "b1": 1.0},
        )
        sonnet = _model_aggregate(
            stratum_recall_mean={m.STRATUM_A: 1.0, m.STRATUM_B: 1.0}, per_case_recall_mean={"a1": 1.0, "b1": 1.0},
        )

        assert m._gate_data_coverage_is_complete(cases, haiku, sonnet) is True

    def test_false_when_one_case_lacks_data_for_one_model_while_its_stratum_still_has_data_from_another_case(
        self,
    ) -> None:
        """Stratum B has two cases, b1 and b2. Haiku's own stratum_recall_mean
        for Stratum B stays populated from case_b2 alone, even though case_b1
        is missing from Haiku's per_case_recall_mean. Only the cases_covered
        check, not the strata_covered check, can catch that gap."""
        cases = (
            m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=frozenset({"b1.py:f"})),
            m.RecallCase(id="b2", stratum=m.STRATUM_B, question="q", key=frozenset({"b2.py:f"})),
        )
        haiku = _model_aggregate(stratum_recall_mean={m.STRATUM_B: 1.0}, per_case_recall_mean={"b2": 1.0})
        sonnet = _model_aggregate(
            stratum_recall_mean={m.STRATUM_B: 1.0}, per_case_recall_mean={"b1": 1.0, "b2": 1.0},
        )

        assert m._gate_data_coverage_is_complete(cases, haiku, sonnet) is False

    def test_false_when_a_whole_stratum_lacks_data(self) -> None:
        cases = (
            m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"})),
            m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=frozenset({"b.py:f"})),
        )
        haiku = _model_aggregate(stratum_recall_mean={m.STRATUM_A: 1.0}, per_case_recall_mean={"a1": 1.0})
        sonnet = _model_aggregate(
            stratum_recall_mean={m.STRATUM_A: 1.0, m.STRATUM_B: 1.0}, per_case_recall_mean={"a1": 1.0, "b1": 1.0},
        )

        assert m._gate_data_coverage_is_complete(cases, haiku, sonnet) is False


class TestScoreAndDecide:
    def test_all_samples_discarded_is_uncallable_with_no_exception(self) -> None:
        """A second, distinct road to UNCALLABLE beside a too-low Wilson
        lower bound: when every sample for a model is discarded (persistent
        arm/observed-model mismatch), there is no scored data at all to
        decide on. A regression that called evaluate_gate unconditionally
        would hit wilson_precision_lower_bound's n<=0 ScoringError instead
        of reporting UNCALLABLE."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        mismatched_attempt = m.SampleAttempt(
            observed_model_family=msmr.MODEL_FAMILY_SONNET, observed_model_ids=frozenset(), cost_usd=1.0,
            score=m.score_sample(("a.py",), key=case.key),
        )
        outcomes = (
            m.SampleOutcome("c1", msmr.DISPATCH_EXPLORE_HAIKU, 0, mismatched_attempt, discarded=True),
            m.SampleOutcome("c1", msmr.DISPATCH_EXPLORE_SONNET, 0, mismatched_attempt, discarded=True),
        )

        verdict, _haiku_agg, _sonnet_agg = m._score_and_decide((case,), outcomes)

        assert verdict == m.GateVerdict.UNCALLABLE

    def test_one_stratum_with_no_common_scored_data_is_uncallable(self) -> None:
        """A dropout confined to one stratum for one model (every Stratum B
        sample for Haiku discarded, while Sonnet's own Stratum B sample
        scored normally) must not pass term 1 vacuously just because
        Haiku's overall precision is still computable from Stratum A
        alone."""
        case_a = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        case_b = m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=frozenset({"b.py:f"}))

        def attempt(family: str) -> m.SampleAttempt:
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=1.0,
                score=m.score_sample(("a.py",), key=frozenset({"a.py"})),
            )

        outcomes = (
            m.SampleOutcome("a1", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_HAIKU), discarded=False),
            m.SampleOutcome("a1", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET), discarded=False),
            m.SampleOutcome("b1", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_SONNET), discarded=True),
            m.SampleOutcome("b1", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET), discarded=False),
        )

        verdict, haiku_agg, _sonnet_agg = m._score_and_decide((case_a, case_b), outcomes)

        assert haiku_agg.precision_point_estimate is not None  # data exists, just not for Stratum B
        assert verdict == m.GateVerdict.UNCALLABLE

    def test_full_coverage_of_two_stratum_b_cases_passes(self) -> None:
        """Baseline for test_single_case_dropout_in_a_multi_case_stratum_is_uncallable
        below. The identical three-case fixture, scored in full (zero false
        positives, full recall) for both models, clears every gate term
        outright. This establishes that the dropout test's own flip to
        UNCALLABLE is attributable to the single case_b1 dropout alone, not
        to an already-failing baseline or to an insufficient pooled n."""
        case_a = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=_wide_site_key("a1"))
        case_b1 = m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=_wide_site_key("b1"))
        case_b2 = m.RecallCase(id="b2", stratum=m.STRATUM_B, question="q", key=_wide_site_key("b2"))

        def attempt(family: str, key: frozenset[str]) -> m.SampleAttempt:
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=1.0,
                score=m.score_sample(tuple(key), key=key),
            )

        outcomes = (
            m.SampleOutcome(
                "a1", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_HAIKU, case_a.key), discarded=False,
            ),
            m.SampleOutcome(
                "a1", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_a.key), discarded=False,
            ),
            m.SampleOutcome(
                "b1", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_HAIKU, case_b1.key), discarded=False,
            ),
            m.SampleOutcome(
                "b1", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_b1.key), discarded=False,
            ),
            m.SampleOutcome(
                "b2", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_HAIKU, case_b2.key), discarded=False,
            ),
            m.SampleOutcome(
                "b2", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_b2.key), discarded=False,
            ),
        )

        verdict, _haiku_agg, _sonnet_agg = m._score_and_decide((case_a, case_b1, case_b2), outcomes)

        assert verdict == m.GateVerdict.PASS

    def test_single_case_dropout_in_a_multi_case_stratum_is_uncallable(self) -> None:
        """Isolates _gate_data_coverage_is_complete's cases_covered check
        from its strata_covered check: Stratum B has two cases here, and
        only case_b1 drops out for Haiku while case_b2 still scores
        normally for both models, so Stratum B's own stratum_recall_mean
        is still populated for Haiku (macro-averaged over the surviving
        case_b2 alone) -- strata_covered alone would pass this vacuously.
        Only cases_covered catches the case_b1 dropout. Each case's key has
        20 sites (_wide_site_key). The pooled n is 60 with no dropout, or 40
        with case_b1 dropped, either of which clears the Wilson lower
        bound's n>=35 floor at zero false positives. The test above proves
        the identical fixture passes outright with no dropout, so the flip
        to UNCALLABLE here is attributable to the dropout alone, not to an
        insufficient n."""
        case_a = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=_wide_site_key("a1"))
        case_b1 = m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=_wide_site_key("b1"))
        case_b2 = m.RecallCase(id="b2", stratum=m.STRATUM_B, question="q", key=_wide_site_key("b2"))

        def attempt(family: str, key: frozenset[str]) -> m.SampleAttempt:
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=1.0,
                score=m.score_sample(tuple(key), key=key),
            )

        outcomes = (
            m.SampleOutcome(
                "a1", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_HAIKU, case_a.key), discarded=False,
            ),
            m.SampleOutcome(
                "a1", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_a.key), discarded=False,
            ),
            m.SampleOutcome(
                "b1", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_b1.key), discarded=True,
            ),
            m.SampleOutcome(
                "b1", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_b1.key), discarded=False,
            ),
            m.SampleOutcome(
                "b2", msmr.DISPATCH_EXPLORE_HAIKU, 0, attempt(msmr.MODEL_FAMILY_HAIKU, case_b2.key), discarded=False,
            ),
            m.SampleOutcome(
                "b2", msmr.DISPATCH_EXPLORE_SONNET, 0, attempt(msmr.MODEL_FAMILY_SONNET, case_b2.key), discarded=False,
            ),
        )

        verdict, haiku_agg, _sonnet_agg = m._score_and_decide((case_a, case_b1, case_b2), outcomes)

        assert m.STRATUM_B in haiku_agg.stratum_recall_mean  # populated from case_b2 alone
        assert "b1" not in haiku_agg.per_case_recall_mean  # the dropout cases_covered must catch
        assert verdict == m.GateVerdict.UNCALLABLE


# --- Wilson score interval -----------------------------------------------------


class TestWilsonLowerBound:
    def test_raises_for_non_positive_n(self) -> None:
        with pytest.raises(m.ScoringError, match="positive"):
            m.wilson_lower_bound(0, 0)

    def test_full_success_lower_bound_is_below_one(self) -> None:
        lower = m.wilson_lower_bound(10, 10)
        assert 0.0 < lower < 1.0


class TestWilsonPrecisionLowerBound:
    def test_zero_false_positives_n_65_matches_known_value(self) -> None:
        assert m.wilson_precision_lower_bound(0, 65) == pytest.approx(0.944, abs=0.001)

    def test_zero_false_positives_n_35_matches_known_value(self) -> None:
        assert m.wilson_precision_lower_bound(0, 35) == pytest.approx(0.90, abs=0.01)


# --- Adoption gate ---------------------------------------------------------------


class TestEvaluateGate:
    def test_pass_when_everything_clears(self) -> None:
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=0.0,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.PASS

    def test_fails_when_a_stratum_gap_exceeds_five_points(self) -> None:
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.06, "B": 0.0}, min_per_case_recall_gap=0.0,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.FAIL

    def test_passes_when_a_stratum_gap_is_exactly_five_points(self) -> None:
        """Pins the boundary as inclusive -- a >/>= mutation on the stratum-gap
        check would flip this to FAIL unnoticed."""
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.05, "B": 0.0}, min_per_case_recall_gap=0.0,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.PASS

    def test_fails_when_the_worst_per_case_gap_exceeds_fifteen_points(self) -> None:
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=-0.16,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.FAIL

    def test_passes_when_the_worst_per_case_gap_is_exactly_fifteen_points(self) -> None:
        """Pins the boundary as inclusive -- a </<= mutation on the per-case-gap
        check would flip this to FAIL unnoticed."""
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=-0.15,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.PASS

    def test_passes_when_haiku_outperforms_on_the_worst_case_too(self) -> None:
        """A positive min_per_case_recall_gap means even Haiku's worst case
        beat Sonnet -- term 2 must not flag that as a cliff."""
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=0.16,
            haiku_precision_point_estimate=0.99, haiku_precision_wilson_lower_bound=0.95,
        )
        assert verdict == m.GateVerdict.PASS

    def test_uncallable_when_lower_bound_below_floor_regardless_of_point_estimate(self) -> None:
        """Pins the term-3 ordering: a lower bound below 0.90 is UNCALLABLE
        even when the point estimate would independently fail too."""
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=0.0,
            haiku_precision_point_estimate=0.5, haiku_precision_wilson_lower_bound=0.5,
        )
        assert verdict == m.GateVerdict.UNCALLABLE

    def test_fails_on_point_estimate_when_lower_bound_is_sufficient(self) -> None:
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=0.0,
            haiku_precision_point_estimate=0.95, haiku_precision_wilson_lower_bound=0.92,
        )
        assert verdict == m.GateVerdict.FAIL

    def test_per_case_cliff_check_runs_before_the_wilson_lower_bound_check(self) -> None:
        """Term ordering: when term 2's cliff guard and term 3's Wilson
        lower bound would independently disagree (FAIL vs UNCALLABLE), term
        2 is checked first per the plan's exact ordering (1, 2, then 3), so
        the verdict is FAIL -- the same bug class the real run's max()/
        min() reduction slip already surfaced once, just at the ordering
        level instead of the reduction level."""
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.0, "B": 0.0}, min_per_case_recall_gap=-0.5,
            haiku_precision_point_estimate=0.5, haiku_precision_wilson_lower_bound=0.5,
        )
        assert verdict == m.GateVerdict.FAIL

    def test_stratum_gap_check_runs_before_the_precision_terms(self) -> None:
        """Term ordering: a term-1 failure is reported even though term 3's
        inputs here would otherwise drive an UNCALLABLE verdict -- pins that
        term 1 is checked first, per the plan's exact ordering."""
        verdict = m.evaluate_gate(
            stratum_recall_gaps={"A": 0.5}, min_per_case_recall_gap=-0.5,
            haiku_precision_point_estimate=0.1, haiku_precision_wilson_lower_bound=0.1,
        )
        assert verdict == m.GateVerdict.FAIL


# --- Dispatch mechanics --------------------------------------------------------


class TestBuildDispatchPrompt:
    def test_includes_question_contract_and_no_model_param_clause(self) -> None:
        case = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="Where is X handled?", key=frozenset({"a.py"}))
        prompt = m.build_dispatch_prompt(case, Path("/repo"))
        assert "Where is X handled?" in prompt
        assert "```json" in prompt
        assert msmr.DISPATCH_PROMPT_EXPLORE_HAIKU_SUFFIX in prompt
        assert '"Explore"' in prompt

    def test_includes_the_repo_dir_and_relative_path_reporting_instruction(self) -> None:
        case = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        prompt = m.build_dispatch_prompt(case, Path("/repo/checkout"))
        assert "The repository to search is the directory /repo/checkout." in prompt
        assert "Report every path relative to that directory." in prompt

    def test_stratum_a_contract_asks_for_file_paths(self) -> None:
        case = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        assert "file paths" in m.build_dispatch_prompt(case, Path("/repo"))

    def test_stratum_b_contract_asks_for_path_symbol_entries(self) -> None:
        case = m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=frozenset({"a.py:f"}))
        assert "path:symbol" in m.build_dispatch_prompt(case, Path("/repo"))

    def test_stratum_b_contract_defines_the_containing_function_and_top_level_main(self) -> None:
        case = m.RecallCase(id="b1", stratum=m.STRATUM_B, question="q", key=frozenset({"a.py:f"}))
        prompt = m.build_dispatch_prompt(case, Path("/repo"))
        assert "CONTAINS the matching line, not the function being called there" in prompt
        assert "top-level-main" in prompt

    def test_stratum_a_contract_does_not_mention_symbols(self) -> None:
        case = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        prompt = m.build_dispatch_prompt(case, Path("/repo"))
        assert "symbol" not in prompt
        assert "top-level-main" not in prompt


class TestBuildRunCommand:
    def test_haiku_arm_uses_haiku_override_and_default_permission_mode(self) -> None:
        cmd = m.build_run_command(
            msmr.DISPATCH_EXPLORE_HAIKU, "prompt text", session_id="sid", budget_cap_usd=5.0, repo_dir=Path("/repo"),
        )
        assert "--permission-mode" not in cmd
        assert cmd[cmd.index("--session-id") + 1] == "sid"
        assert cmd[cmd.index("--max-budget-usd") + 1] == "5.0"
        payload = json.loads(cmd[cmd.index("--agents") + 1])
        assert payload["Explore"]["model"] == "haiku"

    def test_sonnet_arm_uses_sonnet_override(self) -> None:
        cmd = m.build_run_command(
            msmr.DISPATCH_EXPLORE_SONNET, "prompt", session_id="sid", budget_cap_usd=1.0, repo_dir=Path("/repo"),
        )
        payload = json.loads(cmd[cmd.index("--agents") + 1])
        assert payload["Explore"]["model"] == "sonnet"

    def test_grants_add_dir_access_to_repo_dir(self) -> None:
        """Without --add-dir, Explore's Read/Grep/Glob calls against repo_dir
        are denied under the default permission mode's non-interactive -p
        launch, since repo_dir sits outside the headless parent's own
        tmp_project cwd."""
        cmd = m.build_run_command(
            msmr.DISPATCH_EXPLORE_HAIKU, "prompt", session_id="sid", budget_cap_usd=1.0,
            repo_dir=Path("/repo/checkout"),
        )
        assert cmd[cmd.index("--add-dir") + 1] == "/repo/checkout"


class TestBuildDryRunPlan:
    def test_builds_every_combination_without_launching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail_if_called(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("must not launch a subprocess during --dry-run")

        monkeypatch.setattr(msmr, "_run_claude_to_completion", _fail_if_called)
        cases = (m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"})),)

        plan = m.build_dry_run_plan(cases, m.ARMS, 2, budget_cap_usd=1.0, repo_dir=Path("/repo"))

        assert len(plan) == 1 * 2 * 2
        assert all("command" in entry and "prompt" in entry for entry in plan)


class TestValidateKeepArtifactsDir:
    def test_a_path_inside_the_repository_is_refused(self) -> None:
        repo_roots = m._repo_roots_for_containment_check()
        assert repo_roots  # this test itself runs inside a git checkout

        with pytest.raises(m.KeepArtifactsValidationError, match="inside a worktree of this repository"):
            m.validate_keep_artifacts_dir(repo_roots[0] / "tmp-keep-artifacts")

    def test_a_path_outside_the_repository_is_accepted(self, tmp_path: Path) -> None:
        m.validate_keep_artifacts_dir(tmp_path / "kept")  # must not raise

    def test_the_repo_root_itself_is_refused(self, tmp_path: Path) -> None:
        """Exercises the `resolved == repo_root` branch directly, distinct
        from the subdirectory (`repo_root in resolved.parents`) branch the
        other tests in this class cover."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "README.md", "x")
        _commit(repo, "init")

        with pytest.raises(m.KeepArtifactsValidationError, match="inside a worktree of this repository"):
            m.validate_keep_artifacts_dir(repo, start_dir=repo)

    def test_a_path_inside_a_sibling_worktree_is_refused(self, tmp_path: Path) -> None:
        """Both worktrees share the same tracked history, so a
        --keep-artifacts path under a *different* worktree of the same
        repository must be refused too, not only one under the worktree the
        scorer happens to run from."""
        main = _init_repo(tmp_path / "main")
        _write(main, "README.md", "x")
        _commit(main, "init")
        linked = tmp_path / "linked"
        _git(main, "worktree", "add", str(linked), "-b", "linked-branch")

        with pytest.raises(m.KeepArtifactsValidationError, match="inside a worktree of this repository"):
            m.validate_keep_artifacts_dir(linked / "tmp-keep-artifacts", start_dir=main)

    def test_missing_git_binary_degrades_to_no_repo_roots_instead_of_crashing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise_file_not_found(*_args: object, **_kwargs: object) -> None:
            raise FileNotFoundError("git")

        monkeypatch.setattr(m.subprocess, "run", _raise_file_not_found)

        assert m._repo_roots_for_containment_check() == ()
        m.validate_keep_artifacts_dir(tmp_path / "kept")  # must not raise, nothing to guard against


class TestExecuteOneSampleAttempt:
    """Component test of the call sequence connecting msmr's launch/sidecar
    primitives to this module's answer extraction and scoring -- mirrors
    msmr's own TestExecuteMatrixCell, which exists because this exact kind
    of composition already produced two real bugs by hand in the sibling
    module (symlink path mismatch, sidecar-flush race). Stubs only the
    subprocess launch; temp-dir resolution, session-store lookup, sidecar
    parsing, and cleanup all run for real."""

    def test_launch_lookup_extraction_and_cleanup_all_target_the_same_resolved_path(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="Where is X?", key=frozenset({"a.py", "b.py"}))
        seen_cwd: list[Path] = []

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            assert cwd == cwd.resolve()
            seen_cwd.append(cwd)
            session_id = cmd[cmd.index("--session-id") + 1]
            subagent_dir = msmr.run_skill_evals.compute_session_store_dir(cwd) / session_id / msmr.SUBAGENT_SUBDIR
            subagent_dir.mkdir(parents=True)
            (subagent_dir / "agent-1.meta.json").write_text(
                json.dumps({"toolUseId": "toolu_1", "agentType": "Explore"})
            )
            _write_jsonl(subagent_dir / "agent-1.jsonl", [_text_record('```json\n["a.py", "b.py"]\n```')])
            lines = [
                json.dumps(
                    {"event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Task"}}}
                ).encode()
            ]
            return lines, False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)

        attempt = m.execute_one_sample_attempt(
            case, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=11.5, timeout_s=90, repo_dir=Path("/repo"),
        )

        assert attempt.score.recall == 1.0
        assert attempt.score.format_failure is False

        launched_cwd = seen_cwd[0]
        assert not launched_cwd.exists(), "cleanup must remove the same tmp_project it launched in"
        assert not msmr.run_skill_evals.compute_session_store_dir(launched_cwd).exists(), (
            "cleanup must remove the session store keyed on the same resolved path used for launch"
        )

    def test_keep_artifacts_dir_copies_the_session_and_subagents_before_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without this copy, the finally block's own cleanup deletes the
        session store before a caller can inspect what the subagent actually
        received -- the only way to diagnose a live run after it finishes."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="Where is X?", key=frozenset({"a.py"}))

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            session_id = cmd[cmd.index("--session-id") + 1]
            session_store_dir = msmr.run_skill_evals.compute_session_store_dir(cwd)
            subagent_dir = session_store_dir / session_id / msmr.SUBAGENT_SUBDIR
            subagent_dir.mkdir(parents=True)
            _write_jsonl(session_store_dir / f"{session_id}.jsonl", [_tool_use_record("Task")])
            (subagent_dir / "agent-1.meta.json").write_text(
                json.dumps({"toolUseId": "toolu_1", "agentType": "Explore"})
            )
            _write_jsonl(subagent_dir / "agent-1.jsonl", [_text_record('```json\n["a.py"]\n```')])
            lines = [
                json.dumps(
                    {"event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Task"}}}
                ).encode()
            ]
            return lines, False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)
        keep_artifacts_dir = tmp_path / "kept"

        m.execute_one_sample_attempt(
            case, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=11.5, timeout_s=90, repo_dir=Path("/repo"),
            keep_artifacts_dir=keep_artifacts_dir, sample_index=3,
        )

        dest = keep_artifacts_dir / "c1-explore-haiku-3"
        assert dest.is_dir()
        assert list(dest.glob("*.jsonl")), "the parent session .jsonl must be copied before cleanup removes it"
        assert (dest / "subagents" / "agent-1.jsonl").is_file()

    def test_no_dispatch_observed_is_a_format_failure_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            return [], False  # the parent never attempted a dispatch at all

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)

        attempt = m.execute_one_sample_attempt(
            case, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=11.5, timeout_s=90, repo_dir=Path("/repo"),
        )

        assert attempt.observed_model_family is None
        assert attempt.score.format_failure is True

    def test_missing_paired_jsonl_sidecar_is_a_format_failure_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A meta.json flushed with its paired *.jsonl transcript still
        pending or lost -- a third shape of the sidecar-flush race this
        composition has already broken on by hand twice -- must fall back
        to a format failure rather than an IndexError on the empty
        jsonl_paths glob."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            session_id = cmd[cmd.index("--session-id") + 1]
            subagent_dir = msmr.run_skill_evals.compute_session_store_dir(cwd) / session_id / msmr.SUBAGENT_SUBDIR
            subagent_dir.mkdir(parents=True)
            (subagent_dir / "agent-1.meta.json").write_text(
                json.dumps({"toolUseId": "toolu_1", "agentType": "Explore"})
            )
            # No paired agent-1.jsonl written.
            lines = [
                json.dumps(
                    {"event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Task"}}}
                ).encode()
            ]
            return lines, False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)

        attempt = m.execute_one_sample_attempt(
            case, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=11.5, timeout_s=90, repo_dir=Path("/repo"),
        )

        assert attempt.score.format_failure is True

    def test_two_dispatches_pair_the_first_by_sorted_filename_with_its_own_answer(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Confirms the sorted-glob index-pairing the code comment
        documents: dispatches[0] (sorted by meta.json filename) must be
        scored against jsonl_paths[0] (sorted by .jsonl filename) for the
        SAME dispatch, not a different one, when a disobedient model makes
        more than one Explore dispatch."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            session_id = cmd[cmd.index("--session-id") + 1]
            subagent_dir = msmr.run_skill_evals.compute_session_store_dir(cwd) / session_id / msmr.SUBAGENT_SUBDIR
            subagent_dir.mkdir(parents=True)
            (subagent_dir / "agent-aa.meta.json").write_text(
                json.dumps({"toolUseId": "toolu_1", "agentType": "Explore"})
            )
            _write_jsonl(subagent_dir / "agent-aa.jsonl", [
                {"type": "assistant", "message": {
                    "model": "claude-haiku-4-5-20251001",
                    "content": [{"type": "text", "text": '```json\n["a.py"]\n```'}],
                }},
            ])
            (subagent_dir / "agent-zz.meta.json").write_text(
                json.dumps({"toolUseId": "toolu_2", "agentType": "Explore"})
            )
            _write_jsonl(subagent_dir / "agent-zz.jsonl", [
                {"type": "assistant", "message": {
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": '```json\n["wrong.py"]\n```'}],
                }},
            ])
            lines = [
                json.dumps(
                    {"event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Task"}}}
                ).encode()
            ]
            return lines, False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)

        attempt = m.execute_one_sample_attempt(
            case, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=11.5, timeout_s=90, repo_dir=Path("/repo"),
        )

        assert attempt.observed_model_family == msmr.MODEL_FAMILY_HAIKU  # agent-aa's own model
        assert attempt.parsed_items == ("a.py",)  # agent-aa's own answer, not agent-zz's

    def test_timed_out_attempt_scores_as_a_format_failure_and_charges_the_budget_cap(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The exact shape that miscalibrated a real $32.75 run: a
        harness-level timeout kills the child before any subagent dispatch
        is observed. It must score as a format failure, not crash, and its
        cost must be charged conservatively at the per-dispatch cap rather
        than $0, since a killed process never emits the stream's "result"
        event."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            return [], True  # killed before any line, including a "result" event

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)

        attempt = m.execute_one_sample_attempt(
            case, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=11.5, timeout_s=90, repo_dir=Path("/repo"),
        )

        assert attempt.timed_out is True
        assert attempt.score.format_failure is True
        assert attempt.cost_usd == 11.5
        assert attempt.cost_source == m.COST_SOURCE_CAP


class TestRunOneLogicalSample:
    def test_matching_family_on_first_attempt_is_not_discarded(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, calls = _scripted_launcher([msmr.MODEL_FAMILY_HAIKU])

        outcome, cost = m.run_one_logical_sample(
            case, msmr.DISPATCH_EXPLORE_HAIKU, 0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert outcome.discarded is False
        assert cost == 1.0
        assert calls["count"] == 1

    def test_mismatch_then_match_retries_and_is_not_discarded(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, calls = _scripted_launcher([msmr.MODEL_FAMILY_SONNET, msmr.MODEL_FAMILY_HAIKU])

        outcome, cost = m.run_one_logical_sample(
            case, msmr.DISPATCH_EXPLORE_HAIKU, 0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert outcome.discarded is False
        assert cost == 2.0
        assert calls["count"] == 2

    def test_persistent_mismatch_exhausts_retries_and_is_discarded(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, calls = _scripted_launcher([msmr.MODEL_FAMILY_SONNET] * 10)

        outcome, cost = m.run_one_logical_sample(
            case, msmr.DISPATCH_EXPLORE_HAIKU, 0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert outcome.discarded is True
        assert calls["count"] == m.MAX_ARM_MISMATCH_RETRIES + 1
        assert cost == (m.MAX_ARM_MISMATCH_RETRIES + 1) * 1.0

    def test_no_dispatch_observed_counts_as_a_mismatch_and_is_discarded(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, calls = _scripted_launcher([None] * 10)

        outcome, _cost = m.run_one_logical_sample(
            case, msmr.DISPATCH_EXPLORE_HAIKU, 0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert outcome.discarded is True
        assert calls["count"] == m.MAX_ARM_MISMATCH_RETRIES + 1

    def test_first_attempt_times_out_and_the_retry_succeeds_keeps_both_attempts(self) -> None:
        """A harness-level timeout on the first attempt observes no
        dispatch at all, which also counts as an arm/observed-model
        mismatch and triggers a retry. The retry's own success must not
        make the first attempt's timeout and capped cost vanish: both
        attempts belong in the outcome's attempts list, the retained
        `.attempt` is only the winning one, and aggregate_model_results
        must still count the earlier timeout."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        budget_cap = 2.0
        reported_cost = 0.5
        calls: dict[str, int] = {"count": 0}

        def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
            calls["count"] += 1
            if calls["count"] == 1:
                return m.SampleAttempt(
                    observed_model_family=None, observed_model_ids=frozenset(), cost_usd=budget_cap,
                    cost_source=m.COST_SOURCE_CAP, timed_out=True,
                    score=m.score_sample(None, key=case.key, format_failure=True),
                )
            return m.SampleAttempt(
                observed_model_family=msmr.MODEL_FAMILY_HAIKU, observed_model_ids=frozenset(), cost_usd=reported_cost,
                score=m.score_sample(("x.py",), key=case.key),
            )

        outcome, cost = m.run_one_logical_sample(
            case, msmr.DISPATCH_EXPLORE_HAIKU, 0, budget_cap_usd=budget_cap, timeout_s=1, launcher=launcher,
        )

        assert outcome.discarded is False
        assert len(outcome.attempts) == 2
        assert cost == pytest.approx(budget_cap + reported_cost)

        agg = m.aggregate_model_results((case,), msmr.DISPATCH_EXPLORE_HAIKU, (outcome,))
        assert agg.timeouts == 1


class TestRunBatch:
    def test_runs_k_samples_per_case_per_arm(self) -> None:
        cases = (
            m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q1", key=frozenset({"a.py"})),
            m.RecallCase(id="c2", stratum=m.STRATUM_A, question="q2", key=frozenset({"b.py"})),
        )
        launcher, _calls = _matching_family_launcher()

        batch = m.run_batch(
            cases, m.ARMS, k=3, cumulative_stop_usd=1000.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert len(batch.outcomes) == len(cases) * len(m.ARMS) * 3
        assert batch.cumulative_stop_triggered is False

    def test_cumulative_stop_fires_mid_batch(self) -> None:
        """Mocked launcher, no real subprocess -- a fixed per-call cost that
        crosses cumulative_stop_usd partway through must abort the whole
        batch, not just the next dispatch."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        launcher, calls = _scripted_launcher([msmr.MODEL_FAMILY_HAIKU] * 20, cost=2.0)

        batch = m.run_batch(
            (case,), (msmr.DISPATCH_EXPLORE_HAIKU,), k=5, cumulative_stop_usd=5.0,
            budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert len(batch.outcomes) == 3  # 2 + 2 + 2 >= 5 on the third call
        assert batch.cumulative_stop_triggered is True
        assert batch.realized_spend_usd == 6.0
        assert calls["count"] == 3

    def test_starting_spend_continues_the_running_total(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        launcher, _calls = _scripted_launcher([msmr.MODEL_FAMILY_HAIKU] * 10, cost=1.0)

        batch = m.run_batch(
            (case,), (msmr.DISPATCH_EXPLORE_HAIKU,), k=2, cumulative_stop_usd=100.0,
            budget_cap_usd=1.0, timeout_s=1, launcher=launcher, starting_spend=50.0,
        )

        assert batch.realized_spend_usd == 52.0

    def test_sample_index_offset_numbers_outcomes_continuing_from_the_offset(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        launcher, _calls = _scripted_launcher([msmr.MODEL_FAMILY_HAIKU] * 10, cost=0.0)

        batch = m.run_batch(
            (case,), (msmr.DISPATCH_EXPLORE_HAIKU,), k=2, cumulative_stop_usd=100.0,
            budget_cap_usd=1.0, timeout_s=1, launcher=launcher, sample_index_offset=5,
        )

        assert [o.sample_index for o in batch.outcomes] == [5, 6]

    def test_cumulative_stop_accounts_for_a_timed_out_samples_capped_cost(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real timed-out dispatch is cost-accounted at the per-dispatch
        cap (execute_one_sample_attempt's own fix), not $0, and that capped
        cost reaches run_batch's cumulative-stop accounting end-to-end --
        the exact pattern that under-counted spend in the first K=5 run.
        Drives the launcher through the real execute_one_sample_attempt
        (only msmr._run_claude_to_completion is stubbed) rather than a
        scripted launcher that never times out, so the interaction between
        execute_one_sample_attempt's cap-charging and run_batch's
        summation is actually exercised. A harness-level timeout never
        observes a dispatch either, so every attempt also counts as an
        arm/observed-model mismatch and exhausts run_one_logical_sample's
        own retries before this one logical sample's outcome is recorded."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py"}))
        budget_cap = 11.5
        attempts_per_logical_sample = m.MAX_ARM_MISMATCH_RETRIES + 1

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            return [], True  # killed before any line, including a "result" event

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)

        def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
            return m.execute_one_sample_attempt(
                case, arm, budget_cap_usd=budget_cap_usd, timeout_s=timeout_s, repo_dir=Path("/repo"),
            )

        batch = m.run_batch(
            (case,), (msmr.DISPATCH_EXPLORE_HAIKU,), k=5, cumulative_stop_usd=budget_cap,
            budget_cap_usd=budget_cap, timeout_s=1, launcher=launcher,
        )

        assert batch.cumulative_stop_triggered is True
        assert len(batch.outcomes) == 1  # the very first logical sample already crosses the stop
        assert batch.outcomes[0].discarded is True
        assert batch.realized_spend_usd == pytest.approx(budget_cap * attempts_per_logical_sample)
        assert len(batch.outcomes[0].attempts) == attempts_per_logical_sample
        assert all(a.timed_out and a.cost_source == m.COST_SOURCE_CAP for a in batch.outcomes[0].attempts)


class TestRunRecallGate:
    def test_pass_verdict_with_no_uncallable_retry(self) -> None:
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py", "b.py", "c.py", "d.py", "e.py"}))

        def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
            family = m.ARM_EXPECTED_FAMILY[arm]
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=0.0,
                score=m.score_sample(tuple(sorted(case.key)), key=case.key),
            )

        report = m.run_recall_gate(
            (case,), k=20, cumulative_stop_usd=300.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert report.uncallable_retry_ran is False
        assert report.verdict == m.GateVerdict.PASS
        assert report.k_used == 20

    def test_uncallable_retry_raises_k_by_three_and_can_resolve_to_pass(self) -> None:
        """A 5-site case with zero false positives gives pooled n=25 at
        K=5 (Wilson lower bound ~0.867, below the 0.90 floor -> UNCALLABLE),
        and n=40 at K=8 after the +3 retry (lower bound ~0.912, clears it)."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"a.py", "b.py", "c.py", "d.py", "e.py"}))
        calls = {"count": 0}

        def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
            calls["count"] += 1
            family = m.ARM_EXPECTED_FAMILY[arm]
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=0.0,
                score=m.score_sample(tuple(sorted(case.key)), key=case.key),
            )

        report = m.run_recall_gate(
            (case,), k=5, cumulative_stop_usd=300.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert report.uncallable_retry_ran is True
        assert report.verdict == m.GateVerdict.PASS
        assert report.k_used == 5 + m.UNCALLABLE_K_INCREMENT
        assert calls["count"] == (5 + m.UNCALLABLE_K_INCREMENT) * len(m.ARMS)

    def test_uncallable_a_second_time_stays_uncallable(self) -> None:
        """A single-site case never pools enough n to clear the Wilson
        floor even after the +3 retry -- the plan's "not decidable within
        budget" outcome, distinct from a FAIL."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))

        def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
            family = m.ARM_EXPECTED_FAMILY[arm]
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=0.0,
                score=m.score_sample(("x.py",), key=case.key),
            )

        report = m.run_recall_gate(
            (case,), k=5, cumulative_stop_usd=300.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert report.uncallable_retry_ran is True
        assert report.verdict == m.GateVerdict.UNCALLABLE

    def test_cumulative_stop_during_first_batch_skips_the_uncallable_retry(self) -> None:
        """A batch cut short by the cumulative stop must not trigger a
        further retry -- the stop already ended the whole run."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, _calls = _scripted_launcher([msmr.MODEL_FAMILY_HAIKU] * 50, cost=100.0)

        report = m.run_recall_gate(
            (case,), k=5, cumulative_stop_usd=50.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert report.cumulative_stop_triggered is True
        assert report.uncallable_retry_ran is False

    def test_cumulative_stop_during_the_uncallable_retry_carries_over_starting_spend(self) -> None:
        """The retry batch's own cumulative-stop check must continue the
        first batch's realized spend, not reset to zero -- a reset would
        let the retry's own cost alone evade a ceiling the combined spend
        has already reached."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        calls = {"count": 0}

        def launcher(case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int) -> m.SampleAttempt:
            calls["count"] += 1
            # First batch: k=5 * len(ARMS)=2 arms = 10 calls at 1.0 each,
            # 10.0 total -- under the 15.0 ceiling. The retry's first call
            # alone costs 5.0, which only reaches the ceiling once added to
            # the 10.0 carried over from the first batch.
            cost = 1.0 if calls["count"] <= 10 else 5.0
            family = m.ARM_EXPECTED_FAMILY[arm]
            return m.SampleAttempt(
                observed_model_family=family, observed_model_ids=frozenset(), cost_usd=cost,
                score=m.score_sample(("x.py",), key=case.key),
            )

        report = m.run_recall_gate(
            (case,), k=5, cumulative_stop_usd=15.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
        )

        assert report.uncallable_retry_ran is True
        assert report.cumulative_stop_triggered is True
        assert report.realized_spend_usd == 15.0
        assert calls["count"] == 11  # 10 in the first batch, 1 in the retry before the stop fires

    def test_max_k_raises_zero_never_enters_the_retry_loop(self) -> None:
        """A smoke batch (K=1, max_k_raises=0) must stay UNCALLABLE rather
        than spending on a K-raise retry it never asked for."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, calls = _matching_family_launcher()

        report = m.run_recall_gate(
            (case,), k=1, cumulative_stop_usd=300.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
            max_k_raises=0,
        )

        assert report.uncallable_retry_ran is False
        assert report.verdict == m.GateVerdict.UNCALLABLE
        assert report.k_used == 1
        assert calls["count"] == 1 * len(m.ARMS)

    def test_max_k_raises_two_raises_k_twice_before_giving_up(self) -> None:
        """A single-site case never pools enough n to clear the Wilson floor
        (TestRunRecallGate's test_uncallable_a_second_time_stays_uncallable
        shows one raise isn't enough) -- max_k_raises=2 must spend a second
        raise rather than stopping after the first."""
        case = m.RecallCase(id="c1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        launcher, calls = _matching_family_launcher()

        report = m.run_recall_gate(
            (case,), k=5, cumulative_stop_usd=300.0, budget_cap_usd=1.0, timeout_s=1, launcher=launcher,
            max_k_raises=2,
        )

        assert report.uncallable_retry_ran is True
        assert report.verdict == m.GateVerdict.UNCALLABLE
        assert report.k_used == 5 + 2 * m.UNCALLABLE_K_INCREMENT
        assert calls["count"] == (5 + 2 * m.UNCALLABLE_K_INCREMENT) * len(m.ARMS)


# --- CLI -------------------------------------------------------------------------


class TestBuildLauncher:
    def test_sample_index_increments_independently_per_case_and_arm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_build_launcher is the actual seam main() uses -- its sample_index
        counter must key on (case.id, arm), not globally or per case only,
        or --keep-artifacts DIR/<case>-<arm>-<sample>/ directories silently
        misname or collide."""
        seen: list[tuple[str, str, int]] = []

        def fake_execute_one_sample_attempt(
            case: m.RecallCase, arm: str, *, budget_cap_usd: float, timeout_s: int,
            repo_dir: Path, keep_artifacts_dir: Path | None, sample_index: int,
        ) -> m.SampleAttempt:
            seen.append((case.id, arm, sample_index))
            return m.SampleAttempt(
                observed_model_family=m.ARM_EXPECTED_FAMILY[arm], observed_model_ids=frozenset(), cost_usd=0.0,
                score=m.score_sample((), key=case.key),
            )

        monkeypatch.setattr(m, "execute_one_sample_attempt", fake_execute_one_sample_attempt)
        launcher = m._build_launcher(repo_dir=Path("/repo"), keep_artifacts_dir=None)
        case_a = m.RecallCase(id="a1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))
        case_b = m.RecallCase(id="b1", stratum=m.STRATUM_A, question="q", key=frozenset({"x.py"}))

        launcher(case_a, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=1.0, timeout_s=1)
        launcher(case_a, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=1.0, timeout_s=1)
        launcher(case_a, msmr.DISPATCH_EXPLORE_SONNET, budget_cap_usd=1.0, timeout_s=1)
        launcher(case_b, msmr.DISPATCH_EXPLORE_HAIKU, budget_cap_usd=1.0, timeout_s=1)

        assert seen == [
            ("a1", msmr.DISPATCH_EXPLORE_HAIKU, 0),
            ("a1", msmr.DISPATCH_EXPLORE_HAIKU, 1),
            ("a1", msmr.DISPATCH_EXPLORE_SONNET, 0),
            ("b1", msmr.DISPATCH_EXPLORE_HAIKU, 0),
        ]


class TestMain:
    def _fixture_path(self, tmp_path: Path) -> Path:
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps(_valid_fixture_data()))
        return path

    def test_dry_run_without_repo_dir_still_works(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture_path = self._fixture_path(tmp_path)
        monkeypatch.setattr(sys, "argv", ["measure_explore_recall.py", "--fixtures", str(fixture_path), "--dry-run"])

        assert m.main() == 0

    def test_dry_run_with_a_mismatched_repo_dir_still_validates_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validation runs even under --dry-run when --repo-dir is given --
        it's cheap and catches a stale checkout before any real spend."""
        fixture_path = self._fixture_path(tmp_path)
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "x = 1\n")
        _commit(repo, "initial")
        monkeypatch.setattr(sys, "argv", [
            "measure_explore_recall.py", "--fixtures", str(fixture_path), "--dry-run",
            "--repo-dir", str(repo),
        ])

        with pytest.raises(SystemExit, match="does not start with the fixture's pinned_commit"):
            m.main()

    def test_symlinked_repo_dir_resolves_before_add_dir_prompt_and_answer_normalization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On macOS a `/tmp/...` --repo-dir argument and a model-reported
        `/private/tmp/...` path must normalize to the same relative path --
        main() resolves args.repo_dir once, before it threads into the
        --add-dir grant and the prompt (checked here via --dry-run) and, for
        a live run, into answer normalization (checked directly against the
        same resolved value, since driving a live sample through main()
        would require mocking msmr's subprocess layer end to end)."""
        real_repo = tmp_path / "real_repo"
        _init_repo(real_repo)
        _write(real_repo, "a.py", "x = 1\n")
        pinned_commit = _commit(real_repo, "initial")
        resolved = real_repo.resolve()
        symlinked_repo = tmp_path / "repo_symlink"
        symlinked_repo.symlink_to(real_repo)

        fixture_data = _valid_fixture_data()
        fixture_data["pinned_commit"] = pinned_commit
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(fixture_data))
        plan_path = tmp_path / "plan.json"
        monkeypatch.setattr(sys, "argv", [
            "measure_explore_recall.py", "--fixtures", str(fixture_path), "--dry-run",
            "--repo-dir", str(symlinked_repo), "--cases", "a0", "--json", str(plan_path),
        ])

        assert m.main() == 0

        plan = json.loads(plan_path.read_text())
        assert plan
        for entry in plan:
            assert entry["command"][entry["command"].index("--add-dir") + 1] == str(resolved)
            assert f"The repository to search is the directory {resolved}." in entry["prompt"]

        # The resolved path is exactly what a live run threads into answer
        # normalization too (execute_one_sample_attempt -> read_final_answer),
        # so an entry a model returns prefixed with that same resolved path
        # must strip to the relative form.
        assert m.normalize_entry(f"{resolved}/a.py", repo_dir=resolved) == "a.py"

    def test_a_live_run_without_repo_dir_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture_path = self._fixture_path(tmp_path)
        monkeypatch.setattr(sys, "argv", ["measure_explore_recall.py", "--fixtures", str(fixture_path)])

        with pytest.raises(SystemExit, match="--repo-dir is required"):
            m.main()

    def test_cases_flag_rejects_an_unknown_case_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture_path = self._fixture_path(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "measure_explore_recall.py", "--fixtures", str(fixture_path), "--dry-run", "--cases", "no-such-case",
        ])

        with pytest.raises(SystemExit, match="unknown case id"):
            m.main()

    def test_a_live_run_exits_on_the_verdict_and_writes_a_json_report_that_round_trips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Drives main()'s live-launch branch end to end with a stubbed
        subprocess layer, the same technique TestExecuteOneSampleAttempt
        uses -- the one production path real money has already run through
        with no prior direct test coverage (_build_launcher construction,
        run_recall_gate, the exit-code mapping, and the --json write through
        gate_report_to_dict). No --keep-artifacts flag is passed here, so
        that resolution path stays uncovered by this test."""
        fixture_data = _valid_fixture_data()
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "path/to/file_0_0.py", "x = 1\n")
        pinned_commit = _commit(repo, "initial")
        fixture_data["pinned_commit"] = pinned_commit
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(fixture_data))
        json_path = tmp_path / "report.json"

        def fake_run_claude(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
            requested_model = json.loads(cmd[cmd.index("--agents") + 1])["Explore"]["model"]
            session_id = cmd[cmd.index("--session-id") + 1]
            subagent_dir = msmr.run_skill_evals.compute_session_store_dir(cwd) / session_id / msmr.SUBAGENT_SUBDIR
            subagent_dir.mkdir(parents=True)
            (subagent_dir / "agent-1.meta.json").write_text(
                json.dumps({"toolUseId": "toolu_1", "agentType": "Explore"})
            )
            answer_record = {
                "type": "assistant",
                "message": {
                    "model": f"claude-{requested_model}-4-5-20251001",
                    "content": [{"type": "text", "text": '```json\n["path/to/file_0_0.py"]\n```'}],
                },
            }
            _write_jsonl(subagent_dir / "agent-1.jsonl", [answer_record])
            lines = [
                json.dumps(
                    {"event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Task"}}}
                ).encode()
            ]
            return lines, False

        monkeypatch.setattr(msmr, "_run_claude_to_completion", fake_run_claude)
        monkeypatch.setattr(sys, "argv", [
            "measure_explore_recall.py", "--fixtures", str(fixture_path), "--repo-dir", str(repo),
            "--cases", "a0", "--k", "1", "--max-k-raises", "0", "--json", str(json_path),
        ])

        exit_code = m.main()

        report = json.loads(json_path.read_text())
        assert set(report) == {
            "verdict", "repo_dir", "pinned_commit", "haiku", "sonnet", "realized_spend_usd",
            "cumulative_stop_triggered", "uncallable_retry_ran", "k_used", "samples",
        }
        # k=1 pools n=1 non-near-miss item, far below the Wilson lower bound's
        # n>=35 floor at zero false positives, so this stub always lands UNCALLABLE.
        assert report["verdict"] == m.GateVerdict.UNCALLABLE.value
        assert exit_code == 1

    def test_cases_flag_restricts_a_dry_run_to_the_named_cases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixture_path = self._fixture_path(tmp_path)
        plan_path = tmp_path / "plan.json"
        monkeypatch.setattr(sys, "argv", [
            "measure_explore_recall.py", "--fixtures", str(fixture_path), "--dry-run",
            "--cases", "a0,b0", "--json", str(plan_path),
        ])

        assert m.main() == 0

        plan = json.loads(plan_path.read_text())
        assert {entry["case_id"] for entry in plan} == {"a0", "b0"}
