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

from .conftest import _base_test_env

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "ci-watch.sh"

_PR_NUMBER = "713"
# Opaque placeholder (ci-watch.sh doesn't validate SHA shape) — deliberately
# non-hex ('z') to dodge the redaction gate's shape-only long-hex-identifier
# detector.
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
    token_log=None,
):
    """Return source for a gh shim script modeling one ci-watch.sh run.

    view_fails    -> `gh pr view --json headRefOid` exits non-zero
    view_stderr   -> stderr text that call writes when it fails
    watch_output  -> the combined stdout+stderr text `gh pr checks --watch`
                      writes (ci-watch.sh discards its exit code, so this is
                      the only channel the script actually reads)
    watch_exit    -> the exit code `--watch` itself returns; ci-watch.sh
                      ignores it and reads WATCH_OUTPUT instead
    json_fails    -> the follow-up `gh pr checks --json ...` call exits
                      non-zero with no stdout
    json_stderr   -> stderr text that call writes when it fails
    json_payload  -> the JSON array that call prints on success
    token_log     -> optional path; when given, the shim appends one
                      tab-separated line per matched call ("view"/"watch"/
                      "json") recording that call's own GH_TOKEN and
                      GH_ENTERPRISE_TOKEN — lets a test assert which calls
                      saw the CI_CHECKS_GH_TOKEN override and which saw the
                      ambient token untouched.
    """
    token_log_repr = repr(str(token_log)) if token_log is not None else "None"
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import os
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
        TOKEN_LOG = {token_log_repr}

        def log_call(name):
            if TOKEN_LOG is None:
                return
            with open(TOKEN_LOG, "a") as f:
                f.write(
                    name + "\\t"
                    + os.environ.get("GH_TOKEN", "") + "\\t"
                    + os.environ.get("GH_ENTERPRISE_TOKEN", "") + "\\n"
                )

        args = sys.argv[1:]

        if args == ["pr", "view", PR_NUMBER, "--json", "headRefOid", "--jq", ".headRefOid"]:
            log_call("view")
            if VIEW_FAILS:
                sys.stderr.write(VIEW_STDERR)
                sys.exit(1)
            print(HEAD_SHA)
            sys.exit(0)

        if args == ["pr", "checks", PR_NUMBER, "--watch"]:
            log_call("watch")
            sys.stderr.write(WATCH_OUTPUT)
            sys.exit(WATCH_EXIT)

        if args == ["pr", "checks", PR_NUMBER, "--json",
                     "name,bucket,description,link,workflow"]:
            log_call("json")
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
    """Yield a factory that installs a gh shim and returns the env dict.

    extra_env layers CI_CHECKS_GH_TOKEN/GH_TOKEN fixtures on top of the
    credential-scrubbed base env, modeling a container that has (or hasn't)
    exported the classic-PAT override — never sourced from the calling
    test process's own os.environ, which _base_test_env() has already
    stripped of every _SENSITIVE_ENV_VARS entry.
    """
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()

    def _make_env(*, extra_env=None, token_log=None, **kwargs) -> dict:
        shim_py = shim_dir / "gh"
        shim_py.write_text(_gh_shim_source(token_log=token_log, **kwargs))
        shim_py.chmod(0o755)
        new_path = str(shim_dir) + ":" + os.environ.get("PATH", "")
        env = {**_base_test_env(), "PATH": new_path}
        if extra_env:
            env.update(extra_env)
        return env

    return _make_env


def _parse_token_log(path: Path) -> dict[str, tuple[str, str]]:
    """Map call name ("view"/"watch"/"json") -> (GH_TOKEN, GH_ENTERPRISE_TOKEN)
    as recorded by the gh shim's log_call, one entry per matched invocation."""
    calls = {}
    for line in path.read_text().splitlines():
        name, gh_token, gh_enterprise_token = line.split("\t")
        calls[name] = (gh_token, gh_enterprise_token)
    return calls


def _run(env, *args):
    return subprocess.run(
        [str(_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_usage_error_on_missing_arg():
    result = _run(_base_test_env())
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "CI_RESULT: error" in result.stdout


def test_usage_error_on_empty_arg():
    # The usage guard is `[[ "$#" -ne 1 ]] || [[ -z "$1" ]]` — a single
    # empty-string argument trips the second half, never exercised by the
    # zero-arg case above.
    result = _run(_base_test_env(), "")
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "CI_RESULT: error" in result.stdout


def test_usage_error_on_non_numeric_arg():
    result = _run(_base_test_env(), "not-a-number")
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
    # PATH must keep bash (needed for the shebang) but exclude gh; an
    # allowlist works because the "gh missing" check runs before any other
    # external command.
    bash_only_dir = tmp_path / "bash_only"
    bash_only_dir.mkdir()
    (bash_only_dir / "bash").symlink_to(shutil.which("bash"))
    env = {**_base_test_env(), "PATH": str(bash_only_dir)}
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    assert "CI_RESULT: error gh not installed" in result.stdout


def test_checks_gh_token_override_applies_to_watch_and_json_only(fake_gh, tmp_path):
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "fixture-broader-checks-token"},
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0] == f"LAUNCH_SHA: {_HEAD_SHA}"
    assert lines[1].startswith("CI_RESULT: checks ")
    assert json.loads(lines[1][len("CI_RESULT: checks "):]) == checks
    calls = _parse_token_log(token_log)
    assert calls["view"] == ("", "")
    assert calls["watch"] == ("fixture-broader-checks-token", "")
    assert calls["json"] == ("fixture-broader-checks-token", "")
    # The escalation to the account-wide token is visible in the agent's own
    # transcript, once per wrapped call, without logging the token value.
    assert result.stderr.count("using CI_CHECKS_GH_TOKEN override for Checks API") == 2
    assert "fixture-broader-checks-token" not in result.stdout
    assert "fixture-broader-checks-token" not in result.stderr


def test_checks_gh_token_unset_leaves_all_calls_unwrapped(fake_gh, tmp_path):
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0] == f"LAUNCH_SHA: {_HEAD_SHA}"
    assert lines[1].startswith("CI_RESULT: checks ")
    assert json.loads(lines[1][len("CI_RESULT: checks "):]) == checks
    calls = _parse_token_log(token_log)
    assert calls["view"] == ("", "")
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
    assert "using CI_CHECKS_GH_TOKEN override" not in result.stderr


def test_checks_gh_token_empty_string_behaves_as_unset(fake_gh, tmp_path):
    # Exercises the wrapper's actual gate ([[ -n "${CI_CHECKS_GH_TOKEN:-}" ]],
    # which treats empty and unset alike), not just an existence check.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": ""},
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["view"] == ("", "")
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
    assert "using CI_CHECKS_GH_TOKEN override" not in result.stderr


def test_json_failure_with_token_unset_appends_hint_to_existing_error(fake_gh):
    env = fake_gh(
        watch_output="Some checks are still pending\n",
        watch_exit=8,
        json_fails=True,
        json_stderr="error connecting to api.github.com\n",
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    error_lines = [line for line in result.stdout.splitlines() if line.startswith("CI_RESULT: error")]
    assert len(error_lines) == 1
    error_line = error_lines[0]
    # Append-not-replace: the original gh stderr and the new hint must
    # co-occur on the same line, not one displacing the other.
    assert "error connecting to api.github.com" in error_line
    assert "CI_CHECKS_GH_TOKEN" in error_line
    # Hedged, not asserted: this fixture's stderr isn't 403-shaped, so a flat
    # unhedged claim here would be misleading.
    assert "if this is a 403" in error_line


def test_json_failure_with_token_set_has_no_hint(fake_gh):
    env = fake_gh(
        watch_output="Some checks are still pending\n",
        watch_exit=8,
        json_fails=True,
        json_stderr="error connecting to api.github.com\n",
        extra_env={"CI_CHECKS_GH_TOKEN": "fixture-broader-checks-token"},
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    error_lines = [line for line in result.stdout.splitlines() if line.startswith("CI_RESULT: error")]
    assert len(error_lines) == 1
    error_line = error_lines[0]
    assert "error connecting to api.github.com" in error_line
    assert "if this is a 403" not in error_line


def test_ambient_shell_credentials_are_scrubbed_from_subprocess_env(fake_gh, tmp_path, monkeypatch):
    # Simulates a contributor's real dev shell, where all three vars are
    # already exported, to confirm none of them reach ci-watch.sh's
    # subprocess env or the gh calls it makes -- only meaningful now that
    # NODE_AUTH_TOKEN is in _SENSITIVE_ENV_VARS alongside CI_CHECKS_GH_TOKEN.
    monkeypatch.setenv("CI_CHECKS_GH_TOKEN", "leaked-ci-checks-token")
    monkeypatch.setenv("NODE_AUTH_TOKEN", "leaked-node-auth-token")
    monkeypatch.setenv("GH_TOKEN", "leaked-gh-token")
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
    )
    assert "CI_CHECKS_GH_TOKEN" not in env
    assert "NODE_AUTH_TOKEN" not in env
    assert "GH_TOKEN" not in env
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    log_text = token_log.read_text()
    assert "leaked-ci-checks-token" not in log_text
    assert "leaked-node-auth-token" not in log_text
    assert "leaked-gh-token" not in log_text
    calls = _parse_token_log(token_log)
    assert calls["view"] == ("", "")
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")


def test_ambient_gh_token_untouched_on_view_but_overridden_on_watch_and_json(fake_gh, tmp_path):
    # The wrapper's actual job: leave the unwrapped `gh pr view` call on
    # whatever GH_TOKEN the caller's shell already set (the fine-grained PAT
    # use_ghorg exports), and only override it for the two Checks-API calls.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={
            "GH_TOKEN": "ambient-finegrained-token",
            "CI_CHECKS_GH_TOKEN": "override-broader-checks-token",
        },
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["view"] == ("ambient-finegrained-token", "")
    assert calls["watch"] == ("override-broader-checks-token", "")
    assert calls["json"] == ("override-broader-checks-token", "")
