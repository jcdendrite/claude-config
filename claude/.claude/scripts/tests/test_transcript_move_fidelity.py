"""Seeded-defect self-test for tests/_move_fidelity.py.

Every check the helper reports is proved by a synthetic source, junit XML string, or script written under tmp_path:
a clean case yields no findings, and one seeded defect per check yields exactly the finding that names it.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from ._move_fidelity import CAPTURE_TARGET_MODULE_NAME, capture, main

HELPER_PATH = Path(__file__).with_name("_move_fidelity.py")


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


# --- nodes -------------------------------------------------------------------

MOVED_BEFORE = '''\
@pytest.mark.parametrize("value", [1, 2])
def moved_fn(value):
    total = helper(value)
    assert total, "needs a total"
    count = len(str(value))
    # explain the doubling
    return total * 2
'''
MOVED_AFTER = MOVED_BEFORE.replace("helper(", "util.helper(")
RENAME_HELPER = ("--rename", "helper=util.helper")


def _run_nodes_with_stderr(capsys, tmp_path, before_source, after_sources, *options, moved=("moved_fn",)):
    before = _write(tmp_path, "before.py", before_source)
    argv = ["nodes", "--before", before]
    for number, source in enumerate(after_sources):
        argv += ["--after", _write(tmp_path, f"after{number}.py", source)]
    return _invoke(capsys, *argv, "--moved", *moved, *options)


def _run_nodes(capsys, tmp_path, before_source, after_sources, *options, moved=("moved_fn",)):
    code, lines, _ = _run_nodes_with_stderr(capsys, tmp_path, before_source, after_sources, *options, moved=moved)
    return code, lines


def test_nodes_clean_move_with_one_rename_and_one_decorator_has_no_findings(capsys, tmp_path):
    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [MOVED_AFTER], *RENAME_HELPER)

    assert code == 0
    assert lines == ["compared-nodes: 1"]


@pytest.mark.parametrize(
    ("after_source", "rename_options"),
    [
        pytest.param(MOVED_AFTER.replace("    count = len(str(value))\n", ""), RENAME_HELPER, id="deleted-statement"),
        pytest.param(MOVED_AFTER.replace('    assert total, "needs a total"\n', ""), RENAME_HELPER, id="deleted-assert"),
        pytest.param(MOVED_AFTER.replace("needs a total", "needs a sum"), RENAME_HELPER, id="changed-string-literal"),
        pytest.param(
            MOVED_AFTER.replace('@pytest.mark.parametrize("value", [1, 2])\n', ""),
            RENAME_HELPER,
            id="removed-parametrize-decorator",
        ),
        pytest.param(MOVED_AFTER, ("--rename", "other=util.other"), id="rename-missing-from-the-map"),
    ],
)
def test_nodes_flags_a_changed_node_as_node_ast(capsys, tmp_path, after_source, rename_options):
    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [after_source], *rename_options)

    assert code == 1
    assert [check for check in _checks(lines) if check != "rename-unused"] == ["node-ast"]


def test_nodes_flags_an_extra_method_in_a_moved_class_as_node_ast(capsys, tmp_path):
    class_before = 'class MovedHelper:\n    """Holds a value."""\n\n    def get(self):\n        return 1\n'
    class_after_with_extra_method = class_before + "\n    def extra(self):\n        return 2\n"

    clean_code, clean_lines = _run_nodes(capsys, tmp_path, class_before, [class_before], moved=("MovedHelper",))
    code, lines = _run_nodes(capsys, tmp_path, class_before, [class_after_with_extra_method], moved=("MovedHelper",))

    assert (clean_code, clean_lines) == (0, ["compared-nodes: 1"])
    assert (code, _checks(lines)) == (1, ["node-ast"])


def test_nodes_flags_a_changed_comment_inside_a_moved_node_as_node_comments_only(capsys, tmp_path):
    changed_comment = MOVED_AFTER.replace("explain the doubling", "explain doubling")

    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [changed_comment], *RENAME_HELPER)

    assert (code, _checks(lines)) == (1, ["node-comments"])


def test_nodes_flags_a_dropped_node_as_node_missing_and_does_not_count_it(capsys, tmp_path):
    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, ["unrelated = 1\n"])

    assert (code, _checks(lines), lines[-1]) == (1, ["node-missing"], "compared-nodes: 0")


def test_nodes_flags_a_name_absent_from_before_as_node_missing(capsys, tmp_path):
    code, lines = _run_nodes(capsys, tmp_path, "unrelated = 1\n", [MOVED_BEFORE])

    assert (code, _checks(lines)) == (1, ["node-missing"])


def test_nodes_flags_a_node_defined_in_two_after_files_as_node_duplicate(capsys, tmp_path):
    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [MOVED_BEFORE, MOVED_BEFORE])

    assert (code, _checks(lines)) == (1, ["node-duplicate"])


def test_nodes_flags_a_whitespace_only_change_in_a_verbatim_node_as_verbatim_without_node_ast(capsys, tmp_path):
    spaced = MOVED_BEFORE.replace("total = helper(value)", "total  =  helper(value)")

    unchanged_code, unchanged_lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [MOVED_BEFORE], "--verbatim", "moved_fn")
    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [spaced], "--verbatim", "moved_fn")

    assert (unchanged_code, unchanged_lines) == (0, ["compared-nodes: 1"])
    assert (code, _checks(lines)) == (1, ["verbatim"])


def test_nodes_flags_a_new_name_already_in_the_before_node_as_rename_ambiguous(capsys, tmp_path):
    already_qualified = MOVED_BEFORE.replace("helper(", "util.helper(")

    code, lines = _run_nodes(capsys, tmp_path, already_qualified, [already_qualified], *RENAME_HELPER)

    assert (code, _checks(lines)) == (1, ["rename-ambiguous"])


def test_nodes_does_not_reverse_a_new_name_that_only_occurs_inside_a_longer_identifier(capsys, tmp_path):
    before = "def moved_fn(value):\n    return helper_extra(value)\n"
    after = "def moved_fn(value):\n    return util.helper_extra(value)\n"

    code, lines = _run_nodes(capsys, tmp_path, before, [after], *RENAME_HELPER)

    assert (code, _checks(lines)) == (1, ["node-ast", "rename-unused"])


def test_nodes_does_not_reverse_a_new_name_reached_through_attribute_access(capsys, tmp_path):
    before = "def moved_fn(value):\n    return obj.old_name(value)\n"
    after = "def moved_fn(value):\n    return obj.helper(value)\n"

    code, lines = _run_nodes(capsys, tmp_path, before, [after], "--rename", "old_name=helper")

    assert (code, _checks(lines)) == (1, ["node-ast", "rename-unused"])


def test_nodes_reverses_overlapping_new_names_longest_first(capsys, tmp_path):
    before = "def moved_fn(value):\n    return collect_all(value)\n"
    after = "def moved_fn(value):\n    return util.helper.all(value)\n"

    code, lines = _run_nodes(
        capsys, tmp_path, before, [after], "--rename", "helper=util.helper", "collect_all=util.helper.all"
    )

    assert (code, lines) == (0, ["compared-nodes: 1"])


def test_nodes_flags_a_changed_string_holding_a_hash_as_node_ast_not_node_comments(capsys, tmp_path):
    code, lines = _run_nodes(capsys, tmp_path, 'LABEL = "# a"\n', ['LABEL = "# b"\n'], moved=("LABEL",))

    assert (code, _checks(lines)) == (1, ["node-ast"])


def test_nodes_flags_a_rename_that_no_compared_node_applied_as_rename_unused(capsys, tmp_path):
    code, lines = _run_nodes(capsys, tmp_path, MOVED_BEFORE, [MOVED_BEFORE], *RENAME_HELPER)

    assert (code, _checks(lines)) == (1, ["rename-unused"])
    assert "helper=util.helper" in lines[0]


def test_nodes_accepts_a_rename_used_only_by_the_second_of_two_compared_nodes(capsys, tmp_path):
    first = "def first_fn(value):\n    return value\n"
    second = "def second_fn(value):\n    return helper(value)\n"

    code, lines = _run_nodes(
        capsys, tmp_path, first + "\n\n" + second, [first + "\n\n" + second.replace("helper", "util.helper")],
        *RENAME_HELPER, moved=("first_fn", "second_fn"),
    )

    assert (code, lines) == (0, ["compared-nodes: 2"])


def test_nodes_flags_a_name_defined_twice_in_before_as_node_missing(capsys, tmp_path):
    defined_twice = MOVED_BEFORE + "\n\n" + MOVED_BEFORE

    code, lines = _run_nodes(capsys, tmp_path, defined_twice, [MOVED_BEFORE])

    assert (code, _checks(lines)) == (1, ["node-missing"])
    assert "defined 2 time(s)" in lines[0]


def test_nodes_compares_async_defs_and_annotated_assignments(capsys, tmp_path):
    source = "COUNT: int = 1\n\n\nasync def fetch_all():\n    return COUNT\n"

    code, lines = _run_nodes(capsys, tmp_path, source, [source], moved=("COUNT", "fetch_all"))

    assert (code, lines) == (0, ["compared-nodes: 2"])


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        pytest.param(("--verbatim", "not_moved"), "not listed in --moved: not_moved", id="verbatim-not-in-moved"),
        pytest.param(("--rename", "no-equals-sign"), "--rename expects NAME=VALUE", id="malformed-rename"),
        pytest.param(("--rename", "a=util.x", "b=util.x"), "share the same NEW name", id="two-renames-share-a-new-name"),
    ],
)
def test_nodes_exits_2_on_bad_arguments(capsys, tmp_path, options, reason):
    code, lines, err = _run_nodes_with_stderr(capsys, tmp_path, MOVED_BEFORE, [MOVED_AFTER], *options)

    assert (code, lines) == (2, [])
    assert reason in err


def test_nodes_exits_2_when_a_file_does_not_parse(capsys, tmp_path):
    code, lines, err = _run_nodes_with_stderr(capsys, tmp_path, MOVED_BEFORE, ["def broken(:\n"])

    assert (code, lines) == (2, [])
    assert "cannot parse" in err


# --- remainder ---------------------------------------------------------------

SHIM_BEFORE = '''\
"""Shim docstring."""
import os
from pkg import keep

# banner
def moved_out(arg):
    return arg


def stays(arg):
    return keep(arg)


def also_stays(arg):
    return os.sep + arg


def edited(arg):
    return 1
'''
SHIM_AFTER = '''\
"""Shim docstring."""
import os
from pkg import keep
from pkg.scope import moved_out

# banner
def stays(arg):
    return keep(arg)


def also_stays(arg):
    return os.sep + arg


def edited(arg):
    return 2


def fresh():
    return 3
'''
SHIM_DECLARATIONS = {
    "removed": ["moved_out"],
    "added": ["fresh"],
    "changed": ["edited"],
    "import_added": ["pkg.scope:moved_out"],
    "import_removed": [],
}


def _run_remainder_with_stderr(capsys, tmp_path, before_source, after_source, **declarations):
    argv = [
        "remainder",
        "--before",
        _write(tmp_path, "before.py", before_source),
        "--after",
        _write(tmp_path, "after.py", after_source),
    ]
    for key, names in declarations.items():
        if names:
            argv += ["--" + key.replace("_", "-"), *names]
    return _invoke(capsys, *argv)


def _run_remainder(capsys, tmp_path, before_source, after_source, **declarations):
    code, lines, _ = _run_remainder_with_stderr(capsys, tmp_path, before_source, after_source, **declarations)
    return code, lines


def test_remainder_clean_shim_move_with_a_rebinding_import_has_no_findings(capsys, tmp_path):
    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, SHIM_AFTER, **SHIM_DECLARATIONS)

    assert (code, lines) == (0, ["remaining-statements: 3"])


def test_remainder_identity_with_no_declarations_is_clean(capsys, tmp_path):
    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, SHIM_BEFORE)

    assert code == 0
    assert _checks(lines) == []


def test_remainder_flags_a_deleted_remaining_statement(capsys, tmp_path):
    without_stays = SHIM_AFTER.replace("def stays(arg):\n    return keep(arg)\n\n\n", "")

    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, without_stays, **SHIM_DECLARATIONS)

    assert (code, _checks(lines)) == (1, ["remainder-ast"])


def test_remainder_ast_finding_names_the_changed_construct(capsys, tmp_path):
    changed_call = SHIM_AFTER.replace("return keep(arg)", "return discard(arg)")

    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, changed_call, **SHIM_DECLARATIONS)

    assert (code, _checks(lines)) == (1, ["remainder-ast"])
    _, _, detail = lines[0].partition("first difference: ")
    assert "func=Name(id='keep'" in detail
    assert "func=Name(id='discard'" in detail


def test_remainder_flags_reordered_remaining_nodes(capsys, tmp_path):
    stays_block = "def stays(arg):\n    return keep(arg)\n\n\n"
    also_block = "def also_stays(arg):\n    return os.sep + arg\n\n\n"
    reordered = SHIM_AFTER.replace(stays_block + also_block, also_block + stays_block)

    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, reordered, **SHIM_DECLARATIONS)

    assert code == 1
    assert set(_checks(lines)) == {"remainder-ast"}


def test_remainder_flags_a_changed_comment_outside_the_dropped_spans(capsys, tmp_path):
    code, lines = _run_remainder(
        capsys, tmp_path, SHIM_BEFORE, SHIM_AFTER.replace("# banner", "# other banner"), **SHIM_DECLARATIONS
    )

    assert (code, _checks(lines)) == (1, ["remainder-comments"])


def test_remainder_ignores_a_changed_comment_inside_a_declared_changed_node(capsys, tmp_path):
    before = SHIM_BEFORE.replace("    return 1\n", "    # old note\n    return 1\n")
    after = SHIM_AFTER.replace("    return 2\n", "    # new note\n    return 2\n")

    code, lines = _run_remainder(capsys, tmp_path, before, after, **SHIM_DECLARATIONS)

    assert (code, _checks(lines)) == (0, [])


def test_remainder_reports_one_comment_finding_per_contiguous_block(capsys, tmp_path):
    before = "# first\n# second\nVALUE = 1\n"
    after = "# merged\nVALUE = 1\n"

    code, lines = _run_remainder(capsys, tmp_path, before, after)

    assert (code, _checks(lines)) == (1, ["remainder-comments"])


def test_remainder_flags_an_undeclared_added_import(capsys, tmp_path):
    with_json = SHIM_AFTER.replace("import os\n", "import os\nimport json\n")

    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, with_json, **SHIM_DECLARATIONS)

    assert (code, _checks(lines)) == (1, ["import-diff"])


def test_remainder_flags_a_declared_import_that_was_not_added(capsys, tmp_path):
    code, lines = _run_remainder(
        capsys, tmp_path, SHIM_BEFORE, SHIM_AFTER, **{**SHIM_DECLARATIONS, "import_added": ["pkg.scope:moved_out", "pkg:ghost"]}
    )

    assert (code, _checks(lines)) == (1, ["import-diff"])


def test_remainder_flags_a_changed_module_docstring_unless_declared_changed(capsys, tmp_path):
    new_docstring = SHIM_AFTER.replace("Shim docstring.", "Other docstring.")

    undeclared_code, undeclared_lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, new_docstring, **SHIM_DECLARATIONS)
    declared_code, declared_lines = _run_remainder(
        capsys, tmp_path, SHIM_BEFORE, new_docstring, **{**SHIM_DECLARATIONS, "changed": ["edited", "__doc__"]}
    )

    assert (undeclared_code, _checks(undeclared_lines)) == (1, ["remainder-ast"])
    assert (declared_code, _checks(declared_lines)) == (0, [])


@pytest.mark.parametrize(
    ("declarations", "expected_checks"),
    [
        pytest.param(
            {**SHIM_DECLARATIONS, "removed": ["moved_out", "stays"]},
            {"declared-mismatch", "remainder-ast"},
            id="removed-name-still-defined-after",
        ),
        pytest.param(
            {**SHIM_DECLARATIONS, "removed": ["moved_out", "ghost"]}, {"declared-mismatch"}, id="removed-name-absent-from-before"
        ),
        pytest.param(
            {**SHIM_DECLARATIONS, "added": ["fresh", "ghost"]}, {"declared-mismatch"}, id="added-name-absent-from-after"
        ),
        pytest.param(
            {**SHIM_DECLARATIONS, "added": ["fresh", "stays"]},
            {"declared-mismatch", "remainder-ast"},
            id="added-name-already-defined-before",
        ),
        pytest.param(
            {**SHIM_DECLARATIONS, "removed": [], "changed": ["edited", "moved_out"]},
            {"declared-mismatch"},
            id="changed-name-missing-after",
        ),
        pytest.param(
            {**SHIM_DECLARATIONS, "added": [], "changed": ["edited", "fresh"]},
            {"declared-mismatch"},
            id="changed-name-missing-before",
        ),
    ],
)
def test_remainder_flags_a_declaration_the_files_contradict(capsys, tmp_path, declarations, expected_checks):
    code, lines = _run_remainder(capsys, tmp_path, SHIM_BEFORE, SHIM_AFTER, **declarations)

    assert code == 1
    assert set(_checks(lines)) == expected_checks


def test_remainder_treats_a_new_module_against_an_empty_before_as_all_added(capsys, tmp_path):
    new_module = '"""Owns the moved code."""\n\n\ndef moved_out(arg):\n    return arg\n'

    code, lines = _run_remainder(capsys, tmp_path, "", new_module, added=["__doc__", "moved_out"])

    assert (code, lines) == (0, ["remaining-statements: 0"])


def test_remainder_exits_2_when_a_name_is_declared_twice(capsys, tmp_path):
    code, lines, err = _run_remainder_with_stderr(
        capsys, tmp_path, SHIM_BEFORE, SHIM_AFTER, removed=["edited"], changed=["edited"]
    )

    assert (code, lines) == (2, [])
    assert "edited is declared under both --removed and --changed" in err


# --- capture and compare-captures -------------------------------------------

ECHO_SCRIPT = '''\
import argparse
import json
import os
import sys

PINNED_NAMES = ("PYTHONPATH", "COLUMNS", "PYTHONHASHSEED", "CLAUDE_CONFIG_DIR", "TRANSCRIPT_CONFIG_DIRS_FILE", "EXTRA")


def build_parser():
    parser = argparse.ArgumentParser(prog="echo")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("zeta", help="registered first, sorts last")
    subcommands.add_parser("alpha", aliases=["a"], help="registered second, sorts first")
    return parser


if __name__ == "__main__":
    report = {
        "argv": sys.argv[1:],
        "env": {name: os.environ.get(name) for name in PINNED_NAMES},
        "cwd": os.getcwd(),
        "stdin": sys.stdin.read(),
    }
    print(json.dumps(report))
    sys.exit(int(os.environ.get("ECHO_EXIT", "0")))
'''


@pytest.fixture
def echo_target(tmp_path):
    script = _write(tmp_path, "echo_cli.py", ECHO_SCRIPT)
    account = tmp_path / "account"
    account.mkdir()
    return script, account


def _capture_argv(echo_target, out: Path, *options) -> list[str]:
    script, account = echo_target
    return ["capture", "--script", str(script), "--account", str(account), "--out", str(out), *options]


def _single_run(capsys, tmp_path, echo_target, *options):
    runs = _write(tmp_path, "runs.txt", "go\n")
    out = tmp_path / "out"
    code, _, err = _invoke(capsys, *_capture_argv(echo_target, out, "--runs", runs, *options))
    return code, err, out


def _echoed_env(out: Path) -> dict:
    return json.loads((out / "run-01.stdout").read_text())["env"]


def test_capture_pins_the_environment_over_hostile_ambient_values(capsys, tmp_path, monkeypatch, echo_target):
    _, account = echo_target
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile-pythonpath"))
    monkeypatch.setenv("COLUMNS", "33")
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "hostile-account"))
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "hostile-dirs-file"))

    code, err, out = _single_run(capsys, tmp_path, echo_target)

    env = _echoed_env(out)
    assert (code, err) == (0, "")
    assert env["PYTHONPATH"] is None
    assert env["COLUMNS"] == "100"
    assert env["PYTHONHASHSEED"] == "0"
    assert env["CLAUDE_CONFIG_DIR"] == str(account.resolve())
    dirs_file = Path(env["TRANSCRIPT_CONFIG_DIRS_FILE"])
    assert dirs_file.parent == account.resolve()
    assert not dirs_file.exists()


@pytest.fixture
def stdin_holding_unread_data():
    """Point file descriptor 0 at a closed pipe that still holds data, so an inheriting child reads text, not EOF."""
    read_end, write_end = os.pipe()
    os.write(write_end, b"leaked from the caller")
    os.close(write_end)
    saved_stdin = os.dup(0)
    os.dup2(read_end, 0)
    try:
        yield
    finally:
        os.dup2(saved_stdin, 0)
        os.close(saved_stdin)
        os.close(read_end)


def test_capture_runs_the_child_in_the_callers_cwd_with_stdin_at_eof(
    capsys, tmp_path, monkeypatch, echo_target, stdin_holding_unread_data
):
    caller_dir = tmp_path / "caller-dir"
    caller_dir.mkdir()
    monkeypatch.chdir(caller_dir)

    code, _, out = _single_run(capsys, tmp_path, echo_target)

    report = json.loads((out / "run-01.stdout").read_text())
    assert code == 0
    assert Path(report["cwd"]).resolve() == caller_dir.resolve()
    assert report["stdin"] == ""


def test_capture_records_a_nonzero_exit_without_failing(capsys, tmp_path, echo_target):
    code, _, out = _single_run(capsys, tmp_path, echo_target, "--env", "ECHO_EXIT=3")

    assert code == 0
    assert (out / "run-01.exit").read_text() == "3\n"
    assert (out / "run-01.stderr").read_bytes() == b""


def test_capture_env_overrides_a_pinned_value(capsys, tmp_path, echo_target):
    _, _, out = _single_run(capsys, tmp_path, echo_target, "--env", "COLUMNS=55", "EXTRA=a=b")

    assert _echoed_env(out)["COLUMNS"] == "55"
    assert _echoed_env(out)["EXTRA"] == "a=b"


def test_capture_unset_removes_a_pinned_value(capsys, tmp_path, echo_target):
    _, _, out = _single_run(capsys, tmp_path, echo_target, "--unset", "COLUMNS")

    assert _echoed_env(out)["COLUMNS"] is None


def test_capture_applies_unset_after_env_so_unset_wins_for_the_same_name(capsys, tmp_path, echo_target):
    _, _, out = _single_run(capsys, tmp_path, echo_target, "--env", "COLUMNS=55", "--unset", "COLUMNS")

    assert _echoed_env(out)["COLUMNS"] is None


def test_capture_runs_file_skips_blank_and_comment_lines_and_keeps_quoted_arguments(capsys, tmp_path, echo_target):
    runs = _write(tmp_path, "runs.txt", "\n# a comment\nbuckets '*' --flag\n   \nsecond run\n")
    out = tmp_path / "out"

    code, lines, _ = _invoke(capsys, *_capture_argv(echo_target, out, "--runs", runs))

    assert (code, lines) == (0, [f"captured 2 run(s) into {out}"])
    assert sorted(path.name for path in out.glob("*.argv")) == ["run-01.argv", "run-02.argv"]
    assert json.loads((out / "run-01.stdout").read_text())["argv"] == ["buckets", "*", "--flag"]
    assert json.loads((out / "run-02.stdout").read_text())["argv"] == ["second", "run"]
    assert (out / "run-01.argv").read_text() == "buckets '*' --flag\n"


def test_capture_all_help_lists_top_then_each_subcommand_in_registration_order_not_sorted_without_aliases(tmp_path, echo_target):
    script, account = echo_target

    labels = capture(script, account, tmp_path / "out", all_help=True)

    assert labels == ["help-top", "help-zeta", "help-alpha"]
    assert (tmp_path / "out" / "help-alpha.argv").read_text() == "alpha --help\n"
    assert (tmp_path / "out" / "help-top.argv").read_text() == "--help\n"


def test_capture_all_help_restores_sys_path_and_registers_no_module(capsys, tmp_path, echo_target):
    path_before = list(sys.path)

    code, _, _ = _invoke(capsys, *_capture_argv(echo_target, tmp_path / "out", "--all-help"))

    assert code == 0
    assert sys.path == path_before
    assert CAPTURE_TARGET_MODULE_NAME not in sys.modules


def test_capture_all_help_restores_sys_path_when_the_script_fails_to_load(capsys, tmp_path):
    broken = _write(tmp_path, "broken_cli.py", "raise RuntimeError('boom')\n")
    account = tmp_path / "account"
    account.mkdir()
    path_before = list(sys.path)

    code, _, err = _invoke(capsys, *_capture_argv((broken, account), tmp_path / "out", "--all-help"))

    assert code == 2
    assert "bad-input" in err
    assert sys.path == path_before


def test_capture_refuses_an_existing_out_directory(capsys, tmp_path, echo_target):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("kept")

    code, _, err = _invoke(capsys, *_capture_argv(echo_target, out, "--all-help"))

    assert code == 2
    assert "already exists" in err
    assert [path.name for path in out.iterdir()] == ["keep.txt"]


def test_capture_exits_2_when_nothing_is_requested(capsys, tmp_path, echo_target):
    code, _, err = _invoke(capsys, *_capture_argv(echo_target, tmp_path / "out"))

    assert code == 2
    assert "nothing to capture" in err


def _write_capture(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir()
    for name, text in files.items():
        (directory / name).write_text(text)
    return directory


def test_compare_captures_identical_directories_are_clean(capsys, tmp_path):
    files = {"run-01.stdout": "same\n", "run-01.exit": "0\n"}
    left, right = _write_capture(tmp_path / "a", files), _write_capture(tmp_path / "b", files)

    code, lines, _ = _invoke(capsys, "compare-captures", left, right)

    assert (code, lines) == (0, ["compared-files: 2"])


def test_compare_captures_flags_one_changed_byte_with_a_short_diff(capsys, tmp_path):
    left = _write_capture(tmp_path / "a", {"run-01.stdout": "alpha\nbeta\n"})
    right = _write_capture(tmp_path / "b", {"run-01.stdout": "alpha\nbetA\n"})

    code, lines, _ = _invoke(capsys, "compare-captures", left, right)

    assert (code, _checks(lines)) == (1, ["capture-bytes"])
    assert any(line.startswith("  -beta") for line in lines)
    assert any(line.startswith("  +betA") for line in lines)


def test_compare_captures_flags_a_one_sided_file(capsys, tmp_path):
    left = _write_capture(tmp_path / "a", {"run-01.stdout": "x\n", "run-02.stdout": "y\n"})
    right = _write_capture(tmp_path / "b", {"run-01.stdout": "x\n"})

    code, lines, _ = _invoke(capsys, "compare-captures", left, right)

    assert (code, _checks(lines)) == (1, ["capture-missing"])
    assert "run-02.stdout" in lines[0]


def test_compare_captures_mask_hides_a_difference_it_covers(capsys, tmp_path):
    left = _write_capture(tmp_path / "a", {"run-01.stdout": "wrote /tmp/run-1234/out\n"})
    right = _write_capture(tmp_path / "b", {"run-01.stdout": "wrote /tmp/run-5678/out\n"})

    unmasked_code, unmasked_lines, _ = _invoke(capsys, "compare-captures", left, right)
    masked_code, masked_lines, _ = _invoke(capsys, "compare-captures", left, right, "--mask", r"run-\d+")

    assert (unmasked_code, _checks(unmasked_lines)) == (1, ["capture-bytes"])
    assert (masked_code, masked_lines) == (0, ["compared-files: 1"])


def test_compare_captures_exits_2_for_two_empty_directories(capsys, tmp_path):
    left, right = _write_capture(tmp_path / "a", {}), _write_capture(tmp_path / "b", {})

    code, lines, _ = _invoke(capsys, "compare-captures", left, right)

    assert (code, lines) == (2, [])


def test_compare_captures_exits_2_for_an_invalid_mask(capsys, tmp_path):
    left = _write_capture(tmp_path / "a", {"run-01.stdout": "x\n"})

    code, _, err = _invoke(capsys, "compare-captures", left, left, "--mask", "(")

    assert code == 2
    assert "invalid --mask" in err


# --- test-ids ----------------------------------------------------------------

TREE = "claude..claude.scripts.tests"
OTHER_TREE = "claude..claude.hooks.tests"


def _junit(*cases: tuple[str, str, tuple[str, ...]]) -> str:
    body = "".join(
        f"<testcase classname={quoteattr(classname)} name={quoteattr(name)}>"
        + "".join(f"<{tag}/>" for tag in tags)
        + "</testcase>"
        for classname, name, tags in cases
    )
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


def _run_test_ids(capsys, tmp_path, before_cases, after_cases, *options):
    before = tmp_path / "before.xml"
    after = tmp_path / "after.xml"
    before.write_text(_junit(*before_cases))
    after.write_text(_junit(*after_cases))
    code, lines, err = _invoke(capsys, "test-ids", "--before", before, "--after", after, *options)
    return code, lines, err


def test_test_ids_same_ids_under_a_different_file_are_clean(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ()), (f"{TREE}.test_old.TestThing", "test_two[a-b]", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", ()), (f"{TREE}.test_new.TestThing", "test_two[a-b]", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, lines) == (0, ["testcases: before 2, after 2"])


def test_test_ids_flags_a_dropped_id(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ()), (f"{TREE}.test_old.TestThing", "test_two", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, _checks(lines)) == (1, ["id-missing"])
    assert "TestThing::test_two" in lines[0]


def test_test_ids_flags_an_added_id_until_it_is_declared_new(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", ()), (f"{TREE}.test_new.TestThing", "test_three", ())]

    undeclared_code, undeclared_lines, _ = _run_test_ids(capsys, tmp_path, before, after)
    declared_code, declared_lines, _ = _run_test_ids(capsys, tmp_path, before, after, "--expect-new", "TestThing::test_three")

    assert (undeclared_code, _checks(undeclared_lines)) == (1, ["id-unexpected"])
    assert (declared_code, _checks(declared_lines)) == (0, [])


def test_test_ids_counts_a_colliding_new_id_as_new_only_for_its_excess_copy(capsys, tmp_path):
    before = [(f"{TREE}.test_a", "test_smoke", ())]
    after = [(f"{TREE}.test_a", "test_smoke", ()), (f"{TREE}.test_b", "test_smoke", ())]

    undeclared_code, undeclared_lines, _ = _run_test_ids(capsys, tmp_path, before, after)
    declared_code, declared_lines, _ = _run_test_ids(capsys, tmp_path, before, after, "--expect-new", "test_smoke")

    assert (undeclared_code, _checks(undeclared_lines)) == (1, ["id-unexpected"])
    assert (declared_code, declared_lines) == (0, ["testcases: before 1, after 2"])


def test_test_ids_flags_an_expect_new_id_that_never_appears_after(capsys, tmp_path):
    cases = [(f"{TREE}.test_old.TestThing", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, cases, cases, "--expect-new", "TestThing::test_ghost")

    assert (code, _checks(lines)) == (1, ["expect-new-mismatch"])
    assert "never appears" in lines[0]


def test_test_ids_flags_an_expect_new_id_present_on_both_sides(capsys, tmp_path):
    cases = [(f"{TREE}.test_old.TestThing", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, cases, cases, "--expect-new", "TestThing::test_one")

    assert (code, _checks(lines)) == (1, ["expect-new-mismatch"])
    assert "does not occur more often" in lines[0]


def test_test_ids_flags_passed_to_skipped_as_an_outcome_change(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", ("skipped",))]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, _checks(lines)) == (1, ["id-outcome"])
    assert "before passed, after skipped" in lines[0]


def test_test_ids_flags_two_copies_before_and_one_after_as_missing(capsys, tmp_path):
    before = [(f"{TREE}.test_old", "test_one", ()), (f"{TREE}.test_other", "test_one", ())]
    after = [(f"{TREE}.test_new", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, _checks(lines)) == (1, ["id-missing"])


def test_test_ids_rename_maps_a_before_class_to_its_after_name(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestOld", "test_one", ()), (f"{TREE}.test_old.TestOld", "test_two", ())]
    after = [(f"{TREE}.test_new.TestNew", "test_one", ()), (f"{TREE}.test_new.TestNew", "test_two", ())]

    unmapped_code, unmapped_lines, _ = _run_test_ids(capsys, tmp_path, before, after)
    mapped_code, mapped_lines, _ = _run_test_ids(capsys, tmp_path, before, after, "--rename", "TestOld=TestNew")

    assert (unmapped_code, set(_checks(unmapped_lines))) == (1, {"id-missing", "id-unexpected"})
    assert (mapped_code, _checks(mapped_lines)) == (0, [])


def test_test_ids_rename_matches_whole_parts_not_a_longer_name(capsys, tmp_path):
    cases = [(f"{TREE}.test_old.TestOldish", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, cases, cases, "--rename", "TestOld=TestNew")

    assert (code, lines) == (0, ["testcases: before 1, after 1"])


def test_test_ids_rename_maps_a_parametrized_function_by_its_stem(capsys, tmp_path):
    before = [(f"{TREE}.test_old", "test_old[x]", ()), (f"{TREE}.test_old", "test_old_other", ())]
    after = [(f"{TREE}.test_new", "test_new[x]", ()), (f"{TREE}.test_new", "test_old_other", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after, "--rename", "test_old=test_new")

    assert (code, lines) == (0, ["testcases: before 2, after 2"])


def test_test_ids_keys_on_the_last_test_component_of_the_classname(capsys, tmp_path):
    before = [(f"{TREE}.test_pkg.test_old.TestThing", "test_one", ())]
    after = [(f"{TREE}.test_pkg.test_new.TestThing", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, lines) == (0, ["testcases: before 1, after 1"])


def test_test_ids_file_prefix_naming_one_test_file_keeps_its_functions_and_classes(capsys, tmp_path):
    cases = [(f"{TREE}.test_old", "test_free", ()), (f"{TREE}.test_old.TestThing", "test_one", ())]
    outside = [(f"{TREE}.test_other", "test_elsewhere", ())]

    code, lines, _ = _run_test_ids(
        capsys, tmp_path, cases + outside, cases, "--file-prefix", "claude/.claude/scripts/tests/test_old.py"
    )

    assert (code, lines) == (0, ["testcases: before 2, after 2"])


def test_test_ids_file_prefix_matches_whole_path_segments(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ()), (f"{TREE}_extra.test_other.TestOther", "test_gone", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after, "--file-prefix", "claude/.claude/scripts/tests")

    assert (code, lines) == (0, ["testcases: before 1, after 1"])


def test_test_ids_file_prefix_excludes_another_test_tree(capsys, tmp_path):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ()), (f"{OTHER_TREE}.test_hook.TestHook", "test_gone", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", ())]

    unscoped_code, unscoped_lines, _ = _run_test_ids(capsys, tmp_path, before, after)
    scoped_code, scoped_lines, _ = _run_test_ids(
        capsys, tmp_path, before, after, "--file-prefix", "claude/.claude/scripts/tests/"
    )

    assert (unscoped_code, _checks(unscoped_lines)) == (1, ["id-missing"])
    assert (scoped_code, scoped_lines) == (0, ["testcases: before 1, after 1"])


def test_test_ids_exits_2_for_a_classname_with_no_test_component(capsys, tmp_path):
    before = [(f"{TREE}.helpers.Thing", "test_one", ())]

    code, _, err = _run_test_ids(capsys, tmp_path, before, before)

    assert code == 2
    assert "no test_ component" in err


def test_test_ids_does_not_exit_2_for_a_non_test_classname_outside_the_file_prefix(capsys, tmp_path):
    cases = [(f"{TREE}.test_old.TestThing", "test_one", ()), ("elsewhere.helpers.Thing", "test_one", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, cases, cases, "--file-prefix", "claude/.claude/scripts/tests/")

    assert (code, lines) == (0, ["testcases: before 1, after 1"])


def test_test_ids_keys_a_module_level_test_function_by_its_name_alone(capsys, tmp_path):
    before = [(f"{TREE}.test_old", "test_free_function", ())]
    after = [(f"{TREE}.test_new", "test_free_function", ())]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, lines) == (0, ["testcases: before 1, after 1"])


def test_test_ids_unions_several_xml_files_on_each_side(capsys, tmp_path):
    parallel = tmp_path / "parallel.xml"
    timing = tmp_path / "timing.xml"
    combined = tmp_path / "combined.xml"
    parallel.write_text(_junit((f"{TREE}.test_old", "test_fast", ())))
    timing.write_text(_junit((f"{TREE}.test_old", "test_slow", ())))
    combined.write_text(_junit((f"{TREE}.test_new", "test_fast", ()), (f"{TREE}.test_new", "test_slow", ())))

    code, lines, _ = _invoke(
        capsys, "test-ids", "--before", parallel, "--before", timing, "--after", combined
    )

    assert (code, lines) == (0, ["testcases: before 2, after 2"])


def test_test_ids_exits_2_for_unparseable_xml(capsys, tmp_path):
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites>")

    code, _, err = _invoke(capsys, "test-ids", "--before", broken, "--after", broken)

    assert code == 2
    assert "cannot read junit XML" in err


def test_test_ids_exits_2_when_no_testcase_survives_the_file_prefix(capsys, tmp_path):
    cases = [(f"{TREE}.test_old", "test_one", ())]

    code, _, err = _run_test_ids(capsys, tmp_path, cases, cases, "--file-prefix", "nowhere/")

    assert code == 2
    assert "no testcases" in err


# --- packaging ---------------------------------------------------------------


def test_helper_runs_as_a_script_without_the_package(tmp_path):
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH), "compare-captures", str(tmp_path), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "bad-input" in result.stderr


def test_helper_imports_only_the_standard_library_and_no_siblings():
    tree = ast.parse(HELPER_PATH.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in the helper"
            imported.append((node.module or "").split(".")[0])

    assert set(imported) <= set(sys.stdlib_module_names)
