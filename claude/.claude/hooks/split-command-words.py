#!/usr/bin/env python3
"""Split a Bash command string into words, one per output line, for the
fallback path of _lib.sh's _lib_command_words (used when `xargs -n1` cannot
tokenize the command, for example a quoted value that spans a newline).

Reads the raw command on stdin and writes the words to stdout. Both are
UTF-8 with surrogateescape, so every byte round-trips into the callers'
path checks. A newline inside a word is written as a space, so no multi-line
word can equal a flag word.

Exit status contract:
  0  success, words on stdout (possibly none).
  3  the input cannot be tokenized (shlex rejects it, such as an unbalanced
     quote) or stdin is over 2 MiB. Nothing is written to stdout.
  2  any other failure: a tool fault, not a property of the command.
Status 1 is never used, so CPython's own uncaught-exception status cannot
read as "cannot be tokenized".

Before tokenizing, the script drops heredoc bodies (an opener line is kept
whole), drops comment lines, and joins backslash-newline continuations. It
then splits with stdlib `shlex` in POSIX mode, with comment handling off so a
mid-word `#` (`Fix#12`) stays part of its word.

The splitter diverges from `xargs -n1` on purpose:
  - A backslash-newline joins the lines (xargs yields two words).
  - `\\"` inside double quotes is honored (xargs fails on it).
  - `-e`, `-n`, and `-E` words are kept (xargs's echo swallows them).
  - A `\\\\` pair inside double quotes yields one backslash (xargs keeps both).

Residual over-denies, where valid bash still makes the splitter exit 3:
  - A `"$(...)"` or backtick substitution whose inner quotes leave the flat
    quote parity open, such as `--title "$(echo "it's")"`.
  - A `$'...'` string with an escaped apostrophe, such as `$'it\\'s'`.
  - An inline comment holding an unbalanced quote, such as
    `git commit -F /x  # don't amend`. Only whole comment lines are dropped.
  - A multi-line quoted value whose closing quote sits on a line starting with
    `#`, such as a message whose last line begins `#123`. The quote-blind
    comment-line drop removes that line, leaving the quote open.
Residual over-denies outside the splitter:
  - An unrelated tool's flag that a caller also reads (`ssh -T host`,
    `tar -T list`) reaches the caller once xargs's failure is recovered. The
    splitter exits 0 there; the caller's file read denies.
  - A command made only of comment lines that xargs cannot split, such as
    `# it's`, yields no words, and `_lib_command_words` denies it.
Residual misses, where the splitter drops words bash keeps. Each applies only
past the point where xargs failed:
  - A quoted `<<WORD` mention followed later by a line equal to `WORD` makes
    the splitter drop the lines between them.
  - A `<<WORD` inside a comment line, or an arithmetic `<<` such as
    `$((1<<3))`, opens a heredoc the same way and drops lines bash keeps.
  - The comment-line drop is quote-blind. A line starting with `#` inside a
    multi-line quoted value is dropped, which hides any flag that follows the
    closing quote on that line.

The helper is calibrated for cooperative callers, which make honest mistakes.
The quote-blind drops above are not closed against input crafted to evade
them.

The 2 MiB cap bounds memory, not time. `shlex` is quadratic in the length of
one word, and the heredoc scan is quadratic in the number of unterminated
openers. The caller's 5 s cap (`_lib_capped` in _lib.sh) is the only runtime bound. A machine with neither
`timeout` nor `gtimeout` does not kill an over-long run, and a hook that
outlives the harness timeout leaves the call un-gated (see `_lib_capped_for`'s
header in _lib.sh).

parse-git-command.py keeps its own heredoc stripper, which skips an opener
that follows an odd quote count on its line.
"""
import re
import shlex
import sys

EXIT_INTERNAL_ERROR = 2
EXIT_UNTOKENIZABLE = 3

# Matches the cap parse-manifest-dependencies.py uses; a real shell command is far smaller.
MAX_STDIN_BYTES = 2 * 1024 * 1024

# `<<` or `<<-` that is not part of `<<<`, then optional blanks, then a bare, single-quoted,
# double-quoted, or backslash-prefixed delimiter. Matched regardless of quote context.
HEREDOC_OPENER_RE = re.compile(
    r"(?<!<)<<(?!<)(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([^\s'\"\\;&|<>()`$]+))"
)

COMMENT_LINE_RE = re.compile(r"[ \t]*#")

# A backslash-newline preceded by an even number of backslashes is an escaped backslash
# followed by a real newline, not a continuation.
LINE_CONTINUATION_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\\n")


def _opener_delimiters(line):
    """Return (strip_tabs, delimiter) for each heredoc opener on `line`, in order."""
    openers = []
    for match in HEREDOC_OPENER_RE.finditer(line):
        delimiter = match.group(2) or match.group(3) or match.group(4)
        openers.append((match.group(1) == "-", delimiter))
    return openers


def drop_heredoc_bodies(text):
    """Remove the body lines of every terminated heredoc, keeping each opener line whole.

    Lines are split on LF only: `str.splitlines()` would also break on U+2028, U+0085,
    and `\\x0b`/`\\x0c`/`\\x1c`-`\\x1e`, which bash treats as ordinary characters.
    """
    lines = text.split("\n")
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for strip_tabs, delimiter in _opener_delimiters(line):
            terminator_index = None
            for candidate_index in range(index, len(lines)):
                candidate = lines[candidate_index].lstrip("\t") if strip_tabs else lines[candidate_index]
                if candidate == delimiter:
                    terminator_index = candidate_index
                    break
            if terminator_index is not None:
                index = terminator_index + 1
    return "\n".join(kept)


def drop_comment_lines(text):
    """Remove every line whose first non-blank character is `#`."""
    return "\n".join(line for line in text.split("\n") if not COMMENT_LINE_RE.match(line))


def join_line_continuations(text):
    return LINE_CONTINUATION_RE.sub(r"\1", text)


def split_command_words(command):
    """Return the words of `command`, or raise ValueError when it cannot be tokenized."""
    text = join_line_continuations(drop_comment_lines(drop_heredoc_bodies(command)))
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return [word.replace("\n", " ") for word in lexer]


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            return EXIT_UNTOKENIZABLE
        command = raw.decode("utf-8", errors="surrogateescape")
        try:
            words = split_command_words(command)
        except ValueError:
            return EXIT_UNTOKENIZABLE
        output = "".join(word + "\n" for word in words)
        sys.stdout.buffer.write(output.encode("utf-8", errors="surrogateescape"))
        sys.stdout.buffer.flush()
    except Exception as error:
        print("split-command-words.py: internal error: " + type(error).__name__, file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
