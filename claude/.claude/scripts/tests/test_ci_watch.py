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

from .conftest import (
    _base_test_env,
    _direnv_shim_source_exits_nonzero_with_unset_payload,
    _direnv_shim_source_reads_stdin,
    _direnv_shim_source_static_export,
    _direnv_shim_source_unconditional_unset,
    _shimmed_env,
)

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
    """Yield a factory that installs a gh shim (and, via _shimmed_env, a
    default no-op direnv shim) and returns the env dict.

    extra_env layers CI_CHECKS_GH_TOKEN/GH_TOKEN fixtures on top of the
    credential-scrubbed base env, modeling a container that has (or hasn't)
    exported the classic-PAT override — never sourced from the calling
    test process's own os.environ, which _base_test_env() has already
    stripped of every _SENSITIVE_ENV_VARS entry.

    direnv_source/direnv_present pass straight through to _shimmed_env, for
    tests exercising resolve_ci_checks_gh_token's direnv resolution path
    instead of the plain ambient-env case every other test here covers.
    Routing ci-watch.sh's own gh shim through the same _shimmed_env seam
    cleanup-merged-branches.sh's tests use (rather than building PATH
    directly from the real os.environ["PATH"]) matters now that
    resolve_ci_checks_gh_token calls real direnv in a subshell: without
    this, every test here would invoke whatever direnv is actually
    installed on the machine running the suite, against this repo's own
    real .envrc.
    """
    def _make_env(
        *,
        extra_env=None,
        token_log=None,
        direnv_source=None,
        direnv_present=True,
        **kwargs,
    ) -> dict:
        gh_shim_source_text = _gh_shim_source(token_log=token_log, **kwargs)
        env = _shimmed_env(
            tmp_path, gh_shim_source_text,
            direnv_source=direnv_source, direnv_present=direnv_present,
        )
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
    # PATH must keep bash (needed for the shebang) and dirname (needed to
    # resolve _direnv-lib.sh's own source path, ahead of the "gh missing"
    # check) but exclude gh; an allowlist works because nothing else runs
    # before that check.
    bash_only_dir = tmp_path / "bash_only"
    bash_only_dir.mkdir()
    (bash_only_dir / "bash").symlink_to(shutil.which("bash"))
    (bash_only_dir / "dirname").symlink_to(shutil.which("dirname"))
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
    # Pins the doc pointer in the hint so a future edit that drops or
    # renames it without updating the hint text is caught.
    assert "docs/scripts.md" in error_line


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


# ---------------------------------------------------------------------------
# resolve_ci_checks_gh_token — direnv resolution of CI_CHECKS_GH_TOKEN
#
# ci-watch.sh is launched via `Bash` `run_in_background`, a non-interactive
# shell that never fires direnv's PROMPT_COMMAND hook — resolve_ci_checks_gh_token
# resyncs CI_CHECKS_GH_TOKEN against this directory's own direnv-sourced
# identity before gh_with_checks_token's first call, the same problem shape
# cleanup-merged-branches.sh's load_repo_environment already solves.
# ---------------------------------------------------------------------------

def _direnv_shim_source_multi_export(exports: dict) -> str:
    """direnv shim exporting more than one NAME=VALUE pair unconditionally on
    `export bash`, regardless of cwd — models a single .envrc setting several
    vars at once (a real .envrc's typical shape), for the subshell-containment
    test below."""
    payload = json.dumps(exports)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, shlex, sys

        EXPORTS = json.loads({payload!r})

        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            for name, value in EXPORTS.items():
                print(f"export {{name}}={{shlex.quote(value)}}")
        sys.exit(0)
    """)


def test_direnv_absent_leaves_ambient_token_intact(fake_gh, tmp_path):
    # direnv_present=False curates a PATH with no direnv binary at all —
    # resolve_ci_checks_gh_token's own `command -v direnv` guard must return
    # immediately, leaving the fixture's ambient CI_CHECKS_GH_TOKEN as the
    # only value the wrapped calls ever see.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-only-token"},
        direnv_present=False,
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("ambient-only-token", "")
    assert calls["json"] == ("ambient-only-token", "")
    assert "resolved via direnv" not in result.stderr


def test_direnv_supplies_token_when_ambient_unset(fake_gh, tmp_path):
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        direnv_source=_direnv_shim_source_static_export(
            "CI_CHECKS_GH_TOKEN", "direnv-supplied-token",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("direnv-supplied-token", "")
    assert calls["json"] == ("direnv-supplied-token", "")
    assert "direnv-supplied-token" not in result.stdout
    assert "direnv-supplied-token" not in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr


def test_direnv_answer_overrides_different_ambient_value(fake_gh, tmp_path):
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "stale-ambient-token"},
        direnv_source=_direnv_shim_source_static_export(
            "CI_CHECKS_GH_TOKEN", "fresh-direnv-token",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("fresh-direnv-token", "")
    assert calls["json"] == ("fresh-direnv-token", "")
    assert "stale-ambient-token" not in result.stdout
    assert "stale-ambient-token" not in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr


def test_direnv_resolving_to_same_ambient_value_suppresses_notice(fake_gh, tmp_path):
    # The notice exists to flag a change from ambient — direnv confirming
    # the same value this process already inherited must not fire it, or
    # every run on a contributor machine with direnv installed and a
    # matching .envrc would spam this line even though nothing changed.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "stable-token"},
        direnv_source=_direnv_shim_source_static_export(
            "CI_CHECKS_GH_TOKEN", "stable-token",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("stable-token", "")
    assert calls["json"] == ("stable-token", "")
    assert "resolved via direnv" not in result.stderr


def test_direnv_unset_export_clears_stale_ambient_value(fake_gh, tmp_path):
    # Models a container sibling with no matching .envrc for this
    # directory: direnv's own diff mechanism unsets whatever a prior
    # interactive session loaded, matching what a real `cd` would do.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "stale-container-token"},
        direnv_source=_direnv_shim_source_unconditional_unset("CI_CHECKS_GH_TOKEN"),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    # The override cleared, so watch/json fall back to the unwrapped path —
    # same as an ambient CI_CHECKS_GH_TOKEN that was never set.
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
    assert "stale-container-token" not in result.stdout
    assert "stale-container-token" not in result.stderr
    assert "using CI_CHECKS_GH_TOKEN override" not in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr


def test_direnv_exiting_nonzero_leaves_ambient_untouched_and_does_not_abort(fake_gh, tmp_path):
    # Models a non-`allow`ed .envrc: `export bash` exits 1 but still writes
    # an unset payload to stdout — resolve_ci_checks_gh_token's guard must
    # discard this cleanly rather than aborting the script under
    # set -euo pipefail (this script runs for potentially hours in the
    # background; an .envrc misbehaving must never kill the watch).
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-survives-token"},
        direnv_source=_direnv_shim_source_exits_nonzero_with_unset_payload(
            name="CI_CHECKS_GH_TOKEN",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("ambient-survives-token", "")
    assert calls["json"] == ("ambient-survives-token", "")
    # A discarded direnv failure is indistinguishable from direnv having
    # nothing to say: the resolved value never differs from ambient, so
    # the notice (gated on an actual diff) must not fire either.
    assert "resolved via direnv" not in result.stderr


def test_direnv_export_containing_other_vars_does_not_perturb_running_script(fake_gh, tmp_path):
    # Subshell-containment guarantee: an .envrc exporting GH_TOKEN, PATH, and
    # STDERR_FILE alongside CI_CHECKS_GH_TOKEN must not let any of the first
    # three cross into the running script's own environment — only
    # CI_CHECKS_GH_TOKEN's resolved value may. The unwrapped `gh pr view`
    # call is the tell for a GH_TOKEN leak: it never receives the
    # CI_CHECKS_GH_TOKEN override, so it only ever sees a leaked GH_TOKEN if
    # containment failed. STDERR_FILE is the tell for the EXIT trap: if
    # containment failed and STDERR_FILE were hijacked to this decoy path,
    # the trap's `rm -f "$STDERR_FILE"` would delete it.
    token_log = tmp_path / "token.log"
    decoy_stderr_file = tmp_path / "decoy-stderr-file"
    decoy_stderr_file.write_text("decoy-content-must-survive")
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"GH_TOKEN": "original-ambient-gh-token"},
        direnv_source=_direnv_shim_source_multi_export({
            "CI_CHECKS_GH_TOKEN": "direnv-resolved-checks-token",
            "GH_TOKEN": "leaked-from-envrc-gh-token",
            "PATH": "/nonexistent-path-from-envrc",
            "STDERR_FILE": str(decoy_stderr_file),
        }),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    # The unwrapped view call must see the original ambient GH_TOKEN, never
    # the .envrc's — proving GH_TOKEN did not cross the subshell boundary.
    assert calls["view"] == ("original-ambient-gh-token", "")
    # CI_CHECKS_GH_TOKEN is the one var meant to cross: the wrapped calls
    # must use the direnv-resolved value.
    assert calls["watch"] == ("direnv-resolved-checks-token", "")
    assert calls["json"] == ("direnv-resolved-checks-token", "")
    assert "leaked-from-envrc-gh-token" not in result.stdout
    assert "leaked-from-envrc-gh-token" not in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr
    # The decoy file, never the script's own real STDERR_FILE, must survive
    # untouched — proving the trap's target was never hijacked.
    assert decoy_stderr_file.read_text() == "decoy-content-must-survive"


def test_stdin_reading_envrc_does_not_hang(fake_gh):
    # resolve_ci_checks_gh_token wraps direnv_export_bash's eval inside an
    # extra nested subshell/command-substitution layer that
    # cleanup-merged-branches.sh's own call site doesn't have — a
    # regression dropping or reordering direnv_export_bash's </dev/null
    # redirect at this specific call site would hang the script rather
    # than fail an assertion, so this needs a held-open pipe rather than
    # the default DEVNULL stdin every other test here relies on.
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        direnv_source=_direnv_shim_source_reads_stdin(),
    )
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        [str(_SCRIPT), _PR_NUMBER], env=env, stdin=read_fd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    os.close(read_fd)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail(
            "script hung on a stdin-reading .envrc — "
            "direnv_export_bash's </dev/null guard regressed at "
            "resolve_ci_checks_gh_token's nested subshell call site"
        )
    finally:
        os.close(write_fd)
    assert proc.returncode == 0
