"""Tests for the read-cost report: skipped-input counts, sections and printed figures."""
import os
from collections import Counter

import pytest
from transcript_analysis import corpus, pricing, read_cost, subagent_mix

from ._read_cost_helpers import (
    _TRACKED_AGENT_TYPE,
    _UNTRACKED_AGENT_TYPE,
    _read_cost_args,
    _read_exchange,
    _sidechain_copies,
    _turn,
    _weights,
)
from .conftest import (
    _edit_use,
    _read_result,
    _read_use,
    _write_jsonl,
    _write_subagent_dispatch,
)


@pytest.fixture()
def only_the_tracked_agent_type_is_tracked(stub_repo_tracked_agent_types):
    stub_repo_tracked_agent_types(_TRACKED_AGENT_TYPE)


_SKIPPED_INPUT_KEYS = (
    "unreadable_roots", "unreadable_project_dirs", "unreadable_main_files", "unreadable_subagent_dirs",
    "unreadable_subagent_files", "unreadable_subagent_meta", "invalid_usage_turns",
)
_NO_SKIPPED_INPUT = dict.fromkeys(_SKIPPED_INPUT_KEYS, 0)


def _skipped_input_counts(report: str) -> dict[str, int]:
    line = next(line for line in report.splitlines() if line.startswith("skipped input:"))
    return {key: int(value.replace(",", "")) for key, value in (token.split("=") for token in line.split()[2:])}


def _lines_after(report: str, header: str, count: int) -> list[str]:
    lines = report.splitlines()
    start = lines.index(header) + 1
    return lines[start:start + count]


@pytest.mark.usefixtures("only_the_tracked_agent_type_is_tracked")
class TestSkippedInput:
    def _run(self, capsys) -> str:
        read_cost.cmd_read_cost(_read_cost_args())
        return capsys.readouterr().out

    def test_clean_corpus_reports_zero_for_every_count(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1", usage={"output_tokens": 1})])
        assert _skipped_input_counts(self._run(capsys)) == _NO_SKIPPED_INPUT

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_projects_directory_is_counted_as_an_unreadable_root(self, fake_projects, capsys):
        projects_dir = fake_projects.parent
        os.chmod(projects_dir, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(projects_dir, 0o755)  # restore before tmp_path teardown
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_roots": 1}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_project_directory_is_counted(self, fake_projects, capsys):
        os.chmod(fake_projects, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(fake_projects, 0o755)
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_project_dirs": 1}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_main_transcript_is_counted(self, fake_projects, capsys):
        main_transcript = fake_projects / "sess.jsonl"
        _write_jsonl(main_transcript, [_turn("m1")])
        os.chmod(main_transcript, 0o000)
        try:
            out = self._run(capsys)
        finally:
            os.chmod(main_transcript, 0o644)
        assert _skipped_input_counts(out) == {**_NO_SKIPPED_INPUT, "unreadable_main_files": 1}
        assert "sessions=0 " in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unlistable_subagent_directory_is_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", _sidechain_copies([_turn("s1")]))
        subagent_dir = fake_projects / "sess" / "subagents"
        os.chmod(subagent_dir, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(subagent_dir, 0o755)
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_subagent_dirs": 1}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_subagent_transcript_is_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", _sidechain_copies([_turn("s1")]))
        subagent_transcript = fake_projects / "sess" / "subagents" / "agent-a.jsonl"
        os.chmod(subagent_transcript, 0o000)
        try:
            counts = _skipped_input_counts(self._run(capsys))
        finally:
            os.chmod(subagent_transcript, 0o644)
        assert counts == {**_NO_SKIPPED_INPUT, "unreadable_subagent_files": 1}

    def test_unreadable_meta_is_counted_beside_the_withheld_row_its_agent_folds_into(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", _sidechain_copies([_turn("s1")]))
        (fake_projects / "sess" / "subagents" / "agent-a.meta.json").write_text("not json")

        out = self._run(capsys)

        assert _skipped_input_counts(out) == {**_NO_SKIPPED_INPUT, "unreadable_subagent_meta": 1}
        withheld_row = f"{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}: agents=1 "
        withheld_index = next(i for i, line in enumerate(out.splitlines()) if line.startswith(withheld_row))
        assert out.splitlines()[withheld_index + 2] == (
            "    also holds agents with a missing, unreadable or typeless .meta.json; unreadable: 1"
        )
        assert out.count("also holds agents") == 1

    def test_missing_meta_is_not_counted_as_unreadable(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", _sidechain_copies([_turn("s1")]))
        (fake_projects / "sess" / "subagents" / "agent-a.meta.json").unlink()

        assert _skipped_input_counts(self._run(capsys)) == _NO_SKIPPED_INPUT

    def test_turn_with_invalid_usage_is_counted_and_not_billed(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _turn("m1", usage={"output_tokens": -1}), _turn("m2", usage={"output_tokens": 1}),
        ])

        out = self._run(capsys)

        assert _skipped_input_counts(out) == {**_NO_SKIPPED_INPUT, "invalid_usage_turns": 1}
        assert "sessions=1 api_calls=1" in out


@pytest.mark.usefixtures("only_the_tracked_agent_type_is_tracked")
class TestReport:
    _SESSION_ID = "sentinel-session-id"
    _MAIN_PATH = "/sentinel/dir/big.py"
    _OTHER_PATH = "/sentinel/dir/other.py"
    _TRACKED_AGENT_ID = "agent-sentinel-tracked"
    _UNTRACKED_AGENT_ID = "agent-sentinel-untracked"
    _TRACKED_DISPATCH_ID = "sentinel-dispatch-tracked"
    _UNTRACKED_DISPATCH_ID = "sentinel-dispatch-untracked"
    _MESSAGE_IDS = ("sentinel-msg-main-1", "sentinel-msg-main-2", "sentinel-msg-tracked", "sentinel-msg-untracked")
    _READ_IDS = ("sentinel-read-main", "sentinel-read-tracked", "sentinel-read-untracked")
    _EDIT_ID = "sentinel-edit-main"

    def _write_corpus(self, fake_projects) -> None:
        main_turn_1, main_turn_2, tracked_turn, untracked_turn = self._MESSAGE_IDS
        main_read, tracked_read, untracked_read = self._READ_IDS
        main_records = [
            _turn(main_turn_1, [_read_use(main_read, file_path=self._MAIN_PATH)], usage={"output_tokens": 10}),
            _read_result(main_read, "line\n" * 1000),
            _turn(main_turn_2, [_edit_use(self._EDIT_ID, path=self._MAIN_PATH)], usage={"output_tokens": 10}),
        ]
        _write_jsonl(fake_projects / f"{self._SESSION_ID}.jsonl", main_records)
        _write_subagent_dispatch(
            fake_projects, self._SESSION_ID, self._TRACKED_AGENT_ID, self._TRACKED_DISPATCH_ID,
            _sidechain_copies([
                _turn(tracked_turn, [_read_use(tracked_read, file_path=self._MAIN_PATH)], usage={"output_tokens": 10}),
                _read_result(tracked_read, "line\n" * 1000),
            ]),
            agent_type=_TRACKED_AGENT_TYPE,
        )
        _write_subagent_dispatch(
            fake_projects, self._SESSION_ID, self._UNTRACKED_AGENT_ID, self._UNTRACKED_DISPATCH_ID,
            _sidechain_copies([
                _turn(
                    untracked_turn, [_read_use(untracked_read, file_path=self._OTHER_PATH)],
                    usage={"output_tokens": 10},
                ),
                _read_result(untracked_read, "line\n" * 1000),
            ]),
            agent_type=_UNTRACKED_AGENT_TYPE,
        )

    def test_report_prints_the_price_ratio_note_and_aggregates_without_identifiers(
        self, fake_projects, capsys,
    ):
        self._write_corpus(fake_projects)

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "One price ratio" in out
        assert "sessions=1 api_calls=4" in out
        assert "## Main-thread whole-file reads over 800 lines: 1" in out
        assert "edited_later=True: n=1" in out
        withheld_identifiers = (
            os.path.basename(self._MAIN_PATH), os.path.basename(self._OTHER_PATH), _UNTRACKED_AGENT_TYPE,
        )
        for withheld_identifier in withheld_identifiers:
            assert withheld_identifier not in out
        assert "sentinel" not in out
        assert fake_projects.name not in out
        assert str(fake_projects.parent.parent) not in out

    def test_tracked_agent_type_prints_by_name_and_untracked_folds_into_the_withheld_row(
        self, fake_projects, capsys,
    ):
        self._write_corpus(fake_projects)

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert f"\n{_TRACKED_AGENT_TYPE}: agents=1 " in out
        assert f"\n{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}: agents=1 " in out
        assert "\nTOTAL: agents=2 " in out
        withheld_row = f"{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}: agents=1 "
        withheld_index = next(i for i, line in enumerate(out.splitlines()) if line.startswith(withheld_row))
        assert out.splitlines()[withheld_index + 2] == (
            "    also holds agents with a missing, unreadable or typeless .meta.json; unreadable: 0"
        )
        assert out.count("also holds agents") == 1

    def test_agent_type_named_only_by_the_allowlist_seam_prints_by_name(
        self, fake_projects, stub_repo_tracked_agent_types, capsys,
    ):
        allowlist_only_type = "sentinel-allowlist-only-agent-type"
        stub_repo_tracked_agent_types(allowlist_only_type)
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a", "tool-1", _sidechain_copies([_turn("s1")]), agent_type=allowlist_only_type,
        )

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert f"\n{allowlist_only_type}: agents=1 " in out
        assert f"\n{subagent_mix._WITHHELD_AGENT_TYPE_LABEL}:" not in out
        assert "also holds agents" not in out

    def test_empty_corpus_prints_zero_shares_without_division_error(self, fake_projects, capsys):
        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "sessions=0 api_calls=0" in out
        assert "billed share: main=0.0% subagent=0.0%" in out
        assert "edited_later=False: n=0 tokens=0 median_remaining_turns=0" in out
        assert "\nTOTAL: agents=0 " in out

    def test_resent_session_reports_the_repeated_reads_it_skipped(self, fake_projects, capsys):
        resent_records = _read_exchange("m1", "r1", "/a.py")
        _write_jsonl(fake_projects / "sess-a.jsonl", resent_records)
        _write_jsonl(fake_projects / "sess-b.jsonl", resent_records)

        read_cost.cmd_read_cost(_read_cost_args())

        out = capsys.readouterr().out
        assert "sessions=2 " in out
        assert "main-thread whole-file reads scanned=1\n" in out
        sessions_line = next(line for line in out.splitlines() if line.startswith("sessions="))
        assert sessions_line.endswith(" repeated_reads_skipped=1")

    def test_command_parses_no_subagent_transcript_before_the_session_starts_accumulating(
        self, fake_projects, monkeypatch,
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [_turn("m1")])
        _write_subagent_dispatch(fake_projects, "sess", "agent-a", "tool-1", _sidechain_copies([_turn("s1")]))
        _write_subagent_dispatch(fake_projects, "sess", "agent-b", "tool-2", _sidechain_copies([_turn("s2")]))
        subagent_files_parsed: list[str] = []
        subagent_files_parsed_at_session_start: list[int] = []
        parse_jsonl_records = corpus._parse_jsonl_records
        accumulate_session = read_cost._accumulate_session

        def recording_parse(path):
            if path.parent.name == corpus.SUBAGENT_SUBDIR:
                subagent_files_parsed.append(path.name)
            return parse_jsonl_records(path)

        def spy(totals, seen, main_records, subagent_windows, weights):
            subagent_files_parsed_at_session_start.append(len(subagent_files_parsed))
            return accumulate_session(totals, seen, main_records, subagent_windows, weights)

        monkeypatch.setattr(corpus, "_parse_jsonl_records", recording_parse)
        monkeypatch.setattr(read_cost, "_accumulate_session", spy)

        read_cost.cmd_read_cost(_read_cost_args())

        assert subagent_files_parsed_at_session_start == [0]
        assert sorted(subagent_files_parsed) == ["agent-a.jsonl", "agent-b.jsonl"]

    def test_header_prints_the_unit_and_class_weights_derived_from_pricing_rates(self, capsys):
        model = "claude-sonnet-4-6"
        assert model not in pricing._CACHE_READ_MULTIPLIER_OVERRIDES
        rates = pricing._model_rates(model)
        write_weight = rates["cache_write_5m"] / rates["cache_read"]
        class_weights = ", ".join(f"{cls}={rates[cls] / rates['cache_read']:g}" for cls in pricing._TOKEN_CLASSES)

        read_cost._print_read_cost_report(read_cost._ReadCostTotals(), _weights(), frozenset({_TRACKED_AGENT_TYPE}))

        out_lines = capsys.readouterr().out.splitlines()
        assert (
            "Unit: cache-read-token equivalents. A read's weight is its estimated tokens"
            f" (4 chars/token) times one 5-minute cache write ({write_weight:g}) plus one"
            " cache read (1) per later turn in its context window."
        ) in out_lines
        assert f"Class weights: {class_weights}." in out_lines


@pytest.mark.usefixtures("only_the_tracked_agent_type_is_tracked")
class TestPrintedFigures:
    """Shares and denominators over a hand-computed fixture: total billed 75000, main billed 50000.

    A main-thread whole-file read of `r` remaining turns and 200 tokens weighs 200 * (12.5 + r).
    """

    @staticmethod
    def _main_read(lines: int, remaining_turns: int, *, edited_later: bool) -> read_cost._ReadEvent:
        return read_cost._ReadEvent(
            path="/p.py", whole_file=True, tokens=200, lines=lines,
            remaining_turns=remaining_turns, edited_later=edited_later,
        )

    def _print(self, totals: read_cost._ReadCostTotals, tracked: frozenset[str], capsys) -> str:
        read_cost._print_read_cost_report(totals, _weights(), tracked)
        return capsys.readouterr().out

    def _hand_computed_totals(self) -> read_cost._ReadCostTotals:
        totals = read_cost._ReadCostTotals(billed_main=50000.0, billed_subagent=25000.0)
        totals.main_whole_file_events = [
            self._main_read(350, 2, edited_later=False),
            self._main_read(351, 4, edited_later=False),
            self._main_read(800, 1, edited_later=True),
            self._main_read(801, 3, edited_later=True),
            self._main_read(801, 6, edited_later=False),
        ]
        totals.rows_by_agent_type = {
            _TRACKED_AGENT_TYPE: read_cost._AgentTypeRow(
                agents=2, turns=10, billed=7500.0, whole_file_reads=3, whole_file_tokens=600,
                whole_file_weight=3000.0, all_read_weight=4500.0, large_weight=1500.0,
                large_unedited_weight=750.0, repeat_in_agent_weight=450.0, seen_elsewhere_weight=600.0,
                kind_weight=Counter({read_cost._KIND_INSTRUCTION: 1200.0, read_cost._KIND_CODE_OR_OTHER: 1800.0}),
            ),
        }
        return totals

    def test_billed_share_splits_main_from_subagent_over_total_billed(self, capsys):
        out = self._print(self._hand_computed_totals(), frozenset({_TRACKED_AGENT_TYPE}), capsys)
        assert "billed share: main=66.7% subagent=33.3%" in out

    def test_cohort_over_350_lines_excludes_exactly_350_and_splits_by_edited_later(self):
        totals = self._hand_computed_totals()

        cohort = read_cost._cohort_figures(
            totals.main_whole_file_events, 350, _weights(), totals.billed_total, totals.billed_main,
        )

        assert cohort.reads == 4
        assert cohort.edited_later.reads == 2
        assert cohort.edited_later.tokens == 400
        assert cohort.edited_later.median_remaining_turns == 3
        assert cohort.edited_later.share_of_total_pct == pytest.approx(100 * 5800 / 75000)
        assert cohort.edited_later.share_of_main_pct == pytest.approx(11.6)
        assert cohort.never_edited.reads == 2
        assert cohort.never_edited.tokens == 400
        assert cohort.never_edited.median_remaining_turns == 6
        assert cohort.never_edited.share_of_total_pct == pytest.approx(100 * 7000 / 75000)
        assert cohort.never_edited.share_of_main_pct == pytest.approx(14.0)

    def test_cohort_over_800_lines_excludes_exactly_800_and_splits_by_edited_later(self):
        totals = self._hand_computed_totals()

        cohort = read_cost._cohort_figures(
            totals.main_whole_file_events, 800, _weights(), totals.billed_total, totals.billed_main,
        )

        assert cohort.reads == 2
        assert cohort.edited_later.reads == 1
        assert cohort.edited_later.tokens == 200
        assert cohort.edited_later.median_remaining_turns == 3
        assert cohort.edited_later.share_of_total_pct == pytest.approx(100 * 3100 / 75000)
        assert cohort.edited_later.share_of_main_pct == pytest.approx(6.2)
        assert cohort.never_edited.reads == 1
        assert cohort.never_edited.tokens == 200
        assert cohort.never_edited.median_remaining_turns == 6
        assert cohort.never_edited.share_of_total_pct == pytest.approx(100 * 3700 / 75000)
        assert cohort.never_edited.share_of_main_pct == pytest.approx(7.4)

    def test_prints_exactly_the_350_and_800_line_cohorts_in_that_order(self, capsys):
        out = self._print(self._hand_computed_totals(), frozenset({_TRACKED_AGENT_TYPE}), capsys)

        cohort_headers = [line for line in out.splitlines() if line.startswith("## Main-thread whole-file reads over")]
        assert cohort_headers == [
            "## Main-thread whole-file reads over 350 lines: 4",
            "## Main-thread whole-file reads over 800 lines: 2",
        ]

    def test_cohort_with_no_reads_has_zero_figures(self):
        cohort = read_cost._cohort_figures([], 350, _weights(), 0.0, 0.0)

        assert cohort.reads == 0
        for subset in (cohort.edited_later, cohort.never_edited):
            assert subset.reads == 0
            assert subset.tokens == 0
            assert subset.median_remaining_turns == 0
            assert subset.share_of_total_pct == 0.0
            assert subset.share_of_main_pct == 0.0

    def test_agent_type_figures_divide_each_weight_by_its_denominator(self):
        row = self._hand_computed_totals().rows_by_agent_type[_TRACKED_AGENT_TYPE]

        figures = read_cost._agent_type_figures(row, 75000.0)

        assert figures == read_cost._AgentTypeFigures(
            agents=2,
            whole_file_reads=3,
            whole_file_tokens=600,
            mean_turns=5.0,
            share_of_total_spend_pct=pytest.approx(10.0),
            whole_file_weight_share_pct=pytest.approx(4.0),
            all_read_weight_share_pct=pytest.approx(6.0),
            large_pct=pytest.approx(50.0),
            large_unedited_pct=pytest.approx(25.0),
            repeat_in_agent_pct=pytest.approx(15.0),
            seen_elsewhere_pct=pytest.approx(20.0),
            kind_pcts={
                read_cost._KIND_CODE_OR_OTHER: pytest.approx(60.0), read_cost._KIND_INSTRUCTION: pytest.approx(40.0),
            },
        )

    def test_agent_type_with_no_agents_and_no_reads_has_zero_figures(self):
        figures = read_cost._agent_type_figures(read_cost._AgentTypeRow(), 0.0)

        assert figures == read_cost._AgentTypeFigures(
            agents=0,
            whole_file_reads=0,
            whole_file_tokens=0,
            mean_turns=0.0,
            share_of_total_spend_pct=0.0,
            whole_file_weight_share_pct=0.0,
            all_read_weight_share_pct=0.0,
            large_pct=0.0,
            large_unedited_pct=0.0,
            repeat_in_agent_pct=0.0,
            seen_elsewhere_pct=0.0,
            kind_pcts={},
        )

    def test_printed_cohort_and_agent_type_lines_keep_their_published_layout(self, capsys):
        out = self._print(self._hand_computed_totals(), frozenset({_TRACKED_AGENT_TYPE}), capsys)

        assert (
            "\n## Main-thread whole-file reads over 800 lines: 2\n"
            "  edited_later=True: n=1 tokens=200 median_remaining_turns=3 share_of_total=4.1% share_of_main=6.2%\n"
            "  edited_later=False: n=1 tokens=200 median_remaining_turns=6 share_of_total=4.9% share_of_main=7.4%\n"
        ) in out
        assert (
            f"\n{_TRACKED_AGENT_TYPE}: agents=2 mean_turns=5 share_of_total_spend=10.0% whole_file_reads=3"
            " whole_file_tokens=600 whole_file_weight_share=4.0% all_read_weight_share=6.0%\n"
            "    of whole: large=50.0% large_unedited=25.0% repeat_in_agent=15.0% seen_elsewhere=20.0%"
            " kinds: code/other=60.0% instruction=40.0%\n"
        ) in out

    def test_every_tracked_agent_type_prints_a_row(self, capsys):
        tracked_names = [f"tracked-agent-{index:02d}" for index in range(20)]
        totals = read_cost._ReadCostTotals(billed_main=1.0)
        totals.rows_by_agent_type = {
            name: read_cost._AgentTypeRow(agents=1, whole_file_weight=float(index))
            for index, name in enumerate(tracked_names)
        }

        out = self._print(totals, frozenset(tracked_names), capsys)

        assert all(f"\n{name}: agents=1 " in out for name in tracked_names)

    def test_size_bucket_table_prints_each_scope_and_label_cell(self, capsys):
        totals = read_cost._ReadCostTotals(billed_main=1.0)
        cell_counts = {
            (read_cost._SCOPE_MAIN, "<2000"): 1, (read_cost._SCOPE_MAIN, "2000-4999"): 2,
            (read_cost._SCOPE_MAIN, "5000+"): 3, (read_cost._SCOPE_SUBAGENT, "<2000"): 4,
            (read_cost._SCOPE_SUBAGENT, "2000-4999"): 5, (read_cost._SCOPE_SUBAGENT, "5000+"): 6,
        }
        for key, count in cell_counts.items():
            totals.bucket_calls[key] = count
            totals.bucket_tokens[key] = count * 1000

        out = self._print(totals, frozenset(), capsys)

        assert _lines_after(out, "## Whole-file reads by size bucket (est. tokens)", 6) == [
            "  main      <2000      calls=1 tokens=1,000",
            "  main      2000-4999  calls=2 tokens=2,000",
            "  main      5000+      calls=3 tokens=3,000",
            "  subagent  <2000      calls=4 tokens=4,000",
            "  subagent  2000-4999  calls=5 tokens=5,000",
            "  subagent  5000+      calls=6 tokens=6,000",
        ]

    def test_tracked_agent_types_print_by_descending_whole_file_weight_then_name(self, capsys):
        totals = read_cost._ReadCostTotals(billed_main=1.0)
        totals.rows_by_agent_type = {
            "b-tie": read_cost._AgentTypeRow(agents=1, whole_file_weight=3.0),
            "light": read_cost._AgentTypeRow(agents=1, whole_file_weight=1.0),
            "a-tie": read_cost._AgentTypeRow(agents=1, whole_file_weight=3.0),
            "heavy": read_cost._AgentTypeRow(agents=1, whole_file_weight=5.0),
        }

        out = self._print(totals, frozenset(totals.rows_by_agent_type), capsys)

        printed_labels = [line.split(":")[0] for line in out.splitlines() if ": agents=" in line]
        assert printed_labels == ["TOTAL", "heavy", "a-tie", "b-tie", "light"]
