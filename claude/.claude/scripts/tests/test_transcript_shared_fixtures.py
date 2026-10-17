"""Pins that conftest's fake_projects, cost_ledger_file, and cost_ledger_enabled work in a
test file that loads no transcript-analysis.py shim. This file deliberately loads none, so a
fixture that comes to depend on the shim fails here first."""
from transcript_analysis import cost_ledger, ledger_common, pr_cost_ledger, scope


def test_fake_projects_patches_every_config_dir_binding_without_a_shim(fake_projects, tmp_path):
    assert "_mod" not in globals()
    # Reading vars(scope) never triggers the lazy PROJECTS_DIR resolution that attribute access runs.
    # A cached lazy value and the fixture's setattr both leave a real entry.
    # Only a value equal to this test's projects directory therefore proves the patch.
    assert vars(scope)["PROJECTS_DIR"] == fake_projects.parent
    assert scope.config_dir() == tmp_path
    assert cost_ledger.config_dir() == tmp_path
    assert ledger_common.config_dir() == tmp_path
    assert pr_cost_ledger.config_dir() == tmp_path


def test_cost_ledger_fixtures_patch_their_targets_without_a_shim(
    cost_ledger_file, cost_ledger_enabled
):
    assert cost_ledger._cost_ledger_path() == cost_ledger_file
    assert cost_ledger.config_dir() == cost_ledger_enabled
    assert ledger_common.config_dir() == cost_ledger_enabled
