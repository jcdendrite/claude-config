"""Tests for pr-create-preflight.py, the script behind /ready-for-review steps 1 and 8.

One layer per part of the contract, so a failure is localized:
- The pure layer imports the script and calls its TICKET-ID, title, sink-rule and body-check functions.
- The `main` layer runs `main` in-process with git patched, so branch names git refuses reach it too.
- The git-backed layer runs `main` against a scratch repository.
- The subprocess layer covers exit status, stderr and the `&&` chain at the process boundary.
TestSkillContract ties the keys and command lines the skill names to the script's.

Python-floor tests hold the script to 3.11 grammar. They do not cover stdlib APIs added after 3.11.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import shlex
import stat
import string
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import SKILLS_DIR, init_git_repo_with_commit

from .conftest import _INHERITED_GIT_VARIABLES
from .test_plan_push_preflight import _fstring_offenders

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "pr-create-preflight.py"
_SKILL_MD = SKILLS_DIR / "ready-for-review" / "SKILL.md"

# A harness bound so a child blocked on a FIFO fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60

_TITLE_ALLOWED_CHARACTERS = string.ascii_letters + string.digits + "._: -"
_PATH_ALLOWED_CHARACTERS = string.ascii_letters + string.digits + "._/-"
_PATH_PATTERN_FOR_FIXTURES = r"/[A-Za-z0-9._/-]*"

_USAGE_LINE_PREFIX = "REASON: usage: "
_TITLE_SENTENCE_START = "REASON: not-plain-word: the title is empty or holds a character outside"
_PATH_SENTENCE_START = "REASON: not-plain-word: the body path is not absolute or holds a character outside"

_WHITESPACE_BYTES = (b" ", b"\t", b"\n", b"\v", b"\f", b"\r")


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keeps the contributor's git config and any inherited repository selection out of every git call."""
    for variable in _INHERITED_GIT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


@pytest.fixture(scope="module")
def preflight():
    """The script as a module. Loading inside a fixture makes a missing script fail each test, not collection."""
    spec = importlib.util.spec_from_file_location("pr_create_preflight", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plain_tmp_path(tmp_path: Path) -> Path:
    """A scratch directory whose path the body-path sink rule admits."""
    assert re.fullmatch(_PATH_PATTERN_FOR_FIXTURES, str(tmp_path)), (
        f"pytest's temp directory {str(tmp_path)!r} holds a character the body-path sink rule refuses. "
        "The base temp directory embeds the login and TMPDIR, so re-run with --basetemp=<plain absolute path>."
    )
    return tmp_path


def _stop_of(preflight, function, *arguments) -> tuple[str, str] | None:
    """The (code, sentence) a call stops with, or None when it returns."""
    try:
        function(*arguments)
    except preflight.Stop as stop:
        return stop.code, stop.sentence
    return None


def _title_for(preflight, branch: str) -> str:
    return preflight._derive_title(preflight._ticket_id(branch), preflight._slug_words(branch))


class TestTicketId:
    @pytest.mark.parametrize(
        ("branch", "expected"),
        [
            ("GH-12/x", "GH-12"),
            ("fix-12", "fix-12"),
            ("gh-1/x", "gh-1"),
            ("GH-1/sub/slug", "GH-1"),
        ],
        ids=["prefix-slash", "bare-branch-matches", "case-kept", "first-segment-only"],
    )
    def test_matching_first_segment_is_the_id(self, preflight, branch: str, expected: str) -> None:
        assert preflight._ticket_id(branch) == expected

    @pytest.mark.parametrize(
        "branch",
        [
            "GH-12abc/x",
            "GH-/x",
            "GH-1-2/x",
            "user/GH-1/slug",
            "-1/x",
            "GH-\uff11/x",
            "é-1/x",
            "\u212a-1/x",
            "GH-1\n/x",
        ],
        ids=[
            "trailing-letters",
            "no-digits",
            "two-hyphens",
            "non-first-segment",
            "no-letters",
            "fullwidth-digit",
            "non-ascii-letter",
            "kelvin-sign",
            "trailing-newline",
        ],
    )
    def test_anything_else_has_no_id(self, preflight, branch: str) -> None:
        assert preflight._ticket_id(branch) is None

    @pytest.mark.parametrize(
        ("branch", "expected_words"),
        [
            ("user/GH-1/slug", ["slug"]),
            ("GH-1/sub/slug", ["slug"]),
            ("fix-the-bug", ["fix", "the", "bug"]),
            ("topic/--a---b--", ["a", "b"]),
            ("topic/---", []),
        ],
        ids=["last-segment", "last-segment-after-id", "no-slash", "empty-pieces-dropped", "no-words"],
    )
    def test_slug_words_come_from_the_last_segment(self, preflight, branch: str, expected_words: list[str]) -> None:
        assert preflight._slug_words(branch) == expected_words


_SEVEN_WORDS_70 = "aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ffffffffff-gggg"
_TITLE_70 = "aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee ffffffffff gggg"
_SIX_WORDS_65 = "aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee ffffffffff"
_PREFIXED_TITLE_70 = "TICKET-1: aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee ggggg"
_PREFIXED_FIVE_WORDS_64 = "TICKET-1: aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee"

# (branch, expected title). Expected values are literals, so none comes from the code under test.
_TITLE_ROWS = [
    pytest.param("topic/fix-the-bug", "fix the bug", id="plain-slug"),
    pytest.param("TICKET-1/fix-the-bug", "TICKET-1: fix the bug", id="plain-slug-with-id"),
    pytest.param("GH-42", "GH-42: GH 42", id="bare-id-branch-uses-the-uniform-rule"),
    pytest.param("topic/" + _SEVEN_WORDS_70, _TITLE_70, id="exactly-70"),
    pytest.param(
        "TICKET-1/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ggggg",
        _PREFIXED_TITLE_70,
        id="exactly-70-with-id",
    ),
    pytest.param(
        "topic/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ffffffffff-ggggg",
        _SIX_WORDS_65,
        id="71-drops-the-last-word",
    ),
    pytest.param(
        "TICKET-1/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-gggggg",
        _PREFIXED_FIVE_WORDS_64,
        id="71-drops-the-last-word-with-id",
    ),
    pytest.param(
        "topic/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ffffffffff-gggggg-h",
        _SIX_WORDS_65,
        id="a-shorter-word-after-the-cut-stays-dropped",
    ),
    pytest.param(
        "TICKET-1/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-gggggg-h",
        _PREFIXED_FIVE_WORDS_64,
        id="a-shorter-word-after-the-cut-stays-dropped-with-id",
    ),
    pytest.param("topic/" + _SEVEN_WORDS_70 + "-hhh", _TITLE_70, id="70-kept-when-a-word-follows"),
    pytest.param(
        "TICKET-1/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ggggg-hhh",
        _PREFIXED_TITLE_70,
        id="70-kept-when-a-word-follows-with-id",
    ),
    pytest.param("topic/aaaaaaaaaa-" + "b" * 61, "aaaaaaaaaa", id="first-word-fits-second-does-not"),
    pytest.param(
        "TICKET-1/aaaaaaaaaa-" + "b" * 51, "TICKET-1: aaaaaaaaaa", id="first-word-fits-second-does-not-with-id"
    ),
    pytest.param("topic/" + "a" * 75 + "-bbb", "a" * 70, id="first-word-too-long-alone"),
    pytest.param(
        "TICKET-1/" + "a" * 75, "TICKET-1: " + "a" * 60, id="first-word-too-long-alone-with-id"
    ),
    pytest.param(
        "topic/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ffffffffff--ggggg",
        _SIX_WORDS_65,
        id="consecutive-hyphens-at-the-cut",
    ),
    pytest.param(
        "TICKET-1/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee--gggggg",
        _PREFIXED_FIVE_WORDS_64,
        id="consecutive-hyphens-at-the-cut-with-id",
    ),
    pytest.param("topic/fix-bug-", "fix bug", id="trailing-hyphen"),
    pytest.param("TICKET-1/fix-bug-", "TICKET-1: fix bug", id="trailing-hyphen-with-id"),
    pytest.param("topic/-fix-bug", "fix bug", id="leading-hyphen"),
    pytest.param("TICKET-1/-fix-bug", "TICKET-1: fix bug", id="leading-hyphen-with-id"),
    pytest.param("topic/---", "", id="all-hyphen-slug"),
    pytest.param("TICKET-1/---", "", id="all-hyphen-slug-with-id"),
]


class TestTitle:
    @pytest.mark.parametrize(("branch", "expected_title"), _TITLE_ROWS)
    def test_title_matches_the_expected_literal_and_fits(self, preflight, branch: str, expected_title: str) -> None:
        title = _title_for(preflight, branch)

        assert title == expected_title
        assert len(title) <= 70

    def test_an_id_whose_prefix_fills_70_characters_cuts_to_the_id_and_colon(self, preflight) -> None:
        long_id = "A" * 66 + "-1"
        assert len(long_id) + len(": ") == 70

        title = _title_for(preflight, long_id + "/x")

        assert title == long_id + ":"
        assert len(title) == 69

    def test_the_table_literals_have_the_lengths_the_rows_name(self) -> None:
        assert len(_TITLE_70) == 70
        assert len(_PREFIXED_TITLE_70) == 70
        assert len(_SIX_WORDS_65) == 65
        assert len(_PREFIXED_FIVE_WORDS_64) == 64


_GOOD_TITLE = "TICKET-1: fix the bug"
_GOOD_PATH = "/tmp/pr-body.AbC123"


class TestSinkRule:
    @pytest.mark.parametrize("title", [_GOOD_TITLE, "a.b_c:d-e", "a" * 70], ids=["plain", "all-punctuation", "70-chars"])
    def test_title_rule_accepts_plain_titles(self, preflight, title: str) -> None:
        assert _stop_of(preflight, preflight._require_plain_title, title) is None

    @pytest.mark.parametrize(
        "path", [_GOOD_PATH, "/var//folders/ab/pr-body.XyZ", "/"], ids=["mktemp-shape", "doubled-slash", "root"]
    )
    def test_path_rule_accepts_plain_paths(self, preflight, path: str) -> None:
        assert _stop_of(preflight, preflight._require_plain_body_path, path) is None

    @pytest.mark.parametrize("hazard", ['"', "$", "`", "\\", "'", ";", "<", "[", "\t", "é"], ids=repr)
    def test_title_rule_refuses_a_hazard_inside_an_accepted_value(self, preflight, hazard: str) -> None:
        stop = _stop_of(preflight, preflight._require_plain_title, "ab" + hazard + "cd")

        assert stop is not None
        assert stop[0] == "not-plain-word"

    @pytest.mark.parametrize("hazard", ['"', "$", "`", "\\", "'", ";", "<", "[", "\t", "é"], ids=repr)
    def test_path_rule_refuses_a_hazard_inside_an_accepted_value(self, preflight, hazard: str) -> None:
        stop = _stop_of(preflight, preflight._require_plain_body_path, "/tmp/ab" + hazard + "cd")

        assert stop is not None
        assert stop[0] == "not-plain-word"

    def test_title_rule_refuses_empty_trailing_newline_and_slash(self, preflight) -> None:
        for value in ("", _GOOD_TITLE + "\n", "a/b"):
            assert _stop_of(preflight, preflight._require_plain_title, value) is not None, repr(value)

    def test_path_rule_refuses_empty_trailing_newline_relative_tilde_space_and_colon(self, preflight) -> None:
        for value in ("", _GOOD_PATH + "\n", "tmp/x", "~/x", "/tmp/a b", "/tmp/a:b"):
            assert _stop_of(preflight, preflight._require_plain_body_path, value) is not None, repr(value)

    def test_each_rule_refuses_with_its_own_fixed_sentence(self, preflight) -> None:
        title_stop = _stop_of(preflight, preflight._require_plain_title, "")
        path_stop = _stop_of(preflight, preflight._require_plain_body_path, "")

        assert title_stop is not None
        assert path_stop is not None
        assert title_stop[1] != path_stop[1]
        assert "empty" in title_stop[1]
        assert "not absolute" in path_stop[1]

    def test_title_rule_accepts_exactly_the_allowed_set_across_code_points_0_to_255(self, preflight) -> None:
        accepted = {
            character
            for character in map(chr, range(256))
            if _stop_of(preflight, preflight._require_plain_title, "a" + character + "a") is None
        }

        assert accepted == set(_TITLE_ALLOWED_CHARACTERS)

    def test_path_rule_accepts_exactly_the_allowed_set_across_code_points_0_to_255(self, preflight) -> None:
        accepted = {
            character
            for character in map(chr, range(256))
            if _stop_of(preflight, preflight._require_plain_body_path, "/a" + character + "a") is None
        }

        assert accepted == set(_PATH_ALLOWED_CHARACTERS)


class TestBodyCheck:
    """A truncated write can leave only a newline, so the check refuses a whitespace-only file, not merely a zero-byte file."""

    @pytest.mark.parametrize("whitespace", _WHITESPACE_BYTES, ids=repr)
    def test_each_whitespace_byte_alone_is_empty(self, preflight, tmp_path: Path, whitespace: bytes) -> None:
        body = tmp_path / "body"
        body.write_bytes(whitespace)

        stop = _stop_of(preflight, preflight._require_body_content, str(body))

        assert stop is not None
        assert stop[0] == "body-empty"

    def test_all_six_whitespace_bytes_together_and_an_empty_file_are_empty(
        self, preflight, tmp_path: Path
    ) -> None:
        body = tmp_path / "body"
        for content in (b"".join(_WHITESPACE_BYTES), b""):
            body.write_bytes(content)

            stop = _stop_of(preflight, preflight._require_body_content, str(body))

            assert stop is not None, content
            assert stop[0] == "body-empty"

    @pytest.mark.parametrize(
        "content",
        [b"x", b"\x1c", b"\x85", b"\xa0", "\xa0".encode(), b"\xff", b"\x00", b"  x  \n"],
        ids=["letter", "file-separator", "next-line", "latin1-nbsp", "utf8-nbsp", "invalid-utf8", "nul", "padded"],
    )
    def test_any_other_byte_is_content(self, preflight, tmp_path: Path, content: bytes) -> None:
        """A NUL-only file is content here although `[ -n "$(tr -d '[:space:]' < file)" ]` refuses it.

        Bash command substitution drops NUL bytes, so that test sees an empty string.

        The file-separator, next-line, latin1-nbsp, utf8-nbsp and invalid-utf8 rows are bytes a check that
        decodes to str would count as whitespace or fail to decode.
        The letter and padded rows are the plain-content baseline, padded with whitespace at both ends.
        """
        body = tmp_path / "body"
        body.write_bytes(content)

        assert _stop_of(preflight, preflight._require_body_content, str(body)) is None

    def test_a_missing_path_and_a_directory_are_unreadable(self, preflight, tmp_path: Path) -> None:
        for path in (tmp_path / "missing", tmp_path):
            stop = _stop_of(preflight, preflight._require_body_content, str(path))

            assert stop is not None, path
            assert stop[0] == "body-unreadable"

    def test_a_character_device_is_unreadable_not_empty(self, preflight) -> None:
        """This is the in-process row that needs the regular-file check.

        Opening a directory raises OSError into the same code anyway.
        """
        null_device = Path(os.devnull)
        if not null_device.exists() or not stat.S_ISCHR(null_device.stat().st_mode):
            pytest.skip(f"{null_device} is not a character device on this platform")

        stop = _stop_of(preflight, preflight._require_body_content, str(null_device))

        assert stop is not None
        assert stop[0] == "body-unreadable"

    def test_a_symlink_to_a_regular_file_passes_as_the_shell_file_test_does(
        self, preflight, tmp_path: Path
    ) -> None:
        target = tmp_path / "target"
        target.write_bytes(b"x")
        link = tmp_path / "link"
        link.symlink_to(target)

        assert _stop_of(preflight, preflight._require_body_content, str(link)) is None

    def test_an_open_error_on_a_regular_file_is_unreadable(
        self, preflight, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pins that the open sits inside the one `try`, so an `OSError` there maps to body-unreadable.

        Does not pin the read's placement: the open raises first, so the read is never reached.
        """
        body = tmp_path / "body"
        body.write_bytes(b"x")

        def raising_open(*_arguments, **_keywords):
            raise PermissionError("denied")

        monkeypatch.setattr(preflight, "open", raising_open, raising=False)

        stop = _stop_of(preflight, preflight._require_body_content, str(body))

        assert stop is not None
        assert stop[0] == "body-unreadable"


@dataclass
class MainResult:
    status: int
    stdout: str
    stderr: bytes

    @property
    def lines(self) -> list[str]:
        return self.stdout[:-1].split("\n")


def _pretend_head(preflight, monkeypatch: pytest.MonkeyPatch, branch: str | None) -> None:
    """Makes the script's git call answer as a checkout of `branch`, or as a detached HEAD when None."""

    def fake_git(*_arguments: str) -> subprocess.CompletedProcess:
        if branch is None:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="refs/heads/" + branch + "\n", stderr="")

    monkeypatch.setattr(preflight, "_git", fake_git)


def _run_main(preflight, capsysbinary, argv: list[str]) -> MainResult:
    status = preflight.main(argv)
    captured = capsysbinary.readouterr()
    return MainResult(status, captured.out.decode("utf-8"), captured.err)


def _write_body(directory: Path, content: bytes = b"A body.\n") -> str:
    body = directory / "body"
    body.write_bytes(content)
    return str(body)


class TestMain:
    def test_ticket_prints_the_id(self, preflight, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "GH-12/fix-it")

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout) == (0, "TICKET_ID: GH-12\n")

    def test_ticket_prints_none_when_the_branch_has_no_id(self, preflight, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "fix-it")

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout) == (0, "TICKET_ID: none\n")

    def test_ticket_ignores_a_branch_name_the_title_rule_would_refuse(self, preflight, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "GH-1/it's-fix")

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout) == (0, "TICKET_ID: GH-1\n")

    def test_create_stops_on_a_branch_name_whose_title_is_not_plain(
        self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary
    ) -> None:
        _pretend_head(preflight, monkeypatch, "GH-1/it's-fix")

        result = _run_main(preflight, capsysbinary, ["create", _write_body(plain_tmp_path)])

        assert result.status == 3
        assert result.stdout.startswith(_TITLE_SENTENCE_START)
        assert "it's" not in result.stdout

    def test_create_prints_the_exact_title_line(self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "GH-12/fix-the-bug")

        result = _run_main(preflight, capsysbinary, ["create", _write_body(plain_tmp_path)])

        assert (result.status, result.stdout) == (0, "TITLE: GH-12: fix the bug\n")

    def test_the_rule_judges_the_printed_title_when_the_cut_drops_the_only_unsafe_word(
        self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary
    ) -> None:
        """Pins that the rule runs on the truncated title and that the same title is the one printed."""
        branch = "TICKET-1/aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-ggggg-it's"
        _pretend_head(preflight, monkeypatch, branch)

        result = _run_main(preflight, capsysbinary, ["create", _write_body(plain_tmp_path)])

        assert (result.status, result.stdout) == (0, "TITLE: " + _PREFIXED_TITLE_70 + "\n")

    def test_the_title_check_comes_before_the_path_check(self, preflight, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "topic/---")

        result = _run_main(preflight, capsysbinary, ["create", "relative path"])

        assert result.status == 3
        assert result.stdout.startswith(_TITLE_SENTENCE_START)

    def test_the_path_check_comes_before_the_file_check(self, preflight, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "topic/fix-it")

        plain_missing = _run_main(preflight, capsysbinary, ["create", "/missing/pr-body.x"])
        spaced_missing = _run_main(preflight, capsysbinary, ["create", "/missing/pr body"])

        assert plain_missing.stdout.startswith("REASON: body-unreadable: ")
        assert spaced_missing.stdout.startswith(_PATH_SENTENCE_START)

    def test_a_detached_head_stops_before_the_sink_rule_judges_a_bad_path(
        self, preflight, monkeypatch, capsysbinary
    ) -> None:
        _pretend_head(preflight, monkeypatch, None)

        result = _run_main(preflight, capsysbinary, ["create", "relative"])

        assert result.status == 3
        assert result.stdout.startswith("REASON: not-on-branch: ")

    def test_a_head_outside_refs_heads_is_not_on_a_branch(self, preflight, monkeypatch, capsysbinary) -> None:
        monkeypatch.setattr(
            preflight, "_git", lambda *_a: subprocess.CompletedProcess([], 0, stdout="refs/tags/v1\n", stderr="")
        )

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout.startswith("REASON: not-on-branch: ")) == (3, True)

    def test_an_unexpected_exception_prints_only_its_type_name_and_exits_1(
        self, preflight, monkeypatch, capsysbinary
    ) -> None:
        def raising_git(*_arguments: str):
            raise RuntimeError("secret detail /private/path")

        monkeypatch.setattr(preflight, "_git", raising_git)

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout, result.stderr) == (1, "REASON: internal-error: RuntimeError\n", b"")

    @pytest.mark.parametrize(
        "argv",
        [[], ["create"], ["create", "/a", "/b"], ["ticket", "extra"], ["unknown"], ["-h"], ["--help"], [""]],
        ids=["none", "create-no-path", "create-two-paths", "ticket-extra", "unknown-mode", "dash-h", "help", "empty"],
    )
    def test_usage_errors_exit_2_with_one_usage_line(self, preflight, capsysbinary, argv: list[str]) -> None:
        result = _run_main(preflight, capsysbinary, argv)

        assert result.status == 2
        assert len(result.lines) == 1
        assert result.lines[0].startswith(_USAGE_LINE_PREFIX)
        assert result.stderr == b""

    def test_the_usage_line_is_the_fixed_sentence_and_echoes_no_argument(self, preflight, capsysbinary) -> None:
        result = _run_main(preflight, capsysbinary, ["create", "/a", "/b"])

        assert result.status == 2
        assert result.lines == [_USAGE_LINE_PREFIX + preflight._USAGE_SENTENCE]

    def test_the_line_builder_refuses_an_unknown_key(self, preflight) -> None:
        with pytest.raises(RuntimeError):
            preflight._line("OTHER", "x")


def _docstring_reason_codes(preflight) -> tuple[str, ...]:
    match = re.search(r"^Reason codes: ([a-z0-9, \n-]+)\n\n", preflight.__doc__, flags=re.MULTILINE)
    assert match is not None
    return tuple(re.split(r",\s*", " ".join(match.group(1).split())))


# Each scenario names a stop the script can reach and the status `main` must return for it.
# Arguments are (argv factory taking a plain temp directory, head branch or None for detached HEAD, stop code,
# name of the script's `_SENTENCE_*` constant that stop must print).
_STOP_SCENARIOS = {
    "ticket-detached": (lambda directory: ["ticket"], None, "not-on-branch", "_SENTENCE_NOT_ON_BRANCH"),
    "create-detached": (
        lambda directory: ["create", _write_body(directory)],
        None,
        "not-on-branch",
        "_SENTENCE_NOT_ON_BRANCH",
    ),
    "empty-title": (
        lambda directory: ["create", _write_body(directory)],
        "topic/---",
        "not-plain-word",
        "_SENTENCE_TITLE_NOT_PLAIN",
    ),
    "unsafe-title": (
        lambda directory: ["create", _write_body(directory)],
        "topic/it's",
        "not-plain-word",
        "_SENTENCE_TITLE_NOT_PLAIN",
    ),
    "relative-path": (
        lambda directory: ["create", "relative"],
        "topic/fix",
        "not-plain-word",
        "_SENTENCE_PATH_NOT_PLAIN",
    ),
    "missing-file": (
        lambda directory: ["create", str(directory / "missing")],
        "topic/fix",
        "body-unreadable",
        "_SENTENCE_BODY_UNREADABLE",
    ),
    "directory": (
        lambda directory: ["create", str(directory)],
        "topic/fix",
        "body-unreadable",
        "_SENTENCE_BODY_UNREADABLE",
    ),
    "whitespace-file": (
        lambda directory: ["create", _write_body(directory, b" \n")],
        "topic/fix",
        "body-empty",
        "_SENTENCE_BODY_EMPTY",
    ),
}


class TestExitStatus:
    """A stop must end a `&&` chain, so every stop exits 3 and only a value line exits 0."""

    @pytest.mark.parametrize("scenario", sorted(_STOP_SCENARIOS))
    def test_every_stop_exits_3_with_one_reason_line_and_no_value_line(
        self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary, scenario: str
    ) -> None:
        make_argv, branch, code, _sentence_constant = _STOP_SCENARIOS[scenario]
        _pretend_head(preflight, monkeypatch, branch)

        result = _run_main(preflight, capsysbinary, make_argv(plain_tmp_path))

        assert result.status == 3
        assert len(result.lines) == 1
        assert result.lines[0].startswith("REASON: " + code + ": ")
        assert result.stderr == b""

    @pytest.mark.parametrize("scenario", sorted(_STOP_SCENARIOS))
    def test_a_stop_prints_fixed_sentence_text_that_echoes_no_input(
        self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary, scenario: str
    ) -> None:
        make_argv, branch, code, sentence_constant = _STOP_SCENARIOS[scenario]
        _pretend_head(preflight, monkeypatch, branch)

        result = _run_main(preflight, capsysbinary, make_argv(plain_tmp_path))

        assert result.lines[0] == "REASON: " + code + ": " + getattr(preflight, sentence_constant)

    def test_the_scenarios_cover_every_documented_stop_code(self, preflight) -> None:
        stop_codes = set(_docstring_reason_codes(preflight)) - {"usage", "internal-error"}

        assert {code for _argv, _branch, code, _sentence in _STOP_SCENARIOS.values()} == stop_codes

    def test_a_value_line_exits_0_for_each_mode(self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary) -> None:
        _pretend_head(preflight, monkeypatch, "GH-3/fix")

        ticket = _run_main(preflight, capsysbinary, ["ticket"])
        create = _run_main(preflight, capsysbinary, ["create", _write_body(plain_tmp_path)])

        assert (ticket.status, ticket.lines[0].startswith("TICKET_ID: ")) == (0, True)
        assert (create.status, create.lines[0].startswith("TITLE: ")) == (0, True)


class TestGitBacked:
    @pytest.fixture
    def repo(self, plain_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        repository = init_git_repo_with_commit(plain_tmp_path / "repo", branch="GH-5/fix-the-thing")
        monkeypatch.chdir(repository)
        return repository

    def test_ticket_reads_the_checked_out_branch(self, preflight, repo: Path, capsysbinary) -> None:
        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout) == (0, "TICKET_ID: GH-5\n")

    def test_create_derives_the_title_from_the_checked_out_branch(
        self, preflight, repo: Path, plain_tmp_path: Path, capsysbinary
    ) -> None:
        result = _run_main(preflight, capsysbinary, ["create", _write_body(plain_tmp_path)])

        assert (result.status, result.stdout) == (0, "TITLE: GH-5: fix the thing\n")

    @pytest.mark.parametrize("mode_argv", [["ticket"], ["create", "/unused"]], ids=["ticket", "create"])
    def test_a_detached_head_is_not_on_a_branch(self, preflight, repo: Path, capsysbinary, mode_argv: list[str]) -> None:
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)

        result = _run_main(preflight, capsysbinary, mode_argv)

        assert (result.status, result.stdout.startswith("REASON: not-on-branch: ")) == (3, True)

    def test_a_directory_that_is_not_a_repository_is_not_on_a_branch(
        self, preflight, plain_tmp_path: Path, monkeypatch, capsysbinary
    ) -> None:
        plain = plain_tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout.startswith("REASON: not-on-branch: ")) == (3, True)

    def test_a_head_pointing_at_a_tag_ref_is_not_on_a_branch(self, preflight, repo: Path, capsysbinary) -> None:
        subprocess.run(["git", "tag", "v1"], cwd=repo, check=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/tags/v1"], cwd=repo, check=True)

        result = _run_main(preflight, capsysbinary, ["ticket"])

        assert (result.status, result.stdout.startswith("REASON: not-on-branch: ")) == (3, True)


def _child_env(**updates: str) -> dict[str, str]:
    return {**os.environ, **updates}


def _run_script(*arguments: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *arguments],
        cwd=cwd,
        env=env if env is not None else _child_env(),
        capture_output=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


class TestProcessBoundary:
    @pytest.fixture
    def repo(self, plain_tmp_path: Path) -> Path:
        return init_git_repo_with_commit(plain_tmp_path / "repo", branch="GH-5/fix-the-thing")

    def test_a_passing_create_exits_0_with_the_title_and_empty_stderr(self, repo: Path, plain_tmp_path: Path) -> None:
        result = _run_script("create", _write_body(plain_tmp_path), cwd=repo)

        assert (result.returncode, result.stdout, result.stderr) == (0, b"TITLE: GH-5: fix the thing\n", b"")

    def test_help_is_a_usage_error(self, repo: Path) -> None:
        result = _run_script("--help", cwd=repo)

        assert result.returncode == 2
        assert result.stdout.startswith(_USAGE_LINE_PREFIX.encode())
        assert result.stderr == b""

    def test_a_fifo_body_path_is_unreadable_without_blocking(self, repo: Path, plain_tmp_path: Path) -> None:
        fifo = plain_tmp_path / "fifo"
        os.mkfifo(fifo)

        result = _run_script("create", str(fifo), cwd=repo)

        assert result.returncode == 3
        assert result.stdout.startswith(b"REASON: body-unreadable: ")
        assert result.stderr == b""

    def test_a_stop_ends_an_and_chain_before_the_next_command_runs(self, repo: Path, plain_tmp_path: Path) -> None:
        empty_body = plain_tmp_path / "empty"
        empty_body.write_bytes(b"")
        chain = (
            shlex.quote(sys.executable) + " " + shlex.quote(str(_SCRIPT_PATH))
            + " create " + shlex.quote(str(empty_body)) + " && echo reached"
        )

        completed = subprocess.run(
            ["bash", "-c", chain], cwd=repo, env=_child_env(), capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS
        )

        assert b"reached" not in completed.stdout
        assert completed.stdout.startswith(b"REASON: body-empty: ")

    def test_git_writing_to_stderr_is_not_relayed(self, plain_tmp_path: Path) -> None:
        plain = plain_tmp_path / "plain"
        plain.mkdir()

        result = _run_script("create", "/unused", cwd=plain)

        assert result.returncode == 3
        assert result.stdout.startswith(b"REASON: not-on-branch: ")
        assert result.stderr == b""

    def test_a_git_that_cannot_start_ends_as_an_internal_error(self, plain_tmp_path: Path) -> None:
        """An existing empty PATH directory, because deleting PATH falls back to /bin:/usr/bin, where git lives."""
        empty_path_directory = plain_tmp_path / "empty-path"
        empty_path_directory.mkdir()
        working_directory = plain_tmp_path / "work"
        working_directory.mkdir()

        result = _run_script(
            "create", "/unused", cwd=working_directory, env=_child_env(PATH=str(empty_path_directory))
        )

        assert result.returncode == 1
        assert result.stdout == b"REASON: internal-error: FileNotFoundError\n"
        assert result.stderr == b""

    def test_the_script_is_executable(self) -> None:
        assert os.access(_SCRIPT_PATH, os.X_OK)


class TestDocstringContract:
    def test_docstring_keys_line_equals_the_key_constant(self, preflight) -> None:
        match = re.search(r"^Keys: ([A-Z_, ]+)$", preflight.__doc__, flags=re.MULTILINE)

        assert match is not None
        assert tuple(re.split(r",\s*", match.group(1))) == preflight._KEYS

    def test_docstring_reason_codes_line_equals_the_code_constant(self, preflight) -> None:
        assert _docstring_reason_codes(preflight) == preflight._REASON_CODES


class TestPythonFloor:
    def test_script_parses_under_python_311_grammar(self) -> None:
        """Covers 3.11 grammar except PEP 701 f-strings, which parse accepts."""
        ast.parse(_SCRIPT_PATH.read_text(), feature_version=(3, 11))

    @pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 f-strings only parse on 3.12+")
    def test_script_has_no_fstring_that_only_python_312_accepts(self) -> None:
        """Covers the PEP 701 f-string forms that feature_version=(3, 11) accepts."""
        assert _fstring_offenders('f"{d["k"]}"') != [], "the check must flag a nested string literal"

        assert _fstring_offenders(_SCRIPT_PATH.read_text()) == []


_STEP1_START = "\n## 1. Preconditions (halt on fail)\n"
_STEP2_START = "\n## 2."
_STEP8_START = "\n## 8. Create PR if missing (skip if PR already exists)\n"
_STEP9_START = "\n## 9."


def _skill_section(start: str, end: str) -> str:
    text = _SKILL_MD.read_text()
    assert start in text, f"ready-for-review/SKILL.md no longer has the {start.strip()!r} heading"
    after_heading = text.split(start, 1)[1]
    assert end in after_heading, f"ready-for-review/SKILL.md has no {end.strip()!r} heading to bound {start.strip()!r}"
    return after_heading.split(end, 1)[0]


# test_skills.py pins the full clauses around these commands.
class TestSkillContract:
    def test_step1_runs_ticket_mode_and_names_its_key_and_the_none_value(self, preflight) -> None:
        section = _skill_section(_STEP1_START, _STEP2_START)
        command = f"~/.claude/scripts/{_SCRIPT_PATH.name} {preflight._MODE_TICKET}"

        ticket_key = "TICKET_ID"
        assert ticket_key in preflight._KEYS
        assert f"`python3 {command}`" in section
        assert f"`{ticket_key}`" in section
        assert f"`{preflight._NO_TICKET_ID}`" in section

    def test_step8_runs_create_mode_and_names_its_keys(self, preflight) -> None:
        section = _skill_section(_STEP8_START, _STEP9_START)
        command = f"~/.claude/scripts/{_SCRIPT_PATH.name} {preflight._MODE_CREATE} '<path>'"

        assert f"`python3 {command}`" in section
        assert f"`python3 {command} && gh pr create --title" in section
        for key in ("TITLE", "REASON"):
            assert key in preflight._KEYS
            assert f"`{key}`" in section
