"""Tests for render-settings.sh.

Each test builds its own scratch $CLAUDE_CONFIG_DIR under tmp_path and
invokes the real script via subprocess. Most tests need no shim, since the
script's main external dependency is jq. The two TestChmodPortability shim
cases are the exception: each prepends a fake chmod that rejects a literal
`--` argument to PATH. The shim cannot detect a dash-leading operand reaching
chmod unnormalized, which only a real BSD chmod would reject.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "render-settings.sh"
_SETTINGS_BASE_JSON = Path(__file__).resolve().parents[2] / "settings.base.json"
_GUARD_HOOK = Path(__file__).resolve().parents[2] / "hooks" / "guard-settings-session-keys.sh"
_REPO_ROOT = Path(__file__).resolve().parents[4]

# The overlay's closed top-level allowlist, excluding the conditionally
# admissible `permissions` -- see TestBaseOverlayDisjointness, which mirrors
# TestBaseKeyPlacementDisjointness in test_guard_settings_session_keys.py.
OVERLAY_ALLOWED_TOP_LEVEL_KEYS = {"autoMode", "env", "skillListingBudgetFraction"}

# The overlay's exact-name env allowlist, written out independently of
# render-settings.sh so a widening of the script's list fails these tests.
ENV_ALLOWED_NAMES = {
    "ANTHROPIC_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "DISABLE_BUG_COMMAND",
    "DISABLE_ERROR_REPORTING",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "DISABLE_TELEMETRY",
}


def _write_json(path: Path, content: dict | list) -> None:
    path.write_text(json.dumps(content))


def _run_script(
    *args: str,
    config_dir: Path,
    cwd: Path | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
        timeout=15,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rule4_dotted_paths_from_render_settings() -> list[str]:
    """render-settings.sh's own RULE4_DOTTED_PATHS_JSON, via its print interface.

    Mirrors guard-settings-session-keys.sh's --print-guarded-keys mode --
    both are drift-check interfaces only, not on the render's hot path (see
    render-settings.sh's own comment on why it doesn't shell out to
    guard-settings-session-keys.sh on every render). This is comparing two
    independently-maintained literals for drift, not standing in for
    actually exercising rule 4 (TestShallowCarryForward's rule-4 cases
    already do that through the real script).
    """
    result = subprocess.run(
        [str(_SCRIPT), "--print-rule4-dotted-paths"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _env_allowed_names_from_render_settings() -> list[str]:
    """render-settings.sh's own OVERLAY_ENV_ALLOWED_NAMES_JSON, via its print
    interface, for the same drift check as the rule-4 paths above."""
    result = subprocess.run(
        [str(_SCRIPT), "--print-env-allowed-names"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestNoOverlay:
    def test_renders_base_unchanged_when_overlay_absent(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        base = {"permissions": {"deny": ["a", "b"]}, "hooks": {"PreToolUse": []}}
        _write_json(config_dir / "settings.base.json", base)

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == base


class TestOverlayMerge:
    def test_overlay_array_replaces_base_array_rather_than_concatenating(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(
            config_dir / "settings.base.json",
            {"autoMode": {"environment": ["$defaults", "base-only-entry"]}},
        )
        _write_json(
            config_dir / "settings.overlay.json",
            {"autoMode": {"environment": ["$defaults", "overlay-entry"]}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["autoMode"]["environment"] == ["$defaults", "overlay-entry"]

    def test_explicit_overlay_argument_overrides_default_overlay_path(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        # Deliberately no settings.overlay.json at the default path -- only
        # the explicitly-named alternate overlay should be read.
        alt_overlay = tmp_path / "alt-overlay.json"
        _write_json(alt_overlay, {"autoMode": {"environment": ["$defaults"]}})

        result = _run_script(str(alt_overlay), config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {
            "otherKey": "base-value",
            "autoMode": {"environment": ["$defaults"]},
        }

    def test_explicit_overlay_argument_naming_nonexistent_file_renders_base_only(
        self, tmp_path: Path
    ) -> None:
        """Pins the current behavior for a typo'd/stale explicit $1: silent
        base-only render, identical to the no-overlay-configured case -- not
        a loud failure."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        missing_overlay = tmp_path / "does-not-exist.json"

        result = _run_script(str(missing_overlay), config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {"otherKey": "base-value"}

    def test_overlay_autoMode_entirely_replaces_base_autoMode(self, tmp_path: Path) -> None:
        """Confirms the merge of an overlay-allowed key is shallow whole-
        value replacement (jq `+`), not a deep merge -- the overlay's
        autoMode entirely replaces base's rather than combining with it."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(
            config_dir / "settings.base.json",
            {"autoMode": {"environment": ["$defaults", "base-only-entry"]}},
        )
        _write_json(
            config_dir / "settings.overlay.json",
            {"autoMode": {"environment": ["$defaults"]}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["autoMode"] == {"environment": ["$defaults"]}


class TestMissingBase:
    def test_missing_base_fails_loudly_with_no_partial_write(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "settings.base.json" in result.stderr
        assert not (config_dir / "settings.json").exists()


class TestJqCannotRun:
    """A jq that cannot run says nothing about the input, so the render must
    not blame the file: it names jq, and a genuinely malformed input still
    gets the not-valid-JSON message."""

    @staticmethod
    def _make_configs(tmp_path: Path) -> Path:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        return config_dir

    @staticmethod
    def _write_jq_shim(bin_dir: Path, failing_operand_suffix: str, exit_status: int) -> None:
        """A jq that exits `exit_status` for any call naming a file ending in
        `failing_operand_suffix` and otherwise runs the real jq."""
        real_jq = shutil.which("jq")
        assert real_jq is not None, "jq must be installed to run this test"
        bin_dir.mkdir()
        shim = bin_dir / "jq"
        shim.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            f'  case "$arg" in *{failing_operand_suffix}) exit {exit_status} ;; esac\n'
            "done\n"
            f'exec {shlex.quote(real_jq)} "$@"\n'
        )
        shim.chmod(0o755)

    def test_absent_jq_is_reported_as_jq_not_running_rather_than_invalid_json(
        self, tmp_path: Path
    ) -> None:
        config_dir = self._make_configs(tmp_path)
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()

        result = _run_script(config_dir=config_dir, extra_env={"PATH": str(empty_bin)})

        assert result.returncode != 0
        assert "jq could not run" in result.stderr
        assert "not valid JSON" not in result.stderr
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.parametrize("jq_exit_status", [126, 127])
    @pytest.mark.parametrize(
        ("failing_operand_suffix", "unexamined_file"),
        [("settings.base.json", "settings.base.json"), ("settings.overlay.json", "settings.overlay.json")],
        ids=["base-check", "overlay-check"],
    )
    def test_jq_exiting_126_or_127_names_jq_for_the_base_and_the_overlay_check(
        self,
        tmp_path: Path,
        jq_exit_status: int,
        failing_operand_suffix: str,
        unexamined_file: str,
    ) -> None:
        config_dir = self._make_configs(tmp_path)
        _write_json(config_dir / "settings.overlay.json", {"autoMode": {"environment": ["$defaults"]}})
        bin_dir = tmp_path / "jq-shim-bin"
        self._write_jq_shim(bin_dir, failing_operand_suffix, jq_exit_status)

        result = _run_script(config_dir=config_dir, extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})

        assert result.returncode != 0
        assert f"jq could not run (exit {jq_exit_status}" in result.stderr
        assert f"{unexamined_file} was not examined" in result.stderr
        assert "not valid JSON" not in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_malformed_base_is_still_reported_as_invalid_json(self, tmp_path: Path) -> None:
        config_dir = self._make_configs(tmp_path)
        (config_dir / "settings.base.json").write_text("{not valid json")

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "not valid JSON" in result.stderr
        assert "jq could not run" not in result.stderr


class TestOverlayValidation:
    def test_overlay_valid_json_but_not_object_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", [1, 2])

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "not a JSON object" in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_overlay_malformed_json_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        (config_dir / "settings.overlay.json").write_text("{not valid json")

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_overlay_fails_loudly_rather_than_silently_skipping(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "settings.overlay.json"
        _write_json(overlay, {"autoMode": {"environment": ["$defaults"]}})
        overlay.chmod(0o000)
        try:
            result = _run_script(config_dir=config_dir)
        finally:
            overlay.chmod(0o644)

        assert result.returncode != 0
        assert "not readable" in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_overlay_key_outside_closed_set_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"autoMode": {"environment": ["$defaults"]}, "notAllowed": "x"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "notAllowed" in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_rejected_keys_closed_set_message_names_the_key_not_its_value(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.overlay.json",
            {
                "autoMode": {"environment": ["$defaults"]},
                "leakedToken": "sk-should-never-appear",
            },
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "leakedToken" in result.stderr
        assert "sk-should-never-appear" not in result.stderr
        assert not (config_dir / "settings.json").exists()


class TestOverlayAllowlist:
    """Closed top-level allowlist plus the exact-name env allowlist."""

    @pytest.mark.parametrize(
        "bad_name",
        [
            "BASH_ENV",
            "NODE_OPTIONS",
            "LD_PRELOAD",
            "PATH",
            "DYLD_INSERT_LIBRARIES",
            "DYLD_LIBRARY_PATH",
            "PYTHONPATH",
            "GIT_SSH_COMMAND",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
            "CLAUDE_CODE_SHELL_PREFIX",
        ],
    )
    def test_env_name_outside_allowlist_is_rejected(self, tmp_path: Path, bad_name: str) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {bad_name: "x"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert bad_name in result.stderr
        assert "outside the allowed set" in result.stderr
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.parametrize(
        "near_miss_name",
        [
            "ANTHROPIC_",
            "CLAUDE_CODE_",
            "DISABLE_",
            "ANTHROPIC",
            "anthropic_model",
            "Anthropic_Model",
            "ANTHROPIC_MODELS",
            "ANTHROPIC_MODEL-X",
            "X_ANTHROPIC_MODEL",
            "DISABLE_TELEMETR",
            "DISABLE_TELEMETRY_X",
            "DISABLE_TELEMETRY ",
            "ANTHROPIC_MODEL\n",
            "\nANTHROPIC_MODEL",
        ],
    )
    def test_env_name_near_miss_of_an_allowed_name_is_rejected(
        self, tmp_path: Path, near_miss_name: str
    ) -> None:
        """Literal near-misses of the allowed names, so a prefix match, a
        case-insensitive match, or a line-anchored match each fail one of
        these."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {near_miss_name: "x"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "outside the allowed set" in result.stderr
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.parametrize(
        "allowed_name",
        sorted(ENV_ALLOWED_NAMES),
    )
    def test_each_allowed_env_name_with_a_string_value_is_accepted(
        self, tmp_path: Path, allowed_name: str
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {allowed_name: "some-value"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["env"][allowed_name] == "some-value"

    def test_env_refusal_names_the_allowed_set_and_the_shell_profile_alternative(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"HTTPS_PROXY": "http://proxy.example"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        for allowed_name in ENV_ALLOWED_NAMES:
            assert allowed_name in result.stderr
        assert "shell profile" in result.stderr

    def test_the_allowed_set_the_script_enforces_equals_the_pinned_set(self) -> None:
        """An eighth name added to the script fails here even when no deny
        fixture names it."""
        script_names = _env_allowed_names_from_render_settings()

        assert len(script_names) == len(set(script_names)), f"duplicate allowed names: {script_names}"
        assert set(script_names) == ENV_ALLOWED_NAMES

    def test_env_mixing_an_allowed_name_with_a_disallowed_one_is_rejected_and_keeps_prior_render(
        self, tmp_path: Path
    ) -> None:
        """A guard that accepted when any key was allowed would render this."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        prior_render = '{"prior": "render"}\n'
        (config_dir / "settings.json").write_text(prior_render)
        _write_json(
            config_dir / "settings.overlay.json",
            {"env": {"DISABLE_TELEMETRY": "1", "NODE_OPTIONS": "x"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        # NODE_OPTIONS sorts after the allowed name, so a check that looked
        # only at the first key would accept this overlay. The refusal names
        # it alone, not the allowed name beside it.
        assert "}: NODE_OPTIONS -- refusing" in result.stderr
        assert (config_dir / "settings.json").read_text() == prior_render

    def test_empty_env_object_is_accepted(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text())["env"] == {}
        assert "dropped" not in result.stderr

    @pytest.mark.parametrize(
        "credential_name",
        [
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_CLIENT_SECRET",
            "CLAUDE_CODE_SOME_KEY",
            "ANTHROPIC__KEY",
        ],
    )
    def test_refused_credential_shaped_env_key_never_echoes_its_value_and_keeps_prior_render(
        self, tmp_path: Path, credential_name: str
    ) -> None:
        """A credential-shaped name is refused as an unlisted name, the
        diagnostic names the key and never the value, and a prior render stays
        byte-identical."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        prior_content = json.dumps({"otherKey": "stale-value"})
        (config_dir / "settings.json").write_text(prior_content)
        _write_json(config_dir / "settings.overlay.json", {"env": {credential_name: "sk-ant-example"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "outside the allowed set" in result.stderr
        assert credential_name in result.stderr
        assert "shell profile" in result.stderr
        assert "sk-ant-example" not in result.stderr
        assert (config_dir / "settings.json").read_text() == prior_content

    def test_non_object_env_value_is_rejected_with_diagnostic_not_jq_error(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": "not-an-object"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "non-object env value" in result.stderr
        assert "jq: error" not in result.stderr

    def test_null_env_value_is_rejected_with_diagnostic_not_jq_error(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": None})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "non-object env value" in result.stderr
        assert "jq: error" not in result.stderr

    def test_allowed_env_name_with_non_string_value_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_TELEMETRY": 123}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "DISABLE_TELEMETRY" in result.stderr

    def test_non_object_permissions_value_is_rejected_with_diagnostic_not_jq_error(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": "not-an-object"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "non-object permissions value" in result.stderr
        assert "jq: error" not in result.stderr

    def test_null_permissions_value_is_rejected_with_diagnostic_not_jq_error(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": None})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "non-object permissions value" in result.stderr
        assert "jq: error" not in result.stderr

    def test_permissions_object_with_extra_key_is_rejected_naming_it(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"permissions": {"defaultMode": "plan", "deny": ["Bash(rm *)"]}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "deny" in result.stderr

    @pytest.mark.parametrize(
        "bad_key",
        [
            "hooks",
            "statusLine",
            "enabledPlugins",
            "extraKnownMarketplaces",
            "skillOverrides",
            "model",
            "notAllowed",
        ],
    )
    def test_overlay_top_level_key_outside_allowlist_is_rejected(self, tmp_path: Path, bad_key: str) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {bad_key: "x"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert bad_key in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_overlay_with_only_autoMode_and_skillListingBudgetFraction_is_accepted(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"autoMode": {"environment": ["$defaults"]}, "skillListingBudgetFraction": 0.2},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["autoMode"] == {"environment": ["$defaults"]}
        assert rendered["skillListingBudgetFraction"] == 0.2


class TestUnvalidatedOverlayValueShapes:
    """autoMode and skillListingBudgetFraction get no type/shape validation,
    unlike env and permissions above -- a deliberate scope decision, not an
    oversight. These pin today's accept-unconditionally behavior so a future
    tightening is a deliberate test change, not a silent behavior shift."""

    def test_non_numeric_skill_listing_budget_fraction_is_accepted_unconditionally(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"skillListingBudgetFraction": "not-a-number"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["skillListingBudgetFraction"] == "not-a-number"

    def test_out_of_range_skill_listing_budget_fraction_is_accepted_unconditionally(
        self, tmp_path: Path
    ) -> None:
        """docs/skills.md documents skillListingBudgetFraction as a fraction
        of the context window (default 0.01). This test's 2.5 value is
        outside the implied [0,1] range and still passes through today."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"skillListingBudgetFraction": 2.5})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["skillListingBudgetFraction"] == 2.5

    def test_non_object_auto_mode_is_accepted_unconditionally(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"autoMode": "not-an-object"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["autoMode"] == "not-an-object"


class TestDefaultModeNestedException:
    """permissions.defaultMode is a narrow, value-guarded nested
    exception outside the top-level allowlist."""

    @pytest.mark.parametrize("accepted_mode", ["default", "plan"])
    def test_accepted_default_mode_overrides_merged_result(self, tmp_path: Path, accepted_mode: str) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["a"]}})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": accepted_mode}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["permissions"]["defaultMode"] == accepted_mode
        assert rendered["permissions"]["deny"] == ["a"]

    def test_base_set_default_mode_is_overridden_by_overlay_accepted_value(self, tmp_path: Path) -> None:
        """Confirms the nested override applies on top of rule 1's
        wholesale permissions merge even when base already sets the path."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(
            config_dir / "settings.base.json",
            {"permissions": {"deny": ["a"], "defaultMode": "default"}},
        )
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "plan"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["permissions"]["defaultMode"] == "plan"
        assert rendered["permissions"]["deny"] == ["a"]

    def test_bypass_permissions_is_refused_naming_alternatives(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "bypassPermissions"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "bypassPermissions" in result.stderr
        assert "--permission-mode" in result.stderr
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.parametrize("refused_mode", ["bypassPermissions", "acceptEdits", "auto", "dontAsk"])
    def test_refusal_points_at_project_scope_settings_local_json_not_the_tracked_file(
        self, tmp_path: Path, refused_mode: str
    ) -> None:
        """The pointer must name the gitignored per-project file: the tracked
        .claude/settings.json would steer a consumer toward a committed,
        team-wide default mode."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": refused_mode}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "project-scope .claude/settings.local.json" in result.stderr
        assert ".claude/settings.json" not in result.stderr

    def test_accept_edits_is_refused(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "acceptEdits"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "acceptEdits" in result.stderr

    def test_auto_is_refused_pending_classifier_verification(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "auto"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert 'sets permissions.defaultMode="auto"' in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_dont_ask_is_refused_pending_verification(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "dontAsk"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "dontAsk" in result.stderr

    def test_unknown_default_mode_value_is_refused(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "not-a-real-mode"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "not-a-real-mode" in result.stderr

    @pytest.mark.parametrize(
        "non_string_mode",
        [
            ["plan"],
            ["default"],
            ["default", "plan"],
            [],
            1,
            True,
            None,
            {"mode": "plan"},
        ],
        ids=["array-plan", "array-default", "array-both", "empty-array", "number", "bool", "null", "object"],
    )
    def test_non_string_default_mode_is_refused_and_leaves_prior_render_untouched(
        self, tmp_path: Path, non_string_mode: object
    ) -> None:
        """jq's index() matches a subarray, so an array holding an accepted
        value would pass a bare membership check. Every non-string type must
        be refused before that check, with the prior render byte-identical."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        prior_content = json.dumps({"otherKey": "stale-value"})
        (config_dir / "settings.json").write_text(prior_content)
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": non_string_mode}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "non-string permissions.defaultMode" in result.stderr
        assert (config_dir / "settings.json").read_text() == prior_content

    def test_default_mode_does_not_reopen_deny(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["Bash(sudo *)"]}})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "plan"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["permissions"]["deny"] == ["Bash(sudo *)"]


# One entry per overlay refusal arm: (overlay content or raw text, stderr
# fragment naming the refusal). Every arm must leave a prior render untouched.
_OVERLAY_REFUSAL_FIXTURES = {
    "malformed-json": ("{not valid json", "not valid JSON"),
    "non-object-overlay": ([1, 2], "not a JSON object"),
    "key-outside-closed-set": ({"notAllowed": "x"}, "notAllowed"),
    "permissions-non-object": ({"permissions": "not-an-object"}, "non-object permissions value"),
    "permissions-null": ({"permissions": None}, "non-object permissions value"),
    "permissions-empty-object": ({"permissions": {}}, "does not set defaultMode"),
    "permissions-extra-key-only": ({"permissions": {"deny": []}}, "outside {defaultMode}: deny"),
    "permissions-extra-key-beside-mode": (
        {"permissions": {"defaultMode": "plan", "deny": []}},
        "outside {defaultMode}: deny",
    ),
    "default-mode-non-string": ({"permissions": {"defaultMode": ["plan"]}}, "non-string permissions.defaultMode"),
    "default-mode-bypass": ({"permissions": {"defaultMode": "bypassPermissions"}}, "not accepted"),
    "default-mode-accept-edits": ({"permissions": {"defaultMode": "acceptEdits"}}, "not accepted"),
    "default-mode-auto": ({"permissions": {"defaultMode": "auto"}}, "not accepted"),
    "default-mode-dont-ask": ({"permissions": {"defaultMode": "dontAsk"}}, "not accepted"),
    "env-non-object": ({"env": "not-an-object"}, "non-object env value"),
    "env-null": ({"env": None}, "non-object env value"),
    "env-name-outside-allowlist": ({"env": {"BASH_ENV": "x"}}, "outside the allowed set"),
    "env-credential-named": ({"env": {"ANTHROPIC_API_KEY": "sk-ant-example"}}, "outside the allowed set"),
    "env-non-string-value": ({"env": {"DISABLE_TELEMETRY": 1}}, "non-string env value(s) for: DISABLE_TELEMETRY"),
}


class TestRefusalPreservesPriorRender:
    """An overlay validation failure must not touch the pre-existing
    $target -- the property the whole plan exists to protect. A write placed
    after a late validation, or a check added after the merge, fails the arm
    it precedes."""

    _PRIOR_RENDER = {"permissions": {"deny": ["Bash(sudo *)"]}, "hooks": {"PreToolUse": []}}

    @pytest.mark.parametrize(
        ("overlay_content", "refusal_fragment"),
        list(_OVERLAY_REFUSAL_FIXTURES.values()),
        ids=list(_OVERLAY_REFUSAL_FIXTURES),
    )
    def test_each_overlay_refusal_leaves_the_prior_render_byte_identical(
        self, tmp_path: Path, overlay_content: object, refusal_fragment: str
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        target = config_dir / "settings.json"
        prior_content = json.dumps(self._PRIOR_RENDER)
        target.write_text(prior_content)
        overlay = config_dir / "settings.overlay.json"
        if isinstance(overlay_content, str):
            overlay.write_text(overlay_content)
        else:
            _write_json(overlay, overlay_content)

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert refusal_fragment in result.stderr
        assert "an existing settings.json keeps its previous deny rules and hooks" in result.stderr
        assert "base changes are not delivered until the overlay is fixed" in result.stderr
        assert target.read_text() == prior_content
        assert sorted(entry.name for entry in config_dir.iterdir()) == [
            "settings.base.json",
            "settings.json",
            "settings.overlay.json",
        ], "a refusal must not leave a temp file behind"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_overlay_leaves_the_prior_render_byte_identical(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        target = config_dir / "settings.json"
        prior_content = json.dumps(self._PRIOR_RENDER)
        target.write_text(prior_content)
        overlay = config_dir / "settings.overlay.json"
        _write_json(overlay, {"autoMode": {"environment": ["$defaults"]}})
        overlay.chmod(0o000)
        try:
            result = _run_script(config_dir=config_dir)
        finally:
            overlay.chmod(0o644)

        assert result.returncode != 0
        assert "not readable" in result.stderr
        assert target.read_text() == prior_content


class TestOverlayChmodHardening:
    """The overlay chmod runs before the validation checks below it, so
    rejection must not skip the hardening -- see render-settings.sh."""

    def test_overlay_is_chmod_600_after_a_successful_render(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "settings.overlay.json"
        _write_json(overlay, {"autoMode": {"environment": ["$defaults"]}})
        overlay.chmod(0o644)

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert (overlay.stat().st_mode & 0o777) == 0o600

    def test_overlay_is_chmod_600_when_malformed_json_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "settings.overlay.json"
        overlay.write_text("{not valid json")
        overlay.chmod(0o644)

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert (overlay.stat().st_mode & 0o777) == 0o600

    def test_overlay_is_chmod_600_when_disallowed_key_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "settings.overlay.json"
        _write_json(overlay, {"autoMode": {"environment": ["$defaults"]}, "notAllowed": "x"})
        overlay.chmod(0o644)

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert (overlay.stat().st_mode & 0o777) == 0o600

    def test_overlay_is_chmod_600_when_env_name_outside_allowlist_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "settings.overlay.json"
        _write_json(overlay, {"env": {"PATH": "/x"}})
        overlay.chmod(0o644)

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert (overlay.stat().st_mode & 0o777) == 0o600


class TestChmodPortability:
    """Verifies the leading-dash-to-./-prefix normalization keeps a
    dash-prefixed overlay filename from ever reaching chmod as a raw
    argument, regardless of which chmod variant runs it."""

    def test_dash_leading_overlay_argument_is_not_parsed_as_a_flag(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "-oops.json", {"autoMode": {"environment": ["$defaults"]}})

        result = _run_script("-oops.json", config_dir=config_dir, cwd=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["autoMode"] == {"environment": ["$defaults"]}

    def test_bsd_style_chmod_shim_reports_no_warning_and_leaves_mode_600(self, tmp_path: Path) -> None:
        """CI runs Linux, where the real chmod accepts a dash-prefixed
        argument unconditionally, so this regression needs a fake chmod that
        fails on one to make the normalization enforceable on every push."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "settings.overlay.json"
        _write_json(overlay, {"autoMode": {"environment": ["$defaults"]}})
        overlay.chmod(0o644)

        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_chmod = fake_bin / "chmod"
        fake_chmod.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  if [ "$arg" = "--" ]; then\n'
            '    echo "chmod: illegal option -- --" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "done\n"
            'exec /bin/chmod "$@"\n'
        )
        fake_chmod.chmod(0o755)

        result = _run_script(
            config_dir=config_dir,
            extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 0, result.stderr
        assert "could not chmod 600" not in result.stderr
        assert (overlay.stat().st_mode & 0o777) == 0o600

    def test_dash_leading_overlay_path_chmod_under_bsd_style_shim(self, tmp_path: Path) -> None:
        """Intersection of the two regressions above: a dash-leading overlay
        path must not reach the BSD-style chmod shim in a way that trips its
        -- rejection, since the dash-guard's ./-prefixing changes the exact
        argument chmod receives."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        overlay = config_dir / "-oops.json"
        _write_json(overlay, {"autoMode": {"environment": ["$defaults"]}})
        overlay.chmod(0o644)

        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_chmod = fake_bin / "chmod"
        fake_chmod.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  if [ "$arg" = "--" ]; then\n'
            '    echo "chmod: illegal option -- --" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "done\n"
            'exec /bin/chmod "$@"\n'
        )
        fake_chmod.chmod(0o755)

        result = _run_script(
            "-oops.json",
            config_dir=config_dir,
            cwd=config_dir,
            extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 0, result.stderr
        assert "could not chmod 600" not in result.stderr
        assert (overlay.stat().st_mode & 0o777) == 0o600


class TestDirectoryAtTarget:
    """A directory at $target must not silently absorb the rendered file."""

    def test_directory_at_target_refuses_render_without_moving_file_inside_it(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        target = config_dir / "settings.json"
        target.mkdir()

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert target.is_dir()
        assert list(target.iterdir()) == []


class TestWritePathFailures:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unwritable_config_dir_fails_loudly_with_no_partial_settings_json(
        self, tmp_path: Path
    ) -> None:
        """A read-only config_dir can't hold the mktemp temp file the render
        writes ahead of its atomic rename into settings.json -- this must
        fail loudly with render-settings.sh's own diagnostic convention, not
        mktemp's raw stderr. Covers only the total pre-write failure (no
        settings.json exists yet, so mktemp itself is the first thing that
        fails); see test_unwritable_config_dir_leaves_a_pre_existing_settings_json_untouched
        below for the failed-re-render-over-a-working-file case."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        config_dir.chmod(0o500)
        try:
            result = _run_script(config_dir=config_dir)
        finally:
            config_dir.chmod(0o755)

        assert result.returncode != 0
        assert "render-settings.sh:" in result.stderr
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unwritable_config_dir_leaves_a_pre_existing_settings_json_untouched(
        self, tmp_path: Path
    ) -> None:
        """The more realistic production shape: a machine that already has a
        working settings.json, then loses write access to config_dir (a
        permissions change, a full disk, a cross-device mv failure) before
        the next render. mktemp fails the same way as the no-prior-file case
        above, but here there's a valid file at risk of being clobbered --
        this pins that the failed render leaves it byte-identical rather
        than truncating or partially overwriting it. The prior file differs
        from the render's output, since an identical render exits before
        any write and so never reaches mktemp."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        prior_content = json.dumps({"otherKey": "stale-value", "theme": "dark"})
        (config_dir / "settings.json").write_text(prior_content)
        config_dir.chmod(0o500)
        try:
            result = _run_script(config_dir=config_dir)
        finally:
            config_dir.chmod(0o755)

        assert result.returncode != 0
        assert "render-settings.sh:" in result.stderr
        assert (config_dir / "settings.json").read_text() == prior_content


class TestBaseValidation:
    def test_base_valid_json_but_not_object_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", [1, 2])

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "not a JSON object" in result.stderr
        assert not (config_dir / "settings.json").exists()

    def test_base_malformed_json_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        (config_dir / "settings.base.json").write_text("{not valid json")

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert not (config_dir / "settings.json").exists()


class TestIdempotency:
    def test_second_render_of_unchanged_inputs_leaves_output_byte_identical(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"autoMode": {"environment": ["$defaults"]}})

        first = _run_script(config_dir=config_dir)
        assert first.returncode == 0, first.stderr
        first_hash = _sha256(config_dir / "settings.json")

        second = _run_script(config_dir=config_dir)
        assert second.returncode == 0, second.stderr
        second_hash = _sha256(config_dir / "settings.json")

        assert first_hash == second_hash

    def test_unchanged_render_does_not_rewrite_a_regular_settings_json(self, tmp_path: Path) -> None:
        """An identical render exits before the temp-file replace, so the
        file keeps its inode and mtime and an app write cannot be lost to a
        no-op rewrite."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        target = config_dir / "settings.json"
        first = _run_script(config_dir=config_dir)
        assert first.returncode == 0, first.stderr
        os.utime(target, ns=(1_000_000_000, 1_000_000_000))
        inode_before = target.stat().st_ino

        second = _run_script(config_dir=config_dir)

        assert second.returncode == 0, second.stderr
        assert target.stat().st_ino == inode_before
        assert target.stat().st_mtime_ns == 1_000_000_000

    def test_semantically_equal_prior_file_is_left_byte_identical(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        pretty_prior = json.dumps({"otherKey": "base-value"}, indent=4)
        (config_dir / "settings.json").write_text(pretty_prior)

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert (config_dir / "settings.json").read_text() == pretty_prior

    def test_changed_render_replaces_the_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "new-value"})
        (config_dir / "settings.json").write_text(json.dumps({"otherKey": "old-value"}))

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text()) == {"otherKey": "new-value"}

    def test_unchanged_render_still_replaces_a_symlink_with_a_regular_file(self, tmp_path: Path) -> None:
        """The skip applies only to a regular file: a symlink whose target
        already holds the rendered content is still replaced, so a later
        write cannot go through it."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        real_file = tmp_path / "real-settings.json"
        _write_json(real_file, {"otherKey": "base-value"})
        target = config_dir / "settings.json"
        target.symlink_to(real_file)

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert not target.is_symlink()
        assert json.loads(target.read_text()) == {"otherKey": "base-value"}

    def test_concurrent_renders_of_different_overlays_yield_one_racers_full_output(
        self, tmp_path: Path
    ) -> None:
        """Two real render-settings.sh subprocesses racing against the same
        config_dir -- the two-terminal-tabs-opening-at-once scenario
        ensure-settings-render.sh's rc hook can trigger -- each given its own
        overlay so the two computed merges genuinely differ. A byte-identical
        race (both racers given the same inputs) can't distinguish an atomic
        replace from a non-atomic one, since either racer's output would be
        indistinguishable from the other's; giving each racer a different
        overlay means settings.json must match one racer's full output.
        With payloads this small a plain redirect would also look atomic per
        racer, so the failure this smoke detects is a fixed temp name that
        collides between the racers and fails the loser's mv.
        The two processes are not forced to overlap, so the smoke catches
        that regression probabilistically, and a pass is not proof of
        atomicity."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        base = {"otherKey": "base-value"}
        _write_json(config_dir / "settings.base.json", base)

        overlay_a = tmp_path / "overlay-a.json"
        overlay_b = tmp_path / "overlay-b.json"
        _write_json(overlay_a, {"autoMode": {"environment": ["racer-a"]}})
        _write_json(overlay_b, {"autoMode": {"environment": ["racer-b"]}})
        expected_a = {**base, "autoMode": {"environment": ["racer-a"]}}
        expected_b = {**base, "autoMode": {"environment": ["racer-b"]}}

        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        procs = [
            subprocess.Popen(
                [str(_SCRIPT), str(overlay)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for overlay in (overlay_a, overlay_b)
        ]
        try:
            results = [proc.communicate(timeout=30) for proc in procs]
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

        for proc, (_, stderr) in zip(procs, results, strict=True):
            assert proc.returncode == 0, stderr

        # Whichever racer's mv wins, the result must be that racer's own
        # full merge output, never a hybrid -- the assertion a non-atomic
        # write (e.g. a `>` redirect to a shared temp name) would fail.
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered in (expected_a, expected_b), (
            "settings.json must match exactly one racer's full merge "
            f"output, not a hybrid of both; got {rendered!r}"
        )


class TestThemeTuiPreservation:
    """theme/tui are written directly into the live settings.json by
    Claude Code's /theme and /tui commands, not by base or overlay, so a
    render must carry forward the target's pre-existing values instead of
    discarding them. An instance of rule 3's general fallback, not a
    hardcoded two-key special case."""

    def test_prior_target_theme_and_tui_survive_the_render(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.json",
            {"theme": "dark", "tui": True, "otherKey": "stale-value"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {"otherKey": "base-value", "theme": "dark", "tui": True}

    def test_prior_target_theme_and_tui_are_not_overridden_by_unrelated_base_changes(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "new-base-value"})
        _write_json(
            config_dir / "settings.json",
            {"theme": "light", "tui": False, "otherKey": "old-base-value"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["theme"] == "light"
        assert rendered["tui"] is False
        assert rendered["otherKey"] == "new-base-value"

    def test_no_prior_target_renders_without_theme_or_tui_keys(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert "theme" not in rendered
        assert "tui" not in rendered

    def test_prior_target_without_theme_or_tui_keys_renders_without_them(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.json", {"otherKey": "stale-value"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert "theme" not in rendered
        assert "tui" not in rendered

    def test_prior_target_null_theme_is_omitted_while_tui_survives(self, tmp_path: Path) -> None:
        """A null-valued carried key is treated as absent, matching this
        mechanism's original theme/tui-only precedent, now applied to every
        top-level key rule 3 might carry forward."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.json",
            {"theme": None, "tui": "fullscreen", "otherKey": "stale-value"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert "theme" not in rendered
        assert rendered["tui"] == "fullscreen"

    def test_prior_target_with_only_theme_set_renders_without_a_tui_key(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.json", {"theme": "solarized"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["theme"] == "solarized"
        assert "tui" not in rendered

    def test_malformed_prior_target_is_tolerated_not_treated_as_a_render_failure(
        self, tmp_path: Path
    ) -> None:
        """$target is this script's own prior output, not user-supplied
        input -- a corrupted prior file must not block producing a good
        new one."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        (config_dir / "settings.json").write_text("{not valid json")

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {"otherKey": "base-value"}

    @pytest.mark.parametrize(
        "unusable_prior_text",
        ["", "   \n\t\n", "null", "[]", '"x"', "42", "true", '{"a": 1} {"b": 2}'],
        ids=["zero-byte", "whitespace-only", "null", "array", "string", "number", "bool", "two-values"],
    )
    def test_empty_or_non_object_prior_target_renders_as_no_prior_state(
        self, tmp_path: Path, unusable_prior_text: str
    ) -> None:
        """A zero-byte file (a truncated write) or a non-object JSON value
        carries nothing forward, and must not block the render that would
        repair it."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        (config_dir / "settings.json").write_text(unusable_prior_text)

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text()) == {"otherKey": "base-value"}

    def test_empty_base_render_still_repairs_a_non_object_prior_target(self, tmp_path: Path) -> None:
        """With an empty-object base, the render's output equals the
        fallback no-prior-state value, so only the prior-is-an-object check
        keeps the skip-write from leaving a garbage file in place."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {})
        (config_dir / "settings.json").write_text("[]")

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text()) == {}

    def test_dangling_symlink_prior_target_is_tolerated_and_replaced_with_a_regular_file(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        target = config_dir / "settings.json"
        target.symlink_to(tmp_path / "does-not-exist.json")

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert not target.is_symlink()
        rendered = json.loads(target.read_text())
        assert rendered == {"otherKey": "base-value"}

    def test_repeated_renders_with_unchanged_theme_and_tui_stay_byte_identical(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(
            config_dir / "settings.json",
            {"theme": "dark", "tui": True, "otherKey": "stale-value"},
        )

        first = _run_script(config_dir=config_dir)
        assert first.returncode == 0, first.stderr
        first_hash = _sha256(config_dir / "settings.json")

        second = _run_script(config_dir=config_dir)
        assert second.returncode == 0, second.stderr
        second_hash = _sha256(config_dir / "settings.json")

        assert first_hash == second_hash


class TestSymlinkWriteThroughRefusal:
    def test_target_symlink_is_replaced_not_written_through(self, tmp_path: Path) -> None:
        """The critical write-safety case: if settings.json is still a
        symlink into some other file when the render runs, the render must
        replace the symlink itself -- never write through it. The canary
        file's own "canary" key is an unclaimed top-level key, so it
        carries forward under rule 3 like any other prior-render key --
        that's the widened mechanism working as designed, not a leak."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})

        canary = tmp_path / "canary.json"
        canary_original_content = json.dumps({"canary": "untouched"})
        canary.write_text(canary_original_content)
        target = config_dir / "settings.json"
        target.symlink_to(canary)

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert canary.read_text() == canary_original_content
        assert not target.is_symlink()
        assert json.loads(target.read_text()) == {
            "otherKey": "base-value",
            "canary": "untouched",
        }


class TestShallowCarryForward:
    """Eight cases covering carry-forward rules 1-4."""

    def test_base_owned_key_wins_over_prior_files_value(self, tmp_path: Path) -> None:
        """Rule 1: a key base sets wins over whatever the prior render had."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["current"]}})
        _write_json(config_dir / "settings.json", {"permissions": {"deny": ["stale"]}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["permissions"] == {"deny": ["current"]}

    def test_nested_key_removed_from_base_does_not_survive_from_prior_file(
        self, tmp_path: Path
    ) -> None:
        """Rule 1, the deep-merge resurrection guard: a nested path present
        in the prior render but absent from base's current permissions
        object must not survive -- top-level-only merging, never jq `*`."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["a"]}})
        _write_json(
            config_dir / "settings.json",
            {"permissions": {"deny": ["a", "b", "old-removed-hook-path"]}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["permissions"] == {"deny": ["a"]}

    def test_overlay_allowed_key_absent_from_overlay_stays_absent(self, tmp_path: Path) -> None:
        """Rule 2: deleting autoMode from the overlay (the prescribed way to
        disable it) must actually take effect on the next render, not
        resurrect the prior render's value."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.json",
            {"autoMode": {"environment": ["$defaults"]}, "otherKey": "v"},
        )
        # No settings.overlay.json this render.

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert "autoMode" not in rendered

    def test_unclaimed_top_level_key_carries_forward(self, tmp_path: Path) -> None:
        """Rule 3, the general fallback: model, once absent from both base
        and overlay, needs no key-specific case -- it carries forward
        exactly like any other unclaimed top-level key."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.json", {"model": "opus", "otherKey": "v"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["model"] == "opus"

    @pytest.mark.parametrize("plugin_key", ["enabledPlugins", "extraKnownMarketplaces"])
    def test_plugin_keys_base_does_not_define_carry_forward(self, tmp_path: Path, plugin_key: str) -> None:
        """register-marketplace.sh reads both keys from the rendered file, so
        they must survive a render rather than be treated as base-owned."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.json", {plugin_key: {"example": True}, "otherKey": "v"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text())[plugin_key] == {"example": True}

    def test_theme_and_tui_still_preserved_under_the_widened_rule(self, tmp_path: Path) -> None:
        """Rule 3: widening carry-forward from a hardcoded theme/tui pair to
        every top-level key is not a regression on the original behavior --
        see TestThemeTuiPreservation for the full theme/tui suite."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.json", {"theme": "dark", "tui": True, "otherKey": "v"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["theme"] == "dark"
        assert rendered["tui"] is True

    def test_rule4_dotted_paths_survive_when_overlay_sets_env_without_them(
        self, tmp_path: Path
    ) -> None:
        """Rule 4: an overlay that sets env without the two app-written
        dotted paths must not drop them -- rules 1-3 alone would, since env
        itself is rule 2's territory and the overlay's own env object wins
        wholesale over the prior render's."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_TELEMETRY": "1"}})
        _write_json(
            config_dir / "settings.json",
            {
                "env": {
                    "CLAUDE_CODE_EFFORT_LEVEL": "high",
                    "ANTHROPIC_MODEL": "opus",
                    "DISABLE_TELEMETRY": "0",
                },
                "otherKey": "v",
            },
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["env"] == {
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_EFFORT_LEVEL": "high",
            "ANTHROPIC_MODEL": "opus",
        }

    def test_overlay_set_rule4_path_wins_over_prior_files_value(self, tmp_path: Path) -> None:
        """Rule 4's conditional: an unconditional rule 4 would let the stale
        prior-file value silently overwrite the overlay's own current
        setting."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"ANTHROPIC_MODEL": "new-value"}})
        _write_json(
            config_dir / "settings.json",
            {"env": {"ANTHROPIC_MODEL": "old-stale-value"}, "otherKey": "v"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["env"]["ANTHROPIC_MODEL"] == "new-value"

    def test_rule4_creates_a_fresh_env_object_when_merged_result_has_none(
        self, tmp_path: Path
    ) -> None:
        """Rule 4, the "no overlay at all" branch: the merged result from
        rules 1-3 has no env key at all (no overlay sets one, and env is
        rule 2's territory so it can't carry forward), yet the prior
        render's env.CLAUDE_CODE_EFFORT_LEVEL must still survive."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.json",
            {"env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}, "otherKey": "v"},
        )
        # No settings.overlay.json this render.

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {"otherKey": "v", "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}


class TestRuleFourGuardedKeysCrossCheck:
    def test_rule4_dotted_paths_match_print_guarded_keys_dotted_subset(self) -> None:
        result = subprocess.run(
            [str(_GUARD_HOOK), "--print-guarded-keys"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        guarded_keys = json.loads(result.stdout)
        dotted_subset = sorted(k for k in guarded_keys if "." in k)
        assert sorted(_rule4_dotted_paths_from_render_settings()) == dotted_subset


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicate JSON keys: {duplicates}"
    return dict(pairs)


class TestBaseFileShape:
    def test_base_file_has_no_duplicate_keys_at_any_depth(self) -> None:
        """json.load keeps only the last of two same-named keys, so a duplicate
        silently drops the first block's entries from the rendered file (a
        duplicated `permissions.ask` once dropped a settings-file-edit ask
        rule this way)."""
        json.loads(_SETTINGS_BASE_JSON.read_text(), object_pairs_hook=_reject_duplicate_keys)

    @pytest.mark.parametrize(
        "text",
        [
            '{"a": 1, "a": 2}',
            '{"outer": {"a": 1, "a": 2}}',
            '{"outer": [{"x": 1}, {"a": 1, "a": 2}]}',
        ],
        ids=["top-level", "nested-object", "object-in-list"],
    )
    def test_duplicate_key_helper_rejects_a_duplicate_at_every_depth(self, text: str) -> None:
        with pytest.raises(AssertionError, match="duplicate JSON keys"):
            json.loads(text, object_pairs_hook=_reject_duplicate_keys)

    def test_duplicate_key_helper_accepts_a_repeated_array_value(self) -> None:
        json.loads('{"a": [1, 1], "b": 1}', object_pairs_hook=_reject_duplicate_keys)


class TestBaseOverlayDisjointness:
    """Regression guard: git merge-tree's auto-merge can silently land one
    of the overlay's allowed keys into base with no conflict marker.
    Mirrors TestBaseKeyPlacementDisjointness in
    test_guard_settings_session_keys.py."""

    def test_base_top_level_keys_disjoint_from_overlay_allowed_keys(self) -> None:
        base_keys = set(json.loads(_SETTINGS_BASE_JSON.read_text()).keys())
        overlap = base_keys & OVERLAY_ALLOWED_TOP_LEVEL_KEYS
        assert overlap == set(), f"settings.base.json sets overlay-allowed key(s): {overlap}"


# The real base's top-level keys that are deliberately not base-owned: if base
# drops one, the live file's value carries forward instead of being removed.
# A new base key must be added here or to render-settings.sh's base-owned set,
# so the choice is made on purpose.
BASE_KEYS_DELIBERATELY_NOT_BASE_OWNED = {
    "attribution",
    "disableArtifact",
    "disableWorkflows",
    "syncClaudeAiSkills",
}


def _base_owned_keys_from_render_settings() -> list[str]:
    result = subprocess.run(
        [str(_SCRIPT), "--print-base-owned-keys"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestBaseOwnedKeysBinding:
    """render-settings.sh's base-owned key set, bound to the real base file."""

    def test_base_owned_keys_not_defined_by_the_real_base_are_exactly_the_expected_set(self) -> None:
        """A base-owned key that base does not define is deleted on every
        render, so none may be listed: the expected set is empty."""
        base_keys = set(json.loads(_SETTINGS_BASE_JSON.read_text()))
        undefined_but_owned = set(_base_owned_keys_from_render_settings()) - base_keys
        assert undefined_but_owned == set()

    def test_every_real_base_key_is_base_owned_or_deliberately_carried_forward(self) -> None:
        base_keys = set(json.loads(_SETTINGS_BASE_JSON.read_text()))
        base_owned = set(_base_owned_keys_from_render_settings())
        assert base_keys - base_owned == BASE_KEYS_DELIBERATELY_NOT_BASE_OWNED

    def test_real_base_renders_to_itself_with_no_overlay_and_no_prior_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        shutil.copy(_SETTINGS_BASE_JSON, config_dir / "settings.base.json")
        real_base = json.loads(_SETTINGS_BASE_JSON.read_text())

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text()) == real_base

    def test_real_base_floor_survives_a_prior_file_that_tampered_with_it(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        shutil.copy(_SETTINGS_BASE_JSON, config_dir / "settings.base.json")
        real_base = json.loads(_SETTINGS_BASE_JSON.read_text())
        _write_json(
            config_dir / "settings.json",
            {"permissions": {"deny": []}, "hooks": {}, "statusLine": {"type": "none"}, "theme": "dark"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        for floor_key in ("permissions", "hooks", "statusLine", "skillOverrides"):
            assert rendered[floor_key] == real_base[floor_key], floor_key
        assert rendered["theme"] == "dark"

    def test_base_owned_key_absent_from_base_is_dropped_and_named_not_carried(self, tmp_path: Path) -> None:
        """A base-owned key never comes from the prior file, even when base
        no longer defines it, and the drop is announced."""
        for base_owned_key in _base_owned_keys_from_render_settings():
            config_dir = tmp_path / f"cfg-{base_owned_key}"
            config_dir.mkdir()
            _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
            _write_json(config_dir / "settings.json", {"otherKey": "v", base_owned_key: "stale"})

            result = _run_script(config_dir=config_dir)

            assert result.returncode == 0, result.stderr
            assert base_owned_key not in json.loads((config_dir / "settings.json").read_text())
            assert f"dropped top-level keys: {base_owned_key}" in result.stderr


class TestGeneratedFilesAreGitignored:
    """The rendered settings.json and the overlay (which can hold env values)
    sit inside the stow package directory when a session writes through a
    dangling symlink, so neither may be stageable from there."""

    @pytest.mark.parametrize(
        "ignored_path",
        [
            "claude/.claude/settings.json",
            "claude/.claude/settings.json.AbC123",
            "claude/.claude/settings.overlay.json",
        ],
    )
    def test_generated_file_in_the_package_directory_is_ignored(self, ignored_path: str) -> None:
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "check-ignore", "-q", ignored_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, f"{ignored_path} is not gitignored: {result.stderr}"

    def test_tracked_base_file_is_not_ignored(self) -> None:
        # --no-index: without it, check-ignore never reports a tracked path, so
        # this case could not fail whatever .gitignore says.
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "check-ignore", "-q", "--no-index", "claude/.claude/settings.base.json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 1


class TestEnabledKeyDeletion:
    """enabled has no consumer anymore and no special-cased handling --
    an overlay carrying it is refused the same as any other unrecognized
    top-level key."""

    def test_enabled_false_is_refused_as_an_unrecognized_key(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"enabled": False})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "skillListingBudgetFraction}: enabled -- refusing to render" in result.stderr
        assert not (config_dir / "settings.json").exists()


class TestStderrDisclosure:
    """The stderr-disclosure contract: change-triggered, and no env value ever
    printed. Env keys and top-level keys are named by key only; dropped
    permissions entries print their rule text, which is what lets a user
    recover a dropped rule."""

    def test_no_change_produces_no_disclosure_line(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"autoMode": {"environment": ["$defaults"]}})

        first = _run_script(config_dir=config_dir)
        assert first.returncode == 0, first.stderr

        second = _run_script(config_dir=config_dir)
        assert second.returncode == 0, second.stderr
        assert "this render changed settings.json" not in second.stderr

    def test_category_a_names_carried_forward_top_level_keys(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v1"})
        _write_json(config_dir / "settings.json", {"theme": "dark", "otherKey": "v0"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "this render changed settings.json" in result.stderr
        assert "carried forward top-level keys: theme" in result.stderr

    def test_category_b_names_newly_applied_overlay_env_keys_by_dotted_path(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_TELEMETRY": "https://x"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "env.DISABLE_TELEMETRY" in result.stderr

    def test_category_c_names_overlay_set_default_mode(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "plan"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "permissions.defaultMode=plan" in result.stderr

    def test_combined_categories_a_and_b_both_named_in_one_render(self, tmp_path: Path) -> None:
        """Closes the early-return-after-first-match gap: a newly-added
        overlay env key must not hide behind an unrelated, simultaneous,
        legitimate top-level carry-forward."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_TELEMETRY": "https://x"}})
        _write_json(config_dir / "settings.json", {"theme": "dark", "otherKey": "v"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "carried forward top-level keys: theme" in result.stderr
        assert "env.DISABLE_TELEMETRY" in result.stderr

    def test_env_value_never_appears_in_disclosure_only_the_dotted_path_does(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"env": {"DISABLE_TELEMETRY": "https://secret-marker.example"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "env.DISABLE_TELEMETRY" in result.stderr
        assert "secret-marker.example" not in result.stderr

    def test_changed_overlay_env_value_names_the_key_but_not_either_value(self, tmp_path: Path) -> None:
        """The redirect shape: a key already in the prior render whose value
        the overlay now changes."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"env": {"DISABLE_TELEMETRY": "https://new-marker.example"}},
        )
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "v", "env": {"DISABLE_TELEMETRY": "https://old-marker.example"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "overlay env keys applied or changed: env.DISABLE_TELEMETRY" in result.stderr
        assert "new-marker.example" not in result.stderr
        assert "old-marker.example" not in result.stderr

    def test_unchanged_overlay_env_value_is_not_named_when_another_key_changes(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "new"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_TELEMETRY": "https://same.example"}})
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "old", "env": {"DISABLE_TELEMETRY": "https://same.example"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text())["otherKey"] == "new"
        assert "env." not in result.stderr

    def test_dropped_top_level_key_is_named_without_its_value(self, tmp_path: Path) -> None:
        """Rule 2: the overlay no longer carries env, so the prior render's
        env is dropped. The drop is named by key, never by value."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "v", "env": {"DISABLE_TELEMETRY": "https://dropped-marker.example"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "dropped top-level keys: env" in result.stderr
        assert "dropped-marker.example" not in result.stderr

    def test_dropped_permissions_entries_are_named(self, tmp_path: Path) -> None:
        """Rule 1 replaces permissions wholesale, so a user-added entry in the
        live file is dropped, including a tightening deny entry, and the drop
        is announced."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(
            config_dir / "settings.base.json",
            {"permissions": {"deny": ["Bash(sudo *)"], "allow": ["Read"]}},
        )
        _write_json(
            config_dir / "settings.json",
            {
                "permissions": {
                    "deny": ["Bash(sudo *)", "Bash(user-added-deny)"],
                    "allow": ["Read", "Bash(user-added-allow)"],
                    "defaultMode": "acceptEdits",
                }
            },
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert 'permissions.deny["Bash(user-added-deny)"]' in result.stderr
        assert 'permissions.allow["Bash(user-added-allow)"]' in result.stderr
        assert "permissions.defaultMode" in result.stderr
        assert "acceptEdits" not in result.stderr
        assert 'permissions.deny["Bash(sudo *)"]' not in result.stderr

    def test_dropped_permissions_array_entries_are_named_when_base_has_no_such_field(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["a"]}})
        _write_json(config_dir / "settings.json", {"permissions": {"deny": ["a"], "ask": ["Edit"]}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert 'permissions.ask["Edit"]' in result.stderr

    def test_dropped_env_key_under_a_retained_env_object_is_named_without_its_value(
        self, tmp_path: Path
    ) -> None:
        """Rule 4 re-applies env.ANTHROPIC_MODEL, so `env` stays in the result
        and the top-level drop disclosure never fires. The prior env keys the
        render removes from that retained object are still named, by key
        only."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.json",
            {
                "otherKey": "v",
                "env": {
                    "ANTHROPIC_MODEL": "kept-marker",
                    "HTTPS_PROXY": "https://dropped-proxy-marker.example",
                },
            },
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text())["env"] == {
            "ANTHROPIC_MODEL": "kept-marker"
        }
        assert "dropped env keys: env.HTTPS_PROXY" in result.stderr
        assert "env.ANTHROPIC_MODEL" not in result.stderr
        assert "dropped-proxy-marker.example" not in result.stderr

    def test_dropped_env_key_is_named_next_to_a_newly_applied_overlay_env_key(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_ERROR_REPORTING": "1"}})
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "v", "env": {"DISABLE_TELEMETRY": "1"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "dropped env keys: env.DISABLE_TELEMETRY" in result.stderr
        assert "overlay env keys applied or changed: env.DISABLE_ERROR_REPORTING" in result.stderr

    def test_env_keys_the_render_keeps_are_not_named_as_dropped(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "new"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"DISABLE_TELEMETRY": "https://same.example"}})
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "old", "env": {"DISABLE_TELEMETRY": "https://same.example"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "dropped env keys" not in result.stderr

    def test_a_wholly_dropped_env_object_is_named_once_as_a_top_level_key(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "v", "env": {"DISABLE_TELEMETRY": "1"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "dropped top-level keys: env" in result.stderr
        assert "dropped env keys" not in result.stderr

    def test_a_wholly_dropped_permissions_object_is_named_once_as_a_top_level_key(
        self, tmp_path: Path
    ) -> None:
        """Base ships no permissions, so the prior object is dropped whole.
        It is named once as the top-level key, never again per entry."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.json",
            {"otherKey": "v", "permissions": {"allow": ["Bash(user-added-allow)"], "defaultMode": "plan"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "dropped top-level keys: permissions" in result.stderr
        assert "dropped permissions entries" not in result.stderr
        assert "user-added-allow" not in result.stderr

    def test_an_empty_prior_env_object_is_not_reported_as_dropped(self, tmp_path: Path) -> None:
        """An empty env object holds nothing to lose, so reporting it would
        dilute the drop lines that matter."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.json", {"otherKey": "v", "env": {}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "env" not in json.loads((config_dir / "settings.json").read_text())
        assert "dropped" not in result.stderr

    @pytest.mark.parametrize(
        "dropped_rule",
        [
            'Bash(grep -E "x\\|y" *)',
            "Bash(echo h\u00e9llo)",
            {"tool": "obj", "args": ["a", "b"]},
        ],
        ids=["quote-and-backslash", "non-ascii", "object-entry"],
    )
    def test_dropped_permissions_rule_text_round_trips_through_json(
        self, tmp_path: Path, dropped_rule: object
    ) -> None:
        """The printed rule text is what lets a user recover the rule, so the
        bracketed text must parse back to exactly the dropped value."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"allow": []}})
        _write_json(config_dir / "settings.json", {"permissions": {"allow": [dropped_rule]}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        disclosure_line = result.stderr.strip().splitlines()[-1]
        bracketed = disclosure_line.split("dropped permissions entries: permissions.allow[", 1)[1]
        assert json.loads(bracketed.removesuffix("]")) == dropped_rule

    @pytest.mark.parametrize(
        ("floor_key", "user_edited_value"),
        [
            ("hooks", {"PreToolUse": [], "PostToolUse": [{"matcher": "Bash", "hooks": []}]}),
            ("statusLine", {"type": "command", "command": "user-statusline"}),
            ("skillOverrides", {"base-skill": "off", "user-skill": "name-only"}),
        ],
    )
    def test_user_edit_under_a_base_owned_nested_key_is_reverted_without_a_message(
        self, tmp_path: Path, floor_key: str, user_edited_value: object
    ) -> None:
        """The disclosure does not look inside hooks, statusLine, or
        skillOverrides: base's value replaces the whole prior value and
        stderr stays silent. docs/auto-mode.md and docs/scripts.md state this."""
        base = {
            "hooks": {"PreToolUse": []},
            "statusLine": {"type": "command", "command": "base-statusline"},
            "skillOverrides": {"base-skill": "off"},
        }
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", base)
        _write_json(config_dir / "settings.json", {**base, floor_key: user_edited_value})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / "settings.json").read_text()) == base
        assert result.stderr == ""

    @pytest.mark.parametrize("base_defined_key", sorted(BASE_KEYS_DELIBERATELY_NOT_BASE_OWNED))
    def test_live_edit_to_a_base_defined_key_is_reverted_without_a_message(
        self, tmp_path: Path, base_defined_key: str
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        shutil.copy(_SETTINGS_BASE_JSON, config_dir / "settings.base.json")
        real_base = json.loads(_SETTINGS_BASE_JSON.read_text())
        _write_json(config_dir / "settings.json", {base_defined_key: "user-edited-marker"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered[base_defined_key] == real_base[base_defined_key]
        assert "dropped" not in result.stderr
        assert "carried forward" not in result.stderr
        assert "user-edited-marker" not in result.stderr

    @pytest.mark.parametrize("prior_state", ["absent", "dangling-symlink"])
    def test_first_render_with_no_readable_prior_file_names_nothing(
        self, tmp_path: Path, prior_state: str
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        shutil.copy(_SETTINGS_BASE_JSON, config_dir / "settings.base.json")
        if prior_state == "dangling-symlink":
            (config_dir / "settings.json").symlink_to(config_dir / "no-such-file.json")

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert not (config_dir / "settings.json").is_symlink()

    def test_steady_state_carry_of_an_already_carried_key_is_silent(self, tmp_path: Path) -> None:
        """Pins that a key carried on a previous render and unchanged since
        produces no disclosure and no write, since the output equals the
        prior file."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.json", {"otherKey": "v", "apiKeyHelper": "helper"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_carried_forward_key_value_never_appears_in_disclosure_only_the_name_does(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v1"})
        _write_json(
            config_dir / "settings.json",
            {"theme": "should-not-leak-in-stderr", "otherKey": "v0"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "carried forward top-level keys: theme" in result.stderr
        assert "should-not-leak-in-stderr" not in result.stderr
