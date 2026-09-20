#!/usr/bin/env python3
"""check-ledger-citations.py -- mechanically check that every ledger-style
citation in a plan file resolves to a label the plan actually writes down.

Usage: check-ledger-citations.py [--quiet] <path-to-plan-file>

A citation is a token at one of three sites: an `anchors:` value, the word
`row`/`rows` immediately before a number, or a bare label alone in square
brackets. A definition is a label a ledger row writes at the start of a line
inside a level-2 section that carries an `anchors:` line. A citation with no
matching definition is an orphan.

A letter-label definition starts with an uppercase letter (`G1`, `M7`), so a
lowercase token such as `x86` or `v2` never defines one. A bare number after
`and` continues an `anchors:` value as a row, so `anchors: G4 and 2 others`
cites `row2`.

An `anchors:` clause outside a fenced block is a citation; a fenced block is
how a plan quotes one as an example. A fence closes on a line of the opener's
character that is at least as long as the opener's run (backticks or `~~~`).
Inline code spans are blanked only for the `row <N>` and bracketed-label
sites, because a plan's own `anchors:` clauses are commonly wrapped in an
inline span.

Prints one FAIL line per orphan plus the labels the plan defined, and (always,
unless --quiet suppressed a pass) the fixed list of things a clean result does
not cover. A leading UTF-8 byte-order mark is ignored. The script splits lines
on LF only, so a plan with lone-CR line endings gets wrong line numbers and a
spurious row, where the CI backstop reads it with universal newlines.

Exit codes:
  0  no orphan (including a plan with no `anchors:` line)
  1  at least one orphan and nothing else
  2  any other failure, with a one-line message on stderr: wrong argument
     count, unknown flag, unreadable or missing file, non-UTF-8 content, or an
     unexpected exception
"""
from __future__ import annotations

import re
import sys
from bisect import bisect_left
from pathlib import Path

SCRIPT_NAME = "check-ledger-citations.py"

# Resolve without a written definition: the ledger grammar defines them once,
# outside any row.
ALWAYS_RESOLVING_LABELS = frozenset({"root", "givens"})

# Not a truth claim: each item is something a zero exit says nothing about.
RESIDUAL_CHECKLIST_ITEMS = (
    "whether a defined row's content is true (a fabricated row carrying a real label passes)",
    "whether a row's tag matches its actual provenance",
    "citations to sources outside this file (file:line, doc sections, ticket IDs)",
    "any citation inside a fenced code block, which is treated as quoted rather than asserted",
    "a plan with no `anchors:` clause outside a fenced block, which is not scanned at all: its "
    "`row N` and bracketed-label citations are never read, and it exits 0",
    "labels in an `anchors:` value after a separator other than a comma, semicolon, or the word `and`",
    "`anchors` keys not spelled `anchors:` directly before the value (`**anchors:** G9`, `anchors : G9`)",
    "labels in an `anchors:` value that wraps onto the next line",
    "labels in an `anchors:` value that follows a non-label word (`anchors: none, G9`)",
    "labels in an `anchors:` value that are punctuation-wrapped (`` `G9` ``, `(G9)`)",
    "labels in an `anchors:` value written as a spaced letter label (`G 9`)",
    "fences this script pairs differently from Markdown (indented four or more spaces, "
    "prefixed by a list marker, ended by an outdent, or inside an HTML block), which can hide "
    "visible text between two fences",
    "an escaped backtick, which this script reads as a code-span delimiter that can pair with "
    "a later span and hide the text between them",
    "labels whose letter prefix is four or more letters long, which no site recognizes",
    "an inline code span hard-wrapped across two lines, which Markdown reads as one span but "
    "this script scans as two unquoted lines",
    "whether the plan's rows carry written labels at all -- a ledger numbering its rows by "
    "list position defines none, and the `Defined labels:` line is the only signal of it",
    "a definition inside an HTML comment, which counts as defined although Markdown does not render it",
    "non-ASCII whitespace (`NBSP`) or a homoglyph inside a citation, or a second byte-order mark or "
    "a zero-width character before a fence opener, which this script does not "
    "read: the citation is not checked, and the unrecognized fence can hide a later orphan",
)

_ROW_LABEL = r"rows?(?:[ \t]*\d+[a-z]?|[ \t]+[A-Za-z]{1,3}\d+[a-z]?)"
_LETTER_LABEL = r"[A-Za-z]{1,3}\d+[a-z]?"
_NUMBER_LABEL = r"\d+[a-z]?"
# A label ends at a non-word character, so a longer identifier never yields a
# shorter label as a prefix.
_LABEL_END = r"(?![A-Za-z0-9_])"

_ANCHORS_KEY_RE = re.compile(r"\banchors:[ \t]*", re.IGNORECASE)
_ANCHOR_TARGET_RE = re.compile(
    rf"(?:{_ROW_LABEL}|{_LETTER_LABEL}|{_NUMBER_LABEL}|root|givens){_LABEL_END}",
    re.IGNORECASE,
)
_ANCHOR_SEPARATOR_RE = re.compile(r"[ \t]*(?:[,;][ \t]*and\b|[,;]|and\b)[ \t]*", re.IGNORECASE)
_ROW_WORD_CITATION_RE = re.compile(rf"\b{_ROW_LABEL}{_LABEL_END}", re.IGNORECASE)
_BRACKETED_CITATION_RE = re.compile(r"\[[ \t]*([A-Za-z]{1,3}[ \t]?\d+[a-z]?)[ \t]*\]", re.IGNORECASE)

_BACKTICK_RUN_RE = re.compile(r"`+")
_FENCE_OPEN_RE = re.compile(r"[ \t]*(?P<run>`{3,}|~{3,})(?P<info>[^\n]*)")

_H2_HEADING_RE = re.compile(r"##[ \t]")
# Every prefix piece starts with its own non-space character and consumes the
# whitespace after it once.
# So no two whitespace quantifiers can trade spaces, and matching stays linear
# in the line length.
_DEFINITION_LINE_RE = re.compile(
    r"[ \t]*(?:#{1,6}[ \t]+)?(?:>[ \t]*)*(?:[-*+][ \t]+)?(?:\|[ \t]*)?(?:(?:\*\*|__)[ \t]*)?"
    # `(?-i:[A-Z])` requires an uppercase first letter, so `x86` or `v2` never defines a label.
    r"(?P<label>row[ \t]*\d+[a-z]?|(?-i:[A-Z])[a-z]{0,2}\d+[a-z]?)"
    r"(?:\*\*|__|`|[ \t]*(?:[|:—(\[.-]|\r?$))",
    re.IGNORECASE,
)


def _blank(line: str) -> str:
    return " " * len(line)


def _fence_opener(line: str) -> tuple[str, int] | None:
    """(fence character, run length) when the line opens a fenced block. A
    backtick fence whose info string holds a backtick is an inline span."""
    match = _FENCE_OPEN_RE.match(line)
    if match is None:
        return None
    run = match.group("run")
    if run[0] == "`" and "`" in match.group("info"):
        return None
    return run[0], len(run)


def _closes_fence(line: str, fence_char: str, fence_length: int) -> bool:
    body = line.strip(" \t\r")
    return len(body) >= fence_length and body == fence_char * len(body)


def strip_fenced_blocks(text: str) -> str:
    """Blank every fenced code block, keeping each line's length and the
    file's line count so a later offset maps to the file's real line number.
    An unterminated fence is left unstripped, so what follows it is still
    scanned rather than silently hidden."""
    lines = text.split("\n")
    open_index: int | None = None
    fence_char = ""
    fence_length = 0
    for i, line in enumerate(lines):
        if open_index is None:
            opener = _fence_opener(line)
            if opener is not None:
                open_index = i
                fence_char, fence_length = opener
        elif _closes_fence(line, fence_char, fence_length):
            for j in range(open_index, i + 1):
                lines[j] = _blank(lines[j])
            open_index = None
    return "\n".join(lines)


def _strip_inline_spans_in_line(line: str) -> str:
    """A run of N backticks closes on the next run of exactly N backticks, so
    a span that quotes a backticked token (`` `x` ``) is blanked as one unit.
    An opener with no closer is left as text. One pass over the runs keeps this
    linear in the line length."""
    runs = [(match.start(), match.end()) for match in _BACKTICK_RUN_RE.finditer(line)]
    next_same_length: list[int | None] = [None] * len(runs)
    latest_index_by_length: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        length = runs[index][1] - runs[index][0]
        next_same_length[index] = latest_index_by_length.get(length)
        latest_index_by_length[length] = index
    characters = list(line)
    index = 0
    while index < len(runs):
        closer_index = next_same_length[index]
        if closer_index is None:
            index += 1
            continue
        span_start, span_end = runs[index][0], runs[closer_index][1]
        characters[span_start:span_end] = " " * (span_end - span_start)
        index = closer_index + 1
    return "".join(characters)


def strip_inline_spans(text: str) -> str:
    """Blank every inline code span, keeping each line's length and the file's
    line count. A span never crosses a line."""
    return "\n".join(
        _strip_inline_spans_in_line(line) if "`" in line else line for line in text.split("\n")
    )


def normalize_label(token: str) -> str:
    """Reduce a written label to its comparison form: lowercase, no spaces, no
    sentence-final period, a bare number read as a row, and a leading `row`
    word dropped before a letter-label (`row G2` is the `G2` given)."""
    text = token.strip().lower().rstrip(".")
    row_number = re.fullmatch(r"rows?\s*(\d+[a-z]?)", text)
    if row_number:
        return "row" + row_number.group(1)
    row_letter_label = re.fullmatch(r"rows?\s+([a-z]{1,3}\d+[a-z]?)", text)
    if row_letter_label:
        return row_letter_label.group(1)
    if re.fullmatch(r"\d+[a-z]?", text):
        return "row" + text
    return re.sub(r"\s+", "", text)


def collect_defined_labels(text: str) -> set[str]:
    """Labels a ledger row writes at the start of a line. Only lines inside a
    level-2 section that carries an `anchors:` line count (plus the text above
    the first level-2 heading, when it carries one), so a short code defined
    elsewhere in the file for an unrelated purpose is not a valid target."""
    lines = strip_fenced_blocks(text).split("\n")
    heading_indexes = [i for i, line in enumerate(lines) if _H2_HEADING_RE.match(line)]
    section_bounds = [0, *heading_indexes, len(lines)]
    defined: set[str] = set()
    for start, end in zip(section_bounds, section_bounds[1:], strict=False):
        section = lines[start:end]
        if not any(_ANCHORS_KEY_RE.search(line) for line in section):
            continue
        for line in section:
            definition = _DEFINITION_LINE_RE.match(line)
            if definition:
                defined.add(normalize_label(definition.group("label")))
    return defined


def _anchor_targets(fence_stripped: str) -> list[tuple[str, int]]:
    """(raw target, offset) for every label-shaped token in an `anchors:`
    value. Targets continue across `,`, `;`, and the word `and`. The value
    ends at the first token that is not label-shaped, which covers an em-dash,
    backtick, `]`, newline, sentence-final period, and a non-label word such
    as `value`."""
    targets: list[tuple[str, int]] = []
    for key in _ANCHORS_KEY_RE.finditer(fence_stripped):
        position = key.end()
        while True:
            target = _ANCHOR_TARGET_RE.match(fence_stripped, position)
            if target is None:
                break
            targets.append((target.group(), target.start()))
            separator = _ANCHOR_SEPARATOR_RE.match(fence_stripped, target.end())
            if separator is None:
                break
            position = separator.end()
    return targets


def find_citations(text: str) -> list[tuple[str, int]]:
    """Unique (normalized token, line) pairs at the three citation sites,
    ordered by line. A plan with no `anchors:` line outside a fenced block has
    no ledger and cites nothing. `root` and `givens` are never returned."""
    fence_stripped = strip_fenced_blocks(text)
    if not _ANCHORS_KEY_RE.search(fence_stripped):
        return []
    span_stripped = strip_inline_spans(fence_stripped)
    newline_offsets = [i for i, char in enumerate(text) if char == "\n"]

    def line_of(offset: int) -> int:
        return bisect_left(newline_offsets, offset) + 1

    found: set[tuple[str, int]] = set()
    for raw_target, offset in _anchor_targets(fence_stripped):
        found.add((normalize_label(raw_target), line_of(offset)))
    for match in _ROW_WORD_CITATION_RE.finditer(span_stripped):
        found.add((normalize_label(match.group()), line_of(match.start())))
    for match in _BRACKETED_CITATION_RE.finditer(span_stripped):
        found.add((normalize_label(match.group(1)), line_of(match.start())))
    return sorted(
        (pair for pair in found if pair[0] not in ALWAYS_RESOLVING_LABELS),
        key=lambda pair: (pair[1], pair[0]),
    )


def _unresolved(citations: list[tuple[str, int]], defined: set[str]) -> list[tuple[str, int]]:
    return [pair for pair in citations if pair[0] not in defined]


def find_orphan_citations(text: str) -> list[tuple[str, int]]:
    """The (normalized token, line) pairs from find_citations that no defined
    label resolves."""
    return _unresolved(find_citations(text), collect_defined_labels(text))


def _printable_path(path: Path | str) -> str:
    """Render the plan path (or a flag argument) inertly for output: ASCII only, control characters
    and lone surrogates shown as escapes. The path is contributor-controlled.
    Mirrors select-tests.py's printable_path, which a hyphen-named script
    cannot be imported for."""
    return ascii(str(path))[1:-1]


def main(argv: list[str]) -> int:
    args = argv[1:]
    quiet = False
    if args and args[0] == "--quiet":
        quiet = True
        args = args[1:]
    if len(args) == 1 and args[0].startswith("-"):
        print(f"{SCRIPT_NAME}: unknown flag: {_printable_path(args[0])}", file=sys.stderr)
        return 2
    if len(args) != 1:
        print(f"usage: {SCRIPT_NAME} [--quiet] <path-to-plan-file>", file=sys.stderr)
        return 2
    plan_path = Path(args[0])

    try:
        # A symlink to a device node (git tracks symlinks) could hang memory reads
        # with a MemoryError that the OSError handler below won't catch, so
        # non-regular targets are rejected here.
        # A directory is deliberately left unrejected, for read_bytes() to raise
        # its own IsADirectoryError.
        if not plan_path.is_file() and not plan_path.is_dir():
            print(f"{SCRIPT_NAME}: no such file: {_printable_path(plan_path)}", file=sys.stderr)
            return 2
        # Plan files are KB-sized, so the read is unbounded by design.
        raw = plan_path.read_bytes()
    except FileNotFoundError:
        print(f"{SCRIPT_NAME}: no such file: {_printable_path(plan_path)}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{SCRIPT_NAME}: cannot read {_printable_path(plan_path)}: {exc}", file=sys.stderr)
        return 2

    try:
        plan_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        print(f"{SCRIPT_NAME}: {_printable_path(plan_path)} is not valid UTF-8: {exc}", file=sys.stderr)
        return 2

    # Exit 1 is reserved for "an orphan was found", so a crash here must not
    # surface as a traceback with the interpreter's default exit code of 1.
    try:
        return _check_plan(plan_path, plan_text, quiet)
    except Exception as exc:
        print(
            f"{SCRIPT_NAME}: unexpected {type(exc).__name__} while checking {_printable_path(plan_path)}: {exc}",
            file=sys.stderr,
        )
        return 2


def _check_plan(plan_path: Path, plan_text: str, quiet: bool) -> int:
    written_labels = collect_defined_labels(plan_text)
    citations = find_citations(plan_text)
    orphans = _unresolved(citations, written_labels)

    if not orphans and quiet:
        return 0

    report: list[str] = []
    if orphans:
        for token, line in orphans:
            report.append(f"FAIL: {_printable_path(plan_path)}:{line}: citation '{token}' resolves to no defined ledger label")
        report.append(f"Defined labels: {', '.join(sorted(written_labels | ALWAYS_RESOLVING_LABELS))}")
    else:
        report.append(
            "PASS: every ledger citation resolves "
            f"({len(written_labels)} labels defined, {len(citations)} citations checked)"
        )
    report.append("")
    report.append("Not checked by this script -- a clean result does not cover:")
    report.extend(f"  - {item}" for item in RESIDUAL_CHECKLIST_ITEMS)

    # One write, so an output-encoding failure raises before any partial report is emitted.
    print("\n".join(report))
    return 1 if orphans else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
