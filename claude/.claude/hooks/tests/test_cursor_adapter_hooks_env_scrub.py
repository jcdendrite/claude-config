"""Tests that this tree's conftest clears CLAUDE_CONFIG_ADAPTER_* before a test body runs.

The conftest's autouse fixture runs before any test body, so its test is a nested pytest run. The run starts with
the adapter's session variable, its host-PID variable and an invented variable of the same prefix set, and collects
a probe under the real conftest. The probe asserts that a hook environment built through `_build_subprocess_env`
lacks all three. The same probe under `--noconftest` must fail naming all three, which shows they reached the
nested run.

claude/.claude/scripts/tests/test_cursor_adapter_scripts_env_scrub.py holds the scripts tree's own copy,
so an edit to either conftest selects its own check.
"""

from __future__ import annotations

from pathlib import Path

from helpers import _build_subprocess_env

from ._cursor_identity_fixtures import LEAKED_ADAPTER_ENVIRONMENT, only_probe_case, run_nested_probe

PROBE_CLASS_NAME = "ProbeHookEnvironmentLacksAdapterVariables"


class ProbeHookEnvironmentLacksAdapterVariables:
    """Collected only by the nested run, which names this class in `python_classes`. The default `Test*` pattern skips it."""

    def test_a_hook_environment_built_from_the_parent_lacks_every_adapter_variable(self):
        hook_environment = _build_subprocess_env(Path("/scratch-home"), None)
        leaked = [name for name in LEAKED_ADAPTER_ENVIRONMENT if name in hook_environment]
        assert not leaked, leaked


class TestEnvScrubNestedRun:
    def test_the_conftest_clears_every_adapter_variable_before_the_probe_body_runs(self, tmp_path):
        report_path = tmp_path / "with-conftest.xml"

        result = run_nested_probe(Path(__file__), PROBE_CLASS_NAME, report_path)

        case = only_probe_case(report_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert [child.tag for child in case] == [], "the probe must pass, not skip, fail or error"

    def test_the_probe_fails_naming_every_variable_when_no_conftest_clears_them(self, tmp_path):
        """Shows the variables reached the nested run, so the passing run above is not vacuous."""
        report_path = tmp_path / "without-conftest.xml"

        result = run_nested_probe(Path(__file__), PROBE_CLASS_NAME, report_path, "--noconftest")

        case = only_probe_case(report_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert [child.tag for child in case] == ["failure"], "a setup error or skip is not the probe's own failure"
        failure_message = case.find("failure").get("message", "")
        assert all(name in failure_message for name in LEAKED_ADAPTER_ENVIRONMENT), failure_message
