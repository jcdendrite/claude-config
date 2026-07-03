#!/usr/bin/env python3
"""Tokenize a Bash command string into CD/GIT/SENTINEL records for
require-worktree-for-git-writes.sh.

Reads the raw command on stdin, writes one record per line to stdout, with
fields separated by the ASCII Unit Separator (0x1f, written \\x1f below) —
NOT a tab. Bash's word-splitting treats tab as an IFS-whitespace character
regardless of what IFS is set to, so consecutive tabs collapse instead of
producing an empty field (`IFS=$'\\t' read -r a b c <<< $'x\\t\\ty'` yields
`b=y`, not `b=""` — verified empirically); this silently shifts every field
after an empty one (e.g. an absent `-C`, which is the common case) out of
alignment in the bash consumer's `read`. The Unit Separator is not
IFS-whitespace, so `IFS=$'\\x1f' read` preserves empty fields correctly.
`_lib.sh::_lib_parse_tool_input_or_deny` already uses this same byte for
the identical reason.

    CD\\x1f<target>\\x1f<preceding-op>\\x1f<in-group>
    GIT\\x1f<subcmd>\\x1f<c-path>\\x1f<c-status>\\x1f<preceding-op>\\x1f<in-group>
    SENTINEL\\x1f<reason>

<target>    literal cd argument, or "" if it cannot be resolved as a plain
            path (missing, "-", or requires shell expansion: ~, $, *, ?, or
            an embedded delimiter byte/newline).
<preceding-op>  the operator immediately before this segment: ;, &&, ||, |,
            &, or START for the first segment (or the first segment inside
            a group).
<in-group>  1 if the segment sits inside an unclosed ( ... ) / $( ... ) /
            `...` group, else 0. A git write in this state is judged
            "not cleanly resolvable" by the caller regardless of any cd or
            -C seen — group-scoped cd does not affect the parent shell's
            cwd, so nothing inside a group can be trusted to relocate a
            write safely.
<c-path>    the value of a literal global `-C` flag, or "" if absent.
<c-status>  NONE (no -C), LITERAL (c-path is a plain resolvable value), or
            UNRESOLVED (more than one -C, or a value needing expansion) —
            UNRESOLVED must deny a write outright; it must not fall back to
            the threaded cwd, since the flag genuinely retargets git's
            working directory to an address we cannot resolve.
<reason>    fail-closed explanation; the caller denies on any SENTINEL line
            regardless of position in the output.

A segment that invokes neither `cd` nor `git` emits nothing.

Tokenization uses the stdlib shlex class configured
`posix=True, punctuation_chars=True, whitespace_split=True, commenters=''`.
`whitespace_split=True` is required alongside `punctuation_chars` for
shell-like word splitting (confirmed against
https://docs.python.org/3/library/shlex.html and empirically: without it,
punctuation characters fragment ordinary argument words). `shlex.split()`
cannot be used here — it rejects the `punctuation_chars` keyword entirely.
`punctuation_chars=True` requires Python 3.6+.

Empirically verified behavior this parser depends on (python3 -c checks
during implementation): `&&`/`||`/`;`/`|`/`&` come through as single
tokens; a quoted argument (`"git history"`, `'git push'`) tokenizes as one
token, so it can never equal the bare word `git`; `~`, `$VAR`, and `$(...)`
are never expanded — they pass through literally, which is exactly the
signal used to mark a cd/-C target as needing expansion; unbalanced quotes
raise ValueError. Backticks are NOT in the default punctuation_chars set,
so a raw command has spaces inserted around every backtick before
tokenization — otherwise `` `git push` `` would tokenize as two tokens
(`` `git `` and ``push` ``) that never equal the bare word `git`, silently
hiding a git invocation from detection.
"""
import re
import shlex
import sys

# Global flags that consume the next word. Deliberately duplicates (not
# shares) `_lib.sh`'s `_lib_extract_git_subcmd` flag-skip list — this
# parser and that bash function serve two independently-evolving purposes
# (cwd-aware write judgment here; commit-message/PR-readiness fragment
# parsing there) that happen to need the same small, stable domain fact.
# See require-worktree-for-git-writes.sh's "Scope boundary" header section
# for the named two-parsers-coexist exception this is part of.
GIT_FLAGS_WITH_ARG = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
}

FIELD_SEP = "\x1f"  # ASCII Unit Separator — see module docstring.

OPERATORS = {";", "&&", "||", "|", "&"}

# Heredoc opener: `<<WORD`, `<<-WORD`, `<<'WORD'`, `<<"WORD"`. Requires an
# even number of quote characters before the match on the same line — an
# odd count means the `<<` sits inside an already-open quoted string, so it
# is not a real heredoc redirection (e.g. `echo "see <<EOF for setup"`).
HEREDOC_RE = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _quote_count_before(line, index):
    segment = line[:index]
    return segment.count("'") + segment.count('"')


def strip_heredocs(command):
    """Drop heredoc bodies from `command`. Returns the stripped command, or
    None if a heredoc is opened but never terminated (caller must treat
    that as a parse failure, not silently drop to EOF)."""
    lines = command.split("\n")
    out_lines = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        match = None
        for candidate in HEREDOC_RE.finditer(line):
            if _quote_count_before(line, candidate.start()) % 2 == 0:
                match = candidate
                break
        if match is None:
            out_lines.append(line)
            i += 1
            continue
        out_lines.append(line[: match.start()])
        strip_tabs = match.group(1) == "-"
        terminator = match.group(3)
        i += 1
        terminated = False
        while i < n:
            body_line = lines[i]
            candidate_line = body_line.lstrip("\t") if strip_tabs else body_line
            i += 1
            if candidate_line == terminator:
                terminated = True
                break
        if not terminated:
            return None
    return "\n".join(out_lines)


def _contains_wire_delimiter(value):
    """True if `value` contains the field separator or newline byte used
    by this script's stdout protocol (field separator within a record,
    newline between records). Any token interpolated into an emitted
    record must be checked against this — an unchecked value could forge
    extra fields or record lines (a `git` subcommand token containing a
    literal delimiter/newline can fabricate a fake CD/GIT record that the
    bash consumer would parse as if it were real). A literal tab is also
    rejected, though it is no longer a wire delimiter, as defense in depth
    — an unusual character for a real path or subcommand token."""
    return FIELD_SEP in value or "\n" in value or "\t" in value


def _needs_expansion(value):
    if not value or value == "-":
        return True
    if any(ch in value for ch in "~$*?"):
        return True
    return _contains_wire_delimiter(value)


def classify_cd_segment(tokens):
    if len(tokens) < 2 or _needs_expansion(tokens[1]):
        return ""
    return tokens[1]


def _is_git_token(token):
    return token == "git" or token.endswith("/git")


def classify_git_segment(tokens):
    """Returns None if not a git-invoking segment, "SENTINEL" if a git
    invocation has no discoverable subcommand, else a dict with subcmd,
    c_path, c_status."""
    git_idx = None
    for idx, token in enumerate(tokens):
        if _is_git_token(token):
            git_idx = idx
            break
    if git_idx is None:
        return None

    subcmd = None
    c_path = ""
    c_status = "NONE"
    seen_c_count = 0
    i = git_idx + 1
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token in GIT_FLAGS_WITH_ARG:
            if i + 1 >= n:
                return "SENTINEL"
            value = tokens[i + 1]
            if token == "-C":
                seen_c_count += 1
                if seen_c_count > 1 or _needs_expansion(value):
                    c_status = "UNRESOLVED"
                    c_path = ""
                elif c_status != "UNRESOLVED":
                    c_status = "LITERAL"
                    c_path = value
            i += 2
            continue
        if token.startswith("-") and token != "-":
            i += 1
            continue
        subcmd = token
        break

    if subcmd is None or _contains_wire_delimiter(subcmd):
        # A subcommand token containing a literal tab/newline (only reachable
        # via a quoted argument with embedded whitespace) would otherwise be
        # written verbatim into "GIT\t{subcmd}\t..." and printed as-is —
        # print() terminates on \n, so an embedded newline splits one record
        # into multiple physical lines, and if the attacker-chosen content
        # between the delimiters matches the wire grammar, the bash consumer
        # parses it as a separate, fabricated record it never actually
        # emitted for a real cd/git invocation. Deny rather than risk that.
        return "SENTINEL"
    return {"subcmd": subcmd, "c_path": c_path, "c_status": c_status}


def emit_segment(tokens, preceding_op, in_group_flag, records):
    if not tokens:
        return
    if tokens[0] == "cd":
        target = classify_cd_segment(tokens)
        records.append(
            FIELD_SEP.join(["CD", target, preceding_op, "1" if in_group_flag else "0"])
        )
        return
    result = classify_git_segment(tokens)
    if result is None:
        return
    if result == "SENTINEL":
        records.append(FIELD_SEP.join(["SENTINEL", "could not determine the git subcommand"]))
        return
    records.append(
        FIELD_SEP.join(
            [
                "GIT",
                result["subcmd"],
                result["c_path"],
                result["c_status"],
                preceding_op,
                "1" if in_group_flag else "0",
            ]
        )
    )


def tokenize(command):
    """Returns a token list, or None on unbalanced quotes."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return None


def build_records(command):
    """Pure string -> list-of-record-strings. Returns a single-element
    SENTINEL list on any parse failure."""
    stripped = strip_heredocs(command)
    if stripped is None:
        return [FIELD_SEP.join(["SENTINEL", "unterminated heredoc"])]

    # Backticks are not in shlex's default punctuation_chars, so a raw
    # backtick would otherwise fuse into an adjacent word (see module
    # docstring). Isolating them as their own tokens lets the segment loop
    # below track backtick-substitution scope the same way it tracks
    # parenthesized groups.
    normalized = stripped.replace("`", " ` ")

    tokens = tokenize(normalized)
    if tokens is None:
        return [FIELD_SEP.join(["SENTINEL", "could not tokenize command (unbalanced quotes)"])]

    records = []
    current = []
    preceding_op = "START"
    paren_depth = 0
    backtick_open = False

    def in_group():
        return paren_depth > 0 or backtick_open

    def flush(next_op):
        nonlocal current, preceding_op
        emit_segment(current, preceding_op, in_group(), records)
        current = []
        preceding_op = next_op

    unbalanced = False
    for token in tokens:
        if token in OPERATORS:
            flush(token)
        elif token == "(":
            flush(preceding_op)
            paren_depth += 1
        elif token == ")":
            flush(preceding_op)
            paren_depth -= 1
            if paren_depth < 0:
                unbalanced = True
                break
        elif token == "`":
            flush(preceding_op)
            backtick_open = not backtick_open
        elif token == "$":
            # Inert marker preceding "(" in a $(...) command substitution —
            # the following "(" already opens the group; "$" carries no
            # information of its own once isolated as its own token.
            continue
        else:
            current.append(token)
    if not unbalanced:
        flush(None)

    if unbalanced or paren_depth != 0 or backtick_open:
        return [FIELD_SEP.join(["SENTINEL", "unbalanced group in command"])]

    # A SENTINEL from a mid-stream unparseable git invocation takes
    # priority over any records after it — the caller denies on the first
    # SENTINEL line regardless of position, so ordering here doesn't
    # matter for correctness, but returning early keeps the output small.
    for record in records:
        if record.startswith("SENTINEL"):
            return [record]
    return records


def main():
    command = sys.stdin.read()
    for record in build_records(command):
        print(record)


if __name__ == "__main__":
    main()
