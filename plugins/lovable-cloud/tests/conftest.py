"""Shared pytest fixtures for plugins/lovable-cloud/tests/."""
from __future__ import annotations

import pytest
from helpers import install_shell_exec_guard


@pytest.fixture(autouse=True)
def _clear_ambient_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this suite exercises CLAUDE_CONFIG_DIR-override behavior —
    every test isolates via HOME instead. Clear it so a CLAUDE_CONFIG_DIR set
    in the developer's own shell can't outrank an isolated HOME passed to
    run_hook()/run_hook_reason() (claude/.claude/tests/helpers.py), since
    MIGRATION_TOKEN_DIR resolves via ${CLAUDE_CONFIG_DIR:-$HOME/.claude}."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture(autouse=True)
def _guard_shell_exec(monkeypatch):
    """Fail a test that execs a shell file in a way that can pass without the file running.
    The wrapper lives in helpers.install_shell_exec_guard; each test tree defines its own
    fixture because importing one into a conftest trips ruff F401."""
    install_shell_exec_guard(monkeypatch)
