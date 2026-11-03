"""Tests for install-dev.sh.

The script bootstraps a contributor Python venv from requirements-dev.txt.
Tests exercise the critical branches:

1. CWD anchor checks: requirements-dev.txt absent → non-zero exit; .venv is a
   symlink → non-zero exit with symlink guidance.
2. ensurepip-missing on a Debian system → non-zero exit + apt guidance naming
   the pinned minor's package (python<X.Y>-venv).
3. ensurepip-missing on a non-Debian system → non-zero exit + generic guidance,
   no apt mention.
4. An existing .venv that fails a health-probe conjunct → removed, recreated
   without touching adjacent files, and announced as unhealthy rather than as a
   minor mismatch. One case fails each of: python reporting no minor, a .venv
   with no bin/python, the dependency import, `pip --version`, `ruff --version`,
   and `shellcheck --version`.
5. Idempotency: a healthy .venv is not recreated on a second run.
6. The requirements sync is `install --quiet -r requirements-dev.txt` through the
   venv's own pip; a pip install failure → non-zero exit (set -e propagates it).
7. private-projects.md opt-in gate: missing/directory/dangling-symlink/
   permission-denied → non-zero exit naming the file and the docs pointer;
   a present, readable regular file (comment-only or zero-byte) falls
   through unchanged. Resolution is a union matching
   deny-private-project-refs.sh's own: $HOME/.claude is the floor,
   overridden only when CLAUDE_CONFIG_DIR is absolute and its own copy
   exists.
8. Step 7, the pyright-lsp local-scope install, one case per branch:
   - no `claude` CLI
   - a marketplace-list read failure
   - `jq` genuinely absent from PATH
   - an unregistered marketplace
   - already installed
   - a plugin-list read failure, which still reaches the install arm
   - a local entry for another directory, which still reaches the install arm
   - a project-scope entry for this directory, which still reaches the install arm
   - not installed, with no terminal: the script prints the install command and skips
   - not installed, at a pty, answered `y`: the script installs at local scope
   - not installed, at a pty, answered Enter: the plugin stays uninstalled
   - not installed, at a pty, sent EOF (Ctrl-D): the plugin stays uninstalled
   - a confirmed install that fails: the script still exits 0
   - the `pyright-langserver` binary present or absent
9. `_confirm_plugin_install`, sourced directly. An answer starting with y or
   Y confirms. Any other answer, Enter, and EOF decline.
10. The CI Python pin, which install-dev.sh reads from .github/workflows/tests.yml
    (the parsing matrix lives in test_ci_python_pin_lib.py):
    - the pinned `python<X.Y>` absent from PATH → non-zero exit naming the
      interpreter and tests.yml, no fallback to python3, no .venv created
    - a pin that cannot be read → non-zero exit before any interpreter runs
    - the venv-minor probe runs under a real interpreter: a .venv on another
      minor is removed and rebuilt from the pinned interpreter, and one on the
      pinned minor is kept
    - a pinned interpreter that is absent, fails to start or lacks ensurepip,
      with a healthy .venv on another minor → non-zero exit, the old .venv untouched
    - a pinned interpreter that is absent while the existing .venv is on the
      pinned minor but unhealthy → an error naming the failed health probe,
      not an instruction to install the minor the .venv already runs
    - a pinned interpreter that builds a venv on another minor → the
      verification failure, with no `Done.` line
    - a healthy .venv on the pinned minor needs no python<X.Y> on PATH

PATH stubs replace the pinned python<X.Y> interpreter so tests run in isolation
without mutating the real .venv. The repo_root_stub fixture writes a tests.yml
pinning PINNED_MINOR, a minor no real interpreter on a test runner is expected
to have.
HOME is isolated per test (see the `fake_home` fixture) so the gate
never reads the real invoking environment's
~/.claude/private-projects.md. `_run_script` gives the script /dev/null as
stdin, so Step 7's `[ -t 0 ]` check is false. The interactive cases attach
a real pseudo-terminal through `_run_script_with_pty_stdin`.
"""
from __future__ import annotations

import json
import os
import pty
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from helpers import build_path_without, link_bash_into

# install-dev.sh lives at the repo root: climb from scripts/tests/ (parents[0])
# → scripts/ (parents[1]) → claude/.claude/ (parents[2]) → claude/ (parents[3])
# → repo root (parents[4]).
REPO_ROOT = Path(__file__).parents[4]
SCRIPT = REPO_ROOT / "install-dev.sh"
_PLUGIN_INSTALL_LIB = REPO_ROOT / "claude" / ".claude" / "scripts" / "_plugin-install-lib.sh"
PINNED_MINOR = "3.99"
OTHER_MINOR = "3.98"
PINNED_INTERPRETER = f"python{PINNED_MINOR}"
STALE_VENV_MARKER = Path("lib") / "stale_marker"
BUILT_VENV_MARKER = Path("lib") / "built_by_pinned_interpreter_marker"


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

def _write_stub(bin_dir: Path, name: str, body: str) -> Path:
    """Write an executable bash stub at bin_dir/name."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / name
    stub.write_text("#!/bin/bash\n" + body)
    stub.chmod(0o755)
    return stub


def _script_env(
    fake_home: Path,
    stub_bin: Path | None = None,
    extra_env: dict | None = None,
    path_tail: str | None = None,
) -> dict:
    """Build the isolated PATH/HOME env install-dev.sh runs under.

    HOME must be isolated (not the real invoking environment's $HOME) — the
    script's private-projects.md gate would otherwise read the real
    developer/CI machine's ~/.claude/private-projects.md.

    path_tail overrides the "/usr/bin:/bin" fallback appended after
    stub_bin. A caller that needs a binary genuinely absent from PATH (e.g.
    jq, which some platforms ship at /usr/bin) passes a path built by
    helpers.build_path_without instead — /usr/bin:/bin's own contents are
    platform-dependent and cannot be relied on to omit a given binary.

    Without a path_tail, a directory holding the caller's bash leads PATH, so
    `#!/usr/bin/env bash` resolves to the bash running the suite, not the
    system's. A path_tail from build_path_without already carries bash."""
    if path_tail is not None:
        system_path = path_tail
    else:
        caller_bash_dir = fake_home.parent / "caller-bash"
        caller_bash_dir.mkdir(exist_ok=True)
        link_bash_into(caller_bash_dir)
        system_path = f"{caller_bash_dir}:/usr/bin:/bin"
    path_val = f"{stub_bin}:{system_path}" if stub_bin else system_path
    env = {"PATH": path_val, "HOME": str(fake_home)}
    if extra_env:
        env.update(extra_env)
    return env


def _run_script(
    cwd: Path,
    fake_home: Path,
    stub_bin: Path | None = None,
    extra_env: dict | None = None,
    path_tail: str | None = None,
) -> subprocess.CompletedProcess:
    """Run install-dev.sh from cwd with an isolated PATH and HOME, stdin
    attached to /dev/null (not a terminal)."""
    env = _script_env(fake_home, stub_bin, extra_env, path_tail)
    return subprocess.run(
        [str(SCRIPT)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


_PTY_RUN_TIMEOUT_SECONDS = 10
# Bounds a run whose prompt never gets a reply; matches test_migrate_legacy_config.py's pty runner.


def _run_script_with_pty_stdin(
    cwd: Path, fake_home: Path, stub_bin: Path | None, reply: str
) -> subprocess.CompletedProcess:
    """Run install-dev.sh with stdin attached to a real pseudo-terminal.

    `reply` is written to the pty's master side before the script runs, so
    it is already queued for whatever `read -r -p` call consumes it. Every
    caller must pass a reply, because otherwise the prompt waits until
    `_PTY_RUN_TIMEOUT_SECONDS` expires. For this helper, EOF is spelled
    `"\x04"` (the terminal's EOF character, Ctrl-D), not `""` as for the
    piped `TestConfirmPluginInstall` helper. An empty reply here would
    leave the prompt waiting until the timeout."""
    env = _script_env(fake_home, stub_bin)
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            [str(SCRIPT)],
            cwd=str(cwd),
            env=env,
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        os.write(master_fd, reply.encode())
        try:
            stdout, stderr = proc.communicate(timeout=_PTY_RUN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        os.close(master_fd)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_workflow_pin(repo_root_stub: Path, minor: str) -> None:
    workflow = repo_root_stub / ".github" / "workflows" / "tests.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        f"      - uses: actions/setup-python@abc123\n        with:\n          python-version: '{minor}'\n"
    )


@pytest.fixture
def repo_root_stub(tmp_path: Path) -> Path:
    """A temp directory that looks like the repo root: has requirements-dev.txt
    and a tests.yml pinning PINNED_MINOR, no .venv. The real requirements-dev.txt
    is copied so pip-install stubs can receive the real file path without
    synthetic content."""
    real_requirements = REPO_ROOT / "requirements-dev.txt"
    shutil.copy(real_requirements, tmp_path / "requirements-dev.txt")
    _write_workflow_pin(tmp_path, PINNED_MINOR)
    return tmp_path


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """An isolated $HOME, pre-seeded with a comment-only private-projects.md
    so the script's opt-in gate passes by default. Tests exercising the gate
    itself remove or replace this file."""
    home = tmp_path / "fake_home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "private-projects.md").write_text("# no projects yet\n")
    return home


def _write_venv_tree(
    venv_dir: Path,
    minor: str = PINNED_MINOR,
    pip_exit: int = 0,
    pip_version_exit: int = 0,
    dependencies_importable: bool = True,
    ruff_exit: int = 0,
    pip_call_log: Path | None = None,
) -> None:
    """Write a minimal stub venv whose health probe passes by default.

    `minor` is what its python prints for the version probe. Its python answers
    only that probe and the dependency-import probe. Any other call exits 0 with no
    output, so the `Done.` line's pytest and pyyaml versions are empty. `pip_call_log`,
    when given, receives one line per pip call: the path pip was invoked by, then
    its arguments.

    Every binary check_venv_healthy probes must be stubbed here — the probe
    calls .venv/bin/<tool> directly, so a PATH stub cannot satisfy it."""
    venv_bin = venv_dir / "bin"
    venv_bin.mkdir(parents=True)
    import_probe_exit = 0 if dependencies_importable else 1
    log_pip_call = (
        f"printf '%s\\n' \"$0 $*\" >> {shlex.quote(str(pip_call_log))}\n" if pip_call_log else ""
    )
    stubs = {
        "python": (
            'case "$*" in\n'
            f'  *version_info*) echo "{minor}" ;;\n'
            f'  *"import yaml, pytest, xdist"*) exit {import_probe_exit} ;;\n'
            "esac\nexit 0\n"
        ),
        "pip": f'{log_pip_call}case "$1" in --version) exit {pip_version_exit} ;; esac\nexit {pip_exit}\n',
        "ruff": f'echo "ruff 0.6.0"\nexit {ruff_exit}\n',
        "shellcheck": 'echo "ShellCheck - shell script analysis tool"\necho "version: 0.11.0"\nexit 0\n',
    }
    for name, body in stubs.items():
        _write_stub(venv_bin, name, body)


def _make_healthy_venv_stub(repo_root_stub: Path, minor: str = PINNED_MINOR) -> None:
    _write_venv_tree(repo_root_stub / ".venv", minor)


def _add_stale_marker(repo_root_stub: Path) -> Path:
    """Add a file to the existing .venv that no pinned-interpreter build
    produces, so its survival or removal shows whether the old tree was kept."""
    marker = repo_root_stub / ".venv" / STALE_VENV_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("old-tree\n")
    return marker


def _delegate_version_probe_to_running_interpreter(venv_dir: Path) -> None:
    """Make the venv's `python` run the real interpreter for the `version_info`
    probe, so install-dev.sh's own probe text is evaluated, not matched."""
    _write_stub(
        venv_dir / "bin",
        "python",
        f'case "$*" in *version_info*) exec {shlex.quote(sys.executable)} "$@" ;; esac\nexit 0\n',
    )


_MINOR_TOKEN = re.compile(r"\d+\.\d+")


def _recreation_announcement(stdout: str) -> str:
    """The one stdout line announcing that the existing .venv is being recreated.
    Both announcement arms contain "recreating"; the "Creating .venv" line does not."""
    announcements = [line for line in stdout.splitlines() if "recreating" in line]
    assert len(announcements) == 1, stdout
    return announcements[0]


def _assert_announced_as_incomplete_or_unhealthy(stdout: str) -> None:
    """The unhealthy arm names no Python minor, so a minor on the line means the mismatch arm fired."""
    announcement = _recreation_announcement(stdout)
    assert "incomplete or unhealthy" in announcement, announcement
    assert not _MINOR_TOKEN.search(announcement), announcement


def _assert_announced_as_minor_mismatch(stdout: str, existing_minor: str) -> None:
    announcement = _recreation_announcement(stdout)
    assert f"runs Python {existing_minor} but CI pins {PINNED_MINOR}" in announcement, announcement
    assert PINNED_INTERPRETER in announcement, announcement


def _write_pinned_interpreter(
    repo_root_stub: Path,
    sentinel: Path | None = None,
    ensurepip_available: bool = True,
    built_venv_pip_exit: int = 0,
    pinned_minor: str = PINNED_MINOR,
    built_venv_minor: str | None = None,
    starts: bool = True,
    pip_call_log: Path | None = None,
) -> Path:
    """Write the pinned `python<pinned_minor>` PATH stub into repo_root_stub/bin
    and return that bin directory.

    A `starts=False` interpreter also fails `-c "import ensurepip"`, as a dead
    version-manager shim does. Any call outside `-c pass`, `-c "import ensurepip"`
    and `-m venv <dir>` exits 97. The venv it builds reports `built_venv_minor`
    (default `pinned_minor`), holds BUILT_VENV_MARKER, and logs its pip calls to
    `pip_call_log` when given."""
    venv_template = repo_root_stub / "built_venv_template"
    _write_venv_tree(
        venv_template, built_venv_minor or pinned_minor, built_venv_pip_exit, pip_call_log=pip_call_log
    )
    built_marker = venv_template / BUILT_VENV_MARKER
    built_marker.parent.mkdir(parents=True, exist_ok=True)
    built_marker.write_text("new-tree\n")
    touch_sentinel = f"touch '{sentinel}'" if sentinel else ":"
    start_exit = 0 if starts else 1
    bin_dir = repo_root_stub / "bin"
    interpreter = f"python{pinned_minor}"
    _write_stub(bin_dir, interpreter, textwrap.dedent(f"""\
        if [ "$1" = "-c" ] && [ "$2" = "pass" ]; then
          [ {start_exit} -eq 0 ] || echo "{interpreter}: simulated start failure" >&2
          exit {start_exit}
        fi
        if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
          exit {0 if ensurepip_available and starts else 1}
        fi
        if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
          {touch_sentinel}
          mkdir -p "$3"
          cp -R '{venv_template}/.' "$3/"
          exit 0
        fi
        echo "{interpreter} stub: unsupported call: $*" >&2
        exit 97
    """))
    return bin_dir


_PYRIGHT_LSP_PLUGIN = "pyright-lsp@claude-plugins-official"


def _make_claude_shim(
    bin_dir: Path,
    marketplaces: list[str] | None,
    plugins: list[tuple[str, str, str]] | None,
    call_log: Path,
    install_exit: int = 0,
) -> Path:
    """Write a bash `claude` stub, so it depends on no interpreter on PATH.
    It handles `plugin marketplace list --json`, `plugin list
    --json`, and `plugin install`. It logs every invocation's arguments, as
    `$*`, one line per call, to call_log. It exits 1 on anything unhandled.

    marketplaces=None simulates `claude plugin marketplace list --json`
    itself failing (exit 1). Otherwise it's rendered as that command's
    `.name`-bearing JSON array. plugins=None simulates `claude plugin list
    --json` itself failing (exit 1) the same way. Otherwise it's a list of
    (id, scope, project_path) tuples rendered as that command's JSON
    array."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    call_log.parent.mkdir(parents=True, exist_ok=True)
    marketplace_list_should_fail = "true" if marketplaces is None else "false"
    marketplace_json = "[]" if marketplaces is None else json.dumps(
        [{"name": name} for name in marketplaces]
    )
    plugin_list_should_fail = "true" if plugins is None else "false"
    plugin_json = "[]" if plugins is None else json.dumps(
        [{"id": pid, "scope": scope, "projectPath": project_path} for pid, scope, project_path in plugins]
    )
    stub = bin_dir / "claude"
    stub.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        printf '%s\\n' "$*" >> '{call_log}'
        if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then
          if [ "{marketplace_list_should_fail}" = "true" ]; then
            exit 1
          fi
          echo '{marketplace_json}'
          exit 0
        fi
        if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then
          if [ "{plugin_list_should_fail}" = "true" ]; then
            exit 1
          fi
          echo '{plugin_json}'
          exit 0
        fi
        if [ "$1" = "plugin" ] && [ "$2" = "install" ]; then
          exit {install_exit}
        fi
        exit 1
    """))
    stub.chmod(0o755)
    return stub


def _link_real_jq(bin_dir: Path) -> None:
    """Symlink the real jq into bin_dir, ahead of install-dev.sh's default
    PATH tail, so the script's `jq -r` pipeline can parse the claude shim's
    JSON output."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    real_jq = shutil.which("jq")
    assert real_jq, "jq must be installed on the test runner's PATH"
    (bin_dir / "jq").symlink_to(real_jq)


def _pyright_lsp_baseline(repo_root_stub: Path) -> tuple[Path, Path]:
    """Builds a healthy `.venv` and a pinned-interpreter stub for
    `TestPyrightLspStep`. The stub's `ensurepip` check passes. Its `-m venv`
    call touches a sentinel. Returns `(bin_dir, venv_created_sentinel)`. The
    sentinel proves Step 7 never feeds into `check_venv_healthy`'s recreate
    path."""
    _make_healthy_venv_stub(repo_root_stub)
    sentinel = repo_root_stub / "venv_created.flag"
    return _write_pinned_interpreter(repo_root_stub, sentinel), sentinel


# ---------------------------------------------------------------------------
# Test class: CWD anchor and symlink guard
# ---------------------------------------------------------------------------

class TestCwdAnchorCheck:
    """The script must refuse with a clear error when the CWD anchor fails —
    either requirements-dev.txt is absent (wrong directory) or .venv is a
    symlink (worktree mishap). Both guards protect the rm -rf .venv operation."""

    def test_absent_requirements_exits_nonzero_with_guidance(self, tmp_path: Path, fake_home: Path):
        """Running from a directory with no requirements-dev.txt must exit non-zero
        and name the missing file so the contributor knows what to fix."""
        # No requirements-dev.txt in tmp_path — simulates running from wrong dir.
        result = _run_script(tmp_path, fake_home)
        assert result.returncode != 0, (
            f"Expected non-zero exit when requirements-dev.txt is absent; got {result.returncode}"
        )
        assert "requirements-dev.txt" in result.stderr, (
            f"Error message must name the missing file; got: {result.stderr!r}"
        )

    def test_dotenv_symlink_exits_nonzero_with_guidance(self, repo_root_stub: Path, fake_home: Path):
        """Running when .venv is a symlink must exit non-zero and name the
        symlink concern so the contributor knows to check their worktree setup.
        This guards against rm -rf accidentally following a symlink."""
        target = repo_root_stub / "other_venv"
        target.mkdir()
        symlink = repo_root_stub / ".venv"
        symlink.symlink_to(target)

        result = _run_script(repo_root_stub, fake_home)
        assert result.returncode != 0, (
            f"Expected non-zero exit when .venv is a symlink; got {result.returncode}"
        )
        assert "symlink" in result.stderr, (
            f"Error message must mention symlink; got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Test class: ensurepip-missing on Debian → apt guidance with version token
# ---------------------------------------------------------------------------

class TestEnsurepipMissingDebian:
    """On a Debian system (apt-get present or /etc/debian_version present),
    the script must exit non-zero and emit an apt install line naming the
    package for the pinned minor (python<X.Y>-venv).

    _INSTALL_DEV_IS_DEBIAN=true forces the Debian branch regardless of
    whether /etc/debian_version exists in the test environment."""

    def test_exit_nonzero_and_apt_guidance_emitted(self, repo_root_stub: Path, fake_home: Path):
        bin_dir = _write_pinned_interpreter(repo_root_stub, ensurepip_available=False)

        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "true"}
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when ensurepip is missing; got {result.returncode}"
        )
        assert "apt install" in result.stderr, (
            f"Expected apt install guidance in stderr; got: {result.stderr!r}"
        )
        assert f"python{PINNED_MINOR}-venv" in result.stderr, (
            f"Expected the pinned minor's package name in stderr; got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Test class: ensurepip-missing on non-Debian → generic guidance, no apt
# ---------------------------------------------------------------------------

class TestEnsurepipMissingNonDebian:
    """On a non-Debian system, the script must exit non-zero and emit generic
    venv-support guidance without mentioning apt or the Debian-specific package.

    _INSTALL_DEV_IS_DEBIAN=false forces the non-Debian branch so the test runs
    correctly even on machines where /etc/debian_version exists."""

    def test_exit_nonzero_and_generic_guidance(self, repo_root_stub: Path, fake_home: Path):
        bin_dir = _write_pinned_interpreter(repo_root_stub, ensurepip_available=False)

        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "false"}
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when ensurepip is missing; got {result.returncode}"
        )
        assert "apt" not in result.stderr, (
            f"Debian branch must not fire on non-Debian; stderr: {result.stderr!r}"
        )
        assert "venv support" in result.stderr, (
            f"Expected the generic venv-support guidance in stderr; got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Test class: pinned interpreter on PATH that fails to start
# ---------------------------------------------------------------------------

class TestPinnedInterpreterFailsToStart:
    """A pinned `python<X.Y>` that is on PATH but cannot run (for example a
    version-manager shim for a version that is not active) is reported
    separately from one that lacks ensurepip, with the interpreter's own error."""

    def test_start_failure_shows_the_interpreters_stderr_and_no_ensurepip_guidance(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir = _write_pinned_interpreter(repo_root_stub, starts=False)

        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "true"}
        )

        assert result.returncode != 0, f"stdout: {result.stdout}"
        assert f"{PINNED_INTERPRETER} is on PATH but failed to start" in result.stderr, result.stderr
        assert "simulated start failure" in result.stderr, result.stderr
        assert "ensurepip" not in result.stderr, result.stderr
        assert "apt install" not in result.stderr, result.stderr
        assert not (repo_root_stub / ".venv").exists()


# ---------------------------------------------------------------------------
# Test class: pip-less .venv → removed and recreated, adjacent files intact
# ---------------------------------------------------------------------------

class TestUnhealthyVenvIsRecreated:
    """If .venv exists but a health-probe conjunct fails, the script removes
    .venv and recreates it without touching adjacent files, and announces it as
    unhealthy rather than as a minor mismatch. Each case fails only the
    conjunct it names. The `shellcheck --version` conjunct is covered in
    TestIdempotency."""

    def test_venv_whose_python_cannot_run_is_removed_and_adjacent_files_untouched(
        self, repo_root_stub: Path, fake_home: Path
    ):
        # A python that exits 1 for every call reports no minor, so the minor
        # conjunct fails first and the dependency import is never reached.
        venv_bin = repo_root_stub / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        venv_python = venv_bin / "python"
        venv_python.write_text("#!/bin/bash\nexit 1\n")
        venv_python.chmod(0o755)

        # A sentinel file adjacent to .venv — must survive the rm -rf .venv.
        adjacent_file = repo_root_stub / "sentinel.txt"
        adjacent_file.write_text("must-survive\n")

        bin_dir = _write_pinned_interpreter(repo_root_stub)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        # The old unhealthy .venv should have been removed and a new one created.
        assert (repo_root_stub / ".venv").is_dir(), (
            ".venv directory should exist after recreation"
        )
        # Adjacent sentinel must be untouched — rm -rf .venv must be scoped.
        assert adjacent_file.exists(), (
            "Adjacent files must survive .venv removal — rm -rf blast radius is bounded"
        )
        assert adjacent_file.read_text() == "must-survive\n", (
            "Adjacent file content must be unchanged after .venv recreation"
        )
        assert result.returncode == 0, (
            f"Expected exit 0 after venv recreation; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert (repo_root_stub / ".venv" / BUILT_VENV_MARKER).exists()
        _assert_announced_as_incomplete_or_unhealthy(result.stdout)

    def test_venv_directory_without_a_python_executable_is_recreated(
        self, repo_root_stub: Path, fake_home: Path
    ):
        (repo_root_stub / ".venv").mkdir()
        stale_marker = _add_stale_marker(repo_root_stub)
        bin_dir = _write_pinned_interpreter(repo_root_stub)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert not stale_marker.exists()
        assert (repo_root_stub / ".venv" / BUILT_VENV_MARKER).exists()
        _assert_announced_as_incomplete_or_unhealthy(result.stdout)

    @pytest.mark.parametrize(
        "broken_tool",
        [
            pytest.param({"dependencies_importable": False}, id="dependency-import-probe"),
            pytest.param({"ruff_exit": 1}, id="ruff-version"),
            # A stale console-script shebang (a moved checkout) leaves python and
            # the native tools working but makes pip fail.
            pytest.param({"pip_version_exit": 126}, id="pip-console-script"),
        ],
    )
    def test_venv_on_the_pinned_minor_failing_one_probe_conjunct_is_recreated_as_unhealthy(
        self, repo_root_stub: Path, fake_home: Path, broken_tool: dict
    ):
        _write_venv_tree(repo_root_stub / ".venv", PINNED_MINOR, **broken_tool)
        stale_marker = _add_stale_marker(repo_root_stub)
        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = _write_pinned_interpreter(repo_root_stub, sentinel)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert sentinel.exists(), "a .venv failing one health-probe conjunct must be rebuilt"
        assert not stale_marker.exists(), "the old tree must be removed, not built over"
        _assert_announced_as_incomplete_or_unhealthy(result.stdout)


# ---------------------------------------------------------------------------
# Test class: idempotency — healthy .venv is not recreated on a second run
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Running install-dev.sh on an already-healthy .venv must not remove or
    recreate it — pip install (no-op when pins are satisfied) should be the
    only write operation."""

    def test_healthy_venv_not_recreated(self, repo_root_stub: Path, fake_home: Path):
        """A second run when .venv is already healthy must exit 0 and skip
        the pinned interpreter's -m venv creation step, while still syncing
        requirements-dev.txt on the kept venv."""
        _make_healthy_venv_stub(repo_root_stub)

        # Sentinel: touched by the stub if the pinned interpreter's -m venv is called.
        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = _write_pinned_interpreter(repo_root_stub, sentinel)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, (
            f"Expected exit 0 on a healthy venv; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert not sentinel.exists(), (
            "the pinned interpreter's -m venv must not be called when .venv is already healthy"
        )
        assert "shellcheck=0.11.0" in result.stdout, (
            "Expected the printed version summary to contain shellcheck=0.11.0, "
            "parsed from `shellcheck --version`'s `version:` line via "
            f"install-dev.sh's `awk '/^version:/ {{print $2}}'`; got stdout: "
            f"{result.stdout!r}"
        )
        assert "Syncing requirements-dev.txt..." in result.stdout, result.stdout

    def test_venv_missing_shellcheck_is_recreated(self, repo_root_stub: Path, fake_home: Path):
        """A .venv missing only shellcheck must be treated unhealthy, or the
        probe passes and the contributor is left with no shell-lint tool."""
        _make_healthy_venv_stub(repo_root_stub)
        # Remove only shellcheck — everything else still passes the probe.
        (repo_root_stub / ".venv" / "bin" / "shellcheck").unlink()

        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = _write_pinned_interpreter(repo_root_stub, sentinel)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert sentinel.exists(), (
            "A .venv without shellcheck must be recreated — check_venv_healthy "
            "is what makes the new pin reach existing contributors"
        )
        assert result.returncode == 0, (
            f"Expected exit 0 after recreation; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        _assert_announced_as_incomplete_or_unhealthy(result.stdout)


# ---------------------------------------------------------------------------
# Test class: pip install failure propagates as non-zero exit
# ---------------------------------------------------------------------------

class TestPipInstallFailure:
    """If pip install fails after venv creation, set -e propagates the
    non-zero exit code — the script must not silently succeed."""

    def test_pip_failure_exits_nonzero(self, repo_root_stub: Path, fake_home: Path):
        bin_dir = _write_pinned_interpreter(repo_root_stub, built_venv_pip_exit=1)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode != 0, (
            f"Expected non-zero exit when pip install fails; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "Syncing requirements-dev.txt..." in result.stdout, result.stdout
        assert "Done." not in result.stdout, result.stdout


class TestRequirementsSync:
    """The sync installs requirements-dev.txt through the venv's own pip."""

    def test_sync_installs_requirements_dev_through_the_venvs_own_pip(
        self, repo_root_stub: Path, fake_home: Path
    ):
        pip_call_log = repo_root_stub / "pip_calls.log"
        bin_dir = _write_pinned_interpreter(repo_root_stub, pip_call_log=pip_call_log)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        sync_calls = [line for line in pip_call_log.read_text().splitlines() if "--version" not in line]
        assert sync_calls == [".venv/bin/pip install --quiet -r requirements-dev.txt"], sync_calls


# ---------------------------------------------------------------------------
# Test class: the interpreter pinned in tests.yml
# ---------------------------------------------------------------------------

class TestPinnedInterpreter:
    """.venv must run the Python minor tests.yml pins for CI. install-dev.sh
    builds it from `python<X.Y>` and rebuilds a venv on another minor. It
    never falls back to python3. The pin-parsing matrix is covered in
    test_ci_python_pin_lib.py."""

    def test_missing_pinned_interpreter_fails_without_creating_a_venv_or_falling_back_to_python3(
        self, repo_root_stub: Path, fake_home: Path
    ):
        python3_called = repo_root_stub / "python3_called.flag"
        bin_dir = repo_root_stub / "bin"
        _write_stub(bin_dir, "python3", f"touch '{python3_called}'\nexit 0\n")

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode != 0, f"stdout: {result.stdout}"
        assert PINNED_INTERPRETER in result.stderr, result.stderr
        assert "not found on PATH" in result.stderr, result.stderr
        assert ".github/workflows/tests.yml" in result.stderr, result.stderr
        assert not python3_called.exists(), "python3 must not stand in for the pinned interpreter"
        assert not (repo_root_stub / ".venv").exists()

    @pytest.mark.parametrize(
        "pinned_interpreter_state",
        ["absent", "fails-to-start", "fails-ensurepip"],
    )
    def test_refusing_the_pinned_interpreter_leaves_a_healthy_venv_on_another_minor_untouched(
        self, repo_root_stub: Path, fake_home: Path, pinned_interpreter_state: str
    ):
        """Every pinned-interpreter check must run before the old .venv is
        removed, or a refusal costs the contributor the venv they still have."""
        _make_healthy_venv_stub(repo_root_stub, OTHER_MINOR)
        stale_marker = _add_stale_marker(repo_root_stub)
        sentinel = repo_root_stub / "venv_created.flag"
        interpreter_kwargs = {
            "absent": None,
            "fails-to-start": {"starts": False},
            "fails-ensurepip": {"ensurepip_available": False},
        }[pinned_interpreter_state]
        bin_dir = (
            None if interpreter_kwargs is None
            else _write_pinned_interpreter(repo_root_stub, sentinel, **interpreter_kwargs)
        )

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode != 0, f"stdout: {result.stdout}"
        assert stale_marker.exists(), "the old .venv must survive a refusal"
        assert (repo_root_stub / ".venv" / "bin" / "python").exists()
        assert not sentinel.exists()

    def test_absent_pinned_interpreter_with_an_unhealthy_venv_on_the_pinned_minor_reports_the_failed_health_probe(
        self, repo_root_stub: Path, fake_home: Path
    ):
        """A .venv already on the pinned minor must not be told to install that minor."""
        _write_venv_tree(repo_root_stub / ".venv", PINNED_MINOR, ruff_exit=1)
        stale_marker = _add_stale_marker(repo_root_stub)

        result = _run_script(repo_root_stub, fake_home)

        assert result.returncode != 0, f"stdout: {result.stdout}"
        assert ".venv failed its health probe" in result.stderr, result.stderr
        assert f"{PINNED_INTERPRETER}, which is not on PATH" in result.stderr, result.stderr
        assert "must run the same minor" not in result.stderr, result.stderr
        assert stale_marker.exists(), "the old .venv must survive a refusal"

    def test_unreadable_pin_fails_before_any_venv_or_interpreter_work(
        self, repo_root_stub: Path, fake_home: Path
    ):
        """One unreadable-pin case proves the wiring; the pin lib's own tests hold the matrix.
        A failed pin read leaves the pin empty, which resolves to the bare name `python`.
        That name is stubbed too, so a script that carried on past the failed read would
        record a call here instead of exiting on the interpreter lookup."""
        (repo_root_stub / ".github" / "workflows" / "tests.yml").unlink()
        interpreter_called = repo_root_stub / "interpreter_called.flag"
        bin_dir = repo_root_stub / "bin"
        for interpreter in (PINNED_INTERPRETER, f"python{OTHER_MINOR}", "python3", "python"):
            _write_stub(bin_dir, interpreter, f"touch '{interpreter_called}'\nexit 0\n")

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode != 0, f"stdout: {result.stdout}"
        assert "cannot read .github/workflows/tests.yml" in result.stderr, result.stderr
        assert "not found on PATH" not in result.stderr, (
            f"the interpreter lookup ran after the failed pin read: {result.stderr}"
        )
        assert not interpreter_called.exists(), "no interpreter may run when the pin cannot be read"
        assert not (repo_root_stub / ".venv").exists()

    def test_real_minor_probe_rebuilds_a_venv_on_a_different_minor(self, repo_root_stub: Path, fake_home: Path):
        """The venv's python runs the real interpreter for the minor probe, so
        a regression in the probe's program text fails here and not only in
        production."""
        running_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        _make_healthy_venv_stub(repo_root_stub)
        _delegate_version_probe_to_running_interpreter(repo_root_stub / ".venv")
        stale_marker = _add_stale_marker(repo_root_stub)
        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = _write_pinned_interpreter(repo_root_stub, sentinel)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert sentinel.exists(), "a .venv on a minor other than the pin must be rebuilt"
        _assert_announced_as_minor_mismatch(result.stdout, running_minor)
        assert not stale_marker.exists(), "the old tree must be removed, not built over"
        assert (repo_root_stub / ".venv" / BUILT_VENV_MARKER).exists()

    def test_real_minor_probe_keeps_a_venv_on_the_pinned_minor(self, repo_root_stub: Path, fake_home: Path):
        running_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        _write_workflow_pin(repo_root_stub, running_minor)
        _make_healthy_venv_stub(repo_root_stub)
        _delegate_version_probe_to_running_interpreter(repo_root_stub / ".venv")
        stale_marker = _add_stale_marker(repo_root_stub)
        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = _write_pinned_interpreter(repo_root_stub, sentinel, pinned_minor=running_minor)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert not sentinel.exists(), "a .venv on the pinned minor must not be rebuilt"
        assert stale_marker.exists(), "the kept .venv must be the old tree"

    def test_healthy_venv_on_the_pinned_minor_needs_no_pinned_interpreter_on_path(
        self, repo_root_stub: Path, fake_home: Path
    ):
        _write_venv_tree(repo_root_stub / ".venv", PINNED_MINOR)

        result = _run_script(repo_root_stub, fake_home)

        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"

    def test_pinned_interpreter_that_builds_another_minor_fails_verification_without_done(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir = _write_pinned_interpreter(repo_root_stub, built_venv_minor=OTHER_MINOR)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode != 0, f"stdout: {result.stdout}"
        assert ".venv verification failed after install" in result.stderr, result.stderr
        assert "Done." not in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# Test class: private-projects.md opt-in gate
# ---------------------------------------------------------------------------

class TestPrivateProjectsGate:
    """The script must refuse to proceed until the contributor has opted into
    the private-project redaction blocklist — an existence-and-readability
    check (`-f && -r`), not a content requirement (see
    docs/private-project-redaction.md). Resolution and readability both
    mirror deny-private-project-refs.sh's own gate exactly (verified against
    claude/.claude/hooks/deny-private-project-refs.sh:676-680)."""

    def _stub_healthy_pinned_interpreter(self, repo_root_stub: Path) -> Path:
        """A pinned-interpreter stub whose ensurepip check passes and whose
        -m venv touches a sentinel — used to confirm the gate's fall-through
        cases don't recreate an already-healthy .venv. It also lets a run with
        no .venv build one, so only the gate can make such a run fail."""
        return _write_pinned_interpreter(repo_root_stub, repo_root_stub / "venv_created.flag")

    def _run_expecting_gate_refusal(self, repo_root_stub: Path, fake_home: Path) -> subprocess.CompletedProcess:
        """Run with a working pinned interpreter and no .venv, so the script
        exits 0 and builds .venv unless the gate refuses. The refusal is then
        the gate's own: its message, and no venv side effect."""
        bin_dir = self._stub_healthy_pinned_interpreter(repo_root_stub)
        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)
        assert result.returncode != 0, f"the gate must refuse; stdout: {result.stdout}"
        assert "not found or not readable" in result.stderr, (
            f"Error message must be the gate's own; got: {result.stderr!r}"
        )
        assert "private-projects.md" in result.stderr, (
            f"Error message must name the file; got: {result.stderr!r}"
        )
        assert "private-project-redaction.md" in result.stderr, (
            f"Error message must point at the docs; got: {result.stderr!r}"
        )
        assert not (repo_root_stub / ".venv").exists(), (
            "the gate must fail before any venv side effect runs"
        )
        assert not (repo_root_stub / "venv_created.flag").exists()
        return result

    def test_missing_file_exits_nonzero_with_guidance(self, repo_root_stub: Path, fake_home: Path):
        (fake_home / ".claude" / "private-projects.md").unlink()
        self._run_expecting_gate_refusal(repo_root_stub, fake_home)

    def test_directory_at_path_exits_nonzero(self, repo_root_stub: Path, fake_home: Path):
        """A directory accidentally created at the file's path must be
        treated the same as absent — a realistic contributor slip that a
        bare `-e` check would wrongly accept."""
        target = fake_home / ".claude" / "private-projects.md"
        target.unlink()
        target.mkdir()
        self._run_expecting_gate_refusal(repo_root_stub, fake_home)

    def test_dangling_symlink_at_path_exits_nonzero(self, repo_root_stub: Path, fake_home: Path):
        """A dangling symlink at the file's path must be treated the same
        as absent."""
        target = fake_home / ".claude" / "private-projects.md"
        target.unlink()
        target.symlink_to(fake_home / ".claude" / "nonexistent-target.md")
        self._run_expecting_gate_refusal(repo_root_stub, fake_home)

    def test_permission_denied_file_fails_the_gate(self, repo_root_stub: Path, fake_home: Path):
        """A permission-denied regular file must fail the gate — `-r`
        matches deny-private-project-refs.sh's own readability bar, so a
        file the hook can't read can't satisfy the gate either (else the
        contributor sees "you're protected" while the hook silently isn't
        reading anything)."""
        target = fake_home / ".claude" / "private-projects.md"
        target.chmod(0o000)
        try:
            self._run_expecting_gate_refusal(repo_root_stub, fake_home)
        finally:
            target.chmod(0o644)  # restore so tmp_path cleanup can remove it

    def test_comment_only_file_falls_through_to_venv_logic(self, repo_root_stub: Path, fake_home: Path):
        """The default fake_home fixture's file is comment-only — confirms
        that satisfies the gate and reaches the existing venv logic
        unchanged (no separate content requirement)."""
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_pinned_interpreter(repo_root_stub)
        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)
        assert result.returncode == 0, (
            f"Expected exit 0 with a comment-only private-projects.md; got "
            f"{result.returncode}\nstderr: {result.stderr}"
        )
        assert not (repo_root_stub / "venv_created.flag").exists(), (
            "the pinned interpreter's -m venv must not be called when .venv is already healthy"
        )

    def test_zero_byte_file_falls_through_to_venv_logic(self, repo_root_stub: Path, fake_home: Path):
        """A bare `touch`ed file (no header, no content) is behaviorally
        identical to a comment-only file under `-f && -r` — as valid an
        opt-in signal as the doc's own header-comment example."""
        (fake_home / ".claude" / "private-projects.md").write_text("")
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_pinned_interpreter(repo_root_stub)
        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)
        assert result.returncode == 0, (
            f"Expected exit 0 with a zero-byte private-projects.md; got "
            f"{result.returncode}\nstderr: {result.stderr}"
        )

    def test_claude_config_dir_override_is_honored(self, repo_root_stub: Path, fake_home: Path, tmp_path: Path):
        """A contributor on a diverged CLAUDE_CONFIG_DIR profile with their
        own populated private-projects.md there (and nothing at
        $HOME/.claude) must pass the gate."""
        (fake_home / ".claude" / "private-projects.md").unlink()
        diverged_dir = tmp_path / "diverged_config"
        diverged_dir.mkdir()
        (diverged_dir / "private-projects.md").write_text("Acme Corp\n")
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_pinned_interpreter(repo_root_stub)
        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"CLAUDE_CONFIG_DIR": str(diverged_dir)}
        )
        assert result.returncode == 0, (
            f"Expected exit 0 when CLAUDE_CONFIG_DIR points at a populated file; "
            f"got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_claude_config_dir_without_file_falls_back_to_home(
        self, repo_root_stub: Path, fake_home: Path, tmp_path: Path
    ):
        """Union, not swap: a CLAUDE_CONFIG_DIR with no private-projects.md
        of its own must fall back to the already-populated $HOME/.claude
        copy, not block — a contributor fully protected by the hook today
        (its own union resolves to the $HOME/.claude floor) must not be
        blocked by this gate."""
        diverged_dir = tmp_path / "diverged_config_no_file"
        diverged_dir.mkdir()
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_pinned_interpreter(repo_root_stub)
        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"CLAUDE_CONFIG_DIR": str(diverged_dir)}
        )
        assert result.returncode == 0, (
            f"Expected exit 0 — CLAUDE_CONFIG_DIR with no file of its own must fall "
            f"back to $HOME/.claude, not block; got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_non_absolute_claude_config_dir_falls_back_to_home(
        self, repo_root_stub: Path, fake_home: Path
    ):
        """A non-absolute CLAUDE_CONFIG_DIR must not be used for resolution
        at all — same fallback-to-floor behavior as unset, matching
        _lib_config_dir's own relative-value rejection
        (claude/.claude/hooks/_lib.sh)."""
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_pinned_interpreter(repo_root_stub)
        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"CLAUDE_CONFIG_DIR": "relative/path"}
        )
        assert result.returncode == 0, (
            f"Expected exit 0 — a non-absolute CLAUDE_CONFIG_DIR must fall back to "
            f"$HOME/.claude; got {result.returncode}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Test class: Step 7, the pyright-lsp local-scope install
# ---------------------------------------------------------------------------

class TestPyrightLspStep:
    """Step 7 runs only after a healthy .venv is confirmed. It is never
    fatal. It never touches check_venv_healthy. Each case isolates one
    branch. `_pyright_lsp_baseline` supplies the shared healthy-venv/pinned-interpreter
    setup. `_make_claude_shim` supplies a `claude` stub that varies only
    what the branch needs. Cases that reach the prompt run under a real
    pty."""

    def test_no_claude_cli_skips_and_still_prints_venv_summary(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "claude CLI not found" in result.stdout, result.stdout
        assert _PYRIGHT_LSP_PLUGIN in result.stdout
        assert "Done. .venv is ready" in result.stdout, (
            "Step 7 skipping must not suppress Step 6's own summary"
        )

    def test_marketplace_list_read_failure_skips_with_stderr_warning(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(bin_dir, marketplaces=None, plugins=[], call_log=call_log)
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "could not read registered marketplaces" in result.stderr, result.stderr
        assert "plugin install" not in call_log.read_text()

    def test_unregistered_marketplace_skips_naming_remedy(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(bin_dir, marketplaces=["some-other-marketplace"], plugins=[], call_log=call_log)
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "claude-plugins-official" in result.stdout
        assert "claude plugin marketplace add anthropics/claude-plugins-official" in result.stdout
        assert "every project on this profile" in result.stdout
        log_text = call_log.read_text()
        assert "plugin install" not in log_text
        assert "marketplace add" not in log_text

    def test_registered_and_not_installed_without_terminal_prints_command_and_skips(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir, marketplaces=["claude-plugins-official"], plugins=[], call_log=call_log
        )
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "not an interactive terminal" in result.stdout, result.stdout
        assert (
            f"cd {os.path.realpath(repo_root_stub)} && claude plugin install {_PYRIGHT_LSP_PLUGIN} --scope local"
            in result.stdout
        ), result.stdout
        assert "plugin install" not in call_log.read_text()

    def test_already_installed_skips_install(self, repo_root_stub: Path, fake_home: Path):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir,
            marketplaces=["claude-plugins-official"],
            plugins=[(_PYRIGHT_LSP_PLUGIN, "local", str(repo_root_stub))],
            call_log=call_log,
        )
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert f"{_PYRIGHT_LSP_PLUGIN} (already installed)" in result.stdout
        assert "plugin install" not in call_log.read_text()

    def test_local_entry_for_other_directory_still_reaches_install_arm(
        self, repo_root_stub: Path, fake_home: Path, tmp_path: Path
    ):
        """The deny side of the path match: a local-scope entry for the
        same id but a different directory must not count as installed
        here."""
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        other_dir = tmp_path / "other-checkout"
        other_dir.mkdir()
        _make_claude_shim(
            bin_dir,
            marketplaces=["claude-plugins-official"],
            plugins=[(_PYRIGHT_LSP_PLUGIN, "local", str(other_dir))],
            call_log=call_log,
        )
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "not an interactive terminal" in result.stdout, result.stdout
        assert "(already installed)" not in result.stdout, result.stdout

    def test_project_scope_entry_at_matching_path_still_reaches_install_arm(
        self, repo_root_stub: Path, fake_home: Path
    ):
        """Mirrors test_install_sh_project_scope_plugins.py:268-270: the
        `.scope == "local"` filter must be load-bearing, so a same-id,
        same-path entry at another scope must not read as installed
        here."""
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir,
            marketplaces=["claude-plugins-official"],
            plugins=[(_PYRIGHT_LSP_PLUGIN, "project", str(repo_root_stub))],
            call_log=call_log,
        )
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "not an interactive terminal" in result.stdout, result.stdout
        assert "(already installed)" not in result.stdout, result.stdout

    def test_interactive_yes_installs_at_local_scope(self, repo_root_stub: Path, fake_home: Path):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir, marketplaces=["claude-plugins-official"], plugins=[], call_log=call_log
        )
        _link_real_jq(bin_dir)

        result = _run_script_with_pty_stdin(repo_root_stub, fake_home, bin_dir, "y\n")

        assert result.returncode == 0, f"stderr: {result.stderr}"
        log_lines = call_log.read_text().splitlines()
        install_calls = [line for line in log_lines if line.startswith("plugin install")]
        assert install_calls == [f"plugin install {_PYRIGHT_LSP_PLUGIN} --scope local"], log_lines
        assert f"Install {_PYRIGHT_LSP_PLUGIN} at local scope? [y/N]" in result.stderr, result.stderr

    def test_interactive_enter_declines_and_leaves_plugin_uninstalled(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir, marketplaces=["claude-plugins-official"], plugins=[], call_log=call_log
        )
        _link_real_jq(bin_dir)

        result = _run_script_with_pty_stdin(repo_root_stub, fake_home, bin_dir, "\n")

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "plugin install" not in call_log.read_text()
        assert f"leaving {_PYRIGHT_LSP_PLUGIN} uninstalled" in result.stdout, result.stdout

    def test_interactive_eof_declines_and_leaves_plugin_uninstalled(
        self, repo_root_stub: Path, fake_home: Path
    ):
        """Pins the `|| answer=""` guard inside the real script, not only in
        the direct tests."""
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir, marketplaces=["claude-plugins-official"], plugins=[], call_log=call_log
        )
        _link_real_jq(bin_dir)

        result = _run_script_with_pty_stdin(repo_root_stub, fake_home, bin_dir, "\x04")

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "plugin install" not in call_log.read_text()
        assert f"leaving {_PYRIGHT_LSP_PLUGIN} uninstalled" in result.stdout, result.stdout

    def test_confirmed_install_failure_does_not_abort_script(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir,
            marketplaces=["claude-plugins-official"],
            plugins=[],
            call_log=call_log,
            install_exit=1,
        )
        _link_real_jq(bin_dir)

        result = _run_script_with_pty_stdin(repo_root_stub, fake_home, bin_dir, "y\n")

        assert result.returncode == 0, (
            f"A failed plugin install must not abort the script; got "
            f"{result.returncode}\nstderr: {result.stderr}"
        )
        assert "failed to install" in result.stderr, result.stderr

    def test_pyright_langserver_present_prints_path(self, repo_root_stub: Path, fake_home: Path):
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir,
            marketplaces=["claude-plugins-official"],
            plugins=[(_PYRIGHT_LSP_PLUGIN, "local", str(repo_root_stub))],
            call_log=call_log,
        )
        _link_real_jq(bin_dir)
        _write_stub(bin_dir, "pyright-langserver", "exit 0\n")

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "✓ pyright-langserver (" in result.stdout, result.stdout

    def test_pyright_langserver_absent_prints_hint_and_leaves_venv_sentinel_untouched(
        self, repo_root_stub: Path, fake_home: Path
    ):
        bin_dir, sentinel = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir,
            marketplaces=["claude-plugins-official"],
            plugins=[(_PYRIGHT_LSP_PLUGIN, "local", str(repo_root_stub))],
            call_log=call_log,
        )
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "pipx install pyright" in result.stdout, result.stdout
        assert not sentinel.exists(), (
            "the pyright-langserver probe must not feed into check_venv_healthy's "
            "recreate path"
        )

    def test_plugin_list_read_failure_proceeds_as_if_none_installed(
        self, repo_root_stub: Path, fake_home: Path
    ):
        """Mirrors install.sh's project-scope loop: a plugin-list read
        failure warns and proceeds as if nothing is installed, so the
        install arm is still reached."""
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir, marketplaces=["claude-plugins-official"], plugins=None, call_log=call_log
        )
        _link_real_jq(bin_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "could not read installed local-scope plugins" in result.stderr, result.stderr
        assert "not an interactive terminal" in result.stdout, result.stdout
        assert "plugin install" not in call_log.read_text()

    def test_jq_absent_skips_via_pipefail_not_shim_exit_code(
        self, repo_root_stub: Path, fake_home: Path, tmp_path: Path
    ):
        """jq absent must be caught by the real `set -o pipefail` pipe
        failure on `claude ... | jq -r ...`, not by a shim exit code.
        The shim's own `plugin marketplace list` call succeeds here.
        jq itself is what's missing.
        No _link_real_jq call: that's the point.
        """
        bin_dir, _ = _pyright_lsp_baseline(repo_root_stub)
        call_log = repo_root_stub / "claude_calls.log"
        _make_claude_shim(
            bin_dir, marketplaces=["claude-plugins-official"], plugins=[], call_log=call_log
        )
        farm_dir = tmp_path / "path-without-jq"
        farm_dir.mkdir()
        path_tail = build_path_without("jq", farm_dir)

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir, path_tail=path_tail)

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "could not read registered marketplaces" in result.stderr, result.stderr
        assert "plugin install" not in call_log.read_text()


# ---------------------------------------------------------------------------
# Test class: _confirm_plugin_install, sourced directly
# ---------------------------------------------------------------------------

def _run_confirm_plugin_install(answer: str) -> subprocess.CompletedProcess:
    script = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(_PLUGIN_INSTALL_LIB))}\n"
        f"if _confirm_plugin_install {_PYRIGHT_LSP_PLUGIN} local; then echo confirmed; else echo declined; fi\n"
    )
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", script],
        input=answer,
        capture_output=True,
        text=True,
        check=False,
    )


class TestConfirmPluginInstall:
    """An answer starting with y or Y confirms. Any other answer, Enter, and
    EOF decline."""

    @pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n"])
    def test_y_prefixed_answer_confirms(self, answer: str):
        result = _run_confirm_plugin_install(answer)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert result.stdout.strip() == "confirmed", result.stdout

    @pytest.mark.parametrize("answer", ["n\n", "\n", "x\n", ""])
    def test_other_answer_enter_and_eof_decline(self, answer: str):
        result = _run_confirm_plugin_install(answer)
        assert result.returncode == 0, (
            f"EOF must not trip set -e; got {result.returncode}, stderr: {result.stderr}"
        )
        assert result.stdout.strip() == "declined", result.stdout
