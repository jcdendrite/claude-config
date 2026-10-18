"""Check that a code move between files changed only what was declared.

Run as `_move_fidelity.py <subcommand>`, or import the check functions. One usage line per subcommand:

  nodes --before FILE --after FILE... --moved NAME... [--rename OLD=NEW...] [--verbatim NAME...]
  remainder --before FILE --after FILE [--removed|--added|--changed NAME...] [--import-added|--import-removed TRIPLE...]
  capture --script FILE --account DIR --out DIR [--runs FILE] [--all-help] [--env NAME=VALUE...] [--unset NAME...]
  compare-captures DIR_A DIR_B [--mask REGEX...] [--moved-callable QUALNAME=MODULE...]
  test-ids --before XML... --after XML... [--file-prefix PATH] [--rename OLD=NEW...] [--expect-new ID...]

Each finding prints as one line prefixed with its check name. Exit status is 0 when clean, 1 on any finding,
2 on bad input. The module imports only the standard library and uses no relative imports.

`nodes --rename` reverses a NEW name only where it occurs as code, including inside an f-string replacement field,
so a NEW name in a string literal, docstring, or comment is compared as written.
`nodes --rename` needs the f-string tokens Python 3.12's `tokenize` reports, and exits 2 on an interpreter without them.
`capture --all-help` also writes `<label>.parser` for the top level and each subcommand: one line per argparse action,
mutually exclusive group, and `set_defaults` entry, with each callable rendered as `<callable MODULE:QUALNAME>`
and the captured script's own module as `<script>`.
The `<script>` substitution applies to `<callable ...>` tokens only.
An `<instance MODULE:TYPE>` token prints the type's own module. For a type the script defines, that is the harness's
capture-target module name (`_move_fidelity_capture_target`). --moved-callable does not rewrite it.
The dump covers one level of subcommands and no callable's body.
A value that is not a callable with `__module__` and `__qualname__`, and whose `repr()` carries a memory address,
renders as `<instance MODULE:TYPE>`: its type only.
Its state is not compared, such as the arguments of a `functools.partial` over a function or a validator instance's fields.
The dump covers a fixed subset of attributes, PARSER_ATTRIBUTES per parser and ACTION_ATTRIBUTES per action.
Other settings are not compared, such as `fromfile_prefix_chars`, `conflict_handler`, and a version action's version string.
The dump calls `build_parser()` in the harness process under the ambient environment, not capture's pinned one.
`compare-captures --moved-callable QUALNAME=MODULE` rewrites `<script>:QUALNAME` to `MODULE:QUALNAME`
in DIR_A's `.parser` files only, so DIR_A must be the before side.
`remainder` drops every top-level import statement, with any comment on its lines, before comparing,
so it checks neither import order nor import-line comments.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import importlib.util
import io
import os
import re
import shlex
import subprocess
import sys
import tokenize
import xml.etree.ElementTree as ElementTree
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_BAD_INPUT = 2

MODULE_DOCSTRING_NAME = "__doc__"
# argparse wraps help text at COLUMNS, so capture pins it.
PINNED_COLUMNS = "100"
# A fixed hash seed fixes set iteration order in the captured script.
PINNED_HASHSEED = "0"
# A file name under --account that capture never creates, so a corpus-config lookup finds no file.
ABSENT_CONFIG_DIRS_FILE = "absent-config-dirs"
CAPTURE_TARGET_MODULE_NAME = "_move_fidelity_capture_target"
MASK_TOKEN = b"<masked>"
SCRIPT_MODULE_LABEL = "<script>"
PARSER_DUMP_SUFFIX = ".parser"
TOP_HELP_LABEL = "help-top"
PARSER_ATTRIBUTES = ("allow_abbrev", "argument_default", "prefix_chars", "exit_on_error")
ACTION_ATTRIBUTES = ("option_strings", "dest", "nargs", "const", "default", "type", "choices", "required")
OUTCOME_TAGS = ("failure", "error", "skipped")
PASSED_OUTCOME = "passed"
SNIPPET_LIMIT = 80
DIFF_LINE_LIMIT = 20


class BadInput(Exception):
    """An unusable argument or file; main() maps it to exit status 2."""


# --- shared helpers ----------------------------------------------------------


def _one_line(text: str, limit: int = SNIPPET_LIMIT) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3] + "..."


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BadInput(f"cannot read {path}: {error}") from error


def _parse_key_value_pairs(specs: Iterable[str], flag: str) -> list[tuple[str, str]]:
    pairs = []
    for spec in specs:
        key, separator, value = spec.partition("=")
        if not separator or not key:
            raise BadInput(f"{flag} expects NAME=VALUE with a non-empty NAME, got {spec!r}")
        pairs.append((key, value))
    return pairs


def _require_distinct_declarations(declared_by_flag: dict[str, Sequence[str]]) -> None:
    owner_by_name: dict[str, str] = {}
    for flag, names in declared_by_flag.items():
        for name in names:
            if owner_by_name.setdefault(name, flag) != flag:
                raise BadInput(f"{name} is declared under both {owner_by_name[name]} and {flag}")


@dataclass(frozen=True)
class TopLevelNode:
    name: str
    statement: ast.stmt
    first_line: int
    last_line: int


@dataclass(frozen=True)
class ParsedSource:
    path: Path
    source: str
    lines: list[str]
    tree: ast.Module
    nodes: list[TopLevelNode]

    @property
    def has_module_docstring(self) -> bool:
        return ast.get_docstring(self.tree, clean=False) is not None

    @property
    def defined_names(self) -> set[str]:
        names = {node.name for node in self.nodes}
        if self.has_module_docstring:
            names.add(MODULE_DOCSTRING_NAME)
        return names

    def segment(self, node: TopLevelNode) -> str:
        return "".join(self.lines[node.first_line - 1 : node.last_line])


def _statement_name(statement: ast.stmt) -> str | None:
    if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return statement.name
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
        return statement.targets[0].id
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id
    return None


def _first_line(statement: ast.stmt) -> int:
    decorators = getattr(statement, "decorator_list", [])
    return min([statement.lineno, *(decorator.lineno for decorator in decorators)])


def _parse_source(source: str, label: str) -> ast.Module:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise BadInput(f"cannot parse {label}: {error}") from error


def _load_source(path: Path) -> ParsedSource:
    source = _read_text(path)
    tree = _parse_source(source, str(path))
    nodes = [
        TopLevelNode(name, statement, _first_line(statement), statement.end_lineno)
        for statement in tree.body
        if (name := _statement_name(statement)) is not None
    ]
    return ParsedSource(path, source, io.StringIO(source).readlines(), tree, nodes)


def _tokens(source: str) -> list[tokenize.TokenInfo]:
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError) as error:
        raise BadInput(f"cannot tokenize source: {error}") from error


def _comment_tokens(source: str) -> list[tuple[int, str]]:
    return [(token.start[0], token.string) for token in _tokens(source) if token.type == tokenize.COMMENT]


def _emit(findings: list[str], summary: str) -> int:
    for finding in findings:
        print(finding)
    print(summary)
    return EXIT_FINDINGS if findings else EXIT_CLEAN


# --- nodes -------------------------------------------------------------------


def _name_boundary_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    """Match any of `names` as a whole identifier or dotted name, longest first so overlapping names resolve to the longest."""
    longest_first = sorted(set(names), key=len, reverse=True)
    if not longest_first:
        return None
    return re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(name) for name in longest_first) + r")(?!\w)")


def _non_code_spans(source: str) -> list[tuple[int, int]]:
    """Character spans of the STRING, FSTRING_MIDDLE, and COMMENT tokens; a multi-line token's span covers each of its lines."""
    line_starts = [0]
    for line in io.StringIO(source).readlines():
        line_starts.append(line_starts[-1] + len(line))
    non_code_types = {tokenize.STRING, tokenize.FSTRING_MIDDLE, tokenize.COMMENT}
    return [
        (line_starts[token.start[0] - 1] + token.start[1], line_starts[token.end[0] - 1] + token.end[1])
        for token in _tokens(source)
        if token.type in non_code_types
    ]


def _code_name_matches(text: str, pattern: re.Pattern[str]) -> list[re.Match[str]]:
    """Matches of `pattern` that start outside every string, f-string literal, and comment token of `text`."""
    spans = _non_code_spans(text)
    return [match for match in pattern.finditer(text) if not any(start <= match.start() < end for start, end in spans)]


def _contains_name_as_code(text: str, name: str) -> bool:
    pattern = _name_boundary_pattern([name])
    return pattern is not None and bool(_code_name_matches(text, pattern))


def _reverse_renames(text: str, reverse_map: dict[str, str], reverse_pattern: re.Pattern[str] | None) -> str:
    """Replace each NEW name that occurs as code in `text` with its OLD name."""
    if reverse_pattern is None:
        return text
    pieces = []
    cursor = 0
    for match in _code_name_matches(text, reverse_pattern):
        pieces.append(text[cursor : match.start()])
        pieces.append(reverse_map[match.group(0)])
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _first_dump_difference(before_dump: str, after_dump: str) -> str:
    for before_line, after_line in zip_longest(before_dump.splitlines(), after_dump.splitlines(), fillvalue="<end>"):
        if before_line != after_line:
            return f"before {before_line.strip()!r}, after {after_line.strip()!r}"
    return "dumps differ"


def _node_dump(text: str, label: str) -> tuple[str | None, str | None]:
    """Return (dump, None), or (None, reason) when the text is not exactly one statement."""
    try:
        body = ast.parse(text).body
    except (SyntaxError, ValueError) as error:
        return None, f"{label} text does not parse: {error}"
    if len(body) != 1:
        return None, f"{label} text holds {len(body)} statements, expected 1"
    return ast.dump(body[0], indent=1), None


def check_nodes(
    before_path: Path,
    after_paths: Sequence[Path],
    moved: Sequence[str],
    renames: Sequence[tuple[str, str]] = (),
    verbatim: Sequence[str] = (),
) -> tuple[list[str], int]:
    """Return (findings, compared-node count). A node is compared once it is defined exactly once on each side."""
    moved_names = list(dict.fromkeys(moved))
    if not moved_names:
        raise BadInput("--moved needs at least one name")
    for name in moved_names:
        if not name.isidentifier():
            raise BadInput(f"--moved name {name!r} is not an identifier")
    stray_verbatim = [name for name in verbatim if name not in moved_names]
    if stray_verbatim:
        raise BadInput(f"--verbatim names not listed in --moved: {', '.join(stray_verbatim)}")
    reverse_map = {new: old for old, new in renames}
    if len(reverse_map) != len(renames):
        raise BadInput("two --rename entries share the same NEW name")
    # The attribute is read at call time so the module still imports, and the other subcommands still run, below Python 3.12.
    if renames and not hasattr(tokenize, "FSTRING_MIDDLE"):
        raise BadInput("--rename needs Python 3.12 or later: tokenize reports no f-string tokens")
    reverse_pattern = _name_boundary_pattern(reverse_map)

    before = _load_source(before_path)
    afters = [_load_source(path) for path in after_paths]
    findings: list[str] = []
    compared = 0
    compared_after_texts: list[str] = []
    for name in moved_names:
        before_matches = [node for node in before.nodes if node.name == name]
        if len(before_matches) != 1:
            findings.append(f"node-missing: {name}: defined {len(before_matches)} time(s) in {before_path}, expected exactly 1")
            continue
        after_matches = [(parsed, node) for parsed in afters for node in parsed.nodes if node.name == name]
        if not after_matches:
            findings.append(f"node-missing: {name}: not defined in any --after file")
            continue
        if len(after_matches) > 1:
            sites = ", ".join(str(parsed.path) for parsed, _ in after_matches)
            findings.append(f"node-duplicate: {name}: defined {len(after_matches)} times across --after files: {sites}")
            continue
        after, after_node = after_matches[0]
        compared += 1
        before_text = before.segment(before_matches[0])
        after_text = after.segment(after_node)
        compared_after_texts.append(after_text)
        findings.extend(_compare_node(name, before_text, after_text, reverse_map, reverse_pattern, name in verbatim))
    # Matching with the reversal's own longest-first pattern leaves a NEW name that occurs only inside a longer NEW name unused.
    used_new_names = {
        match.group(0)
        for text in compared_after_texts
        for match in (_code_name_matches(text, reverse_pattern) if reverse_pattern else ())
    }
    for new_name, old_name in reverse_map.items():
        if new_name not in used_new_names:
            findings.append(f"rename-unused: {old_name}={new_name}: {new_name} occurs in none of the compared nodes")
    return findings, compared


def _compare_node(
    name: str,
    before_text: str,
    after_text: str,
    reverse_map: dict[str, str],
    reverse_pattern: re.Pattern[str] | None,
    is_verbatim: bool,
) -> list[str]:
    findings = []
    if is_verbatim and before_text != after_text:
        findings.append(f"verbatim: {name}: source lines differ from the original")
    for new_name in reverse_map:
        if _contains_name_as_code(before_text, new_name):
            findings.append(f"rename-ambiguous: {name}: {new_name} already occurs in the original node")
            return findings
    reversed_text = _reverse_renames(after_text, reverse_map, reverse_pattern)
    before_dump, before_problem = _node_dump(before_text, "before")
    after_dump, after_problem = _node_dump(reversed_text, "after")
    if before_problem or after_problem:
        findings.append(f"node-ast: {name}: {before_problem or after_problem}")
    elif before_dump != after_dump:
        findings.append(f"node-ast: {name}: {_first_dump_difference(before_dump, after_dump)}")
    before_comments = [text for _, text in _comment_tokens(before_text)]
    after_comments = [text for _, text in _comment_tokens(reversed_text)]
    if before_comments != after_comments:
        findings.append(f"node-comments: {name}: before {before_comments}, after {after_comments}")
    return findings


# --- remainder ---------------------------------------------------------------


def _import_triples(parsed: ParsedSource) -> set[str]:
    """Render top-level imports as `module:name[:asname]`, or the bare name for a plain import.

    A plain import's alias renders as `name::asname` so it cannot collide with a from-import triple.
    """
    triples = set()
    for statement in parsed.tree.body:
        if isinstance(statement, ast.ImportFrom):
            module = "." * statement.level + (statement.module or "")
            for alias in statement.names:
                triples.add(f"{module}:{alias.name}" + (f":{alias.asname}" if alias.asname else ""))
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                triples.add(alias.name + (f"::{alias.asname}" if alias.asname else ""))
    return triples


def _split_body(parsed: ParsedSource, dropped_names: set[str]) -> tuple[list[ast.stmt], list[tuple[int, int]]]:
    """Split the module body into kept statements and the line spans of dropped ones (named nodes and imports)."""
    name_by_statement = {id(node.statement): node.name for node in parsed.nodes}
    docstring_statement = parsed.tree.body[0] if parsed.has_module_docstring else None
    kept: list[ast.stmt] = []
    dropped_spans: list[tuple[int, int]] = []
    for statement in parsed.tree.body:
        dropped = (
            isinstance(statement, ast.Import | ast.ImportFrom)
            or name_by_statement.get(id(statement)) in dropped_names
            or (statement is docstring_statement and MODULE_DOCSTRING_NAME in dropped_names)
        )
        if dropped:
            dropped_spans.append((_first_line(statement), statement.end_lineno))
        else:
            kept.append(statement)
    return kept, dropped_spans


def _comment_blocks(source: str, excluded_spans: list[tuple[int, int]]) -> list[tuple[str, ...]]:
    """Group the comments outside `excluded_spans` into blocks of consecutive lines."""
    blocks: list[list] = []
    for line, text in _comment_tokens(source):
        if any(first <= line <= last for first, last in excluded_spans):
            continue
        if blocks and blocks[-1][0] == line - 1:
            blocks[-1][0] = line
            blocks[-1][1].append(text)
        else:
            blocks.append([line, [text]])
    return [tuple(texts) for _, texts in blocks]


def _statement_summary(parsed: ParsedSource, statement: ast.stmt) -> str:
    return _one_line(parsed.lines[_first_line(statement) - 1])


def _declared_name_findings(
    before: ParsedSource, after: ParsedSource, removed: Sequence[str], added: Sequence[str], changed: Sequence[str]
) -> list[str]:
    before_defined, after_defined = before.defined_names, after.defined_names
    findings = []
    for name in removed:
        if name not in before_defined:
            findings.append(f"declared-mismatch: --removed {name} is not defined in {before.path}")
        if name in after_defined:
            findings.append(f"declared-mismatch: --removed {name} is still defined in {after.path}")
    for name in added:
        if name not in after_defined:
            findings.append(f"declared-mismatch: --added {name} is not defined in {after.path}")
        if name in before_defined:
            findings.append(f"declared-mismatch: --added {name} is already defined in {before.path}")
    for name in changed:
        for side, defined in ((before, before_defined), (after, after_defined)):
            if name not in defined:
                findings.append(f"declared-mismatch: --changed {name} is not defined in {side.path}")
    return findings


def _import_diff_findings(
    before: ParsedSource, after: ParsedSource, declared_added: Sequence[str], declared_removed: Sequence[str]
) -> list[str]:
    before_triples, after_triples = _import_triples(before), _import_triples(after)
    actual_added, actual_removed = after_triples - before_triples, before_triples - after_triples
    findings = []
    for triple in sorted(actual_added - set(declared_added)):
        findings.append(f"import-diff: undeclared added import {triple}")
    for triple in sorted(set(declared_added) - actual_added):
        findings.append(f"import-diff: declared added import {triple} is not an added import")
    for triple in sorted(actual_removed - set(declared_removed)):
        findings.append(f"import-diff: undeclared removed import {triple}")
    for triple in sorted(set(declared_removed) - actual_removed):
        findings.append(f"import-diff: declared removed import {triple} is not a removed import")
    return findings


def check_remainder(
    before_path: Path,
    after_path: Path,
    removed: Sequence[str] = (),
    added: Sequence[str] = (),
    changed: Sequence[str] = (),
    import_added: Sequence[str] = (),
    import_removed: Sequence[str] = (),
) -> tuple[list[str], int]:
    """Return (findings, kept-statement count of the after side)."""
    _require_distinct_declarations({"--removed": removed, "--added": added, "--changed": changed})
    before, after = _load_source(before_path), _load_source(after_path)
    findings = _declared_name_findings(before, after, removed, added, changed)
    findings.extend(_import_diff_findings(before, after, import_added, import_removed))

    before_kept, before_spans = _split_body(before, {*removed, *changed})
    after_kept, after_spans = _split_body(after, {*added, *changed})
    before_dumps = [ast.dump(statement) for statement in before_kept]
    after_dumps = [ast.dump(statement) for statement in after_kept]
    for tag, before_start, before_end, after_start, after_end in _differences(before_dumps, after_dumps):
        before_summary = [_statement_summary(before, statement) for statement in before_kept[before_start:before_end]]
        after_summary = [_statement_summary(after, statement) for statement in after_kept[after_start:after_end]]
        detail = _first_statement_difference(before_kept[before_start:before_end], after_kept[after_start:after_end])
        findings.append(f"remainder-ast: {tag}: before {before_summary}, after {after_summary}{detail}")

    before_blocks = _comment_blocks(before.source, before_spans)
    after_blocks = _comment_blocks(after.source, after_spans)
    for _, before_start, before_end, after_start, after_end in _differences(before_blocks, after_blocks):
        for before_block, after_block in zip_longest(before_blocks[before_start:before_end], after_blocks[after_start:after_end]):
            findings.append(f"remainder-comments: before {list(before_block or ())}, after {list(after_block or ())}")
    return findings, len(after_kept)


def _first_statement_difference(before_statements: Sequence[ast.stmt], after_statements: Sequence[ast.stmt]) -> str:
    """Name the first differing dump line of the first position-paired statements that differ, or return an empty string."""
    for before_statement, after_statement in zip(before_statements, after_statements, strict=False):
        before_dump, after_dump = ast.dump(before_statement, indent=1), ast.dump(after_statement, indent=1)
        if before_dump != after_dump:
            return f"; first difference: {_first_dump_difference(before_dump, after_dump)}"
    return ""


def _differences(before_items: Sequence, after_items: Sequence) -> list[tuple[str, int, int, int, int]]:
    matcher = difflib.SequenceMatcher(None, before_items, after_items, autojunk=False)
    return [opcode for opcode in matcher.get_opcodes() if opcode[0] != "equal"]


# --- capture -----------------------------------------------------------------


def build_capture_env(account: Path, overrides: Sequence[tuple[str, str]] = (), unsets: Sequence[str] = ()) -> dict[str, str]:
    """The ambient environment minus PYTHONPATH, with pinned variables, then `overrides`, then `unsets`."""
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    env.update(
        CLAUDE_CONFIG_DIR=str(account),
        TRANSCRIPT_CONFIG_DIRS_FILE=str(account / ABSENT_CONFIG_DIRS_FILE),
        COLUMNS=PINNED_COLUMNS,
        PYTHONHASHSEED=PINNED_HASHSEED,
    )
    env.update(overrides)
    for name in unsets:
        env.pop(name, None)
    return env


def _read_runs(runs_file: Path) -> list[list[str]]:
    runs = []
    for number, line in enumerate(_read_text(runs_file).splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            runs.append(shlex.split(line))
        except ValueError as error:
            raise BadInput(f"{runs_file} line {number}: {error}") from error
    return runs


def _load_parser(script: Path) -> argparse.ArgumentParser:
    """Run the script's `build_parser()` in this process, under the ambient environment."""
    original_path = list(sys.path)
    original_module_names = set(sys.modules)
    sys.path.insert(0, str(script.parent))
    try:
        spec = importlib.util.spec_from_file_location(CAPTURE_TARGET_MODULE_NAME, script)
        if spec is None or spec.loader is None:
            raise BadInput(f"cannot load {script} as a module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_parser()
    except BadInput:
        raise
    except (Exception, SystemExit) as error:
        raise BadInput(f"cannot read subcommands from {script}: {error!r}") from error
    finally:
        sys.path[:] = original_path
        # A sibling module left cached would be reused by a later call that loads a same-named module of different content.
        for module_name in set(sys.modules) - original_module_names:
            del sys.modules[module_name]


def _subcommand_parsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Subcommand parsers keyed by their first registered name, in registration order; an alias adds no entry."""
    parsers: dict[str, argparse.ArgumentParser] = {}
    seen_parsers: set[int] = set()
    # argparse exposes no public accessor for registered subcommands.
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            if id(subparser) not in seen_parsers:
                seen_parsers.add(id(subparser))
                parsers[name] = subparser
    return parsers


def _render_value(value: object) -> str:
    """Render a parser-configuration value as text that is stable across processes and carries no memory address."""
    if value is None or isinstance(value, bool | int | float | str):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        return "(" + ", ".join(_render_value(item) for item in value) + ("," if len(value) == 1 else "") + ")"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_render_value(key)}: {_render_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, set | frozenset):
        return type(value).__name__ + "{" + ", ".join(sorted(_render_value(item) for item in value)) + "}"
    module, qualname = getattr(value, "__module__", None), getattr(value, "__qualname__", None)
    if callable(value) and isinstance(module, str) and isinstance(qualname, str):
        return f"<callable {SCRIPT_MODULE_LABEL if module == CAPTURE_TARGET_MODULE_NAME else module}:{qualname}>"
    rendered = repr(value)
    if " at 0x" not in rendered:
        return rendered
    return f"<instance {type(value).__module__}:{type(value).__qualname__}>"


def _action_line(action: argparse.Action) -> str:
    fields = []
    for attribute in ACTION_ATTRIBUTES:
        value = getattr(action, attribute)
        if attribute == "choices" and isinstance(action, argparse._SubParsersAction):
            value = list(value)
        fields.append(f"{attribute}={_render_value(value)}")
    return f"action {_render_value(type(action))} " + " ".join(fields)


def dump_parser(parser: argparse.ArgumentParser) -> str:
    """Render one parser's configuration as `\\n`-terminated lines: `parser`, then each `action`, `mutex`, and `default`."""
    # argparse exposes no public accessor for a parser's actions, mutually exclusive groups, or set_defaults entries.
    lines = ["parser " + " ".join(f"{name}={_render_value(getattr(parser, name))}" for name in PARSER_ATTRIBUTES)]
    lines.extend(_action_line(action) for action in parser._actions)
    for group in parser._mutually_exclusive_groups:
        dests = [action.dest for action in group._group_actions]
        lines.append(f"mutex required={_render_value(group.required)} dests={_render_value(dests)}")
    lines.extend(f"default {key}={_render_value(parser._defaults[key])}" for key in sorted(parser._defaults))
    return "\n".join(lines) + "\n"


def capture(
    script: Path,
    account: Path,
    out: Path,
    runs_file: Path | None = None,
    all_help: bool = False,
    env_overrides: Sequence[tuple[str, str]] = (),
    unsets: Sequence[str] = (),
) -> list[str]:
    """Run the script once per planned argv and write `<label>.argv/.stdout/.stderr/.exit` under `out`; return the labels.

    With `all_help`, the help labels also get `<label>.parser`, the dump of that parser's configuration.
    """
    script, account = script.resolve(), account.resolve()
    if not script.is_file():
        raise BadInput(f"--script {script} is not a file")
    if not account.is_dir():
        raise BadInput(f"--account {account} is not a directory")
    if out.exists():
        raise BadInput(f"--out {out} already exists")
    if runs_file is None and not all_help:
        raise BadInput("nothing to capture: pass --runs and/or --all-help")
    planned: list[tuple[str, list[str]]] = []
    parser_dumps: dict[str, str] = {}
    if all_help:
        top_parser = _load_parser(script)
        planned.append((TOP_HELP_LABEL, ["--help"]))
        parser_dumps[TOP_HELP_LABEL] = dump_parser(top_parser)
        for name, subparser in _subcommand_parsers(top_parser).items():
            if not re.fullmatch(r"[\w.-]+", name):
                raise BadInput(f"subcommand name {name!r} cannot be a capture label")
            label = f"help-{name}"
            if label == TOP_HELP_LABEL:
                raise BadInput(f"subcommand name {name!r} collides with the top-level capture label {TOP_HELP_LABEL}")
            planned.append((label, [name, "--help"]))
            parser_dumps[label] = dump_parser(subparser)
    if runs_file is not None:
        planned.extend((f"run-{number:02d}", argv) for number, argv in enumerate(_read_runs(runs_file), start=1))
    if not planned:
        raise BadInput("no runs to capture")
    env = build_capture_env(account, env_overrides, unsets)
    try:
        out.mkdir(parents=True)
    except FileExistsError as error:
        raise BadInput(f"--out {out} already exists") from error
    for label, argv in planned:
        result = subprocess.run(
            [sys.executable, str(script), *argv], stdin=subprocess.DEVNULL, capture_output=True, env=env, check=False
        )
        (out / f"{label}.argv").write_text(shlex.join(argv) + "\n")
        (out / f"{label}.stdout").write_bytes(result.stdout)
        (out / f"{label}.stderr").write_bytes(result.stderr)
        (out / f"{label}.exit").write_text(f"{result.returncode}\n")
        if label in parser_dumps:
            (out / f"{label}{PARSER_DUMP_SUFFIX}").write_text(parser_dumps[label], encoding="utf-8")
    return [label for label, _ in planned]


def _relative_files(directory: Path) -> list[str]:
    if not directory.is_dir():
        raise BadInput(f"{directory} is not a directory")
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file())


def _rewrite_moved_callables(data: bytes, moved_callables: Sequence[tuple[str, str]], rewritten_qualnames: set[str]) -> bytes:
    """Rewrite each `<callable <script>:QUALNAME>` token to its declared MODULE, recording the QUALNAMEs that matched."""
    for qualname, module in moved_callables:
        script_token = f"<callable {SCRIPT_MODULE_LABEL}:{qualname}>".encode()
        if script_token in data:
            data = data.replace(script_token, f"<callable {module}:{qualname}>".encode())
            rewritten_qualnames.add(qualname)
    return data


def compare_captures(
    dir_a: Path, dir_b: Path, masks: Sequence[str] = (), moved_callables: Sequence[tuple[str, str]] = ()
) -> tuple[list[str], int]:
    """Return (findings, compared-file count) comparing every file byte for byte after the moved-callable rewrite and masking.

    The rewrite touches only `dir_a`'s `.parser` files, so `dir_a` must be the before side.
    """
    try:
        patterns = [re.compile(mask.encode("utf-8")) for mask in masks]
    except re.error as error:
        raise BadInput(f"invalid --mask regex: {error}") from error
    declared_qualnames: set[str] = set()
    for qualname, module in moved_callables:
        if not module:
            raise BadInput(f"--moved-callable {qualname}= has an empty MODULE")
        if qualname in declared_qualnames:
            raise BadInput(f"--moved-callable {qualname} is declared more than once")
        declared_qualnames.add(qualname)
    files_a, files_b = _relative_files(dir_a), _relative_files(dir_b)
    if not files_a and not files_b:
        raise BadInput(f"neither {dir_a} nor {dir_b} holds any files")

    def masked(data: bytes) -> bytes:
        for pattern in patterns:
            data = pattern.sub(lambda _match: MASK_TOKEN, data)
        return data

    rewritten_qualnames: set[str] = set()
    rewritten_parser_files = {
        name: _rewrite_moved_callables((dir_a / name).read_bytes(), moved_callables, rewritten_qualnames)
        for name in files_a
        if name.endswith(PARSER_DUMP_SUFFIX)
    }
    findings = []
    for name in sorted(set(files_a) ^ set(files_b)):
        findings.append(f"capture-missing: {name} is present only in {dir_a if name in files_a else dir_b}")
    compared = sorted(set(files_a) & set(files_b))
    for name in compared:
        data_a = masked(rewritten_parser_files[name] if name in rewritten_parser_files else (dir_a / name).read_bytes())
        data_b = masked((dir_b / name).read_bytes())
        if data_a == data_b:
            continue
        findings.append(f"capture-bytes: {name} differs")
        diff = difflib.unified_diff(
            data_a.decode("utf-8", "replace").splitlines(),
            data_b.decode("utf-8", "replace").splitlines(),
            fromfile=f"{dir_a}/{name}",
            tofile=f"{dir_b}/{name}",
            lineterm="",
            n=1,
        )
        findings.extend(f"  {line}" for line in list(diff)[:DIFF_LINE_LIMIT])
    for qualname, module in moved_callables:
        if qualname not in rewritten_qualnames:
            unused_token = f"{SCRIPT_MODULE_LABEL}:{qualname}"
            findings.append(f"moved-callable-unused: {qualname}={module}: {unused_token} occurs in no .parser file of {dir_a}")
    return findings, len(compared)


# --- test-ids ----------------------------------------------------------------


def _testcase_id(classname: str, name: str) -> str:
    components = classname.split(".")
    module_index = max((index for index, part in enumerate(components) if part.startswith("test_")), default=None)
    if module_index is None:
        raise BadInput(f"testcase classname {classname!r} has no test_ component to key on")
    return "::".join([*components[module_index + 1 :], name])


def _under_prefix(classname: str, module_prefix: str) -> bool:
    """True when the classname is the prefix's module or lies below it, matching whole dotted segments."""
    return not module_prefix or classname == module_prefix or classname.startswith(module_prefix + ".")


def _read_testcases(xml_paths: Sequence[Path], module_prefix: str) -> dict[str, Counter[str]]:
    outcomes_by_id: dict[str, Counter[str]] = {}
    for path in xml_paths:
        try:
            root = ElementTree.parse(path).getroot()
        except (OSError, ElementTree.ParseError) as error:
            raise BadInput(f"cannot read junit XML {path}: {error}") from error
        for case in root.iter("testcase"):
            classname = case.get("classname", "")
            if not _under_prefix(classname, module_prefix):
                continue
            tags = sorted({child.tag for child in case if child.tag in OUTCOME_TAGS})
            outcome = "+".join(tags) or PASSED_OUTCOME
            outcomes_by_id.setdefault(_testcase_id(classname, case.get("name", "")), Counter())[outcome] += 1
    return outcomes_by_id


def _rename_id(test_id: str, renames: Sequence[tuple[str, str]]) -> str:
    for old, new in renames:
        if test_id == old or test_id.startswith((old + "::", old + "[")):
            return new + test_id[len(old) :]
    return test_id


def compare_test_ids(
    before_xmls: Sequence[Path],
    after_xmls: Sequence[Path],
    file_prefix: str = "",
    renames: Sequence[tuple[str, str]] = (),
    expect_new: Sequence[str] = (),
) -> tuple[list[str], int, int]:
    """Return (findings, before testcase count, after testcase count)."""
    module_prefix = file_prefix.replace("/", ".").rstrip(".").removesuffix(".py")
    before_raw = _read_testcases(before_xmls, module_prefix)
    after = _read_testcases(after_xmls, module_prefix)
    before: dict[str, Counter[str]] = {}
    for test_id, outcomes in before_raw.items():
        before.setdefault(_rename_id(test_id, renames), Counter()).update(outcomes)
    before_total = sum(sum(outcomes.values()) for outcomes in before.values())
    after_total = sum(sum(outcomes.values()) for outcomes in after.values())
    if not before_total and not after_total:
        raise BadInput("no testcases on either side" + (f" under --file-prefix {file_prefix}" if file_prefix else ""))
    findings = []
    for test_id in sorted(set(before) | set(after)):
        before_outcomes, after_outcomes = before.get(test_id, Counter()), after.get(test_id, Counter())
        shared = before_outcomes & after_outcomes
        before_left = sorted((before_outcomes - shared).elements())
        after_left = sorted((after_outcomes - shared).elements())
        for before_outcome, after_outcome in zip(before_left, after_left, strict=False):
            findings.append(f"id-outcome: {test_id}: before {before_outcome}, after {after_outcome}")
        for before_outcome in before_left[len(after_left) :]:
            findings.append(f"id-missing: {test_id} ({before_outcome}) is absent after the move")
        for after_outcome in after_left[len(before_left) :]:
            if test_id not in expect_new:
                findings.append(f"id-unexpected: {test_id} ({after_outcome}) is new after the move")
    # IDs drop the module, so a new test can share an ID with an existing one: a declared ID is new when its count grows.
    for test_id in expect_new:
        if test_id not in after:
            findings.append(f"expect-new-mismatch: {test_id} never appears after the move")
        elif sum(after[test_id].values()) <= sum(before.get(test_id, Counter()).values()):
            findings.append(f"expect-new-mismatch: {test_id} does not occur more often after the move than before")
    return findings, before_total, after_total


# --- command line ------------------------------------------------------------


def _run_nodes(args: argparse.Namespace) -> int:
    findings, compared = check_nodes(
        args.before, args.after, args.moved or [], _parse_key_value_pairs(args.rename or [], "--rename"), args.verbatim or []
    )
    return _emit(findings, f"compared-nodes: {compared}")


def _run_remainder(args: argparse.Namespace) -> int:
    findings, kept = check_remainder(
        args.before,
        args.after,
        args.removed or [],
        args.added or [],
        args.changed or [],
        args.import_added or [],
        args.import_removed or [],
    )
    return _emit(findings, f"remaining-statements: {kept}")


def _run_capture(args: argparse.Namespace) -> int:
    labels = capture(
        args.script,
        args.account,
        args.out,
        args.runs,
        args.all_help,
        _parse_key_value_pairs(args.env or [], "--env"),
        args.unset or [],
    )
    print(f"captured {len(labels)} run(s) into {args.out}")
    return EXIT_CLEAN


def _run_compare_captures(args: argparse.Namespace) -> int:
    findings, compared = compare_captures(
        args.dir_a, args.dir_b, args.mask or [], _parse_key_value_pairs(args.moved_callable or [], "--moved-callable")
    )
    return _emit(findings, f"compared-files: {compared}")


def _run_test_ids(args: argparse.Namespace) -> int:
    findings, before_total, after_total = compare_test_ids(
        args.before,
        args.after,
        args.file_prefix,
        _parse_key_value_pairs(args.rename or [], "--rename"),
        args.expect_new or [],
    )
    return _emit(findings, f"testcases: before {before_total}, after {after_total}")


def _add_list_option(parser: argparse.ArgumentParser, flag: str, metavar: str, help_text: str) -> None:
    parser.add_argument(flag, nargs="+", action="extend", metavar=metavar, help=help_text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="_move_fidelity.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    nodes = subcommands.add_parser("nodes", help="compare moved top-level nodes between files")
    nodes.add_argument("--before", required=True, type=Path, help="file the nodes moved out of")
    nodes.add_argument("--after", required=True, action="append", type=Path, help="file the nodes moved into; repeatable")
    _add_list_option(nodes, "--moved", "NAME", "top-level names that moved (required)")
    _add_list_option(nodes, "--rename", "OLD=NEW", "pre-move to post-move names; a NEW no node uses is rename-unused")
    _add_list_option(nodes, "--verbatim", "NAME", "moved names whose exact source lines must also match")
    nodes.set_defaults(handler=_run_nodes)

    remainder = subcommands.add_parser("remainder", help="compare what is left of a file after dropping declared nodes")
    remainder.add_argument("--before", required=True, type=Path)
    remainder.add_argument("--after", required=True, type=Path)
    _add_list_option(remainder, "--removed", "NAME", "names defined before and gone after; __doc__ is the module docstring")
    _add_list_option(remainder, "--added", "NAME", "names absent before and defined after")
    _add_list_option(remainder, "--changed", "NAME", "names defined on both sides whose bodies may differ")
    _add_list_option(remainder, "--import-added", "TRIPLE", "module:name[:asname], or the bare name for a plain import")
    _add_list_option(remainder, "--import-removed", "TRIPLE", "same shape as --import-added")
    remainder.set_defaults(handler=_run_remainder)

    capture_parser = subcommands.add_parser("capture", help="record a script's output under a pinned environment")
    capture_parser.add_argument("--script", required=True, type=Path)
    capture_parser.add_argument("--account", required=True, type=Path, help="existing directory used as CLAUDE_CONFIG_DIR")
    capture_parser.add_argument("--out", required=True, type=Path, help="directory to create; an existing one is refused")
    capture_parser.add_argument("--runs", type=Path, help="one shlex-split argv per line; blank and # lines are skipped")
    capture_parser.add_argument(
        "--all-help",
        action="store_true",
        help="capture --help, and a dump of a fixed subset of parser attributes, for the top level and every subcommand",
    )
    _add_list_option(capture_parser, "--env", "NAME=VALUE", "override a pinned or ambient variable")
    _add_list_option(capture_parser, "--unset", "NAME", "remove a variable; applied after --env")
    capture_parser.set_defaults(handler=_run_capture)

    compare = subcommands.add_parser("compare-captures", help="compare two capture directories byte for byte")
    compare.add_argument("dir_a", type=Path)
    compare.add_argument("dir_b", type=Path)
    _add_list_option(compare, "--mask", "REGEX", "replace each match with a fixed token on both sides before comparing")
    _add_list_option(
        compare,
        "--moved-callable",
        "QUALNAME=MODULE",
        "rewrite DIR_A's <script>:QUALNAME to MODULE:QUALNAME in .parser files, for <callable ...> tokens only; "
        "one that rewrites nothing is moved-callable-unused",
    )
    compare.set_defaults(handler=_run_compare_captures)

    test_ids = subcommands.add_parser("test-ids", help="compare per-test outcomes across junit XML runs")
    test_ids.add_argument("--before", required=True, action="append", type=Path, help="junit XML; repeatable, unioned")
    test_ids.add_argument("--after", required=True, action="append", type=Path, help="junit XML; repeatable, unioned")
    test_ids.add_argument(
        "--file-prefix",
        default="",
        help="keep only testcases under this path, relative to pytest's rootdir, e.g. claude/.claude/scripts/tests/",
    )
    _add_list_option(test_ids, "--rename", "OLD=NEW", "rename a before ID, its leading ::-part, or its [param] stem")
    _add_list_option(
        test_ids,
        "--expect-new",
        "ID",
        "IDs that must occur more often after than before; a listed ID that does not is an expect-new-mismatch",
    )
    test_ids.set_defaults(handler=_run_test_ids)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (BadInput, OSError) as error:
        print(f"bad-input: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())
