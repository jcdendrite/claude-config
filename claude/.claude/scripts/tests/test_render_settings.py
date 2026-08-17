"""Tests for render-settings.sh.

Each test builds its own scratch $CLAUDE_CONFIG_DIR under tmp_path and
invokes the real script via subprocess -- no shim needed, since the script's
only external dependency is jq itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "render-settings.sh"


def _write_json(path: Path, content: dict | list) -> None:
    path.write_text(json.dumps(content))


def _run_script(*args: str, config_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    def test_overlay_key_wins_over_base_key(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"enabled": False, "otherKey": "base-value"})
        _write_json(config_dir / "settings.overlay.json", {"enabled": True})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {"enabled": True, "otherKey": "base-value"}

    def test_overlay_array_replaces_base_array_rather_than_concatenating(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(
            config_dir / "settings.base.json",
            {"autoMode": {"environment": ["$defaults", "base-only-entry"]}},
        )
        _write_json(
            config_dir / "settings.overlay.json",
            {"enabled": True, "autoMode": {"environment": ["$defaults", "overlay-entry"]}},
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
        _write_json(alt_overlay, {"enabled": True})

        result = _run_script(str(alt_overlay), config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert rendered == {"enabled": True, "otherKey": "base-value"}

    def test_overlay_autoMode_without_enabled_key_survives_unmodified(self, tmp_path: Path) -> None:
        """An absent `enabled` key must not trigger the strip -- pins the
        `null == false` branch of the strip condition, distinct from the
        explicit `enabled: false` case covered elsewhere."""
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


class TestEnabledFalseStripsAutoMode:
    def test_strips_autoMode_from_base_when_overlay_disables(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(
            config_dir / "settings.base.json",
            {"autoMode": {"environment": ["$defaults"]}, "otherKey": "base-value"},
        )
        _write_json(config_dir / "settings.overlay.json", {"enabled": False})

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert "autoMode" not in rendered
        assert rendered["otherKey"] == "base-value"

    def test_strips_autoMode_even_when_overlay_itself_supplies_one(self, tmp_path: Path) -> None:
        """enabled:false must fail closed regardless of what the overlay's
        own autoMode says -- not just when the overlay omits autoMode."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        _write_json(config_dir / "settings.base.json", {"autoMode": {"environment": ["$defaults"]}})
        _write_json(
            config_dir / "settings.overlay.json",
            {"enabled": False, "autoMode": {"environment": ["$defaults", "leaked-entry"]}},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode == 0, result.stderr
        rendered = json.loads((config_dir / "settings.json").read_text())
        assert "autoMode" not in rendered


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
        _write_json(overlay, {"enabled": True})
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
        _write_json(config_dir / "settings.overlay.json", {"enabled": True, "notAllowed": "x"})

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
            {"enabled": True, "leakedToken": "sk-should-never-appear"},
        )

        result = _run_script(config_dir=config_dir)

        assert result.returncode != 0
        assert "leakedToken" in result.stderr
        assert "sk-should-never-appear" not in result.stderr
        assert not (config_dir / "settings.json").exists()


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
        _write_json(config_dir / "settings.overlay.json", {"enabled": True})

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
        replace the symlink itself -- never write through it."""
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
        assert json.loads(target.read_text()) == {"otherKey": "base-value"}
