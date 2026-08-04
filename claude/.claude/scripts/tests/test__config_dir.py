"""Tests for _config_dir.py's config_dir() branching."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from _config_dir import config_dir  # noqa: E402


def test_returns_claude_config_dir_when_set_absolute(tmp_path, monkeypatch):
    override = tmp_path / "custom-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    assert config_dir() == override


def test_falls_back_to_home_claude_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".claude"


def test_relative_claude_config_dir_raises_value_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/path")
    with pytest.raises(ValueError, match="must be an absolute path"):
        config_dir()


def test_empty_string_claude_config_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".claude"
