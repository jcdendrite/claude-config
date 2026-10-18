"""Seeded-defect self-test for tests/_move_fidelity.py's test-ids subcommand.

Every test-ids check is proved by junit XML written under tmp_path: a clean case yields no findings,
and one seeded defect per check yields exactly the finding that names it.
The other subcommands' seeds live in test_transcript_move_fidelity.py and test_transcript_move_fidelity_parser.py.
"""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

import pytest

from ._move_fidelity_helpers import _checks, _invoke

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


@pytest.mark.parametrize("outcome_tag", ["skipped", "failure", "error"])
def test_test_ids_flags_passed_to_a_non_passing_outcome_as_an_outcome_change(capsys, tmp_path, outcome_tag):
    before = [(f"{TREE}.test_old.TestThing", "test_one", ())]
    after = [(f"{TREE}.test_new.TestThing", "test_one", (outcome_tag,))]

    code, lines, _ = _run_test_ids(capsys, tmp_path, before, after)

    assert (code, _checks(lines)) == (1, ["id-outcome"])
    assert f"before passed, after {outcome_tag}" in lines[0]


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


def test_test_ids_rename_maps_an_unparametrized_module_level_test_whose_id_equals_the_old_name(capsys, tmp_path):
    before = [(f"{TREE}.test_old", "test_free_function", ())]
    after = [(f"{TREE}.test_new", "test_renamed_function", ())]

    unmapped_code, unmapped_lines, _ = _run_test_ids(capsys, tmp_path, before, after)
    mapped_code, mapped_lines, _ = _run_test_ids(
        capsys, tmp_path, before, after, "--rename", "test_free_function=test_renamed_function"
    )

    assert (unmapped_code, set(_checks(unmapped_lines))) == (1, {"id-missing", "id-unexpected"})
    assert (mapped_code, mapped_lines) == (0, ["testcases: before 1, after 1"])


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
    assert "--file-prefix nowhere/" in err


def test_test_ids_empty_runs_exit_2_without_naming_a_file_prefix_that_was_not_given(capsys, tmp_path):
    code, _, err = _run_test_ids(capsys, tmp_path, [], [])

    assert code == 2
    assert "no testcases" in err
    assert "--file-prefix" not in err
