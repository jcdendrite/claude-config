"""Tests for evals/replay_bench/comment_metrics. Sources are synthetic. Diff and
raw-listing inputs are real `git diff` output from a tmp-path repository, built
with the same flags the runner uses. No test launches `claude`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from replay_bench import comment_metrics
from replay_bench.comment_metrics import LineClass
from review_bench import local_git

CODE, COMMENT, DOCSTRING = LineClass.CODE, LineClass.COMMENT, LineClass.DOCSTRING
BLANK, PROSE, UNCLASSIFIED = LineClass.BLANK, LineClass.PROSE, LineClass.UNCLASSIFIED

_GIT_IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.com")


# --- helpers -----------------------------------------------------------------


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *_GIT_IDENTITY, "-c", "core.quotePath=false", *args],
        cwd=repo, capture_output=True, check=True, env=local_git.isolated_git_environment(),
    )
    return result.stdout


def _write(repo: Path, rel_path: str, content: str | bytes) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode() if isinstance(content, str) else content)


def _repo_with_base(tmp_path: Path, base_files: dict[str, str | bytes]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel_path, content in base_files.items():
        _write(repo, rel_path, content)
    _git(repo, "add", "-A", "--force")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD").decode().strip()


def _diff_and_listing(repo: Path, base: str) -> tuple[str, str]:
    _git(repo, "add", "-A")
    diff_text = _git(repo, "diff", "--cached", "-U0", "--no-renames", *local_git.DIFF_TEXT_ARGS, base).decode(errors="replace")
    raw_listing = _git(repo, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", base).decode()
    return diff_text, raw_listing


def _blobs_of_regular_files(repo: Path, raw_listing: str) -> dict[str, bytes]:
    return {
        entry.path: _git(repo, "cat-file", "blob", entry.new_blob)
        for entry in comment_metrics.parse_raw_listing(raw_listing)
        if entry.new_mode in comment_metrics.REGULAR_FILE_MODES
    }


def _measure_change(
    tmp_path: Path, *, before: dict[str, str | bytes] | None = None, after: dict[str, str | bytes],
) -> comment_metrics.CommentMetrics:
    repo, base = _repo_with_base(tmp_path, before or {})
    for rel_path, content in after.items():
        _write(repo, rel_path, content)
    diff_text, raw_listing = _diff_and_listing(repo, base)
    return comment_metrics.measure_diff(diff_text, raw_listing, _blobs_of_regular_files(repo, raw_listing))


def _classes_of(path: str, source: str | bytes, line_numbers: list[int] | None = None) -> list[LineClass]:
    data = source.encode() if isinstance(source, str) else source
    if line_numbers is None:
        line_numbers = list(range(1, len(data.removesuffix(b"\n").split(b"\n")) + 1))
    classification = comment_metrics.classify_file(path, data, line_numbers)
    return [classification.line_classes[number] for number in line_numbers]


def _added_lines_in(
    tmp_path: Path, after: dict[str, str | bytes], before: dict[str, str | bytes] | None = None,
) -> dict[str, tuple[int, ...]]:
    repo, base = _repo_with_base(tmp_path, before or {})
    for rel_path, content in after.items():
        _write(repo, rel_path, content)
    diff_text, _raw_listing = _diff_and_listing(repo, base)
    return comment_metrics.parse_added_lines(diff_text)


# --- extension table ---------------------------------------------------------

_HASH_EXTENSIONS = ["sh", "bash", "zsh", "rb", "pl", "r", "yml", "yaml", "toml", "tf", "ps1"]
_SLASH_EXTENSIONS = [
    "js", "jsx", "mjs", "cjs", "ts", "tsx", "go", "rs", "java", "kt", "kts", "swift",
    "c", "h", "cc", "cpp", "hpp", "cs", "scala", "dart", "php", "scss",
]
_DASH_EXTENSIONS = ["sql", "lua", "hs"]


class TestExtensionTable:
    def test_table_names_exactly_the_extensions_and_filenames_the_tests_cover(self):
        covered = {*_HASH_EXTENSIONS, *_SLASH_EXTENSIONS, "css", *_DASH_EXTENSIONS}
        assert set(comment_metrics.COMMENT_SYNTAX_BY_EXTENSION) == covered
        assert set(comment_metrics.COMMENT_SYNTAX_BY_FILENAME) == {"Dockerfile", "Makefile"}

    @pytest.mark.parametrize("extension", _HASH_EXTENSIONS)
    def test_hash_family_counts_a_hash_line_as_comment_and_a_slash_line_as_code(self, extension):
        # `tf` accepts `//` comments in the language; the table does not, which pins that gap.
        assert _classes_of(f"f.{extension}", "# a note\n// not a hash comment\n") == [COMMENT, CODE]

    @pytest.mark.parametrize("filename", ["Dockerfile", "Makefile"])
    def test_hash_family_applies_to_extensionless_build_files(self, filename):
        assert _classes_of(f"build/{filename}", "# a note\nRUN x\n") == [COMMENT, CODE]

    @pytest.mark.parametrize("extension", _SLASH_EXTENSIONS)
    def test_slash_family_counts_slash_and_block_lines_as_comment_and_a_hash_line_as_code(self, extension):
        # `php` also accepts `#` comments in the language; the table does not, which pins that gap.
        source = "// a note\n/* block\n   still block */\n# not a slash comment\n"
        assert _classes_of(f"f.{extension}", source) == [COMMENT, COMMENT, COMMENT, CODE]

    def test_css_counts_a_block_as_comment_and_a_double_slash_line_as_code(self):
        assert _classes_of("f.css", "/* a note */\n// not a css comment\n") == [COMMENT, CODE]

    @pytest.mark.parametrize("extension", _DASH_EXTENSIONS)
    def test_dash_family_counts_a_dash_line_as_comment_and_a_block_line_as_code(self, extension):
        # SQL also accepts `/* */`; the table does not, which pins that gap.
        assert _classes_of(f"f.{extension}", "-- a note\n/* block */\n") == [COMMENT, CODE]

    @pytest.mark.parametrize("extension", ["md", "markdown", "rst", "txt"])
    def test_prose_extensions_count_every_non_blank_line_as_prose(self, extension):
        assert _classes_of(f"notes.{extension}", "Some text.\n\n# heading\n") == [PROSE, BLANK, PROSE]

    def test_markdown_fenced_block_with_hash_lines_is_prose(self):
        assert _classes_of("notes.md", "```sh\n# install\nmake\n```\n") == [PROSE, PROSE, PROSE, PROSE]

    def test_an_unknown_extension_is_unclassified(self):
        assert _classes_of("data.xyz", "# looks like a comment\nvalue\n") == [UNCLASSIFIED, UNCLASSIFIED]

    def test_a_path_without_an_extension_that_is_not_a_known_filename_is_unclassified(self):
        assert _classes_of("LICENSE", "text\n") == [UNCLASSIFIED]

    def test_extension_match_ignores_case(self):
        assert _classes_of("Script.SH", "# a note\n") == [COMMENT]


# --- Python ------------------------------------------------------------------


class TestPythonClassification:
    def test_docstring_lines_of_a_multi_line_docstring_are_docstring(self):
        source = 'def f():\n    """Summary.\n\n    More detail.\n    """\n    return 1\n'
        assert _classes_of("m.py", source) == [CODE, DOCSTRING, BLANK, DOCSTRING, DOCSTRING, CODE]

    def test_a_one_line_docstring_on_its_own_line_is_docstring(self):
        assert _classes_of("m.py", 'def f():\n    """Summary."""\n    return 1\n') == [CODE, DOCSTRING, CODE]

    def test_a_one_line_docstring_on_the_def_line_is_code_because_it_shares_the_line_with_code(self):
        assert _classes_of("m.py", 'def f(): """Summary."""\n') == [CODE]

    def test_a_whole_line_comment_is_comment(self):
        assert _classes_of("m.py", "# a note\nx = 1\n") == [COMMENT, CODE]

    def test_a_code_line_with_a_trailing_comment_is_code_and_counts_one_trailing_comment(self):
        classification = comment_metrics.classify_file("m.py", b"x = 1  # why\n# whole\ny = 2\n", [1, 2, 3])
        assert list(classification.line_classes.values()) == [CODE, COMMENT, CODE]
        assert classification.python_trailing_comments == 1

    def test_a_hash_inside_a_string_is_code_and_no_trailing_comment(self):
        classification = comment_metrics.classify_file("m.py", b'x = "a # b"\n', [1])
        assert classification.line_classes == {1: CODE}
        assert classification.python_trailing_comments == 0

    def test_a_non_docstring_multi_line_string_with_hash_leading_lines_is_code(self):
        source = 'x = """\n# not a comment\n# nor this\n"""\n'
        assert _classes_of("m.py", source) == [CODE, CODE, CODE, CODE]

    def test_an_added_line_inside_an_existing_docstring_is_docstring_though_its_opener_is_not_added(self):
        source = 'def f():\n    """Summary.\n\n    Added detail line.\n    """\n'
        assert _classes_of("m.py", source, [4]) == [DOCSTRING]

    def test_a_file_that_fails_to_tokenize_falls_back_to_the_hash_prefix_rule(self):
        source = '# note\nx = """never closed\n# inside\n'
        classification = comment_metrics.classify_file("m.py", source.encode(), [1, 3])
        assert classification.line_classes == {1: COMMENT, 3: COMMENT}
        assert classification.python_ast_rejected is True

    def test_a_file_ast_rejects_but_tokenize_accepts_is_tallied_and_its_docstring_counts_as_code(self):
        source = 'def f():\n    """Summary."""\n    x = = 1\n'
        classification = comment_metrics.classify_file("m.py", source.encode(), [2])
        assert classification.line_classes == {2: CODE}
        assert classification.python_ast_rejected is True

    def test_a_file_ast_accepts_is_not_tallied_as_rejected(self):
        assert comment_metrics.classify_file("m.py", b"x = 1\n", [1]).python_ast_rejected is False

    def test_a_docstring_line_beginning_with_a_string_prefix_does_not_count_the_prefix_as_a_word(self):
        classification = comment_metrics.classify_file("m.py", b'def f():\n    r"""\n    two words\n    """\n', [2, 3, 4])
        assert classification.words == {2: 0, 3: 2, 4: 0}


# --- shell and C family ------------------------------------------------------


class TestShellClassification:
    def test_a_shebang_on_line_one_is_code(self):
        assert _classes_of("run.sh", "#!/bin/sh\n# note\n") == [CODE, COMMENT]

    def test_a_hash_line_inside_a_heredoc_is_comment_a_known_gap(self):
        source = "cat <<EOF\n# data, not a comment\nEOF\n"
        assert _classes_of("run.sh", source) == [CODE, COMMENT, CODE]

    def test_a_hash_at_line_start_inside_a_multi_line_quoted_string_is_comment_a_known_gap(self):
        source = 'x="first\n# still the string\nlast"\n'
        assert _classes_of("run.sh", source) == [CODE, COMMENT, CODE]

    def test_hash_expansions_mid_line_are_code(self):
        assert _classes_of("run.sh", 'echo "$#"\nname=${path#prefix}\n') == [CODE, CODE]


class TestCFamilyClassification:
    def test_double_and_triple_slash_lines_are_comment(self):
        assert _classes_of("m.rs", "// plain\n/// doc\nlet x = 1;\n") == [COMMENT, COMMENT, CODE]

    def test_a_block_comment_opened_at_line_start_is_comment_across_lines(self):
        source = "/**\n * Summary.\n * More.\n */\nint x;\n"
        assert _classes_of("m.c", source) == [COMMENT, COMMENT, COMMENT, COMMENT, CODE]

    def test_a_closing_line_followed_by_code_is_code(self):
        assert _classes_of("m.c", "/* open\n   still */ int x;\n") == [COMMENT, CODE]

    def test_a_block_opened_and_closed_on_one_line_is_comment(self):
        assert _classes_of("m.c", "/* note */\nint x;\n") == [COMMENT, CODE]

    def test_a_block_opener_mid_line_inside_a_string_is_code_and_opens_nothing(self):
        assert _classes_of("m.js", 'const s = "/* not a comment";\nconst t = 1;\n') == [CODE, CODE]

    def test_a_template_literal_line_starting_with_a_block_opener_opens_a_comment_a_known_gap(self):
        source = "const s = `\n/* inside the template\n`;\nconst t = 1;\n"
        assert _classes_of("m.js", source) == [CODE, COMMENT, COMMENT, COMMENT]


# --- decoding ----------------------------------------------------------------


class TestDecoding:
    def test_bytes_that_are_not_utf8_leave_added_lines_unclassified_and_set_the_undecodable_flag(self):
        classification = comment_metrics.classify_file("m.py", b"# caf\xe9\nx = 1\n", [1, 2])
        assert classification.line_classes == {1: UNCLASSIFIED, 2: UNCLASSIFIED}
        assert classification.undecodable is True

    def test_a_file_with_a_utf8_bom_classifies_as_it_would_without_one(self):
        with_bom = comment_metrics.classify_file("m.py", b"\xef\xbb\xbf# note\nx = 1\n", [1, 2])
        without_bom = comment_metrics.classify_file("m.py", b"# note\nx = 1\n", [1, 2])
        assert with_bom == without_bom
        assert with_bom.line_classes == {1: COMMENT, 2: CODE}
        assert with_bom.undecodable is False

    def test_a_chinese_comment_with_no_spaces_counts_as_one_word_a_known_gap(self):
        classification = comment_metrics.classify_file("m.py", "# 这是一个没有空格的注释\n".encode(), [1])
        assert classification.words == {1: 1}

    def test_an_added_line_number_past_the_end_of_the_text_raises(self):
        with pytest.raises(ValueError, match="m.py has 1 lines"):
            comment_metrics.classify_file("m.py", b"x = 1", [2])


# --- words -------------------------------------------------------------------


class TestWords:
    @pytest.mark.parametrize(
        ("text", "expected_words"),
        [
            pytest.param("# two words", 2, id="hash delimiter holds no letter"),
            pytest.param("#glued", 1, id="delimiter glued to a word"),
            pytest.param("/** */", 0, id="empty block comment"),
            pytest.param("// -- ***", 0, id="tokens with no letter or digit"),
            pytest.param("  # v2 of 3  ", 3, id="digits count as words"),
            pytest.param("", 0, id="empty text"),
        ],
    )
    def test_counts_whitespace_separated_tokens_that_hold_a_letter_or_digit(self, text, expected_words):
        assert comment_metrics.count_words(text) == expected_words

    def test_comment_and_docstring_line_words_come_from_the_classified_lines_only(self):
        source = 'x = 1  # trailing words ignored\n# one two three\n\ndef f():\n    """\n    docs here\n    """\n'
        classification = comment_metrics.classify_file("m.py", source.encode(), [1, 2, 3, 4, 5, 6, 7])
        assert classification.words == {2: 3, 5: 0, 6: 2, 7: 0}


# --- test-path rule ----------------------------------------------------------


class TestIsTestPath:
    @pytest.mark.parametrize(
        "path",
        [
            "test/a.py", "src/tests/a.py", "web/__tests__/a.js", "web/spec/a.rb",
            "test_a.py", "pkg/a_test.go", "pkg/a.test.ts", "pkg/a.spec.ts", "pkg/conftest.py",
        ],
    )
    def test_matches_each_test_segment_and_basename_pattern(self, path):
        assert comment_metrics.is_test_path(path) is True

    @pytest.mark.parametrize("path", ["pkg/contest.py", "latest/a.py", "pkg/attest.py", "pkg/testing.py", "src/a.py"])
    def test_rejects_lookalikes(self, path):
        assert comment_metrics.is_test_path(path) is False


# --- blocks ------------------------------------------------------------------


class TestBlocks:
    def test_consecutive_added_comment_lines_form_one_block(self):
        classification = comment_metrics.classify_file("m.py", b"# a b\n# c\nx = 1\n", [1, 2, 3])
        assert comment_metrics.group_blocks("m.py", classification) == [
            comment_metrics.CommentBlock("m.py", COMMENT, lines=2, words=3, is_test=False),
        ]

    def test_a_code_line_between_comment_lines_splits_two_blocks(self):
        classification = comment_metrics.classify_file("m.py", b"# a\nx = 1\n# b\n", [1, 2, 3])
        blocks = comment_metrics.group_blocks("m.py", classification)
        assert [(block.lines, block.words) for block in blocks] == [(1, 1), (1, 1)]

    def test_a_gap_in_added_line_numbers_splits_two_blocks_though_the_lines_are_adjacent_in_the_list(self):
        classification = comment_metrics.classify_file("m.py", b"# a\nx = 1\n# b\n", [1, 3])
        assert len(comment_metrics.group_blocks("m.py", classification)) == 2

    def test_a_comment_run_next_to_a_docstring_run_stays_two_blocks_of_different_kind(self):
        source = '# note\n"""\nDocs.\n"""\n'
        classification = comment_metrics.classify_file("m.py", source.encode(), [1, 2, 3, 4])
        blocks = comment_metrics.group_blocks("m.py", classification)
        assert [(block.kind, block.lines) for block in blocks] == [(COMMENT, 1), (DOCSTRING, 3)]

    def test_a_block_in_a_test_file_is_marked_test(self):
        classification = comment_metrics.classify_file("tests/t.py", b"# a\n", [1])
        assert comment_metrics.group_blocks("tests/t.py", classification)[0].is_test is True

    def test_lines_with_no_comment_or_docstring_make_no_block(self):
        classification = comment_metrics.classify_file("m.py", b"x = 1\n\ny = 2\n", [1, 2, 3])
        assert comment_metrics.group_blocks("m.py", classification) == []


# --- diff parsing from real git output ---------------------------------------


class TestParseAddedLines:
    def test_a_hunk_header_with_no_count_means_one_added_line(self, tmp_path):
        before = {"m.py": "a\nb\nc\n"}
        added = _added_lines_in(tmp_path, {"m.py": "a\nB\nc\n"}, before)
        assert added == {"m.py": (2,)}

    def test_a_new_file_adds_every_line(self, tmp_path):
        assert _added_lines_in(tmp_path, {"m.py": "a\nb\nc\n"}) == {"m.py": (1, 2, 3)}

    def test_a_zero_count_hunk_adds_no_lines(self, tmp_path):
        before = {"m.py": "a\nb\nc\n"}
        assert _added_lines_in(tmp_path, {"m.py": "a\nc\n"}, before) == {}

    def test_a_pure_deletion_and_a_later_addition_report_only_the_addition(self, tmp_path):
        before = {"m.py": "a\nb\nc\nd\n"}
        added = _added_lines_in(tmp_path, {"m.py": "a\nc\nd\nnew\n"}, before)
        assert added == {"m.py": (4,)}

    def test_a_deleted_file_adds_nothing(self, tmp_path):
        repo, base = _repo_with_base(tmp_path, {"m.py": "a\nb\n"})
        (repo / "m.py").unlink()
        diff_text, _raw_listing = _diff_and_listing(repo, base)
        assert "+++ /dev/null" in diff_text
        assert comment_metrics.parse_added_lines(diff_text) == {}

    def test_a_binary_file_adds_nothing(self, tmp_path):
        added = _added_lines_in(tmp_path, {"blob.bin": b"\x00\x01\x02binary\x00"})
        assert added == {}

    def test_a_missing_newline_marker_does_not_disturb_the_next_file(self, tmp_path):
        added = _added_lines_in(tmp_path, {"a.py": "x = 1", "b.py": "y = 2\n"})
        assert added == {"a.py": (1,), "b.py": (1,)}

    def test_a_missing_newline_marker_inside_an_open_hunk_does_not_swallow_the_later_lines_or_files(self, tmp_path):
        before = {"a.py": "x = 1\ny = 2"}
        after = {"a.py": "x = 1\ny = 2\nz = 3\nw = 4\n", "b.py": "p = 1\nq = 2\n"}

        added = _added_lines_in(tmp_path, after, before)

        assert added == {"a.py": (2, 3, 4), "b.py": (1, 2)}

    def test_a_path_with_spaces_is_returned_without_git_trailing_tab(self, tmp_path):
        added = _added_lines_in(tmp_path, {"dir with space/my file.py": "x = 1\n"})
        assert added == {"dir with space/my file.py": (1,)}

    def test_section_text_after_the_hunk_header_is_ignored(self, tmp_path):
        before = {"m.py": "def f():\n    a = 1\n    b = 2\n" + "pass\n" * 10 + "def g():\n    c = 3\n"}
        after = {"m.py": before["m.py"].replace("c = 3", "c = 3\n    d = 4")}
        repo, base = _repo_with_base(tmp_path, before)
        _write(repo, "m.py", after["m.py"])
        diff_text, _raw_listing = _diff_and_listing(repo, base)
        assert any(line.startswith("@@ ") and line.endswith("@@ def g():") for line in diff_text.split("\n"))
        assert comment_metrics.parse_added_lines(diff_text) == {"m.py": (16,)}

    def test_an_added_line_that_looks_like_a_file_header_is_not_read_as_one(self, tmp_path):
        added = _added_lines_in(tmp_path, {"m.txt": "++ b/other.txt\nplain\n"})
        assert added == {"m.txt": (1, 2)}

    def test_a_path_git_still_quotes_keeps_its_quoted_text_as_the_key(self, tmp_path):
        added = _added_lines_in(tmp_path, {"we\tird.py": "x = 1\n"})
        assert list(added) == ['"b/we\\tird.py"']
        assert all(comment_metrics.is_git_quoted_path(path) for path in added)


class TestParseRawListing:
    def test_reads_mode_blob_status_and_verbatim_path_of_each_entry(self, tmp_path):
        repo, base = _repo_with_base(tmp_path, {"keep.py": "x = 1\n"})
        _write(repo, "dir with space/new.py", "y = 2\n")
        (repo / "keep.py").unlink()
        _diff_text, raw_listing = _diff_and_listing(repo, base)
        entries = {entry.path: entry for entry in comment_metrics.parse_raw_listing(raw_listing)}
        assert entries["dir with space/new.py"].new_mode == "100644"
        assert entries["dir with space/new.py"].status == "A"
        assert entries["keep.py"].status == "D"
        assert entries["keep.py"].new_mode == "000000"

    def test_an_empty_listing_has_no_entries(self):
        assert comment_metrics.parse_raw_listing("") == []

    def test_a_malformed_entry_raises(self):
        with pytest.raises(ValueError, match="unrecognized raw listing entry"):
            comment_metrics.parse_raw_listing("not-metadata\0path\0")

    def test_the_old_mode_of_each_entry_is_read(self, tmp_path):
        repo, base = _repo_with_base(tmp_path, {"keep.py": "x = 1\n"})
        _write(repo, "new.py", "y = 2\n")
        _diff_text, raw_listing = _diff_and_listing(repo, base)
        (entry,) = comment_metrics.parse_raw_listing(raw_listing)
        assert (entry.old_mode, entry.new_mode) == ("000000", "100644")

    def test_a_sha_256_object_name_is_refused_as_an_unrecognized_entry(self):
        sha_256_entry = ":100644 100644 " + "a" * 64 + " " + "b" * 64 + " M\0path\0"
        with pytest.raises(ValueError, match="unrecognized raw listing entry"):
            comment_metrics.parse_raw_listing(sha_256_entry)

    def test_a_metadata_field_without_a_path_raises(self):
        with pytest.raises(ValueError, match="odd number"):
            comment_metrics.parse_raw_listing(":100644 100644 " + "a" * 40 + " " + "b" * 40 + " M\0")


# --- run-level aggregate -----------------------------------------------------


class TestMeasureDiff:
    def test_splits_line_counts_and_words_by_test_and_non_test_scope(self, tmp_path):
        metrics = _measure_change(tmp_path, after={
            "src/m.py": "# one two\nx = 1\n",
            "tests/test_m.py": "# three\ny = 2\nz = 3\n",
        })
        assert metrics.line_counts["non_test"][CODE] == 1
        assert metrics.line_counts["non_test"][COMMENT] == 1
        assert metrics.line_counts["test"][CODE] == 2
        assert metrics.line_counts["test"][COMMENT] == 1
        assert metrics.comment_words == {"test": 1, "non_test": 2}
        assert metrics.changed_paths == ["src/m.py", "tests/test_m.py"]

    def test_counts_docstring_comment_and_prose_lines_across_languages(self, tmp_path):
        metrics = _measure_change(tmp_path, after={
            "m.py": 'def f():\n    """Summary."""\n    return 1\n',
            "notes.md": "Some prose.\n",
            "data.xyz": "whatever\n",
        })
        non_test = metrics.line_counts["non_test"]
        assert (non_test[DOCSTRING], non_test[CODE], non_test[PROSE], non_test[UNCLASSIFIED]) == (1, 2, 1, 1)
        assert [block.kind for block in metrics.blocks] == [DOCSTRING]

    def test_an_added_line_inside_an_unchanged_docstring_opener_counts_as_docstring(self, tmp_path):
        before = {"m.py": 'def f():\n    """Summary.\n    """\n'}
        after = {"m.py": 'def f():\n    """Summary.\n    Added detail.\n    """\n'}
        metrics = _measure_change(tmp_path, before=before, after=after)
        assert metrics.line_counts["non_test"][DOCSTRING] == 1
        assert metrics.comment_words["non_test"] == 2

    def test_python_trailing_comment_and_ast_rejection_are_tallied(self, tmp_path):
        metrics = _measure_change(tmp_path, after={"ok.py": "x = 1  # why\n", "bad.py": "x = = 1\n"})
        assert metrics.python_trailing_comments == 1
        assert metrics.python_files_ast_rejected == 1

    def test_undecodable_file_is_tallied_and_its_lines_are_unclassified(self, tmp_path):
        metrics = _measure_change(tmp_path, after={"m.py": b"# caf\xe9\n"})
        assert metrics.undecodable_files == 1
        assert metrics.line_counts["non_test"][UNCLASSIFIED] == 1

    def test_a_path_git_quotes_counts_its_added_lines_as_unclassified(self, tmp_path):
        metrics = _measure_change(tmp_path, after={"we\tird.py": "# a\nx = 1\n"})
        assert metrics.line_counts["non_test"][UNCLASSIFIED] == 2
        assert metrics.line_counts["non_test"][CODE] == 0

    def test_symlink_and_gitlink_entries_are_counted_and_not_classified(self, tmp_path):
        repo, base = _repo_with_base(tmp_path, {})
        _write(repo, "real.py", "x = 1\n")
        os.symlink("real.py", repo / "link.py")
        nested = repo / "sub"
        nested.mkdir()
        _git(nested, "init", "-q")
        _git(nested, "commit", "-q", "--allow-empty", "-m", "nested")
        diff_text, raw_listing = _diff_and_listing(repo, base)

        metrics = comment_metrics.measure_diff(diff_text, raw_listing, _blobs_of_regular_files(repo, raw_listing))

        assert metrics.non_file_entries == 2
        assert metrics.line_counts["non_test"][CODE] == 1
        assert sorted(metrics.changed_paths) == ["link.py", "real.py", "sub"]

    def test_raises_when_a_diff_path_is_missing_from_the_raw_listing(self):
        diff_text = "diff --git a/m.py b/m.py\n--- /dev/null\n+++ b/m.py\n@@ -0,0 +1 @@\n+x = 1\n"
        with pytest.raises(ValueError, match="raw listing does not name"):
            comment_metrics.measure_diff(diff_text, "", {})

    def test_raises_when_a_regular_file_has_no_supplied_bytes(self, tmp_path):
        repo, base = _repo_with_base(tmp_path, {})
        _write(repo, "m.py", "x = 1\n")
        diff_text, raw_listing = _diff_and_listing(repo, base)
        with pytest.raises(ValueError, match="no post-change bytes"):
            comment_metrics.measure_diff(diff_text, raw_listing, {})

    def test_an_empty_change_measures_to_all_zero(self):
        metrics = comment_metrics.measure_diff("", "", {})
        assert metrics == comment_metrics.CommentMetrics()
        assert sum(sum(counts.values()) for counts in metrics.line_counts.values()) == 0

    def test_to_dict_and_from_dict_round_trip_through_json_types(self, tmp_path):
        metrics = _measure_change(tmp_path, after={
            "m.py": '"""Docs here."""\n# note\nx = 1  # t\n',
            "tests/test_m.py": "# c\n",
            "bad.py": "x = = 1\n",
        })
        restored = comment_metrics.CommentMetrics.from_dict(json.loads(json.dumps(metrics.to_dict())))
        assert restored == metrics
        assert restored.python_files_ast_rejected == 1
