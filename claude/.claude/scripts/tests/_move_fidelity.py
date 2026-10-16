"""Check that a code move between files changed only what was declared.

Run as `_move_fidelity.py <subcommand>`, or import the check functions. One usage line per subcommand:

  nodes --before FILE --after FILE... --moved NAME... [--rename OLD=NEW...] [--verbatim NAME...]
  remainder --before FILE --after FILE [--removed|--added|--changed NAME...] [--import-added|--import-removed TRIPLE...]
  capture --script FILE --account DIR --out DIR [--runs FILE] [--all-help] [--env NAME=VALUE...] [--unset NAME...]
  compare-captures DIR_A DIR_B [--mask REGEX...]
  test-ids --before XML... --after XML... [--file-prefix PATH] [--rename OLD=NEW...] [--expect-new ID...]

Each finding prints as one line prefixed with its check name. Exit status is 0 when clean, 1 on any finding,
2 on bad input. The module imports only the standard library and uses no relative imports.

`nodes --rename` reverses each NEW name to its OLD by a textual match over the node's source, so it also rewrites a NEW name
that occurs inside a string literal or a comment.
`capture --all-help` captures rendered help only, so it cannot see an argparse default, type, or dest that the help output
does not display. `remainder` drops every top-level import statement, with any comment on its lines, before comparing,
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


def _comment_tokens(source: str) -> list[tuple[int, str]]:
    try:
        return [
            (token.start[0], token.string)
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError) as error:
        raise BadInput(f"cannot tokenize source: {error}") from error


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


def _contains_name(text: str, name: str) -> bool:
    pattern = _name_boundary_pattern([name])
    return pattern is not None and pattern.search(text) is not None


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
        match.group(0) for text in compared_after_texts for match in (reverse_pattern.finditer(text) if reverse_pattern else ())
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
        if _contains_name(before_text, new_name):
            findings.append(f"rename-ambiguous: {name}: {new_name} already occurs in the original node")
            return findings
    reversed_text = reverse_pattern.sub(lambda match: reverse_map[match.group(0)], after_text) if reverse_pattern else after_text
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


def subcommand_names(script: Path) -> list[str]:
    """Registered subcommand names, in registration order, read from the script's `build_parser()`."""
    original_path = list(sys.path)
    sys.path.insert(0, str(script.parent))
    try:
        spec = importlib.util.spec_from_file_location(CAPTURE_TARGET_MODULE_NAME, script)
        if spec is None or spec.loader is None:
            raise BadInput(f"cannot load {script} as a module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parser = module.build_parser()
    except BadInput:
        raise
    except (Exception, SystemExit) as error:
        raise BadInput(f"cannot read subcommands from {script}: {error!r}") from error
    finally:
        sys.path[:] = original_path
    names: list[str] = []
    seen_parsers: set[int] = set()
    # argparse exposes no public accessor for registered subcommands.
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            if id(subparser) not in seen_parsers:
                seen_parsers.add(id(subparser))
                names.append(name)
    return names


def capture(
    script: Path,
    account: Path,
    out: Path,
    runs_file: Path | None = None,
    all_help: bool = False,
    env_overrides: Sequence[tuple[str, str]] = (),
    unsets: Sequence[str] = (),
) -> list[str]:
    """Run the script once per planned argv and write `<label>.argv/.stdout/.stderr/.exit` under `out`; return the labels."""
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
    if all_help:
        planned.append(("help-top", ["--help"]))
        for name in subcommand_names(script):
            if not re.fullmatch(r"[\w.-]+", name):
                raise BadInput(f"subcommand name {name!r} cannot be a capture label")
            planned.append((f"help-{name}", [name, "--help"]))
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
    return [label for label, _ in planned]


def _relative_files(directory: Path) -> list[str]:
    if not directory.is_dir():
        raise BadInput(f"{directory} is not a directory")
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file())


def compare_captures(dir_a: Path, dir_b: Path, masks: Sequence[str] = ()) -> tuple[list[str], int]:
    """Return (findings, compared-file count) comparing every file byte for byte after masking."""
    try:
        patterns = [re.compile(mask.encode("utf-8")) for mask in masks]
    except re.error as error:
        raise BadInput(f"invalid --mask regex: {error}") from error
    files_a, files_b = _relative_files(dir_a), _relative_files(dir_b)
    if not files_a and not files_b:
        raise BadInput(f"neither {dir_a} nor {dir_b} holds any files")

    def masked(directory: Path, name: str) -> bytes:
        data = (directory / name).read_bytes()
        for pattern in patterns:
            data = pattern.sub(lambda _match: MASK_TOKEN, data)
        return data

    findings = []
    for name in sorted(set(files_a) ^ set(files_b)):
        findings.append(f"capture-missing: {name} is present only in {dir_a if name in files_a else dir_b}")
    compared = sorted(set(files_a) & set(files_b))
    for name in compared:
        data_a, data_b = masked(dir_a, name), masked(dir_b, name)
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
    findings, compared = compare_captures(args.dir_a, args.dir_b, args.mask or [])
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
    capture_parser.add_argument("--all-help", action="store_true", help="capture --help for the top level and every subcommand")
    _add_list_option(capture_parser, "--env", "NAME=VALUE", "override a pinned or ambient variable")
    _add_list_option(capture_parser, "--unset", "NAME", "remove a variable; applied after --env")
    capture_parser.set_defaults(handler=_run_capture)

    compare = subcommands.add_parser("compare-captures", help="compare two capture directories byte for byte")
    compare.add_argument("dir_a", type=Path)
    compare.add_argument("dir_b", type=Path)
    _add_list_option(compare, "--mask", "REGEX", "replace each match with a fixed token on both sides before comparing")
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
    _add_list_option(test_ids, "--expect-new", "ID", "IDs that must appear only after; others are an expect-new-mismatch")
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
