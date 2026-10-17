"""Seeded-defect self-test for tests/_move_fidelity.py's parser dump (capture --all-help's .parser files)
and compare-captures --moved-callable.

Every check is proved by a synthetic script or capture directory written under tmp_path:
a clean case yields no findings, and one seeded defect per check yields exactly the finding that names it.
The other subcommands' seeds live in test_transcript_move_fidelity.py and test_transcript_move_fidelity_test_ids.py.
"""
from __future__ import annotations

import functools
import sys
import textwrap
from pathlib import Path

import pytest

from ._move_fidelity import capture, main


def _invoke(capsys, *argv) -> tuple[int, list[str], str]:
    code = main([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def _checks(lines: list[str]) -> list[str]:
    """Check names of the finding lines; the last output line is always the summary, and diff lines are indented."""
    return [line.split(":", 1)[0] for line in lines[:-1] if not line.startswith(" ")]


def _write(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(source))
    return path


def _write_capture(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir()
    for name, text in files.items():
        (directory / name).write_text(text)
    return directory


# --- synthetic scripts -------------------------------------------------------

SCRIPT_TEMPLATE = """\
import argparse
{imports}

{definitions}

def build_parser():
    parser = argparse.ArgumentParser(prog="synthetic")
    subcommands = parser.add_subparsers(dest="command")
{registrations}
    return parser


if __name__ == "__main__":
    build_parser().parse_args()
"""
TWO_HANDLERS = "def handler_one(args):\n    return 1\n\n\ndef handler_two(args):\n    return 2\n"


def _synthetic_script(registrations: str, definitions: str = "", imports: str = "") -> str:
    return SCRIPT_TEMPLATE.format(
        imports=textwrap.dedent(imports),
        definitions=textwrap.dedent(definitions),
        registrations=textwrap.indent(textwrap.dedent(registrations), "    "),
    )


def _alpha(statement: str = "", options: str = "") -> str:
    return f'alpha = subcommands.add_parser("alpha"{options})\n{statement}\n'


def _capture_all_help(directory: Path, script_source: str, sibling_modules: dict[str, str] | None = None) -> Path:
    """Capture the script's help into `directory/capture`; sibling modules sit beside it and leave sys.modules afterward."""
    directory.mkdir(parents=True)
    (directory / "account").mkdir()
    for module_name, source in (sibling_modules or {}).items():
        _write(directory, f"{module_name}.py", source)
    script = _write(directory, "cli.py", script_source)
    try:
        capture(script, directory / "account", directory / "capture", all_help=True)
    finally:
        for module_name in sibling_modules or {}:
            sys.modules.pop(module_name, None)
    return directory / "capture"


def _parser_text(capture_dir: Path, label: str) -> str:
    return (capture_dir / f"{label}.parser").read_text()


TWO_SUBCOMMANDS = _synthetic_script(
    _alpha('alpha.add_argument("--count", type=int, default=3)') + 'subcommands.add_parser("beta")\n'
)


def test_capturing_the_same_script_twice_compares_clean_and_writes_a_parser_file_per_help_label_only(capsys, tmp_path):
    runs = _write(tmp_path, "runs.txt", "alpha --count 2\n")
    first, second = tmp_path / "first", tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()
        (directory / "account").mkdir()
        script = _write(directory, "cli.py", TWO_SUBCOMMANDS)
        capture(script, directory / "account", directory / "capture", runs, all_help=True)

    code, lines, _ = _invoke(capsys, "compare-captures", first / "capture", second / "capture")

    assert code == 0
    assert _checks(lines) == []
    assert sorted(path.name for path in (first / "capture").glob("*.parser")) == [
        "help-alpha.parser",
        "help-beta.parser",
        "help-top.parser",
    ]
    assert (first / "capture" / "run-01.stdout").is_file()


def test_capture_without_all_help_writes_no_parser_file(tmp_path):
    directory = tmp_path / "runs-only"
    directory.mkdir()
    (directory / "account").mkdir()
    script = _write(directory, "cli.py", TWO_SUBCOMMANDS)
    runs = _write(tmp_path, "runs.txt", "alpha\n")

    capture(script, directory / "account", directory / "capture", runs)

    assert list((directory / "capture").glob("*.parser")) == []


def test_a_subcommand_named_top_exits_2_because_its_label_collides_with_the_top_level_dump(capsys, tmp_path):
    (tmp_path / "account").mkdir()
    script = _write(tmp_path, "cli.py", _synthetic_script('subcommands.add_parser("top")\n'))

    code, lines, err = _invoke(
        capsys, "capture", "--script", script, "--account", tmp_path / "account", "--out", tmp_path / "capture", "--all-help"
    )

    assert (code, lines) == (2, [])
    assert err == "bad-input: subcommand name 'top' collides with the top-level capture label help-top\n"
    assert not (tmp_path / "capture").exists()


# --- golden text -------------------------------------------------------------

GOLDEN_DEFINITIONS = "def run_golden(args):\n    return 0\n"
GOLDEN_REGISTRATIONS = """\
subcommands.add_parser("zeta")
golden = subcommands.add_parser("golden")
golden.add_argument(
    "--level", dest="verbosity", type=int, default=7, choices=[1, 5, 9], metavar="LEVEL", nargs="?", const=4, required=True
)
speed = golden.add_mutually_exclusive_group(required=True)
speed.add_argument("--zip", action="store_true")
speed.add_argument("--zap", action="store_true")
noise = golden.add_mutually_exclusive_group()
noise.add_argument("--quiet", action="store_true")
noise.add_argument("--loud", action="store_true")
golden.set_defaults(func=run_golden)
golden.set_defaults(alpha_key=1)
subcommands.add_parser("alpha")
subcommands.add_parser("strict", allow_abbrev=False, argument_default=argparse.SUPPRESS, prefix_chars="-+", exit_on_error=False)
"""
HELP_ACTION = (
    "action <callable argparse:_HelpAction> option_strings=['-h', '--help'] dest='help' nargs=0 const=None "
    "default='==SUPPRESS==' type=None choices=None required=False"
)
DEFAULT_PARSER_LINE = "parser allow_abbrev=True argument_default=None prefix_chars='-' exit_on_error=True"
STORE_TRUE_ACTION = (
    "action <callable argparse:_StoreTrueAction> option_strings=['{flag}'] dest='{dest}' nargs=0 const=True "
    "default=False type=None choices=None required=False"
)
GOLDEN_SUBPARSER_TEXT = "\n".join(
    [
        DEFAULT_PARSER_LINE,
        HELP_ACTION,
        "action <callable argparse:_StoreAction> option_strings=['--level'] dest='verbosity' nargs='?' const=4 default=7 "
        "type=<callable builtins:int> choices=[1, 5, 9] required=True",
        STORE_TRUE_ACTION.format(flag="--zip", dest="zip"),
        STORE_TRUE_ACTION.format(flag="--zap", dest="zap"),
        STORE_TRUE_ACTION.format(flag="--quiet", dest="quiet"),
        STORE_TRUE_ACTION.format(flag="--loud", dest="loud"),
        "mutex required=True dests=['zip', 'zap']",
        "mutex required=False dests=['quiet', 'loud']",
        "default alpha_key=1",
        "default func=<callable <script>:run_golden>",
        "",
    ]
)
GOLDEN_TOP_TEXT = "\n".join(
    [
        DEFAULT_PARSER_LINE,
        HELP_ACTION,
        "action <callable argparse:_SubParsersAction> option_strings=[] dest='command' nargs='A...' const=None default=None "
        "type=None choices=['zeta', 'golden', 'alpha', 'strict'] required=False",
        "",
    ]
)


@pytest.fixture
def golden_capture(tmp_path):
    return _capture_all_help(tmp_path / "golden", _synthetic_script(GOLDEN_REGISTRATIONS, GOLDEN_DEFINITIONS))


def test_top_level_dump_pins_the_subparsers_choices_as_names_in_registration_order(golden_capture):
    assert _parser_text(golden_capture, "help-top") == GOLDEN_TOP_TEXT


def test_subparser_dump_pins_every_line_shape_in_order(golden_capture):
    assert _parser_text(golden_capture, "help-golden") == GOLDEN_SUBPARSER_TEXT


def test_subparser_dump_pins_the_four_parser_attributes(golden_capture):
    first_line = _parser_text(golden_capture, "help-strict").splitlines()[0]

    assert first_line == "parser allow_abbrev=False argument_default='==SUPPRESS==' prefix_chars='-+' exit_on_error=False"


# --- help-invisible mutations ------------------------------------------------


@pytest.mark.parametrize(
    ("before_registration", "after_registration"),
    [
        pytest.param(
            _alpha('alpha.add_argument("--n", default=1)'), _alpha('alpha.add_argument("--n", default=2)'), id="default"
        ),
        pytest.param(_alpha('alpha.add_argument("--n", type=int)'), _alpha('alpha.add_argument("--n", type=float)'), id="type"),
        pytest.param(
            _alpha('alpha.add_argument("--n", dest="first", metavar="N")'),
            _alpha('alpha.add_argument("--n", dest="second", metavar="N")'),
            id="dest-behind-a-metavar",
        ),
        pytest.param(
            _alpha('alpha.add_argument("--n", nargs="?", const=1, metavar="N")'),
            _alpha('alpha.add_argument("--n", nargs="?", const=2, metavar="N")'),
            id="const",
        ),
        pytest.param(
            _alpha('alpha.add_argument("--n", choices=["a", "b"], metavar="N")'),
            _alpha('alpha.add_argument("--n", choices=["a", "c"], metavar="N")'),
            id="choices-behind-a-metavar",
        ),
        pytest.param(_alpha(options=", allow_abbrev=True"), _alpha(options=", allow_abbrev=False"), id="allow-abbrev"),
        pytest.param(
            _alpha("alpha.set_defaults(func=handler_one)"),
            _alpha("alpha.set_defaults(func=handler_two)"),
            id="set-defaults-handler",
        ),
    ],
)
def test_a_change_help_cannot_show_is_a_capture_bytes_finding_on_that_subcommands_parser_file_only(
    capsys, tmp_path, before_registration, after_registration
):
    before = _capture_all_help(tmp_path / "before", _synthetic_script(before_registration, TWO_HANDLERS))
    after = _capture_all_help(tmp_path / "after", _synthetic_script(after_registration, TWO_HANDLERS))

    code, lines, _ = _invoke(capsys, "compare-captures", before, after)

    assert (code, _checks(lines)) == (1, ["capture-bytes"])
    assert lines[0] == "capture-bytes: help-alpha.parser differs"
    assert (before / "help-alpha.stdout").read_bytes() == (after / "help-alpha.stdout").read_bytes()


def test_swapping_two_subcommands_registration_order_is_a_capture_bytes_finding_on_the_top_level_parser_file(capsys, tmp_path):
    alpha_then_beta = 'subcommands.add_parser("alpha")\nsubcommands.add_parser("beta")\n'
    beta_then_alpha = 'subcommands.add_parser("beta")\nsubcommands.add_parser("alpha")\n'
    before = _capture_all_help(tmp_path / "before", _synthetic_script(alpha_then_beta))
    after = _capture_all_help(tmp_path / "after", _synthetic_script(beta_then_alpha))

    code, lines, _ = _invoke(capsys, "compare-captures", before, after)

    capture_bytes_lines = [line for line in lines if line.startswith("capture-bytes")]
    assert code == 1
    assert capture_bytes_lines == ["capture-bytes: help-top.parser differs", "capture-bytes: help-top.stdout differs"]


def test_parser_dump_reads_the_ambient_environment_while_the_captured_help_reads_the_pinned_one(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "ambient-config-dir")
    registrations = _alpha('alpha.add_argument("--cfg", default=os.environ["CLAUDE_CONFIG_DIR"], help="cfg %(default)s")')

    out = _capture_all_help(tmp_path / "ambient", _synthetic_script(registrations, imports="import os"))

    help_without_wrapping = "".join((out / "help-alpha.stdout").read_text().split())
    assert "default='ambient-config-dir'" in _parser_text(out, "help-alpha")
    assert "ambient-config-dir" not in help_without_wrapping
    assert str((tmp_path / "ambient" / "account").resolve()) in help_without_wrapping


# --- value rendering ---------------------------------------------------------

X_ACTION_LINE = (
    "action <callable argparse:_StoreAction> option_strings=['--x'] dest='x' nargs=None const=None "
    "default={default} type={type} choices=None required=False"
)


def _x_action_line(directory: Path, argument_kwargs: str, imports: str = "", definitions: str = "") -> str:
    registrations = _alpha(f'alpha.add_argument("--x", {argument_kwargs})')
    out = _capture_all_help(directory, _synthetic_script(registrations, definitions, imports))
    return next(line for line in _parser_text(out, "help-alpha").splitlines() if "option_strings=['--x']" in line)


@pytest.mark.parametrize(
    ("argument_kwargs", "imports", "default_text", "type_text"),
    [
        pytest.param("default=(1, 2)", "", "(1, 2)", "None", id="tuple"),
        pytest.param("default=(1,)", "", "(1,)", "None", id="one-item-tuple"),
        pytest.param('default={"b": 1, "a": [2]}', "", "{'b': 1, 'a': [2]}", "None", id="dict-in-insertion-order"),
        pytest.param("type=int", "", "None", "<callable builtins:int>", id="builtin-type-as-a-callable"),
        pytest.param(
            'default=Decimal("1.5")', "from decimal import Decimal", "Decimal('1.5')", "None", id="plain-repr-without-an-address"
        ),
    ],
)
def test_value_rendering_follows_the_specified_rule_for_each_value_shape(
    tmp_path, argument_kwargs, imports, default_text, type_text
):
    line = _x_action_line(tmp_path / "render", argument_kwargs, imports)

    assert line == X_ACTION_LINE.format(default=default_text, type=type_text)


def test_a_frozenset_default_renders_its_items_sorted_as_strings(tmp_path):
    small_ints = frozenset({10, 9, 2})
    assert list(small_ints) != [10, 2, 9], "precondition: iteration order must differ from the as-string sort"

    line = _x_action_line(tmp_path / "render", "default=frozenset({10, 9, 2})")

    assert line == X_ACTION_LINE.format(default="frozenset{10, 2, 9}", type="None")


def test_an_object_default_renders_as_its_type_and_the_file_holds_no_memory_address(tmp_path):
    line = _x_action_line(tmp_path / "render", "default=object()")

    assert line == X_ACTION_LINE.format(default="<instance builtins:object>", type="None")
    assert "0x" not in line


def test_a_callable_without_a_qualname_renders_as_its_own_repr(tmp_path):
    partial_repr = repr(functools.partial(int, base=16))
    assert " at 0x" not in partial_repr, "precondition: the repr must carry no address"

    line = _x_action_line(tmp_path / "render", "type=functools.partial(int, base=16)", "import functools")

    assert line == X_ACTION_LINE.format(default="None", type=partial_repr)


def test_a_handler_imported_from_a_module_beside_the_script_renders_with_that_modules_name(tmp_path):
    handler_module_name = "move_fidelity_render_handler_module"
    registrations = _alpha("alpha.set_defaults(func=handler)")

    out = _capture_all_help(
        tmp_path / "render",
        _synthetic_script(registrations, imports=f"from {handler_module_name} import handler"),
        {handler_module_name: "def handler(args):\n    return 0\n"},
    )

    assert f"default func=<callable {handler_module_name}:handler>\n" in _parser_text(out, "help-alpha")


def test_two_captures_of_scripts_importing_a_same_named_sibling_module_each_dump_their_own_siblings_value(tmp_path):
    sibling_module_name = "move_fidelity_same_named_sibling_defaults"
    script_source = _synthetic_script(
        _alpha('alpha.add_argument("--n", default=sibling_defaults_value)'),
        imports=f"from {sibling_module_name} import VALUE as sibling_defaults_value",
    )
    capture_dirs = []
    try:
        # Not _capture_all_help: its own sys.modules cleanup would hide the leak this test guards.
        for label, sibling_value in (("first", 1), ("second", 2)):
            directory = tmp_path / label
            directory.mkdir()
            (directory / "account").mkdir()
            _write(directory, f"{sibling_module_name}.py", f"VALUE = {sibling_value}\n")
            script = _write(directory, "cli.py", script_source)
            capture(script, directory / "account", directory / "capture", all_help=True)
            capture_dirs.append(directory / "capture")
    finally:
        sys.modules.pop(sibling_module_name, None)

    first_action_line, second_action_line = (
        next(line for line in _parser_text(capture_dir, "help-alpha").splitlines() if "option_strings=['--n']" in line)
        for capture_dir in capture_dirs
    )
    assert " default=1 " in first_action_line
    assert " default=2 " in second_action_line


# --- compare-captures --moved-callable ---------------------------------------

SCRIPT_HANDLER_PARSER = "default func=<callable <script>:cmd_a>\n"
MOVED_HANDLER_PARSER = "default func=<callable pkg.mod:cmd_a>\n"


def _handler_captures(tmp_path, before_files: dict[str, str], after_files: dict[str, str]) -> tuple[Path, Path]:
    return _write_capture(tmp_path / "a", before_files), _write_capture(tmp_path / "b", after_files)


@pytest.mark.parametrize(
    ("options", "expected_checks"),
    [
        pytest.param((), ["capture-bytes"], id="undeclared"),
        pytest.param(("--moved-callable", "cmd_a=pkg.mod"), [], id="declared-with-the-right-module"),
        pytest.param(("--moved-callable", "cmd_a=pkg.other"), ["capture-bytes"], id="declared-with-the-wrong-module"),
    ],
)
def test_a_handler_moved_off_the_script_is_clean_only_under_the_matching_declaration(
    capsys, tmp_path, options, expected_checks
):
    before, after = _handler_captures(tmp_path, {"help-x.parser": SCRIPT_HANDLER_PARSER}, {"help-x.parser": MOVED_HANDLER_PARSER})

    code, lines, _ = _invoke(capsys, "compare-captures", before, after, *options)

    assert (code, _checks(lines)) == (1 if expected_checks else 0, expected_checks)


def test_a_declaration_whose_after_side_still_shows_the_script_binding_is_a_capture_bytes_finding(capsys, tmp_path):
    unchanged = {"help-x.parser": SCRIPT_HANDLER_PARSER}
    before, after = _handler_captures(tmp_path, unchanged, unchanged)

    code, lines, _ = _invoke(capsys, "compare-captures", before, after, "--moved-callable", "cmd_a=pkg.mod")

    assert (code, _checks(lines)) == (1, ["capture-bytes"])
    assert lines[0] == "capture-bytes: help-x.parser differs"


def test_a_declared_qualname_that_appears_nowhere_is_a_moved_callable_unused_finding_naming_it(capsys, tmp_path):
    before, after = _handler_captures(tmp_path, {"help-x.parser": MOVED_HANDLER_PARSER}, {"help-x.parser": MOVED_HANDLER_PARSER})

    code, lines, _ = _invoke(capsys, "compare-captures", before, after, "--moved-callable", "ghost=pkg.mod")

    assert (code, _checks(lines)) == (1, ["moved-callable-unused"])
    assert lines[0].startswith("moved-callable-unused: ghost=pkg.mod: <script>:ghost ")


def test_one_used_and_one_unused_declaration_give_exactly_one_finding_naming_the_unused_qualname(capsys, tmp_path):
    before, after = _handler_captures(tmp_path, {"help-x.parser": SCRIPT_HANDLER_PARSER}, {"help-x.parser": MOVED_HANDLER_PARSER})

    code, lines, _ = _invoke(
        capsys, "compare-captures", before, after, "--moved-callable", "cmd_a=pkg.mod", "ghost=pkg.mod"
    )

    assert (code, _checks(lines)) == (1, ["moved-callable-unused"])
    assert lines[0].startswith("moved-callable-unused: ghost=pkg.mod: ")


def test_declaring_a_qualname_leaves_a_longer_qualname_it_prefixes_unrewritten(capsys, tmp_path):
    before_text = SCRIPT_HANDLER_PARSER + "default other=<callable <script>:cmd_ab>\n"
    after_text = MOVED_HANDLER_PARSER + "default other=<callable <script>:cmd_ab>\n"
    before, after = _handler_captures(tmp_path, {"help-x.parser": before_text}, {"help-x.parser": after_text})

    code, lines, _ = _invoke(capsys, "compare-captures", before, after, "--moved-callable", "cmd_a=pkg.mod")

    assert (code, lines) == (0, ["compared-files: 1"])


def test_the_same_token_inside_a_stdout_file_is_not_rewritten(capsys, tmp_path):
    before, after = _handler_captures(
        tmp_path,
        {"help-x.parser": SCRIPT_HANDLER_PARSER, "help-x.stdout": SCRIPT_HANDLER_PARSER},
        {"help-x.parser": MOVED_HANDLER_PARSER, "help-x.stdout": MOVED_HANDLER_PARSER},
    )

    code, lines, _ = _invoke(capsys, "compare-captures", before, after, "--moved-callable", "cmd_a=pkg.mod")

    assert (code, lines[0]) == (1, "capture-bytes: help-x.stdout differs")
    assert _checks(lines) == ["capture-bytes"]


@pytest.mark.parametrize(
    ("declarations", "reason"),
    [
        pytest.param(("cmd_a",), "--moved-callable expects NAME=VALUE", id="no-equals-sign"),
        pytest.param(("=pkg.mod",), "--moved-callable expects NAME=VALUE", id="empty-qualname"),
        pytest.param(("cmd_a=",), "--moved-callable cmd_a= has an empty MODULE", id="empty-module"),
        pytest.param(
            ("cmd_a=pkg.mod", "cmd_a=pkg.other"), "--moved-callable cmd_a is declared more than once", id="repeated-qualname"
        ),
    ],
)
def test_a_malformed_moved_callable_declaration_exits_2_with_bad_input(capsys, tmp_path, declarations, reason):
    before, after = _handler_captures(tmp_path, {"help-x.parser": SCRIPT_HANDLER_PARSER}, {"help-x.parser": MOVED_HANDLER_PARSER})

    code, lines, err = _invoke(capsys, "compare-captures", before, after, "--moved-callable", *declarations)

    assert (code, lines) == (2, [])
    assert err.startswith("bad-input: ")
    assert reason in err


def test_a_handler_relocated_to_a_sibling_module_compares_clean_only_under_the_declaration(capsys, tmp_path):
    relocated_module_name = "move_fidelity_relocated_handler_module"
    handler_source = "def handler(args):\n    return 0\n"
    before_script = _synthetic_script(_alpha("alpha.set_defaults(func=handler)"), handler_source)
    after_script = _synthetic_script(
        _alpha("alpha.set_defaults(func=handler)"), imports=f"from {relocated_module_name} import handler"
    )
    before = _capture_all_help(tmp_path / "before", before_script)
    after = _capture_all_help(tmp_path / "after", after_script, {relocated_module_name: handler_source})

    declared_code, declared_lines, _ = _invoke(
        capsys, "compare-captures", before, after, "--moved-callable", f"handler={relocated_module_name}"
    )
    undeclared_code, undeclared_lines, _ = _invoke(capsys, "compare-captures", before, after)

    assert (declared_code, declared_lines[:-1]) == (0, [])
    assert undeclared_code == 1
    assert [line for line in undeclared_lines if line.startswith("capture-bytes")] == ["capture-bytes: help-alpha.parser differs"]
