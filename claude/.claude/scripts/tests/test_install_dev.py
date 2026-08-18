"""Tests for install-dev.sh.

The script bootstraps a contributor Python venv from requirements-dev.txt.
Tests exercise the critical branches:

1. CWD anchor checks: requirements-dev.txt absent → non-zero exit; .venv is a
   symlink → non-zero exit with symlink guidance.
2. ensurepip-missing on a Debian system → non-zero exit + apt guidance with
   version-specific package name (python3.NN-venv).
3. ensurepip-missing on a non-Debian system → non-zero exit + generic guidance,
   no apt mention.
4. An unhealthy existing .venv (pip-less stub) → removed and recreated without
   touching adjacent files.
5. Idempotency: a healthy .venv is not recreated on a second run.
6. pip install failure → non-zero exit (set -e propagates pip's exit code).
7. Apt package-name derivation from `python3 --version` output → version token
   matches the parsed version, including two-digit minors (3.10+).
8. private-projects.md opt-in gate: missing/directory/dangling-symlink/
   permission-denied → non-zero exit naming the file and the docs pointer;
   a present, readable regular file (comment-only or zero-byte) falls
   through unchanged. Resolution is a union matching
   deny-private-project-refs.sh's own: $HOME/.claude is the floor,
   overridden only when CLAUDE_CONFIG_DIR is absolute and its own copy
   exists.

PATH stubs replace system python3/ruff so tests run in isolation without
mutating the real .venv. HOME is isolated per test (see the `fake_home`
fixture) so the gate never reads the real invoking environment's
~/.claude/private-projects.md.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# install-dev.sh lives at the repo root: climb from scripts/tests/ (parents[0])
# → scripts/ (parents[1]) → claude/.claude/ (parents[2]) → claude/ (parents[3])
# → repo root (parents[4]).
REPO_ROOT = Path(__file__).parents[4]
SCRIPT = REPO_ROOT / "install-dev.sh"


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


def _run_script(
    cwd: Path, fake_home: Path, stub_bin: Path | None = None, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run install-dev.sh from cwd with an isolated PATH and HOME.

    HOME must be isolated (not the real invoking environment's $HOME) — the
    script's private-projects.md gate would otherwise read the real
    developer/CI machine's ~/.claude/private-projects.md."""
    system_path = "/usr/bin:/bin"
    path_val = f"{stub_bin}:{system_path}" if stub_bin else system_path
    env = {"PATH": path_val, "HOME": str(fake_home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(SCRIPT)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo_root_stub(tmp_path: Path) -> Path:
    """A temp directory that looks like the repo root: has requirements-dev.txt,
    no .venv. The real requirements-dev.txt is copied so pip-install stubs can
    receive the real file path without synthetic content."""
    real_requirements = REPO_ROOT / "requirements-dev.txt"
    shutil.copy(real_requirements, tmp_path / "requirements-dev.txt")
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


def _make_healthy_venv_stub(repo_root_stub: Path) -> None:
    """Create a minimal stub .venv whose health probe passes: python exits 0
    for any -c argument, pip exits 0, ruff and shellcheck print version lines.

    Every binary check_venv_healthy probes must be stubbed here — the probe
    calls .venv/bin/<tool> directly, so a PATH stub cannot satisfy it."""
    venv_bin = repo_root_stub / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/bash\nexit 0\n")
    (venv_bin / "python").chmod(0o755)
    (venv_bin / "pip").write_text("#!/bin/bash\nexit 0\n")
    (venv_bin / "pip").chmod(0o755)
    (venv_bin / "ruff").write_text('#!/bin/bash\necho "ruff 0.6.0"\nexit 0\n')
    (venv_bin / "ruff").chmod(0o755)
    (venv_bin / "shellcheck").write_text(
        '#!/bin/bash\nprintf "ShellCheck - shell script analysis tool\\n"\n'
        'printf "version: 0.11.0\\n"\nexit 0\n'
    )
    (venv_bin / "shellcheck").chmod(0o755)


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
    version-specific package (python3.NN-venv).

    _INSTALL_DEV_IS_DEBIAN=true forces the Debian branch regardless of
    whether /etc/debian_version exists in the test environment."""

    def test_exit_nonzero_and_apt_guidance_emitted(self, repo_root_stub: Path, fake_home: Path):
        bin_dir = repo_root_stub / "bin"
        # python3 stub: `import ensurepip` fails; `--version` reports 3.11.
        _write_stub(bin_dir, "python3", textwrap.dedent("""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 1
            fi
            if [ "$1" = "--version" ]; then
              echo "Python 3.11.9"
              exit 0
            fi
            exit 0
        """))

        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "true"}
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when ensurepip is missing; got {result.returncode}"
        )
        assert "apt install" in result.stderr, (
            f"Expected apt install guidance in stderr; got: {result.stderr!r}"
        )
        assert "python3.11-venv" in result.stderr, (
            f"Expected version-specific package name in stderr; got: {result.stderr!r}"
        )

    def test_version_token_present_in_package_name(self, repo_root_stub: Path, fake_home: Path):
        """Apt package name must embed the major.minor from python3 --version."""
        bin_dir = repo_root_stub / "bin"
        _write_stub(bin_dir, "python3", textwrap.dedent("""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 1
            fi
            if [ "$1" = "--version" ]; then
              echo "Python 3.12.3"
              exit 0
            fi
            exit 0
        """))

        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "true"}
        )

        assert result.returncode != 0
        assert "python3.12-venv" in result.stderr, (
            f"Expected python3.12-venv in stderr; got: {result.stderr!r}"
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
        bin_dir = repo_root_stub / "bin"
        _write_stub(bin_dir, "python3", textwrap.dedent("""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 1
            fi
            exit 0
        """))

        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "false"}
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when ensurepip is missing; got {result.returncode}"
        )
        assert "apt" not in result.stderr, (
            f"Debian branch must not fire on non-Debian; stderr: {result.stderr!r}"
        )
        # Generic message must name the concept of venv support
        generic_keywords = ["python3-venv", "venv support"]
        assert any(kw in result.stderr for kw in generic_keywords), (
            f"Expected one of {generic_keywords!r} in stderr; got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Test class: pip-less .venv → removed and recreated, adjacent files intact
# ---------------------------------------------------------------------------

class TestUnhealthyVenvIsRecreated:
    """If .venv exists but the health probe fails (no python executable,
    missing deps), the script removes .venv and recreates it without
    touching adjacent files in the directory."""

    def test_pipless_venv_removed_and_adjacent_files_untouched(self, repo_root_stub: Path, fake_home: Path):
        # Set up a stub .venv: has bin/python but no pip; health probe will fail
        # because the python stub cannot `import yaml, pytest`.
        venv_bin = repo_root_stub / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        venv_python = venv_bin / "python"
        venv_python.write_text("#!/bin/bash\nexit 1\n")  # -c "import yaml, pytest" fails
        venv_python.chmod(0o755)

        # A sentinel file adjacent to .venv — must survive the rm -rf .venv.
        adjacent_file = repo_root_stub / "sentinel.txt"
        adjacent_file.write_text("must-survive\n")

        bin_dir = repo_root_stub / "bin"
        # python3 stub: ensurepip passes; `-m venv <dir>` creates a minimal
        # healthy venv (python + pip + ruff) so check_venv_healthy passes after
        # recreation. ruff is created inside the venv because check_venv_healthy
        # calls .venv/bin/ruff directly — it cannot be satisfied by the PATH stub.
        _write_stub(bin_dir, "python3", textwrap.dedent("""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 0
            fi
            if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
              venv_dir="$3"
              mkdir -p "$venv_dir/bin"
              printf '%s\\n' '#!/bin/bash' 'exit 0' > "$venv_dir/bin/python"
              chmod +x "$venv_dir/bin/python"
              printf '%s\\n' '#!/bin/bash' 'exit 0' > "$venv_dir/bin/pip"
              chmod +x "$venv_dir/bin/pip"
              printf '%s\\n' '#!/bin/bash' 'echo "ruff 0.6.0"' 'exit 0' > "$venv_dir/bin/ruff"
              chmod +x "$venv_dir/bin/ruff"
              printf '%s\\n' '#!/bin/bash' 'echo "version: 0.11.0"' 'exit 0' > "$venv_dir/bin/shellcheck"
              chmod +x "$venv_dir/bin/shellcheck"
              exit 0
            fi
            exit 0
        """))

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


# ---------------------------------------------------------------------------
# Test class: idempotency — healthy .venv is not recreated on a second run
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Running install-dev.sh on an already-healthy .venv must not remove or
    recreate it — pip install (no-op when pins are satisfied) should be the
    only write operation."""

    def test_healthy_venv_not_recreated(self, repo_root_stub: Path, fake_home: Path):
        """A second run when .venv is already healthy must exit 0 and skip
        the python3 -m venv creation step."""
        _make_healthy_venv_stub(repo_root_stub)

        # Sentinel: touched by the stub if python3 -m venv is called.
        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = repo_root_stub / "bin"
        _write_stub(bin_dir, "python3", textwrap.dedent(f"""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 0
            fi
            if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
              touch '{sentinel}'
              exit 0
            fi
            exit 0
        """))

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode == 0, (
            f"Expected exit 0 on a healthy venv; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert not sentinel.exists(), (
            "python3 -m venv must not be called when .venv is already healthy"
        )
        assert "shellcheck=0.11.0" in result.stdout, (
            "Expected the printed version summary to contain shellcheck=0.11.0, "
            "parsed from `shellcheck --version`'s `version:` line via "
            f"install-dev.sh's `awk '/^version:/ {{print $2}}'`; got stdout: "
            f"{result.stdout!r}"
        )

    def test_venv_missing_shellcheck_is_recreated(self, repo_root_stub: Path, fake_home: Path):
        """A .venv missing only shellcheck must be treated unhealthy, or the
        probe passes and the contributor is left with no shell-lint tool."""
        _make_healthy_venv_stub(repo_root_stub)
        # Remove only shellcheck — everything else still passes the probe.
        (repo_root_stub / ".venv" / "bin" / "shellcheck").unlink()

        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = repo_root_stub / "bin"
        _write_stub(bin_dir, "python3", textwrap.dedent(f"""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 0
            fi
            if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
              touch '{sentinel}'
              venv_dir="$3"
              mkdir -p "$venv_dir/bin"
              printf '%s\\n' '#!/bin/bash' 'exit 0' > "$venv_dir/bin/python"
              chmod +x "$venv_dir/bin/python"
              printf '%s\\n' '#!/bin/bash' 'exit 0' > "$venv_dir/bin/pip"
              chmod +x "$venv_dir/bin/pip"
              printf '%s\\n' '#!/bin/bash' 'echo "ruff 0.6.0"' 'exit 0' > "$venv_dir/bin/ruff"
              chmod +x "$venv_dir/bin/ruff"
              printf '%s\\n' '#!/bin/bash' 'echo "version: 0.11.0"' 'exit 0' > "$venv_dir/bin/shellcheck"
              chmod +x "$venv_dir/bin/shellcheck"
              exit 0
            fi
            exit 0
        """))

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert sentinel.exists(), (
            "A .venv without shellcheck must be recreated — check_venv_healthy "
            "is what makes the new pin reach existing contributors"
        )
        assert result.returncode == 0, (
            f"Expected exit 0 after recreation; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Test class: pip install failure propagates as non-zero exit
# ---------------------------------------------------------------------------

class TestPipInstallFailure:
    """If pip install fails after venv creation, set -e propagates the
    non-zero exit code — the script must not silently succeed."""

    def test_pip_failure_exits_nonzero(self, repo_root_stub: Path, fake_home: Path):
        bin_dir = repo_root_stub / "bin"
        # python3 stub: ensurepip passes; -m venv creates a venv whose pip exits 1.
        _write_stub(bin_dir, "python3", textwrap.dedent("""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 0
            fi
            if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
              venv_dir="$3"
              mkdir -p "$venv_dir/bin"
              printf '%s\\n' '#!/bin/bash' 'exit 0' > "$venv_dir/bin/python"
              chmod +x "$venv_dir/bin/python"
              printf '%s\\n' '#!/bin/bash' 'exit 1' > "$venv_dir/bin/pip"
              chmod +x "$venv_dir/bin/pip"
              printf '%s\\n' '#!/bin/bash' 'echo "ruff 0.6.0"' 'exit 0' > "$venv_dir/bin/ruff"
              chmod +x "$venv_dir/bin/ruff"
              printf '%s\\n' '#!/bin/bash' 'echo "version: 0.11.0"' 'exit 0' > "$venv_dir/bin/shellcheck"
              chmod +x "$venv_dir/bin/shellcheck"
              exit 0
            fi
            exit 0
        """))

        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)

        assert result.returncode != 0, (
            f"Expected non-zero exit when pip install fails; got {result.returncode}\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Test class: apt package-name derivation from python3 --version
# ---------------------------------------------------------------------------

class TestAptPackageNameVersionDerivation:
    """The apt install guidance must embed the version token parsed from
    `python3 --version` — python3.NN-venv, not a hardcoded version.

    Uses distinct minor versions (3.9 single-digit, 3.10 two-digit) to exercise
    the `grep -oE '3\\.[0-9]+'` extraction on different minor-version widths.
    Two-digit minor (3.10) is the non-trivial case — a naive `[0-9]` would
    stop at the first digit and emit `python3.1-venv` instead of `python3.10-venv`.
    """

    def _run_with_python_version(self, repo_root_stub: Path, fake_home: Path, version_string: str) -> subprocess.CompletedProcess:
        bin_dir = repo_root_stub / "stub_bin" / version_string.replace(".", "_")
        _write_stub(bin_dir, "python3", textwrap.dedent(f"""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 1
            fi
            if [ "$1" = "--version" ]; then
              echo "Python {version_string}"
              exit 0
            fi
            exit 0
        """))
        return _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "true"}
        )

    def test_version_3_9_produces_python3_9_venv(self, repo_root_stub: Path, fake_home: Path):
        """Single-digit minor: 3.9 → python3.9-venv."""
        result = self._run_with_python_version(repo_root_stub, fake_home, "3.9.18")
        assert result.returncode != 0
        assert "python3.9-venv" in result.stderr, (
            f"Expected python3.9-venv; got: {result.stderr!r}"
        )

    def test_version_3_10_produces_python3_10_venv(self, repo_root_stub: Path, fake_home: Path):
        """Two-digit minor: 3.10 → python3.10-venv (not python3.1-venv).

        Exercises that `grep -oE '3\\.[0-9]+'` captures the full minor token
        including both digits when the minor is >= 10."""
        result = self._run_with_python_version(repo_root_stub, fake_home, "3.10.14")
        assert result.returncode != 0
        assert "python3.10-venv" in result.stderr, (
            f"Expected python3.10-venv; got: {result.stderr!r}"
        )
        assert "python3.1-venv" not in result.stderr, (
            "Single-digit truncation: grep must not stop at the first digit"
        )

    def test_unparseable_version_exits_nonzero_with_guidance(self, repo_root_stub: Path, fake_home: Path):
        """If python3 --version output does not match the 3.X pattern, the
        script must exit non-zero and emit a 'could not parse' error."""
        bin_dir = repo_root_stub / "stub_bin" / "unparseable"
        _write_stub(bin_dir, "python3", textwrap.dedent("""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 1
            fi
            if [ "$1" = "--version" ]; then
              echo "Python unknown"
              exit 0
            fi
            exit 0
        """))
        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"_INSTALL_DEV_IS_DEBIAN": "true"}
        )
        assert result.returncode != 0
        assert "could not parse" in result.stderr, (
            f"Expected 'could not parse' in stderr; got: {result.stderr!r}"
        )


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

    def _stub_healthy_python3(self, repo_root_stub: Path) -> Path:
        """A python3 stub whose ensurepip check passes and whose -m venv
        touches a sentinel — used to confirm the gate's fall-through cases
        don't recreate an already-healthy .venv."""
        sentinel = repo_root_stub / "venv_created.flag"
        bin_dir = repo_root_stub / "bin"
        _write_stub(bin_dir, "python3", textwrap.dedent(f"""\
            if [ "$1" = "-c" ] && [ "$2" = "import ensurepip" ]; then
              exit 0
            fi
            if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
              touch '{sentinel}'
              exit 0
            fi
            exit 0
        """))
        return bin_dir

    def test_missing_file_exits_nonzero_with_guidance(self, repo_root_stub: Path, fake_home: Path):
        (fake_home / ".claude" / "private-projects.md").unlink()
        result = _run_script(repo_root_stub, fake_home)
        assert result.returncode != 0, (
            f"Expected non-zero exit when private-projects.md is absent; got {result.returncode}"
        )
        assert "private-projects.md" in result.stderr, (
            f"Error message must name the missing file; got: {result.stderr!r}"
        )
        assert "private-project-redaction.md" in result.stderr, (
            f"Error message must point at the docs; got: {result.stderr!r}"
        )
        assert not (repo_root_stub / ".venv").exists(), (
            "the gate must fail before any venv side effect runs"
        )

    def test_directory_at_path_exits_nonzero(self, repo_root_stub: Path, fake_home: Path):
        """A directory accidentally created at the file's path must be
        treated the same as absent — a realistic contributor slip that a
        bare `-e` check would wrongly accept."""
        target = fake_home / ".claude" / "private-projects.md"
        target.unlink()
        target.mkdir()
        result = _run_script(repo_root_stub, fake_home)
        assert result.returncode != 0, (
            f"Expected non-zero exit when private-projects.md is a directory; got {result.returncode}"
        )
        assert "private-projects.md" in result.stderr
        assert not (repo_root_stub / ".venv").exists()

    def test_dangling_symlink_at_path_exits_nonzero(self, repo_root_stub: Path, fake_home: Path):
        """A dangling symlink at the file's path must be treated the same
        as absent."""
        target = fake_home / ".claude" / "private-projects.md"
        target.unlink()
        target.symlink_to(fake_home / ".claude" / "nonexistent-target.md")
        result = _run_script(repo_root_stub, fake_home)
        assert result.returncode != 0, (
            f"Expected non-zero exit for a dangling symlink; got {result.returncode}"
        )
        assert "private-projects.md" in result.stderr
        assert not (repo_root_stub / ".venv").exists()

    def test_permission_denied_file_fails_the_gate(self, repo_root_stub: Path, fake_home: Path):
        """A permission-denied regular file must fail the gate — `-r`
        matches deny-private-project-refs.sh's own readability bar, so a
        file the hook can't read can't satisfy the gate either (else the
        contributor sees "you're protected" while the hook silently isn't
        reading anything)."""
        target = fake_home / ".claude" / "private-projects.md"
        target.chmod(0o000)
        try:
            result = _run_script(repo_root_stub, fake_home)
        finally:
            target.chmod(0o644)  # restore so tmp_path cleanup can remove it
        assert result.returncode != 0, (
            f"Expected non-zero exit — the gate must match the hook's [ -r ] bar; "
            f"got {result.returncode}\nstdout: {result.stdout}"
        )
        assert not (repo_root_stub / ".venv").exists()

    def test_comment_only_file_falls_through_to_venv_logic(self, repo_root_stub: Path, fake_home: Path):
        """The default fake_home fixture's file is comment-only — confirms
        that satisfies the gate and reaches the existing venv logic
        unchanged (no separate content requirement)."""
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_python3(repo_root_stub)
        result = _run_script(repo_root_stub, fake_home, stub_bin=bin_dir)
        assert result.returncode == 0, (
            f"Expected exit 0 with a comment-only private-projects.md; got "
            f"{result.returncode}\nstderr: {result.stderr}"
        )
        assert not (repo_root_stub / "venv_created.flag").exists(), (
            "python3 -m venv must not be called when .venv is already healthy"
        )

    def test_zero_byte_file_falls_through_to_venv_logic(self, repo_root_stub: Path, fake_home: Path):
        """A bare `touch`ed file (no header, no content) is behaviorally
        identical to a comment-only file under `-f && -r` — as valid an
        opt-in signal as the doc's own header-comment example."""
        (fake_home / ".claude" / "private-projects.md").write_text("")
        _make_healthy_venv_stub(repo_root_stub)
        bin_dir = self._stub_healthy_python3(repo_root_stub)
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
        bin_dir = self._stub_healthy_python3(repo_root_stub)
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
        bin_dir = self._stub_healthy_python3(repo_root_stub)
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
        bin_dir = self._stub_healthy_python3(repo_root_stub)
        result = _run_script(
            repo_root_stub, fake_home, stub_bin=bin_dir, extra_env={"CLAUDE_CONFIG_DIR": "relative/path"}
        )
        assert result.returncode == 0, (
            f"Expected exit 0 — a non-absolute CLAUDE_CONFIG_DIR must fall back to "
            f"$HOME/.claude; got {result.returncode}\nstderr: {result.stderr}"
        )
