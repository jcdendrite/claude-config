"""Classifies the lines a diff adds as code, comment, docstring, blank, prose or
unclassified, and groups comment and docstring lines into blocks.

Classification is whole-line and lexical, by file extension. It reads each
file's post-change bytes, which the caller supplies, so a line added inside an
existing Python docstring still classifies as docstring.
"""
from __future__ import annotations

import codecs
import io
import re
import tokenize
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from review_bench import mine_szz


class LineClass(StrEnum):
    BLANK = "blank"
    DOCSTRING = "docstring"
    COMMENT = "comment"
    CODE = "code"
    PROSE = "prose"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class CommentSyntax:
    """`block` is an (opener, closer) pair that only counts when the opener starts the line."""

    line_prefixes: tuple[str, ...] = ()
    block: tuple[str, str] | None = None


HASH_SYNTAX = CommentSyntax(line_prefixes=("#",))
SLASH_SYNTAX = CommentSyntax(line_prefixes=("//",), block=("/*", "*/"))
BLOCK_ONLY_SYNTAX = CommentSyntax(block=("/*", "*/"))
DASH_SYNTAX = CommentSyntax(line_prefixes=("--",))

_EXTENSIONS_BY_SYNTAX: dict[CommentSyntax, tuple[str, ...]] = {
    HASH_SYNTAX: ("sh", "bash", "zsh", "rb", "pl", "r", "yml", "yaml", "toml", "tf", "ps1"),
    SLASH_SYNTAX: (
        "js", "jsx", "mjs", "cjs", "ts", "tsx", "go", "rs", "java", "kt", "kts", "swift",
        "c", "h", "cc", "cpp", "hpp", "cs", "scala", "dart", "php", "scss",
    ),
    BLOCK_ONLY_SYNTAX: ("css",),
    DASH_SYNTAX: ("sql", "lua", "hs"),
}

# Extend these tables to add a language. Python is separate: it classifies comments through `tokenize` and docstrings through
# `mine_szz._python_docstring_lines`.
COMMENT_SYNTAX_BY_EXTENSION: dict[str, CommentSyntax] = {
    extension: syntax for syntax, extensions in _EXTENSIONS_BY_SYNTAX.items() for extension in extensions
}
COMMENT_SYNTAX_BY_FILENAME: dict[str, CommentSyntax] = {"Dockerfile": HASH_SYNTAX, "Makefile": HASH_SYNTAX}
PYTHON_EXTENSIONS = frozenset({"py"})
PROSE_EXTENSIONS = frozenset({"md", "markdown", "rst", "txt"})

TEST_PATH_SEGMENTS = frozenset({"test", "tests", "__tests__", "spec"})
TEST_BASENAME_PATTERNS = ("test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py")

# Modes of a `git diff --raw` entry's side.
REGULAR_FILE_MODES = frozenset({"100644", "100755"})
NON_FILE_MODES = frozenset({"120000", "160000"})

_SCOPES = ("test", "non_test")
_BLOCK_CLASSES = (LineClass.COMMENT, LineClass.DOCSTRING)

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_RAW_ENTRY_RE = re.compile(r"^:(\d{6}) (\d{6}) ([0-9a-f]{40}) ([0-9a-f]{40}) ([A-Z])\d*$")
# Python string prefixes hold letters, so they are the one delimiter that would otherwise count as a word.
_PYTHON_QUOTES_OPENER_RE = re.compile(r"""^\s*[rRuUbBfF]{0,2}(?:"{3}|'{3})""")


def is_test_path(path: str) -> bool:
    path_object = PurePosixPath(path)
    return any(segment in TEST_PATH_SEGMENTS for segment in path_object.parts) or any(
        fnmatchcase(path_object.name, pattern) for pattern in TEST_BASENAME_PATTERNS
    )


def is_git_quoted_path(path: str) -> bool:
    """A name git still wraps in double quotes under `core.quotePath=false`: one with a
    control character, a double quote or a backslash."""
    return path.startswith('"')


def count_words(text: str) -> int:
    """Whitespace-separated tokens holding a letter or digit. A script written without
    spaces between words counts one word per whitespace-separated run."""
    return sum(1 for token in text.split() if any(character.isalnum() for character in token))


@dataclass(frozen=True)
class ChangedEntry:
    path: str
    old_mode: str
    new_mode: str
    new_blob: str
    status: str


def parse_raw_listing(raw_listing: str) -> list[ChangedEntry]:
    """Parses `git diff --raw -z --no-abbrev --no-renames` output. With `-z` each path is
    verbatim and NUL-terminated, so an unusual name is never quoted."""
    fields = raw_listing.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise ValueError(f"raw listing holds an odd number of NUL-separated fields: {len(fields)}")
    entries = []
    for metadata, path in zip(fields[0::2], fields[1::2], strict=True):
        match = _RAW_ENTRY_RE.match(metadata)
        if match is None:
            raise ValueError(f"unrecognized raw listing entry: {metadata!r}")
        old_mode, new_mode, _old_blob, new_blob, status = match.groups()
        entries.append(ChangedEntry(path=path, old_mode=old_mode, new_mode=new_mode, new_blob=new_blob, status=status))
    return entries


def parse_added_lines(diff_text: str) -> dict[str, tuple[int, ...]]:
    """Maps each path to the line numbers a `git diff -U0 --no-renames` adds to it, by the
    default `a/` and `b/` prefixes. A path git quotes keeps its quoted text as the key."""
    added: dict[str, list[int]] = {}
    current_path: str | None = None
    old_remaining = new_remaining = 0
    for line in diff_text.split("\n"):
        # Count a hunk's body lines instead of testing prefixes, since an added line may itself read `++ b/x`.
        if old_remaining or new_remaining:
            if line.startswith("-"):
                old_remaining -= 1
            elif line.startswith("+"):
                new_remaining -= 1
            elif not line.startswith("\\"):
                old_remaining -= 1
                new_remaining -= 1
            continue
        if line.startswith("diff --git "):
            current_path = None
        elif line.startswith("+++ "):
            current_path = _path_of_new_side(line[len("+++ "):])
        elif (hunk := _HUNK_HEADER_RE.match(line)) is not None:
            old_remaining = int(hunk.group(2)) if hunk.group(2) is not None else 1
            new_start = int(hunk.group(3))
            new_remaining = int(hunk.group(4)) if hunk.group(4) is not None else 1
            if current_path is not None and new_remaining:
                added.setdefault(current_path, []).extend(range(new_start, new_start + new_remaining))
    return {path: tuple(numbers) for path, numbers in added.items()}


def _path_of_new_side(header_value: str) -> str | None:
    # Git ends the name with a tab when the name holds a space.
    name = header_value.removesuffix("\t")
    if name == "/dev/null":
        return None
    if is_git_quoted_path(name):
        return name
    return name.removeprefix("b/")


@dataclass(frozen=True)
class FileClassification:
    """`line_classes` and `words` are keyed by added line number. `words` holds only comment and docstring lines."""

    line_classes: dict[int, LineClass]
    words: dict[int, int]
    python_trailing_comments: int = 0
    python_ast_rejected: bool = False
    undecodable: bool = False


def classify_file(path: str, data: bytes, added_line_numbers: Iterable[int]) -> FileClassification:
    """Classifies the given 1-based lines of a file's post-change `data`. Raises ValueError for
    a line number past the end of the file, which means `data` is not the diff's post-change text."""
    numbers = tuple(added_line_numbers)
    extension = PurePosixPath(path).suffix.removeprefix(".").lower()
    syntax = COMMENT_SYNTAX_BY_FILENAME.get(PurePosixPath(path).name) or COMMENT_SYNTAX_BY_EXTENSION.get(extension)
    is_python = extension in PYTHON_EXTENSIONS
    is_prose = extension in PROSE_EXTENSIONS
    if syntax is None and not is_python and not is_prose:
        return FileClassification({number: LineClass.UNCLASSIFIED for number in numbers}, {})
    text = _decode_utf8(data)
    if text is None:
        return FileClassification({number: LineClass.UNCLASSIFIED for number in numbers}, {}, undecodable=True)
    lines = text.split("\n")
    if numbers and max(numbers) > len(lines):
        raise ValueError(f"{path} has {len(lines)} lines but the diff adds line {max(numbers)}")
    if is_python:
        return _classify_python(text, lines, numbers)
    if is_prose:
        classes = {number: LineClass.BLANK if not lines[number - 1].strip() else LineClass.PROSE for number in numbers}
        return FileClassification(classes, {})
    assert syntax is not None
    every_line_class = _classify_with_syntax(lines, syntax)
    classes = {number: every_line_class[number] for number in numbers}
    return FileClassification(classes, _comment_words(lines, classes))


def _decode_utf8(data: bytes) -> str | None:
    try:
        return data.removeprefix(codecs.BOM_UTF8).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _classify_with_syntax(lines: list[str], syntax: CommentSyntax) -> dict[int, LineClass]:
    """Every line's class, because a block comment's state carries from earlier lines."""
    classes: dict[int, LineClass] = {}
    inside_block = False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            classes[number] = LineClass.BLANK
        elif inside_block:
            assert syntax.block is not None
            closer_at = stripped.find(syntax.block[1])
            inside_block = closer_at == -1
            classes[number] = _block_line_class(stripped, closer_at, syntax)
        elif number == 1 and "#" in syntax.line_prefixes and stripped.startswith("#!"):
            classes[number] = LineClass.CODE
        elif stripped.startswith(syntax.line_prefixes):
            classes[number] = LineClass.COMMENT
        elif syntax.block is not None and stripped.startswith(syntax.block[0]):
            closer_at = stripped.find(syntax.block[1], len(syntax.block[0]))
            inside_block = closer_at == -1
            classes[number] = _block_line_class(stripped, closer_at, syntax)
        else:
            classes[number] = LineClass.CODE
    return classes


def _block_line_class(stripped_line: str, closer_at: int, syntax: CommentSyntax) -> LineClass:
    """A line that closes the block and carries more text after the closer counts as code."""
    assert syntax.block is not None
    if closer_at == -1 or not stripped_line[closer_at + len(syntax.block[1]):].strip():
        return LineClass.COMMENT
    return LineClass.CODE


def _classify_python(text: str, lines: list[str], numbers: tuple[int, ...]) -> FileClassification:
    parse_stats = mine_szz._MineStats()
    docstring_lines = mine_szz._python_docstring_lines(text, stats=parse_stats)
    whole_line_comments, trailing_comments = _python_comment_rows(text, lines)
    classes: dict[int, LineClass] = {}
    trailing_count = 0
    for number in numbers:
        if not lines[number - 1].strip():
            classes[number] = LineClass.BLANK
        elif number in docstring_lines:
            classes[number] = LineClass.DOCSTRING
        elif number in whole_line_comments:
            classes[number] = LineClass.COMMENT
        else:
            classes[number] = LineClass.CODE
            trailing_count += number in trailing_comments
    return FileClassification(
        classes,
        _comment_words(lines, classes),
        python_trailing_comments=trailing_count,
        python_ast_rejected=parse_stats.python_docstring_parse_failures > 0,
    )


def _python_comment_rows(text: str, lines: list[str]) -> tuple[set[int], set[int]]:
    """Rows holding a whole-line comment, and rows where a comment follows code. A `#` inside
    a string is no comment token. When tokenizing fails, a line starting with `#` is the only comment."""
    whole_line: set[int] = set()
    trailing: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type != tokenize.COMMENT:
                continue
            row, column = token.start
            (trailing if lines[row - 1][:column].strip() else whole_line).add(row)
    except (tokenize.TokenError, SyntaxError, ValueError):
        return {number for number, line in enumerate(lines, start=1) if line.strip().startswith("#")}, set()
    return whole_line, trailing


def _comment_words(lines: list[str], classes: Mapping[int, LineClass]) -> dict[int, int]:
    words: dict[int, int] = {}
    for number, line_class in classes.items():
        if line_class not in _BLOCK_CLASSES:
            continue
        text = lines[number - 1]
        if line_class is LineClass.DOCSTRING:
            text = _PYTHON_QUOTES_OPENER_RE.sub("", text, count=1)
        words[number] = count_words(text)
    return words


@dataclass(frozen=True)
class CommentBlock:
    """A maximal run of consecutive added lines of one class, comment or docstring, in one file."""

    path: str
    kind: LineClass
    lines: int
    words: int
    is_test: bool

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind.value, "lines": self.lines, "words": self.words, "is_test": self.is_test}

    @classmethod
    def from_dict(cls, data: Mapping) -> CommentBlock:
        return cls(
            path=data["path"], kind=LineClass(data["kind"]), lines=data["lines"], words=data["words"],
            is_test=data["is_test"],
        )


def group_blocks(path: str, classification: FileClassification) -> list[CommentBlock]:
    is_test = is_test_path(path)
    blocks: list[CommentBlock] = []
    run_kind: LineClass | None = None
    run_numbers: list[int] = []

    def close_run() -> None:
        if run_kind is not None:
            words = sum(classification.words[number] for number in run_numbers)
            blocks.append(CommentBlock(path, run_kind, len(run_numbers), words, is_test))

    for number in sorted(classification.line_classes):
        line_class = classification.line_classes[number]
        continues_run = run_kind is line_class and run_numbers[-1] + 1 == number
        if continues_run:
            run_numbers.append(number)
            continue
        close_run()
        run_kind, run_numbers = (line_class, [number]) if line_class in _BLOCK_CLASSES else (None, [])
    close_run()
    return blocks


def _zeroed_line_counts() -> dict[str, dict[str, int]]:
    return {scope: {line_class.value: 0 for line_class in LineClass} for scope in _SCOPES}


def _zeroed_words() -> dict[str, int]:
    return {scope: 0 for scope in _SCOPES}


@dataclass
class CommentMetrics:
    """One run's tallies over the lines a change adds.

    `line_counts` and `comment_words` split by `test` and `non_test` scope. `comment_words`
    counts comment and docstring words together. Symlink and gitlink entries are counted in
    `non_file_entries` and never classified."""

    line_counts: dict[str, dict[str, int]] = field(default_factory=_zeroed_line_counts)
    comment_words: dict[str, int] = field(default_factory=_zeroed_words)
    blocks: list[CommentBlock] = field(default_factory=list)
    python_trailing_comments: int = 0
    python_files_ast_rejected: int = 0
    non_file_entries: int = 0
    undecodable_files: int = 0
    changed_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "line_counts": {scope: dict(counts) for scope, counts in self.line_counts.items()},
            "comment_words": dict(self.comment_words),
            "blocks": [block.to_dict() for block in self.blocks],
            "python_trailing_comments": self.python_trailing_comments,
            "python_files_ast_rejected": self.python_files_ast_rejected,
            "non_file_entries": self.non_file_entries,
            "undecodable_files": self.undecodable_files,
            "changed_paths": list(self.changed_paths),
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> CommentMetrics:
        return cls(
            line_counts={scope: dict(data["line_counts"][scope]) for scope in _SCOPES},
            comment_words={scope: data["comment_words"][scope] for scope in _SCOPES},
            blocks=[CommentBlock.from_dict(block) for block in data["blocks"]],
            python_trailing_comments=data["python_trailing_comments"],
            python_files_ast_rejected=data["python_files_ast_rejected"],
            non_file_entries=data["non_file_entries"],
            undecodable_files=data["undecodable_files"],
            changed_paths=list(data["changed_paths"]),
        )


def measure_diff(diff_text: str, raw_listing: str, blob_by_path: Mapping[str, bytes]) -> CommentMetrics:
    """Classifies what a change adds. `diff_text` is `git diff -U0 --no-renames` output,
    `raw_listing` is the same change as `git diff --raw -z --no-abbrev --no-renames`, and
    `blob_by_path` holds the post-change bytes of every regular-file entry that adds lines.
    Decode `diff_text` with `errors="replace"`, since git emits an added line's bytes as they are.

    Raises ValueError when the three inputs disagree about which paths changed."""
    entry_by_path = {entry.path: entry for entry in parse_raw_listing(raw_listing)}
    metrics = CommentMetrics(changed_paths=sorted(entry_by_path))
    metrics.non_file_entries = sum(entry.new_mode in NON_FILE_MODES for entry in entry_by_path.values())
    for path, numbers in parse_added_lines(diff_text).items():
        scope = "test" if is_test_path(path) else "non_test"
        if is_git_quoted_path(path):
            metrics.line_counts[scope][LineClass.UNCLASSIFIED.value] += len(numbers)
            continue
        entry = entry_by_path.get(path)
        if entry is None:
            raise ValueError(f"diff adds lines to {path!r}, which the raw listing does not name")
        if entry.new_mode in NON_FILE_MODES:
            continue
        if path not in blob_by_path:
            raise ValueError(f"no post-change bytes for {path!r}")
        classification = classify_file(path, blob_by_path[path], numbers)
        for line_class in classification.line_classes.values():
            metrics.line_counts[scope][line_class.value] += 1
        metrics.comment_words[scope] += sum(classification.words.values())
        metrics.blocks.extend(group_blocks(path, classification))
        metrics.python_trailing_comments += classification.python_trailing_comments
        metrics.python_files_ast_rejected += classification.python_ast_rejected
        metrics.undecodable_files += classification.undecodable
    return metrics
