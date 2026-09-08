"""Tests for render-settings.sh.

Each test builds its own scratch $CLAUDE_CONFIG_DIR under tmp_path and
invokes the real script via subprocess. Most tests need no shim, since the
script's main external dependency is jq; TestChmodPortability's CI-portable
regression case is the one exception, prepending a fake chmod that
reproduces BSD chmod's `--` rejection to PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "render-settings.sh"
_SETTINGS_BASE_JSON = Path(__file__).resolve().parents[2] / "settings.base.json"
_GUARD_HOOK = Path(__file__).resolve().parents[2] / "hooks" / "guard-settings-session-keys.sh"

# The overlay's closed top-level allowlist (M5), excluding the conditionally
# admissible `permissions` -- see TestBaseOverlayDisjointness, which mirrors
# TestBaseKeyPlacementDisjointness in test_guard_settings_session_keys.py.
OVERLAY_ALLOWED_TOP_LEVEL_KEYS = {"autoMode", "env", "skillListingBudgetFraction"}


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
    """Closed top-level allowlist plus the env namespace-rule guard."""

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
        ],
    )
    def test_env_name_outside_namespace_is_rejected(self, tmp_path: Path, bad_name: str) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {bad_name: "x"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert bad_name in result.stderr
        assert not (config_dir / "settings.json").exists()

    @pytest.mark.parametrize(
        "good_name",
        [
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_ENABLE_TELEMETRY",
            "DISABLE_TELEMETRY",
        ],
    )
    def test_env_name_matching_namespace_with_string_value_is_accepted(
        self, tmp_path: Path, good_name: str
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {good_name: "some-value"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["env"][good_name] == "some-value"

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

    def test_namespace_matching_env_name_with_non_string_value_is_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"env": {"ANTHROPIC_BASE_URL": 123}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "ANTHROPIC_BASE_URL" in result.stderr

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
        """Confirms M11's nested override applies on top of rule 1's
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
        assert "auto" in result.stderr

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

    def test_default_mode_does_not_reopen_deny(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["Bash(sudo *)"]}})
        _write_json(config_dir / "settings.overlay.json", {"permissions": {"defaultMode": "plan"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["permissions"]["deny"] == ["Bash(sudo *)"]


class TestRefusalPreservesPriorRender:
    """An overlay validation failure must not touch the pre-existing
    $target -- the property the whole plan exists to protect."""

    def test_overlay_validation_failure_leaves_prior_target_byte_identical(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"permissions": {"deny": ["Bash(sudo *)"]}})
        target = config_dir / "settings.json"
        prior_content = json.dumps(
            {"permissions": {"deny": ["Bash(sudo *)"]}, "hooks": {"PreToolUse": []}}
        )
        target.write_text(prior_content)
        _write_json(config_dir / "settings.overlay.json", {"notAllowed": "x"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
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

    def test_overlay_is_chmod_600_when_env_namespace_violation_is_rejected(self, tmp_path: Path) -> None:
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
    """BSD chmod does not accept -- as an end-of-options marker."""

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
        """CI runs Linux, where GNU chmod accepts -- unconditionally and this
        regression would still pass with the bug present. This shim
        reproduces BSD chmod's -- rejection so the fix is enforceable on
        every push, not just a one-time manual macOS run."""
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


class TestThemeTuiPreservation:
    """theme/tui are written directly into the live settings.json by
    Claude Code's /theme and /tui commands, not by base or overlay, so a
    render must carry forward the target's pre-existing values instead of
    discarding them. Now an instance of M3's general rule-3 fallback, not a
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
        carries forward under M3 rule 3 like any other prior-render key --
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
        """Rule 2, the regression test for the bug plan-architect found:
        deleting autoMode from the overlay (M6's own prescribed way to
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
        and overlay (M4), needs no key-specific case -- it carries forward
        exactly like any other unclaimed top-level key."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(config_dir / "settings.json", {"model": "opus", "otherKey": "v"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered["model"] == "opus"

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
        """Rule 4's conditional, the regression test for the round-3
        BLOCKER: an unconditional rule 4 would let the stale prior-file
        value silently overwrite the overlay's own current setting."""
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
        )
        assert result.returncode == 0, result.stderr
        guarded_keys = json.loads(result.stdout)
        dotted_subset = sorted(k for k in guarded_keys if "." in k)
        assert sorted(_rule4_dotted_paths_from_render_settings()) == dotted_subset


class TestBaseOverlayDisjointness:
    """Regression guard: git merge-tree's auto-merge can silently land one
    of the overlay's allowed keys into base with no conflict marker.
    Mirrors TestBaseKeyPlacementDisjointness in
    test_guard_settings_session_keys.py."""

    def test_base_top_level_keys_disjoint_from_overlay_allowed_keys(self) -> None:
        base_keys = set(json.loads(_SETTINGS_BASE_JSON.read_text()).keys())
        overlap = base_keys & OVERLAY_ALLOWED_TOP_LEVEL_KEYS
        assert overlap == set(), f"settings.base.json sets overlay-allowed key(s): {overlap}"


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
        assert "enabled" in result.stderr
        assert not (config_dir / "settings.json").exists()


class TestStderrDisclosure:
    """The canonical stderr-disclosure deliverable specified in Phase 1's
    render-settings.sh bullet: change-triggered, name-only, never-value."""

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
        _write_json(config_dir / "settings.overlay.json", {"env": {"ANTHROPIC_BASE_URL": "https://x"}})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "env.ANTHROPIC_BASE_URL" in result.stderr

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
        _write_json(config_dir / "settings.overlay.json", {"env": {"ANTHROPIC_BASE_URL": "https://x"}})
        _write_json(config_dir / "settings.json", {"theme": "dark", "otherKey": "v"})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "carried forward top-level keys: theme" in result.stderr
        assert "env.ANTHROPIC_BASE_URL" in result.stderr

    def test_env_value_never_appears_in_disclosure_only_the_dotted_path_does(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"otherKey": "v"})
        _write_json(
            config_dir / "settings.overlay.json",
            {"env": {"ANTHROPIC_AUTH_TOKEN": "sk-secret-marker-token"}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        assert "env.ANTHROPIC_AUTH_TOKEN" in result.stderr
        assert "sk-secret-marker-token" not in result.stderr

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
