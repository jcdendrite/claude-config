"""Tests for plugins/skill-management/hooks/provision-validator-venv.sh.

The script bootstraps a Python venv with pyyaml for the SKILL.md structural
validator. The motivating regression: on Debian/Ubuntu systems without
`python3-venv`, `python3 -m venv` emits a multi-line ensurepip banner that
SessionStart surfaces at the top of every new session. The script must
degrade gracefully — single one-line warning, exit 0, no banner leakage.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"
PROVISION_SCRIPT = (
    _PLUGINS_DIR / "skill-management" / "hooks" / "provision-validator-venv.sh"
)
REQUIREMENTS_FILE = _PLUGINS_DIR / "skill-management" / "requirements.txt"


def _write_stub_python(bin_dir: Path, body: str) -> Path:
    """Drop a python3 stub at bin_dir/python3 with `body` as its script body."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "python3"
    stub.write_text("#!/bin/bash\n" + body)
    stub.chmod(0o755)
    return stub


def _run(plugin_data: Path, plugin_root: Path, extra_path: Path | None = None):
    env = {
        "CLAUDE_PLUGIN_DATA": str(plugin_data),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        # Minimal PATH — explicitly omit the system PATH so a stubbed python3
        # is the only one visible (or no python3 at all when we want to test
        # the "python3 not found" path).
        "PATH": f"{extra_path}:/usr/bin:/bin" if extra_path else "/nonexistent",
    }
    return subprocess.run(
        [str(PROVISION_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def plugin_dirs(tmp_path):
    """Plugin root with a requirements.txt + empty plugin data dir."""
    root = tmp_path / "plugin-root"
    root.mkdir()
    (root / "requirements.txt").write_text(REQUIREMENTS_FILE.read_text())
    data = tmp_path / "plugin-data"
    data.mkdir()
    return root, data


class TestVenvCreationFailureIsGraceful:
    """If `python3 -m venv` fails (missing ensurepip / python3-venv), the
    script must NOT surface python3's distro-specific banner at session
    start. It must emit a single graceful one-liner and exit 0."""

    def test_venv_failure_suppresses_python_stderr(self, tmp_path, plugin_dirs):
        root, data = plugin_dirs
        bin_dir = tmp_path / "bin"
        _write_stub_python(
            bin_dir,
            'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
            '  echo "ensurepip is not available. On Debian/Ubuntu systems..." >&2\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
        )

        result = _run(data, root, extra_path=bin_dir)

        assert result.returncode == 0, (
            f"script must exit 0 even when venv creation fails; got {result.returncode}"
        )
        assert "ensurepip is not available" not in result.stderr, (
            "python3's distro-specific banner leaked through; SessionStart "
            "would surface it at the top of every new session"
        )
        assert "failed to provision Python venv" in result.stderr, (
            "expected the script's graceful one-liner; got: " + repr(result.stderr)
        )

    def test_venv_failure_removes_cached_requirements(self, tmp_path, plugin_dirs):
        """Removing the cached requirements.txt is what gates retry: the
        cache-hit check at the top of the script compares root vs. data
        requirements.txt. If we leave a stale copy after a failed provision,
        the next session sees a cache hit and never retries, so a user who
        installs python3-venv between sessions stays stuck on the warning."""
        root, data = plugin_dirs
        bin_dir = tmp_path / "bin"
        _write_stub_python(
            bin_dir,
            'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then exit 1; fi\nexit 0\n',
        )

        _run(data, root, extra_path=bin_dir)

        assert not (data / "requirements.txt").exists(), (
            "cached requirements.txt must be removed after provisioning failure "
            "so the next session retries"
        )


class TestPipFailureLeavesNoBrokenVenv:
    """If `python3 -m venv` succeeds but `pip install` fails (network down,
    yanked release), the venv exists with a python binary but pyyaml is
    absent. require-skill-review.sh elects `venv/bin/python` as
    VALIDATOR_PYTHON whenever the file is executable — without the import
    probe + cleanup, a half-provisioned venv silently breaks the structural
    validator at commit time, and the next session's retry could leave the
    broken venv in place between sessions."""

    def test_pip_failure_removes_venv_directory(self, tmp_path, plugin_dirs):
        root, data = plugin_dirs
        bin_dir = tmp_path / "bin"
        # Stub: -m venv creates an executable venv/bin/python (so the
        # post-failure cleanup has something to remove); pip install fails.
        _write_stub_python(
            bin_dir,
            'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
            '  mkdir -p "$3/bin"\n'
            "  printf '%s\\n' '#!/bin/bash' 'exit 0' > \"$3/bin/python\"\n"
            "  printf '%s\\n' '#!/bin/bash' 'exit 1' > \"$3/bin/pip\"\n"
            '  chmod +x "$3/bin/python" "$3/bin/pip"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
        )

        result = _run(data, root, extra_path=bin_dir)

        assert result.returncode == 0
        assert not (data / "venv").exists(), (
            "partial venv directory must be removed when provisioning fails — "
            "otherwise require-skill-review.sh elects venv/bin/python as "
            "VALIDATOR_PYTHON but the validator's `import yaml` fails"
        )

    def test_import_probe_catches_venv_without_yaml(self, tmp_path, plugin_dirs):
        """Even when both `python3 -m venv` and `pip install` exit 0, the
        script must verify pyyaml is actually importable before declaring
        success — pip can exit 0 on cached dependency resolution while the
        package itself is missing from the resulting site-packages."""
        root, data = plugin_dirs
        bin_dir = tmp_path / "bin"
        # Stub: -m venv + pip both succeed, but `python -c 'import yaml'`
        # fails (no yaml in the fake site-packages).
        _write_stub_python(
            bin_dir,
            'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
            '  mkdir -p "$3/bin"\n'
            "  printf '%s\\n' '#!/bin/bash' 'if [ \"$1\" = \"-c\" ]; then exit 1; fi; exit 0' > \"$3/bin/python\"\n"
            "  printf '%s\\n' '#!/bin/bash' 'exit 0' > \"$3/bin/pip\"\n"
            '  chmod +x "$3/bin/python" "$3/bin/pip"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
        )

        result = _run(data, root, extra_path=bin_dir)

        assert result.returncode == 0
        assert not (data / "venv").exists(), (
            "venv directory must be removed when the yaml import probe fails"
        )
        assert not (data / "requirements.txt").exists(), (
            "cached requirements.txt must be removed when the yaml import "
            "probe fails so the next session retries"
        )
        assert "failed to provision Python venv" in result.stderr


class TestPython3Absent:
    """When python3 is not on PATH, the script warns once and exits 0
    (matches the pre-existing behavior of the inline hook command)."""

    def test_no_python3_warns_and_exits_zero(self, plugin_dirs):
        root, data = plugin_dirs

        result = _run(data, root, extra_path=None)

        assert result.returncode == 0
        assert "python3 not found" in result.stderr


class TestCacheHit:
    """When the cached requirements.txt matches the plugin's requirements.txt,
    the script must short-circuit — no python3 invocation, no stderr."""

    def test_cache_hit_short_circuits(self, tmp_path, plugin_dirs):
        root, data = plugin_dirs
        # Pre-populate the cache as if a prior session already provisioned.
        (data / "requirements.txt").write_text((root / "requirements.txt").read_text())

        # Stub python3 to fail loudly — if the script invokes it, the test
        # will surface the failure.
        bin_dir = tmp_path / "bin"
        _write_stub_python(bin_dir, 'echo "SHOULD NOT BE CALLED" >&2\nexit 99\n')

        result = _run(data, root, extra_path=bin_dir)

        assert result.returncode == 0
        assert result.stderr == "", (
            f"cache hit must produce no stderr; got: {result.stderr!r}"
        )


class TestHooksJsonInvokesScript:
    """Defense against accidental drift: the SessionStart hook in
    plugins/skill-management/hooks/hooks.json must invoke this script, not
    re-inline the provisioning logic."""

    def test_session_start_command_invokes_provision_script(self):
        hooks_config = json.loads(
            (_PLUGINS_DIR / "skill-management" / "hooks" / "hooks.json").read_text()
        )
        session_start_entries = hooks_config["hooks"]["SessionStart"]
        # Flatten any matcher groups → list of hook command strings.
        commands = [
            hook["command"]
            for entry in session_start_entries
            for hook in entry["hooks"]
            if hook.get("type") == "command"
        ]
        assert any("provision-validator-venv.sh" in cmd for cmd in commands), (
            "SessionStart hook must invoke provision-validator-venv.sh — if "
            "the provisioning logic was inlined back into hooks.json, the "
            "graceful-failure tests in this file no longer cover the "
            "behavior that actually runs at session start. Found commands: "
            f"{commands!r}"
        )
