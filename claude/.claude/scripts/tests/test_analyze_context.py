"""Tests for analyze-context.py's CLAUDE_DIR resolution."""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "analyze-context.py"
_spec = importlib.util.spec_from_file_location("analyze_context", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def test_claude_dir_honors_claude_config_dir(monkeypatch, tmp_path):
    """CLAUDE_DIR (and PROJECTS_DIR/SESSION_META_DIR derived from it) is
    computed at import time from config_dir(); a fresh import with
    CLAUDE_CONFIG_DIR set resolves under that directory instead of
    ~/.claude."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("analyze_context_config_dir_case", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tmp_path == mod.CLAUDE_DIR
    assert tmp_path / "projects" == mod.PROJECTS_DIR
    assert tmp_path / "usage-data" / "session-meta" == mod.SESSION_META_DIR
