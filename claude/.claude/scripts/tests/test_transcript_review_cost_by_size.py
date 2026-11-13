"""Tests for transcript_analysis/review_cost_by_size.py (review-cost-by-size).

Aggregates are tested over literal records. The command is tested over pr-cost ledger files, with `subprocess.run`
faked so that any `gh` call, and any git call beyond the origin lookup, shows in the recorded argv.
"""
import argparse
import re
import subprocess
from pathlib import Path

import pytest
from helpers import REPO_ROOT, heading_texts, normalize_heading
from transcript_analysis import cli, ledger_rounds, pr_cost_ledger, review_cost_by_size, review_rounds, scope

from ._pr_cost_helpers import (
    _LEDGER_DIR_MARKER,
    _REPRICED_ROUND_MODEL,
    _fake_pr_cost_subprocess_run,
    _ledger_pr_row,
    _review_rounds_cell,
    _round_with_priced_splits,
)
from .conftest import _option_help, _priced, _skill_block, _two_declared_roots, _user_msg, _write_jsonl

# Module-level so test_select_tests.py's TestCrossDomainReadCompleteness sees this read.
_PRIVATE_PROJECT_REDACTION_DOC = REPO_ROOT / "docs" / "private-project-redaction.md"
_TRANSCRIPT_ANALYSIS_DOC = REPO_ROOT / "docs" / "transcript-analysis.md"
_PR_COST_DOC = REPO_ROOT / "docs" / "pr-cost.md"

_HEADER_ACTIVE_ACCOUNT = (
    "REVIEW COST BY SIZE SOURCES (pr-cost ledger rows for this checkout's origin; active account only)"
)
_HEADER_EVERY_ACCOUNT = (
    "REVIEW COST BY SIZE SOURCES (pr-cost ledger rows for this checkout's origin; every declared account)"
)
_NOTICE_TEXT = (
    "Scope unverified -- a pr-cost ledger row does not record which project directories its --record run read, so a"
    " PR's rounds can include another repository's session on a same-named branch; see docs/transcript-analysis.md"
    " § \"review-cost-by-size\"."
)
_HEADER_LEDGER_PATH_OVERRIDE = (
    "REVIEW COST BY SIZE SOURCES (pr-cost ledger rows for this checkout's origin; PR_COST_LEDGER_PATH file,"
    " account scope unverified)"
)
_NO_PR_REPORTED_TEXT = (
    "No PR reported. A row recorded before the review_rounds column needs pr-cost --record --force --pr N to refill"
    " it. That re-capture is safe only while every session of that PR is inside cleanupPeriodDays; outside it, it"
    " permanently understates the PR's row. The ledger diagnostics below count the PRs"
    " left out; see docs/transcript-analysis.md § \"review-cost-by-size\"."
)
_NO_PR_RECORDED_TEXT = (
    "No PR reported. The ledger read above has no row for this repository. Run pr-cost --record to capture merged PRs"
    " past the as-of window; it needs the pr_cost_recording config key (see docs/pr-cost.md § \"Data\")."
)
_ORIGIN_LOOKUP_ONLY = [["git", "remote", "get-url", "origin"]]

# The origin identity every ledger row and the faked `git remote get-url origin` carry. Each part is its own sentinel.
_ORIGIN_HOST = "sentinel-host.example"
_ORIGIN_OWNER = "sentinel-org"
_ORIGIN_REPO_NAME = "sentinel-repo"
_ORIGIN_REPO = f"{_ORIGIN_OWNER}/{_ORIGIN_REPO_NAME}"
# A row for another repository on the origin host, and a row for the origin repository's slug on another host.
_OTHER_HOST = "zz-other-host.example"
_OTHER_OWNER = "zz-other-org"
_OTHER_REPO_NAME = "zz-other-repo"
_ESC = "\x1b"
# Distinctive sentinels that satisfy the ledger's key grammar and cannot collide with report text.
_UNPRICED_MODEL_SENTINEL = "zzunpricedmodelsentinel"
_UNKNOWN_SKILL_SENTINEL = "zzunknownskillsentinel"
_UNKNOWN_ROUND_KEY_SENTINEL = "zzunknownroundkeysentinel"
_OVERRIDE_BASENAME = "zzoverridesentinel.tsv"
# 100_000 input tokens on _REPRICED_ROUND_MODEL at its base input rate of $2.00 per million (pricing._MODEL_BASE_INPUT_RATES).
_DOLLARS_PER_100K_INPUT_TOKENS = 0.20


def _dollars_text(input_tokens_in_100k: float) -> str:
    """The report's dollar text for a price of `input_tokens_in_100k` times 100_000 input tokens."""
    return f"${input_tokens_in_100k * _DOLLARS_PER_100K_INPUT_TOKENS:.2f}"


def _branch_token(pr_number: int) -> str:
    return f"zzbranchtoken{pr_number}"


def _head_branch(pr_number: int) -> str:
    """A head name carrying an ANSI escape, as a hostile ledger could supply. It holds no tab, newline or carriage
    return, which the ledger writer refuses."""
    return f"{_ESC}[31m{_branch_token(pr_number)}"


def _machine_label(pr_number: int) -> str:
    return f"zqm{pr_number}"


def _second_machine_label(pr_number: int) -> str:
    return f"zqn{pr_number}"


def _row_components(*pr_numbers: int) -> tuple[str, ...]:
    """The branch token and both machine labels of each seeded PR, none of which a report may print."""
    return tuple(
        component for pr_number in pr_numbers
        for component in (_branch_token(pr_number), _machine_label(pr_number), _second_machine_label(pr_number))
    )


def _ledger_round(*, main_input: int = 100_000, agent_input: int = 50_000, **overrides) -> dict:
    """A priceable round whose main and agent splits differ and whose stored micro-dollars differ from their price.

    The defaults hold 100_000 main input tokens and 50_000 agent input tokens, which price at 1.5 times
    _DOLLARS_PER_100K_INPUT_TOKENS, while the stored micro-dollars sum to 16.
    """
    return _round_with_priced_splits(
        {"standard": {"input": main_input}}, {"standard": {"input": agent_input}},
        **{
            "main_usd_micros": 7, "agent_usd_micros": 9,
            "unpriced_turns": 0, "dangling": 0, "invalid_usage_records": 0, "after_merge": 0,
            **overrides,
        },
    )


def _optional_code_review_cell(code_review: list[dict] | None, **other_skills: list[dict]) -> dict:
    """A cell whose code-review list is `code_review`, or with no code-review key at all when None."""
    return _review_rounds_cell(
        {"code-review": code_review, **other_skills} if code_review is not None else dict(other_skills)
    )


def _origin_ledger_row(pr_number: int, *, cell: dict | None, machine: str | None = None, **overrides) -> dict:
    return _ledger_pr_row(pr_number, machine=machine or _machine_label(pr_number), cell=cell, **{
        "host": _ORIGIN_HOST, "repo": _ORIGIN_REPO, "head_branch": _head_branch(pr_number),
        "additions": 3, "deletions": 2, **overrides,
    })


def _kept_row(pr_number: int, round_count: int = 1, **overrides) -> dict:
    cell = _optional_code_review_cell([_ledger_round() for _ in range(round_count)])
    return _origin_ledger_row(pr_number, cell=cell, **overrides)


def _write_account_ledger(account_dir: Path, rows: list[dict]) -> None:
    """Write `rows` to the ledger the command reads for the account whose `projects` directory is under `account_dir`."""
    pr_cost_ledger._write_pr_cost_ledger_file(
        pr_cost_ledger._pr_cost_ledger_path(config_dir_override=account_dir), rows,
    )


def _account_directory_names(roots: list[Path]) -> tuple[str, ...]:
    """The account directory names of roots returned by `_two_declared_roots`, each root being `<account>/projects`."""
    for root in roots:
        assert root.name == "projects", f"expected an <account>/projects root, got {root}"
    return tuple(root.parent.name for root in roots)


def _review_cost_by_size_args(
    *, projects: str = "*", this_repo: bool = False, all_accounts: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects, "this_repo": this_repo, "all_accounts": all_accounts, "config_dir": None,
    })()


def _review_cost_by_size_args_scoped_to_seeded_session(fake_projects: Path) -> object:
    """Args whose project scope already resolves to `fake_projects`, so a regression that reads transcripts finds the
    seeded session and fails on its figures rather than on the faked subprocess's refusal of the slug lookup."""
    args = _review_cost_by_size_args(this_repo=True)
    args._this_repo_slugs = [fake_projects.name]
    return args


def _write_code_review_session(session_path, branch: str, round_count: int) -> None:
    """One transcript session of `round_count` code-review rounds on `branch`, each priced at
    _DOLLARS_PER_100K_INPUT_TOKENS and closed by a fresh user prompt. The command ignores transcripts, so a figure
    from this session in a report is a defect."""
    records = []
    for round_index in range(round_count):
        minute = 2 * round_index
        records.append(_priced(
            _REPRICED_ROUND_MODEL, input=100_000, branch=branch, ts=f"2026-08-01T10:{minute:02d}:00.000Z",
            content=[_skill_block(f"skill-{round_index}", "code-review")],
        ))
        records.append(_user_msg("thanks", branch=branch, ts=f"2026-08-01T10:{minute + 1:02d}:00.000Z"))
    _write_jsonl(session_path, records)


def _run_cmd(monkeypatch, capsys, args, *, call_log=None):
    monkeypatch.setattr(
        subprocess, "run", _fake_pr_cost_subprocess_run(host=_ORIGIN_HOST, repo=_ORIGIN_REPO, call_log=call_log),
    )
    review_cost_by_size.cmd_review_cost_by_size(args)
    return capsys.readouterr()


def _run_report(monkeypatch, capsys, args) -> tuple[str, str]:
    """Run the command to a printed report, and assert it made no call beyond the origin lookup."""
    call_log: list[list[str]] = []
    out, err = _run_cmd(monkeypatch, capsys, args, call_log=call_log)
    assert call_log == _ORIGIN_LOOKUP_ONLY
    return out, err


def _printed_pr_numbers(stdout: str) -> list[int]:
    _, _, rows_section = stdout.partition("Per-PR rows")
    return [int(match.group(1)) for match in re.finditer(r"^\s+(\d+)\s+[\d,]+\s+\d+\s", rows_section, re.MULTILINE)]


def _count_line(out: str, label: str) -> int:
    """The count on the `  <label>: <n>` line of the diagnostics block."""
    matches = re.findall(rf"^  {re.escape(label)}: ([\d,]+)$", out, re.MULTILINE)
    assert len(matches) == 1, f"expected one {label!r} line, found {matches}"
    return int(matches[0].replace(",", ""))


def _assert_withheld(out: str, err: str, *components: str) -> None:
    for component in components:
        assert component not in out + err, f"{component!r} reached the report"


# The first alternative matches the digits that belong to a label: a size bucket's edges and the trend's rounds floor.
# The second matches a value slot.
_LABEL_OR_FIGURE_PATTERN = re.compile(
    r"(?P<label>^\s+\d+(?:-\d+|\+) lines:|at least \d+ rounds)|(?P<figure>n/a|-?\$?\d[\d,]*(?:\.\d+)?%?)"
)
_PER_PR_ROWS_HEADING = "Per-PR rows"


def _docs_sample_lines() -> list[str]:
    """The lines of the fenced block that follows the review-cost-by-size section's "Sample output" label."""
    text = _TRANSCRIPT_ANALYSIS_DOC.read_text(encoding="utf-8")
    section = text[text.index("\n## review-cost-by-size\n"):]
    after_label = section[section.index("**Sample output**"):]
    start = after_label.index("\n```\n") + len("\n```\n")
    return after_label[start:after_label.index("\n```", start)].splitlines()


def _label_lines_with_figures_masked(report_lines: list[str]) -> list[str]:
    """Every line through the per-PR table's column header, each value replaced by `#` and each label kept."""
    heading_index = next(index for index, line in enumerate(report_lines) if line.startswith(_PER_PR_ROWS_HEADING))
    return [
        _LABEL_OR_FIGURE_PATTERN.sub(lambda match: match["label"] or "#", line)
        for line in report_lines[: heading_index + 2]
    ]


class TestPrintPrRows:
    def test_rows_sort_by_lines_ascending_then_pr_number_ascending(self, capsys):
        records = [
            review_cost_by_size._PrRecord(number=number, lines=lines, round_dollars=(0.1,))
            for number, lines in ((98, 5), (97, 5), (60, 9), (99, 2))
        ]
        review_cost_by_size._print_pr_rows(records)
        assert _printed_pr_numbers(capsys.readouterr().out) == [99, 97, 98, 60]


# The PRs excluded for each reason. The counts differ, so a count printed under another reason's label fails.
_EXCLUDED_PR_NUMBERS: dict[str, tuple[int, ...]] = {
    ledger_rounds.EXCLUDED_NO_CELL: (21,),
    ledger_rounds.EXCLUDED_MULTI_ROW: (22, 23),
    ledger_rounds.EXCLUDED_JOIN_CONFIDENCE_LOW: (24, 25, 26),
    ledger_rounds.EXCLUDED_RULE: (27, 28, 29, 30),
    ledger_rounds.EXCLUDED_UNPRICEABLE_ROUND: (32, 33, 34, 35, 36),
}
_EXCLUDED_PR_COUNT = sum(len(numbers) for numbers in _EXCLUDED_PR_NUMBERS.values())
_NON_ORIGIN_PR_NUMBERS = (89, 90)
_ACTIVE_ACCOUNT_PR_NUMBERS = (11, 12, *(number for numbers in _EXCLUDED_PR_NUMBERS.values() for number in numbers))


def _excluded_cell(review_round: dict) -> dict:
    """A cell holding an after-merge round and then `review_round`, so an exclusion decided at `review_round` must not
    count the after-merge round ahead of it."""
    return _optional_code_review_cell([_ledger_round(after_merge=1), review_round])


def _excluded_rows() -> list[dict]:
    """The rows of `_EXCLUDED_PR_NUMBERS`, each carrying its own seeded `machine` and `head_branch`."""
    unpriced_round = _ledger_round(main_tokens={_UNPRICED_MODEL_SENTINEL: {"standard": {"input": 1}}})
    rows = [_origin_ledger_row(number, cell=None) for number in _EXCLUDED_PR_NUMBERS[ledger_rounds.EXCLUDED_NO_CELL]]
    for number in _EXCLUDED_PR_NUMBERS[ledger_rounds.EXCLUDED_MULTI_ROW]:
        rows.append(_origin_ledger_row(number, cell=_excluded_cell(_ledger_round()), machine=_machine_label(number)))
        rows.append(_origin_ledger_row(number, cell=_excluded_cell(_ledger_round()), machine=_second_machine_label(number)))
    for number in _EXCLUDED_PR_NUMBERS[ledger_rounds.EXCLUDED_JOIN_CONFIDENCE_LOW]:
        rows.append(_origin_ledger_row(
            number, cell=_excluded_cell(_ledger_round()), join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
        ))
    for number in _EXCLUDED_PR_NUMBERS[ledger_rounds.EXCLUDED_RULE]:
        rows.append(_origin_ledger_row(
            number, cell={**_excluded_cell(_ledger_round()), "rule": review_rounds.REVIEW_ROUND_RULE + 1},
        ))
    for number in _EXCLUDED_PR_NUMBERS[ledger_rounds.EXCLUDED_UNPRICEABLE_ROUND]:
        rows.append(_origin_ledger_row(number, cell=_excluded_cell(unpriced_round)))
    return rows


def _non_origin_rows() -> list[dict]:
    """A kept-looking PR on the origin host for another repository, and one for the origin repository's slug on
    another host. Neither is read, counted or named by a report."""
    return [
        _origin_ledger_row(_NON_ORIGIN_PR_NUMBERS[0], cell=_optional_code_review_cell([_ledger_round()]), host=_OTHER_HOST),
        _origin_ledger_row(
            _NON_ORIGIN_PR_NUMBERS[1], cell=_optional_code_review_cell([_ledger_round()]),
            repo=f"{_OTHER_OWNER}/{_OTHER_REPO_NAME}",
        ),
    ]


def _non_origin_components() -> tuple[str, ...]:
    return _OTHER_HOST, _OTHER_OWNER, _OTHER_REPO_NAME, *_row_components(*_NON_ORIGIN_PR_NUMBERS)


def _active_account_rows() -> list[dict]:
    """PRs 11 and 12 are kept, the PRs of `_EXCLUDED_PR_NUMBERS` are excluded, and the PRs of `_non_origin_rows`
    belong to another repository and another host. PR 11 also holds an after-merge code-review round and an
    after-merge plan-review round."""
    return [
        _origin_ledger_row(11, additions=100, deletions=49, cell=_optional_code_review_cell(
            [_ledger_round(), _ledger_round(), _ledger_round(after_merge=1)],
            **{"plan-review": [_ledger_round(), _ledger_round(after_merge=1)]},
        )),
        _origin_ledger_row(12, additions=7, deletions=0, cell=_optional_code_review_cell(
            [_ledger_round(**{_UNKNOWN_ROUND_KEY_SENTINEL: 5})], **{_UNKNOWN_SKILL_SENTINEL: [_ledger_round()]},
        )),
        *_excluded_rows(),
        *_non_origin_rows(),
    ]


def _success_run_withheld(tmp_path: Path) -> tuple[str, ...]:
    return (
        _ESC, _ORIGIN_HOST, _ORIGIN_OWNER, _ORIGIN_REPO_NAME, _LEDGER_DIR_MARKER, str(tmp_path),
        _UNPRICED_MODEL_SENTINEL, _UNKNOWN_SKILL_SENTINEL, _UNKNOWN_ROUND_KEY_SENTINEL,
        *_row_components(*_ACTIVE_ACCOUNT_PR_NUMBERS, 31, 41, 42), *_non_origin_components(),
    )


def _assert_exclusion_counts_are_zero(out: str) -> None:
    for reason in ledger_rounds.EXCLUSION_REASONS:
        assert _count_line(out, f"PRs excluded, {reason}") == 0


def _assert_exclusion_counts(out: str) -> None:
    for reason, numbers in _EXCLUDED_PR_NUMBERS.items():
        assert _count_line(out, f"PRs excluded, {reason}") == len(numbers)


def _round_scaled(main_input: int) -> dict:
    """A round whose agent split is half its main split."""
    return _ledger_round(main_input=main_input, agent_input=main_input // 2)


def _diagnostics_block(out: str) -> list[str]:
    return out.split("\nLedger diagnostics\n")[1].split("\n\n")[0].splitlines()


class TestCmdRefusalsAndFailures:
    @pytest.mark.parametrize(
        "args_kwargs",
        [
            pytest.param({"this_repo": False}, id="no-this-repo"),
            pytest.param({"this_repo": False, "all_accounts": True}, id="all-accounts-without-this-repo"),
            pytest.param({"this_repo": False, "projects": "x"}, id="projects-without-this-repo"),
            pytest.param({"this_repo": True, "projects": "x"}, id="projects-with-this-repo"),
        ],
    )
    def test_refuses_without_this_repo_before_any_subprocess_call(self, monkeypatch, capsys, args_kwargs):
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(monkeypatch, capsys, _review_cost_by_size_args(**args_kwargs), call_log=call_log)
        assert exc_info.value.code == 2
        assert call_log == []
        assert "review-cost-by-size" in capsys.readouterr().err

    def test_default_mode_exits_2_under_its_own_name_when_the_config_dir_is_unresolvable(self, monkeypatch, capsys):
        def unresolvable_config_dir():
            raise ValueError("no HOME")

        monkeypatch.setattr(scope, "config_dir", unresolvable_config_dir)
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True), call_log=call_log)
        assert exc_info.value.code == 2
        assert call_log == []
        assert "review-cost-by-size: no HOME" in capsys.readouterr().err

    def test_all_accounts_exits_2_under_projects_dir_after_the_banner_when_the_config_dir_is_unresolvable(
        self, monkeypatch, capsys,
    ):
        """`_projects_dir` reads config_dir() only while PROJECTS_DIR is absent from the module globals, and an
        earlier test's monkeypatch can leave it there as a real global, so the test removes it first."""
        def unresolvable_config_dir():
            raise ValueError("no HOME")

        monkeypatch.delitem(vars(scope), "PROJECTS_DIR", raising=False)
        monkeypatch.setattr(scope, "config_dir", unresolvable_config_dir)
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(
                monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, all_accounts=True), call_log=call_log,
            )
        assert exc_info.value.code == 2
        assert call_log == []
        out, err = capsys.readouterr()
        assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
        assert err.splitlines() == [review_cost_by_size._PRIVATE_VIEW_BANNER, "_projects_dir: no HOME"]

    def test_all_accounts_over_a_single_root_still_marks_both_streams_do_not_publish(
        self, fake_projects, monkeypatch, capsys,
    ):
        out, err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, all_accounts=True))
        assert out.splitlines()[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert err == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"

    @pytest.mark.parametrize("all_accounts", [False, True], ids=["default-mode", "all-accounts"])
    def test_failing_origin_remote_lookup_exits_1_under_this_commands_name(
        self, fake_projects, monkeypatch, capsys, all_accounts,
    ):
        """The helper's `subcommand` default is `pr-cost`; the failure message must name this command."""
        def origin_remote_lookup_fails(cmd, *args, **kwargs):
            raise subprocess.CalledProcessError(128, cmd)

        monkeypatch.setattr(subprocess, "run", origin_remote_lookup_fails)
        with pytest.raises(SystemExit) as exc_info:
            review_cost_by_size.cmd_review_cost_by_size(
                _review_cost_by_size_args(this_repo=True, all_accounts=all_accounts),
            )
        assert exc_info.value.code == 1
        out, err = capsys.readouterr()
        error_lines = err.splitlines()
        if all_accounts:
            assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
            assert error_lines[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
            assert error_lines[1].startswith("review-cost-by-size: could not resolve this repo's own remote")
        else:
            assert out == ""
            assert error_lines[0].startswith("review-cost-by-size: could not resolve this repo's own remote")

    def test_a_zero_length_ledger_exits_1_with_the_parse_error_and_prints_no_report(
        self, fake_projects, monkeypatch, capsys,
    ):
        pr_cost_ledger._pr_cost_ledger_path(config_dir_override=fake_projects.parent.parent).write_text("")
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True), call_log=call_log)
        assert exc_info.value.code == 1
        assert call_log == _ORIGIN_LOOKUP_ONLY
        out, err = capsys.readouterr()
        with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as parse_error:
            pr_cost_ledger._parse_pr_cost_ledger_file_text("")
        assert out == ""
        assert err == f"review-cost-by-size: account-1: {parse_error.value}\n"

    def test_all_accounts_prints_the_banner_on_both_streams_before_a_ledger_failure(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        pr_cost_ledger._pr_cost_ledger_path(config_dir_override=roots[1].parent).write_bytes(b"\xff\xfe\x00")
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(
                monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, all_accounts=True), call_log=call_log,
            )
        assert exc_info.value.code == 1
        assert call_log == _ORIGIN_LOOKUP_ONLY
        out, err = capsys.readouterr()
        assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
        assert err.splitlines() == [
            review_cost_by_size._PRIVATE_VIEW_BANNER, "review-cost-by-size: account-2: ledger file could not be read",
        ]

    def test_an_exported_ledger_path_is_refused_under_all_accounts_across_two_roots_after_the_banner(
        self, tmp_path, monkeypatch, capsys,
    ):
        _two_declared_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(tmp_path / _OVERRIDE_BASENAME))
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(
                monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, all_accounts=True), call_log=call_log,
            )
        assert exc_info.value.code == 2
        assert call_log == _ORIGIN_LOOKUP_ONLY
        out, err = capsys.readouterr()
        assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
        assert err.splitlines()[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert err.splitlines()[1].startswith("review-cost-by-size: PR_COST_LEDGER_PATH is refused")
        _assert_withheld(out, err, str(tmp_path), _OVERRIDE_BASENAME)


class TestScopeHeader:
    @pytest.mark.parametrize(
        ("all_accounts", "ledger_path_overridden", "expected"),
        [
            pytest.param(False, False, _HEADER_ACTIVE_ACCOUNT, id="default"),
            pytest.param(True, False, _HEADER_EVERY_ACCOUNT, id="all-accounts"),
            pytest.param(False, True, _HEADER_LEDGER_PATH_OVERRIDE, id="default-with-override"),
            pytest.param(True, True, _HEADER_LEDGER_PATH_OVERRIDE, id="all-accounts-with-override"),
        ],
    )
    def test_the_header_names_the_account_scope_and_an_override_replaces_it(
        self, all_accounts, ledger_path_overridden, expected,
    ):
        assert review_cost_by_size._scope_header(all_accounts, ledger_path_overridden) == expected


class TestCmdSuccessRuns:
    """Each success run asserts its positive controls and withholds every component of the seeded rows."""

    def test_default_mode_reads_the_active_account_ledger_alone_and_withholds_every_seeded_component(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        _write_account_ledger(roots[0].parent, _active_account_rows())
        _write_account_ledger(roots[1].parent, [_kept_row(31)])

        out, err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        lines = out.splitlines()
        assert lines[0] == _HEADER_ACTIVE_ACCOUNT
        assert err == ""
        assert "DO NOT PUBLISH" not in out
        assert _printed_pr_numbers(out) == [12, 11]
        assert _count_line(out, "PRs reported") == 2
        assert _count_line(out, "PRs with a current row for this repository") == 2 + _EXCLUDED_PR_COUNT
        _assert_exclusion_counts(out)
        _assert_withheld(out, err, *_success_run_withheld(tmp_path), *_account_directory_names(roots))

    def test_all_accounts_reads_a_second_account_with_no_opt_in_and_marks_both_streams_do_not_publish(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        _write_account_ledger(roots[0].parent, _active_account_rows())
        _write_account_ledger(roots[1].parent, [_kept_row(31)])

        out, err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, all_accounts=True))

        lines = out.splitlines()
        assert lines[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert lines[1] == _HEADER_EVERY_ACCOUNT
        assert err == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
        assert _printed_pr_numbers(out) == [31, 12, 11]
        assert _count_line(out, "PRs reported") == 3
        assert _count_line(out, "PRs with a current row for this repository") == 3 + _EXCLUDED_PR_COUNT
        _assert_withheld(out, err, *_success_run_withheld(tmp_path), *_account_directory_names(roots))

    @pytest.mark.parametrize("all_accounts", [False, True], ids=["default-mode", "all-accounts-single-root"])
    def test_an_exported_ledger_path_replaces_the_active_ledger_at_one_root_and_the_header_says_so(
        self, fake_projects, tmp_path, monkeypatch, capsys, all_accounts,
    ):
        override_file = tmp_path / _LEDGER_DIR_MARKER / _OVERRIDE_BASENAME
        override_file.parent.mkdir()
        pr_cost_ledger._write_pr_cost_ledger_file(override_file, [_kept_row(41), *_non_origin_rows()])
        _write_account_ledger(tmp_path, [_kept_row(42)])
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(override_file))

        out, err = _run_report(
            monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, all_accounts=all_accounts),
        )

        lines = out.splitlines()
        assert lines[1 if all_accounts else 0] == _HEADER_LEDGER_PATH_OVERRIDE
        assert _printed_pr_numbers(out) == [41]
        _assert_withheld(out, err, *_success_run_withheld(tmp_path), str(override_file), _OVERRIDE_BASENAME)

    def test_an_empty_exported_ledger_path_counts_as_unset_and_the_header_names_the_active_account(
        self, fake_projects, monkeypatch, capsys,
    ):
        _write_account_ledger(fake_projects.parent.parent, [_kept_row(43)])
        monkeypatch.setenv("PR_COST_LEDGER_PATH", "")

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        assert out.splitlines()[0] == _HEADER_ACTIVE_ACCOUNT
        assert _printed_pr_numbers(out) == [43]

    def test_the_report_prints_the_ledgers_figures_and_never_a_transcript_sessions(
        self, fake_projects, monkeypatch, capsys,
    ):
        """The ledger PR's two rounds cost three times _DOLLARS_PER_100K_INPUT_TOKENS. A transcript session on the same
        branch holds five rounds at five times it, which a transcript fallback would print."""
        _write_account_ledger(fake_projects.parent.parent, [_kept_row(71, round_count=2)])
        _write_code_review_session(fake_projects / "sess-same-branch.jsonl", _head_branch(71), 5)

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args_scoped_to_seeded_session(fake_projects))

        assert _printed_pr_numbers(out) == [71]
        assert f"  {71:>7}  {5:>8,}  {2:>6}  {_dollars_text(3):>10}  {_dollars_text(1.5):>10}" in out.splitlines()
        assert _dollars_text(5) not in out

    def test_the_header_is_followed_by_the_scope_unverified_notice(self, fake_projects, monkeypatch, capsys):
        _write_account_ledger(fake_projects.parent.parent, [_kept_row(11)])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert out.splitlines()[:2] == [_HEADER_ACTIVE_ACCOUNT, review_cost_by_size._SCOPE_UNVERIFIED_NOTICE]
        assert _NO_PR_REPORTED_TEXT not in out

    def test_a_degraded_row_is_reported_with_its_size(self, fake_projects, monkeypatch, capsys):
        _write_account_ledger(fake_projects.parent.parent, [
            _kept_row(11, status=pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK, additions=37, deletions=5),
        ])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert f"  {11:>7}  {42:>8,}  {1:>6}" in out


class TestCmdArithmeticAndDiagnostics:
    def test_a_pr_is_sized_by_additions_plus_deletions_and_its_rounds_are_repriced_in_list_order(
        self, fake_projects, monkeypatch, capsys,
    ):
        """Four code-review rounds cost 1.5, 4.5, 3 and 6 times _DOLLARS_PER_100K_INPUT_TOKENS in list order, so the
        within-PR rho is 0.80, where sorted order would give 1.00. A huge after-merge round sits between the second
        and third, and a huge plan-review round beside them. Neither adds to the series. Every stored micro-dollar
        figure differs from the splits' price."""
        pr_cell = _optional_code_review_cell(
            [
                _round_scaled(100_000), _round_scaled(300_000), _round_scaled(5_000_000) | {"after_merge": 1},
                _round_scaled(200_000), _round_scaled(400_000),
            ],
            **{"plan-review": [_round_scaled(9_000_000)]},
        )
        _write_account_ledger(fake_projects.parent.parent, [_origin_ledger_row(61, additions=1000, deletions=480, cell=pr_cell)])

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        assert f"  {61:>7}  {'1,480':>8}  {4:>6}  {_dollars_text(15):>10}  {_dollars_text(3.75):>10}" in out.splitlines()
        assert "  PRs with a rho: 1; median rho 0.80; share above zero 100.0%" in out.splitlines()

    def test_a_pr_whose_rounds_carry_no_agent_tokens_is_reported_at_its_main_price(
        self, fake_projects, monkeypatch, capsys,
    ):
        """Main input of 100_000 tokens prices at _DOLLARS_PER_100K_INPUT_TOKENS, so two rounds with an empty agent object
        cost twice that. A PR whose rounds have neither object costs nothing. Both are reported, not excluded as
        unpriceable."""
        _write_account_ledger(fake_projects.parent.parent, [
            _origin_ledger_row(
                97, cell=_optional_code_review_cell([_ledger_round(agent_tokens={}), _ledger_round(agent_tokens={})]),
            ),
            _origin_ledger_row(98, cell=_optional_code_review_cell([_ledger_round(main_tokens={}, agent_tokens={})])),
        ])

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        assert _count_line(out, "PRs reported") == 2
        assert _printed_pr_numbers(out) == [97, 98]
        assert f"  {97:>7}  {5:>8,}  {2:>6}  {_dollars_text(2):>10}  {_dollars_text(1):>10}" in out.splitlines()
        assert f"  {98:>7}  {5:>8,}  {1:>6}  {_dollars_text(0):>10}  {_dollars_text(0):>10}" in out.splitlines()
        _assert_exclusion_counts_are_zero(out)

    def test_a_row_with_negative_additions_plus_deletions_is_reported_with_its_negative_size(
        self, fake_projects, monkeypatch, capsys,
    ):
        """Only a hand-edited ledger can hold a negative size. Its aggregate treatment is pinned in
        test_transcript_review_cost_by_size_aggregates.py."""
        _write_account_ledger(fake_projects.parent.parent, [
            _kept_row(95, additions=-30, deletions=10), _kept_row(96, additions=3, deletions=2),
        ])

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        assert _count_line(out, "PRs reported") == 2
        assert any(line.startswith(f"  {95:>7}  {-20:>8,}  {1:>6}") for line in out.splitlines())

    def test_the_per_pr_rows_print_after_every_aggregate_and_the_diagnostics(self, fake_projects, monkeypatch, capsys):
        _write_account_ledger(fake_projects.parent.parent, [_kept_row(11)])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert out.index("Aggregates") < out.index("Ledger diagnostics") < out.index("Per-PR rows")

    def test_the_diagnostics_print_their_exact_line_set_with_each_count_flowing_from_the_fixture(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        _write_account_ledger(roots[0].parent, _active_account_rows())

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        assert _diagnostics_block(out) == [
            f"  PRs with a current row for this repository: {2 + _EXCLUDED_PR_COUNT}",
            "  PRs reported: 2",
            *(f"  PRs excluded, {reason}: {len(numbers)}" for reason, numbers in _EXCLUDED_PR_NUMBERS.items()),
            "  PRs with no code-review round before merge: 0",
            "  Code-review rounds removed as opened at or after merge: 1",
            "  Unpriced turns inside reported rounds: 0",
            "  Dangling dispatches inside reported rounds: 0",
        ]

    def test_an_excluded_prs_after_merge_round_is_not_counted_as_removed(self, fake_projects, monkeypatch, capsys):
        _write_account_ledger(fake_projects.parent.parent, [
            _kept_row(11),
            _origin_ledger_row(
                24, cell=_excluded_cell(_ledger_round()), join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
            ),
        ])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert _count_line(out, f"PRs excluded, {ledger_rounds.EXCLUDED_JOIN_CONFIDENCE_LOW}") == 1
        assert _count_line(out, "Code-review rounds removed as opened at or after merge") == 0

    def test_an_unpriceable_prs_after_merge_round_ahead_of_the_unpriceable_one_is_not_counted_as_removed(
        self, fake_projects, monkeypatch, capsys,
    ):
        unpriced_round = _ledger_round(main_tokens={_UNPRICED_MODEL_SENTINEL: {"standard": {"input": 1}}})
        _write_account_ledger(
            fake_projects.parent.parent, [_kept_row(11), _origin_ledger_row(32, cell=_excluded_cell(unpriced_round))],
        )
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert _count_line(out, f"PRs excluded, {ledger_rounds.EXCLUDED_UNPRICEABLE_ROUND}") == 1
        assert _count_line(out, "Code-review rounds removed as opened at or after merge") == 0

    def test_a_cell_under_an_older_rule_lands_under_the_rule_reason_alone(self, fake_projects, monkeypatch, capsys):
        older_rule_cell = {**_excluded_cell(_ledger_round()), "rule": review_rounds.REVIEW_ROUND_RULE - 1}
        _write_account_ledger(fake_projects.parent.parent, [_kept_row(11), _origin_ledger_row(27, cell=older_rule_cell)])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        for reason in ledger_rounds.EXCLUSION_REASONS:
            assert _count_line(out, f"PRs excluded, {reason}") == int(reason == ledger_rounds.EXCLUDED_RULE)
        assert _count_line(out, "PRs with a current row for this repository") == 2
        assert _count_line(out, "PRs reported") == 1
        assert _count_line(out, "Code-review rounds removed as opened at or after merge") == 0

    @pytest.mark.parametrize("reported_invalid_usage_records", [1_234, 0])
    def test_the_sums_cover_the_reported_code_review_rounds_alone(
        self, fake_projects, monkeypatch, capsys, reported_invalid_usage_records,
    ):
        """Four rounds carry nonzero, distinct counts: the reported code-review round, an after-merge code-review
        round, the kept PR's plan-review round and an excluded PR's round. Only the first reaches the sums."""
        reported = _ledger_round(unpriced_turns=3, dangling=5, invalid_usage_records=reported_invalid_usage_records)
        after_merge = _ledger_round(unpriced_turns=700, dangling=1_100, invalid_usage_records=1_300, after_merge=1)
        plan_review = _ledger_round(unpriced_turns=17, dangling=19, invalid_usage_records=23)
        plan_review_after_merge = _ledger_round(
            unpriced_turns=2_900, dangling=3_100, invalid_usage_records=3_700, after_merge=1,
        )
        excluded = _ledger_round(unpriced_turns=31, dangling=37, invalid_usage_records=41)
        _write_account_ledger(fake_projects.parent.parent, [
            _origin_ledger_row(51, cell=_optional_code_review_cell(
                [reported, after_merge], **{"plan-review": [plan_review, plan_review_after_merge]},
            )),
            _origin_ledger_row(
                52, cell=_optional_code_review_cell([excluded]), join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
            ),
        ])

        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))

        assert _count_line(out, "Unpriced turns inside reported rounds") == 3
        assert _count_line(out, "Dangling dispatches inside reported rounds") == 5
        assert _count_line(out, "Code-review rounds removed as opened at or after merge") == 1
        malformed_lines = [line for line in out.splitlines() if "MALFORMED USAGE" in line]
        if reported_invalid_usage_records:
            assert malformed_lines == [
                "  MALFORMED USAGE — 1,234 usage-bearing turns inside reported rounds had non-numeric or negative"
                " token fields and were excluded from this report's dollars"
            ]
        else:
            assert malformed_lines == []

    def test_the_sums_total_the_counts_of_every_reported_pr(self, capsys):
        def reported_pr(number: int, *, unpriced_turns: int, dangling: int, invalid_usage_records: int):
            code_review_round = _ledger_round(
                unpriced_turns=unpriced_turns, dangling=dangling, invalid_usage_records=invalid_usage_records,
            )
            return ledger_rounds.LedgerPr(number, 5, {"code-review": [code_review_round]}, {"code-review": [0.3]})

        reported_prs = [
            reported_pr(1, unpriced_turns=3, dangling=5, invalid_usage_records=7),
            reported_pr(2, unpriced_turns=11, dangling=13, invalid_usage_records=17),
        ]
        ledger_read = ledger_rounds.LedgerRead(reported_prs, 2, dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0), {})

        review_cost_by_size._print_ledger_diagnostics(ledger_read, reported_prs)

        out = capsys.readouterr().out
        assert _count_line(out, "Unpriced turns inside reported rounds") == 14
        assert _count_line(out, "Dangling dispatches inside reported rounds") == 18
        assert "MALFORMED USAGE — 24 usage-bearing turns" in out

    def test_a_kept_pr_without_a_code_review_round_before_merge_is_counted_and_not_reported(
        self, fake_projects, monkeypatch, capsys,
    ):
        _write_account_ledger(fake_projects.parent.parent, [
            _origin_ledger_row(91, cell=_optional_code_review_cell(None, **{"plan-review": [_ledger_round()]})),
            _origin_ledger_row(92, cell=_optional_code_review_cell([_ledger_round(after_merge=1)])),
            _origin_ledger_row(93, cell=_optional_code_review_cell(None)),
            _kept_row(94),
        ])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert _count_line(out, "PRs with no code-review round before merge") == 3
        assert _count_line(out, "PRs reported") == 1
        assert _printed_pr_numbers(out) == [94]
        assert _NO_PR_REPORTED_TEXT not in out


class TestCmdEmptyReport:
    @pytest.mark.parametrize(
        ("ledger_rows", "decoy_prs", "excluded_prs", "no_review_prs", "expected_message"),
        [
            pytest.param(None, (81,), 0, 0, _NO_PR_RECORDED_TEXT, id="missing-ledger"),
            pytest.param([], (81,), 0, 0, _NO_PR_RECORDED_TEXT, id="header-only-ledger"),
            pytest.param(_non_origin_rows(), (81,), 0, 0, _NO_PR_RECORDED_TEXT, id="other-repository-rows-only"),
            pytest.param(
                [
                    _origin_ledger_row(81, cell=None),
                    _origin_ledger_row(
                        82, cell=_optional_code_review_cell([_ledger_round()]),
                        join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
                    ),
                    *_non_origin_rows(),
                ],
                (81, 82), 2, 0, _NO_PR_REPORTED_TEXT, id="all-rows-excluded",
            ),
            pytest.param(
                [
                    _origin_ledger_row(
                        91, cell=_optional_code_review_cell(None, **{"plan-review": [_ledger_round()]}),
                    ),
                    _origin_ledger_row(92, cell=_optional_code_review_cell([_ledger_round(after_merge=1)])),
                    _origin_ledger_row(93, cell=_optional_code_review_cell(None)),
                    *_non_origin_rows(),
                ],
                (91, 92, 93), 0, 3, _NO_PR_REPORTED_TEXT, id="kept-prs-without-a-review-round",
            ),
        ],
    )
    def test_an_empty_report_prints_the_no_pr_message_zero_aggregates_and_the_counts_that_explain_it(
        self, fake_projects, tmp_path, monkeypatch, capsys,
        ledger_rows, decoy_prs, excluded_prs, no_review_prs, expected_message,
    ):
        """`decoy_prs` get a session on their head branch, whose components a report must withhold. A case with no
        origin row has no branch to share, so its decoy only checks that a transcript scan finds nothing to report."""
        if ledger_rows is not None:
            _write_account_ledger(fake_projects.parent.parent, ledger_rows)
        for decoy_pr in decoy_prs:
            _write_code_review_session(fake_projects / f"sess-same-branch-{decoy_pr}.jsonl", _head_branch(decoy_pr), 3)

        out, err = _run_report(monkeypatch, capsys, _review_cost_by_size_args_scoped_to_seeded_session(fake_projects))

        lines = out.splitlines()
        assert lines[:3] == [
            _HEADER_ACTIVE_ACCOUNT, review_cost_by_size._SCOPE_UNVERIFIED_NOTICE,
            expected_message,
        ]
        assert "  rounds per PR: n/a (n=0)" in lines
        assert _count_line(out, "PRs reported") == 0
        assert _count_line(out, "PRs with no code-review round before merge") == no_review_prs
        assert sum(
            _count_line(out, f"PRs excluded, {reason}") for reason in ledger_rounds.EXCLUSION_REASONS
        ) == excluded_prs
        assert _printed_pr_numbers(out) == []
        _assert_withheld(
            out, err, _ESC, str(tmp_path), _LEDGER_DIR_MARKER, _ORIGIN_HOST, _ORIGIN_OWNER, _ORIGIN_REPO_NAME,
            *_row_components(*decoy_prs), *_non_origin_components(),
        )


class TestWording:
    def test_the_scope_unverified_notice_and_the_no_pr_message_read_verbatim(self):
        assert review_cost_by_size._SCOPE_UNVERIFIED_NOTICE == _NOTICE_TEXT
        assert review_cost_by_size._NO_PR_REPORTED_MESSAGE == _NO_PR_REPORTED_TEXT
        assert review_cost_by_size._NO_PR_RECORDED_MESSAGE == _NO_PR_RECORDED_TEXT

    @pytest.mark.parametrize(
        ("message_name", "cited_doc"),
        [
            pytest.param("_SCOPE_UNVERIFIED_NOTICE", _TRANSCRIPT_ANALYSIS_DOC, id="scope-unverified-notice"),
            pytest.param("_NO_PR_REPORTED_MESSAGE", _TRANSCRIPT_ANALYSIS_DOC, id="no-pr-reported"),
            pytest.param("_NO_PR_RECORDED_MESSAGE", _PR_COST_DOC, id="no-pr-recorded"),
        ],
    )
    def test_each_message_cites_a_real_heading(self, message_name, cited_doc):
        message = getattr(review_cost_by_size, message_name)
        cited = re.search(r'§\s+"([^"\n]+)"', message)
        assert cited, f"{message_name} does not cite a heading in the § \"...\" form"
        assert normalize_heading(cited.group(1)) in heading_texts(cited_doc.read_text())

    def test_private_view_banner_cites_a_real_heading(self):
        banner = review_cost_by_size._PRIVATE_VIEW_BANNER
        assert banner.startswith("DO NOT PUBLISH")
        cited = re.search(r'§\s+"([^"\n]+)"', banner)
        assert cited, f"{banner!r} does not cite a heading in the § \"...\" form"
        assert normalize_heading(cited.group(1)) in heading_texts(_PRIVATE_PROJECT_REDACTION_DOC.read_text())

    def test_the_docs_sample_labels_match_the_real_report_over_a_ledger_fixture(
        self, fake_projects, monkeypatch, capsys,
    ):
        _write_account_ledger(fake_projects.parent.parent, [_kept_row(11, round_count=2)])
        out, _err = _run_report(monkeypatch, capsys, _review_cost_by_size_args(this_repo=True))
        assert _label_lines_with_figures_masked(_docs_sample_lines()) == _label_lines_with_figures_masked(
            out.splitlines()
        )


class TestSubparserHelp:
    @staticmethod
    def _review_cost_by_size_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
        top_level = argparse.ArgumentParser()
        subparsers = top_level.add_subparsers()
        review_cost_by_size._add_review_cost_by_size_subparser(subparsers)
        return top_level, subparsers.choices["review-cost-by-size"]

    def test_this_repo_help_describes_ledger_rows_and_makes_no_worktree_claim(self):
        _top_level, subparser = self._review_cost_by_size_parser()
        help_text = _option_help(subparser, "--this-repo")
        assert help_text == (
            "Select the pr-cost ledger rows recorded for this checkout's origin repository"
            " (see docs/transcript-analysis.md)."
        )

    def test_all_accounts_help_names_the_ledger_and_the_multi_account_exclusion(self):
        _top_level, subparser = self._review_cost_by_size_parser()
        assert _option_help(subparser, "--all-accounts") == (
            "Read every declared account's pr-cost ledger instead of the active account's alone. A PR with"
            " more than one row that carries a review_rounds cell is excluded and counted, not summed."
            " A private view: prints DO NOT PUBLISH, and its output is never proposed for publication."
        )

    def test_the_command_help_says_it_needs_a_ledger_and_makes_no_gh_call(self, monkeypatch):
        """argparse wraps at the terminal width, so the width is pinned above the longest help line."""
        monkeypatch.setenv("COLUMNS", "1000")
        top_level, _subparser = self._review_cost_by_size_parser()
        assert "Requires --this-repo and a pr-cost ledger; makes no gh call." in " ".join(top_level.format_help().split())


class TestArgparseWiring:
    def test_all_accounts_flag_sets_all_accounts_through_the_real_parser(self):
        parser = cli.build_parser()
        with_flag = parser.parse_args(["review-cost-by-size", "--this-repo", "--all-accounts"])
        without_flag = parser.parse_args(["review-cost-by-size", "--this-repo"])
        assert with_flag.func is review_cost_by_size.cmd_review_cost_by_size
        assert with_flag.all_accounts is True
        assert without_flag.func is review_cost_by_size.cmd_review_cost_by_size
        assert without_flag.all_accounts is False
