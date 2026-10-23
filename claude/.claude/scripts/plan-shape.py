#!/usr/bin/env python3
"""Checks a /plan-it plan file's heading structure for Step 5, and scaffolds a new plan's four headings.

Usage: plan-shape.py prepare|check <absolute plan path>

`prepare` runs before a run's first dispatch. It appends the four-heading scaffold when the file
holds only its title and Context, and writes nothing to any other file. It opens every file for
reading and appending, so it needs write access to the plan file. `check` runs after a dispatch
and only reads. Both print `KEY: value` lines, and the verdict is positive only when no `REASON`
line prints.

Plan path: absolute, with every segment after the leading `/` non-empty and neither `.` nor `..`.
The last segment fully matches `[A-Za-z0-9][A-Za-z0-9._-]*\\.md`. The parent, after symlink
resolution, is a directory named `plans` whose own parent is named `.claude`. The `.claude` test
is what keeps an arbitrary `plans` directory out, so dropping it is not the repair for a refused
path. The rule tests directory names only, so it admits any `.claude/plans/` directory, not just
the current repository's. Under a config directory with another name, the plan-mode harness path is refused.

File: opened once, with no symlink followed and no blocking on a FIFO, and it must be a regular
file. `prepare` opens it for reading and appending, `check` for reading. Its bytes must decode as
strict UTF-8, and a carriage return anywhere stops evaluation. The script never rewrites line
endings.

Lines: the text split on "\\n" only, numbered from 1. A final "\\n" ends the last line instead of
starting an empty one. A blank line holds only U+0020 and U+0009.

Heading line: a line outside fenced code whose first three characters are `## `. Heading identity
is exact line equality, so `## Approach ` with a trailing space is a heading line but not
`## Approach`. The `## Approach` heading line is the first heading line reading exactly
`## Approach`. This rule is narrower than a CommonMark ATX heading: a leading-space `## X`, `##`
followed by a tab and `X`, and a bare `##` render as headings but are not heading lines here.

Fenced code: an opener is up to three spaces, then three or more backticks or three or more
tildes. For a backtick run the rest of the line holds no backtick, and a tab in the indentation
disqualifies the line. While a fence is open, a closer is up to three spaces, then the opener's
character at least as many times as the opener's run, then only spaces or tabs. Every line from
opener to closer is fenced, and an opener-shaped line inside is content. Not modeled: list items,
block quotes, HTML blocks, indented code and setext headings.

File-level conditions, checked first in this order. Each of the first five ends evaluation:
- plan-path: the path rule above.
- unreadable: a missing file, a symlink at the last component, a non-regular file, a failed open
  or a failed read.
- not-utf8: the bytes do not decode as strict UTF-8.
- carriage-return: a CR appears anywhere, cited by the first CR's line.
- unclosed-fence: a fence is still open at the end of the file, cited by the opener's line. This
  departs from CommonMark, which runs it to the end of the document, because a forgotten closer
  would hide every later heading.
- hash-range: Step 5's Context hash ends at the first line from line 2 on that reads exactly
  `## Approach`, fenced or not, or at the end of the file. The condition passes when that end is
  the `## Approach` heading line, or is the end of the file in a file with no `## Approach`
  heading line. Otherwise it fails and cites every line reading exactly `## Approach`. It is
  reported with the mode's own conditions instead of ending evaluation.

`prepare` conditions:
- With no `## Approach` heading line, the file's heading lines must be exactly one line,
  `## Context`. Any other heading line, or none, gives not-title-and-context, cited by the heading
  lines. With neither that reason nor hash-range, the scaffold is appended: `\\n` when the file does not end in `\\n`, then `\\n`
  when it does not end in `\\n\\n`, then the four headings with the placeholder under
  `## Approach`. One `os.write` call sends it, and no existing byte changes.
- With a `## Approach` heading line, every heading line from the first one to the end of the file
  must be one of the four. Any other gives foreign-heading, cited by its lines. Order, duplicates
  and missing headings pass, and heading lines above the `## Approach` heading line are not
  examined.
- unwritable: the append raised or wrote fewer bytes than the scaffold, so the file may now hold
  part of it.

`check` conditions, one `REASON` line per failure, after the file-level conditions:
- placeholder: the first non-blank line after the `## Approach` heading line, before the next
  heading line, reads exactly `(pending: plan-architect)`. Only that line is tested, byte for
  byte, so trailing text or a placeholder line further down the section does not match.
- headings: the heading lines from the `## Approach` heading line to the end of the file are not
  exactly the four, in order. A file with no `## Approach` heading line fails this, with no line
  cited.
- empty-section: a heading line in that region has no non-blank line before the next heading line
  or the end of the file. Each such heading gets its own line, cited by its line number.

Keys: PREPARE, CHECK, STAGE, REASON
Reason codes: usage, internal-error, plan-path, unreadable, not-utf8, carriage-return, unclosed-fence,
  hash-range, not-title-and-context, foreign-heading, unwritable, placeholder, headings, empty-section

Output: one write after the decision and after any append.
- `PREPARE: ready|stop` or `CHECK: pass|fail`, then `STAGE: first-authoring|revision` when printed,
  then `REASON: <code>: <sentence>` lines, file-level first.
- STAGE prints only when a dispatch may follow and the file has a `## Approach` heading line: with
  `PREPARE: ready`, and with a `check` whose reason codes, if any, are all among placeholder,
  headings and empty-section. It is `first-authoring` when the placeholder condition holds and
  `revision` otherwise.
- A REASON sentence is fixed text whose only variable part is a list of line numbers. The one
  exception is internal-error, which carries the exception's type name.

Exit status:
- 0 when a verdict is reached, `stop` and `fail` included.
- 1 on an unexpected exception: the mode's negative verdict and an internal-error line. For
  `prepare` that line adds that the plan file may have changed.
- 2 on usage: a wrong argument count or an unknown mode, `-h` and `--help` included. It prints
  one `REASON: usage:` line and no verdict.

Nothing goes to stderr while stdout is writable. An unexpected exception intentionally leaves no
locator: stderr stays empty and stdout carries only the exception's type name.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from typing import NamedTuple

_MODE_PREPARE = "prepare"
_MODE_CHECK = "check"
_MODES = (_MODE_PREPARE, _MODE_CHECK)

_KEYS = ("PREPARE", "CHECK", "STAGE", "REASON")
_REASON_CODES = (
    "usage",
    "internal-error",
    "plan-path",
    "unreadable",
    "not-utf8",
    "carriage-return",
    "unclosed-fence",
    "hash-range",
    "not-title-and-context",
    "foreign-heading",
    "unwritable",
    "placeholder",
    "headings",
    "empty-section",
)
# A check failure with only these codes re-dispatches, because plan-architect can repair each from the file alone.
_REDISPATCH_CODES = frozenset({"placeholder", "headings", "empty-section"})

_STAGE_FIRST_AUTHORING = "first-authoring"
_STAGE_REVISION = "revision"

_PLAN_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*\.md"
_PLANS_DIRECTORY_NAME = "plans"
_READ_CHUNK_BYTES = 1 << 16
_CLAUDE_DIRECTORY_NAME = ".claude"

_HEADING_PREFIX = "## "
_CONTEXT_HEADING = "## Context"
_APPROACH_HEADING = "## Approach"
_FOUR_HEADINGS = (_APPROACH_HEADING, "## Critical files", "## Verification", "## Out of scope")
_PLACEHOLDER = "(pending: plan-architect)"
_SCAFFOLD = (
    _APPROACH_HEADING + "\n\n" + _PLACEHOLDER + "\n\n"
    + _FOUR_HEADINGS[1] + "\n\n" + _FOUR_HEADINGS[2] + "\n\n" + _FOUR_HEADINGS[3] + "\n"
)

_FENCE_OPENER_PATTERN = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_FENCE_CLOSER_PATTERN = re.compile(r" {0,3}(`+|~+)[ \t]*")
_BLANK_CHARACTERS = " \t"

_INTERNAL_ERROR_PREPARE_SUFFIX = "; the plan file may have changed"

# Every REASON sentence the script prints, with `{lines}` standing for a list of line numbers.
# internal-error carries an exception type name instead.
_USAGE_SENTENCE = "plan-shape.py prepare|check <absolute plan path>"
_SENTENCE_PLAN_PATH = (
    "the plan path is not an absolute path to a file directly under a .claude/plans/ directory,"
    " with a name that starts with an ASCII letter or digit, holds only ASCII letters, digits, dots,"
    " underscores and hyphens, and ends in lowercase .md, or it fails another part of the path rule"
    " that plan-shape.py's module docstring states."
)
_SENTENCE_UNREADABLE_CHECK = "the plan file cannot be opened for reading as a regular file, or cannot be read."
_SENTENCE_UNREADABLE_PREPARE = (
    "the plan file cannot be opened for reading and appending as a regular file, or cannot be read."
)
_SENTENCE_NOT_UTF8 = "the plan file's bytes do not decode as strict UTF text."
_SENTENCE_CARRIAGE_RETURN = "the plan file holds a carriage return, first at line {lines}."
_SENTENCE_UNCLOSED_FENCE = "a code fence opened at line {lines} is never closed."
_SENTENCE_HASH_RANGE = (
    "the Context hash would not end at the `## Approach` heading line; lines reading exactly"
    " `## Approach`: {lines}."
)
_SENTENCE_NOT_TITLE_AND_CONTEXT = (
    "the file has no `## Approach` heading line and its heading lines are not exactly `## Context`;"
    " heading lines: {lines}."
)
_SENTENCE_FOREIGN_HEADING = (
    "the region from `## Approach` on holds heading lines besides the four; heading lines: {lines}."
)
_SENTENCE_UNWRITABLE = "the scaffold append failed, and the plan file may now hold part of the scaffold."
_SENTENCE_PLACEHOLDER = "the first non-blank line under `## Approach` is still the placeholder."
_SENTENCE_HEADINGS = (
    "the heading lines from `## Approach` on are not exactly the four, in order; heading lines: {lines}."
)
_SENTENCE_EMPTY_SECTION = (
    "the heading at line {lines} has no non-blank line before the next heading or the end of the file."
)

_EXIT_DECIDED = 0
_EXIT_INTERNAL_ERROR = 1
_EXIT_USAGE = 2


class Reason(NamedTuple):
    code: str
    sentence: str
    line_numbers: tuple[int, ...] = ()


class FileLevelStop(Exception):
    """A file-level condition that ends evaluation, carried to `_evaluate` as the only reason."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.code)
        self.reason = reason


class Outcome(NamedTuple):
    mode: str
    reasons: list[Reason]
    stage: str | None


class FenceScan(NamedTuple):
    fenced_line_numbers: frozenset[int]
    unclosed_opener_line: int | None


def _line(key: str, value: str) -> str:
    """The only builder of output lines, so an unknown key cannot print."""
    if key not in _KEYS:
        raise RuntimeError("not an output key")
    return key + ": " + value


def _emit(lines: list[str]) -> None:
    # One write of UTF-8 bytes, so an encoding error never leaves partial output on stdout.
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _render_reason(reason: Reason) -> str:
    numbers = ", ".join(str(number) for number in reason.line_numbers) or "none"
    return _line("REASON", reason.code + ": " + reason.sentence.replace("{lines}", numbers))


def _split_lines(text: str) -> list[str]:
    """Splits on "\\n" only, as `sed` does. `str.splitlines` also splits on U+2028 and similar."""
    if text == "":
        return []
    body = text.removesuffix("\n")
    return body.split("\n")


def _is_blank(line: str) -> bool:
    return line.strip(_BLANK_CHARACTERS) == ""


def _scan_fences(lines: list[str]) -> FenceScan:
    """Finds the fenced lines and the opener's line of any fence still open at the end."""
    fenced: set[int] = set()
    open_character: str | None = None
    open_length = 0
    opener_line = 0
    for number, line in enumerate(lines, start=1):
        if open_character is None:
            opener = _FENCE_OPENER_PATTERN.fullmatch(line)
            if opener is None:
                continue
            run, info_string = opener.groups()
            if run[0] == "`" and "`" in info_string:
                continue
            open_character, open_length, opener_line = run[0], len(run), number
            fenced.add(number)
            continue
        fenced.add(number)
        closer = _FENCE_CLOSER_PATTERN.fullmatch(line)
        if closer is not None and closer.group(1)[0] == open_character and len(closer.group(1)) >= open_length:
            open_character = None
    return FenceScan(frozenset(fenced), opener_line if open_character is not None else None)


def _heading_line_numbers(lines: list[str], fenced: frozenset[int]) -> list[int]:
    return [
        number
        for number, line in enumerate(lines, start=1)
        if line.startswith(_HEADING_PREFIX) and number not in fenced
    ]


def _first_non_blank_line_number(lines: list[str], after_line: int, before_line: int) -> int | None:
    """The first non-blank line strictly between the two line numbers, or None."""
    for number in range(after_line + 1, before_line):
        if not _is_blank(lines[number - 1]):
            return number
    return None


def _resolve_plan_file(plan_argument: str) -> str:
    """The resolved path of a `.md` file directly under a `.claude/plans/` directory, or a plan-path stop."""
    refusal = FileLevelStop(Reason("plan-path", _SENTENCE_PLAN_PATH))
    if not plan_argument.startswith("/"):
        raise refusal
    segments = plan_argument[1:].split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise refusal
    if re.fullmatch(_PLAN_NAME_PATTERN, segments[-1]) is None:
        raise refusal
    resolved_parent = os.path.realpath(os.path.dirname(plan_argument))
    plans_directory_ok = os.path.isdir(resolved_parent) and os.path.basename(resolved_parent) == _PLANS_DIRECTORY_NAME
    if not plans_directory_ok or os.path.basename(os.path.dirname(resolved_parent)) != _CLAUDE_DIRECTORY_NAME:
        raise refusal
    return os.path.join(resolved_parent, segments[-1])


def _unreadable_stop(mode: str) -> FileLevelStop:
    sentence = _SENTENCE_UNREADABLE_PREPARE if mode == _MODE_PREPARE else _SENTENCE_UNREADABLE_CHECK
    return FileLevelStop(Reason("unreadable", sentence))


def _open_regular_file(plan_file: str, mode: str) -> int:
    """Opens without following a symlink or blocking on a FIFO, and requires a regular file."""
    access = os.O_RDWR | os.O_APPEND if mode == _MODE_PREPARE else os.O_RDONLY
    unreadable = _unreadable_stop(mode)
    try:
        descriptor = os.open(plan_file, access | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise unreadable from None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise unreadable
    return descriptor


def _read_all(descriptor: int, mode: str) -> bytes:
    chunks = []
    try:
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if chunk == b"":
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError:
        raise _unreadable_stop(mode) from None


def _decode_plan_text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise FileLevelStop(Reason("not-utf8", _SENTENCE_NOT_UTF8)) from None
    carriage_return_index = text.find("\r")
    if carriage_return_index != -1:
        first_line = text.count("\n", 0, carriage_return_index) + 1
        raise FileLevelStop(Reason("carriage-return", _SENTENCE_CARRIAGE_RETURN, (first_line,)))
    return text


def _hash_range_reasons(lines: list[str], approach_line: int | None) -> list[Reason]:
    # The hash's range ends at the first match from line 2 on, because sed tests its end address first on line 2.
    approach_like_lines = [number for number, line in enumerate(lines, start=1) if line == _APPROACH_HEADING]
    hash_end_line = next((number for number in approach_like_lines if number >= 2), None)
    if hash_end_line == approach_line:
        return []
    return [Reason("hash-range", _SENTENCE_HASH_RANGE, tuple(approach_like_lines))]


def _placeholder_present(lines: list[str], region_headings: list[int]) -> bool:
    approach_line = region_headings[0]
    next_heading_line = region_headings[1] if len(region_headings) > 1 else len(lines) + 1
    first_line = _first_non_blank_line_number(lines, approach_line, next_heading_line)
    return first_line is not None and lines[first_line - 1] == _PLACEHOLDER


def _empty_section_reasons(lines: list[str], region_headings: list[int]) -> list[Reason]:
    reasons = []
    for index, heading_line in enumerate(region_headings):
        is_last = index + 1 == len(region_headings)
        end_line = len(lines) + 1 if is_last else region_headings[index + 1]
        if _first_non_blank_line_number(lines, heading_line, end_line) is None:
            reasons.append(Reason("empty-section", _SENTENCE_EMPTY_SECTION, (heading_line,)))
    return reasons


def _stage_for(region_headings: list[int], lines: list[str]) -> str:
    return _STAGE_FIRST_AUTHORING if _placeholder_present(lines, region_headings) else _STAGE_REVISION


def _check_outcome(lines: list[str], headings: list[int], approach_line: int | None) -> Outcome:
    reasons = _hash_range_reasons(lines, approach_line)
    region_headings = [] if approach_line is None else [number for number in headings if number >= approach_line]
    if region_headings and _placeholder_present(lines, region_headings):
        reasons.append(Reason("placeholder", _SENTENCE_PLACEHOLDER))
    if [lines[number - 1] for number in region_headings] != list(_FOUR_HEADINGS):
        reasons.append(Reason("headings", _SENTENCE_HEADINGS, tuple(region_headings)))
    reasons.extend(_empty_section_reasons(lines, region_headings))
    dispatch_may_follow = bool(region_headings) and all(reason.code in _REDISPATCH_CODES for reason in reasons)
    stage = _stage_for(region_headings, lines) if dispatch_may_follow else None
    return Outcome(_MODE_CHECK, reasons, stage)


def _scaffold_payload(raw: bytes) -> bytes:
    if raw.endswith(b"\n\n"):
        padding = ""
    elif raw.endswith(b"\n"):
        padding = "\n"
    else:
        padding = "\n\n"
    return (padding + _SCAFFOLD).encode("utf-8")


def _append_scaffold(descriptor: int, raw: bytes) -> list[Reason]:
    payload = _scaffold_payload(raw)
    try:
        written = os.write(descriptor, payload)
    except OSError:
        return [Reason("unwritable", _SENTENCE_UNWRITABLE)]
    if written != len(payload):
        return [Reason("unwritable", _SENTENCE_UNWRITABLE)]
    return []


def _prepare_outcome(
    lines: list[str], headings: list[int], approach_line: int | None, descriptor: int, raw: bytes
) -> Outcome:
    reasons = _hash_range_reasons(lines, approach_line)
    if approach_line is None:
        if [lines[number - 1] for number in headings] != [_CONTEXT_HEADING]:
            reasons.append(Reason("not-title-and-context", _SENTENCE_NOT_TITLE_AND_CONTEXT, tuple(headings)))
        if reasons:
            return Outcome(_MODE_PREPARE, reasons, None)
        reasons.extend(_append_scaffold(descriptor, raw))
        return Outcome(_MODE_PREPARE, reasons, None if reasons else _STAGE_FIRST_AUTHORING)
    region_headings = [number for number in headings if number >= approach_line]
    foreign_headings = [number for number in region_headings if lines[number - 1] not in _FOUR_HEADINGS]
    if foreign_headings:
        reasons.append(Reason("foreign-heading", _SENTENCE_FOREIGN_HEADING, tuple(foreign_headings)))
    return Outcome(_MODE_PREPARE, reasons, None if reasons else _stage_for(region_headings, lines))


def _evaluate(mode: str, plan_argument: str) -> Outcome:
    try:
        plan_file = _resolve_plan_file(plan_argument)
        descriptor = _open_regular_file(plan_file, mode)
        try:
            raw = _read_all(descriptor, mode)
            lines = _split_lines(_decode_plan_text(raw))
            fence_scan = _scan_fences(lines)
            if fence_scan.unclosed_opener_line is not None:
                unclosed = Reason("unclosed-fence", _SENTENCE_UNCLOSED_FENCE, (fence_scan.unclosed_opener_line,))
                raise FileLevelStop(unclosed)
            headings = _heading_line_numbers(lines, fence_scan.fenced_line_numbers)
            approach_line = next((number for number in headings if lines[number - 1] == _APPROACH_HEADING), None)
            if mode == _MODE_PREPARE:
                return _prepare_outcome(lines, headings, approach_line, descriptor, raw)
            return _check_outcome(lines, headings, approach_line)
        finally:
            os.close(descriptor)
    except FileLevelStop as stop:
        return Outcome(mode, [stop.reason], None)


def _verdict_line(mode: str, positive: bool) -> str:
    if mode == _MODE_PREPARE:
        return _line("PREPARE", "ready" if positive else "stop")
    return _line("CHECK", "pass" if positive else "fail")


def _render_outcome(outcome: Outcome) -> list[str]:
    lines = [_verdict_line(outcome.mode, not outcome.reasons)]
    if outcome.stage is not None:
        lines.append(_line("STAGE", outcome.stage))
    lines.extend(_render_reason(reason) for reason in outcome.reasons)
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in _MODES:
        _emit([_render_reason(Reason("usage", _USAGE_SENTENCE))])
        return _EXIT_USAGE
    mode, plan_argument = argv
    try:
        lines = _render_outcome(_evaluate(mode, plan_argument))
        status = _EXIT_DECIDED
    except Exception as error:
        # Only the type name is printed: an exception's text can carry the plan path.
        detail = type(error).__name__ + (_INTERNAL_ERROR_PREPARE_SUFFIX if mode == _MODE_PREPARE else "")
        lines = [
            _verdict_line(mode, False),
            _line("REASON", "internal-error: " + detail),
        ]
        status = _EXIT_INTERNAL_ERROR
    _emit(lines)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
