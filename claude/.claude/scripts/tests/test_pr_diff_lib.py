"""Direct unit tests for _pr-diff-lib.sh, the helpers behind pr-diff-against-base.sh's --context-files mode.

The lib is sourced standalone (never through the script): the subprocess tests in test_pr_diff_against_base.py
need one wiring case per branch, and this file pins each helper's contract over its input matrix.
Helpers return their value in PR_DIFF_RESULT (or a documented PR_DIFF_* array), so each case prints it with printf.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, run_hook

from ._pr_diff_helpers import (
    _CREDENTIAL_FILE_READS_HOOK,
    _DIFF_PIN,
    _RSA_KEY,
    _SSH_DIR,
    _credential_read_gates_verdict,
    _git_out,
)
from .conftest import _init_repo

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_LIB = _SCRIPTS_DIR / "_pr-diff-lib.sh"
_HOOKS_LIB = HOOKS_DIR / "_lib.sh"

_ZERO_BLOB = "0" * 40
_BLOB_A = "a" * 40
_BLOB_B = "b" * 40
_EMPTY_BLOB = hashlib.sha1(b"blob 0\x00").hexdigest()  # git's object id for a zero-byte blob


def _bash(snippet: str, *args: str | bytes, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Source _lib.sh and the lib, then run snippet under `set -euo pipefail` with args as $1...

    The strict mode matches the script's, so an unbound variable or an unchecked failure in a helper fails here too.
    """
    harness = f'. "$1"; . "$2"; shift 2; set -euo pipefail\n{snippet}'
    return subprocess.run(
        ["bash", "-c", harness, "bash", str(_HOOKS_LIB), str(_LIB), *args],
        capture_output=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        cwd=cwd,
    )


def _result_of(function_call: str, *args: str | bytes) -> bytes:
    """The PR_DIFF_RESULT a helper leaves after `function_call "$@"`, as raw bytes; fails on a non-zero exit."""
    process = _bash(f'{function_call} "$@"\nprintf %s "$PR_DIFF_RESULT"', *args)
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return process.stdout


def _array_of(snippet_before: str, array_name: str, *args: str | bytes, env: dict | None = None) -> list[str]:
    """Run snippet_before (which fills `array_name`), then read the array back, one NUL-terminated entry per element."""
    process = _bash(
        f'{snippet_before}\nfor element in "${{{array_name}[@]}}"; do printf "%s\\0" "$element"; done',
        *args, env=env,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return [entry.decode("utf-8", "surrogateescape") for entry in process.stdout.split(b"\0")[:-1]]


# --- step names and the halt line ----------------------------------------------------------------


def test_halt_step_names_are_a_closed_list_of_kebab_case_names() -> None:
    names = _array_of(":", "PR_DIFF_STEP_NAMES")

    assert len(names) == len(set(names)) >= 10
    assert all(name == name.lower() and name.replace("-", "").isalpha() for name in names)
    for required in ("lane-diff", "header-check", "lane-slice", "ignore-check", "expect-diff", "status-check", "output-dir"):
        assert required in names


def test_halt_line_with_step_and_detail() -> None:
    process = _bash('_pr_diff_halt_line header-check 1 "unexpected header x"')

    assert process.stdout.decode() == (
        "pr-diff-against-base.sh: --context-files failed at header-check (exit 1): unexpected header x; "
        'see docs/scripts.md § "When --context-files halts"\n'
    )


def test_halt_line_without_detail_shows_the_failing_status() -> None:
    process = _bash('_pr_diff_halt_line lane-diff 128 ""')

    assert process.stdout.decode() == (
        "pr-diff-against-base.sh: --context-files failed at lane-diff (exit 128); "
        'see docs/scripts.md § "When --context-files halts"\n'
    )


def test_halt_line_leaves_the_step_out_when_unknown() -> None:
    process = _bash('_pr_diff_halt_line "" 2')

    assert process.stdout.decode() == (
        "pr-diff-against-base.sh: --context-files failed (exit 2); "
        'see docs/scripts.md § "When --context-files halts"\n'
    )


# --- argument grammar ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("validator", "value", "valid"),
    [
        ("suffix", "1792433222-GH-1208-reviewer-con", True),
        ("suffix", "1-", True),
        ("suffix", "12345678901234567890-" + "a" * 20, True),
        ("suffix", "123456789012345678901-a", False),
        ("suffix", "1-" + "a" * 21, False),
        ("suffix", "-abc", False),
        ("suffix", "12abc", False),
        ("suffix", "12-a_b", False),
        ("suffix", "12-a\n", False),
        ("agent_name", "staff-sdet", True),
        ("agent_name", "a", True),
        ("agent_name", "a" + "b" * 63, True),
        ("agent_name", "a" + "b" * 64, False),
        ("agent_name", "Staff", False),
        ("agent_name", "1abc", False),
        ("agent_name", "a_b", False),
        ("agent_name", "", False),
        ("listing_id", "a" * 64, True),
        ("listing_id", "A" * 64, False),
        ("listing_id", "a" * 63, False),
        ("listing_id", "a" * 65, False),
        ("listing_id", "g" * 64, False),
        ("ordinal", "1", True),
        ("ordinal", "999999", True),
        ("ordinal", "1000000", False),
        ("ordinal", "0", False),
        ("ordinal", "01", False),
        ("ordinal", "+1", False),
        ("ordinal", "-1", False),
        ("ordinal", "", False),
        ("ordinal", "1a", False),
    ],
)
def test_argument_validators(validator: str, value: str, valid: bool) -> None:
    process = _bash(f'_pr_diff_valid_{validator} "$1"', value)

    assert process.returncode == (0 if valid else 1), process.stderr


@pytest.mark.parametrize(
    ("argument", "agent", "form", "numbers"),
    [
        ("staff-sdet", "staff-sdet", "whole", ""),
        ("comment-discipline-reviewer:diff", "comment-discipline-reviewer", "diff", ""),
        ("staff-sdet=3", "staff-sdet", "listed", "3"),
        ("staff-sdet=3,1,2", "staff-sdet", "listed", "3 1 2"),
        ("staff-sdet=3,3,1,3", "staff-sdet", "listed", "3 1"),
    ],
)
def test_lane_forms_parse(argument: str, agent: str, form: str, numbers: str) -> None:
    process = _bash(
        '_pr_diff_parse_lane "$1"; printf "%s|%s|%s" "$PR_DIFF_LANE_AGENT" "$PR_DIFF_LANE_FORM" "$PR_DIFF_LANE_NUMBERS"',
        argument,
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout.decode() == f"{agent}|{form}|{numbers}"


@pytest.mark.parametrize(
    "argument",
    [
        "",
        "Staff",
        "staff:Diff",
        "staff-sdet:diff=1",
        "staff-sdet=",
        "staff-sdet=0",
        "staff-sdet=01",
        "staff-sdet=+1",
        "staff-sdet=-1",
        "staff-sdet=1,",
        "staff-sdet=,1",
        "staff-sdet=1,,2",
        "staff-sdet=a",
        "staff-sdet=1000000",
        "staff-sdet=1=2",
        "=1",
    ],
)
def test_malformed_lanes_are_rejected_with_a_usage_message(argument: str) -> None:
    process = _bash('_pr_diff_parse_lane "$1" && exit 9; printf "%s" "$PR_DIFF_USAGE_ERROR"', argument)

    assert process.returncode == 0, (process.returncode, process.stderr)
    assert process.stdout


def test_lane_row_indices_cover_every_row_for_a_whole_diff_lane_and_only_named_rows_for_a_listed_one() -> None:
    whole = _array_of('_pr_diff_lane_row_indices whole "" 4', "PR_DIFF_LANE_ROWS")
    diff_only = _array_of('_pr_diff_lane_row_indices diff "" 3', "PR_DIFF_LANE_ROWS")
    listed = _array_of('_pr_diff_lane_row_indices listed "4 2" 5', "PR_DIFF_LANE_ROWS")

    assert whole == ["0", "1", "2", "3"]
    assert diff_only == ["0", "1", "2"]
    assert listed == ["1", "3"]


# --- path quoting ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("plain.txt", b"plain.txt"),
        ("with space.txt", b"with space.txt"),
        ("co:lon.txt", b"co:lon.txt"),
        ("-leading-dash", b"-leading-dash"),
        ("$(dollar)", b"$(dollar)"),
        ("back\\slash", b"back\\slash"),
        ('in"ner', b'in"ner'),
        ("nïve-ü", "nïve-ü".encode()),
        ('"leading-quote', b'"\\"leading-quote"'),
        ("tab\tname", b'"tab\\tname"'),
        ("new\nline", b'"new\\nline"'),
        ("bell\a", b'"bell\\a"'),
        ("vt\x0b", b'"vt\\v"'),
        ("ff\x0c", b'"ff\\f"'),
        ("cr\r", b'"cr\\r"'),
        ("ctrl\x01", b'"ctrl\\001"'),
        ("del\x7f", b'"del\\177"'),
        ("tab\té", '"tab\\té"'.encode()),
        ("both\"and\\\tq", b'"both\\"and\\\\\\tq"'),
    ],
)
def test_index_path_quotes_only_a_control_byte_or_a_leading_quote(path: str, expected: bytes) -> None:
    assert _result_of("_pr_diff_index_path", path) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a/plain", b"a/plain"),
        ("a/with space", b"a/with space"),
        ("a/back\\slash", b'"a/back\\\\slash"'),
        ('a/in"ner', b'"a/in\\"ner"'),
        ("a/tab\tx", b'"a/tab\\tx"'),
        ("a/ünï", "a/ünï".encode()),
    ],
)
def test_git_header_name_quoting_covers_backslash_and_inner_quote_unlike_the_index_rule(name: str, expected: bytes) -> None:
    assert _result_of("_pr_diff_git_quote_name", name) == expected


@pytest.mark.parametrize(
    "quoted_and_raw",
    [
        ('"a\\tb"', "a\tb"),
        ('"a\\"b"', 'a"b'),
        ('"a\\\\b"', "a\\b"),
        ('"bell\\a\\b\\f\\n\\r\\t\\v"', "bell\a\b\f\n\r\t\v"),
        ('"o\\001x"', "o\x01x"),
        ('"d\\177"', "d\x7f"),
        ('"\\303\\274"', "ü"),
        ("plain", "plain"),
        ('"', '"'),
        ('"unterminated', '"unterminated'),
    ],
)
def test_cunquote_decodes_git_c_quoted_names(quoted_and_raw: tuple[str, str]) -> None:
    quoted, raw = quoted_and_raw

    assert _result_of("_pr_diff_cunquote", quoted) == raw.encode()


def test_cunquote_round_trips_what_the_index_cquote_writes() -> None:
    nasty = 'a"b\\c\td\ne\x01f\x7fgü'

    quoted = _result_of("_pr_diff_cquote", nasty)

    assert _result_of("_pr_diff_cunquote", quoted) == nasty.encode()


def _git_header_lines(repo: Path) -> list[bytes]:
    """Every `diff --git` line `git diff --cached` prints, called exactly as the script's shared diff call is."""
    process = _git_out(repo, *_DIFF_PIN, "-p", "--full-index", "-U3", "--cached", "--")
    return [line for line in process.stdout.split(b"\n") if line.startswith(b"diff --git ")]


def _expected_header(old: str, new: str) -> bytes:
    return _result_of("_pr_diff_expected_header", old, new)


# One name per git quoting class: a named escape (bell), an octal-class control byte, 0x7f, a quote, a backslash,
# a non-ASCII byte (raw under core.quotePath=false), a space (git does not quote 0x20), and the tab and newline escapes.
_QUOTING_CLASS_NAMES = [
    "bell\a.txt",
    "ctrl\x01.txt",
    "del\x7f.txt",
    'qu"ote.txt',
    "back\\slash.txt",
    "ünï.txt",
    "sp ace.txt",
    "tab\tname.txt",
    "new\nline.txt",
]


def test_expected_header_matches_git_for_each_quoting_class(tmp_path: Path) -> None:
    """The header builder is compared with the line a real git printed, never with a typed literal."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    for index, name in enumerate(_QUOTING_CLASS_NAMES):
        (repo / name).write_text(f"base {index}\n")
    _git_out(repo, "add", "-A")
    _git_out(repo, "commit", "-q", "-m", "base")
    for index, name in enumerate(_QUOTING_CLASS_NAMES):
        (repo / name).write_text(f"changed {index}\n")
    _git_out(repo, "add", "-A")

    printed = _git_header_lines(repo)

    assert sorted(printed) == sorted(_expected_header(name, name) for name in _QUOTING_CLASS_NAMES)


def test_expected_header_quotes_each_side_of_a_rename_on_its_own(tmp_path: Path) -> None:
    """A rename from a plain name to a quoted one, and one from a quoted name to a plain one."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = "".join(f"line {number}\n" for number in range(20))
    (repo / "plain_old.txt").write_text(body)
    (repo / 'qu"oted_old.txt').write_text(body.replace("line", "other"))
    _git_out(repo, "add", "-A")
    _git_out(repo, "commit", "-q", "-m", "base")
    _git_out(repo, "mv", "plain_old.txt", 'qu"oted_new.txt')
    _git_out(repo, "mv", 'qu"oted_old.txt', "plain_new.txt")
    (repo / 'qu"oted_new.txt').write_text(body.replace("line 3", "line three"))
    (repo / "plain_new.txt").write_text(body.replace("line", "other").replace("other 4", "other four"))
    _git_out(repo, "add", "-A")

    printed = _git_header_lines(repo)

    assert sorted(printed) == sorted([
        _expected_header("plain_old.txt", 'qu"oted_new.txt'),
        _expected_header('qu"oted_old.txt', "plain_new.txt"),
    ])


# --- raw and numstat records ------------------------------------------------------------------------------


def _raw_record(status: str, path: str, *, old_path: str | None = None, old_mode: str = "100644",
                new_mode: str = "100644", old_blob: str = _BLOB_A, new_blob: str = _BLOB_B) -> bytes:
    meta = f":{old_mode} {new_mode} {old_blob} {new_blob} {status}".encode()
    names = [old_path, path] if old_path is not None else [path]
    return meta + b"\0" + b"".join(name.encode() + b"\0" for name in names)


def _parse_raw_rows(tmp_path: Path, raw: bytes) -> list[list[str]]:
    """Parse raw bytes and return one [status, path, old_path, old_mode, new_mode, old_blob, new_blob] list per row."""
    raw_file = tmp_path / "raw"
    raw_file.write_bytes(raw)
    process = _bash(
        '_pr_diff_parse_raw "$1"\n'
        'for index in "${!PR_DIFF_ROW_STATUS[@]}"; do\n'
        '  printf "%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\0" "${PR_DIFF_ROW_STATUS[index]}" "${PR_DIFF_ROW_PATH[index]}" '
        '"${PR_DIFF_ROW_OLD_PATH[index]}" "${PR_DIFF_ROW_OLD_MODE[index]}" "${PR_DIFF_ROW_NEW_MODE[index]}" '
        '"${PR_DIFF_ROW_OLD_BLOB[index]}" "${PR_DIFF_ROW_NEW_BLOB[index]}"\ndone',
        str(raw_file),
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return [entry.decode().split("\t") for entry in process.stdout.split(b"\0")[:-1]]


def test_raw_parse_reads_each_status_and_both_names_of_a_rename_or_copy(tmp_path: Path) -> None:
    raw = (
        _raw_record("A", "added.txt", old_mode="000000", old_blob=_ZERO_BLOB)
        + _raw_record("M", "mod.txt")
        + _raw_record("D", "gone.txt", new_mode="000000", new_blob=_ZERO_BLOB)
        + _raw_record("T", "typed", old_mode="100644", new_mode="120000")
        + _raw_record("R087", "new name.txt", old_path="old name.txt")
        + _raw_record("C100", "copy.txt", old_path="src.txt")
        + _raw_record("U", "conflict.txt", old_mode="000000", new_mode="000000", old_blob=_ZERO_BLOB, new_blob=_ZERO_BLOB)
    )

    rows = _parse_raw_rows(tmp_path, raw)

    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("A", "added.txt", "added.txt"),
        ("M", "mod.txt", "mod.txt"),
        ("D", "gone.txt", "gone.txt"),
        ("T", "typed", "typed"),
        ("R087", "new name.txt", "old name.txt"),
        ("C100", "copy.txt", "src.txt"),
        ("U", "conflict.txt", "conflict.txt"),
    ]
    assert rows[0][3:] == ["000000", "100644", _ZERO_BLOB, _BLOB_B]
    assert rows[3][3:5] == ["100644", "120000"]


def test_raw_parse_keeps_a_path_holding_a_newline_a_tab_and_a_colon_whole(tmp_path: Path) -> None:
    raw_file = tmp_path / "raw"
    raw_file.write_bytes(_raw_record("M", "a\nb\tc:d"))

    process = _bash('_pr_diff_parse_raw "$1"; printf %s "${PR_DIFF_ROW_PATH[0]}"', str(raw_file))

    assert process.stdout == b"a\nb\tc:d"


def test_raw_parse_of_an_empty_file_yields_no_rows(tmp_path: Path) -> None:
    assert _parse_raw_rows(tmp_path, b"") == []


@pytest.mark.parametrize(
    "raw",
    [
        b"not-a-mode-line\0path\0",
        b":100644 100644 aaa bbb M\0",
        b":100644 100644 aaa bbb R100\0only-one-path\0",
    ],
)
def test_raw_parse_rejects_a_malformed_record(tmp_path: Path, raw: bytes) -> None:
    raw_file = tmp_path / "raw"
    raw_file.write_bytes(raw)

    process = _bash('_pr_diff_parse_raw "$1" && exit 9; printf %s "$PR_DIFF_PARSE_ERROR"', str(raw_file))

    assert process.returncode == 0, process.stderr
    assert process.stdout


def test_untracked_paths_become_question_mark_rows_after_the_raw_rows(tmp_path: Path) -> None:
    raw_file = tmp_path / "raw"
    raw_file.write_bytes(_raw_record("M", "tracked.txt"))
    untracked_file = tmp_path / "untracked"
    untracked_file.write_bytes(b"nested/\0plain new.txt\0")
    process = _bash(
        '_pr_diff_parse_raw "$1"; _pr_diff_append_untracked "$2"\n'
        'printf "%s|" "${PR_DIFF_ROW_STATUS[@]}"; printf "%s|" "${PR_DIFF_ROW_PATH[@]}"',
        str(raw_file), str(untracked_file),
    )

    assert process.stdout.decode() == "M|?|?|tracked.txt|nested/|plain new.txt|"


@pytest.mark.parametrize(
    ("status", "valid"),
    [("A", True), ("M", True), ("D", True), ("T", True), ("R100", True), ("R000", True), ("C075", True), ("?", True),
     ("U", False), ("X", False), ("B", False), ("R", False), ("R10", False), ("R1000", False), ("MM", False)],
)
def test_status_grammar(status: str, valid: bool) -> None:
    process = _bash(
        'PR_DIFF_ROW_STATUS=(M "$1" A); _pr_diff_check_status_grammar && exit 0; printf %s "$PR_DIFF_BAD_ROW"; exit 1',
        status,
    )

    assert process.returncode == (0 if valid else 1)
    if not valid:
        assert process.stdout == b"1"


def _numstat_bytes(*records: tuple[str, str, str] | tuple[str, str, str, str]) -> bytes:
    out = b""
    for record in records:
        if len(record) == 3:
            out += f"{record[0]}\t{record[1]}\t{record[2]}".encode() + b"\0"
        else:
            out += f"{record[0]}\t{record[1]}\t".encode() + b"\0" + record[2].encode() + b"\0" + record[3].encode() + b"\0"
    return out


def test_numstat_parse_keys_a_rename_by_its_new_name_and_reads_a_binary_file(tmp_path: Path) -> None:
    numstat_file = tmp_path / "numstat"
    numstat_file.write_bytes(
        _numstat_bytes(("3", "1", "plain.txt"), ("-", "-", "bin.dat"), ("2", "2", "old.txt", "new.txt"), ("0", "0", "tab\tname"))
    )

    process = _bash(
        '_pr_diff_parse_numstat "$1"; printf "%s|" "$PR_DIFF_NUMSTAT_COUNT" "${PR_DIFF_NUMSTAT[plain.txt]}" '
        '"${PR_DIFF_NUMSTAT[bin.dat]}" "${PR_DIFF_NUMSTAT[new.txt]}" "${PR_DIFF_NUMSTAT[$(printf "tab\\tname")]}"',
        str(numstat_file),
    )

    assert process.stdout.decode() == "4|3 1|- -|2 2|0 0|"


@pytest.mark.parametrize(
    ("reader", "expected_error"),
    [("_pr_diff_parse_raw", "could not read the raw output"), ("_pr_diff_parse_numstat", "could not read the numstat output")],
)
def test_a_record_reader_called_from_an_if_takes_the_failure_branch_on_a_missing_file(
    tmp_path: Path, reader: str, expected_error: str
) -> None:
    """`set -e` is off inside a function called from an `if`, so a reader that skips its own check parses nothing and succeeds."""
    snippet = f'if {reader} "$1"; then printf OK; else printf "ERR:%s" "$PR_DIFF_PARSE_ERROR"; fi'
    process = _bash(snippet, str(tmp_path / "missing"))

    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert process.stdout.decode() == f"ERR:{expected_error}"


def _classify(tmp_path: Path, raw: bytes, numstat: bytes, *, untracked: bytes = b"") -> subprocess.CompletedProcess:
    raw_file, numstat_file, untracked_file = tmp_path / "raw", tmp_path / "numstat", tmp_path / "untracked"
    raw_file.write_bytes(raw)
    numstat_file.write_bytes(numstat)
    untracked_file.write_bytes(untracked)
    return _bash(
        '_pr_diff_parse_raw "$1"; _pr_diff_append_untracked "$3"; _pr_diff_parse_numstat "$2"\n'
        'if _pr_diff_classify_rows; then\n'
        '  for index in "${!PR_DIFF_ROW_NOTES[@]}"; do\n'
        '    printf "%s=%s;" "${PR_DIFF_ROW_PATH[index]}" "${PR_DIFF_ROW_NOTES[index]}"\n'
        '  done\n'
        'else printf "ERR:%s" "$PR_DIFF_PARSE_ERROR"; fi',
        str(raw_file), str(numstat_file), str(untracked_file),
    )


def test_classification_notes_binary_gitlink_symlink_mode_empty_and_untracked_rows(tmp_path: Path) -> None:
    raw = (
        _raw_record("M", "bin.dat")
        + _raw_record("M", "sub", old_mode="160000", new_mode="160000")
        + _raw_record("A", "link", old_mode="000000", new_mode="120000", old_blob=_ZERO_BLOB)
        + _raw_record("M", "run.sh", old_mode="100644", new_mode="100755")
        + _raw_record("A", "empty.txt", old_mode="000000", old_blob=_ZERO_BLOB, new_blob=_EMPTY_BLOB)
        + _raw_record("D", "gone-empty.txt", new_mode="000000", new_blob=_ZERO_BLOB, old_blob=_EMPTY_BLOB)
        + _raw_record("M", "plain.txt")
        + _raw_record("R100", "moved.txt", old_path="orig.txt")
        + _raw_record("T", "retyped", new_mode="120000")
        + _raw_record("A", "new.bin", old_mode="000000", old_blob=_ZERO_BLOB)
        + _raw_record("A", "new-sub", old_mode="000000", new_mode="160000", old_blob=_ZERO_BLOB)
        + _raw_record("D", "gone-link", old_mode="120000", new_mode="000000", new_blob=_ZERO_BLOB)
    )
    numstat = _numstat_bytes(
        ("-", "-", "bin.dat"), ("1", "1", "sub"), ("1", "0", "link"), ("0", "0", "run.sh"), ("0", "0", "empty.txt"),
        ("0", "0", "gone-empty.txt"), ("2", "1", "plain.txt"), ("0", "0", "orig.txt", "moved.txt"), ("1", "1", "retyped"),
        ("-", "-", "new.bin"), ("1", "0", "new-sub"), ("0", "1", "gone-link"),
    )

    process = _classify(tmp_path, raw, numstat, untracked=b"loose.txt\0")

    assert process.stdout.decode() == (
        "bin.dat=binary;sub=gitlink;link=symlink;run.sh=mode;empty.txt=empty;gone-empty.txt=empty;"
        "plain.txt=;moved.txt=;retyped=symlink;new.bin=binary;new-sub=gitlink;gone-link=symlink;loose.txt=untracked;"
    )


def test_classification_halts_on_a_raw_row_with_no_numstat_entry_and_the_reverse(tmp_path: Path) -> None:
    two_rows = _raw_record("M", "a.txt") + _raw_record("M", "b.txt")
    missing_numstat = _classify(tmp_path, two_rows, _numstat_bytes(("1", "1", "a.txt")))
    extra_numstat = _classify(tmp_path, _raw_record("M", "a.txt"), _numstat_bytes(("1", "1", "a.txt"), ("1", "1", "b.txt")))

    assert missing_numstat.stdout.decode().startswith("ERR:no numstat entry for b.txt")
    assert extra_numstat.stdout.decode().startswith("ERR:the raw output holds 1 rows and the numstat output 2")


# --- notes, ranges, lane order -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("", b"-"),
        ("withheld binary", b"binary,withheld"),
        ("untracked skipped unavailable", b"unavailable,skipped,untracked"),
        ("no-eol over-cap commits-only", b"no-eol,over-cap,commits-only"),
        ("mode symlink gitlink empty", b"gitlink,symlink,mode,empty"),
        ("binary binary", b"binary"),
    ],
)
def test_notes_join_in_the_grammars_canonical_order(given: str, expected: bytes) -> None:
    assert _result_of("_pr_diff_canonical_notes", given) == expected


def test_next_range_tiles_each_body_after_the_last_and_the_last_row_ends_at_eof() -> None:
    process = _bash(
        'cursor=1\n'
        'for lines in 3 5 0 2; do _pr_diff_next_range cursor "$lines"; printf "%s|" "$PR_DIFF_RESULT"; done\n'
        'printf "next=%s" "$cursor"',
    )

    assert process.stdout.decode() == "1+3|4+5|-|9+2|next=11"


def test_budget_is_measured_from_the_enrichment_start() -> None:
    def exhausted(start: int, now: int, limit: int) -> bool:
        return _bash(f"_pr_diff_budget_exhausted {start} {now} {limit}").returncode == 0

    assert not exhausted(100, 100, 120)
    assert not exhausted(100, 219, 120)
    assert exhausted(100, 220, 120)
    assert exhausted(100, 500, 120)


def test_enrichment_clock_starts_on_the_first_call_and_is_never_reset() -> None:
    """SECONDS is assigned between calls so the budget is read without waiting; the margins far exceed the test's run time."""
    process = _bash(
        'CONTEXT_ENRICHMENT_SECONDS=120; ENRICH_START=""\n'
        'SECONDS=1000; _pr_diff_enrichment_has_time && printf "first=true start=%s;" "$ENRICH_START"\n'
        'SECONDS=1050; _pr_diff_enrichment_has_time && printf "within=true start=%s;" "$ENRICH_START"\n'
        'SECONDS=1117; _pr_diff_enrichment_has_time && printf "late-in-budget=true start=%s;" "$ENRICH_START"\n'
        'SECONDS=1130; _pr_diff_enrichment_has_time || printf "at-budget=false start=%s;" "$ENRICH_START"\n'
        'SECONDS=1500; _pr_diff_enrichment_has_time || printf "past-budget=false start=%s;" "$ENRICH_START"\n'
        'SECONDS=1010; _pr_diff_enrichment_has_time && printf "clock-rewound=true start=%s;" "$ENRICH_START"'
    )

    fields = process.stdout.decode().rstrip(";").split(";")

    assert process.returncode == 0, process.stderr
    starts = {field.split("start=")[1] for field in fields}
    assert len(starts) == 1, "the start is set once and never reset"
    first_start = int(starts.pop())
    assert 1000 <= first_start < 1010
    assert [field.split(" ")[0] for field in fields] == [
        "first=true", "within=true", "late-in-budget=true", "at-budget=false", "past-budget=false", "clock-rewound=true",
    ]


def test_enrichment_budget_is_exhausted_at_exactly_the_limit() -> None:
    process = _bash(
        'CONTEXT_ENRICHMENT_SECONDS=120; ENRICH_START=1000\n'
        'SECONDS=1117; _pr_diff_enrichment_has_time && printf "under;"\n'
        'SECONDS=1120; _pr_diff_enrichment_has_time || printf "at-limit;"'
    )

    assert process.stdout.decode().split(";")[:2] == ["under", "at-limit"]


def test_enrichment_order_puts_listed_lane_rows_first_then_rows_only_a_whole_diff_lane_reaches() -> None:
    order = _array_of(
        "PR_DIFF_ROW_STATUS=(M M M M M M)\n"
        'PR_DIFF_LANE_FORM_LIST=(whole listed diff listed)\n'
        'PR_DIFF_LANE_NUMBERS_LIST=("" "5 2" "" "2 3")\n'
        "_pr_diff_enrichment_order",
        "PR_DIFF_ENRICH_ORDER",
    )

    assert order == ["1", "2", "4", "0", "3", "5"]


def test_enrichment_order_has_no_whole_diff_tail_without_a_whole_diff_lane_and_nothing_for_a_diff_only_lane() -> None:
    listed_only = _array_of(
        'PR_DIFF_ROW_STATUS=(M M M)\nPR_DIFF_LANE_FORM_LIST=(listed diff)\nPR_DIFF_LANE_NUMBERS_LIST=("2" "")\n'
        "_pr_diff_enrichment_order",
        "PR_DIFF_ENRICH_ORDER",
    )
    diff_only = _array_of(
        'PR_DIFF_ROW_STATUS=(M M M)\nPR_DIFF_LANE_FORM_LIST=(diff)\nPR_DIFF_LANE_NUMBERS_LIST=("")\n_pr_diff_enrichment_order\n'
        "PR_DIFF_ENRICH_ORDER+=(none)",
        "PR_DIFF_ENRICH_ORDER",
    )

    assert listed_only == ["1"]
    assert diff_only == ["none"]


@pytest.mark.parametrize(
    ("left", "right", "equal"),
    [
        ("", "\n", True),
        ("", "", True),
        ("abc\n\n", "abc", True),
        ("abc", "abc\n", True),
        ("abc", "abd", False),
        ("a\nb", "a\n\nb", False),
        ("x", "", False),
    ],
)
def test_normalized_compare_ignores_every_trailing_newline(left: str, right: str, equal: bool) -> None:
    process = _bash('_pr_diff_normalized_equal "$1" "$2"', left, right)

    assert process.returncode == (0 if equal else 1)


def test_count_lines_reports_lines_bytes_and_a_missing_final_newline(tmp_path: Path) -> None:
    def count(content: bytes, max_bytes: int = 1000) -> str:
        body = tmp_path / "body"
        body.write_bytes(content)
        process = _bash(
            '_pr_diff_count_lines "$1" "$2"\n'
            'printf "%s %s %s %s" "$PR_DIFF_LINES" "$PR_DIFF_BYTES" "$PR_DIFF_NOEOL" "$PR_DIFF_EXCEEDS"',
            str(body), str(max_bytes),
        )
        assert process.returncode == 0, process.stderr
        return process.stdout.decode()

    assert count(b"") == "0 0 0 0"
    assert count(b"a\nb\n") == "2 4 0 0"
    assert count(b"a\nb") == "2 4 1 0"
    assert count(b"\n\n") == "2 2 0 0"
    assert count("é\n".encode()) == "1 3 0 0"
    assert count(b"aaaa\nbbbb\n", max_bytes=7).endswith(" 1")


# --- the shared-diff header check -----------------------------------------------------------------------------


def _check_headers(tmp_path: Path, rows: list[tuple[str, str, str]], found: list[tuple[int, str]], shared_bytes: int) -> str:
    """Run the header check for rows [(status, path, old_path)] against found [(offset, header)] records.

    Prints "OK:<row headers>" or "ERR:<detail>".
    """
    found_file = tmp_path / "found"
    found_file.write_bytes(b"".join(f"{offset}:{header}\n".encode() for offset, header in found))
    statuses = " ".join(f"'{status}'" for status, _, _ in rows)
    path_lines = "\n".join(
        f"PR_DIFF_ROW_PATH[{index}]=$(printf %b '{path}'); PR_DIFF_ROW_OLD_PATH[{index}]=$(printf %b '{old_path or path}')"
        for index, (_, path, old_path) in enumerate(rows)
    )
    process = _bash(
        f'PR_DIFF_ROW_STATUS=({statuses}); {path_lines}\n'
        '_pr_diff_build_expected_headers\n'
        'if _pr_diff_check_headers "$1" "$2"; then\n'
        '  printf "OK:"; for index in "${!PR_DIFF_ROW_HDRS[@]}"; do printf "%s;" "${PR_DIFF_ROW_HDRS[index]}"; done\n'
        'else printf "ERR:%s" "$PR_DIFF_HEADER_DETAIL"; fi',
        str(found_file), str(shared_bytes),
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return process.stdout.decode()


def test_header_check_passes_when_found_headers_equal_the_expected_multiset(tmp_path: Path) -> None:
    rows = [("M", "a.txt", ""), ("A", "b.txt", "")]
    found = [(0, "diff --git a/a.txt b/a.txt"), (50, "diff --git a/b.txt b/b.txt")]

    assert _check_headers(tmp_path, rows, found, 100) == "OK:0;1;"


def test_header_check_expects_two_adjacent_identical_headers_for_a_type_change(tmp_path: Path) -> None:
    rows = [("T", "link", ""), ("M", "z.txt", "")]
    header = "diff --git a/link b/link"

    assert _check_headers(tmp_path, rows, [(0, header), (40, header), (80, "diff --git a/z.txt b/z.txt")], 120) == "OK:0 1;2;"
    missing_second_header = [(0, header), (80, "diff --git a/z.txt b/z.txt")]
    assert _check_headers(tmp_path, rows, missing_second_header, 120) == "ERR:expected 3 headers, found 2"
    assert _check_headers(
        tmp_path, rows, [(0, header), (40, "diff --git a/z.txt b/z.txt"), (80, header)], 120
    ) == "ERR:the two headers for link are not adjacent"


def test_header_check_expects_no_header_for_an_untracked_row(tmp_path: Path) -> None:
    rows = [("M", "a.txt", ""), ("?", "loose.txt", "")]

    assert _check_headers(tmp_path, rows, [(0, "diff --git a/a.txt b/a.txt")], 40) == "OK:0;;"
    assert _check_headers(
        tmp_path, rows, [(0, "diff --git a/a.txt b/a.txt"), (40, "diff --git a/loose.txt b/loose.txt")], 80
    ).startswith("ERR:unexpected header diff --git a/loose.txt b/loose.txt")


def test_header_check_detail_names_the_first_unexpected_header_in_file_order(tmp_path: Path) -> None:
    rows = [("M", "a.txt", "")]
    found = [(0, "diff --git a/a.txt b/a.txt"), (40, "diff --git a/x b/x"), (80, "diff --git a/y b/y")]

    assert _check_headers(tmp_path, rows, found, 120) == "ERR:unexpected header diff --git a/x b/x"


def test_header_check_detail_names_the_first_missing_header_in_row_order(tmp_path: Path) -> None:
    rows = [("M", "a.txt", ""), ("M", "b.txt", ""), ("M", "c.txt", "")]
    found = [(0, "diff --git a/a.txt b/a.txt")]

    assert _check_headers(tmp_path, rows, found, 40) == "ERR:missing header for b.txt"


def test_header_check_detail_gives_the_counts_when_only_counts_differ(tmp_path: Path) -> None:
    rows = [("M", "a.txt", "")]
    found = [(0, "diff --git a/a.txt b/a.txt"), (40, "diff --git a/a.txt b/a.txt")]

    assert _check_headers(tmp_path, rows, found, 80) == "ERR:expected 1 headers, found 2"


def test_header_check_halts_on_data_before_the_first_header(tmp_path: Path) -> None:
    rows = [("M", "a.txt", "")]

    detail = _check_headers(tmp_path, rows, [(7, "diff --git a/a.txt b/a.txt")], 47)

    assert detail.startswith("ERR:expected the first of 1 headers at offset 0")


def test_header_check_called_from_an_if_takes_the_failure_branch_on_a_missing_found_file(tmp_path: Path) -> None:
    process = _bash(
        "PR_DIFF_ROW_STATUS=(M); PR_DIFF_ROW_PATH=(a.txt); PR_DIFF_ROW_OLD_PATH=(a.txt)\n"
        "_pr_diff_build_expected_headers\n"
        'if _pr_diff_check_headers "$1" 0; then printf OK; else printf "ERR:%s" "$PR_DIFF_HEADER_DETAIL"; fi',
        str(tmp_path / "missing"),
    )

    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert process.stdout.decode() == "ERR:could not read the found headers"


def test_header_check_empty_expected_set_passes_only_for_a_zero_byte_diff(tmp_path: Path) -> None:
    rows = [("?", "loose.txt", "")]

    assert _check_headers(tmp_path, rows, [], 0) == "OK:;"
    assert _check_headers(tmp_path, rows, [], 25).startswith("ERR:expected 0 headers, found none in 25 bytes")
    assert _check_headers(tmp_path, rows, [(0, "diff --git a/x b/x")], 30).startswith("ERR:expected 0 headers, found 1")


def test_header_check_halts_as_ambiguous_when_two_rows_would_print_the_same_header(tmp_path: Path) -> None:
    """A modified `p b/p` and a rename `p` to `p b/p b/p` both print `diff --git a/p b/p b/p b/p`."""
    rows = [("M", "p b/p", ""), ("R100", "p b/p b/p", "p")]
    header = "diff --git a/p b/p b/p b/p"

    detail = _check_headers(tmp_path, rows, [(0, header), (40, header)], 80)

    assert detail == f"ERR:ambiguous header {header}"


def test_header_check_lets_a_type_changes_own_duplicate_pass_as_unambiguous(tmp_path: Path) -> None:
    header = "diff --git a/t b/t"

    assert _check_headers(tmp_path, [("T", "t", "")], [(0, header), (30, header)], 60) == "OK:0 1;"


def test_header_check_keys_a_path_holding_a_dollar_bracket_and_quote_without_evaluating_it(tmp_path: Path) -> None:
    marker = tmp_path / "evaluated"
    awkward = "a]$(touch " + str(marker) + ")"
    header = f"diff --git a/{awkward} b/{awkward}"
    found_file = tmp_path / "found"
    found_file.write_bytes(f"0:{header}\n".encode())

    process = _bash(
        'PR_DIFF_ROW_STATUS=(M); PR_DIFF_ROW_PATH[0]=$3; PR_DIFF_ROW_OLD_PATH[0]=$3\n'
        '_pr_diff_build_expected_headers; _pr_diff_check_headers "$1" "$2" && printf OK',
        str(found_file), "40", awkward,
    )

    assert process.stdout == b"OK", process.stderr
    assert not marker.exists()


# --- block runs ------------------------------------------------------------------------------------------------


def _runs(offsets: list[int], total: int, kept: list[int]) -> list[tuple[int, int]]:
    process = _bash(
        f'PR_DIFF_HDR_OFFSET=({" ".join(map(str, offsets))}); _pr_diff_block_runs {total} {" ".join(map(str, kept))}\n'
        'for index in "${!PR_DIFF_RUN_END[@]}"; do\n'
        '  printf "%s,%s;" "${PR_DIFF_RUN_END[index]}" "${PR_DIFF_RUN_LENGTH[index]}"\n'
        'done'
    )
    assert process.returncode == 0, process.stderr
    return [tuple(map(int, pair.split(","))) for pair in process.stdout.decode().split(";") if pair]  # type: ignore[misc]


def test_a_block_runs_to_the_next_header_and_the_last_to_the_end_of_the_file() -> None:
    assert _runs([0, 10, 30], 50, [1]) == [(30, 20)]
    assert _runs([0, 10, 30], 50, [2]) == [(50, 20)]


def test_a_header_at_offset_zero_starts_a_run_of_its_own_length() -> None:
    assert _runs([0, 10, 30], 50, [0]) == [(10, 10)]


def test_all_blocks_adjacent_are_one_run() -> None:
    assert _runs([0, 10, 30], 50, [0, 1, 2]) == [(50, 50)]


def test_a_gap_from_a_withheld_or_non_lane_block_makes_two_runs() -> None:
    assert _runs([0, 10, 30, 40], 60, [0, 2, 3]) == [(10, 10), (60, 30)]


def test_the_two_blocks_of_a_type_change_sit_in_one_run() -> None:
    assert _runs([0, 20, 40, 60], 80, [1, 2]) == [(60, 40)]


def test_the_run_that_ends_at_the_end_of_the_file_ends_at_the_total_byte_count() -> None:
    assert _runs([0, 10, 30], 33, [1, 2]) == [(33, 23)]


def test_a_buffer_whose_last_byte_is_not_a_newline_still_ends_at_the_byte_count() -> None:
    assert _runs([0, 10], 17, [1]) == [(17, 7)]


def test_an_empty_selection_has_no_run() -> None:
    assert _runs([0, 10], 17, []) == []


def test_runs_copy_exactly_the_bytes_head_and_tail_are_asked_for(tmp_path: Path) -> None:
    """The (end, length) pair means `head -c end | tail -c length`; check the real tools reproduce the block bytes."""
    shared = b"diff --git a/a b/a\nA-body\ndiff --git a/b b/b\nB-body\ndiff --git a/c b/c\nC-body\n"
    offsets = [0, shared.index(b"diff --git a/b"), shared.index(b"diff --git a/c")]
    shared_file = tmp_path / "shared"
    shared_file.write_bytes(shared)
    (end, length), = _runs(offsets, len(shared), [1, 2])

    head = subprocess.run(["head", "-c", str(end), str(shared_file)], capture_output=True, check=True)
    tail = subprocess.run(["tail", "-c", str(length)], input=head.stdout, capture_output=True, check=True)

    assert tail.stdout == shared[offsets[1]:]


# --- listing digest ----------------------------------------------------------------------------------------------


def _listing_id(
    tmp_path: Path, raw: bytes, untracked: bytes, *, mode: str = "staged", base: str = "b" * 40, head: str = "-"
) -> str:
    raw_file, untracked_file = tmp_path / "raw", tmp_path / "untracked"
    raw_file.write_bytes(raw)
    untracked_file.write_bytes(untracked)
    return _result_of("_pr_diff_listing_id", mode, base, head, str(raw_file), str(untracked_file)).decode()


def test_listing_id_is_stable_for_identical_inputs_and_a_sha256_hex_digest(tmp_path: Path) -> None:
    raw = _raw_record("M", "a.txt")

    first = _listing_id(tmp_path, raw, b"")

    assert first == _listing_id(tmp_path, raw, b"")
    assert len(first) == 64 and set(first) <= set("0123456789abcdef")


def test_listing_id_changes_with_each_input(tmp_path: Path) -> None:
    raw = _raw_record("M", "a.txt")
    baseline = _listing_id(tmp_path, raw, b"")

    variants = {
        "mode": _listing_id(tmp_path, raw, b"", mode="cumulative"),
        "base": _listing_id(tmp_path, raw, b"", base="c" * 40),
        "head": _listing_id(tmp_path, raw, b"", head="d" * 40),
        "raw path": _listing_id(tmp_path, _raw_record("M", "b.txt"), b""),
        "raw blob id": _listing_id(tmp_path, _raw_record("M", "a.txt", new_blob="c" * 40), b""),
        "untracked": _listing_id(tmp_path, raw, b"loose.txt\0"),
    }

    assert baseline not in variants.values()
    assert len(set(variants.values())) == len(variants)


def test_listing_id_keeps_inputs_that_differ_only_by_where_a_nul_falls_apart(tmp_path: Path) -> None:
    """The raw/untracked boundary is a NUL, so moving one path across it must change the digest."""
    one_raw_none_untracked = _listing_id(tmp_path, b"path\0", b"")
    none_raw_one_untracked = _listing_id(tmp_path, b"", b"path\0")
    split_paths = _listing_id(tmp_path, b"a\0", b"b\0")
    moved_paths = _listing_id(tmp_path, b"a\0b\0", b"")

    assert len({one_raw_none_untracked, none_raw_one_untracked, split_paths, moved_paths}) == 4


def test_listing_id_never_reads_the_files_through_command_substitution(tmp_path: Path) -> None:
    """A NUL inside the raw bytes still reaches the digest: the id equals a sha256 computed in Python over the same stream."""
    raw, untracked = b"a\0b\0", b"c\0"
    stream = b"staged\0" + b"b" * 40 + b"\0-\0" + b"\0" + raw + b"\0" + untracked + b"\0"

    assert _listing_id(tmp_path, raw, untracked) == hashlib.sha256(stream).hexdigest()


@pytest.mark.parametrize("missing", ["raw", "untracked"])
def test_listing_id_fails_when_an_input_file_is_unreadable(tmp_path: Path, missing: str) -> None:
    """A digest of a truncated stream would name a different listing, so the call must fail instead of printing one."""
    (tmp_path / "raw").write_bytes(_raw_record("M", "a.txt"))
    (tmp_path / "untracked").write_bytes(b"")
    (tmp_path / missing).unlink()

    process = _bash(
        'if _pr_diff_listing_id staged "$3" - "$1" "$2"; then printf "OK:%s" "$PR_DIFF_RESULT"; else printf FAILED; fi',
        str(tmp_path / "raw"), str(tmp_path / "untracked"), "b" * 40,
    )

    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert process.stdout.decode() == "FAILED"


def _working_tree_listing_id(tmp_path: Path, rows: list[tuple[str, ...]], untracked: bytes = b"") -> str:
    """The working-tree listing id for rows given as (status, old mode, new mode, old blob, new blob, old path, path)."""
    raw = b"".join(
        _raw_record(status, path, old_path=old_path if old_path != path else None, old_mode=old_mode, new_mode=new_mode,
                    old_blob=old_blob, new_blob=new_blob)
        for status, old_mode, new_mode, old_blob, new_blob, old_path, path in rows
    )
    raw_file, untracked_file = tmp_path / "raw", tmp_path / "untracked"
    raw_file.write_bytes(raw)
    untracked_file.write_bytes(untracked)
    process = _bash(
        '_pr_diff_parse_raw "$1"; _pr_diff_append_untracked "$2"\n'
        '_pr_diff_listing_id working-tree "$3" - "$1" "$2"\nprintf %s "$PR_DIFF_RESULT"',
        str(raw_file), str(untracked_file), "b" * 40,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return process.stdout.decode()


def test_working_tree_listing_id_leaves_the_new_side_blob_id_out(tmp_path: Path) -> None:
    """git prints an all-zero new-side id until its first diff refreshes a stat-dirty index; the id must not move with it."""
    row = ("M", "100644", "100644", _BLOB_A, _ZERO_BLOB, "a.txt", "a.txt")
    refreshed = ("M", "100644", "100644", _BLOB_A, _BLOB_B, "a.txt", "a.txt")

    assert _working_tree_listing_id(tmp_path, [row]) == _working_tree_listing_id(tmp_path, [refreshed])


def test_working_tree_listing_id_still_follows_every_other_field_and_the_untracked_list(tmp_path: Path) -> None:
    baseline_row = ("M", "100644", "100644", _BLOB_A, _BLOB_B, "a.txt", "a.txt")
    baseline = _working_tree_listing_id(tmp_path, [baseline_row])

    variants = {
        "path": _working_tree_listing_id(tmp_path, [("M", "100644", "100644", _BLOB_A, _BLOB_B, "b.txt", "b.txt")]),
        "old blob": _working_tree_listing_id(tmp_path, [("M", "100644", "100644", "c" * 40, _BLOB_B, "a.txt", "a.txt")]),
        "new mode": _working_tree_listing_id(tmp_path, [("M", "100644", "100755", _BLOB_A, _BLOB_B, "a.txt", "a.txt")]),
        "status": _working_tree_listing_id(tmp_path, [("D", "100644", "000000", _BLOB_A, _ZERO_BLOB, "a.txt", "a.txt")]),
        "rename source": _working_tree_listing_id(tmp_path, [("R100", "100644", "100644", _BLOB_A, _BLOB_B, "z.txt", "a.txt")]),
        "an extra row": _working_tree_listing_id(tmp_path, [baseline_row, ("M", "100644", "100644", _BLOB_A, _BLOB_B, "q", "q")]),
        "untracked": _working_tree_listing_id(tmp_path, [baseline_row], b"loose.txt\0"),
    }

    assert baseline not in variants.values()
    assert len(set(variants.values())) == len(variants)


def test_staged_and_cumulative_listing_ids_still_follow_the_new_side_blob_id(tmp_path: Path) -> None:
    zeroed = _raw_record("M", "a.txt", new_blob=_ZERO_BLOB)
    real = _raw_record("M", "a.txt", new_blob=_BLOB_B)

    for mode in ("staged", "cumulative"):
        assert _listing_id(tmp_path, zeroed, b"", mode=mode) != _listing_id(tmp_path, real, b"", mode=mode)


# --- history header names ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ("diff --git a/x.txt b/y.txt", ["x.txt", "y.txt"]),
        ('diff --git "a/x\\ty" b/z', ["x\ty", "z"]),
        ('diff --git a/q "b/w\\"v"', ["q", 'w"v']),
        ("diff --git a/sp ace b/sp ace", ["sp", "ace", "sp", "ace"]),
        ("--- a/old.txt", ["old.txt"]),
        ("+++ b/new.txt", ["new.txt"]),
        ("--- /dev/null", []),
        ("+++ /dev/null", []),
        ("--- a/with space.txt\t", ["with space.txt"]),
        ('--- "a/tab\\there"', ["tab\there"]),
        ("rename from old/name.txt", ["old/name.txt"]),
        ('rename to "new\\tname"', ["new\tname"]),
        ("index 123..456 100644", []),
        ("+added line", []),
    ],
)
def test_history_header_names_are_unquoted_and_lose_their_a_or_b_prefix(line: str, names: list[str]) -> None:
    assert _array_of('_pr_diff_history_header_names "$1"', "PR_DIFF_HEADER_NAMES", line) == names


# --- withholding predicates ------------------------------------------------------------------------------------------


def _scan(paths: list[str], *, globs: list[str] | None = None, root: str = "/repo", env: dict | None = None) -> list[int]:
    """Run the withholding scan over relative paths and return the 0/1 verdict of each."""
    glob_array = " ".join(f"'{glob}'" for glob in (globs or []))
    process = _bash(
        'root=$1; shift\nPR_DIFF_SCAN_TEXT=("$@")\n'
        f'PR_DIFF_GUARD_GLOBS=({glob_array})\n'
        '_pr_diff_scan_withheld "$root"\nprintf "%s" "${PR_DIFF_SCAN_HIT[*]}"',
        root, *paths, env=env,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return [int(token) for token in process.stdout.decode().split()]


def test_scan_withholds_the_built_in_credential_names_dotenv_variants_and_unsafe_ssh_files() -> None:
    paths = [
        "src/main.py", ".env", "app/.env.prod", "app/.env.ci", ".env.example", f"keys/{_RSA_KEY}", f"keys/{_RSA_KEY}.pub",
        f"{_SSH_DIR}/deploy_key", f"{_SSH_DIR}/config", f"{_SSH_DIR}/known_hosts", "home/.netrc", "notes/env.md",
    ]

    assert _scan(paths) == [0, 1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 0]


def test_scan_of_an_empty_list_has_no_verdicts() -> None:
    assert _scan([]) == []


def test_scan_applies_the_guard_globs_to_the_absolute_path_not_the_relative_name() -> None:
    assert _scan(["secrets/a.txt", "other/b.txt"], globs=["/repo/secrets/*"], root="/repo") == [1, 0]
    assert _scan(["secrets/a.txt"], globs=["secrets/*"], root="/repo") == [0]


def test_the_credential_regex_pass_needs_no_external_tool_and_reads_each_line_of_a_path_apart() -> None:
    """With no tool on PATH the regex pass still marks a credential-named path, so no grep dialect decides the verdict."""
    process = _bash(
        f'PATH=/nonexistent\nPR_DIFF_SCAN_TEXT=("keys/{_RSA_KEY}" "src/x.py" "ID_RSA.PUB" "a\n.netrc" "a.netrc\nb")\n'
        'PR_DIFF_SCAN_HIT=(0 0 0 0 0)\n_pr_diff_mark_credential_regex_hits /repo\nprintf "%s" "${PR_DIFF_SCAN_HIT[*]}"'
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout.decode() == "1 0 0 1 0"


def test_the_credential_regex_pass_restores_the_callers_nocasematch_setting() -> None:
    process = _bash(
        'PR_DIFF_SCAN_TEXT=(x); PR_DIFF_SCAN_HIT=(0)\n'
        'shopt -u nocasematch; _pr_diff_mark_credential_regex_hits /repo; shopt -q nocasematch && echo leaked\n'
        'shopt -s nocasematch; _pr_diff_mark_credential_regex_hits /repo; shopt -q nocasematch && echo kept'
    )

    assert process.stdout.decode().split() == ["kept"]


# --- the guard-glob matcher ----------------------------------------------------------------------------------------------


def _hook_verdict(path: str, home: Path, config_dir: Path | None) -> str:
    extra_env = {"CLAUDE_CONFIG_DIR": str(config_dir)} if config_dir is not None else {"CLAUDE_CONFIG_DIR": ""}
    return run_hook(
        _CREDENTIAL_FILE_READS_HOOK,
        {"tool_name": "Read", "tool_input": {"file_path": path}},
        home=home,
        extra_env=extra_env,
    )


def _matcher_verdicts(paths: list[str], guard_file: Path, home: Path, config_dir: Path | None) -> list[str]:
    env = {**os.environ, "HOME": str(home)}
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    process = _bash(
        '_pr_diff_guard_file_path; _pr_diff_read_guard_globs "$PR_DIFF_RESULT"\n'
        'for path in "$@"; do\n'
        '  if _pr_diff_guard_glob_matches ignore-case "$path" "${PR_DIFF_GUARD_GLOBS[@]}"; then echo deny; else echo allow; fi\n'
        'done',
        *paths, env=env,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return process.stdout.decode().split()


# Paths no built-in credential rule matches, so the hook's verdict is decided by the guard globs alone.
_GUARD_PARITY_PATHS = [
    "/work/vault/store.bin",
    "/work/vault/sub/deep.bin",
    "/work/exact-secret.dat",
    "/work/exact-secretX.dat",
    "/work/q-a.dat",
    "/work/q-ab.dat",
    "/work/MixedCase/Thing.bin",
    "/work/mixedcase/thing.bin",
    "/work/plain/readme.md",
]
_GUARD_PARITY_GLOBS = "# a comment\n\n/work/vault/*\n/work/exact-secret.dat\n/work/q-?.dat\n/work/mixedcase/THING.bin\n"


def test_guard_glob_matcher_matches_hook_verdict(tmp_path: Path) -> None:
    """The real deny-credential-file-reads.sh Read verdict is the oracle for exact, `*` and `?` globs,
    a mixed-case glob, a comment and a blank line."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    guard_file = home / ".claude" / "credential-file-guard.md"
    guard_file.write_text(_GUARD_PARITY_GLOBS)

    hook = [_hook_verdict(path, home, None) for path in _GUARD_PARITY_PATHS]
    matcher = _matcher_verdicts(_GUARD_PARITY_PATHS, guard_file, home, None)

    assert matcher == hook
    assert set(hook) == {"allow", "deny"}


def test_guard_glob_matcher_prefers_the_config_dir_guard_file_over_the_legacy_one(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    (home / ".claude").mkdir(parents=True)
    config_dir.mkdir()
    (home / ".claude" / "credential-file-guard.md").write_text("/work/legacy/*\n")
    (config_dir / "credential-file-guard.md").write_text("/work/current/*\n")
    paths = ["/work/legacy/a.bin", "/work/current/a.bin"]

    hook = [_hook_verdict(path, home, config_dir) for path in paths]
    matcher = _matcher_verdicts(paths, config_dir / "credential-file-guard.md", home, config_dir)

    assert hook == ["allow", "deny"]
    assert matcher == hook


def test_guard_glob_matcher_falls_back_to_the_legacy_guard_file_without_a_config_dir_copy(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    (home / ".claude").mkdir(parents=True)
    config_dir.mkdir()
    (home / ".claude" / "credential-file-guard.md").write_text("/work/legacy/*\n")
    paths = ["/work/legacy/a.bin", "/work/other/a.bin"]

    hook = [_hook_verdict(path, home, config_dir) for path in paths]
    matcher = _matcher_verdicts(paths, home / ".claude" / "credential-file-guard.md", home, config_dir)

    assert hook == ["deny", "allow"]
    assert matcher == hook


def test_guard_glob_matcher_matches_nothing_for_an_absent_guard_file_like_the_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    paths = ["/work/vault/store.bin"]

    hook = [_hook_verdict(path, home, None) for path in paths]
    matcher = _matcher_verdicts(paths, home / ".claude" / "credential-file-guard.md", home, None)

    assert hook == ["allow"]
    assert matcher == hook


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_guard_glob_matcher_matches_nothing_for_an_unreadable_guard_file_like_the_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    guard_file = home / ".claude" / "credential-file-guard.md"
    guard_file.write_text("/work/vault/*\n")
    guard_file.chmod(0)
    paths = ["/work/vault/store.bin"]

    try:
        hook = [_hook_verdict(path, home, None) for path in paths]
        matcher = _matcher_verdicts(paths, guard_file, home, None)
    finally:
        guard_file.chmod(0o600)

    assert hook == ["allow"]
    assert matcher == hook


def test_guard_glob_matcher_runs_case_in_the_shell_and_forks_nothing_per_path(tmp_path: Path) -> None:
    """A PATH holding no tool at all still matches: the matcher is `case` under nocasematch, not an external call."""
    process = _bash(
        'PATH=/nonexistent; _pr_diff_guard_glob_matches ignore-case "/work/Vault/A.bin" "/work/vault/*"',
    )

    assert process.returncode == 0, process.stderr


def test_guard_glob_matcher_restores_the_callers_nocasematch_setting() -> None:
    process = _bash(
        'matches() { _pr_diff_guard_glob_matches "$@" || true; }\n'
        'shopt -u nocasematch; matches ignore-case /a/B "/a/b"; shopt -q nocasematch && echo leaked\n'
        'shopt -u nocasematch; matches match-case /a/B "/a/b"; shopt -q nocasematch && echo leaked-match-case\n'
        'shopt -s nocasematch; matches ignore-case /a/B "/a/c"; shopt -q nocasematch && echo kept\n'
        'shopt -s nocasematch; matches match-case /a/B "/a/c"; shopt -q nocasematch && echo kept-match-case'
    )

    assert process.stdout.decode().split() == ["kept", "kept-match-case"]


def test_guard_glob_matcher_with_no_globs_matches_nothing() -> None:
    process = _bash(
        'PR_DIFF_GUARD_GLOBS=(); _pr_diff_guard_glob_matches ignore-case /a/b "${PR_DIFF_GUARD_GLOBS[@]}" && exit 9; exit 0'
    )

    assert process.returncode == 0



# --- parity with the Read hooks over one name matrix ------------------------------------------------------------------

_DENY_DATA_FILE_READS_HOOK = HOOKS_DIR / "deny-data-file-reads.sh"

# (relative name, Read verdict). The verdict a Read of "<root>/<name>" gets is deny when either credential Read gate denies it.
_READ_GATE_NAME_MATRIX = [
    ("src/main.py", "allow"),
    ("notes/env.md", "allow"),
    (".env", "deny"),
    (".ENV", "deny"),
    ("app/.env." + "local", "deny"),  # split: the redaction gate's hostname detector matches the joined suffix
    ("app/.env.custom", "deny"),
    ("x/.env.custom/", "deny"),
    (".env.example", "allow"),
    (".env.example/", "allow"),
    (".env.sample", "allow"),
    (".env\n", "deny"),
    (f"keys/{_RSA_KEY}", "deny"),
    ("keys/ID_RSA", "deny"),
    (f"keys/{_RSA_KEY}/", "deny"),
    (f"keys/{_RSA_KEY}.pub", "allow"),
    (f"{_SSH_DIR}/deploy_key", "deny"),
    (".SSH/Deploy_Key", "deny"),
    (".ssh.bak/deploy_key", "deny"),
    (f"dir/{_SSH_DIR}/sub/deploy_key", "deny"),
    (f"{_SSH_DIR}/", "deny"),
    (f"{_SSH_DIR}/config", "allow"),
    (f"{_SSH_DIR}/known_hosts", "allow"),
    (f"{_SSH_DIR}/{_RSA_KEY}.pub", "allow"),
    (f"dir with space/{_SSH_DIR}/deploy_key", "deny"),
    (".netrc", "deny"),
    (".NetRC", "deny"),
    ("home/_netrc", "deny"),
    ("dir with space/.netrc", "deny"),
    (".aws/credentials", "deny"),
    (".aws/credentials.bak", "deny"),
    ("credentials.json", "deny"),
    ("note\n.netrc", "deny"),
    (f"x\n{_SSH_DIR}/deploy_key", "deny"),
    ("a.netrc\nb", "allow"),
    ("first\nsecond\n.env.example", "allow"),
]


@pytest.mark.parametrize(("name", "expected"), _READ_GATE_NAME_MATRIX, ids=[repr(name) for name, _ in _READ_GATE_NAME_MATRIX])
def test_scan_withheld_matches_the_credential_read_gates_over_the_name_matrix(tmp_path: Path, name: str, expected: str) -> None:
    """The hooks' Read verdicts are the oracle: embedded and trailing newlines, a trailing slash, case, .ssh variants."""
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": ""}

    hook_verdict = _credential_read_gates_verdict(name, home)
    scan_verdict = "deny" if _scan([name], env=env) == [1] else "allow"

    assert hook_verdict == expected
    assert scan_verdict == hook_verdict


def test_the_name_matrix_holds_both_verdicts() -> None:
    assert {verdict for _name, verdict in _READ_GATE_NAME_MATRIX} == {"allow", "deny"}


# A store whose name predicate spans the directory above the file only denies when the scan sees the root too.
_ROOTED_STORE_MATRIX = [
    ("/work/.aws", "credentials", "deny"),
    ("/work/.docker", "config.json", "deny"),
    ("/work/.kube", "config", "deny"),
    ("/work/.config", "gh/hosts.yml", "deny"),
    ("/work/.ssh", "deploy_key", "deny"),
    ("/work/.ssh", "config", "allow"),
    ("/work/project", "credentials", "allow"),
    ("/work/project", "config.json", "allow"),
    ("/work/project", "config", "allow"),
    ("/work/project", "gh/hosts.yml", "allow"),
    ("/work/project", "deploy_key", "allow"),
]


@pytest.mark.parametrize(
    ("root", "name", "expected"), _ROOTED_STORE_MATRIX, ids=[f"{root}|{name}" for root, name, _ in _ROOTED_STORE_MATRIX]
)
def test_scan_withheld_matches_the_credential_read_gates_when_the_store_directory_is_in_the_root(
    tmp_path: Path, root: str, name: str, expected: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": ""}

    hook_verdict = _credential_read_gates_verdict(name, home, root)
    scan_verdict = "deny" if _scan([name], root=root, env=env) == [1] else "allow"

    assert hook_verdict == expected
    assert scan_verdict == hook_verdict


@pytest.mark.parametrize(
    ("path", "basename"),
    [("a/b/c.txt", "c.txt"), ("a/b/", "b"), ("a/b///", "b"), ("c", "c"), ("x/.env\n", ".env"), ("a\n/b", "b")],
)
def test_basename_helper_matches_basename_in_a_command_substitution(tmp_path: Path, path: str, basename: str) -> None:
    process = _bash(
        '_pr_diff_basename "$1"; printf "%s" "$PR_DIFF_RESULT"; printf "\\0"; printf "%s" "$(basename -- "$1")"', path
    )

    helper_result, _, oracle_result = process.stdout.decode().partition("\0")
    assert helper_result == oracle_result == basename


# --- the data-file guard ------------------------------------------------------------------------------------------------

_DATA_GUARD_GLOBS = "# patient data\n\n*/exports-private/*\n*/PRIVATE/*\n"


def _data_guard_home(tmp_path: Path, *, armed: bool = True, globs: str = _DATA_GUARD_GLOBS) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    if armed:
        (home / ".claude" / "data-file-read-guard.md").write_text(globs)
    return home


def _data_guard_scan(paths: list[str], root: Path, home: Path) -> list[int]:
    env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": ""}
    process = _bash(
        'root=$1; shift\nPR_DIFF_SCAN_TEXT=("$@")\nPR_DIFF_GUARD_GLOBS=()\n_pr_diff_load_data_guard\n'
        '_pr_diff_scan_withheld "$root"\nprintf "%s" "${PR_DIFF_SCAN_HIT[*]}"',
        str(root), *paths, env=env,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return [int(token) for token in process.stdout.decode().split()]


def _data_guard_read_verdict(file_path: str, home: Path) -> str:
    return run_hook(
        _DENY_DATA_FILE_READS_HOOK, {"tool_name": "Read", "tool_input": {"file_path": file_path}}, home=home,
        extra_env={"CLAUDE_CONFIG_DIR": ""},
    )


_DATA_EXTENSION_ARM = re.compile(r'case "\$BASENAME" in\s*\n\s*((?:\*\.\w+\|)*\*\.\w+)\)')
_DATA_EXTENSION_COUNT_FLOOR = 14


def _hook_data_extensions() -> list[str]:
    """The extensions the hook's built-in rule denies, read from its extension `case` arm so a later addition is covered."""
    arm = _DATA_EXTENSION_ARM.search(_DENY_DATA_FILE_READS_HOOK.read_text())
    assert arm is not None, "the hook's extension case arm no longer parses"
    extensions = [pattern.removeprefix("*.") for pattern in arm.group(1).split("|")]
    assert len(extensions) >= _DATA_EXTENSION_COUNT_FLOOR, extensions
    return extensions


def _data_guard_tree(root: Path) -> list[str]:
    """Relative names that exercise each data-file rule, plus allowed neighbours."""
    plain_files = (
        "src/app.py", "Downloads/notes.txt", "downloads/notes.txt",
        "x/exports-private/a.txt", "x/PRIVATE/a.txt", "x/private/a.txt",
    )
    for plain in plain_files:
        (root / plain).parent.mkdir(parents=True, exist_ok=True)
        (root / plain).write_text("plain text\n")
    return [
        "src/app.py",
        "data/report.csv", "data/REPORT.CSV", "data/dump.parquet", "data/table.db", "data/model.pkl", "data/export.csv/",
        "data/report.csv.txt",
        "Downloads/notes.txt", "downloads/notes.txt", "Downloads",
        "x/exports-private/a.txt", "x/PRIVATE/a.txt", "x/private/a.txt",
        *(f"data/x.{extension}" for extension in _hook_data_extensions()),
    ]


def test_data_guard_scan_matches_the_data_file_read_hook_over_extension_downloads_glob_and_case(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    home = _data_guard_home(tmp_path)
    names = _data_guard_tree(root)

    hook = [_data_guard_read_verdict(f"{root}/{name}", home) for name in names]
    scan = ["deny" if hit == 1 else "allow" for hit in _data_guard_scan(names, root, home)]

    assert scan == hook
    assert set(hook) == {"allow", "deny"}
    assert dict(zip(names, hook, strict=True)) == {
        **{f"data/x.{extension}": "deny" for extension in _hook_data_extensions()},
        "src/app.py": "allow",
        "data/report.csv": "deny", "data/REPORT.CSV": "allow", "data/dump.parquet": "deny", "data/table.db": "deny",
        "data/model.pkl": "deny", "data/export.csv/": "deny", "data/report.csv.txt": "allow",
        "Downloads/notes.txt": "deny", "downloads/notes.txt": "allow", "Downloads": "allow",
        "x/exports-private/a.txt": "deny", "x/PRIVATE/a.txt": "deny", "x/private/a.txt": "allow",
    }


def test_data_guard_scan_marks_nothing_while_the_guard_file_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    home = _data_guard_home(tmp_path, armed=False)
    names = _data_guard_tree(root)

    assert _data_guard_scan(names, root, home) == [0] * len(names)
    assert {_data_guard_read_verdict(f"{root}/data/report.csv", home)} == {"allow"}


def test_data_guard_with_an_empty_guard_file_applies_the_built_in_rules_only(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    home = _data_guard_home(tmp_path, globs="")

    assert _data_guard_scan(["data/report.csv", "x/exports-private/a.txt"], root, home) == [1, 0]


def test_data_guard_prefers_the_config_dir_copy_over_the_legacy_one_like_the_hook(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    home = _data_guard_home(tmp_path, globs="*/legacy-only/*\n")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "data-file-read-guard.md").write_text("*/current-only/*\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir)}

    process = _bash(
        'root=$1; shift\nPR_DIFF_SCAN_TEXT=("$@")\nPR_DIFF_GUARD_GLOBS=()\n_pr_diff_load_data_guard\n'
        '_pr_diff_scan_withheld "$root"\nprintf "%s" "${PR_DIFF_SCAN_HIT[*]}"',
        str(root), "a/legacy-only/x.txt", "a/current-only/x.txt", env=env,
    )
    hook_verdicts = [
        run_hook(_DENY_DATA_FILE_READS_HOOK, {"tool_name": "Read", "tool_input": {"file_path": f"{root}/{name}"}}, home=home,
                 extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)})
        for name in ("a/legacy-only/x.txt", "a/current-only/x.txt")
    ]

    assert hook_verdicts == ["allow", "deny"]
    assert process.stdout.decode().split() == ["0", "1"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_data_guard_is_inactive_for_an_unreadable_guard_file_like_the_hook(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    home = _data_guard_home(tmp_path)
    guard_file = home / ".claude" / "data-file-read-guard.md"
    guard_file.chmod(0)

    try:
        scan = _data_guard_scan(["data/report.csv"], root, home)
        hook = _data_guard_read_verdict(f"{root}/data/report.csv", home)
    finally:
        guard_file.chmod(0o600)

    assert scan == [0]
    assert hook == "allow"


def test_data_guard_scan_withholds_a_credential_free_data_name_in_a_history_header_list(tmp_path: Path) -> None:
    """The same scan serves the history-header names, so an earlier name with a data extension is marked too."""
    root = tmp_path / "work"
    root.mkdir()
    home = _data_guard_home(tmp_path)

    assert _data_guard_scan(["old/records.csv", "old/records.txt"], root, home) == [1, 0]


# --- hostile keys in the numstat tables ---------------------------------------------------------------------------------

_HOSTILE_KEYS = ["a]b", "$(touch EVALUATED)", "@", "*", "a b", "`touch EVALUATED`", "x[0]", "q'\"r", "~", "-"]


@pytest.mark.parametrize("key", _HOSTILE_KEYS, ids=repr)
def test_numstat_lookups_take_a_hostile_path_as_a_literal_key(tmp_path: Path, key: str) -> None:
    """The raw row's path is looked up in the numstat table through nested subscripts; none of these keys may evaluate."""
    marker = tmp_path / "EVALUATED"
    path = key.replace("EVALUATED", str(marker))
    process = _classify(tmp_path, _raw_record("M", path), _numstat_bytes(("3", "1", path)))

    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert process.stdout.decode() == f"{path}=;"
    assert not marker.exists()


def test_numstat_lookup_of_a_key_missing_from_the_table_halts_instead_of_matching_a_glob(tmp_path: Path) -> None:
    """A raw path of `*` finds no entry when the numstat names a different file: keys are literal, never patterns."""
    process = _classify(tmp_path, _raw_record("M", "*"), _numstat_bytes(("3", "1", "real.txt")))

    assert process.stdout.decode().startswith("ERR:no numstat entry for *")
