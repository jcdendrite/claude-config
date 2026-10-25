#!/usr/bin/env python3
"""Derives /ready-for-review's `<TICKET-ID>` and PR title, and checks the PR body file before the PR opens.

Usage: pr-create-preflight.py ticket
       pr-create-preflight.py create <body path>

`ticket` runs in step 1 and prints the branch's `<TICKET-ID>`. `create` runs in step 8 and prints
the PR title, or stops. Any other argument list, `-h` and `--help` included, is a usage error.
Both modes print one `KEY: value` line, or one `REASON` line on a stop.

Branch: read with `git symbolic-ref --quiet HEAD`. A nonzero exit or a ref outside `refs/heads/`
stops with not-on-branch in both modes.

TICKET-ID: the branch's text before its first `/`, or the whole name when it has no `/`. It is the
ID when it fully matches `[A-Za-z]+-[0-9]+` with no flags, so only ASCII letters and digits match.
Otherwise there is none, printed as `none`.

Slug: the branch's text after its last `/`, or the whole name when it has no `/`. Its words are its
`-`-separated pieces with empty pieces dropped, so repeated, leading and trailing hyphens add no space.

Title: empty when the slug has no words. Otherwise `<TICKET-ID>: ` followed by the slug's words joined
by single spaces, or the words alone when there is no TICKET-ID.

Over 70 characters: the title keeps the longest leading run of words that fits in 70 characters with its
prefix. When even the first word does not fit, the title is the untruncated title's first 70 characters
with trailing spaces removed. The sink rule admits only ASCII, so in every printed title a character
is a byte.

Sink rule: `create` holds the two values the session copies into the PR-creation command to one rule. A
value passes when it fully matches its pattern: `[A-Za-z0-9._: -]+` for the title as it would print,
after truncation, and `/[A-Za-z0-9._/-]*` for the body path as the script receives it. These
characters neither end nor expand a word inside the double-quoted `--title` argument or the bare
`--body-file` word. The title pattern's `+` refuses the empty title.

`create` conditions, checked in this order, with the first failure ending evaluation:
- not-on-branch: as above.
- not-plain-word: the title fails the sink rule. A slug with no words fails here.
- not-plain-word: the body path fails the sink rule, before any file access.
- body-unreadable: `os.stat` on the path finds a file that is not regular, or the `stat`, the open or
  the read raises OSError. `os.stat` follows a symlink. A FIFO is refused before any
  open, so nothing blocks.
- body-empty: the file holds no byte outside the six whitespace bytes space, tab, LF, VT, FF and CR.
  NUL is content, so a file of only NUL and whitespace bytes passes.

Keys: TICKET_ID, TITLE, REASON
Reason codes: usage, internal-error, not-on-branch, not-plain-word, body-unreadable, body-empty

Output: one write after the decision.
- `ticket` prints `TICKET_ID: <id>` or `TICKET_ID: none`.
- `create` prints `TITLE: <title>`.
- A stop prints one `REASON: <code>: <sentence>` line instead, never alongside a value line.
- Every REASON sentence is fixed text, so none echoes branch, path or body characters. The one
  exception is internal-error, which carries the exception's type name.

Exit status:
- 0 only when a value line prints.
- 3 on a stop, so a stop ends a `&&` chain before the PR-creation command runs.
- 1 on an unexpected exception, printing only `REASON: internal-error: <exception type name>`.
- 2 on usage.

Nothing goes to stderr while stdout is writable, and git's stderr is captured, never relayed.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys

_MODE_TICKET = "ticket"
_MODE_CREATE = "create"

_KEYS = ("TICKET_ID", "TITLE", "REASON")
_REASON_CODES = (
    "usage",
    "internal-error",
    "not-on-branch",
    "not-plain-word",
    "body-unreadable",
    "body-empty",
)

_TICKET_ID_PATTERN = r"[A-Za-z]+-[0-9]+"
_TITLE_PATTERN = r"[A-Za-z0-9._: -]+"
_BODY_PATH_PATTERN = r"/[A-Za-z0-9._/-]*"
_BODY_WHITESPACE = b" \t\n\v\f\r"
_NO_TICKET_ID = "none"
_TITLE_MAX_CHARACTERS = 70
_BRANCH_REF_PREFIX = "refs/heads/"

_EXIT_DECIDED = 0
_EXIT_INTERNAL_ERROR = 1
_EXIT_USAGE = 2
_EXIT_STOP = 3

_USAGE_SENTENCE = "pr-create-preflight.py ticket | pr-create-preflight.py create <body path>"
_SENTENCE_NOT_ON_BRANCH = "HEAD is not on a local branch."
_SENTENCE_TITLE_NOT_PLAIN = "the title is empty or holds a character outside A-Z a-z 0-9 . _ : - and space."
_SENTENCE_PATH_NOT_PLAIN = "the body path is not absolute or holds a character outside A-Z a-z 0-9 . _ / -."
_SENTENCE_BODY_UNREADABLE = "the body path is not a readable regular file."
_SENTENCE_BODY_EMPTY = "the body file holds only whitespace."


class Stop(Exception):
    """A named reason a mode stops, carried to main as one `REASON` line."""

    def __init__(self, code: str, sentence: str) -> None:
        super().__init__(code)
        self.code = code
        self.sentence = sentence


def _fullmatches(pattern: str, value: str) -> bool:
    # fullmatch, not a `$`-anchored match, because `$` accepts a trailing newline.
    return re.fullmatch(pattern, value) is not None


def _line(key: str, value: str) -> str:
    """The only builder of output lines, so an unknown key cannot print."""
    if key not in _KEYS:
        raise RuntimeError("not an output key")
    return key + ": " + value


def _emit(lines: list[str]) -> None:
    # One write of UTF-8 bytes, so an encoding error never leaves partial output on stdout.
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _output_value(text: str) -> str:
    return text.removesuffix("\n")


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """The only place a child process starts. Child stderr is captured and never relayed.

    Output is decoded here, not by text mode, because text mode turns a lone CR or CRLF into "\\n".
    """
    completed = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    return subprocess.CompletedProcess(
        argv, completed.returncode, stdout=completed.stdout.decode("utf-8", errors="replace"), stderr=""
    )


def _git(*args: str) -> subprocess.CompletedProcess:
    return _run(["git", *args], dict(os.environ))


def _read_branch() -> str:
    completed = _git("symbolic-ref", "--quiet", "HEAD")
    head_ref = _output_value(completed.stdout)
    if completed.returncode != 0 or not head_ref.startswith(_BRANCH_REF_PREFIX):
        raise Stop("not-on-branch", _SENTENCE_NOT_ON_BRANCH)
    return head_ref.removeprefix(_BRANCH_REF_PREFIX)


def _ticket_id(branch: str) -> str | None:
    first_segment = branch.split("/", 1)[0]
    return first_segment if _fullmatches(_TICKET_ID_PATTERN, first_segment) else None


def _slug_words(branch: str) -> list[str]:
    slug = branch.rsplit("/", 1)[-1]
    return [word for word in slug.split("-") if word]


def _derive_title(ticket_id: str | None, words: list[str]) -> str:
    if not words:
        return ""
    prefix = "" if ticket_id is None else ticket_id + ": "
    full_title = prefix + " ".join(words)
    if len(full_title) <= _TITLE_MAX_CHARACTERS:
        return full_title
    title = prefix + words[0]
    if len(title) > _TITLE_MAX_CHARACTERS:
        return full_title[:_TITLE_MAX_CHARACTERS].rstrip(" ")
    for word in words[1:]:
        longer_title = title + " " + word
        if len(longer_title) > _TITLE_MAX_CHARACTERS:
            break
        title = longer_title
    return title


def _require_plain_title(title: str) -> None:
    if not _fullmatches(_TITLE_PATTERN, title):
        raise Stop("not-plain-word", _SENTENCE_TITLE_NOT_PLAIN)


def _require_plain_body_path(body_path: str) -> None:
    if not _fullmatches(_BODY_PATH_PATTERN, body_path):
        raise Stop("not-plain-word", _SENTENCE_PATH_NOT_PLAIN)


def _read_regular_file(path: str) -> bytes:
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise Stop("body-unreadable", _SENTENCE_BODY_UNREADABLE)
        with open(path, "rb") as body_file:
            return body_file.read()
    except OSError:
        raise Stop("body-unreadable", _SENTENCE_BODY_UNREADABLE) from None


def _require_body_content(body_path: str) -> None:
    if _read_regular_file(body_path).strip(_BODY_WHITESPACE) == b"":
        raise Stop("body-empty", _SENTENCE_BODY_EMPTY)


def _render_stop(code: str, sentence: str) -> str:
    return _line("REASON", code + ": " + sentence)


def _ticket_lines() -> list[str]:
    ticket_id = _ticket_id(_read_branch())
    return [_line("TICKET_ID", _NO_TICKET_ID if ticket_id is None else ticket_id)]


def _create_lines(body_path: str) -> list[str]:
    branch = _read_branch()
    title = _derive_title(_ticket_id(branch), _slug_words(branch))
    _require_plain_title(title)
    _require_plain_body_path(body_path)
    _require_body_content(body_path)
    return [_line("TITLE", title)]


def _arguments_are_valid(argv: list[str]) -> bool:
    return (argv == [_MODE_TICKET]) or (len(argv) == 2 and argv[0] == _MODE_CREATE)


def main(argv: list[str]) -> int:
    if not _arguments_are_valid(argv):
        _emit([_render_stop("usage", _USAGE_SENTENCE)])
        return _EXIT_USAGE
    try:
        lines = _ticket_lines() if argv[0] == _MODE_TICKET else _create_lines(argv[1])
        status = _EXIT_DECIDED
    except Stop as stop:
        lines = [_render_stop(stop.code, stop.sentence)]
        status = _EXIT_STOP
    except Exception as error:
        # Only the type name is printed: an exception's text can carry the path.
        lines = [_render_stop("internal-error", type(error).__name__)]
        status = _EXIT_INTERNAL_ERROR
    _emit(lines)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
