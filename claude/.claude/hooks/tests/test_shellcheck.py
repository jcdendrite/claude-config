"""ShellCheck coverage: every tracked shell script is discovered and clean.

The failure mode these tests exist to prevent is *silent under-linting*: a
shell file that discovery never returns is never checked, and CI stays green
while the file rots. `scripts/list-shell-files.sh` owns discovery for both CI
and these tests, so a bug in its shebang *classification* pattern would be
invisible to a test that reused the same pattern — the tests below re-derive
that classification independently rather than recompiling the production
regex. This independence covers shebang matching only: both the production
script and `_independently_expected_files()` enumerate via the same
`git ls-files -z` and both read only the first line of each file, so a shared
blind spot upstream of classification (e.g. a byte-order mark before the
shebang) is invisible to both.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    REPO_ROOT,
    init_ci_detect_step_test_repo,
    run_ci_detect_step,
)

DISCOVERY_SCRIPT = REPO_ROOT / "scripts" / "list-shell-files.sh"

# Tracked shell scripts that carry no .sh extension and are identifiable only
# by shebang. Hardcoded on purpose: in CI an explicit list would rot into "did
# the author remember?", but in a test it is the known floor that makes a
# discovery regression fail loudly. This is a floor, not a ceiling — it only
# fails when a currently-listed file stops being discovered; it does nothing
# to catch a newly-added extensionless script that discovery also misses. Add
# to this list when a new extensionless shell script is committed.
KNOWN_EXTENSIONLESS_SHELL_FILES = frozenset(
    {
        "claude/.local/bin/analyze-context",
        "claude/.local/bin/claude-artifact",
        "claude/.local/bin/claude-auto",
        "claude/.local/bin/claude-workflow",
        "claude/.local/bin/cleanup-merged-branches",
        "claude/.local/bin/register-marketplace",
        "claude/.local/bin/resume-context",
        "claude/.local/bin/token-analyzer",
        "claude/.local/bin/update-claude-config-plugins",
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


class TestCiGateCoversDiscovery:
    """The CI path filter must trigger on everything discovery lints.

    Discovery is content-based (shebang); the CI gate is necessarily
    path-based. If SHELL_REGEX is narrower than discovery, edits to the
    uncovered files never run the shellcheck step — the silent-under-linting
    failure this suite exists to prevent, reintroduced one layer up in the
    workflow rather than in discovery itself.
    """

    @staticmethod
    def _shell_regex_pattern() -> str:
        workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
        match = re.search(r"^\s*SHELL_REGEX='([^']+)'", workflow, re.MULTILINE)
        assert match, "SHELL_REGEX not found in tests.yml — did the gate move?"
        return match.group(1)

    @staticmethod
    def _grep_e_matches(pattern: str, candidate: str) -> bool:
        """Match via the real `grep -E` binary rather than `re.compile`.

        CI evaluates SHELL_REGEX with `grep -E`, a POSIX ERE engine. Today ERE
        and Python's `re` agree on this pattern, but a future GNU-ERE-only
        construct would be silently reinterpreted by `re.compile` instead of
        erroring — leaving this test green while CI's `grep -E` diverges.
        Shelling out to `grep -E` keeps the test in the same dialect CI uses.
        """
        result = subprocess.run(
            ["grep", "-E", pattern],
            input=candidate,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def test_every_discovered_file_matches_the_ci_filter(self):
        pattern = self._shell_regex_pattern()
        unmatched = sorted(
            p for p in _discovered_files() if not self._grep_e_matches(pattern, p)
        )
        assert not unmatched, (
            "Discovered shell files that SHELL_REGEX in .github/workflows/"
            "tests.yml would not trigger on — editing one of these alone would "
            f"skip the shellcheck step entirely: {unmatched}"
        )

    def test_filter_triggers_on_the_config_and_discovery_script(self):
        """Neither is a .sh consumer, but both change what gets linted."""
        pattern = self._shell_regex_pattern()
        for path in (".shellcheckrc", "scripts/list-shell-files.sh"):
            assert self._grep_e_matches(pattern, path), (
                f"SHELL_REGEX must match {path} — editing it changes lint "
                "results without touching any linted file"
            )


class TestDetectStepFailOpenSetsShellChanged:
    """Executes the detect step's actual `run:` body, not its extracted pattern.

    TestCiGateCoversDiscovery only inspects SHELL_REGEX as a string — it
    cannot see a branch that returns before that pattern is ever evaluated.
    The detect step has exactly such a branch: on an unresolvable or
    zero-SHA BASE it echoes changed=true and exits before the block that
    computes shell_changed ever runs, so shell_changed would be absent
    (not false) rather than true. An absent output never equals 'true', so
    the shellcheck step would silently skip while CI reports green. These
    tests run the step's real shell body under bash against a throwaway git
    repository to prove both the normal and fail-open paths set
    shell_changed correctly.
    """

    def test_resolvable_base_with_shell_file_changed_sets_shell_changed_true(
        self, tmp_path: Path
    ):
        repo, base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"scripts/example.sh": "#!/bin/bash\necho hi\n"}
        )
        outputs = run_ci_detect_step(repo, base_sha, head_sha)
        assert outputs.get("shell_changed") == "true", (
            "Expected shell_changed=true for a resolvable BASE with a .sh "
            f"file changed; got outputs: {outputs}"
        )

    def test_zero_sha_base_sets_shell_changed_true(self, tmp_path: Path):
        """The fail-open regression this test class exists to guard.

        The zero-SHA/unresolvable-BASE early-exit branch must set
        shell_changed=true alongside changed=true. That branch returns before
        the block further down the step that otherwise computes
        shell_changed, so if the early exit only sets changed=true,
        shell_changed stays absent rather than false — and an absent output
        never equals 'true', so the shellcheck step silently skips while CI
        reports green. If this assertion fails, that is exactly the
        regression that has crept back in.
        """
        repo, _base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"README.md": "second\n"}
        )
        zero_sha = "0" * 40
        outputs = run_ci_detect_step(repo, zero_sha, head_sha)
        assert outputs.get("shell_changed") == "true", (
            "shell_changed must be 'true' when BASE is the zero SHA (the "
            f"fail-open path) — got {outputs.get('shell_changed')!r}. The "
            "detect step's early-exit branch for an unresolvable/zero BASE "
            "must set shell_changed=true alongside changed=true, or the "
            "shellcheck step silently skips while CI reports green."
        )

    def test_resolvable_base_with_only_non_shell_file_changed_sets_shell_changed_false(
        self, tmp_path: Path
    ):
        repo, base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"docs/notes.md": "some notes\n"}
        )
        outputs = run_ci_detect_step(repo, base_sha, head_sha)
        assert outputs.get("shell_changed") == "false", (
            "Expected shell_changed=false for a resolvable BASE touching only "
            f"a non-shell, non-test file; got outputs: {outputs}"
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

    def test_exits_nonzero_when_given_no_files(self):
        """An empty file list must fail, not pass vacuously.

        If discovery ever returns nothing, the CI step has to go red. Exit 3
        is shellcheck's "invoked with bad syntax" code.
        """
        binary = _require_shellcheck()
        result = subprocess.run(
            [binary], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        assert result.returncode != 0, (
            "shellcheck with no file arguments must not exit 0, or a broken "
            "discovery regex would silently pass CI"
        )

    def test_xargs_zero_composition_exits_nonzero_on_empty_input(self):
        """Pins the CI step's actual invocation, not bare shellcheck alone.

        The CI step runs `./scripts/list-shell-files.sh | xargs -0 shellcheck`,
        not `shellcheck` directly. Whether zero bytes of input still invoke
        `shellcheck` once (and therefore still hit its bad-syntax exit code)
        depends on GNU xargs running the command once when `-r` /
        `--no-run-if-empty` is absent — a composition the bare-binary test
        above cannot observe.
        """
        try:
            invokes_on_empty = _xargs_invokes_command_on_empty_input()
        except (OSError, subprocess.SubprocessError) as exc:
            invokes_on_empty = False
            reason = f"could not run the xargs probe used to gate this test: {exc}"
        else:
            reason = (
                "local xargs does not invoke the command on empty input "
                "(BSD/macOS default); CI's ubuntu-24.04 runner uses GNU "
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
            "discovery regex producing no output would silently pass CI"
        )


class TestShellcheckrc:
    """The repo-root .shellcheckrc is what lets the CI step run without flags."""

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
