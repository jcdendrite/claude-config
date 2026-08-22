"""Tests for ci-watch.sh.

The gh CLI is replaced in every test by a PATH shim modeling the exact
sequence ci-watch.sh runs: `pr view --json headRefOid --jq .headRefOid`,
then `pr checks <n> --watch`, then (unless the watch output already signals
zero checks) `pr checks <n> --json name,bucket,description,link,workflow`.
No real network or git-hosting call is made — the shim validates the exact
args each invocation passes, so an argument-construction regression fails
the test rather than silently returning canned data regardless of shape.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "ci-watch.sh"

_PR_NUMBER = "713"
# A placeholder value, not a real commit SHA — ci-watch.sh treats this as
# an opaque string, so it need not look hex-shaped. Deliberately non-hex
# (contains 'z') to avoid the redaction gate's long-hex-identifier detector,
# which pattern-matches on shape alone and can't distinguish real from
# fabricated.
_HEAD_SHA = "placeholder-sha-not-a-real-commit-zzzzz"


def _gh_shim_source(
    *,
    view_fails=False,
    view_stderr="",
    watch_output,
    watch_exit=0,
    json_fails=False,
    json_stderr="",
    json_payload=None,
):
    """Return source for a gh shim script modeling one ci-watch.sh run.

    view_fails    -> `gh pr view --json headRefOid` exits non-zero
    view_stderr   -> stderr text that call writes when it fails
    watch_output  -> the combined stdout+stderr text `gh pr checks --watch`
                      writes (ci-watch.sh discards its exit code, so this is
                      the only channel the script actually reads)
    watch_exit    -> the exit code `--watch` itself returns (ci-watch.sh
                      ignores this — asserted here only to document that)
    json_fails    -> the follow-up `gh pr checks --json ...` call exits
                      non-zero with no stdout
    json_stderr   -> stderr text that call writes when it fails
    json_payload  -> the JSON array that call prints on success
    """
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys

        PR_NUMBER = {_PR_NUMBER!r}
        HEAD_SHA = {_HEAD_SHA!r}
        VIEW_FAILS = {view_fails!r}
        VIEW_STDERR = {view_stderr!r}
        WATCH_OUTPUT = {watch_output!r}
        WATCH_EXIT = {watch_exit!r}
        JSON_FAILS = {json_fails!r}
        JSON_STDERR = {json_stderr!r}
        JSON_PAYLOAD = {json.dumps(json_payload)!r}

        args = sys.argv[1:]

        if args == ["pr", "view", PR_NUMBER, "--json", "headRefOid", "--jq", ".headRefOid"]:
            if VIEW_FAILS:
                sys.stderr.write(VIEW_STDERR)
                sys.exit(1)
            print(HEAD_SHA)
            sys.exit(0)

        if args == ["pr", "checks", PR_NUMBER, "--watch"]:
            sys.stderr.write(WATCH_OUTPUT)
            sys.exit(WATCH_EXIT)

        if args == ["pr", "checks", PR_NUMBER, "--json",
                     "name,bucket,description,link,workflow"]:
            if JSON_FAILS:
                sys.stderr.write(JSON_STDERR)
                sys.exit(1)
            print(JSON_PAYLOAD)
            sys.exit(0)

        sys.stderr.write("gh shim: unexpected args: " + repr(args) + chr(10))
        sys.exit(1)
    """)


@pytest.fixture()
def fake_gh(tmp_path):
    """Yield a factory that installs a gh shim and returns the env dict."""
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()

    def _make_env(**kwargs) -> dict:
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source(**kwargs))
        shim_py.chmod(0o755)
        new_path = str(shim_dir) + ":" + os.environ.get("PATH", "")
        return {**os.environ, "PATH": new_path}

    return _make_env


def _run(env, *args):
    return subprocess.run(
        [str(_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_usage_error_on_missing_arg():
    result = _run({**os.environ})
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "CI_RESULT: error" in result.stdout


def test_usage_error_on_empty_arg():
    # The usage guard is `[[ "$#" -ne 1 ]] || [[ -z "$1" ]]` — a single
    # empty-string argument trips the second half, never exercised by the
    # zero-arg case above.
    result = _run({**os.environ}, "")
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "CI_RESULT: error" in result.stdout


def test_zero_checks_reports_none(fake_gh):
    env = fake_gh(
        watch_output="no checks reported on the 'defer-ci-check-to-end' branch\n",
        watch_exit=1,
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    assert f"LAUNCH_SHA: {_HEAD_SHA}" in result.stdout
    assert "CI_RESULT: none" in result.stdout
    # The follow-up --json call must never fire once the zero-checks text matched.
    assert "CI_RESULT: checks" not in result.stdout


def test_resolved_checks_reports_snapshot(fake_gh):
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    # Full-output equality, not substring membership: the script's own header
    # comment documents stdout as exactly one LAUNCH_SHA line followed by one
    # CI_RESULT line — pin that shape, not just that the pieces appear.
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0] == f"LAUNCH_SHA: {_HEAD_SHA}"
    assert lines[1].startswith("CI_RESULT: checks ")
    assert json.loads(lines[1][len("CI_RESULT: checks "):]) == checks


def test_json_snapshot_failure_reports_error(fake_gh):
    env = fake_gh(
        watch_output="Some checks are still pending\n",
        watch_exit=8,
        json_fails=True,
        json_stderr="error connecting to api.github.com\n",
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    assert "CI_RESULT: error" in result.stdout
    assert "CI_RESULT: none" not in result.stdout
    assert "CI_RESULT: checks" not in result.stdout
    # The captured gh stderr must reach the terminal line, not be discarded.
    assert "error connecting to api.github.com" in result.stdout


def test_head_sha_lookup_failure_reports_error_before_watch(fake_gh):
    env = fake_gh(
        view_fails=True,
        view_stderr="HTTP 404: Not Found\n",
        watch_output="",
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    assert "CI_RESULT: error" in result.stdout
    assert "LAUNCH_SHA:" not in result.stdout
    assert "HTTP 404: Not Found" in result.stdout


def test_missing_gh_reports_error(tmp_path):
    # ci-watch.sh's own shebang (`#!/usr/bin/env bash`) needs `bash` on
    # PATH to even start, so a bare empty PATH can't isolate "gh missing"
    # from "bash missing" (env exits 127, not the script's own error path).
    # The "gh missing" branch exits before any other external command runs
    # (checked ahead of the mktemp call), so an allowlist PATH containing
    # only a bash symlink is sufficient — no need to scan and filter the
    # real PATH for gh's location, matching the simpler, more robust
    # allowlist pattern test_cleanup_idle_open_pr_worktrees.py's own
    # "gh missing" test already uses.
    bash_only_dir = tmp_path / "bash_only"
    bash_only_dir.mkdir()
    (bash_only_dir / "bash").symlink_to(shutil.which("bash"))
    env = {**os.environ, "PATH": str(bash_only_dir)}
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    assert "CI_RESULT: error gh not installed" in result.stdout
