"""Contract tests for plugin and marketplace manifest version-field conventions.

The convention enforced here: a plugin's version is declared in its
.claude-plugin/plugin.json only. Marketplace entries carry no `version` key
because Claude Code resolves plugin.json first and silently masks any
marketplace value, making a marketplace version field dead weight that can
only drift.

Run with: pytest claude/.claude/
"""
from __future__ import annotations

import json
from pathlib import Path

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where this shared test helper lives.
from helpers import REPO_ROOT

_REPO_ROOT = REPO_ROOT
_MARKETPLACE_JSON = _REPO_ROOT / ".claude-plugin" / "marketplace.json"
_PLUGINS_DIR = _REPO_ROOT / "plugins"


def _marketplace_plugin_entries() -> list[dict]:
    data = json.loads(_MARKETPLACE_JSON.read_text())
    return data.get("plugins", [])


def _plugin_json_paths() -> list[Path]:
    return sorted(_PLUGINS_DIR.glob("*/.claude-plugin/plugin.json"))


def test_marketplace_entries_have_no_version_field():
    """Every marketplace plugin entry must omit the `version` key."""
    entries_with_version = [
        entry["name"]
        for entry in _marketplace_plugin_entries()
        if "version" in entry
    ]
    assert entries_with_version == [], (
        f"Marketplace entries must not carry a `version` field "
        f"(plugin.json wins silently): {entries_with_version}"
    )


def test_plugin_json_files_have_version_field():
    """Every plugin's .claude-plugin/plugin.json must declare a `version`."""
    plugin_jsons = _plugin_json_paths()
    assert plugin_jsons, "Expected at least one plugin under plugins/"

    missing_version = []
    for plugin_json_path in plugin_jsons:
        data = json.loads(plugin_json_path.read_text())
        if "version" not in data:
            missing_version.append(str(plugin_json_path.relative_to(_REPO_ROOT)))

    assert missing_version == [], (
        f"plugin.json files missing `version`: {missing_version}"
    )
