"""ShellCheck coverage: every tracked shell script is discovered and clean.

The failure mode these tests exist to prevent is *silent under-linting*: a
shell file that discovery never returns is never checked, and CI stays green
while the file rots. `scripts/list-shell-files.sh` owns discovery for both the
contributor lint command and these tests, so a bug in its shebang
*classification* pattern would be invisible to a test that reused the same
pattern — the tests below re-derive that classification independently rather
than recompiling the production regex. This independence covers shebang
matching only: both the production script and `_independently_expected_files()`
enumerate via the same `git ls-files -z` and both read only the first line of
each file, so a shared blind spot upstream of classification (e.g. a
byte-order mark before the shebang) is invisible to both.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import REPO_ROOT

DISCOVERY_SCRIPT = REPO_ROOT / "scripts" / "list-shell-files.sh"

# Tracked shell scripts that carry no .sh extension and are identifiable only
# by shebang. Hardcoded on purpose: in the discovery script an explicit list
# would rot into "did the author remember?", but in a test it is the known
# floor that makes a discovery regression fail loudly. This is a floor, not a
# ceiling — it only fails when a currently-listed file stops being discovered;
# it does nothing to catch a newly-added extensionless script that discovery
# also misses. Add to this list when a new extensionless shell script is
# committed.
KNOWN_EXTENSIONLESS_SHELL_FILES = frozenset(
    {
        "claude/.local/bin/analyze-context",
        "claude/.local/bin/claude-artifact",
        "claude/.local/bin/claude-auto",
        "claude/.local/bin/claude-workflow",
        "claude/.local/bin/cleanup-merged-branches",
        "claude/.local/bin/find-consumed-continuity-file",
        "claude/.local/bin/register-marketplace",
        "claude/.local/bin/resume-context",
        "claude/.local/bin/token-analyzer",
        "claude/.local/bin/update-claude-config-plugins",
        "claude/.local/bin/worktree-removal-status",
        "plugins/lovable-cloud/scripts/new-migration",
    }
)

# Deliberately looser than the production pattern in list-shell-files.sh, and
# written in Python's regex dialect rather than ERE, so the two cannot share a
# bug. Anything this matches must also be discovered.
_BROAD_SHEBANG = re.compile(r"^#!.*\b(ba|da|k|z)?sh\b")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _discovered_files() -> set[str]:
    """Run the production discovery script and parse its NUL-separated output."""
    out = subprocess.run(
        ["bash", str(DISCOVERY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {p for p in out.split("\0") if p}


def _independently_expected_files() -> set[str]:
    """Re-derive the shell-file set without reusing production logic."""
    expected: set[str] = set()
    for rel_path in _tracked_files():
        if rel_path.endswith(".sh"):
            expected.add(rel_path)
            continue
        abs_path = REPO_ROOT / rel_path
        if not abs_path.is_file():
            continue
        try:
            with abs_path.open("r", encoding="utf-8", errors="strict") as handle:
                first_line = handle.readline()
        except (OSError, UnicodeDecodeError):
            continue
        if _BROAD_SHEBANG.match(first_line):
            expected.add(rel_path)
    return expected


def _shellcheck_binary() -> str | None:
    return shutil.which("shellcheck")


def _require_shellcheck() -> str:
    """Resolve shellcheck, failing rather than skipping when running in CI.

    Skipping locally is right for a contributor who has not re-run
    install-dev.sh. Skipping in CI is not: a failed shellcheck-py install would
    turn "lint everything" into a silent no-op while the job still reports
    green, which is the same silent-success failure this suite exists to catch.
    """
    binary = _shellcheck_binary()
    if binary is None:
        if os.environ.get("CI"):
            pytest.fail(
                "shellcheck is not on PATH in CI — shellcheck-py failed to "
                "install. Failing rather than skipping: a skipped lint step "
                "reports green while checking nothing."
            )
        pytest.skip("shellcheck not installed; run ./install-dev.sh")
    return binary


def _xargs_invokes_command_on_empty_input() -> bool:
    """True when `xargs -0 <cmd>` on empty stdin still invokes <cmd> once.

    GNU xargs does this by default; BSD/macOS xargs does not (see
    `xargs -r`/`--no-run-if-empty` in the FreeBSD/macOS man page). Probes
    with `false`, not the real shellcheck binary, so this decision never
    depends on shellcheck being installed — that's `_require_shellcheck()`'s
    job, and it runs independently. May raise `OSError` or
    `subprocess.SubprocessError` if `xargs` itself isn't resolvable; callers
    distinguish that from a negative result rather than conflating the two.
    """
    result = subprocess.run(
        ["xargs", "-0", "false"], input="", capture_output=True, text=True
    )
    return result.returncode != 0


class TestRequireShellcheck:
    """Missing shellcheck fails in CI, not skips: a skip passes the lint vacuously."""

    # Catch BaseException and compare the outcome type exactly: a guard
    # collapsed to pytest.skip would otherwise escape pytest.raises(Failed)
    # and report this test as skipped rather than failed.
    def test_fails_in_ci_when_shellcheck_is_missing(self, monkeypatch):
        monkeypatch.setattr(sys.modules[__name__], "_shellcheck_binary", lambda: None)
        monkeypatch.setenv("CI", "true")
        with pytest.raises(BaseException) as outcome:
            _require_shellcheck()
        assert outcome.type is pytest.fail.Exception

    def test_skips_outside_ci_when_shellcheck_is_missing(self, monkeypatch):
        monkeypatch.setattr(sys.modules[__name__], "_shellcheck_binary", lambda: None)
        monkeypatch.delenv("CI", raising=False)
        with pytest.raises(BaseException) as outcome:
            _require_shellcheck()
        assert outcome.type is pytest.skip.Exception


class TestDiscovery:
    """scripts/list-shell-files.sh must return every tracked shell script."""

    def test_discovery_misses_nothing_an_independent_scan_finds(self):
        discovered = _discovered_files()
        expected = _independently_expected_files()
        missing = expected - discovered
        assert not missing, (
            "Shell files that an independent scan found but discovery did not "
            f"return — these would never be linted: {sorted(missing)}"
        )

    def test_known_extensionless_scripts_are_discovered(self):
        discovered = _discovered_files()
        missing = KNOWN_EXTENSIONLESS_SHELL_FILES - discovered
        assert not missing, (
            "Known extensionless shell scripts absent from discovery — the "
            f"shebang pattern has regressed: {sorted(missing)}"
        )

    def test_discovery_output_is_nul_separated(self):
        """Paths may contain whitespace; consumers rely on xargs -0."""
        raw = subprocess.run(
            ["bash", str(DISCOVERY_SCRIPT)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "\0" in raw, "Discovery output must be NUL-separated"
        assert "\n" not in raw, (
            "Discovery emitted a newline — output must be NUL-separated only, "
            "or paths containing newlines would split incorrectly"
        )


class TestRepoIsClean:
    """The tracked shell corpus passes shellcheck at its default severity."""

    def test_all_discovered_files_pass_shellcheck(self):
        binary = _require_shellcheck()
        files = sorted(_discovered_files())
        assert files, "Discovery returned nothing — the corpus cannot be empty"
        result = subprocess.run(
            [binary, "-f", "gcc", *files],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "shellcheck reported findings:\n"
            f"{result.stdout}{result.stderr}"
        )


class TestGateActuallyBites:
    """A clean tree proves today's code is clean, never that the gate works."""

    def test_flags_backtick_in_double_quoted_string(self, tmp_path: Path):
        """The defect class that motivated adopting shellcheck here.

        A backtick inside a double-quoted string holding another language's
        source is parsed by bash as command substitution, silently deleting the
        text. ShellCheck reports it as SC2006, a *style*-severity finding — so
        this test also pins the severity floor: raising it to `warning` would
        make this fail.
        """
        binary = _require_shellcheck()
        script = tmp_path / "backtick-regression.sh"
        script.write_text(
            '#!/usr/bin/env bash\n'
            'python3 -c "\n'
            "# use the `foo` helper below\n"
            "print('hi')\n"
            '"\n'
        )
        result = subprocess.run(
            [binary, "-f", "gcc", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "shellcheck accepted a backtick inside a double-quoted string — "
            "the gate would not catch the defect it was adopted for"
        )
        assert "SC2006" in result.stdout, (
            f"Expected SC2006; got:\n{result.stdout}{result.stderr}"
        )

    def test_xargs_zero_composition_exits_nonzero_on_empty_input(self):
        """Pins the `xargs -0 <shellcheck>` composition of the contributor command.

        README.md's lint command is
        `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
        This test drops the `scripts/venv-run.sh` wrapper and calls the
        resolved binary directly. Whether zero bytes of input still invoke
        shellcheck once (and therefore still hit its bad-syntax exit code)
        depends on GNU xargs running the command once when `-r` /
        `--no-run-if-empty` is absent. It does not go through
        `scripts/venv-run.sh` because CI installs with `pip` and has no `.venv`,
        so venv-run.sh would exit 127. That status would satisfy the test's
        `returncode != 0` assertion without shellcheck ever running.
        """
        try:
            invokes_on_empty = _xargs_invokes_command_on_empty_input()
        except (OSError, subprocess.SubprocessError) as exc:
            invokes_on_empty = False
            reason = f"could not run the xargs probe used to gate this test: {exc}"
        else:
            reason = (
                "local xargs does not invoke the command on empty input "
                "(BSD/macOS default); the ubuntu-24.04 CI runner uses GNU "
                "xargs, where this guarantee holds"
            )
        if not invokes_on_empty:
            if os.environ.get("CI"):
                pytest.fail(reason)
            pytest.skip(reason)
        binary = _require_shellcheck()
        result = subprocess.run(
            ["xargs", "-0", binary],
            input="",
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "`xargs -0 shellcheck` on empty input must not exit 0 — a broken "
            "discovery regex producing no output would silently pass the lint"
        )


class TestShellcheckrc:
    """The repo-root .shellcheckrc is what lets shellcheck run without flags."""

    def test_rc_file_exists_with_required_directives(self):
        rc_path = REPO_ROOT / ".shellcheckrc"
        assert rc_path.is_file(), ".shellcheckrc must exist at the repo root"
        body = rc_path.read_text()
        assert "external-sources=true" in body
        assert "source-path=SCRIPTDIR" in body

    def test_rc_resolves_relative_to_file_not_cwd(self, tmp_path: Path):
        """Sourced files resolve from the checked file's directory.

        Run from `/` so a cwd-relative implementation would fail. This is what
        allows CI, an editor integration, and an ad-hoc invocation from any
        directory to agree.
        """
        binary = _require_shellcheck()
        target = REPO_ROOT / "claude" / ".claude" / "hooks" / "require-code-review.sh"
        assert target.is_file(), "fixture hook missing; update this test"
        result = subprocess.run(
            [binary, "-f", "gcc", str(target)],
            cwd="/",
            capture_output=True,
            text=True,
        )
        assert "SC1091" not in result.stdout, (
            "SC1091 fired with cwd=/ — .shellcheckrc is not resolving relative "
            f"to the checked file:\n{result.stdout}"
        )
